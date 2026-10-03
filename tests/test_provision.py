from __future__ import annotations

import unittest

from mirror.provision import destination_layout, webhook_name


class ProvisionTests(unittest.TestCase):
    def test_blank_name_becomes_mirror(self) -> None:
        plan = destination_layout([], "   ")
        self.assertEqual(plan["name"], "mirror")
        self.assertEqual(plan["categories"], [])
        self.assertEqual(plan["channels"], [])

    def test_disabled_and_bad_ids_dropped(self) -> None:
        plan = destination_layout(
            [
                {"channel_id": "10", "guild_id": "1", "guild_name": "Desk", "channel_name": "general", "enabled": False},
                {"channel_id": "nope", "guild_id": "1", "guild_name": "Desk", "channel_name": "secret"},
                {"channel_id": "", "guild_id": "1", "guild_name": "Desk", "channel_name": "x"},
            ],
            "Copy",
        )
        self.assertEqual(plan["channels"], [])
        self.assertEqual(plan["name"], "Copy")

    def test_two_guilds_keep_their_categories(self) -> None:
        plan = destination_layout(
            [
                {"channel_id": "11", "guild_id": "2", "guild_name": "Other", "channel_name": "lobby", "parent": ""},
                {"channel_id": "10", "guild_id": "1", "guild_name": "Desk", "channel_name": "general", "parent": ""},
            ],
            "mirror",
        )
        self.assertEqual([item["name"] for item in plan["categories"]], ["Desk", "Other"])
        self.assertEqual(plan["channels"][0]["category_key"], "1|")
        self.assertEqual(plan["channels"][1]["guild_id"], "2")

    def test_parent_category_name(self) -> None:
        plan = destination_layout(
            [
                {
                    "channel_id": "10",
                    "guild_id": "1",
                    "guild_name": "Desk",
                    "channel_name": "notes",
                    "parent": "talk",
                }
            ],
            "mirror",
        )
        self.assertEqual(plan["categories"][0]["name"], "Desk / talk")
        self.assertEqual(plan["categories"][0]["key"], "1|talk")

    def test_duplicate_slugs(self) -> None:
        plan = destination_layout(
            [
                {"channel_id": "10", "guild_id": "1", "guild_name": "Desk", "channel_name": "General", "parent": ""},
                {"channel_id": "11", "guild_id": "1", "guild_name": "Desk", "channel_name": "general", "parent": "talk"},
            ],
            "mirror",
        )
        self.assertEqual([item["name"] for item in plan["channels"]], ["general", "general-2"])

    def test_slug_and_topic(self) -> None:
        plan = destination_layout(
            [
                {
                    "channel_id": "10",
                    "guild_id": "1",
                    "guild_name": "Desk",
                    "channel_name": "Hello, World!",
                    "parent": "",
                    "topic": "x" * 1100,
                }
            ],
            "n" * 150,
        )
        self.assertEqual(plan["channels"][0]["name"], "hello-world")
        self.assertEqual(len(plan["channels"][0]["topic"]), 1024)
        self.assertEqual(len(plan["name"]), 100)

    def test_empty_slug(self) -> None:
        plan = destination_layout(
            [{"channel_id": "10", "guild_id": "1", "guild_name": "Desk", "channel_name": "!!!", "parent": ""}],
            "mirror",
        )
        self.assertEqual(plan["channels"][0]["name"], "channel")

    def test_unicode_names_keep_letters(self) -> None:
        names = ["日本語 チャット", "Привет Мир", "Café_Lounge", "!!!", "ü" * 150]
        plan = destination_layout(
            [
                {"channel_id": str(10 + n), "guild_id": "1", "guild_name": "Desk", "channel_name": name, "parent": ""}
                for n, name in enumerate(names)
            ],
            "mirror",
        )
        got = [item["name"] for item in plan["channels"]]
        self.assertEqual(got[:4], ["日本語-チャット", "привет-мир", "café-lounge", "channel"])
        self.assertEqual(got[4], "ü" * 100)

    def test_underscore_becomes_hyphen(self) -> None:
        plan = destination_layout(
            [{"channel_id": "10", "guild_id": "1", "guild_name": "Desk", "channel_name": "__dev__ notes__", "parent": ""}],
            "mirror",
        )
        self.assertEqual(plan["channels"][0]["name"], "dev-notes")

    def test_webhook_name_rules(self) -> None:
        samples = ["discord-updates", "Clyde", "DiscordFan", "discord.", "x CLYDE discord y", "ev eryone"]
        for value in samples:
            name = webhook_name(value)
            self.assertNotIn("discord", name.casefold())
            self.assertNotIn("clyde", name.casefold())
            self.assertEqual(webhook_name(name), name)
        self.assertEqual(webhook_name("DiscordFan"), "Dis.cordFan")
        self.assertEqual(webhook_name("discord."), "dis.cord.")
        self.assertEqual(webhook_name("clyde"), "cly.de")
        self.assertEqual(webhook_name("everyone"), "everyone.")
        self.assertEqual(webhook_name("Here"), "Here.")
        self.assertEqual(webhook_name("here"), "here.")
        self.assertEqual(webhook_name(""), "mirror")
        self.assertEqual(webhook_name("   "), "mirror")
        self.assertEqual(webhook_name("general"), "general")
        self.assertEqual(webhook_name("  two   words "), "two words")
        long = webhook_name("a" * 120)
        self.assertEqual(len(long), 80)
        self.assertEqual(webhook_name(long), long)
        spaced = webhook_name("b" * 79 + " c")
        self.assertEqual(webhook_name(spaced), spaced)
        for value in ["everyone", "here", "x" * 200, "discord" * 20]:
            name = webhook_name(value)
            self.assertTrue(1 <= len(name) <= 80)
            self.assertNotIn(name.casefold(), {"everyone", "here"})
            self.assertEqual(webhook_name(name), name)


if __name__ == "__main__":
    unittest.main()
