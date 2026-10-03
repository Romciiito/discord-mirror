from __future__ import annotations

from typing import Any

VIEW_CHANNEL = 1 << 10
READ_MESSAGE_HISTORY = 1 << 16
ADMINISTRATOR = 1 << 3
TEXT_TYPES = {0, 5}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def channel_permissions(
    base: int,
    overwrites: list[dict[str, Any]] | None,
    guild_id: str,
    user_id: str,
    role_ids: list[str],
    owner: bool = False,
) -> int:
    if owner or base & ADMINISTRATOR:
        return (1 << 50) - 1
    table: dict[str, tuple[int, int]] = {}
    for row in overwrites or []:
        if isinstance(row, dict) and row.get("id") is not None:
            table[str(row["id"])] = (_int(row.get("allow")), _int(row.get("deny")))
    perms = base
    if guild_id in table:
        allow, deny = table[guild_id]
        perms = (perms & ~deny) | allow
    allow = 0
    deny = 0
    for role_id in role_ids:
        pair = table.get(str(role_id))
        if pair:
            allow |= pair[0]
            deny |= pair[1]
    perms = (perms & ~deny) | allow
    if user_id in table:
        member_allow, member_deny = table[user_id]
        perms = (perms & ~member_deny) | member_allow
    return perms


def can_read_history(perms: int) -> tuple[bool, str]:
    if not perms & VIEW_CHANNEL:
        return False, "hidden"
    if not perms & READ_MESSAGE_HISTORY:
        return False, "no history"
    return True, ""


def readable_plan(
    channels: list[dict[str, Any]],
    *,
    base: int,
    owner: bool,
    user_id: str,
    guild_id: str,
    role_ids: list[str],
) -> dict[str, Any]:
    readable: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    other = 0
    for channel in channels:
        if not isinstance(channel, dict) or channel.get("type") == 4:
            continue
        name = str(channel.get("name") or "channel")
        if channel.get("type") not in TEXT_TYPES:
            other += 1
            continue
        perms = channel_permissions(
            base,
            channel.get("permission_overwrites"),
            guild_id,
            user_id,
            role_ids,
            owner,
        )
        ok, reason = can_read_history(perms)
        if not ok:
            skipped.append({"name": name, "reason": reason})
            continue
        readable.append(channel)
    readable.sort(key=lambda item: (item.get("position") or 0, str(item.get("id") or "")))
    used = {str(item.get("parent_id")) for item in readable if item.get("parent_id")}
    categories = [
        item
        for item in channels
        if isinstance(item, dict) and item.get("type") == 4 and str(item.get("id")) in used
    ]
    categories.sort(key=lambda item: (item.get("position") or 0, str(item.get("id") or "")))
    return {"channels": readable, "categories": categories, "skipped": skipped, "other": other}
