from __future__ import annotations

import json
import unittest
from typing import Any

import aiohttp

from mirror.relay import (
    ATTEMPTS,
    UPLOAD_LIMIT,
    Relay,
    author_name,
    payload_for,
    plan_uploads,
    safe_name,
)

HOOK = "https://discord.com/api/webhooks/1/abc"
OTHER = "https://discord.com/api/webhooks/2/def"
CDN = "https://cdn.discordapp.com/attachments/1/2/"


class FakeContent:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def read(self, n: int = -1) -> bytes:
        return self.body[:100]


class FakeResp:
    def __init__(
        self,
        status: int = 200,
        payload: Any = None,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> None:
        self.status = status
        self.payload = payload
        self.headers = headers or {}
        if body is None:
            body = b"" if payload is None else json.dumps(payload).encode()
        self.body = body
        self.content = FakeContent(body)

    async def read(self) -> bytes:
        return self.body

    async def json(self, content_type: Any = None) -> Any:
        if not self.body:
            return None
        return json.loads(self.body)

    async def text(self) -> str:
        return self.body.decode()

    async def __aenter__(self) -> FakeResp:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.queue: list[Any] = []
        self.downloads: dict[str, FakeResp] = {}

    def _take(self, method: str, url: str, kwargs: dict[str, Any]) -> FakeResp:
        self.calls.append((method, url, kwargs))
        item = self.queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def get(self, url: str, **kwargs: Any) -> FakeResp:
        self.calls.append(("get", url, kwargs))
        return self.downloads.get(url) or FakeResp(404)

    def post(self, url: str, **kwargs: Any) -> FakeResp:
        return self._take("post", url, kwargs)

    def patch(self, url: str, **kwargs: Any) -> FakeResp:
        return self._take("patch", url, kwargs)

    def delete(self, url: str, **kwargs: Any) -> FakeResp:
        return self._take("delete", url, kwargs)


def attachment(name: str, size: int, url: str | None = None) -> dict[str, Any]:
    return {"name": name, "url": url or CDN + name, "content_type": "image/png", "size": size}


def make_view(attachments: list[dict[str, Any]], content: str = "hello") -> dict[str, Any]:
    return {
        "id": "1",
        "channel_id": "2",
        "channel_name": "general",
        "guild_name": "Desk",
        "author": "Ada",
        "avatar": "",
        "content": content,
        "embeds": [],
        "attachments": attachments,
        "stickers": [],
        "timestamp": "",
        "edited": None,
        "deleted": False,
        "reactions": {},
        "reply": "",
    }


def form_payload(form: aiohttp.FormData) -> dict[str, Any]:
    for options, _, value in form._fields:
        if options.get("name") == "payload_json":
            return json.loads(value)
    raise AssertionError("no payload_json")


def form_files(form: aiohttp.FormData) -> list[bytes]:
    return [value for options, _, value in form._fields if str(options.get("name")).startswith("files[")]


class RelayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.session = FakeSession()
        self.relay = Relay(self.session)
        self.waits: list[float] = []

        async def wait(seconds: float) -> None:
            self.waits.append(seconds)

        self.relay._wait = wait

    def sends(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [call for call in self.session.calls if call[0] != "get"]

    def test_author_name_breaks_blocked_substrings(self) -> None:
        fan = author_name({"author": {"username": "DiscordFan"}})
        self.assertNotIn("discord", fan.casefold())
        self.assertEqual(fan, "Dis.cordFan")
        self.assertNotIn("clyde", author_name({"author": {"username": "Clyde"}}).casefold())
        self.assertEqual(safe_name("discord."), "dis.cord.")
        self.assertNotIn("discord", safe_name("discorddiscord").casefold())
        self.assertNotIn("clyde", safe_name("ClydeClyde").casefold())
        self.assertNotIn("discord", safe_name("DISCORD  clyde\tdiscord").casefold())
        self.assertEqual(author_name({"author": {"username": "everyone"}}), "everyone.")
        self.assertEqual(author_name({"author": {"username": "here"}}), "here.")
        self.assertEqual(author_name({"member": {"nick": "HERE"}, "author": {"username": "x"}}), "HERE.")
        self.assertEqual(author_name({"author": {"username": "   "}}), "member")
        self.assertEqual(safe_name(""), "member")
        self.assertEqual(len(author_name({"author": {"username": "a" * 120}})), 80)
        for raw in ("DiscordFan", "discord.", "everyone", "a" * 79 + " discord", "x" * 200, " here ", "Clyde"):
            once = safe_name(raw)
            self.assertEqual(safe_name(once), once)
            self.assertTrue(1 <= len(once) <= 80)
        view = make_view([])
        view["author"] = "clyde"
        self.assertNotIn("clyde", payload_for(view, False)["username"].casefold())
        view["author"] = ""
        self.assertEqual(payload_for(view, False)["username"], "member")

    async def test_files_reads_whole_body(self) -> None:
        item = attachment("big.bin", 3_000_000)
        self.session.downloads[item["url"]] = FakeResp(200, body=b"z" * 3_000_000)
        files, failed = await self.relay._files([item])
        self.assertEqual(failed, [])
        self.assertEqual(len(files), 1)
        self.assertEqual(len(files[0][1]), 3_000_000)

    async def test_files_reject_bad_downloads(self) -> None:
        good = attachment("a.png", 10)
        empty = attachment("b.png", 10)
        missing = attachment("c.png", 10)
        huge = attachment("d.png", 10)
        self.session.downloads[good["url"]] = FakeResp(200, body=b"1" * 10)
        self.session.downloads[empty["url"]] = FakeResp(200, body=b"")
        self.session.downloads[huge["url"]] = FakeResp(200, body=b"1" * (UPLOAD_LIMIT + 1))
        files, failed = await self.relay._files([good, empty, missing, huge])
        self.assertEqual([name for name, _, _ in files], ["a.png"])
        self.assertEqual(failed, [empty, missing, huge])

    def test_plan_uploads_limits(self) -> None:
        many = [attachment(f"f{i}.png", 1_000) for i in range(12)]
        upload, linked = plan_uploads(many)
        self.assertEqual(upload, many[:10])
        self.assertEqual(linked, many[10:])
        big = attachment("big.bin", UPLOAD_LIMIT + 1)
        off = attachment("x.png", 10, "https://example.com/x.png")
        zero = attachment("zero.png", 0)
        upload, linked = plan_uploads([big, off, zero])
        self.assertEqual(upload, [])
        self.assertEqual(linked, [big, off, zero])
        six = [attachment(f"s{i}.bin", 2_000_000) for i in range(6)]
        upload, linked = plan_uploads(six)
        self.assertEqual(upload, six[:5])
        self.assertLessEqual(sum(item["size"] for item in upload), UPLOAD_LIMIT)
        self.assertEqual(linked, six[5:])

    async def create_with_link(self) -> tuple[dict[str, Any], str, str | None]:
        small = attachment("a.png", 10)
        off = attachment("b.png", 10, "https://example.com/b.png")
        self.session.downloads[small["url"]] = FakeResp(200, body=b"1" * 10)
        view = make_view([small, off])
        self.session.queue.append(FakeResp(200, {"id": "5"}))
        sent = await self.relay.create(HOOK, view, False)
        return view, off["url"], sent

    async def test_create_sends_links_and_sets_view_links(self) -> None:
        view, link, sent = await self.create_with_link()
        self.assertEqual(sent, "5")
        method, url, kwargs = self.sends()[0]
        self.assertEqual((method, url), ("post", HOOK + "?wait=true"))
        self.assertIsInstance(kwargs.get("data"), aiohttp.FormData)
        self.assertEqual(form_files(kwargs["data"]), [b"1" * 10])
        content = form_payload(kwargs["data"])["content"]
        self.assertTrue(content.endswith(link))
        self.assertTrue(content.startswith("hello"))
        self.assertEqual(view["links"], [link])

    async def test_edit_keeps_links(self) -> None:
        view, link, _ = await self.create_with_link()
        posted = form_payload(self.sends()[0][2]["data"])["content"]
        self.session.queue.append(FakeResp(200, {"id": "5"}))
        await self.relay.edit(HOOK, "5", view, False)
        method, url, kwargs = self.sends()[1]
        self.assertEqual((method, url), ("patch", HOOK + "/messages/5"))
        self.assertEqual(kwargs["json"]["content"], posted)
        self.assertIn(link, kwargs["json"]["content"].splitlines())
        self.assertNotIn("username", kwargs["json"])
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")

    async def test_failed_download_becomes_link(self) -> None:
        item = attachment("gone.png", 10)
        view = make_view([item], content="")
        self.session.queue.append(FakeResp(200, {"id": "6"}))
        self.assertEqual(await self.relay.create(HOOK, view, False), "6")
        kwargs = self.sends()[0][2]
        self.assertNotIn("data", kwargs)
        self.assertEqual(kwargs["json"]["content"], item["url"])
        self.assertEqual(view["links"], [item["url"]])

    async def test_too_large_post_falls_back_to_links(self) -> None:
        for first in (FakeResp(413), FakeResp(400, {"code": 40005, "message": "Request entity too large"})):
            self.session.calls.clear()
            items = [attachment("a.png", 10), attachment("b.png", 10), attachment("c.png", 10, "https://example.com/c")]
            for item in items[:2]:
                self.session.downloads[item["url"]] = FakeResp(200, body=b"1" * 10)
            view = make_view(items)
            self.session.queue.extend([first, FakeResp(200, {"id": "7"})])
            self.assertEqual(await self.relay.create(HOOK, view, True), "7")
            sends = self.sends()
            self.assertEqual(len(sends), 2)
            self.assertIn("data", sends[0][2])
            self.assertNotIn("data", sends[1][2])
            content = sends[1][2]["json"]["content"]
            for item in items:
                self.assertIn(item["url"], content)
            self.assertTrue(content.startswith("Desk / #general"))
            self.assertEqual(view["links"], [item["url"] for item in items])

    async def test_429_retries_with_fresh_formdata(self) -> None:
        item = attachment("a.png", 10)
        self.session.downloads[item["url"]] = FakeResp(200, body=b"1" * 10)
        self.session.queue.extend([FakeResp(429, {"retry_after": 1.5, "global": False}), FakeResp(200, {"id": "9"})])
        self.assertEqual(await self.relay.create(HOOK, make_view([item]), False), "9")
        posts = self.sends()
        self.assertEqual(len(posts), 2)
        first, second = posts[0][2]["data"], posts[1][2]["data"]
        self.assertIsInstance(first, aiohttp.FormData)
        self.assertIsInstance(second, aiohttp.FormData)
        self.assertIsNot(first, second)
        self.assertEqual(form_files(second), [b"1" * 10])
        self.assertEqual(len(self.waits), 1)
        self.assertAlmostEqual(self.waits[0], 1.5, delta=0.05)

        self.session.calls.clear()
        self.relay._next.clear()
        self.waits.clear()
        self.session.queue.extend([FakeResp(429, {"retry_after": 0.8}), FakeResp(200, {"id": "9"})])
        await self.relay.edit(HOOK, "9", make_view([]), False)
        self.assertEqual([call[0] for call in self.sends()], ["patch", "patch"])
        self.assertAlmostEqual(self.waits[0], 0.8, delta=0.05)

        self.session.calls.clear()
        self.relay._next.clear()
        self.waits.clear()
        self.session.queue.extend([FakeResp(429, headers={"Retry-After": "2"}), FakeResp(204)])
        await self.relay.remove(HOOK, "9")
        self.assertEqual([call[0] for call in self.sends()], ["delete", "delete"])
        self.assertAlmostEqual(self.waits[0], 2.0, delta=0.05)

    async def test_retry_after_is_capped(self) -> None:
        self.session.queue.extend([FakeResp(429, {"retry_after": 900, "global": True}), FakeResp(200, {"id": "3"})])
        self.assertEqual(await self.relay.create(HOOK, make_view([]), False), "3")
        self.assertEqual(len(self.waits), 1)
        self.assertLessEqual(self.waits[0], 60.0)
        self.assertGreater(self.waits[0], 59.0)

    async def test_rate_limit_headers_pace_next_request(self) -> None:
        headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "2.0"}
        self.session.queue.append(FakeResp(200, {"id": "1"}, headers=headers))
        self.assertEqual(await self.relay.create(HOOK, make_view([]), False), "1")
        self.assertEqual(self.waits, [])
        self.session.queue.append(FakeResp(200, {"id": "2"}))
        self.assertEqual(await self.relay.create(OTHER, make_view([]), False), "2")
        self.assertEqual(self.waits, [])
        self.session.queue.append(FakeResp(200, {"id": "3"}))
        self.assertEqual(await self.relay.create(HOOK, make_view([]), False), "3")
        self.assertEqual(len(self.waits), 1)
        self.assertGreaterEqual(self.waits[0], 1.9)
        self.session.queue.append(FakeResp(200, {"id": "4"}, headers={"X-RateLimit-Remaining": "4"}))
        self.waits.clear()
        await self.relay.create(OTHER, make_view([]), False)
        self.assertTrue(all(wait <= 0.5 for wait in self.waits))

    async def test_server_error_then_success(self) -> None:
        self.session.queue.extend([FakeResp(500), aiohttp.ClientConnectionError("reset"), FakeResp(200, {"id": "4"})])
        self.assertEqual(await self.relay.create(HOOK, make_view([]), False), "4")
        self.assertEqual(len(self.sends()), 3)
        self.assertGreaterEqual(self.waits[0], 0.9)
        self.assertGreaterEqual(self.waits[1], 1.9)

    async def test_gives_up_after_attempts(self) -> None:
        self.session.queue.extend([FakeResp(500) for _ in range(ATTEMPTS)])
        self.assertIsNone(await self.relay.create(HOOK, make_view([]), False))
        self.assertEqual(len(self.sends()), ATTEMPTS)
        self.assertEqual(len(self.waits), ATTEMPTS - 1)

    async def test_client_error_is_not_retried(self) -> None:
        self.session.queue.append(FakeResp(400, {"code": 50006, "message": "Cannot send an empty message"}))
        self.assertIsNone(await self.relay.create(HOOK, make_view([]), False))
        self.assertEqual(len(self.sends()), 1)
        self.session.queue.append(FakeResp(404, {"code": 10008}))
        await self.relay.remove(HOOK, "5")
        self.session.queue.append(FakeResp(404, {"code": 10008}))
        await self.relay.edit(HOOK, "5", make_view([]), False)
        self.assertEqual(len(self.sends()), 3)

    def test_content_never_exceeds_2000_with_links(self) -> None:
        links = [f"https://example.com/{i}/" + "a" * 40 for i in range(3)]
        view = make_view([], content="x" * 3000)
        view["links"] = links
        content = payload_for(view, True)["content"]
        self.assertLessEqual(len(content), 2000)
        self.assertTrue(content.startswith("Desk / #general"))
        self.assertTrue(content.endswith("\n".join(links)))
        long = [f"https://example.com/{i:02d}/" + "b" * 280 for i in range(15)]
        view["links"] = long
        content = payload_for(view, True)["content"]
        self.assertLessEqual(len(content), 2000)
        self.assertTrue(content.startswith("Desk / #general"))
        kept = [line for line in content.splitlines() if line.startswith("https://")]
        self.assertGreater(len(kept), 0)
        self.assertEqual(kept, long[: len(kept)])
        view["content"] = ""
        content = payload_for(view, False)["content"]
        self.assertLessEqual(len(content), 2000)
        self.assertEqual(content.splitlines(), long[: len(content.splitlines())])

    def test_default_links_without_view_key(self) -> None:
        view = make_view([attachment("a.png", 10), attachment("b.png", 10, "https://example.com/b")], content="")
        self.assertEqual(payload_for(view, False)["content"], "https://example.com/b")
        view["attachments"] = [attachment("a.png", 10)]
        self.assertEqual(payload_for(view, False)["content"], "(attachment)")
        view["links"] = []
        self.assertEqual(payload_for(view, False)["content"], "(attachment)")


if __name__ == "__main__":
    unittest.main()
