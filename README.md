# Color Canopy control system

Control software for the Home Depot × Hearst "Whole Home" activation at 284 Meeting Street, Charleston (Oct 20 – Nov 1 2026): a guest touchscreen that drives **The Color Canopy** — 9 Hubspace permanent-light strands, 270 heads — plus staff tablets for four Hubspace vignettes, all through the Hubspace (Afero) cloud.

**Status:** walking skeleton — canopy geometry, a password-gated app, and a canopy visualizer, deployed to Railway staging. The full build is specified in [`docs/ULTRACODE_BUILD_PROMPT.md`](docs/ULTRACODE_BUILD_PROMPT.md) and tracked in [`docs/STATUS.md`](docs/STATUS.md).

## Quick start
```bash
make setup   # uv sync (Python 3.12)
make test    # pytest
make run     # http://localhost:8000 → the Studio (/ops/showcase/, D56; live only with VWBD's two kits, so add ?preview=1 in sim); canopy visualizer at /visualizer/
```

## Where things are
| Path | What |
|---|---|
| `docs/context/` | The spec: project brief, canopy design, Hubspace platform facts, strand device model, architecture decisions, open questions, engineering contracts |
| `research/` | Evidence behind the spec, with sources (Sep 2026) |
| `conductor/` | The Conductor service (FastAPI) |
| `config/` | Canopy geometry and, later, devices, modes and vignettes |
| `web/` | Browser surfaces (visualizer now; kiosk, vignette pages and ops dashboard to come) |
| `docs/CLOUD_SETUP.md` | How to launch the cloud build session and connect Railway |

## Staging
https://conductor-staging-b94b.up.railway.app (Railway project `color-canopy`, environment `staging`, simulator mode, password-protected). Credentials are in Railway variables, never in git.

## Safety rules
Secrets never enter git. Nothing talks to real Hubspace devices unless `HUBSPACE_LIVE=1` is set and the device is on the allowlist. Show accounts live only on the venue box — never on staging, in cloud sessions or in CI. Binding details: [`docs/context/07-engineering-contracts.md`](docs/context/07-engineering-contracts.md).
