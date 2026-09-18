from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import aiohttp

from .admin_store import DEFAULT_SETTINGS, EcobotAdminStore
from .structured import StructuredOutputError, parse_json_object
from .qq_archive import QQArchive


class QZoneSource(Protocol):
    async def fetch_new_posts(
        self, qq_id: str, cursor: str | None
    ) -> tuple[Sequence[Mapping[str, Any]], str | None]: ...


BinaryFetcher = Callable[[str], Awaitable[tuple[bytes, str | None, str | None]]]


class QQSyncCoordinator:
    """Rate-limited background enrichment for facts absent from message events."""

    def __init__(
        self,
        archive: QQArchive,
        *,
        binary_fetcher: BinaryFetcher | None = None,
        qzone_source: QZoneSource | None = None,
        profile_interval: timedelta = timedelta(hours=6),
        group_interval: timedelta = timedelta(hours=1),
        avatar_interval: timedelta = timedelta(hours=24),
        qzone_interval: timedelta = timedelta(minutes=15),
        relationship_scan_interval: timedelta = timedelta(minutes=30),
        relationship_evaluation_interval: timedelta = timedelta(days=7),
        relationship_minimum_new_evidence: int = 10,
        avatar_concurrency: int = 4,
        inventory_interval: timedelta = timedelta(hours=6),
        history_interval: timedelta = timedelta(hours=12),
        plugin_context: Any = None,
        admin_store: EcobotAdminStore | None = None,
    ) -> None:
        if avatar_concurrency < 1:
            raise ValueError("avatar_concurrency must be positive")
        self.archive = archive
        self.binary_fetcher = binary_fetcher or self._fetch_binary
        self.qzone_source = qzone_source
        self.profile_interval = profile_interval
        self.group_interval = group_interval
        self.avatar_interval = avatar_interval
        self.qzone_interval = qzone_interval
        self.relationship_scan_interval = relationship_scan_interval
        self.relationship_evaluation_interval = relationship_evaluation_interval
        self.relationship_minimum_new_evidence = relationship_minimum_new_evidence
        self.inventory_interval = inventory_interval
        self.history_interval = history_interval
        self.plugin_context = plugin_context
        self.admin_store = admin_store
        self.settings = dict(DEFAULT_SETTINGS)
        self._avatar_slots = asyncio.Semaphore(avatar_concurrency)
        self._tasks: set[asyncio.Task[None]] = set()

    def apply_settings(self, settings: Mapping[str, Any]) -> None:
        self.settings = {**DEFAULT_SETTINGS, **settings}
        self.relationship_scan_interval = timedelta(
            seconds=int(self.settings["relationship_scan_interval_seconds"])
        )
        self.relationship_evaluation_interval = timedelta(
            hours=int(self.settings["relationship_evaluation_interval_hours"])
        )
        self.relationship_minimum_new_evidence = int(
            self.settings["relationship_minimum_new_evidence"]
        )

    def schedule(self, event: Any) -> None:
        if event.get_platform_name() != "aiocqhttp" or not hasattr(event, "bot"):
            return
        task = asyncio.create_task(self.sync_event(event), name="ecobot-qq-fact-sync")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def sync_event(self, event: Any) -> None:
        qq_id = str(event.get_sender_id() or "").strip()
        group_id = str(event.get_group_id() or "").strip()
        bot_qq_id = str(event.get_self_id() or "").strip()
        if not qq_id or not bot_qq_id:
            return
        await asyncio.gather(
            self._sync_user_profile(event, qq_id),
            self._sync_user_avatar(qq_id),
            self._sync_qzone(qq_id),
            self._sync_inventory(event, bot_qq_id),
            self._sync_message_media(event),
            self._sync_group(event, group_id, bot_qq_id) if group_id else self._noop(),
        )
        await self._evaluate_relationships(str(getattr(event, "unified_msg_origin", "")))

    async def _evaluate_relationships(self, channel_id: str) -> None:
        if not self.archive.try_claim_sync(
            "qq_relationship_evaluation",
            "global",
            self.relationship_scan_interval,
        ):
            return
        try:
            evaluations = await asyncio.to_thread(
                self.archive.evaluate_due_relationships,
                minimum_new_evidence=self.relationship_minimum_new_evidence,
                maximum_interval=self.relationship_evaluation_interval,
            )
            if self.settings["relationship_ai_enabled"]:
                for evaluation in evaluations:
                    await self._review_relationship(channel_id, evaluation)
            self.archive.update_sync_state(
                "qq_relationship_evaluation", "global", success=True
            )
        except Exception as exc:
            self.archive.update_sync_state(
                "qq_relationship_evaluation",
                "global",
                success=False,
                error=str(exc),
            )

    async def _review_relationship(
        self, channel_id: str, evaluation: Mapping[str, Any]
    ) -> None:
        if self.plugin_context is None:
            return
        configured_id = str(self.settings["relationship_provider_id"] or "")
        provider = (
            self.plugin_context.get_provider_by_id(configured_id)
            if configured_id
            else await self.plugin_context.get_using_provider_async(channel_id)
        )
        if provider is None and configured_id:
            provider = await self.plugin_context.get_using_provider_async(channel_id)
        provider_id = configured_id or "session"
        if provider is None:
            return
        system_prompt = (
            "You review relationship evidence for Ecobot. Return one JSON object only. "
            "Do not invent facts. relation_type and summary must be concise.\n"
            f"Additional constraints:\n{self.settings['relationship_prompt']}"
        )
        input_prompt = (
            f"statistical_evaluation={json.dumps(dict(evaluation), ensure_ascii=False)}\n"
            'schema={"relation_type":"string","summary":"string","confidence":0.0}'
        )
        started = time.perf_counter()
        output = None
        error = None
        try:
            prompt = input_prompt
            data = None
            for attempt in range(int(self.settings["structured_output_retries"]) + 1):
                response = await asyncio.wait_for(
                    provider.text_chat(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        session_id=channel_id,
                        temperature=float(self.settings["generation_temperature"]),
                        top_p=float(self.settings["generation_top_p"]),
                        max_tokens=int(self.settings["generation_max_tokens"]),
                        request_max_retries=max(
                            1, int(self.settings["request_max_retries"]) + 1
                        ),
                    ),
                    timeout=float(self.settings["model_timeout_seconds"]),
                )
                output = response.completion_text
                try:
                    data = parse_json_object(output or "")
                    break
                except StructuredOutputError as exc:
                    if attempt >= int(self.settings["structured_output_retries"]):
                        raise
                    prompt += f"\nPrevious output was invalid: {exc}. Return valid JSON only."
            if data is None:
                return
            await asyncio.to_thread(
                self.archive.apply_ai_relationship_evaluation,
                int(evaluation["evaluation_id"]),
                relation_type=str(data.get("relation_type") or ""),
                summary=str(data.get("summary") or ""),
                confidence=float(data.get("confidence", 0.0)),
                evaluator=f"ecobot-ai:{provider_id}",
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if self.admin_store is not None:
                self.admin_store.record_phase_trace(
                    batch_id=None,
                    channel_id=channel_id,
                    phase="relationship",
                    provider_id=provider_id,
                    status="error" if error else "success",
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    system_prompt=system_prompt,
                    input_prompt=input_prompt,
                    output_text=output,
                    error=error,
                )

    async def _sync_user_profile(self, event: Any, qq_id: str) -> None:
        resource = f"profile:{qq_id}"
        if not self.archive.try_claim_sync("qq_user_profile", resource, self.profile_interval):
            return
        try:
            profile = await event.bot.call_action(
                "get_stranger_info",
                user_id=int(qq_id) if qq_id.isdigit() else qq_id,
                no_cache=True,
                **self._routing_params(event),
            )
            if isinstance(profile, Mapping):
                await asyncio.to_thread(self.archive.record_user_profile, profile)
            self.archive.update_sync_state("qq_user_profile", resource, success=True)
        except Exception as exc:
            self.archive.update_sync_state(
                "qq_user_profile", resource, success=False, error=str(exc)
            )

    async def _sync_group(self, event: Any, group_id: str, bot_qq_id: str) -> None:
        resource = f"group:{group_id}"
        if not self.archive.try_claim_sync("qq_group_snapshot", resource, self.group_interval):
            return
        api_group_id = int(group_id) if group_id.isdigit() else group_id
        try:
            group, members = await asyncio.gather(
                event.bot.call_action(
                    "get_group_info",
                    group_id=api_group_id,
                    no_cache=True,
                    **self._routing_params(event),
                ),
                event.bot.call_action(
                    "get_group_member_list",
                    group_id=api_group_id,
                    no_cache=True,
                    **self._routing_params(event),
                ),
            )
            if not isinstance(group, Mapping) or not isinstance(members, list):
                raise TypeError("NapCat returned an invalid group snapshot")
            typed_members = [item for item in members if isinstance(item, Mapping)]
            await asyncio.to_thread(
                self.archive.record_group_snapshot,
                group,
                typed_members,
                bot_qq_id=bot_qq_id,
            )
            self.archive.update_sync_state("qq_group_snapshot", resource, success=True)
            await asyncio.gather(
                self._sync_group_avatar(group_id),
                *(self._sync_user_avatar(str(item["user_id"])) for item in typed_members if item.get("user_id") is not None),
            )
            await self._backfill_group_history(event, group_id, bot_qq_id)
        except Exception as exc:
            self.archive.update_sync_state(
                "qq_group_snapshot", resource, success=False, error=str(exc)
            )

    async def _sync_user_avatar(self, qq_id: str) -> None:
        resource = f"user:{qq_id}"
        if not self.archive.try_claim_sync("qq_avatar", resource, self.avatar_interval):
            return
        url = f"https://q1.qlogo.cn/g?b=qq&nk={quote(qq_id)}&s=640"
        try:
            async with self._avatar_slots:
                content, etag, mime_type = await self.binary_fetcher(url)
            content_hash = await asyncio.to_thread(
                self.archive.record_binary_asset,
                content,
                media_type="qq_user_avatar",
                mime_type=mime_type,
                source_url=url,
            )
            await asyncio.to_thread(
                self.archive.record_user_avatar,
                qq_id,
                url,
                content_hash=content_hash,
                etag=etag,
            )
            self.archive.update_sync_state("qq_avatar", resource, success=True)
        except Exception as exc:
            self.archive.update_sync_state(
                "qq_avatar", resource, success=False, error=str(exc)
            )

    async def _sync_group_avatar(self, group_id: str) -> None:
        resource = f"group:{group_id}"
        if not self.archive.try_claim_sync("qq_group_avatar", resource, self.avatar_interval):
            return
        quoted = quote(group_id)
        url = f"https://p.qlogo.cn/gh/{quoted}/{quoted}/640/"
        try:
            async with self._avatar_slots:
                content, etag, mime_type = await self.binary_fetcher(url)
            content_hash = await asyncio.to_thread(
                self.archive.record_binary_asset,
                content,
                media_type="qq_group_avatar",
                mime_type=mime_type,
                source_url=url,
            )
            await asyncio.to_thread(
                self.archive.record_group_avatar,
                group_id,
                url,
                content_hash=content_hash,
                etag=etag,
            )
            self.archive.update_sync_state("qq_group_avatar", resource, success=True)
        except Exception as exc:
            self.archive.update_sync_state(
                "qq_group_avatar", resource, success=False, error=str(exc)
            )

    async def _sync_qzone(self, qq_id: str) -> None:
        if self.qzone_source is None:
            return
        resource = f"user:{qq_id}"
        if not self.archive.try_claim_sync("qq_qzone", resource, self.qzone_interval):
            return
        try:
            posts, cursor = await self.qzone_source.fetch_new_posts(
                qq_id, self.archive.get_sync_cursor("qq_qzone", resource)
            )
            for post in posts:
                post_id = str(post.get("post_id") or post.get("tid") or "").strip()
                if not post_id:
                    continue
                media = post.get("media")
                stored_media = await self._store_remote_media(
                    media if isinstance(media, list) else (),
                    media_type_prefix="qq_qzone",
                )
                await asyncio.to_thread(
                    self.archive.record_qzone_post,
                    qq_id,
                    post_id,
                    str(post.get("content") or post.get("content_text") or ""),
                    created_at=self._optional_int(post.get("created_at") or post.get("time")),
                    post_type=str(post.get("post_type") or "") or None,
                    media=stored_media,
                    raw=post,
                )
            self.archive.update_sync_state(
                "qq_qzone", resource, cursor=cursor, success=True
            )
        except Exception as exc:
            self.archive.update_sync_state(
                "qq_qzone", resource, success=False, error=str(exc)
            )

    async def _sync_inventory(self, event: Any, bot_qq_id: str) -> None:
        if not self.archive.try_claim_sync(
            "qq_inventory", f"bot:{bot_qq_id}", self.inventory_interval
        ):
            return
        try:
            friends, groups = await asyncio.gather(
                event.bot.call_action("get_friend_list", **self._routing_params(event)),
                event.bot.call_action("get_group_list", **self._routing_params(event)),
            )
            if not isinstance(friends, list) or not isinstance(groups, list):
                raise TypeError("NapCat returned an invalid QQ inventory")
            typed_friends = [item for item in friends if isinstance(item, Mapping)]
            typed_groups = [item for item in groups if isinstance(item, Mapping)]
            await asyncio.gather(
                asyncio.to_thread(
                    self.archive.record_friend_inventory, bot_qq_id, typed_friends
                ),
                asyncio.to_thread(
                    self.archive.record_group_inventory, bot_qq_id, typed_groups
                ),
            )
            self.archive.update_sync_state(
                "qq_inventory", f"bot:{bot_qq_id}", success=True
            )
        except Exception as exc:
            self.archive.update_sync_state(
                "qq_inventory", f"bot:{bot_qq_id}", success=False, error=str(exc)
            )

    async def _backfill_group_history(
        self, event: Any, group_id: str, bot_qq_id: str
    ) -> None:
        resource = f"group:{group_id}"
        if not self.archive.try_claim_sync(
            "qq_group_history", resource, self.history_interval
        ):
            return
        try:
            response = await event.bot.call_action(
                "get_group_msg_history",
                group_id=int(group_id) if group_id.isdigit() else group_id,
                count=100,
                **self._routing_params(event),
            )
            if isinstance(response, Mapping):
                messages = response.get("messages") or response.get("message_list") or []
            else:
                messages = response
            if not isinstance(messages, list):
                raise TypeError("NapCat returned an invalid group history response")
            imported = 0
            for message in messages:
                if not isinstance(message, Mapping):
                    continue
                raw = dict(message)
                raw.setdefault("self_id", bot_qq_id)
                raw.setdefault("group_id", group_id)
                raw.setdefault("post_type", "message")
                raw.setdefault("message_type", "group")
                if await asyncio.to_thread(self.archive.record_raw_onebot_message, raw):
                    imported += 1
            self.archive.update_sync_state(
                "qq_group_history",
                resource,
                cursor=str(imported),
                success=True,
                raw={"received": len(messages), "imported": imported},
            )
        except Exception as exc:
            self.archive.update_sync_state(
                "qq_group_history", resource, success=False, error=str(exc)
            )

    async def _sync_message_media(self, event: Any) -> None:
        message_obj = getattr(event, "message_obj", None)
        message_id = str(getattr(message_obj, "message_id", "") or "").strip()
        if not message_id:
            return
        pending = await asyncio.to_thread(
            self.archive.pending_message_media, message_id
        )
        for item in pending:
            source_url = item.get("source_url")
            source_file = item.get("source_file")
            if not source_url and isinstance(source_file, str) and source_file.startswith(("http://", "https://")):
                source_url = source_file
            try:
                if source_url:
                    content, _, mime_type = await self.binary_fetcher(str(source_url))
                elif source_file and Path(str(source_file)).is_file():
                    content = await asyncio.to_thread(Path(str(source_file)).read_bytes)
                    mime_type = None
                else:
                    raise ValueError("message media has no readable source")
                content_hash = await asyncio.to_thread(
                    self.archive.record_binary_asset,
                    content,
                    media_type=f"qq_message_{item['media_type']}",
                    mime_type=mime_type,
                    source_url=str(source_url) if source_url else None,
                )
                await asyncio.to_thread(
                    self.archive.record_message_media,
                    message_row_id=item["message_row_id"],
                    position=item["position"],
                    media_type=item["media_type"],
                    source_url=str(source_url) if source_url else None,
                    source_file=str(source_file) if source_file else None,
                    content_hash=content_hash,
                    success=True,
                    metadata=item["metadata"],
                )
            except Exception as exc:
                await asyncio.to_thread(
                    self.archive.record_message_media,
                    message_row_id=item["message_row_id"],
                    position=item["position"],
                    media_type=item["media_type"],
                    source_url=str(source_url) if source_url else None,
                    source_file=str(source_file) if source_file else None,
                    content_hash=None,
                    success=False,
                    error=str(exc),
                    metadata=item["metadata"],
                )

    async def _store_remote_media(
        self,
        media: Sequence[Mapping[str, Any]],
        *,
        media_type_prefix: str,
    ) -> list[dict[str, Any]]:
        stored: list[dict[str, Any]] = []
        for item in media:
            value = dict(item)
            url = str(value.get("url") or value.get("media_url") or "").strip()
            if url.startswith(("http://", "https://")):
                try:
                    content, _, mime_type = await self.binary_fetcher(url)
                    value["content_hash"] = await asyncio.to_thread(
                        self.archive.record_binary_asset,
                        content,
                        media_type=f"{media_type_prefix}_{value.get('type') or 'media'}",
                        mime_type=mime_type,
                        source_url=url,
                    )
                except Exception as exc:
                    value["archive_error"] = str(exc)
            stored.append(value)
        return stored

    @staticmethod
    def _routing_params(event: Any) -> dict[str, Any]:
        self_id = str(event.get_self_id() or "").strip()
        return {"self_id": int(self_id) if self_id.isdigit() else self_id} if self_id else {}

    @staticmethod
    async def _fetch_binary(
        url: str,
    ) -> tuple[bytes, str | None, str | None]:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                return (
                    await response.read(),
                    response.headers.get("ETag"),
                    response.headers.get("Content-Type"),
                )

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    async def _noop() -> None:
        return None

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
