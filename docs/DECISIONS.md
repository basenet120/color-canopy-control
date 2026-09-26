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
