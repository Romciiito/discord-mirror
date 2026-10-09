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
        self.seq = 1000

    async def call(self, method: str, path: str, **kw: Any) -> Any:
        body = kw.get("json") or {}
        self.calls.append((method, path, body))
        if method == "POST" and path == "/guilds":
            return {"id": "900"}
        if method == "POST" and path.endswith("/webhooks"):
            self.seq += 1
            return {"id": str(self.seq), "token": "tok"}
        if method == "POST" and path.endswith("/channels"):
            if body.get("name") in self.fail:
                raise ApiError(400, "no")
            self.seq += 1
            return {"id": str(self.seq)}
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
