from __future__ import annotations

import unittest

from mirror.provision import destination_layout


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


if __name__ == "__main__":
    unittest.main()
