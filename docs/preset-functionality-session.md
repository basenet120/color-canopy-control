# Preset functionality tests — September 28, 2026

Real bench kit; commands use the running app's authorization, device lease, executor and readback. Physical results come from the user. These checks cover selected built-in presets, not all presets or the browser controls.

| Preset check | Result | App / cloud | Physical observation | Notes |
|---|---|---|---|---|
| Rainbow Roll, speed -8, 40% | Fail | App API: rainbow-roll, brightness 40%, speed -8; HTTP 200, applied. Readback: preset rainbow-roll at 40% (on-screen preview is approximate). | User: colors flash but do not switch LED diodes. | Preset selection and readback matched; the requested movement between LEDs was not observed. |
| Rainbow Road, speed 0, 40% | Pass | App API: rainbow-road, brightness 40%, speed 0; HTTP 200, applied. Readback: preset rainbow-road at 40% (on-screen preview is approximate). | User confirmed colors travel between LEDs. |  |
| Rainbow Road, speed -8, 40% | Pass | App API: rainbow-road, brightness 40%, speed -8; HTTP 200, applied. Readback: preset rainbow-road at 40% (on-screen preview is approximate). | User confirmed it became very slow. | Visible speed reduction compared with speed 0; -8 is impractically slow for this test. |
| Gradient Rainbow, speed 0, 40% | Pass | App API: gradient-rainbow, brightness 40%, speed 0; HTTP 200, applied. Readback: preset gradient-rainbow at 40% (on-screen preview is approximate). | User confirmed static matching-color pairs progress through rainbow colors along the strand. | Native gradient uses matching adjacent pairs; it is not an individually unique color for every LED. |
| Fade 3, speed 0, 40% | Pass | App API: fade-3, brightness 40%, speed 0; HTTP 200, applied. Readback: preset fade-3 at 40% (on-screen preview is approximate). | User observed very slow red-to-green-to-blue cycling, with darkness between transitions. | Observed behavior is fade-through-black. Seamless crossfading was not demonstrated; this records the actual native preset behavior. |
| Jump 3, speed -3, 20%, 20 seconds | Fail | App API: jump-3, brightness 20%, speed -3; HTTP 200, applied. Readback: preset jump-3 at 20% (on-screen preview is approximate). | User saw only red, then the automatic return to warm white. | No color jumps were observed within the 20-second window. The speed may be too slow for this window; a faster follow-up is needed. Warm white restoration was verified. |
| Jump 3, speed +5, 20%, 20 seconds | Pass | App API: jump-3, brightness 20%, speed 5; HTTP 200, mismatch. Readback: white 2200 K at 20%. | User confirmed sharp red/green/blue changes during the second test. | Physical flashing/color-jump behavior passed. Immediate cloud verification reported a color-mode mismatch; this discrepancy is retained and no software verification pass is claimed. Automatic warm-white return applied successfully. |

Custom motion/brightness effect codes and hand-painted gradients need separate commissioning; their existing gates were retained.

The light was returned to warm white at 2200 K and 50%. A fresh read confirmed this final state through the real driver with no read error, and the test lock was released. The app remains running.

Results describe the observed firmware presets. A missed spatial-motion expectation or a test window that showed only red does not establish that the preset selector is broken. The Jump 3 +5 physical pass is separate from its immediate cloud-readback mismatch.
