from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any

from .contracts import ActionIntent, ActionStatus
from .runtime import AutonomousRuntime


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class LifeTickResult:
    tick_id: str
    created_intent_id: str | None
    completed_intent_id: str | None
    resolution_status: str | None
    changed_state: bool


class AutonomousLifeLoop:
    """Advances Ecobot's private life independently from incoming messages."""

    def __init__(
        self,
        runtime: AutonomousRuntime,
        *,
        minimum_state_interval: timedelta = timedelta(minutes=1),
        intent_proposer: Callable[[dict[str, Any]], ActionIntent | None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.minimum_state_interval = minimum_state_interval
        self.intent_proposer = intent_proposer

    def tick(
        self,
        *,
        now: datetime | None = None,
        proposed_intent: ActionIntent | None = None,
    ) -> LifeTickResult:
        current_time = now or _now()
        tick_id = f"life:{current_time.timestamp()}:{uuid.uuid4().hex[:8]}"
        state = self.runtime.store.subjective_state(self.runtime.agent_id)
        if state is None:
            state = self.runtime.set_subjective_state()

        self.runtime.observe(
            "time_tick",
            event_id=tick_id,
            payload={
                "source": "autonomous_life_loop",
                "location_id": state.location_id,
                "activity": state.activity,
            },
        )

        changed_state = self._evolve_state(state, current_time)
        active = self._active_intent()
        completed_intent_id: str | None = None
        resolution_status: str | None = None

        if active is not None:
            intent = ActionIntent(
                intent_id=active["intent_id"],
                action_type=active["action_type"],
                target_id=active.get("target_id"),
                arguments=active["arguments"],
                priority=float(active["priority"]),
                reason=active["reason"],
                expires_at=active.get("expires_at"),
            )
            resolution = self.runtime.resolve_intent(intent)
            resolution_status = resolution.status
            if resolution.status == "succeeded":
                attempt = self.runtime.start_action(intent)
                self.runtime.record_receipt(
                    attempt.attempt_id,
                    ActionStatus.SUCCEEDED,
                    observed=True,
                )
                self.runtime.observe_consequence(
                    attempt.attempt_id,
                    "world_action_applied",
                    confidence=1.0,
                    payload={
                        "action_type": intent.action_type,
                        "next_location_id": resolution.next_location_id,
                    },
                )
                for reaction in resolution.reactions:
                    self.runtime.observe_consequence(
                        attempt.attempt_id,
                        str(reaction.get("kind") or "world_reaction"),
                        confidence=0.8,
                        payload=dict(reaction),
                    )
                self._apply_action_state(intent, resolution.next_location_id)
                self.runtime.review_experience(
                    category="habit",
                    proposal={
                        "action_type": intent.action_type,
                        "result": "succeeded",
                    },
                    evidence=[attempt.attempt_id],
                    confidence=0.45,
                    source_event_id=attempt.attempt_id,
                )
                completed_intent_id = intent.intent_id

        created_intent_id: str | None = None
        if active is None:
            if proposed_intent is not None:
                self.runtime.submit_intent(proposed_intent)
                created_intent_id = proposed_intent.intent_id
            else:
                created_intent_id = self._propose_intent(state)

        return LifeTickResult(
            tick_id=tick_id,
            created_intent_id=created_intent_id,
            completed_intent_id=completed_intent_id,
            resolution_status=resolution_status,
            changed_state=changed_state,
        )

    def _active_intent(self) -> dict[str, Any] | None:
        for status in ("running", "planned", "needs_preparation", "unknown"):
            items = self.runtime.store.intentions(20, status=status)
            if items:
                return items[0]
        return None

    def _evolve_state(self, state, current_time: datetime) -> bool:
        try:
            previous_time = _parse_time(state.updated_at)
        except (TypeError, ValueError):
            previous_time = current_time
        elapsed_hours = max(0.0, (current_time - previous_time).total_seconds() / 3600.0)
        if elapsed_hours * 3600 < self.minimum_state_interval.total_seconds():
            return False
        drives = dict(state.drives)
        drives["社交"] = min(1.0, float(drives.get("社交", 0.45)) + elapsed_hours * 0.03)
        drives["探索"] = min(1.0, float(drives.get("探索", 0.3)) + elapsed_hours * 0.015)
        drives["休息"] = min(1.0, float(drives.get("休息", 0.2)) + elapsed_hours * 0.02)
        energy = max(0.0, state.energy - elapsed_hours * 1.5)
        attention_load = max(0.0, state.attention_load - elapsed_hours * 4.0)
        self.runtime.set_subjective_state(
            location_id=state.location_id,
            activity=state.activity,
            focus=state.focus,
            mood=state.mood,
            drives=drives,
            energy=energy,
            attention_load=attention_load,
        )
        return True

    def _propose_intent(self, state) -> str | None:
        if self.intent_proposer is None:
            return None
        proposal = self.intent_proposer(
            {
                "state": state,
                "recent_events": self.runtime.store.events(20),
                "pending_obligations": self.runtime.store.obligations(20),
                "capabilities": self.runtime.store.capabilities(100),
            }
        )
        if proposal is None:
            return None
        self.runtime.submit_intent(proposal)
        return proposal.intent_id

    def _apply_action_state(self, intent: ActionIntent, next_location_id: str | None) -> None:
        state = self.runtime.store.subjective_state(self.runtime.agent_id)
        if state is None:
            return
        if intent.action_type == "go_to" and next_location_id:
            self.runtime.set_subjective_state(
                location_id=next_location_id,
                activity="移动中",
                focus=None,
                energy=max(0.0, state.energy - 1.0),
                attention_load=min(100.0, state.attention_load + 2.0),
            )
        elif intent.action_type == "start_activity":
            activity = str(intent.arguments.get("activity") or "活动")
            energy = min(100.0, state.energy + 8.0) if activity == "休息" else max(0.0, state.energy - 2.0)
            self.runtime.set_subjective_state(
                location_id=state.location_id,
                activity=activity,
                focus=state.focus,
                energy=energy,
                attention_load=state.attention_load,
            )
