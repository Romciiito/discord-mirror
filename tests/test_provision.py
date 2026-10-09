from __future__ import annotations

import unittest

from mirror.provision import copy_layout, same_name, webhook_name


def row(channel_id: str, name: str, parent: str = "", topic: str = "", enabled: bool = True) -> dict:
    return {"channel_id": channel_id, "channel_name": name, "parent": parent, "topic": topic, "enabled": enabled}


class CopyLayoutTests(unittest.TestCase):
    def test_first_source_keeps_its_own_categories(self) -> None:
        plan = copy_layout("Desk", [row("10", "general", "Talk"), row("11", "dev", "Talk", "  builds  here "), row("12", "news")], False)
        self.assertEqual(plan["categories"], [{"key": "talk", "name": "Talk"}])
        self.assertEqual(
            plan["channels"],
            [
                {"source_id": "10", "name": "general", "category_key": "talk", "topic": ""},
                {"source_id": "11", "name": "dev", "category_key": "talk", "topic": "builds here"},
                {"source_id": "12", "name": "news", "category_key": "", "topic": ""},
            ],
        )

    def test_copy_layout_prefixes_only_a_shared_target(self) -> None:
        rows = [row("10", "general", "Talk"), row("12", "news")]
        plan = copy_layout("Desk", rows, True)
        self.assertEqual(plan["categories"], [{"key": "desk / talk", "name": "Desk / Talk"}, {"key": "desk", "name": "Desk"}])
        self.assertEqual([c["category_key"] for c in plan["channels"]], ["desk / talk", "desk"])
        self.assertEqual(copy_layout("Desk", rows, False)["categories"], [{"key": "talk", "name": "Talk"}])

    def test_copy_layout_skips_disabled_and_bad_rows_and_folds_categories(self) -> None:
        plan = copy_layout("  Desk  ", [row("10", "a", "Talk"), row("11", "b", "talk"), row("x", "c"), row("13", "d", enabled=False), row("14", "   ")], False)
        self.assertEqual(plan["categories"], [{"key": "talk", "name": "Talk"}])
        self.assertEqual([c["source_id"] for c in plan["channels"]], ["10", "11", "14"])
        self.assertEqual(plan["channels"][2]["name"], "channel")
        self.assertEqual(copy_layout("", [row("1", "a")], True)["categories"], [{"key": "server", "name": "server"}])
        self.assertEqual(len(copy_layout("x" * 200, [row("1", "y" * 200, "z" * 200)], True)["categories"][0]["name"]), 100)
        self.assertEqual(len(copy_layout("x", [row("1", "y" * 200)], False)["channels"][0]["name"]), 100)
        # Store.selection() keeps the tick as the integer 0 or 1
        self.assertEqual(copy_layout("Desk", [row("15", "e") | {"enabled": 0}], False)["channels"], [])

    def test_same_name_follows_what_discord_shows(self) -> None:
        self.assertTrue(same_name("General Chat", "general-chat"))
        self.assertTrue(same_name("dev", "DEV"))
        self.assertTrue(same_name("a  b", "a-b"))
        self.assertFalse(same_name("general", "general-2"))
        self.assertFalse(same_name("", "channel"))
        # a channel named "channel" (also the name an empty source name gets) is reused on the next fill (decision 9i)
        self.assertTrue(same_name("Channel", "channel"))
        self.assertFalse(same_name("!!!", "channel"))


class ProvisionTests(unittest.TestCase):
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
