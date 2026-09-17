import unittest
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot.admin_store import EcobotAdminStore
from ecobot.contracts import Inference
from ecobot.agent_state import AgentStateStore
from ecobot.qq_archive import QQArchive
from ecobot.refresh_scheduler import RefreshScheduler
from ecobot.world_model import WorldModel


class AdminStoreTests(unittest.TestCase):
    def test_phase_traces_respect_prompt_privacy(self) -> None:
        with TemporaryDirectory() as directory:
            store = EcobotAdminStore(Path(directory) / "world.db")
            store.update_settings({"trace_include_prompts": False})
            store.record_phase_trace(
                batch_id="batch",
                channel_id="channel",
                phase="observe",
                provider_id="provider",
                status="success",
                duration_ms=12,
                system_prompt="private system",
                input_prompt="private input",
                output_text='{"summary":"ok"}',
                error=None,
            )
            trace = store.phase_traces()[0]
            self.assertIsNone(trace["system_prompt"])
            self.assertIsNone(trace["input_prompt"])
            self.assertEqual(trace["output_text"], '{"summary":"ok"}')
            store.close()

    def test_settings_are_validated_and_persisted(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            store = EcobotAdminStore(path)
            updated = store.update_settings(
                {
                    "idle_interval_seconds": 120,
                    "desire_time_growth_enabled": True,
                    "desire_time_growth_per_hour": 7.5,
                    "desire_time_growth_max": 35,
                }
            )
            self.assertEqual(updated["idle_interval_seconds"], 120)
            self.assertEqual(updated["desire_time_growth_per_hour"], 7.5)
            self.assertEqual(updated["desire_time_growth_max"], 35)
            self.assertTrue(updated["debug_log_enabled"])
            self.assertFalse(updated["debug_log_include_prompts"])
            with self.assertRaises(ValueError):
                store.update_settings({"idle_interval_seconds": 0})
            with self.assertRaises(ValueError):
                store.update_settings({"identity_imitation_enabled": True})
            with self.assertRaises(ValueError):
                store.update_settings({"high_fidelity_imitation_enabled": True})
            store.close()
            restored = EcobotAdminStore(path)
            self.assertEqual(restored.settings()["idle_interval_seconds"], 120)
            restored.close()

    def test_operational_views_query_ecobot_and_qq_data(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            QQArchive(path).close()
            AgentStateStore(path).close()
            RefreshScheduler(path).close()
            WorldModel(database_path=path).close()
            connection = sqlite3.connect(path)
            with connection:
                connection.execute(
                    """
                    INSERT INTO qq_users(
                        qq_id, current_nickname, first_seen_at, last_seen_at
                    ) VALUES ('10001', '小明', '2026-01-01', '2026-01-02')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO qq_groups(
                        group_id, current_name, first_seen_at, last_seen_at
                    ) VALUES ('20001', '测试群', '2026-01-01', '2026-01-02')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO ecobot_world_events(
                        channel_id, revision, kind, payload_json
                    ) VALUES ('group:20001', 1, 'message', '{}')
                    """
                )
            connection.close()

            store = EcobotAdminStore(path)
            self.assertEqual(store.users("小明")[0]["qq_id"], "10001")
            self.assertEqual(store.groups("测试")[0]["group_id"], "20001")
            self.assertEqual(store.world_events()[0]["kind"], "message")
            self.assertEqual(store.messages(), [])
            self.assertEqual(store.actions(), [])
            store.close()

    def test_affinity_views_include_profiles_and_filterable_history(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            archive = QQArchive(path)
            archive.close()
            world = WorldModel(database_path=path)
            world.commit_inference(
                "group:20001",
                "10001",
                Inference(
                    "友好交流",
                    relation_delta=2,
                    confidence=1,
                    trust_delta=1,
                    familiarity_delta=1,
                    relationship_reason="认真回应并尊重边界",
                ),
                batch_id="batch-1",
            )
            world.commit_inference(
                "group:20001",
                "10002",
                Inference("普通交流", confidence=1),
                batch_id="batch-2",
            )
            world.close()

            store = EcobotAdminStore(path)
            profiles = store.affinities()
            events = store.affinity_events("10001")

            self.assertEqual({item["user_id"] for item in profiles}, {"10001", "10002"})
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["user_id"], "10001")
            self.assertEqual(events[0]["batch_id"], "batch-1")
            self.assertEqual(events[0]["reason"], "认真回应并尊重边界")
            store.close()

    def test_manual_affinity_update_is_validated_and_audited(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            WorldModel(database_path=path).close()
            store = EcobotAdminStore(path)

            updated = store.update_affinity(
                "10001",
                {
                    "affinity_score": 45,
                    "trust_score": 32,
                    "familiarity": 60,
                    "reason": "补录现实中的长期关系",
                },
            )
            event = store.affinity_events("10001")[0]

            self.assertEqual(updated["affinity_score"], 45)
            self.assertEqual(updated["trust_score"], 32)
            self.assertEqual(updated["familiarity"], 60)
            self.assertEqual(updated["stage"], "友好")
            self.assertEqual(event["channel_id"], "manual:webui")
            self.assertEqual(event["applied_affinity_delta"], 35)
            self.assertEqual(event["reason"], "补录现实中的长期关系")
            with self.assertRaises(ValueError):
                store.update_affinity("10001", {"trust_score": 101})
            with self.assertRaises(ValueError):
                store.update_affinity("10001", {"familiarity": -1})
            store.close()

    def test_special_affinity_levels_are_manual_and_audited(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            WorldModel(database_path=path).close()
            store = EcobotAdminStore(path)

            locked = store.update_affinity(
                "10001",
                {"special_level": "unforgivable", "reason": "管理员确认特殊等级"},
            )
            self.assertTrue(locked["special_locked"])
            self.assertEqual(locked["stage"], "罪大恶极")

            unlocked = store.update_affinity(
                "10001", {"special_level": "none", "reason": "管理员解除锁定"}
            )
            self.assertFalse(unlocked["special_locked"])
            self.assertEqual(unlocked["stage"], "初步认识")
            self.assertEqual(len(store.affinity_events("10001")), 2)
            store.close()
