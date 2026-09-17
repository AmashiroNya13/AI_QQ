from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class SceneState:
    scene_id: str
    location_id: str
    local_time: str
    activity: str
    occupants: tuple[str, ...] = ()
    objects: Mapping[str, Any] = field(default_factory=dict)
    version: int = 1


@dataclass(frozen=True, slots=True)
class WorldResolution:
    intent_id: str
    status: str
    reason: str
    estimated_duration_seconds: int = 0
    next_location_id: str | None = None
    required_preparations: tuple[str, ...] = ()
    reactions: tuple[Mapping[str, Any], ...] = ()


class WorldActionResolver:
    def __init__(self) -> None:
        self._locations: dict[str, dict[str, Any]] = {
            "home": {"name": "家", "description": "可以休息、整理物品和独处的居所", "neighbors": {"library", "street"}, "objects": {"desk": "书桌", "phone": "手机"}, "availability": {}},
            "library": {"name": "图书馆", "description": "适合阅读和安静观察的公共空间", "neighbors": {"home", "street"}, "objects": {"books": "书架", "seat": "座位"}, "availability": {"open": True}},
            "street": {"name": "街道", "description": "可以散步、偶遇和观察环境的地方", "neighbors": {"home", "library"}, "objects": {"shop": "商店", "bench": "长椅"}, "availability": {}},
        }

    def locations(self) -> dict[str, dict[str, Any]]:
        return {
            key: {**value, "neighbors": set(value.get("neighbors", set()))}
            for key, value in self._locations.items()
        }

    def register_location(
        self,
        location_id: str,
        *,
        name: str,
        neighbors: tuple[str, ...] = (),
    ) -> None:
        self._locations[str(location_id)] = {
            "name": name,
            "description": "",
            "neighbors": set(neighbors),
            "objects": {},
            "availability": {},
        }

    def resolve(
        self,
        intent_id: str,
        action_type: str,
        arguments: Mapping[str, Any],
        *,
        current_location_id: str,
        current_activity: str | None = None,
        world_rules: tuple[Mapping[str, Any], ...] = (),
        active_schedule_facts: tuple[Mapping[str, Any], ...] = (),
    ) -> WorldResolution:
        if action_type == "go_to":
            target = str(arguments.get("location_id") or "").strip()
            if not target:
                return WorldResolution(intent_id, "blocked", "没有指定目的地")
            if target not in self._locations:
                return WorldResolution(
                    intent_id,
                    "needs_preparation",
                    "目的地尚未存在于当前世界，需要先建立地点或确认路径",
                    required_preparations=("场景扩展", "路线确认"),
                    reactions=(
                        {
                            "kind": "scene_expansion_proposed",
                            "location_id": target,
                            "reason": "主体提出了尚不存在的目的地",
                        },
                    ),
                )
            if target == current_location_id:
                return WorldResolution(intent_id, "succeeded", "已经在目的地", next_location_id=target)
            current = self._locations.get(current_location_id, {})
            neighbors = set(current.get("neighbors", set()))
            if target not in neighbors:
                return WorldResolution(
                    intent_id,
                    "needs_preparation",
                    "当前地点没有直接通往目的地的路径",
                    estimated_duration_seconds=900,
                    required_preparations=("规划路线",),
                )
            reactions = self._world_reactions(
                action_type,
                arguments,
                current_location_id=current_location_id,
                current_activity=current_activity,
                world_rules=world_rules,
                active_schedule_facts=active_schedule_facts,
            )
            return WorldResolution(
                intent_id,
                "succeeded",
                "目的地可达",
                estimated_duration_seconds=600,
                next_location_id=target,
                reactions=reactions,
            )
        if action_type == "start_activity":
            activity = str(arguments.get("activity") or "").strip()
            if not activity:
                return WorldResolution(intent_id, "blocked", "没有指定活动")
            reactions = self._world_reactions(
                action_type,
                arguments,
                current_location_id=current_location_id,
                current_activity=current_activity,
                world_rules=world_rules,
                active_schedule_facts=active_schedule_facts,
            )
            return WorldResolution(
                intent_id,
                "succeeded",
                "活动可以开始，世界会持续产生相应后果",
                estimated_duration_seconds=300,
                reactions=reactions,
            )
        return WorldResolution(
            intent_id,
            "needs_preparation",
            "当前世界尚无直接能力，但意图被保留为待探索目标",
            required_preparations=("寻找能力",),
            reactions=(
                {
                    "kind": "capability_gap",
                    "action_type": action_type,
                    "reason": "世界尚未提供此行动的实现方式",
                },
            ),
        )

    @staticmethod
    def _world_reactions(
        action_type: str,
        arguments: Mapping[str, Any],
        *,
        current_location_id: str,
        current_activity: str | None,
        world_rules: tuple[Mapping[str, Any], ...],
        active_schedule_facts: tuple[Mapping[str, Any], ...],
    ) -> tuple[Mapping[str, Any], ...]:
        reactions: list[Mapping[str, Any]] = []
        target_location = str(arguments.get("location_id") or "")
        activity = str(arguments.get("activity") or "")
        for rule in world_rules:
            conditions = rule.get("conditions", {})
            if conditions.get("current_activity") and conditions["current_activity"] != current_activity:
                continue
            if conditions.get("current_location_id") and conditions["current_location_id"] != current_location_id:
                continue
            if conditions.get("target_location_id") and conditions["target_location_id"] != target_location:
                continue
            if conditions.get("activity") and conditions["activity"] != activity:
                continue
            reactions.extend(dict(item) for item in rule.get("reactions", ()))
        for fact in active_schedule_facts:
            if fact.get("location_id") != current_location_id:
                continue
            if fact.get("activity") and fact["activity"] != current_activity:
                continue
            leaving = action_type == "go_to" and target_location != current_location_id
            changing_activity = action_type == "start_activity" and activity != current_activity
            if leaving or changing_activity:
                reactions.extend(dict(item) for item in fact.get("reactions", ()))
        return tuple(reactions)
