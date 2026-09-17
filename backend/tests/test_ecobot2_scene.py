import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot2.contracts import ActionIntent
from ecobot2.runtime import AutonomousRuntime


class SceneResolutionTests(unittest.TestCase):
    def test_open_intent_is_resolved_by_world_state(self) -> None:
        with TemporaryDirectory() as directory:
            runtime = AutonomousRuntime(Path(directory) / "world.db")
            runtime.set_subjective_state(location_id="home")

            reachable = runtime.resolve_intent(
                ActionIntent("move-1", "go_to", None, {"location_id": "library"})
            )
            unknown = runtime.resolve_intent(
                ActionIntent("move-2", "go_to", None, {"location_id": "moon"})
            )

            self.assertEqual(reachable.status, "succeeded")
            self.assertEqual(unknown.status, "needs_preparation")
            self.assertTrue(runtime.store.scene_expansion_proposals(10))
            runtime.close()
