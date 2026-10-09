# Own Copies and Relay Format Implementation Plan (plans B and C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A ticked source server is filled into a server the owner owns with one key, the shared "mirror" server and its provisioning go, Start refuses in the engine when a ticked channel has no webhook, and the relay sends stickers and files 1:1 within Discord's limits.

**Architecture:** The store gains a `targets` table (one target server per source server) and loses the shared-server options. `provision.py` keeps `webhook_name` and gets a pure `copy_layout` that turns the ticked rows of one source into the categories and channels a fill creates. The engine's `fill_copy` writes those into a target the account owns, reusing channels of the same name and webhooks it made before, and never deletes. `Engine._start` carries the Start rule. The CLI opens a target picker with `c` on a server row. The relay uploads up to 10 files of up to 20 MiB each, replaces a larger file with one line and two links, uploads PNG, APNG and GIF stickers as images, always sends the `embeds` key on an edit and keeps the embeds under 6000 characters in total.

**Tech Stack:** Python 3.10+, aiohttp, `prompt_toolkit` 3.0.53, `unittest` + `IsolatedAsyncioTestCase` (as the existing tests). No new dependency.

**Spec:** GitHub issue #9 (`gh issue view 9`), design notes `docs/superpowers/specs/2026-10-08-terminal-cli-design.md` — decisions 9a, 9b', 9d, 9e, 9g', 9h, 9i, 9j, 9k, 9l, 9m, 9n, 9o (plan B) and 10a–10e (plan C); glossary `CONTEXT.md`; ADR `docs/adr/0001-copies-live-in-servers-the-owner-creates.md`. Plan A (`docs/superpowers/plans/2026-10-09-cli-shell.md`) is done on `main`'s PR #10; this plan builds on its head `3fb3269`.

## Global Constraints

- Python floor 3.10; CI runs Ubuntu 3.12 x64 and Windows 3.10 x64 / 3.12 x64 / 3.12 x86. No dependency without a `py3-none-any` or x86 wheel.
- Everything user-facing and in the repo is English (decision 7).
- Commit messages: one technical sentence ending with a period, as the repo does (`git log --oneline -5`). No AI trailers, no AI mentions in code, docs or commits.
- `git add` names files; never `git add -A`. Files are LF (`git ls-files --eol` shows `i/lf w/lf`); an editor that writes CRLF must be undone before the commit.
- Tests never call Discord: every HTTP object is a fake (`tests/test_engine.py` `FakeHTTP`, `tests/test_relay.py` `FakeSession`). Tests never touch a `data/` folder: every `Store` opens in a `tempfile.TemporaryDirectory()`.
- Mando writes to a Discord server only when the account **owns** it (`owner` flag of `/users/@me/guilds`, decision 9b') and **never deletes** anything there (decision 9i): no `DELETE` request appears in `Engine.fill_copy` or anything it calls.
- The Start rule (decisions 9f, 9k): a ticked channel without a webhook URL makes `Engine._start` raise `ApiError(400, "#<name> has no webhook")`; it never creates a server, channel or webhook.
- The test command and its module list are unchanged: `python -m unittest tests.test_core tests.test_store tests.test_keychain tests.test_provision tests.test_relay tests.test_gateway tests.test_engine tests.test_web tests.test_cli_flow tests.test_cli_controller tests.test_cli_render -q` (`CLAUDE.md`, `.github/workflows/test.yml`, `README.md`). The CI smoke step (`/api/state` 200, cross-origin `/api/stop` 403 "bad origin", same-origin 200) must keep passing.
- Hint lines fit 60 columns (plan A's rule): count the characters of every new hint.
- **Error handling around every engine call in the controller** (plan A's rule): `except (ApiError, RuntimeError) as exc: self.error = str(exc)` and then `except Exception: log.exception("<what> failed"); self.error = UNEXPECTED`; `CancelledError` is never caught; `busy` is set for the call's duration and released in `finally`.
- Engine notes are one line each, lower case, no trailing period, as the existing `self.note(...)` calls.

## Review Focus

Inputs the spec implies but no decision names; each line's test is pinned to the task that owns the code:

1. **A second fill into the same target** (after a crash mid-fill, or because the owner ticked two more channels): no second channel of the same name and no second webhook on a reused channel, also when two fills of the same source start before the first one ends (the second waits and finds what the first made), and when Discord fails to list a found channel's webhooks (the fill then wires that channel later, never with a second webhook). → Task 4 (`test_fill_reuses_channels_and_their_webhooks`, `test_two_fills_at_once_create_each_channel_and_webhook_once`, `test_a_channel_whose_webhooks_cannot_be_listed_gets_no_second_webhook`).
2. **A ticked channel that the lists do not show** (decision 9k's row, stored without `parent`): a fill still creates a channel for it from the stored name. → Task 4 (`test_fill_covers_an_unlisted_ticked_channel`).
3. **Two sources into one target**: the second gets "<source> / <category>" categories and a "<source>" category for loose channels; the first's channels are untouched, also when the first source is filled again after the second arrived (no new category, channel or webhook). "Holds" is what the target holds, not where the links point now: a source filled into another target later, or whose first fill failed halfway, still has its channels in the first target, so a source filled there afterwards is prefixed too. A channel of the same name outside a source's own category (decision 9i) is taken only when it carries that row's webhook or no other source is in the target, so the first source keeps the server's own `general` it reused after a second source arrived and never takes the second source's `general`; a second source's channel whose category Discord refuses is skipped, never put loose beside the first source's. Names do not tell sources apart (server names are not unique, a later source's "<source>" can be a category of the first source or of the server itself), so a category is the source's a fill made it for: each category a fill creates is recorded for its source, a later source uses only the categories recorded for it, the first source also those no fill made, and no fill uses a category recorded for another source (two categories of one name can then stand in the target). → Task 2 (`test_copy_layout_prefixes_only_a_shared_target`) and Task 4 (`test_second_source_into_a_target_gets_prefixed_categories`, `test_refill_of_the_first_source_keeps_its_names_after_a_second_source`, `test_a_target_keeps_holding_a_source_that_moved_to_another_target`, `test_a_target_holds_a_source_whose_first_fill_failed`, `test_first_source_reuses_channels_of_the_same_name_in_other_categories`, `test_a_first_source_finds_its_channel_by_its_webhook_after_a_second_source`, `test_a_second_source_whose_category_fails_takes_no_loose_channel`, `test_a_second_source_named_like_a_category_of_the_first_gets_its_own_category`, `test_sources_of_the_same_name_get_their_own_categories`, `test_a_first_source_never_takes_a_category_made_for_a_later_source`, `test_a_later_source_never_takes_a_category_no_fill_made`, and in `tests/test_core.py` `test_a_target_remembers_every_source_filled_into_it`, `test_a_target_tells_whether_it_holds_another_source`, `test_a_target_remembers_the_categories_made_for_each_source`).
4. **A fill while the mirror runs**: the new webhook URLs are mirrored to without a restart. → Task 4 (`test_fill_while_running_refreshes`).
5. **A message with a sticker and a file over 20 MiB**: one post, the sticker uploaded, the file as one line with two links, no second attempt. → Task 8 (`test_sticker_and_oversize_file_in_one_post`).
6. **Names that differ only in an emoji, a separator such as "┃" or an underscore** (`🔥-general` and `💬-general`): two channels in the copy, never one channel with one webhook for both; an emoji-only name is found again on the next fill. The same holds for two source channels with the very same name in one category (Discord allows it): each existing channel serves one row only, so each gets its own channel and webhook, and a refill finds each again, also when a row without a URL comes first in the store, Discord lists the copies in another order, or a copy stands in another category. → Task 2 (`test_same_name_keeps_emoji_separators_and_underscores_apart`) and Task 4 (`test_fill_gives_two_channels_of_the_same_name_their_own_copies`, `test_refill_of_two_channels_of_the_same_name_keeps_each_on_its_own_copy`, `test_a_newly_ticked_row_of_the_same_name_never_takes_a_copy_that_carries_another_rows_webhook`, `test_a_row_without_a_url_never_takes_the_copy_of_a_row_of_the_same_name`, `test_a_row_ticked_in_its_own_place_never_takes_a_copy_found_elsewhere_for_another_row`).
7. **A ticked channel with an own webhook (decision 9g) at a fill**: the fill covers it (decision 9n) and gives it the copy's webhook, the `hooks` memory follows, and the engine notes "<n> earlier webhook url(s) replaced" in its log; a refill that finds its own webhooks again says nothing. The CLI never shows the engine's log, so its line after the fill starts with the same count, the `replaced` of the fill report (the CLI counts nothing itself: a count from its rows before and after the refresh differed from the engine's on a webhook found again under another host; a fill into another target replaces every URL of the earlier copy and says so too; the Task 6 review measured the note invisible after `c` and Enter). Whether an own webhook should survive a fill is the owner's open point (the gap after 9o in the design notes). → Task 4 (`test_fill_replaces_an_own_webhook_of_a_ticked_channel_and_notes_it`, `test_refill_of_the_first_source_keeps_its_names_after_a_second_source`, `test_fill_that_finds_a_webhook_pasted_with_another_host_replaces_nothing`) and Task 6 (`test_a_fill_that_replaces_earlier_webhook_urls_says_so`, `test_the_line_after_a_fill_shows_the_count_the_engine_reports`).
8. **A fill that fails after its writes** (the refresh of a running mirror raises after `Engine.fill_copy` stored the copy's URLs and the link): the CLI reads the snapshot again on every error of the fill, so its rows hold the copy's URLs and the next save keeps them instead of putting the URLs from before back (measured in the Task 6 review). → Task 6 (`test_a_fill_that_fails_after_its_writes_shows_what_the_store_holds`).
9. **An age-restricted source channel**: its copy is created with the age gate (`nsfw`), as the copy code this branch removed did (`_wire_copy` at 3fb3269); a channel the fill finds again is never altered. The restriction travels on the stored row as the parent and the topic do, so the fill reads no listing of the source. → Task 2 (`test_copy_layout_carries_the_age_restriction`) and Task 4 (`test_fill_creates_the_copy_of_an_age_restricted_channel_with_the_age_gate`, `test_store_without_the_age_restriction_column_opens`, `test_channel_list_and_saved_rows_carry_the_age_restriction`; in `tests/test_cli_controller.py` `test_a_ticked_channel_keeps_its_age_restriction`).

---

## File structure

| File | Responsibility |
|---|---|
| `mirror/store.py` | `targets` table (from Task 4 a link's `shared` flag is read from `fills`); `fills` table, `record_fill` and `holds_another` (Task 4: the sources each target holds); `fill_categories` table, `record_category` and `category_sources` (Task 4: the source each category a fill made belongs to); `options()` without `global_webhook`, `dest_name`, `dest_guild_id`; `set_options(backfill, include_threads, mirror)`; `set_target`, `targets`; `set_dest_guild` and `clear_destination` removed |
| `mirror/provision.py` | `copy_layout(source_name, rows, shared)` and `same_name(a, b)` (new, pure); `webhook_name` kept; `destination_layout` removed |
| `mirror/engine.py` | `owned_guilds()`, `fill_copy()` (under the `_setup` lock), `_fill_copy()`, `_fill()`, `_hooks_on()`, `_webhook_on()`; `_start` with the Start rule; `copy_guild`, `_wire_copy`, `reset_destination`, `_provision`, `_wire_layout`, `GONE` removed; no `global_webhook` anywhere |
| `mirror/web.py` | `GET /api/targets`, `POST /api/guilds/{guild_id}/fill`; the copy and reset routes removed |
| `mirror/cli/controller.py` | `c` opens the target picker (depth `targets`), Enter fills, Esc returns; the 9e notice on untick; hints |
| `mirror/cli/render.py` | the target picker screen; `copy: <name>` on a server row |
| `mirror/relay.py` | 20 MiB per file, no total cap, the over-limit line (10b); stickers as uploads (10a); `embeds` always on an edit (#6); 6000-character embed total (#8 part); `guild_id` in the view |
| `tests/test_core.py`, `tests/test_provision.py`, `tests/test_engine.py`, `tests/test_web.py`, `tests/test_cli_controller.py`, `tests/test_cli_render.py`, `tests/test_relay.py` | extended and pruned |
| `README.md`, `CONTEXT.md` | the fill, the key `c`, the relay limits |

Names used across tasks: a **target** is `{"id": str, "name": str}`; a **link** in `Store.targets()` is `{"target_id": str, "target_name": str}` keyed by the source server id, and from Task 4 on also `"shared": bool` (whether another source was filled into that target before this source, decision 9o; the value the `fills` record holds for that pair, which `Store.record_fill` gave the source there); a **layout** is `{"categories": [{"key", "name"}], "channels": [{"source_id", "name", "category_key", "topic"}]}`; **pairs** are `list[tuple[source_channel_id, webhook_url]]` as `Store.fill_webhooks` takes them.

---

### Task 1: Store — one target per source server, no shared-server options

**Files:**
- Modify: `mirror/store.py`
- Test: `tests/test_core.py` (`test_store_roundtrip`, new `test_store_targets_per_source`, new `test_old_database_opens_and_ignores_the_shared_server_columns`), `tests/test_engine.py` (`test_store_hooks_survive_untick_and_retick`, line 202)

**Interfaces:**
- Produces: `Store.options() -> {"backfill": int, "include_threads": bool, "mirror": bool}`; `Store.set_options(backfill: int, include_threads: bool, mirror: bool) -> None`; `Store.set_target(source_guild_id: str, target_guild_id: str, target_name: str) -> None`; `Store.targets() -> dict[str, dict[str, str]]` as `{source_guild_id: {"target_id", "target_name"}}`. `Store.set_dest_guild` and `Store.clear_destination` no longer exist. `fill_webhooks`, `hooks`, `selection`, `replace_selection` unchanged.
- Callers of `set_options` to update in this task so the suite stays green: `mirror/engine.py:344` (inside `copy_guild`, removed in Task 3 — for now pass `(options["backfill"], options["include_threads"], True)`), `:463` (`save_setup`), `:489` (`_start`); `tests/test_engine.py:436`, `:459`, `:474`, `:667`, `:688`, `:714`, `:742` (all `set_options(0, False, "", True)`; measured in the Task 1 run). `options()["dest_name"]`, `["dest_guild_id"]`, `["global_webhook"]` readers: `mirror/engine.py:518` and `:521` (`_provision`), `:877`, `:886`, `:897`-`:898` — until Task 3 removes them, read with `.get(..., "")` so nothing raises. `clear_destination`/`set_dest_guild` callers: `mirror/engine.py:345-346`, `:469`, `:529` and the tests named above — Task 3 removes the engine code; in this task replace `self.store.clear_destination()` in `copy_guild` and `reset_destination` with nothing (the method goes) and `set_dest_guild(...)` with nothing, and delete the three `_provision`/`copy_guild` tests that assert on them only if they fail (`test_provision_reports_gone_destination`, `test_provision_into_existing_server_reuses_categories`, `test_start_while_running_provisions_and_refreshes`, `test_stop_during_running_start_is_respected`, `test_copy_guild_keeps_new_destination`); Task 3 rewrites what survives. Record every deleted test in the commit message. The new snapshot key `targets` breaks the key-set assertion of `tests/test_web.py` `test_data_dir_with_spaces` (line 204); add `"targets"` to that set.

- [ ] **Step 1: Write the failing tests**

In `tests/test_core.py`, replace `test_store_roundtrip` and add two tests:

```python
    def test_store_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            store.set_token("x" * 50, True)
            self.assertEqual(store.token(), "x" * 50)
            store.set_options(25, True, True)
            self.assertEqual(store.options(), {"backfill": 25, "include_threads": True, "mirror": True})
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
            self.assertEqual(store.hooks(), {"10": "https://discord.com/api/webhooks/1/abc"})
            store.remember_relay("1", "10", "https://discord.com/api/webhooks/1/abc", "99")
            self.assertEqual(store.relay_row("1")["webhook_message_id"], "99")
            store.forget_token()
            self.assertEqual(store.token(), "")
            if sys.platform != "win32":
                self.assertEqual((Path(tmp) / "state.db").stat().st_mode & 0o777, 0o600)
            store.close()

    def test_store_targets_per_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            self.assertEqual(store.targets(), {})
            store.set_target("5", "900", "Desk copy")
            store.set_target("6", "900", "Desk copy")
            store.set_target("5", "901", "Other")
            self.assertEqual(
                store.targets(),
                {"5": {"target_id": "901", "target_name": "Other"}, "6": {"target_id": "900", "target_name": "Desk copy"}},
            )
            self.assertFalse(hasattr(store, "set_dest_guild"))
            self.assertFalse(hasattr(store, "clear_destination"))
            store.close()

    def test_old_database_opens_and_ignores_the_shared_server_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "state.db")
            conn.executescript(
                """
                CREATE TABLE account (id INTEGER PRIMARY KEY CHECK (id = 1), token TEXT, keep INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE options (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    backfill INTEGER NOT NULL DEFAULT 0,
                    include_threads INTEGER NOT NULL DEFAULT 0,
                    global_webhook TEXT NOT NULL DEFAULT '',
                    mirror INTEGER NOT NULL DEFAULT 0,
                    dest_name TEXT NOT NULL DEFAULT 'mirror',
                    dest_guild_id TEXT NOT NULL DEFAULT ''
                );
                INSERT INTO options (id, backfill, global_webhook, dest_name, dest_guild_id) VALUES (1, 50, 'https://x', 'Old', '7');
                CREATE TABLE selection (
                    channel_id TEXT PRIMARY KEY,
                    guild_id TEXT NOT NULL,
                    guild_name TEXT NOT NULL DEFAULT '',
                    channel_name TEXT NOT NULL DEFAULT '',
                    webhook_url TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1
                );
                INSERT INTO selection (channel_id, guild_id, guild_name, channel_name, webhook_url) VALUES ('10', '5', 'Desk', 'general', 'https://discord.com/api/webhooks/1/abc');
                """
            )
            conn.commit()
            conn.close()
            store = Store(tmp)
            self.assertEqual(store.options(), {"backfill": 50, "include_threads": False, "mirror": False})
            self.assertEqual(store.selection()[0]["webhook_url"], "https://discord.com/api/webhooks/1/abc")
            self.assertEqual(store.selection()[0]["parent"], "")
            self.assertEqual(store.hooks(), {"10": "https://discord.com/api/webhooks/1/abc"})
            self.assertEqual(store.targets(), {})
            store.set_options(0, True, True)
            self.assertEqual(store.options(), {"backfill": 0, "include_threads": True, "mirror": True})
            store.close()
```

`tests/test_core.py` does not import `sqlite3` yet (measured: its imports are `json`, `sys`, `tempfile`, `unittest`, `zlib`, `Path`); add `import sqlite3` to them. In `tests/test_engine.py` `test_store_hooks_survive_untick_and_retick`, delete the five lines from `self.store.clear_destination()` to the end of the test (the hooks memory is kept, decision 9l; a cleared destination no longer exists).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -X utf8 -m unittest tests.test_core -q`
Expected: FAIL — `set_options() missing 1 required positional argument` for the roundtrip, `AttributeError: 'Store' object has no attribute 'targets'` for the two new tests.

- [ ] **Step 3: Implement the store**

In `mirror/store.py`:

```python
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
```

`_migrate` keeps only the `selection` columns and the `hooks` back-fill (the old `options` columns `global_webhook`, `dest_name`, `dest_guild_id` stay in old files and are never read, decision 9h):

```python
    def _migrate(self) -> None:
        picked_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(selection)")}
        if "parent" not in picked_cols:
            self.conn.execute("ALTER TABLE selection ADD COLUMN parent TEXT NOT NULL DEFAULT ''")
        if "topic" not in picked_cols:
            self.conn.execute("ALTER TABLE selection ADD COLUMN topic TEXT NOT NULL DEFAULT ''")
        self.conn.execute(
            "INSERT OR IGNORE INTO hooks (channel_id, webhook_url) "
            "SELECT channel_id, webhook_url FROM selection WHERE webhook_url != ''"
        )
```

Options and targets:

```python
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
```

Delete `set_dest_guild` and `clear_destination`. Then update the callers listed under Interfaces so the whole suite runs: in `mirror/engine.py` change the three `set_options` calls, make the four `options[...]` reads of removed keys `options.get(..., "")`, drop the `clear_destination`/`set_dest_guild` calls, and in `tests/test_engine.py` change line 667 to `self.engine.store.set_options(0, False, True)`. Add `"targets": self.store.targets()` to `Engine.snapshot()` (the CLI and the API read it in Tasks 5 and 6).

- [ ] **Step 4: Run the full suite**

Run: `PYTHONUTF8=1 python -X utf8 -m unittest tests.test_core tests.test_store tests.test_keychain tests.test_provision tests.test_relay tests.test_gateway tests.test_engine tests.test_web tests.test_cli_flow tests.test_cli_controller tests.test_cli_render -q`
Expected: OK. If one of the five provisioning tests named under Interfaces fails only because `set_dest_guild`/`clear_destination` are gone, delete that test (Task 3 replaces the behaviour) and name it in the commit message.

- [ ] **Step 5: Commit**

```bash
git add mirror/store.py mirror/engine.py tests/test_core.py tests/test_engine.py tests/test_web.py
git commit -m "Store one target server per source server and drop the shared-server options."
```

---

### Task 2: `copy_layout` — the categories and channels a fill creates

**Files:**
- Modify: `mirror/provision.py`
- Test: `tests/test_provision.py`

**Interfaces:**
- Produces: `copy_layout(source_name: str, rows: list[dict], shared: bool) -> dict` with `{"categories": [{"key": str, "name": str}], "channels": [{"source_id": str, "name": str, "category_key": str, "topic": str, "nsfw": True}]}` (`nsfw` only on a channel whose row has a true `nsfw`); `same_name(a: str, b: str) -> bool` (two names of the same channel: equal after case folding with each run of whitespace written as one dash; every other character, emoji, `┃` and `_` included, counts); `webhook_name` unchanged. `shared` means this source is **not the first** filled into the target: Task 4 records the flag in the store's `fills` record (the sources each target holds) at the source's first fill into that target and passes the recorded value on a refill, so the first source keeps its own category names after a second source arrived (decision 9o). The `destination_layout` tests are removed here; the function itself goes in Task 3.
- Rules (decisions 9b', 9n, 9o): rows with `enabled` false (`False`, or the `0` that `Store.selection()` returns for an unticked row) or a non-numeric `channel_id` are skipped; a row's `parent` is the source category name (`""` for a loose channel); when `shared` is false the category name is the parent as is and a loose channel has `category_key == ""`; when `shared` is true the category name is `"<source> / <parent>"` and a loose channel goes under a category named `"<source>"`; categories are deduplicated case-insensitively, in order of first appearance, `key` is the folded name; channel `name` is the stored `channel_name` with collapsed whitespace, 100 characters at most, `"channel"` when empty; `topic` collapsed, 1024 at most; `"nsfw": True` when the row's `nsfw` is true (an age-restricted source channel, whose copy is created with the age gate; the value comes from the row alone, which carries it from the source's channel list as Task 4 describes), no `nsfw` key otherwise. `same_name` is true for two names with the same non-empty shown form (`"-".join(name.casefold().split())`), so a channel named `channel` (also the name an empty source name gets) and an emoji-only name such as `🔥` are reused on the next fill (decision 9i), while `🔥-general` and `💬-general`, `📢┃news` and `news`, or `a_b` and `a-b` stay two channels; an empty or whitespace-only name matches nothing. The source names come from Discord's own channel list (`Engine.channels`), so a channel a fill created carries the source's name again and the comparison needs no other folding.

- [ ] **Step 1: Write the failing tests**

Replace the `destination_layout` tests in `tests/test_provision.py` with these (keep the `webhook_name` tests as they are):

```python
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

    def test_copy_layout_carries_the_age_restriction(self) -> None:
        # the fill creates the copy of an age-restricted source channel with the age gate; other channels get no key
        plan = copy_layout("Desk", [row("10", "general") | {"nsfw": True}, row("11", "news") | {"nsfw": False}, row("12", "dev")], False)
        self.assertEqual(
            plan["channels"],
            [
                {"source_id": "10", "name": "general", "category_key": "", "topic": "", "nsfw": True},
                {"source_id": "11", "name": "news", "category_key": "", "topic": ""},
                {"source_id": "12", "name": "dev", "category_key": "", "topic": ""},
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
        self.assertFalse(same_name("   ", ""))
        # a channel named "channel" (also the name an empty source name gets) is reused on the next fill (decision 9i)
        self.assertTrue(same_name("Channel", "channel"))
        self.assertFalse(same_name("!!!", "channel"))

    def test_same_name_keeps_emoji_separators_and_underscores_apart(self) -> None:
        # two source channels that differ only in an emoji, a separator or an underscore are two channels in the copy
        self.assertFalse(same_name("🔥-general", "💬-general"))
        self.assertFalse(same_name("📢┃news", "news"))
        self.assertFalse(same_name("a_b", "a-b"))
        self.assertFalse(same_name("a_b", "a__b"))
        self.assertFalse(same_name("🔥", "💬"))
        # a name without a letter or digit is still found again on the next fill (decision 9i)
        self.assertTrue(same_name("🔥", "🔥"))
        self.assertTrue(same_name("📢┃News", "📢┃news"))
        self.assertTrue(same_name("!!!", "!!!"))
```

Check the file's existing imports (`unittest`) and keep the `webhook_name` test class.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -X utf8 -m unittest tests.test_provision -q`
Expected: FAIL — `ImportError: cannot import name 'copy_layout'`.

- [ ] **Step 3: Implement**

In `mirror/provision.py`, add next to `destination_layout` (which stays until Task 3, see Step 4):

```python
def copy_layout(source_name: str, rows: list[dict], shared: bool) -> dict[str, Any]:
    """The categories and channels a fill creates in the target for the ticked rows of one source server
    (decisions 9b', 9n, 9o). `shared` is false for the first source filled into a target, which keeps the
    source's own category names, and true for a source filled into a target another source was filled into
    first, which gets "<source> / <category>" and a "<source>" category for its loose channels. The names alone
    do not keep two sources apart (server names are not unique, and "<source>" can be a category of the first
    source), so the caller uses a category only for the source a fill made it for (`Store.record_category`).
    The caller keeps the flag in the store's record of the sources a target holds (`Store.record_fill`), so a
    refill gets the layout of the first fill even after a second source arrived."""
    source = " ".join(str(source_name or "").split())[:100] or "server"
    categories: list[dict[str, str]] = []
    seen: set[str] = set()
    channels: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("enabled", True):
            continue
        channel_id = str(row.get("channel_id") or "")
        if not channel_id.isdigit():
            continue
        parent = " ".join(str(row.get("parent") or "").split())
        if shared:
            label = f"{source} / {parent}" if parent else source
        else:
            label = parent
        label = label[:100]
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            categories.append({"key": key, "name": label})
        name = " ".join(str(row.get("channel_name") or "").split())[:100] or "channel"
        topic = " ".join(str(row.get("topic") or "").split())[:1024]
        channel: dict[str, Any] = {
            "source_id": channel_id,
            "name": name,
            "category_key": key if label else "",
            "topic": topic,
        }
        if row.get("nsfw"):
            channel["nsfw"] = True  # an age-restricted source channel: its copy is created with the age gate
        channels.append(channel)
    return {"categories": categories, "channels": channels}


def same_name(a: str, b: str) -> bool:
    """Whether two text channel names name the same channel for a fill (decision 9i): equal once the case is
    folded and each run of whitespace is written as one dash. Every other character counts, so emoji,
    separators such as "┃" and underscores keep two channels apart; the names come from Discord's own
    channel list, so a channel a fill created carries the source's name again. An empty name matches
    nothing."""
    first = _shown(a)
    return bool(first) and first == _shown(b)


def _shown(value: str) -> str:
    return "-".join(str(value or "").casefold().split())
```

Keep `webhook_name`, `_fold` and `_slug` unchanged; `same_name` does not use `_slug` (its `[\W_]+` rule drops emoji, `┃` and `_`, so `🔥-general` and `💬-general` would be one channel and `🔥` would never be found again), and `_slug` with `_SLUG` stays only for `destination_layout` until Task 3.

- [ ] **Step 4: Run the full suite**

Run the full test command from Global Constraints. Expected: OK. `mirror/engine.py` still imports and calls `destination_layout` in this task, so `destination_layout` stays in `provision.py` untouched here; Task 3 deletes it together with `_provision`.

- [ ] **Step 5: Commit**

```bash
git add mirror/provision.py tests/test_provision.py
git commit -m "Add copy_layout and same_name for filling a source's ticked channels into an owned server."
```

---

### Task 3: Start refuses a ticked channel without a webhook; the shared mirror server goes

**Files:**
- Modify: `mirror/engine.py`, `mirror/provision.py` (delete `destination_layout` with `_slug`, `_SLUG` and `import re`, which only it uses — `same_name` has its own `_shown`), `mirror/web.py` (delete the two routes and handlers)
- Test: `tests/test_engine.py`, `tests/test_web.py`

**Interfaces:**
- Produces: `Engine._start` raises `ApiError(400, "#<channel_name or id> has no webhook")` for the first enabled row without a URL in `store.selection()` order (the store orders by guild name then channel name), before anything else happens; while running, `start()` only refreshes. `Engine.refresh` notes `"#<name> has no webhook, not mirrored"` per such row and survives a stop during its awaits. `save_setup(body)` reads `backfill`, `include_threads`, `mirror`, `channels` only. `_webhook_for`, `_prefix`, `_own_webhook` use row URLs only. Removed from the engine: `copy_guild`, `_wire_copy`, `reset_destination`, `_provision`, `_wire_layout`, `GONE`, the `destination_layout` import. Removed from `web.py`: `reset_destination`, `copy_guild` handlers and the routes `POST /api/guilds/{guild_id}/copy`, `POST /api/destination/reset`.
- Consumes: Task 1's `set_options(backfill, include_threads, mirror)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_engine.py`, delete `test_wire_layout_reuses_category_and_survives_failure`, `test_provision_reports_gone_destination`, `test_provision_into_existing_server_reuses_categories`, `test_copy_guild_keeps_new_destination` and the `from mirror.provision import destination_layout` import if any of them is still there. Replace `test_start_while_running_provisions_and_refreshes`, `test_stop_during_running_start_is_respected` and `test_refresh_notes_missing_webhooks` with:

```python
    async def test_start_refuses_the_first_ticked_channel_without_a_webhook(self) -> None:
        http = FakeHTTP()
        self.engine.http = http
        self.store.replace_selection([row("11", name="lobby"), row("10", HOOK, name="general")])
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            with self.assertRaises(ApiError) as caught:
                await self.engine.start()
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(str(caught.exception), "#lobby has no webhook")
        self.assertFalse(self.engine.running)
        self.assertEqual(FakeGateway.made, [])
        self.assertFalse(any(method == "POST" for method, path, body in http.calls))
        self.assertFalse(self.store.options()["mirror"])

    async def test_start_while_running_refreshes_instead_of_starting_again(self) -> None:
        self.engine.http = FakeHTTP()
        self.store.replace_selection([row("10", HOOK)])
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            await self.engine.start()
            self.assertTrue(self.engine.running)
            self.store.replace_selection([row("10", HOOK), row("11", HOOK_B, name="lobby")])
            await self.engine.start()
            gateway = FakeGateway.made[0]
            self.assertEqual(len(FakeGateway.made), 1)
            self.assertEqual(sorted(gateway.subs[-1]["5"]), ["10", "11"])
            self.assertEqual(gateway.resubscribed, 1)
            await self.engine.stop()

    async def test_stop_during_a_running_refresh_is_respected(self) -> None:
        http = FakeHTTP()
        gate = asyncio.Event()
        entered = asyncio.Event()

        async def held(guild_id: str) -> list[dict]:
            entered.set()
            await gate.wait()
            return []

        self.engine.http = http
        self.store.replace_selection([row("10", HOOK)])
        self.store.set_options(0, True, True)
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            await self.engine.start()
            http.active_threads = held
            self.store.replace_selection([row("10", HOOK), row("11", HOOK_B, name="lobby")])
            task = asyncio.create_task(self.engine.start())
            await asyncio.wait_for(entered.wait(), 1)
            await self.engine.stop()
            gate.set()
            await asyncio.wait_for(task, 1)
            self.assertFalse(self.engine.running)
            self.assertEqual(len(FakeGateway.made), 1)
            self.assertEqual(FakeGateway.made[0].resubscribed, 0)

    async def test_refresh_notes_a_ticked_channel_without_a_webhook(self) -> None:
        self.engine.http = FakeHTTP()
        self.store.replace_selection([row("10", HOOK)])
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            await self.engine.start()
            self.store.replace_selection([row("10", HOOK), row("11", name="lobby")])
            await self.engine.refresh()
            self.assertIn("#lobby has no webhook, not mirrored", self.notes())
            self.assertEqual(sorted(FakeGateway.made[0].subs[-1]["5"]), ["10", "11"])
            await self.engine.stop()

    def test_save_setup_keeps_only_the_three_options(self) -> None:
        self.engine.save_setup({"backfill": 999, "include_threads": True, "mirror": True, "global_webhook": HOOK, "dest_name": "x", "channels": [row("10", HOOK)]})
        self.assertEqual(self.store.options(), {"backfill": 500, "include_threads": True, "mirror": True})
        self.assertEqual(self.store.selection()[0]["webhook_url"], HOOK)

    def test_prefix_only_when_two_channels_share_a_webhook(self) -> None:
        self.store.replace_selection([row("10", HOOK), row("11", HOOK_B, name="lobby"), row("12", "", name="quiet")])
        self.assertFalse(self.engine._prefix(HOOK))
        self.assertFalse(self.engine._prefix(""))
        self.store.replace_selection([row("10", HOOK), row("11", HOOK, name="lobby")])
        self.assertTrue(self.engine._prefix(HOOK))
        self.assertFalse(hasattr(self.engine, "copy_guild"))
        self.assertFalse(hasattr(self.engine, "reset_destination"))
        self.assertFalse(hasattr(self.engine, "_provision"))
```

In `tests/test_web.py`, add to `WebTests`:

```python
    async def test_copy_and_reset_routes_are_gone(self) -> None:
        client = await self.client()
        resp = await client.post("/api/guilds/5/copy")
        self.assertEqual(resp.status, 404)
        resp = await client.post("/api/destination/reset")
        self.assertEqual(resp.status, 404)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -X utf8 -m unittest tests.test_engine tests.test_web -q`
Expected: FAIL — the refusal test sees provisioning `POST` calls and `running` true; the `hasattr` assertions fail; the web test gets 401/400, not 404.

- [ ] **Step 3: Implement**

In `mirror/engine.py`:

- Imports: `from .provision import webhook_name` only (Task 4 adds `copy_layout, same_name`); drop `GONE`.
- Delete `copy_guild`, `_wire_copy`, `reset_destination`, `_provision`, `_wire_layout`.
- `save_setup`: remove the `global_webhook` and `dest_name` lines; end with `self.store.set_options(backfill, include_threads, mirror)` then `self.store.replace_selection(cleaned)`.
- `_start`:

```python
    async def _start(self) -> None:
        http = self._require_http()
        rows = [row for row in self.store.selection() if row["enabled"]]
        if not rows:
            raise ApiError(400, "select servers first")
        # the Start rule (decisions 9f, 9k): Mando never creates a webhook at Start; the CLI jumps to this row
        missing = next((row for row in rows if not str(row.get("webhook_url") or "").strip()), None)
        if missing is not None:
            raise ApiError(400, f"#{missing.get('channel_name') or missing['channel_id']} has no webhook")
        if self.running:
            await self.refresh()
            return
        options = self.store.options()
        if not options["mirror"]:
            self.store.set_options(options["backfill"], options["include_threads"], True)
            options = self.store.options()
        self._index(rows, options["include_threads"])
        ... (unchanged from here: _load_threads, Gateway, backfill task, status event)
```

- `refresh`: after `await self._load_threads(self.http, rows)` re-read the gateway, because a stop during that await sets it to `None`:

```python
    async def refresh(self) -> None:
        if not self.running or self.gateway is None or self.http is None:
            return
        options = self.store.options()
        rows = [row for row in self.store.selection() if row["enabled"]]
        if not rows:
            raise ApiError(400, "pick at least one channel")
        self._index(rows, options["include_threads"])
        if options["include_threads"]:
            await self._load_threads(self.http, rows)
        gateway = self.gateway
        if gateway is None or not self.running:
            return  # stopped while the threads were listed
        gateway.set_subscriptions(self._guild_map(rows), options["include_threads"])
        await gateway.resubscribe()
        self.note(f"selection updated, {len(rows)} channel(s)")
        for row in rows:
            if not str(row.get("webhook_url") or "").strip():
                self.note(f"#{row.get('channel_name') or row['channel_id']} has no webhook, not mirrored")
```

- `_webhook_for`: `return row["webhook_url"]`; `_prefix`: `target = row["webhook_url"]` and `if target and target == url`; `_own_webhook`: drop the `global_webhook` block. Remove every remaining `options[...]` read of the three dead keys (`grep -n "global_webhook\|dest_name\|dest_guild" mirror/engine.py` must print nothing afterwards; across the package, `grep -rn --include=*.py` on `mirror/` still prints `mirror/cli/controller.py`'s `_save_options` body, which Task 6 changes, and the 9h comment in `mirror/store.py` `_migrate`, measured in the Task 3 run).

In `mirror/provision.py` delete `destination_layout`, `_slug`, `_SLUG` and `import re` (nothing else uses them; `grep -n "_slug\|_SLUG\|^import re$" mirror/provision.py` must print nothing afterwards); keep `copy_layout`, `same_name`, `_shown`, `webhook_name`, `_fold`.

In `mirror/web.py` delete the `reset_destination` and `copy_guild` handlers and their two `app.router.add_post` lines.

- [ ] **Step 4: Run the full suite**

Run the full test command. Expected: OK. Also run `grep -rn --include=*.py "global_webhook\|dest_name\|dest_guild\|destination_layout\|copy_guild\|reset_destination\|_provision" mirror/ tests/` — only these may still mention the dead names (measured in the Task 3 run): `mirror/cli/controller.py` `_save_options` and `tests/test_cli_controller.py` (Task 6 cleans both), the 9h comment in `mirror/store.py` `_migrate`, the old-database schemas in `tests/test_core.py` and `tests/test_engine.py` (`OLD_SCHEMA`), the `hasattr` checks in `tests/test_core.py` (`set_dest_guild`) and in `test_prefix_only_when_two_channels_share_a_webhook`, and the dead keys fed to `test_save_setup_keeps_only_the_three_options`.

- [ ] **Step 5: Commit**

```bash
git add mirror/engine.py mirror/provision.py mirror/web.py tests/test_engine.py tests/test_web.py
git commit -m "Refuse Start on a ticked channel without a webhook and remove the shared mirror server with its provisioning."
```

---

### Task 4: `owned_guilds` and `fill_copy` — filling a server the owner owns

**Files:**
- Modify: `mirror/engine.py`, `mirror/store.py` (the `shared` flag of a link, the `fills` record, the `fill_categories` record, the `nsfw` column of `selection`), `mirror/cli/controller.py` (`_remember` and the stand-in row of an unlisted channel keep `nsfw`)
- Test: `tests/test_engine.py`, `tests/test_cli_controller.py` (new `test_a_ticked_channel_keeps_its_age_restriction`, `test_start_jumps_to_a_ticked_channel_of_a_server_the_account_left`), `tests/test_core.py` (`test_store_targets_per_source`, new `test_targets_table_without_shared_column_opens`, new `test_a_new_targets_table_has_no_shared_column`, new `test_targets_table_with_shared_column_and_no_fills_opens`, new `test_targets_table_with_shared_column_reads_the_fills_record`, new `test_a_target_remembers_every_source_filled_into_it`, new `test_a_target_tells_whether_it_holds_another_source`, new `test_a_target_remembers_the_categories_made_for_each_source`)

**Interfaces:**
- Produces: `Engine.owned_guilds() -> list[{"id", "name"}]` sorted by folded name, the servers whose `/users/@me/guilds` entry has `owner` true; `Engine.fill_copy(source_id: str, target_id: str) -> {"target": str, "filled": int, "reused": int, "replaced": int}`, which holds `self._setup` around `Engine._fill_copy(source_id, target_id)` (same result); `Engine._fill(http, target_id, layout, existing, before: dict[source_channel_id, url], alone: bool, source_id: str) -> tuple[pairs, reused]`; `Engine._hooks_on(http, channel_id) -> list[dict] | None` (the webhooks Discord lists on a channel, `None` on an `ApiError`, which says nothing about them); `Engine._webhook_on(http, channel_id, name, look: bool, listed: list[dict] | None = None) -> tuple[str, bool]`. In the store: `Store.set_target(source_guild_id, target_guild_id, target_name)` keeps its Task 1 signature and records the pair in `fills` as `record_fill` does; `Store.targets()` links are `{"target_id", "target_name", "shared": bool}`, the flag read from the `fills` row of that pair (`False` when there is none). The `fills` record alone holds the flag, so a snapshot and a fill never read two answers: the `targets` table gets no `shared` column, and a file whose `targets` table has one (a development file from an earlier state of this task) keeps it unread, as the old `options` columns stay (decision 9h). A new table `fills (target_guild_id, source_guild_id, shared, PRIMARY KEY (target_guild_id, source_guild_id))` records every source a target holds and is never emptied (Mando never deletes in the target, decision 9i, so a source moved to another target or whose first fill failed halfway still has its channels there); `_migrate` copies every `targets` link into it (`INSERT OR IGNORE`), with the flag of a `targets` column `shared` when the file has that column and `False` otherwise, so a file from before this table keeps what its links say. `Store.record_fill(source_guild_id, target_guild_id) -> bool` returns the recorded flag when the pair is in `fills`, otherwise records the pair with `shared` = whether another source is recorded for that target, and returns that. `Store.holds_another(target_guild_id, source_guild_id) -> bool` says whether `fills` holds a source other than this one in that target, whichever came first (the first source keeps `shared` false after a second one arrived, so the flag cannot say it). A new table `fill_categories (target_guild_id, category_id, source_guild_id, PRIMARY KEY (target_guild_id, category_id))` records, for each category a fill created, the source it was created for, and is never emptied; `Store.record_category(target_guild_id, source_guild_id, category_id)` adds a category (`INSERT OR IGNORE`: the first record stays) and `Store.category_sources(target_guild_id) -> dict[category_id, source_guild_id]` lists a target's. The `selection` table gets `nsfw INTEGER NOT NULL DEFAULT 0`, the age restriction of the source channel, which `_migrate` adds to a file without it as it adds `parent` and `topic`; `replace_selection` writes 1 for a row with a true `nsfw` and 0 otherwise, and `selection()` reads it. `Engine.channels` gives each channel `"nsfw": bool` from Discord's channel `nsfw`, and the rows that reach the store keep it: the CLI's `_remember` (and the stand-in row it shows for a ticked channel no list shows) and `Engine.save_setup`, which `PUT /api/setup` calls. Start and `refresh` never rewrite a row (measured: only `_remember` writes a listed channel's facts onto a row, on Enter on a server, `a` in a channel list or the tick of one channel), so a row stored before the column has no age gate until the owner ticks its channel again.
- Errors (`ApiError(400, ...)`): `"unknown server"` (non-numeric id), `"a server cannot be its own copy"`, `"tick the server or some of its channels first"` (no enabled row of the source), `"pick a server you own"` (target not in `owned_guilds()`), `"no webhook could be created"` (no pair at the end). `"add a token first"` (401) comes from `_require_http`.
- Behaviour: one fill at a time and never beside a Start: `fill_copy` holds `self._setup`, the lock `start` holds, and `_fill_copy` does the work, so a second fill reads the target after the first one wrote to it (two fills at once read the same `existing` and created every category, channel and webhook twice, which Mando never deletes, measured in the Task 4 review, round 2; `refresh` takes no lock, so the refresh at the end does not wait for itself); `rows` are every enabled row of the source, with or without a webhook URL (a second fill finds its own webhooks again and keeps the same URLs; a fill covers every ticked channel of the source, decision 9n, so an own webhook the owner typed, decision 9g, or the URL an earlier fill made in another target is replaced by the copy's webhook, and the hooks memory follows the row; the store keeps no mark of who made a URL, so the two cannot be told apart, and leaving a row with a URL alone would make a fill into a second target create channels and webhooks there while every row keeps the first target's URL, measured in the Task 4 review; the spec records this as a gap for the owner after decision 9o); `existing = await http.channels(target_id)`; each row carries its source channel's age restriction as `nsfw`, stored when the owner ticked the channel, so the fill reads no listing of the source (the copy code this branch removed, `_wire_copy` at 3fb3269, took the flag from the source's channel listing through `readable_plan`; a second listing inside the fill had no error handling, so a 403, 404 or 5xx of it ended a fill that needed no source listing before, it ran unpaced under the `_setup` lock, and a ticked channel missing from it got no gate); then `shared = store.record_fill(source_id, target_id)`, before the first write into the target: `shared` means this source is not the first the target holds (decision 9o, "a target that already holds another source"), which is what the target holds and not where the links point now, because a link moves with a later pick while the channels stay; a refill gets the recorded flag again (the categories of its first fill, so the first source keeps its own category names after a second source arrived, also after it was filled into another target and back, and no channel is created twice); a fill that fails after `record_fill` leaves the pair recorded, so a later source is prefixed (at worst a prefix where none was needed, never two sources in one category); `alone = not store.holds_another(target_id, source_id)` (no other source in the target, first or later); the layout is `copy_layout(rows[0]["guild_name"], rows, shared)`; `before` maps each row's channel id to its URL before the fill; a category is the source's a fill made it for, not whoever carries its name, so before `_fill` the categories of `existing` are cut to those this source may use: a later source (`shared`) only the categories `category_sources` gives to it, the first source also those no fill made (the server's own, decision 9i), and no source a category made for another source (names do not tell sources apart: Discord server names are not unique, and a later source's "<source>" or "<source> / <category>" can be a category of the first source or of the server itself; measured in the Task 4 review, round 3, with the real engine and `KeepingHTTP`: a source named "Trading" with a loose `general`, filled after a source with `general` under "Trading", found that category and channel and got the first source's webhook, reported as reused; three sources named "Gaming" gave the third the second's "Gaming / Talk" and webhook; the first source refilled with a newly ticked category named like a later source's took the later source's channel; and a source named "Text Channels" took the server's own `general` that the first source had reused, with its webhook); `_fill` records each category it creates for `source_id` with `record_category` right after Discord made it, so a fill that fails later still has it recorded; two categories of one name can then stand in the target; within what is left, categories are found by folded name; then each channel of the layout gets one existing text channel or none, and an existing channel serves one row only (a `claimed` set: two source channels of the same name in one category get two channels and two webhooks, never one channel with one webhook for both; the review of Task 4, round 2, measured the earlier code matching the second one to the channel just created for the first and reporting it as reused). A channel that carries a row's webhook is that row's copy, wherever it stands, so the rows get channels in four steps, each over the whole layout before the next: (1) every row with a URL whose category is in the target, or that is loose, claims the unclaimed text channel with `same_name` and the same `parent_id` (empty for a loose channel) that carries the row's webhook (`_hooks_on` lists a hook with the id of the row's URL and a `token`; that hook's URL is kept and counts as reused); (2) every row with a URL still without a channel claims the unclaimed text channel with `same_name` under any parent that carries its webhook; (3) every row still without one takes the first unclaimed text channel with `same_name` and the same `parent_id`, so a name found elsewhere never takes a channel another row has in its own place; (4) when `alone`, every row still without one takes the first unclaimed text channel with `same_name` under any parent (decision 9i, "a channel with the same name as the source channel is reused": a fresh server's own `general` under "Text Channels" serves a loose source `general`, which the same-parent rule alone missed, measured in round 2), never with another source in the target, since it may be that source's channel (decision 9o; measured in round 2: "the first of them for any unshared source" gave the first source, refilled after a second source arrived, the second source's channel and webhook, and "only when alone" made that refill create a new channel). A row without a URL lists nothing in steps 1 and 2, and each channel is listed at most once in them; a listing that fails is not kept, and the channel is never listed again in steps 1 and 2. The carriers claim first because neither Discord's listing order nor the store's is the copies' order (measured with `KeepingHTTP`: with two source `general` under one category, a refill that took the first of them gave each row the other's channel and webhook and noted both URLs as replaced; the next code looked for the carrier only among two or more channels and only while placing that row, and then a third `general` ticked since, without a URL and first in the store, took the first row's copy, the first row took the second's and the second got a new channel, `replaced` 2; a row whose URL was cleared took the other row's copy when Discord listed it first, `replaced` 1; and a `general` newly ticked under the server's "Text Channels" took the channel a first fill had reused there for a `general` under "Talk", `replaced` 1). A category is created (type 4) only for a channel created under it, so a channel found elsewhere leaves no empty category; a channel without an existing one is created (type 0, `parent_id`, `topic` when present, `nsfw: true` when its layout channel has it, so the copy of an age-restricted channel keeps the age gate; only on creation, since a found channel is never altered) unless its category is not in the target because Discord refused it: then it is skipped with the note `"#<name> was not created (its category failed)"` and never put loose, where a second source's channel would take the first source's loose channel of the same name and its webhook (measured in round 2). A found channel's webhooks are listed with `GET /channels/{id}/webhooks` (once per fill: `_webhook_on` takes the listing steps 1 and 2 made) and one whose `name == webhook_name(channel name)` and that has a `token` is reused, otherwise a webhook is created; a created channel gets a new webhook without the listing; a found channel whose listing failed in steps 1 and 2, or was not made there, is listed by `_webhook_on`, once, and when that fails too the fill notes `"webhooks of #<name> could not be listed"` and gives the row no webhook (no URL, no `pairs` entry, its stored URL unchanged): the channel may carry a webhook already, and Mando never deletes a second one (measured before this fix: a 500 on the listing of one of two `general` under one category was kept as an empty listing, the row took that channel, `_webhook_on` trusted the empty listing and created a second webhook beside the row's own, and the URL counted as replaced; each such refill added one more); paces: `_wait(0.3)` after each category request, `_wait(0.25)` after each channel request, each listing of steps 1 and 2 and each channel's webhook step (none for a row whose webhook steps 1 and 2 found); no `DELETE` ever; a channel or webhook that fails is noted and skipped (`"#<name> was not created (<exc>)"`, `"webhook for #<name> failed (<exc>)"`); then `store.fill_webhooks(pairs)`, `store.set_target(source_id, target_id, target name)`, a note `"webhooks on <n> channel(s) in <target>"` (`", <k> reused"` appended when `k > 0`), a note `"<r> earlier webhook url(s) replaced"` when `r > 0` (`r` counts the pairs whose row had a non-empty URL before the fill that names another webhook than the new one, compared by `webhook_parts`, the id and token, since a found webhook is written with the discord.com host and the same webhook pasted with a `ptb.`, `canary.` or `discordapp.com` host is not replaced; so a refill that finds its webhooks again counts nothing), and `await self.refresh()` when running. Returns `{"target": name, "filled": len(pairs), "reused": k, "replaced": r}` where `k` counts reused webhooks and `r` is the count of the note (0 when there is no note), so a caller that never shows the log (the CLI, Task 6) shows the engine's count.
- Consumes: Task 1 `set_target`/`targets` (`targets` extended here with `shared`), Task 2 `copy_layout`/`same_name`.

- [ ] **Step 1: Write the failing tests**

Extend `FakeHTTP` in `tests/test_engine.py`: record `GET` calls on `/webhooks` and answer from a `hooks: dict[str, list[dict]]` map keyed by channel id; a created channel answers with its new id and the posted body (no test reads a created channel back through `channels(target)`, so the fake keeps no list of them); the `POST /guilds` branch goes, since nothing creates a server after Task 3:

```python
class FakeHTTP:
    def __init__(self, existing: list[dict] | None = None, gone: ApiError | None = None, fail: tuple = ()) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.existing = existing or []
        self.gone = gone
        self.fail = set(fail)
        self.sources: dict[str, list[dict]] = {}
        self.listed: list[dict] = []
        self.hooks: dict[str, list[dict]] = {}
        self.refused: set[tuple[str, str]] = set()  # (method, path) answered with a 500
        self.seq = 1000

    async def call(self, method: str, path: str, **kw: Any) -> Any:
        body = kw.get("json") or {}
        self.calls.append((method, path, body))
        if (method, path) in self.refused:
            raise ApiError(500, f"{method} {path} failed")
        if method == "GET" and path.endswith("/webhooks"):
            return list(self.hooks.get(path.split("/")[2], []))
        if method == "POST" and path.endswith("/webhooks"):
            self.seq += 1
            return {"id": str(self.seq), "token": "tok"}
        if method == "POST" and path.endswith("/channels"):
            if body.get("name") in self.fail:
                raise ApiError(400, "no")
            self.seq += 1
            return {"id": str(self.seq), **body}
        if method == "GET" and path.endswith("/member"):
            return {"roles": []}
        return None
```

(`channels`, `guilds`, `active_threads` stay as they are.) Below it, a fake that keeps what a fill creates and yields at every request, for the test of two fills at once:

```python
class KeepingHTTP(FakeHTTP):
    """A FakeHTTP that keeps the channels and webhooks a fill creates, and lets other tasks run at every request
    as a real request does."""

    async def call(self, method: str, path: str, **kw: Any) -> Any:
        await asyncio.sleep(0)
        made = await super().call(method, path, **kw)
        where = path.split("/")[2]
        if method == "POST" and path.endswith("/channels") and isinstance(made, dict):
            self.sources.setdefault(where, []).append({"parent_id": None, **made})
        elif method == "POST" and path.endswith("/webhooks") and isinstance(made, dict):
            self.hooks.setdefault(where, []).append({"name": (kw.get("json") or {}).get("name"), **made})
        return made

    async def channels(self, guild_id: str) -> list[dict]:
        await asyncio.sleep(0)
        return [dict(item) for item in await super().channels(guild_id)]
```

Add the tests:

```python
    def posts(self, http: FakeHTTP, suffix: str) -> list[dict]:
        return [body for method, path, body in http.calls if method == "POST" and path.endswith(suffix)]

    async def test_owned_guilds_lists_only_servers_the_account_owns(self) -> None:
        http = FakeHTTP()
        http.listed = [
            {"id": "5", "name": "Desk", "owner": False},
            {"id": "900", "name": "zeta copy", "owner": True},
            {"id": "901", "name": "Alpha", "owner": True},
        ]
        self.engine.http = http
        self.assertEqual(await self.engine.owned_guilds(), [{"id": "901", "name": "Alpha"}, {"id": "900", "name": "zeta copy"}])
        self.engine.http = None
        with self.assertRaises(ApiError):
            await self.engine.owned_guilds()

    async def test_fill_creates_categories_channels_and_webhooks_in_an_owned_server(self) -> None:
        http = FakeHTTP()
        http.listed = [{"id": "5", "name": "Desk", "owner": False}, {"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("11", name="news"), row("20", name="other", guild_id="6")])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 0, "replaced": 0})
        categories = [b for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 4]
        self.assertEqual([c["name"] for c in categories], ["Talk"])
        texts = [b for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 0]
        self.assertEqual([(t["name"], "parent_id" in t) for t in texts], [("general", True), ("news", False)])
        self.assertEqual(len(self.posts(http, "/webhooks")), 2)
        self.assertFalse(any(method == "DELETE" for method, path, body in http.calls))
        self.assertFalse(any(method == "GET" and path.endswith("/webhooks") for method, path, body in http.calls))
        hooks = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertTrue(hooks["10"].startswith("https://discord.com/api/webhooks/"))
        self.assertTrue(hooks["11"].startswith("https://discord.com/api/webhooks/"))
        self.assertEqual(hooks["20"], "")
        self.assertEqual(self.store.targets(), {"5": {"target_id": "900", "target_name": "Desk copy", "shared": False}})
        self.assertIn("webhooks on 2 channel(s) in Desk copy", self.notes())
        self.assertEqual(self.delays, [0.3, 0.25, 0.25, 0.25, 0.25])

    async def test_fill_creates_the_copy_of_an_age_restricted_channel_with_the_age_gate(self) -> None:
        # the age restriction travels on the stored row, as the parent and the topic do; the fill reads no listing of
        # the source and sets the gate only on a channel it creates: "rules", found again in the target, is never altered
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [{"id": "t1", "type": 0, "name": "rules", "parent_id": None}]
        self.engine.http = http
        self.store.replace_selection(
            [row("10", name="general") | {"nsfw": True}, row("11", name="news"), row("12", name="rules") | {"nsfw": True}]
        )
        listed: list[str] = []
        listing = http.channels

        async def counted(guild_id: str) -> list[dict]:
            listed.append(guild_id)
            return await listing(guild_id)

        http.channels = counted
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report["filled"], 3)
        self.assertEqual(listed, ["900"])
        texts = {b["name"]: b for b in self.posts(http, "/guilds/900/channels")}
        self.assertEqual(sorted(texts), ["general", "news"])
        self.assertIs(texts["general"]["nsfw"], True)
        self.assertNotIn("nsfw", texts["news"])
        self.assertEqual({method for method, path, body in http.calls}, {"GET", "POST"})

    async def test_fill_reuses_channels_and_their_webhooks(self) -> None:
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "talk"},
            {"id": "t1", "type": 0, "name": "General", "parent_id": "c1"},
            {"id": "t2", "type": 0, "name": "news", "parent_id": None},
            {"id": "t3", "type": 0, "name": "general", "parent_id": None},
        ]
        http.hooks["t1"] = [{"id": "77", "name": "general", "token": "old"}, {"id": "78", "name": "someone else", "token": "x"}]
        http.hooks["t2"] = [{"id": "79", "name": "news"}]
        self.engine.http = http
        # the source channel "general" sits under "Talk": the existing "General" under "talk" is reused (same name as
        # Discord shows it, same parent), not the loose "general"; its webhook named "general" has a token and is kept
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("11", name="news")])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 1, "replaced": 0})
        self.assertEqual(self.posts(http, "/guilds/900/channels"), [])
        self.assertEqual([path for method, path, body in http.calls if method == "POST"], ["/channels/t2/webhooks"])
        hooks = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual(hooks["10"], "https://discord.com/api/webhooks/77/old")
        self.assertTrue(hooks["11"].startswith("https://discord.com/api/webhooks/1001/"))
        self.assertIn("webhooks on 2 channel(s) in Desk copy, 1 reused", self.notes())

    async def test_two_fills_at_once_create_each_channel_and_webhook_once(self) -> None:
        # a second fill started before the first one ends (the web page, a second client) waits for it and then
        # finds its category, channels and webhooks again (Review Focus 1); Mando never deletes the duplicates
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("11", name="news")])
        reports = await asyncio.gather(self.engine.fill_copy("5", "900"), self.engine.fill_copy("5", "900"))
        self.assertEqual([report["reused"] for report in reports], [0, 2])
        self.assertEqual([(b["type"], b["name"]) for b in self.posts(http, "/guilds/900/channels")], [(4, "Talk"), (0, "general"), (0, "news")])
        self.assertEqual(len(self.posts(http, "/webhooks")), 2)

    async def test_first_source_reuses_channels_of_the_same_name_in_other_categories(self) -> None:
        # decision 9i: a channel with the source channel's name is reused. A fresh server has "general" under
        # "Text Channels"; the first source in a target, with no other source there to mix with (9o), reuses it for
        # a loose "general", and the loose "rules" for "rules" under "Info", which is then not created empty
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "Text Channels"},
            {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"},
            {"id": "c2", "type": 4, "name": "Voice Channels"},
            {"id": "v1", "type": 2, "name": "General", "parent_id": "c2"},
            {"id": "t2", "type": 0, "name": "rules", "parent_id": None},
        ]
        self.engine.http = http
        self.store.replace_selection([row("10", name="general"), row("11", name="rules") | {"parent": "Info"}, row("12", name="news") | {"parent": "Chat"}])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 3, "reused": 0, "replaced": 0})
        self.assertEqual([(b["type"], b["name"]) for b in self.posts(http, "/guilds/900/channels")], [(4, "Chat"), (0, "news")])
        hooked = [path for method, path, body in http.calls if method == "POST" and path.endswith("/webhooks")]
        self.assertIn("/channels/t1/webhooks", hooked)
        self.assertIn("/channels/t2/webhooks", hooked)
        self.assertNotIn("/channels/v1/webhooks", hooked)

    async def test_fill_gives_two_channels_of_the_same_name_their_own_copies(self) -> None:
        # Discord allows two "general" in one category: each gets its own channel and webhook in the copy, never one
        # channel with one webhook for both (Review Focus 6), and a refill finds each of them again
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("11", name="general") | {"parent": "Talk"}])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 0, "replaced": 0})
        self.assertEqual([(b["type"], b["name"]) for b in self.posts(http, "/guilds/900/channels")], [(4, "Talk"), (0, "general"), (0, "general")])
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertNotEqual(urls["10"], urls["11"])
        http.calls.clear()
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 2, "replaced": 0})
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, urls)

    async def test_refill_of_two_channels_of_the_same_name_keeps_each_on_its_own_copy(self) -> None:
        # two "general" under "Talk": the store lists 10 before 11 (a tie on the name), Discord lists 11's copy first.
        # Each row takes the copy that carries its own webhook, never the other's copy and history
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "Talk"},
            {"id": "t11", "type": 0, "name": "general", "parent_id": "c1"},
            {"id": "t10", "type": 0, "name": "general", "parent_id": "c1"},
        ]
        http.hooks["t11"] = [{"id": "2", "name": "general", "token": "b"}]
        http.hooks["t10"] = [{"id": "1", "name": "general", "token": "a"}]
        self.engine.http = http
        urls = {"10": "https://discord.com/api/webhooks/1/a", "11": "https://discord.com/api/webhooks/2/b"}
        self.store.replace_selection(
            [row("10", urls["10"], name="general") | {"parent": "Talk"}, row("11", urls["11"], name="general") | {"parent": "Talk"}]
        )
        self.assertEqual([r["channel_id"] for r in self.store.selection()], ["10", "11"])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 2, "replaced": 0})
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, urls)
        self.assertFalse(any("replaced" in line for line in self.notes()))

    async def test_a_newly_ticked_row_of_the_same_name_never_takes_a_copy_that_carries_another_rows_webhook(self) -> None:
        # 10 and 11 have their copies t10 and t11 under Talk; 12, a third "general" ticked since and without a URL,
        # comes first in the store. The rows whose webhook a channel carries take it first, so 12 gets a new channel
        # and neither history flows into the other row's copy
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "Talk"},
            {"id": "t10", "type": 0, "name": "general", "parent_id": "c1"},
            {"id": "t11", "type": 0, "name": "general", "parent_id": "c1"},
        ]
        http.hooks["t10"] = [{"id": "1", "name": "general", "token": "a"}]
        http.hooks["t11"] = [{"id": "2", "name": "general", "token": "b"}]
        self.engine.http = http
        urls = {"10": "https://discord.com/api/webhooks/1/a", "11": "https://discord.com/api/webhooks/2/b"}
        self.store.replace_selection(
            [
                row("12", name="general") | {"parent": "Talk"},
                row("10", urls["10"], name="general") | {"parent": "Talk"},
                row("11", urls["11"], name="general") | {"parent": "Talk"},
            ]
        )
        self.assertEqual([r["channel_id"] for r in self.store.selection()], ["12", "10", "11"])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 3, "reused": 2, "replaced": 0})
        self.assertEqual(self.posts(http, "/guilds/900/channels"), [{"name": "general", "type": 0, "parent_id": "c1"}])
        after = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual((after["10"], after["11"]), (urls["10"], urls["11"]))
        self.assertNotIn(after["12"], urls.values())
        self.assertFalse(any("replaced" in line for line in self.notes()))

    async def test_a_row_without_a_url_never_takes_the_copy_of_a_row_of_the_same_name(self) -> None:
        # two "general" under Talk: 10 has no URL any more, 11 has its own, and Discord lists 11's copy first. 11 takes
        # the copy that carries its webhook, 10 the other one, and no URL counts as replaced
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "Talk"},
            {"id": "t11", "type": 0, "name": "general", "parent_id": "c1"},
            {"id": "t10", "type": 0, "name": "general", "parent_id": "c1"},
        ]
        http.hooks["t10"] = [{"id": "1", "name": "general", "token": "a"}]
        http.hooks["t11"] = [{"id": "2", "name": "general", "token": "b"}]
        self.engine.http = http
        self.store.replace_selection(
            [
                row("10", name="general") | {"parent": "Talk"},
                row("11", "https://discord.com/api/webhooks/2/b", name="general") | {"parent": "Talk"},
            ]
        )
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 2, "replaced": 0})
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual(
            {r["channel_id"]: r["webhook_url"] for r in self.store.selection()},
            {"10": "https://discord.com/api/webhooks/1/a", "11": "https://discord.com/api/webhooks/2/b"},
        )
        self.assertFalse(any("replaced" in line for line in self.notes()))

    async def test_a_row_ticked_in_its_own_place_never_takes_a_copy_found_elsewhere_for_another_row(self) -> None:
        # Desk (5), alone in 900, reused the server's own "general" under "Text Channels" for its "general" under
        # "Talk" (decision 9i). The owner then ticks Desk's "general" under "Text Channels": the channel carries 10's
        # webhook, so 10 keeps it and 12 gets a new channel in its category, not 10's copy and history
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "Text Channels"},
            {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"},
        ]
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}])
        await self.engine.fill_copy("5", "900")
        first = self.store.selection()[0]["webhook_url"]
        self.assertEqual(self.posts(http, "/guilds/900/channels"), [])
        self.store.replace_selection(
            [row("10", name="general") | {"parent": "Talk"}, row("12", name="general") | {"parent": "Text Channels"}]
        )
        http.calls.clear()
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 1, "replaced": 0})
        self.assertEqual(self.posts(http, "/guilds/900/channels"), [{"name": "general", "type": 0, "parent_id": "c1"}])
        after = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual(after["10"], first)
        self.assertNotEqual(after["12"], first)

    async def test_a_channel_whose_webhooks_cannot_be_listed_gets_no_second_webhook(self) -> None:
        # Discord answers 500 to the listing of t10's webhooks: the fill cannot tell whether t10 carries 10's webhook,
        # so it creates none there (Mando never deletes one) and reports #general as not wired; a later fill whose
        # listing works finds 10's webhook again
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "Talk"},
            {"id": "t10", "type": 0, "name": "general", "parent_id": "c1"},
            {"id": "t11", "type": 0, "name": "general", "parent_id": "c1"},
        ]
        http.hooks["t10"] = [{"id": "1", "name": "general", "token": "a"}]
        http.hooks["t11"] = [{"id": "2", "name": "general", "token": "b"}]
        http.refused.add(("GET", "/channels/t10/webhooks"))
        self.engine.http = http
        urls = {"10": "https://discord.com/api/webhooks/1/a", "11": "https://discord.com/api/webhooks/2/b"}
        self.store.replace_selection(
            [row("10", urls["10"], name="general") | {"parent": "Talk"}, row("11", urls["11"], name="general") | {"parent": "Talk"}]
        )
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 1, "replaced": 0})
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertIn("webhooks of #general could not be listed", self.notes())
        # listed once while looking for 10's webhook and once more in the webhook step, never cached as empty
        self.assertEqual([path for method, path, body in http.calls if method == "GET"].count("/channels/t10/webhooks"), 2)
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, urls)
        http.refused.clear()
        http.calls.clear()
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 2, "replaced": 0})
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, urls)

    async def test_a_second_source_whose_category_fails_takes_no_loose_channel(self) -> None:
        # Desk (5) holds a loose "general" with its webhook in 900. Other (6) comes second and Discord refuses its
        # "Other / Talk": its "general" is skipped, never put loose beside Desk's and never given Desk's webhook (9o)
        http = FakeHTTP(fail=("Other / Talk",))
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [{"id": "t1", "type": 0, "name": "general", "parent_id": None}]
        http.hooks["t1"] = [{"id": "77", "name": "general", "token": "old"}]
        self.engine.http = http
        self.store.set_target("5", "900", "Desk copy")
        self.store.replace_selection(
            [
                row("10", "https://discord.com/api/webhooks/77/old", name="general"),
                row("20", name="general", guild_id="6") | {"guild_name": "Other", "parent": "Talk"},
                row("21", name="news", guild_id="6") | {"guild_name": "Other"},
            ]
        )
        report = await self.engine.fill_copy("6", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 0, "replaced": 0})
        self.assertIn("#general was not created (its category failed)", self.notes())
        self.assertEqual([(b["type"], b["name"]) for b in self.posts(http, "/guilds/900/channels")], [(4, "Other / Talk"), (4, "Other"), (0, "news")])
        self.assertFalse(any(path == "/channels/t1/webhooks" for method, path, body in http.calls))
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual(urls["20"], "")
        self.assertEqual(urls["10"], "https://discord.com/api/webhooks/77/old")

    async def test_a_first_source_finds_its_channel_by_its_webhook_after_a_second_source(self) -> None:
        # Desk (5) was alone in 900 and its loose "general" reused the server's "general" under "Text Channels" (9i).
        # Other (6) came second with its own "general" under "Other". Filling Desk again takes the channel that
        # carries Desk's webhook, wherever Discord lists it, and never Other's channel of the same name (9o)
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c3", "type": 4, "name": "Other"},
            {"id": "t3", "type": 0, "name": "general", "parent_id": "c3"},
            {"id": "c1", "type": 4, "name": "Text Channels"},
            {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"},
        ]
        http.hooks["t3"] = [{"id": "88", "name": "general", "token": "other"}]
        http.hooks["t1"] = [{"id": "77", "name": "general", "token": "old"}]
        self.engine.http = http
        self.store.set_target("5", "900", "Desk copy")
        self.store.set_target("6", "900", "Desk copy")
        self.store.replace_selection(
            [
                row("10", "https://discord.com/api/webhooks/77/old", name="general"),
                row("20", "https://discord.com/api/webhooks/88/other", name="general", guild_id="6") | {"guild_name": "Other"},
            ]
        )
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 1, "replaced": 0})
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual(urls, {"10": "https://discord.com/api/webhooks/77/old", "20": "https://discord.com/api/webhooks/88/other"})
        self.assertFalse(any("replaced" in line for line in self.notes()))

    async def test_fill_replaces_an_own_webhook_of_a_ticked_channel_and_notes_it(self) -> None:
        # a fill covers every ticked channel of the source (decision 9n), so a channel with an own webhook goes to the
        # copy from now on; the note keeps the change visible, and the hooks memory follows the row
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", HOOK, name="general"), row("11", name="news")])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 0, "replaced": 1})
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertNotEqual(urls["10"], HOOK)
        self.assertTrue(urls["10"].startswith("https://discord.com/api/webhooks/"))
        self.assertEqual(self.store.hooks(), urls)
        self.assertIn("1 earlier webhook url(s) replaced", self.notes())

    async def test_fill_that_finds_a_webhook_pasted_with_another_host_replaces_nothing(self) -> None:
        # the row's URL names webhook 77 with a ptb. host; the fill finds 77 on the channel and writes it with the
        # discord.com host, which is the same webhook, so nothing counts as replaced
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [{"id": "t1", "type": 0, "name": "general", "parent_id": None}]
        http.hooks["t1"] = [{"id": "77", "name": "general", "token": "tok"}]
        self.engine.http = http
        self.store.replace_selection([row("10", "https://ptb.discord.com/api/webhooks/77/tok", name="general")])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 1, "replaced": 0})
        self.assertEqual(self.store.selection()[0]["webhook_url"], "https://discord.com/api/webhooks/77/tok")
        self.assertFalse(any("replaced" in line for line in self.notes()))

    async def test_fill_covers_an_unlisted_ticked_channel(self) -> None:
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([{"channel_id": "12", "guild_id": "5", "guild_name": "Desk", "channel_name": "gone", "webhook_url": "", "enabled": True}])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report["filled"], 1)
        self.assertEqual([t["name"] for t in self.posts(http, "/guilds/900/channels")], ["gone"])

    async def test_second_source_into_a_target_gets_prefixed_categories(self) -> None:
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [{"id": "c1", "type": 4, "name": "Talk"}, {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"}]
        self.engine.http = http
        self.store.set_target("5", "900", "Desk copy")
        self.store.replace_selection([row("20", name="general", guild_id="6") | {"guild_name": "Other", "parent": "Talk"}, row("21", name="loose", guild_id="6") | {"guild_name": "Other"}])
        report = await self.engine.fill_copy("6", "900")
        self.assertEqual(report["filled"], 2)
        categories = [b["name"] for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 4]
        self.assertEqual(categories, ["Other / Talk", "Other"])
        texts = [b for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 0]
        self.assertEqual([t["name"] for t in texts], ["general", "loose"])
        self.assertTrue(all(t.get("parent_id") for t in texts))
        self.assertEqual(self.store.targets()["6"], {"target_id": "900", "target_name": "Desk copy", "shared": True})
        self.assertEqual(self.store.targets()["5"], {"target_id": "900", "target_name": "Desk copy", "shared": False})

    async def test_refill_of_the_first_source_keeps_its_names_after_a_second_source(self) -> None:
        # Desk (5) was filled first into 900 and keeps "Talk"; Other (6) came second and got "Other / Talk" (decision 9o).
        # Filling Desk again must find its own category, channel and webhook, not build "Desk / Talk" beside them.
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "Talk"},
            {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"},
            {"id": "c2", "type": 4, "name": "Other / Talk"},
            {"id": "t2", "type": 0, "name": "general", "parent_id": "c2"},
        ]
        http.hooks["t1"] = [{"id": "77", "name": "general", "token": "old"}]
        self.engine.http = http
        self.store.set_target("5", "900", "Desk copy")
        self.store.set_target("6", "900", "Desk copy")
        # the row holds the URL of Desk's first fill: found again, so nothing counts as replaced
        self.store.replace_selection([row("10", "https://discord.com/api/webhooks/77/old", name="general") | {"parent": "Talk"}])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 1, "replaced": 0})
        self.assertEqual([path for method, path, body in http.calls if method == "POST"], [])
        # one spot of that name: its webhooks are listed once, when the row looks for the channel that carries its
        # webhook, with one wait; the webhook step then neither lists nor waits
        self.assertEqual([path for method, path, body in http.calls if method == "GET"], ["/channels/t1/webhooks"])
        self.assertEqual(self.delays, [0.25])
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, {"10": "https://discord.com/api/webhooks/77/old"})
        self.assertFalse(any("replaced" in line for line in self.notes()))
        self.assertEqual(self.store.targets()["5"], {"target_id": "900", "target_name": "Desk copy", "shared": False})
        self.assertEqual(self.store.targets()["6"], {"target_id": "900", "target_name": "Desk copy", "shared": True})

    async def test_a_target_keeps_holding_a_source_that_moved_to_another_target(self) -> None:
        # Desk (5) is filled into 900 and then into 901; nothing is deleted in 900 (decision 9i), so 900 still holds
        # Desk's "Talk", "#general" and its webhook. Other (6) filled into 900 next is the second source there
        # (decision 9o): its own category and webhook, never Desk's. Desk filled into 900 again finds its own channel.
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}, {"id": "901", "name": "Spare", "owner": True}]
        http.sources["900"] = []
        http.sources["901"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("20", name="general", guild_id="6") | {"guild_name": "Other", "parent": "Talk"}])
        await self.engine.fill_copy("5", "900")
        await self.engine.fill_copy("5", "901")
        http.sources["900"] = [{"id": "c1", "type": 4, "name": "Talk"}, {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"}]
        http.hooks["t1"] = [{"id": "77", "name": "general", "token": "old"}]
        http.calls.clear()
        report = await self.engine.fill_copy("6", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 0, "replaced": 0})
        self.assertEqual([b["name"] for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 4], ["Other / Talk"])
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertNotEqual(urls["20"], "https://discord.com/api/webhooks/77/old")
        self.assertEqual(self.store.targets()["6"], {"target_id": "900", "target_name": "Desk copy", "shared": True})
        http.calls.clear()
        # the row holds the URL the fill into 901 made, so finding Desk's own webhook in 900 again replaces it
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 1, "replaced": 1})
        self.assertEqual([path for method, path, body in http.calls if method == "POST"], [])
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual(urls["10"], "https://discord.com/api/webhooks/77/old")
        self.assertEqual(self.store.targets()["5"], {"target_id": "900", "target_name": "Desk copy", "shared": False})

    async def test_a_target_holds_a_source_whose_first_fill_failed(self) -> None:
        # Desk's first fill into 900 made the category "Talk" and then failed on its channel, so no link was stored;
        # 900 holds Desk's category all the same, and Other filled into 900 next is the second source there (decision 9o)
        http = FakeHTTP(fail=("general",))
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("20", name="news", guild_id="6") | {"guild_name": "Other", "parent": "Talk"}])
        with self.assertRaises(ApiError):
            await self.engine.fill_copy("5", "900")
        self.assertEqual(self.store.targets(), {})
        # the category is recorded for Desk as soon as Discord made it, before the fill failed
        self.assertEqual(self.store.category_sources("900"), {"1001": "5"})
        http.sources["900"] = [{"id": "c1", "type": 4, "name": "Talk"}]
        http.calls.clear()
        await self.engine.fill_copy("6", "900")
        self.assertEqual([b["name"] for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 4], ["Other / Talk"])
        self.assertTrue(self.store.targets()["6"]["shared"])

    async def test_a_second_source_named_like_a_category_of_the_first_gets_its_own_category(self) -> None:
        # Desk (5) has "general" under "Trading"; the server "Trading" (6) comes second with a loose "general", so its
        # category is named "Trading" too. A category belongs to the source a fill made it for, not to its name: 6 gets
        # its own "Trading", channel and webhook, never Desk's (decision 9o), and each refill finds its own again
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection(
            [row("10", name="general") | {"parent": "Trading"}, row("20", name="general", guild_id="6") | {"guild_name": "Trading"}]
        )
        await self.engine.fill_copy("5", "900")
        http.calls.clear()
        report = await self.engine.fill_copy("6", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 0, "replaced": 0})
        made = self.posts(http, "/guilds/900/channels")
        self.assertEqual([(b["type"], b["name"]) for b in made], [(4, "Trading"), (0, "general")])
        desk, other = [item["id"] for item in http.sources["900"] if item["type"] == 4]
        self.assertEqual(made[1]["parent_id"], other)
        self.assertEqual(self.store.category_sources("900"), {desk: "5", other: "6"})
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertNotEqual(urls["10"], urls["20"])
        http.calls.clear()
        self.assertEqual((await self.engine.fill_copy("5", "900"))["reused"], 1)
        self.assertEqual((await self.engine.fill_copy("6", "900"))["reused"], 1)
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, urls)

    async def test_sources_of_the_same_name_get_their_own_categories(self) -> None:
        # Discord server names are not unique: three servers named "Gaming" with "general" under "Talk" fill one target.
        # The second and the third both get "Gaming / Talk" (decision 9o), as two categories, each with its own channel
        # and webhook, and a refill of each finds its own again
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection(
            [row(channel, name="general", guild_id=guild) | {"guild_name": "Gaming", "parent": "Talk"} for channel, guild in (("10", "5"), ("20", "6"), ("30", "7"))]
        )
        reports = [await self.engine.fill_copy(guild, "900") for guild in ("5", "6", "7")]
        self.assertEqual([report["reused"] for report in reports], [0, 0, 0])
        made = self.posts(http, "/guilds/900/channels")
        self.assertEqual([b["name"] for b in made if b["type"] == 4], ["Talk", "Gaming / Talk", "Gaming / Talk"])
        self.assertEqual(len({b["parent_id"] for b in made if b["type"] == 0}), 3)
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual(len(set(urls.values())), 3)
        http.calls.clear()
        for guild in ("7", "6", "5"):
            self.assertEqual((await self.engine.fill_copy(guild, "900"))["reused"], 1)
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, urls)

    async def test_a_first_source_never_takes_a_category_made_for_a_later_source(self) -> None:
        # Desk (5) came first with "general" under "Talk"; the server "Trading" (6) came second and got the category
        # "Trading" for its loose "general". The owner then ticks Desk's "general" under Desk's own "Trading": Desk's
        # refill makes its own "Trading", never puts Desk's channel in 6's category or on 6's webhook (decision 9o)
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection(
            [row("10", name="general") | {"parent": "Talk"}, row("20", name="general", guild_id="6") | {"guild_name": "Trading"}]
        )
        await self.engine.fill_copy("5", "900")
        await self.engine.fill_copy("6", "900")
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.store.replace_selection(
            [
                row("10", urls["10"], name="general") | {"parent": "Talk"},
                row("11", name="general") | {"parent": "Trading"},
                row("20", urls["20"], name="general", guild_id="6") | {"guild_name": "Trading"},
            ]
        )
        http.calls.clear()
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 2, "reused": 1, "replaced": 0})
        self.assertEqual([(b["type"], b["name"]) for b in self.posts(http, "/guilds/900/channels")], [(4, "Trading"), (0, "general")])
        after = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertEqual((after["10"], after["20"]), (urls["10"], urls["20"]))
        self.assertNotIn(after["11"], (urls["10"], urls["20"]))

    async def test_a_later_source_never_takes_a_category_no_fill_made(self) -> None:
        # a fresh server has "general" under "Text Channels"; Desk (5), alone in 900, reuses it for its loose "general"
        # (decision 9i). The server "Text Channels" (6) comes second: the server's own category is not 6's, so 6 makes
        # its own "Text Channels" and never takes the "general" that carries Desk's webhook (decision 9o)
        http = KeepingHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = [
            {"id": "c1", "type": 4, "name": "Text Channels"},
            {"id": "t1", "type": 0, "name": "general", "parent_id": "c1"},
        ]
        self.engine.http = http
        self.store.replace_selection(
            [row("10", name="general"), row("20", name="general", guild_id="6") | {"guild_name": "Text Channels"}]
        )
        await self.engine.fill_copy("5", "900")
        http.calls.clear()
        report = await self.engine.fill_copy("6", "900")
        self.assertEqual(report, {"target": "Desk copy", "filled": 1, "reused": 0, "replaced": 0})
        made = self.posts(http, "/guilds/900/channels")
        self.assertEqual([(b["type"], b["name"]) for b in made], [(4, "Text Channels"), (0, "general")])
        self.assertNotEqual(made[1]["parent_id"], "c1")
        self.assertFalse(any(path == "/channels/t1/webhooks" for method, path, body in http.calls))
        urls = {r["channel_id"]: r["webhook_url"] for r in self.store.selection()}
        self.assertNotEqual(urls["10"], urls["20"])
        http.calls.clear()
        self.assertEqual((await self.engine.fill_copy("6", "900"))["reused"], 1)
        self.assertEqual((await self.engine.fill_copy("5", "900"))["reused"], 1)
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual({r["channel_id"]: r["webhook_url"] for r in self.store.selection()}, urls)

    async def test_fill_refuses_bad_servers(self) -> None:
        # 7 is a server the account is in but does not own (decision 9b'), 8 is not listed at all: neither is written to
        http = FakeHTTP()
        http.listed = [
            {"id": "5", "name": "Desk", "owner": True},
            {"id": "900", "name": "Desk copy", "owner": True},
            {"id": "7", "name": "Theirs", "owner": False},
        ]
        self.engine.http = http
        self.store.replace_selection([row("10", name="general")])
        for source, target, text in (("x", "900", "unknown server"), ("5", "5", "a server cannot be its own copy"), ("6", "900", "tick the server or some of its channels first"), ("5", "7", "pick a server you own"), ("5", "8", "pick a server you own")):
            with self.assertRaises(ApiError) as caught:
                await self.engine.fill_copy(source, target)
            self.assertEqual(str(caught.exception), text)
        self.assertEqual([c for c in http.calls if c[0] == "POST"], [])
        self.assertEqual(self.store.targets(), {})
        self.assertEqual(self.store.selection()[0]["webhook_url"], "")

    async def test_fill_skips_what_discord_refuses_and_fails_when_nothing_is_wired(self) -> None:
        http = FakeHTTP(fail=("Talk", "news"))
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general") | {"parent": "Talk"}, row("11", name="news"), row("12", name="lobby")])
        report = await self.engine.fill_copy("5", "900")
        self.assertEqual(report["filled"], 1)
        self.assertIn("category Talk was not created (no)", self.notes())
        self.assertIn("#news was not created (no)", self.notes())
        # a channel whose category Discord refused is skipped, never put loose (decision 9o)
        self.assertIn("#general was not created (its category failed)", self.notes())
        self.assertEqual([b["name"] for b in self.posts(http, "/guilds/900/channels") if b.get("type") == 0], ["lobby", "news"])
        http = FakeHTTP(fail=("general",))
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        self.store.replace_selection([row("10", name="general")])
        with self.assertRaises(ApiError) as caught:
            await self.engine.fill_copy("5", "900")
        self.assertEqual(str(caught.exception), "no webhook could be created")

    async def test_fill_while_running_refreshes(self) -> None:
        http = FakeHTTP()
        http.listed = [{"id": "900", "name": "Desk copy", "owner": True}]
        http.sources["900"] = []
        self.engine.http = http
        with mock.patch.object(engine_mod, "Gateway", FakeGateway):
            self.store.replace_selection([row("10", HOOK)])
            await self.engine.start()
            self.store.replace_selection([row("10", HOOK), row("11", name="lobby")])
            await self.engine.fill_copy("5", "900")
            self.assertEqual(FakeGateway.made[0].resubscribed, 1)
            self.assertTrue(self.engine._webhook_for("11").startswith("https://discord.com/api/webhooks/"))
            await self.engine.stop()
```

The selection row carries the age restriction of its source channel (column `nsfw`), so the fill needs no listing of the source. In `tests/test_engine.py`, after `test_store_migrates_old_db`:

```python
    def test_store_without_the_age_restriction_column_opens(self) -> None:
        # a row stored before the column has no age gate until the owner ticks the channel again
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "state.db")
            conn.executescript(OLD_SCHEMA)
            conn.execute("INSERT INTO selection (channel_id, guild_id, channel_name) VALUES ('10', '5', 'general')")
            conn.commit()
            conn.close()
            store = Store(tmp)
            self.assertEqual([(r["channel_id"], r["nsfw"]) for r in store.selection()], [("10", 0)])
            store.replace_selection([row("10") | {"nsfw": True}, row("11", name="news")])
            store.close()
            again = Store(tmp)
            self.assertEqual([(r["channel_id"], r["nsfw"]) for r in again.selection()], [("10", 1), ("11", 0)])
            again.close()

    async def test_channel_list_and_saved_rows_carry_the_age_restriction(self) -> None:
        # Engine.channels gives each channel its age restriction, and the save of PUT /api/setup keeps it on the row
        http = FakeHTTP()
        http.listed = [{"id": "5", "name": "Desk", "owner": True}]
        http.sources["5"] = [
            {"id": "10", "type": 0, "name": "general", "nsfw": True},
            {"id": "11", "type": 0, "name": "news"},
        ]
        self.engine.http = http
        self.engine.user = {"id": "1", "username": "ada"}
        listed = await self.engine.channels("5")
        self.assertEqual([(c["id"], c["nsfw"]) for c in listed], [("10", True), ("11", False)])
        self.engine.save_setup(
            {"channels": [row(c["id"], name=c["name"]) | {"nsfw": c["nsfw"]} for c in listed]}
        )
        self.assertEqual([(r["channel_id"], r["nsfw"]) for r in self.store.selection()], [("10", 1), ("11", 0)])
```

and in `tests/test_cli_controller.py` `ServersScreenTests`, after `test_enter_ticks_a_whole_server_and_again_unticks_it` (the stand-in row of an unlisted channel in `test_start_jumps_to_a_ticked_channel_of_a_server_the_account_left` carries the stored `nsfw` too):

```python
    async def test_a_ticked_channel_keeps_its_age_restriction(self) -> None:
        # the fill creates the copy of an age-restricted channel with the age gate from the saved row alone
        ui, engine = await self.open_servers()
        engine.channel_lists["g1"][0]["nsfw"] = True
        await keys(ui, "Enter")
        saved = {row["channel_id"]: row["nsfw"] for row in engine.calls[-1][1]["channels"]}
        self.assertEqual(saved, {"c1": True, "c2": False})
```

In `tests/test_core.py`, `test_store_targets_per_source` gets the flag (replace its `set_target` lines and its `targets()` assertion; keep the two `hasattr` checks), a new test opens a file whose `targets` table Task 1 made without a `shared` column (and without `fills` and `fill_categories`), a new test pins that a new file's `targets` table has no `shared` column, two new tests open a file whose `targets` table has one (without `fills`, and with `fills` holding another answer than the column), a new test pins the `fills` record, and a new test pins the `fill_categories` record:

```python
            store.set_target("5", "900", "Desk copy")
            store.set_target("6", "900", "Desk copy")
            store.set_target("7", "900", "Desk copy")
            store.set_target("5", "901", "Other")
            store.set_target("7", "902", "Third")
            # the flag is the fills record's (decision 9o): 900 held 5 first, so 6 came second there
            self.assertEqual(
                store.targets(),
                {
                    "5": {"target_id": "901", "target_name": "Other", "shared": False},
                    "6": {"target_id": "900", "target_name": "Desk copy", "shared": True},
                    "7": {"target_id": "902", "target_name": "Third", "shared": False},
                },
            )
```

```python
    def test_targets_table_without_shared_column_opens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "state.db")
            conn.executescript(
                """
                CREATE TABLE targets (
                    source_guild_id TEXT PRIMARY KEY,
                    target_guild_id TEXT NOT NULL,
                    target_name TEXT NOT NULL DEFAULT ''
                );
                INSERT INTO targets (source_guild_id, target_guild_id, target_name) VALUES ('5', '900', 'Desk copy');
                """
            )
            conn.commit()
            conn.close()
            store = Store(tmp)
            self.assertEqual(store.targets(), {"5": {"target_id": "900", "target_name": "Desk copy", "shared": False}})
            store.set_target("6", "900", "Desk copy")
            self.assertTrue(store.targets()["6"]["shared"])
            # the link stored before the record of fills existed still says that 900 holds 5
            self.assertFalse(store.record_fill("5", "900"))
            self.assertTrue(store.record_fill("8", "900"))
            self.assertEqual(store.category_sources("900"), {})
            store.close()

    def test_a_new_targets_table_has_no_shared_column(self) -> None:
        # the fills record alone holds the flag, so a snapshot and a fill never read two answers
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            columns = [row[1] for row in store.conn.execute("PRAGMA table_info(targets)")]
            self.assertEqual(columns, ["source_guild_id", "target_guild_id", "target_name"])
            store.close()

    def test_targets_table_with_shared_column_and_no_fills_opens(self) -> None:
        # a file from before the fills record: the flag sat in the targets column only
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "state.db")
            conn.executescript(
                """
                CREATE TABLE targets (
                    source_guild_id TEXT PRIMARY KEY,
                    target_guild_id TEXT NOT NULL,
                    target_name TEXT NOT NULL DEFAULT '',
                    shared INTEGER NOT NULL DEFAULT 0
                );
                INSERT INTO targets VALUES ('5', '900', 'Desk copy', 0), ('6', '900', 'Desk copy', 1);
                """
            )
            conn.commit()
            conn.close()
            store = Store(tmp)
            self.assertEqual(
                store.targets(),
                {
                    "5": {"target_id": "900", "target_name": "Desk copy", "shared": False},
                    "6": {"target_id": "900", "target_name": "Desk copy", "shared": True},
                },
            )
            self.assertFalse(store.record_fill("5", "900"))
            self.assertTrue(store.record_fill("6", "900"))
            store.close()

    def test_targets_table_with_shared_column_reads_the_fills_record(self) -> None:
        # a file whose targets column and fills record both hold the flag: the column stays in place unread, as the
        # old option columns do (9h), so a column that says another answer than the record changes nothing
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "state.db")
            conn.executescript(
                """
                CREATE TABLE targets (
                    source_guild_id TEXT PRIMARY KEY,
                    target_guild_id TEXT NOT NULL,
                    target_name TEXT NOT NULL DEFAULT '',
                    shared INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE fills (
                    target_guild_id TEXT NOT NULL,
                    source_guild_id TEXT NOT NULL,
                    shared INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (target_guild_id, source_guild_id)
                );
                INSERT INTO targets VALUES ('5', '900', 'Desk copy', 0), ('6', '900', 'Desk copy', 0);
                INSERT INTO fills VALUES ('900', '5', 0), ('900', '6', 1);
                """
            )
            conn.commit()
            conn.close()
            store = Store(tmp)
            self.assertEqual(
                store.targets(),
                {
                    "5": {"target_id": "900", "target_name": "Desk copy", "shared": False},
                    "6": {"target_id": "900", "target_name": "Desk copy", "shared": True},
                },
            )
            store.set_target("6", "900", "Desk copy")
            self.assertTrue(store.targets()["6"]["shared"])
            columns = [row[1] for row in store.conn.execute("PRAGMA table_info(targets)")]
            self.assertIn("shared", columns)
            store.close()

    def test_a_target_remembers_every_source_filled_into_it(self) -> None:
        # decision 9o: the first source in a target keeps its names; nothing is deleted there (9i), so a source moved
        # to another target is still held by the first one, and a later source there is not the first
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            self.assertFalse(store.record_fill("5", "900"))
            self.assertTrue(store.record_fill("6", "900"))
            self.assertFalse(store.record_fill("5", "900"))
            self.assertFalse(store.record_fill("5", "901"))
            store.set_target("5", "901", "Spare")
            self.assertEqual(store.targets()["5"], {"target_id": "901", "target_name": "Spare", "shared": False})
            self.assertTrue(store.record_fill("7", "900"))
            self.assertTrue(store.record_fill("6", "900"))
            self.assertFalse(store.record_fill("5", "900"))
            self.assertTrue(store.record_fill("7", "901"))
            store.set_target("8", "902", "Third")
            self.assertTrue(store.record_fill("9", "902"))
            store.close()
            again = Store(tmp)
            self.assertFalse(again.record_fill("5", "900"))
            self.assertTrue(again.record_fill("7", "900"))
            again.close()

    def test_a_target_tells_whether_it_holds_another_source(self) -> None:
        # the first source keeps shared False after a second one arrived; a fill of it must still know that the
        # target holds another source, whose channels may carry the same names (decision 9o)
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            self.assertFalse(store.holds_another("900", "5"))
            self.assertFalse(store.record_fill("5", "900"))
            self.assertFalse(store.holds_another("900", "5"))
            self.assertTrue(store.holds_another("900", "6"))
            self.assertTrue(store.record_fill("6", "900"))
            self.assertTrue(store.holds_another("900", "5"))
            self.assertFalse(store.holds_another("901", "5"))
            store.close()

    def test_a_target_remembers_the_categories_made_for_each_source(self) -> None:
        # names do not tell two sources apart (decision 9o), so each category a fill makes is recorded for its source;
        # the first record of a category stays
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            self.assertEqual(store.category_sources("900"), {})
            store.record_category("900", "5", "c1")
            store.record_category("900", "6", "c2")
            store.record_category("900", "6", "c1")
            store.record_category("901", "5", "c3")
            self.assertEqual(store.category_sources("900"), {"c1": "5", "c2": "6"})
            store.close()
            again = Store(tmp)
            self.assertEqual(again.category_sources("900"), {"c1": "5", "c2": "6"})
            self.assertEqual(again.category_sources("901"), {"c3": "5"})
            again.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -X utf8 -m unittest tests.test_engine tests.test_core -q`
Expected: FAIL — `AttributeError: 'Engine' object has no attribute 'owned_guilds'` / `'fill_copy'`, and in `tests.test_core` `test_store_targets_per_source`, `test_targets_table_without_shared_column_opens`, `test_targets_table_with_shared_column_and_no_fills_opens` and `test_targets_table_with_shared_column_reads_the_fills_record` with an `AssertionError` (the links have no `"shared"` key; measured on 487f08f, the commit before this task, where `test_a_new_targets_table_has_no_shared_column` passes, since Task 1's table has no such column), `test_a_target_remembers_every_source_filled_into_it` with `AttributeError: 'Store' object has no attribute 'record_fill'` and `test_a_target_remembers_the_categories_made_for_each_source` with `AttributeError: 'Store' object has no attribute 'category_sources'`. (Added in the Task 4 review, round 3, onto the code without the `fill_categories` record, the four tests of a category made for another source failed on the measured mix: `test_a_second_source_named_like_a_category_of_the_first_gets_its_own_category` and `test_a_later_source_never_takes_a_category_no_fill_made` with `{'filled': 1, 'reused': 1} != {'filled': 1, 'reused': 0}`, `test_sources_of_the_same_name_get_their_own_categories` with `[0, 0, 1] != [0, 0, 0]`, `test_a_first_source_never_takes_a_category_made_for_a_later_source` with `'reused': 2 != 'reused': 1`.)

- [ ] **Step 3: Implement**

In `mirror/store.py`, the `targets` table in `SCHEMA` stays as Task 1 made it (no `shared` column: the `fills` record alone holds the flag), and `SCHEMA` gets the `fills` and `fill_categories` tables after it (an older file gets them from `CREATE TABLE IF NOT EXISTS`):

```sql
CREATE TABLE IF NOT EXISTS fills (
    target_guild_id TEXT NOT NULL,
    source_guild_id TEXT NOT NULL,
    shared INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (target_guild_id, source_guild_id)
);
CREATE TABLE IF NOT EXISTS fill_categories (
    target_guild_id TEXT NOT NULL,
    category_id TEXT NOT NULL,
    source_guild_id TEXT NOT NULL,
    PRIMARY KEY (target_guild_id, category_id)
);
```

`_migrate` adds the `nsfw` column after the `topic` one, the same way:

```python
        if "nsfw" not in picked_cols:
            # a row stored before this column has no age gate until the owner ticks its channel again
            self.conn.execute("ALTER TABLE selection ADD COLUMN nsfw INTEGER NOT NULL DEFAULT 0")
```

`_migrate` copies the links of an older file into `fills`, with the flag of a `targets` column `shared` when the file has one:

```python
        # a link stored before the fills record existed: its target holds that source. A file whose targets table
        # has the shared column gives the flag from it here; nothing else reads that column, as with the options above
        target_cols = {row[1] for row in self.conn.execute("PRAGMA table_info(targets)")}
        flag = "shared" if "shared" in target_cols else "0"
        self.conn.execute(
            "INSERT OR IGNORE INTO fills (target_guild_id, source_guild_id, shared) "
            f"SELECT target_guild_id, source_guild_id, {flag} FROM targets"
        )
```

and `set_target` / `record_fill` / `holds_another` / `record_category` / `category_sources` / `targets` carry it:

```python
    def set_target(self, source_guild_id: str, target_guild_id: str, target_name: str) -> None:
        """The server the owner picked as the copy of one source server (decision 9b'); a later pick replaces it.
        The fills record holds the pair from then on, as `record_fill` records it, and keeps it when a later pick
        moves the link away; the link's `shared` flag is the record's (decision 9o)."""
        source, target = str(source_guild_id), str(target_guild_id)
        self.conn.execute(
            "INSERT INTO targets (source_guild_id, target_guild_id, target_name) VALUES (?, ?, ?) "
            "ON CONFLICT(source_guild_id) DO UPDATE SET "
            "target_guild_id = excluded.target_guild_id, target_name = excluded.target_name",
            (source, target, str(target_name or "")[:100]),
        )
        self.record_fill(source, target)
        self.conn.commit()

    def record_fill(self, source_guild_id: str, target_guild_id: str) -> bool:
        """Record, before a fill writes to the target, that the target holds this source from now on (Mando never
        deletes there, decision 9i), and return whether another source was in that target first (decision 9o).
        A source already recorded there gets its first answer again, so a refill keeps the categories of its
        first fill, also after a second source arrived or after the source was filled into another target."""
        source, target = str(source_guild_id), str(target_guild_id)
        found = self.conn.execute(
            "SELECT shared FROM fills WHERE target_guild_id = ? AND source_guild_id = ?", (target, source)
        ).fetchone()
        if found is not None:
            return bool(found["shared"])
        shared = self.holds_another(target, source)
        self.conn.execute(
            "INSERT INTO fills (target_guild_id, source_guild_id, shared) VALUES (?, ?, ?)",
            (target, source, int(shared)),
        )
        self.conn.commit()
        return shared

    def holds_another(self, target_guild_id: str, source_guild_id: str) -> bool:
        """Whether the fills record holds a source other than this one in that target, whichever came first
        (decision 9o): a channel of the same name there may then be the other source's."""
        other = self.conn.execute(
            "SELECT 1 FROM fills WHERE target_guild_id = ? AND source_guild_id != ? LIMIT 1",
            (str(target_guild_id), str(source_guild_id)),
        ).fetchone()
        return other is not None

    def record_category(self, target_guild_id: str, source_guild_id: str, category_id: str) -> None:
        """Record that a fill of this source made that category in the target (decision 9o): it is this source's,
        whatever its name, since two sources can carry the same name. The first record of a category stays."""
        self.conn.execute(
            "INSERT OR IGNORE INTO fill_categories (target_guild_id, category_id, source_guild_id) VALUES (?, ?, ?)",
            (str(target_guild_id), str(category_id), str(source_guild_id)),
        )
        self.conn.commit()

    def category_sources(self, target_guild_id: str) -> dict[str, str]:
        """The categories fills made in the target, each with the source it was made for."""
        rows = self.conn.execute(
            "SELECT category_id, source_guild_id FROM fill_categories WHERE target_guild_id = ?",
            (str(target_guild_id),),
        ).fetchall()
        return {str(row["category_id"]): str(row["source_guild_id"]) for row in rows}

    def targets(self) -> dict[str, dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT t.source_guild_id, t.target_guild_id, t.target_name, COALESCE(f.shared, 0) AS shared "
            "FROM targets t LEFT JOIN fills f "
            "ON f.target_guild_id = t.target_guild_id AND f.source_guild_id = t.source_guild_id"
        ).fetchall()
        return {
            str(row["source_guild_id"]): {
                "target_id": str(row["target_guild_id"]),
                "target_name": str(row["target_name"]),
                "shared": bool(row["shared"]),
            }
            for row in rows
        }
```

In `mirror/engine.py` (`from .provision import copy_layout, same_name, webhook_name`), after `channels()`:

```python
    async def owned_guilds(self) -> list[dict[str, Any]]:
        """The servers the account owns (the `owner` flag of /users/@me/guilds): the only servers a fill writes to
        (decision 9b')."""
        http = self._require_http()
        rows = [
            {"id": str(guild.get("id")), "name": guild.get("name") or "server"}
            for guild in await http.guilds()
            if guild.get("owner")
        ]
        rows.sort(key=lambda item: item["name"].casefold())
        return rows

    async def fill_copy(self, source_id: str, target_id: str) -> dict[str, Any]:
        """Create the ticked channels of one source server, with their categories and one webhook each, inside a
        server the owner owns, and keep the webhook URLs (decisions 9b', 9i, 9n, 9o). Nothing is ever deleted, so
        one fill runs at a time and never beside a Start (`_setup`): a second fill reads the target after the
        first one wrote to it and creates nothing twice."""
        async with self._setup:
            return await self._fill_copy(source_id, target_id)

    async def _fill_copy(self, source_id: str, target_id: str) -> dict[str, Any]:
        if not source_id.isdigit() or not target_id.isdigit():
            raise ApiError(400, "unknown server")
        if source_id == target_id:
            raise ApiError(400, "a server cannot be its own copy")
        http = self._require_http()
        rows = [row for row in self.store.selection() if row["enabled"] and row["guild_id"] == source_id]
        if not rows:
            raise ApiError(400, "tick the server or some of its channels first")
        target = next((guild for guild in await self.owned_guilds() if guild["id"] == target_id), None)
        if target is None:
            raise ApiError(400, "pick a server you own")
        existing = await http.channels(target_id)
        # what the target holds, not where the links point now: a source moved to another target, or whose first
        # fill failed halfway, still has its channels here (decisions 9i, 9o); a refill keeps its first layout
        shared = self.store.record_fill(source_id, target_id)
        # with another source in the target, first or later, a channel of the same name outside this source's
        # category may be that source's channel (decision 9o)
        alone = not self.store.holds_another(target_id, source_id)
        # a category is the source's a fill made it for, not whoever carries its name: server names are not unique,
        # and a later source's "<source>" can be a category of the first (decision 9o). A later source uses only the
        # categories made for it, the first source also those no fill made (the server's own, decision 9i)
        made = self.store.category_sources(target_id)
        existing = [
            item
            for item in existing
            if not isinstance(item, dict)
            or item.get("type") != 4
            or made.get(str(item.get("id") or "")) == source_id
            or (not shared and str(item.get("id") or "") not in made)
        ]
        source_name = rows[0].get("guild_name") or "server"
        layout = copy_layout(source_name, rows, shared)
        self.note(f"filling {target['name']} from {source_name}")
        before = {str(row["channel_id"]): str(row.get("webhook_url") or "") for row in rows}
        pairs, reused = await self._fill(http, target_id, layout, existing, before, alone, source_id)
        # a fill covers every ticked channel (decision 9n), an own webhook or an earlier copy's URL included; the
        # store has no mark of who made a URL, so each one this fill changes is counted and noted, never kept. A URL
        # is compared by its webhook id and token: a found webhook is written with the discord.com host, and the
        # same webhook pasted with a ptb., canary. or discordapp.com host is not replaced
        replaced = sum(
            1 for source, url in pairs if before.get(source) and webhook_parts(before[source]) != webhook_parts(url)
        )
        self.store.fill_webhooks(pairs)
        self.store.set_target(source_id, target_id, target["name"])
        text = f"webhooks on {len(pairs)} channel(s) in {target['name']}"
        if reused:
            text += f", {reused} reused"
        self.note(text)
        if replaced:
            self.note(f"{replaced} earlier webhook url(s) replaced")
        if self.running:
            await self.refresh()
        return {"target": target["name"], "filled": len(pairs), "reused": reused, "replaced": replaced}

    async def _fill(
        self,
        http: DiscordHTTP,
        target_id: str,
        layout: dict[str, Any],
        existing: list[dict[str, Any]],
        before: dict[str, str],
        alone: bool,
        source_id: str,
    ) -> tuple[list[tuple[str, str]], int]:
        """Find or create each channel of the layout in the target and give it a webhook (decisions 9i, 9o). A
        channel of the same name (`same_name`) that carries a row's webhook (`before`) is that row's, under its own
        category or elsewhere, and every row with a URL claims it before any row takes a channel by name, so two
        rows of one name never swap copies. Then a row takes the first free channel of its name under its own
        category (loose for a loose one; `existing` holds only the categories this source may use), and after that
        one elsewhere if the target holds no other source (`alone`), since it may otherwise be another source's
        channel. An existing channel serves one row only, a category is created only for a channel created under it
        and recorded for `source_id` as soon as Discord made it, and a channel whose category Discord refuses is
        skipped, never put loose."""
        categories: dict[str, str] = {}
        texts: list[dict[str, Any]] = []
        for item in existing:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if item.get("type") == 4:
                categories.setdefault(str(item.get("name") or "").casefold(), str(item["id"]))
            elif item.get("type") in TEXT_TYPES:
                texts.append(item)
        parents = {
            category["key"]: categories[category["name"].casefold()]
            for category in layout["categories"]
            if category["name"].casefold() in categories
        }
        claimed: set[str] = set()
        found: dict[str, str] = {}  # source channel id -> the existing channel it gets
        carried: dict[str, str] = {}  # source channel id -> the row's own webhook, found on that channel
        listed: dict[str, list[dict[str, Any]]] = {}  # existing channel id -> its webhooks, once fetched
        unlisted: set[str] = set()  # existing channels whose webhooks Discord refused to list in this fill

        def free(name: str, parent: str | None) -> list[str]:
            return [
                str(item["id"])
                for item in texts
                if str(item["id"]) not in claimed
                and same_name(str(item.get("name") or ""), name)
                and (parent is None or str(item.get("parent_id") or "") == parent)
            ]

        async def carrier(source: str, spots: list[str]) -> str:
            """The spot whose webhooks hold the row's own webhook (`before`), each spot listed once and kept in
            `listed`, with that webhook kept in `carried`; "" when no spot carries it. A spot whose listing fails is
            kept out of `listed`, so the webhook step lists it again rather than take it for one without webhooks."""
            mine = webhook_parts(before.get(source, ""))
            for spot in spots if mine else []:
                if spot not in listed and spot not in unlisted:
                    hooks = await self._hooks_on(http, spot)
                    await self._wait(0.25)
                    if hooks is None:
                        unlisted.add(spot)
                    else:
                        listed[spot] = hooks
                own = next(
                    (
                        hook
                        for hook in listed.get(spot, [])
                        if str(hook.get("id") or "") == mine[0] and hook.get("token")
                    ),
                    None,
                )
                if own is None:
                    continue
                try:
                    carried[source] = clean_webhook(f"https://discord.com/api/webhooks/{own['id']}/{own['token']}")
                except ApiError:
                    continue
                return spot
            return ""

        def home(channel: dict[str, Any]) -> str | None:
            """The parent of the channel's own place in the target ("" when loose), None when its category is not in
            the target yet, so nothing is under it."""
            key = channel["category_key"]
            return None if key and key not in parents else parents.get(key, "")

        # a channel that carries a row's own webhook is that row's copy, wherever it stands: every row with a URL
        # claims it before any row takes a channel by its name, first in its own place, then elsewhere, so a row
        # without a URL, or a row listed first, never takes another row's copy and history
        for channel in layout["channels"]:
            parent = home(channel)
            if parent is not None:
                pick = await carrier(channel["source_id"], free(channel["name"], parent))
                if pick:
                    found[channel["source_id"]] = pick
                    claimed.add(pick)
        for channel in layout["channels"]:
            source = channel["source_id"]
            if source not in found:
                pick = await carrier(source, free(channel["name"], None))
                if pick:
                    found[source] = pick
                    claimed.add(pick)
        # then by name, every channel in its own place first, so a name found elsewhere never takes a channel another
        # row has there; elsewhere only when the target holds no other source (`alone`)
        for channel in layout["channels"]:
            source, parent = channel["source_id"], home(channel)
            spots = free(channel["name"], parent) if source not in found and parent is not None else []
            if spots:
                found[source] = spots[0]
                claimed.add(spots[0])
        for channel in layout["channels"] if alone else []:
            source = channel["source_id"]
            spots = free(channel["name"], None) if source not in found else []
            if spots:
                found[source] = spots[0]
                claimed.add(spots[0])
        needed = {channel["category_key"] for channel in layout["channels"] if channel["source_id"] not in found}
        for category in layout["categories"]:
            if category["key"] in parents or category["key"] not in needed:
                continue
            try:
                created = await http.call("POST", f"/guilds/{target_id}/channels", json={"name": category["name"], "type": 4})
            except ApiError as exc:
                self.note(f"category {category['name']} was not created ({exc})")
                await self._wait(0.3)
                continue
            if isinstance(created, dict) and created.get("id"):
                parents[category["key"]] = str(created["id"])
                self.store.record_category(target_id, source_id, str(created["id"]))
            else:
                self.note(f"category {category['name']} was not created")
            await self._wait(0.3)
        pairs: list[tuple[str, str]] = []
        reused = 0
        for channel in layout["channels"]:
            source = channel["source_id"]
            dest = found.get(source, "")
            if not dest:
                key = channel["category_key"]
                if key and key not in parents:
                    # never loose: a loose channel of the same name may be another source's (decision 9o)
                    self.note(f"#{channel['name']} was not created (its category failed)")
                    continue
                body: dict[str, Any] = {"name": channel["name"], "type": 0}
                if key:
                    body["parent_id"] = parents[key]
                if channel["topic"]:
                    body["topic"] = channel["topic"]
                if channel.get("nsfw"):
                    body["nsfw"] = True  # on creation only: a channel the fill finds is never altered
                try:
                    created = await http.call("POST", f"/guilds/{target_id}/channels", json=body)
                except ApiError as exc:
                    self.note(f"#{channel['name']} was not created ({exc})")
                    await self._wait(0.25)
                    continue
                if not isinstance(created, dict) or not created.get("id"):
                    self.note(f"#{channel['name']} was not created")
                    await self._wait(0.25)
                    continue
                dest = str(created["id"])
                await self._wait(0.25)
            if source in carried:
                url, kept = carried[source], True
            else:
                url, kept = await self._webhook_on(
                    http, dest, channel["name"], look=source in found, listed=listed.get(dest)
                )
                await self._wait(0.25)
            if url:
                pairs.append((source, url))
                reused += int(kept)
                self.note(f"#{channel['name']}")
        if not pairs:
            raise ApiError(400, "no webhook could be created")
        return pairs, reused

    async def _hooks_on(self, http: DiscordHTTP, channel_id: str) -> list[dict[str, Any]] | None:
        """The webhooks Discord lists on a channel; None when it refuses, which says nothing about them."""
        try:
            listed = await http.call("GET", f"/channels/{channel_id}/webhooks")
        except ApiError:
            return None
        return [hook for hook in listed if isinstance(hook, dict)] if isinstance(listed, list) else []

    async def _webhook_on(
        self, http: DiscordHTTP, channel_id: str, name: str, look: bool, listed: list[dict[str, Any]] | None = None
    ) -> tuple[str, bool]:
        """The URL of a webhook on the channel: one this fill made before, when `look` and it is still there
        (same name, token visible; `listed` when the fill already fetched the channel's webhooks, None when it did
        not or its listing failed), otherwise a new one. ("", False) when Discord refuses, and when the webhooks of
        a found channel cannot be listed: it may carry one already, and Mando never deletes the second."""
        wanted = webhook_name(name)
        if look:
            hooks = listed if listed is not None else await self._hooks_on(http, channel_id)
            if hooks is None:
                self.note(f"webhooks of #{name} could not be listed")
                return "", False
            for hook in hooks:
                if hook.get("token") and hook.get("name") == wanted:
                    try:
                        return clean_webhook(f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}"), True
                    except ApiError:
                        continue
        try:
            hook = await http.call("POST", f"/channels/{channel_id}/webhooks", json={"name": wanted})
        except ApiError as exc:
            self.note(f"webhook for #{name} failed ({exc})")
            return "", False
        if not isinstance(hook, dict) or not hook.get("id") or not hook.get("token"):
            self.note(f"webhook for #{name} failed")
            return "", False
        try:
            return clean_webhook(f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}"), False
        except ApiError as exc:
            self.note(f"webhook for #{name} failed ({exc})")
            return "", False
```

Check the expected `self.delays` in the first test against this pacing (`0.3` after the one category, `0.25` after each of two channels and two webhooks) and adjust the test only if the implementation above is what the plan says and the numbers were miscounted here — then record it in `odchylky`.

- [ ] **Step 4: Run the full suite**

Run the full test command. Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mirror/engine.py mirror/store.py mirror/cli/controller.py tests/test_engine.py tests/test_core.py tests/test_cli_controller.py
git commit -m "Fill a server the owner owns with a source's ticked channels, reusing channels and webhooks of the same name and keeping the first source's category names on a refill."
```

---

### Task 5: API — the owned servers and the fill

**Files:**
- Modify: `mirror/web.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Produces: `GET /api/targets` → `{"targets": [...]}` from `engine.owned_guilds()`; `POST /api/guilds/{guild_id}/fill` with JSON `{"target": "<id>"}` → the snapshot plus `"report"`, the report of `engine.fill_copy(guild_id, target)` unchanged (`target`, `filled`, `reused`, `replaced`); `_json` already enforces the JSON content type (415) and an object body (400). Without a token both answer 401 `{"error": "add a token first"}` through `guard`.

- [ ] **Step 1: Write the failing tests**

```python
    async def test_targets_and_fill_need_a_token(self) -> None:
        client = await self.client()
        resp = await client.get("/api/targets")
        self.assertEqual(resp.status, 401)
        self.assertEqual(await resp.json(), {"error": "add a token first"})
        resp = await client.post("/api/guilds/5/fill", json={"target": "900"})
        self.assertEqual(resp.status, 401)
        resp = await client.post("/api/guilds/5/fill", data="x", headers={"Content-Type": "text/plain"})
        self.assertEqual(resp.status, 415)

    async def test_targets_route_lists_the_owned_servers(self) -> None:
        client = await self.client()
        engine = client.server.app["engine"]

        async def owned() -> list[dict]:
            return [{"id": "901", "name": "Alpha"}, {"id": "900", "name": "zeta copy"}]

        engine.owned_guilds = owned
        resp = await client.get("/api/targets")
        self.assertEqual(resp.status, 200)
        self.assertEqual(
            await resp.json(), {"targets": [{"id": "901", "name": "Alpha"}, {"id": "900", "name": "zeta copy"}]}
        )

    async def test_fill_route_calls_the_engine_and_returns_the_report(self) -> None:
        client = await self.client()
        engine = client.server.app["engine"]

        async def fill(source: str, target: str) -> dict:
            return {"target": f"{source}->{target}", "filled": 2, "reused": 0, "replaced": 1}

        engine.fill_copy = fill
        resp = await client.post("/api/guilds/5/fill", json={"target": 900})
        self.assertEqual(resp.status, 200)
        body = await resp.json()
        self.assertEqual(body["report"], {"target": "5->900", "filled": 2, "reused": 0, "replaced": 1})
        self.assertIn("selection", body)
        self.assertEqual(body["targets"], {})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -X utf8 -m unittest tests.test_web -q`
Expected: FAIL — 404 for both routes.

- [ ] **Step 3: Implement**

In `mirror/web.py`:

```python
async def targets(request: web.Request) -> web.Response:
    return web.json_response({"targets": await request.app["engine"].owned_guilds()})


async def fill_copy(request: web.Request) -> web.Response:
    engine: Engine = request.app["engine"]
    body = await _json(request)
    report = await engine.fill_copy(request.match_info["guild_id"], str(body.get("target") or ""))
    out = engine.snapshot()
    out["report"] = report
    return web.json_response(out)
```

Routes, next to `channels`:

```python
    app.router.add_get("/api/targets", targets)
    app.router.add_post("/api/guilds/{guild_id}/fill", fill_copy)
```

- [ ] **Step 4: Run the full suite**

Run the full test command. Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mirror/web.py tests/test_web.py
git commit -m "Add the owned servers and the fill to the API."
```

---

### Task 6: CLI — `c` picks the copy, Enter fills it, unticking keeps it

**Files:**
- Modify: `mirror/cli/controller.py`, `mirror/cli/render.py`
- Test: `tests/test_cli_controller.py`, `tests/test_cli_render.py`

**Interfaces:**
- Produces, on the servers screen at depth `guilds`: `c`/`C` on a server row opens the target picker (depth `targets`): `self.targets` = `engine.owned_guilds()` without the source itself, `self.target_source` = the server, `local_index` on the current target when the snapshot's `targets` links the source, else 0. Errors before opening: `"tick the server or some of its channels first"` when no picked row belongs to the server; `"you own no other server, create one in Discord first"` when the list is empty. At depth `targets`: Up/Down wrap, Enter → `engine.fill_copy(source id, target id)` then `refresh()`, `self.error = "<filled> channel(s) ready in <target>"`, led by `"<r> earlier webhook url(s) replaced, "` when the report's `replaced` is `r > 0` (an own webhook, decision 9g, or an earlier copy's URL, decision 9n; the engine notes the count in its log, which the CLI never shows, measured in the Task 6 review, and reports it; the CLI prints the engine's count and derives none from its rows, which differed from the engine's for a webhook found again under another host; the count leads so a long target name cut at the screen width never cuts it), back to depth `guilds` with the cursor on the source; an `ApiError`/`RuntimeError` keeps the picker open with its text, and every error of the fill reads the snapshot again (`_resync`, as `_save_options` does), because `Engine.fill_copy` can raise after the store holds the copy's URLs and the link (the refresh of a running mirror) and the next save would otherwise put the URLs from before back (measured in the Task 6 review); Esc → depth `guilds`, cursor on the source. Unticking a server (Enter on a ticked server) whose source has a link in the snapshot's `targets` ends with `self.error = "copy in <target_name> kept, delete it in Discord if you do not need it"` (decision 9e). Hints: depth `guilds` `"enter toggles, c fills copy, right opens channels, esc back"` (59 characters); depth `targets` `"enter fills the copy, esc back"`. `_save_options` sends `backfill`, `include_threads`, `mirror`, `channels` only.
- Render: depth `targets`: title `"Copy of <source> into"`, rows `"[x] <name>"` for the linked target and `"[ ] <name>"` otherwise (no row for an empty list: `c` refuses to open the picker without a server the owner owns); depth `guilds`: a linked server row ends with `"  copy: <target_name>"`.
- Consumes: Task 1's snapshot key `targets`, Task 4's `owned_guilds`/`fill_copy`.

- [ ] **Step 1: Write the failing tests**

Update `FakeEngine` in `tests/test_cli_controller.py`: `self.options = {"backfill": 0, "include_threads": False, "mirror": False}`; `self.targets: dict = {}`; `self.owned: list[dict] = []`; `self.replaced: int | None = None` (the count a fill reports, when a test sets one; otherwise the fake counts the rows whose URL it changed); `snapshot()` adds `"targets": dict(self.targets)`; `save_setup` drops the `dest_name` line; delete `reset_destination`; add:

```python
    async def owned_guilds(self) -> list[dict]:
        self.calls.append(("owned_guilds",))
        self._maybe_fail("owned_guilds")
        return list(self.owned)

    async def fill_copy(self, source_id: str, target_id: str) -> dict:
        self.calls.append(("fill_copy", source_id, target_id))
        self._maybe_fail("fill_copy")
        name = next(g["name"] for g in self.owned if g["id"] == target_id)
        self.targets[source_id] = {"target_id": target_id, "target_name": name}
        # as Engine.fill_copy: every ticked row of the source gets the target's webhook, an own one included
        # (decision 9n), and a refill into the same target finds the same webhook again; the report counts the
        # earlier URLs the fill replaced
        replaced = 0
        for row in self.selection:
            if row["guild_id"] == source_id:
                url = f"https://discord.com/api/webhooks/{target_id}{row['channel_id']}/t"
                replaced += int(row.get("webhook_url") not in ("", None, url))
                row["webhook_url"] = url
        # as Engine.fill_copy: the refresh of a running mirror comes after the store holds the URLs and the link
        self._maybe_fail("fill_copy_refresh")
        return {"target": name, "filled": 2, "reused": 0, "replaced": replaced if self.replaced is None else self.replaced}
```

Change the assertions at lines 494–495 (the `_save_options` body) to `self.assertNotIn("global_webhook", engine.calls[-1][1])` and `self.assertNotIn("dest_name", engine.calls[-1][1])`. Two existing tests pin the old guilds hint and take the new one, `"enter toggles, c fills copy, right opens channels, esc back"`: `test_channels_stop_at_the_list_ends_and_escape_returns_to_their_server` in `tests/test_cli_controller.py` and the `hint = ...` line of `test_long_list_keeps_the_cursor_row_in_view` in `tests/test_cli_render.py`. Add to `ServersScreenTests`:

```python
    async def test_c_opens_the_target_picker_and_enter_fills(self) -> None:
        ui, engine = await self.open_servers()
        engine.owned = [{"id": "g1", "name": "Qwen"}, {"id": "t1", "name": "Qwen copy"}, {"id": "t2", "name": "Spare"}]
        await keys(ui, "c")
        self.assertEqual(ui.error, "tick the server or some of its channels first")
        self.assertEqual(ui.depth, "guilds")
        await keys(ui, "Enter", "c")
        self.assertEqual(ui.depth, "targets")
        self.assertEqual([t["id"] for t in ui.targets], ["t1", "t2"])
        self.assertEqual(ui.target_source["id"], "g1")
        self.assertEqual(ui.hint(), "enter fills the copy, esc back")
        await keys(ui, "ArrowDown", "Enter")
        self.assertEqual(engine.calls[-1], ("fill_copy", "g1", "t2"))
        self.assertEqual(ui.depth, "guilds")
        self.assertEqual(ui.local_index, 0)
        self.assertEqual(ui.error, "2 channel(s) ready in Spare")
        self.assertEqual(ui.snap["targets"]["g1"]["target_name"], "Spare")
        self.assertTrue(all(row["webhook_url"] for row in ui.picked.values()))
        self.assertEqual(ui.hint(), "enter toggles, c fills copy, right opens channels, esc back")
        self.assertLessEqual(len(ui.hint()), 60)

    async def test_target_picker_starts_on_the_linked_target_and_escape_returns(self) -> None:
        ui, engine = await self.open_servers()
        engine.owned = [{"id": "t1", "name": "Qwen copy"}, {"id": "t2", "name": "Spare"}]
        engine.targets["g1"] = {"target_id": "t2", "target_name": "Spare"}
        ui.refresh()
        await keys(ui, "Enter", "c")
        self.assertEqual(ui.local_index, 1)
        await keys(ui, "ArrowDown")
        self.assertEqual(ui.local_index, 0)
        await keys(ui, "Escape")
        self.assertEqual(ui.depth, "guilds")
        self.assertEqual(ui.local_index, 0)
        self.assertEqual(ui.error, "")

    async def test_leaving_the_target_picker_returns_to_the_row_of_its_server(self) -> None:
        ui, engine = await self.open_servers()
        engine.channel_lists["g2"] = [{"id": "c3", "name": "lobby", "parent": "", "topic": ""}]
        engine.owned = [{"id": "t1", "name": "Qwen copy"}]
        await keys(ui, "ArrowDown", "Enter", "c")
        self.assertEqual((ui.depth, ui.target_source["id"]), ("targets", "g2"))
        await keys(ui, "Escape")
        self.assertEqual((ui.depth, ui.local_index, ui.target_source), ("guilds", 1, None))
        await keys(ui, "c", "Enter")
        self.assertEqual(engine.calls[-1], ("fill_copy", "g2", "t1"))
        self.assertEqual((ui.depth, ui.local_index, ui.target_source), ("guilds", 1, None))

    async def test_fill_errors_keep_the_picker_open(self) -> None:
        ui, engine = await self.open_servers()
        engine.owned = [{"id": "t1", "name": "Qwen copy"}]
        engine.fail["fill_copy"] = ApiError(400, "no webhook could be created")
        await keys(ui, "Enter", "c", "Enter")
        self.assertEqual(ui.depth, "targets")
        self.assertEqual(ui.error, "no webhook could be created")
        engine.fail["fill_copy"] = aiohttp.ClientError("boom")
        with self.assertLogs("mirror.cli", level="ERROR"):
            await keys(ui, "Enter")
        self.assertEqual(ui.error, "request failed")
        self.assertFalse(ui.busy)

    async def test_a_fill_that_fails_after_its_writes_shows_what_the_store_holds(self) -> None:
        # Engine.fill_copy refreshes a running mirror after the store holds the copy's URLs and the link, and that
        # refresh can raise: the screen must show the stored rows, or the next save puts the old URLs back
        ui, engine = await self.open_servers()
        engine.owned = [{"id": "t1", "name": "Qwen copy"}]
        await keys(ui, "Enter")
        own = "https://discord.com/api/webhooks/111/own"
        next(row for row in engine.selection if row["channel_id"] == "c1")["webhook_url"] = own
        ui.refresh()
        engine.fail["fill_copy_refresh"] = RuntimeError("gateway closed")
        await keys(ui, "c", "Enter")
        self.assertEqual(ui.depth, "targets")
        self.assertEqual(ui.error, "gateway closed")
        self.assertEqual(ui.picked["c1"]["webhook_url"], "https://discord.com/api/webhooks/t1c1/t")
        self.assertEqual(ui.snap["targets"]["g1"]["target_name"], "Qwen copy")
        await keys(ui, "Escape", "ArrowRight", "a")
        self.assertEqual(engine.calls[-1][0], "save_setup")
        stored = {row["channel_id"]: row["webhook_url"] for row in engine.selection}
        self.assertEqual(stored["c1"], "https://discord.com/api/webhooks/t1c1/t")
        await keys(ui, "Escape")
        next(row for row in engine.selection if row["channel_id"] == "c1")["webhook_url"] = own
        ui.refresh()
        engine.fail["fill_copy_refresh"] = sqlite3.OperationalError("database is locked")
        await keys(ui, "c")
        with self.assertLogs("mirror.cli", level="ERROR"):
            await keys(ui, "Enter")
        self.assertEqual(ui.depth, "targets")
        self.assertEqual(ui.error, "request failed")
        self.assertEqual(ui.picked["c1"]["webhook_url"], "https://discord.com/api/webhooks/t1c1/t")
        self.assertFalse(ui.busy)

    async def test_a_fill_that_replaces_earlier_webhook_urls_says_so(self) -> None:
        # the engine notes the count in its log, which the CLI does not show, so the line shows the report's count
        # (gap in 9g and 9n)
        ui, engine = await self.open_servers()
        long_name = "The archive of every message we ever wrote"
        engine.owned = [{"id": "t1", "name": "Qwen copy"}, {"id": "t2", "name": long_name}]
        await keys(ui, "Enter")
        next(row for row in engine.selection if row["channel_id"] == "c1")["webhook_url"] = "https://discord.com/api/webhooks/111/own"
        ui.refresh()
        await keys(ui, "c", "Enter")
        self.assertEqual(engine.calls[-1], ("fill_copy", "g1", "t1"))
        self.assertEqual(ui.error, "1 earlier webhook url(s) replaced, 2 channel(s) ready in Qwen copy")
        await keys(ui, "c", "Enter")
        self.assertEqual(engine.calls[-1], ("fill_copy", "g1", "t1"))
        self.assertEqual(ui.error, "2 channel(s) ready in Qwen copy")
        await keys(ui, "c", "ArrowDown", "Enter")
        self.assertEqual(engine.calls[-1], ("fill_copy", "g1", "t2"))
        self.assertEqual(ui.error, f"2 earlier webhook url(s) replaced, 2 channel(s) ready in {long_name}")
        # the count leads, so a narrow screen that cuts a long server name never cuts it
        self.assertIn("2 earlier webhook url(s) replaced, 2 channel(s) ready in The", render(ui, 60, 10))

    async def test_the_line_after_a_fill_shows_the_count_the_engine_reports(self) -> None:
        # the engine compares webhook ids, not URLs: a row whose own webhook it found again under the discord.com host
        # changes its URL and is not replaced, and the CLI never counts on its own
        ui, engine = await self.open_servers()
        engine.owned = [{"id": "t1", "name": "Qwen copy"}]
        await keys(ui, "Enter")
        next(row for row in engine.selection if row["channel_id"] == "c1")["webhook_url"] = "https://ptb.discord.com/api/webhooks/111/own"
        ui.refresh()
        engine.replaced = 0
        await keys(ui, "c", "Enter")
        self.assertEqual(ui.error, "2 channel(s) ready in Qwen copy")
        engine.replaced = 3
        await keys(ui, "c", "Enter")
        self.assertEqual(ui.error, "3 earlier webhook url(s) replaced, 2 channel(s) ready in Qwen copy")

    async def test_no_owned_server_and_a_failed_list_show_errors(self) -> None:
        ui, engine = await self.open_servers()
        engine.owned = [{"id": "g1", "name": "Qwen"}]
        await keys(ui, "Enter", "c")
        self.assertEqual(ui.depth, "guilds")
        self.assertEqual(ui.error, "you own no other server, create one in Discord first")
        engine.fail["owned_guilds"] = ApiError(401, "add a token first")
        await keys(ui, "c")
        self.assertEqual(ui.error, "add a token first")
        self.assertEqual(ui.depth, "guilds")

    async def test_unticking_a_server_with_a_copy_says_the_copy_is_kept(self) -> None:
        ui, engine = await self.open_servers()
        engine.targets["g1"] = {"target_id": "t2", "target_name": "Spare"}
        ui.refresh()
        await keys(ui, "Enter")
        self.assertEqual(ui.error, "")
        await keys(ui, "Enter")
        self.assertEqual(ui.picked, {})
        self.assertEqual(ui.error, "copy in Spare kept, delete it in Discord if you do not need it")
        self.assertEqual(engine.calls[-1][0], "save_setup")
```

In `tests/test_cli_render.py`:

```python
    def test_target_picker_and_the_copy_mark(self) -> None:
        ui = ui_on("servers", targets={"g1": {"target_id": "t2", "target_name": "Spare"}})
        ui.guilds = [{"id": "g1", "name": "Qwen", "icon": ""}, {"id": "g2", "name": "Moody", "icon": ""}]
        ui.picked = {"c1": {"channel_id": "c1", "guild_id": "g1", "webhook_url": ""}}
        lines = render(ui, 60, 8)
        self.assertEqual(lines[1], "Select servers")
        self.assertEqual(lines[2], "> [x] Qwen  copy: Spare")
        self.assertEqual(lines[3], "  [ ] Moody")
        ui.depth = "targets"
        ui.target_source = ui.guilds[0]
        ui.targets = [{"id": "t1", "name": "Qwen copy"}, {"id": "t2", "name": "Spare"}]
        ui.local_index = 1
        lines = render(ui, 60, 8)
        self.assertEqual(lines[1], "Copy of Qwen into")
        self.assertEqual(lines[2], "  [ ] Qwen copy")
        self.assertEqual(lines[3], "> [x] Spare")
        self.assertEqual(lines[-1], "enter fills the copy, esc back")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -X utf8 -m unittest tests.test_cli_controller tests.test_cli_render -q`
Expected: FAIL — `c` does nothing (`depth` stays `guilds`, `error` empty); the render test lacks the copy mark.

- [ ] **Step 3: Implement**

`mirror/cli/controller.py`:

- `__init__`: `self.targets: list[dict[str, Any]] = []`, `self.target_source: dict[str, Any] | None = None`.
- `hint()`: before the `screen == "servers"` lines add `if screen == "servers" and self.depth == "targets": return "enter fills the copy, esc back"`; change the guilds hint to `"enter toggles, c fills copy, right opens channels, esc back"`.
- `_save_options`: body is `{"backfill": ..., "include_threads": ..., "mirror": ..., "channels": list(self.picked.values())}`.
- `_server_key`: at the top, after the `channels` branch:

```python
        if self.depth == "targets":
            await self._target_key(key)
            return
```

and in the guilds branch: `elif key in ("c", "C"): await self._open_targets(self.guilds[self.local_index])`. The Escape of the `channels` branch becomes `self._back_to_guilds(self.active_guild["id"] if self.active_guild else None)`, the helper `_leave_targets` uses too (added after the gate: both restored the same row with the same lines).

- New methods:

```python
    # ---- the copy of a source server: a server the owner owns, filled by Mando (decisions 9b', 9m, 9e) ----

    def _target_of(self, guild_id: str) -> dict[str, Any] | None:
        return (self.snap.get("targets") or {}).get(guild_id)

    async def _open_targets(self, guild: dict[str, Any]) -> None:
        self.error = ""
        if not any(row.get("guild_id") == guild["id"] for row in self.picked.values()):
            self.error = "tick the server or some of its channels first"
            return
        self.busy = True
        try:
            owned = [g for g in await self.engine.owned_guilds() if g["id"] != guild["id"]]
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
            return
        except Exception:
            log.exception("owned server list failed")
            self.error = UNEXPECTED
            return
        finally:
            self.busy = False
        if not owned:
            self.error = "you own no other server, create one in Discord first"
            return
        self.targets = owned
        self.target_source = guild
        self.depth = "targets"
        link = self._target_of(guild["id"]) or {}
        self.local_index = max(0, next((at for at, g in enumerate(owned) if g["id"] == link.get("target_id")), 0))

    async def _target_key(self, key: str) -> None:
        if key == "Escape":
            self._leave_targets()
            return
        if not self.targets:
            return
        if key == "ArrowDown":
            self.local_index = (self.local_index + 1) % len(self.targets)
        elif key == "ArrowUp":
            self.local_index = (self.local_index - 1 + len(self.targets)) % len(self.targets)
        elif key == "Enter":
            await self._fill(self.targets[self.local_index])

    def _leave_targets(self) -> None:
        source = self.target_source["id"] if self.target_source else None
        self.target_source = None
        self._back_to_guilds(source)

    def _back_to_guilds(self, guild_id: str | None) -> None:
        """Back to the server list, on the row of the server just left (the first row when it is not listed)."""
        self.depth = "guilds"
        self.local_index = max(0, next((at for at, g in enumerate(self.guilds) if g["id"] == guild_id), 0))

    async def _fill(self, target: dict[str, Any]) -> None:
        source = self.target_source or {}
        self.busy = True
        try:
            report = await self.engine.fill_copy(str(source.get("id") or ""), target["id"])
            self.refresh()
        except (ApiError, RuntimeError) as exc:
            self.error = str(exc)
            # Engine.fill_copy can raise after the store holds the copy's URLs and the link (the refresh of a running
            # mirror): the screen takes the stored rows, or the next save would put the URLs from before back
            self._resync()
            return
        except Exception:
            log.exception("fill failed")
            self.error = UNEXPECTED
            self._resync()
            return
        finally:
            self.busy = False
        self._leave_targets()
        text = f"{report.get('filled', 0)} channel(s) ready in {report.get('target') or target.get('name')}"
        # a fill gives every ticked channel of the source the copy's webhook, an own one (decision 9g) or an earlier
        # copy's URL included (decision 9n); the engine notes the count in its log, which the CLI does not show, and
        # reports it, so the line shows the engine's count. The count leads, so a narrow screen that cuts a long
        # server name never cuts it
        replaced = int(report.get("replaced") or 0)
        self.error = f"{replaced} earlier webhook url(s) replaced, {text}" if replaced else text
```

- `_toggle_guild`, untick branch: after `await self._save_options()`, add

```python
            link = self._target_of(guild["id"])
            if link and not self.error:
                self.error = f"copy in {link.get('target_name') or 'the copy'} kept, delete it in Discord if you do not need it"
```

`mirror/cli/render.py`, in the `servers` branch before the `else` for the guild list:

```python
        elif ui.depth == "targets" and ui.target_source:
            title = f"Copy of {ui.target_source.get('name') or 'server'} into"
            link = (ui.snap.get("targets") or {}).get(ui.target_source.get("id")) or {}
            for at, target in enumerate(ui.targets):
                mark = "[x] " if target.get("id") == link.get("target_id") else "[ ] "
                rows.append(_row(f"{mark}{target.get('name')}", at == ui.local_index))
```

and in the guild list loop:

```python
            links = ui.snap.get("targets") or {}
            for at, guild in enumerate(ui.guilds):
                mark = "[x] " if guild.get("id") in selected else "[ ] "
                label = f"{mark}{guild.get('name')}"
                link = links.get(guild.get("id"))
                if link:
                    label += f"  copy: {link.get('target_name') or link.get('target_id')}"
                rows.append(_row(label, at == ui.local_index))
```

- [ ] **Step 4: Run the full suite**

Run the full test command. Expected: OK. Also `grep -rn --include=*.py "dest_name\|global_webhook\|reset_destination" mirror/ tests/test_cli_controller.py tests/test_cli_render.py` prints only the `mirror/store.py` comment on the old options columns (Task 1, decision 9h) and the two `assertNotIn` lines of Step 1.

- [ ] **Step 5: Commit**

```bash
git add mirror/cli/controller.py mirror/cli/render.py tests/test_cli_controller.py tests/test_cli_render.py docs/superpowers/plans/2026-10-09-own-copies.md
git commit -m "Pick the copy of a source server with c and fill it from the CLI."
```

---

### Task 7: Relay — files 1:1 up to 20 MiB each, a larger file as one line with two links

**Files:**
- Modify: `mirror/relay.py`, `mirror/engine.py` (`guild_id` into the view)
- Test: `tests/test_relay.py`, `tests/test_engine.py`

**Interfaces:**
- Produces: `UPLOAD_LIMIT = 20 * 1024 * 1024` (per file, decision 10b); `UPLOAD_FILES = 10` kept; `plan_uploads(attachments) -> (upload, linked)` takes a file when its host is a CDN host, `0 < size <= UPLOAD_LIMIT` and fewer than `UPLOAD_FILES` are taken — no total cap (a 413 still falls back to links as today); `Relay._files` drops its total cap too. `oversize_lines(view) -> list[str]`: for each attachment with `size > UPLOAD_LIMIT`, three lines: `"This message has a file over the upload limit: <name> (<size>)"`, `"https://discord.com/channels/<guild_id or @me>/<channel_id>/<id>"`, `<url>`; `<size>` is `f"{size / 1048576:.1f} MiB"`. `payload_for` puts those lines at the head of the block `fit_content` keeps (after the content, the stickers line and "(deleted)", before the links), so a long message is clipped instead of the file, and the links block (`link_urls`) leaves the oversize files out. `view_from_message(message, channel_name, guild_name, guild_id="")` adds `"guild_id": str(message.get("guild_id") or guild_id)` to the view; the engine passes the row's guild id (`self.guild_of[channel_id]`, filled in `_index` from `row["guild_id"]`, threads mapped to their parent's).

- [ ] **Step 1: Write the failing tests**

In `tests/test_relay.py` replace `test_plan_uploads_limits` and add two tests; `make_view` gets `"guild_id": "9"`:

```python
    def test_plan_uploads_limits(self) -> None:
        many = [attachment(f"f{i}.png", 1_000) for i in range(12)]
        upload, linked = plan_uploads(many)
        self.assertEqual(upload, many[:10])
        self.assertEqual(linked, many[10:])
        big = attachment("big.bin", UPLOAD_LIMIT + 1)
        off = attachment("x.png", 10, "https://example.com/x.png")
        zero = attachment("zero.png", 0)
        upload, linked = plan_uploads([big, off, zero])
        self.assertEqual(upload, [])
        self.assertEqual(linked, [big, off, zero])
        six = [attachment(f"s{i}.bin", UPLOAD_LIMIT) for i in range(6)]
        upload, linked = plan_uploads(six)
        self.assertEqual(upload, six)
        self.assertEqual(linked, [])
        self.assertEqual(UPLOAD_LIMIT, 20 * 1024 * 1024)

    def test_oversize_file_becomes_a_line_with_two_links(self) -> None:
        big = attachment("movie.mp4", 25 * 1024 * 1024 + 1)
        off = attachment("x.png", 10, "https://example.com/x.png")
        view = make_view([big, off])
        body = payload_for(view, False)
        lines = body["content"].split("\n")
        self.assertEqual(lines[0], "hello")
        self.assertEqual(lines[1], "This message has a file over the upload limit: movie.mp4 (25.0 MiB)")
        self.assertEqual(lines[2], "https://discord.com/channels/9/2/1")
        self.assertEqual(lines[3], big["url"])
        self.assertEqual(lines[4], off["url"])
        self.assertEqual(len(lines), 5)
        view["guild_id"] = ""
        self.assertIn("https://discord.com/channels/@me/2/1", payload_for(view, False)["content"])

    async def test_oversize_file_is_never_downloaded(self) -> None:
        big = attachment("movie.mp4", UPLOAD_LIMIT + 1)
        self.session.queue.append(FakeResp(200, {"id": "5"}))
        sent = await self.relay.create(HOOK, make_view([big]), False)
        self.assertEqual(sent, "5")
        self.assertEqual([c for c in self.session.calls if c[0] == "get"], [])
        method, url, kwargs = self.sends()[0]
        self.assertIn("json", kwargs)
        self.assertIn("over the upload limit: movie.mp4", kwargs["json"]["content"])
```

In `tests/test_engine.py` add:

```python
    def test_views_carry_the_guild_id(self) -> None:
        self.engine._index([row("10", HOOK)], False)
        view = engine_mod.view_from_message({"id": "1", "channel_id": "10", "author": {"username": "a"}}, "general", "Desk", self.engine.guild_of.get("10", ""))
        self.assertEqual(view["guild_id"], "5")
        view = engine_mod.view_from_message({"id": "1", "channel_id": "10", "guild_id": "77", "author": {"username": "a"}}, "general", "Desk", "5")
        self.assertEqual(view["guild_id"], "77")
```

The implementation added four more tests: in `tests/test_relay.py` `test_oversize_line_survives_a_long_message` (a 3000-character message with a file over the limit keeps the over-limit line and both links, measured lost when the lines sat in the clipped text), `test_files_have_no_total_cap` (two files of `UPLOAD_LIMIT // 2 + 1` bytes both upload) and `test_too_large_fallback_keeps_the_oversize_file_out_of_the_links` (after a 413 the over-limit file is one line and two links, never a third link); in `tests/test_engine.py` `test_relayed_views_carry_the_guild_id_of_their_row` (`_create` in a channel, in a thread from `_load_threads` and in one from `THREAD_CREATE`, and `_restore_view` all carry the row's guild id when the message has none, as REST history messages do).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -X utf8 -m unittest tests.test_relay tests.test_engine -q`
Expected: FAIL — `UPLOAD_LIMIT` is 10_000_000, no oversize line, `guild_of` missing.

- [ ] **Step 3: Implement**

`mirror/relay.py`:

```python
UPLOAD_FILES = 10
UPLOAD_LIMIT = 20 * 1024 * 1024  # per file, the documented default (decision 10b)
OVER_LIMIT = "This message has a file over the upload limit"
```

```python
def plan_uploads(attachments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    upload: list[dict[str, Any]] = []
    linked: list[dict[str, Any]] = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        size = size_of(item)
        if host_of(str(item.get("url") or "")) in CDN_HOSTS and 0 < size <= UPLOAD_LIMIT and len(upload) < UPLOAD_FILES:
            upload.append(item)
        else:
            linked.append(item)
    return upload, linked


def oversize(item: dict[str, Any]) -> bool:
    return size_of(item) > UPLOAD_LIMIT


def message_link(view: dict[str, Any]) -> str:
    guild = str(view.get("guild_id") or "") or "@me"
    return f"https://discord.com/channels/{guild}/{view.get('channel_id')}/{view.get('id')}"


def oversize_lines(view: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in view.get("attachments") or []:
        if isinstance(item, dict) and oversize(item):
            size = f"{size_of(item) / 1048576:.1f} MiB"
            lines.append(f"{OVER_LIMIT}: {item.get('name') or 'file'} ({size})")
            lines.append(message_link(view))
            if item.get("url"):
                lines.append(str(item["url"]))
    return lines


def link_urls(view: dict[str, Any]) -> list[str]:
    links = view.get("links")
    if not isinstance(links, list):
        links = [item.get("url") for item in plan_uploads(view.get("attachments") or [])[1] if not oversize(item)]
    return [str(url) for url in links if url]
```

In `payload_for`: `content = fit_content(text, oversize_lines(view) + link_urls(view))`. (The first version of this plan put the lines into the text after the content line; measured with a 3000-character message, `fit_content` clipped the text to 2000 and the over-limit line and both links were gone, while a linked file kept its link. In the kept block `fit_content` drops from the end, so the expiring download link goes before the message link and the line.) In `Relay._post`, the fallback link lists must leave oversize items out: `links = [str(item.get("url")) for item in attachments if id(item) not in sent and item.get("url") and not oversize(item)]` and the same filter in the `too_large` branch. In `_files`, drop `total` and its check. In `view_from_message(message, channel_name, guild_name, guild_id="")` add `"guild_id": str(message.get("guild_id") or guild_id or "")`.

`mirror/engine.py`: `self.guild_of: dict[str, str] = {}` in `__init__`; in `_index`: `self.guild_of = {}` beside `self.names = {}` and `self.guild_of[row["channel_id"]] = str(row.get("guild_id") or "")`; in `_load_threads` and the `THREAD_CREATE` branch: `self.guild_of[thread_id] = self.guild_of.get(parent, "")`; the two `view_from_message(...)` calls in `_create` and `_restore_view` pass `self.guild_of.get(channel_id, "")`.

- [ ] **Step 4: Run the full suite**

Run the full test command. Expected: OK (`test_files_reject_bad_downloads` still passes: its "huge" body is `UPLOAD_LIMIT + 1` bytes, now 20 MiB — keep it, it runs in well under a second).

- [ ] **Step 5: Commit**

```bash
git add mirror/relay.py mirror/engine.py tests/test_relay.py tests/test_engine.py
git commit -m "Upload files up to 20 MiB each and replace a larger file with one line and two links."
```

---

### Task 8: Relay — stickers as images, the embeds key on every edit, the 6000-character embed total

**Files:**
- Modify: `mirror/relay.py`
- Test: `tests/test_relay.py`

**Interfaces:**
- Produces: `sticker_files(message) -> list[dict]`: for each `sticker_items` entry with `format_type` 1 or 2 an attachment `{"name": "<name>.png", "url": "https://cdn.discordapp.com/stickers/<id>.png", "content_type": "image/png", "size": 0, "sticker": True}`, for 4 `{"name": "<name>.gif", "url": "https://media.discordapp.net/stickers/<id>.gif", "content_type": "image/gif", "size": 0, "sticker": True}`; `sticker_names(message)` returns only the names of format 3 (Lottie) stickers (decision 10a). `view_from_message` appends the sticker files to `attachments`. `plan_uploads` takes a sticker item with `size == 0` (its size is unknown; `_fetch` enforces the limit on the body). `payload_for(view, prefix, keep_embeds: bool = False)` keeps the `embeds` key, `[]` when the message has none, when `keep_embeds` is true, and its content fallback (`(empty)`, `(attachment)`) still follows whether the message has embeds; `Relay.edit` builds its body with `keep_embeds=True`, so an edit always sends `"embeds"` (issue #6). `safe_embeds` stops adding embeds once the total of title, description, field names and values, footer text and author name would pass 6000 characters (`EMBED_TOTAL = 6000`). Decision 10c (the dot) and 10d (prefix only on a shared URL, pinned in Task 3) need no change.

- [ ] **Step 1: Write the failing tests**

```python
    def test_stickers_become_image_uploads_and_lottie_stays_a_name(self) -> None:
        message = {
            "id": "1", "channel_id": "2", "author": {"username": "a"},
            "sticker_items": [
                {"id": "100", "name": "wave", "format_type": 1},
                {"id": "101", "name": "spin", "format_type": 2},
                {"id": "102", "name": "dance", "format_type": 4},
                {"id": "103", "name": "vector", "format_type": 3},
            ],
        }
        view = view_from_message(message, "general", "Desk")
        self.assertEqual(view["stickers"], ["vector"])
        self.assertEqual(
            [(a["name"], a["url"], a["content_type"]) for a in view["attachments"]],
            [
                ("wave.png", "https://cdn.discordapp.com/stickers/100.png", "image/png"),
                ("spin.png", "https://cdn.discordapp.com/stickers/101.png", "image/png"),
                ("dance.gif", "https://media.discordapp.net/stickers/102.gif", "image/gif"),
            ],
        )
        upload, linked = plan_uploads(view["attachments"])
        self.assertEqual(len(upload), 3)
        self.assertEqual(linked, [])
        self.assertEqual(payload_for(view, False)["content"], "stickers: vector")

    async def test_sticker_and_oversize_file_in_one_post(self) -> None:
        big = attachment("movie.mp4", UPLOAD_LIMIT + 1)
        sticker = {"name": "wave.png", "url": "https://cdn.discordapp.com/stickers/100.png", "content_type": "image/png", "size": 0, "sticker": True}
        self.session.downloads[sticker["url"]] = FakeResp(200, body=b"\x89PNG")
        view = make_view([big, sticker])
        self.session.queue.append(FakeResp(200, {"id": "5"}))
        sent = await self.relay.create(HOOK, view, False)
        self.assertEqual(sent, "5")
        self.assertEqual(len(self.sends()), 1)
        method, url, kwargs = self.sends()[0]
        self.assertEqual(form_files(kwargs["data"]), [b"\x89PNG"])
        content = form_payload(kwargs["data"])["content"]
        self.assertIn("over the upload limit: movie.mp4", content)
        self.assertNotIn("wave.png", content)
        self.assertEqual(view["links"], [])

    async def test_edit_always_sends_the_embeds_key(self) -> None:
        self.session.queue.append(FakeResp(200, {"id": "5"}))
        await self.relay.edit(HOOK, "5", make_view([]), False)
        method, url, kwargs = self.sends()[0]
        self.assertEqual(method, "patch")
        self.assertEqual(kwargs["json"]["embeds"], [])
        self.assertNotIn("username", kwargs["json"])

    async def test_edit_that_removes_every_embed_keeps_the_content_fallback(self) -> None:
        # the body an edit sent before payload_for kept the embeds key itself (issue #6, decision 10e)
        for files, fallback in (([], "(empty)"), ([attachment("a.png", 10)], "(attachment)")):
            self.session.queue.append(FakeResp(200, {"id": "5"}))
            await self.relay.edit(HOOK, "5", make_view(files, content=""), False)
            self.assertEqual(
                self.sends()[-1][2]["json"],
                {"content": fallback, "allowed_mentions": {"parse": []}, "embeds": []},
            )

    def test_payload_keeps_an_empty_embeds_key_only_when_asked(self) -> None:
        view = make_view([], content="")
        self.assertEqual(payload_for(view, False, keep_embeds=True)["embeds"], [])
        self.assertEqual(payload_for(view, False, keep_embeds=True)["content"], "(empty)")
        self.assertNotIn("embeds", payload_for(view, False))
        self.assertIsNone(payload_for(view | {"embeds": [{"title": "t"}]}, False, keep_embeds=True)["content"])

    def test_embeds_stay_under_the_total(self) -> None:
        from mirror.relay import safe_embeds
        embeds = [{"title": "t", "description": "d" * 4000}, {"description": "e" * 1990}, {"description": "f" * 20}, {"title": "g"}]
        kept = safe_embeds({"embeds": embeds})
        self.assertEqual(len(kept), 2)
        self.assertEqual(kept[1]["description"], "e" * 1990)
        many = [{"title": f"t{i}", "fields": [{"name": "n" * 256, "value": "v" * 1024}]} for i in range(10)]
        kept = safe_embeds({"embeds": many})
        self.assertLessEqual(sum(len(e["title"]) + 256 + 1024 for e in kept), 6000)
        self.assertEqual(len(kept), 4)
```

Add `view_from_message` to the `from mirror.relay import (...)` list.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONUTF8=1 python -X utf8 -m unittest tests.test_relay -q`
Expected: FAIL — `view["stickers"]` lists all four names and `attachments` is empty; the edit body has no `embeds`; `safe_embeds` keeps every embed up to ten. (The two tests after `test_edit_always_sends_the_embeds_key` were added after the gate, when the rule moved into `payload_for`; measured on e519eab, whose `edit` set the key on the body `payload_for` returned: `test_payload_keeps_an_empty_embeds_key_only_when_asked` fails with `TypeError: payload_for() got an unexpected keyword argument 'keep_embeds'`, and `test_edit_that_removes_every_embed_keeps_the_content_fallback` passes, since it pins the body that `edit` sent.)

- [ ] **Step 3: Implement**

`mirror/relay.py`:

```python
EMBED_TOTAL = 6000
STICKER_PNG = 1
STICKER_APNG = 2
STICKER_LOTTIE = 3
STICKER_GIF = 4


def sticker_names(message: dict[str, Any]) -> list[str]:
    """Stickers that have no image to send: Lottie (decision 10a)."""
    names = []
    for sticker in message.get("sticker_items") or []:
        if isinstance(sticker, dict) and sticker.get("name") and sticker.get("format_type") == STICKER_LOTTIE:
            names.append(str(sticker["name"])[:64])
    return names


def sticker_files(message: dict[str, Any]) -> list[dict[str, Any]]:
    """PNG, APNG and GIF stickers as image files from the CDN, uploaded like attachments (decision 10a)."""
    files: list[dict[str, Any]] = []
    for sticker in message.get("sticker_items") or []:
        if not isinstance(sticker, dict) or not sticker.get("id"):
            continue
        kind = sticker.get("format_type")
        name = str(sticker.get("name") or "sticker")[:64]
        if kind in (STICKER_PNG, STICKER_APNG):
            url, ext, mime = f"https://cdn.discordapp.com/stickers/{sticker['id']}.png", "png", "image/png"
        elif kind == STICKER_GIF:
            url, ext, mime = f"https://media.discordapp.net/stickers/{sticker['id']}.gif", "gif", "image/gif"
        else:
            continue
        files.append({"name": f"{name}.{ext}", "url": url, "content_type": mime, "size": 0, "sticker": True})
    return files
```

In `view_from_message`, after the attachments loop: `attachments.extend(sticker_files(message))`. In `plan_uploads`, the size condition becomes `(0 < size <= UPLOAD_LIMIT or (size == 0 and item.get("sticker")))`. In `oversize`, a sticker is never oversize (`size_of` is 0). `payload_for` gets `keep_embeds: bool = False`: it pops an empty `embeds` key only when `keep_embeds` is false, and its content fallback asks whether the body has embeds (`not body.get("embeds")`), not whether it has the key, so a kept empty key leaves the fallback as it was; `Relay.edit` builds its body with `payload_for(view, prefix, keep_embeds=True)` (the key is always present on an edit, issue #6). In `safe_embeds`: keep a running `total`; compute `used = embed_chars(item)` before appending and `if total + used > EMBED_TOTAL: break` where

```python
def embed_chars(item: dict[str, Any]) -> int:
    count = len(item.get("title") or "") + len(item.get("description") or "")
    for field in item.get("fields") or []:
        count += len(field.get("name") or "") + len(field.get("value") or "")
    footer = item.get("footer") or {}
    author = item.get("author") or {}
    return count + len(str(footer.get("text") or "")) + len(str(author.get("name") or ""))
```

- [ ] **Step 4: Run the full suite**

Run the full test command. Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add mirror/relay.py tests/test_relay.py
git commit -m "Send PNG, APNG and GIF stickers as images, always send the embeds key on an edit and keep embeds under 6000 characters."
```

---

### Task 9: Docs — README, glossary, design notes

**Files:**
- Modify: `README.md`, `CONTEXT.md`, `docs/superpowers/specs/2026-10-08-terminal-cli-design.md`

**Interfaces:** none (text only). Every sentence below is checked against the code of Tasks 1–8 before it is written; a sentence the code does not do is a finding, not a doc fix.

- [ ] **Step 1: README "First run"**

Lead the list with how to start Mando, then replace steps 3 and 4 and the paragraph after the list with the text below. (The Task 9 implementation measured three sentences of the first draft against the code and corrected them: a single Enter in the channel list ticks a channel only together with a URL, since Esc or an empty Enter unticks it again, so "tick a few" goes through `a` and unticking; the relay posts to an own webhook in any server, so Mando does not "only write" to owned servers; and the relay deletes a mirrored message whose source was deleted, so Mando does not "never delete anything" in the copy.)

```markdown
Start Mando as described under Start (`sh start.sh`, or `start.bat` on Windows), then:

3. In Discord, create a server for the copy, unless you already own one to use. Mando creates channels and webhooks only in servers your account owns. To use your own webhooks instead, create a webhook in each channel the messages should go to and copy its URL; it can be in any server, also one the account is not in.
4. Select servers. Enter on a source server ticks all its listed channels. To tick only a few, open its channels with the right arrow, press `a` to tick them all, then untick the others: Enter on a channel that shows `no webhook`, then Enter on its empty webhook row (Enter alone unticks one that shows `webhook set`). Back in the server list (Esc), `c` on the server lists the servers you own: Enter on one fills it with the ticked channels, their categories and one webhook per channel. For your own webhooks instead, Enter on a channel that is not ticked, or that shows `no webhook`, opens its webhook row: paste the URL and press Enter.
5. Webhook settings: pick backfill and threads.
6. Back to the menu and Start/Resume mirror.

The ticked source channels are the selection, kept in `state.db` across restarts. Each one is mirrored to its webhook, its target. Mando never creates a server, and in yours it never deletes a category, channel or webhook. A fill adds what is missing and reuses what it finds: a channel of the same name in the same category (or loose, for a loose one), and the webhook named after the channel on it. A channel of the same name elsewhere is reused too while no other source shares the server, or when it carries that channel's webhook. Several sources can share one server; a source filled into a server that already holds another source gets its own categories, named `source / category`, and a category named `source` for its loose channels. A fill gives each ticked channel of the source that it fills the copy's webhook, also one that had your own URL; the line after the fill then starts with `<n> earlier webhook url(s) replaced`. Unticking a server stops mirroring it and keeps the copy. Start refuses while a ticked channel has no webhook: it shows `#channel has no webhook` and opens Select servers at that channel.
```

- [ ] **Step 2: README "Menu"**

In the Select servers paragraph, add after the first sentence: "`c` on a server lists the servers you own, with the one that is this server's copy marked; Enter fills it and the server row then shows `copy: <name>`, the line under the list `<n> channel(s) ready in <name>`. Esc goes back without a fill. `c` needs some of the server's channels ticked (`tick the server or some of its channels first`) and a server you own besides this one (`you own no other server, create one in Discord first`). A fill while the mirror runs takes effect without a restart." After the server Enter sentence add: "unticking a server with a copy shows `copy in <name> kept, delete it in Discord if you do not need it`". Replace the sentence about `a` and the server Enter with: "Enter on a source server and `a` in the channel list tick channels without opening their rows; a channel that shows `no webhook` gets its URL with Enter or through a fill."

- [ ] **Step 3: README "What gets mirrored"**

Replace the attachments bullet with: "- Up to 10 files per message are uploaded 1:1, each up to 20 MiB; sticker images count among the 10. A larger file is replaced by the line `This message has a file over the upload limit: <name> (<size> MiB)`, a link to the original message and the file's download link. A file past the tenth, a file that cannot be downloaded, and every file of an upload Discord refuses as too large are posted as links." Add: "- PNG, APNG and GIF stickers are posted as images; a Lottie sticker is named on a `stickers: <name>` line." Replace the retry bullet with "- Rate limits, Discord server errors and network errors are waited out and the request is sent again, up to five attempts in all." (`relay.ATTEMPTS` is 5 in all, a 429 included, and a 4xx other than 429 is not sent again). Replace the shared-webhook bullet with: "- When two selected channels share one webhook URL, each message starts with a `server / #channel` line." Add: "- An edit always sends the embeds the message has left, so an edit that removes every embed removes them in the mirrored message too. At most 10 embeds are posted, and an embed that would take their text past Discord's 6000-character total is left out, with the ones after it." (`safe_embeds` stops at the first embed over the total.)

- [ ] **Step 4: CONTEXT.md**

Replace the **Copy** entry with:

```markdown
**Copy**:
A Discord server the owner owns and picks for one ticked source server; a fill adds the
source's ticked channels, their categories and one webhook per channel to it and never
deletes anything in it. Several sources may share one copy.
_Avoid_: mirror server, destination server, dest

**Fill**:
Mando creating, inside a copy, the categories, channels and webhooks that the ticked
channels of one source still lack there, reusing what is already there; each ticked
channel the fill gives a webhook in the copy then posts there.
_Avoid_: provision, sync, deploy
```

and add to the **Own webhook** entry: "A fill of its source server replaces it with the copy's webhook."

- [ ] **Step 5: Design notes**

Under decision 9b' add one line: "Implemented in plan B (`docs/superpowers/plans/2026-10-09-own-copies.md`): `Engine.fill_copy`, the `c` key (9m)." Under 10a add: "Implemented in Task 8 of `docs/superpowers/plans/2026-10-09-own-copies.md` (plans B and C); the real-webhook check is the owner's manual step at the end of that plan." (Decision 10a belongs to plan C, which this file carries with plan B.)

- [ ] **Step 6: Run the full suite and the attribution scan, then commit**

Run the full test command (OK expected) and `git grep -niwE 'claude|anthropic' -- ':!CLAUDE.md'` (only `.gitignore:.claude/` may match).

Measured in the Task 9 implementation: at `d99fc17` the scan lists 9 lines, the `.gitignore` line, the plans' references to the root instructions file and the command line above; none is an attribution. The check is therefore that the diff adds no line the scan matches (the same pattern over the added lines of `git diff -U0` is empty).

```bash
git add README.md CONTEXT.md docs/superpowers/specs/2026-10-08-terminal-cli-design.md docs/superpowers/plans/2026-10-09-own-copies.md
git commit -m "Describe the fill, the c key and the relay limits in the README and the glossary, and mark decisions 9b' and 10a implemented."
```

---

## Manual check by the owner (attended, after the gate)

Not a task for an agent: it writes to the owner's Discord account.

1. `start.bat`, sign in, tick a small source server, `c`, pick the test server `1557894816161992704`, Enter. Expect the categories, channels and webhooks in that server, the row `copy: <name>`, and no deleted channel there.
2. `c` again on the same server, Enter on the same target. Expect the CLI line "n channel(s) ready in <name>", the engine's note "webhooks on n channel(s) in <name>, n reused" in the `log` list of `http://127.0.0.1:8765/api/state` (`Engine.note` keeps its notes in memory and never writes them to `data/mando.log`), and no second channel or webhook.
3. Start. Post in a source channel a PNG sticker, a GIF sticker and a file over 20 MiB. Expect the stickers as images and the file as one line with two links in the copy. Edit a source message to remove an embed; expect the mirrored embed gone.
4. Post one message with three files of about 9 MiB each in a source channel and check whether the copy holds the three files or three links. Each file is under the 20 MiB per-file limit and no total is capped (decision 10b), so the relay sends all three in one upload request. When Discord answers that request with HTTP 413 or an error body with code 40005 (`too_large` in `mirror/relay.py`), `Relay._post` posts the message again with the link of every file of the message that is not over 20 MiB and logs `webhook upload too large, sending links` at INFO in `data/mando.log` (a file whose download failed is posted as a link too, with no log line). Three links with that line mean that Discord's total for one webhook request is lower than 3 x 9 MiB; the owner then decides whether a total cap per message comes back.
5. Decide the gap after decision 9o in the design notes: a fill gives a ticked channel with an own webhook the copy's webhook (the CLI line after the fill: "1 earlier webhook url(s) replaced, n channel(s) ready in <name>"). Keep it, or ask for own webhooks to survive a fill, which needs a mark on each URL a fill made.
