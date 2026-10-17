# 05 — Architecture decisions

These are decided. Build them; don't relitigate them. If real evidence (a failing test, a bench result) says one is wrong, pause that item, write the case under "Decisions needed" in `docs/STATUS.md`, and continue with other work — never silently deviate. How to implement them safely: `07-engineering-contracts.md`.

## One brain, many thin surfaces
A single Python 3.12 service — **the Conductor** — owns all device communication, scene logic and state. Every human-facing surface is a web page it serves. **The show runs on one mini PC at the venue** that auto-starts on boot; it is the only runtime that ever holds show accounts. The same container runs on Railway staging for the bench kit and for demos.

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
                     DeviceDriver interface (swappable)
        .--------------------------+----------------------------.
   AferoDriver                  SimDriver                  SupplementalDriver
   (aioafero, pinned;           (in-memory devices,        (sACN/DDP/WLED/FPP for any
    one bridge per Hubspace     realistic latency,          "supplemental lighting";
    account; REST readback;     fault injection)            interface stub only)
    optional Conclave trigger)
```

## Decisions
1. **Python 3.12 + FastAPI + asyncio** for the Conductor (aioafero is async Python).
2. **aioafero pinned to an exact version**, wrapped behind our own adapter module; nothing outside the adapter imports aioafero.
3. **Config is data, not code.** YAML in `config/`: accounts (references only), devices, canopy geometry (modules, orientation, head template, cable path, calibration overrides), modes, vignette controls, branding. Ops edits (registry, index maps, slot hashes, and the published looks (`07` §8)) go to an overlay under `CONDUCTOR_DATA_DIR`. Passwords are never stored; refresh tokens are stored only encrypted under `CONDUCTOR_DATA_DIR` — never in git (`07` §3, §8).
4. **SimDriver is a first-class citizen**, not a mock afterthought: it models async acks, echo latency, per-account throttling (403/429/silent degradation), 500 outage windows, disconnects and token expiry. Development, CI, demos and any cloud agent session run against it by default.
5. **Live hardware requires an explicit opt-in**: `CONDUCTOR_MODE` + `HUBSPACE_LIVE=1` + a device allowlist, enforced at one choke point in the adapter's transport (`07` §1). Anything that could hit the Afero cloud is off by default.
6. **Command executor**: per-account bounded concurrency; per-account and per-device rate budgets (default ≤ 1 write/s per device, configurable); latest-wins coalescing per device so rapid guest taps never build a queue; retries with backoff on 429/5xx/timeouts/connection errors; response-shape validation; a **re-assert pass** a few seconds after each change, only for devices whose readback disagrees with desired (motion looks re-trigger all nine or none — `07` §6).
7. **Desired vs reported state** per device and attribute. Reported comes from **REST readback** (per-change GETs plus a periodic true-up); Conclave, when enabled, is only a change trigger. UI states are honest: **Ready / Updating / Needs attention** — never fake completion.
8. **Scenes and modes are compiled**, not scripted: a mode compiles to per-device targets using one of a few techniques (per-head frame, whole-strand color, on-controller effect with palette/speed, stepped choreography with a minimum step interval, multi-device vignette look). The executor applies the compiled result.
9. **Web surfaces are static HTML/CSS/JS served by the Conductor**, with all assets vendored (no runtime CDN), large touch targets, WebSocket state push. They must render and degrade gracefully when the internet or a device is down.
10. **Telemetry from day one**: every command, ack, echo and error with timestamps (JSONL or SQLite), surfaced on the ops dashboard and exportable for bench reports and the post-show case study.
11. **Separate Hubspace accounts per vignette** (Home Depot's advice) plus the canopy on its own account(s); the Conductor holds all of them.
12. **Kiosk honesty**: on-screen copy describes what Hubspace is actually doing. If any supplemental (non-Hubspace) lighting is ever used, the copy never attributes it to Hubspace.
13. **Deployment**: `make`-driven dev workflow; Docker Compose + systemd on the venue box (ready by ~Oct 8, `07` §10); Chrome kiosk mode on the canopy touchscreen; iPads in Guided Access pointed at their vignette URL; Railway staging for the bench kit and demos, never show accounts. Runbooks in `docs/runbooks/`.

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
