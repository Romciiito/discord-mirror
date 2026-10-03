# Mirror

A local page for Discord channels the account can already read.

Messages, edits, deletes, files, and embeds show up as they happen. If you want the same stream in another channel, give it an incoming webhook. The account itself never sends, reacts, or edits.

Python 3.10 or newer. macOS or Linux.

## Run

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sh start.sh
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765).

`start.sh` makes the virtualenv on the first run. Leave the terminal open. The page binds to `127.0.0.1` unless you set `HOST`.

## Account

Paste a user token, or on macOS fill in a Keychain service and account. That is:

```sh
security find-generic-password -s SERVICE -a ACCOUNT -w
```

"Keep on this machine" stores the token in `data/state.db` (mode 600). Uncheck it and the token stays in memory until you quit.

The token is sent only to `discord.com`.

## What to follow

Pick a server, then the channels. **All text** checks the text and announcement channels in that server. **Backfill** pulls the last N messages once, then the session stays live. Threads stay out unless you turn them on.

## Copying into another channel

In the destination channel: Edit Channel → Integrations → Webhooks → New Webhook → Copy Webhook URL.

Turn on **Also post into Discord** and paste that URL. One URL can take every checked channel. A channel can override it with its own URL.

Mentions are not pinged in the destination. Reactions stay on the local page.

## Careful

This holds a second session open on the account. Discord can limit or close an account for that. Run it for an account you mean to use, on a machine you control.

## In the tree

| Path | What it is |
|---|---|
| `mirror/` | Session, gateway, and webhook copy |
| `static/` | The page |
| `data/` | Local database. Not in git. |
