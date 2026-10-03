from __future__ import annotations

import re
from typing import Any

_SLUG = re.compile(r"[^a-z0-9]+")


def destination_layout(selected: list[dict], dest_name: str) -> dict[str, Any]:
    name = " ".join(str(dest_name or "").split())
    if not name:
        name = "mirror"
    name = name[:100]
    kept: list[dict[str, Any]] = []
    for row in selected:
        if row.get("enabled") is False:
            continue
        channel_id = str(row.get("channel_id") or "")
        if not channel_id.isdigit():
            continue
        kept.append(row)
    groups: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in kept:
        guild_id = str(row.get("guild_id") or "")
        parent = str(row.get("parent") or "")
        key = guild_id + "|" + parent
        if key not in groups:
            guild_name = str(row.get("guild_name") or "server")
            label = guild_name if not parent else f"{guild_name} / {parent}"
            groups[key] = {"key": key, "name": label[:100], "guild_name": guild_name, "parent": parent, "rows": []}
            order.append(key)
        groups[key]["rows"].append(row)
    order.sort(key=lambda key: (groups[key]["guild_name"].casefold(), groups[key]["parent"].casefold(), key))
    used: set[str] = set()
    channels: list[dict[str, str]] = []
    for key in order:
        for row in groups[key]["rows"]:
            slug = _slug(str(row.get("channel_name") or ""))
            unique = slug
            n = 2
            while unique in used:
                suffix = f"-{n}"
                unique = (slug[: 100 - len(suffix)] + suffix)[:100]
                n += 1
            used.add(unique)
            topic = " ".join(str(row.get("topic") or "").split())
            channels.append(
                {
                    "source_id": str(row["channel_id"]),
                    "guild_id": str(row.get("guild_id") or ""),
                    "name": unique,
                    "category_key": key,
                    "topic": topic[:1024],
                }
            )
    categories = [{"key": key, "name": groups[key]["name"]} for key in order]
    return {"name": name, "categories": categories, "channels": channels}


def _slug(value: str) -> str:
    slug = _SLUG.sub("-", value.casefold()).strip("-")
    if not slug:
        slug = "channel"
    return slug[:100]
