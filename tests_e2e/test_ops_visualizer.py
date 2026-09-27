"""/ops, /ops/calibrate/ and /visualizer in a real browser on the simulator, with a canopy engine (the real one when
the app registers it, else tests/fake_engine.FakeEngine via tests_e2e/ops_server.py).

Screenshots: docs/screenshots/ops-phone*.png (390×844), ops-tablet.png (1024×768), ops-calibrate-phone.png,
visualizer-desktop*.png (1280×800).
"""

from __future__ import annotations

import json
import time
import urllib.request

import pytest
from conftest import AUTH_HEADER

PHONE = {"width": 390, "height": 844}
TABLET = {"width": 1024, "height": 768}
DESKTOP = {"width": 1280, "height": 800}
HELD = "canopy-r1c1"  # Module 5, held on the module page to show a mismatch


@pytest.fixture
def call(ops_server):
    def _call(path: str, method: str = "GET", body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{ops_server}{path}",
            data=data,
            method=method,
            headers={**AUTH_HEADER, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read() or b"null")

    return _call


@pytest.fixture
def page_at(browser, ops_server):
    contexts = []

    def make(viewport: dict):
        ctx = browser.new_context(
            base_url=ops_server,
            viewport=viewport,
            extra_http_headers=AUTH_HEADER,
            device_scale_factor=1,
            has_touch=viewport["width"] < 800,
        )
        contexts.append(ctx)
        return ctx.new_page()

    yield make
    for ctx in contexts:
        ctx.close()


def _wait_mode(call, mode: str, phase: str = "ready", timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = call("/api/canopy/state")
        if state["mode"] == mode and state["phase"] == phase:
            return state
        time.sleep(0.2)
    raise AssertionError(f"canopy never reached {phase} on {mode}")


@pytest.fixture
def held_module(call):
    """Golden Hour on the canopy, with Module 5 held on the module page showing blue (desired ≠ reported)."""
    call("/api/canopy/mode", "POST", {"mode": "golden-hour"})
    _wait_mode(call, "golden-hour")
    lease = call(f"/api/module/{HELD}/open", "POST", {"steal": True})["lease"]
    call(
        f"/api/module/{HELD}/whole",
        "POST",
        {"lease": lease, "look": {"kind": "rgb", "rgb": [60, 120, 255], "brightness": 90}},
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        heads = call("/api/canopy/heads")
        module = next(m for m in heads["modules"] if m["device_id"] == HELD)
        if module["counts"]["mismatch"] == 30:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("the held module's readback never showed the blue look")
    yield lease
    call(f"/api/module/{HELD}/close", "POST", {"lease": lease})


def _open_ops(page):
    page.goto("/ops/")
    page.wait_for_selector("#canopy-grid .cell >> nth=8")
    page.wait_for_selector("#checklist li >> nth=7")
    page.wait_for_selector("#logs li.log")
    page.wait_for_function("document.querySelector('#live').dataset.state === 'open'")


def test_ops_phone_controls_checklist_and_test_mode_change(page_at, call, screenshots):
    call("/api/canopy/mode", "POST", {"mode": "rainbow-row-sunset"})
    _wait_mode(call, "rainbow-row-sunset")
    page = page_at(PHONE)
    _open_ops(page)
    assert page.locator(".sim-badge").inner_text().strip().lower() == "simulator"
    assert page.inner_text("#canopy-mode") == "Rainbow Row Sunset"
    assert "Hubspace strand" in page.inner_text("#canopy-feature")
    assert page.locator("#canopy-grid .cell.s-ready").count() == 9
    # every checklist row shows a status, and every non-pass row a fix
    for row in page.locator("#checklist li").all():
        tag = row.locator(".tag").text_content()
        assert tag in ("Pass", "Check", "Fail")
        if tag != "Pass":
            assert row.locator(".fix").count() == 1

    # the 7 am test mode change: one ops change, all nine timed to Ready, then back to Sunset
    page.click("#test-mode-btn")
    page.wait_for_function(
        "document.querySelector('#test-result').textContent.includes('reached Ready')", timeout=20000
    )
    assert "All 9 modules reached Ready" in page.inner_text("#test-result")
    page.wait_for_function(
        "document.querySelector(\"#checklist li[data-item='test_mode_change'] .tag\").textContent === 'Pass'"
    )

    # pause and resume guest input
    page.click("#pause-btn")
    page.wait_for_function("document.querySelector('#pause-btn').textContent === 'Resume guest input'")
    assert page.inner_text("#canopy-guest") == "Guest input paused"
    assert call("/api/canopy/state")["guest_input"] is False
    page.click("#pause-btn")
    page.wait_for_function("document.querySelector('#pause-btn').textContent === 'Pause guest input'")
    assert call("/api/canopy/state")["guest_input"] is True

    # commission asks first; Cancel sends nothing
    page.click("#commission-btn")
    page.wait_for_selector("#commission-dialog[open]")
    before = page.inner_text("#control-result")
    page.click("#commission-dialog button[value='cancel']")
    page.wait_for_timeout(300)
    assert page.inner_text("#control-result") == before  # nothing was sent
    page.click("#commission-btn")
    page.click("#commission-dialog button[value='go']")
    page.wait_for_function("document.querySelector('#control-result').textContent !== " + json.dumps(before))

    # the test changed Sunset → Lowcountry Calm (the idle mode), and stays there: that's where doors open
    call("/api/canopy/mode", "POST", {"mode": "rainbow-row-sunset"})
    _wait_mode(call, "rainbow-row-sunset")
    page.reload()
    _open_ops(page)
    page.wait_for_function("document.querySelectorAll('#canopy-grid .cell.s-ready').length === 9")
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(screenshots / "ops-phone.png"))
    page.screenshot(path=str(screenshots / "ops-phone-full.png"), full_page=True)


def test_ops_mode_picker_and_enrollment(page_at, call):
    page = page_at(PHONE)
    _open_ops(page)
    page.select_option("#mode-select", "goodnight-glow")
    page.click("#mode-btn")
    page.wait_for_function("document.querySelector('#canopy-mode').textContent === 'The Goodnight Glow'")
    _wait_mode(call, "goodnight-glow")
    # a kiosk link: no grants; the Copy row appears
    page.check("#enroll-role input[value='kiosk']")
    page.fill("#enroll-name", "Canopy kiosk")
    page.click("#enroll-form button[type='submit']")
    page.wait_for_selector("#enroll-link:not([hidden])")
    assert "/enroll/" in page.input_value("#enroll-url")
    page.wait_for_function("document.querySelector('#enrollments').textContent.includes('Canopy kiosk')")
    # a vignette link needs ids
    page.check("#enroll-role input[value='vignette']")
    assert page.locator("#enroll-vignette").is_visible() and page.locator("#enroll-devices").is_hidden()
    page.fill("#enroll-name", "Living room iPad")
    page.fill("#enroll-vignettes", "living-room")
    page.click("#enroll-form button[type='submit']")
    page.wait_for_function("document.querySelector('#enrollments').textContent.includes('Living room iPad')")


def test_ops_tablet_shows_a_held_module(page_at, held_module, screenshots):
    page = page_at(TABLET)
    _open_ops(page)
    cell = page.locator("#canopy-grid .cell[data-module='r1c1']")
    page.wait_for_function(
        "document.querySelector(\"#canopy-grid .cell[data-module='r1c1']\").classList.contains('s-needs_attention')"
    )
    assert "Held" in cell.inner_text()
    assert page.locator("#canopy-grid .cell[data-module='r1c1'] circle.mismatch").count() == 30
    assert "Module 5" in page.inner_text("#module-issues")
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(screenshots / "ops-tablet.png"), full_page=True)


def test_calibration_hub_links_into_the_module_page(page_at, screenshots):
    page = page_at(PHONE)
    page.goto("/ops/calibrate/")
    page.wait_for_selector(".cal-card >> nth=8")
    assert page.locator(".sim-badge").count() == 1
    link = page.locator(".cal-card[data-device='canopy-r0c1'] a.btn")
    assert link.get_attribute("href") == "/ops/module/?device=canopy-r0c1&tab=calibrate"
    page.screenshot(path=str(screenshots / "ops-calibrate-phone.png"), full_page=True)
    link.click()
    page.wait_for_selector("#workspace:not([hidden])")
    page.wait_for_function(
        "document.querySelector(\".tabs [data-tab='calibrate']\").getAttribute('aria-selected') === 'true'"
    )
    assert page.locator("#tab-calibrate").is_visible()
    page.goto("about:blank")  # pagehide releases the module lock


def test_visualizer_desired_vs_reported(page_at, call, held_module, screenshots):
    page = page_at(DESKTOP)
    page.goto("/visualizer/")
    page.wait_for_function("document.querySelector('#stats').textContent.includes('match')")
    stats = page.inner_text("#stats")
    assert "270 heads" in stats and "30 differ" in stats and "240 match" in stats
    assert page.locator("h1").inner_text() == "The Color Canopy"
    assert page.locator("#schemes button").count() == 2
    assert "Golden Hour" in page.inner_text("#now")
    assert "SIMULATED" in page.inner_text("#readback-note")
    assert page.locator(".sim-badge").count() == 1
    page.wait_for_function("document.querySelector('#live').dataset.state === 'open'")
    page.screenshot(path=str(screenshots / "visualizer-desktop.png"), full_page=True)
    page.click("button[data-view='reported']")
    assert page.get_attribute("button[data-view='reported']", "aria-pressed") == "true"
    assert page.locator("#legend-ring").is_hidden()
    page.click("#wire")
    page.screenshot(path=str(screenshots / "visualizer-desktop-reported-wire.png"), full_page=True)
    # live: a mode change reaches the page over the "canopy" topic, without a reload
    page.click("button[data-view='both']")
    call("/api/canopy/mode", "POST", {"mode": "lowcountry-calm"})
    page.wait_for_function(
        "document.querySelector('#now').textContent.includes('Lowcountry Calm')", timeout=8000
    )


def test_visualizer_still_draws_the_layout_without_colours(page_at):
    page = page_at(DESKTOP)
    page.route(
        "**/api/canopy/heads", lambda route: route.fulfill(status=503, body='{"detail": "engine down"}')
    )
    page.goto("/visualizer/")
    page.wait_for_function("document.querySelector('#stats').textContent.includes('270')")
    assert page.locator("#schemes button").count() == 2
    assert page.locator("button[data-view='both']").is_disabled()
    assert "engine down" in page.inner_text("#engine-note")


# ---- review fixes: honest controls (F3), alerts on /ops (F5), devices grouped by role / room ----------------------
def _json_route(status: int, payload: dict):
    return lambda route: route.fulfill(
        status=status, content_type="application/json", body=json.dumps(payload)
    )


def test_ops_refusals_show_in_the_error_style(page_at):
    """Review F3: the engine's refusal (a 409) or a commissioning run with problems (ok:false) is never green."""
    page = page_at(PHONE)
    _open_ops(page)
    page.route(
        "**/api/canopy/safe-look",
        _json_route(409, {"detail": "The safe look can't be compiled: no strand technique (test)."}),
    )
    page.click("#safe-btn")
    page.wait_for_function(
        "document.querySelector('#control-result').textContent.includes(\"can't be compiled\")"
    )
    assert page.get_attribute("#control-result", "class") == "error"
    page.route(
        "**/api/canopy/commission",
        _json_route(
            200, {"ok": False, "message": "Wrote 7 slot(s); 2 need attention.", "result": {}, "state": None}
        ),
    )
    page.click("#commission-btn")
    page.click("#commission-dialog button[value='go']")
    page.wait_for_function(
        "document.querySelector('#control-result').textContent.includes('2 need attention')"
    )
    assert page.get_attribute("#control-result", "class") == "error"
    # a real, accepted change is green and says what went where
    page.unroute("**/api/canopy/safe-look")
    page.click("#safe-btn")
    page.wait_for_function(
        "document.querySelector('#control-result').textContent.startsWith('Safe look sent')"
    )
    assert page.get_attribute("#control-result", "class") == "banner-ok"
    assert "of 9 modules" in page.inner_text("#control-result")


def test_ops_alerts_can_be_acknowledged(page_at, screenshots):
    """Review F5: an engine alert (here injected into the summary in the browser) shows with its module link and an
    Acknowledge button that calls the ack API."""
    page = page_at(PHONE)
    acked: list[dict] = []
    alert = {
        "message": "Module 3: the hand-painted pattern in custom-1 changed (hash 1a2b3c4d, expected 5e6f7a8b). "
        "Repaint it in the Hubspace app, or record the new hash on /ops.",
        "link": "/ops/module/?device=canopy-r0c2",
        "alert": "slot:canopy-r0c2:custom-1",
        "level": "warn",
        "at": time.time(),
    }

    def summary(route):
        body = route.fetch().json()
        if not acked:
            body["attention"] = [*body["attention"], alert]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def ack(route):
        acked.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))

    page.route("**/api/ops/summary", summary)
    page.route("**/api/ops/alerts/ack", ack)
    _open_ops(page)
    page.wait_for_selector("#attention li.alert")
    item = page.locator("#attention li.alert")
    assert "custom-1 changed" in item.inner_text()
    assert item.locator("a").get_attribute("href") == "/ops/module/?device=canopy-r0c2"
    page.locator("#attention-card").screenshot(path=str(screenshots / "ops-attention-phone.png"))
    item.locator("button.ack").click()
    page.wait_for_selector("#attention li.alert", state="detached")
    assert acked == [{"key": "slot:canopy-r0c2:custom-1"}]


def test_ops_devices_grouped_by_role_and_room(page_at, screenshots):
    page = page_at(TABLET)
    _open_ops(page)
    page.wait_for_selector("#devices section[data-group='vignette-garden'] li")
    groups = page.eval_on_selector_all("#devices section.dev-group", "els => els.map((e) => e.dataset.group)")
    assert groups == [
        "canopy",
        "bench",
        "spares",
        "vignette-entry",
        "vignette-living",
        "vignette-bedroom",
        "vignette-garden",
    ]
    for key in ("canopy", "bench", "spares"):
        for row in page.locator(f"#devices section[data-group='{key}'] li").all():
            assert row.locator("a", has_text="Open module page").count() == 1
            assert row.locator("a", has_text="Bench").count() == 1
            assert "30 heads" in row.inner_text()
    assert page.locator("#devices section[data-group='canopy'] li").count() == 9
    for key in ("vignette-entry", "vignette-living", "vignette-bedroom", "vignette-garden"):
        room = key.split("-", 1)[1]
        section = page.locator(f"#devices section[data-group='{key}']")
        assert section.locator("a", has_text="Open module page").count() == 0
        assert section.locator("a", has_text="Bench").count() == 0
        assert "heads" not in section.inner_text()
        for link in section.locator("li a", has_text="Open room page").all():
            assert link.get_attribute("href") == f"/vignette/{room}/"
    assert "Holiday Living Room" in page.inner_text("#devices section[data-group='vignette-living'] h3")
    page.locator("#devices-card").screenshot(path=str(screenshots / "ops-devices-tablet.png"))
