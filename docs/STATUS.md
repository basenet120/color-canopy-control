# Status

_Last updated: 2026-10-07. Paris's VWBD field work (Sep 28 – Oct 7, on two real kits) is merged into `main` as
canon (D54). One kit, Kingston, is commissioned for per-head frames. The show engine is not yet mapping-aware (gap
1 below)._

## PR stack (merge order — merge commits, not squash)
1. **`claude/paris-folder-consolidate-3addc3` → `main`**: the consolidation. It has two parts:
   - an import commit (`79a0082`): Paris's 2026-10-07 package source, verbatim on the kit commit it forked from
     (`1eb8551`);
   - a merge into `main` that resolves every conflict in Paris's favour.

   It also carries PR #10's STATUS commit, so **#10 is superseded**. PRs #1–#9 were already merged on Sep 27, and their
   remote branches are fully merged and can be deleted. **Merging this redeploys Railway staging.** Check the pre-deploy
   item under Next step first.

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

## Known gaps from the merge review — not fixed here (Paris canon; each needs a decision or bench time)
1. **The show path ignores the framebuffer mapping (high, before install).** The T1 compiler and composer,
   `/api/devices`, the attention list and the 7 am head-count check still assume frame length = heads. On real 60-entry
   modules they flag every module, and T1 canvas looks can't use them. Fix: port Paris's `framebuffer_mapping` into
   those paths. Main's removed 929c0d2 shows the shape, including the "the length is capacity, check the heads by eye"
   wording.
2. **`/` redirects to the Studio in every mode**, including the venue box and staging, where it can't connect (D56).
   Decide whether to keep it there or redirect only in the VWBD workshop.
3. **Bugs in Paris's code** (fixes drafted in review, not applied):
   - the scene-gradient restore loses preset speed and party-mode yet reports `ok`;
   - Start/Restart effect on a mapped kit rewrites entries 30–59 to OFF. That shifts the slot hash of an app-recorded
     T2 slot if the app fills those entries, which is unknown because no hand-painted app pattern has been dumped
     yet;
   - `effect_probe` ends on the probed slot instead of the previous look;
   - the original `address_probe` defaults to Custom 3 and never restores it;
   - a paired restart with a preset that has no brightness → HTTP 500;
   - a pending gradient record can block the feature (manual recovery: inspect
     `<CONDUCTOR_DATA_DIR>/studio-gradients/active.json`; the `backup-*.json` files keep the originals);
   - pair and gradient errors aren't logged;
   - module-page slot cards say "60 heads" on a 30-head mapped kit;
   - the "Presentation view" link shows for tester-role users.
4. **Test gaps:**
   - no route-level tests for `/api/studio/pair/*`;
   - the writer-path allowlist test doesn't list the new jobs;
   - Paris's JS tests and the tracker check aren't in `make test` or CI.
5. **The kit builder** (`scripts/make_workshop_kit.py`) would not ship `TEST-CENTER.html`, `tests/`, `tools/` or
   Paris's docs. Until it does, VWBD keeps working from Paris's own folder rather than a freshly built kit.
6. **The simulator still models the 30-entry sibling string.** The real Gate A dump now exists
   (`docs/bench-capability-check/gate-a-result.json`) but isn't the SQHDK03 fixture yet.
7. **Stale text in Paris's own files** (left as canon; fix with the next change to each):
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

## Next step
1. **Before merging:** check staging's `/data/overrides/registry.yaml` for `frame_offset`. Only main's Calibrate-tab
   frame-offset field (Sep 27 – Oct 7) could have written it. The registry forbids unknown keys, so remove it or staging won't load the
   registry after the redeploy.
2. Alon merges this PR. Then `git pull` in the main checkout (it was 84 commits behind) and delete the merged
   `claude/*` remote branches.
3. Decide gap 2 (home redirect) and the `address_probe` default slot. Then make one small fix-up PR for gaps 3–4.
4. **Port the mapping into the show path (gap 1).** This is required before the canopy's T1 looks can run on real kits
   at install (Oct 20–23); feature freeze target Oct 12. 07 §6 (binding) still says only "frame length = the device's
   framebuffer length". Alon words the mapping rule into it as part of the port.
5. At VWBD, with an observer and fresh full-slot backups, for String light 1: its own `gate_c_safe` physical answer →
   its own bounded `address_probe_safe` runs → record its own map → only then a live paired gradient. Don't clear the
   preflight to make a demo work.
6. Turn the Gate A dump into the simulator's SQHDK03 fixture (gap 6).

## Needs real hardware (UNVERIFIED)
- **The other kits.** Kingston is the only kit with a T1 flag and a map. Every canopy module needs its own Gate C and
  address map at install (~5 min each; D25, D54).
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
- **Home redirect** (gap 2, D56) and the **`address_probe` default slot** (D55).
- **Golden Hour technique and kiosk look** (06 #12–13, was due Oct 5). The new input is the bounded code-1 movement on
  Kingston; there is no direction or multi-controller phase data yet.
- **Vignette surface** (06 #11, was due Oct 1): thin `/vignette/<room>` pages are built. The Oct 12 fallback
  criteria are in D26.
- **Authorization letter** from Home Depot before any show-account writes (06 #20).
- **A second named person** with the show-account password and inbox (`docs/runbooks/outages.md`).
- Skim D51–D56 in `docs/DECISIONS.md`.

## Top risks
1. **The show path isn't mapping-aware** (gap 1). Without the port, T1 canvas modes on real 60-entry kits are refused,
   and the 7 am check fails every module.
2. **Gate C per kit:** proven on Kingston only, and String light 1's custom-frame tests are a NO followed by an
   unanswered run. Any canopy kit that fails Gate C falls back to T2 (hand-paint in the app, ~15–20 min per module,
   then "record it" on `/ops/module`) or T3.
3. **Real throttling** may differ from the soak's model; the rate-ramp job and the venue box measure it.
4. **The venue box is untested on real hardware.** Do the dry-run steps in `venue-box.md` §4 as soon as the mini PC
   arrives.
