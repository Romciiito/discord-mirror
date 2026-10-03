# Mando

Discord smart scraper.

Open the page. Press any key. The menu is:

1. Start/Resume mirror
2. Settings
3. Exit

Settings:

1. Add token
2. Select servers, once the token works
3. Webhook settings
4. Back to menu

Up and down move. Enter or the number opens the line. Esc goes back.

Select servers only lists channels the account can open and read. Enter ticks a whole server. Right arrow opens its channels.

Webhook settings names the new server. On start, the same account creates that server, one channel for each channel you selected, and a webhook named the same as the channel. The next start reuses that server. "New server on next start" throws that away. Left and right arrows change backfill and threads. Enter, the number, or a click steps backfill to the next amount and turns threads on or off. When typing a value, Enter keeps it and Esc cancels it.

## Run

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sh start.sh
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765).

Python 3.10 or newer. macOS, Linux, or Windows 10 and 11 (32-bit or 64-bit Python).

## Run on Windows

Double-click `start.bat`, or run it from any folder. In PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File start.ps1
```

With PowerShell 7 use `pwsh -File start.ps1`. Both create `.venv`, install the requirements the first time and whenever `requirements.txt` changes, start the server, and open [http://127.0.0.1:8765](http://127.0.0.1:8765). They find Python through the `py -3` launcher, then `python`. `HOST` and `PORT` work the same: `set PORT=9000` before `start.bat`, or `$env:PORT = "9000"` before `start.ps1`.

By hand:

```cmd
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m mirror
```

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

"Keep on this machine" stores the token in `data/state.db`. On macOS and Linux the file is mode 600. On Windows the token inside it is protected with DPAPI for the current Windows user, so it only opens on this machine and account. The token is sent only to `discord.com`.

## Careful

This holds a second session open on the account, and creating a server uses that account. Discord can limit or close an account for a second session. It can also ask for a captcha before it will create a server. The page binds to `127.0.0.1` unless you set `HOST`.
