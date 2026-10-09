from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlsplit

from aiohttp import web

from .discord_api import ApiError
from .engine import Engine
from .keychain import read_keychain
from .store import Store

log = logging.getLogger("mirror.web")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
PING = 15.0


def hostname(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else value[1:]
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value


@web.middleware
async def origin_guard(request: web.Request, handler):
    host, _port = request.app["bind"]
    if hostname(host) in LOOPBACK and hostname(request.host) not in LOOPBACK:
        return web.json_response({"error": "bad host"}, status=403)
    if request.path.startswith("/api/"):
        origin = request.headers.get("Origin")
        if origin is not None and (origin == "null" or _netloc(origin) != request.host.casefold()):
            return web.json_response({"error": "bad origin"}, status=403)
    return await handler(request)


def _netloc(origin: str) -> str:
    try:
        return urlsplit(origin).netloc.casefold()
    except ValueError:
        return ""


@web.middleware
async def guard(request: web.Request, handler):
    try:
        return await handler(request)
    except ApiError as exc:
        status = exc.status if 400 <= exc.status < 500 else 400
        return web.json_response({"error": str(exc)}, status=status)
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except asyncio.CancelledError:
        raise
    except web.HTTPException:
        raise
    except Exception:
        log.exception("request failed")
        return web.json_response({"error": "request failed"}, status=500)


async def index(_request: web.Request) -> web.Response:
    return web.Response(text="UI unavailable now", content_type="text/plain")


async def state(request: web.Request) -> web.Response:
    return web.json_response(request.app["engine"].snapshot())


async def feed(request: web.Request) -> web.Response:
    return web.json_response({"messages": request.app["engine"].feed_items()})


async def open_session(request: web.Request) -> web.Response:
    body = await _json(request)
    engine: Engine = request.app["engine"]
    token = str(body.get("token") or "")
    if not token and (body.get("keychain_service") or body.get("keychain_account")):
        token = await read_keychain(str(body.get("keychain_service") or ""), str(body.get("keychain_account") or ""))
    if not token:
        raise ApiError(400, "paste a token or use the keychain fields")
    user = await engine.use_token(token, bool(body.get("keep", True)))
    engine.note(f"signed in as {user.get('global_name') or user.get('username') or user.get('id')}")
    return web.json_response(engine.snapshot())


async def close_session(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    await engine.forget()
    return web.json_response(engine.snapshot())


async def guilds(request: web.Request) -> web.Response:
    return web.json_response({"guilds": await request.app["engine"].guilds()})


async def channels(request: web.Request) -> web.Response:
    return web.json_response({"channels": await request.app["engine"].channels(request.match_info["guild_id"])})


async def targets(request: web.Request) -> web.Response:
    return web.json_response({"targets": await request.app["engine"].owned_guilds()})


async def fill_copy(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    body = await _json(request)
    report = await engine.fill_copy(request.match_info["guild_id"], str(body.get("target") or ""))
    out = engine.snapshot()
    out["report"] = report
    return web.json_response(out)


async def save_setup(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    engine.save_setup(await _json(request))
    if engine.running:
        await engine.refresh()
    return web.json_response(engine.snapshot())


async def start(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    await engine.start()
    return web.json_response(engine.snapshot())


async def stop(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    await engine.stop()
    return web.json_response(engine.snapshot())


async def events(request: web.Request) -> web.StreamResponse:
    engine: Engine = request.app["engine"]
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)
    try:
        # registered only once the headers went out, inside the try: a client gone before them leaves no queue
        engine.listeners.add(queue)
        request.app["streams"].add(queue)
        await response.write(b": ok\n\n")
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=PING)
            except asyncio.TimeoutError:
                if queue not in engine.listeners:
                    break
                await response.write(b": ping\n\n")
                continue
            if item is None:
                break  # _end_streams: the server is shutting down
            payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            await response.write(f"data: {payload}\n\n".encode())
            if queue not in engine.listeners:
                break
    finally:
        engine.listeners.discard(queue)
        request.app["streams"].discard(queue)
    return response


async def _json(request: web.Request) -> dict:
    if request.content_type != "application/json":
        raise ApiError(415, "expected json")
    try:
        body = await request.json()
    except Exception as exc:
        raise ApiError(400, "expected json") from exc
    if not isinstance(body, dict):
        raise ApiError(400, "expected json")
    return body


async def _start(app: web.Application) -> None:
    await app["engine"].open()
    await app["engine"].restore()


async def _end_streams(app: web.Application) -> None:
    """End every idle /api/events stream at once, before runner.cleanup waits for in-flight handlers, so a connected
    client does not hold the exit for the runner's shutdown_timeout before Engine.close runs. A handler blocked in
    response.write (a client that stopped reading) does not see the sentinel: runner.cleanup cancels it once its
    shutdown_timeout ran out twice. The CLI's own listener queue is not one of these and is left alone."""
    for queue in list(app["streams"]):
        app["engine"].listeners.discard(queue)
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            pass  # the handler ends after its next item, since its queue is no longer a listener


async def _stop(app: web.Application) -> None:
    await app["engine"].close()


def create_app(data_dir: str, host: str = "127.0.0.1", port: int = 8765) -> web.Application:
    app = web.Application(middlewares=[origin_guard, guard], client_max_size=1024 * 512)
    app["bind"] = (host, port)
    app["engine"] = Engine(Store(data_dir))
    app["streams"] = set()
    app.on_startup.append(_start)
    app.on_shutdown.append(_end_streams)
    app.on_cleanup.append(_stop)
    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/feed", feed)
    app.router.add_post("/api/session", open_session)
    app.router.add_delete("/api/session", close_session)
    app.router.add_get("/api/guilds", guilds)
    app.router.add_get("/api/guilds/{guild_id}/channels", channels)
    app.router.add_get("/api/targets", targets)
    app.router.add_post("/api/guilds/{guild_id}/fill", fill_copy)
    app.router.add_put("/api/setup", save_setup)
    app.router.add_post("/api/start", start)
    app.router.add_post("/api/stop", stop)
    app.router.add_get("/api/events", events)
    return app
