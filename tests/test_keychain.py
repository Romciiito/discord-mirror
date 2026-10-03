from __future__ import annotations

import ctypes
import unittest
from typing import Any
from unittest import mock

from mirror import keychain
from mirror.keychain import CREDENTIALW, FILETIME, read_keychain


class FakeAdvapi:
    def __init__(self, raw: bytes | None, user: str | None, target: str = "svc") -> None:
        self.raw = raw
        self.user = user
        self.target = target
        self.keep: list[Any] = []
        self.freed: list[Any] = []
        self.calls: list[tuple[str, int, int]] = []

    def CredReadW(self, target: str, kind: int, flags: int, out: Any) -> int:
        self.calls.append((target, kind, flags))
        if self.raw is None or target != self.target:
            return 0
        raw = self.raw
        buf = ctypes.create_string_buffer(raw, len(raw))
        rec = CREDENTIALW()
        rec.Type = 1
        rec.TargetName = target
        rec.UserName = self.user
        rec.CredentialBlobSize = len(raw)
        if raw:
            rec.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
        self.keep.append((buf, rec))
        out[0] = ctypes.pointer(rec)
        return 1

    def CredFree(self, p: Any) -> None:
        self.freed.append(p)


class LayoutTests(unittest.TestCase):
    def test_credential_layout(self) -> None:
        ptr = ctypes.sizeof(ctypes.c_void_p)
        self.assertEqual(ctypes.sizeof(FILETIME), 8)
        self.assertEqual(ctypes.sizeof(CREDENTIALW), 52 if ptr == 4 else 80)
        self.assertEqual(CREDENTIALW.LastWritten.offset, 16 if ptr == 4 else 24)
        self.assertEqual(CREDENTIALW.CredentialBlobSize.offset, 24 if ptr == 4 else 32)
        self.assertEqual(CREDENTIALW.CredentialBlob.offset, 28 if ptr == 4 else 40)
        self.assertEqual(CREDENTIALW.Persist.offset, 32 if ptr == 4 else 48)
        self.assertEqual(CREDENTIALW.UserName.offset, 48 if ptr == 4 else 72)

    def test_decode_rules(self) -> None:
        self.assertEqual(keychain._decode("ab".encode("utf-16-le")), "ab")
        self.assertEqual(keychain._decode(b"ab"), "ab")
        self.assertEqual(keychain._decode(b""), "")
        with self.assertRaises(RuntimeError):
            keychain._decode(b"\xc3")


class WindowsTests(unittest.IsolatedAsyncioTestCase):
    async def read(self, fake: FakeAdvapi, service: str = "svc", account: str = "ada") -> str:
        with mock.patch.object(keychain, "WINDOWS", True), mock.patch.object(keychain, "_advapi32", lambda: fake):
            return await read_keychain(service, account)

    async def fails(self, fake: FakeAdvapi, message: str, service: str = "svc", account: str = "ada") -> RuntimeError:
        with self.assertRaises(RuntimeError) as ctx:
            await self.read(fake, service, account)
        self.assertEqual(str(ctx.exception), message)
        return ctx.exception

    async def test_windows_utf16_credential(self) -> None:
        fake = FakeAdvapi(("x" * 50).encode("utf-16-le"), "Ada")
        self.assertEqual(await self.read(fake, "svc", "ADA"), "x" * 50)
        self.assertEqual(fake.calls, [("svc", 1, 0)])
        self.assertEqual(len(fake.freed), 1)

    async def test_windows_utf16_with_trailing_nul_and_bom(self) -> None:
        fake = FakeAdvapi(b"\xff\xfe" + "tok".encode("utf-16-le") + b"\x00\x00", "ada")
        self.assertEqual(await self.read(fake), "tok")
        fake = FakeAdvapi("tok".encode("utf-16-le") + b"\x00\x00", "ada")
        self.assertEqual(await self.read(fake), "tok")
        self.assertEqual(len(fake.freed), 1)

    async def test_windows_utf8_credential(self) -> None:
        self.assertEqual(await self.read(FakeAdvapi(b"tok\n", "ada")), "tok")
        self.assertEqual(await self.read(FakeAdvapi(b"tok\r\n\x00", "ada")), "tok")
        value = "tök€n.abc"
        self.assertEqual(await self.read(FakeAdvapi(value.encode("utf-8"), "ada")), value)

    async def test_windows_account_mismatch(self) -> None:
        fake = FakeAdvapi(b"secret-token", "bob")
        err = await self.fails(fake, "keychain account does not match")
        self.assertEqual(len(fake.freed), 1)
        self.assertNotIn("secret-token", str(err))

    async def test_windows_missing(self) -> None:
        fake = FakeAdvapi(None, "ada")
        await self.fails(fake, "keychain lookup failed")
        self.assertEqual(fake.freed, [])
        fake = FakeAdvapi(b"tok", "ada", target="other")
        await self.fails(fake, "keychain lookup failed")
        self.assertEqual(fake.freed, [])

    async def test_windows_empty(self) -> None:
        for raw in (b"", b"\x00\x00", b"\n"):
            fake = FakeAdvapi(raw, "ada")
            await self.fails(fake, "keychain entry is empty")
            self.assertEqual(len(fake.freed), 1)

    async def test_windows_not_text(self) -> None:
        fake = FakeAdvapi(b"\xff\xfe\xff", "ada")
        err = await self.fails(fake, "keychain entry is not text")
        self.assertNotIn("\xff", str(err))
        self.assertIsNone(err.__cause__)
        self.assertTrue(err.__suppress_context__)
        self.assertEqual(len(fake.freed), 1)

    async def test_windows_validation(self) -> None:
        fake = FakeAdvapi(b"tok", "ada")
        for service, account in (("", "a"), ("a", ""), (" ", "b"), ("a\nb", "b"), ("a", "b\x00"), ("a\rb", "b")):
            await self.fails(fake, "keychain service and account are required", service, account)
        self.assertEqual(fake.calls, [])
        self.assertEqual(await self.read(fake, " svc ", " ada "), "tok")
        self.assertEqual(fake.calls, [("svc", 1, 0)])


class Proc:
    def __init__(self, out: bytes, code: int) -> None:
        self.out = out
        self.returncode = code

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.out, b""


class OtherPlatformTests(unittest.IsolatedAsyncioTestCase):
    async def test_other_platform_message(self) -> None:
        with mock.patch.object(keychain, "WINDOWS", False), mock.patch.object(keychain.shutil, "which", return_value=None):
            for service, account in (("svc", "ada"), ("", "")):
                with self.assertRaises(RuntimeError) as ctx:
                    await read_keychain(service, account)
                self.assertEqual(str(ctx.exception), "keychain is available on macOS and Windows")

    async def mac(self, out: bytes, code: int) -> tuple[str, list[Any]]:
        argv: list[Any] = []

        async def spawn(*args: Any, **kw: Any) -> Proc:
            argv.extend(args)
            return Proc(out, code)

        with mock.patch.object(keychain, "WINDOWS", False), mock.patch.object(
            keychain.shutil, "which", return_value="/usr/bin/security"
        ), mock.patch.object(keychain.asyncio, "create_subprocess_exec", spawn):
            return await read_keychain(" svc ", "ada"), argv

    async def test_mac_path_unchanged(self) -> None:
        secret, argv = await self.mac(b"tok\n", 0)
        self.assertEqual(secret, "tok")
        self.assertEqual(argv, ["/usr/bin/security", "find-generic-password", "-s", "svc", "-a", "ada", "-w"])
        with self.assertRaises(RuntimeError) as ctx:
            await self.mac(b"tok\n", 1)
        self.assertEqual(str(ctx.exception), "keychain lookup failed")
        with self.assertRaises(RuntimeError) as ctx:
            await self.mac(b"\n", 0)
        self.assertEqual(str(ctx.exception), "keychain entry is empty")
        spawn = mock.AsyncMock()
        with mock.patch.object(keychain, "WINDOWS", False), mock.patch.object(
            keychain.shutil, "which", return_value="/usr/bin/security"
        ), mock.patch.object(keychain.asyncio, "create_subprocess_exec", spawn):
            with self.assertRaises(RuntimeError) as ctx:
                await read_keychain("s\nvc", "ada")
        spawn.assert_not_called()
        self.assertEqual(str(ctx.exception), "keychain service and account are required")


if __name__ == "__main__":
    unittest.main()
