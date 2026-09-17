import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot.core.pipeline.ecobot_behavior.stage import EcobotBehaviorStage


class FakeBehaviorEvent:
    def __init__(self, raw_message, *, content="") -> None:
        self.message_obj = SimpleNamespace(
            message_id="message-1",
            raw_message=raw_message,
        )
        self.trace = SimpleNamespace(span_id="span-1")
        self.unified_msg_origin = "default:FriendMessage:10001"
        self.created_at = 1.0
        self.is_at_or_wake_command = True
        self.content = content
        self.stopped = False

    def get_extra(self, key, default=None):
        return default

    def stop_event(self):
        self.stopped = True

    def get_platform_name(self):
        return "aiocqhttp"

    def get_platform_id(self):
        return "default"

    def get_message_type(self):
        return "FriendMessage"

    def get_sender_name(self):
        return "Alice"

    def get_group_id(self):
        return ""

    def is_private_chat(self):
        return True

    def is_wake_up(self):
        return True

    def is_admin(self):
        return False

    def get_messages(self):
        return []

    def get_message_outline(self):
        return self.content

    def get_message_str(self):
        return self.content

    def get_sender_id(self):
        return "10001"


class EcobotBehaviorStageTests(unittest.IsolatedAsyncioTestCase):
    def make_stage(self):
        stage = object.__new__(EcobotBehaviorStage)
        stage.settings_store = SimpleNamespace(settings=lambda: {"enabled": True})
        stage.bridge = SimpleNamespace(apply_settings=lambda settings: None)
        stage.refresh_scheduler = SimpleNamespace(run=AsyncMock())
        stage._routes = {}
        return stage

    async def test_input_status_stops_before_refresh_scheduler(self) -> None:
        stage = self.make_stage()
        event = FakeBehaviorEvent(
            {
                "post_type": "notice",
                "notice_type": "notify",
                "sub_type": "input_status",
            }
        )

        await stage.process(event)

        stage.refresh_scheduler.run.assert_not_awaited()
        self.assertTrue(event.stopped)

    async def test_real_private_message_reaches_refresh_scheduler(self) -> None:
        stage = self.make_stage()
        stage.refresh_scheduler.run.return_value = (
            SimpleNamespace(should_process=False, reason="coalesced"),
            None,
        )
        event = FakeBehaviorEvent(
            {"post_type": "message", "message_type": "private"},
            content="hello",
        )

        await stage.process(event)

        stage.refresh_scheduler.run.assert_awaited_once()
        self.assertFalse(event.stopped)

    async def test_non_text_message_reaches_refresh_scheduler(self) -> None:
        stage = self.make_stage()
        stage.refresh_scheduler.run.return_value = (
            SimpleNamespace(should_process=False, reason="coalesced"),
            None,
        )
        event = FakeBehaviorEvent(
            {
                "post_type": "message",
                "message_type": "private",
                "message": [{"type": "image", "data": {"file": "image.jpg"}}],
            }
        )

        await stage.process(event)

        stage.refresh_scheduler.run.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
