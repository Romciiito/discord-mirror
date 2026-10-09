# Mando

Discord smart scraper.

Mando runs in your terminal. It reads the Discord channels your account can already see and posts their messages again through webhooks: each source channel you tick goes to its own webhook, in a server of your choice. New messages, edits, deletes, replies, embeds and attachments follow as they happen. Backfill can mirror recent history first.

Python 3.10 or newer. macOS, Linux, or Windows 10 and 11 (32-bit or 64-bit Python).

## Start

macOS and Linux:

```sh
sh start.sh
```

Windows: double-click `start.bat`. It does not need PowerShell. If Windows shows "Windows protected your PC", choose More info, then Run anyway.

`start.ps1` does the same from PowerShell. Windows blocks scripts that came from a downloaded ZIP, so unblock the folder once:

```powershell
Get-ChildItem -Recurse | Unblock-File
```

Then run `pwsh -File start.ps1`, or `powershell -ExecutionPolicy Bypass -File start.ps1` without unblocking.

Each script creates `.venv`, installs the requirements the first time and whenever `requirements.txt` changes, and starts Mando in the same terminal. On Windows the scripts find Python through the `py -3` launcher, then `python`.

By hand:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m mirror
```

```cmd
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m mirror
```

Mando shows the CLI when its input and output are a terminal (on Windows, a console). Otherwise, for example with its output redirected to a file, only the API runs.

## First run

1. Press any key, open Settings, then Add token.
2. Type or paste the token on the token row and press Enter. The line under the list says whether Discord accepts it. Then pick save token. Or fill in the keychain service and account and pick read keychain.
3. In Discord, create a webhook in each channel the messages should go to and copy its URL. It can be in any server, also one the account is not in.
4. Select servers. Right arrow opens a source server's channels. Enter on a source channel ticks it and opens its webhook row: paste the webhook URL and press Enter.
5. Webhook settings: pick backfill and threads.
6. Back to the menu and Start/Resume mirror.

The ticked source channels are the selection, kept in `state.db` across restarts. Each one is mirrored to its own webhook, its target. The CLI does not create servers, channels or webhooks: create them in Discord and give every ticked source channel its own webhook. Start refuses while a ticked channel has no webhook: it shows `#channel has no webhook` and opens Select servers at that channel.

## Menu

The top line of every screen is the status line: the account that is signed in (or "signed out"), running or stopped, the count of mirrored messages and the last error ("-" when there is none). The bottom line lists the keys that work on the screen.

The menu is:

1. Start/Resume mirror
2. Settings
3. Exit

Settings:

1. Add token
2. Select servers, once the token works
3. Webhook settings
4. Interface: UI (unavailable now), which cannot be chosen: the browser UI is not available yet
5. Back to menu

Up and down move. Enter or the number opens the line. Esc goes back. Ctrl+C or Ctrl+Q closes Mando.

Start/Resume mirror shows the feed: the latest messages of the selected source channels, one per line as `server / #channel · author: text`, marked `(edited)` or `(deleted)`, as many as fit the window. Esc returns to the menu and the mirror keeps running. Exit stops the mirror and shows Stopped; any key returns to the menu.

In Add token, typing or pasting on the token row opens it. Enter or Esc keeps what you typed and asks Discord at once; the line under the list then shows `✓ token works – signed in as <name>`, `✗ token rejected by Discord` or `✗ could not reach Discord (<reason>)`, or another `✗` line when the token cannot be checked, such as one that looks too short. The check only asks; save token signs in with the token. In the keychain fields Enter keeps the value and Esc cancels the edit.

Select servers lists your source servers and only the text channels the account can open and read. Enter on a source server ticks all its channels, or unticks them all when one of them is ticked. In the channel list, Enter on a channel ticks it and opens its webhook row; Enter keeps the URL, which is checked at once. Enter on an empty row unticks the channel. Esc closes the row and leaves the channel as it was before that Enter, so it unticks only a channel that the Enter just ticked. Enter on a channel with a webhook unticks it. Enter on a source server and `a` in the channel list tick channels without opening their rows; a channel that shows `no webhook` gets its URL with Enter.

In Webhook settings, left and right change backfill and threads. Enter or the number steps backfill to the next amount (0, 25, 50, 100, 250, 500 messages per channel) and turns threads on or off.

## What gets mirrored

- Each message is posted under the author's name and avatar. Names that Discord does not allow on webhooks, such as ones containing "discord" or "clyde", get a dot added.
- Edits and deletes follow the mirrored message, also after a restart.
- Up to 10 attachments, 10 MB in total, are uploaded. Anything else is posted as a link.
- Rate limits and Discord errors are waited out and the request is sent again, up to five times, instead of being dropped.
- With threads on, active threads under a selected channel are mirrored to that channel's webhook.
- When two selected channels share one webhook, each message starts with a `server / #channel` line.
- If the connection to Discord drops, Mando resumes the same session and catches up on what it missed.

## Account

Paste a user token, or use a keychain service and account.

On macOS Mando reads a Keychain item, the same one this prints:

```sh
security find-generic-password -s SERVICE -a ACCOUNT -w
```

On Windows it reads a generic credential from Credential Manager. Create it with:

```cmd
cmdkey /generic:SERVICE /user:ACCOUNT /pass:TOKEN
```

"Keychain service" is `SERVICE`, the target name. "Keychain account" is `ACCOUNT`, its user name; case does not matter. `cmdkey /list` shows it and `cmdkey /delete:SERVICE` removes it. A token typed on the command line lands in the console history.

On Linux there is no keychain. Paste the token.

"Keep on this machine" stores the token in `data/state.db`. On macOS and Linux the file is mode 600. On Windows the token inside it is protected with DPAPI for the current Windows user, so it only opens on this machine and account. A saved token is only forgotten when Discord rejects it; if Discord cannot be reached at start, Mando keeps it and tries again. The token is sent only to `discord.com`.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `HOST` | `127.0.0.1` | Address of the API the CLI also exposes |
| `PORT` | `8765` | Port of the API |
| `DATA_DIR` | `data` next to `mirror/` | Where `state.db` and the log file `mando.log` live |
| `LOG_LEVEL` | `INFO` | Level of `mando.log` |

`export PORT=9000` before `sh start.sh`, `set PORT=9000` before `start.bat`, or `$env:PORT = "9000"` before `start.ps1`.

Logs go to `mando.log`, never to the terminal. The file is rotated at 1 MB and three old files are kept.

The API answers under `/api/`; `/` only says "UI unavailable now". It refuses requests sent from other websites. Bound to `127.0.0.1`, it also refuses requests for any other host name.

## Tests

Unit tests:

```sh
python -m unittest tests.test_core tests.test_store tests.test_keychain tests.test_provision tests.test_relay tests.test_gateway tests.test_engine tests.test_web tests.test_cli_flow tests.test_cli_controller tests.test_cli_render
```

They come in three groups:

- mirroring: `tests.test_core`, `tests.test_store`, `tests.test_keychain`, `tests.test_provision`, `tests.test_relay`, `tests.test_gateway`, `tests.test_engine`
- API: `tests.test_web`
- CLI: `tests.test_cli_flow`, `tests.test_cli_controller`, `tests.test_cli_render`

CI runs the unit tests on Linux (Python 3.12), Windows x64 (Python 3.10 and 3.12) and Windows x86 (Python 3.12). On Linux and Windows it also starts `python -m mirror` without a terminal and checks that the API answers and refuses a request from another website.

## Careful

This holds a second session open on the account. Discord can limit or close an account for a second session. The API binds to `127.0.0.1` unless you set `HOST`; anything else lets other machines on the network use the account through the API.
