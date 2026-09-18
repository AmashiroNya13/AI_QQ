import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot.admin_store import EcobotAdminStore
from ecobot.qq_archive import QQArchive
from ecobot.refresh_scheduler import RefreshScheduler


class AdminStoreTests(unittest.TestCase):
    def test_phase_traces_respect_prompt_privacy(self) -> None:
        with TemporaryDirectory() as directory:
            store = EcobotAdminStore(Path(directory) / "world.db")
            store.update_settings({"trace_include_prompts": False})
            store.record_phase_trace(
                batch_id="batch", channel_id="channel", phase="主体决策", provider_id="provider",
                status="success", duration_ms=12, system_prompt="private system",
                input_prompt="private input", output_text='{"decision":"wait"}', error=None,
            )
            trace = store.phase_traces()[0]
            self.assertIsNone(trace["system_prompt"])
            self.assertIsNone(trace["input_prompt"])
            self.assertEqual(trace["output_text"], '{"decision":"wait"}')
            store.close()

    def test_settings_are_validated_and_persisted(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            store = EcobotAdminStore(path)
            updated = store.update_settings({"idle_interval_seconds": 120, "memory_reconstruction_hops": 3})
            self.assertEqual(updated["idle_interval_seconds"], 120)
            self.assertEqual(updated["memory_reconstruction_hops"], 3)
            with self.assertRaises(ValueError):
                store.update_settings({"idle_interval_seconds": 0})
            with self.assertRaises(ValueError):
                store.update_settings({"removed_legacy_setting": True})
            store.close()
            restored = EcobotAdminStore(path)
            self.assertEqual(restored.settings()["idle_interval_seconds"], 120)
            self.assertNotIn("identity_imitation_enabled", restored.settings())
            restored.close()

    def test_operational_views_query_qq_data(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "world.db"
            QQArchive(path).close()
            RefreshScheduler(path).close()
            connection = sqlite3.connect(path)
            with connection:
                connection.execute("INSERT INTO qq_users(qq_id, current_nickname, first_seen_at, last_seen_at) VALUES ('10001', '小明', '2026-01-01', '2026-01-02')")
                connection.execute("INSERT INTO qq_groups(group_id, current_name, first_seen_at, last_seen_at) VALUES ('20001', '测试群', '2026-01-01', '2026-01-02')")
            connection.close()
            store = EcobotAdminStore(path)
            self.assertEqual(store.users("小明")[0]["qq_id"], "10001")
            self.assertEqual(store.groups("测试")[0]["group_id"], "20001")
            store.close()


if __name__ == "__main__":
    unittest.main()
