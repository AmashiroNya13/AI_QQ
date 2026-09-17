from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LEADING_PATTERN = re.compile(r"^\s*(欸|诶|哎|嗯|唔|哼|啊|喂)[，,。…！？!?~～]*")
_TRAILING_PATTERN = re.compile(r"([啊呀啦呢吧嘛哦诶欸哼唔嗯]+[！!？?~～…]*)\s*$")
_EMOJI_PATTERN = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", re.UNICODE)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return dot / (left_norm * right_norm)


def style_features(text: str) -> dict[str, str]:
    value = text.strip()
    leading = _LEADING_PATTERN.search(value)
    trailing = _TRAILING_PATTERN.search(value)
    if value.endswith(("？", "?")):
        form = "疑问式"
    elif value.endswith(("！", "!")):
        form = "感叹式"
    elif value.endswith(("…", "~", "～")):
        form = "拖长尾音"
    elif len(value) <= 12:
        form = "短回应"
    else:
        form = "陈述式"
    emoji = "有表情符号" if _EMOJI_PATTERN.search(value) else ""
    return {
        "leading": leading.group(1) if leading else "",
        "trailing": trailing.group(1) if trailing else "",
        "form": form,
        "emoji": emoji,
    }


def style_signature(text: str) -> str:
    features = style_features(text)
    flavor = [features["leading"], features["trailing"], features["emoji"]]
    if not any(flavor):
        return ""
    return "|".join([*flavor, features["form"]])


class StyleMemoryStore:
    """Local, auditable style examples with lexical and vector retrieval."""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._guard, self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ecobot_style_examples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    channel_id TEXT,
                    source_id TEXT NOT NULL UNIQUE,
                    content TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    embedding_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot_style_examples_user
                    ON ecobot_style_examples(user_id, id DESC);
                CREATE TABLE IF NOT EXISTS ecobot_style_profiles (
                    user_id TEXT PRIMARY KEY,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def learn(
        self,
        user_id: str,
        content: str,
        *,
        source_id: str,
        channel_id: str | None = None,
        embedding: Sequence[float] | None = None,
    ) -> bool:
        normalized_user_id = str(user_id).strip()
        text = content.strip()
        if not normalized_user_id or not text or not source_id.strip():
            return False
        with self._guard, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO ecobot_style_examples(
                    user_id, channel_id, source_id, content, features_json,
                    embedding_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_user_id,
                    channel_id,
                    source_id,
                    text,
                    json.dumps(style_features(text), ensure_ascii=False),
                    json.dumps(list(embedding)) if embedding else None,
                    _utc_now(),
                ),
            )
            if cursor.rowcount == 0:
                return False
            self._connection.execute(
                """
                INSERT INTO ecobot_style_profiles(user_id, sample_count, updated_at)
                VALUES (?, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    sample_count = ecobot_style_profiles.sample_count + 1,
                    updated_at = excluded.updated_at
                """,
                (normalized_user_id, _utc_now()),
            )
        return True

    def context(
        self,
        user_id: str,
        query: str,
        *,
        query_embedding: Sequence[float] | None = None,
        limit: int = 4,
        minimum_similarity: float = 0.35,
        exclude_source_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_user_id = str(user_id).strip()
        bounded_limit = max(1, min(8, int(limit)))
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT content, features_json, embedding_json, created_at
                FROM ecobot_style_examples
                WHERE user_id = ? AND (? IS NULL OR source_id != ?)
                ORDER BY id DESC LIMIT 300
                """,
                (normalized_user_id, exclude_source_id, exclude_source_id),
            ).fetchall()
        features = [json.loads(row[1]) for row in rows]
        ranked: list[tuple[float, sqlite3.Row]] = []
        for position, row in enumerate(rows):
            embedding = json.loads(row[2]) if row[2] else None
            similarity = _cosine(query_embedding, embedding) if query_embedding and embedding else None
            if similarity is not None and similarity < minimum_similarity:
                continue
            lexical_bonus = 0.1 if query and any(token in row[0] for token in query.split() if len(token) > 1) else 0.0
            score = similarity if similarity is not None else 0.0
            ranked.append((score + lexical_bonus - position * 0.0001, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        selected = ranked[:bounded_limit]
        return {
            "user_id": normalized_user_id,
            "sample_count": len(rows),
            "profile": self._profile(features),
            "references": [
                {
                    "content": str(row["content"])[:180],
                    "features": json.loads(row["features_json"]),
                    "similarity": round(score, 3) if query_embedding else None,
                    "created_at": row["created_at"],
                }
                for score, row in selected
            ],
        }

    def profiles(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            try:
                rows = self._connection.execute(
                    """
                    SELECT p.user_id, u.current_nickname, p.sample_count, p.updated_at
                    FROM ecobot_style_profiles p
                    LEFT JOIN qq_users u ON u.qq_id = p.user_id
                    ORDER BY p.updated_at DESC LIMIT ?
                    """,
                    (max(1, min(1000, int(limit))),),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = self._connection.execute(
                    """
                    SELECT user_id, NULL AS current_nickname, sample_count, updated_at
                    FROM ecobot_style_profiles ORDER BY updated_at DESC LIMIT ?
                    """,
                    (max(1, min(1000, int(limit))),),
                ).fetchall()
        return [dict(row) for row in rows]

    def examples(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT id, user_id, channel_id, source_id, content, features_json, created_at
                FROM ecobot_style_examples WHERE user_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (str(user_id).strip(), max(1, min(1000, int(limit)))),
            ).fetchall()
        return [
            dict(row) | {"features": json.loads(row[5])}
            for row in rows
        ]

    def backfill_from_archive(self, user_id: str, limit: int = 5000) -> int:
        normalized_user_id = str(user_id).strip()
        if not normalized_user_id:
            raise ValueError("QQ 号不能为空")
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT id, group_id, content_text FROM qq_messages
                WHERE sender_qq_id = ? AND content_text != ''
                ORDER BY id DESC LIMIT ?
                """,
                (normalized_user_id, max(1, min(50000, int(limit)))),
            ).fetchall()
        inserted = 0
        for row in rows:
            inserted += int(
                self.learn(
                    normalized_user_id,
                    str(row[2]),
                    source_id=f"qq_message:{row[0]}",
                    channel_id=str(row[1]) if row[1] else None,
                )
            )
        return inserted

    @staticmethod
    def _profile(features: Sequence[Mapping[str, str]]) -> dict[str, Any]:
        def common(key: str) -> list[str]:
            counter = Counter(item.get(key, "") for item in features if item.get(key, ""))
            return [value for value, _ in counter.most_common(4)]

        return {
            "common_openings": common("leading"),
            "common_endings": common("trailing"),
            "common_forms": common("form"),
            "uses_emoji": any(item.get("emoji") for item in features),
        }

    def close(self) -> None:
        with self._guard:
            self._connection.close()
