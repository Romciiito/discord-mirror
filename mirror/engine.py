from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from typing import Any

import aiohttp

from .discord_api import ApiError, DiscordHTTP, clean_webhook, load_properties, webhook_parts
from .gateway import Gateway
from .relay import Relay, safe_embeds, view_from_message
from .store import Store

log = logging.getLogger("mirror.engine")

TEXT_TYPES = {0, 5, 15}


class Engine:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.session: aiohttp.ClientSession | None = None
        self.http: DiscordHTTP | None = None
        self.gateway: Gateway | None = None
        self.relay: Relay | None = None
        self.user: dict[str, Any] | None = None
        self.running = False
        self.status = "idle"
        self.lines: deque[str] = deque(maxlen=60)
        self.feed: dict[str, dict[str, Any]] = {}
        self.order: deque[str] = deque()
        self.listeners: set[asyncio.Queue] = set()
        self.backfilling = False
        self.holding: list[tuple[str, dict[str, Any]]] = []
        self.lock = asyncio.Lock()
        self.names: dict[str, tuple[str, str]] = {}
        self.selected: set[str] = set()
        self.threads: dict[str, str] = {}
        self.include_threads = False
        self._properties: dict[str, Any] | None = None
        self._backfill_task: asyncio.Task | None = None

    async def open(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40))
        self.relay = Relay(self.session)

    async def close(self) -> None:
        await self.stop()
        if self.session is not None:
            await self.session.close()
            self.session = None

    def note(self, text: str) -> None:
        self.lines.appendleft(text)
        self._emit({"kind": "log", "text": text})

    def snapshot(self) -> dict[str, Any]:
        return {
            "user": self.user,
            "running": self.running,
            "status": self.status,
            "has_token": self.http is not None,
            "options": self.store.options(),
            "selection": self.store.selection(),
            "log": list(self.lines),
        }

    def feed_items(self) -> list[dict[str, Any]]:
        return [self.feed[mid] for mid in self.order if mid in self.feed]

    async def restore(self) -> None:
        token = self.store.token()
        if not token:
            return
        try:
            await self.use_token(token, keep=True)
            self.note("saved token accepted")
        except Exception:
            self.note("saved token was rejected")
            self.store.forget_token()

    async def use_token(self, token: str, keep: bool) -> dict[str, Any]:
        token = token.strip()
        if len(token) < 40:
            raise ApiError(400, "token looks too short")
        if self.session is None:
            raise ApiError(500, "session is not ready")
        if self._properties is None:
            self._properties = await load_properties(self.session)
        http = DiscordHTTP(self.session, token, self._properties)
        me = await http.me()
        self.http = http
        self.user = {
            "id": str(me.get("id") or ""),
            "username": me.get("username") or "",
            "global_name": me.get("global_name") or "",
        }
        self.store.set_token(token, keep)
        self.status = "ready"
        return self.user

    async def forget(self) -> None:
        await self.stop()
        self.http = None
        self.user = None
        self.status = "idle"
        self.store.forget_token()
        self.note("token cleared")

    async def guilds(self) -> list[dict[str, Any]]:
        http = self._require_http()
        rows = []
        for guild in await http.guilds():
            rows.append(
                {
                    "id": str(guild.get("id")),
                    "name": guild.get("name") or "server",
                    "icon": guild.get("icon") or "",
                }
            )
        rows.sort(key=lambda item: item["name"].casefold())
        return rows

    async def channels(self, guild_id: str) -> list[dict[str, Any]]:
        if not guild_id.isdigit():
            raise ApiError(400, "unknown server")
        http = self._require_http()
        raw = await http.channels(guild_id)
        by_id = {str(item.get("id")): item for item in raw if isinstance(item, dict)}
        grouped: list[dict[str, Any]] = []
        parents = [item for item in raw if item.get("type") == 4]
        parents.sort(key=lambda item: item.get("position") or 0)
        loose = [item for item in raw if item.get("type") in TEXT_TYPES and not item.get("parent_id")]
        loose.sort(key=lambda item: item.get("position") or 0)

        def pack(item: dict[str, Any], parent_name: str) -> dict[str, Any]:
            return {
                "id": str(item.get("id")),
                "name": item.get("name") or "channel",
                "type": item.get("type"),
                "parent": parent_name,
            }

        for item in loose:
            grouped.append(pack(item, ""))
        for parent in parents:
            parent_name = parent.get("name") or ""
            children = [
                item
                for item in raw
                if item.get("type") in TEXT_TYPES and str(item.get("parent_id") or "") == str(parent.get("id"))
            ]
            children.sort(key=lambda item: item.get("position") or 0)
            for item in children:
                grouped.append(pack(item, parent_name))
        seen = {item["id"] for item in grouped}
        for item in raw:
            if item.get("type") in TEXT_TYPES and str(item.get("id")) not in seen:
                parent = by_id.get(str(item.get("parent_id") or ""))
                grouped.append(pack(item, (parent or {}).get("name") or ""))
        return grouped

    def save_setup(self, body: dict[str, Any]) -> None:
        try:
            backfill = int(body.get("backfill") or 0)
        except (TypeError, ValueError):
            backfill = 0
        backfill = max(0, min(backfill, 500))
        mirror = bool(body.get("mirror"))
        include_threads = bool(body.get("include_threads"))
        global_webhook = clean_webhook(str(body.get("global_webhook") or ""))
        if mirror and not global_webhook:
            per_channel = False
            for row in body.get("channels") or []:
                if row.get("enabled", True) and str(row.get("webhook_url") or "").strip():
                    per_channel = True
                    break
            if not per_channel:
                raise ApiError(400, "add a webhook url, or turn the discord copy off")
        cleaned = []
        for row in body.get("channels") or []:
            channel_id = str(row.get("channel_id") or row.get("id") or "")
            guild_id = str(row.get("guild_id") or "")
            if not channel_id.isdigit() or not guild_id.isdigit():
                continue
            hook = clean_webhook(str(row.get("webhook_url") or ""))
            cleaned.append(
                {
                    "channel_id": channel_id,
                    "guild_id": guild_id,
                    "guild_name": str(row.get("guild_name") or "")[:80],
                    "channel_name": str(row.get("channel_name") or row.get("name") or "")[:80],
                    "webhook_url": hook,
                    "enabled": bool(row.get("enabled", True)),
                }
            )
        self.store.set_options(backfill, include_threads, global_webhook, mirror)
        self.store.replace_selection(cleaned)

    async def start(self) -> None:
        if self.running:
            return
        http = self._require_http()
        options = self.store.options()
        rows = [row for row in self.store.selection() if row["enabled"]]
        if not rows:
            raise ApiError(400, "pick at least one channel")
        self._index(rows, options["include_threads"])
        if options["include_threads"]:
            await self._load_threads(http, rows)
        self.gateway = Gateway(http, self.on_dispatch, self._fatal)
        self.gateway.set_subscriptions(self._guild_map(rows), options["include_threads"])
        self.backfilling = options["backfill"] > 0
        self.holding.clear()
        self.running = True
        self.status = "connecting"
        self.note(f"connecting, {len(rows)} channel(s)")
        self.gateway.start()
        if options["backfill"] > 0:
            self._backfill_task = asyncio.create_task(self._backfill(rows, options["backfill"]))
        self._emit({"kind": "status", "running": True, "status": self.status})

    async def refresh(self) -> None:
        if not self.running or self.gateway is None or self.http is None:
            return
        options = self.store.options()
        rows = [row for row in self.store.selection() if row["enabled"]]
        if not rows:
            raise ApiError(400, "pick at least one channel")
        self._index(rows, options["include_threads"])
        if options["include_threads"]:
            await self._load_threads(self.http, rows)
        self.gateway.set_subscriptions(self._guild_map(rows), options["include_threads"])
        await self.gateway.resubscribe()
        self.note(f"selection updated, {len(rows)} channel(s)")

    async def stop(self) -> None:
        self.running = False
        self.backfilling = False
        task = self._backfill_task
        self._backfill_task = None
        if task is not None:
            task.cancel()
        gateway = self.gateway
        self.gateway = None
        if gateway is not None:
            await gateway.stop()
        self.status = "stopped"
        self.note("stopped")
        self._emit({"kind": "status", "running": False, "status": self.status})

    async def _fatal(self, text: str) -> None:
        self.running = False
        self.backfilling = False
        self.status = "error"
        self.note(text)
        self._emit({"kind": "status", "running": False, "status": self.status})

    async def on_dispatch(self, event: str, data: dict[str, Any]) -> None:
        if event == "READY":
            self.status = "live"
            self.note("live")
            self._emit({"kind": "status", "running": True, "status": self.status})
            return
        if event == "RESUMED":
            self.status = "live"
            self.note("resumed")
            return
        if not self.running:
            return
        async with self.lock:
            if self.backfilling and event.startswith("MESSAGE"):
                self.holding.append((event, data))
                return
        await self._handle(event, data)

    async def _backfill(self, rows: list[dict], limit: int) -> None:
        http = self.http
        try:
            if http is None:
                return
            for row in rows:
                if not self.running:
                    return
                try:
                    messages = await http.history(str(row["channel_id"]), limit)
                except Exception:
                    self.note(f"history skipped for #{row.get('channel_name') or row['channel_id']}")
                    continue
                self.note(f"history #{row.get('channel_name') or row['channel_id']} ({len(messages)})")
                for message in messages:
                    if not self.running:
                        return
                    await self._handle("MESSAGE_CREATE", message)
        finally:
            pending: list[tuple[str, dict[str, Any]]]
            async with self.lock:
                self.backfilling = False
                pending = list(self.holding)
                self.holding.clear()
            for event, data in pending:
                if self.running:
                    await self._handle(event, data)
            if self.running:
                self.note("history done")

    async def _handle(self, event: str, data: dict[str, Any]) -> None:
        if event == "THREAD_CREATE" and self.include_threads:
            parent = str(data.get("parent_id") or "")
            if parent in self.selected:
                thread_id = str(data.get("id") or "")
                guild_name = self.names.get(parent, ("", ""))[0]
                self.threads[thread_id] = parent
                self.names[thread_id] = (guild_name, str(data.get("name") or "thread"))
            return
        if event == "MESSAGE_CREATE":
            await self._create(data)
        elif event == "MESSAGE_UPDATE":
            await self._update(data)
        elif event == "MESSAGE_DELETE":
            await self._delete(str(data.get("id") or ""), str(data.get("channel_id") or ""))
        elif event == "MESSAGE_DELETE_BULK":
            channel_id = str(data.get("channel_id") or "")
            for message_id in data.get("ids") or []:
                await self._delete(str(message_id), channel_id)
        elif event in {"MESSAGE_REACTION_ADD", "MESSAGE_REACTION_REMOVE"}:
            self._reaction(event, data)

    async def _create(self, message: dict[str, Any]) -> None:
        channel_id = str(message.get("channel_id") or "")
        if channel_id not in self.names and channel_id not in self.threads:
            return
        if self._own_webhook(message):
            return
        message_id = str(message.get("id") or "")
        if not message_id or message_id in self.feed:
            return
        if not _worth_showing(message):
            return
        guild_name, channel_name = self.names.get(channel_id, ("", channel_id))
        view = view_from_message(message, channel_name, guild_name)
        self._push(view)
        await self._relay_create(view)

    async def _update(self, message: dict[str, Any]) -> None:
        channel_id = str(message.get("channel_id") or "")
        if channel_id not in self.names and channel_id not in self.threads:
            return
        message_id = str(message.get("id") or "")
        view = self.feed.get(message_id)
        if view is None:
            if _worth_showing(message):
                await self._create(message)
            return
        if "content" in message:
            view["content"] = str(message.get("content") or "")
        if "embeds" in message:
            view["embeds"] = safe_embeds(message)
        if message.get("edited_timestamp"):
            view["edited"] = message.get("edited_timestamp")
        self._emit({"kind": "message", "message": view})
        row = self.store.relay_row(message_id)
        if row and self.store.options()["mirror"]:
            await self.relay.edit(row["webhook_url"], row["webhook_message_id"], view, self._prefix(row["webhook_url"]))

    async def _delete(self, message_id: str, channel_id: str) -> None:
        if channel_id and channel_id not in self.names and channel_id not in self.threads:
            return
        view = self.feed.get(message_id)
        if view is not None:
            view["deleted"] = True
            self._emit({"kind": "message", "message": view})
        row = self.store.relay_row(message_id)
        if row and self.store.options()["mirror"]:
            await self.relay.remove(row["webhook_url"], row["webhook_message_id"])
            self.store.drop_relay(message_id)

    def _reaction(self, event: str, data: dict[str, Any]) -> None:
        channel_id = str(data.get("channel_id") or "")
        if channel_id not in self.names and channel_id not in self.threads:
            return
        message_id = str(data.get("message_id") or "")
        view = self.feed.get(message_id)
        if view is None:
            return
        emoji = data.get("emoji") or {}
        name = emoji.get("name") or "reaction"
        counts = view.setdefault("reactions", {})
        current = int(counts.get(name) or 0)
        if event == "MESSAGE_REACTION_ADD":
            counts[name] = current + 1
        else:
            counts[name] = max(0, current - 1)
            if counts[name] == 0:
                counts.pop(name, None)
        self._emit({"kind": "message", "message": view})

    async def _relay_create(self, view: dict[str, Any]) -> None:
        options = self.store.options()
        if not options["mirror"] or self.relay is None:
            return
        if self.store.relay_row(view["id"]):
            return
        url = self._webhook_for(view["channel_id"])
        if not url:
            return
        sent = await self.relay.create(url, view, self._prefix(url))
        if sent:
            self.store.remember_relay(view["id"], view["channel_id"], url, sent)

    def _webhook_for(self, channel_id: str) -> str:
        parent = self.threads.get(channel_id, channel_id)
        options = self.store.options()
        for row in self.store.selection():
            if row["channel_id"] == parent and row["enabled"]:
                return row["webhook_url"] or options["global_webhook"]
        return ""

    def _prefix(self, url: str) -> bool:
        users = 0
        options = self.store.options()
        for row in self.store.selection():
            if not row["enabled"]:
                continue
            target = row["webhook_url"] or options["global_webhook"]
            if target == url:
                users += 1
        return users > 1

    def _own_webhook(self, message: dict[str, Any]) -> bool:
        hook = str(message.get("webhook_id") or "")
        if not hook:
            return False
        options = self.store.options()
        known = set()
        if options["global_webhook"]:
            parts = webhook_parts(options["global_webhook"])
            if parts:
                known.add(parts[0])
        for row in self.store.selection():
            parts = webhook_parts(row.get("webhook_url") or "")
            if parts:
                known.add(parts[0])
        return hook in known

    def _index(self, rows: list[dict], include_threads: bool) -> None:
        self.include_threads = include_threads
        self.names = {}
        self.selected = set()
        self.threads = {}
        for row in rows:
            self.selected.add(row["channel_id"])
            self.names[row["channel_id"]] = (row.get("guild_name") or "", row.get("channel_name") or row["channel_id"])

    async def _load_threads(self, http: DiscordHTTP, rows: list[dict]) -> None:
        guilds = {row["guild_id"] for row in rows}
        for guild_id in guilds:
            for thread in await http.active_threads(guild_id):
                parent = str(thread.get("parent_id") or "")
                if parent not in self.selected:
                    continue
                thread_id = str(thread.get("id") or "")
                guild_name = self.names.get(parent, ("", ""))[0]
                self.threads[thread_id] = parent
                self.names[thread_id] = (guild_name, str(thread.get("name") or "thread"))

    def _guild_map(self, rows: list[dict]) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        parent_guild = {}
        for row in rows:
            grouped[row["guild_id"]].append(row["channel_id"])
            parent_guild[row["channel_id"]] = row["guild_id"]
        for thread_id, parent in self.threads.items():
            guild_id = parent_guild.get(parent)
            if guild_id and thread_id not in grouped[guild_id]:
                grouped[guild_id].append(thread_id)
        return grouped

    def _push(self, view: dict[str, Any]) -> None:
        message_id = view["id"]
        self.feed[message_id] = view
        if message_id not in self.order:
            self.order.append(message_id)
        while len(self.order) > 500:
            old = self.order.popleft()
            self.feed.pop(old, None)
        self._emit({"kind": "message", "message": view})

    def _emit(self, item: dict[str, Any]) -> None:
        dead = []
        for queue in self.listeners:
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                dead.append(queue)
        for queue in dead:
            self.listeners.discard(queue)

    def _require_http(self) -> DiscordHTTP:
        if self.http is None:
            raise ApiError(401, "add a token first")
        return self.http


def _worth_showing(message: dict[str, Any]) -> bool:
    if message.get("content"):
        return True
    if message.get("attachments") or message.get("embeds") or message.get("sticker_items"):
        return True
    return False
