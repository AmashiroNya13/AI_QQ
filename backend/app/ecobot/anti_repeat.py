from __future__ import annotations

import json
import math
import sqlite3
import threading
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from hashlib import sha256
from pathlib import Path

from .style_memory import style_signature


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize(value: str) -> str:
    return "".join(value.casefold().split())


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norms if norms else -1.0


class AntiRepeatGuard:
    """Atomic content and expressive-style repeat guard."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        window: timedelta = timedelta(hours=6),
        fuzzy_threshold: float = 0.92,
        semantic_threshold: float = 0.94,
        style_window: timedelta = timedelta(hours=1),
        style_repeat_limit: int = 1,
    ) -> None:
        self.window = window
        self.fuzzy_threshold = fuzzy_threshold
        self.semantic_threshold = semantic_threshold
        self.style_window = style_window
        self.style_repeat_limit = max(1, int(style_repeat_limit))
        self._guard = threading.RLock()
        self._connection = sqlite3.connect(database_path, check_same_thread=False, timeout=30)
        self._connection.execute("PRAGMA journal_mode=WAL")
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ecobot_expressions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    expression TEXT NOT NULL,
                    normalized_expression TEXT NOT NULL,
                    expression_hash TEXT NOT NULL,
                    embedding_json TEXT,
                    style_signature TEXT,
                    status TEXT NOT NULL,
                    batch_id TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot_expressions_recent
                    ON ecobot_expressions(channel_id, created_at DESC, id DESC);
                CREATE INDEX IF NOT EXISTS idx_ecobot_expressions_hash
                    ON ecobot_expressions(channel_id, expression_hash, created_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot_desire_drives (
                    channel_id TEXT PRIMARY KEY,
                    accumulation_started_at TEXT NOT NULL,
                    last_expression_at TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row[1]
                for row in self._connection.execute(
                    "PRAGMA table_info(ecobot_expressions)"
                ).fetchall()
            }
            if "style_signature" not in columns:
                self._connection.execute(
                    "ALTER TABLE ecobot_expressions ADD COLUMN style_signature TEXT"
                )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ecobot_expressions_style
                ON ecobot_expressions(channel_id, style_signature, created_at DESC)
                """
            )

    def desire_time_context(
        self,
        channel_id: str,
        *,
        growth_per_hour: float,
        maximum_boost: float,
        now: datetime | None = None,
    ) -> dict[str, object]:
        current_time = now or _now()
        with self._guard, self._connection:
            row = self._connection.execute(
                """
                SELECT accumulation_started_at, last_expression_at
                FROM ecobot_desire_drives WHERE channel_id = ?
                """,
                (channel_id,),
            ).fetchone()
            if row is None:
                sent = self._connection.execute(
                    """
                    SELECT completed_at FROM ecobot_expressions
                    WHERE channel_id = ? AND status = 'sent' AND completed_at IS NOT NULL
                    ORDER BY completed_at DESC, id DESC LIMIT 1
                    """,
                    (channel_id,),
                ).fetchone()
                last_expression_at = str(sent[0]) if sent is not None else None
                accumulation_started_at = last_expression_at or current_time.isoformat()
                self._connection.execute(
                    """
                    INSERT INTO ecobot_desire_drives(
                        channel_id, accumulation_started_at, last_expression_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        channel_id,
                        accumulation_started_at,
                        last_expression_at,
                        current_time.isoformat(),
                    ),
                )
            else:
                accumulation_started_at = str(row[0])
                last_expression_at = str(row[1]) if row[1] else None

        try:
            started_at = datetime.fromisoformat(accumulation_started_at)
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            started_at = current_time
        elapsed_seconds = max(0.0, (current_time - started_at).total_seconds())
        hourly_growth = max(0.0, float(growth_per_hour))
        boost_limit = max(0.0, min(100.0, float(maximum_boost)))
        boost = min(boost_limit, elapsed_seconds / 3600.0 * hourly_growth)
        return {
            "elapsed_seconds": round(elapsed_seconds, 3),
            "elapsed_hours": round(elapsed_seconds / 3600.0, 3),
            "growth_per_hour": hourly_growth,
            "maximum_boost": boost_limit,
            "time_boost": round(boost, 3),
            "accumulation_started_at": accumulation_started_at,
            "last_expression_at": last_expression_at,
        }

    def reply_style_context(
        self, channel_id: str, *, now: datetime | None = None
    ) -> dict[str, object]:
        current_time = now or _now()
        cutoff = (current_time - self.style_window).isoformat()
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT style_signature, COUNT(*)
                FROM ecobot_expressions
                WHERE channel_id = ? AND created_at >= ? AND status != 'failed'
                  AND style_signature IS NOT NULL AND style_signature != ''
                GROUP BY style_signature
                ORDER BY COUNT(*) DESC, MAX(id) DESC LIMIT 12
                """,
                (channel_id, cutoff),
            ).fetchall()
        return {
            "enabled": True,
            "window_minutes": round(self.style_window.total_seconds() / 60.0, 1),
            "repeat_limit": self.style_repeat_limit,
            "avoid_signatures": [str(row[0]) for row in rows],
        }

    def reserve(
        self,
        channel_id: str,
        expression: str,
        *,
        batch_id: str | None = None,
        embedding: Sequence[float] | None = None,
        now: datetime | None = None,
    ) -> int | None:
        created_at = now or _now()
        normalized = _normalize(expression)
        if not normalized:
            return None
        digest = sha256(normalized.encode("utf-8")).hexdigest()
        cutoff = (created_at - self.window).isoformat()
        candidate_style = style_signature(expression)
        with self._guard, self._connection:
            rows = self._connection.execute(
                """
                SELECT normalized_expression, expression_hash, embedding_json
                FROM ecobot_expressions
                WHERE channel_id = ? AND created_at >= ? AND status != 'failed'
                ORDER BY id DESC LIMIT 50
                """,
                (channel_id, cutoff),
            ).fetchall()
            for previous, previous_hash, previous_embedding_json in rows:
                if previous_hash == digest:
                    return None
                if SequenceMatcher(None, normalized, previous).ratio() >= self.fuzzy_threshold:
                    return None
                if embedding is not None and previous_embedding_json:
                    previous_embedding = json.loads(previous_embedding_json)
                    if _cosine(embedding, previous_embedding) >= self.semantic_threshold:
                        return None
            if candidate_style:
                style_cutoff = (created_at - self.style_window).isoformat()
                style_row = self._connection.execute(
                    """
                    SELECT COUNT(*) FROM ecobot_expressions
                    WHERE channel_id = ? AND style_signature = ? AND created_at >= ?
                      AND status != 'failed'
                    """,
                    (channel_id, candidate_style, style_cutoff),
                ).fetchone()
                if style_row is not None and int(style_row[0]) >= self.style_repeat_limit:
                    return None
            cursor = self._connection.execute(
                """
                INSERT INTO ecobot_expressions(
                    channel_id, expression, normalized_expression, expression_hash,
                    embedding_json, style_signature, status, batch_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'reserved', ?, ?)
                """,
                (
                    channel_id,
                    expression,
                    normalized,
                    digest,
                    json.dumps(list(embedding)) if embedding is not None else None,
                    candidate_style or None,
                    batch_id,
                    created_at.isoformat(),
                ),
            )
            return int(cursor.lastrowid)

    def mark(self, reservation_id: int, *, success: bool, error: str | None = None) -> None:
        completed_at = _now()
        with self._guard, self._connection:
            row = self._connection.execute(
                "SELECT channel_id FROM ecobot_expressions WHERE id = ?",
                (reservation_id,),
            ).fetchone()
            self._connection.execute(
                """
                UPDATE ecobot_expressions SET status = ?, completed_at = ?, error = ?
                WHERE id = ?
                """,
                (
                    "sent" if success else "failed",
                    completed_at.isoformat(),
                    error,
                    reservation_id,
                ),
            )
            if success and row is not None:
                self._connection.execute(
                    """
                    INSERT INTO ecobot_desire_drives(
                        channel_id, accumulation_started_at, last_expression_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(channel_id) DO UPDATE SET
                        accumulation_started_at = excluded.accumulation_started_at,
                        last_expression_at = excluded.last_expression_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(row[0]),
                        completed_at.isoformat(),
                        completed_at.isoformat(),
                        completed_at.isoformat(),
                    ),
                )

    def close(self) -> None:
        with self._guard:
            self._connection.close()
