# Status

_Last updated: 2026-09-26 (evening) — P0 and P0b built, reviewed and fixed on the simulator. P1–P3 next._

## ⚠ Blocked: this session can't push
The cloud session's git proxy refuses `basenet120/color-canopy-control` ("not in this session's authorized
repository set"), and no GitHub CLI or repo-attach tool is available here. Everything is committed locally on the
stacked branches below and will be pushed (and the PRs opened) the moment access exists. **Alon: add the repo to
this session's sources with push access** (claude.ai/code → the session → Sources), then say "push".

## PR stack (merge order — please use merge commits, not squash)
1. `claude/p0-contracts` — build plan, decisions log, and the contract modules (clock, live gate, token store,
   redaction, WS tickets + hub, driver interface, rows/compare/slot hash, capabilities, locks, telemetry, config +
   registry overlay, executor/accounts/roles/bench contract types).
2. `claude/p0-gate-drivers` — SimDriver (pinned fault model), Afero adapter + the choke point (`GatedClientSession`),
   executor + desired/reported tracker, roles/enrollment/cookie principals, `/ws/live` with tickets, services
   container + app wiring, writer-path allowlist test, colour recipes.
3. `claude/p0-bench` — account connect (CLI `python -m conductor.auth login` + hardened `/ops/accounts`), devices API,
   ops landing page with tester enrollment, bench Gates A–C + index order + hue ramp + latency trials + whole-strand +
   restore on `/ops/bench` and `python -m conductor.bench`, `/ops/bench/clock`, anonymizer,
   `docs/runbooks/bench-prototype-01.md`; also the six modes as data (`config/modes.yaml`) and the engine contract.
4. `claude/p0b-module` — `/ops/module` (the single-module test interface) with Playwright e2e, shutdown hygiene, and
   the fixes for all 26 findings of the P0 adversarial review (D15–D20).

## Next step
**Alon at the bench (once the stack is pushed and merged):** follow `docs/runbooks/bench-prototype-01.md` — on
staging set `CONDUCTOR_MODE=bench`, `HUBSPACE_LIVE=1`, `HUBSPACE_ACCOUNT_ALLOWLIST=<bench email>`,
`HUBSPACE_LIVE_UNTIL=2026-10-19`; connect the bench account; register the kit; add its id to
`HUBSPACE_DEVICE_ALLOWLIST`; run Gate A first and paste the anonymized JSON back into a build session.
**Build session:** P1 engine + reconciler + soak, P2 looks, P3 kiosk / ops / visualizer (running next).

## Verified in sim (and how)
- `make test`: 529 passed. `make lint`: clean. `make e2e`: 5 passed (Playwright, Chromium 141, in this sandbox).
- Container: image built from the repo Dockerfile (via a proxy-CA wrapper, D14) and smoke-tested like CI: `/healthz`
  open, 401 without credentials, 200 with; every P0 page and `/api/devices` 200; `import aioafero` works.
- Live gate: the 07 §1 truth table; every writer path (executor engine/bench/burst, direct driver write, bench runner,
  module page) is refused for a registered-but-not-allowlisted device with **zero requests on the wire**, while the
  allowlisted control goes out (`tests/test_writer_paths.py`); a grep test proves only `conductor/drivers/afero*.py`
  touch aioafero/aiohttp; non-allowlisted emails never reach Afero; sim + live is bench-only and on-demand.
- Credentials: a canary signs in with an emailed code through the HTTP routes against a stub Keycloak with sentinel
  password/OTP/tokens/account id — none appear in responses, logs (under uvicorn's real logging config) or files.
- Tester role reaches only `/ops/module` for its granted devices (401/403 elsewhere).
- Screenshots reviewed by the lead: `docs/screenshots/module-*.png` (phone and tablet).

## Needs real hardware (UNVERIFIED until the bench)
The SQHDK03 cloud profile (segment count, slots, `individual`, CCT range, presets), whether Afero accepts slot selects
(Gate B) and frame writes (Gate C), readback semantics (Gate B unplugged), effect codes, per-puck white, real latency
and throttling, token rotation behaviour, 401/403 semantics, the index order, iOS Safari cookie/beacon behaviour.

## Decisions needed from Alon
- Vignette surface by **Oct 1** (06 #11). Default: thin custom pages in P4, stock app if not solid by Oct 12.
- Golden Hour technique and kiosk look by **Oct 5** (06 #12–13). Until a motion code is benched, Golden Hour is a
  static amber glow and its copy says so.
- Review D1–D20 in `docs/DECISIONS.md`, especially D2 (CLI account connect doesn't need the page flag) and D10
  (enrollment links need one tap to confirm).

## Top risks
1. Gate C fails (Afero rejects third-party frames) → Sunset/Garden/Golden Hour drop to T2 (hand-painted slots,
   ~15–20 min per module) or T3. The ladder and copy handle it; staff time is the cost.
2. Real throttling behaviour is unknown; the soak's 5 % 429 + 1 % silent drop model is an assumption.
3. The GitHub push block: nothing reaches Alon until the repo is attached to this session.

## Review fixes — surfaces (`claude/wt-fix2-surfaces`, 2026-09-27)
Fixes for the P1–P3 adversarial review's surface findings F2, F3, F4, F5, F6, F8 (D21–D28), plus the /ops device
list grouped by role/room and the 7 am clock check wording for the venue box (chrony).
- **Verified in sim:** `uv run pytest -q` all green (regression tests in `tests/test_surfaces_review_fixes.py`, most
  against the real canopy engine on the simulator); `uv run pytest -q tests_e2e` 30 passed (Playwright, Chromium),
  including a refused kiosk choice never showing "Changing the light…", flat per-module drawing at T3, red refusal
  lines on /ops, acknowledging an alert, and the grouped device list. Screenshots regenerated and reviewed:
  `docs/screenshots/ops-*.png` (new: `ops-attention-phone.png` — alert injected in the browser — and
  `ops-devices-tablet.png`), `kiosk-*.png`.
- **Needs the venue box:** the clock item reading the watchdog's flag folder (`/run/canopy/timesync` mounted at
  `/run/systemd/timesync`) — unit-tested with a temporary folder only; process-log output of Conductor warnings
  under the real compose/journald setup.
- Also: `tests/test_deploy.py` compose-tag test deflaked (compose names either missing variable first).
