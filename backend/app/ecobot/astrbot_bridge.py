from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import mcp

from astrbot import logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot.core.message.components import At, AtAll, Image, Record, Reply
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .contracts import ActionFeedback, ActionSpec, BatchState, BehaviorResult, Desire, Inference, Stimulus
from ecobot2.contracts import ActionIntent, ActionStatus
from ecobot2.runtime import AutonomousRuntime
from .agent_state import AgentStateStore
from .admin_store import DEFAULT_SETTINGS, EcobotAdminStore
from .anti_repeat import AntiRepeatGuard
from .heartbeat import HeartbeatKernel
from .memory import MemoryStore
from .model_thinker import ModelBehaviorThinker
from .qq_archive import QQArchive
from .style_memory import StyleMemoryStore
from .world_model import WorldModel


PASSIVE_EVENT_EXTRA = "_ecobot_passive_observation"

PHASE_LABELS = {
    "observe": "观察",
    "analyze_infer": "分析推断",
    "desire": "欲望判断",
    "plan": "行为规划",
    "style_rewrite": "高保真风格改写",
    "reflect": "结果反思",
}

STOP_REASON_LABELS = {
    None: "正常完成",
    "silent_by_desire": "欲望判断决定保持沉默",
    "private_cognition_blocked": "检测到内部思考，已阻止对外发送",
    "action_budget_exhausted": "工具动作次数已达到上限",
    "action_round_budget_exhausted": "动作轮数已达到上限",
    "reflection_could_not_replan": "反思阶段无法继续规划",
}

BATCH_STATE_LABELS = {
    "waiting": "等待",
    "observing": "观察",
    "inferring": "分析推断",
    "desiring": "欲望判断",
    "planning": "行为规划",
    "acting": "执行动作",
    "reflecting": "结果反思",
    "completed": "完成",
    "failed": "失败",
}

TRIGGER_LABELS = {
    "message": "消息",
    "idle": "空闲心跳",
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
    """Only user messages may trigger cognition; notices remain archive-only facts."""
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


def _tool_catalog(tool_set) -> str:
    rows = []
    for tool in tool_set.tools:
        schema = json.dumps(tool.parameters or {}, ensure_ascii=False, separators=(",", ":"))
        rows.append(f"- {tool.name}: {tool.description or 'No description'}; schema={schema}")
    return "\n".join(rows) or "No actions are currently available."


def _tool_output(value: Any) -> Any:
    if isinstance(value, mcp.types.CallToolResult):
        return value.model_dump(mode="json")
    return value


class AstrBotBehaviorBridge:
    def __init__(
        self,
        plugin_context,
        world_model: WorldModel,
        qq_archive: QQArchive | None = None,
        agent_state_store: AgentStateStore | None = None,
        memory_store: MemoryStore | None = None,
        style_store: StyleMemoryStore | None = None,
        autonomous_runtime: AutonomousRuntime | None = None,
        anti_repeat: AntiRepeatGuard | None = None,
        admin_store: EcobotAdminStore | None = None,
        agent_id: str = "ecobot",
        max_action_rounds: int = 4,
        max_actions: int = 12,
        memory_limit: int = 12,
        recent_message_limit: int = 20,
        subjective_mode: bool = False,
    ) -> None:
        self.plugin_context = plugin_context
        self.world_model = world_model
        self.qq_archive = qq_archive
        self.agent_state_store = agent_state_store
        self.memory_store = memory_store
        self.style_store = style_store
        self.autonomous_runtime = autonomous_runtime
        self.anti_repeat = anti_repeat
        self.admin_store = admin_store
        self.agent_id = agent_id
        self.max_action_rounds = max_action_rounds
        self.max_actions = max_actions
        self.memory_limit = memory_limit
        self.recent_message_limit = recent_message_limit
        self.subjective_mode = subjective_mode
        self._expression_reservations: dict[str, int] = {}
        self._autonomous_action_attempts: dict[str, str] = {}
        self.settings = dict(DEFAULT_SETTINGS)

    def apply_settings(self, settings: dict[str, Any]) -> None:
        self.settings = {**DEFAULT_SETTINGS, **settings}
        self.max_action_rounds = int(self.settings["max_action_rounds"])
        self.max_actions = int(self.settings["max_actions"])
        self.memory_limit = int(self.settings["memory_limit"])
        self.recent_message_limit = int(self.settings["recent_message_limit"])
        self.world_model.configure_affinity(
            enabled=bool(self.settings["affinity_enabled"]),
            positive_step_limit=float(self.settings["affinity_positive_step_limit"]),
            negative_step_limit=float(self.settings["affinity_negative_step_limit"]),
            irritation_half_life_hours=float(
                self.settings["affinity_irritation_half_life_hours"]
            ),
        )
        if self.anti_repeat is not None:
            from datetime import timedelta

            self.anti_repeat.window = timedelta(
                minutes=int(self.settings["anti_repeat_window_minutes"])
            )
            self.anti_repeat.fuzzy_threshold = float(
                self.settings["anti_repeat_fuzzy_threshold"]
            )
            self.anti_repeat.semantic_threshold = float(
                self.settings["anti_repeat_semantic_threshold"]
            )
            self.anti_repeat.style_window = timedelta(
                minutes=int(self.settings["reply_style_window_minutes"])
            )
            self.anti_repeat.style_repeat_limit = int(
                self.settings["reply_style_repeat_limit"]
            )

    def archive_event(self, event: AstrMessageEvent) -> bool:
        if self.qq_archive is None:
            return False
        return self.qq_archive.record_event(event)

    def observe_passive(self, event: AstrMessageEvent) -> None:
        self.world_model.record_stimulus(event_to_stimulus(event))

    async def _resolve_persona(self, event: AstrMessageEvent) -> str:
        conversation_persona_id = None
        conversation_manager = getattr(self.plugin_context, "conversation_manager", None)
        if conversation_manager is not None:
            conversation_id = await conversation_manager.get_curr_conversation_id(
                event.unified_msg_origin
            )
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
        provider_settings = (
            get_config(event.unified_msg_origin) if callable(get_config) else {}
        )
        _, persona, _, _ = await persona_manager.resolve_selected_persona(
            umo=event.unified_msg_origin,
            conversation_persona_id=conversation_persona_id,
            platform_name=event.get_platform_name(),
            provider_settings=provider_settings,
        )
        return str(persona.get("prompt") or "") if persona else ""

    def _embedding_callback(self):
        if not self.settings["memory_embedding_enabled"]:
            return None
        provider_id = str(self.settings["memory_embedding_provider_id"] or "")
        if provider_id:
            provider = self.plugin_context.get_provider_by_id(provider_id)
            callback = getattr(provider, "get_embedding", None)
            if callable(callback):
                return callback
            logger.warning(
                "[Ecobot 调试] 嵌入模型不可用：%s；已改用默认嵌入模型",
                provider_id,
            )
        get_providers = getattr(self.plugin_context, "get_all_embedding_providers", None)
        if not callable(get_providers):
            return None
        providers = get_providers()
        if not providers:
            return None
        return providers[0].get_embedding

    async def process(
        self,
        event: AstrMessageEvent,
        *,
        batch_id: str | None = None,
    ) -> BehaviorResult | None:
        return await self.process_stimulus(
            event_to_stimulus(event), event=event, batch_id=batch_id
        )

    async def propose_autonomous_intent(
        self, channel_id: str, *, event: AstrMessageEvent | None = None
    ) -> ActionIntent | None:
        if self.autonomous_runtime is None:
            return None
        provider = await self.plugin_context.get_using_provider_async(channel_id)
        if provider is None:
            return None
        persona = await self._resolve_persona(event) if event is not None else ""
        state = self.autonomous_runtime.store.subjective_state(self.agent_id)
        if state is None:
            state = self.autonomous_runtime.set_subjective_state()
        scene = self.autonomous_runtime.store.scene("main")
        prompt = json.dumps(
            {
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
                "scene": (
                    {
                        "location_id": scene.location_id,
                        "local_time": scene.local_time,
                        "activity": scene.activity,
                        "occupants": list(scene.occupants),
                        "objects": dict(scene.objects),
                    }
                    if scene
                    else None
                ),
                "pending_intentions": self.autonomous_runtime.store.intentions(
                    12, status="planned"
                ),
                "pending_obligations": self.autonomous_runtime.store.obligations(12),
                "capabilities": self.autonomous_runtime.store.capabilities(100),
                "recent_events": self.autonomous_runtime.store.events(12),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        system = (
            "你是 Ecobot 人格主体的自主意图能力。你不是调度器，也没有必须完成的行为。"
            "根据主体自己的身份、当前感受、世界状态和历史经验，自由决定此刻想做什么，"
            "也可以选择等待、观察或什么都不做。课程、工作、社交和休息都只是可能的世界事实，"
            "不能因为某项事实存在就强迫主体服从。允许提出当前能力暂时无法完成的开放意图。"
            "只返回 JSON，不输出解释。"
        )
        if persona:
            system += f"\n主体身份设定：\n{persona}"
        response = await asyncio.wait_for(
            provider.text_chat(
                prompt=(
                    "当前主体与世界快照：\n"
                    f"{prompt}\n\n"
                    "输出：{\"action_type\":\"wait 或任意意图名称\","
                    "\"arguments\":{},\"reason\":\"中文主观理由\","
                    "\"priority\":0.0,\"target_id\":null}"
                ),
                system_prompt=system,
                session_id=f"{channel_id}:autonomous-life",
                temperature=float(self.settings["generation_temperature"]),
                top_p=float(self.settings["generation_top_p"]),
                max_tokens=min(800, int(self.settings["generation_max_tokens"])),
                request_max_retries=max(1, int(self.settings["request_max_retries"]) + 1),
            ),
            timeout=float(self.settings["model_timeout_seconds"]),
        )
        if response.role == "err" or not response.completion_text:
            return None
        raw = response.completion_text.strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
        action_type = str(data.get("action_type") or "wait").strip()
        if not action_type or action_type.lower() in {"wait", "等待", "none", "无"}:
            return None
        try:
            priority = max(0.0, min(1.0, float(data.get("priority", 0.5))))
        except (TypeError, ValueError):
            priority = 0.5
        arguments = data.get("arguments")
        if not isinstance(arguments, Mapping):
            arguments = {}
        return ActionIntent(
            intent_id=f"intent:autonomous:{time.time_ns()}",
            action_type=action_type,
            target_id=(str(data["target_id"]) if data.get("target_id") else None),
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
        allow_expression: bool,
    ) -> BehaviorResult | None:
        provider = await self.plugin_context.get_using_provider_async(stimulus.channel_id)
        if provider is None:
            return None
        if self.world_model is not None:
            self.world_model.record_stimulus(stimulus)
        persona = await self._resolve_persona(event) if event is not None else ""
        state = self.autonomous_runtime.store.subjective_state(self.agent_id) if self.autonomous_runtime else None
        snapshot = {
            "stimulus": {
                "event_id": stimulus.event_id,
                "channel_id": stimulus.channel_id,
                "user_id": stimulus.user_id,
                "content": stimulus.content,
                "timestamp": stimulus.timestamp,
                "metadata": dict(stimulus.metadata),
            },
            "subjective_state": (
                {
                    "location_id": state.location_id,
                    "activity": state.activity,
                    "focus": state.focus,
                    "mood": dict(state.mood),
                    "drives": dict(state.drives),
                    "energy": state.energy,
                    "attention_load": state.attention_load,
                    "updated_at": state.updated_at,
                }
                if state
                else None
            ),
            "recent_events": self.autonomous_runtime.store.events(12) if self.autonomous_runtime else [],
            "pending_obligations": self.autonomous_runtime.store.obligations(12) if self.autonomous_runtime else [],
            "capabilities": self.autonomous_runtime.store.capabilities(100) if self.autonomous_runtime else [],
        }
        system = (
            "你是人格主体的当前决策能力。你不是回复机器人调度器，也没有预设必须完成的行为。"
            "结合主体身份、主观状态、当前刺激和世界事实，自主决定回应、行动、等待、忽略或提出开放意图。"
            "QQ 消息只是刺激；课程、社交、休息和工作都不是必需行为。不要跳出主体身份，不要提及 AI、模型、程序或系统。"
            "只返回 JSON。"
        )
        if persona:
            system += f"\n主体身份设定：\n{persona}"
        prompt = (
            "当前主体上下文：\n"
            + json.dumps(snapshot, ensure_ascii=False, default=str, separators=(",", ":"))
            + "\n输出格式："
            '{"decision":"reply|intent|wait|ignore",'
            '"expression":null,"action_type":null,"arguments":{},'
            '"target_id":null,"reason":"中文主观理由",'
            '"priority":0.0,"relation_delta":0.1,"trust_delta":0.0,'
            '"familiarity_delta":0.1,"confidence":0.0,"state_update":null}'
        )
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
        if response.role == "err" or not response.completion_text:
            return None
        raw = response.completion_text.strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
        decision = str(data.get("decision") or "wait").strip().lower()
        expression = str(data.get("expression") or "").strip() or None
        action_type = str(data.get("action_type") or "").strip()
        arguments = data.get("arguments") if isinstance(data.get("arguments"), Mapping) else {}
        if decision not in {"reply", "intent", "wait", "ignore"}:
            decision = "wait"
        if not allow_expression or decision != "reply":
            expression = None
        if decision in {"wait", "ignore"} and not action_type:
            expression = None
        if action_type and self.autonomous_runtime is not None:
            try:
                priority = max(0.0, min(1.0, float(data.get("priority", 0.5))))
            except (TypeError, ValueError):
                priority = 0.5
            self.autonomous_runtime.submit_intent(
                ActionIntent(
                    intent_id=f"intent:stimulus:{stimulus.event_id}",
                    action_type=action_type,
                    target_id=str(data["target_id"]) if data.get("target_id") else None,
                    arguments=dict(arguments),
                    priority=priority,
                    reason=str(data.get("reason") or "主体根据刺激形成意图"),
                )
            )
        state_update = data.get("state_update")
        if isinstance(state_update, Mapping) and self.autonomous_runtime is not None:
            energy = state_update.get("energy")
            attention_load = state_update.get("attention_load")
            self.autonomous_runtime.set_subjective_state(
                location_id=(str(state_update["location_id"]) if state_update.get("location_id") else None),
                activity=(str(state_update["activity"]) if state_update.get("activity") else None),
                focus=(str(state_update["focus"]) if state_update.get("focus") else None),
                mood=(state_update.get("mood") if isinstance(state_update.get("mood"), Mapping) else None),
                drives=(state_update.get("drives") if isinstance(state_update.get("drives"), Mapping) else None),
                energy=(float(energy) if energy is not None else None),
                attention_load=(float(attention_load) if attention_load is not None else None),
            )
        try:
            relation_delta = float(data.get("relation_delta", 0.1))
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
            trust_delta = float(data.get("trust_delta", 0.0))
            familiarity_delta = float(data.get("familiarity_delta", 0.1))
        except (TypeError, ValueError):
            relation_delta, confidence, trust_delta, familiarity_delta = 0.1, 0.0, 0.0, 0.1
        if self.world_model is not None:
            self.world_model.commit_inference(
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
                update_affinity=not bool(stimulus.metadata.get("skip_affinity")),
                update_relation=not bool(stimulus.metadata.get("skip_relation")),
            )
        desire_score = 100.0 if decision == "reply" else 0.0
        result = BehaviorResult(
            batch_id=batch_id or uuid.uuid4().hex,
            state=BatchState.COMPLETED,
            expression=expression,
            desire=Desire(desire_score, decision == "reply", (str(data.get("reason") or decision),)),
            feedback=(),
            state_history=(BatchState.WAITING, BatchState.COMPLETED),
            stop_reason=None if expression else "subject_decided_to_wait",
        )
        if expression and self.memory_store is not None:
            self.memory_store.remember(
                expression,
                source_type="ecobot_subjective_expression",
                source_id=result.batch_id,
                memory_type="behavior",
                channel_id=stimulus.channel_id,
                user_id=stimulus.user_id,
                importance=0.5,
                metadata={"decision": decision},
            )
        if expression and batch_id and self.autonomous_runtime is not None:
            attempt = self.autonomous_runtime.start_action(
                ActionIntent(
                    intent_id=batch_id,
                    action_type="send_expression",
                    target_id=stimulus.user_id if stimulus.metadata.get("is_private") else None,
                    arguments={"channel_id": stimulus.channel_id, "expression": expression},
                    reason="主体决定对外表达",
                )
            )
            self._autonomous_action_attempts[batch_id] = attempt.attempt_id
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
            qq_context = self.qq_archive.model_context(
                stimulus.user_id,
                group_id=stimulus.metadata.get("group_id"),
            )
            group_id = stimulus.metadata.get("group_id")
            if group_id:
                participants = self.qq_archive.recent_group_participants(
                    str(group_id), limit=12
                )
        if self.autonomous_runtime is not None:
            if str(stimulus.metadata.get("trigger") or "message") == "idle":
                self.autonomous_runtime.observe(
                    "time_tick",
                    event_id=stimulus.event_id,
                    channel_id=stimulus.channel_id,
                    payload={"source": "idle_heartbeat"},
                )
            else:
                self.autonomous_runtime.observe_message(
                    event_id=stimulus.event_id,
                    channel_id=stimulus.channel_id,
                    actor_id=stimulus.user_id,
                    content=stimulus.content,
                    metadata=stimulus.metadata,
                )
                is_private = bool(stimulus.metadata.get("is_private"))
                explicit_address = bool(
                    stimulus.metadata.get("has_at") or stimulus.metadata.get("has_reply")
                )
                attention = self.autonomous_runtime.record_attention(
                    stimulus.event_id,
                    channel_id=stimulus.channel_id,
                    thread_id=None,
                    addressee_id=stimulus.user_id if is_private else None,
                    should_reply=is_private or explicit_address,
                    confidence=0.98 if is_private or explicit_address else 0.58,
                    attention_cost=0.2 if is_private or explicit_address else 0.55,
                    reason=(
                        "私聊默认指向 Ecobot"
                        if is_private
                        else "检测到显式 @ 或回复引用"
                        if explicit_address
                        else "群聊未检测到明确指向，交由欲望和线程判断"
                    ),
                )
                qq_context["recent_group_participants"] = [
                    {
                        **participant,
                        "affinity": {
                            key: profile[key]
                            for key in (
                                "affinity_score",
                                "trust_score",
                                "familiarity",
                                "irritation",
                                "stage",
                                "special_level",
                            )
                        },
                    }
                    for participant in participants
                    for profile in (
                        self.world_model.affinity_profile(str(participant["qq_id"])),
                    )
                ]
                stimulus = replace(
                    stimulus,
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
                stimulus = replace(
                    stimulus,
                    metadata={**stimulus.metadata, "qq_context": qq_context},
                )
        if self.subjective_mode:
            return await self._process_subjective_stimulus(
                stimulus,
                event=event,
                batch_id=batch_id,
                allow_expression=allow_expression,
            )
        session_provider = await self.plugin_context.get_using_provider_async(
            stimulus.channel_id
        )
        enabled_phases = {
            "observe": bool(self.settings["observe_enabled"]),
            "analyze_infer": bool(self.settings["infer_enabled"]),
            "desire": bool(self.settings["desire_enabled"]),
            "plan": bool(self.settings["plan_enabled"]),
            "reflect": bool(self.settings["reflect_enabled"]),
        }
        if session_provider is None and any(enabled_phases.values()):
            return None

        tool_set = self.plugin_context.get_llm_tool_manager().get_full_tool_set()
        imitation_enabled = bool(self.settings["identity_imitation_enabled"])
        imitation_user_id = str(self.settings["identity_imitation_user_id"] or "").strip()
        if imitation_enabled and not imitation_user_id:
            logger.error(
                "[Ecobot 调试] 身份模仿配置缺少目标 QQ 号，已回退到原人格链路"
            )
            imitation_enabled = False
        persona = (
            ""
            if imitation_enabled
            else (
                await self._resolve_persona(event)
                if event is not None and self.settings["persona_enabled"]
                else ""
            )
        )
        embed = self._embedding_callback()
        embedding_cache: dict[str, list[float]] = {}

        async def cached_embed(content: str) -> list[float]:
            if content in embedding_cache:
                return embedding_cache[content]
            if embed is None:
                return []
            value = await embed(content)
            embedding_cache[content] = value
            return value

        embedding_callback = cached_embed if embed is not None else None
        memories = (
            await self.memory_store.retrieve_with_embedding(
                stimulus,
                embed=embedding_callback,
                limit=self.memory_limit,
                recent_limit=self.recent_message_limit,
                semantic_min_similarity=float(
                    self.settings["memory_semantic_min_similarity"]
                ),
            )
            if self.memory_store is not None
            else ()
        )
        style_reference: dict[str, Any] = {}
        trigger = str(stimulus.metadata.get("trigger") or "message")
        if self.style_store is not None:
            style_embedding = None
            if trigger != "idle" and embedding_callback is not None:
                try:
                    style_embedding = await embedding_callback(stimulus.content)
                except Exception:
                    style_embedding = None
            if trigger != "idle" and self.settings["style_learning_enabled"]:
                self.style_store.learn(
                    stimulus.user_id,
                    stimulus.content,
                    source_id=f"qq_message:{stimulus.event_id}",
                    channel_id=str(stimulus.metadata.get("group_id") or "") or None,
                    embedding=style_embedding,
                )
            reference_users = {
                str(user_id).strip()
                for user_id in self.settings["style_reference_user_ids"]
                if str(user_id).strip()
            }
            reference_target = (
                imitation_user_id
                if imitation_enabled and imitation_user_id
                else stimulus.user_id
                if stimulus.user_id in reference_users
                else ""
            )
            if (
                (self.settings["style_reference_enabled"] or imitation_enabled)
                and reference_target
            ):
                style_reference = self.style_store.context(
                    reference_target,
                    stimulus.content if trigger != "idle" else "",
                    query_embedding=(
                        embedding_cache.get(stimulus.content)
                        if trigger != "idle"
                        else None
                    ),
                    limit=int(self.settings["style_reference_limit"]),
                    minimum_similarity=float(self.settings["style_reference_min_similarity"]),
                    exclude_source_id=f"qq_message:{stimulus.event_id}",
                )
                if imitation_enabled:
                    style_reference["imitation_mode"] = True
                    style_reference["target_user_id"] = imitation_user_id
                    style_reference["available"] = bool(style_reference["references"])
                    if not style_reference["available"]:
                        logger.warning(
                            "[Ecobot 调试] 身份模仿目标没有可用风格样本 | QQ=%s",
                            imitation_user_id,
                        )
        reply_style_guard = (
            self.anti_repeat.reply_style_context(stimulus.channel_id)
            if self.anti_repeat is not None
            and self.settings["reply_style_repeat_enabled"]
            else {}
        )
        if style_reference or reply_style_guard:
            stimulus = replace(
                stimulus,
                metadata={
                    **stimulus.metadata,
                    "style_reference": style_reference,
                    "reply_style_guard": reply_style_guard,
                },
            )

        phase_setting_keys = {
            "observe": "observe_provider_id",
            "analyze_infer": "infer_provider_id",
            "desire": "desire_provider_id",
            "plan": "plan_provider_id",
            "style_rewrite": "style_rewrite_provider_id",
            "reflect": "reflect_provider_id",
        }

        def phase_provider(phase: str):
            configured_id = str(self.settings[phase_setting_keys[phase]] or "")
            if configured_id:
                selected = self.plugin_context.get_provider_by_id(configured_id)
                if selected is not None and callable(getattr(selected, "text_chat", None)):
                    return selected, configured_id
                logger.warning(
                    "[Ecobot 调试] 阶段模型不可用：模型=%s，阶段=%s；已回退到当前会话模型",
                    configured_id,
                    PHASE_LABELS[phase],
                )
            return session_provider, configured_id or "session"

        def phase_completer(phase: str):
            async def complete(system_prompt: str, prompt: str) -> str:
                provider, provider_id = phase_provider(phase)
                if provider is None:
                    raise RuntimeError(f"阶段“{PHASE_LABELS[phase]}”没有可用的对话模型")
                started = time.perf_counter()
                output = None
                error = None
                if self.settings["debug_log_enabled"]:
                    logger.info(
                        "[Ecobot 调试][批次=%s][%s] 开始调用模型 | 模型=%s | 频道=%s | 系统提示=%d字符 | 输入=%d字符",
                        batch_id or "临时批次",
                        PHASE_LABELS[phase],
                        provider_id,
                        stimulus.channel_id,
                        len(system_prompt),
                        len(prompt),
                    )
                    if self.settings["debug_log_include_prompts"]:
                        logger.info(
                            "[Ecobot 调试][批次=%s][%s] 系统提示词：\n%s\n输入提示词：\n%s",
                            batch_id or "临时批次",
                            PHASE_LABELS[phase],
                            system_prompt,
                            prompt,
                        )
                try:
                    response = await asyncio.wait_for(
                        provider.text_chat(
                            prompt=prompt,
                            system_prompt=system_prompt,
                            session_id=stimulus.channel_id,
                            temperature=float(self.settings["generation_temperature"]),
                            top_p=float(self.settings["generation_top_p"]),
                            max_tokens=int(self.settings["generation_max_tokens"]),
                            request_max_retries=max(
                                1, int(self.settings["request_max_retries"]) + 1
                            ),
                        ),
                        timeout=float(self.settings["model_timeout_seconds"]),
                    )
                    if response.role == "err":
                        raise RuntimeError(
                            response.completion_text or "model request failed"
                        )
                    output = response.completion_text
                    if not output:
                        raise RuntimeError("模型返回了空的结构化结果")
                    if self.settings["debug_log_enabled"]:
                        logger.info(
                            "[Ecobot 调试][批次=%s][%s] 调用完成 | 模型=%s | 耗时=%d毫秒 | 思考结果：\n%s",
                            batch_id or "临时批次",
                            PHASE_LABELS[phase],
                            provider_id,
                            round((time.perf_counter() - started) * 1000),
                            output,
                        )
                    return output
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    if self.settings["debug_log_enabled"]:
                        logger.error(
                            "[Ecobot 调试][批次=%s][%s] 调用失败 | 模型=%s | 耗时=%d毫秒 | 错误=%s",
                            batch_id or "临时批次",
                            PHASE_LABELS[phase],
                            provider_id,
                            round((time.perf_counter() - started) * 1000),
                            error,
                        )
                    raise
                finally:
                    if self.admin_store is not None:
                        self.admin_store.record_phase_trace(
                            batch_id=batch_id,
                            channel_id=stimulus.channel_id,
                            phase=phase,
                            provider_id=provider_id,
                            status="error" if error else "success",
                            duration_ms=round((time.perf_counter() - started) * 1000),
                            system_prompt=system_prompt,
                            input_prompt=prompt,
                            output_text=output,
                            error=error,
                        )

            return complete

        completers = {phase: phase_completer(phase) for phase in enabled_phases}
        completers["style_rewrite"] = phase_completer("style_rewrite")

        desire_time_context = {}
        if (
            self.anti_repeat is not None
            and self.settings["desire_time_growth_enabled"]
        ):
            desire_time_context = self.anti_repeat.desire_time_context(
                stimulus.channel_id,
                growth_per_hour=float(self.settings["desire_time_growth_per_hour"]),
                maximum_boost=float(self.settings["desire_time_growth_max"]),
            )

        async def execute(action: ActionSpec) -> ActionFeedback:
            if self.settings["debug_log_enabled"]:
                logger.info(
                    "[Ecobot 调试][批次=%s][工具调用] 准备执行 | 工具=%s | 风险=%s | 参数=%s",
                    batch_id or "临时批次",
                    action.name,
                    action.risk,
                    _debug_value(action.arguments, limit=4000),
                )
            if not self.settings["tools_enabled"] or not allow_actions:
                return ActionFeedback(action, success=False, error="tool execution is disabled")
            if event is None:
                return ActionFeedback(
                    action,
                    success=False,
                    error="actions requiring an incoming event are unavailable during idle heartbeat",
                )
            allowlist = set(self.settings["tool_allowlist"])
            if allowlist and action.name not in allowlist:
                return ActionFeedback(action, success=False, error="tool is not in the allowlist")
            risk_order = {"low": 0, "medium": 1, "high": 2}
            if risk_order.get(action.risk, 2) > risk_order[self.settings["max_tool_risk"]]:
                return ActionFeedback(action, success=False, error="tool risk exceeds configured maximum")
            tool = tool_set.get_tool(action.name)
            if tool is None:
                return ActionFeedback(
                    action,
                    success=False,
                    error=f"action is unavailable: {action.name}",
                )
            run_context = ContextWrapper(
                context=AstrAgentContext(context=self.plugin_context, event=event),
                tool_call_timeout=120,
            )
            outputs = []
            async for output in FunctionToolExecutor.execute(
                tool, run_context, **dict(action.arguments)
            ):
                if output is not None:
                    outputs.append(_tool_output(output))
            has_error = any(
                isinstance(item, dict) and item.get("isError") is True
                for item in outputs
            )
            feedback = ActionFeedback(
                action,
                success=not has_error,
                output=outputs,
                error="tool reported an error" if has_error else None,
            )
            if self.settings["debug_log_enabled"]:
                logger.info(
                    "[Ecobot 调试][批次=%s][工具调用] 执行完成 | 工具=%s | 成功=%s | 结果=%s",
                    batch_id or "临时批次",
                    action.name,
                    "是" if feedback.success else "否",
                    _debug_value(feedback.output, limit=6000),
                )
            return feedback

        actual_batch_id = batch_id

        async def allow_expression(channel_id: str, expression: str) -> bool:
            if not allow_expression:
                return False
            if self.anti_repeat is None:
                return True
            embedding = None
            if embedding_callback is not None:
                try:
                    embedding = await embedding_callback(expression)
                except Exception:
                    embedding = None
            reservation_id = self.anti_repeat.reserve(
                channel_id,
                expression,
                batch_id=actual_batch_id,
                embedding=embedding,
            )
            if reservation_id is None:
                return False
            if actual_batch_id:
                self._expression_reservations[actual_batch_id] = reservation_id
            return True

        thinker = ModelBehaviorThinker(
            completers["observe"],
            persona=persona,
            action_catalog=(
                _tool_catalog(tool_set)
                if self.settings["tools_enabled"] and allow_actions
                else "当前没有可用动作。"
            ),
            structured_output_retries=int(self.settings["structured_output_retries"]),
            phase_completers=completers,
            phase_enabled=enabled_phases,
            phase_prompts={
                "observe": str(self.settings["observe_prompt"]),
                "analyze_infer": str(self.settings["infer_prompt"]),
                "desire": str(self.settings["desire_prompt"]),
                "plan": str(self.settings["plan_prompt"]),
                "style_rewrite": str(self.settings["style_rewrite_prompt"]),
                "reflect": str(self.settings["reflect_prompt"]),
            },
            desire_threshold=float(self.settings["desire_threshold"]),
            desire_time_context=desire_time_context,
        )

        async def rewrite_expression(
            rewrite_stimulus: Stimulus,
            expression: str,
            rewrite_world,
        ) -> str | None:
            if not (
                self.settings["identity_imitation_enabled"]
                and self.settings["high_fidelity_imitation_enabled"]
                and rewrite_stimulus.metadata.get("style_reference", {}).get(
                    "imitation_mode"
                )
                and rewrite_stimulus.metadata.get("style_reference", {}).get(
                    "available"
                )
            ):
                return expression
            try:
                return await thinker.rewrite_expression(
                    rewrite_stimulus, expression, rewrite_world
                )
            except Exception:
                logger.exception(
                    "[Ecobot 调试] 高保真风格改写失败，已使用规划阶段原回复 | 批次=%s",
                    batch_id or "临时批次",
                )
                return expression

        kernel = HeartbeatKernel(
            self.world_model,
            thinker,
            execute,
            max_action_rounds=self.max_action_rounds,
            max_actions=self.max_actions,
            agent_state_store=self.agent_state_store,
            agent_id=self.agent_id,
            expression_gate=allow_expression,
            expression_rewriter=rewrite_expression,
            state_update_enabled=bool(self.settings["state_update_enabled"]),
        )
        if self.settings["debug_log_enabled"]:
            logger.info(
                "[Ecobot 调试][批次=%s] 开始思考 | 触发=%s | 用户=%s | 频道=%s | 内容=%s | 记忆=%d条 | 人格=%s",
                batch_id or "临时批次",
                TRIGGER_LABELS.get(
                    str(stimulus.metadata.get("trigger") or "message"),
                    str(stimulus.metadata.get("trigger") or "消息"),
                ),
                stimulus.user_id,
                stimulus.channel_id,
                _debug_value(stimulus.content, limit=1000),
                len(memories),
                "已载入" if persona else "未载入",
            )
        result = await kernel.process(stimulus, batch_id=batch_id, memories=memories)
        if (
            self.autonomous_runtime is not None
            and result.expression
            and batch_id
        ):
            attempt = self.autonomous_runtime.start_action(
                ActionIntent(
                    intent_id=batch_id,
                    action_type="send_expression",
                    target_id=stimulus.user_id if stimulus.metadata.get("is_private") else None,
                    arguments={
                        "channel_id": stimulus.channel_id,
                        "expression": result.expression,
                    },
                    reason="行为规划产生对外表达",
                )
            )
            self._autonomous_action_attempts[batch_id] = attempt.attempt_id
        if self.settings["debug_log_enabled"]:
            logger.info(
                "[Ecobot 调试][批次=%s] 思考结束 | 欲望分数=%.1f | 阈值=%.1f | 是否参与=%s | 判断依据=%s | 状态链=%s | 停止原因=%s | 最终表达=%s",
                result.batch_id,
                result.desire.score,
                float(self.settings["desire_threshold"]),
                "是" if result.desire.should_engage else "否",
                _debug_value(result.desire.reasons, limit=2000),
                " → ".join(
                    BATCH_STATE_LABELS.get(state.value, state.value)
                    for state in result.state_history
                ),
                STOP_REASON_LABELS.get(result.stop_reason, result.stop_reason or "正常完成"),
                _debug_value(result.expression or "（保持沉默）", limit=6000),
            )
        if self.memory_store is not None:
            if result.expression:
                await self.memory_store.remember_with_embedding(
                    result.expression,
                    embed=embed,
                    source_type="ecobot_expression",
                    source_id=result.batch_id,
                    memory_type="behavior",
                    channel_id=stimulus.channel_id,
                    user_id=stimulus.user_id,
                    importance=min(1.0, result.desire.score / 100.0),
                    metadata={"stop_reason": result.stop_reason},
                )
            for position, feedback in enumerate(result.feedback):
                self.memory_store.remember(
                    json.dumps(
                        {
                            "action": feedback.action.name,
                            "success": feedback.success,
                            "output": feedback.output,
                            "error": feedback.error,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                    source_type="ecobot_action_feedback",
                    source_id=f"{result.batch_id}:{position}",
                    memory_type="procedural",
                    channel_id=stimulus.channel_id,
                    user_id=stimulus.user_id,
                    importance=0.7 if not feedback.success else 0.5,
                )
        return result

    def mark_expression(
        self, batch_id: str, *, success: bool, error: str | None = None
    ) -> None:
        attempt_id = self._autonomous_action_attempts.pop(batch_id, None)
        if attempt_id is not None and self.autonomous_runtime is not None:
            self.autonomous_runtime.record_receipt(
                attempt_id,
                ActionStatus.SUCCEEDED if success else ActionStatus.FAILED,
                error_code=("delivery_failed" if not success else None),
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
                relevance=0.65,
                valence=0.2 if success else -0.35,
                controllability=0.7 if success else 0.35,
                agency_confidence=1.0 if success else 0.0,
                boundary_violation=0.0,
                emotion="满足" if success else "挫败",
                intensity=0.2 if success else 0.45,
                reason="表达成功送达" if success else "表达未能送达，等待后续环境反馈",
            )
        reservation_id = self._expression_reservations.pop(batch_id, None)
        if reservation_id is not None and self.anti_repeat is not None:
            self.anti_repeat.mark(reservation_id, success=success, error=error)
