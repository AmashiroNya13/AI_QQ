import unittest

from ecobot.contracts import ActionFeedback, ActionSpec, Desire, Inference, Observation, Stimulus
from ecobot.model_thinker import ModelBehaviorThinker, StructuredOutputError, parse_json_object
from ecobot.world_model import WorldModel, WorldSnapshot


class JsonParsingTests(unittest.TestCase):
    def test_parses_fenced_object(self) -> None:
        self.assertEqual(parse_json_object('```json\n{"ok": true}\n```'), {"ok": True})

    def test_rejects_non_object(self) -> None:
        with self.assertRaises(StructuredOutputError):
            parse_json_object("[1, 2]")


class ModelThinkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_omits_repeated_full_qq_context(self) -> None:
        prompts = []

        async def complete(system: str, prompt: str) -> str:
            prompts.append(prompt)
            return '{"summary":"seen","facts":[]}'

        thinker = ModelBehaviorThinker(complete)
        huge_context = {
            "profile": {"nickname": "Alice", "richBuffer": {str(i): i for i in range(500)}},
            "raw_event_json": "x" * 20000,
        }
        stimulus = Stimulus(
            "event",
            "channel",
            "user",
            "hello",
            1.0,
            {"is_wake": False, "qq_context": huge_context},
        )
        world_model = WorldModel()
        world_model.record_stimulus(stimulus)
        world = world_model.snapshot("channel", social_context=huge_context)

        await thinker.observe(stimulus, world)

        self.assertEqual(len(prompts), 1)
        self.assertNotIn("richBuffer", prompts[0])
        self.assertNotIn("raw_event_json", prompts[0])
        self.assertLess(len(prompts[0]), 5000)

    async def test_disabled_phases_use_deterministic_fallbacks(self) -> None:
        async def complete(system: str, prompt: str) -> str:
            raise AssertionError("disabled phases must not call a provider")

        thinker = ModelBehaviorThinker(
            complete,
            phase_enabled={
                "observe": False,
                "analyze_infer": False,
                "desire": False,
                "plan": False,
                "reflect": False,
            },
        )
        stimulus = Stimulus("event", "channel", "user", "hello", 1.0)
        world = WorldModel().snapshot("channel")
        observation = await thinker.observe(stimulus, world)
        inference = await thinker.analyze_infer(stimulus, observation, world)
        desire = await thinker.desire(stimulus, observation, inference, world)
        plan = await thinker.plan(stimulus, observation, inference, desire, world)
        reflection = await thinker.reflect(stimulus, plan, (), world)

        self.assertEqual(observation.summary, "hello")
        self.assertEqual(inference.intent, "unknown")
        self.assertTrue(desire.should_engage)
        self.assertEqual(plan.actions, ())
        self.assertTrue(reflection.satisfied)

    async def test_desire_threshold_can_suppress_a_positive_decision(self) -> None:
        async def complete(system: str, prompt: str) -> str:
            return '{"score":49,"should_engage":true,"reasons":["weak"]}'

        thinker = ModelBehaviorThinker(complete, desire_threshold=50)
        stimulus = Stimulus("event", "channel", "user", "hello", 1.0)
        observation = Observation("hello", ("hello",))
        result = await thinker.desire(
            stimulus,
            observation,
            Inference("chat", "neutral", 0, 0.5),
            WorldModel().snapshot("channel"),
        )
        self.assertFalse(result.should_engage)

    async def test_fractional_desire_score_is_normalized_to_percentage(self) -> None:
        async def complete(system: str, prompt: str) -> str:
            return '{"score":0.75,"should_engage":true,"reasons":["direct"]}'

        thinker = ModelBehaviorThinker(complete, desire_threshold=50)
        result = await thinker.desire(
            Stimulus("event", "channel", "user", "hello", 1.0),
            Observation("hello", ("hello",)),
            Inference("chat", "neutral", 0, 0.5),
            WorldModel().snapshot("channel"),
        )

        self.assertEqual(result.score, 75)
        self.assertTrue(result.should_engage)
        self.assertTrue(any("兼容旧量纲" in reason for reason in result.reasons))

    async def test_percentage_desire_score_is_not_rescaled(self) -> None:
        async def complete(system: str, prompt: str) -> str:
            return '{"score":75,"should_engage":true,"reasons":["direct"]}'

        thinker = ModelBehaviorThinker(complete, desire_threshold=50)
        result = await thinker.desire(
            Stimulus("event", "channel", "user", "hello", 1.0),
            Observation("hello", ("hello",)),
            Inference("chat", "neutral", 0, 0.5),
            WorldModel().snapshot("channel"),
        )

        self.assertEqual(result.score, 75)
        self.assertTrue(result.should_engage)

    async def test_deep_trust_direct_message_gets_participation_guard(self) -> None:
        async def complete(system: str, prompt: str) -> str:
            return '{"score":0.25,"should_engage":false,"silence_reason":"none","reasons":["uncertain"]}'

        thinker = ModelBehaviorThinker(complete, desire_threshold=50)
        world = WorldSnapshot(
            channel_id="channel",
            revision=1,
            relation_scores={},
            recent_events=(),
            affinity={"stage": "深度信赖", "irritation": 0},
        )
        result = await thinker.desire(
            Stimulus(
                "event",
                "channel",
                "user",
                "我喜欢你",
                1.0,
                {"is_private": True},
            ),
            Observation("对方表达喜欢", ("对方说我喜欢你",)),
            Inference("表达喜欢", "开心", 0, 0.9),
            world,
        )

        self.assertEqual(result.score, 70)
        self.assertTrue(result.should_engage)
        self.assertTrue(any("参与保障" in reason for reason in result.reasons))

    async def test_explicit_silence_request_keeps_deep_trust_message_silent(self) -> None:
        async def complete(system: str, prompt: str) -> str:
            return '{"score":10,"should_engage":false,"silence_reason":"explicit_silence_request","reasons":["对方要求安静"]}'

        thinker = ModelBehaviorThinker(complete, desire_threshold=50)
        world = WorldSnapshot(
            channel_id="channel",
            revision=1,
            relation_scores={},
            recent_events=(),
            affinity={"stage": "深度信赖", "irritation": 0},
        )
        result = await thinker.desire(
            Stimulus("event", "channel", "user", "先别回复", 1.0, {"is_private": True}),
            Observation("对方要求暂不回复", ("对方说先别回复",)),
            Inference("要求安静", "平静", 0, 0.9),
            world,
        )

        self.assertEqual(result.score, 10)
        self.assertFalse(result.should_engage)

    async def test_time_desire_can_turn_hesitation_into_participation(self) -> None:
        async def complete(system: str, prompt: str) -> str:
            self.assertIn('"time_boost":30', prompt)
            return '{"score":25,"should_engage":false,"silence_reason":"none","reasons":["有点犹豫"]}'

        thinker = ModelBehaviorThinker(
            complete,
            desire_threshold=50,
            desire_time_context={"elapsed_hours": 6, "time_boost": 30},
        )
        result = await thinker.desire(
            Stimulus("event", "channel", "user", "普通消息", 1.0),
            Observation("收到消息", ("普通消息",)),
            Inference("闲聊", "平静", 0, 0.8),
            WorldModel().snapshot("channel"),
        )

        self.assertEqual(result.score, 55)
        self.assertTrue(result.should_engage)
        self.assertTrue(any("时间欲望" in reason for reason in result.reasons))

    async def test_time_desire_does_not_override_boundaries(self) -> None:
        async def complete(system: str, prompt: str) -> str:
            return '{"score":45,"should_engage":false,"silence_reason":"boundary","reasons":["需要保持边界"]}'

        thinker = ModelBehaviorThinker(
            complete,
            desire_threshold=50,
            desire_time_context={"elapsed_hours": 10, "time_boost": 40},
        )
        result = await thinker.desire(
            Stimulus("event", "channel", "user", "越界请求", 1.0),
            Observation("收到越界请求", ("越界请求",)),
            Inference("越界请求", "警惕", 0, 0.9),
            WorldModel().snapshot("channel"),
        )

        self.assertEqual(result.score, 85)
        self.assertFalse(result.should_engage)

    async def test_invalid_json_is_repaired_once(self) -> None:
        outputs = iter(["not json", '{"summary":"fixed","facts":[]}'])

        async def complete(system: str, prompt: str) -> str:
            return next(outputs)

        thinker = ModelBehaviorThinker(complete)
        result = await thinker.observe(
            Stimulus("event", "channel", "user", "hello", 1.0),
            WorldModel().snapshot("channel"),
        )
        self.assertEqual(result.summary, "fixed")

    async def test_all_phases_map_structured_results(self) -> None:
        systems = []
        outputs = iter(
            [
                '{"summary":"seen","facts":["hello"]}',
                '{"intent":"chat","emotion":"calm","relation_delta":0.5,"confidence":0.8}',
                '{"score":75,"should_engage":true,"reasons":["direct"]}',
                '{"actions":[{"name":"lookup","arguments":{"q":"x"},"risk":"low"}],"expression":"ok","request_heartbeat":false}',
                '{"satisfied":true,"reason":"complete","revised_plan":null}',
            ]
        )

        async def complete(system: str, prompt: str) -> str:
            systems.append(system)
            self.assertIn("Ecobot", system)
            return next(outputs)

        thinker = ModelBehaviorThinker(complete, persona="quiet")
        stimulus = Stimulus("event", "channel", "user", "hello", 1.0)
        world = WorldModel().snapshot("channel")
        observation = await thinker.observe(stimulus, world)
        inference = await thinker.analyze_infer(stimulus, observation, world)
        desire = await thinker.desire(stimulus, observation, inference, world)
        plan = await thinker.plan(stimulus, observation, inference, desire, world)
        reflection = await thinker.reflect(
            stimulus,
            plan,
            (ActionFeedback(ActionSpec("lookup"), True, "found"),),
            world,
        )

        self.assertEqual(observation.facts, ("hello",))
        self.assertEqual(inference.intent, "chat")
        self.assertTrue(desire.should_engage)
        self.assertEqual(plan.actions[0].name, "lookup")
        self.assertTrue(reflection.satisfied)
        self.assertTrue(
            all("所有面向人的字段值必须使用简体中文" in system for system in systems)
        )

    async def test_non_string_expression_is_not_exposed(self) -> None:
        async def complete(system: str, prompt: str) -> str:
            return '{"actions":[],"expression":{"analysis":"private"}}'

        thinker = ModelBehaviorThinker(complete)
        result = await thinker.plan(
            Stimulus("event", "channel", "user", "hello", 1.0),
            Observation("hello", ("hello",)),
            Inference("chat", "neutral", 0, 1),
            Desire(90, True, ("direct",)),
            WorldModel().snapshot("channel"),
        )
        self.assertIsNone(result.expression)

    async def test_every_phase_receives_social_context(self) -> None:
        prompts = []
        outputs = iter(
            [
                '{"summary":"seen","facts":[]}',
                '{"intent":"chat","emotion":"calm","relation_delta":0,"confidence":1}',
                '{"score":90,"should_engage":true,"reasons":[]}',
                '{"actions":[],"expression":"ok","request_heartbeat":false}',
                '{"satisfied":true,"reason":"done","revised_plan":null}',
            ]
        )

        async def complete(system: str, prompt: str) -> str:
            prompts.append(prompt)
            return next(outputs)

        thinker = ModelBehaviorThinker(complete)
        stimulus = Stimulus("event", "channel", "user", "hello", 1.0)
        world = WorldModel().snapshot(
            "channel", social_context={"membership": {"role": "owner"}}
        )
        observation = await thinker.observe(stimulus, world)
        inference = await thinker.analyze_infer(stimulus, observation, world)
        desire = await thinker.desire(stimulus, observation, inference, world)
        plan = await thinker.plan(stimulus, observation, inference, desire, world)
        await thinker.reflect(stimulus, plan, (), world)

        self.assertEqual(len(prompts), 5)
        self.assertTrue(all('"role":"owner"' in prompt for prompt in prompts))

    async def test_relationship_aware_phases_receive_affinity_guidance(self) -> None:
        prompts = []
        outputs = iter(
            [
                '{"intent":"闲聊","emotion":"平静","relation_delta":0,"confidence":1}',
                '{"score":80,"should_engage":true,"reasons":["关系稳定"]}',
                '{"actions":[],"expression":"知道了","request_heartbeat":false}',
            ]
        )

        async def complete(system: str, prompt: str) -> str:
            prompts.append(prompt)
            return next(outputs)

        thinker = ModelBehaviorThinker(complete)
        stimulus = Stimulus("event", "channel", "user", "hello", 1.0)
        world = WorldSnapshot(
            channel_id="channel",
            revision=1,
            relation_scores={},
            recent_events=(),
            affinity={
                "stage": "亲近",
                "irritation": 5,
                "response_guidance": "允许明显关心和更高主动性。",
            },
        )
        observation = Observation("收到问候", ("对方问候",))
        inference = await thinker.analyze_infer(stimulus, observation, world)
        desire = await thinker.desire(stimulus, observation, inference, world)
        await thinker.plan(stimulus, observation, inference, desire, world)

        self.assertEqual(len(prompts), 3)
        self.assertTrue(all('"stage":"亲近"' in prompt for prompt in prompts))
        self.assertTrue(all("允许明显关心和更高主动性" in prompt for prompt in prompts))


if __name__ == "__main__":
    unittest.main()
