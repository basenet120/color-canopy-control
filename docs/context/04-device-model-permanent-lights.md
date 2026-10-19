# 04 — Device model: the canopy strand (Hampton Bay SQHDK03-15PSL)

Evidence and sources: `research/permanent-lights/` (start with `_synthesis.md`, then `api-model.md`). Everything marked **UNVERIFIED** must be settled by the bench gates at the bottom before the show depends on it.
Bench results from VWBD (Sep 28 – Oct 7 2026) are tagged **PHYSICAL** (seen on the lights by the observer), **CLOUD** (Afero HTTP/readback) or **SCREEN** (on-screen preview only), and come from Kingston (repo `bench-kit`, the commissioned kit) unless stated. String light 1 (`shop-string-1`, private overlay only) is not commissioned. Evidence: `docs/bench-capability-check/`, `docs/*-session.md`, `docs/vwbd-*-2026-10-02.json`; Paris's Oct 7 package (`evidence/`, `CHANGELOG.txt`) for the two-kit runs.

## Hardware
- A Home Depot–branded **Nanoleaf Permanent Outdoor Lights** kit (maker Nanogrid Ltd; Nanoleaf warranty doc, UPC prefix and Nanoleaf staff answering its Home Depot Q&A) running Hubspace firmware. Starter kit SQHDK03-15PSL (HD #330863181); expansion string SQHDE03-5PSL (10 pucks, 16.4 ft, HD #330863255, $29.97).
- **One kit = one Hubspace cloud device.** Inline controller: 2.4 GHz Wi-Fi + BLE, buttons power / brightness − / + / "M" next-scene, microphone for Party Mode, puck-count detection at power-up.
- 30 pucks on 3 strings × 16.4 ft × 10 pucks, **fixed 19.7″ (50 cm) spacing**. Up to 60 pucks / 6 strings per controller (still one device). Separate kits stay separate devices.
- Pucks: **hexagonal** with two screw ears (~35 × 32 mm body, ~2″ with ears — measure before VWBD cuts panel openings; the deck calls them octagonal). Each puck: 1 RGB LED + 3 warm-white + 3 cool-white LEDs; tunable white 2200–6500 K; CRI 80; 50 lm/puck in white. Saturated colors are much dimmer than whites; reviewers say orange, yellow and purple render weakly — matters for Rainbow Row Sunset and Golden Hour.
- Power supply 36 V DC / 1 A (36 W), polarized plug, **indoor/dry use only** per the HD manual. Manual forbids covered/recessed installs and enclosed spaces above 40 °C → the plenum above each panel needs ventilation.
- Cutting/splicing is physically possible (2 splicers included) but official guidance conflicts and cutting correlates with the "length detection failed / only the first 12 pucks light" failure → **loop the ~22 ft of slack per module above the panel; never cut.**
- 3-year warranty is **void in commercial use**; controllers and PSUs are not sold separately → buy spare kits. $99 Special Buy through Nov 3 2026 (regular $119).
- Reliability themes (4.1★, 114 reviews): controller/PSU failures, only first ~12 pucks lighting, single pucks stuck white, white flashes, schedule misfires, Wi-Fi dropouts. Reset: hold power ~10 s until three red flashes.

## Cloud profile (SQHDK03 dumped on Kingston — Gate A Sep 28, re-read Oct 2, CLOUD: `docs/bench-capability-check/gate-a-result.json`, `saved-profile-and-patterns.json`, `docs/vwbd-commissioning-transfer-2026-10-02.json`; attr numbers and numeric mode codes still come from sibling strings)

| attr | functionClass / instance | values |
|---|---|---|
| 1 | `power` | on / off |
| 2 | `brightness` | 1–100 (whole strand) |
| 3 | `color-temperature` | **2200–6500 step 100**; 2200 K and 6500 K at 50% seen (PHYSICAL, Sep 28, `docs/current-live-test-session.md`) |
| 4 | `color-rgb` | `{"color-rgb": {"r","g","b"}}` 0–255 (whole strand); steady red, green, blue at 50% seen (PHYSICAL, Sep 28) |
| 5 | `color-mode` | SQHDK03 lists circadian-rhythm, white, individual, color, sequence — **`individual` present**; no mixed, night-light or music-sync. Sibling string: white=0, color=1, sequence=2, individual=4; other devices: circadian-rhythm=10 — read the list from each device |
| 6 | `color-sequence` / `preset` | 33 firmware presets + `custom`, e.g. christmas-static, christmas-effect, snowfall, mistletoe, prism, cross-faded, effervescent, fade-3/7, jump-3/7, rainbow-road, rainbow-roll, gradient-rainbow, home-depot (full list in the dump); bench behaviour in gate 7 |
| 11 | `speed` / `color-sequence` | −10…+10 step 1 (−10 slowest; −8 is already very slow on Rainbow Road, PHYSICAL); **resets after power-off** (sibling; unchecked on SQHDK03) |
| 12 | `color-individual` / `custom` | selects a slot: `custom-1` → 351, `custom-2` → 352, `custom-3` → 353; value hints carry geometry — SQHDK03: `"60-segment"` on all three slots, no `"no-white"` (siblings: `"20-segment"`, `"no-white"`) |
| 31 | `toggle` / `party-mode` | on/off — keep off |
| 100 | `restore-values` | restored / not-restored / partial-restore |
| 300 | `color-sequence` / `custom` | named on-device programs. SQHDK03 lists only sleep, wake-up, sound-to-sleep and six holiday fades (diwali-, cinco-de-mayo-, st-patricks-day-, halloween-, easter-, hanukkah-fade) — **no nightlight, dinner-party, chill, focus or clarity** (sibling devices had those; sleep there = 40→5% over 30 min). None run on the bench yet |
| 351–353 | `color-sequence-v2` / `custom-1..3` | **one object per saved pattern holding every puck's color + on-device animation parameters**; SQHDK03: 3 slots × 60 entries |

Also on SQHDK03 (CLOUD): `connected-light-count` 0–60 (Kingston reads 30), `warm-dimming`, `power-off-timer`, vacation-mode, schedule-pause — 26 functions in all.

### The per-puck pattern object (`color-sequence-v2`)
```json
{"color-sequence-v2": {
  "id": 0, "version": 1, "headerFlags": 128, "sequenceFlags": 0,
  "brightnessDepth": 100, "brightnessEffect": 0, "brightnessSpeed": 50,
  "motionEffect": 0, "motionSpeed": 50,
  "frameBuffer": {"flags": 0, "framebuffer": [
    {"r": 255, "g": 110, "b": 15, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0}
  ]}}}
```
- Kingston's three slots read back with exactly this header (CLOUD, Sep 28).
- `framebuffer` index = position along the wire. **Kingston: entries 0–29 drive heads 1–30 counted from the controller end; entries 30–59 drive nothing** (PHYSICAL, Sep 28: single-entry probes at 0, 1 and 29, a 30–59 block, a head-15 marker, a red→purple→blue 30-head gradient — `docs/bench-capability-check/address-probe-*.json`, `module-marker.json`, `module-gradient.json`). Intermediate heads weren't swept one by one.
- **Length = segment hint, not puck count** (20 on the Novolink string, 40 on the RGBIC tape): SQHDK03 reports 60 entries per slot with 30 pucks attached (CLOUD, Kingston; String light 1 also 60, package `evidence/shop-safe-gate-c-2026-10-06.json`); other puck counts are untested (gate 10). Kingston's Gate A slots (solid red/green/blue, written in one burst with the setup values on Sep 27) fill all 60 (CLOUD); no hand-painted app pattern has been dumped yet, so whether one fills entries 30–59 is unknown (matters for T2). Per-head frames from the module page, the Studio and the show engine reach the pucks only through that kit's own `framebuffer_mapping` in the ops overlay, with unmapped entries written OFF (D54, D57; effect start/restart on the module page keeps them as read); bench jobs address raw controller entries and need only the kit's T1 flag.
- Per-entry `cct` / `whiteBrightness` should drive the white LEDs on this RGBW product (SQHDK03 has no `no-white` hint; the sibling dumps with `no-white` were RGB-only) — UNVERIFIED, not bench-tested. Kingston's Gate A slots carry cct 0 / whiteBrightness 0 (CLOUD).
- `motionEffect` / `brightnessEffect` codes are mostly unknown (0 = static; 6 observed elsewhere). **`motionEffect` 1 at `motionSpeed` 30 moved separated red/green/blue blocks to different heads** — a bounded pass only (PHYSICAL, Kingston, Oct 2: 20 s at 30% brightness, `docs/vwbd-effect-probe-2026-10-02.json`); on a contiguous rainbow (Sep 28) the same code looked still or slightly flashing (`docs/custom-pattern-session.md`). Vendor name, direction, period and phase unknown; `effects.enabled` stays empty. Effect header fields round-trip in readback (CLOUD), which doesn't prove the device applied them. Keep codes in config, never hard-code; enumerate with the bounded `effect_probe` (D55).

## Write shapes
**Full frame + select + mode + power in ONE PUT per module** (verified offline by running aioafero 9.3.0's own code with the network stubbed; ~3.4 KB for 30 entries, ~6 KB at SQHDK03's 60 — estimated by serialising Kingston's readback. Live: Gate C's frame + select + mode + power PUT changed Kingston's lights, PHYSICAL, Sep 28):
```
PUT https://api2.afero.net/v1/accounts/{accountId}/metadevices/{metadeviceId}/state
Host: semantics2.afero.net      Content-Type: application/json; charset=utf-8
{"metadeviceId": "...", "values": [
  {"functionClass": "color-sequence-v2", "functionInstance": "custom-1", "value": {"color-sequence-v2": {…header…, "frameBuffer": {…}}}, "lastUpdateTime": <epoch ms>},
  {"functionClass": "color-individual", "functionInstance": "custom", "value": "custom-1", "lastUpdateTime": <ms>},
  {"functionClass": "color-mode", "functionInstance": null, "value": "individual", "lastUpdateTime": <ms>},
  {"functionClass": "power", "functionInstance": null, "value": "on", "lastUpdateTime": <ms>}]}
```
**Slot select only** (show-time, < 1 KB): the `color-individual` + `color-mode` rows (+ `power`).
**Whole-strand looks** use the same explicit-rows path. aioafero's `set_state` / `set_rgb` / `set_effect` show the wire encodings (color-temperature as a string, `{"color-rgb": {r,g,b}}`, programs as preset `custom` + color-sequence/custom `<name>`) but return nothing and silently adjust or drop values — see `07` §4.

### Gotchas (each one is a real bug someone hit)
- aioafero 9.3.0 has **no API for frames or slot select** (PR #61 is stale — assume it never lands). Build the rows by hand and send them through `bridge.create_request("put", …, True, …)` per `07` §4 (D51). `bridge.lights.update(id, states=[...])` also emits the right PUT, but its return value is ambiguous (`None` = HTTP 400 *or* exhausted retries *or* unknown device; other errors come back as response objects or exceptions).
- `lastUpdateTime` must be **epoch milliseconds** — seconds return HTTP 200 and are silently dropped (aioafero #65). `update()` stamps it for you; the `bridge.create_request` path doesn't.
- For re-asserts send the **full state vector** (defined in `07` §5) — `set_state`/`set_effect` skip fields that match aioafero's cache.
- Copy the header (`id`, `version`, `headerFlags`, `sequenceFlags`, `frameBuffer.flags`) and the framebuffer length **from the device's own readback**; change only colors and bench-verified effect fields.
- HTTP 2xx ≠ lights changed, and REST readback only proves the cloud stored the value (device-level application is UNVERIFIED — `07` §5). aioafero's Conclave path stores attr-351 pushes raw, so never trust the push cache for frames. Seen in the field: readback matched while the observer saw no change or a different look (Kingston's rainbow-mode dim to 20%; String light 1's whole-strand blue, and its custom frame seen as "whitish pinkish"), and once the reverse (Jump 3 at +5 jumped visibly while readback mismatched on color-mode) — `docs/current-live-test-session.md`, `docs/preset-functionality-session.md`, package `evidence/shop-*-2026-10-06.json`.
- A single PUT restoring a frame, the slot select and a non-custom look returned 200, but readback still differed on `color-individual` / `color-mode`: the test's custom selection and individual mode stayed instead of the requested originals (CLOUD, String light 1, run 20261007T003050-gate_c_safe-f613f6, package `evidence/shop-safe-gate-c-2026-10-06.json`). Restore the frame, the selection and the look as separate writes (D55).
- aioafero's default session is `limit_per_host=3` → 9 module PUTs go out in 3 waves. Pass your own session (settings in `07` §4).
- aioafero does **not** split addressable strings into per-zone clones (only a `…-light-party-mode` switch clone); writes go to the parent metadevice id.
- A raw Afero attribute write exists (`POST /v1/accounts/{aid}/devices/{deviceId}/actions {"type":"attribute_write","attrId":351,"data":"<hex LE>"}`) — document it, don't build it; use only if the semantic PUT is rejected and only after a bench proof.

## Canopy control model
- 9 metadevices on one dedicated canopy account (the vignettes are outside this system, D67). No group/scene/sync call exists, so **every mode change compiles to exactly one PUT per module**: a burst of 9 concurrent PUTs over a warm session with a fresh token, then readback, then re-assert any module that differs (≤ 27 requests per change). A stepped look (below) is several updates, each one PUT per module, never one update split (`07` §6).
- **Store and select**: the three painted looks (Rainbow Row Sunset, Garden Gathering, Golden Hour — static 2-D designs since D63) are written into slots custom-1..3 at commissioning; at show time the kiosk only selects slots. The reconciler hashes slot contents via REST and rewrites (or alerts) on drift — this also undoes any staff edits made in the app.
- On-device animation runs on each controller with no ongoing cloud traffic — but on its **own free-running clock**. Nothing phase-syncs the 9 controllers. No show mode uses it (`effects.enabled` is empty). The motion the show has is the reverse: **stepped looks** (D65), where the Conductor holds the phase and sends every step through the cloud.
- **Fallback ladder per mode** (chosen by config/capabilities, not code): **T1** frames written by API + select (proven on Kingston only: Gate C plus a 30-head painted gradient, PHYSICAL; the Studio's paired per-head gradient has been seen only on screen, SCREEN, and never sent to hardware — preflight blocks it until String light 1 is commissioned, D56) → **T2** slots hand-painted in the Hubspace app (≈15–20 min/module) and selected by API (formal Gate B not run) → **stepped** module-level colours (and, for a breath, levels) re-sent a step at a time (D65, D75: travel, breathe or shimmer; any scene may carry one stepped rung, just before modules; simulator only until Gate R) → **T3** whole-strand CCT/RGB/preset, including module-level composition (each module = one coarse pixel). Kiosk copy follows the tier actually in use.

The six modes are the six approved scenes (`config/scenes.yaml`, D59). Each mode renders its scene's palette at every
tier, at the scene's brightness. Since D63 the palette is laid out as **one 2-D design over the canopy** (a `field` in
canopy inches, reflected-ceiling view; `02` "Which way is left"), named once in `config/modes.yaml` with a YAML anchor
so every tier that draws it uses the same design. Three controller slots hold three painted looks, so three modes have
a canvas; the other three start at module level. Two scenes move today (D65); since D75 any scene may (PR D adds the
options). (The original brief's table — white CCT, a holiday preset, a warm pathway, an amber band with motion — is
in `02` and git history.)

What each tier does with the design:
- **canvas (T1):** the design at each of the 270 heads, written into the mode's slot at commissioning and only
  selected at show time. Copy: "Every one of the 270 lights set individually, saved as a Hubspace custom pattern on
  each of nine strands."
- **slot (T2):** select the slot, hand-painted in the Hubspace app (the reconciler verifies its hash and never
  rewrites it). Copy: "A custom pattern painted light by light in the Hubspace app, saved on each strand." A hand
  painting matches the 2-D design only as well as the painter follows it.
- **stepped (D65, D75):** the module-level design re-sent a step at a time, each module sent its next RGB colour and
  level (one full-state PUT per module whose rows change; never a slot object). `mode: travel` (D65) shifts the
  design along its layout by one sixth of a repeat a step; `breathe` and `shimmer` (D75) keep it in place and breathe
  each module's colour and/or brightness (below). Step 0 is the modules rung's still look exactly (the schema and the
  compiler check it). After a bounded run the look settles on the mode's best still tier (below). The moving copy
  ("… through the Hubspace cloud … sent its next colour every few seconds, so … move diagonally / out from one edge
  …", or for a breath "… sent a brighter or softer level every few seconds, so the palette swells and softens …")
  shows only while a run moves; otherwise the still sentence shows.
- **modules (T3):** each module set to the design at its centre ((col + ½)·75, (row + ½)·40 in) as one RGB colour:
  4–9 module colours per scene. The copy names the layout ("from the centre out", "diagonally", "from one edge",
  "from one spot"). On a partial canopy (D62) it says instead "two Hubspace strands, each set to a single colour from
  the scene", which claims no canopy-wide layout.
- **strand:** the scene's solid colour on every strand ("all nine Hubspace strands set to …", counted on a partial
  canopy). The safe look is Lowcountry Calm at this tier.

| Scene | Design (D63) | Ladder | Brightness | Moves? | On live hardware now |
|---|---|---|---|---|---|
| Lowcountry Calm (idle, safe look) | radial from the canopy's centre: sea glass in the middle, soft ivory at the corners | modules → strand | 70% | no rung today (D75 allows one: start-up and an idle return show it still unless `motion.idle` is on; a guest's choice runs it) | modules |
| Rainbow Row Sunset (custom-1) | linear at 45°, corner to corner: pastel coral → blush pink → lilac | canvas → slot → modules → strand | 85% | no | modules |
| Garden Gathering (custom-2) | radial from the middle of one long edge (112.5, 120): ivory there, sage green at the far corners | canvas → slot → modules → strand | 75% | no | modules |
| Coastal Tides | a tilted shoreline, linear at 78°: ocean blue → aqua → seafoam → foam white | **stepped** → modules → strand | 85% | stepped: the tide rolls diagonally (forward: ocean blue moves on toward the foam-white side) | modules (stepped refused: live off) |
| Golden Hour (custom-3) | a radial sun at the middle of the left edge (0, 60), in the canopy's proportions: soft gold → peach → sunset pink → rose → lavender | canvas → slot → **stepped** → modules → strand | 95% | a guest's choice: stepped first (sun rays move out from the left edge), then the per-head sun (T1/T2) where it can run, else module level (D75); an /ops choice: T1/T2 at once, stepped only where they can't run | modules (canvas, slot and stepped all refused) |
| The Goodnight Glow | a radial moon over r0c2 (187.5, 20): moonlight blue there, periwinkle at the far corner | modules → strand | 28% | never | modules |

"On live hardware now": every scene runs at module level (or strand below it), because no upper rung is open yet.
On the Mac show box as D62 recorded it (two kits registered, at r0c0 and r0c1) every look drives those two and
leaves the other seven out. What each upper rung needs before a real runtime uses it:
- **canvas (T1):** Gate C (`t1_frames`) on every module; on a 60-entry kit, its own proven `framebuffer_mapping`
  (D54, D57); and a commissioned slot record (origin `api`) for the mode's slot on every registry module. No canopy
  kit has a recorded map, and no live runtime holds T1 records on a canopy module.
- **slot (T2):** Gate B (`t2_select`) on every module and a hash recorded as hand-painted in the Hubspace app on
  every module. Formal Gate B has not been run on any kit.
- **stepped:** `motion.stepped.live_enabled: true`, which only Alon sets, after a Gate R pass on the show kits.
  Until then the show's `tier_allowed` refuses the stepped rung on any non-simulated driver and the ladder lands on
  the next rung (`tests/test_d65_modes.py` proves it through the show's own wiring on a live-named driver). The one
  exception is the operator-started stepped rehearsal, which opens the rung for its one mode while it runs.
- A hysteresis downgrade never lands on a stepped rung: from Golden Hour's T1 or T2 it passes over stepped to the
  still modules rung, and an upgrade lifts back past it, so easing off never starts a run.

In the simulator every tier is allowed (07 §6 "sim runs T1"), so a guest's Golden Hour steps and then shows its
per-head sun (an /ops choice shows the sun at once), and Coastal Tides steps whenever it is chosen.

### Stepped looks (D65): cadence, budget, runs
Code: `conductor/sequence.py` (the runner), `Tier.STEPPED` in `conductor/looks/compile.py`; settings
`motion.stepped` in `config/conductor.yaml`.
- **Cadence.** Each scene's design pace is `step_s: 6` with `steps_per_cycle: 6`. The runner uses the longest of:
  the design's step, `min_step_s` (4 s; a step's verification takes about 2.6 s, D65), the busiest
  account's modules × 60 / the planned rate, and (with the ripple on) the ripple's span + 4 s. The planned rate is
  `account_writes_per_min` (90, the cap on writes of any kind in any 60 s on one account) less `budget_reserve`
  (20%) = 72 a minute. So nine modules on one account step every 7.5 s, and the Mac box's two kits every 6 s. A step
  longer than `max_step_s` (12 s) isn't run: the still look holds and an alert says why.
- **The cap.** A step that would take the account past 90 writes in any 60 s (every write on the account, by the
  executor's own count) is held back: nothing is sent and the next step tries again. Since D75 a step also keeps a
  guest's burst of room (one write per module): it waits when the writes still in its window plus its own plus that
  burst would pass 90, so a mood chosen mid-run (which now runs too) never starts at full pace on top of the old
  run's steps. What the runner can't hold back (a guest's burst, 429 retries, re-asserts) lands in the rest of the
  20% reserve.
- **Bounded runs.** A run lasts `run_s` (90 s, at most `kiosk.idle_return_s`) from when the look was chosen; then
  the look settles on the mode's best still tier (D75): its canvas or slot where that can run (one write per module
  that changes, source `settle`), else the still design is sent to the modules that differ and holds. After a
  governor trip or two held steps the module-level hold goes out now and the settle waits (the cool-off, or one cap
  window); D72's fast back-off doesn't hold it. A choice runs once: a re-pick of the same look (tier change, new
  capabilities, a publish) keeps a running run's phase and never starts a new one, and a choice that couldn't run
  when it was installed (a cool-off) counts as run, so a re-pick after the cool-off lifts holds still. Which choices
  run first: a guest's (kiosk) runs the rung first; an /ops choice keeps the ladder order; start-up, the idle return,
  the safe look and the checklist's change back show a still tier. The idle return never moves for a run.
- **Breathe and shimmer (D75).** Each module's colour moves up to `shift` (0–0.5) along the design's own t (clamped,
  never wrapped) and its brightness up to `brightness_swing` (0–20) points either side of the rung's, one breath of
  `steps_per_cycle` steps per module (up, back, down, back: `compile.breath_wave`). `lag_steps` lets the design's
  start lead (breathe) or seeds each module's own start (shimmer, `seed`). Lags count over the driven strands, so a
  partial canopy's kits never rest together under moving copy (a breath that would send nothing for a step holds
  still instead), and the cycle is a breath plus the largest lag. A guest's brightness during a breath stays relative:
  the breath swings around it, never below `kiosk.guest_brightness_min`. The copy says what changes: "swells and
  softens" / "glow and soften" with a swing, "drifts and settles" without one, never "next colour" with no shift.
- **Held steps (D75).** A step held back by the cap is drawn as the step last sent (the panel's frames and levels
  freeze there), and two held steps in a row end the run on the still look and copy (`HELD_STEPS_END`).
- **The idle breath (`motion.idle`, off).** With `{breathe_run_s, every_s}` set, the idle look (if it has a rung)
  breathes once for `breathe_run_s` every `every_s` while it holds with nothing else in force, and an idle return
  lands as one breath. It never moves the idle clocks or the guest settings, yields at once to any guest input, and
  runs only where `tier_allowed` lets the stepped rung run. Shipped `null`; Alon turns it on only after a 30-minute
  live soak.
- **Held, not queued.** During an outage, commissioning, a guest-input pause or while a guest has the canopy off,
  the step index advances but nothing is sent; a step that comes due late is skipped. A guest brightness change
  close to the next step goes out with that step (`fold_guest_within_s`).
- **Throttle governor.** Per account, over 60 s: at ≥ 8 write attempts answered 429/403 that are ≥ 25% of the
  run's attempts, the run stops, the still look goes out, and stepped looks on that account hold still for 5 min
  (`governor_cooloff_s`). It never rotates accounts or IPs.
- **Re-asserts.** The Conductor holds the phase, so re-sending one module the current step is correct; 07 §6's
  all-or-none re-trigger is for on-device motion only.
- **Surfaces.** The guest panel draws exactly the frames the engine sends (`state.lit.motion` `{kind: "steps"}`; it
  switches, it doesn't blend); `/ops` shows a stepped card (step n of N, the step length, writes a minute on the
  account against the cap).
- **Verified in the simulator only** (D65): the main soak steps Coastal Tides whenever it is chosen and still passes
  every pinned row; the stepped scenario ran 124 steps over 12 runs at 7.5 s with at most 72 program PUTs and 89
  writes of any kind in any 60 s on the account. Each step's landing spread was p95 2.78 s against 05's 2 s: reported,
  not gating, until Alon signs off a per-step target. Since D75 the stepped scenario steps all six scenes with all
  three kinds (153 steps, one PUT per changed module per step, at most 72 program PUTs a minute). Since the review's
  cap fix (B-R1-1: a step leaves room for a guest's burst) its all-writes maximum is 82 in any 60 s on seed 0 (80–89
  over seeds 0–15), under the proposed 90 (D75).
- **Needs real hardware:** Gate R (below), whether a strand cuts or fades between colours (bench item 6), and the
  start skew across kits (gate 12).

### The ripple arrival (D64)
A mood chosen by a guest, by ops or by the idle hold can land in up to three rings, ordered by its design over the
driven modules (from the centre out, from one edge, corner to corner). Each module still gets exactly one full-state
PUT for the change, so the ripple costs no writes. Re-asserts, recovery, tier changes, the safe look, guest
brightness/power and start-up stay one burst. It is **off** (`kiosk.ripple_stagger_s: 0`, one burst) on every runtime,
simulated or live; there is no separate live gate, so setting it turns it on everywhere. It is off because 05's
"first to last module within 2 s at p95" fails by design under a ripple and `config/sim/acceptance.yaml` is pinned.
The soak runs a separate SIMULATED ripple scenario at 1.2 s: settled p95 5.77 s against (3 − 1) × 1.2 + 5 = 7.4 s,
one PUT per module per change; each ring's own spread p95 2.29 s (reported, not gating). To turn it on, Alon sets
1.2 s and signs off the per-ring spread target. Needs real hardware: start skew well under the stagger (gate 12), and
cut vs fade (item 6).

*No mode uses on-device motion (the soak still enables a SIMULATED code on Golden Hour to measure the all-or-none
re-trigger). The rest of this paragraph holds for any later on-device motion look:* such a look only reads as one wash if on-device motion runs the same direction in every module: **ask VWBD to route every module's cable column-wise, starting at the module's canvas-left edge (B modules included), so the index rises with canvas X.** Software can't reverse on-device motion. Only code 1 has been seen moving (bounded, direction unknown) and `effects.enabled` is empty, so the custom-slot motion isn't available yet (gate 9).

## Bench gates — one kit, now (don't wait for Prototype 01)
Status after VWBD (Kingston unless stated) is in bold at the end of each gate.
1. **Gate A — read-only profile dump**: `fetch_metadevice` with expansions; commit the anonymized JSON as the SimDriver fixture. Record segment hint + framebuffer length, slot count, color-mode values (is `individual` present?), CCT range, preset + program lists, speed range, restore-values, party-mode. **Done (CLOUD, Sep 28, re-read Oct 2): results in the profile table; anonymized dump in `docs/bench-capability-check/saved-profile-and-patterns.json`.**
2. App-paint an index-coded pattern (puck 1 red, 2 green, 3 blue, 4 white) into slot 1 → read back → confirm order and which end is index 0. Tabulate effect fields while switching effects/speeds in the app. **Index order done by API address probes instead (PHYSICAL, Sep 28 — see the framebuffer bullet). Effect-field tabulation not done.**
3. **Gate B — slot select** by API (color-individual + color-mode): visible switch ≤ 2 s, readback matches → proves T2. Then switch the kit off at the wall, PUT another select and GET: if the GET shows the new value, readback can't detect device-level misses. **Formal Gate B not run on any kit: no T2 or device-level-readback proof. Gate C's BEFORE select on Kingston (custom-3 → custom-1, Sep 28) was confirmed by readback only (CLOUD, `docs/bench-capability-check/gate-c-result.json`). Both slots held the same red frame, so the switch couldn't be seen; no slot select has been seen on the lights on any kit.**
4. **Gate C — replay write**: PUT the exact object read from slot 1 into slot 2 + select → HTTP 200, readback unchanged, lights match → proves T1. **Passed on Kingston (PHYSICAL, Sep 28): custom-2's green frame replayed into custom-3 + select, lights went red → green; HTTP 200 and hash match (CLOUD) — `docs/bench-capability-check/gate-c-result.json`. T1 holds for Kingston only. String light 1 unresolved: its latest `gate_c_safe` (run 20261007T010607-gate_c_safe-30ed44) has a CLOUD match and exact restore but no physical answer; an earlier run (…9603dd) got "No — whitish pinkish". No T1 flag, no map. Its whole-strand RGB was seen to respond (PHYSICAL, Oct 6, 90 s red/blue paired hold, package `evidence/pair-identification-2026-10-06.json`), which proves nothing about frames.**
5. Synthetic hue ramp (hue = 12°·i) → photograph for the index map; film all 30 changing in one frame; 20 trials of PUT-to-light latency. **Not done.**
6. Edge cases: 29/31/60-entry frames; per-puck white (cct 2700, whiteBrightness 100, colorBrightness 0); rewrite the active slot without reselecting; cut vs fade; order of rows within one PUT; a combined PUT starting from power-off; a PUT with lastUpdateTime = now − 10 min (does a lagging clock get writes dropped?). **Not done, except that 60 entries is the native length every bench write used.**
7. Whole-strand + presets via API: CCT floor/ceiling, 1–10% dimming smoothness, HD orange, presets at speed −10/0/+10, programs. **Partly done (PHYSICAL, Sep 28): CCT floor/ceiling seen at 50% only; dimming 50→20% seen in steady white (a rainbow-mode dim to 20% read back but wasn't seen); presets at −8/−3/0/+5 — Rainbow Road travels between pucks (very slow at −8), Rainbow Roll at −8 flashes in place, Gradient Rainbow is static matching adjacent pairs, Fade 3 fades through black, Jump 3 jumps sharply at +5 (at −3 only red showed in 20 s) — `docs/preset-functionality-session.md`. Not done: 1–10% smoothness, HD orange (a `home-depot` preset is listed, look unobserved), speed −10/+10, programs.**
8. On-device proof: start an animation, cut the WAN for 10–30 min — it must keep running. **Not done.**
9. Motion enumeration: sweep motionEffect/brightnessEffect codes; find a directional scroll, its slowest period, whether re-select restarts phase. **Code 1 only, bounded (see the pattern-object bullets); no sweep, no direction, period or phase answer.**
10. Power: pull mains in each mode × restore setting. Boot with strings 2–3 unplugged and dump the hints and all three framebuffer lengths — whether the cloud profile reflects the detected puck count at all is UNVERIFIED (the segment hint reads 60 with 30 pucks attached, so it isn't the puck count; `connected-light-count` reads 30 on Kingston, CLOUD, but hasn't been seen to change); if it doesn't, the 12-puck fault can only be caught visually. **Not done (`head_count_boot` not run).**
11. Single-device rate: 50 frame writes at 5 s / 2 s / 1 s spacing; compare readback to sent to catch silent drops; stop at first 429/403. **Not done.**
12. With 2–3 kits: start skew (9-way-style parallel PUTs, 120 fps video, 20 trials) and drift at 10/30/60 min; chaining test (60 pucks on one controller: 60 independent entries or 30 repeated?). **Unresolved: Kingston + String light 1 started Rainbow Road in one paired burst (Oct 2); both were seen running (PHYSICAL), but corresponding-head alignment was "hard to tell from their positions". Host send times 0 ms apart at clock resolution is host-side only, not hardware phase (package `evidence/paired-rainbow-test-2026-10-02.json`, `CHANGELOG.txt`). No video, trials, drift or chaining test.**
13. On Prototype 01 with Mylar: color recipes (RGB-only vs RGB + warm-white blend) for amber, HD orange, peach, ivory at show brightness; 4 h thermal run; calibration in A and B orientation. **Not done.**
14. **Gate R — the stepped rehearsal** (D65, `docs/runbooks/stepped-rehearsal.md`): an operator-started, bounded stepped run from `/ops` (never cron, CI or a restart), first on the two VWBD kits on the bench account, then on the nine show kits on the canopy account during install week. It passes only if the run went its full length, nothing failed throttled, throttled attempts stayed under 5%, there was no 403, and the account saw at most 90 writes in any 60 s. The observer also records cut vs fade (item 6), whether the flow reads, and the start skew. A `"simulated": true` report never counts. **No result recorded in these docs yet (Oct 14). Stepped looks stay off live until a pass on the show kits and Alon sets `motion.stepped.live_enabled`.**
