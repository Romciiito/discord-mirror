from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from aiohttp import web

from .web import create_app


class FileOnlyHandler(logging.handlers.RotatingFileHandler):
    """The rotating log file; it never writes to stderr, which is the CLI's screen."""

    def doRollover(self) -> None:
        try:
            super().doRollover()
        except OSError:
            # Windows cannot rename a file another process holds open (a second instance on the same data
            # directory, a log viewer, a scanner): keep appending to the current file, retry on the next record
            if self.stream is None:
                self.stream = self._open()

    def handleError(self, record: logging.LogRecord) -> None:
        pass  # the stock handler prints a traceback to stderr; a record that cannot be written is dropped


def configure_logging(data_dir: str) -> None:
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    handler = FileOnlyHandler(
        Path(data_dir) / "mando.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    root.addHandler(handler)
    logging.getLogger("aiohttp.access").propagate = False


def wants_cli() -> bool:
    # a closed fd 0 or 1 leaves sys.stdin or sys.stdout None: no terminal, so the server alone
    if not all(stream is not None and stream.isatty() for stream in (sys.stdin, sys.stdout)):
        return False
    return has_console()


def has_console() -> bool:
    """Whether the TTYs are a console. Windows calls the NUL device a TTY (isatty() is True for
    `< NUL > NUL`), and prompt_toolkit then stops with NoConsoleScreenBufferError before the server runs.
    POSIX isatty() already says no for /dev/null."""
    if os.name != "nt":
        return True
    import ctypes
    import msvcrt
    from ctypes import wintypes

    try:
        # the screen buffer behind the standard output handle, the one prompt_toolkit's Win32Output asks for
        # (os.get_terminal_size on Windows calls GetConsoleScreenBufferInfo on GetStdHandle)
        os.get_terminal_size(1)
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    except (OSError, ValueError):
        return False
    # a console input handle; a file, a pipe or NUL is refused (get_terminal_size(0) fails on a real one too)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetConsoleMode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetConsoleMode.restype = wintypes.BOOL
    return bool(kernel32.GetConsoleMode(handle, ctypes.byref(wintypes.DWORD())))


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
