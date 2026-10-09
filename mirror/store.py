from __future__ import annotations

import base64
import ctypes
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("mirror.store")

WINDOWS = sys.platform == "win32"
SEALED = "dpapi:"
UI_FORBIDDEN = 0x01
DWORD = ctypes.c_uint32

SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token TEXT,
    keep INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS options (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    backfill INTEGER NOT NULL DEFAULT 0,
    include_threads INTEGER NOT NULL DEFAULT 0,
    mirror INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS selection (
    channel_id TEXT PRIMARY KEY,
    guild_id TEXT NOT NULL,
    guild_name TEXT NOT NULL DEFAULT '',
    channel_name TEXT NOT NULL DEFAULT '',
    webhook_url TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS relayed (
    source_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    webhook_url TEXT NOT NULL,
    webhook_message_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hooks (
    channel_id TEXT PRIMARY KEY,
    webhook_url TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS targets (
    source_guild_id TEXT PRIMARY KEY,
    target_guild_id TEXT NOT NULL,
    target_name TEXT NOT NULL DEFAULT ''
);
"""


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_libs: tuple[Any, Any] | None = None


def _dpapi() -> tuple[Any, Any]:
    global _libs
    if _libs is None:
        blob = ctypes.POINTER(DATA_BLOB)
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        crypt.CryptProtectData.argtypes = [
            blob, ctypes.c_wchar_p, blob, ctypes.c_void_p, ctypes.c_void_p, DWORD, blob
        ]
        crypt.CryptProtectData.restype = ctypes.c_int
        crypt.CryptUnprotectData.argtypes = [
            blob, ctypes.POINTER(ctypes.c_wchar_p), blob, ctypes.c_void_p, ctypes.c_void_p, DWORD, blob
        ]
        crypt.CryptUnprotectData.restype = ctypes.c_int
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        _libs = (crypt, kernel)
    return _libs


def _call(name: str, data: bytes) -> bytes | None:
    crypt, kernel = _dpapi()
    raw = ctypes.create_string_buffer(data, len(data))
    inp = DATA_BLOB(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_char)))
    out = DATA_BLOB()
    if not getattr(crypt, name)(ctypes.pointer(inp), None, None, None, None, UI_FORBIDDEN, ctypes.pointer(out)):
        return None
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        if out.pbData:
            kernel.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _seal(token: str) -> str:
    if not WINDOWS:
        return token
    try:
        blob = _call("CryptProtectData", token.encode("utf-8"))
    except OSError:
        blob = None
    if blob is None:
        log.warning("token stored without protection")
        return token
    return SEALED + base64.b64encode(blob).decode("ascii")


def _open(value: str) -> str:
    if not value.startswith(SEALED):
        return value
    if not WINDOWS:
        return ""
    try:
        blob = base64.b64decode(value[len(SEALED):], validate=True)
        plain = _call("CryptUnprotectData", blob) if blob else None
        return plain.decode("utf-8") if plain is not None else ""
    except (ValueError, OSError):
        return ""


class Store:
    def __init__(self, directory: str) -> None:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        if not WINDOWS:
            os.chmod(path, 0o700)
        self.path = path / "state.db"
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO options (id) VALUES (1)")
        self._migrate()
        self.conn.commit()
        if not WINDOWS:
            os.chmod(self.path, 0o600)

    def _migrate(self) -> None:
        # old files keep the options columns global_webhook, dest_name and dest_guild_id; nothing reads them (9h)
        picked_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(selection)")}
        if "parent" not in picked_cols:
            self.conn.execute("ALTER TABLE selection ADD COLUMN parent TEXT NOT NULL DEFAULT ''")
        if "topic" not in picked_cols:
            self.conn.execute("ALTER TABLE selection ADD COLUMN topic TEXT NOT NULL DEFAULT ''")
        self.conn.execute(
            "INSERT OR IGNORE INTO hooks (channel_id, webhook_url) "
            "SELECT channel_id, webhook_url FROM selection WHERE webhook_url != ''"
        )

    def close(self) -> None:
        self.conn.close()

    def token(self) -> str:
        row = self.conn.execute("SELECT token, keep FROM account WHERE id = 1").fetchone()
        if row is None or not row["keep"] or not row["token"]:
            return ""
        return _open(str(row["token"]))

    def set_token(self, token: str, keep: bool) -> None:
        if keep and token:
            self.conn.execute(
                "INSERT INTO account (id, token, keep) VALUES (1, ?, 1) "
                "ON CONFLICT(id) DO UPDATE SET token = excluded.token, keep = 1",
                (_seal(token),),
            )
        else:
            self.conn.execute("DELETE FROM account WHERE id = 1")
        self.conn.commit()

    def forget_token(self) -> None:
        self.conn.execute("DELETE FROM account WHERE id = 1")
        self.conn.commit()

    def options(self) -> dict:
        row = self.conn.execute("SELECT backfill, include_threads, mirror FROM options WHERE id = 1").fetchone()
        return {
            "backfill": int(row["backfill"]),
            "include_threads": bool(row["include_threads"]),
            "mirror": bool(row["mirror"]),
        }

    def set_options(self, backfill: int, include_threads: bool, mirror: bool) -> None:
        self.conn.execute(
            "UPDATE options SET backfill = ?, include_threads = ?, mirror = ? WHERE id = 1",
            (int(backfill), int(include_threads), int(mirror)),
        )
        self.conn.commit()

    def set_target(self, source_guild_id: str, target_guild_id: str, target_name: str) -> None:
        """The server the owner picked as the copy of one source server (decision 9b'); a later pick replaces it."""
        self.conn.execute(
            "INSERT INTO targets (source_guild_id, target_guild_id, target_name) VALUES (?, ?, ?) "
            "ON CONFLICT(source_guild_id) DO UPDATE SET "
            "target_guild_id = excluded.target_guild_id, target_name = excluded.target_name",
            (str(source_guild_id), str(target_guild_id), str(target_name or "")[:100]),
        )
        self.conn.commit()

    def targets(self) -> dict[str, dict[str, str]]:
        rows = self.conn.execute("SELECT source_guild_id, target_guild_id, target_name FROM targets").fetchall()
        return {
            str(row["source_guild_id"]): {"target_id": str(row["target_guild_id"]), "target_name": str(row["target_name"])}
            for row in rows
        }

    def fill_webhooks(self, pairs: list[tuple[str, str]]) -> None:
        for source_id, url in pairs:
            self.conn.execute("UPDATE selection SET webhook_url = ? WHERE channel_id = ?", (url, source_id))
            self._keep_hook(source_id, url)
        self.conn.commit()

    def _keep_hook(self, channel_id: str, url: str) -> None:
        if not channel_id or not url:
            return
        self.conn.execute(
            "INSERT INTO hooks (channel_id, webhook_url) VALUES (?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET webhook_url = excluded.webhook_url",
            (channel_id, url),
        )

    def hooks(self) -> dict[str, str]:
        rows = self.conn.execute("SELECT channel_id, webhook_url FROM hooks").fetchall()
        return {str(row["channel_id"]): str(row["webhook_url"]) for row in rows}

    def selection(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT channel_id, guild_id, guild_name, channel_name, webhook_url, enabled, parent, topic "
            "FROM selection ORDER BY guild_name, channel_name"
        ).fetchall()
        return [dict(row) for row in rows]

    def replace_selection(self, rows: list[dict]) -> None:
        previous = {row["channel_id"]: row["webhook_url"] for row in self.selection()}
        kept = self.hooks()
        packed = []
        for row in rows:
            channel_id = str(row["channel_id"])
            hook = row.get("webhook_url") or previous.get(channel_id) or kept.get(channel_id) or ""
            packed.append(
                (
                    channel_id,
                    str(row["guild_id"]),
                    row.get("guild_name") or "",
                    row.get("channel_name") or "",
                    hook,
                    1 if row.get("enabled", 1) else 0,
                    row.get("parent") or "",
                    str(row.get("topic") or "")[:1024],
                )
            )
        self.conn.execute("DELETE FROM selection")
        self.conn.executemany(
            "INSERT INTO selection (channel_id, guild_id, guild_name, channel_name, webhook_url, enabled, parent, topic) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            packed,
        )
        for item in packed:
            self._keep_hook(item[0], item[4])
        self.conn.commit()

    def remember_relay(self, source_id: str, channel_id: str, webhook_url: str, webhook_message_id: str) -> None:
        self.conn.execute(
            "INSERT INTO relayed (source_id, channel_id, webhook_url, webhook_message_id) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(source_id) DO UPDATE SET "
            "webhook_url = excluded.webhook_url, webhook_message_id = excluded.webhook_message_id",
            (source_id, channel_id, webhook_url, webhook_message_id),
        )
        self.conn.commit()

    def relay_row(self, source_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM relayed WHERE source_id = ?", (source_id,)).fetchone()
        return dict(row) if row else None

    def drop_relay(self, source_id: str) -> None:
        self.conn.execute("DELETE FROM relayed WHERE source_id = ?", (source_id,))
        self.conn.commit()
