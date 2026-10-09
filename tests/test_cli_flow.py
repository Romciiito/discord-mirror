from __future__ import annotations

import unittest

from mirror.cli.flow import items, reduce

WELCOME = {"screen": "welcome", "index": 0, "action": None, "locked": False}


def st(screen: str, index: int, action=None, locked=False) -> dict:
    return {"screen": screen, "index": index, "action": action, "locked": locked}


class FlowTests(unittest.TestCase):
    def test_null_state_opens_welcome(self) -> None:
        self.assertEqual(reduce(None, {"key": "Enter"}, {"tokenOk": True}), WELCOME)
        self.assertEqual(reduce(None), WELCOME)

    def test_any_key_leaves_welcome_for_the_menu(self) -> None:
        for key in ["Enter", "a", "ArrowDown", "1", "Escape"]:
            want = WELCOME if key == "Escape" else st("menu", 0)
            self.assertEqual(reduce({"screen": "welcome", "index": 0}, {"key": key}), want)

    def test_menu_labels_and_order(self) -> None:
        want = [
            {"id": "start", "label": "Start/Resume mirror"},
            {"id": "settings", "label": "Settings"},
            {"id": "exit", "label": "Exit"},
        ]
        self.assertEqual(items("menu"), want)
        self.assertEqual(items("menu", {"tokenOk": True}), want)

    def test_settings_items_and_locks(self) -> None:
        def rows(servers_locked: bool) -> list[dict]:
            return [
                {"id": "token", "label": "Add token"},
                {"id": "servers", "label": "Select servers", "locked": servers_locked},
                {"id": "webhooks", "label": "Webhook settings"},
                {"id": "interface", "label": "Interface: UI (unavailable now)", "locked": True},
                {"id": "back", "label": "Back to menu"},
            ]

        self.assertEqual(items("settings", {"tokenOk": False}), rows(True))
        self.assertEqual(items("settings"), rows(True))
        self.assertEqual(items("settings", {"tokenOk": True}), rows(False))
        for screen in ["welcome", "token", "webhooks", "servers", "exit"]:
            self.assertEqual(items(screen, {"tokenOk": True}), [])

    def test_locked_items_do_not_open(self) -> None:
        state = {"screen": "settings", "index": 0}
        self.assertEqual(reduce(state, {"key": "2"}, {"tokenOk": False}), st("settings", 1, locked=True))
        self.assertEqual(reduce(state, {"key": "2"}), st("settings", 1, locked=True))
        self.assertEqual(reduce({"screen": "settings", "index": 1}, {"key": "Enter"}), st("settings", 1, locked=True))
        self.assertEqual(reduce(state, {"key": "4"}, {"tokenOk": True}), st("settings", 3, locked=True))
        self.assertEqual(reduce({"screen": "settings", "index": 3}, {"key": "Enter"}, {"tokenOk": True}), st("settings", 3, locked=True))
        self.assertEqual(state, {"screen": "settings", "index": 0})

    def test_digit_or_enter_on_unlocked_servers_opens_servers(self) -> None:
        self.assertEqual(reduce({"screen": "settings", "index": 0}, {"key": "2"}, {"tokenOk": True}), st("servers", 0, "servers"))
        self.assertEqual(reduce({"screen": "settings", "index": 1}, {"key": "Enter"}, {"tokenOk": True}), st("servers", 0, "servers"))

    def test_escape_from_servers_returns_to_settings(self) -> None:
        ok = {"tokenOk": True}
        self.assertEqual(reduce({"screen": "servers", "index": 2}, {"key": "Escape"}, ok), st("settings", 0))
        self.assertEqual(reduce({"screen": "servers", "index": 0}, {"key": "Enter"}, ok), st("servers", 0))
        self.assertEqual(reduce({"screen": "servers", "index": 0}, {"key": "ArrowDown"}, ok), st("servers", 0))

    def test_start_action(self) -> None:
        state = {"screen": "menu", "index": 0}
        nxt = reduce(state, {"key": "Enter"})
        self.assertEqual(nxt, st("menu", 0, "start"))
        self.assertIsNot(nxt, state)
        self.assertEqual(reduce({"screen": "menu", "index": 2}, {"key": "1"}), st("menu", 0, "start"))

    def test_exit_screen(self) -> None:
        self.assertEqual(reduce({"screen": "menu", "index": 2}, {"key": "Enter"}), st("exit", 0, "exit"))
        self.assertEqual(reduce({"screen": "menu", "index": 0}, {"key": "3"}), st("exit", 0, "exit"))
        self.assertEqual(reduce({"screen": "exit", "index": 0}, {"key": "Escape"}), st("menu", 0))
        self.assertEqual(reduce({"screen": "exit", "index": 0}, {"key": "Enter"}), st("exit", 0))

    def test_wrap_from_last_item_to_first(self) -> None:
        self.assertEqual(reduce({"screen": "menu", "index": 2}, {"key": "ArrowDown"}), st("menu", 0))
        self.assertEqual(reduce({"screen": "menu", "index": 0}, {"key": "ArrowUp"}), st("menu", 2))
        self.assertEqual(reduce({"screen": "settings", "index": 4}, {"key": "ArrowDown"}, {"tokenOk": False}), st("settings", 0))
        self.assertEqual(reduce({"screen": "settings", "index": 0}, {"key": "ArrowUp"}, {"tokenOk": True}), st("settings", 4))

    def test_digit_out_of_range_is_ignored(self) -> None:
        self.assertEqual(reduce({"screen": "menu", "index": 1}, {"key": "9"}), st("menu", 1))
        self.assertEqual(reduce({"screen": "settings", "index": 2}, {"key": "9"}, {"tokenOk": True}), st("settings", 2))
        self.assertEqual(reduce({"screen": "menu", "index": 0}, {"key": "4"}), st("menu", 0))

    def test_navigation_and_form_keys(self) -> None:
        self.assertEqual(reduce({"screen": "menu", "index": 1}, {"key": "Enter"}), st("settings", 0, "settings"))
        self.assertEqual(reduce({"screen": "menu", "index": 0}, {"key": "2"}), st("settings", 0, "settings"))
        self.assertEqual(reduce({"screen": "settings", "index": 0}, {"key": "Enter"}), st("token", 0, "token"))
        self.assertEqual(reduce({"screen": "settings", "index": 0}, {"key": "3"}, {"tokenOk": False}), st("webhooks", 0, "webhooks"))
        self.assertEqual(reduce({"screen": "settings", "index": 4}, {"key": "Enter"}, {"tokenOk": True}), st("menu", 0, "back"))
        self.assertEqual(reduce({"screen": "settings", "index": 1}, {"key": "5"}), st("menu", 0, "back"))
        for key in ["ArrowUp", "1", "Enter"]:
            self.assertEqual(reduce({"screen": "token", "index": 0}, {"key": key}), st("token", 0))
        self.assertEqual(reduce({"screen": "webhooks", "index": 4}, {"key": "Escape"}), st("settings", 0))
        self.assertEqual(reduce({"screen": "webhooks", "index": 0}, {"key": "ArrowDown"}), st("webhooks", 0))
        self.assertEqual(reduce({"screen": "settings", "index": 2}, {"key": "Escape"}, {"tokenOk": True}), st("menu", 0))
        self.assertEqual(reduce({"screen": "menu", "index": 1}, {"key": "Escape"}), st("menu", 1))
        self.assertEqual(reduce({"screen": "welcome", "index": 0}, {"key": "Escape"}), WELCOME)
        self.assertEqual(reduce({"screen": "nope", "index": 4}, {"key": "x"}), st("menu", 0))
        self.assertEqual(reduce({"screen": "nope", "index": 4}, {"key": "Escape"}), WELCOME)
        self.assertEqual(reduce({"screen": "menu", "index": 1}, {"key": "a"}), st("menu", 1))
        state = {"screen": "menu", "index": 1}
        reduce(state, {"key": "ArrowDown"})
        self.assertEqual(state, {"screen": "menu", "index": 1})


if __name__ == "__main__":
    unittest.main()
