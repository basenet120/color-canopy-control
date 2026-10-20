"""Regression tests for the P1–P3 adversarial review's surface findings (F2–F6, F8), against the REAL canopy engine
where the finding was about it (conductor/canopy.py reports refusals as a CanopyState with `refused=True`, never an
exception — the fakes elsewhere raise ModeRefused, which is how these slipped through).

- F2  a refused kiosk select is a plain non-2xx (never 200 "Changing the light…"); commissioning is "resting".
- F3  ops controls report the engine's refusal (409 / ok:false), never a green "Done."
- F4  installing the log ring keeps Conductor warnings on stderr; startup problems reach the ring.
- F5  engine alerts (telemetry `alert` warn/error and `tier` downgrades) reach /ops Needs attention and the log.
- F6  the checklist fails Guest input while the canopy rests; re-assert says when there's nothing to re-assert.
- F8  at module level (T3) the kiosk gets one colour per module, from the plan actually in use.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest
from conftest import _env
from fake_engine import RecordingEngine
from fastapi.testclient import TestClient
from partial_canopy import LIVE, partial_show, showing
from show_started import wait_until

from conductor import logring, redact
from conductor.engine_api import CanopyPhase, CanopyState, PlanError, Tier
from conductor.routes import ops as ops_routes

REAL_NTP_STATUS = ops_routes.ntp_status  # before the autouse fixture swaps it
CALM = "lowcountry-calm"
SUNSET = "rainbow-row-sunset"
GOLDEN = "golden-hour"


@pytest.fixture(autouse=True)
def _fast_ntp(monkeypatch):
    async def synced():
        return True, "test: synced"

    monkeypatch.setattr(ops_routes, "ntp_status", synced)


def _engine(client):
    return client.app.state.services.extras["canopy"]


def _settled(client, mode: str | None = CALM, timeout: float = 30.0):
    """Wait until startup commissioning is over and the canopy is ready (on `mode`, when given)."""
    engine, wiring = _engine(client), client.app.state.services.extras["show"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = engine.state()
        # `started`, not just "not commissioning": the first look is READY before commissioning begins (show_started.py)
        ready = st.phase == CanopyPhase.READY and (mode is None or st.mode == mode)
        if wiring.started and not engine.commissioning and ready:
            engine._last_kiosk_mono = None  # no guest cooldown left over from another step
            return engine
        time.sleep(0.05)
    raise AssertionError(f"the canopy never settled: {engine.state()}")


def _refuse_every_compile(engine) -> None:
    def refuse(mode_id, tier):
        raise PlanError(f"{mode_id} can't compile at {tier} (test)")

    engine.deps.compile = refuse


@pytest.fixture
def stuck_show(monkeypatch, tmp_path):
    """The real engine whose looks never compile (review F1's stuck engine): no plan, so the canopy rests."""
    from conductor.app import app
    from conductor.show import ShowWiring

    def refuse(self, mode_id, tier):
        raise PlanError("the looks can't compile on this box (test)")

    monkeypatch.setattr(ShowWiring, "compile", refuse)
    _env(monkeypatch, tmp_path, engine=True, CONDUCTOR_AUTH="off")
    with TestClient(app) as client:
        wiring = client.app.state.services.extras["show"]
        wait_until("the stuck show's startup", lambda: wiring.started and not _engine(client).commissioning)
        yield client


@pytest.fixture
def t3_show(monkeypatch, tmp_path):
    """The real engine as live runs before Gate B/C and Gate R: canvas, slot and stepped tiers not allowed (as
    tests/partial_canopy.py's t3_only), so Sunset is T3 (D77 gives every scene a stepped rung)."""
    from conductor.app import app
    from conductor.show import STEPPED_LIVE_OFF, ShowWiring

    def only_t3(self, mode_id, tier):
        if tier in (Tier.T1, Tier.T2):
            return False, "Gate C isn't proven on this kit (test)"
        if tier is Tier.STEPPED:
            return False, STEPPED_LIVE_OFF
        return True, ""

    monkeypatch.setattr(ShowWiring, "tier_allowed", only_t3)
    _env(monkeypatch, tmp_path, engine=True, CONDUCTOR_AUTH="off")
    with TestClient(app) as client:
        yield client


# ---- F2: kiosk select refusals from the real engine -------------------------------------------------------------
def test_f2_select_during_commissioning_is_a_503_and_the_kiosk_state_is_resting(app_client_show):
    client = app_client_show
    engine = _settled(client)
    engine._commissioning = True  # what commission() holds while it writes the slots
    try:
        st = client.get("/api/kiosk/state").json()
        assert st["phase"] == "resting", "commissioning changes no look: never 'Changing the light…'"
        assert "being set up" in st["message"]
        r = client.post("/api/kiosk/select", json={"mode": SUNSET})
        assert r.status_code == 503, r.text
        assert "being set up" in r.json()["detail"]
        assert int(r.headers["Retry-After"]) >= 1
    finally:
        engine._commissioning = False
    assert engine.state().mode == CALM


def test_f2_select_that_no_tier_can_compile_is_a_409_and_nothing_changes(app_client_show):
    client = app_client_show
    engine = _settled(client)
    plan = engine.current_plan()
    _refuse_every_compile(engine)
    r = client.post("/api/kiosk/select", json={"mode": SUNSET})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "That mood isn't available right now. Choose another."
    assert engine.current_plan() is plan and engine.state().mode == CALM
    assert engine.state().cooldown_until is None, "a refused choice starts no cooldown"


class _RefusingEngine(RecordingEngine):
    """Reports a refusal the way conductor/canopy.py does: a CanopyState with refused=True (never raises)."""

    def __init__(self, *, cooldown_until=None, retry_after_s=None, **flags):
        super().__init__()
        self._cooldown_until = cooldown_until
        self._retry_after_s = retry_after_s
        self.commissioning = flags.get("commissioning", False)
        self.paused = flags.get("paused", False)
        self.outage = flags.get("outage")
        self.stopped = flags.get("stopped", False)

    async def request_mode(self, mode_id, *, source):
        self._call("request_mode", mode_id=mode_id, source=source)
        st = self.state()
        st.refused, st.message, st.retry_after_s = True, "engine says no", self._retry_after_s
        st.cooldown_until = self._cooldown_until
        return st


@pytest.mark.parametrize(
    "flags,status",
    [
        ({"commissioning": True, "retry_after_s": 30.0}, 503),
        ({"outage": {"reason": "x"}, "retry_after_s": 20.0}, 503),
        ({"stopped": True}, 503),
        ({"paused": True}, 423),
        ({"cooldown_until": "soon", "retry_after_s": 5.0}, 429),
        ({}, 409),
    ],
)
def test_f2_every_refused_state_maps_to_a_plain_refusal(app_client, flags, status):
    services = app_client.app.state.services
    if flags.get("cooldown_until") == "soon":
        flags["cooldown_until"] = services.clock.now() + 5.0
    services.extras["canopy"] = _RefusingEngine(**flags)
    r = app_client.post("/api/kiosk/select", json={"mode": SUNSET})
    assert r.status_code == status, r.text
    assert "engine says no" not in r.json()["detail"], "guest copy is the kiosk's own calm sentence"
    if status in (429, 503) and flags.get("retry_after_s"):
        assert int(r.headers["Retry-After"]) >= 1


# ---- F3: ops controls tell the truth -----------------------------------------------------------------------------
def test_f3_safe_look_and_mode_change_refusals_are_409_with_the_engines_reason(app_client_show):
    client = app_client_show
    engine = _settled(client)
    _refuse_every_compile(engine)
    r = client.post("/api/canopy/safe-look", json={})
    assert r.status_code == 409, r.text
    assert "safe look can't be compiled" in r.json()["detail"]
    r = client.post("/api/canopy/mode", json={"mode": GOLDEN})
    assert r.status_code == 409, r.text
    assert "can't be shown right now" in r.json()["detail"]
    assert engine.state().mode == CALM


def test_f3_safe_look_message_comes_from_what_was_sent(app_client_show):
    client = app_client_show
    _settled(client)
    client.post("/api/canopy/mode", json={"mode": GOLDEN})
    r = client.post("/api/canopy/safe-look", json={})
    assert r.status_code == 200 and r.json()["ok"] is True
    message = r.json()["message"]
    assert "Lowcountry Calm" in message and "9 of 9 modules" in message


def test_f3_safe_look_on_a_partial_canopy_counts_the_registered_modules(monkeypatch, tmp_path):
    with partial_show(monkeypatch, tmp_path, LIVE) as client:
        showing(client)
        r = client.post("/api/canopy/safe-look", json={})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    message = r.json()["message"]
    assert "Lowcountry Calm" in message and "on 2 of 9 modules" in message and "9 of 9" not in message
    assert "7 have no Hubspace device registered" in message and "/ops/accounts" in message


def test_f3_commission_refused_or_with_problems_is_not_ok(app_client_show):
    client = app_client_show
    engine = _settled(client)
    engine._commissioning = True
    try:
        r = client.post("/api/canopy/commission", json={})
    finally:
        engine._commissioning = False
    assert r.status_code == 409 and "already running" in r.json()["detail"]

    class Partial(RecordingEngine):
        async def commission(self, *, source):
            return {"ok": False, "message": "Wrote 7 slot(s); 2 need attention.", "finished_at": 1.0}

    client.app.state.services.extras["canopy"] = Partial()
    r = client.post("/api/canopy/commission", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["message"] == "Wrote 7 slot(s); 2 need attention."
    client.app.state.services.extras["canopy"] = engine


def test_f3_ops_js_shows_refusals_in_the_error_style():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "web" / "ops" / "ops.js").read_text()
    assert 'res.ok === false ? "error" : "ok"' in js
    assert '"Done."' not in js


# ---- F4: the log ring keeps stderr -------------------------------------------------------------------------------
@pytest.fixture
def fresh_ring():
    logring.uninstall()
    yield
    logring.uninstall()


def test_f4_conductor_warnings_still_reach_stderr_after_the_ring_installs(fresh_ring, capsys):
    redact.install()
    redact.add_secret("sekret-token-value-123")
    log = logging.getLogger("conductor.review.f4")
    logring.install()
    log.warning("AFTER ring: carrying sekret-token-value-123")
    log.error("an error after the ring")
    err = capsys.readouterr().err
    assert "AFTER ring" in err and "an error after the ring" in err
    assert "sekret-token-value-123" not in err
    log.info("an info line stays off stderr")
    assert "info line" not in capsys.readouterr().err
    ring = logring.installed()
    assert any("AFTER ring" in r["message"] for r in ring.records())


def test_f4_uninstall_takes_the_stderr_handler_away_too(fresh_ring):
    root = logging.getLogger()
    before = list(root.handlers)
    logring.install()
    assert len(root.handlers) >= len(before) + 1
    logring.uninstall()
    assert root.handlers == before


def test_f4_startup_problems_reach_the_log_tail(fresh_ring, monkeypatch, tmp_path):
    import conductor.services as services_module
    from conductor.app import app

    original = services_module.build_services

    def with_problem():
        services = original()
        services.problems.append("Startup problem for the review test.")
        return services

    monkeypatch.setattr(services_module, "build_services", with_problem)
    _env(monkeypatch, tmp_path, CONDUCTOR_AUTH="off")
    with TestClient(app) as client:
        records = client.get("/api/ops/logs").json()["records"]
    assert any("Startup problem for the review test." in r["message"] for r in records)


# ---- F5: engine alerts reach /ops ---------------------------------------------------------------------------------
def _attention(client) -> list[dict]:
    return client.get("/api/ops/summary").json()["attention"]


def test_f5_warn_alerts_and_tier_downgrades_reach_needs_attention_and_the_log(app_client):
    services = app_client.app.state.services
    drift = (
        "Module 1: the hand-painted pattern in custom-1 changed (hash abc, expected def). Repaint it in the "
        "Hubspace app, or record the new hash on /ops."
    )
    services.telemetry.record("alert", level="warn", device_id="canopy-r0c0", message=drift)
    services.telemetry.record("alert", level="info", message="Guest input paused (ops).")
    services.telemetry.record(
        "tier",
        mode=GOLDEN,
        reason="3 throttles in 60 s",
        direction="down",
        **{"from": "canvas", "to": "slot"},
    )
    services.telemetry.record(
        "alert", level="warn", message=f"{GOLDEN}: down from canvas to slot — 3 throttles in 60 s"
    )
    items = _attention(app_client)
    by_text = {i["message"]: i for i in items}
    drift_item = next(i for i in items if "hand-painted pattern in custom-1" in i["message"])
    assert drift_item["link"] == "/ops/module/?device=canopy-r0c0"
    assert drift_item.get("alert"), "an alert can be acknowledged"
    assert not any("Guest input paused" in m for m in by_text), "info alerts aren't problems"
    tier_items = [i for i in items if "Golden Hour" in i["message"] or GOLDEN in i["message"]]
    assert len(tier_items) == 1, f"one line per downgrade, not two: {tier_items}"
    assert "lower tier" in tier_items[0]["message"]
    # the log tail has it too (WARNING)
    records = app_client.get("/api/ops/logs").json()["records"]
    assert any(
        "hand-painted pattern in custom-1" in r["message"] and r["level"] == "WARNING" for r in records
    )

    # acknowledge clears it; the upgrade clears the downgrade by itself
    r = app_client.post("/api/ops/alerts/ack", json={"key": drift_item["alert"]})
    assert r.status_code == 200, r.text
    services.telemetry.record(
        "tier", mode=GOLDEN, reason="5 clean minutes", direction="up", **{"from": "slot", "to": "canvas"}
    )
    services.telemetry.record(
        "alert", level="info", message=f"{GOLDEN}: back up from slot to canvas — 5 clean"
    )
    items = _attention(app_client)
    assert not any("custom-1" in i["message"] for i in items)
    assert not any("Golden Hour" in i["message"] or GOLDEN in i["message"] for i in items)
    assert app_client.post("/api/ops/alerts/ack", json={"key": "nope"}).status_code == 404


def test_f5_the_same_alert_again_is_one_line_with_a_count(app_client):
    services = app_client.app.state.services
    for _ in range(3):
        services.telemetry.record("alert", level="warn", message="Idle return refused: nope.")
    items = [i for i in _attention(app_client) if "Idle return refused" in i["message"]]
    assert len(items) == 1 and "(3 times)" in items[0]["message"]


def test_f5_resting_alert_clears_when_hubspace_answers_again(app_client):
    services = app_client.app.state.services
    services.telemetry.record("alert", level="warn", message="Canopy resting: 3 modules failed (connection)")
    assert any("Canopy resting" in i["message"] for i in _attention(app_client))
    services.telemetry.record(
        "alert", level="info", message="Hubspace is answering again (probe); re-applying …"
    )
    assert not any("Canopy resting" in i["message"] for i in _attention(app_client))


# ---- F6: the checklist while the canopy rests -------------------------------------------------------------------
class _RestingEngine(RecordingEngine):
    def state(self) -> CanopyState:
        st = super().state()
        st.mode = st.label = st.tier = st.feature = None
        st.phase = CanopyPhase.RESTING
        st.message = "The canopy is getting ready."
        return st


def test_f6_guest_input_fails_while_the_canopy_rests_and_needs_attention_says_so(app_client):
    app_client.app.state.services.extras["canopy"] = _RestingEngine()
    body = app_client.get("/api/ops/checklist").json()
    item = next(i for i in body["items"] if i["id"] == "guest_input")
    assert item["status"] == "fail"
    assert "resting" in item["detail"].lower() and "getting ready" in item["detail"]
    assert any("getting ready" in i["message"] for i in _attention(app_client))
    r = app_client.post("/api/canopy/reassert", json={})
    assert r.status_code == 409 and "nothing to re-assert" in r.json()["detail"].lower()


def test_f6_the_stuck_engine_end_to_end(stuck_show):
    client = stuck_show
    st = client.get("/api/kiosk/state").json()
    assert st["phase"] == "resting"
    assert client.post("/api/kiosk/select", json={"mode": SUNSET}).status_code == 503
    assert client.post("/api/canopy/safe-look", json={}).status_code == 409
    assert client.post("/api/canopy/mode", json={"mode": GOLDEN}).status_code == 409
    r = client.post("/api/canopy/reassert", json={})
    assert r.status_code == 409 and "nothing to re-assert" in r.json()["detail"].lower()
    body = client.get("/api/ops/checklist").json()
    guest = next(i for i in body["items"] if i["id"] == "guest_input")
    assert guest["status"] == "fail"
    r = client.post("/api/ops/checklist/test-mode-change", json={})
    assert r.status_code == 200
    result = r.json()
    assert result["ok"] is False and "refused" in result["message"].lower()
    body = client.get("/api/ops/checklist").json()
    test_item = next(i for i in body["items"] if i["id"] == "test_mode_change")
    assert test_item["status"] == "fail"


# ---- F8: the kiosk illustration at module level -----------------------------------------------------------------
def test_f8_t3_state_and_modes_carry_one_colour_per_module(t3_show):
    client = t3_show
    _settled(client)
    listing = {m["id"]: m for m in client.get("/api/kiosk/modes").json()}
    assert listing[SUNSET]["tier"] == "modules" and listing[SUNSET]["pattern"] == "modules"
    assert len(listing[SUNSET]["module_colours"]) == 9
    # Lowcountry Calm at module level too (D59), its tilted rings (D77) as the pins hold them
    calm = listing[CALM]
    assert calm["tier"] == "modules" and calm["pattern"] == "modules"
    shown = calm["module_colours"]  # the module colours at Calm's brightness (each module's preview)
    corners = {shown[i] for i in (0, 2, 6, 8)}
    assert len(shown) == 9 and shown[4] not in corners  # sea glass near the centre, not at a corner
    pin = json.loads((Path(__file__).parent / "fixtures_scene_frames.json").read_text())["_pins"][CALM]
    if pin["layout_words"] == "from one spot":  # D77's rings tilted along the current: the corners differ
        assert len(corners) > 1

    assert client.post("/api/canopy/mode", json={"mode": SUNSET}).status_code == 200
    _settled(client, SUNSET)
    st = client.get("/api/kiosk/state").json()
    assert st["tier"] == "modules"
    colours = st["module_colours"]
    assert len(colours) == 9 and all(c.startswith("#") and len(c) == 7 for c in colours)
    plan = _engine(client).current_plan()
    first = plan.modules["canopy-r0c0"].preview[0]
    assert colours[0] == "#{:02X}{:02X}{:02X}".format(first["r"], first["g"], first["b"])
    assert len(set(colours)) > 1, "Sunset's design differs across the modules at module level"

    # the safe look is Calm's whole-strand technique: one flat colour (soft ivory) on every module, drawn as such
    assert client.post("/api/canopy/safe-look", json={}).status_code == 200
    _settled(client, CALM)
    st = client.get("/api/kiosk/state").json()
    assert st["tier"] == "strand" and len(set(st["module_colours"])) == 1
    listing = {m["id"]: m for m in client.get("/api/kiosk/modes").json()}
    assert listing[CALM]["tier"] == "strand" and listing[CALM]["pattern"] == "modules"


def test_f8_guest_panel_previews_what_is_lit_from_the_state():
    """The guest panel replaced the mode kiosk; its preview follows state.look (one colour when the strands took the
    solid fallback). Behaviour: tests/guest-panel.test.mjs ("now showing draws the one colour…")."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "web" / "kiosk" / "panel-model.mjs").read_text()
    assert 'state.look === "solid"' in js and "scene.solid" in js


# ---- (a) the /ops device list is grouped by role ---------------------------------------------------------------
def test_devices_come_grouped_canopy_bench_spares(app_client):
    """Canopy modules, bench, spares — and nothing after them (D67: no vignette rooms)."""
    body = app_client.get("/api/devices").json()
    groups = body["groups"]
    ids = [g["id"] for g in groups]
    assert ids == ["canopy", "bench", "spares"]
    assert all(g["strand"] is True and g["link"] is None for g in groups)
    canopy = groups[0]
    assert canopy["strand"] is True and len(canopy["devices"]) == 9
    assert canopy["devices"][0] == "canopy-r0c0" and canopy["devices"][-1] == "canopy-r2c2"
    by_id = {d["id"]: d for d in body["devices"]}
    assert not any("vignette" in d or "kind" in d for d in body["devices"])
    placed = [d for g in groups for d in g["devices"]]
    assert sorted(placed) == sorted(by_id), "every device in exactly one group"


def test_ops_js_offers_module_page_and_bench_only_for_strands():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "web" / "ops" / "ops.js").read_text()
    item = js[js.index("function deviceItem") : js.index("function renderDevices")]
    assert "strand\n      ? h(" in item and "Open room page" not in item
    assert "strand ? capsLine(d) : profileLine(d)" in item
    assert "strand && d.head_count_problem" in item


# ---- (b) the 7 am clock check on a Linux host -----------------------------------------------------------------
def test_clock_flag_folder_without_the_flag_means_not_synced(app_client, monkeypatch, tmp_path):
    folder = tmp_path / "timesync"
    folder.mkdir()  # timesyncd's folder is there; no "synchronized" file in it
    monkeypatch.setattr(ops_routes, "TIMESYNC_FLAG", folder / "synchronized")
    monkeypatch.setattr(ops_routes, "ntp_status", REAL_NTP_STATUS)
    body = app_client.get("/api/ops/checklist").json()
    clock = next(i for i in body["items"] if i["id"] == "clock")
    assert clock["status"] == "fail"
    assert "not synced to internet time" in clock["detail"]
    assert "canopy-mac status" in clock["fix"] and "internet" in clock["fix"]
    assert (
        "mount" not in clock["fix"].lower() and "set-ntp" not in clock["fix"] and "chrony" not in clock["fix"]
    )

    (folder / "synchronized").touch()  # timesyncd's flag: synced
    body = app_client.get("/api/ops/checklist").json()
    clock = next(i for i in body["items"] if i["id"] == "clock")
    assert clock["status"] == "pass" and "synced to internet time" in clock["detail"]


def test_clock_that_cant_be_seen_never_asks_for_a_mount(app_client, monkeypatch, tmp_path):
    monkeypatch.setattr(ops_routes, "TIMESYNC_FLAG", tmp_path / "absent" / "synchronized")
    monkeypatch.setattr(ops_routes.shutil, "which", lambda _name: None)
    monkeypatch.setattr(ops_routes, "ntp_status", REAL_NTP_STATUS)
    body = app_client.get("/api/ops/checklist").json()
    clock = next(i for i in body["items"] if i["id"] == "clock")
    assert clock["status"] == "warn"
    assert "mount" not in clock["fix"].lower() and "canopy-mac status" in clock["fix"]
    assert (
        "chrony" not in clock["fix"] and "canopy-status" not in clock["fix"]
    )  # the Ubuntu tooling is gone (D67)
