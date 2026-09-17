from __future__ import annotations

import json
import math
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .affinity import (
    INITIAL_AFFINITY,
    INITIAL_TRUST,
    SPECIAL_LEVEL_NONE,
    SPECIAL_LEVELS,
    affinity_stage,
)

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "persona_enabled": True,
    "passive_interval_seconds": 15,
    "idle_interval_seconds": 600,
    "idle_poll_seconds": 5,
    "idle_allow_proactive_expression": True,
    "idle_prompt": (
        "当前没有新消息。结合当前场景、持续维护的人物状态和人格，判断是否需要采取"
        "自然且连贯的主动行为；保持沉默也是有效选择。"
    ),
    "max_action_rounds": 4,
    "max_actions": 12,
    "recent_message_limit": 20,
    "memory_limit": 12,
    "memory_embedding_enabled": True,
    "memory_embedding_provider_id": "",
    "memory_semantic_min_similarity": 0.35,
    "structured_output_retries": 1,
    "model_timeout_seconds": 90,
    "request_max_retries": 2,
    "generation_temperature": 0.4,
    "generation_top_p": 0.9,
    "generation_max_tokens": 1200,
    "desire_threshold": 50.0,
    "desire_time_growth_enabled": True,
    "desire_time_growth_per_hour": 5.0,
    "desire_time_growth_max": 40.0,
    "state_update_enabled": True,
    "tools_enabled": True,
    "tool_allowlist": [],
    "max_tool_risk": "medium",
    "anti_repeat_window_minutes": 360,
    "anti_repeat_fuzzy_threshold": 0.92,
    "anti_repeat_semantic_threshold": 0.94,
    "reply_style_repeat_enabled": True,
    "reply_style_window_minutes": 60,
    "reply_style_repeat_limit": 1,
    "style_learning_enabled": True,
    "style_reference_enabled": True,
    "style_reference_user_ids": [],
    "style_reference_limit": 6,
    "style_reference_min_similarity": 0.35,
    "identity_imitation_enabled": False,
    "identity_imitation_user_id": "",
    "high_fidelity_imitation_enabled": False,
    "style_rewrite_provider_id": "",
    "style_rewrite_prompt": "保持原回复的事实和立场，只进行目标人物风格改写。",
    "relationship_scan_interval_seconds": 1800,
    "relationship_evaluation_interval_hours": 168,
    "relationship_minimum_new_evidence": 10,
    "relationship_ai_enabled": False,
    "relationship_provider_id": "",
    "relationship_prompt": (
        "谨慎复核关系统计证据，区分事实与推断，并使用简体中文返回简洁的关系类型、"
        "关系摘要和置信度。"
    ),
    "affinity_enabled": True,
    "affinity_rules_version": 2,
    "affinity_positive_step_limit": 20.0,
    "affinity_negative_step_limit": 30.0,
    "affinity_irritation_half_life_hours": 12.0,
    "trace_enabled": True,
    "trace_include_prompts": True,
    "trace_max_records": 5000,
    "debug_log_enabled": True,
    "debug_log_include_prompts": False,
    "observe_enabled": True,
    "observe_provider_id": "",
    "observe_prompt": "只提取当前刺激和世界状态直接支持的事实，所有可读内容使用简体中文。",
    "infer_enabled": True,
    "infer_provider_id": "",
    "infer_prompt": "谨慎推断意图、情绪和关系影响；存在不确定性时必须降低置信度，并使用简体中文。",
    "desire_enabled": True,
    "desire_provider_id": "",
    "desire_prompt": "结合人格动机、场景和人物状态判断是否参与；沉默也是有效行为，理由使用简体中文。",
    "plan_enabled": True,
    "plan_provider_id": "",
    "plan_prompt": (
        "优先选择最小且连贯的行为，保持当前状态、时间和场景的连续性，所有可读内容使用简体中文。"
    ),
    "reflect_enabled": True,
    "reflect_provider_id": "",
    "reflect_prompt": "仅当动作反馈表明原计划未完成时重新规划，反思理由使用简体中文。",
}

BOOLEAN_SETTINGS = {
    "enabled",
    "persona_enabled",
    "idle_allow_proactive_expression",
    "memory_embedding_enabled",
    "reply_style_repeat_enabled",
    "style_learning_enabled",
    "style_reference_enabled",
    "identity_imitation_enabled",
    "high_fidelity_imitation_enabled",
    "state_update_enabled",
    "tools_enabled",
    "relationship_ai_enabled",
    "affinity_enabled",
    "trace_enabled",
    "trace_include_prompts",
    "debug_log_enabled",
    "debug_log_include_prompts",
    "desire_time_growth_enabled",
    "observe_enabled",
    "infer_enabled",
    "desire_enabled",
    "plan_enabled",
    "reflect_enabled",
}
INTEGER_RANGES = {
    "affinity_rules_version": (1, 2),
    "passive_interval_seconds": (0, 86400),
    "idle_interval_seconds": (1, 604800),
    "idle_poll_seconds": (1, 3600),
    "max_action_rounds": (1, 20),
    "max_actions": (1, 100),
    "recent_message_limit": (1, 500),
    "memory_limit": (1, 500),
    "structured_output_retries": (0, 5),
    "model_timeout_seconds": (5, 600),
    "request_max_retries": (0, 10),
    "generation_max_tokens": (64, 32768),
    "anti_repeat_window_minutes": (1, 43200),
    "reply_style_window_minutes": (1, 43200),
    "reply_style_repeat_limit": (1, 20),
    "style_reference_limit": (1, 8),
    "relationship_scan_interval_seconds": (60, 604800),
    "relationship_evaluation_interval_hours": (1, 8760),
    "relationship_minimum_new_evidence": (1, 10000),
    "trace_max_records": (100, 100000),
}
FLOAT_RANGES = {
    "memory_semantic_min_similarity": (0.0, 1.0),
    "generation_temperature": (0.0, 2.0),
    "generation_top_p": (0.0, 1.0),
    "desire_threshold": (0.0, 100.0),
    "desire_time_growth_per_hour": (0.0, 100.0),
    "desire_time_growth_max": (0.0, 100.0),
    "anti_repeat_fuzzy_threshold": (0.0, 1.0),
    "anti_repeat_semantic_threshold": (0.0, 1.0),
    "style_reference_min_similarity": (0.0, 1.0),
    "affinity_positive_step_limit": (0.1, 20.0),
    "affinity_negative_step_limit": (0.1, 30.0),
    "affinity_irritation_half_life_hours": (0.1, 720.0),
}
STRING_SETTINGS = {
    "idle_prompt",
    "memory_embedding_provider_id",
    "relationship_provider_id",
    "identity_imitation_user_id",
    "style_rewrite_provider_id",
    "style_rewrite_prompt",
    "relationship_prompt",
    "observe_provider_id",
    "observe_prompt",
    "infer_provider_id",
    "infer_prompt",
    "desire_provider_id",
    "desire_prompt",
    "plan_provider_id",
    "plan_prompt",
    "reflect_provider_id",
    "reflect_prompt",
}
ENUM_SETTINGS = {"max_tool_risk": {"low", "medium", "high"}}
LIST_SETTINGS = {"tool_allowlist", "style_reference_user_ids"}


class EcobotAdminStore:
    """Read-only operational views plus validated runtime settings."""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ecobot_settings (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    settings_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            profile_table = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ecobot_affinity_profiles'"
            ).fetchone()
            if profile_table:
                columns = {
                    row[1]
                    for row in self._connection.execute(
                        "PRAGMA table_info(ecobot_affinity_profiles)"
                    ).fetchall()
                }
                for name, statement in (
                    ("special_level", "ALTER TABLE ecobot_affinity_profiles ADD COLUMN special_level TEXT NOT NULL DEFAULT 'none'"),
                    ("special_reason", "ALTER TABLE ecobot_affinity_profiles ADD COLUMN special_reason TEXT"),
                    ("special_set_at", "ALTER TABLE ecobot_affinity_profiles ADD COLUMN special_set_at TEXT"),
                ):
                    if name not in columns:
                        self._connection.execute(statement)
            self._connection.execute(
                "INSERT OR IGNORE INTO ecobot_settings(id, settings_json) VALUES (1, ?)",
                (json.dumps(DEFAULT_SETTINGS, ensure_ascii=False),),
            )
            settings_row = self._connection.execute(
                "SELECT settings_json FROM ecobot_settings WHERE id = 1"
            ).fetchone()
            stored_settings = json.loads(settings_row[0])
            if int(stored_settings.get("affinity_rules_version", 1)) < 2:
                stored_settings.update(
                    {
                        "affinity_rules_version": 2,
                        "affinity_positive_step_limit": 20.0,
                        "affinity_negative_step_limit": 30.0,
                    }
                )
                self._connection.execute(
                    "UPDATE ecobot_settings SET settings_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
                    (json.dumps(stored_settings, ensure_ascii=False),),
                )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ecobot_ai_phase_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id TEXT,
                    channel_id TEXT,
                    phase TEXT NOT NULL,
                    provider_id TEXT,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    system_prompt TEXT,
                    input_prompt TEXT,
                    output_text TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_ecobot_ai_phase_traces_recent
                ON ecobot_ai_phase_traces(created_at DESC, id DESC)
                """
            )

    def settings(self) -> dict[str, Any]:
        with self._guard:
            row = self._connection.execute(
                "SELECT settings_json, updated_at FROM ecobot_settings WHERE id = 1"
            ).fetchone()
        value = {**DEFAULT_SETTINGS, **json.loads(row[0])}
        value["updated_at"] = row[1]
        return value

    def update_settings(self, changes: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError(f"unknown Ecobot settings: {', '.join(sorted(unknown))}")
        value = self.settings()
        value.pop("updated_at", None)
        for key, raw in changes.items():
            if key in BOOLEAN_SETTINGS:
                value[key] = bool(raw)
                continue
            if key in INTEGER_RANGES:
                number = int(raw)
                minimum, maximum = INTEGER_RANGES[key]
                if not minimum <= number <= maximum:
                    raise ValueError(f"{key} must be between {minimum} and {maximum}")
                value[key] = number
                continue
            if key in FLOAT_RANGES:
                number = float(raw)
                minimum, maximum = FLOAT_RANGES[key]
                if not minimum <= number <= maximum:
                    raise ValueError(f"{key} must be between {minimum} and {maximum}")
                value[key] = number
                continue
            if key in STRING_SETTINGS:
                value[key] = str(raw or "").strip()
                continue
            if key in ENUM_SETTINGS:
                option = str(raw or "").strip().lower()
                if option not in ENUM_SETTINGS[key]:
                    raise ValueError(f"{key} has an unsupported value")
                value[key] = option
                continue
            if key in LIST_SETTINGS:
                if not isinstance(raw, list):
                    raise ValueError(f"{key} must be a list")
                value[key] = list(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))
                continue
        if value["identity_imitation_enabled"] and not value["identity_imitation_user_id"]:
            raise ValueError("启用身份模仿时必须填写被模仿对象 QQ 号")
        if value["high_fidelity_imitation_enabled"] and not value["identity_imitation_enabled"]:
            raise ValueError("高保真二次改写只能在身份模仿模式下启用")
        with self._guard, self._connection:
            self._connection.execute(
                """
                UPDATE ecobot_settings SET settings_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (json.dumps(value, ensure_ascii=False),),
            )
        return self.settings()

    def status(self) -> dict[str, Any]:
        counts = {}
        for table in (
            "qq_users",
            "qq_groups",
            "qq_messages",
            "qq_outbound_messages",
            "qq_binary_assets",
            "qq_relationships",
            "ecobot_affinity_profiles",
            "ecobot_style_profiles",
            "ecobot_memories",
            "ecobot_behavior_batches",
        ):
            counts[table] = self._count(table)
        with self._guard:
            schema = self._connection.execute(
                "SELECT version FROM ecobot_schema_versions WHERE component = 'qq_archive'"
            ).fetchone()
            latest = self._connection.execute(
                """
                SELECT batch_id, channel_id, trigger, status, created_at, completed_at,
                       stop_reason, error
                FROM ecobot_behavior_batches ORDER BY created_at DESC LIMIT 1
                """
            ).fetchone()
        return {
            "schema_version": int(schema[0]) if schema else None,
            "counts": counts,
            "latest_batch": dict(latest) if latest else None,
            "settings": self.settings(),
        }

    def state(self, agent_id: str = "ecobot") -> dict[str, Any] | None:
        return self._one(
            "SELECT * FROM ecobot_agent_states WHERE agent_id = ?", (agent_id,)
        )

    def state_history(self, agent_id: str = "ecobot", limit: int = 50) -> list[dict[str, Any]]:
        return self._many(
            """
            SELECT version, trigger, reason, batch_id, next_state_json, changed_at
            FROM ecobot_agent_state_history WHERE agent_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (agent_id, max(1, min(limit, 500))),
        )

    def relationships(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._many(
            """
            SELECT person_a_qq_id, person_b_qq_id, evidence_count,
                   current_relation_type, current_summary, interaction_strength,
                   reciprocity, confidence, last_interaction_at, last_evaluated_at
            FROM qq_relationships
            ORDER BY interaction_strength DESC, last_interaction_at DESC LIMIT ?
            """,
            (max(1, min(limit, 1000)),),
        )

    def affinities(self, limit: int = 100) -> list[dict[str, Any]]:
        profiles = self._many(
            """
            SELECT p.user_id, u.current_nickname, p.affinity_score,
                   p.trust_score, p.familiarity, p.irritation,
                   p.special_level, p.special_reason, p.special_set_at,
                   p.interaction_count, p.positive_interactions,
                   p.negative_interactions, p.last_reason,
                   p.first_interaction_at, p.last_interaction_at, p.updated_at
            FROM ecobot_affinity_profiles p
            LEFT JOIN qq_users u ON u.qq_id = p.user_id
            ORDER BY p.last_interaction_at DESC LIMIT ?
            """,
            (max(1, min(limit, 1000)),),
        )
        now = datetime.now(timezone.utc)
        half_life = float(self.settings()["affinity_irritation_half_life_hours"])
        for profile in profiles:
            profile["special_locked"] = profile["special_level"] != SPECIAL_LEVEL_NONE
            profile["stage"] = affinity_stage(
                float(profile["affinity_score"]), profile["special_level"]
            )
            profile["irritation"] = round(
                _decayed_irritation(
                    float(profile["irritation"]),
                    profile["updated_at"],
                    now,
                    half_life,
                ),
                2,
            )
        return profiles

    def affinity_events(
        self, user_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return self._many(
            """
            SELECT id, user_id, channel_id, batch_id, raw_affinity_delta,
                   applied_affinity_delta, raw_trust_delta, applied_trust_delta,
                   familiarity_delta, irritation_delta, confidence, reason,
                   previous_stage, new_stage, created_at
            FROM ecobot_affinity_events
            WHERE (? IS NULL OR user_id = ?)
            ORDER BY id DESC LIMIT ?
            """,
            (user_id, user_id, max(1, min(limit, 1000))),
        )

    def style_profiles(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._many(
            """
            SELECT p.user_id, u.current_nickname, p.sample_count, p.updated_at
            FROM ecobot_style_profiles p
            LEFT JOIN qq_users u ON u.qq_id = p.user_id
            ORDER BY p.updated_at DESC LIMIT ?
            """,
            (max(1, min(limit, 1000)),),
        )

    def style_examples(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self._many(
            """
            SELECT id, user_id, channel_id, source_id, content, features_json, created_at
            FROM ecobot_style_examples WHERE user_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (str(user_id).strip(), max(1, min(limit, 1000))),
        )

    def update_affinity(
        self, user_id: str, changes: Mapping[str, Any]
    ) -> dict[str, Any]:
        normalized_user_id = str(user_id).strip()
        if not normalized_user_id:
            raise ValueError("QQ 号不能为空")
        allowed = {
            "affinity_score",
            "trust_score",
            "familiarity",
            "special_level",
            "reason",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"不支持的好感字段: {', '.join(sorted(unknown))}")

        values: dict[str, float] = {}
        ranges = {
            "affinity_score": (-100.0, 100.0),
            "trust_score": (-100.0, 100.0),
            "familiarity": (0.0, 100.0),
        }
        for key, (minimum, maximum) in ranges.items():
            if key not in changes:
                continue
            try:
                number = float(changes[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} 必须是数字") from exc
            if not math.isfinite(number) or not minimum <= number <= maximum:
                raise ValueError(f"{key} 必须在 {minimum:g} 到 {maximum:g} 之间")
            values[key] = number
        requested_special_level = changes.get("special_level")
        if requested_special_level is not None:
            requested_special_level = str(requested_special_level).strip().lower()
            if requested_special_level not in SPECIAL_LEVELS:
                raise ValueError("special_level 只支持 none、unforgivable 或 supreme")
        if not values and requested_special_level is None:
            raise ValueError("至少需要调整一项好感数据")

        reason = str(changes.get("reason") or "管理员在 WebUI 手动调整").strip()
        if len(reason) > 500:
            raise ValueError("调整理由不能超过 500 个字符")
        now = datetime.now(timezone.utc).isoformat()
        with self._guard, self._connection:
            row = self._connection.execute(
                """
                SELECT affinity_score, trust_score, familiarity, irritation,
                       special_level, special_reason, special_set_at,
                       interaction_count, positive_interactions,
                       negative_interactions, first_interaction_at,
                       last_interaction_at, updated_at
                FROM ecobot_affinity_profiles WHERE user_id = ?
                """,
                (normalized_user_id,),
            ).fetchone()
            if row is None:
                current_affinity = INITIAL_AFFINITY
                current_trust = INITIAL_TRUST
                current_familiarity = 0.0
                irritation = 0.0
                current_special_level = SPECIAL_LEVEL_NONE
                current_special_reason = current_special_set_at = None
                interaction_count = positive_count = negative_count = 0
                first_interaction_at = last_interaction_at = now
            else:
                current_affinity = float(row[0])
                current_trust = float(row[1])
                current_familiarity = float(row[2])
                irritation = _decayed_irritation(
                    float(row[3]),
                    row[12],
                    datetime.fromisoformat(now),
                    float(self.settings()["affinity_irritation_half_life_hours"]),
                )
                current_special_level = str(row[4] or SPECIAL_LEVEL_NONE)
                current_special_reason = row[5]
                current_special_set_at = row[6]
                interaction_count = int(row[7])
                positive_count = int(row[8])
                negative_count = int(row[9])
                first_interaction_at = row[10]
                last_interaction_at = row[11]

            new_affinity = values.get("affinity_score", current_affinity)
            new_trust = values.get("trust_score", current_trust)
            new_familiarity = values.get("familiarity", current_familiarity)
            new_special_level = requested_special_level or current_special_level
            if requested_special_level == SPECIAL_LEVEL_NONE:
                new_special_reason = new_special_set_at = None
            elif requested_special_level is not None:
                new_special_reason = reason
                new_special_set_at = now
            else:
                new_special_reason = current_special_reason
                new_special_set_at = current_special_set_at
            previous_stage = affinity_stage(current_affinity, current_special_level)
            new_stage = affinity_stage(new_affinity, new_special_level)
            self._connection.execute(
                """
                INSERT INTO ecobot_affinity_profiles(
                    user_id, affinity_score, trust_score, familiarity,
                    irritation, special_level, special_reason, special_set_at,
                    interaction_count, positive_interactions,
                    negative_interactions, last_reason, first_interaction_at,
                    last_interaction_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    affinity_score = excluded.affinity_score,
                    trust_score = excluded.trust_score,
                    familiarity = excluded.familiarity,
                    special_level = excluded.special_level,
                    special_reason = excluded.special_reason,
                    special_set_at = excluded.special_set_at,
                    last_reason = excluded.last_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_user_id,
                    new_affinity,
                    new_trust,
                    new_familiarity,
                    irritation,
                    new_special_level,
                    new_special_reason,
                    new_special_set_at,
                    interaction_count,
                    positive_count,
                    negative_count,
                    reason,
                    first_interaction_at,
                    last_interaction_at,
                    now,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO ecobot_affinity_events(
                    user_id, channel_id, batch_id, raw_affinity_delta,
                    applied_affinity_delta, raw_trust_delta,
                    applied_trust_delta, familiarity_delta, irritation_delta,
                    confidence, reason, previous_stage, new_stage, created_at
                ) VALUES (?, 'manual:webui', NULL, ?, ?, ?, ?, ?, 0, 1, ?, ?, ?, ?)
                """,
                (
                    normalized_user_id,
                    new_affinity - current_affinity,
                    new_affinity - current_affinity,
                    new_trust - current_trust,
                    new_trust - current_trust,
                    new_familiarity - current_familiarity,
                    reason,
                    previous_stage,
                    new_stage,
                    now,
                ),
            )

        profile = self._one(
            """
            SELECT p.user_id, NULL AS current_nickname, p.affinity_score,
                   p.trust_score, p.familiarity, p.irritation,
                   p.special_level, p.special_reason, p.special_set_at,
                   p.interaction_count, p.positive_interactions,
                   p.negative_interactions, p.last_reason,
                   p.first_interaction_at, p.last_interaction_at, p.updated_at
            FROM ecobot_affinity_profiles p
            WHERE p.user_id = ?
            """,
            (normalized_user_id,),
        )
        if profile is None:
            raise RuntimeError("保存后无法读取好感档案")
        return profile | {
            "stage": new_stage,
            "special_locked": profile["special_level"] != SPECIAL_LEVEL_NONE,
        }

    def memories(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._many(
            """
            SELECT id, memory_type, channel_id, user_id, source_type, source_id,
                   content, importance, created_at, last_accessed_at, access_count
            FROM ecobot_memories ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (max(1, min(limit, 1000)),),
        )

    def batches(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._many(
            """
            SELECT batch_id, batch_number, channel_id, trigger, priority, status,
                   created_at, started_at, completed_at, stop_reason, error
            FROM ecobot_behavior_batches ORDER BY created_at DESC LIMIT ?
            """,
            (max(1, min(limit, 1000)),),
        )

    def actions(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._many(
            """
            SELECT id, agent_id, batch_id, channel_id, action_type, description,
                   status, scene, started_at, expected_end_at, completed_at,
                   completion_reason
            FROM ecobot_agent_actions ORDER BY started_at DESC, id DESC LIMIT ?
            """,
            (max(1, min(limit, 1000)),),
        )

    def phase_traces(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._many(
            """
            SELECT id, batch_id, channel_id, phase, provider_id, status,
                   duration_ms, system_prompt, input_prompt, output_text,
                   error, created_at
            FROM ecobot_ai_phase_traces ORDER BY id DESC LIMIT ?
            """,
            (max(1, min(limit, 1000)),),
        )

    def record_phase_trace(
        self,
        *,
        batch_id: str | None,
        channel_id: str | None,
        phase: str,
        provider_id: str | None,
        status: str,
        duration_ms: int,
        system_prompt: str | None,
        input_prompt: str | None,
        output_text: str | None,
        error: str | None,
    ) -> None:
        settings = self.settings()
        if not settings["trace_enabled"]:
            return
        include_prompts = settings["trace_include_prompts"]
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO ecobot_ai_phase_traces(
                    batch_id, channel_id, phase, provider_id, status,
                    duration_ms, system_prompt, input_prompt, output_text,
                    error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    channel_id,
                    phase,
                    provider_id,
                    status,
                    max(0, int(duration_ms)),
                    system_prompt if include_prompts else None,
                    input_prompt if include_prompts else None,
                    output_text,
                    error,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            self._connection.execute(
                """
                DELETE FROM ecobot_ai_phase_traces WHERE id NOT IN (
                    SELECT id FROM ecobot_ai_phase_traces ORDER BY id DESC LIMIT ?
                )
                """,
                (settings["trace_max_records"],),
            )

    def world_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._many(
            """
            SELECT id, channel_id, revision, kind, payload_json, created_at
            FROM ecobot_world_events ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (max(1, min(limit, 1000)),),
        )

    def users(self, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        search = f"%{query.strip()}%" if query and query.strip() else "%"
        return self._many(
            """
            SELECT u.qq_id, u.current_nickname, u.current_avatar_url,
                   u.first_seen_at, u.last_seen_at, u.last_profile_sync_at,
                   (SELECT COUNT(*) FROM qq_messages m
                    WHERE m.sender_qq_id = u.qq_id) AS message_count,
                   (SELECT COUNT(*) FROM qq_group_memberships gm
                    WHERE gm.qq_id = u.qq_id AND gm.left_at IS NULL) AS group_count
            FROM qq_users u
            WHERE u.qq_id LIKE ? OR COALESCE(u.current_nickname, '') LIKE ?
            ORDER BY u.last_seen_at DESC LIMIT ?
            """,
            (search, search, max(1, min(limit, 1000))),
        )

    def groups(self, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        search = f"%{query.strip()}%" if query and query.strip() else "%"
        return self._many(
            """
            SELECT g.group_id, g.current_name, g.current_avatar_url,
                   g.current_owner_qq_id, g.member_count, g.max_member_count,
                   g.first_seen_at, g.last_seen_at, g.last_snapshot_at,
                   (SELECT COUNT(*) FROM qq_messages m
                    WHERE m.group_id = g.group_id) AS message_count
            FROM qq_groups g
            WHERE g.group_id LIKE ? OR COALESCE(g.current_name, '') LIKE ?
            ORDER BY g.last_seen_at DESC LIMIT ?
            """,
            (search, search, max(1, min(limit, 1000))),
        )

    def messages(
        self,
        query: str | None = None,
        group_id: str | None = None,
        sender_qq_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        search = f"%{query.strip()}%" if query and query.strip() else "%"
        return self._many(
            """
            SELECT m.id, m.platform_message_id, m.message_type, m.group_id,
                   m.sender_qq_id, m.sender_nickname, m.sender_group_card,
                   m.sender_role, m.content_text, m.content_outline, m.sent_at,
                   m.observed_at, m.recalled_at, g.current_name AS group_name
            FROM qq_messages m
            LEFT JOIN qq_groups g ON g.group_id = m.group_id
            WHERE (m.content_text LIKE ? OR m.content_outline LIKE ?)
              AND (? IS NULL OR m.group_id = ?)
              AND (? IS NULL OR m.sender_qq_id = ?)
            ORDER BY COALESCE(m.sent_at, 0) DESC, m.id DESC LIMIT ?
            """,
            (
                search,
                search,
                group_id,
                group_id,
                sender_qq_id,
                sender_qq_id,
                max(1, min(limit, 1000)),
            ),
        )

    def _count(self, table: str) -> int:
        with self._guard:
            try:
                return int(self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.OperationalError:
                return 0

    def _one(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._guard:
            try:
                row = self._connection.execute(sql, params).fetchone()
            except sqlite3.OperationalError:
                return None
        return dict(row) if row else None

    def _many(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._guard:
            try:
                rows = self._connection.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                return []
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._guard:
            self._connection.close()


def _affinity_stage(score: float, trust: float, familiarity: float) -> str:
    return affinity_stage(score)


def _decayed_irritation(
    value: float,
    updated_at: str,
    now: datetime,
    half_life_hours: float,
) -> float:
    try:
        previous = datetime.fromisoformat(updated_at)
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        elapsed_hours = max(0.0, (now - previous).total_seconds() / 3600.0)
    except (TypeError, ValueError):
        elapsed_hours = 0.0
    return value * math.pow(0.5, elapsed_hours / max(0.1, half_life_hours))
