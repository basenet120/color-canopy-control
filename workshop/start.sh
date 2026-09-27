#!/usr/bin/env bash
# Color Canopy workshop — Mac / Linux starter. Usage: ./start.sh [sim|allow|setup|status]
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (a small Python manager) — one time only…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
echo "Getting the program ready (the first time takes a few minutes)…"
uv sync --frozen --no-dev --extra live --quiet
exec uv run --no-sync python -m conductor.workshop "${1:-run}"
