"""Screens of the CLI: the port of the old browser page's app.js, calling the engine in-process."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from ..discord_api import ApiError, clean_webhook
from ..keychain import read_keychain
from .flow import items, reduce

log = logging.getLogger("mirror.cli")

BACKFILL = [0, 25, 50, 100, 250, 500]
TOKEN_ROWS = ["token", "keep", "save", "service", "account", "keychain", "back"]
HOOK_ROWS = ["backfill", "threads", "back"]
TEXT_FIELDS = ("token", "service", "account")
LOCKED_TEXT = {"servers": "add a working token first", "interface": "the browser UI is not available yet"}
UNEXPECTED = "request failed"  # what the old page showed for any other error (web.guard)
MAX_MESSAGES = 200


def _menu_state(screen: str = "menu", index: int = 0) -> dict[str, Any]:
    return {"screen": screen, "index": index, "action": None, "locked": False}


class Controller:
    def __init__(self, engine: Any, read_keychain: Callable[[str, str], Awaitable[str]] = read_keychain) -> None:
        self.engine = engine
        self.read_keychain = read_keychain
        self.flow = reduce(None)
        self.snap: dict[str, Any] = {"user": None, "running": False, "status": "idle", "options": {}, "selection": [], "log": [], "mirrored": 0}
        self.messages: list[dict[str, Any]] = []
        self.error = ""
        self.busy = False
        self.typing: str | None = None
        self.draft = ""
        self.fields: dict[str, Any] = {"token": "", "service": "", "account": "", "keep": True}
        self.token_check = ""
        self.token_index = 0
        self.hook_index = 0
        self.local_index = 0
        self.depth = "guilds"
        self.guilds: list[dict[str, Any]] = []
        self.channel_rows: list[dict[str, Any]] = []
        self.active_guild: dict[str, Any] | None = None
        self.picked: dict[str, dict[str, Any]] = {}
        self.webhook_channel: dict[str, Any] | None = None  # the channel whose URL row is open (Task 6b)

    # ---- state -------------------------------------------------------------

    def ctx(self) -> dict[str, Any]:
        user = self.snap.get("user") or {}
        return {"tokenOk": bool(user.get("id"))}

    def refresh(self) -> None:
        self._apply(self.engine.snapshot())

    def _apply(self, snap: dict[str, Any]) -> None:
        self.snap = snap
        self.picked = {row["channel_id"]: row for row in snap.get("selection") or [] if row.get("enabled")}

    async def load(self) -> None:
        self.refresh()
        self.messages = list(self.engine.feed_items())[-MAX_MESSAGES:]

    def on_event(self, item: dict[str, Any]) -> None:
        kind = item.get("kind")
        if kind == "log" and item.get("text"):
            self.snap["log"] = [item["text"]] + list(self.snap.get("log") or [])[:59]
        elif kind == "status":
            self.snap["running"] = bool(item.get("running"))
            self.snap["status"] = item.get("status") or self.snap.get("status")
            if "mirrored" in item:
                self.snap["mirrored"] = item["mirrored"]
        elif kind == "message" and item.get("message"):
            self._place(item["message"])

    def _place(self, message: dict[str, Any]) -> None:
        for at, known in enumerate(self.messages):
            if known.get("id") == message.get("id"):
                self.messages[at] = message
                break
        else:
            self.messages.append(message)
        if len(self.messages) > MAX_MESSAGES:
            del self.messages[: len(self.messages) - MAX_MESSAGES]

    def hint(self) -> str:
        if self.typing == "token":
            return "enter or esc keeps it"
        if self.typing:
            return "enter keeps it, esc cancels the edit"
        screen = self.flow["screen"]
        if screen in ("welcome", "exit"):
            return "press any key"
        if screen == "running":
            return "esc returns to the menu"
        if screen == "servers" and self.depth == "channels":
            return "enter toggles, a selects all, esc back"
        if screen == "servers":
            return "enter toggles the server, right opens channels, esc back"
        if screen == "webhooks":
            return "up and down move, enter or a number opens, left and right change backfill and threads, esc back"
        if screen == "token":
            return "up and down move, enter opens, esc back"
        return "up and down move, enter or a number opens, esc back"

    # ---- keys --------------------------------------------------------------

    async def press(self, key: str, plain: bool = False) -> None:
        if self.busy:
            return
        screen = self.flow["screen"]
        if screen == "welcome":
            self.flow = reduce(self.flow, {"key": key}, self.ctx())
            self.error = ""
            return
        if screen == "exit":
            self.flow = _menu_state()
            self.error = ""
            return
        if screen == "running":
            if key == "Escape":
                self.flow = _menu_state()
            return
        if screen in ("menu", "settings"):
            nxt = reduce(self.flow, {"key": key}, self.ctx())
            if nxt["locked"]:
                self.flow = nxt
                item = items(screen, self.ctx())[nxt["index"]]
                self.error = LOCKED_TEXT.get(item["id"], "locked")
                return
            self.error = ""
            self.flow = nxt
            if nxt["action"] == "start":
                await self._begin()
            elif nxt["action"] == "exit":
                await self._halt()
            elif nxt["action"] == "servers":
                await self._load_guilds()
            return
        if screen == "token":
            await self._token_key(key, plain)
        elif screen == "webhooks":
            await self._hook_key(key)
        elif screen == "servers":
            await self._server_key(key)

    async def paste(self, text: str) -> None:
        if self.busy or self.flow["screen"] != "token" or self.typing or TOKEN_ROWS[self.token_index] != "token":
            return
        self._type_token(text)

    # ---- menu actions ------------------------------------------------------

    async def _begin(self) -> None:
        self.busy = True
        self.error = ""
        self.flow = _menu_state("running")
        try:
            await self.engine.start()
            self.refresh()
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
            self.flow = _menu_state()
        except Exception:
            log.exception("start failed")
            self.error = UNEXPECTED
            self.flow = _menu_state()
        finally:
            self.busy = False

    async def _halt(self) -> None:
        self.busy = True
        try:
            await self.engine.stop()
            self.refresh()
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
        except Exception:
            log.exception("stop failed")
            self.error = UNEXPECTED
        finally:
            self.busy = False
            self.flow = _menu_state("exit")

    # ---- token, webhooks and servers screens: Tasks 3, 4 and 6 -------------

    async def _token_key(self, key: str, plain: bool) -> None:
        raise NotImplementedError

    def _type_token(self, text: str) -> None:
        raise NotImplementedError

    async def _hook_key(self, key: str) -> None:
        raise NotImplementedError

    async def _server_key(self, key: str) -> None:
        raise NotImplementedError

    async def _load_guilds(self) -> None:
        raise NotImplementedError
