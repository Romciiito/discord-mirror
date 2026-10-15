from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import uuid
from typing import Any

import aiohttp

log = logging.getLogger("mirror.api")

API = "https://discord.com/api/v9"
CAPABILITIES = 22525
BUILD_RE = re.compile(r'"BUILD_NUMBER"\s*:\s*"(\d+)"')
CLOUDFLARE_RE = re.compile(r"error code:\s*(\d+)", re.IGNORECASE)  # Cloudflare's block and rate-limit pages
WEBHOOK_RE = re.compile(
    r"^https://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api/webhooks/(\d+)/([\w-]+)/?$"
)
LAUNCH_MASK = (
    0b00000000100000000001000000010000000010000001000000001000000000000010000010000001000000000100000000000001000000000000100000000000
)


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.payload = payload or {}  # Discord's JSON error object, when Discord sent one

    @property
    def reason(self) -> str:
        """Discord's own words for the refusal: `403: <message> (code <n>)`."""
        message = str(self.payload.get("message") or "").strip()[:160]
        if not message:
            reason = f"HTTP {self.status}"
        elif message.startswith(f"{self.status}:"):
            reason = message
        else:
            reason = f"{self.status}: {message}"
        code = self.payload.get("code")
        return f"{reason} (code {code})" if code else reason


class NotDiscordAnswer(ApiError):
    """An answer that is not Discord's JSON, such as a Cloudflare block page: Discord itself was not reached."""

    def __init__(self, status: int, detail: str, cloudflare: str = "") -> None:
        super().__init__(status, detail)
        self.cloudflare = cloudflare

    @property
    def reason(self) -> str:
        return _foreign_reason(self.status, self.cloudflare)


def _foreign_reason(status: int, cloudflare: str) -> str:
    if cloudflare:
        return f"HTTP {status}, Cloudflare error code {cloudflare}"
    return f"HTTP {status}, not a Discord answer"


def launch_signature() -> str:
    bits = uuid.uuid4().int & (~LAUNCH_MASK & ((1 << 128) - 1))
    return str(uuid.UUID(int=bits))


def build_properties(build: int, chrome_major: int) -> dict[str, Any]:
    major = str(chrome_major)
    return {
        "os": "Mac OS X",
        "browser": "Chrome",
        "device": "",
        "system_locale": "en-US",
        "browser_user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36"
        ),
        "browser_version": f"{major}.0.0.0",
        "os_version": "10.15.7",
        "referrer": "",
        "referring_domain": "",
        "referrer_current": "",
        "referring_domain_current": "",
        "release_channel": "stable",
        "client_build_number": int(build),
        "client_event_source": None,
        "has_client_mods": False,
        "client_launch_id": str(uuid.uuid4()),
        "client_heartbeat_session_id": str(uuid.uuid4()),
        "client_app_state": "unfocused",
        "launch_signature": launch_signature(),
        "is_fast_connect": False,
        "gateway_connect_reasons": "AppSkeleton",
    }


def super_property_header(properties: dict[str, Any]) -> str:
    raw = json.dumps(properties, separators=(",", ":"), ensure_ascii=False).encode()
    return base64.b64encode(raw).decode()


def webhook_parts(url: str) -> tuple[str, str] | None:
    match = WEBHOOK_RE.match((url or "").strip())
    if not match:
        return None
    return match.group(1), match.group(2)


def clean_webhook(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    url = url.split("#", 1)[0].split("?", 1)[0]
    if url.endswith("/"):
        url = url[:-1]
    if webhook_parts(url) is None:
        raise ApiError(400, "webhook url is not a discord incoming webhook")
    return url


def clean_token(raw: str) -> str:
    raw = raw.strip()
    for left, right in ('""', "''", "“”"):
        if raw.startswith(left) and raw.endswith(right):
            return raw[1:-1].strip()
    return raw


class DiscordHTTP:
    def __init__(self, session: aiohttp.ClientSession, token: str, properties: dict[str, Any]) -> None:
        self.session = session
        self.token = token
        self.properties = properties
        self.init_ms = 0

    def headers(self, *, json_body: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Authorization": self.token,
            "Origin": "https://discord.com",
            "Referer": "https://discord.com/channels/@me",
            "User-Agent": self.properties["browser_user_agent"],
            "X-Discord-Locale": "en-US",
            "X-Super-Properties": super_property_header(self.properties),
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    async def call(self, method: str, path: str, **kwargs: Any) -> Any:
        _status, data = await self._answer(method, path, **kwargs)
        return data

    async def _answer(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        url = path if path.startswith("https://") else f"{API}{path}"
        headers = self.headers(json_body="json" in kwargs)
        extra = kwargs.pop("headers", None)
        if extra:
            headers.update(extra)
        last = ""  # the body of the last 429, named when the request stays rate limited
        for _ in range(5):
            async with self.session.request(method, url, headers=headers, **kwargs) as resp:
                if resp.status == 429:
                    last = ""
                    try:
                        last = await resp.text()
                        delay = float(json.loads(last).get("retry_after", 1))
                    except Exception:
                        delay = 1.0
                    await asyncio.sleep(min(delay, 30))
                    continue
                text = await resp.text()
                if resp.status >= 400:
                    raise _refusal(resp.status, _fail_detail(method, path, resp.status, text), text)
                if not text:
                    return resp.status, None
                try:
                    return resp.status, json.loads(text)
                except json.JSONDecodeError:
                    return resp.status, text
        code = cloudflare_code(last)
        raise _refusal(429, f"{method} {path} stayed rate limited" + (f" (Cloudflare error code {code})" if code else ""), last)

    async def me(self) -> dict[str, Any]:
        status, data = await self._answer("GET", "/users/@me")
        if not isinstance(data, dict) or "id" not in data:
            # a 2xx without the user, such as a block page served as 200: not a refusal of the token
            code = cloudflare_code(data) if isinstance(data, str) else ""
            raise NotDiscordAnswer(status, f"GET /users/@me failed ({_foreign_reason(status, code)})", code)
        return data

    async def guilds(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        after = None
        while True:
            query = "?limit=200" if after is None else f"?limit=200&after={after}"
            page = await self.call("GET", f"/users/@me/guilds{query}")
            if not isinstance(page, list) or not page:
                break
            found.extend(page)
            if len(page) < 200:
                break
            after = page[-1]["id"]
        return found

    async def channels(self, guild_id: str) -> list[dict[str, Any]]:
        data = await self.call("GET", f"/guilds/{guild_id}/channels")
        if not isinstance(data, list):
            return []
        return data

    async def history(self, channel_id: str, limit: int) -> list[dict[str, Any]]:
        wanted = max(0, min(int(limit), 500))
        if wanted == 0:
            return []
        collected: list[dict[str, Any]] = []
        before = None
        while len(collected) < wanted:
            batch = min(100, wanted - len(collected))
            path = f"/channels/{channel_id}/messages?limit={batch}"
            if before:
                path += f"&before={before}"
            page = await self.call("GET", path)
            if not isinstance(page, list) or not page:
                break
            collected.extend(page)
            before = page[-1]["id"]
            if len(page) < batch:
                break
            await asyncio.sleep(0.35)
        collected.reverse()
        return collected

    async def active_threads(self, guild_id: str) -> list[dict[str, Any]]:
        try:
            data = await self.call("GET", f"/guilds/{guild_id}/threads/active")
        except ApiError:
            return []
        if not isinstance(data, dict):
            return []
        threads = data.get("threads") or []
        return threads if isinstance(threads, list) else []

    async def gateway_url(self) -> str:
        data = await self.call("GET", "/gateway")
        if not isinstance(data, dict) or not data.get("url"):
            raise ApiError(500, "gateway url missing")
        return str(data["url"])


def cloudflare_code(text: str) -> str:
    """The Cloudflare error code in a page that is not Discord's answer (`error code: 1020`), or ""."""
    match = CLOUDFLARE_RE.search(text or "")
    return match.group(1) if match else ""


def _discord_error(text: str) -> dict[str, Any] | None:
    """Discord's JSON error object in a response body, or None when the body is not one (Cloudflare pages are not)."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _refusal(status: int, detail: str, text: str) -> ApiError:
    payload = _discord_error(text)
    if payload is None:
        return NotDiscordAnswer(status, detail, cloudflare_code(text))
    return ApiError(status, detail, payload)


def _fail_detail(method: str, path: str, status: int, text: str) -> str:
    fallback = f"{method} {path} failed (HTTP {status})"
    payload = _discord_error(text)
    if payload is None:
        code = cloudflare_code(text)
        return f"{method} {path} failed (HTTP {status}, Cloudflare error code {code})" if code else fallback
    if payload.get("captcha_sitekey") or payload.get("captcha_key"):
        return "Discord asked for a captcha before it would create the server"
    message = payload.get("message")
    if message:
        return str(message)[:160]
    return fallback


async def load_properties(session: aiohttp.ClientSession) -> dict[str, Any]:
    build = 0
    try:
        async with session.get(
            "https://discord.com/login",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=aiohttp.ClientTimeout(total=12),
        ) as resp:
            html = await resp.text()
        match = BUILD_RE.search(html)
        if match:
            build = int(match.group(1))
    except Exception:
        log.warning("build number lookup failed")
    chrome_major = 131
    try:
        async with session.get(
            "https://versionhistory.googleapis.com/v1/chrome/platforms/mac/channels/stable/versions",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            payload = await resp.json(content_type=None)
        version = payload["versions"][0]["version"]
        chrome_major = int(str(version).split(".", 1)[0])
    except Exception:
        log.warning("chrome version lookup failed")
    if build <= 0:
        build = 627798
    return build_properties(build, chrome_major)
