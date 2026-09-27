# Build plan

_Written Sep 26 2026 (the context docs' date, so the schedule in the build prompt stands). One plan for all tiers;
contracts are amended per tier. Binding sources: `docs/context/07-engineering-contracts.md`, `04`, `05`._

## Schedule

| Target | Tier | What "done" means |
|---|---|---|
| **Oct 1 (Thu)** | P0 — live gate + bench | Gate/choke point, SimDriver, Afero adapter, token store, CLI + `/ops/accounts`, Gates A–C + hue ramp on `/ops/bench` and CLI, `/ops/bench/clock`, bench runbook |
| **Oct 3 (Sat)** | P0b — `/ops/module` | Single-module page on a phone: map, paint, whole-module controls, test cards, slots, effects, calibration, hold, telemetry, lock, tester role; Playwright in sim |
| Oct 5 (Mon) | P1 engine + P2 looks + P3 kiosk visuals | Registry/capabilities/executor/reconciler/soak; composer, slots, six modes, tiers; kiosk screenshots to the client |
| Oct 8 (Thu) | P3 surfaces + P3.5 venue box | `/kiosk`, `/ops`, visualizer desired-vs-reported, enrollment, `make e2e`; `deploy/`, runbooks |
| **Oct 12 (Mon)** | Feature freeze | Tag + pinned image. P4 vignettes in if solid, else stock app on vignette iPads (call made in STATUS) |
| Oct 20–23 | Install | Commissioning runbook |

## Module map (who owns what)

```
conductor/
  clock.py            Clock, RealClock, VirtualClock, VirtualTimeLoop, run_virtual          [contract, done]
  gate.py             GateSettings (env → mode truth table), LiveGate (purpose/device/account)  [contract, done]
  tokens.py           TokenStore (Fernet, env+account bound, atomic 0600, .prev generation)   [contract, done]
  redact.py           process-wide secret redaction filter                                    [contract, done]
  wsauth.py           TicketBook (single-use, 30 s), origin_allowed                           [contract, done]
  ws.py               WsHub: /ws/live protocol, topics, per-role topic rules                   [contract, done]
  config.py           ConductorSettings, Registry (+ ops overlay, history), ConfigError        [contract, done]
  rows.py             StateRow builders, Look → full state vector, compare(), slot_hash()     [contract, done]
  capabilities.py     detect(functions, states) → Capabilities                                [contract, done]
  locks.py            DeviceLockManager (exclusive, 10 min idle, allowlist-checked)            [contract, done]
  telemetry.py        Telemetry (ring + JSONL, stats p50/p95, subscribers)                    [contract, done]
  executor_api.py     Command, CommandResult, CommandStatus, DeviceStatus                     [contract, done]
  accounts_api.py     AccountManager protocol, LoginOutcome, AccountStatus                    [contract, done]
  roles_api.py        Role, Principal, path rules                                             [contract, done]
  bench_api.py        JobSpec, JobResult, Verdict, BenchContext, BenchJob                     [contract, done]
  drivers/base.py     DeviceDriver protocol, Outcome, WriteResult, ReadResult, DeviceInfo      [contract, done]
  drivers/sim.py      SimDriver + fault model                                                  [P0 · agent A]
  sim/profiles.py     load + resize profiles (sqhdk03-sim = the sibling string at 30 heads)    [P0 · agent A]
  render.py           state rows → per-head display colour (visualizer, module page)           [P0 · agent A]
  drivers/afero.py    AferoDriver + AferoAccounts — the ONLY aioafero importer                 [P0 · agent B]
  drivers/afero_transport.py  GatedClientSession: the choke point (aiohttp only)               [P0 · agent B]
  accounts_sim.py     SimAccounts (simulated sign-in for UI tests)                             [P0 · agent B]
  executor.py         CommandExecutor                                                          [P0 · agent C]
  state.py            DeviceStateTracker (desired vs reported, Ready/Updating/Needs attention) [P0 · agent C]
  roles.py            RoleStore (enrollment links, sessions), path rules                       [P0 · agent D]
  auth.py             + cookie principals, /ws/* ticket bypass (extend; keep Basic + tests)    [P0 · agent D]
  routes/core.py      /api/ws-ticket, /enroll/<token>, /ws/live                                [P0 · agent D]
  services.py         Services container built in lifespan (drivers per purpose, executors)    [P0 · lead]
  app.py              wiring: routers + static mounts                                          [lead]
  anonymize.py        scrub ids/MACs/SSIDs/emails/names/geo for "copy anonymized JSON"         [P0 · agent E]
  bench/              jobs (gate_a, gate_b, gate_c, hue_ramp, ...), runner, results, CLI       [P0 · agent E]
  routes/bench.py     /api/bench/*                                                             [P0 · agent E]
  routes/accounts.py  /api/accounts/* + /ops/accounts form handling                            [P0 · agent G]
  auth_cli.py         `python -m conductor.auth login` talks to the running server             [P0 · agent G]
  module/             single-module session: paint, cards, sweep, calibration, hold            [P0b · agent F]
  routes/module.py    /api/module/*                                                            [P0b · agent F]
  reconciler.py, slots.py, looks/, modes.py, kiosk.py, vignettes.py                            [P1–P4]
web/
  static/             shared CSS (system fonts), api.js (fetch + ws ticket + backoff), badge   [P0 · agent D]
  ops/accounts/, ops/bench/, ops/bench/clock/, ops/module/, ops/, kiosk/, vignette/, visualizer/
```

Runtime code lives under `conductor/`, `config/`, `web/` only — the Dockerfile ships nothing else.

## The seams (how pieces talk)

- **Logical ids everywhere.** Pages and APIs name devices by registry id (`bench-kit`, `canopy-r1c2`). Each
  driver has its own address: `sim-<id>` on the SimDriver, the overlay's `metadevice_id` on the AferoDriver.
  `Services.target(device_id, purpose)` returns `(driver, executor, address)`.
- **Drivers per purpose.** `engine` → SimDriver unless `CONDUCTOR_MODE=live`. `bench` → AferoDriver when the gate
  allows Purpose.BENCH *and* the device has a metadevice id; else SimDriver (results say SIMULATED). One executor
  per driver instance, so the per-device budget is shared whenever two purposes hit the same driver.
- **Everything writes through `CommandExecutor.submit/burst`** → driver `write()` → (Afero) `GatedClientSession`
  → `LiveGate.check_request`. No other code path can send a request to Afero.
- **Locks** are keyed by driver address. Bench jobs and `/ops/module` hold them; ENGINE commands to a locked device
  resolve LOCKED; BENCH commands need the lease.
- **Push:** `WsHub.publish(topic, data)`; pages get a ticket from `GET /api/ws-ticket`, open `/ws/live`, send
  `{"type":"auth"}` first, then subscribe (`ws.py` has the protocol).
- **Clock:** every service takes a `Clock`; tests and `make soak` run on `VirtualTimeLoop`.

## v1 seams (designed for, not built)

The build prompt's "seams, not code, for v1" (05 phase-2 ideas, 06 #12, 07 §6–§7). Each is a place the design already
leaves room for; none has code beyond what's named. Building one is its own PR, bench-gated where it touches hardware.

| Seam | Where it would plug in | What exists today |
|---|---|---|
| **Conclave push** (P4, optional) | A change *trigger* only (07 §5): the Afero adapter opens the Conclave stream (`conductor/drivers/afero_transport.py` already lets `conclave*.afero.net` through the choke point) and calls the reconciler's `check_module_soon(device_id, target)` for the module that changed, so drift is read (REST) at once instead of on the rolling true-up. Readback stays the truth. | Host allowlisted in the transport; nothing subscribes. The rolling true-up (`reconciler.py`, every module read once per `true_up_interval_s` = 5 s) is what finds out-of-band changes today. |
| **SimDriver animation clocks and drift** | `SimDevice.animation_origin` (`conductor/drivers/sim.py`) records each apply's phase origin within `start_skew_max_s`; a clock model would advance a per-device phase from it (period from the motion code and speed, drift per hour, reset on power loss) and `device_view` would report the phase, so the soak can measure "all nine in phase" for motion plans. | The origin and skew are recorded; nothing animates. Motion plans are measured only as "all nine re-triggered as one burst". |
| **Golden Hour's second technique: the stepped sweep** | A new `TechniqueName` (`conductor/looks/schema.py`, e.g. `sweep`) on Golden Hour's ladder in `config/modes.yaml`, compiled (`conductor/looks/compile.py`) to a *sequence* of `ModePlan` steps, each one PUT per module it touches (never one module's update split across PUTs, 07 §6), ≥ 2 s per step with a hard cap (06 #6). The engine would run the steps from a task on the Clock, as the current plan's "program", stopping on the next request; the executor's 1 write/s per device and burst rules apply unchanged. | Golden Hour ships one technique (on-device motion in a tight 9-PUT burst); the ladder falls back to slot/modules. 06 #12 picks between them from bench footage. |
| **`SupplementalDriver`** | `conductor/drivers/supplemental.py` implements `DeviceDriver` (sACN/DDP/WLED/FPP) and `Services.target` would pick it for the registry devices it serves (today it picks by purpose only); its devices would get their own executor. Kiosk copy must never credit it to Hubspace (05 #12). | Interface stub; every call raises `NotImplementedError`. |
| **Presence triggers** | A new request *source* for `CanopyEngine.request_mode(mode_id, source="presence")` (and the vignettes' `scene()`), fed by a small service on the Clock (a sensor webhook or a camera count) with its own cooldown, below guest input (kiosk wins) and above idle hold. The pause (`pause_guest_input`) would silence it too. | `request_mode` already takes any `source`; cooldown and pause rules key off `source == "kiosk"`. |
| **Audio** | The same seam as presence (a `source="audio"` mode request or scene), plus an optional audio cue alongside a mode change published on the `canopy` WS topic for the kiosk page to play. Never audio-reactive lighting through Hubspace: the write budget (≤ 1 write/s per device) can't follow a beat. | Nothing. |
| **Shows beyond the six modes** | New entries in `config/modes.yaml` (validated by `conductor/looks/schema.py`; the engine, commissioning and kiosk read the list, not a count). A seventh T1 mode needs a slot: there are three (`custom-1…3`), so a new canvas mode shares or replaces one, or runs at T3 (`modules`). The kiosk's 2 × 3 grid (`web/kiosk/kiosk.css`) is laid out for six. A timed show = a sequence of mode requests from a scheduler task on the Clock, `source="show"`. | Six modes; the 2 × 3 kiosk grid. |

## HTTP API (P0 / P0b)

| Method + path | Who | What |
|---|---|---|
| `GET /api/ws-ticket` | any principal | `{ticket, expires_in}` bound to the caller's role and grants |
| `GET /enroll/<token>` | open (token is the credential) | one-time enrollment → sets `cc_session`, redirects to the role's page |
| `POST /api/roles/enrollments` | ops | create a kiosk/vignette/tester link `{role, name, grants}` |
| `GET/DELETE /api/roles/sessions` | ops | list / revoke enrolled devices |
| `GET /api/devices` | ops | registry + capabilities + status + lock holder, per device |
| `GET /api/accounts` | ops | `AccountStatus` per account |
| `POST /api/accounts/login` | ops (JSON) | `{account, email, password}` → `LoginOutcome` |
| `POST /api/accounts/otp` | ops (JSON) | `{nonce, code}` → `LoginOutcome` |
| `POST /ops/accounts` | ops, form, only with `CONDUCTOR_ACCOUNT_CONNECT=1` | same, hardened per 07 §3 |
| `POST /api/accounts/{account}/register` | ops | `{metadevice_id, device_id}` → sets the registry overlay |
| `GET /api/bench/jobs` | ops | JobSpecs + which driver bench runs on (simulated?) |
| `POST /api/bench/run` | ops | `{job, device_id, params}` → `{run_id}`; progress on topic `bench` |
| `GET /api/bench/runs[/{id}]` | ops | results; `/{id}/anonymized` = the copyable JSON |
| `POST /api/bench/runs/{id}/flags` | ops | apply proposed DeviceFlags to the overlay (after a human confirms) |
| `GET /api/module` | ops, tester | devices this principal may open |
| `POST /api/module/{id}/open` / `close` | ops, tester(grant) | take/release the device lock → `{lease}` |
| `GET /api/module/{id}` | ops, tester(grant) | map (positions, wire order), capabilities, desired/reported per head, slots, flags, telemetry |
| `POST /api/module/{id}/paint` | … | `{lease, slot, heads:[{index, r,g,b,cct,colorBrightness,whiteBrightness}] or fill/gradient}` → T1 write+select |
| `POST /api/module/{id}/whole` | … | whole-module look (`Look` JSON) |
| `POST /api/module/{id}/card` | … | `{lease, card, params}` one-tap test cards |
| `POST /api/module/{id}/sweep` | … | `{lease, action: start|pause|resume|stop, step_s}` |
| `POST /api/module/{id}/slot` | … | `{lease, slot, action: select|write_current|read}` |
| `POST /api/module/{id}/effects` | … | `{lease, slot, motionEffect, brightnessEffect, speeds, action: start|restart}` (bench-enabled codes only) |
| `POST /api/module/{id}/calibration` | … | `{lease, cable_path, start_corner, orientation, wire_order?}` → overlay |
| `POST /api/module/{id}/hold` | … | `{lease, on, name}` photo mode |

## Test plan

- **Unit (make test, CI):** every contract module (done), SimDriver fault model with seeded randomness, executor
  semantics (coalescing, rate, retries, locks, gate) on the virtual clock, tracker statuses, Afero adapter against
  a fake transport (row shapes, epoch-ms stamps, MAX_RETRIES=1, outcome classification, token rotation
  persistence, open() wrapper), credential canary, roles/path rules, ws ticket/origin, bench jobs on the sim,
  module API on the sim.
- **Writer-path allowlist test:** executor, reconciler, idle hold, slot manager, bench, module page and vignettes
  each attempt a write to a non-allowlisted id through a real AferoDriver on a fake transport → DENIED, zero
  requests on the wire. Grep test: no aioafero import and no aiohttp write outside `conductor/drivers/afero*.py`.
- **Soak (`make soak`):** 8 simulated hours on `VirtualTimeLoop` with `config/sim/acceptance.yaml` → report.
- **E2E (`make e2e`, Playwright, not in CI):** module page paint/sweep/lock release; kiosk selection reaches the
  SimDriver and the visualizer; screenshots of every surface at kiosk and phone sizes, reviewed by the lead.
- **Container:** `docker build` + run + curl smoke (same as CI) before each PR.

## Delivery

PRs in order, each from its own `claude/*` branch stacked on the previous one, each asking for a merge commit:
1. `claude/p0-contracts` — this plan, DECISIONS, contract modules and their tests.
2. `claude/p0-gate-drivers` — SimDriver, Afero adapter + choke point, executor, tracker, roles/ws, services.
3. `claude/p0-bench` — accounts (CLI + page), bench jobs + pages + clock + CLI, runbook.
4. `claude/p0b-module` — `/ops/module`.
5. `claude/p1-engine`, 6. `claude/p2-looks`, 7. `claude/p3-surfaces`, 8. `claude/p35-venue`, 9. `claude/p4-vignettes`.
