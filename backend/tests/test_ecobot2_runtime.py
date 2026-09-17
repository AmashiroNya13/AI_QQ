import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot2.contracts import ActionIntent, ActionStatus
from ecobot2.runtime import AutonomousRuntime


class AutonomousRuntimeTests(unittest.TestCase):
    def test_action_receipt_and_consequence_form_a_closed_loop(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")
            runtime.set_subjective_state(location_id="bedroom", activity="reading")
            runtime.observe_message(
                event_id="message-1",
                channel_id="group:1",
                actor_id="10001",
                content="你好",
            )
            attempt = runtime.start_action(
                ActionIntent("intent-1", "send_message", "10001", {"text": "你好"})
            )
            receipt = runtime.record_receipt(
                attempt.attempt_id,
                ActionStatus.FAILED,
                error_code="muted",
                error_detail="当前账号被禁言",
            )
            consequence = runtime.observe_consequence(
                attempt.attempt_id,
                "mute_detected",
                actor_id="20002",
                confidence=0.95,
            )
            appraisal = runtime.appraise(
                consequence.consequence_id,
                relevance=1,
                valence=-1,
                controllability=0.2,
                agency_confidence=0.95,
                boundary_violation=0.8,
                emotion="愤怒",
                intensity=0.8,
                relationship_target_id="20002",
                reason="表达被明确阻断",
            )

            self.assertEqual(receipt.status, ActionStatus.FAILED)
            self.assertEqual(consequence.actor_id, "20002")
            self.assertEqual(appraisal.emotion, "愤怒")
            self.assertIsNotNone(runtime.store.subjective_state("ecobot"))
            runtime.close()

    def test_unknown_failure_does_not_require_a_person_attribution(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")
            attempt = runtime.start_action(ActionIntent("intent-1", "send_message", None))
            consequence = runtime.observe_consequence(
                attempt.attempt_id, "delivery_unknown", confidence=0.2
            )

            self.assertIsNone(consequence.actor_id)
            self.assertLess(consequence.confidence, 0.5)
            runtime.close()

    def test_intent_scene_attention_and_revision_survive_restart(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "world.db"
            runtime = AutonomousRuntime(database)
            runtime.set_subjective_state(location_id="home", activity="阅读")
            intent = ActionIntent(
                "intent-open-1",
                "start_activity",
                None,
                {"activity": "散步"},
                priority=0.8,
                reason="想换换空气",
            )
            runtime.resolve_intent(intent)
            runtime.record_attention(
                "message-1",
                channel_id="group:1",
                thread_id="group:1:active",
                addressee_id=None,
                should_reply=False,
                confidence=0.7,
                attention_cost=0.4,
                reason="群聊没有明确指向",
            )
            revision_id = runtime.review_experience(
                category="habit",
                proposal={"name": "更愿意在安静时散步"},
                evidence=["intent-open-1"],
                confidence=0.72,
            )
            self.assertGreater(revision_id, 0)
            runtime.close()

            reopened = AutonomousRuntime(database)
            self.assertEqual(reopened.store.scene("main").activity, "阅读")
            self.assertEqual(reopened.store.intentions(10)[0]["intent_id"], "intent-open-1")
            self.assertFalse(reopened.store.attention_decisions(10)[0]["should_reply"])
            self.assertEqual(reopened.store.identity_revisions(10)[0]["category"], "habit")
            reopened.close()

    def test_partial_state_update_preserves_scene_continuity(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")
            runtime.set_subjective_state(
                location_id="library",
                activity="阅读",
                focus="小说",
                mood={"平静": 0.8},
                energy=62,
            )

            updated = runtime.set_subjective_state(energy=51)

            self.assertEqual(updated.location_id, "library")
            self.assertEqual(updated.activity, "阅读")
            self.assertEqual(updated.focus, "小说")
            self.assertEqual(updated.mood, {"平静": 0.8})
            self.assertEqual(updated.energy, 51)
            runtime.close()

    def test_unknown_receipt_is_rejected_without_creating_an_event(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")

            with self.assertRaisesRegex(ValueError, "unknown action attempt"):
                runtime.record_receipt("missing", ActionStatus.SUCCEEDED)

            self.assertEqual(runtime.store.actions(), [])
            runtime.close()
