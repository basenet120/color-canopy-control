# Packaged animations on Hubspace permanent lights — findings and the ask (Oct 22 2026)

Question (Alon, Charleston, Oct 22): is there a format that sends animations faster than one cloud frame per step —
a "packaged", preloaded animation the controller plays itself — that can do what the per-light show does now
(a picture moving in any canvas direction across all nine modules, in step, faster than ~2 s a step)?

**Answer: not today.** On-device effects are fast and need one write, but they move along the wire only, and the
nine controllers aren't phase-locked. Canvas-direction, synchronized, sub-second per-light motion needs Hubspace
(Leedarson firmware + Afero cloud) to change. Precedence for facts: `docs/context/` first (see `ERRATA.md`).

## What was tested (live, Module 7 = `canopy-r0c2`, bench job `effect_sweep`, deployed `408f11a`)
- `motionEffect` 1–12 at speed 50, 8 s each, rainbow, reselect on (runs `20261022T141139-effect_sweep-b08627`,
  `20261022T142435-effect_sweep-e34f4b` under `<data>/bench/`). Every code was accepted; Alon: "the effects work
  great". Per-code behaviour wasn't recorded (the bench page only offers yes/no) — **record it next time**.
- Readback often lags a look (brightness), which stopped the first sweep; fixed on `claude/effect-sweep` (`424cf43`,
  not deployed). `vary: speed` + pattern `band` (`62fdff7`, not deployed) test step rate per `motionSpeed` and a
  Light Show #4-coloured band.

## Why on-device effects can't replace the cloud-stepped show
- Motion shifts the frame along the **framebuffer index = wire order**. Every module is `cable_path:
  serpentine_rows` (8/7/8/7 rows, the wire reverses each row), so motion snakes; software can't choose the path.
- No phase sync between controllers: each starts when its own cloud write lands. Crystal drift is small; the start
  skew from cloud delivery is the real gap. Whether reselecting restarts the phase is untested (gate 9).
- Cloud streaming is capped by the rule of ≤ 1 write/s per device; at 1 s spacing 0 % of frames confirmed (every
  write accepted, superseded before its readback), at 2 s 29 % — so ~2 s per canvas-wide step stays the ceiling.
- 60-entry frames drive 30 heads: whether motion wraps at 30 or 60 entries is untested.

## What on-device effects *can* do (options, none built)
1. **Row-crossing wave:** colour each row uniformly; shifting along the serpentine moves the bands row to row
   (perpendicular to rows), with a sweeping edge. One write per module; modules drift apart.
2. **Direction-free texture:** `brightnessEffect` (untested) breathe/pulse/twinkle over a canvas-designed per-light
   picture (rings, gradients). Wire order doesn't matter.
3. **Hybrid:** keep the 2 s cloud-stepped canvas motion; each frame also carries an on-device shimmer so the canopy
   isn't still between steps.

## Hacky leads not yet tried (cheapest first)
1. **Raw attribute dump (read-only):** `GET https://api2.afero.net/v1/accounts/{aid}/devices/{deviceId16hex}?expansions=attributes`
   lists every MCU attribute as hex — may reveal hidden firmware features (more slots, a sequence buffer, an
   index-order table) and attr 351's byte layout. Unverified that Hubspace tokens may call it. Needs an adapter
   addition (aioafero is imported only in the Afero adapter) and runs only on the show box.
2. **Untested header fields:** `sequenceFlags` (0), `headerFlags` (128), `frameBuffer.flags` (0), `id`, `version` —
   "sequence" + "frameBuffer" suggest room for multi-frame sequences or direction/mirror options. Probe bit by bit
   with a sweep-style bench job (bounded, original restored).
3. **`motionEffect` codes 13–255:** possibly reverse, bounce or per-section motion (each kit = 3 strings × 10, which
   would align with rows). The deployed `effect_sweep` covers this.
4. **Sync by re-trigger** (if reselect restarts phase) or by Afero's server-side rules
   (`/v1/accounts/{aid}/rules`, legacy; unverified for Hubspace tokens).
5. **Raw `attribute_write` to attr 300** (byte-coded whole-strand step programs: `[r,g,b,%,duration LE16]` steps)
   — custom timed module-level sequences; unverified, whole-strand only.

Ruled out: custom firmware (ESP32 eFuse-locked, OTA is partner-realm only); local BLE control (session crypto
unsolved, and bypassing a protection is against our rules); local Wi-Fi API (none); Conclave writes (subscribe-only).

## What Hubspace would need to change (the pitch to Home Depot)
| Change | Where | Unlocks | Prior art |
|---|---|---|---|
| Keyframe sequences: a slot holds N frames + per-frame durations, loops locally | controller firmware | true preloaded animations, any picture, any speed | the slot header already hints at it |
| Pixel map: per-head x/y or an index-order table | firmware + app (camera mapping) | motion follows the layout, not the wire | WLED 2D/serpentine, Twinkly mapping |
| Group time sync: effects start/loop on a shared wall-clock time | firmware (devices already keep time for schedules) | nine controllers as one canopy | Twinkly / Govee multi-device sync |
| Local real-time stream (opt-in LAN, ~20–40 fps) | firmware + app | live shows, music sync | Hue Entertainment API, Govee LAN, WLED DDP/E1.31 |
| Synchronized multi-device write; sanctioned developer API with published rate limits | Afero cloud | reliable shows, third-party integrators | Afero's old partner API (portal now dead) |

Home Depot would commission Leedarson (firmware) and Afero (platform). Our case: the only nine-kit, per-light,
multi-account Hubspace show running, with kiosk, Design Lab and canvas-to-light mapping — a reference design for a
"Hubspace Pro / Light Shows" tier; this activation is the demo.

## Next steps if resumed
1. Record what codes 1–12 do (and 13+), then `brightnessEffect`, on one module (deployed tool, no deploy).
2. Deploy `claude/effect-sweep` tip (full suite first) for `vary: speed` and `band`.
3. Build the read-only raw-attribute dump (adapter + bench job), then the header-flag probe.
4. Draft the Home Depot pitch from the table above.
