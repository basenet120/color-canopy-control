#!/usr/bin/env bash
# Paste this into the cloud environment's "Setup script" field (claude.ai/code → environment → Setup script).
# It runs once when the environment's machine is first built, and is cached when it finishes within about 5 minutes.
# The per-session dependency install is scripts/cloud_setup.sh, run by the SessionStart hook in .claude/settings.json.
# Nothing here touches Hubspace: cloud sessions run on the simulator only.
set -euo pipefail

# Python 3.12+ (aioafero needs it). Ubuntu 24.04's python3 is 3.12.
python3 - <<'PY'
import sys
assert sys.version_info >= (3, 12), f"Python 3.12+ needed, found {sys.version.split()[0]}"
PY

# uv (preinstalled on the cloud image; install it if a future image drops it)
if ! command -v uv >/dev/null 2>&1; then
  python3 -m pip install --quiet --user --break-system-packages uv
fi

echo "cloud_env_setup: $(python3 --version), uv $(uv --version 2>/dev/null || "$HOME/.local/bin/uv" --version), node $(node --version)"
