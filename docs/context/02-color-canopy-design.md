# 02 — The Color Canopy (current design)

Source: VWBD deck `Ref Docs/Color Canopy.pdf`. This supersedes the earlier 20×10 grid of 200 smart A19 bulbs.

## Concept
A sculptural overhead light installation: a modular white lighting plane hides all wiring and points light **downward** through a suspended landscape of **clear + frosted Mylar** ribbons (straight ribbons plus softened Spanish-moss-inspired cut profiles, hanging 6–24″ at variable depths; the built design, VWBD drawing A-20-1: 650 fins hanging 14–24.5″ in a bowl, D74). Guests choose Charleston-inspired light environments on a touchscreen; Home Depot orange and warm light are produced by the Hubspace lighting itself — the Mylar is colorless.

Design language: layered tree canopy and filtered shade, the drape and changing density of Spanish moss, shaded piazzas, warm coastal light shifting through **ivory, amber, Home Depot orange, peach and shadow**.

## The fixture: one complete Hubspace product per module
**Hampton Bay 30 LED 50 ft. Plug-in Mini Integrated LED Novelty Permanent Outdoor String Light Starter Kit, Powered by Hubspace** — model SQHDK03-15PSL, Home Depot internet #330863181.
https://www.homedepot.com/p/Hampton-Bay-30-LED-50-ft-Plug-in-Integrated-LED-Novelty-Permanent-Outdoor-String-Light-Starter-Kit-Powered-by-Hubspace-SQHDK03-15PSL/330863181

VWBD chose it ("Option 1 — preferred") over individual A19 bulbs on track because: the 30-light system hides behind each module, only low-profile heads are visible from below, fixture spacing repeats across the canopy, no track system is needed, light is directed downward, and fabrication is modular. **The control consequence is huge: the canopy becomes 9 Hubspace devices instead of 200.** See `04-device-model-permanent-lights.md` for how the cloud API exposes the strand.

Physical facts that affect the build (details in `04`):
- It's a Home Depot–branded **Nanoleaf** Permanent Outdoor Lights kit: 30 hexagonal pucks (RGB + warm white + cool white each) on 3 strings, **fixed 19.7″ spacing along the cable**. The 10″ grid therefore leaves ~22 ft of slack per module to loop above the panel — **don't cut the strings** (cutting correlates with a known "only 12 pucks light" failure).
- Power supply is rated indoor/dry only; the manual forbids enclosed spaces above 40 °C — the plenum needs ventilation and reachable controllers.
- Warranty is void in commercial use → spare kits, not warranty claims.

## Geometry
- 3 × 3 modules = 9 modules; each module is a 75″ × 40″ painted-white face panel; overall canopy 225″ × 120″ (18′-9″ × 10′-0″).
- 30 lights per module, 270 lights total, staggered **8 / 7 / 8 / 7** rows.
- **10.00″ light pitch maintained across every module seam.**
- Orientation: module B = module A rotated 180°.
- **VWBD drawing A-20-1** ('Fin diagram with lights', Oct 18; a mirrored plan at 1″ = 3′) shows an outline of
  132″ × 237″: the 225″ × 120″ canopy plus a **6″ apron** all round. Canopy frame: x = v − 6, y = 126 − u (u, v =
  inches from the sheet's top-left; the sheet turned 90° counter-clockwise). All 270 drawn lights land on this
  lattice under p14. Under p18 only 150 do (r0c1, r1c0, r1c2 and r2c1 would be flipped), and under the clockwise
  sheet turn none do. Source and extract: `docs/plans/preset-options/` (§1).

### Derived head positions (module-local inches, origin at module top-left, reflected-ceiling view)
Working this backwards from the RCP and the 10″-across-seams rule:
- 4 rows at y = 5, 15, 25, 35
- "8-rows": x = 2.5, 12.5, … 72.5 (8 heads) · "7-rows": x = 7.5, 17.5, … 67.5 (7 heads) — half-pitch offset, a brick/staggered lattice
- Module A: rows 0 and 2 are 8-rows; rows 1 and 3 are 7-rows
- Module B (A rotated 180°: (x, y) → (75 − x, 40 − y)): rows 0 and 2 become 7-rows; rows 1 and 3 become 8-rows

With **A / B / A across X** (deck page 14), every canopy row alternates correctly across the vertical seams (e.g. A ends at 72.5, B's first head sits at 75 + 7.5 = 82.5 → 10″ gap). Across the canopy there are 12 rows of 23 or 22 heads (6 × 23 + 6 × 22 = 270). Stacking modules down Y with the same orientation keeps the 7/8 alternation across horizontal seams (row y = 35 of one module, y = 5 of the next → 10″).

### Orientation scheme: closed (D74), p14
The deck contradicted itself (page 14: **A / B / A across X, the same orientation repeated down Y**; the page-18
appendix: A A A / B B B / A A A). VWBD's drawing A-20-1 settles it in favour of **p14**: every drawn light sits on the
p14 lattice, and only p14 keeps the 10″ pitch across the seams. The software still takes per-module orientation from
config; p18 stays in `config/canopy.yaml` only as `/api/canopy/layout?scheme=p18`'s comparison scheme.

### Which way is "left" (the designs and the guest picture)
`config/canopy.yaml` is the reflected-ceiling view: you are looking up, and the origin is the canopy's top-left. The
scene designs (D63) use the same frame. So "the left edge" (Golden Hour's sun) and "r0c2" (the Goodnight moon) are
RCP positions, and which physical side of the room they fall on depends on how the canopy is hung. The guest panel
draws the same frame. `kiosk.preview` (`mirror_x`, `rotate_deg`, default unmirrored at 0°) turns the picture so
that it matches what a guest at the stand sees overhead. Set it at install (`06` #26).

### Head index order (cable path) — must come from the real build
Per-head color control is addressed by index along the strand (head 0 nearest the controller, presumably). How VWBD routes the 50 ft cable behind each panel (serpentine by row? which corner is head 0? how rotation flips it for B modules?) determines the index → position map. On-device animations run in wire order, so for Golden Hour we're asking VWBD to route every module **column-wise from its canvas-left edge, B modules included**, so the index rises with canvas X everywhere. (No scene uses on-device animation now: Golden Hour's motion is D65's module-level steps, which don't depend on the wiring. The ask still holds for any later on-device motion.) The software must support:
1. a config-declared default path (serpentine, start corner, per orientation), and
2. a **calibration mode**: blink head *i* on module *m*, tech taps its position on the canopy visualizer, mapping saved to config.

The drawing (A-20-1) shows `serpentine_rows` (run 1 → 2 → 3 → 4 along each module's 75″ side), not the column-wise
routing asked for. That matters only for on-device motion, which no scene uses; head 0's end and the B flip still
need calibration.

### The fins (D74)
The Mylar fins are data: `config/fins.json` (a byte copy of the drawing's extract), named by `canopy.yaml → canopy →
fins` and validated wherever the canopy loads (`conductor/fins.py`, `geometry.load_canopy_config`). A missing or bad
file stops the start and the deploy preflight with a sentence naming the file, the entry and the fix.

| Drop | 14″ | 15″ | 16″ | 17″ | 18″ | 19″ | 20″ | 21″ | 22″ | 23″ | 24.5″ | total |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Fins | 160 | 88 | 52 | 44 | 48 | 40 | 38 | 42 | 38 | 48 | 52 | 650 |

- Each fin is about 5″ wide in plan (`fin_width_in`); the key's inches are its hanging **drop**.
- The drops form a bowl: 24.5″ across the centre panel, 14″ at the rim. The 104 fins in the apron are all 14″: they
  catch only spill from the edge lights, a built-in vignette.
- The fin angles flow: a coherent current crosses the centre panel at about 32° (coherence 0.84). The layout has
  180° symmetry; the light lattice doesn't (columns of 22 and 23 heads).
- **The `fins` design field** (`field: {kind: fins}`): t = (24.5 − D) / 10.5, clamped, where D is the mean drop of the
  fins within 12″ weighted by **exp(−(d/6)²)**. This is not the textbook exp(−d²/2σ²) with σ 6: it is the form the
  plan's `lights.json` was made with (0.0102″ at worst over the 270 heads; the textbook form misses by up to 0.19″).
  `invert` swaps the ends. `transform: mirror` is refused: the fold would put the first colour on the rim too, and no
  module reaches the rim. Its layout words are always "from the centre out", inverted too (a stepped run in
  reverse reads "toward the centre").
- **Module level reads three rings**: the centre module at t 0, the four edge-middles at ≈ 0.39, the four corners at
  ≈ 0.67. The rim's own colour never fills a strand, so the Lab's generated module-level sentence names only the
  stops module level reaches (`draft.reached_stops`).
- The Design Lab's layout picker offers it ("Fins": no parameters, Mirror off, a note on the three rings), so a
  fins design can be published to the show. No shipped scene uses the kind yet (D77 does).

### VWBD panel numbers (D74)
VWBD numbers its panels row by row on its sheet, across our short axis, so P-0n is not Module n:

| VWBD panel | P-01 | P-02 | P-03 | P-04 | P-05 | P-06 | P-07 | P-08 | P-09 |
|---|---|---|---|---|---|---|---|---|---|
| Canopy position | r2c0 | r1c0 | **r0c0** | r2c1 | r1c1 | **r0c1** | r2c2 | r1c2 | r0c2 |

They are `canopy.yaml → canopy → vwbd_panels`, checked at load (all nine, each number once). `/api/canopy/layout`
gives each module its `vwbd_panel` (the same under `?scheme=p18`: it is per position), and the ops `/visualizer`
labels read "r0c0 · A · P-03"; the kiosk payload never carries them. At install, record each kit against the panel
it hangs on (plan §9). Today's Modules 1–2 (r0c0, r0c1) belong on **P-03 and P-06**.

## Lowcountry Modes (the six guest choices)
| Mode | Brief |
|---|---|
| Lowcountry Calm | warm ivory + relaxed lamp glow |
| Rainbow Row Sunset | orange, peach + warm white |
| Garden Gathering | warm pathway + canopy glow |
| Holiday at Home | warm living-room + holiday lighting |
| Golden Hour | rich amber wash **moving across the canopy** |
| The Goodnight Glow | soft, warm dimmed light |

"Final scene behavior will be confirmed through fixture testing and supported Hubspace functionality." Golden Hour is the only mode that requires motion across modules — the hardest one through a cloud API.

*Superseded (Oct 11, D59):* the table above is the original brief. The owner approved Paris's six scenes
(`config/scenes.yaml`): Lowcountry Calm, Rainbow Row Sunset, Garden Gathering, **Coastal Tides** (replacing Holiday at
Home), Golden Hour and The Goodnight Glow, each a named palette with a mood. `config/modes.yaml` renders exactly those
palettes, at the scene's brightness, at every tier.

*Superseded again (Oct 13–14, D63, D64, D65 in `docs/DECISIONS.md`):*
- **D63: each scene is one 2-D design over the canopy**, not D59's left-to-right gradient (x / 225). The design is a
  `field` in canopy inches in the reflected-ceiling view (x from the left edge, y from the top edge; see Geometry),
  with the palette as evenly spaced stops. Calm: radial from the centre (sea glass in the middle). Rainbow Row
  Sunset: corner to corner. Garden Gathering: radial from the middle of one long edge (ivory there). Coastal Tides: a
  tilted shoreline. Golden Hour: a sun at the middle of the left edge. Goodnight Glow: a moon over r0c2. T1 paints
  the design at all 270 heads; module level (T3) sets each module to the design at its centre, so a scene shows 4–9
  module colours, not three columns. The layouts are drafts for Paris's review. Freeze them before commissioning,
  because changing a design makes every recorded slot hash of a painted scene stale.
- **D64: a new mood can land ring by ring** in its design's order (from the centre out, from one edge, corner to
  corner), with no extra writes. This is built but **off** (`kiosk.ripple_stagger_s: 0`) until Alon flips it.
- **D65: two scenes move.** Coastal Tides (the tide rolls diagonally across the panels) and Golden Hour (where its
  per-head sun isn't available, sun rays move out from the left edge). The Conductor re-sends each strand its next
  module-level colour every few seconds, in 90 s runs, and then holds the still design. This replaces D59's "no scene
  moves" for these two scenes only. Lowcountry Calm (the idle look) and The Goodnight Glow stay still. It runs in the
  simulator only. On real lights it stays **off until the stepped rehearsal (Gate R)** passes and Alon sets
  `motion.stepped.live_enabled`. Until then, real lights show Golden Hour's sun still.
- **D75 (Oct 18): any scene may move.** A scene may carry one stepped rung of three kinds: travel (D65's: the design
  moves), breathe (the design stays put and each strand's colour and/or level swells and softens in place, the
  design's start leading) and shimmer (each strand breathes on its own rhythm). A guest's choice runs it first and
  then holds the scene's best still tier (Golden Hour's per-head sun where it is commissioned); /ops keeps the ladder
  order. The idle look may carry a rung too, and may breathe now and then (`motion.idle`, off). No scene changes with
  D75 itself (PR D adds the options), and nothing changes on real lights until Gate R.

*Superseded again (D74):*
- **D74: the fins are data and a design field.** VWBD's drawing A-20-1 gives the 650 fins (validated at load) and
  closes the orientation question (p14). A design can be laid out on the fins' rings (`kind: fins`), and the Design
  Lab's layout picker offers "Fins", so a fins design can be published to the live show from now on (D68's path).
  The iPad's Now-showing picture draws the fins. No shipped scene uses the kind until D77.

*Superseded again (D76, D77):*
- **D76: options and the review menu.** A scene may name `option_of: <base id>`. While more than six scenes are
  visible, the iPad shows one row per scene with its options side by side, and /ops can take a card off the iPad
  without a deploy. In the Lab, "Make this the scene" copies an option onto its base.
- **D77 (Oct 19): 18 options, three per scene, every one moving.** For the client review the six scenes become
  "#1" (the approved palette, re-fitted to the fins) plus two drafted options each. Every option has a stepped rung
  (D75), which live hardware refuses until Gate R and `motion.stepped.live_enabled`. The palettes and descriptions are
  in `config/scenes.yaml`.

  | Id | Name | Design (module level) | Motion |
  |---|---|---|---|
  | `lowcountry-calm` | Lowcountry Calm #1 (idle, safe look) | rings tilted along the current: an inverted radial centred 12.5″ off the middle, radius 130″ | breathe: the sea glass drifts and settles |
  | `lowcountry-calm-2` | Lowcountry Calm #2 | fin rings | breathe: the level, the centre leading |
  | `lowcountry-calm-3` | Lowcountry Calm #3 | the current (linear 22°, mirrored) | travel, 8 steps |
  | `rainbow-row-sunset` | Rainbow Row Sunset #1 (custom-1) | the current (linear 18°) | travel |
  | `rainbow-row-sunset-2` | Rainbow Row Sunset #2 | the current (linear 18°), six facade colours | travel |
  | `rainbow-row-sunset-3` | Rainbow Row Sunset #3 | fin rings | travel, in toward the centre |
  | `garden-gathering` | Garden Gathering #1 (custom-2) | rings tilted along the current (inverted radial, 12.5″ off the middle) | travel, in toward one spot |
  | `garden-gathering-2` | Garden Gathering #2 | fin rings | travel, from the centre out |
  | `garden-gathering-3` | Garden Gathering #3 | fin rings | shimmer: the level |
  | `coastal-tides` | Coastal Tides #1 | fin rings | breathe: the ocean blue drifts and settles |
  | `coastal-tides-2` | Coastal Tides #2 | a channel across the current (linear 140°, mirrored, inverted) | travel |
  | `coastal-tides-3` | Coastal Tides #3 | a sweep around a spot near the middle (107.5″, 55″), mirrored | travel, 8 steps |
  | `golden-hour` | Golden Hour #1 (custom-3) | a radial sun at the middle of the left edge, radius 225″ (sunset pink on the deep centre) | travel: rays from the sun |
  | `golden-hour-2` | Golden Hour #2 | fin rings: warm white on the deep centre, Home Depot orange on the corners | travel, from the centre out |
  | `golden-hour-3` | Golden Hour #3 | the current (linear 18°) | travel |
  | `goodnight-glow` | The Goodnight Glow #1 | fin rings, inverted (moonlight blue on the deep centre) | breathe: the level |
  | `goodnight-glow-2` | The Goodnight Glow #2 | fin rings | shimmer: the level |
  | `goodnight-glow-3` | The Goodnight Glow #3 | fin rings | breathe: the level, the centre leading |

  The current is field angle 18° (31.4° in true inches: field angles are measured in the canopy's proportions).
  **The nine fin-ring options show three rings at module level** (the centre, the edge-middles, the corners). Their
  rim colour shows light by light on the apron, and on a whole strand only during a run. The other nine show 4–9
  module colours. The #1s keep their palettes, moods, slots and the idle/safe role; only the placement changed (and
  Calm #1's description). This supersedes D59's "six scenes, names never change" for the review only, until PR E.

## Hubspace control strategy (client direction, verbatim intent)
- Client prioritizes a visible **"bulb" lighting effect** over LED strip lighting.
- Hubspace-connected fixtures demonstrate the selected app-controlled features and color states.
- **Where fixture quantity exceeds practical app control, supplemental lighting can visually extend the effect** across the canopy.
- Final programming and touchscreen signage must **accurately reflect the Hubspace functionality being demonstrated**.

## Prototype 01 (VWBD's next step)
One full-scale 40″ × 75″ module with 30 Hubspace heads (one complete kit), wiring concealed above the panel, clear + frosted Mylar, orange/amber light first. They're testing fixture fit + rear wire routing, light spread at full density, clear vs frosted transmission, ribbon profiles, hanging density and 6/12/18/24″ depths, and photographing from below and at eye level. Goal: freeze the lighting module before scaling to the full 3 × 3.

**For us, Prototype 01 is the first real-hardware bench:** one kit = one cloud device. Everything in the control-path bench plan should run against it (see `04-device-model-permanent-lights.md`). The hands-on tool for it is the **single-module test interface** (`/ops/module`): paint pucks, whole-module controls, one-tap test cards matched to the test list above (white temperatures, HD orange/amber/peach/ivory, recipe compare, thirds split for Mylar zones, hue ramp, wiring sweep), calibration and a photo-hold mode. VWBD's crew can use it from their shop through Railway staging with a tester login.
