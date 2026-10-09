from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from aiohttp import web

from .web import create_app


def configure_logging(data_dir: str) -> None:
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        Path(data_dir) / "mando.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    root.addHandler(handler)
    logging.getLogger("aiohttp.access").propagate = False


def wants_cli() -> bool:
    # a closed fd 0 or 1 leaves sys.stdin or sys.stdout None: no terminal, so the server alone
    return all(stream is not None and stream.isatty() for stream in (sys.stdin, sys.stdout))


def serve_and_cli(app: web.Application, host: str, port: int) -> None:
    from .cli.app import run_cli

    async def run() -> None:
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            # inside the try as in web.run_app: a port in use still closes the engine
            await web.TCPSite(runner, host, port).start()
            await run_cli(app["engine"])
        finally:
            await runner.cleanup()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass  # Ctrl+C before the CLI took the terminal; web.run_app ends the same way


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    data = os.environ.get("DATA_DIR")
    if not data:
        data = str(Path(__file__).resolve().parent.parent / "data")
    configure_logging(data)
    app = create_app(data, host, port)
    if wants_cli():
        serve_and_cli(app, host, port)
    else:
        web.run_app(app, host=host, port=port, print=None, access_log=None)


if __name__ == "__main__":
    main()
