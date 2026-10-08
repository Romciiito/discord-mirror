# Mando

Mando reads messages from Discord servers the owner is a member of and posts them
again, through webhooks, into places the owner controls. This glossary fixes the
words used in the CLI, the docs and the code.

## Language

### Sources

**Source server**:
A Discord server the owner is a member of and reads messages from.
_Avoid_: guild, origin server

**Source channel**:
A text channel in a source server that Mando can read.
_Avoid_: origin channel

**Selection**:
The set of source servers and source channels the owner has ticked for mirroring.
_Avoid_: setup, config

### Targets

**Copy**:
A new Discord server created by Mando for one ticked source server, named by the
owner, with the readable categories and channels of the source.
_Avoid_: mirror server, destination server, dest

**Own webhook**:
A webhook URL the owner supplies for one ticked source channel; messages from that
channel are posted there.
_Avoid_: global webhook, custom webhook

**Target**:
Where a source channel's messages go: a channel in a copy, or an own webhook.
_Avoid_: destination

### Messages

**Mirrored message**:
A message Mando has posted to a target for one source message, and keeps in step
with edits and deletes of the source.
_Avoid_: relayed message, forwarded message

**Backfill**:
The last N messages of each selected channel, mirrored once at start before live
messages.
_Avoid_: history, catch-up

### Interface

**Token**:
The owner's Discord account token, used to read sources and to create copies.
_Avoid_: key, login

**Feed**:
The list of the most recent mirrored messages shown on the main screen.
_Avoid_: log, stream

**Status line**:
The single line on the main screen that shows running or stopped, the count of
mirrored messages and the last error.
_Avoid_: status bar
