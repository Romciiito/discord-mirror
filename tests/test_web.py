from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import logging.handlers
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

import mirror.__main__ as entry
import mirror.web as webmod
from mirror.web import create_app, hostname

ROOT = Path(__file__).resolve().parent.parent


class HostnameTests(unittest.TestCase):
    def test_hostname_forms(self) -> None:
        self.assertEqual(hostname("localhost:8765"), "localhost")
        self.assertEqual(hostname("[::1]:8765"), "::1")
        self.assertEqual(hostname("[::1]"), "::1")
        self.assertEqual(hostname("::1"), "::1")
        self.assertEqual(hostname("LocalHost"), "localhost")
        self.assertEqual(hostname("127.0.0.1"), "127.0.0.1")
        self.assertEqual(hostname("attacker.example:8765"), "attacker.example")


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.clients: list[TestClient] = []

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            await client.close()
        self.tmp.cleanup()

    async def client(self, host: str = "127.0.0.1") -> TestClient:
        app = create_app(self.tmp.name, host, 8765)
        client = TestClient(TestServer(app, host="127.0.0.1"))
        await client.start_server()
        self.clients.append(client)
        return client

    def host_of(self, client: TestClient) -> str:
        return f"{client.server.host}:{client.server.port}"

    async def test_404_is_not_500(self) -> None:
        client = await self.client()
        with self.assertNoLogs("mirror.web", level="ERROR"):
            resp = await client.get("/favicon.ico")
            self.assertEqual(resp.status, 404)
            resp = await client.get("/api/nope")
            self.assertEqual(resp.status, 404)
            resp = await client.get("/api/start")
            self.assertEqual(resp.status, 405)

    async def test_cross_origin_is_rejected(self) -> None:
        client = await self.client()
        resp = await client.post("/api/stop", headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status, 403)
        self.assertEqual(await resp.json(), {"error": "bad origin"})
        resp = await client.post("/api/stop", headers={"Origin": "null"})
        self.assertEqual(resp.status, 403)
        resp = await client.post("/api/stop", headers={"Origin": "http://" + self.host_of(client)})
        self.assertEqual(resp.status, 200)
        resp = await client.post("/api/stop")
        self.assertEqual(resp.status, 200)
        resp = await client.get("/", headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status, 200)

    async def test_foreign_host_is_rejected_on_loopback(self) -> None:
        client = await self.client()
        resp = await client.get("/api/state", headers={"Host": "attacker.example:8765"})
        self.assertEqual(resp.status, 403)
        self.assertEqual(await resp.json(), {"error": "bad host"})
        resp = await client.get("/", headers={"Host": "attacker.example:8765"})
        self.assertEqual(resp.status, 403)
        resp = await client.get("/api/state", headers={"Host": "localhost:1234"})
        self.assertEqual(resp.status, 200)
        resp = await client.get("/api/state", headers={"Host": "[::1]:8765"})
        self.assertEqual(resp.status, 200)

    async def test_rebinding_origin_and_host_are_rejected(self) -> None:
        client = await self.client()
        headers = {"Host": "attacker.example:8765", "Origin": "http://attacker.example:8765"}
        resp = await client.get("/api/events", headers=headers)
        self.assertEqual(resp.status, 403)
        resp = await client.put("/api/setup", json={"backfill": 1}, headers=headers)
        self.assertEqual(resp.status, 403)

    async def test_non_loopback_bind_skips_host_check_but_keeps_origin(self) -> None:
        client = await self.client("0.0.0.0")
        resp = await client.get("/api/state", headers={"Host": "attacker.example"})
        self.assertEqual(resp.status, 200)
        resp = await client.post("/api/stop", headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status, 403)

    async def test_json_bodies_require_json_type(self) -> None:
        client = await self.client()
        resp = await client.put("/api/setup", data='{"backfill": 25}', headers={"Content-Type": "text/plain"})
        self.assertEqual(resp.status, 415)
        self.assertEqual(await resp.json(), {"error": "expected json"})
        resp = await client.put("/api/setup", data='{"backfill": 25}', headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["options"]["backfill"], 25)
        resp = await client.put("/api/setup", data="[]", headers={"Content-Type": "application/json"})
        self.assertEqual(resp.status, 400)
        resp = await client.post("/api/session", data='{"token": "x"}', headers={"Content-Type": "text/plain"})
        self.assertEqual(resp.status, 415)
        resp = await client.post("/api/session", data=b'{"token": "x"}')
        self.assertEqual(resp.status, 415)

    async def test_events_stream_ends_when_queue_dropped(self) -> None:
        client = await self.client()
        engine = client.server.app["engine"]
        with mock.patch.object(webmod, "PING", 0.05):
            resp = await client.get("/api/events", timeout=aiohttp.ClientTimeout(total=None))
            self.assertEqual(resp.status, 200)
            first = await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 1.0)
            self.assertEqual(first, b": ok\n\n")
            self.assertEqual(len(engine.listeners), 1)
            engine.listeners.clear()
            await asyncio.wait_for(resp.read(), 1.0)
            self.assertEqual(resp.status, 200)

    async def test_events_end_after_item_when_dropped(self) -> None:
        client = await self.client()
        engine = client.server.app["engine"]
        resp = await client.get("/api/events", timeout=aiohttp.ClientTimeout(total=None))
        await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 1.0)
        queue = next(iter(engine.listeners))
        engine.listeners.clear()
        queue.put_nowait({"kind": "log", "text": "last"})
        body = await asyncio.wait_for(resp.read(), 1.0)
        self.assertIn(b'"text":"last"', body)

    async def test_events_delivers_items(self) -> None:
        client = await self.client()
        engine = client.server.app["engine"]
        resp = await client.get("/api/events", timeout=aiohttp.ClientTimeout(total=None))
        await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 1.0)
        engine.note("hello")
        chunk = await asyncio.wait_for(resp.content.readuntil(b"\n\n"), 1.0)
        self.assertTrue(chunk.startswith(b"data: "))
        self.assertEqual(json.loads(chunk[6:].decode()), {"kind": "log", "text": "hello"})
        resp.close()

    async def test_cleanup_closes_store(self) -> None:
        client = await self.client()
        engine = client.server.app["engine"]
        resp = await client.get("/api/state")
        self.assertEqual(resp.status, 200)
        await client.close()
        self.assertIsNone(engine.session)
        with self.assertRaises(sqlite3.ProgrammingError):
            engine.store.conn.execute("SELECT 1")

    async def test_data_dir_with_spaces(self) -> None:
        data = os.path.join(self.tmp.name, "my data")
        app = create_app(data, "127.0.0.1", 8765)
        client = TestClient(TestServer(app, host="127.0.0.1"))
        await client.start_server()
        self.clients.append(client)
        resp = await client.get("/api/state")
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(set(body), {"user", "running", "status", "has_token", "options", "selection", "log", "mirrored"})
        self.assertFalse(body["has_token"])
        self.assertEqual(app["engine"].store.path, Path(data) / "state.db")
        self.assertTrue(os.path.isfile(os.path.join(data, "state.db")))

    async def test_root_says_the_ui_is_unavailable_and_static_is_gone(self) -> None:
        client = await self.client()
        resp = await client.get("/")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.content_type, "text/plain")
        self.assertEqual(await resp.text(), "UI unavailable now")
        resp = await client.get("/static/app.js")
        self.assertEqual(resp.status, 404)


class MainTests(unittest.TestCase):
    """configure_logging opens a file under DATA_DIR; every test closes the handlers it added, or
    TemporaryDirectory cannot delete the open file on Windows."""

    def setUp(self) -> None:
        self.root = logging.getLogger()
        self.before = list(self.root.handlers)
        self.level = self.root.level
        self.access = logging.getLogger("aiohttp.access").propagate

    def tearDown(self) -> None:
        for handler in list(self.root.handlers):
            if handler not in self.before:
                self.root.removeHandler(handler)
                handler.close()
        self.root.setLevel(self.level)
        logging.getLogger("aiohttp.access").propagate = self.access

    def test_main_runs_server_only_without_tty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"HOST": "0.0.0.0", "PORT": "9000", "DATA_DIR": tmp}
            with mock.patch.dict(os.environ, env), mock.patch.object(entry, "wants_cli", return_value=False), mock.patch.object(
                entry, "create_app"
            ) as make, mock.patch.object(entry.web, "run_app") as run, mock.patch.object(entry, "serve_and_cli") as both:
                entry.main()
            make.assert_called_once_with(tmp, "0.0.0.0", 9000)
            run.assert_called_once()
            both.assert_not_called()
            self.assertIs(run.call_args.args[0], make.return_value)
            self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")
            self.assertEqual(run.call_args.kwargs["port"], 9000)
            self.assertIsNone(run.call_args.kwargs["access_log"])
            self.assertTrue(os.path.isfile(os.path.join(tmp, "mando.log")))
            self.tearDown()

    def test_main_runs_server_and_cli_with_a_tty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"DATA_DIR": tmp}
            with mock.patch.dict(os.environ, env), mock.patch.object(entry, "wants_cli", return_value=True), mock.patch.object(
                entry, "create_app"
            ) as make, mock.patch.object(entry.web, "run_app") as run, mock.patch.object(entry, "serve_and_cli") as both:
                entry.main()
            both.assert_called_once_with(make.return_value, "127.0.0.1", 8765)
            run.assert_not_called()
            self.tearDown()

    def test_wants_cli_needs_both_ttys(self) -> None:
        with mock.patch("sys.stdin") as stdin, mock.patch("sys.stdout") as stdout, mock.patch.object(
            entry, "has_console", return_value=True
        ):
            stdin.isatty.return_value = True
            stdout.isatty.return_value = False
            self.assertFalse(entry.wants_cli())
            stdout.isatty.return_value = True
            self.assertTrue(entry.wants_cli())

    def test_wants_cli_needs_a_console_behind_the_ttys(self) -> None:
        # Windows calls the NUL device a TTY: isatty() is True for `< NUL > NUL`
        with mock.patch("sys.stdin") as stdin, mock.patch("sys.stdout") as stdout, mock.patch.object(
            entry, "has_console", return_value=False
        ):
            stdin.isatty.return_value = True
            stdout.isatty.return_value = True
            self.assertFalse(entry.wants_cli())

    def test_the_null_device_is_no_terminal(self) -> None:
        code = "import sys\nimport mirror.__main__ as entry\nsys.stderr.write(repr(entry.wants_cli()))\n"
        done = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=dict(os.environ, PYTHONUTF8="1"),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr.strip(), "False")

    @unittest.skipUnless(os.name == "nt", "a console of its own is a Windows process flag")
    def test_a_real_console_still_runs_the_cli(self) -> None:
        # the child gets a console without a window, so its standard handles are a console's; it reports
        # through a file because a pipe on stdout would replace that console
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "wants_cli.txt")
            code = (
                "import sys\nimport mirror.__main__ as entry\n"
                "open(sys.argv[1], 'w').write(repr(entry.wants_cli()))\n"
            )
            subprocess.run(
                [sys.executable, "-c", code, out], cwd=ROOT, env=dict(os.environ, PYTHONUTF8="1"),
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=60,
            )
            with open(out, encoding="utf-8") as seen:
                self.assertEqual(seen.read(), "True")

    def test_without_a_terminal_the_api_server_runs(self) -> None:
        # story 36: stdin and stdout on the null device (a hidden start, a scheduler, `start /b` with
        # redirects) must still serve the API
        port = _free_port()
        tmp = tempfile.mkdtemp()
        self.addCleanup(_remove_when_free, tmp)
        env = dict(os.environ, HOST="127.0.0.1", DATA_DIR=os.path.join(tmp, "data"), PORT=str(port), PYTHONUTF8="1")
        err_path = os.path.join(tmp, "err.txt")
        with open(err_path, "wb") as err:
            proc = subprocess.Popen(
                [sys.executable, "-m", "mirror"], cwd=ROOT, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
            )
        try:
            status = asyncio.run(_poll_state(port, proc, 30.0))
        finally:
            proc.kill()
            proc.wait(10)
        with open(err_path, "rb") as err:
            self.assertEqual(status, 200, err.read().decode("utf-8", "replace")[-2000:])

    def test_wants_cli_without_stdin_is_false(self) -> None:
        # Python sets sys.stdin to None when fd 0 is closed (`python -m mirror <&-`); the server
        # must still start, as it did before the CLI existed
        with mock.patch("sys.stdin", None), mock.patch("sys.stdout") as stdout:
            stdout.isatty.return_value = True
            self.assertFalse(entry.wants_cli())

    def test_logging_goes_to_the_file_not_the_console(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            entry.configure_logging(tmp)
            added = [h for h in self.root.handlers if h not in self.before]
            self.assertEqual(len(added), 1)
            self.assertIsInstance(added[0], logging.handlers.RotatingFileHandler)
            self.assertFalse(logging.getLogger("aiohttp.access").propagate)
            self.tearDown()

    def test_a_failed_rotation_keeps_logging_to_the_file_and_off_stderr(self) -> None:
        # Windows cannot rename a log file another process holds open (a second instance on the same
        # DATA_DIR, a log viewer, a scanner); the stock handler then prints a traceback to stderr, over
        # the CLI's screen, for every record and drops the record. POSIX renames an open file, so there
        # the refusal is simulated.
        with tempfile.TemporaryDirectory() as tmp:
            entry.configure_logging(tmp)
            (handler,) = [h for h in self.root.handlers if h not in self.before]
            handler.maxBytes = 200
            path = os.path.join(tmp, "mando.log")
            log = logging.getLogger("mirror.rotation-test")
            err = io.StringIO()
            refused = (
                contextlib.nullcontext()
                if os.name == "nt"
                else mock.patch("os.rename", side_effect=PermissionError(13, "file in use"))
            )
            held = open(path, "rb")
            try:
                with contextlib.redirect_stderr(err), refused:
                    for n in range(20):
                        log.warning("record %d %s", n, "x" * 50)
            finally:
                held.close()
            self.assertEqual(err.getvalue(), "")
            self.assertFalse(os.path.exists(path + ".1"))
            with open(path, encoding="utf-8") as kept:
                self.assertIn("record 19 ", kept.read())
            # once the other handle is gone the next record rotates as usual
            log.warning("after the holder let go")
            self.assertTrue(os.path.exists(path + ".1"))
            self.tearDown()

    def test_server_only_path_runs_without_prompt_toolkit(self) -> None:
        # the CI smoke step and scripts start `python -m mirror` without a terminal; that path must not
        # need the CLI's library, so it is blocked here (None in sys.modules makes the import fail)
        code = (
            "import sys\n"
            "sys.modules['prompt_toolkit'] = None\n"
            "from unittest import mock\n"
            "import mirror.__main__ as entry\n"
            "with mock.patch.object(entry.web, 'run_app') as run:\n"
            "    entry.main()\n"
            "print(run.call_count)\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, DATA_DIR=tmp, PYTHONUTF8="1")
            done = subprocess.run(
                [sys.executable, "-c", code], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout.strip(), "1")
            self.assertTrue(os.path.isfile(os.path.join(tmp, "mando.log")))


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _remove_when_free(path: str, timeout: float = 10.0) -> None:
    """Remove the data directory of a killed `python -m mirror`. A venv's python.exe on Windows is a launcher:
    the interpreter it started outlives the killed launcher for a moment and still holds mando.log."""
    end = time.monotonic() + timeout
    while True:
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if time.monotonic() > end:
                raise
            time.sleep(0.1)


async def _poll_state(port: int, proc: subprocess.Popen, timeout: float) -> int | None:
    """GET /api/state until it answers, the process dies or the time is up."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    async with aiohttp.ClientSession() as session:
        while loop.time() < end and proc.poll() is None:
            try:
                async with session.get(f"http://127.0.0.1:{port}/api/state") as resp:
                    return resp.status
            except aiohttp.ClientError:
                await asyncio.sleep(0.3)
    return None


class ServeAndCliTests(unittest.TestCase):
    """serve_and_cli with the CLI replaced: the API server it runs next to the CLI and the cleanup after."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_the_api_answers_while_the_cli_runs_and_the_engine_closes_after(self) -> None:
        port = _free_port()
        app = create_app(self.tmp.name, "127.0.0.1", port)
        engine = app["engine"]
        seen: dict[str, Any] = {}

        async def cli(given: Any) -> None:
            seen["engine"] = given
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/api/state") as resp:
                    seen["state"] = resp.status

        with mock.patch("mirror.cli.app.run_cli", cli), mock.patch.object(
            engine, "close", wraps=engine.close
        ) as close, self.assertNoLogs("aiohttp.access"):
            entry.serve_and_cli(app, "127.0.0.1", port)
        self.assertIs(seen["engine"], engine)
        self.assertEqual(seen["state"], 200)
        close.assert_awaited_once()

    def test_a_port_in_use_still_closes_the_engine(self) -> None:
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            port = taken.getsockname()[1]
            app = create_app(self.tmp.name, "127.0.0.1", port)
            engine = app["engine"]
            with mock.patch("mirror.cli.app.run_cli") as cli, mock.patch.object(engine, "close", wraps=engine.close) as close:
                with self.assertRaises(OSError):
                    entry.serve_and_cli(app, "127.0.0.1", port)
        cli.assert_not_called()
        close.assert_awaited_once()

    def test_ctrl_c_before_the_cli_took_the_terminal_ends_quietly(self) -> None:
        port = _free_port()
        app = create_app(self.tmp.name, "127.0.0.1", port)
        engine = app["engine"]

        async def cli(_engine: Any) -> None:
            raise KeyboardInterrupt

        with mock.patch("mirror.cli.app.run_cli", cli), mock.patch.object(engine, "close", wraps=engine.close) as close:
            try:
                entry.serve_and_cli(app, "127.0.0.1", port)
            except KeyboardInterrupt:
                self.fail("Ctrl+C while the CLI starts ends in a traceback")
        close.assert_awaited_once()


@contextlib.contextmanager
def _pipe_terminal():
    """A prompt_toolkit session on a pipe instead of a terminal: the test types into the pipe."""
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        yield pipe


async def _until(check: Any, timeout: float = 3.0) -> bool:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not check():
        if loop.time() > end:
            return False
        await asyncio.sleep(0.02)
    return True


async def _ended(task: asyncio.Future, timeout: float = 3.0) -> bool:
    """Whether the app ended in time. One that did not is cancelled together with every other task (a
    prompt_toolkit "Press ENTER" task keeps a cancelled app waiting), so a broken CLI fails the test
    instead of hanging the run."""
    done, _ = await asyncio.wait({task}, timeout=timeout)
    if not done:
        for other in asyncio.all_tasks():
            if other is not asyncio.current_task():
                other.cancel()
        await asyncio.wait({task}, timeout=timeout)
        return False
    task.result()
    return True


def _recording_controller() -> Any:
    from mirror.cli.controller import Controller
    from tests.test_cli_controller import FakeEngine

    class Recording(Controller):
        def __init__(self, engine: Any) -> None:
            super().__init__(engine)
            self.calls: list[tuple] = []

        async def press(self, key: str, plain: bool = False) -> None:
            self.calls.append(("press", key, plain))
            await super().press(key, plain)

        async def paste(self, text: str) -> None:
            self.calls.append(("paste", text))
            await super().paste(text)

    return Recording(FakeEngine())


class CliAppTests(unittest.IsolatedAsyncioTestCase):
    """The terminal layer driven through a pipe: keys in, controller calls out, rendered lines on screen."""

    async def test_keys_and_paste_reach_the_controller(self) -> None:
        from mirror.cli.app import build

        controller = _recording_controller()
        with _pipe_terminal() as pipe:
            app = build(controller)
            task = asyncio.ensure_future(app.run_async())
            for chunk in ["x", "\x1b[B", "\x1b[A", "\x1b[C", "\x1b[D", "\x7f", "\x08", "\t", "é"]:
                pipe.send_text(chunk)
            self.assertTrue(await _until(lambda: len(controller.calls) >= 8))
            pipe.send_text("\x1b")  # a lone Escape is told apart from a sequence after ttimeoutlen
            self.assertTrue(await _until(lambda: len(controller.calls) >= 9))
            pipe.send_text("\x1b[200~ab\r\ncd\re\x1b[201~")
            pipe.send_text("\r")
            self.assertTrue(await _until(lambda: len(controller.calls) >= 11))
            pipe.send_text("\x11")
            self.assertTrue(await _ended(task))
        self.assertEqual(
            controller.calls,
            [
                ("press", "x", True), ("press", "ArrowDown", False), ("press", "ArrowUp", False),
                ("press", "ArrowRight", False), ("press", "ArrowLeft", False), ("press", "Backspace", False),
                ("press", "Backspace", False), ("press", "é", True), ("press", "Escape", False),
                ("paste", "ab\ncd\ne"), ("press", "Enter", False),
            ],
        )

    async def test_ctrl_c_and_ctrl_q_end_the_cli(self) -> None:
        from mirror.cli.app import build

        for key in ("\x03", "\x11"):
            with self.subTest(key=key), _pipe_terminal() as pipe:
                task = asyncio.ensure_future(build(_recording_controller()).run_async())
                pipe.send_text(key)
                self.assertTrue(await _ended(task))

    async def test_sigint_ends_the_cli(self) -> None:
        # on POSIX prompt_toolkit turns SIGINT into a <sigint> key while it runs (on Windows Ctrl+C is a key)
        from mirror.cli.app import build

        with _pipe_terminal():
            app = build(_recording_controller())
            task = asyncio.ensure_future(app.run_async())
            self.assertTrue(await _until(lambda: app.is_running))
            app.key_processor.send_sigint()
            self.assertTrue(await _ended(task))

    async def test_the_screen_shows_the_rendered_lines_for_the_terminal_size(self) -> None:
        from mirror.cli.app import build
        from mirror.cli.render import render

        controller = _recording_controller()
        with _pipe_terminal() as pipe:
            app = build(controller)
            task = asyncio.ensure_future(app.run_async())
            self.assertTrue(await _until(lambda: app.is_running))
            size = app.output.get_size()
            content = app.layout.current_control.create_content(size.columns, size.rows)
            shown = ["".join(text for _style, text, *_rest in content.get_line(i)) for i in range(content.line_count)]
            pipe.send_text("\x11")
            self.assertTrue(await _ended(task))
        self.assertEqual(shown, render(controller, size.columns, size.rows))

    async def test_engine_events_reach_the_controller_and_the_listener_goes_at_exit(self) -> None:
        from mirror.cli import app as cliapp
        from mirror.cli.controller import Controller
        from tests.test_cli_controller import FakeEngine

        made: list[Controller] = []

        class Kept(Controller):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                made.append(self)

        engine = FakeEngine()
        with _pipe_terminal() as pipe, mock.patch.object(cliapp, "Controller", Kept):
            task = asyncio.ensure_future(cliapp.run_cli(engine))
            self.assertTrue(await _until(lambda: bool(engine.listeners)))
            for queue in list(engine.listeners):
                queue.put_nowait({"kind": "mirrored", "mirrored": 3})
            self.assertTrue(await _until(lambda: bool(made) and made[0].snap.get("mirrored") == 3))
            pipe.send_text("\x11")
            self.assertTrue(await _ended(task))
        self.assertEqual(engine.listeners, set())

    async def test_a_burst_of_engine_events_keeps_the_cli_subscribed(self) -> None:
        # Engine._emit drops a listener whose queue is full, and the CLI never subscribes again; a backfill
        # with mirror off emits an event per history message (up to 500) and a log line without yielding
        from mirror.cli import app as cliapp
        from mirror.cli.controller import Controller
        from mirror.engine import Engine
        from tests.test_cli_controller import FakeEngine

        made: list[Controller] = []

        class Kept(Controller):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                made.append(self)

        engine = FakeEngine()
        with _pipe_terminal() as pipe, mock.patch.object(cliapp, "Controller", Kept):
            task = asyncio.ensure_future(cliapp.run_cli(engine))
            self.assertTrue(await _until(lambda: bool(engine.listeners)))
            (queue,) = engine.listeners
            for n in range(1, 601):
                Engine._emit(engine, {"kind": "mirrored", "mirrored": n})  # the engine's own drop rule
            still = queue in engine.listeners
            reached = await _until(lambda: made[0].snap.get("mirrored") == 600)
            pipe.send_text("\x11")
            self.assertTrue(await _ended(task))
        self.assertTrue(still, "the engine dropped the CLI's listener")
        self.assertTrue(reached)

    async def test_an_event_the_controller_cannot_take_is_logged_and_the_next_one_arrives(self) -> None:
        # the CLI's queue has no bound, so a pump that died on one event would let it grow for the session
        from mirror.cli import app as cliapp
        from mirror.cli.controller import Controller
        from tests.test_cli_controller import FakeEngine

        made: list[Controller] = []

        class Picky(Controller):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                made.append(self)

            def on_event(self, item: dict[str, Any]) -> None:
                if item.get("bad"):
                    raise ValueError("cannot take it")
                super().on_event(item)

        engine = FakeEngine()
        with _pipe_terminal() as pipe, mock.patch.object(cliapp, "Controller", Picky), self.assertLogs(
            "mirror.cli", "ERROR"
        ) as logs:
            task = asyncio.ensure_future(cliapp.run_cli(engine))
            self.assertTrue(await _until(lambda: bool(engine.listeners)))
            (queue,) = engine.listeners
            queue.put_nowait({"kind": "mirrored", "mirrored": 1, "bad": True})
            queue.put_nowait({"kind": "mirrored", "mirrored": 2})
            reached = await _until(lambda: made[0].snap.get("mirrored") == 2)
            pipe.send_text("\x11")
            self.assertTrue(await _ended(task))
        self.assertTrue(reached)
        self.assertIn("cannot take it", "\n".join(logs.output))

    async def test_a_loop_error_goes_to_the_log_not_over_the_screen(self) -> None:
        # the engine, the gateway and the API server share the CLI's loop; prompt_toolkit's own handler
        # would print their unhandled errors over the screen and wait for Enter
        from prompt_toolkit.application import get_app

        from mirror.cli.app import run_cli
        from tests.test_cli_controller import FakeEngine

        engine = FakeEngine()
        printed = io.StringIO()
        with _pipe_terminal() as pipe, contextlib.redirect_stdout(printed), self.assertLogs("asyncio", "ERROR") as logs:
            task = asyncio.ensure_future(run_cli(engine))
            self.assertTrue(await _until(lambda: get_app().is_running))
            asyncio.get_running_loop().call_exception_handler(
                {"message": "engine task failed", "exception": RuntimeError("boom")}
            )
            await asyncio.sleep(0.2)
            pipe.send_text("\x11")
            ended = await _ended(task)
            self.assertEqual(printed.getvalue(), "")
            self.assertTrue(ended, "Ctrl+Q no longer ends the CLI")
        self.assertIn("engine task failed", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
