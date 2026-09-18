import unittest

from ecobot.contracts import BatchState, BehaviorResult, Desire, Inference, Stimulus


class ContractTests(unittest.TestCase):
    def test_stimulus_rejects_empty_identity(self) -> None:
        with self.assertRaises(ValueError):
            Stimulus("", "channel", "user", "hello", 1.0)

    def test_stimulus_metadata_is_immutable(self) -> None:
        stimulus = Stimulus("event", "channel", "user", "hello", 1.0, {"at": True})
        with self.assertRaises(TypeError):
            stimulus.metadata["at"] = False

    def test_inference_confidence_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            Inference(intent="chat", confidence=1.1)

    def test_subject_result_keeps_single_decision_state(self) -> None:
        result = BehaviorResult("batch", BatchState.COMPLETED, "ok", Desire(100, True))
        self.assertEqual(result.state.value, "completed")


if __name__ == "__main__":
    unittest.main()
