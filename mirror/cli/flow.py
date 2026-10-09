"""Navigation reducer of the CLI: a one-to-one port of the old browser page's flow.js."""
from __future__ import annotations

from typing import Any

MENU = [
    {"id": "start", "label": "Start/Resume mirror"},
    {"id": "settings", "label": "Settings"},
    {"id": "exit", "label": "Exit"},
]
KNOWN = {"welcome", "menu", "settings", "token", "webhooks", "servers", "exit"}
FORMS = {"token", "webhooks", "servers"}


def items(screen: str, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    token_ok = bool(ctx and ctx.get("tokenOk"))
    if screen == "menu":
        return [dict(item) for item in MENU]
    if screen == "settings":
        return [
            {"id": "token", "label": "Add token"},
            {"id": "servers", "label": "Select servers", "locked": not token_ok},
            {"id": "webhooks", "label": "Webhook settings"},
            {"id": "interface", "label": "Interface: UI (unavailable now)", "locked": True},
            {"id": "back", "label": "Back to menu"},
        ]
    return []


def _pack(screen: str, index: int, action: str | None, locked: bool) -> dict[str, Any]:
    return {"screen": screen, "index": index, "action": action, "locked": locked}


def _activate(item: dict[str, Any], index: int) -> dict[str, Any]:
    target = item["id"]
    if target == "start":
        return _pack("menu", index, "start", False)
    if target in ("settings", "exit", "token", "servers", "webhooks"):
        return _pack(target, 0, target, False)
    if target == "back":
        return _pack("menu", 0, "back", False)
    return _pack("menu", index, target, False)


def reduce(state: dict[str, Any] | None, event: dict[str, Any] | None = None, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    if state is None:
        return _pack("welcome", 0, None, False)
    screen = state.get("screen")
    index = int(state.get("index") or 0)
    if screen not in KNOWN:
        screen, index = "welcome", 0
    key = event.get("key") if event else None
    if screen == "welcome":
        if key == "Escape":
            return _pack("welcome", index, None, False)
        return _pack("menu", 0, None, False)
    if key == "Escape":
        if screen in FORMS:
            return _pack("settings", 0, None, False)
        if screen in ("settings", "exit"):
            return _pack("menu", 0, None, False)
        return _pack(screen, index, None, False)
    if screen in FORMS or screen == "exit":
        return _pack(screen, index, None, False)
    listed = items(screen, ctx)
    count = len(listed)
    if key == "ArrowDown":
        return _pack(screen, (index + 1) % count, None, False)
    if key == "ArrowUp":
        return _pack(screen, (index - 1 + count) % count, None, False)
    if isinstance(key, str) and len(key) == 1 and "1" <= key <= "9":
        pick = int(key) - 1
        if pick >= count:
            return _pack(screen, index, None, False)
        item = listed[pick]
        if item.get("locked"):
            return _pack(screen, pick, None, True)
        return _activate(item, pick)
    if key == "Enter":
        if index >= count:
            return _pack(screen, index, None, False)
        item = listed[index]
        if item.get("locked"):
            return _pack(screen, index, None, True)
        return _activate(item, index)
    return _pack(screen, index, None, False)
