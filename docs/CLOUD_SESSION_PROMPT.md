# Starter prompt for a cloud session

Paste everything below the line at the start of a new cloud session (environment `canopy-dev`, branch `main`), then add
your task on the last line.

---

You're working on the Color Canopy control system (Home Depot × Hearst "Whole Home", Charleston: install Oct 20–23
2026, event Oct 29) in a Claude Code cloud session.

**Before you start**
1. Read CLAUDE.md and docs/STATUS.md (the current state and next steps), then whatever docs/context/01–07 and
   docs/DECISIONS.md your task touches.
2. Paris's VWBD field work is canon (D54–D58). The six approved scenes are config/scenes.yaml (D59), and the
   customer-facing iPad guest panel is /kiosk (D60, docs/design/guest-panel.md).
3. The session-start hook has run `uv sync`. Check with `make test` (about 4 minutes) before you change anything.

**Hard rules for this session**
- The simulator only. Never set HUBSPACE_LIVE, never contact the Hubspace/Afero cloud, never add credentials or
  tokens. This machine can't reach the shop panels, and the show accounts never touch a cloud session.
- To see the guest panel, run `make guest-dev` (simulator, port 8100) and use `?debug=1`.
- Work on a `claude/*` branch based on `main`. Run `make lint` and `make test` (and `make soak` for engine or reconciler
  changes) before committing.
- Open a PR against `main` that says what was verified and how. Never merge, approve or enable auto-merge; Alon
  merges. Never edit .github/workflows/.
- Update docs/STATUS.md when you finish a unit of work. Anything that needs real hardware goes under "Needs real
  hardware" for someone at the shop laptop.

**My task:**
