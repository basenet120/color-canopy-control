# Status

_Last updated: 2026-10-19 (the preset-options stack, **to merge in this order: A, B, C, D**, merge commits, each
targeting `main`; A, B and C are merged as #38, #39 and #40 (and PR F as #41), and D is #42; see their blocks at the top. PR A
`claude/fin-visualizer` (D74: fins data, the `fins` field, the fin-lit iPad picture); PR B `claude/motion-every-scene`
(D75: motion on every scene — breathe and shimmer beside travel, a guest's choice runs first then settles on the best
still tier, the idle breath off); PR C `claude/ipad-review-menu` (D76: the iPad review menu, hide and promote); PR D
`claude/preset-options-catalog` (D77: the 18-option catalog, three per scene, every one moving, built on a merge of A,
B (with its review rounds 1–6, Oct 19) and C; D's own review round 1 fixed, tests and docs only; round 2 moved Garden #3 and Goodnight #2 to the plan's designs; round 3 re-ran the soak on the final catalog, now PASS). #37 is merged (`3c3ad38`). Deploy nothing until Alon asks; PR D's deploy order is in its block). Before that 2026-10-18 evening (PR G `claude/flagged-not-changing`: a
flagged note no longer holds the panel at "changing", D73, merged as #37 `3c3ad38`; and `7b21918` — D70 + D71 + D72 — deployed on the show box; see the PR G block). Also evening: PLAN, not started, `claude/preset-options-plan` (#36), three options per scene from VWBD's fin drawing; see its block. Before that, PR F
`claude/guest-throttle-pacing`: fast guest pacing, 2 s with a throttle back-off, D72; see its block). Before that, late afternoon (PR E `claude/readback-echo-lag`: the first readback's echo
lag, D71; see its block). Before that, 2026-10-18 afternoon (PR D `claude/guest-budget`: guests paced by the write budget, D70; before that
PR C `claude/panel-latency`: input to reaction on the guest panel, D69 —
merged as #31 and deployed on the show box at `0f8f38c`; see its block). **#28–#30 (PR A, B, B2) were merged into `main` on Oct 18** (checked with `gh pr list --state merged`); the
blocks below still read as before the merge. Before that 2026-10-18 (PR B2 review 5 fixed, see its block; before that 2026-10-17 night: PR B2
`claude/live-lab-repaint`: the operator slot repaint, D68's B2 part, on top of
PR B `claude/live-lab`: the Design Lab publishes to the live show, D68, built on PR A `claude/canopy-only`: the
Conductor controls the canopy only, D67; the Studio is screen only; see the three blocks below). Before that 2026-10-17 (Design Lab Slice C integrated as #27
`claude/design-lab-motion`; all three Lab slices built; see the PR stack), 2026-10-16.
**The show box is the Mac mini** (D61), **live since Oct 17** on `6ce971b` (`main` with the Design Lab #25–#27;
Alon deployed it from paste-commands; no release tag yet). **Canopy Modules 1–2 are VWBD's two kits for real**
(Kingston test = Module 1, String light 1 = Module 2) on VWBD's Hubspace account, which Alon says is the live show
account (logical account `bench`): **the Oct 19 undo is cancelled** (Alon, Oct 17). The iPad guest panel drives them
(Alon, Oct 17). A five-PR stack (#19 → #23) makes a partial canopy honest and
adds canopy-wide 2-D designs (D63), a true 270-light iPad preview, the ripple arrival (D64, off) and stepped waves
across the 3×3 panels (D65, sim-first, off on hardware until Gate R). Before that (Oct 7): Paris's VWBD field work is
canon (D54); Kingston is commissioned for per-head frames on VWBD's laptop; D57/D58 maps._

## preset-options PR D `claude/preset-options-catalog` — the 18-option catalog (D77) → `main` (Oct 19)
**Merge `claude/fin-visualizer`, `claude/motion-every-scene` and `claude/ipad-review-menu` first** (preset-options
PRs A, B and C; their numbers once opened), then this one. Use merge commits, not squash. The branch is cut from
`origin/main` (#37 merged as `3c3ad38`) with A, B and C merged in one at a time (`aaf2cbb`, `d67a3b5`, `6db9799`),
each conflict settled as integration.json says. B was merged again after its review rounds 4–6 (`9291130`, B's tip
`84aaf24`); A (`c13bfae`) and C (`f5df34d`) had not moved. Not pushed, no PR yet. Plan `docs/plans/preset-options/README.md` §4
and "§4 as built"; decision D77.

**What changed:**
- **The catalog.** 18 scenes in `config/scenes.yaml`, in catalog order: each scene's #1 (its id, approved palette,
  mood, slot and idle/safe role; renamed "<Scene> #1", the "#1" kept until PR E, C27) then `<id>-2` and `<id>-3`
  (`option_of: <id>`). 18 modes in `config/modes.yaml`, and the 46 new colours in `config/recipes.yaml`, one key
  each. The #1s are re-fitted to the fins: Calm #1 and Garden #1 are tilted rings, Tides #1 and Goodnight #1 sit on
  the fin rings, Sunset #1 runs along the current, and Golden #1's sun is stretched so the deep centre is sunset pink.
  Golden Hour #2 uses the reordered stops (D3). Only names change on the #1s, plus Calm #1's description:
  `git diff 6db9799 -- config/scenes.yaml` shows the six palettes unchanged.
- **Every option steps:** 11 travel, 5 breathe, 2 shimmer, gated live until Gate R and `live_enabled`. There is no
  `motion:` key, and `direction` is written on every rung (the Lab export can't add it). Each `run_s` is a whole
  number of cycles; Garden #3's shimmer uses lag 2 with seed 31, and Goodnight #2's is the firefly hop (colour and
  level, three steps a breath, lag 6, seed 107; review round 2). The moving copy uses D75's words: the plan's prefix C
  is dropped and "a brighter or softer level" is used.
- **The pins:** `tests/fixtures_scene_frames.json` regenerated by `make scene-pins` (never hand-merged).
- **Tests:** a new `tests/test_d77_catalog.py` (184 tests: invariants always; D77's draft guarded by
  `skip_unless_drafted`; the review menu, the overlay, the 7 am change on the real engine, the panel JSON under
  80 KB). Every six-scene, still-scene and name-pinned test is updated on purpose. A new `tests/live_path.py`
  (`refuse_stepped`) is used where a still tier is the subject. FakeEngine caches its modes parse. C's review fixture
  yields to a catalog with its own options (C13).
- **Soak (step 13).** The stepped candidates are B's six with Garden Gathering #3 in Sunset's place, and the
  `simulated:` block is gone. A mood chosen mid-run starts its own run. The idle return must have no runner. The
  ripple run strips the rungs (the live path).
- **e2e (step 14).** The plain StubPanel hides the 12 options through `ipad_menu.menu`, and the review stub is the
  real 18. Names are read from `load_scenes()`. "18 of 18" on /ops. The live tap accepts any frame of the run.
- **Docs:** D77 (and D76 under its own heading); doc 02 (D76/D77 bullets and the 18-option table); doc 04 (the #1
  rows and a 12-row options table); doc 06 #29; the plan's "§4 as built"; the rehearsal, rate-probe and
  show-operation runbooks. guest-panel.md is unchanged (the panel isn't). Lab tag stays `lab-11`; kiosk tag
  `catalog-1` on all four places.
- **Files beyond spec_D's list** (re-derived from the merged suite, or integration): `tests/live_path.py`,
  `tests/fake_engine.py`, `tests/review_fixture.py`, `tests/test_ipad_menu.py`, `tests/test_live_lab_routes.py`,
  `tests/test_live_looks.py`, `tests/test_module_geometry.py`, the "six" docstrings in `conductor/routes/kiosk.py`,
  `conductor/routes/design.py`, `conductor/routes/design_lab.py`, `conductor/design_lab/__init__.py` and two lab.js
  comments (C21), `docs/design-lab/PLAN.md` and `docs/runbooks/show-operation.md`.
- Lab exports made before D77 lands propose a reserved D-number; renumber by hand.

**Verified (sim, macOS, this branch):**
- `nice -n 15 uv run --offline pytest -q -p no:cacheprovider` after merging B's review rounds 4–6 (`9291130`):
  **3180 passed, 6 skipped (the gated round trip), 44 subtests in 892 s**. Before that re-merge, on the final code:
  3162 passed, with the browser walk below running at the same time. After the A + B + C merge, before the catalog:
  2766 passed.
- **The B re-merge (`9291130`)** changed no config. D's 18 modes load under B-R6-1 (a lagged breath whose nine
  starts are equal is refused; none of D's breathe or shimmer rungs collapses), `regen_scene_pins.py --check` is up
  to date, `ruff` is clean (259 files), `node --test tests/*.test.mjs` is 180/180, and `python -m conductor.soak`
  (seed 0) to a scratch path gives the committed report line for line (only the generated time and wall time
  differ). The browser walk and `make lab-roundtrip` were not rerun (no JS, config or Lab export code changed).
- `make lab-roundtrip` (`LAB_ROUNDTRIP=1`, niced): **6 passed in 90 min**. The six cases are canonical, rename, add,
  remove, reorder and breathe. Each export, applied to a scratch copy of this tree, passes the whole suite there, so
  no approved value is pinned outside `_pins`.
- `make scene-pins`, then `scripts/regen_scene_pins.py --check`: up to date. A scratch comparison of `_pins.t3`
  with spec_D's step-9 table found 0 mismatches: the 18 frame keys and the 18 pins are in catalog order, 17 rows
  match exactly, and Golden Hour #2 differs by D3's reorder (corners `F96504`/`F96302`, edge-middles
  `FEBF65`/`FEBD63`, centre `FFF1DA`). All 18 layout words match.
- The spec's one-liner over the shipped trio (`scene_mode_problems`, `approved=` the shipped modes) prints `18 []`.
- `ruff check .` and `ruff format --check .`: clean (259 files).
- Node: guest-panel 32/32, canopy-preview 16/16, lab-model 95/95, ops-ipad 4/4. CI runs the first, second and
  fourth through `tests/test_kiosk_node.py` (one wrapper, integration C24) and lab-model through
  `tests/test_lab_static.py`.
- **`make soak` (seed 0; `config/sim/acceptance.yaml` untouched), committed in `docs/reports/sim-acceptance.md`:**
  - **The report reads PASS** (re-run on the final catalog after review round 2, review D-R3-1). **Every pinned row
    passes.** Main run: 148 changes, p95 4.70 s, max 5.80 s. By tier: canvas 4, modules 48, stepped 122 (every mood
    steps when a guest chooses it), and 7 runs settled onto their canvas or slot. Silent drops that mattered: 31,
    healed p95 5.10 s. Out-of-band heal: 12 changes, p95 8.82 s, **max 9.70 s against the 10 s limit** (thin margin;
    seeds 1–5 give max 5.70–6.70 s).
  - **The ripple run, rungs stripped (the live path):** 59 of 60 changes landed ring by ring (one went out as the
    SIMULATED motion burst). Every module got exactly 1 PUT, none early, and there were 0 downgrades. The last-ring
    settle (a soak.yaml proposal, not pinned) is p95 7.05 s against 7.4 s, PASS. Seeds 1–5 give p95 5.65, 7.00,
    5.92, 6.11 and 6.48 s, all PASS (scratch reports). Nothing was tuned. The ring spread is p95 2.20 s (reported,
    not gating).
  - **The stepped scenario:** 12 runs of Golden Hour, Coastal Tides, Lowcountry Calm, Garden Gathering, Garden
    Gathering #3 and The Goodnight Glow, each on its own rung. 175 steps landed, with 0 superseded, failed or skipped
    (1 was carried after a pinned fault, of 15 faulted writes). Each step sent 1 PUT per changed module. At most 72
    program PUTs and 83 writes of any kind landed in any 60 s on the account. There were 0 governor trips and 0
    downgrades. The 9 finished runs settled canvas 3 and stepped 6. 3 runs were cut short by another mood, which ran
    its own rung. The idle return came at 180.4 s and installed Calm #1 still at modules, with no runner.
  - Only the two reported (non-gating) spreads fail, as before.
- `git diff 6db9799 -- config/scenes.yaml`: the six #1 palettes are unchanged; only the names and Calm #1's
  description changed. `git diff --stat origin/main...HEAD -- .github/workflows`: empty. gitleaks over
  `origin/main..HEAD`: no leaks.
- **Measured (C's ask):**
  - `catalog_thumbs` over the 18 real cards: per head 86.7 ms / 55,837 B of JSON, module level 5.7 ms / 8,569 B.
  - `thumb_basis` on the sim engine over 18 visible scenes: 51–52 ms. C measured 22–28 ms on its fixture; every
    option now compiles its stepped rung. It runs on every review-menu poll (2 s).
  - `/api/kiosk/panel` with fins and 18 thumbnails: 62.7 KB, pinned under 80 KB in `test_d77_catalog`.
- **Browser walk on the simulator** (`scripts/dev_design_lab.py --port 8304` through the `po-D-lab` launch config,
  auth off, a throwaway data dir; stopped and removed afterwards):
  - `/kiosk/` at 1180 × 820:
    - 18 cards in 6 rows (each scene's #1, #2 and #3 side by side, in catalog order), each a thumbnail canvas.
    - Only the scene list scrolls (1520 px of list in 542 px); the sheet and the page don't.
    - Now Showing reads "Lowcountry Calm #1", ready, with the still feature line: no run at start-up.
    - Layout checked by DOM at 1024 × 768 and 820 × 1180 as well (cards ≥ 195 px, no sheet or page scroll).
  - **One tap per kind of motion:** Calm #3 (travel), Coastal Tides #1 (colour breath), Garden Gathering #3
    (shimmer), Coastal Tides #3 (sweep), Golden Hour #1 (bloom).
    - Each showed its own moving copy, and the page's line matched the server's.
    - The panel carried `steps` with levels, and the dots changed between two reads 9 s apart.
    - Golden Hour #1's run ended on its commissioned canvas with the canvas copy ("Every one of the 270 lights …").
      For one poll in between, the settle's "changing" phase showed the default line.
  - **Calm #2 mid-run, guest brightness to 40:** the breath's levels moved from 60–80 to 30–50 (D75's relative
    rule), and the moving copy stayed true.
  - **/ops "On the iPad":**
    - All 12 options off: the iPad went back to the 3 × 2 grid with strips and no scroll, and Now Showing still named
      the hidden Calm #2 that was showing.
    - Back on: 18 cards again.
    - The attract screen cycles six strips.
  - **The Lab (`/ops/showcase/#lab`):**
    - All 18 scenes open with an empty warnings line; the nine fin-ring options show **Fins** pressed (nine before
      review round 2 moved Garden #3 off the fin rings; not re-walked since).
    - The motion kind and direction match modes.yaml, the breaths and shimmers show shift, swing and lag, and the
      shimmers show seeds 31 and 25 (Goodnight #2's seed is 107 since review round 2; not re-walked).
    - An unchanged 18-scene version exports an empty patch.
  - **Near-tie probe** (`/api/lab/preview`):
    - Single-colour tweaks raise no warning: on Golden Hour #2 the middle colour to warm white, nudged, or the rim to
      Home Depot orange; on Goodnight #1, Tides #1, Calm #2 and Sunset #3, a colour one unit off its neighbour.
    - Pulling two of Golden Hour #2's colours onto a third does raise two-d ("only 3 colour(s)").
  - **Promote walk-through** (C26), Golden Hour #2 → "Make this the scene":
    - The button's title and the confirm question say the motion is copied too.
    - The notice says #1's draft took #2's design and motion and that nothing was published. The base is on
      screen, "from the centre out", with no warnings.
    - "Save & publish" saved "Client Oct 19 v1". The dialog listed the colour sign-off and the custom-3 repaint (9
      slot writes).
    - After publishing, the simulated show's Golden Hour #1 has #2's palette and keeps its name with "#1" (C27).
    - The export proposes D78 and carries the "Made the scene on site (D76 …)" line and the regenerated pins.

**Review round 1 (Oct 19; D-R1-1, D-R1-2, D-R1-3, D-R1-6, TS-1, TS-2, TS-4, TS-5): tests and docs only; no config,
code, JS or tag changed.** Each test fix was mutation-checked: the mutation fails the new test and was reverted
(`git status` clean after each); the old publish, breath and named-colour tests passed or skipped it.
- **The fins publish tests (D-R1-1, D-R1-2, TS-2; integration C25).** They edited Coastal Tides, which D ships on the
  fins, so the Fins edit changed nothing. `test_a_fins_design_publishes_to_the_simulated_show` and
  `test_fins_drafts_pass_the_published_trio_checks` now move `off_the_fins()` onto the fins: the first unpainted
  travel scene not on the fins in git (Golden Hour #3, linear 18°, today). The show's field is not fins before the
  publish and is a FinsField after it (modules and stepped); the copy is regenerated "from the centre out"; there
  are nine look writes. Mutation: `_patched_mode` keeping the old field for a fins draft (the old trio-checks test
  caught it only through its Golden Hour half).
- **Named colours (TS-1).** The truth half of `test_named_colours_are_on_the_strands` is an invariant now: a colour
  named at module level has weight ≥ 0.3 at some module centre, and one named in the moving copy is some strand's
  nearest colour at some step. Only the C28 hedge rule waits on `drafted`. New
  `test_the_labs_generated_copy_names_only_colours_on_the_strands` (18) holds the same on the Lab's regenerated
  copy. Mutation: D3's reorder reverted in config with the pins regenerated ("Home Depot orange" named at 0.024).
  **The first `make lab-roundtrip` after this failed canonical, add and reorder, and both causes were in tests:**
  the new test asserted the regenerated sentence differs from git's (false once an export has put it there; it now
  asserts the trio carries `generated_copy`'s sentences), and `test_lab_export.edit_scene`, the export stand-in for
  `apply_drafts`, wrote a T3 sentence naming every colour in stop order. On the "add" export that read "from the
  centre out: periwinkle, then lavender, moonlight blue and dusk", where Goodnight's inverted fin rings show dusk in
  the middle and no strand shows periwinkle. The real Lab never writes that; the stand-in now writes the Lab's own
  T3 sentence ("dusk, then moonlight blue and lavender"), as it already did for a breath.
- **A breath's sends (TS-4).** Grouped by the runner's step window (t0 + n × step): at most one per device per step
  of the run, at most one in the settle, none after it. Mutation: the runner re-sending, 4 s into a step, each
  module the next step leaves alone.
- **Docstring (TS-5):** the review-menu test says CI runs guest-panel.test.mjs through `tests/test_kiosk_node.py`.
- **Docs (D-R1-3, D-R1-6):** doc 04's paragraph above the scene table (18 options, three canvases, fifteen starting
  at stepped, every option moving, refused live until Gate R) and its Tides #2 row; doc 02's Tides #2 row ("a
  channel along the current") and a D77 note on the D65 bullet; the design-lab PLAN's three "the idle scene never
  steps / has no stepped rung" lines marked superseded by D75/D77.
- **Verified (on `ba8f6fa`, the last code commit of the round):** `nice -n 15 uv run --offline pytest -q -p
  no:cacheprovider`: **3198 passed, 6 skipped, 44 subtests in 890 s** (3180 before, plus the 18 new). `make
  lab-roundtrip` (niced): **6 passed in 90 min** (canonical, rename, add, remove, reorder, breathe). `ruff check .`
  and `ruff format --check .` clean (259 files). `regen_scene_pins.py --check` up to date. gitleaks over
  `origin/main..HEAD`: no leaks. No dev server was started.

**Review round 2 (Oct 19; D-R2-3): two designs moved to the plan, two differences given their reasons (`f80b293`).**
- **Garden Gathering #3** is the plan's warm pools, not a ninth fin-ring option: the current folded at 140°
  (Coastal Tides #2's layout): Edison amber on the centre and along the current, lantern gold beside it, fern green
  on the two far corners. Eight fin-ring options now.
- **The Goodnight Glow #2's** shimmer is the plan's firefly hop: a three-step breath of colour and level (reverse,
  shift 0.35, swing 6, lag 6, seed 107, run 144 s); one or two strands glow at a time, each corner turns full
  firefly at its own step, the centre never does. Its copy now says "its next colour and a brighter or softer level".
- **Kept, with reasons** (README §4 as built, D77): Rainbow Row Sunset #2 at 18° (the plan's 15° in true inches is
  8.1° as a field angle; two-d fires under 15°); Coastal Tides #3's folded sweep (evenly spaced stops can't narrow
  the wedge; unfolded, beam white sits beside night sea).
- Four tests in `tests/test_d77_catalog.py` pin them; mutation-checked (the round-1 config fails the warm-pools and
  firefly tests; an unfolded sweep and a 14° current fail the other two; config restored, `git status` showed only
  the intended files).
- **Verified:** full suite `nice -n 15 uv run --offline pytest -q -p no:cacheprovider`: **3200 passed, 6 skipped, 44
  subtests in 894 s**. `ruff check .` and `ruff format --check .` clean; `regen_scene_pins.py --check` up to date
  (pins regenerated by `make scene-pins`). No dev server. **`make lab-roundtrip` not re-run this round (UNVERIFIED
  for the two new designs);** the Lab walk-through's "nine Fins pressed" and "seeds 31 and 25" lines predate it.
  `docs/reports/sim-acceptance.md` was not re-run: its stepped scenario includes garden-gathering-3, whose design
  changed (same shimmer parameters).
- **Review round 3 (D-R3-1, D-R3-3), docs and the soak report only, no code or config changed:**
  - **D-R3-1:** `make soak` (seed 0) re-run on the final tree (after round 2's Garden #3 and Goodnight #2) and
    `docs/reports/sim-acceptance.md` committed: it now reads **PASS** (it read FAIL on the pre-round-2 catalog). The
    soak bullet above and D77's "The soak." carry the new numbers (main p95 4.70 s, ripple last ring p95 7.05 s,
    out-of-band heal max 9.70 s); the "fin rings ripple as 1, 4 and 4" explanation for a FAIL is dropped and seeds
    1–5 were re-measured (`nice -n 15 uv run --offline python -m conductor.soak --seed N --report <scratch>`, each
    exit 0 and PASS). The stepped-scenario lines are unchanged.
  - **D-R3-3:** guest-panel.md §12 item 10 no longer says the card thumbnails reuse the fin layout; it points at
    item 11 (C12 option b: no fins, a thumbnail dot radius), which matches `paintThumb` in kiosk.js.
  - No test added: neither fix changes behaviour; the soak re-run is the check. Full pytest not re-run (no code or
    config changed since round 2's 3200 passed).

**Not run / UNVERIFIED:**
- **`make e2e`: UNVERIFIED.** Playwright's browsers aren't installed on this Mac (07 §9). The e2e files collect (57
  tests), and the live tap's frame logic was checked against the real engine's panel in a scratch test. The
  `kiosk-review-*.png` screenshots at 1180 × 820, 1024 × 768 and 820 × 1180 aren't made; none are tracked.
- **Found in the walk-through, not fixed here: the Lab's generated copy lower-cases colour names.** After the
  promote, the iPad read "… warm white, then honey, home depot orange and amber." The same happens to "Edison
  amber" (Garden Gathering #3). The hand-written copy keeps the capitals, but any Lab edit or promote of those
  options regenerates the sentence (`draft.colour_phrase`, the strand template). Decide before the session: a
  proper-noun rule in the Lab's copy, or names without one (06 #29's brand check).
- **Found: a promote adds new recipe keys.** The export's patch adds `golden-hour-warm-white`, `golden-hour-honey`,
  `golden-hour-home-depot-orange` and `golden-hour-amber` beside #2's own keys for the same colours. This is the
  Lab's allocation, with no clash (`test_each_option_can_be_made_its_scene_without_a_recipe_key_clash`). The colour
  study would then tune two keys per colour until PR E removes the options.
- **On hardware:** nothing. On live every option shows its still module-level design until Gate R and
  `motion.stepped.live_enabled` (`show.py`'s `tier_allowed`).
- B7 stands: a moving option's Lab try-on stays still at module level (D66).

**What the 7 am test mode change does now:** with Calm #1 showing it changes to The Goodnight Glow #1. In the
simulator that is its breath (ops keep ladder order, and the rung is first), and every module reaches Ready within
the window. It then restores Calm #1 still at module level (the change back never starts a run). On live hardware
the rung is refused until Gate R and `live_enabled`, so the change lands at module level. Pinned by
`test_the_7am_test_mode_change_on_the_real_engine`.

**Needs real hardware:** the breathe and shimmer pictures on the lights (does a level step read as a breath or a
blink; snap or fade, bench item 6); Gate R on Modules 1–2, then on nine with Rainbow Row Sunset #2 (9 PUTs a step);
the rate probe, before `live_enabled`; per-light pictures only after commissioning (module level until then, so the
eight fin-ring options read as three rings); the dark Goodnight options at 25–26% previewed on a kit; the colour
study for the 46 recipes; the "Home Depot orange" brand check.

**Deploy order:** export any live Lab publication first (GET `/api/lab/export`: after D the ids, order and ladders
differ from git, so the show falls back to approved with an /ops warning). Deploy D before commissioning custom-1 to
custom-3 at the venue (Sunset's angle, Garden's rings and Golden's radius change the painted designs, so every
recorded slot hash goes stale; a slot commissioned earlier needs the operator repaint). After the deploy the iPad
shows the review menu (18 cards); /ops "On the iPad" hides the 12 options to go back to six.

**Questions for Alon:**
1. The eight fin-ring options (Calm #2, Sunset #3, Garden #2, Tides #1, Golden #2, Goodnight #1, #2 and #3)
   show three visibly different colours at module level (the centre, the edge-middles, the corners); they pass two-d
   only on near-ties (ΔE76 0.33–1.23). Keep the fin rings (the default, D1), or move them to the tilted ring Calm #1
   and Garden #1 use (4–8 visible colours, "from one spot", the per-light picture no longer following the fins)?
2. PR C's two (tier-true cards against a labelled design view; thumbnails on the six cards after PR E) still stand.

**Next step:** see "Next step" below (Alon merges A, B, C, D; export; deploy at the venue; bench item 6; the rate
probe; Gate R on two kits, then nine; `live_enabled`; the client session; promote and hide; PR E).

## preset-options PR A — fins data, the `fins` field, the fin-lit iPad picture (D74), `claude/fin-visualizer` → `main` (Oct 18)
Cut from `origin/claude/flagged-not-changing` (= `main` + #37): **merge #37 first**, then this (merge commit). Sim
only: nothing new is sent to a device, and no scene changes. Plan `docs/plans/preset-options/README.md` §8; D74.
**What changed:**
- **The data:** `config/fins.json` (a byte copy of the plan's `fins.json`, VWBD drawing A-20-1's 650 fins) and two
  new `canopy.yaml` sections: `fins` (file, 6″ apron, 5″ fin width) and `vwbd_panels` (P-01…P-09 per position).
  The canopy.yaml comments now say p14 is what is built (270 drawn lights on the p14 lattice, 150 under p18) and the
  routing drawn is `serpentine_rows`.
- **The loader:** `conductor/fins.py` (strict models, the immutable `FinMap`, a cached loader, plain ConfigError
  sentences) and `geometry.load_canopy_config`, which now reads through `config._read_yaml` and attaches `fins`,
  `fin_map` and `vwbd_panels`. **Every device path loads the canopy through it, so a missing or broken fins.json
  stops the start and the deploy preflight** (`FAILED in looks`; pinned in `tests/test_fins.py`; the reason is now
  said in `live.preflight_line` and the preflight docstring). A canopy.yaml without the sections loads as before
  (the frozen Lab baseline does).
- **The field:** `{kind: fins}`, t = (24.5 − D)/10.5 with D the exp(−(d/6)²)-weighted drop within 12″ (the form
  `lights.json` was made with: 0.0102″ at worst; the plan README has an erratum). Module centres read 0 / ≈ 0.39 /
  ≈ 0.67; `bands` rounds to 2 decimals so the three rings stay whole; mirror is refused; the words are "from the
  centre out". `load_modes` checks modes.yaml against the loaded canopy (with its fin map); a fins design over a
  canopy without one is a plain sentence per canvas.
- **The Lab:** the layout picker offers **Fins** (`LAYOUT_KINDS`, pinned to `F.FIELD_KINDS`): no parameters, Mirror
  disabled, and a note true for every palette size, plain or inverted. Server side, a fins draft over a canopy
  without a fin map is one plain problem from `apply_drafts`, `draft_changes` and `resolve_copy` (which the preview
  calls first, so `/api/lab/preview` answers it as a 422 on `layout`, not a 500; review A-R1-2),
  `export.describe_field` names "fin rings", and the router drops every kind's union tag. **A fins design can be published to the live show from this
  PR** (D68's path); sim tests publish a fins Coastal Tides to the simulated show and pass a fins Tides + Golden Hour
  trio through `validate_trio` and the STEPPED/T3 compiles. The Lab's stage paints the rings per head (no fin
  strokes there). Lab tag `lab-8` → **`lab-9`**.
- **The kiosk payload:** `/api/kiosk/panel`'s `canopy` adds `apron_in`, `outline`, `fin_width_in` and the 650 `fins`
  ({x, y, angle_deg, drop_in}; none of fins.json's text): 4,523 → 44,251 B (38,421 B compact), pinned under 48,000.
  `state.lit` is unchanged.
- **Ops:** `/api/canopy/layout` modules carry `vwbd_panel` (the same under `?scheme=p18`); `/visualizer` labels read
  "r0c0 · A · P-03", falling back to "r0c0 · P-03" when narrow.
- **The iPad picture:** `canopy-preview.mjs` grows the drawing by the 6″ apron (237 × 132), draws 1.6″ head dots and
  each fin as a stroke along its axis; `finNeighbours` (4 nearest heads, w = exp(−(d/6)²), memoised per geometry: 8 ms
  once in node) and a pure `finPaint` (lit/changing mixed at full strength; a fin is changing, and pulses, when half
  its lit weight is; unlit grey at half its drop's opacity; no colour claim once 10% of its weight is unknown, drawn
  as a dashed `--slider-rest` line). `kiosk.js` compares the geometry key before `layout()` (no layout per poll any
  more) and repaints fins only when a dot's paint changed. SVG, not the plan's canvas 2D (guest-panel.md §12 item 10).
  Kiosk tag **`fins-1`**. `tests/test_kiosk_node.py` puts the canopy-preview + guest-panel node tests in CI's pytest.
- **Docs:** doc 02 (the orientation question closed, the apron, the fins, the panel table, the D74 bullet), doc 06
  #14/#15, the plan README erratum on the weight form, guest-panel.md §2.6 (the fins, a Fins column in the state
  table, reduced motion), §6 and §12 item 10, D74 in DECISIONS.md.
- Lab exports made before D77 lands propose a reserved D-number; renumber by hand.

**Verified (sim, macOS, this branch):**
- `nice -n 15 uv run --offline pytest -q -p no:cacheprovider` on the final code (`310454f`, the review fix; the docs
  commit after it changes no code): ****2560 passed, 5 skipped, 44 subtests in 687 s****. (Before this PR, at #37:
  2473 passed.)
- Review A-R1-2 (the preview's fin-map refusal): `resolve_copy` over the frozen baseline canopy raises the plain
  DraftError, and `POST /api/lab/preview` with that canopy answers 422 with exactly that problem
  (`tests/test_lab_draft.py`, `tests/test_lab_routes_preview.py`). Mutation check: with the new guard removed both
  fail (the field's bare ValueError); restored, both pass.
- The spec's targeted list (22 files) plus `tests/test_static.py`: 757 passed in 132 s.
- `ruff check .` and `ruff format --check .`: clean.
- Node: canopy-preview + guest-panel **38/38** (33 before), lab-model **86/86** (85 before); both now also run under
  CI's pytest (`tests/test_kiosk_node.py`, `tests/test_lab_static.py`).
- `scripts/regen_scene_pins.py --check`: up to date (no scene changed).
- `make lab-roundtrip` (`LAB_ROUNDTRIP=1`, niced): **5 passed in 57 min** (the canonical edits, rename, add, remove,
  reorder: each export applied to a scratch copy of this tree passes the whole suite there).
- `load_canopy_config()` → `FinMap(650 fins, sha256 7a1754886f97)`, the fins section and all nine panels;
  `preflight.run` over an empty sim data dir → 0; with fins.json deleted or one fin's `angle_deg` 181 → 1 (tests).
- gitleaks over the branch's 17 commits (`--log-opts=origin/claude/flagged-not-changing..HEAD`) and over a bare clone
  of the branch (no `.gitleaksignore`, what CI sees for new commits): no leaks found.
- **Browser walk on the simulator** (`scripts/dev_design_lab.py --port 8301`, auth off, a throwaway data dir;
  Chromium in the Claude browser pane; stopped afterwards):
  - `/kiosk/` (at step 10): 650 `line.fin`, 270 dots, one outline, viewBox `0 0 237 132`; after a Golden Hour tap
    the fins pulsed with their dots ring by ring (650 → 370 → 80 → 0 changing) and settled lit; a Coastal Tides
    stepped run changed the fins' strokes step by step; no console errors; no DOM mutation in the picture over 6 s
    steady; no sheet scroll at 1180 × 820, 1024 × 768 and 820 × 1180.
  - Frame times over the 20 s after a Coastal Tides tap at 1180 × 820 (the scene change, its pulse and the run):
    p50 16.7 ms, p95 18.6 ms, p99 33.4 ms, two long tasks (66 ms and 100 ms, at the change). **Not throttled** (the
    pane has no CPU throttling), on the Mac mini with the full suite running niced alongside.
  - The Lab (`/ops/showcase/#lab`, final tree): Rainbow Row Sunset shows Linear / Radial / Sweep / Fins. Fins shows
    the note, disables and unticks Mirror (also after Mirror was ticked on Radial), paints three rings at module level
    (coral on the centre strand, a pink on the four edge-middles, a lilac-leaning pink on the four corners), reads
    "from the centre out", and the
    module-level sentence names only "pastel coral, then blush pink" (lilac, the rim's colour, isn't reached); with
    Invert, "lilac, then blush pink". Radial and back to Fins kept Invert. Reset to approved restored linear. No
    console errors; the four buttons fit at 820 wide. Nothing was published or tried on.
  - `/api/canopy/layout` gave r0c0 P-03 … r2c2 P-07; `/ops/showcase/` serves `lab.css?v=lab-9` and `lab.js?v=lab-9`.

**Not run / UNVERIFIED:**
- `make e2e`: Playwright's Chromium isn't installed on this Mac. `tests_e2e/test_kiosk.py` is extended (650 fins, the
  outline, both viewBoxes, fin colours against a Python mix, the changing pulse, unlit and dashed unknown fins via a
  StubPanel `unknown` option, no mutation over 2.5 polls) and collects (21 tests); its Python mix was checked against
  `finPaint` in node for all 650 fins of three stubs. So the three tracked visualizer screenshots
  (`docs/screenshots/visualizer-*.png`, which would gain the P-0n labels) are **not regenerated**; the kiosk canopy
  screenshots stay untracked, as before.
- Frame times under 4× CPU throttling (the spec's DevTools check): not measured.

**Needs real hardware / UNVERIFIED:**
- The 9th-gen iPad's WebKit drawing 650 fins + 270 dots through a stepped run and a changing pulse.
- What the live picture shows for Modules 1–2: flagged-only since D73, they read unknown after every confirmed scene
  (outline dots, dashed fins) while the seven panels with no kit are unlit grey (pinned by
  `test_flagged_kits_read_unknown_in_the_picture_after_a_confirmed_scene`). `lit_state` is left as it is (A3).
- Clear vs frosted Mylar (the picture draws frosted only); recording each kit against its P-0n at install (plan §9).
- A slot repaint of a fins frame on a real kit (a painted fins scene published live): no kit has a proven map (D54).

**Blocked:** nothing.

## PR F `claude/flagged-reads-lit` — flagged kits read lit on the iPad's picture, not as outlines (D78) → `main` (Oct 19)
Not the Oct 18 PR F (`claude/guest-throttle-pacing`, #35). Cut from `main` at `3c3ad38` (#37 merged); independent of
the preset-options PRs A–D, no other PR first, small. **Found (Alon was told with A3):** since D73 a module whose only
problem is a flagged note (D54's "no proven map yet"), with its look confirmed, is "stuck" at once, and
`canopy_view.lit_state` drew any stuck module as `unknown`. So the live iPad drew Modules 1–2 as outlines right after
every confirmed scene (before D73, 10 s later), while their lights showed the scene.
- **The fix (D78):** the engine's module record carries `flagged_only` (`tracker.flagged_only`), and `lit_state` reads
  such a module as confirmed: `lit` with its colours, or `off` once a guest's power-off reads back off. A module stuck
  for any other reason (a readback that still differs, a failed write, an offline controller), a locked one and a
  resting canopy still read `unknown`. /ops is unchanged: the note shows and the phase needs attention. The payload
  changes, not the JS: **no kiosk or Lab tag bump**. Docs: D78, guest-panel.md §2.6 (`lit` and `unknown` rows), the
  payload note in `conductor/routes/guest.py`.
- **Verified (sim, macOS):** **`tests/test_flagged_reads_lit.py`** (10 tests), written first against `3c3ad38`, where
  3 failed, as on the iPad: nine flagged modules read `unknown` through the engine; the truth table read the record as
  `unknown`; on the two-kit partial show, GET /api/kiosk/panel drew r0c1 `unknown` after a confirmed scene. After the
  fix:
  - a confirmed flagged module's picture equals an unflagged one's (`lit`, same colours);
  - the record reads `stuck` and `flagged_only` true, and the look phase stays needs attention;
  - the panel shows both kits `lit` with colours and the other seven `dark`.
  Controls, passing before and after:
  - a silent drop, with or without the note, is stuck only past the retry window (> 9.5 s), never `flagged_only`, and
    reads `unknown` at the instant it is stuck;
  - a locked flagged module reads `unknown`;
  - in the truth table, a locked record (even marked `flagged_only`), a stuck one with `flagged_only` false, and a
    failed write all read `unknown`, on and off;
  - a resting canopy reads `unknown`.
  **Mutation-checked:** without the record field, 2 fail; with lit_state unchanged, 3 fail; with the stuck check
  dropped, or every stuck module treated as flagged-only, 4 fail. `nice -n 15 uv run --offline pytest -q` → 2484
  passed, 5 skipped, 44 subtests (11.5 min, final code tree; D73's tests included); ruff check / format clean; node
  151/151 (no JS changed). No dev server was run.
- **Not changed (a follow-up for Alon):** the guest panel also hides the feature line (the technique sentence) while
  the look phase needs attention (`conductor/guest_panel.py`, "feature"), and flagged kits keep it there. Checked in
  the sim on the two-kit setup: settled and unflagged, the panel says "Colour and dimming: two Hubspace strands, each
  set to a single colour from the scene."; with both kits flagged, it shows no sentence. Same cause as D78; whether a
  flagged note alone should hide a sentence that is still true is Alon's call.
- **Lab exports:** once D78 is on `main`, the export proposes D79 (max + 1), clear of the reserved D74–D77. If D77
  lands first, an export made before this PR merges proposes D78, which this PR reserves: renumber it by hand. Keep
  DECISIONS.md in numeric order whatever the merge order.
- **Needs the real box:** after deploying, tap a scene. Once the kits confirm, the iPad should fill Modules 1–2 in the
  scene's colours, with no outlines, and /ops should still show the D54 note on both.
- **Deploy:** the usual paste block, on its own. No iPad reload is needed: the page is unchanged and reads `state.lit`
  on its next poll.

## preset-options PR B — motion on every scene (D75), `claude/motion-every-scene` → `main` (Oct 18)
Cut from `origin/claude/flagged-not-changing` (= `main` + #37). **Merge #37 first** (already satisfied: #37 is merged
into `main`, `3c3ad38`). Not pushed, no PR yet. Any scene
may carry one stepped rung (just before `[modules, strand]`) of three kinds: travel (D65), **breathe** (each strand's
colour and/or level swells and softens in place, the design's start leading) and **shimmer** (each strand on its own
seeded rhythm). A breath's brightness rides in the step's own PUT. A guest's (kiosk) choice runs the rung first and
then **settles** onto the best still tier (Golden Hour's per-head sun where commissioned); /ops keeps the ladder
order; re-picks, start-up, the idle return, the safe look and the checklist's change back never start a run; a choice
runs once. Held steps are drawn as held, two in a row end the run. The idle look may carry a rung; `motion.idle`
(its breath) ships `null`. Any look can be rehearsed. "This scene moves" is `Mode.moves`; `motion: true` is refused
beside a rung. **No scene changes** (`config/scenes.yaml`, `modes.yaml`, `recipes.yaml` untouched: PR D adds the
options), and **nothing changes on real lights**: `motion.stepped.live_enabled` stays false.

**The preset-options stack, in merge order** (each targets `main`, merge commits, not squash): #37
`claude/flagged-not-changing` (merged, `3c3ad38`) → preset-options PR A `claude/fin-visualizer` (D74) → **PR B `claude/motion-every-scene`
(D75)** → PR C `claude/ipad-review-menu` (D76) → PR D (the 18-option catalog, D77: "merge #A, #B, #C first"). Lab
exports made before D77 lands propose a reserved D-number; renumber by hand.

- **Review round 1 (Oct 19): every confirmed finding fixed.** Each code fix has its test and a mutation check (the
  fixed line reverted, the test failing, the fix restored); the line-ending and docs fixes are checked by diff and
  reading:
  - **B-R1-1, the cap:** since D75 a mood chosen mid-run runs too, and the old check admitted the new run's first
    step at exactly 90 (stepped soak 91, FAIL). A step now keeps a guest's burst of room (one write per module):
    held when the writes still in its 60 s window (less those leaving within the 1 s per-device gap) plus its own
    plus that burst would pass 90. Same threshold in a run's steady state; a run on top of another holds its first
    step. Stepped soak 82 (seeds 0–15: 80–89, was 86–92), no run ended by held steps.
  - **B-R1-1, guest 100:** a guest's level now moves the breath's centre so the whole swing fits [10, 100] (at 100
    it breathes 92–100, at 10 it breathes 10–18); every step still sends every module under the moving copy. Before,
    half a level-only breath went silent at 100. The hold after the run is at the guest's own level.
  - **B-R1-2:** the soak counts on its own a step a pinned fault made a module miss (429 on every attempt, or a
    silent drop; the next step carries it by design) and bounds it by the faulted writes; `test_short_soak` no
    longer pins the faults' luck at 0. The breathe export's 2 failing steps were both 3 × 429 (traced).
  - **B-R1-3:** the Lab's "Show box today" pace plays the show box's own program (its lags over the two kits), or
    says it holds still (the server's refusal) when that compile refuses.
  - **B-R1-4:** `demo-model.mjs` and `index.html` keep their CRLF endings: their diff is the 13 + 6 real lines.
  - **B-R1-4 (runbook):** Gate R at the full rate uses a breathe with `lag_steps: 0` or travel; a breathe look is for
    watching level steps (a lagged breath writes less, not more).
  - **B-R1-5:** the PLAN block, stack item 00 and Next step now carry PR B.
  - **TS-1 / TS-2 / TS-3:** tests for the floor at `guest_brightness_min`, the guest-applied `levels` the iPad draws,
    and two holds apart that don't end a run (a sent step resets the count).
- **Review round 2 (Oct 19): every confirmed finding fixed**, each code fix and test with a mutation check (the line
  reverted, its test failing, `git checkout --` and `git status` clean again):
  - **B-R2-1:** a choice whose runner can't run when it is installed (a governor cool-off, a step too long) now
    counts as run, and an idle breath's `run_s` is spent with it. Before, a guest's Coastal Tides (no canvas or slot,
    so the plan stays stepped) chosen during a cool-off started stepping on the next re-pick once the cool-off
    lifted, against D75's "re-picks never start a run". Tests: the kiosk case under four re-pick sources, and the
    idle-return breath.
  - **B-R2-2:** doc 04 gives the stepped soak's all-writes maximum as it now stands: 82 on seed 0 (80–89 over seeds
    0–15) under the proposed 90.
  - **B-R2-TS-1 / TS-2 / TS-5:** tests that a run ended by held steps settles one cap window (60 s) later, that the
    idle return with `motion.idle` on is one `breathe_run_s` breath, that a guest choosing the idle look mid-breath
    gets the full run, and that a folded, inverted, repeated breathe takes its lags from the plain design.
- **Review round 3 (Oct 19): every confirmed finding fixed**, each with a test and a mutation check (the fixed line
  reverted, its test failing, the fix restored and `git status` showing only the intended changes):
  - **B-R3-1:** a guest's brightness was folded into the next step even when the cap would hold that step back, so
    it waited for the run's end (8.2 s in the reviewer's probe) while the panel drew it. `fold_guest` now checks the
    account's room: a change the cap would hold back goes out at once as its own burst (the room the cap keeps), and a
    step held after all (the window filled in the moments between) sends the folded change then, on the step last
    sent. Tests: the reviewer's probe (out at once, the panel never draws a level the lights weren't sent) and a
    fold the cap then holds (sent at that step, on step 1's colours). With every guest level reaching the lights, a
    breath's step after a held one could have nothing to write and counted as sent, so the guest-mash breath ran on
    under moving copy with every changing step held: **only a step that writes now resets the held count** (D75
    updated; test: a breath whose every changing step is held ends after two).
  - **B-R3-2:** the Lab's Motion hints follow the kind of motion: travel moves the layout; a breathe or shimmer keeps
    it in place (each module's resting colour); a shimmer's lag is the seed's spread, not the design's far end
    (`steppedControls` `designHow` / `lagHow`, swapped by `syncMotionEditor`). Node and static tests.
  - **B-R3-TS-1:** a guest's Coastal Tides run downgraded mid-run and lifted inside `run_s` stays on the modules rung
    with nothing stepping; the finished-run re-pick test now downgrades before its upgrade, so that step can fail.
  - **B-R3-TS-5:** a runner test with sends landing 5 ms after each boundary (as on hardware) and one 429 retry in
    the window: no step held; counting the full 60 s held step 8 (73 + 9 + 9 = 91).
- **Review round 4 (Oct 19): every confirmed finding fixed**, each with a test and a mutation check (the fixed line
  or the pinned branch reverted, its test failing, restored, `git status` showing only the intended changes):
  - **B-R4-C1:** an idle breath on modules carrying only a flagged note (D73; Modules 1–2's D54 note) read "Changing
    the light…" while nothing was sent (8 s on a level breath, 26 s on a lagged one): the runner counted a module
    showing the hold as verified only when it read ready. A flagged-only module with its look confirmed
    (`tracker.flagged_only`) now counts too (D75 notes it). Test: every module flagged, the reconciler's idle breath
    sampled every 0.25 s for 30 s, never `updating` (breathe-level and breathe-rings). Only after Gate R with
    `motion.idle` on; both are off today.
  - **B-R4-2:** this file: #37 is merged (`3c3ad38`); "merge #37 first" stays, marked satisfied.
  - **B-R4-TS-2:** on an approved trio that already breathes (every PR D seed will), changing only the swing, the
    shift or the lag (the colours reached unchanged) gives the generated sentence for "the kind of motion changed"
    alone, and the save accepts it. Kills `_motion_kind` reduced to the mode or missing any one part.
  - **B-R4-TS-3:** a colour-only breathe and shimmer (shift 0.3, swing 0) generate exactly "its next colour" with
    "drifts and settles" / "drift and settle", and apply. Kills each colour-only branch of `_breath_sentence`.
  - **B-R4-TS-6:** the soak report's technique words are pinned in full from the test fixture (D65's "6 s steps
    designed, 90 s runs", and a breathe and a shimmer line), not from config, so `make lab-roundtrip` still holds.
- **Review round 5 (Oct 19): every confirmed finding fixed.** All four were test gaps over code that was already
  right, so no code changed; each new test was mutation-checked (the line it pins reverted, the test failing,
  `git checkout --` and `git status` showing only the test changes):
  - **B-R5-TS-2:** a guest's run that ends on its own, then a Lab publish with a longer run (150 s) as the very first
    re-pick: it only holds, no step. Kills `finish` without `note_run_over` (that re-pick restarted the steps). The
    older test always re-picked through the reconciler first, and `_install` marked the choice itself.
  - **B-R5-TS-3:** the 07 §6 settle test was vacuous: a run ends on its last step (step 14 = cycle step 2), not on
    the hold, so the two "unchanged" modules weren't. Their T1 rows now come from the step sent last, which the test
    checks before the settle, and a second test runs the deferred settle (`_settle_later`, a run the budget ended)
    onto a motion plan. Kills either `if still.motion:` (7 settles instead of 9).
  - **B-R5-TS-4:** a guest's Golden Hour that ran before commissioning held at module level, then lifts to the sun
    once commissioning's re-pick fires (one write per module, no step), under the show's own T1 rule; and a guest's
    Golden Hour chosen during a cool-off shows the sun at once. Each kills one of the two lift paths on its own
    (`_repick_best`'s lift, `_install`'s swap); before, nothing caught removing both.
  - **B-R5-TS-5:** a static test pins that `motionPaint` plays `M.paceSteps(st, tl.pace)` with `levels:
    step.levels`, and that `paintStage` passes a per-head level function through. Kills each of the three reverts,
    including the one that brought back B-R1-3.
- **Review round 6 (Oct 19): every confirmed finding fixed**, each with a test and a mutation check (the fixed or
  pinned line reverted, the test failing, restored, `git status` showing only the intended changes):
  - **B-R6-1:** a lagged breath whose nine starts all came out equal (a shimmer's seed, 183 at lag 1 among 10 of
    seeds 0–1999; or a design whose t rounds to one step at every module centre, a 600 in sun at lag 1) breathed as
    one under "on their own" or "from one edge", and passed the schema, the blocking rules and the Lab's apply. The
    schema now refuses it wherever there is a canopy (`_lag_problems`, through `_stepped_problems`: the load, the
    scene rules, the Lab's apply), on `stepped.seed` or `stepped.lag_steps`; D75 and doc 04 say so. Tests: schema,
    a scene-rules MUTATIONS case, the Lab's apply. **PR D:** a hand-picked seed or lagged design that collapses is
    now refused at load, so `make scene-pins` and the suite catch it.
  - **B-R6-TS-2:** a colour-only breath (shift 0.05) on a sun at r0c0's centre names soft gold to rose, never
    lavender, and the names equal the stops nearest `composer._offset_t` at the nine centres. Kills a wrap in
    `reached_stops`.
  - **B-R6-TS-4:** a guest's 90 s run of the idle look with `motion.idle` every 60 s: one runner to the end, no idle
    breath until it ends on its own, then the breath. Kills removing both run guards (reconciler and engine).
  - **B-R6-TS-5:** a try-on of breathe-level (rung first) cleared by a pause comes back at module level, no runner,
    no program command through the pause and after the resume. Kills `_restore_approved` without `| STILL_ONLY`.
  - Not rerun after round 6 (no JS and no step changed): `LAB_ROUNDTRIP=1` (its breathe case's export was checked to
    apply and load, lags 0, 1, 2) and the browser checks.
- **Verified (sim, macOS), how** (final tree, after the review fixes):
  - `nice -n 15 uv run --offline pytest -q -p no:cacheprovider` (full suite, after round 6): **2607 passed, 6
    skipped, 44 subtests** (11.6 min; 2601 after round 5, 2596 after round 4, 2589 after round 3, 2583 after
    round 2, 2577 after round 1).
  - `node --test tests/*.test.mjs`: 157/157 pass. `ruff check .` and `ruff format --check .`: clean (251 files).
  - After rounds 2, 3, 4 and 6, `python -m conductor.soak` (seed 0) to a scratch path gives the
    committed report line for line (only the generated time and wall time differ), so
    `docs/reports/sim-acceptance.md` stands as committed.
  - `make scene-pins` / `regen_scene_pins.py --check`: up to date (only the two stepped `_pins` gained the new keys).
  - `LAB_ROUNDTRIP=1 pytest tests/test_lab_roundtrip.py`, **all six cases** (canonical, rename, add, remove, reorder,
    breathe: each export applied to a copy of the tree): the whole suite passes on every exported tree (~13 min
    each). The breathe case failed before the review fixes (2 fault-carried steps pinned at 0).
  - `make soak` (seed 0, `docs/reports/sim-acceptance.md` committed; `config/sim/acceptance.yaml` untouched):
    **PASS**. Stepped scenario: all six scenes, all three kinds, 153 steps, 0 superseded/failed/skipped, one PUT per
    changed module per step, at most 72 program PUTs and 82 writes of any kind in 60 s, 9 finished runs settled
    canvas 5 / stepped 4, the idle return still at 180.5 s, no governor trip or downgrade. The main run's settle
    p95 is 4.49 s on this seed (5.19 s before): its random path moved with the cap rule; D72's known cost stands.
    Seeds 0–15 of the stepped scenario: writes in 60 s 80–89, 0 superseded otherwise, 1 carried after a fault
    (seed 6: a 429, then a silent drop). Deltas in D75.
  - Browser, on the simulator only (`scripts/dev_design_lab.py --port 8302`, stopped afterwards; port 8000 never
    touched). From the build: (a) Lab → Golden Hour → Breathe: swing 5 (95 + 5), "Steps per breath", the sentence
    "… swells and softens from one edge: peach, then sunset pink and rose" with lag 2; per-module opacities each
    step. (b) brightness 97 re-clamps the swing to 3; Shimmer shows the seed; Travel zeroes the breath fields.
    (e) Golden Hour chosen on `/kiosk/`: stepped with per-frame `levels`, then the per-head sun. (f) an /ops choice
    of Golden Hour: canvas at once; the rehearsal list includes Lowcountry Calm. After the review: Coastal Tides →
    Breathe (lag 2) at "Show box today" plays the show box's own run ("Step 9 of 15 · every 6 s", the two-strand
    sentence); Shimmer, 3 steps, lag 6, seed 2 at that pace shows the server's refusal, Play disabled, and "Show
    box today (two modules, 6 s): holds still", while "Nine modules" still plays. No page console errors. **Not
    done in the browser:** (c) Save + Export (proven by `tests/test_lab_export.py`) and (d) publishing a breathe
    draft and watching it on `/kiosk/`.
  - **UNVERIFIED:** `tests_e2e/test_kiosk.py::test_a_breathing_run_draws_each_frames_level_as_opacity` (no
    Playwright browser on this Mac; 07 §9).
- **Build deviations from the spec** (all recorded in D75 or the commits): D72's fast back-off no longer defers a
  run's settle (its 120 s after a 90 s run landed past the 180 s idle return, so under 5% throttling Golden Hour never
  reached its sun); the soak's next choice waits for the interrupting mood's own run; Lab tag lab-10 (not lab-9, per
  the build decisions); `stepped.idle_note(settings, scene_id, mode)` takes the scene id (a Mode has none). From the
  review: TS-1 suggested pinning the minimum's rows at 10, 14, 10, 10, which is itself a silent step (B-R1-1's own
  failure), so the test pins the moved centre's 14, 18, 14, 10; B-R1-2 classifies fault-carried steps rather than
  pinning the short soak to fixture modes (it also keeps `make soak`'s landing row from failing on an unlucky
  seed); B-R1-4 has no pytest (the repo has no line-ending policy; a `.gitattributes` is a repo-wide call for
  Alon): `git diff --stat` equals `--stat -w` for both files. B-R3-1 also suggested folding only when no step was
  held just before (`held_steps == 0`); the build checks the account's room alone, the exact test the step will
  make, since after one hold a fold can still carry the level in the step that goes out where a burst of its own
  would likely hold that step and end the run. B-R3-1 brought the held-count rule (a step with nothing to write
  resets nothing), which the guest-mash test needed once folded levels stopped waiting.
- **For Alon's review:** the three 07 edits (§1's writer paths gain the stepped runner, its settle and the idle
  breath; §6's re-trigger line is for on-device motion looks only, `Mode.motion` stays false; §6's kiosk constants:
  an idle return never starts a run unless `motion.idle` is on). `motion.idle` writes on a timer once on (07 §9's
  "no routines that write to real devices"): it is off, yields to any guest, waits for every other holder and goes
  through `tier_allowed`. The runner's cap check now keeps a guest's burst of room (review B-R1-1; the rehearsal's
  confirm says so), which brought the stepped soak's all-writes maximum under the proposed 90 without touching
  `budget_reserve`.
- **Follow-up (B7, D66 kept):** a moving option's Lab try-on stays still at module level; plan §7 wanted it to run
  its steps. Decide in PR E or later.
- **For PR D:** kiosk.js `paintCanopy` now computes each dot's opacity (`moduleOpacity`) and the canopy-preview import
  drops `levelOpacity`: merge with PR A's fin loop per integration C10/C11. Drop the soak's `simulated:` block once
  every scene has its own rung (C9). Rewrite `test_the_idle_look_can_be_rehearsed_with_the_d65_default_rung` when
  Calm has its own rung (C8). `MUTATIONS['breath-repeats-a-step']` sets lag 0 and shift 0 explicitly.
- **Needs real hardware:** bench item 6 (snap or fade, and whether a level step reads as a breath or a blink); the
  account rate probe (`docs/runbooks/account-rate-probe.md`); Gate R on nine (a breathe with `lag_steps: 0` or a
  travel look for the full 72 writes/min; a breathe look to watch level steps); the `motion.idle`
  soak (live_enabled on, then `motion.idle` on, watched on /ops for 30 min, `null` to revert); Alon's PRs for
  `live_enabled` and `motion.idle`.
- **Blocked:** nothing in software.
- **Next step:** review, then open the PR (Alon merges). After it and A and C: PR D's 18 options on this base.

## preset-options PR C `claude/ipad-review-menu` — the iPad review menu, hide and promote (D76) → `main` (Oct 18)
**Merge #37 (`claude/flagged-not-changing`) first:** this branch is cut from its tip (main + #37). Merge commits, not
squash. Plan `docs/plans/preset-options/README.md` §6; decision D76. **Nothing changes on the live iPad while six or
fewer scenes are visible** (today's six): the review menu, the thumbnails and the promote button only appear once PR
D's options are in git. Built and tested over an in-memory 18-scene fixture (`tests/review_fixture.py`: the six plus
`<id>-2`/`-3` options at module level); the real catalog is PR D's.
- **Built:** `Scene.option_of` (one level deep; left out of every dump while unset, so `trio_sha` and published looks
  are byte-identical: `1444f9d4ae93` before and after, checked at step 1); `kiosk.hidden_scenes` with its never-fail ops overlay
  `<data>/overrides/settings.yaml`, the idle scene never hideable; `conductor/ipad_menu.py` (the menu, the /ops
  switch, its "Needs attention" lines, the conductor.yaml fold, card thumbnails); the panel serves only visible
  scenes, `option_of`/`group`/`thumb` and `showing`, and a hidden id gets the calm 404 on both choose routes; /ops
  "On the iPad" (switches, the picker's "(off the iPad)", the overlay download's conductor.yaml hunk); the kiosk
  review menu (grouped rows, pinned side, only `#grid` scrolls, scroll kept across rebuilds and reset on attract,
  still canvas thumbnails at the tier in use, true Now Showing for a hidden scene); published trios and Lab drafts
  can't regroup a scene; the Lab's `option_of`/`group`/`on_ipad`, "Make this the scene" (client-side working-draft
  swap, Undo, no new route) and the export's "Made the scene on site" line, which prompts for the client's sign-off
  and never claims it. Lab cache tag `lab-11`, kiosk tag `review-1`.
- **Review fixes (round 1, Oct 18):** (a) the review cards are tier-true for every scene: `thumb_basis` asks the
  engine for each visible scene's likely plan (`_likely_plan`, as /api/kiosk/modes does), so a modules- or
  stepped-first scene the reconciler has eased down to its strand rung (the idle Lowcountry Calm among them) draws
  no module picture while the ceiling shows one whole-strand look (round 3 makes it draw that look's colour, not
  the palette strip). The test that pinned "options and modules-first
  are never compiled" now pins the opposite, beside a stub test (Calm showing at strand) and a sim-engine test (a
  hysteresis floor at strand). (b) `cardsKey` carries the scene's `brightness_draft` beside the thumbnail's `sig`,
  so a brightness-only publish repaints the card's opacity; a card with no thumbnail (the six-card strip) is keyed
  as before. (c) /ops "On the iPad": a failed load writes its own `#ipad-error` line, which the next fresh answer
  clears; a switch's own answer in `#ipad-result` stays until the next switch. Each fix has a test that fails with
  the fix reverted (checked by hand, then restored).
- **Review fixes (round 2, Oct 18):** (a) **on a partial canopy the cards light only what the engine drives**
  (C-R2-1, D62): every card used to light all nine panels while Now Showing lit two. `thumb_plans` keeps each scene's
  likely plan beside its basis, and `GuestPanel.thumbs` masks the cached nine-position thumbnail to the positions
  that plan drives (`driven_positions`; with no plan, the registered ones), so the seven unregistered panels are
  dark hairlines with `color`/`colors` null and the sig moves with the mask; a mask change never redraws a design.
  Tested on the real engine over the two-kit partial show (`tests/partial_canopy.py`): all 18 cards light r0c0 and
  r0c1, as Now Showing does. (b) **"Make this the scene" says when it copies the option's motion** (C-R2-4): with a
  stepped rung on the base and stepped timing on the option, the button's title, the confirm and the done notice
  now name the stepped direction, steps per cycle, step time and run length (`promoteTakesMotion`, the "took-motion"
  note, `promoteNotes`); D76 and design-lab PLAN §5.2 say so. (c) **the attract screen's six-or-fewer rule has a
  test that can fail** (C-TS-1): six scenes where two share a group, and one group of three, cycle every scene.
  Mutation-checked by hand, then restored: the panel without the mask, `driven_positions` ignoring the plan,
  `mask_thumb` as a no-op, promoteDraft without its note, the title without `{motion}`, lab.js's confirm without
  `{motion}`, and attractScenes without its `<= GRID_MAX` branch each fail at least one new test.
- **Review fixes (round 3, Oct 19):** **a whole-strand card draws that look's one colour, not the palette strip**
  (C-R3-1, C1). A scene whose likely plan is the strand rung (a modules- or stepped-first scene the reconciler's
  floor has eased down) had `thumb: null`, so the kiosk drew the palette strip's 30-colour gradient in the box where
  the other cards show the ceiling, while every strand look in git is one rgb colour; the docs said "none". Now
  `thumb_plans` gives it the basis `"strand"`, and `GuestPanel.thumbs` reads that card from the plan itself
  (`ipad_menu.plan_thumb` over the new `canopy_view.plan_picture`, which shares `lit_state`'s colour rule): the
  strand colour on each driven panel and the others dark (D62), never cached, with no `mode_design` call. A look the
  strand plays on its own (a preset or program; none in git) makes no colour claim, so its card has no thumbnail
  and keeps the strip. D76 and guest-panel.md §2.5 and §4.5 say so. Tests: the basis tests now pin "strand"; a stub
  test (aqua on the two kits equals `lit_state`'s claim, and the sig follows the colour); a preset test; the
  sim-engine floor test (the modes.yaml strand colour on all nine panels, equal to `plan_picture`); and an e2e test
  that the card paints aqua on r0c0/r0c1 and nothing on the undriven panels, while a card with no thumbnail keeps
  the strip. Mutation-checked by hand, then restored: `thumb_plans` giving strand None (4 tests fail), the panel
  ignoring the plan picture (2), `plan_picture` skipping a position with no claim (1), `_claim` ignoring a preset
  (1), and kiosk.js drawing the strip for a strand thumbnail (the e2e test).
- **Verified (sim, macOS, Oct 19, after review round 3):**
  `nice -n 15 uv run --offline pytest -q -p no:cacheprovider tests` → 2566 passed, 5 skipped, 44 subtests (12 min)
  (2564 after round 2, 2560 after round 1; baseline at #37: 2473 passed, 5 skipped); `ruff check` /
  `ruff format --check` clean; `node --test` on each of the ten `tests/*.test.mjs` → fail 0 (guest-panel 32,
  ops-ipad 4, lab-model 89, canopy-preview 10); `scripts/regen_scene_pins.py --check` exit 0 (no scene, mode or
  recipe changed); `pytest tests_e2e --collect-only` → 55 collected (review_fixture imports conductor only).
  **Browser (local only; CI runs no browser; the kiosk review group was re-run after round 3, while
  `tests_e2e/test_ops_visualizer.py` was last run before round 1, so round 1's /ops error line and round 2's promote
  motion copy are proven by pytest, node and static tests only):** 15 kiosk review tests pass against the stubbed panel at 1180×820,
  1024×768 and 820×1180 (rows, CDP touch scroll that chooses nothing, a tap after scrolling, scroll kept across a
  rebuild, painted pixels and alpha at dpr 2, a whole-strand card's one colour beside a card that keeps the strip,
  a quarter-turned view, six options keep the 3 × 2, a hidden scene named in Now Showing, one attract strip per
  group); before round 1, the whole of `tests_e2e/test_ops_visualizer.py` (11, with the new On the iPad switch
  test). Run with the system Chrome (`channel="chrome"`, a scratch plugin: Playwright's own Chromium isn't
  downloaded on this Mac) on port 8303 only.
  **Pre-existing, not PR C's:** `test_panel_ready_changing_cooldown` (`#hdr-spinner` not visible) and
  `test_debug_drawer_is_for_ops_only` fail the same way on the untouched #37 tree with that browser. **Soak** (before
  review round 1; rounds 1–3 change no engine code): `python -m conductor.soak` → the same numbers as the committed
  report (19 PASS; the gating settle row FAIL at p95 5.19 s, as since D72; the two reported spreads FAIL as before);
  the report was restored, not committed.
- **Measured (for PR D):** `catalog_thumbs` over the 18 review scenes, warm: every card per head 72.2 ms / 55,837 B
  of JSON (the worst case); module level 3.4 ms / 8,569 B. `thumb_basis` on the sim engine over 18 visible scenes:
  22–28 ms (niced) since review round 1 compiles every visible scene, as /api/kiosk/modes does (11.0 ms when only
  the three painted bases were compiled; they come out per head at T1 there). It runs on every review-menu panel
  poll (2 s). The spec's read-only baseline on git's six: `mode_design` 22.8 ms, of which 1.0 ms is the modules.
- **UNVERIFIED:** scrolling on the 9th-gen iPad in Guided Access (Chromium's synthesized touch scroll is the closest
  proof); the promote UI in a real Lab (git has no options before PR D: covered by node, static and route tests,
  and a promoted draft passes `apply_drafts` and `POST /api/lab/versions`); the 18-option and fins thumbnail costs;
  the per-poll `thumb_basis` cost on the show box; Playwright's own Chromium (system Chrome used).
- **Needs hardware:** nothing for the logic; the client review session at the venue.
- **Deploy notes:** hard-reload `/ops` and `/ops/showcase` after the deploy (plain StaticFiles). Lab exports made
  before D77 lands propose a reserved D-number; renumber by hand. If C merges before A and B, DECISIONS.md has a
  D74–D75 gap until they land. Merge order per integration: #37, A, B, C (C may merge before D).
- **Decisions recorded in D76:** card thumbnails draw no fins and use a thumbnail dot radius (C12); the base keeps
  its "#1" until PR E or a Lab rename (C27).
- **Questions for Alon:** (1) the review cards draw each scene at the tier the engine would use (per light only for a
  painted scene shown as canvas or slot; the strand look's one colour for one shown as a whole-strand look, round 3;
  module colours otherwise, which on the show box is every other scene until per-light is commissioned), against
  plan §5.4's per-light design on the iPad: keep tier-true cards, or add a labelled design view (new client copy)?
  (2) Once PR E brings the iPad back to six, should those six cards also get tier-true thumbnails instead of palette
  strips?
- **Next:** PR D on a merge of A + B + C. Its verification should include a hand walk-through of promote on
  `make lab-dev` with the 18 options (golden-hour-2 → golden-hour: the button, the swap, the notice, the recipe keys,
  Publish, the export's promotion line, and the motion line on a stepped base) and `catalog_thumbs` /
  `thumb_basis` timings with fins. D also adapts
  `tests/review_fixture.py` and C's six-count tests to the real 18-scene catalog (integration notes).

## PR G — a flagged note no longer holds the panel at "changing" (D73), `claude/flagged-not-changing` → `main` (Oct 18)
**Merged into `main` as #37 (`3c3ad38`, Oct 18 17:14).**
**Deployed first, Oct 18 16:23 (Alon asked): `7b21918`** (main after #35: D70 + D71 + D72) on the Mac show box —
backup `canopy-data-20261018-162349-manual.tar.gz`, previous `0f8f38c` in `~/canopy-prev-sha.txt`, preflight ok,
rc 0, `/healthz` OK, `bench` reconnected in ~2 s, the running config read back `fast_pacing: True, 2 s`. **True on
hardware:** scene taps applied on both kits — ack 0.18–0.36 s, verified 1.34–1.56 s on one kit and ~2.5–2.7 s on the
other (it now waits for its second readback instead of re-sending: D71 working; one Rainbow Row re-send at first);
no throttling; the iPad's socket reconnected by itself. **Found:** Alon still waited ~8–12 s between scenes ("One
moment, the lights are changing"): the next scene got through ~12 s after the previous, 0.17 s after a routine
true-up read. Not the cooldown — D73's bug (below). Also seen: after the restart the iPad's reload got four 401s
before a 200: the kiosk cookie is `SameSite=Strict`, so a navigation iOS treats as cross-site (home-screen icon,
another app, Guided Access re-entry) arrives without it and the box answers the Basic challenge — pre-existing,
worth a decision (Lax would keep top-level navigations signed in; the writes stay JSON + same-site checked).
- **The fix (D73):** a module needing attention only for a flagged note, with its look confirmed, is "stuck" at once
  instead of "changing" for the 10 s retry window; a real readback mismatch still gets its retries.
- **Verified (sim, macOS):** `pytest -q`: 2473 passed, 5 skipped, 44 subtests (full suite, final tree); ruff clean.
- **Deploy:** the usual paste block, on its own; no iPad reload. Then a scene and another ~2 s later should both land.

## PLAN (preset-options PRs A–D built) — three options per scene, motion on every option, fin visualizer, `claude/preset-options-plan` (Oct 18)
Docs only: `docs/plans/preset-options/README.md`, plus the data extracted from VWBD's drawing A-20-1 (`fins.json`,
`lights.json`, `fin-map.png`, `extract/`). Alon's decisions:
- **18 iPad cards ("Scene #1/#2/#3"):** #1 keeps the approved palette, re-placed at module level on the fin rings;
  #2 and #3 get new colours.
- **Every option moves or breathes on the real lights this iteration.**
- **Per-light first,** with a module-level fallback.
- **The fin visualizer is a separate PR.**

What the drawing says:
- **canopy.yaml's lattice is VWBD's sheet rotated 90°** (all 270 lights match). p14 orientation is confirmed;
  routing is `serpentine_rows`.
- **The 650 fins hang 14–24.5″ in a bowl:** 3 rings at module level, 11 per light, a 6″ apron of 104 rim fins, and a
  32° current through the centre.
- **VWBD's P-01…P-09 ≠ our Module 1…9** (P-03 = r0c0, P-06 = r0c1; table in the plan §1.2). Record kit ↔ panel at
  install.

Next: PRs A (fins and visualizer), B (motion for every scene, with Gate R and `live_enabled` on the critical path) and
C (iPad review menu, hide, promote), in parallel. Then D (the 18-option catalog), then E (cleanup after the client
session). Briefs are in plan §10. **Built: preset-options PR A `claude/fin-visualizer` (D74), PR B
`claude/motion-every-scene` (D75), PR C `claude/ipad-review-menu` (D76) and PR D `claude/preset-options-catalog`
(D77, the 18 options)** (sim-verified, not pushed: their blocks at the top). E is not; nothing is verified on
hardware.

## PR F — fast guest pacing: 2 s whatever the size, backing off on real throttling (D72), `claude/guest-throttle-pacing` → `main` (Oct 18)
Cut from `main` at `209e52b` (#31–#34 all merged; checked each branch tip is in `main`). Alon: D70's 7.5 s on nine
modules is cushion — how low can it go? Only ~1 write/s per device is published; the 90/min account cap was a D65
planning number, never measured; the show box has never sent more than 15 writes/min nor been throttled. **The
switch `kiosk.fast_pacing` (on, Alon's call):** every guest input waits only `cooldown_min_s` (2 s), nine modules
included; ≥ 2 write attempts answered 429/403 on an account in 60 s → guests there wait D70's budget gap until 120 s
after the last one (one info alert per back-off; 503/504 never count). `fast_pacing: false` = D70 exactly. **On
today's two kits both give 2 s** — it matters once all nine are driven.
- **Known cost in sim (accepted by Alon):** six soak seeds, nine modules — settle p95 avg 4.89 s (2 s) vs 4.64 s (D70);
  gating rows fail on 4/6 seeds vs 1/6 (seed 3 fails under every setting); a 3 s floor: 4.64 s, 3/6. Only the
  button-mash burst pays: changes overlap the previous one's retries and verification; one heal took 20 s (seed 4).
  `docs/reports/sim-acceptance.md` now reads **FAIL** (seed 0: settle p95 5.19 s). Watch settle times and heal alerts
  in install week; flip the switch off if they slip on the real kits.
- **Verified (sim, macOS):** `nice -n 15 uv run --offline pytest -q` → 2470 passed, 5 skipped, 44 subtests (11 min, final
  tree; `tests/test_soak.py`'s short soak still passes); ruff clean; node 151/151; the soak as above (seeds 0–5 each
  way, plus a 3 s floor). **`tests/test_fast_pacing.py`** (8 tests): on by default and 2 s for 0, 2 and 9 modules;
  off = D70 exactly (2 / 2.5 / 7.5 s); one throttle doesn't back off, two in a minute (a 429 and a 403) do — 7.5 s
  on nine, held 120 s after the last, one alert, then 2 s again; 503/504 and two throttles 61 s apart never back off;
  off, throttles change nothing and alert nothing; end to end, real simulator 429s on a guest's burst make the next
  guest wait 7.5 s; a minute of mashing every control on nine modules gets an input through every ≥ 2 s and every
  device still ≤ 1 write/s; two strands give 2 s either way. D70's tests run with the switch off
  (`tests/test_guest_budget.py`, two older cooldown tests pinned to it). **Mutation-checked:** ignoring the switch →
  5 of 8 fail; not counting throttles → 2 of 8 fail.
- **The first real account number:** `docs/runbooks/account-rate-probe.md` (Alon runs it on the Mac: both kits at 1
  write/s for 3 min ≈ 120 writes/min on `bench`; throttled → `fast_pacing: false`).
- **Deploy:** the usual paste block, on its own (no iPad reload). Reversing it: set `kiosk.fast_pacing: false` in
  `config/conductor.yaml` through a PR (or flip it in the show checkout's config and `canopy-mac install`).

## PR E — the first readback's echo lag (D71), `claude/readback-echo-lag` → `main` (Oct 18)
Cut from `main` at `0f8f38c` (after #31), with `main` merged in after #33 (PR D below, which took D70); stands alone. The findings are in
`docs/reports/readback-echo-lag-2026-10-18.md`, from a read-only pass over the live telemetry (Oct 17–18, Modules 1–2).
- **Found:** about 1.2 s after the ack, the first verification GET still shows the previous scene's brightness
  and/or colour on 33 of 56 scene changes and both guest brightness changes. That is cloud echo lag, not a clamp:
  identical re-sends read back right, and the same transitions sometimes read fresh. The engine's early re-send cut
  every one of those 35 commands short (no command got its own 2nd readback), so each cost an extra PUT and ~2.75 s
  to "ready". On Oct 17, back-to-back re-sends stayed stale up to 3 times (5.7 s).
- **Changed (sim-tested):** a change's first early re-send waits for the command's 2nd readback that still differs;
  later re-sends in the same change go on the first readback, as before (3 drops in a row still heal ≤ 10 s).
  Readback telemetry now carries `n` and `differs` (sent vs read, non-slot rows).
- **Verified (sim, macOS):** `nice -n 15 uv run pytest -q` → 2453 passed, 5 skipped, 44 subtests (11 min); ruff
  check / format clean. New `tests/test_readback_echo_lag.py`: with device-side readback lagging 1.3–1.6 s, a 60 → 70
  scene change costs one PUT per module, no early re-send, first GET stale, second matched, and `differs` reads
  `sent 70, read 60`. **Mutation-checked:** with the threshold back at 1, it fails (9 re-sends). The 3-drops-in-a-row
  self-heal test is what forced "later re-sends go on the first readback" (11.0 s without it). The stepped-drop test
  now expects a re-send 2.0–3.6 s after a drop (was ≤ 2.6 s). The live numbers come from read-only scripts over the
  show box's telemetry; nothing touched `~/canopy-show` or a device.
- **With PR D's guest gap (D70):** the gap assumes one write per driven module; with this PR the usual stale first
  readback no longer adds a second one. On two kits the gap is 2 s, so a guest's next tap can land before a module's
  2nd readback (≈ ack + 2.2 s): that command then ends superseded and the newer one verifies itself, as with any
  quick second tap. Nothing is re-sent for the older one.
- **Needs hardware (bench step in the report):** confirm `read` = the old scene's value on live scene changes;
  measure the echo lag by polling GET every 0.25 s; shorten `readback_interval_s` only if p95 < 1.5 s.
- **Deploy:** Alon's call, after merging (the usual paste block). Nothing was deployed or written to a device.

## PR D — guests paced by the write budget, not a fixed 8 s (D70), `claude/guest-budget` → `main` (Oct 18)
**Merge #32 (`claude/panel-latency-deploy`, STATUS only) first** — this branch carries it (a merge commit), so its
STATUS edits sit on #32's. Alon: is the 8 s between presets required by the API, and can it be cut for everything?
Not an API rule (Afero publishes none; ~1 write/s per device is the only guidance, and the account cap is our
unmeasured 90/min). Now the gap after a guest input (mood, brightness, on/off) = one write per driven module (most on
one account) × 60 / 72, never under 2 s: **2 s on today's two kits, 7.5 s on all nine.** Ops bypass it; the Lab's
try-on keeps its 8 s gap (`lab.apply_min_gap_s`); the soak keeps its seeded schedule. Settings: `kiosk.cooldown_min_s`
(2), `kiosk.guest_writes_per_min` (72, capped by `motion.stepped.account_writes_per_min`); `kiosk.cooldown_s` is gone
(the show box has no settings overlay that sets it). 07 §6's kiosk-constant line is rewritten for Alon's review.
- **Verified (sim, macOS):** `nice -n 15 uv run --offline pytest -q` → 2458 passed, 5 skipped, 44 subtests (11 min, on the
  final tree); ruff check / format clean; `node --test tests/*.test.mjs` 151/151; `python -m conductor.soak` exit 0,
  21 PASS rows, the only FAIL the non-gating D65 stepped spread (p95 2.78 s, as before); the mash now accepts a few
  more kiosk taps (38 refused, at a 7.5 s gap) and every row stays inside its target. **`tests/test_guest_budget.py`**
  (8 tests): the gap is 2 s for 0–2 modules, 2.5 s for 3, 7.5 s for 9; the account carrying the most modules sets it
  (5 + 4 → 4.17 s); a lower account cap lowers it (cap 40 → 13.5 s) and the floor is a floor; on the engine (virtual
  time) the next guest choice is refused 0.3 s before the gap and accepted 0.05 s after it, ops never wait, for two
  strands (2 s) and nine (7.5 s); brightness and on/off buy the same 2 s; two minutes of a guest hitting every control
  every 0.25 s on nine modules: accepted inputs ≥ 7.5 s apart and the account ≤ 90 writes in any 60 s; the Lab keeps
  8 s. Pinned-8 s tests updated (5.5 s left 2 s into a 7.5 s gap). **Mutation-checked:** `guest_gap_s` returning a
  fixed 8 s → 6 of the 8 fail. **Browser** (sim dev server, port 8200): a scene on the nine-module simulator →
  `cooldown_remaining_s` 7.496; a second scene 0.4 s later → 429, retry 7.09 s.
- **Deploy:** the usual paste block, on its own; no iPad reload needed (no page change).
- **Needs the real box:** on the two kits, a scene then another 2 s later — both land and the chip counts 2 s; the
  account's real cap (Gate R) before all nine kits run at 7.5 s.

## PR C — input to reaction on the guest panel (D69), `claude/panel-latency` → `main` (Oct 18)
**Merged (#31) and deployed Oct 18 12:06 on the Mac show box: `0f8f38c`** (Alon asked; the PR's paste block run from
this session: backup `canopy-data-20261018-120607-manual.tar.gz`, previous sha `96b31ad` in `~/canopy-prev-sha.txt`,
preflight ok, `canopy-mac install` rc 0, `/healthz` OK, `bench` reconnected with 12 devices within seconds; `canopy`
not connected, as before). **True on hardware (Modules 1–2, telemetry + the Conductor log):**
- PUT acks 0.18–0.34 s; readback-verified 1.35–1.45 s after send (the 1.0 s `readback_delay_s` + a ~0.15 s GET).
- The background token refresh fired every ~90 s (12:07:55, 12:09:26, 12:10:56, all ok), and every guest burst's
  own check found the bearer fresh (`ensure_fresh` 0.0 s): no burst waited on Keycloak.
- The iPad loaded the new page (`kiosk.js?v=latency-1` and its three modules at the same tag) and its live socket was
  accepted (`WebSocket /ws/live`); three scene taps (Rainbow Row Sunset, Garden Gathering, Golden Hour) applied.
- **The deploy's first iPad reload broke the panel briefly (my bug):** the iPad ran its cached *old* kiosk.js, whose
  unversioned import of `/kiosk/panel-model.mjs` fetched the *new* module, which no longer exported `createPacer` — a
  missing ES-module export stops the page. The version tags only guard the other mix. Loading the panel again on its
  own address picked up the whole new version. (A `/kiosk/` on the Mac's IP got 401: the kiosk cookie belongs to the
  address the iPad was enrolled on — give the iPad's own address, never the IP, in a reload instruction.) Any other
  browser that had `/kiosk/` cached before Oct 18 can hit the same break once; `/kiosk/?r=1` fixes it. From now on
  everything under `/kiosk/` and `/static/` is `no-cache`, so this can't recur — but **never drop an export the
  kiosk's modules had without bumping the `?v=` tag in index.html and kiosk.js together.**
- **Not tested live:** a brightness or on/off change inside the cooldown (Alon stopped the test before the slider
  part); it is covered in sim (`tests/test_panel_latency.py`, browser check).
- **Seen, pre-existing (not D69):** on these kits the first verification readback after a PUT often still shows the
  old `brightness` (66 times on Oct 18, most before this deploy), so the reconciler re-sends at once and "ready" comes
  at ~2.75 s instead of ~1.4 s, with one extra write per kit. Worth a bench check: does a second GET (no re-send)
  match? If the cloud only echoes brightness late, waiting for it would save the write and ~1.3 s.
Alon: "minimize delay between input and reaction on the panels". Cut from `main` at `96b31ad` (after #28–#30); one
PR, merge commit. Measured on the simulator first (`make lab-dev`, port 8200): a tap is answered in ~7 ms and the
burst goes out without waiting; the delay was in the panel and in how guest input met the engine's cooldown.
- **Fixed — brightness/power inside the cooldown were silently dropped.** The panel got 200, then the engine refused
  the lane's job (shared 8 s cooldown, D60): the slider snapped back after 3 s and the lights never took the level.
  Now the change waits for the cooldown's end and goes out (latest wins, one burst; the budget holds), with true copy
  ("Your brightness is next. It reaches the lights in a moment.") and `state.next` keeping the controls in place. The
  slider sends **once, on release** (its first-touch send used to start the cooldown with the wrong level).
- **The panel reads its state on each server push** (the `canopy` WebSocket topic as a nudge; the 2 s poll stays the
  fallback); single-flight reads; 6 s/10 s timeouts so a hung request can't freeze it. Tap → "ready" on the panel:
  2.0–3.9 s (avg ~2.8) before, ~2.2 s after (sim; the rest is the readback, `readback_delay_s` 1.0, unchanged).
- **Delivery:** `/kiosk/*` and `/static/*` now `Cache-Control: no-cache`; the kiosk's modules share one `?v=` tag
  (bump index.html's and kiosk.js's together).
- **Live path:** the AferoDriver refreshes a live account's bearer in the background at < 30 s left, so a guest
  burst never waits on Keycloak first (was ~13% of bursts); a newer command no longer waits for an older command's
  readback GET on the wire (left to finish, never cancelled); redaction is indexed (~0.63 → ~0.014 ms a call after
  12 days of token rotation, same output); the preview skips its layout on animation frames.
- **Verified (sim, macOS):** `nice -n 15 uv run --offline pytest -q` → 2450 passed, 5 skipped, 44 subtests (11 min; the
  redaction strategy pick and its long-text cases landed after it: `tests/test_panel_latency.py` + the redaction and
  credential-canary suites re-run, 50 passed); ruff check / format clean; `node --test tests/*.test.mjs` 151/151;
  `python -m conductor.soak` exit 0, every gating row PASS, the only FAIL the non-gating D65 stepped spread (p95
  2.78 s, as before); mode change settled p95 3.49 → 3.40 s, the 1.2 s ripple's settled p95 5.53 → 5.13 s and ring
  spread p95 1.98 → 1.80 s (the executor change; report updated). **`tests/test_panel_latency.py`** (16 tests): the
  lane holds a change through the cooldown and runs only the latest, once, at its end; a plain 1 s gap is not
  "held"; a broken hold never blocks; closing a waiting lane is immediate; on the real app (cooldown shortened to
  2 s) three slider levels inside the cooldown → one engine call with the last, none refused, landed at the
  cooldown's end, the panel saying `cooldown` + the "next" message + `state.next` meanwhile; switching off likewise;
  the token keeper refreshes at < 30 s so a burst's check sends nothing, leaves a fresh bearer alone, never in sim
  mode or for an account needing a sign-in, retries a failure only after 30 s; a newer command goes out at the
  budget while the older readback GET (3 s) is on the wire, which finishes uncancelled; redaction equals the old
  pass on 2,050 random texts (both paths) and costs the same at 50 and 8,800 secrets; `/kiosk/*` and `/static/*`
  answer `no-cache` and 304 on their ETag; kiosk.js's imports carry index.html's version. **Mutation-checked:** the
  lane without `hold=` → both app tests fail; the keeper without `_refresh_ahead` → its test fails; the executor
  reading without `_read_unless_newer` → its test fails. **Browser** (sim dev server, port 8200, in-app browser): the
  new page loads its modules at `?v=latency-1`, fetches a WS ticket after the first read, no console errors; tap →
  card settled 2.17–2.21 s (3 taps) vs the old cached page 2.01–3.95 s (4 taps); a scene tap then a slider drag sent
  exactly one brightness POST (on release), the header said "Your brightness is next…" with the knob held at 24, and
  at the cooldown's end the canopy read back 24. **Not run here:** `make e2e` (Playwright's browser isn't installed
  on this Mac; its slider test is updated to expect one send on release — UNVERIFIED locally).
- **Deploy (Alon, after merging; on its own):** the PR A paste block (backup, checkout the merge sha, `canopy-mac
  install`, rc 3 rollback). Then reload the guest iPad **once at `/kiosk/?r=1`** (leave Guided Access, open that
  URL, Guided Access on): the iPad cached the old page without `no-cache`, so a plain reload may keep the old code
  for a while (harmless — the old page works against the new server — but without the fixes). From then on a reload
  always picks up a deploy.
- **Needs the real box:** the real PUT-to-light time on Modules 1–2; that the iPad (WebKit) keeps the live socket
  (ticket flow, 07 §2) — if it can't, the panel falls back to the 2 s poll on its own; a guest dimming right after a
  scene on the kits (the level lands when the cooldown ends); how often a burst hit the inline token refresh before
  (telemetry `token` events: `ensure_fresh` with a non-zero `elapsed_s` should become rare, `refresh_ahead` ~every
  90 s per account).

## PR B2 — the operator repaint (D68), `claude/live-lab-repaint` → `main` (built Oct 17; plan `docs/live-lab/PLAN.md` §2.6–§2.8, §5.2 B2)
**Merge PR B first ("merge #B first"); B2 targets `main` too; merge commits, not squash. Deploy B2 only after bench
item 6 (rewriting the showing slot, then the re-select) has been run once on a show kit with its own Gate C (from
`/ops/module`).** Built on `claude/live-lab` at `c32f3f0` (B1 + U + R integrated); the stream's commit `1803274` is
merged into `claude/live-lab-repaint` (a clean merge: B2 was cut from `claude/live-lab`'s head).
- **Integration check (Oct 17, macOS, sim, after the merge):** `nice -n 15 uv run --offline pytest -q` → 2354 passed,
  5 skipped, 44 subtests (10 m 6 s); ruff check / format clean; `node --test tests/*.test.mjs` 144/144. **Smoke on a
  sim dev server** (`scripts/dev_design_lab.py --port 8215`, throwaway data, killed after): the start-up commissioning
  wrote 27 slots, the catalog then said sentence (c) "…repaints custom-3 on 9 modules…". (1) Golden Hour, one palette
  stop changed → 200, the confirm listed only the 3 modules whose compiled slot differs (Modules 3, 6, 9: the others'
  records already held the design), 3 slot writes (`publish repaint: Golden Hour → custom-3`, one per module, all
  applied) in 2.5 s, the info alert. (2) A second publish 2 s later → 200 at once, "in 118 s", held, `queued_until` =
  the first start + 120 s, no slot write. (3) Revert to approved 3 s after that → 200 at once (never blocked), it
  replaced the queued job (latest wins, due time kept); at the due time 3 writes (Modules 3, 6, 9: git's design over
  what (1) wrote), then held false. (4) After the gate, a layout change shown on the canopy (`show: true`): 9 module
  writes now (held), the repaint's 9 slot writes (one per module), guest taps every 0.4 s during the window all 503
  "One moment: the canopy is changing." (kiosk state `updating` with the same copy), the history row `applied: 9`. (5)
  Again with no guest taps: after the repaint, exactly one T1 burst (`golden-hour` at `canvas`, 9 commands; telemetry
  source "reconciler", the capability re-pick). In (4) a guest tap landed 0.3 s after the window and took the canopy
  (coastal-tides) before the re-pick — a guest choice, so no T1 burst for Golden Hour then. Two small things seen,
  not B2 regressions: the window's retry-after said 12–14 s for a 5 s window (an upper bound), and with `show: true`
  the confirm still says "Nothing on the canopy changes now (no changed scene is showing)" while the cost line counts
  the 9 writes (B1's sentence, unchanged by B2; the Lab shows the "Show <scene> on the canopy" checkbox beside it).
- **Review 5 (Oct 18, 9 findings, all fixed; `tests/test_lab_repaint_review.py`, 21 tests, each failing before its
  fix or on its mutant).** First merged the fixed `claude/live-lab` (review 4) in: conflicts in `canopy.py`,
  `publish.py` (the confirm takes both `show` and B2's `tokens`; the queued map and the fallback's no-Undo both go
  into the new file) and `tests/live_lab.py`; review 4's rolled-back-Revert test keeps the publish's repaint from
  running so its premise stands. Then: **B2-1** the persisted gate and queue reached the engine only once every module
  was read — with one module unreadable at boot a publish repainted inside the gate, the reconciler rewrote the queued
  slots 36–41 s after the last repaint, and a publish dropped the queued scene. Now `ShowWiring._restore_repaints`
  runs right after `engine.start`: the gate seeded, the queued scenes started at their due times over every module
  with an api record for the slot (pending at once, shown queued), a publish carries them over (`repaint_jobs` keeps
  a queued job's unread modules), and a window opens only after `repaint_ready` (the first capability round; in sim
  the start-up commissioning, which cancels a restored job first); the window's target is recomputed then, an unread
  module is left to the reconciler, and with nothing judgeable the repaint "didn't start (the Hubspace profile of N
  modules hadn't been read yet)" — never "already holds". No history row for a repaint whose slots already held the
  design. **B2-2** a job's scenes now run in due order (stable). **B2-3 / UI-3** StateJSON.engine gains
  `repaint_quiet` (and `commissioning` is only a real one); ops' Re-assert all answers 409 "A publish is repainting a
  painted scene's slot right now (about N s left)…" + Retry-After; `reassert_all` refuses in the window and while
  commissioning; the try-on panel's `TRIAL_COPY.repainting` (`?v=lab-8`). **UI-6** sentences (b)/(c) say "up to" /
  "those … whose design changes". **Mutation gaps** Q3, Q5, Q7, Q30, Q31, S2, P3, L3 — tests added; 16 mutants of
  the fixes and those guards (`scratchpad` runner) each killed by its named test. **Verified (macOS, sim, Oct 18):**
  `nice -n 15 uv run --offline pytest -q` → 2434 passed, 5 skipped, 44 subtests (10 m 48 s); ruff check / format
  clean; `node --test tests/*.test.mjs` 150/150. Not run: a browser walk of the new try-on reason or a sim dev
  server smoke of the restart (the restart paths are covered on the virtual clock with the simulator's devices kept).
- **What it does.** A publish of a new painted design no longer needs the reconciler: the scene installs at once,
  held below T1 (truthful module level), and the engine gets ONE repaint job (`CanopyEngine.start_repaint`,
  `RepaintJob`s): the scenes whose eligible modules — their own api record, Gate C at their address and, on a longer
  frame, a valid map (`ShowWiring.repaint_plan`, per module; one that can't take T1 drops out alone) — hold another
  design, plus a queued job's scenes it replaces. Its (device, slot) pairs are pending from that moment (the hold
  follows them; `reconciler.check_slots` skips them — the only reconciler.py edit). When a scene comes due: refused
  (its marks cleared, the scene stays held) if the engine is stopped, commissioning, resting or rehearsing; else a
  **quiet window** (`quiet`, which the public `commissioning` property returns: reconciler tick/re-assert/idle
  hold/reset, the stepped runner and the capability re-pick hold still; guest, ops, try-on and Gate R requests are
  refused with "One moment: the canopy is changing." + retry-after — the kiosk routes and the guest panel say the same,
  503 / `waiting: "changing"`; a running try-on ends first), `wait_idle` for the commands in flight, the target
  recomputed from the live looks, then `_commission_plan(min_interval=0, only=listed, verb="publish repaint",
  locked=True)`: per module under its `_slot_lock` (the modules concurrently), header from the readback, one slot
  write through the executor, `slot_written` stamped at submit, at most two retries only when it didn't land, never
  over an app record or a module held by the module page, the readback recorded exactly like commissioning (the
  registry overlay). The scene's `finally` clears its marks and the window; fully applied → the hold lifts → one T1
  re-pick (the re-select; also sent when a showing T1 scene wasn't held). Commissioning is refused while a job is
  queued or running; a publish only while one is writing (Lab sentence "<Scene>'s slots are being repainted (about N s
  left).").
- **The 2-min gate** (`lab.repaint_min_interval_s: 120`, `config/conductor.yaml`, validated 60–600): enforced inside
  `start_repaint` (monotonic, from the start of the scene's last operator writing; never later than one interval
  from now); a sooner publish installs and queues (never 409); a newer publish replaces a queued job (marks cancelled
  with it; the due time stays); Revert and Undo queue the same way. The overlay keeps `repaints` (the gate) and
  `queued` (the job, written with the publication); the engine's hook (`looks.live.note_repaint`, no history copy)
  writes the gate when a scene starts, drops the scene from the queue and fills its history row's `repaint` counts
  when it ends. After a restart the gate and the queue go back to the engine as it starts, before any profile is
  read (review 5, below; in sim the start-up commissioning, which writes the live design, replaces the restored job
  and the queue settles), the gate seeded from the wall times clamped to one interval (a ±1 h clock step can't
  stretch or skip it).
- **The Lab's view.** The confirm adds "<Scene>'s painted design is repainted into custom-3 on N modules<, in S s>
  (one slot write each)." and the cost "repaints custom-3 on N modules: up to 3N slot writes (one each unless a write
  doesn't land), N reads, then one T1 burst"; a confirm shown earlier still matches while its "in S s" ticks down (by
  up to one interval: the number is then an upper bound). `confirm_required.repaint` = [{scene, slot, modules
  (labels), writes, in_s}], `PublishReport.repaint` = [{scene, slot, modules, in_s}], `LiveJSON.repaint` = the
  RepaintJSON U renders (`running`, `queued_until`, `started_at`, `finished_at`, per-module status, per module
  updated), `state.show_looks.repainting`, RepaintInfo's §2.6 count sentences (a)/(b)/(c) and `available` false only
  under the app lock (B1's api lock and its preview warning are gone; `lab-model.mjs`'s B1 note no longer shows under
  the app lock — no `?v=` bump, so a cached page may show it until a reload). Telemetry `lab repaint`; alerts keyed
  `looks:repaint:<mode>` (info "…'s painted design is in custom-3 on N modules." / warn "…: custom-3 repainted on k of
  N modules (…); it shows at module level until the rest are current (the reconciler retries after 10 min).").
- **Contract text:** 07 §6's "Operator repaint (D68)" paragraph (§6.2 of the plan, verbatim) and §1's writer list
  ("and the Lab's publish repaint"); D68's PR B2 bullet. New writer path `WRITER_PATHS["lab publish (slot
  repaint)"]` (denied on the wire for a non-allowlisted id; reaches it for the bench kit).
- **Verified (macOS, sim, virtual clock):** `nice -n 15 uv run --offline pytest -q` → 2354 passed, 5 skipped, 44 subtests (10 m 3 s); `uv run --offline ruff
  check . && uv run --offline ruff format --check .` clean; `node --test tests/*.test.mjs` → 144/144.
  `tests/test_lab_repaint.py` (20 tests): one slot write per module (9), ≥ 1 s between writes to any device with the
  simulator's per-device 1 s budget on (0 violations), records = the live compile, `stale_slot` None right after and
  after 30 virtual minutes with no reconciler rewrite, no stale/"waiting" alert, then exactly one T1 burst; the quiet
  window (guest taps every 0.5 s all refused with the sentence and a retry-after, the kiosk route 503 with it, an
  overdue idle return held, Gate R and a try-on refused, the re-pick burst over before the first header read, no
  SUPERSEDED slot result); a MISMATCH on a pending module starts no rewrite; a failed module (3 attempts, stamped at
  the last, the reconciler's rewrite once ≥ 600 s later, held until then); partly commissioned (5 of 9 + one with a
  bad map: 5 writes, sentence (b), a sixth commissioned later gets the published design); a module held by the module
  page (locked, then the reconciler); an app record never written; the gate (republish at 60 s → 200, held, queued
  `in_s` ≈ 60, no slot write before 120 s, nine at 120 s; Revert and Undo inside the gate → 200; brightness only → no
  repaint); restart mid-queue with a ±1 h wall step; the simulator's restart settling the queue; refusal at job start
  (outage); two painted scenes as one job in sequence; commissioning refused while queued; a publish refused only
  while writing; the confirm's ticking wait; a newer job cancelling a queued one; a newer install aborting a queued
  job; the slot lock against a running reconciler rewrite; a module whose record already holds the design isn't
  written. B1's tests updated where B2 changes the answer (the hold test now ends with the repaint, the hardware
  api-lock test became "no Gate C → no slot write", the RepaintJSON fixtures `live_repainting.json`/`repaint.json`
  are now checked against the real answer, during and after). **Mutation checks (16, each killed by the named
  test):** the `check_slots` pending guard; `commissioning` → `quiet`; `wait_idle` before the first header read; the
  `_slot_lock` (`locked=True`); the submit-time `slot_written` stamp; `min_interval=0` (→ 600 s); the `only` filter;
  the gate clamp (its three layers together: the restart seed, `start_repaint`'s bound, the requeue's wait — the
  restart seed alone is NOT covered by the others when nothing is queued: review 5's S2 test covers it); `start_repaint`'s own due enforcement; `commission`'s refusal while repainting; the
  generation abort; `looks_refusal`'s "repainting"; the marks set in `start_repaint`; the kiosk's copy in the window;
  both requeue paths (show box, simulator).
- **Notes for review (deviations from the plan's letter).** (1) `start_repaint(jobs: Sequence[RepaintJob], *,
  generation, publication)` — the due times ride in the jobs. (2) The job's task is not one of the engine's burst
  tasks, so `wait_idle` never waits for a queued repaint (`wait_repaint()` does; `stop()` cancels it). (3) The publish
  calls `start_repaint` **before** `reload_looks` (both in the one synchronous section), so the re-pick's hold already
  follows the new marks and a Revert whose slots already hold git's design lifts at once with one burst. (4) The
  confirm's "in S s" is matched with slack (as the try-on's re-apply sentence). (5) Partly commissioned sentence (b)
  lists the module labels comma-separated ("Module 1, Module 2, …"), not "Modules 1–5". (6) The repaint confirm
  names the scene as the show runs it now (B1's hold note does the same).
- **Needs the show box / real hardware:** bench item 6 once on a show kit with its own Gate C (the first operator
  repaint rewrites the showing slot, then the re-select burst); the first repaint on the show kits with `/ops` open
  (writes in 60 s, any 429/403); a republish inside 2 min on the lights (queued, held at module level, then
  repainted). On today's two kits (no Gate C, nothing commissioned) a painted design publishes with no slot write
  (tested in sim with hardware rules). `nice -n 15 uv run --offline python -m conductor.soak` → exit 0, 21 PASS rows,
  the only FAIL the non-gating "(reported)" D65 landing spread (p95 2.78 s), as on PR A/B (`sim-acceptance.md` only
  changed its timestamp, so it was reverted). **Not run:** `make lab-roundtrip` (the export path is untouched by B2)
  and the browser walk of §5.3 B2 (repaint progress 9/9, the queued countdown) — the JSON the page reads is checked
  against U's fixtures instead.

## PR B — the live Lab (D68), `claude/live-lab` → `main` (built Oct 17; plan `docs/live-lab/PLAN.md` §2–§3)
Alon, Oct 17: "make the test lab work on the live system so that I can just think of everything as one system".
**Merge PR A (`claude/canopy-only`) first; PR B targets `main` too (memory: target main, not stacked bases); merge
commits, not squash.** `claude/live-lab` = PR A + stream P's B1 (server) + stream U (the Lab page), merged in that
order. Not in PR B: the operator slot repaint (B2, `claude/live-lab-repaint`, after bench item 6 on a show kit).
- **Server (B1).** `conductor/looks/live.py`: the `overrides/show-looks.yaml` overlay (load with alias/size/depth
  refusal and a broad fallback that never fails the start and never moves the file, `validate_trio`,
  `install_looks` = the §2.4 checklist; `config_sha`/`ConfigTrio` moved here and re-exported by the Lab),
  `build_live_looks` in the lifespan right after the AlertBoard, the engine seams (`LOOKS_SOURCE` in
  `RECOMPILE_SOURCES`, `looks_refusal`, `reload_looks`, the `_t1_hold` skipped in `compile_best`, lifted by a slot
  record, seeded at every capability apply; the refused-re-pick safe look), `ShowWiring.recipes`/`install_looks`/
  `repaint_plan`, and every live reader on the live trio (kiosk modes incl. the per-mode `pattern` specs, the guest
  panel catalog, ops `mode_list`, alert labels, the design route, the module page and its open sessions, the Lab's
  try-on, catalog and state). `conductor/design_lab/publish.py`: publish / Revert / Undo, the refusal matrix (Lab
  sentences), the confirm sentence + cost, the `expected_live` + generation conflict guard, one synchronous section
  (checks → write → install → re-pick), rollback. Routes `GET /api/lab/live`, `POST /api/lab/publish`; DELETE of the
  live version → 409. The D66 T1 lock became the **app lock** (a design change over a hand-painted slot is refused
  everywhere) plus, **on hardware only**, the B1 api lock at publish (Revert/Undo exempt). **In the simulator a
  painted design change publishes** (held at module level, then the reconciler's stale rewrite), as D66's lock was
  off there — a deviation from the plan's §5.2 wording, recorded in D68 (one condition in `plan_publish` if Alon wants
  the sim locked too).
- **The Lab page (U).** `lab.js` / `lab-model.mjs` / `lab.css` (`?v=lab-6`; `lab-7` after review 4): the show bar (live / published,
  simulator vs hardware, the fallback line, held scenes, git-changed), the two badges per scene (Live: Approved /
  Published, and Live / Draft / In try-on against the **live** look), working drafts seeded from the live look,
  Publish to the show / Save & publish / Live now, the confirm dialog with the server's sentence verbatim and the
  publisher's name, Undo last publish, Revert the show to approved, History, the conflict re-ask, Download version /
  Open a version file, the 5 s poll. It degrades readably on a server without the live routes.
- **Integration glue (lead).** `conductor.preflight` gained the read-only published-looks check
  (`looks.live.preflight_line`): a file the new code can't use is **named, not fatal** (the show would start on the
  approved looks with an /ops alert), so `canopy-mac install` still restarts; test
  `test_live_looks.py::test_conductor_preflight_names_the_published_looks_and_never_fails_or_writes_for_them`
  (mutation: the check dropped from `CHECKS` → it fails). `tests/test_live_lab_routes.py` now also checks **U's**
  fixtures (`live_*.json`, `publish_*.json`, `catalog_additions.json`, `state_additions.json`, `live_fallback.json`)
  against the real answers — every key the page reads is there with its type. Two known differences, both harmless:
  a publish answer's `state` is the engine's CanopyState (as in the try-on answers; the page only takes a Lab state
  that has `engine`, so it ignores it and reads `/api/lab/state`), and the layout example is the radial union arm.
  `guest_panel.py` lost its now-unused `pathlib` import. Merge conflicts: 05 #3 (both edits kept), DECISIONS (D67
  then D68), this file (B above A), the Studio's `index.html` and the two version pins (R's `studio.js?v=screen-1`,
  U's `lab.js?v=lab-6`), `tests/fixtures_live_lab/repaint_info.json` (B1's, the one checked against the real answer).
- **Verified (lead, macOS, sim, on the merged `claude/live-lab`):** `nice -n 15 uv run --offline pytest -q` (no
  `--ignore`) → 2333 passed, 5 skipped, 44 subtests (9 m 45 s); `ruff check` + `ruff format --check` clean; `node --test tests/*.test.mjs` → 144/144.
  **Smoke on a dev server** (`scripts/dev_design_lab.py --port 8213`, throwaway `--data`, killed afterwards; port
  8000 untouched): `GET /api/lab/live` (approved, simulated); Golden Hour showing at T1; a Golden Hour colour edit
  (Peach `#FFB69D → #FF7A4D`) saved as v0001 through `POST /api/lab/versions`; `POST /api/lab/publish` → 409
  `confirm_required` with the simulator sentence and cost → confirmed → 200 `p0001`, 9 writes, Golden Hour held;
  `/api/canopy/state` then showed Golden Hour at module level (T1 held; the colours per module are in
  `/api/canopy/heads` and `/api/kiosk/modes`' `module_colours`, which now carry the new peach), the guest panel's
  catalog and `/api/kiosk/modes`' preview had `#FF7A4D`; **restart over the same data dir** → `source: published`,
  `p0001`, the kiosk catalog still `#FF7A4D`, T1 again (the sim recommissions from the live trio), no /ops problem;
  **a hand-corrupted `show-looks.yaml`** (truncated + bad YAML) → the server started, `/healthz` 200, `fallback:
  true` with the YAML reason, the kiosk back on git's `#FFB69D`, the /ops "Needs attention" line and the warn alert,
  the file's bytes unchanged. Then in the Lab page (browser, 1440×900) against that same real server: the red
  fallback line, Publish to this simulator → the server's sentence in the dialog → published (the old file kept as a
  history copy, the /ops line gone), Revert this simulator to approved, Undo last publish — no page errors.
  `nice -n 15 uv run --offline python -m conductor.soak` → exit 0, 21 PASS rows; the only FAIL is the non-gating
  "(reported)" D65 landing spread (p95 2.78 s), as on #27 and PR A (`docs/reports/sim-acceptance.md` only changed its
  timestamp, so it was reverted). **Not run:** `make lab-roundtrip` (five full suites, ~50 min, too heavy on the live
  box tonight; the export path changed by one sentence, whose pin B1 updated) and the e2e suite (no Playwright
  browser here).
- **Notes for review.** (1) The page's `index.html` is served without `Cache-Control`, so a browser that opened the
  Studio before the deploy can show the old Lab (`lab.js?v=lab-5`) until a reload — reload the Lab once after the
  deploy. (2) After a fallback from a file that isn't YAML at all, nothing of it parses, so publication ids restart
  at `p0001` (the old file is kept in `overrides/history/`). (3) `ShowWiring._apply_new_caps` now seeds the T1 hold
  on every capability apply: a painted mode whose api record differs from what the live trio compiles to runs at
  module level until the reconciler rewrites its slot (truthful).
- **Needs the show box:** the deploy (§8.1 step 3: note the Lab's show bar before; after, the same name or "the
  approved looks" and no "Published looks: … couldn't be used" line), a publish of a non-painted scene on the two
  kits, then Revert.
- **Review 4 fixes (Oct 17, on this branch after merging the fixed `claude/canopy-only`; PLAN §10 has the list).**
  Each was reproduced first and fixed at its root, with a regression test that failed on the code before the fix
  (17 of the 24 new route/engine tests fail there; the other 7 cover guards that already worked and each fails on its
  mutant — 18 mutants, all killed). Behaviour changes: **Revert to approved clears a fallback** (it used to be refused
  as "already what the show runs", so the red line and the /ops alert could never be cleared); a publish is
  **refused while config/ on disk differs from the git the show loaded** (a deploy under way), and the Lab builds on
  that git (no re-read when it is first opened); a **rollback** restores a fallback's problem and its warns,
  recomputes the T1 hold under the restored trio and names what really runs; the rollback warn is resolved by the
  next good publish, and can't-be-shown has its own key `looks:show:<mode>`, resolved when the mode is shown again;
  the **"Show <scene>" box is part of the confirm** (sentence, notes and cost follow it; a toggle re-asks; off by
  default for Undo and Revert); "today" at T1 paints the live painted design (`heads: "published"` when it is) and
  is never T1 while the scene is held; the try-on line no longer claims the slots keep the approved design; the
  **Studio reads the scenes as the show runs them** (`GET /api/canopy/scenes`, `web/ops/showcase/studio-model.mjs`,
  re-read on focus, on leaving #lab and every 15 s while visible; demo-model's SCENES is the fallback, and the export
  notes say so); the preflight is the show's own load (read only), so it never calls a usable file unusable. Pins:
  `lab.js`/`lab.css`/`lab-model.mjs` `?v=lab-7`, `studio.js` `?v=screen-2`. `tests/test_lab_routes_preview.py`'s
  "today in sim" test now waits for the show's start-up (the Lab used to take ~40 ms re-reading config/, which
  covered a race in the test). Browser check on a dev server (`--port 8216`, throwaway data, killed): a bad-YAML
  overlay → Revert dialog with the fallback sentence → 200, red line gone, `previous: null`; a Golden Hour publish
  with the box ticked said "switches to Golden Hour now on 9 modules … sends 9 writes", unticking re-asked ("Nothing
  on the canopy changes now … sends nothing"), the confirm sent `show: false` and the canopy stayed on Lowcountry
  Calm; the Undo dialog opened unticked; the Studio's Golden Hour chips and tile showed the published `#FFD080`. Suite after the fixes: `nice -n 15 uv run --offline
  pytest -q` → 2392 passed, 5 skipped, 44 subtests (10 m; `tests/test_deploy.py` passes now that PR A moved its
  checks); `ruff check` + `ruff format --check` clean; `node --test tests/*.test.mjs` → 150/150.

## PR A — canopy only (D67), `claude/canopy-only` → `main` (Oct 17; plan `docs/live-lab/PLAN.md` §1, stream R)
Alon on site, Oct 17: "remove all the stuff that isn't the canopy control". **Merge this before PR B** (the live
Lab); both target `main`; merge commits, not squash.
- **What went** (all in git history at `6ce971b`): the four vignette rooms (`conductor/vignettes.py`, the routes and
  pages, `config/vignettes.yaml`, their 15 registry entries and four accounts, the `vignette` role, enrollment and
  WebSocket topic, the synthesized vignette sim profiles, Gate R's "vignette accounts clean" criterion and report
  section); the Ubuntu venue box (`deploy/venue/**`, `scripts/release.sh`, the Chromium resting page,
  `venue-box.md`, `tests/test_deploy.py` — its env-doc check and the `deploy/` secret scan moved to
  `tests/test_mac_box.py`); the VWBD workshop kit and Paris's tooling (`workshop/`, `conductor/workshop.py`, the kit
  builder, `scripts/dev_guest_panel.py`, `tools/`, TEST-CENTER and its inventory docs, the root hand-off docs); the
  guest panel's workshop target (one target now: the canopy engine) and `make guest-dev`; `/` → the visualizer
  everywhere. **The Studio is screen only (Alon's override of the plan):** every live shop-kit control went —
  Connect, Apply scene gradient, Apply selected solid, brightness/power, the module select and "Both modules", the
  "Not connected" indicator, the fine-tune looks/colour/white/effect tools, Show saved Custom 3, the motion demo's
  live run, `/api/studio/pair/*` with `module/pair.py` and `module/scene_gradient.py`. It keeps the scene preview
  (the show's own design over the full canopy, tour, angle, module/head level, palette chips, an illustration
  brightness) and the Lab panel, and says the Lab is the only live control. `/ops/module` is untouched.
- **Old data on the live Mac is tolerated, not migrated** (§1.5): a saved session/enrollment of the removed role is
  skipped per record (the guest iPad's kiosk session survives; an unreadable record of a role that exists still sets
  the file aside); a registry-overlay entry for a removed vignette device (`REMOVED_DEVICE_IDS`, 15 ids, or any
  `role: vignette` entry) and a `vignette`/`kind` key — or a `profile` naming one of the six deleted vignette sim
  profiles (`REMOVED_PROFILES`) — on a surviving entry are ignored with a "Needs attention" line and pruned by the
  next /ops registry edit (`save_overlay_patch`); `overlay_diff` ignores them too.
  **`conductor/preflight.py`** (offline, read-only, binds nothing): settings, registry, the role store from a temp
  copy, and every registry entry's sim profile (startup builds a SimDriver over them, live mode too);
  `deploy/mac/run-conductor.sh --check` runs it, so `canopy-mac install` (which runs `--check` after `uv sync` and
  before it removes the hold or reloads launchd) never restarts the show onto code that can't load its data. When it
  fails, install **exits 3** and prints the checkout rollback (the last installed commit, `state/installed_sha`,
  else `~/canopy-prev-sha.txt`): the checkout is the rejected code and any restart of the KeepAlive job would start
  it, so the paste block rolls it back at once.
- **Contract text:** 07 §1/§2/§8/§10, 05 #3/#11/#13 (+ diagram), 04/06/01/03 notes, D67, and CLAUDE.md's opening line
  and show-accounts rule (lead's instruction: applied here for Alon to review in the PR). Runbooks are Mac-only now
  (`release-and-freeze.md` rewritten: a release is a tag; deploy = the paste block below; rollback = the previous
  commit + `canopy-mac install`).
- **Review fixes (R1–R3, Oct 17):** R1 a surviving overlay entry with a deleted vignette sim profile
  (`outdoor-plug-2` …) passed the preflight and then crashed startup's SimDriver — now ignored/pruned by value and
  the preflight builds every entry's profile; R2 after a failed preflight the checkout stayed on the rejected code
  while the docs said the show was safe — `canopy-mac install` exits 3 with the rollback and the paste blocks (here,
  `release-and-freeze.md`, PLAN §8.1) roll back on rc 3; `mac-show-box.md` §5 says so; R3 two comments named the
  deleted `release.sh`/`install.sh`/`canopy-start` (`.gitignore`, `test_review3_fixes.py`) — fixed, and a test now
  scans code, tests, deploy, web, config and the runbooks for the deleted tooling names. Each regression test was
  run failing before its fix; R1's two halves are each mutation-checked (no profile pop → the old-overlay app start
  and preflight fail; no preflight profile build → the unknown-profile preflight test fails).
- **Verified (sim, macOS):** `nice -n 15 uv run --offline pytest -q` (no `--ignore` any more: `tests/test_deploy.py` is gone): 2242 passed, 5 skipped, 44 subtests (after R1–R3);
  `uv run --offline ruff check . && uv run --offline ruff format --check .`; `node --test tests/*.test.mjs` 123/123;
  `nice -n 15 uv run --offline python -m conductor.soak` exit 0, 21 PASS rows, the only FAIL the non-gating
  "(reported)" D65 landing spread (p95 2.78 s), as in #27.
  `tests/test_canopy_only.py` (23 tests after R1–R3): the removed surfaces 404 for ops; no conductor module imports what went
  (AST); no vignette role/account/device, `len(REMOVED_DEVICE_IDS) == 15`; a vignette enrollment → 400 "…Use kiosk or
  tester."; WebSocket topics; **old data**: a kiosk + vignette `sessions.json` loads the kiosk (its cookie opens the
  panel through the app), nothing set aside; an overlay with a removed-id patch, a full `role: vignette` entry,
  `bench-kit: {kind}`, a full spare with `vignette`/`kind` and a full spare with `profile: outdoor-plug-2` loads, the app starts (`/healthz` 200), `/ops` names
  them, the next `save_overlay_patch` prunes them and `overlay_diff().problem == ""`; the guest panel's target is the
  engine with `CONDUCTOR_ENV=workshop-sim`; the preflight passes over that data and names it, fails (exit 1) over an
  overlay naming an unknown id or a sim profile SimDriver can't build (enabled or disabled), a session file the store
  would set aside, or a bad `CONDUCTOR_MODE`, and never
  changes the data dir's bytes; the Studio makes no request but `GET /api/canopy/design`. `tests/test_mac_box.py`:
  `--check` runs `python -m conductor.preflight` from the checkout over the pinned data folder, and a failing
  preflight stops `--check` and `canopy-mac install` before the agents are written, and install then exits 3 with
  the rollback line (the installed sha, else `~/canopy-prev-sha.txt`, else a placeholder). **20 mutants, each killed by its
  named test** (the `_merge` id tolerance and key pop, both `save_overlay_patch` prunes, the `overlay_diff` skip, the
  `_load` per-record skip and a widened skip, a vignette route back, the WS topic, the Needs-attention line, the
  preflight's temp copy / roles failure / registry check, the `--check` preflight call and its `die`, a Fernet-shaped
  string in `canopy-mac`, an undocumented env variable, a Studio module call / Connect button back, the workshop home
  back). `python -m conductor.preflight` over a scratch data dir with old data: exit 0, names the 1 removed device,
  the 2 entries' removed fields and the skipped session record. Browser (own dev server, `scripts/dev_design_lab.py
  --port 8213`, 1440×900 and 1180×820): the Studio loads with no console error, 270 lights painted from the show's
  design, no "Both"/module select/Connect, Golden Hour head-by-head ↔ module level, angle, tour; its only requests
  are `GET /api/canopy/design`; the Lab opens on `#lab` and its stage draws 270 lights; `/ops` offers kiosk and
  tester only and lists canopy, bench, spares.
- **The final grep** (`git grep -n -i -E
  'vignette|workshop|PAIR_IDS|scene_gradient|module\.pair|TEST-CENTER|deploy/venue|dev_guest_panel|kiosk-resting|venue[- ]box|canopy-status|systemctl|chrony|docker compose|guest-dev|VWBD-HANDOFF|shop-string-1|release\.sh|install\.sh|canopy-(start|stop|restart|backup|restore|logs|kiosk-enroll)\b'
  -- . ':!research'`; the last three alternatives added for R3) leaves only this **allowlist**: history docs (`docs/BUILD_PLAN.md`,
  `docs/ULTRACODE_BUILD_PROMPT.md`, `docs/CLOUD_SESSION_PROMPT.md`, `docs/design-lab/PLAN.md` + `PROMPT.md`,
  `docs/live-lab/PLAN.md`, `docs/DECISIONS.md`, this file's history); the §1.0 evidence files
  (`docs/current-live-test-session.md`, `docs/live-workshop-2026-09-28.test-center.json`); `.gitignore`'s
  `workshop.env`/`workshop-data*` lines; `conductor/gate.py`'s pinned "venue box" strings (and `tests/test_gate.py`'s
  match on them); the removal tolerance and its D67 notes (`conductor/config.py` `REMOVED_*`, `roles.py`,
  `roles_api.py`, `services.py`, `preflight.py`, `rehearsal.py`'s docstring); D67 scope notes in README and
  `docs/context/01/03/04/05/06/07`, `install-day-commissioning.md`; the strip/negative tests
  (`tests/test_canopy_only.py`, `test_roles.py`, `test_guest_panel.py`, `test_studio_routes.py`,
  `test_interface_structure.py`, `test_surfaces_review_fixes.py`, `test_ops_routes.py`, `test_stepped_rehearsal.py`,
  `test_review3_fixes.py`, `test_dev_design_lab.py` / `test_lab_review_fixes.py` proving `CONDUCTOR_ENV=workshop` is
  stripped); `tests_e2e/test_kiosk.py`'s `kiosk-resting.png` (the guest panel's own resting state). `shop-string-1`
  is left only in tests (the old shop-kit overlay shape); the Studio's geometry no longer carries the shop kits.
- **Merge notes:** `tests/test_lab_static.py` and `tests/test_lab_router.py` pin `studio.js?v=screen-1` on the lines
  next to `lab.js?v=lab-5`, and `web/ops/showcase/index.html`'s `studio.js` line sits next to the `lab.js` line: when
  stream U bumps the Lab to `lab-6`, take both sides. 05 #3 is one line that P's B1 clause also edits: take both.
- **Deploy (Alon, on the Mac, after merging; coordinate with anyone on the iPad) — PR A on its own:**
  ```
  canopy-mac backup
  git -C ~/canopy-show status --short                     # must print nothing
  git -C ~/canopy-show rev-parse HEAD | tee ~/canopy-prev-sha.txt
  git -C ~/canopy-show fetch --tags origin
  git -C ~/canopy-show checkout --detach <A tag or merge sha>
  ~/canopy-show/deploy/mac/canopy-mac install; rc=$?    # uv sync, then --check runs conductor.preflight, then restarts
  if [ $rc = 3 ]; then  # the preflight rejected this checkout: nothing restarted, but any restart would start it
    prev=$(cat ~/Library/"Application Support"/ColorCanopy/state/installed_sha 2>/dev/null || cat ~/canopy-prev-sha.txt)
    git -C ~/canopy-show checkout --detach "$prev" && (cd ~/canopy-show && uv sync --frozen --no-dev --extra live --quiet)
  fi                    # roll the checkout back at once (install printed the same line), then fix
  canopy-mac status; git -C ~/canopy-show describe --tags --always
  #  /ops "Needs attention": at most the "removed vignette devices/fields" lines; the kiosk iPad's panel still loads
  # Rollback: git -C ~/canopy-show checkout --detach "$(cat ~/canopy-prev-sha.txt)" && ~/canopy-show/deploy/mac/canopy-mac install
  # Data restore only if the box won't start:  canopy-mac restore <file from the backup above> --yes
  ```
- **Needs the real box:** the deploy itself (the preflight over the live data folder, which nobody here may read);
  that the iPad's kiosk session survives; that `/ops` shows at most the D67 "removed vignette" lines and that the
  first registry edit clears them from the overlay (the line goes at the next restart).

## PR stack (merge order — merge commits, not squash)
00. **Oct 18:** #28–#35 (PR A, B, B2, C, the C deploy notes, D, E, F) are merged into `main`; the show box runs
    `7b21918` (main after #35: D70 + D71 + D72) since 16:23. **PR G `claude/flagged-not-changing` (D73) is merged
    as #37 (`3c3ad38`, Oct 18 17:14)**, so A, B and C's "merge #37 first" is met. **Open: preset-options PR A
    `claude/fin-visualizer` → `main`** (D74). **Then preset-options PR B `claude/motion-every-scene`** (D75) → `main`
    (built and sim-verified with review rounds 1–6 fixed, not pushed, no PR number yet; its block at the top). **Then
    preset-options PR C `claude/ipad-review-menu`** (D76; merge commits). **Then preset-options PR D
    `claude/preset-options-catalog` → `main`** (D77; built on a merge of A, B and C, so "merge A, B and C first";
    merge commits, not squash). The preset-options stack in merge order: A, B, C, D. PR F `claude/flagged-reads-lit` (D78) is merged as #41.
0. **Where the stack really is (checked Oct 17 with `gh pr list --json baseRefName,mergeCommit`).** #19 (D62) is on
   `main`. **#20–#23 were merged on Oct 15–16, but each into the branch below it, not into `main`** (the stacked-PR
   trap again), so `main` has none of P1/P2, the 2-D designs + ripple (D63/D64), the Studio's design painter or stepped
   motion + Gate R (D65), and neither does the show box, which deploys from `main`. **#24 `claude/stack-to-main` →
   `main`** carries them over: `claude/studio-design` (#23's merge, tree identical to #23's tip `1ce3de5`) plus
   `origin/main` (only #17's cloud/Remote Control docs were missing; clean merge). Verified (macOS): 1594 passed,
   lint, JS 67/67. **Every PR below targets `main`; merge them in number order, #24 first.** (#20 P1/P2, #21
   canopy-field, #22 studio-design and #23 stepped-motion are described in their PRs and in the D63–D65 section below.)
0f. **#25 `claude/design-lab` → `main`, after #24 `claude/stack-to-main`** (Design Lab Slice A, screen only; plan
    `docs/design-lab/PLAN.md`): drafts over the parsed config, `/api/lab/*` (catalog, state, preview, versions,
    restore, export), the corrupt-store safety, the Studio's Lab panel (tier views, today / show-box views, A/B,
    versions, Present, export), the extracted validator `conductor/looks/scene_rules.py`, the scene pins fixture and
    frozen recipes, `make lab-dev` on port 8200. **No show-path edits.** Integrated Oct 16 from four streams (merge
    commits, no conflicts) plus two glue commits. **Verified (sim, macOS, `--ignore=tests/test_deploy.py`):** pytest
    1960 passed, 5 skipped (the gated round trip), 136 subtests; `make lint`; node 95/95. `LAB_ROUNDTRIP=1 make
    lab-roundtrip`: all five exported configs (canonical, rename, add, remove, reorder) pass the whole suite; remove
    needed a rerun, its first run hit one wall-clock flake in the pre-existing
    `test_a_rehearsal_from_ops_on_the_show_engine_writes_its_report_and_leaves_the_look_still` (passed in isolation
    6/6 and in every other full run). `tests/test_lab_slice_a.py` drives the Done-means client workflow through the
    real routes (preview < 1 s, save "Client Oct 17 v2", compare, export, `git apply`, re-read == the saved drafts)
    and checks nothing the show reads changed. curl smoke on `scripts/dev_design_lab.py --port 8200`: catalog 200,
    Golden Hour preview 200 in 28 ms, save 201, export 200 with a patch that `git apply --check`s on the working
    tree. **Not yet:** a browser pass of the integrated Lab at 1440×900 and 1180×820 touch with real previews (S4
    checked the UI against fixture answers); D66 in `docs/DECISIONS.md`; the PR. **Needs a real iPad:** the
    colour-picker popover, element fullscreen in Present, swipe without pull-to-refresh, the 28 px slider thumbs.
    Slice B (try-on) is #26 below; C (motion editor) is not built.
    **Review fixes (Oct 16, same branch):** a five-lens review's confirmed findings, each fixed at the root with a
    regression test that failed before the fix (or a mutation test that kills a mutant that used to survive):
    client copy (name, mood, description) refuses motion words and "270"/"nine", in the draft model and in
    scene_rules' catalog rule; a description the colours or layout moved under gets a note (preview, export); the T3
    sentence names only colours a strand reaches (`reached_stops`; the new sweep default is [75, 60]); the T1 lock
    uses the slot-repaint predicate (a colour rename stays editable) and is tested on save; the show-box note follows
    the stepped gate; the export lists the solid, stepped timing (D65) and client copy under their own headers and
    the Studio SCENES note covers added colours and the opening look; the store rejects NaN/inf, FIFOs/devices and
    deep YAML (set aside), deletes exactly one entry (unique labels for unreadable ones) and never reissues a deleted
    name; races and internal KeyErrors answer 404/500 correctly (`NoSuchVersion`); the catalog survives a hand-edited
    version; every Lab answer carries `X-Lab-Config-Sha` and the page notices a restart on a new config. Page: no
    duplicated stage after save/delete/angle/Present (blocker), one save per double tap, stale alerts clear, the
    today view's caption, badge ("Approved design") and note say when the slots hold the approved design (editor
    and Present), D63 warnings show on the stage, the export dialog fits, positions read "Modules 1–2", the iPad
    tabs and tiles fit. Asset version `lab-2`. **Verified (sim, macOS, `--ignore=tests/test_deploy.py`):** pytest
    2044 passed, 5 skipped, 136 subtests; `make lint`; node 100/100; `tests/test_lab_review_fixes.py` 84 tests (45
    of them fail on f2a2f6d) plus 17 mutants killed; a Playwright walk (system Chrome, 1440×900 and 1180×820 touch,
    own dev server on 8209) 29/29 on the fixes, 20 of its checks failing on f2a2f6d. **Not re-run:** `make
    lab-roundtrip` (the gated export round trip).
0g. **#26 `claude/design-lab-trial` → `main`, after #25** (Design Lab Slice B, the try-on; D66's Slice B line; plan
    §6). **Show-path edits:** `conductor/canopy.py` (the trial seam), `conductor/show.py` (`_trial_for`,
    `context(recipes=)`, the trial compile at T3/strand only), `conductor/config.py` + `config/conductor.yaml`
    (`lab.trial_max_s: 150`), one `trial_active` branch in `conductor/routes/kiosk.py`; **not** `reconciler.py`,
    `executor.py` or the drivers. Lab side: `conductor/design_lab/trial.py`, `GET /api/lab/trial`,
    `POST /api/lab/trial/start|stop`; the Lab's Try-on panel (Apply → the server's confirm sentence naming every
    driven module and the bound → Try it on; countdown, "In try-on" badge, Back to approved, "Apply again in N s");
    the iPad's scene cards rebuild on a content change (`cardsKey`). Integrated Oct 16 from two streams (B1 server,
    B2 UI; merge commits, no conflicts) plus one glue commit: the preview's "today" shows the try-on's installed plan
    while it runs (it showed the slots' painted design while the lights were at module level), the page re-reads it
    when a try-on starts or ends, Lab assets `lab-3`.
    **Verified (sim, macOS, `--ignore=tests/test_deploy.py`):** pytest 2094 passed, 5 skipped, 136 subtests;
    `make lint`; node 115/115; `python -m conductor.soak` exit 0, 21 PASS rows, the one FAIL is the non-gating
    "(reported)" D65 stepped landing spread (p95 2.78 s > 2 s; not B's; the report was not committed, it goes with
    C); `LAB_ROUNDTRIP=1 make lab-roundtrip`: 5 passed (all five exported configs pass the whole suite, 48 min).
    `tests/test_lab_trial.py` (49 tests, virtual clock) covers every test plan §6.6 names, incl. the 20-virtual-minute Golden Hour try-on (hex and layout changed) with
    zero slot writes, an unchanged `t1_target` slot hash and no stale-slot alert; B1 recorded 25 mutation checks
    (about 30 mutants), each killed by a named test.
    **Smoke** (`scripts/dev_design_lab.py --port 8203`, curl + the browser at 1440×900): a Golden Hour draft with
    Sunset pink `#FF9BC4` → `#FF8FBF`: start without confirm → 409 with the sentence and "sends 9 writes now, up to
    ~36 over the next 10 s"; confirmed → 200, `tier: modules`, 150 s bound; `/api/canopy/heads` went from canvas
    (24–30 colours per module) to one colour per module, six of nine modules differing from a try-on of the
    unchanged approved draft; a confirmed re-apply 4 s later → 409 "Apply again in 4 s" + `Retry-After: 4`; after
    the gap → 200, `applies: 2`, bound unchanged; `POST /api/kiosk/scene` and `/api/kiosk/select` → 503 "Staff are
    trying a new look. It will be back shortly." + `retry_after_s`; stop → `trial: null`, Golden Hour back on canvas
    with all 270 heads matching. In the browser the real routes drove the panel (confirm, badge, countdown, today at
    module level with the try-on note, Back to approved → today back to the painted approved design); the only
    console error was the expected 409. **Needs the real kits (after the deploy block below):** a try-on on Modules
    1–2 (only r0c0/r0c1 registered there, so the sentence names two modules and "sends 2 writes now, up to ~8"),
    that the lights show the draft's colours at module level and come back on Back to approved and at the bound,
    real write latency within the 8 s gap, and the iPad's "Staff are trying a new look" toast. **Review fixes (Oct 17,
    10 confirmed findings):** the iPad no longer keeps saying "Staff are trying a new look" after a try-on (or
    "being tested" after a rehearsal) ends — the panel state gains `waiting` (`"try-on"|"testing"|null`), a stale
    lane refusal under the title is dropped once its flag lifts or a scene is accepted, and kiosk.js drops the
    waiting toast at the first fresh read with `waiting: null` (`waitToastOver` in panel-model.mjs); the confirm
    sentence reads "Try on your draft of Golden Hour …" unless the draft renames the scene; TrialJSON `label` is the
    approved scene's name (Back to approved named the draft). Six test gaps closed, each test checked against its
    mutant: a first start the engine refuses, re-picks keep the try-on (engine + the real capability loop), stop()
    awaits the bound task, the restart test checks before start-up's idle look, the concurrent-starts test really
    overlaps, and an AST check refuses any writer call from a Lab source (`design_lab/trial.py` left
    `COVERED_MODULES`). **Verified (sim, macOS, `--ignore=tests/test_deploy.py`):** pytest 2106 passed, 5 skipped,
    136 subtests; `make lint`; node 116/116; soak: only the non-gating "(reported)" D65 row FAILs, as before.
    Browser (own dev server on 8205): during a try-on the iPad toast counts down; after Back to approved it is gone
    within one poll and the header reads "Choose a scene below."; a renamed draft shows "On the lights now: Pinker
    Hour (Golden Hour)" and Back to approved says "the lights show Golden Hour's approved look". **Needs a real
    iPad:** the Try-on panel's tap targets and the confirm block's scroll position. **Second review fixes (Oct 17, 3
    confirmed findings, each with a virtual-clock test that fails without its fix):** (1) a re-apply keeps the
    running session's bound, so its confirm now names the time left ("… for the 110 s left of the running try-on (a
    re-apply never extends it)"; one shown up to 30 s earlier still matches; the cost adds "the running try-on still
    ends in N s"), the first start's "for up to 150 s" no longer confirms it, and with less than `kiosk.cooldown_s`
    of the bound left it is refused (409 "This try-on ends in 6 s, too soon for another apply; …", Retry-After,
    nothing sent, gap not used up); the engine also refuses an apply whose session ended or changed while it was
    built (`expect_session`, `min_left_s`); (2) a capability re-pick that would ADD a module to the try-on's plan (one
    registered, or newly read, mid-try-on) ends the try-on so every module gets the approved look; same-set and
    shrinking re-picks keep it (`ShowWiring._joining_trial`); (3) an exception (not a PlanError) while a try-on
    starts, re-applies or ends no longer strands the override or its plan: override and bound task cleared, the
    approved look back (the safe look if that raises too), an alert, the route answers a plain 500, the bound task
    ends cleanly. The Lab's JS is unchanged (it already re-asks on a 409 confirm and counts a plain 409 down).
    **Verified (sim, macOS, `--ignore=tests/test_deploy.py`):** pytest 2120 passed, 5 skipped, 136 subtests; `make lint`; node 116/116; soak exit 0, only
    the non-gating "(reported)" D65 row FAILs, as before; `tests/test_lab_trial.py` 74 tests. **CI fix (Oct 17):** the try-on route tests failed on CI (apply gap 2.47 s left, a missing `retry_after_s`, "105 s left") because FastAPI runs the sync dependencies (`require_ops`, `_no_cross_site`, …) on real worker threads, and the virtual loop jumps its clock to the next timer while a thread computes; `no_threads` now runs `anyio.to_thread.run_sync` inline and refuses `Thread.start`, and the time assertions are bounded by the clock read around each request. Verified: with every dependency and build made 50 ms slower in real time the old tests fail 9, the new pass 74; `tests/test_lab_trial.py` 30/30 under 10 CPU hogs (the first 4 under 30); pytest 2120 passed, 5 skipped; `make lint`; node tests pass. **Open for Alon:** the try-on
    doesn't apply the T1 lock (it never writes a slot), so a locked painted design can be tried on at module level;
    say if it should.
    **Deploy block (§11; Alon runs it, coordinated with Paris; this ends Paris's break-glass hold):**
    ```
    # after merging #24 → #26 (retarget each to main first; merge commits):
    git -C ~/canopy-show switch main && git -C ~/canopy-show pull --ff-only && canopy-mac install && canopy-mac start
    # hand the box back to Paris (break-glass hold):
    canopy-mac stop --yes
    ```
    (Done Oct 17: #24–#27 merged and `6ce971b` deployed; the kits stay Modules 1–2.)
0h. **#27 `claude/design-lab-motion` → `main`, after #26** (Design Lab Slice C, the motion editor; D66's
    Slice C line; plan §7). **No show-path file touched:** `show.py`, `canopy.py`, `reconciler.py`, `executor.py`,
    `sequence.py`, the drivers and `config/` are unchanged; `motion.stepped.live_enabled` and
    `kiosk.ripple_stagger_s` are never written (route, AST and config checks). Server (C1):
    `conductor/design_lab/stepped.py` (`effective_step_s`, the runner's rule, pinned equal to a real `ProgramRunner`
    for nine and two modules; `stepped_preview`: the compiled stepped steps exactly, `pace.nine` 7.5 s / `pace.today`
    6 s at the 6 s design, `runs`, moving copy incl. D62's two-strand copy, the live sentence; `ripple_rings`: the
    show's own ripple bands); PreviewJSON fills `stepped` and `rings` and adds `ripple`; CatalogJSON adds the
    editor's limits and live lines; drafts take `step_s` in 0.5 s steps; an exported stepped timing's D65 line names
    its pace and keeps the modes.yaml comment true. Page (C2): the editor's Motion section (direction, steps per
    cycle, step time, run length, run line, live line, greyed effects card; scenes without a stepped rung say why),
    **Play the steps** under the picture at the server's pace (pace toggle: nine modules or the show box's Modules
    1–2, step counter, pause/stop, a labelled 4× fast preview), the moving sentence only while it plays, **Play the
    ripple** at a 0–1.2 s stagger (screen only), Present plays the steps (button or Space). Integrated Oct 17 from two
    streams (merge commits, no conflicts) plus two glue commits: assets `lab-4`; the live line is the server's
    `STEPPED_LIVE_OFF` + note word for word and the ripple label the server's note; a pace the server says would
    hold still is not played; at the show box's pace the moving sentence is the partial canopy's (D62).
    **Verified (sim, macOS, `--ignore=tests/test_deploy.py`):** pytest 2312 passed, 5 skipped, 136 subtests; `make
    lint`; node 124/124; `tests/test_lab_stepped.py` 192 tests (virtual clock, a real `ProgramRunner` on the engine);
    C1 16 and C2 6 mutants, each killed by a named test. `python -m conductor.soak`: exit 0 (overall PASS, 33 s wall),
    the one FAIL is the non-gating "(reported)" D65 stepped landing spread (p95 2.78 s > 2 s, unchanged since #23);
    **`docs/reports/sim-acceptance.md` is regenerated and committed in this PR** (plan §1). `LAB_ROUNDTRIP=1 make lab-roundtrip` on the final branch (Oct 17): 5 passed (all five exported configs pass the whole suite, 50 min).
    **Browser smoke** (own dev server `scripts/dev_design_lab.py --port 8208`, Playwright on system Chrome, 1440×900
    and 1180×820 touch, screenshots looked at): Coastal Tides plays 12 steps of 7.5 s (15 of 6 s at the show box's
    pace, only Modules 1–2 lit); its step time edited to 9 s → "10 steps of 9 s (90 s)" at both paces, run length's
    minimum follows to 9; direction → Reverse marks it Draft with generated stepped copy; the live line reads
    "Stepped looks are off on live hardware until the stepped rehearsal (Gate R) passes. Alon sets
    motion.stepped.live_enabled after Gate R."; Play the ripple lands three rings 0.6 s apart under the D64
    screen-only label; the effects card is greyed; Present plays and Space pauses; Lowcountry Calm says the idle look
    never steps. No page errors (the only console error is the old favicon 404). **Needs a look on the Mac:** none
    left for Slice C. **Needs a real iPad:** the Motion sliders' thumbs, the pace toggle and Play in Present by
    touch. **Needs the real kits:** nothing new — stepped stays off on hardware until Gate R, the ripple until D64.
    **Slice C review fixes (Oct 17, 16 findings; no show-path file, neither live setting touched):** an approved
    `step_s` off the half second (valid for `SteppedTechnique`, e.g. 6.25) no longer stops the whole Lab (the
    half-second rule now applies to an edit only, at `stepped.step_s`; the page snaps only the edited field); a run
    that fits one step (step 0 is the still design) is said to hold still and isn't played; Golden Hour says it
    steps only where its painted rungs can't run (server `steps_when`, on the Motion card, editor and Present); with
    `motion.stepped.live_enabled` set, the live line is the server's `STEPPED_LIVE_ON` (no "..", the catalog's
    off sentence is null), the show-box pace note drops "stepped stays off" and the export's D65 section says "live
    on"; the ripple line claims "as the canopy does today" only at the canopy's own stagger; the export's `step_s`
    comment follows the D65 line's pace (ripple rings, a still run) and no longer says "instead" twice; in A/B the
    run ends with the longest side; at the show box's pace the tier list labels whose moving sentence each line is.
    Assets `lab-5`. **Verified (sim, macOS, `--ignore=tests/test_deploy.py`):** pytest 2345 passed, 5 skipped, 136
    subtests; `make lint`; node 133/133 (`lab-model.test.mjs` 62); each fix has a regression test seen failing
    first; 18 mutants (6 page guards, export settings/still/route plumbing, ms ceil, catalog live note, idle
    wording, the one-step still, the live clause, the increment check, the A/B end) each killed;
    `python -m conductor.soak` exit 0, same numbers as above (report unchanged but its timestamp). **Browser**
    (own dev server on 8210, sim): Golden Hour's "steps only where…" line on the Motion card, editor and Present;
    the ripple at 0 s "as the canopy does today"; at the show box's pace the tier list's two labelled lines; A/B
    with a 30 s draft against the approved 90 s run keeps A stepping after B's "Run over" and ends both on their
    holds; a 10 s run can't be played and says why; an exported step_s 10 gives the "live off until Gate R"
    section; no console errors. The live-on lines are checked by tests only (no Lab path sets the flag).
    **For Alon (show path, outside the Slice C freeze):** `ProgramRunner` (and `SteppedTechnique._honest`) accept a
    run of exactly one step (`count == 1`): the iPad would show the moving copy for one step while no colour changes.
    The Lab now says such a run holds still; `test_a_one_step_run_is_still_in_the_lab_and_the_runners_gap_is_pinned`
    pins the gap until `count_for(step) < 2` (and `run_s < 2 × step_s`) is decided.
    **CI flakes (Oct 17, #25's branch, merged forward):** `test_a_rehearsal_from_ops_on_the_show_engine_…` (KeyError `'preview'`)
    waited for "a plan and not commissioning", which holds for an instant *before* the simulator's startup
    commissioning begins (the first look goes out first); on a slow box the rehearsal preview then met
    commissioning (409) or started a rehearsal that commissioning's re-pick overrode. Tests now wait on
    `ShowWiring.started` (commissioning run and the look re-picked; `tests/show_started.py`), as do the kiosk/guest
    rehearsal helper, `partial_canopy.showing` and the surfaces fixtures. The Lab's "preview under a second"
    checks bound the preview's CPU time, not wall time. Reproduced by delaying startup commissioning 0.05–2 s
    (failed before, 157/157 after); the 9 touched tests
    passed 30 runs in a row under 20 CPU hogs (2 per core); full suite 2044 passed, 5 skipped, 136 subtests;
    `make lint`.
- **Merged:** PR #18, `claude/mac-show-box` (D61), as `9c4c7aa` on Oct 13.
- **Items 1–6 below are older and all merged** (#11–#16); kept for their notes.
0i. **PR A `claude/canopy-only` → `main`, after #27** (D67, canopy only; the block at the top of this file has
    what went, the old-data tolerance, what was verified and how, and the deploy paste block). Merge before PR B
    (the live Lab); merge commits, not squash.
0j. **PR B `claude/live-lab` → `main`, after PR A** (D68, the live Lab: B1 server + the Lab page; the PR B block at
    the top). Both target `main`; "merge #A first"; merge commits. Deploy it on its own, after PR A is deployed.
0k. **PR B2 `claude/live-lab-repaint` → `main`, after PR B** (D68's operator repaint; the PR B2 block at the top).
    Targets `main`; "merge #B first"; merge commits. Deploy only after bench item 6 has been run once on a show kit
    with its own Gate C, on its own, after PR B is deployed.
1. **`claude/paris-folder-consolidate-3addc3` → `main`**: the consolidation. It has two parts:
   - an import commit (`79a0082`): Paris's 2026-10-07 package source, verbatim on the kit commit it forked from
     (`1eb8551`);
   - a merge into `main` that resolves every conflict in Paris's favour.

   It also carries PR #10's STATUS commit, so **#10 is superseded**. PRs #1–#9 were already merged on Sep 27, and their
   remote branches are fully merged and can be deleted. **Merging this redeploys Railway staging.** Check the pre-deploy
   item under Next step first.
2. **`claude/review-fixes` → after #11**: fixes for the bugs the merge review found in Paris's Studio and bench code,
   the two decisions (home redirect, retiring the original `address_probe`), route-level Studio tests, writer-path
   coverage for the new jobs and Studio paths, and true copy (see Known gaps 2–4 and 7).
3. **`claude/show-path-mapping` → after `claude/review-fixes`**: the show path reads each kit's own controller map
   (D57, closes gap 1). Also: the controller swap carries the map, module-page effect start/restart keeps unmapped
   entries as read, an opt-in 60-entry simulator profile, and the 07 §6 mapping rule (flagged for Alon's wording).
4. **`claude/record-map` → after `claude/show-path-mapping`**: **Apply map** on `/ops/bench` records a kit's controller
   map from its own two answered `address_probe_safe` runs (D58, closes gap 9). It follows the agreed proof rule and
   only ever records the identity map. Also: taking back a Yes revokes it, the show re-reads that module at once,
   a CLI `map` command, and yes/no questions for the two map probes.
5. **`claude/scene-modes` → after the scene catalog commit (`d897843`, `config/scenes.yaml`)**: the show's six modes
   become the six approved scenes (D59). Coastal Tides replaces Holiday at Home, every tier renders the scene's
   palette, and nothing moves. Three scenes are painted (one per slot); the other three start at module level.
   Verified in sim only: the full pytest suite, the node tests, `make lint`, and an 8-hour soak with every target
   PASS (24 SIMULATED motion plans; canvas 71 and module level 96 accepted changes). No light was touched.
6. **`claude/guest-panel` (based on `d897843`, the six-scene catalog) → after the scene catalog**: the guest panel's
   backend (part 1 of 2; the iPad UI follows on top). `conductor/guest_panel.py` + `conductor/routes/guest.py`:
   `GET /api/kiosk/panel`, `POST /api/kiosk/scene|brightness|power`, ops-only `GET /api/kiosk/debug`. Workshop target
   (CONDUCTOR_ENV=workshop* and both shop modules registered) drives the two shop strands only through ModuleSession /
   `apply_pair` / `scene_gradient` as "guest panel" (never steals a lock; gradient when both kits have their own map,
   else the scene's solid colour; Custom 1 only, the original Custom 1 backup kept); engine target everywhere else
   (`request_mode(scene_id)`; brightness/power only if the engine has `set_guest_*`). "Guest panel" buttons on /ops
   and the Studio header; `make guest-dev` runs it on the simulator (port 8100, 60-entry controllers, mapped).
   **Verified (sim):** `tests/test_guest_panel.py` (30) + two new writer paths; full suite, lint, node tests; the
   launcher exercised by curl (gradient applied, brightness coalesced, leases "guest panel").
   **Needs real hardware:** a guest scene on Kingston + String light 1 (String light 1 needs its own Gate C and map
   first, or the panel shows solid colours — by design).
   **Part 2, the iPad UI (same branch):** `/kiosk/` is now the Hubspace-style guest panel (spec + as-built notes:
   `docs/design/guest-panel.md`): dark device header with the master switch, light sheet with six scene cards, a
   Now-showing serpentine of the 30 lights, the Hubspace brightness slider and the server's feature line. Pure logic
   in `web/kiosk/panel-model.mjs` (`tests/guest-panel.test.mjs`). Touch: ≥ 64 pt targets, pressed states, the slider
   sends ≤ 1/s while dragging plus the final value, no zoom / callout / overscroll, safe areas, attract after 2 min,
   calm copy only. `?debug=1` (ops only) adds the debug drawer and the iPad-frame preview. The state now carries
   `look` ("gradient" | "solid") and each scene its `solid` colour, so the preview draws what is actually lit.
   Quicksand (OFL) is vendored as one variable file renamed "Canopy Rounded" (OFL Reserved Font Name) under
   `web/static/fonts/canopy-rounded/`, so weights 500/600 really render — **needs Alon's OK** (convention is system
   fonts; removing the `@font-face` rule falls back to SF Pro Rounded). Hubspace logos in `web/kiosk/brand/` — **logo use needs
   Home Depot / Hearst brand confirmation**.
   **Verified (sim):** node + Python suites; driven with Playwright on `make guest-dev` at 1180×820, 1024×768 and
   820×1180 with touch emulation (scene, cooldown toast, slider drag pacing, power, debug, attract, frame preview; no
   page errors, no overflow). `tests_e2e/test_kiosk.py` rewritten for the panel but **not run** (the pinned
   Playwright browser isn't installed here). **Needs a real iPad:** Safari touch feel, Guided Access, home-screen
   web-app mode.

5. **`claude/guest-engine-controls` → after the scenes catalog (`d897843`)**: guest brightness and on/off in the
   show engine for the iPad guest panel (D60). It adds `set_guest_brightness`, `set_guest_power` and
   `clear_guest_overrides`, and the guest fields in `CanopyState`. The overrides are desired state, so the
   reconciler keeps them. Choosing a mood, the safe look and an idle reset (`kiosk.idle_reset_s`, 300 s) clear them.
   It also adds `POST /api/canopy/guest-reset` and a "Clear guest brightness / off" button on `/ops`. No kiosk routes:
   the guest panel branch calls the engine methods.

## Canopy-wide designs, the ripple and stepped waves (D63–D65) — 2026-10-13/14
Alon asked for looks laid out on the grid of all 270 lights across the nine panels — radial patterns, vertical and
horizontal waves — instead of along each strand's wire (the "snake"). Built as #21–#23 by workflows (read-only design
with a judge, then one implementer per package, two adversarial reviewers, a fixer; every finding fixed with a test
that fails before it). Sim only; nothing new has reached a light.
- **Static 2-D designs (D63).** The compiler was already (x, y)-aware; the data was x-only. Each scene is now one
  design over canopy inches (radial, linear at any angle, several centres, sweep) driving the per-head frames (T1) and
  the one-colour-per-strand look (T3, sampled at each panel's centre by row and column): Calm radial, Sunset corner to
  corner, Garden from the bottom edge, Coastal a tilted shoreline, Golden Hour a sun at the left edge, Goodnight a
  moon over a corner. Palettes, names, brightness and slots unchanged; drafts for Paris.
- **What motion the cloud allows (and what it doesn't).** Per-head animation across 270 lights is impossible through
  Afero: each per-head frame is a 3–5 KB slot rewrite (07 §6 forbids looping them), a controller stores only three
  looks, and on-device effects run along each wire on unsynced clocks. Built instead: (1) the **ripple** (D64): a new
  mood lands ring by ring in its design's order, one PUT per module, off (`kiosk.ripple_stagger_s: 0`); (2) **stepped
  waves** (D65): the module-level design moves one step every few seconds across the 3×3 panels — 6 s designed, 7.5 s
  effective on nine modules (20% budget reserve under the 90 writes/min account cap), 90 s runs then the still look,
  governor holds the look on throttling. Coastal Tides steps first; Golden Hour steps only where its per-head sun
  isn't available; Calm (idle) and Goodnight never move.
- **Sim results (soak):** all 9 pinned rows PASS. Ripple at 1.2 s: settled p95 5.1 s (target 7.4), one PUT per module
  per change. Stepped: ≤ 72 step writes and ≤ 89 writes of any kind per account per 60 s (cap 90), one write per
  changed module per step, 0 governor trips and 0 downgrades at 5% throttling; each step's landing spread p95 2.78 s
  against 05's 2 s (reported, not gating) — waves read as coarse steps.
- **Live today:** unchanged. The Mac's two kits run T3; the stepped tier is refused on a non-sim driver
  (`STEPPED_LIVE_OFF`), proven through the show's own tier rules; downgrades skip the stepped rung.
- **Hardware still needed:** Gate R rehearsal on Modules 1–2 (VWBD's kits, now) and on all nine show kits
  during install week (`docs/runbooks/stepped-rehearsal.md`); cut-vs-fade of a strand colour change (bench item 6) and
  start skew across kits (gate 12); Gate C + map + commissioning per kit before any per-head design shows; the iPad
  preview's orientation at the venue (`kiosk.preview`). Freeze the designs before install commissioning.

## VWBD's kits are canopy Modules 1–2 (for real since Oct 17; first set up Oct 13)
Alon's call, for client demos: the iPad guest panel drives VWBD's two shop kits as the canopy's first two modules.
Done on the Mac show box (Alon ran the commands; the auto-mode check kept Claude from changing the live box):
- **What changed (ops overlay only, `~/Library/Application Support/ColorCanopy/data/overrides/registry.yaml`,
  NOT_FOR_GIT):** `canopy-r0c0` and `canopy-r0c1` got `account: bench`; Kingston test was released from `bench-kit`
  and registered as `canopy-r0c0` (Module 1); String light 1 moved from the spare entry `shop-string-1` to
  `canopy-r0c1` (Module 2) through the register API's spare hand-off. Backup first
  (`canopy-data-20261013-163538-manual.tar.gz`) and the pre-change overlay kept as
  `overrides/history/registry-before-canopy-test.yaml`.
- **True on hardware (Oct 13):** right after registering, the engine sent its look to both kits; a scene chosen on the
  test iPad (Garden Gathering) was accepted and the panel reported it ready; Alon confirmed the basic demo works. Each
  strand shows one colour (module level, T3): neither kit has bench flags or a controller map on this Mac, so
  per-light painting is off. Not yet measured: readback timing, re-assert behaviour, brightness and power from the
  iPad on these kits.
- **D62 is deployed (Oct 17, `6ce971b`):** the partial-canopy copy counts the two driven strands. Nobody may drive
  these two kits from the Hubspace app, the Studio or another Conductor: the reconciler puts the show's look back
  within seconds.
- **The Oct 13 hold ended on Oct 17** when Alon deployed `6ce971b` (`canopy-mac install` lifts the hold).
- **Oct 17: for real, no undo.** Alon: these shop kits are real canopy kits and VWBD's account is the live show
  account, so the planned Oct 19 undo is **cancelled**. (The undo was run once on Oct 17 by mistake and reverted at
  once from that day's 16:54 backup, `canopy-data-20261017-165403-manual.tar.gz`; the overlay is the Oct 13 mapping
  again: `canopy-r0c0`/`canopy-r0c1` on account `bench` with their ids, `bench-kit` and `shop-string-1` empty —
  checked with `load_registry`.) The show runs them at module level until each kit's own Gate C + map is recorded on
  the Mac. The /ops overlay diff proposes `account: bench` for these two positions in `config/devices.yaml`; whether
  git should say so is Alon's call, so don't commit that diff without him. Writes to the live account before Home
  Depot's authorization letter (06 #20) are Alon's call.

## The Mac show box (D61) — 2026-10-13
Alon moved the show onto the Mac mini (M2 Pro, macOS 26.2) instead of the Ubuntu mini PC. Runbook:
`docs/runbooks/mac-show-box.md`; helper: `deploy/mac/canopy-mac`.
- **Installed on the Mac mini, live:** show checkout `~/canopy-show` at `claude/mac-show-box` (switch it to the
  release tag once merged); settings and data under `~/Library/Application Support/ColorCanopy/` (generated ops login
  and key, 0600); `HUBSPACE_ACCOUNT_ALLOWLIST` allows only VWBD's shop account (the bench kits' account, not a show
  account); Alon signed it in on Oct 13 (`canopy-mac login --account bench`, password typed by him, no emailed code).
  It sees Kingston test and String light 1, both now registered and writable (Alon's go, Oct 13): Kingston test as
  `bench-kit`, String light 1 as `shop-string-1`, a new overlay entry copied from VWBD's (role `spare` on the bench
  account, so the engine never drives it). Neither has bench flags or a map on this Mac yet (they're in VWBD's
  overlay; per D19/D54 a kit's own flags may move with it, never another kit's). Nothing has been written to a light
  from this Mac. The guest panel's target here is the canopy engine (nine modules, none registered), so on the test
  iPad (kiosk-enrolled "Test iPad") scenes don't change a light; the module page drives the two kits.
- **Verified on this Mac (how):** `/api/version` says live/venue; `/healthz` 200 and `/ops/` 401 without the login,
  both over the LAN address; `kill -9` → launchd restarted it, healthy within ~1 s; a frozen process (`kill -STOP`) →
  the watchdog logged 2 failed checks, then restarted it on the 3rd (`kickstart -k`; launchd force-killed the frozen
  process after the 30 s shutdown allowance), healthy again 231 s after the freeze (120 s of that is the start-up
  grace); re-running `install` over the running Conductor (this failed at first and left it unloaded: `bootout`
  returns before the process exits — fixed, `install` now waits for launchd, and the watchdog loads a Conductor that
  is neither loaded nor held, which it did on the real launchd at 14:50); a manual backup. `tests/test_mac_box.py` 27
  pass, and six guards were each broken in turn and a test failed each time (the `CONDUCTOR_AUTH` drop, the data-dir
  pin, the 0600 check, the hold, the wait for launchd, the watchdog's reload).
- **Not verified:** a real reboot and a power cut (FileVault is now off; automatic login still to be switched on,
  runbook §1); the Mac is on Wi-Fi, not wired to a show router; the 7 am checklist's *Clock synced* item can't see
  macOS's NTP state (warns; `canopy-mac status` shows the sntp offset); a phone/iPad reaching it over the network.
  Done on the Mac (Alon, Oct 13): FileVault off; `sudo pmset -a autorestart 1`; the AC profile already had sleep,
  display sleep and disk sleep at 0 (never) and wake for network on.

## What the consolidation did
- **Source of Paris's work:** `NEW STUFF FROM PARIS/color-canopy-integration-2026-10-07/source/` (gitignored drop
  folder; manifest verified, 234 files). The baseline kit inside it is byte-identical to `1eb8551`, so this was a true
  three-way merge.
- **Paris wins:** the 12 files changed on both sides take Paris's whole file (`afero.py`, `session.py`, `rows.py`,
  `render.py`, `config.py`, `bench/jobs.py`, `routes/module.py`, the module page, `workshop/README.md`,
  `tests/test_hubspace_transport.py`). His 60 new files keep their kit paths:
  - the Studio (`web/ops/showcase/`, `conductor/module/pair.py`, `scene_gradient.py`) and the module page's
    `preset-preview.js`;
  - root docs (`VWBD-HANDOFF.md`, `CONNECTION-REPAIR.md`, `VIEWPORT-REPAIR.md`, `VALIDATION.md`, `LOCAL-TESTING.md`,
    `FORK.md`, `VWBD-CLIENT-PROMPT.txt`, `MANIFEST.sha256`). `VWBD-HANDOFF.md` and `MANIFEST.sha256` are Oct 2
    records and historical: the handoff's pending motion test was answered by run ed22fc, and the current state is
    this file and runbook §5a;
  - the standalone tracker `TEST-CENTER.html` (and `open-test-center.bat`);
  - `docs/*.md|json` and `docs/bench-capability-check/` evidence;
  - `tools/update_studio_geometry.py` and `workshop/validated-windows-requirements.txt`;
  - his unittest and JS tests in `tests/`.
- **Taken out:** main's superseded frame-layout design (929c0d2: `FrameLayout`, `frame_offset`, the Calibrate tab's
  frame-offset field, the 60-entry default simulator, their tests and 21 screenshots) went back to `1eb8551` (D54). Every kit path (`conductor/`,
  `config/`, `web/`, `workshop/`) now equals Paris's tree, except the two kept main changes below.
- **Kept from main:** D51 (the same fix Paris made; his version kept), D53 (accounts-route disconnects) with its
  tests, the ops.js frame-length label, and `.gitleaksignore`.
- **Tests:**
  - five existing repo tests updated. Four assert Paris's intentional changes: preset previews no longer invent
    colours from preset names, and the module page imports a second vendored file, `preset-preview.js`. The fifth,
    the job-list test, gains the four new jobs;
  - added: a test that a frame shorter than the module's pucks still refuses per-head painting, and three SimDriver
    end-to-end tests of `gate_c_safe` / `address_probe_safe` through the real runner and executor (exact restore,
    three separate restore commands, ≥ 1 s apart).
- **Lint:** `ruff --fix` plus `ruff format` over Paris's files. The format is AST-identical, checked for every file.
  One `# noqa: BLE001` with a reason.
- **Docs:**
  - 07 §4, 04 and ERRATA now prescribe `bridge.create_request` (D51); following the old text would bring back the
    "Couldn't reach Hubspace" bug;
  - 04, 06 and the bench runbook updated from VWBD's evidence;
  - D52 marked superseded; D54–D56 added; README's home URL corrected.
- **Not merged, on purpose:** Paris's private runtime (`workshop.env`, which holds the secret key and page password,
  and `workshop-data/`, which holds an encrypted Hubspace token, device ids, backups and telemetry). It stays on
  VWBD's laptop. `NEW STUFF FROM PARIS/` is gitignored.

## What is true on real hardware (Oct 7) — keep the evidence types separate
Two 30-head shop modules at VWBD, each on its own controller:
- **Kingston test** = `bench-kit`.
- **String light 1** = `shop-string-1`, which exists only in VWBD's private overlay.

Earlier, Sep 27, before Paris's fork work: VWBD reported sign-in and token refresh, discovery and registration, and
whole-strand test cards working on the first real kit, plus a Slots-tab select. That report has no run record, so the
select isn't counted as physical.

Sources: `docs/preset-functionality-session.md`, `docs/custom-pattern-session.md`, `docs/bench-capability-check/`,
`docs/vwbd-*.json`, and the package's `CHANGELOG.txt` and `evidence/`.

**Kingston:**
- **Physical (seen on the lights at VWBD):**
  - power off/on; dimming 50→20 % in warm white, and up to 100 %; 2200 K and 6500 K at 50 %; whole-strand red,
    green and blue;
  - Rainbow Road travels between heads; Rainbow Roll flashes in place; Gradient Rainbow shows static pairs; Fade 3
    passes through black; Jump 3 at +5 gives sharp RGB changes;
  - Gate C T1 frame write (red→green);
  - address map: 0 → head 1, 1 → head 2, 29 → head 30, 30–59 → nothing; the head-15 marker; a red→purple→blue
    30-head painted gradient;
  - motion code 1 at speed 30 moved separated blocks (bounded: 20 s at 30 %, Oct 2).
- **Cloud (Afero readback):**
  - Gate A dump: 3 slots × 60 entries, CCT 2200–6500, speed −10…10, 33 presets, 9 programs;
  - full effect-header readback;
  - exact slot restores after every test.

**String light 1:**
- **Physical:** whole-RGB responded in the 90 s red/blue identification ("one red and one blue"); an earlier 20 s plain
  blue wasn't seen ("didnt see a change").
- **Custom frames: NOT confirmed.**
  - Run `9603dd` was answered "No — whitish pinkish".
  - Run `20261007T010607-gate_c_safe-30ed44` shows a cloud match and an exact restore, but its physical question is
    unanswered.
  - No T1 flag and no map. Never copy Kingston's.

**Both kits together:**
- **Physical:** after one paired Rainbow Road burst, both showed a rainbow; phase alignment is unknown ("hard to tell").
  The 0 ms host send skew is host timing, not proof the strands started together.
- **The paired per-head gradient has never been sent to hardware:** preflight correctly blocks String light 1.
  Golden Hour's 30-colour gradient was shown on screen only.

## Verified in sim on the merged tree (and how) — 2026-10-07, macOS, Python 3.12
- `make lint`: clean.
- `uv run pytest`: **998 passed**, 18 failed, all in `tests/test_deploy.py`. That is the Linux-only venue installer
  run on macOS; it fails identically on Paris's tree, he changed nothing under `deploy/`, and CI runs ubuntu-24.04.
- Paris's own suites: 72 Python tests pass; `node --test tests/*.test.mjs` 19 pass; `node tests/validate_live_import.cjs`
  passes (88 base cases, 22 variants).
- `uv run python -m conductor.soak`: **9/9 PASS** (`docs/reports/sim-acceptance.md` regenerated): p95 3.48 s,
  queue ≤ 18, heals ≤ 6.1 s, 0 tier downgrades, writes to one device ≥ 1.0 s apart.
- **Not run:** `make e2e`. The pinned Playwright needs `chromium_headless_shell-1194`, which isn't installed on this
  Mac. The screenshots are the pre-VWBD set; regenerate them when e2e runs.

- **Follow-up branches** (same Mac, each from a clean worktree):
  - `claude/review-fixes`: 970 passed.
  - `claude/show-path-mapping`: **984 passed**, with `test_deploy.py` excluded.
  - Both: lint clean, JS 19/19, tracker check OK, soak 9/9 PASS.
  - One-off soaks with all nine modules on the 60-entry profile: 9/9 mapped and 9/9 unmapped.
  - Every new regression test was checked to fail on the old code.
  - `framemap.frame_indices` matched Paris's original rule on 200,000 random inputs.
  - `claude/record-map`: **1028 passed** with `test_deploy.py` excluded (984 + 44 in `tests/test_bench_map.py`).
    Lint clean, JS 19/19, tracker check OK; the soak wasn't rerun (nothing in the show path changed). After the
    review fixes, each of 33 guards was removed in turn, and a named test failed each time. They cover the proof
    rule's refusals (including a later Yes/No probe of part of 30–59), the per-kit filter on the partner run, the
    re-check after the profile read, the bench-target match, the revoke and every entry it reaches, the show re-read
    after apply and after a revoke, the yes/no question, and the same-site/JSON guard. Before the review fixes, by
    hand on a local simulator server with two probes marked real by the test seam: the `/ops/bench` card gave the
    reason, showed **Apply map** after the Yes, recorded the map (`/api/devices` then said mapped), and a No took it
    back. That check wasn't repeated after the fixes.

- **`claude/guest-engine-controls` (D60), 2026-10-11, simulator only:** lint clean; **1061 passed** with `test_deploy.py` excluded; JS 19/19 and the tracker check pass. `tests/test_guest_controls.py`
  has 21 engine tests on the virtual clock, plus a guest-reset route test. They cover one write per module at every
  tier (motion plans as one burst) and drift re-asserted to the guest's level, including drift onto the designed
  value; two mutations of the reconciler fail that test. Also covered: power off/on, mood choice vs re-pick, the idle
  reset and the idle scene, the shared cooldown, pause/outage/commissioning refusals, safe look and ops clear, lock
  release, and the rate budget under rapid ops changes. Soak 9/9 PASS (report in the session scratchpad;
  `docs/reports/sim-acceptance.md` not regenerated). The `/ops` chip and button were checked in a browser on a local
  simulator server, with the state response faked to show a guest override.
  **Review fixes (same day):** the idle reset now acts only while a guest setting is in force. Before, with
  `idle_scene` set to something other than the idle hold's mood, the two alternated every few minutes, and the reset
  could undo a mood ops chose later. `idle_scene` must name a mode, or startup refuses. Guest levels are clamped to
  each device's brightness range. A brightness that isn't a whole number is refused. A reset that fell due during an
  outage goes out with the recovery burst, and on resume the idle hold goes before the reset, so each is one burst.
  Re-verified: lint clean; **1089 passed** (`test_deploy.py` excluded); soak 9/9 PASS (scratchpad report). There are
  16 new tests, and each was mutation-checked: all 13 code mutations fail at least one test.

## Known gaps from the merge review — not fixed here (Paris canon; each needs a decision or bench time)
1. ~~The show path ignores the framebuffer mapping~~ **Done in `claude/show-path-mapping` (D57)**, in the simulator
   only. These now read each kit's own map under one rule: the T1 compiler and composer, previews, `/api/devices`, the
   attention list and the 7 am check. A mapped 60-entry module takes T1 canvas looks. An unmapped one is refused for
   T1 with a true D54 message and still takes whole-strand looks. Show-engine T1 through a map has never reached a
   real kit.
2. ~~`/` redirects to the Studio in every mode~~ **Decided and done (`claude/review-fixes`, D56):** the Studio is home
   only when `CONDUCTOR_ENV` starts with `workshop`; elsewhere `/` goes to `/visualizer/`.
3. **Bugs in Paris's code.**
   Fixed in `claude/review-fixes`, each with a regression test that fails on the old code:
   - the gradient restore now keeps preset speed and party-mode;
   - a paired restart with no brightness uses the preset's level instead of returning HTTP 500;
   - pair and gradient errors are logged (device and phase, never credentials);
   - slot cards show the mapped head count;
   - the Presentation link is hidden from testers;
   - the original `address_probe` is retired (D55).

   Still open:
   - Start/Restart effect on a mapped kit rewrites entries 30–59 to OFF. That shifts the slot hash of an app-recorded
     T2 slot if the app fills those entries, which is unknown because no hand-painted app pattern has been dumped
     yet;
   - `effect_probe` ends on the probed slot instead of the previous look. It needs a bench check of whether
     re-entering individual mode reloads the header;
   - a pending gradient record can block the feature (manual recovery: inspect
     `<CONDUCTOR_DATA_DIR>/studio-gradients/active.json`; the `backup-*.json` files keep the originals);
   - whether real Afero readbacks include speed and party-mode, which the gradient fix backs up only when present, is
     unverified on hardware.
4. **Test gaps.** `claude/review-fixes` adds route-level Studio tests (14) and writer-path coverage for `gate_c_safe`,
   `address_probe_safe`, `effect_probe` and both Studio write paths: a non-allowlisted device gets zero requests.
   Still open: Paris's JS tests and the tracker check aren't in `make test` or CI.
5. **The kit builder** (`scripts/make_workshop_kit.py`) would not ship `TEST-CENTER.html`, `tests/`, `tools/` or
   Paris's docs. Until it does, VWBD keeps working from Paris's own folder rather than a freshly built kit.
6. **The simulator still models the 30-entry sibling string.** The real Gate A dump now exists
   (`docs/bench-capability-check/gate-a-result.json`) but isn't the SQHDK03 fixture yet.
7. **Stale text in Paris's own files.** All four items below are fixed in `claude/review-fixes`. Still stale:
   `config/sim/soak.yaml`'s header comment says no motion code has been benched.
   - `workshop/README.md` points to the Oct 2 handoff and says painting unlocks after the original Gate C. On a
     60-entry kit painting also needs the kit's own map: commission per runbook §5a (`gate_c_safe` → `address_probe_safe`
     → record the map).
   - The Studio's motion-demo copy says blocks "travel"; the evidence says only "moved to different heads" (D56).
   - The Gate A card in `jobs.py` says "30-entry frame" (60 passes too).
   - `soak.py`'s report says no effect code has been benched (code 1 has one bounded run).
8. **CI and the e2e suite:** `main` CI was red from Sep 27 (PRs #9 and #10, and the first run of this PR) on two
   `test_deploy.py` watchdog tests. Their stub `systemctl` reported a start 1 µs after boot, so a runner booted
   < 120 s earlier put the watchdog inside its start-up grace and it exited silently. The stub now reports no
   timestamp. `tests_e2e` wasn't run for this merge (see Verified).
9. ~~No in-app way to record a kit's map~~ **Done in `claude/record-map` (D58)**, in the simulator only. **Apply map**
   on an address probe's result card records the identity map, only when all of these hold: Gate C on that very kit,
   and its newest markers run and newest 30–59 run both answered Yes (and any later Yes/No probe of part of 30–59
   too). Anything else is refused with the reason. It writes the ops overlay only (NOT_FOR_GIT) and sends nothing to
   the lights itself; on a canopy module a running show then re-reads it and sends its current look again, through
   the map. Those two `address_probe_safe` runs now ask yes/no questions (Paris's job: its writes and restore are
   unchanged), so probes saved before this branch don't count. It has never run against a real kit. The runbooks
   (§5a, install day, controller swap) use the button.

## Next step
000000. **The preset-options stack (D74–D77), in this order:** Alon reviews and merges A
    `claude/fin-visualizer`, B `claude/motion-every-scene`, C `claude/ipad-review-menu`, then D
    `claude/preset-options-catalog` (merge commits). Then, on the show box and only when Alon asks:
    1. export any live Lab publication (GET `/api/lab/export`), because D's ids, order and ladders make it unusable;
    2. deploy D at the venue, before commissioning custom-1 to custom-3 (D's painted designs change their slot
       hashes); hard-reload `/ops` and `/ops/showcase`;
    3. bench item 6 on a kit: does a colour step snap or fade, and does a level step read as a breath or a blink;
    4. the account rate probe (`docs/runbooks/account-rate-probe.md`), before `live_enabled`;
    5. Gate R on Modules 1–2, then on the nine show kits with Rainbow Row Sunset #2 (9 PUTs a step);
    6. Alon's `motion.stepped.live_enabled` PR;
    7. the client session: review the 18 options on the iPad, change them in the Lab, promote the chosen option onto
       its #1 ("Make this the scene") and hide the rest on /ops ("On the iPad");
    8. PR E: export the session's choices into git, settle the names (drop "#1") and the catalog.
00000. **Alon (preset-options PR A, D74):** after #37, review and merge `claude/fin-visualizer` (merge commit); it is
    sim-verified only, changes no scene and sends nothing new to a device. Deploy only when you ask for it: a missing
    or broken `config/fins.json` now fails the preflight before the restart. Then preset-options PRs B and C (in
    parallel, merged after A), then D (the 18-option catalog).
00000. **preset-options PR B (D75, `claude/motion-every-scene`):** review it (its block at the top, including the
    review fixes), then open its PR to `main` ("merge #37 first", already satisfied; merge commit, not squash). Alon merges. Nothing
    changes on real lights until Gate R and Alon's `live_enabled` PR.
00000. **Alon (preset-options PR C, D76):** after #37, review and merge `claude/ipad-review-menu` (nothing changes
    on the iPad until PR D's options are in git); answer its two questions (tier-true cards vs a labelled design
    view; thumbnails on the six cards after PR E). Hard-reload `/ops` and `/ops/showcase` after deploying it.
0000. **Alon (PR G, D73, merged as #37 `3c3ad38`):** deploy it if the box still runs `7b21918` (its last record
    here); then a second scene ~2 s after the first should land on the kits. Decide the kiosk cookie (`SameSite=Strict` → 401s on
    some iPad reloads). Run the account probe once (`docs/runbooks/account-rate-probe.md`), not during a demo.
000. **PR F (D72) merged (#35) and deployed with D70 + D71 (`7b21918`, Oct 18 16:23).**
00. **PR C (D69) is merged and deployed (Oct 18, `0f8f38c`).** Still to try on the kits: tap a scene, dim right
    after it — the level should land when the cooldown ends and the panel should say so meanwhile. Then the
    brightness-readback bench check in the PR C block.
0a. **Alon (PR A, D67):** review CLAUDE.md's two changed lines in the PR (the lead asked stream R to apply them),
    merge PR A (merge commit), then deploy it **on its own** with the paste block in the PR A section above (no
    registry undo: the kits stay Modules 1–2). Then PR B (the live Lab): merge after A, deploy on its own (PR B
    block, "Needs the show box"), reload the Lab once after the deploy. Then PR B2 (the operator repaint): merge
    after B; deploy only after bench item 6 on a show kit with its own Gate C (install, Oct 20–23), with `/ops` open
    for the first repaint.
0. **Alon:** (a) ~~Undo the test-kit Modules 1–2 by Oct 19~~ — cancelled Oct 17: they are the real Modules 1–2. (b) Done Oct 17:
   #24–#27 merged and `6ce971b` deployed. (c)
   Decide D63 (layouts, with Paris), D64 (ripple on: `kiosk.ripple_stagger_s: 1.2`) and D65 (which scenes move); run
   Gate R on Modules 1–2. (d) Release tag for the freeze. (e) On the Mac: show accounts when
   they exist, automatic login, wired + reserved address, restart and pull the plug once (`mac-show-box.md` §1–§2).
   Signing in a show account waits for Home Depot's authorization (06 #20).
   (f) **Design Lab:** merged and deployed Oct 17 (`6ce971b`). All three Lab slices are live;
   before the client session, open `make lab-dev` on the laptop and walk Golden Hour (colour, layout, brightness,
   save, compare, Present, export) and Coastal Tides' Motion section once. Stepped live and the ripple stay off
   (Gate R, D64) — the Lab only shows them.
1. **Before merging:** check staging's `/data/overrides/registry.yaml` for `frame_offset`. Only main's Calibrate-tab
   frame-offset field (Sep 27 – Oct 7) could have written it. The registry forbids unknown keys, so remove it or staging won't load the
   registry after the redeploy.
2. Alon merges this PR. Then `git pull` in the main checkout (it was 84 commits behind) and delete the merged
   `claude/*` remote branches.
3. Merge `claude/review-fixes` after #11 (gaps 2–4 and 7), then `claude/show-path-mapping` (gap 1, D57), then
   `claude/record-map` (gap 9, D58). Check show-path-mapping's 07 §6 sentence (the binding contract's wording is
   yours) and D58's proof rule as written.
4. **Use Apply map on a real kit once before install** (Oct 20–23; feature freeze target Oct 12): so far it has run
   only on the simulator. String light 1 (step 5) is the natural first use. A kit whose probes show anything but the
   plain order gets no map, and that needs a decision before install.
5. At VWBD, with an observer and fresh full-slot backups, for String light 1: its own `gate_c_safe` physical answer →
   its own bounded `address_probe_safe` runs (markers, then `start=30 count=30`) → **Apply map** → only then a live
   paired gradient. Don't clear the preflight to make a demo work.
6. Turn the Gate A dump into the simulator's SQHDK03 fixture (gap 6).

## Needs real hardware (UNVERIFIED)
- **The Design Lab (D66):** Slice C (motion editor) is screen only and needs nothing on hardware; its paces are
  the runner's own (7.5 s on nine modules, 6 s on two), checked only in sim until Gate R. The try-on (Slice B) is
  sim only. On the show box: a module-level try-on on the two kits, Back to
  approved and the bound's end on the lights, and the iPad's refusal toast during it.
- **The other kits.** Kingston is the only kit with a T1 flag and a map. Every canopy module needs its own Gate C and
  address map at install (~5 min each; D25, D54, D57). The show engine has never sent a mapped T1 frame to real
  hardware, and Apply map (D58) has never recorded a map from real runs.
- Formal Gate B (slot select). No recorded run shows a slot select on the lights (the Sep 27 Slots-tab report has no
  run record); Gate C's BEFORE select was confirmed by readback only (CLOUD). Also readback semantics with the kit unplugged.
- **Effect codes:** only code 1 has been tried, bounded. Vendor name, direction, period and phase restart are
  unknown, and `effects.enabled` stays empty. Since D59 no show scene moves, Golden Hour included. Two-controller
  phase alignment is unmeasured.
- Per-puck white and colour recipes through Mylar (Prototype 01), including the six scene palettes at their draft
  brightness (D59: they are RGB-only until then); per-entry CCT/white fields; the hue ramp; 1–10 % dimming smoothness.

  unknown, and `effects.enabled` stays empty, so Golden Hour remains a static glow and says so. Two-controller phase
  alignment is unmeasured.
- **Guest brightness on painted looks (D60):** whether `brightness` dims a strand that shows a custom slot. A
  rainbow-mode dim read back without being seen on Sep 28. Also unbenched: turning back on, which is one PUT that
  starts from power-off.
- Per-puck white and colour recipes through Mylar (Prototype 01); per-entry CCT/white fields; the hue ramp; 1–10 %
  dimming smoothness.
- Real latency, throttling (writes and the 1.8 GET/s true-up reads), token rotation, 401/403 semantics.
- Stale-clock writes, rewriting the showing slot, the 12-puck fault. Whether that fault shows in the cloud (frame
  length, `connected-light-count`) is unbenched (gate 10); until then the check is visual.
- Vignette device profiles and the deadbolt readback.
- The show box itself (now the Mac mini, D61): a real reboot and power cut with automatic login, wired to the show
  router with a fixed address, install from a tag, UPS, the cold spare, and an iPad on the show network.
- iOS Safari: enrollment cookies, beacon on page close, Guided Access.
- **The fin-lit iPad picture (D74):** the 9th-gen iPad's WebKit drawing 650 fins + 270 dots through a stepped run
  and a changing pulse; clear vs frosted Mylar; each kit recorded against its VWBD panel (P-0n) at install.
- **The 18 options (D77):** the breathe and shimmer pictures on the lights (snap or fade; a level step as a breath or a
  blink); Gate R on nine with Rainbow Row Sunset #2; the per-light pictures only after commissioning (until then
  module level, where the eight fin-ring options read as three rings); the dark Goodnight options at 25–26% previewed
  on a kit before the session; the colour study for the 46 new recipes; the review menu's scrolling on the 9th-gen
  iPad in Guided Access.

## Decisions Alon owes
- **D77's fin-ring options** (preset-options PR D, question 1): keep the eight on the fin rings (three visible
  colours at module level; the default) or move them to the tilted ring (4–8 visible colours, "from one spot").
  Also: the 12 options' colours and "#n" names at the client session (D59), "Home Depot orange" with the brand check
  (06 #29), and PR C's two questions (tier-true cards; thumbnails on the six cards after PR E).
- **Which three scenes get the painted slots** (D59). Today Rainbow Row Sunset, Garden Gathering and Golden Hour keep
  custom-1..3, and Lowcountry Calm (the idle look), Coastal Tides and The Goodnight Glow run at module level. Swapping
  is a config change. Also the scenes' brightness drafts, at the physical mood approval. (Golden Hour's technique,
  06 #12, is settled by D59: static.)
- **D63 scene layouts** (with Paris): the six 2-D drafts in #21, viewable in the Studio and the iPad preview in sim.
- **D64 ripple on or off**, and the per-ring reading of 05's spread target (p95 2.00 s per ring in the soak).
- **D65 which scenes move**, the cadence (6 s designed / 7.5 s effective), and turning `motion.stepped.live_enabled`
  on only after Gate R passes; also the stepped step-spread target (p95 2.78 s in sim).
- **Kiosk look** (06 #13, was due Oct 5): client copy and brand sign-off.
- **The feature line on flagged kits** (PR F `claude/flagged-reads-lit` block): today a flagged note alone (D54 on
  Modules 1–2) hides the iPad's technique sentence, although the sentence is still true.
- **Vignette surface** (06 #11, was due Oct 1): thin `/vignette/<room>` pages are built. The Oct 12 fallback
  criteria are in D26.
- **Authorization letter** from Home Depot before any show-account writes (06 #20).
- **A second named person** with the show-account password and inbox (`docs/runbooks/outages.md`).
- Skim D51–D58 in `docs/DECISIONS.md` (D58 is the map proof rule as built).

## Top risks
0. **Cloud motion is coarse and unmeasured on hardware.** Stepped waves step every ~7.5 s with up to ~2.8 s between
   panels in sim; real latency, throttling (per account or per IP) and cut-vs-fade are unknown until Gate R. Live
   stays off until then.
1. **Every canopy kit needs its own Gate C and map** before painted looks can use per-head frames (D57). Recording a map
   is now an Apply map tap on `/ops/bench` (D58), but that has never been used on a real kit. Show-engine T1 through a
   map is proven in the simulator only.
2. **Gate C per kit:** proven on Kingston only, and String light 1's custom-frame tests are a NO followed by an
   unanswered run. Any canopy kit that fails Gate C falls back to T2 (hand-paint in the app, ~15–20 min per module,
   then "record it" on `/ops/module`) or T3.
3. **Real throttling** may differ from the soak's model; the rate-ramp job and the venue box measure it.
4. **The show box (the Mac mini, D61) hasn't been through a reboot or a power cut.** It runs live under launchd;
   do `mac-show-box.md` §1–§2's checks (FileVault/auto-login decision, `autorestart`, wired + fixed address).
