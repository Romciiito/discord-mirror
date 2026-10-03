from __future__ import annotations

import asyncio
import shutil


async def read_keychain(service: str, account: str) -> str:
    binary = shutil.which("security")
    if not binary:
        raise RuntimeError("keychain is only available on macOS")
    service = (service or "").strip()
    account = (account or "").strip()
    if not service or not account:
        raise RuntimeError("keychain service and account are required")
    if any(ch in service or ch in account for ch in "\r\n\x00"):
        raise RuntimeError("keychain service and account are required")
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
