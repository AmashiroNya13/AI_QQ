from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class EventKind(StrEnum):
    MESSAGE = "message"
    TIME_TICK = "time_tick"
    SCENE_CHANGED = "scene_changed"
    INTENT_CREATED = "intent_created"
    WORLD_RESOLUTION = "world_resolution"
    ACTION_RECEIPT = "action_receipt"
    CONSEQUENCE = "consequence"
    APPRAISAL = "appraisal"
    ATTENTION_DECISION = "attention_decision"
    EXPERIENCE_REVIEW = "experience_review"
    IDENTITY_REVISION = "identity_revision"


class ActionStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_id: str
    kind: str
    occurred_at: str
    channel_id: str | None = None
    actor_id: str | None = None
    target_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SubjectiveState:
    agent_id: str
    location_id: str
    activity: str
    focus: str | None
    mood: Mapping[str, float]
    drives: Mapping[str, float]
    energy: float
    attention_load: float
    version: int
    updated_at: str


@dataclass(frozen=True, slots=True)
class ActionIntent:
    intent_id: str
    action_type: str
    target_id: str | None
    arguments: Mapping[str, Any] = field(default_factory=dict)
    priority: float = 0.5
    reason: str = ""
    expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class PersistentIntent:
    intent_id: str
    action_type: str
    target_id: str | None
    arguments: Mapping[str, Any]
    priority: float
    reason: str
    status: str
    expires_at: str | None
    created_at: str
    updated_at: str
    resolution: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionAttempt:
    attempt_id: str
    intent_id: str
    action_type: str
    status: ActionStatus
    started_at: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionReceipt:
    attempt_id: str
    status: ActionStatus
    completed_at: str
    error_code: str | None = None
    error_detail: str | None = None
    platform_message_id: str | None = None
    observed: bool = False


@dataclass(frozen=True, slots=True)
class ObservedConsequence:
    consequence_id: str
    attempt_id: str
    kind: str
    observed_at: str
    actor_id: str | None = None
    confidence: float = 0.0
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Appraisal:
    appraisal_id: str
    source_event_id: str
    relevance: float
    valence: float
    controllability: float
    agency_confidence: float
    boundary_violation: float
    emotion: str
    intensity: float
    relationship_target_id: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AttentionDecision:
    decision_id: str
    event_id: str
    channel_id: str
    thread_id: str | None
    addressee_id: str | None
    should_reply: bool
    confidence: float
    attention_cost: float
    reason: str
    created_at: str
