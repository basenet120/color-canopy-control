# Custom patterns and individual LEDs — September 28, 2026

Tests use the real bench kit through the running app’s device lock, executor and readback. The user supplies physical observations. Existing custom slots were backed up before any overwrite.

Gate C passed physically on the repeat: Custom 1 supplied a red BEFORE look, then Custom 2’s green frame was copied into Custom 3. Hubspace returned HTTP 200 and the expected frame hash. The user confirmed red → green. The app recorded that answer and enabled T1 frame writes for this specific kit.

| Controller addresses | Command | User observation |
|---|---|---|
| 0 | Red; all others off | Only the first head was red |
| 1 | Green; all others off | Only the second head was green |
| 29 | Blue; all others off | Only the thirtieth / last head was blue |
| 30–59 | Red; first 30 off | All 30 physical heads were dark |

The Module page now supports a device-bound controller address map. For this kit, 30 physical heads map to addresses 0–29 in a 60-entry framebuffer. Static Paint commands expand to 60 entries with unused addresses off. Slot loads and viewport readback map back to the 30 displayed heads. A changed kit, changed buffer size, inconsistent slot lengths or missing Gate C proof still refuses per-head writes.

This mapping is saved in the local private registry, outside the portable ZIP. It does not claim that the 8/7/8/7 panel geometry matches the physical installation or that every intermediate head has been individually swept.

The new Bench “Address probe” job is reusable from the Bench page. It requires Gate C, validates the requested address range, preserves the controller frame length and static header, saves the previous slot as a run artifact, and does not infer a physical map automatically.

## Module Paint: gradient

Module Paint API: applied, HTTP 200; slot custom-3 (60 controller entries; 30 heads on the map) at 40%.

Physical observation: Yes — a steady red-to-purple-to-blue gradient

## Module Paint: marker

Module Paint API: applied, HTTP 200; slot custom-3 (60 controller entries; 30 heads on the map) at 40%.

Physical observation: Yes — only head 15 is green

## Custom effects from Windows

The user is using the Windows app, not the phone app. The connection library contains no verified custom-effect table. A new timed Bench effect test exercises one explicitly selected candidate at 30% brightness for at most 20 seconds, restores the original slot header and pattern, and records the physical observation. It does not automatically enable or assign a behavior name to the candidate. Motion code 1 comes from an existing simulator fixture and is only a candidate until physically observed.

Effect testing exposed a validation gap: canonical slot hashes contain colors only, so the previous write comparison ignored effect codes and speeds. Full slot writes now compare all requested effect fields as well as colors. Explicit hash-only expectations retain their original color-only meaning. Earlier successful cloud results below do not establish that the effect headers matched.

The diagnostic can now use separated red, green and blue blocks with dark gaps, making motion easier to distinguish from flashing. It also leaves individual mode and re-enters it before applying the candidate, and records actual effect-header readback.

- motionEffect candidate 1, speed 30: pattern rainbow; effect-header readback not checked by the earlier implementation; write applied; restore applied. Full mode reset before candidate: no. Physical observation: User reports only seeing one green light; observation of the timed rainbow interval is not confirmed..
- motionEffect candidate 1, speed 30: pattern rainbow; effect-header readback not checked by the earlier implementation; write applied; restore applied. Full mode reset before candidate: no. Physical observation: User watched the repeat: rainbow appeared but stayed still. No visible motion at code 1, speed 30..
- motionEffect candidate 1, speed 30: pattern rainbow; effect-header readback not checked by the earlier implementation; write applied; restore applied. Full mode reset before candidate: yes. Physical observation: After full mode reset, user saw slight flashing but no clear movement between heads..
- motionEffect candidate 1, speed 30: pattern blocks; effect-header readback matched; write applied; restore applied. Full mode reset before candidate: yes. Physical observation: Awaiting user observation.

Validation: 51 Python checks passed, including address probes, controller mapping, effect-field readback comparison, and timed-effect restoration/cancellation. The previously validated viewport animation has 6 JavaScript checks. No real credentials, registry mappings or account data are added to the portable source package.
