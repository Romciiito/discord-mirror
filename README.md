# Mando

Discord smart scraper.

Mando runs in your terminal. It reads the Discord channels your account can already see and posts their messages again through webhooks: each source channel you tick goes to its webhook, in a server of your choice. New messages, edits, deletes, replies, embeds, stickers and attachments follow as they happen. Backfill can mirror recent history first.

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

Start Mando as described under Start (`sh start.sh`, or `start.bat` on Windows), then:

1. Press any key, open Settings, then Add token.
2. Type or paste the token on the token row and press Enter. The line under the list says whether Discord accepts it. Then pick save token. Or fill in the keychain service and account and pick read keychain.
3. In Discord, create a server for the copy, unless you already own one to use. Mando creates channels and webhooks only in servers your account owns. To use your own webhooks instead, create a webhook in each channel the messages should go to and copy its URL; it can be in any server, also one the account is not in.
4. Select servers. Enter on a source server ticks all its listed channels. To tick only a few, open its channels with the right arrow, press `a` to tick them all, then untick the others: Enter on a channel that shows `no webhook`, then Enter on its empty webhook row (Enter alone unticks one that shows `webhook set`). Back in the server list (Esc), `c` on the server lists the servers you own: Enter on one fills it with the ticked channels, their categories and one webhook per channel. For your own webhooks instead, Enter on a channel that is not ticked, or that shows `no webhook`, opens its webhook row: paste the URL and press Enter.
5. Webhook settings: pick backfill and threads.
6. Back to the menu and Start/Resume mirror.

The ticked source channels are the selection, kept in `state.db` across restarts. Each one is mirrored to its webhook, its target. Mando never creates a server, and in yours it never deletes a category, channel or webhook. A fill adds what is missing and reuses what it finds: a channel of the same name in the same category (or loose, for a loose one), and the webhook named after the channel on it. A channel of the same name elsewhere is reused too while no other source shares the server, or when it carries that channel's webhook. Several sources can share one server; a source filled into a server that already holds another source gets its own categories, named `source / category`, and a category named `source` for its loose channels. A fill gives each ticked channel of the source that it fills the copy's webhook, also one that had your own URL; the line after the fill then starts with `<n> earlier webhook url(s) replaced`. Unticking a server stops mirroring it and keeps the copy. Start refuses while a ticked channel has no webhook: it shows `#channel has no webhook` and opens Select servers at that channel.

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

Select servers lists your source servers and only the text channels the account can open and read. `c` on a server lists the servers you own, with the one that is this server's copy marked; Enter fills it and the server row then shows `copy: <name>`, the line under the list `<n> channel(s) ready in <name>`. Esc goes back without a fill. `c` needs some of the server's channels ticked (`tick the server or some of its channels first`) and a server you own besides this one (`you own no other server, create one in Discord first`). A fill while the mirror runs takes effect without a restart. Enter on a source server ticks all its listed channels, or unticks every channel of that server when any of them is ticked, listed or not; unticking a server with a copy shows `copy in <name> kept, delete it in Discord if you do not need it`. In the channel list, Enter on a channel ticks it and opens its webhook row; Enter keeps the URL once its shape is checked; Discord is asked only when mirroring starts and posts to it. A webhook that no longer exists shows up as `#channel: webhook post failed` on the status line. Enter on an empty row unticks the channel. Esc closes the row and leaves the channel as it was before that Enter, so it unticks only a channel that the Enter just ticked. Enter on a channel with a webhook unticks it. Enter on a source server and `a` in the channel list tick channels without opening their rows; a channel that shows `no webhook` gets its URL with Enter or through a fill.

In Webhook settings, left and right change backfill and threads. Enter or the number steps backfill to the next amount (0, 25, 50, 100, 250, 500 messages per channel) and turns threads on or off.

## What gets mirrored

- Each message is posted under the author's name and avatar. Names that Discord does not allow on webhooks, such as ones containing "discord" or "clyde", get a dot added.
- Edits and deletes follow the mirrored message, also after a restart.
- Up to 10 files per message are uploaded 1:1, each up to 20 MiB; sticker images count among the 10. A larger file is replaced by the line `This message has a file over the upload limit: <name> (<size> MiB)`, a link to the original message and the file's download link. A file past the tenth, a file that cannot be downloaded, and every file of an upload Discord refuses as too large are posted as links.
- PNG, APNG and GIF stickers are posted as images; a Lottie sticker is named on a `stickers: <name>` line.
- Rate limits, Discord server errors and network errors are waited out and the request is sent again, up to five attempts in all.
- With threads on, active threads under a selected channel are mirrored to that channel's webhook.
- When two selected channels share one webhook URL, each message starts with a `server / #channel` line.
- An edit always sends the embeds the message has left, so an edit that removes every embed removes them in the mirrored message too. At most 10 embeds are posted, and an embed that would take their text past Discord's 6000-character total is left out, with the ones after it.
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
