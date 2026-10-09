from __future__ import annotations

import asyncio
import unittest
from typing import Any

import aiohttp

from mirror.cli.controller import Controller
from mirror.discord_api import ApiError


class FakeEngine:
    def __init__(self) -> None:
        self.user: dict | None = None
        self.running = False
        self.status = "idle"
        self.http = None
        self.options = {"backfill": 0, "include_threads": False, "dest_name": "mirror", "mirror": False, "global_webhook": ""}
        self.selection: list[dict] = []
        self.log: list[str] = []
        self.feed: list[dict] = []
        self.listeners: set[asyncio.Queue] = set()
        self.calls: list[tuple] = []
        self.fail: dict[str, Exception] = {}
        self.mirrored = 0
        self.checks: dict[str, dict] = {}
        self.guild_list: list[dict] = []
        self.channel_lists: dict[str, list[dict]] = {}
        self.keychain: dict[tuple[str, str], str] = {}

    def snapshot(self) -> dict[str, Any]:
        return {
            "user": self.user, "running": self.running, "status": self.status,
            "has_token": self.http is not None, "options": dict(self.options),
            "selection": list(self.selection), "log": list(self.log), "mirrored": self.mirrored,
        }

    def feed_items(self) -> list[dict]:
        return list(self.feed)

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise self.fail[name]

    async def start(self) -> None:
        self.calls.append(("start",))
        self._maybe_fail("start")
        self.running, self.status = True, "connecting"

    async def stop(self) -> None:
        self.calls.append(("stop",))
        self._maybe_fail("stop")
        self.running, self.status = False, "stopped"

    async def use_token(self, token: str, keep: bool) -> dict:
        self.calls.append(("use_token", token, keep))
        self._maybe_fail("use_token")
        self.user = {"id": "1", "username": "sosa", "global_name": "Sosa"}
        self.http = object()
        return self.user

    async def check_token(self, raw: str) -> dict:
        self.calls.append(("check_token", raw))
        self._maybe_fail("check_token")
        return self.checks.get(raw, {"result": "rejected"})

    def note(self, text: str) -> None:
        self.log.insert(0, text)

    async def guilds(self) -> list[dict]:
        self.calls.append(("guilds",))
        self._maybe_fail("guilds")
        return list(self.guild_list)

    async def channels(self, guild_id: str) -> list[dict]:
        self.calls.append(("channels", guild_id))
        self._maybe_fail("channels")
        return list(self.channel_lists.get(guild_id, []))

    def save_setup(self, body: dict) -> None:
        self.calls.append(("save_setup", body))
        self._maybe_fail("save_setup")
        self.options["backfill"] = int(body.get("backfill") or 0)
        self.options["include_threads"] = bool(body.get("include_threads"))
        self.options["dest_name"] = str(body.get("dest_name") or "mirror")
        self.selection = [dict(row) for row in body.get("channels") or []]

    async def reset_destination(self) -> None:
        self.calls.append(("reset_destination",))
        self._maybe_fail("reset_destination")


async def read_keychain_fake(service: str, account: str) -> str:
    return {("svc", "acc"): "k" * 40}.get((service, account), "")


def make() -> tuple[Controller, FakeEngine]:
    engine = FakeEngine()
    ui = Controller(engine, read_keychain=read_keychain_fake)
    ui.refresh()
    return ui, engine


async def keys(ui: Controller, *names: str) -> None:
    for name in names:
        await ui.press(name, plain=len(name) == 1)


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_starts_on_welcome_and_any_key_opens_the_menu(self) -> None:
        ui, _ = make()
        self.assertEqual(ui.flow["screen"], "welcome")
        self.assertEqual(ui.hint(), "press any key")
        await keys(ui, "x")
        self.assertEqual(ui.flow["screen"], "menu")
        self.assertEqual(ui.hint(), "up and down move, enter or a number opens, esc back")

    async def test_locked_items_set_the_error_and_do_not_open(self) -> None:
        ui, _ = make()
        await keys(ui, "Enter", "2", "2")
        self.assertEqual(ui.flow["screen"], "settings")
        self.assertEqual(ui.flow["index"], 1)
        self.assertEqual(ui.error, "add a working token first")
        await keys(ui, "4")
        self.assertEqual(ui.flow["screen"], "settings")
        self.assertEqual(ui.flow["index"], 3)
        self.assertEqual(ui.error, "the browser UI is not available yet")
        await keys(ui, "ArrowDown")
        self.assertEqual(ui.error, "")

    async def test_start_opens_running_and_calls_the_engine(self) -> None:
        ui, engine = make()
        await keys(ui, "Enter", "1")
        self.assertEqual(engine.calls, [("start",)])
        self.assertEqual(ui.flow["screen"], "running")
        self.assertTrue(ui.snap["running"])
        self.assertEqual(ui.hint(), "esc returns to the menu")
        await keys(ui, "Escape")
        self.assertEqual(ui.flow["screen"], "menu")
        self.assertTrue(ui.snap["running"])

    async def test_start_failure_shows_the_error_on_the_menu(self) -> None:
        ui, engine = make()
        engine.fail["start"] = ApiError(400, "select servers first")
        await keys(ui, "Enter", "1")
        self.assertEqual(ui.flow["screen"], "menu")
        self.assertEqual(ui.error, "select servers first")

    async def test_exit_stops_and_any_key_returns_to_the_menu(self) -> None:
        ui, engine = make()
        await keys(ui, "Enter", "3")
        self.assertEqual(engine.calls, [("stop",)])
        self.assertEqual(ui.flow["screen"], "exit")
        self.assertEqual(ui.hint(), "press any key")
        await keys(ui, "q")
        self.assertEqual(ui.flow["screen"], "menu")

    async def test_keys_are_ignored_while_busy(self) -> None:
        ui, engine = make()
        await keys(ui, "Enter")
        ui.busy = True
        await keys(ui, "1", "3")
        self.assertEqual(engine.calls, [])
        self.assertEqual(ui.flow["screen"], "menu")

    async def test_messages_are_kept_on_any_screen(self) -> None:
        ui, _ = make()
        await keys(ui, "Enter")
        ui.on_event({"kind": "message", "message": {"id": "m1", "channel_name": "general", "author": "ann", "content": "hi"}})
        ui.on_event({"kind": "message", "message": {"id": "m1", "channel_name": "general", "author": "ann", "content": "hi!", "edited": "t"}})
        ui.on_event({"kind": "status", "running": True, "status": "live", "mirrored": 7})
        ui.on_event({"kind": "log", "text": "connected"})
        self.assertEqual([m["content"] for m in ui.messages], ["hi!"])
        self.assertEqual(ui.snap["status"], "live")
        self.assertEqual(ui.snap["mirrored"], 7)
        self.assertEqual(ui.snap["log"][0], "connected")

    async def test_load_reads_snapshot_and_feed(self) -> None:
        ui, engine = make()
        engine.feed = [{"id": "a", "content": "old"}]
        engine.log = ["saved token accepted"]
        await ui.load()
        self.assertEqual([m["id"] for m in ui.messages], ["a"])
        self.assertEqual(ui.snap["log"], ["saved token accepted"])


class NetworkErrorTests(unittest.IsolatedAsyncioTestCase):
    """The HTTP layer lets aiohttp and timeout errors through; the old page showed them as "request failed"."""

    async def test_network_error_on_start_returns_to_the_menu(self) -> None:
        ui, engine = make()
        engine.fail["start"] = aiohttp.ClientConnectionError("Cannot connect to host discord.com:443")
        with self.assertLogs("mirror.cli", level="ERROR"):
            await keys(ui, "Enter", "1")
        self.assertEqual(engine.calls, [("start",)])
        self.assertEqual(ui.flow["screen"], "menu")
        self.assertEqual(ui.error, "request failed")
        self.assertFalse(ui.busy)

    async def test_timeout_on_stop_still_opens_exit(self) -> None:
        ui, engine = make()
        engine.fail["stop"] = asyncio.TimeoutError()
        with self.assertLogs("mirror.cli", level="ERROR"):
            await keys(ui, "Enter", "3")
        self.assertEqual(engine.calls, [("stop",)])
        self.assertEqual(ui.flow["screen"], "exit")
        self.assertEqual(ui.error, "request failed")
        self.assertFalse(ui.busy)


if __name__ == "__main__":
    unittest.main()
