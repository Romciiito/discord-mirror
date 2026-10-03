# Mirror

A local terminal for Discord channels the account can already read.

Pick a server and it makes a new server with the same name. Only text channels you can open and read are copied. Each new channel gets a webhook named the same as that channel, and new messages are posted through it. The account itself never sends them.

Python 3.10 or newer. macOS or Linux.

## Run

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sh start.sh
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765).

Up and down move. Enter opens. Esc goes back. Left and right change a value.

## Account

Paste a user token, or on macOS fill in a Keychain service and account. That is:

```sh
security find-generic-password -s SERVICE -a ACCOUNT -w
```

"Keep on this machine" stores the token in `data/state.db` (mode 600). Leave it off and the token stays in memory until you quit.

The token is sent only to `discord.com`.

## Copying a server

On **servers**, choose one. **Copy** creates a new server you own:

- same server name
- same channel names, under the same categories
- a webhook on each new channel, named the same as the channel
- channels you cannot see, or cannot read history in, are left out
- voice and other non-text channels are left out
- announcement channels are created as normal text channels

The follow list is replaced with those channels, and the session starts. Backfill and threads are set on the main menu before you copy.

Mentions are not pinged in the copy. Reactions stay on the local transcript.

## Careful

This holds a second session open on the account, and creating a server uses that account. Discord can limit or close an account for a second session. It can also ask for a captcha before it will create a server. Run it for an account you mean to use, on a machine you control. The page binds to `127.0.0.1` unless you set `HOST`.

## In the tree

| Path | What it is |
|---|---|
| `mirror/` | Session, gateway, server copy, webhook |
| `static/` | The terminal page |
| `data/` | Local database. Not in git. |
