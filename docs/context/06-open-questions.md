# 06 — Open questions

Ranked by how much they change the build. Every item has an owner and a **default** — agents proceed on the default and never block on an answer. When an answer lands, update this file and the affected config.

## Settled by the bench kit (owner: Alon)
1. **What cloud profile does SQHDK03 expose?** Segment count (30 or a fixed 60), slot count, `individual` color mode, per-puck white, CCT range, presets, programs, restore-values.
   *Default:* the sibling model in `04`, seeded from `conductor/sim/profiles/hampton-bay-string-20seg.json` resized to 30; frame length = the device's framebuffer length; `expected_heads` = 30 from the registry.
2. **Does Afero accept third-party writes** of (a) slot select and (b) full `color-sequence-v2` frames via the /v1 semantic PUT?
   *Default:* build the full T1 → T2 → T3 ladder; sim runs T1, live runs T3 until the Gate B/C flags are set.
3. **Does readback reflect the device or only the cloud?** (Gate B's kit-unplugged test.)
   *Default:* assume cloud-only; the SimDriver models both behind a flag.
4. **What do the `motionEffect` / `brightnessEffect` codes do?** Is there a slow directional scroll? Does re-selecting restart the phase?
   *Default:* every code disabled in config until benched.
5. **Start skew and drift across controllers** for one 9-PUT burst.
   *Default:* ≤ 1.5 s start skew, free drift; Golden Hour re-triggers all nine on every selection.
6. **Real Afero limits** for 9-PUT bursts plus a stepped sweep on one account, alongside 4 vignette accounts behind one IP. Per account or per IP? 429s or silent drops?
   *Default:* ≤ 1 write/s per device, bursts of 9 per change, stepped sweep ≥ 2 s/step with a hard cap, hysteresis downgrade (`07` §6). Note: numbers measured from Railway reflect a datacenter IP — re-measure from the venue box.
7. **Per-puck white and color recipes** through clear/frosted Mylar at show brightness.
   *Default:* recipes in config; start with RGB + warm-white blends for ivory and amber; tune on Prototype 01.
8. **Power-loss behavior**: do slots, mode and speed survive? Is restore-values exposed? Does the profile change when the 12-puck fault happens?
   *Default:* re-send the full state vector whenever a module comes back online; always re-send speed and party-mode off; the 12-puck fault is a visual check until proven otherwise.
9. **Chaining**: does a 60-puck chain give 60 independent entries? *Default:* one kit per module.
10. **Does the Keycloak refresh-token rotation invalidate older tokens?** *Default:* assume yes — persist every rotation (`07` §3).

## Decisions Alon owes (with dates)
11. **Vignette control surface — by Oct 1.** Custom `/vignette/<id>` pages vs. the stock Hubspace app on each vignette iPad (Guided Access, one account per room — honest by construction, no build).
    *Default:* build thin custom pages in P4; if they aren't solid by Oct 12, ship the stock app on the vignette iPads.
12. **Golden Hour technique — by Oct 5**, from bench footage: on-device motion burst vs. stepped sweep. *Default:* build one technique (on-device motion) first; the other is a seam.
13. **Kiosk look — to the client by Oct 5** for copy and brand sign-off. *Default:* House Beautiful × Home Depot styling, words-only "Hubspace".

## VWBD (fabrication)
14. **Cable routing**: can every module be routed so the index rises with canvas X (B modules included), strings uncut, slack looped above the panel? Heads 20″ apart (same x in this lattice) can't be neighbours on the wire (the puck spacing is 19.7″).
    *Default:* `serpentine_rows` in config; the calibration tool records the real order in the ops overlay.
15. **Orientation scheme**: deck p.14 (A/B/A across X) or the p.18 appendix (AAA/BBB/AAA)? Only p.14 keeps the 10″ pitch across seams. *Default:* p14.
16. **Plenum**: controllers and PSUs hidden but reachable and ventilated (PSU indoor/dry only; manual forbids enclosed spaces above 40 °C); per-module switched power, ideally a LAN-controllable PDU. *Default:* no remote power control in software; the runbook says "power-cycle module N at its switch".
17. **Puck openings**: pucks are hexagonal with screw ears (~2″) — measure a real puck first. *Default:* n/a to software.
18. **Final vignette device census.** *Default (placeholders, clearly labeled in config):*
    - `entry` — 1 Defiant Wi-Fi deadbolt
    - `living` — 4 EcoSmart A19 color bulbs + 1 dual-outlet outdoor plug (tree/garland)
    - `bedroom` — 4 EcoSmart A19 color bulbs + 1 plug (humidifier) + 1 ceiling fan (disabled until confirmed)
    - `garden` — 1 Hampton Bay string light (20-segment) + 1 path-light set + 1 dual-outlet outdoor plug (fountain + spare)

## Hearst / Home Depot
19. **Golden Hour**: is "amber wash moving across the canopy" a hard requirement? Who approves the bench footage? *Default:* ship the best benched technique; kiosk copy matches what it actually does.
20. **Authorization letter** from Home Depot for programmatic control of activation-owned Hubspace devices/accounts (Oct 15 – Nov 2). *Default:* no writes to show accounts from any runtime until it exists or Alon explicitly says go; show accounts only ever live on the venue box.
21. **Accounts**: who creates and owns the canopy and vignette accounts. *Default:* Alon creates them; MFA off; staff don't open the Hubspace app on show accounts during public hours.
22. **Kiosk branding and copy**: the Hubspace logo in the UI needs brand sign-off. *Default:* words-only "Hubspace" references.
23. **Bedroom humidifier** (no Hubspace humidifier exists). *Default:* a standard humidifier on a Hubspace smart plug, on/off only.
    **Lock demo.** *Default:* lock and unlock from the tablet, with a confirm step.
    **Oct 29 evening.** *Default:* the same interactions as tour days.
    **Case-study permission** for show telemetry in the SOW. *Default:* collect telemetry; publish nothing until permission exists.

## Logistics (Alon)
24. **Spares**: 12 kits (9 + 3) + 2 SQHDE03-5PSL strings at the $99 Special Buy (ends Nov 3) — confirm stock and buy this week. *Default:* the registry supports unassigned spare devices.
25. **Staff phones**: freeze the Hubspace app version before Oct 20 (an Aug 2026 update removed custom colors from some effects). *Default:* documented in the show-day runbook.
