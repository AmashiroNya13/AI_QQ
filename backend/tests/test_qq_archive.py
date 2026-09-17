import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from ecobot.qq_archive import QQArchive


class FakeQQEvent:
    def __init__(
        self,
        *,
        message_id="message-1",
        group_name="Test Group",
        nickname="Alice",
        card="Group Alice",
        role="member",
        post_type="message",
        notice_type=None,
        sub_type="normal",
    ) -> None:
        raw = {
            "post_type": post_type,
            "self_id": 90001,
            "user_id": 10001,
            "group_id": 20001,
            "group_name": group_name,
            "message_id": message_id,
            "message_type": "group",
            "sub_type": sub_type,
            "time": 100,
            "sender": {
                "user_id": 10001,
                "nickname": nickname,
                "card": card,
                "role": role,
                "title": "title",
            },
            "message": [
                {"type": "text", "data": {"text": "hello"}},
                {"type": "at", "data": {"qq": "10002"}},
            ],
        }
        if notice_type:
            raw["notice_type"] = notice_type
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            self_id="90001",
            timestamp=100,
            raw_message=raw,
            group=SimpleNamespace(group_name=group_name),
        )

    def get_platform_name(self):
        return "aiocqhttp"

    def get_sender_id(self):
        return str(self.message_obj.raw_message["user_id"])

    def get_sender_name(self):
        sender = self.message_obj.raw_message["sender"]
        return sender["card"] or sender["nickname"]

    def get_group_id(self):
        return str(self.message_obj.raw_message.get("group_id") or "")

    def get_message_type(self):
        return "GroupMessage"

    def get_message_str(self):
        return "hello"

    def get_message_outline(self):
        return "hello [At:10002]"


class QQArchiveTests(unittest.TestCase):
    def test_extended_archive_search_inventory_media_and_membership_episodes(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            archive = QQArchive(path)
            event = FakeQQEvent(message_id="message-media")
            event.message_obj.raw_message["message"].append(
                {"type": "image", "data": {"url": "https://example/image.jpg"}}
            )
            archive.record_event(event)
            leave = FakeQQEvent(
                message_id="leave", post_type="notice", notice_type="group_decrease"
            )
            archive.record_event(leave)
            archive.record_event(FakeQQEvent(message_id="rejoin"))
            archive.record_friend_inventory(
                "90001", [{"user_id": "10001", "nickname": "Alice", "remark": "A"}]
            )
            archive.record_group_inventory(
                "90001", [{"group_id": "20001", "group_name": "Test Group"}]
            )
            archive.record_outbound_message(
                channel_id="aiocqhttp:GroupMessage:20001",
                content="outbound",
                batch_id="batch-1",
                success=False,
                error="offline",
                group_id="20001",
            )
            pending = archive.pending_message_media("message-media")
            self.assertEqual(len(pending), 1)
            content_hash = archive.record_binary_asset(
                b"image", media_type="qq_message_image", mime_type="image/jpeg"
            )
            archive.record_message_media(
                message_row_id=pending[0]["message_row_id"],
                position=pending[0]["position"],
                media_type="image",
                source_url=pending[0]["source_url"],
                source_file=None,
                content_hash=content_hash,
                success=True,
            )
            self.assertTrue(
                archive.record_raw_onebot_message(
                    {
                        "post_type": "message",
                        "self_id": 90001,
                        "user_id": 10002,
                        "group_id": 20001,
                        "message_id": "historic-1",
                        "message_type": "group",
                        "time": 50,
                        "sender": {"nickname": "Bob", "role": "member"},
                        "message": [{"type": "text", "data": {"text": "historic phrase"}}],
                    }
                )
            )
            found = archive.search_messages("historic", group_id="20001")
            self.assertEqual(found[0]["platform_message_id"], "historic-1")
            archive.close()

            connection = sqlite3.connect(path)
            episodes = connection.execute(
                """
                SELECT joined_at, left_at FROM qq_group_membership_episodes
                WHERE group_id = '20001' AND qq_id = '10001' ORDER BY id
                """
            ).fetchall()
            self.assertEqual(len(episodes), 2)
            self.assertIsNotNone(episodes[0][1])
            self.assertIsNone(episodes[1][1])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM qq_notice_facts").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM qq_friend_inventory_snapshots").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT fetch_status FROM qq_message_media").fetchone()[0], "stored")
            self.assertEqual(connection.execute("SELECT status FROM qq_outbound_messages").fetchone()[0], "failed")
            connection.close()

    def test_relationship_evidence_and_periodic_memory_are_bidirectional(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            archive = QQArchive(path)
            archive.record_event(FakeQQEvent(message_id="message-1"))

            response = FakeQQEvent(message_id="message-2")
            raw = response.message_obj.raw_message
            raw["user_id"] = 10002
            raw["time"] = 120
            raw["sender"] = {
                "user_id": 10002,
                "nickname": "Bob",
                "card": "Group Bob",
                "role": "member",
            }
            raw["message"] = [
                {"type": "text", "data": {"text": "response"}},
                {"type": "at", "data": {"qq": "10001"}},
            ]
            archive.record_event(response)

            evaluations = archive.evaluate_due_relationships(
                minimum_new_evidence=1
            )
            self.assertEqual(len(evaluations), 1)
            self.assertGreater(evaluations[0]["reciprocity"], 0.0)
            self.assertEqual(archive.evaluate_due_relationships(minimum_new_evidence=1), [])
            archive.close()

            connection = sqlite3.connect(path)
            relation = connection.execute(
                """
                SELECT person_a_qq_id, person_b_qq_id, evidence_count,
                       current_relation_type, last_evaluated_at
                FROM qq_relationships
                """
            ).fetchone()
            self.assertEqual(relation[:4], ("10001", "10002", 3, "recurring_interaction"))
            self.assertIsNotNone(relation[4])
            evidence_types = {
                row[0]
                for row in connection.execute(
                    "SELECT evidence_type FROM qq_relationship_evidence"
                )
            }
            self.assertEqual(evidence_types, {"mention", "adjacent_turn"})
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM qq_relationship_evaluations"
                ).fetchone()[0],
                1,
            )
            memory = connection.execute(
                """
                SELECT memory_type, superseded_at FROM qq_relationship_memories
                """
            ).fetchone()
            self.assertEqual(memory, ("periodic_assessment", None))
            connection.close()

    def test_message_archive_tracks_identity_group_and_changes(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            archive = QQArchive(path)
            self.assertTrue(archive.record_event(FakeQQEvent()))
            self.assertFalse(archive.record_event(FakeQQEvent()))
            self.assertTrue(
                archive.record_event(
                    FakeQQEvent(
                        message_id="message-2",
                        group_name="Renamed Group",
                        nickname="Alice New",
                        card="New Card",
                        role="admin",
                    )
                )
            )
            archive.close()

            connection = sqlite3.connect(path)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM qq_messages").fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT current_nickname FROM qq_users WHERE qq_id = '10001'"
                ).fetchone()[0],
                "Alice New",
            )
            membership = connection.execute(
                """
                SELECT current_group_card, current_role
                FROM qq_group_memberships
                WHERE group_id = '20001' AND qq_id = '10001'
                """
            ).fetchone()
            self.assertEqual(membership, ("New Card", "admin"))
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM qq_user_nickname_history"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM qq_group_member_name_history"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM qq_group_role_history"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM qq_group_name_history"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM qq_message_segments"
                ).fetchone()[0],
                4,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT mentioned_qq_id FROM qq_message_mentions"
                ).fetchone()[0],
                "10002",
            )
            connection.close()

    def test_model_context_contains_timestamped_identity_group_and_role_history(self) -> None:
        with TemporaryDirectory() as directory:
            archive = QQArchive(Path(directory) / "world.db")
            archive.record_event(FakeQQEvent())
            archive.record_event(
                FakeQQEvent(message_id="message-2", card="New Card", role="admin")
            )
            context = archive.model_context("10001", group_id="20001")
            archive.close()

        self.assertEqual(context["person"]["qq_id"], "10001")
        self.assertEqual(context["group"]["name"], "Test Group")
        self.assertEqual(context["membership"]["role"], "admin")
        self.assertEqual(context["membership"]["group_card"], "New Card")
        self.assertTrue(context["membership"]["role_history"][0]["valid_from"])
        self.assertIn("assembled_at", context)

    def test_notice_records_role_card_departure_and_recall(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            archive = QQArchive(path)
            archive.record_event(FakeQQEvent())

            admin_notice = FakeQQEvent(
                message_id="notice-1",
                post_type="notice",
                notice_type="group_admin",
                sub_type="set",
            )
            archive.record_event(admin_notice)

            card_notice = FakeQQEvent(
                message_id="notice-2",
                post_type="notice",
                notice_type="group_card",
            )
            card_notice.message_obj.raw_message["card_new"] = "Notice Card"
            archive.record_event(card_notice)

            recall_notice = FakeQQEvent(
                message_id="notice-3",
                post_type="notice",
                notice_type="group_recall",
            )
            recall_notice.message_obj.raw_message["message_id"] = "message-1"
            archive.record_event(recall_notice)

            leave_notice = FakeQQEvent(
                message_id="notice-4",
                post_type="notice",
                notice_type="group_decrease",
            )
            archive.record_event(leave_notice)
            archive.close()

            connection = sqlite3.connect(path)
            row = connection.execute(
                """
                SELECT current_group_card, current_role, left_at
                FROM qq_group_memberships
                WHERE group_id = '20001' AND qq_id = '10001'
                """
            ).fetchone()
            self.assertEqual(row[0], "Notice Card")
            self.assertEqual(row[1], "admin")
            self.assertIsNotNone(row[2])
            recalled_at = connection.execute(
                "SELECT recalled_at FROM qq_messages WHERE platform_message_id = 'message-1'"
            ).fetchone()[0]
            self.assertIsNotNone(recalled_at)
            connection.close()

    def test_snapshots_avatars_and_qzone_posts_have_stable_storage(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            archive = QQArchive(path)
            archive.record_group_snapshot(
                {
                    "group_id": "20001",
                    "group_name": "Group",
                    "member_count": 1,
                    "max_member_count": 200,
                },
                [
                    {
                        "user_id": "10001",
                        "nickname": "Alice",
                        "card": "A",
                        "role": "owner",
                        "level": "10",
                        "join_time": 1,
                        "last_sent_time": 2,
                    }
                ],
                bot_qq_id="90001",
            )
            self.assertTrue(
                archive.record_user_avatar(
                    "10001", "https://example/avatar", content_hash="hash-1"
                )
            )
            self.assertFalse(
                archive.record_user_avatar(
                    "10001", "https://example/avatar", content_hash="hash-1"
                )
            )
            self.assertTrue(
                archive.record_user_avatar(
                    "10001", "https://example/avatar", content_hash="hash-2"
                )
            )
            archive.record_group_avatar(
                "20001", "https://example/group-avatar", content_hash="group-hash"
            )
            archive.record_qzone_post(
                "10001",
                "post-1",
                "first post",
                created_at=10,
                media=[{"type": "image", "url": "https://example/image"}],
                raw={"tid": "post-1"},
            )
            archive.record_qzone_post(
                "10001", "post-1", "edited post", created_at=10
            )
            archive.close()

            connection = sqlite3.connect(path)
            group = connection.execute(
                """
                SELECT current_owner_qq_id, member_count, max_member_count
                FROM qq_groups WHERE group_id = '20001'
                """
            ).fetchone()
            self.assertEqual(group, ("10001", 1, 200))
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM qq_user_avatar_history"
                ).fetchone()[0],
                2,
            )
            post = connection.execute(
                "SELECT content_text FROM qq_qzone_posts WHERE post_id = 'post-1'"
            ).fetchone()[0]
            self.assertEqual(post, "edited post")
            connection.close()


if __name__ == "__main__":
    unittest.main()
