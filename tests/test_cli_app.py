from __future__ import annotations

import asyncio
import sys
import unittest
from typing import Callable

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mirror.cli.app import build, read_clipboard
from mirror.cli.controller import Controller

CTRL_V = "\x16"
# Shift+Insert as the Windows console sends it in VT input mode: CSI 2 ; 2 ~ (Insert with Shift)
SHIFT_INSERT = "\x1b[2;2~"
# a plain key sent last: once it reaches the controller, every key before it went through the bindings
END = "z"


class FakeController(Controller):
    """Records what the app hands it; the rest is a fresh controller, so the screen still renders."""

    def __init__(self) -> None:
        super().__init__(None)
        self.pasted: list[str] = []
        self.pressed: list[tuple[str, bool]] = []
        self.ended = asyncio.Event()

    async def paste(self, text: str) -> None:
        self.pasted.append(text)

    async def press(self, key: str, plain: bool = False) -> None:
        self.pressed.append((key, plain))
        if key == END:
            self.ended.set()


class FakeClipboard:
    def __init__(self, text: str) -> None:
        self.text = text
        self.reads = 0

    def __call__(self) -> str:
        self.reads += 1
        return self.text


async def send(clipboard: Callable[[], str], keys: str) -> FakeController:
    ui = FakeController()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = build(ui, clipboard=clipboard)
        running = asyncio.create_task(app.run_async())
        pipe.send_text(keys + END)
        try:
            await asyncio.wait_for(ui.ended.wait(), 5)
        finally:
            if app.is_running:
                app.exit()
            await running
    return ui


class PasteKeyTests(unittest.IsolatedAsyncioTestCase):
    async def test_ctrl_v_pastes_the_clipboard(self) -> None:
        clipboard = FakeClipboard("tok\r\nen\rx")
        ui = await send(clipboard, CTRL_V)
        self.assertEqual(clipboard.reads, 1)
        self.assertEqual(ui.pasted, ["tok\nen\nx"])
        self.assertEqual(ui.pressed, [(END, True)])

    async def test_shift_insert_pastes_the_clipboard(self) -> None:
        clipboard = FakeClipboard("tok\r\nen")
        ui = await send(clipboard, SHIFT_INSERT)
        self.assertEqual(clipboard.reads, 1)
        self.assertEqual(ui.pasted, ["tok\nen"])
        # the sequence is one key: no Esc and no "[2;2~" typed on the screen
        self.assertEqual(ui.pressed, [(END, True)])

    async def test_an_empty_clipboard_hands_the_controller_nothing(self) -> None:
        clipboard = FakeClipboard("")
        ui = await send(clipboard, CTRL_V + SHIFT_INSERT)
        self.assertEqual(clipboard.reads, 2)
        self.assertEqual(ui.pasted, [])
        self.assertEqual(ui.pressed, [(END, True)])

    async def test_bracketed_paste_still_pastes_its_own_text(self) -> None:
        clipboard = FakeClipboard("from the clipboard")
        ui = await send(clipboard, "\x1b[200~ab\r\ncd\x1b[201~")
        self.assertEqual(clipboard.reads, 0)
        self.assertEqual(ui.pasted, ["ab\ncd"])

    async def test_a_printable_key_is_pressed_as_plain(self) -> None:
        clipboard = FakeClipboard("tok")
        ui = await send(clipboard, "a")
        self.assertEqual(clipboard.reads, 0)
        self.assertEqual(ui.pasted, [])
        self.assertEqual(ui.pressed, [("a", True), (END, True)])


class ReadClipboardTests(unittest.TestCase):
    def test_returns_text_and_never_raises(self) -> None:
        # reads only; whatever the clipboard holds on this machine, the answer is a str
        self.assertIsInstance(read_clipboard(), str)

    @unittest.skipIf(sys.platform == "win32", "the Windows reader reads the real clipboard")
    def test_is_empty_off_windows(self) -> None:
        self.assertEqual(read_clipboard(), "")


if __name__ == "__main__":
    unittest.main()
