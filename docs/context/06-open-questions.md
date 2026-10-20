# 06 — Open questions

Ranked by how much they change the build. Every item has an owner and a **default** — agents proceed on the default and never block on an answer. When an answer lands, update this file and the affected config.

Answers from real hardware name the kit, date, source and evidence type, kept separate: **PHYSICAL** (seen on the lights at VWBD), **CLOUD** (Afero HTTP/readback), **SCREEN** (preview only). Kingston = `bench-kit` (commissioned); String light 1 = `shop-string-1` (VWBD's private overlay only, not commissioned). "package" = Paris's 2026-10-07 integration package (`evidence/`, outside the repo).

## Settled by the bench kit (owner: Alon)
1. **What cloud profile does SQHDK03 expose?** Segment count (30 or a fixed 60), slot count, `individual` color mode, per-puck white, CCT range, presets, programs, restore-values.
   *Answered on Kingston — CLOUD, Gate A dump, Sep 28, repeated Oct 2:* a 60-entry frame in each of 3 slots (segment hint 60), `individual` present, CCT 2200–6500 K step 100, RGB, 33 presets, 9 programs, speed −10…10, restore-values, party-mode, 26 functions; model field `TBD` (`docs/bench-capability-check/gate-a-result.json`, `docs/bench-capability-check/saved-profile-and-patterns.json`, `docs/vwbd-commissioning-transfer-2026-10-02.json`). **Per-puck white still open:** real frames carry per-entry `cct`/`whiteBrightness`, but no per-puck white has been sent.
   *Default:* the sibling model in `04`, seeded from `conductor/sim/profiles/hampton-bay-string-20seg.json` resized to 30; frame length = the device's framebuffer length; `expected_heads` = 30 from the registry. Rebuilding the simulator default from the real dump is **not done yet** (`sqhdk03-sim` is still the resized sibling, 30 entries — D54). Live, per-head frames (module page, Studio, show engine) on a 60-entry kit go only through its own `framebuffer_mapping` (D54, D57); bench jobs write raw controller entries.
2. **Does Afero accept third-party writes** of (a) slot select and (b) full `color-sequence-v2` frames via the /v1 semantic PUT?
   *Partly answered.* (a) VWBD reported the Slots-tab select working on its first real kit (Sep 27 STATUS, commit `b892b0e`). That report has no run record and names no kit, so it isn't counted as PHYSICAL, and no recorded run shows a slot select on the lights: Gate C's BEFORE select on Kingston (custom-3 → custom-1, both holding the same red frame) was confirmed by readback only (CLOUD, Sep 28, `docs/bench-capability-check/gate-c-result.json`); formal Gate B never run, `t2_select` unset on both kits. (b) **Yes on Kingston** — PHYSICAL, Sep 28: Gate C copied Custom 2's green frame into Custom 3, HTTP 200, hash match, the observer saw red → green; `t1_frames` set for that kit only (`docs/bench-capability-check/gate-c-result.json`; red → green per `docs/custom-pattern-session.md`; carried over at VWBD on Oct 2 after a device-digest match, `docs/vwbd-commissioning-transfer-2026-10-02.json`). **Unresolved on String light 1:** Afero accepts and reads back its frames (CLOUD), but run `9603dd` was answered "No — whitish pinkish" and the latest, `20261007T010607-gate_c_safe-30ed44` (Oct 6–7), has a cloud match and exact restore with no physical answer; no flag, no map (package `evidence/shop-safe-gate-c-*.json`).
   *Default:* build the full T1 → T2 → T3 ladder; sim runs T1, live runs T3 until the Gate B/C flags are set. Kingston has its flag; the show engine uses T1 on a 60-entry kit only through that kit's own `framebuffer_mapping` (D54, D57), and no canopy kit has a recorded map yet.
3. **Does readback reflect the device or only the cloud?** (Gate B's kit-unplugged test.)
   *Still open:* not run; no device-level readback proof on either kit (`docs/vwbd-commissioning-transfer-2026-10-02.json`). Readback has disagreed with the lights — Kingston, Sep 28: a 20 % dim on a rainbow preset read back 20 % with no visible change; Jump 3 at +5 read back white 2200 K while the observer saw RGB jumps (`docs/current-live-test-session.md`, `docs/preset-functionality-session.md`).
   *Default:* assume cloud-only; the SimDriver models both behind a flag.
4. **What do the `motionEffect` / `brightnessEffect` codes do?** Is there a slow directional scroll? Does re-selecting restart the phase?
   *Partly answered (Kingston only):* `motionEffect` 1 at speed 30 moved separated R/G/B blocks to different heads — PHYSICAL, bounded: one 20 s run at 30 % brightness, Oct 2 (run `20261002T161338-effect_probe-ed22fc`, `docs/vwbd-effect-probe-2026-10-02.json`); effect header read back as sent (CLOUD). Sep 28 runs of code 1 on a rainbow looked still or slightly flashing, headers unverified (`docs/custom-pattern-session.md`). Unknown: vendor name, direction, period, speed scale, whether re-selecting restarts the phase; no other motion code and no `brightnessEffect` tested. Built-in presets do move on-device: Rainbow Road travels between heads (speed 0; −8 very slow) — PHYSICAL, Kingston, Sep 28 (`docs/preset-functionality-session.md`).
   *Default:* every code disabled in config until benched — unchanged: `effects.enabled` stays empty; one bounded run doesn't enable code 1 (D55).
5. **Start skew and drift across controllers** for one 9-PUT burst.
   *Partly answered (2 kits, not 9):* Oct 2, Kingston + String light 1 were sent Rainbow Road in one paired burst (speed 0, 30 %): both showed a rainbow — PHYSICAL ("BOTH RAINBOW"); head-to-head phase unconfirmed ("hard to tell from their positions", package `VALIDATION-STATUS.json`, `CHANGELOG.txt`). Host send skew 0 ms at clock resolution, acks ~200 ms, readback 1.3–2.4 s — CLOUD/host timing, not hardware phase. A paired Fade 3 has cloud readback only; drift never measured (package `evidence/paired-*.json`).
   *Default:* ≤ 1.5 s start skew, free drift; a look with on-device motion re-triggers all nine on every selection (no show mode has one since D59). Stepped looks (D65) don't depend on controller phase: the Conductor sends each step, so skew shows as each step's landing spread (#27).
6. **Real Afero limits** for 9-PUT bursts plus a stepped sweep on one account, behind the venue's one IP (the Conductor holds only the canopy and bench accounts, D67). Per account or per IP? 429s or silent drops?
   *Default:* ≤ 1 write/s per device, bursts of 9 per change, hysteresis downgrade (`07` §6). Stepped looks: #27 (cadence and cap) and #28 (per account or per IP). Note: numbers measured from Railway reflect a datacenter IP — re-measure from the show box (Gate R does, `04` gate 14).
7. **Per-puck white and color recipes** through clear/frosted Mylar at show brightness.
   *Default:* recipes in config; start with RGB + warm-white blends for ivory and amber; tune on Prototype 01.
8. **Power-loss behavior**: do slots, mode and speed survive? Is restore-values exposed? Does the profile change when the 12-puck fault happens?
   *Partly answered:* restore-values is exposed — `not-restored` / `restored` / `partial-restore`, reading `partial-restore` on Kingston (CLOUD, Gate A dump, Sep 28, `docs/bench-capability-check/saved-profile-and-patterns.json`). What each value does, whether slots/mode/speed survive a power cut, and the 12-puck fault: untested. The same dump reports `connected-light-count` 30 (range 0–60); whether it changes on the fault is untested.
   *Default:* re-send the full state vector whenever a module comes back online; always re-send speed and party-mode off; the 12-puck fault is a visual check until proven otherwise.
9. **Chaining**: does a 60-puck chain give 60 independent entries? *Default:* one kit per module.
   *Still open:* only 30-puck kits tested. The 60-entry frame (and `connected-light-count`'s 0–60 range) is what the controller reports, not proof that 60 pucks work; with 30 attached, entries 30–59 drive nothing (PHYSICAL, Kingston, Sep 28, `docs/custom-pattern-session.md`).
10. **Does the Keycloak refresh-token rotation invalidate older tokens?** *Default:* assume yes — persist every rotation (`07` §3).

## Decisions Alon owes (with dates)
11. **Closed — out of scope (D67, Oct 17):** the Conductor controls the canopy only; the vignettes run on the stock Hubspace app. Was: **Vignette control surface — by Oct 1.** Custom `/vignette/<id>` pages vs. the stock Hubspace app on each vignette iPad (Guided Access, one account per room — honest by construction, no build).
    *Default:* build thin custom pages in P4; if they aren't solid by Oct 12, ship the stock app on the vignette iPads.
12. **Golden Hour technique — by Oct 5**, from bench footage: on-device motion burst vs. stepped sweep. *Default:* build one technique (on-device motion) first; the other is a seam.
    *Answered by the approved scenes (D59, Oct 11):* Golden Hour is a static five-colour gradient; no show mode uses motion. Motion stays a seam.
    *Superseded (D65, Oct 14):* neither technique. Golden Hour keeps its still per-head sun at T1 and T2 (D63); below them it has a **stepped** rung, where the Conductor moves sun rays out from the left edge at module level, a step every few seconds, in 90 s runs. Coastal Tides steps too. Simulator only: live stays off until Gate R and Alon sets `motion.stepped.live_enabled` (#27). On-device motion is still a seam (#4).
    *New input (as of Oct 7):* only code 1's bounded block movement on Kingston (#4); no direction, period, run-to-run consistency or multi-controller phase data (#5). The Studio's Golden Hour per-head gradient (D56) is SCREEN only, 30 static colours, and has never been sent to hardware (preflight blocks it; package `evidence/scene-gradient-browser-2026-10-06.json`, `evidence/scene-gradient-api-2026-10-06.json`). Only whole-strand single palette colours have reached real lights (package `CHANGELOG.txt`).
13. **Kiosk look — to the client by Oct 5** for copy and brand sign-off. *Default:* House Beautiful × Home Depot styling, words-only "Hubspace".
    *Note (Oct 6):* Paris's Studio (`/ops/showcase/`, D56) shows six scene palettes chosen in his session as the primary interface; its list has Coastal Tides where `02` and `config/modes.yaml` have Holiday at Home (`web/ops/showcase/demo-model.mjs`). It's an ops page, not the guest kiosk; no client sign-off recorded.
    *Resolved (D59, Oct 11):* the owner approved those six scenes; `config/modes.yaml` now has Coastal Tides in place of Holiday at Home and every mode uses its scene's palette.

## VWBD (fabrication)
14. **Cable routing**: can every module be routed so the index rises with canvas X (B modules included), strings uncut, slack looped above the panel? Heads 20″ apart (same x in this lattice) can't be neighbours on the wire (the puck spacing is 19.7″).
    *Partly answered (Kingston, PHYSICAL, Sep 28):* entries 0–29 drive pucks 1–30, index 0 at the controller end; 30–59 drive nothing (probes at 0, 1, 29 and 30–59, the head-15 marker, a red→purple→blue gradient; intermediate heads not swept one by one — `docs/custom-pattern-session.md`, `docs/bench-capability-check/address-probe-*.json`). That is the kit's `framebuffer_mapping` (D54), not a panel route: routing on a panel, B-module flips and String light 1's order are unverified.
    *Default:* `serpentine_rows` in config; the calibration tool records the real order in the ops overlay.
    *Drawn (A-20-1, Oct 18):* `serpentine_rows` along each module's 75″ side, not column-wise; head 0's end and the B flips still need calibration.
15. **Orientation scheme**: deck p.14 (A/B/A across X) or the p.18 appendix (AAA/BBB/AAA)? Only p.14 keeps the 10″ pitch across seams. *Default:* p14.
    *Closed (D74, Oct 18):* p14, by VWBD drawing A-20-1 (all 270 drawn lights on the p14 lattice; 150 under p18).
16. **Plenum**: controllers and PSUs hidden but reachable and ventilated (PSU indoor/dry only; manual forbids enclosed spaces above 40 °C); per-module switched power, ideally a LAN-controllable PDU. *Default:* no remote power control in software; the runbook says "power-cycle module N at its switch".
17. **Puck openings**: pucks are hexagonal with screw ears (~2″) — measure a real puck first. *Default:* n/a to software.
18. **Closed — out of scope (D67, Oct 17):** the vignette devices left the registry. Was: **Final vignette device census.** *Default (placeholders, clearly labeled in config):*
    - `entry` — 1 Defiant Wi-Fi deadbolt
    - `living` — 4 EcoSmart A19 color bulbs + 1 dual-outlet outdoor plug (tree/garland)
    - `bedroom` — 4 EcoSmart A19 color bulbs + 1 plug (humidifier) + 1 ceiling fan (disabled until confirmed)
    - `garden` — 1 Hampton Bay string light (20-segment) + 1 path-light set + 1 dual-outlet outdoor plug (fountain + spare)

## Hearst / Home Depot
19. **Golden Hour**: is "amber wash moving across the canopy" a hard requirement? Who approves the bench footage? *Default:* ship the best benched technique; kiosk copy matches what it actually does.
    *Now (D65):* the moving technique is module-level steps through the cloud, off live until Gate R; Paris or Alon can choose other scenes to move in `config/modes.yaml`. Until Gate R passes, Golden Hour is a still sun on real lights and the kiosk says so.
20. **Authorization letter** from Home Depot for programmatic control of activation-owned Hubspace devices/accounts (Oct 15 – Nov 2). *Default:* no writes to show accounts from any runtime until it exists or Alon explicitly says go; show accounts only ever live on the show box.
21. **Accounts**: who creates and owns the canopy account (the only show account the Conductor holds, D67). *Default:* Alon creates them; MFA off; staff don't open the Hubspace app on show accounts during public hours.
22. **Kiosk branding and copy**: the Hubspace logo in the UI needs brand sign-off. *Default:* words-only "Hubspace" references.
23. **Bedroom humidifier** (no Hubspace humidifier exists). *Default:* a standard humidifier on a Hubspace smart plug, on/off only.
    **Lock demo.** *Default:* lock and unlock from the tablet, with a confirm step.
    **Oct 29 evening.** *Default:* the same interactions as tour days.
    **Case-study permission** for show telemetry in the SOW. *Default:* collect telemetry; publish nothing until permission exists.

## Logistics (Alon)
24. **Spares**: 12 kits (9 + 3) + 2 SQHDE03-5PSL strings at the $99 Special Buy (ends Nov 3) — confirm stock and buy this week. *Default:* the registry supports unassigned spare devices.
25. **Staff phones**: freeze the Hubspace app version before Oct 20 (an Aug 2026 update removed custom colors from some effects). *Default:* documented in the show-day runbook.

## Motion and the guest picture (D63–D65; owner: Alon)
26. **Preview orientation.** Does the guest panel's picture of the canopy match what a guest at the stand sees
    overhead? The designs and the picture use the reflected-ceiling view of `config/canopy.yaml` (looking up, origin
    at the canopy's top-left, `02` "Which way is left"), so where Golden Hour's sun (the left edge) and the Goodnight
    moon (r0c2) land in the room depends on how the canopy is hung and where the stand faces.
    *Default:* `kiosk.preview.mirror_x: false`, `rotate_deg: 0` (the RCP drawn as is). Set both at install, standing
    at the stand under the lit canopy. This changes only the picture, never the lights. If the sun should sit on the
    other side of the room, that is a design change for Paris (`config/modes.yaml`) and makes the painted scenes'
    slot hashes stale (D63), so decide it before commissioning.
27. **Stepped cadence and cap.** How fast can the canopy step through the cloud without throttling, does a strand
    cut or fade between colours (bench item 6), and is a step's landing spread acceptable?
    *Default (D65, `motion.stepped` in `config/conductor.yaml`):* design pace 6 s a step, 6 steps a cycle; the runner
    plans at the account cap of 90 writes a minute less a 20% reserve (72), so nine modules on one account step every
    7.5 s (two kits every 6 s); never faster than 4 s, and a step that would need more than 12 s holds still instead.
    A step that would take the account past 90 writes in any 60 s is held back (since D75, one that would leave less
    than a guest's burst of room under 90). Runs last 90 s, then the still design
    holds. The governor stops a run at ≥ 8 throttled attempts that are ≥ 25% of its attempts in 60 s and holds stepped
    looks on that account still for 5 min. **Live on since D79 (Oct 19):** Gate R passed on one kit (String light 1,
    10 min, 13 writes a minute, 0 throttled); the nine-kit Gate R (`04` gate 14) still runs at install, and only Alon
    flips it. **Bench item 6 answered on String light 1:** a strand's colour change **fades** (observer: "a nice
    fade"); not yet seen on the other kits. Each step's landing spread was p95 2.78 s in the simulator against 05's 2 s
    target: reported, not gating, until Alon signs off a per-step target.
28. **Throttling: per account or per IP?** The canopy account and the bench account write from the show box's one
    IP (the vignettes are outside this system, D67; anything they do in the stock app comes from the same venue
    network, so the per-IP question stays open).
    *Default:* assume per account. The executor's budgets (≤ 1 write/s per device, bounded concurrency per account),
    the stepped cap and the governor all count per account. Gate R watches the canopy and bench accounts (no
    vignette criterion since D67). First account number: `docs/runbooks/account-rate-probe.md` (two kits at their
    per-device limit, ≈ 120 writes/min; D72's fast guest pacing assumes no tighter account limit). If it turns out to
    be per IP, the
    stepped cap would have to count every account's writes from the box, and stepped stays off until it does. Never
    rotate accounts or IPs to get around a limit.

## The client review (D77; owner: Alon)
29. **Review options' palettes, names and module-level read.**
    - The 12 options' colours and the "#n" names (and Calm #1's description) need the client's on-site sign-off
      (D59).
    - "Home Depot orange" (Golden Hour #2) as a guest-visible colour name needs the brand check (with #22).
    - The eight fin-ring options (Lowcountry Calm #2, Rainbow Row Sunset #3, Garden Gathering #2, Coastal Tides #1,
      Golden Hour #2, The Goodnight Glow #1, #2 and #3) read as three rings on the real lights at module
      level: the centre, the edge-middles and the corners. The alternative is the tilted ring Calm #1 and Garden #1
      use (4–8 visible module colours, "from one spot"), at the cost of the per-light picture following the fins.
    - The dark Goodnight options at 25–26% (night blue, deep moss) may read near-black under the fins; preview them
      on a kit before the session.

    *Default:* ship as drafted (fin rings kept, D1); change in the Design Lab at the session, then export (PR E).
