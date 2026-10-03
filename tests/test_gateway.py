from __future__ import annotations

import asyncio
import json
import logging
import unittest
from typing import Any
from unittest import mock

import aiohttp

from mirror import gateway as gw
from mirror.gateway import Gateway


def text(packet: dict) -> aiohttp.WSMessage:
    return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, json.dumps(packet), None)


def hello(ms: int = 40) -> dict:
    return {"op": 10, "d": {"heartbeat_interval": ms}}


def ready(seq: int = 1) -> dict:
    return {"op": 0, "s": seq, "t": "READY", "d": {"session_id": "s", "resume_gateway_url": "wss://r"}}


def resumed(seq: int = 5) -> dict:
    return {"op": 0, "s": seq, "t": "RESUMED", "d": {}}


CLOSED = aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, None)


class FakeWS:
    def __init__(self, packets: list[Any], end: str = "block", ack: bool = True) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        for item in packets:
            if isinstance(item, int):
                self.queue.put_nowait(aiohttp.WSMessage(aiohttp.WSMsgType.CLOSE, item, ""))
            else:
                self.queue.put_nowait(text(item))
        self.end = end
        self.ack = ack
        self.closed = False
        self.close_code: int | None = None
        self.codes: list[int] = []
        self.sent: list[dict] = []
        self.timeouts: list[float | None] = []
        self.seen_closed: bool | None = None

    async def receive(self, timeout: float | None = None) -> aiohttp.WSMessage:
        self.timeouts.append(timeout)
        if self.queue.empty() and (self.closed or self.end == "closed"):
            return CLOSED
        if timeout:
            msg = await asyncio.wait_for(self.queue.get(), timeout)
        else:
            msg = await self.queue.get()
        if msg.type == aiohttp.WSMsgType.CLOSE:
            self.close_code = msg.data
        return msg

    async def close(self, code: int = 1000, message: bytes = b"") -> bool:
        if self.closed:
            return False
        self.closed = True
        self.codes.append(code)
        if self.close_code is None:
            self.close_code = code
        self.queue.put_nowait(CLOSED)
        return True

    async def send_str(self, raw: str) -> None:
        packet = json.loads(raw)
        self.sent.append(packet)
        if packet.get("op") == 40 and self.ack:
            self.queue.put_nowait(text({"op": 11}))

    def ops(self) -> list[int]:
        return [p["op"] for p in self.sent]

    def login(self) -> dict:
        return next(p for p in self.sent if p["op"] in (2, 6))


class FakeCtx:
    def __init__(self, ws: FakeWS) -> None:
        self.ws = ws

    async def __aenter__(self) -> FakeWS:
        return self.ws

    async def __aexit__(self, *exc: Any) -> None:
        self.ws.seen_closed = self.ws.closed
        await self.ws.close()


class FakeSession:
    def __init__(self, sockets: list[FakeWS]) -> None:
        self.sockets = list(sockets)
        self.calls: list[tuple[str, dict]] = []

    def ws_connect(self, url: str, **kw: Any) -> FakeCtx:
        self.calls.append((url, kw))
        return FakeCtx(self.sockets.pop(0))


class FakeHTTP:
    def __init__(self, sockets: list[FakeWS]) -> None:
        self.token = "t"
        self.properties = {
            "browser_user_agent": "ua",
            "client_heartbeat_session_id": "h",
            "client_launch_id": "l",
        }
        self.session = FakeSession(sockets)

    async def gateway_url(self) -> str:
        return "wss://x"


async def until(check: Any, limit: float = 1.0) -> bool:
    end = asyncio.get_running_loop().time() + limit
    while not check():
        if asyncio.get_running_loop().time() > end:
            return False
        await asyncio.sleep(0.005)
    return True


class GatewayTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        log = logging.getLogger("mirror.gateway")
        self.addCleanup(log.setLevel, log.level)
        log.setLevel(logging.CRITICAL)

    def build(self, sockets: list[FakeWS], resume: bool = True) -> Gateway:
        self.events: list[str] = []
        self.fatal: list[str] = []
        self.waits: list[float] = []

        async def dispatch(event: str, data: dict) -> None:
            self.events.append(event)

        async def fatal(msg: str) -> None:
            self.fatal.append(msg)

        async def wait(seconds: float) -> None:
            self.waits.append(seconds)
            await asyncio.sleep(0)

        self.http = FakeHTTP(sockets)
        gate = Gateway(self.http, dispatch, fatal)
        gate._wait = wait
        if resume:
            gate.resume_url = "wss://x"
        return gate

    async def once(self, gate: Gateway, limit: float = 1.0) -> None:
        await asyncio.wait_for(gate._once(), limit)

    async def test_op7_closes_with_4000_and_keeps_session(self) -> None:
        ws = FakeWS([hello(), ready(3), {"op": 7, "d": None}])
        gate = self.build([ws], resume=False)
        await self.once(gate)
        self.assertEqual(ws.codes, [gw.RECONNECT])
        self.assertTrue(ws.seen_closed)
        self.assertEqual(gate.session_id, "s")
        self.assertEqual(gate.resume_url, "wss://r")
        self.assertEqual(gate.sequence, 3)
        self.assertIs(self.http.session.calls[0][1]["autoclose"], False)

    async def test_op9_resumable_keeps_session(self) -> None:
        ws = FakeWS([hello(), ready(4), {"op": 9, "d": True}])
        gate = self.build([ws])
        await self.once(gate)
        self.assertEqual(ws.codes, [4000])
        self.assertEqual(gate.session_id, "s")
        self.assertEqual(gate.sequence, 4)
        self.assertEqual(gate.resume_url, "wss://r")

    async def test_op9_not_resumable_clears_session(self) -> None:
        ws = FakeWS([hello(), ready(4), {"op": 9, "d": False}])
        gate = self.build([ws])
        await self.once(gate)
        self.assertEqual(ws.codes, [4000])
        self.assertIsNone(gate.session_id)
        self.assertIsNone(gate.sequence)
        self.assertIsNone(gate.resume_url)

    async def test_hello_missing_closes_with_4000(self) -> None:
        ws = FakeWS([])
        gate = self.build([ws])
        with mock.patch.object(gw, "HELLO_WAIT", 0.05):
            with self.assertRaises(RuntimeError):
                await self.once(gate)
        self.assertEqual(ws.codes, [4000])
        self.assertEqual(ws.timeouts[0], 0.05)

    async def test_stop_uses_1000(self) -> None:
        ws = FakeWS([hello(), ready()])
        gate = self.build([ws])
        gate.start()
        self.assertTrue(await until(lambda: "READY" in self.events))
        await asyncio.wait_for(gate.stop(), 1.0)
        self.assertEqual(ws.codes, [1000])
        self.assertIsNone(gate._task)
        self.assertEqual(len(self.http.session.calls), 1)

    async def test_missing_ack_closes_resumable(self) -> None:
        first = FakeWS([hello(40), ready(2)], ack=False)
        second = FakeWS([hello(40), resumed(6)])
        gate = self.build([first, second])
        try:
            with self.assertLogs("mirror.gateway", "WARNING") as seen:
                gate.start()
                self.assertTrue(await until(lambda: first.closed, 1.0))
            self.assertIn("heartbeat not acked", "\n".join(seen.output))
            self.assertEqual(first.codes, [4000])
            self.assertTrue(await until(lambda: "RESUMED" in self.events, 1.0))
            login = second.login()
            self.assertEqual(login["op"], 6)
            self.assertEqual(login["d"]["session_id"], "s")
            self.assertEqual(login["d"]["seq"], 2)
            self.assertEqual(gate.sequence, 6)
        finally:
            await asyncio.wait_for(gate.stop(), 1.0)
        self.assertEqual(second.codes, [1000])

    async def test_acked_beats_keep_connection(self) -> None:
        ws = FakeWS([hello(40), ready()])
        gate = self.build([ws])
        task = asyncio.create_task(gate._once())
        await asyncio.sleep(0.3)
        try:
            self.assertEqual(ws.codes, [])
            self.assertGreaterEqual(ws.ops().count(40), 3)
            self.assertFalse(task.done())
        finally:
            await asyncio.wait_for(gate.stop(), 1.0)
            await asyncio.wait_for(task, 1.0)
        self.assertEqual(ws.codes, [1000])

    async def test_receive_timeout_ends_session(self) -> None:
        ws = FakeWS([hello(10000), ready()])
        gate = self.build([ws])
        gate._patience = lambda interval: interval / 100
        await self.once(gate)
        self.assertEqual(ws.codes, [4000])
        self.assertIn(0.1, ws.timeouts)
        self.assertEqual(gate.session_id, "s")

    async def test_read_timeout_follows_interval(self) -> None:
        gate = self.build([])
        self.assertEqual(gate._patience(41.25), 92.5)

    def script(self, gate: Gateway, plan: list[str]) -> None:
        steps = iter(plan)

        async def once() -> None:
            step = next(steps, None)
            if step is None:
                gate._stop.set()
                return
            if step == "ok":
                gate._healthy = True
                return
            if step == "quiet":
                return
            raise RuntimeError("boom")

        gate._once = once

    async def test_backoff_resets_only_after_ready(self) -> None:
        gate = self.build([])
        self.script(gate, ["fail", "fail", "quiet", "ok", "fail"])
        await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual([int(w) for w in self.waits], [1, 2, 4, 1, 2])
        for w in self.waits:
            self.assertLess(w - int(w), 1.0)

    async def test_backoff_caps_at_20(self) -> None:
        gate = self.build([])
        self.script(gate, ["fail"] * 8)
        await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual([int(w) for w in self.waits], [1, 2, 4, 8, 16, 20, 20, 20])

    async def test_wait_returns_on_stop(self) -> None:
        gate = Gateway(FakeHTTP([]), self.noop)
        task = asyncio.create_task(gate._wait(30.0))
        await asyncio.sleep(0.01)
        gate._stop.set()
        await asyncio.wait_for(task, 0.2)

    async def noop(self, event: str, data: dict) -> None:
        return None

    async def test_server_close_4004_is_fatal(self) -> None:
        ws = FakeWS([hello(), 4004])
        gate = self.build([ws])
        await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual(self.fatal, ["token was rejected"])
        self.assertTrue(gate._stop.is_set())
        self.assertEqual(ws.codes, [4000])
        self.assertEqual(self.waits, [])

    async def test_server_close_refused_is_fatal(self) -> None:
        ws = FakeWS([hello(), ready(), 4013])
        gate = self.build([ws])
        await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual(self.fatal, ["gateway refused the session"])
        self.assertEqual(ws.codes, [4000])

    async def test_4009_clears_session(self) -> None:
        ws = FakeWS([hello(), ready(7), 4009])
        gate = self.build([ws])
        await self.once(gate)
        self.assertIsNone(gate.session_id)
        self.assertIsNone(gate.sequence)
        self.assertIsNone(gate.resume_url)
        self.assertEqual(self.fatal, [])
        self.assertFalse(gate._stop.is_set())
        self.assertEqual(ws.codes, [4000])

    async def test_other_server_close_keeps_session(self) -> None:
        ws = FakeWS([hello(), ready(7), 1001])
        gate = self.build([ws])
        await self.once(gate)
        self.assertEqual(gate.session_id, "s")
        self.assertEqual(gate.sequence, 7)
        self.assertEqual(ws.codes, [4000])
        self.assertEqual(self.fatal, [])

    async def test_identify_vs_resume(self) -> None:
        fresh = FakeWS([hello(), {"op": 7}])
        gate = self.build([fresh], resume=False)
        await self.once(gate)
        self.assertEqual(fresh.login()["op"], 2)
        self.assertEqual(self.http.session.calls[0][0].split("?")[0], "wss://x")
        again = FakeWS([hello(), {"op": 7}])
        gate = self.build([again])
        gate.session_id = "s"
        gate.sequence = 9
        gate.resume_url = "wss://r"
        await self.once(gate)
        login = again.login()
        self.assertEqual(login["op"], 6)
        self.assertEqual(login["d"], {"token": "t", "session_id": "s", "seq": 9})
        self.assertEqual(self.http.session.calls[0][0].split("?")[0], "wss://r")

    async def test_ready_sends_op41_and_marks_healthy(self) -> None:
        ws = FakeWS([hello(), ready(), {"op": 7}])
        gate = self.build([ws])
        await self.once(gate)
        self.assertIn(41, ws.ops())
        self.assertTrue(gate._healthy)
        self.assertEqual(self.events, ["READY"])


if __name__ == "__main__":
    unittest.main()
