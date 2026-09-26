# Color Canopy control system

Control software for the Home Depot × Hearst "Whole Home" activation in Charleston (install Oct 20–23 2026, tours Oct 23–25, event Oct 29, strike Nov 1): a guest touchscreen driving **The Color Canopy** (9 Hubspace permanent-light strands, 270 heads) plus staff control for four Hubspace vignettes, all through the Afero cloud.

## Read before working
1. `docs/STATUS.md` — where things stand, the PR stack, the next step
2. `docs/context/01-project-brief.md` — the activation, spaces, dates, scope decisions
3. `docs/context/02-color-canopy-design.md` — canopy geometry, orientation, Lowcountry Modes, Prototype 01
4. `docs/context/03-hubspace-platform-facts.md` — Afero API, auth, Conclave, aioafero behaviour, rate limits
5. `docs/context/04-device-model-permanent-lights.md` — how the strand is controlled (per-puck color, slots, effects, bench gates)
6. `docs/context/05-architecture-decisions.md` — **decided** architecture; don't relitigate
7. `docs/context/06-open-questions.md` — every unknown, with its default
8. `docs/context/07-engineering-contracts.md` — **binding** rules: live gate, auth, credentials, aioafero semantics, state, executor, persistence, delivery
Evidence with sources: `research/` — read `research/ERRATA.md` first (precedence + superseded claims).

## Rules
- **Never commit secrets.** Passwords are never stored anywhere; refresh tokens live only encrypted under `CONDUCTOR_DATA_DIR`. If you see a credential in a diff, stop.
- **Simulator by default.** Nothing calls the Afero cloud unless `HUBSPACE_LIVE=1`, and nothing writes to a device outside the allowlist (`07` §1). Tests never hit the network.
- **Show accounts live only on the venue box** — never on Railway staging, in a cloud session, or in CI.
- **Respect the rate budget**: ≤ 1 write/s per device, bounded concurrency per account. Never rotate accounts or IPs to get around throttling or a block.
- **aioafero is pinned** and imported only inside the Afero adapter.
- **Kiosk copy must be true** to what Hubspace is actually doing, at the tier actually in use.
- Keep `main` deployable. Work on a branch; open PRs that say what was verified and how. **Never merge, approve or enable auto-merge** — Alon merges. **Never edit `.github/workflows/`.**
- Update `docs/STATUS.md` whenever you finish a unit of work: PR stack in merge order, next step, what's verified (and how), what needs real hardware, what's blocked.

## Conventions
- Python 3.12, FastAPI, asyncio, pytest, ruff. Type hints on public functions. `make setup`, `make test`, `make lint`, `make run`.
- Config in `config/*.yaml` (defaults, in git) plus an ops overlay under `CONDUCTOR_DATA_DIR`; validate on load with clear error messages.
- Web surfaces are static HTML/CSS/JS served by the Conductor; vendor all assets (no runtime CDN); system font stacks.
- All timing through an injectable clock. Every command path is observable: send time, HTTP result, readback, errors.
