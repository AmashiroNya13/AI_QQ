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
from ecobot.contracts import Stimulus
from ecobot.world_model import WorldModel
from ecobot.style_memory import StyleMemoryStore
from ecobot2.runtime import AutonomousRuntime


class FakeEvent:
    def __init__(self, *, explicit_wake: bool = True) -> None:
        self.message_obj = SimpleNamespace(message_id="message-1")
        self.trace = SimpleNamespace(span_id="span-1")
        self.unified_msg_origin = "aiocqhttp:GroupMessage:group-1"
        self.created_at = 10.0
        self.is_at_or_wake_command = explicit_wake

    def get_platform_name(self):
        return "aiocqhttp"

    def get_platform_id(self):
        return "qq"

    def get_message_type(self):
        return "GroupMessage"

    def get_sender_name(self):
        return "Alice"

    def get_group_id(self):
        return "group-1"

    def is_private_chat(self):
        return False

    def is_wake_up(self):
        return True

    def is_admin(self):
        return False

    def get_messages(self):
        return []

    def get_message_outline(self):
        return "hello"

    def get_message_str(self):
        return "hello"

    def get_sender_id(self):
        return "user-1"


class AstrBotBridgeTests(unittest.TestCase):
    def test_event_is_normalized_without_astrbot_state_leaking(self) -> None:
        stimulus = event_to_stimulus(FakeEvent())
        self.assertEqual(stimulus.event_id, "message-1")
        self.assertEqual(stimulus.channel_id, "aiocqhttp:GroupMessage:group-1")
        self.assertEqual(stimulus.user_id, "user-1")
        self.assertEqual(stimulus.content, "hello")
        self.assertTrue(stimulus.metadata["is_wake"])

    def test_environment_can_disable_ecobot(self) -> None:
        with patch.dict(os.environ, {"ECOBOT_ENABLED": "off"}):
            self.assertFalse(is_enabled())

    def test_generic_handler_wake_does_not_bypass_passive_coalescing(self) -> None:
        event = FakeEvent(explicit_wake=False)
        self.assertTrue(event.is_wake_up())

        stimulus = event_to_stimulus(event)

        self.assertFalse(stimulus.metadata["is_wake"])

    def test_input_status_notice_is_not_a_behavioral_message(self) -> None:
        event = FakeEvent()
        event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "input_status",
        }

        self.assertFalse(is_behavioral_message_event(event))

    def test_other_qq_notices_are_archive_only(self) -> None:
        event = FakeEvent()
        event.message_obj.raw_message = {
            "post_type": "notice",
            "notice_type": "group_admin",
            "sub_type": "set",
        }

        self.assertFalse(is_behavioral_message_event(event))

    def test_non_text_message_event_remains_behavioral(self) -> None:
        event = FakeEvent()
        event.message_obj.raw_message = {
            "post_type": "message",
            "message": [{"type": "image", "data": {"file": "image.jpg"}}],
        }

        self.assertTrue(is_behavioral_message_event(event))


class AstrBotBridgeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_autonomous_observation_handles_private_or_missing_archive_context(self) -> None:
        outputs = iter(
            [
                '{"summary":"收到私聊","facts":[]}',
                '{"intent":"聊天","emotion":"平静","relation_delta":0.1,"confidence":1}',
                '{"score":90,"should_engage":true,"reasons":["私聊"]}',
                '{"actions":[],"expression":"在","request_heartbeat":false}',
            ]
        )

        class Provider:
            async def text_chat(self, **kwargs):
                return SimpleNamespace(role="assistant", completion_text=next(outputs))

        class ToolManager:
            def get_full_tool_set(self):
                return SimpleNamespace(tools=[], get_tool=lambda name: None)

        class PluginContext:
            async def get_using_provider_async(self, umo):
                return Provider()

            def get_llm_tool_manager(self):
                return ToolManager()

        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")
            bridge = AstrBotBehaviorBridge(
                PluginContext(), WorldModel(), autonomous_runtime=runtime
            )
            stimulus = Stimulus(
                event_id="private-1",
                channel_id="aiocqhttp:FriendMessage:user-1",
                user_id="user-1",
                content="你好",
                timestamp=10.0,
                metadata={"is_private": True, "group_id": None},
            )

            result = await bridge.process_stimulus(stimulus)

            self.assertEqual(result.expression, "在")
            attention = runtime.store.attention_decisions(1)[0]
            self.assertTrue(attention["should_reply"])
            runtime.close()

    async def test_autonomous_intent_is_selected_by_the_subject_model(self) -> None:
        class Provider:
            async def text_chat(self, **kwargs):
                return SimpleNamespace(
                    role="assistant",
                    completion_text=(
                        '{"action_type":"go_to","arguments":{"location_id":"street"},'
                        '"reason":"我就是想出去走走","priority":0.83,"target_id":null}'
                    ),
                )

        class PluginContext:
            async def get_using_provider_async(self, umo):
                return Provider()

        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")
            bridge = AstrBotBehaviorBridge(
                PluginContext(), WorldModel(), autonomous_runtime=runtime
            )

            intent = await bridge.propose_autonomous_intent("group:1")

            self.assertEqual(intent.action_type, "go_to")
            self.assertEqual(intent.arguments["location_id"], "street")
            self.assertEqual(intent.reason, "我就是想出去走走")
            runtime.close()

    async def test_subjective_mode_uses_one_decision_call_for_a_message(self) -> None:
        calls = []

        class Provider:
            async def text_chat(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    role="assistant",
                    completion_text=(
                        '{"decision":"reply","expression":"我先回你一下",'
                        '"action_type":null,"arguments":{},"reason":"想回应",'
                        '"relation_delta":0.1,"trust_delta":0,"familiarity_delta":0.1,"confidence":1}'
                    ),
                )

        class PluginContext:
            async def get_using_provider_async(self, umo):
                return Provider()

        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")
            bridge = AstrBotBehaviorBridge(
                PluginContext(), WorldModel(), autonomous_runtime=runtime, subjective_mode=True
            )

            result = await bridge.process_stimulus(
                Stimulus(
                    event_id="subject-message-1",
                    channel_id="friend:1",
                    user_id="10001",
                    content="你好",
                    timestamp=1.0,
                    metadata={"is_private": True},
                ),
                batch_id="batch-subject-1",
            )

            self.assertEqual(result.expression, "我先回你一下")
            self.assertEqual(len(calls), 1)
            runtime.close()
    async def test_configured_phase_provider_is_used(self) -> None:
        session_calls = []
        phase_calls = []
        outputs = iter([
            '{"summary":"seen","facts":[]}',
            '{"intent":"chat","emotion":"calm","relation_delta":0,"confidence":1}',
            '{"score":90,"should_engage":true,"reasons":[]}',
            '{"actions":[],"expression":"routed","request_heartbeat":false}',
        ])

        class Provider:
            def __init__(self, calls):
                self.calls = calls

            async def text_chat(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(role="assistant", completion_text=next(outputs))

        session_provider = Provider(session_calls)
        phase_provider = Provider(phase_calls)

        class ToolManager:
            def get_full_tool_set(self):
                return SimpleNamespace(tools=[], get_tool=lambda name: None)

        class PluginContext:
            async def get_using_provider_async(self, umo):
                return session_provider

            def get_provider_by_id(self, provider_id):
                return phase_provider if provider_id == "phase-model" else None

            def get_llm_tool_manager(self):
                return ToolManager()

        bridge = AstrBotBehaviorBridge(PluginContext(), WorldModel())
        bridge.apply_settings({"observe_provider_id": "phase-model"})
        result = await bridge.process(FakeEvent())

        self.assertEqual(result.expression, "routed")
        self.assertEqual(len(phase_calls), 1)
        self.assertEqual(len(session_calls), 3)

    async def test_selected_provider_drives_behavior_result(self) -> None:
        outputs = iter(
            [
                '{"summary":"seen","facts":["hello"]}',
                '{"intent":"chat","emotion":"calm","relation_delta":0.5,"confidence":0.9}',
                '{"score":88,"should_engage":true,"reasons":["direct"]}',
                '{"actions":[],"expression":"Ecobot reply","request_heartbeat":false}',
            ]
        )

        class Provider:
            async def text_chat(self, **kwargs):
                return SimpleNamespace(role="assistant", completion_text=next(outputs))

        class ToolManager:
            def get_full_tool_set(self):
                return SimpleNamespace(tools=[], get_tool=lambda name: None)

        class PluginContext:
            async def get_using_provider_async(self, umo):
                return Provider()

            def get_llm_tool_manager(self):
                return ToolManager()

        with patch("ecobot.astrbot_bridge.logger.info") as log_info:
            result = await AstrBotBehaviorBridge(
                PluginContext(), WorldModel()
            ).process(FakeEvent())

        self.assertIsNotNone(result)
        self.assertEqual(result.expression, "Ecobot reply")
        self.assertEqual(result.desire.score, 88)
        calls = [call.args for call in log_info.call_args_list]
        self.assertTrue(
            any("开始调用模型" in args[0] and "观察" in args for args in calls)
        )
        self.assertTrue(any("思考结果" in args[0] for args in calls))
        self.assertTrue(any("思考结束" in args[0] for args in calls))

    async def test_high_fidelity_imitation_uses_a_distinct_rewrite_phase(self) -> None:
        outputs = iter(
            [
                '{"summary":"收到消息","facts":[]}',
                '{"intent":"闲聊","emotion":"平静","relation_delta":0.1,"confidence":1}',
                '{"score":90,"should_engage":true,"reasons":["直接消息"]}',
                '{"actions":[],"expression":"原始回复","request_heartbeat":false}',
                '{"expression":"改写后的回复啊～"}',
            ]
        )
        calls = []

        class Provider:
            async def text_chat(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(role="assistant", completion_text=next(outputs))

        class ToolManager:
            def get_full_tool_set(self):
                return SimpleNamespace(tools=[], get_tool=lambda name: None)

        class PluginContext:
            async def get_using_provider_async(self, umo):
                return Provider()

            def get_llm_tool_manager(self):
                return ToolManager()

            def get_provider_by_id(self, provider_id):
                return None

        with TemporaryDirectory() as directory:
            style_store = StyleMemoryStore(Path(directory) / "world.db")
            style_store.learn("user-1", "欸，这样啊～", source_id="qq_message:style")
            bridge = AstrBotBehaviorBridge(
                PluginContext(), WorldModel(), style_store=style_store
            )
            bridge.apply_settings(
                {
                    "identity_imitation_enabled": True,
                    "identity_imitation_user_id": "user-1",
                    "high_fidelity_imitation_enabled": True,
                    "memory_embedding_enabled": False,
                }
            )
            result = await bridge.process(FakeEvent())

            self.assertEqual(result.expression, "改写后的回复啊～")
            self.assertEqual(len(calls), 5)
            self.assertIn("阶段=style_rewrite", calls[-1]["prompt"])
            style_store.close()


if __name__ == "__main__":
    unittest.main()
