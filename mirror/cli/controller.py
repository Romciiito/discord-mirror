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


def _display(user: dict[str, Any]) -> str:
    return str(user.get("global_name") or user.get("username") or user.get("id") or "")


class Controller:
    def __init__(self, engine: Any, read_keychain: Callable[[str, str], Awaitable[str]] = read_keychain) -> None:
        self.engine = engine
        self.read_keychain = read_keychain
        self.flow = reduce(None)
        self.snap: dict[str, Any] = {"user": None, "running": False, "status": "idle", "options": {}, "selection": [], "log": [], "mirrored": 0}
        self.messages: list[dict[str, Any]] = []
        self.error = ""
        # the engine's last error: why its last run failed (the log text it noted just before the status
        # event "error", Engine._fatal) or which webhook post failed (the "error" event, plan Task 7);
        # kept after Exit, cleared when the engine reports running again
        self.engine_error = ""
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
            if self.snap["status"] == "error":
                self.engine_error = str((self.snap.get("log") or ["error"])[0])
            elif self.snap["running"]:
                self.engine_error = ""
            if "mirrored" in item:
                self.snap["mirrored"] = item["mirrored"]
        elif kind == "error" and item.get("text"):
            self.engine_error = str(item["text"])
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
        if self.busy or self.flow["screen"] != "token":
            return
        if self.typing in TEXT_FIELDS:
            # an open field takes the paste at the end, as typed keys do; the token keeps the raw
            # text for clean_token, a keychain field keeps one line as the page's <input> did
            self.draft += text if self.typing == "token" else text.replace("\r", "").replace("\n", "")
        elif TOKEN_ROWS[self.token_index] == "token":
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
        if self.typing in TEXT_FIELDS:
            if key == "Enter" or key == "Escape":
                if self.typing == "token" or key == "Enter":
                    await self._commit_field()
                else:
                    self._cancel_edit()
            elif key == "Backspace":
                self.draft = self.draft[:-1]
            elif plain:
                self.draft += key
            return
        if key == "ArrowDown":
            self.token_index = (self.token_index + 1) % len(TOKEN_ROWS)
        elif key == "ArrowUp":
            self.token_index = (self.token_index - 1 + len(TOKEN_ROWS)) % len(TOKEN_ROWS)
        elif key == "Enter":
            await self._token_action()
        elif key == "Escape":
            self.flow = reduce(self.flow, {"key": "Escape"}, self.ctx())
            self.typing = None
        elif len(key) == 1 and "1" <= key <= str(len(TOKEN_ROWS)):
            self.token_index = int(key) - 1
            await self._token_action()
        elif plain and TOKEN_ROWS[self.token_index] == "token":
            self._type_token(key)

    def _type_token(self, text: str) -> None:
        self.typing = "token"
        self.draft = text
        self.error = ""

    async def _commit_field(self) -> None:
        field = self.typing
        if field in TEXT_FIELDS:
            self.fields[field] = self.draft
        self.typing = None
        self.draft = ""
        if field == "token":
            await self._check_token(self.fields["token"])

    def _cancel_edit(self) -> None:
        self.typing = None
        self.draft = ""

    async def _check_token(self, raw: str) -> None:
        self.busy = True
        try:
            report = await self.engine.check_token(raw)
        except (ApiError, RuntimeError) as exc:
            self.token_check = f"✗ {exc}"
            return
        except Exception:
            log.exception("token check failed")
            self.token_check = ""  # never leave the result of an earlier token next to the new one
            self.error = UNEXPECTED
            return
        finally:
            self.busy = False
        result = report.get("result")
        if result == "works":
            self.token_check = f"✓ token works – signed in as {_display(report.get('user') or {})}"
        elif result == "rejected":
            self.token_check = "✗ token rejected by Discord"
        else:
            self.token_check = f"✗ could not reach Discord ({report.get('reason') or 'network error'})"

    async def _token_action(self) -> None:
        target = TOKEN_ROWS[self.token_index]
        self.error = ""
        if self.typing in TEXT_FIELDS and target != self.typing:
            await self._commit_field()
        if target in TEXT_FIELDS:
            if self.typing == target:
                return
            self.typing = target
            self.draft = self.fields[target] or ""
        elif target == "keep":
            self.fields["keep"] = not self.fields["keep"]
        elif target == "back":
            self.flow = reduce(self.flow, {"key": "Escape"}, self.ctx())
        elif target == "save":
            await self._sign_in(self.fields["token"])
        elif target == "keychain":
            token = await self._keychain_token()
            if token is not None:
                await self._sign_in(token)

    async def _keychain_token(self) -> str | None:
        """The keychain secret, "" when both fields are empty, None when the lookup failed (error is set)."""
        if not (self.fields["service"] or self.fields["account"]):
            return ""
        self.busy = True
        try:
            return await self.read_keychain(self.fields["service"], self.fields["account"])
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
        except Exception:
            log.exception("keychain read failed")
            self.error = UNEXPECTED
        finally:
            self.busy = False
        return None

    async def _sign_in(self, token: str) -> None:
        if not token:
            self.error = "paste a token or use the keychain fields"
            return
        self.busy = True
        try:
            user = await self.engine.use_token(token, bool(self.fields["keep"]))
            self.engine.note(f"signed in as {_display(user)}")
            self.refresh()
            self.fields["token"] = ""
            self.token_check = ""
            self.error = ""
            self.flow = reduce(self.flow, {"key": "Escape"}, self.ctx())
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
        except Exception:
            log.exception("sign in failed")
            self.error = UNEXPECTED
        finally:
            self.busy = False

    async def _hook_key(self, key: str) -> None:
        if key == "ArrowDown":
            self.hook_index = (self.hook_index + 1) % len(HOOK_ROWS)
        elif key == "ArrowUp":
            self.hook_index = (self.hook_index - 1 + len(HOOK_ROWS)) % len(HOOK_ROWS)
        elif key == "ArrowRight":
            await self._shift_hook(1)
        elif key == "ArrowLeft":
            await self._shift_hook(-1)
        elif key == "Enter":
            await self._hook_action()
        elif key == "Escape":
            self.flow = reduce(self.flow, {"key": "Escape"}, self.ctx())
        elif len(key) == 1 and "1" <= key <= str(len(HOOK_ROWS)):
            self.hook_index = int(key) - 1
            await self._hook_action()

    async def _hook_action(self) -> None:
        target = HOOK_ROWS[self.hook_index]
        self.error = ""
        if target == "backfill":
            await self._step_backfill(1, wrap=True)
        elif target == "threads":
            self.snap["options"]["include_threads"] = not self.snap["options"].get("include_threads")
            await self._save_options()
        elif target == "back":
            self.flow = reduce(self.flow, {"key": "Escape"}, self.ctx())

    async def _step_backfill(self, direction: int, wrap: bool) -> None:
        current = int(self.snap["options"].get("backfill") or 0)
        at = BACKFILL.index(current) if current in BACKFILL else 0
        if wrap:
            at = (at + direction + len(BACKFILL)) % len(BACKFILL)
        else:
            at = max(0, min(len(BACKFILL) - 1, at + direction))
        self.snap["options"]["backfill"] = BACKFILL[at]
        await self._save_options()

    async def _shift_hook(self, direction: int) -> None:
        target = HOOK_ROWS[self.hook_index]
        if target == "backfill":
            await self._step_backfill(direction, wrap=False)
        elif target == "threads":
            self.snap["options"]["include_threads"] = direction > 0
            await self._save_options()

    async def _save_options(self) -> None:
        """What PUT /api/setup did for the page: save, then refresh a running engine (web.save_setup)."""
        options = self.snap["options"]
        body = {
            "backfill": options.get("backfill", 0),
            "include_threads": bool(options.get("include_threads")),
            "mirror": bool(options.get("mirror")),
            "global_webhook": "",
            "dest_name": options.get("dest_name") or "mirror",
            "channels": list(self.picked.values()),
        }
        self.busy = True
        try:
            self.engine.save_setup(body)
            if self.engine.running:
                await self.engine.refresh()
            self.refresh()
            self.error = ""
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
        except Exception:
            log.exception("save failed")
            self.error = UNEXPECTED
        finally:
            self.busy = False

    async def _server_key(self, key: str) -> None:
        raise NotImplementedError

    async def _load_guilds(self) -> None:
        raise NotImplementedError
