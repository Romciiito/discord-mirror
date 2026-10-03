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

Webhook settings names the new server. On start, the same account creates that server, one channel for each channel you selected, and a webhook named the same as the channel. The next start reuses that server. "New server on next start" throws that away.

## Run

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sh start.sh
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765).

Python 3.10 or newer. macOS or Linux.

## Account

Paste a user token, or on macOS use a Keychain service and account:

```sh
security find-generic-password -s SERVICE -a ACCOUNT -w
```

"Keep on this machine" stores the token in `data/state.db` (mode 600). The token is sent only to `discord.com`.

## Careful

This holds a second session open on the account, and creating a server uses that account. Discord can limit or close an account for a second session. It can also ask for a captcha before it will create a server. The page binds to `127.0.0.1` unless you set `HOST`.
