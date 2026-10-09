from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from typing import Any

import aiohttp

from mirror.cli.controller import Controller
from mirror.cli.render import render, status_line
from mirror.discord_api import ApiError, clean_token
from mirror.engine import Engine
from mirror.store import Store


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
        return self.checks.get(clean_token(raw), {"result": "rejected"})  # Engine.check_token cleans the raw text

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

    async def refresh(self) -> None:
        self.calls.append(("refresh",))
        self._maybe_fail("refresh")

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

    async def test_a_new_run_clears_the_engine_error_after_start_refreshed(self) -> None:
        # engine events reach the controller through a queue (Engine._emit), so the "connecting" status
        # of a new run arrives after _begin has already refreshed the snapshot to running
        ui, _ = make()
        ui.on_event({"kind": "log", "text": "token was rejected"})
        ui.on_event({"kind": "status", "running": False, "status": "error"})
        self.assertEqual(ui.engine_error, "token was rejected")
        await keys(ui, "Enter", "1")
        self.assertTrue(ui.snap["running"])
        self.assertEqual(ui.engine_error, "token was rejected")
        ui.on_event({"kind": "status", "running": True, "status": "connecting"})
        self.assertEqual(ui.engine_error, "")

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

    async def test_mirrored_event_moves_the_count_and_keeps_the_engine_error(self) -> None:
        # Engine._relay_create emits "mirrored" after each created mirrored message; a status event cannot
        # carry the count mid-run, because a backfill post lands while the status is still "connecting"
        ui, _ = make()
        ui.on_event({"kind": "status", "running": True, "status": "connecting", "mirrored": 0})
        ui.on_event({"kind": "error", "text": "#general: webhook post failed"})
        ui.on_event({"kind": "mirrored", "mirrored": 3})
        self.assertEqual(ui.snap["mirrored"], 3)
        self.assertEqual(ui.snap["status"], "connecting")
        self.assertTrue(ui.snap["running"])
        self.assertEqual(ui.engine_error, "#general: webhook post failed")

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

    async def test_a_failed_stop_shows_the_engine_stopped_on_the_status_line(self) -> None:
        ui, engine = make()
        engine.running = True
        ui.refresh()

        async def stop() -> None:
            engine.calls.append(("stop",))
            engine.running = False  # Engine.stop clears running before it awaits gateway.stop()
            raise RuntimeError("gateway timeout")

        engine.stop = stop
        await keys(ui, "Enter", "3")
        self.assertEqual(ui.flow["screen"], "exit")
        self.assertEqual(status_line(ui), "signed out · stopped · 0 mirrored · gateway timeout")
        self.assertFalse(ui.busy)


class TokenScreenTests(unittest.IsolatedAsyncioTestCase):
    async def open_token(self) -> tuple[Controller, FakeEngine]:
        ui, engine = make()
        await keys(ui, "Enter", "2", "1")
        self.assertEqual(ui.flow["screen"], "token")
        return ui, engine

    async def test_enter_on_the_token_row_opens_the_field_and_enter_checks(self) -> None:
        ui, engine = await self.open_token()
        engine.checks["a" * 40] = {"result": "works", "user": {"id": "1", "username": "sosa", "global_name": "Sosa"}}
        await keys(ui, "1")
        self.assertEqual(ui.typing, "token")
        self.assertEqual(ui.hint(), "enter or esc keeps it")
        for ch in "a" * 40:
            await ui.press(ch, plain=True)
        self.assertEqual(ui.draft, "a" * 40)
        await keys(ui, "Enter")
        self.assertIsNone(ui.typing)
        self.assertEqual(ui.fields["token"], "a" * 40)
        self.assertEqual(engine.calls[-1], ("check_token", "a" * 40))
        self.assertEqual(ui.token_check, "✓ token works – signed in as Sosa")

    async def test_escape_keeps_the_token_and_reports_a_rejection(self) -> None:
        ui, engine = await self.open_token()
        await keys(ui, "1")
        for ch in "b" * 40:
            await ui.press(ch, plain=True)
        await keys(ui, "Escape")
        self.assertEqual(ui.fields["token"], "b" * 40)
        self.assertEqual(ui.token_check, "✗ token rejected by Discord")
        self.assertEqual(ui.flow["screen"], "token")

    async def test_unreachable_and_short_tokens_are_reported(self) -> None:
        ui, engine = await self.open_token()
        engine.checks["c" * 40] = {"result": "unreachable", "reason": "timeout"}
        await keys(ui, "1")
        for ch in "c" * 40:
            await ui.press(ch, plain=True)
        await keys(ui, "Enter")
        self.assertEqual(ui.token_check, "✗ could not reach Discord (timeout)")
        engine.fail["check_token"] = ApiError(400, "token looks too short")
        await keys(ui, "x", "Enter")
        self.assertEqual(ui.fields["token"], "x")
        self.assertEqual(ui.token_check, "✗ token looks too short")

    async def test_reopening_the_token_field_starts_from_the_kept_token(self) -> None:
        ui, _ = await self.open_token()
        await ui.paste("j" * 40)
        await keys(ui, "Enter", "1")
        self.assertEqual(ui.typing, "token")
        self.assertEqual(ui.draft, "j" * 40)
        await keys(ui, "x", "Enter")
        self.assertEqual(ui.fields["token"], "j" * 40 + "x")

    async def test_typing_on_the_closed_token_row_opens_it_with_the_text(self) -> None:
        ui, _ = await self.open_token()
        await ui.press("z", plain=True)
        self.assertEqual(ui.typing, "token")
        self.assertEqual(ui.draft, "z")

    async def test_pasted_token_is_checked_after_cleaning(self) -> None:
        ui, engine = await self.open_token()
        engine.checks["d" * 40] = {"result": "works", "user": {"id": "9", "username": "u", "global_name": ""}}
        await ui.paste('  "' + "d" * 40 + '"\n')
        self.assertEqual(ui.typing, "token")
        await keys(ui, "Enter")
        self.assertEqual(engine.calls[-1], ("check_token", '  "' + "d" * 40 + '"\n'))
        self.assertEqual(ui.token_check, "✓ token works – signed in as u")

    async def test_paste_into_the_open_token_field_is_checked_on_enter(self) -> None:
        ui, engine = await self.open_token()
        engine.checks["p" * 40] = {"result": "works", "user": {"id": "1", "username": "sosa", "global_name": "Sosa"}}
        await keys(ui, "Enter")
        self.assertEqual(ui.typing, "token")
        await ui.paste("p" * 40 + "\n")
        self.assertEqual(ui.draft, "p" * 40 + "\n")
        await keys(ui, "Enter")
        self.assertEqual(ui.fields["token"], "p" * 40 + "\n")
        self.assertEqual(engine.calls[-1], ("check_token", "p" * 40 + "\n"))
        self.assertEqual(ui.token_check, "✓ token works – signed in as Sosa")

    async def test_paste_into_an_open_keychain_field_appends_one_line(self) -> None:
        ui, engine = await self.open_token()
        await keys(ui, "4", "s")
        await ui.paste("vc\r\n")
        self.assertEqual(ui.typing, "service")
        self.assertEqual(ui.draft, "svc")
        await keys(ui, "Enter", "5")
        await ui.paste("acc")
        self.assertEqual(ui.draft, "acc")
        await keys(ui, "Enter", "6")
        self.assertEqual(engine.calls[-1], ("use_token", "k" * 40, True))

    async def test_backspace_deletes_the_last_character_of_the_open_field(self) -> None:
        ui, engine = await self.open_token()
        await keys(ui, "1", "a", "b", "Backspace")
        self.assertEqual(ui.typing, "token")
        self.assertEqual(ui.draft, "a")
        await keys(ui, "Backspace", "Backspace")
        self.assertEqual(ui.draft, "")
        await keys(ui, "Enter", "4", "s", "v", "x", "Backspace", "c", "Enter")
        self.assertEqual(ui.fields["service"], "svc")
        await keys(ui, "Backspace")
        self.assertIsNone(ui.typing)
        self.assertEqual(ui.fields["service"], "svc")
        self.assertEqual(ui.flow["screen"], "token")

    async def test_save_signs_in_and_returns_to_settings(self) -> None:
        ui, engine = await self.open_token()
        await keys(ui, "1")
        for ch in "e" * 40:
            await ui.press(ch, plain=True)
        await keys(ui, "Enter", "2", "3")
        self.assertEqual(engine.calls[-1], ("use_token", "e" * 40, False))
        self.assertEqual(ui.flow["screen"], "settings")
        self.assertEqual(ui.fields["token"], "")
        self.assertEqual(ui.token_check, "")
        self.assertTrue(ui.ctx()["tokenOk"])
        self.assertEqual(engine.log[0], "signed in as Sosa")

    async def test_save_without_a_token_shows_the_error(self) -> None:
        ui, engine = await self.open_token()
        await keys(ui, "3")
        self.assertEqual(ui.error, "paste a token or use the keychain fields")
        self.assertEqual(engine.calls, [])

    async def test_keychain_fields_keep_on_enter_and_cancel_on_escape(self) -> None:
        ui, engine = await self.open_token()
        await keys(ui, "4")
        self.assertEqual(ui.typing, "service")
        self.assertEqual(ui.hint(), "enter keeps it, esc cancels the edit")
        for ch in "svc":
            await ui.press(ch, plain=True)
        await keys(ui, "Enter")
        self.assertEqual(ui.fields["service"], "svc")
        await keys(ui, "5")
        for ch in "nope":
            await ui.press(ch, plain=True)
        await keys(ui, "Escape")
        self.assertEqual(ui.fields["account"], "")
        await keys(ui, "5")
        for ch in "acc":
            await ui.press(ch, plain=True)
        await keys(ui, "Enter", "6")
        self.assertEqual(engine.calls[-1], ("use_token", "k" * 40, True))
        self.assertEqual(ui.flow["screen"], "settings")

    async def test_keychain_miss_shows_the_error(self) -> None:
        ui, engine = await self.open_token()
        await keys(ui, "6")
        self.assertEqual(ui.error, "paste a token or use the keychain fields")
        self.assertEqual(engine.calls, [])

    async def test_moving_to_another_row_commits_the_draft(self) -> None:
        ui, _ = await self.open_token()
        await keys(ui, "1")
        for ch in "f" * 40:
            await ui.press(ch, plain=True)
        await keys(ui, "ArrowDown")
        self.assertEqual(ui.typing, "token")
        self.assertEqual(ui.draft, "f" * 40 + "ArrowDown"[:0])
        await keys(ui, "Enter")
        await keys(ui, "4")
        self.assertEqual(ui.typing, "service")
        self.assertEqual(ui.fields["token"], "f" * 40)

    async def test_escape_on_a_closed_row_returns_to_settings(self) -> None:
        ui, _ = await self.open_token()
        await keys(ui, "Escape")
        self.assertEqual(ui.flow["screen"], "settings")

    async def test_keychain_lookup_failure_shows_the_error(self) -> None:
        engine = FakeEngine()
        seen: list[bool] = []

        async def failing(service: str, account: str) -> str:
            seen.append(ui.busy)
            raise RuntimeError("keychain lookup failed")

        ui = Controller(engine, read_keychain=failing)
        ui.refresh()
        await keys(ui, "Enter", "2", "1", "4", "s", "Enter", "5", "a", "Enter", "6")
        self.assertEqual(seen, [True])
        self.assertEqual(ui.error, "keychain lookup failed")
        self.assertEqual(engine.calls, [])
        self.assertEqual(ui.flow["screen"], "token")
        self.assertFalse(ui.busy)

    async def test_network_error_on_check_is_logged_and_clears_the_old_result(self) -> None:
        ui, engine = await self.open_token()
        engine.checks["g" * 40] = {"result": "works", "user": {"id": "1", "username": "sosa", "global_name": "Sosa"}}
        await ui.paste("g" * 40)
        await keys(ui, "Enter")
        self.assertEqual(ui.token_check, "✓ token works – signed in as Sosa")
        engine.fail["check_token"] = aiohttp.ClientConnectionError("Cannot connect to host discord.com:443")
        with self.assertLogs("mirror.cli", level="ERROR") as logs:
            await keys(ui, "1", "h", "Enter")
        self.assertNotIn("g" * 40, "\n".join(logs.output))
        self.assertEqual(ui.fields["token"], "g" * 40 + "h")
        self.assertEqual(ui.token_check, "")
        self.assertEqual(ui.error, "request failed")
        self.assertFalse(ui.busy)

    async def test_network_error_on_sign_in_stays_on_the_token_screen(self) -> None:
        ui, engine = await self.open_token()
        engine.fail["use_token"] = asyncio.TimeoutError()
        await ui.paste("i" * 40)
        await keys(ui, "Enter")
        with self.assertLogs("mirror.cli", level="ERROR") as logs:
            await keys(ui, "3")
        self.assertNotIn("i" * 40, "\n".join(logs.output))
        self.assertEqual(engine.calls[-1], ("use_token", "i" * 40, True))
        self.assertEqual(ui.flow["screen"], "token")
        self.assertEqual(ui.fields["token"], "i" * 40)
        self.assertEqual(ui.error, "request failed")
        self.assertFalse(ui.busy)


class WebhookScreenTests(unittest.IsolatedAsyncioTestCase):
    async def open_hooks(self) -> tuple[Controller, FakeEngine]:
        ui, engine = make()
        await keys(ui, "Enter", "2", "3")
        self.assertEqual(ui.flow["screen"], "webhooks")
        return ui, engine

    async def test_right_and_left_step_backfill_and_save(self) -> None:
        ui, engine = await self.open_hooks()
        self.assertEqual(ui.hook_index, 0)
        await keys(ui, "ArrowRight", "ArrowRight")
        self.assertEqual(ui.snap["options"]["backfill"], 50)
        self.assertEqual(engine.calls[-1][0], "save_setup")
        self.assertEqual(engine.calls[-1][1]["backfill"], 50)
        self.assertEqual(engine.calls[-1][1]["global_webhook"], "")
        self.assertEqual(engine.calls[-1][1]["dest_name"], "mirror")
        await keys(ui, "ArrowLeft", "ArrowLeft", "ArrowLeft")
        self.assertEqual(ui.snap["options"]["backfill"], 0)

    async def test_enter_on_backfill_wraps_and_threads_toggle(self) -> None:
        ui, engine = await self.open_hooks()
        for _ in range(6):
            await keys(ui, "1")
        self.assertEqual(ui.snap["options"]["backfill"], 0)
        await keys(ui, "2")
        self.assertTrue(ui.snap["options"]["include_threads"])
        await keys(ui, "ArrowLeft")
        self.assertFalse(ui.snap["options"]["include_threads"])

    async def test_digits_wrap_and_back_returns(self) -> None:
        ui, engine = await self.open_hooks()
        await keys(ui, "ArrowUp")
        self.assertEqual(ui.hook_index, 2)
        await keys(ui, "ArrowDown")
        self.assertEqual(ui.hook_index, 0)
        await keys(ui, "4")
        self.assertEqual(ui.flow["screen"], "webhooks")
        await keys(ui, "3")
        self.assertEqual(ui.flow["screen"], "settings")

    async def test_save_failure_shows_the_error(self) -> None:
        ui, engine = await self.open_hooks()
        engine.fail["save_setup"] = ApiError(400, "bad webhook")
        await keys(ui, "2")
        self.assertEqual(ui.error, "bad webhook")

    async def test_a_failed_save_shows_the_stored_options_again(self) -> None:
        ui, engine = await self.open_hooks()
        engine.fail["save_setup"] = ApiError(400, "database is locked")
        await keys(ui, "2")
        self.assertEqual(ui.error, "database is locked")
        self.assertFalse(ui.snap["options"]["include_threads"])
        self.assertIn("> 2  threads  off", render(ui, 80, 24))
        await keys(ui, "1")
        self.assertEqual(ui.snap["options"]["backfill"], 0)
        self.assertFalse(ui.busy)

    async def test_a_failed_save_keeps_its_error_when_the_state_cannot_be_read_either(self) -> None:
        ui, engine = await self.open_hooks()
        engine.fail["save_setup"] = RuntimeError("database is locked")

        def broken() -> dict:
            raise sqlite3.OperationalError("disk I/O error")

        engine.snapshot = broken
        with self.assertLogs("mirror.cli", level="ERROR"):
            await keys(ui, "2")
        self.assertEqual(ui.error, "database is locked")
        self.assertFalse(ui.busy)

    async def test_save_sends_the_picked_rows_and_the_stored_options(self) -> None:
        picked = {"channel_id": "10", "guild_id": "5", "guild_name": "Src", "channel_name": "general",
                  "webhook_url": "", "enabled": True, "parent": "", "topic": ""}
        ui, engine = await self.open_hooks()
        engine.selection = [dict(picked)]
        engine.options["mirror"] = True
        ui.refresh()
        await keys(ui, "2")
        body = engine.calls[-1][1]
        self.assertEqual(body["channels"], [picked])
        self.assertTrue(body["mirror"])
        self.assertTrue(body["include_threads"])
        self.assertEqual(engine.selection, [picked])

    async def test_save_while_running_refreshes_the_running_engine(self) -> None:
        # PUT /api/setup refreshed a running engine after saving (mirror/web.py save_setup), so the
        # threads toggle reaches the live gateway; a stopped engine is only saved
        ui, engine = await self.open_hooks()
        engine.running = True
        await keys(ui, "2")
        self.assertEqual([call[0] for call in engine.calls], ["save_setup", "refresh"])
        self.assertTrue(ui.snap["options"]["include_threads"])
        engine.running = False
        await keys(ui, "ArrowLeft")
        self.assertEqual([call[0] for call in engine.calls], ["save_setup", "refresh", "save_setup"])
        self.assertFalse(ui.snap["options"]["include_threads"])

    async def test_refresh_failure_and_network_errors_show_the_error(self) -> None:
        ui, engine = await self.open_hooks()
        engine.running = True
        engine.fail["refresh"] = ApiError(400, "pick at least one channel")
        await keys(ui, "1")
        self.assertEqual(ui.error, "pick at least one channel")
        self.assertFalse(ui.busy)
        engine.fail["refresh"] = aiohttp.ClientConnectionError("Cannot connect to host discord.com:443")
        with self.assertLogs("mirror.cli", level="ERROR"):
            await keys(ui, "2")
        self.assertEqual(ui.error, "request failed")
        self.assertFalse(ui.busy)
        await keys(ui, "ArrowDown")
        self.assertEqual(ui.error, "request failed")
        await keys(ui, "Enter")
        self.assertEqual(ui.flow["screen"], "settings")
        self.assertEqual(ui.error, "")

    async def test_keys_are_dropped_while_the_running_engine_refreshes(self) -> None:
        ui, engine = await self.open_hooks()
        engine.running = True
        entered, gate = asyncio.Event(), asyncio.Event()

        async def held() -> None:
            engine.calls.append(("refresh",))
            entered.set()
            await gate.wait()

        engine.refresh = held
        task = asyncio.create_task(keys(ui, "2"))
        await asyncio.wait_for(entered.wait(), 1)
        self.assertTrue(ui.busy)
        await keys(ui, "1", "ArrowRight", "Escape")
        gate.set()
        await asyncio.wait_for(task, 1)
        self.assertEqual([call[0] for call in engine.calls], ["save_setup", "refresh"])
        self.assertEqual(ui.flow["screen"], "webhooks")
        self.assertEqual(ui.snap["options"]["backfill"], 0)
        self.assertFalse(ui.busy)


class ServersScreenTests(unittest.IsolatedAsyncioTestCase):
    async def open_servers(self) -> tuple[Controller, FakeEngine]:
        ui, engine = make()
        engine.user = {"id": "1", "username": "sosa", "global_name": "Sosa"}
        engine.http = object()
        engine.guild_list = [{"id": "g1", "name": "Qwen", "icon": ""}, {"id": "g2", "name": "Moody", "icon": ""}]
        engine.channel_lists["g1"] = [
            {"id": "c1", "name": "general", "parent": "text", "topic": ""},
            {"id": "c2", "name": "dev", "parent": "", "topic": "t"},
        ]
        ui.refresh()
        await keys(ui, "Enter", "2", "2")
        self.assertEqual(ui.flow["screen"], "servers")
        self.assertEqual([g["id"] for g in ui.guilds], ["g1", "g2"])
        return ui, engine

    async def test_enter_ticks_a_whole_server_and_again_unticks_it(self) -> None:
        ui, engine = await self.open_servers()
        await keys(ui, "Enter")
        body = engine.calls[-1][1]
        self.assertEqual(sorted(row["channel_id"] for row in body["channels"]), ["c1", "c2"])
        self.assertEqual(body["channels"][0]["guild_name"], "Qwen")
        self.assertEqual(body["channels"][0]["webhook_url"], "")
        self.assertEqual(set(ui.picked), {"c1", "c2"})
        await keys(ui, "Enter")
        self.assertEqual(engine.calls[-1][1]["channels"], [])

    async def test_right_opens_channels_and_a_ticks_all(self) -> None:
        ui, engine = await self.open_servers()
        await keys(ui, "ArrowRight")
        self.assertEqual(ui.depth, "channels")
        self.assertEqual(ui.active_guild["id"], "g1")
        self.assertEqual(ui.hint(), "enter toggles, a selects all, esc back")
        await keys(ui, "ArrowDown")
        self.assertEqual(ui.local_index, 1)
        await keys(ui, "a")
        self.assertEqual(set(ui.picked), {"c1", "c2"})
        self.assertEqual(engine.calls[-1][1]["channels"][0]["webhook_url"], "")
        await keys(ui, "Escape")
        self.assertEqual(ui.depth, "guilds")
        self.assertEqual(ui.local_index, 0)
        await keys(ui, "Escape")
        self.assertEqual(ui.flow["screen"], "settings")

    async def test_unreadable_server_shows_the_error(self) -> None:
        ui, engine = await self.open_servers()
        await keys(ui, "ArrowDown", "Enter")
        self.assertEqual(ui.error, "nothing in that server can be read")
        self.assertEqual(ui.picked, {})

    async def test_guild_list_failure_returns_to_settings(self) -> None:
        ui, engine = make()
        engine.user = {"id": "1", "username": "sosa", "global_name": "Sosa"}
        engine.fail["guilds"] = ApiError(401, "add a token first")
        ui.refresh()
        await keys(ui, "Enter", "2", "2")
        self.assertEqual(ui.flow["screen"], "settings")
        self.assertEqual(ui.flow["index"], 1)
        self.assertEqual(ui.error, "add a token first")

    async def test_channels_stop_at_the_list_ends_and_escape_returns_to_their_server(self) -> None:
        # no Enter on a channel here: Task 6b changes what it does (the own webhook URL row)
        ui, engine = await self.open_servers()
        self.assertEqual(ui.hint(), "enter toggles the server, right opens channels, esc back")
        await keys(ui, "ArrowDown", "ArrowRight")
        self.assertEqual(ui.depth, "channels")
        self.assertEqual(ui.active_guild["id"], "g2")
        self.assertEqual(ui.channel_rows, [])
        await keys(ui, "a", "ArrowDown", "ArrowUp")
        self.assertEqual([call[0] for call in engine.calls], ["guilds", "channels"])
        await keys(ui, "Escape")
        self.assertEqual((ui.depth, ui.local_index), ("guilds", 1))
        await keys(ui, "ArrowUp", "ArrowRight", "ArrowUp")
        self.assertEqual((ui.depth, ui.local_index), ("channels", 0))
        await keys(ui, "ArrowDown", "ArrowDown")
        self.assertEqual(ui.local_index, 1)
        await keys(ui, "Escape")
        self.assertEqual((ui.depth, ui.local_index), ("guilds", 0))
        await keys(ui, "ArrowUp")
        self.assertEqual(ui.local_index, 1)
        await keys(ui, "ArrowDown")
        self.assertEqual(ui.local_index, 0)
        self.assertNotIn("save_setup", [call[0] for call in engine.calls])

    async def test_ticking_keeps_an_own_webhook_already_saved(self) -> None:
        hook = "https://discord.com/api/webhooks/1/abc"
        ui, engine = await self.open_servers()
        engine.selection = [{"channel_id": "c1", "guild_id": "g1", "guild_name": "Qwen", "channel_name": "general",
                             "parent": "text", "topic": "", "webhook_url": hook, "enabled": True}]
        ui.refresh()
        await keys(ui, "ArrowRight", "a")
        rows = {row["channel_id"]: row for row in engine.calls[-1][1]["channels"]}
        self.assertEqual(rows["c1"]["webhook_url"], hook)
        self.assertEqual(rows["c2"]["webhook_url"], "")

    async def test_network_error_on_the_guild_list_returns_to_settings(self) -> None:
        ui, engine = make()
        engine.user = {"id": "1", "username": "sosa", "global_name": "Sosa"}
        engine.fail["guilds"] = asyncio.TimeoutError()
        ui.refresh()
        with self.assertLogs("mirror.cli", level="ERROR"):
            await keys(ui, "Enter", "2", "2")
        self.assertEqual(ui.flow["screen"], "settings")
        self.assertEqual(ui.flow["index"], 1)
        self.assertEqual(ui.error, "request failed")
        self.assertFalse(ui.busy)

    async def test_channel_list_errors_keep_the_server_list(self) -> None:
        ui, engine = await self.open_servers()
        engine.fail["channels"] = ApiError(400, "unknown server")
        await keys(ui, "ArrowRight")
        self.assertEqual((ui.depth, ui.active_guild, ui.error), ("guilds", None, "unknown server"))
        await keys(ui, "Enter")
        self.assertEqual(ui.error, "unknown server")
        engine.fail["channels"] = aiohttp.ClientConnectionError("Cannot connect to host discord.com:443")
        with self.assertLogs("mirror.cli", level="ERROR"):
            await keys(ui, "ArrowRight")
        self.assertEqual((ui.depth, ui.active_guild, ui.error), ("guilds", None, "request failed"))
        with self.assertLogs("mirror.cli", level="ERROR"):
            await keys(ui, "Enter")
        self.assertEqual(ui.error, "request failed")
        self.assertEqual(ui.picked, {})
        self.assertNotIn("save_setup", [call[0] for call in engine.calls])
        self.assertEqual(ui.flow["screen"], "servers")
        self.assertFalse(ui.busy)

    def unlisted(self, ui: Controller, engine: FakeEngine, picked: list[str]) -> None:
        # #gone (13) is stored but the account cannot list it now (deleted, hidden, or a failed member call)
        engine.user = {"id": "1", "username": "sosa", "global_name": "Sosa"}
        engine.http = object()
        engine.guild_list = [{"id": "900", "name": "Qwen", "icon": ""}]
        engine.channel_lists["900"] = [{"id": "12", "name": "dev", "parent": "", "topic": ""}]
        names = {"12": "dev", "13": "gone"}
        engine.selection = [{"channel_id": cid, "guild_id": "900", "guild_name": "Qwen", "channel_name": names[cid],
                             "parent": "", "topic": "", "webhook_url": "", "enabled": True} for cid in picked]
        ui.refresh()

    async def test_enter_on_a_ticked_server_unticks_its_unlisted_channels_too(self) -> None:
        ui, engine = make()
        self.unlisted(ui, engine, ["12", "13"])
        await keys(ui, "Enter", "2", "2", "Enter")
        self.assertEqual(ui.picked, {})
        self.assertEqual(engine.calls[-1][0], "save_setup")
        self.assertEqual([row for row in engine.calls[-1][1]["channels"] if row["guild_id"] == "900"], [])

    async def test_a_server_whose_only_tick_is_unlisted_shows_ticked_and_enter_unticks_it(self) -> None:
        ui, engine = make()
        self.unlisted(ui, engine, ["13"])
        await keys(ui, "Enter", "2", "2")
        self.assertIn("> [x] Qwen", render(ui, 80, 24))
        await keys(ui, "Enter")
        self.assertEqual(ui.picked, {})
        self.assertEqual(engine.calls[-1][0], "save_setup")
        self.assertEqual(engine.calls[-1][1]["channels"], [])
        self.assertIn("> [ ] Qwen", render(ui, 80, 24))

    async def test_a_ticked_server_that_lists_nothing_now_still_unticks(self) -> None:
        ui, engine = make()
        self.unlisted(ui, engine, ["13"])
        engine.channel_lists["900"] = []
        await keys(ui, "Enter", "2", "2", "Enter")
        self.assertEqual(ui.picked, {})
        self.assertEqual(engine.calls[-1][0], "save_setup")
        self.assertEqual(engine.calls[-1][1]["channels"], [])
        self.assertEqual(ui.error, "")

    async def test_keys_are_dropped_while_a_server_is_listed(self) -> None:
        ui, engine = await self.open_servers()
        entered, gate = asyncio.Event(), asyncio.Event()

        async def held(guild_id: str) -> list[dict]:
            engine.calls.append(("channels", guild_id))
            entered.set()
            await gate.wait()
            return list(engine.channel_lists.get(guild_id, []))

        engine.channels = held
        task = asyncio.create_task(keys(ui, "Enter"))
        await asyncio.wait_for(entered.wait(), 1)
        self.assertTrue(ui.busy)
        await keys(ui, "Enter", "ArrowDown", "ArrowRight", "Escape")
        gate.set()
        await asyncio.wait_for(task, 1)
        self.assertEqual([call[0] for call in engine.calls], ["guilds", "channels", "save_setup"])
        self.assertEqual(set(ui.picked), {"c1", "c2"})
        self.assertEqual((ui.flow["screen"], ui.depth, ui.local_index), ("servers", "guilds", 0))
        self.assertFalse(ui.busy)


HOOK = "https://discord.com/api/webhooks/123/abc"


class OwnWebhookTests(unittest.IsolatedAsyncioTestCase):
    async def open_channels(self) -> tuple[Controller, FakeEngine]:
        ui, engine = make()
        engine.user = {"id": "1", "username": "sosa", "global_name": "Sosa"}
        engine.http = object()
        engine.guild_list = [{"id": "g1", "name": "Qwen", "icon": ""}]
        engine.channel_lists["g1"] = [
            {"id": "c1", "name": "general", "parent": "", "topic": ""},
            {"id": "c2", "name": "dev", "parent": "", "topic": ""},
        ]
        ui.refresh()
        await keys(ui, "Enter", "2", "2", "ArrowRight")
        self.assertEqual(ui.depth, "channels")
        return ui, engine

    async def test_enter_ticks_the_channel_and_opens_the_url_row(self) -> None:
        ui, engine = await self.open_channels()
        await keys(ui, "Enter")
        self.assertEqual(ui.typing, "webhook")
        self.assertEqual(ui.webhook_channel["id"], "c1")
        self.assertIn("c1", ui.picked)
        self.assertEqual(ui.hint(), "enter keeps the url, empty enter or esc unticks")
        for ch in HOOK:
            await ui.press(ch, plain=True)
        await keys(ui, "Enter")
        self.assertIsNone(ui.typing)
        self.assertEqual(ui.picked["c1"]["webhook_url"], HOOK)
        self.assertEqual(engine.calls[-1][1]["channels"][0]["webhook_url"], HOOK)

    async def test_the_url_row_of_a_channel_ticked_before_says_esc_leaves_it(self) -> None:
        ui, engine = await self.open_channels()
        await keys(ui, "a", "Enter")
        self.assertEqual((ui.typing, ui.webhook_new), ("webhook", False))
        self.assertEqual(ui.hint(), "enter keeps the url, empty enter unticks, esc leaves it as before")
        await keys(ui, "Escape")
        self.assertEqual(ui.hint(), "enter toggles, a selects all, esc back")

    async def test_invalid_url_keeps_the_row_open_with_the_error(self) -> None:
        ui, engine = await self.open_channels()
        await keys(ui, "Enter")
        for ch in "https://example.com/x":
            await ui.press(ch, plain=True)
        await keys(ui, "Enter")
        self.assertEqual(ui.typing, "webhook")
        self.assertEqual(ui.error, "webhook url is not a discord incoming webhook")
        self.assertEqual(ui.picked["c1"]["webhook_url"], "")

    async def test_empty_enter_or_escape_unticks_the_channel(self) -> None:
        ui, engine = await self.open_channels()
        await keys(ui, "Enter", "Enter")
        self.assertIsNone(ui.typing)
        self.assertNotIn("c1", ui.picked)
        await keys(ui, "Enter", "Escape")
        self.assertNotIn("c1", ui.picked)
        self.assertEqual(engine.calls[-1][1]["channels"], [])

    async def test_escape_with_text_cancels_the_edit(self) -> None:
        # Esc puts the channel back as it was before Enter: a channel this Enter ticked is unticked again (the
        # real store fills a URL it kept for a channel into a row saved without one, RealEngineStartTests), a
        # channel that was already ticked without a URL stays ticked without one
        ui, engine = await self.open_channels()
        await keys(ui, "Enter")
        for ch in "https://":
            await ui.press(ch, plain=True)
        await keys(ui, "Escape")
        self.assertIsNone(ui.typing)
        self.assertNotIn("c1", ui.picked)
        self.assertEqual(engine.calls[-1][1]["channels"], [])
        await keys(ui, "a", "Enter")
        self.assertEqual((ui.typing, ui.webhook_channel["id"], ui.draft), ("webhook", "c1", ""))
        await ui.paste("https://")
        await keys(ui, "Escape")
        self.assertIsNone(ui.typing)
        self.assertEqual(ui.picked["c1"]["webhook_url"], "")
        self.assertEqual([row["channel_id"] for row in engine.selection], ["c1", "c2"])
        await keys(ui, "Enter")
        self.assertEqual((ui.typing, ui.draft), ("webhook", ""))

    async def assert_escape_keeps_a_channel_ticked_by_a(self, typed: str) -> None:
        ui, engine = await self.open_channels()
        await keys(ui, "a", "Enter")
        self.assertEqual((ui.typing, ui.webhook_channel["id"]), ("webhook", "c1"))
        for ch in typed:
            await ui.press(ch, plain=True)
        await keys(ui, "Escape")
        self.assertIsNone(ui.typing)
        self.assertIn("c1", ui.picked)
        self.assertEqual(ui.picked["c1"]["webhook_url"], "")
        saved = engine.calls[-1]
        self.assertEqual(saved[0], "save_setup")
        self.assertEqual([(row["channel_id"], row["webhook_url"]) for row in saved[1]["channels"]], [("c1", ""), ("c2", "")])

    async def test_escape_without_text_keeps_a_channel_that_a_ticked(self) -> None:
        # Esc puts the channel back as it was before Enter: only the Enter that ticked it unticks it again
        await self.assert_escape_keeps_a_channel_ticked_by_a("")

    async def test_escape_after_one_character_keeps_a_channel_that_a_ticked(self) -> None:
        await self.assert_escape_keeps_a_channel_ticked_by_a("h")

    async def test_escape_on_a_channel_ticked_before_saves_and_refreshes_nothing(self) -> None:
        # nothing changed, so a running mirror is not refreshed (gateway resubscribe, "selection updated")
        ui, engine = await self.open_channels()
        engine.running = True
        await keys(ui, "a", "Enter")
        self.assertEqual((ui.typing, ui.webhook_channel["id"]), ("webhook", "c1"))
        before = list(engine.calls)
        await ui.paste("https://")
        await keys(ui, "Escape")
        self.assertIsNone(ui.typing)
        self.assertEqual(engine.calls, before)
        self.assertEqual(set(ui.picked), {"c1", "c2"})
        self.assertEqual(ui.picked["c1"]["webhook_url"], "")

    async def test_backspace_edits_the_url_row(self) -> None:
        ui, engine = await self.open_channels()
        await keys(ui, "Enter")
        await ui.paste(HOOK + "x")
        await keys(ui, "Backspace")
        self.assertEqual(ui.draft, HOOK)
        await keys(ui, "Enter")
        self.assertEqual(ui.picked["c1"]["webhook_url"], HOOK)

    async def test_enter_on_a_channel_with_a_url_unticks_it(self) -> None:
        ui, engine = await self.open_channels()
        await keys(ui, "Enter")
        await ui.paste(HOOK + "\n")
        await keys(ui, "Enter")
        self.assertEqual(ui.picked["c1"]["webhook_url"], HOOK)
        await keys(ui, "Enter")
        self.assertNotIn("c1", ui.picked)

    async def test_start_refuses_a_ticked_channel_without_a_webhook_and_jumps_to_it(self) -> None:
        ui, engine = await self.open_channels()
        await keys(ui, "ArrowDown", "a", "Escape", "Escape", "Escape")
        self.assertEqual(ui.flow["screen"], "menu")
        self.assertEqual(set(ui.picked), {"c1", "c2"})
        await keys(ui, "1")
        # after the last save, Start only lists the servers and that server's channels to jump there
        self.assertEqual([call[0] for call in engine.calls[-3:]], ["save_setup", "guilds", "channels"])
        self.assertEqual(engine.calls[-1], ("channels", "g1"))
        self.assertNotIn(("start",), engine.calls)
        self.assertEqual(ui.error, "#general has no webhook")
        self.assertEqual(ui.flow["screen"], "servers")
        self.assertEqual(ui.depth, "channels")
        self.assertEqual(ui.active_guild["id"], "g1")
        self.assertEqual(ui.channel_rows[ui.local_index]["id"], "c1")

    async def test_refused_start_stays_on_the_menu_when_the_channel_list_fails(self) -> None:
        ui, engine = await self.open_channels()
        await keys(ui, "a", "Escape", "Escape", "Escape")
        engine.fail["channels"] = ApiError(403, "missing access")
        await keys(ui, "1")
        self.assertNotIn(("start",), engine.calls)
        self.assertEqual(ui.flow["screen"], "menu")
        self.assertEqual(ui.error, "#general has no webhook (missing access)")
        self.assertFalse(ui.busy)
        engine.fail["channels"] = aiohttp.ClientConnectionError("Cannot connect to host discord.com:443")
        with self.assertLogs("mirror.cli", level="ERROR"):
            await keys(ui, "1")
        self.assertNotIn(("start",), engine.calls)
        self.assertEqual(ui.flow["screen"], "menu")
        self.assertEqual(ui.error, "#general has no webhook (request failed)")
        self.assertFalse(ui.busy)

    async def test_start_runs_when_every_ticked_channel_has_a_webhook(self) -> None:
        ui, engine = await self.open_channels()
        await keys(ui, "Enter")
        await ui.paste(HOOK)
        await keys(ui, "Enter")
        # Start must run with the ticked channel, not with an empty selection
        self.assertEqual(engine.selection[0]["webhook_url"], HOOK)
        await keys(ui, "Escape", "Escape", "Escape", "1")
        self.assertEqual(set(ui.picked), {"c1"})
        self.assertEqual(engine.calls[-1], ("start",))
        self.assertEqual(ui.flow["screen"], "running")

    def stale(self, ui: Controller, engine: FakeEngine, rows: list[dict]) -> None:
        engine.user = {"id": "1", "username": "sosa", "global_name": "Sosa"}
        engine.http = object()
        engine.guild_list = [{"id": "g1", "name": "Qwen", "icon": ""}]
        engine.channel_lists["g1"] = [{"id": "c2", "name": "dev", "parent": "", "topic": ""}]
        engine.selection = [dict(row, enabled=True) for row in rows]
        ui.refresh()

    async def test_start_jumps_to_a_ticked_channel_that_is_no_longer_listed(self) -> None:
        # a kept selection (story 26) can hold a channel deleted or hidden since, and the engine's list can
        # also miss a readable one (Engine._role_ids reads no roles when the member call fails): the row is
        # added to the channel list so Enter can give it a URL or untick it; Start never drops it itself
        ui, engine = make()
        self.stale(ui, engine, [
            {"channel_id": "c9", "guild_id": "g1", "guild_name": "Qwen", "channel_name": "gone", "webhook_url": ""},
            {"channel_id": "c2", "guild_id": "g1", "guild_name": "Qwen", "channel_name": "dev", "webhook_url": HOOK},
        ])
        await keys(ui, "Enter", "1")
        self.assertEqual([call[0] for call in engine.calls], ["guilds", "channels"])
        self.assertEqual([row["channel_id"] for row in engine.selection], ["c9", "c2"])
        self.assertEqual(set(ui.picked), {"c9", "c2"})
        self.assertEqual(ui.error, "#gone has no webhook (not listed)")
        self.assertEqual((ui.flow["screen"], ui.depth, ui.active_guild["id"]), ("servers", "channels", "g1"))
        self.assertEqual([channel["id"] for channel in ui.channel_rows], ["c2", "c9"])
        self.assertEqual(ui.channel_rows[ui.local_index]["id"], "c9")
        self.assertFalse(ui.busy)
        await keys(ui, "Enter", "Enter")
        self.assertEqual([row["channel_id"] for row in engine.selection], ["c2"])
        await keys(ui, "Escape", "Escape", "Escape", "1")
        self.assertEqual(engine.calls[-1], ("start",))
        self.assertEqual(ui.flow["screen"], "running")

    async def test_start_jumps_to_a_ticked_channel_of_a_server_the_account_left(self) -> None:
        ui, engine = make()
        self.stale(ui, engine, [
            {"channel_id": "c7", "guild_id": "g7", "guild_name": "Old", "channel_name": "news",
             "parent": "Info", "topic": "t", "webhook_url": ""},
        ])
        await keys(ui, "Enter", "1")
        # the server is not in the account's server list, so its channels are not asked for
        self.assertEqual([call[0] for call in engine.calls], ["guilds"])
        self.assertEqual(set(ui.picked), {"c7"})
        self.assertEqual(ui.error, "#news has no webhook (not listed)")
        self.assertEqual((ui.flow["screen"], ui.depth), ("servers", "channels"))
        self.assertEqual((ui.active_guild["id"], ui.active_guild["name"]), ("g7", "Old"))
        self.assertEqual(ui.channel_rows, [{"id": "c7", "name": "news", "parent": "Info", "topic": "t"}])
        self.assertEqual(ui.local_index, 0)
        await keys(ui, "Enter")
        await ui.paste(HOOK)
        await keys(ui, "Enter")
        # the URL row of the shown row keeps the stored server, parent and topic
        self.assertEqual(engine.selection[0]["webhook_url"], HOOK)
        self.assertEqual((engine.selection[0]["guild_id"], engine.selection[0]["parent"]), ("g7", "Info"))
        await keys(ui, "Escape")
        self.assertEqual((ui.depth, ui.local_index), ("guilds", 0))

    async def test_a_failed_save_keeps_start_refused_while_the_stored_row_has_no_webhook(self) -> None:
        # Start checks what is stored, not the ticks in memory a failed save left behind: engine.start()
        # reads the stored selection and would try to create a server for a stored row without a URL
        ui, engine = make()
        self.stale(ui, engine, [
            {"channel_id": "c9", "guild_id": "g1", "guild_name": "Qwen", "channel_name": "gone", "webhook_url": ""},
        ])
        await keys(ui, "Enter", "1")
        engine.fail["save_setup"] = RuntimeError("database is locked")
        await keys(ui, "Enter", "Enter")
        self.assertEqual(ui.error, "database is locked")
        self.assertEqual([row["channel_id"] for row in engine.selection], ["c9"])
        await keys(ui, "Escape", "Escape", "Escape", "1")
        self.assertNotIn(("start",), engine.calls)
        self.assertEqual(ui.error, "#gone has no webhook (not listed)")
        self.assertEqual(ui.channel_rows[ui.local_index]["id"], "c9")
        self.assertFalse(ui.busy)

    async def test_a_failed_url_save_keeps_start_refused(self) -> None:
        ui, engine = await self.open_channels()
        engine.selection = [{"channel_id": "c1", "guild_id": "g1", "guild_name": "Qwen", "channel_name": "general",
                             "parent": "", "topic": "", "webhook_url": "", "enabled": True}]
        ui.refresh()
        await keys(ui, "Enter")
        await ui.paste(HOOK)
        engine.fail["save_setup"] = RuntimeError("database is locked")
        await keys(ui, "Enter")
        self.assertEqual(ui.error, "database is locked")
        await keys(ui, "Escape", "Escape", "Escape", "1")
        self.assertNotIn(("start",), engine.calls)
        self.assertEqual(ui.error, "#general has no webhook")
        self.assertEqual(ui.channel_rows[ui.local_index]["id"], "c1")
        del engine.fail["save_setup"]
        await keys(ui, "Enter")
        await ui.paste(HOOK)
        await keys(ui, "Enter", "Escape", "Escape", "Escape", "1")
        self.assertEqual(engine.calls[-1], ("start",))

    async def test_start_does_not_run_when_the_stored_selection_cannot_be_read(self) -> None:
        ui, engine = make()
        await keys(ui, "Enter")
        snapshot = engine.snapshot

        def locked() -> dict:
            raise RuntimeError("database is locked")

        def broken() -> dict:
            raise sqlite3.OperationalError("disk I/O error")

        engine.snapshot = locked
        await keys(ui, "1")
        self.assertEqual(ui.error, "database is locked")
        self.assertEqual(ui.flow["screen"], "menu")
        engine.snapshot = broken
        with self.assertLogs("mirror.cli", level="ERROR"):
            await keys(ui, "1")
        self.assertEqual(ui.error, "request failed")
        self.assertEqual(ui.flow["screen"], "menu")
        self.assertNotIn(("start",), engine.calls)
        self.assertFalse(ui.busy)
        engine.snapshot = snapshot
        await keys(ui, "1")
        self.assertEqual(engine.calls, [("start",)])


PERMS = str((1 << 10) | (1 << 16))  # VIEW_CHANNEL and READ_MESSAGE_HISTORY


class RoleGatedHTTP:
    """Discord for the real Engine: #gated (13) is readable through role 77 only."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.member_error: ApiError | None = None

    async def guilds(self) -> list[dict]:
        self.calls.append(("GET", "/users/@me/guilds"))
        return [{"id": "900", "name": "Qwen", "permissions": PERMS}]

    async def channels(self, guild_id: str) -> list[dict]:
        self.calls.append(("GET", f"/guilds/{guild_id}/channels"))
        return [
            {"id": "12", "name": "dev", "type": 0, "position": 0},
            {"id": "13", "name": "gated", "type": 0, "position": 1, "permission_overwrites": [
                {"id": "900", "type": 0, "allow": "0", "deny": str(1 << 10)},
                {"id": "77", "type": 0, "allow": str(1 << 10), "deny": "0"},
            ]},
        ]

    async def call(self, method: str, path: str, **kw: Any) -> Any:
        self.calls.append((method, path))
        if path == "/users/@me/guilds/900/member":
            if self.member_error is not None:
                raise self.member_error
            return {"roles": ["77"]}
        raise ApiError(403, f"{method} {path} is not expected")


class RealEngineStartTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.engine = Engine(self.store)
        self.http = RoleGatedHTTP()
        self.engine.http = self.http
        self.engine.user = {"id": "1", "username": "sosa", "global_name": "Sosa"}
        self.store.replace_selection([{
            "channel_id": "13", "guild_id": "900", "guild_name": "Qwen", "channel_name": "gated",
            "webhook_url": "", "enabled": True, "parent": "", "topic": "",
        }])

    async def asyncTearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    async def test_a_failed_member_call_keeps_the_ticked_channel(self) -> None:
        self.assertEqual([c["id"] for c in await self.engine.channels("900")], ["12", "13"])
        self.http.member_error = ApiError(500, "GET /users/@me/guilds/900/member failed")
        # Engine._role_ids reads no roles, so the engine's list misses #gated: that proves nothing
        self.assertEqual([c["id"] for c in await self.engine.channels("900")], ["12"])
        ui = Controller(self.engine, read_keychain=read_keychain_fake)
        ui.refresh()
        await keys(ui, "Enter", "1")
        self.assertEqual([row["channel_id"] for row in self.store.selection()], ["13"])
        self.assertEqual(set(ui.picked), {"13"})
        self.assertEqual(ui.error, "#gated has no webhook (not listed)")
        self.assertEqual(ui.channel_rows[ui.local_index]["id"], "13")
        self.assertNotIn(("POST", "/guilds"), self.http.calls)
        self.assertFalse(self.engine.running)

    async def open_channels(self) -> Controller:
        self.store.replace_selection([])
        ui = Controller(self.engine, read_keychain=read_keychain_fake)
        ui.refresh()
        await keys(ui, "Enter", "2", "2", "ArrowRight")
        self.assertEqual([channel["id"] for channel in ui.channel_rows], ["12", "13"])
        return ui

    def stored(self) -> list[tuple[str, str]]:
        return [(row["channel_id"], row["webhook_url"]) for row in self.store.selection()]

    async def test_a_channel_ticked_again_by_enter_never_takes_its_kept_url(self) -> None:
        # Store.replace_selection fills a row saved without a URL from the URL the channel had (hooks), so a
        # single tick is stored only with a URL entered in its row: Esc and an empty Enter untick it again
        ui = await self.open_channels()
        await keys(ui, "Enter")
        await ui.paste(HOOK)
        await keys(ui, "Enter")
        self.assertEqual(self.stored(), [("12", HOOK)])
        await keys(ui, "Enter")
        self.assertEqual(self.stored(), [])
        self.assertEqual(self.store.hooks(), {"12": HOOK})
        await keys(ui, "Enter")
        self.assertEqual((ui.typing, ui.draft), ("webhook", ""))
        await ui.paste("https://")
        await keys(ui, "Escape")
        self.assertIsNone(ui.typing)
        self.assertNotIn("12", ui.picked)
        self.assertEqual(self.stored(), [])
        await keys(ui, "Enter", "Enter")
        self.assertNotIn("12", ui.picked)
        self.assertEqual(self.stored(), [])
        await keys(ui, "Escape", "Escape", "Escape", "1")
        self.assertEqual(ui.error, "select servers first")
        self.assertEqual(ui.flow["screen"], "menu")
        self.assertFalse(self.engine.running)
        self.assertNotIn(("POST", "/guilds"), self.http.calls)

    async def test_select_all_and_the_server_enter_bring_back_a_kept_url(self) -> None:
        # plan A leaves the store as it is: a channel ticked by `a` or by Enter on its server gets back the URL
        # the store kept for it ("webhook set"); a URL entered in its row replaces the kept one
        ui = await self.open_channels()
        await keys(ui, "Enter")
        await ui.paste(HOOK)
        await keys(ui, "Enter", "Enter")
        self.assertEqual(self.stored(), [])
        await keys(ui, "a")
        self.assertEqual(self.stored(), [("12", HOOK), ("13", "")])
        self.assertEqual(ui.picked["12"]["webhook_url"], HOOK)
        await keys(ui, "Escape", "Enter")
        self.assertEqual(self.stored(), [])
        await keys(ui, "Enter")
        self.assertEqual(self.stored(), [("12", HOOK), ("13", "")])
        other = "https://discord.com/api/webhooks/456/def"
        await keys(ui, "ArrowRight", "Enter")
        self.assertEqual(self.stored(), [("13", "")])
        await keys(ui, "Enter")
        await ui.paste(other)
        await keys(ui, "Enter")
        self.assertEqual(self.stored(), [("12", other), ("13", "")])
        self.assertEqual(self.store.hooks(), {"12": other})
        await keys(ui, "Escape", "Escape", "Escape", "1")
        self.assertEqual(ui.error, "#gated has no webhook")
        self.assertEqual(ui.channel_rows[ui.local_index]["id"], "13")
        self.assertFalse(self.engine.running)
        self.assertNotIn(("POST", "/guilds"), self.http.calls)


if __name__ == "__main__":
    unittest.main()
