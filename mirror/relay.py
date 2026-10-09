from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

import aiohttp

from .discord_api import webhook_parts

log = logging.getLogger("mirror.relay")

BLOCKED_WORDS = ("discord", "clyde")
BLOCKED_NAMES = {"everyone", "here"}
CDN_HOSTS = {"cdn.discordapp.com", "media.discordapp.net"}
UPLOAD_FILES = 10
UPLOAD_LIMIT = 20 * 1024 * 1024  # per file, the documented default (decision 10b)
OVER_LIMIT = "This message has a file over the upload limit"
ATTEMPTS = 5
RETRY_CAP = 60.0
PACE = 0.5
TEXT_FLOOR = 64
JSON_HEADERS = {"Content-Type": "application/json"}
EMBED_TOTAL = 6000
STICKER_PNG = 1
STICKER_APNG = 2
STICKER_LOTTIE = 3
STICKER_GIF = 4


def safe_name(name: str) -> str:
    name = " ".join(str(name).split())
    while True:
        folded = ""
        owner: list[int] = []
        for index, char in enumerate(name):
            part = char.casefold()
            folded += part
            owner.extend([index] * len(part))
        hits = [spot for spot in (folded.find(word) for word in BLOCKED_WORDS) if spot >= 0]
        if not hits:
            break
        cut = owner[min(hits) + 2] + 1
        name = f"{name[:cut]}.{name[cut:]}"
    if name.casefold() in BLOCKED_NAMES:
        name = f"{name}."
    return name[:80].strip() or "member"


def author_name(message: dict[str, Any]) -> str:
    member = message.get("member") or {}
    author = message.get("author") or {}
    name = member.get("nick") or author.get("global_name") or author.get("username") or "member"
    return safe_name(str(name))


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


def embed_chars(item: dict[str, Any]) -> int:
    count = len(item.get("title") or "") + len(item.get("description") or "")
    for field in item.get("fields") or []:
        count += len(field.get("name") or "") + len(field.get("value") or "")
    footer = item.get("footer") or {}
    author = item.get("author") or {}
    return count + len(str(footer.get("text") or "")) + len(str(author.get("name") or ""))


def safe_embeds(message: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    total = 0
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
            # the title, description, field, footer and author texts of all embeds share 6000 characters
            used = embed_chars(item)
            if total + used > EMBED_TOTAL:
                break
            total += used
            out.append(item)
        if len(out) == 10:
            break
    return out


def sticker_names(message: dict[str, Any]) -> list[str]:
    """Stickers that have no image to send: Lottie (decision 10a)."""
    names = []
    for sticker in message.get("sticker_items") or []:
        if isinstance(sticker, dict) and sticker.get("name") and sticker.get("format_type") == STICKER_LOTTIE:
            names.append(str(sticker["name"])[:64])
    return names


def sticker_files(message: dict[str, Any]) -> list[dict[str, Any]]:
    """PNG, APNG and GIF stickers as image files from the CDN, uploaded like attachments (decision 10a)."""
    files: list[dict[str, Any]] = []
    for sticker in message.get("sticker_items") or []:
        if not isinstance(sticker, dict) or not sticker.get("id"):
            continue
        kind = sticker.get("format_type")
        name = str(sticker.get("name") or "sticker")[:64]
        if kind in (STICKER_PNG, STICKER_APNG):
            url, ext, mime = f"https://cdn.discordapp.com/stickers/{sticker['id']}.png", "png", "image/png"
        elif kind == STICKER_GIF:
            url, ext, mime = f"https://media.discordapp.net/stickers/{sticker['id']}.gif", "gif", "image/gif"
        else:
            continue
        files.append({"name": f"{name}.{ext}", "url": url, "content_type": mime, "size": 0, "sticker": True})
    return files


def view_from_message(
    message: dict[str, Any], channel_name: str, guild_name: str, guild_id: str = ""
) -> dict[str, Any]:
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
    attachments.extend(sticker_files(message))
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
        "guild_id": str(message.get("guild_id") or guild_id or ""),
    }


def host_of(url: str) -> str:
    try:
        return url.split("/", 3)[2].split(":", 1)[0].lower()
    except Exception:
        return ""


def size_of(item: dict[str, Any]) -> int:
    try:
        return int(item.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def plan_uploads(attachments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    upload: list[dict[str, Any]] = []
    linked: list[dict[str, Any]] = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        size = size_of(item)
        # a sticker's size is unknown (0); _fetch holds its body to the limit
        if (
            host_of(str(item.get("url") or "")) in CDN_HOSTS
            and (0 < size <= UPLOAD_LIMIT or (size == 0 and item.get("sticker")))
            and len(upload) < UPLOAD_FILES
        ):
            upload.append(item)
        else:
            linked.append(item)
    return upload, linked


def oversize(item: dict[str, Any]) -> bool:
    return size_of(item) > UPLOAD_LIMIT


def message_link(view: dict[str, Any]) -> str:
    guild = str(view.get("guild_id") or "") or "@me"
    return f"https://discord.com/channels/{guild}/{view.get('channel_id')}/{view.get('id')}"


def oversize_lines(view: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in view.get("attachments") or []:
        if isinstance(item, dict) and oversize(item):
            size = f"{size_of(item) / 1048576:.1f} MiB"
            lines.append(f"{OVER_LIMIT}: {item.get('name') or 'file'} ({size})")
            lines.append(message_link(view))
            if item.get("url"):
                lines.append(str(item["url"]))
    return lines


def link_urls(view: dict[str, Any]) -> list[str]:
    links = view.get("links")
    if not isinstance(links, list):
        links = [item.get("url") for item in plan_uploads(view.get("attachments") or [])[1] if not oversize(item)]
    return [str(url) for url in links if url]


def fit_content(text: str, urls: list[str]) -> str:
    keep = list(urls)
    floor = min(len(text), TEXT_FLOOR) + 1 if text else 0
    while keep and len("\n".join(keep)) + floor > 2000:
        keep.pop()
    block = "\n".join(keep)
    if not block:
        return clip(text, 2000)
    if not text:
        return block
    return clip(text, 2000 - len(block) - 1) + "\n" + block


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
    text = "\n".join(line for line in lines if line).strip()
    # the over-limit lines lead the block fit_content keeps, so a long message cannot clip a file away
    content = fit_content(text, oversize_lines(view) + link_urls(view))
    body: dict[str, Any] = {
        "content": content or None,
        "username": safe_name(str(view.get("author") or "")),
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


def too_large(status: int, data: Any) -> bool:
    return status == 413 or (isinstance(data, dict) and data.get("code") == 40005)


def header_float(headers: Any, key: str) -> float | None:
    try:
        value = headers.get(key)
        return None if value is None else float(value)
    except (TypeError, ValueError, AttributeError):
        return None


def retry_delay(headers: Any, data: Any) -> float:
    delay = None
    if isinstance(data, dict):
        try:
            delay = float(data["retry_after"]) if data.get("retry_after") is not None else None
        except (TypeError, ValueError):
            delay = None
    if delay is None:
        delay = header_float(headers, "Retry-After")
    if delay is None or delay != delay or delay < 0:
        delay = 1.0
    return min(delay, RETRY_CAP)


class Relay:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session
        self._locks: dict[str, asyncio.Lock] = {}
        self._next: dict[str, float] = {}

    def _lock(self, url: str) -> asyncio.Lock:
        lock = self._locks.get(url)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[url] = lock
        return lock

    async def _wait(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    def _hold(self, webhook_url: str, seconds: float) -> None:
        self._next[webhook_url] = max(self._next.get(webhook_url, 0.0), time.monotonic() + seconds)

    def _pace(self, webhook_url: str, headers: Any) -> None:
        remaining = header_float(headers, "X-RateLimit-Remaining")
        reset = header_float(headers, "X-RateLimit-Reset-After")
        gap = PACE
        if remaining is not None and remaining <= 0 and reset is not None and reset == reset:
            gap = max(PACE, min(reset, RETRY_CAP))
        self._next[webhook_url] = time.monotonic() + gap

    async def _send(
        self,
        webhook_url: str,
        method: str,
        url: str,
        make: Callable[[], dict[str, Any]],
        timeout: float,
    ) -> tuple[int, Any]:
        status = 0
        backoff = 1.0
        for attempt in range(ATTEMPTS):
            gap = min(self._next.get(webhook_url, 0.0) - time.monotonic(), RETRY_CAP)
            if gap > 0:
                await self._wait(gap)
            try:
                call = getattr(self.session, method)
                async with call(url, timeout=aiohttp.ClientTimeout(total=timeout), **make()) as resp:
                    status = resp.status
                    try:
                        data = await resp.json(content_type=None)
                    except Exception:
                        data = None
                    self._pace(webhook_url, resp.headers)
                    if status == 429:
                        self._hold(webhook_url, retry_delay(resp.headers, data))
                        continue
                    if status < 500:
                        return status, data
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
                log.info("webhook %s attempt %s failed: %s", method, attempt + 1, exc)
            self._hold(webhook_url, backoff)
            backoff = min(backoff * 2, 8.0)
        log.warning("webhook %s gave up after %s attempts (last status %s)", method, ATTEMPTS, status)
        return status, None

    async def create(self, webhook_url: str, view: dict[str, Any], prefix: bool) -> str | None:
        if webhook_parts(webhook_url) is None:
            return None
        try:
            async with self._lock(webhook_url):
                return await self._post(webhook_url, view, prefix)
        except Exception:
            log.exception("webhook post failed")
            return None

    async def edit(self, webhook_url: str, webhook_message_id: str, view: dict[str, Any], prefix: bool) -> None:
        if webhook_parts(webhook_url) is None:
            return
        try:
            body = payload_for(view, prefix)
            body["embeds"] = view.get("embeds") or []  # always on an edit, so removed embeds go too (issue #6)
            body.pop("username", None)
            body.pop("avatar_url", None)
            url = f"{webhook_url.rstrip('/')}/messages/{webhook_message_id}"
            async with self._lock(webhook_url):
                status, _ = await self._send(
                    webhook_url, "patch", url, lambda: {"json": body, "headers": dict(JSON_HEADERS)}, 30
                )
            if status == 404:
                log.info("webhook edit 404")
            elif 400 <= status < 500 and status != 429:
                log.warning("webhook edit %s", status)
        except Exception:
            log.exception("webhook edit failed")

    async def remove(self, webhook_url: str, webhook_message_id: str) -> None:
        if webhook_parts(webhook_url) is None:
            return
        try:
            url = f"{webhook_url.rstrip('/')}/messages/{webhook_message_id}"
            async with self._lock(webhook_url):
                status, _ = await self._send(webhook_url, "delete", url, dict, 30)
            if 400 <= status < 500 and status not in (404, 429):
                log.warning("webhook delete %s", status)
        except Exception:
            log.exception("webhook delete failed")

    async def _post(self, webhook_url: str, view: dict[str, Any], prefix: bool) -> str | None:
        attachments = [item for item in view.get("attachments") or [] if isinstance(item, dict)]
        upload, _ = plan_uploads(attachments)
        files, failed = await self._files(upload)
        sent = {id(item) for item in upload} - {id(item) for item in failed}
        links = [
            str(item.get("url"))
            for item in attachments
            if id(item) not in sent and item.get("url") and not oversize(item)
        ]
        url = webhook_url.rstrip("/") + "?wait=true"
        body = payload_for({**view, "links": links}, prefix)
        plain = not files
        status, data = 0, None
        if files:
            text = json.dumps(body, separators=(",", ":"), ensure_ascii=False)

            def make() -> dict[str, Any]:
                form = aiohttp.FormData()
                form.add_field("payload_json", text)
                for index, (name, blob, content_type) in enumerate(files):
                    form.add_field(
                        f"files[{index}]",
                        blob,
                        filename=name,
                        content_type=content_type or "application/octet-stream",
                    )
                return {"data": form}

            status, data = await self._send(webhook_url, "post", url, make, 120)
            if too_large(status, data):
                log.info("webhook upload too large, sending links")
                links = [str(item.get("url")) for item in attachments if item.get("url") and not oversize(item)]
                body = payload_for({**view, "links": links}, prefix)
                plain = True
        if plain:
            status, data = await self._send(
                webhook_url, "post", url, lambda: {"json": body, "headers": dict(JSON_HEADERS)}, 30
            )
        view["links"] = links
        if 200 <= status < 300 and isinstance(data, dict) and data.get("id"):
            return str(data["id"])
        if 400 <= status < 500 and status != 429:
            log.warning("webhook post %s", status)
        return None

    async def _files(
        self, attachments: list[dict[str, Any]]
    ) -> tuple[list[tuple[str, bytes, str]], list[dict[str, Any]]]:
        files: list[tuple[str, bytes, str]] = []
        failed: list[dict[str, Any]] = []
        for item in attachments:
            data = None
            if len(files) < UPLOAD_FILES:
                data = await self._fetch(str(item.get("url") or ""), size_of(item))
            if data is None:
                failed.append(item)
                continue
            files.append((str(item.get("name") or "file")[:80], data, str(item.get("content_type") or "")))
        return files, failed

    async def _fetch(self, url: str, size: int) -> bytes | None:
        if host_of(url) not in CDN_HOSTS or size > UPLOAD_LIMIT:
            return None
        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    return None
                declared = header_float(resp.headers, "Content-Length")
                if declared is not None and declared > UPLOAD_LIMIT:
                    return None
                data = await resp.read()
        except Exception:
            return None
        if not data or len(data) > UPLOAD_LIMIT:
            return None
        return bytes(data)
