"""The Design Lab's page assets (docs/design-lab/PLAN.md §5): vendored, painting only through the server's design, with
the Studio's four hooks in place. The page model's behaviour is `tests/lab-model.test.mjs` (run here under Node when
it is installed, so CI's plain pytest runs it too)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SHOWCASE = ROOT / "web" / "ops" / "showcase"
LAB_FILES = ("lab.js", "lab.css", "lab-model.mjs")
DEMO_MODEL_URL = (
    "./demo-model.mjs?v=show-design-3"  # the URL studio.js imports: one module instance on the page
)
# demo-model's handoff palette and the painters that fall back to it (demo-model.mjs scenePainters): under a Draft
# label they would show Paris's handoff colours, not the draft's
FORBIDDEN = ("scenePainters", "sceneDesignView", "sceneColorAt", "SCENES")


def _text(name: str) -> str:
    return (SHOWCASE / name).read_text(encoding="utf-8")


def _code(text: str) -> str:
    """JS/CSS without its comments, for checks on what the code does (not what it says)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)(^|[^:\"'])//.*$", r"\1", text)


@pytest.mark.parametrize("name", LAB_FILES)
def test_lab_assets_are_vendored(name):
    text = _text(name)
    assert not re.search(r"https?://", text), f"{name} references an external URL"
    assert "@import" not in text
    assert "@font-face" not in text


@pytest.mark.parametrize("name", ("lab.js", "lab-model.mjs"))
def test_the_lab_never_paints_from_the_handoff_palette(name):
    text = _text(name)
    for word in FORBIDDEN:
        assert not re.search(rf"\b{word}\b", text), f"{name} mentions {word}"


def test_only_the_page_model_paints():
    """lab.js paints the canopy only with what lab-model's viewPainter returns: it never calls the design painter or
    the guest panel's painter itself, and the page model imports them from the same URLs the Studio uses."""
    js = _code(_text("lab.js"))
    model = _code(_text("lab-model.mjs"))
    assert "designPaint" not in js and "paintFor" not in js
    assert "M.viewPainter(" in js
    for call in re.finditer(r"paintCanopy\(([^;]*)\)", js):
        assert call.group(1).startswith("st.view, p.head, p.spill"), call.group(0)
    assert f'from "{DEMO_MODEL_URL}"' in js
    assert f'from "{DEMO_MODEL_URL}"' in model
    assert 'from "../../kiosk/canopy-preview.mjs"' in model  # = /kiosk/canopy-preview.mjs from /ops/showcase/
    assert 'from "/static/app.js"' in js
    assert "document" not in model and "window" not in model and "fetch(" not in model, (
        "the page model is pure"
    )


def test_the_studio_has_the_four_hooks_and_still_loads_studio_js():
    html = _text("index.html")
    assert '<link rel="stylesheet" href="./lab.css?v=lab-1">' in html
    assert html.index("studio.css") < html.index("lab.css")
    assert '<a class="text-link" href="#lab" id="lab-link">Design Lab</a>' in html
    assert '<section id="lab" class="lab" aria-label="Design Lab" hidden></section>' in html
    assert '<script type="module" src="./studio.js?v=show-design-3"></script>' in html
    assert html.index('src="./studio.js') < html.index('src="./lab.js?v=lab-1"')


def test_the_lab_has_one_alert_region_and_the_simulator_badge():
    js = _text("lab.js")
    assert (
        js.count('id: "lab-alert"') == 2
    )  # the shell before the catalog answers, and the built Lab (one at a time)
    assert 'role: "alert"' in js
    assert 'id: "lab-sim"' in js and "L.catalog.simulated" in js
    assert ".lab-alert {" in _text("lab.css")


def test_the_lab_opens_on_its_hash_and_hides_the_rest_of_the_studio():
    js = _code(_text("lab.js"))
    css = _code(_text("lab.css"))
    assert 'location.hash === "#lab"' in js
    assert 'addEventListener("hashchange", route)' in js
    assert 'classList.toggle("lab-open", open)' in js
    assert re.search(r"body\.lab-open main > :not\(#lab\)\s*\{\s*display:\s*none !important;", css)


def test_ipad_safari_never_zooms_and_targets_are_44px():
    css = _code(_text("lab.css"))
    assert re.search(r"\.lab input, \.lab select, \.lab textarea \{ font-size: 16px; \}", css)
    for size in re.findall(r"font-size:\s*(\d+)px", css):
        assert int(size) >= 10
    assert "--lab-tap: 44px" in css
    assert "touch-action: manipulation" in css
    # system fonts only: the Studio's stacks
    for family in re.findall(r"font(?:-family)?:[^;]*", css):
        assert not re.search(r"url\(|['\"](?!Segoe UI)[A-Z][a-z]+ [A-Z]", family), family


def test_present_owns_the_touch_while_it_runs():
    js = _code(_text("lab.js"))
    css = _code(_text("lab.css"))
    assert re.search(
        r"html\.lab-presenting, html\.lab-presenting body \{ overflow: hidden; overscroll-behavior: none; \}",
        css,
    )
    assert re.search(r"\.lab-present \{[^}]*touch-action: none;", css)
    assert '"touchmove"' in js and "{ passive: false }" in js
    assert "requestFullscreen" in js and '"fullscreenchange"' in js
    assert "M.swipeOf(" in js and "M.presentStep(" in js
    assert 'id: "lab-present-exit"' in js


def test_browser_storage_is_only_touched_inside_try():
    """Private windows and blocked storage throw on access: the Lab reaches localStorage through one guarded helper."""
    js = _code(_text("lab.js"))
    assert js.count("localStorage") == 1
    guarded = re.search(r"function storage\(\) \{\s*try \{\s*return window\.localStorage;", js)
    assert guarded, "localStorage is read outside try/catch"


def test_writes_send_json_even_for_delete():
    """The router's same-site guard wants JSON on every non-GET, DELETE included (PLAN §4)."""
    js = _code(_text("lab.js"))
    assert re.search(r'method: "DELETE", body: \{\}', js)
    for method in re.findall(r'method: "(\w+)"', js):
        assert method in {"POST", "DELETE"}


@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node isn't installed; lab-model.mjs is UNVERIFIED here"
)
def test_the_page_model_under_node():
    node = shutil.which("node")
    result = subprocess.run(
        [node, "--test", str(ROOT / "tests" / "lab-model.test.mjs")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert re.search(r"(?m)^ℹ fail 0$", result.stdout), result.stdout[-2000:]
