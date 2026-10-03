from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from typing import Any

import aiohttp

from .access import TEXT_TYPES, readable_plan
from .provision import destination_layout, webhook_name
from .discord_api import ApiError, DiscordHTTP, clean_webhook, load_properties, webhook_parts
from .gateway import Gateway
from .relay import Relay, safe_embeds, view_from_message
from .store import Store

log = logging.getLogger("mirror.engine")

GONE = 'the mirror server is gone or cannot be used, pick "new server on next start"'


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
        self._restore_task: asyncio.Task | None = None
        self._setup = asyncio.Lock()

    async def open(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40))
        self.relay = Relay(self.session)

    async def close(self) -> None:
        await self._cancel_restore()
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
        except ApiError as exc:
            if exc.status in (401, 403):
                self.note("saved token was rejected")
                self.store.forget_token()
                return
            self._retry_later(token, exc)
        except Exception as exc:
            self._retry_later(token, exc)

    def _retry_later(self, token: str, exc: Exception) -> None:
        reason = str(exc) or "network error"
        self.note(f"saved token check failed ({reason}), retrying")
        self._restore_task = asyncio.create_task(self._retry_token(token))

    async def _retry_token(self, token: str) -> None:
        delay = 5.0
        try:
            while True:
                await self._wait(delay)
                if self.http is not None or self.store.token() != token:
                    return
                try:
                    await self.use_token(token, keep=True)
                except ApiError as exc:
                    if exc.status in (401, 403):
                        self.note("saved token was rejected")
                        self.store.forget_token()
                        return
                except Exception:
                    pass
                else:
                    self.note("saved token accepted")
                    return
                delay = min(delay * 2, 120.0)
        finally:
            if self._restore_task is asyncio.current_task():
                self._restore_task = None

    async def _cancel_restore(self) -> None:
        task = self._restore_task
        if task is None or task is asyncio.current_task():
            return
        self._restore_task = None
        task.cancel()
        await asyncio.wait({task})

    async def _wait(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def use_token(self, token: str, keep: bool) -> dict[str, Any]:
        await self._cancel_restore()
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
        await self._cancel_restore()
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
        listed = await self._listed_guild(guild_id)
        raw = await http.channels(guild_id)
        role_ids = await self._role_ids(guild_id)
        try:
            base = int(listed.get("permissions") or 0)
        except (TypeError, ValueError):
            base = 0
        user_id = self.user["id"] if self.user else ""
        allowed = {
            str(item.get("id"))
            for item in readable_plan(
                raw,
                base=base,
                owner=bool(listed.get("owner")),
                user_id=user_id,
                guild_id=guild_id,
                role_ids=role_ids,
            )["channels"]
        }
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
                "topic": item.get("topic") or "",
            }

        def take(item: dict[str, Any], parent_name: str) -> None:
            if str(item.get("id")) in allowed:
                grouped.append(pack(item, parent_name))

        for item in loose:
            take(item, "")
        for parent in parents:
            parent_name = parent.get("name") or ""
            children = [
                item
                for item in raw
                if item.get("type") in TEXT_TYPES and str(item.get("parent_id") or "") == str(parent.get("id"))
            ]
            children.sort(key=lambda item: item.get("position") or 0)
            for item in children:
                take(item, parent_name)
        seen = {item["id"] for item in grouped}
        for item in raw:
            if item.get("type") in TEXT_TYPES and str(item.get("id")) in allowed and str(item.get("id")) not in seen:
                parent = by_id.get(str(item.get("parent_id") or ""))
                grouped.append(pack(item, (parent or {}).get("name") or ""))
        return grouped

    async def _listed_guild(self, guild_id: str) -> dict[str, Any]:
        for guild in await self._require_http().guilds():
            if str(guild.get("id")) == guild_id:
                return guild
        raise ApiError(404, "server not found")

    async def _role_ids(self, guild_id: str) -> list[str]:
        try:
            member = await self._require_http().call("GET", f"/users/@me/guilds/{guild_id}/member")
        except ApiError:
            return []
        if not isinstance(member, dict):
            return []
        return [str(role) for role in member.get("roles") or []]

    async def copy_guild(self, guild_id: str) -> dict[str, Any]:
        if not guild_id.isdigit():
            raise ApiError(400, "unknown server")
        http = self._require_http()
        listed = None
        for guild in await http.guilds():
            if str(guild.get("id")) == guild_id:
                listed = guild
                break
        if listed is None:
            raise ApiError(404, "server not found")
        raw = await http.channels(guild_id)
        role_ids: list[str] = []
        try:
            member = await http.call("GET", f"/users/@me/guilds/{guild_id}/member")
            if isinstance(member, dict):
                role_ids = [str(role) for role in member.get("roles") or []]
        except ApiError:
            role_ids = []
        user_id = self.user["id"] if self.user else ""
        try:
            base = int(listed.get("permissions") or 0)
        except (TypeError, ValueError):
            base = 0
        plan = readable_plan(
            raw,
            base=base,
            owner=bool(listed.get("owner")),
            user_id=user_id,
            guild_id=guild_id,
            role_ids=role_ids,
        )
        if not plan["channels"]:
            raise ApiError(400, "no channel in that server can be read")
        name = str(listed.get("name") or "server")[:100]
        self.note(f"copying {name}")
        for item in plan["skipped"]:
            self.note(f"skip #{item['name']} ({item['reason']})")
        if plan["other"]:
            self.note(f"left out {plan['other']} channel(s) that are not text")
        if any(item.get("type") == 5 for item in plan["channels"]):
            self.note("announcement channels are created as text channels")
        created = await http.call("POST", "/guilds", json={"name": name})
        if not isinstance(created, dict) or not created.get("id"):
            raise ApiError(500, "server was not created")
        dest_id = str(created["id"])
        self.note("server created")
        wired = await self._wire_copy(http, dest_id, plan)
        options = self.store.options()
        rows = [
            {
                "channel_id": item["source_id"],
                "guild_id": guild_id,
                "guild_name": name,
                "channel_name": item["name"],
                "webhook_url": item["webhook_url"],
                "enabled": True,
            }
            for item in wired
        ]
        self.store.set_options(options["backfill"], options["include_threads"], "", True)
        self.store.clear_destination()
        self.store.set_dest_guild(dest_id)
        self.store.replace_selection(rows)
        self.note(f"webhooks on {len(rows)} channel(s)")
        if self.running:
            await self.refresh()
        else:
            await self.start()
        return {
            "server": name,
            "destination_id": dest_id,
            "copied": len(rows),
            "skipped": plan["skipped"],
            "other": plan["other"],
        }

    async def _wire_copy(self, http: DiscordHTTP, dest_id: str, plan: dict[str, Any]) -> list[dict[str, str]]:
        parents: dict[str, str] = {}
        made: set[str] = set()
        for category in plan["categories"]:
            body = {"name": str(category.get("name") or "category")[:100], "type": 4}
            try:
                created = await http.call("POST", f"/guilds/{dest_id}/channels", json=body)
            except ApiError as exc:
                self.note(f"category {body['name']} was not created ({exc})")
                created = None
            if isinstance(created, dict) and created.get("id"):
                parents[str(category.get("id"))] = str(created["id"])
                made.add(str(created["id"]))
            await self._wait(0.3)
        wired: list[dict[str, str]] = []
        for channel in plan["channels"]:
            source_name = str(channel.get("name") or "channel")
            body: dict[str, Any] = {"name": source_name[:100], "type": 0}
            parent = parents.get(str(channel.get("parent_id") or ""))
            if parent:
                body["parent_id"] = parent
            topic = str(channel.get("topic") or "").strip()
            if topic:
                body["topic"] = topic[:1024]
            if channel.get("nsfw"):
                body["nsfw"] = True
            try:
                created = await http.call("POST", f"/guilds/{dest_id}/channels", json=body)
            except ApiError as exc:
                self.note(f"#{source_name} was not created ({exc})")
                continue
            if not isinstance(created, dict) or not created.get("id"):
                self.note(f"#{source_name} was not created")
                continue
            dest_channel = str(created["id"])
            made.add(dest_channel)
            await self._wait(0.25)
            try:
                hook = await http.call(
                    "POST",
                    f"/channels/{dest_channel}/webhooks",
                    json={"name": webhook_name(source_name)},
                )
            except ApiError as exc:
                self.note(f"webhook for #{source_name} failed ({exc})")
                continue
            if not isinstance(hook, dict) or not hook.get("id") or not hook.get("token"):
                self.note(f"webhook for #{source_name} failed")
                continue
            try:
                url = clean_webhook(f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}")
            except ApiError as exc:
                self.note(f"webhook for #{source_name} failed ({exc})")
                continue
            wired.append({"source_id": str(channel.get("id")), "name": source_name[:80], "webhook_url": url})
            self.note(f"#{source_name}")
            await self._wait(0.25)
        if not wired:
            raise ApiError(400, "no webhook could be created")
        try:
            leftovers = await http.channels(dest_id)
        except ApiError:
            leftovers = []
        for channel in leftovers:
            channel_id = str(channel.get("id") or "")
            if channel_id and channel_id not in made:
                try:
                    await http.call("DELETE", f"/channels/{channel_id}")
                    await self._wait(0.2)
                except ApiError:
                    self.note(f"left default #{channel.get('name') or channel_id}")
        return wired

    def save_setup(self, body: dict[str, Any]) -> None:
        try:
            backfill = int(body.get("backfill") or 0)
        except (TypeError, ValueError):
            backfill = 0
        backfill = max(0, min(backfill, 500))
        mirror = bool(body.get("mirror"))
        include_threads = bool(body.get("include_threads"))
        global_webhook = clean_webhook(str(body.get("global_webhook") or ""))
        dest_name = body.get("dest_name")
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
                    "parent": str(row.get("parent") or "")[:80],
                    "topic": str(row.get("topic") or "")[:1024],
                }
            )
        self.store.set_options(backfill, include_threads, global_webhook, mirror, None if dest_name is None else str(dest_name))
        self.store.replace_selection(cleaned)

    async def reset_destination(self) -> None:
        if self.running:
            await self.stop()
        self.store.clear_destination()
        self.note("next start creates a new server")

    async def start(self) -> None:
        async with self._setup:
            await self._start()

    async def _start(self) -> None:
        http = self._require_http()
        rows = [row for row in self.store.selection() if row["enabled"]]
        if not rows:
            raise ApiError(400, "select servers first")
        if any(not str(row.get("webhook_url") or "").strip() for row in rows):
            await self._provision(rows)
            rows = [row for row in self.store.selection() if row["enabled"] and str(row.get("webhook_url") or "").strip()]
        if not rows:
            raise ApiError(400, "the mirror server could not be created")
        options = self.store.options()
        if not options["mirror"]:
            self.store.set_options(options["backfill"], options["include_threads"], "", True, options["dest_name"])
            options = self.store.options()
        if self.running:
            await self.refresh()
            return
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
        stale = self._backfill_task
        self._backfill_task = None
        if stale is not None:
            stale.cancel()
        if options["backfill"] > 0:
            self._backfill_task = asyncio.create_task(self._backfill(rows, options["backfill"]))
        self._emit({"kind": "status", "running": True, "status": self.status})

    async def _provision(self, rows: list[dict[str, Any]]) -> None:
        http = self._require_http()
        missing = [row for row in rows if not str(row.get("webhook_url") or "").strip()]
        options = self.store.options()
        layout = destination_layout(missing, options["dest_name"])
        if not layout["channels"]:
            raise ApiError(400, "select servers first")
        dest_id = options["dest_guild_id"]
        fresh = False
        existing: list[dict[str, Any]] = []
        if not dest_id:
            created = await http.call("POST", "/guilds", json={"name": layout["name"]})
            if not isinstance(created, dict) or not created.get("id"):
                raise ApiError(500, "server was not created")
            dest_id = str(created["id"])
            self.store.set_dest_guild(dest_id)
            fresh = True
            self.note(f"server created: {layout['name']}")
        else:
            try:
                existing = await http.channels(dest_id)
            except ApiError as exc:
                if exc.status in (403, 404):
                    raise ApiError(400, GONE) from exc
                raise
            self.note("adding channels to the mirror server")
        pairs = await self._wire_layout(http, dest_id, layout, fresh, existing)
        self.store.fill_webhooks(pairs)
        self.note(f"webhooks on {len(pairs)} channel(s)")

    async def _wire_layout(
        self,
        http: DiscordHTTP,
        dest_id: str,
        layout: dict[str, Any],
        fresh: bool,
        existing: list[dict[str, Any]] | None = None,
    ) -> list[tuple[str, str]]:
        parents: dict[str, str] = {}
        made: set[str] = set()
        known: dict[str, str] = {}
        for item in existing or []:
            if isinstance(item, dict) and item.get("type") == 4 and item.get("id"):
                known.setdefault(str(item.get("name") or "").casefold(), str(item["id"]))
        for category in layout["categories"]:
            name = str(category["name"])
            found = known.get(name.casefold())
            if found:
                parents[str(category["key"])] = found
                continue
            try:
                created = await http.call(
                    "POST",
                    f"/guilds/{dest_id}/channels",
                    json={"name": name, "type": 4},
                )
            except ApiError as exc:
                self.note(f"category {name} was not created ({exc})")
                await self._wait(0.3)
                continue
            if isinstance(created, dict) and created.get("id"):
                parents[str(category["key"])] = str(created["id"])
                made.add(str(created["id"]))
                known[name.casefold()] = str(created["id"])
            else:
                self.note(f"category {name} was not created")
            await self._wait(0.3)
        pairs: list[tuple[str, str]] = []
        for channel in layout["channels"]:
            body: dict[str, Any] = {"name": channel["name"], "type": 0}
            parent = parents.get(channel["category_key"])
            if parent:
                body["parent_id"] = parent
            if channel.get("topic"):
                body["topic"] = channel["topic"]
            try:
                created = await http.call("POST", f"/guilds/{dest_id}/channels", json=body)
            except ApiError as exc:
                self.note(f"#{channel['name']} was not created ({exc})")
                continue
            if not isinstance(created, dict) or not created.get("id"):
                self.note(f"#{channel['name']} was not created")
                continue
            dest_channel = str(created["id"])
            made.add(dest_channel)
            await self._wait(0.25)
            try:
                hook = await http.call(
                    "POST",
                    f"/channels/{dest_channel}/webhooks",
                    json={"name": webhook_name(channel["name"])},
                )
            except ApiError as exc:
                self.note(f"webhook for #{channel['name']} failed ({exc})")
                continue
            if not isinstance(hook, dict) or not hook.get("id") or not hook.get("token"):
                self.note(f"webhook for #{channel['name']} failed")
                continue
            try:
                url = clean_webhook(f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}")
            except ApiError as exc:
                self.note(f"webhook for #{channel['name']} failed ({exc})")
                continue
            pairs.append((channel["source_id"], url))
            self.note(f"#{channel['name']}")
            await self._wait(0.25)
        if fresh:
            try:
                leftovers = await http.channels(dest_id)
            except ApiError:
                leftovers = []
            for channel in leftovers:
                channel_id = str(channel.get("id") or "")
                if channel_id and channel_id not in made:
                    try:
                        await http.call("DELETE", f"/channels/{channel_id}")
                        await self._wait(0.2)
                    except ApiError:
                        self.note(f"left default #{channel.get('name') or channel_id}")
        if not pairs:
            raise ApiError(400, "no webhook could be created")
        return pairs

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
        missing = [row for row in rows if not str(row.get("webhook_url") or "").strip()]
        if missing:
            self.note(f"{len(missing)} channel(s) need start/resume")

    async def stop(self) -> None:
        self.running = False
        self.backfilling = False
        self.holding.clear()
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
            if self._backfill_task is asyncio.current_task():
                try:
                    await self._drain()
                finally:
                    if self._backfill_task is asyncio.current_task():
                        self._backfill_task = None

    async def _drain(self) -> None:
        while True:
            async with self.lock:
                if not self.running:
                    self.holding.clear()
                    self.backfilling = False
                    break
                if not self.holding:
                    self.backfilling = False
                    break
                batch = self.holding[:50]
                del self.holding[:50]
            for event, data in batch:
                if not self.running:
                    break
                try:
                    await self._handle(event, data)
                except Exception:
                    log.exception("held %s failed", event)
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
            await self._restore_view(message, channel_id, message_id)
            return
        if "content" in message:
            view["content"] = str(message.get("content") or "")
        if "embeds" in message:
            view["embeds"] = safe_embeds(message)
        if message.get("edited_timestamp"):
            view["edited"] = message.get("edited_timestamp")
        self._emit({"kind": "message", "message": view})
        row = self.store.relay_row(message_id)
        if row and self.store.options()["mirror"] and self.relay is not None:
            await self.relay.edit(row["webhook_url"], row["webhook_message_id"], view, self._prefix(row["webhook_url"]))

    async def _restore_view(self, message: dict[str, Any], channel_id: str, message_id: str) -> None:
        full = "content" in message and bool(message.get("author"))
        if not message_id or not full:
            return
        row = self.store.relay_row(message_id)
        if row is None:
            if _worth_showing(message):
                await self._create(message)
            return
        if self._own_webhook(message):
            return
        guild_name, channel_name = self.names.get(channel_id, ("", channel_id))
        view = view_from_message(message, channel_name, guild_name)
        self._push(view)
        if self.store.options()["mirror"] and self.relay is not None:
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
