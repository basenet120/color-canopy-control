"""/ops controls, desired-vs-reported heads, the 7 am checklist and the log tail (conductor/routes/ops.py).

The canopy engine (P1) is built in parallel, so these run against tests/fake_engine.py: `RecordingEngine` proves
each control reaches the engine with source="ops"; `FakeEngine` drives the real executor on the simulator so the
heads and the test mode change see real (simulated) readback.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from types import SimpleNamespace

import pytest
from fake_engine import FakeEngine, RecordingEngine
from partial_canopy import LIVE, partial_show, showing
from show_started import past_startup, wait_until

from conductor import logring, redact
from conductor.engine_api import PlanError
from conductor.gate import Purpose
from conductor.geometry import build_layout, load_canopy_config, panel_order
from conductor.routes import ops as ops_routes

CONTROL_CALLS = [
    ("/api/canopy/reassert", {}, "reassert_all", {"source": "ops"}),
    ("/api/canopy/pause", {"paused": True}, "pause_guest_input", {"paused": True, "source": "ops"}),
    ("/api/canopy/pause", {"paused": False}, "pause_guest_input", {"paused": False, "source": "ops"}),
    ("/api/canopy/safe-look", {}, "safe_look", {"source": "ops"}),
    ("/api/canopy/guest-reset", {}, "clear_guest_overrides", {"source": "ops"}),
    ("/api/canopy/commission", {}, "commission", {"source": "ops"}),
    (
        "/api/canopy/mode",
        {"mode": "golden-hour"},
        "request_mode",
        {"mode_id": "golden-hour", "source": "ops"},
    ),
]


def _services(client):
    return client.app.state.services


def _install(client, engine_cls=FakeEngine):
    services = _services(client)
    engine = engine_cls(services) if engine_cls is FakeEngine else engine_cls()
    services.extras["canopy"] = engine
    return engine


def _wait_ready(client, mode: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    state = {}
    while time.monotonic() < deadline:
        state = client.get("/api/canopy/state").json()
        if state["mode"] == mode and state["phase"] == "ready":
            return state
        time.sleep(0.05)
    raise AssertionError(f"canopy never reached ready on {mode}: {state}")


@pytest.fixture(autouse=True)
def _fast_ntp(monkeypatch):
    async def synced():
        return True, "test: synced"

    monkeypatch.setattr(ops_routes, "ntp_status", synced)


# ---- controls ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("path,body,name,kwargs", CONTROL_CALLS)
def test_every_control_route_calls_the_engine_as_ops(app_client, path, body, name, kwargs):
    engine = _install(app_client, RecordingEngine)
    response = app_client.post(path, json=body)
    assert response.status_code == 200, response.text
    assert engine.calls == [(name, kwargs)]
    payload = response.json()
    assert payload["ok"] is True and "state" in payload


def test_controls_answer_503_plainly_without_the_engine(app_client):
    for path, body, _, _ in CONTROL_CALLS:
        response = app_client.post(path, json=body)
        assert response.status_code == 503
        assert "canopy engine isn't running" in response.json()["detail"]
    assert app_client.get("/api/canopy/state").status_code == 503
    assert app_client.post("/api/ops/checklist/test-mode-change", json={}).status_code == 503


def test_state_lists_the_modes_for_the_picker(app_client):
    _install(app_client, RecordingEngine)
    body = app_client.get("/api/canopy/state").json()
    assert body["mode"] == "lowcountry-calm" and body["guest_input"] is True
    ids = [m["id"] for m in body["modes"]]
    assert (
        ids[0] == "lowcountry-calm" and "golden-hour" in ids and len(ids) == 36
    )  # D77's catalog and D80's options
    assert all(m["label"] for m in body["modes"])
    assert all(m["on_ipad"] is True for m in body["modes"])


def test_the_mode_picker_marks_scenes_off_the_ipad(app_client):
    """A scene off the iPad (D76) is still in the ops picker: ops can choose it; the picker says guests can't."""
    _install(app_client, RecordingEngine)
    _services(app_client).settings.kiosk.hidden_scenes = ["golden-hour"]
    modes = app_client.get("/api/canopy/state").json()["modes"]
    assert {m["id"]: m["on_ipad"] for m in modes} == {m["id"]: m["id"] != "golden-hour" for m in modes}
    assert len(modes) == 36
    script = app_client.get("/ops/ops.js").text
    assert "(off the iPad)" in script and "m.on_ipad" in script


def test_the_ops_page_draws_the_ipad_card_from_the_served_model(app_client):
    page = app_client.get("/ops/").text
    for el in (
        'id="ipad-card"',
        'id="ipad-scenes"',
        'id="ipad-summary"',
        'id="ipad-error"',  # a load that failed (cleared by the next fresh answer); ipad-result is a switch's own
        'id="ipad-notes"',
        'id="ipad-result"',
    ):
        assert el in page, el
    assert "The idle scene is always on the iPad." in page
    assert "six modes" not in page
    script = app_client.get("/ops/ops.js").text
    assert 'from "/ops/ipad-model.mjs"' in script
    assert "ipadRows(" in script and "ipadKey(" in script and "ipadSummary(" in script
    assert "/api/ops/ipad-scenes" in script and 'role: "switch"' in script and '"aria-checked"' in script
    assert "all nine" not in script
    model = app_client.get("/ops/ipad-model.mjs")
    assert model.status_code == 200 and "javascript" in model.headers["content-type"]
    assert "export function ipadRows" in model.text and "export const GRID_MAX = 6;" in model.text
    assert "http://" not in model.text and "https://" not in model.text
    css = app_client.get("/ops/ops.css").text
    assert ".ipad-row" in css and ".is-option" in css and "min-height: 44px" in css


def test_unknown_mode_and_engine_refusals_are_plain(app_client):
    engine = _install(app_client, RecordingEngine)
    response = app_client.post("/api/canopy/mode", json={"mode": "disco"})
    assert response.status_code == 400 and "no mode called 'disco'" in response.json()["detail"]
    assert engine.calls == []
    engine.raise_on["commission"] = PlanError("Slot hashes are unknown until Gate C passes.")
    response = app_client.post("/api/canopy/commission", json={})
    assert (
        response.status_code == 409
        and response.json()["detail"] == "Slot hashes are unknown until Gate C passes."
    )
    engine.raise_on["reassert_all"] = RuntimeError("boom secret-ish internals")
    response = app_client.post("/api/canopy/reassert", json={})
    assert response.status_code == 500
    assert "unexpected error" in response.json()["detail"] and "boom" not in response.json()["detail"]


def test_guest_reset_says_what_it_cleared_and_refuses_an_engine_without_guest_settings(app_client):
    engine = _install(app_client, RecordingEngine)
    nothing = app_client.post("/api/canopy/guest-reset", json={})
    assert nothing.status_code == 200 and nothing.json()["message"].startswith("Nothing to clear")
    off = dataclasses.replace(engine.state(), guest_power=False, guest_brightness=20)
    engine.state = lambda: off  # a guest turned it off; the recording engine "clears" nothing itself
    cleared = app_client.post("/api/canopy/guest-reset", json={})
    assert cleared.status_code == 200
    assert cleared.json()["message"].startswith("Guest settings cleared, back on at the designed brightness")

    class Older:  # an engine from before D60
        def state(self):
            return off

    _services(app_client).extras["canopy"] = Older()
    refused = app_client.post("/api/canopy/guest-reset", json={})
    assert refused.status_code == 409 and "no guest brightness" in refused.json()["detail"]


def test_posts_must_be_same_site_json(app_client):
    engine = _install(app_client, RecordingEngine)
    response = app_client.post(
        "/api/canopy/pause", json={"paused": True}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    response = app_client.post(
        "/api/canopy/safe-look", content=b"x=1", headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    assert response.status_code == 415
    assert engine.calls == []
    response = app_client.post("/api/canopy/safe-look", json={}, headers={"Origin": "http://testserver"})
    assert response.status_code == 200


def test_controls_publish_on_the_ops_topic(app_client, monkeypatch):
    _install(app_client, RecordingEngine)
    published = []
    monkeypatch.setattr(
        _services(app_client).hub, "publish", lambda topic, data: published.append((topic, data))
    )
    app_client.post("/api/canopy/pause", json={"paused": True})
    assert published and published[-1][0] == "ops" and published[-1][1]["action"] == "pause"


# ---- heads ---------------------------------------------------------------------------------------------------
def test_heads_without_the_engine_is_just_the_layout(app_client):
    body = app_client.get("/api/canopy/heads").json()
    assert body["engine"] is False and body["engine_message"]
    assert body["counts"]["heads"] == 270 and len(body["heads"]) == 270
    assert all(h["desired"] is None and h["reported"] is None and h["match"] is None for h in body["heads"])
    layout = {(h.module, h.index): (h.x, h.y) for h in build_layout(load_canopy_config())}
    assert {(h["module"], h["index"]): (h["x"], h["y"]) for h in body["heads"]} == layout
    assert len(body["modules"]) == 9 and body["simulated"] is True
    assert "SIMULATED" in body["readback_note"]


def test_heads_show_desired_from_the_plan_and_reported_from_readback(app_client):
    engine = _install(app_client)
    assert app_client.post("/api/canopy/mode", json={"mode": "golden-hour"}).status_code == 200
    _wait_ready(app_client, "golden-hour")
    body = app_client.get("/api/canopy/heads").json()
    assert body["engine"] is True and body["mode"] == "golden-hour" and body["tier"] == "modules"
    from conductor.looks import load_modes

    # the T3 sentence names the design's layout (D63: a sun at one edge, "… laid out from one edge: gold and peach at
    # the middle of one short side, …"), read from the live mode so a Design Lab export can't strand a literal here
    assert body["feature"] == load_modes().mode("golden-hour").techniques.modules.feature
    assert len(body["heads"]) == 270 and body["counts"]["match"] == 270
    plan = engine.current_plan()
    by_module = {m["id"]: m for m in body["modules"]}
    for h in body["heads"]:
        module = by_module[h["module"]]
        assert h["device_id"] == module["device_id"]
        assert h["desired"] == plan.modules[h["device_id"]].preview[h["index"]]
        assert h["reported"] is not None and h["match"] is True
    for m in body["modules"]:
        assert m["status"] == "ready" and m["desired_source"] == "plan" and m["reported_at"]
        assert m["counts"] == {"match": 30, "mismatch": 0, "unknown": 0}
    # the sun's side gold-peach, the far side rose-lavender (the design at each module), so colours come per module
    left = next(h for h in body["heads"] if h["module"] == "r0c0")["reported"]
    right = next(h for h in body["heads"] if h["module"] == "r0c2")["reported"]
    assert left["g"] > right["g"] and left["b"] < right["b"]


def test_heads_flag_a_module_whose_readback_differs(app_client):
    _install(app_client)
    app_client.post("/api/canopy/mode", json={"mode": "lowcountry-calm"})
    _wait_ready(app_client, "lowcountry-calm")
    services = _services(app_client)
    # the cloud now reports something else for module 5 (e.g. an app edit): readback no longer matches
    from conductor import rows as R
    from conductor.drivers.base import Outcome, ReadResult, StateRow

    other = R.full_state_vector(R.Look("rgb", rgb=(0, 0, 255), brightness=100))
    rows = [StateRow(r.function_class, r.function_instance, r.value) for r in other]
    services.tracker.note_readback(
        "sim-canopy-r1c1",
        ReadResult("sim-canopy-r1c1", Outcome.OK, rows=rows, http_status=200, simulated=True),
    )
    body = app_client.get("/api/canopy/heads").json()
    module = next(m for m in body["modules"] if m["id"] == "r1c1")
    assert module["counts"]["mismatch"] == 30 and module["status"] == "needs_attention"
    head = next(h for h in body["heads"] if h["module"] == "r1c1")
    assert head["match"] is False and head["reported"]["b"] > 200 and head["desired"]["b"] < 200
    assert body["counts"]["mismatch"] == 30 and body["counts"]["match"] == 240


def test_heads_in_a_stepped_run_compare_with_the_step_sent_not_the_first(app_client, monkeypatch):
    # Oct 21 (venue rehearsal): the plan's target is a stepped run's first step, so every later step read as most heads
    # differing although each write landed. While a run steps, the step actually sent (the tracker's) is compared
    engine = _install(app_client)
    app_client.post("/api/canopy/mode", json={"mode": "lowcountry-calm"})
    _wait_ready(app_client, "lowcountry-calm")
    plan = engine.current_plan()
    first_step = {"r": 0, "g": 0, "b": 255}  # unlike anything sent since
    for target in plan.modules.values():
        target.preview = [first_step] * len(target.preview)
    monkeypatch.setattr(engine, "current_plan", lambda: plan)
    real_state = ops_routes._state_json
    monkeypatch.setattr(
        ops_routes, "_state_json", lambda e: {**(real_state(e) or {}), "program": {"step": 5}}
    )
    body = app_client.get("/api/canopy/heads").json()
    assert body["counts"]["match"] == 270 and body["counts"]["mismatch"] == 0
    assert all(m["desired_source"] == "tracker" for m in body["modules"])
    monkeypatch.setattr(ops_routes, "_state_json", real_state)  # no run: the plan's look, as before
    assert app_client.get("/api/canopy/heads").json()["counts"]["mismatch"] == 270


def test_heads_with_a_write_in_flight_are_checking_not_mismatched(app_client, monkeypatch):
    _install(app_client)
    app_client.post("/api/canopy/mode", json={"mode": "lowcountry-calm"})
    _wait_ready(app_client, "lowcountry-calm")
    services = _services(app_client)
    real = services.tracker.snapshot

    def in_flight(address):
        snap = real(address)
        if address == "sim-canopy-r1c1":
            snap = {**snap, "status": "updating"}  # the next step sent, its readback not back yet
        return snap

    monkeypatch.setattr(services.tracker, "snapshot", in_flight)
    real_state = ops_routes._state_json
    monkeypatch.setattr(
        ops_routes, "_state_json", lambda e: {**(real_state(e) or {}), "program": {"step": 5}}
    )
    body = app_client.get("/api/canopy/heads").json()
    module = next(m for m in body["modules"] if m["id"] == "r1c1")
    assert module["counts"] == {"match": 0, "mismatch": 0, "unknown": 30}
    assert body["counts"]["match"] == 240 and body["counts"]["mismatch"] == 0


def test_heads_honour_each_modules_calibration(app_client):
    services = _services(app_client)
    services.update_registry("canopy-r0c0", {"cable_path": "serpentine_rows", "start_corner": "top-right"})
    body = app_client.get("/api/canopy/heads").json()
    cfg = load_canopy_config()
    order = panel_order(cfg, "serpentine_rows", "top-right")
    r0c0 = sorted((h for h in body["heads"] if h["module"] == "r0c0"), key=lambda h: h["index"])
    assert [(h["x"], h["y"]) for h in r0c0] == [
        (float(x), float(y)) for x, y in order
    ]  # orientation A, origin 0,0
    assert next(m for m in body["modules"] if m["id"] == "r0c0")["calibrated"] is True
    # a B module (r0c1 on scheme p14) is rotated 180° and offset by one module width
    default = {(h.module, h.index): (h.x, h.y) for h in build_layout(cfg)}
    r0c1 = {(h["module"], h["index"]): (h["x"], h["y"]) for h in body["heads"] if h["module"] == "r0c1"}
    assert r0c1 == {k: v for k, v in default.items() if k[0] == "r0c1"}


def test_calibration_hub_lists_the_nine_modules(app_client):
    services = _services(app_client)
    services.update_registry("canopy-r2c2", {"start_corner": "bottom-left"})
    body = app_client.get("/api/ops/calibration").json()
    assert body["total"] == 9 and body["calibrated"] == 1
    m = next(x for x in body["modules"] if x["device_id"] == "canopy-r2c2")
    assert m["start_corner"] == "bottom-left" and m["calibrated"] is True
    assert m["link"] == "/ops/module/?device=canopy-r2c2&tab=calibrate"
    b = next(x for x in body["modules"] if x["device_id"] == "canopy-r0c1")
    assert b["orientation"] == "B" and b["calibrated"] is False and b["hops_fit"] is True


# ---- checklist -----------------------------------------------------------------------------------------------
EXPECTED_ITEMS = {"clock", "token", "modules", "heads", "slots", "budget", "test_mode_change", "guest_input"}


def _items(body):
    return {i["id"]: i for i in body["items"]}


def test_checklist_items_have_statuses_and_fixes(app_client):
    _install(app_client)
    now_ms = time.time() * 1000
    body = app_client.get(f"/api/ops/checklist?client_ms={now_ms}").json()
    items = _items(body)
    assert set(items) == EXPECTED_ITEMS
    for item in body["items"]:
        assert item["status"] in ("pass", "warn", "fail") and item["title"] and item["detail"]
        if item["status"] != "pass":
            assert item["fix"], item
    assert items["clock"]["status"] == "pass"
    assert items["token"]["status"] == "pass" and "Simulator" in items["token"]["detail"]
    assert items["modules"]["status"] == "warn"  # nothing shown yet → no readback
    assert items["heads"]["status"] == "pass" and "look up" in items["heads"]["note"]
    assert items["slots"]["status"] == "warn" and "Commission slots" in items["slots"]["fix"]
    assert items["budget"]["status"] == "pass"
    assert items["test_mode_change"]["status"] == "warn"
    assert items["guest_input"]["status"] == "pass"
    assert body["overall"] == "warn" and body["simulated"] is True


def test_checklist_clock_compares_with_the_browser(app_client, monkeypatch):
    far = time.time() * 1000 - 120_000
    body = app_client.get(f"/api/ops/checklist?client_ms={far}").json()
    clock = _items(body)["clock"]
    assert clock["status"] == "warn" and "120" in clock["detail"] and "automatic time" in clock["fix"]

    async def unknown():
        return None, "the box's NTP status isn't visible to the Conductor"

    monkeypatch.setattr(ops_routes, "ntp_status", unknown)
    clock = _items(app_client.get(f"/api/ops/checklist?client_ms={time.time() * 1000}").json())["clock"]
    assert clock["status"] == "warn" and "canopy-mac status" in clock["fix"]
    clock = _items(app_client.get(f"/api/ops/checklist?client_ms={far}").json())["clock"]
    assert clock["status"] == "fail"

    async def unsynced():
        return False, "timedatectl says NTPSynchronized=no"

    monkeypatch.setattr(ops_routes, "ntp_status", unsynced)
    clock = _items(app_client.get("/api/ops/checklist").json())["clock"]
    assert (
        clock["status"] == "fail"
        and "Set time automatically" in clock["fix"]
        and "chrony" not in clock["fix"]
    )
    assert "not synced to internet time" in clock["detail"]


def test_checklist_without_the_engine_fails_guest_input(app_client):
    items = _items(app_client.get("/api/ops/checklist").json())
    assert items["guest_input"]["status"] == "fail" and "engine" in items["guest_input"]["detail"]


def test_checklist_guest_input_paused_warns(app_client):
    _install(app_client, RecordingEngine)
    app_client.post("/api/canopy/pause", json={"paused": True})
    item = _items(app_client.get("/api/ops/checklist").json())["guest_input"]
    assert item["status"] == "warn" and "Resume" in item["fix"]


def test_checklist_head_count_problem_fails(app_client, monkeypatch):
    app_client.get("/api/devices")  # read (and cache) every profile
    services = _services(app_client)
    record = services.extras["capabilities"]["canopy-r1c0"]
    record["caps"].framebuffer_lengths = {"custom-1": 12, "custom-2": 12, "custom-3": 12}
    item = _items(app_client.get("/api/ops/checklist").json())["heads"]
    assert item["status"] == "fail" and "12 heads" in item["detail"] and "power-cycle" in item["fix"]


def test_checklist_slot_hash_drift_fails(app_client):
    _install(app_client)
    app_client.post("/api/canopy/mode", json={"mode": "lowcountry-calm"})
    _wait_ready(app_client, "lowcountry-calm")
    services = _services(app_client)
    snap = services.tracker.snapshot("sim-canopy-r0c0")
    frames = {
        r["functionInstance"]: r["value"]
        for r in snap["reported"]
        if r["functionClass"] == "color-sequence-v2"
    }
    assert frames, "the simulator's readback carries the slot objects"
    from conductor.rows import slot_hash

    good = {slot: slot_hash(v) for slot, v in frames.items()}
    for entry in services.registry.canopy_modules():
        services.update_registry(entry.id, {"slot_hashes": good})
    # every module's slots read back identically on a fresh simulator, so matching hashes pass everywhere
    item = _items(app_client.get("/api/ops/checklist").json())["slots"]
    assert item["status"] == "pass", item
    services.update_registry("canopy-r2c1", {"slot_hashes": {**good, "custom-1": "0" * 16}})
    item = _items(app_client.get("/api/ops/checklist").json())["slots"]
    assert item["status"] == "fail" and "Module 8 custom-1" in item["detail"]


def test_checklist_rate_budget_counts_engine_writes(app_client):
    _install(app_client)
    app_client.post("/api/canopy/mode", json={"mode": "garden-gathering"})
    _wait_ready(app_client, "garden-gathering")
    item = _items(app_client.get("/api/ops/checklist").json())["budget"]
    assert item["status"] == "pass" and "1 of 60 writes" in item["detail"]


def test_test_mode_change_times_all_nine_to_ready(app_client):
    engine = _install(app_client)
    response = app_client.post("/api/ops/checklist/test-mode-change", json={})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ok"] is True and result["mode"] == "lowcountry-calm" and result["simulated"] is True
    assert len(result["modules"]) == 9 and all(m["ready_after_s"] is not None for m in result["modules"])
    assert result["elapsed_s"] <= 10 and "All 9 modules reached Ready" in result["message"]
    assert engine.calls[-1] == ("request_mode", {"mode_id": "lowcountry-calm", "source": "ops"})
    item = _items(app_client.get("/api/ops/checklist").json())["test_mode_change"]
    assert item["status"] == "pass" and item["detail"] == result["message"]
    items = _items(app_client.get("/api/ops/checklist").json())
    assert items["modules"]["status"] == "pass" and items["modules"]["title"] == "9/9 modules online"


def test_test_mode_change_restores_the_previous_mode(app_client):
    engine = _install(app_client)
    app_client.post("/api/canopy/mode", json={"mode": "lowcountry-calm"})
    _wait_ready(app_client, "lowcountry-calm")
    result = app_client.post("/api/ops/checklist/test-mode-change", json={}).json()
    assert result["mode"] == "goodnight-glow" and result["ok"] is True
    assert result["restored_to"] == "lowcountry-calm"
    # D75: the change back puts the look back still (its own source never starts a run); the test's choice is ops
    assert engine.calls[-2] == ("request_mode", {"mode_id": "goodnight-glow", "source": "ops"})
    assert engine.calls[-1] == ("request_mode", {"mode_id": "lowcountry-calm", "source": "ops-restore"})


def test_test_mode_change_reports_modules_that_miss_the_window(app_client, monkeypatch):
    engine = _install(app_client)
    services = _services(app_client)
    lease = services.locks.acquire("sim-canopy-r1c2", "module page · test")
    monkeypatch.setattr(ops_routes, "TEST_WINDOW_S", 2.0)
    result = app_client.post("/api/ops/checklist/test-mode-change", json={"mode": "golden-hour"}).json()
    services.locks.release("sim-canopy-r1c2", lease.token)
    assert result["ok"] is False and "Module 6 didn't reach Ready" in result["message"]
    late = [m for m in result["modules"] if m["ready_after_s"] is None]
    assert [m["device_id"] for m in late] == ["canopy-r1c2"]
    assert engine.mode == "golden-hour"
    item = _items(app_client.get("/api/ops/checklist").json())["test_mode_change"]
    assert item["status"] == "fail" and item["fix"]


# ---- logs ----------------------------------------------------------------------------------------------------
@pytest.fixture
def ring():
    logring.uninstall()
    handler = logring.install(capacity=50)
    yield handler
    logring.uninstall()


def test_logs_endpoint_returns_redacted_lines(app_client, ring):
    secret = "sentinel-refresh-token-9f8e7d6c5b"
    redact.add_secret(secret)
    log = logging.getLogger("conductor.test_ops")
    log.info("canopy engine started")
    log.warning("token refresh used %s for the canopy account", secret)
    logging.getLogger("conductor.test_ops").handle(
        logging.makeLogRecord(
            {
                "name": "conductor.test_ops",
                "levelno": logging.ERROR,
                "levelname": "ERROR",
                "msg": f"raw record with {secret}",
            }
        )
    )
    response = app_client.get("/api/ops/logs")
    assert response.status_code == 200
    assert secret not in response.text
    body = response.json()
    messages = [r["message"] for r in body["records"]]
    assert "canopy engine started" in messages
    assert any("token refresh used ***" in m for m in messages)
    assert any("raw record with ***" in m for m in messages)
    last = body["last_seq"]
    log.info("after the first poll")
    newer = app_client.get(f"/api/ops/logs?since={last}").json()["records"]
    assert [r["message"] for r in newer] == ["after the first poll"]


def test_logs_endpoint_without_the_ring_is_503(app_client):
    logring.uninstall()
    response = app_client.get("/api/ops/logs")
    assert response.status_code == 503 and "log tail" in response.json()["detail"]


# ---- who may call these --------------------------------------------------------------------------------------
OPS_PATHS = [
    ("GET", "/api/canopy/state"),
    ("GET", "/api/canopy/heads"),
    ("GET", "/api/ops/checklist"),
    ("GET", "/api/ops/logs"),
    ("GET", "/api/ops/calibration"),
    ("POST", "/api/canopy/pause"),
    ("POST", "/api/canopy/mode"),
    ("POST", "/api/ops/checklist/test-mode-change"),
    ("GET", "/api/canopy/stepped-rehearsal"),
    ("POST", "/api/canopy/stepped-rehearsal"),
    ("POST", "/api/canopy/stepped-rehearsal/stop"),
]


@pytest.mark.parametrize("role,grants", [("tester", ["canopy-r0c0"]), ("kiosk", [])])
def test_enrolled_devices_get_401(app_client_auth, role, grants):
    ops = ("crew", "canopy")
    services = _services(app_client_auth)
    engine = RecordingEngine()
    services.extras["canopy"] = engine
    link = app_client_auth.post(
        "/api/roles/enrollments", json={"role": role, "name": "t", "grants": grants}, auth=ops
    )
    assert link.status_code == 201
    redeemed = app_client_auth.post(link.json()["path"], follow_redirects=False)
    assert redeemed.status_code == 303 and "cc_session" in app_client_auth.cookies
    for method, path in OPS_PATHS:
        body = (
            {"paused": True}
            if path.endswith("pause")
            else {"mode": "golden-hour"}
            if path.endswith("mode")
            else {"mode": "golden-hour", "minutes": 2, "confirm": "x"}
            if path.endswith("rehearsal")
            else {}
        )
        response = app_client_auth.request(method, path, json=body if method == "POST" else None)
        assert response.status_code == 401, (role, path, response.status_code)
    assert engine.calls == []
    app_client_auth.cookies.clear()
    assert app_client_auth.get("/api/canopy/heads").status_code == 401
    assert app_client_auth.get("/api/canopy/heads", auth=ops).status_code == 200


def test_checklist_modules_fails_while_a_module_is_held_on_the_module_page(app_client):
    _install(app_client)
    app_client.post("/api/canopy/mode", json={"mode": "lowcountry-calm"})
    _wait_ready(app_client, "lowcountry-calm")
    services = _services(app_client)
    lease = services.locks.acquire("sim-canopy-r0c2", "VWBD shop phone")
    item = _items(app_client.get("/api/ops/checklist").json())["modules"]
    services.locks.release("sim-canopy-r0c2", lease.token)
    assert (
        item["status"] == "fail" and "Module 3 (open on the module page by VWBD shop phone" in item["detail"]
    )
    assert item["title"] == "8/9 modules online"


def test_ops_pages_are_served_and_vendored(app_client):
    for path, needle in (
        ("/ops/", "7 am checklist"),
        ("/ops/calibrate/", "Check each module's wire order"),
        ("/visualizer/", "The Color Canopy"),
    ):
        page = app_client.get(path)
        assert page.status_code == 200 and needle in page.text, path
    for asset in (
        "/ops/ops.js",
        "/ops/counts-model.mjs",
        "/ops/ops.css",
        "/ops/calibrate/calibrate.js",
        "/visualizer/",
    ):
        text = app_client.get(asset).text
        assert "http://" not in text and "https://" not in text, f"{asset} references an external URL"
    visualizer = app_client.get("/visualizer/").text
    assert 'connectLive({\n  topics: ["canopy"]' in visualizer and "/api/canopy/heads" in visualizer
    assert 'id="schemes"' in visualizer and 'id="wire"' in visualizer and "pitch_violations" in visualizer


# ---- D62 follow-up: a partial canopy, every /ops count says registered of nine -------------------------------------
# The real engine with only Modules 1-2 registered (tests/partial_canopy.py), as on the Mac show box before install.
def _partial_settled(c, timeout: float = 30.0) -> dict:
    """The partial canopy once its two registered modules read back Ready (the other seven are dark)."""
    showing(c)
    deadline = time.monotonic() + timeout
    state = {}
    while time.monotonic() < deadline:
        state = c.get("/api/canopy/state").json()
        if state["look_phase"] == "ready":
            heads = c.get("/api/canopy/heads").json()
            if heads["counts"]["unknown"] == 0 and heads["counts"]["mismatch"] == 0:
                return state
        time.sleep(0.05)
    raise AssertionError(f"the partial canopy never settled: {state}")


def test_partial_canopy_sent_line_counts_what_the_plan_drives_and_names_the_held_and_left_out(
    monkeypatch, tmp_path
):
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        _partial_settled(c)
        services = _services(c)
        lease = services.locks.acquire("sim-canopy-r0c1", "VWBD shop phone")
        try:
            r = c.post("/api/canopy/mode", json={"mode": "golden-hour"})
        finally:
            services.locks.release("sim-canopy-r0c1", lease.token)
    assert r.status_code == 200, r.text
    message = r.json()["message"]
    assert (
        "on 1 of 9 modules; 1 held on a test page are left alone until they're released; 7 have no "
        in message
    )
    assert "no Hubspace device registered, so they were left out (register them on /ops/accounts)." in message


def test_partial_canopy_test_mode_change_times_only_the_registered_modules_and_still_fails(
    monkeypatch, tmp_path
):
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        _partial_settled(c)
        item = _items(c.get("/api/ops/checklist").json())["test_mode_change"]
        assert "times every module with a Hubspace device registered (2 of 9 now) to Ready" in item["fix"]
        result = c.post("/api/ops/checklist/test-mode-change", json={}).json()
        after = _items(c.get("/api/ops/checklist").json())["test_mode_change"]
    # broke out as soon as both registered modules were Ready, rather than waiting the window out on the other seven
    assert result["elapsed_s"] < ops_routes.TEST_WINDOW_S / 2, result
    assert result["ok"] is False
    timed = {m["device_id"]: m for m in result["modules"] if m["registered"]}
    assert sorted(timed) == sorted(LIVE) and all(m["ready_after_s"] is not None for m in timed.values())
    others = [m for m in result["modules"] if not m["registered"]]
    assert len(others) == 7 and all(m["ready_after_s"] is None for m in others)
    message = result["message"]
    assert message.startswith("Module 1 and Module 2 (the 2 registered modules) reached Ready on "), message
    assert "didn't reach Ready" not in message
    assert (
        "Not timed: Module 3, Module 4, Module 5, Module 6, Module 7, Module 8 and Module 9 have no Hubspace device "
        "registered (register them on /ops/accounts)." in message
    )
    assert "/ops/accounts" in result["fix"]
    assert after["status"] == "fail" and after["detail"] == message and after["fix"] == result["fix"]


def test_partial_canopy_test_mode_change_names_only_the_registered_module_that_missed_the_window(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(ops_routes, "TEST_WINDOW_S", 3.0)  # Module 2 is held, so it never reaches Ready
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        _partial_settled(c)
        services = _services(c)
        lease = services.locks.acquire("sim-canopy-r0c1", "VWBD shop phone")
        try:
            result = c.post("/api/ops/checklist/test-mode-change", json={}).json()
        finally:
            services.locks.release("sim-canopy-r0c1", lease.token)
    assert result["ok"] is False
    message = result["message"]
    assert message.startswith("Module 2 didn't reach Ready within 3 s of changing to "), message
    assert (
        " Not timed: Module 3, Module 4, Module 5, Module 6, Module 7, Module 8 and Module 9 have no Hubspace "
        "device registered (register them on /ops/accounts)." in message
    )
    assert result["fix"].startswith("Open the modules that didn't reach Ready"), result["fix"]
    assert "/ops/accounts" in result["fix"]


def test_partial_canopy_checklist_counts_registered_of_nine(monkeypatch, tmp_path):
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        _partial_settled(c)
        items = _items(c.get("/api/ops/checklist").json())
    modules = items["modules"]
    assert modules["title"] == "2/9 modules online" and modules["status"] == "fail", modules
    assert "No Hubspace device registered: Module 3, Module 4" in modules["detail"]
    assert modules["fix"] == "Register a device for each of those positions on /ops/accounts."
    assert "Module 3 (" not in modules["detail"]  # its own clause, not a needs-attention line
    heads = items["heads"]
    assert heads["status"] == "warn", heads
    text = heads["detail"] + heads["fix"] + heads["note"]
    assert "270" not in text and "Couldn't read the profile" not in text
    assert "All 2 registered modules report their expected heads" in heads["detail"]
    assert (
        "the 60 heads of the 2 registered modules should light; the other 7 positions stay dark"
        in heads["note"]
    )
    assert "/ops/accounts" in heads["fix"]
    slots = items["slots"]
    assert slots["detail"].startswith("No recorded slot hash for Module 1 and Module 2 ("), slots
    assert (
        "Module 3, Module 4, Module 5, Module 6, Module 7, Module 8 and Module 9 have no Hubspace device "
        in (slots["detail"])
    )
    assert slots["fix"].startswith("Press Commission slots.") and "/ops/accounts" in slots["fix"]
    assert "T1) shows only once all nine have their slots recorded" in slots["fix"]


def test_partial_canopy_slots_never_pass_on_hashes_no_device_can_read_back(monkeypatch, tmp_path):
    # the full canopy commissioned (hashes on all nine), then seven registrations lost: the hashes stay on the
    # registry entries, but nothing reads them back, so the item warns and names those positions
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        _partial_settled(c)
        services = _services(c)
        snap = services.tracker.snapshot("sim-canopy-r0c0")
        from conductor.rows import slot_hash

        good = {
            r["functionInstance"]: slot_hash(r["value"])
            for r in snap["reported"]
            if r["functionClass"] == "color-sequence-v2"
        }
        assert good, "the simulator's readback carries the slot objects"
        for entry in services.registry.canopy_modules():
            services.update_registry(entry.id, {"slot_hashes": good})
        slots = _items(c.get("/api/ops/checklist").json())["slots"]
    assert slots["status"] == "warn", slots
    assert "reads back with its recorded hash" not in slots["detail"]
    assert slots["detail"] == (
        "Module 3, Module 4, Module 5, Module 6, Module 7, Module 8 and Module 9 have slot hashes recorded but no "
        "Hubspace device registered, so they can't be read back."
    )
    assert "Commission slots" not in slots["fix"] and "/ops/accounts" in slots["fix"]


def test_partial_canopy_modules_item_fails_and_names_the_unregistered_in_every_branch(monkeypatch, tmp_path):
    # Module 2 waiting on readback (just after a reboot), offline, then held on a test page: each branch still fails
    # and names the seven positions with no device, and its fix ends by sending them to /ops/accounts
    gap = " No Hubspace device registered: Module 3, Module 4, Module 5, Module 6, Module 7, Module 8 and Module 9."
    then = " Then register a device on /ops/accounts for each position that has none."
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        _partial_settled(c)
        services = _services(c)
        real = services.tracker.snapshot
        override: dict = {}

        def snapshot(address):
            snap = real(address)
            return {**snap, **override} if address == "sim-canopy-r0c1" else snap

        monkeypatch.setattr(services.tracker, "snapshot", snapshot)
        override.update(reported_at=None, status="updating")
        waiting = _items(c.get("/api/ops/checklist").json())["modules"]
        override.clear()
        override.update(available=False)
        offline = _items(c.get("/api/ops/checklist").json())["modules"]
        override.clear()
        lease = services.locks.acquire("sim-canopy-r0c1", "VWBD shop phone")
        try:
            held = _items(c.get("/api/ops/checklist").json())["modules"]
        finally:
            services.locks.release("sim-canopy-r0c1", lease.token)
    assert waiting["status"] == "fail" and waiting["title"] == "1/9 modules online", waiting
    assert waiting["detail"].endswith(f"No confirmed readback yet: Module 2.{gap}"), waiting
    assert waiting["fix"].startswith("Press Re-assert all") and waiting["fix"].endswith(then), waiting
    assert offline["status"] == "fail" and offline["detail"].endswith(f"Offline: Module 2.{gap}"), offline
    assert offline["fix"].endswith(then), offline
    assert held["status"] == "fail" and "Needs attention: Module 2 (open on the module page" in held["detail"]
    assert held["detail"].endswith(gap) and held["fix"].endswith(then), held


def test_partial_canopy_heads_count_the_dark_positions_apart(monkeypatch, tmp_path):
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        _partial_settled(c)
        body = c.get("/api/canopy/heads").json()
    counts = body["counts"]
    assert counts["heads"] == 270 and counts["no_device"] == 210 and counts["unknown"] == 0
    assert counts["match"] == 60 and counts["modules"] == {"total": 9, "registered": 2}
    registered = {m["device_id"]: m["registered"] for m in body["modules"]}
    assert [d for d, r in registered.items() if r] == list(LIVE)
    dark = next(m for m in body["modules"] if m["device_id"] == "canopy-r2c2")
    assert dark["counts"] == {"match": 0, "mismatch": 0, "unknown": 30}  # per-module counts unchanged


def test_nothing_registered_says_register_on_ops_accounts_not_restart(monkeypatch, tmp_path):
    with partial_show(monkeypatch, tmp_path, ()) as c:
        engine = c.app.state.services.extras["canopy"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and engine.state().phase != "resting":
            time.sleep(0.05)
        item = _items(c.get("/api/ops/checklist").json())["guest_input"]
        result = c.post("/api/ops/checklist/test-mode-change", json={"mode": "golden-hour"}).json()
    assert item["status"] == "fail", item
    assert "/ops/accounts" in item["fix"] and "restart the Conductor" not in item["fix"]
    assert "comes out of rest on its own" in item["fix"]
    assert result["ok"] is False and "/ops/accounts" in result["fix"]
    assert "restart the Conductor" not in result["fix"]
    assert not any(m["registered"] for m in result["modules"])


def test_full_canopy_test_mode_change_marks_every_module_registered(app_client):
    _install(app_client)
    item = _items(app_client.get("/api/ops/checklist").json())["test_mode_change"]
    assert (
        item["fix"]
        == "Press Run test mode change: it changes the mode once and times all nine modules to Ready."
    )
    result = app_client.post("/api/ops/checklist/test-mode-change", json={}).json()
    assert result["ok"] is True and all(m["registered"] for m in result["modules"]) and "fix" not in result
    heads = app_client.get("/api/canopy/heads").json()["counts"]
    assert heads["no_device"] == 0 and heads["modules"] == {"total": 9, "registered": 9}


def test_ops_page_counts_positions_not_nine(app_client):
    page = app_client.get("/ops/").text
    start = page.index('<dialog id="commission-dialog"')
    dialog = page[start : page.index("</dialog>", start)]
    assert "nine" not in dialog and 'id="commission-scope"' in dialog
    assert "with a Hubspace device registered" in dialog
    assert 'aria-label="The nine canopy positions"' in page
    script = app_client.get("/ops/ops.js").text
    assert "all nine" not in script
    assert "m.registered !== false" in script  # an unregistered position is never "Not ready"
    # an unregistered position's grid cell and its issue line both link to /ops/accounts, not a module page
    assert 'const href = unregistered\n        ? "/ops/accounts/"' in script
    assert 'h("a", { href: "/ops/accounts/" }, "Register a device")' in script
    # the counts' wording is the pure module tests/ops-counts.test.mjs pins, served as JavaScript
    assert 'from "/ops/counts-model.mjs"' in script
    assert "headsCounts(headsData.counts)" in script and "commissionScope(p)" in script
    model = app_client.get("/ops/counts-model.mjs")
    assert model.status_code == 200 and "javascript" in model.headers["content-type"]
    assert "export function headsCounts" in model.text


class _PlanEngine:
    """Just enough engine for _sent_line: a plan, and optionally an outage."""

    def __init__(self, plan, outage=None):
        self._plan, self.outage = plan, outage

    def current_plan(self):
        return self._plan


def _sent(monkeypatch, *, registered, unregistered, driven, skipped=(), locked=(), outage=None, plan=True):
    from types import SimpleNamespace

    monkeypatch.setattr(
        ops_routes, "split_canopy_positions", lambda services: (list(registered), list(unregistered))
    )
    the_plan = (
        SimpleNamespace(modules=dict.fromkeys(driven), skipped=dict.fromkeys(skipped, "left out"))
        if plan
        else None
    )
    modules = {d: {"locked": d in locked} for d in [*registered, *unregistered]}
    state = SimpleNamespace(label="Golden Hour", mode="golden-hour", tier="modules", modules=modules)
    return ops_routes._sent_line(None, _PlanEngine(the_plan, outage), state, "Changing to")


def test_sent_line_counts_from_the_plan_and_registry(monkeypatch):
    nine = [f"m{i}" for i in range(9)]
    full = _sent(monkeypatch, registered=nine, unregistered=[], driven=nine, locked=["m2"])
    assert full == (
        "Changing to: Golden Hour (T3, one colour per module) on 8 of 9 modules; 1 held on a test page are left alone "
        "until they're released."
    )
    one = _sent(monkeypatch, registered=nine[:8], unregistered=nine[8:], driven=nine[:8])
    assert one.endswith(
        "on 8 of 9 modules; 1 has no Hubspace device registered, so it was left out (register it on /ops/accounts)."
    )
    # a registered module whose profile hasn't been read is left out of the plan (D36) and isn't counted as sent
    unread = _sent(monkeypatch, registered=nine[:2], unregistered=nine[2:], driven=["m0"], skipped=nine[1:])
    assert "on 1 of 9 modules; 7 have no Hubspace device registered" in unread
    assert unread.endswith("; 1 whose profile hasn't been read was left out.")
    # an engine with no plan to read: the modules its state reports (the fakes)
    assert "on 9 of 9 modules" in _sent(monkeypatch, registered=nine, unregistered=[], driven=[], plan=False)


def test_sent_line_during_an_outage_names_the_registered_modules(monkeypatch):
    nine = [f"m{i}" for i in range(9)]
    partial = _sent(monkeypatch, registered=nine[:2], unregistered=nine[2:], driven=nine[:2], outage=1.0)
    assert partial.endswith(
        "so it goes to the 2 modules with a device registered as soon as Hubspace answers again."
    )
    full = _sent(monkeypatch, registered=nine, unregistered=[], driven=nine, outage=1.0)
    assert full.endswith("so it goes to every module as soon as Hubspace answers again.")


# ---- the stepped card (Tier.STEPPED, D65; web/ops/stepped-model.mjs words it) --------------------------------
def _runner(services, **kw):
    """Just enough ProgramRunner for the gauge: a run of all the canopy modules, at step 2 of 15, 6 s steps."""
    modules = dict.fromkeys(e.id for e in services.registry.canopy_modules())
    return SimpleNamespace(
        running=True,
        plan=SimpleNamespace(mode_id="golden-hour", modules=modules),
        index=2,
        count=15,
        step_s=6.0,
        run_len_s=15 * 6.0,  # count × step_s (an even pace)
        program=SimpleNamespace(step_s=6.0),
        t0_wall=1000.0,
        **kw,
    )


def test_the_stepped_card_shows_the_accounts_writes_a_minute_and_the_cap(app_client):
    services = _services(app_client)
    engine = _install(app_client)
    app_client.post("/api/canopy/mode", json={"mode": "garden-gathering"})
    _wait_ready(app_client, "garden-gathering")
    assert (
        app_client.get("/api/canopy/state").json()["stepped"] is None
    )  # no stepped run, no cool-off: no card
    engine.runner, engine.stepping, engine.stats = _runner(services), True, {"governor_trips": 0}
    gauge = app_client.get("/api/canopy/state").json()["stepped"]
    entry = services.registry.canopy_modules()[0]
    target = services.target(entry.id, Purpose.ENGINE)
    account = target.driver.account_of(target.address)
    written = target.executor.budget()["accounts"][account]["writes_60s"]
    assert written >= 9  # the mode change: one write per module, on the canopy's account
    assert gauge == {
        "running": True,
        "moving": True,
        "cap": 90.0,
        "live_enabled": True,
        "cooloff": [],
        "governor_trips": 0,
        "mode": "golden-hour",
        "step": 2,
        "steps": 15,
        "step_s": 6.0,
        "design_step_s": 6.0,
        "holds_at": 1090.0,
        "account": account,
        "writes_60s": written,
    }
    # held (paused, resting, off): the run's clock goes on, nothing is sent
    engine.stepping = False
    assert app_client.get("/api/canopy/state").json()["stepped"]["moving"] is False


def test_the_stepped_card_shows_the_governors_cool_off_without_a_run(app_client):
    services = _services(app_client)
    engine = _install(app_client, RecordingEngine)
    engine.runner, engine.stats = None, {"governor_trips": 1}
    now, mono = services.clock.now(), services.clock.monotonic()
    engine.stepped_cooloff = {"canopy": mono + 120.0, "old": mono - 1.0}  # an expired cool-off isn't shown
    gauge = app_client.get("/api/canopy/state").json()["stepped"]
    assert gauge["running"] is False and gauge["governor_trips"] == 1 and "writes_60s" not in gauge
    (cooling,) = gauge["cooloff"]
    assert cooling["account"] == "canopy" and cooling["until"] == pytest.approx(now + 120.0, abs=2.0)
    engine.stepped_cooloff = {}
    assert app_client.get("/api/canopy/state").json()["stepped"] is None


def test_the_ops_page_draws_the_stepped_card_from_the_served_model(app_client):
    page = app_client.get("/ops/").text
    assert 'id="canopy-stepped"' in page
    script = app_client.get("/ops/ops.js").text
    assert (
        'from "/ops/stepped-model.mjs"' in script
        and "steppedLines(st.program, steppedGauge, clock)" in script
    )
    assert 'stepped: "T3 stepped' in script  # the tier chip knows the stepped tier
    model = app_client.get("/ops/stepped-model.mjs")
    assert model.status_code == 200 and "javascript" in model.headers["content-type"]
    assert "export function steppedLines" in model.text
    assert "http://" not in model.text and "https://" not in model.text


# ---- the stepped rehearsal (Gate R; conductor/rehearsal.py) ------------------------------------------------------
class RehearsalEngine(RecordingEngine):
    """RecordingEngine plus the rehearsal calls: the preview's sentence names the modules and the minutes."""

    def rehearsal_preview(self, mode_id: str, minutes: int) -> dict:
        self._call("rehearsal_preview", mode_id=mode_id, minutes=minutes)
        return {
            "mode": mode_id,
            "minutes": minutes,
            "confirm": f"Rehearse {mode_id} for {minutes} min on Module 1?",
        }

    async def rehearse(self, mode_id: str, minutes: int, *, source: str) -> dict:
        self._call("rehearse", mode_id=mode_id, minutes=minutes, source=source)
        return {"ok": True, "message": "Stepped rehearsal started."}

    def stop_rehearsal(self, *, source: str) -> dict:
        self._call("stop_rehearsal", source=source)
        return {"ok": True, "message": "Stopped."}

    def rehearsal_status(self) -> dict:
        return {"running": None, "last": None, "max_minutes": 10}


def test_the_rehearsal_starts_only_with_the_confirm_sentence_and_never_past_ten_minutes(app_client):
    engine = _install(app_client, RehearsalEngine)
    route = "/api/canopy/stepped-rehearsal"
    for minutes in (11, 0, 2.5):
        assert app_client.post(route, json={"mode": "golden-hour", "minutes": minutes}).status_code == 422
    assert app_client.get(route, params={"mode": "golden-hour", "minutes": 11}).status_code == 400
    assert app_client.get(route, params={"mode": "golden-hour"}).status_code == 400  # minutes are part of it
    assert app_client.post(route, json={"mode": "disco", "minutes": 2}).status_code == 400
    assert engine.calls == []
    # no sentence: 409 with the sentence to confirm, and nothing started
    asked = app_client.post(route, json={"mode": "golden-hour", "minutes": 2})
    sentence = "Rehearse golden-hour for 2 min on Module 1?"
    assert asked.status_code == 409 and asked.json()["confirm_required"] is True
    assert asked.json()["confirm"] == sentence and sentence in asked.json()["detail"]
    # a sentence for other minutes (or modules) isn't this one
    stale = app_client.post(route, json={"mode": "golden-hour", "minutes": 3, "confirm": sentence})
    assert stale.status_code == 409
    assert [name for name, _ in engine.calls] == ["rehearsal_preview", "rehearsal_preview"]
    engine.calls.clear()
    preview = app_client.get(route, params={"mode": "golden-hour", "minutes": 2}).json()
    # D75: any look can be rehearsed, the idle look included
    assert preview["preview"]["confirm"] == sentence and "lowcountry-calm" in [
        m["id"] for m in preview["modes"]
    ]
    started = app_client.post(route, json={"mode": "golden-hour", "minutes": 2, "confirm": sentence})
    assert started.status_code == 200 and started.json()["ok"] is True
    assert engine.calls[-1] == ("rehearse", {"mode_id": "golden-hour", "minutes": 2, "source": "ops"})
    stop = app_client.post(f"{route}/stop", json={})
    assert stop.status_code == 200 and engine.calls[-1] == ("stop_rehearsal", {"source": "ops"})
    # same-site JSON only, like every control
    evil = app_client.post(
        route,
        json={"mode": "golden-hour", "minutes": 2, "confirm": sentence},
        headers={"Origin": "https://evil.example"},
    )
    assert evil.status_code == 403 and engine.calls[-1][0] == "stop_rehearsal"


def test_an_engine_without_the_rehearsal_says_so(app_client):
    _install(app_client, RecordingEngine)
    response = app_client.post(
        "/api/canopy/stepped-rehearsal", json={"mode": "golden-hour", "minutes": 2, "confirm": "x"}
    )
    assert response.status_code == 409 and response.json()["detail"] == ops_routes.NO_REHEARSAL


def test_a_rehearsal_from_ops_on_the_show_engine_writes_its_report_and_leaves_the_look_still(
    app_client_show, tmp_path
):
    from test_stepped_rehearsal import REPORT_FIELDS

    c = app_client_show
    engine = past_startup(c)  # past startup commissioning, which would refuse the rehearsal or land in it
    route = "/api/canopy/stepped-rehearsal"
    asked = c.get(route, params={"mode": "golden-hour", "minutes": 2})
    assert asked.status_code == 200, asked.text
    preview = asked.json()["preview"]
    sentence = preview["confirm"]
    labels = [e.label for e in _services(c).registry.canopy_modules()]
    assert "for 2 minutes" in sentence and all(label in sentence for label in labels)
    assert "(simulator: no real lights)" in sentence
    started = c.post(route, json={"mode": "golden-hour", "minutes": 2, "confirm": sentence})
    assert started.status_code == 200, started.text
    state = c.get("/api/canopy/state").json()
    assert state["tier"] == "stepped" and state["rehearsal"]["running"]["mode"] == "golden-hour"
    assert c.post("/api/canopy/mode", json={"mode": "golden-hour"}).status_code == 200  # ops still decides
    wait_until(
        "the rehearsal's report", lambda: engine.last_rehearsal, describe=lambda: engine.rehearsal_status()
    )
    last = c.get("/api/canopy/state").json()["rehearsal"]["last"]
    assert last["verdict"] == "fail" and last["ended"] == "ops chose golden-hour"  # not a silent restart
    report = json.loads((tmp_path / "data" / "bench" / "rehearsals" / f"{last['run_id']}.json").read_text())
    assert set(report) == REPORT_FIELDS and report["source"] == "ops" and report["simulated"] is True
    after = c.get("/api/canopy/state").json()
    assert after["program"] is None and after["rehearsal"]["running"] is None and after["tier"] != "stepped"
    assert c.post(f"{route}/stop", json={}).status_code == 409  # nothing left to stop


def test_the_ops_page_starts_a_rehearsal_only_through_its_confirm_dialog(app_client):
    page = app_client.get("/ops/").text
    for element in ('id="rehearsal-mode"', 'id="rehearsal-minutes"', 'max="10"', 'id="rehearsal-dialog"'):
        assert element in page
    script = app_client.get("/ops/ops.js").text
    assert "rehearsalText" in script and 'from "/ops/stepped-model.mjs"' in script
    # the only POST that starts one carries the sentence the dialog showed
    assert script.count('"/api/canopy/stepped-rehearsal", pending') == 1
    assert 'dialog.returnValue === "go") startRehearsal()' in script
