from __future__ import annotations

import asyncio
import json
import math
import sqlite3
import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from enum import Enum
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from .affinity import (
    INITIAL_AFFINITY,
    INITIAL_TRUST,
    MINIMUM_AFFINITY_CHANGE,
    affinity_guidance,
    affinity_stage,
)
from .contracts import ActionFeedback, Inference, Stimulus


@dataclass(frozen=True, slots=True)
class WorldEvent:
    kind: str
    payload: Any


@dataclass(frozen=True, slots=True)
class WorldSnapshot:
    channel_id: str
    revision: int
    relation_scores: dict[str, float]
    recent_events: tuple[WorldEvent, ...]
    agent_state: dict[str, Any] = field(default_factory=dict)
    memories: tuple[dict[str, Any], ...] = ()
    social_context: dict[str, Any] = field(default_factory=dict)
    affinity: dict[str, Any] = field(default_factory=dict)
    style_reference: dict[str, Any] = field(default_factory=dict)
    reply_style_guard: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _ChannelState:
    revision: int = 0
    relation_scores: dict[str, float] = field(default_factory=dict)
    events: list[WorldEvent] = field(default_factory=list)


class WorldModel:
    """Small transactional world-state boundary for the behavior kernel."""

    def __init__(
        self,
        max_events_per_channel: int = 200,
        database_path: str | Path | None = None,
    ) -> None:
        if max_events_per_channel < 1:
            raise ValueError("max_events_per_channel must be positive")
        self._max_events = max_events_per_channel
        self._channels: dict[str, _ChannelState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._affinity_enabled = True
        self._affinity_positive_step_limit = 20.0
        self._affinity_negative_step_limit = 30.0
        self._affinity_irritation_half_life_hours = 12.0
        if database_path is not None:
            path = Path(database_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(path, check_same_thread=False)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._initialize_schema()

    def _initialize_schema(self) -> None:
        if self._connection is None:
            return
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ecobot_world_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_id, revision)
            );
            CREATE INDEX IF NOT EXISTS idx_ecobot_world_channel_revision
                ON ecobot_world_events(channel_id, revision DESC);
            CREATE TABLE IF NOT EXISTS ecobot_relations (
                channel_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                score REAL NOT NULL,
                updated_revision INTEGER NOT NULL,
                PRIMARY KEY(channel_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS ecobot_affinity_profiles (
                user_id TEXT PRIMARY KEY,
                affinity_score REAL NOT NULL DEFAULT 10,
                trust_score REAL NOT NULL DEFAULT 10,
                familiarity REAL NOT NULL DEFAULT 0,
                irritation REAL NOT NULL DEFAULT 0,
                special_level TEXT NOT NULL DEFAULT 'none',
                special_reason TEXT,
                special_set_at TEXT,
                interaction_count INTEGER NOT NULL DEFAULT 0,
                positive_interactions INTEGER NOT NULL DEFAULT 0,
                negative_interactions INTEGER NOT NULL DEFAULT 0,
                last_reason TEXT,
                first_interaction_at TEXT NOT NULL,
                last_interaction_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ecobot_affinity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                batch_id TEXT,
                raw_affinity_delta REAL NOT NULL,
                applied_affinity_delta REAL NOT NULL,
                raw_trust_delta REAL NOT NULL,
                applied_trust_delta REAL NOT NULL,
                familiarity_delta REAL NOT NULL,
                irritation_delta REAL NOT NULL,
                confidence REAL NOT NULL,
                reason TEXT,
                previous_stage TEXT NOT NULL,
                new_stage TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ecobot_affinity_events_user
                ON ecobot_affinity_events(user_id, id DESC);
            """
        )
        columns = {
            row[1]
            for row in self._connection.execute(
                "PRAGMA table_info(ecobot_affinity_profiles)"
            ).fetchall()
        }
        for name, statement in (
            ("special_level", "ALTER TABLE ecobot_affinity_profiles ADD COLUMN special_level TEXT NOT NULL DEFAULT 'none'"),
            ("special_reason", "ALTER TABLE ecobot_affinity_profiles ADD COLUMN special_reason TEXT"),
            ("special_set_at", "ALTER TABLE ecobot_affinity_profiles ADD COLUMN special_set_at TEXT"),
        ):
            if name not in columns:
                self._connection.execute(statement)
        now = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            """
            INSERT OR IGNORE INTO ecobot_affinity_profiles(
                user_id, affinity_score, trust_score, familiarity, irritation,
                interaction_count, positive_interactions, negative_interactions,
                last_reason, first_interaction_at, last_interaction_at, updated_at
            )
            SELECT user_id, AVG(score), 10, MIN(100, COUNT(*) * 2), 0,
                   0, 0, 0, '从旧版频道关系分迁移', ?, ?, ?
            FROM ecobot_relations GROUP BY user_id
            """,
            (now, now, now),
        )
        self._connection.commit()

    def configure_affinity(
        self,
        *,
        enabled: bool,
        positive_step_limit: float,
        negative_step_limit: float,
        irritation_half_life_hours: float,
    ) -> None:
        self._affinity_enabled = bool(enabled)
        self._affinity_positive_step_limit = max(0.1, float(positive_step_limit))
        self._affinity_negative_step_limit = max(0.1, float(negative_step_limit))
        self._affinity_irritation_half_life_hours = max(
            0.1, float(irritation_half_life_hours)
        )

    def _state(self, channel_id: str) -> _ChannelState:
        state = self._channels.get(channel_id)
        if state is not None:
            return state
        state = self._load_state(channel_id)
        self._channels[channel_id] = state
        return state

    def _load_state(self, channel_id: str) -> _ChannelState:
        if self._connection is None:
            return _ChannelState()
        relation_rows = self._connection.execute(
            "SELECT user_id, score FROM ecobot_relations WHERE channel_id = ?",
            (channel_id,),
        ).fetchall()
        event_rows = self._connection.execute(
            """
            SELECT revision, kind, payload_json
            FROM ecobot_world_events
            WHERE channel_id = ?
            ORDER BY revision DESC
            LIMIT ?
            """,
            (channel_id, self._max_events),
        ).fetchall()
        events = [
            WorldEvent(kind, json.loads(payload_json))
            for _, kind, payload_json in reversed(event_rows)
        ]
        revision = max((row[0] for row in event_rows), default=0)
        return _ChannelState(
            revision=revision,
            relation_scores={user_id: float(score) for user_id, score in relation_rows},
            events=events,
        )

    def channel_lock(self, channel_id: str) -> asyncio.Lock:
        return self._locks.setdefault(channel_id, asyncio.Lock())

    def snapshot(
        self,
        channel_id: str,
        *,
        agent_state: Mapping[str, Any] | None = None,
        memories: tuple[dict[str, Any], ...] = (),
        social_context: Mapping[str, Any] | None = None,
        focus_user_id: str | None = None,
        style_reference: Mapping[str, Any] | None = None,
        reply_style_guard: Mapping[str, Any] | None = None,
    ) -> WorldSnapshot:
        with self._guard:
            state = self._state(channel_id)
            return WorldSnapshot(
                channel_id=channel_id,
                revision=state.revision,
                relation_scores=dict(state.relation_scores),
                recent_events=tuple(state.events),
                agent_state=dict(agent_state or {}),
                memories=memories,
                social_context=dict(social_context or {}),
                affinity=(
                    self.affinity_profile(focus_user_id)
                    if focus_user_id and self._affinity_enabled
                    else {}
                ),
                style_reference=dict(style_reference or {}),
                reply_style_guard=dict(reply_style_guard or {}),
            )

    def record_stimulus(self, stimulus: Stimulus) -> None:
        self._append(stimulus.channel_id, WorldEvent("stimulus", stimulus))

    def record_feedback(self, channel_id: str, feedback: ActionFeedback) -> None:
        self._append(channel_id, WorldEvent("action_feedback", feedback))

    def commit_inference(
        self,
        channel_id: str,
        user_id: str,
        inference: Inference,
        *,
        batch_id: str | None = None,
        occurred_at: datetime | None = None,
        update_affinity: bool = True,
        update_relation: bool = True,
    ) -> float:
        with self._guard:
            state = self._state(channel_id)
            current = state.relation_scores.get(user_id, 0.0)
            updated = current
            if update_relation:
                updated = max(-100.0, min(100.0, current + inference.relation_delta))
                state.relation_scores[user_id] = updated
            event = WorldEvent("inference", inference)
            revision = self._append_memory(state, event)
            if self._connection is not None:
                with self._connection:
                    self._insert_event(channel_id, revision, event)
                    if update_relation:
                        self._connection.execute(
                            """
                            INSERT INTO ecobot_relations(channel_id, user_id, score, updated_revision)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(channel_id, user_id) DO UPDATE SET
                                score = excluded.score,
                                updated_revision = excluded.updated_revision
                            """,
                            (channel_id, user_id, updated, revision),
                        )
                    self._prune_events(channel_id)
                    if self._affinity_enabled and update_affinity:
                        self._commit_affinity(
                            channel_id,
                            user_id,
                            inference,
                            batch_id=batch_id,
                            occurred_at=occurred_at,
                        )
            return updated

    def affinity_profile(
        self, user_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        current_time = now or datetime.now(timezone.utc)
        if self._connection is None:
            return self._empty_affinity(user_id)
        with self._guard:
            row = self._connection.execute(
                """
                SELECT affinity_score, trust_score, familiarity, irritation,
                       special_level, special_reason, special_set_at,
                       interaction_count, positive_interactions, negative_interactions,
                       last_reason, first_interaction_at, last_interaction_at, updated_at
                FROM ecobot_affinity_profiles WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            return self._empty_affinity(user_id)
        irritation = self._decayed_irritation(float(row[3]), row[13], current_time)
        score = float(row[0])
        trust = float(row[1])
        familiarity = float(row[2])
        special_level = str(row[4] or "none")
        stage = self._affinity_stage(score, trust, familiarity, special_level)
        return {
            "user_id": user_id,
            "affinity_score": round(score, 2),
            "trust_score": round(trust, 2),
            "familiarity": round(familiarity, 2),
            "irritation": round(irritation, 2),
            "special_level": special_level,
            "special_locked": special_level != "none",
            "special_reason": row[5],
            "special_set_at": row[6],
            "stage": stage,
            "interaction_count": int(row[7]),
            "positive_interactions": int(row[8]),
            "negative_interactions": int(row[9]),
            "last_reason": row[10],
            "first_interaction_at": row[11],
            "last_interaction_at": row[12],
            "updated_at": row[13],
            "response_guidance": self._affinity_guidance(
                stage, trust, familiarity, irritation
            ),
        }

    def _commit_affinity(
        self,
        channel_id: str,
        user_id: str,
        inference: Inference,
        *,
        batch_id: str | None,
        occurred_at: datetime | None,
    ) -> float:
        assert self._connection is not None
        now = occurred_at or datetime.now(timezone.utc)
        now_text = now.isoformat()
        row = self._connection.execute(
            """
            SELECT affinity_score, trust_score, familiarity, irritation,
                   special_level, interaction_count, positive_interactions, negative_interactions,
                   first_interaction_at, updated_at
            FROM ecobot_affinity_profiles WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            current_score = INITIAL_AFFINITY
            current_trust = INITIAL_TRUST
            current_familiarity = current_irritation = 0.0
            special_level = "none"
            interaction_count = positive_count = negative_count = 0
            first_interaction_at = now_text
            previous_updated_at = now_text
        else:
            current_score = float(row[0])
            current_trust = float(row[1])
            current_familiarity = float(row[2])
            current_irritation = float(row[3])
            special_level = str(row[4] or "none")
            interaction_count = int(row[5])
            positive_count = int(row[6])
            negative_count = int(row[7])
            first_interaction_at = row[8]
            previous_updated_at = row[9]

        confidence = max(0.0, min(1.0, float(inference.confidence)))
        current_irritation = self._decayed_irritation(
            current_irritation, previous_updated_at, now
        )
        raw_affinity = float(inference.relation_delta)
        affinity_delta = raw_affinity * confidence
        if affinity_delta >= 0:
            damping = max(0.2, 1.0 - max(0.0, current_score) / 110.0)
            affinity_delta = min(
                self._affinity_positive_step_limit, affinity_delta * damping
            )
        else:
            damping = max(0.35, 1.0 - max(0.0, -current_score) / 120.0)
            affinity_delta = max(
                -self._affinity_negative_step_limit, affinity_delta * damping
            )
        if abs(affinity_delta) < MINIMUM_AFFINITY_CHANGE:
            affinity_delta = (
                MINIMUM_AFFINITY_CHANGE
                if raw_affinity >= 0
                else -MINIMUM_AFFINITY_CHANGE
            )

        raw_trust = float(inference.trust_delta)
        trust_delta = raw_trust * confidence
        trust_delta = max(
            -self._affinity_negative_step_limit * 0.8,
            min(self._affinity_positive_step_limit * 0.7, trust_delta),
        )
        familiarity_delta = float(inference.familiarity_delta)
        if familiarity_delta <= 0:
            familiarity_delta = 0.25 + 0.75 * confidence
        familiarity_delta = max(0.0, min(2.0, familiarity_delta))
        irritation_delta = (
            min(15.0, abs(affinity_delta) * 2.0 + abs(min(0.0, trust_delta)))
            if affinity_delta < 0 or trust_delta < 0
            else max(-5.0, -affinity_delta * 0.6)
        )

        applied_affinity_delta = 0.0 if special_level != "none" else affinity_delta
        new_score = max(-100.0, min(100.0, current_score + applied_affinity_delta))
        new_trust = max(-100.0, min(100.0, current_trust + trust_delta))
        new_familiarity = max(
            0.0, min(100.0, current_familiarity + familiarity_delta)
        )
        new_irritation = max(
            0.0, min(100.0, current_irritation + irritation_delta)
        )
        previous_stage = self._affinity_stage(
            current_score, current_trust, current_familiarity, special_level
        )
        new_stage = self._affinity_stage(new_score, new_trust, new_familiarity, special_level)
        positive_count += int(applied_affinity_delta > 0.05)
        negative_count += int(applied_affinity_delta < -0.05)
        reason = inference.relationship_reason.strip() or None
        self._connection.execute(
            """
            INSERT INTO ecobot_affinity_profiles(
                user_id, affinity_score, trust_score, familiarity, irritation,
                special_level,
                interaction_count, positive_interactions, negative_interactions,
                last_reason, first_interaction_at, last_interaction_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                affinity_score = excluded.affinity_score,
                trust_score = excluded.trust_score,
                familiarity = excluded.familiarity,
                irritation = excluded.irritation,
                special_level = excluded.special_level,
                interaction_count = excluded.interaction_count,
                positive_interactions = excluded.positive_interactions,
                negative_interactions = excluded.negative_interactions,
                last_reason = excluded.last_reason,
                last_interaction_at = excluded.last_interaction_at,
                updated_at = excluded.updated_at
            """,
            (
                user_id, new_score, new_trust, new_familiarity, new_irritation,
                special_level,
                interaction_count + 1, positive_count, negative_count, reason,
                first_interaction_at, now_text, now_text,
            ),
        )
        self._connection.execute(
            """
            INSERT INTO ecobot_affinity_events(
                user_id, channel_id, batch_id, raw_affinity_delta,
                applied_affinity_delta, raw_trust_delta, applied_trust_delta,
                familiarity_delta, irritation_delta, confidence, reason,
                previous_stage, new_stage, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, channel_id, batch_id, raw_affinity, applied_affinity_delta,
                raw_trust, trust_delta, familiarity_delta, irritation_delta,
                confidence, reason, previous_stage, new_stage, now_text,
            ),
        )
        return new_score

    def _decayed_irritation(
        self, value: float, updated_at: str, now: datetime
    ) -> float:
        try:
            previous = datetime.fromisoformat(updated_at)
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            elapsed_hours = max(0.0, (now - previous).total_seconds() / 3600.0)
        except (TypeError, ValueError):
            elapsed_hours = 0.0
        return value * math.pow(0.5, elapsed_hours / self._affinity_irritation_half_life_hours)

    @staticmethod
    def _affinity_stage(
        score: float, trust: float, familiarity: float, special_level: str = "none"
    ) -> str:
        return affinity_stage(score, special_level)

    @staticmethod
    def _affinity_guidance(
        stage: str, trust: float, familiarity: float, irritation: float
    ) -> str:
        return affinity_guidance(stage, trust, familiarity, irritation)

    @staticmethod
    def _empty_affinity(user_id: str) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "affinity_score": INITIAL_AFFINITY,
            "trust_score": INITIAL_TRUST,
            "familiarity": 0.0,
            "irritation": 0.0,
            "special_level": "none",
            "special_locked": False,
            "special_reason": None,
            "special_set_at": None,
            "stage": "初步认识",
            "interaction_count": 0,
            "positive_interactions": 0,
            "negative_interactions": 0,
            "last_reason": None,
            "response_guidance": affinity_guidance("初步认识", INITIAL_TRUST, 0, 0),
        }

    def _append(self, channel_id: str, event: WorldEvent) -> None:
        with self._guard:
            state = self._state(channel_id)
            revision = self._append_memory(state, event)
            if self._connection is not None:
                with self._connection:
                    self._insert_event(channel_id, revision, event)
                    self._prune_events(channel_id)

    def _append_memory(self, state: _ChannelState, event: WorldEvent) -> int:
        state.events.append(event)
        if len(state.events) > self._max_events:
            del state.events[: len(state.events) - self._max_events]
        state.revision += 1
        return state.revision

    def _insert_event(
        self, channel_id: str, revision: int, event: WorldEvent
    ) -> None:
        assert self._connection is not None
        self._connection.execute(
            """
            INSERT INTO ecobot_world_events(channel_id, revision, kind, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                channel_id,
                revision,
                event.kind,
                json.dumps(_jsonable(event.payload), ensure_ascii=False),
            ),
        )

    def _prune_events(self, channel_id: str) -> None:
        assert self._connection is not None
        self._connection.execute(
            """
            DELETE FROM ecobot_world_events
            WHERE id IN (
                SELECT id FROM ecobot_world_events
                WHERE channel_id = ?
                ORDER BY revision DESC
                LIMIT -1 OFFSET ?
            )
            """,
            (channel_id, self._max_events),
        )

    def close(self) -> None:
        with self._guard:
            if self._connection is not None:
                self._connection.close()
                self._connection = None


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _jsonable(getattr(value, item.name))
            for item in dataclass_fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)
