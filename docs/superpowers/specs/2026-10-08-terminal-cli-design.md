# Terminal CLI and token input: decisions so far

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
   `164b830` to `b28cbc1`.
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

## Open: owner requirement for server selection (decision 9 pending)

The owner's words, translated:

- In Select servers, **Enter ticks a whole server** and the **right arrow
  opens it** to tick single channels. This works today; the CLI keeps it.
- **A ticked whole server** offers to create a new server under a name the
  owner chooses. It spawns the original's categories and channels, readable ones
  only.
  - This already exists as `Engine.copy_guild` (`mirror/engine.py:259-334`,
    `POST /api/guilds/{id}/copy`).
  - The new server's name is taken from the source and cannot be chosen.
  - The old page never offered it.
- **A single ticked channel** asks for a webhook URL of the owner's own channel.
  Messages are posted there in the same format as the original: embeds, visuals
  and images.
  - The backend stores a per-channel `webhook_url` (`mirror/engine.py:425`).
  - The old page never let anyone enter it.
- **Today's default is different.** Everything selected goes into ONE shared
  mirror server named `dest_name` (default "mirror"), with a category per source
  server (`Engine._provision`, `mirror/engine.py:489`).
- **Still to decide:** does the shared-mirror mode go away? And what does a server
  with only some channels ticked do?

## Open: "identical format" (decision 10 pending)

Today's relay changes messages in these ways (`mirror/relay.py`):

- A reply becomes a text line "replying to X: …" (`relay.py:72-87`).
- Stickers are sent as names only (`relay.py:132-137`).
- More than 10 attachments, or more than 10 MB in total, go out as links
  (`relay.py:17-19`).
- Embeds are filtered by type and clipped (`relay.py:90-129`).
- Author names containing "discord" or "clyde" get a dot added
  (`relay.py:27-43`).
- When several channels share one webhook, a "server / #channel" prefix is added
  (`relay.py:230-235`).

What Discord webhooks allow to change here is **not verified yet**.

## Open: engine risks found while comparing with stegripe/discord-chat-mirror

The comparison was made against stegripe commit `ba0a5f9`. stegripe only mirrors
new messages; it has no edits, deletes, backfill, threads, retries or reconnect
handler. It has four things we lack:

- posting under the webhook's own name and avatar;
- a `[BOT]`/`[USER]` suffix;
- sticker images;
- one channel fanned out to many webhooks.

Our risks (the proposal is separate issues; the owner has not decided yet):

- **R1.** `on_dispatch` is awaited inside the gateway read loop
  (`gateway.py:294`). A long relay wait can miss the heartbeat ack, and the
  heartbeat check (`gateway.py:222-225`) then drops the socket. The code path is
  verified; the behaviour is inference.
- **R2.** A failed webhook delete still drops the relay row (`engine.py:802-803`).
- **R3.** An edit that removes all embeds sends no `embeds` key
  (`relay.py:254-255`), so old embeds probably stay. The Discord side is inference.
- **R4.** `THREAD_CREATE` does not resubscribe (`engine.py:719-726`).
- **R5.** A live message is dropped after 5 failed attempts. The 6000-character
  embed total is not enforced. The `relayed` table is never pruned.

## Small open points from the token fix

- **Hint text.** While the token is being edited, the hint still says "enter
  keeps it, esc cancels the edit". The proposal is "enter or esc keeps it".
- **Commit authors.** `3bf1a4b`, `bdaae78` and `ede6275` carry the wrong author.
  The owner approved rewriting them to
  `Romciiito <80603298+Romciiito@users.noreply.github.com>` with a force-push.

## Gate review of the token fix — NEEDS_FIXES

Reviewed `ede6275..b28cbc1`. Unit 124/124, flow 12/12, e2e 31/31.

- **important:** `static/app.js:93`. While the token field is open, `hint()` still
  says "esc cancels the edit", but `escapeToken` (`app.js:379-382`) commits the
  draft. Clearing the field and pressing Esc wipes a saved token. This was
  reproduced with a Playwright probe. The fix is a token branch in `hint()`;
  the wording is the owner's call.
- **nit:** `Engine.check_token` has no `session is None` guard, unlike
  `use_token`. No caller exists yet.
- **nit:** `check_token` re-raises 4xx errors other than 401/403, including a 429
  that comes after the client's 5 retries.
- **nit:** `test_checked_token_too_short_is_refused_before_discord` does not
  record `session.get()`, so it only partly guards "before Discord".
- **nit:** Ctrl+V with an empty clipboard opens an empty token field.
