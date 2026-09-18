import asyncio
import time
from contextlib import suppress
from datetime import timedelta

from astrbot import logger
from astrbot.core.message.message_event_result import (
    MessageChain,
    MessageEventResult,
    ResultContentType,
)
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.filter.regex import RegexFilter
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from ecobot.astrbot_bridge import (
    PASSIVE_EVENT_EXTRA,
    STOP_REASON_LABELS,
    AstrBotBehaviorBridge,
    event_to_stimulus,
    is_behavioral_message_event,
    is_enabled,
)
from ecobot.admin_store import EcobotAdminStore
from ecobot.anti_repeat import AntiRepeatGuard
from ecobot.contracts import Stimulus
from ecobot.qq_archive import QQArchive
from ecobot.refresh_scheduler import RefreshScheduler
from ecobot2.runtime import AutonomousRuntime
from ecobot2.life_loop import AutonomousLifeLoop

from ..context import PipelineContext
from ..stage import Stage, register_stage


def _has_explicit_plugin_request(event: AstrMessageEvent) -> bool:
    explicit_filters = (CommandFilter, CommandGroupFilter, RegexFilter)
    return any(
        isinstance(event_filter, explicit_filters)
        for handler in event.get_extra("activated_handlers", [])
        for event_filter in handler.event_filters
    )


@register_stage
class EcobotBehaviorStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        database_path = f"{get_astrbot_data_path()}/ecobot/world.db"
        self.ctx = ctx
        self.settings_store = EcobotAdminStore(database_path)
        settings = self.settings_store.settings()
        self.autonomous_runtime = AutonomousRuntime(database_path)
        self.life_loop = AutonomousLifeLoop(self.autonomous_runtime)
        if self.autonomous_runtime.store.subjective_state("ecobot") is None:
            self.autonomous_runtime.set_subjective_state()
        self.anti_repeat = AntiRepeatGuard(database_path)
        self.refresh_scheduler = RefreshScheduler(
            database_path,
            passive_interval=timedelta(
                seconds=settings["passive_interval_seconds"]
            ),
            idle_interval=timedelta(seconds=settings["idle_interval_seconds"]),
        )
        self.qq_archive = QQArchive(database_path)
        self._routes: dict[str, AstrMessageEvent] = {}
        self.bridge = AstrBotBehaviorBridge(
            ctx.plugin_manager.context,
            self.qq_archive,
            self.autonomous_runtime,
            self.anti_repeat,
            self.settings_store,
        )
        self.bridge.apply_settings(settings)
        if settings["debug_log_enabled"]:
            logger.info(
                "[Ecobot 调试] 中文调试日志已启用 | 主体决策追踪=%s",
                "开启" if settings["trace_enabled"] else "关闭",
            )
        self._idle_task = asyncio.create_task(
            self._idle_loop(), name="ecobot-idle-driver"
        )
        self._life_task = asyncio.create_task(
            self._life_loop(), name="ecobot-life-driver"
        )

    async def process(self, event: AstrMessageEvent) -> None:
        if not is_enabled():
            return
        settings = self.settings_store.settings()
        self.bridge.apply_settings(settings)
        if not settings["enabled"]:
            return

        if _has_explicit_plugin_request(event):
            return

        if not is_behavioral_message_event(event):
            logger.debug(
                "[Ecobot][聊天调度器] 已忽略输入状态通知，不创建思考批次 | 频道=%s",
                event.unified_msg_origin,
            )
            event.stop_event()
            return

        stimulus = event_to_stimulus(event)
        self._routes[stimulus.channel_id] = event
        try:
            decision, result = await self.refresh_scheduler.run(
                stimulus,
                lambda batch_id: self.bridge.process(event, batch_id=batch_id),
                agent_id="ecobot",
            )
        except Exception:
            logger.exception(
                "[Ecobot][心跳处理器] 行为桥接失败，已回退到 AstrBot"
            )
            return

        if not decision.should_process:
            logger.debug(
                "[Ecobot][刷新调度器] 消息已合并等待 | 频道=%s | 原因=%s",
                stimulus.channel_id,
                decision.reason,
            )
            if event.get_extra(PASSIVE_EVENT_EXTRA, False):
                event.stop_event()
            return

        if result is None:
            if event.get_extra(PASSIVE_EVENT_EXTRA, False):
                event.stop_event()
            return

        event.should_call_llm(True)
        event.set_extra("activated_handlers", [])
        event.set_extra("ecobot_behavior_result", result)
        if result.expression:
            event.set_result(
                MessageEventResult()
                .message(result.expression)
                .set_result_content_type(ResultContentType.LLM_RESULT)
            )
            event.set_extra("_ecobot_behavior_bridge", self.bridge)
            event.set_extra("_ecobot_expression_batch_id", result.batch_id)
            logger.info(
                "[Ecobot][聊天调度器] 已生成待发送表达 | 批次=%s | 欲望分数=%.1f",
                result.batch_id,
                result.desire.score,
            )
        else:
            logger.info(
                "[Ecobot][聊天调度器] 决定保持沉默 | 批次=%s | 原因=%s",
                result.batch_id,
                STOP_REASON_LABELS.get(
                    result.stop_reason, result.stop_reason or "正常完成"
                ),
            )
            event.stop_event()

    async def _idle_loop(self) -> None:
        while True:
            try:
                settings = self.settings_store.settings()
                await asyncio.sleep(settings["idle_poll_seconds"])
                self.refresh_scheduler.passive_interval = timedelta(
                    seconds=settings["passive_interval_seconds"]
                )
                self.refresh_scheduler.idle_interval = timedelta(
                    seconds=settings["idle_interval_seconds"]
                )
                self.bridge.apply_settings(settings)
                if not settings["enabled"]:
                    continue
                for channel_id in self.refresh_scheduler.due_idle_channels():
                    event = self._routes.get(channel_id)
                    if event is None:
                        self.refresh_scheduler.postpone_idle(
                            channel_id, self.refresh_scheduler.idle_interval
                        )
                        continue
                    stimulus = Stimulus(
                        event_id=f"idle:{channel_id}:{time.time_ns()}",
                        channel_id=channel_id,
                        user_id=str(event.get_self_id() or "ecobot"),
                        content=settings["idle_prompt"],
                        timestamp=time.time(),
                        metadata={
                            "trigger": "idle",
                            "is_wake": False,
                            "is_private": event.is_private_chat(),
                            "group_id": event.get_group_id(),
                            "platform": event.get_platform_name(),
                            "skip_affinity": True,
                            "skip_relation": True,
                        },
                    )
                    decision, result = await self.refresh_scheduler.run(
                        stimulus,
                        lambda batch_id: self.bridge.process_stimulus(
                            stimulus,
                            event=event,
                            batch_id=batch_id,
                            allow_actions=True,
                            allow_expression=settings[
                                "idle_allow_proactive_expression"
                            ],
                        ),
                        agent_id="ecobot",
                    )
                    if result is None or not result.expression:
                        continue
                    error = None
                    try:
                        sent = await self.ctx.plugin_manager.context.send_message(
                            channel_id, MessageChain().message(result.expression)
                        )
                        if not sent:
                            error = "no matching platform route"
                    except Exception as exc:
                        sent = False
                        error = f"{type(exc).__name__}: {exc}"
                        logger.exception(
                            "[Ecobot][空闲驱动] 主动消息发送失败 | 频道=%s",
                            channel_id,
                        )
                    self.bridge.mark_expression(
                        decision.batch_id or result.batch_id,
                        success=sent,
                        error=error,
                    )
                    self.qq_archive.record_outbound_message(
                        channel_id=channel_id,
                        content=result.expression,
                        batch_id=result.batch_id,
                        success=sent,
                        error=error,
                        platform_name=event.get_platform_name(),
                        group_id=event.get_group_id(),
                        target_qq_id=(
                            event.get_sender_id() if event.is_private_chat() else None
                        ),
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[Ecobot][空闲驱动] 本轮空闲检查失败")

    async def _life_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(30)
                settings = self.settings_store.settings()
                if not settings["enabled"]:
                    continue
                route_channel = next(iter(self._routes), None)
                proposal = (
                    await self.bridge.propose_autonomous_intent(
                        route_channel,
                        event=self._routes.get(route_channel),
                    )
                    if route_channel and not self.life_loop.has_active_intent()
                    else None
                )
                result = self.life_loop.tick(proposed_intent=proposal)
                logger.debug(
                    "[Ecobot][生命主循环] 时间推进 | 新意图=%s | 完成意图=%s | 世界解析=%s | 状态更新=%s",
                    result.created_intent_id or "无",
                    result.completed_intent_id or "无",
                    result.resolution_status or "未执行",
                    "是" if result.changed_state else "否",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[Ecobot][生命主循环] 本轮自治推进失败")

    async def close(self) -> None:
        self._idle_task.cancel()
        self._life_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._idle_task
        with suppress(asyncio.CancelledError):
            await self._life_task
        self.refresh_scheduler.close()
        self.anti_repeat.close()
        self.autonomous_runtime.close()
        self.qq_archive.close()
        self.settings_store.close()
