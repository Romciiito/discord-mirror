from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

import mirror.__main__ as entry
import mirror.web as webmod
from mirror.web import create_app, hostname


class HostnameTests(unittest.TestCase):
    def test_hostname_forms(self) -> None:
        self.assertEqual(hostname("localhost:8765"), "localhost")
        self.assertEqual(hostname("[::1]:8765"), "::1")
        self.assertEqual(hostname("[::1]"), "::1")
        self.assertEqual(hostname("::1"), "::1")
        self.assertEqual(hostname("LocalHost"), "localhost")
        self.assertEqual(hostname("127.0.0.1"), "127.0.0.1")
        self.assertEqual(hostname("attacker.example:8765"), "attacker.example")


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.clients: list[TestClient] = []

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            await client.close()
        self.tmp.cleanup()

    async def client(self, host: str = "127.0.0.1") -> TestClient:
        app = create_app(self.tmp.name, host, 8765)
        client = TestClient(TestServer(app, host="127.0.0.1"))
        await client.start_server()
        self.clients.append(client)
        return client

    def host_of(self, client: TestClient) -> str:
        return f"{client.server.host}:{client.server.port}"

    async def test_404_is_not_500(self) -> None:
        client = await self.client()
        with self.assertNoLogs("mirror.web", level="ERROR"):
            resp = await client.get("/favicon.ico")
            self.assertEqual(resp.status, 404)
            resp = await client.get("/api/nope")
            self.assertEqual(resp.status, 404)
            resp = await client.get("/api/start")
            self.assertEqual(resp.status, 405)

    async def test_cross_origin_is_rejected(self) -> None:
        client = await self.client()
        resp = await client.post("/api/stop", headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status, 403)
        self.assertEqual(await resp.json(), {"error": "bad origin"})
        resp = await client.post("/api/stop", headers={"Origin": "null"})
        self.assertEqual(resp.status, 403)
        resp = await client.post("/api/stop", headers={"Origin": "http://" + self.host_of(client)})
        self.assertEqual(resp.status, 200)
        resp = await client.post("/api/stop")
        self.assertEqual(resp.status, 200)
        resp = await client.get("/", headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status, 200)

    async def test_foreign_host_is_rejected_on_loopback(self) -> None:
        client = await self.client()
        resp = await client.get("/api/state", headers={"Host": "attacker.example:8765"})
        self.assertEqual(resp.status, 403)
        self.assertEqual(await resp.json(), {"error": "bad host"})
        resp = await client.get("/", headers={"Host": "attacker.example:8765"})
        self.assertEqual(resp.status, 403)
        resp = await client.get("/api/state", headers={"Host": "localhost:1234"})
        self.assertEqual(resp.status, 200)
        resp = await client.get("/api/state", headers={"Host": "[::1]:8765"})
        self.assertEqual(resp.status, 200)

    async def test_rebinding_origin_and_host_are_rejected(self) -> None:
        client = await self.client()
        headers = {"Host": "attacker.example:8765", "Origin": "http://attacker.example:8765"}
        resp = await client.get("/api/events", headers=headers)
        self.assertEqual(resp.status, 403)
        resp = await client.put("/api/setup", json={"backfill": 1}, headers=headers)
        self.assertEqual(resp.status, 403)

    async def test_non_loopback_bind_skips_host_check_but_keeps_origin(self) -> None:
        client = await self.client("0.0.0.0")
        resp = await client.get("/api/state", headers={"Host": "attacker.example"})
        self.assertEqual(resp.status, 200)
        resp = await client.post("/api/stop", headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status, 403)

    async def test_json_bodies_require_json_type(self) -> None:
        client = await self.client()
        resp = await client.put("/api/setup", data='{"backfill": 25}', headers={"Content-Type": "text/plain"})
        self.assertEqual(resp.status, 415)
        self.assertEqual(await resp.json(), {"error": "expected json"})
        resp = await client.put("/api/setup", data='{"backfill": 25}', headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["options"]["backfill"], 25)
        resp = await client.put("/api/setup", data="[]", headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status, 400)
        resp = await client.post("/api/session", data='{"token": "x"}', headers={"Content-Type": "text/plain"})
        self.assertEqual(resp.status, 415)
        resp = await client.post("/api/session", data=b'{"token": "x"}')
        self.assertEqual(resp.status, 415)

    async def test_events_stream_ends_when_queue_dropped(self) -> None:
        client = await self.client()
        engine = client.server.app["engine"]
        with mock.patch.object(webmod, "PING", 0.05):
            resp = await client.get("/api/events", timeout=aiohttp.ClientTimeout(total=None))
            self.assertEqual(resp.status, 200)
            first = await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 1.0)
            self.assertEqual(first, b": ok\n\n")
            self.assertEqual(len(engine.listeners), 1)
            engine.listeners.clear()
            await asyncio.wait_for(resp.read(), 1.0)
            self.assertEqual(resp.status, 200)

    async def test_events_end_after_item_when_dropped(self) -> None:
        client = await self.client()
        engine = client.server.app["engine"]
        resp = await client.get("/api/events", timeout=aiohttp.ClientTimeout(total=None))
        await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 1.0)
        queue = next(iter(engine.listeners))
        engine.listeners.clear()
        queue.put_nowait({"kind": "log", "text": "last"})
        body = await asyncio.wait_for(resp.read(), 1.0)
        self.assertIn(b'"text":"last"', body)

    async def test_events_delivers_items(self) -> None:
        client = await self.client()
        engine = client.server.app["engine"]
        resp = await client.get("/api/events", timeout=aiohttp.ClientTimeout(total=None))
        await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 1.0)
        engine.note("hello")
        chunk = await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 1.0)
        self.assertTrue(chunk.startswith(b"data: "))
        self.assertEqual(json.loads(chunk[6:].decode()), {"kind": "log", "text": "hello"})
        resp.close()


class MainTests(unittest.TestCase):
    def test_main_passes_bind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"HOST": "0.0.0.0", "PORT": "9000", "DATA_DIR": tmp}
            with mock.patch.dict(os.environ, env), mock.patch("logging.basicConfig"), mock.patch.object(
                entry, "create_app"
            ) as make, mock.patch.object(entry.web, "run_app") as run:
                entry.main()
            make.assert_called_once_with(tmp, "0.0.0.0", 9000)
            run.assert_called_once()
            self.assertIs(run.call_args.args[0], make.return_value)
            self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")
            self.assertEqual(run.call_args.kwargs["port"], 9000)


if __name__ == "__main__":
    unittest.main()
