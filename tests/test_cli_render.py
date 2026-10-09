from __future__ import annotations

import unittest

from mirror.cli.controller import Controller
from mirror.cli.render import feed_lines, render, status_line
from tests.test_cli_controller import FakeEngine, read_keychain_fake


def ui_on(screen: str, **snap) -> Controller:
    engine = FakeEngine()
    for key, value in snap.items():
        setattr(engine, key, value)
    ui = Controller(engine, read_keychain=read_keychain_fake)
    ui.refresh()
    ui.flow = {"screen": screen, "index": 0, "action": None, "locked": False}
    return ui


class RenderTests(unittest.TestCase):
    def test_status_line(self) -> None:
        ui = ui_on("menu")
        self.assertEqual(status_line(ui), "signed out · stopped · 0 mirrored · -")
        ui = ui_on("menu", user={"id": "1", "username": "sosa", "global_name": "Sosa"}, running=True, status="live", mirrored=12)
        ui.error = "webhook 404"
        self.assertEqual(status_line(ui), "Sosa · running · 12 mirrored · webhook 404")

    def test_menu_rows_cursor_and_hint(self) -> None:
        ui = ui_on("menu")
        lines = render(ui, 60, 10)
        self.assertEqual(len(lines), 10)
        self.assertEqual(lines[0], "signed out · stopped · 0 mirrored · -")
        self.assertEqual(lines[1], "menu")
        self.assertEqual(lines[2], "> 1  Start/Resume mirror")
        self.assertEqual(lines[3], "  2  Settings")
        self.assertEqual(lines[4], "  3  Exit")
        self.assertEqual(lines[-1], "up and down move, enter or a number opens, esc back")

    def test_settings_shows_locked_marks(self) -> None:
        ui = ui_on("settings")
        lines = render(ui, 60, 12)
        self.assertIn("  2  Select servers  locked", lines)
        self.assertIn("  4  Interface: UI (unavailable now)  locked", lines)

    def test_token_screen_shows_field_check_and_masks_the_token(self) -> None:
        ui = ui_on("token")
        ui.typing = "token"
        ui.draft = "abc"
        ui.token_check = "✗ token rejected by Discord"
        lines = render(ui, 60, 14)
        self.assertEqual(lines[1], "Add token")
        self.assertEqual(lines[2], "> token ***")
        self.assertIn("✗ token rejected by Discord", lines)
        self.assertEqual(lines[-1], "enter or esc keeps it")
        ui.typing = None
        ui.fields["token"] = "x" * 40
        lines = render(ui, 60, 14)
        self.assertEqual(lines[2], "> 1  token  set")
        self.assertEqual(lines[3], "  2  keep on this machine  yes")

    def test_feed_lines_format(self) -> None:
        messages = [
            {"id": "1", "guild_name": "Qwen", "channel_name": "general", "author": "ann", "content": "hello"},
            {"id": "2", "guild_name": "Qwen", "channel_name": "general", "author": "bob", "content": "fixed", "edited": "t"},
            {"id": "3", "channel_name": "dev", "author": "cy", "content": "", "attachments": [{}], "deleted": True},
        ]
        self.assertEqual(
            feed_lines(messages, 2),
            ["Qwen / #general · bob: fixed (edited)", "#dev · cy: (attachment) (deleted)"],
        )
        self.assertEqual(feed_lines(messages, 0), [])

    def test_running_screen_fills_the_height_with_the_feed(self) -> None:
        ui = ui_on("running", running=True, status="live")
        ui.messages = [{"id": str(n), "channel_name": "c", "author": "a", "content": f"m{n}"} for n in range(50)]
        lines = render(ui, 60, 10)
        self.assertEqual(len(lines), 10)
        self.assertEqual(lines[1], "mirroring")
        self.assertEqual(lines[2], "#c · a: m44")
        self.assertEqual(lines[7], "#c · a: m49")
        self.assertEqual(lines[-1], "esc returns to the menu")

    def test_render_fits_small_terminal(self) -> None:
        ui = ui_on("running", running=True)
        ui.messages = [{"id": "1", "channel_name": "c", "author": "a", "content": "x" * 100}]
        ui.error = "e" * 100
        lines = render(ui, 20, 3)
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(len(line) <= 20 for line in lines))
        self.assertEqual(render(ui, 20, 1), [status_line(ui)[:20]])

    def test_render_keeps_exactly_height_lines_at_zero_size(self) -> None:
        # the contract is "exactly height lines, each at most width characters", down to zero
        ui = ui_on("menu")
        self.assertEqual(render(ui, 20, 0), [])
        self.assertEqual(render(ui, 0, 3), ["", "", ""])
        self.assertEqual(render(ui, 0, 0), [])
        self.assertEqual(render(ui, -5, -5), [])

    def test_control_characters_never_split_a_line(self) -> None:
        # the terminal layer joins the lines with "\n" (plan Task 8), so a newline inside
        # an error, a name or a message would push the hint off the screen
        ui = ui_on("running", running=True)
        ui.error = "bad\nthing\r"
        ui.messages = [
            {"id": "1", "guild_name": "Café 漢字 \U0001F468‍\U0001F469‍\U0001F467", "channel_name": "gen eral",
             "author": "a\tb", "content": "one\r\ntwo\x1b[2J"},
        ]
        lines = render(ui, 80, 10)
        self.assertEqual(len("\n".join(lines).split("\n")), 10)
        self.assertEqual(lines[0], "signed out · running · 0 mirrored · bad thing ")
        self.assertEqual(
            lines[2],
            "Café 漢字 \U0001F468‍\U0001F469‍\U0001F467 / #gen eral · a b: one  two [2J",
        )
        self.assertEqual(feed_lines(ui.messages, 1), [lines[2]])

    def test_servers_screen_marks_and_channels(self) -> None:
        ui = ui_on("servers")
        ui.guilds = [{"id": "g1", "name": "Qwen"}, {"id": "g2", "name": "Moody"}]
        ui.picked = {"c1": {"channel_id": "c1", "guild_id": "g1", "enabled": True}}
        lines = render(ui, 60, 8)
        self.assertEqual(lines[1], "Select servers")
        self.assertEqual(lines[2], "> [x] Qwen")
        self.assertEqual(lines[3], "  [ ] Moody")
        ui.depth = "channels"
        ui.active_guild = ui.guilds[0]
        ui.channel_rows = [{"id": "c1", "name": "general", "parent": "text"}, {"id": "c2", "name": "dev", "parent": ""}]
        lines = render(ui, 60, 8)
        self.assertEqual(lines[1], "Qwen")
        self.assertEqual(lines[2], "> [x] text / #general  no webhook")
        self.assertEqual(lines[3], "  [ ] #dev")
        self.assertEqual(lines[-1], "enter toggles, a selects all, esc back")
        ui.picked["c1"]["webhook_url"] = "https://discord.com/api/webhooks/1/t"
        self.assertEqual(render(ui, 60, 8)[2], "> [x] text / #general  webhook set")
        ui.typing = "webhook"
        ui.webhook_channel = ui.channel_rows[1]
        ui.draft = "https://"
        self.assertEqual(render(ui, 60, 8)[3], "  [ ] #dev  webhook: https://")
        self.assertEqual(render(ui, 60, 8)[-1], "enter keeps it, esc cancels the edit")

    def test_webhook_settings_rows(self) -> None:
        ui = ui_on("webhooks")
        lines = render(ui, 60, 8)
        self.assertEqual(lines[1], "Webhook settings")
        self.assertEqual(lines[2], "> 1  backfill  live only")
        self.assertEqual(lines[3], "  2  threads  off")
        self.assertEqual(lines[4], "  3  back")


if __name__ == "__main__":
    unittest.main()
