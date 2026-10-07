# Color Canopy: resume on VWBD

Prepared October 2, 2026. Physical observations below were collected September 28, 2026. This document is the current handoff; statements in older session reports about an app being running or a final light state are historical, not a claim about today.

## What the user wants

Continue building and testing the local Hubspace app, especially individual LEDs, custom patterns and effects. The assistant should run setup and commands where possible; the user watches the physical lights and supplies observations. The user is working from the computer, not the Hubspace phone app. Keep cloud readback, browser behavior and physical behavior as separate evidence.

The user explicitly chose `color-canopy-workshop-kit-1eb8551.zip` as the baseline. This distribution contains that baseline plus the fixes and testing structure made in the local Windows session. Do not replace it wholesale with the older remote checkout. The former VWBD chat, **Troubleshoot Hubspace control app** (`01a0e283-d4eb-7df0-9f58-96a363797625`), supplied the recovered test workflow. Workflow commit `54190de` and later connection repair `62ffba9` are background provenance, not a claim that this snapshot is either Git commit.

## Resume here

Individual-head addressing and static custom gradients passed physical checks. **Custom motion is still unconfirmed.** The last test used motionEffect code 1, speed 30, separated red/green/blue blocks with dark gaps, for 20 seconds. Full color and effect-header readback matched, and restoring the previous pattern succeeded. The user has not supplied a physical observation for that last blocks test.

Ask whether they remember that test; otherwise repeat it after they are ready to watch. Earlier rainbow versions appeared still or slightly flashing, without clear travel. Code 1 comes only from a simulator fixture; there is no verified vendor effect-code table in the connection library. Do not label it a known motion type or enable it globally. `effects.enabled` remains empty.

## Transfer and startup

1. Extract `color-canopy-vwbd-2026-10-02.zip` into a new folder on VWBD. The app is the enclosed `color-canopy-workshop` folder. Do not run from inside the ZIP. Read `VALIDATION.md` and, if desired, verify `MANIFEST.sha256` before making changes.
2. Preserve any existing VWBD installation and its local settings. If upgrading that installation, back it up and merge this source after reviewing local changes. Keep its `workshop.env` and matching `workshop-data` together: the environment contains the encryption key for saved sign-in. Do not overwrite them with placeholders. If moving private runtime data between folders, resolve `CONDUCTOR_DATA_DIR` to the actual destination path. This public-to-the-user source bundle deliberately contains neither file nor directory.
3. On Windows, run `start.bat` from the app folder. In a terminal, `start.bat run` is explicit real bench startup. On macOS/Linux, run `bash workshop/start.sh run`. First startup installs uv if needed and resolves the supplied lockfile with the live extra. Python 3.12 or newer is required. No virtual environment binaries are bundled.
4. A fresh installation asks for the bench account email and prints its generated page username/password. This is the local page sign-in, not yet the Hubspace account sign-in. Open `http://localhost:8000/ops/accounts/`, sign in to Hubspace there, select Show devices, and register the intended module with Use as bench kit.
5. If that runtime has not authorized this registered device, stop its server, run `start.bat allow` (or `bash workshop/start.sh allow`), then restart. Registration and permission to write are separate. The explicit allow step should apply only to the selected bench kit. Check the device's Writable badge and the real driver state; a generic read-only explanation by itself is not the current device's access status.
6. The default configured write-until date is **October 19, 2026**. Inspect the existing runtime's actual setting if testing later; do not diagnose an expired write window as a connection defect.
7. Open Module at `http://localhost:8000/ops/module/` or Bench at `http://localhost:8000/ops/bench/`. Start with fresh readback. Click **Done** on Module before starting a Bench job; Module holds a device control lock while open. Avoid running two independent servers against the same kit during testing.

`start.bat sim` and `bash workshop/start.sh sim` explicitly start simulator practice. Normal workshop bench mode intentionally keeps the main Ops/kiosk/show engine simulated while **Module and Bench can control the real authorized kit**. Seeing simulation elsewhere does not establish that Module is simulated. Do not switch the whole show engine to live merely to remove that label.

The former `http://127.0.0.1:8765/` page was a static test tracker preview, not the Hubspace control server. Use port 8000 for the app unless VWBD deliberately changes it.

Windows fallback, only if the supplied uv launcher cannot run: create an accessible Python virtual environment and install `workshop/validated-windows-requirements.txt`, then use that interpreter with `-m conductor.workshop run`. These are the exact dependencies used in the repaired Windows runtime; the lockfile remains the normal cross-platform installation path. Do not copy the old machine's virtual environment.

## Carry over commissioning for the same physical kit

The local source computer had T1 frame writes proven on its registered bench kit and this device-bound map:

- 30 physical light heads.
- Three custom slots each report 60 controller entries.
- Displayed heads 1–30 map to controller addresses 0–29.
- Addresses 30–59 were observed to affect no connected head and are written off.

`docs/bench-commissioning-proof.json` carries a SHA-256 digest of the original registered device ID and the observed mapping, without exposing the ID. It is evidence, not a live registry or authorization. Compare the digest to the freshly registered VWBD `metadevice_id` using UTF-8, stripped of surrounding whitespace and without a newline. Do not print the actual ID or credentials into shared logs.

For an identical device, first use fresh Gate A readback to confirm the expected 30-head setup and consistent 60-entry slots. Review the included Gate C and address-probe evidence. The receiving assistant may then carry the previously proven `t1_frames: true` and map into the local registry using `conductor.config.save_overlay_patch`, bound to that exact registered ID. Preserve any independently proven flags already on that same device. The relevant fields are `flags.t1_frames`, `flags_proven_on`, and `framebuffer_mapping: {frame_length: 60, indices: [0, ..., 29], proven_on: <current registered ID>}`. Stop the server for an offline registry edit and restart to load it. This does not replace the runtime's own device allowlist.

If the digest, frame size or physical kit differs, the old proof does not apply: commission that kit through the normal gates and physical observations. This transfer asserts no Gate B/T2 or device-level readback proof. It also does not prove the drawn 8/7/8/7 panel geometry or that every intermediate head has been swept individually.

## Changes already made

| Area | Change and practical result |
|---|---|
| Hubspace connection | In `conductor/drivers/afero.py`, read the body inside `async with bridge.create_request(...) as response`. The pinned aioafero 9.3.0 `request` helper released the response too early. Discovery and real control worked after the repair. Whole-operation timeout, authorization gates and single-attempt behavior remain. See `CONNECTION-REPAIR.md`. |
| Authorization wording | Accounts explains device badges rather than always implying an authorized kit is read-only. Preview text distinguishes an approximate on-screen animation from a simulator driver. |
| Module colors and motion | Replaced fake colors derived from preset names with known palettes and explicit unavailable previews. Added moving Rainbow Road, in-place Rainbow Roll pulses, paired static Gradient Rainbow, Fade 3 through black, and Jump 3 RGB changes. Traveling patterns move colors between LED addresses. |
| Preview timing | User confirmed correct colors and visible movement, but the viewport was slower. Added a local 0.25x–4x Preview timing control, saved by device/preset/hardware speed. It does not change the physical light speed. Timing and phase remain approximate; cloud state does not provide live animation frames. The user's final calibration value was not captured. |
| Preview refresh | Refreshes selected settings every five seconds while visible, avoids overlapping reads and stale-session updates, respects reduced motion, and distinguishes requested settings from readback. Browser cache changes are included. See `VIEWPORT-REPAIR.md`. |
| 30/60 mapping | Added validated `FramebufferMapping` in `conductor/config.py`, bound to one real device. Module Paint expands 30 heads to 60 controller entries, leaving unmapped entries off; slot load/readback maps back to 30 heads. Frame size and Gate C checks remain. |
| Bench address probe | Tests a specified address range, preserves header/frame length, saves the prior custom slot, and records human observations. It requires T1 and does not infer a physical mapping automatically. Its look stays visible after completion. |
| Bench effect probe | Tests one explicit motion/brightness candidate for 2–20 seconds at 30% brightness. Supports rainbow or high-contrast blocks, leaves and re-enters individual mode, and attempts to restore the saved slot/header and brightness after the test or cancellation. It never automatically enables an effect. |
| Effect readback | Full slot writes now verify all requested effect fields as well as the color hash. Previously the canonical hash excluded the header, allowing a color match to hide an effect mismatch. Explicit hash-only requests keep color-only semantics. The latest blocks test used the corrected verifier. |

## Physical test results

| Test | User observation / qualification |
|---|---|
| Power off and on | Both physically confirmed. |
| Brightness | Increase to 100% was visible; steady warm white dimmed from 50% to 20%. The first rainbow 20% attempt had delayed readback and no observed dimming; that attempt is not a physical pass. |
| White | Steady warm white at 2200 K and cool white at 6500 K, both 50%, confirmed. |
| RGB | Whole strand steady red, green and blue confirmed. |
| Rainbow Roll, -8, 40% | Colors flashed in place, no spatial travel observed. This is not proof the selector failed. |
| Rainbow Road, 0 then -8, 40% | Colors traveled between LEDs. -8 was very slow. |
| Gradient Rainbow, 40% | Static matching pairs progressed through rainbow colors. |
| Fade 3 | Slowly changed red/green/blue with darkness between transitions. |
| Jump 3 | At -3, only red appeared during 20 seconds. At +5, sharp RGB changes were physically confirmed despite immediate cloud color-mode mismatch. Timed tests returned to warm white. |
| Gate C custom write | Repeated test visibly changed red to solid green after copying the green frame into Custom 3. T1 passed for this kit. |
| Address 0 | Only head 1 red. |
| Address 1 | Only head 2 green. |
| Address 29 | Only head 30 blue. |
| Addresses 30–59 | All 30 physical heads dark. |
| Module Paint gradient | All 30 heads steady red near the controller, through purple, to blue at the last head. |
| Module Paint marker | Only physical head 15 green. |
| Custom motion code 1, speed 30, rainbow | First interval missed; watched repeat stayed still; after a mode reset, slight flashing without clear movement. Those earlier runs did not verify the effect header. |
| Custom motion code 1, speed 30, blocks | Colors and full effect header matched in the final run; restore passed. **Physical result still pending.** |

Last blocks run: `20260928T195904-effect_probe-076887`. Requested and reported header: `brightnessDepth=100`, `brightnessEffect=0`, `brightnessSpeed=50`, `motionEffect=1`, `motionSpeed=30`. The saved pre-test Custom 3 hash was `34668817133b8700`; restore reported motion 0, speed 50. That pattern had changed since the earlier single-green-head test. Read current state and save a new backup before continuing; do not assume the light is still showing one green head.

The earliest saved profile includes static Custom 1 red, Custom 2 green and Custom 3 blue, but these are historical backups. **Do not blindly restore original blue over the user's newer Custom 3.**

## Next live test from the computer

After startup, registration/allowlist and same-kit commissioning checks:

1. Read current state and saved slots. Keep a fresh backup. Ensure Module has released its lock.
2. Tell the user exactly when the observation interval starts and wait for readiness; previous short tests finished before they looked.
3. Use Bench's `effect_probe` with `slot=custom-3`, `field=motionEffect`, `code=1`, `speed=30`, `duration_s=20`, `pattern=blocks`. Expect static blocks, a brief whole-white mode reset, then the candidate interval and restoration. Observe whether lit blocks move to different heads, flash in place, or remain still.
4. Record the human answer separately from HTTP acceptance and full header readback. Verify restoration. Do not scan many unknown codes automatically or claim a behavior from the simulator fixture.
5. Once behavior is understood, select the next bounded candidate or speed test deliberately. Continue UI testing separately; the user reported Module was the only page fully working, and the rest of the app is not yet validated end to end.

The portable CLI uses the running server's normal authorization and locks. With a configured environment and the page username, this prompts privately for the page password:

```text
python -m conductor.bench --server http://localhost:8000 --user workshop run effect_probe --device bench-kit --param slot=custom-3 --param field=motionEffect --param code=1 --param speed=30 --param duration_s=20 --param pattern=blocks
```

Use the app's installed Python (`uv run --no-sync python ...`, or the active virtual environment). A receiving assistant can read its own local `workshop.env` into memory for automation without printing secrets. Do not put passwords in shell arguments or shareable reports. If a tool security policy blocks an action, use a supported tool or ask for the necessary user action; do not work around the policy through another route.

## Included evidence and testing structure

- `TEST-CENTER.html` / `open-test-center.bat`: standalone interactive checklist, JSON import/export, variants, reports. Browser-local storage itself is not portable; import `docs/live-workshop-2026-09-28.test-center.json` to recover the saved session.
- The tracker contains **88 base cases and 22 saved live variants**. Most full interface cases remain Not run. API and physical checks do not silently certify every browser control or preset.
- `docs/current-live-test-session.md`: connection, authorization and whole-module observations.
- `docs/preset-functionality-session.md`: seven selected built-in-preset variants.
- `docs/custom-pattern-session.md`: Gate C, address map, custom Paint and effect history.
- `docs/bench-capability-check/`: 13 freshly scrubbed JSON evidence files, including the saved original profile/patterns and the latest blocks test. Identifiers are placeholders. These are diagnostic records, not runnable private configuration.
- `docs/bench-commissioning-proof.json`: device digest and transfer conditions for the previously observed T1/mapping facts.
- `docs/interface-troubleshooting-workflow.md`, `test-cases.json`, `interface-test-log.md`: recovered test structure. The original remote chat retrieval yielded 86 complete cases plus partial UX-07; UX-07 was completed locally and LOC-01 added to reach 88. This is not an exact recovery of the truncated tail.
- `docs/testing-baseline.json`: original ZIP identity and original hashes. Later repaired files intentionally differ; this is not the release manifest.
- `docs/initial-tracker-validation-2026-09-28.md`: initial pre-live-test record, retained as history.
- `VALIDATION.md`, `docs/release-validation.json`, `MANIFEST.sha256`: current snapshot checks and contents.

Run from the app root:

```text
python -B -m unittest discover -s tests -v
node --test tests/preset-preview.test.mjs
node tests/validate_live_import.cjs
```

No live commands were sent while preparing this distribution. The package excludes local passwords, Hubspace credentials/tokens, account/device authorization, the private runtime registry, runtime databases, virtual environments and machine logs. On VWBD, independently check startup and connection; packaging validation cannot establish that the other computer is already configured or online.
