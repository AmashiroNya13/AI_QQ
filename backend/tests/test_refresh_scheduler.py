import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot.contracts import BatchState, BehaviorResult, Desire, Stimulus
from ecobot.refresh_scheduler import RefreshScheduler


def stimulus(event_id, *, wake=False, trigger="message"):
    return Stimulus(
        event_id,
        "group-1",
        "user-1",
        "hello",
        1.0,
        {"is_wake": wake, "trigger": trigger},
    )


class RefreshSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_passive_events_are_coalesced_but_wake_is_immediate(self) -> None:
        with TemporaryDirectory() as directory:
            scheduler = RefreshScheduler(
                Path(directory) / "world.db",
                passive_interval=timedelta(minutes=1),
            )
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)

            async def process(batch_id):
                return BehaviorResult(
                    batch_id,
                    BatchState.COMPLETED,
                    None,
                    Desire(0, False),
                    (),
                    (BatchState.WAITING, BatchState.COMPLETED),
                    "silent",
                )

            first, _ = await scheduler.run(
                stimulus("1"), process, agent_id="bot", now=now
            )
            second, _ = await scheduler.run(
                stimulus("2"), process, agent_id="bot", now=now + timedelta(seconds=1)
            )
            direct, _ = await scheduler.run(
                stimulus("3", wake=True),
                process,
                agent_id="bot",
                now=now + timedelta(seconds=2),
            )
            self.assertTrue(first.should_process)
            self.assertFalse(second.should_process)
            self.assertTrue(direct.should_process)
            self.assertEqual(direct.batch_number, 2)
            scheduler.close()

    async def test_idle_due_uses_same_batch_path(self) -> None:
        with TemporaryDirectory() as directory:
            scheduler = RefreshScheduler(
                Path(directory) / "world.db",
                idle_interval=timedelta(minutes=10),
            )
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            scheduler.decide(stimulus("first"), agent_id="bot", now=now)
            self.assertEqual(
                scheduler.due_idle_channels(now=now + timedelta(minutes=9)), []
            )
            self.assertEqual(
                scheduler.due_idle_channels(now=now + timedelta(minutes=10)),
                ["group-1"],
            )
            scheduler.close()

    async def test_duplicate_event_id_is_never_scheduled_twice(self) -> None:
        with TemporaryDirectory() as directory:
            scheduler = RefreshScheduler(Path(directory) / "world.db")
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            first = scheduler.decide(
                stimulus("same", wake=True), agent_id="bot", now=now
            )
            duplicate = scheduler.decide(
                stimulus("same", wake=True), agent_id="bot", now=now
            )
            self.assertTrue(first.should_process)
            self.assertFalse(duplicate.should_process)
            self.assertIn("duplicate", duplicate.reason)
            scheduler.close()


if __name__ == "__main__":
    unittest.main()
