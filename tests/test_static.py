"""The shared static web assets: vendored (no CDN, no web fonts) and app.js behaves (07 §2, §10)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"


def test_static_assets_are_vendored():
    files = sorted(STATIC.iterdir())
    assert {f.name for f in files} >= {"base.css", "app.js"}
    for f in files:
        text = f.read_text()
        assert not re.search(r"https?://", text), f"{f.name} references an external URL"
        assert "@import" not in text and "@font-face" not in text


def test_base_css_has_the_shared_pieces():
    css = (STATIC / "base.css").read_text()
    for needle in ("#F96302", "#F7F1E6", ".sim-badge", ".pill.ready", ".pill.updating", ".pill.attention"):
        assert needle in css
    assert "--tap: 44px" in css
    assert "system-ui" in css


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node isn't installed; app.js behaviour is UNVERIFIED here"
)
def test_app_js_behaviour_under_node():
    node = shutil.which("node")
    result = subprocess.run(
        [node, str(ROOT / "tests" / "js" / "app_test.mjs"), (STATIC / "app.js").as_uri()],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "app.js ok" in result.stdout
