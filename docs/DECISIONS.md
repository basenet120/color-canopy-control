# Decisions log

Judgment calls made during the build, newest last. Each says what was decided, why, and what would change it.
Architecture decisions in `docs/context/05` are not relitigated here.

## 2026-09-26

**D1 — Devices are addressed by logical registry id; drivers resolve their own address.**
`bench-kit` is `sim-bench-kit` to the SimDriver and its overlay `metadevice_id` to the AferoDriver. In `bench` mode
the engine runs on the simulator while bench jobs reach the real kit, so one id can't serve both. Real metadevice
ids live only in the ops overlay (never in git). Locks and the rate budget are keyed by driver address.

**D2 — Account connect: JSON API for the CLI, HTML form behind `CONDUCTOR_ACCOUNT_CONNECT=1`.**
07 §3 says the CLI "talks to the running server" and `/ops/accounts` needs the flag. The CLI uses
`POST /api/accounts/login|otp` with the ops Basic credential and a JSON body (not submittable cross-site without a
CORS preflight, which we never grant); it works whenever the gate allows Purpose.ACCOUNT (bench/live). The HTML
form needs the flag, a same-origin check, an attempt limit, `no-store` and `frame-ancestors 'none'`, and turns
itself off after a successful connect. Revisit if Alon wants the flag to gate the CLI too.

**D3 — `sim` + `HUBSPACE_LIVE=1` allows bench jobs only, not account connect.**
The 07 §1 table lists only bench jobs for that row. Connect accounts in `bench` mode (staging) instead.

**D4 — Browser WebSockets live under `/ws/`; the Basic middleware lets them through to ticket auth.**
The existing `/ws` test (no credentials → 1008) still holds because `/ws` isn't under `/ws/`. `/ws/live` closes
with 1008 unless the first message is a valid single-use ticket; foreign Origins are refused before accept.

**D5 — The tester role (and kiosk/vignette) uses one-time enrollment links + an HttpOnly session cookie.**
Same mechanism as 07 §2's kiosk enrollment, so VWBD's crew never gets the ops password. Sessions are revocable
from /ops and survive restarts (`<data>/roles/sessions.json`).

**D6 — `make soak` runs on a virtual-time event loop.**
`VirtualTimeLoop` jumps to the next timer whenever nothing is runnable, so every `asyncio.sleep`/`wait_for`
is free and 8 simulated hours run in seconds with unmodified production code. Only valid without real I/O
(the SimDriver world); the Afero adapter always runs on the real loop.

**D7 — Colour temperature goes on the wire as a string ("2700") and is compared numerically.**
07 §4 cites aioafero's encoding as a string; the fixture reads back an int. `rows.compare` normalizes both.
Bench Gate A/7 settles the device's own encoding.

**D8 — Bench and module-page writes always hold the device's lock.**
07 §1 requires the lock while the page is open; making the executor refuse BENCH writes without a lease means a
bug can't bypass it. ENGINE writes to a locked device resolve LOCKED (nothing sent).

**D9 — This session can't push to GitHub.**
The repo isn't in the cloud session's authorized sources (proxy: "not in this session's authorized repository
set"). Work is committed locally on stacked `claude/*` branches and pushed as soon as access is granted. Alon:
add `basenet120/color-canopy-control` to the session's sources (claude.ai/code → session → Sources / GitHub app).

**D10 — Enrollment links: `GET /enroll/<token>` shows a confirm page; `POST` uses the link.**
Link previews (Messages, Slack, mail scanners) fetch pasted links on their own and would burn a GET-redeemed
token. The confirm button also makes the redirect same-origin, so the SameSite=Strict cookie arrives on the next
request. Cost: one tap. (07 §2 says "one-time link"; this keeps it one-time.)

**D11 — Afero adapter details.** Background connects and aioafero's own polling run under Purpose.ENGINE when the
gate allows it, else BENCH. Writes are pre-checked with `check_write` in the driver as well as at the transport,
so a refused write can't trigger a token refresh. An account is "degraded" after 2 consecutive transport/5xx
failures. `disconnect` deletes the stored token. A refresh token rotated by aioafero's background polling is
persisted within 5 s (a keeper task) and on every operation and shutdown. aioafero stamps token expiry with real
wall time, so `ensure_fresh_token` reads real time, not the injected clock.

**D12 — The executor's verification is part of a command.** A newer command for the same device supersedes a
command that is still verifying (its readback no longer matters). Per-account writes are shown as a gauge; only
concurrency is enforced per account (05 #6 gives no per-account write figure).

**D13 — Simulator defaults.** Readback semantics default to "cloud" (the pessimistic case, 06 #3). A device that
comes back online keeps what it last applied (real behaviour: Gate 10). Reads see the same throttle rate as writes,
which is slightly harsher than the pinned soak conditions. `discover`/`fetch_profile` never fault.

**D14 — Local container smoke test.** The sandbox's egress proxy re-signs TLS, so `docker build` here uses a
wrapper Dockerfile (scratchpad only) that trusts the proxy CA; everything after that is the repo's Dockerfile
verbatim. CI builds the real Dockerfile unmodified.

## 2026-09-26 — after the P0 adversarial review (26 confirmed findings, all fixed with regression tests)

**D15 — Adapter hardening.** Sim mode with `HUBSPACE_LIVE=1` is on-demand: nothing connects at startup or
reconnects on its own; a bench call opens the bridge, which closes after 120 s idle (07 §1 "bench jobs only").
A first 401 on a healthy account is `AUTH_STALE` (retryable once, forces a token refresh); only `InvalidAuth` from
the refresh means re-sign-in. 503/504 keep their real status and count toward "degraded" (2 in a row) while staying
retryable throttles; 429/403 don't count as an outage. The outage watchdog needs a real device state readback to
declare recovery (users/me only when the account has no registered device). Tokens are saved only from the bridge
in use; after "disconnect" the account stays down until an explicit connect. Accepted race: a refresh in flight on a
bridge being dropped loses that rotation (the account may need a new sign-in) — the alternative was an 8 s wait on
every disconnect.

**D16 — Redaction is process-wide.** `redact.install()` wraps the LogRecord factory, so every record is scrubbed at
creation under plain uvicorn (no root handlers), plus filters on uvicorn's handlers, its access logger and
`lastResort`. Masks registered secrets, bearer/JWT strings, credential fields, the Afero account id in URLs, and
`/enroll/<token>` paths.

**D17 — Mutating module-page calls need JSON + a same-host Origin/Referer** (a request with neither header is a
non-browser client and allowed; the JSON requirement stops cross-site form posts). The close beacon is exempt but
needs a valid lease. `/ops/accounts/*` gets its security headers from a middleware, whichever route serves it.

**D18 — Executor lifecycle.** `close()` resolves every retrying/verifying command. A lock that is released or taken
over withdraws that lease's queued and retrying commands (SUPERSEDED with a reason); a PUT already on the wire can't be
recalled. Before a burst the executor calls the driver's optional `ensure_fresh_token(account)`; a failed check doesn't
block the burst. Per-device send-time history is pruned. Lock telemetry records expire and steal.

**D19 — Honest bench gates.** Gate C refuses (ERROR, nothing sent) when the target slot already equals the source —
run the hue ramp into slot 2 first — and its BEFORE look is never the target slot. Latency trials alternate between
visibly different looks; spacing is clamped to [rate budget, 30 s] with lock refreshes, and a running job can be
stopped. Flags are bound to the kit that proved them (`flags_proven_on`), and taking back a "yes" un-applies the flags
that run applied (only if the overlay still holds exactly what it applied).

**D20 — Hold means hold.** Entering photo/hold mode withdraws the page's pending and retrying commands so the lights
don't change during a photo. The module page shows SIMULATOR from its own target's flag, never the global badge.

## 2026-09-27 — P1–P4

**D21 — Engine behaviour (P1).** Kiosk requests are refused (never raised) during the 8 s cooldown, while guest input
is paused, while resting (outage) and while slots are being commissioned; ops/idle/reconciler requests bypass the
cooldown. Only a command still throttled after the executor's retries counts toward the "≥ 3 throttles in 60 s"
downgrade — counting each 429 would downgrade at kiosk pace under the pinned 5 % rate. When a verification readback
already differs, the module is re-sent at once (≤ 3 per module per change; motion modes re-send all nine once) —
without this the p95 target missed, because ~9 % of changes contain a silent drop. Locked modules (bench/module page)
don't block READY. The canopy rests when ≥ 3 modules fail with transport/5xx errors and nothing succeeds in between.

**D22 — Show wiring.** On the simulator the slots are commissioned automatically at startup, so demos run T1 as the
show will after install day; on hardware commissioning is an explicit /ops action. A T1 mode is shown only once every
module's slot has a recorded hash. Mode requests during commissioning are refused with "Writing the saved patterns…".

**D23 — Looks.** Canvas looks are composed over the 270 heads in canopy inches through each module's calibrated wire
order; the head-count guard refuses a frame whose length differs from the module's expected heads. Colour temperature
outside a device's range is clamped with a note (Goodnight 2200 → 2700 K on the sibling profile) — never silently.
Holiday uses the first listed preset the device actually has. Golden Hour is a static amber glow, and says so, until
a directional motionEffect code is bench-enabled.

**D24 — Venue box (P3.5).** Secrets live only in `/etc/canopy/conductor.env` (root, 0600); non-secret box settings
in `/etc/canopy/box.env`. The data volume is external so no compose command can delete it. Break-glass means stopping
the Conductor (`canopy-stop`, held through reboots until `canopy-start`), because merely pausing guest input lets the
reconciler undo app edits. Three spare kits are registry entries on the canopy account; registering a spare's device
as a module moves it off the spare and carries only bench flags that same kit proved. The cold spare restores
`/data` with `--skip-tokens` and signs in fresh (07 §3: never copy tokens between runtimes). The venue env example sets
`HUBSPACE_LIVE_UNTIL=2026-11-02` (end of Home Depot's authorization window, pending the letter).

**D25 — Gates on every module.** T1/T2 are used only when every module's own kit passed (flags are bound to the kit,
D19). Install day runs Gates B and C on all nine modules (~5 min each), not just one.

**D26 — Vignettes (P4).** Device profiles for the A19 bulb, the plugs, the deadbolt, the fan and the path lights are
synthesized from aioafero 9.3.0's controller source (UNVERIFIED until real dumps). Every action sends the device's
full rows (an outlet toggle restates the other outlet). Locks are never part of scenes and need a two-step confirm.
The bedroom fan is disabled until the census confirms it. A broken `config/vignettes.yaml` turns the vignette pages
off with a "Needs attention" line instead of stopping the app. **Oct 12 fallback criteria (06 #11):** switch a room's
iPad to the stock Hubspace app (Guided Access, that room's account) unless by Oct 12 (1) each device kind has been
dumped at the bench and matches its profile, (2) from the venue box a tap reads back Ready within ~5 s p95 and a scene
within ~10 s with no throttling pattern, (3) the real deadbolt locks and unlocks with a readback, (4) the pages run
all day on the real iPads in Guided Access, and (5) the authorization letter covers the vignette accounts.

## 2026-09-27 — after the P1–P3 adversarial review: surfaces (kiosk, /ops, log tail)

**D27 — Refusals are never a 200.** The real engine reports a refusal as a `CanopyState` with `refused=True`; the
kiosk route maps it from the engine's own flags (stopped / commissioning / outage → 503 + Retry-After, paused → 423,
cooldown → 429, anything else — no tier compiles — → 409) and logs the engine's sentence; guests only ever see the
kiosk's calm copy. Ops controls answer 409 with the engine's sentence; a commissioning run that never started is a
409, one that ran with problems is `ok: false`. Success lines are built from the state the engine returned (look,
tier, "N of 9 modules", held modules, outage), never a fixed sentence.

**D28 — Commissioning reads as "resting" on the kiosk.** Writing slot contents changes no look, so the kiosk's view
of the state (`/api/kiosk/state`) turns an engine UPDATING-while-commissioning into RESTING "being set up". The kiosk
now treats a WebSocket "canopy" event only as a trigger to re-read `/api/kiosk/state` (debounced 120 ms), so there
is one view of the truth; the engine's own phase is unchanged (canopy.py wasn't this fix's to edit).

**D29 — The log ring keeps stderr.** Adding the ring to the root logger switched off `logging.lastResort`, so
`logring.install()` also adds a redacted WARNING+ stderr handler (resolving `sys.stderr` at write time, like
lastResort) unless the root already writes to the console. The ring is installed first thing in the lifespan.

**D30 — Engine alerts on /ops.** `conductor/alerts.py` subscribes to telemetry and keeps warn/error `alert`s and
`tier` downgrades as one line per problem (keyed: tier per mode, slot per device+slot, outage, commissioning, the
7 am test), with a count, until resolved by the matching event (upgrade, "rewrote it (applied)", Hubspace answering,
an all-good info alert) or acknowledged (`POST /api/ops/alerts/ack`, hidden until it happens again). Info alerts
are logged at INFO, warn/error at WARNING. No tracker `flag_problem` for drift (the tracker's problems have other
owners); the checklist's Slot hashes item still catches it.

**D31 — The checklist fails Guest input while the canopy rests** (not paused), and "Needs attention" says so.
Re-assert with no look answers 409 "nothing to re-assert"; during commissioning, 409 "wait"; during an outage it
says a module was probed. A refused test mode change is a failed test with its own fix text.

**D32 — Clock check on the venue box.** The flag's folder mounted but no `synchronized` file = **not synced** (the
watchdog removes the flag when chrony isn't synced). The FAIL says plainly the box's clock is not synced to internet
time and the fix is the network + chrony (`chronyc tracking`, `systemctl restart chrony`); "can't tell" (staging)
never asks for a mount.

**D33 — The kiosk draws the tier in use.** `/api/kiosk/state` carries `module_colours` (nine, r0c0…r2c2, the mean of
each module's plan preview) when the plan is T3, and `/api/kiosk/modes` gives each mode the tier the engine would
pick now (`compile_best`, ~15 ms per mode) with pattern "modules" at T3. The kiosk re-reads the list when the tier in
use differs from it (at most every 30 s).

**D34 — /ops device list by role and room.** `/api/devices` adds `vignette`, `kind` and `groups` (canopy, bench,
spares, then rooms in vignettes.yaml order). Module page / Bench / head count only for strands; vignette devices get
their room page.
