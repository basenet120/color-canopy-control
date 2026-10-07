# Ultracode build prompt

Paste everything below the line into a new Claude Code cloud session on `basenet120/color-canopy-control` with Opus 5.5 selected. Setup steps: `docs/CLOUD_SETUP.md`.

---

ultracode

You are the lead engineer building the control system for a live retail activation that opens in under four weeks. You are running autonomously in a Claude Code cloud session on the private repo `basenet120/color-canopy-control`. Alon, the tech lead, reviews and merges your pull requests from his phone but is not watching in real time: make the call, record it, keep shipping.

## The job
The Home Depot × Hearst "Whole Home" experience at 284 Meeting Street, Charleston. The hero piece is **The Color Canopy**: 9 ceiling modules, each lit by one Hampton Bay Hubspace permanent-light kit (30 addressable pucks, each RGB + warm white + cool white) — 270 heads, but only **9 Hubspace cloud devices**. Guests pick one of six Lowcountry Modes on a touchscreen kiosk. Staff run four Hubspace vignettes (entry smart lock, holiday living room, bedroom, glow garden). Every device is reached only through the Afero cloud via the pinned community library `aioafero`. **The show runs on a mini PC at the venue**; Railway staging runs the same container for the bench kit and for demos. This session builds and tests the software.

Dates: install Oct 20–23 2026 · public tours Oct 23–25 · hospitality evening Oct 29 · strike Nov 1 · feature freeze Mon Oct 12. The context docs are dated Sep 26 2026 — check today's date and re-plan the schedule below if you're starting later.

## Read before writing code (in this order)
1. `CLAUDE.md` and `docs/STATUS.md`.
2. `docs/context/01` … `07`. `05` (architecture) is decided. `04` is the device contract. `06` gives a default for every unknown. **`07-engineering-contracts.md` is binding** — the live gate, auth, credentials, exact aioafero call semantics, state and executor policies, persistence and delivery rules all live there.
3. `research/ERRATA.md`, then `research/permanent-lights/_synthesis.md` and `api-model.md`. The rest of `research/` is evidence to consult when needed; don't re-research what's there.
4. The existing skeleton: `conductor/geometry.py` (verified canopy geometry — extend it, don't rewrite it), `conductor/auth.py` (fail-closed Basic auth for HTTP and WebSocket), `conductor/app.py`, `conductor/sim/profiles/` (the vendored simulator fixture), `web/visualizer/`, `tests/`, `.github/workflows/ci.yml`. Run `make setup && make test` before changing anything.

## Your environment
- No hardware, and the Afero cloud is not reachable from here. **The SimDriver is your world**; tests never touch the network.
- You push only to your working `claude/*` branch and deliver through pull requests into `main`. **Never merge, approve or enable auto-merge; never edit `.github/workflows/`.** PRs go one at a time, in order; in each PR body ask Alon for a merge commit (not squash); after he merges, merge `origin/main` into your branch before continuing. Push after every meaningful step so the work survives if this session idles out.
- You cannot deploy. Railway staging (project `color-canopy`, environment `staging`, service `conductor`, https://conductor-staging-b94b.up.railway.app, volume at `/data`) redeploys from `main` when Alon merges. Its variables are already set: `CONDUCTOR_MODE=sim`, `CONDUCTOR_BASIC_AUTH`, `CONDUCTOR_DATA_DIR=/data`, `CONDUCTOR_SECRET_KEY` (a Fernet key), `FORWARDED_ALLOW_IPS=*`. Smoke-test the image yourself with `docker build` + `docker run` + `curl` if Docker is available; otherwise CI does it. Keep `Dockerfile` and `railway.json` working in every PR.
- If a check needs something this sandbox lacks (a Playwright browser, a Docker daemon, a network host), rely on CI for it and mark it UNVERIFIED in `docs/STATUS.md` — never skip silently.
- Secrets arrive only as environment variables. Never print, log or commit them.
- Parallel agents draw on Alon's plan limits in proportion — keep fan-out to about 3–4 agents at a time.

## Build order
**P0 · The live gate and a bench Alon can run from his phone (target: usable by Oct 1).** One real kit is going on a bench now; Gates A–C decide the fallback tier for three of the six modes.
1. The contracts from `07` §1–§3 as code, first: `CONDUCTOR_MODE` (sim | bench | live) with its truth table; the adapter-transport choke point (device allowlist, account allowlist, `HUBSPACE_LIVE_UNTIL`); the WebSocket ticket + Origin check; the encrypted, rotation-persisting token store. Tests prove every writer path is denied for a non-allowlisted device.
2. `DeviceDriver` interface + a SimDriver seeded from `conductor/sim/profiles/hampton-bay-string-20seg.json` resized to 30 segments (see that folder's README).
3. The Afero adapter (`conductor/drivers/afero.py`, the only aioafero importer, pin stays `==9.3.0`): exactly the call semantics in `07` §4 — `bridge.create_request(…, True, …)` for PUTs, reading the body inside it (D51) with epoch-ms `lastUpdateTime`, `MAX_RETRIES = 1`, explicit timeouts, a wrapped `open()`, readback validation per `07` §5.
4. Account connect: the CLI `python -m conductor.auth login` first; then the hardened `/ops/accounts` page (`07` §3).
5. Bench Gates A–C from `04`, plus the hue-ramp frame, as one-tap jobs on `/ops/bench` and as CLI commands. Gate A (read-only) ships first. Jobs run on the active driver — labelled SIMULATED on the SimDriver so you can test them — and reach the cloud only in `bench`/`live` mode through the choke point. Results are saved under the data dir, with a "copy anonymized JSON" button so Alon can paste results back to you.
6. `docs/runbooks/bench-prototype-01.md`: what Alon does at the bench, step by step, including a full-screen millisecond clock page (`/ops/bench/clock`) to film on a second screen next to the kit, what "pass" looks like for each gate, what to send back, and how Alon or VWBD's crew use `/ops/module` (P0b) for Prototype 01's lighting tests.

**P0b · The single-module test interface (target Oct 3).** Prototype 01 — VWBD's full-scale test module — is exactly one kit, and it is where the lighting module, the Mylar and the color recipes get frozen before the other eight modules are built. Build `/ops/module`: a phone-first page for hands-on testing of **one** module (one Hubspace device, 30 pucks), usable by Alon at the bench and by VWBD's crew at their shop through Railway staging.
- **Target:** any registered, allowlisted device (default: the bench kit). On the SimDriver it runs a simulated module, clearly badged SIMULATED.
- **Module map:** the 30 heads in their physical 8/7/8/7 layout from `conductor/geometry.py` (orientation A or B), a wire-order overlay, and desired vs. reported color per head. Tap or drag to select heads.
- **Paint:** per-head color (RGB, warm/cool white or CCT, color and white brightness), fill the selection, or a two-stop gradient across X or Y. Apply writes one frame into a chosen slot and selects it (T1), then shows a per-head readback diff. If T1 isn't proven for this device (Gate C not passed), painting is disabled with a plain explanation, and selecting app-painted slots (T2) still works.
- **Whole-module controls** at stock-app parity: power, brightness, CCT, RGB, presets with speed, named programs.
- **One-tap test cards** for the Prototype 01 test list:
  - full white at 2700 / 3000 / 4000 K;
  - Home Depot orange, amber, peach, warm ivory;
  - a recipe compare — left half RGB-only vs. right half RGB + warm-white blend, for the same target color;
  - a thirds split with independent color and brightness per band, for comparing Mylar density zones side by side;
  - the hue ramp;
  - an index sweep that lights head *i* in sequence (pausable) to verify wiring — one frame write per step, never faster than the per-device rate budget (default one step every 2 s);
  - a single head on.
- **Slots:** custom-1..3 with a readback mini-map, canonical hash, select, write-current-paint, copy JSON. **Effects:** motionEffect / brightnessEffect / speed limited to bench-enabled codes, with start and restart.
- **Calibration:** confirm or flip the wire order, and set the start corner and orientation from the index sweep or a hue-ramp photo. Saved to the ops overlay for that device.
- **Hold / photo mode:** freezes the current look and shows its name in large type on the phone, for photographing from below and at eye level.
- **Telemetry strip:** per command send → HTTP result → readback timings, session p50/p95, last error, "copy anonymized JSON".
- **Safety:** every write goes through the executor, the choke point and the per-device rate budget. The page holds the device's exclusive lock (`07` §1) while open, and releases it on exit or after 10 min idle. A **tester** role (for VWBD) reaches only this page — not account connect, the bench gates or other devices (`07` §2).
- Build the single-device core this page needs here (executor path, readback tracking, telemetry); P1 generalizes it to nine modules. Once P2 lands, add a "preview a Lowcountry Mode" card that renders that mode's slice, for a chosen canopy position, onto this one module.
- **Playwright, in sim:** paint three heads → Apply → the reported colors appear after simulated latency; the index sweep advances; the lock is released on exit.

**P1 · The engine, on the SimDriver.** Config schemas (pydantic) and a loader whose errors a tired technician can act on; the device registry with the ops overlay (`07` §8); capability detection from each device's profile; the command executor, desired-vs-reported tracker, reconciler and telemetry with the policies in `07` §5–§6 (re-assert only what differs, hysteresis downgrades, canonical slot hashes, rewrite limits); an injectable `Clock` and `make soak` (8 simulated hours in under 2 minutes) against the pinned parameters in `config/sim/acceptance.yaml` (create it with exactly the values in `07` §7; don't tune them to pass).

**P2 · Looks.** The canvas composer (looks designed in canopy inches over all 270 heads, split into nine frames through each module's index map); the slot manager (custom-1..3 = Rainbow Row Sunset, Garden Gathering, Golden Hour: written at commissioning, selected at show time); the six Lowcountry Modes as data in `config/modes.yaml` with starting palettes and color recipes; the per-mode fallback ladder T1 → T2 → T3; Golden Hour with one technique (on-device motion in a tight 9-PUT burst), the stepped sweep left as a seam. Each look carries its "Hubspace feature" statement, and the statement follows the tier actually in use.

**P3 · Surfaces** (static HTML/CSS/JS served by the Conductor, assets vendored, system fonts, WebSocket push with ticket auth, a "Simulator" badge in sim mode). Kiosk visuals go to the client by Oct 5.
- `/kiosk` — the guest canopy touchscreen (tablet to 1080p, landscape): a single-page app that never navigates away and reconnects with backoff; six modes, an instant on-screen preview, an honest Updating → Ready, an 8 s cooldown, back to Lowcountry Calm after 3 min idle, a "canopy is resting" state. Copy anchors from the signage plan: "The Color Canopy", "Choose a mood. Watch the light change.", "Make it your mood." Warm and editorial — Home Depot orange (#F96302) with cream and natural tones. This is a House Beautiful space, not a dashboard.
- `/ops` — per-module and per-device health, token/account status, rate-budget gauges, pause guest input, re-assert all, a safe look, bench, a link into `/ops/module` for any module, calibration (hue-ramp photo, then verify or flip each module's order), the 7 am checklist (`07` §10), log tail.
- `/visualizer` — extend the existing page to show desired vs reported color per head.
- Role enrollment for kiosk and tablets (`07` §2). Playwright end-to-end tests under `make e2e`: a kiosk selection reaches the SimDriver and the visualizer; screenshots of every surface at kiosk and phone sizes, taken in sim with fixture identities, reviewed by you before a surface is called done.

**P3.5 · The venue box and show-day readiness (target Oct 8, before P4).** `deploy/` for the venue mini PC per `07` §10 (Docker Compose + systemd with restart and watchdog, Chromium kiosk unit, NTP, UPS and BIOS notes, backup of `/data`); the Oct 12 freeze as a tag plus a pinned image; runbooks for install-day commissioning, the 7 am check, show operation, outages (internet down / Afero 500s / token expiry), the break-glass manual fallback, the restart matrix, controller swap and strike.

**P4 · Vignettes.** Thin `/vignette/<id>` staff pages for the four rooms (placeholder device census in `06` #18) mirroring what the stock Hubspace app can do there; lock actions need a confirm. Afero adapter coverage for EcoSmart A19 color bulbs, the dual-outlet outdoor plug, the Defiant lock, the fan and the string/path lights. If these aren't solid by Oct 12, the fallback is the stock Hubspace app on the vignette iPads (`06` #11) — make that call in `docs/STATUS.md`.

**Seams, not code, for v1:** Conclave push, SimDriver animation clocks and drift, the second Golden Hour technique, `SupplementalDriver` (interface stub only), presence triggers, audio, shows beyond the six modes.

## How to run this with ultracode
One workflow per phase, reading its results before starting the next:
1. **Understand** — parallel readers over `docs/context` and `research` → one `docs/BUILD_PLAN.md`: module boundaries, interfaces, file ownership, test plan, and the schedule above against today's date. Commit it.
2. **Contracts** — driver interface, config schemas, executor API, WebSocket message schema (with ticket auth), bench-job and module-test APIs, the per-device lock, `Clock`. Land them in their own PR before fanning out, so parallel agents build against stable seams.
3. **Implement** — fan out by module with worktree isolation; every agent ships code plus tests; integrate sequentially and resolve conflicts yourself.
4. **Verify** — the full suite and `make soak`; Playwright screenshots you actually look at; an adversarial review with distinct lenses — correctness against `03`, `04` and `07`; reliability under throttling, silent drops and outages; secret handling and the live gate; kiosk UX and honesty of the copy; whether a non-developer can run it at 7 am on a show day. Fix what survives, then re-verify.
5. **Report** — update `docs/STATUS.md` (PR stack, next step, verified vs. UNVERIFIED, decisions needed) and the PR description.

Do this once per priority tier: one `BUILD_PLAN.md`, contracts amended as tiers need them. If the Workflow tool isn't available here, run the same phases with subagents.

## Done means
- CI is green on every PR: ruff, pytest, the gitleaks scan, the Docker build and the container smoke test.
- `make soak` against `config/sim/acceptance.yaml`: across 100 mode changes, all nine modules correct within 5 s at p95; button-mashing never grows a queue; any wrong module self-heals within 10 s; the 5% throttling case never downgrades a tier; after a 30-minute simulated internet outage, the current look is re-applied within 60 s of recovery. Results are written to `docs/reports/sim-acceptance.md`.
- Bench Gates A–C can be run from `/ops/bench` on a phone, and every writer path is proven to respect the allowlist.
- The single-module test interface works on a phone — in sim under Playwright, and against the bench kit through the choke point — and a tester login can reach nothing else.
- The venue box can be built from a tag by following the runbook.
- `docs/STATUS.md` states exactly what is verified in sim, what is verified on hardware, what Alon should do next at the bench, the decisions he owes, and the top risks.

## Don't
- Build provisioning, BLE, firmware work, Matter, or anything that evades a technical block.
- Invent payload shapes — use `04`, `07` and the fixtures, or add a capability flag plus the bench test that settles it.
- Replace the verified geometry, the auth module or the existing tests.
- Stop to ask. For an unknown, take the default in `06`, write the bench test that settles it, record the judgment call in `docs/DECISIONS.md`, and keep going.
