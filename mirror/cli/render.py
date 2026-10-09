"""Pure renderer of the CLI: controller state plus the engine snapshot in, lines out."""
from __future__ import annotations

from typing import Any

from .controller import BACKFILL, HOOK_ROWS, TOKEN_ROWS, _display
from .flow import items

TOKEN_LABELS = {
    "token": "token", "keep": "keep on this machine", "save": "save token",
    "service": "keychain service", "account": "keychain account", "keychain": "read keychain", "back": "back",
}
# control characters (Unicode Cc) and the line and paragraph separators: the terminal layer joins
# the lines with "\n", so one of these inside an error, a name or a message would break a line
BREAKS = dict.fromkeys([*range(0x20), *range(0x7F, 0xA0), 0x2028, 0x2029], " ")

# a screen body: title lines, list rows, the row the window must keep in view, notes under the list
Body = tuple[list[str], list[str], int, list[str]]


def _plain(text: str) -> str:
    return text.translate(BREAKS)


def status_line(ui: Any) -> str:
    who = _display(ui.snap.get("user") or {}) or "signed out"
    state = "running" if ui.snap.get("running") else "stopped"
    last = ui.error or ui.engine_error or "-"
    return f"{who} · {state} · {int(ui.snap.get('mirrored') or 0)} mirrored · {last}"


def feed_lines(messages: list[dict[str, Any]], count: int) -> list[str]:
    out: list[str] = []
    for message in messages[-count:] if count > 0 else []:
        where = f"#{message.get('channel_name') or '?'}"
        if message.get("guild_name"):
            where = f"{message['guild_name']} / {where}"
        text = str(message.get("content") or "") or ("(attachment)" if message.get("attachments") else "(empty)")
        line = f"{where} · {message.get('author') or '?'}: {text}"
        if message.get("edited"):
            line += " (edited)"
        if message.get("deleted"):
            line += " (deleted)"
        out.append(_plain(line))
    return out


def _row(label: str, on: bool) -> str:
    return ("> " if on else "  ") + label


def _backfill_label(value: Any) -> str:
    amount = int(value or 0)
    return f"last {amount}" if amount else "live only"


def _notes(ui: Any) -> list[str]:
    return [ui.error] if ui.error else []


def _body(ui: Any, height: int) -> Body:
    screen = ui.flow["screen"]
    rows: list[str] = []
    if screen == "welcome":
        return ["Discord smart scraper - Mando", "Press any key to continue"], [], 0, []
    if screen == "exit":
        return ["Stopped", "Press any key to continue"], [], 0, []
    if screen == "running":
        rows = feed_lines(ui.messages, max(0, height - 4))
        return ["mirroring" if ui.snap.get("running") else "working"], rows, len(rows) - 1, []
    if screen in ("menu", "settings"):
        for at, item in enumerate(items(screen, ui.ctx())):
            label = f"{at + 1}  {item['label']}" + ("  locked" if item.get("locked") else "")
            rows.append(_row(label, at == ui.flow["index"]))
        return [screen], rows, ui.flow["index"], _notes(ui)
    if screen == "token":
        for at, key in enumerate(TOKEN_ROWS):
            label = TOKEN_LABELS[key]
            if ui.typing == key:
                shown = "*" * len(ui.draft) if key == "token" else ui.draft
                rows.append(_row(f"{label} {shown}", at == ui.token_index))
                continue
            extra = ""
            if key == "token" and ui.fields.get("token"):
                extra = "  set"
            elif key == "keep":
                extra = "  yes" if ui.fields.get("keep") else "  no"
            elif key in ("service", "account") and ui.fields.get(key):
                extra = "  " + str(ui.fields[key])
            rows.append(_row(f"{at + 1}  {label}{extra}", at == ui.token_index))
        focus = TOKEN_ROWS.index(ui.typing) if ui.typing in TOKEN_ROWS else ui.token_index
        return ["Add token"], rows, focus, ([ui.token_check] if ui.token_check else []) + _notes(ui)
    if screen == "webhooks":
        options = ui.snap.get("options") or {}
        labels = {
            "backfill": f"backfill  {_backfill_label(options.get('backfill'))}",
            "threads": f"threads  {'on' if options.get('include_threads') else 'off'}",
            "back": "back",
        }
        for at, key in enumerate(HOOK_ROWS):
            rows.append(_row(f"{at + 1}  {labels[key]}", at == ui.hook_index))
        return ["Webhook settings"], rows, ui.hook_index, _notes(ui)
    if screen == "servers":
        focus = ui.local_index
        if ui.depth == "channels" and ui.active_guild:
            title = str(ui.active_guild.get("name") or "server")
            if not ui.channel_rows:
                rows.append("nothing in that server can be read")
            for at, channel in enumerate(ui.channel_rows):
                row = ui.picked.get(channel.get("id"))
                mark = "[x] " if row else "[ ] "
                parent = f"{channel['parent']} / " if channel.get("parent") else ""
                label = f"{mark}{parent}#{channel.get('name')}"
                if ui.typing == "webhook" and ui.webhook_channel and ui.webhook_channel.get("id") == channel.get("id"):
                    label += f"  webhook: {ui.draft}"
                    focus = at
                elif row and row.get("webhook_url"):
                    label += "  webhook set"
                elif row:
                    label += "  no webhook"
                rows.append(_row(label, at == ui.local_index))
        elif ui.depth == "targets" and ui.target_source:
            title = f"Copy of {ui.target_source.get('name') or 'server'} into"
            link = (ui.snap.get("targets") or {}).get(ui.target_source.get("id")) or {}
            for at, target in enumerate(ui.targets):
                mark = "[x] " if target.get("id") == link.get("target_id") else "[ ] "
                rows.append(_row(f"{mark}{target.get('name')}", at == ui.local_index))
        else:
            title = "Select servers"
            if not ui.guilds:
                rows.append("no servers")
            selected = {row.get("guild_id") for row in ui.picked.values()}
            links = ui.snap.get("targets") or {}
            for at, guild in enumerate(ui.guilds):
                mark = "[x] " if guild.get("id") in selected else "[ ] "
                label = f"{mark}{guild.get('name')}"
                link = links.get(guild.get("id"))
                if link:
                    label += f"  copy: {link.get('target_name') or link.get('target_id')}"
                rows.append(_row(label, at == ui.local_index))
        return [title], rows, focus, _notes(ui)
    return ["Press any key to continue"], [], 0, []


def _fit(body: Body, room: int) -> list[str]:
    """At most `room` lines. A list taller than the room is cut to a window around the focus row, so
    the cursor row (or the row being typed into) is always drawn; when the room is too short even
    for the title, the notes and one row, the focus row wins over the notes, and the notes over the title."""
    head, rows, focus, notes = body
    if room <= 0:
        return []
    shown = min(len(rows), max(1, room - len(head) - len(notes))) if rows else 0
    notes = notes[: room - shown]
    head = head[: room - shown - len(notes)]
    focus = max(0, min(focus, len(rows) - 1))
    start = max(0, min(focus - shown // 2, len(rows) - shown))
    return head + rows[start : start + shown] + notes


def render(ui: Any, width: int, height: int) -> list[str]:
    """Exactly `height` lines of at most `width` characters each, none of them with a line break."""
    width = max(0, int(width))
    height = max(0, int(height))
    lines = [status_line(ui)]
    if height >= 2:
        room = height - 2
        body = _fit(_body(ui, height), room)
        lines.extend(body)
        lines.extend([""] * (room - len(body)))
        lines.append(ui.hint())
    return [_plain(line[:width]) for line in lines[:height]]
