from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


class BatchState(StrEnum):
    WAITING = "waiting"
    OBSERVING = "observing"
    INFERRING = "inferring"
    DESIRING = "desiring"
    PLANNING = "planning"
    ACTING = "acting"
    REFLECTING = "reflecting"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class Stimulus:
    event_id: str
    channel_id: str
    user_id: str
    content: str
    timestamp: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("event_id", "channel_id", "user_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class Observation:
    summary: str
    facts: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Inference:
    intent: str
    emotion: str = "neutral"
    relation_delta: float = 0.0
    confidence: float = 0.0
    trust_delta: float = 0.0
    familiarity_delta: float = 0.0
    relationship_reason: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Desire:
    score: float
    should_engage: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionSpec:
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    risk: str = "low"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("action name must not be empty")
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))


@dataclass(frozen=True, slots=True)
class Plan:
    actions: tuple[ActionSpec, ...] = ()
    expression: str | None = None
    request_heartbeat: bool = False
    state_update: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.state_update is not None:
            object.__setattr__(self, "state_update", _freeze_mapping(self.state_update))


@dataclass(frozen=True, slots=True)
class ActionFeedback:
    action: ActionSpec
    success: bool
    output: Any = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Reflection:
    satisfied: bool
    reason: str
    revised_plan: Plan | None = None

    def __post_init__(self) -> None:
        if self.satisfied and self.revised_plan is not None:
            raise ValueError("a satisfied reflection cannot include a revised plan")


@dataclass(frozen=True, slots=True)
class BehaviorResult:
    batch_id: str
    state: BatchState
    expression: str | None
    desire: Desire
    feedback: tuple[ActionFeedback, ...]
    state_history: tuple[BatchState, ...]
    stop_reason: str | None = None


class BehaviorBatchError(RuntimeError):
    def __init__(
        self,
        batch_id: str,
        state_history: tuple[BatchState, ...],
        cause: Exception,
    ) -> None:
        super().__init__(f"behavior batch {batch_id} failed: {cause}")
        self.batch_id = batch_id
        self.state_history = state_history
        self.cause = cause
