# discord-mirror (Mando)

## Models and workflow

Model per node, the main loop and gates: the global `~/.claude/CLAUDE.md`, section
"Model-per-node allocation (D272, revised D305)". Process: sections "Working mode (D299)"
and "Skill routing (D300)". Nothing here overrides them.

The Workflow tool reads library graphs from `.claude/workflows/` on disk; the folder is
ignored by git, so copy the graphs there locally (the global copies do not resolve by name).

## Checks

- Unit: `python -m unittest tests.test_core tests.test_store tests.test_keychain tests.test_provision tests.test_relay tests.test_gateway tests.test_engine tests.test_web tests.test_cli_flow tests.test_cli_controller tests.test_cli_render -q`
- CI: `.github/workflows/test.yml` runs the unit tests on Ubuntu and Windows (x64, x86) and, on every leg, the smoke step (`python -m mirror` without a terminal serves `/api/state` and `/api/stop`).

## Agent skills

### Issue tracker

Issues live in GitHub Issues of Romciiito/discord-mirror. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical labels, unchanged (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
