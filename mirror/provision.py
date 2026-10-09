from __future__ import annotations

import re
from typing import Any

_SLUG = re.compile(r"[\W_]+")
_BLOCKED = ("discord", "clyde")
_RESERVED = {"everyone", "here"}


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


def copy_layout(source_name: str, rows: list[dict], shared: bool) -> dict[str, Any]:
    """The categories and channels a fill creates in the target for the ticked rows of one source server
    (decisions 9b', 9n, 9o). The first source into a target keeps the source's own category names; a
    source filled into a target that already holds another source gets "<source> / <category>" and a
    "<source>" category for its loose channels, so two sources never mix in one category."""
    source = " ".join(str(source_name or "").split())[:100] or "server"
    categories: list[dict[str, str]] = []
    seen: set[str] = set()
    channels: list[dict[str, str]] = []
    for row in rows:
        if not row.get("enabled", True):
            continue
        channel_id = str(row.get("channel_id") or "")
        if not channel_id.isdigit():
            continue
        parent = " ".join(str(row.get("parent") or "").split())
        if shared:
            label = f"{source} / {parent}" if parent else source
        else:
            label = parent
        label = label[:100]
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            categories.append({"key": key, "name": label})
        name = " ".join(str(row.get("channel_name") or "").split())[:100] or "channel"
        topic = " ".join(str(row.get("topic") or "").split())[:1024]
        channels.append({"source_id": channel_id, "name": name, "category_key": key if label else "", "topic": topic})
    return {"categories": categories, "channels": channels}


def same_name(a: str, b: str) -> bool:
    """Whether Discord shows two text channel names the same way: it lowers the case and turns runs of
    spaces and punctuation into one dash, so a reused channel is found by that form (decision 9i). A name
    with no letter or digit matches nothing."""
    first = _slug(a, "")
    return bool(first) and first == _slug(b, "")


def _slug(value: str, blank: str = "channel") -> str:
    slug = _SLUG.sub("-", value.casefold()).strip("-")
    if not slug:
        slug = blank
    return slug[:100]


def webhook_name(value: str) -> str:
    name = " ".join(str(value or "").split())
    while True:
        folded, index = _fold(name)
        spots = [folded.find(word) for word in _BLOCKED if word in folded]
        if not spots:
            break
        cut = index[min(spots) + 2] + 1
        name = name[:cut] + "." + name[cut:]
    if name.casefold() in _RESERVED:
        name += "."
    name = name[:80].strip()
    return name or "mirror"


def _fold(name: str) -> tuple[str, list[int]]:
    parts: list[str] = []
    index: list[int] = []
    for at, char in enumerate(name):
        piece = char.casefold()
        parts.append(piece)
        index.extend([at] * len(piece))
    return "".join(parts), index
