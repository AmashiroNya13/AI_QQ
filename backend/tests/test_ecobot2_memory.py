import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot.contracts import Inference
from ecobot2.store import AutonomousStore


class EcobotMemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = AutonomousStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_chinese_ctc_reconstruction_finds_episode_without_space_tokenization(self):
        self.store.save_memory_episode(
            source_event_id="event-1",
            channel_id="group-1",
            subject_id="user-1",
            summary="小明喜欢在晚上玩游戏",
            event_kind="message",
            tags=("游戏",),
            content={"text": "小明喜欢在晚上玩游戏"},
        )

        result = self.store.reconstruct_memory(query="晚上玩游戏", limit=4)

        self.assertEqual(result["stop_reason"], "evidence_satisfied")
        self.assertEqual(result["episodes"][0]["source_event_id"], "event-1")
        self.assertEqual(len(self.store.memory_retrievals()), 1)

    def test_persona_increment_accumulates_evidence_and_activates(self):
        kwargs = {
            "increment_id": "increment-1",
            "category": "preference",
            "statement": "更愿意帮助认真求助的人",
            "value": {"direction": "help"},
            "confidence": 0.8,
            "stability": 0.3,
            "auto_activate": True,
            "activation_confidence": 0.75,
            "activation_evidence": 2,
        }
        self.store.save_persona_increment(evidence=["event-1"], **kwargs)
        self.store.save_persona_increment(evidence=["event-2"], **kwargs)

        increments = self.store.persona_increments()

        self.assertEqual(increments[0]["status"], "active")
        self.assertEqual(increments[0]["evidence"], ["event-1", "event-2"])
        self.assertGreaterEqual(increments[0]["stability"], 0.35)

    def test_temporal_relation_is_readable_as_structured_object(self):
        self.store.save_temporal_relation(
            relation_id="relation-1",
            subject_id="person:user-1",
            predicate="appeared_in",
            object_id="channel:group-1",
            object_value={"channel_id": "group-1"},
            observed_at="2026-09-18T00:00:00+00:00",
            valid_from="2026-09-18T00:00:00+00:00",
            valid_to=None,
            confidence=1.0,
            source_event_id="event-1",
        )

        relation = self.store.temporal_relations("person:user-1")[0]

        self.assertEqual(relation["predicate"], "appeared_in")
        self.assertEqual(relation["object"]["channel_id"], "group-1")

    def test_unknown_delivery_failure_creates_uncertain_grievance(self):
        self.store.save_grievance(
            grievance_id="grievance-1",
            target_id=None,
            source_consequence_id="consequence-1",
            reason="表达未送达，责任主体尚不明确",
            responsibility_confidence=0.0,
            intensity=0.25,
            repair_expected=0.2,
            evidence=["consequence-1"],
        )

        grievance = self.store.grievances()[0]

        self.assertIsNone(grievance["target_id"])
        self.assertEqual(grievance["responsibility_confidence"], 0.0)
        self.assertEqual(grievance["repetition_count"], 1)

    def test_apology_can_repair_a_targeted_grievance_without_forcing_full_forgiveness(self):
        self.store.save_grievance(
            grievance_id="grievance-2",
            target_id="operator-1",
            source_consequence_id="consequence-2",
            reason="明确禁言",
            responsibility_confidence=0.9,
            intensity=0.8,
            repair_expected=0.7,
            evidence=["consequence-2"],
        )

        self.store.repair_grievances("operator-1", evidence="apology-1")
        grievance = self.store.grievances("operator-1")[0]

        self.assertLess(grievance["intensity"], 0.8)
        self.assertEqual(grievance["status"], "active")
        self.assertIn("apology-1", grievance["evidence"])

    def test_memory_policy_records_outcomes_and_generates_shadow_candidate(self):
        self.store.save_memory_policy(
            "ctc-active-reconstruction",
            version=1,
            config={"max_hops": 2, "max_episodes": 8},
        )
        self.store._record_memory_policy_outcome(
            "ctc-active-reconstruction", success=False, duration_ms=10, evidence_count=0
        )
        self.store._record_memory_policy_outcome(
            "ctc-active-reconstruction", success=False, duration_ms=20, evidence_count=0
        )
        self.store._record_memory_policy_outcome(
            "ctc-active-reconstruction", success=False, duration_ms=30, evidence_count=0
        )
        self.store._record_memory_policy_outcome(
            "ctc-active-reconstruction", success=False, duration_ms=40, evidence_count=0
        )
        self.store._record_memory_policy_outcome(
            "ctc-active-reconstruction", success=False, duration_ms=50, evidence_count=0
        )

        policies = self.store.memory_policies()

        parent = next(item for item in policies if item["policy_name"] == "ctc-active-reconstruction")
        self.assertEqual(parent["score"]["attempts"], 5)
        self.assertTrue(any(item["status"] == "candidate" for item in policies))

    def test_relationship_state_is_owned_by_autonomous_store(self):
        profile = self.store.apply_relationship_inference(
            "group-1",
            "user-1",
            Inference(
                "认真帮助",
                relation_delta=2.0,
                trust_delta=1.0,
                familiarity_delta=1.0,
                confidence=1.0,
                relationship_reason="帮助了主体",
            ),
            batch_id="batch-1",
        )

        self.assertAlmostEqual(profile["affinity_score"], 11.82, places=2)
        self.assertEqual(profile["trust_score"], 11.0)
        self.assertEqual(len(self.store.relationship_events("user-1")), 1)

        manual = self.store.manual_update_relationship(
            "user-1",
            {"special_level": "supreme", "reason": "管理员锁定"},
        )
        self.assertEqual(manual["stage"], "至高")

    def test_legacy_relationship_migration_maps_columns_and_repairs_corrupt_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE ecobot_affinity_profiles(
                    user_id TEXT PRIMARY KEY,
                    affinity_score REAL NOT NULL DEFAULT 0,
                    trust_score REAL NOT NULL DEFAULT 0,
                    familiarity REAL NOT NULL DEFAULT 0,
                    irritation REAL NOT NULL DEFAULT 0,
                    interaction_count INTEGER NOT NULL DEFAULT 0,
                    positive_interactions INTEGER NOT NULL DEFAULT 0,
                    negative_interactions INTEGER NOT NULL DEFAULT 0,
                    last_reason TEXT,
                    first_interaction_at TEXT NOT NULL,
                    last_interaction_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    special_level TEXT NOT NULL DEFAULT 'none',
                    special_reason TEXT,
                    special_set_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO ecobot_affinity_profiles VALUES(
                    'user-1', 100, 90, 80, 2, 61, 11, 3, '不想动',
                    '2026-09-16T00:00:00+00:00', '2026-09-18T00:00:00+00:00',
                    '2026-09-18T00:00:00+00:00', 'supreme', '管理员锁定',
                    '2026-09-17T00:00:00+00:00'
                )
                """
            )
            connection.commit()
            connection.close()

            store = AutonomousStore(path)
            store.close()

            connection = sqlite3.connect(path)
            connection.execute(
                """
                UPDATE ecobot2_relationship_states
                SET special_level = '61', special_reason = '11', special_set_at = '0',
                    interaction_count = '不想动', positive_interactions = '2026-09-16T00:00:00+00:00',
                    negative_interactions = '2026-09-18T00:00:00+00:00',
                    last_reason = '2026-09-18T00:00:00+00:00',
                    first_interaction_at = 'supreme', last_interaction_at = '管理员锁定',
                    updated_at = '2026-09-17T00:00:00+00:00'
                WHERE user_id = 'user-1'
                """
            )
            connection.commit()
            connection.close()

            repaired = AutonomousStore(path)
            profile = repaired.relationship_profile("user-1")
            repaired.close()

            self.assertEqual(profile["interaction_count"], 61)
            self.assertEqual(profile["positive_interactions"], 11)
            self.assertEqual(profile["negative_interactions"], 3)
            self.assertEqual(profile["special_level"], "supreme")
            self.assertEqual(profile["last_reason"], "不想动")


if __name__ == "__main__":
    unittest.main()
