from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    AttentionDecision,
    ActionAttempt,
    ActionIntent,
    ActionReceipt,
    ActionStatus,
    AgentEvent,
    Appraisal,
    EventKind,
    ObservedConsequence,
    PersistentIntent,
    SubjectiveState,
)
from .store import AutonomousStore
from .scene import SceneState, WorldActionResolver, WorldResolution
from .conversation import (
    ConversationThread,
    DialogueAct,
    SocialObligation,
    classify_dialogue_act,
    infer_addressing,
    new_thread_id,
    normalize_topic,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutonomousRuntime:
    """A persistent subject-environment loop independent from AstrBot messages."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        agent_id: str = "ecobot",
    ) -> None:
        self.agent_id = agent_id
        self.store = AutonomousStore(database_path)
        self.world = WorldActionResolver()
        self._ensure_default_world()
        self._ensure_default_capabilities()

    def _ensure_default_world(self) -> None:
        if self.store.locations(1):
            return
        for location_id, item in self.world.locations().items():
            self.store.save_location(
                location_id,
                name=item["name"],
                description=item.get("description", ""),
                neighbors=tuple(sorted(item.get("neighbors", set()))),
                objects=item.get("objects", {}),
                availability=item.get("availability", {}),
            )

    def _ensure_default_capabilities(self) -> None:
        if self.store.capabilities(1):
            return
        self.store.save_capability(
            "go_to",
            description="在生活世界的地点之间移动",
            available=True,
            requirements={"requires_location_graph": True},
        )
        self.store.save_capability(
            "start_activity",
            description="在当前地点开始一项活动",
            available=True,
            requirements={"requires_activity": True},
        )
        self.store.save_capability(
            "send_expression",
            description="通过平台发送对外表达",
            available=True,
            requirements={"requires_platform_route": True},
        )

    def register_location(
        self,
        location_id: str,
        *,
        name: str,
        neighbors: tuple[str, ...] = (),
        description: str = "",
        objects: dict[str, Any] | None = None,
    ) -> None:
        self.world.register_location(location_id, name=name, neighbors=neighbors)
        self.store.save_location(
            location_id,
            name=name,
            description=description,
            neighbors=list(neighbors),
            objects=objects or {},
        )
        self.store.observe_world_entity(
            f"location:{location_id}",
            entity_kind="location",
            display_name=name,
            attributes={"description": description, "neighbors": list(neighbors)},
        )

    def register_schedule_fact(
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
    ) -> None:
        self.store.save_schedule_fact(
            fact_id,
            title=title,
            location_id=location_id,
            activity=activity,
            start_at=start_at,
            end_at=end_at,
            actors=actors,
            reactions=reactions,
            source_event_id=source_event_id,
        )

    def register_world_rule(
        self,
        rule_id: str,
        *,
        name: str,
        trigger_action_type: str,
        conditions: dict[str, Any],
        reactions: list[dict[str, Any]],
        priority: float = 0.5,
        source_event_id: str | None = None,
    ) -> None:
        self.store.save_world_rule(
            rule_id,
            name=name,
            trigger_action_type=trigger_action_type,
            conditions=conditions,
            reactions=reactions,
            priority=priority,
            source_event_id=source_event_id,
        )

    def observe(
        self,
        kind: str,
        *,
        event_id: str | None = None,
        channel_id: str | None = None,
        actor_id: str | None = None,
        target_id: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> AgentEvent:
        event = AgentEvent(
            event_id=event_id or uuid.uuid4().hex,
            kind=kind,
            occurred_at=_now(),
            channel_id=channel_id,
            actor_id=actor_id,
            target_id=target_id,
            payload=dict(payload or {}),
        )
        self.store.record_event(event)
        return event

    def observe_message(
        self,
        *,
        event_id: str,
        channel_id: str,
        actor_id: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> AgentEvent:
        event = self.observe(
            EventKind.MESSAGE,
            event_id=event_id,
            channel_id=channel_id,
            actor_id=actor_id,
            payload={"content": content, "metadata": dict(metadata or {})},
        )
        self._ingest_conversation(event.event_id, channel_id, actor_id, content, metadata or {})
        return event

    def _ingest_conversation(
        self,
        event_id: str,
        channel_id: str,
        actor_id: str,
        content: str,
        metadata: Mapping[str, Any],
    ) -> None:
        topic = normalize_topic(content)
        self.store.observe_world_entity(
            f"person:{actor_id}",
            entity_kind="person",
            display_name=actor_id,
            attributes={"source": "message", "channel_id": channel_id},
            event_id=event_id,
        )
        self.store.observe_world_entity(
            f"channel:{channel_id}",
            entity_kind="communication_place",
            display_name=channel_id,
            attributes={"last_actor_id": actor_id},
            event_id=event_id,
        )
        existing = next(
            (item for item in self.store.threads(50, channel_id) if item["topic"] == topic and item["status"] == "active"),
            None,
        )
        thread_id = existing["thread_id"] if existing else new_thread_id(channel_id, topic)
        participants = set(existing.get("participants", [])) if existing else set()
        participants.add(actor_id)
        address = infer_addressing(
            event_id,
            actor_id=actor_id,
            content=content,
            metadata=metadata,
            agent_id=self.agent_id,
        )
        if address.selected_target_id:
            participants.add(address.selected_target_id)
        thread = ConversationThread(
            thread_id=thread_id,
            channel_id=channel_id,
            topic=topic,
            participants=tuple(sorted(participants)),
            addressee_id=address.selected_target_id,
            salience=max(0.0, min(1.0, 1.0 - address.confidence * 0.35)),
            version=int(existing["version"] + 1) if existing else 1,
            last_event_at=_now(),
        )
        self.store.save_thread(thread)
        act_type = classify_dialogue_act(content)
        self.store.save_dialogue_act(
            DialogueAct(
                act_id=uuid.uuid4().hex,
                event_id=event_id,
                thread_id=thread_id,
                actor_id=actor_id,
                act_type=act_type,
                target_id=address.selected_target_id,
                confidence=address.confidence,
                evidence=address.evidence,
            )
        )
        self.store.save_belief(
            f"message:{event_id}",
            subject=actor_id,
            predicate="said",
            object_value={"content": content, "channel_id": channel_id},
            confidence=1.0,
            source_event_id=event_id,
        )
        if address.selected_target_id == self.agent_id and act_type.value in {"question", "request"}:
            self.store.save_obligation(
                SocialObligation(
                    obligation_id=f"obligation:{event_id}",
                    thread_id=thread_id,
                    source_event_id=event_id,
                    owner_id=self.agent_id,
                    target_id=actor_id,
                    kind="reply",
                    strength=0.85,
                    reason="对方明确向 Ecobot 提问或提出请求",
                )
            )

    def set_subjective_state(
        self,
        *,
        location_id: str | None = None,
        activity: str | None = None,
        focus: str | None = None,
        mood: Mapping[str, float] | None = None,
        drives: Mapping[str, float] | None = None,
        energy: float | None = None,
        attention_load: float | None = None,
    ) -> SubjectiveState:
        previous = self.store.subjective_state(self.agent_id)
        defaults = {
            "location_id": "home",
            "activity": "idle",
            "focus": None,
            "mood": {"平静": 0.7},
            "drives": {"社交": 0.45, "探索": 0.3, "休息": 0.2},
            "energy": 70.0,
            "attention_load": 0.0,
        }
        state = SubjectiveState(
            agent_id=self.agent_id,
            location_id=location_id or (previous.location_id if previous else defaults["location_id"]),
            activity=activity or (previous.activity if previous else defaults["activity"]),
            focus=focus if focus is not None else (previous.focus if previous else defaults["focus"]),
            mood=dict(mood if mood is not None else (previous.mood if previous else defaults["mood"])),
            drives=dict(drives if drives is not None else (previous.drives if previous else defaults["drives"])),
            energy=max(0.0, min(100.0, energy if energy is not None else (previous.energy if previous else defaults["energy"]))),
            attention_load=max(0.0, min(100.0, attention_load if attention_load is not None else (previous.attention_load if previous else defaults["attention_load"]))),
            version=(previous.version + 1 if previous else 1),
            updated_at=_now(),
        )
        self.store.save_subjective_state(state)
        previous_scene = self.store.scene("main")
        scene = SceneState(
            scene_id="main",
            location_id=state.location_id,
            local_time=state.updated_at,
            activity=state.activity,
            occupants=tuple(previous_scene.occupants if previous_scene else ()),
            objects=dict(previous_scene.objects if previous_scene else {}),
            version=(previous_scene.version + 1 if previous_scene else 1),
        )
        self.store.save_scene(scene, updated_at=state.updated_at)
        self.observe(
            "scene_changed",
            event_id=f"scene:{self.agent_id}:{state.version}",
            payload={
                "scene_id": scene.scene_id,
                "location_id": scene.location_id,
                "activity": scene.activity,
                "version": scene.version,
            },
        )
        return state

    def submit_intent(self, intent: ActionIntent, *, status: str = "planned") -> PersistentIntent:
        timestamp = _now()
        persistent = PersistentIntent(
            intent_id=intent.intent_id,
            action_type=intent.action_type,
            target_id=intent.target_id,
            arguments=dict(intent.arguments),
            priority=max(0.0, min(1.0, float(intent.priority))),
            reason=intent.reason,
            status=status,
            expires_at=intent.expires_at,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.store.save_intent(persistent)
        self.observe(
            "intent_created",
            event_id=f"intent:{intent.intent_id}",
            target_id=intent.target_id,
            payload={
                "action_type": intent.action_type,
                "priority": persistent.priority,
                "reason": intent.reason,
            },
        )
        return persistent

    def start_action(self, intent: ActionIntent) -> ActionAttempt:
        if not self.store.intent_exists(intent.intent_id):
            self.submit_intent(intent)
        self.store.update_intent(intent.intent_id, status=ActionStatus.RUNNING)
        attempt = ActionAttempt(
            attempt_id=uuid.uuid4().hex,
            intent_id=intent.intent_id,
            action_type=intent.action_type,
            status=ActionStatus.RUNNING,
            started_at=_now(),
            arguments=dict(intent.arguments),
        )
        self.store.save_action_attempt(attempt)
        self.observe(
            "action_attempt",
            event_id=attempt.attempt_id,
            target_id=intent.target_id,
            payload={"intent_id": intent.intent_id, "action_type": intent.action_type},
        )
        return attempt

    def resolve_intent(
        self,
        intent: ActionIntent,
        *,
        current_location_id: str | None = None,
    ) -> WorldResolution:
        if not self.store.intent_exists(intent.intent_id):
            self.submit_intent(intent)
        state = self.store.subjective_state(self.agent_id)
        resolution = self.world.resolve(
            intent.intent_id,
            intent.action_type,
            intent.arguments,
            current_location_id=current_location_id or (state.location_id if state else "home"),
            current_activity=state.activity if state else None,
            world_rules=tuple(self.store.world_rules(intent.action_type)),
            active_schedule_facts=tuple(self.store.active_schedule_facts()),
        )
        if intent.action_type == "go_to":
            target_location = str(intent.arguments.get("location_id") or "").strip()
            if target_location and not self.world.locations().get(target_location):
                self.store.observe_world_entity(
                    f"location:{target_location}",
                    entity_kind="location",
                    display_name=target_location,
                    attributes={"status": "proposed"},
                    event_id=f"intent:{intent.intent_id}",
                )
                self.store.save_scene_expansion_proposal(
                    f"scene-proposal:{intent.intent_id}",
                    entity_id=f"location:{target_location}",
                    requested_by_intent_id=intent.intent_id,
                    description="主体提出了当前世界尚未存在的地点",
                    evidence=[intent.intent_id],
                )
        self.observe(
            "world_resolution",
            event_id=f"resolution:{intent.intent_id}",
            payload={
                "intent_id": intent.intent_id,
                "status": resolution.status,
                "reason": resolution.reason,
                "next_location_id": resolution.next_location_id,
                "required_preparations": resolution.required_preparations,
                "reactions": resolution.reactions,
            },
        )
        self.store.update_intent(
            intent.intent_id,
            status=resolution.status,
            resolution={
                "status": resolution.status,
                "reason": resolution.reason,
                "estimated_duration_seconds": resolution.estimated_duration_seconds,
                "next_location_id": resolution.next_location_id,
                "required_preparations": list(resolution.required_preparations),
                "reactions": list(resolution.reactions),
            },
        )
        return resolution

    def record_receipt(
        self,
        attempt_id: str,
        status: ActionStatus,
        *,
        error_code: str | None = None,
        error_detail: str | None = None,
        platform_message_id: str | None = None,
        observed: bool = False,
    ) -> ActionReceipt:
        receipt = ActionReceipt(
            attempt_id=attempt_id,
            status=status,
            completed_at=_now(),
            error_code=error_code,
            error_detail=error_detail,
            platform_message_id=platform_message_id,
            observed=observed,
        )
        if not self.store.save_action_receipt(receipt):
            raise ValueError(f"unknown action attempt: {attempt_id}")
        intent_id = self._intent_id_for_attempt(attempt_id)
        if intent_id:
            self.store.update_intent(intent_id, status=status)
        self.observe(
            EventKind.ACTION_RECEIPT,
            event_id=f"receipt:{attempt_id}",
            payload={
                "attempt_id": attempt_id,
                "status": status,
                "error_code": error_code,
                "error_detail": error_detail,
            },
        )
        return receipt

    def _intent_id_for_attempt(self, attempt_id: str) -> str | None:
        action = self.store.action_attempt(attempt_id)
        return str(action["intent_id"]) if action else None

    def observe_consequence(
        self,
        attempt_id: str,
        kind: str,
        *,
        actor_id: str | None = None,
        confidence: float = 0.0,
        payload: Mapping[str, Any] | None = None,
    ) -> ObservedConsequence:
        consequence = ObservedConsequence(
            consequence_id=uuid.uuid4().hex,
            attempt_id=attempt_id,
            kind=kind,
            observed_at=_now(),
            actor_id=actor_id,
            confidence=max(0.0, min(1.0, confidence)),
            payload=dict(payload or {}),
        )
        self.store.save_consequence(consequence)
        self.observe(
            EventKind.CONSEQUENCE,
            event_id=consequence.consequence_id,
            actor_id=actor_id,
            payload={"attempt_id": attempt_id, "kind": kind, **dict(payload or {})},
        )
        return consequence

    def record_attention(
        self,
        event_id: str,
        *,
        channel_id: str,
        thread_id: str | None,
        addressee_id: str | None,
        should_reply: bool,
        confidence: float,
        attention_cost: float,
        reason: str,
    ) -> AttentionDecision:
        if thread_id is None:
            acts = self.store.dialogue_acts(1, event_id=event_id)
            thread_id = acts[0]["thread_id"] if acts else None
        decision = AttentionDecision(
            decision_id=uuid.uuid4().hex,
            event_id=event_id,
            channel_id=channel_id,
            thread_id=thread_id,
            addressee_id=addressee_id,
            should_reply=bool(should_reply),
            confidence=max(0.0, min(1.0, float(confidence))),
            attention_cost=max(0.0, min(1.0, float(attention_cost))),
            reason=reason,
            created_at=_now(),
        )
        self.store.save_attention_decision(decision)
        self.observe(
            "attention_decision",
            event_id=decision.decision_id,
            channel_id=channel_id,
            target_id=addressee_id,
            payload={
                "source_event_id": event_id,
                "thread_id": thread_id,
                "should_reply": decision.should_reply,
                "confidence": decision.confidence,
                "reason": reason,
            },
        )
        return decision

    def review_experience(
        self,
        *,
        category: str,
        proposal: Mapping[str, Any],
        evidence: list[str],
        confidence: float,
        source_event_id: str | None = None,
    ) -> int:
        revision_id = self.store.propose_identity_revision(
            category,
            dict(proposal),
            list(evidence),
            max(0.0, min(1.0, float(confidence))),
        )
        self.observe(
            "experience_review",
            event_id=f"review:{revision_id}",
            payload={
                "revision_id": revision_id,
                "category": category,
                "proposal": dict(proposal),
                "evidence": list(evidence),
                "confidence": confidence,
                "source_event_id": source_event_id,
            },
        )
        return revision_id

    def appraise(
        self,
        source_event_id: str,
        *,
        relevance: float,
        valence: float,
        controllability: float,
        agency_confidence: float,
        boundary_violation: float,
        emotion: str,
        intensity: float,
        relationship_target_id: str | None = None,
        reason: str = "",
    ) -> Appraisal:
        appraisal = Appraisal(
            appraisal_id=uuid.uuid4().hex,
            source_event_id=source_event_id,
            relevance=max(0.0, min(1.0, relevance)),
            valence=max(-1.0, min(1.0, valence)),
            controllability=max(0.0, min(1.0, controllability)),
            agency_confidence=max(0.0, min(1.0, agency_confidence)),
            boundary_violation=max(0.0, min(1.0, boundary_violation)),
            emotion=emotion,
            intensity=max(0.0, min(1.0, intensity)),
            relationship_target_id=relationship_target_id,
            reason=reason,
        )
        self.store.save_appraisal(appraisal)
        self.observe(
            EventKind.APPRAISAL,
            event_id=appraisal.appraisal_id,
            target_id=relationship_target_id,
            payload={"emotion": emotion, "intensity": intensity, "reason": reason},
        )
        return appraisal

    def close(self) -> None:
        self.store.close()
