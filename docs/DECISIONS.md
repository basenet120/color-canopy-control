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
as a module moves it off the spare and carries only bench flags and the controller map (D57) that same kit proved. The cold spare restores
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
compiler read each kit's map since D57. Whether the 12-puck fault shows in the cloud is
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
check every requested effect field as well as the colour hash; hash-only checks stay colour-only. Bench jobs address raw controller entries (they need
only that kit's T1 flag) and never read or save a map. The show path read no map at first; D57 closed that gap.

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

**D57 — The show path reads each kit's own map (closes D54's gap).** One rule, `conductor/framemap.py`
`frame_indices`, decides whether a saved `framebuffer_mapping` applies: `proven_on` is that device's own address, its
`frame_length` is the one length every slot reports, and it maps exactly the module's heads. `ModuleSession` uses the
same rule; on 200,000 random inputs it matched Paris's original. Every consumer uses it:
- **The show engine:** the T1 composer and compiler write head *i* to entry `indices[i]` and every other entry OFF, so
  the full-frame slot hash stays deterministic.
- **The checks:** `/api/devices`, the attention list and the 7 am check. A mapped kit passes, and its 60-entry frame
  is noted as controller capacity, with the head check visual. A longer frame with no map says the kit has no proven
  map yet and points to runbook §5a. A short frame keeps the plug-in / power-cycle fix. Mixed slot lengths or slots
  with no readback get Refresh.
- **Previews:** `/ops` and the plan previews read heads through the map.

Without a valid map, the Conductor's T1 frames are refused for that kit, but whole-strand looks and app-painted T2
slots still work. A controller swap carries a spare's map only when the map was proven on that same kit. The swap
also drops the show's cached profile of the old controller, so the carried map is never judged against it.

Effect start/restart on the module page keeps the unmapped entries as read. Paint, cards, sweep and preview still
write them OFF. An opt-in `sqhdk03-sim-60` profile models the real kit; the default simulator stays at 30 entries.
Recording a map was still an offline overlay edit (STATUS gap 9) until D58.

**D58 — A kit's map is recorded in the app from its own two answered address probes, and only as the identity
(closes STATUS gap 9).** **Apply map** on an `address_probe_safe` result card on `/ops/bench` (also
`POST /api/bench/runs/{id}/map` and `python -m conductor.bench map <run_id>`) records `framebuffer_mapping` for
device D at controller address A only when all of these hold (the proof rule, accepted by Alon on 2026-10-07):
- (a) Gate C is proven on A: `flags_for(A).t1_frames` is true;
- (b) D's newest markers probe at A (entry 0 red, 1 green, 29 blue) is a real run that finished with an exact restore
  and is answered **Yes** to "Counting from the controller, was head 1 red, head 2 green and head 30 blue, with every
  other head dark?";
- (c) D's newest real probe at A of every entry from the module's head count to the last (30–59) is answered **Yes**
  to "… did every head stay dark (nothing lit) while entries 30–59 were held?", and so is every Yes/No probe at A of
  only part of that range (e.g. 45–59) run after it;
- (d) both runs saw the same frame length L (each run checked that every slot agreed), the kit still reports L when the
  map is applied (one profile read, a GET), and the module map's head count (30) equals the registry's
  `expected_heads`.

The map is then exactly the identity, `{frame_length: L, indices: [0 … heads − 1], proven_on: A}`. Anything else is
refused with a plain reason, and no permutation is ever inferred. That covers a No (markers seen elsewhere, reversed
order, a lit head past the heads), a missing or unanswered probe, a simulated run, a run from a kit that was
re-registered since, and a changed frame length; the reason ends in "tell Alon" where the kit itself looks wrong.
The newest probe of each kind is the one that counts, so a probe of part of 30–59 run before the justifying 30–59
run is superseded by it. Other details:
- the map goes into the ops overlay only (NOT_FOR_GIT), and only on that explicit action, never automatically. The
  POST is refused (403/415) unless it is JSON from this site, like the module and ops writes;
- both runs record what they justified, when, and the map it replaced. Taking back a Yes on either run puts that
  previous map back on every registry entry that still holds exactly what was applied: the run's device, and a
  module a controller swap carried it to;
- applying or revoking drops the show's cached profile of that module and reads it again, as a controller swap does,
  so `/ops`, the attention list, the 7 am check and T1 pick it up at once;
- Apply map itself sends nothing to the lights. But on a canopy module the show counts its re-read as new
  capabilities, so a running show then sends its current look to the canopy again (its own writes, through its
  executor and the live gate), now through the map. A revoke does the same.

So that (b) and (c) can be answered Yes or No, `address_probe_safe` now asks those two questions for a markers run
and for a range wholly past the module's heads. Every other range keeps Paris's free-text question, and the job's
writes and restore are unchanged. Probes saved before this change asked the free-text question, so they don't count.
A newer markers or 30–59 probe, or a later Yes/No probe of part of 30–59, that isn't answered Yes blocks a new
Apply map. None of them removes a map already recorded; taking back the Yes on a run that recorded it does. Lower
ranges keep the free-text question, so Apply map doesn't read them. Simulator only so far: the flow has never run
against a real kit.

## 2026-10-11 — the approved scenes

**D59 — One scene catalog: the show's six modes are Paris's six approved scenes, and every scene is static.** Alon
approved Paris's six scenes (`config/scenes.yaml`, d897843). That file is the one catalog the guest panel, the show's
modes and the Studio build on. `config/modes.yaml` keeps its schema and its ladders, but each mode's id, label, blurb
and preview are now the scene's id, name, mood and palette (in order), and every tier renders that palette:
- **Coastal Tides replaces Holiday at Home** (`coastal-tides`). The other five ids are unchanged.
- **Canvas (T1):** a static gradient across the canopy, x / 225 in (the Studio's illustration samples it the same
  way), with the palette's colours as evenly spaced stops. The stops name new palette recipes in
  `config/recipes.yaml`, painted `rgb_only` at full strength. Their `blend` is the same RGB with no white, because
  per-puck white is unverified (gate 6). The composed frames equal `Scene.color_at(x / 225)` head for head.
- **Slot (T2):** the same design, hand-painted in the Hubspace app, as before.
- **Module level (T3):** each module shows the scene's gradient at its column centre (x = 37.5, 112.5, 187.5 in).
  Lowcountry Calm's mirror shows here: its two side columns match, with sea glass in the middle.
- **Strand:** the scene's solid colour (`default_solid`) on every strand. Every ladder now ends `modules, strand`.
- **Brightness** is the scene's `brightness_draft` at every tier. It is a device setting, never multiplied into a
  colour. The values are drafts until the physical mood approval.
- **Three slots, three painted looks.** The controller holds custom-1..3, and painted looks are stored at
  commissioning and only selected at show time (04). So Rainbow Row Sunset (custom-1), Garden Gathering (custom-2) and
  Golden Hour (custom-3) keep their slots and ladders `canvas, slot, modules, strand`. Lowcountry Calm, Coastal Tides
  and The Goodnight Glow start at module level (`modules, strand`). Giving a slot to another scene is a config change:
  move `slot:`, add its canvas and slot techniques, and add its palette recipes.
- **Static.** No mode has `motion: true`, a `motion_effect` or a `motion_feature`. The soak still enables a SIMULATED
  code on Golden Hour and supplies its own motion copy (`config/sim/soak.yaml`), so the all-or-none re-trigger stays
  measured.
- **Copy.** T1 and T2 keep their sentences. T3 says "Colour and dimming: each Hubspace strand set to a single colour, so
  the palette steps across the canopy column by column." Strand says "Colour and dimming: all nine Hubspace strands set
  to <the solid colour>." The kiosk blurb is the scene's mood, which is true at every tier; the longer description
  stays in `scenes.yaml`.
- **What changes on the lights.** Live, before Gate B/C, every mode now runs at module level, where before three ran at
  T3 and three as whole-strand white or a preset. The safe look (Calm at strand) is soft ivory RGB, not 2700 K white.
  The kiosk draws a T1 gradient across the canopy (`gradient-x`), and draws a one-colour strand look as flat module
  colours. The module page's mode preview now follows the mode's ladder order (T1, then T3, then strand).
- **Kept.** The colour-study recipes (HD orange, amber, …) stay for the module page's cards. The compiler keeps its
  other techniques (white CCT and its clamps, presets, pathway and band canvases, row and middle-row layouts, motion
  copy). They are tested on the pre-D59 modes, kept as a fixture (`tests/fixtures_modes_techniques.yaml`).
  `tests/test_modes_match_scenes.py` pins `modes.yaml` to `scenes.yaml`.

## 2026-10-11 — the iPad guest panel

**D60 — Guest brightness and on/off are part of the engine's desired state.** The guest panel lets guests dim the
canopy and turn it off and on, as well as choose a mood. `CanopyEngine` gains `set_guest_brightness(level, *, source)`,
`set_guest_power(on, *, source)` and `clear_guest_overrides(*, source)`. `CanopyState` JSON gains four fields:
- `guest_brightness`: the guest's level, or null for the look's designed brightness;
- `guest_power`: false while a guest has the canopy off;
- `brightness`: the level the canopy is set to now. That is the guest's level, else the designed one (the brightest
  module's when modules differ);
- `guest_brightness_min`: the lowest level a guest may set.

The rules:
- **Desired state, not a side write.** Every show command sends the plan's full state vector with the guest settings
  applied: the brightness row replaced in place, or `power off` alone. The tracker expects exactly that, and the
  reconciler judges readback against it (`CanopyEngine.desired_rows`). So a re-assert restores the guest's choice and
  never undoes it, even when the drift lands exactly on the designed value. Tier changes, new capabilities, recovery,
  lock releases and early re-sends all carry the guest settings.
- **One write per module.** A change is one burst: one command per module, all at once (a motion plan's modules
  included). Brightness is a control row: no slot object is sent and no frame is rewritten. The executor's
  ≤ 1 write/s per device and its coalescing are unchanged, and a test fires rapid ops changes to prove it.
- **Absolute, for every module.** A guest level replaces each module's designed brightness, clamped to
  [`kiosk.guest_brightness_min` (10), 100], as a Hubspace app group's brightness does, and then to each device's own
  brightness range, as the compiler clamps a designed level. A level that isn't a whole number (true, 30.0, NaN,
  "30") is refused, not coerced. A T3 look whose modules differ (Garden Gathering's bright centre row) flattens to
  one level. Moving the brightness also turns the canopy on (a judgment call: a guest dragging the slider expects
  light).
- **Choosing a mood** clears the brightness and turns the canopy on, as choosing a scene in the Hubspace app does.
  That covers the kiosk, ops, the idle hold and the idle scene. Choosing the mood already showing counts as choosing
  it. Re-picking the look now showing keeps both settings: a tier change, new capabilities, recovery and the
  simulator's start-up commissioning (sources `reconciler`, `recovery`, `startup`).
- **Refusals.** A kiosk change is refused as a kiosk mood choice is: during the cooldown, while guest input is
  paused, and while the canopy rests (outage). The cooldown is shared: after a mood choice a brightness change waits
  the same 8 s, and the other way round. Every source is refused while the engine is stopped, while it commissions,
  and before the first look. Ops bypass the cooldown and the pause. An ops change made during an outage is kept and
  goes out with the look on recovery. A request that changes nothing sends nothing and starts no cooldown.
- **Idle.** A guest brightness or on/off change counts as activity for the idle hold. After `idle_return_s` (180 s)
  the hold returns to `idle_mode`, and that clears the settings. A new **idle reset** acts `kiosk.idle_reset_s`
  (300 s) after the last guest input: if a guest setting is in force, the canopy goes back on at its designed
  brightness, so it is never left dark or dim. With `kiosk.idle_scene` set (default null), the reset also shows that
  mood. With no guest setting in force the reset does nothing, so it never undoes a mood chosen after the last guest
  input (ops' included: choosing a mood clears the settings) and never alternates with the idle hold. `idle_scene`
  must name a mode in `config/modes.yaml`, or the Conductor refuses to start. Like the idle hold, the reset waits
  while guest input is paused (staff own the canopy then), during an outage and during commissioning. It acts on the
  first reconciler tick after a pause or commissioning ends; when the idle hold is due in that tick too, the hold goes
  first and its mood does the clearing, so it is one change. A reset that fell due during an outage goes out with the
  recovery burst, not as a second burst after it.
- **Staff.** The safe look clears both settings: it is always on, at its designed brightness. `POST
  /api/canopy/guest-reset` and the `/ops` button "Clear guest brightness / off" also clear them. The button shows
  only while a guest setting is in force, beside a chip that names the setting. Re-assert keeps the guest settings,
  because it re-sends the desired state.
- **Commissioning** writes slot objects only. Guest settings already in force stay in force through it, and the look
  re-picked afterwards carries them.
- **Not saved.** The settings live in memory only, so a restart comes back on at the designed brightness. The
  guest-input pause is different: it must survive a restart.
- **No kiosk routes here.** The guest panel branch calls these engine methods when they exist. `/api/kiosk/state`
  already carries the new fields.

Unverified on hardware:
- Whether `brightness` dims a strand that is showing a custom slot (individual mode). VWBD saw 50→20 % in steady
  white, but a dim to 20 % in rainbow mode read back without being seen (Sep 28; 04, bench item 7). Until that is
  benched, guest copy shouldn't promise that painted moods dim.
- Turning the canopy back on sends power, the selector and the brightness in one PUT that starts from power-off.
  That case is unbenched (04, bench item 6).

## 2026-10-13 — the show box is a Mac mini

**D61 — The show runs on a Mac mini under launchd, not on the Ubuntu mini PC.** Alon's call on Oct 13: the Mac
mini that ran the workshop work becomes the main machine (07 §10's "Ubuntu 24.04 mini PC" and `venue-box.md` are
superseded for this show; `deploy/venue/` stays for a Linux box or spare). Runbook: `docs/runbooks/mac-show-box.md`.
- **Same runtime, same gates.** `CONDUCTOR_MODE=live`, `HUBSPACE_LIVE=1`, `HUBSPACE_DEVICE_ALLOWLIST=registry`,
  `HUBSPACE_LIVE_UNTIL=2026-11-02`, `CONDUCTOR_ENV=venue`, one uvicorn process, no `--workers`. The settings
  template (`deploy/mac/conductor.env.example`) matches the venue one where they overlap (a test pins that).
- **No Docker.** The Conductor runs from a dedicated clone at the release tag (`~/canopy-show`) with its own
  `uv sync --frozen --no-dev --extra live` environment. The development checkout is never the show.
- **launchd replaces systemd.** Three LaunchAgents: the Conductor (`RunAtLoad`, `KeepAlive`, 30 s `ExitTimeOut` so
  the rotated sign-in is saved), a 30 s `/healthz` watchdog (three failures after a 2 min start-up grace →
  `kickstart -k`), and a 04:30 backup (14 days). `canopy-mac stop --yes` is the break-glass hold: `launchctl
  disable` + bootout + a hold flag the watchdog respects, so it survives a restart.
- **Secrets are generated, not typed.** `canopy-mac install` generates the ops login and the Fernet key into a 0600
  file under `~/Library/Application Support/ColorCanopy/` (outside any checkout). `run-conductor.sh` reads it line by
  line (never `source`), pins `CONDUCTOR_DATA_DIR`, and drops local-only overrides (`CONDUCTOR_AUTH=off` etc.)
  inherited from the parent. `canopy-mac password` shows the ops login only on a terminal.
- **A LaunchAgent needs a logged-in user.** Unattended recovery after a power cut needs automatic login, which needs
  FileVault off; with FileVault on, someone types the password at the Mac first. That trade-off is Alon's (runbook §1).
- **Not carried over:** the Chromium kiosk on the box (the guest panel is the iPad), the `/run/systemd/timesync`
  flag (the 7 am checklist's *Clock synced* item warns "can't see" on macOS; `canopy-mac status` shows the sntp
  offset), and the USB backup copy.

## 2026-10-13 — a partial canopy

**D62 — A canopy position with no Hubspace device registered is left out of every look, the safe one included
(refines D36).** Registering VWBD's two shop kits at `canopy-r0c0`/`canopy-r0c1` on the Mac show box leaves the
other seven positions with no Afero address. Before this, Lowcountry Calm assumed capabilities for all nine and said
"all nine Hubspace strands", a strand fallback said "all two", and module-level copy said the palette steps "across
the canopy column by column" — false on two of nine (CLAUDE.md: kiosk copy must be true).
- **The rule** (`conductor/looks/compile.py` `compile_mode`): with `CompileContext.addresses` (the show), a module
  whose address on the engine's driver is missing is *unregistered*. It goes in `ModePlan.skipped` with "… has no
  Hubspace device registered on this runtime, so it was left out of this look (register one on /ops/accounts)". It
  never gets `SAFE_DEFAULT_CAPS`, and capabilities it still holds from before are ignored. Without `addresses`
  (`EngineCompiler`, the soak, plain compile tests) nothing changes; in sim every module has its sim address.
- **D36 is unchanged for registered modules:** one whose profile hasn't been read is left out of every mode but
  the safe look, which assumes conservative capabilities for it.
- **Nothing registered → no look.** compile raises PlanError naming /ops/accounts and the canopy rests ("getting
  ready"): the guest panel answers 503 resting instead of 409, and the ops Safe look is refused with that reason.
  Before, Calm "went out" while lighting nothing; resting is the honest state.
- **Tiers never mix.** Only the address predicate drops a module. A registered, read module that can't do a tier
  still sends the whole canopy down the ladder (07 §6; motion stays all-or-none over the driven set). There is no
  generic "drop whatever doesn't compile" fallback in `request_mode` or `compile_best`.
- **Copy counts the driven strands.** `_counted` and `_generic_feature` say "both Hubspace strands" for two and
  "one Hubspace strand" / "its one strand" for one ("eight strands", "240 lights" unchanged). At module level (T3)
  with anything left out, the sentence is generated: "Colour and dimming: two Hubspace strands, each set to a single
  colour from the scene." Not "from the scene's palette": each strand shows the scene's gradient sampled at its
  column centre, which is usually a blend (r0c0 is no palette colour in any of the six scenes). The guest panel's
  target label reads "The Color Canopy (2 of 9 strands)" while the look leaves modules out, "(nine strands)"
  otherwise.
- **/ops stays loud.** `ShowWiring.missing()`, `refresh_capabilities()` and `attention()` consider registered
  modules only (no profile read, retry or warning every 60 s for a position with nothing to ask), and `attention()`
  gives each unregistered one its own line naming /ops/accounts, linked there (`attention_items()`). The engine's
  phase stays NEEDS_ATTENTION while any module is left out, the 7 am module check still counts nine, and the T1
  gate still needs slot records on all nine registry modules — so a lost registration on show day is never quiet.
  No opt-in date flag is needed.
- **A registration recompiles.** The capability loop compiles the current look again whenever the set of
  registered modules changes, not only when a profile read brings new capabilities, so a kit registered while Calm
  is showing joins it with assumed capabilities even if its first read fails (D36), and its /ops reason moves from
  "no device registered" to "profile hasn't been read". The ops help text says the Safe look goes to every module
  with a device registered.
- **Not changed:** the engine's runtime paths (`canopy.send`, `active_modules`), the reconciler, the executor, the
  live gate and the allowlist; they already key on a resolved target.

Follow-ups (separate PRs): derive the guest panel's "changing" state and feature line from the driven modules'
readback; an honest Now Showing preview at the strand and module tiers; "N of 9 modules" in the rest of the ops copy
(the commissioning dialog and the timing button still say "all nine modules").

## 2026-10-13 — the scenes as 2-D designs

**D63 — Each scene is one 2-D design over the canopy, and module level samples it (supersedes D59's x / 225 canvas
and column-only T3).** Alon asked for designs that use the canopy's geometry; these layouts are drafts for Paris to
review in the PR. Palettes, names, moods, brightness, ladders, slots and the strand looks are D59's, unchanged.
- **One design per scene** (`config/modes.yaml`): a `field` canvas (`conductor/looks/field.py`, canopy inches in the
  reflected-ceiling view) whose stops are the scene's palette, in order and evenly spaced, painted `rgb_only`. The
  painted three name it once with a YAML anchor, so T1's canvas and T3's sample are the same design.
  - Lowcountry Calm: radial from the canopy's centre, inverted (sea glass in the middle, paling toward ivory at
    the corners).
  - Rainbow Row Sunset (custom-1): linear at 45°, corner to corner.
  - Garden Gathering (custom-2): radial from the middle of one long edge (112.5, 120), inverted (ivory there).
  - Coastal Tides: a tilted shoreline, linear at 78°.
  - Golden Hour (custom-3): a radial sun at the middle of the left edge (0, 60), measured in the canopy's
    proportions (`metric: canopy`, a ring is an ellipse that fills the canopy). In true inches the rows sit only
    40 in apart against an auto radius of 233 in, so T3 read as D59's three columns; in canopy proportions the
    middle-left module is the goldest and the left column's corners are visibly pinker (ΔE76 about 21), so the
    sun's arc shows on the grid as well as at T1.
  - The Goodnight Glow: a radial moon over r0c2 (187.5, 20), inverted (moonlight blue there).
  Radii are `auto` (the farthest canopy point from the centre, in the design's metric) and every design is static
  (`cycles: 1`).
- **T1** paints the design at each of the 270 heads (the palette at `field.value`, head for head). **T3** sets each
  module to the design at its centre (`sample: {at: centre}`, the scene's brightness): 4 to 9 distinct module
  colours per scene, never three columns (in every scene some column changes from top to bottom by at least a
  third of the largest change along a row, in ΔE76; `tests/test_modes_match_scenes.py`). **T2** is still "a custom pattern painted light by light in the Hubspace
  app"; matching a hand-painted slot to the new design needs a per-module painting guide (P12's illustration).
- **Copy.** T1, T2 and strand sentences are unchanged. Each T3 sentence names its layout in the words
  `field.layout_words` requires ("from the centre out", "diagonally", "from one edge", "from one spot") and no other
  layout's, with no movement words. On a partial canopy the T3 sentence is still D62's generated one ("two Hubspace
  strands, each set to a single colour from the scene"), which never claims a canopy-wide layout. On the Mac box's
  two kits (r0c0, r0c1) all six scenes show two different colours.
- **Recipes.** Lowcountry Calm, Coastal Tides and The Goodnight Glow get palette recipes too (D59's rule: the colour's
  name, or `<scene id>-<name>` when taken: `goodnight-glow-lavender`).
- **Kiosk.** `/api/kiosk/modes` draws a field canvas as pattern `design`, and a modules-first mode's tile lists its
  design at each module (through the compiler's own `module_look`) until a plan says otherwise.
- **Slot hashes.** Changing any canvas changes the compiled T1 frames, so every recorded slot hash of a painted scene
  goes stale and the reconciler would report drift. Freeze the designs (this decision merged) before commissioning at
  install, and commission at install rather than relying on show-time rewrites: re-painting after commissioning costs
  27 slot writes, each slot at most once per 10 minutes. No live runtime holds T1 records on a canopy module today, so
  this change triggers no live slot writes.
- **Not changed here.** The Studio's illustration and `config/scenes.yaml` still describe D59's x / 225 strand
  gradient (P12), and the guest panel's preview is P5's. D59's columns stay pinned in `tests/test_compile_field.py`
  as the regression anchor (an x-linear field at the module centres reproduces them byte for byte), so reverting
  this one change restores D59 exactly.

## 2026-10-13 — the ripple arrival

**D64 — A new mood can land ring by ring in its design's own order, with no extra writes (built; off by default).**
Alon asked for looks that move across the 3×3 grid. The cheapest motion the binding rules allow is the arrival itself:
- **What it does.** A mood chosen by a guest, by ops or by the idle hold goes out in up to three rings
  (`RIPPLE_MAX_WAVES`), ordered by the scene's 2-D design (D63) over the driven modules (`field.bands`): the canopy fills
  from the centre out, from one edge, or corner to corner. Each module still gets exactly one full-state PUT for the
  change; nothing is split (07 §6), so the ripple costs no writes. The ring order follows the design's geometry, not
  `invert`/`mirror`/`cycles`, so an inverted radial still lands from its centre.
- **One burst, as before:** the safe look, re-assert, recovery, downgrade/upgrade, guest brightness and power, lock
  release, and any look sent while nothing is showing (start-up).
- **Safety.** A newer mood cancels the rings not yet sent; the reconciler never re-sends a module still waiting for its
  ring; an outage holds the waiting rings. A waiting module reads `updating` ("Waiting for its turn in the ripple.").
  The kiosk preview animates the same rings from `state.lit.motion` (P5).
- **Off by default: `kiosk.ripple_stagger_s: 0`** (today's single burst, byte for byte). Allowed 0–1.5 s with
  (rings − 1) × stagger ≤ 2.4 s, so 1.2 s at most. It stays off because 05's acceptance target ("first to last module
  within 2 s at p95; settled within 5 s") fails by design under a ripple, and `config/sim/acceptance.yaml` is pinned
  (07 §7). The soak runs a separate ripple scenario at 1.2 s: settled p95 5.77 s against (3 − 1) × 1.2 + 5 = 7.4 s,
  one PUT per module per change, no early sends, no downgrades; each ring's own landing spread is p95 2.29 s against
  2 s (reported, not gating).
- **To turn it on:** Alon sets `kiosk.ripple_stagger_s: 1.2` and signs off the per-ring redefinition of 05's spread
  target. Needs real hardware before it is trusted: start skew across kits well under the stagger (bench gate 12), and
  whether a whole-strand colour change cuts or fades (bench item 6).

## 2026-10-14 — scenes that move

**D65 — Coastal Tides and Golden Hour step through the cloud at module level, in bounded runs; live stays off until
Gate R (supersedes D59's "every scene is static" for these two scenes only).** Alon asked for looks that move. The lead's
defaults, in `config/modes.yaml` (Alon can change them there):
- **What moves.** Coastal Tides: ladder `stepped, modules, strand`: the tide rolls diagonally across the panels.
  Golden Hour: `canvas, slot, stepped, modules, strand`: the per-head sun (T1) where it is commissioned; where it
  isn't, sun-ray steps move out from the middle of the left edge at module level; below that its static modules rung.
  Each stepped rung reuses its scene's own design (the same YAML anchor as its modules rung), so step 0 is the still
  module-level look exactly (the schema and the compiler both check it). Lowcountry Calm (the idle look) and The
  Goodnight Glow stay still; Rainbow Row Sunset and Garden Gathering are unchanged. The three slots keep their looks.
- **Why only at module level through the cloud.** 07 §6: one full-state PUT per module per update, never split, so a
  module can only show one colour per step. Slots are store-and-select (04), the controller holds three, and all three
  hold painted scenes, so there is no slot to cycle through; on-device motion codes are unbenched (`effects.enabled`
  is empty). What moves is the Conductor re-sending each strand one colour every few seconds, and the moving copy
  says exactly that ("through the Hubspace cloud … every few seconds"), names the design's flow ("diagonally", "from
  one edge"), and is shown only while a run moves. On a partial canopy it only counts the strands it drives (D62).
- **Cadence.** Designed at 6 s a step, 6 steps a cycle. The runner stretches it to the account's budget: the cap of 90
  writes a minute less the 20% reserve (`motion.stepped.budget_reserve`) is 72, so nine modules on one account step
  every 7.5 s. Never faster than 4 s (`min_step_s`; a step's verification takes about 2.6 s), never slower than 12 s
  (it holds still instead), and a step that would take the account past 90 in any 60 s is held back.
- **Bounded runs.** A run lasts 90 s (`run_s`, at most `kiosk.idle_return_s`), then the still design holds. The idle
  return never moves for a run, and the idle look never steps (the show refuses to load otherwise).
- **Live off until Gate R.** `motion.stepped.live_enabled` stays false: on any non-simulated driver the show's
  `tier_allowed` refuses the stepped rung, so Coastal Tides shows its still modules rung and Golden Hour its canvas,
  slot or modules rung, with nothing stepped (tests/test_d65_modes.py proves it through the show's own wiring on a
  live-named driver). Only the stepped rehearsal opens it, for its one mode while it runs
  (`docs/runbooks/stepped-rehearsal.md`). Alon flips the setting after a pass on the show kits.
- **Verified (simulator only).** The main soak now steps Coastal Tides whenever it is chosen (19 of 167 accepted
  changes) and still passes every pinned row (`config/sim/acceptance.yaml` unchanged). The stepped scenario runs both
  scenes' own rungs first: 124 steps over 12 runs at 7.5 s, none superseded, failed or skipped, one PUT per changed
  module per step, at most 72 program PUTs and 89 writes of any kind in any 60 s on the account (cap 90), no governor
  trip, no downgrade at 5% throttling. Each step's landing spread is p95 2.78 s against 05's 2 s (reported, not
  gating, until Alon signs off a per-step target).
- **Needs real hardware.** Gate R (the rehearsal's 429/403 share and write rate from the venue IP, vignettes clean),
  whether a strand cuts or fades between colours (bench item 6), and start skew across kits.
- **What would change it.** A Gate R fail (it stays off), or Paris or Alon preferring other scenes to move. In a show
  run, throttling stops it on its own: the governor ends a run at 8 or more 429/403 answers that are a quarter of its
  writes in 60 s, and stepped looks on that account hold still for 5 minutes. A hysteresis downgrade never lands
  on a stepped rung: from Golden Hour's per-head sun (or its slot) it passes over the stepped rung to the still
  modules rung, and the floor lifts back past it, so easing off never starts a run (`CanopyEngine.downgrade` /
  `upgrade`).

## 2026-10-16 — the Design Lab

**D66 — The Design Lab: edit the scenes on screen, try them on at module level, approve through git (a freeze
exception; Slices A, B and C built).** Alon asked for a pattern and animation admin so he can iterate the canopy's colours and styles with the
client (Home Depot / Hearst, with Paris from VWBD). We are past the Oct 12 feature freeze
(`docs/runbooks/release-and-freeze.md`); this is the exception, granted by asking for it. Brief and plan:
`docs/design-lab/PROMPT.md`, `docs/design-lab/PLAN.md`.
- **Slice A (built; screen only; no show-path file touched).** A Lab panel in the Studio (`/ops/showcase/#lab`, ops
  only; `/api/lab/*`). A draft is a patch over the parsed `scenes.yaml` / `modes.yaml` / `recipes.yaml` that moves
  every rung of one scene's mode in lockstep (T1 canvas, slot brightness, module sample, stepped design, strand solid,
  preview, recipes); ids, ladders, slot ownership and `lowcountry-calm` never move. The preview goes through the real
  compiler with the draft's own recipes (`mode_design`, `compile_mode`) and shows each tier plus "what this simulator
  shows" / "what the lights can show today" from the running show's registry and bench flags, and a pinned "Show box
  today (Modules 1–2)" view until the Oct 19 undo. Versions are named `Client <Mon D> v<N> – <note>` (a number is
  never reissued) and kept in `<data>/overrides/looks.yaml` (validated, history copies, atomic 0600). A corrupt,
  oversized or unreadable file is set aside and flagged on /ops; the show never reads the file. Export is a reviewed
  git patch of exactly four files (`config/scenes.yaml`, `modes.yaml`, `recipes.yaml`,
  `tests/fixtures_scene_frames.json`), rewritten in place (comments and anchors kept), re-read through the real
  loaders before it is offered, with a drafted DECISIONS entry. Approved looks reach the show only through git →
  merge → `canopy-mac install`; nothing hot-applies config to a running show.
- **Defaults taken** (the three questions Alon hadn't answered): an approved palette colour (D59) can change, as a
  labelled draft whose export says "changes an approved colour — needs the client's sign-off and a D59 update"; Present
  runs in Alon's laptop browser as ops (the Test iPad stays the guest panel); version names as above.
- **Copy stays true and is never free text.** Each kiosk sentence is the approved one while the design is unchanged,
  else generated from the layout's own words and the colours some strand actually shows. The client copy (name, mood,
  description) refuses motion words and light counts ("270", "nine"), because nothing rewrites those for a partial
  canopy (D62); a description the colours moved under gets a note, never a refusal.
- **Locked to D63 in v1:** a design's `cycles` (1) and the module sample (`at: centre`), which the suite pins. On live
  hardware, once a painted scene is commissioned its design is read-only in the Lab ("changing this needs a slot
  repaint at commissioning", 07 §6); its brightness, solid, copy and stepped timing stay editable. In the simulator the
  lock is off and the Lab says what would happen. D63's design-quality rules (`two-d`, `kits-differ`) warn in the
  preview and block only the export. An export that changes a painted scene's design says "Deploy only in a
  commissioning window" (slot repaint).
- **Approved-value pins** in the tests moved to `tests/fixtures_scene_frames.json` (`_pins`), which the export
  regenerates (or `make scene-pins` after a hand edit), so an approved edit passes the suite with only that fixture
  changed; compiler tests compile against frozen copies of the Oct 16 scene recipes.
- **Where a client session runs:** Alon's laptop (`make lab-dev`, sim, port 8200); Railway staging with an
  ops-signed-in iPad once this is on main; the show box only after the deploy.
- **Cut (follow-ups):** the Studio's own scene tiles and shop-kit buttons still read `demo-model.mjs` `SCENES`; each
  export's review notes name a colour that would go stale there.
- **Option for Alon, not built:** a read-only "client viewer" role for Present on the client's own device. It would
  change the binding 07 §2 role table.
- **Out of scope for v1:** hot-applying config, slot repaints and firmware presets from the Lab.
- **Slice B (built Oct 16; try-on at module level through the engine).** One tap tries a draft of one scene on the real
  lights: `POST /api/lab/trial/start` builds an engine `Trial` from deep copies of the show's own trio patched by the
  draft (its own modes **and** recipes), and `CanopyEngine.start_trial` shows it with `request_mode` source `"lab"`,
  one burst (no ripple, whatever `kiosk.ripple_stagger_s` says). `ShowWiring.compile` uses the draft only for that
  scene's `modules`/`strand` rungs, and `compile_best` skips T1/T2/stepped for it while the try-on stands, so a painted
  scene is tried on at module level and says so. The ladder, every T1 compile, `t1_target`, `slot_expectations`,
  `stale_slot`, `adopt_slot`, `rewrite_slot` and commissioning keep the git config: no slot is written or judged
  stale because of a try-on. Ops confirms a sentence naming every driven module and the bound (409
  `confirm_required` until `confirm` equals it) and sees the cost ("sends N writes now, up to ~4N over the next
  10 s"); the server keeps accepted applies at least `kiosk.cooldown_s` (8 s) apart (a double tap is refused with
  `retry_after_s`). A try-on ends at `min(lab.trial_max_s, kiosk.idle_return_s)` (150 s; the setting is validated
  ≤ `idle_return_s`; re-applies never extend it), on **Back to approved**, and before any other source's look is
  compiled (ops, idle, the safe look, commissioning, a rehearsal); pause, stop and commissioning clear it, and a clear
  whose own look then fails puts the approved look back. It is never persisted: a restart comes up clean. It is
  refused while the engine is stopped, commissioning, paused, resting (outage) or rehearsing. Guests are refused with
  "Staff are trying a new look. It will be back shortly." (503 + `retry_after_s`) on mood, brightness and power; the
  sentence is true only while the try-on runs: the guest panel's state carries `waiting: "try-on" | "testing" | null`,
  a lane refusal under the title is dropped once its flag lifts (and on an accepted scene), and the iPad drops a
  waiting toast at the first read after it that says nothing waits (review fix, Oct 17). A re-pick (the reconciler
  after new capabilities) keeps the try-on, unless it would ADD a module to the try-on's plan (one registered, or
  newly read, mid-try-on: the confirm never named it): then the try-on ends and every module gets the approved look
  (second review fix, Oct 17). A re-apply keeps the running session's bound, so it is confirmed against the time left
  ("… for the 110 s left of the running try-on (a re-apply never extends it)"; a sentence shown up to 30 s earlier
  still matches; the cost line adds "the running try-on still ends in N s"), and it is refused (409, `retry_after_s`
  = the time left, nothing sent, the gap not used up) with less than `kiosk.cooldown_s` of the bound left, or when
  the session it was confirmed against ended or another began while it was built. An exception (not a refusal)
  while a try-on starts or ends clears the override and its bound task, puts the approved look back (the safe look
  if that raises too), raises an alert, and the route answers a plain 500. The confirm sentence names the draft only when it renames the scene
  ("Try on your draft of Golden Hour …"); TrialJSON's `label` is the approved scene's name and `draft_name` the
  draft's. The Lab's preview "today" shows the try-on's installed plan while it runs. Every write goes through the engine and the
  executor; `reconciler.py`, `executor.py` and the drivers are unchanged, and the Lab never flips
  `motion.stepped.live_enabled` or `kiosk.ripple_stagger_s`. The try-on doesn't apply the T1 lock (it never writes a
  slot), so a locked painted design can still be tried on at module level. The iPad's scene cards now rebuild when a
  card's contents change (`cardsKey`), not only its id. Telemetry `kind="lab"`: the engine records `trial_start`,
  `trial_apply`, `trial_end` (with `why`); the route records `trial_refused` and `trial_stop`.
- **Slice C (the motion editor; built Oct 17 from C1 server and C2 page, screen only, no show-path file touched).** For Coastal Tides
  and Golden Hour (the two scenes with a stepped rung) a draft edits the direction, `steps_per_cycle` 3–12, `step_s`
  4–12 in half seconds and `run_s` from one step to `min(180, kiosk.idle_return_s)`; each bound is refused with its
  field (`stepped.<name>`). The other four scenes refuse a stepped edit (no rung to add), and the idle look says it
  never steps. The preview's `stepped` is the compiled stepped plan exactly (`compile_mode(scene, Tier.STEPPED, …)`
  over the design context) with its real pace: `pace.nine` 7.5 s and `pace.today` 6.0 s on the show box's two kits
  at the 6 s design (`stepped.effective_step_s`: the runner's rule, copied because `sequence.py` is a show-path file,
  and pinned equal to a real `ProgramRunner` on the engine for nine and two modules, designed and edited steps, four
  budgets and a ripple). Its moving copy is the mode's schema-checked `moving_feature` (approved, or
  `STEPPED_TEMPLATE` when the design or the flow moved; the two-kit view gets D62's count). It shows
  `STEPPED_LIVE_OFF` and "Alon sets motion.stepped.live_enabled after Gate R", and has no control for it. `rings`
  are the show's own ripple bands (`field.bands` of `design_field(mode, T3)`, equal to every tier's plan `waves`
  and the engine's `_rings`), for a screen-only animation at a 0–1.2 s stagger labelled "the canopy's ripple is off
  (kiosk.ripple_stagger_s: 0) until Alon signs off (D64)". The catalog adds the editor's limits, the live lines and
  "Effects — not enabled: no code has passed the bench (06 #4)". An exported stepped timing's D65 line names the
  pace ("nine strands on one account step every 10 s (6 steps, then the still design holds)"), and modes.yaml's
  `step_s` comment follows the new pace. The Lab still never changes `motion.stepped.live_enabled` or
  `kiosk.ripple_stagger_s` (route, AST and config checks).
  **The page (C2):** the editor's Motion section has direction, steps per cycle, step time and run length (bounded
  as above, a run never shorter than a step), a run line ("A 90 s run — Nine modules (7.5 s): 12 steps · Show box
  today (two modules, 6 s): 15 steps; then the still look holds"), the live line and the greyed effects card; a
  scene without a stepped rung shows why, read only. Under the picture, **Play the steps** plays the server's
  compiled steps at the server's pace as `ProgramRunner` schedules them (step n shows `steps[n mod cycle]` from
  n × step, ⌊run_s / step⌋ steps, then the still look holds), with pause, stop and a step counter; a toggle picks
  the nine-module pace or the show box's (only Modules 1–2 lit, the rest dark); a 4× fast preview is labelled "not
  the real pace" wherever it shows; the client never estimates a pace, and a pace the server says would hold still
  (`runs.*.still`, which includes a run that fits only step 0, the still design itself) is not played. The moving
  sentence shows only while the steps play (on the stage, the tier list and Present; at the show box's pace the
  stage and Present show the partial canopy's, D62, and the tier list shows both, each labelled). Golden Hour's
  Motion card, editor and Present say it steps only where its painted rungs can't run (the server's `steps_when`).
  In A/B the run ends when the longest side's does. An approved `step_s` off the half second (valid for the
  schema) loads; only an edit moves in half seconds. **Play the ripple** lands the server's rings at a
  0–1.2 s stagger slider on the screen only, under "Screen preview only — the canopy's ripple is off
  (kiosk.ripple_stagger_s: 0) until Alon signs off (D64)". The live line is the server's `STEPPED_LIVE_OFF` and note
  word for word, and once Alon sets `motion.stepped.live_enabled` every Lab line (live line, show-box pace note,
  the export's D65 section) says it is on. Present plays the steps too (button or Space).
  `docs/reports/sim-acceptance.md` is regenerated and committed with this slice (plan §1).

## 2026-10-17 — canopy only

**D67 — The Conductor controls the canopy only (supersedes the vignette parts of 05 #11/#13, 06 #11/#18, 07 §2's
vignette role, D34's room pages and P4; the Ubuntu venue box of 07 §10 and D61's "deploy/venue/ stays"; the VWBD
workshop kit, D56's workshop home and the Studio's shop-kit commands).** Alon's call on Oct 17 on site, after the
guest panel worked on the two live kits: "remove all the stuff that isn't the canopy control". Plan:
`docs/live-lab/PLAN.md` §1.
- **Gone:** the four vignette rooms (pages, service, routes, `config/vignettes.yaml`, their 15 registry entries and
  four accounts, the `vignette` role and its enrollment, the vignette WebSocket topic, the synthesized vignette sim
  profiles, Gate R's "vignette accounts clean" criterion); the Ubuntu venue box (`deploy/venue`, `scripts/release.sh`,
  the Chromium resting page, `venue-box.md`); the VWBD workshop kit and Paris's tooling (`workshop/`, the launcher,
  the kit builder, TEST-CENTER and its inventory, the Windows geometry tool, the Oct 2 hand-off docs — all in git
  history at `6ce971b`); the guest panel's workshop target and `make guest-dev`; **every live shop-kit control in the
  Studio** (Alon's override of the plan's default, Oct 17: the Studio is screen only) — the "Both modules" pair and
  paired gradient (`/api/studio/pair/*`, `pair.py`, `scene_gradient.py`), Connect, Apply scene gradient, Apply
  selected solid, brightness/power, the module select, the "Not connected" indicator, the fine-tune looks/colour/
  white/effect tools, the saved-Custom-3 button and the motion demo's live run.
- **Kept:** everything the canopy needs (engine, executor, drivers, live gate, accounts, registry, bench gates and
  Apply map, `/ops/module` unchanged, /ops, the guest panel, the visualizer, the Mac show box, the soak), the Studio
  as a **screen preview** (the six scenes over the full canopy from the show's own design, the tour, the angle and
  level toggles) with its Design Lab panel — **the Lab is the only live control** — the Dockerfile (CI builds and
  smoke-tests it), Railway staging, and every hardware evidence file. `CONDUCTOR_ENV=venue` remains the Mac's
  environment name. Home (`/`) is the visualizer everywhere.
- **Old data is tolerated, not migrated:** a saved enrollment or session of the removed role is skipped (the kiosk
  iPad's session survives; an unreadable record of a role that exists still sets the file aside); a registry-overlay
  entry for a removed vignette device (`REMOVED_DEVICE_IDS`, or any `role: vignette` entry), and a `vignette`/`kind`
  key — or a `profile` naming a removed vignette sim profile (`REMOVED_PROFILES`) — on a surviving one, is ignored
  with a "Needs attention" line and pruned by the next /ops registry edit; a leftover `studio-gradients/` folder is
  ignored. `canopy-mac install` runs `conductor.preflight` against the data folder (through `run-conductor.sh
  --check`; it also builds every registry entry's sim profile, as startup's SimDriver does in live mode too) before
  it restarts the show, so new code that can't load it never restarts the show. A rejected checkout is still the
  checkout, though, and the running Conductor is a KeepAlive job: install then exits 3 and prints the rollback, and
  the paste block (`release-and-freeze.md`) rolls the checkout back at once — any restart would start the rejected
  code. The vignettes, if the activation still has them, run on the stock Hubspace app outside this
  system.
- **Contract text changed:** 07 §1 (the show box, the writer list), §2 (the role table), §8 (the data folder's
  home), §10 (the Mac); 05 #3, #11, #13; CLAUDE.md's opening line and the show-accounts rule (applied in PR A, for
  Alon to review there).
- **Modules 1–2 for real (Alon, Oct 17):** VWBD's two shop kits are real canopy kits — Kingston test is Module 1
  (`canopy-r0c0`) and String light 1 is Module 2 (`canopy-r0c1`), on VWBD's Hubspace account, which is the live show
  account (logical account `bench`). The Oct 19 registry undo that the Oct 13 test set-up planned (STATUS) is
  cancelled; D66's "Show box today (Modules 1–2)" view now shows the real Modules 1–2.

## 2026-10-17 — the live Lab

**D68 — The Design Lab publishes to the live show (supersedes D66's "nothing hot-applies config to a running show",
"approved looks reach the show only through git → merge → install", the T1 lock and "out of scope: hot-applying
config, slot repaints"; extends 07 §6 and §8).** Alon, Oct 17: "make the test lab work on the live system so that I
can just think of everything as one system and we can live iterate on site on the live show at the end of the install
before guests arrive." Plan: `docs/live-lab/PLAN.md` §2 (server), §3 (the Lab page).
- **Publish.** A saved Lab version (or **Revert to approved**, git's looks, or **Undo**, the previous publication's
  stored trio) becomes the show's look at once and after every restart: validated (schema, copy honesty, stepped and
  idle rules, git's ids/ladders/slots/motion), written to `overrides/show-looks.yaml` before it is installed, swapped
  into every in-process copy in one synchronous step (`conductor.looks.live.install_looks`: the show's modes and
  recipes, the rehearsal designs, the T1 plans, the module page and its open sessions, the Lab's caches; the kiosk,
  guest panel, /ops, alert labels and the design route read the live trio per request), and the engine re-picks the
  current look (`CanopyEngine.reload_looks`, source `looks`: one burst, guest brightness/power kept; nothing is sent
  when the scene showing didn't change and no try-on runs). Ops confirms the server's sentence (what it replaces,
  the scenes that change or go back to approved, the modules it reaches now, the "Show <scene> on the canopy after
  publishing" switch when that box is ticked — on by default for a version, off for Undo and Revert; a toggle asks
  the server again —, the holds, "the running try-on ends") and sees the cost; a publish computed against a look someone else has since replaced is refused as a conflict
  (`expected_live`, with the new LiveJSON); the publisher's name is recorded. Refused while the engine is stopped,
  commissioning, resting (outage) or rehearsing (Gate R), and while another publish runs; allowed while guest input
  is paused, and while config/ on disk differs from the git the show loaded (a deploy under way). The Lab builds on
  that same git (one git per process: the trio, sha and canopy the show validated at start). An exception after the
  write rolls back: the previous file byte for byte, the previous trio and the state it described (a fallback stays a
  fallback, with its /ops line and alert), the T1 hold recomputed under it, a re-pick under it (else the safe look),
  an alert naming what really runs (resolved by the next good publish), a plain 500. Routes: `GET /api/lab/live`, `POST /api/lab/publish`;
  deleting the live version is refused (409).
- **Fail-safe.** A missing file is the approved looks; an unreadable or invalid one (too big — never read —, not
  YAML, anchors/aliases, too deep, a bad top level or format, a version without its trio, a trio whose `trio_sha`
  doesn't match, or one `validate_trio` refuses) is the approved looks plus an /ops line and a warn alert — never a
  failed start — and is left exactly as it was. **Revert to approved** clears it: the show already runs git, so
  nothing is sent, but the unusable file is replaced by `current: {kind: approved}` (a copy kept in
  overrides/history/) and the /ops line and alert go. A published trio that equals git is followed as git (persisted as
  `current: {kind: approved, name: "approved (in git)"}`), so a later deploy is never masked; one that differs from a
  changed git raises an /ops warn ("… masks config/ changes deployed since …"). `conductor.preflight` (run by
  `canopy-mac install` before the restart) reads the file through the show's own load (read only) and names one it
  can't use, without failing the deploy (the show would start on the approved looks).
- **Painted scenes: copy stays true.** A painted scene whose commissioned (api) slots don't hold the live design runs
  below T1 (`CanopyEngine._t1_hold`, skipped in `compile_best`, never in `tier_allowed`, so the reconciler still
  sees the slot as stale and rewrites it) until a slot record makes it current, then one T1 re-pick; the hold is
  seeded again whenever capabilities are applied (a restart after a publish or a fallback). A design change over a
  hand-painted ("app") slot is refused on every runtime (preview, save and publish). **Delivery:** in PR B (B1),
  **on hardware**, a design change over commissioned slots is refused except for Revert and Undo (the scene is
  held; the reconciler's stale rule rewrites the slots, ≤ once per 10 min per slot, then the hold lifts); **in the
  simulator** it publishes the same way Revert does (held, then rewritten) — as D66's T1 lock was off there — so a
  laptop or staging Lab can iterate on every scene. In PR B2 the operator repaint lands (one slot write per module
  through the executor in a quiet window, only on modules with their own Gate C, map and api record, at most once per
  2 min per scene, `lab.repaint_min_interval_s`; a sooner publish installs at once and queues it; a revert is never
  blocked). On today's two kits (no Gate C, nothing commissioned) nothing is repainted and the design shows at module
  level.
- **PR B2 — the operator repaint (built Oct 17, `docs/live-lab/PLAN.md` §2.6–§2.7; 07 §6's "Operator repaint").**
  B1's api lock is gone (on hardware and in the simulator alike); the app lock stays. A publish whose painted scene's
  commissioned slots won't hold the new design installs at once (the scene is held below T1) and hands the engine
  ONE job (`CanopyEngine.start_repaint`): the scenes whose eligible modules — their own api record, Gate C at their
  address and, on a longer frame, a valid map (`ShowWiring.repaint_plan`, per module: one that can't take T1 drops
  out alone) — have a record that differs, plus any scene of a queued job it replaces. The job's (device, slot) pairs
  are marked pending at once; the reconciler's `check_slots` skips them (its only edit) and the scene stays held while
  any is pending. When a scene comes due the engine opens a quiet window (the public `commissioning` flag reads it, so
  the reconciler, idle hold, stepped runner and capability re-pick hold still; guests, ops, a try-on and Gate R are
  refused with "One moment: the canopy is changing." and a retry-after — ops' Re-assert all and the Lab's try-on
  panel name the repaint, never "commissioning" (review 5); a running try-on ends first), waits for the
  commands in flight, recomputes the target from the live looks, and writes each listed module's slot under its
  `_slot_lock` through `_commission_plan(min_interval=0, only=…, verb="publish repaint", locked=True)`: header from
  the readback, `slot_written` stamped at submit, at most two retries only when a write didn't land, never over an
  app record or a module held by the module page, the readback recorded as commissioning records it (the registry
  overlay), so the reconciler never calls the published design stale. A fully applied scene's hold lifts: one T1
  re-pick, the re-select after rewriting the active slot. A module that failed or was locked keeps the scene at
  module level until the reconciler's stale rule (≥ 600 s after the attempt) or a commissioning makes it current.
  **The 2-min gate** (`lab.repaint_min_interval_s: 120`): enforced inside `start_repaint` (monotonic, from the start
  of a scene's last operator writing); a sooner publish is never refused — it installs and queues (`in_s` in the
  confirm, which may tick down by up to one interval while ops reads it); a newer publish replaces a queued job (the
  due time stays); Revert and Undo queue the same way. The overlay keeps `repaints` (the gate) and `queued` (the
  job), so a restart re-queues it as the engine starts, before any profile is read (review 5: the gate seeded from
  the wall times, clamped to one interval, so a clock step can't stretch or skip it; the queued scenes pending at once
  over the api-record modules, so a module unreadable at boot opens neither the gate nor its slots to the reconciler,
  and a publish carries them over; a window opens only after the show's first capability round); in the simulator
  the start-up commissioning writes the live design itself, replaces the restored job, and the queue settles. A job's
  scenes run one after another in due order. A publish is refused only while a repaint is writing ("repainting"); commissioning is refused while a job
  is queued or running. The Lab reads the job as RepaintJSON (`LiveJSON.repaint`: `queued_until`, `started_at`,
  per-module status) and the history row's `repaint` counts. **Deploy B2 only after bench item 6** (rewriting the
  showing slot, then the re-select) has been run once on a show kit with its own Gate C.
- **Git stays the record.** The Lab's export of the published version is the PR that approves it; once merged and
  deployed the overlay equals git and is followed as git. Until then a deploy doesn't change a published look (the
  /ops warn names the masked change); Revert to approved takes git's.
- **The try-on stays** (temporary, module level, from the live trio); a publish ends it. Kiosk copy stays true: every
  published sentence passes the same rules as git's, the iPad's cards follow the live catalog, and the tier in use
  matches the slots. The Studio (screen only, D67) shows the scenes as the show runs them (`GET /api/canopy/scenes`),
  re-read after a publish; demo-model's SCENES is only its fallback.
- **Not changed:** `motion.stepped.live_enabled` (Gate R), `kiosk.ripple_stagger_s`, ladders, slot ownership; the
  Lab never changes them. `<data>/overrides/looks.yaml` (the Lab's version store) is unchanged and the show still
  never reads it; it reads only `show-looks.yaml`.

**D69 — Input to reaction on the panels (the panel-latency pass, Oct 18).** Alon asked to cut the delay between a
guest's input on the iPad and the reaction. Measured first, on the simulator (`make lab-dev`, port 8200): the server
answers a tap in ~7 ms and sends the burst without waiting; the lights are the cloud's time; the delay was in how the
panel and the engine handled input. What changed (the rate budget, the cooldown's length and every contract in 07 §6
are unchanged):
- **A brightness or power change inside the guest cooldown waits, it is never dropped.** The engine's 8 s cooldown
  covers every guest input (D60), but the guest panel answered 200 and its lane then got refused: the slider snapped
  back after 3 s and the lights never took the guest's level (the panel spec and the API both say brightness and power
  "still work" in cooldown). Now the lane holds such a change until the cooldown ends (`Lane(hold=…)`, latest wins,
  one burst), so guest bursts stay one per cooldown and the account budget holds. The panel says so truthfully:
  phase `cooldown`, "Your brightness is next. It reaches the lights in a moment." / "The lights turn off (on) in a
  moment.", and `state.next` (`{brightness?, power?}`) keeps the slider and switch where the guest put them. The engine
  itself is unchanged (its D60 test still pins the refusal); only the guest panel stopped asking inside the cooldown.
- **The slider sends once, on release** (and a tap on the track is a release). Its paced sends (first touch, then 1/s)
  started the cooldown with the value under the first touch, so the guest's real level waited a whole cooldown;
  `brightnessToSend` skips a release that changes nothing. (Supersedes guest-panel.md §2.7's "every 250 ms".)
- **The panel reads its state the moment the server pushes a change.** It subscribes to the `canopy` WebSocket topic
  it was always allowed (07 §2's ticket flow, `connectLive`) and uses each event only as a nudge to `GET
  /api/kiosk/panel` (never its data: that is the ops view). The 2 s poll stays as the fallback, so a dead socket costs
  speed, never correctness. Reads are single-flight with a re-read when a nudge lands mid-read; every request has a
  timeout (6 s read, 10 s write) so a hung fetch can't stop the poll loop or leave a card pending. Tap to "ready" on
  the panel: 2.0–3.9 s (avg ~2.8 s) before, ~2.2 s after, on the simulator — the server's own readback plus ≤ 0.25 s.
- **Delivery.** `/kiosk/*` and `/static/*` are served `Cache-Control: no-cache` (a 304 on the LAN), and the kiosk's
  modules load with one version tag (`?v=latency-1` on kiosk.js and its imports): before, the browser guessed a
  freshness lifetime, so an iPad could keep old code for hours after a deploy or, worse, run a new kiosk.js over a
  cached older module.
- **The bearer is refreshed ahead, off the burst's path.** The AferoDriver's token keeper refreshes a connected live
  account once it has < 30 s left (`PROACTIVE_REFRESH_LEFT_S`), so the burst's own check (≥ 15 s, 07 §6) finds it fresh
  and ~13% of guest bursts no longer wait on a Keycloak round trip + users/me + the encrypted save before the first
  PUT. Never in sim mode, never for an account needing a sign-in (07 §3), one try per 30 s while it fails.
- **A newer command never waits on an older command's readback GET on the wire.** The executor stops waiting for it,
  resolves the older command SUPERSEDED and sends the newer one at the rate budget; the GET is left to finish and its
  answer discarded — never cancelled, since aioafero may be refreshing the token inside it.
- **Small CPU on the hot path:** redaction indexes the registered secrets by their tail instead of checking every one
  on every log line (the set grows with every token rotation: ~0.63 ms a call after 12 days, now ~0.014 ms, the same
  output — a randomized test compares it with the old pass); the iPad's preview skips its 270-dot layout on every
  animation frame of a ripple or stepped run.
- **Not changed, and why:** `kiosk.cooldown_s` (it is what keeps guest bursts under the account's write budget),
  `readback_delay_s` (the honest "ready" depends on real readback timing: bench it before shortening), the 2 s poll,
  ripple/stepped settings. **Needs hardware:** the real PUT-to-light time, how often bursts used to hit the inline
  refresh, and the cold-connection cost of a 9-wide burst (about 3 connections stay warm between bursts).

**D70 — Guests are paced by the account's write budget, not a fixed 8 s (supersedes D21's fixed cooldown; 07 §6's
kiosk constant).** Alon asked whether the 8 s between presets is an API rule and to cut it for everything. It is not:
Afero publishes no cooldown; the only API guidance is ~1 write/s per device (03), which the executor already enforces
per device, and the account limit is unpublished — we plan at 90 writes in any 60 s (06 #27, unmeasured until Gate R).
The 8 s was a product/safety number. Now, after an accepted guest input (a mood, a brightness or an on/off), the next
waits `guest_gap_s`: one write per module the look drives, counted on the account that carries the most, × 60 /
`kiosk.guest_writes_per_min` (72: the 90 cap less the 20% reserve the stepped runner also keeps; never above
`motion.stepped.account_writes_per_min`), and never less than `kiosk.cooldown_min_s` (2 s, so a look lands and reads
back — ~1.4 s on the kits — before the next). **Two strands (today's Modules 1–2): 2 s. All nine: 7.5 s.** Ops, idle
and the reconciler bypass it as before; the guest panel's lane (D69) waits out whatever gap is left; the Design Lab's
try-on keeps its own 8 s apply gap (`lab.apply_min_gap_s`), and the soak keeps its seeded schedule (≥ 8 s apart). A
guest mashing every control for two minutes on nine modules: accepted inputs ≥ 7.5 s apart, the account ≤ 90 writes in
any 60 s (`tests/test_guest_budget.py`). Not done: letting a quiet canopy take a quick second change (a token bucket)
— it only helps until the budget runs out and makes the wait after a burst longer; revisit once Gate R measures the
real cap. The brightness-readback re-send (STATUS, PR C) costs a second write per module on many changes; fixing it
would let the budget pay for more changes.

**D71 — A first readback that differs is echo lag, not a drop (Oct 18).** Live telemetry from Oct 17–18 (Modules 1–2)
showed the first verification GET still holding the previous scene's rows on 33 of 56 scene changes and both guest
brightness changes, ≈ 1.2 s after the ack. Brightness and colour were wrong together. Re-sending the identical rows
then read back correct, and the same transitions sometimes read fresh at the first GET. So this is the cloud's echo
lagging, not a clamp or a silent drop. Every one of those 35 commands was cut short by the engine's early re-send, so
none ever got its own 2nd readback; on Oct 17, back-to-back re-sends ~1.2 s apart stayed stale up to 3 times
(5.7 s). Decision: **a change's first early re-send waits for the command's 2nd readback that still differs**
(`EARLY_REASSERT_FROM_READBACK = 2`). Once a module has been re-sent in a change, a differing first readback
re-sends at once, as before, so three drops in a row still heal in ≤ 10 s (07 §6). The usual case now costs no
extra PUT (or nine-module motion burst) and verifies at ≈ ack + 2.2 s instead of ≈ 2.6 s via a re-send. A real drop
is re-sent ≈ 1.2 s later than before. Readback events now carry `n` and `differs` (sent vs read, for every row but a
slot object), so the next live run shows the old value directly. Not changed: `readback_delay_s` /
`readback_interval_s` (bench the lag first: `docs/reports/readback-echo-lag-2026-10-18.md`), the re-send limits,
MISMATCH handling, 07 §5's meaning of "ready". This is the brightness-readback re-send D70 names: the write it saved
goes back to the guests' budget.

**D72 — Fast guest pacing: 2 s whatever the canopy's size, backing off only when Hubspace throttles (Alon, Oct 18;
a switch over D70).** Alon asked how low the full canopy's gap can go and called D70's 7.5 s cushion. The evidence:
the only published Afero limit is ~1 write/s per device (the executor enforces it whatever the guests do); the
account's 90 writes/min was chosen on Oct 13 so nine modules would step every 6 s (D65), never measured; the 20%
reserve made 7.5 s; the show box has never sent more than 15 writes in a minute and has never been throttled. So
`kiosk.fast_pacing` (**on**, Alon's call): every guest input waits only `kiosk.cooldown_min_s` (2 s), nine modules
included; while ≥ `kiosk.fast_backoff_throttles` (2) write attempts on an account were answered 429/403 in the last
60 s, guests there wait D70's budget gap again, until `kiosk.fast_backoff_s` (120 s) after the last one, with one
info alert per back-off (503/504 are an outage, never a throttle). `fast_pacing: false` restores D70 exactly. On
today's two kits both give 2 s: the switch matters once all nine strands are driven.
- **What it costs, in the simulator (known, accepted).** Six seeds of the pinned soak (nine modules, 5% random 429s,
  1% silent drops, 150–800 ms acks): mode-change settle p95 averages 4.89 s at 2 s vs 4.64 s under D70; the gating
  rows fail on 4 of 6 seeds vs 1 of 6 (seed 3 fails under every setting); a 3 s floor averages 4.64 s but still fails
  3 of 6. The cost is only in the button-mash burst: at 2 s a new change lands while the previous one is still being
  retried and verified, so each settles slower, and one out-of-band change found then took 20 s to heal (seed 4; 9 s
  under D70). Normal-paced choices (≥ 8 s apart) are unchanged. `docs/reports/sim-acceptance.md` now reads FAIL
  (seed 0: settle p95 5.19 s). The kits are far kinder than the simulator (acks 0.18–0.34 s, no throttling seen), so
  live it may never show — watch the settle times and the heal alerts in install week.
- **The first real number:** `docs/runbooks/account-rate-probe.md` — both kits at their per-device limit for 3 minutes
  (≈ 120 writes/min on the account), run by Alon. Throttled → set `fast_pacing: false` until the nine kits are
  measured.

**D73 — A module whose only problem is a flagged note isn't "changing" (Oct 18, found live).** After D72 went live
(2 s guest gap), Alon still saw ~8–12 s between scenes: the iPad said "One moment, the lights are changing". Live
telemetry: both kits confirmed each scene at ~1.4–2.6 s with no mismatch, and the next scene got through 0.17 s after
a routine true-up read ~12 s in. Cause: the show flags D54's capability note on each kit ("the controller reports 60
entries for 30 heads and this kit has no proven map yet … per-head frames refused; whole-strand looks still work") as
a tracker problem, so a confirmed module reads NEEDS_ATTENTION; the engine's phase counts a NEEDS_ATTENTION module as
"changing" until it is "stuck" (`reconciler.downgrade_after_wrong_s`, 10 s, timers reset by every new look) — the
retry window meant for a readback that still differs. The guest panel's pending scene clears only when the phase
leaves "changing", so every scene held the panel ~10 s, whatever the cooldown (the old 8 s cooldown hid it). Now
`tracker.flagged_only(address)` — needs attention only for flagged problems, the look sent, read back and matching —
makes the module "stuck" at once: /ops shows the note immediately (true), the guest panel goes to cooldown/ready as
soon as the kits confirm, and a readback that really differs still gets its 10 s of "changing" while the reconciler
retries. Reproduced first (`tests/test_flagged_not_changing.py`: 10.1 s of "changing", the second scene at 11.4 s on
the two-kit setup), then fixed (under 3 s, and under 4 s through the panel).

**D74 — The fins are data, a design field and part of the iPad picture (Oct 18, preset-options PR A).** VWBD drawing
A-20-1 ('Fin diagram with lights', Oct 18) is now the source for the canopy's 650 Mylar fins, and it closes doc 02's
orientation question: **p14** (all 270 drawn lights sit on the p14 lattice; 150 would under p18), with
`serpentine_rows` routing drawn (head 0's end and the B flip still need calibration). Plan:
`docs/plans/preset-options/README.md` §1 and §8.
- **Data, validated wherever the canopy loads.** `config/fins.json` (a byte copy of the plan's extract) is named by
  `canopy.yaml → canopy → fins` (`file`, `apron_in: 6`, `fin_width_in: 5`) and resolved strictly next to canopy.yaml
  (a bare name; no fallback to the repo's `config/`). `conductor/fins.py` refuses unknown keys, non-finite numbers,
  angles outside 0–180° and drops outside 0–60 in, then the drawing's rules: 650 fins; the count and the drop key
  agree with the fins, drop by drop; at least two different drops; the apron and fin width agree with canopy.yaml;
  the outline is the canopy plus its apron and every fin hangs inside it; no two fins share a centre; every light has
  a fin within 12 in. `canopy.yaml → canopy → vwbd_panels` must give every position one of P-01…P-09, each once.
  Problems are ConfigError sentences naming the file, the entry and the fix (ten listed at most).
  `geometry.load_canopy_config` reads through `config._read_yaml`, so bad YAML or a missing `canopy:` is a ConfigError
  naming the file too (it was a raw YAMLError or KeyError), and it attaches `fins`, `fin_map` (immutable, cached per
  file mtime and size) and `vwbd_panels`. Every device path loads the canopy through it, so **a missing or bad
  fins.json stops the start and the deploy preflight** (`live.preflight_line` loads the canopy outside
  `load_live_looks`' tolerance on purpose). A canopy.yaml without the two sections loads as before. `load_modes`
  checks modes.yaml against the loaded canopy, fin map included.
- **The field `{kind: fins}`.** t = (24.5 − D) / 10.5, clamped to [0, 1] before any phase wraps, where D is the mean
  drop of the fins within 12 in weighted by **w = exp(−(d/6)²)**. That is the form `lights.json` was made with
  (0.0102 in at worst at the 270 heads); the textbook exp(−d²/72) misses by up to 0.192 in, so the plan README has an
  erratum and a test pins the form. `invert` and `cycles` work as for every kind (scenes keep cycles 1, D63).
  **`transform: mirror` is refused:** the fold would read the first colour on the rim as well, module level never
  gets past t ≈ 0.675, so the T3 phrase "<first> at both ends" would be false copy on the kiosk. The layout words are
  always "from the centre out". `F.bands` rounds a fins t to 2 decimals, so the drawing's three symmetric rings
  (centre 0, edge-middles ≈ 0.39, corners ≈ 0.67) stay whole for the D64 ripple. A fins design over a canopy with no
  fin map is a plain ConfigError in modes.yaml and one DraftError in the Lab, before any field is read: from
  `apply_drafts`, `draft_changes` and `resolve_copy`, which the preview calls first, so `/api/lab/preview` answers it
  as a 422 problem on `layout`, never a 500 (the shipped canopy.yaml has the fin map, so only a canopy without one
  meets it).
- **The Lab.** The layout picker offers **Fins** (`LAYOUT_KINDS` = `F.FIELD_KINDS`, pinned): no parameters, Mirror
  disabled, and a note that is true for every palette size, plain or inverted (the rim's colour shows light by
  light, never on a whole strand). The generated module-level sentence names only `reached_stops`. **From here a fins
  design can be published to the live show** by D68's path (`validate_trio` → `install_looks` → the live compile,
  plus the operator slot repaint for a painted scene); sim tests cover it. Lab tag `lab-9`.
- **The iPad picture.** `canopy_view.canopy_geometry` adds `apron_in`, `outline`, `fin_width_in` and `fins`
  [{x, y, angle_deg, drop_in}] (never the file's free text): `/api/kiosk/panel`'s canopy grows from 4,523 B to
  44,251 B (38,421 B compact), pinned under 48,000; the geometry cache key leaves the fin map out. `lit_state` is
  unchanged. The kiosk draws 237 × 132 in (the canopy plus the apron, its outline dashed), head dots at 1.6 in and the
  650 fins as 5 in strokes along their plan axes. Each fin mixes its four nearest heads' `state.lit` colours with the
  same w = exp(−(d/6)²) (`finNeighbours`, once per geometry; `finPaint` is pure). Its four states:
  - **lit:** the lit heads' colours mixed at full strength, at the level's opacity × its drop's (14″ 0.45 … 24.5″ 1)
    × √(the lit weight), at most 1;
  - **changing:** the same mix with the dots' 1.2 s pulse, when at least half its lit weight is changing;
  - **unlit:** `#D3D2D8` at half its drop's opacity when no head around it is lit (dark or off);
  - **unknown:** once 10% or more of its weight is on unknown heads, a dashed `--slider-rest` line and no colour
    claim.

  With reduced motion the fins snap to the current frame and don't pulse. The picture is laid out only when the
  geometry key changes, and the fins are repainted only when a dot's paint changed. **SVG strokes, not plan §8.4's
  canvas 2D:** SVG keeps the dots' 600 ms fades and the shared pulse, the per-node paint dedup, D69's
  no-layout-per-frame structure and the e2e selectors, at 650 + 270 nodes in the card. Frosted only (clear vs
  frosted Mylar waits for Prototype 01's photos). Kiosk tag `fins-1`.
- **Ops.** `/api/canopy/layout`'s modules carry `vwbd_panel` (per position, the same under `?scheme=p18`), and the
  `/visualizer` labels read "r0c0 · A · P-03". VWBD numbers its panels row by row on its sheet, so P-0n is not
  Module n: record each kit against its panel at install (today's Modules 1–2 belong on P-03 and P-06).
- **Not changed:** no shipped scene uses the kind (D77 does). `lit_state` is left alone, so since D73 the show box's
  flagged-only Modules 1–2 read unknown after every confirmed scene and draw as outline dots and dashed fins (D78 reads them lit). fins.json
  stays outside `live.config_sha` on purpose (widening it would flag every saved Lab version and publication at
  deploy; published trios are re-validated against the loaded canopy at every start).

## 2026-10-18 — motion on every scene

**D75 — Any scene may move: breathe and shimmer beside travel, a guest's choice runs first and then settles on the
best still tier, and the idle look may breathe (off); live stays off until Gate R (supersedes D65's "the idle look
never steps" and, for a guest's choice, "Golden Hour steps only where its canvas and slot can't run").** The
preset-options plan (`docs/plans/preset-options/README.md` §5.2, PR B) asked for motion on every scene without
changing any scene. `config/scenes.yaml`, `modes.yaml` and `recipes.yaml` are untouched (PR D adds the options), so
`config_sha` and a Lab publication already on the show box stay valid. What is decided:
- **What moves.** Any scene may carry one stepped rung, placed just before `[modules, strand]`. `SteppedTechnique`
  gains `mode: travel | breathe | shimmer` (travel is D65's, unchanged, and the default). A breath keeps the design
  in place: each module's colour moves up to `shift` (0–0.5) along the design's own t (before mirror and invert,
  clamped, never wrapped, so a rim module never jumps to the centre's colour) and its brightness up to
  `brightness_swing` (0–20) points either side of the rung's brightness. The waveform is one breath of
  `steps_per_cycle` steps per module: up to a peak on a whole step, back, down, back (`compile.breath_wave`; no two
  consecutive steps equal). `lag_steps` (0–6) makes the design's start lead the breath; a shimmer picks each
  module's start from `seed` (sha256, the same on every run). A lagged breath (a shimmer, or a breathe with
  `lag_steps` > 0) whose nine starts all come out equal (a seed such as 183 at lag 1, or a design whose t rounds to
  one step at every module centre) is refused wherever a canopy is checked (the load, the scene rules, the Lab's
  apply), on its `seed` or `lag_steps`: it would breathe as one under "on their own" or its lead words (review
  B-R6-1). Lags are counted over the modules the plan drives, so
  the two live kits never rest together under copy that says they move, and a breath whose driven strands would get
  nothing for a whole step refuses to compile there (the still look holds). Step 0 is the still design in every
  mode; the program's cycle is a breath plus its largest lag. Travel takes none of the breath's parameters ("set"
  means not the default, so `shift: 0` on a travel rung loads).
- **Copy says what changes.** A swing's copy says `swells and softens` / `glow and soften`; a colour-only breath
  says `drifts and settles`; "next colour" is refused when `shift` is 0, "brighter"/"softer" when the swing is 0; a
  breath names no flow except a lagged breathe's own start ("from the centre out"). The partial canopy's sentence
  says "a brighter or softer level" where only levels change. The Lab's generated breath sentence names only the
  colours the breath reaches.
- **Brightness rides in the step's own PUT.** One full-state PUT per changed module per step, never split (07 §6).
  A guest's brightness during a breath is **relative**: the breath swings around the guest's level, and its centre
  moves so the whole swing fits `[kiosk.guest_brightness_min, 100]` (at 100 a swing of 4 breathes 92–100, at the
  minimum of 10 it breathes 10–18), so every step still changes something under the moving copy; clamped level by
  level, half a level-only breath went silent at 100 (review B-R1-1). The hold after the run is at the guest's own
  level. This amends D60's "absolute" for breaths only (0 offset for every other plan).
- **Which plan a source shows.** A guest's (kiosk) choice runs the rung first; an /ops choice keeps the ladder order
  (D65: Golden Hour's canvas at once, Coastal Tides' rung); a re-pick (`reconciler`, `recovery`, `startup`, a
  publish) never starts a run and keeps a running one's phase; start-up, the idle return, the safe look, a try-on
  and the checklist's change back (`ops-restore`) show a still tier. "Show after publish" is an /ops choice. A floor
  never steps. **A choice runs once:** once its run is over, a re-pick builds the runner over (DONE at once) and
  lifts it to the best still tier (the never-ran swap); a publish with a longer `run_s` never restarts a finished run.
  A choice whose runner can't run when it is installed (a governor cool-off, a step too long) counts as run too, so
  a re-pick after the cool-off lifts never starts its steps mid-way (review B-R2-1; an idle breath's `run_s` is
  spent the same way).
- **The settle.** At a run's end the look moves to the mode's canvas or slot when that can run (one write per module
  that changes, source `settle`; a motion plan re-triggers all nine), else the runner's module-level hold stays.
  After a governor trip or two held steps the hold goes out now and the settle waits (the cool-off, or one cap
  window). D72's fast back-off does **not** hold it (a build deviation from the spec): the back-off only puts guest
  pacing back on the budget rule the run already kept, and its 120 s after a 90 s run landed past the 180 s idle
  return, so under the soak's pinned 5% throttling Golden Hour never reached its per-head sun.
- **Held steps.** A budget-held step is drawn as the step last sent (frames and levels frozen), and two held steps in
  a row (`HELD_STEPS_END`) end the run on the still look and the still copy; a sent step resets the count. (Review
  round 3: only a step that writes something resets it. A breath's step after a held one, back at the level shown,
  writes nothing; counted as sent it let a breath whose every changing step was held run on under moving copy.)
- **A step keeps a guest's room** (review B-R1-1). A guest's mood chosen mid-run now runs too, so a 60 s window could
  hold the old run's steps, the mood's burst and the new run's steps: nine sends, 81 writes before any retry, and the
  old check (the window plus the step within the cap) admitted the new run's first step at exactly 90. A step is now
  held when the writes still in the window it shares (the executor's count less those older than 60 s less the
  1 s per-device gap, at most one per device, which leave before the step's own are out) plus its own plus one write
  per module on the account (a guest's burst, the share of `budget_reserve` a mood chosen mid-run needs) would pass
  `account_writes_per_min`. In a run's steady state that is the same threshold as before (the discounted second is
  the oldest step, one per module); a run that starts on top of another holds its first step until the room is back.
  What no runner check can hold back (a guest's burst after the steps, 429 retries, re-asserts) lands in the rest of
  the reserve. The rehearsal's confirm says so.
- **The idle breath (`motion.idle`, off: `null`).** `{breathe_run_s, every_s}`: while the idle look holds with nothing
  else in force, it breathes once for `breathe_run_s` every `every_s`, and an idle return lands as one breath. It
  never moves the idle clocks or the guest settings, yields at once to any guest input, waits while paused, resting,
  rehearsing, trying on or while a guest's brightness or off is in force, and runs only through `tier_allowed` (never
  on live hardware until `live_enabled`). The show refuses `motion.idle` when the idle look has no stepped rung or
  `breathe_run_s` fits fewer than two paced steps. Alon turns it on only after a 30-minute live soak (live_enabled
  on, then motion.idle on, watched on /ops, `null` to revert). A breath sends nothing at its start (step 0 is the
  hold), so a module already showing the hold counts as verified then, whether it reads ready or carries only a
  flagged note with its look confirmed (D73, `tracker.flagged_only`: Modules 1–2's D54 note); the guest panel never
  says "Changing the light…" while nothing is sent (review B-R4-C1).
- **Any look can be rehearsed**, the idle look included: its own rung, or its module-level design at the D65 defaults.
- **"This scene moves" is `Mode.moves`** (a stepped rung in the ladder). `Mode.motion` keeps its 07 §6 meaning (the
  on-device all-or-none burst flag): it stays false, and the schema now refuses `motion: true` beside a stepped rung
  (`scene_rules` "static" and `live.validate_trio` would refuse it anyway). PR D's brief must write no `motion:` key
  and assert `mode.moves and not mode.motion`.
- **Resting modules keep their rolling reads** during a run (a lagged breath leaves modules unwritten for several
  steps), so the true-up still heals an out-of-band change on a resting module within 10 s.
- **The Lab and the panels.** The Lab edits the kind of motion and the breath's parameters (lab-10), previews each
  step's per-module levels, exports added `mode` / `brightness_swing` / `lag_steps` lines under `techniques →
  stepped`, and says when a rung under higher rungs runs ("When a guest chooses …"). Its "Show box today" pace plays
  the show box's own program (`today_steps`: lags over the two kits, so its cycle differs from the nine's) and, when
  that compile refuses (a breath resting on both kits for a step), says the show box holds still (`today_error`)
  instead of playing the nine-module steps (review B-R1-3). The try-on stays still at module
  level (D66, unchanged). The guest panel's `steps` motion carries per-panel `levels`, drawn as opacity
  (breathe-1).
- **Live off.** `motion.stepped.live_enabled` stays false and `motion.idle` null: nothing changes on real lights
  until Gate R (recommended on nine with a breathe look) and Alon's PRs.
- **Superseded:** D65's "the idle look never steps (the show refuses to load otherwise)" and "Golden Hour steps only
  where its canvas and slot can't run" for a guest's choice; the plan §5.2 names `lag_per_ring` / `steps_per_breath`
  are built as `lag_steps` / `steps_per_cycle` (steps per breath).
- **Soak deltas** (`make soak`, seed 0, `docs/reports/sim-acceptance.md`; `config/sim/acceptance.yaml` untouched;
  against `origin/claude/flagged-not-changing`'s report). Main run: a guest's Golden Hour now steps first, so
  accepted changes by tier went canvas 73 → 51, stepped 19 → 40 (modules 78 → 77), 16 runs then settled onto their
  canvas or slot, and the simulator took 5129 writes (3133). The settle row's p95 is 4.49 s on this seed (5.19 s,
  FAIL since D72): the random path moved with the cap rule below; it is not a fix of D72's known cost, which fails on
  most seeds. Every pinned row passes. **Ripple delta:** 60 of 60 changes landed ring by ring (48 + 12 one-burst
  before), because a guest's Golden Hour lands at module level and steps instead of as T1's SIMULATED motion burst;
  settled p95 6.41 s (7.09; ≤ 7.4), per-ring spread p95 2.50 s (2.20; reported). Stepped scenario: six scenes and
  all three kinds (Garden a SIMULATED breathe, Sunset a shimmer, Goodnight a level-only breathe, Calm travel), 153
  steps landed (124), 0 superseded, failed or skipped, one PUT per changed module per step, at most 72 program PUTs
  and **82 writes of any kind** in any 60 s (87; 91 before the review's cap fix), 9 finished runs settled canvas 5 /
  stepped 4, the idle return still at 180.5 s, no governor trip or downgrade. Over seeds 0–15 of that scenario the
  most writes in 60 s is 80–89 (86–92 before the cap fix, above 90 on seeds 0 and 7), with no run ended by held
  steps (31 single holds over the 16 seeds, mostly a run's first steps on top of another run's). The 89 is eight
  steps already sent plus a guest's mood burst: the reserve's case, which no runner check can hold back.
- **A step a pinned fault made a module miss is counted on its own** (review B-R1-2). When Hubspace answers 429 on
  every attempt of a module's step write (the executor gives up) or the simulator silently drops it (or its
  re-send), the next step carries that module, by design (the reconciler re-sends a failed write only while the
  next step is more than 2 s away). The soak's landing row gates on 0 steps superseded otherwise and bounds the
  fault-carried ones by the writes those faults hit. The faults' random path moves with any config change: a Lab
  export of Coastal Tides as a breath had failed `test_short_soak` on two such steps (each a module answered 429
  three times). Over seeds 0–15 one step (seed 6: a 429, then a silent drop) was carried that way and none was
  superseded otherwise.

## 2026-10-18 — the iPad review menu

**D76 — The iPad review menu: options grouped in rows, cards drawn at the tier in use, hide without a deploy, "Make
this the scene" (Oct 18, preset-options PR C; plan docs/plans/preset-options/README.md §6).** A scene may name
`option_of: <base id>` (config/scenes.yaml): one level deep, and the base must be a scene in the same catalog. While
more than six scenes are visible (`GRID_MAX`, one number in conductor/ipad_menu.py, web/kiosk/panel-model.mjs and
web/ops/ipad-model.mjs), the iPad shows the review menu: one row per scene group with its options side by side, each
group starting at the left, and only the scene list scrolls under a pinned Now Showing and brightness. At six or
fewer it is the 3 × 2 grid, unchanged, so deploying this changes nothing on the live iPad until PR D adds options.
- **The cards draw each scene at the tier the engine would show it at now.** Every visible scene's likely plan, the
  rule /api/kiosk/modes already uses for every listed mode (`routes.kiosk._likely_plan`: the plan showing now when it
  is that scene's, else `compile_best`, which honours the reconciler's hysteresis floor). Per light only when that
  plan is canvas or slot (T1/T2); its nine module colours at module level and the stepped hold; and for a
  whole-strand look, including a modules- or stepped-first scene the reconciler has eased down to its strand rung
  (the idle Lowcountry Calm among them), the one colour that plan puts on each panel it drives (`basis: "strand"`,
  `ipad_menu.plan_thumb` over `canopy_view.plan_picture`, which shares `lit_state`'s colour rule, so the card is the
  claim Now Showing makes for that plan; every strand look in git is one rgb colour). Review round 3 (C-R3-1) chose
  this over keeping the palette strip there: the strip's 30-colour gradient in the box where the other cards show
  the ceiling is a picture the ceiling never shows. A look the strand plays on its own (a preset or program; none in
  git) makes no colour claim on Now Showing, so its card has no thumbnail and keeps the palette strip, the scene's
  colours rather than a picture of the ceiling. This is part of question 1: a labelled design view would change it
  too. A card lights only the positions that plan drives, as Now Showing does
  (`guest_panel.driven_positions`; with no plan, the positions with a device registered): a position with no device
  is left out of every look (D62), so on the two-kit show box every card shows two lit panels and seven dark ones,
  never nine. That is one compile per visible scene per poll, as /api/kiosk/modes pays (22–28 ms for 18 scenes on
  the sim engine). Plan §5.4 said the iPad picture shows the per-light design, but
  CLAUDE.md's copy rule and the panel's own rule ("a picture is copy too") say the card must be what the lights can
  show; on the show box that is module level until per-light is commissioned. Alon's question 1 in the PR: keep the
  tier-true cards, or add a labelled per-light design view (new client copy). Thumbnails appear only in the review
  menu; the six-card grid keeps its palette strips (the question for PR E is in STATUS).
- **Card thumbnails draw no fins** and use a thumbnail dot radius (`DOT_R_IN` × the card's scale; integration
  conflict C12, option b): the nine panels as hairlines and the heads as dots, still, brightness as opacity. They are
  computed on the server (`ipad_menu.catalog_thumbs`, from `routes.design.mode_design`), cached per scene, per
  live-looks generation and basis, and drawn on a rebuild or resize only (D69). The card key carries the thumbnail's
  `sig` (its full-strength colours) and the scene's `brightness_draft` (its opacity), so a brightness-only publish
  repaints the card too.
- **`kiosk.hidden_scenes`** (git default `[]`, one foldable line in config/conductor.yaml) has an ops overlay,
  `<data>/overrides/settings.yaml` (`{kiosk: {hidden_scenes: [ids]}}`), written by the /ops "On the iPad" switches
  atomically at 0600 with a history copy. The overlay replaces git's list; it never fails a start (a file that can't
  be used leaves git's list in force with an /ops "Needs attention" line, as 07 §8 asks). Ids that aren't scenes now
  and the idle scene's id hide nothing, are named on /ops, and the next switch drops them. The /ops overlay download
  folds the list in force back into config/conductor.yaml as a `git apply` hunk, so 07 §8 needs no edit. The
  setting is changed in place (`services.settings` is never rebound: the engine keeps the object).
- **A hidden scene** leaves the cards, the attract screen and both kiosk choose routes (the calm 404). It stays in
  the catalog: ops can still choose it (the picker says "(off the iPad)"), the Design Lab still edits it, and while
  it is showing the iPad still names it in Now Showing (the panel's `showing`). The idle scene (`kiosk.idle_mode`, and
  `kiosk.idle_scene` when set) can never be hidden: git's list refuses it at load, the switch answers 409.
- **"Make this the scene"** is client-side in the Lab, with no new route: `promoteDraft` copies the option's palette,
  layout, sample, brightness, solid colour and description into the base's working draft (the description is the
  option's, so the base's Now-showing line stays true of the design it now has), keeps the base's id, name and mood,
  never adds or drops a stepped rung, and resets the copy choices to auto. When the base has a stepped rung and the
  option has stepped timing, the option's motion is copied too (direction, steps per cycle, step time and run
  length), and the button's title, the confirm question and the done notice say so (`promoteTakesMotion`, the
  "took-motion" note). Undo brings the base's draft back; the base's Publish saves and publishes it. The base's
  name keeps any "#1" until PR E or a Lab rename at the session (C27).
- **The export prompts for the sign-off instead of claiming it.** A base whose palette and module-level field equal
  one of its options' (and differ from its own design in git) gets a "Made the scene on site" line directly after
  the D59 line, asking whoever exports to add who chose it and when: that choice is the D59 sign-off for its colours.
  The record is inferred, never stored (Version and SceneDraft are extra=forbid, so older Conductors still read saved
  versions); a later tweak of the base drops the line, and the D59 line still asks for the sign-off.
- **`option_of` is left out of every dump while unset**, so a catalog with no options dumps exactly as before
  (`trio_sha`, published looks, the scene pins). It is pinned like the ids: a published trio or a Lab draft can never
  regroup a scene (validate_trio, the Lab's shape check).
- Lab exports made before D77 lands propose a reserved D-number (`_next_decision` is max + 1); renumber by hand.

## 2026-10-19 — the 18-option catalog

**D77 — The client-review catalog: 18 options, three per scene, every one moving (Oct 19, preset-options PR D; plan
`docs/plans/preset-options/README.md` §4 and "§4 as built"). Supersedes D59's "six scenes, names never change" for
the client review only, until PR E.**
- **Ids and names.** `config/scenes.yaml` has 18 scenes in catalog order: each scene's #1 keeps its id
  (`lowcountry-calm`, `rainbow-row-sunset`, `garden-gathering`, `coastal-tides`, `golden-hour`, `goodnight-glow`),
  followed by `<id>-2` and `<id>-3`, which carry `option_of: <id>` (D76). Names are `"<Scene name> #n"`, quoted in
  the YAML. The #1s are renamed "… #1" and keep the "#1" until PR E or a Lab rename at the session (C27; D76's
  promote keeps the base's name). Moods stay the scene's own, and every mode's label is its scene's name.
  `config/modes.yaml` lists the 18 modes in the same order. The 46 new colours have one recipe key each, using the
  Lab's allocation (the slug, else `<option id>-<slug>`). The one exception is `rainbow-row-sunset-2-apricot`, kept
  off the plain `apricot` that the Lab round trip's rename case writes. `home-depot-orange` is its own key beside the
  colour-study `hd-orange`, which no scene uses.
- **Unapproved until the session.** The 12 options' colours, names and descriptions, and Calm #1's description
  ("a pale sea-glass heart", for the tilted rings) are drafts. They need D59's on-site sign-off at the client session,
  and the recipes need the colour study. "Home Depot orange" as a guest-visible name also needs the brand check (06
  #29).
- **The #1s keep** their approved palettes, moods, the idle and safe role (`kiosk.idle_mode` and `compile.SAFE_MODES`
  are `lowcountry-calm`) and the painted slots (custom-1 Sunset, custom-2 Garden, custom-3 Golden). **Their placement
  is re-fitted to the fins** (plan §4):
  - Calm #1 and Garden #1 are rings tilted along the current. `fins` takes no parameters, so the tilt is an inverted
    canopy-metric radial centred 12.5 in off the middle along the current (`centre_in [122.5, 67.5]`). Calm also
    gets `radius_in: 130`, so a fourth visible colour reads (its two colours are only ΔE76 11.8 apart). Their copy
    says "from one spot just off the middle".
  - Coastal Tides #1 and The Goodnight Glow #1 (inverted: the moon on the deep centre) sit on the fin rings.
  - Rainbow Row Sunset #1 runs along the current: linear at field angle 18°, which is 31.4° in true inches, because
    field angles are measured in the canopy's proportions.
  - Golden Hour #1's sun is stretched (`radius_in: 225`), so the deep centre is exactly sunset pink. Its stops stay
    evenly spaced, as the scene rules require; the plan asked to retune the stops.
- **Every option steps:** 11 travel, 5 breathe, 2 shimmer. Calm #1 and Coastal Tides #1 breathe their colour
  ("drifts and settles", shift 0.4 and 0.35, the start leading by one step). Calm #2 and Goodnight #1 and #3 breathe
  their level. Garden #3 shimmers its level, with `lag_steps: 2` and seed 31: the corners differ, and the two kits
  (r0c0, r0c1) start apart, so the show box's two kits visibly shimmer (seeds 3 and 11 gave them the same lag).
  Goodnight #2's shimmer is the plan's firefly hop (review D-R2-3): a three-step breath of colour and level
  (`direction: reverse`, `shift: 0.35`, swing 6), its starts up to six steps apart (`lag_steps: 6`, seed 107), so
  after the still step one or two strands are at the top of their breath at a time, and each corner strand turns
  full firefly as it brightens, at its own step; the centre takes deep moss, never firefly. Each `run_s` is a whole
  number of program cycles at the nine-strand pace (D75's cycle is a breath plus its largest lag) and at most
  `kiosk.idle_return_s`: Calm #1, Calm #2 and Garden #3 run 120 s, Tides #1 105 s, Goodnight #2 144 s (two cycles of
  nine 8 s steps) and Goodnight #3 150 s. Ladders are `[stepped, modules, strand]`, and `[canvas, slot, stepped,
  modules, strand]` for the painted three. There is no `motion:` key (D75: "this scene moves" is `Mode.moves`, and
  `Mode.motion` stays false, so there is no T2 burst). Keys left at their default are left out, except `direction`,
  which is written on every rung: the Lab export can add the other stepped lines but not that one, so a direction edit
  on a rung without it is refused. **Live hardware refuses the rung** until Gate R and `motion.stepped.live_enabled`
  (show.py's `tier_allowed`), so on the show box every option shows its still module-level design.
- **Moving copy uses D75's words.** Prefix A ("… each Hubspace strand set to a single colour and sent its next colour
  every few seconds, so …") is for travel and the colour breaths, which say "drifts and settles". Prefix B ("… set to a
  single colour and sent a brighter or softer level every few seconds, so …") is for the level breaths and Garden #3's
  shimmer. Goodnight #2's firefly shimmer changes both, so its copy is sent "its next colour and a brighter or softer
  level". The plan's own prefix C ("the strands around the middle") is dropped. D75's waveform swings both ways, so Coastal Tides
  #1's centre strand does change: at the bottom of the breath it takes some aqua. On a partial canopy the sentence
  says "a brighter or softer level" where only levels change.
- **Module level, stated plainly.** The eight fin-ring options (Calm #2, Sunset #3, Garden #2, Tides #1, Golden #2,
  Goodnight #1, #2 and #3) read as **three rings** on the real lights: the centre, the edge-middles and the
  corners. They pass the two-d warning's "4–9 colours" only on near-ties of ΔE76 0.33–1.23 (r0c0 and r2c2 at t 0.6745
  against r0c2 and r2c0 at 0.6666), and a test pins them as three seen colours. Their rim colour (the last stop, or the
  first when inverted) shows light by light on the apron, and at module level only during a run. The other ten show
  4–9 module colours at least ΔE76 2.3 apart. A palette tweak at the session can collapse a near-tie; the two-d warning
  then stands and the Lab refuses the export. Switching that option to the tilted radial clears it. Alon chooses
  whether to keep the fin rings (STATUS question 1; the default keeps them, decision D1).
- **Where the options differ from §4's prose** (review D-R2-3; each pinned by a test in tests/test_d77_catalog.py):
  - Garden Gathering #3 is the plan's warm pools, not a ninth fin-ring option: the current folded at field angle 140°
    (Coastal Tides #2's layout) puts Edison amber on the centre and on the two corners the current runs through,
    lantern gold on the edge-middles and fern green on the two far corners.
  - The Goodnight Glow #2 keeps its still look on the fin rings with a hint of firefly on the corners; the firefly hop
    is its run (above). A still design can't light one or two strands alone without leaving the rings.
  - Rainbow Row Sunset #2 stays on the current at 18°, not the plan's flatter 15°: the plan's angles are in true
    inches (its current is 32°), and 15° there is 8.1° as a field angle, which reads as columns at module level (the
    two-d warning fires at every field angle under 15°). At 18° all nine strands already show a different facade
    colour, which is what "flatter" was for.
  - Coastal Tides #3's beam is one side (the left column), not a narrow wedge: the stops are the palette evenly
    spaced (a blocking rule), so lamp gold and beam white hold the top of t and the wedge can't be narrowed, and
    without the fold (`transform: mirror`) the sweep's seam puts beam white on a strand beside night sea.
- **Hand copy may hedge** on a colour at about a 35% mix ("a touch of haint blue", "a hint of firefly"; C28). The
  Lab's generated copy names only the stops module level reaches, so a Lab edit or a promote regenerates those
  sentences without the hedges.
- **Golden Hour #2 reorders its stops** (decision D3) to warm white, honey, Home Depot orange and amber. The four
  corner strands then show Home Depot orange at rest (module-centre t ≈ 0.67), amber sits on the apron light by
  light, and the description names orange on the corner strands.
- **Card thumbnails** draw no fins and use the thumbnail dot radius (C12 option b; D76).
- **What PR D relies on**, checked on the merged A + B + C base before building:
  - fins resolve on every loader (`load_modes`, the compiler's default canopy, `canopy_view`, the soak, the scene
    pins, the Lab), and the raw canopy mapping refuses a fins design plainly;
  - D75's relative guest brightness during a breath;
  - start-up, the idle return and the safe look never start a run on the idle look (`motion.idle` off), while a
    guest's choice of Calm #1 runs its breath;
  - `Mode.motion` false, so there is no T2 burst;
  - the partial-canopy copy;
  - D75's "set" rule for travel rungs;
  - forward is sign −1, applied before mirror and invert.
- **The 7 am checklist's test change** now lands on The Goodnight Glow #1's breath in the simulator (ops keep the
  ladder order) and restores Calm #1 still at module level. On live hardware it lands at module level until Gate R
  and `live_enabled`. The rate probe flips Garden Gathering and Lowcountry Calm through ops, and both have rungs now,
  so run it before `live_enabled` is on (the probe runbook says so).
- **The soak.** The stepped scenario's candidates are D75's six with Garden Gathering #3 (the first shimmer) in
  Rainbow Row Sunset's place, and there are no `simulated:` overrides. A mood chosen mid-run starts its own run. The
  idle return must install Calm #1 with no runner. The ripple run strips every stepped rung from its config copy,
  which is the live path until Gate R. In `make soak` (seed 0) on the final catalog the report reads PASS: every
  pinned `acceptance.yaml` row passes (main run p95 4.70 s; out-of-band heal max 9.70 s against 10 s), and the
  ripple's last-ring settle, a soak.yaml proposal that isn't pinned, is p95 7.05 s against 7.4 s. Seeds 1–5 give
  5.65–7.00 s for it, all PASS. Only the two reported spreads fail, as before. Nothing was tuned.
- **Deviations from plan §4's prose** are listed in the plan's "§4 as built".
- **Deploy order.** First, export any live Lab publication (GET `/api/lab/export`): the ids, order and ladders differ
  from git, so after D the show falls back to approved with an /ops warning. Second, deploy D before commissioning
  custom-1 to custom-3 at the venue. Sunset's angle, Garden's rings and Golden's radius change the painted designs, so
  every recorded slot hash goes stale, and a slot commissioned before D needs the operator repaint.
- Lab exports made before D77 lands propose a reserved D-number; renumber by hand.

**D78 — A module whose only problem is a flagged note shows its look on the iPad's picture (Oct 19; follows D73).**
D73 makes such a module "stuck" at once, and `canopy_view.lit_state` drew every stuck module as `unknown` (an outline,
no colour), so the live iPad drew Modules 1–2 as outlines right after every confirmed scene (before D73, 10 s later)
while their lights showed it. Now the engine's module record carries `flagged_only` (`tracker.flagged_only`: the look
sent, read back and matching), and the picture reads that module as confirmed: `lit` with its colours, or `off` once a
guest's power-off reads back off. A module stuck for any other reason (a readback that still differs, a failed write,
an offline controller), a locked one and a resting canopy still make no colour claim; /ops is unchanged (the note
shows, the phase needs attention). The server payload changes, not the kiosk JS: no kiosk or Lab tag bump. Reproduced
first (`tests/test_flagged_reads_lit.py`).

**D79 — Stepped looks on live hardware after Gate R passed on one kit (Oct 19; follows D65, D75, D77).** Alon ran
the stepped rehearsal from `/ops` on the show box (`14c2932`) on the one kit online, String light 1 (Module 2,
`canopy-r0c1`; Module 1 offline in transit and held on `/ops/module`; Modules 3–9 not yet registered): Rainbow Row
Sunset #2 for 10 minutes passed every criterion (100 of 100 steps at 6 s, 104 attempts, 0 throttled, 0 403s, at most
13 writes on `bench` in any 60 s, verified p50 1.46 s and p95 2.68 s, 3 superseded, 0 skipped, no governor trips),
report `rehearsal-20261020T011258Z-rainbow-row-sunset-2.json`. The observer saw each colour change **fade** ("a nice
fade"), bench item 6's answer on this kit. Alon set `motion.stepped.live_enabled: true` knowing it measures one kit
at 13 writes a minute, not nine at 72–90: the nine-kit Gate R (Rainbow Row Sunset #2, 10 min) still runs during
install before tours, and a fail there turns it back off. The rate probe (`account-rate-probe.md`) was not run first
(on one kit it reaches ~60 writes a minute); with `live_enabled` on, its look flips may start stepped runs, so set it
back off for the probe. Also seen: The Goodnight Glow #1's breath (swing 4 around 28%) was not visible on the strand.

**D80 — Per-light motion, the rate test, and 18 per-light options (Oct 19; follows D65, D75, D77, D79).** Alon asked
for (1) a test of how fast the canopy can step (each module every 1–2 s) and (2) three more options per scene whose
ripples and breaths roll gradually at per-light resolution ("individual light level programming"), modules or lights
switching off included. He asked where 07 §6's "never loop 3.4 KB writes" came from (Sep 26's contracts, written
before any bench, as caution about unknowns: unpublished account limits, flash wear or reloads on repeated pattern
writes) and chose both layers plus a bench test, built so it can go live easily:
- **Per-light motion.** A stepped rung may carry a `lights` block (`schema.LightsMotion`): each step sends each module
  ONE PUT with a whole frame (each light's colour and its own colorBrightness, 0 = off), the slot select, the mode and
  the power (04's proven T1 write) into `motion.lights.slot` (custom-3). Effects over an envelope field (radial, fins,
  linear, sweep): ripple (rings rolling out or in), glow (a front growing from the field's start and back, lights past
  it at `floor`, 0 = dark), breathe (a breath rolling outward by `lag`), twinkle (each light's own seeded rhythm), and
  `colour_travel`. The runner, governor, account cap, rehearsal and panel are the stepped ones; per-light steps go no
  faster than `motion.lights.min_step_s` (1 s). It runs in the simulator always; on live hardware only with
  `motion.lights.live_enabled` (off) or during an operator's rehearsal of that look, and per kit only with Gate C and a
  proven map — otherwise the same rung's module-level steps run, and the copy says which (07 §6 amended for exactly
  this). A canvas whose slot a run overwrote is written back into its slot with its next select.
- **The rate test.** The rehearsal's `ladder` mode: steps of 4, 3, 2, 1.5, 1 s, 60 s each, our account cap not
  applied, the per-device 1 s gap and the governor kept, stopped at the first 429/403; the report gives each rung's
  attempts, throttling, verification and `fastest_clean_step_s`. In the simulator (no account limit) a nine-module
  run is clean to 1 s; below ~2 s most writes are superseded before their readback verifies them.
- **18 options** `<scene>-4, -5, -6`, new on-theme colours (drafts, D59/colour study), each a per-light rung with a
  module-level breathe fallback that passes every scene rule; on the iPad unless hidden from /ops (git's
  `hidden_scenes` stays empty). Per-light is opt-in at the compile context (the show and the Lab pass it). Review page: the compiler's own frames animated on the 270-light layout.
- **Not done / needs hardware:** no per-light frame has been streamed to a real kit; neither kit has Gate C + a map on
  the show box; whether a strand reloads or flickers on each frame write, and whether the account takes the pace, is
  the bench rehearsal's and the rate test's to answer. At nine kits our own 90-a-minute plan stretches per-light steps
  to 7.5 s until the rate test justifies more.

**D81 — Per-light looks step every 3 s on nine kits; module-level looks keep 7.5 s (Oct 21, Charleston; follows
D65, D79, D80).** With the nine new kits registered on VWBD's account (bench), commissioned (Gate C + map on all nine)
and online at the venue, Alon ran the rate test (D80) three times on all nine: twice with Rainbow Row Sunset #2
(module level) and once with Coastal Tides #5 (per-light). All three: **0 × 429 and 0 × 403** at up to 540 writes a
minute on one account. But Hubspace's ~1.5–2.6 s readback confirms only part of the steps when they come fast: module
level ~75 % at 4 s and ~50 % at 1.5–2 s; per-light **97 % at 4 s, 93 % at 3 s**, 24 % at 2 s, 0 at 1 s. Module 3 (run
1) and Module 9 (per-light, at 1.5 s) had a few HTTP 424s (device reachability). A first try (account cap 90 → 450,
1.5 s) failed CI: in the soak (5 % simulated throttling, 1 % silent drops) unconfirmed steps piled up re-sends and a
look was downgraded; any looser **stepped** cap degraded the module-level soak. So:
- `motion.lights.account_writes_per_min: 225` — per-light runs pace by their own cap (less the 20 % reserve = 180 a
  minute): nine kits every 3 s, one kit at its designed 1.5–3 s. The runner, the rehearsal report and the Lab use it.
- `motion.stepped` unchanged (cap 90, 7.5 s on nine): the module-level looks keep their Gate R pace.
- The soak counts a module as showing a per-light look when it shows any frame of the run (the look moves faster
  than a slow module confirms), judges its write rows against the cap in force, and allows up to 5 % of steps
  superseded before every module confirmed them (still 0 failed or skipped).
