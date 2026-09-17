import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot.agent_state import AgentStateStore, StateConflictError


class AgentStateStoreTests(unittest.TestCase):
    def test_state_transition_is_versioned_and_records_behavior(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            store = AgentStateStore(path)
            now = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
            initial = store.get("bot", now=now)
            reading = store.transition(
                "bot",
                {
                    "activity": "reading",
                    "behavior": "reading a book at the desk",
                    "scene": "bedroom",
                    "focus": "book",
                    "expected_duration_seconds": 3600,
                    "energy_delta": -5,
                },
                trigger="plan",
                reason="decided to study",
                expected_version=initial.version,
                batch_id="batch-1",
                now=now,
            )
            self.assertEqual(reading.version, 2)
            self.assertEqual(reading.activity, "reading")
            self.assertEqual(reading.expected_end_at, now + timedelta(hours=1))
            self.assertEqual(reading.energy, 65.0)

            adjusted = store.transition(
                "bot",
                {"mood": "focused", "fatigue_delta": 2},
                trigger="observation",
                reason="settled into the activity",
                expected_version=reading.version,
                now=now + timedelta(minutes=10),
            )
            self.assertEqual(adjusted.activity, "reading")
            store.close()

            connection = sqlite3.connect(path)
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM ecobot_agent_state_history"
                ).fetchone()[0],
                3,
            )
            action = connection.execute(
                "SELECT status, description FROM ecobot_agent_actions"
            ).fetchone()
            self.assertEqual(action, ("active", "reading a book at the desk"))
            connection.close()

    def test_expired_activity_advances_to_idle(self) -> None:
        with TemporaryDirectory() as directory:
            store = AgentStateStore(Path(directory) / "world.db")
            now = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
            initial = store.get("bot", now=now)
            store.transition(
                "bot",
                {
                    "activity": "eating",
                    "behavior": "having breakfast",
                    "expected_duration_seconds": 600,
                },
                trigger="plan",
                reason="hungry",
                expected_version=initial.version,
                now=now,
            )
            state = store.advance_due("bot", now=now + timedelta(minutes=11))
            self.assertEqual(state.activity, "idle")
            self.assertIsNone(state.expected_end_at)
            store.close()

    def test_stale_version_is_rejected(self) -> None:
        with TemporaryDirectory() as directory:
            store = AgentStateStore(Path(directory) / "world.db")
            state = store.get("bot")
            with self.assertRaises(StateConflictError):
                store.transition(
                    "bot",
                    {"mood": "busy"},
                    trigger="test",
                    reason="stale update",
                    expected_version=state.version + 1,
                )
            store.close()

    def test_due_intentions_are_consumed_once(self) -> None:
        with TemporaryDirectory() as directory:
            store = AgentStateStore(Path(directory) / "world.db")
            now = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
            store.schedule_intention(
                "bot",
                activity="walk",
                behavior="take a short walk",
                not_before=now,
                reason="scheduled break",
            )
            intention = store.consume_due_intention("bot", now=now)
            self.assertEqual(intention["activity"], "walk")
            self.assertIsNone(store.consume_due_intention("bot", now=now))
            store.close()


if __name__ == "__main__":
    unittest.main()
