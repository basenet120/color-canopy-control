"""/api/kiosk/* and the /kiosk page: the guest touchscreen's API against a FakeEngine at services.extras["canopy"].

Proves: the mode list is modes.yaml; select → the engine (source="kiosk") → state; the cooldown (429 +
retry_after_s), paused guest input (423), unknown modes (404) and a missing or broken engine (503 "resting") are
plain refusals — whether the engine reports them in its state or by raising ModeRefused; JSON only (415); an
enrolled kiosk reaches /kiosk and /api/kiosk/* but nothing of ops or bench; the guest panel page is vendored, built
for touch and never writes its own Hubspace feature copy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from technique_modes import PATH as TECHNIQUES

from conductor.engine_api import (
    CanopyEngineAPI,
    CanopyPhase,
    CanopyState,
    ModePlan,
    ModeRefused,
    PlanError,
    RefusalReason,
)
from conductor.routes.kiosk import RESTING, _pattern, load_kiosk_modes

ROOT = Path(__file__).resolve().parent.parent
MODES_YAML = yaml.safe_load((ROOT / "config" / "modes.yaml").read_text())
KIOSK_DIR = ROOT / "web" / "kiosk"
COOLDOWN_S = 8.0
CALM = "lowcountry-calm"
SUNSET = "rainbow-row-sunset"
FEATURE = "Tunable white and dimming: all nine Hubspace strands set to a warm 2700 K."


class FakeEngine:
    """A CanopyEngineAPI with a real cooldown and pause on the app's clock.

    advertise=False hides the cooldown/pause from state() so the route has to rely on ModeRefused."""

    def __init__(self, clock, *, advertise: bool = True, known: set[str] | None = None) -> None:
        self.clock = clock
        self.advertise = advertise
        self.known = known if known is not None else set(MODES_YAML["modes"])
        self.mode = CALM
        self.phase = CanopyPhase.READY
        self.paused = False
        self.cooldown_until: float | None = None
        self.calls: list[tuple[str, str]] = []
        self.fail: Exception | None = None

    def state(self) -> CanopyState:
        label = MODES_YAML["modes"][self.mode]["label"]
        phase = CanopyPhase.RESTING if self.paused and self.advertise else self.phase
        return CanopyState(
            mode=self.mode,
            label=label,
            tier="strand" if self.mode == CALM else "canvas",
            feature=FEATURE if self.mode == CALM else f"Feature sentence for {self.mode}.",
            phase=phase,
            guest_input=not self.paused if self.advertise else True,
            cooldown_until=self.cooldown_until if self.advertise else None,
            changed_at=self.clock.now(),
            modules={"canopy-r0c0": {"status": "ready", "reason": "", "simulated": True}},
            simulated=True,
            message="",
        )

    def current_plan(self) -> ModePlan | None:
        return None

    async def request_mode(self, mode_id: str, *, source: str) -> CanopyState:
        self.calls.append((mode_id, source))
        if self.fail is not None:
            raise self.fail
        if mode_id not in self.known:
            raise ModeRefused(RefusalReason.UNKNOWN_MODE, "no such mode")
        if self.paused:
            raise ModeRefused(RefusalReason.PAUSED, "paused")
        now = self.clock.now()
        if self.cooldown_until is not None and now < self.cooldown_until:
            raise ModeRefused(RefusalReason.COOLDOWN, "cooling", retry_after_s=self.cooldown_until - now)
        self.mode = mode_id
        self.phase = CanopyPhase.UPDATING
        self.cooldown_until = now + COOLDOWN_S
        return self.state()

    async def reassert_all(self, *, source: str) -> CanopyState:
        return self.state()

    def pause_guest_input(self, paused: bool, *, source: str) -> CanopyState:
        self.paused = paused
        return self.state()

    async def safe_look(self, *, source: str) -> CanopyState:
        self.mode = CALM
        return self.state()

    async def commission(self, *, source: str) -> dict:
        return {}


def _install(client, **kw) -> FakeEngine:
    services = client.app.state.services
    engine = FakeEngine(services.clock, **kw)
    services.extras["canopy"] = engine
    return engine


@pytest.fixture
def engine(app_client) -> FakeEngine:
    return _install(app_client)


def test_fake_engine_implements_the_protocol(app_client):
    engine: CanopyEngineAPI = FakeEngine(app_client.app.state.services.clock)
    assert callable(engine.state) and callable(engine.request_mode)


# ---- modes -------------------------------------------------------------------------------------------------------
def test_modes_list_matches_modes_yaml(app_client, engine):
    r = app_client.get("/api/kiosk/modes")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    listing = r.json()
    specs = MODES_YAML["modes"]
    assert [m["id"] for m in listing] == list(specs)
    assert len(listing) == 6
    for m in listing:
        assert m["label"] == specs[m["id"]]["label"]
        assert m["blurb"] == specs[m["id"]]["blurb"]
        assert m["preview"] == specs[m["id"]]["preview"]
    # the feature sentence is the engine's, and only for the mode actually showing
    by_id = {m["id"]: m for m in listing}
    assert by_id[CALM]["feature"] == FEATURE
    assert all(m["feature"] is None for m in listing if m["id"] != CALM)


def test_modes_patterns_follow_each_modes_technique():
    patterns = {m["id"]: m["pattern"] for m in load_kiosk_modes()}
    assert patterns == {  # the painted three's canvas is a 2-D design (D63); the rest start at module level
        "lowcountry-calm": "modules",
        "rainbow-row-sunset": "design",
        "garden-gathering": "design",
        "coastal-tides": "modules",
        "golden-hour": "design",
        "goodnight-glow": "modules",
    }
    # a hand-painted slot draws like its canvas
    assert _pattern(MODES_YAML["modes"][SUNSET], "slot") == "design"
    assert _pattern(MODES_YAML["modes"][SUNSET], "modules") == "modules"


def test_module_level_modes_picture_their_designs_without_a_plan(app_client, engine):
    """No compiled plan (FakeEngine has no compile_best): a modules-first mode still lists its design at each
    module's centre in r0c0…r2c2 order (D63), so the tile shows what module level puts on the ceiling."""
    from conductor.geometry import load_canopy_config
    from conductor.looks import field as F
    from conductor.looks import load_modes
    from conductor.scenes import load_scenes

    canopy, modes, scenes = load_canopy_config(), load_modes(), load_scenes()
    by_id = {m["id"]: m for m in app_client.get("/api/kiosk/modes").json()}
    for mode_id in ("lowcountry-calm", "coastal-tides", "goodnight-glow"):
        spec = modes.mode(mode_id).techniques.modules.sample.canvas.field
        palette = scenes.get(mode_id).model_copy(update={"transform": "linear"})  # the design's stops
        expected = [
            "#" + "".join(f"{v:02X}" for v in palette.color_at(F.value(spec, x, y, canopy)))
            for x, y in F.module_centres(canopy, [f"r{r}c{c}" for r in range(3) for c in range(3)]).values()
        ]
        assert by_id[mode_id]["pattern"] == "modules"
        assert by_id[mode_id]["module_colours"] == expected
        assert len(set(expected)) >= 4  # a 2-D design, not three columns
    # radial: sea glass in the centre (#D1EAE3, the pinned approved value: Design Lab PLAN §3.7), corners alike
    pins = json.loads((Path(__file__).parent / "fixtures_scene_frames.json").read_text())["_pins"]
    calm_pin = pins["lowcountry-calm"]
    calm = by_id["lowcountry-calm"]["module_colours"]
    centre = "#" + "".join(f"{v:02X}" for v in calm_pin["t3"]["r1c1"])
    assert calm[4] == centre, "approved value changed: run make scene-pins and review the fixture diff"
    if calm_pin["layout_words"] == "from the centre out":  # D63's radial from the canopy's centre
        assert calm[0] == calm[2] == calm[6] == calm[8] != calm[4] == centre
        assert calm[1] == calm[7] and calm[3] == calm[5] and calm[1] != calm[3]
    assert all(by_id[m]["module_colours"] is None for m in (SUNSET, "garden-gathering", "golden-hour"))


def test_a_design_tile_needs_the_validated_mode_and_says_nothing_it_cant_work_out(caplog):
    """Raw YAML can't be sampled (the looks loader refused it, or a fixture path): no colours rather than a guess."""
    from conductor.looks import load_modes
    from conductor.routes.kiosk import _static_module_colours

    spec = MODES_YAML["modes"][CALM]
    with caplog.at_level("WARNING", logger="conductor.routes.kiosk"):
        assert _static_module_colours(spec, None) is None
    assert not caplog.text  # the refusal was logged where the loader failed; nothing to add here
    broken = load_modes().mode(CALM).model_copy(deep=True)
    broken.techniques.modules.sample.canvas.stops[0].color = "no-such-colour"
    with caplog.at_level("WARNING", logger="conductor.routes.kiosk"):
        assert _static_module_colours(spec, broken) is None
    assert "couldn't sample a module-level design" in caplog.text
    assert len(_static_module_colours(spec, load_modes().mode(CALM))) == 9


def test_a_modules_first_mode_with_literal_columns_pictures_them_column_by_column():
    """The schema still takes literal `columns` (no show mode uses them since D63): a modules-first mode lists its
    three column colours in r0c0…r2c2 order — the left column's colour on c0 of every row — with no plan and no
    validated mode; anything but three RGB columns says nothing rather than guess."""
    from conductor.routes.kiosk import _static_module_colours

    modules = yaml.safe_load(TECHNIQUES.read_text())["modes"]["golden-hour"]["techniques"]["modules"]
    spec = {"ladder": ["modules", "strand"], "techniques": {"modules": modules}}
    hexes = ["#FF5A00", "#FF7E00", "#FFAA3C"]  # the fixture's amber columns, left to right
    assert [c["rgb"] for c in modules["columns"]] == [[255, 90, 0], [255, 126, 0], [255, 170, 60]]
    assert _static_module_colours(spec) == hexes * 3
    assert _static_module_colours({**spec, "ladder": ["canvas", "modules"]}) is None  # not modules-first
    two = {"ladder": ["modules"], "techniques": {"modules": {"columns": modules["columns"][:2]}}}
    assert _static_module_colours(two) is None
    white = [*modules["columns"][:2], {"kind": "white", "cct": 2700, "brightness": 50}]
    assert (
        _static_module_colours({"ladder": ["modules"], "techniques": {"modules": {"columns": white}}}) is None
    )


def test_each_tier_draws_what_the_ceiling_shows():
    specs = yaml.safe_load(TECHNIQUES.read_text())["modes"]  # the pre-D59 recipes: every technique kind
    assert {m: _pattern(spec) for m, spec in specs.items()} == {
        "lowcountry-calm": "wash",
        "rainbow-row-sunset": "gradient",
        "garden-gathering": "pathway",
        "holiday-at-home": "scatter",
        "golden-hour": "bands",
        "goodnight-glow": "wash",
    }
    sunset = specs["rainbow-row-sunset"]
    assert _pattern(sunset, "slot") == "gradient"  # a hand-painted slot draws like the canvas
    assert _pattern(sunset, "modules") == "modules"
    assert _pattern(specs["lowcountry-calm"], "strand") == "modules"  # one colour per module
    assert _pattern(specs["holiday-at-home"], "strand") == "scatter"  # a preset's heads differ


def test_modes_serve_without_an_engine(app_client):
    app_client.app.state.services.extras.pop("canopy", None)
    listing = app_client.get("/api/kiosk/modes").json()
    assert len(listing) == 6 and all(m["feature"] is None for m in listing)


def test_the_legacy_kiosk_routes_leave_hidden_scenes_out(app_client, engine):
    """kiosk.hidden_scenes (D76) takes a scene off /api/kiosk/modes too, and /api/kiosk/select refuses it as unknown
    before the engine is asked."""
    kiosk = app_client.app.state.services.settings.kiosk
    kiosk.hidden_scenes = ["golden-hour"]  # in place, as the /ops switch does
    ids = [m["id"] for m in app_client.get("/api/kiosk/modes").json()]
    assert ids == [m for m in MODES_YAML["modes"] if m != "golden-hour"]
    r = app_client.post("/api/kiosk/select", json={"mode": "golden-hour"})
    assert r.status_code == 404 and r.json()["detail"] == "That mood isn't one of the canopy's choices."
    assert engine.calls == []
    kiosk.hidden_scenes = []
    assert len(app_client.get("/api/kiosk/modes").json()) == 6
    assert app_client.post("/api/kiosk/select", json={"mode": "golden-hour"}).status_code == 200
    assert engine.calls == [("golden-hour", "kiosk")]


# ---- state + select ------------------------------------------------------------------------------------------------
def test_state_is_the_engines_canopy_state_with_server_relative_cooldown(app_client, engine):
    now = app_client.app.state.services.clock.now()
    engine.cooldown_until = now + 5
    body = app_client.get("/api/kiosk/state").json()
    assert {k: body[k] for k in engine.state().to_json() if k != "changed_at"} == {
        k: v for k, v in engine.state().to_json().items() if k != "changed_at"
    }
    assert body["phase"] == "ready" and body["feature"] == FEATURE and body["simulated"] is True
    assert abs(body["server_now"] - now) < 5
    assert 3 < body["cooldown_remaining_s"] <= 5


def test_select_asks_the_engine_and_returns_state(app_client, engine):
    r = app_client.post("/api/kiosk/select", json={"mode": SUNSET})
    assert r.status_code == 200, r.text
    body = r.json()
    assert engine.calls == [(SUNSET, "kiosk")]
    assert body["mode"] == SUNSET and body["phase"] == "updating"
    assert body["feature"] == f"Feature sentence for {SUNSET}."
    assert 7 < body["cooldown_remaining_s"] <= COOLDOWN_S


def test_cooldown_is_a_429_with_retry_after(app_client, engine):
    assert app_client.post("/api/kiosk/select", json={"mode": SUNSET}).status_code == 200
    r = app_client.post("/api/kiosk/select", json={"mode": "golden-hour"})
    assert r.status_code == 429
    body = r.json()
    assert 7 < body["retry_after_s"] <= COOLDOWN_S
    assert "moment" in body["detail"]
    assert r.headers["retry-after"] == "8"
    assert engine.calls == [(SUNSET, "kiosk")]  # refused before the engine was bothered
    # once the cooldown has passed, the next choice goes through
    engine.cooldown_until = app_client.app.state.services.clock.now() - 0.1
    assert app_client.post("/api/kiosk/select", json={"mode": "golden-hour"}).status_code == 200


def test_cooldown_reported_only_by_the_engine_is_still_a_429(app_client):
    engine = _install(app_client, advertise=False)
    assert app_client.post("/api/kiosk/select", json={"mode": SUNSET}).status_code == 200
    r = app_client.post("/api/kiosk/select", json={"mode": "golden-hour"})
    assert r.status_code == 429 and 7 < r.json()["retry_after_s"] <= COOLDOWN_S
    assert len(engine.calls) == 2


def test_paused_guest_input_is_a_423(app_client, engine):
    engine.pause_guest_input(True, source="ops")
    r = app_client.post("/api/kiosk/select", json={"mode": SUNSET})
    assert r.status_code == 423 and "resting" in r.json()["detail"]
    assert engine.calls == []
    hidden = _install(app_client, advertise=False)
    hidden.paused = True
    assert app_client.post("/api/kiosk/select", json={"mode": SUNSET}).status_code == 423
    assert hidden.calls == [(SUNSET, "kiosk")]


def test_unknown_mode_is_a_404(app_client, engine):
    r = app_client.post("/api/kiosk/select", json={"mode": "disco-inferno"})
    assert r.status_code == 404 and "isn't one of" in r.json()["detail"]
    assert engine.calls == []
    # a mode the yaml has but the engine doesn't know
    picky = _install(app_client, known={CALM})
    assert app_client.post("/api/kiosk/select", json={"mode": SUNSET}).status_code == 404
    assert picky.calls == [(SUNSET, "kiosk")]


def test_no_engine_is_a_503_resting(app_client):
    app_client.app.state.services.extras.pop("canopy", None)
    for r in (
        app_client.get("/api/kiosk/state"),
        app_client.post("/api/kiosk/select", json={"mode": SUNSET}),
    ):
        assert r.status_code == 503
        assert r.json()["detail"] == RESTING == "The canopy is resting. It will be back shortly."


def test_engine_failures_are_plain(app_client, engine):
    engine.fail = RuntimeError("Traceback: something deep")
    r = app_client.post("/api/kiosk/select", json={"mode": SUNSET})
    assert r.status_code == 503 and r.json()["detail"] == RESTING and "Traceback" not in r.text
    engine.fail = PlanError("slot hash unknown for canopy-r1c1")
    r = app_client.post("/api/kiosk/select", json={"mode": SUNSET})
    assert r.status_code == 409 and "canopy-r1c1" not in r.text
    engine.fail = ModeRefused(RefusalReason.RESTING, "outage")
    assert app_client.post("/api/kiosk/select", json={"mode": SUNSET}).status_code == 503


def test_resting_phase_refuses_with_503(app_client, engine):
    engine.phase = CanopyPhase.RESTING
    assert app_client.post("/api/kiosk/select", json={"mode": SUNSET}).status_code == 503
    assert engine.calls == []


def test_select_is_json_only(app_client, engine):
    r = app_client.post("/api/kiosk/select", data={"mode": SUNSET})
    assert r.status_code == 415
    r = app_client.post("/api/kiosk/select", content='{"mode": "x"}', headers={"Content-Type": "text/plain"})
    assert r.status_code == 415
    assert app_client.post("/api/kiosk/select", json={"mood": SUNSET}).status_code == 422
    assert app_client.post("/api/kiosk/select", json={"mode": ""}).status_code == 422
    r = app_client.post(
        "/api/kiosk/select", json={"mode": SUNSET}, headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403
    assert engine.calls == []


# ---- who may call ----------------------------------------------------------------------------------------------
OPS = ("crew", "canopy")


def _enroll(client, role: str, grants: list[str] | None = None) -> None:
    link = client.post(
        "/api/roles/enrollments",
        json={"role": role, "name": f"test {role}", "grants": grants or []},
        auth=OPS,
    ).json()
    assert client.post(link["path"], follow_redirects=False).status_code == 303


def test_an_enrolled_kiosk_reaches_only_the_kiosk(app_client_auth):
    engine = _install(app_client_auth)
    client = app_client_auth
    assert client.get("/api/kiosk/modes").status_code == 401  # not enrolled yet
    _enroll(client, "kiosk")
    assert client.get("/kiosk/").status_code == 200
    assert client.get("/kiosk/kiosk.js").status_code == 200
    assert client.get("/api/kiosk/modes").status_code == 200
    assert client.get("/api/kiosk/state").json()["mode"] == CALM
    assert client.post("/api/kiosk/select", json={"mode": SUNSET}).status_code == 200
    assert engine.calls == [(SUNSET, "kiosk")]
    assert client.get("/api/ws-ticket").status_code == 200
    for path in ("/ops/", "/ops/bench/", "/ops/accounts/", "/api/bench/jobs", "/api/devices", "/visualizer/"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/bench/run", json={}).status_code == 401
    assert client.post("/api/roles/enrollments", json={"role": "kiosk", "name": "x"}).status_code == 401
    # ops can use the kiosk API too
    assert client.get("/api/kiosk/state", auth=OPS).status_code == 200


def test_a_tester_cant_use_the_kiosk_api(app_client_auth):
    _install(app_client_auth)
    _enroll(app_client_auth, "tester", ["bench-kit"])
    assert app_client_auth.get("/api/kiosk/state").status_code == 401
    assert app_client_auth.post("/api/kiosk/select", json={"mode": SUNSET}).status_code == 401


# ---- the page (the Hubspace-style guest panel, docs/design/guest-panel.md) -------------------------------------
PAGE_FILES = ("index.html", "kiosk.css", "kiosk.js", "panel-model.mjs", "canopy-preview.mjs")
FONT_FACE_URL = re.compile(r'url\("(/static/fonts/canopy-rounded/[\w.-]+\.woff2)"\)')


def test_kiosk_page_is_served_and_vendored(app_client):
    r = app_client.get("/kiosk/")
    assert r.status_code == 200 and "The Color Canopy" in r.text
    for name in PAGE_FILES:
        text = (KIOSK_DIR / name).read_text().replace('"http://www.w3.org/2000/svg"', "")  # a namespace
        assert not re.search(r"https?://", text), f"{name} references an external URL"
        assert "@import" not in text
    # the only web font is the vendored Canopy Rounded (Quicksand, SIL OFL 1.1), served by the app itself
    css = (KIOSK_DIR / "kiosk.css").read_text()
    urls = FONT_FACE_URL.findall(css)
    assert urls and css.count("@font-face") == len(urls)
    for url in urls:
        assert app_client.get(url).status_code == 200, url
    assert (ROOT / "web" / "static" / "fonts" / "canopy-rounded" / "OFL.txt").is_file()
    for asset in (
        "/kiosk/kiosk.js",
        "/kiosk/panel-model.mjs",
        "/kiosk/canopy-preview.mjs",
        "/kiosk/brand/hubspace-logo.svg",
    ):
        assert app_client.get(asset).status_code == 200, asset
    html = (KIOSK_DIR / "index.html").read_text()
    assert re.search(r'src="/kiosk/kiosk\.js(\?v=[\w.-]+)?"', html) and 'href="/kiosk/kiosk.css"' in html
    assert "user-scalable=no" in html and "viewport-fit=cover" in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert 'rel="apple-touch-icon"' in html


def test_kiosk_page_serves_every_module_it_imports_and_draws_the_canopy(app_client):
    """Now showing draws the canopy's 270 lights (canopy-preview.mjs) in an svg the screen reader can name, and every
    module kiosk.js imports is a vendored file the app serves as JavaScript."""
    js = (KIOSK_DIR / "kiosk.js").read_text()
    imports = re.findall(r'from "(/[^"]+)"', js)
    paths = [i.split("?")[0] for i in imports]  # (versioned: see test_panel_latency)
    assert "/kiosk/canopy-preview.mjs" in paths and "/kiosk/panel-model.mjs" in paths
    for path in imports:
        r = app_client.get(path)
        assert r.status_code == 200, path
        assert "javascript" in r.headers["content-type"], (path, r.headers["content-type"])
    html = (KIOSK_DIR / "index.html").read_text()
    svg = re.search(r'<svg class="canopy-preview" id="canopy-preview"[^>]*>', html)
    assert svg and 'role="img"' in svg.group(0) and "aria-label=" in svg.group(0)
    assert 'id="snake"' in html  # the fallback when there is no picture (no look yet)


def test_kiosk_title_matches_the_signage_plan():
    html = (KIOSK_DIR / "index.html").read_text()
    assert MODES_YAML["kiosk"]["title"] in html


def test_kiosk_never_writes_its_own_hubspace_feature_copy():
    """The feature sentence comes from state.feature; no technique's sentence (or Hubspace claim) is in the page."""
    page = "".join((KIOSK_DIR / n).read_text() for n in PAGE_FILES)
    for spec in MODES_YAML["modes"].values():
        for technique in spec["techniques"].values():
            for key in ("feature", "fallback_feature", "motion_feature"):
                if key in technique:
                    assert technique[key] not in page
    js = (KIOSK_DIR / "kiosk.js").read_text()
    assert "state.feature" in js
    for name in ("kiosk.js", "panel-model.mjs", "canopy-preview.mjs"):
        js = re.sub(r"(^|\s)//.*$", "", (KIOSK_DIR / name).read_text(), flags=re.MULTILINE)
        assert not re.search(r"['\"`][^'\"`\n]*Hubspace[^'\"`\n]*['\"`]", js), (
            f"{name} must not write Hubspace copy"
        )


def test_kiosk_css_is_built_for_touch():
    css = (KIOSK_DIR / "kiosk.css").read_text()
    assert "#FA4616" in css  # Hubspace orange
    assert '"Canopy Rounded"' in css and "system-ui" in css  # with the system fallback
    for needle in (
        "user-select: none",
        "-webkit-touch-callout: none",
        "touch-action: manipulation",
        "overscroll-behavior: none",
        "env(safe-area-inset-top",
        "env(safe-area-inset-bottom",
        "prefers-reduced-motion: reduce",
    ):
        assert needle in css, needle
    assert "min-height: 72px" in css  # the switch's and slider's hit areas
    # hover is only ever an extra (debug view), never needed on the iPad
    assert all(css.rfind("@media (hover: hover)", 0, m.start()) != -1 for m in re.finditer(r":hover", css))


def _block(flat: str, selector: str) -> str:
    """The declarations of the first `selector{…}` in whitespace-free CSS."""
    start = flat.index(selector + "{") + len(selector) + 1
    return flat[start : flat.index("}", start)]


def test_review_mode_css_scrolls_only_the_scene_list():
    """The review menu (D76, more than six scenes): the sheet never scrolls, only #grid does, overscroll contained,
    rows a fixed --review-row tall, each scene group starting at the left; canvases (the thumbnails) pass taps on."""
    css = (KIOSK_DIR / "kiosk.css").read_text()
    flat = re.sub(r"\s+", "", css)
    sheet = _block(flat, '.app[data-review="true"].sheet-body')
    assert "overflow:hidden" in sheet and "grid-template-rows:minmax(0,1fr)" in sheet
    grid = _block(flat, '.app[data-review="true"].grid')
    for needle in (
        "overflow-y:auto",
        "overflow-x:hidden",
        "overscroll-behavior:contain",
        "grid-auto-rows:minmax(var(--review-row),auto)",
        "min-height:0",
        "mask-image:",
    ):
        assert needle in grid, needle
    assert '.app[data-review="true"].scene.row-start{grid-column-start:1;}' in flat
    assert "img,svg,canvas{" in flat
    assert "--review-row:232px" in flat and "--review-row:214px" in flat
    thumb = _block(flat, ".thumb")
    assert "height:var(--strip-h)" in thumb and "width:100%" in thumb
    # portrait: the scene list takes the height above the pinned side
    portrait = flat[flat.index("@media(orientation:portrait){") :]
    assert (
        '.app[data-review="true"].sheet-body{grid-template-rows:minmax(0,1fr)auto;align-content:stretch;}'
        in portrait
    )


def test_an_engine_that_cant_report_state_is_resting(app_client, engine):
    def broken():
        raise RuntimeError("engine stopped")

    engine.state = broken
    r = app_client.get("/api/kiosk/state")
    assert r.status_code == 503 and r.json()["detail"] == RESTING
    assert app_client.post("/api/kiosk/select", json={"mode": SUNSET}).status_code == 503
    assert app_client.get("/api/kiosk/modes").status_code == 200  # the list still serves


# ---- the stepped rehearsal (Gate R) ------------------------------------------------------------------------------
def _rehearsing(c) -> str:
    """Start a 2-minute stepped rehearsal from /ops on the show engine (as an operator does); returns the route."""
    from show_started import past_startup

    past_startup(c)  # past startup commissioning, which would refuse the rehearsal or land in it
    route = "/api/canopy/stepped-rehearsal"
    asked = c.get(route, params={"mode": "golden-hour", "minutes": 2})
    assert asked.status_code == 200, asked.text
    sentence = asked.json()["preview"]["confirm"]
    started = c.post(route, json={"mode": "golden-hour", "minutes": 2, "confirm": sentence})
    assert started.status_code == 200, started.text
    return route


def test_a_mood_tapped_during_the_stepped_rehearsal_says_the_canopy_is_being_tested(app_client_show):
    """Every mood waits for the rehearsal, so "choose another" would be false: the guest hears the canopy is being
    tested, and when to try again."""
    from conductor.routes.kiosk import TESTING

    c = app_client_show
    route = _rehearsing(c)
    try:
        r = c.post("/api/kiosk/select", json={"mode": "coastal-tides"})
        assert r.status_code == 503 and r.json()["detail"] == TESTING, r.text
        assert 0 < r.json()["retry_after_s"] <= 60
    finally:
        assert c.post(f"{route}/stop", json={}).status_code == 200
