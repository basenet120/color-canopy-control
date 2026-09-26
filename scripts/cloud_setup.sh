#!/usr/bin/env bash
# Installs Python dependencies at the start of a Claude Code cloud session. No-op everywhere else.
set -euo pipefail
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/..}"

if ! command -v uv >/dev/null 2>&1; then
  python3 -m pip install --quiet --user --break-system-packages uv
  export PATH="$HOME/.local/bin:$PATH"
fi
# aioafero needs Python >= 3.12. Use the image's interpreter; a download would be blocked by the sandbox proxy,
# so fail loudly instead of hanging on one.
export UV_PYTHON_DOWNLOADS=never
uv sync --frozen --all-extras
echo "cloud_setup: $(uv run python --version) ready; run 'make test'"
