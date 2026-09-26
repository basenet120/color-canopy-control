# Status

_Last updated: 2026-09-26 — walking skeleton, before the cloud build session._

## PR stack (merge order)
None yet.

## Next step
Launch the cloud build session (`docs/CLOUD_SETUP.md`) with `docs/ULTRACODE_BUILD_PROMPT.md`. It starts with P0: the live gate and the bench.

## Done and verified
- **Context docs** `docs/context/01–07`: project, canopy design, Hubspace platform, strand device model, architecture decisions, open questions (each with a default), and binding engineering contracts written after an adversarial review against aioafero 9.3.0's source. Research evidence is in `research/`; read `research/ERRATA.md` first.
- **Canopy geometry** (`conductor/geometry.py`, `config/canopy.yaml`): 9 modules × 30 heads, staggered 8/7/8/7 at 10″ pitch, A/B orientation (B = A rotated 180°), a default serpentine wire order, and an optional calibrated `wire_order`. Tested:
  - The p14 scheme keeps the 10″ pitch and the stagger across every seam. The p18 appendix scheme breaks it in 24 places, so VWBD must confirm which one they're building.
  - Every wire hop must fit the fixed 19.7″ puck spacing. Same-x heads in this lattice are 20″ apart, so they can never be neighbours on the wire.
- **Auth** (`conductor/auth.py`): Basic auth on every HTTP and WebSocket request except `/healthz`. The app refuses to start without credentials on Railway, with `HUBSPACE_LIVE=1`, or outside sim mode. Tested.
- **App**: `/healthz`, `/api/version` (reports mode and live flag), `/api/canopy/layout?scheme=`, and `/visualizer/`, which renders all 270 heads, the wire order, index numbers, and pitch breaks per scheme. Checked in a browser.
- **Tests**: 23 passing (`make test`); ruff clean.
- **Simulator fixture**: `conductor/sim/profiles/hampton-bay-string-20seg.json`, taken from aioafero's tests at commit 58864da1 (MIT).
- **Deploy**:
  - `Dockerfile` with the `live` extra (aioafero) installed, one worker.
  - `railway.json` with the health check, one replica, and watch paths.
  - Railway staging runs in sim mode behind the password, with a `/data` volume. Verified: `/healthz` returns 200 open; everything else returns 401 without credentials and 200 with them.
- **CI** (`.github/workflows/ci.yml`): ruff, pytest, a gitleaks secret scan, a Docker build, and a container smoke test.
- **Cloud sessions**: a SessionStart hook runs `scripts/cloud_setup.sh`. It does nothing locally. In the cloud it installs the locked dependencies, including `aioafero==9.3.0`, on Python 3.12; verified by simulating `CLAUDE_CODE_REMOTE=true`.
- **Research harness**: `research/permanent-lights/offline_put_harness.py` re-proves offline that one PUT per module carries a full 30-head frame plus the select.

## Not built yet (the cloud build session's job)
Everything in the build order of `docs/ULTRACODE_BUILD_PROMPT.md`.

## Needs real hardware
The bench gates in `docs/context/04-device-model-permanent-lights.md`. Start with Gate A (a read-only profile dump) on one kit.

## Decisions needed
See `docs/context/06-open-questions.md`. The dated ones:
- Vignette surface, by Oct 1
- Golden Hour technique and kiosk look, by Oct 5
