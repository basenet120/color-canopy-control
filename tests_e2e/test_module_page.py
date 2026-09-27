"""/ops/module in a real browser on the simulator: paint → Apply → readback, the index sweep, lock release on
exit, and screenshots at phone and tablet sizes (docs/screenshots/module-*.png)."""

from __future__ import annotations

import re
import time

import pytest

PHONE = {"width": 390, "height": 844}
TABLET = {"width": 1024, "height": 768}

BENCH = "bench-kit"
RED = {"r": 255, "g": 0, "b": 0}
PICKED = (0, 7, 22)


def _wait(predicate, timeout=15.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def _device(api, device=BENCH):
    return next(d for d in api("/api/module")["devices"] if d["id"] == device)


@pytest.fixture(autouse=True)
def release_bench_kit(api):
    """Every test starts and ends with the bench kit free (ops take over + close if a test left it held)."""

    def release():
        if _device(api)["locked"]:
            lease = api(f"/api/module/{BENCH}/open", "POST", {"steal": True})["lease"]
            api(f"/api/module/{BENCH}/close", "POST", {"lease": lease})

    release()
    yield
    release()


def _open_page(context_factory, viewport=PHONE):
    ctx = context_factory(viewport)
    page = ctx.new_page()
    page.goto("/ops/module/")
    page.wait_for_selector("#workspace:not([hidden])")
    page.wait_for_selector("#map g[data-index='29']")
    page.wait_for_function("document.querySelector('#status-pill').textContent === 'Ready'")
    return page


def _core_fill(page, index):
    return page.eval_on_selector(f"#map g[data-index='{index}'] circle.core", "c => c.getAttribute('fill')")


def test_paint_three_heads_apply_and_readback(context_factory, api, screenshots):
    page = _open_page(context_factory)
    assert page.locator(".sim-badge").inner_text().strip().lower() == "simulator"
    assert _device(api)["locked"] is True
    for i in PICKED:
        page.click(f"#map g[data-index='{i}']")
    assert page.inner_text("#selection-count") == "3 selected"
    page.fill("#brush-color", "#ff0000")
    page.click("#paint-selected")
    assert page.locator("#map circle.ring.draft").count() == 3  # drawn as a draft until applied
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(screenshots / "module-phone-draft.png"))
    page.click("#apply-btn")
    page.wait_for_selector("#toast:not([hidden])")
    assert re.search(r"Applied: paint → custom-3 .* SIMULATED", page.inner_text("#toast"))
    # the reported colours (the centre of each head) turn red once readback has them
    for i in PICKED:
        page.wait_for_function(
            f"document.querySelector(\"#map g[data-index='{i}'] circle.core\").getAttribute('fill')"
            " === 'rgb(255, 0, 0)'"
        )
    assert _core_fill(page, 1) == "rgb(0, 0, 0)"
    state = api(f"/api/module/{BENCH}")["state"]
    assert all(state["heads"][i]["reported"] == RED for i in PICKED) and state["diff"] == []
    # the simulator is the real kit's shape: a 60-entry frame, and the map shows its 30 pucks (entries 0–29)
    assert state["head_count"]["reported"] == 60 and state["head_count"]["problem"] is None
    assert len(state["heads"]) == 30 and page.locator("#map g[data-index]").count() == 30
    slot3 = next(s for s in state["slots"] if s["slot"] == "custom-3")
    assert slot3["length"] == 60 and [i for i, c in enumerate(slot3["heads"]) if c == RED] == list(PICKED)
    assert "60-entry pattern, pucks use 0–29" in page.inner_text("#reported-look")
    assert "SIMULATED" in page.inner_text("#tele-last")
    page.click("#map g[data-index='7']")  # clear the selection outline for a clean picture
    page.click("[data-select='none']")
    page.wait_for_function("document.querySelector('#status-pill').textContent === 'Ready'")
    page.screenshot(path=str(screenshots / "module-phone.png"))


def test_index_sweep_advances(context_factory, api, screenshots):
    page = _open_page(context_factory)
    page.click(".tabs [data-tab='cards']")
    page.fill("#sweep-step", "1")
    page.click("#sweep-start")
    page.wait_for_function("/head [2-9] lit/.test(document.querySelector('#sweep-status').textContent)")
    state = api(f"/api/module/{BENCH}")["state"]
    assert state["sweep"]["state"] == "running" and state["sweep"]["index"] >= 1
    lit = [h["index"] for h in state["heads"] if h["reported"] != {"r": 0, "g": 0, "b": 0}]
    assert len(lit) == 1
    page.locator("[data-card='index-sweep']").scroll_into_view_if_needed()
    page.screenshot(path=str(screenshots / "module-phone-tests.png"))
    page.click("#sweep-pause")
    page.wait_for_function("document.querySelector('#sweep-status').textContent.startsWith('Paused')")
    page.click("#sweep-stop")
    page.wait_for_function("document.querySelector('#sweep-status').textContent.startsWith('Stopped')")


def test_lock_is_released_when_the_page_goes_away(context_factory, api):
    page = _open_page(context_factory)
    assert _device(api)["locked"] is True
    page.goto("about:blank")  # navigating away fires pagehide → sendBeacon(close)
    _wait(lambda: not _device(api)["locked"])
    page = _open_page(context_factory)
    assert _device(api)["locked"] is True
    page.close(run_before_unload=True)  # closing the tab
    _wait(lambda: not _device(api)["locked"])


def test_hold_overlay_and_tablet_layout(context_factory, api, screenshots):
    page = _open_page(context_factory, TABLET)
    page.click(".tabs [data-tab='cards']")
    page.click("[data-card='color'] .color-row:first-of-type button:has-text('Painted')")
    page.wait_for_selector("#toast:not([hidden])")
    page.wait_for_function("document.querySelector('#status-pill').textContent === 'Ready'")
    page.screenshot(path=str(screenshots / "module-tablet.png"))
    page.click("#hold-btn")
    page.fill("#hold-input", "HD orange · frosted Mylar")
    page.click("#hold-form button[value='hold']")
    page.wait_for_selector("#hold-overlay:not([hidden])")
    assert page.inner_text("#hold-name") == "HD orange · frosted Mylar"
    assert api(f"/api/module/{BENCH}")["state"]["hold"]["on"] is True
    page.close(run_before_unload=True)

    phone = _open_page(context_factory)
    phone.click("#hold-btn")
    phone.click("#hold-form button[value='hold']")
    phone.wait_for_selector("#hold-overlay:not([hidden])")
    phone.screenshot(path=str(screenshots / "module-phone-hold.png"))
    phone.click("#hold-release")
    phone.wait_for_selector("#hold-overlay", state="hidden")


def test_a_second_page_sees_who_holds_it_and_ops_can_take_over(context_factory, api, screenshots):
    first = _open_page(context_factory)
    ctx = context_factory(PHONE)
    second = ctx.new_page()
    second.goto("/ops/module/")
    second.wait_for_selector("#banner:not([hidden])")
    assert "in use by module page · ops" in second.inner_text("#banner")
    second.screenshot(path=str(screenshots / "module-phone-in-use.png"))
    second.click("#banner button:has-text('Take over')")
    second.wait_for_selector("#workspace:not([hidden])")
    # the first page learns it lost the module (live update or heartbeat) and stops sending
    first.wait_for_selector("#banner:not([hidden])")
    assert "Taken over" in first.inner_text("#banner") or "no longer" in first.inner_text("#banner")
    assert first.inner_text("#status-pill") == "Not holding"


def test_lowcountry_preview_card_paints_a_canopy_slice(context_factory, api, screenshots):
    page = _open_page(context_factory)
    page.click(".tabs [data-tab='cards']")
    page.select_option("#lc-mode", "rainbow-row-sunset")
    page.click("[data-card='lowcountry-preview'] [data-position='r0c1']")
    page.click("#lc-apply")
    page.wait_for_function("/painted per head/.test(document.querySelector('.lc-result').textContent)")
    assert "Kiosk copy" in page.inner_text(".lc-result")
    state = api(f"/api/module/{BENCH}")["state"]
    assert "Rainbow Row Sunset" in state["look_name"]
    assert all(h["reported"] != {"r": 0, "g": 0, "b": 0} for h in state["heads"])
    page.locator("[data-card='lowcountry-preview']").scroll_into_view_if_needed()
    page.screenshot(path=str(screenshots / "module-phone-lowcountry.png"))


def test_calibrate_tab_sets_the_frame_offset(context_factory, api, screenshots):
    """The Calibrate tab says how long the saved pattern is and which entries the pucks use, and saves the offset
    to the overlay (nothing is sent to the lights)."""
    page = _open_page(context_factory)
    page.click(".tabs [data-tab='calibrate']")
    assert page.input_value("#cal-offset") == "0"
    explain = page.inner_text("#offset-explain")
    assert "hold 60 entries" in explain and "use entries 0–29" in explain
    assert "lights nothing, or lights the wrong half" in page.inner_text("#offset-field")
    page.locator("#offset-field").scroll_into_view_if_needed()
    page.screenshot(path=str(screenshots / "module-phone-calibrate.png"))
    page.fill("#cal-offset", "30")
    page.click("#cal-offset-save")
    page.wait_for_function("/use entries 30–59/.test(document.querySelector('#offset-explain').textContent)")
    assert api(f"/api/module/{BENCH}")["state"]["head_count"]["offset"] == 30
    page.fill("#cal-offset", "0")
    page.click("#cal-offset-save")
    page.wait_for_function("/use entries 0–29/.test(document.querySelector('#offset-explain').textContent)")
    assert api(f"/api/module/{BENCH}")["state"]["head_count"]["offset"] == 0
