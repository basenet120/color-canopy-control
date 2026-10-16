# The Design Lab — build brief (as run on 2026-10-16; defaults taken for the three "Alon decides" questions)

You are the lead engineer on a live retail activation that installs in four days. Alon, the tech lead, reviews and merges your pull requests from his phone. He is not watching in real time, so make the call, record it, and keep shipping.

## The job
Build the **Design Lab**. It is an edit panel inside the existing Studio (`/ops/showcase/`) that lets Alon sit with the client (Home Depot / Hearst, with Paris from VWBD) and iterate each Color Canopy scene's:
- colours;
- 2-D layout;
- brightness;
- stepped motion.

A truthful 270-light preview updates in under a second. Every version is named and saved, and any two versions can be compared side by side. A full-screen Present mode is for the client. The approved result goes back into git as a reviewed patch.

Second, only if the deploy prerequisite below is met: a bounded, one-tap **try-on** of a draft on the real lights, at module level, followed by one-tap return to the approved look.

Today every style change means editing YAML, opening a PR, merging and restarting. The Lab removes everything but the last mile, without touching any binding contract.

## Alon decides (defaults apply if he hasn't answered)
1. **Can the client change an approved palette colour (D59)?** Default: **yes, as a draft, clearly labelled.** The export marks it "changes an approved colour — needs the client's sign-off and a D59 update".
2. **Where does Present run?** Default: **Alon's laptop browser**, logged in as ops. The enrolled Test iPad stays the guest panel and points at the show Conductor.
3. **Version names.** Default: `Client <Mon DD> v<N> – <note>`, for example "Client Oct 17 v3 – warmer sunset".

## Dates and the freeze (today is Fri 2026-10-16; re-check)
**Calendar:**
- **Oct 19:** VWBD's two test kits stop being canopy Modules 1–2 on the Mac show box.
- **Oct 20–23:** install and commissioning. Painted designs are written into each kit's custom slots during commissioning.
- **Oct 23–25:** tours.
- **Oct 29:** event.

**The freeze.** We are **past the Oct 12 feature freeze** (`docs/runbooks/release-and-freeze.md`). The Lab is a freeze exception Alon grants; record it as **D66**. That is why:
- **Slice A touches no show-path module** (`show.py`, `canopy.py`, `reconciler.py`, `executor.py`, the drivers). It is only new files, one `include_router` line in `app.py`, and the Studio and `/ops` link edits.
- **Slice B** is the only slice that touches the engine. Its seam stays small and fully tested.

**Slices, in order.** Ship A, then B, then C. Never leave a slice half-built.
- **Slice A, by Sat Oct 17 night:** the screen-only Lab in the simulator. Drafts, preview, versions, A/B, Present, export. This alone lets Alon iterate with the client.
- **Slice B, by Sun Oct 18:** the try-on.
- **Slice C:** the stepped-motion editor and polish.

**Designs freeze before commissioning (D63).** After commissioning, changing a painted scene's design means slot repaints at commissioning (07 §6). After commissioning, the Lab is read-only for painted scenes' T1 design: it says "changing this needs a slot repaint at commissioning". Module-level, brightness and stepped edits stay editable.

## Read first (in this order; don't re-research what's there)
1. `CLAUDE.md`. Then `docs/STATUS.md` **from the base branch**. Older branches' copies are stale and say the show box is live; it has been on break-glass hold since Oct 13.
2. `docs/context/07-engineering-contracts.md`, which is binding: §1 live gate, §2 roles, §5 readback, §6 executor and slots, §7 clock and soak, §8 persistence. Also `05` (decided) and `04` (device model).
3. `docs/DECISIONS.md` **D59–D65**:
   - **D59:** the catalog; ids, names and palette colours approved; `brightness_draft` a draft.
   - **D60:** guest brightness.
   - **D62:** partial-canopy copy.
   - **D63:** 2-D field designs; layouts are Paris's drafts.
   - **D64:** the ripple, off.
   - **D65:** stepped motion, live off until Gate R.

   The next free id is **D66**.
4. The code you build on (on the base branch; verify line numbers, they drift):
   - **config:** `config/{scenes,modes,recipes,conductor}.yaml`;
   - **data model and compiler:** `conductor/scenes.py`; `conductor/looks/{schema,compile,composer,field}.py` (`parse_modes`, `cross_check`, `MOTION_WORDS`, `layout_words`, `motion_words`, `STEPPED_FALSE_CLAIMS`, `SteppedTechnique`, `FieldCanvas`, `ModuleSample`, `CompileContext`, `compile_mode`, `_counted`);
   - **design route:** `conductor/routes/design.py` (`mode_design`, `GET /api/canopy/design`);
   - **show wiring:** `conductor/show.py` (`ShowWiring.context(modes=)`, `compile`, `ladder`, `_rehearsing`, `rehearsal_modes`, `tier_allowed`, `_stepped_allowed`). The Gate R rehearsal is the precedent for an override;
   - **engine:** `conductor/canopy.py` (`request_mode`, `_refusal`, `RECOMPILE_SOURCES`, `REHEARSAL_MESSAGE`, `rehearse`, `pause_guest_input`);
   - **reconciler:** `conductor/reconciler.py` (the idle hold, the stale-slot check);
   - **motion:** `conductor/rehearsal.py`, `conductor/sequence.py`, `conductor/canopy_view.py`;
   - **overlay persistence:** `conductor/config.py` (`save_overlay_patch`, `overlay_text`, `overlay_diff`, `NOT_FOR_GIT`, `ConfigError`/`_explain`);
   - **ops routes:** `conductor/routes/ops.py` (`_no_cross_site`, `Ops`, `_engine_call`/`_refused`/`_publish`, `GET /api/ops/overlay`);
   - **the Studio:** `web/ops/showcase/{index.html,studio.js,studio.css,demo-model.mjs}` (its painter already draws `/api/canopy/design`);
   - **previews:** `web/kiosk/canopy-preview.mjs`, `web/ops/stepped-model.mjs`;
   - **tests:** `tests/test_writer_paths.py`, `test_modes_match_scenes.py`, `test_scenes_catalog.py`, `test_roles.py`, `test_static.py`, `tests/fake_compiler.py` (`make_world`, `settings_with`).

## Base branch and delivery
- **Check the stack first.** Run `git fetch origin && gh pr list --state all --limit 15`. The open stack is #21 `claude/canopy-field` → #22 `claude/studio-design` → #23 `claude/stepped-motion`. It holds everything the Lab edits: 2-D fields, sampled module level, the ripple, stepped motion, `/api/canopy/design` and the Studio's design painter.
- **Base on the tip of `claude/stepped-motion`**, or on `main` if the stack has merged. Merge `origin/main` into your branch first: the tip lacks main's #19 merge commit. Don't base on `claude/partial-truth`: it was merged into `claude/partial-canopy`, not main.
- **Branch and PRs.** Work on `claude/design-lab`, with one stacked PR per slice. Each PR body says "retarget to main before merging (stacked-PR trap); merge commit, not squash". **Never merge, approve or enable auto-merge; never edit `.github/workflows/`.**
- **Keep conflicts small.** Prefer new files: `conductor/design_lab.py`, `conductor/routes/design_lab.py`, `web/ops/showcase/lab.js`/`lab.css`. The stack churns `ops.py`, `ops.js`, `show.py` and `canopy.py`.
- **Studio file churn.** Build the Lab as a panel inside the Studio. Split it out to `web/ops/lab/` only if the Studio file churn conflicts badly with #22/#23, and say so in the PR.

## This Mac is the show box (hard rules if you run locally)
- **Port 8000 belongs to the show Conductor**, which is on hold now, and the enrolled iPad points at it. Never bind 8000: `make run` and the tracked `.claude/launch.json` entry `conductor` both do.
- **Your dev server** runs on **8200** with `CONDUCTOR_MODE=sim`, a throwaway `CONDUCTOR_DATA_DIR` in your scratchpad, and every `HUBSPACE_*` and secret variable stripped (follow `scripts/dev_guest_panel.py`). Add a `design-lab` entry on port 8200 to `.claude/launch.json` in your branch, and never start the `conductor` entry.
- **The show box is off limits.** Never touch `~/canopy-show` or `~/Library/Application Support/ColorCanopy/`. Never run `canopy-mac`. Never set `HUBSPACE_LIVE`. Deploying is Alon's job: give him exact commands in STATUS.
- **Simulator only.** Tests never hit the network, and this session never writes to a show account.

## Slice A: the screen-only Lab (no show-path edits)

### A1. Drafts are patches over the parsed config
- **What a draft is.** A **`SceneDraft`** is keyed by an **existing** scene id. It holds:
  - palette: 2–6 named colours (`Mode.preview` caps at 6, even though `Scene.palette` allows 8);
  - the 2-D design (`FieldCanvas`: linear `angle_deg` / radial / sweep, `cycles`, `transform`, `invert`);
  - the module-level `sample` (`centre`/`mean`);
  - `brightness`;
  - the default solid;
  - stepped parameters, Slice C;
  - **per-tier feature copy, seeded from the approved mode**;
  - name, mood and description, flagged as client copy.
- **How a draft applies.** It applies as a patch over the parsed `SceneCatalog` / `ModesFile` / `RecipesFile`. It updates every rung of that mode in lockstep:
  - the T1 `canvas` and the `slot` rung (same brightness; slot copy unchanged) for the three slot scenes;
  - the `modules` sample;
  - the `strand` solid;
  - the `stepped` rung only where the scene already has one;
  - the `preview` hexes;
  - the recipe entries, following `recipes.yaml`'s key rule (the colour name, or `<scene-id>-<name>` when taken).

  The ladder order and slot ownership are copied from the approved mode, never edited. Re-validate with `parse_modes(recipes=…, canopy=…)`. A failure returns the `ConfigError` text with its mode→field path.
- **Keep the copy true.** Copy edits go through the same honesty rules:
  - `MOTION_WORDS` banned outside moving copy;
  - T3 copy must use `layout_words` for the edited layout;
  - `STEPPED_FALSE_CLAIMS` and "every few seconds" for stepped copy;
  - the numerals "nine" and "270" kept, so `compile._counted` can rewrite counts for a partial canopy (D62).

  When the layout changes, regenerate the T3 copy's layout phrase. Never hand the client a free-text box the validator would refuse.
- **Ids and slots never move.** Never invent scene ids: the kiosk, `/api/kiosk/scene`, `POST /api/canopy/mode` and slots key on them. Never rename or drop `lowcountry-calm` (`SAFE_MODE`, `idle_mode`, the start-up look). A draft never changes which scene owns `custom-1..3`.

### A2. Preview: zero writes, true to the tier
- **The endpoint.** `POST /api/lab/preview {scene_id, draft}` compiles through the **real** compiler, never a second palette implementation. It calls `mode_design` with `CompileContext(registry=[], capabilities={}, modes=<patched>, recipes=<patched>, canopy=…)`, and `compile_mode` per tier for the sentences. It returns:
  - 270 head colours and 9 module colours;
  - the ladder;
  - each tier's feature sentence as the iPad would show it;
  - validation errors.
- **The renderer.** Feed the Studio's existing painter from this response, with a "draft" flag. Do not edit `demo-model.mjs`'s `SCENES` and do not add a second copy of the palette maths. Where the Studio still falls back to `SCENES`, route that tile through the server design.
- **Views:**
  - **T1 painted (all 270 lights)**, **T3 module level (one colour per strand)** and **Strand (one solid)**;
  - **"What the lights can show today"**, read from the registry and bench flags: on the Mac today that is T3 on Modules 1–2 only. Draw nothing lit that the running tier wouldn't light;
  - brightness as a separate level, never multiplied into colours (D59);
  - the Simulator badge in sim.
- **Colour reality, one line, not a lecture:**
  - recipes are RGB-only and per-puck white is unverified;
  - saturated orange, yellow and purple render weakly on these pucks (04), so in that range show a small "check on the real strand" hint.

### A3. Versions, A/B, Present
- **Storage.** Drafts and versions live in `<CONDUCTOR_DATA_DIR>/overrides/looks.yaml`, following `save_overlay_patch`: validate the whole proposed state, write a history copy to `overrides/history/looks-<UTC>.yaml`, then `tokens.atomic_write` at 0600. Never write into `config/`. In Slice A the show never reads this file; it is the Lab's own store.
- **Versions.** Each save is a named version. Versions can be listed, compared and restored; undo restores the previous version. Every scene shows a badge: **approved** (git), **draft** or **in try-on** (B).
- **A/B.** Show approved vs draft, or any version vs any version, side by side.
- **Present.** A full-screen mode with no chrome and the scene name large. Arrow keys and swipe step through scenes and versions. It must work on a laptop at 1440×900 and on an iPad at 1180×820 with touch.
- **A corrupt `looks.yaml`** never breaks the Studio or the Conductor. On a load failure the Lab shows the error and an empty store, `/ops` gets an attention item, and the bad file is kept aside. Test it.

### A4. Export
- **The endpoint.** `GET /api/lab/export?version=…` returns a git-applyable patch that rewrites **only the changed scenes' blocks in place**. It keeps comments and the YAML anchors (`&sunset-design` / `*…`). It covers `config/scenes.yaml`, `modes.yaml`, `recipes.yaml` and the pinned `tests/fixtures_scene_frames.json`, and nothing else; `demo-model.mjs` is untouched.
- **The decision record.** Attach a drafted DECISIONS entry that lists approved-colour changes (D59) and layout changes (D63).
- **The page.** It offers "copy patch" and "download".
- **The round-trip proof.** Export of an unchanged version must equal the parsed config (`model_dump` equality of `ModesFile`/`RecipesFile`/`SceneCatalog`, not byte equality). After applying an edited export, the whole suite passes with only the pinned-fixture updates in the patch.
- **The extracted validator.** Pull the D59/D63/D65 scene↔mode invariants now in `tests/test_modes_match_scenes.py` into a **pure validator**. `/api/lab/preview` and those tests both call it, and the existing tests pass unchanged. Do this after export works if time is tight.

### A5. Access and observability
- **The API.** `/api/lab/*` is ops only: `Depends(require_ops)` plus a router-level `_no_cross_site` (JSON only, same-origin). Register it in `app.py` before the StaticFiles mounts. Link the Studio's Lab tab from the `/ops` Pages card.
- **Role tests.** Add negative tests in `tests/test_roles.py` for kiosk, tester and vignette on every Lab path.
- **No new role.** A "client viewer" role would change the binding 07 §2. Write it into D66 as an option for Alon.
- **Assets.** Vendored, with no `http(s)://` in page assets (`tests/test_static.py`) and system font stacks.
- **Telemetry.** Record every save, export and try-on with `services.telemetry.record(...)`.

## Slice B: try-on on the real lights (module level only)

### Getting it onto the kits before Oct 19
The try-on is only useful on the real kits if all of this happens before Oct 19:
1. Alon merges #19 → #23 and the Lab PRs, retargeting each to main.
2. He runs `git -C ~/canopy-show switch main && git -C ~/canopy-show pull --ff-only && canopy-mac install && canopy-mac start`. This ends the hold Paris's demos rely on, so coordinate it with Paris.

In STATUS, give Alon this as one copy-paste block, plus `canopy-mac stop --yes` to hand the box back. Say plainly that if the stack isn't merged by Sat night, the kit try-on happens in the Hubspace app or on the module page, and the Lab stays screen-only.

### The trial override (the only show-path change)
- **Where it lives.** It is a narrow seam in `ShowWiring`, modelled on the Gate R rehearsal's override, **for the trial scene only, and only at the `modules` and `strand` tiers.**
  - `ladder`, `t1_target`, `slot_expectations`, `stale_slot` and every T1 compile keep reading the effective (git) config. So the reconciler can never see a trial as a stale slot, and never rewrites `custom-N`.
  - For a painted scene, the trial ladder is `[modules, strand]`, and the Lab says "trying on at module level: one colour per strand; the painted design isn't on the lights".
  - No parallel `preview_plan`, no direct driver or executor calls, and no module-page leases for the canopy.
- **Starting a trial.** It is an ops action: `request_mode(scene_id, source="lab")`, with the trial landing as **one burst** (no ripple) through a small `ripple=False` seam.
- **When a trial is refused.** Start is refused while the engine is stopped, commissioning, paused or in an outage.
- **The guest during a trial.** `"lab"` bypasses the kiosk cooldown, like ops. So the Lab enforces its own minimum gap between accepted applies on the server (`kiosk.cooldown_s`, 8 s) and debounces double-taps. While a trial runs, add a `_trial` check to `_refusal` under `source == "kiosk"`: it returns a plain `TRIAL_MESSAGE` with `retry_after`, like `REHEARSAL_MESSAGE`.
- **Confirm and cost.** Starting needs a confirm sentence naming the modules it writes to. Each apply costs more than one write: a re-assert 4 s later and up to 3 early re-sends per module. So the UI has an **Apply** button, never streaming sliders, and a cost line such as "sends 2 writes now, up to ~8 over the next 10 s".
- **How a trial ends.**
  - **Bounded:** `min(lab.trial_max_s, kiosk.idle_return_s)`. `lab.trial_max_s` is validated to be ≤ `idle_return_s` (180 s), so the idle hold, which would otherwise swap the look under a live override, never runs first. A trial of `lowcountry-calm`, which the idle hold skips, still ends at its bound.
  - **Cleared by any other look:** any request from a source other than `"lab"` or the `RECOMPILE_SOURCES` clears the override in the same step, before `compile_best`. That covers ops, idle, safe look, guest after the trial, and commission. So do "Back to approved", a pause and an engine stop.
  - **Never persisted:** a restart comes up with no override and no "in try-on" badge.
- **What the iPad shows.** It shows the trial's true state through the existing `state.lit` path. `kiosk.js` `buildCards` keys only on ids, so add a catalog version to that key in case the catalog changes.
- **Writer paths.** Add the trial path to `tests/test_writer_paths.py` (`WRITER_PATHS`, `COVERED_MODULES`) with a denied-for-non-allowlisted-device test.
- **Required tests (virtual clock):**
  - a trial on `golden-hour` with a commissioned `custom-3` record makes **zero slot writes and no stale-slot alert** over 20 virtual minutes, before and after the revert;
  - the idle return clears the override;
  - a `lowcountry-calm` trial ends at its bound;
  - a restart mid-trial comes up clean;
  - the kiosk is refused during a trial;
  - the server-side apply gap holds;
  - one burst with the ripple set to 1.2 s;
  - refusals in outage, pause and commissioning.

### Approved looks reach the show only through git
Export → PR → Alon merges → `canopy-mac install` (one restart; the sign-in survives). **Out of scope for v1:** hot-applying config to the running show, slot repaints, and firmware presets in the Lab.

## Slice C: the stepped-motion editor
Per-light animation across 270 lights is impossible through Afero. Looping slot rewrites are forbidden (07 §6), and the nine controllers run unsynced clocks. Offer exactly:
- **Stepped waves (D65)**, for scenes that already have a stepped rung:
  - **Edit:** direction, `steps_per_cycle` (3–12), `step_s` (4–12), `run_s` (≤ 180 and ≤ `idle_return_s`) and the design they move.
  - **Preview:** the sim plays the exact steps the Conductor would send (`stepped-model.mjs`) at real pacing: about 7.5 s per step on nine modules, and 6 s on the Mac's two kits. That pace comes from the planned 72 writes/min, which is the 90/min account cap less the 20 % reserve.
  - **On live hardware,** stepped stays refused until Gate R passes and Alon sets `motion.stepped.live_enabled`. The Lab shows that sentence and **never flips it**.
  - **Limits:** the idle scene never steps, and a trial never steps.
- **Ripple arrival (D64):** a screen-only preview of `ripple_stagger_s` (≤ 1.2 s, ≤ 3 rings). The Lab never changes the live value.
- **Custom-slot effects:** greyed out, "not enabled: no code has passed the bench (06 #4)".
- **Copy:** moving copy comes only from the schema-checked `moving_feature`.

## How to run this with ultracode (one workflow per phase, reading results before the next)
1. **Understand (short).** The facts above came from a full codebase map and an adversarial review on Oct 16. Verify the ones you rely on against the base, then write `docs/design-lab/PLAN.md` (files, API shapes, tests, the slice schedule against today's date) and commit it.
2. **Contracts.** The `SceneDraft` schema, the patch function, the `/api/lab/*` shapes, the `looks.yaml` format, and for B the trial-override seam. Land them first so parallel agents build against stable seams.
3. **Implement.** Fan out with worktree isolation, about 4 agents at a time:
   - draft + patch + preview API;
   - versions + overlay;
   - export + extracted validator;
   - Studio Lab panel + Present;
   - then B (trial seam + tests);
   - then C.

   Every agent ships code plus tests (`make_world`/`settings_with` on the virtual clock). Integrate sequentially and resolve conflicts yourself.
4. **Verify,** with adversarial reviewers, each with a distinct lens:
   - **contracts:** 07 §1/§2/§6/§8, the rate budget, slot ownership, never flipping a live gate;
   - **truth:** every preview, badge and sentence matches what the lights at the running tier would show; partial canopy (D62); no T1 stale-slot path;
   - **resilience:** corrupt overlay, trial vs idle hold, outage, pause, commissioning, restart, double-tap;
   - **client UX:** can Alon change Golden Hour's pink, compare it with approved, present it full screen and export it in under 30 seconds on a laptop and an iPad?;
   - **mutation:** break each guard and confirm a named test fails.

   Fix what survives, then re-verify. Run the full suite, `make lint`, `node --test tests/*.test.mjs` and `make soak` (pinned `config/sim/acceptance.yaml`, never tuned). Commit the regenerated `docs/reports/sim-acceptance.md` only in the last slice's PR.
5. **Report.** Update `docs/STATUS.md` (PR stack in merge order, what's verified and how, what needs the real kits, Alon's copy-paste deploy block), add **D66** (the freeze exception, the Lab, the role option), and write each PR description.

**UI verification.** Locally, use the browser preview on port 8200 at 1440×900 and 1180×820 (touch), and look at the screenshots yourself before calling a surface done. In a cloud session without a browser, verify with `node --test` on the page modules plus TestClient HTML and JSON checks, and list every unscreenshotted surface in STATUS under "needs a look on the Mac". Run `make docker-smoke` only if Docker exists; otherwise CI covers it.

## Done means
- **The client workflow, in sim on port 8200.** Alon opens the Studio's Lab tab and picks Golden Hour. He changes a colour, the layout angle and the brightness, and sees the 270-light canopy and each tier's sentence update in under a second. He saves "Client Oct 17 v2", compares it with approved, presents it full screen, and exports a patch. The patch applies cleanly to `config/` and passes the whole suite.
- **Slice B, if built.** A try-on on the simulated canopy shows on `/visualizer` and the iPad panel, ends at its bound or on "Back to approved", and makes zero slot writes.
- **Failure safety.** A corrupt `looks.yaml` leaves the Studio and Conductor working.
- **Hard limits, each with a test.** No Lab path can:
  - write to a device outside the engine;
  - flip `live_enabled` or `ripple_stagger_s`;
  - take or rewrite a show slot;
  - bind port 8000.
- **CI is green** on every PR: ruff, pytest, gitleaks, the Docker build and the smoke test.
- **STATUS is current.** It says what is verified in sim, what still needs the real kits, and the exact deploy block.

## Don't
- Build anything that pretends the canopy can do per-light animation, synced effects, or anything else the bench hasn't proven.
- Write to `config/*.yaml` at runtime, hot-swap the running show's config, or make the show depend on `looks.yaml`.
- Add roles, widen path rules, enable effect codes, or turn on stepped live or the ripple.
- Stop to ask. For an unknown, take the default in `06` or above, record the judgment call in `docs/DECISIONS.md` (D66+), and keep going.
