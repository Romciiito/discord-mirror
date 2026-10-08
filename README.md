# Mando

Discord smart scraper.

Mando opens a local page that reads the Discord channels your account can already see and copies them into a server of your own through webhooks. New messages, edits, deletes, replies, embeds and attachments follow as they happen. Backfill can copy recent history first.

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

Each script creates `.venv`, installs the requirements the first time and whenever `requirements.txt` changes, and starts the server. On Windows the scripts find Python through the `py -3` launcher, then `python`, and open the page by themselves. Otherwise open [http://127.0.0.1:8765](http://127.0.0.1:8765).

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

## First run

1. Press any key, open Settings, then Add token.
2. Paste the token, or fill in the keychain service and account and pick read keychain.
3. Select servers. Enter ticks a whole server. Right arrow opens its channels, where Enter ticks one channel and `a` ticks them all.
4. Webhook settings: name the mirror server and pick backfill and threads.
5. Back to the menu and Start/Resume mirror.

On start, the same account creates the mirror server, a category for each source server and category, one channel for each channel you selected, and a webhook named after the channel. The next start reuses that server and its categories. Channels ticked while the mirror runs get their channel and webhook on the next Start/Resume, which works while running. "New server on next start" forgets the current mirror server.

## Menu

The menu is:

1. Start/Resume mirror
2. Settings
3. Exit

Settings:

1. Add token
2. Select servers, once the token works
3. Webhook settings
4. Back to menu

Up and down move. Enter, the number, or a click opens the line. Esc goes back. Exit stops the mirror.

Select servers only lists text channels the account can open and read.

In Webhook settings, left and right change backfill and threads. Enter, the number, or a click steps backfill to the next amount (0, 25, 50, 100, 250, 500 messages per channel) and turns threads on or off. When typing a value, Enter keeps it and Esc cancels it.

## What gets copied

- Each message is posted under the author's name and avatar. Names that Discord does not allow on webhooks, such as ones containing "discord" or "clyde", get a dot added.
- Edits and deletes follow the copied message, also after a restart.
- Up to 10 attachments, 10 MB in total, are uploaded. Anything else is posted as a link.
- Rate limits and Discord errors are waited out and the request is sent again, up to five times, instead of being dropped.
- With threads on, active threads under a selected channel are copied into that channel.
- If the connection to Discord drops, Mando resumes the same session and catches up on what it missed.

## Account

Paste a user token, or use a keychain service and account.

On macOS the page reads a Keychain item, the same one this prints:

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
| `HOST` | `127.0.0.1` | Address the page binds to |
| `PORT` | `8765` | Port of the page |
| `DATA_DIR` | `data` next to `mirror/` | Where `state.db` lives |
| `LOG_LEVEL` | `INFO` | Log level |

`export PORT=9000` before `sh start.sh`, `set PORT=9000` before `start.bat`, or `$env:PORT = "9000"` before `start.ps1`.

The page refuses requests sent from other websites. Bound to `127.0.0.1`, it also refuses requests for any other host name.

## Tests

Unit tests:

```sh
python -m unittest tests.test_core tests.test_store tests.test_keychain tests.test_provision tests.test_relay tests.test_gateway tests.test_engine tests.test_web
node --test tests/test_flow.mjs
```

End-to-end tests start the server on a free port with a temporary data directory and drive the page in headless Chromium. They need Node 22:

```sh
npm install
npx playwright install chromium
npm run e2e
```

On Linux, `npx playwright install --with-deps chromium` also installs the system libraries Chromium needs.

CI runs the unit tests on Linux, Windows x64 (Python 3.10 and 3.12) and Windows x86, and the end-to-end tests on Linux and Windows.

## Careful

This holds a second session open on the account, and creating a server uses that account. Discord can limit or close an account for a second session. It can also ask for a captcha before it will create a server. The page binds to `127.0.0.1` unless you set `HOST`; anything else lets other machines on the network use the account through the page.
