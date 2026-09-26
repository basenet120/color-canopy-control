# Status

_Last updated: 2026-09-26 — P0 in progress in the cloud build session._

## ⚠ Blocked: this session can't push
The cloud session's git proxy refuses `basenet120/color-canopy-control` ("not in this session's authorized
repository set"), and no GitHub CLI or repo-attach tool is available here. Everything is committed locally on
stacked `claude/*` branches and will be pushed the moment access exists. **Alon: add the repo to this session's
sources** (claude.ai/code → the session → Sources / re-attach the repo with push access), then say "push".

## PR stack (merge order)
Each PR is a `claude/*` branch stacked on the previous one. Please use **merge commits**, not squash.
1. `claude/p0-contracts` — build plan, decisions log, and the contract modules every P0 agent builds against
   (clock, live gate, token store, redaction, WS tickets + hub, driver interface, rows/compare/slot hash,
   capabilities, locks, telemetry, config + registry overlay, executor/accounts/roles/bench contract types). _Local only._

## Next step
P0 wave 1 (running now): SimDriver, Afero adapter + choke point, executor + tracker, roles/enrollment/WebSocket
routes + shared web assets. Then the services container and wiring, then wave 2: account connect (CLI + page),
bench Gates A–C + hue ramp + clock page + runbook, and `/ops/module` (P0b).

## Verified (and how)
- `make test`: 57 passed (contract tests: the 07 §1 truth table, allowlist intersection, `HUBSPACE_LIVE_UNTIL`,
  account allowlist before any password, the per-request choke-point rule, token store round trip / rotation /
  0600 / environment binding / bad key, redaction, ticket single-use + expiry, Origin check, frame header copy +
  length refusal, full state vectors, compare with missing rows, slot hash, capability detection on the fixture,
  lock exclusivity + idle expiry + allowlist, registry overlay validation + history).
- `make lint`: clean.
- The virtual-time loop runs an hour of timers in milliseconds (`tests/test_contracts.py`).

## UNVERIFIED / needs real hardware
Everything in `docs/context/04` bench gates. Nothing has touched Afero from this session (it can't reach it, by design).

## Decisions needed
See `docs/DECISIONS.md` (D1–D9) for calls made; `docs/context/06-open-questions.md` for dated decisions
(vignette surface by Oct 1, Golden Hour technique and kiosk look by Oct 5).
