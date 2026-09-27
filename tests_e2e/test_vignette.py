"""The vignette tablets (P4) in a real browser, on the simulator: every room renders at iPad size with an honest
per-device status, a colour change reaches the SimDriver and reads back Ready, and the lock takes two taps.

Screenshots (reviewed by the lead): docs/screenshots/vignette-<room>-ipad.png at 1024 × 768.
Runs against the Conductor without the canopy engine (vignettes don't use it)."""

from __future__ import annotations

import json
import urllib.request

import pytest
from conftest import AUTH_HEADER, TABLET

ROOMS = ("entry", "living", "bedroom", "garden")


def _get(base: str, path: str) -> dict:
    request = urllib.request.Request(f"{base}{path}", headers=AUTH_HEADER)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        return json.loads(response.read())


def _open(design_context_factory, room: str):
    ctx = design_context_factory(TABLET, has_touch=True)
    page = ctx.new_page()
    page.goto(f"/vignette/{room}/")
    page.wait_for_selector("body[data-view='room']")
    page.wait_for_selector("#live[data-state='open']", timeout=15000)
    return page


@pytest.mark.parametrize("room", ROOMS)
def test_each_room_renders_at_ipad_size(design_context_factory, design_server, screenshots, room):
    page = _open(design_context_factory, room)
    data = _get(design_server, f"/api/vignette/{room}")
    assert page.locator("#title").inner_text() == data["title"]
    # every device's card reads Ready from the tracker (the page shows the look each device already has)
    page.wait_for_function("document.querySelectorAll('.pill.updating').length === 0", timeout=15000)
    assert page.locator(".pill.attention").count() == 0
    assert page.locator(".pill.ready").count() >= 1
    assert page.locator(".sim-badge").is_visible()  # a persistent Simulator badge on the simulator
    assert page.locator("a").count() == 0  # nothing to navigate away with (Guided Access)
    # nothing spills sideways at iPad size
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(screenshots / f"vignette-{room}-ipad.png"))


def test_a_colour_change_reaches_the_simulator_and_reads_back_ready(design_context_factory, design_server):
    page = _open(design_context_factory, "living")
    card = page.locator(".card", has=page.locator("h2", has_text="All lamps"))
    card.locator(".swatch[title='Holiday red']").click()
    card.locator(".pill.updating, .pill.ready").first.wait_for()
    page.wait_for_function(
        "document.querySelectorAll('.card .pill.ready').length >= 2 && "
        "document.querySelectorAll('.pill.updating').length === 0",
        timeout=20000,
    )
    assert card.locator(".swatch[title='Holiday red']").get_attribute("aria-pressed") == "true"
    data = _get(design_server, "/api/vignette/living")
    lamps = [d for d in data["devices"] if d["kind"] == "bulb"]
    assert all(d["now"]["mode"] == "color" and d["now"]["hex"] == "#E0182D" for d in lamps)
    assert all(d["status"] == "ready" for d in lamps)


def test_the_lock_needs_two_taps(design_context_factory, design_server):
    page = _open(design_context_factory, "entry")
    before = _get(design_server, "/api/vignette/entry")["devices"][0]["now"]["position"]
    verb = "Lock" if before == "unlocked" else "Unlock"
    page.get_by_role("button", name=f"{verb} the door").click()
    confirm = page.get_by_role("button", name=f"Confirm: {verb.lower()}")
    assert confirm.is_visible()
    # nothing was sent on the first tap
    assert _get(design_server, "/api/vignette/entry")["devices"][0]["now"]["position"] == before
    confirm.click()
    expected = "Locked" if verb == "Lock" else "Unlocked"
    page.locator(".lock-position", has_text=expected).wait_for(timeout=20000)
    page.wait_for_function("document.querySelector('.pill.ready') !== null", timeout=20000)
