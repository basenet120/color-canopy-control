"""/kiosk in a real browser (make e2e): the Hubspace-style guest panel on the iPad stand (docs/design/guest-panel.md).

Two groups:
- **Design, against a stubbed panel API** (always runs): /api/kiosk/panel and the scene/brightness/power writes are
  answered in the browser by a small stub (Playwright routing; nothing in the app changes), so the page's states can
  be driven and photographed: ready, changing, cooldown, a refusal, power off, paused, resting, a lost connection and
  the solid-colour fallback. The plain stub (`canopy: null`, as before the first look) draws the 30-light
  serpentine; the canopy stub (`canopy=True`) serves
  the real canopy geometry and a `state.lit`, and the page draws its 270 lights (full and partial canopy, a tap's
  pulse, power off, a turned view). Screenshots: docs/screenshots/kiosk-*.png at the three iPad sizes.
- **Against the real engine** (skips until GET /api/kiosk/panel serves a non-resting canopy): tap a scene, the state
  goes changing then steady, the feature line shows the server's sentence and the 270 dots show the server's picture.
"""

from __future__ import annotations

import json
import time
import urllib.error

import pytest

from conductor.canopy_view import canopy_geometry, load_canopy
from conductor.config import CONFIG_DIR, PreviewSettings
from conductor.guest_panel import scene_json
from conductor.scenes import load_scenes

LANDSCAPE = {"width": 1180, "height": 820}
OLDER = {"width": 1024, "height": 768}
PORTRAIT = {"width": 820, "height": 1180}
COOLDOWN_S = 8.0
RINGS = [["r1c1"], ["r0c1", "r1c0", "r1c2", "r2c1"], ["r0c0", "r0c2", "r2c0", "r2c2"]]  # centre out
FEATURE = "Every light set individually as a Hubspace custom pattern on both strands."
SOLID_FEATURE = "Each Hubspace strand set to one colour from the scene."


def _wait(predicate, timeout=20.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


# ---- a stub panel for the design group --------------------------------------------------------------------------
class StubPanel:
    """Answers /api/kiosk/panel and the three writes like the guest panel backend would, on wall time."""

    def __init__(
        self,
        scene: str | None = "lowcountry-calm",
        settle_s: float = 1.2,
        *,
        canopy: bool = False,
        driven: set[str] | None = None,
        view: PreviewSettings | None = None,
        ripple_s: float | None = None,
        steps: dict | None = None,
    ) -> None:
        self.scenes = [scene_json(s) for s in load_scenes().scenes]
        # the canopy target: the real geometry, and a picture per position (`driven`: a partial canopy)
        self.canopy = canopy_geometry(load_canopy(str(CONFIG_DIR)), view) if canopy else None
        self.driven = driven
        self.ripple_s = ripple_s  # a scene lands ring by ring from the centre, this far apart (lit.motion)
        self.ripple_t0: float | None = None
        # a stepped run in progress (lit.motion "steps", D75): {frames, levels?, step_s}; it starts `lead_s` after the
        # page's first request, so frame 0 is on screen before it switches
        self.steps = steps
        self.steps_t0: float | None = None
        self.scene = scene
        self.power: bool | None = True
        self.brightness = 70
        self.look: str | None = "gradient"
        self.phase = "ready"
        self.message = ""
        self.cooldown_until = 0.0
        self.settle_at: float | None = None
        self.settle_s = settle_s
        self.writes: list[tuple[str, dict]] = []
        self.refuse: tuple[int, dict] | None = None  # answer every scene write with this

    def state(self) -> dict:
        now = time.time()
        if self.settle_at is not None and now >= self.settle_at:
            self.settle_at = None
            self.phase = "ready"
        phase = self.phase
        if phase == "ready" and now < self.cooldown_until:
            phase = "cooldown"
        changing = phase == "changing"
        feature = None
        if not changing and self.power is not False and self.look:
            feature = FEATURE if self.look == "gradient" else SOLID_FEATURE
        return {
            "power": self.power,
            "brightness": self.brightness,
            "scene": self.scene,
            "look": None if changing else self.look,
            "phase": phase,
            "message": "Changing the light…" if changing else self.message,
            "feature": feature,
            "cooldown_remaining_s": round(max(0.0, self.cooldown_until - now), 3),
            "lit": self.lit(changing),
        }

    def lit(self, changing: bool) -> dict | None:
        """The canopy's picture, as conductor/canopy_view.py serves it: each head the scene's palette colour across
        the canopy's width (a stand-in design), a driven position lit or changing, the rest dark; all off when off."""
        if self.canopy is None or self.scene is None:
            return None
        palette = next(s for s in self.scenes if s["id"] == self.scene)["palette"]
        modules = []
        for m in self.canopy["modules"]:
            item = {"pos": m["pos"], "state": "off", "color": None, "colors": None}
            if self.power is False:
                pass
            elif self.driven is not None and m["pos"] not in self.driven:
                item["state"] = "dark"
            else:
                item["state"] = "changing" if changing else "lit"
                item["colors"] = [
                    palette[min(len(palette) - 1, int(x / self.canopy["width_in"] * len(palette)))]["hex"]
                    for x, _y in m["heads"]
                ]
            modules.append(item)
        motion = None
        if self.ripple_t0 is not None and time.time() < self.ripple_t0 + 2 * self.ripple_s:
            motion = {"kind": "ripple", "t0": self.ripple_t0, "stagger_s": self.ripple_s, "rings": RINGS}
        if self.steps is not None and self.power is not False:
            if self.steps_t0 is None:
                self.steps_t0 = time.time() + float(self.steps.get("lead_s", 3.0))
            step_s, frames = float(self.steps["step_s"]), self.steps["frames"]
            motion = {
                "kind": "steps",
                "t0": self.steps_t0,
                "step_s": step_s,
                "frames": frames,
                "ends_at": self.steps_t0 + len(frames) * step_s,
            }
            if "levels" in self.steps:
                motion["levels"] = self.steps["levels"]
        return {
            "level": None if self.power is False else self.brightness,
            "modules": modules,
            "motion": motion,
        }

    def panel(self) -> dict:
        return {
            "target": "canopy",  # the only target (D67); the serpentine case is `canopy: null`
            "target_label": "The Color Canopy",
            "simulated": True,
            "viewer": "ops",
            "scenes": self.scenes,
            "capabilities": {"scene": True, "brightness": True, "power": True},
            "brightness_range": [10, 100],
            "canopy": self.canopy,
            "state": self.state(),
            "server_now": time.time(),
        }

    def _change(self) -> None:
        self.phase = "changing"
        self.settle_at = time.time() + self.settle_s

    def write(self, kind: str, body: dict) -> tuple[int, dict]:
        self.writes.append((kind, body))
        now = time.time()
        if self.phase == "paused":
            return 423, {"detail": "Guest choices are paused for a moment."}
        if kind == "scene":
            if self.refuse is not None:
                return self.refuse
            if now < self.cooldown_until:
                left = self.cooldown_until - now
                return 429, {"detail": "You can choose a new scene in a moment.", "retry_after_s": left}
            self.scene, self.power = body["scene"], True
            self.cooldown_until = now + COOLDOWN_S
            if self.ripple_s:
                self.ripple_t0 = now
        elif kind == "brightness":
            self.brightness = max(10, int(body["level"]))
        elif kind == "power":
            self.power = bool(body["on"])
        self._change()
        return 200, {"ok": True, "state": self.state(), "server_now": now}

    def install(self, page) -> None:
        def on_panel(route):
            route.fulfill(status=200, content_type="application/json", body=json.dumps(self.panel()))

        def writer(kind):
            def on_write(route):
                status, payload = self.write(kind, json.loads(route.request.post_data or "{}"))
                route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

            return on_write

        page.route("**/api/kiosk/panel", on_panel)
        for kind in ("scene", "brightness", "power"):
            page.route(f"**/api/kiosk/{kind}", writer(kind))


def _open(context_factory, viewport, stub: StubPanel | None = None, path: str = "/kiosk/"):
    ctx = context_factory(viewport, has_touch=True)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if stub is not None:
        stub.install(page)
    page.goto(path)
    return ctx, page, errors


def _ready(page):
    page.wait_for_selector("body[data-view='panel']", state="attached")
    page.wait_for_selector(".scene[data-scene='golden-hour']")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(700)  # the header tint's crossfade


def _no_overflow(page, viewport):
    overflow = page.evaluate(
        "[document.documentElement.scrollWidth - innerWidth, document.documentElement.scrollHeight - innerHeight,"
        " (() => { const b = document.querySelector('#sheet-body'); return b.scrollHeight - b.clientHeight; })()]"
    )
    assert overflow[0] <= 0 and overflow[1] <= 0, overflow
    assert overflow[2] <= 1, f"the sheet scrolls at {viewport}"


# ---- design group --------------------------------------------------------------------------------------------
def test_panel_ready_changing_cooldown(design_context_factory, screenshots):
    stub = StubPanel(scene="lowcountry-calm")
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    assert page.locator(".scene").count() == 6
    assert page.text_content("h1") == "The Color Canopy"
    assert page.get_attribute(".scene[data-scene='lowcountry-calm']", "data-state") == "selected"
    assert page.text_content("#feature-text") == FEATURE
    assert page.text_content("#now-name") == "Lowcountry Calm"
    assert page.eval_on_selector_all("#snake rect", "els => els.length") == 30
    for card in page.locator(".scene").all():  # big targets for fingers at arm's length
        box = card.bounding_box()
        assert box["height"] >= 64 and box["width"] >= 64
    switch = page.locator("#power").bounding_box()
    assert switch["height"] >= 72 and switch["width"] >= 96
    assert page.locator("#slider").bounding_box()["height"] >= 72
    _no_overflow(page, LANDSCAPE)
    page.screenshot(path=str(screenshots / "kiosk-1180x820.png"))

    # tap: the card goes pending at once, the server's "changing", then the cooldown chip
    page.tap(".scene[data-scene='coastal-tides']")
    page.wait_for_function(
        "document.querySelector(\"[data-scene='coastal-tides']\").dataset.state === 'pending'"
    )
    assert stub.writes == [("scene", {"scene": "coastal-tides"})]
    assert page.locator("#hdr-spinner").is_visible()
    page.screenshot(path=str(screenshots / "kiosk-changing.png"))
    page.wait_for_function(
        "document.querySelector(\"[data-scene='coastal-tides']\").dataset.state === 'selected'"
    )
    page.wait_for_selector("#chip:not([hidden])")
    assert page.text_content("#now-name") == "Coastal Tides"
    assert page.get_attribute(".scene[data-scene='golden-hour']", "aria-disabled") == "true"

    # a tap on a disabled card sends nothing and explains, never silently
    page.tap(".scene[data-scene='golden-hour']")
    page.wait_for_selector("#toast:not([hidden])")
    assert "new scene in" in page.text_content("#toast-text")
    assert len(stub.writes) == 1
    page.screenshot(path=str(screenshots / "kiosk-cooldown.png"))
    assert errors == []


def test_panel_refusal_shows_the_calm_detail(design_context_factory):
    stub = StubPanel(scene="lowcountry-calm")
    stub.refuse = (409, {"detail": "Someone is using the module page right now. Try again in a moment."})
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    page.tap(".scene[data-scene='golden-hour']")
    page.wait_for_selector("#toast:not([hidden])")
    assert page.text_content("#toast-text") == stub.refuse[1]["detail"]
    assert "409" not in page.inner_text("body")
    page.wait_for_timeout(300)
    assert page.get_attribute(".scene[data-scene='lowcountry-calm']", "data-state") == "selected"
    assert page.get_attribute(".scene[data-scene='golden-hour']", "data-state") == "idle"
    assert errors == []


def test_panel_slider_sends_the_final_value_once_on_release(design_context_factory):
    """D69: every guest input shares the engine's cooldown, so a drag sends one level, the final one, on release."""
    stub = StubPanel()
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    box = page.locator("#slider").bounding_box()
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + 40, y)
    page.mouse.down()
    for i in range(20):  # ~600 ms of dragging
        page.mouse.move(box["x"] + 40 + i * (box["width"] - 80) / 20, y)
        page.wait_for_timeout(30)
    page.mouse.up()
    page.wait_for_timeout(300)
    levels = [b["level"] for kind, b in stub.writes if kind == "brightness"]
    assert len(levels) == 1 and levels[0] >= 90, levels  # nothing while dragging; the final value on release
    assert page.text_content("#bright-value") == str(levels[-1])
    assert errors == []


def test_panel_power_off_and_solid_look(design_context_factory, screenshots):
    stub = StubPanel(scene="golden-hour")
    stub.look = "solid"
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    assert page.text_content("#feature-text") == SOLID_FEATURE
    fills = page.eval_on_selector_all("#snake rect", "els => els.map((e) => e.getAttribute('fill'))")
    assert len(set(fills)) == 1, "the solid fallback is drawn as the one colour lit"

    page.tap("#power")
    page.wait_for_function("document.querySelector('#power').getAttribute('aria-checked') === 'false'")
    assert stub.writes[-1] == ("power", {"on": False})
    page.wait_for_function("document.querySelector('#now-name').textContent === 'Lights are off'")
    fills = page.eval_on_selector_all("#snake rect", "els => els.map((e) => e.getAttribute('fill'))")
    assert set(fills) == {"rgb(211, 210, 216)"}
    page.wait_for_timeout(1500)
    page.screenshot(path=str(screenshots / "kiosk-power-off.png"))
    assert errors == []


@pytest.mark.parametrize(
    ("viewport", "name"), [(OLDER, "kiosk-1024x768.png"), (PORTRAIT, "kiosk-820x1180.png")]
)
def test_panel_sizes(design_context_factory, screenshots, viewport, name):
    stub = StubPanel(scene="rainbow-row-sunset")
    _ctx, page, errors = _open(design_context_factory, viewport, stub)
    _ready(page)
    _no_overflow(page, viewport)
    for card in page.locator(".scene").all():
        box = card.bounding_box()
        assert box["height"] >= 64 and box["width"] >= 64
        assert box["y"] + box["height"] <= viewport["height"]
    page.screenshot(path=str(screenshots / name))
    assert errors == []


# ---- the canopy's picture (web/kiosk/canopy-preview.mjs) ---------------------------------------------------------
UNLIT = "#D3D2D8"


def _dots(page) -> list[dict]:
    return page.eval_on_selector_all(
        "#canopy-preview circle.dot",
        "els => els.map((e) => ({fill: e.getAttribute('fill'), cls: e.getAttribute('class'),"
        " opacity: e.getAttribute('fill-opacity')}))",
    )


def _picture(stub: StubPanel) -> list[str]:
    """The fill of every dot as the page should draw it, in the page's own order (geometry modules × heads)."""
    lit = {m["pos"]: m for m in stub.state()["lit"]["modules"]}
    fills = []
    for m in stub.canopy["modules"]:
        item = lit[m["pos"]]
        for k in range(len(m["heads"])):
            if item["state"] in ("off", "dark"):
                fills.append(UNLIT)
            else:
                fills.append(item["colors"][k] if item["colors"] else item["color"])
    return fills


@pytest.mark.parametrize(
    ("viewport", "name"),
    [
        (LANDSCAPE, "kiosk-canopy-1180x820.png"),
        (OLDER, "kiosk-canopy-1024x768.png"),
        (PORTRAIT, "kiosk-canopy-820x1180.png"),
    ],
)
def test_panel_draws_the_canopy_of_270_lights(design_context_factory, screenshots, viewport, name):
    stub = StubPanel(scene="golden-hour", canopy=True)
    _ctx, page, errors = _open(design_context_factory, viewport, stub)
    _ready(page)
    assert page.is_visible("#canopy-preview") and not page.is_visible("#snake")
    assert page.eval_on_selector_all("#canopy-preview rect.panel", "els => els.length") == 9
    dots = _dots(page)
    assert len(dots) == 270
    assert [d["fill"] for d in dots] == _picture(stub)  # each head its own colour, as the server says
    assert len({d["fill"] for d in dots}) > 1
    assert {d["opacity"] for d in dots} == {"0.805"}  # level 70 over the 0.35 floor
    assert page.get_attribute("#canopy-preview", "aria-label") == "Preview: 9 of 9 panels lit"
    _no_overflow(page, viewport)
    page.screenshot(path=str(screenshots / name))
    assert errors == []


def test_panel_canopy_pulses_while_changing_then_settles(design_context_factory, screenshots):
    stub = StubPanel(scene="lowcountry-calm", canopy=True, settle_s=2.5)
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    page.tap(".scene[data-scene='coastal-tides']")
    page.wait_for_function("document.querySelectorAll('#canopy-preview circle.changing').length === 270")
    assert page.text_content("#hdr-sub-text") == "Changing the light…"
    page.screenshot(path=str(screenshots / "kiosk-canopy-changing.png"))
    page.wait_for_function(
        "document.querySelectorAll('#canopy-preview circle.changing').length === 0", timeout=10000
    )
    assert [d["fill"] for d in _dots(page)] == _picture(stub)
    assert errors == []


def test_panel_canopy_ripple_lands_ring_by_ring(design_context_factory):
    """lit.motion "ripple": the page keeps each ring's old colours until t0 + ring × stagger (server time), then
    switches it, and the animation stops once the last ring has landed."""
    stub = StubPanel(scene="coastal-tides", canopy=True, settle_s=4.0, ripple_s=1.5)
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    before = _dots(page)
    page.tap(".scene[data-scene='golden-hour']")
    page.wait_for_function("document.querySelectorAll('#canopy-preview circle.changing').length > 0")
    order = [m["pos"] for m in stub.canopy["modules"] for _ in m["heads"]]
    after = _picture(stub)

    def fills_of(pos: str, fills: list) -> list:
        return [f for p, f in zip(order, fills, strict=True) if p == pos]

    now = [d["fill"] for d in _dots(page)]
    assert fills_of("r1c1", now) == fills_of("r1c1", after)  # the centre lands at once
    old = [d["fill"] for d in before]
    assert fills_of("r0c0", now) == fills_of("r0c0", old)  # a corner still shows the old look
    page.wait_for_function(  # the corners land at t0 + 3 s
        f"(() => {{ const d = [...document.querySelectorAll('#canopy-preview circle.dot')]; "
        f"return d[0].getAttribute('fill') === {json.dumps(after[0])}; }})()",
        timeout=6000,
    )
    assert [d["fill"] for d in _dots(page)] == after
    assert errors == []


def test_panel_canopy_partial_and_off(design_context_factory, screenshots):
    stub = StubPanel(scene="golden-hour", canopy=True, driven={"r0c0", "r0c1"})
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    fills = [d["fill"] for d in _dots(page)]
    assert fills == _picture(stub)
    assert fills.count(UNLIT) == 7 * 30  # left out: unlit, never a colour
    assert page.get_attribute("#canopy-preview", "aria-label") == "Preview: 2 of 9 panels lit"
    page.screenshot(path=str(screenshots / "kiosk-canopy-partial.png"))

    page.tap("#power")
    page.wait_for_function("document.querySelector('#now-name').textContent === 'Lights are off'")
    page.wait_for_function(
        f"[...document.querySelectorAll('#canopy-preview circle.dot')].every((e) => e.getAttribute('fill') === "
        f"{json.dumps(UNLIT)})"
    )
    assert page.get_attribute("#canopy-preview", "aria-label") == "Preview: the canopy is off"
    assert errors == []


def test_a_breathing_run_draws_each_frames_level_as_opacity(design_context_factory):
    """D75: a breath's frames carry each panel's level; the page draws it as that panel's dots' opacity over the dim
    floor (70 → 0.805, 80 → 0.87), colours at full strength, and no layout work between frames (D69)."""
    frame = ["#79CED4"] * 9
    stub = StubPanel(
        scene="coastal-tides",
        canopy=True,
        steps={"frames": [frame, list(frame)], "levels": [[70] * 9, [80] * 9], "step_s": 1.5, "lead_s": 3.0},
    )
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    boxes = "els => els.map((e) => [e.getAttribute('cx'), e.getAttribute('cy'), e.getAttribute('r')])"
    first = _dots(page)
    first_boxes = page.eval_on_selector_all("#canopy-preview circle.dot", boxes)
    assert len(first) == 270
    assert {d["opacity"] for d in first} == {"0.805"} and {d["fill"] for d in first} == {"#79CED4"}
    page.wait_for_function(
        "[...document.querySelectorAll('#canopy-preview circle.dot')]"
        ".every((e) => e.getAttribute('fill-opacity') === '0.87')",
        timeout=10000,
    )
    second = _dots(page)
    assert {d["opacity"] for d in second} == {"0.87"} and {d["fill"] for d in second} == {"#79CED4"}
    assert page.eval_on_selector_all("#canopy-preview circle.dot", boxes) == first_boxes
    assert errors == []


@pytest.mark.parametrize("viewport", [LANDSCAPE, OLDER, PORTRAIT])
def test_panel_canopy_turned_view_fits(design_context_factory, viewport):
    """A quarter-turned view (kiosk.preview.rotate_deg 90, set at install) letterboxes into the card at every size."""
    stub = StubPanel(scene="golden-hour", canopy=True, view=PreviewSettings(rotate_deg=90, mirror_x=True))
    _ctx, page, errors = _open(design_context_factory, viewport, stub)
    _ready(page)
    assert page.get_attribute("#canopy-preview", "viewBox") == "0 0 120 225"
    assert len(_dots(page)) == 270
    _no_overflow(page, viewport)
    assert errors == []


def test_panel_paused_and_resting(design_context_factory, screenshots):
    stub = StubPanel(scene="goodnight-glow")
    stub.phase = "paused"
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    assert page.locator("#paused").is_visible()
    assert page.get_attribute("#power", "aria-disabled") == "true"
    page.screenshot(path=str(screenshots / "kiosk-paused.png"))

    stub.phase = "resting"
    stub.message = "The canopy is resting. It will be back shortly."
    page.wait_for_selector("#resting:not([hidden])", timeout=5000)
    assert page.text_content("#resting-text") == stub.message
    page.screenshot(path=str(screenshots / "kiosk-resting.png"))
    assert errors == []


def test_panel_reconnects_calmly(design_context_factory):
    stub = StubPanel()
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    page.unroute("**/api/kiosk/panel")
    page.route("**/api/**", lambda route: route.abort())
    page.wait_for_selector("#toast:not([hidden])", timeout=15000)
    assert page.text_content("#toast-text") == "Hang on, we're reconnecting."
    assert page.url.endswith("/kiosk/")
    page.unroute("**/api/**")
    stub.install(page)
    page.wait_for_selector("#toast", state="hidden", timeout=10000)
    assert errors == []


def test_panel_rests_when_the_engine_is_missing(design_context_factory):
    """No stub: without the engine the real panel API reports "resting", and the page rests calmly."""
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE)
    page.wait_for_selector("#resting:not([hidden])", state="attached", timeout=10000)
    assert "503" not in page.inner_text("body")
    assert errors == []


def test_debug_drawer_is_for_ops_only(design_context_factory, screenshots):
    stub = StubPanel()
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub, path="/kiosk/?debug=1")
    _ready(page)
    page.tap(".dbg-btn")
    page.wait_for_selector(".dbg")
    assert "canopy" in page.inner_text(".dbg")
    page.screenshot(path=str(screenshots / "kiosk-debug.png"))

    stub_kiosk = StubPanel()
    _ctx2, page2, errors2 = _open(design_context_factory, LANDSCAPE, stub_kiosk, path="/kiosk/?debug=1")
    page2.route(
        "**/api/kiosk/panel",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({**stub_kiosk.panel(), "viewer": "kiosk"}),
        ),
    )
    page2.reload()
    _ready(page2)
    assert page2.locator(".dbg-btn").count() == 0  # a kiosk device gets the plain panel
    assert errors == [] and errors2 == []


# ---- against the real engine ------------------------------------------------------------------------------------
def _panel_live(api) -> bool:
    try:
        return api("/api/kiosk/panel")["state"]["phase"] != "resting"
    except urllib.error.HTTPError:
        return False


def test_panel_scene_reaches_the_simulator(context_factory, api, screenshots):
    if not _panel_live(api):
        pytest.skip("The canopy engine isn't serving the guest panel yet (GET /api/kiosk/panel is resting).")
    _wait(lambda: api("/api/kiosk/panel")["state"]["phase"] == "ready", timeout=60)
    before = api("/api/kiosk/panel")
    ids = [s["id"] for s in before["scenes"]]
    target = ids[1] if before["state"]["scene"] != ids[1] else ids[2]

    _ctx, page, errors = _open(context_factory, LANDSCAPE)
    _ready(page)
    with page.expect_response("**/api/kiosk/scene") as response:
        page.tap(f".scene[data-scene='{target}']")
    assert response.value.status == 200
    _wait(lambda: api("/api/kiosk/panel")["state"]["phase"] in ("ready", "cooldown"), timeout=60)
    after = api("/api/kiosk/panel")["state"]
    assert after["scene"] == target and after["feature"]
    page.wait_for_function(
        f"document.querySelector('#feature-text').textContent === {json.dumps(after['feature'])}",
        timeout=10000,
    )
    # the 270 dots are the server's picture of what the engine installed
    body = api("/api/kiosk/panel")
    lit = {m["pos"]: m for m in body["state"]["lit"]["modules"]}
    unlit = {"off": UNLIT, "dark": UNLIT, "unknown": "none"}  # unknown: an outline, no colour claim
    want = [
        unlit.get(lit[m["pos"]]["state"])
        or (lit[m["pos"]]["colors"] or [lit[m["pos"]]["color"]] * len(m["heads"]))[k]
        for m in body["canopy"]["modules"]
        for k in range(len(m["heads"]))
    ]
    page.wait_for_function(
        f"JSON.stringify([...document.querySelectorAll('#canopy-preview circle.dot')].map((e) => "
        f"e.getAttribute('fill'))) === {json.dumps(json.dumps(want, separators=(',', ':')))}",
        timeout=10000,
    )
    page.screenshot(path=str(screenshots / "kiosk-live-1180x820.png"))
    assert errors == []
