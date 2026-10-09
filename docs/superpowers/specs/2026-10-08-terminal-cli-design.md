# Terminal CLI and token input: decisions so far

The spec synthesised from these decisions is GitHub issue #9 (`ready-for-agent`).
Glossary: `CONTEXT.md`. Decision record: `docs/adr/0001-copies-live-in-servers-the-owner-creates.md`.

Brainstorming record, 2026-10-08. Every decision below was made by the owner;
facts carry the file and line they were measured at (base `main` @ `0416376`).

## Starting point (measured)

- The browser page in `static/` is a terminal-styled menu (`index.html` has one
  `#screen`; `app.js` holds the token, webhooks and servers screens and a live feed
  over `/api/feed` + SSE `/api/events`).
- The terminal that starts the app only shows aiohttp access logs and is never
  cleared (`mirror/__main__.py` runs `web.run_app` with default logging).
- `start.bat:63` opens the browser after the server is up.
- The whole project is already in English. Only Unicode test fixtures are not
  (`tests/test_store.py`, `tests/test_provision.py`, `tests/test_keychain.py`).

## Decisions

1. **The terminal CLI has the same menu as today's browser page.** Start/Resume,
   Settings (Add token, Select servers, Webhook settings), Exit, plus one status
   line. The screen is redrawn and cleared, and logs go to a file in `DATA_DIR`,
   not to the terminal.
2. **The app always starts in the CLI.** Settings get an item
   "Interface: UI (unavailable now)" that cannot be chosen. Storing the choice and
   opening the browser by themselves come later, together with the UI.
3. **The CLI is built on `prompt_toolkit`.** `prompt_toolkit` 3.0.53 ships a
   `py3-none-any` wheel (measured with `pip download`), so it works on Windows x86
   / Python 3.10, which CI covers. It is added to `requirements.txt`.
4. **The browser UI is not built now.**
5. **The HTTP server keeps only `/api/*`.**
   - `/` answers "UI unavailable now".
   - Remove `static/`, `tests/test_flow.mjs`, `tests/e2e/`, the e2e CI job and
     `package.json`. Remove the browser opening from `start.bat`.
   - The CLI calls the engine in-process.
   - `tests/test_web.py` and the Windows smoke step (they use `/api/state` and
     `/api/stop`) stay valid.
6. **The CLI main screen shows the menu, a status line and a live feed.**
   - The status line shows running/stopped, the count of mirrored messages and the
     last error.
   - The feed shows the last N messages, as `server / #channel · author: text`,
     with edited/deleted marks. N follows the window height.
   - The engine already emits these events through `_emit`.
7. **Everything user-facing and in the repo is English.**
8. **The token fix ships first, separately.** It is done: the plan is in
   `docs/superpowers/plans/2026-10-08-token-input.md` and the commits run from
   `24a28b2` to `aa6f8b3`.
   - **8b:** validity is checked right after Enter in the token field. The CLI
     shows `✓ token works – signed in as <name>` / `✗ token rejected by Discord` /
     `✗ could not reach Discord (<reason>)`.
   - `Engine.check_token` returns works / rejected / unreachable and stores nothing.
   - The browser page does not get this report, because it is being removed.

## Token bug (measured, fixed in 8)

| Input path in the old page | Token that reached the server |
|---|---|
| open row, type or paste, Enter, save | intact, including `_` and `-` |
| open row, paste, **Esc**, save | **empty** |
| paste **without opening** the row, save | **empty** |
| `"token"` with quotes | **quotes kept** |

The owner's screenshot shows `/api/session 400 221`. That is the same response
size as "paste a token or use the keychain fields" in a local run, which means
the server received an empty token. Underscores were never the cause. Whether
Discord accepts a given token cannot be measured from the cloud container, because
Discord answers it with 403.

## Decision 9: server selection (decided 2026-10-09)

Facts measured on `feat/terminal-cli` @ `b213deb` before deciding:

- `copy_guild` (`mirror/engine.py:282-357`) names the new server after the source
  (`:316`), creates it at once (`POST /guilds`, `:324`) and starts the mirror.
- `copy_guild` calls `store.replace_selection(rows)` (`:344`) and the store keeps a
  single `dest_guild_id` (`mirror/store.py:204`), so copying a second server wipes
  the first copy's selection.
- Rows without a `webhook_url` are provisioned into the shared `dest_name` server
  at start (`_start` -> `_provision`, `mirror/engine.py:480-481, 512`).
- `global_webhook` is dead: rows without a webhook are provisioned first
  (`:480-482`) and start clears the option while `mirror` is false (`:486-487`).
- A per-channel `webhook_url` is stored (`save_setup`, `:448`) but no screen lets
  anyone enter it.

Decisions (owner):

- **9a.** The shared "mirror" server goes away: `dest_name`, `_provision`,
  `destination_layout` and the "server name" item in webhook settings are removed.
- **9b.** Every ticked source server gets its own copy; several copies can run at
  once. `dest_guild_id` becomes a per-source-server link, not a global option.
- **9c.** Enter on a server asks for the copy's name (prefilled with the source
  name) and creates the server right away, as `copy_guild` does today.
- **9d.** `global_webhook` is removed. A single ticked channel must have its own
  webhook URL.
- **9e.** When a server is unticked, Mando stops mirroring it and keeps the copy;
  the CLI says "copy <name> kept, delete it in Discord if you do not need it".
  Delete Guild is not in the current Discord docs, so Mando does not delete.
- **9f.** A server with only some channels ticked has no copy; each ticked
  channel needs a webhook URL, otherwise Start refuses with "#x has no webhook"
  and the CLI jumps to that row.
- **9g.** In the channel list, Enter ticks a channel and opens the webhook URL row
  at once; Esc without a URL unticks it again. The URL is checked on entry with
  the existing `clean_webhook`.
- **9g'.** (2026-10-09) 9g and the CLI side of 9f ship in plan A (`docs/superpowers/plans/2026-10-09-cli-shell.md`, Task 6b), because an own webhook is independent of the reading account and the engine already mirrors when every ticked row has a URL. Plan A's Webhook settings screen keeps only backfill, threads and back. Plan B moves the Start rule into the engine.
- **9h.** Upgrade keeps the selection. The old `dest_name` and `dest_guild_id`
  columns are ignored, channels without a webhook follow 9f, and the old shared
  server on Discord is left alone (as in 9e).

**Measured 2026-10-09: Mando cannot create servers.** The Discord changelog of
2025-04-15, "Deprecating Guild Creation by Apps", retired `POST /guilds` for
applications from 2025-07-15; `developers/resources/guild.mdx` on `main` has no
Create Guild or Delete Guild section and `guild-template.mdx` has no Create Guild
From Guild Template section. A one-off probe from the owner's machine with the
owner's user token (`POST /guilds` with `{"name": "mando-probe-<time>"}`, the same
headers as `DiscordHTTP`) answered **403 `{"code": 10008, "message": "Unknown
Message"}`**. `copy_guild` (`mirror/engine.py:324`) would fail the same way.
Still documented and used by `_wire_copy`: Create Guild Channel
(`guild.mdx:893`) and Create Webhook (`webhook.mdx`). Decisions 9b and 9c are
therefore reopened and replaced (owner, 2026-10-09):

- **9b'.** A copy is a server the owner creates and names in Discord. Enter on a
  ticked source server lists the servers the owner **owns** (`owner` flag from
  `/users/@me/guilds`); the owner picks the target and Mando creates the readable
  categories, channels and webhooks of the source inside it (Create Guild Channel,
  Create Webhook). 9c (name prompt in the CLI) is dropped.
- **9i.** In the owner's server Mando only adds, never deletes: the leftover
  cleanup in `_wire_copy` (`mirror/engine.py:418-426`) goes. A channel with the
  same name as the source channel is reused and only gets a webhook.
- **9j.** Several source servers may share one target server. When a target is
  shared, each source gets its own category named after the source.
- **Measured 2026-10-09 on a fresh server the owner created in Discord:**
  `/users/@me/guilds` returns `owner: true` for it (and `false` for the 27 others);
  `POST /guilds/{id}/channels` type 4 -> 201, type 0 with `parent_id` and `topic`
  -> 201, `POST /channels/{id}/webhooks` -> 200 with a token, `DELETE /webhooks/{id}`
  -> 204, `DELETE /channels/{id}` -> 200 twice. The server's own channels were
  untouched. 9b' stands on measured calls.

## Decision 10: "identical format" (decided 2026-10-09)

Facts from `discord/discord-api-docs` (`main`, read 2026-10-09; file:line in the
`developers/` tree):

- Execute Webhook has no `message_reference` and no `sticker_ids`: a webhook
  message cannot be a real reply and cannot carry a sticker
  (`resources/webhook.mdx`, params table).
- Webhook embeds are always `rich`; `type`, `provider`, `video` and image
  `height`/`width`/`proxy_url` cannot be set (`resources/webhook.mdx:259`).
- At most 10 embeds, and at most 6000 characters across all embeds, else 400
  (`resources/message.mdx:621`).
- Upload limit: 20 MiB per file by default, higher with Nitro or server boost
  (`reference.mdx:438`). The number of files per webhook request and a total
  request size for webhooks are not documented; 25 MiB is stated for Create
  Message only.
- Attachment CDN URLs are signed and expire (`reference.mdx:413-431`); the
  lifetime is not stated. Sticker images are on unsigned CDN paths:
  `stickers/<id>.png` for PNG/APNG, `media.discordapp.net/stickers/<id>.gif` for
  GIF; Lottie has no image (`reference.mdx`, image formatting table).
- `clyde`/`discord` are forbidden in a webhook's name at creation
  (`resources/webhook.mdx:127`); for the per-message `username` override nothing
  is documented.
- Edit Webhook Message: "All parameters to this endpoint are optional and
  nullable" (`resources/webhook.mdx:315`); what an omitted `embeds` key does is
  not documented.

Today's relay (`mirror/relay.py`) measured against that:

- Reply as a text line (`relay.py:72-87`): forced, no webhook reply exists.
- Embed filter and clipping (`relay.py:90-130`): forced by the rich-only rule and
  the limits; the 6000 total is missing (issue #8).
- Stickers as names only (`relay.py:132-137`), attachments over 10 files / 10 MB
  as links (`relay.py:18-19, 187-207`), the name dot (`relay.py:27-43`) and the
  "server / #channel" prefix (`relay.py:230-235`): see decisions below.

Decisions (owner):

- **10a.** Stickers: PNG, APNG and GIF are sent as images from the CDN, Lottie
  keeps the name. This is tested on a real webhook before anyone else uses it.
- **10b.** Files are re-uploaded 1:1 up to the documented 20 MiB per file (the
  10 MB threshold goes up). A file over the limit is replaced by one line:
  "This message has a file over the upload limit: <name> (<size>)", followed by a
  link to the original message (`https://discord.com/channels/<guild>/<channel>/<message>`,
  permanent) and the attachment's download link (signed, expires).
- **10c.** The dot in author names containing "discord" or "clyde" stays. It is
  the safe choice while the per-message rule is undocumented.
- **10d.** The "server / #channel" prefix stays and applies only when two channels
  share the same webhook URL.

## Engine risks found while comparing with stegripe/discord-chat-mirror (filed 2026-10-09)

The comparison was made against stegripe commit `ba0a5f9`. stegripe only mirrors
new messages; it has no edits, deletes, backfill, threads, retries or reconnect
handler. It has four things we lack:

- posting under the webhook's own name and avatar;
- a `[BOT]`/`[USER]` suffix;
- sticker images;
- one channel fanned out to many webhooks.

Our risks, filed as GitHub issues with line numbers measured on `b213deb`:

- **R1 (#4).** `on_dispatch` is awaited inside the gateway read loop
  (`gateway.py:294`). A long relay wait can miss the heartbeat ack, and the
  heartbeat check (`gateway.py:222-225`) then drops the socket. The code path is
  verified; the behaviour is inference.
- **R2 (#5).** A failed webhook delete still drops the relay row (`engine.py:802-803`).
- **R3 (#6).** An edit that removes all embeds sends no `embeds` key
  (`relay.py:254-255`), so old embeds probably stay. The Discord side is inference.
- **R4 (#7).** `THREAD_CREATE` does not resubscribe (`engine.py:719-726`).
- **R5 (#8).** A live message is dropped after 5 failed attempts. The 6000-character
  embed total is not enforced. The `relayed` table is never pruned.

## Small points from the token fix (closed)

- **Hint text.** The owner chose "enter or esc keeps it". It is shown only while
  the token field is open; the keychain fields keep "enter keeps it, esc cancels
  the edit" because Esc still cancels there. Fixed test-first in `ccadd83`.
- **Commit authors.** Rewritten to
  `Romciiito <80603298+Romciiito@users.noreply.github.com>` with a force-push.
  The branch is now `feat/terminal-cli`.

## Gate review of the token fix — NEEDS_FIXES

Reviewed `c9b48a6..aa6f8b3` (cloud hashes before the author rewrite: `ede6275..b28cbc1`). Unit 124/124, flow 12/12, e2e 31/31.

- **important:** `static/app.js:93`. While the token field is open, `hint()` still
  says "esc cancels the edit", but `escapeToken` (`app.js:379-382`) commits the
  draft. Clearing the field and pressing Esc wipes a saved token. This was
  reproduced with a Playwright probe. The fix is a token branch in `hint()`;
  the wording is the owner's call. Fixed in `ccadd83`; not re-gated yet.
- **nit:** `Engine.check_token` has no `session is None` guard, unlike
  `use_token`. No caller exists yet.
- **nit:** `check_token` re-raises 4xx errors other than 401/403, including a 429
  that comes after the client's 5 retries.
- **nit:** `test_checked_token_too_short_is_refused_before_discord` does not
  record `session.get()`, so it only partly guards "before Discord".
- **nit:** Ctrl+V with an empty clipboard opens an empty token field.
