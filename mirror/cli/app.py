"""The terminal layer: keys in, lines out. Every behaviour lives in the controller and the renderer."""
from __future__ import annotations

import asyncio
import ctypes
import functools
import logging
import sys
from typing import Any, Callable

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from .controller import Controller
from .render import render

log = logging.getLogger("mirror.cli")

KEY_NAMES = {
    "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
    "enter": "Enter", "escape": "Escape", "backspace": "Backspace",
}

# Shift+Insert as the Windows console sends it in VT input mode (CSI 2 ; 2 ~). prompt_toolkit 3.0.53 maps
# Insert (CSI 2 ~) but not this one and would read it as Esc followed by the typed text "[2;2~"
ANSI_SEQUENCES.setdefault("\x1b[2;2~", Keys.ShiftInsert)

CF_UNICODETEXT = 13


@functools.lru_cache(maxsize=None)
def _win32() -> tuple[Any, Any]:
    from ctypes import wintypes

    # own library objects: types set on the shared ctypes.windll ones would apply to every other caller too.
    # Handles are pointer-sized, so x86 and x64 need the types spelled out
    user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    return user32, kernel32


def read_clipboard() -> str:
    """The text on the Windows clipboard. "" when it holds no text, when another program keeps it open, and off
    Windows, where the terminal pastes by itself as bracketed paste."""
    if sys.platform != "win32":
        return ""
    user32, kernel32 = _win32()
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return ""
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def build(controller: Controller, clipboard: Callable[[], str] = read_clipboard) -> Application:
    bindings = KeyBindings()

    def text() -> str:
        size = get_app().output.get_size()
        return "\n".join(render(controller, size.columns, size.rows))

    def bind(name: str, key: str) -> None:
        @bindings.add(name, eager=True)
        async def _handler(event: Any) -> None:
            await controller.press(key)
            event.app.invalidate()

    for name, key in KEY_NAMES.items():
        bind(name, key)

    async def paste(event: Any, text: str) -> None:
        await controller.paste(text.replace("\r\n", "\n").replace("\r", "\n"))
        event.app.invalidate()

    @bindings.add(Keys.BracketedPaste)
    async def _paste(event: Any) -> None:
        await paste(event, event.data)

    # prompt_toolkit turns on VT input in the Windows console, and there the console hands Ctrl+V and Shift+Insert
    # to the app as keys instead of pasting; a right click, and Ctrl+Shift+V where enabled, still paste
    @bindings.add("c-v")
    @bindings.add(Keys.ShiftInsert)
    async def _paste_clipboard(event: Any) -> None:
        copied = clipboard()
        if copied:
            await paste(event, copied)

    @bindings.add(Keys.Any)
    async def _any(event: Any) -> None:
        data = event.data or ""
        if len(data) == 1 and data.isprintable():
            await controller.press(data, plain=True)
            event.app.invalidate()

    # in the app Ctrl+C is a key, not a signal; on POSIX prompt_toolkit turns a SIGINT sent to the process
    # into the <sigint> key, which nothing else handles
    @bindings.add("c-c", eager=True)
    @bindings.add("c-q", eager=True)
    @bindings.add("<sigint>")
    def _quit(event: Any) -> None:
        event.app.exit()

    app: Application = Application(
        layout=Layout(Window(FormattedTextControl(text=text), wrap_lines=False)),
        key_bindings=bindings,
        full_screen=True,
        mouse_support=False,
    )
    app.ttimeoutlen = 0.05
    return app


async def run_cli(engine: Any) -> None:
    controller = Controller(engine)
    await controller.load()
    app = build(controller)
    # no bound: Engine._emit drops a listener whose queue is full and the CLI never subscribes again, while a
    # backfill with mirror off emits up to 500 message events and a log line without yielding to this pump
    queue: asyncio.Queue = asyncio.Queue()
    engine.listeners.add(queue)

    async def pump() -> None:
        while True:
            item = await queue.get()
            try:
                controller.on_event(item)
            except Exception:
                # a pump that died here would leave the unbounded queue growing for the rest of the session
                log.exception("engine event failed")
            app.invalidate()

    pumping = asyncio.create_task(pump())
    try:
        # the engine, the gateway and the API server share this loop; prompt_toolkit's own exception handler
        # would print their unhandled errors over the screen and wait for Enter, the default one logs them
        await app.run_async(set_exception_handler=False)
    finally:
        pumping.cancel()
        engine.listeners.discard(queue)
