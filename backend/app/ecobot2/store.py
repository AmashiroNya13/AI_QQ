from __future__ import annotations

import json
import hashlib
import math
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
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
from ecobot.contracts import Inference
from ecobot.affinity import (
    INITIAL_AFFINITY,
    INITIAL_TRUST,
    MINIMUM_AFFINITY_CHANGE,
    SPECIAL_LEVEL_NONE,
    SPECIAL_LEVELS,
    affinity_guidance,
    affinity_stage,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _memory_node_id(node_type: str, label: str) -> str:
    normalized = f"{node_type}:{label.strip().lower()}".encode("utf-8")
    return f"ctc:{hashlib.sha256(normalized).hexdigest()[:32]}"


def _memory_query_tokens(value: str, *, limit: int = 16) -> tuple[str, ...]:
    text = str(value or "").strip().lower()
    if not text:
        return ()
    tokens: list[str] = []
    for token in re.findall(r"[\w-]{2,}", text):
        tokens.append(token)
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", text))
    tokens.extend(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
    tokens.extend(cjk[index:index + 3] for index in range(max(0, len(cjk) - 2)))
    return tuple(dict.fromkeys(token for token in tokens if token))[:limit]


class AutonomousStore:
    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._relationship_enabled = True
        self._relationship_positive_step_limit = 20.0
        self._relationship_negative_step_limit = 30.0
        self._relationship_irritation_half_life_hours = 12.0
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
                CREATE TABLE IF NOT EXISTS ecobot2_memory_episodes (
                    episode_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL,
                    channel_id TEXT,
                    subject_id TEXT,
                    summary TEXT NOT NULL,
                    event_kind TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    importance REAL NOT NULL,
                    valence REAL NOT NULL,
                    cues_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source_batch_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_memory_episodes_time
                    ON ecobot2_memory_episodes(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_ecobot2_memory_episodes_subject
                    ON ecobot2_memory_episodes(subject_id, started_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_memory_ctc_nodes (
                    node_id TEXT PRIMARY KEY,
                    node_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    weight REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(node_type, label)
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_memory_ctc_nodes_type
                    ON ecobot2_memory_ctc_nodes(node_type, weight DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_memory_ctc_edges (
                    edge_id TEXT PRIMARY KEY,
                    from_node_id TEXT NOT NULL,
                    to_node_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    weight REAL NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(from_node_id, to_node_id, relation)
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_memory_ctc_edges_from
                    ON ecobot2_memory_ctc_edges(from_node_id, weight DESC);
                CREATE INDEX IF NOT EXISTS idx_ecobot2_memory_ctc_edges_to
                    ON ecobot2_memory_ctc_edges(to_node_id, weight DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_persona_increments (
                    increment_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    stability REAL NOT NULL,
                    status TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_persona_increments_status
                    ON ecobot2_persona_increments(status, confidence DESC, updated_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_temporal_relations (
                    relation_id TEXT PRIMARY KEY,
                    subject_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_id TEXT,
                    object_json TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    observed_at TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_event_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(subject_id, predicate, object_id, observed_at)
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_temporal_relations_subject
                    ON ecobot2_temporal_relations(subject_id, predicate, valid_to, observed_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_memory_retrievals (
                    retrieval_id TEXT PRIMARY KEY,
                    query_json TEXT NOT NULL,
                    actions_json TEXT NOT NULL,
                    node_ids_json TEXT NOT NULL,
                    episode_ids_json TEXT NOT NULL,
                    stop_reason TEXT NOT NULL,
                    max_hops INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    duration_ms INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_memory_retrievals_time
                    ON ecobot2_memory_retrievals(created_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_memory_policies (
                    policy_name TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    config_json TEXT NOT NULL,
                    score_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ecobot2_thread_transitions (
                    transition_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    from_status TEXT NOT NULL,
                    to_status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    last_event_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_thread_transitions_thread
                    ON ecobot2_thread_transitions(thread_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_grievances (
                    grievance_id TEXT PRIMARY KEY,
                    target_id TEXT,
                    source_consequence_id TEXT,
                    reason TEXT NOT NULL,
                    responsibility_confidence REAL NOT NULL,
                    intensity REAL NOT NULL,
                    repair_expected REAL NOT NULL,
                    repetition_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    first_triggered_at TEXT NOT NULL,
                    last_triggered_at TEXT NOT NULL,
                    resolved_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_ecobot2_grievances_target
                    ON ecobot2_grievances(target_id, status, last_triggered_at DESC);
                CREATE TABLE IF NOT EXISTS ecobot2_relationship_states (
                    user_id TEXT PRIMARY KEY,
                    affinity_score REAL NOT NULL,
                    trust_score REAL NOT NULL,
                    familiarity REAL NOT NULL,
                    irritation REAL NOT NULL,
                    special_level TEXT NOT NULL DEFAULT 'none',
                    special_reason TEXT,
                    special_set_at TEXT,
                    interaction_count INTEGER NOT NULL,
                    positive_interactions INTEGER NOT NULL,
                    negative_interactions INTEGER NOT NULL,
                    last_reason TEXT,
                    first_interaction_at TEXT NOT NULL,
                    last_interaction_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ecobot2_relationship_events (
                    event_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    channel_id TEXT,
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
                CREATE INDEX IF NOT EXISTS idx_ecobot2_relationship_events_user
                    ON ecobot2_relationship_events(user_id, created_at DESC);
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
            self._connection.execute(
                "INSERT OR IGNORE INTO ecobot2_schema_versions(version, applied_at) VALUES (3, ?)",
                (_now(),),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO ecobot2_schema_versions(version, applied_at) VALUES (4, ?)",
                (_now(),),
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO ecobot2_schema_versions(version, applied_at) VALUES (5, ?)",
                (_now(),),
            )
        self._migrate_legacy_relationships()

    def _migrate_legacy_relationships(self) -> None:
        with self._guard, self._connection:
            exists = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ecobot_affinity_profiles'"
            ).fetchone()
            if exists:
                rows = self._connection.execute("SELECT * FROM ecobot_affinity_profiles").fetchall()
                for row in rows:
                    values = self._legacy_relationship_values(row)
                    current = self._connection.execute(
                        "SELECT * FROM ecobot2_relationship_states WHERE user_id = ?",
                        (values[0],),
                    ).fetchone()
                    if current is None or self._relationship_state_needs_repair(current):
                        self._connection.execute(
                            """
                            INSERT INTO ecobot2_relationship_states(
                                user_id, affinity_score, trust_score, familiarity, irritation,
                                special_level, special_reason, special_set_at,
                                interaction_count, positive_interactions, negative_interactions,
                                last_reason, first_interaction_at, last_interaction_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(user_id) DO UPDATE SET
                                affinity_score=excluded.affinity_score,
                                trust_score=excluded.trust_score,
                                familiarity=excluded.familiarity,
                                irritation=excluded.irritation,
                                special_level=excluded.special_level,
                                special_reason=excluded.special_reason,
                                special_set_at=excluded.special_set_at,
                                interaction_count=excluded.interaction_count,
                                positive_interactions=excluded.positive_interactions,
                                negative_interactions=excluded.negative_interactions,
                                last_reason=excluded.last_reason,
                                first_interaction_at=excluded.first_interaction_at,
                                last_interaction_at=excluded.last_interaction_at,
                                updated_at=excluded.updated_at
                            """,
                            values,
                        )
            events = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ecobot_affinity_events'"
            ).fetchone()
            if events:
                rows = self._connection.execute("SELECT * FROM ecobot_affinity_events").fetchall()
                for row in rows:
                    self._connection.execute(
                        """
                        INSERT OR IGNORE INTO ecobot2_relationship_events(
                            event_id, user_id, channel_id, batch_id, raw_affinity_delta,
                            applied_affinity_delta, raw_trust_delta, applied_trust_delta,
                            familiarity_delta, irritation_delta, confidence, reason,
                            previous_stage, new_stage, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (f"legacy-affinity:{row[0]}", *tuple(row[1:15])),
                    )

    @staticmethod
    def _legacy_relationship_values(row: sqlite3.Row) -> tuple[Any, ...]:
        columns = set(row.keys())

        def value(name: str, default: Any = None) -> Any:
            if name not in columns or row[name] is None:
                return default
            return row[name]

        now = _now()
        return (
            value("user_id", "unknown"),
            value("affinity_score", INITIAL_AFFINITY),
            value("trust_score", INITIAL_TRUST),
            value("familiarity", 0.0),
            value("irritation", 0.0),
            value("special_level", SPECIAL_LEVEL_NONE),
            value("special_reason"),
            value("special_set_at"),
            value("interaction_count", 0),
            value("positive_interactions", 0),
            value("negative_interactions", 0),
            value("last_reason"),
            value("first_interaction_at", now),
            value("last_interaction_at", now),
            value("updated_at", now),
        )

    @staticmethod
    def _relationship_state_needs_repair(row: sqlite3.Row) -> bool:
        try:
            for field in ("affinity_score", "trust_score", "familiarity", "irritation"):
                float(row[field])
            for field in ("interaction_count", "positive_interactions", "negative_interactions"):
                int(row[field])
        except (KeyError, TypeError, ValueError):
            return True
        return str(row["special_level"] or SPECIAL_LEVEL_NONE) not in SPECIAL_LEVELS

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

    def save_memory_episode(
        self,
        *,
        source_event_id: str,
        summary: str,
        event_kind: str,
        channel_id: str | None = None,
        subject_id: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        importance: float = 0.5,
        valence: float = 0.0,
        cues: list[str] | tuple[str, ...] = (),
        tags: list[str] | tuple[str, ...] = (),
        content: dict[str, Any] | None = None,
        source_batch_id: str | None = None,
        episode_id: str | None = None,
    ) -> str:
        timestamp = started_at or _now()
        resolved_id = episode_id or f"episode:{source_event_id}"
        clean_cues = tuple(dict.fromkeys(str(item).strip() for item in cues if str(item).strip()))
        clean_tags = tuple(dict.fromkeys(str(item).strip() for item in tags if str(item).strip()))
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot2_memory_episodes(
                    episode_id, source_event_id, channel_id, subject_id, summary,
                    event_kind, started_at, ended_at, importance, valence,
                    cues_json, tags_json, content_json, status, source_batch_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                ON CONFLICT(episode_id) DO UPDATE SET
                    summary=excluded.summary, ended_at=excluded.ended_at,
                    importance=excluded.importance, valence=excluded.valence,
                    cues_json=excluded.cues_json, tags_json=excluded.tags_json,
                    content_json=excluded.content_json, source_batch_id=excluded.source_batch_id,
                    updated_at=excluded.updated_at
                """,
                (
                    resolved_id,
                    source_event_id,
                    channel_id,
                    subject_id,
                    str(summary).strip()[:2000],
                    str(event_kind),
                    timestamp,
                    ended_at,
                    max(0.0, min(1.0, float(importance))),
                    max(-1.0, min(1.0, float(valence))),
                    _json(clean_cues),
                    _json(clean_tags),
                    _json(content or {}),
                    source_batch_id,
                    timestamp,
                    _now(),
                ),
            )
            content_node = self._upsert_memory_node(
                "content",
                resolved_id,
                {"episode_id": resolved_id, "summary": str(summary).strip()[:2000]},
                weight=max(0.1, float(importance)),
            )
            phrase_nodes = [
                self._upsert_memory_node("phrase", item, {"label": item}, weight=0.4)
                for item in _memory_query_tokens(summary)
            ]
            cue_nodes = [
                self._upsert_memory_node("cue", item, {"label": item}, weight=0.5)
                for item in clean_cues
            ]
            tag_nodes = [
                self._upsert_memory_node("tag", item, {"label": item}, weight=0.5)
                for item in clean_tags
            ]
            for cue_node in cue_nodes:
                for tag_node in tag_nodes or [content_node]:
                    self._upsert_memory_edge(cue_node, tag_node, "cue_to_tag" if tag_nodes else "cue_to_content", source_event_id)
            for tag_node in tag_nodes:
                self._upsert_memory_edge(tag_node, content_node, "tag_to_content", source_event_id)
            for phrase_node in phrase_nodes:
                self._upsert_memory_edge(phrase_node, content_node, "phrase_to_content", source_event_id)
        return resolved_id

    def _upsert_memory_node(
        self,
        node_type: str,
        label: str,
        value: dict[str, Any],
        *,
        weight: float,
    ) -> str:
        node_id = _memory_node_id(node_type, label)
        timestamp = _now()
        self._connection.execute(
            """
            INSERT INTO ecobot2_memory_ctc_nodes(
                node_id, node_type, label, value_json, weight, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                value_json=excluded.value_json,
                weight=MAX(ecobot2_memory_ctc_nodes.weight, excluded.weight),
                updated_at=excluded.updated_at
            """,
            (node_id, node_type, label, _json(value), max(0.0, min(1.0, weight)), timestamp, timestamp),
        )
        return node_id

    def _upsert_memory_edge(
        self,
        from_node_id: str,
        to_node_id: str,
        relation: str,
        evidence: str,
    ) -> None:
        edge_id = _memory_node_id("edge", f"{from_node_id}:{to_node_id}:{relation}")
        timestamp = _now()
        self._connection.execute(
            """
            INSERT INTO ecobot2_memory_ctc_edges(
                edge_id, from_node_id, to_node_id, relation, weight,
                evidence_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0.5, ?, ?, ?)
            ON CONFLICT(edge_id) DO UPDATE SET
                weight=MIN(1.0, ecobot2_memory_ctc_edges.weight + 0.05),
                evidence_json=excluded.evidence_json,
                updated_at=excluded.updated_at
            """,
            (edge_id, from_node_id, to_node_id, relation, _json([evidence]), timestamp, timestamp),
        )

    def reconstruct_memory(
        self,
        *,
        query: str = "",
        cues: list[str] | tuple[str, ...] = (),
        tags: list[str] | tuple[str, ...] = (),
        limit: int = 8,
        max_hops: int = 2,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        normalized_query = str(query or "").strip().lower()
        seed_labels = tuple(dict.fromkeys(
            [str(item).strip().lower() for item in tuple(cues) + tuple(tags) if str(item).strip()]
            + list(_memory_query_tokens(normalized_query))
        ))
        with self._guard, self._connection:
            seeds: set[str] = set()
            for label in seed_labels:
                rows = self._connection.execute(
                    "SELECT node_id FROM ecobot2_memory_ctc_nodes WHERE lower(label) = ? OR lower(label) LIKE ? LIMIT 12",
                    (label, f"%{label}%"),
                ).fetchall()
                seeds.update(row["node_id"] for row in rows)
            visited = set(seeds)
            frontier = set(seeds)
            actions: list[dict[str, Any]] = [{"action": "seed", "labels": list(seed_labels), "nodes": len(seeds)}]
            for hop in range(max(0, min(4, int(max_hops)))):
                if not frontier:
                    break
                placeholders = ",".join("?" for _ in frontier)
                rows = self._connection.execute(
                    f"SELECT from_node_id, to_node_id, relation, weight FROM ecobot2_memory_ctc_edges WHERE from_node_id IN ({placeholders}) OR to_node_id IN ({placeholders}) ORDER BY weight DESC LIMIT 200",
                    tuple(frontier) + tuple(frontier),
                ).fetchall()
                next_frontier = set()
                for row in rows:
                    for node_id in (row["from_node_id"], row["to_node_id"]):
                        if node_id not in visited:
                            visited.add(node_id)
                            next_frontier.add(node_id)
                frontier = next_frontier
                actions.append({"action": "expand", "hop": hop + 1, "new_nodes": len(frontier), "edges": len(rows)})
            content_nodes = list(visited)
            episodes: list[dict[str, Any]] = []
            for node_id in content_nodes:
                row = self._connection.execute(
                    "SELECT value_json FROM ecobot2_memory_ctc_nodes WHERE node_id = ? AND node_type = 'content'",
                    (node_id,),
                ).fetchone()
                if not row:
                    continue
                episode_id = json.loads(row["value_json"]).get("episode_id")
                episode = self._connection.execute(
                    "SELECT * FROM ecobot2_memory_episodes WHERE episode_id = ?",
                    (episode_id,),
                ).fetchone()
                if episode:
                    episodes.append(dict(episode) | {
                        "cues": json.loads(episode["cues_json"]),
                        "tags": json.loads(episode["tags_json"]),
                        "content": json.loads(episode["content_json"]),
                    })
            episodes.sort(key=lambda item: (item["importance"], item["started_at"]), reverse=True)
            episodes = episodes[: max(1, min(100, int(limit)))]
            stop_reason = "evidence_satisfied" if episodes else "no_associated_memory"
            retrieval_id = f"retrieval:{uuid.uuid4().hex}"
            duration_ms = int((time.perf_counter() - started) * 1000)
            self._connection.execute(
                """
                INSERT INTO ecobot2_memory_retrievals(
                    retrieval_id, query_json, actions_json, node_ids_json,
                    episode_ids_json, stop_reason, max_hops, created_at, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    retrieval_id,
                    _json({"query": query, "cues": list(cues), "tags": list(tags)}),
                    _json(actions),
                    _json(sorted(visited)),
                    _json([item["episode_id"] for item in episodes]),
                    stop_reason,
                    max(0, min(4, int(max_hops))),
                    _now(),
                    duration_ms,
                ),
            )
            self._record_memory_policy_outcome(
                "ctc-active-reconstruction",
                success=bool(episodes),
                duration_ms=duration_ms,
                evidence_count=len(episodes),
            )
        return {"retrieval_id": retrieval_id, "episodes": episodes, "actions": actions, "stop_reason": stop_reason}

    def memory_episodes(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_memory_episodes ORDER BY started_at DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [dict(row) | {
            "cues": json.loads(row["cues_json"]),
            "tags": json.loads(row["tags_json"]),
            "content": json.loads(row["content_json"]),
        } for row in rows]

    def save_persona_increment(
        self,
        *,
        increment_id: str,
        category: str,
        statement: str,
        value: dict[str, Any] | None,
        evidence: list[str] | tuple[str, ...],
        confidence: float,
        stability: float,
        status: str = "candidate",
        auto_activate: bool = False,
        activation_confidence: float = 0.75,
        activation_evidence: int = 3,
    ) -> None:
        timestamp = _now()
        with self._guard, self._connection:
            existing = self._connection.execute(
                "SELECT evidence_json, confidence, stability, status FROM ecobot2_persona_increments WHERE increment_id = ?",
                (increment_id,),
            ).fetchone()
            previous_evidence = json.loads(existing["evidence_json"]) if existing else []
            merged_evidence = list(dict.fromkeys(
                [str(item) for item in previous_evidence if str(item).strip()]
                + [str(item) for item in evidence if str(item).strip()]
            ))[:128]
            merged_confidence = max(
                float(existing["confidence"]) if existing else 0.0,
                max(0.0, min(1.0, float(confidence))),
            )
            merged_stability = min(
                1.0,
                max(float(existing["stability"]) if existing else 0.0, float(stability))
                + (0.05 if existing and len(merged_evidence) > len(previous_evidence) else 0.0),
            )
            resolved_status = status
            if existing and existing["status"] == "active":
                resolved_status = "active"
            elif auto_activate and merged_confidence >= float(activation_confidence) and len(merged_evidence) >= max(1, int(activation_evidence)):
                resolved_status = "active"
            self._connection.execute(
                """
                INSERT INTO ecobot2_persona_increments(
                    increment_id, category, statement, value_json, evidence_json,
                    confidence, stability, status, valid_from, valid_to, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(increment_id) DO UPDATE SET
                    statement=excluded.statement, value_json=excluded.value_json,
                    evidence_json=excluded.evidence_json, confidence=excluded.confidence,
                    stability=excluded.stability, status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (
                    increment_id,
                    category,
                    statement.strip()[:2000],
                    _json(value or {}),
                    _json(merged_evidence),
                    merged_confidence,
                    merged_stability,
                    resolved_status,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )

    def persona_increments(self, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM ecobot2_persona_increments"
        values: list[Any] = []
        if status:
            query += " WHERE status = ?"
            values.append(status)
        query += " ORDER BY confidence DESC, updated_at DESC LIMIT ?"
        values.append(max(1, min(1000, int(limit))))
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return [dict(row) | {"value": json.loads(row["value_json"]), "evidence": json.loads(row["evidence_json"])} for row in rows]

    def save_temporal_relation(
        self,
        *,
        relation_id: str,
        subject_id: str,
        predicate: str,
        object_id: str | None,
        object_value: Any,
        observed_at: str,
        valid_from: str | None,
        valid_to: str | None,
        confidence: float,
        source_event_id: str | None,
        status: str = "active",
    ) -> None:
        timestamp = _now()
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO ecobot2_temporal_relations(
                    relation_id, subject_id, predicate, object_id, object_json,
                    valid_from, valid_to, observed_at, confidence, source_event_id,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM ecobot2_temporal_relations WHERE relation_id = ?), ?), ?)
                """,
                (
                    relation_id, subject_id, predicate, object_id, _json(object_value),
                    valid_from, valid_to, observed_at, max(0.0, min(1.0, float(confidence))),
                    source_event_id, status, relation_id, timestamp, timestamp,
                ),
            )

    def temporal_relations(self, subject_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM ecobot2_temporal_relations"
        values: list[Any] = []
        if subject_id:
            query += " WHERE subject_id = ?"
            values.append(subject_id)
        query += " ORDER BY observed_at DESC LIMIT ?"
        values.append(max(1, min(1000, int(limit))))
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return [dict(row) | {"object": json.loads(row["object_json"])} for row in rows]

    def close_stale_threads(self, channel_id: str, *, max_age_minutes: int = 30, reason: str = "话题自然冷却") -> int:
        now = datetime.now(timezone.utc)
        changed = 0
        with self._guard, self._connection:
            rows = self._connection.execute(
                "SELECT thread_id, last_event_at FROM ecobot2_conversation_threads WHERE channel_id = ? AND status = 'active'",
                (channel_id,),
            ).fetchall()
            for row in rows:
                try:
                    last_event = datetime.fromisoformat(row["last_event_at"])
                except (TypeError, ValueError):
                    continue
                if now - last_event <= timedelta(minutes=max_age_minutes):
                    continue
                self._connection.execute(
                    "UPDATE ecobot2_conversation_threads SET status = 'cooled', updated_at = ? WHERE thread_id = ?",
                    (_now(), row["thread_id"]),
                )
                self._connection.execute(
                    "INSERT INTO ecobot2_thread_transitions(transition_id, thread_id, from_status, to_status, reason, last_event_at, created_at) VALUES (?, ?, 'active', 'cooled', ?, ?, ?)",
                    (f"transition:{uuid.uuid4().hex}", row["thread_id"], reason, row["last_event_at"], _now()),
                )
                changed += 1
        return changed

    def memory_retrievals(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_memory_retrievals ORDER BY created_at DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [dict(row) | {
            "query": json.loads(row["query_json"]),
            "actions": json.loads(row["actions_json"]),
            "node_ids": json.loads(row["node_ids_json"]),
            "episode_ids": json.loads(row["episode_ids_json"]),
        } for row in rows]

    def save_memory_policy(
        self,
        policy_name: str,
        *,
        version: int,
        config: dict[str, Any],
        score: dict[str, Any] | None = None,
        status: str = "active",
    ) -> None:
        timestamp = _now()
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot2_memory_policies(
                    policy_name, version, config_json, score_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_name) DO UPDATE SET
                    version=excluded.version, config_json=excluded.config_json,
                    score_json=excluded.score_json, status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (policy_name, max(1, int(version)), _json(config), _json(score or {}), status, timestamp, timestamp),
            )

    def memory_policies(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                "SELECT * FROM ecobot2_memory_policies ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()
        return [dict(row) | {
            "config": json.loads(row["config_json"]),
            "score": json.loads(row["score_json"]),
        } for row in rows]

    def configure_relationship(
        self,
        *,
        enabled: bool,
        positive_step_limit: float,
        negative_step_limit: float,
        irritation_half_life_hours: float,
    ) -> None:
        self._relationship_enabled = bool(enabled)
        self._relationship_positive_step_limit = max(0.1, float(positive_step_limit))
        self._relationship_negative_step_limit = max(0.1, float(negative_step_limit))
        self._relationship_irritation_half_life_hours = max(0.1, float(irritation_half_life_hours))

    def _empty_relationship(self, user_id: str) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "affinity_score": INITIAL_AFFINITY,
            "trust_score": INITIAL_TRUST,
            "familiarity": 0.0,
            "irritation": 0.0,
            "special_level": SPECIAL_LEVEL_NONE,
            "special_locked": False,
            "special_reason": None,
            "special_set_at": None,
            "stage": "初步认识",
            "interaction_count": 0,
            "positive_interactions": 0,
            "negative_interactions": 0,
            "last_reason": None,
            "first_interaction_at": None,
            "last_interaction_at": None,
            "updated_at": None,
            "response_guidance": affinity_guidance("初步认识", INITIAL_TRUST, 0, 0),
        }

    def relationship_profile(self, user_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        with self._guard:
            row = self._connection.execute(
                "SELECT * FROM ecobot2_relationship_states WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return self._empty_relationship(user_id)
        try:
            previous = datetime.fromisoformat(row["updated_at"])
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            elapsed = max(0.0, (current - previous).total_seconds() / 3600.0)
            irritation = float(row["irritation"]) * math.pow(
                0.5, elapsed / self._relationship_irritation_half_life_hours
            )
        except (TypeError, ValueError):
            irritation = float(row["irritation"])
        special = str(row["special_level"] or SPECIAL_LEVEL_NONE)
        stage = affinity_stage(float(row["affinity_score"]), special)
        return {
            "user_id": user_id,
            "affinity_score": round(float(row["affinity_score"]), 2),
            "trust_score": round(float(row["trust_score"]), 2),
            "familiarity": round(float(row["familiarity"]), 2),
            "irritation": round(irritation, 2),
            "special_level": special,
            "special_locked": special != SPECIAL_LEVEL_NONE,
            "special_reason": row["special_reason"],
            "special_set_at": row["special_set_at"],
            "stage": stage,
            "interaction_count": int(row["interaction_count"]),
            "positive_interactions": int(row["positive_interactions"]),
            "negative_interactions": int(row["negative_interactions"]),
            "last_reason": row["last_reason"],
            "first_interaction_at": row["first_interaction_at"],
            "last_interaction_at": row["last_interaction_at"],
            "updated_at": row["updated_at"],
            "response_guidance": affinity_guidance(stage, float(row["trust_score"]), float(row["familiarity"]), irritation),
        }

    def apply_relationship_inference(
        self,
        channel_id: str,
        user_id: str,
        inference: Inference,
        *,
        batch_id: str | None = None,
        occurred_at: datetime | None = None,
        update: bool = True,
    ) -> dict[str, Any]:
        now = occurred_at or datetime.now(timezone.utc)
        now_text = now.isoformat()
        with self._guard, self._connection:
            row = self._connection.execute(
                "SELECT * FROM ecobot2_relationship_states WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            current_score = float(row["affinity_score"]) if row else INITIAL_AFFINITY
            current_trust = float(row["trust_score"]) if row else INITIAL_TRUST
            current_familiarity = float(row["familiarity"]) if row else 0.0
            current_irritation = float(row["irritation"]) if row else 0.0
            special = str(row["special_level"] or SPECIAL_LEVEL_NONE) if row else SPECIAL_LEVEL_NONE
            interaction_count = int(row["interaction_count"]) if row else 0
            positive_count = int(row["positive_interactions"]) if row else 0
            negative_count = int(row["negative_interactions"]) if row else 0
            first_at = row["first_interaction_at"] if row else now_text
            previous_at = row["updated_at"] if row else now_text
            try:
                previous = datetime.fromisoformat(previous_at)
                if previous.tzinfo is None:
                    previous = previous.replace(tzinfo=timezone.utc)
                elapsed = max(0.0, (now - previous).total_seconds() / 3600.0)
                current_irritation *= math.pow(0.5, elapsed / self._relationship_irritation_half_life_hours)
            except (TypeError, ValueError):
                pass
            confidence = max(0.0, min(1.0, float(inference.confidence)))
            raw_affinity = float(inference.relation_delta)
            delta = raw_affinity * confidence
            if delta >= 0:
                delta = min(self._relationship_positive_step_limit, delta * max(0.2, 1.0 - max(0.0, current_score) / 110.0))
            else:
                delta = max(-self._relationship_negative_step_limit, delta * max(0.35, 1.0 - max(0.0, -current_score) / 120.0))
            if abs(delta) < MINIMUM_AFFINITY_CHANGE:
                delta = MINIMUM_AFFINITY_CHANGE if raw_affinity >= 0 else -MINIMUM_AFFINITY_CHANGE
            raw_trust = float(inference.trust_delta)
            trust_delta = max(-self._relationship_negative_step_limit * 0.8, min(self._relationship_positive_step_limit * 0.7, raw_trust * confidence))
            familiarity_delta = float(inference.familiarity_delta)
            if familiarity_delta <= 0:
                familiarity_delta = 0.25 + 0.75 * confidence
            familiarity_delta = max(0.0, min(2.0, familiarity_delta))
            irritation_delta = min(15.0, abs(delta) * 2.0 + abs(min(0.0, trust_delta))) if delta < 0 or trust_delta < 0 else max(-5.0, -delta * 0.6)
            applied = 0.0 if special != SPECIAL_LEVEL_NONE or not update or not self._relationship_enabled else delta
            new_score = max(-100.0, min(100.0, current_score + applied))
            new_trust = max(-100.0, min(100.0, current_trust + trust_delta))
            new_familiarity = max(0.0, min(100.0, current_familiarity + familiarity_delta))
            new_irritation = max(0.0, min(100.0, current_irritation + irritation_delta))
            previous_stage = affinity_stage(current_score, special)
            new_stage = affinity_stage(new_score, special)
            positive_count += int(applied > 0.05)
            negative_count += int(applied < -0.05)
            reason = inference.relationship_reason.strip() or None
            self._connection.execute(
                """
                INSERT INTO ecobot2_relationship_states(
                    user_id, affinity_score, trust_score, familiarity, irritation,
                    special_level, special_reason, special_set_at, interaction_count,
                    positive_interactions, negative_interactions, last_reason,
                    first_interaction_at, last_interaction_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    affinity_score=excluded.affinity_score, trust_score=excluded.trust_score,
                    familiarity=excluded.familiarity, irritation=excluded.irritation,
                    special_level=excluded.special_level, special_reason=excluded.special_reason,
                    special_set_at=excluded.special_set_at, interaction_count=excluded.interaction_count,
                    positive_interactions=excluded.positive_interactions, negative_interactions=excluded.negative_interactions,
                    last_reason=excluded.last_reason, last_interaction_at=excluded.last_interaction_at,
                    updated_at=excluded.updated_at
                """,
                (user_id, new_score, new_trust, new_familiarity, new_irritation, special,
                 row["special_reason"] if row else None, row["special_set_at"] if row else None,
                 interaction_count + 1, positive_count, negative_count, reason, first_at, now_text, now_text),
            )
            self._connection.execute(
                """
                INSERT INTO ecobot2_relationship_events(
                    event_id, user_id, channel_id, batch_id, raw_affinity_delta,
                    applied_affinity_delta, raw_trust_delta, applied_trust_delta,
                    familiarity_delta, irritation_delta, confidence, reason,
                    previous_stage, new_stage, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"relationship:{uuid.uuid4().hex}", user_id, channel_id, batch_id, raw_affinity, applied,
                 raw_trust, trust_delta, familiarity_delta, irritation_delta, confidence, reason,
                 previous_stage, new_stage, now_text),
            )
        return self.relationship_profile(user_id, now=now)

    def relationship_profiles(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._guard:
            ids = [row[0] for row in self._connection.execute(
                "SELECT user_id FROM ecobot2_relationship_states ORDER BY last_interaction_at DESC LIMIT ?",
                (max(1, min(1000, int(limit))),),
            ).fetchall()]
        return [self.relationship_profile(str(user_id)) for user_id in ids]

    def relationship_events(self, user_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM ecobot2_relationship_events"
        values: list[Any] = []
        if user_id:
            query += " WHERE user_id = ?"
            values.append(user_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        values.append(max(1, min(1000, int(limit))))
        with self._guard:
            return [dict(row) for row in self._connection.execute(query, values).fetchall()]

    def manual_update_relationship(self, user_id: str, changes: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {"affinity_score", "trust_score", "familiarity", "special_level", "reason"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"不支持的关系字段: {', '.join(sorted(unknown))}")
        current = self.relationship_profile(user_id)
        special = str(changes.get("special_level", current["special_level"]))
        if special not in SPECIAL_LEVELS:
            raise ValueError("不支持的特殊关系等级")
        now = _now()
        values = {
            "affinity_score": max(-100.0, min(100.0, float(changes.get("affinity_score", current["affinity_score"]))),),
            "trust_score": max(-100.0, min(100.0, float(changes.get("trust_score", current["trust_score"]))),),
            "familiarity": max(0.0, min(100.0, float(changes.get("familiarity", current["familiarity"]))),),
        }
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot2_relationship_states(
                    user_id, affinity_score, trust_score, familiarity, irritation, special_level,
                    special_reason, special_set_at, interaction_count, positive_interactions,
                    negative_interactions, last_reason, first_interaction_at, last_interaction_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    affinity_score=excluded.affinity_score, trust_score=excluded.trust_score,
                    familiarity=excluded.familiarity, special_level=excluded.special_level,
                    special_reason=excluded.special_reason, special_set_at=excluded.special_set_at,
                    last_reason=excluded.last_reason, updated_at=excluded.updated_at
                """,
                (user_id, values["affinity_score"], values["trust_score"], values["familiarity"], current["irritation"], special,
                 str(changes.get("reason") or "管理员手动调整"), now, current["interaction_count"], current["positive_interactions"],
                 current["negative_interactions"], str(changes.get("reason") or "管理员手动调整"), current["first_interaction_at"] or now, now, now),
            )
        return self.relationship_profile(user_id)

    def _record_memory_policy_outcome(
        self,
        policy_name: str,
        *,
        success: bool,
        duration_ms: int,
        evidence_count: int,
    ) -> None:
        row = self._connection.execute(
            "SELECT version, config_json, score_json FROM ecobot2_memory_policies WHERE policy_name = ?",
            (policy_name,),
        ).fetchone()
        if row is None:
            return
        score = json.loads(row["score_json"])
        attempts = int(score.get("attempts", 0)) + 1
        successes = int(score.get("successes", 0)) + int(success)
        total_duration = int(score.get("total_duration_ms", 0)) + max(0, int(duration_ms))
        zero_hits = int(score.get("zero_hits", 0)) + int(not success)
        score.update(
            {
                "attempts": attempts,
                "successes": successes,
                "zero_hits": zero_hits,
                "success_rate": round(successes / attempts, 4),
                "avg_duration_ms": round(total_duration / attempts, 2),
                "last_evidence_count": int(evidence_count),
                "last_evaluated_at": _now(),
            }
        )
        self._connection.execute(
            "UPDATE ecobot2_memory_policies SET score_json = ?, updated_at = ? WHERE policy_name = ?",
            (_json(score), _now(), policy_name),
        )
        if attempts < 5 or successes > 0:
            return
        candidate_name = f"{policy_name}:v{int(row['version']) + 1}"
        candidate_config = json.loads(row["config_json"])
        candidate_config.update(
            {
                "max_hops": min(4, int(candidate_config.get("max_hops", 2)) + 1),
                "max_episodes": min(20, int(candidate_config.get("max_episodes", 8)) + 4),
                "selection": "active-expansion-with-wider-evidence",
            }
        )
        self._connection.execute(
            """
            INSERT OR IGNORE INTO ecobot2_memory_policies(
                policy_name, version, config_json, score_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'candidate', ?, ?)
            """,
            (candidate_name, int(row["version"]) + 1, _json(candidate_config), _json({"status": "待影子评估", "parent": policy_name}), _now(), _now()),
        )

    def save_grievance(
        self,
        *,
        grievance_id: str,
        target_id: str | None,
        source_consequence_id: str | None,
        reason: str,
        responsibility_confidence: float,
        intensity: float,
        repair_expected: float,
        evidence: list[str] | tuple[str, ...] = (),
        status: str = "active",
    ) -> None:
        timestamp = _now()
        with self._guard, self._connection:
            existing = self._connection.execute(
                "SELECT * FROM ecobot2_grievances WHERE grievance_id = ?",
                (grievance_id,),
            ).fetchone()
            old_evidence = json.loads(existing["evidence_json"]) if existing else []
            merged_evidence = list(dict.fromkeys(old_evidence + [str(item) for item in evidence if str(item).strip()]))[:128]
            self._connection.execute(
                """
                INSERT INTO ecobot2_grievances(
                    grievance_id, target_id, source_consequence_id, reason,
                    responsibility_confidence, intensity, repair_expected,
                    repetition_count, status, evidence_json, first_triggered_at,
                    last_triggered_at, resolved_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(grievance_id) DO UPDATE SET
                    target_id=excluded.target_id,
                    source_consequence_id=excluded.source_consequence_id,
                    reason=excluded.reason,
                    responsibility_confidence=MAX(ecobot2_grievances.responsibility_confidence, excluded.responsibility_confidence),
                    intensity=MIN(1.0, ecobot2_grievances.intensity * 0.8 + excluded.intensity * 0.2 + 0.1),
                    repair_expected=MAX(ecobot2_grievances.repair_expected, excluded.repair_expected),
                    repetition_count=ecobot2_grievances.repetition_count + 1,
                    status=excluded.status,
                    evidence_json=excluded.evidence_json,
                    last_triggered_at=excluded.last_triggered_at,
                    resolved_at=NULL,
                    updated_at=excluded.updated_at
                """,
                (
                    grievance_id,
                    target_id,
                    source_consequence_id,
                    reason.strip()[:1000],
                    max(0.0, min(1.0, float(responsibility_confidence))),
                    max(0.0, min(1.0, float(intensity))),
                    max(0.0, min(1.0, float(repair_expected))),
                    int(existing["repetition_count"]) + 1 if existing else 1,
                    status,
                    _json(merged_evidence),
                    existing["first_triggered_at"] if existing else timestamp,
                    timestamp,
                    existing["created_at"] if existing else timestamp,
                    timestamp,
                ),
            )

    def grievances(self, target_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT * FROM ecobot2_grievances"
        values: list[Any] = []
        if target_id:
            query += " WHERE target_id = ?"
            values.append(target_id)
        query += " ORDER BY intensity DESC, last_triggered_at DESC LIMIT ?"
        values.append(max(1, min(1000, int(limit))))
        with self._guard:
            rows = self._connection.execute(query, values).fetchall()
        return [dict(row) | {"evidence": json.loads(row["evidence_json"])} for row in rows]

    def repair_grievances(self, target_id: str, *, amount: float = 0.25, evidence: str | None = None) -> int:
        with self._guard, self._connection:
            if evidence:
                rows = self._connection.execute(
                    "SELECT grievance_id, evidence_json FROM ecobot2_grievances WHERE target_id = ? AND status = 'active'",
                    (target_id,),
                ).fetchall()
                for row in rows:
                    values = list(dict.fromkeys(json.loads(row["evidence_json"]) + [evidence]))[-128:]
                    self._connection.execute(
                        "UPDATE ecobot2_grievances SET evidence_json = ?, updated_at = ? WHERE grievance_id = ?",
                        (_json(values), _now(), row["grievance_id"]),
                    )
            cursor = self._connection.execute(
                """
                UPDATE ecobot2_grievances
                SET intensity = MAX(0.0, intensity - ?),
                    repair_expected = MAX(0.0, repair_expected - ?),
                    status = CASE WHEN intensity - ? <= 0.1 THEN 'repaired' ELSE status END,
                    resolved_at = CASE WHEN intensity - ? <= 0.1 THEN ? ELSE resolved_at END,
                    updated_at = ?
                WHERE target_id = ? AND status = 'active'
                """,
                (max(0.0, float(amount)), max(0.0, float(amount) * 0.5), max(0.0, float(amount)), max(0.0, float(amount)), _now(), _now(), target_id),
            )
            return cursor.rowcount

    def close(self) -> None:
        with self._guard:
            self._connection.close()
