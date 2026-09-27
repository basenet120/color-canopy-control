"""/kiosk in a real browser (make e2e): the guest canopy touchscreen.

Two groups:
- **Design, against a stubbed canopy** (always runs): /api/kiosk/state and /api/kiosk/select are answered in the
  browser by a small stub engine (Playwright routing — nothing in the app changes), so the page's states can be
  driven and photographed without P1: ready, updating + the cooldown ring, resting, and a lost connection.
  Screenshots: docs/screenshots/kiosk-{1280x800,1920x1080,1024x768,updating,resting}.png.
- **Against the real engine** (skips until the engine is wired, i.e. GET /api/kiosk/state is 200): tap a mode →
  the state goes updating → ready, every canopy module's tracker reports the new look (the SimDriver's readback
  matches the desired rows), the cooldown shows, and screenshots kiosk-live-*.png.
"""

from __future__ import annotations

import json
import time
import urllib.error
from pathlib import Path

import pytest

KIOSK = {"width": 1280, "height": 800}
HD = {"width": 1920, "height": 1080}
TABLET = {"width": 1024, "height": 768}
COOLDOWN_S = 8.0
ROOT = Path(__file__).resolve().parent.parent


def _wait(predicate, timeout=20.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def _modes() -> dict:
    import yaml

    return yaml.safe_load((ROOT / "config" / "modes.yaml").read_text())["modes"]


# ---- a stub canopy for the design group ----------------------------------------------------------------------
class StubCanopy:
    """Answers /api/kiosk/state and /select in the browser like the engine would, on wall time."""

    def __init__(self, mode: str = "lowcountry-calm", settle_s: float = 1.2) -> None:
        self.modes = _modes()
        self.mode = mode
        self.phase = "ready"
        self.guest_input = True
        self.cooldown_until: float | None = None
        self.settle_s = settle_s
        self.settle_at: float | None = None
        self.selects: list[str] = []

    def feature(self) -> str:
        spec = self.modes[self.mode]
        first = spec["ladder"][0]
        return spec["techniques"][first]["feature"]

    def state(self) -> dict:
        now = time.time()
        if self.settle_at is not None and now >= self.settle_at and self.phase == "updating":
            self.phase = "ready"
        remaining = max(0.0, (self.cooldown_until or 0) - now)
        return {
            "mode": self.mode,
            "label": self.modes[self.mode]["label"],
            "tier": self.modes[self.mode]["ladder"][0],
            "feature": self.feature(),
            "phase": self.phase,
            "guest_input": self.guest_input,
            "cooldown_until": self.cooldown_until,
            "changed_at": now,
            "modules": {},
            "simulated": True,
            "message": "",
            "server_now": now,
            "cooldown_remaining_s": round(remaining, 3),
        }

    def select(self, mode: str) -> tuple[int, dict]:
        self.selects.append(mode)
        now = time.time()
        if not self.guest_input:
            return 423, {"detail": "The canopy is resting for a moment. It will be back shortly."}
        if self.cooldown_until and now < self.cooldown_until:
            return 429, {"detail": "The light is still changing.", "retry_after_s": self.cooldown_until - now}
        self.mode = mode
        self.phase = "updating"
        self.settle_at = now + self.settle_s
        self.cooldown_until = now + COOLDOWN_S
        return 200, self.state()

    def install(self, page) -> None:
        def on_state(route):
            route.fulfill(status=200, content_type="application/json", body=json.dumps(self.state()))

        def on_select(route):
            body = json.loads(route.request.post_data or "{}")
            status, payload = self.select(body.get("mode", ""))
            route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

        page.route("**/api/kiosk/state", on_state)
        page.route("**/api/kiosk/select", on_select)


# Test instrumentation only: remember the page's WebSockets so a test can drop them like a restarting Conductor.
TRACK_SOCKETS = """
(() => {
  const Real = window.WebSocket;
  window.__sockets = [];
  window.WebSocket = function (...args) {
    const ws = new Real(...args);
    window.__sockets.push(ws);
    return ws;
  };
  window.WebSocket.prototype = Real.prototype;
  Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
})();
"""


def _open(context_factory, viewport, stub: StubCanopy | None = None):
    ctx = context_factory(viewport, has_touch=True)
    ctx.add_init_script(TRACK_SOCKETS)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if stub is not None:
        stub.install(page)
    page.goto("/kiosk/")
    page.wait_for_selector(".tile[data-mode='golden-hour']")
    return ctx, page, errors


def _settle_fonts(page):
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(900)  # the canopy's colour transition


# ---- design group --------------------------------------------------------------------------------------------
def test_kiosk_design_ready_updating_cooldown(context_factory, screenshots):
    stub = StubCanopy(mode="lowcountry-calm")
    _ctx, page, errors = _open(context_factory, KIOSK, stub)
    page.wait_for_selector("body[data-view='live']")
    assert page.locator(".tile").count() == 6
    assert page.eval_on_selector_all("#canopy-svg .core circle", "els => els.length") == 270
    assert page.text_content("#status-text") == "Ready"
    assert page.text_content("#feature-text") == stub.feature()
    assert page.text_content("h1") == "The Color Canopy"
    assert page.locator(".sim-badge").is_visible()
    # big targets
    box = page.locator(".tile").first.bounding_box()
    assert box["height"] >= 80 and box["width"] >= 80
    _settle_fonts(page)
    page.screenshot(path=str(screenshots / "kiosk-calm-1280x800.png"))

    # tap: instant preview, then the server's updating, the cooldown ring, then ready
    page.tap(".tile[data-mode='rainbow-row-sunset']")
    page.wait_for_function("document.querySelector('#now-label').textContent === 'Rainbow Row Sunset'")
    page.wait_for_function("document.querySelector('#status-text').textContent === 'Changing the light…'")
    assert stub.selects == ["rainbow-row-sunset"]
    assert page.evaluate("document.body.classList.contains('cooling')")
    assert "rainbow-row-sunset" in page.get_attribute(".tile.current", "data-mode")
    page.wait_for_timeout(300)
    page.screenshot(path=str(screenshots / "kiosk-updating.png"))

    # a tap during the cooldown doesn't send anything and says so gently
    page.tap(".tile[data-mode='golden-hour']")
    page.wait_for_function("document.querySelector('#hint').classList.contains('shown')")
    assert stub.selects == ["rainbow-row-sunset"]

    # the WS carries nothing here (no engine), so the page's poll picks up "ready" (≤ 30 s); nudge it instead
    page.evaluate("window.dispatchEvent(new Event('online'))")
    stub.cooldown_until = time.time()  # let the cooldown end for the photograph
    page.reload()
    page.wait_for_selector("body[data-view='live']")
    page.wait_for_function("document.querySelector('#status-text').textContent === 'Ready'")
    page.wait_for_function("!document.body.classList.contains('cooling')")
    _settle_fonts(page)
    page.screenshot(path=str(screenshots / "kiosk-1280x800.png"))
    assert errors == []


@pytest.mark.parametrize(
    ("viewport", "mode", "name"),
    [(HD, "golden-hour", "kiosk-1920x1080.png"), (TABLET, "garden-gathering", "kiosk-1024x768.png")],
)
def test_kiosk_design_sizes(context_factory, screenshots, viewport, mode, name):
    stub = StubCanopy(mode=mode)
    _ctx, page, errors = _open(context_factory, viewport, stub)
    page.wait_for_selector("body[data-view='live']")
    page.wait_for_function("document.querySelector('#status-text').textContent === 'Ready'")
    # nothing overflows the screen: the kiosk never scrolls
    overflow = page.evaluate(
        "[document.documentElement.scrollWidth - innerWidth, document.documentElement.scrollHeight - innerHeight]"
    )
    assert overflow[0] <= 0 and overflow[1] <= 0
    for tile in page.locator(".tile").all():
        box = tile.bounding_box()
        assert box["height"] >= 80 and box["width"] >= 80
        assert box["y"] + box["height"] <= viewport["height"]
    _settle_fonts(page)
    page.screenshot(path=str(screenshots / name))
    assert errors == []


def test_kiosk_resting_when_paused_and_when_disconnected(context_factory, screenshots):
    stub = StubCanopy(mode="goodnight-glow")
    stub.guest_input = False
    stub.phase = "resting"
    _ctx, page, errors = _open(context_factory, KIOSK, stub)
    page.wait_for_selector("body[data-view='resting']", state="attached")
    assert page.locator("#resting").is_visible()
    assert page.text_content("#resting-text") == "The canopy is resting. It will be back shortly."
    _settle_fonts(page)
    page.screenshot(path=str(screenshots / "kiosk-resting.png"))

    # back: the kiosk follows the state
    stub.guest_input = True
    stub.phase = "ready"
    page.reload()
    page.wait_for_selector("body[data-view='live']")

    # the Conductor goes away: after a short grace, the resting screen (never a browser error page)
    page.wait_for_function("window.__sockets.some((ws) => ws.readyState === 1)")
    page.unroute("**/api/kiosk/state")
    page.route("**/api/**", lambda route: route.abort())
    page.evaluate("window.__sockets.forEach((ws) => ws.close())")
    page.wait_for_selector("body[data-view='resting']", state="attached", timeout=20000)
    assert page.locator("#resting").is_visible()
    assert page.url.endswith("/kiosk/")
    # and back again once the Conductor answers
    page.unroute("**/api/**")
    stub.install(page)
    page.wait_for_selector("body[data-view='live']", state="attached", timeout=40000)
    assert errors == []


def test_kiosk_resting_when_engine_missing(context_factory):
    """No stub: without the engine the real API answers 503, and the page rests calmly."""
    _ctx, page, errors = _open(context_factory, KIOSK)
    page.wait_for_selector("body[data-view='resting']", state="attached")
    assert page.locator("#resting").is_visible()
    assert "503" not in page.inner_text("body")
    assert errors == []


# ---- against the real engine ------------------------------------------------------------------------------------
def _engine_present(api) -> bool:
    try:
        api("/api/kiosk/state")
    except urllib.error.HTTPError:
        return False
    return True


def test_kiosk_selection_reaches_the_simulator(context_factory, api, screenshots):
    if not _engine_present(api):
        pytest.skip(
            "The canopy engine isn't wired yet (GET /api/kiosk/state is 503); the lead runs this after P1."
        )
    _wait(lambda: api("/api/kiosk/state")["cooldown_remaining_s"] == 0, timeout=15)
    before = api("/api/kiosk/state")
    modules = sorted(before["modules"])
    assert len(modules) == 9
    desired_before = {m: api(f"/api/devices/{m}")["tracker"]["desired"] for m in modules}
    target = "rainbow-row-sunset" if before["mode"] != "rainbow-row-sunset" else "golden-hour"
    label = _modes()[target]["label"]

    _ctx, page, errors = _open(context_factory, KIOSK)
    page.wait_for_selector("body[data-view='live']")
    with page.expect_response("**/api/kiosk/select") as response:
        page.tap(f".tile[data-mode='{target}']")
    selected = response.value.json()
    assert response.value.status == 200
    assert selected["mode"] == target and selected["phase"] == "updating"
    page.wait_for_function(f"document.querySelector('#now-label').textContent === {json.dumps(label)}")
    assert page.evaluate("document.body.classList.contains('cooling')")

    # ready, honestly: the engine says ready and every module's readback matches what it sent
    _wait(lambda: api("/api/kiosk/state")["phase"] == "ready", timeout=60)
    page.wait_for_function("document.querySelector('#status-text').textContent === 'Ready'", timeout=40000)
    after = api("/api/kiosk/state")
    assert after["mode"] == target and after["feature"]
    assert page.text_content("#feature-text") == after["feature"]
    for m in modules:
        tracker = api(f"/api/devices/{m}")["tracker"]
        assert tracker["status"] == "ready", (m, tracker["reason"])
        assert tracker["mismatches"] == [] and tracker["reported"]
        assert tracker["desired"] != desired_before[m], m
    _settle_fonts(page)
    page.screenshot(path=str(screenshots / "kiosk-live-1280x800.png"))

    _ctx2, page2, errors2 = _open(context_factory, HD)
    page2.wait_for_selector("body[data-view='live']")
    _settle_fonts(page2)
    page2.screenshot(path=str(screenshots / "kiosk-live-1920x1080.png"))
    assert errors == [] and errors2 == []
