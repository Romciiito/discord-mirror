from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from mirror.access import READ_MESSAGE_HISTORY, VIEW_CHANNEL, readable_plan
from mirror.discord_api import ApiError, CAPABILITIES, _fail_detail, build_properties, clean_token, clean_webhook
from mirror.gateway import Inflate
from mirror.relay import author_name, clip, payload_for, view_from_message
from mirror.store import Store


class CoreTests(unittest.TestCase):
    def test_capabilities_match_desktop_default(self) -> None:
        value = 0
        for bit in (0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14):
            value |= 1 << bit
        self.assertEqual(CAPABILITIES, value)
        self.assertEqual(CAPABILITIES, 22525)

    def test_properties_look_like_mac_chrome(self) -> None:
        props = build_properties(627798, 131)
        self.assertEqual(props["os"], "Mac OS X")
        self.assertIn("Macintosh", props["browser_user_agent"])
        self.assertEqual(props["client_build_number"], 627798)
        self.assertNotIn("token", props)

    def test_webhook_host_is_discord_only(self) -> None:
        good = "https://discord.com/api/webhooks/123/abc_DEF-1"
        self.assertEqual(clean_webhook(good), good)
        self.assertEqual(clean_webhook(""), "")
        with self.assertRaises(ApiError):
            clean_webhook("https://example.com/api/webhooks/123/abc")
        with self.assertRaises(ApiError):
            clean_webhook("https://discord.com.evil.com/api/webhooks/123/abc")

    def test_token_loses_whitespace_and_double_quotes(self) -> None:
        self.assertEqual(clean_token(' \t" MTIz.Gx_y-Z.abc_DEF-1 "\n'), "MTIz.Gx_y-Z.abc_DEF-1")

    def test_token_loses_single_and_curly_quotes(self) -> None:
        self.assertEqual(clean_token("'MTIz.Gx_y-Z.abc_DEF-1'"), "MTIz.Gx_y-Z.abc_DEF-1")
        self.assertEqual(clean_token("“MTIz.Gx_y-Z.abc_DEF-1”"), "MTIz.Gx_y-Z.abc_DEF-1")

    def test_token_loses_one_matching_pair_only(self) -> None:
        self.assertEqual(clean_token('""MTIz.Gx_y-Z""'), '"MTIz.Gx_y-Z"')
        self.assertEqual(clean_token('"MTIz.Gx_y-Z'), '"MTIz.Gx_y-Z')
        self.assertEqual(clean_token("'MTIz.Gx_y-Z\""), "'MTIz.Gx_y-Z\"")

    def test_payload_clips_and_drops_mentions(self) -> None:
        view = {
            "author": "clyde",
            "channel_name": "general",
            "guild_name": "Desk",
            "content": "x" * 3000,
            "embeds": [],
            "attachments": [],
            "stickers": [],
            "reply": "",
            "avatar": "",
            "deleted": False,
        }
        body = payload_for(view, True)
        self.assertLessEqual(len(body["content"]), 2000)
        self.assertEqual(body["allowed_mentions"], {"parse": []})
        self.assertTrue(body["content"].startswith("Desk / #general"))

    def test_author_and_view(self) -> None:
        self.assertEqual(author_name({"author": {"username": "everyone"}}), "everyone.")
        self.assertEqual(clip("abcd", 3), "ab…")
        message = {
            "id": "9",
            "channel_id": "8",
            "content": "hello",
            "author": {"id": "1", "username": "ada", "global_name": "Ada", "avatar": "abc"},
            "attachments": [{"filename": "a.png", "url": "https://cdn.discordapp.com/a.png", "size": 10}],
            "embeds": [{"type": "rich", "title": "t", "description": "d"}],
        }
        view = view_from_message(message, "general", "Desk")
        self.assertEqual(view["author"], "Ada")
        self.assertEqual(view["attachments"][0]["name"], "a.png")
        self.assertEqual(view["embeds"][0]["title"], "t")

    def test_inflate_sync_flush(self) -> None:
        raw = json.dumps({"op": 10, "d": {"heartbeat_interval": 41250}}).encode()
        compressor = zlib.compressobj()
        blob = compressor.compress(raw) + compressor.flush(zlib.Z_SYNC_FLUSH)
        mid = len(blob) // 2
        inflater = Inflate()
        self.assertIsNone(inflater.feed(blob[:mid]))
        parsed = json.loads(inflater.feed(blob[mid:]))
        self.assertEqual(parsed["op"], 10)

    def test_store_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.set_token("x" * 50, True)
            self.assertEqual(store.token(), "x" * 50)
            store.set_options(25, True, "https://discord.com/api/webhooks/1/abc", True, "Desk copy")
            self.assertEqual(store.options()["backfill"], 25)
            self.assertEqual(store.options()["dest_name"], "Desk copy")
            store.replace_selection(
                [
                    {
                        "channel_id": "10",
                        "guild_id": "20",
                        "guild_name": "Desk",
                        "channel_name": "general",
                        "webhook_url": "",
                        "enabled": 1,
                        "parent": "talk",
                    }
                ]
            )
            self.assertEqual(store.selection()[0]["channel_name"], "general")
            self.assertEqual(store.selection()[0]["parent"], "talk")
            store.fill_webhooks([("10", "https://discord.com/api/webhooks/1/abc")])
            self.assertEqual(store.selection()[0]["webhook_url"], "https://discord.com/api/webhooks/1/abc")
            store.clear_destination()
            self.assertEqual(store.selection()[0]["webhook_url"], "")
            self.assertEqual(store.options()["dest_guild_id"], "")
            store.remember_relay("1", "10", "https://discord.com/api/webhooks/1/abc", "99")
            self.assertEqual(store.relay_row("1")["webhook_message_id"], "99")
            store.forget_token()
            self.assertEqual(store.token(), "")
            if sys.platform != "win32":
                self.assertEqual((Path(tmp) / "state.db").stat().st_mode & 0o777, 0o600)
            store.close()

    def test_only_readable_text_channels_are_copied(self) -> None:
        everyone = VIEW_CHANNEL | READ_MESSAGE_HISTORY
        channels = [
            {"id": "1", "type": 4, "name": "Talk", "position": 0},
            {"id": "2", "type": 0, "name": "general", "position": 0, "parent_id": "1"},
            {
                "id": "3",
                "type": 0,
                "name": "staff",
                "position": 1,
                "parent_id": "1",
                "permission_overwrites": [{"id": "9", "allow": "0", "deny": str(VIEW_CHANNEL)}],
            },
            {
                "id": "4",
                "type": 5,
                "name": "news",
                "position": 2,
                "parent_id": "1",
                "permission_overwrites": [{"id": "9", "allow": "0", "deny": str(READ_MESSAGE_HISTORY)}],
            },
            {"id": "5", "type": 2, "name": "Voice", "position": 3},
            {"id": "6", "type": 0, "name": "lobby", "position": 4},
        ]
        plan = readable_plan(channels, base=everyone, owner=False, user_id="7", guild_id="9", role_ids=[])
        self.assertEqual([item["name"] for item in plan["channels"]], ["general", "lobby"])
        self.assertEqual([item["name"] for item in plan["categories"]], ["Talk"])
        self.assertEqual(plan["skipped"], [{"name": "staff", "reason": "hidden"}, {"name": "news", "reason": "no history"}])
        self.assertEqual(plan["other"], 1)
        owned = readable_plan(channels, base=0, owner=True, user_id="7", guild_id="9", role_ids=[])
        self.assertEqual([item["name"] for item in owned["channels"]], ["general", "staff", "news", "lobby"])

    def test_captcha_error_is_plain(self) -> None:
        detail = _fail_detail("POST", "/guilds", '{"captcha_sitekey":"x","message":"captcha"}')
        self.assertIn("captcha", detail.casefold())
        self.assertNotIn("sitekey", detail)


if __name__ == "__main__":
    unittest.main()
