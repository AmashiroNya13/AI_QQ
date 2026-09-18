from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from astrbot import logger
from astrbot.core.message.components import At, AtAll, Image, Record, Reply
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from ecobot2.contracts import ActionIntent, ActionStatus
from ecobot2.runtime import AutonomousRuntime

from .admin_store import DEFAULT_SETTINGS, EcobotAdminStore
from .anti_repeat import AntiRepeatGuard
from .contracts import BatchState, BehaviorResult, Desire, Inference, Stimulus
from .qq_archive import QQArchive


PASSIVE_EVENT_EXTRA = "_ecobot_passive_observation"

STOP_REASON_LABELS = {
    None: "正常完成",
    "subject_decided_to_wait": "主体决定保持沉默",
    "duplicate_expression": "表达与近期内容或范式重复",
    "private_cognition_blocked": "检测到内部思考，已阻止对外发送",
}


def _debug_value(value: Any, *, limit: int | None = None) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    if limit is not None and len(text) > limit:
        return text[:limit] + f"……（已省略 {len(text) - limit} 个字符）"
    return text


def is_enabled() -> bool:
    return os.environ.get("ECOBOT_ENABLED", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def is_behavioral_message_event(event: AstrMessageEvent) -> bool:
    message_obj = getattr(event, "message_obj", None)
    raw_message = getattr(message_obj, "raw_message", None)
    if not isinstance(raw_message, Mapping):
        return True
    return raw_message.get("post_type") in (None, "message")


def event_to_stimulus(event: AstrMessageEvent) -> Stimulus:
    message_obj = event.message_obj
    event_id = str(getattr(message_obj, "message_id", "") or event.trace.span_id)
    explicit_wake = getattr(event, "is_at_or_wake_command", None)
    if explicit_wake is None:
        explicit_wake = event.is_wake_up()
    metadata = {
        "platform": event.get_platform_name(),
        "platform_id": event.get_platform_id(),
        "message_type": str(event.get_message_type()),
        "sender_name": event.get_sender_name(),
        "group_id": event.get_group_id(),
        "is_private": event.is_private_chat(),
        "is_wake": bool(explicit_wake),
        "is_admin": event.is_admin(),
        "has_at": any(isinstance(item, (At, AtAll)) for item in event.get_messages()),
        "has_reply": any(isinstance(item, Reply) for item in event.get_messages()),
        "has_image": any(isinstance(item, Image) for item in event.get_messages()),
        "has_record": any(isinstance(item, Record) for item in event.get_messages()),
    }
    content = event.get_message_outline() or event.get_message_str() or "[empty event]"
    return Stimulus(
        event_id=event_id,
        channel_id=event.unified_msg_origin,
        user_id=event.get_sender_id() or "unknown",
        content=content,
        timestamp=event.created_at,
        metadata=metadata,
    )


class AstrBotBehaviorBridge:
    """The single adapter between platform stimuli and the Ecobot subject."""

    def __init__(
        self,
        plugin_context,
        qq_archive: QQArchive | None = None,
        autonomous_runtime: AutonomousRuntime | None = None,
        anti_repeat: AntiRepeatGuard | None = None,
        admin_store: EcobotAdminStore | None = None,
        agent_id: str = "ecobot",
    ) -> None:
        self.plugin_context = plugin_context
        self.qq_archive = qq_archive
        self.autonomous_runtime = autonomous_runtime
        self.anti_repeat = anti_repeat
        self.admin_store = admin_store
        self.agent_id = agent_id
        self.settings = dict(DEFAULT_SETTINGS)
        self._expression_reservations: dict[str, int] = {}
        self._autonomous_action_attempts: dict[str, str] = {}

    def apply_settings(self, settings: dict[str, Any]) -> None:
        self.settings = {**DEFAULT_SETTINGS, **settings}
        if self.autonomous_runtime is not None:
            self.autonomous_runtime.store.configure_relationship(
                enabled=bool(self.settings["affinity_enabled"]),
                positive_step_limit=float(self.settings["affinity_positive_step_limit"]),
                negative_step_limit=float(self.settings["affinity_negative_step_limit"]),
                irritation_half_life_hours=float(self.settings["affinity_irritation_half_life_hours"]),
            )
        if self.anti_repeat is not None:
            from datetime import timedelta

            self.anti_repeat.window = timedelta(minutes=int(self.settings["anti_repeat_window_minutes"]))
            self.anti_repeat.fuzzy_threshold = float(self.settings["anti_repeat_fuzzy_threshold"])
            self.anti_repeat.semantic_threshold = float(self.settings["anti_repeat_semantic_threshold"])
            self.anti_repeat.style_window = timedelta(minutes=int(self.settings["reply_style_window_minutes"]))
            self.anti_repeat.style_repeat_limit = int(self.settings["reply_style_repeat_limit"])

    def archive_event(self, event: AstrMessageEvent) -> bool:
        return self.qq_archive.record_event(event) if self.qq_archive is not None else False

    def observe_passive(self, event: AstrMessageEvent) -> None:
        if self.autonomous_runtime is not None:
            self.autonomous_runtime.observe_message(
                event_id=event_to_stimulus(event).event_id,
                channel_id=event.unified_msg_origin,
                actor_id=event.get_sender_id() or "unknown",
                content=event.get_message_str() or event.get_message_outline() or "[empty event]",
                metadata={},
            )

    async def _resolve_persona(self, event: AstrMessageEvent) -> str:
        conversation_persona_id = None
        conversation_manager = getattr(self.plugin_context, "conversation_manager", None)
        if conversation_manager is not None:
            conversation_id = await conversation_manager.get_curr_conversation_id(event.unified_msg_origin)
            if conversation_id:
                conversation = await conversation_manager.get_conversation(
                    event.unified_msg_origin, conversation_id
                )
                if conversation is not None:
                    conversation_persona_id = conversation.persona_id
        persona_manager = getattr(self.plugin_context, "persona_manager", None)
        if persona_manager is None:
            return ""
        get_config = getattr(self.plugin_context, "get_config", None)
        provider_settings = get_config(event.unified_msg_origin) if callable(get_config) else {}
        _, persona, _, _ = await persona_manager.resolve_selected_persona(
            umo=event.unified_msg_origin,
            conversation_persona_id=conversation_persona_id,
            platform_name=event.get_platform_name(),
            provider_settings=provider_settings,
        )
        return str(persona.get("prompt") or "") if persona else ""

    def _persona_growth_context(self) -> str:
        if not self.settings.get("persona_growth_enabled") or self.autonomous_runtime is None:
            return ""
        increments = self.autonomous_runtime.store.persona_increments(
            int(self.settings.get("persona_growth_limit", 12)), status="active"
        )
        if not increments:
            return ""
        lines = ["人格空地：以下是主体从长期经历中形成的、可被新证据修正的倾向，不是绝对规则。"]
        for item in increments:
            lines.append(
                f"- [{item.get('category', 'experience')}] {item.get('statement', '')} "
                f"（置信度={float(item.get('confidence', 0.0)):.2f}，稳定度={float(item.get('stability', 0.0)):.2f}）"
            )
        return "\n".join(lines)

    async def _resolve_persona_context(self, event: AstrMessageEvent | None) -> str:
        parts: list[str] = []
        base = str(self.settings.get("persona_base_prompt") or "").strip()
        if base:
            parts.append(f"基础人格：\n{base}")
        if event is not None and self.settings.get("persona_enabled"):
            selected = (await self._resolve_persona(event)).strip()
            if selected:
                parts.append(f"当前会话人格设定：\n{selected}")
        growth = self._persona_growth_context()
        if growth:
            parts.append(growth)
        return "\n\n".join(parts)

    def _save_persona_increment(self, stimulus: Stimulus, increment: Mapping[str, Any] | None) -> None:
        if not isinstance(increment, Mapping) or self.autonomous_runtime is None:
            return
        statement = str(increment.get("statement") or "").strip()
        if not statement:
            return
        category = str(increment.get("category") or "experience").strip()
        increment_id = "persona-increment:" + hashlib.sha256(
            f"{category}:{statement.casefold()}".encode("utf-8")
        ).hexdigest()[:32]
        try:
            confidence = max(0.0, min(1.0, float(increment.get("confidence", 0.4))))
            stability = max(0.0, min(1.0, float(increment.get("stability", 0.2))))
        except (TypeError, ValueError):
            confidence, stability = 0.4, 0.2
        self.autonomous_runtime.store.save_persona_increment(
            increment_id=increment_id,
            category=category,
            statement=statement,
            value=increment.get("value") if isinstance(increment.get("value"), Mapping) else {},
            evidence=[stimulus.event_id],
            confidence=confidence,
            stability=stability,
            status="candidate",
            auto_activate=bool(self.settings.get("persona_growth_auto_activate")),
            activation_confidence=float(self.settings.get("persona_growth_min_confidence", 0.75)),
            activation_evidence=int(self.settings.get("persona_growth_min_evidence", 3)),
        )

    def _apply_world_observations(self, stimulus: Stimulus, observations: Any) -> None:
        if self.autonomous_runtime is None or not isinstance(observations, list):
            return
        store = self.autonomous_runtime.store
        for position, raw in enumerate(observations[:20]):
            if not isinstance(raw, Mapping):
                continue
            kind = str(raw.get("kind") or "entity").strip().lower()
            try:
                confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
            except (TypeError, ValueError):
                confidence = 0.5
            display_name = str(raw.get("name") or raw.get("display_name") or "").strip()
            entity_id = str(raw.get("id") or "").strip() or f"{kind}:{display_name}"
            if not display_name and not entity_id:
                continue
            if kind in {"entity", "person", "object", "organization", "place"}:
                attributes = dict(raw.get("attributes") or {}) if isinstance(raw.get("attributes"), Mapping) else {}
                for field_name in ("description", "location_id", "owner_id", "state", "status"):
                    if raw.get(field_name) is not None:
                        attributes[field_name] = raw.get(field_name)
                attributes.update({"confidence": confidence, "source": "model_observation"})
                store.observe_world_entity(
                    entity_id,
                    entity_kind="person" if kind == "person" else kind,
                    display_name=display_name or entity_id,
                    attributes=attributes,
                    event_id=stimulus.event_id,
                )
                if kind in {"place", "location"}:
                    self.autonomous_runtime.register_location(
                        entity_id.removeprefix("location:"),
                        name=display_name or entity_id,
                        neighbors=tuple(str(item) for item in raw.get("neighbors", ()) if str(item).strip()),
                        description=str(raw.get("description") or ""),
                        objects=dict(raw.get("objects") or {}) if isinstance(raw.get("objects"), Mapping) else {},
                    )
            elif kind == "belief":
                store.save_belief(
                    f"belief:{stimulus.event_id}:{position}",
                    subject=str(raw.get("subject") or self.agent_id),
                    predicate=str(raw.get("predicate") or "observed"),
                    object_value=raw.get("value", display_name or entity_id),
                    confidence=confidence,
                    source_event_id=stimulus.event_id,
                )
            elif kind == "relation":
                observed_at = datetime.now(timezone.utc).isoformat()
                store.save_temporal_relation(
                    relation_id=f"relation:observation:{stimulus.event_id}:{position}",
                    subject_id=str(raw.get("subject") or self.agent_id),
                    predicate=str(raw.get("predicate") or "related_to"),
                    object_id=str(raw.get("object_id") or entity_id),
                    object_value=raw.get("value", display_name or entity_id),
                    observed_at=observed_at,
                    valid_from=str(raw.get("valid_from") or observed_at),
                    valid_to=str(raw["valid_to"]) if raw.get("valid_to") else None,
                    confidence=confidence,
                    source_event_id=stimulus.event_id,
                )
            elif kind == "schedule":
                start_at, end_at = str(raw.get("start_at") or ""), str(raw.get("end_at") or "")
                if start_at and end_at:
                    self.autonomous_runtime.register_schedule_fact(
                        f"schedule:{stimulus.event_id}:{position}",
                        title=display_name or str(raw.get("title") or "新出现的安排"),
                        location_id=str(raw.get("location_id") or "unknown"),
                        activity=str(raw.get("activity") or "未命名活动"),
                        start_at=start_at,
                        end_at=end_at,
                        actors=tuple(str(item) for item in raw.get("actors", ()) if str(item).strip()),
                        reactions=tuple(item for item in raw.get("reactions", ()) if isinstance(item, Mapping)),
                        source_event_id=stimulus.event_id,
                    )
            elif kind == "rule":
                self.autonomous_runtime.register_world_rule(
                    f"rule:{stimulus.event_id}:{position}",
                    name=display_name or str(raw.get("name") or "新出现的世界反应"),
                    trigger_action_type=str(raw.get("trigger_action_type") or "unknown"),
                    conditions=dict(raw.get("conditions") or {}) if isinstance(raw.get("conditions"), Mapping) else {},
                    reactions=[dict(item) for item in raw.get("reactions", ()) if isinstance(item, Mapping)],
                    priority=confidence,
                    source_event_id=stimulus.event_id,
                )
            self.autonomous_runtime.observe(
                "world_observation",
                event_id=f"world-observation:{stimulus.event_id}:{position}",
                channel_id=stimulus.channel_id,
                actor_id=self.agent_id,
                payload={"kind": kind, "entity_id": entity_id, "confidence": confidence},
            )

    async def process(self, event: AstrMessageEvent, *, batch_id: str | None = None) -> BehaviorResult | None:
        return await self.process_stimulus(event_to_stimulus(event), event=event, batch_id=batch_id)

    async def propose_autonomous_intent(
        self, channel_id: str, *, event: AstrMessageEvent | None = None
    ) -> ActionIntent | None:
        if self.autonomous_runtime is None:
            return None
        provider = await self.plugin_context.get_using_provider_async(channel_id)
        if provider is None:
            return None
        persona = await self._resolve_persona_context(event)
        state = self.autonomous_runtime.store.subjective_state(self.agent_id) or self.autonomous_runtime.set_subjective_state()
        snapshot = {
            "trigger": "time_progressed",
            "subjective_state": {
                "location_id": state.location_id,
                "activity": state.activity,
                "focus": state.focus,
                "mood": dict(state.mood),
                "drives": dict(state.drives),
                "energy": state.energy,
                "attention_load": state.attention_load,
                "updated_at": state.updated_at,
            },
            "scene": self.autonomous_runtime.store.scenes(1),
            "world_entities": self.autonomous_runtime.store.world_entities(30),
            "pending_intentions": self.autonomous_runtime.store.intentions(12, status="planned"),
            "recent_intentions": self.autonomous_runtime.store.intentions(20),
            "recent_actions": self.autonomous_runtime.store.actions(20),
            "pending_obligations": self.autonomous_runtime.store.obligations(12),
            "capabilities": self.autonomous_runtime.store.capabilities(100),
            "recent_events": self.autonomous_runtime.store.events(12),
        }
        system = (
            "你是 Ecobot 世界中的主体，不是调度器。时间流逝只是刺激；你可以提出任意意图、等待或什么都不做。"
            "课程、工作、社交和休息不是强制行为。不要因为上一次选择了某个动作就机械重复；除非状态、动机或环境发生变化，否则优先等待。"
            "不要把所有意图都写成 start_activity；可以选择移动、表达、观察、思考、休息或其他当前世界允许/尚待建立能力的意图。只返回 JSON，不提及 AI、模型或内部机制。"
        )
        if persona:
            system += f"\n主体身份设定：\n{persona}"
        started = time.perf_counter()
        response = await asyncio.wait_for(
            provider.text_chat(
                prompt="当前世界快照：\n" + json.dumps(snapshot, ensure_ascii=False, default=str, separators=(",", ":"))
                + '\n输出：{"action_type":"wait 或任意意图名称","arguments":{},"reason":"中文理由","priority":0.0,"target_id":null}',
                system_prompt=system,
                session_id=f"{channel_id}:autonomous-life",
                temperature=float(self.settings["generation_temperature"]),
                top_p=float(self.settings["generation_top_p"]),
                max_tokens=min(800, int(self.settings["generation_max_tokens"])),
                request_max_retries=max(1, int(self.settings["request_max_retries"]) + 1),
            ),
            timeout=float(self.settings["model_timeout_seconds"]),
        )
        if self.admin_store is not None:
            self.admin_store.record_phase_trace(
                batch_id=None,
                channel_id=channel_id,
                phase="主体意图",
                provider_id="session",
                status="error" if response.role == "err" else "success",
                duration_ms=int((time.perf_counter() - started) * 1000),
                system_prompt=system,
                input_prompt=json.dumps(snapshot, ensure_ascii=False, default=str),
                output_text=response.completion_text if response.role != "err" else None,
                error=response.completion_text if response.role == "err" else None,
            )
        raw = (response.completion_text or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if response.role == "err" or start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
        action_type = str(data.get("action_type") or "wait").strip()
        if action_type.lower() in {"wait", "等待", "none", "无"}:
            return None
        try:
            priority = max(0.0, min(1.0, float(data.get("priority", 0.5))))
        except (TypeError, ValueError):
            priority = 0.5
        arguments = data.get("arguments") if isinstance(data.get("arguments"), Mapping) else {}
        return ActionIntent(
            intent_id=f"intent:autonomous:{time.time_ns()}",
            action_type=action_type,
            target_id=str(data["target_id"]) if data.get("target_id") else None,
            arguments=dict(arguments),
            priority=priority,
            reason=str(data.get("reason") or "主体产生了一个尚未解释的意图"),
        )

    async def _process_subjective_stimulus(
        self,
        stimulus: Stimulus,
        *,
        event: AstrMessageEvent | None,
        batch_id: str | None,
        allow_actions: bool,
        allow_expression: bool,
    ) -> BehaviorResult | None:
        provider = await self.plugin_context.get_using_provider_async(stimulus.channel_id)
        if provider is None:
            return None
        if self.autonomous_runtime is not None:
            self.autonomous_runtime.observe(
                "stimulus_received",
                event_id=f"stimulus:{stimulus.event_id}",
                channel_id=stimulus.channel_id,
                actor_id=stimulus.user_id,
                payload={"content": stimulus.content},
            )
        persona = await self._resolve_persona_context(event)
        state = self.autonomous_runtime.store.subjective_state(self.agent_id) if self.autonomous_runtime else None
        memory_context = self.autonomous_runtime.store.reconstruct_memory(
            query=stimulus.content,
            cues=(stimulus.user_id, stimulus.channel_id),
            tags=(str(stimulus.metadata.get("group_id") or ""),),
            limit=int(self.settings.get("memory_reconstruction_limit", 8)),
            max_hops=int(self.settings.get("memory_reconstruction_hops", 2)),
        ) if self.autonomous_runtime and self.settings.get("memory_reconstruction_enabled") else {"episodes": [], "actions": [], "stop_reason": "memory_unavailable"}
        snapshot = {
            "stimulus": {
                "event_id": stimulus.event_id,
                "channel_id": stimulus.channel_id,
                "user_id": stimulus.user_id,
                "content": stimulus.content,
                "timestamp": stimulus.timestamp,
                "metadata": dict(stimulus.metadata),
            },
            "subjective_state": {
                "location_id": state.location_id,
                "activity": state.activity,
                "focus": state.focus,
                "mood": dict(state.mood),
                "drives": dict(state.drives),
                "energy": state.energy,
                "attention_load": state.attention_load,
                "updated_at": state.updated_at,
            } if state else None,
            "world_entities": self.autonomous_runtime.store.world_entities(40) if self.autonomous_runtime else [],
            "locations": self.autonomous_runtime.store.locations(40) if self.autonomous_runtime else [],
            "scenes": self.autonomous_runtime.store.scenes(10) if self.autonomous_runtime else [],
            "schedule_facts": self.autonomous_runtime.store.schedule_facts(20) if self.autonomous_runtime else [],
            "world_rules": self.autonomous_runtime.store.world_rules()[:20] if self.autonomous_runtime else [],
            "beliefs": self.autonomous_runtime.store.beliefs(20) if self.autonomous_runtime else [],
            "recent_events": self.autonomous_runtime.store.events(12) if self.autonomous_runtime else [],
            "memory_reconstruction": memory_context,
            "persona_increments": self.autonomous_runtime.store.persona_increments(20, status="active") if self.autonomous_runtime else [],
            "pending_obligations": self.autonomous_runtime.store.obligations(12) if self.autonomous_runtime else [],
            "capabilities": self.autonomous_runtime.store.capabilities(100) if self.autonomous_runtime else [],
            "reply_style_guard": self.anti_repeat.reply_style_context(stimulus.channel_id) if self.anti_repeat else {},
        }
        system = (
            "你是人格主体在持续世界中的当前意识，不是回复机器人调度器。"
            "结合自身状态、人格、关系、记忆、世界和当前刺激，自主决定回应、提出意图、等待或忽略。"
            "QQ 消息只是刺激；课程、工作、社交和休息不是必需行为。不要提及 AI、模型、程序或内部机制。"
            "desire_score 表示当前主观行动意愿，不是是否回复的布尔值；必须根据刺激、关系、驱动力和等待累积给出 0 到 100 的连续估计，不要默认 100。"
            "persona_increment 只有在经历真正改变主体对自身偏好、价值、习惯或边界的认识时才填写，否则为 null。"
            "world_observations 只能记录当前证据支持的新世界事实，带置信度，不是对主体的命令。"
            "reply_style_guard 中的近期范式要主动避开，但不要为了避开而破坏自然表达。只返回 JSON。"
        )
        if persona:
            system += f"\n主体身份设定：\n{persona}"
        prompt = "当前主体上下文：\n" + json.dumps(snapshot, ensure_ascii=False, default=str, separators=(",", ":")) + "\n输出格式：" + (
            '{"decision":"reply|intent|wait|ignore","expression":null,"action_type":null,"arguments":{},'
            '"target_id":null,"reason":"中文主观理由","priority":0.0,"relation_delta":0.1,'
            '"desire_score":0.0,"trust_delta":0.0,"familiarity_delta":0.1,"confidence":0.0,"state_update":null,'
            '"persona_increment":null,"world_observations":[]}'
        )
        started = time.perf_counter()
        response = await asyncio.wait_for(
            provider.text_chat(
                prompt=prompt,
                system_prompt=system,
                session_id=f"{stimulus.channel_id}:subject",
                temperature=float(self.settings["generation_temperature"]),
                top_p=float(self.settings["generation_top_p"]),
                max_tokens=int(self.settings["generation_max_tokens"]),
                request_max_retries=max(1, int(self.settings["request_max_retries"]) + 1),
            ),
            timeout=float(self.settings["model_timeout_seconds"]),
        )
        raw = (response.completion_text or "").strip()
        if self.admin_store is not None:
            self.admin_store.record_phase_trace(
                batch_id=batch_id,
                channel_id=stimulus.channel_id,
                phase="主体决策",
                provider_id="session",
                status="error" if response.role == "err" else "success",
                duration_ms=int((time.perf_counter() - started) * 1000),
                system_prompt=system,
                input_prompt=prompt,
                output_text=raw or None,
                error=raw if response.role == "err" else None,
            )
        if response.role == "err":
            return None
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
        decision = str(data.get("decision") or "wait").strip().lower()
        if decision not in {"reply", "intent", "wait", "ignore"}:
            decision = "wait"
        expression = str(data.get("expression") or "").strip() or None
        action_type = str(data.get("action_type") or "").strip()
        arguments = data.get("arguments") if isinstance(data.get("arguments"), Mapping) else {}
        if decision != "reply" or not allow_expression:
            expression = None
        if action_type and allow_actions and self.autonomous_runtime is not None:
            try:
                priority = max(0.0, min(1.0, float(data.get("priority", 0.5))))
            except (TypeError, ValueError):
                priority = 0.5
            self.autonomous_runtime.submit_intent(ActionIntent(
                intent_id=f"intent:stimulus:{stimulus.event_id}",
                action_type=action_type,
                target_id=str(data["target_id"]) if data.get("target_id") else None,
                arguments=dict(arguments),
                priority=priority,
                reason=str(data.get("reason") or "主体根据刺激形成意图"),
            ))
        state_update = data.get("state_update")
        if isinstance(state_update, Mapping) and self.autonomous_runtime is not None:
            self.autonomous_runtime.set_subjective_state(
                location_id=str(state_update["location_id"]) if state_update.get("location_id") else None,
                activity=str(state_update["activity"]) if state_update.get("activity") else None,
                focus=str(state_update["focus"]) if state_update.get("focus") else None,
                mood=state_update.get("mood") if isinstance(state_update.get("mood"), Mapping) else None,
                drives=state_update.get("drives") if isinstance(state_update.get("drives"), Mapping) else None,
                energy=float(state_update["energy"]) if state_update.get("energy") is not None else None,
                attention_load=float(state_update["attention_load"]) if state_update.get("attention_load") is not None else None,
            )
        self._save_persona_increment(stimulus, data.get("persona_increment"))
        self._apply_world_observations(stimulus, data.get("world_observations"))
        try:
            relation_delta = float(data.get("relation_delta", 0.1))
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
            trust_delta = float(data.get("trust_delta", 0.0))
            familiarity_delta = float(data.get("familiarity_delta", 0.1))
        except (TypeError, ValueError):
            relation_delta, confidence, trust_delta, familiarity_delta = 0.1, 0.0, 0.0, 0.1
        if self.autonomous_runtime is not None:
            self.autonomous_runtime.store.apply_relationship_inference(
                stimulus.channel_id,
                stimulus.user_id,
                Inference(
                intent=str(data.get("reason") or decision),
                emotion=str(data.get("emotion") or "平静"),
                relation_delta=relation_delta,
                confidence=confidence,
                trust_delta=trust_delta,
                familiarity_delta=familiarity_delta,
                relationship_reason=str(data.get("reason") or "主体对刺激作出判断"),
                ),
                batch_id=batch_id,
                update=not bool(stimulus.metadata.get("skip_affinity")) and not bool(stimulus.metadata.get("skip_relation")),
            )
        decision_base = {
            "reply": 58.0,
            "intent": 46.0,
            "wait": 22.0,
            "ignore": 10.0,
        }[decision]
        try:
            model_desire = float(data.get("desire_score"))
            if not 0.0 <= model_desire <= 100.0:
                raise ValueError
        except (TypeError, ValueError):
            model_desire = decision_base
        attention_context = stimulus.metadata.get("attention_decision")
        attention_confidence = 0.0
        if isinstance(attention_context, Mapping):
            try:
                attention_confidence = max(0.0, min(1.0, float(attention_context.get("confidence", 0.0))))
            except (TypeError, ValueError):
                attention_confidence = 0.0
        relation = self.autonomous_runtime.store.relationship_profile(stimulus.user_id) if self.autonomous_runtime else {}
        affinity = float(relation.get("affinity_score", 10.0))
        trust = float(relation.get("trust_score", 0.0))
        irritation = float(relation.get("irritation", 0.0))
        time_context = self.anti_repeat.desire_time_context(
            stimulus.channel_id,
            growth_per_hour=8.0,
            maximum_boost=35.0,
        ) if self.anti_repeat else {"time_boost": 0.0}
        desire_score = max(
            0.0,
            min(
                100.0,
                model_desire
                + attention_confidence * (8.0 if decision in {"reply", "intent"} else 2.0)
                + (6.0 if stimulus.metadata.get("is_private") else 0.0)
                + max(-8.0, min(12.0, affinity / 12.0))
                + max(-4.0, min(6.0, trust / 15.0))
                + float(time_context.get("time_boost", 0.0))
                - irritation * 0.12,
            ),
        )
        desire_reasons = (
            str(data.get("reason") or decision),
            f"基础意愿={model_desire:.1f}",
            f"等待累积={float(time_context.get('time_boost', 0.0)):.1f}",
            f"关系影响={affinity / 12.0:.1f}",
        )
        result = BehaviorResult(
            batch_id=batch_id or uuid.uuid4().hex,
            state=BatchState.COMPLETED,
            expression=expression,
            desire=Desire(desire_score, decision == "reply", desire_reasons),
            feedback=(),
            state_history=(BatchState.WAITING, BatchState.COMPLETED),
            stop_reason=None if expression else "subject_decided_to_wait",
            persona_increment=data.get("persona_increment") if isinstance(data.get("persona_increment"), Mapping) else None,
        )
        if expression and self.anti_repeat is not None:
            reservation_id = self.anti_repeat.reserve(stimulus.channel_id, expression, batch_id=result.batch_id)
            if reservation_id is None:
                result = BehaviorResult(
                    batch_id=result.batch_id,
                    state=result.state,
                    expression=None,
                    desire=result.desire,
                    feedback=result.feedback,
                    state_history=result.state_history,
                    stop_reason="duplicate_expression",
                    persona_increment=result.persona_increment,
                )
            else:
                self._expression_reservations[result.batch_id] = reservation_id
        if result.expression and self.autonomous_runtime is not None:
            self.autonomous_runtime.store.save_memory_episode(
                source_event_id=result.batch_id,
                channel_id=stimulus.channel_id,
                subject_id=self.agent_id,
                summary=result.expression,
                event_kind="expression",
                importance=0.5,
                cues=(stimulus.channel_id,),
                tags=("self_expression", decision),
                content={"trigger_event_id": stimulus.event_id, "decision": decision},
            )
            if batch_id:
                attempt = self.autonomous_runtime.start_action(ActionIntent(
                    intent_id=batch_id,
                    action_type="send_expression",
                    target_id=stimulus.user_id if stimulus.metadata.get("is_private") else None,
                    arguments={"channel_id": stimulus.channel_id, "expression": result.expression},
                    reason="主体决定对外表达",
                ))
                self._autonomous_action_attempts[batch_id] = attempt.attempt_id
        if self.settings.get("debug_log_enabled"):
            logger.info(
                "[Ecobot 调试][批次=%s] 主体决策结束 | 决定=%s | 状态=%s | 停止=%s | 表达=%s",
                result.batch_id,
                decision,
                " → ".join(state.value for state in result.state_history),
                STOP_REASON_LABELS.get(result.stop_reason, result.stop_reason or "正常完成"),
                _debug_value(result.expression or "（保持沉默）", limit=4000),
            )
        return result

    async def process_stimulus(
        self,
        stimulus: Stimulus,
        *,
        event: AstrMessageEvent | None = None,
        batch_id: str | None = None,
        allow_actions: bool = True,
        allow_expression: bool = True,
    ) -> BehaviorResult | None:
        qq_context: dict[str, Any] = {}
        participants: list[dict[str, Any]] = []
        if self.qq_archive is not None:
            qq_context = self.qq_archive.model_context(stimulus.user_id, group_id=stimulus.metadata.get("group_id"))
            group_id = stimulus.metadata.get("group_id")
            if group_id:
                participants = self.qq_archive.recent_group_participants(str(group_id), limit=12)
        if self.autonomous_runtime is not None:
            if str(stimulus.metadata.get("trigger") or "message") == "idle":
                self.autonomous_runtime.observe("time_tick", event_id=stimulus.event_id, channel_id=stimulus.channel_id, payload={"source": "idle_heartbeat"})
            else:
                self.autonomous_runtime.observe_message(
                    event_id=stimulus.event_id,
                    channel_id=stimulus.channel_id,
                    actor_id=stimulus.user_id,
                    content=stimulus.content,
                    metadata=stimulus.metadata,
                )
                is_private = bool(stimulus.metadata.get("is_private"))
                explicit_address = bool(stimulus.metadata.get("has_at") or stimulus.metadata.get("has_reply"))
                dialogue_acts = self.autonomous_runtime.store.dialogue_acts(1, event_id=stimulus.event_id)
                dialogue_act = dialogue_acts[0] if dialogue_acts else {}
                address_target = str(dialogue_act.get("target_id") or "")
                address_confidence = max(0.0, min(1.0, float(dialogue_act.get("confidence", 0.5))))
                directed_to_subject = is_private or address_target == self.agent_id
                if is_private:
                    attention_confidence = 0.98
                    attention_cost = 0.2
                    attention_reason = "私聊频道默认指向主体"
                elif directed_to_subject:
                    attention_confidence = max(0.7, address_confidence)
                    attention_cost = max(0.15, 0.45 - attention_confidence * 0.2)
                    attention_reason = "对话分析认为消息指向主体"
                elif explicit_address:
                    attention_confidence = max(0.35, address_confidence * 0.75)
                    attention_cost = 0.4 + (1.0 - attention_confidence) * 0.3
                    attention_reason = "检测到 @ 或回复，但目标尚未确认是主体"
                else:
                    attention_confidence = address_confidence
                    attention_cost = 0.35 + address_confidence * 0.35
                    attention_reason = "群聊未明确指向主体，交由主体结合线程和欲望判断"
                attention = self.autonomous_runtime.record_attention(
                    stimulus.event_id,
                    channel_id=stimulus.channel_id,
                    thread_id=None,
                    addressee_id=address_target or (stimulus.user_id if is_private else None),
                    should_reply=directed_to_subject,
                    confidence=attention_confidence,
                    attention_cost=attention_cost,
                    reason=attention_reason,
                )
                qq_context["recent_group_participants"] = [
                    {
                        **participant,
                        "affinity": {
                            key: profile[key]
                            for key in ("affinity_score", "trust_score", "familiarity", "irritation", "stage", "special_level")
                        },
                    }
                    for participant in participants
                    for profile in (self.autonomous_runtime.store.relationship_profile(str(participant["qq_id"])),)
                ]
                stimulus = Stimulus(
                    event_id=stimulus.event_id,
                    channel_id=stimulus.channel_id,
                    user_id=stimulus.user_id,
                    content=stimulus.content,
                    timestamp=stimulus.timestamp,
                    metadata={
                        **stimulus.metadata,
                        "attention_decision": {
                            "should_reply": attention.should_reply,
                            "confidence": attention.confidence,
                            "attention_cost": attention.attention_cost,
                            "reason": attention.reason,
                        },
                    },
                )
            if qq_context:
                stimulus = Stimulus(
                    event_id=stimulus.event_id,
                    channel_id=stimulus.channel_id,
                    user_id=stimulus.user_id,
                    content=stimulus.content,
                    timestamp=stimulus.timestamp,
                    metadata={**stimulus.metadata, "qq_context": qq_context},
                )
        return await self._process_subjective_stimulus(
            stimulus,
            event=event,
            batch_id=batch_id,
            allow_actions=allow_actions,
            allow_expression=allow_expression,
        )

    def mark_expression(self, batch_id: str, *, success: bool, error: str | None = None) -> None:
        attempt_id = self._autonomous_action_attempts.pop(batch_id, None)
        if attempt_id is not None and self.autonomous_runtime is not None:
            attempt = self.autonomous_runtime.store.action_attempt(attempt_id) or {}
            arguments = attempt.get("arguments") if isinstance(attempt.get("arguments"), Mapping) else {}
            expression = str(arguments.get("expression") or "")
            expression_factor = min(1.0, len(expression) / 80.0)
            directness_factor = 0.12 if arguments.get("target_id") else 0.0
            self.autonomous_runtime.record_receipt(
                attempt_id,
                ActionStatus.SUCCEEDED if success else ActionStatus.FAILED,
                error_code="delivery_failed" if not success else None,
                error_detail=error,
            )
            consequence = self.autonomous_runtime.observe_consequence(
                attempt_id,
                "message_delivered" if success else "delivery_failed",
                confidence=1.0 if success else 0.8,
                payload={"error": error} if error else {},
            )
            self.autonomous_runtime.appraise(
                consequence.consequence_id,
                relevance=min(0.98, 0.35 + expression_factor * 0.35 + directness_factor + (0.12 if success else 0.0)),
                valence=min(1.0, 0.08 + expression_factor * 0.22) if success else -min(1.0, 0.25 + expression_factor * 0.25),
                controllability=min(1.0, 0.5 + (0.3 if success else 0.05) + expression_factor * 0.15),
                agency_confidence=min(1.0, 0.65 + (0.25 if success else 0.0) + directness_factor),
                boundary_violation=0.0,
                emotion="满足" if success else "挫败",
                intensity=min(1.0, 0.06 + expression_factor * 0.28 + (0.08 if success else 0.3)),
                reason=(f"表达已送达，长度和指向使这次互动的重要性为 {expression_factor:.2f}" if success
                        else "表达未能送达，主体需要等待后续环境反馈"),
            )
        reservation_id = self._expression_reservations.pop(batch_id, None)
        if reservation_id is not None and self.anti_repeat is not None:
            self.anti_repeat.mark(reservation_id, success=success, error=error)
