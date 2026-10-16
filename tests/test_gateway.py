from __future__ import annotations

import asyncio
import json
import logging
import time
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
    def __init__(self, packets: list[Any], end: str = "block", ack: bool = True, fail_ops: frozenset[int] = frozenset()) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        for item in packets:
            if isinstance(item, int):
                self.queue.put_nowait(aiohttp.WSMessage(aiohttp.WSMsgType.CLOSE, item, ""))
            elif isinstance(item, tuple):
                # Discord's close frame with a reason: (code, reason)
                self.queue.put_nowait(aiohttp.WSMessage(aiohttp.WSMsgType.CLOSE, item[0], item[1]))
            else:
                self.queue.put_nowait(text(item))
        self.end = end
        self.ack = ack
        # ops whose send meets a closing transport, as aiohttp raises it (mando.log 2026-10-16 21:39:17)
        self.fail_ops = fail_ops
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

    def exception(self) -> BaseException | None:
        return None

    async def send_str(self, raw: str) -> None:
        packet = json.loads(raw)
        if packet.get("op") in self.fail_ops:
            raise aiohttp.ClientConnectionResetError("Cannot write to closing transport")
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

    async def test_stop_after_the_task_died_returns_and_logs_its_error(self) -> None:
        # Engine.stop awaits Gateway.stop before Engine.close closes the session and the store: a task that
        # already ended with an error, even a KeyboardInterrupt delivered inside its step, must not abort that
        async def boom() -> None:
            raise RuntimeError("boom")

        crashed = asyncio.create_task(boom())
        await asyncio.wait({crashed})
        interrupted = asyncio.get_running_loop().create_future()
        interrupted.set_exception(KeyboardInterrupt())
        for task, kind in ((crashed, "RuntimeError"), (interrupted, "KeyboardInterrupt")):
            with self.subTest(kind):
                gate = self.build([])
                gate._task = task  # type: ignore[assignment]
                with self.assertLogs("mirror.gateway", logging.WARNING) as logs:
                    try:
                        # awaited here, not in a wait_for task, so a re-raised KeyboardInterrupt fails this test
                        # instead of ending the test run
                        await gate.stop()
                    except BaseException as exc:
                        self.fail(f"stop re-raised the dead task's {exc!r}")
                self.assertIsNone(gate._task)
                self.assertIn(kind, "\n".join(logs.output))

    async def test_stop_cancels_a_task_that_outlives_the_wait_and_awaits_its_end(self) -> None:
        ended = asyncio.Event()

        async def stubborn() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.01)
                ended.set()
                raise

        gate = self.build([])
        gate._task = asyncio.create_task(stubborn())
        task = gate._task
        await asyncio.sleep(0)
        with mock.patch.object(gw, "STOP_WAIT", 0.05):
            await asyncio.wait_for(gate.stop(), 1.0)
        self.assertTrue(ended.is_set())
        self.assertTrue(task.cancelled())
        self.assertIsNone(gate._task)

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
        try:
            self.assertTrue(await until(lambda: ws.ops().count(40) >= 3, 5.0))
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

    async def test_a_receive_timeout_without_a_set_timeout_ends_the_read(self) -> None:
        # aiohttp can time out on its own receive timeout while ours is None
        class Timing(FakeWS):
            async def receive(self, timeout: float | None = None) -> aiohttp.WSMessage:
                raise asyncio.TimeoutError()

        gate = self.build([])
        self.assertIsNone(await gate._recv_json(Timing([]), gw.Inflate(), None))
        self.assertEqual(gate._why, "receive timed out")

    async def test_read_timeout_follows_interval(self) -> None:
        gate = self.build([])
        self.assertEqual(gate._patience(41.25), 92.5)

    def script(self, gate: Gateway, plan: list[str]) -> None:
        steps = iter(plan)

        async def once() -> None:
            gate._begin()
            step = next(steps, None)
            if step is None:
                gate._stop.set()
                return
            if step == "quiet":
                return
            if step == "fail":
                raise RuntimeError("boom")
            # "ok": READY and 61 s of life; "short": READY and Discord's close 2 s later; "resumed": RESUMED, 2 s
            lived = gw.HEALTHY_AFTER + 1 if step == "ok" else 2.0
            gate._live_at = time.monotonic() - lived
            gate._live_event = "RESUMED" if step == "resumed" else "READY"
            gate._identified = step != "resumed"  # a READY follows an IDENTIFY, a RESUMED a RESUME
            gate._close_code = 4000

        gate._once = once

    async def test_backoff_resets_only_after_a_session_that_lived_60_s(self) -> None:
        gate = self.build([])
        self.script(gate, ["fail", "fail", "quiet", "ok", "fail"])
        await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual([int(w) for w in self.waits], [1, 2, 4, 1, 2])
        for w in self.waits:
            self.assertLess(w - int(w), 1.0)

    async def test_short_sessions_keep_the_backoff_growing(self) -> None:
        gate = self.build([])
        self.script(gate, ["short", "short", "ok", "short", "short"])
        await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual([int(w) for w in self.waits], [1, 2, 1, 2, 4])
        self.assertEqual(self.fatal, [])

    async def test_three_short_sessions_in_a_row_stop_the_gateway(self) -> None:
        gate = self.build([])
        self.script(gate, ["short", "quiet", "short", "fail", "short", "short"])
        await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual(self.fatal, ["close 4000: Discord keeps closing, stopped"])
        self.assertTrue(gate._stop.is_set())
        self.assertEqual([int(w) for w in self.waits], [1, 2, 4, 8])

    async def test_short_resumed_sessions_do_not_stop_the_gateway(self) -> None:
        # a RESUME starts no new session; the stop guards against repeated IDENTIFY
        gate = self.build([])
        self.script(gate, ["resumed"] * 4)
        await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual(self.fatal, [])
        self.assertEqual([int(w) for w in self.waits], [1, 2, 4, 8])

    async def test_a_storm_of_short_sessions_identifies_three_times_and_stops(self) -> None:
        # mando.log 2026-10-16 21:35-21:40: READY every 5-6 s, never RESUMED. Here Discord closes each session
        # right after READY with 4000 and refuses the resume (op 9, false), so each READY needs an IDENTIFY
        sockets = []
        for at in range(5):
            if at % 2:
                sockets.append(FakeWS([hello(10000), {"op": 9, "d": False}]))
            else:
                sockets.append(FakeWS([hello(10000), ready(), (4000, "")]))
        gate = self.build(sockets, resume=False)
        lines = (await self.ended(gate)).splitlines()
        self.assertEqual(len(lines), 5)
        self.assertIn("short session 1 of 3, next resume", lines[0])
        self.assertIn("no READY or RESUMED, next identify", lines[1])
        self.assertIn(
            "short session 3 of 3, next stop: close 4000: Discord keeps closing, stopped",
            lines[4],
        )
        self.assertEqual(self.fatal, ["close 4000: Discord keeps closing, stopped"])
        self.assertEqual(len(self.http.session.calls), 5)
        self.assertEqual([ws.login()["op"] for ws in sockets], [2, 6, 2, 6, 2])
        self.assertEqual([int(w) for w in self.waits], [1, 2, 4, 8])

    async def test_identifies_that_discord_closes_before_ready_stop_the_gateway(self) -> None:
        # review of PR #14: without READY the session was never counted and the loop identified every ~21 s
        sockets = [FakeWS([hello(10000), (4000, "")]) for _ in range(3)]
        gate = self.build(sockets, resume=False)
        lines = (await self.ended(gate)).splitlines()
        self.assertEqual(self.fatal, ["close 4000: Discord keeps closing, stopped"])
        self.assertEqual([ws.login()["op"] for ws in sockets], [2, 2, 2])
        self.assertEqual([int(w) for w in self.waits], [1, 2])
        self.assertIn("no READY or RESUMED, short session 1 of 3, next identify", lines[0])

    async def test_identifies_answered_with_op9_stop_the_gateway(self) -> None:
        sockets = [FakeWS([hello(10000), {"op": 9, "d": False}]) for _ in range(3)]
        gate = self.build(sockets, resume=False)
        await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual(self.fatal, ["connection keeps ending, stopped"])
        self.assertEqual([ws.login()["op"] for ws in sockets], [2, 2, 2])

    async def test_a_short_ready_counts_also_on_a_connection_that_sent_resume(self) -> None:
        # a READY is always a new session; the review's engine scenario answers each RESUME with READY
        sockets = [FakeWS([hello(10000), ready(), (4000, "Unknown error")]) for _ in range(3)]
        gate = self.build(sockets, resume=False)
        await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual([ws.login()["op"] for ws in sockets], [2, 6, 6])
        self.assertEqual(self.fatal, ["close 4000: Discord keeps closing, stopped"])

    async def test_a_new_start_after_the_stop_counts_from_zero(self) -> None:
        # review of PR #14: start() on the same object kept _short and _fatal_text, so its first short session
        # stopped the gateway again and a stop by Mando was logged as a stop for Discord's closes
        sockets = [FakeWS([hello(10000), ready(), 4009]) for _ in range(4)]
        sockets.append(FakeWS([hello(10000), ready()]))
        gate = self.build(sockets, resume=False)
        await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual(self.fatal, ["close 4009: Discord keeps closing, stopped"])
        with self.assertLogs("mirror.gateway", logging.WARNING) as seen:
            gate.start()
            self.assertTrue(await until(lambda: self.events.count("READY") == 5, 1.0), self.fatal)
            await asyncio.wait_for(gate.stop(), 1.0)
        ended = [line for line in seen.output if "gateway session ended" in line]
        self.assertEqual(len(ended), 1, ended)
        self.assertIn("short session 1 of 3, next identify", ended[0])
        self.assertEqual(self.fatal, ["close 4009: Discord keeps closing, stopped"])

    async def test_connections_without_an_identify_are_not_counted(self) -> None:
        # a missing HELLO sends no IDENTIFY; a refused RESUME is covered by the storm test above
        sockets = [FakeWS([]) for _ in range(3)]
        gate = self.build(sockets, resume=False)
        self.stop_at_first_wait(gate, 3)
        with mock.patch.object(gw, "HELLO_WAIT", 0.02):
            await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual(self.fatal, [])
        self.assertEqual([ws.sent for ws in sockets], [[], [], []])
        self.assertEqual(gate._short, 0)

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

    async def test_server_close_4015_too_many_sessions_is_fatal(self) -> None:
        # discord-userdoccers: 4015 "Too many sessions", at most 50 active Gateway sessions for a user account;
        # another IDENTIFY would only add one
        ws = FakeWS([hello(), ready(), 4015])
        gate = self.build([ws])
        await asyncio.wait_for(gate._run(), 1.0)
        self.assertEqual(self.fatal, ["close 4015: too many sessions, stopped"])
        self.assertTrue(gate._stop.is_set())
        self.assertEqual(len(self.http.session.calls), 1)
        self.assertEqual(self.waits, [])

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

    async def test_ready_sends_op41_and_marks_the_session_live(self) -> None:
        ws = FakeWS([hello(), ready(), {"op": 7}])
        gate = self.build([ws])
        await self.once(gate)
        self.assertIn(41, ws.ops())
        self.assertIsNotNone(gate._live_at)
        self.assertEqual(gate._live_event, "READY")
        self.assertEqual(self.events, ["READY"])

    def stop_at_first_wait(self, gate: Gateway, waits: int = 1) -> None:
        # the session's end is logged before the backoff wait; stopping there ends _run after `waits` sessions
        async def wait(seconds: float) -> None:
            self.waits.append(seconds)
            if len(self.waits) >= waits:
                gate._stop.set()

        gate._wait = wait

    async def ended(self, gate: Gateway) -> str:
        with self.assertLogs("mirror.gateway", logging.WARNING) as seen:
            await asyncio.wait_for(gate._run(), 1.0)
        return "\n".join(line for line in seen.output if "gateway session ended" in line)

    async def test_a_session_end_is_logged_with_the_close_code_reason_age_and_next_step(self) -> None:
        ws = FakeWS([hello(), ready(3), (4000, "Unknown error")])
        gate = self.build([ws], resume=False)
        self.http.token = "TOKEN-THAT-MUST-NOT-BE-LOGGED"
        self.stop_at_first_wait(gate)
        line = await self.ended(gate)
        self.assertIn("close code 4000", line)
        self.assertIn("reason 'Unknown error'", line)
        self.assertRegex(line, r"\d+\.\d s after READY")
        self.assertRegex(line, r"next resume in \d+\.\d s")
        self.assertNotIn("TOKEN-THAT-MUST-NOT-BE-LOGGED", line)
        self.assertEqual(len(self.waits), 1)

    async def test_a_session_end_with_4009_says_it_identifies_next(self) -> None:
        ws = FakeWS([hello(), ready(), 4009])
        gate = self.build([ws])
        self.stop_at_first_wait(gate)
        line = await self.ended(gate)
        self.assertIn("close code 4009", line)
        self.assertIn("next identify", line)

    async def test_an_invalid_session_end_names_op9_without_a_close_code(self) -> None:
        ws = FakeWS([hello(), ready(), {"op": 9, "d": False}])
        gate = self.build([ws])
        self.stop_at_first_wait(gate)
        line = await self.ended(gate)
        self.assertIn("no close code", line)
        self.assertIn("invalid session (op 9, resumable false)", line)
        self.assertIn("next identify", line)

    async def test_a_resume_that_is_refused_is_logged_as_an_end_before_ready(self) -> None:
        ws = FakeWS([hello(), {"op": 9, "d": False}])
        gate = self.build([ws])
        gate.session_id = "s"
        gate.sequence = 4
        self.stop_at_first_wait(gate)
        line = await self.ended(gate)
        self.assertEqual(ws.login()["op"], 6)
        self.assertIn("no READY or RESUMED", line)
        self.assertIn("next identify", line)

    async def test_a_fatal_close_is_logged_with_its_code_and_the_stop(self) -> None:
        ws = FakeWS([hello(), ready(), (4004, "Authentication failed")])
        gate = self.build([ws])
        line = await self.ended(gate)
        self.assertIn("close code 4004, reason 'Authentication failed'", line)
        self.assertIn("next stop: token was rejected", line)
        self.assertEqual(self.fatal, ["token was rejected"])

    async def test_a_stop_by_mando_is_not_logged_as_a_session_end(self) -> None:
        ws = FakeWS([hello(), ready()])
        gate = self.build([ws])
        log = logging.getLogger("mirror.gateway")
        log.setLevel(logging.WARNING)
        with mock.patch.object(log, "warning") as warning:
            gate.start()
            self.assertTrue(await until(lambda: "READY" in self.events))
            await asyncio.wait_for(gate.stop(), 1.0)
        self.assertEqual([c for c in warning.call_args_list if "gateway session ended" in str(c)], [])

    async def test_a_send_on_a_closing_transport_reads_discords_close_frame(self) -> None:
        # mando.log 2026-10-16 21:39:17: op 14 after READY raised "Cannot write to closing transport" out of
        # _once, so the close frame waiting in the reader, and its code, were never read
        ws = FakeWS([hello(10000), ready(), (4000, "")], fail_ops=frozenset({14}))
        gate = self.build([ws])
        gate.set_subscriptions({"111": ["1"]}, False)
        self.stop_at_first_wait(gate)
        line = await self.ended(gate)
        self.assertIn("close code 4000", line)
        self.assertIn("send failed: ClientConnectionResetError('Cannot write to closing transport')", line)
        self.assertNotIn("gateway session failed", line)
        self.assertEqual(self.events, ["READY"])

    async def test_resubscribe_on_a_closing_transport_does_not_raise(self) -> None:
        ws = FakeWS([], fail_ops=frozenset({14}))
        gate = self.build([])
        gate._ws = ws  # type: ignore[assignment]
        gate.set_subscriptions({"111": ["1"]}, False)
        await asyncio.wait_for(gate.resubscribe(), 1.0)
        self.assertEqual(ws.sent, [])

    async def test_each_subscription_is_logged_with_its_server_channel_count_and_size(self) -> None:
        ws = FakeWS([hello(10000), ready(), {"op": 7}])
        gate = self.build([ws])
        gate.set_subscriptions({"111": ["1", "2", "3"], "222": []}, False)
        with self.assertLogs("mirror.gateway", logging.INFO) as seen:
            await self.once(gate)
        sent = [p for p in ws.sent if p["op"] == 14]
        self.assertEqual(len(sent), 1)
        size = len(json.dumps(sent[0], separators=(",", ":")).encode())
        lines = [line for line in seen.output if "subscribing" in line]
        self.assertEqual(lines, [f"INFO:mirror.gateway:gateway subscribing server 111: 3 channel(s), {size} bytes"])

    async def test_a_session_that_raised_is_logged_with_the_error_and_the_next_step(self) -> None:
        ws = FakeWS([])
        gate = self.build([ws])
        gate.session_id = "s"
        gate.sequence = 4
        self.stop_at_first_wait(gate)
        with mock.patch.object(gw, "HELLO_WAIT", 0.05):
            line = await self.ended(gate)
        self.assertIn("RuntimeError('gateway hello missing')", line)
        self.assertIn("next resume", line)


if __name__ == "__main__":
    unittest.main()
