---
status: accepted
date: 2026-10-09
---

# Copies live in servers the owner creates; Mando never creates or deletes a server

Mando used to create one shared "mirror" server itself (`POST /guilds`) and provision a
category per source server inside it. On 2026-10-09 the owner's user token got
`403 {"code": 10008}` from `POST /guilds`, and the Discord developer docs no longer
document Create Guild, Create Guild From Template or Delete Guild (the changelog of
2025-04-15 retired guild creation for apps). Creating a category, a channel and a webhook
inside a server the owner owns was measured to work.

We decided that a copy is a server the owner creates and names in Discord; Mando lists the
servers the owner owns, fills the chosen one with the readable categories, channels and
webhooks of the source, only ever adds to it, and never deletes a server or a channel it
did not create. The shared mirror server, `dest_name`, `_provision` and the single
`dest_guild_id` are removed; the link is per source server.

## Consequences

- One manual step for the owner per copy (create the server in Discord).
- Several source servers may share one target; a shared target gets a category per source.
- Unticking a server keeps its copy; the CLI says so and the owner deletes it in Discord.
