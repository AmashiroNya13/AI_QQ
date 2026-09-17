import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ecobot.contracts import (
    ActionFeedback,
    ActionSpec,
    BatchState,
    Desire,
    Inference,
    Observation,
    Plan,
    Reflection,
    Stimulus,
)
from ecobot.heartbeat import HeartbeatKernel
from ecobot.agent_state import AgentStateStore
from ecobot.world_model import WorldModel


class ScriptedThinker:
    def __init__(self, engage: bool = True, replan: bool = False) -> None:
        self.engage = engage
        self.replan = replan
        self.reflections = 0

    async def observe(self, stimulus, world):
        return Observation("observed", (stimulus.content,))

    async def analyze_infer(self, stimulus, observation, world):
        return Inference("chat", relation_delta=0.5, confidence=0.9)

    async def desire(self, stimulus, observation, inference, world):
        return Desire(80 if self.engage else 10, self.engage, ("test",))

    async def plan(self, stimulus, observation, inference, desire, world):
        return Plan((ActionSpec("first"),), "done")

    async def reflect(self, stimulus, plan, feedback, world):
        self.reflections += 1
        if self.replan and self.reflections == 1:
            return Reflection(False, "retry", Plan((ActionSpec("second"),), "replanned"))
        return Reflection(True, "done")


def stimulus(channel: str = "channel") -> Stimulus:
    return Stimulus("event", channel, "user", "hello", 1.0)


class HeartbeatKernelTests(unittest.IsolatedAsyncioTestCase):
    async def test_silent_desire_still_commits_world_state(self) -> None:
        thinker = ScriptedThinker(engage=False)

        async def execute(action):
            raise AssertionError("silent decisions must not execute actions")

        world = WorldModel()
        result = await HeartbeatKernel(world, thinker, execute).process(stimulus())

        self.assertIsNone(result.expression)
        self.assertEqual(result.stop_reason, "silent_by_desire")
        self.assertEqual(world.snapshot("channel").relation_scores["user"], 0.5)

    async def test_idle_heartbeat_does_not_create_a_relation_to_the_bot(self) -> None:
        thinker = ScriptedThinker(engage=False)

        async def execute(action):
            raise AssertionError("silent decisions must not execute actions")

        world = WorldModel()
        idle = Stimulus(
            "idle-event",
            "channel",
            "ecobot",
            "idle prompt",
            1.0,
            {"trigger": "idle", "skip_affinity": True, "skip_relation": True},
        )
        await HeartbeatKernel(world, thinker, execute).process(idle)

        self.assertNotIn("ecobot", world.snapshot("channel").relation_scores)

    async def test_action_feedback_can_trigger_replan(self) -> None:
        thinker = ScriptedThinker(replan=True)
        calls = []

        async def execute(action):
            calls.append(action.name)
            return ActionFeedback(action, success=action.name == "second")

        result = await HeartbeatKernel(WorldModel(), thinker, execute).process(stimulus())

        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(result.expression, "replanned")
        self.assertEqual(len(result.feedback), 2)
        self.assertIn(BatchState.REFLECTING, result.state_history)

    async def test_action_budget_stops_loop(self) -> None:
        thinker = ScriptedThinker(replan=True)

        async def execute(action):
            return ActionFeedback(action, success=False)

        result = await HeartbeatKernel(
            WorldModel(), thinker, execute, max_actions=1
        ).process(stimulus())

        self.assertEqual(result.stop_reason, "action_budget_exhausted")
        self.assertEqual(len(result.feedback), 1)

    async def test_executor_error_becomes_reflectable_feedback(self) -> None:
        thinker = ScriptedThinker()

        async def execute(action):
            raise RuntimeError("offline")

        result = await HeartbeatKernel(WorldModel(), thinker, execute).process(stimulus())

        self.assertFalse(result.feedback[0].success)
        self.assertIn("offline", result.feedback[0].error)

    async def test_replanned_duplicate_action_is_suppressed(self) -> None:
        thinker = ScriptedThinker(replan=True)
        calls = []

        async def execute(action):
            calls.append(action.name)
            return ActionFeedback(action, success=False)

        async def same_reflect(stimulus, plan, feedback, world):
            thinker.reflections += 1
            if thinker.reflections == 1:
                return Reflection(False, "retry", Plan((ActionSpec("first"),), "retry"))
            return Reflection(True, "done")

        thinker.reflect = same_reflect
        result = await HeartbeatKernel(WorldModel(), thinker, execute).process(stimulus())
        self.assertEqual(calls, ["first"])
        self.assertIn("duplicate action suppressed", result.feedback[-1].error)

    async def test_same_channel_is_serialized(self) -> None:
        thinker = ScriptedThinker()
        active = 0
        max_active = 0

        async def execute(action):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return ActionFeedback(action, success=True)

        kernel = HeartbeatKernel(WorldModel(), thinker, execute)
        await asyncio.gather(
            kernel.process(stimulus("same")),
            kernel.process(Stimulus("event-2", "same", "user", "hello", 2.0)),
        )
        self.assertEqual(max_active, 1)

    async def test_scheduler_batch_id_and_state_update_are_committed(self) -> None:
        class StatefulThinker(ScriptedThinker):
            async def plan(self, stimulus, observation, inference, desire, world):
                self.assert_state = world.agent_state
                return Plan(
                    expression="working",
                    state_update={
                        "activity": "studying",
                        "behavior": "reading a book",
                        "scene": "bedroom desk",
                        "expected_duration_seconds": 600,
                    },
                )

        with TemporaryDirectory() as directory:
            store = AgentStateStore(Path(directory) / "world.db")
            thinker = StatefulThinker()

            async def execute(action):
                return ActionFeedback(action, True)

            kernel = HeartbeatKernel(
                WorldModel(), thinker, execute, agent_state_store=store, agent_id="bot"
            )
            result = await kernel.process(stimulus(), batch_id="scheduled-batch")

            self.assertEqual(result.batch_id, "scheduled-batch")
            self.assertEqual(thinker.assert_state["activity"], "idle")
            self.assertEqual(store.get("bot").activity, "studying")
            store.close()

    async def test_private_cognition_json_is_blocked(self) -> None:
        thinker = ScriptedThinker()

        async def plan(stimulus, observation, inference, desire, world):
            return Plan(expression='{"analysis":"private","expression":"hello"}')

        thinker.plan = plan

        async def execute(action):
            return ActionFeedback(action, True)

        result = await HeartbeatKernel(WorldModel(), thinker, execute).process(stimulus())
        self.assertIsNone(result.expression)
        self.assertEqual(result.stop_reason, "private_cognition_blocked")

    async def test_private_cognition_text_is_blocked(self) -> None:
        thinker = ScriptedThinker()

        async def plan(stimulus, observation, inference, desire, world):
            return Plan(expression="分析：对方在试探我的态度")

        thinker.plan = plan

        async def execute(action):
            return ActionFeedback(action, True)

        result = await HeartbeatKernel(WorldModel(), thinker, execute).process(stimulus())
        self.assertIsNone(result.expression)
        self.assertEqual(result.stop_reason, "private_cognition_blocked")

    async def test_high_fidelity_rewriter_runs_before_expression_gate(self) -> None:
        thinker = ScriptedThinker()
        gated: list[str] = []

        async def rewrite(stimulus, expression, world):
            return f"{expression}（改写）"

        async def gate(channel_id, expression):
            gated.append(expression)
            return True

        async def execute(action):
            return ActionFeedback(action, True)

        result = await HeartbeatKernel(
            WorldModel(),
            thinker,
            execute,
            expression_gate=gate,
            expression_rewriter=rewrite,
        ).process(stimulus())

        self.assertEqual(result.expression, "done（改写）")
        self.assertEqual(gated, ["done（改写）"])


if __name__ == "__main__":
    unittest.main()
