from astrbot import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from ecobot.astrbot_bridge import is_enabled
from ecobot.admin_store import EcobotAdminStore
from ecobot.qq_archive import QQArchive
from ecobot.qq_sync import QQSyncCoordinator
from ecobot2.runtime import AutonomousRuntime

from ..context import PipelineContext
from ..stage import Stage, register_stage


@register_stage
class EcobotArchiveStage(Stage):
    async def initialize(self, ctx: PipelineContext) -> None:
        database_path = f"{get_astrbot_data_path()}/ecobot/world.db"
        self.archive = QQArchive(database_path)
        self.autonomous_runtime = AutonomousRuntime(database_path)
        self.settings_store = EcobotAdminStore(database_path)
        self.sync = QQSyncCoordinator(
            self.archive,
            plugin_context=ctx.plugin_manager.context,
            admin_store=self.settings_store,
        )
        self.sync.apply_settings(self.settings_store.settings())

    async def process(self, event: AstrMessageEvent) -> None:
        if not is_enabled():
            return
        settings = self.settings_store.settings()
        self.sync.apply_settings(settings)
        if not settings["enabled"]:
            return
        raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
        if isinstance(raw_message, dict) and raw_message.get("post_type") == "notice":
            message_id = str(getattr(event.message_obj, "message_id", "") or event.trace.span_id)
            notice_event = self.autonomous_runtime.observe(
                "platform_notice",
                event_id=f"notice:{message_id}",
                channel_id=event.unified_msg_origin,
                actor_id=str(raw_message.get("operator_id") or raw_message.get("user_id") or "") or None,
                target_id=str(raw_message.get("user_id") or "") or None,
                payload={
                    "notice_type": raw_message.get("notice_type"),
                    "sub_type": raw_message.get("sub_type"),
                    "group_id": raw_message.get("group_id"),
                    "raw_notice": raw_message,
                },
            )
            target_id = str(raw_message.get("user_id") or "") or None
            operator_id = str(raw_message.get("operator_id") or "") or None
            if (
                raw_message.get("notice_type") == "group_ban"
                and target_id
                and target_id == str(event.get_self_id() or "")
            ):
                self.autonomous_runtime.appraise(
                    notice_event.event_id,
                    relevance=1.0,
                    valence=-0.9,
                    controllability=0.1,
                    agency_confidence=0.95 if operator_id else 0.0,
                    boundary_violation=0.9,
                    emotion="愤怒",
                    intensity=0.9,
                    relationship_target_id=operator_id,
                    reason="平台明确反馈当前账号被群禁言，且存在可归因的操作者"
                    if operator_id
                    else "平台明确反馈当前账号被群禁言，但没有可靠操作者信息",
                )
                self.autonomous_runtime.store.save_grievance(
                    grievance_id=f"grievance:notice:{notice_event.event_id}",
                    target_id=operator_id,
                    source_consequence_id=notice_event.event_id,
                    reason="平台明确反馈当前账号被群禁言" + ("，操作者信息明确" if operator_id else "，操作者未知"),
                    responsibility_confidence=0.95 if operator_id else 0.0,
                    intensity=0.9,
                    repair_expected=0.7,
                    evidence=[notice_event.event_id],
                )
        if self.archive.record_event(event):
            logger.debug(
                "[Ecobot][QQ事实档案] 事件已归档 | 频道=%s",
                event.unified_msg_origin,
            )
        self.sync.schedule(event)

    async def close(self) -> None:
        await self.sync.close()
        self.archive.close()
        self.autonomous_runtime.close()
        self.settings_store.close()
