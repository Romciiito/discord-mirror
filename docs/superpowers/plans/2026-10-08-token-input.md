# Token input fix

Goal: a pasted token reaches Discord exactly once and unchanged, apart from
surrounding whitespace and one pair of wrapping quotes.

## Measured cause (2026-10-08)

- `engine.use_token` only strips whitespace (`mirror/engine.py:140`), so `"token"`
  is sent to Discord with its quotes.
- In the page, Esc in the token field drops the draft, and typing or pasting while
  the token row is not open goes nowhere. Both end in `POST /api/session` with an
  empty token: `400 paste a token or use the keychain fields`.
- Underscores and dashes reach the server unchanged (Playwright run).

## Seams (agreed)

1. `clean_token(raw)` in `mirror/discord_api.py`: strips whitespace and one pair of
   wrapping `"…"`, `'…'` or `“…”`; leaves `_`, `-`, `.` alone.
2. `Engine.use_token` (tests in `tests/test_engine.py`): the cleaned token is the one
   used for `/users/@me` and stored.
3. Browser e2e (`tests/e2e/test_token.mjs`): Esc in the token field keeps a pasted
   token; typing or pasting on the closed token row opens it and keeps the text.

## Tasks

1. Red/green for seam 1, then wire it into `use_token` (seam 2).
2. Red/green for seam 3 in `static/app.js`.
3. Run the unit suite, `node --test tests/test_flow.mjs` and `npm run e2e`.
