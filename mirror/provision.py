from __future__ import annotations

from typing import Any

_BLOCKED = ("discord", "clyde")
_RESERVED = {"everyone", "here"}


def copy_layout(source_name: str, rows: list[dict], shared: bool) -> dict[str, Any]:
    """The categories and channels a fill creates in the target for the ticked rows of one source server
    (decisions 9b', 9n, 9o). `shared` is false for the first source filled into a target, which keeps the
    source's own category names, and true for a source filled into a target another source was filled into
    first, which gets "<source> / <category>" and a "<source>" category for its loose channels. The names alone
    do not keep two sources apart (server names are not unique, and "<source>" can be a category of the first
    source), so the caller uses a category only for the source a fill made it for (`Store.record_category`).
    The caller keeps the flag in the store's record of the sources a target holds (`Store.record_fill`), so a
    refill gets the layout of the first fill even after a second source arrived."""
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
        channel: dict[str, Any] = {
            "source_id": channel_id,
            "name": name,
            "category_key": key if label else "",
            "topic": topic,
        }
        if row.get("nsfw"):
            channel["nsfw"] = True  # an age-restricted source channel: its copy is created with the age gate
        channels.append(channel)
    return {"categories": categories, "channels": channels}


def same_name(a: str, b: str) -> bool:
    """Whether two text channel names name the same channel for a fill (decision 9i): equal once the case is
    folded and each run of whitespace is written as one dash. Every other character counts, so emoji,
    separators such as "┃" and underscores keep two channels apart; the names come from Discord's own
    channel list, so a channel a fill created carries the source's name again. An empty name matches
    nothing."""
    first = _shown(a)
    return bool(first) and first == _shown(b)


def _shown(value: str) -> str:
    return "-".join(str(value or "").casefold().split())


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
