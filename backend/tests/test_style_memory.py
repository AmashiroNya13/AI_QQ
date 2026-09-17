import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot.style_memory import StyleMemoryStore, style_signature


class StyleMemoryStoreTests(unittest.TestCase):
    def test_style_features_and_vector_context_are_traceable(self) -> None:
        with TemporaryDirectory() as directory:
            store = StyleMemoryStore(Path(directory) / "world.db")
            store.learn(
                "10001",
                "欸，今天也挺有意思啊～",
                source_id="qq_message:1",
                embedding=[1.0, 0.0],
            )
            store.learn(
                "10001",
                "嗯？你怎么会这么想呢",
                source_id="qq_message:2",
                embedding=[0.0, 1.0],
            )

            context = store.context(
                "10001", "你怎么想", query_embedding=[0.0, 1.0], limit=2
            )

            self.assertEqual(context["sample_count"], 2)
            self.assertIn("欸", context["profile"]["common_openings"])
            self.assertEqual(context["references"][0]["content"], "嗯？你怎么会这么想呢")
            self.assertTrue(style_signature("欸，今天也挺有意思啊～"))
            store.close()

    def test_archive_backfill_does_not_duplicate_live_samples(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            store = StyleMemoryStore(path)
            with store._connection:
                store._connection.execute(
                    """
                    CREATE TABLE qq_messages(
                        id INTEGER PRIMARY KEY, group_id TEXT, sender_qq_id TEXT,
                        content_text TEXT
                    )
                    """
                )
                store._connection.execute(
                    "INSERT INTO qq_messages VALUES (1, '20001', '10001', '哼，才不是呢')"
                )
            store.learn("10001", "哼，才不是呢", source_id="qq_message:1")

            self.assertEqual(store.backfill_from_archive("10001"), 0)
            self.assertEqual(store.profiles()[0]["sample_count"], 1)
            store.close()
