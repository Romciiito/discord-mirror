from __future__ import annotations

import asyncio
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
from mirror.provision import destination_layout
from mirror.store import Store

HOOK = "https://discord.com/api/webhooks/111/aaa"
HOOK_B = "https://discord.com/api/webhooks/222/bbb"

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
        self.store.clear_destination()
        self.assertEqual({item["webhook_url"] for item in self.store.selection()}, {""})
        self.assertEqual(self.store.hooks(), {})
        self.store.replace_selection([row("10")])
        self.assertEqual(self.store.selection()[0]["webhook_url"], "")

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

    async def test_update_after_restart_edits_relay(self) -> None:
        self.store.set_options(0, False, "", True)
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
        self.store.set_options(0, False, "", True)
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
        self.store.set_options(0, False, "", True)
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

    async def test_wire_layout_reuses_category_and_survives_failure(self) -> None:
        http = FakeHTTP(existing=[{"id": "c1", "type": 4, "name": "desk"}], fail=("Other",))
        layout = destination_layout(
            [row("10", name="discord-updates"), row("11", name="lobby", guild_id="6") | {"guild_name": "Other"}],
            "mirror",
        )
        pairs = await self.engine._wire_layout(http, "500", layout, False, http.existing)
        posts = [(path, body) for method, path, body in http.calls if method == "POST"]
        names = [body.get("name") for path, body in posts if path == "/guilds/500/channels"]
        self.assertNotIn("Desk", names)
        self.assertIn("Other", names)
        created = {body["name"]: body for path, body in posts if path == "/guilds/500/channels" and body.get("type") == 0}
        self.assertEqual(created["discord-updates"]["parent_id"], "c1")
        self.assertNotIn("parent_id", created["lobby"])
        self.assertEqual([source for source, url in pairs], ["10", "11"])
        self.assertTrue(any("category Other was not created" in note for note in self.notes()))
        hooks = [body["name"] for path, body in posts if path.endswith("/webhooks")]
        self.assertEqual(len(hooks), 2)
        for name in hooks:
            self.assertNotIn("discord", name.casefold())
            self.assertNotIn("clyde", name.casefold())
        self.assertFalse(any(method == "DELETE" for method, path, body in http.calls))

    async def test_provision_reports_gone_destination(self) -> None:
        self.store.set_dest_guild("1")
        self.engine.http = FakeHTTP(gone=ApiError(404, "Unknown Guild"))
        with self.assertRaises(ApiError) as caught:
            await self.engine._provision([row("10")])
        self.assertIn("new server on next start", str(caught.exception))
        self.engine.http = FakeHTTP(gone=ApiError(500, "boom"))
        with self.assertRaises(ApiError) as caught:
            await self.engine._provision([row("10")])
        self.assertEqual(caught.exception.status, 500)
        self.engine.http = None

    async def test_provision_into_existing_server_reuses_categories(self) -> None:
        self.store.set_dest_guild("500")
        http = FakeHTTP(existing=[{"id": "c1", "type": 4, "name": "Desk"}])
        self.engine.http = http
        self.store.replace_selection([row("10")])
        await self.engine._provision(self.store.selection())
        self.assertFalse(any(body.get("type") == 4 for method, path, body in http.calls))
        self.assertTrue(self.store.selection()[0]["webhook_url"].startswith("https://discord.com/api/webhooks/"))
        self.engine.http = None

    async def test_start_while_running_provisions_and_refreshes(self) -> None:
        http = FakeHTTP()
        self.engine.http = http
        self.store.set_dest_guild("500")
        self.store.replace_selection([row("10", HOOK)])
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            await self.engine.start()
            self.assertTrue(self.engine.running)
            self.assertEqual(len(FakeGateway.made), 1)
            self.store.replace_selection([row("10"), row("11", name="lobby")])
            await self.engine.start()
            gateway = FakeGateway.made[0]
            self.assertEqual(len(FakeGateway.made), 1)
            self.assertTrue(self.engine.running)
            hooks = {item["channel_id"]: item["webhook_url"] for item in self.store.selection()}
            self.assertEqual(hooks["10"], HOOK)
            self.assertTrue(hooks["11"].startswith("https://discord.com/api/webhooks/"))
            self.assertEqual(sorted(gateway.subs[-1]["5"]), ["10", "11"])
            self.assertEqual(gateway.resubscribed, 1)
            self.assertNotIn("need start/resume", " ".join(self.notes()))
            await self.engine.stop()

    async def test_stop_during_running_start_is_respected(self) -> None:
        self.engine.http = FakeHTTP()
        self.store.set_dest_guild("500")
        self.store.replace_selection([row("10", HOOK)])
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            await self.engine.start()
            self.assertTrue(self.engine.running)
            self.store.replace_selection([row("10"), row("11", name="lobby")])
            gate = asyncio.Event()
            entered = asyncio.Event()

            async def held(seconds: float) -> None:
                entered.set()
                await gate.wait()

            self.engine._wait = held
            task = asyncio.create_task(self.engine.start())
            await asyncio.wait_for(entered.wait(), 1)
            await self.engine.stop()
            self.assertFalse(self.engine.running)
            gate.set()
            await asyncio.wait_for(task, 1)
            self.assertFalse(self.engine.running)
            self.assertEqual(len(FakeGateway.made), 1)

    async def test_refresh_notes_missing_webhooks(self) -> None:
        self.engine.http = FakeHTTP()
        self.store.replace_selection([row("10", HOOK)])
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            await self.engine.start()
            self.store.replace_selection([row("10"), row("11", name="lobby")])
            await self.engine.refresh()
            self.assertTrue(any("1 channel(s) need start/resume" in note for note in self.notes()))
            self.assertEqual(sorted(FakeGateway.made[0].subs[-1]["5"]), ["10", "11"])
            await self.engine.stop()

    async def test_copy_guild_keeps_new_destination(self) -> None:
        http = FakeHTTP()
        http.listed = [{"id": "5", "name": "Src", "owner": True, "permissions": "0"}]
        http.sources["5"] = [{"id": "10", "type": 0, "name": "discord-news", "position": 0}]
        self.engine.http = http
        self.store.fill_webhooks([("99", HOOK_B)])
        self.store.replace_selection([row("99", HOOK_B)])
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            report = await self.engine.copy_guild("5")
            self.assertEqual(report["destination_id"], "900")
            self.assertEqual(self.store.options()["dest_guild_id"], "900")
            self.assertNotIn("99", self.store.hooks())
            hooks = [body["name"] for method, path, body in http.calls if path.endswith("/webhooks")]
            self.assertEqual(hooks, ["dis.cord-news"])
            await self.engine.stop()

    def test_text_types_consistent(self) -> None:
        self.assertIs(engine_mod.TEXT_TYPES, access.TEXT_TYPES)
        self.assertNotIn(15, engine_mod.TEXT_TYPES)


if __name__ == "__main__":
    unittest.main()
