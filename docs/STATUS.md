# Status

_Last updated: 2026-09-27 — P0 through P4 built on the simulator, three adversarial review rounds fixed, ready for the
bench kit. Nothing has touched real Hubspace hardware yet._

## ⚠ Blocked: this session can't push
The cloud session's git proxy refuses `basenet120/color-canopy-control` ("not in this session's authorized repository
set"), and there is no GitHub CLI or repo-attach tool here. All work is committed locally on the stacked branches
below. **Alon: attach the repo to this session with push access** (claude.ai/code → the session → Sources), then say
"push" — the branches go up and the PRs open in order. Until then CI (incl. the gitleaks scan) has not run.

## PR stack (merge order — please use merge commits, not squash)
Each branch is stacked on the previous one; every cut point was verified green locally (ruff + pytest).
1. `claude/p0-contracts` — build plan, decisions log, the contract modules.
2. `claude/p0-gate-drivers` — SimDriver, Afero adapter + choke point, executor + tracker, roles/enrollment/WS tickets,
   services container, writer-path allowlist test.
3. `claude/p0-bench` — account connect (CLI + hardened page), devices API, ops landing, bench Gates A–C + friends,
   clock page, anonymizer, bench runbook; the modes as data and the engine contract.
4. `claude/p0b-module` — `/ops/module` (single-module test interface) + e2e; fixes for the 26 P0 review findings.
5. `claude/p1-engine-looks` — P1 canopy engine, reconciler, idle hold, commissioning, `make soak`; P2 looks
   (modes schema, canvas composer, compiler, module-page mode preview).
6. `claude/p3-surfaces` — `/kiosk`, full `/ops` (7 am checklist, log tail, calibration hub, enrollment), visualizer
   desired vs reported, engine wiring into the app.
7. `claude/p35-p4-venue-vignettes` — P3.5 venue box (`deploy/venue`, release/freeze, ten runbooks), P4 vignette pages
   and device coverage, and the fixes for the 18 P1–P3 and 15 final-round review findings + 8 completeness gaps.

## Workshop kit for VWBD (branch `claude/workshop-kit`, PR 8 after the stack)
`uv run python scripts/make_workshop_kit.py` builds `dist/color-canopy-workshop-kit-<commit>.zip` (~570 KB, committed
files only; no research, context docs or settings). VWBD unzips it on a shop laptop and runs `start.command` (Mac) or
`start.bat` (Windows): bench mode on one laptop, only their test account and their registered module allowed, a
simulator practice mode, and `workshop/README.md` walks them through connect → Gates A–C → the Prototype 01 tests on
`/ops/module`. Verified: from a fresh unzip on Linux, setup + simulator + every page. UNVERIFIED: macOS and Windows
starters (not runnable here) and the real-module path (needs the kit). Alternative with no install: once the stack is
merged, staging in bench mode + a tester link from /ops (the original P0b design).

## Next step (Alon, at the bench)
1. Attach the repo (above); merge the stack in order as CI goes green.
2. Staging: `CONDUCTOR_MODE=bench`, `HUBSPACE_LIVE=1`, `HUBSPACE_ACCOUNT_ALLOWLIST=<bench email>`,
   `HUBSPACE_LIVE_UNTIL=2026-10-19` (keep `CONDUCTOR_SECRET_KEY` and `CONDUCTOR_BASIC_AUTH`).
3. Follow `docs/runbooks/bench-prototype-01.md`: connect the bench account (`python -m conductor.auth login
   --server https://conductor-staging-b94b.up.railway.app`, or `/ops/accounts` with `CONDUCTOR_ACCOUNT_CONNECT=1`),
   register the kit, put its id in `HUBSPACE_DEVICE_ALLOWLIST`, open `/ops/bench/clock` on a second screen, and run
   **Gate A first**. Paste its "Copy anonymized JSON" into a build session — it becomes the real SQHDK03 fixture.
4. Then Gates B, B-unplugged, C, the hue ramp, latency trials, and the four new jobs (stale clock, active-slot
   rewrite, rate ramp, head-count boot). Give VWBD a tester link from `/ops` → "Set up a device" for `/ops/module`.

## Verified in sim (and how) — 2026-09-27
- `make lint` clean; `make test` **923 passed**; `make e2e` **34 passed** (Playwright/Chromium in this sandbox).
- `make soak` against the pinned `config/sim/acceptance.yaml`, through the show's own wiring — **every target PASS**
  (`docs/reports/sim-acceptance.md`): all nine correct after a change p95 3.5 s (≤ 5); queue depth max 18 (never
  grows); silent drops healed ≤ 3.1 s and changes made outside the Conductor detected + healed ≤ 6.1 s (≤ 10); 0 tier
  downgrades at 5 % throttling; look re-applied 5.2 s after the 30-min outage and 8.9 s after the 43-min 500 window
  (≤ 60); writes to one device never < 1.0 s apart. 8 simulated hours in 54 s wall.
- Container: the repo Dockerfile builds (via a proxy-CA wrapper in this sandbox, D14) and passes the CI smoke checks;
  every surface (`/kiosk`, `/ops`, `/ops/bench`, `/ops/module`, `/vignette/<room>`, checklist, overlay) answers 200
  with credentials and 401 without.
- Live gate: every writer path (engine burst, reconciler, idle hold, commissioning, module page incl. recording a
  hand-painted slot, bench runner, vignette actions/scenes, direct driver writes) is refused for a registered but
  non-allowlisted device with zero requests on the wire; only `conductor/drivers/afero*.py` touch aioafero/aiohttp;
  sim + live is bench-only and on demand; `HUBSPACE_LIVE_UNTIL` enforced.
- Credentials: a canary signs in (with an emailed code) through the HTTP routes against a stub Keycloak using sentinel
  password/OTP/tokens/account id; none appear in responses, logs (under uvicorn's real logging config), the /ops log
  tail or files. Tokens are Fernet-encrypted, bound to runtime + account, rotated atomically.
- Roles: a tester reaches only `/ops/module` for granted devices; a kiosk only `/kiosk`; a vignette tablet only its
  room; revoked sessions lose their sockets.
- Screenshots reviewed by the lead (`docs/screenshots/`): kiosk (1024×768, 1280×800, 1920×1080, updating, resting),
  module page (phone, tablet, hold), ops (phone, tablet, attention), calibrate, visualizer (desktop, phone), bench and
  clock (phone), accounts (phone), vignettes (all four rooms, iPad).

## Needs real hardware (UNVERIFIED)
- The SQHDK03 cloud profile (Gate A): segment count, slots, `individual`, CCT range, presets, programs.
- Afero accepting slot selects (Gate B) and frame writes (Gate C); readback semantics with the kit unplugged.
- Effect codes (every code disabled until benched → Golden Hour is a static amber glow and says so).
- Per-puck white / colour recipes through Mylar (Prototype 01); index order and wire routing (calibration).
- Real latency, throttling (writes and the 1.8 GET/s true-up reads), token rotation, 401/403 semantics.
- Stale-clock writes, rewriting the showing slot, the 12-puck fault (the four new bench jobs settle them).
- Vignette device profiles (synthesized from aioafero's controller source) and the deadbolt readback.
- The venue box itself: systemd, the Chromium snap kiosk + resting page, install from a tag, BIOS/UPS/NTP, cold spare.
- iOS Safari: enrollment cookies, beacon on page close, Guided Access. gitleaks (runs in CI only).

## Decisions Alon owes
- **Vignette surface — Oct 1** (06 #11). Built: thin `/vignette/<room>` pages. The Oct 12 fallback criteria are in
  D26: any room failing them goes to the stock Hubspace app in Guided Access.
- **Golden Hour technique and kiosk look — Oct 5** (06 #12–13). Kiosk screenshots are ready for the client
  (`docs/screenshots/kiosk-*.png`). Golden Hour needs a benched directional motion code to claim movement.
- **Authorization letter** from Home Depot before any show-account writes (06 #20); `HUBSPACE_LIVE_UNTIL=2026-11-02`
  in the venue env example assumes its window.
- **A second named person** with the show-account password and inbox (`docs/runbooks/outages.md`).
- Gates B/C run on **all nine** modules at install (~5 min each), because flags are bound to each kit (D25).
- Skim D1–D50 in `docs/DECISIONS.md`; most worth a look: D2 (CLI connect without the page flag), D10 (enrollment
  links need a confirm tap), D15 (sim + live is on demand), D44 (5 s rolling true-up reads).

## Top risks
1. **Gate C fails** (Afero rejects third-party frames) → painted modes fall to T2 (hand-paint in the app, ~15–20 min
   per module, then "record it" on `/ops/module`) or T3. Copy follows automatically; staff time is the cost.
2. **Real throttling** differs from the soak's 5 % 429 + 1 % silent-drop model, or the rolling true-up reads get
   throttled — the rate-ramp job and the venue box measure it; the interval is one config value.
3. **Push access** — nothing reaches Alon, CI or staging until the repo is attached to this session.
4. **Venue box untested on real hardware** before Oct 8 — do the runbook's dry-run steps (`venue-box.md` §4) on the
   real mini PC as soon as it arrives.
5. Sandbox note: on Sep 27 an install-script test escaped its stubs and ran `apt-get install chrony` and
   `systemctl enable` inside this disposable cloud container only (no repo, device or external-service impact). The
   test now pins PATH and refuses installs outside its temp dir.
