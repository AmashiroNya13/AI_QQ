from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    AttentionDecision,
    ActionAttempt,
    ActionReceipt,
    AgentEvent,
    Appraisal,
    ObservedConsequence,
    PersistentIntent,
    SubjectiveState,
)
from .scene import SceneState
from .conversation import ConversationThread, DialogueAct, SocialObligation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


class AutonomousStore:
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
                CREATE TABLE IF NOT EXISTS ecobot2_schema_versions (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ecobot2_events (
                    event_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    channel_id TEXT,
                    actor_id TEXT,
                    target_id TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_events_time
                    ON ecobot2_events(occurred_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_subjective_state (
                    agent_id TEXT PRIMARY KEY,
                    location_id TEXT NOT NULL,
                    activity TEXT NOT NULL,
                    focus TEXT,
                    mood_json TEXT NOT NULL,
                    drives_json TEXT NOT NULL,
                    energy REAL NOT NULL,
                    attention_load REAL NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ecobot2_action_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    completed_at TEXT,
                    error_code TEXT,
                    error_detail TEXT,
                    platform_message_id TEXT,
                    observed INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_actions_time
                    ON ecobot2_action_attempts(started_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_consequences (
                    consequence_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    actor_id TEXT,
                    confidence REAL NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ecobot2_appraisals (
                    appraisal_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL,
                    relevance REAL NOT NULL,
                    valence REAL NOT NULL,
                    controllability REAL NOT NULL,
                    agency_confidence REAL NOT NULL,
                    boundary_violation REAL NOT NULL,
                    emotion TEXT NOT NULL,
                    intensity REAL NOT NULL,
                    relationship_target_id TEXT,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ecobot2_identity_revision_candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'candidate',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ecobot2_locations (
                    location_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    neighbors_json TEXT NOT NULL,
                    objects_json TEXT NOT NULL,
                    availability_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ecobot2_scene_states (
                    scene_id TEXT PRIMARY KEY,
                    location_id TEXT NOT NULL,
                    local_time TEXT NOT NULL,
                    activity TEXT NOT NULL,
                    occupants_json TEXT NOT NULL,
                    objects_json TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_scene_location
                    ON ecobot2_scene_states(location_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_intentions (
                    intent_id TEXT PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    target_id TEXT,
                    arguments_json TEXT NOT NULL,
                    priority REAL NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    expires_at TEXT,
                    resolution_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_intentions_status
                    ON ecobot2_intentions(status, priority DESC, created_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_attention_decisions (
                    decision_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    thread_id TEXT,
                    addressee_id TEXT,
                    should_reply INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    attention_cost REAL NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_attention_time
                    ON ecobot2_attention_decisions(created_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_conversation_threads (
                    thread_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    participants_json TEXT NOT NULL,
                    addressee_id TEXT,
                    salience REAL NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    last_event_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_threads_channel
                    ON ecobot2_conversation_threads(channel_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_dialogue_acts (
                    act_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    act_type TEXT NOT NULL,
                    target_id TEXT,
                    confidence REAL NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_dialogue_event
                    ON ecobot2_dialogue_acts(event_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_social_obligations (
                    obligation_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    source_event_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    target_id TEXT,
                    kind TEXT NOT NULL,
                    strength REAL NOT NULL,
                    status TEXT NOT NULL,
                    due_at TEXT,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_obligations_status
                    ON ecobot2_social_obligations(owner_id, status, strength DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_beliefs (
                    belief_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_event_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_beliefs_subject
                    ON ecobot2_beliefs(subject, status, updated_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_capabilities (
                    capability_name TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    available INTEGER NOT NULL,
                    requirements_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ecobot2_world_entities (
                    entity_id TEXT PRIMARY KEY,
                    entity_kind TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    first_event_id TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_event_id TEXT,
                    last_seen_at TEXT NOT NULL,
                    version INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_world_entities_kind
                    ON ecobot2_world_entities(entity_kind, last_seen_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_scene_expansion_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    requested_by_intent_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ecobot2_schedule_facts (
                    fact_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    location_id TEXT NOT NULL,
                    activity TEXT NOT NULL,
                    start_at TEXT NOT NULL,
                    end_at TEXT NOT NULL,
                    actors_json TEXT NOT NULL,
                    reactions_json TEXT NOT NULL,
                    source_event_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_schedule_time
                    ON ecobot2_schedule_facts(start_at, end_at, status);
                CREATE TABLE IF NOT EXISTS ecobot2_world_rules (
                    rule_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    trigger_action_type TEXT NOT NULL,
                    condition_json TEXT NOT NULL,
                    reactions_json TEXT NOT NULL,
                    priority REAL NOT NULL,
                    source_event_id TEXT,
                    enabled INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_world_rules_action
                    ON ecobot2_world_rules(trigger_action_type, enabled, priority DESC);
                """
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO ecobot2_schema_versions(version, applied_at) VALUES (1, ?)",
                (_now(),),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO ecobot2_schema_versions(version, applied_at) VALUES (2, ?)",
                (_now(),),
            )

    def record_event(self, event: AgentEvent) -> bool:
        with self._guard, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO ecobot2_events(
                    event_id, kind, occurred_at, channel_id, actor_id, target_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.kind,
                    event.occurred_at,
                    event.channel_id,
                    event.actor_id,
                    event.target_id,
                    _json(event.payload),
                ),
            )
            return cursor.rowcount > 0

    def save_subjective_state(self, state: SubjectiveState) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot2_subjective_state(
                    agent_id, location_id, activity, focus, mood_json, drives_json,
                    energy, attention_load, version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    location_id=excluded.location_id, activity=excluded.activity,
                    focus=excluded.focus, mood_json=excluded.mood_json,
                    drives_json=excluded.drives_json, energy=excluded.energy,
                    attention_load=excluded.attention_load, version=excluded.version,
                    updated_at=excluded.updated_at
                """,
                (
                    state.agent_id,
                    state.location_id,
                    state.activity,
                    state.focus,
                    _json(state.mood),
                    _json(state.drives),
                    state.energy,
                    state.attention_load,
                    state.version,
                    state.updated_at,
                ),
            )

    def subjective_state(self, agent_id: str) -> SubjectiveState | None:
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM ecobot2_subjective_state WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        if row is None:
            return None
        return SubjectiveState(
            agent_id=row["agent_id"],
            location_id=row["location_id"],
            activity=row["activity"],
            focus=row["focus"],
            mood=json.loads(row["mood_json"]),
            drives=json.loads(row["drives_json"]),
            energy=float(row["energy"]),
            attention_load=float(row["attention_load"]),
            version=int(row["version"]),
            updated_at=row["updated_at"],
        )

    def save_action_attempt(self, attempt: ActionAttempt) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO ecobot2_action_attempts(
                    attempt_id, intent_id, action_type, status, started_at, arguments_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.intent_id,
                    attempt.action_type,
                    attempt.status,
                    attempt.started_at,
                    _json(attempt.arguments),
                ),
            )

    def save_action_receipt(self, receipt: ActionReceipt) -> bool:
        with self._guard, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE ecobot2_action_attempts SET
                    status=?, completed_at=?, error_code=?, error_detail=?,
                    platform_message_id=?, observed=?
                WHERE attempt_id=?
                """,
                (
                    receipt.status,
                    receipt.completed_at,
                    receipt.error_code,
                    receipt.error_detail,
                    receipt.platform_message_id,
                    int(receipt.observed),
                    receipt.attempt_id,
                ),
            )
            return cursor.rowcount > 0

    def action_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM ecobot2_action_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            return None
        return dict(row) | {"arguments": json.loads(row["arguments_json"])}

    def intent_exists(self, intent_id: str) -> bool:
        with self._guard:
            row = self._connection.execute(
                "SELECT 1 FROM ecobot2_intentions WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
        return row is not None

    def save_consequence(self, consequence: ObservedConsequence) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO ecobot2_consequences(
                    consequence_id, attempt_id, kind, observed_at, actor_id,
                    confidence, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    consequence.consequence_id,
                    consequence.attempt_id,
                    consequence.kind,
                    consequence.observed_at,
                    consequence.actor_id,
                    consequence.confidence,
                    _json(consequence.payload),
                ),
            )

    def save_location(
        self,
        location_id: str,
        *,
        name: str,
        description: str = "",
        neighbors: list[str] | tuple[str, ...] = (),
        objects: dict[str, Any] | None = None,
        availability: dict[str, Any] | None = None,
    ) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot2_locations(
                    location_id, name, description, neighbors_json,
                    objects_json, availability_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(location_id) DO UPDATE SET
                    name=excluded.name, description=excluded.description,
                    neighbors_json=excluded.neighbors_json, objects_json=excluded.objects_json,
                    availability_json=excluded.availability_json, updated_at=excluded.updated_at
                """,
                (
                    str(location_id),
                    str(name),
                    str(description),
                    _json(list(neighbors)),
                    _json(dict(objects or {})),
                    _json(dict(availability or {})),
                    _now(),
                ),
            )

    def locations(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_locations ORDER BY location_id LIMIT ?",
                (max(1, min(5000, int(limit))),),
            ).fetchall()
        return [
            dict(row)
            | {
                "neighbors": json.loads(row["neighbors_json"]),
                "objects": json.loads(row["objects_json"]),
                "availability": json.loads(row["availability_json"]),
            }
            for row in rows
        ]

    def save_scene(self, scene: SceneState, *, updated_at: str | None = None) -> None:
        timestamp = updated_at or _now()
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot2_scene_states(
                    scene_id, location_id, local_time, activity, occupants_json,
                    objects_json, version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scene_id) DO UPDATE SET
                    location_id=excluded.location_id, local_time=excluded.local_time,
                    activity=excluded.activity, occupants_json=excluded.occupants_json,
                    objects_json=excluded.objects_json, version=excluded.version,
                    updated_at=excluded.updated_at
                """,
                (
                    scene.scene_id,
                    scene.location_id,
                    scene.local_time,
                    scene.activity,
                    _json(list(scene.occupants)),
                    _json(dict(scene.objects)),
                    scene.version,
                    timestamp,
                ),
            )

    def scene(self, scene_id: str = "main") -> SceneState | None:
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM ecobot2_scene_states WHERE scene_id = ?", (scene_id,)
            ).fetchone()
        if row is None:
            return None
        return SceneState(
            scene_id=row["scene_id"],
            location_id=row["location_id"],
            local_time=row["local_time"],
            activity=row["activity"],
            occupants=tuple(json.loads(row["occupants_json"])),
            objects=json.loads(row["objects_json"]),
            version=int(row["version"]),
        )

    def scenes(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_scene_states ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [
            dict(row)
            | {
                "occupants": json.loads(row["occupants_json"]),
                "objects": json.loads(row["objects_json"]),
            }
            for row in rows
        ]

    def save_intent(
        self,
        intent: PersistentIntent,
    ) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot2_intentions(
                    intent_id, action_type, target_id, arguments_json, priority,
                    reason, status, expires_at, resolution_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(intent_id) DO UPDATE SET
                    action_type=excluded.action_type, target_id=excluded.target_id,
                    arguments_json=excluded.arguments_json, priority=excluded.priority,
                    reason=excluded.reason, status=excluded.status, expires_at=excluded.expires_at,
                    resolution_json=excluded.resolution_json, updated_at=excluded.updated_at
                """,
                (
                    intent.intent_id,
                    intent.action_type,
                    intent.target_id,
                    _json(dict(intent.arguments)),
                    intent.priority,
                    intent.reason,
                    intent.status,
                    intent.expires_at,
                    _json(dict(intent.resolution)),
                    intent.created_at,
                    intent.updated_at,
                ),
            )

    def update_intent(
        self,
        intent_id: str,
        *,
        status: str | None = None,
        resolution: dict[str, Any] | None = None,
    ) -> None:
        updates: list[str] = ["updated_at = ?"]
        values: list[Any] = [_now()]
        if status is not None:
            updates.append("status = ?")
            values.append(status)
        if resolution is not None:
            updates.append("resolution_json = ?")
            values.append(_json(resolution))
        values.append(intent_id)
        with self._guard, self._connection:
            self._connection.execute(
                f"UPDATE ecobot2_intentions SET {', '.join(updates)} WHERE intent_id = ?",
                values,
            )

    def intentions(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ecobot2_intentions"
        values: list[Any] = []
        if status:
            query += " WHERE status = ?"
            values.append(status)
        query += " ORDER BY priority DESC, created_at DESC LIMIT ?"
        values.append(max(1, min(1000, int(limit))))
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return [
            dict(row)
            | {
                "arguments": json.loads(row["arguments_json"]),
                "resolution": json.loads(row["resolution_json"]),
            }
            for row in rows
        ]

    def save_attention_decision(self, decision: AttentionDecision) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO ecobot2_attention_decisions(
                    decision_id, event_id, channel_id, thread_id, addressee_id,
                    should_reply, confidence, attention_cost, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.event_id,
                    decision.channel_id,
                    decision.thread_id,
                    decision.addressee_id,
                    int(decision.should_reply),
                    decision.confidence,
                    decision.attention_cost,
                    decision.reason,
                    decision.created_at,
                ),
            )

    def save_thread(self, thread: ConversationThread) -> None:
        timestamp = _now()
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot2_conversation_threads(
                    thread_id, channel_id, topic, participants_json, addressee_id,
                    salience, status, version, last_event_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    topic=excluded.topic, participants_json=excluded.participants_json,
                    addressee_id=excluded.addressee_id, salience=excluded.salience,
                    status=excluded.status, version=excluded.version,
                    last_event_at=excluded.last_event_at, updated_at=excluded.updated_at
                """,
                (
                    thread.thread_id,
                    thread.channel_id,
                    thread.topic,
                    _json(list(thread.participants)),
                    thread.addressee_id,
                    thread.salience,
                    thread.status,
                    thread.version,
                    thread.last_event_at or timestamp,
                    timestamp,
                    timestamp,
                ),
            )

    def save_dialogue_act(self, act: DialogueAct) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO ecobot2_dialogue_acts(
                    act_id, event_id, thread_id, actor_id, act_type, target_id,
                    confidence, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    act.act_id,
                    act.event_id,
                    act.thread_id,
                    act.actor_id,
                    act.act_type,
                    act.target_id,
                    act.confidence,
                    _json(list(act.evidence)),
                    _now(),
                ),
            )

    def save_obligation(self, obligation: SocialObligation) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO ecobot2_social_obligations(
                    obligation_id, thread_id, source_event_id, owner_id, target_id,
                    kind, strength, status, due_at, reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    obligation.obligation_id,
                    obligation.thread_id,
                    obligation.source_event_id,
                    obligation.owner_id,
                    obligation.target_id,
                    obligation.kind,
                    obligation.strength,
                    obligation.status,
                    obligation.due_at,
                    obligation.reason,
                    _now(),
                    _now(),
                ),
            )

    def threads(self, limit: int = 100, channel_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ecobot2_conversation_threads"
        values: list[Any] = []
        if channel_id:
            query += " WHERE channel_id = ?"
            values.append(channel_id)
        query += " ORDER BY updated_at DESC LIMIT ?"
        values.append(max(1, min(1000, int(limit))))
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return [dict(row) | {"participants": json.loads(row["participants_json"])} for row in rows]

    def dialogue_acts(self, limit: int = 100, event_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ecobot2_dialogue_acts"
        values: list[Any] = []
        if event_id:
            query += " WHERE event_id = ?"
            values.append(event_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        values.append(max(1, min(1000, int(limit))))
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return [dict(row) | {"evidence": json.loads(row["evidence_json"])} for row in rows]

    def obligations(self, limit: int = 100, status: str | None = "pending") -> list[dict[str, Any]]:
        query = "SELECT * FROM ecobot2_social_obligations"
        values: list[Any] = []
        if status:
            query += " WHERE status = ?"
            values.append(status)
        query += " ORDER BY strength DESC, updated_at DESC LIMIT ?"
        values.append(max(1, min(1000, int(limit))))
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return [dict(row) for row in rows]

    def save_belief(
        self,
        belief_id: str,
        *,
        subject: str,
        predicate: str,
        object_value: Any,
        confidence: float,
        source_event_id: str | None,
        status: str = "active",
    ) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO ecobot2_beliefs(
                    belief_id, subject, predicate, object_json, confidence,
                    source_event_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM ecobot2_beliefs WHERE belief_id = ?), ?), ?)
                """,
                (
                    belief_id,
                    subject,
                    predicate,
                    _json(object_value),
                    max(0.0, min(1.0, float(confidence))),
                    source_event_id,
                    status,
                    belief_id,
                    _now(),
                    _now(),
                ),
            )

    def beliefs(self, limit: int = 100, subject: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ecobot2_beliefs"
        values: list[Any] = []
        if subject:
            query += " WHERE subject = ?"
            values.append(subject)
        query += " ORDER BY updated_at DESC LIMIT ?"
        values.append(max(1, min(1000, int(limit))))
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return [dict(row) | {"object": json.loads(row["object_json"])} for row in rows]

    def save_capability(
        self,
        capability_name: str,
        *,
        description: str,
        available: bool,
        requirements: dict[str, Any] | None = None,
    ) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot2_capabilities(
                    capability_name, description, available, requirements_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(capability_name) DO UPDATE SET
                    description=excluded.description, available=excluded.available,
                    requirements_json=excluded.requirements_json, updated_at=excluded.updated_at
                """,
                (
                    capability_name,
                    description,
                    int(available),
                    _json(requirements or {}),
                    _now(),
                ),
            )

    def capabilities(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_capabilities ORDER BY capability_name LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [
            dict(row)
            | {"available": bool(row["available"]), "requirements": json.loads(row["requirements_json"])}
            for row in rows
        ]

    def observe_world_entity(
        self,
        entity_id: str,
        *,
        entity_kind: str,
        display_name: str,
        attributes: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> None:
        timestamp = _now()
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot2_world_entities(
                    entity_id, entity_kind, display_name, attributes_json,
                    first_event_id, first_seen_at, last_event_id, last_seen_at, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(entity_id) DO UPDATE SET
                    entity_kind=excluded.entity_kind, display_name=excluded.display_name,
                    attributes_json=excluded.attributes_json, last_event_id=excluded.last_event_id,
                    last_seen_at=excluded.last_seen_at, version=ecobot2_world_entities.version + 1
                """,
                (
                    entity_id,
                    entity_kind,
                    display_name,
                    _json(attributes or {}),
                    event_id,
                    timestamp,
                    event_id,
                    timestamp,
                ),
            )

    def world_entities(self, limit: int = 200, entity_kind: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ecobot2_world_entities"
        values: list[Any] = []
        if entity_kind:
            query += " WHERE entity_kind = ?"
            values.append(entity_kind)
        query += " ORDER BY last_seen_at DESC LIMIT ?"
        values.append(max(1, min(5000, int(limit))))
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return [dict(row) | {"attributes": json.loads(row["attributes_json"])} for row in rows]

    def save_scene_expansion_proposal(
        self,
        proposal_id: str,
        *,
        entity_id: str,
        requested_by_intent_id: str,
        description: str,
        evidence: list[str],
        status: str = "proposed",
    ) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO ecobot2_scene_expansion_proposals(
                    proposal_id, entity_id, requested_by_intent_id, description,
                    status, evidence_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    entity_id,
                    requested_by_intent_id,
                    description,
                    status,
                    _json(evidence),
                    _now(),
                    _now(),
                ),
            )

    def scene_expansion_proposals(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_scene_expansion_proposals ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [dict(row) | {"evidence": json.loads(row["evidence_json"])} for row in rows]

    def save_schedule_fact(
        self,
        fact_id: str,
        *,
        title: str,
        location_id: str,
        activity: str,
        start_at: str,
        end_at: str,
        actors: list[str] | tuple[str, ...] = (),
        reactions: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
        source_event_id: str | None = None,
        status: str = "active",
    ) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot2_schedule_facts(
                    fact_id, title, location_id, activity, start_at, end_at,
                    actors_json, reactions_json, source_event_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fact_id) DO UPDATE SET
                    title=excluded.title, location_id=excluded.location_id,
                    activity=excluded.activity, start_at=excluded.start_at, end_at=excluded.end_at,
                    actors_json=excluded.actors_json, reactions_json=excluded.reactions_json,
                    source_event_id=excluded.source_event_id, status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (
                    fact_id,
                    title,
                    location_id,
                    activity,
                    start_at,
                    end_at,
                    _json(list(actors)),
                    _json(list(reactions)),
                    source_event_id,
                    status,
                    _now(),
                    _now(),
                ),
            )

    def active_schedule_facts(self, at: str | None = None) -> list[dict[str, Any]]:
        timestamp = at or _now()
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT * FROM ecobot2_schedule_facts
                WHERE status = 'active' AND start_at <= ? AND end_at > ?
                ORDER BY start_at, fact_id
                """,
                (timestamp, timestamp),
            ).fetchall()
        return [
            dict(row)
            | {
                "actors": json.loads(row["actors_json"]),
                "reactions": json.loads(row["reactions_json"]),
            }
            for row in rows
        ]

    def schedule_facts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_schedule_facts ORDER BY start_at DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [
            dict(row)
            | {
                "actors": json.loads(row["actors_json"]),
                "reactions": json.loads(row["reactions_json"]),
            }
            for row in rows
        ]

    def save_world_rule(
        self,
        rule_id: str,
        *,
        name: str,
        trigger_action_type: str,
        conditions: dict[str, Any],
        reactions: list[dict[str, Any]],
        priority: float = 0.5,
        source_event_id: str | None = None,
        enabled: bool = True,
    ) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot2_world_rules(
                    rule_id, name, trigger_action_type, condition_json, reactions_json,
                    priority, source_event_id, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    name=excluded.name, trigger_action_type=excluded.trigger_action_type,
                    condition_json=excluded.condition_json, reactions_json=excluded.reactions_json,
                    priority=excluded.priority, source_event_id=excluded.source_event_id,
                    enabled=excluded.enabled, updated_at=excluded.updated_at
                """,
                (
                    rule_id,
                    name,
                    trigger_action_type,
                    _json(conditions),
                    _json(reactions),
                    max(0.0, min(1.0, float(priority))),
                    source_event_id,
                    int(enabled),
                    _now(),
                    _now(),
                ),
            )

    def world_rules(self, action_type: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ecobot2_world_rules WHERE enabled = 1"
        values: list[Any] = []
        if action_type:
            query += " AND trigger_action_type = ?"
            values.append(action_type)
        query += " ORDER BY priority DESC, updated_at DESC"
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return [
            dict(row)
            | {
                "conditions": json.loads(row["condition_json"]),
                "reactions": json.loads(row["reactions_json"]),
                "enabled": bool(row["enabled"]),
            }
            for row in rows
        ]

    def attention_decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_attention_decisions ORDER BY created_at DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [dict(row) | {"should_reply": bool(row["should_reply"])} for row in rows]

    def identity_revisions(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ecobot2_identity_revision_candidates"
        values: list[Any] = []
        if status:
            query += " WHERE status = ?"
            values.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        values.append(max(1, min(1000, int(limit))))
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return [
            dict(row)
            | {
                "proposal": json.loads(row["proposal_json"]),
                "evidence": json.loads(row["evidence_json"]),
            }
            for row in rows
        ]

    def save_appraisal(self, appraisal: Appraisal) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO ecobot2_appraisals(
                    appraisal_id, source_event_id, relevance, valence,
                    controllability, agency_confidence, boundary_violation,
                    emotion, intensity, relationship_target_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    appraisal.appraisal_id,
                    appraisal.source_event_id,
                    appraisal.relevance,
                    appraisal.valence,
                    appraisal.controllability,
                    appraisal.agency_confidence,
                    appraisal.boundary_violation,
                    appraisal.emotion,
                    appraisal.intensity,
                    appraisal.relationship_target_id,
                    appraisal.reason,
                    _now(),
                ),
            )

    def propose_identity_revision(
        self,
        category: str,
        proposal: dict[str, Any],
        evidence: list[str],
        confidence: float,
    ) -> int:
        with self._guard, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO ecobot2_identity_revision_candidates(
                    category, proposal_json, evidence_json, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (category, _json(proposal), _json(evidence), confidence, _now()),
            )
            return int(cursor.lastrowid)

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_events ORDER BY occurred_at DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    def actions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_action_attempts ORDER BY started_at DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [dict(row) | {"arguments": json.loads(row["arguments_json"])} for row in rows]

    def consequences(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_consequences ORDER BY observed_at DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    def appraisals(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_appraisals ORDER BY created_at DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._guard:
            self._connection.close()
