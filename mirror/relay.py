from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from .discord_api import webhook_parts

log = logging.getLogger("mirror.relay")

BLOCKED_NAMES = {"clyde", "discord", "everyone", "here"}
CDN_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}


def author_name(message: dict[str, Any]) -> str:
    member = message.get("member") or {}
    author = message.get("author") or {}
    name = member.get("nick") or author.get("global_name") or author.get("username") or "member"
    name = " ".join(str(name).split())
    if not name:
        name = "member"
    if name.casefold() in BLOCKED_NAMES:
        name = f"{name}."
    return name[:80]


def avatar_url(message: dict[str, Any]) -> str | None:
    author = message.get("author") or {}
    avatar = author.get("avatar")
    author_id = author.get("id")
    if avatar and author_id:
        ext = "gif" if str(avatar).startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{author_id}/{avatar}.{ext}?size=128"
    return None


def clip(text: str, limit: int) -> str:
    text = text.replace("\u0000", "")
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def reply_line(message: dict[str, Any]) -> str:
    ref = message.get("referenced_message")
    if not isinstance(ref, dict):
        if message.get("message_reference"):
            return "reply"
        return ""
    who = author_name(ref)
    snippet = " ".join(str(ref.get("content") or "").split())
    if not snippet:
        if ref.get("attachments"):
            snippet = "attachment"
        elif ref.get("embeds"):
            snippet = "embed"
        else:
            snippet = "message"
    return clip(f"replying to {who}: {snippet}", 180)


def safe_embeds(message: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for embed in message.get("embeds") or []:
        if not isinstance(embed, dict):
            continue
        kind = embed.get("type") or "rich"
        if kind not in {"rich", "image", "video", "link", "article", "gifv"}:
            continue
        item: dict[str, Any] = {}
        for key, limit in (("title", 256), ("description", 4096), ("url", 2048)):
            value = embed.get(key)
            if isinstance(value, str) and value:
                item[key] = clip(value, limit)
        if isinstance(embed.get("color"), int):
            item["color"] = embed["color"]
        for key in ("author", "footer", "image", "thumbnail"):
            block = embed.get(key)
            if isinstance(block, dict):
                item[key] = block
        fields = []
        for field in (embed.get("fields") or [])[:25]:
            if not isinstance(field, dict):
                continue
            name = field.get("name")
            value = field.get("value")
            if isinstance(name, str) and isinstance(value, str):
                fields.append(
                    {
                        "name": clip(name, 256) or " ",
                        "value": clip(value, 1024) or " ",
                        "inline": bool(field.get("inline")),
                    }
                )
        if fields:
            item["fields"] = fields
        if item:
            out.append(item)
        if len(out) == 10:
            break
    return out


def sticker_names(message: dict[str, Any]) -> list[str]:
    names = []
    for sticker in message.get("sticker_items") or []:
        if isinstance(sticker, dict) and sticker.get("name"):
            names.append(str(sticker["name"])[:64])
    return names


def view_from_message(message: dict[str, Any], channel_name: str, guild_name: str) -> dict[str, Any]:
    attachments = []
    for item in message.get("attachments") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("proxy_url") or ""
        if not str(url).startswith("https://"):
            continue
        attachments.append(
            {
                "name": str(item.get("filename") or "file")[:120],
                "url": url,
                "content_type": item.get("content_type") or "",
                "size": int(item.get("size") or 0),
            }
        )
    return {
        "id": str(message.get("id") or ""),
        "channel_id": str(message.get("channel_id") or ""),
        "channel_name": channel_name,
        "guild_name": guild_name,
        "author": author_name(message),
        "avatar": avatar_url(message) or "",
        "content": str(message.get("content") or ""),
        "embeds": safe_embeds(message),
        "attachments": attachments,
        "stickers": sticker_names(message),
        "timestamp": message.get("timestamp") or "",
        "edited": message.get("edited_timestamp"),
        "deleted": False,
        "reactions": {},
        "reply": reply_line(message),
    }


def payload_for(view: dict[str, Any], prefix: bool) -> dict[str, Any]:
    lines: list[str] = []
    if prefix and view.get("channel_name"):
        guild = view.get("guild_name") or ""
        chan = view["channel_name"]
        lines.append(f"{guild} / #{chan}".strip(" /") if guild else f"#{chan}")
    if view.get("reply"):
        lines.append(str(view["reply"]))
    if view.get("content"):
        lines.append(str(view["content"]))
    if view.get("stickers"):
        lines.append("stickers: " + ", ".join(view["stickers"]))
    if view.get("deleted"):
        lines.append("(deleted)")
    content = clip("\n".join(line for line in lines if line).strip(), 2000)
    body: dict[str, Any] = {
        "content": content or None,
        "username": clip(str(view.get("author") or "member"), 80),
        "embeds": view.get("embeds") or [],
        "allowed_mentions": {"parse": []},
    }
    if view.get("avatar"):
        body["avatar_url"] = view["avatar"]
    if not body["embeds"]:
        body.pop("embeds")
    if body["content"] is None and "embeds" not in body:
        body["content"] = "(attachment)" if view.get("attachments") else "(empty)"
    return body


def host_of(url: str) -> str:
    try:
        return url.split("/", 3)[2].split(":", 1)[0].lower()
    except Exception:
        return ""


class Relay:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, url: str) -> asyncio.Lock:
        lock = self._locks.get(url)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[url] = lock
        return lock

    async def create(self, webhook_url: str, view: dict[str, Any], prefix: bool) -> str | None:
        parts = webhook_parts(webhook_url)
        if parts is None:
            return None
        async with self._lock(webhook_url):
            message_id = await self._post(webhook_url, view, prefix)
            await asyncio.sleep(0.45)
            return message_id

    async def edit(self, webhook_url: str, webhook_message_id: str, view: dict[str, Any], prefix: bool) -> None:
        if webhook_parts(webhook_url) is None:
            return
        body = payload_for(view, prefix)
        body.pop("username", None)
        body.pop("avatar_url", None)
        async with self._lock(webhook_url):
            url = f"{webhook_url.rstrip('/')}/messages/{webhook_message_id}"
            try:
                async with self.session.patch(
                    url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 429:
                        await self._sleep_limited(resp)
                    elif resp.status >= 400:
                        log.warning("webhook edit %s", resp.status)
            except Exception:
                log.exception("webhook edit failed")
            await asyncio.sleep(0.45)

    async def remove(self, webhook_url: str, webhook_message_id: str) -> None:
        if webhook_parts(webhook_url) is None:
            return
        async with self._lock(webhook_url):
            url = f"{webhook_url.rstrip('/')}/messages/{webhook_message_id}"
            try:
                async with self.session.delete(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 429:
                        await self._sleep_limited(resp)
                    elif resp.status >= 400 and resp.status != 404:
                        log.warning("webhook delete %s", resp.status)
            except Exception:
                log.exception("webhook delete failed")
            await asyncio.sleep(0.45)

    async def _post(self, webhook_url: str, view: dict[str, Any], prefix: bool) -> str | None:
        body = payload_for(view, prefix)
        files = await self._files(view.get("attachments") or [])
        url = webhook_url.rstrip("/") + "?wait=true"
        try:
            if files:
                form = aiohttp.FormData()
                form.add_field("payload_json", json.dumps(body, separators=(",", ":"), ensure_ascii=False))
                for index, (name, data, content_type) in enumerate(files):
                    form.add_field(
                        f"files[{index}]",
                        data,
                        filename=name,
                        content_type=content_type or "application/octet-stream",
                    )
                async with self.session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    return await self._message_id(resp)
            async with self.session.post(
                url,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                return await self._message_id(resp)
        except Exception:
            log.exception("webhook post failed")
            return None

    async def _message_id(self, resp: aiohttp.ClientResponse) -> str | None:
        if resp.status == 429:
            await self._sleep_limited(resp)
            return None
        if resp.status >= 400:
            log.warning("webhook post %s", resp.status)
            return None
        try:
            data = await resp.json(content_type=None)
        except Exception:
            return None
        if isinstance(data, dict) and data.get("id"):
            return str(data["id"])
        return None

    async def _sleep_limited(self, resp: aiohttp.ClientResponse) -> None:
        try:
            body = await resp.json(content_type=None)
            delay = float(body.get("retry_after", 1))
        except Exception:
            delay = 1.0
        await asyncio.sleep(min(delay, 20))

    async def _files(self, attachments: list[dict[str, Any]]) -> list[tuple[str, bytes, str]]:
        found: list[tuple[str, bytes, str]] = []
        for item in attachments[:4]:
            url = str(item.get("url") or "")
            if host_of(url) not in CDN_HOSTS:
                continue
            size = int(item.get("size") or 0)
            if size and size > 8_000_000:
                continue
            try:
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.content.read(8_000_001)
            except Exception:
                continue
            if len(data) > 8_000_000 or not data:
                continue
            found.append((str(item.get("name") or "file")[:80], data, str(item.get("content_type") or "")))
        return found

