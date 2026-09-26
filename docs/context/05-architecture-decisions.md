# 05 — Architecture decisions

These are decided. Build them; don't relitigate them. If real evidence (a failing test, a bench result) says one is wrong, stop and write the case in `docs/STATUS.md` instead of silently deviating.

## One brain, many thin surfaces
A single Python 3.12 service — **the Conductor** — owns all device communication, scene logic and state. Every human-facing surface is a web page it serves. It runs on one mini PC at the venue and auto-starts on boot.

```
 Canopy kiosk      4 vignette tablets      Ops dashboard      Canopy visualizer
 (guest touch)     (staff, Guided Access)  (tech team)        (preview + design review)
        \                 |                     |                    /
         '---------- HTTP + WebSocket on the show LAN --------------'
                                   |
                           THE CONDUCTOR (FastAPI, asyncio)
   config loader · scene/mode engine · command executor · state tracker (desired vs reported)
   reconciler (re-assert) · telemetry · calibration · auth/token manager
                                   |
                     LightingDriver interface (swappable)
        .--------------------------+----------------------------.
   AferoDriver                  SimDriver                  SupplementalDriver
   (aioafero, pinned;           (in-memory devices,        (sACN/DDP/WLED/FPP for any
    one bridge per Hubspace     realistic latency,          "supplemental lighting";
    account; Conclave push)     fault injection)            interface + sim now)
```

## Decisions
1. **Python 3.12 + FastAPI + asyncio** for the Conductor (aioafero is async Python).
2. **aioafero pinned to an exact version**, wrapped behind our own adapter module; nothing outside the adapter imports aioafero.
3. **Config is data, not code.** YAML in `config/`: accounts (references only), devices, canopy geometry (modules, orientation, head template, cable path, calibration overrides), modes, vignette controls, branding. Secrets (refresh tokens) come from env vars or a gitignored `secrets/` dir — never from git.
4. **SimDriver is a first-class citizen**, not a mock afterthought: it models async acks, Conclave-style echo latency, per-account throttling (429/403/silent degradation), 500 outage windows, disconnects and token expiry. Development, CI, demos and any cloud agent session run against it by default.
5. **Live hardware requires an explicit opt-in** (`HUBSPACE_LIVE=1` plus configured accounts). Anything that could hit the Afero cloud is off by default.
6. **Command executor**: per-account bounded concurrency; per-account and per-device rate budgets (default ≤ 1 write/s per device, configurable); latest-wins coalescing per device so rapid guest taps never build a queue; retries with backoff on 429/5xx/timeouts/connection errors; response-shape validation; a **re-assert pass** a few seconds after each scene for any device whose reported state disagrees with desired.
7. **Desired vs reported state** per device and attribute. Reported comes from Conclave events with a slow REST true-up. UI states are honest: **Ready / Updating / Needs attention** — never fake completion.
8. **Scenes and modes are compiled**, not scripted: a mode compiles to per-device targets using one of a few techniques (per-head frame, whole-strand color, on-controller effect with palette/speed, stepped choreography with a minimum step interval, multi-device vignette look). The executor applies the compiled result.
9. **Web surfaces are static HTML/CSS/JS served by the Conductor**, with all assets vendored (no runtime CDN), large touch targets, WebSocket state push. They must render and degrade gracefully when the internet or a device is down.
10. **Telemetry from day one**: every command, ack, echo and error with timestamps (JSONL or SQLite), surfaced on the ops dashboard and exportable for bench reports and the post-show case study.
11. **Separate Hubspace accounts per vignette** (Home Depot's advice) plus the canopy on its own account(s); the Conductor holds all of them.
12. **Kiosk honesty**: on-screen copy describes what Hubspace is actually doing. If any supplemental (non-Hubspace) lighting is ever used, the copy never attributes it to Hubspace.
13. **Deployment**: `make`-driven dev workflow; Docker Compose or a systemd unit for the venue box; Chrome kiosk mode on the canopy touchscreen; iPads in Guided Access pointed at their vignette URL. Runbooks in `docs/runbooks/`.

## Explicit non-goals for v1
- Device provisioning or ownership transfer (the official app does that, once, during install week).
- BLE control, custom firmware, Matter.
- Anything that evades a technical block (rotating IPs or accounts after throttling/bans).
- Cloud-dependent "panic" buttons presented as safety controls — venue lighting stays independent.
- Phase-2 ideas (presence triggers, audio-reactive, choreographed shows beyond the six modes) — design seams for them, don't build them.

## Acceptance targets (from VWBD's roadmap, adapted to 9 strands)
These are the bar for bench and rehearsal; the software must make them measurable.
- 95% of mode changes begin visibly within 2 s of Apply; full look settled within 5 s (every change within 10 s).
- Looks meant to change together: first-to-last module spread ≤ 2 s at p95.
- Any wrong head/module self-heals within 10 s (re-assert), across 100 mode changes.
- 8-hour soak at expected guest cadence: no queue growth, no silent failure, no operator reset.
- Recovery: after internet returns, the current look is re-applied within 60 s.
