from __future__ import annotations

import base64
import ctypes
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from mirror import store as store_mod
from mirror.store import DATA_BLOB, SEALED, Store

read = ctypes.string_at
PREFIX = b"sealed:"


class FakeDpapi:
    def __init__(self) -> None:
        self.keep: list = []
        self.handed: list[int] = []
        self.freed: list = []
        self.calls: list[tuple[str, bytes, int]] = []
        self.protect_ok = True
        self.unprotect_ok = True

    def _give(self, out, data: bytes) -> int:
        buf = ctypes.create_string_buffer(data, len(data))
        self.keep.append(buf)
        self.handed.append(ctypes.addressof(buf))
        out.contents.cbData = len(data)
        out.contents.pbData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
        return 1

    def CryptProtectData(self, inp, desc, entropy, reserved, prompt, flags, out) -> int:
        data = read(inp.contents.pbData, inp.contents.cbData)
        self.calls.append(("protect", data, flags))
        if not self.protect_ok:
            return 0
        return self._give(out, PREFIX + data)

    def CryptUnprotectData(self, inp, desc, entropy, reserved, prompt, flags, out) -> int:
        data = read(inp.contents.pbData, inp.contents.cbData)
        self.calls.append(("unprotect", data, flags))
        if not self.unprotect_ok or not data.startswith(PREFIX):
            return 0
        return self._give(out, data[len(PREFIX):])

    def LocalFree(self, handle) -> None:
        self.freed.append(handle)


def raw_row(store: Store) -> str | None:
    row = store.conn.execute("SELECT token FROM account WHERE id = 1").fetchone()
    return None if row is None else row["token"]


def put_row(store: Store, value: str) -> None:
    store.conn.execute("INSERT INTO account VALUES (1, ?, 1)", (value,))
    store.conn.commit()


def sealed(data: bytes) -> str:
    return SEALED + base64.b64encode(data).decode("ascii")


class StoreTests(unittest.TestCase):
    @contextmanager
    def windows(self, fake: FakeDpapi) -> Iterator[None]:
        with mock.patch.object(store_mod, "WINDOWS", True), mock.patch.object(
            store_mod, "_dpapi", return_value=(fake, fake)
        ):
            yield

    def test_blob_layout(self) -> None:
        width = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(ctypes.sizeof(DATA_BLOB), 2 * width)
        self.assertEqual(DATA_BLOB.cbData.offset, 0)
        self.assertEqual(DATA_BLOB.cbData.size, 4)
        self.assertEqual(DATA_BLOB.pbData.offset, width)

    def test_windows_round_trip(self) -> None:
        fake = FakeDpapi()
        token = "x" * 50
        with tempfile.TemporaryDirectory() as tmp, self.windows(fake):
            store = Store(tmp)
            try:
                store.set_token(token, True)
                value = raw_row(store)
                self.assertTrue(value.startswith(SEALED))
                self.assertEqual(base64.b64decode(value[len(SEALED):]), PREFIX + token.encode())
                self.assertEqual(store.token(), token)
                protects = [call for call in fake.calls if call[0] == "protect"]
                self.assertEqual(protects, [("protect", token.encode(), 1)])
                self.assertEqual(len(fake.freed), 2)
                self.assertEqual([handle.value for handle in fake.freed], fake.handed)
            finally:
                store.close()

    def test_non_ascii_token(self) -> None:
        fake = FakeDpapi()
        token = "jeton-éß-日本-\U0001f511"
        with tempfile.TemporaryDirectory() as tmp, self.windows(fake):
            store = Store(tmp)
            try:
                store.set_token(token, True)
                self.assertTrue(raw_row(store).startswith(SEALED))
                self.assertEqual(fake.calls[0][1], token.encode("utf-8"))
                self.assertEqual(store.token(), token)
            finally:
                store.close()

    def test_plain_row_loads_and_is_resealed(self) -> None:
        fake = FakeDpapi()
        token = "plain.token.value"
        with tempfile.TemporaryDirectory() as tmp, self.windows(fake):
            store = Store(tmp)
            try:
                put_row(store, token)
                self.assertEqual(store.token(), token)
                self.assertEqual(fake.calls, [])
                store.set_token(token, True)
                self.assertTrue(raw_row(store).startswith(SEALED))
                self.assertEqual(store.token(), token)
            finally:
                store.close()

    def test_bad_blob_reads_as_no_token(self) -> None:
        fake = FakeDpapi()
        fake.unprotect_ok = False
        with tempfile.TemporaryDirectory() as tmp, self.windows(fake):
            store = Store(tmp)
            try:
                value = sealed(PREFIX + b"x")
                put_row(store, value)
                self.assertEqual(store.token(), "")
                self.assertEqual(raw_row(store), value)
                fake.unprotect_ok = True
                for value in (SEALED + "%%%not-base64", sealed(b"junk"), SEALED):
                    store.conn.execute("UPDATE account SET token = ? WHERE id = 1", (value,))
                    store.conn.commit()
                    self.assertEqual(store.token(), "")
                    self.assertEqual(raw_row(store), value)
                store.conn.execute("UPDATE account SET token = ? WHERE id = 1", (sealed(PREFIX + b"\xff\xfe"),))
                store.conn.commit()
                self.assertEqual(store.token(), "")
                self.assertEqual(len(fake.freed), len(fake.handed))
            finally:
                store.close()

    def test_sealed_row_reads_as_no_token_off_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(store_mod, "WINDOWS", False), mock.patch.object(
            store_mod, "_dpapi", side_effect=AssertionError("dpapi used")
        ) as loader:
            store = Store(tmp)
            try:
                value = sealed(PREFIX + b"token")
                put_row(store, value)
                self.assertEqual(store.token(), "")
                self.assertEqual(raw_row(store), value)
                loader.assert_not_called()
            finally:
                store.close()

    def test_protect_failure_keeps_plain_and_warns(self) -> None:
        fake = FakeDpapi()
        fake.protect_ok = False
        with tempfile.TemporaryDirectory() as tmp, self.windows(fake):
            store = Store(tmp)
            try:
                with self.assertLogs("mirror.store", level="WARNING") as seen:
                    store.set_token("t" * 40, True)
                self.assertNotIn("t" * 40, "".join(seen.output))
                self.assertEqual(raw_row(store), "t" * 40)
                self.assertEqual(store.token(), "t" * 40)
                self.assertEqual(fake.freed, [])
            finally:
                store.close()

    def test_free_runs_when_copy_fails(self) -> None:
        fake = FakeDpapi()
        with mock.patch.object(store_mod, "_dpapi", return_value=(fake, fake)), mock.patch.object(
            store_mod.ctypes, "string_at", side_effect=MemoryError
        ):
            with self.assertRaises(MemoryError):
                store_mod._call("CryptProtectData", b"abc")
        self.assertEqual([handle.value for handle in fake.freed], fake.handed)
        self.assertEqual(len(fake.freed), 1)

    def test_loader_sets_signatures(self) -> None:
        libs: dict[str, mock.MagicMock] = {}

        def load(name: str, use_last_error: bool = False) -> mock.MagicMock:
            self.assertTrue(use_last_error)
            libs[name] = mock.MagicMock()
            return libs[name]

        with mock.patch.object(ctypes, "WinDLL", side_effect=load, create=True) as dll, mock.patch.object(
            store_mod, "_libs", None
        ):
            first = store_mod._dpapi()
            second = store_mod._dpapi()
        self.assertIs(first, second)
        self.assertEqual(dll.call_count, 2)
        self.assertEqual(first, (libs["crypt32"], libs["kernel32"]))
        blob = ctypes.POINTER(DATA_BLOB)
        for name in ("CryptProtectData", "CryptUnprotectData"):
            func = getattr(libs["crypt32"], name)
            self.assertEqual(len(func.argtypes), 7)
            self.assertIs(func.argtypes[0], blob)
            self.assertIs(func.argtypes[5], ctypes.c_uint32)
            self.assertIs(func.argtypes[6], blob)
            self.assertIs(func.restype, ctypes.c_int)
        self.assertEqual(libs["kernel32"].LocalFree.argtypes, [ctypes.c_void_p])
        self.assertIs(libs["kernel32"].LocalFree.restype, ctypes.c_void_p)

    def test_posix_format_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(store_mod, "WINDOWS", False):
            store = Store(tmp)
            try:
                store.set_token("y" * 30, True)
                self.assertEqual(raw_row(store), "y" * 30)
                self.assertEqual(store.token(), "y" * 30)
                store.forget_token()
                self.assertIsNone(raw_row(store))
                self.assertEqual(store.token(), "")
            finally:
                store.close()

    def test_chmod_only_off_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for flag, expected in ((False, 2), (True, 0)):
                where = Path(tmp) / str(flag)
                with mock.patch.object(store_mod, "WINDOWS", flag), mock.patch.object(store_mod.os, "chmod") as chmod:
                    store = Store(str(where))
                    store.close()
                self.assertEqual(chmod.call_count, expected)
                if expected:
                    self.assertEqual(
                        chmod.call_args_list, [mock.call(where, 0o700), mock.call(where / "state.db", 0o600)]
                    )

    def test_set_token_keep_false_deletes(self) -> None:
        fake = FakeDpapi()
        with tempfile.TemporaryDirectory() as tmp, self.windows(fake):
            store = Store(tmp)
            try:
                put_row(store, sealed(PREFIX + b"old"))
                store.set_token("z" * 20, False)
                self.assertIsNone(raw_row(store))
                self.assertEqual(store.token(), "")
                self.assertEqual(fake.calls, [])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
