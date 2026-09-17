from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


@dataclass(frozen=True, slots=True)
class AgentState:
    agent_id: str
    version: int
    activity: str
    behavior: str
    scene: str
    location: str | None
    focus: str | None
    companions: tuple[str, ...]
    mood: str
    energy: float
    hunger: float
    fatigue: float
    social_drive: float
    goal: str | None
    activity_started_at: datetime
    expected_end_at: datetime | None
    updated_at: datetime
    metadata: Mapping[str, Any]

    def to_prompt_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["activity_started_at"] = _iso(self.activity_started_at)
        value["expected_end_at"] = _iso(self.expected_end_at)
        value["updated_at"] = _iso(self.updated_at)
        return value


class StateConflictError(RuntimeError):
    pass


class AgentStateStore:
    """Persistent single-agent state and behavior timeline."""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ecobot_agent_states (
                    agent_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    activity TEXT NOT NULL,
                    behavior TEXT NOT NULL,
                    scene TEXT NOT NULL,
                    location TEXT,
                    focus TEXT,
                    companions_json TEXT NOT NULL,
                    mood TEXT NOT NULL,
                    energy REAL NOT NULL,
                    hunger REAL NOT NULL,
                    fatigue REAL NOT NULL,
                    social_drive REAL NOT NULL,
                    goal TEXT,
                    activity_started_at TEXT NOT NULL,
                    expected_end_at TEXT,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ecobot_agent_state_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL REFERENCES ecobot_agent_states(agent_id),
                    version INTEGER NOT NULL,
                    trigger TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    batch_id TEXT,
                    previous_state_json TEXT,
                    next_state_json TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    UNIQUE(agent_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot_agent_state_history
                    ON ecobot_agent_state_history(agent_id, changed_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS ecobot_agent_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL REFERENCES ecobot_agent_states(agent_id),
                    batch_id TEXT,
                    channel_id TEXT,
                    action_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scene TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    expected_end_at TEXT,
                    completed_at TEXT,
                    completion_reason TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot_agent_actions_active
                    ON ecobot_agent_actions(agent_id, status, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ecobot_agent_actions_channel
                    ON ecobot_agent_actions(channel_id, started_at DESC);

                CREATE TABLE IF NOT EXISTS ecobot_agent_intentions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL REFERENCES ecobot_agent_states(agent_id),
                    activity TEXT NOT NULL,
                    behavior TEXT NOT NULL,
                    scene TEXT,
                    location TEXT,
                    not_before TEXT NOT NULL,
                    expires_at TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    consumed_at TEXT,
                    metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot_agent_intentions_due
                    ON ecobot_agent_intentions(agent_id, status, not_before, priority DESC);
                """
            )

    def get(self, agent_id: str, *, now: datetime | None = None) -> AgentState:
        if not agent_id.strip():
            raise ValueError("agent_id must not be empty")
        checked_at = now or utc_now()
        with self._guard, self._connection:
            row = self._connection.execute(
                "SELECT * FROM ecobot_agent_states WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if row is None:
                state = AgentState(
                    agent_id=agent_id,
                    version=1,
                    activity="idle",
                    behavior="quietly observing the current scene",
                    scene="online",
                    location=None,
                    focus=None,
                    companions=(),
                    mood="calm",
                    energy=70.0,
                    hunger=20.0,
                    fatigue=20.0,
                    social_drive=45.0,
                    goal=None,
                    activity_started_at=checked_at,
                    expected_end_at=None,
                    updated_at=checked_at,
                    metadata={},
                )
                self._insert_state(state)
                self._insert_history(None, state, "initialize", "initial state", None)
                return state
            return self._row_to_state(row)

    def advance_due(
        self, agent_id: str, *, now: datetime | None = None
    ) -> AgentState:
        checked_at = now or utc_now()
        state = self.get(agent_id, now=checked_at)
        if state.expected_end_at is None or state.expected_end_at > checked_at:
            return state
        return self.transition(
            agent_id,
            {
                "activity": "idle",
                "behavior": f"finished {state.activity} and is deciding what to do next",
                "focus": None,
                "expected_duration_seconds": None,
            },
            trigger="time_due",
            reason=f"scheduled activity {state.activity} reached its expected end",
            expected_version=state.version,
            now=checked_at,
        )

    def transition(
        self,
        agent_id: str,
        update: Mapping[str, Any],
        *,
        trigger: str,
        reason: str,
        expected_version: int | None = None,
        batch_id: str | None = None,
        channel_id: str | None = None,
        now: datetime | None = None,
    ) -> AgentState:
        changed_at = now or utc_now()
        with self._guard, self._connection:
            current = self.get(agent_id, now=changed_at)
            if expected_version is not None and current.version != expected_version:
                raise StateConflictError(
                    f"agent state version changed: expected {expected_version}, got {current.version}"
                )
            next_state = self._build_next_state(current, update, changed_at)
            cursor = self._connection.execute(
                """
                UPDATE ecobot_agent_states SET
                    version = ?, activity = ?, behavior = ?, scene = ?, location = ?,
                    focus = ?, companions_json = ?, mood = ?, energy = ?, hunger = ?,
                    fatigue = ?, social_drive = ?, goal = ?, activity_started_at = ?,
                    expected_end_at = ?, updated_at = ?, metadata_json = ?
                WHERE agent_id = ? AND version = ?
                """,
                self._state_values(next_state) + (agent_id, current.version),
            )
            if cursor.rowcount != 1:
                raise StateConflictError("agent state was updated concurrently")
            self._insert_history(
                current, next_state, trigger, reason, batch_id
            )
            if (
                next_state.activity != current.activity
                or next_state.behavior != current.behavior
            ):
                self._complete_active_actions(agent_id, changed_at, "state_transition")
                self._connection.execute(
                    """
                    INSERT INTO ecobot_agent_actions(
                        agent_id, batch_id, channel_id, action_type, description,
                        status, scene, started_at, expected_end_at, metadata_json
                    ) VALUES (?, ?, ?, 'state_behavior', ?, 'active', ?, ?, ?, ?)
                    """,
                    (
                        agent_id,
                        batch_id,
                        channel_id,
                        next_state.behavior,
                        next_state.scene,
                        _iso(next_state.activity_started_at),
                        _iso(next_state.expected_end_at),
                        _json({"activity": next_state.activity, "trigger": trigger}),
                    ),
                )
            return next_state

    def schedule_intention(
        self,
        agent_id: str,
        *,
        activity: str,
        behavior: str,
        not_before: datetime,
        reason: str,
        scene: str | None = None,
        location: str | None = None,
        expires_at: datetime | None = None,
        priority: int = 0,
        metadata: Mapping[str, Any] | None = None,
    ) -> int:
        self.get(agent_id)
        with self._guard, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO ecobot_agent_intentions(
                    agent_id, activity, behavior, scene, location, not_before,
                    expires_at, priority, reason, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    activity,
                    behavior,
                    scene,
                    location,
                    _iso(not_before),
                    _iso(expires_at),
                    priority,
                    reason,
                    _iso(utc_now()),
                    _json(dict(metadata or {})),
                ),
            )
            return int(cursor.lastrowid)

    def consume_due_intention(
        self, agent_id: str, *, now: datetime | None = None
    ) -> dict[str, Any] | None:
        checked_at = now or utc_now()
        with self._guard, self._connection:
            self._connection.execute(
                """
                UPDATE ecobot_agent_intentions SET status = 'expired', consumed_at = ?
                WHERE agent_id = ? AND status = 'pending'
                  AND expires_at IS NOT NULL AND expires_at < ?
                """,
                (_iso(checked_at), agent_id, _iso(checked_at)),
            )
            row = self._connection.execute(
                """
                SELECT id, activity, behavior, scene, location, reason, metadata_json
                FROM ecobot_agent_intentions
                WHERE agent_id = ? AND status = 'pending' AND not_before <= ?
                ORDER BY priority DESC, not_before ASC, id ASC LIMIT 1
                """,
                (agent_id, _iso(checked_at)),
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                """
                UPDATE ecobot_agent_intentions SET status = 'consumed', consumed_at = ?
                WHERE id = ?
                """,
                (_iso(checked_at), row[0]),
            )
            return {
                "id": int(row[0]),
                "activity": row[1],
                "behavior": row[2],
                "scene": row[3],
                "location": row[4],
                "reason": row[5],
                "metadata": json.loads(row[6]),
            }

    def recent_history(self, agent_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT version, trigger, reason, next_state_json, changed_at
                FROM ecobot_agent_state_history WHERE agent_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (agent_id, max(1, limit)),
            ).fetchall()
            return [
                {
                    "version": row[0],
                    "trigger": row[1],
                    "reason": row[2],
                    "state": json.loads(row[3]),
                    "changed_at": row[4],
                }
                for row in reversed(rows)
            ]

    def _build_next_state(
        self, current: AgentState, update: Mapping[str, Any], changed_at: datetime
    ) -> AgentState:
        activity = str(update.get("activity", current.activity)).strip()
        behavior = str(update.get("behavior", current.behavior)).strip()
        scene = str(update.get("scene", current.scene)).strip()
        mood = str(update.get("mood", current.mood)).strip()
        if not all((activity, behavior, scene, mood)):
            raise ValueError("activity, behavior, scene, and mood must not be empty")
        companions_value = update.get("companions", current.companions)
        companions = tuple(str(item) for item in companions_value)
        duration = update.get("expected_duration_seconds", "unchanged")
        if duration == "unchanged":
            expected_end_at = current.expected_end_at
        elif duration is None:
            expected_end_at = None
        else:
            seconds = max(0.0, float(duration))
            expected_end_at = changed_at + timedelta(seconds=seconds)
        activity_changed = activity != current.activity or behavior != current.behavior
        return replace(
            current,
            version=current.version + 1,
            activity=activity,
            behavior=behavior,
            scene=scene,
            location=self._optional_text(update.get("location", current.location)),
            focus=self._optional_text(update.get("focus", current.focus)),
            companions=companions,
            mood=mood,
            energy=_clamp(current.energy + float(update.get("energy_delta", 0.0))),
            hunger=_clamp(current.hunger + float(update.get("hunger_delta", 0.0))),
            fatigue=_clamp(current.fatigue + float(update.get("fatigue_delta", 0.0))),
            social_drive=_clamp(
                current.social_drive + float(update.get("social_drive_delta", 0.0))
            ),
            goal=self._optional_text(update.get("goal", current.goal)),
            activity_started_at=changed_at if activity_changed else current.activity_started_at,
            expected_end_at=expected_end_at,
            updated_at=changed_at,
            metadata={**dict(current.metadata), **dict(update.get("metadata", {}))},
        )

    def _insert_state(self, state: AgentState) -> None:
        self._connection.execute(
            """
            INSERT INTO ecobot_agent_states(
                agent_id, version, activity, behavior, scene, location, focus,
                companions_json, mood, energy, hunger, fatigue, social_drive,
                goal, activity_started_at, expected_end_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (state.agent_id,) + self._state_values(state),
        )

    @staticmethod
    def _state_values(state: AgentState) -> tuple[Any, ...]:
        return (
            state.version,
            state.activity,
            state.behavior,
            state.scene,
            state.location,
            state.focus,
            _json(state.companions),
            state.mood,
            state.energy,
            state.hunger,
            state.fatigue,
            state.social_drive,
            state.goal,
            _iso(state.activity_started_at),
            _iso(state.expected_end_at),
            _iso(state.updated_at),
            _json(dict(state.metadata)),
        )

    def _insert_history(
        self,
        previous: AgentState | None,
        next_state: AgentState,
        trigger: str,
        reason: str,
        batch_id: str | None,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO ecobot_agent_state_history(
                agent_id, version, trigger, reason, batch_id,
                previous_state_json, next_state_json, changed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                next_state.agent_id,
                next_state.version,
                trigger,
                reason,
                batch_id,
                _json(previous.to_prompt_dict()) if previous else None,
                _json(next_state.to_prompt_dict()),
                _iso(next_state.updated_at),
            ),
        )

    def _complete_active_actions(
        self, agent_id: str, completed_at: datetime, reason: str
    ) -> None:
        self._connection.execute(
            """
            UPDATE ecobot_agent_actions SET
                status = 'completed', completed_at = ?, completion_reason = ?
            WHERE agent_id = ? AND status = 'active'
            """,
            (_iso(completed_at), reason, agent_id),
        )

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _row_to_state(row: tuple[Any, ...]) -> AgentState:
        return AgentState(
            agent_id=row[0],
            version=int(row[1]),
            activity=row[2],
            behavior=row[3],
            scene=row[4],
            location=row[5],
            focus=row[6],
            companions=tuple(json.loads(row[7])),
            mood=row[8],
            energy=float(row[9]),
            hunger=float(row[10]),
            fatigue=float(row[11]),
            social_drive=float(row[12]),
            goal=row[13],
            activity_started_at=_parse(row[14]) or utc_now(),
            expected_end_at=_parse(row[15]),
            updated_at=_parse(row[16]) or utc_now(),
            metadata=json.loads(row[17]),
        )

    def close(self) -> None:
        with self._guard:
            self._connection.close()
