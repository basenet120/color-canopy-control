# Canopy only + the live Lab — build plan

_Written Sat 2026-10-17 evening by the planner, on `claude/canopy-only` at `6ce971b` (main: the Design Lab #25–#27
merged and deployed on the Mac). Every symbol and line number below was read on that commit; line numbers drift, so
search by name. Inputs: CLAUDE.md, `docs/STATUS.md`, `docs/context/05` and `07` (binding), `docs/DECISIONS.md`
D59–D66, `docs/design-lab/PLAN.md` + `PROMPT.md`, and the code._

Alon's request (Oct 17, on site, after the guest panel worked on the two live kits): **"1. remove all the stuff that
isn't the canopy control. 2. make the test lab work on the live system so that I can just think of everything as one
system and we can live iterate on site on the live show at the end of the install before guests arrive."**

**Hard rules for every agent (repeat them in each agent prompt).** This Mac mini is the LIVE show box right now (the
Conductor on port 8000 drives two real kits). Never bind port 8000 (`make run` and the `conductor` launch entry do);
never touch `~/canopy-show`, `~/Library/Application Support/ColorCanopy` or `~/Library/Logs/ColorCanopy`; never run
`canopy-mac`; never set `HUBSPACE_LIVE`; no network in tests; Python only via `uv run --offline`; prefix any heavy run
with `nice -n 15` (`nice -n 15 uv run --offline pytest -q`); never edit `.github/workflows/`; never push, merge,
approve or enable auto-merge; never use bare `git stash`; don't edit `CLAUDE.md` or `.claude/` (drafts for Alon are in
§6.4); no new dependencies (`canopy-mac install` runs `uv sync --frozen`). Dev servers only on ports 8200–8299
(`scripts/dev_design_lab.py --port 82xx`). `tests/test_deploy.py` fails on macOS (known) until stream R deletes it.

---

## 0. Streams, branches, integration

| Stream | What | Branch (worktree) | PR |
|---|---|---|---|
| **R** removal | §1: vignettes, the Ubuntu venue box, the VWBD workshop kit + Paris tooling, the Studio's shop-kit pair; old-data tolerance; D67; 07 §1/§2/§10 + 05 text | `claude/canopy-only` (from `6ce971b`) | **PR A** → `main` |
| **P** publish server, part 1 (**B1**) | §2 minus §2.6–§2.7: the published-looks overlay, startup load + fail-safe, the hot-apply checklist, the T1 hold, engine/show seams, publish / revert / undo routes, the conflict guard, the B1 painted-scene lock (§2.6.0), tests; D68; 07 §8 text | `claude/live-lab` (from `6ce971b`) | **PR B** → `main`, "merge #A first" |
| **P** part 2 (**B2**) | §2.6–§2.7: the operator repaint job (quiet window, slot locks, queue + 2-min gate), its reconciler guard, its tests; 07 §6 text | `claude/live-lab-repaint` (from `claude/live-lab` once B1's tests pass) | **PR B2** → `main`, "merge #B first" |
| **U** Lab UI | §3: one system in the Lab (live / published / draft), Publish, Revert, Undo, history, conflict, version files, repaint progress (shown only when the server sends `repaint`) | `claude/live-lab-ui` (from `6ce971b`) | merged into `claude/live-lab` → PR B |

**Why B is split (review, ops-ux):** the repaint does nothing on today's two kits (no Gate C or map on the Mac), is the
part most likely to slip, and its first real use is an unbenched active-slot rewrite (04 item 6). B1 is everything Alon
needs for module-level iteration and deploys on Oct 19; B2 lands after B1 is live and bench item 6 has been run once on
a show kit with its own Gate C (from `/ops/module`). Alon's decisions on the repaint (after a confirm, 2-min rule) are
unchanged; only the delivery date moves (D68 says so). P builds B1 first, then B2; U's UI already handles both.

The three streams start together from `6ce971b` and never talk; U builds against the JSON shapes in §2.11 (with the
fixtures in `tests/fixtures_live_lab/*.json` that U writes from §2.11 verbatim and P's route tests load and check
against the real answers — the one place the two meet). **Integration (orchestrator, after all three report):**
1. `claude/live-lab` ← merge `claude/live-lab-ui` (merge commit). Conflicts expected: none (disjoint files, §4).
2. `claude/live-lab` ← merge `claude/canopy-only` (merge commit). Conflicts expected only in the shared files of
   §4.2, each with its resolution rule there.
3. Full suite, lint, node, soak on `claude/live-lab`; STATUS for PR B. PR B's body: "merge #A first, then retarget
   nothing — both target main (memory: target main, not stacked bases); merge commits, not squash".
4. `claude/live-lab-repaint` ← merge `claude/live-lab` (after step 3); full suite again; PR B2's body: "merge #B
   first; deploy only after bench item 6 has been run once on a show kit with its own Gate C".
Pushing and opening the PRs is outside this run (never push).

---

## 1. Stream R — the removal inventory (PR A, `claude/canopy-only`)

### 1.0 What stays (don't touch)
Engine, executor, drivers (Afero + sim), live gate, accounts/auth/tokens, registry, bench gates + jobs + Apply map +
`/ops/bench` (commissioning each kit), `/ops/module` (per-kit test, T2 record, calibration), `/ops`, `/ops/accounts`,
the iPad guest panel (engine target), `/visualizer`, the Studio + Design Lab, the Mac show box (`deploy/mac/**`,
`docs/runbooks/mac-show-box.md`, `tests/test_mac_box.py`), the soak, `Dockerfile` + `.dockerignore` (CI builds and
smoke-tests the image), `railway.json` + `.railwayignore`, the cloud / Remote Control docs and scripts
(`docs/CLOUD_SETUP.md`, `docs/CLOUD_SESSION_PROMPT.md`, `docs/REMOTE_CONTROL_SETUP.md`, `scripts/cloud_*.sh`,
`scripts/macmini_remote_control.sh`), hardware evidence (`docs/bench-capability-check/*`, `docs/vwbd-*.json`,
`docs/bench-commissioning-proof.json`, `docs/preset-functionality-session.md`, `docs/custom-pattern-session.md`,
`docs/current-live-test-session.md` and its `docs/live-workshop-2026-09-28.test-center.json` — both cited by 04, 06 and
`stepped-rehearsal.md` as the Sep 28 hardware record, so kept as evidence although the second is a TEST-CENTER file),
`research/**`, `config/sim/acceptance.yaml` (pinned; it names no vignette — verified with grep, so nothing in it
changes), `.gitignore`'s `workshop.env` / `workshop-data*/` lines (they keep a stray private runtime out of git).
`CONDUCTOR_ENV=venue` stays: it is the Mac's environment name (D61), not the Ubuntu box. The word "venue" (the
place) stays in prose; only the Ubuntu **venue box** goes.

### 1.1 Delete (git rm)
- **Vignettes:** `conductor/vignettes.py`, `conductor/routes/vignette.py`, `config/vignettes.yaml`,
  `web/vignette/{index.html,vignette.css,vignette.js}`, `tests/test_vignettes.py`, `tests/test_vignette_routes.py`,
  `tests/test_vignette_afero.py`, `tests_e2e/test_vignette.py`,
  `docs/screenshots/vignette-{entry,living,bedroom,garden}-ipad.png`.
- **Ubuntu venue box:** `deploy/venue/**` (23 files: `bin/canopy-{backup,kiosk-enroll,logs,restart,restore,start,
  status,stop}`, `box.env.example`, `conductor.env.example`, `docker-compose.yml`, `install.sh`,
  `lib/{common,kiosk-prestart,preflight,watchdog}.sh`, `systemd/*` ×7), `scripts/release.sh` (a linux/amd64 Docker
  release for that box; the Mac deploys from a git checkout), `web/kiosk-resting/index.html` (the box's Chromium
  resting page; D61 didn't carry the Chromium kiosk over; nothing mounts it), `tests/js/resting_test.mjs`,
  `tests/test_deploy.py` (the venue box + release.sh; its **two** generic checks — the env-doc check and the
  secret/identifier scan of `deploy/` — move to `tests/test_mac_box.py` first, §1.3),
  `docs/runbooks/venue-box.md`.
- **VWBD workshop kit + Paris tooling:** `workshop/**` (7), `conductor/workshop.py` (the workshop launcher,
  `python -m conductor.workshop`), `scripts/make_workshop_kit.py`, `scripts/dev_guest_panel.py` (move its
  `UNSAFE_ENV` tuple into `scripts/dev_design_lab.py` first, §1.2), `tools/update_studio_geometry.py` (regenerates the
  Studio geometry from workshop maps on Windows; `tools/` becomes empty), `TEST-CENTER.html`, `open-test-center.bat`,
  root `CONNECTION-REPAIR.md`, `FORK.md`, `LOCAL-TESTING.md`, `MANIFEST.sha256`, `VALIDATION.md`, `VIEWPORT-REPAIR.md`,
  `VWBD-CLIENT-PROMPT.txt`, `VWBD-HANDOFF.md`, `docs/interface-test-log.md`,
  `docs/interface-troubleshooting-workflow.md`, `docs/test-cases.json`, `docs/testing-baseline.json`,
  `docs/source-changes-from-user-baseline.json`, `docs/release-validation.json`,
  `docs/initial-tracker-validation-2026-09-28.md` (each referenced only by the files above),
  `tests/validate_live_import.cjs`, `tests/test_workshop.py`.
- **The Studio's two-shop-kit pair:** `conductor/module/pair.py`, `conductor/module/scene_gradient.py`,
  `tests/test_studio_pair.py`, `tests/test_scene_gradient.py`.

### 1.2 Edit — code (every reference found by `git grep -n -i -E 'vignette|workshop|PAIR_IDS|apply_pair|
scene_gradient|module\.pair|dev_guest_panel|deploy/venue|TEST-CENTER|kiosk-resting'`)
| File:line | Change |
|---|---|
| `conductor/app.py:30, :190` | drop `vignette` from the routes import and `app.include_router(vignette.router)` |
| `conductor/app.py:56, :66-77` | drop `_start_vignettes(services)` and the function |
| `conductor/app.py:171-177` | `/` always redirects to `/visualizer/` (D56's workshop branch goes); docstring/comment updated |
| `conductor/config.py:245-246` | `Role = Literal["bench", "canopy_module", "spare"]`; delete `VIGNETTE_KINDS` |
| `conductor/config.py:290-291` | delete `DeviceEntry.vignette` / `.kind` fields |
| `conductor/config.py:380-386` | delete the `role == "vignette"` checks in `RegistryFile._checks` |
| `conductor/config.py:437-453` `_merge` | **old-data tolerance (§1.5):** (a) an overlay entry whose id is in `REMOVED_DEVICE_IDS` (the **15** vignette ids of `6ce971b`'s `config/devices.yaml`, spelled out literally as a frozenset: `entry-deadbolt`, `living-lamp-1..4`, `living-plug`, `bedroom-lamp-1..4`, `bedroom-plug`, `bedroom-fan`, `garden-string`, `garden-path`, `garden-plug`) or whose patch says `role: vignette` is dropped, not an error; (b) on every **surviving** patch the removed keys `vignette` and `kind` are popped before update/insert (`extra=forbid` would otherwise crash-loop launchd on a full spare entry copied from VWBD's overlay, e.g. `shop-string-1`). New signature `_merge(base, overlay, path) -> tuple[dict, Dropped]` with `Dropped(ids: list[str], fields: dict[str, list[str]])`; `load_registry` stores it on `Registry.dropped: list[str]` and `Registry.dropped_fields: dict[str, list[str]]` (dataclass fields, default empty) |
| `conductor/config.py:481-502` `save_overlay_patch` (every /ops register, bench flag, Apply map, calibration write goes through it) | unpack `merged, dropped = _merge(...)`; remove `dropped.ids` and each id's `dropped.fields` keys from `devices` **before** validating and writing, so the first ops edit after the deploy prunes them (as roles' next `_save` does) and the "Needs attention" line clears |
| `conductor/config.py:534-599` `overlay_diff` | skip `device_id in registry.dropped` and ignore `registry.dropped_fields` keys (else the /ops "download as diff" fails re-validation) |
| `conductor/services.py` (where `services.problems` is filled after `load_registry`, :192-218) | when `registry.dropped`: "The registry overlay still names removed vignette devices (…ids…); they were ignored (D67). The next registry edit on /ops removes them." When `registry.dropped_fields`: "The registry overlay had removed vignette fields (kind/vignette) on …ids…; they were ignored (D67)." |
| `config/devices.yaml:13-20` | delete accounts `entry`, `living`, `bedroom`, `garden`; `canopy` label → "Canopy account (show box only)" |
| `config/devices.yaml:59-84` | delete the Vignettes block (comment + 15 entries) |
| `conductor/roles_api.py:6, :16, :34, :44` | drop the `vignette` principal, its path rule, `Role.VIGNETTE`; grants comment "device ids (tester)" |
| `conductor/roles.py:4, :41-42, :116-142` | `ENROLLABLE = (Role.KIOSK, Role.TESTER)`; error text "Use kiosk or tester."; drop the vignette-grant rule and wording |
| `conductor/roles.py:376-378, :393-394` | drop the VIGNETTE branches of `path_allowed` and `home_path` |
| `conductor/roles.py:87-88` `_Record.from_json` + `:296-317` `_load` | **old-data tolerance (§1.5):** a record whose `role` isn't a `Role` any more is skipped (logged once with its role and fingerprint), never a corrupt file: the others load; the next `_save` drops it |
| `conductor/routes/core.py:47` | drop `Role.VIGNETTE` from the role-label map |
| `conductor/ws.py:27, :51-56` | drop the `vignette:<id>` topic and its role check |
| `conductor/wsauth.py:3` | docstring: "iOS Safari (the guest iPad)" |
| `conductor/routes/devices.py:4-9, :146, :186-222, :272-275` | drop the `vignette` field from the device JSON, the room titles helper, the vignette room groups and the room-page link (`device_page` keeps `/ops/` and `/ops/module/…`) |
| `conductor/module/manager.py:29-38, :138-145` | delete `NOT_A_STRAND`, `module_page_device` (every remaining role is a strand) and the 409 branch; keep `devices()` filtering by `principal.can_device` only. **Don't touch `:56-59`** (stream P edits them) |
| `conductor/bench/runner.py:263` | `entries = list(self.services.registry.devices)` |
| `conductor/rehearsal.py:36-39, :165, :179-181, :221-222, :243-246, :269-281, :364-414, :470-475` | Gate R loses the `vignettes_clean` criterion and the `vignette` report section (accounts, attempts, tokens, alerts); docstring says the IP's other accounts are now only the bench account's |
| `conductor/canopy.py:1231` | the rehearsal-403 alert: "…check the accounts on /ops/accounts before running it again." (no vignettes). **Only this sentence:** stream P owns every other canopy.py change |
| `conductor/sim/profiles.py:18-30, :128-362` | delete the synthesized vignette profiles (`SYNTHESIZED_NOTE`, `_ecosmart_a19_rgb` … `_defiant_deadbolt`, `SYNTHESIZED`, `is_synthesized`) and their branch in `load_profile`; `profile_names()` = `["sqhdk03-sim", "string-20seg", "sqhdk03-sim-60"]` |
| `conductor/guest_panel.py:1-64` docstring | one target: the canopy engine. Delete the WorkshopTarget design notes and `make guest-dev` |
| `conductor/guest_panel.py:85-86` | drop the `scene_gradient` and `pair` imports (and `ModuleSession`, `ModuleError`, `LockError`, `DeviceLockManager` if now unused — ruff F401 says, for imports only) |
| `conductor/guest_panel.py:95-104` | delete exactly `IDLE_RELEASE_S`, `FEATURE_GRADIENT`, `FEATURE_SOLID`, `FEATURES`, `BUSY`, `GUEST_OWNER` (and drop `GUEST_OWNER` from `tests/test_guest_panel.py:33`), each after `git grep -n` shows no use outside deleted code (ruff doesn't report unused module constants). **Keep** `CLOSE_WAIT_S` (`Lane.close`, :227), `HEADS` (`scene_json`, :251), `CHANGING` and the other message constants |
| `conductor/guest_panel.py:261-675, :676-689` | delete `WorkshopDeps`, `WorkshopTarget`, `_module_errors` |
| `conductor/guest_panel.py:980-1026` | delete `choose_target`, `workshop_deps` |
| `conductor/guest_panel.py:1028-1061` `GuestPanel` | drop `self.workshop`; `target()` returns the one `EngineTarget`; `close()` closes it. **Keep `def scenes(self)` (`:1045-1046`) byte-identical** (stream P changes those two lines; leaving them untouched keeps the merge clean) |
| `conductor/routes/guest.py:7, :20, :28` | docstring: `target` is always `"canopy"` (the field stays in the JSON so the iPad's code is unchanged) |
| `conductor/routes/module.py:36-37, :91-170` | delete `PairBody`, `GradientBody` and the three `/api/studio/pair/*` routes and their imports |
| `conductor/gate.py:37` | comment drops "vignettes" (strings at `:69`, `:100`, `:203` say "venue box": leave; tests pin them) |
| `scripts/dev_design_lab.py:6, :27` | define `UNSAFE_ENV` here (copy the tuple from `dev_guest_panel.py:29-38`), drop the import; docstring "as on the show box" |
| `deploy/mac/run-conductor.sh:32` | comment: "(the variables a parent shell might leak)" |
| `deploy/mac/run-conductor.sh:59-71` (`--check`) | before `exit 0`, run `"$ROOT/.venv/bin/python" -m conductor.preflight` and `die` on a non-zero exit. `canopy-mac install` already runs `--check` after `uv sync` and **before** removing the HOLD and reloading launchd (canopy-mac:225-235), so new code that can't load the live data folder never restarts the show; install then exits 3 with the checkout rollback (review R2) |
| `conductor/preflight.py` (new, R; P adds the looks line in B1) | offline, binds no port, read-only: `load_settings`, `load_registry(CONFIG_DIR, CONDUCTOR_DATA_DIR)`, `RoleStore` load of `data/roles/*.json` (in a temp copy, so nothing is set aside), and (B1) `load_live_looks`; prints dropped ids/fields, skipped role records and any looks fallback; exit 1 on `ConfigError`/exception. Test: over a data dir with a vignette patch, a `kind:` key and a vignette session it exits 0 and names them; over a registry overlay naming an unknown non-vignette id it exits 1; the data dir's bytes are unchanged |
| `deploy/mac/canopy-mac:213` | message "This is the Mac show box's helper; it runs only on macOS." (check `tests/test_mac_box.py` doesn't pin the old text) |
| `deploy/mac/conductor.env.example:14, :19, :35` | "canopy first" (no vignette accounts); "from staging or a laptop"; "(these must stay unset)"; add commented lines `# CONDUCTOR_DATA_DIR=` (pinned by run-conductor.sh), `# CONDUCTOR_URL=`, `# RAILWAY_GIT_COMMIT_SHA=`, `# PORT=` (Railway/image only) so the moved env-doc test passes |
| `conductor/routes/ops.py:116-118, :905` (comments), `:950-954` (`ntp_fix`), `:976-978` (the `synced is None` fix) | the 7 am Clock item names the deleted Ubuntu tooling. New text for both fixes: "On the show box run `canopy-mac status` (the Clock line shows the offset from Apple time). If it's off, check the internet, then System Settings → General → Date & Time → Set time automatically, and press Run again. (A staging server can't report this; nothing to do there.)" Keep the `timedatectl`/`TIMESYNC_FLAG` path in `ntp_status` (Railway/Linux); comments drop "venue box" / `deploy/venue`. **R owns these hunks; P owns `mode_list` (:183-189)** |
| comments and copy | `conductor/tokens.py:5`, `conductor/bench/jobs.py:1439` ("A lagging venue-box clock" in a job result → "show box"), `conductor/routes/bench.py:62`, `conductor/routes/design.py:25`, `conductor/routes/ops.py:30`, `conductor/routes/module.py:306`, `conductor/auth.py:11` (Chromium kiosk → the guest iPad) |
| `Makefile:1, :17-20` | delete `guest-dev` (target, comment, `.PHONY` entry) |
| `web/ops/index.html:122-140` | the enrollment card: kiosk or tester only (drop the Vignette radio, `#enroll-vignette`, `#enroll-vignettes`) |
| `web/ops/ops.js:534-538, :637-642, :738-740` | no room-page link, no vignette name hint, no vignette grants |
| `web/ops/module/module.js:1466-1469` | the banner: "…it isn't a strand you have access to." |
| `web/ops/showcase/index.html:23-24` (+ the gradient-apply buttons) | Studio: drop `<option value="both">`, `#pair-status` (and `#pair-kingston`, `#pair-string`, `#pair-summary`), `#restart-pair`, and the controls that post `/api/studio/pair/gradient`; default the select to `bench-kit`; bump `studio.js?v=` (not `demo-model.mjs`'s `?v=show-design-3`, which `lab.js` also imports) |
| `web/ops/showcase/studio.js` | delete `connectPair`, `releasePair`, `sendPair`, `refreshPair`, `heartbeatPair`, `renderPair`, `readGradientStatus` and the paired-gradient apply/restore; every `S.device==='both'` branch takes the single-device branch; `S.pair` goes; the `restart-pair` listener, the `pagehide` pair close and the interval's pair heartbeat go; `S.device` defaults to `'bench-kit'`; drop `PAIR_IDS` from the import |
| `web/ops/showcase/demo-model.mjs:87-108, :176-178` | delete `PAIR_IDS`, `matchingStaticFrames`, `matchingWholeSettings`, `sharedCapabilities`; `displayLayout(scope='canopy', device='bench-kit', maps={})` (default changed from `'both'`, which would now read `CANOPY.bench['both']` and throw) and `const ids=[device]` (scope `canopy`, which the Lab uses, is unchanged). The "String light 1" option (`shop-string-1`) stays by default (§9 item 5) |
| `web/kiosk/kiosk.js:7`, `web/kiosk/panel-model.mjs:222` | comments only ("no look yet") |

The Studio's single-kit controls stay (they are the module page's API with Kingston's ids); stripping them too is
Alon's call (§9).

### 1.3 Edit — tests
| File | Change |
|---|---|
| `tests/test_guest_panel.py` | delete every workshop test (`_workshop`, `_launcher`, the target parametrize rows for workshop, the shop-strand scene/brightness/power/lease/debug tests, the `dev_guest_panel` launcher tests `:960-975`, `PAIR_IDS` import); add `test_the_target_is_the_canopy_engine_even_with_conductor_env_workshop` |
| `tests/test_studio_routes.py` | delete the pair tests and fixtures; `test_home_is_the_visualizer_everywhere` (workshop, workshop-sim, venue, staging, local → `/visualizer/`); keep the design-route tests |
| `tests/test_writer_paths.py:180-338, :586-642, :652` | delete `_studio_pair*`, `_guest_panel_workshop`, `_guest_panel_scene`, `_guest_panel_power`, `_vignette_action` and their `WRITER_PATHS` / `COVERED_MODULES` entries; docstring `:8` |
| `tests/test_roles.py:15, :77, :146, :152, :248, :257-275` | drop the VIGNETTE principal and tests; add vignette paths to the "denied for kiosk/tester" lists (`/vignette/x/` must 401 for everyone but ops, and 404 for ops); add `("vignette", ["living"], "Use kiosk or tester")` to `test_bad_enrollments_are_refused_in_plain_english`; `_load` tolerance: one kiosk + one `role: vignette` record → the kiosk loads, no `.corrupt`; one kiosk + one record with digest `"zz"` → still set aside as `.corrupt` with `store.problem` set (the skip catches only `role not in {r.value for r in Role}`) |
| `tests/test_ws*.py` (or test_canopy_only) | `ws.topic_allowed("vignette", frozenset({"living"}), "canopy") is False`; `topic_allowed("kiosk", frozenset(), "vignette:living") is False` |
| `tests/test_review3_fixes.py:1-3, :109-300, :311-360, :548` | delete V1/V2/V3/V5-vignette/V7; keep F3 (pause across restarts), V5's non-vignette ticket checks (rewrite with `Role.KIOSK`), the overlay download |
| `tests/test_stepped_rehearsal.py:55, :61, :181, :288, :455-487` | `CRITERIA` without `vignettes_clean`; delete `test_problems_on_a_vignette_account_fail_it`; the report has no `vignette` key |
| `tests/test_surfaces_review_fixes.py:451-459` | device groups end with spares (no vignette rooms) |
| `tests/test_devices_routes.py:18` | `== 10 + len(registry.by_role("spare"))` |
| `tests/test_lab_access.py:3, :56, :132` | `ENROLLED = [("kiosk", []), ("tester", ["bench-kit"])]` |
| `tests/test_interface_structure.py` | drop `kiosk-resting` and `vignette` from the pages; delete the three tracker tests and the `cases` load |
| `tests/test_mac_box.py:27, :91-101` | delete the venue-example comparison; **move** from `test_deploy.py` (1) `test_env_example_documents_every_variable_the_code_reads` with `_documented` (:49-51), `_variables_the_code_reads` (:155-160), `ENV_ACCESS`, `PREFIXED`, `IMAGE_VARIABLES`, against `deploy/mac/conductor.env.example` (the four commented lines of §1.2 make it pass), and (2) `test_deploy_files_hold_nothing_secret_or_identifying` (:183-191) over `(ROOT/"deploy").rglob("*")` (now deploy/mac only; drop `release.sh`). Mutation: a Fernet-shaped string in `deploy/mac/canopy-mac` → (2) fails |
| `tests/test_lab_roundtrip.py:10-12, :79` | drop `--ignore=tests/test_deploy.py` and the test_workshop note |
| `tests/test_sim_profiles.py` | adjust only if a synthesized profile is named (none today) |
| `tests/showcase-model.test.mjs:3-20`, `tests/showcase-scenes.test.mjs:5, :29-32, :42-45` | drop the paired-state tests and imports; `displayLayout('shop','bench-kit')` and `displayLayout('shop')` (the default) each have 30 heads |
| `tests_e2e/test_kiosk.py:7, :133, :501`, `tests_e2e/test_ops_visualizer.py:172-176, :347-372` | stub target `"canopy"` with `canopy: null` for the serpentine case; no vignette enrollment or rooms (not run in CI; keep them importable) |
| comments only | `tests/test_engine_program.py:318`, `tests/test_state.py:235`, `tests/test_accounts_routes.py:416`, `tests/test_kiosk_routes.py:442`, `tests/guest-panel.test.mjs:248` (optional) |
| keep as is | `tests/test_dev_design_lab.py:52`, `tests/test_lab_review_fixes.py:750` (they prove `CONDUCTOR_ENV=workshop` is stripped) |

**New:** `tests/test_canopy_only.py` — (1) `/vignette/living/`, `/api/vignette/living`, `/api/studio/pair/whole` and
`/api/studio/pair/gradient` answer 404 to ops; (2) no `conductor/**/*.py` imports `vignettes`, `module.pair`,
`module.scene_gradient` or `workshop` (AST); (3) `Role` has no `vignette`; the registry has no vignette role or
account; `len(REMOVED_DEVICE_IDS) == 15` and none is in today's `devices.yaml`; an ops `POST
/api/roles/enrollments {role: "vignette", name: "x", grants: ["living"]}` → 400 "…Use kiosk or tester."; (4) **old
data:** a `roles/sessions.json` holding a kiosk record and a vignette record loads the kiosk session (`principal()`
works) and no `.corrupt` file appears; an `overrides/registry.yaml` with a patch for `living-lamp-1`, a full `{role:
vignette, …}` entry, `bench-kit: {kind: string}` and `shop-string-1: {role: spare, account: bench, profile:
sqhdk03-sim, vignette: garden, kind: string}` loads, lists the ids in `registry.dropped` and the keys in
`dropped_fields`, and `services.problems` names them; the app starts (`TestClient` lifespan) and `/healthz` is 200;
then `save_overlay_patch(reg, "bench-kit", {"label": "x"})` succeeds, the written overlay no longer holds
`living-lamp-1` or any `kind`/`vignette` key, the reloaded `registry.dropped == []`, and `overlay_diff(reg).problem ==
""`; (5) `GuestPanel` target is the engine with `CONDUCTOR_ENV=workshop-sim`. Mutation targets: remove the `_merge`
id tolerance → (4) fails with ConfigError; remove the key pop → (4) fails with ConfigError; remove the
`save_overlay_patch` prune → the dropped id survives the write; remove the `_load` per-record skip → the kiosk
session is lost; widen it to every ValueError → the `zz` digest test fails; re-add the vignette router → (1) fails.

### 1.4 Edit — docs (R)
| File | Change |
|---|---|
| `README.md:3, :11-12, :21-22, :29` | canopy only; `make run` → visualizer; drop `make guest-dev`; config/web rows say canopy, guest panel, ops, Lab; `:29` "Show accounts live only on the show box (the Mac mini)" |
| `docs/context/07-engineering-contracts.md:70` (§8) | "a named volume with nightly backup on the venue box" → "the `/data` volume on Railway; `~/Library/Application Support/ColorCanopy/data` on the show box (D61), backed up nightly by `canopy-mac backup`". **R owns this sentence; P adds only its new §8 paragraph** |
| `docs/context/05-architecture-decisions.md:30` (#3) | drop "vignette controls". **R owns this; P adds only its clause** |
| `docs/context/04-device-model-permanent-lights.md:197` | Gate R: drop "with the four vignette accounts connected … the vignette accounts stayed clean" |
| `docs/runbooks/bench-prototype-01.md:183, :225, :230` | replace the `VWBD-HANDOFF.md` links with "the Oct 2 hand-off, kept in git history at `6ce971b`"; drop the "paired or gradient test" step |
| `docs/runbooks/strike.md` §5 "Clear the box" (:48-55) | replace the Ubuntu commands with: `canopy-mac backup` (copy it off the box) → `canopy-mac stop --yes` → `canopy-mac uninstall` → delete `~/Library/Application Support/ColorCanopy` (settings, tokens, data) and the backup folder per `mac-show-box.md` §7 → `tailscale logout` if used |
| `docs/runbooks/seven-am-check.md:12-19` (Clock item) | "On the show box run `canopy-mac status` → Clock (offset from Apple time); if it's more than a second or two off, check the internet, then System Settings → General → Date & Time → Set time automatically. The WARN 'can't see whether the box's clock is synced' is expected on the Mac: use `canopy-mac status`." (matches the new `routes/ops.py` text) |
| `docs/context/01-project-brief.md:24, :28, :34` | a dated note: "Scope (Oct 17, D67): the Conductor controls the canopy only; the four vignettes run on the stock Hubspace app, outside this system." Leave the brief's history otherwise |
| `docs/context/03-hubspace-platform-facts.md:40-42` | mark "Device facts relevant to vignettes" as out of scope (D67) |
| `docs/context/04-device-model-permanent-lights.md:77` | "(the vignettes are outside this system, D67)" |
| `docs/context/05-architecture-decisions.md:6, :9, :38, :40` | §6.3 below |
| `docs/context/06-open-questions.md:11, :18, :21, :23-24, :28` | #11 and #18: "closed — out of scope (D67)"; #21, #6, #28: drop the vignette accounts (Gate R now watches the canopy and bench accounts only) |
| `docs/context/07-engineering-contracts.md` §1 (`:12`, `:20`), §2 (`:24-25`), §10 (`:81`) | §6.1 below |
| `docs/runbooks/release-and-freeze.md` | rewrite for the Mac: a release is a tag on `main`; deploy = the mac runbook's paste block at that tag; rollback = switch `~/canopy-show` to the previous tag + `canopy-mac install`; the cold spare is a second Mac/laptop with a checkout and the last `canopy-mac backup` (§9 decision); no Docker image, no `install.sh` |
| `docs/runbooks/mac-show-box.md:4, :51, :55, :88` | no venue-box comparisons (drop the left column of the `:88` table, keep the Mac column); remote access: Tailscale or Screen Sharing (no `venue-box.md` link) |
| `docs/runbooks/{install-day-commissioning,outages,restart-matrix,seven-am-check,show-operation,stepped-rehearsal,strike}.md` | "venue box" → "the show box (the Mac mini)"; `sudo canopy-status` → `canopy-mac status`; drop every vignette tablet / account line (install-day §9 becomes "Enroll the kiosk iPad"; seven-am `:21`, `:71`; stepped-rehearsal `:5`, `:13`, `:30`, `:92`, `:114`; outages `:61`; restart-matrix `:13`; strike `:42` keeps "remove the vignette devices from their accounts" only if the client asks — drop it) |
| `docs/runbooks/bench-prototype-01.md:113-116, :152, :236, :308` | drop the workshop launcher and `scene_gradient` paragraphs; "show accounts only on the show box" |
| `docs/design/guest-panel.md:185` | "the serpentine of 30 lights (when `state.lit` is null: no look yet, an older server)" |
| `docs/CLOUD_SETUP.md:12, :17-18, :61, :75-78` | no `make guest-dev`, no workshop launcher, no test_deploy note; live work = the Mac show box (mac runbook) |
| `docs/REMOTE_CONTROL_SETUP.md:10, :40-63` | the Mac is the show box (D61); no workshop launcher, no venue box |
| `docs/DECISIONS.md` | append **D67** (§6.5). Don't rewrite history; D67 names what it supersedes |
| `docs/STATUS.md` | new top block for PR A (what went, the tolerance, verified how, the deploy note); leave the history below; drop the `--ignore=tests/test_deploy.py` advice from the "how to verify" line going forward |
| leave (historical prompts/plans) | `docs/BUILD_PLAN.md`, `docs/ULTRACODE_BUILD_PROMPT.md`, `docs/CLOUD_SESSION_PROMPT.md`, `docs/design-lab/PLAN.md`, `PROMPT.md`, `research/**` |

### 1.5 Old data on the live Mac (why the two tolerances are not optional)
The deploy runs PR A's code over the Mac's existing data folder. Two files could hold vignette data from earlier work
(we can't read them; the rules forbid it):
- `data/roles/sessions.json` / `enrollments.json`: today `_Record.from_json` does `Role(raw["role"])`; one vignette
  record would raise `ValueError`, `_load` would set the **whole file** aside, and the **Test iPad's kiosk session
  would be lost** (re-enrollment on site). Per-record skip fixes it.
- `data/overrides/registry.yaml`: `_merge` raises `ConfigError` for a patch whose id isn't in `devices.yaml` → the
  Conductor refuses to start → launchd restarts it forever. Per-entry drop fixes it. The same crash follows a
  `vignette:` or `kind:` key on a **surviving** entry (`extra=forbid`; `shop-string-1` was copied from VWBD's overlay
  and its keys can't be read) — the key pop fixes it. So does a `profile:` naming one of the six deleted vignette sim
  profiles (SimDriver, built at every start, live mode too, would refuse it): `REMOVED_PROFILES` pops it by value
  (review R1). `save_overlay_patch` (every install-day register) uses `_merge` too and prunes what was dropped, so the
  leftovers go with the first ops edit.
All are covered by `tests/test_canopy_only.py` (4), and `conductor.preflight` (run by `canopy-mac install` before the
restart; it also builds every entry's sim profile) catches anything else the new code can't load. After a failed
preflight the checkout is the rejected code, so install exits 3 with the rollback and the paste block (§8.1) rolls
the checkout back at once (review R2): any restart of the KeepAlive job would start the rejected code. Token files of removed accounts (if any) are left alone and
unused (`afero.py` looks tokens up by registry account). A leftover `studio-gradients/` folder is ignored.

### 1.6 R verification (put in STATUS)
`nice -n 15 uv run --offline pytest -q` (no `--ignore` needed any more), `uv run --offline ruff check . && uv run
--offline ruff format --check .`, `node --test tests/*.test.mjs`, `nice -n 15 uv run --offline python -m
conductor.soak` (exit 0; same rows as #27: the only FAIL is the non-gating "(reported)" D65 spread), a final
`git grep -n -i -E 'vignette|workshop|PAIR_IDS|scene_gradient|module\.pair|TEST-CENTER|deploy/venue|dev_guest_panel|kiosk-resting|venue[- ]box|canopy-status|systemctl|chrony|docker compose|guest-dev|VWBD-HANDOFF|shop-string-1'`
whose remaining hits are only an explicit allowlist written into STATUS (history docs of §1.4's "leave" row,
`research/**`, evidence files of §1.0, `.gitignore`, the strip tests, `gate.py`'s pinned "venue box" strings, the
removal tolerance (`REMOVED_DEVICE_IDS`, D67), the Studio's kept `shop-string-1` option, demo-model's generated
`CANOPY` geometry), `uv run --offline python -m conductor.preflight` over a scratch data dir, and a browser
pass of the Studio + Lab on `scripts/dev_design_lab.py --port 8211` (the Studio loads, the device select has no
"Both", the Lab's stage draws).

---

## 2. Stream P — publishing to the live show (server side, PR B)

### 2.1 The model in one paragraph
There are three looks per scene: **approved** (git: `config/{scenes,modes,recipes}.yaml`), **live** (what the show
compiles from now: the approved trio, or a **published** trio) and **draft** (the Lab's working edit / a saved Lab
version). Publishing a saved Lab version makes `apply_drafts(approved, version.drafts)` the live trio: it is
validated, written to `<data>/overrides/show-looks.yaml` (07 §8's overlay pattern) **before** it is installed, every
in-process copy is swapped in one synchronous step (§2.4), the engine re-picks the current look (one burst), and a
painted scene whose commissioned slots no longer hold the live design is **held off T1** (it runs at module level,
true to the new palette) until its slots are repainted (B2: the operator repaint job, §2.6; until then the B1 lock,
§2.6.0). A restart loads the same file; a missing file is "approved"; a bad file is "approved" plus an /ops alert and
never a failed start (§2.3). **Revert to approved** is a publish whose trio is git's; **Undo last publish** reinstalls
the previous publication's stored trio. Neither is ever blocked by the repaint limit (§2.7). Git stays the record:
the existing Lab export turns the published version
into a reviewed patch (approve-into-git later). `<data>/overrides/looks.yaml` (the Lab's version store) is unchanged
and the show still never reads it; the show reads only `show-looks.yaml`.

### 2.2 The overlay: `<data>/overrides/show-looks.yaml` (new module `conductor/looks/live.py`, show path)
```yaml
format: 1
seq: 3                        # last publication number issued (p0001, p0002, …; never reissued)
current:                      # what the show runs; null = the approved (git) looks
  id: p0003
  kind: version               # version | approved (approved = follow git; no trio stored)
  version: v0007              # the Lab version published (null for approved)
  name: "Client Oct 22 v3 – warmer sunset"
  published_at: 1792700000.0  # services.clock.now()
  by: ops                     # principal role/name from the route
  base_config_sha: 3f9a1c0e2b7d   # git trio sha (design_lab.store.config_sha rule) at publish time
  trio_sha: 9be04d11a2c0      # sha256[:12] of the canonical JSON of `trio` below (json.dumps(sort_keys, separators))
  changed: [golden-hour]      # scenes differing from the approved trio at publish time
  drafts: {golden-hour: {...}}  # the version's SceneDraft dumps — provenance only; the show never parses them
  trio:                       # what the show loads (null when kind: approved)
    scenes:  {...}            # SceneCatalog.model_dump(mode="json")
    modes:   {...}            # ModesFile.model_dump(mode="json", exclude_unset=True) — reloaded through parse_modes
    recipes: {...}            # RecipesFile.model_dump(mode="json")
previous: {...}               # the Publication `current` replaced (same shape, trio included; null at first) — Undo
history:                      # newest first, ≤ 200, metadata only
  - {id: p0003, kind: version, version: v0007, name: "...", published_at: ..., by: "Alon", changed: [golden-hour],
     repaint: {golden-hour: {slot: custom-3, applied: 9, skipped: 0, failed: 0}} | null, outcome: "live"}
repaints:                     # B2. scene → wall time of the last operator repaint (the 2-min gate survives a restart)
  golden-hour: 1792700000.0
queued: {}                    # B2. scene → wall time a queued repaint is due (re-queued after a restart)
```
`by` is the publisher's name from the dialog (`who`, §2.11), falling back to "ops" (one Basic credential for
everyone). `seq`, `repaints` and `queued` are parsed **leniently and separately** from `current`/`previous`/`history`
(a bad history row never resets `seq` — ids are never reissued — or the gate). History copies of the file
(`overrides/history/show-looks-<stamp>.yaml`) are pruned to the newest 50.
```python
# conductor/looks/live.py — imports conductor.looks/.scenes/.config only; never conductor.design_lab
LOOKS_FILE = "show-looks.yaml"; LOOKS_KEY = "looks"; LOCK_KEY = "looks:lock"
MAX_BYTES = 2_000_000; MAX_HISTORY = 200; MAX_DEPTH = 64
ALERT_PREFIX = "Published looks:"         # AlertBoard key rule (§2.12)

@dataclass(frozen=True)
class ConfigTrio:                           # MOVED here from design_lab/draft.py:194 (draft.py re-exports it)
    scenes: SceneCatalog; modes: ModesFile; recipes: RecipesFile
    def dump(self) -> dict

class Publication(_Strict):                 # `current`
    id: str = Field(pattern=r"^p\d{4,}$"); kind: Literal["version", "approved"]; version: str | None = None
    name: str = Field(min_length=1, max_length=80); published_at: float; by: str = Field("", max_length=80)
    base_config_sha: str = Field(pattern=r"^[0-9a-f]{12}$"); trio_sha: str | None = None
    changed: list[str] = []; drafts: dict[str, Any] = {}; trio: dict[str, Any] | None = None
class HistoryEntry(_Strict): id; kind; version; name; published_at; by; changed; repaint: dict | None; outcome: str
class ShowLooksFile(_Strict):
    format: Literal[1] = 1; seq: int = Field(0, ge=0); current: Publication | None = None
    previous: Publication | None = None
    history: list[HistoryEntry] = Field([], max_length=MAX_HISTORY); repaints: dict[str, float] = {}
    queued: dict[str, float] = {}
# YAML: loaded with `_NoAliasLoader(yaml.SafeLoader)` that raises on any anchor/alias event (no alias bombs), and
# dumped with a SafeDumper whose `ignore_aliases` returns True. `config_sha` + `CONFIG_FILES` MOVE here from
# design_lab/store.py:612 (store.py re-exports them), so live.py never imports conductor.design_lab.

@dataclass
class LiveLooks:                            # services.extras[LOOKS_KEY]
    git: ConfigTrio                         # the approved trio (deep copies of the show's own loaders)
    trio: ConfigTrio                        # what the show compiles from NOW (git or published)
    source: Literal["approved", "published"]
    file: ShowLooksFile                     # as loaded / last written (an empty one when missing or unusable)
    path: Path | None                       # None: no data dir (publishing refused)
    problem: str | None = None              # why the file couldn't be used (the show runs approved)
    generation: int = 0                     # +1 on every install_looks
    repaint: dict | None = None             # B2: the running/queued/last repaint job's progress (RepaintJSON, §2.11)
    equals_approved: bool = False           # was a version whose trio equalled git's at load (now followed as approved)
    git_changed: bool = False               # current.base_config_sha != git's sha now (and not equal to git)

def build_live_looks(services) -> LiveLooks           # called ONCE, eagerly, in the lifespan right after
                                                      # build_services() (engine or not), on the loop thread; stores
                                                      # services.extras[LOOKS_KEY]; never raises for the overlay (only
                                                      # a broken GIT config raises ConfigError)
def live_looks(services) -> LiveLooks                 # reads extras[LOOKS_KEY]; route tests without the lifespan get
                                                      # it from the test fixture's build_live_looks (no lazy build:
                                                      # threadpool readers would race it)
def live_trio(services) -> ConfigTrio                 # live_looks(services).trio — every live reader calls this ONCE
                                                      # per request/compile and keeps the reference (atomic swap)
def load_live_looks(config_dir: Path, data: Path | None, *, settings, canopy: dict) -> LiveLooks
def validate_trio(raw: Mapping, *, git: ConfigTrio, settings, canopy: dict, path: str) -> ConfigTrio  # ConfigError
def trio_sha(trio: ConfigTrio) -> str
def write_looks(looks: LiveLooks, new_file: ShowLooksFile, *, now: float) -> None   # SYNC on the loop thread (small
                                                      # file): history copy, then atomic_write 0600; raises OSError
def install_looks(services, new: ConfigTrio, *, why: str) -> list[str]              # THE checklist (§2.4); installs
                                                      # deep copies (model_copy(deep=True) of each of the three), so
                                                      # the live trio never shares objects with lab.approved/the store
```
**`validate_trio`** (all of it, first failing step wins, every message becomes a bullet of one `ConfigError`):
`SceneCatalog.model_validate`; `RecipesFile.model_validate`; `parse_modes(raw["modes"], recipes=…, canopy=…,
path="the published looks")` (schema.py:599: schema + `cross_check`, so every kiosk sentence is held to the same
honesty rules as git); **invariants against `git`**: scene ids and order, mode ids and order, every mode's `ladder`,
`slot` and `motion` equal git's (the Lab never changes them; a deploy that changes them makes the old overlay
unusable rather than masking git's structure); `conductor.show._check_idle_scene(settings.kiosk.idle_scene,
list(modes.modes))` and `conductor.show._check_stepped(settings, modes)` (imported lazily; `list(modes.modes)`, not
`list(modes)` — design-lab PLAN §3.2.3's trap); `scene_rules.scene_mode_problems(…, rules=BLOCKING)` (scene_rules.py:49;
`WARNINGS` don't block, §9). Round trip: a test pins `parse_modes(dump).model_dump() == modes.model_dump()` for the
git trio and a patched one; if `exclude_unset` loses anything, store the full dump instead.

### 2.3 Startup load order and the fail-safe
1. `build_services()` as today (registry, settings — a bad **git** config still refuses to start, as today), then
   the AlertBoard, then **`build_live_looks(services)`** (eager, on the loop thread, engine or not), then ShowWiring.
2. `ShowWiring.__init__` (show.py:126) reads `looks = live_looks(services)` **instead of** `load_modes(...)`:
   `self.modes_file = looks.trio.modes`; new `self.recipes = looks.trio.recipes`; then the existing
   `_check_idle_scene` / `_check_stepped` on `self.modes_file` (they passed inside `validate_trio` already).
3. `load_live_looks`: `git = load_trio`-equivalent (deep copies of `load_scenes`, `load_modes`, `load_recipes`);
   then the file. **The whole overlay stage (stat, read, parse, depth, models, `trio_sha`, `validate_trio`) is wrapped
   in `except Exception` → fallback** (a RecursionError, an alias, a MemoryError-shaped input can't hang or stop the
   lifespan behind the 30 s watchdog):
   - no data dir / no file → `source="approved"`, no alert.
   - file > `MAX_BYTES` (stat first, never read), unreadable, not YAML, any anchor/alias, too deep, bad top level,
     unknown `format`, `current.kind == "version"` with `trio` missing, `trio_sha` mismatch ("edited by hand or cut
     short"), or `validate_trio` refuses → **fallback**: `source="approved"`, `trio = git`, `problem = <first line,
     ≤ 160 chars>`, `file` = whatever parsed leniently (`seq`, `repaints`, `queued`, `history` survive), else empty.
     **The file is left where it is, byte for byte** (a later publish writes a history copy before replacing it).
     `looks.problem` is set (**not** appended to `services.problems`, a static startup list):
     `routes/devices.runtime_problems` adds `f"{ALERT_PREFIX} overrides/show-looks.yaml couldn't be used ({problem}), so
     the show runs the approved (git) looks. Publish again from the Design Lab, or Revert to approved."` **while
     `looks.problem` is set**, and `install_looks` clears it, so /ops never shows a fixed problem. The log/AlertBoard
     copy is one `telemetry.record("alert", level="warn", …)` under the key `looks:load` (§2.12).
   - `current.kind == "approved"` or `current is None` → approved.
   - valid version whose trio **equals git's** (approve-into-git merged and deployed) → follow git: `source="approved"`,
     `trio = git`, and `current` becomes `{kind: approved, name: "approved (in git)"}` persisted at once with one
     small atomic write (history entry `outcome: "in git"`; the old current moves to `previous`). A failed write is
     logged and ignored (memory still follows git). This is the one write `load` may make. So the next deploy's git
     edits are never silently masked by a trio that once equalled git.
   - valid version that differs from git → `source="published"`, `trio = validated`;
     `git_changed = base_config_sha != config_sha(config_dir)`; when `git_changed`, a **warn** (shown on /ops, key
     `looks:git`) and a runtime problem line: "Published looks: “<name>” is live and masks config/ changes deployed
     since <date>; Revert to approved or republish." (resolved by the next install).
   Nothing here awaits or talks to a device, and nothing else writes. The engine starts afterwards and compiles from `trio`; in sim
   `_sim_commission` commissions from it, so records match it.
4. **Slots after a fallback** (the decided default, §9): the show runs git, so a painted slot that holds the published
   design is "stale" (`stale_slot`) and the reconciler's existing rule rewrites it back to git's design, at most once
   per 10 min per slot (07 §6). Meanwhile the T1 hold (§2.5) keeps that scene at module level, so the iPad's copy and
   the lights agree. The /ops warn says why; the Lab shows it (§3).

### 2.4 The hot-apply checklist (one function) and what reads live per request
```python
def install_looks(services, new: ConfigTrio, *, why: str) -> list[str]:
    """Swap every process-lifetime copy of scenes/modes/recipes for `new`, synchronously on the loop thread (no await:
    no request can see half of it). Returns the names of what it refreshed (logged, telemetry `lab looks_installed`)."""
    done = []
    looks = live_looks(services)
    new = ConfigTrio(*(m.model_copy(deep=True) for m in (new.scenes, new.modes, new.recipes)))
    looks.trio = new
    looks.generation += 1
    looks.problem = None  # a fixed fallback leaves /ops at once (runtime_problems reads it)
    done.append("live looks")
    wiring = services.extras.get("show")  # ShowWiring
    if wiring is not None:
        wiring.install_looks(new.modes, new.recipes)  # modes_file, recipes, _rehearsal_modes.clear()
        done += ["show modes", "show recipes", "rehearsal designs"]
    engine = services.extras.get("canopy")
    if engine is not None:
        engine.invalidate_t1_plans()
        done.append("T1 plans")  # canopy.py:2287 (_t1_plans, _t1_failed)
    manager = services.extras.get("module_manager")  # MANAGER_KEY
    if manager is not None:
        manager.install_looks(new.recipes, new.modes)
        done.append("module page")  # + every open session's deps
    lab = services.extras.get("design_lab")
    if lab is not None:
        lab.live_changed()
        done.append("design lab")  # drops its per-live caches (catalog seeds)
    return done
```
The full list of copies, each verified in the code, and how each is handled:

| # | Copy (where) | Handling |
|---|---|---|
| 1 | `ShowWiring.modes_file` (show.py:130; read by `context()` :411, `ladder` :442, `rehearsal_modes` :466-469, `mode_ids` :497, `tier_allowed` :504) | `ShowWiring.install_looks(modes, recipes)` sets it |
| 2 | recipes passed into `context()` (show.py:412 passes `recipes=None` → `compile._default_recipes`, the git cache) | `context()` uses `recipes if recipes is not None else self.recipes` |
| 3 | `ShowWiring._rehearsal_modes` (show.py:151, :457-494) | cleared by `install_looks` |
| 4 | `_check_idle_scene` / `_check_stepped` (show.py:65, :83) | run inside `validate_trio` before anything is written or installed |
| 5 | engine `_t1_plans` / `_t1_failed` (canopy.py:2287) | `invalidate_t1_plans()` |
| 6 | engine current plan (and a ProgramRunner's plan) | re-picked by `engine.reload_looks` (§2.5), one burst |
| 7 | engine trial (`Trial.modes/recipes` patched over the old live trio) | ended by `reload_looks` (§2.9) |
| 8 | `compile._default_modes/_default_recipes/_default_canopy` lru caches (compile.py:130-143; `CompileContext.__post_init__` :186-195, `_modes()` :264) | **stay the git caches by definition**; no live reader may rely on them: every live reader passes `modes=`/`recipes=` from `live_trio`. Pinned by `test_no_live_reader_uses_the_git_defaults` (§5.2) |
| 9 | `scenes.load_scenes` lru (scenes.py:104) — the guest panel's catalog (guest_panel.py:1045-1046) | `GuestPanel.scenes()` returns `live_trio(self.services).scenes`; `load_scenes` stays git (the Lab's `load_trio`) |
| 10 | the iPad's cards (`web/kiosk/kiosk.js:206` `buildCards`, key `cardsKey` panel-model.mjs:170) | already rebuild on any content change (Slice B); the panel's catalog is read per request (routes/guest.py:101 `_catalog`). **No kiosk change** |
| 11 | `routes/kiosk.py` `MODES_FILE` raw YAML (:68, `load_kiosk_modes` :236, `_modes_or_503` :449, **`/api/kiosk/modes`' per-mode `pattern` specs :487-490**), `_from_looks()` → `load_modes()` of `CONFIG_DIR` (:220-233), `_sampled_colours` `CompileContext(registry=[], capabilities={})` (:170) | `load_kiosk_modes(path=MODES_FILE, *, modes: ModesFile | None = None, recipes=None)`: with `modes`, the raw spec is `modes.model_dump(mode="json", exclude_unset=True)` and `looks` is `modes.modes`; `_modes_or_503(services)` passes `live_trio(services)`; `_sampled_colours(mode, *, modes, recipes)` builds its context from them; `:487` `specs = live_trio(services).modes.model_dump(mode="json", exclude_unset=True)["modes"]` (its own parametrized case, and `_read_yaml` patched to raise in the no-git-defaults test). The `path=` form stays for the tests that pass a file |
| 12 | `routes/ops.py` `load_modes(config_dir)` raw YAML (:183) → `mode_list` (:189; used :367, :510, :536, :1280, :1501, :1515) | `mode_list(services)` from `live_trio(services).modes` (id, label, blurb, slot) |
| 13 | `alerts.py` `_mode_labels` raw YAML (:186-197) | labels from `live_trio(services).modes` |
| 14 | `routes/design.py` `_context` (:50-51; `/api/canopy/design`, the Studio/visualizer design) | `CompileContext(…, modes=trio.modes, recipes=trio.recipes, config_dir=…)` |
| 15 | `ModuleManager.recipes` / `.modes` (module/manager.py:56-59) and every open `ModuleSession.deps` (`SessionDeps.recipes` :94, `.modes` :101; built once in `deps()` :71-86) | built from `live_trio(services)`; `ModuleManager.install_looks(recipes, modes)` sets both and `session.deps = dataclasses.replace(session.deps, recipes=…, modes=…)` for each session in `self.sessions` |
| 16 | Lab: `LabService.approved` (git, stays), catalog seeds, `trial.show_trio` (trial.py:55-70: `wiring.modes_file` + `wiring.context().recipes` + **`load_scenes` (git)**) | `show_trio(lab)` = deep copies of `live_trio(lab.services)` (all three); `LabService.live_changed()` clears any per-live cache; `_approved_name` (trial.py:255) → the live scene's name |
| 17 | `canopy_view` lru caches (:75, :91, :139), `framemap.module_heads` (:95), sim profiles, `design_lab.preview.design_caps` (:101) | geometry/profiles only (canopy.yaml is never published) — unaffected; listed in the test as "not looks" |
| 18 | soak (`conductor/soak.py`) | offline, its own data dir → git; unaffected |
| 19 | engine `_t1_hold` (§2.5) | `engine.reload_looks` recomputes it for the changed painted modes before the re-pick |

### 2.5 Engine and show seams (`conductor/canopy.py`, `conductor/show.py`) — B1 unless marked B2
In canopy.py "publish" already means the WebSocket state push (`_publish_soon`, `PUBLISH_MIN_INTERVAL_S`), so the
new names say **looks**; executor labels keep "publish repaint: …" for the operator's view.
```python
# canopy.py
LOOKS_SOURCE = "looks"
RECOMPILE_SOURCES = frozenset({"reconciler", "recovery", "startup", LOOKS_SOURCE})
    # a looks re-pick keeps the guest's brightness/power (D60), is one burst (ONE_BURST_SOURCES ⊇ it), never ends a
    # rehearsal (refused anyway). _request's `source not in ("reconciler", "recovery")` line makes it count as a change
    # for the idle hold — intended: staff just changed the look.

class CanopyEngine:
    _t1_hold: set[str]   # modes kept off T1 because their commissioned slots don't hold the LIVE design (yet)

    def looks_refusal(self) -> tuple[str, float | None] | None
        # (code, retry_after_s): "stopped" | "commissioning" (30) | "outage" (outage_probe_interval_s) |
        # "rehearsal" (_rehearsal_left()) | B2 "repainting" (only while a repaint is WRITING: _repaint_quiet; its
        # estimate). Guest input paused is NOT a refusal (staff own the canopy then). A queued repaint that hasn't
        # started is not a refusal: the new publish replaces it (§2.7). publish.py turns codes into Lab sentences.

    def compile_best(...)  # NEW, next to the try-on's TRIAL_SKIP line: `if mode_id in self._t1_hold: skip |= {T1}`.
        # NOT in tier_allowed: t1_target/stale_slot use tier_allowed and must still see the slot as stale, or a held
        # mode's failed module would never be rewritten and the hold would never lift.

    def t1_hold_for(self, modes: Collection[str]) -> set[str]
        # sync. For each mode with a slot and T1 in its ladder whose tier_allowed(T1) holds: compile T1 from the live
        # trio (deps.compile, not the cache); held when any registry canopy module's api record hash differs from
        # plan.modules[dev].slot_hash, or (B2) any (dev, slot) is in _repaint_pending. PlanError → not held (T1
        # doesn't compile, so it isn't picked anyway).

    async def reload_looks(self, *, changed: Collection[str], why: str) -> CanopyState
        # Called by publish.py in the SAME synchronous section as write_looks + install_looks (no await before the
        # _request below). No refusal re-check here: the caller checked looks_refusal() in that same section.
        # cleared = self._clear_trial(f"the look was published ({why})")   # no repick: the re-pick below is the burst
        # self.invalidate_t1_plans()
        # self._t1_hold = (self._t1_hold - set(changed)) | self.t1_hold_for(changed)
        # if self._plan is None or (self._plan.mode_id not in changed and cleared is None):
        #     self._publish_soon(); return self.state()                     # current look unchanged: zero writes
        # try:
        #     st = await self._request(self._plan.mode_id, source=LOOKS_SOURCE, ripple=False)
        # except BaseException as exc:
        #     self._recover_from_trial_error(cleared, exc, restore=True); raise
        # if st.refused (compile_best found nothing under the new trio):
        #     self._restore_approved(cleared)       # compiles from the live trio now
        #     if the current plan is still a try-on or old-trio plan: self._safe_look_after_error(<why>)
        #     warn "Published looks: <scene> can't be shown under the published looks (<why>); the safe look is on."
        # return st

    def _record_slot_result(...)  # NEW tail (after _record_slot_value): when a held mode's records now equal the
        # live T1 compile on every module and nothing of it is pending, drop it from _t1_hold and _replan_soon() —
        # the single T1 re-select, one burst (REPLAN_DELAY_S coalesces several modules).
    # start of the engine (after ShowWiring.started's capability reads): _t1_hold = t1_hold_for(<painted modes>) —
    # covers a restart after a fallback, a failed repaint, or a queued one (B2 re-queues `queued` at its due time).

    # ---- B2 -------------------------------------------------------------------------------------------------------
    _repaint_pending: set[tuple[str, str]]; _repaint_task: asyncio.Task | None; _repaint_quiet: bool
    _repaint_last: dict[str, float]            # mode → monotonic time of the last operator repaint's start
    @property
    def quiet(self) -> bool: return self._commissioning or self._repaint_quiet
    @property
    def commissioning(self) -> bool: return self.quiet
        # the PUBLIC property (read by reconciler.py:237/:260/:372/:551/:572, sequence.py:239/:313, show.py:326)
        # returns `quiet`, so the reconciler's tick/reassert, the idle hold, the stepped runner and the caps re-pick
        # all hold still during a repaint with no edit there. Internal `self._commissioning` reads stay as they are.
    def start_repaint(self, jobs: Mapping[str, Collection[str]], *, generation: int, due: Mapping[str, float]) -> None
        # ONE job per publish. Synchronously, in the same call that spawns the task: cancel a queued job that hasn't
        # started writing (its marks are cleared), add every (dev, slot) of `jobs` to _repaint_pending, then
        # self._repaint_task = self._spawn(self._repaint(jobs, generation, due), name="canopy:repaint")
        # The task's try/finally wraps EVERYTHING (the waits and refusal checks included), so marks never leak.
    @property
    def repainting(self) -> bool: return self._repaint_task is not None and not self._repaint_task.done()
    async def _repaint(self, jobs, generation, due) -> dict   # §2.6
    async def commission(...)  # NEW refusal after the two early returns: while self.repainting →
        # {"ok": False, "message": "A publish is repainting <slot> (about <s> s left); commission once it ends.",
        #  "retry_after_s": s}
    # _refusal, clear_guest_overrides, reassert_all, _rehearsal_checks, _trial_refusal: while _repaint_quiet, refuse
    # like commissioning with REPAINT_MESSAGE = "One moment: the canopy is changing." + retry_after_s (the kiosk sees
    # it as a short wait; true: the slots are being rewritten).
    async def _commission_plan(self, plan, source, *, min_interval: float | None = None,
                               only: Collection[str] | None = None, verb: str = "commission",
                               locked: bool = False) -> dict
        # min_interval None = settings.reconciler.slot_rewrite_min_interval_s (today's behaviour, byte for byte);
        # `verb` prefixes the command label; `only` limits the modules; `locked=True` wraps each module's header read →
        # burst → _record_slot_result in `async with self._slot_lock(device_id)` (one burst per device; the executor
        # paces each device anyway). When verb != "commission", slot_written[(dev, slot)] is stamped at SUBMIT for
        # every module whose slot Command was built, whatever the outcome (reconciler.py:676's "the limit counts from
        # the attempt"), so a throttled/failed repaint is never followed by an immediate reconciler rewrite.
```
```python
# show.py
class ShowWiring:
    recipes: RecipesFile                                     # NEW (2.4 #2)
    def install_looks(self, modes, recipes) -> None          # sets modes_file + recipes, clears _rehearsal_modes
    def repaint_plan(self, trio: ConfigTrio) -> dict[str, RepaintTarget]     # B2 (B1 uses its counts for the lock)
        # Per MODULE, not all-or-nothing. For each mode with a `slot` and T1 in ladder(mode): the eligible modules are
        # the registry canopy modules whose record for the slot is origin "api" and which have their own t1_frames
        # flag and (60-entry kits) map. Compile T1 over a context restricted to them
        # (dataclasses.replace(context, registry=<those entries>); PlanError → none eligible). A module is listed when
        # its record hash != plan.modules[dev].slot_hash. RepaintTarget(slot, listed, eligible, total, app: list[str]
        # (modules whose record is origin "app"), uncommissioned: list[str]). Pure: no engine cache touched.
```
**Reconciler (B2, one line in `reconciler.py`):** `check_slots`' loop starts with `if (device_id, slot) in
engine.repaint_pending: continue`, so neither the stale nor the drift branch (adopt / rewrite) runs for a pending pair,
whichever path reached it (tick, `_on_result` MISMATCH → `check_slots`, `_slot_recorded` → `check_module_soon`). The
reconciler's 600 s limit is otherwise unchanged. No `executor.py`, `sequence.py` or driver edit.

### 2.6.0 B1: painted scenes before the repaint job exists
`plan_publish` uses `repaint_plan(new)`. For a changed painted scene whose design (frame) changes:
- **no module has the slot commissioned** (today's two kits): allowed. T1 isn't allowed anyway; the scene shows the
  new design at module level (T3) and strand. A kit commissioned later gets the live design.
- **any module has an origin-"app" record for the slot** (the install-day T2 fallback, hand-painted): refused for
  that scene, 409 `{detail: "Golden Hour's slot is hand-painted in the Hubspace app on 3 modules (Modules 1–3);
  publishing a new design would make the iPad describe colours the lights don't show. Repaint it in the app and record
  it on the module page's Slots tab first, or publish only its brightness, copy or timing.", field:
  "golden-hour.layout"}`. Stays in B2 (`check_app_lock`, shown as a warning by the Lab's preview and save too).
- **api records exist** (B1 only): refused for that scene: "Golden Hour's painted design is in custom-3 on N modules;
  repainting from the Lab isn't on this box yet. Publish its colours with the painted design unchanged, or publish and
  then commission again on /ops/bench." — **except Revert to approved and Undo**, which are always allowed: the scene
  is held off T1 (shows truthfully at module level) and the reconciler's existing stale rule rewrites the slots
  (≤ once per 10 min per slot, its own slot lock), after which the hold lifts. The confirm says so.
Non-frame changes of a painted scene (brightness, solid, copy, stepped timing, a colour name) are never locked.

### 2.6 B2: the repaint path (painted scenes, custom-1..3)
`CanopyEngine._repaint(jobs, generation, due)` — one task per publish, scenes in sequence; `try/finally` around all:
1. Per scene: wait until `due[mode]` (the 2-min gate, §2.7; the scene is held off T1 meanwhile, its marks pending).
   Abort (clear that scene's marks) when `live_looks().generation != generation` (a newer publish replaced it).
   Refusal checks at start: stopped, commissioning, outage, rehearsal → that scene reports `ok: false` with the
   reason; marks cleared; hold stays (truthful module level) until the reconciler or a commissioning makes it current.
2. `self._repaint_quiet = True`; `await self.wait_idle(timeout=30)` (the publish's own re-pick burst and any guest or
   stepped command finish first, as commissioning does at canopy.py:808-810).
3. `targets = deps.repaint_plan(live trio)[mode]` (recomputed now, from the live trio); compile T1 over the eligible
   modules; `report = await self._commission_plan(plan, LOOKS_SOURCE, min_interval=0.0, only=targets.listed,
   verb="publish repaint", locked=True)` — never over an "app" record, never on a module held by the module page
   ("locked"), one slot write per module through the executor (≤ 1 write/s/device, per-account concurrency; retried at
   most twice more within the job only when it didn't land: COMMISSION_ROUNDS), header from the device's readback,
   each applied readback hash recorded exactly like commissioning (`_record_slot_result` → … →
   `overrides/registry.yaml`), so after a restart the record equals what the live trio compiles to. `slot_written`
   stamped at submit (§2.5). `self._repaint_last[mode] = monotonic` and `looks.file.repaints[mode] = wall` persisted.
4. `finally` (per scene and for the job): discard the scene's `(device, slot)` marks; `_repaint_quiet = False`.
   Fully applied → the hold lifts through `_record_slot_result`'s hook → `_replan_soon()` → one T1 burst (that send is
   the re-select: rewriting the active slot without one is unbenched, 04 item 6; when `plan.motion` it re-triggers
   all modules, 07 §6). Any module failed/locked/skipped → the mode stays held at module level until the reconciler
   (≥ 600 s after the attempt) or a commissioning makes it current; the warn says so.
5. Telemetry `lab` `action="repaint"`; alerts under the key `looks:repaint:<mode>` (§2.12): info "Published looks:
   <Scene>'s painted design is in <slot> on N modules." or warn "Published looks: <Scene>: <slot> repainted on N of M
   modules (<labels>: <reason>); it shows at module level until the rest are current (the reconciler retries after 10
   min)." `LiveLooks.repaint` is updated per module (the Lab polls it).
**Partly commissioned canopy** (likely at the end of install): only modules with their own api record + Gate C + map
are repainted; the others get the live design when commissioned (commissioning honours the 600 s `slot_written`
limit: runbook line "commission after your last publish, or wait 10 min after a publish before commissioning that
kit"). RepaintInfo/confirm sentences come from counts: (a) none: "No slot repaint: no module has custom-3
commissioned on this show box."; (b) some: "custom-3 is commissioned on 5 of 9 modules (Modules 1–5); publishing
repaints those 5 (one slot write each). The other 4 get the published design when they are commissioned."; (c) all:
"Publishing a new painted design repaints custom-3 on 9 modules (one slot write each)."

### 2.7 B2: the operator repaint limit (2 min per scene) — never blocks a look change
- Setting: `LabSettings.repaint_min_interval_s: float = Field(120, ge=60, le=600)` (config.py:201) and
  `config/conductor.yaml` `lab: {repaint_min_interval_s: 120}` with a comment ("Alon, Oct 17: an operator republish of
  a painted scene repaints its slot at most this often; the reconciler's own drift rewrite stays at
  reconciler.slot_rewrite_min_interval_s").
- **The publish always installs at once** (the scene runs held at module level, true to the new palette). The slot
  repaint is **queued**: `due = now + left`, `left = clamp(max(persisted wall-clock left, monotonic left), 0,
  interval)` from `looks.file.repaints[mode]` and `engine._repaint_last[mode]` (the clamp means an NTP step can't lock
  it out or open it early by more than the interval). `start_repaint` itself enforces `due`, so no caller can bypass
  it. A publish while a repaint is queued replaces the job (latest wins; the due time stays). The confirm says
  "…repainted into custom-3 in 56 s." `queued` is persisted and re-queued after a restart.
- Revert to approved and Undo are publishes like any other: never refused by the gate; their repaint (if the slots
  need one) is queued the same way. When the slot already holds the reverted design (the queued repaint never ran),
  `repaint_plan` lists nothing and the hold lifts at once.
- Worst case on the account: three painted scenes republished back to back = ≤ 3·27 slot writes (only when writes
  don't land) + 27 header reads + 9 look PUTs + the T1 bursts, all paced by the executor, at most once per 2 min per
  scene.

### 2.8 Refusal matrix (`POST /api/lab/publish`) — Lab sentences in `design_lab/publish.py`, not the guest copy
| State | Answer | Written / sent |
|---|---|---|
| no engine (`CONDUCTOR_ENGINE=off`) | 503 `{detail: NO_ENGINE}` | nothing |
| no data dir | 409 "This runtime has no data folder, so a publish couldn't survive a restart." | nothing |
| engine stopped | 409 "The canopy engine is stopped (canopy-mac start)." | nothing |
| commissioning | 409 "A kit is being commissioned (/ops/bench); publish once it ends." + `retry_after_s` | nothing |
| outage (resting) | 409 "Hubspace isn't answering (resting since 12:04), so nothing can reach the lights. Your version is saved; publish it once /ops says the canopy is back." + `retry_after_s` | nothing |
| stepped rehearsal running (Gate R) | 409 "A stepped rehearsal is running; stop it first." + `retry_after_s` | nothing |
| B2: a repaint writing now | 409 "Golden Hour's slots are being repainted (about 8 s left)." + `retry_after_s` | nothing |
| another publish in progress (`asyncio.Lock`, `extras[LOCK_KEY]`) | 409 "Another publish is in progress." | nothing |
| `expected_live` ≠ the live id (someone else published since this dialog opened) | 409 `{detail: "The show changed while you were deciding: “<name>” was published at 12:10 by <who>. Look again before publishing.", live: LiveJSON, conflict: true}` | nothing |
| guest input paused | **allowed** (staff own the canopy; this is the pre-guest iteration case) | — |
| a try-on running | **allowed**; it ends (the confirm says so) | — |
| unknown version | 404 | nothing |
| `version: "previous"` with no previous | 409 "There is no earlier publish to go back to." | nothing |
| version stale (doesn't apply over today's git) | 409 `{detail, stale: true, problems}` | nothing |
| the trio fails `validate_trio` (also for "previous") | 422 `{detail, problems}` (Lab shape) | nothing |
| a painted design change over an "app" slot (§2.6.0) | 409 `{detail, field}` | nothing |
| B1: a painted design change over api records (§2.6.0; not Revert/Undo) | 409 `{detail, field}` | nothing |
| equals what the show runs | 409 `{detail: "This is already what the show runs.", nothing: true}` | nothing |
| no / stale `confirm` | 409 `{detail, confirm_required: true, confirm, cost, changes, repaint, ends_trial, replaces}` | nothing |
| overlay can't be written (read-only, OSError) | 409 `{detail, read_only: true}` | nothing (memory untouched: the write precedes the install) |
| an exception during install/re-pick | 500 plain; **rollback**: previous file restored, `install_looks(previous)`, the B2 job cancelled (marks cleared), then `await engine._request(mode, source=LOOKS_SOURCE, ripple=False)` under the restored trio (else the safe look), alert warn | possibly two bursts |
**Order in `publish()`:** build + plan (threadpool: `published_trio`, `plan_publish`, the confirm sentence) → then
**one synchronous section on the loop thread**: lock held; `looks_refusal()`; `expected_live` and the plan's
`generation` still current (else conflict); confirm matches; `write_looks` (sync, small file; history copy first; an
OSError → `read_only`, nothing installed); `install_looks`; the sync prefix of `reload_looks` → then the awaits (the
re-pick burst, `start_repaint`, alerts). No await between the refusal check and the install, so no re-check or
"restore the file after a refusal" path exists. The confirm is recomputed on the confirmed request (same
`plan_publish`); a sentence that no longer matches → 409 `confirm_required` again (the trial's `confirm_matches`).

### 2.9 Interaction with the try-on and the reconciler
- **Try-on:** `reload_looks` clears it (`_clear_trial("the look was published (…)")`, telemetry `trial_end`) with no
  repick of its own; the re-pick that follows shows the published look on every module (one burst); a refused re-pick
  restores the live look or the safe look (§2.5), never leaves the draft plan up. `PublishPlan.driven` = the current
  plan's modules **whenever a try-on runs** (the re-pick happens then even if its scene didn't change), so the
  confirm's cost is true. A try-on started after a publish patches the live trio (`show_trio`, 2.4 #16).
- **Reconciler:** pending pairs are skipped entirely in `check_slots` (B2, §2.5); during the quiet window the tick and
  reassert hold still (`commissioning` property); after an applied repaint the record equals the compiled hash, so the
  next true-up sees "current". A failed module's `slot_written` is the attempt's time, so the stale rewrite comes
  ≥ 600 s later, under `rewrite_slot`'s `_slot_lock`. Commissioning and a repaint never overlap (`commission` refuses
  while `repainting`; `looks_refusal` refuses while commissioning). Gate R can't start during a repaint
  (`_rehearsal_checks` refuses while quiet) and a publish is refused during Gate R.
- **Ripple:** a looks re-pick is one burst (`ONE_BURST_SOURCES` contains `RECOMPILE_SOURCES`).
- **Idle / idle_scene / safe look / guest brightness:** unchanged; they compile from the live trio.

### 2.10 T1 and slot records on kits without Gate C / map
See §2.6.0 and §2.6's "partly commissioned" paragraph: a kit without its own Gate C and map is never repainted and
never blocks the others; T1 for the scene stays off until every canopy position is commissioned and current (today's
`tier_allowed` rule plus the hold). The Lab's T1 lock becomes the app lock + (B1) the api-record lock (§2.13).

### 2.11 Routes and JSON (`conductor/routes/design_lab.py`, ops only, `_no_cross_site`, `no-store`, `LabRoute`)
| Method & path | Request | 200 | Errors | Telemetry `kind="lab"` |
|---|---|---|---|---|
| `GET /api/lab/live` | — | `LiveJSON` | — | — |
| `POST /api/lab/publish` | `{version: "v0007" \| "approved" \| "previous", confirm?: str, expected_live: str, who?: str (≤ 40), show?: bool}` | `{live: LiveJSON, report: PublishReport, state: StateJSON}` | §2.8 | `publish_refused` (why), `publish` (id, version, name, by, changed, repaint scenes, sent), `publish_failed` |
| existing `GET /api/lab/catalog` | — | + per scene `live_look: SceneDraft`, `live_source: "approved"\|"published"`, `repaint: RepaintInfo` (replaces `t1_lock`'s meaning, below); top level `show_looks: {id, source, name}` | — | — |
| existing `GET /api/lab/state` | — | + `show_looks: {id, source, generation, fallback, repainting}` | — | — |
| existing `DELETE /api/lab/versions/{id}` | — | as today | **new** 409 "This version is live on the show; publish another (or revert) first." while `current.version == id` | — |
`expected_live` = the live `current.id`, or `"approved"` when current is null. `who` is stored as `by` (else "ops").
`show: true` (the dialog's "Show <scene> on the canopy after publishing" checkbox, offered when no changed scene is
showing) → after `reload_looks`, `engine.request_mode(<scene>, source="ops")` (one burst; the cost line counts it).
```jsonc
// LiveJSON
{"source": "approved" | "published", "simulated": false,
 "fallback": false, "problem": null,                       // problem set ⇒ the show runs approved because of it
 "current": {"id": "p0003", "kind": "version" | "approved", "version": "v0007", "name": "Client Oct 22 v3 – warmer sunset",
             "published_at": 1792700000.0, "by": "Alon", "changed": ["golden-hour"],
             "git_changed": false, "version_exists": true} | null,
 "previous": {"id": "p0002", "name": "...", "published_at": 1792690000.0, "by": "ops"} | null,   // Undo target
 "scenes": [{"id": "golden-hour", "name": "Golden Hour", "source": "approved" | "published",
             "palette": [{"name": "Peach", "hex": "#FFB38A"}], "mood": "...",
             "held": false}],                              // held = at module level until its slots hold the design
 "history": [{"id": "p0003", "kind": "version", "version": "v0007", "name": "...", "published_at": 1792700000.0,
              "by": "Alon", "changed": ["golden-hour"], "repaint": {"golden-hour": {"slot": "custom-3", "applied": 9,
              "skipped": 0, "failed": 0}} | null, "outcome": "live" | "replaced" | "rolled back" | "in git",
              "version_exists": true}],                    // newest first, ≤ 20 in the answer
 "repaint": RepaintJSON | null,                            // B2 (null in B1)
 "canopy": {"power": true, "brightness": 100, "designed_brightness": 100},   // for the "the canopy is off" note
 "writable": true}
// RepaintJSON (B2)
{"running": true, "queued_until": 1792700057.0 | null, "scene": "golden-hour", "label": "Golden Hour",
 "slot": "custom-3", "publication": "p0003", "started_at": null, "finished_at": null,
 "modules": [{"id": "canopy-r0c0", "label": "Module 1", "status": "pending" | "applied" | "skipped" | "locked" |
              "failed" | "mismatch", "reason": null}]}
// 409 confirm_required (POST /api/lab/publish without a matching confirm)
{"detail": "<confirm sentence>", "confirm_required": true, "confirm": "<confirm sentence>",
 "cost": "sends 2 writes now (one per module); no slot repaint",
 "replaces": {"id": "p0002", "name": "Client Oct 22 v2"} | null,
 "changes": [{"scene": "golden-hour", "name": "Golden Hour", "what": ["palette", "layout"], "needs_d59": true,
              "to": "published" | "approved"}],
 "repaint": [{"scene": "golden-hour", "slot": "custom-3", "modules": ["Module 1", "…"], "writes": 9, "in_s": 56}],
 "ends_trial": false, "show_offer": "golden-hour" | null,
 "notes": ["The canopy is off (guest power) — publishing doesn't turn it on."],
 "warnings": ["Golden Hour: reads as stripes at module level (D63)"]}
// PublishReport
{"id": "p0004", "sent": 2, "repick": true, "ended_trial": false, "copies": ["live looks", "show modes", "…"],
 "held": ["golden-hour"], "repaint": [{"scene": "golden-hour", "slot": "custom-3", "modules": 9, "in_s": 56}] }
// RepaintInfo (catalog, per painted scene; null for the other three)
{"slot": "custom-3", "commissioned": ["r0c0", "…"], "eligible": 5, "total": 9, "app": [], "simulated": true,
 "available": true,                                         // false in B1: a frame change over api records is refused
 "sentence": "<the count-based sentence of §2.6>"}
```
**Confirm sentence** (Lab side, `conductor/design_lab/publish.py`): `"Publish “<version name>” to the show<,
replacing “<live name>”>: <Scene A> and <Scene B> change<, <Scene C> goes back to approved>. The canopy shows it now
on <N> modules (<labels>) and after every restart."` + per held painted scene `" <Scene> shows at module level until
its slots are repainted."` + (B2) `" <Scene>'s painted design is repainted into <slot> on <M> modules<, in 56 s>
(one slot write each)."` + `" The running try-on ends."` when one runs. Revert: `"Revert the show to the approved
looks: <scenes> change back. …"`; Undo: `"Go back to “<previous name>”: …"`. Cost: `"sends <n> writes now (one per
module)"` (n = `driven`) + B2 `"; repaints <slot> on <M> modules: up to <3·M> slot writes (one each unless a write
doesn't land), <M> reads, then one T1 burst"` or `"; no slot repaint"`. On a simulator runtime the sentence starts
"Publish to this simulator …".

`conductor/design_lab/publish.py` (Lab side; calls only `conductor.looks.live` and the engine/wiring, never a driver
or executor — `tests/test_lab_access.py::test_no_lab_module_calls_a_writer` already walks every Lab source):
```python
def published_trio(lab: LabService, version_id: str) -> tuple[ConfigTrio, Version | None]
    # "approved" → lab.approved (git, deep copy); "previous" → validate_trio(file.previous.trio) (works when the Lab
    # version is gone or stale, as long as git's ids/ladders/slots haven't moved); else apply_drafts(lab.approved,
    # version.drafts, …) (DraftError → stale)
def plan_publish(lab: LabService, new: ConfigTrio) -> PublishPlan   # CPU: threadpool
@dataclass(frozen=True)
class PublishPlan:
    changed: list[str]; to_approved: list[str]           # vs the live trio (scene + its mode + the recipes it names)
    repaint: dict[str, RepaintTarget]                    # ShowWiring.repaint_plan(new) (B1: drives the lock only)
    locked: dict[str, str]                               # scene → §2.6.0 refusal sentence (app; B1 api)
    held: list[str]                                      # painted scenes that will run at module level after install
    driven: list[str]                                    # current plan's modules if its mode is in changed OR a
                                                         # try-on runs, else []
    generation: int; live_id: str                        # what the plan was computed against (the sync re-check)
    ends_trial: bool; equals_live: bool; warnings: list[str]; notes: list[str]
def confirm_sentence(lab, plan, *, name: str, kind: str) -> str
def cost_line(plan) -> str
REFUSALS: dict[str, str]                                 # looks_refusal code → the Lab sentence of §2.8
async def publish(lab: LabService, body: PublishBody, *, by: str) -> dict   # the §2.8 order
```

### 2.12 Telemetry and alerts
`kind="lab"`: `publish`, `publish_refused`, `publish_failed`, `looks_installed` (generation, copies), `repaint`;
engine `trial_end` with why "the look was published (…)"; executor command telemetry carries `source="looks"` and
labels `publish repaint: <label> → custom-N`. AlertBoard keys (`alerts.py` `_key_of`, add before the generic rule):
- "Published looks: overrides/show-looks.yaml couldn't be used…" → `looks:load`; resolved by `install_looks`
  (it records an info "Published looks: loaded …" carrying the same prefix).
- "Published looks: “<name>” is live and masks config/ changes…" → `looks:git`; resolved by the next install.
- "Published looks: <Scene>…" (repaint, can't-be-shown) → `looks:repaint:<mode_id>` — the event carries
  `mode_id=` and `_key_of` uses it — resolved only by that scene's own success info.
So scene B's success never clears scene A's warn, and a repaint success never clears a load-fallback warn. Slot keys
(`slot:<device>:<slot>`) clear on "… rewrote it (applied)" today; the pending marks keep the reconciler from raising
"waiting" ones during a repaint. `_mode_labels` reads `live_trio(services)` (2.4 #13).

### 2.13 Lab-side changes (P)
- `draft.py:194` `ConfigTrio` → `from conductor.looks.live import ConfigTrio`; `store.py:612` `config_sha`,
  `CONFIG_FILES` → `from conductor.looks.live import …` (re-exports; every Lab import keeps working).
- `preview.py:347-387` T1 lock → **`check_app_lock`** (refuses a painted design change only where an "app" record
  exists — the T2 truth rule, §2.6.0) plus, in B1 only, the api-record rule as a **warning** in preview/save
  ("publishing this needs a repaint, which isn't on this box yet") and a refusal at publish; `t1_lock()` keeps its
  keys and adds `repaint: RepaintInfo` (U reads `repaint`). D66's "read-only once commissioned" is superseded by D68.
  Update `tests/test_lab_preview.py:491-554`, `tests/test_lab_review_fixes.py:312`.
- `routes/design_lab.py` `catalog_json` (:276): `live_look` = `D.seed_draft(live_trio, scene.id)`, `live_source`;
  `state` per scene compares the current version's draft with the **live** seed (`"draft"` when it differs from the
  live look; `"in try-on"` as today); `show_looks` top level. `state_json`: `show_looks`. `delete` (:495): the 409.
- `trial.py:55` `show_trio` from `live_trio`; `_approved_name` → live name; the trial's "Back to approved" text in
  TrialJSON (`message`/route sentences) says "the show's look" where it meant the approved one.
- `export.py`: unchanged (exports a version against git). The export's "Deploy only in a commissioning window" note
  gains "— or publish it from the Lab" (one sentence; update its pin).

---

## 3. Stream U — the Lab as one system (laptop 1440×900, iPad 1180×820)

Files: `web/ops/showcase/lab.js`, `lab-model.mjs`, `lab.css`, `tests/lab-model.test.mjs`, `tests/test_lab_static.py`,
`tests/fixtures_live_lab/*.json` (§0), the `lab.css?v=` / `lab.js?v=` bumps to `lab-6` in
`web/ops/showcase/index.html` (two attributes only). No server file.

### 3.1 What "one system" looks like
- **Show bar** (top of the Lab, always visible, `role=status`): the live state in one line.
  - approved: `● Live on the show: the approved looks` · `git 3f9a1c` · chip **Live hardware** or **Simulator**.
  - published: `● Live on the show: “Client Oct 22 v3 – warmer sunset” · published 12:04 by Alon · Golden Hour differs
    from approved` · [**Undo last publish**] (when `live.previous`; back to “<previous name>”) · [**Revert the show to
    approved**] · [History].
  - simulator runtime (`live.simulated`): the line reads `● Simulator on <host>: nothing here reaches the canopy`, and
    every "Publish to the show" label reads **Publish to this simulator** ("to the show" only when `simulated` is
    false).
  - a held painted scene (`scenes[].held`): "Golden Hour shows at module level until its slots are repainted" (+ B2
    "repaint in 56 s" from `repaint.queued_until`).
  - fallback (`live.fallback`): a red line "The published looks couldn't be loaded (<problem>), so the show runs the
    approved looks. Publish again, or Revert to approved." (not dismissible while it stands).
  - `git_changed && !equals_approved`: an amber note "config/ changed since this was published; the show still runs
    the published looks." `equals_approved`: "(the same as approved — it's in git)".
  - repaint running: "Repainting Golden Hour into custom-3: 6 of 9 modules done" + a per-module list on tap
    (`repaintProgress`), then "Golden Hour's painted design is in custom-3 on 9 modules" (or the warn sentence).
- **Scene list** (each of the six): two badges — **Live: Approved / Published** (from `live_source`) and the working
  state **Live** (working draft equals the live look) / **Draft** (differs from live) / **In try-on**. Never the word
  "Approved" for a published look.
- **Where a draft starts:** a scene's working draft seeds from `live_look` (not `approved`) when there is no stored
  working draft; the editor menu "Reset draft ▾" offers **to the show's look** and **to approved (git)** (the old
  "Revert to approved" button was client-only and is renamed so it can't be confused with the show-level Revert).
- **Primary action — Publish:** in the versions bar and the editor header, one button:
  **Publish to the show** (the selected saved version differs from live), **Save & publish** (the working drafts
  differ from the selected version: save first with the note field, then publish the new version), disabled
  **Live now** (nothing differs). Flow: POST without `confirm` → 409 `confirm_required` → a dialog with the server's
  sentence verbatim, the cost line, the changes list (scenes, what changed, "changes an approved colour — needs the
  client's sign-off" when `needs_d59`), the repaint lines with modules, warnings (D63, non-blocking), "The running
  try-on ends", the `notes` (canopy off / guest brightness low), a checkbox **Show <scene> on the canopy after
  publishing** (on by default, when `show_offer`), a field **Your name, for the show's history** (asked once per
  browser, kept as a UI pref in localStorage, sent as `who`) → **Publish now** (sends `confirm` = the sentence,
  `expected_live` = the live id the dialog was opened from) / Cancel. A 409 `conflict` re-shows the dialog from the
  answer's `live` with its sentence on top; a 409 with `retry_after_s` shows its sentence with a countdown on the
  button; a plain 409 shows its sentence in `#lab-alert` (the `field` one marks the scene); 422/stale marks the version
  stale. Success: toast "Live now: <name>", the show bar and badges update from the answer's `live`, the stage's
  "today" view refetches.
- **Revert the show to approved** / **Undo last publish:** the same flow with `{version: "approved"}` /
  `{version: "previous"}`; styled secondary/danger; the dialog's sentence is the server's. Never blocked by the repaint
  limit (it queues).
- **History** (a drawer from the show bar; ≤ 20 rows from `live.history`): name, time, by, scenes, repaint outcome,
  outcome badge; actions **Open in editor** (restore the version into the working drafts, existing restore route),
  **Publish again** (if `version_exists`), **Export to git** (the existing export dialog for that version).
- **Versions from elsewhere (one system across laptop, staging and the box):** the versions bar gets **Download
  version** (a `.json` of the existing `GET /api/lab/versions/{id}` answer: name, note, drafts, config_sha) and **Open a
  version file** (`<input type=file accept=".json,application/json">`), which POSTs its `drafts` to the existing
  `POST /api/lab/versions` with `name` and `note: "from <original name>"` (≤ 60 chars) — same validation, app-lock and
  stale rules as a save. No server route.
- **Try-on** stays as built; its labels follow the server (the show's look). Present: the badge says Live / Draft.
- **What the iPad shows:** a small strip under the scene list, "On the iPad now:" + the six cards' names and swatches
  from `live.scenes`, with "Drafts and try-ons never change the iPad's cards; a publish does." (a try-on changes the
  lights only, D66).
- **Staying in sync:** poll `GET /api/lab/live` every 5 s while the page is visible (and on `visibilitychange`/focus),
  every 1 s while `repaint.running`; when `current.id` changes from elsewhere: a notice "The show changed: “<name>”
  was published at 12:10" and the badges recompute; working drafts are never overwritten.

### 3.2 `lab-model.mjs` additions (pure; node tests)
```js
export function liveBadge(scene)                         // 'Approved' | 'Published' (from catalog scene.live_source)
export function workingState(working, liveLook, trial)   // 'live' | 'draft' | 'in try-on' (draftEquals vs liveLook)
export function seedFor(scene, stored)                   // stored working draft, else scene.live_look
export function publishAction({working, selected, live}) // {label: 'Publish to the show'|'Save & publish'|'Live now', enabled, version}
export function publishDialog(body409)                   // {sentence, cost, changes:[…], repaint:[…], warnings, endsTrial}
export function showBar(live, catalog)                   // {tone: 'ok'|'warn'|'error', text, actions}
export function repaintProgress(repaint)                 // {text: '6 of 9 modules done', done, rows:[…]}
export function historyRows(history)                     // rows with action flags
export function liveNotice(prev, next)                   // null | 'The show changed: …'
export function gateCountdown(retryAfterS, nowMs, sentMs)// seconds left, never negative (retry_after and queued_until)
export function versionFile(versionJson)                 // the download's object (name, note, drafts, config_sha)
export function readVersionFile(text)                    // {name, note, drafts} | {error} (bad JSON, no drafts)
export function publishLabel(simulated, base)            // 'Publish to the show' ↔ 'Publish to this simulator'
```
### 3.3 Touch and layout
All new targets ≥ 44 px, text inputs 16 px, the confirm dialog scrolls inside itself and fits 1180×820, the show bar
wraps to two lines < 1280 px, no hover-only affordance; system font stacks; no CDN (static test).

---

## 4. File ownership

### 4.1 Per stream
| Stream | Owns (creates or edits) |
|---|---|
| R | every file in §1.1 (deletes) and §1.2–§1.4, incl. `conductor/app.py`, `conductor/config.py` (registry region :245-600 only: `_merge`, `save_overlay_patch`, `overlay_diff`), `conductor/services.py` (problems lines), `conductor/preflight.py` (new), `deploy/mac/run-conductor.sh` (`--check` runs preflight), `conductor/routes/ops.py` (the Clock-item hunks and comments only: :30, :116-118, :905, :950-954, :976-978), `config/devices.yaml`, `conductor/roles.py`, `roles_api.py`, `routes/core.py`, `ws.py`, `wsauth.py`, `routes/devices.py`, `routes/module.py`, `module/manager.py` (vignette lines only), `bench/runner.py`, `rehearsal.py`, `canopy.py:1231` (one sentence), `sim/profiles.py`, `guest_panel.py` (all but `scenes()`), `routes/guest.py` (docstring), `scripts/dev_design_lab.py`, `deploy/mac/*` (comments/messages), `Makefile`, Studio files (`studio.js`, `demo-model.mjs`, `showcase/index.html` pair elements), `web/ops/{index.html,ops.js}`, `web/ops/module/module.js`, kiosk comments, the tests of §1.3 + `tests/test_canopy_only.py`, docs of §1.4, D67, STATUS (PR A) |
| P | `conductor/looks/live.py` (new), `conductor/design_lab/publish.py` (new), `conductor/show.py`, `conductor/canopy.py` (all but :1231), `conductor/config.py` (`LabSettings` :201-207 only), `config/conductor.yaml` (`lab:`), `conductor/guest_panel.py` (`scenes()` :1045-1046 only), `conductor/routes/kiosk.py`, `conductor/routes/ops.py` (`mode_list` :183-189 and its callers only), `conductor/routes/devices.py` (`runtime_problems` :261-268 only), `conductor/app.py` (the lifespan's `build_live_looks` line only), `conductor/reconciler.py` (B2: the one `check_slots` line), `conductor/preflight.py` (B1: the looks line), `conductor/alerts.py`, `conductor/routes/design.py`, `conductor/module/manager.py` (:56-59 + new `install_looks`), `conductor/routes/design_lab.py`, `conductor/design_lab/{draft.py (ConfigTrio re-export), preview.py, trial.py, export.py (one sentence), service.py (live_changed)}`, tests `tests/test_live_looks.py`, `tests/test_lab_publish.py`, `tests/test_live_lab_routes.py` (new), the Lab test updates of §2.13, `tests/test_writer_paths.py` (+1 entry), `tests/test_kiosk_routes.py`/`test_ops_routes.py`/`test_alerts*` updates for the new reader signatures, D68, 07 §6/§8 + 05 #3 text, STATUS (PR B server part) |
| U | `web/ops/showcase/lab.js`, `lab-model.mjs`, `lab.css`, `tests/lab-model.test.mjs`, `tests/test_lab_static.py`, `tests/fixtures_live_lab/*.json`, `web/ops/showcase/index.html` (the two `?v=lab-6` attributes only) |

### 4.2 Shared files and how the merge resolves
| File | R | P / U | Resolution |
|---|---|---|---|
| `conductor/guest_panel.py` | workshop cut | P: `scenes()` body | take R's file, re-apply P's two lines |
| `conductor/module/manager.py` | :29-38, :138-145 | P: :56-59, new method | disjoint hunks |
| `conductor/config.py` | :245-600 | P: :201-207 | disjoint |
| `conductor/routes/ops.py` | :30, :116-118, :905, :950-954, :976-978 (Clock text, comments) | P: `mode_list` :183-189 + callers | disjoint hunks |
| `conductor/routes/devices.py` | :4-9, :146, :186-222, :272-275 | P: `runtime_problems` :261-268 | disjoint hunks |
| `conductor/app.py` | :30, :56, :66-77, :171-190 | P: one `build_live_looks(services)` line in the lifespan after the AlertBoard | disjoint |
| `conductor/preflight.py` | creates it (registry, roles, settings) | P (B1): adds `load_live_looks` + its line | P rebases its hunk on R's file |
| `conductor/canopy.py` | :1231 sentence | P: everything else | disjoint |
| `tests/test_writer_paths.py` | removes entries | P: adds `"lab publish (slot repaint)"` | union of the dict |
| `web/ops/showcase/index.html` | pair elements, `studio.js?v=` | U: `lab.*?v=lab-6` | disjoint lines |
| `docs/context/07`, `05` | §1, §2, §10, **§8's venue-box sentence (:70)**; 05 diagram/#11/#13, **#3's "vignette controls"** | P: §8's new paragraph (B1), §6's (B2); 05 #3's new clause | disjoint sentences |
| `docs/STATUS.md`, `docs/DECISIONS.md` | PR A block, D67 | PR B / B2 blocks, D68 | integrator: B2's above B's above A's; D67 then D68 |
| `tests/test_lab_access.py` | `ENROLLED` | P: nothing (the route walk covers new routes) | — |

---

## 5. Tests (each new guard mutation-checked: break it, see the named test fail, restore)

### 5.1 R — §1.3, §1.6 (mutation targets listed with `tests/test_canopy_only.py`).

### 5.2 P (virtual clock; `tests/fake_compiler.py` worlds for the engine; the `Show` harness of
`tests/test_stepped_rehearsal.py` / `tests/test_lab_trial.py` over the shipped `config/` for show-level tests, waiting
on `ShowWiring.started`, `tests/show_started.py`; `no_threads` for route timing)

**B1**
- **Overlay load** (`test_live_looks.py`): missing → approved, no alert; valid → published (plan T3 colours = the
  draft's); each fallback cause parametrized (oversize never read — patch `Path.read_bytes`/`read_text` to fail;
  bad YAML; deep YAML (RecursionError path); an anchor/alias (and a billion-laughs file: returns within 1 s); bad top
  level; unknown format; version without trio; `trio_sha` mismatch; schema refusal; a ladder that differs from git;
  `idle_scene` refusal; `run_s` > `idle_return_s`; `validate_trio` monkeypatched to raise `RuntimeError`) → approved +
  `problem` + warn alert (`looks:load`) + a `runtime_problems` line, **file bytes unchanged**, and the app starts
  (`TestClient` lifespan, `/healthz` 200, `/api/kiosk/panel` 200); then a publish → `runtime_problems` has no
  "Published looks" line and the AlertBoard no `looks:load`; a bad history row keeps `seq` and `repaints`; a version
  equal to git → followed as approved, the file rewritten with `current.kind: approved`, and a later git change is
  used after the next restart; a differing version with `git_changed` → a warn on /ops (`looks:git`); the modes dump
  round-trips through `parse_modes`; `live.py` imports nothing from `conductor.design_lab` (AST).
- **`test_publish_refreshes_every_copy_without_a_restart`** (the checklist test; parametrized over §2.4's table rows
  1–16 and 19 so a removed line fails its own case): publish a Golden Hour draft renamed "Pinker Hour" with Sunset pink
  `#FF9BC4 → #FF8FBF` and a layout change; then assert, without restarting: `live_looks().trio` is the new trio and
  generation +1, and it shares no object with `lab.approved`; `wiring.modes_file is live.modes`,
  `wiring.context().recipes is live.recipes`; `wiring._rehearsal_modes == {}` and `wiring.rehearsal_modes("golden-hour")`
  derives from the new design; `engine._t1_plans` recompiles to the new hash; `GET /api/kiosk/panel` scenes show
  "Pinker Hour" and `#FF8FBF`; `GET /api/kiosk/modes` label, module colours and each mode's `pattern` (:487);
  `GET /api/canopy/design?mode=golden-hour` heads; `/api/canopy/state` `modes` labels (ops `mode_list`); an alert
  label (`AlertBoard` labels); `ModuleManager.of(s).recipes/modes` and an **open** session's `deps.recipes/modes`;
  Lab catalog `live_look`/`live_source`; `trial.show_trio(lab)` contains the new scene; the engine's current plan
  (when Golden Hour is showing) carries the new feature/colours; `_t1_hold` recomputed.
- **`test_no_live_reader_uses_the_git_defaults`**: after a publish, monkeypatch `compile._default_modes`,
  `compile._default_recipes`, `scenes.load_scenes` (and its `guest_panel` import site) and `routes.kiosk._read_yaml`
  to raise, then hit every live surface (kiosk panel/modes/select, `/api/canopy/state`, `/api/canopy/design`,
  `/api/ops/checklist`, the module page's Lowcountry preview, an engine re-pick, a try-on start) → all 200.
- **Re-pick:** current mode changed → exactly one command per driven module (one burst), guest brightness kept; current
  mode unchanged → zero writes; with `ripple_stagger_s: 1.2` still one burst; `show: true` → one burst of the scene.
- **T1 truth / hold:** Golden Hour showing at T1 (sim, all nine recorded), publish a layout change (B1: Revert path;
  B2: publish) → `engine.current_plan().tier` is T3 and its feature is the T3 rung's sentence; after the records
  become current (B1: the reconciler's stale rewrite; B2: the repaint) → T1 with exactly one PUT per module; with one
  module left stale it stays T3 over 20 virtual minutes until that module is current; after a restart with a stale
  record the hold is seeded.
- **Re-pick failure:** `compile_best` returns None after a publish during a try-on → `engine.current_plan()` is the
  live look or the safe look, never the draft plan; `reload_looks` raising → the publish rolls back and the current
  plan was compiled from the previous trio.
- **Restart:** publish, `close()`, rebuild services over the same data dir → the show compiles the published trio, no
  slot write after QUIET_S over 20 virtual minutes, `stale_slot` None everywhere, no "pattern in custom-" alert.
- **B1 locks:** an app record on one module + a palette/layout change → 409 with `field`, nothing written; a
  brightness-only change → accepted; (B1) api records + a layout change → 409; Revert to approved over api records →
  accepted, the scene held at T3, the reconciler's stale rewrite follows.
- **Kits without Gate C/map** (`engine_driver.simulated=False`, no flags): publish → zero slot writes, `RepaintInfo`
  sentence (a), module colours updated.
- **Refusals:** each row of §2.8 parametrized (the Lab sentences pinned); nothing written (file bytes,
  `registry.yaml`, driver writes) on every refusal; paused → allowed; concurrent publishes → one 200, one 409;
  **conflict:** two clients, B publishes between A's `confirm_required` and A's confirm → A gets `conflict: true`
  with `live`, and the overlay holds B's bytes; DELETE of the live version → 409.
- **Undo:** publish v1, publish v2, delete v1 in the Lab, `{version: "previous"}` → v1's trio is live (from the stored
  `previous`), `previous` is now v2.
- **Try-on:** publish during a try-on → `engine.trial is None`, one burst total, `trial_end` why mentions publish,
  `PublishPlan.driven` non-empty even when the try-on's scene isn't in `changed`; a try-on after a publish keeps the
  other scenes' published looks.
- **Rollback:** monkeypatch `install_looks` (and separately `reload_looks`) to raise → 500, the previous file restored
  byte for byte, `live_trio` the previous trio, the current plan re-picked under it.
- **Write failure:** `atomic_write` raising OSError → 409 `read_only`, `live_trio` unchanged, generation unchanged.
- **Revert to approved:** `current.kind == "approved"`, trio git, restart follows git; a later git change is picked
  up after a restart.
- **Copy truth:** after any publish, `engine.current_plan().feature` equals the live mode's rung sentence for the tier
  actually in use; the D62 partial-canopy sentence still appears with two registered modules.
- **Alert keys:** scene B's success info doesn't clear scene A's repaint warn; a repaint success doesn't clear
  `looks:load`.
- **Routes** (`test_live_lab_routes.py`): every answer matches the shapes of §2.11 and U's
  `tests/fixtures_live_lab/*.json` key sets; ops only (kiosk/tester 401/403 past the path table); same-origin + JSON.
- **Mutation targets B1 (P must record each as killed):** each `install_looks` line (incl. the deep copy and
  `problem = None`); `context()`'s `self.recipes` fallback; `LOOKS_SOURCE` in `RECOMPILE_SOURCES` (guest brightness
  lost); write-before-install order (crash-after-write test); the invariant check (ladder test); `trio_sha` check; the
  alias rejection; the broad `except` around the overlay stage; the "file left in place" rule; the `equals git →
  approved` rewrite; `compile_best`'s hold skip; the hold-drop hook; `reload_looks`' refused-re-pick restore; the
  `expected_live` check; the app lock; the B1 api lock and its Revert/Undo exemption.

**B2** (`tests/test_lab_repaint.py`)
- **Repaint (sim, Show harness):** Golden Hour layout change → exactly one slot write per commissioned module (9),
  ≥ 1.0 s between writes to one device (sim stats `min_interval_violations_total == 0`), the registry overlay's
  `slot_hashes["custom-3"]` = the new compiled hash for all nine, `stale_slot` None, no reconciler rewrite and no
  "waiting" alert over 20 virtual minutes (`reconciler.stats["slot_rewrites"]` unchanged), then exactly one T1 burst.
- **Quiet window:** guest brightness taps every 0.5 s during a 9-module repaint → exactly 9 slot writes, 0 SUPERSEDED
  slot results, the taps answered "One moment: the canopy is changing." with `retry_after_s`; a reconciler tick and a
  Gate R start during the window do nothing / are refused.
- **Slot lock / drift:** a MISMATCH readback on a pending module during the repaint → no `rewrite_slot` started.
- **Failed repaint:** a sim fault fails one module → marks cleared, `slot_written` stamped, no reconciler slot write
  for 600 virtual s, one at 601 s; the scene stays held until then.
- **Partly commissioned:** 5 of 9 modules recorded with Gate C + map → 5 slot writes, records match, a 6th
  commissioned later gets the live design, and the confirm sentence (b) names 5 modules.
- **Locked / app:** a module held by the module page → `locked`, the reconciler takes it after release; an app record
  is never written (unit: `_commission_plan(min_interval=0)` skips it).
- **Queue + gate:** a frame-changing republish at 60 s → 200, installed at once (held at T3), repaint queued with
  `in_s ≈ 60`, no slot write before 120 s, one per module at 120 s; Revert within 60 s → installed at once (never
  409); a brightness-only republish → no repaint; the gate after a restart (persisted `repaints`, `queued`
  re-queued); a wall-clock jump of +1 h / −1 h → the wait stays ≤ the interval.
- **Job bookkeeping:** a refusal at job start (outage) → `_repaint_pending` empty; two painted scenes in one publish →
  `repainting` True from publish until the second scene's finally; a newer publish cancels a queued job and its marks.
- **Writer path:** `WRITER_PATHS["lab publish (slot repaint)"]` — `engine.start_repaint(...)` on the FakeCompiler
  world; denied for `SPARE_MD`, reaches the wire for `BENCH_MD`.
- **Mutation targets B2:** the `check_slots` pending guard; the `commissioning`→`quiet` property; `wait_idle` before
  the first header read; `_slot_lock` in the repaint; the submit-time `slot_written` stamp; `_commission_plan`'s
  `min_interval` override (→ 600 s, the republish test fails) and its `only` filter; the gate clamp; `start_repaint`'s
  own `due` enforcement; `commission`'s refusal while repainting; the generation abort.

### 5.3 U
`tests/lab-model.test.mjs`: every §3.2 function, incl. `publishAction` for the three labels, `workingState` with
lower-case hexes (`draftEquals`), `showBar` for approved / published / fallback / git-changed / equals-approved,
`repaintProgress` counts, `gateCountdown` never negative, `liveNotice` only on an id change, the version-file round
trip (`readVersionFile(JSON.stringify(versionFile(v)))` gives back the drafts; bad input → error), `publishLabel` for
both runtimes. `tests/test_lab_static.py`: the publish dialog exists, the poll interval constants (5000, 1000), the
file input accepts only `.json`, no `http(s)://`, no `localStorage` key for publish state (only working drafts and
UI prefs such as the publisher's name), no import of `SCENES`/`scenePainters`. Browser walk (Playwright on
system Chrome against `scripts/dev_design_lab.py --port 8212`, 1440×900 and 1180×820 touch, after integration):
B1: publish a Golden Hour edit (confirm with the name field, success, badges, "shows at module level" note), a
conflict from a second tab, Undo last publish, Revert to approved, History → Publish again, Download version → Open a
version file in a second dev Lab (port 8213), the "Publish to this simulator" label, the fallback banner with a
hand-broken file. B2 (after step 4 of §0): repaint progress to 9/9 in sim, a republish within 2 min (installed at
once, "repaint in N s" countdown).
Mutation targets: `seedFor`'s live fallback, `publishAction`'s save-first branch, `workingState` vs live (not approved).

### 5.4 Whole suite (integrator, macOS)
`nice -n 15 uv run --offline pytest -q`; lint; `node --test tests/*.test.mjs`; `nice -n 15 uv run --offline python -m
conductor.soak` (exit 0; acceptance.yaml untouched; the soak's own data dir has no overlay → git; same rows as #27);
`LAB_ROUNDTRIP=1 make lab-roundtrip` once (the export path is touched by one sentence).

---

## 6. Contract text and decisions (drafts; R and P commit them in their PRs)

### 6.1 07 §1 / §2 / §10 (R)
- §1 table row `live`: "the show box (the Mac mini at the venue, D61) — the only runtime that ever holds show
  accounts". §1 last-but-one bullet: "A test enumerates every writer path (executor, reconciler, idle hold, slot
  manager, bench, module page, the Lab's try-on) and proves each is denied for a non-allowlisted id." (B2 adds "and
  the Lab's publish repaint".)
- §8 (:70): "a named volume with nightly backup on the venue box" → "the `/data` volume on Railway;
  `~/Library/Application Support/ColorCanopy/data` on the show box (D61), backed up nightly by `canopy-mac backup`".
- §2 WebSockets: "…and the guest iPad is WebKit." §2 Roles, replacing the bullet:
  "**Roles:** `ops` (the Basic credential: everything); `kiosk` (the guest iPad, enrolled once by a one-time link that
  sets an HttpOnly cookie: only `/kiosk`, `/api/kiosk/*`, `/api/ws-ticket`, `/api/version`, `/static/*`); `tester`
  (granted device ids: only `/ops/module` and `/api/module/<granted>` — never account connect, the bench gates, other
  devices, show accounts or the Lab). There is no vignette role (D67). A kiosk escape must not reach account connect,
  bench or the Lab."
- §10: replace "Venue box by ~Oct 8: Ubuntu 24.04 mini PC … Docker Compose + systemd …" with "**The show box (D61):**
  the Mac mini under launchd (`deploy/mac`, `docs/runbooks/mac-show-box.md`): a dedicated checkout at the release tag,
  a 30 s `/healthz` watchdog, a nightly backup, automatic login after a power cut (FileVault off), NTP checked by
  `canopy-mac status`. Hotfix path = PR → CI → merge → tag → the runbook's paste block; rollback = the previous tag."
  The cold-spare bullet: "a second Mac or laptop with a checkout at the tag and the last `canopy-mac backup`."
  "Chromium kiosk" → "the guest iPad in Guided Access (`/kiosk/`)".

### 6.2 07 §8 (P, B1) and §6 (P, B2)
- §8, after the overlay sentence (B1): "The show's **published looks** are one more overlay,
  `overrides/show-looks.yaml` (D68): the scenes/modes/recipes trio the show compiles from, written atomically (0600,
  a history copy first) before it is installed, validated at every load with the same schema, copy and stepped rules
  as git plus git's ids, ladders and slots; missing = the git defaults; unreadable or invalid = the git defaults with
  an /ops alert, never a failed start, and the file is left as it was; a published trio that equals git is followed as
  git. Git stays the record: a published version reaches git only through the Lab's export and a PR."
- 05 decision 3 gains (B1): "…index maps, slot hashes, and the published looks (07 §8)) go to an overlay under
  `CONDUCTOR_DATA_DIR`."
- §6, after "Rewrite a drifted slot at most once per 10 min, then alert — never loop 3.4 KB writes at show hardware."
  (B2): "**Operator repaint (D68):** publishing a new painted design from the Design Lab repaints that scene's slot,
  after a confirm naming the modules and the cost, only on modules whose slot was commissioned (origin "api") and that
  have their own Gate C and map; never over a hand-painted ("app") slot (a design change over one is refused). One slot
  write per module through the executor (≤ 1 write/s per device; retried at most twice more within the job only when
  it didn't land), header from the device's readback, inside a quiet window (no show, guest, reconciler or rehearsal
  command until it ends), the readback hash recorded exactly as commissioning records it. Until a module's slot holds
  the live design the scene runs below T1, so the copy is true. An operator repaints a given scene at most once per
  `lab.repaint_min_interval_s` (120 s): a sooner publish installs at once and queues the repaint; nothing ever blocks
  a revert. The reconciler's own drift/stale rewrite stays at once per `reconciler.slot_rewrite_min_interval_s`
  (600 s), counted from an operator repaint's attempt too, and never acts on a slot whose operator repaint is pending.
  For motion looks the re-select after a repaint is all nine as one burst."

### 6.3 05 (R)
Diagram: `Guest iPad (kiosk)   Ops dashboard + Design Lab   Canopy visualizer`; "The show runs on one box at the
venue that auto-starts on boot" stays with "(the Mac mini, D61)"; decision 11 → "**The canopy on its own Hubspace
account(s)**; the Conductor holds only those (the vignettes are outside this system, D67)"; decision 13 → "make-driven
dev; the Mac show box under launchd (D61); the guest iPad in Guided Access on `/kiosk/`; Railway staging for demos,
never show accounts". Decision 8's "multi-device vignette look" goes.

### 6.4 CLAUDE.md (draft for Alon to apply; agents don't edit it)
- line 3: "…a guest iPad panel driving **The Color Canopy** (9 Hubspace permanent-light strands, 270 heads) and staff
  tools (ops, bench, module page, the Design Lab, which publishes to the live show), all through the Afero cloud."
- Rules: "**Show accounts live only on the show box** (the Mac mini, D61) — never on Railway staging, in a cloud
  session, or in CI."

### 6.5 D67 (R, `docs/DECISIONS.md`, new dated section "2026-10-17 — canopy only")
**D67 — The Conductor controls the canopy only (supersedes the vignette parts of 05 #11/#13, 06 #11/#18, 07 §2's
vignette role, D34's room pages and P4; the Ubuntu venue box of 07 §10 and D61's "deploy/venue/ stays"; the VWBD
workshop kit, D56's workshop home and the Studio's paired shop-kit commands).** Alon's call on Oct 17 on site, after
the guest panel worked on the two live kits: "remove all the stuff that isn't the canopy control".
- **Gone:** the four vignette rooms (pages, service, routes, `config/vignettes.yaml`, their 15 registry entries and
  four accounts, the `vignette` role and its enrollment, the vignette WebSocket topic, the synthesized vignette sim
  profiles, Gate R's "vignette accounts clean" criterion); the Ubuntu venue box (`deploy/venue`, `scripts/release.sh`,
  the Chromium resting page, `venue-box.md`); the VWBD workshop kit and Paris's tooling (`workshop/`, the launcher,
  the kit builder, TEST-CENTER and its inventory, the Windows geometry tool, the Oct 2 hand-off docs); the guest
  panel's workshop target and `make guest-dev`; the Studio's "Both modules" pair and paired gradient
  (`/api/studio/pair/*`, `pair.py`, `scene_gradient.py`).
- **Kept:** everything the canopy needs (engine, executor, drivers, live gate, accounts, registry, bench gates and
  Apply map, the module page, /ops, the guest panel, the visualizer, the Studio's single-kit controls and the Design
  Lab, the Mac show box, the soak), the Dockerfile (CI builds and smoke-tests it), Railway staging, and every hardware
  evidence file. `CONDUCTOR_ENV=venue` remains the Mac's environment name.
- **Old data is tolerated, not migrated:** a saved enrollment or session of the removed role is skipped (the kiosk
  iPad's session survives); a registry-overlay entry for a removed vignette device, and a `vignette`/`kind` key on a
  surviving one, is ignored with a "Needs attention" line and pruned by the next /ops registry edit; a leftover
  `studio-gradients/` folder is ignored. `canopy-mac install` runs `conductor.preflight` against the data folder
  before it restarts the show. The vignettes, if the activation still has them, run on the stock Hubspace app outside this system.
- **Contract text changed:** 07 §1 (the show box, the writer list), §2 (the role table), §8 (the data folder's
  home), §10 (the Mac); 05 #3, #11, #13.

### 6.6 D68 (P, new section "2026-10-17 — the live Lab")
**D68 — The Design Lab publishes to the live show (supersedes D66's "nothing hot-applies config to a running show",
"approved looks reach the show only through git → merge → install", the T1 lock and "out of scope: hot-applying
config, slot repaints"; extends 07 §6 and §8).** Alon, Oct 17: "make the test lab work on the live system so that I
can just think of everything as one system and we can live iterate on site on the live show at the end of the install
before guests arrive."
- **Publish.** A saved Lab version (or **Revert to approved**, git's looks, or **Undo**, the previous publication's
  stored trio) becomes the show's look at once and after every restart: validated (schema, copy honesty, stepped and
  idle rules, git's ids/ladders/slots), written to `overrides/show-looks.yaml` before it is installed, swapped into
  every in-process copy in one step (`conductor.looks.live.install_looks`), and the engine re-picks the current look
  (one burst; guest brightness/power kept). Ops confirms the server's sentence (what it replaces, modules, cost,
  repaint, "the running try-on ends"); a publish computed against a look someone else has since replaced is refused
  as a conflict; the publisher's name is recorded. Refused while the engine is stopped, commissioning, resting
  (outage), rehearsing (Gate R) or a repaint is writing; allowed while guest input is paused. Versions move between
  the laptop, staging and the show box as version files (download / open), validated like a save.
- **Fail-safe.** A missing file is the approved looks; an unreadable or invalid one is the approved looks plus an /ops
  alert — never a failed start — and is left as it was. A published trio that equals git is followed as git (so a later
  deploy is never masked); one that differs from a changed git raises an /ops warn. `canopy-mac install` checks the
  data folder with the new code before restarting.
- **Painted scenes: copy stays true.** A painted scene whose commissioned slots don't hold the live design runs below
  T1 (module level) until they do. A design change over a hand-painted ("app") slot is refused. **Delivery:** in PR B
  (B1) a design change over commissioned ("api") slots is refused except for Revert/Undo (the reconciler's stale rule
  then repaints, ≤ once per 10 min per slot); in PR B2 the operator repaint lands: after the confirm, one slot write per
  module through the executor in a quiet window, only on modules with their own Gate C, map and api record, recorded
  like commissioning so the reconciler never calls the published design stale or reverts it; then one T1 burst. An
  operator repaints a given scene at most once per 2 min (`lab.repaint_min_interval_s`, Alon's choice): a sooner
  publish installs at once and queues the repaint, so a revert is never blocked. The reconciler's 600 s limit is
  unchanged (and counts from an operator attempt). On today's two kits (no Gate C or map) nothing is repainted and the
  design shows at module level.
- **Git stays the record.** The Lab's export of the published version is the PR that approves it; once merged and
  deployed the overlay equals git and is followed as git. Until then a deploy doesn't change a published look (an
  /ops warn names the masked change); Revert to approved takes git's.
- **The try-on stays** (temporary, module level); a publish ends it. Kiosk copy stays true: every published sentence
  passes the same rules as git's, the iPad's cards follow the live catalog, and the tier in use matches the slots.
- **Not changed:** `motion.stepped.live_enabled` (Gate R), `kiosk.ripple_stagger_s`, ladders, slot ownership; the
  Lab never changes them.

---

## 7. Risks
1. **The live box is running now.** No agent touches it. PR A, B and B2 reach it only through Alon's paste block
   (§8.1), each on its own, after merge, with a backup first, the previous SHA recorded, and `conductor.preflight`
   run by `canopy-mac install` before launchd restarts anything. The old code ignores `show-looks.yaml`, so rolling
   back B is safe; "Revert the show to approved" first if anything was repainted (B2).
2. **Old data under the new code** (PR A): a vignette session/enrollment, a registry patch for a removed id or a
   removed key on a surviving entry would lose the iPad's session or crash-loop launchd; §1.5's tolerances, their
   tests and the preflight are a release gate for PR A.
3. **A bad overlay must never stop the show:** load never raises for it (broad except, no aliases, size and depth
   limits); the tests start the real app over every fallback cause; the file is never moved; a publish validates
   before writing; the write is atomic with a history copy, on the loop thread, before the install.
4. **Repaint and throttling (B2):** slot objects are 3–5 KB; Gate R (the venue IP's write rate) hasn't run. A publish
   of one painted scene costs ≤ 9 slot writes (≤ 27 if writes don't land) + 9 reads + 9 look PUTs + one T1 burst,
   paced by the executor, in a quiet window, at most once per 2 min per scene. A failed module waits ≥ 600 s for the
   reconciler. First repaint on the show kits: watch `/ops` (writes in 60 s, 429/403) before the doors open.
5. **Masking git:** a published version hides later git edits until Revert to approved; /ops warns once git changed,
   and a version equal to git is followed as git.
6. **A mistaken publish mid-show:** the confirm names what changes and what it replaces; Undo last publish and Revert
   to approved are one step and never blocked. Runbook: no publish while guests are in the room.
7. **Rewriting the active slot** without a re-select is unbenched; B2 ends every repaint with a T1 burst, and B2 is
   deployed only after bench item 6 has been run once on a show kit.
8. **Hand-editing Paris's minified Studio** (R): keep the edit surgical; node tests + a browser load of the Studio.
9. **Merge conflicts** between R and P: §4.2 lists every shared file; P keeps away from R's regions.
10. **Commissioning at install** writes the live (possibly published) design — intended; commission after the final
    publish, or wait 10 min after a publish before commissioning a kit (the 600 s `slot_written` limit).
11. **Several people on the Lab with one ops login:** the `expected_live` conflict check and the recorded name keep
    one person from silently replacing another's look.

---

## 8. Schedule
| When | What |
|---|---|
| **Sat Oct 17, evening** | this plan; R, P, U start (worktrees from `6ce971b`) |
| Sat night | R done: PR A ready (suite, lint, node, soak, §1.6 grep, preflight, Studio/Lab browser load) |
| Sun Oct 18 | P B1 (overlay + load + checklist + hold + routes; checklist and fallback tests first) and U; then P B2 |
| Mon Oct 19 morning | integration (§0 steps 1–3), whole suite, soak, roundtrip, the B1 browser walk; PR B ready |
| Mon Oct 19 | the registry undo first (§8.1 step 1); Alon merges A → deploys A; then merges B → deploys B (separate moments) |
| Mon Oct 19 – Tue Oct 20 | §0 step 4: PR B2 ready (suite, B2 tests, browser walk B2) |
| Oct 20–23 install | register the nine kits, Gate C + map + commissioning per kit (bench runbook §5a); bench item 6 once on a show kit from `/ops/module`; then merge + deploy B2; Gate R if time allows |
| **End of install (Oct 22–23, before guests)** | live iteration: try-on → save → **Publish** → look at the canopy → adjust → publish again (painted repaints queued under the 2-min rule) → when happy, Export → PR → Alon merges (approve into git; the overlay is then followed as git) |
| Tours Oct 23–25, event Oct 29 | freeze: no publish during guest hours; the published look survives restarts; Undo / Revert to approved if anything looks wrong |

### 8.1 Deploy paste block (Alon, on the Mac; goes into STATUS too; coordinate with anyone on the iPad)
```
# 0. Before anything
canopy-mac backup
git -C ~/canopy-show status --short                     # must print nothing
git -C ~/canopy-show rev-parse HEAD | tee ~/canopy-prev-sha.txt
# 1. The Oct 19 registry undo, exactly as STATUS says, under the code already running, then verify it
# 2. PR A on its own
git -C ~/canopy-show fetch --tags origin
git -C ~/canopy-show checkout --detach <A tag or merge sha>
~/canopy-show/deploy/mac/canopy-mac install; rc=$?    # uv sync, then --check runs conductor.preflight, then restarts
if [ $rc = 3 ]; then  # the preflight rejected this checkout: nothing restarted, but any restart would start it
  prev=$(cat ~/Library/"Application Support"/ColorCanopy/state/installed_sha 2>/dev/null || cat ~/canopy-prev-sha.txt)
  git -C ~/canopy-show checkout --detach "$prev" && (cd ~/canopy-show && uv sync --frozen --no-dev --extra live --quiet)
fi                    # roll the checkout back at once (install printed the same line), then fix
canopy-mac status; git -C ~/canopy-show describe --tags --always
#    /ops "Needs attention": at most the "removed vignette devices/fields" lines; the kiosk iPad's panel still loads
# 3. PR B, later, the same way (<B sha>). Before: note the Lab show bar. After: it says the same name (or "the
#    approved looks"), /ops has no "Published looks: … couldn't be used" warn
# 4. PR B2, after bench item 6, the same way (<B2 sha>)
# Rollback, B2 or B only: Lab → Revert the show to approved (if anything was repainted), then
git -C ~/canopy-show checkout --detach <previous sha> && ~/canopy-show/deploy/mac/canopy-mac install
# Rollback, everything:
git -C ~/canopy-show checkout --detach "$(cat ~/canopy-prev-sha.txt)" && ~/canopy-show/deploy/mac/canopy-mac install
# Data restore only if the box won't start:  canopy-mac restore <file from step 0> --yes
```
(`canopy-mac install` removes the HOLD and restarts, so no separate `start`. Once tags exist, use tags, per
`mac-show-box.md` §5. `/api/version` says `commit: local` on the Mac; `describe` is the proof of what runs.)

---

## 9. Decisions for Alon (defaults taken; say if any should change)
1. **Painted slots after a broken overlay** go back to the approved design through the reconciler (default), rather
   than being held as published; the scene runs at module level meanwhile.
2. **A deploy doesn't change a published look that differs from git** (default; /ops warns); one that equals git is
   followed as git. Revert to approved takes git's.
3. **Publish is allowed while guest input is paused** and refused during an outage (default). The try-on is still
   refused while paused (D66), so try-on → publish with guest input on, or publish directly.
4. **D63 design warnings (two-d, kits-differ) don't block a publish** (default); they still block the git export.
5. **The Studio keeps its single-kit controls and its "String light 1" option** (default: only "Both modules" goes);
   say if the Studio should become screen-only, or drop `shop-string-1` with the pair.
6. **Cold spare** without the Ubuntu installer = a second Mac/laptop with a checkout and the last backup.
7. **Kept as evidence** although Paris-era: `docs/current-live-test-session.md` and
   `docs/live-workshop-2026-09-28.test-center.json` (04/06/stepped-rehearsal cite them).
8. **CLAUDE.md text** (§6.4) is drafted for you to apply.
9. **PR B split (B1 Oct 19, B2 after bench item 6 on a show kit).** Your repaint decisions are kept; only the date
   moves. Until B2, a painted design change over commissioned slots is refused (Revert/Undo excepted).
10. **The 2-min rule queues instead of refusing** (default): a sooner publish installs at once (module level) and the
    slot repaint runs when the 2 min are up; a newer publish replaces a queued repaint. Say if you'd rather a second
    design change of the same scene inside 2 min be refused (Revert/Undo would still never be).
11. **A painted design change is refused where any module's slot is hand-painted (app)** (default), so the iPad never
    describes colours the lights don't show.
12. **Guests are asked to wait during a repaint's quiet window** (about M·1.2+3 s: "One moment: the canopy is
    changing.") (default); this only matters if you publish with guests present.

---

## 10. Review log (Oct 17, three critics; each finding checked against `6ce971b`)
**Engine safety**
- E1 blocker, repaint without a quiet window → SUPERSEDED retries — **accepted** (executor.py:10-14, canopy.py:166-169, :808-810 confirmed): `quiet` property behind the public `commissioning`, refusals while writing, `wait_idle` first (§2.5, §2.6).
- E2 failed repaint stamps no `slot_written` → instant reconciler rewrite — **accepted** (canopy.py:2100-2101 vs reconciler.py:676): stamp at submit (§2.5).
- E3 no `_slot_lock`; drift branch can race — **accepted** (canopy.py:2170, :2406): `locked=True` per module + a one-line `check_slots` guard; the "no reconciler edit" sentence is gone (§2.5).
- E4 T1 shows the old slot under new copy — **accepted, moved**: the hold lives in `compile_best`, not `tier_allowed` as proposed (t1_target/stale_slot use tier_allowed, so a hold there would hide a failed module's staleness forever) (§2.5).
- E5 2-min gate blocks Revert/rollback; wall-clock bypass — **accepted**: install always, queue the repaint, clamp, enforced in `start_repaint` (§2.7).
- E6 `reload_looks` orphans the try-on plan; rollback doesn't re-pick; `driven` wrong — **accepted** (canopy.py:562-574 confirmed) (§2.5, §2.8, §2.9).
- E7 a trio equal to git masks later deploys; info alerts invisible — **accepted** (alerts.py:131-136): follow git + persist `approved`; warn when git changed. Option (c) rebase **rejected** (a partial merge of two trios is new surface, and the warn + Revert covers it).
- E8 fallback line stuck in static `services.problems` — **accepted** (services.py:78, devices.py:263): `runtime_problems` reads `looks.problem` (§2.3).
- E9 pending marks leak; `repainting` gaps — **accepted**: one job per publish, marks added in `start_repaint`, try/finally around all (§2.5).
- E10 kiosk.py:487 raw YAML reader missed — **accepted** (confirmed) (§2.4 row 11).
- E11 contract text overstates precision; motion re-select — **accepted** (§6.2, cost line, T1 burst re-selects all).
- E12 load robustness (RecursionError, aliases, lenient seq, config_sha import) — **accepted** (store.py:53 imports design_lab confirmed) (§2.2, §2.3).
- E13 threadpool write → re-check/restore path — **accepted**: sync write in one section; the re-check path is gone (§2.8).
- E14 one `looks` alert key — **accepted** (§2.12).
- E15 lazy LiveLooks off-loop; shared sub-models — **accepted**: eager build, deep copies (§2.2, §2.4).

**Removal**
- R1 `vignette`/`kind` keys crash-loop under `extra=forbid` — **accepted** (config.py:68-69, :290-291) (§1.2, §1.5).
- R2 `save_overlay_patch`/`overlay_diff` call `_merge` — **accepted** (config.py:493, :550-562) (§1.2, test 4).
- R3 deleting test_deploy loses the deploy/ secret scan — **accepted** (test_deploy.py:183-191) (§1.3, env example lines).
- R4 strike §5 and 7 am clock are Ubuntu commands — **accepted** (strike.md:48-55, seven-am-check.md:12-19) (§1.4).
- R5 routes/ops.py Clock copy names deleted tooling — **accepted** (ops.py:116-118, :950-979) (§1.2, §4).
- R6 15 vignette entries, not 17 — **accepted** (`grep -c` → 15) (§1.2, D67).
- R7 missed docs/comments; grep too narrow — **accepted** (§1.2, §1.4, §1.6).
- R8 role-cut tests thin — **accepted** (roles.py, ws.py:51-56 confirmed) (§1.3, test_canopy_only).
- R9 ruff doesn't catch unused constants; CLOSE_WAIT_S/HEADS are used — **accepted** (guest_panel.py:227, :251) (§1.2).
- R10 `displayLayout` default `'both'` throws; String light 1 option — **accepted**; keeping the option is §9 item 5.
- R11 pending studio gradient at deploy — **rejected as a deploy step** (neither Mac kit has T1/map, so no gradient can be pending, and the endpoint goes in A); noted in D67 that a leftover folder is ignored.

**Ops/UX**
- O1 deploy not paste-safe; no SHA; A+B together; no preflight — **accepted** (canopy-mac:225-235 confirmed): §8.1, preflight inside `--check` (no extra step, runs before the restart).
- O2 repaint window shows old slot under new copy — **accepted**, merged with E4 (§2.5).
- O3 partly commissioned canopy — **accepted**: per-module `repaint_plan`, count-based sentences (§2.5, §2.6).
- O4 T2 hand-painted fallback describes wrong colours — **accepted**: app lock (§2.6.0).
- O5 undo depends on a Lab version; live version deletable — **accepted**: `previous` with its trio, Undo, DELETE 409 (§2.2, §2.11).
- O6 two people with one login overwrite each other — **accepted**: `expected_live` conflict, `who` (§2.8, §2.11, §3.1).
- O7 no way to bring laptop/staging versions to the box — **accepted** UI-only (download/open file), simulator label (§3.1).
- O8 split B into B1/B2 — **accepted** (§0, §8, D68); Alon confirms (§9 item 9).
- O9 publishing an off-canopy scene shows nothing — **accepted**: `show` checkbox + canopy-off/dim notes (§2.11).
- O10 guest copy reused for ops refusals — **accepted**: Lab sentences (§2.8).
- O11 gate wait shown only after a 409 — **accepted in the queue form** (`in_s`, `queued_until`); the pre-press countdown label is moot once nothing is refused.
- O12 `publish` name clash in canopy.py — **accepted**: `LOOKS_SOURCE` etc. (§2.5).
- O13 07 §8 still names the venue box; deploy check of a surviving look — **accepted** (§6.1, §8.1 step 3).
