# Color Canopy: interface troubleshooting workflow

Use this with a person watching the lights and a technical partner or AI examining the app. Work through the workshop controls first, then the venue pages. This is a test plan derived from the current source; the unchecked features have not been certified as working.

> Local baseline: `color-canopy-workshop-kit-1eb8551.zip`, supplied September 28, 2026. Original application files are preserved byte for byte. Open `TEST-CENTER.html` in the app folder for the interactive checklist. See `LOCAL-TESTING.md` for setup and provenance.

## Start here: one test at a time

1. Choose one row from the test inventory below.
2. Agree on what the screen should do and, for a real-device test, what the lights should do.
3. Perform one action. Wait for its result before sending another command.
4. Record the screen result, the physical result, and any error separately.
5. If it fails, repeat from the same starting state once. Capture evidence before restarting.
6. Fix that reproducible issue, repeat the exact test, then check the adjacent controls it could affect.
7. Move to the next row. Keep an independent issue for each distinct failure.

Use `interface-test-log.md` beside this file to keep the session record and issue reports.

**First session:** establish the correct module, then test Power on/off, Set brightness only, Set white, and Set colour. Finish those before attempting per-puck painting or effects.

## Record the starting state

- Date, Git commit, and whether this is the fork or another checkout.
- Browser/device: laptop browser, Codex in-app browser, or phone browser; record which one.
- Runtime mode and the target shown on the page: real bench kit or simulator.
- Account connected, module registered, module allowed, and any capability flags already proven for this kit.
- What the lights currently show; selected slot; brightness; whether Hold or a sweep is active.
- Which other module/bench pages are open. Use **Done** to release a module before a bench job needs it.

Run one server on port 8000. Use the same known checkout throughout a session. A code change needs a server restart; refresh the browser afterward. Avoid making a code change halfway through collecting a before/after comparison.

### Two environments, different evidence

| Environment | How to start | What it establishes |
|---|---|---|
| Simulator | Stop the current server, then `start.bat sim` | Page rendering, navigation, supported simulated actions, forms, ownership, feedback, and error handling. It uses a separate simulator data folder. |
| Real workshop module | Stop the simulator, then `start.bat` | Actual Hubspace commands and physical light behavior for the registered and allowed bench kit. |

In **bench mode**, the show engine and kiosk use the simulator. The single-module page and bench jobs can use the real kit when it has a Hubspace ID and the gate permits bench traffic. This is how `conductor/services.py` routes the work. A working simulated Ops or kiosk control does not prove that the bench light changed.

Readback is also a separate observation: the cloud can accept a value without proving that the physical light displayed it. Record both.

### Status and issue type

| Result | Use it when |
|---|---|
| Not run | No test has been performed in this session. |
| Pass | The agreed UI behavior and applicable physical behavior match. |
| Fail | A supported feature fails despite its prerequisites being met. |
| Blocked | Setup, account state, ownership, or an unproven capability prevents the test. Record exactly what is needed. |
| Not applicable | The hardware or current deployment does not provide this feature. State why. |

Track design feedback separately as **Usability improvement**. A correctly blocked control may still need a clearer explanation and a link to the required setup step.

## Page and feature inventory

Every row starts **Not run**. For a row with several choices, record a separate result for each choice. Cover every advertised preset, program, scene, slot, and mode once. Test numeric controls at minimum, middle, and maximum; use a few deliberate combinations after individual settings work. Record the options actually offered by this device instead of assuming every model exposes the same ones.

### 1. Startup and account setup

| ID | Test | Expected result / evidence |
|---|---|---|
| SET-01 | Start the chosen copy; open Accounts, Module, Bench, and Ops | Correct app is running; pages load with styles/scripts; no permanent Loading state. |
| SET-02 | Open the pages on the laptop and a phone on the same Wi-Fi | Laptop uses localhost; phone uses the laptop address printed at startup; controls fit both screens. |
| ACC-01 | Inspect Bench kit account, then Show devices | Connected state agrees with account status; the device list loads; names and registration state are clear. |
| ACC-02 | Register the intended module using Use as Bench kit | Success message appears; subsequent list and restart retain Registered as bench-kit. |
| ACC-03 | Run the normal allow step and restart | The correct registered kit becomes eligible for control; the UI no longer presents it as unallowed. |
| ACC-04 | Reopen Accounts after a successful login | The sign-in form's intentional disabled state is explained; Show devices still works. |
| ACC-05 | Disconnect, reconnect, and any emailed-code flow | Test in simulator first or in a planned account-maintenance session. Account state updates, the result is clear, and reconnect instructions work. Do not repeatedly log out during control tests. |
| ACC-06 | Wrong/missing input and failed sign-in | Clear actionable message; no secret values echoed; the form recovers for another attempt. Use simulator fixtures for deliberate invalid-input cases. |

### 2. Module page: session, map, and whole-module controls

Page: `/ops/module/`. Choose the intended device and confirm its target before sending commands.

| ID | Test | Expected result / evidence |
|---|---|---|
| MOD-01 | Open device picker, choose kit, press Done, reopen | Correct device loads; ownership is shown; Done releases it for the next session. |
| MOD-02 | Switch among Paint, Module, Tests, Slots, Effects, Calibrate | Each tab opens the matching panel and retains an understandable selected-tab state. |
| MOD-03 | Tap one puck; drag across several; All / None / Invert | Selection count and highlighted pucks agree; selection alone does not send a light command. |
| MOD-04 | Toggle Wire order; inspect reported centre, desired ring, draft marker | Legend matches what is displayed; numbers and wire route remain readable on phone and laptop. |
| MOD-05 | Module → Power on, then Power off, then on | UI reports each outcome and the physical module follows it; note latency separately. |
| MOD-06 | Set brightness only at three values | Supported brightness changes without unexpectedly changing the selected colour or mode. |
| MOD-07 | Set white at supported low / middle / high temperature | White mode and target temperature apply; any clamping to hardware limits is explained. |
| MOD-08 | Set colour: red, green, blue, then one show colour | UI and physical strand agree on the selected colour; compare reported state and visible result. |
| MOD-09 | Every listed preset; speed at low / middle / high | The selected preset runs; the supported speed changes visibly or any limitation is explained. |
| MOD-10 | Every listed program | Each offered program is accepted and shown. Empty capability lists disable the control clearly. |

### 3. Bench capabilities and diagnostics

Page: `/ops/bench/`. Release the module page before starting a job. Gate B and Gate C prove different capabilities; **Apply flags** records the result for the specific kit. A disabled Paint tab before Gate C is setup-blocked, not automatically a defect.

| ID | Test | Expected result / evidence |
|---|---|---|
| BEN-01 | Choose bench-kit; inspect target status | The page clearly identifies real or simulated execution. Job results identify the same target. |
| BEN-02 | Gate A: read-only profile dump | Results identify the device capabilities and frame/slot counts; JSON and profile download work. Record actual counts. |
| BEN-03 | Index order | The reported saved-pattern order can be compared with an intentionally recognisable pattern on the strand. |
| BEN-04 | Gate B: select a slot | Distinct reference patterns make the change observable; the job gives a verdict and collects the physical observation. |
| BEN-05 | Gate C: replay a slot | With distinct reference patterns, the BEFORE state changes to the copied pattern; readback and physical observation are both recorded. This job overwrites a destination slot: record its existing pattern first. |
| BEN-06 | Answer questions and Apply flags | Flags apply only when the run and physical answers support them; blocked application explains why. Verify the module page unlocks the appropriate feature. |
| BEN-07 | Hue ramp | The physical strand displays the index pattern; photo and JSON can be associated with this run. |
| BEN-08 | Latency trials | Trial options apply, progress updates, and recorded timings are distinguishable from physical response measured on video. |
| BEN-09 | Whole-strand looks | Each test look runs and each observation question corresponds to that look. |
| BEN-10 | Stale-clock test | Result distinguishes the old-timestamp write from its current-timestamp control where applicable; record physical observation. |
| BEN-11 | Rewrite the showing slot | Existing pattern is recorded first; job reports readback and asks whether the selected slot visibly updated without reselecting. |
| BEN-12 | Rate ramp | Run once after basic control is stable; progress and throttle/stop behavior agree with the job description. Keep the existing rate budget. |
| BEN-13 | Unplugged Gate B and head-count-at-boot jobs | Schedule as deliberate hardware tests after the ordinary UI pass. Follow the existing bench runbook, provide its confirmations, and restore the full physical kit afterward. |
| BEN-14 | Restore | Returns the test kit to the documented warm-white reference look and reports the outcome. |
| BEN-15 | Stop this run, Recent runs, Refresh, JSON details/export | Stop ends the job and releases ownership; completed results remain accessible; exports are usable. |
| BEN-16 | Clock page: time, connection status, Full screen, PUT flash | `/ops/bench/clock/` updates, fullscreen works or clearly reports browser restrictions, and a bench send can be matched to its timestamp. |

The existing `docs/bench-prototype-01.md` describes the individual jobs in detail. Its Railway setup section belongs to the staging deployment; use this workshop fork's `start.bat` and local settings for this laptop.

### 4. Paint tab

Start after the kit's Gate C result and flags have been confirmed. Record existing slot patterns before overwriting them; use the intended scratch slot for ordinary painting tests.

| ID | Test | Expected result / evidence |
|---|---|---|
| PNT-01 | Show-colour swatches; RGB only / RGB + warm white | Brush preview and chosen recipe agree; choices are clearly distinguished. |
| PNT-02 | Colour picker and colour brightness | Draft responds to supported values without changing the real light until Apply. |
| PNT-03 | White Off / Warm / Cool, temperature and white brightness | Relevant controls appear; draft and applied output reflect the chosen colour/white combination. |
| PNT-04 | Paint selected / Paint all | Only the intended draft heads change; unselected heads retain their draft values where applicable. |
| PNT-05 | Two-colour gradient: left→right and top→bottom | Gradient follows the displayed spatial direction; selected-only behavior matches the page description. |
| PNT-06 | All heads off / Undo changes | Draft-off and draft-reset produce the expected preview; these are distinguished from immediate power commands. |
| PNT-07 | Choose each available destination slot, then Apply | The named slot is written and selected; no silent change to another slot. Record screen result, readback, and actual lights. |
| PNT-08 | Apply with no meaningful change, during a pending command, or while Hold is on | Button state and response prevent confusing duplicate operations; any refusal states what to do next. |

### 5. Tests tab

| ID | Test | Expected result / evidence |
|---|---|---|
| TST-01 | Full white: 2700 / 3000 / 4000 K | Each supported value applies; clamping is explained when needed. |
| TST-02 | Each show colour, whole-strand and painted, both recipes | Chosen colour and route agree with the visible output; painted variants respect capability restrictions. |
| TST-03 | Recipe compare | Left and right recipes appear in the described positions; centre divider remains dark as specified. |
| TST-04 | Thirds split, with distinct colours and brightness values | The three spatial bands match the map and the independent values entered. |
| TST-05 | Hue ramp | Colour sequence follows wire indices; use it to check the physical mapping. |
| TST-06 | Single head: first, middle, last, previous, next | Only the intended physical head lights; boundary behavior is understandable. |
| TST-07 | Index sweep: start, pause, resume, stop, step interval | The active index, actual lit head, and state agree; paused/stopped sweeps stop sending changes. |
| TST-08 | Lowcountry preview: each mode and canopy position | The selected module slice or documented whole-module fallback appears. If unavailable, record the displayed reason and loading/log details. |

### 6. Slots, effects, calibration, and Hold

| ID | Test | Expected result / evidence |
|---|---|---|
| SLT-01 | Inspect each saved slot and preview | Slot name, selected state, and preview match the readback; old data is distinguishable from new data. |
| SLT-02 | Select each slot with distinct known content | Correct stored pattern becomes visible; result reports its selected state. |
| SLT-03 | Each slot action offered by this device: rewrite, read/copy JSON | Action targets the named slot; writing requires the relevant capability; JSON can be copied. |
| SLT-04 | Painted in the Hubspace app — record it; cancel and accept | Only use on a pattern actually painted in Hubspace. Cancel changes nothing; accept records its provenance without sending a pattern write. |
| EFX-01 | Inspect motion/brightness effect availability | Only supported, enabled effect codes are selectable; unavailable effects explain what is missing. |
| EFX-02 | Each enabled motion and brightness effect, plus static | Selected slot and effect match the output; isolate one effect at a time first. |
| EFX-03 | Motion speed, brightness speed, brightness depth | Minimum/middle/maximum supported settings have the documented effect; labels agree with values sent. |
| EFX-04 | Start and Restart | Start applies the requested effect; Restart restarts the selected slot's animation. Disabled/refused cases explain their prerequisites. |
| CAL-01 | Module Calibrate: orientation A/B, rows/columns, four start corners | Preview mapping follows the selected physical arrangement; test variants in simulator first. |
| CAL-02 | Save, reload, and reopen | Mapping persists for the intended module. Restore the actual physical mapping after variant tests. |
| CAL-03 | Flip wire order, then verify with sweep | Wire order reverses as described, and the physical first/last heads agree with the saved mapping. |
| CAL-04 | Calibration overview `/ops/calibrate/` | Nine-module overview and links match canopy configuration; this venue overview is distinct from calibrating the single bench kit. |
| HLD-01 | Hold dialog: name, Cancel, Start hold, Release hold | Cancel leaves state alone; Hold displays the correct name and prevents changes from this module session; release restores controls. |
| HLD-02 | Hold during sweep | Sweep pauses and remains understandable; no unexplained change while holding a photo look. |
| TEL-01 | Telemetry summary, Details, Copy anonymized JSON | Result, timing, readback mismatch, and error describe the latest command; copied export belongs to the current session. |

### 7. Ops, visualizer, kiosk, rooms, and roles

Exercise venue-oriented controls in simulator mode first. Passing these tests does not prove real venue hardware works; that requires the relevant deployment and devices.

| ID | Test | Expected result / evidence |
|---|---|---|
| OPS-01 | `/ops/`: status, nine modules, Needs attention, account summary | Cards load, target/mode is understandable, updates arrive, and attention items agree with underlying state. |
| OPS-02 | Pause/resume guest input; kiosk interaction | Pause state is visible; guest actions are blocked appropriately; resume restores them. |
| OPS-03 | Change each mode, Re-assert all, Safe look | The simulated canopy and reported state follow the requested operation; progress/error feedback is clear. |
| OPS-04 | Commission slots: cancel and confirm | Cancellation leaves the scene alone; confirmation reports the commissioning outcome and respects the documented slot rules. Simulator first: this can overwrite saved patterns. |
| OPS-05 | 7 am checklist; Run test mode change | Checklist identifies actual missing prerequisites and results update. |
| OPS-06 | Rate budget, logs expand/collapse, device refresh, alerts | Data updates; controls do not leave stale panels; a failure has a readable diagnostic trail. |
| OPS-07 | Navigation links and registry-overlay download | Links reach their intended pages; the overlay downloads successfully. Keep deployment identifiers in local diagnostic records. |
| VIS-01 | `/visualizer/`: Wire order, Index numbers, Desired/Reported/Both | Each toggle visibly changes the appropriate layer and stays synchronized with simulated canopy state. |
| KSK-01 | `/kiosk/`: every advertised mode tile | Correct mode is selected; active state, progress, and any cooldown agree with the engine. |
| KSK-02 | Repeated taps, paused input, temporary disconnection, recovery | Clear busy/cooldown/resting state; recovery restores current state without stale queued selections. |
| VIG-01 | `/vignette/entry/`, `/vignette/living/`, `/vignette/bedroom/`, `/vignette/garden/` | Each configured room loads the correct devices, controls, scenes, and title. Missing devices/features have clear explanations. |
| VIG-02 | Every control and scene offered in each room | Repeat once per actual device/action/scene; sheet opens/closes correctly, values persist as expected, and feedback belongs to that device. Test simulated lock/fan/plug actions in simulator. |
| ROL-01 | Create setup links for tester, kiosk, and vignette roles | Appropriate options are shown; link creation/copy works; assigned scope matches the form. |
| ROL-02 | Open each role in a separate browser profile | Correct landing page and permitted controls are available. Do not use an ops-authenticated browser to prove restricted-role behavior. |
| ROL-03 | Reuse/expire/revoke setup links and sessions | User sees a clear explanation; revoked access does not continue to authorize new actions. Simulator first. |

### 8. Repeat these checks across the app

| ID | Test | Expected result / evidence |
|---|---|---|
| UX-01 | Laptop vs phone, narrow portrait vs landscape | No inaccessible tabs, clipped dialogs, overlapping labels, or controls beyond the screen. |
| UX-02 | Keyboard Tab, Enter/Space, and dialog close/cancel | Focus is visible and usable; action can be completed or cancelled without a mouse. |
| UX-03 | Refresh, back/forward, reopen a tab | State reloads cleanly; old ownership and stale draft state do not silently send commands. |
| UX-04 | Open the same module on two clients; take over | Ownership conflict is explained; only the active session can change the module. |
| UX-05 | Stop/restart the server during a simulator action | UI reports loss of connection; after restart it reconnects/reloads or provides a clear recovery action. |
| UX-06 | Empty, invalid, boundary, and rapidly changed inputs | Validation names the problem; no invisible failure or false success; final result matches the intended value. |
| UX-07 | Copy/download/fullscreen on laptop and phone | The action succeeds or gives a clear browser-permission explanation; downloaded/copied results belong to this run. Record the browser and any fallback needed. |

### 9. Local baseline (added on this computer)

| ID | Test | Expected result / evidence |
|---|---|---|
| LOC-01 | Confirm the running folder and connection baseline | Record the actual running app folder and build, open Accounts and Show devices, and establish whether the supplied ZIP is the working installation. Do not treat a connection on the other computer as a local pass. |

## Local testing order

1. LOC-01, SET-01 and ACC-01: identify the app and intended kit.
2. MOD-01 and MOD-05 through MOD-08: session, power, brightness, white, colour.
3. BEN-01 through BEN-06: profile and capability gates, one at a time.
4. Paint, Tests, Slots, Effects, Calibration and Hold.
5. Simulator: Ops, visualizer, kiosk, rooms and access roles.
6. Phone, recovery and cross-page checks.

For each preset, slot, numeric boundary or device variant, use **Add variant** in the test center. A base-case pass does not establish that every variant passed. Use a separate session for simulator, real workshop and venue work.

## Failure triage

- Blocked: name the missing setup, lock, capability or hardware prerequisite.
- Functional: reproduce the incorrect behavior with the prerequisites satisfied.
- Usability: describe the label, feedback or workflow that caused confusion.
- Visual: record the screen size and clipping, overlap or visibility problem.

Record the screen result and physical result separately. Attach filenames for screenshots, videos or anonymized device JSON. Capture evidence before restarting, change one thing at a time, then repeat the same test and affected neighbors. Stop sweeps/jobs and use Done at closeout.

## Recovery provenance

Source: VWBD chat **Troubleshoot Hubspace control app**, workflow commit `54190de`, September 27, 2026. Retrieved from its completed file-change records on September 28, 2026.

The source reported 88 cases. The chat retrieval limit exposed 86 complete rows and the beginning of UX-07; the rest of that file was not available. Rows through UX-06 are recovered source text. The expected result for UX-07 and all material from section 9 onward were written locally. LOC-01 is a new local case, not a recovered original case. This local inventory has 88 cases; it is not an exact copy of the full original document.

The source's live account and allowlist observations describe the other computer only. All results here begin Not run.
