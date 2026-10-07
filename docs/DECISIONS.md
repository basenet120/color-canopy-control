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

## 2026-09-27 — canopy engine, after the P1–P3 adversarial review (engine findings F1–F11)

Numbered D35–E9 so they don't collide with other review branches; the lead may renumber when merging.

**D35 — Capabilities are a background loop, and the first look waits for its first round.** `ShowWiring.start()`
returns at once; a task reads every module's profile concurrently (15 s timeout each), retries the ones missing with
backoff 1, 2, 4 … 60 s, and reads again on the engine's outage recovery, the driver's `on_recovery`, a module coming
back online (a readback with `available: true` after `false`) and `/api/devices?refresh=1`. The idle mode is requested
after the first round (success or not) rather than by `engine.start()`, so a box whose profiles answer at once doesn't
send Calm twice. New or changed capabilities re-compile the current mode (source "reconciler"). In sim, commissioning
waits until every module is read.

**D36 — The safe look assumes conservative capabilities; everything else leaves an unread module out.** Lowcountry
Calm compiles for a module without a profile with the sibling string's documented white range (2700–6500 K, 04), so
the safe look can always go out. Every other mode compiles for the modules that were read and lists the rest in
`ModePlan.skipped` (the engine shows them as needing attention). Copy that counts ("all nine", "270 lights") counts
the modules actually driven.

**D37 — Slot records carry an origin and effect fields.** `DeviceEntry.slot_origin` ("api" = commissioned, "app" =
recorded as hand-painted) and `slot_effects` sit next to `slot_hashes`. A hash without an origin is "api" (before this,
commissioning was the only writer), so an old overlay can never make the "painted in the Hubspace app" copy appear. T2
needs "app" records; T1 is shown only with "api" records on every module. Commissioning never overwrites an "app" slot
(protecting 15–20 min of hand-painting per module); going back to T1 for it means removing its record in the overlay
(runbook). Changing that would need an explicit "forget this record" action.

**D38 — T1 is judged against the commissioned readback hash; stale slots are rewritten once.** The engine's expected
slot row names only the recorded hash (`rows.slot_hash_expectation`, read by `rows.compare`; the canonical projection
is unchanged). When the look now compiles to another pattern or other effect codes (recalibration, a design edit, a
code enabled or disabled), the reconciler rewrites that slot within the existing 10-minute limit — once per compiled
content, never in a loop — and the expectation, copy and motion flag follow the new record. A slot that already holds
exactly the current content (e.g. a rewrite whose verification a newer command superseded) is recorded without a
write. One slot write per module at a time.

**D39 — Motion copy follows the device, and `motion` means "re-trigger as one burst".** Golden Hour's motion copy
appears only when every module's slot holds the benched code (recorded from readback at commissioning, else the
header readback). `ModePlan.motion` is set whenever a slot may animate (a code configured and enabled, a slot that
still holds an effect, a hand-painted slot of a `motion: true` mode whose header animates or is unknown), so copy and
re-trigger semantics are no longer the same flag. The effect fields are checked on readback apart from the hash;
only motionEffect/brightnessEffect count (speeds and depth aren't claimed in copy).

**D40 — 503/504 are an outage, 429/403 are throttling.** THROTTLED attempts or readbacks with HTTP 503/504 count toward
the resting streak like SERVER_ERROR and never toward the throttle downgrade, matching the adapter's own outage
classification (D15).

**D41 — Upgrades lift past rungs that can't be shown.** When a floor lifts onto a rung that isn't allowed or doesn't
compile, it keeps lifting to the next usable rung in the same step, so T3 → T1 takes one clean interval (5 min) when T2
isn't available.

**D42 — Lowcountry Calm never names a Kelvin value it can't show.** A strand look whose copy names its Kelvin value uses
the configured fallback look (and its copy) when a module's range would clamp that value; without a fallback, the
copy names the value actually sent. Goodnight's copy names no value, so its 2200 → 2700 K clamp stands.

**D43 — The soak runs the show's wiring, with a SIMULATED motion code.** `make soak` builds `build_services` +
`ShowWiring` on the virtual clock with a temporary data dir (telemetry kept in memory), so it exercises the capability
loop, the commissioning gate, overlay records and the real ladder. `config/sim/soak.yaml` enables motionEffect 1 for
Golden Hour in the soak only (nothing benched; the report labels it SIMULATED) so the all-or-none re-trigger is
measured. The pinned acceptance conditions are unchanged. `run_soak(compiler=…)` still runs the bare engine.

## 2026-09-27 — final verify round (15 confirmed findings + 8 completeness gaps, all addressed)

**D44 — Self-heal covers changes made outside the Conductor.** The reconciler reads each canopy module once every
5 s on a rolling schedule (≈ 1.8 GET/s on the canopy account; reads never count against the write budget), so a
module changed in the Hubspace app or one that lost its look is back within 10 s (soak: p95 6.1 s over 12 injected
out-of-band changes). Slot hashes are re-checked only when a slot changed or every 60 s. The read load on real Afero
is UNVERIFIED — if the bench rate test or the venue shows read throttling, raise `reconciler.true_up_interval_s`.

**D45 — Guest-input pause survives restarts** (atomic file under `<data>/state/`); an unreadable file counts as paused.

**D46 — Unenrolled kiosk screens never show a sign-in dialog.** `/kiosk` and `/kiosk/*` without a principal get a
calm "This screen isn't set up yet" page (401, no Basic challenge); every other path keeps the challenge. While the
Conductor is down the kiosk unit shows a local resting page that polls `/healthz`, never the desktop.

**D47 — Releases are amd64 and tags switch last.** `release.sh` builds `--platform linux/amd64`; `install.sh` checks
the image's commit and architecture and writes `CONDUCTOR_TAG` only after the image is verified, recording the
previous tag for rollback. The cold spare installs with `--spare` (held and stopped until takeover).

**D48 — Overlay export.** `GET /api/ops/overlay` (ops only) downloads the ops overlay YAML (with real ids, for
restoring on a box) plus a diff against `config/devices.yaml` with real ids replaced (for committing to git).

**D49 — Four more bench jobs settle the remaining UNVERIFIED behaviours:** `stale_clock_put` (lastUpdateTime
now − 10 min), `active_slot_rewrite` (rewrite the showing slot without reselecting), `rate_ramp` (5 → 2 → 1 s frame
writes, silent-drop count, stop at the first failure) and `head_count_boot` (the 12-puck fault). A bench-only stamp
override on a command is the one way to send a chosen lastUpdateTime, and it still passes the gate.

**D50 — Vignette pages see only look rows.** Tablets and the `vignette:<room>` topic never get telemetry rows (SSID,
MACs, geo). A revoked enrolled session's sockets are closed, including tickets issued before the revoke.

## 2026-09-27 — first real kit (VWBD's workshop)

**D51 — Read Hubspace replies inside the request context.** aioafero 9.3.0's `bridge.request()` releases the
response before returning it; reading it again raised ClientConnectionError on a 200 ("Couldn't reach Hubspace"
on Show devices). The adapter uses `bridge.create_request(method, url, True, …)` and reads the body inside it, under
the same timeout; status handling stays ours, one HTTP attempt. Found and patched by VWBD; their loopback test with
real aiohttp is in `tests/test_hubspace_transport.py` (the fake transport can't model release).

**D52 — Frame length ≠ puck count.** The real SQHDK03 reports a fixed 60-entry framebuffer per slot with 30 pucks
attached (04's "or a fixed 60"). Frames are always written at the device's length; the module's pucks occupy entries
`frame_offset … frame_offset + expected_heads − 1` (default offset 0, UNVERIFIED — the index sweep confirms it, and
the Calibrate tab's offset fixes it without code), every other entry is sent OFF so hashes stay stable. The canonical
slot hash still covers the full frame (07 §6). A frame shorter than the pucks need is still refused. Consequence:
the 12-puck fault can't be seen from the frame length on this model; the 7 am head-count check says so and asks for
a visual check. The simulator's default kit now has 60-entry frames driving 30 pucks, like the real one.
_Mechanism superseded by D54 (2026-10-07): no `frame_offset`, no frame-offset field on the Calibrate tab, and the
default simulator kit is back to 30 entries. Still true: Kingston reports 60 entries with 30 pucks, and frames shorter
than the pucks are refused. Unused entries are written OFF only through a kit's mapping. The 7 am check, composer and
compiler are back to frame = heads until the port in STATUS gap 1. Whether the 12-puck fault shows in the cloud is
unbenched (gate 10)._

**D53 — A client that disconnects mid-request on the accounts routes is a quiet no-op** (499, one INFO line), not a
traceback.

## 2026-10-07 — Paris's VWBD field work merged as canon

**D54 — Paris's VWBD source is canon; a per-kit `framebuffer_mapping` replaces D52's `frame_offset`.** Paris worked
on the real kits at VWBD from Sep 28 to Oct 7, on a fork of the workshop kit (1eb8551). Alon made that source canonical.
Where it and main both changed a file, Paris's whole file won. Main's post-fork frame-layout work (929c0d2: `FrameLayout`,
`frame_offset`, the Calibrate tab's frame-offset field, the 60-entry default simulator) came out as one unit. D51's fix (Paris's version) and D53
stay. Per-head painting (module page, Studio) reaches a kit's pucks only through `framebuffer_mapping {frame_length, indices, proven_on}`. That field
lives in the ops overlay (NOT_FOR_GIT) and never transfers between kits (D25). It applies only when `proven_on` is that
device's id and the frame length, head count and slot lengths agree. Unmapped entries are written OFF, and readback is
mapped back through the same indices. Without a mapping the frame must equal the heads. Kingston's map was watched on
the lights on Sep 28: entries 0–29 drive pucks 1–30 from the controller end, and 30–59 drive nothing. Slot writes now
check every requested effect field as well as the colour hash; hash-only checks stay colour-only. **Known gap:** only
the module page and the Studio read the mapping. Bench jobs address raw controller entries (they need only that kit's
T1 flag) and never read or save a map. The show engine's T1 compile and composer, `/api/devices`,
the attention list and the 7 am head-count check do not. A real 60-entry module is flagged there, and T1 canvas looks
can't use it, until the mapping is ported into those paths.

**D55 — Bounded commissioning jobs restore what they touch and promote nothing.** `gate_c_safe` copies a slot into
Custom 1 or 2 (never Custom 3), holds it for a bounded time, then restores the frame, the slot selection and the active
look as three separate commands. A single combined restore left String light 1 in Custom mode (run f613f6, CLOUD readback).
`address_probe_safe` defaults to Custom 2, refuses Custom 3 and restores exactly. `effect_probe` tests one effect code
for 2–20 s and restores the slot, but not the previous look. No job enables or names an effect code or saves a map;
`effects.enabled` stays empty. The original `address_probe` defaulted to Custom 3 and left its pattern there, so it is **no longer a runnable job**
(decided 2026-10-07). The bench page, CLI and runner refuse it; the function survives only inside `address_probe_safe`,
which tracks and restores what it touched.

**D56 — The Studio (`/ops/showcase/`) is Paris's six-scene home, and `/` redirects to it.** Six scene palettes
(Lowcountry Calm … The Goodnight Glow) are drawn on the saved 270-head canopy. They live in
`web/ops/showcase/demo-model.mjs`; `config/modes.yaml` is unchanged. The Studio adds:
- ops-only paired control of the two shop modules (`POST /api/studio/pair/whole`: both sessions preflighted, separate
  leases, one shared lock);
- a paired per-head scene gradient (`GET/POST /api/studio/pair/gradient`). It needs each kit's own T1 proof and
  mapping, keeps a durable Custom 1 backup under the data dir, and never writes Custom 3;
- a one-tap Kingston motion demo. It runs the bench `effect_probe` (code 1, speed 30, 20 s, on Custom 3; it ends on
  Custom 3 per D55) and is gated on Kingston's T1 flag and map. Its copy says blocks "travel between light heads", but
  the evidence is only that they "moved to different heads"; direction is unverified.

The gradient has never been sent to real hardware, because preflight blocks the uncommissioned String light 1. The
Studio is wired to VWBD's two kits: `bench-kit` (label "Kingston test…") and `shop-string-1` (label "String light 1…"),
the second only in VWBD's private overlay. Anywhere else the live Studio refuses to connect and only `?preview=1`
works. So `/` redirects to the Studio only when the runtime environment (`CONDUCTOR_ENV`, else Railway's
environment name) starts with `workshop`: Paris's laptop, live or practice. Everywhere else (venue box, staging,
local) it goes to `/visualizer/` as before (decided 2026-10-07).
