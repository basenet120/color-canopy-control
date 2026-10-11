"""The shared static web assets: vendored (no CDN; the only web font is vendored with its licence) and app.js behaves (07 §2, §10)."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"


def test_static_assets_are_vendored():
    files = sorted(f for f in STATIC.iterdir() if f.is_file())
    assert {f.name for f in files} >= {"base.css", "app.js"}
    for f in files:
        text = f.read_text()
        assert not re.search(r"https?://", text), f"{f.name} references an external URL"
        assert "@import" not in text and "@font-face" not in text
    # the one vendored web font (the guest panel's Quicksand) ships with its licence; nothing else lives in fonts/
    for family in (STATIC / "fonts").iterdir():
        assert (family / "OFL.txt").is_file(), family.name
        assert {f.suffix for f in family.iterdir()} <= {".woff2", ".txt"}, family.name


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


def test_module_page_badge_follows_the_open_device_not_the_engine():
    """bench-ux F4: in bench mode /api/version says simulated (the engine) while the module page drives the real
    kit. The page must badge only from the open device's own `simulated` flag."""
    js = (ROOT / "web" / "ops" / "module" / "module.js").read_text()
    assert not re.search(r"\bsimBadge\(", js), "module.js must not use the global /api/version badge"
    assert "setSimBadge(st.simulated === true)" in js
    assert "setSimBadge(false)" in js  # the picker drives nothing
