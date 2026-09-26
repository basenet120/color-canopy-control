#!/usr/bin/env bash
# Installs Python dependencies at the start of a Claude Code cloud session. No-op everywhere else.
set -euo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/..}"

command -v uv >/dev/null 2>&1 || python3 -m pip install --quiet uv
# aioafero needs Python >= 3.12; uv reuses a system 3.12 if the image has one, otherwise downloads it.
uv sync --frozen --all-extras
echo "cloud_setup: $(uv run python --version) ready; run 'make test'"
