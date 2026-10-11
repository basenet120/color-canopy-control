# 02 — The Color Canopy (current design)

Source: VWBD deck `Ref Docs/Color Canopy.pdf`. This supersedes the earlier 20×10 grid of 200 smart A19 bulbs.

## Concept
A sculptural overhead light installation: a modular white lighting plane hides all wiring and points light **downward** through a suspended landscape of **clear + frosted Mylar** ribbons (straight ribbons plus softened Spanish-moss-inspired cut profiles, hanging 6–24″ at variable depths). Guests choose Charleston-inspired light environments on a touchscreen; Home Depot orange and warm light are produced by the Hubspace lighting itself — the Mylar is colorless.

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

### Derived head positions (module-local inches, origin at module top-left, reflected-ceiling view)
Working this backwards from the RCP and the 10″-across-seams rule:
- 4 rows at y = 5, 15, 25, 35
- "8-rows": x = 2.5, 12.5, … 72.5 (8 heads) · "7-rows": x = 7.5, 17.5, … 67.5 (7 heads) — half-pitch offset, a brick/staggered lattice
- Module A: rows 0 and 2 are 8-rows; rows 1 and 3 are 7-rows
- Module B (A rotated 180°: (x, y) → (75 − x, 40 − y)): rows 0 and 2 become 7-rows; rows 1 and 3 become 8-rows

With **A / B / A across X** (deck page 14), every canopy row alternates correctly across the vertical seams (e.g. A ends at 72.5, B's first head sits at 75 + 7.5 = 82.5 → 10″ gap). Across the canopy there are 12 rows of 23 or 22 heads (6 × 23 + 6 × 22 = 270). Stacking modules down Y with the same orientation keeps the 7/8 alternation across horizontal seams (row y = 35 of one module, y = 5 of the next → 10″).

### ⚠ Open design point — orientation scheme
The deck contradicts itself: page 14 says **A / B / A across X, same orientation repeated down Y**; the appendix plan on page 18 says **A A A / B B B / A A A** (orientation varies down Y instead). Only the page-14 scheme preserves the 10″ pitch across vertical seams with the lattice above. The software must therefore take **per-module orientation from config**, and VWBD must confirm which scheme is being fabricated.

### Head index order (cable path) — must come from the real build
Per-head color control is addressed by index along the strand (head 0 nearest the controller, presumably). How VWBD routes the 50 ft cable behind each panel (serpentine by row? which corner is head 0? how rotation flips it for B modules?) determines the index → position map. On-device animations run in wire order, so for Golden Hour we're asking VWBD to route every module **column-wise from its canvas-left edge, B modules included**, so the index rises with canvas X everywhere. The software must support:
1. a config-declared default path (serpentine, start corner, per orientation), and
2. a **calibration mode**: blink head *i* on module *m*, tech taps its position on the canopy visualizer, mapping saved to config.

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
Home), Golden Hour and The Goodnight Glow, each a named static palette with a mood. `config/modes.yaml` renders exactly
those palettes; no scene moves, Golden Hour included. See `04` "Canopy control model" for how each one runs.

## Hubspace control strategy (client direction, verbatim intent)
- Client prioritizes a visible **"bulb" lighting effect** over LED strip lighting.
- Hubspace-connected fixtures demonstrate the selected app-controlled features and color states.
- **Where fixture quantity exceeds practical app control, supplemental lighting can visually extend the effect** across the canopy.
- Final programming and touchscreen signage must **accurately reflect the Hubspace functionality being demonstrated**.

## Prototype 01 (VWBD's next step)
One full-scale 40″ × 75″ module with 30 Hubspace heads (one complete kit), wiring concealed above the panel, clear + frosted Mylar, orange/amber light first. They're testing fixture fit + rear wire routing, light spread at full density, clear vs frosted transmission, ribbon profiles, hanging density and 6/12/18/24″ depths, and photographing from below and at eye level. Goal: freeze the lighting module before scaling to the full 3 × 3.

**For us, Prototype 01 is the first real-hardware bench:** one kit = one cloud device. Everything in the control-path bench plan should run against it (see `04-device-model-permanent-lights.md`). The hands-on tool for it is the **single-module test interface** (`/ops/module`): paint pucks, whole-module controls, one-tap test cards matched to the test list above (white temperatures, HD orange/amber/peach/ivory, recipe compare, thirds split for Mylar zones, hue ramp, wiring sweep), calibration and a photo-hold mode. VWBD's crew can use it from their shop through Railway staging with a tester login.
