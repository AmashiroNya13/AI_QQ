from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from .contracts import BehaviorResult, Stimulus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _serialize_stimulus(stimulus: Stimulus) -> str:
    return json.dumps(
        {
            "event_id": stimulus.event_id,
            "channel_id": stimulus.channel_id,
            "user_id": stimulus.user_id,
            "content": stimulus.content,
            "timestamp": stimulus.timestamp,
            "metadata": dict(stimulus.metadata),
        },
        ensure_ascii=False,
        default=str,
    )


@dataclass(frozen=True, slots=True)
class RefreshDecision:
    strategy: str
    reason: str
    priority: int
    batch_id: str | None = None
    batch_number: int | None = None

    @property
    def should_process(self) -> bool:
        return self.strategy == "process"


BatchProcessor = Callable[[str], Awaitable[BehaviorResult | None]]


class RefreshScheduler:
    """Persistent debounce, batch, and idle scheduling boundary."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        passive_interval: timedelta = timedelta(seconds=15),
        idle_interval: timedelta = timedelta(minutes=10),
    ) -> None:
        if passive_interval.total_seconds() < 0:
            raise ValueError("passive_interval must not be negative")
        if idle_interval.total_seconds() <= 0:
            raise ValueError("idle_interval must be positive")
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.passive_interval = passive_interval
        self.idle_interval = idle_interval
        self._guard = threading.RLock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ecobot_refresh_channels (
                    channel_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    batch_counter INTEGER NOT NULL DEFAULT 0,
                    is_busy INTEGER NOT NULL DEFAULT 0,
                    last_event_at TEXT,
                    last_processed_at TEXT,
                    next_idle_at TEXT NOT NULL,
                    last_trigger TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot_refresh_channels_idle
                    ON ecobot_refresh_channels(is_busy, next_idle_at);

                CREATE TABLE IF NOT EXISTS ecobot_behavior_batches (
                    batch_id TEXT PRIMARY KEY,
                    batch_number INTEGER NOT NULL,
                    channel_id TEXT NOT NULL REFERENCES ecobot_refresh_channels(channel_id),
                    agent_id TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    stimulus_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    stop_reason TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot_behavior_batches_channel
                    ON ecobot_behavior_batches(channel_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS ecobot_refresh_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    batch_id TEXT,
                    decided_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot_refresh_decisions_channel
                    ON ecobot_refresh_decisions(channel_id, decided_at DESC, id DESC);
                """
            )
            self._connection.execute(
                "UPDATE ecobot_refresh_channels SET is_busy = 0 WHERE is_busy != 0"
            )

    async def run(
        self,
        stimulus: Stimulus,
        processor: BatchProcessor,
        *,
        agent_id: str,
        now: datetime | None = None,
    ) -> tuple[RefreshDecision, BehaviorResult | None]:
        lock = self._locks.setdefault(stimulus.channel_id, asyncio.Lock())
        async with lock:
            checked_at = now or _utc_now()
            decision = self.decide(stimulus, agent_id=agent_id, now=checked_at)
            if not decision.should_process or decision.batch_id is None:
                return decision, None
            self._mark_started(decision.batch_id, stimulus.channel_id, checked_at)
            try:
                result = await processor(decision.batch_id)
            except Exception as exc:
                self._mark_failed(
                    decision.batch_id,
                    stimulus.channel_id,
                    type(exc).__name__ + ": " + str(exc),
                    _utc_now(),
                )
                raise
            self.complete(
                decision.batch_id,
                stimulus.channel_id,
                stop_reason=result.stop_reason if result is not None else "no_result",
                now=_utc_now(),
            )
            return decision, result

    def decide(
        self,
        stimulus: Stimulus,
        *,
        agent_id: str,
        now: datetime | None = None,
    ) -> RefreshDecision:
        checked_at = now or _utc_now()
        trigger = str(stimulus.metadata.get("trigger") or "message")
        with self._guard, self._connection:
            duplicate = self._connection.execute(
                """
                SELECT batch_id FROM ecobot_refresh_decisions
                WHERE channel_id = ? AND event_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (stimulus.channel_id, stimulus.event_id),
            ).fetchone()
            if duplicate is not None:
                return RefreshDecision(
                    "skip",
                    "duplicate event already scheduled",
                    0,
                    str(duplicate[0]) if duplicate[0] else None,
                    None,
                )
            row = self._ensure_channel(stimulus.channel_id, agent_id, checked_at)
            last_processed = (
                datetime.fromisoformat(row[2]) if row[2] is not None else None
            )
            is_wake = bool(stimulus.metadata.get("is_wake"))
            is_private = bool(stimulus.metadata.get("is_private"))
            is_idle = trigger == "idle"
            if is_idle:
                next_idle = datetime.fromisoformat(row[3])
                should_process = checked_at >= next_idle
                priority = 20
                reason = "idle heartbeat is due" if should_process else "idle heartbeat is not due"
            elif is_wake or is_private:
                should_process = True
                priority = 100 if is_wake else 80
                reason = "directly addressed message" if is_wake else "private message"
            elif last_processed is None:
                should_process = True
                priority = 40
                reason = "first observed event in channel"
            else:
                should_process = checked_at - last_processed >= self.passive_interval
                priority = 30
                reason = (
                    "passive observation interval elapsed"
                    if should_process
                    else "passive events coalesced by refresh scheduler"
                )

            batch_id = None
            batch_number = None
            strategy = "process" if should_process else "skip"
            if should_process:
                batch_number = int(row[0]) + 1
                batch_id = uuid.uuid4().hex
                self._connection.execute(
                    """
                    UPDATE ecobot_refresh_channels SET
                        batch_counter = ?, last_event_at = ?, last_trigger = ?
                    WHERE channel_id = ?
                    """,
                    (batch_number, _iso(checked_at), trigger, stimulus.channel_id),
                )
                self._connection.execute(
                    """
                    INSERT INTO ecobot_behavior_batches(
                        batch_id, batch_number, channel_id, agent_id, trigger,
                        priority, status, stimulus_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'waiting', ?, ?)
                    """,
                    (
                        batch_id,
                        batch_number,
                        stimulus.channel_id,
                        agent_id,
                        trigger,
                        priority,
                        _serialize_stimulus(stimulus),
                        _iso(checked_at),
                    ),
                )
            else:
                self._connection.execute(
                    """
                    UPDATE ecobot_refresh_channels SET last_event_at = ?, last_trigger = ?
                    WHERE channel_id = ?
                    """,
                    (_iso(checked_at), trigger, stimulus.channel_id),
                )
            self._connection.execute(
                """
                INSERT INTO ecobot_refresh_decisions(
                    channel_id, event_id, trigger, strategy, priority,
                    reason, batch_id, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stimulus.channel_id,
                    stimulus.event_id,
                    trigger,
                    strategy,
                    priority,
                    reason,
                    batch_id,
                    _iso(checked_at),
                ),
            )
            return RefreshDecision(strategy, reason, priority, batch_id, batch_number)

    def complete(
        self,
        batch_id: str,
        channel_id: str,
        *,
        stop_reason: str | None,
        idle_delay: timedelta | None = None,
        now: datetime | None = None,
    ) -> None:
        completed_at = now or _utc_now()
        delay = idle_delay or self.idle_interval
        with self._guard, self._connection:
            self._connection.execute(
                """
                UPDATE ecobot_behavior_batches SET
                    status = 'completed', completed_at = ?, stop_reason = ?
                WHERE batch_id = ?
                """,
                (_iso(completed_at), stop_reason, batch_id),
            )
            self._connection.execute(
                """
                UPDATE ecobot_refresh_channels SET
                    is_busy = 0, last_processed_at = ?, next_idle_at = ?
                WHERE channel_id = ?
                """,
                (_iso(completed_at), _iso(completed_at + delay), channel_id),
            )

    def due_idle_channels(
        self, *, now: datetime | None = None, limit: int = 20
    ) -> list[str]:
        checked_at = now or _utc_now()
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT channel_id FROM ecobot_refresh_channels
                WHERE is_busy = 0 AND next_idle_at <= ?
                ORDER BY next_idle_at ASC LIMIT ?
                """,
                (_iso(checked_at), max(1, limit)),
            ).fetchall()
            return [str(row[0]) for row in rows]

    def postpone_idle(
        self,
        channel_id: str,
        delay: timedelta,
        *,
        now: datetime | None = None,
    ) -> None:
        checked_at = now or _utc_now()
        with self._guard, self._connection:
            self._connection.execute(
                """
                UPDATE ecobot_refresh_channels SET next_idle_at = ? WHERE channel_id = ?
                """,
                (_iso(checked_at + delay), channel_id),
            )

    def _ensure_channel(
        self, channel_id: str, agent_id: str, checked_at: datetime
    ) -> tuple[Any, ...]:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO ecobot_refresh_channels(
                channel_id, agent_id, next_idle_at
            ) VALUES (?, ?, ?)
            """,
            (channel_id, agent_id, _iso(checked_at + self.idle_interval)),
        )
        row = self._connection.execute(
            """
            SELECT batch_counter, is_busy, last_processed_at, next_idle_at
            FROM ecobot_refresh_channels WHERE channel_id = ?
            """,
            (channel_id,),
        ).fetchone()
        assert row is not None
        return row

    def _mark_started(
        self, batch_id: str, channel_id: str, started_at: datetime
    ) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                UPDATE ecobot_behavior_batches SET status = 'running', started_at = ?
                WHERE batch_id = ?
                """,
                (_iso(started_at), batch_id),
            )
            self._connection.execute(
                "UPDATE ecobot_refresh_channels SET is_busy = 1 WHERE channel_id = ?",
                (channel_id,),
            )

    def _mark_failed(
        self, batch_id: str, channel_id: str, error: str, failed_at: datetime
    ) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                UPDATE ecobot_behavior_batches SET
                    status = 'failed', completed_at = ?, error = ?
                WHERE batch_id = ?
                """,
                (_iso(failed_at), error, batch_id),
            )
            self._connection.execute(
                """
                UPDATE ecobot_refresh_channels SET
                    is_busy = 0, next_idle_at = ? WHERE channel_id = ?
                """,
                (_iso(failed_at + self.idle_interval), channel_id),
            )

    def close(self) -> None:
        with self._guard:
            self._connection.close()
