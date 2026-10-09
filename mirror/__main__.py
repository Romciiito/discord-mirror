from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Awaitable, Callable

from aiohttp import web

from .web import create_app

class ListenError(OSError):
    """The API server could not listen on host:port; any other OSError is not reported as one."""


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


async def _serve(
    app: web.Application, host: str, port: int, body: Callable[[], Awaitable[None]], handle_signals: bool
) -> None:
    """The API server on host:port while body runs. Only a failed listen becomes a ListenError; any other
    OSError, from the app's startup or from body, keeps its traceback. With handle_signals, SIGINT and SIGTERM
    raise GracefulExit on POSIX as under web.run_app, and the cancelled body still reaches runner.cleanup."""
    runner = web.AppRunner(app, access_log=None, handle_signals=handle_signals)
    await runner.setup()
    try:
        # inside the try as in web.run_app: a port in use still closes the engine
        try:
            await web.TCPSite(runner, host, port).start()
        except OSError as exc:
            raise ListenError(exc.errno, exc.strerror or str(exc)) from exc
        await body()
    finally:
        await runner.cleanup()


def _run(
    app: web.Application, host: str, port: int, body: Callable[[], Awaitable[None]], handle_signals: bool = False
) -> None:
    try:
        asyncio.run(_serve(app, host, port, body, handle_signals))
    except (KeyboardInterrupt, web.GracefulExit):
        # Ctrl+C before the CLI took the terminal, or SIGINT/SIGTERM on the server alone; web.run_app ends
        # the same way, with code 0
        pass


async def _forever() -> None:
    await asyncio.Event().wait()


def serve(app: web.Application, host: str, port: int) -> None:
    """The API server alone, until the process is stopped."""
    _run(app, host, port, _forever, handle_signals=True)


def serve_and_cli(app: web.Application, host: str, port: int) -> None:
    from .cli.app import run_cli

    # prompt_toolkit binds SIGINT itself while the CLI runs
    _run(app, host, port, lambda: run_cli(app["engine"]), handle_signals=False)


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    data = os.environ.get("DATA_DIR")
    if not data:
        data = str(Path(__file__).resolve().parent.parent / "data")
    configure_logging(data)
    app = create_app(data, host, port)
    try:
        if wants_cli():
            serve_and_cli(app, host, port)
        else:
            serve(app, host, port)
    except ListenError as exc:
        # a failed listen: one line instead of a traceback, after the engine was closed
        message = f"could not listen on {host}:{port}: {exc.strerror or exc}"
        logging.getLogger("mirror").error(message)
        print(message, file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
