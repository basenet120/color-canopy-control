# 06 — Open questions

Ranked by how much they change the build. Each has an owner and a **default** — agents proceed on the default and never block on an answer. When an answer lands, update this file and the affected config.

## Settled by the bench kit (owner: Alon)
1. **What cloud profile does SQHDK03 expose?** Segment count (30 or fixed 60), slot count, `individual` color mode, per-puck white, CCT range, presets, programs, restore-values.
   *Default:* the sibling model in `04-device-model-permanent-lights.md` — 30-entry frames, 3 slots — detected at runtime from the device's own profile; refuse per-puck writes when the detected count isn't 30 and flag the module.
2. **Does Afero accept third-party writes** of (a) slot select and (b) full `color-sequence-v2` frames via the /v1 semantic PUT?
   *Default:* build the full T1 → T2 → T3 fallback ladder; config picks the tier per mode.
3. **What do the `motionEffect` / `brightnessEffect` codes do?** Is there a slow directional scroll? Does re-selecting restart the phase?
   *Default:* effect table lives in config with every code disabled until benched.
4. **Start skew and drift across controllers** for one 9-PUT burst.
   *Default:* assume ≤ 2 s start spread and free drift; Golden Hour re-triggers on every selection.
5. **Real Afero limits** for 9-PUT bursts + a stepped sweep on one account, alongside 4 vignette accounts behind one IP. Per-account or per-IP? 429s or silent drops?
   *Default:* ≤ 1 write/s per device, bursts of 9 per mode change, stepped sweep ≥ 2 s/step with a hard cap and automatic downgrade on any 429/403/5xx or readback mismatch.
6. **Per-puck white and color recipes** through clear/frosted Mylar at show brightness.
   *Default:* recipes in config; start RGB + warm-white blends for ivory/amber, tune on Prototype 01.
7. **Power-loss behavior**: do slots, mode and speed survive? Is restore-values exposed? Does the 12-puck failure show up?
   *Default:* reconciler re-sends the full state whenever a module comes back online; always re-send speed and party-mode=off.
8. **Chaining**: does a 60-puck chain give 60 independent entries?
   *Default:* one kit per module.
9. **Can the Claude cloud sandbox reach Afero?** (Its proxy may break aioafero's session and the raw-TLS Conclave socket.)
   *Default:* live tests run on the Railway bench service or the laptop, never from the cloud build sandbox.

## VWBD (fabrication)
10. **Cable routing**: will every module be routed column-wise from its canvas-left edge (B modules included), strings uncut, slack looped above the panel? *Default:* config supports any path; a calibration tool builds the index map from the real build.
11. **Orientation scheme**: deck p.14 (A/B/A across X) or p.18 appendix (AAA/BBB/AAA)? Only p.14 keeps 10″ pitch across seams. *Default:* p.14 in `config/canopy.yaml`.
12. **Plenum**: controllers + PSUs hidden but reachable and ventilated (PSU indoor/dry only; manual forbids enclosed spaces above 40 °C). Per-module switched power (ideally a LAN-controllable PDU) for remote power-cycles?
13. **Puck openings**: pucks are hexagonal with screw ears (~2″) — measure a real puck before cutting panels.
14. **Final vignette device census**: lamps/uplights per room, string/path light SKUs, fan yes/no, fountain pump amps + restart behavior.

## Hearst / Home Depot
15. **Golden Hour**: is "amber wash moving across the canopy" a hard requirement? Who approves slow in-module waves vs a stepped sweep from bench video, and by when? *Default:* build both techniques behind config.
16. **Authorization letter** from Home Depot for programmatic control of activation-owned Hubspace devices/accounts (Oct 15 – Nov 2). *Default:* no live writes to show accounts from any cloud-hosted runtime until it exists or Alon explicitly says go.
17. **Accounts**: who creates and owns the canopy + vignette accounts; MFA stays off; staff don't open the Hubspace app on show accounts during public hours.
18. **Kiosk branding and copy**: Hubspace mark/logo in the UI needs brand sign-off; who reviews on-screen copy? *Default:* words-only "Hubspace" references, House Beautiful × Home Depot styling.
19. **Bedroom humidifier** (no Hubspace humidifier exists): plug-switched standard humidifier OK? **Lock demo**: full lock/unlock from the tablet, or status only? **Oct 29 evening**: same interactions or a locked preset? **Case-study permission** for show telemetry in the SOW?

## Logistics (Alon)
20. **Spares**: 12 kits (9 + 3) + 2 SQHDE03-5PSL strings at the $99 Special Buy (ends Nov 3) — confirm stock and buy this week.
21. **Staff phones**: freeze the Hubspace app version before Oct 20 (an Aug 2026 update removed custom colors from some effects).
