# Color Canopy control system

Control software for the Home Depot × Hearst "Whole Home" activation in Charleston (install Oct 20–23 2026, tours Oct 23–25, event Oct 29, strike Nov 1): a guest touchscreen driving **The Color Canopy** (9 Hubspace permanent-light strands, 270 heads) plus staff tablets for four Hubspace vignettes, all through the Afero cloud.

## Read before working
1. `docs/context/01-project-brief.md` — the activation, spaces, dates, scope decisions
2. `docs/context/02-color-canopy-design.md` — canopy geometry, orientation, Lowcountry Modes, Prototype 01
3. `docs/context/03-hubspace-platform-facts.md` — Afero API, auth, Conclave, aioafero limits, rate limits
4. `docs/context/04-device-model-permanent-lights.md` — how the strand is controlled (per-head color, effects)
5. `docs/context/05-architecture-decisions.md` — **decided** architecture; don't relitigate
6. `docs/context/06-open-questions.md` — what's still unknown and who owns it
Deep evidence with sources: `research/*.md`. Source decks: `Ref Docs/`.

## Rules
- **Never commit secrets.** Hubspace passwords/refresh tokens live in env vars or the gitignored `secrets/` dir. If you see a credential in a diff, stop.
- **Simulator by default.** Nothing may call the Afero cloud unless `HUBSPACE_LIVE=1` is set and accounts are configured. Tests never hit the network.
- **Respect the rate budget.** Default ≤ 1 write/s per device, bounded concurrency per account. Never write code that rotates accounts or IPs to get around throttling or a block.
- **aioafero is pinned** and only imported inside the Afero adapter module.
- **Kiosk copy must be true**: it describes what Hubspace is actually doing.
- Keep `main` deployable. Work on branches; open PRs with a summary of what was verified and how.
- Update `docs/STATUS.md` whenever you finish a unit of work: what's done, what's verified (and how), what needs real hardware, what's blocked.

## Conventions
- Python 3.12, FastAPI, asyncio, pytest, ruff. Type hints on public functions.
- Config in `config/*.yaml`; validate on load with clear error messages.
- Web surfaces are static HTML/CSS/JS served by the Conductor; vendor all assets (no runtime CDN).
- Every command path is observable: log send time, HTTP ack, Conclave echo, errors.
