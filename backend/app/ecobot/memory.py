from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .contracts import Stimulus


EmbeddingCallback = Callable[[str], Awaitable[list[float]]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return dot / (left_norm * right_norm)


class MemoryStore:
    """Traceable L1/L2 memory with lexical and optional vector retrieval."""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._fts_enabled = False
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ecobot_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_type TEXT NOT NULL,
                    channel_id TEXT,
                    user_id TEXT,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    normalized_content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    importance REAL NOT NULL DEFAULT 0.5,
                    embedding_json TEXT,
                    created_at TEXT NOT NULL,
                    last_accessed_at TEXT,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(source_type, source_id)
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot_memories_channel_time
                    ON ecobot_memories(channel_id, created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_ecobot_memories_user_time
                    ON ecobot_memories(user_id, created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_ecobot_memories_hash
                    ON ecobot_memories(content_hash);
                """
            )
            try:
                self._connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS ecobot_memories_fts "
                    "USING fts5(content, content='ecobot_memories', content_rowid='id')"
                )
                self._connection.executescript(
                    """
                    CREATE TRIGGER IF NOT EXISTS ecobot_memories_ai AFTER INSERT ON ecobot_memories BEGIN
                        INSERT INTO ecobot_memories_fts(rowid, content) VALUES (new.id, new.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS ecobot_memories_ad AFTER DELETE ON ecobot_memories BEGIN
                        INSERT INTO ecobot_memories_fts(ecobot_memories_fts, rowid, content)
                        VALUES ('delete', old.id, old.content);
                    END;
                    CREATE TRIGGER IF NOT EXISTS ecobot_memories_au AFTER UPDATE OF content ON ecobot_memories BEGIN
                        INSERT INTO ecobot_memories_fts(ecobot_memories_fts, rowid, content)
                        VALUES ('delete', old.id, old.content);
                        INSERT INTO ecobot_memories_fts(rowid, content) VALUES (new.id, new.content);
                    END;
                    """
                )
                self._fts_enabled = True
            except sqlite3.OperationalError:
                self._fts_enabled = False

    def remember(
        self,
        content: str,
        *,
        source_type: str,
        source_id: str,
        memory_type: str = "episodic",
        channel_id: str | None = None,
        user_id: str | None = None,
        importance: float = 0.5,
        embedding: Sequence[float] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        text = content.strip()
        if not text or not source_type.strip() or not source_id.strip():
            raise ValueError("content, source_type, and source_id must not be empty")
        normalized = _normalize(text)
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot_memories(
                    memory_type, channel_id, user_id, source_type, source_id,
                    content, normalized_content, content_hash, importance,
                    embedding_json, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    memory_type = excluded.memory_type,
                    channel_id = excluded.channel_id,
                    user_id = excluded.user_id,
                    content = excluded.content,
                    normalized_content = excluded.normalized_content,
                    content_hash = excluded.content_hash,
                    importance = excluded.importance,
                    embedding_json = COALESCE(excluded.embedding_json, ecobot_memories.embedding_json),
                    metadata_json = excluded.metadata_json
                """,
                (
                    memory_type,
                    channel_id,
                    user_id,
                    source_type,
                    source_id,
                    text,
                    normalized,
                    sha256(normalized.encode("utf-8")).hexdigest(),
                    max(0.0, min(1.0, float(importance))),
                    json.dumps(list(embedding)) if embedding is not None else None,
                    _utc_now(),
                    json.dumps(dict(metadata or {}), ensure_ascii=False, default=str),
                ),
            )
            row = self._connection.execute(
                "SELECT id FROM ecobot_memories WHERE source_type = ? AND source_id = ?",
                (source_type, source_id),
            ).fetchone()
            assert row is not None
            return int(row[0])

    async def remember_with_embedding(
        self,
        content: str,
        *,
        embed: EmbeddingCallback | None = None,
        **kwargs: Any,
    ) -> int:
        embedding = None
        if embed is not None:
            try:
                embedding = await embed(content)
            except Exception:
                embedding = None
        return self.remember(content, embedding=embedding, **kwargs)

    def recent_messages(self, stimulus: Stimulus, limit: int = 20) -> list[dict[str, Any]]:
        group_id = stimulus.metadata.get("group_id")
        with self._guard:
            try:
                if group_id:
                    rows = self._connection.execute(
                        """
                        SELECT id, sender_qq_id, content_text, sent_at, observed_at
                        FROM qq_messages WHERE group_id = ?
                        ORDER BY COALESCE(sent_at, 0) DESC, id DESC LIMIT ?
                        """,
                        (str(group_id), max(1, limit)),
                    ).fetchall()
                else:
                    rows = self._connection.execute(
                        """
                        SELECT id, sender_qq_id, content_text, sent_at, observed_at
                        FROM qq_messages WHERE sender_qq_id = ? AND group_id IS NULL
                        ORDER BY COALESCE(sent_at, 0) DESC, id DESC LIMIT ?
                        """,
                        (stimulus.user_id, max(1, limit)),
                    ).fetchall()
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc):
                    raise
                rows = []
        now = time.time()
        return [
            {
                "layer": "L1",
                "source_type": "qq_message",
                "source_id": str(row[0]),
                "user_id": str(row[1]),
                "content": str(row[2]),
                "sent_at": row[3],
                "sent_at_iso": (
                    datetime.fromtimestamp(float(row[3]), timezone.utc).isoformat()
                    if row[3] is not None
                    else None
                ),
                "observed_at": row[4],
                "age_seconds": max(0.0, now - float(row[3])) if row[3] is not None else None,
            }
            for row in reversed(rows)
        ]

    def retrieve(
        self,
        stimulus: Stimulus,
        *,
        limit: int = 12,
        recent_limit: int = 20,
        query_embedding: Sequence[float] | None = None,
        semantic_min_similarity: float = 0.0,
    ) -> tuple[dict[str, Any], ...]:
        self.sync_archive_memories()
        l1 = self.recent_messages(stimulus, limit=max(1, recent_limit))
        candidates = self._lexical_candidates(
            stimulus.content, stimulus.channel_id, stimulus.user_id, max(limit * 4, 20)
        )
        if query_embedding is not None:
            semantic_candidates = []
            lexical_only_candidates = []
            for item in candidates:
                embedding = item.pop("_embedding", None)
                if embedding:
                    item["similarity"] = _cosine(query_embedding, embedding)
                    if item["similarity"] >= semantic_min_similarity:
                        semantic_candidates.append(item)
                else:
                    lexical_only_candidates.append(item)
            semantic_candidates.sort(
                key=lambda item: (item.get("similarity", -1.0), item["importance"]),
                reverse=True,
            )
            candidates = semantic_candidates + lexical_only_candidates
        for item in candidates:
            item.pop("_embedding", None)
        selected = candidates[:limit]
        if selected:
            ids = [item["id"] for item in selected]
            placeholders = ",".join("?" for _ in ids)
            with self._guard, self._connection:
                self._connection.execute(
                    f"UPDATE ecobot_memories SET access_count = access_count + 1, "
                    f"last_accessed_at = ? WHERE id IN ({placeholders})",
                    (_utc_now(), *ids),
                )
        return tuple(l1 + selected)

    def sync_archive_memories(self) -> int:
        """Promote durable archive facts into L2 without duplicating source rows."""
        promoted = 0
        sources = (
            (
                "qq_relationship_memory",
                """
                SELECT CAST(r.id AS TEXT), r.content, r.person_a_qq_id,
                       r.group_id, r.confidence, r.created_at,
                       json_object('person_b_qq_id', r.person_b_qq_id,
                                   'memory_type', r.memory_type)
                FROM qq_relationship_memories r
                WHERE r.superseded_at IS NULL
                """,
                "relationship",
            ),
            (
                "qq_qzone_post",
                """
                SELECT CAST(q.id AS TEXT), q.content_text, q.qq_id,
                       NULL, 0.55, q.updated_at,
                       json_object('post_id', q.post_id, 'post_type', q.post_type)
                FROM qq_qzone_posts q WHERE q.deleted_at IS NULL
                  AND q.content_text != ''
                """,
                "episodic",
            ),
        )
        for source_type, sql, memory_type in sources:
            with self._guard:
                try:
                    rows = self._connection.execute(sql).fetchall()
                except sqlite3.OperationalError:
                    continue
            for source_id, content, user_id, channel_id, importance, _, metadata in rows:
                with self._guard:
                    exists = self._connection.execute(
                        "SELECT 1 FROM ecobot_memories WHERE source_type = ? AND source_id = ?",
                        (source_type, source_id),
                    ).fetchone()
                if exists:
                    continue
                self.remember(
                    str(content),
                    source_type=source_type,
                    source_id=str(source_id),
                    memory_type=memory_type,
                    channel_id=str(channel_id) if channel_id else None,
                    user_id=str(user_id) if user_id else None,
                    importance=float(importance),
                    metadata=json.loads(metadata) if metadata else {},
                )
                promoted += 1
        return promoted

    async def retrieve_with_embedding(
        self,
        stimulus: Stimulus,
        *,
        embed: EmbeddingCallback | None = None,
        limit: int = 12,
        recent_limit: int = 20,
        semantic_min_similarity: float = 0.0,
    ) -> tuple[dict[str, Any], ...]:
        query_embedding = None
        if embed is not None:
            try:
                query_embedding = await embed(stimulus.content)
            except Exception:
                query_embedding = None
        return self.retrieve(
            stimulus,
            limit=limit,
            recent_limit=recent_limit,
            query_embedding=query_embedding,
            semantic_min_similarity=semantic_min_similarity,
        )

    def _lexical_candidates(
        self, query: str, channel_id: str, user_id: str, limit: int
    ) -> list[dict[str, Any]]:
        tokens = [token for token in re.findall(r"[\w\u4e00-\u9fff]+", query.casefold()) if token]
        with self._guard:
            rows: list[tuple[Any, ...]] = []
            if self._fts_enabled and tokens:
                expression = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:8])
                try:
                    rows = self._connection.execute(
                        """
                        SELECT m.id, m.memory_type, m.source_type, m.source_id,
                               m.channel_id, m.user_id, m.content, m.importance,
                               m.embedding_json, m.created_at, m.metadata_json
                        FROM ecobot_memories_fts f
                        JOIN ecobot_memories m ON m.id = f.rowid
                        WHERE ecobot_memories_fts MATCH ?
                          AND (m.channel_id IS NULL OR m.channel_id = ? OR m.user_id = ?)
                        ORDER BY bm25(ecobot_memories_fts), m.importance DESC LIMIT ?
                        """,
                        (expression, channel_id, user_id, limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows:
                pattern = f"%{_normalize(query)}%"
                rows = self._connection.execute(
                    """
                    SELECT id, memory_type, source_type, source_id, channel_id,
                           user_id, content, importance, embedding_json,
                           created_at, metadata_json
                    FROM ecobot_memories
                    WHERE (channel_id IS NULL OR channel_id = ? OR user_id = ?)
                      AND (? = '%%' OR normalized_content LIKE ?)
                    ORDER BY importance DESC, created_at DESC, id DESC LIMIT ?
                    """,
                    (channel_id, user_id, pattern, pattern, limit),
                ).fetchall()
        return [
            {
                "layer": "L2",
                "id": int(row[0]),
                "memory_type": row[1],
                "source_type": row[2],
                "source_id": row[3],
                "channel_id": row[4],
                "user_id": row[5],
                "content": row[6],
                "importance": float(row[7]),
                "_embedding": json.loads(row[8]) if row[8] else None,
                "created_at": row[9],
                "metadata": json.loads(row[10]),
            }
            for row in rows
        ]

    def close(self) -> None:
        with self._guard:
            self._connection.close()
