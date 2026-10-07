# Live workshop test session — September 28, 2026

App folder: `outputs/color-canopy-local-testing/color-canopy-workshop` in the September 28 `im-x20` chat.

The assistant will operate startup and available test commands; the user observes the real light and reports the physical result. One command at a time. A software success or cloud readback alone does not establish a physical pass.

The Module checks below exercised the running app's API with its normal authorization, device lock and command executor. They establish live-control behavior; the full tracker cases also require browser-button coverage and additional values, which remain unrun unless separately recorded.

| Test | Result | Evidence / next step |
|---|---|---|
| LOC-01 Running-copy baseline | Recorded | User supplied the exact running folder; saved settings select bench mode with live Hubspace enabled. |
| ACC-01 Show devices | Passed after repair | App discovered two real devices; user successfully selected the intended bench kit. |
| ACC-02 Register bench-kit | Passed | User confirmed “Registered as bench-kit”; real ID saved in local registry. |
| ACC-03 Allow registered kit | Passed | Ran `conductor.workshop allow --yes`, restarted, and opened bench-kit with `simulated:false`, driver `afero`, no read error. |
| MOD-05 Power off/on | Passed, physically confirmed | Both writes returned HTTP 200 / applied. User confirmed the real light turned off and back on. |
| MOD-06 Brightness | Live control passed, timing caveat retained | User confirmed an increase to 100% in rainbow mode and a decrease from 50% to 20% in steady white. First rainbow dimming had delayed readback and no immediately observed change. |
| MOD-07 White temperature | Low/high live checks passed | User physically confirmed warm white at 2200 K and cool white at 6500 K, both at 50%. Middle-temperature case remains unrun. |
| MOD-08 Colour | RGB live checks passed | Red, green and blue at 50% passed cloud verification and user observation. The additional show-colour/UI case remains unrun. |
| MOD-09 Selected presets | Seven live variants recorded | Rainbow Road motion/speed, paired Gradient Rainbow, Fade 3 behavior and Jump 3 flashing observed. See `preset-functionality-session.md`; remaining presets and browser controls are unrun. |

## UI-001: device discovery fails after sign-in

Type: functional. The app read the Hubspace response after aioafero had released it. Applied the prior validated response-lifecycle repair to the user's actual running copy. All 20 local regression/structure tests passed. State: **live retest passed**: discovery, registration, real writes and power readback succeeded; power transitions were physically confirmed by the user.

## UI-002: misleading authorization and simulation wording

The Accounts guidance was always visible, including after authorization. It now explains that each device's badge shows its access and that Writable devices are already enabled. Animation descriptions now say “on-screen preview is approximate” because the old “simulated approximation” suffix also appeared for real hardware. Both source copies were updated and the app was restarted to load the change.

## Timing observation

The first brightness write was accepted by Hubspace but readback lagged beyond the app's immediate verification window. A later fresh read showed the requested 20% before the retry was sent. This is recorded as delayed readback, not proof of a rejected command or a physical pass.

| Action | Cloud result | Physical observation |
|---|---|---|
| Power off | Applied; reported off | User: “Yes, the light turned off.” |
| Power on | Applied; reported previous rainbow preset at 98% | User: “Yes, it turned back on.” |
| Brightness 20% | Accepted; initial readback mismatch, later reported 20% | User requested retry. |
| Brightness 20%, retry | Applied; reported 20% | User: “No, it still looks the same.” |
| Brightness 100% | Applied; reported 100% | User: “Yes, it became brighter.” |
| Warm white 2200 K, 50% | Applied; matching readback | User: “Yes, steady warm white.” |
| Warm white brightness 50% to 20% | Applied; matching readback | User: “Yes, it dimmed.” |
| Cool white 6500 K, 50% | Applied; matching readback | User: “Yes, it became cool white.” |
| Red, 50% | Applied; matching readback | User: “Yes, steady red.” |
| Green, 50% | Applied; matching readback | User: “Yes, steady green.” |
| Blue, 50% | Applied; matching readback | User: “Yes, steady blue.” |
| Return to warm white 2200 K, 50% | Applied; matching readback | This final return command was not separately physically confirmed. |

## Current state and coverage

At the end of the initial whole-module checks, the app was running in real bench mode at http://localhost:8000/ops/module/, with the authorized bench kit reporting warm white at 2200 K and 50%. Those test locks were released. Later custom-pattern checks change this output; see [the custom-pattern session](custom-pattern-session.md) for their results. The other account device remains unregistered/read-only.

Import `live-workshop-2026-09-28.test-center.json` into the Test Center to review these observations as separate live variants. The original 88-case interface checklist remains intact; API checks do not silently mark untested browser workflows complete.

## Preset follow-up

Five built-in presets were exercised in seven variants. Rainbow Roll at -8 flashed in place; spatial motion was not observed. Rainbow Road at 0 traveled between LEDs and at -8 moved very slowly. Gradient Rainbow displayed a static rainbow progression in matching adjacent pairs. Fade 3 slowly cycled red, green and blue through darkness. Jump 3 at -3 showed only red within the 20-second window; at +5 the user confirmed sharp RGB changes.

The +5 Jump 3 command returned HTTP 200 but immediate readback still differed on color-mode. Physical operation passed; that cloud-verification discrepancy is retained. Both timed Jump 3 tests automatically returned to warm white. A final fresh read confirmed real-driver operation, white 2200 K at 50%, no read error, and the test lock was released.
