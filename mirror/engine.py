from __future__ import annotations

import asyncio
import logging
import sys
from collections import defaultdict, deque
from typing import Any

import aiohttp

from .access import TEXT_TYPES, readable_plan
from .provision import copy_layout, same_name, webhook_name
from .discord_api import ApiError, DiscordHTTP, clean_token, clean_webhook, load_properties, webhook_parts
from .gateway import Gateway
from .relay import Relay, safe_embeds, view_from_message
from .store import Store

log = logging.getLogger("mirror.engine")
WINDOWS = sys.platform == "win32"


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
        self.mirrored = 0  # mirrored messages created since this engine started (the status line count)
        self.lines: deque[str] = deque(maxlen=60)
        self.feed: dict[str, dict[str, Any]] = {}
        self.order: deque[str] = deque()
        self.listeners: set[asyncio.Queue] = set()
        self.backfilling = False
        self.holding: list[tuple[str, dict[str, Any]]] = []
        self.lock = asyncio.Lock()
        self.names: dict[str, tuple[str, str]] = {}
        self.guild_of: dict[str, str] = {}  # channel or thread id -> its source server id (message links)
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
            if WINDOWS:
                await self._wait(0.25)
        self.store.close()

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
            "mirrored": self.mirrored,
            "targets": self.store.targets(),
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
        token = clean_token(token)
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

    async def check_token(self, raw: str) -> dict[str, Any]:
        token = clean_token(raw)
        if len(token) < 40:
            raise ApiError(400, "token looks too short")
        if self._properties is None:
            self._properties = await load_properties(self.session)
        try:
            me = await DiscordHTTP(self.session, token, self._properties).me()
        except ApiError as exc:
            if exc.status in (401, 403):
                return {"result": "rejected"}
            if exc.status >= 500:
                return {"result": "unreachable", "reason": str(exc)}
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            return {"result": "unreachable", "reason": str(exc) or "network error"}
        user = {
            "id": str(me.get("id") or ""),
            "username": me.get("username") or "",
            "global_name": me.get("global_name") or "",
        }
        return {"result": "works", "user": user}

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

    async def owned_guilds(self) -> list[dict[str, Any]]:
        """The servers the account owns (the `owner` flag of /users/@me/guilds): the only servers a fill writes to
        (decision 9b')."""
        http = self._require_http()
        rows = [
            {"id": str(guild.get("id")), "name": guild.get("name") or "server"}
            for guild in await http.guilds()
            if guild.get("owner")
        ]
        rows.sort(key=lambda item: item["name"].casefold())
        return rows

    async def fill_copy(self, source_id: str, target_id: str) -> dict[str, Any]:
        """Create the ticked channels of one source server, with their categories and one webhook each, inside a
        server the owner owns, and keep the webhook URLs (decisions 9b', 9i, 9n, 9o). Nothing is ever deleted, so
        one fill runs at a time and never beside a Start (`_setup`): a second fill reads the target after the
        first one wrote to it and creates nothing twice."""
        async with self._setup:
            return await self._fill_copy(source_id, target_id)

    async def _fill_copy(self, source_id: str, target_id: str) -> dict[str, Any]:
        if not source_id.isdigit() or not target_id.isdigit():
            raise ApiError(400, "unknown server")
        if source_id == target_id:
            raise ApiError(400, "a server cannot be its own copy")
        http = self._require_http()
        rows = [row for row in self.store.selection() if row["enabled"] and row["guild_id"] == source_id]
        if not rows:
            raise ApiError(400, "tick the server or some of its channels first")
        target = next((guild for guild in await self.owned_guilds() if guild["id"] == target_id), None)
        if target is None:
            raise ApiError(400, "pick a server you own")
        existing = await http.channels(target_id)
        # the store rows hold no age restriction: the source's own listing, read once before any write, says which
        # ticked channel has it, so its copy is created with the age gate
        gated = {
            str(item.get("id"))
            for item in await http.channels(source_id)
            if isinstance(item, dict) and item.get("nsfw")
        }
        rows = [row | {"nsfw": True} if str(row["channel_id"]) in gated else row for row in rows]
        # what the target holds, not where the links point now: a source moved to another target, or whose first
        # fill failed halfway, still has its channels here (decisions 9i, 9o); a refill keeps its first layout
        shared = self.store.record_fill(source_id, target_id)
        # with another source in the target, first or later, a channel of the same name outside this source's
        # category may be that source's channel (decision 9o)
        alone = not self.store.holds_another(target_id, source_id)
        # a category is the source's a fill made it for, not whoever carries its name: server names are not unique,
        # and a later source's "<source>" can be a category of the first (decision 9o). A later source uses only the
        # categories made for it, the first source also those no fill made (the server's own, decision 9i)
        made = self.store.category_sources(target_id)
        existing = [
            item
            for item in existing
            if not isinstance(item, dict)
            or item.get("type") != 4
            or made.get(str(item.get("id") or "")) == source_id
            or (not shared and str(item.get("id") or "") not in made)
        ]
        source_name = rows[0].get("guild_name") or "server"
        layout = copy_layout(source_name, rows, shared)
        self.note(f"filling {target['name']} from {source_name}")
        before = {str(row["channel_id"]): str(row.get("webhook_url") or "") for row in rows}
        pairs, reused = await self._fill(http, target_id, layout, existing, before, alone, source_id)
        # a fill covers every ticked channel (decision 9n), an own webhook or an earlier copy's URL included; the
        # store has no mark of who made a URL, so each one this fill changes is counted and noted, never kept. A URL
        # is compared by its webhook id and token: a found webhook is written with the discord.com host, and the
        # same webhook pasted with a ptb., canary. or discordapp.com host is not replaced
        replaced = sum(
            1 for source, url in pairs if before.get(source) and webhook_parts(before[source]) != webhook_parts(url)
        )
        self.store.fill_webhooks(pairs)
        self.store.set_target(source_id, target_id, target["name"])
        text = f"webhooks on {len(pairs)} channel(s) in {target['name']}"
        if reused:
            text += f", {reused} reused"
        self.note(text)
        if replaced:
            self.note(f"{replaced} earlier webhook url(s) replaced")
        if self.running:
            await self.refresh()
        return {"target": target["name"], "filled": len(pairs), "reused": reused, "replaced": replaced}

    async def _fill(
        self,
        http: DiscordHTTP,
        target_id: str,
        layout: dict[str, Any],
        existing: list[dict[str, Any]],
        before: dict[str, str],
        alone: bool,
        source_id: str,
    ) -> tuple[list[tuple[str, str]], int]:
        """Find or create each channel of the layout in the target and give it a webhook (decisions 9i, 9o). A
        channel is found again by `same_name` under its own category (loose for a loose one; `existing` holds only
        the categories this source may use), of two or more there the one that carries the row's webhook (`before`),
        so two rows of one name never swap copies. One of the same name elsewhere is taken when it carries the row's
        webhook (`before`), or, when none does, if the target holds no other source (`alone`), since it may
        otherwise be another source's channel. An existing channel serves one row only, a category is created only
        for a channel created under it and recorded for `source_id` as soon as Discord made it, and a channel whose
        category Discord refuses is skipped, never put loose."""
        categories: dict[str, str] = {}
        texts: list[dict[str, Any]] = []
        for item in existing:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if item.get("type") == 4:
                categories.setdefault(str(item.get("name") or "").casefold(), str(item["id"]))
            elif item.get("type") in TEXT_TYPES:
                texts.append(item)
        parents = {
            category["key"]: categories[category["name"].casefold()]
            for category in layout["categories"]
            if category["name"].casefold() in categories
        }
        claimed: set[str] = set()
        found: dict[str, str] = {}  # source channel id -> the existing channel it gets
        carried: dict[str, str] = {}  # source channel id -> the row's own webhook, found on that channel
        listed: dict[str, list[dict[str, Any]]] = {}  # existing channel id -> its webhooks, once fetched

        def free(name: str, parent: str | None) -> list[str]:
            return [
                str(item["id"])
                for item in texts
                if str(item["id"]) not in claimed
                and same_name(str(item.get("name") or ""), name)
                and (parent is None or str(item.get("parent_id") or "") == parent)
            ]

        async def carrier(source: str, spots: list[str]) -> str:
            """The spot whose webhooks hold the row's own webhook (`before`), each spot listed once and kept in
            `listed`, with that webhook kept in `carried`; "" when no spot carries it."""
            mine = webhook_parts(before.get(source, ""))
            for spot in spots if mine else []:
                if spot not in listed:
                    listed[spot] = await self._hooks_on(http, spot)
                    await self._wait(0.25)
                own = next(
                    (hook for hook in listed[spot] if str(hook.get("id") or "") == mine[0] and hook.get("token")), None
                )
                if own is None:
                    continue
                try:
                    carried[source] = clean_webhook(f"https://discord.com/api/webhooks/{own['id']}/{own['token']}")
                except ApiError:
                    continue
                return spot
            return ""

        # every channel in its own place first, so a name found elsewhere never takes a channel another row has there;
        # of two or more of the same name there, the one that carries the row's webhook, so two rows never swap copies
        for channel in layout["channels"]:
            key = channel["category_key"]
            if key and key not in parents:
                continue  # its category is not in the target yet, so nothing is under it
            spots = free(channel["name"], parents.get(key, ""))
            if spots:
                pick = (await carrier(channel["source_id"], spots) if len(spots) > 1 else "") or spots[0]
                found[channel["source_id"]] = pick
                claimed.add(pick)
        for channel in layout["channels"]:
            source = channel["source_id"]
            if source in found:
                continue
            spots = free(channel["name"], None)
            pick = await carrier(source, spots)
            if not pick and alone and spots:
                pick = spots[0]
            if pick:
                found[source] = pick
                claimed.add(pick)
        needed = {channel["category_key"] for channel in layout["channels"] if channel["source_id"] not in found}
        for category in layout["categories"]:
            if category["key"] in parents or category["key"] not in needed:
                continue
            try:
                created = await http.call("POST", f"/guilds/{target_id}/channels", json={"name": category["name"], "type": 4})
            except ApiError as exc:
                self.note(f"category {category['name']} was not created ({exc})")
                await self._wait(0.3)
                continue
            if isinstance(created, dict) and created.get("id"):
                parents[category["key"]] = str(created["id"])
                self.store.record_category(target_id, source_id, str(created["id"]))
            else:
                self.note(f"category {category['name']} was not created")
            await self._wait(0.3)
        pairs: list[tuple[str, str]] = []
        reused = 0
        for channel in layout["channels"]:
            source = channel["source_id"]
            dest = found.get(source, "")
            if not dest:
                key = channel["category_key"]
                if key and key not in parents:
                    # never loose: a loose channel of the same name may be another source's (decision 9o)
                    self.note(f"#{channel['name']} was not created (its category failed)")
                    continue
                body: dict[str, Any] = {"name": channel["name"], "type": 0}
                if key:
                    body["parent_id"] = parents[key]
                if channel["topic"]:
                    body["topic"] = channel["topic"]
                if channel.get("nsfw"):
                    body["nsfw"] = True  # on creation only: a channel the fill finds is never altered
                try:
                    created = await http.call("POST", f"/guilds/{target_id}/channels", json=body)
                except ApiError as exc:
                    self.note(f"#{channel['name']} was not created ({exc})")
                    await self._wait(0.25)
                    continue
                if not isinstance(created, dict) or not created.get("id"):
                    self.note(f"#{channel['name']} was not created")
                    await self._wait(0.25)
                    continue
                dest = str(created["id"])
                await self._wait(0.25)
            if source in carried:
                url, kept = carried[source], True
            else:
                url, kept = await self._webhook_on(
                    http, dest, channel["name"], look=source in found, listed=listed.get(dest)
                )
                await self._wait(0.25)
            if url:
                pairs.append((source, url))
                reused += int(kept)
                self.note(f"#{channel['name']}")
        if not pairs:
            raise ApiError(400, "no webhook could be created")
        return pairs, reused

    async def _hooks_on(self, http: DiscordHTTP, channel_id: str) -> list[dict[str, Any]]:
        """The webhooks Discord lists on a channel; none when it refuses."""
        try:
            listed = await http.call("GET", f"/channels/{channel_id}/webhooks")
        except ApiError:
            return []
        return [hook for hook in listed if isinstance(hook, dict)] if isinstance(listed, list) else []

    async def _webhook_on(
        self, http: DiscordHTTP, channel_id: str, name: str, look: bool, listed: list[dict[str, Any]] | None = None
    ) -> tuple[str, bool]:
        """The URL of a webhook on the channel: one this fill made before, when `look` and it is still there
        (same name, token visible; `listed` when the fill already fetched the channel's webhooks), otherwise a new
        one. ("", False) when Discord refuses."""
        wanted = webhook_name(name)
        if look:
            for hook in listed if listed is not None else await self._hooks_on(http, channel_id):
                if hook.get("token") and hook.get("name") == wanted:
                    try:
                        return clean_webhook(f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}"), True
                    except ApiError:
                        continue
        try:
            hook = await http.call("POST", f"/channels/{channel_id}/webhooks", json={"name": wanted})
        except ApiError as exc:
            self.note(f"webhook for #{name} failed ({exc})")
            return "", False
        if not isinstance(hook, dict) or not hook.get("id") or not hook.get("token"):
            self.note(f"webhook for #{name} failed")
            return "", False
        try:
            return clean_webhook(f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}"), False
        except ApiError as exc:
            self.note(f"webhook for #{name} failed ({exc})")
            return "", False

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

    def save_setup(self, body: dict[str, Any]) -> None:
        try:
            backfill = int(body.get("backfill") or 0)
        except (TypeError, ValueError):
            backfill = 0
        backfill = max(0, min(backfill, 500))
        mirror = bool(body.get("mirror"))
        include_threads = bool(body.get("include_threads"))
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
        self.store.set_options(backfill, include_threads, mirror)
        self.store.replace_selection(cleaned)

    async def start(self) -> None:
        async with self._setup:
            await self._start()

    async def _start(self) -> None:
        http = self._require_http()
        rows = [row for row in self.store.selection() if row["enabled"]]
        if not rows:
            raise ApiError(400, "select servers first")
        # the Start rule (decisions 9f, 9k): Mando never creates a webhook at Start; the CLI jumps to this row
        missing = next((row for row in rows if not str(row.get("webhook_url") or "").strip()), None)
        if missing is not None:
            raise ApiError(400, f"#{missing.get('channel_name') or missing['channel_id']} has no webhook")
        if self.running:
            await self.refresh()
            return
        options = self.store.options()
        if not options["mirror"]:
            self.store.set_options(options["backfill"], options["include_threads"], True)
            options = self.store.options()
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
        self._emit({"kind": "status", "running": True, "status": self.status, "mirrored": self.mirrored})

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
        gateway = self.gateway
        if gateway is None or not self.running:
            return  # stopped while the threads were listed
        gateway.set_subscriptions(self._guild_map(rows), options["include_threads"])
        await gateway.resubscribe()
        self.note(f"selection updated, {len(rows)} channel(s)")
        for row in rows:
            if not str(row.get("webhook_url") or "").strip():
                self.note(f"#{row.get('channel_name') or row['channel_id']} has no webhook, not mirrored")

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
        self._emit({"kind": "status", "running": False, "status": self.status, "mirrored": self.mirrored})

    async def _fatal(self, text: str) -> None:
        self.running = False
        self.backfilling = False
        self.status = "error"
        self.note(text)
        self._emit({"kind": "status", "running": False, "status": self.status, "mirrored": self.mirrored})

    async def on_dispatch(self, event: str, data: dict[str, Any]) -> None:
        if event == "READY":
            self.status = "live"
            self.note("live")
            self._emit({"kind": "status", "running": True, "status": self.status, "mirrored": self.mirrored})
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
                self.guild_of[thread_id] = self.guild_of.get(parent, "")
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
        view = view_from_message(message, channel_name, guild_name, self.guild_of.get(channel_id, ""))
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
        view = view_from_message(message, channel_name, guild_name, self.guild_of.get(channel_id, ""))
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
            self.mirrored += 1
            self.store.remember_relay(view["id"], view["channel_id"], url, sent)
            # a separate event: no status is emitted between READY and Stop, and a status event during
            # backfill would repeat "connecting", the status that marks a new run
            self._emit({"kind": "mirrored", "mirrored": self.mirrored})
        else:
            # relay.py logs why (the status, or the attempts it gave up after) to the log file
            text = f"#{view.get('channel_name') or view['channel_id']}: webhook post failed"
            self.note(text)
            self._emit({"kind": "error", "text": text})

    def _webhook_for(self, channel_id: str) -> str:
        parent = self.threads.get(channel_id, channel_id)
        for row in self.store.selection():
            if row["channel_id"] == parent and row["enabled"]:
                return row["webhook_url"]
        return ""

    def _prefix(self, url: str) -> bool:
        users = 0
        for row in self.store.selection():
            if not row["enabled"]:
                continue
            target = row["webhook_url"]
            if target and target == url:
                users += 1
        return users > 1

    def _own_webhook(self, message: dict[str, Any]) -> bool:
        hook = str(message.get("webhook_id") or "")
        if not hook:
            return False
        known = set()
        for row in self.store.selection():
            parts = webhook_parts(row.get("webhook_url") or "")
            if parts:
                known.add(parts[0])
        return hook in known

    def _index(self, rows: list[dict], include_threads: bool) -> None:
        self.include_threads = include_threads
        self.names = {}
        self.guild_of = {}
        self.selected = set()
        self.threads = {}
        for row in rows:
            self.selected.add(row["channel_id"])
            self.names[row["channel_id"]] = (row.get("guild_name") or "", row.get("channel_name") or row["channel_id"])
            self.guild_of[row["channel_id"]] = str(row.get("guild_id") or "")

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
                self.guild_of[thread_id] = self.guild_of.get(parent, "")

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
