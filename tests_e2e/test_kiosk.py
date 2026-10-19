"""/kiosk in a real browser (make e2e): the Hubspace-style guest panel on the iPad stand (docs/design/guest-panel.md).

Two groups:
- **Design, against a stubbed panel API** (always runs): /api/kiosk/panel and the scene/brightness/power writes are
  answered in the browser by a small stub (Playwright routing; nothing in the app changes), so the page's states can
  be driven and photographed: ready, changing, cooldown, a refusal, power off, paused, resting, a lost connection and
  the solid-colour fallback. The plain stub (`canopy: null`, as before the first look) draws the 30-light
  serpentine; the canopy stub (`canopy=True`) serves
  the real canopy geometry and a `state.lit`, and the page draws its 270 lights (full and partial canopy, a tap's
  pulse, power off, a turned view). Screenshots: docs/screenshots/kiosk-*.png at the three iPad sizes.
  The review stub (`review=True`, D76) serves the 18-scene review fixture (tests/review_fixture.py: fixture data only,
  07 §9) with the server's own menu rule (`ipad_menu.menu`, `hide` = kiosk.hidden_scenes) and thumbnails
  (`ipad_menu.catalog_thumbs`): grouped rows, the scrolling scene list under a pinned side, painted card pictures, a
  whole-strand card's one colour (`ipad_menu.plan_thumb`) beside a card with none (the strip) and the six-option
  grid. Its screenshots (kiosk-review-*.png, kiosk-six-options-1180x820.png) are left untracked: PR D
  takes them from the real catalog.
- **Against the real engine** (skips until GET /api/kiosk/panel serves a non-resting canopy): tap a scene, the state
  goes changing then steady, the feature line shows the server's sentence and the 270 dots show the server's picture.
"""

from __future__ import annotations

import json
import time
import urllib.error

import pytest

from conductor import ipad_menu
from conductor.canopy_view import canopy_geometry, load_canopy
from conductor.config import CONFIG_DIR, PreviewSettings, load_registry, load_settings
from conductor.engine_api import ModePlan, ModuleTarget, Tier
from conductor.guest_panel import scene_json
from conductor.looks.live import git_trio
from conductor.scenes import load_scenes
from tests.review_fixture import review_scenes, review_trio

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
UNKNOWN_SCENE = "That scene isn't one of the panel's choices."
PER_LIGHT = ("canvas", "slot")


class StubPanel:
    """Answers /api/kiosk/panel and the three writes like the guest panel backend would, on wall time.

    `review=True` serves the 18-scene review fixture; `hide` is kiosk.hidden_scenes. Which scenes are visible comes
    from the server's own rule (`ipad_menu.menu`, idle scene included), so the stub can't drift from it. Thumbnails
    are sent only while the review menu shows, on the basis the sim engine gives at T1: per light for a canvas- or
    slot-first scene, else its nine module colours."""

    def __init__(
        self,
        scene: str | None = "lowcountry-calm",
        settle_s: float = 1.2,
        *,
        canopy: bool = False,
        driven: set[str] | None = None,
        view: PreviewSettings | None = None,
        ripple_s: float | None = None,
        review: bool = False,
        hide: frozenset[str] = frozenset(),
    ) -> None:
        catalog = review_scenes(load_scenes()) if review else load_scenes()
        kiosk = load_settings().kiosk.model_copy(update={"hidden_scenes": sorted(hide)})
        self.menu = ipad_menu.menu(catalog, kiosk)
        thumbs: dict[str, dict | None] = {}
        if self.menu.review:
            trio = review_trio(git_trio(CONFIG_DIR)) if review else git_trio(CONFIG_DIR)
            basis = {
                s.id: "heads" if str(trio.modes.modes[s.id].ladder[0]) in PER_LIGHT else "modules"
                for s in self.menu.visible.scenes
            }
            thumbs = ipad_menu.catalog_thumbs(trio, load_canopy(str(CONFIG_DIR)), CONFIG_DIR, basis)
        visible = {s.id for s in self.menu.visible.scenes}
        # every scene (state.scene and Now Showing use the whole catalog), and the cards (the visible ones; the
        # same dicts, so an edit to one shows in both)
        self.all_scenes = [scene_json(s, thumbs.get(s.id)) for s in catalog.scenes]
        self.scenes = [s for s in self.all_scenes if s["id"] in visible]
        # the canopy target: the real geometry, and a picture per position (`driven`: a partial canopy)
        self.canopy = canopy_geometry(load_canopy(str(CONFIG_DIR)), view) if canopy else None
        self.driven = driven
        self.ripple_s = ripple_s  # a scene lands ring by ring from the centre, this far apart (lit.motion)
        self.ripple_t0: float | None = None
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
        palette = next(s for s in self.all_scenes if s["id"] == self.scene)["palette"]
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
        return {
            "level": None if self.power is False else self.brightness,
            "modules": modules,
            "motion": motion,
        }

    def panel(self) -> dict:
        visible = {s["id"] for s in self.scenes}
        showing = next(
            (s for s in self.all_scenes if s["id"] == self.scene and s["id"] not in visible), None
        )  # a hidden scene that is showing: Now Showing still names it
        return {
            "target": "canopy",  # the only target (D67); the serpentine case is `canopy: null`
            "target_label": "The Color Canopy",
            "simulated": True,
            "viewer": "ops",
            "scenes": self.scenes,
            "showing": showing,
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
            if body.get("scene") not in {s["id"] for s in self.scenes}:  # unknown, or hidden on the iPad
                return 404, {"detail": UNKNOWN_SCENE}
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


def _open(context_factory, viewport, stub: StubPanel | None = None, path: str = "/kiosk/", **kw):
    ctx = context_factory(viewport, has_touch=True, **kw)
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if stub is not None:
        stub.install(page)
    page.goto(path)
    return ctx, page, errors


def _ready(page, scene: str = "golden-hour"):
    page.wait_for_selector("body[data-view='panel']", state="attached")
    page.wait_for_selector(f".scene[data-scene='{scene}']")
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


# ---- the review menu (D76): more than six scenes visible --------------------------------------------------------
REVIEW_SIZES = [
    (LANDSCAPE, "kiosk-review-1180x820.png"),
    (OLDER, "kiosk-review-1024x768.png"),
    (PORTRAIT, "kiosk-review-820x1180.png"),
]
BASES = [
    "lowcountry-calm",
    "rainbow-row-sunset",
    "garden-gathering",
    "coastal-tides",
    "golden-hour",
    "goodnight-glow",
]


def _cards(page) -> list[dict]:
    return page.eval_on_selector_all(
        "#grid .scene",
        "els => els.map((c) => { const r = c.getBoundingClientRect(); return {id: c.dataset.scene, x: r.x, y: r.y,"
        " w: r.width, h: r.height, bottom: r.bottom, rowStart: c.classList.contains('row-start')}; })",
    )


def _box(page, sel: str) -> dict:
    return page.evaluate(f"document.querySelector({json.dumps(sel)}).getBoundingClientRect().toJSON()")


def _doc_scroll(page) -> list[float]:
    return page.evaluate(
        "[document.documentElement.scrollWidth - innerWidth, document.documentElement.scrollHeight - innerHeight]"
    )


def _label(scene: dict) -> str:  # = panel-model.mjs sceneLabel
    return f"{scene['name']}. {scene['mood']}. Colors: {', '.join(p['name'] for p in scene['palette'])}."


@pytest.mark.parametrize(("viewport", "name"), REVIEW_SIZES)
def test_review_menu_groups_each_scenes_options_in_one_row(
    design_context_factory, screenshots, viewport, name
):
    stub = StubPanel(scene="golden-hour", review=True, canopy=True)
    _ctx, page, errors = _open(design_context_factory, viewport, stub)
    _ready(page)
    assert page.get_attribute("#app", "data-review") == "true"
    cards = _cards(page)
    assert [c["id"] for c in cards] == [s["id"] for s in stub.scenes] and len(cards) == 18
    row = page.evaluate(
        "parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--review-row'))"
    )
    for i in range(6):
        group = cards[3 * i : 3 * i + 3]
        assert max(c["y"] for c in group) - min(c["y"] for c in group) <= 1, group  # one row per scene
        assert group[0]["x"] < group[1]["x"] < group[2]["x"], group
        assert abs(group[0]["x"] - cards[0]["x"]) <= 1 and group[0]["rowStart"], (
            group
        )  # each starts at the left
    for c in cards:
        assert c["w"] >= 64 and c["h"] >= 64, c
        assert c["h"] <= row + 1, (c, row)  # two-line names fit the row instead of growing it
    page.screenshot(path=str(screenshots / name))
    assert errors == []


@pytest.mark.parametrize("viewport", [LANDSCAPE, PORTRAIT])
def test_review_menu_scrolls_the_scene_list_under_a_pinned_side(
    design_context_factory, screenshots, viewport
):
    stub = StubPanel(scene="golden-hour", review=True, canopy=True)
    _ctx, page, errors = _open(design_context_factory, viewport, stub)
    _ready(page)
    doc = _doc_scroll(page)
    assert doc[0] <= 0 and doc[1] <= 0, doc  # the page itself never scrolls
    sheet = page.evaluate(
        "(() => { const b = document.querySelector('#sheet-body'); return b.scrollHeight - b.clientHeight; })()"
    )
    assert sheet <= 1
    grid = page.evaluate(
        "(() => { const g = document.querySelector('#grid'); return [g.scrollHeight, g.clientHeight,"
        " g.scrollWidth - g.clientWidth]; })()"
    )
    assert grid[0] > grid[1] and grid[2] <= 1, grid  # only the scene list scrolls, and only up and down
    side = (_box(page, "#now-name"), _box(page, "#slider"))

    # a finger drag on a card scrolls the list and chooses nothing (the card cancels the press)
    first = _box(page, "#grid .scene")
    cdp = page.context.new_cdp_session(page)
    cdp.send(
        "Input.synthesizeScrollGesture",
        {
            "x": first["x"] + first["width"] / 2,
            "y": first["y"] + first["height"] / 2,
            "yDistance": -400,
            "gestureSourceType": "touch",
        },
    )
    page.wait_for_timeout(300)
    assert page.evaluate("document.querySelector('#grid').scrollTop") > 0
    assert not [w for w in stub.writes if w[0] == "scene"], stub.writes

    page.evaluate("document.querySelector('#grid').scrollTop = 1e6")
    page.wait_for_timeout(100)
    assert (_box(page, "#now-name"), _box(page, "#slider")) == side  # Now Showing and brightness stay put
    grid_box = _box(page, "#grid")
    last = _cards(page)[-1]
    assert last["y"] >= grid_box["top"] - 1 and last["bottom"] <= grid_box["bottom"] + 1, (last, grid_box)
    if viewport == LANDSCAPE:
        page.screenshot(path=str(screenshots / "kiosk-review-scrolled-1180x820.png"))
    page.tap('.scene[data-scene="goodnight-glow-3"]')
    page.wait_for_timeout(300)
    assert stub.writes[-1] == ("scene", {"scene": "goodnight-glow-3"})
    assert errors == []


def test_review_scroll_survives_a_rebuild(design_context_factory):
    stub = StubPanel(scene="golden-hour", review=True, canopy=True)
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    page.evaluate("document.querySelector('#grid').scrollTop = 300")
    page.wait_for_timeout(100)
    before = page.evaluate("document.querySelector('#grid').scrollTop")
    assert before > 0
    renamed = "Garden Gathering #2 (b)"
    next(s for s in stub.all_scenes if s["id"] == "garden-gathering-2")["name"] = renamed  # a cardsKey change
    page.wait_for_function(
        "document.querySelector(\".scene[data-scene='garden-gathering-2'] .scene-name\").textContent === "
        + json.dumps(renamed),
        timeout=4000,
    )
    assert abs(page.evaluate("document.querySelector('#grid').scrollTop") - before) <= 1
    assert errors == []


def test_review_rows_start_each_scene_at_the_left(design_context_factory):
    stub = StubPanel(scene="golden-hour", review=True, canopy=True, hide=frozenset({"rainbow-row-sunset-2"}))
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    cards = {c["id"]: c for c in _cards(page)}
    assert len(cards) == 17 and "rainbow-row-sunset-2" not in cards
    assert abs(cards["garden-gathering"]["x"] - cards["lowcountry-calm"]["x"]) <= 1
    assert (
        cards["garden-gathering"]["y"] > cards["rainbow-row-sunset-3"]["y"]
    )  # a short row never pulls the next up
    assert errors == []


def test_review_cards_draw_their_own_design(design_context_factory):
    stub = StubPanel(scene="golden-hour", review=True, canopy=True)
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub, device_scale_factor=2)
    _ready(page)
    page.wait_for_function(
        "[...document.querySelectorAll('#grid canvas.thumb')].every((c) => c.dataset.thumb)", timeout=5000
    )
    by_id = {s["id"]: s for s in stub.scenes}
    cards = page.eval_on_selector_all(
        "#grid .scene",
        "els => els.map((c) => ({id: c.dataset.scene, label: c.getAttribute('aria-label'),"
        " canvases: [...c.querySelectorAll('canvas.thumb')].map((x) => ({hidden: x.getAttribute('aria-hidden'),"
        " sig: x.dataset.thumb})), strips: c.querySelectorAll('.strip').length}))",
    )
    for c in cards:
        scene = by_id[c["id"]]
        assert c["canvases"] == [{"hidden": "true", "sig": scene["thumb"]["sig"]}], c
        assert c["strips"] == 0 and c["label"] == _label(scene), c
    assert by_id["golden-hour"]["thumb"]["basis"] == "heads"  # painted: per light at T1
    assert by_id["golden-hour-2"]["thumb"]["basis"] == "modules"  # an option: its nine module colours
    got = page.evaluate(
        """async (scenes) => {
          const P = await import('/kiosk/canopy-preview.mjs?v=review-1');
          const panel = await (await fetch('/api/kiosk/panel')).json();
          const out = {};
          for (const s of scenes) {
            const canvas = document.querySelector(`.scene[data-scene="${s.id}"] canvas.thumb`);
            const dots = P.layout(panel.canopy, { width: canvas.clientWidth, height: canvas.clientHeight }).dots;
            const ctx = canvas.getContext('2d');
            const picks = [dots[0], dots[100], dots[269]].map((d) => ({
              pos: d.pos, k: d.k,
              rgba: [...ctx.getImageData(Math.round(d.cx * 2), Math.round(d.cy * 2), 1, 1).data],
            }));
            const view = panel.canopy.view || {};
            out[s.id] = { picks, alpha: Math.round(P.levelOpacity(s.brightness_draft, view.dim_floor) * 255) };
          }
          out.urls = ['golden-hour', 'golden-hour-2', 'golden-hour-3'].map(
            (id) => document.querySelector(`.scene[data-scene="${id}"] canvas.thumb`).toDataURL());
          return out;
        }""",
        [by_id["golden-hour"], by_id["golden-hour-2"]],
    )
    for sid in ("golden-hour", "golden-hour-2"):
        modules = {m["pos"]: m for m in by_id[sid]["thumb"]["modules"]}
        for pick in got[sid]["picks"]:
            m = modules[pick["pos"]]
            hex_ = m["colors"][pick["k"]] if m["colors"] else m["color"]
            want = [int(hex_[i : i + 2], 16) for i in (1, 3, 5)]
            assert all(abs(a - b) <= 6 for a, b in zip(pick["rgba"][:3], want, strict=True)), (
                sid,
                pick,
                want,
            )
            assert abs(pick["rgba"][3] - got[sid]["alpha"]) <= 8, (sid, pick, got[sid]["alpha"])
    urls = got["urls"]
    assert len(set(urls)) == 3, "the three designs of one scene draw three different pictures"
    assert errors == []


def test_a_whole_strand_review_card_draws_its_one_colour_and_a_card_with_none_keeps_the_strip(
    design_context_factory,
):
    """C1 at the strand tier (D76): a scene the engine would run as one whole-strand look gets a "strand" thumbnail,
    built by the server's own `ipad_menu.plan_thumb` from that plan (here aqua on the two kits it drives), and its
    card paints it on the canvas like any other card: the one colour on the driven panels, nothing on the others,
    never the palette strip's gradient. A card with no thumbnail (a preset the strand plays on its own makes no colour
    claim) keeps the palette strip."""
    stub = StubPanel(scene="golden-hour", review=True, canopy=True)
    by_id = {s["id"]: s for s in stub.scenes}
    registry = load_registry()
    ids = {e.module: e.id for e in registry.canopy_modules()}
    aqua = {"r": 121, "g": 206, "b": 212}
    targets = {
        ids[p]: ModuleTarget(ids[p], [], [], colors=[dict(aqua) for _ in range(30)]) for p in ("r0c0", "r0c1")
    }
    plan = ModePlan("coastal-tides", "Coastal Tides", Tier.STRAND, "", False, targets)
    strand = ipad_menu.plan_thumb("coastal-tides", load_canopy(str(CONFIG_DIR)), registry, plan)
    assert strand is not None and strand["basis"] == "strand"
    by_id["coastal-tides"]["thumb"] = strand
    by_id["coastal-tides-2"]["thumb"] = None  # e.g. a preset strand look: no thumbnail
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub, device_scale_factor=2)
    _ready(page)
    page.wait_for_function(
        "[...document.querySelectorAll('#grid canvas.thumb')].every((c) => c.dataset.thumb)", timeout=5000
    )
    got = page.evaluate(
        """async (scene) => {
          const P = await import('/kiosk/canopy-preview.mjs?v=review-1');
          const panel = await (await fetch('/api/kiosk/panel')).json();
          const card = document.querySelector(`.scene[data-scene="${scene.id}"]`);
          const other = document.querySelector('.scene[data-scene="coastal-tides-2"]');
          const canvas = card.querySelector('canvas.thumb');
          if (!canvas) return { sig: null, strips: card.querySelectorAll('.strip').length };
          const dots = P.layout(panel.canopy, { width: canvas.clientWidth, height: canvas.clientHeight }).dots;
          const ctx = canvas.getContext('2d');
          const at = (d) => [...ctx.getImageData(Math.round(d.cx * 2), Math.round(d.cy * 2), 1, 1).data];
          const first = (pos) => dots.find((d) => d.pos === pos);
          return {
            sig: canvas.dataset.thumb,
            strips: card.querySelectorAll('.strip').length,
            picks: Object.fromEntries(['r0c0', 'r0c1', 'r1c1', 'r2c2'].map((p) => [p, at(first(p))])),
            alpha: Math.round(P.levelOpacity(scene.brightness_draft, (panel.canopy.view || {}).dim_floor) * 255),
            other: { canvases: other.querySelectorAll('canvas.thumb').length,
                     strip: (other.querySelector('.strip') || {}).style?.background || '' },
          };
        }""",
        by_id["coastal-tides"],
    )
    assert got["sig"] == strand["sig"] and got["strips"] == 0, got
    for pos in ("r0c0", "r0c1"):  # the driven panels: the strand look's one colour, at the card's opacity
        rgba = got["picks"][pos]
        assert all(abs(a - b) <= 6 for a, b in zip(rgba[:3], (121, 206, 212), strict=True)), (pos, rgba)
        assert abs(rgba[3] - got["alpha"]) <= 8, (pos, rgba, got["alpha"])
    for pos in ("r1c1", "r2c2"):  # not driven: no dot drawn
        assert got["picks"][pos][3] == 0, (pos, got["picks"][pos])
    assert got["other"]["canvases"] == 0 and "gradient" in got["other"]["strip"], got["other"]
    assert errors == []


def test_review_thumbnails_follow_a_quarter_turned_view(design_context_factory):
    stub = StubPanel(
        scene="golden-hour",
        review=True,
        canopy=True,
        view=PreviewSettings(rotate_deg=90, mirror_x=True),
    )
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    outside = page.evaluate(
        """async () => {
          const P = await import('/kiosk/canopy-preview.mjs?v=review-1');
          const panel = await (await fetch('/api/kiosk/panel')).json();
          const bad = [];
          for (const canvas of document.querySelectorAll('#grid canvas.thumb')) {
            const w = canvas.clientWidth, h = canvas.clientHeight;
            for (const d of P.layout(panel.canopy, { width: w, height: h }).dots)
              if (d.cx < 0 || d.cy < 0 || d.cx > w || d.cy > h) bad.push([canvas.closest('.scene').dataset.scene, d.cx, d.cy, w, h]);
          }
          return [document.querySelectorAll('#grid canvas.thumb').length, bad.slice(0, 5)];
        }"""
    )
    assert outside[0] == 18 and outside[1] == [], outside
    doc = _doc_scroll(page)
    assert doc[0] <= 0 and doc[1] <= 0, doc
    assert errors == []


# five '#2' options and the idle scene (which can't be hidden): six visible, so the usual grid
SIX_OPTIONS = frozenset(
    {b for b in BASES if b != "lowcountry-calm"} | {f"{b}-3" for b in BASES} | {"lowcountry-calm-2"}
)


@pytest.mark.parametrize("viewport", [LANDSCAPE, OLDER, PORTRAIT])
def test_six_visible_options_keep_the_3x2_grid(design_context_factory, screenshots, viewport):
    stub = StubPanel(scene="lowcountry-calm", review=True, canopy=True, hide=SIX_OPTIONS)
    assert [s["id"] for s in stub.scenes] == ["lowcountry-calm"] + [f"{b}-2" for b in BASES[1:]]
    _ctx, page, errors = _open(design_context_factory, viewport, stub)
    _ready(page, scene="lowcountry-calm")
    assert page.get_attribute("#app", "data-review") == "false"
    assert page.locator("#grid .scene").count() == 6
    assert page.locator("#grid canvas.thumb").count() == 0 and page.locator("#grid .strip").count() == 6
    _no_overflow(page, viewport)
    cards = _cards(page)
    assert all(c["bottom"] <= viewport["height"] for c in cards), cards
    rows = sorted({round(c["y"]) for c in cards})
    assert len(rows) == 2 and all(sum(round(c["y"]) == y for c in cards) == 3 for y in rows), cards
    if viewport == LANDSCAPE:
        page.screenshot(path=str(screenshots / "kiosk-six-options-1180x820.png"))
    assert errors == []


def test_a_hidden_scene_that_is_showing_is_named_in_now_showing(design_context_factory):
    stub = StubPanel(scene="golden-hour-3", review=True, canopy=True, hide=frozenset({"golden-hour-3"}))
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub)
    _ready(page)
    assert page.text_content("#now-name") == "Golden Hour #3"
    assert page.locator(".scene[data-scene='golden-hour-3']").count() == 0
    assert page.locator(".scene[data-state='selected']").count() == 0
    assert len(_dots(page)) == 270
    assert errors == []


def test_the_review_attract_cycles_one_strip_per_scene(design_context_factory):
    stub = StubPanel(scene="golden-hour", review=True)
    _ctx, page, errors = _open(design_context_factory, LANDSCAPE, stub, path="/kiosk/?debug=1")
    _ready(page)
    page.evaluate("document.querySelector('#grid').scrollTop = 1e6")
    assert page.evaluate("document.querySelector('#grid').scrollTop") > 0
    page.tap(".dbg-btn")
    page.wait_for_selector(".dbg")
    page.click("text=Show attract now")
    page.wait_for_selector("#attract:not([hidden])")
    assert page.locator("#attract-strips span").count() == 6  # one per scene group, never 18
    assert page.text_content("#attract-mood").strip()
    page.wait_for_timeout(700)  # past the debug button's swallow window
    page.mouse.click(LANDSCAPE["width"] / 2, LANDSCAPE["height"] / 2)
    page.wait_for_selector("#attract", state="hidden", timeout=3000)
    assert page.evaluate("document.querySelector('#grid').scrollTop") == 0  # the next guest starts at the top
    assert errors == []


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
