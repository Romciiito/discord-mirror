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
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=8)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None

    async def resubscribe(self) -> None:
        async with self._sub_lock:
            ws = self._ws
            if ws is None or ws.closed:
                return
            await self._send_subscriptions(ws)

    async def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                await self._once()
                delay = 1.0
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("gateway session ended")
            if self._stop.is_set():
                return
            await asyncio.sleep(delay + random.random())
            delay = min(delay * 2, 20.0)

    async def _once(self) -> None:
        base = self.resume_url or await self.http.gateway_url()
        url = f"{base}?v=9&encoding=json&compress=zlib-stream"
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=20)
        headers = {
            "Origin": "https://discord.com",
            "User-Agent": self.http.properties["browser_user_agent"],
        }
        async with self.http.session.ws_connect(
            url,
            headers=headers,
            autoping=True,
            heartbeat=None,
            max_msg_size=0,
            timeout=timeout,
            compress=0,
        ) as ws:
            self._ws = ws
            inflator = Inflate()
            hello = await self._recv_json(ws, inflator)
            if not hello or hello.get("op") != 10:
                raise RuntimeError("gateway hello missing")
            interval = float(hello["d"]["heartbeat_interval"]) / 1000.0
            hb = asyncio.create_task(self._heartbeat(ws, interval))
            try:
                await self._identify_or_resume(ws)
                await self._read_loop(ws, inflator)
                if ws.close_code == 4004:
                    self._stop.set()
                    if self.on_fatal is not None:
                        await self.on_fatal("token was rejected")
                    return
            finally:
                hb.cancel()
                self._ws = None
                try:
                    await hb
                except asyncio.CancelledError:
                    pass

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
                await self._beat(ws)
        except Exception:
            return

    async def _beat(self, ws: aiohttp.ClientWebSocketResponse) -> None:
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

    async def _read_loop(self, ws: aiohttp.ClientWebSocketResponse, inflator: Inflate) -> None:
        while not self._stop.is_set():
            packet = await self._recv_json(ws, inflator)
            if packet is None:
                return
            op = packet.get("op")
            data = packet.get("d")
            seq = packet.get("s")
            if seq is not None:
                self.sequence = seq
            if op == 11:
                continue
            if op == 1:
                await self._beat(ws)
                continue
            if op == 7:
                return
            if op == 9:
                if data is not True:
                    self.session_id = None
                    self.sequence = None
                    self.resume_url = None
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
                await self.resubscribe()
                log.info("gateway ready")
            elif event == "RESUMED":
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

    async def _recv_json(self, ws: aiohttp.ClientWebSocketResponse, inflator: Inflate) -> dict | None:
        while not ws.closed:
            msg = await ws.receive()
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
