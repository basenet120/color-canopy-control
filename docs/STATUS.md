# Status

_Last updated: 2026-10-07. Paris's VWBD field work (Sep 28 – Oct 7, on two real kits) is merged into `main` as
canon (D54). One kit, Kingston, is commissioned for per-head frames. The follow-ups make the show engine read each
kit's own map (D57) and let `/ops/bench` record a kit's map from its own bench evidence (D58, **Apply map**; simulator
only so far)._

## PR stack (merge order — merge commits, not squash)
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
5. **`claude/guest-panel` (based on `d897843`, the six-scene catalog) → after the scene catalog**: the guest panel's
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
   Quicksand (OFL) is vendored under `web/static/fonts/` — **needs Alon's OK** (convention is system fonts; removing
   the two `@font-face` rules falls back to SF Pro Rounded). Hubspace logos in `web/kiosk/brand/` — **logo use needs
   Home Depot / Hearst brand confirmation**.
   **Verified (sim):** node + Python suites; driven with Playwright on `make guest-dev` at 1180×820, 1024×768 and
   820×1180 with touch emulation (scene, cooldown toast, slider drag pacing, power, debug, attract, frame preview; no
   page errors, no overflow). `tests_e2e/test_kiosk.py` rewritten for the panel but **not run** (the pinned
   Playwright browser isn't installed here). **Needs a real iPad:** Safari touch feel, Guided Access, home-screen
   web-app mode.

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
- **The other kits.** Kingston is the only kit with a T1 flag and a map. Every canopy module needs its own Gate C and
  address map at install (~5 min each; D25, D54, D57). The show engine has never sent a mapped T1 frame to real
  hardware, and Apply map (D58) has never recorded a map from real runs.
- Formal Gate B (slot select). No recorded run shows a slot select on the lights (the Sep 27 Slots-tab report has no
  run record); Gate C's BEFORE select was confirmed by readback only (CLOUD). Also readback semantics with the kit unplugged.
- **Effect codes:** only code 1 has been tried, bounded. Vendor name, direction, period and phase restart are
  unknown, and `effects.enabled` stays empty, so Golden Hour remains a static glow and says so. Two-controller phase
  alignment is unmeasured.
- Per-puck white and colour recipes through Mylar (Prototype 01); per-entry CCT/white fields; the hue ramp; 1–10 %
  dimming smoothness.
- Real latency, throttling (writes and the 1.8 GET/s true-up reads), token rotation, 401/403 semantics.
- Stale-clock writes, rewriting the showing slot, the 12-puck fault. Whether that fault shows in the cloud (frame
  length, `connected-light-count`) is unbenched (gate 10); until then the check is visual.
- Vignette device profiles and the deadbolt readback.
- The venue box itself: systemd, the Chromium kiosk and resting page, install from a tag, BIOS/UPS/NTP, cold spare.
- iOS Safari: enrollment cookies, beacon on page close, Guided Access.

## Decisions Alon owes
- **Golden Hour technique and kiosk look** (06 #12–13, was due Oct 5). The new input is the bounded code-1 movement on
  Kingston; there is no direction or multi-controller phase data yet.
- **Vignette surface** (06 #11, was due Oct 1): thin `/vignette/<room>` pages are built. The Oct 12 fallback
  criteria are in D26.
- **Authorization letter** from Home Depot before any show-account writes (06 #20).
- **A second named person** with the show-account password and inbox (`docs/runbooks/outages.md`).
- Skim D51–D58 in `docs/DECISIONS.md` (D58 is the map proof rule as built).

## Top risks
1. **Every canopy kit needs its own Gate C and map** before painted looks can use per-head frames (D57). Recording a map
   is now an Apply map tap on `/ops/bench` (D58), but that has never been used on a real kit. Show-engine T1 through a
   map is proven in the simulator only.
2. **Gate C per kit:** proven on Kingston only, and String light 1's custom-frame tests are a NO followed by an
   unanswered run. Any canopy kit that fails Gate C falls back to T2 (hand-paint in the app, ~15–20 min per module,
   then "record it" on `/ops/module`) or T3.
3. **Real throttling** may differ from the soak's model; the rate-ramp job and the venue box measure it.
4. **The venue box is untested on real hardware.** Do the dry-run steps in `venue-box.md` §4 as soon as the mini PC
   arrives.
