# Plan: three options per scene from VWBD's fin drawing, motion on every option, a fin visualizer

_Oct 18, 2026. **Plan only. Nothing here is implemented.** Alon asked for the plan to be complete before the show box
moves, with no work started. Source: VWBD drawing **A-20-1 "Fin diagram with lights"** (job 20260921, dated
2026-10-18), sent by Alon. The PDF itself stays out of git, like the other Ref Docs. This folder holds what was
extracted from it._

Read in this order: §0 decisions, §1 what the drawing says, §2 constraints in today's code, §3–§4 the designs,
§5–§8 the build, §9 install day, §10 PRs and briefs, §11 open points.

## 0. Decisions (Alon, Oct 18)

| Question | Alon's answer |
|---|---|
| Palettes | **#1** keeps the scene's approved palette, but the **colour placement is reworked for module-level control** to match the real fin geometry. **#2 and #3 use new colours** for every scene. |
| Motion | **Every option includes some motion or "breathing" in this first iteration, working on the real lights.** |
| Fidelity | **Per-light first, with a module-level fallback.** |
| Visualizer | **A separate PR, built in parallel.** |
| Scope now | **Plan everything, start nothing.** |

How the cards are used: the client picks from 18 cards on the iPad, named "PRESET NAME #1/#2/#3". Alon and the
client tweak the winners in the Design Lab on site. Then the losing cards come off the iPad menu.

## 1. What the drawing says

### 1.1 Same lights, rotated: the software's geometry was already right
- The sheet is a **mirrored plan** (reflected ceiling, looking up) at 1″ = 3′. The overall outline is **132″ × 237″**,
  the long axis vertical on the sheet.
- The seams show the panels are the documented **40″ × 75″ modules**. There is a **6″ apron** all round, so the
  outline is 225 + 12 by 120 + 12.
- Every panel has **30 lights in four staggered runs (7/8/7/8)** on the documented 10″ lattice. The lattice runs
  unbroken across every seam.
- Mapping to `config/canopy.yaml`: **x = v − 6, y = 126 − u**, where u and v are inches from the sheet's top-left
  corner (the sheet rotated 90° counter-clockwise). Under this mapping **all 270 drawn lights land exactly on
  canopy.yaml's lattice** with orientation scheme p14. Under the clockwise rotation, none do.
- **This closes doc 02's open orientation point in favour of p14:** A/B/A along the 225″ axis, same orientation
  repeated across the 120″ axis.
- **The strand routing is drawn as `serpentine_rows`**, along each module's 75″ side: run 1 → run 2 joined at one end,
  2 → 3 at the other, 3 → 4 at the first. That matches canopy.yaml's placeholder `cable_path: serpentine_rows`. It
  is *not* the "column-wise from canvas-left" routing docs 02/04 asked VWBD for. That only matters for on-device
  motion, which no option uses (§5); per-light frames use the calibrated `framebuffer_mapping` anyway.

### 1.2 VWBD's panel numbers are not our module numbers

| VWBD panel | P-01 | P-02 | P-03 | P-04 | P-05 | P-06 | P-07 | P-08 | P-09 |
|---|---|---|---|---|---|---|---|---|---|
| Our module | r2c0 (Module 7) | r1c0 (4) | **r0c0 (1)** | r2c1 (8) | r1c1 (5, centre) | r0c1 (2) | r2c2 (9) | r1c2 (6) | r0c2 (3) |

VWBD numbers row by row on their sheet, which runs across our canopy's short axis. **At install, each kit must be
recorded against the panel it is hung on** (§9). Hanging "Module 1" on P-01 puts a corner kit's look in the wrong
corner. The two kits live today ("Module 1/2" = r0c0/r0c1) belong on **P-03 and P-06**.

### 1.3 The fins (650, matching the drawing's key exactly)

| Drop | 14″ | 15″ | 16″ | 17″ | 18″ | 19″ | 20″ | 21″ | 22″ | 23″ | 24.5″ |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Count | 160 | 88 | 52 | 44 | 48 | 40 | 38 | 42 | 38 | 48 | 52 |

- **Every fin is drawn about 5″ wide in plan.** The key's inches are the hanging **drop** of each clear/frosted Mylar
  ribbon, not its width.
- **The drops form a hanging bowl**: concentric, roughly rectangular rings. They run from 14″ at the rim to 24.5″
  across the centre panel, about 1″ deeper per ring.
- **The 104 shortest fins (all 14″) hang in the 6″ apron**, outside every panel. They catch only spill from the edge
  lights, so the rim will read dimmer: a built-in vignette. The centre's fins carry 75% more Mylar each than the rim's.
- **Each light has 2–5 fins within 7″ (median 3).** Measured at the lights, the drop spreads evenly over 11 one-inch
  rings of about 24 lights each. **A per-light design can paint the fin rings almost exactly.**
- **At module level the canopy collapses to three rings.** Per-light mean drop:

  | | col 0 | col 1 | col 2 |
  |---|---|---|---|
  | **row 0** | r0c0 16.9″ corner | r0c1 19.6″ side | r0c2 16.9″ corner |
  | **row 1** | r1c0 19.5″ end | **r1c1 24.3″ centre** | r1c2 19.5″ end |
  | **row 2** | r2c0 17.0″ corner | r2c1 19.5″ side | r2c2 17.0″ corner |

  The existing field `radial {centre_in: [112.5, 60], metric: canopy}` samples the module centres at **0 / 0.67 /
  0.94**: centre / edge-middles / corners, the same order as the fins. **Module-level placement can use it as-is.**
- **The fin angles form a flow field.** Neighbouring fins differ by about 17°. Two interlocking S-shaped eddies swirl
  through the outer modules. **A strong, coherent current crosses the centre panel at about 32°** (top-left → bottom-
  right in the canopy frame, y down; coherence 0.84). Rainbow Row Sunset's current 45° "corner to corner" design
  already runs nearly with the grain.
- **The fin layout has 180° symmetry** (P-01 ≡ P-09, P-02 ≡ P-08, P-03 ≡ P-07, P-04 ≡ P-06). The light lattice does
  not (columns of 22 vs 23 heads), so the mapping in §1.1 still matters for per-light frames.

### 1.4 What this means for design
1. **Put the most luminous colour of each look on the deep centre**, where the most Mylar hangs. Put the darker or
   more saturated colours toward the rim, which reads dimmer anyway.
2. **At module level, design in three rings** (centre, the four edge-middles, the four corners) and give one of them
   a slight tilt along the current, so a look shows ≥ 4 module colours and r0c0 ≠ r0c1 (the existing `two-d` and
   `kits-differ` warnings).
3. **Motion reads best when it travels along the rings** (out from the deep centre, or in toward it) **or along the
   current.** Anything else fights the sculpture.

### 1.5 Files in this folder
- `fins.json`: the 650 fins in canopy.yaml's frame. Each has `x, y` (centre, inches), `angle_deg` (plan axis, mod
  180) and `drop_in`. Also the drop key, the apron, the outer outline and the mapping.
- `lights.json`: the 270 lights. Each has its module, its VWBD panel, `x, y`, `fin_drop_in` (Gaussian σ 6″ mean of
  the fins around it; see the erratum below for what "σ 6″" means), `fin_flow_deg` and `fins_within_7in`. It also
  includes the panel → module table.
- `fin-map.png`: a redraw of `fins.json` and `lights.json` in the canopy frame (pale = 14″, dark teal = 24.5″,
  orange = lights). Checked against the sheet by overlay: every fin and light in place.
- `extract/`: the scripts (`extract.py` fins, `lattice.py` lights and orientation, `route.py` strand routing). To
  re-run:
  1. `pdfimages -j <pdf> img`
  2. `magick img-000.jpg -depth 8 rgb:img.rgb`
  3. `python3 -I extract.py img.rgb fins_raw.json` (and the same for the other two scripts)

  The scripts expect the 5100 × 3300 raster with the plan's border at (275.5, 530.5) px.

  Then, as done for these files:
  - Drop components ≤ 3 px wide. They are anti-aliasing slivers; 656 → 650.
  - Convert pixels to inches: (px − border) / 8.333.
  - Map into the canopy frame with x = v − 6, y = 126 − u. A fin axis at sheet angle a becomes atan2(−cos a, sin a).
  - Take each light's drop as the Gaussian (σ 6″, cut at 12″) mean of the fins around it (the erratum below).

  **Erratum (D74, PR A):** "Gaussian σ 6″" in this plan means **w = exp(−(d/6)²)** for the fins within 12 in
  (σ ≈ 4.24″ in the textbook sense). That is what `lights.json` was computed with (max error 0.0102 in over the 270
  lights); the textbook exp(−d²/72) misses by up to 0.192 in. `conductor/fins.py` uses the first form and a test
  pins it. `lights.json` and `fins.json` stay byte-identical: they are the acceptance data (copied to
  `tests/fixtures_fins_lights.json` and `config/fins.json`).

## 2. What today's code allows, and what has to change
Mapped by a read-only pass over the repo on Oct 18. File references are in §10's briefs.

| Constraint today | Consequence | Changed in |
|---|---|---|
| The Lab can't create, hide, reorder or delete a scene. Ids and order must match git; there is no hidden/enabled flag (schemas forbid unknown keys). | All 18 options go into git (`scenes.yaml`, `modes.yaml`, `recipes.yaml`, the scene pins) and get deployed. Hiding needs a new flag. | PR D (catalog), PR C (hide) |
| Only **3 controller slots** (custom-1..3) exist on the SQHDK03, and three modes already own them. | **At most 3 looks can be per-light on the lights at one time.** The other 15 run at module level on the lights; per-light shows on screen. | §5.4, §9 |
| Per-light (canvas/T1) needs Gate C, a proven framebuffer map and a commissioned slot on **all nine** kits. Neither live kit has a proven map. | **The client review on the real lights will be module level plus motion.** Per-light comes after install commissioning, for the three finals that gain most from it. | §9 |
| Scene rules (blocking) require every scene to be static (`motion: false`, no effect, cycles 1). Only Coastal Tides and Golden Hour may have a stepped rung. The idle mode may never step. | Every option moving needs rule changes, plus an idle policy for Lowcountry Calm. | PR B |
| Stepped (D65): one RGB PUT per changed module per step, design pace 4–12 s, stretched to the account budget (7.5 s on nine modules), runs ≤ 180 s and then holds still. **Off on real lights** until Gate R passes and Alon sets `motion.stepped.live_enabled`. | Motion on the real lights in iteration 1 means Gate R passes early in install week (§5.3). | PR B, §5.3 |
| D72: guests pace at 2 s whatever the size and back off only on real throttling. The account's 90/min is unmeasured (`docs/runbooks/account-rate-probe.md`). | Stepping and guest input share one account. Gate R plus the rate probe set the step pace. | §5.5 |
| The iPad grid is 3 × 2 with no scrolling (spec and e2e). A card's strip is the 1-D palette gradient, so options sharing a palette look identical. | The review menu needs a grouped 6 × 3 layout and 2-D card thumbnails. | PR C |
| Every palette colour needs its own recipe key, even when two scenes share a hex. The preview takes 1–6 hexes. Copy rules forbid motion words, "270" and "nine" in names, moods and descriptions. `#` starts a YAML comment. | ~45 new recipe keys. Quote every name and label (`label: Golden Hour #1` would load as "Golden Hour"). Run `copy_problem` on every string. | PR D |
| The Studio's tiles show only `demo-model.mjs`'s six. | The 18 options don't appear in the Studio (the Lab and the iPad follow the catalog). Fine for the review. | — |

**Tests pinned to six scenes** (they will fail and must change on purpose):
- `tests/test_scenes_catalog.py` 15–26 (the APPROVED ids)
- `tests/test_kiosk_routes.py` 131, 246
- `tests/test_looks_schema.py` 82–90
- `tests/test_ops_routes.py` 98
- `tests/test_module_api.py` 331
- `tests/test_modes_match_scenes.py` 149–162
- `tests_e2e/test_kiosk.py` 220 and `_no_overflow`

These need `make scene-pins`: `test_lab_export` 337, `test_scene_rules` 244, `test_modes_match_scenes` 58/253,
`test_scenes_catalog` 41, `test_d65_modes` 44. `test_modes_match_scenes` and `test_compile` 264 run over every
scene, so every new one must pass them.

## 3. The design language

### 3.1 Placement (where colours sit)
| Primitive | Field (canopy frame) | Per light | Module level |
|---|---|---|---|
| **Fin rings** | new kind `fins`: t = (24.5 − fin drop at the head) / 10.5, from `fins.json` | the 11 real rings, including the eddies | sample with `at: mean` (the module's average), or use `radial {centre_in: [112.5, 60], metric: canopy}`, which gives the same 3 rings |
| **The current** | `linear {angle_deg: 32}` (check `field.py`'s angle convention against §1.3; Rainbow Row's 45° reads "corner to corner") | bands along the fin grain | 5 diagonal bands across the 9 modules |
| **Rings + tilt** | rings with 20% of the current mixed in (a new `blend` of two fields, or a small `centre_in` offset along 32°) | asymmetric rings | ≥ 4 module colours, r0c0 ≠ r0c1 |
| **Sun / moon at an end** | `radial {centre_in: [0, 60], metric: canopy}` (exists) | arcs | the sun column, then rings across |
| **Beam** | `sweep {centre_in: [112.5, 60], start_deg}` (exists) | a wedge around the centre | the 8 outer modules, one or two lit |

### 3.2 Motion (module level, Conductor-stepped through the cloud)
Each step is **one full-state PUT per module whose colour changes**. The step PUT may also carry `brightness`:
07 §5's full vector already includes it, so a brightness change costs no extra write. On nine modules a step comes
every ≥ 7.5 s (6 s on two kits) unless Gate R and the rate probe allow less (§5.5). A run lasts ≤ 180 s, then the
still design holds.

| Motion | What moves | Exists? | Moving copy (shown only while it runs; must contain "every few seconds") |
|---|---|---|---|
| **Bloom** | rings travel out from the deep centre to the corners | yes (stepped `forward` on a ring field) | "…spreads out from the centre, one ring every few seconds." |
| **Gather** | rings travel in from the corners to the centre | yes (`reverse`) | "…draws in toward the centre every few seconds." |
| **Current** | bands slide along the fin current, corner to corner | yes (stepped on `linear`) | "…slides across the canopy along the fins every few seconds." |
| **Sweep** | a beam steps around the eight outer modules | yes (stepped on `sweep`; verify it accepts sweep) | "…turns around the canopy every few seconds." |
| **Breathe** | nothing travels: each module's colour moves a little along its palette and back, and/or its brightness swells and softens, the centre ring first and each outer ring one step later | **new** (PR B) | "…swells and softens every few seconds." |
| **Shimmer** | breathe, but each module on its own seeded slow phase (string lights, fireflies) | **new** (PR B, a breathe variant) | "…glow and soften on their own every few seconds." |

The kiosk honesty rule stays. Stepped copy may never claim an on-device effect or light-by-light animation. A
moving option says how it moves only while it moves, and says it is a few-seconds step.

### 3.3 Rules of thumb used below
- The most luminous colour goes on the deep centre; the darker or more saturated colours go toward the rim.
- At least 4 module colours, r0c0 ≠ r0c1.
- Each scene keeps its brightness draft (Calm 70, Sunset 85, Garden 75, Tides 85, Golden 95, Goodnight 28) unless
  noted.
- Breathe swings stay small (±6–12 brightness points), because a 7.5 s step is coarse. Whether a colour change on the
  kit snaps or fades is unknown (§5.3).

## 4. The 18 options

Ids:
- **#1 keeps the original id** (`golden-hour`, …). It holds the slot, the idle/safe status, the tests and the saved
  Lab versions.
- **#2 and #3 are `<id>-2` and `<id>-3`.**
- Names are `"<Scene name> #n"`, always quoted in YAML.

Moods stay the scene's own. Descriptions below are drafts: run each through `copy_problem` (no motion words, check
the layout words against the field).

### Lowcountry Calm (mood "Settle in"; idle look; 70)
| | #1 `lowcountry-calm` | #2 `lowcountry-calm-2` "Haint blue porch" | #3 `lowcountry-calm-3` "Marsh morning" |
|---|---|---|---|
| Palette | **approved:** Soft ivory #F7F2E7, Sea glass #D1EAE3 | Porch light #FFF3DC, Warm linen #F3E6CF, Haint blue #BFE3DF | Oyster shell #EEE6DA, Spartina #CFDDBE, Mist blue #D3E2EA |
| Placement | **re-fit:** sea glass on the deep centre panel, ivory at the shallow corners, the edge-middles between, tilted 20% along the current (4–5 module colours). Today it is a plain radial from the centre. | fin rings: porch-light ivory on the deep centre, linen ring, haint blue on the rim and apron (Charleston's porch-ceiling blue) | the current: marsh bands along the fin grain, mist on the deep centre |
| Motion | **Breathe:** the sea-glass centre swells to the edge-middles and back, 4 steps per breath | **Breathe:** the porch-light centre brightens and softens (±10), rim steady | **Current:** mist drifts along the grain, 8 steps per cycle |
| Description draft | "Soft ivory with a pale sea-glass heart for a calm, airy setting." | "Haint blue, Charleston's porch-ceiling color, around a warm porch-light glow." | "Oyster shell, spartina green and mist blue, like morning on the marsh." |

### Rainbow Row Sunset ("Stay a little longer"; 85)
| | #1 `rainbow-row-sunset` (custom-1) | #2 `rainbow-row-sunset-2` "Facade row" | #3 `rainbow-row-sunset-3` "Harbor dusk" |
|---|---|---|---|
| Palette | **approved:** Pastel coral #FF9E96, Blush pink #FFD2E0, Lilac #C4B3EC | Facade pink #F6B8C8, Butter yellow #F8E39A, Mint #BFE6CF, Sky blue #AFD5F2, Lavender #CDB9EA, Apricot #F9C9A0 | Apricot glow #FFB98A, Coral #FF8A7A, Rose #E979A0, Dusk violet #9A86D6 |
| Placement | **re-fit:** along the fin current at 32° (today 45°), coral at the r0c0 corner, **blush on the deep centre**, lilac at r2c2 (5–6 module colours) | the current, flatter (≈ 15°) so each module lands on a different facade: six houses down the long axis | fin rings: apricot on the deep centre (sun glow on the longest fins), dusk violet on the rim |
| Motion | **Current:** the facade colours slide corner to corner, 6 steps | **Current:** a stroll down the row, 6 steps | **Gather:** the light draws in toward the centre, 6 steps |
| Description draft | "Pastel coral, blush pink and lilac, inspired by Charleston’s colorful facades." (unchanged) | "Six Rainbow Row facade colors: pink, butter, mint, sky, lavender and apricot." | "Apricot and coral over the harbor, rose and dusk violet at the edges." |

### Garden Gathering ("Bring everyone together"; 75)
| | #1 `garden-gathering` (custom-2) | #2 `garden-gathering-2` "Magnolia garden" | #3 `garden-gathering-3` "String-light supper" |
|---|---|---|---|
| Palette | **approved:** Sage green #8FBF9D, Pale lime #E6EED1, Ivory #F7F2E7 | Magnolia cream #FBF3E1, Blossom pink #F3C6D0, Leaf green #86B886, Moss #A9C49A | Edison amber #FFC37F, Lantern gold #FFE1A3, Fern green #9DC89B, Warm white #FFF4E2 |
| Placement | **re-fit:** ivory on the deep centre ("the table everyone gathers at"), pale lime on the edge-middles, sage on the corners. Fin rings plus tilt replace today's long-edge origin. | fin rings: magnolia cream on the deep centre, blossom ring, greens on the rim | warm pools: amber and gold on the deep centre and along the current, greens at the corners |
| Motion | **Gather:** sage → lime → ivory toward the centre, 6 steps (the mood, literally) | **Bloom:** the blossom opens outward, 6 steps | **Shimmer:** modules glow and soften on their own slow rhythms (±10) |
| Description draft | "Sage green softens into pale lime and ivory for a fresh garden glow." (unchanged) | "Magnolia cream and blossom pink framed by leaf and moss greens." | "Edison amber and lantern gold over fern green, like supper under string lights." |

### Coastal Tides ("Find your coast"; 85)
| | #1 `coastal-tides` | #2 `coastal-tides-2` "Marsh tide" | #3 `coastal-tides-3` "Lighthouse night" |
|---|---|---|---|
| Palette | **approved:** Ocean blue #659BD5, Aqua #79CED4, Seafoam #B2E8D2, Foam white #E9F3E7 | Deep teal #2F8F9D, Marsh green #8EBF8C, Tidewater blue #8FC3E6, Oyster white #EEE7D8 | Night sea #34548F, Harbor blue #5B8FCB, Lamp gold #FFE4AE, Beam white #F3F6FF |
| Placement | **re-fit:** deep water (ocean blue) on the deep centre, aqua on the edge-middles, seafoam and foam white on the shallow corners and apron (the beach). Today it is a tilted line at 78°. | the current: a tidal channel of deep teal along the fin grain, marsh green and oyster white on the banks | sweep: a wedge of beam white and lamp gold around the centre over night-sea blues |
| Motion | **Breathe (tide):** the shoreline moves out a ring and back, 6 steps per tide | **Current:** the tide runs the channel, 6 steps | **Sweep:** the beam steps around the eight outer modules, 8 steps per turn |
| Description draft | "Ocean blue, aqua, seafoam and foam white, inspired by the Lowcountry coastline." (unchanged) | "Deep teal channels and marsh green, with oyster white along the banks." | "Night-sea blues with a beam of white and lamp gold, a nod to the Morris Island Lighthouse." |

### Golden Hour ("Let the evening glow"; 95)
| | #1 `golden-hour` (custom-3) | #2 `golden-hour-2` "Home Depot gold" | #3 `golden-hour-3` "Marsh sunset" |
|---|---|---|---|
| Palette | **approved:** Soft gold #FFDB9B, Peach #FFB69D, Sunset pink #FF9BC4, Rose #E779B0, Lavender #B59AE2 | Warm white #FFF1DA, Honey #FFD27A, Amber #FFAE45, Home Depot orange #F96302 | Deep amber #FFA040, Burnt orange #E8753A, Dusty rose #D98AA2, Smoky violet #8F7CC4 |
| Placement | **re-fit:** the sun stays at the left end (sun at the r1c0 end-middle). Retune the stops so the **deep centre lands on sunset pink** ("pinks lead the sunset" on the longest fins), rose next, lavender at the far corners (6–7 module colours). | fin rings: warm white on the deep centre, honey, amber, **Home Depot orange on the rim** | the current: the low sun's path corner to corner along the fin grain |
| Motion | **Bloom from the sun:** today's D65 rays across the canopy, 6 steps | **Bloom:** the glow spreads to the orange rim, 6 steps | **Current:** the sunset slides corner to corner, 6 steps |
| Description draft | "Soft gold, peach, sunset pink, rose and lavender. Pinks lead the sunset." (unchanged) | "Warm white and honey deepening to amber and Home Depot orange at the edges." | "Deep amber and burnt orange over the marsh, cooling to dusty rose and smoky violet." |

### The Goodnight Glow ("Slow the night down"; 28)
| | #1 `goodnight-glow` | #2 `goodnight-glow-2` "Firefly night" | #3 `goodnight-glow-3` "Candle and lamp" |
|---|---|---|---|
| Palette | **approved:** Periwinkle #8B8BD1, Lavender #BEA9E6, Moonlight blue #C4D6F3 | Night blue #4A5DA6, Deep moss #5B7A5A, Firefly #D9F07A | Candle amber #FFBF73, Dusky mauve #A487B3, Night blue #5C70AA |
| Placement | **re-fit:** the moon moves from the r0c2 corner (the shallowest fins, least glow) to the **deep centre**: moonlight blue there, lavender on the edge-middles, periwinkle on the corners and apron | night blue and deep moss in rings, firefly accents (per light: a few heads per module; module level: one or two modules at a time) | fin rings: a candle-amber heart on the deep centre, mauve ring, night blue rim |
| Motion | **Breathe, sleep-slow:** brightness 24 ↔ 32, 4 steps per breath at 10 s | **Shimmer:** firefly glows hop between modules | **Breathe:** the candle heart swells and softens (22 ↔ 30) |
| Brightness | 28 | 25 | 26 |
| Description draft | "Dim periwinkle, lavender and moonlight blue for a restful evening mood." (unchanged) | "Night blue and deep moss with soft firefly glints." | "A warm candle-amber heart with dusky mauve and night blue around it." |

Checks to run while building:
- Every hex gets its own recipe key (`<scene-id>-<name>` when the name is taken). Recipes stay UNVERIFIED until the
  colour study.
- "Home Depot orange" needs a brand check with the client.
- Low brightness × dark colours (Night blue, Deep moss at 25%) may read near-black under the fins. Preview on a kit
  before the client session.

## 5. Motion that works on the real lights in this first iteration

### 5.1 The path: stepped motion for every option
- Only the Conductor-stepped path can be made to work by install week. It is built and simulator-proven (D65), it
  keeps all nine modules in phase, uses the exact palette colours, and its kiosk copy can be true.
- On-device motion is unknown territory. Only `motionEffect` 1 has been seen moving (bounded, Oct 2), `brightnessEffect`
  codes are unknown, each controller runs its own unsynced clock, and it needs a slot (only 3 exist).
- **Probe the firmware presets in parallel** (`fade-3/7`, `cross-faded`, `effervescent`, and whether the `custom`
  program can take our colours). That would give a truly continuous, zero-write breath on each strand. It goes in
  only if the bench shows it with our colours. It is not a dependency.

### 5.2 Code (PR B)
1. **Allow a stepped rung on every scene.**
   - Relax the blocking rule "static" to "static, or stepped (and breathe) at module level".
   - Keep `motion: true` only where a stepped rung exists.
   - Keep "no on-device effect" for every scene.
   - Ladder shapes: `[stepped, modules, strand]`; painted ones `[canvas, slot, stepped, modules, strand]` (as Golden
     Hour today).
2. **Add breathe and shimmer to `SteppedTechnique`:**
   - `mode: travel | breathe | shimmer`. Travel is today's behaviour.
   - Breathe parameters: `shift` (ring offset swing, 0–0.5), `brightness_swing` (0–20 points), `lag_per_ring` (steps)
     and `steps_per_breath` (3–12).
   - Shimmer adds `seed`.
   - Step 0 stays exactly the still design, so the hold is unchanged.
   - Brightness rides in the step's own PUT (one write). Keep "never split a module's update" (07 §6) and the verified
     readback per step.
   - Moving-copy validators get a breathe vocabulary ("swells and softens", "glow and soften") and keep the "every few
     seconds" rule.
3. **Settle onto the best still tier after a run.**
   - Today a run holds on the `modules` rung.
   - With per-light first, a scene whose canvas/slot tier is available should hold on that tier once the run ends: one
     slot-select write per module. Motion while it runs, light-by-light detail at rest.
   - Copy follows the tier in use at each moment.
4. **Idle look policy (Lowcountry Calm).**
   - "The idle mode may never step" stays the default.
   - New `motion.idle: {breathe_run_s: 60, every_s: 300}`, off by default, yields at once to any guest input. Alon
     switches it on only after a 30-minute live soak shows the budget and the readbacks hold (a breath every 5 min ≈
     20% duty, ≈ 72 writes/min during a breath on nine modules).
   - When a guest **chooses** a Calm option, it breathes for its run like any other option.
5. **The Lab:** let `step_s`, `steps_per_cycle`, `run_s`, `mode` and the breathe parameters be edited for every scene
   with a stepped rung (today only for scenes that already had one). Validation is unchanged.
6. **The preview** (iPad picture, Lab, Studio): animate breathe and shimmer the way steps are animated today
   (`canopy_view` 252–288, `canopy-preview.mjs` `motionColors`).

### 5.3 Gates, in this order
1. **Snap or fade, 5 minutes, Module 1 (Kingston) from the show box at the venue.** Send coral → blush → coral at
   brightness 85, then 85 → 70 → 85. Film it.
   - If the kit fades between colours, breathe reads as breathing.
   - If it snaps, keep swings small (≤ 8 points, palette steps ≤ 1/6) and lean on Bloom/Current, which read as motion
     either way.
2. **Account rate probe** (D72's `docs/runbooks/account-rate-probe.md`, Alon).
3. **Gate R on Modules 1–2** (`docs/runbooks/stepped-rehearsal.md`) as soon as the Mac is up at the venue.
4. **Gate R on the nine show kits during install (Oct 20–22)**, before the client session and before tours.
5. **Alon sets `motion.stepped.live_enabled: true`** in a PR. Until then every option shows its still design on the
   real lights and the iPad says nothing about motion. That is true, but it isn't what Alon asked for, so this gate is
   on the critical path.

### 5.4 Per-light vs motion
- At most 3 options can be per-light on the lights, and only after commissioning on all nine kits.
- For the review, the real lights show every option at module level, moving. The iPad picture shows the per-light
  design.
- After the client picks, give the three slots to the three finals that gain most from per-light detail (likely
  Golden Hour's sun, the Sunset current, the Garden rings). Commission those at install. With §5.2-3, each settles
  into per-light detail after its run.

### 5.5 Budget
- On nine modules a step is ≤ 9 writes. 72 writes/min leaves D65's 20% reserve under the planned 90.
- D72 guests at 2 s also write on the same account. The stepped runner already holds steps that would pass the cap
  (`budget_holds`). Watch it in Gate R and the soak.
- If the rate probe shows ≥ 120/min with no throttling, raise `motion.stepped.account_writes_per_min` and shorten
  `step_s`. Breathe gains most from that.

## 6. The iPad menu for the client review (PR C)
1. **Review layout.**
   - When more than 6 scenes are visible, the grid becomes **six rows, one per scene, with three cards each (#1 #2 #3)**,
     so options compare side by side.
   - The scene sheet scrolls vertically. Now Showing and the brightness slider stay pinned.
   - With ≤ 6 visible it is today's 3 × 2 with no scrolling, unchanged.
   - The spec (`docs/design/guest-panel.md` "Six cards", "no page scroll", 3 × 2) gets a "review mode" section. The
     e2e no-overflow check applies only to ≤ 6 cards.
2. **2-D card thumbnails.**
   - Today's 30-segment strip is the 1-D palette, so #1 and its re-placed design look alike, and options sharing a
     palette are identical.
   - Each card gets a small top-down canopy picture of its own design. Reuse the preview renderer, or PR A's fin
     renderer when it lands.
3. **Hiding the losers without a deploy.**
   - New ops-overlay setting `kiosk.hidden_scenes: [ids]`, with a "Show on the iPad" switch per scene on `/ops`.
   - `GuestPanel.scenes()` is the iPad's single source and filters it. The attract screen cycles only visible scenes.
   - The idle scene can never be hidden.
   - A hidden scene stays in the catalog (the Lab still edits it; ops can still pick it).
4. **Promote a winner.**
   - If the winner is #1: edit and publish it in the Lab as today.
   - If it is #2 or #3: a Lab action **"Make this the scene"** copies the option's palette, layout, brightness and
     motion into the base id and publishes. The base id keeps its slot, its idle status and its tests. Then hide the
     #2/#3 ids.
   - The base keeps its plain name (no "#n") unless Alon types one. Name and palette changes are flagged `needs_d59`;
     the client's on-site sign-off is that approval, recorded in the export.
5. **Clean up (PR E, after the session).**
   - **Export the published tweaks first** (`GET /api/lab/export`) and merge them. A publication made with 18 ids
     can't be used once git drops to 6 (`live.py` 448–461), so the order matters.
   - Then a PR removes the `-2`/`-3` scenes, their modes and recipes, re-pins (`make scene-pins`) and returns the
     iPad to 3 × 2.

## 7. Design Lab on site
- It already edits any scene by id, so the 18 options are editable once they are in git.
- PR B adds the motion parameters; PR C adds "Make this the scene".
- Keep the try-on (≤ 150 s, module level). For a moving option the try-on should run its steps. Check it shares the
  stepped budget.
- Workflow at the session:
  1. Try on the options.
  2. Tweak the winner's colours, rings and pace.
  3. Save a version.
  4. Publish.
  5. Hide the other cards.
  6. Export at the end of the day.

## 8. Fin visualizer (PR A, in parallel)
1. **Data:** `config/fins.json` from this folder's `fins.json`. Validate on load: 650 fins, the count per drop matches
   the key, inside the outline, angle 0–180. Add a `fins` section to `canopy.yaml` (apron 6″, fin width 5″) and the
   VWBD panel per module (§1.2).
2. **The `fins` field kind** (§3.1): per-head t from the Gaussian mean drop (w = exp(−(d/6)²) within 12 in, the
   erratum in §1.5). Precompute per head at load; it equals `lights.json`. It is shared with PR D.
3. **Server:** `canopy_view.canopy_geometry` also sends `fins` (x, y, angle, drop) and the outline with the apron.
   `lit_state` is unchanged (head colours).
4. **Client** (`web/kiosk/canopy-preview.mjs`, `kiosk.js` `buildCanopy`/`paintCanopy`):
   - Draw each fin as a 5″ stroke at its angle.
   - Colour it by the lights around it: precompute each fin's 3–4 nearest heads with Gaussian weights (σ ≈ 6″) and
     mix their colours.
   - Opacity and brightness rise with the drop (14″ faint, 24.5″ strong) and with the scene brightness.
   - Heads become small dots under the fins; the apron fins are drawn (spill only).
   - Keep `viewTransform` (mirror/rotate from `kiosk.preview`).
   - Animate with today's motion colours. Budget: 650 strokes per frame on canvas 2D is fine on an iPad.
5. **Card thumbnails** (PR C) use the same renderer, smaller and still.
6. **Tests:**
   - Python: data validation, the rotation maps all 270 lights onto canopy.yaml, `fins` field values per ring.
   - Node: fin colour mixing, transform, and no layout work on animation frames (D69's rule).
7. **Unknown:** clear vs frosted Mylar. Draw frosted (it glows); a switch for clear can come after Prototype 01's
   photos.

## 9. Install-day additions
- **Record which kit hangs on which VWBD panel.** Label each kit with its panel number, set `devices.yaml` `module:`
  per §1.2, and keep "Module n" labels as the r/c names or rename them "P-0n". Verify with the existing blink/identify
  path before any per-light frame. Today's kits go on **P-03 (r0c0) and P-06 (r0c1)** if they stay Modules 1–2.
- **Set `kiosk.preview` rotate/mirror** so the iPad picture matches what a guest at the stand sees overhead. Golden
  Hour #1's sun end (the canopy's "left" = VWBD's top of sheet, P-01–P-03) should be the end Alon wants the sun on.
- **Gate R on nine, then `live_enabled`** (§5.3).
- **Per-light maps and commissioning** for the three finals' slots, after the client picks (§5.4).
- **Client session agenda (≈ 45 min):**
  1. Walk the 18 cards, scene by scene, with each option's motion running.
  2. Narrow to one per scene.
  3. Tweak the winners in the Lab.
  4. Make each the scene.
  5. Hide the rest.
  6. Export.

## 10. PRs, order and briefs
Every PR targets `main`, simulator only, and never deploys. Alon merges. Update `docs/STATUS.md` and add the next
D-numbers (D73 onward; D72 is the latest on `main` as of Oct 18).

```
PR 0  this plan + data (docs only)        ──┐
PR A  fins data, `fins` field, visualizer ──┼──▶ PR D  the 18-option catalog ──▶ (deploy) ──▶ client session ──▶ PR E cleanup
PR B  motion for every scene              ──┤
PR C  iPad review menu, hide, promote     ──┘ (C can merge before D: it only shows when > 6 scenes are visible)
```

**PR A: fins data, `fins` field, visualizer** (independent).
- Data and validation (§8.1).
- The `fins` field kind in `conductor/looks/field.py` and its compile path.
- `conductor/canopy_view.py` geometry.
- `web/kiosk/canopy-preview.mjs` fin rendering.
- Tests (§8.6) and a doc 02 update (§1.1–§1.3 facts, orientation closed, apron, panel numbers).

**PR B: motion for every scene** (independent).
- The scene-rule changes in `conductor/looks/scene_rules.py` (static → static-or-stepped; idle).
- `SteppedTechnique` modes in `conductor/looks/schema.py` 265–302.
- Breathe and shimmer in `conductor/looks/compile.py` 971–1010 and `conductor/sequence.py` (step brightness in the
  PUT).
- The settle-to-best-tier hold (`conductor/show.py` 95–115, 751–781).
- The idle policy in `conductor/config.py` and `config/conductor.yaml`.
- Lab motion fields (`conductor/design_lab/draft.py` 97–114, 608–649).
- Preview animation.
- Tests: breathe step 0 equals the still design, one write per changed module, brightness in the same PUT, budget
  holds on nine, an idle breath yields to a guest, the moving copy rules, and the soak with every scene stepping.

**PR C: iPad review menu, hide and promote** (independent).
- `web/kiosk/kiosk.js` 212–245, `panel-model.mjs`, `kiosk.css` 297–306 / 693–716 (grouped rows, pinned side, 2-D
  thumbnails).
- `kiosk.hidden_scenes` in the overlay schema plus `/ops` switches.
- The `GuestPanel.scenes()` filter (`conductor/guest_panel.py` 576–579).
- The Lab's "Make this the scene" (`conductor/design_lab/`).
- The spec update in `docs/design/guest-panel.md`.
- Node and e2e tests (no-overflow only at ≤ 6).

**PR D: the 18-option catalog** (after A and B; it uses `fins` and breathe).
- §4 into `config/scenes.yaml`, `config/modes.yaml` and `config/recipes.yaml` (quoted names).
- Re-fit the six #1 designs.
- `make scene-pins`.
- Update the six-scene tests on purpose, listing each one changed in the PR.
- Run every string through `copy_problem`.
- Screenshots of all 18 from the simulator's iPad and Lab.
- Golden Hour stays on custom-3, Sunset on custom-1 and Garden on custom-2 until the session.

**PR E: after the client session.** Export → merge the published tweaks, then remove the `-2`/`-3` options, re-pin,
and restore 3 × 2 (§6.5).

Timeline (dates 2026):
- **Oct 19:** PRs A, B and C in parallel. Simulator-only work can run in cloud sessions while the Mac travels; show
  accounts never go to the cloud.
- **Oct 20:** PR D; Alon merges and deploys at the venue. Snap/fade check, rate probe and Gate R on Modules 1–2.
- **Oct 20–22:** Gate R on nine, then `live_enabled`. Client session. Promote and hide.
- **Oct 22–23:** PR E, before tours start on Oct 23.

## 11. Open points (with the default this plan uses)
| Open point | Default |
|---|---|
| Which room end is the canopy's "left" (Golden Hour #1's sun), and where the iPad stands | Set at install via `kiosk.preview`. Centre-anchored designs (most options) don't care. |
| Do the kits snap or fade between colours? | Unknown. Bench check §5.3-1 decides the breathe swing. |
| Does the account take ≥ 72 writes/min from the venue for 10 minutes? | Gate R decides. Fail = still designs on the lights (true, but not what Alon asked for). |
| Per-light maps on all nine kits by the client session? | Not assumed. The review is module level plus motion on the lights, per-light on the iPad. |
| Idle Calm breathing all day | Off until a 30-minute live soak; Calm options breathe when chosen. |
| Home Depot orange as a named colour | Brand check with the client before the session. |
| Clear vs frosted Mylar in the visualizer | Frosted. |
| Copy-rule words ("fading", "deepening", "side by side") in the drafts | Fix during PR D with `copy_problem`. |
