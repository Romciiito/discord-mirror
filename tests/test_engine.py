from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import aiohttp

from mirror import access, engine as engine_mod
from mirror.discord_api import ApiError
from mirror.engine import Engine
from mirror.store import Store

HOOK = "https://discord.com/api/webhooks/111/aaa"
HOOK_B = "https://discord.com/api/webhooks/222/bbb"
TOKEN = "fake-token_for.tests_only-0123456789.abcdefghij_klmnop"

OLD_SCHEMA = """
CREATE TABLE account (id INTEGER PRIMARY KEY CHECK (id = 1), token TEXT, keep INTEGER NOT NULL DEFAULT 0);
CREATE TABLE options (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    backfill INTEGER NOT NULL DEFAULT 0,
    include_threads INTEGER NOT NULL DEFAULT 0,
    global_webhook TEXT NOT NULL DEFAULT '',
    mirror INTEGER NOT NULL DEFAULT 0,
    dest_name TEXT NOT NULL DEFAULT 'mirror',
    dest_guild_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE selection (
    channel_id TEXT PRIMARY KEY,
    guild_id TEXT NOT NULL,
    guild_name TEXT NOT NULL DEFAULT '',
    channel_name TEXT NOT NULL DEFAULT '',
    webhook_url TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    parent TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT ''
);
CREATE TABLE relayed (
    source_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    webhook_url TEXT NOT NULL,
    webhook_message_id TEXT NOT NULL
);
"""


def row(channel_id: str, webhook_url: str = "", name: str = "general", guild_id: str = "5") -> dict[str, Any]:
    return {
        "channel_id": channel_id,
        "guild_id": guild_id,
        "guild_name": "Desk",
        "channel_name": name,
        "webhook_url": webhook_url,
        "enabled": True,
        "parent": "",
        "topic": "",
    }


class FakeHTTP:
    def __init__(self, existing: list[dict] | None = None, gone: ApiError | None = None, fail: tuple = ()) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.existing = existing or []
        self.gone = gone
        self.fail = set(fail)
        self.sources: dict[str, list[dict]] = {}
        self.listed: list[dict] = []
        self.hooks: dict[str, list[dict]] = {}
        self.seq = 1000

    async def call(self, method: str, path: str, **kw: Any) -> Any:
        body = kw.get("json") or {}
        self.calls.append((method, path, body))
        if method == "GET" and path.endswith("/webhooks"):
            return list(self.hooks.get(path.split("/")[2], []))
        if method == "POST" and path.endswith("/webhooks"):
            self.seq += 1
            return {"id": str(self.seq), "token": "tok"}
        if method == "POST" and path.endswith("/channels"):
            if body.get("name") in self.fail:
                raise ApiError(400, "no")
            self.seq += 1
            return {"id": str(self.seq), **body}
        if method == "GET" and path.endswith("/member"):
            return {"roles": []}
        return None

    async def channels(self, guild_id: str) -> list[dict]:
        if self.gone is not None:
            raise self.gone
        if guild_id in self.sources:
            return self.sources[guild_id]
        return list(self.existing)

    async def guilds(self) -> list[dict]:
        return self.listed

    async def active_threads(self, guild_id: str) -> list[dict]:
        return []


class KeepingHTTP(FakeHTTP):
    """A FakeHTTP that keeps the channels and webhooks a fill creates, and lets other tasks run at every request
    as a real request does."""

    async def call(self, method: str, path: str, **kw: Any) -> Any:
        await asyncio.sleep(0)
        made = await super().call(method, path, **kw)
        where = path.split("/")[2]
        if method == "POST" and path.endswith("/channels") and isinstance(made, dict):
            self.sources.setdefault(where, []).append({"parent_id": None, **made})
        elif method == "POST" and path.endswith("/webhooks") and isinstance(made, dict):
            self.hooks.setdefault(where, []).append({"name": (kw.get("json") or {}).get("name"), **made})
        return made

    async def channels(self, guild_id: str) -> list[dict]:
        await asyncio.sleep(0)
        return [dict(item) for item in await super().channels(guild_id)]


class FakeResp:
    def __init__(self, body: str, status: int = 200) -> None:
        self.status = status
        self.body = body

    async def text(self) -> str:
        return self.body

    async def json(self, content_type: Any = None) -> Any:
        return json.loads(self.body)

    async def __aenter__(self) -> FakeResp:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class FakeDiscord:
    def __init__(self, answer: FakeResp | BaseException | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.answer = answer or FakeResp('{"id": "7", "username": "ada"}')

    def get(self, url: str, **kw: Any) -> FakeResp:
        return FakeResp('{"versions": [{"version": "131.0.0.0"}]}')

    def request(self, method: str, url: str, **kw: Any) -> FakeResp:
        self.sent.append((url, kw["headers"]["Authorization"]))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer

    async def close(self) -> None:
        pass


class FakeGateway:
    made: list["FakeGateway"] = []

    def __init__(self, http: Any, on_dispatch: Any, on_fatal: Any = None) -> None:
        self.subs: list[dict[str, list[str]]] = []
        self.resubscribed = 0
        FakeGateway.made.append(self)

    def set_subscriptions(self, guild_channels: dict[str, list[str]], threads: bool) -> None:
        self.subs.append({key: list(value) for key, value in guild_channels.items()})

    def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def resubscribe(self) -> None:
        self.resubscribed += 1


class FakeRelay:
    def __init__(self) -> None:
        self.edits: list[tuple[str, str, dict, bool]] = []
        self.creates: list[dict] = []

    async def create(self, webhook_url: str, view: dict, prefix: bool) -> str | None:
        self.creates.append(view)
        return "1"

    async def edit(self, webhook_url: str, webhook_message_id: str, view: dict, prefix: bool) -> None:
        self.edits.append((webhook_url, webhook_message_id, dict(view), prefix))

    async def remove(self, webhook_url: str, webhook_message_id: str) -> None:
        pass


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.engine = Engine(self.store)
        self.delays: list[float] = []

        async def fast(seconds: float) -> None:
            self.delays.append(seconds)

        self.engine._wait = fast
        FakeGateway.made = []

    async def asyncTearDown(self) -> None:
        await self.engine.close()
        self.store.close()
        self.tmp.cleanup()

    def notes(self) -> list[str]:
        return list(reversed(self.engine.lines))

    def test_store_hooks_survive_untick_and_retick(self) -> None:
        self.store.replace_selection([row("10"), row("11", name="lobby")])
        self.store.fill_webhooks([("10", HOOK), ("11", HOOK_B)])
        self.store.replace_selection([row("11", name="lobby")])
        self.assertEqual([item["channel_id"] for item in self.store.selection()], ["11"])
        self.store.replace_selection([row("10"), row("11", name="lobby")])
        hooks = {item["channel_id"]: item["webhook_url"] for item in self.store.selection()}
        self.assertEqual(hooks, {"10": HOOK, "11": HOOK_B})
        self.assertEqual(self.store.hooks(), {"10": HOOK, "11": HOOK_B})

    def test_store_selection_webhook_updates_hooks(self) -> None:
        self.store.replace_selection([row("10", HOOK)])
        self.store.replace_selection([])
        self.store.replace_selection([row("10")])
        self.assertEqual(self.store.selection()[0]["webhook_url"], HOOK)

    def test_store_migrates_old_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "state.db")
            conn.executescript(OLD_SCHEMA)
            conn.execute("INSERT INTO options (id) VALUES (1)")
            conn.execute(
                "INSERT INTO selection (channel_id, guild_id, webhook_url) VALUES ('10', '5', ?)",
                (HOOK,),
            )
            conn.execute("INSERT INTO selection (channel_id, guild_id, webhook_url) VALUES ('11', '5', '')")
            conn.commit()
            conn.close()
            store = Store(tmp)
            self.assertEqual(store.hooks(), {"10": HOOK})
            store.close()
            again = Store(tmp)
            self.assertEqual(again.hooks(), {"10": HOOK})
            again.replace_selection([])
            again.replace_selection([row("10")])
            self.assertEqual(again.selection()[0]["webhook_url"], HOOK)
            again.close()

    async def test_close_closes_store(self) -> None:
        await self.engine.open()
        await self.engine.close()
        self.assertIsNone(self.engine.session)
        with self.assertRaises(sqlite3.ProgrammingError):
            self.store.conn.execute("SELECT 1")
        await self.engine.close()

    async def test_close_settles_only_on_windows(self) -> None:
        await self.engine.open()
        with mock.patch.object(engine_mod, "WINDOWS", True):
            await self.engine.close()
        self.assertEqual(self.delays, [0.25])
        with tempfile.TemporaryDirectory() as tmp:
            other = Engine(Store(tmp))
            waits: list[float] = []

            async def fast(seconds: float) -> None:
                waits.append(seconds)

            other._wait = fast
            await other.open()
            with mock.patch.object(engine_mod, "WINDOWS", False):
                await other.close()
            self.assertEqual(waits, [])
            with self.assertRaises(sqlite3.ProgrammingError):
                other.store.conn.execute("SELECT 1")

    async def test_restore_keeps_token_on_network_error(self) -> None:
        token = "x" * 50
        self.store.set_token(token, True)
        calls: list[str] = []

        async def use_token(value: str, keep: bool) -> dict:
            calls.append(value)
            if len(calls) == 1:
                raise aiohttp.ClientConnectionError()
            return {"id": "1"}

        self.engine.use_token = use_token
        await self.engine.restore()
        task = self.engine._restore_task
        self.assertIsNotNone(task)
        self.assertEqual(self.store.token(), token)
        await task
        self.assertEqual(self.store.token(), token)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.delays, [5.0])
        notes = self.notes()
        failed = [n for n in notes if "retrying" in n]
        self.assertEqual(len(failed), 1)
        self.assertGreater(notes.index("saved token accepted"), notes.index(failed[0]))
        self.assertIsNone(self.engine._restore_task)

    async def test_restore_keeps_token_on_server_error_and_backs_off(self) -> None:
        token = "x" * 50
        self.store.set_token(token, True)
        calls: list[str] = []

        async def use_token(value: str, keep: bool) -> dict:
            calls.append(value)
            if len(calls) < 5:
                raise ApiError(502 if len(calls) % 2 else 429, "busy")
            return {"id": "1"}

        self.engine.use_token = use_token
        await self.engine.restore()
        await self.engine._restore_task
        self.assertEqual(self.store.token(), token)
        self.assertEqual(self.delays, [5.0, 10.0, 20.0, 40.0])
        self.assertIn("saved token accepted", self.notes())

    async def test_restore_forgets_on_401(self) -> None:
        self.store.set_token("x" * 50, True)

        async def use_token(value: str, keep: bool) -> dict:
            raise ApiError(401, "x")

        self.engine.use_token = use_token
        await self.engine.restore()
        self.assertEqual(self.store.token(), "")
        self.assertIsNone(self.engine._restore_task)
        self.assertIn("saved token was rejected", self.notes())

    async def test_restore_retry_stops_on_403(self) -> None:
        self.store.set_token("x" * 50, True)
        calls: list[str] = []

        async def use_token(value: str, keep: bool) -> dict:
            calls.append(value)
            if len(calls) == 1:
                raise OSError("down")
            raise ApiError(403, "nope")

        self.engine.use_token = use_token
        await self.engine.restore()
        await self.engine._restore_task
        self.assertEqual(self.store.token(), "")
        self.assertEqual(len(calls), 2)
        self.assertIn("saved token was rejected", self.notes())

    async def test_restore_retry_stops_on_manual_token(self) -> None:
        self.store.set_token("x" * 50, True)
        calls: list[str] = []

        async def use_token(value: str, keep: bool) -> dict:
            calls.append(value)
            raise aiohttp.ClientConnectionError()

        async def signed_in(seconds: float) -> None:
            self.engine.http = object()

        self.engine.use_token = use_token
        self.engine._wait = signed_in
        await self.engine.restore()
        await self.engine._restore_task
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.token(), "x" * 50)
        self.engine.http = None

    async def test_close_cancels_restore_retry(self) -> None:
        self.store.set_token("x" * 50, True)
        gate = asyncio.Event()

        async def use_token(value: str, keep: bool) -> dict:
            raise aiohttp.ClientConnectionError()

        async def slow(seconds: float) -> None:
            await gate.wait()

        self.engine.use_token = use_token
        self.engine._wait = slow
        await self.engine.restore()
        task = self.engine._restore_task
        await asyncio.sleep(0)
        await self.engine.forget()
        self.assertTrue(task.cancelled())
        self.assertIsNone(self.engine._restore_task)

    async def test_pasted_token_is_cleaned_before_check_and_store(self) -> None:
        discord = FakeDiscord()
        self.engine.session = discord
        await self.engine.use_token(' "' + TOKEN + '"\n', True)
        self.assertEqual(discord.sent, [("https://discord.com/api/v9/users/@me", TOKEN)])
        self.assertEqual(self.store.token(), TOKEN)

    async def test_checked_token_reports_the_user_and_keeps_nothing(self) -> None:
        discord = FakeDiscord()
        self.engine.session = discord
        report = await self.engine.check_token(' "' + TOKEN + '"\n')
        self.assertEqual(report, {"result": "works", "user": {"id": "7", "username": "ada", "global_name": ""}})
        self.assertEqual(discord.sent, [("https://discord.com/api/v9/users/@me", TOKEN)])
        self.assertEqual(self.store.token(), "")
        self.assertIsNone(self.engine.snapshot()["user"])
        self.assertFalse(self.engine.snapshot()["has_token"])

    async def test_checked_token_too_short_is_refused_before_discord(self) -> None:
        discord = FakeDiscord()
        self.engine.session = discord
        for raw in ("  ", '"MTIz.Gx_y-Z"'):
            with self.subTest(raw=raw):
                with self.assertRaises(ApiError) as caught:
                    await self.engine.check_token(raw)
                self.assertEqual(caught.exception.status, 400)
        self.assertEqual(discord.sent, [])

    async def test_checked_token_refused_by_discord_is_rejected(self) -> None:
        for answer in (
            FakeResp('{"message": "401: Unauthorized"}', 401),
            FakeResp('{"message": "Forbidden"}', 403),
            FakeResp("{}"),
        ):
            with self.subTest(status=answer.status, body=answer.body):
                self.engine.session = FakeDiscord(answer)
                self.assertEqual(await self.engine.check_token(TOKEN), {"result": "rejected"})

    async def test_checked_token_on_discord_5xx_is_unreachable(self) -> None:
        self.engine.session = FakeDiscord(FakeResp("bad gateway", 502))
        report = await self.engine.check_token(TOKEN)
        self.assertEqual(report, {"result": "unreachable", "reason": "GET /users/@me failed"})

    async def test_checked_token_on_network_error_is_unreachable(self) -> None:
        for error, reason in (
            (aiohttp.ClientConnectionError("connection refused"), "connection refused"),
            (asyncio.TimeoutError(), "network error"),
        ):
            with self.subTest(error=type(error).__name__):
                self.engine.session = FakeDiscord(error)
                self.assertEqual(await self.engine.check_token(TOKEN), {"result": "unreachable", "reason": reason})

    async def test_update_after_restart_edits_relay(self) -> None:
        self.store.set_options(0, False, True)
        self.store.replace_selection([row("8", HOOK)])
        self.store.remember_relay("9", "8", HOOK, "77")
        self.engine.names = {"8": ("Desk", "general")}
        relay = FakeRelay()
        self.engine.relay = relay
        message = {
            "id": "9",
            "channel_id": "8",
            "author": {"id": "1", "username": "amy"},
            "content": "new",
            "edited_timestamp": "2026-01-01T00:00:00+00:00",
        }
        await self.engine._update(message)
        self.assertEqual(len(relay.edits), 1)
        url, mid, view, prefix = relay.edits[0]
        self.assertEqual((url, mid), (HOOK, "77"))
        self.assertEqual(view["content"], "new")
        self.assertEqual(view["edited"], "2026-01-01T00:00:00+00:00")
        self.assertIn("9", self.engine.feed)
        self.assertEqual(relay.creates, [])

    async def test_partial_update_without_author_is_ignored(self) -> None:
        self.store.set_options(0, False, True)
        self.store.replace_selection([row("8", HOOK)])
        self.store.remember_relay("9", "8", HOOK, "77")
        self.engine.names = {"8": ("Desk", "general")}
        relay = FakeRelay()
        self.engine.relay = relay
        await self.engine._update({"id": "9", "channel_id": "8", "embeds": [{"title": "x"}]})
        await self.engine._update({"id": "9", "channel_id": "8", "content": "only"})
        self.assertEqual(relay.edits, [])
        self.assertEqual(self.engine.feed, {})
        await self.engine._update({"id": "10", "channel_id": "8", "embeds": [{"title": "x"}]})
        self.assertEqual(relay.creates, [])
        self.assertEqual(self.engine.feed, {})

    async def test_full_update_without_relay_row_creates(self) -> None:
        self.store.set_options(0, False, True)
        self.store.replace_selection([row("8", HOOK)])
        self.engine.names = {"8": ("Desk", "general")}
        relay = FakeRelay()
        self.engine.relay = relay
        await self.engine._update(
            {"id": "12", "channel_id": "8", "author": {"id": "1", "username": "amy"}, "content": "hi"}
        )
        self.assertIn("12", self.engine.feed)
        self.assertEqual(len(relay.creates), 1)

    def test_views_carry_the_guild_id(self) -> None:
        self.engine._index([row("10", HOOK)], False)
        view = engine_mod.view_from_message({"id": "1", "channel_id": "10", "author": {"username": "a"}}, "general", "Desk", self.engine.guild_of.get("10", ""))
        self.assertEqual(view["guild_id"], "5")
        view = engine_mod.view_from_message({"id": "1", "channel_id": "10", "guild_id": "77", "author": {"username": "a"}}, "general", "Desk", "5")
        self.assertEqual(view["guild_id"], "77")

    async def test_relayed_views_carry_the_guild_id_of_their_row(self) -> None:
        self.store.set_options(0, True, True)
        self.store.replace_selection([row("10", HOOK)])
        self.engine._index(self.store.selection(), True)

        class Threads:
            async def active_threads(self, guild_id: str) -> list[dict]:
                return [{"id": "30", "parent_id": "10", "name": "old"}] if guild_id == "5" else []

        await self.engine._load_threads(Threads(), self.store.selection())
        await self.engine._handle("THREAD_CREATE", {"id": "31", "parent_id": "10", "name": "new"})
        relay = FakeRelay()
        self.engine.relay = relay
        for message_id, channel_id in (("1", "10"), ("2", "30"), ("3", "31")):
            await self.engine._create(
                {"id": message_id, "channel_id": channel_id, "author": {"id": "1", "username": "amy"}, "content": "hi"}
            )
        self.assertEqual([view["guild_id"] for view in relay.creates], ["5", "5", "5"])
        self.store.remember_relay("4", "10", HOOK, "77")
        await self.engine._update({"id": "4", "channel_id": "10", "author": {"id": "1", "username": "amy"}, "content": "new"})
        self.assertEqual(len(relay.edits), 1)
        self.assertEqual(relay.edits[0][2]["guild_id"], "5")

    async def test_drain_keeps_order(self) -> None:
        handled: list[tuple[str, str]] = []
        m1 = {"id": "1", "channel_id": "8"}
        m1b = {"id": "1", "channel_id": "8", "content": "b"}
        m2 = {"id": "2", "channel_id": "8"}

        async def record(event: str, data: dict) -> None:
            handled.append((event, data["id"]))
            if len(handled) == 1:
                await self.engine.on_dispatch("MESSAGE_CREATE", m2)
                self.assertEqual(len(handled), 1)

        self.engine._handle = record
        self.engine.running = True
        self.engine.backfilling = True
        self.engine.holding = [("MESSAGE_CREATE", m1), ("MESSAGE_UPDATE", m1b)]
        await self.engine._drain()
        self.assertEqual(handled, [("MESSAGE_CREATE", "1"), ("MESSAGE_UPDATE", "1"), ("MESSAGE_CREATE", "2")])
        self.assertFalse(self.engine.backfilling)
        self.assertEqual(self.engine.holding, [])
        self.assertIn("history done", self.notes())
        self.engine.running = False

    async def test_drain_survives_bad_event(self) -> None:
        handled: list[str] = []

        async def record(event: str, data: dict) -> None:
            if data["id"] == "1":
                raise ValueError("bad")
            handled.append(data["id"])

        self.engine._handle = record
        self.engine.running = True
        self.engine.backfilling = True
        self.engine.holding = [("MESSAGE_CREATE", {"id": "1"}), ("MESSAGE_CREATE", {"id": "2"})]
        with self.assertLogs("mirror.engine", level="ERROR"):
            await self.engine._drain()
        self.assertEqual(handled, ["2"])
        self.engine.running = False

    async def test_drain_when_stopped_clears(self) -> None:
        handled: list[str] = []

        async def record(event: str, data: dict) -> None:
            handled.append(data["id"])

        self.engine._handle = record
        self.engine.running = False
        self.engine.backfilling = True
        self.engine.holding = [("MESSAGE_CREATE", {"id": "1"})]
        await self.engine._drain()
        self.assertEqual(handled, [])
        self.assertEqual(self.engine.holding, [])
        self.assertFalse(self.engine.backfilling)

    async def test_start_refuses_the_first_ticked_channel_without_a_webhook(self) -> None:
        http = FakeHTTP()
        self.engine.http = http
        self.store.replace_selection([row("11", name="lobby"), row("10", HOOK, name="general")])
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            with self.assertRaises(ApiError) as caught:
                await self.engine.start()
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(str(caught.exception), "#lobby has no webhook")
        self.assertFalse(self.engine.running)
        self.assertEqual(FakeGateway.made, [])
        self.assertFalse(any(method == "POST" for method, path, body in http.calls))
        self.assertFalse(self.store.options()["mirror"])

    async def test_start_while_running_refreshes_instead_of_starting_again(self) -> None:
        self.engine.http = FakeHTTP()
        self.store.replace_selection([row("10", HOOK)])
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            await self.engine.start()
            self.assertTrue(self.engine.running)
            self.store.replace_selection([row("10", HOOK), row("11", HOOK_B, name="lobby")])
            await self.engine.start()
            gateway = FakeGateway.made[0]
            self.assertEqual(len(FakeGateway.made), 1)
            self.assertEqual(sorted(gateway.subs[-1]["5"]), ["10", "11"])
            self.assertEqual(gateway.resubscribed, 1)
            await self.engine.stop()

    async def test_stop_during_a_running_refresh_is_respected(self) -> None:
        http = FakeHTTP()
        gate = asyncio.Event()
        entered = asyncio.Event()

        async def held(guild_id: str) -> list[dict]:
            entered.set()
            await gate.wait()
            return []

        self.engine.http = http
        self.store.replace_selection([row("10", HOOK)])
        self.store.set_options(0, True, True)
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            await self.engine.start()
            http.active_threads = held
            self.store.replace_selection([row("10", HOOK), row("11", HOOK_B, name="lobby")])
            task = asyncio.create_task(self.engine.start())
            await asyncio.wait_for(entered.wait(), 1)
            await self.engine.stop()
            gate.set()
            await asyncio.wait_for(task, 1)
            self.assertFalse(self.engine.running)
            self.assertEqual(len(FakeGateway.made), 1)
            self.assertEqual(FakeGateway.made[0].resubscribed, 0)

    async def test_refresh_notes_a_ticked_channel_without_a_webhook(self) -> None:
        self.engine.http = FakeHTTP()
        self.store.replace_selection([row("10", HOOK)])
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            await self.engine.start()
            self.store.replace_selection([row("10", HOOK), row("11", name="lobby")])
            await self.engine.refresh()
            self.assertIn("#lobby has no webhook, not mirrored", self.notes())
            self.assertEqual(sorted(FakeGateway.made[0].subs[-1]["5"]), ["10", "11"])
            await self.engine.stop()

    def posts(self, http: FakeHTTP, suffix: str) -> list[dict]:
        return [body for method, path, body in http.calls if method == "POST" and path.endswith(suffix)]

    async def test_owned_guilds_lists_only_servers_the_account_owns(self) -> None:
        http = FakeHTTP()
        http.listed = [
            {"id": "5", "name": "Desk", "owner": False},
            {"id": "900", "name": "zeta copy", "owner": True},
            {"id": "901", "name": "Alpha", "owner": True},
        ]
        self.engine.http = http
        self.assertEqual(await self.engine.owned_guilds(), [{"id": "901", "name": "Alpha"}, {"id": "900", "name": "zeta copy"}])
        self.engine.http = None
        with self.assertRaises(ApiError):
            await self.engine.owned_guilds()

    async def test_fill_creates_categories_channels_and_webhooks_in_an_owned_server(self) -> None:
        http = FakeHTTP()
        http.listed = [{"id": "5", "name": "Desk", "owner": False}, {"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("11", name="news"), row("20", name="other", guild_id="6")])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 0})
        categories = [b for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 4]
        self.assertEqual([c["name"] for c in categories], ["Talk"])
        texts = [b for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 0]
        self.assertEqual([(t["name"], "parent_id" in t) for t in texts], [("general", True), ("news", False)])
        self.assertEqual(len(self.posts(http, "/webhooks")), 2)
        self.assertFalse(any(method == "DELETE" for method, path, body in http.calls))
        self.assertFalse(any(method == "GET" and path.endswith("/webhooks") for method, path, body in http.calls))
        hooks = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertTrue(hooks["10"].startswith("https://discord.com/api/webhooks/"))
        self.assertTrue(hooks["11"].startswith("https://discord.com/api/webhooks/"))
        self.assertEqual(hooks["20"], "")
        self.assertEqual(self.store.targets(), {"5": {"target_id": "900", "target_name": "Desk copy", "shared": False}})
        self.assertIn("webhooks on 2 channel(s) in Desk copy", self.notes())
        self.assertEqual(self.delays, [0.3, 0.25, 0.25, 0.25, 0.25])

    async def test_fill_reuses_channels_and_their_webhooks(self) -> None:
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "talk"},
            {"id": "t1", "type": 0, "name": "General", "parent_id": "c1"},
            {"id": "t2", "type": 0, "name": "news", "parent_id": None},
            {"id": "t3", "type": 0, "name": "general", "parent_id": None},
        ]
        http.hooks["t1"] = [{"id": "77", "name": "general", "token": "old"}, {"id": "78", "name": "someone else", "token": "x"}]
        http.hooks["t2"] = [{"id": "79", "name": "news"}]
        self.engine.http = http
        # the source channel "general" sits under "Talk": the existing "General" under "talk" is reused (same name as
        # Discord shows it, same parent), not the loose "general"; its webhook named "general" has a token and is kept
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("11", name="news")])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 1})
        self.assertEqual(self.posts(http, "/guilds/900/channels"), [])
        self.assertEqual([path for method, path, body in http.calls if method == "POST"], ["/channels/t2/webhooks"])
        hooks = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual(hooks["10"], "https://discord.com/api/webhooks/77/old")
        self.assertTrue(hooks["11"].startswith("https://discord.com/api/webhooks/1001/"))
        self.assertIn("webhooks on 2 channel(s) in Desk copy, 1 reused", self.notes())

    async def test_two_fills_at_once_create_each_channel_and_webhook_once(self) -> None:
        # a second fill started before the first one ends (the web page, a second client) waits for it and then
        # finds its category, channels and webhooks again (Review Focus 1); Mando never deletes the duplicates
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("11", name="news")])
        reports = await asyncio.gather(self.engine.fill_copy("5", "900"), self.engine.fill_copy("5", "900"))
        self.assertEqual([report["reused"] for report in reports], [0, 2])
        self.assertEqual([(b["type"], b["name"]) for b in self.posts(http, "/guilds/900/channels")], [(4, "Talk"), (0, "general"), (0, "news")])
        self.assertEqual(len(self.posts(http, "/webhooks")), 2)

    async def test_first_source_reuses_channels_of_the_same_name_in_other_categories(self) -> None:
        # decision 9i: a channel with the source channel's name is reused. A fresh server has "general" under
        # "Text Channels"; the first source in a target, with no other source there to mix with (9o), reuses it for
        # a loose "general", and the loose "rules" for "rules" under "Info", which is then not created empty
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "Text Channels"},
            {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"},
            {"id": "c2", "type": 4, "name": "Voice Channels"},
            {"id": "v1", "type": 2, "name": "General", "parent_id": "c2"},
            {"id": "t2", "type": 0, "name": "rules", "parent_id": None},
        ]
        self.engine.http = http
        self.store.replace_selection([row("10", name="general"), row("11", name="rules") | {"parent": "Info"}, row("12", name="news") | {"parent": "Chat"}])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 3, "reused": 0})
        self.assertEqual([(b["type"], b["name"]) for b in self.posts(http, "/guilds/900/channels")], [(4, "Chat"), (0, "news")])
        hooked = [path for method, path, body in http.calls if method == "POST" and path.endswith("/webhooks")]
        self.assertIn("/channels/t1/webhooks", hooked)
        self.assertIn("/channels/t2/webhooks", hooked)
        self.assertNotIn("/channels/v1/webhooks", hooked)

    async def test_fill_gives_two_channels_of_the_same_name_their_own_copies(self) -> None:
        # Discord allows two "general" in one category: each gets its own channel and webhook in the copy, never one
        # channel with one webhook for both (Review Focus 6), and a refill finds each of them again
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("11", name="general") | {"parent": "Talk"}])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 0})
        self.assertEqual([(b["type"], b["name"]) for b in self.posts(http, "/guilds/900/channels")], [(4, "Talk"), (0, "general"), (0, "general")])
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertNotEqual(urls["10"], urls["11"])
        http.calls.clear()
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 2})
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, urls)

    async def test_a_second_source_whose_category_fails_takes_no_loose_channel(self) -> None:
        # Desk (5) holds a loose "general" with its webhook in 900. Other (6) comes second and Discord refuses its
        # "Other / Talk": its "general" is skipped, never put loose beside Desk's and never given Desk's webhook (9o)
        http = FakeHTTP(fail=("Other / Talk",))
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [{"id": "t1", "type": 0, "name": "general", "parent_id": None}]
        http.hooks["t1"] = [{"id": "77", "name": "general", "token": "old"}]
        self.engine.http = http
        self.store.set_target("5", "900", "Desk copy")
        self.store.replace_selection(
            [
                row("10", "https://discord.com/api/webhooks/77/old", name="general"),
                row("20", name="general", guild_id="6") | {"guild_name": "Other", "parent": "Talk"},
                row("21", name="news", guild_id="6") | {"guild_name": "Other"},
            ]
        )
        report = await self.engine.fill_copy("6", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 0})
        self.assertIn("#general was not created (its category failed)", self.notes())
        self.assertEqual([(b["type"], b["name"]) for b in self.posts(http, "/guilds/900/channels")], [(4, "Other / Talk"), (4, "Other"), (0, "news")])
        self.assertFalse(any(path == "/channels/t1/webhooks" for method, path, body in http.calls))
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual(urls["20"], "")
        self.assertEqual(urls["10"], "https://discord.com/api/webhooks/77/old")

    async def test_a_first_source_finds_its_channel_by_its_webhook_after_a_second_source(self) -> None:
        # Desk (5) was alone in 900 and its loose "general" reused the server's "general" under "Text Channels" (9i).
        # Other (6) came second with its own "general" under "Other". Filling Desk again takes the channel that
        # carries Desk's webhook, wherever Discord lists it, and never Other's channel of the same name (9o)
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c3", "type": 4, "name": "Other"},
            {"id": "t3", "type": 0, "name": "general", "parent_id": "c3"},
            {"id": "c1", "type": 4, "name": "Text Channels"},
            {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"},
        ]
        http.hooks["t3"] = [{"id": "88", "name": "general", "token": "other"}]
        http.hooks["t1"] = [{"id": "77", "name": "general", "token": "old"}]
        self.engine.http = http
        self.store.set_target("5", "900", "Desk copy", False)
        self.store.set_target("6", "900", "Desk copy", True)
        self.store.replace_selection(
            [
                row("10", "https://discord.com/api/webhooks/77/old", name="general"),
                row("20", "https://discord.com/api/webhooks/88/other", name="general", guild_id="6") | {"guild_name": "Other"},
            ]
        )
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 1})
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual(urls, {"10": "https://discord.com/api/webhooks/77/old", "20": "https://discord.com/api/webhooks/88/other"})
        self.assertFalse(any("replaced" in line for line in self.notes()))

    async def test_fill_replaces_an_own_webhook_of_a_ticked_channel_and_notes_it(self) -> None:
        # a fill covers every ticked channel of the source (decision 9n), so a channel with an own webhook goes to the
        # copy from now on; the note keeps the change visible, and the hooks memory follows the row
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", HOOK, name="general"), row("11", name="news")])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 0})
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertNotEqual(urls["10"], HOOK)
        self.assertTrue(urls["10"].startswith("https://discord.com/api/webhooks/"))
        self.assertEqual(self.store.hooks(), urls)
        self.assertIn("1 earlier webhook url(s) replaced", self.notes())

    async def test_fill_covers_an_unlisted_ticked_channel(self) -> None:
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([{"channel_id": "12", "guild_id": "5", "guild_name": "Desk", "channel_name": "gone", "webhook_url": "", "enabled": True}])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report["filled"], 1)
        self.assertEqual([t["name"] for t in self.posts(http, "/guilds/900/channels")], ["gone"])

    async def test_second_source_into_a_target_gets_prefixed_categories(self) -> None:
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [{"id": "c1", "type": 4, "name": "Talk"}, {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"}]
        self.engine.http = http
        self.store.set_target("5", "900", "Desk copy")
        self.store.replace_selection([row("20", name="general", guild_id="6") | {"guild_name": "Other", "parent": "Talk"}, row("21", name="loose", guild_id="6") | {"guild_name": "Other"}])
        report = await self.engine.fill_copy("6", "900")
        self.assertEqual(report["filled"], 2)
        categories = [b["name"] for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 4]
        self.assertEqual(categories, ["Other / Talk", "Other"])
        texts = [b for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 0]
        self.assertEqual([t["name"] for t in texts], ["general", "loose"])
        self.assertTrue(all(t.get("parent_id") for t in texts))
        self.assertEqual(self.store.targets()["6"], {"target_id": "900", "target_name": "Desk copy", "shared": True})
        self.assertEqual(self.store.targets()["5"], {"target_id": "900", "target_name": "Desk copy", "shared": False})

    async def test_refill_of_the_first_source_keeps_its_names_after_a_second_source(self) -> None:
        # Desk (5) was filled first into 900 and keeps "Talk"; Other (6) came second and got "Other / Talk" (decision 9o).
        # Filling Desk again must find its own category, channel and webhook, not build "Desk / Talk" beside them.
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "Talk"},
            {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"},
            {"id": "c2", "type": 4, "name": "Other / Talk"},
            {"id": "t2", "type": 0, "name": "general", "parent_id": "c2"},
        ]
        http.hooks["t1"] = [{"id": "77", "name": "general", "token": "old"}]
        self.engine.http = http
        self.store.set_target("5", "900", "Desk copy", False)
        self.store.set_target("6", "900", "Desk copy", True)
        # the row holds the URL of Desk's first fill: found again, so nothing counts as replaced
        self.store.replace_selection([row("10", "https://discord.com/api/webhooks/77/old", name="general") | {"parent": "Talk"}])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 1})
        self.assertEqual([path for method, path, body in http.calls if method == "POST"], [])
        self.assertEqual([path for method, path, body in http.calls if method == "GET"], ["/channels/t1/webhooks"])
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, {"10": "https://discord.com/api/webhooks/77/old"})
        self.assertFalse(any("replaced" in line for line in self.notes()))
        self.assertEqual(self.store.targets()["5"], {"target_id": "900", "target_name": "Desk copy", "shared": False})
        self.assertEqual(self.store.targets()["6"], {"target_id": "900", "target_name": "Desk copy", "shared": True})

    async def test_a_target_keeps_holding_a_source_that_moved_to_another_target(self) -> None:
        # Desk (5) is filled into 900 and then into 901; nothing is deleted in 900 (decision 9i), so 900 still holds
        # Desk's "Talk", "#general" and its webhook. Other (6) filled into 900 next is the second source there
        # (decision 9o): its own category and webhook, never Desk's. Desk filled into 900 again finds its own channel.
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}, {"id": "901", "name": "Spare", "owner": True}]
        http.sources["900"] = []
        http.sources["901"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("20", name="general", guild_id="6") | {"guild_name": "Other", "parent": "Talk"}])
        await self.engine.fill_copy("5", "900")
        await self.engine.fill_copy("5", "901")
        http.sources["900"] = [{"id": "c1", "type": 4, "name": "Talk"}, {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"}]
        http.hooks["t1"] = [{"id": "77", "name": "general", "token": "old"}]
        http.calls.clear()
        report = await self.engine.fill_copy("6", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 0})
        self.assertEqual([b["name"] for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 4], ["Other / Talk"])
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertNotEqual(urls["20"], "https://discord.com/api/webhooks/77/old")
        self.assertEqual(self.store.targets()["6"], {"target_id": "900", "target_name": "Desk copy", "shared": True})
        http.calls.clear()
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 1})
        self.assertEqual([path for method, path, body in http.calls if method == "POST"], [])
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual(urls["10"], "https://discord.com/api/webhooks/77/old")
        self.assertEqual(self.store.targets()["5"], {"target_id": "900", "target_name": "Desk copy", "shared": False})

    async def test_a_target_holds_a_source_whose_first_fill_failed(self) -> None:
        # Desk's first fill into 900 made the category "Talk" and then failed on its channel, so no link was stored;
        # 900 holds Desk's category all the same, and Other filled into 900 next is the second source there (decision 9o)
        http = FakeHTTP(fail=("general",))
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("20", name="news", guild_id="6") | {"guild_name": "Other", "parent": "Talk"}])
        with self.assertRaises(ApiError):
            await self.engine.fill_copy("5", "900")
        self.assertEqual(self.store.targets(), {})
        # the category is recorded for Desk as soon as Discord made it, before the fill failed
        self.assertEqual(self.store.category_sources("900"), {"1001": "5"})
        http.sources["900"] = [{"id": "c1", "type": 4, "name": "Talk"}]
        http.calls.clear()
        await self.engine.fill_copy("6", "900")
        self.assertEqual([b["name"] for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 4], ["Other / Talk"])
        self.assertTrue(self.store.targets()["6"]["shared"])

    async def test_a_second_source_named_like_a_category_of_the_first_gets_its_own_category(self) -> None:
        # Desk (5) has "general" under "Trading"; the server "Trading" (6) comes second with a loose "general", so its
        # category is named "Trading" too. A category belongs to the source a fill made it for, not to its name: 6 gets
        # its own "Trading", channel and webhook, never Desk's (decision 9o), and each refill finds its own again
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection(
            [row("10", name="general") | {"parent": "Trading"}, row("20", name="general", guild_id="6") | {"guild_name": "Trading"}]
        )
        await self.engine.fill_copy("5", "900")
        http.calls.clear()
        report = await self.engine.fill_copy("6", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 0})
        made = self.posts(http, "/guilds/900/channels")
        self.assertEqual([(b["type"], b["name"]) for b in made], [(4, "Trading"), (0, "general")])
        desk, other = [item["id"] for item in http.sources["900"] if item["type"] == 4]
        self.assertEqual(made[1]["parent_id"], other)
        self.assertEqual(self.store.category_sources("900"), {desk: "5", other: "6"})
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertNotEqual(urls["10"], urls["20"])
        http.calls.clear()
        self.assertEqual((await self.engine.fill_copy("5", "900"))["reused"], 1)
        self.assertEqual((await self.engine.fill_copy("6", "900"))["reused"], 1)
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, urls)

    async def test_sources_of_the_same_name_get_their_own_categories(self) -> None:
        # Discord server names are not unique: three servers named "Gaming" with "general" under "Talk" fill one target.
        # The second and the third both get "Gaming / Talk" (decision 9o), as two categories, each with its own channel
        # and webhook, and a refill of each finds its own again
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection(
            [row(channel, name="general", guild_id=guild) | {"guild_name": "Gaming", "parent": "Talk"} for channel, guild in (("10", "5"), ("20", "6"), ("30", "7"))]
        )
        reports = [await self.engine.fill_copy(guild, "900") for guild in ("5", "6", "7")]
        self.assertEqual([report["reused"] for report in reports], [0, 0, 0])
        made = self.posts(http, "/guilds/900/channels")
        self.assertEqual([b["name"] for b in made if b["type"] == 4], ["Talk", "Gaming / Talk", "Gaming / Talk"])
        self.assertEqual(len({b["parent_id"] for b in made if b["type"] == 0}), 3)
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual(len(set(urls.values())), 3)
        http.calls.clear()
        for guild in ("7", "6", "5"):
            self.assertEqual((await self.engine.fill_copy(guild, "900"))["reused"], 1)
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, urls)

    async def test_a_first_source_never_takes_a_category_made_for_a_later_source(self) -> None:
        # Desk (5) came first with "general" under "Talk"; the server "Trading" (6) came second and got the category
        # "Trading" for its loose "general". The owner then ticks Desk's "general" under Desk's own "Trading": Desk's
        # refill makes its own "Trading", never puts Desk's channel in 6's category or on 6's webhook (decision 9o)
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection(
            [row("10", name="general") | {"parent": "Talk"}, row("20", name="general", guild_id="6") | {"guild_name": "Trading"}]
        )
        await self.engine.fill_copy("5", "900")
        await self.engine.fill_copy("6", "900")
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.store.replace_selection(
            [
                row("10", urls["10"], name="general") | {"parent": "Talk"},
                row("11", name="general") | {"parent": "Trading"},
                row("20", urls["20"], name="general", guild_id="6") | {"guild_name": "Trading"},
            ]
        )
        http.calls.clear()
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 1})
        self.assertEqual([(b["type"], b["name"]) for b in self.posts(http, "/guilds/900/channels")], [(4, "Trading"), (0, "general")])
        after = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual((after["10"], after["20"]), (urls["10"], urls["20"]))
        self.assertNotIn(after["11"], (urls["10"], urls["20"]))

    async def test_a_later_source_never_takes_a_category_no_fill_made(self) -> None:
        # a fresh server has "general" under "Text Channels"; Desk (5), alone in 900, reuses it for its loose "general"
        # (decision 9i). The server "Text Channels" (6) comes second: the server's own category is not 6's, so 6 makes
        # its own "Text Channels" and never takes the "general" that carries Desk's webhook (decision 9o)
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "Text Channels"},
            {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"},
        ]
        self.engine.http = http
        self.store.replace_selection(
            [row("10", name="general"), row("20", name="general", guild_id="6") | {"guild_name": "Text Channels"}]
        )
        await self.engine.fill_copy("5", "900")
        http.calls.clear()
        report = await self.engine.fill_copy("6", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 0})
        made = self.posts(http, "/guilds/900/channels")
        self.assertEqual([(b["type"], b["name"]) for b in made], [(4, "Text Channels"), (0, "general")])
        self.assertNotEqual(made[1]["parent_id"], "c1")
        self.assertFalse(any(path == "/channels/t1/webhooks" for method, path, body in http.calls))
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertNotEqual(urls["10"], urls["20"])
        http.calls.clear()
        self.assertEqual((await self.engine.fill_copy("6", "900"))["reused"], 1)
        self.assertEqual((await self.engine.fill_copy("5", "900"))["reused"], 1)
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, urls)

    async def test_fill_refuses_bad_servers(self) -> None:
        # 7 is a server the account is in but does not own (decision 9b'), 8 is not listed at all: neither is written to
        http = FakeHTTP()
        http.listed = [
            {"id": "5", "name": "Desk", "owner": True},
            {"id": "900", "name": "Desk copy", "owner": True},
            {"id": "7", "name": "Theirs", "owner": False},
        ]
        self.engine.http = http
        self.store.replace_selection([row("10", name="general")])
        for source, target, text in (("x", "900", "unknown server"), ("5", "5", "a server cannot be its own copy"), ("6", "900", "tick the server or some of its channels first"), ("5", "7", "pick a server you own"), ("5", "8", "pick a server you own")):
            with self.assertRaises(ApiError) as caught:
                await self.engine.fill_copy(source, target)
            self.assertEqual(str(caught.exception), text)
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual(self.store.targets(), {})
        self.assertEqual(self.store.selection()[0]["webhook_url"], "")

    async def test_fill_skips_what_discord_refuses_and_fails_when_nothing_is_wired(self) -> None:
        http = FakeHTTP(fail=("Talk", "news"))
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("11", name="news"), row("12", name="lobby")])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report["filled"], 1)
        self.assertIn("category Talk was not created (no)", self.notes())
        self.assertIn("#news was not created (no)", self.notes())
        # a channel whose category Discord refused is skipped, never put loose (decision 9o)
        self.assertIn("#general was not created (its category failed)", self.notes())
        self.assertEqual([b["name"] for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 0], ["lobby", "news"])
        http = FakeHTTP(fail=("general",))
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general")])
        with self.assertRaises(ApiError) as caught:
            await self.engine.fill_copy("5", "900")
        self.assertEqual(str(caught.exception), "no webhook could be created")

    async def test_fill_while_running_refreshes(self) -> None:
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            self.store.replace_selection([row("10", HOOK)])
            await self.engine.start()
            self.store.replace_selection([row("10", HOOK), row("11", name="lobby")])
            await self.engine.fill_copy("5", "900")
            self.assertEqual(FakeGateway.made[0].resubscribed, 1)
            self.assertTrue(self.engine._webhook_for("11").startswith("https://discord.com/api/webhooks/"))
            await self.engine.stop()

    def test_save_setup_keeps_only_the_three_options(self) -> None:
        self.engine.save_setup({"backfill": 999, "include_threads": True, "mirror": True, "global_webhook": HOOK, "dest_name": "x", "channels": [row("10", HOOK)]})
        self.assertEqual(self.store.options(), {"backfill": 500, "include_threads": True, "mirror": True})
        self.assertEqual(self.store.selection()[0]["webhook_url"], HOOK)

    def test_prefix_only_when_two_channels_share_a_webhook(self) -> None:
        self.store.replace_selection([row("10", HOOK), row("11", HOOK_B, name="lobby"), row("12", "", name="quiet")])
        self.assertFalse(self.engine._prefix(HOOK))
        self.assertFalse(self.engine._prefix(""))
        self.store.replace_selection([row("10", HOOK), row("11", HOOK, name="lobby")])
        self.assertTrue(self.engine._prefix(HOOK))
        self.assertFalse(hasattr(self.engine, "copy_guild"))
        self.assertFalse(hasattr(self.engine, "reset_destination"))
        self.assertFalse(hasattr(self.engine, "_provision"))

    def test_text_types_consistent(self) -> None:
        self.assertIs(engine_mod.TEXT_TYPES, access.TEXT_TYPES)
        self.assertNotIn(15, engine_mod.TEXT_TYPES)

    async def test_mirrored_counter_follows_created_messages(self) -> None:
        relay = FakeRelay()
        self.engine.relay = relay
        self.engine.store.replace_selection([row("c1", "https://discord.com/api/webhooks/1/t")])
        self.engine.store.set_options(0, False, True)
        self.assertEqual(self.engine.snapshot()["mirrored"], 0)
        await self.engine._relay_create({"id": "m1", "channel_id": "c1", "content": "hi"})
        await self.engine._relay_create({"id": "m1", "channel_id": "c1", "content": "hi"})
        await self.engine._relay_create({"id": "m2", "channel_id": "c1", "content": "yo"})
        self.assertEqual(self.engine.snapshot()["mirrored"], 2)
        self.assertEqual(len(relay.creates), 2)

    async def test_failed_webhook_post_reaches_the_status_line(self) -> None:
        # relay.create returns None when Discord refuses the post or never answers (relay.py logs the
        # status to the log file); the engine notes it and emits an "error" event (story 5)
        from mirror.cli.controller import Controller
        from mirror.cli.render import status_line

        class RefusingRelay(FakeRelay):
            async def create(self, webhook_url: str, view: dict, prefix: bool) -> str | None:
                self.creates.append(view)
                return None

        self.engine.relay = RefusingRelay()
        self.engine.store.replace_selection([row("c1", "https://discord.com/api/webhooks/1/t")])
        self.engine.store.set_options(0, False, True)
        queue: asyncio.Queue = asyncio.Queue()
        self.engine.listeners.add(queue)
        await self.engine._relay_create({"id": "m1", "channel_id": "c1", "channel_name": "general", "content": "hi"})
        events = [queue.get_nowait() for _ in range(queue.qsize())]
        self.assertEqual(
            events,
            [
                {"kind": "log", "text": "#general: webhook post failed"},
                {"kind": "error", "text": "#general: webhook post failed"},
            ],
        )
        self.assertEqual(self.engine.snapshot()["mirrored"], 0)
        self.assertIsNone(self.engine.store.relay_row("m1"))
        ui = Controller(self.engine)
        ui.refresh()
        for event in events:
            ui.on_event(event)
        self.assertEqual(status_line(ui), "signed out · stopped · 0 mirrored · #general: webhook post failed")

    async def test_status_events_carry_the_mirrored_count(self) -> None:
        # every status emit (start, READY, stop, the gateway's fatal error) carries the counter,
        # which the controller copies into its snapshot for the status line
        self.engine.http = FakeHTTP()
        self.engine.relay = FakeRelay()
        self.store.replace_selection([row("10", HOOK)])
        self.store.set_options(0, False, True)
        queue: asyncio.Queue = asyncio.Queue()
        self.engine.listeners.add(queue)
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            await self.engine.start()
            await self.engine._relay_create({"id": "m1", "channel_id": "10", "content": "hi"})
            await self.engine.on_dispatch("READY", {})
            await self.engine._fatal("gateway closed (4004)")
            await self.engine.stop()
        events = [queue.get_nowait() for _ in range(queue.qsize())]
        status = [(item["status"], item.get("mirrored")) for item in events if item["kind"] == "status"]
        self.assertEqual(status, [("connecting", 0), ("live", 1), ("error", 1), ("stopped", 1)])
        self.engine.http = None

    async def test_each_mirrored_message_moves_the_status_line_count(self) -> None:
        # a live run reports no status between READY and Stop, so each created mirrored message emits
        # the count on its own "mirrored" event; a failed post keeps its error next to the count
        from mirror.cli.controller import Controller
        from mirror.cli.render import status_line

        class PickyRelay(FakeRelay):
            async def create(self, webhook_url: str, view: dict, prefix: bool) -> str | None:
                self.creates.append(view)
                return None if view["id"] == "m1" else "1"

        self.engine.http = FakeHTTP()
        self.engine.relay = PickyRelay()
        self.store.replace_selection([row("10", HOOK)])
        self.store.set_options(0, False, True)
        ui = Controller(self.engine)
        ui.refresh()
        queue: asyncio.Queue = asyncio.Queue()
        self.engine.listeners.add(queue)
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            await self.engine.start()
            await self.engine.on_dispatch("READY", {})
            for at in range(3):
                message = {"id": f"m{at}", "channel_id": "10", "author": {"id": "1", "username": "ann"}, "content": "hi"}
                await self.engine.on_dispatch("MESSAGE_CREATE", message)
            events = [queue.get_nowait() for _ in range(queue.qsize())]
            for event in events:
                ui.on_event(event)
            self.assertEqual(
                [item for item in events if item["kind"] == "mirrored"],
                [{"kind": "mirrored", "mirrored": 1}, {"kind": "mirrored", "mirrored": 2}],
            )
            self.assertEqual(self.engine.snapshot()["mirrored"], 2)
            self.assertEqual(status_line(ui), "signed out · running · 2 mirrored · #general: webhook post failed")
            await self.engine.stop()
        self.engine.http = None


if __name__ == "__main__":
    unittest.main()
