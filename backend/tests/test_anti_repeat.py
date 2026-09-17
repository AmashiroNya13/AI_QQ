import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot.anti_repeat import AntiRepeatGuard


class AntiRepeatTests(unittest.TestCase):
    def test_exact_fuzzy_and_semantic_duplicates_are_blocked(self) -> None:
        with TemporaryDirectory() as directory:
            guard = AntiRepeatGuard(Path(directory) / "world.db")
            reservation = guard.reserve("channel", "Hello world", embedding=[1.0, 0.0])
            self.assertIsNotNone(reservation)
            guard.mark(reservation, success=True)
            self.assertIsNone(guard.reserve("channel", " hello  world "))
            self.assertIsNone(guard.reserve("channel", "Hello world!"))
            self.assertIsNone(
                guard.reserve("channel", "different words", embedding=[0.99, 0.01])
            )
            self.assertIsNotNone(
                guard.reserve("channel", "unrelated", embedding=[0.0, 1.0])
            )
            guard.close()

    def test_desire_time_grows_and_resets_after_successful_expression(self) -> None:
        with TemporaryDirectory() as directory:
            guard = AntiRepeatGuard(Path(directory) / "world.db")
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            initial = guard.desire_time_context(
                "channel",
                growth_per_hour=5,
                maximum_boost=40,
                now=start,
            )
            grown = guard.desire_time_context(
                "channel",
                growth_per_hour=5,
                maximum_boost=40,
                now=start + timedelta(hours=3),
            )
            capped = guard.desire_time_context(
                "channel",
                growth_per_hour=5,
                maximum_boost=40,
                now=start + timedelta(hours=20),
            )
            reservation = guard.reserve(
                "channel",
                "终于想说话了",
                now=start + timedelta(hours=3),
            )
            self.assertIsNotNone(reservation)
            guard.mark(reservation, success=True)
            reset = guard.desire_time_context(
                "channel",
                growth_per_hour=5,
                maximum_boost=40,
            )

            self.assertEqual(initial["time_boost"], 0)
            self.assertEqual(grown["time_boost"], 15)
            self.assertEqual(capped["time_boost"], 40)
            self.assertLess(float(reset["time_boost"]), 0.01)
            guard.close()

    def test_failed_expression_does_not_reset_desire_time(self) -> None:
        with TemporaryDirectory() as directory:
            guard = AntiRepeatGuard(Path(directory) / "world.db")
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            guard.desire_time_context(
                "channel",
                growth_per_hour=10,
                maximum_boost=40,
                now=start,
            )
            reservation = guard.reserve(
                "channel",
                "发送失败",
                now=start + timedelta(hours=2),
            )
            self.assertIsNotNone(reservation)
            guard.mark(reservation, success=False, error="network")
            context = guard.desire_time_context(
                "channel",
                growth_per_hour=10,
                maximum_boost=40,
                now=start + timedelta(hours=2),
            )

            self.assertEqual(context["time_boost"], 20)
            guard.close()

    def test_repeated_distinctive_reply_style_is_blocked(self) -> None:
        with TemporaryDirectory() as directory:
            guard = AntiRepeatGuard(Path(directory) / "world.db", style_repeat_limit=1)
            first = guard.reserve("channel", "欸，今天还行啊～")
            self.assertIsNotNone(first)
            guard.mark(first, success=True)

            self.assertIsNone(guard.reserve("channel", "欸，这个话题也不错啊～"))
            self.assertIsNotNone(guard.reserve("channel", "我先想一下，再告诉你"))
            guard.close()

    def test_legacy_expression_table_is_migrated_before_style_index_creation(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            connection = sqlite3.connect(path)
            with connection:
                connection.execute(
                    """
                    CREATE TABLE ecobot_expressions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        channel_id TEXT NOT NULL,
                        expression TEXT NOT NULL,
                        normalized_expression TEXT NOT NULL,
                        expression_hash TEXT NOT NULL,
                        embedding_json TEXT,
                        status TEXT NOT NULL,
                        batch_id TEXT,
                        created_at TEXT NOT NULL,
                        completed_at TEXT,
                        error TEXT
                    )
                    """
                )
            connection.close()

            guard = AntiRepeatGuard(path)
            columns = {
                row[1]
                for row in guard._connection.execute(
                    "PRAGMA table_info(ecobot_expressions)"
                ).fetchall()
            }
            self.assertIn("style_signature", columns)
            guard.close()
