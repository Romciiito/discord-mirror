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
        # kept after Exit, cleared when a new run begins (the status "connecting" of Engine._start)
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
        self.webhook_new = False  # the Enter that opened the URL row ticked the channel: Esc unticks it again
        self.targets: list[dict[str, Any]] = []  # the servers the owner owns, offered as the copy (decision 9m)
        self.target_source: dict[str, Any] | None = None  # the source server the target picker fills

    # ---- state -------------------------------------------------------------

    def ctx(self) -> dict[str, Any]:
        user = self.snap.get("user") or {}
        return {"tokenOk": bool(user.get("id"))}

    def refresh(self) -> None:
        self._apply(self.engine.snapshot())

    def _apply(self, snap: dict[str, Any]) -> None:
        self.snap = snap
        # copies: the URL row edits a picked row, which must not change the snapshot's rows before a save
        self.picked = {row["channel_id"]: dict(row) for row in snap.get("selection") or [] if row.get("enabled")}

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
            elif item.get("status") == "connecting":
                # only Engine._start reports "connecting"; the gateway's READY also reports running
                # ("live"), within the same run, and must not hide a webhook post that already failed
                self.engine_error = ""
            if "mirrored" in item:
                self.snap["mirrored"] = item["mirrored"]
        elif kind == "error" and item.get("text"):
            self.engine_error = str(item["text"])
        elif kind == "mirrored" and "mirrored" in item:
            # Engine._relay_create emits the count after each created mirrored message
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
        if self.typing == "webhook":
            # Esc unticks a channel the Enter that opened the row ticked, and leaves one ticked before as it was;
            # both fit a 60-column terminal, since the renderer cuts every line to the width
            if self.webhook_new:
                return "enter keeps the url, empty enter or esc unticks"
            return "enter keeps the url, empty enter unticks, esc cancels"
        if self.typing:
            return "enter keeps it, esc cancels the edit"
        screen = self.flow["screen"]
        if screen in ("welcome", "exit"):
            return "press any key"
        if screen == "running":
            return "esc returns to the menu"
        if screen == "servers" and self.depth == "targets":
            return "enter fills the copy, esc back"
        if screen == "servers" and self.depth == "channels":
            return "enter toggles, a selects all, esc back"
        if screen == "servers":
            return "enter toggles, c fills copy, right opens channels, esc back"
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
        if self.busy:
            return
        if self.typing == "webhook":
            self.draft += text.strip()
            return
        if self.flow["screen"] != "token":
            return
        if self.typing in TEXT_FIELDS:
            # an open field takes the paste at the end, as typed keys do; the token keeps the raw
            # text for clean_token, a keychain field keeps one line as the page's <input> did
            self.draft += text if self.typing == "token" else text.replace("\r", "").replace("\n", "")
        elif TOKEN_ROWS[self.token_index] == "token":
            self._type_token(text)

    # ---- menu actions ------------------------------------------------------

    async def _begin(self) -> None:
        try:
            # engine.start() reads the stored selection, so Start checks that and not the ticks a failed
            # save left in memory (a stored row without a URL would make the engine create a server)
            self.refresh()
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
            self.flow = _menu_state()
            return
        except Exception:
            log.exception("reading the selection before start failed")
            self.error = UNEXPECTED
            self.flow = _menu_state()
            return
        blocker = self._start_blocker()
        if blocker is not None:
            await self._jump_to(blocker)
            return
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

    def _start_blocker(self) -> dict[str, Any] | None:
        """The first ticked row without an own webhook: Start refuses it (decision 9f)."""
        for row in self.picked.values():
            if not str(row.get("webhook_url") or "").strip():
                return row
        return None

    async def _jump_to(self, row: dict[str, Any]) -> None:
        name = f"#{row.get('channel_name') or row.get('channel_id')}"
        self.error = f"{name} has no webhook"
        self.busy = True
        try:
            self.guilds = list(await self.engine.guilds())
            guild = next((g for g in self.guilds if g["id"] == row.get("guild_id")), None)
            listed = list(await self.engine.channels(guild["id"])) if guild else []
        except (ApiError, RuntimeError) as exc:
            # a list that failed proves nothing about the row: it stays ticked and Start stays refused
            self.error = f"{self.error} ({exc})"
            self.flow = _menu_state()
            return
        except Exception:
            log.exception("jump to the channel without a webhook failed")
            self.error = f"{self.error} ({UNEXPECTED})"
            self.flow = _menu_state()
            return
        finally:
            self.busy = False
        at = next((at for at, channel in enumerate(listed) if channel["id"] == row.get("channel_id")), None)
        if at is None:
            # not listed: the channel was deleted or hidden, the account left the server, or the engine's
            # list missed it (Engine._role_ids reads no roles when the member call fails), so this proves
            # nothing; the row is shown at the end of the list so Enter can give it a URL or untick it
            guild = guild or {"id": row.get("guild_id"), "name": row.get("guild_name") or "", "icon": ""}
            listed.append({
                "id": row.get("channel_id"),
                "name": row.get("channel_name") or str(row.get("channel_id") or ""),
                "parent": row.get("parent") or "",
                "topic": row.get("topic") or "",
            })
            at = len(listed) - 1
            self.error = f"{name} has no webhook (not listed)"
        self.channel_rows = listed
        self.active_guild = guild
        self.depth = "channels"
        self.local_index = at
        self.flow = _menu_state("servers")

    async def _halt(self) -> None:
        self.busy = True
        try:
            await self.engine.stop()
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
        except Exception:
            log.exception("stop failed")
            self.error = UNEXPECTED
        finally:
            self.busy = False
            self.flow = _menu_state("exit")
            # on both paths: a stop that raised has already cleared Engine.running before gateway.stop()
            self._resync()

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
        # no field is open here: _token_key sends every key to an open field and returns before this runs
        if target in TEXT_FIELDS:
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
            self._resync()
        except Exception:
            log.exception("save failed")
            self.error = UNEXPECTED
            self._resync()
        finally:
            self.busy = False

    def _resync(self) -> None:
        """After a failed engine call: show again the options and ticks the engine and store hold, not the ones
        the screen changed before the call; the error of the call stays when the state cannot be read either."""
        try:
            self.refresh()
        except (ApiError, RuntimeError) as exc:
            self.error = self.error or str(exc)
        except Exception:
            log.exception("reading the state failed")
            self.error = self.error or UNEXPECTED

    async def _load_guilds(self) -> None:
        self.busy = True
        self.error = ""
        self.depth = "guilds"
        self.local_index = 0
        try:
            self.guilds = list(await self.engine.guilds())
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
            self.flow = _menu_state("settings", 1)
        except Exception:
            log.exception("server list failed")
            self.error = UNEXPECTED
            self.flow = _menu_state("settings", 1)
        finally:
            self.busy = False

    async def _server_key(self, key: str) -> None:
        if self.depth == "channels":
            if self.typing == "webhook":
                await self._webhook_key(key)
                return
            if key == "Escape":
                self.depth = "guilds"
                active = self.active_guild["id"] if self.active_guild else None
                self.local_index = max(0, next((at for at, g in enumerate(self.guilds) if g["id"] == active), 0))
                return
            if not self.channel_rows:
                return
            if key == "ArrowDown":
                self.local_index = min(len(self.channel_rows) - 1, self.local_index + 1)
            elif key == "ArrowUp":
                self.local_index = max(0, self.local_index - 1)
            elif key == "Enter":
                await self._channel_enter(self.channel_rows[self.local_index])
            elif key in ("a", "A"):
                for channel in self.channel_rows:
                    self._remember(self.active_guild, channel)
                await self._save_options()
            return
        if self.depth == "targets":
            await self._target_key(key)
            return
        if key == "Escape":
            self.flow = reduce(self.flow, {"key": "Escape"}, self.ctx())
            return
        if not self.guilds:
            return
        if key == "ArrowDown":
            self.local_index = (self.local_index + 1) % len(self.guilds)
        elif key == "ArrowUp":
            self.local_index = (self.local_index - 1 + len(self.guilds)) % len(self.guilds)
        elif key == "Enter":
            await self._toggle_guild(self.guilds[self.local_index])
        elif key == "ArrowRight":
            await self._open_channels(self.guilds[self.local_index])
        elif key in ("c", "C"):
            await self._open_targets(self.guilds[self.local_index])

    def _remember(self, guild: dict[str, Any], channel: dict[str, Any]) -> None:
        previous = self.picked.get(channel["id"]) or {}
        self.picked[channel["id"]] = {
            "channel_id": channel["id"],
            "guild_id": guild["id"],
            "guild_name": guild.get("name") or "",
            "channel_name": channel.get("name") or "",
            "parent": channel.get("parent") or "",
            "topic": channel.get("topic") or "",
            "webhook_url": previous.get("webhook_url") or "",
            "enabled": True,
        }

    async def _toggle_channel(self, channel: dict[str, Any]) -> None:
        if not self.active_guild:
            return
        if channel["id"] in self.picked:
            del self.picked[channel["id"]]
        else:
            self._remember(self.active_guild, channel)
        await self._save_options()

    # ---- own webhook URL row of a ticked channel: Task 6b (decisions 9d, 9g) ----

    async def _channel_enter(self, channel: dict[str, Any]) -> None:
        row = self.picked.get(channel["id"])
        if row and row.get("webhook_url"):
            await self._toggle_channel(channel)
            return
        if not row:
            self._remember(self.active_guild, channel)
        self.typing = "webhook"
        self.webhook_channel = channel
        self.webhook_new = not row
        self.draft = ""
        self.error = ""

    async def _webhook_key(self, key: str) -> None:
        channel = self.webhook_channel or {}
        if key == "Enter":
            url = self.draft.strip()
            if not url:
                # a channel this Enter ticked lived in memory only, so unticking it restores the stored selection and
                # nothing is saved; a channel ticked before was stored, so unticking it is saved
                await self._close_webhook_row(untick=True, save=not self.webhook_new)
                return
            try:
                cleaned = clean_webhook(url)
            except ApiError as exc:
                self.error = str(exc)
                return
            if channel.get("id") in self.picked:
                self.picked[channel["id"]]["webhook_url"] = cleaned
            await self._close_webhook_row(untick=False)
        elif key == "Escape":
            # cancel puts the channel back as it was before Enter, typed or not: a channel this Enter ticked only in
            # memory is unticked again, a channel ticked before is unchanged; either way the selection is the stored
            # one, so nothing is saved and a running mirror is not refreshed
            await self._close_webhook_row(untick=self.webhook_new, save=False)
        elif key == "Backspace":
            self.draft = self.draft[:-1]
        elif len(key) == 1:
            self.draft += key

    async def _close_webhook_row(self, untick: bool, save: bool = True) -> None:
        channel = self.webhook_channel or {}
        if untick:
            self.picked.pop(channel.get("id"), None)
        self.typing = None
        self.webhook_channel = None
        self.webhook_new = False
        self.draft = ""
        self.error = ""
        if save:
            await self._save_options()

    async def _toggle_guild(self, guild: dict[str, Any]) -> None:
        self.error = ""
        # ticked as the servers screen marks it: any picked row of the server, listed or not; unticking needs no
        # channel list, so a stored row the account cannot list now (deleted, hidden, access lost) still goes
        ours = [cid for cid, row in self.picked.items() if row.get("guild_id") == guild["id"]]
        if ours:
            for cid in ours:
                del self.picked[cid]
            await self._save_options()
            # the copy stays in the owner's server: Mando never deletes there (decisions 9e, 9i)
            link = self._target_of(guild["id"])
            if link and not self.error:
                self.error = f"copy in {link.get('target_name') or 'the copy'} kept, delete it in Discord if you do not need it"
            return
        self.busy = True
        try:
            listed = list(await self.engine.channels(guild["id"]))
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
            return
        except Exception:
            log.exception("channel list failed")
            self.error = UNEXPECTED
            return
        finally:
            self.busy = False
        if not listed:
            self.error = "nothing in that server can be read"
            return
        for channel in listed:
            self._remember(guild, channel)
        # _save_options sets busy for its own duration, so ours is released before it runs
        await self._save_options()

    async def _open_channels(self, guild: dict[str, Any]) -> None:
        self.busy = True
        self.error = ""
        try:
            self.channel_rows = list(await self.engine.channels(guild["id"]))
            self.active_guild = guild
            self.depth = "channels"
            self.local_index = 0
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
        except Exception:
            log.exception("channel list failed")
            self.error = UNEXPECTED
        finally:
            self.busy = False

    # ---- the copy of a source server: a server the owner owns, filled by Mando (decisions 9b', 9m, 9e) ----

    def _target_of(self, guild_id: str) -> dict[str, Any] | None:
        return (self.snap.get("targets") or {}).get(guild_id)

    async def _open_targets(self, guild: dict[str, Any]) -> None:
        self.error = ""
        if not any(row.get("guild_id") == guild["id"] for row in self.picked.values()):
            self.error = "tick the server or some of its channels first"
            return
        self.busy = True
        try:
            owned = [g for g in await self.engine.owned_guilds() if g["id"] != guild["id"]]
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
            return
        except Exception:
            log.exception("owned server list failed")
            self.error = UNEXPECTED
            return
        finally:
            self.busy = False
        if not owned:
            self.error = "you own no other server, create one in Discord first"
            return
        self.targets = owned
        self.target_source = guild
        self.depth = "targets"
        link = self._target_of(guild["id"]) or {}
        self.local_index = max(0, next((at for at, g in enumerate(owned) if g["id"] == link.get("target_id")), 0))

    async def _target_key(self, key: str) -> None:
        if key == "Escape":
            self._leave_targets()
            return
        if not self.targets:
            return
        if key == "ArrowDown":
            self.local_index = (self.local_index + 1) % len(self.targets)
        elif key == "ArrowUp":
            self.local_index = (self.local_index - 1 + len(self.targets)) % len(self.targets)
        elif key == "Enter":
            await self._fill(self.targets[self.local_index])

    def _leave_targets(self) -> None:
        source = self.target_source["id"] if self.target_source else None
        self.depth = "guilds"
        self.target_source = None
        self.local_index = max(0, next((at for at, g in enumerate(self.guilds) if g["id"] == source), 0))

    async def _fill(self, target: dict[str, Any]) -> None:
        source = self.target_source or {}
        self.busy = True
        try:
            report = await self.engine.fill_copy(str(source.get("id") or ""), target["id"])
            self.refresh()
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
            # Engine.fill_copy can raise after the store holds the copy's URLs and the link (the refresh of a running
            # mirror): the screen takes the stored rows, or the next save would put the URLs from before back
            self._resync()
            return
        except Exception:
            log.exception("fill failed")
            self.error = UNEXPECTED
            self._resync()
            return
        finally:
            self.busy = False
        self._leave_targets()
        text = f"{report.get('filled', 0)} channel(s) ready in {report.get('target') or target.get('name')}"
        # a fill gives every ticked channel of the source the copy's webhook, an own one (decision 9g) or an earlier
        # copy's URL included (decision 9n); the engine notes the count in its log, which the CLI does not show, and
        # reports it, so the line shows the engine's count. The count leads, so a narrow screen that cuts a long
        # server name never cuts it
        replaced = int(report.get("replaced") or 0)
        self.error = f"{replaced} earlier webhook url(s) replaced, {text}" if replaced else text
