import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from ecobot.astrbot_bridge import (
    AstrBotBehaviorBridge,
    event_to_stimulus,
    is_behavioral_message_event,
    is_enabled,
)
from ecobot.anti_repeat import AntiRepeatGuard
from ecobot.contracts import Stimulus
from ecobot2.runtime import AutonomousRuntime


class FakeEvent:
    def __init__(self, *, explicit_wake: bool = True) -> None:
        self.message_obj = SimpleNamespace(message_id="message-1")
        self.trace = SimpleNamespace(span_id="span-1")
        self.unified_msg_origin = "aiocqhttp:GroupMessage:group-1"
        self.created_at = 10.0
        self.is_at_or_wake_command = explicit_wake

    def get_platform_name(self): return "aiocqhttp"
    def get_platform_id(self): return "qq"
    def get_message_type(self): return "GroupMessage"
    def get_sender_name(self): return "Alice"
    def get_group_id(self): return "group-1"
    def is_private_chat(self): return False
    def is_wake_up(self): return True
    def is_admin(self): return False
    def get_messages(self): return []
    def get_message_outline(self): return "hello"
    def get_message_str(self): return "hello"
    def get_sender_id(self): return "user-1"


class BridgeTests(unittest.TestCase):
    def test_event_is_normalized_without_astrbot_state_leaking(self) -> None:
        stimulus = event_to_stimulus(FakeEvent())
        self.assertEqual(stimulus.event_id, "message-1")
        self.assertEqual(stimulus.channel_id, "aiocqhttp:GroupMessage:group-1")
        self.assertEqual(stimulus.user_id, "user-1")
        self.assertEqual(stimulus.content, "hello")

    def test_environment_and_notice_filters(self) -> None:
        with patch.dict(os.environ, {"ECOBOT_ENABLED": "off"}):
            self.assertFalse(is_enabled())
        event = FakeEvent()
        event.message_obj.raw_message = {"post_type": "notice", "notice_type": "input_status"}
        self.assertFalse(is_behavioral_message_event(event))


class SubjectDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_subject_decision_is_one_model_call_and_can_write_intent(self) -> None:
        calls = []

        class Provider:
            async def text_chat(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    role="assistant",
                    completion_text=(
                        '{"decision":"reply","expression":"我先回你一下",'
                        '"action_type":"go_to","arguments":{"location_id":"street"},'
                        '"reason":"想出去走走","priority":0.8,"relation_delta":0.1,'
                        '"trust_delta":0,"familiarity_delta":0.1,"confidence":1}'
                    ),
                )

        class PluginContext:
            async def get_using_provider_async(self, _): return Provider()

        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")
            bridge = AstrBotBehaviorBridge(PluginContext(), autonomous_runtime=runtime)
            result = await bridge.process_stimulus(
                Stimulus("event-1", "friend:1", "user-1", "你好", 1.0, {"is_private": True}),
                batch_id="batch-1",
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(result.expression, "我先回你一下")
            self.assertGreater(result.desire.score, 0.0)
            self.assertLess(result.desire.score, 100.0)
            self.assertEqual(runtime.store.intentions(10, status="planned")[0]["action_type"], "go_to")
            runtime.close()

    async def test_idle_action_permission_is_explicit(self) -> None:
        class Provider:
            async def text_chat(self, **kwargs):
                return SimpleNamespace(
                    role="assistant",
                    completion_text=(
                        '{"decision":"wait","expression":null,"action_type":"go_to",'
                        '"arguments":{"location_id":"street"},"reason":"测试","priority":0.5}'
                    ),
                )

        class PluginContext:
            async def get_using_provider_async(self, _): return Provider()

        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")
            bridge = AstrBotBehaviorBridge(PluginContext(), autonomous_runtime=runtime)
            await bridge.process_stimulus(
                Stimulus("idle-1", "group:1", "ecobot", "空闲", 1.0, {"trigger": "idle"}),
                allow_actions=False,
            )
            self.assertEqual(runtime.store.intentions(10, status="planned"), [])
            runtime.close()

    async def test_expression_guard_is_used_before_delivery(self) -> None:
        class Provider:
            async def text_chat(self, **kwargs):
                return SimpleNamespace(role="assistant", completion_text='{"decision":"reply","expression":"重复回复"}')

        class PluginContext:
            async def get_using_provider_async(self, _): return Provider()

        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            guard = AntiRepeatGuard(path)
            first = guard.reserve("friend:1", "重复回复")
            guard.mark(first, success=True)
            runtime = AutonomousRuntime(path)
            bridge = AstrBotBehaviorBridge(PluginContext(), autonomous_runtime=runtime, anti_repeat=guard)
            result = await bridge.process_stimulus(
                Stimulus("event-2", "friend:1", "user-1", "你好", 1.0, {"is_private": True}),
                batch_id="batch-2",
            )
            self.assertIsNone(result.expression)
            self.assertEqual(result.stop_reason, "duplicate_expression")
            runtime.close()
            guard.close()


if __name__ == "__main__":
    unittest.main()
