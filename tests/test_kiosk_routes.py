"""/api/kiosk/* and the /kiosk page: the guest touchscreen's API against a FakeEngine at services.extras["canopy"].

Proves: the mode list is modes.yaml; select → the engine (source="kiosk") → state; the cooldown (429 +
retry_after_s), paused guest input (423), unknown modes (404) and a missing or broken engine (503 "resting") are
plain refusals — whether the engine reports them in its state or by raising ModeRefused; JSON only (415); an
enrolled kiosk reaches /kiosk and /api/kiosk/* but nothing of ops or bench; the guest panel page is vendored, built
for touch and never writes its own Hubspace feature copy.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from conductor.engine_api import (
    CanopyEngineAPI,
    CanopyPhase,
    CanopyState,
    ModePlan,
    ModeRefused,
    PlanError,
    RefusalReason,
)
from conductor.routes.kiosk import RESTING, load_kiosk_modes

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
    assert patterns == {
        "lowcountry-calm": "wash",
        "rainbow-row-sunset": "gradient",
        "garden-gathering": "pathway",
        "holiday-at-home": "scatter",
        "golden-hour": "bands",
        "goodnight-glow": "wash",
    }


def test_modes_serve_without_an_engine(app_client):
    app_client.app.state.services.extras.pop("canopy", None)
    listing = app_client.get("/api/kiosk/modes").json()
    assert len(listing) == 6 and all(m["feature"] is None for m in listing)


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
PAGE_FILES = ("index.html", "kiosk.css", "kiosk.js", "panel-model.mjs")
FONT_FACE_URL = re.compile(r'url\("(/static/fonts/quicksand/[\w.-]+\.woff2)"\)')


def test_kiosk_page_is_served_and_vendored(app_client):
    r = app_client.get("/kiosk/")
    assert r.status_code == 200 and "The Color Canopy" in r.text
    for name in PAGE_FILES:
        text = (KIOSK_DIR / name).read_text().replace('"http://www.w3.org/2000/svg"', "")  # a namespace
        assert not re.search(r"https?://", text), f"{name} references an external URL"
        assert "@import" not in text
    # the only web font is the vendored Quicksand (SIL OFL 1.1), served by the app itself
    css = (KIOSK_DIR / "kiosk.css").read_text()
    urls = FONT_FACE_URL.findall(css)
    assert urls and css.count("@font-face") == len(urls)
    for url in urls:
        assert app_client.get(url).status_code == 200, url
    assert (ROOT / "web" / "static" / "fonts" / "quicksand" / "OFL.txt").is_file()
    for asset in ("/kiosk/kiosk.js", "/kiosk/panel-model.mjs", "/kiosk/brand/hubspace-logo.svg"):
        assert app_client.get(asset).status_code == 200, asset
    html = (KIOSK_DIR / "index.html").read_text()
    assert 'src="/kiosk/kiosk.js"' in html and 'href="/kiosk/kiosk.css"' in html
    assert "user-scalable=no" in html and "viewport-fit=cover" in html
    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert 'rel="apple-touch-icon"' in html


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
    for name in ("kiosk.js", "panel-model.mjs"):
        js = re.sub(r"(^|\s)//.*$", "", (KIOSK_DIR / name).read_text(), flags=re.MULTILINE)
        assert not re.search(r"['\"`][^'\"`\n]*Hubspace[^'\"`\n]*['\"`]", js), (
            f"{name} must not write Hubspace copy"
        )


def test_kiosk_css_is_built_for_touch():
    css = (KIOSK_DIR / "kiosk.css").read_text()
    assert "#FA4616" in css  # Hubspace orange
    assert '"Quicksand"' in css and "system-ui" in css  # with the system fallback
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


def test_an_engine_that_cant_report_state_is_resting(app_client, engine):
    def broken():
        raise RuntimeError("engine stopped")

    engine.state = broken
    r = app_client.get("/api/kiosk/state")
    assert r.status_code == 503 and r.json()["detail"] == RESTING
    assert app_client.post("/api/kiosk/select", json={"mode": SUNSET}).status_code == 503
    assert app_client.get("/api/kiosk/modes").status_code == 200  # the list still serves
