from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping


class BatchState(StrEnum):
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"


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
class Inference:
    intent: str
    emotion: str = "neutral"
    relation_delta: float = 0.0
    confidence: float = 0.0
    trust_delta: float = 0.0
    familiarity_delta: float = 0.0
    relationship_reason: str = ""
    persona_increment: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Desire:
    score: float
    should_engage: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BehaviorResult:
    batch_id: str
    state: BatchState
    expression: str | None
    desire: Desire
    feedback: tuple[Any, ...] = ()
    state_history: tuple[BatchState, ...] = ()
    stop_reason: str | None = None
    persona_increment: Mapping[str, Any] | None = None
