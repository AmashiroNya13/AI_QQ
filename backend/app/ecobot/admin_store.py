from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "persona_enabled": True,
    "persona_base_prompt": "",
    "persona_growth_enabled": True,
    "persona_growth_auto_activate": True,
    "persona_growth_min_confidence": 0.75,
    "persona_growth_min_evidence": 3,
    "persona_growth_limit": 12,
    "passive_interval_seconds": 15,
    "idle_interval_seconds": 600,
    "idle_poll_seconds": 5,
    "idle_allow_proactive_expression": True,
    "idle_prompt": (
        "当前没有新消息。结合当前场景、持续维护的人物状态和人格，判断是否需要采取"
        "自然且连贯的主动行为；保持沉默也是有效选择。"
    ),
    "memory_reconstruction_enabled": True,
    "memory_reconstruction_limit": 8,
    "memory_reconstruction_hops": 2,
    "structured_output_retries": 1,
    "model_timeout_seconds": 90,
    "request_max_retries": 2,
    "generation_temperature": 0.4,
    "generation_top_p": 0.9,
    "generation_max_tokens": 1200,
    "anti_repeat_window_minutes": 360,
    "anti_repeat_fuzzy_threshold": 0.92,
    "anti_repeat_semantic_threshold": 0.94,
    "reply_style_repeat_enabled": True,
    "reply_style_window_minutes": 60,
    "reply_style_repeat_limit": 1,
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
}

BOOLEAN_SETTINGS = {
    "enabled",
    "persona_enabled",
    "persona_growth_enabled",
    "persona_growth_auto_activate",
    "idle_allow_proactive_expression",
    "memory_reconstruction_enabled",
    "reply_style_repeat_enabled",
    "relationship_ai_enabled",
    "affinity_enabled",
    "trace_enabled",
    "trace_include_prompts",
    "debug_log_enabled",
}
INTEGER_RANGES = {
    "affinity_rules_version": (1, 2),
    "passive_interval_seconds": (0, 86400),
    "idle_interval_seconds": (1, 604800),
    "idle_poll_seconds": (1, 3600),
    "memory_reconstruction_limit": (1, 100),
    "memory_reconstruction_hops": (0, 4),
    "structured_output_retries": (0, 5),
    "model_timeout_seconds": (5, 600),
    "request_max_retries": (0, 10),
    "generation_max_tokens": (64, 32768),
    "anti_repeat_window_minutes": (1, 43200),
    "reply_style_window_minutes": (1, 43200),
    "reply_style_repeat_limit": (1, 20),
    "persona_growth_min_evidence": (1, 100),
    "persona_growth_limit": (1, 100),
    "relationship_scan_interval_seconds": (60, 604800),
    "relationship_evaluation_interval_hours": (1, 8760),
    "relationship_minimum_new_evidence": (1, 10000),
    "trace_max_records": (100, 100000),
}
FLOAT_RANGES = {
    "persona_growth_min_confidence": (0.0, 1.0),
    "generation_temperature": (0.0, 2.0),
    "generation_top_p": (0.0, 1.0),
    "anti_repeat_fuzzy_threshold": (0.0, 1.0),
    "anti_repeat_semantic_threshold": (0.0, 1.0),
    "affinity_positive_step_limit": (0.1, 20.0),
    "affinity_negative_step_limit": (0.1, 30.0),
    "affinity_irritation_half_life_hours": (0.1, 720.0),
}
STRING_SETTINGS = {
    "idle_prompt",
    "persona_base_prompt",
    "relationship_provider_id",
    "relationship_prompt",
}
ENUM_SETTINGS = set()
LIST_SETTINGS = set()

ACTIVE_SETTINGS = set(DEFAULT_SETTINGS)


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
        stored = json.loads(row[0])
        value = {
            **{key: DEFAULT_SETTINGS[key] for key in ACTIVE_SETTINGS},
            **{key: value for key, value in stored.items() if key in ACTIVE_SETTINGS},
        }
        value["updated_at"] = row[1]
        return value

    def update_settings(self, changes: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(changes) - ACTIVE_SETTINGS
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
            "ecobot2_memory_episodes",
            "ecobot2_memory_retrievals",
            "ecobot2_persona_increments",
            "ecobot2_temporal_relations",
            "ecobot2_grievances",
        ):
            counts[table] = self._count(table)
        with self._guard:
            schema = self._connection.execute(
                "SELECT version FROM ecobot_schema_versions WHERE component = 'qq_archive'"
            ).fetchone()
            try:
                latest = self._connection.execute(
                    """
                    SELECT event_id AS batch_id, channel_id, kind AS trigger,
                           'completed' AS status, occurred_at AS created_at,
                           occurred_at AS completed_at, NULL AS stop_reason,
                           NULL AS error
                    FROM ecobot2_events ORDER BY occurred_at DESC LIMIT 1
                    """
                ).fetchone()
            except sqlite3.OperationalError:
                latest = None
        return {
            "schema_version": int(schema[0]) if schema else None,
            "counts": counts,
            "latest_batch": dict(latest) if latest else None,
            "settings": self.settings(),
        }

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
