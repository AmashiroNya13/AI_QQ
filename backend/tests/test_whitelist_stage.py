import unittest
from types import SimpleNamespace

from astrbot.core.pipeline.whitelist_check.stage import WhitelistCheckStage
from astrbot.core.platform.message_type import MessageType


class FakeEvent:
    def __init__(self, *, sender_id="user", group_id=None, origin="default:FriendMessage:user"):
        self.unified_msg_origin = origin
        self.role = "member"
        self.stopped = False
        self._sender_id = sender_id
        self._group_id = group_id

    def get_extra(self, key, default=None):
        return default

    def get_platform_name(self):
        return "aiocqhttp"

    def get_message_type(self):
        return MessageType.GROUP_MESSAGE if self._group_id else MessageType.FRIEND_MESSAGE

    def get_group_id(self):
        return self._group_id

    def get_sender_id(self):
        return self._sender_id

    def stop_event(self):
        self.stopped = True


def context(whitelist):
    return SimpleNamespace(
        astrbot_config={
            "platform_settings": {
                "enable_id_white_list": True,
                "id_whitelist": whitelist,
                "wl_ignore_admin_on_group": False,
                "wl_ignore_admin_on_friend": False,
                "id_whitelist_log": False,
            }
        }
    )


class WhitelistStageTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_sender_qq_is_allowed(self):
        stage = WhitelistCheckStage()
        await stage.initialize(context(["1419356795"]))
        event = FakeEvent(sender_id="1419356795")

        await stage.process(event)

        self.assertFalse(event.stopped)

    async def test_group_id_is_allowed(self):
        stage = WhitelistCheckStage()
        await stage.initialize(context(["123456"]))
        event = FakeEvent(sender_id="user", group_id="123456", origin="default:GroupMessage:123456")

        await stage.process(event)

        self.assertFalse(event.stopped)

    async def test_unlisted_private_sender_is_stopped(self):
        stage = WhitelistCheckStage()
        await stage.initialize(context(["allowed"]))
        event = FakeEvent(sender_id="blocked")

        await stage.process(event)

        self.assertTrue(event.stopped)
