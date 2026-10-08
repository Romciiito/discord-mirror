# discord-mirror (Mando)

## Models and workflow

Model per node, the main loop and gates: the global `~/.claude/CLAUDE.md`, section
"Model-per-node allocation (D272, revised D305)". Process: sections "Working mode (D299)"
and "Skill routing (D300)". Nothing here overrides them.

Library graphs for the Workflow tool live in `.claude/workflows/` (copies from
claude-config; the global copies do not resolve by name).

## Checks

- Unit: `python -m unittest tests.test_core tests.test_store tests.test_keychain tests.test_provision tests.test_relay tests.test_gateway tests.test_engine tests.test_web -q`
- Front-end flow: `node --test tests/test_flow.mjs`
- Browser e2e: `npm ci && npm run e2e` (Playwright, Chromium)
- CI: `.github/workflows/test.yml` runs all three on Ubuntu and Windows (x64, x86).

## Agent skills

### Issue tracker

Issues live in GitHub Issues of Romciiito/discord-mirror. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical labels, unchanged (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
