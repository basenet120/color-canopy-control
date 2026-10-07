# Module viewport repair — September 28, 2026

The Module viewport previously drew unrecognized presets with three colors derived from a hash of the preset name. These could be unrelated to the real light. All firmware presets were rendered as static images, and the interface could say every head matched even though it had only confirmed preset settings.

The updated viewport:

- Uses the observed RGB behavior for Fade 3 and Jump 3, paired rainbow gradients, Rainbow Road travel, and Rainbow Roll pulsing.
- Animates those models locally, with a pause control and reduced-motion preference support. Ordinary state refreshes retain animation phase.
- Moves fixed color values between LED positions for Rainbow Road, so travel is visibly distinct from Rainbow Roll pulsing in place. The preset selector now explains those similar names.
- Provides a preview-only timing multiplier from 0.25× to 4×, saved locally for the selected device, preset and hardware speed. Adjusting it preserves preview phase and sends no light command.
- Identifies timing and phase as unsynchronized illustrations. Hubspace reports selected settings, not a live stream of LED animation frames.
- Shows neutral grey and an unavailable-preview notice for unsupported preset models instead of inventing colors.
- Refreshes settings every five seconds while the visible Module session is open, with no overlapping reads or additional light writes. Hidden/closed sessions stop requesting refreshes.
- Compares the actual setting values separately from displayed colors, and no longer claims that matching preview pixels prove physical output.

Validation: 26 Python regression/structure/render checks and 6 JavaScript animation/lifecycle checks passed. JavaScript syntax passed. The existing transport timeout test was given 300 ms instead of 50 ms to accommodate Windows connection startup while retaining its five-second stalled-body test. The app was restarted, reconnected to Hubspace, and returned the new preview fields through its real-device API. The user confirmed the colors are correct and the Rainbow Road preview moves. The user reported that the preview ran slower than the lights, which prompted the local timing adjustment; an exact timing/phase match is not claimed.

## Individual LEDs and custom patterns

Bench Gate A passed. The device exposes three saved custom slots with 60 framebuffer entries each, while the user confirmed 30 physical heads. The app previously treated those two counts as the same and refused per-head painting.

The original patterns are backed up locally in `docs/bench-capability-check/`: Custom 1 red, Custom 2 green, Custom 3 blue. Gate C subsequently passed with the user confirming red → green, enabling frame writes for this specific kit. Address probes confirmed addresses 0, 1 and 29 reach physical heads 1, 2 and 30, while addresses 30–59 light none. Module now uses a device-bound mapping that retains the full 60-entry wire buffer and presents 30 physical heads. The user confirmed a custom red → purple → blue gradient across all 30 heads. See [the custom-pattern session](docs/custom-pattern-session.md) for the latest individual-head results and remaining effects work.

The mapping and address diagnostic add 14 regression checks; all 40 Python checks passed. Private device mappings and account data remain outside the portable package.

In this workshop configuration, Module and Bench can target the real registered kit. The main show engine remains simulated; changing that is separate from commissioning individual-LED control on the bench kit.
