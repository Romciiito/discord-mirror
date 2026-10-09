"""The terminal layer: keys in, lines out. Every behaviour lives in the controller and the renderer."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from prompt_toolkit.application import Application, get_app
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


def build(controller: Controller) -> Application:
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

    @bindings.add(Keys.BracketedPaste)
    async def _paste(event: Any) -> None:
        await controller.paste(event.data.replace("\r\n", "\n").replace("\r", "\n"))
        event.app.invalidate()

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
