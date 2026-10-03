from __future__ import annotations

import asyncio
import ctypes
import shutil
import sys
from typing import Any

WINDOWS = sys.platform == "win32"
GENERIC = 1
DWORD = ctypes.c_uint32


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", DWORD), ("dwHighDateTime", DWORD)]


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", DWORD),
        ("Type", DWORD),
        ("TargetName", ctypes.c_wchar_p),
        ("Comment", ctypes.c_wchar_p),
        ("LastWritten", FILETIME),
        ("CredentialBlobSize", DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", DWORD),
        ("AttributeCount", DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


_lib: Any = None


def _advapi32() -> Any:
    global _lib
    if _lib is None:
        lib = ctypes.WinDLL("advapi32", use_last_error=True)
        lib.CredReadW.argtypes = [ctypes.c_wchar_p, DWORD, DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
        lib.CredReadW.restype = ctypes.c_int
        lib.CredFree.argtypes = [ctypes.c_void_p]
        lib.CredFree.restype = None
        _lib = lib
    return _lib


def _clean(service: str, account: str) -> tuple[str, str]:
    service = (service or "").strip()
    account = (account or "").strip()
    if not service or not account:
        raise RuntimeError("keychain service and account are required")
    if any(ch in service or ch in account for ch in "\r\n\x00"):
        raise RuntimeError("keychain service and account are required")
    return service, account


def _decode(raw: bytes) -> str:
    odd = raw[1::2]
    try:
        if raw.startswith(b"\xff\xfe"):
            text = raw[2:].decode("utf-16-le")
        elif len(raw) >= 2 and len(raw) % 2 == 0 and odd.count(0) * 2 >= len(odd):
            text = raw.decode("utf-16-le")
        else:
            text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise RuntimeError("keychain entry is not text") from None
    return text.rstrip("\x00\r\n")


def _read_windows(service: str, account: str) -> str:
    lib = _advapi32()
    cred = ctypes.POINTER(CREDENTIALW)()
    if not lib.CredReadW(service, GENERIC, 0, ctypes.pointer(cred)) or not cred:
        raise RuntimeError("keychain lookup failed")
    try:
        rec = cred.contents
        user = rec.UserName or ""
        size = int(rec.CredentialBlobSize)
        raw = ctypes.string_at(rec.CredentialBlob, size) if size and rec.CredentialBlob else b""
    finally:
        lib.CredFree(ctypes.cast(cred, ctypes.c_void_p))
    if user.casefold() != account.casefold():
        raise RuntimeError("keychain account does not match")
    secret = _decode(raw)
    if not secret:
        raise RuntimeError("keychain entry is empty")
    return secret


async def read_keychain(service: str, account: str) -> str:
    if WINDOWS:
        service, account = _clean(service, account)
        return _read_windows(service, account)
    binary = shutil.which("security")
    if not binary:
        raise RuntimeError("keychain is available on macOS and Windows")
    service, account = _clean(service, account)
    proc = await asyncio.create_subprocess_exec(
        binary,
        "find-generic-password",
        "-s",
        service,
        "-a",
        account,
        "-w",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError("keychain lookup failed")
    secret = stdout.decode("utf-8", errors="strict").rstrip("\n")
    if not secret:
        raise RuntimeError("keychain entry is empty")
    return secret
