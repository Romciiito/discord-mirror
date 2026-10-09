from __future__ import annotations

import asyncio
import contextlib
import errno
import io
import json
import logging
import logging.handlers
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from typing import Any, Awaitable, Callable
from unittest import mock

import aiohttp
from aiohttp import web
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
            ) as make, mock.patch.object(entry, "serve") as run, mock.patch.object(entry, "serve_and_cli") as both:
                entry.main()
            make.assert_called_once_with(tmp, "0.0.0.0", 9000)
            run.assert_called_once_with(make.return_value, "0.0.0.0", 9000)
            both.assert_not_called()
            self.assertTrue(os.path.isfile(os.path.join(tmp, "mando.log")))
            self.tearDown()

    def test_main_runs_server_and_cli_with_a_tty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"DATA_DIR": tmp}
            with mock.patch.dict(os.environ, env), mock.patch.object(entry, "wants_cli", return_value=True), mock.patch.object(
                entry, "create_app"
            ) as make, mock.patch.object(entry, "serve") as run, mock.patch.object(entry, "serve_and_cli") as both:
                entry.main()
            both.assert_called_once_with(make.return_value, "127.0.0.1", 8765)
            run.assert_not_called()
            self.tearDown()

    def _headless(self, host: str, port: int, *patches: Any) -> tuple[Any, str, Any]:
        """main() without a terminal on a real app: (what it raised, its stderr, the engine's wrapped close)."""
        made: dict[str, Any] = {}
        err = io.StringIO()
        raised: Any = None
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:

            def make(*args: Any) -> Any:
                app = create_app(*args)
                made["close"] = stack.enter_context(
                    mock.patch.object(app["engine"], "close", wraps=app["engine"].close)
                )
                made["app"] = app
                return app

            env = {"HOST": host, "PORT": str(port), "DATA_DIR": tmp}
            stack.enter_context(mock.patch.dict(os.environ, env))
            stack.enter_context(mock.patch.object(entry, "wants_cli", return_value=False))
            stack.enter_context(mock.patch.object(entry, "create_app", side_effect=make))
            for patch in patches:
                stack.enter_context(patch(made))
            stack.enter_context(contextlib.redirect_stderr(err))
            # unittest shows aiohttp's NotAppKeyWarning from create_app on stderr; `python -m mirror` does not
            stack.enter_context(warnings.catch_warnings())
            warnings.simplefilter("ignore")
            try:
                entry.main()
            except BaseException as exc:  # noqa: BLE001 - the test inspects what main() ended with
                raised = exc
            if "app" in made:
                # a failed on_startup hook skips the cleanup, as in web.run_app: close the store for Windows
                made["app"]["engine"].store.close()
            self.tearDown()
        return raised, err.getvalue(), made.get("close")

    def test_a_port_in_use_without_a_tty_ends_with_one_line_and_code_1(self) -> None:
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            port = taken.getsockname()[1]
            with self.assertLogs("mirror", "ERROR") as logs:
                raised, err, close = self._headless("127.0.0.1", port)
        self.assertIsInstance(raised, SystemExit)
        self.assertEqual(raised.code, 1)
        lines = err.splitlines()
        self.assertEqual(len(lines), 1, err)
        self.assertTrue(lines[0].startswith(f"could not listen on 127.0.0.1:{port}: "), err)
        self.assertEqual(logs.records[-1].getMessage(), lines[0])
        close.assert_awaited_once()

    def test_an_unknown_host_without_a_tty_ends_with_one_line_and_code_1(self) -> None:
        # getaddrinfo fails with socket.gaierror, errno 11001 on Windows and -2 on Linux
        unknown = socket.gaierror(11001, "getaddrinfo failed")
        start = lambda made: mock.patch.object(entry.web.TCPSite, "start", side_effect=unknown)  # noqa: E731
        raised, err, close = self._headless("nosuch.invalid", 9000, start)
        self.assertIsInstance(raised, SystemExit)
        self.assertEqual(raised.code, 1)
        self.assertEqual(err, "could not listen on nosuch.invalid:9000: getaddrinfo failed\n")
        close.assert_awaited_once()

    def test_an_unbindable_address_without_a_tty_ends_with_one_line_and_code_1(self) -> None:
        # Python 3.12+ asyncio skips EADDRNOTAVAIL and raises an OSError without an errno (measured on 3.14)
        missing = OSError("could not bind on any address out of [('10.255.255.1', 9000)]")
        start = lambda made: mock.patch.object(entry.web.TCPSite, "start", side_effect=missing)  # noqa: E731
        raised, err, _close = self._headless("10.255.255.1", 9000, start)
        self.assertIsInstance(raised, SystemExit)
        self.assertEqual(raised.code, 1)
        self.assertEqual(
            err, "could not listen on 10.255.255.1:9000: could not bind on any address out of [('10.255.255.1', 9000)]\n"
        )

    def test_an_os_error_from_the_server_startup_without_a_tty_is_not_reported_as_could_not_listen(self) -> None:
        # an errno-less OSError (TimeoutError on 3.11+) from an on_startup hook keeps its traceback
        late = TimeoutError("open timed out")
        opening = lambda made: mock.patch("mirror.engine.Engine.open", side_effect=late)  # noqa: E731
        raised, err, _close = self._headless("127.0.0.1", _free_port(), opening)
        self.assertIs(raised, late)
        self.assertNotIn("could not listen", err)

    def test_an_os_error_while_serving_without_a_tty_is_not_reported_as_could_not_listen(self) -> None:
        async def broken() -> None:
            raise OSError(errno.EIO, "boom")

        body = lambda made: mock.patch.object(entry, "_forever", broken)  # noqa: E731
        raised, err, close = self._headless("127.0.0.1", _free_port(), body)
        self.assertIsInstance(raised, OSError)
        self.assertNotIsInstance(raised, entry.ListenError)
        self.assertEqual(raised.errno, errno.EIO)
        self.assertNotIn("could not listen", err)
        close.assert_awaited_once()

    def test_without_a_tty_the_api_answers_and_the_engine_closes_after(self) -> None:
        port = _free_port()
        seen: dict[str, Any] = {}

        async def ask() -> None:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/api/state") as resp:
                    seen["state"] = resp.status

        body = lambda made: mock.patch.object(entry, "_forever", ask)  # noqa: E731
        with self.assertNoLogs("aiohttp.access"):
            raised, err, close = self._headless("127.0.0.1", port, body)
        self.assertIsNone(raised)
        self.assertEqual(seen["state"], 200)
        self.assertEqual(err, "")
        close.assert_awaited_once()

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

    def test_with_output_redirected_the_api_server_keeps_serving_until_killed(self) -> None:
        # the CI smoke step: stdout and stderr redirected to files, then a hard kill
        port = _free_port()
        tmp = tempfile.mkdtemp()
        self.addCleanup(_remove_when_free, tmp)
        env = dict(os.environ, HOST="127.0.0.1", DATA_DIR=os.path.join(tmp, "data"), PORT=str(port), PYTHONUTF8="1")
        out_path, err_path = os.path.join(tmp, "out.txt"), os.path.join(tmp, "err.txt")
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            proc = subprocess.Popen(
                [sys.executable, "-m", "mirror"], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
            )
        try:
            first = asyncio.run(_poll_state(port, proc, 30.0))
            time.sleep(1.0)
            second = asyncio.run(_poll_state(port, proc, 5.0))
            alive = proc.poll() is None
        finally:
            proc.kill()
            proc.wait(5)
        with open(err_path, "rb") as err:
            detail = err.read().decode("utf-8", "replace")[-2000:]
        self.assertEqual((first, second, alive), (200, 200, True), detail)
        with open(out_path, "rb") as out:
            self.assertEqual(out.read(), b"")

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
            "with mock.patch.object(entry, 'serve') as run:\n"
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

    def test_a_port_in_use_with_a_tty_ends_with_one_line_and_code_1(self) -> None:
        root = logging.getLogger()
        before, level = list(root.handlers), root.level

        def restore_logging() -> None:
            for handler in list(root.handlers):
                if handler not in before:
                    root.removeHandler(handler)
                    handler.close()
            root.setLevel(level)

        self.addCleanup(restore_logging)
        made: dict[str, Any] = {}
        err = io.StringIO()
        with socket.socket() as taken, contextlib.ExitStack() as stack:

            def make(*args: Any) -> Any:
                app = create_app(*args)
                made["close"] = stack.enter_context(
                    mock.patch.object(app["engine"], "close", wraps=app["engine"].close)
                )
                return app

            taken.bind(("127.0.0.1", 0))
            taken.listen()
            port = taken.getsockname()[1]
            env = {"HOST": "127.0.0.1", "PORT": str(port), "DATA_DIR": self.tmp.name}
            stack.enter_context(mock.patch.dict(os.environ, env))
            stack.enter_context(mock.patch.object(entry, "wants_cli", return_value=True))
            stack.enter_context(mock.patch.object(entry, "create_app", side_effect=make))
            cli = stack.enter_context(mock.patch("mirror.cli.app.run_cli"))
            stack.enter_context(contextlib.redirect_stderr(err))
            # unittest shows aiohttp's NotAppKeyWarning from create_app on stderr; `python -m mirror` does not
            stack.enter_context(warnings.catch_warnings())
            warnings.simplefilter("ignore")
            with self.assertRaises(SystemExit) as ended:
                entry.main()
            restore_logging()
        self.assertEqual(ended.exception.code, 1)
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 1, err.getvalue())
        self.assertIn(f"could not listen on 127.0.0.1:{port}", lines[0])
        self.assertNotIn("Traceback", err.getvalue())
        cli.assert_not_called()
        made["close"].assert_awaited_once()

    def test_an_os_error_from_the_cli_is_not_reported_as_a_port_in_use(self) -> None:
        # a terminal detached mid-session or a console write error ends the CLI with an OSError after the
        # server listened: it is not a bind failure, so its traceback stays and the engine still closes
        root = logging.getLogger()
        before, level = list(root.handlers), root.level

        def restore_logging() -> None:
            for handler in list(root.handlers):
                if handler not in before:
                    root.removeHandler(handler)
                    handler.close()
            root.setLevel(level)

        self.addCleanup(restore_logging)
        made: dict[str, Any] = {}
        err = io.StringIO()
        port = _free_port()

        async def cli(_engine: Any) -> None:
            raise OSError(errno.EIO, "boom")

        with contextlib.ExitStack() as stack:

            def make(*args: Any) -> Any:
                app = create_app(*args)
                made["close"] = stack.enter_context(
                    mock.patch.object(app["engine"], "close", wraps=app["engine"].close)
                )
                return app

            env = {"HOST": "127.0.0.1", "PORT": str(port), "DATA_DIR": self.tmp.name}
            stack.enter_context(mock.patch.dict(os.environ, env))
            stack.enter_context(mock.patch.object(entry, "wants_cli", return_value=True))
            stack.enter_context(mock.patch.object(entry, "create_app", side_effect=make))
            stack.enter_context(mock.patch("mirror.cli.app.run_cli", cli))
            stack.enter_context(contextlib.redirect_stderr(err))
            stack.enter_context(warnings.catch_warnings())
            warnings.simplefilter("ignore")
            with self.assertRaises(OSError) as raised:
                entry.main()
            restore_logging()
        self.assertEqual(raised.exception.errno, errno.EIO)
        self.assertNotIn("could not listen", err.getvalue())
        made["close"].assert_awaited_once()

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


class GracefulExitTests(unittest.TestCase):
    """SIGINT/SIGTERM on POSIX: the headless server stops gracefully the way web.run_app did; next to the CLI
    prompt_toolkit keeps SIGINT. Windows has no loop signal handlers, so the signal itself is not sent here."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _runner_kwargs(self, run: Callable[[web.Application, int], None]) -> dict[str, Any]:
        port = _free_port()
        app = create_app(self.tmp.name, "127.0.0.1", port)
        seen: dict[str, Any] = {}
        real = web.AppRunner

        def recording(*args: Any, **kwargs: Any) -> web.AppRunner:
            seen.update(kwargs)
            return real(*args, **kwargs)

        with mock.patch.object(web, "AppRunner", recording):
            run(app, port)
        return seen

    def test_the_server_alone_lets_the_runner_handle_the_signals(self) -> None:
        async def done() -> None:
            pass

        def run(app: web.Application, port: int) -> None:
            with mock.patch.object(entry, "_forever", done):
                entry.serve(app, "127.0.0.1", port)

        self.assertIs(self._runner_kwargs(run).get("handle_signals"), True)

    def test_next_to_the_cli_the_runner_leaves_the_signals_alone(self) -> None:
        async def cli(_engine: Any) -> None:
            pass

        def run(app: web.Application, port: int) -> None:
            with mock.patch("mirror.cli.app.run_cli", cli):
                entry.serve_and_cli(app, "127.0.0.1", port)

        self.assertIs(self._runner_kwargs(run).get("handle_signals"), False)

    def _ends_quietly(self, body: Callable[[], Awaitable[None]]) -> None:
        port = _free_port()
        app = create_app(self.tmp.name, "127.0.0.1", port)
        engine = app["engine"]
        with mock.patch.object(engine, "close", wraps=engine.close) as close:
            try:
                entry._run(app, "127.0.0.1", port, body)
            except SystemExit:
                self.fail("a graceful exit ends in SystemExit instead of code 0")
        close.assert_awaited_once()

    def test_a_graceful_exit_raised_by_the_body_ends_quietly_and_closes_the_engine(self) -> None:
        async def body() -> None:
            raise web.GracefulExit()

        self._ends_quietly(body)

    def test_a_graceful_exit_from_a_loop_callback_ends_quietly_and_closes_the_engine(self) -> None:
        # what a POSIX loop does with SIGTERM under handle_signals=True: it runs aiohttp's
        # _raise_graceful_exit as a loop callback while the body still waits
        from aiohttp.web_runner import _raise_graceful_exit

        async def body() -> None:
            asyncio.get_running_loop().call_soon(_raise_graceful_exit)
            await asyncio.Event().wait()

        self._ends_quietly(body)

    def _ends_with_the_engine_fully_closed(
        self,
        interrupt: Callable[[], None] | None,
        handle_signals: bool = False,
        in_gateway: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        # the real Engine with a running gateway: Gateway.stop is the real one, and _run waits on the stop event
        # the way the real one waits on the socket, so a cancelled gateway task re-raises CancelledError and a
        # gateway stopped before that ends at once; Engine.close must still reach session.close and store.close.
        # interrupt runs as a loop callback; in_gateway runs inside a step of the gateway task itself
        from mirror.gateway import Gateway

        class IdleGateway(Gateway):
            async def _run(self) -> None:
                if in_gateway is not None:
                    in_gateway()
                await self._stop.wait()

        port = _free_port()
        app = create_app(self.tmp.name, "127.0.0.1", port)
        engine = app["engine"]
        seen: dict[str, Any] = {}

        async def noop(*_args: Any) -> None:
            pass

        async def body() -> None:
            gateway = IdleGateway(None, noop)  # type: ignore[arg-type]
            gateway.start()
            seen["task"] = gateway._task
            engine.gateway = gateway
            engine.running = True
            if interrupt is not None:
                asyncio.get_running_loop().call_soon(interrupt)
            await asyncio.Event().wait()

        with mock.patch.object(engine.store, "close", wraps=engine.store.close) as store_close, self.assertNoLogs(
            "asyncio", level="WARNING"
        ):
            try:
                entry._run(app, "127.0.0.1", port, body, handle_signals=handle_signals)
            except (SystemExit, KeyboardInterrupt) as exc:
                self.fail(f"the interrupt ends in {type(exc).__name__} instead of code 0")
        self.assertTrue(seen["task"].done())
        self.assertIsNone(engine.gateway)
        self.assertEqual(engine.status, "stopped")
        self.assertIn("stopped", engine.lines)
        self.assertIsNone(engine.session)
        store_close.assert_called_once()
        with self.assertRaises(sqlite3.ProgrammingError):
            engine.store.conn.execute("SELECT 1")
        return seen

    def test_a_graceful_exit_with_a_running_gateway_still_closes_the_session_and_the_store(self) -> None:
        # what a POSIX loop does with SIGINT or SIGTERM under handle_signals=True
        from aiohttp.web_runner import _raise_graceful_exit

        self._ends_with_the_engine_fully_closed(_raise_graceful_exit)

    def test_ctrl_c_with_a_running_gateway_still_closes_the_session_and_the_store(self) -> None:
        def ctrl_c() -> None:
            raise KeyboardInterrupt

        self._ends_with_the_engine_fully_closed(ctrl_c)

    def test_sigint_inside_the_gateway_task_cancels_the_server_task_and_the_gateway_stops_normally(self) -> None:
        # the headless server on Windows, where the loop has no signal handlers: Ctrl+C is a SIGINT the Python
        # handler sees at whatever bytecode runs; here inside a step of the gateway task. As under asyncio.Runner
        # it must cancel the server task instead of raising KeyboardInterrupt into that task, so Gateway.stop
        # ends the gateway task normally. On POSIX the runner's loop handler takes SIGINT and the exit is the
        # graceful one; both end the same way here.
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, previous)

        seen = self._ends_with_the_engine_fully_closed(
            None, handle_signals=True, in_gateway=lambda: signal.raise_signal(signal.SIGINT)
        )
        task = seen["task"]
        self.assertFalse(task.cancelled())
        self.assertIsNone(task.exception(), "the SIGINT landed in the gateway task")
        self.assertIs(signal.getsignal(signal.SIGINT), signal.default_int_handler)

    def test_a_graceful_exit_with_a_client_on_the_event_stream_closes_the_engine_without_the_shutdown_wait(
        self,
    ) -> None:
        # runner.cleanup waits up to shutdown_timeout for in-flight handlers, twice over, before the app's cleanup
        # runs Engine.close; an /api/events client that stays connected must not hold the exit for that long
        from aiohttp.web_runner import _raise_graceful_exit

        shutdown_timeout = 3.0
        port = _free_port()
        app = create_app(self.tmp.name, "127.0.0.1", port)
        engine = app["engine"]
        real = web.AppRunner
        seen: dict[str, Any] = {}

        def short_shutdown(*args: Any, **kwargs: Any) -> web.AppRunner:
            return real(*args, shutdown_timeout=shutdown_timeout, **kwargs)

        def subscribe() -> None:
            # a raw socket the test keeps open until _run returned, so only the server can end the stream
            sock = socket.create_connection(("127.0.0.1", port), timeout=5)
            seen["sock"] = sock
            sock.sendall(f"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
            data = b""
            while b": ok\n\n" not in data:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError(f"the event stream ended before its first line: {data!r}")
                data += chunk

        async def body() -> None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, subscribe)
            seen["listeners"] = len(engine.listeners)
            seen["interrupted"] = time.monotonic()
            loop.call_soon(_raise_graceful_exit)
            await asyncio.Event().wait()

        try:
            with mock.patch.object(web, "AppRunner", short_shutdown):
                entry._run(app, "127.0.0.1", port, body, handle_signals=True)
            elapsed = time.monotonic() - seen["interrupted"]
        finally:
            if "sock" in seen:
                seen["sock"].close()
        self.assertEqual(seen["listeners"], 1)
        self.assertLess(elapsed, shutdown_timeout / 2, f"the exit took {elapsed:.2f} s")
        self.assertIsNone(engine.session)
        with self.assertRaises(sqlite3.ProgrammingError):
            engine.store.conn.execute("SELECT 1")


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
