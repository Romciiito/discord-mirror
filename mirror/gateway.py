from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import zlib
from typing import Any, Awaitable, Callable

import aiohttp

from .discord_api import CAPABILITIES, DiscordHTTP

log = logging.getLogger("mirror.gateway")

Dispatch = Callable[[str, dict[str, Any]], Awaitable[None]]

RECONNECT = 4000
HELLO_WAIT = 20.0
STOP_WAIT = 8.0
FORGET = frozenset({4007, 4009})
REFUSED = frozenset({4010, 4011, 4012, 4013, 4014})


def ws_timeout() -> Any:
    kind = getattr(aiohttp, "ClientWSTimeout", None)
    if kind is None:
        return 5.0
    return kind(ws_receive=None, ws_close=5.0)


class Inflate:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.context = zlib.decompressobj()

    def feed(self, data: bytes) -> str | None:
        self.buffer.extend(data)
        if len(data) < 4 or data[-4:] != b"\x00\x00\xff\xff":
            return None
        raw = self.context.decompress(self.buffer)
        self.buffer = bytearray()
        return raw.decode("utf-8")


class Gateway:
    def __init__(self, http: DiscordHTTP, on_dispatch: Dispatch, on_fatal: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.http = http
        self.on_dispatch = on_dispatch
        self.on_fatal = on_fatal
        self.session_id: str | None = None
        self.sequence: int | None = None
        self.resume_url: str | None = None
        self._stop = asyncio.Event()
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._channels: dict[str, list[str]] = {}
        self._threads = False
        self._sub_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._acked = True
        self._healthy = False

    def set_subscriptions(self, guild_channels: dict[str, list[str]], threads: bool) -> None:
        self._channels = {str(k): [str(c) for c in v] for k, v in guild_channels.items()}
        self._threads = threads

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        ws = self._ws
        if ws is not None and not ws.closed:
            await ws.close(code=1000)
        task, self._task = self._task, None
        if task is None:
            return
        # waited on, never its result(): an error the task already ended with is logged here instead of aborting
        # Engine.stop before Engine.close closes the session and the store
        if not task.done():
            try:
                await asyncio.wait({task}, timeout=STOP_WAIT)
            except asyncio.CancelledError:
                task.cancel()
                raise
        if not task.done():
            task.cancel()
            await asyncio.wait({task})
        if not task.cancelled() and task.exception() is not None:
            log.warning("gateway task ended with an error", exc_info=task.exception())

    async def resubscribe(self) -> None:
        async with self._sub_lock:
            ws = self._ws
            if ws is None or ws.closed:
                return
            await self._send_subscriptions(ws)

    async def _wait(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            self._healthy = False
            try:
                await self._once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("gateway session ended")
            if self._stop.is_set():
                return
            if self._healthy:
                delay = 1.0
            await self._wait(delay + random.random())
            delay = min(delay * 2, 20.0)

    async def _drop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        if ws.closed:
            return
        try:
            await ws.close(code=RECONNECT)
        except Exception:
            pass

    async def _fail(self, ws: aiohttp.ClientWebSocketResponse, text: str) -> None:
        await self._drop(ws)
        self._stop.set()
        if self.on_fatal is not None:
            await self.on_fatal(text)

    def _forget(self) -> None:
        self.session_id = None
        self.sequence = None
        self.resume_url = None

    def _patience(self, interval: float) -> float:
        return interval * 2 + 10

    async def _once(self) -> None:
        base = self.resume_url or await self.http.gateway_url()
        url = f"{base}?v=9&encoding=json&compress=zlib-stream"
        headers = {
            "Origin": "https://discord.com",
            "User-Agent": self.http.properties["browser_user_agent"],
        }
        async with self.http.session.ws_connect(
            url,
            headers=headers,
            autoping=True,
            autoclose=False,
            heartbeat=None,
            max_msg_size=0,
            timeout=ws_timeout(),
            compress=0,
        ) as ws:
            self._ws = ws
            self._acked = True
            hb: asyncio.Task | None = None
            try:
                inflator = Inflate()
                hello = await self._recv_json(ws, inflator, HELLO_WAIT)
                if not hello or hello.get("op") != 10:
                    raise RuntimeError("gateway hello missing")
                interval = float(hello["d"]["heartbeat_interval"]) / 1000.0
                hb = asyncio.create_task(self._heartbeat(ws, interval))
                await self._identify_or_resume(ws)
                await self._read_loop(ws, inflator, interval)
                if self._stop.is_set():
                    return
                code = ws.close_code
                if code == 4004:
                    await self._fail(ws, "token was rejected")
                elif code in REFUSED:
                    await self._fail(ws, "gateway refused the session")
                elif code in FORGET:
                    self._forget()
            finally:
                if hb is not None:
                    hb.cancel()
                    try:
                        await hb
                    except asyncio.CancelledError:
                        pass
                self._ws = None
                if not self._stop.is_set():
                    await self._drop(ws)

    async def _identify_or_resume(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        if self.session_id and self.sequence is not None and self.resume_url:
            await self._send(
                ws,
                {
                    "op": 6,
                    "d": {
                        "token": self.http.token,
                        "session_id": self.session_id,
                        "seq": self.sequence,
                    },
                },
            )
            return
        props = dict(self.http.properties)
        await self._send(
            ws,
            {
                "op": 2,
                "d": {
                    "token": self.http.token,
                    "capabilities": CAPABILITIES,
                    "properties": props,
                    "presence": {"status": "online", "since": 0, "activities": [], "afk": False},
                    "compress": False,
                    "client_state": {"guild_versions": {}},
                },
            },
        )

    async def _heartbeat(self, ws: aiohttp.ClientWebSocketResponse, interval: float) -> None:
        try:
            await self._beat(ws)
            while not ws.closed and not self._stop.is_set():
                await asyncio.sleep(interval)
                if ws.closed or self._stop.is_set():
                    return
                if not self._acked:
                    log.warning("heartbeat not acked")
                    await self._drop(ws)
                    return
                await self._beat(ws)
        except Exception:
            return

    async def _beat(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._acked = False
        await self._send(
            ws,
            {
                "op": 40,
                "d": {
                    "qos": {"ver": 27, "active": True, "reasons": ["foregrounded"]},
                    "seq": self.sequence,
                },
            },
        )

    async def _read_loop(self, ws: aiohttp.ClientWebSocketResponse, inflator: Inflate, interval: float | None = None) -> None:
        limit = self._patience(interval) if interval else None
        while not self._stop.is_set():
            packet = await self._recv_json(ws, inflator, limit)
            if packet is None:
                return
            op = packet.get("op")
            data = packet.get("d")
            seq = packet.get("s")
            if seq is not None:
                self.sequence = seq
            if op == 11:
                self._acked = True
                continue
            if op == 1:
                await self._beat(ws)
                continue
            if op == 7:
                return
            if op == 9:
                if data is not True:
                    self._forget()
                return
            if op != 0:
                continue
            event = packet.get("t") or ""
            payload = data if isinstance(data, dict) else {}
            if event == "READY":
                self.session_id = payload.get("session_id")
                url = payload.get("resume_gateway_url")
                if url:
                    self.resume_url = str(url)
                await self._send(
                    ws,
                    {
                        "op": 41,
                        "d": {
                            "initialization_timestamp": int(time.time() * 1000),
                            "session_id": self.http.properties.get("client_heartbeat_session_id"),
                            "client_launch_id": self.http.properties.get("client_launch_id"),
                        },
                    },
                )
                self._healthy = True
                await self.resubscribe()
                log.info("gateway ready")
            elif event == "RESUMED":
                self._healthy = True
                await self.resubscribe()
                log.info("gateway resumed")
            try:
                await self.on_dispatch(event, payload)
            except Exception:
                log.exception("dispatch failed for %s", event)

    async def _send_subscriptions(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        for guild_id, channels in self._channels.items():
            if not channels:
                continue
            await self._send(
                ws,
                {
                    "op": 14,
                    "d": {
                        "guild_id": guild_id,
                        "typing": False,
                        "threads": self._threads,
                        "activities": False,
                        "members": [],
                        "channels": {channel_id: [[0, 99]] for channel_id in channels},
                    },
                },
            )
            await asyncio.sleep(0.25)

    async def _recv_json(self, ws: aiohttp.ClientWebSocketResponse, inflator: Inflate, timeout: float | None = None) -> dict | None:
        while not ws.closed:
            try:
                msg = await ws.receive(timeout=timeout)
            except asyncio.TimeoutError:
                return None
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                return None
            if msg.type == aiohttp.WSMsgType.ERROR:
                return None
            text = None
            if msg.type == aiohttp.WSMsgType.BINARY:
                text = inflator.feed(msg.data)
            elif msg.type == aiohttp.WSMsgType.TEXT:
                text = msg.data
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None

    async def _send(self, ws: aiohttp.ClientWebSocketResponse, payload: dict) -> None:
        if ws.closed:
            return
        raw = json.dumps(payload, separators=(",", ":"))
        async with self._send_lock:
            if ws.closed:
                return
            await ws.send_str(raw)
