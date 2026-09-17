import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot2.life_loop import AutonomousLifeLoop
from ecobot2.runtime import AutonomousRuntime
from ecobot2.contracts import ActionIntent


class AutonomousLifeLoopTests(unittest.TestCase):
    def test_life_progresses_without_an_incoming_message(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            runtime.set_subjective_state(location_id="home", activity="idle", energy=20)
            loop = AutonomousLifeLoop(
                runtime,
                intent_proposer=lambda context: ActionIntent(
                    "intent-rest-1",
                    "start_activity",
                    None,
                    {"activity": "休息"},
                    reason="主体自行决定休息",
                ),
            )

            first = loop.tick(now=start)
            second = loop.tick(now=start + timedelta(minutes=61))

            self.assertIsNotNone(first.created_intent_id)
            self.assertEqual(second.completed_intent_id, first.created_intent_id)
            self.assertEqual(second.resolution_status, "succeeded")
            self.assertTrue(runtime.store.events(20))
            self.assertTrue(runtime.store.consequences(20))
            runtime.close()

    def test_class_does_not_forbid_leaving_but_world_reacts(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")
            runtime.register_location("school", name="学校", neighbors=("classroom", "street"))
            runtime.register_location("classroom", name="教室", neighbors=("school",))
            runtime.register_world_rule(
                "rule-class-leave",
                name="离开课堂反馈",
                trigger_action_type="go_to",
                conditions={"current_activity": "上课", "target_location_id": "school"},
                reactions=[
                    {"kind": "teacher_intervenes", "authority": "teacher"},
                    {"kind": "attendance_risk", "reason": "离开课程可能影响出勤记录"},
                ],
            )
            runtime.set_subjective_state(location_id="classroom", activity="上课")
            runtime.submit_intent(
                ActionIntent(
                    "intent-leave-class",
                    "go_to",
                    None,
                    {"location_id": "school"},
                    reason="主体决定翘课离开",
                )
            )

            result = AutonomousLifeLoop(runtime).tick()

            self.assertEqual(result.completed_intent_id, "intent-leave-class")
            kinds = {item["kind"] for item in runtime.store.consequences(20)}
            self.assertIn("teacher_intervenes", kinds)
            self.assertEqual(runtime.store.subjective_state("ecobot").location_id, "school")
            runtime.close()

    def test_message_creates_thread_belief_and_reply_obligation(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")
            runtime.observe_message(
                event_id="message-1",
                channel_id="group:1",
                actor_id="10001",
                content="@ecobot 你现在在做什么？",
                metadata={"has_at": True, "is_private": False},
            )

            self.assertEqual(len(runtime.store.threads(10)), 1)
            self.assertEqual(len(runtime.store.dialogue_acts(10)), 1)
            self.assertEqual(runtime.store.dialogue_acts(10)[0]["act_type"], "question")
            self.assertEqual(len(runtime.store.obligations(10)), 1)
            self.assertEqual(len(runtime.store.beliefs(10)), 1)
            runtime.close()
