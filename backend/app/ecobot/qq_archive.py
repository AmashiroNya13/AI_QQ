from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    data = getattr(value, "__dict__", None)
    if isinstance(data, dict):
        return _jsonable(data)
    return repr(value)


def _json(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, separators=(",", ":"))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _integer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _compact_prompt_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:500]
    if isinstance(value, Mapping):
        return {
            str(key): _compact_prompt_value(item, depth=depth + 1)
            for key, item in value.items()
            if item not in (None, "", [], {})
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_compact_prompt_value(item, depth=depth + 1) for item in value[:30]]
    if isinstance(value, str):
        return value[:1000]
    return value


def _loaded_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return _compact_prompt_value(parsed) if isinstance(parsed, Mapping) else {}


class QQArchive:
    """Append-first QQ fact archive built from OneBot events and snapshots."""

    def __init__(self, database_path: str | Path) -> None:
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._guard = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ecobot_schema_versions (
                    component TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS qq_bot_accounts (
                    qq_id TEXT PRIMARY KEY,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS qq_users (
                    qq_id TEXT PRIMARY KEY,
                    current_nickname TEXT,
                    current_avatar_url TEXT,
                    current_avatar_hash TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_profile_sync_at TEXT,
                    raw_profile_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_qq_users_last_seen
                    ON qq_users(last_seen_at DESC);

                CREATE TABLE IF NOT EXISTS qq_user_profile_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    qq_id TEXT NOT NULL REFERENCES qq_users(qq_id),
                    observed_at TEXT NOT NULL,
                    profile_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_qq_user_profile_snapshots
                    ON qq_user_profile_snapshots(qq_id, observed_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS qq_binary_assets (
                    content_hash TEXT PRIMARY KEY,
                    media_type TEXT NOT NULL,
                    mime_type TEXT,
                    byte_size INTEGER NOT NULL,
                    content BLOB NOT NULL,
                    source_url TEXT,
                    first_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS qq_user_nickname_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    qq_id TEXT NOT NULL REFERENCES qq_users(qq_id),
                    nickname TEXT NOT NULL,
                    source TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    raw_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_qq_user_nickname_history
                    ON qq_user_nickname_history(qq_id, valid_from DESC);

                CREATE TABLE IF NOT EXISTS qq_user_avatar_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    qq_id TEXT NOT NULL REFERENCES qq_users(qq_id),
                    avatar_url TEXT NOT NULL,
                    content_hash TEXT,
                    etag TEXT,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    raw_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_qq_user_avatar_history
                    ON qq_user_avatar_history(qq_id, valid_from DESC);

                CREATE TABLE IF NOT EXISTS qq_groups (
                    group_id TEXT PRIMARY KEY,
                    current_name TEXT,
                    current_avatar_url TEXT,
                    current_avatar_hash TEXT,
                    current_owner_qq_id TEXT,
                    member_count INTEGER,
                    max_member_count INTEGER,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_snapshot_at TEXT,
                    raw_profile_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_qq_groups_last_seen
                    ON qq_groups(last_seen_at DESC);

                CREATE TABLE IF NOT EXISTS qq_group_observations (
                    group_id TEXT NOT NULL REFERENCES qq_groups(group_id),
                    bot_qq_id TEXT NOT NULL REFERENCES qq_bot_accounts(qq_id),
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY(group_id, bot_qq_id)
                );

                CREATE TABLE IF NOT EXISTS qq_group_name_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL REFERENCES qq_groups(group_id),
                    group_name TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    source TEXT NOT NULL,
                    raw_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_qq_group_name_history
                    ON qq_group_name_history(group_id, valid_from DESC);

                CREATE TABLE IF NOT EXISTS qq_group_avatar_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL REFERENCES qq_groups(group_id),
                    avatar_url TEXT NOT NULL,
                    content_hash TEXT,
                    etag TEXT,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    raw_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_qq_group_avatar_history
                    ON qq_group_avatar_history(group_id, valid_from DESC);

                CREATE TABLE IF NOT EXISTS qq_group_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL REFERENCES qq_groups(group_id),
                    bot_qq_id TEXT NOT NULL REFERENCES qq_bot_accounts(qq_id),
                    owner_qq_id TEXT,
                    member_count INTEGER,
                    observed_at TEXT NOT NULL,
                    group_json TEXT NOT NULL,
                    members_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_qq_group_snapshots
                    ON qq_group_snapshots(group_id, observed_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS qq_group_memberships (
                    group_id TEXT NOT NULL REFERENCES qq_groups(group_id),
                    qq_id TEXT NOT NULL REFERENCES qq_users(qq_id),
                    current_group_card TEXT,
                    current_role TEXT NOT NULL DEFAULT 'member',
                    current_title TEXT,
                    level TEXT,
                    join_time INTEGER,
                    last_sent_time INTEGER,
                    shut_up_until INTEGER,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    left_at TEXT,
                    raw_member_json TEXT,
                    PRIMARY KEY(group_id, qq_id)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_group_memberships_user
                    ON qq_group_memberships(qq_id, group_id);
                CREATE INDEX IF NOT EXISTS idx_qq_group_memberships_role
                    ON qq_group_memberships(group_id, current_role);

                CREATE TABLE IF NOT EXISTS qq_group_membership_episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    qq_id TEXT NOT NULL,
                    joined_at TEXT NOT NULL,
                    left_at TEXT,
                    join_source TEXT NOT NULL,
                    leave_source TEXT,
                    join_raw_json TEXT NOT NULL,
                    leave_raw_json TEXT,
                    FOREIGN KEY(group_id, qq_id)
                        REFERENCES qq_group_memberships(group_id, qq_id)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_membership_episodes_pair
                    ON qq_group_membership_episodes(group_id, qq_id, joined_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS qq_group_member_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    qq_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    member_json TEXT NOT NULL,
                    FOREIGN KEY(group_id, qq_id)
                        REFERENCES qq_group_memberships(group_id, qq_id)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_group_member_snapshots
                    ON qq_group_member_snapshots(group_id, qq_id, observed_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS qq_group_member_name_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    qq_id TEXT NOT NULL,
                    group_card TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    source TEXT NOT NULL,
                    raw_json TEXT,
                    FOREIGN KEY(group_id, qq_id)
                        REFERENCES qq_group_memberships(group_id, qq_id)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_group_member_name_history
                    ON qq_group_member_name_history(group_id, qq_id, valid_from DESC);

                CREATE TABLE IF NOT EXISTS qq_group_role_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    qq_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    source TEXT NOT NULL,
                    raw_json TEXT,
                    FOREIGN KEY(group_id, qq_id)
                        REFERENCES qq_group_memberships(group_id, qq_id)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_group_role_history
                    ON qq_group_role_history(group_id, qq_id, valid_from DESC);

                CREATE TABLE IF NOT EXISTS qq_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_qq_id TEXT NOT NULL REFERENCES qq_bot_accounts(qq_id),
                    event_key TEXT NOT NULL,
                    post_type TEXT NOT NULL,
                    detail_type TEXT,
                    sub_type TEXT,
                    group_id TEXT REFERENCES qq_groups(group_id),
                    actor_qq_id TEXT NOT NULL REFERENCES qq_users(qq_id),
                    occurred_at INTEGER,
                    observed_at TEXT NOT NULL,
                    raw_event_json TEXT NOT NULL,
                    UNIQUE(bot_qq_id, event_key)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_events_group_time
                    ON qq_events(group_id, occurred_at, id);
                CREATE INDEX IF NOT EXISTS idx_qq_events_actor_time
                    ON qq_events(actor_qq_id, occurred_at, id);

                CREATE TABLE IF NOT EXISTS qq_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_qq_id TEXT NOT NULL REFERENCES qq_bot_accounts(qq_id),
                    platform_message_id TEXT NOT NULL,
                    message_seq TEXT,
                    message_type TEXT NOT NULL,
                    sub_type TEXT,
                    group_id TEXT REFERENCES qq_groups(group_id),
                    sender_qq_id TEXT NOT NULL REFERENCES qq_users(qq_id),
                    sender_nickname TEXT,
                    sender_group_card TEXT,
                    sender_role TEXT,
                    content_text TEXT NOT NULL,
                    content_outline TEXT NOT NULL,
                    segments_json TEXT NOT NULL,
                    raw_event_json TEXT NOT NULL,
                    reply_to_message_id TEXT,
                    sent_at INTEGER,
                    observed_at TEXT NOT NULL,
                    recalled_at TEXT,
                    UNIQUE(bot_qq_id, platform_message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_messages_group_time
                    ON qq_messages(group_id, sent_at, id);
                CREATE INDEX IF NOT EXISTS idx_qq_messages_sender_time
                    ON qq_messages(sender_qq_id, sent_at, id);
                CREATE INDEX IF NOT EXISTS idx_qq_messages_group_sender_time
                    ON qq_messages(group_id, sender_qq_id, sent_at, id);

                CREATE TABLE IF NOT EXISTS qq_outbound_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    platform_name TEXT NOT NULL,
                    group_id TEXT,
                    target_qq_id TEXT,
                    content_text TEXT NOT NULL,
                    segments_json TEXT NOT NULL,
                    batch_id TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    attempted_at TEXT NOT NULL,
                    sent_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_qq_outbound_messages_channel
                    ON qq_outbound_messages(channel_id, attempted_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS qq_message_media (
                    message_id INTEGER NOT NULL REFERENCES qq_messages(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    source_url TEXT,
                    source_file TEXT,
                    content_hash TEXT REFERENCES qq_binary_assets(content_hash),
                    fetch_status TEXT NOT NULL,
                    error TEXT,
                    observed_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY(message_id, position)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_message_media_hash
                    ON qq_message_media(content_hash);

                CREATE TABLE IF NOT EXISTS qq_notice_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_qq_id TEXT NOT NULL REFERENCES qq_bot_accounts(qq_id),
                    event_key TEXT NOT NULL,
                    notice_category TEXT NOT NULL,
                    notice_type TEXT,
                    sub_type TEXT,
                    group_id TEXT,
                    actor_qq_id TEXT,
                    target_qq_id TEXT,
                    occurred_at INTEGER,
                    observed_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    UNIQUE(bot_qq_id, event_key, notice_category)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_notice_facts_group_time
                    ON qq_notice_facts(group_id, observed_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS qq_friendships (
                    bot_qq_id TEXT NOT NULL REFERENCES qq_bot_accounts(qq_id),
                    qq_id TEXT NOT NULL REFERENCES qq_users(qq_id),
                    current_remark TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    removed_at TEXT,
                    raw_json TEXT NOT NULL,
                    PRIMARY KEY(bot_qq_id, qq_id)
                );
                CREATE TABLE IF NOT EXISTS qq_friend_inventory_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_qq_id TEXT NOT NULL REFERENCES qq_bot_accounts(qq_id),
                    observed_at TEXT NOT NULL,
                    friends_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS qq_group_inventory_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_qq_id TEXT NOT NULL REFERENCES qq_bot_accounts(qq_id),
                    observed_at TEXT NOT NULL,
                    groups_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS qq_message_segments (
                    message_id INTEGER NOT NULL REFERENCES qq_messages(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    segment_type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY(message_id, position)
                );

                CREATE TABLE IF NOT EXISTS qq_message_mentions (
                    message_id INTEGER NOT NULL REFERENCES qq_messages(id) ON DELETE CASCADE,
                    mentioned_qq_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    PRIMARY KEY(message_id, position)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_message_mentions_user
                    ON qq_message_mentions(mentioned_qq_id, message_id);

                CREATE TABLE IF NOT EXISTS qq_relationships (
                    person_a_qq_id TEXT NOT NULL REFERENCES qq_users(qq_id),
                    person_b_qq_id TEXT NOT NULL REFERENCES qq_users(qq_id),
                    first_interaction_at TEXT NOT NULL,
                    last_interaction_at TEXT NOT NULL,
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    last_evaluated_evidence_id INTEGER,
                    last_evaluated_at TEXT,
                    current_relation_type TEXT,
                    current_summary TEXT,
                    interaction_strength REAL NOT NULL DEFAULT 0,
                    reciprocity REAL NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0,
                    current_metrics_json TEXT,
                    PRIMARY KEY(person_a_qq_id, person_b_qq_id),
                    CHECK(person_a_qq_id < person_b_qq_id)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_relationships_recent
                    ON qq_relationships(last_interaction_at DESC);

                CREATE TABLE IF NOT EXISTS qq_relationship_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_a_qq_id TEXT NOT NULL,
                    person_b_qq_id TEXT NOT NULL,
                    source_qq_id TEXT NOT NULL REFERENCES qq_users(qq_id),
                    target_qq_id TEXT NOT NULL REFERENCES qq_users(qq_id),
                    group_id TEXT REFERENCES qq_groups(group_id),
                    message_id INTEGER NOT NULL REFERENCES qq_messages(id) ON DELETE CASCADE,
                    evidence_type TEXT NOT NULL,
                    weight REAL NOT NULL,
                    observed_at TEXT NOT NULL,
                    detail_json TEXT NOT NULL,
                    FOREIGN KEY(person_a_qq_id, person_b_qq_id)
                        REFERENCES qq_relationships(person_a_qq_id, person_b_qq_id),
                    UNIQUE(message_id, evidence_type, source_qq_id, target_qq_id)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_relationship_evidence_pair
                    ON qq_relationship_evidence(
                        person_a_qq_id, person_b_qq_id, id DESC
                    );
                CREATE INDEX IF NOT EXISTS idx_qq_relationship_evidence_direction
                    ON qq_relationship_evidence(source_qq_id, target_qq_id, id DESC);

                CREATE TABLE IF NOT EXISTS qq_relationship_evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_a_qq_id TEXT NOT NULL,
                    person_b_qq_id TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    evidence_from_id INTEGER NOT NULL,
                    evidence_to_id INTEGER NOT NULL,
                    new_evidence_count INTEGER NOT NULL,
                    total_evidence_count INTEGER NOT NULL,
                    relation_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    interaction_strength REAL NOT NULL,
                    reciprocity REAL NOT NULL,
                    confidence REAL NOT NULL,
                    metrics_json TEXT NOT NULL,
                    evaluator TEXT NOT NULL,
                    FOREIGN KEY(person_a_qq_id, person_b_qq_id)
                        REFERENCES qq_relationships(person_a_qq_id, person_b_qq_id)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_relationship_evaluations_pair
                    ON qq_relationship_evaluations(
                        person_a_qq_id, person_b_qq_id, evaluated_at DESC, id DESC
                    );

                CREATE TABLE IF NOT EXISTS qq_relationship_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_a_qq_id TEXT NOT NULL,
                    person_b_qq_id TEXT NOT NULL,
                    group_id TEXT REFERENCES qq_groups(group_id),
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_evidence_from_id INTEGER NOT NULL,
                    source_evidence_to_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    superseded_at TEXT,
                    FOREIGN KEY(person_a_qq_id, person_b_qq_id)
                        REFERENCES qq_relationships(person_a_qq_id, person_b_qq_id)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_relationship_memories_pair
                    ON qq_relationship_memories(
                        person_a_qq_id, person_b_qq_id, created_at DESC, id DESC
                    );

                CREATE TABLE IF NOT EXISTS qq_qzone_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    qq_id TEXT NOT NULL REFERENCES qq_users(qq_id),
                    post_id TEXT NOT NULL,
                    post_type TEXT,
                    content_text TEXT NOT NULL,
                    created_at INTEGER,
                    observed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    raw_post_json TEXT NOT NULL,
                    deleted_at TEXT,
                    UNIQUE(qq_id, post_id)
                );
                CREATE INDEX IF NOT EXISTS idx_qq_qzone_posts_user_time
                    ON qq_qzone_posts(qq_id, created_at DESC, id DESC);

                CREATE TABLE IF NOT EXISTS qq_qzone_post_media (
                    post_row_id INTEGER NOT NULL REFERENCES qq_qzone_posts(id) ON DELETE CASCADE,
                    position INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    media_url TEXT,
                    content_hash TEXT,
                    raw_json TEXT NOT NULL,
                    PRIMARY KEY(post_row_id, position)
                );

                CREATE TABLE IF NOT EXISTS qq_sync_state (
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    cursor TEXT,
                    last_attempt_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT,
                    raw_state_json TEXT,
                    PRIMARY KEY(resource_type, resource_id)
                );
                """
            )
            self._connection.execute(
                """
                INSERT INTO ecobot_schema_versions(component, version, applied_at)
                VALUES ('qq_archive', 3, ?)
                ON CONFLICT(component) DO UPDATE SET
                    version = excluded.version,
                    applied_at = excluded.applied_at
                """,
                (_utc_now(),),
            )
            try:
                self._connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS qq_messages_fts "
                    "USING fts5(content_text, content_outline, sender_nickname, sender_group_card)"
                )
                self._connection.execute(
                    "INSERT INTO qq_messages_fts(rowid, content_text, content_outline, sender_nickname, sender_group_card) "
                    "SELECT id, content_text, content_outline, sender_nickname, sender_group_card "
                    "FROM qq_messages WHERE id NOT IN (SELECT rowid FROM qq_messages_fts)"
                )
                self._fts_enabled = True
            except sqlite3.OperationalError:
                self._fts_enabled = False
            self._connection.execute(
                """
                INSERT INTO qq_group_membership_episodes(
                    group_id, qq_id, joined_at, join_source, join_raw_json
                )
                SELECT m.group_id, m.qq_id, m.first_seen_at, 'schema_migration',
                       COALESCE(m.raw_member_json, '{}')
                FROM qq_group_memberships m
                WHERE m.left_at IS NULL AND NOT EXISTS (
                    SELECT 1 FROM qq_group_membership_episodes e
                    WHERE e.group_id = m.group_id AND e.qq_id = m.qq_id
                      AND e.left_at IS NULL
                )
                """
            )

    def record_event(self, event: Any) -> bool:
        if event.get_platform_name() != "aiocqhttp":
            return False
        message_obj = event.message_obj
        raw = _mapping(getattr(message_obj, "raw_message", None))
        observed_at = _utc_now()
        bot_qq_id = _text(getattr(message_obj, "self_id", None)) or _text(
            raw.get("self_id")
        )
        sender_qq_id = _text(event.get_sender_id()) or _text(raw.get("user_id"))
        if not bot_qq_id or not sender_qq_id:
            return False

        sender = _mapping(raw.get("sender"))
        post_type = _text(raw.get("post_type"))
        group_id = _text(event.get_group_id()) or _text(raw.get("group_id"))
        global_nickname = _text(sender.get("nickname"))
        group_card = _text(sender.get("card"))
        visible_name = _text(event.get_sender_name())
        role = _text(sender.get("role")) or "member"
        group_name = _text(raw.get("group_name"))
        group_obj = getattr(message_obj, "group", None)
        if group_name is None and group_obj is not None:
            group_name = _text(getattr(group_obj, "group_name", None))

        with self._guard, self._connection:
            self._upsert_bot(bot_qq_id, observed_at)
            self._upsert_user(
                sender_qq_id,
                global_nickname or (visible_name if not group_id else None),
                observed_at,
                "message_event",
                sender,
            )
            if group_id:
                self._upsert_group(group_id, group_name, observed_at, "message_event", raw)
                self._observe_group(group_id, bot_qq_id, observed_at)
            if group_id and post_type != "notice":
                self._upsert_membership(
                    group_id,
                    sender_qq_id,
                    group_card,
                    role,
                    _text(sender.get("title")),
                    observed_at,
                    "message_event",
                    sender,
                )

            self._record_raw_event(
                message_obj,
                raw,
                bot_qq_id,
                sender_qq_id,
                group_id,
                post_type or "message",
                observed_at,
            )

            if post_type == "notice":
                self._record_notice(raw, bot_qq_id, observed_at)
                return True
            if post_type and post_type != "message":
                return True
            return self._record_message(
                event,
                raw,
                bot_qq_id,
                sender_qq_id,
                group_id,
                global_nickname or visible_name,
                group_card,
                role,
                observed_at,
            )

    def record_outbound_message(
        self,
        *,
        channel_id: str,
        content: str,
        batch_id: str | None,
        success: bool,
        error: str | None = None,
        platform_name: str = "aiocqhttp",
        group_id: str | None = None,
        target_qq_id: str | None = None,
        segments: Sequence[Mapping[str, Any]] = (),
    ) -> int:
        attempted_at = _utc_now()
        with self._guard, self._connection:
            cursor = self._connection.execute(
                """
                INSERT INTO qq_outbound_messages(
                    channel_id, platform_name, group_id, target_qq_id,
                    content_text, segments_json, batch_id, status,
                    error, attempted_at, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    platform_name,
                    group_id,
                    target_qq_id,
                    content,
                    _json(segments),
                    batch_id,
                    "sent" if success else "failed",
                    error,
                    attempted_at,
                    attempted_at if success else None,
                ),
            )
            return int(cursor.lastrowid)

    def _record_raw_event(
        self,
        message_obj: Any,
        raw: dict[str, Any],
        bot_qq_id: str,
        actor_qq_id: str,
        group_id: str | None,
        post_type: str,
        observed_at: str,
    ) -> None:
        event_key = _text(getattr(message_obj, "message_id", None))
        if event_key is None:
            event_key = f"{post_type}:{_text(raw.get('time')) or observed_at}:{actor_qq_id}"
        detail_type = _text(raw.get(f"{post_type}_type"))
        self._connection.execute(
            """
            INSERT OR IGNORE INTO qq_events(
                bot_qq_id, event_key, post_type, detail_type, sub_type,
                group_id, actor_qq_id, occurred_at, observed_at, raw_event_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bot_qq_id,
                event_key,
                post_type,
                detail_type,
                _text(raw.get("sub_type")),
                group_id,
                actor_qq_id,
                _integer(raw.get("time")),
                observed_at,
                _json(raw),
            ),
        )

    def _upsert_bot(self, qq_id: str, observed_at: str) -> None:
        self._connection.execute(
            """
            INSERT INTO qq_bot_accounts(qq_id, first_seen_at, last_seen_at)
            VALUES (?, ?, ?)
            ON CONFLICT(qq_id) DO UPDATE SET last_seen_at = excluded.last_seen_at
            """,
            (qq_id, observed_at, observed_at),
        )

    def _upsert_user(
        self,
        qq_id: str,
        nickname: str | None,
        observed_at: str,
        source: str,
        raw: Any,
    ) -> None:
        row = self._connection.execute(
            "SELECT current_nickname FROM qq_users WHERE qq_id = ?", (qq_id,)
        ).fetchone()
        if row is None:
            self._connection.execute(
                """
                INSERT INTO qq_users(
                    qq_id, current_nickname, first_seen_at, last_seen_at, raw_profile_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (qq_id, nickname, observed_at, observed_at, _json(raw)),
            )
        else:
            self._connection.execute(
                """
                UPDATE qq_users SET
                    current_nickname = COALESCE(?, current_nickname),
                    last_seen_at = ?,
                    raw_profile_json = CASE WHEN ? != '{}' THEN ? ELSE raw_profile_json END
                WHERE qq_id = ?
                """,
                (nickname, observed_at, _json(raw), _json(raw), qq_id),
            )
        if nickname and (row is None or row[0] != nickname):
            self._close_history("qq_user_nickname_history", "qq_id", qq_id, observed_at)
            self._connection.execute(
                """
                INSERT INTO qq_user_nickname_history(
                    qq_id, nickname, source, valid_from, raw_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (qq_id, nickname, source, observed_at, _json(raw)),
            )

    def _upsert_group(
        self,
        group_id: str,
        group_name: str | None,
        observed_at: str,
        source: str,
        raw: Any,
    ) -> None:
        row = self._connection.execute(
            "SELECT current_name FROM qq_groups WHERE group_id = ?", (group_id,)
        ).fetchone()
        if row is None:
            self._connection.execute(
                """
                INSERT INTO qq_groups(
                    group_id, current_name, first_seen_at, last_seen_at, raw_profile_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (group_id, group_name, observed_at, observed_at, _json(raw)),
            )
        else:
            self._connection.execute(
                """
                UPDATE qq_groups SET
                    current_name = COALESCE(?, current_name),
                    last_seen_at = ?, raw_profile_json = ?
                WHERE group_id = ?
                """,
                (group_name, observed_at, _json(raw), group_id),
            )
        if group_name and (row is None or row[0] != group_name):
            self._close_history("qq_group_name_history", "group_id", group_id, observed_at)
            self._connection.execute(
                """
                INSERT INTO qq_group_name_history(
                    group_id, group_name, valid_from, source, raw_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (group_id, group_name, observed_at, source, _json(raw)),
            )

    def _observe_group(self, group_id: str, bot_qq_id: str, observed_at: str) -> None:
        self._connection.execute(
            """
            INSERT INTO qq_group_observations(
                group_id, bot_qq_id, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(group_id, bot_qq_id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at
            """,
            (group_id, bot_qq_id, observed_at, observed_at),
        )

    def _upsert_membership(
        self,
        group_id: str,
        qq_id: str,
        group_card: str | None,
        role: str,
        title: str | None,
        observed_at: str,
        source: str,
        raw: Any,
    ) -> None:
        row = self._connection.execute(
            """
            SELECT current_group_card, current_role, left_at
            FROM qq_group_memberships WHERE group_id = ? AND qq_id = ?
            """,
            (group_id, qq_id),
        ).fetchone()
        self._connection.execute(
            """
            INSERT INTO qq_group_memberships(
                group_id, qq_id, current_group_card, current_role, current_title,
                first_seen_at, last_seen_at, raw_member_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id, qq_id) DO UPDATE SET
                current_group_card = excluded.current_group_card,
                current_role = excluded.current_role,
                current_title = COALESCE(excluded.current_title, current_title),
                last_seen_at = excluded.last_seen_at,
                left_at = NULL,
                raw_member_json = excluded.raw_member_json
            """,
            (
                group_id,
                qq_id,
                group_card,
                role,
                title,
                observed_at,
                observed_at,
                _json(raw),
            ),
        )
        if row is None or row[2] is not None:
            self._connection.execute(
                """
                INSERT INTO qq_group_membership_episodes(
                    group_id, qq_id, joined_at, join_source, join_raw_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (group_id, qq_id, observed_at, source, _json(raw)),
            )
        if row is None or row[0] != group_card:
            self._close_member_history(
                "qq_group_member_name_history", group_id, qq_id, observed_at
            )
            if group_card:
                self._connection.execute(
                    """
                    INSERT INTO qq_group_member_name_history(
                        group_id, qq_id, group_card, valid_from, source, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (group_id, qq_id, group_card, observed_at, source, _json(raw)),
                )
        if row is None or row[1] != role:
            self._close_member_history(
                "qq_group_role_history", group_id, qq_id, observed_at
            )
            self._connection.execute(
                """
                INSERT INTO qq_group_role_history(
                    group_id, qq_id, role, valid_from, source, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (group_id, qq_id, role, observed_at, source, _json(raw)),
            )

    def _record_message(
        self,
        event: Any,
        raw: dict[str, Any],
        bot_qq_id: str,
        sender_qq_id: str,
        group_id: str | None,
        nickname: str | None,
        group_card: str | None,
        role: str,
        observed_at: str,
    ) -> bool:
        message_obj = event.message_obj
        platform_message_id = _text(getattr(message_obj, "message_id", None))
        if not platform_message_id:
            return False
        segments = raw.get("message")
        if not isinstance(segments, list):
            segments = []
        reply_to = None
        for segment in segments:
            segment_data = _mapping(segment)
            if segment_data.get("type") == "reply":
                reply_to = _text(_mapping(segment_data.get("data")).get("id"))
                break
        sent_at = _integer(raw.get("time")) or _integer(
            getattr(message_obj, "timestamp", None)
        )
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO qq_messages(
                bot_qq_id, platform_message_id, message_seq, message_type, sub_type,
                group_id, sender_qq_id, sender_nickname, sender_group_card, sender_role,
                content_text, content_outline, segments_json, raw_event_json,
                reply_to_message_id, sent_at, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bot_qq_id,
                platform_message_id,
                _text(raw.get("message_seq")),
                _text(raw.get("message_type")) or str(event.get_message_type()),
                _text(raw.get("sub_type")),
                group_id,
                sender_qq_id,
                nickname,
                group_card,
                role,
                event.get_message_str() or "",
                event.get_message_outline() or "",
                _json(segments),
                _json(raw),
                reply_to,
                sent_at,
                observed_at,
            ),
        )
        if cursor.rowcount == 0:
            return False
        message_row_id = int(cursor.lastrowid)
        if getattr(self, "_fts_enabled", False):
            self._connection.execute(
                """
                INSERT OR REPLACE INTO qq_messages_fts(
                    rowid, content_text, content_outline,
                    sender_nickname, sender_group_card
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    message_row_id,
                    event.get_message_str() or "",
                    event.get_message_outline() or "",
                    nickname,
                    group_card,
                ),
            )
        for position, segment in enumerate(segments):
            segment_data = _mapping(segment)
            kind = _text(segment_data.get("type")) or "unknown"
            data = _mapping(segment_data.get("data"))
            self._connection.execute(
                """
                INSERT INTO qq_message_segments(
                    message_id, position, segment_type, data_json
                ) VALUES (?, ?, ?, ?)
                """,
                (message_row_id, position, kind, _json(data)),
            )
            if kind == "at" and (mentioned := _text(data.get("qq"))):
                self._connection.execute(
                    """
                    INSERT INTO qq_message_mentions(message_id, mentioned_qq_id, position)
                    VALUES (?, ?, ?)
                    """,
                    (message_row_id, mentioned, position),
                )
                if mentioned not in {"all", bot_qq_id, sender_qq_id}:
                    self._record_relationship_evidence(
                        source_qq_id=sender_qq_id,
                        target_qq_id=mentioned,
                        group_id=group_id,
                        message_row_id=message_row_id,
                        evidence_type="mention",
                        weight=1.0,
                        observed_at=observed_at,
                        detail={"position": position},
                    )
            if kind in {"image", "record", "video", "file"}:
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO qq_message_media(
                        message_id, position, media_type, source_url, source_file,
                        fetch_status, observed_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        message_row_id,
                        position,
                        kind,
                        _text(data.get("url")),
                        _text(data.get("file")),
                        observed_at,
                        _json(data),
                    ),
                )
        if reply_to:
            replied = self._connection.execute(
                """
                SELECT id, sender_qq_id FROM qq_messages
                WHERE bot_qq_id = ? AND platform_message_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (bot_qq_id, reply_to),
            ).fetchone()
            if replied and replied[1] not in {bot_qq_id, sender_qq_id}:
                self._record_relationship_evidence(
                    source_qq_id=sender_qq_id,
                    target_qq_id=str(replied[1]),
                    group_id=group_id,
                    message_row_id=message_row_id,
                    evidence_type="reply",
                    weight=1.5,
                    observed_at=observed_at,
                    detail={
                        "reply_to_platform_message_id": reply_to,
                        "reply_to_message_row_id": int(replied[0]),
                    },
                )
        if group_id and sent_at is not None:
            previous = self._connection.execute(
                """
                SELECT id, sender_qq_id, sent_at FROM qq_messages
                WHERE group_id = ? AND id < ? AND sent_at IS NOT NULL
                ORDER BY sent_at DESC, id DESC LIMIT 1
                """,
                (group_id, message_row_id),
            ).fetchone()
            if (
                previous
                and previous[1] != sender_qq_id
                and abs(sent_at - int(previous[2])) <= 180
            ):
                self._record_relationship_evidence(
                    source_qq_id=sender_qq_id,
                    target_qq_id=str(previous[1]),
                    group_id=group_id,
                    message_row_id=message_row_id,
                    evidence_type="adjacent_turn",
                    weight=0.25,
                    observed_at=observed_at,
                    detail={
                        "previous_message_row_id": int(previous[0]),
                        "gap_seconds": abs(sent_at - int(previous[2])),
                    },
                )
        return True

    def _record_relationship_evidence(
        self,
        *,
        source_qq_id: str,
        target_qq_id: str,
        group_id: str | None,
        message_row_id: int,
        evidence_type: str,
        weight: float,
        observed_at: str,
        detail: Any,
    ) -> bool:
        if not source_qq_id or not target_qq_id or source_qq_id == target_qq_id:
            return False
        self._upsert_user(target_qq_id, None, observed_at, "relationship", {})
        person_a, person_b = sorted((source_qq_id, target_qq_id))
        self._connection.execute(
            """
            INSERT OR IGNORE INTO qq_relationships(
                person_a_qq_id, person_b_qq_id,
                first_interaction_at, last_interaction_at
            ) VALUES (?, ?, ?, ?)
            """,
            (person_a, person_b, observed_at, observed_at),
        )
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO qq_relationship_evidence(
                person_a_qq_id, person_b_qq_id, source_qq_id, target_qq_id,
                group_id, message_id, evidence_type, weight, observed_at, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                person_a,
                person_b,
                source_qq_id,
                target_qq_id,
                group_id,
                message_row_id,
                evidence_type,
                weight,
                observed_at,
                _json(detail),
            ),
        )
        if cursor.rowcount == 0:
            return False
        self._connection.execute(
            """
            UPDATE qq_relationships SET
                last_interaction_at = ?, evidence_count = evidence_count + 1
            WHERE person_a_qq_id = ? AND person_b_qq_id = ?
            """,
            (observed_at, person_a, person_b),
        )
        return True

    def _record_notice(
        self, raw: dict[str, Any], bot_qq_id: str, observed_at: str
    ) -> None:
        notice_type = _text(raw.get("notice_type"))
        group_id = _text(raw.get("group_id"))
        qq_id = _text(raw.get("user_id"))
        notice_category = {
            "group_increase": "membership_joined",
            "group_decrease": "membership_left",
            "group_admin": "role_changed",
            "group_card": "group_card_changed",
            "group_recall": "message_recalled",
            "friend_recall": "message_recalled",
            "group_ban": "member_restricted",
            "friend_add": "friend_added",
            "notify": "interaction_notification",
        }.get(notice_type or "", "other_notice")
        event_key = _text(raw.get("message_id")) or _text(raw.get("flag")) or (
            f"{notice_type}:{_text(raw.get('time')) or observed_at}:"
            f"{group_id or ''}:{qq_id or ''}"
        )
        self._connection.execute(
            """
            INSERT OR IGNORE INTO qq_notice_facts(
                bot_qq_id, event_key, notice_category, notice_type, sub_type,
                group_id, actor_qq_id, target_qq_id, occurred_at, observed_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bot_qq_id,
                event_key,
                notice_category,
                notice_type,
                _text(raw.get("sub_type")),
                group_id,
                _text(raw.get("operator_id")) or qq_id,
                qq_id,
                _integer(raw.get("time")),
                observed_at,
                _json(raw),
            ),
        )
        if notice_type in {"group_recall", "friend_recall"}:
            message_id = _text(raw.get("message_id"))
            if message_id:
                self._connection.execute(
                    """
                    UPDATE qq_messages SET recalled_at = ?
                    WHERE bot_qq_id = ? AND platform_message_id = ?
                    """,
                    (observed_at, bot_qq_id, message_id),
                )
        if not group_id or not qq_id:
            return
        self._upsert_group(group_id, None, observed_at, "notice_event", raw)
        self._upsert_user(qq_id, None, observed_at, "notice_event", raw)
        current = self._connection.execute(
            """
            SELECT current_group_card, current_role
            FROM qq_group_memberships WHERE group_id = ? AND qq_id = ?
            """,
            (group_id, qq_id),
        ).fetchone()
        card = current[0] if current else None
        role = current[1] if current else "member"
        sub_type = _text(raw.get("sub_type"))
        if notice_type == "group_card":
            card = _text(raw.get("card_new")) or card
        elif notice_type == "group_admin":
            role = "admin" if sub_type == "set" else "member"
        self._upsert_membership(
            group_id,
            qq_id,
            card,
            role,
            None,
            observed_at,
            "notice_event",
            raw,
        )
        if notice_type == "group_decrease":
            self._mark_membership_left(
                group_id, qq_id, observed_at, "notice_event", raw
            )

    def record_group_snapshot(
        self,
        group: Mapping[str, Any],
        members: Sequence[Mapping[str, Any]],
        *,
        bot_qq_id: str,
    ) -> None:
        observed_at = _utc_now()
        group_id = _text(group.get("group_id"))
        if not group_id:
            raise ValueError("group snapshot requires group_id")
        with self._guard, self._connection:
            self._upsert_bot(bot_qq_id, observed_at)
            self._upsert_group(
                group_id,
                _text(group.get("group_name")),
                observed_at,
                "group_snapshot",
                group,
            )
            self._observe_group(group_id, bot_qq_id, observed_at)
            owner_id = None
            seen_ids: set[str] = set()
            for member in members:
                qq_id = _text(member.get("user_id"))
                if not qq_id:
                    continue
                seen_ids.add(qq_id)
                role = _text(member.get("role")) or "member"
                if role == "owner":
                    owner_id = qq_id
                self._upsert_user(
                    qq_id,
                    _text(member.get("nickname")),
                    observed_at,
                    "group_snapshot",
                    member,
                )
                self._upsert_membership(
                    group_id,
                    qq_id,
                    _text(member.get("card")),
                    role,
                    _text(member.get("title")),
                    observed_at,
                    "group_snapshot",
                    member,
                )
                self._connection.execute(
                    """
                    INSERT INTO qq_group_member_snapshots(
                        group_id, qq_id, observed_at, member_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (group_id, qq_id, observed_at, _json(member)),
                )
                self._connection.execute(
                    """
                    UPDATE qq_group_memberships SET
                        level = ?, join_time = ?, last_sent_time = ?, shut_up_until = ?
                    WHERE group_id = ? AND qq_id = ?
                    """,
                    (
                        _text(member.get("level")),
                        _integer(member.get("join_time")),
                        _integer(member.get("last_sent_time")),
                        _integer(member.get("shut_up_timestamp")),
                        group_id,
                        qq_id,
                    ),
                )
            self._connection.execute(
                """
                UPDATE qq_groups SET
                    current_owner_qq_id = ?, member_count = ?, max_member_count = ?,
                    last_snapshot_at = ?, raw_profile_json = ?
                WHERE group_id = ?
                """,
                (
                    owner_id,
                    _integer(group.get("member_count")) or len(seen_ids),
                    _integer(group.get("max_member_count")),
                    observed_at,
                    _json(group),
                    group_id,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO qq_group_snapshots(
                    group_id, bot_qq_id, owner_qq_id, member_count,
                    observed_at, group_json, members_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group_id,
                    bot_qq_id,
                    owner_id,
                    _integer(group.get("member_count")) or len(seen_ids),
                    observed_at,
                    _json(group),
                    _json(members),
                ),
            )
            if seen_ids:
                placeholders = ",".join("?" for _ in seen_ids)
                self._connection.execute(
                    f"""
                    UPDATE qq_group_memberships SET left_at = ?
                    WHERE group_id = ? AND qq_id NOT IN ({placeholders}) AND left_at IS NULL
                    """,
                    (observed_at, group_id, *seen_ids),
                )
                departed = self._connection.execute(
                    f"""
                    SELECT qq_id FROM qq_group_membership_episodes
                    WHERE group_id = ? AND left_at IS NULL
                      AND qq_id NOT IN ({placeholders})
                    """,
                    (group_id, *seen_ids),
                ).fetchall()
                for (departed_qq_id,) in departed:
                    self._mark_membership_left(
                        group_id,
                        str(departed_qq_id),
                        observed_at,
                        "group_snapshot",
                        group,
                    )

    def _mark_membership_left(
        self,
        group_id: str,
        qq_id: str,
        observed_at: str,
        source: str,
        raw: Any,
    ) -> None:
        self._connection.execute(
            """
            UPDATE qq_group_memberships SET left_at = ?
            WHERE group_id = ? AND qq_id = ?
            """,
            (observed_at, group_id, qq_id),
        )
        self._connection.execute(
            """
            UPDATE qq_group_membership_episodes SET
                left_at = ?, leave_source = ?, leave_raw_json = ?
            WHERE id = (
                SELECT id FROM qq_group_membership_episodes
                WHERE group_id = ? AND qq_id = ? AND left_at IS NULL
                ORDER BY id DESC LIMIT 1
            )
            """,
            (observed_at, source, _json(raw), group_id, qq_id),
        )

    def record_user_profile(self, profile: Mapping[str, Any]) -> None:
        qq_id = _text(profile.get("user_id"))
        if not qq_id:
            raise ValueError("user profile requires user_id")
        observed_at = _utc_now()
        nickname = _text(profile.get("nickname")) or _text(profile.get("nick"))
        with self._guard, self._connection:
            self._upsert_user(
                qq_id,
                nickname,
                observed_at,
                "user_profile_sync",
                profile,
            )
            self._connection.execute(
                """
                UPDATE qq_users SET last_profile_sync_at = ?, raw_profile_json = ?
                WHERE qq_id = ?
                """,
                (observed_at, _json(profile), qq_id),
            )
            self._connection.execute(
                """
                INSERT INTO qq_user_profile_snapshots(qq_id, observed_at, profile_json)
                VALUES (?, ?, ?)
                """,
                (qq_id, observed_at, _json(profile)),
            )

    def record_friend_inventory(
        self,
        bot_qq_id: str,
        friends: Sequence[Mapping[str, Any]],
    ) -> None:
        observed_at = _utc_now()
        with self._guard, self._connection:
            self._upsert_bot(bot_qq_id, observed_at)
            seen_ids: set[str] = set()
            for friend in friends:
                qq_id = _text(friend.get("user_id"))
                if not qq_id:
                    continue
                seen_ids.add(qq_id)
                self._upsert_user(
                    qq_id,
                    _text(friend.get("nickname")) or _text(friend.get("nick")),
                    observed_at,
                    "friend_inventory",
                    friend,
                )
                self._connection.execute(
                    """
                    INSERT INTO qq_friendships(
                        bot_qq_id, qq_id, current_remark, first_seen_at,
                        last_seen_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(bot_qq_id, qq_id) DO UPDATE SET
                        current_remark = excluded.current_remark,
                        last_seen_at = excluded.last_seen_at,
                        removed_at = NULL,
                        raw_json = excluded.raw_json
                    """,
                    (
                        bot_qq_id,
                        qq_id,
                        _text(friend.get("remark")),
                        observed_at,
                        observed_at,
                        _json(friend),
                    ),
                )
            if seen_ids:
                placeholders = ",".join("?" for _ in seen_ids)
                self._connection.execute(
                    f"""
                    UPDATE qq_friendships SET removed_at = ?
                    WHERE bot_qq_id = ? AND removed_at IS NULL
                      AND qq_id NOT IN ({placeholders})
                    """,
                    (observed_at, bot_qq_id, *seen_ids),
                )
            else:
                self._connection.execute(
                    """
                    UPDATE qq_friendships SET removed_at = ?
                    WHERE bot_qq_id = ? AND removed_at IS NULL
                    """,
                    (observed_at, bot_qq_id),
                )
            self._connection.execute(
                """
                INSERT INTO qq_friend_inventory_snapshots(
                    bot_qq_id, observed_at, friends_json
                ) VALUES (?, ?, ?)
                """,
                (bot_qq_id, observed_at, _json(friends)),
            )

    def record_group_inventory(
        self,
        bot_qq_id: str,
        groups: Sequence[Mapping[str, Any]],
    ) -> None:
        observed_at = _utc_now()
        with self._guard, self._connection:
            self._upsert_bot(bot_qq_id, observed_at)
            for group in groups:
                group_id = _text(group.get("group_id"))
                if not group_id:
                    continue
                self._upsert_group(
                    group_id,
                    _text(group.get("group_name")),
                    observed_at,
                    "group_inventory",
                    group,
                )
                self._observe_group(group_id, bot_qq_id, observed_at)
                self._connection.execute(
                    """
                    UPDATE qq_groups SET
                        member_count = COALESCE(?, member_count),
                        max_member_count = COALESCE(?, max_member_count)
                    WHERE group_id = ?
                    """,
                    (
                        _integer(group.get("member_count")),
                        _integer(group.get("max_member_count")),
                        group_id,
                    ),
                )
            self._connection.execute(
                """
                INSERT INTO qq_group_inventory_snapshots(
                    bot_qq_id, observed_at, groups_json
                ) VALUES (?, ?, ?)
                """,
                (bot_qq_id, observed_at, _json(groups)),
            )

    def record_message_media(
        self,
        *,
        message_row_id: int,
        position: int,
        media_type: str,
        source_url: str | None,
        source_file: str | None,
        content_hash: str | None,
        success: bool,
        error: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO qq_message_media(
                    message_id, position, media_type, source_url, source_file,
                    content_hash, fetch_status, error, observed_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id, position) DO UPDATE SET
                    source_url = COALESCE(excluded.source_url, source_url),
                    source_file = COALESCE(excluded.source_file, source_file),
                    content_hash = COALESCE(excluded.content_hash, content_hash),
                    fetch_status = excluded.fetch_status,
                    error = excluded.error,
                    observed_at = excluded.observed_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    message_row_id,
                    position,
                    media_type,
                    source_url,
                    source_file,
                    content_hash,
                    "stored" if success else "failed",
                    error,
                    _utc_now(),
                    _json(metadata or {}),
                ),
            )

    def pending_message_media(
        self, platform_message_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT mm.message_id, mm.position, mm.media_type,
                       mm.source_url, mm.source_file, mm.metadata_json
                FROM qq_message_media mm
                JOIN qq_messages m ON m.id = mm.message_id
                WHERE m.platform_message_id = ? AND mm.fetch_status = 'pending'
                ORDER BY mm.position LIMIT ?
                """,
                (platform_message_id, max(1, limit)),
            ).fetchall()
        return [
            {
                "message_row_id": int(row[0]),
                "position": int(row[1]),
                "media_type": row[2],
                "source_url": row[3],
                "source_file": row[4],
                "metadata": json.loads(row[5]),
            }
            for row in rows
        ]

    def record_raw_onebot_message(self, raw: Mapping[str, Any]) -> bool:
        value = dict(raw)
        message_id = _text(value.get("message_id"))
        self_id = _text(value.get("self_id"))
        user_id = _text(value.get("user_id"))
        if not message_id or not self_id or not user_id:
            return False

        class RawEvent:
            def __init__(self, payload: dict[str, Any]) -> None:
                self.payload = payload
                self.message_obj = type(
                    "RawMessage",
                    (),
                    {
                        "message_id": message_id,
                        "self_id": self_id,
                        "timestamp": _integer(payload.get("time")),
                        "raw_message": payload,
                        "group": None,
                    },
                )()

            def get_platform_name(self):
                return "aiocqhttp"

            def get_sender_id(self):
                return user_id

            def get_sender_name(self):
                sender = _mapping(self.payload.get("sender"))
                return _text(sender.get("card")) or _text(sender.get("nickname")) or user_id

            def get_group_id(self):
                return _text(self.payload.get("group_id")) or ""

            def get_message_type(self):
                return _text(self.payload.get("message_type")) or "unknown"

            def get_message_str(self):
                message = self.payload.get("raw_message")
                if isinstance(message, str):
                    return message
                return "".join(
                    str(_mapping(_mapping(item).get("data")).get("text") or "")
                    for item in self.payload.get("message", [])
                )

            def get_message_outline(self):
                return self.get_message_str()

        return self.record_event(RawEvent(value))

    def search_messages(
        self,
        query: str,
        *,
        group_id: str | None = None,
        sender_qq_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        text = query.strip()
        if not text:
            return []
        filters = []
        params: list[Any] = []
        if group_id:
            filters.append("m.group_id = ?")
            params.append(group_id)
        if sender_qq_id:
            filters.append("m.sender_qq_id = ?")
            params.append(sender_qq_id)
        where = (" AND " + " AND ".join(filters)) if filters else ""
        with self._guard:
            rows = []
            if getattr(self, "_fts_enabled", False):
                tokens = [token.replace('"', '""') for token in text.split() if token]
                expression = " OR ".join(f'"{token}"' for token in tokens)
                try:
                    rows = self._connection.execute(
                        f"""
                        SELECT m.id, m.platform_message_id, m.group_id,
                               m.sender_qq_id, m.content_text, m.sent_at, m.observed_at
                        FROM qq_messages_fts f
                        JOIN qq_messages m ON m.id = f.rowid
                        WHERE qq_messages_fts MATCH ?{where}
                        ORDER BY bm25(qq_messages_fts), m.id DESC LIMIT ?
                        """,
                        (expression, *params, max(1, limit)),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if not rows:
                rows = self._connection.execute(
                    f"""
                    SELECT m.id, m.platform_message_id, m.group_id,
                           m.sender_qq_id, m.content_text, m.sent_at, m.observed_at
                    FROM qq_messages m
                    WHERE (m.content_text LIKE ? OR m.content_outline LIKE ?){where}
                    ORDER BY m.id DESC LIMIT ?
                    """,
                    (f"%{text}%", f"%{text}%", *params, max(1, limit)),
                ).fetchall()
        return [
            {
                "id": int(row[0]),
                "platform_message_id": row[1],
                "group_id": row[2],
                "sender_qq_id": row[3],
                "content": row[4],
                "sent_at": row[5],
                "observed_at": row[6],
            }
            for row in rows
        ]

    def recent_group_participants(
        self, group_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        normalized_group_id = str(group_id).strip()
        if not normalized_group_id:
            return []
        bounded_limit = max(1, min(50, int(limit)))
        with self._guard:
            rows = self._connection.execute(
                """
                SELECT m.sender_qq_id, u.current_nickname, m.sender_group_card,
                       m.sender_role, m.content_text, m.sent_at
                FROM qq_messages m
                JOIN (
                    SELECT sender_qq_id, MAX(id) AS latest_id
                    FROM qq_messages
                    WHERE group_id = ?
                    GROUP BY sender_qq_id
                    ORDER BY latest_id DESC
                    LIMIT ?
                ) recent ON recent.latest_id = m.id
                LEFT JOIN qq_users u ON u.qq_id = m.sender_qq_id
                ORDER BY m.id DESC
                """,
                (normalized_group_id, bounded_limit),
            ).fetchall()
        return [
            {
                "qq_id": row[0],
                "nickname": row[1],
                "group_card": row[2],
                "role": row[3],
                "latest_message": row[4],
                "latest_message_at": row[5],
            }
            for row in rows
        ]

    def model_context(
        self,
        qq_id: str,
        *,
        group_id: str | None = None,
        history_limit: int = 8,
        relationship_limit: int = 12,
    ) -> dict[str, Any]:
        """Build a timestamped social context for behavior-model phases."""
        qq_id = str(qq_id).strip()
        group_id = str(group_id).strip() if group_id else None
        if not qq_id:
            return {}
        history_limit = max(1, min(50, int(history_limit)))
        relationship_limit = max(1, min(50, int(relationship_limit)))
        with self._guard:
            user = self._connection.execute(
                """
                SELECT current_nickname, current_avatar_url, current_avatar_hash,
                       first_seen_at, last_seen_at, last_profile_sync_at, raw_profile_json
                FROM qq_users WHERE qq_id = ?
                """,
                (qq_id,),
            ).fetchone()
            if user is None:
                return {
                    "assembled_at": _utc_now(),
                    "person": {"qq_id": qq_id},
                    "timestamp_semantics": "Unix seconds for QQ event times; ISO-8601 UTC for observed and validity times.",
                }
            nickname_history = self._connection.execute(
                """
                SELECT nickname, source, valid_from, valid_to
                FROM qq_user_nickname_history WHERE qq_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (qq_id, history_limit),
            ).fetchall()
            avatar_history = self._connection.execute(
                """
                SELECT avatar_url, content_hash, valid_from, valid_to
                FROM qq_user_avatar_history WHERE qq_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (qq_id, history_limit),
            ).fetchall()
            friendship = self._connection.execute(
                """
                SELECT current_remark, first_seen_at, last_seen_at, removed_at
                FROM qq_friendships WHERE qq_id = ?
                ORDER BY last_seen_at DESC LIMIT 1
                """,
                (qq_id,),
            ).fetchone()
            group = None
            membership = None
            group_name_history: list[tuple[Any, ...]] = []
            group_avatar_history: list[tuple[Any, ...]] = []
            card_history: list[tuple[Any, ...]] = []
            role_history: list[tuple[Any, ...]] = []
            if group_id:
                group = self._connection.execute(
                    """
                    SELECT current_name, current_avatar_url, current_avatar_hash,
                           current_owner_qq_id, member_count, max_member_count,
                           first_seen_at, last_seen_at, last_snapshot_at, raw_profile_json
                    FROM qq_groups WHERE group_id = ?
                    """,
                    (group_id,),
                ).fetchone()
                membership = self._connection.execute(
                    """
                    SELECT current_group_card, current_role, current_title, level,
                           join_time, last_sent_time, shut_up_until, first_seen_at,
                           last_seen_at, left_at, raw_member_json
                    FROM qq_group_memberships WHERE group_id = ? AND qq_id = ?
                    """,
                    (group_id, qq_id),
                ).fetchone()
                group_name_history = self._connection.execute(
                    """
                    SELECT group_name, source, valid_from, valid_to
                    FROM qq_group_name_history WHERE group_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (group_id, history_limit),
                ).fetchall()
                group_avatar_history = self._connection.execute(
                    """
                    SELECT avatar_url, content_hash, valid_from, valid_to
                    FROM qq_group_avatar_history WHERE group_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (group_id, history_limit),
                ).fetchall()
                card_history = self._connection.execute(
                    """
                    SELECT group_card, source, valid_from, valid_to
                    FROM qq_group_member_name_history
                    WHERE group_id = ? AND qq_id = ? ORDER BY id DESC LIMIT ?
                    """,
                    (group_id, qq_id, history_limit),
                ).fetchall()
                role_history = self._connection.execute(
                    """
                    SELECT role, source, valid_from, valid_to
                    FROM qq_group_role_history
                    WHERE group_id = ? AND qq_id = ? ORDER BY id DESC LIMIT ?
                    """,
                    (group_id, qq_id, history_limit),
                ).fetchall()
            relationship_params: list[Any] = [qq_id, qq_id]
            relationship_group_filter = ""
            if group_id:
                relationship_group_filter = """
                  AND EXISTS (
                      SELECT 1 FROM qq_relationship_evidence e
                      WHERE e.person_a_qq_id = r.person_a_qq_id
                        AND e.person_b_qq_id = r.person_b_qq_id
                        AND e.group_id = ?
                  )
                """
                relationship_params.append(group_id)
            relationship_params.append(relationship_limit)
            relationships = self._connection.execute(
                f"""
                SELECT CASE WHEN r.person_a_qq_id = ? THEN r.person_b_qq_id
                            ELSE r.person_a_qq_id END AS counterpart_qq_id,
                       u.current_nickname, r.first_interaction_at,
                       r.last_interaction_at, r.evidence_count,
                       r.current_relation_type, r.current_summary,
                       r.interaction_strength, r.reciprocity, r.confidence,
                       r.last_evaluated_at, r.current_metrics_json
                FROM qq_relationships r
                LEFT JOIN qq_users u ON u.qq_id = CASE
                    WHEN r.person_a_qq_id = ? THEN r.person_b_qq_id
                    ELSE r.person_a_qq_id END
                WHERE (r.person_a_qq_id = ? OR r.person_b_qq_id = ?)
                {relationship_group_filter}
                ORDER BY r.last_interaction_at DESC LIMIT ?
                """,
                (qq_id, qq_id, *relationship_params),
            ).fetchall()
            notices = self._connection.execute(
                """
                SELECT notice_category, notice_type, sub_type, group_id,
                       actor_qq_id, target_qq_id, occurred_at, observed_at
                FROM qq_notice_facts
                WHERE (actor_qq_id = ? OR target_qq_id = ?)
                  AND (? IS NULL OR group_id = ?)
                ORDER BY id DESC LIMIT ?
                """,
                (qq_id, qq_id, group_id, group_id, history_limit),
            ).fetchall()

        context: dict[str, Any] = {
            "assembled_at": _utc_now(),
            "timestamp_semantics": "Unix seconds for QQ event times; ISO-8601 UTC for observed and validity times.",
            "person": {
                "qq_id": qq_id,
                "nickname": user[0],
                "avatar_url": user[1],
                "avatar_hash": user[2],
                "first_seen_at": user[3],
                "last_seen_at": user[4],
                "profile_synced_at": user[5],
                "profile": _loaded_json(user[6]),
                "nickname_history": [
                    {"value": row[0], "source": row[1], "valid_from": row[2], "valid_to": row[3]}
                    for row in nickname_history
                ],
                "avatar_history": [
                    {"url": row[0], "content_hash": row[1], "valid_from": row[2], "valid_to": row[3]}
                    for row in avatar_history
                ],
            },
            "relationships": [
                {
                    "counterpart_qq_id": row[0],
                    "counterpart_nickname": row[1],
                    "first_interaction_at": row[2],
                    "last_interaction_at": row[3],
                    "evidence_count": row[4],
                    "relation_type": row[5],
                    "summary": row[6],
                    "interaction_strength": row[7],
                    "reciprocity": row[8],
                    "confidence": row[9],
                    "last_evaluated_at": row[10],
                    "metrics": _loaded_json(row[11]),
                }
                for row in relationships
            ],
            "recent_notices": [
                {
                    "category": row[0], "type": row[1], "sub_type": row[2],
                    "group_id": row[3], "actor_qq_id": row[4], "target_qq_id": row[5],
                    "occurred_at": row[6], "observed_at": row[7],
                }
                for row in notices
            ],
        }
        if friendship:
            context["person"]["friendship"] = {
                "remark": friendship[0], "first_seen_at": friendship[1],
                "last_seen_at": friendship[2], "removed_at": friendship[3],
            }
        if group_id:
            context["group"] = {"group_id": group_id}
            if group:
                context["group"].update(
                    {
                        "name": group[0], "avatar_url": group[1], "avatar_hash": group[2],
                        "owner_qq_id": group[3], "member_count": group[4],
                        "max_member_count": group[5], "first_seen_at": group[6],
                        "last_seen_at": group[7], "snapshot_at": group[8],
                        "profile": _loaded_json(group[9]),
                    }
                )
            context["group"]["name_history"] = [
                {"value": row[0], "source": row[1], "valid_from": row[2], "valid_to": row[3]}
                for row in group_name_history
            ]
            context["group"]["avatar_history"] = [
                {"url": row[0], "content_hash": row[1], "valid_from": row[2], "valid_to": row[3]}
                for row in group_avatar_history
            ]
            if membership:
                context["membership"] = {
                    "group_card": membership[0], "role": membership[1],
                    "title": membership[2], "level": membership[3],
                    "join_time": membership[4], "last_sent_time": membership[5],
                    "shut_up_until": membership[6], "first_seen_at": membership[7],
                    "last_seen_at": membership[8], "left_at": membership[9],
                    "profile": _loaded_json(membership[10]),
                    "group_card_history": [
                        {"value": row[0], "source": row[1], "valid_from": row[2], "valid_to": row[3]}
                        for row in card_history
                    ],
                    "role_history": [
                        {"value": row[0], "source": row[1], "valid_from": row[2], "valid_to": row[3]}
                        for row in role_history
                    ],
                }
        return context

    def record_binary_asset(
        self,
        content: bytes,
        *,
        media_type: str,
        mime_type: str | None = None,
        source_url: str | None = None,
    ) -> str:
        content_hash = hashlib.sha256(content).hexdigest()
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO qq_binary_assets(
                    content_hash, media_type, mime_type, byte_size,
                    content, source_url, first_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content_hash,
                    media_type,
                    mime_type,
                    len(content),
                    content,
                    source_url,
                    _utc_now(),
                ),
            )
        return content_hash

    def record_user_avatar(
        self,
        qq_id: str,
        avatar_url: str,
        *,
        content_hash: str | None = None,
        etag: str | None = None,
        raw: Any = None,
    ) -> bool:
        return self._record_avatar(
            "user", qq_id, avatar_url, content_hash, etag, raw
        )

    def record_group_avatar(
        self,
        group_id: str,
        avatar_url: str,
        *,
        content_hash: str | None = None,
        etag: str | None = None,
        raw: Any = None,
    ) -> bool:
        return self._record_avatar(
            "group", group_id, avatar_url, content_hash, etag, raw
        )

    def _record_avatar(
        self,
        kind: str,
        identity: str,
        avatar_url: str,
        content_hash: str | None,
        etag: str | None,
        raw: Any,
    ) -> bool:
        observed_at = _utc_now()
        table = "qq_user_avatar_history" if kind == "user" else "qq_group_avatar_history"
        key = "qq_id" if kind == "user" else "group_id"
        owner_table = "qq_users" if kind == "user" else "qq_groups"
        with self._guard, self._connection:
            if kind == "user":
                self._upsert_user(identity, None, observed_at, "avatar_sync", raw)
            else:
                self._upsert_group(identity, None, observed_at, "avatar_sync", raw)
            current = self._connection.execute(
                f"""
                SELECT avatar_url, content_hash, etag FROM {table}
                WHERE {key} = ? AND valid_to IS NULL ORDER BY id DESC LIMIT 1
                """,
                (identity,),
            ).fetchone()
            signature = (avatar_url, content_hash, etag)
            if current == signature:
                return False
            self._close_history(table, key, identity, observed_at)
            self._connection.execute(
                f"""
                INSERT INTO {table}(
                    {key}, avatar_url, content_hash, etag, valid_from, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (identity, avatar_url, content_hash, etag, observed_at, _json(raw)),
            )
            self._connection.execute(
                f"""
                UPDATE {owner_table} SET
                    current_avatar_url = ?, current_avatar_hash = ?
                WHERE {key} = ?
                """,
                (avatar_url, content_hash, identity),
            )
            return True

    def record_qzone_post(
        self,
        qq_id: str,
        post_id: str,
        content_text: str,
        *,
        created_at: int | None = None,
        post_type: str | None = None,
        media: Sequence[Mapping[str, Any]] = (),
        raw: Any = None,
    ) -> bool:
        observed_at = _utc_now()
        with self._guard, self._connection:
            self._upsert_user(qq_id, None, observed_at, "qzone_sync", {})
            cursor = self._connection.execute(
                """
                INSERT INTO qq_qzone_posts(
                    qq_id, post_id, post_type, content_text, created_at,
                    observed_at, updated_at, raw_post_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(qq_id, post_id) DO UPDATE SET
                    post_type = excluded.post_type,
                    content_text = excluded.content_text,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    raw_post_json = excluded.raw_post_json,
                    deleted_at = NULL
                """,
                (
                    qq_id,
                    post_id,
                    post_type,
                    content_text,
                    created_at,
                    observed_at,
                    observed_at,
                    _json(raw),
                ),
            )
            row = self._connection.execute(
                "SELECT id FROM qq_qzone_posts WHERE qq_id = ? AND post_id = ?",
                (qq_id, post_id),
            ).fetchone()
            assert row is not None
            post_row_id = int(row[0])
            self._connection.execute(
                "DELETE FROM qq_qzone_post_media WHERE post_row_id = ?",
                (post_row_id,),
            )
            for position, item in enumerate(media):
                self._connection.execute(
                    """
                    INSERT INTO qq_qzone_post_media(
                        post_row_id, position, media_type, media_url, content_hash, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        post_row_id,
                        position,
                        _text(item.get("type")) or "unknown",
                        _text(item.get("url")),
                        _text(item.get("content_hash")),
                        _json(item),
                    ),
                )
            return cursor.rowcount > 0

    def update_sync_state(
        self,
        resource_type: str,
        resource_id: str,
        *,
        cursor: str | None = None,
        success: bool,
        error: str | None = None,
        raw: Any = None,
    ) -> None:
        now = _utc_now()
        with self._guard, self._connection:
            self._connection.execute(
                """
                INSERT INTO qq_sync_state(
                    resource_type, resource_id, cursor, last_attempt_at,
                    last_success_at, last_error, raw_state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(resource_type, resource_id) DO UPDATE SET
                    cursor = COALESCE(excluded.cursor, cursor),
                    last_attempt_at = excluded.last_attempt_at,
                    last_success_at = CASE
                        WHEN excluded.last_error IS NULL THEN excluded.last_success_at
                        ELSE last_success_at
                    END,
                    last_error = excluded.last_error,
                    raw_state_json = excluded.raw_state_json
                """,
                (
                    resource_type,
                    resource_id,
                    cursor,
                    now,
                    now if success else None,
                    error if not success else None,
                    _json(raw),
                ),
            )

    def evaluate_due_relationships(
        self,
        *,
        minimum_new_evidence: int = 10,
        maximum_interval: timedelta = timedelta(days=7),
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if minimum_new_evidence < 1:
            raise ValueError("minimum_new_evidence must be positive")
        if maximum_interval.total_seconds() <= 0:
            raise ValueError("maximum_interval must be positive")
        evaluated_at = now or datetime.now(timezone.utc)
        evaluated_text = evaluated_at.isoformat()
        results: list[dict[str, Any]] = []
        with self._guard, self._connection:
            relationships = self._connection.execute(
                """
                SELECT person_a_qq_id, person_b_qq_id, first_interaction_at,
                       last_evaluated_evidence_id, last_evaluated_at
                FROM qq_relationships
                ORDER BY last_interaction_at ASC
                """
            ).fetchall()
            for person_a, person_b, first_at, last_evidence_id, last_at in relationships:
                new_rows = self._connection.execute(
                    """
                    SELECT id, group_id FROM qq_relationship_evidence
                    WHERE person_a_qq_id = ? AND person_b_qq_id = ? AND id > ?
                    ORDER BY id
                    """,
                    (person_a, person_b, int(last_evidence_id or 0)),
                ).fetchall()
                if not new_rows:
                    continue
                last_evaluated = datetime.fromisoformat(last_at or first_at)
                if (
                    len(new_rows) < minimum_new_evidence
                    and evaluated_at - last_evaluated < maximum_interval
                ):
                    continue

                evidence = self._connection.execute(
                    """
                    SELECT id, source_qq_id, target_qq_id, evidence_type, weight
                    FROM qq_relationship_evidence
                    WHERE person_a_qq_id = ? AND person_b_qq_id = ?
                    ORDER BY id
                    """,
                    (person_a, person_b),
                ).fetchall()
                direction_a_to_b = sum(
                    float(row[4])
                    for row in evidence
                    if row[1] == person_a and row[2] == person_b
                )
                direction_b_to_a = sum(
                    float(row[4])
                    for row in evidence
                    if row[1] == person_b and row[2] == person_a
                )
                larger_direction = max(direction_a_to_b, direction_b_to_a)
                reciprocity = (
                    min(direction_a_to_b, direction_b_to_a) / larger_direction
                    if larger_direction > 0
                    else 0.0
                )
                type_counts: dict[str, int] = {}
                weighted_total = 0.0
                for _, _, _, evidence_type, weight in evidence:
                    type_counts[evidence_type] = type_counts.get(evidence_type, 0) + 1
                    weighted_total += float(weight)
                interaction_strength = min(100.0, 25.0 * math.log1p(weighted_total))
                confidence = min(1.0, math.log1p(len(evidence)) / math.log1p(50))
                relation_type = self._statistical_relation_type(
                    len(evidence), reciprocity, type_counts
                )
                metrics = {
                    "a_to_b_weight": round(direction_a_to_b, 4),
                    "b_to_a_weight": round(direction_b_to_a, 4),
                    "evidence_types": type_counts,
                    "weighted_total": round(weighted_total, 4),
                }
                summary = (
                    f"{person_a} 与 {person_b} 累计出现 {len(evidence)} 条直接互动证据；"
                    f"互动强度 {interaction_strength:.1f}，互惠度 {reciprocity:.2f}；"
                    f"证据构成为 {_json(type_counts)}"
                )
                evidence_from = int(new_rows[0][0])
                evidence_to = int(new_rows[-1][0])
                cursor = self._connection.execute(
                    """
                    INSERT INTO qq_relationship_evaluations(
                        person_a_qq_id, person_b_qq_id, evaluated_at,
                        evidence_from_id, evidence_to_id, new_evidence_count,
                        total_evidence_count, relation_type, summary,
                        interaction_strength, reciprocity, confidence,
                        metrics_json, evaluator
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        person_a,
                        person_b,
                        evaluated_text,
                        evidence_from,
                        evidence_to,
                        len(new_rows),
                        len(evidence),
                        relation_type,
                        summary,
                        interaction_strength,
                        reciprocity,
                        confidence,
                        _json(metrics),
                        "statistical-v1",
                    ),
                )
                group_ids = {row[1] for row in new_rows if row[1] is not None}
                memory_group_id = next(iter(group_ids)) if len(group_ids) == 1 else None
                self._connection.execute(
                    """
                    UPDATE qq_relationship_memories SET superseded_at = ?
                    WHERE person_a_qq_id = ? AND person_b_qq_id = ?
                      AND memory_type = 'periodic_assessment' AND superseded_at IS NULL
                    """,
                    (evaluated_text, person_a, person_b),
                )
                self._connection.execute(
                    """
                    INSERT INTO qq_relationship_memories(
                        person_a_qq_id, person_b_qq_id, group_id, memory_type,
                        content, confidence, source_evidence_from_id,
                        source_evidence_to_id, created_at
                    ) VALUES (?, ?, ?, 'periodic_assessment', ?, ?, ?, ?, ?)
                    """,
                    (
                        person_a,
                        person_b,
                        memory_group_id,
                        summary,
                        confidence,
                        evidence_from,
                        evidence_to,
                        evaluated_text,
                    ),
                )
                self._connection.execute(
                    """
                    UPDATE qq_relationships SET
                        last_evaluated_evidence_id = ?, last_evaluated_at = ?,
                        current_relation_type = ?, current_summary = ?,
                        interaction_strength = ?, reciprocity = ?, confidence = ?,
                        current_metrics_json = ?
                    WHERE person_a_qq_id = ? AND person_b_qq_id = ?
                    """,
                    (
                        evidence_to,
                        evaluated_text,
                        relation_type,
                        summary,
                        interaction_strength,
                        reciprocity,
                        confidence,
                        _json(metrics),
                        person_a,
                        person_b,
                    ),
                )
                results.append(
                    {
                        "evaluation_id": int(cursor.lastrowid),
                        "person_a_qq_id": person_a,
                        "person_b_qq_id": person_b,
                        "relation_type": relation_type,
                        "summary": summary,
                        "interaction_strength": interaction_strength,
                        "reciprocity": reciprocity,
                        "confidence": confidence,
                    }
                )
        return results

    def apply_ai_relationship_evaluation(
        self,
        evaluation_id: int,
        *,
        relation_type: str,
        summary: str,
        confidence: float,
        evaluator: str,
    ) -> bool:
        relation_type = relation_type.strip()
        summary = summary.strip()
        if not relation_type or not summary:
            raise ValueError("AI relationship evaluation must include type and summary")
        confidence = max(0.0, min(1.0, float(confidence)))
        with self._guard, self._connection:
            row = self._connection.execute(
                """
                SELECT person_a_qq_id, person_b_qq_id, evidence_from_id,
                       evidence_to_id, evaluated_at
                FROM qq_relationship_evaluations WHERE id = ?
                """,
                (evaluation_id,),
            ).fetchone()
            if row is None:
                return False
            person_a, person_b, evidence_from, evidence_to, evaluated_at = row
            self._connection.execute(
                """
                UPDATE qq_relationship_evaluations
                SET relation_type = ?, summary = ?, confidence = ?, evaluator = ?
                WHERE id = ?
                """,
                (relation_type, summary, confidence, evaluator, evaluation_id),
            )
            self._connection.execute(
                """
                UPDATE qq_relationship_memories SET superseded_at = ?
                WHERE person_a_qq_id = ? AND person_b_qq_id = ?
                  AND memory_type = 'periodic_assessment' AND superseded_at IS NULL
                """,
                (evaluated_at, person_a, person_b),
            )
            self._connection.execute(
                """
                INSERT INTO qq_relationship_memories(
                    person_a_qq_id, person_b_qq_id, memory_type, content,
                    confidence, source_evidence_from_id, source_evidence_to_id,
                    created_at
                ) VALUES (?, ?, 'periodic_assessment', ?, ?, ?, ?, ?)
                """,
                (
                    person_a,
                    person_b,
                    summary,
                    confidence,
                    evidence_from,
                    evidence_to,
                    evaluated_at,
                ),
            )
            self._connection.execute(
                """
                UPDATE qq_relationships SET current_relation_type = ?,
                    current_summary = ?, confidence = ?
                WHERE person_a_qq_id = ? AND person_b_qq_id = ?
                """,
                (relation_type, summary, confidence, person_a, person_b),
            )
        return True

    @staticmethod
    def _statistical_relation_type(
        evidence_count: int,
        reciprocity: float,
        evidence_types: Mapping[str, int],
    ) -> str:
        direct_count = evidence_types.get("reply", 0) + evidence_types.get("mention", 0)
        if evidence_count < 3:
            return "insufficient_evidence"
        if direct_count >= 4 and reciprocity >= 0.65:
            return "mutual_interaction"
        if evidence_count >= 5 and reciprocity <= 0.2:
            return "one_sided_interaction"
        return "recurring_interaction"

    def get_sync_cursor(self, resource_type: str, resource_id: str) -> str | None:
        with self._guard:
            row = self._connection.execute(
                """
                SELECT cursor FROM qq_sync_state
                WHERE resource_type = ? AND resource_id = ?
                """,
                (resource_type, resource_id),
            ).fetchone()
            return row[0] if row else None

    def try_claim_sync(
        self,
        resource_type: str,
        resource_id: str,
        minimum_interval: timedelta,
        *,
        now: datetime | None = None,
    ) -> bool:
        checked_at = now or datetime.now(timezone.utc)
        checked_text = checked_at.isoformat()
        with self._guard, self._connection:
            row = self._connection.execute(
                """
                SELECT last_attempt_at FROM qq_sync_state
                WHERE resource_type = ? AND resource_id = ?
                """,
                (resource_type, resource_id),
            ).fetchone()
            if row and row[0]:
                last_attempt = datetime.fromisoformat(row[0])
                if checked_at - last_attempt < minimum_interval:
                    return False
            self._connection.execute(
                """
                INSERT INTO qq_sync_state(
                    resource_type, resource_id, last_attempt_at, raw_state_json
                ) VALUES (?, ?, ?, '{}')
                ON CONFLICT(resource_type, resource_id) DO UPDATE SET
                    last_attempt_at = excluded.last_attempt_at,
                    last_error = NULL
                """,
                (resource_type, resource_id, checked_text),
            )
            return True

    def _close_history(
        self, table: str, key: str, identity: str, valid_to: str
    ) -> None:
        self._connection.execute(
            f"UPDATE {table} SET valid_to = ? WHERE {key} = ? AND valid_to IS NULL",
            (valid_to, identity),
        )

    def _close_member_history(
        self, table: str, group_id: str, qq_id: str, valid_to: str
    ) -> None:
        self._connection.execute(
            f"""
            UPDATE {table} SET valid_to = ?
            WHERE group_id = ? AND qq_id = ? AND valid_to IS NULL
            """,
            (valid_to, group_id, qq_id),
        )

    def close(self) -> None:
        with self._guard:
            self._connection.close()
