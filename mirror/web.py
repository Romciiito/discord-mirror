from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import web

from .discord_api import ApiError
from .engine import Engine
from .keychain import read_keychain
from .store import Store

log = logging.getLogger("mirror.web")
STATIC = Path(__file__).resolve().parent.parent / "static"


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
    except Exception:
        log.exception("request failed")
        return web.json_response({"error": "request failed"}, status=500)


async def index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html")


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


async def copy_guild(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    report = await engine.copy_guild(request.match_info["guild_id"])
    body = engine.snapshot()
    body["report"] = report
    return web.json_response(body)


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
    engine.listeners.add(queue)
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
        await response.write(b": ok\n\n")
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                await response.write(b": ping\n\n")
                continue
            payload = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            await response.write(f"data: {payload}\n\n".encode())
    finally:
        engine.listeners.discard(queue)
    return response


async def _json(request: web.Request) -> dict:
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


async def _stop(app: web.Application) -> None:
    await app["engine"].close()


def create_app(data_dir: str) -> web.Application:
    app = web.Application(middlewares=[guard], client_max_size=1024 * 512)
    app["engine"] = Engine(Store(data_dir))
    app.on_startup.append(_start)
    app.on_cleanup.append(_stop)
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC, show_index=False)
    app.router.add_get("/api/state", state)
    app.router.add_get("/api/feed", feed)
    app.router.add_post("/api/session", open_session)
    app.router.add_delete("/api/session", close_session)
    app.router.add_get("/api/guilds", guilds)
    app.router.add_get("/api/guilds/{guild_id}/channels", channels)
    app.router.add_post("/api/guilds/{guild_id}/copy", copy_guild)
    app.router.add_put("/api/setup", save_setup)
    app.router.add_post("/api/start", start)
    app.router.add_post("/api/stop", stop)
    app.router.add_get("/api/events", events)
    return app
