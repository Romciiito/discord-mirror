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


def _plain(text: str) -> str:
    return text.translate(BREAKS)


def status_line(ui: Any) -> str:
    who = _display(ui.snap.get("user") or {}) or "signed out"
    state = "running" if ui.snap.get("running") else "stopped"
    return f"{who} · {state} · {int(ui.snap.get('mirrored') or 0)} mirrored · {ui.error or '-'}"


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


def _body(ui: Any, height: int) -> list[str]:
    screen = ui.flow["screen"]
    lines: list[str] = []
    if screen == "welcome":
        return ["Discord smart scraper - Mando", "Press any key to continue"]
    if screen == "exit":
        return ["Stopped", "Press any key to continue"]
    if screen == "running":
        lines.append("mirroring" if ui.snap.get("running") else "working")
        lines.extend(feed_lines(ui.messages, max(0, height - 4)))
        return lines
    if screen in ("menu", "settings"):
        lines.append(screen)
        for at, item in enumerate(items(screen, ui.ctx())):
            label = f"{at + 1}  {item['label']}" + ("  locked" if item.get("locked") else "")
            lines.append(_row(label, at == ui.flow["index"]))
        return lines + ([ui.error] if ui.error else [])
    if screen == "token":
        lines.append("Add token")
        for at, key in enumerate(TOKEN_ROWS):
            label = TOKEN_LABELS[key]
            if ui.typing == key:
                shown = "*" * len(ui.draft) if key == "token" else ui.draft
                lines.append(_row(f"{label} {shown}", at == ui.token_index))
                continue
            extra = ""
            if key == "token" and ui.fields.get("token"):
                extra = "  set"
            elif key == "keep":
                extra = "  yes" if ui.fields.get("keep") else "  no"
            elif key in ("service", "account") and ui.fields.get(key):
                extra = "  " + str(ui.fields[key])
            lines.append(_row(f"{at + 1}  {label}{extra}", at == ui.token_index))
        if ui.token_check:
            lines.append(ui.token_check)
        return lines + ([ui.error] if ui.error else [])
    if screen == "webhooks":
        options = ui.snap.get("options") or {}
        lines.append("Webhook settings")
        labels = {
            "backfill": f"backfill  {_backfill_label(options.get('backfill'))}",
            "threads": f"threads  {'on' if options.get('include_threads') else 'off'}",
            "back": "back",
        }
        for at, key in enumerate(HOOK_ROWS):
            lines.append(_row(f"{at + 1}  {labels[key]}", at == ui.hook_index))
        return lines + ([ui.error] if ui.error else [])
    if screen == "servers":
        if ui.depth == "channels" and ui.active_guild:
            lines.append(str(ui.active_guild.get("name") or "server"))
            if not ui.channel_rows:
                lines.append("nothing in that server can be read")
            for at, channel in enumerate(ui.channel_rows):
                row = ui.picked.get(channel.get("id"))
                mark = "[x] " if row else "[ ] "
                parent = f"{channel['parent']} / " if channel.get("parent") else ""
                label = f"{mark}{parent}#{channel.get('name')}"
                if ui.typing == "webhook" and ui.webhook_channel and ui.webhook_channel.get("id") == channel.get("id"):
                    label += f"  webhook: {ui.draft}"
                elif row and row.get("webhook_url"):
                    label += "  webhook set"
                elif row:
                    label += "  no webhook"
                lines.append(_row(label, at == ui.local_index))
        else:
            lines.append("Select servers")
            if not ui.guilds:
                lines.append("no servers")
            selected = {row.get("guild_id") for row in ui.picked.values()}
            for at, guild in enumerate(ui.guilds):
                mark = "[x] " if guild.get("id") in selected else "[ ] "
                lines.append(_row(f"{mark}{guild.get('name')}", at == ui.local_index))
        return lines + ([ui.error] if ui.error else [])
    return ["Press any key to continue"]


def render(ui: Any, width: int, height: int) -> list[str]:
    """Exactly `height` lines of at most `width` characters each, none of them with a line break."""
    width = max(0, int(width))
    height = max(0, int(height))
    lines = [status_line(ui)]
    if height >= 2:
        body = _body(ui, height)
        room = height - 2
        lines.extend(body[:room])
        lines.extend([""] * (room - min(len(body), room)))
        lines.append(ui.hint())
    return [_plain(line[:width]) for line in lines[:height]]
