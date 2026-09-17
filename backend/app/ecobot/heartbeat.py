from __future__ import annotations

import uuid
import inspect
import json
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from .contracts import (
    ActionFeedback,
    ActionSpec,
    BatchState,
    BehaviorBatchError,
    BehaviorResult,
    Desire,
    Inference,
    Observation,
    Plan,
    Reflection,
    Stimulus,
)
from .world_model import WorldModel, WorldSnapshot
from .agent_state import AgentStateStore


class BehaviorThinker(Protocol):
    async def observe(
        self, stimulus: Stimulus, world: WorldSnapshot
    ) -> Observation: ...

    async def analyze_infer(
        self, stimulus: Stimulus, observation: Observation, world: WorldSnapshot
    ) -> Inference: ...

    async def desire(
        self,
        stimulus: Stimulus,
        observation: Observation,
        inference: Inference,
        world: WorldSnapshot,
    ) -> Desire: ...

    async def plan(
        self,
        stimulus: Stimulus,
        observation: Observation,
        inference: Inference,
        desire: Desire,
        world: WorldSnapshot,
    ) -> Plan: ...

    async def reflect(
        self,
        stimulus: Stimulus,
        plan: Plan,
        feedback: tuple[ActionFeedback, ...],
        world: WorldSnapshot,
    ) -> Reflection: ...


ActionExecutor = Callable[[ActionSpec], Awaitable[ActionFeedback]]
MemoryRetriever = Callable[[Stimulus], tuple[dict[str, Any], ...]]
ExpressionGate = Callable[[str, str], bool | Awaitable[bool]]
ExpressionRewriter = Callable[[Stimulus, str, WorldSnapshot], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class ThoughtResult:
    observation: Observation
    inference: Inference
    desire: Desire
    plan: Plan | None


class ThoughtsConsumer:
    """Consumes one stimulus through observe, infer, desire, and plan."""

    def __init__(self, thinker: BehaviorThinker) -> None:
        self.thinker = thinker

    async def consume(
        self,
        stimulus: Stimulus,
        world: WorldSnapshot,
        states: list[BatchState],
    ) -> ThoughtResult:
        states.append(BatchState.OBSERVING)
        observation = await self.thinker.observe(stimulus, world)
        states.append(BatchState.INFERRING)
        inference = await self.thinker.analyze_infer(stimulus, observation, world)
        states.append(BatchState.DESIRING)
        desire = await self.thinker.desire(stimulus, observation, inference, world)
        if not desire.should_engage:
            return ThoughtResult(observation, inference, desire, None)
        states.append(BatchState.PLANNING)
        plan = await self.thinker.plan(
            stimulus, observation, inference, desire, world
        )
        return ThoughtResult(observation, inference, desire, plan)


class ChatScheduler:
    """Final expression gate immediately before transport handling."""

    def __init__(self, expression_gate: ExpressionGate | None = None) -> None:
        self.expression_gate = expression_gate

    async def select(
        self, channel_id: str, expression: str | None
    ) -> tuple[str | None, str | None]:
        if expression is None or not expression.strip():
            return None, None
        value = expression.strip()
        if _contains_private_cognition(value):
            return None, "private_cognition_blocked"
        if self.expression_gate is not None:
            allowed = self.expression_gate(channel_id, value)
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                return None, "duplicate_expression"
        return value, None


_PRIVATE_COGNITION_KEYS = {
    "actions",
    "analysis",
    "desire",
    "expression",
    "facts",
    "inference",
    "observation",
    "plan",
    "reasoning",
    "reflection",
    "relation_delta",
    "request_heartbeat",
    "should_engage",
    "state_update",
    "trust_delta",
    "familiarity_delta",
    "relationship_reason",
}

_PRIVATE_COGNITION_PREFIXES = (
    "分析：",
    "分析:",
    "思考：",
    "思考:",
    "推理：",
    "推理:",
    "观察结果：",
    "观察结果:",
    "欲望判断：",
    "欲望判断:",
    "行为规划：",
    "行为规划:",
    "关系变化：",
    "关系变化:",
    "好感度变化：",
    "好感度变化:",
    "<analysis>",
    "<think>",
)


def _contains_private_cognition(value: str) -> bool:
    source = value.strip()
    if source.casefold().startswith(_PRIVATE_COGNITION_PREFIXES):
        return True
    if source.startswith("```") and source.endswith("```"):
        lines = source.splitlines()
        if len(lines) >= 3:
            source = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(source)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and not _PRIVATE_COGNITION_KEYS.isdisjoint(payload)


class ActionsConsumer:
    """Executes planned actions and feeds results back into behavior reflection."""

    def __init__(
        self,
        thinker: BehaviorThinker,
        executor: ActionExecutor,
        world_model: WorldModel,
        *,
        max_action_rounds: int,
        max_actions: int,
    ) -> None:
        self.thinker = thinker
        self.executor = executor
        self.world_model = world_model
        self.max_action_rounds = max_action_rounds
        self.max_actions = max_actions

    async def consume(
        self,
        stimulus: Stimulus,
        initial_plan: Plan,
        states: list[BatchState],
        feedback_items: list[ActionFeedback],
        snapshot_factory: Callable[[], WorldSnapshot],
    ) -> tuple[Plan, str | None]:
        current_plan = initial_plan
        stop_reason: str | None = None
        executed_actions: set[str] = set()
        for _ in range(self.max_action_rounds):
            if not current_plan.actions:
                break
            states.append(BatchState.ACTING)
            round_feedback: list[ActionFeedback] = []
            for action in current_plan.actions:
                if len(feedback_items) >= self.max_actions:
                    stop_reason = "action_budget_exhausted"
                    break
                signature = json.dumps(
                    {"name": action.name, "arguments": dict(action.arguments)},
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                if signature in executed_actions:
                    feedback = ActionFeedback(
                        action=action,
                        success=False,
                        error="duplicate action suppressed",
                    )
                    feedback_items.append(feedback)
                    round_feedback.append(feedback)
                    self.world_model.record_feedback(stimulus.channel_id, feedback)
                    continue
                executed_actions.add(signature)
                try:
                    feedback = await self.executor(action)
                except Exception as exc:
                    feedback = ActionFeedback(
                        action=action,
                        success=False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                feedback_items.append(feedback)
                round_feedback.append(feedback)
                self.world_model.record_feedback(stimulus.channel_id, feedback)

            states.append(BatchState.REFLECTING)
            reflection = await self.thinker.reflect(
                stimulus, current_plan, tuple(round_feedback), snapshot_factory()
            )
            if reflection.satisfied:
                break
            if stop_reason or reflection.revised_plan is None:
                stop_reason = stop_reason or "reflection_could_not_replan"
                break
            current_plan = reflection.revised_plan
        else:
            stop_reason = "action_round_budget_exhausted"
        return current_plan, stop_reason


class HeartbeatKernel:
    """Runs Ecobot's cognition and action-feedback loop per channel."""

    def __init__(
        self,
        world_model: WorldModel,
        thinker: BehaviorThinker,
        action_executor: ActionExecutor,
        *,
        max_action_rounds: int = 4,
        max_actions: int = 12,
        agent_state_store: AgentStateStore | None = None,
        agent_id: str = "ecobot",
        memory_retriever: MemoryRetriever | None = None,
        expression_gate: ExpressionGate | None = None,
        expression_rewriter: ExpressionRewriter | None = None,
        state_update_enabled: bool = True,
    ) -> None:
        if max_action_rounds < 1 or max_actions < 1:
            raise ValueError("behavior budgets must be positive")
        self.world_model = world_model
        self.thinker = thinker
        self.action_executor = action_executor
        self.max_action_rounds = max_action_rounds
        self.max_actions = max_actions
        self.agent_state_store = agent_state_store
        self.agent_id = agent_id
        self.state_update_enabled = state_update_enabled
        self.memory_retriever = memory_retriever
        self.thoughts_consumer = ThoughtsConsumer(thinker)
        self.chat_scheduler = ChatScheduler(expression_gate)
        self.expression_rewriter = expression_rewriter
        self.actions_consumer = ActionsConsumer(
            thinker,
            action_executor,
            world_model,
            max_action_rounds=max_action_rounds,
            max_actions=max_actions,
        )

    async def process(
        self,
        stimulus: Stimulus,
        *,
        batch_id: str | None = None,
        memories: tuple[dict[str, Any], ...] | None = None,
    ) -> BehaviorResult:
        async with self.world_model.channel_lock(stimulus.channel_id):
            return await self._process_locked(
                stimulus, batch_id=batch_id, memories=memories
            )

    async def _process_locked(
        self,
        stimulus: Stimulus,
        *,
        batch_id: str | None,
        memories: tuple[dict[str, Any], ...] | None,
    ) -> BehaviorResult:
        actual_batch_id = batch_id or uuid.uuid4().hex
        states = [BatchState.WAITING]
        feedback_items: list[ActionFeedback] = []
        try:
            return await self._run_batch(
                actual_batch_id, states, feedback_items, stimulus, memories
            )
        except Exception as exc:
            states.append(BatchState.FAILED)
            raise BehaviorBatchError(batch_id, tuple(states), exc) from exc

    async def _run_batch(
        self,
        batch_id: str,
        states: list[BatchState],
        feedback_items: list[ActionFeedback],
        stimulus: Stimulus,
        supplied_memories: tuple[dict[str, Any], ...] | None,
    ) -> BehaviorResult:
        self.world_model.record_stimulus(stimulus)
        agent_state = None
        if self.agent_state_store is not None:
            agent_state = self.agent_state_store.advance_due(self.agent_id)
        memories = supplied_memories
        if memories is None:
            memories = self.memory_retriever(stimulus) if self.memory_retriever else ()

        def snapshot_factory() -> WorldSnapshot:
            return self.world_model.snapshot(
                stimulus.channel_id,
                agent_state=agent_state.to_prompt_dict() if agent_state else None,
                memories=memories,
                social_context=stimulus.metadata.get("qq_context", {}),
                focus_user_id=stimulus.user_id,
                style_reference=stimulus.metadata.get("style_reference", {}),
                reply_style_guard=stimulus.metadata.get("reply_style_guard", {}),
            )

        snapshot = snapshot_factory()
        thoughts = await self.thoughts_consumer.consume(stimulus, snapshot, states)
        if not thoughts.desire.should_engage or thoughts.plan is None:
            self.world_model.commit_inference(
                stimulus.channel_id,
                stimulus.user_id,
                thoughts.inference,
                batch_id=batch_id,
                update_affinity=not bool(stimulus.metadata.get("skip_affinity")),
                update_relation=not bool(stimulus.metadata.get("skip_relation")),
            )
            states.append(BatchState.COMPLETED)
            return BehaviorResult(
                batch_id=batch_id,
                state=BatchState.COMPLETED,
                expression=None,
                desire=thoughts.desire,
                feedback=(),
                state_history=tuple(states),
                stop_reason="silent_by_desire",
            )

        current_plan, stop_reason = await self.actions_consumer.consume(
            stimulus,
            thoughts.plan,
            states,
            feedback_items,
            snapshot_factory,
        )

        if (
            self.agent_state_store is not None
            and self.state_update_enabled
            and current_plan.state_update
        ):
            agent_state = self.agent_state_store.transition(
                self.agent_id,
                current_plan.state_update,
                trigger=str(stimulus.metadata.get("trigger") or "message"),
                reason="behavior plan state update",
                expected_version=agent_state.version if agent_state else None,
                batch_id=batch_id,
                channel_id=stimulus.channel_id,
            )

        expression = current_plan.expression
        if expression and self.expression_rewriter is not None:
            try:
                rewritten = await self.expression_rewriter(
                    stimulus, expression, snapshot_factory()
                )
                if rewritten and rewritten.strip():
                    expression = rewritten.strip()
            except Exception:
                expression = current_plan.expression
        final_expression, expression_stop = await self.chat_scheduler.select(
            stimulus.channel_id, expression
        )
        stop_reason = expression_stop or stop_reason

        self.world_model.commit_inference(
            stimulus.channel_id,
            stimulus.user_id,
            thoughts.inference,
            batch_id=batch_id,
            update_affinity=not bool(stimulus.metadata.get("skip_affinity")),
            update_relation=not bool(stimulus.metadata.get("skip_relation")),
        )
        states.append(BatchState.COMPLETED)
        return BehaviorResult(
            batch_id=batch_id,
            state=BatchState.COMPLETED,
            expression=final_expression,
            desire=thoughts.desire,
            feedback=tuple(feedback_items),
            state_history=tuple(states),
            stop_reason=stop_reason,
        )
