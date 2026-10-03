from __future__ import annotations

import os
import sqlite3
from pathlib import Path


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
    global_webhook TEXT NOT NULL DEFAULT '',
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
"""


class Store:
    def __init__(self, directory: str) -> None:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
        self.path = path / "state.db"
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.execute("INSERT OR IGNORE INTO options (id) VALUES (1)")
        self._migrate()
        self.conn.commit()
        os.chmod(self.path, 0o600)

    def _migrate(self) -> None:
        option_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(options)")}
        if "dest_name" not in option_cols:
            self.conn.execute("ALTER TABLE options ADD COLUMN dest_name TEXT NOT NULL DEFAULT 'mirror'")
        if "dest_guild_id" not in option_cols:
            self.conn.execute("ALTER TABLE options ADD COLUMN dest_guild_id TEXT NOT NULL DEFAULT ''")
        picked_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(selection)")}
        if "parent" not in picked_cols:
            self.conn.execute("ALTER TABLE selection ADD COLUMN parent TEXT NOT NULL DEFAULT ''")
        if "topic" not in picked_cols:
            self.conn.execute("ALTER TABLE selection ADD COLUMN topic TEXT NOT NULL DEFAULT ''")

    def close(self) -> None:
        self.conn.close()

    def token(self) -> str:
        row = self.conn.execute("SELECT token, keep FROM account WHERE id = 1").fetchone()
        if row is None or not row["keep"] or not row["token"]:
            return ""
        return str(row["token"])

    def set_token(self, token: str, keep: bool) -> None:
        if keep and token:
            self.conn.execute(
                "INSERT INTO account (id, token, keep) VALUES (1, ?, 1) "
                "ON CONFLICT(id) DO UPDATE SET token = excluded.token, keep = 1",
                (token,),
            )
        else:
            self.conn.execute("DELETE FROM account WHERE id = 1")
        self.conn.commit()

    def forget_token(self) -> None:
        self.conn.execute("DELETE FROM account WHERE id = 1")
        self.conn.commit()

    def options(self) -> dict:
        row = self.conn.execute("SELECT * FROM options WHERE id = 1").fetchone()
        return {
            "backfill": int(row["backfill"]),
            "include_threads": bool(row["include_threads"]),
            "global_webhook": row["global_webhook"] or "",
            "mirror": bool(row["mirror"]),
            "dest_name": row["dest_name"] or "mirror",
            "dest_guild_id": row["dest_guild_id"] or "",
        }

    def set_options(
        self,
        backfill: int,
        include_threads: bool,
        global_webhook: str,
        mirror: bool,
        dest_name: str | None = None,
    ) -> None:
        if dest_name is None:
            dest_name = self.options()["dest_name"]
        dest_name = " ".join(str(dest_name or "").split())[:100] or "mirror"
        self.conn.execute(
            "UPDATE options SET backfill = ?, include_threads = ?, global_webhook = ?, mirror = ?, dest_name = ? WHERE id = 1",
            (int(backfill), int(include_threads), global_webhook, int(mirror), dest_name),
        )
        self.conn.commit()

    def set_dest_guild(self, guild_id: str) -> None:
        self.conn.execute("UPDATE options SET dest_guild_id = ? WHERE id = 1", (guild_id,))
        self.conn.commit()

    def clear_destination(self) -> None:
        self.conn.execute("UPDATE selection SET webhook_url = ''")
        self.conn.execute("UPDATE options SET dest_guild_id = '' WHERE id = 1")
        self.conn.commit()

    def fill_webhooks(self, pairs: list[tuple[str, str]]) -> None:
        for source_id, url in pairs:
            self.conn.execute("UPDATE selection SET webhook_url = ? WHERE channel_id = ?", (url, source_id))
        self.conn.commit()

    def selection(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT channel_id, guild_id, guild_name, channel_name, webhook_url, enabled, parent, topic "
            "FROM selection ORDER BY guild_name, channel_name"
        ).fetchall()
        return [dict(row) for row in rows]

    def replace_selection(self, rows: list[dict]) -> None:
        previous = {row["channel_id"]: row["webhook_url"] for row in self.selection()}
        self.conn.execute("DELETE FROM selection")
        self.conn.executemany(
            "INSERT INTO selection (channel_id, guild_id, guild_name, channel_name, webhook_url, enabled, parent, topic) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    str(row["channel_id"]),
                    str(row["guild_id"]),
                    row.get("guild_name") or "",
                    row.get("channel_name") or "",
                    row.get("webhook_url") or previous.get(str(row["channel_id"]), ""),
                    1 if row.get("enabled", 1) else 0,
                    row.get("parent") or "",
                    str(row.get("topic") or "")[:1024],
                )
                for row in rows
            ],
        )
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
