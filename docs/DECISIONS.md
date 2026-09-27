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

## 2026-09-27 — canopy engine, after the P1–P3 adversarial review (engine findings F1–F11)

Numbered E1–E9 so they don't collide with other review branches; the lead may renumber when merging.

**E1 — Capabilities are a background loop, and the first look waits for its first round.** `ShowWiring.start()`
returns at once; a task reads every module's profile concurrently (15 s timeout each), retries the ones missing with
backoff 1, 2, 4 … 60 s, and reads again on the engine's outage recovery, the driver's `on_recovery`, a module coming
back online (a readback with `available: true` after `false`) and `/api/devices?refresh=1`. The idle mode is requested
after the first round (success or not) rather than by `engine.start()`, so a box whose profiles answer at once doesn't
send Calm twice. New or changed capabilities re-compile the current mode (source "reconciler"). In sim, commissioning
waits until every module is read.

**E2 — The safe look assumes conservative capabilities; everything else leaves an unread module out.** Lowcountry
Calm compiles for a module without a profile with the sibling string's documented white range (2700–6500 K, 04), so
the safe look can always go out. Every other mode compiles for the modules that were read and lists the rest in
`ModePlan.skipped` (the engine shows them as needing attention). Copy that counts ("all nine", "270 lights") counts
the modules actually driven.

**E3 — Slot records carry an origin and effect fields.** `DeviceEntry.slot_origin` ("api" = commissioned, "app" =
recorded as hand-painted) and `slot_effects` sit next to `slot_hashes`. A hash without an origin is "api" (before this,
commissioning was the only writer), so an old overlay can never make the "painted in the Hubspace app" copy appear. T2
needs "app" records; T1 is shown only with "api" records on every module. Commissioning never overwrites an "app" slot
(protecting 15–20 min of hand-painting per module); going back to T1 for it means removing its record in the overlay
(runbook). Changing that would need an explicit "forget this record" action.

**E4 — T1 is judged against the commissioned readback hash; stale slots are rewritten once.** The engine's expected
slot row names only the recorded hash (`rows.slot_hash_expectation`, read by `rows.compare`; the canonical projection
is unchanged). When the look now compiles to another pattern or other effect codes (recalibration, a design edit, a
code enabled or disabled), the reconciler rewrites that slot within the existing 10-minute limit — once per compiled
content, never in a loop — and the expectation, copy and motion flag follow the new record. A slot that already holds
exactly the current content (e.g. a rewrite whose verification a newer command superseded) is recorded without a
write. One slot write per module at a time.

**E5 — Motion copy follows the device, and `motion` means "re-trigger as one burst".** Golden Hour's motion copy
appears only when every module's slot holds the benched code (recorded from readback at commissioning, else the
header readback). `ModePlan.motion` is set whenever a slot may animate (a code configured and enabled, a slot that
still holds an effect, a hand-painted slot of a `motion: true` mode whose header animates or is unknown), so copy and
re-trigger semantics are no longer the same flag. The effect fields are checked on readback apart from the hash;
only motionEffect/brightnessEffect count (speeds and depth aren't claimed in copy).

**E6 — 503/504 are an outage, 429/403 are throttling.** THROTTLED attempts or readbacks with HTTP 503/504 count toward
the resting streak like SERVER_ERROR and never toward the throttle downgrade, matching the adapter's own outage
classification (D15).

**E7 — Upgrades lift past rungs that can't be shown.** When a floor lifts onto a rung that isn't allowed or doesn't
compile, it keeps lifting to the next usable rung in the same step, so T3 → T1 takes one clean interval (5 min) when T2
isn't available.

**E8 — Lowcountry Calm never names a Kelvin value it can't show.** A strand look whose copy names its Kelvin value uses
the configured fallback look (and its copy) when a module's range would clamp that value; without a fallback, the
copy names the value actually sent. Goodnight's copy names no value, so its 2200 → 2700 K clamp stands.

**E9 — The soak runs the show's wiring, with a SIMULATED motion code.** `make soak` builds `build_services` +
`ShowWiring` on the virtual clock with a temporary data dir (telemetry kept in memory), so it exercises the capability
loop, the commissioning gate, overlay records and the real ladder. `config/sim/soak.yaml` enables motionEffect 1 for
Golden Hour in the soak only (nothing benched; the report labels it SIMULATED) so the all-or-none re-trigger is
measured. The pinned acceptance conditions are unchanged. `run_soak(compiler=…)` still runs the bare engine.
