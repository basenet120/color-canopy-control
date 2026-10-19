"""The kiosk's pure picture and panel model under Node, in CI's pytest (D74).

CI runs only pytest (.github/workflows/ci.yml), so these node test files ran nowhere but a developer's machine until
now: tests/canopy-preview.test.mjs (web/kiosk/canopy-preview.mjs: the canopy, its apron and fins, finNeighbours and
finPaint, and the static checks on kiosk.js and kiosk.css), tests/guest-panel.test.mjs (web/kiosk/panel-model.mjs) and
tests/ops-ipad.test.mjs (web/ops/ipad-model.mjs, the /ops "On the iPad" card's words, D76). The one wrapper for the
three (integration C24: PR C's own ops-ipad wrapper folded in here, so no node file runs twice in CI). The same runner
and summary rule as tests/test_lab_static.py's lab-model wrapper.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FILES = ("canopy-preview.test.mjs", "guest-panel.test.mjs", "ops-ipad.test.mjs")


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node isn't installed; the kiosk's node tests are UNVERIFIED here"
)
def test_the_kiosk_picture_and_panel_model_under_node():
    node = shutil.which("node")
    result = subprocess.run(
        [node, "--test", *(str(ROOT / "tests" / name) for name in FILES)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    # Node's summary line is "ℹ fail 0" with the spec reporter (newer Node, macOS) and "# fail 0" in TAP (CI's runner)
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-2000:]
