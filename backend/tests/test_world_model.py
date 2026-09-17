import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot.contracts import Inference, Stimulus
from ecobot.world_model import WorldModel


class WorldModelTests(unittest.TestCase):
    def test_events_are_bounded_and_revision_increases(self) -> None:
        world = WorldModel(max_events_per_channel=2)
        for index in range(3):
            world.record_stimulus(
                Stimulus(str(index), "channel", "user", "hello", float(index))
            )
        snapshot = world.snapshot("channel")
        self.assertEqual(snapshot.revision, 3)
        self.assertEqual(len(snapshot.recent_events), 2)

    def test_relation_score_is_clamped(self) -> None:
        world = WorldModel()
        score = world.commit_inference(
            "channel", "user", Inference("chat", relation_delta=150)
        )
        self.assertEqual(score, 100.0)

    def test_events_and_relations_survive_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            world = WorldModel(database_path=path)
            world.record_stimulus(Stimulus("1", "channel", "user", "hello", 1.0))
            world.commit_inference(
                "channel", "user", Inference("chat", relation_delta=2.5)
            )
            world.close()

            restored = WorldModel(database_path=path)
            snapshot = restored.snapshot("channel")
            self.assertEqual(snapshot.revision, 2)
            self.assertEqual(snapshot.relation_scores["user"], 2.5)
            self.assertEqual([event.kind for event in snapshot.recent_events], ["stimulus", "inference"])
            restored.close()

    def test_persistent_event_window_is_bounded(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            world = WorldModel(max_events_per_channel=2, database_path=path)
            for index in range(3):
                world.record_stimulus(
                    Stimulus(str(index), "channel", "user", "hello", float(index))
                )
            world.close()

            restored = WorldModel(max_events_per_channel=2, database_path=path)
            snapshot = restored.snapshot("channel")
            self.assertEqual(snapshot.revision, 3)
            self.assertEqual(len(snapshot.recent_events), 2)
            restored.close()

    def test_affinity_is_shared_across_channels_and_added_to_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            world = WorldModel(database_path=Path(directory) / "world.db")
            world.commit_inference(
                "group:one",
                "10001",
                Inference(
                    "友好交流",
                    relation_delta=4,
                    confidence=1,
                    trust_delta=2,
                    familiarity_delta=1,
                    relationship_reason="主动提供了可靠帮助",
                ),
                batch_id="batch-1",
            )

            profile = world.snapshot(
                "group:two", focus_user_id="10001"
            ).affinity

            self.assertEqual(profile["affinity_score"], 13.64)
            self.assertEqual(profile["trust_score"], 12.0)
            self.assertEqual(profile["familiarity"], 1.0)
            self.assertEqual(profile["interaction_count"], 1)
            self.assertEqual(profile["last_reason"], "主动提供了可靠帮助")
            self.assertIn("response_guidance", profile)
            world.close()

    def test_affinity_limits_negative_changes_more_loosely_than_positive(self) -> None:
        with TemporaryDirectory() as directory:
            world = WorldModel(database_path=Path(directory) / "world.db")
            world.configure_affinity(
                enabled=True,
                positive_step_limit=3,
                negative_step_limit=6,
                irritation_half_life_hours=12,
            )
            world.commit_inference(
                "group:one",
                "10001",
                Inference("夸奖", relation_delta=100, confidence=1),
            )
            self.assertEqual(world.affinity_profile("10001")["affinity_score"], 13.0)

            world.commit_inference(
                "group:two",
                "10001",
                Inference("严重冒犯", relation_delta=-100, confidence=1),
            )
            self.assertEqual(world.affinity_profile("10001")["affinity_score"], 7.0)
            world.close()

    def test_extreme_scores_dampen_repeated_changes(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            world = WorldModel(database_path=path)
            world.commit_inference(
                "group:one",
                "10001",
                Inference("友好", relation_delta=3, confidence=1),
            )
            first = world.affinity_profile("10001")["affinity_score"]
            world.commit_inference(
                "group:one",
                "10001",
                Inference("再次友好", relation_delta=3, confidence=1),
            )
            second = world.affinity_profile("10001")["affinity_score"]

            self.assertLess(second - first, first)
            world.close()

    def test_irritation_decays_by_half_life_and_events_are_traceable(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            world = WorldModel(database_path=path)
            start = datetime(2026, 1, 1, tzinfo=timezone.utc)
            world.commit_inference(
                "group:one",
                "10001",
                Inference(
                    "冒犯",
                    relation_delta=-3,
                    confidence=1,
                    relationship_reason="持续使用侮辱性表达",
                ),
                batch_id="batch-negative",
                occurred_at=start,
            )
            immediate = world.affinity_profile("10001", now=start)
            decayed = world.affinity_profile(
                "10001", now=start + timedelta(hours=12)
            )

            self.assertGreater(immediate["irritation"], 0)
            self.assertAlmostEqual(
                decayed["irritation"], immediate["irritation"] / 2, places=2
            )
            world.close()

            connection = sqlite3.connect(path)
            event = connection.execute(
                """
                SELECT batch_id, reason, previous_stage, new_stage
                FROM ecobot_affinity_events
                """
            ).fetchone()
            connection.close()
            self.assertEqual(event[0], "batch-negative")
            self.assertEqual(event[1], "持续使用侮辱性表达")
            self.assertEqual(event[2], "初步认识")
            self.assertEqual(event[3], "陌生")

    def test_special_level_blocks_automatic_affinity_changes(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            world = WorldModel(database_path=path)
            with world._connection:
                world._connection.execute(
                    """
                    INSERT INTO ecobot_affinity_profiles(
                        user_id, affinity_score, trust_score, familiarity, irritation,
                        special_level, interaction_count, positive_interactions,
                        negative_interactions, first_interaction_at,
                        last_interaction_at, updated_at
                    ) VALUES ('10001', 40, 40, 30, 0, 'supreme', 0, 0, 0, ?, ?, ?)
                    """,
                    ("2026-01-01", "2026-01-01", "2026-01-01"),
                )

            world.commit_inference(
                "group:one",
                "10001",
                Inference("严重冒犯", relation_delta=-20, confidence=1),
            )
            profile = world.affinity_profile("10001")
            event = world._connection.execute(
                "SELECT raw_affinity_delta, applied_affinity_delta FROM ecobot_affinity_events"
            ).fetchone()

            self.assertEqual(profile["stage"], "至高")
            self.assertEqual(profile["affinity_score"], 40)
            self.assertEqual(event, (-20, 0))
            world.close()

    def test_every_automatic_interaction_applies_at_least_point_one(self) -> None:
        with TemporaryDirectory() as directory:
            world = WorldModel(database_path=Path(directory) / "world.db")
            world.commit_inference(
                "group:one", "10001", Inference("普通交流", relation_delta=0, confidence=0)
            )
            self.assertEqual(world.affinity_profile("10001")["affinity_score"], 10.1)
            world.close()

    def test_maximum_normal_affinity_is_not_the_manual_supreme_level(self) -> None:
        self.assertEqual(WorldModel._affinity_stage(100, 100, 100), "核心关系")


if __name__ == "__main__":
    unittest.main()
