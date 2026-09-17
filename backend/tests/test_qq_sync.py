import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from ecobot.qq_archive import QQArchive
from ecobot.qq_sync import QQSyncCoordinator


class FakeBot:
    def __init__(self) -> None:
        self.calls = []

    async def call_action(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if action == "get_stranger_info":
            return {"user_id": 10001, "nickname": "Alice Profile"}
        if action == "get_group_info":
            return {
                "group_id": 20001,
                "group_name": "Synced Group",
                "member_count": 2,
                "max_member_count": 500,
            }
        if action == "get_group_member_list":
            return [
                {
                    "user_id": 10001,
                    "nickname": "Alice Profile",
                    "card": "Alice Card",
                    "role": "owner",
                },
                {
                    "user_id": 10002,
                    "nickname": "Bob",
                    "card": "",
                    "role": "member",
                },
            ]
        if action == "get_friend_list":
            return [{"user_id": 10001, "nickname": "Alice Profile", "remark": "A"}]
        if action == "get_group_list":
            return [{"group_id": 20001, "group_name": "Synced Group"}]
        if action == "get_group_msg_history":
            return {"messages": []}
        raise AssertionError(f"unexpected action: {action}")


class FakeSyncEvent:
    def __init__(self) -> None:
        self.bot = FakeBot()

    def get_platform_name(self):
        return "aiocqhttp"

    def get_sender_id(self):
        return "10001"

    def get_group_id(self):
        return "20001"

    def get_self_id(self):
        return "90001"


class FakeQZoneSource:
    def __init__(self) -> None:
        self.cursors = []

    async def fetch_new_posts(self, qq_id, cursor):
        self.cursors.append(cursor)
        return (
            [
                {
                    "post_id": "post-1",
                    "content": "hello qzone",
                    "created_at": 12,
                    "media": [],
                }
            ],
            "cursor-1",
        )


class QQSyncCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_enriches_profile_group_members_and_avatars_once(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            archive = QQArchive(path)
            downloads = []

            async def fetch_binary(url):
                downloads.append(url)
                return url.encode(), "etag", "image/jpeg"

            qzone = FakeQZoneSource()
            sync = QQSyncCoordinator(
                archive,
                binary_fetcher=fetch_binary,
                qzone_source=qzone,
            )
            event = FakeSyncEvent()
            await sync.sync_event(event)
            await sync.sync_event(event)
            archive.close()

            self.assertEqual(
                [action for action, _ in event.bot.calls].count("get_stranger_info"),
                1,
            )
            self.assertEqual(
                [action for action, _ in event.bot.calls].count("get_group_info"),
                1,
            )
            self.assertEqual(len(downloads), 3)
            self.assertEqual(qzone.cursors, [None])

            connection = sqlite3.connect(path)
            user = connection.execute(
                "SELECT current_nickname FROM qq_users WHERE qq_id = '10001'"
            ).fetchone()
            self.assertEqual(user, ("Alice Profile",))
            group = connection.execute(
                """
                SELECT current_name, current_owner_qq_id, member_count
                FROM qq_groups WHERE group_id = '20001'
                """
            ).fetchone()
            self.assertEqual(group, ("Synced Group", "10001", 2))
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM qq_user_avatar_history").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM qq_group_avatar_history").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM qq_qzone_posts").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM qq_binary_assets").fetchone()[0],
                3,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM qq_group_snapshots").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM qq_group_member_snapshots").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM qq_user_profile_snapshots").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM qq_friend_inventory_snapshots").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM qq_group_inventory_snapshots").fetchone()[0],
                1,
            )
            connection.close()


if __name__ == "__main__":
    unittest.main()
