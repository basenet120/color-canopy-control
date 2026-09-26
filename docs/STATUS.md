# Status

_Last updated: 2026-09-26 (walking skeleton, before the cloud build session)._

## Done and verified
- **Context docs** `docs/context/01–06` — project, canopy design, Hubspace platform, device model for the permanent-light strand, architecture decisions, open questions. Research evidence in `research/` and `research/permanent-lights/`.
- **Canopy geometry** (`conductor/geometry.py`, `config/canopy.yaml`): 9 modules × 30 heads, staggered 8/7/8/7 at 10″ pitch, A/B orientation (B = A rotated 180°), default serpentine wire order, calibrated `wire_order` override. 13 tests pass (`make test`), including:
  - p14 scheme keeps 10″ pitch and the stagger across every seam; the p18 appendix scheme breaks it (24 pitch breaks) — VWBD must confirm which they're building.
  - every wire hop must fit the fixed 19.7″ puck spacing; same-x heads in this lattice are 20″ apart, so they can never be neighbours on the wire (constrains VWBD's routing).
- **Conductor skeleton** (`conductor/app.py`): `/healthz` (open), `/api/version`, `/api/canopy/layout?scheme=`, `/visualizer/`; HTTP Basic auth on everything but `/healthz` when `CONDUCTOR_BASIC_AUTH` is set.
- **Visualizer**: renders all 270 heads, wire-order overlay, index numbers, p14/p18 toggle with pitch-break markers. Checked in a browser.
- **Deploy**: `Dockerfile` + `railway.json` (health check `/healthz`); Railway staging runs in simulator mode.
- **Cloud sessions**: `.claude/settings.json` SessionStart hook → `scripts/cloud_setup.sh` (no-op locally; in the cloud installs locked deps incl. `aioafero==9.3.0` on Python 3.12 — verified by simulating `CLAUDE_CODE_REMOTE=true`).

## Not built yet (the cloud build session's job)
Driver interface + SimDriver, Afero adapter, command executor, state tracker + reconciler, modes compiler, kiosk, vignette pages, ops dashboard, calibration tool, bench scripts, runbooks. See `docs/ULTRACODE_BUILD_PROMPT.md`.

## Needs real hardware
Everything in the bench gates of `docs/context/04-device-model-permanent-lights.md` — start with Gate A (read-only profile dump) on one kit.

## Blocked / waiting
See `docs/context/06-open-questions.md`.
