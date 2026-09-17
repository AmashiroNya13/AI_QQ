import unittest
from types import SimpleNamespace

from astrbot.core.pipeline.content_safety_check.stage import ContentSafetyCheckStage
from ecobot.astrbot_bridge import PASSIVE_EVENT_EXTRA


class Event:
    is_at_or_wake_command = False

    def __init__(self, passive=False):
        self.passive = passive

    def get_extra(self, name, default=None):
        return self.passive if name == PASSIVE_EVENT_EXTRA else default

    def get_message_str(self):
        return "hello"

    def get_messages(self):
        return []


class ContentSafetyPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_and_passive_events_continue_pipeline(self) -> None:
        stage = ContentSafetyCheckStage()
        stage.strategy_selector = SimpleNamespace(check=lambda text: (True, ""))
        safe_yields = [item async for item in stage.process(Event())]
        passive_yields = [item async for item in stage.process(Event(passive=True))]
        self.assertEqual(safe_yields, [None])
        self.assertEqual(passive_yields, [None])
