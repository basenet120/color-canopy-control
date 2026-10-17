# Status

_Last updated: 2026-10-17 (Design Lab Slice C integrated as #27 `claude/design-lab-motion`; all three Lab slices built; see the PR stack). Before that 2026-10-16.
**The show box is the Mac mini** (D61, merged), live; **held** since Oct 13 evening so
Paris could demo the Hubspace app (`canopy-mac start` hands back). **Temporarily, canopy Modules 1–2 are VWBD's two
test kits** on the bench account — **undo by Oct 19**. A five-PR stack (#19 → #23) makes a partial canopy honest and
adds canopy-wide 2-D designs (D63), a true 270-light iPad preview, the ripple arrival (D64, off) and stepped waves
across the 3×3 panels (D65, sim-first, off on hardware until Gate R). Before that (Oct 7): Paris's VWBD field work is
canon (D54); Kingston is commissioned for per-head frames on VWBD's laptop; D57/D58 maps._

## PR stack (merge order — merge commits, not squash)
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
    If the stack isn't merged before the Oct 19 undo, the kit try-on happens in the Hubspace app or on `/ops/module`
    and the Lab stays screen-only until after it.
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
- **Merged:** PR #18, `claude/mac-show-box` (D61), as `9c4c7aa` on Oct 13.
- **Items 1–6 below are older and all merged** (#11–#16); kept for their notes.
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
- **Hardware still needed:** Gate R rehearsal on the two test kits (before the Oct 19 undo) and on the nine show kits
  during install week (`docs/runbooks/stepped-rehearsal.md`); cut-vs-fade of a strand colour change (bench item 6) and
  start skew across kits (gate 12); Gate C + map + commissioning per kit before any per-head design shows; the iPad
  preview's orientation at the venue (`kiosk.preview`). Freeze the designs before install commissioning.

## Two test kits as canopy Modules 1–2 (temporary, live) — 2026-10-13
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
- **Until D62 is deployed on the box:** the T3 feature line still says the palette steps "across the canopy column by
  column" and the ops Safe look still says "all nine" — avoid the Safe look button in front of guests. Nobody may
  drive these two kits from the Hubspace app, the Studio or another Conductor meanwhile: the reconciler puts the Mac's
  look back within seconds.
- **Held since Oct 13 evening:** at Alon's request the Conductor was stopped (`canopy-mac stop --yes`) so Paris could
  show the Hubspace app driving the kits; the hold survives a reboot; `canopy-mac start` hands back (it puts
  Lowcountry Calm back on both strands).
- **UNDO by Oct 19, before any real canopy kit is registered on Oct 20** (otherwise registering a real Module 1 or 2
  from the canopy account fails with 409 "belongs to the bench account", and slot records or calibration taken on the
  shop kits could follow the real modules): tell VWBD; `canopy-mac backup`; `canopy-mac stop --yes`; `cp -p
  overrides/history/registry-before-canopy-test.yaml overrides/registry.yaml` (in the data folder, mode 0600); check
  with `load_registry` that `canopy-r0c0`/`r0c1` are back on account canopy with no id; `canopy-mac start`. The kits
  keep the Mac's last look. Never commit the /ops overlay diff while this is in place: it proposes `account: bench`
  for `config/devices.yaml`.

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
0. **Alon:** (a) **by Oct 19, undo the test-kit Modules 1–2** (section above). (b) Review and merge #24 (it carries #20–#23 to
   `main`; merge commit); then between demos `git -C ~/canopy-show switch main && git -C
   ~/canopy-show pull --ff-only && ~/canopy-show/deploy/mac/canopy-mac install` (restarts; the sign-in survives). (c)
   Decide D63 (layouts, with Paris), D64 (ripple on: `kiosk.ripple_stagger_s: 1.2`) and D65 (which scenes move); run
   Gate R on the two test kits before the undo. (d) Release tag for the freeze. (e) On the Mac: show accounts when
   they exist, automatic login, wired + reserved address, restart and pull the plug once (`mac-show-box.md` §1–§2).
   Signing in a show account waits for Home Depot's authorization (06 #20).
   (f) **Design Lab:** merge #24 → #25 → #26 → #27 in that order (each already targets main;
   merge commits, not squash), then run #26's deploy block (in the PR stack above) when Paris can
   spare the box; a kit try-on on Modules 1–2 needs it before the Oct 19 undo. All three Lab slices are built;
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

## Decisions Alon owes
- **Which three scenes get the painted slots** (D59). Today Rainbow Row Sunset, Garden Gathering and Golden Hour keep
  custom-1..3, and Lowcountry Calm (the idle look), Coastal Tides and The Goodnight Glow run at module level. Swapping
  is a config change. Also the scenes' brightness drafts, at the physical mood approval. (Golden Hour's technique,
  06 #12, is settled by D59: static.)
- **D63 scene layouts** (with Paris): the six 2-D drafts in #21, viewable in the Studio and the iPad preview in sim.
- **D64 ripple on or off**, and the per-ring reading of 05's spread target (p95 2.00 s per ring in the soak).
- **D65 which scenes move**, the cadence (6 s designed / 7.5 s effective), and turning `motion.stepped.live_enabled`
  on only after Gate R passes; also the stepped step-spread target (p95 2.78 s in sim).
- **Kiosk look** (06 #13, was due Oct 5): client copy and brand sign-off.
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
