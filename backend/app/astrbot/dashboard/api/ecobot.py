from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query

from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from astrbot.dashboard.responses import ApiError, ok
from ecobot.admin_store import EcobotAdminStore
from ecobot.qq_archive import QQArchive
from ecobot2.store import AutonomousStore

from .auth import AuthContext, ScopeDependency


router = APIRouter(tags=["Ecobot"])
require_data_scope = ScopeDependency("data")
require_config_scope = ScopeDependency("config")


def _database_path() -> Path:
    return Path(get_astrbot_data_path()) / "ecobot" / "world.db"


def _admin() -> EcobotAdminStore:
    return EcobotAdminStore(_database_path())


def _autonomous() -> AutonomousStore:
    return AutonomousStore(_database_path())


@router.get("/ecobot/status")
async def status(_auth: AuthContext = Depends(require_data_scope)):
    store = _admin()
    try:
        return ok(store.status())
    finally:
        store.close()


@router.get("/ecobot/autonomy/events")
async def autonomy_events(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.events(limit))
    finally:
        store.close()


@router.get("/ecobot/autonomy/actions")
async def autonomy_actions(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.actions(limit))
    finally:
        store.close()


@router.get("/ecobot/autonomy/consequences")
async def autonomy_consequences(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.consequences(limit))
    finally:
        store.close()


@router.get("/ecobot/autonomy/appraisals")
async def autonomy_appraisals(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.appraisals(limit))
    finally:
        store.close()


@router.get("/ecobot/autonomy/locations")
async def autonomy_locations(
    limit: int = Query(1000, ge=1, le=5000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.locations(limit))
    finally:
        store.close()


@router.get("/ecobot/autonomy/scenes")
async def autonomy_scenes(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.scenes(limit))
    finally:
        store.close()


@router.get("/ecobot/autonomy/intentions")
async def autonomy_intentions(
    status: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.intentions(limit, status))
    finally:
        store.close()


@router.get("/ecobot/autonomy/attention")
async def autonomy_attention(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.attention_decisions(limit))
    finally:
        store.close()


@router.get("/ecobot/autonomy/revisions")
async def autonomy_revisions(
    status: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.identity_revisions(limit, status))
    finally:
        store.close()


@router.get("/ecobot/autonomy/threads")
async def autonomy_threads(
    channel_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.threads(limit, channel_id))
    finally:
        store.close()


@router.get("/ecobot/autonomy/dialogue-acts")
async def autonomy_dialogue_acts(
    event_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.dialogue_acts(limit, event_id))
    finally:
        store.close()


@router.get("/ecobot/autonomy/obligations")
async def autonomy_obligations(
    status: str | None = "pending",
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.obligations(limit, status))
    finally:
        store.close()


@router.get("/ecobot/autonomy/beliefs")
async def autonomy_beliefs(
    subject: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.beliefs(limit, subject))
    finally:
        store.close()


@router.get("/ecobot/autonomy/capabilities")
async def autonomy_capabilities(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.capabilities(limit))
    finally:
        store.close()


@router.get("/ecobot/autonomy/world-entities")
async def autonomy_world_entities(
    entity_kind: str | None = None,
    limit: int = Query(200, ge=1, le=2000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.world_entities(limit, entity_kind))
    finally:
        store.close()


@router.get("/ecobot/autonomy/schedule-facts")
async def autonomy_schedule_facts(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.schedule_facts(limit))
    finally:
        store.close()


@router.get("/ecobot/autonomy/world-rules")
async def autonomy_world_rules(
    action_type: str | None = None,
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.world_rules(action_type))
    finally:
        store.close()


@router.get("/ecobot/autonomy/scene-expansions")
async def autonomy_scene_expansions(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.scene_expansion_proposals(limit))
    finally:
        store.close()


@router.get("/ecobot/state")
async def state(
    agent_id: str = "ecobot",
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        value = store.subjective_state(agent_id)
        if value is None:
            return ok(None)
        data = asdict(value)
        scene = store.scene("main")
        data.update({
            "behavior": data["activity"],
            "scene": scene.scene_id if scene else "main",
            "location": data["location_id"],
            "mood": data["mood"],
            "scene_state": asdict(scene) if scene else None,
        })
        return ok(data)
    finally:
        store.close()


@router.get("/ecobot/state/history")
async def state_history(
    agent_id: str = "ecobot",
    limit: int = Query(50, ge=1, le=500),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        rows = []
        for event in store.events(max(50, limit * 3)):
            if event.get("kind") not in {"scene_changed", "time_tick"}:
                continue
            payload = event.get("payload") or {}
            rows.append({
                "version": payload.get("version"),
                "trigger": event.get("kind"),
                "reason": payload.get("source") or "主体世界推进",
                "batch_id": event.get("event_id"),
                "changed_at": event.get("occurred_at"),
                "next_state_json": json.dumps(payload, ensure_ascii=False, default=str),
            })
            if len(rows) >= limit:
                break
        return ok(rows)
    finally:
        store.close()


@router.get("/ecobot/relationships")
async def relationships(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _admin()
    try:
        return ok(store.relationships(limit))
    finally:
        store.close()


@router.get("/ecobot/affinities")
async def affinities(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.relationship_profiles(limit))
    finally:
        store.close()


@router.get("/ecobot/affinity/events")
async def affinity_events(
    user_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.relationship_events(user_id, limit))
    finally:
        store.close()


@router.patch("/ecobot/affinities/{user_id}")
async def update_affinity(
    user_id: str,
    changes: dict[str, Any],
    _auth: AuthContext = Depends(require_config_scope),
):
    store = _autonomous()
    try:
        return ok(store.manual_update_relationship(user_id, changes))
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    finally:
        store.close()


@router.get("/ecobot/memory/episodes")
async def memory_episodes(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.memory_episodes(limit))
    finally:
        store.close()


@router.get("/ecobot/memory/retrievals")
async def memory_retrievals(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.memory_retrievals(limit))
    finally:
        store.close()


@router.get("/ecobot/memory/policies")
async def memory_policies(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.memory_policies(limit))
    finally:
        store.close()


@router.get("/ecobot/persona/increments")
async def persona_increments(
    status: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.persona_increments(limit, status))
    finally:
        store.close()


@router.get("/ecobot/temporal-relations")
async def temporal_relations(
    subject_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.temporal_relations(subject_id, limit))
    finally:
        store.close()


@router.get("/ecobot/grievances")
async def grievances(
    target_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _autonomous()
    try:
        return ok(store.grievances(target_id, limit))
    finally:
        store.close()


@router.get("/ecobot/traces")
async def traces(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _admin()
    try:
        return ok(store.phase_traces(limit))
    finally:
        store.close()


@router.get("/ecobot/world/events")
async def world_events(
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _admin()
    try:
        return ok(store.world_events(limit))
    finally:
        store.close()


@router.get("/ecobot/users")
async def users(
    q: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _admin()
    try:
        return ok(store.users(q, limit))
    finally:
        store.close()


@router.get("/ecobot/groups")
async def groups(
    q: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _admin()
    try:
        return ok(store.groups(q, limit))
    finally:
        store.close()


@router.get("/ecobot/messages")
async def messages(
    q: str | None = None,
    group_id: str | None = None,
    sender_qq_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    _auth: AuthContext = Depends(require_data_scope),
):
    store = _admin()
    try:
        return ok(store.messages(q, group_id, sender_qq_id, limit))
    finally:
        store.close()


@router.get("/ecobot/messages/search")
async def search_messages(
    q: str = Query(..., min_length=1),
    group_id: str | None = None,
    sender_qq_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    _auth: AuthContext = Depends(require_data_scope),
):
    archive = QQArchive(_database_path())
    try:
        return ok(
            archive.search_messages(
                q, group_id=group_id, sender_qq_id=sender_qq_id, limit=limit
            )
        )
    finally:
        archive.close()


@router.get("/ecobot/config")
async def config(_auth: AuthContext = Depends(require_config_scope)):
    store = _admin()
    try:
        return ok(store.settings())
    finally:
        store.close()


@router.patch("/ecobot/config")
async def update_config(
    changes: dict[str, Any],
    _auth: AuthContext = Depends(require_config_scope),
):
    store = _admin()
    try:
        return ok(store.update_settings(changes))
    finally:
        store.close()
