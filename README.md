# Color Canopy control system

Control software for the Home Depot × Hearst "Whole Home" activation at 284 Meeting Street, Charleston (Oct 20 – Nov 1 2026): a guest iPad panel that drives **The Color Canopy** — 9 Hubspace permanent-light strands, 270 heads — and the staff tools around it (ops, bench, the module page, the Design Lab), all through the Hubspace (Afero) cloud. The Conductor controls the canopy only (D67); the vignettes, if the activation has them, run on the stock Hubspace app.

**Status:** walking skeleton — canopy geometry, a password-gated app, and a canopy visualizer, deployed to Railway staging. The full build is specified in [`docs/ULTRACODE_BUILD_PROMPT.md`](docs/ULTRACODE_BUILD_PROMPT.md) and tracked in [`docs/STATUS.md`](docs/STATUS.md).

## Quick start
```bash
make setup   # uv sync (Python 3.12)
make test    # pytest
make run     # http://localhost:8000 → the canopy visualizer (never on the Mac show box: port 8000 is the show's)
make lab-dev # the Design Lab on the simulator: http://127.0.0.1:8200/ops/showcase/#lab
```

## Where things are
| Path | What |
|---|---|
| `docs/context/` | The spec: project brief, canopy design, Hubspace platform facts, strand device model, architecture decisions, open questions, engineering contracts |
| `research/` | Evidence behind the spec, with sources (Sep 2026) |
| `conductor/` | The Conductor service (FastAPI) |
| `config/` | Canopy geometry, devices, scenes, modes and recipes (defaults; ops edits go to an overlay) |
| `web/` | Browser surfaces: the guest panel (`/kiosk`), ops (`/ops`, bench, module page), the Studio + Design Lab (`/ops/showcase`), the visualizer |
| `docs/CLOUD_SETUP.md` | How to launch the cloud build session and connect Railway |

## Staging
https://conductor-staging-b94b.up.railway.app (Railway project `color-canopy`, environment `staging`, simulator mode, password-protected). Credentials are in Railway variables, never in git.

## Safety rules
Secrets never enter git. Nothing talks to real Hubspace devices unless `HUBSPACE_LIVE=1` is set and the device is on the allowlist. Show accounts live only on the show box (the Mac mini) — never on staging, in cloud sessions or in CI. Binding details: [`docs/context/07-engineering-contracts.md`](docs/context/07-engineering-contracts.md).
