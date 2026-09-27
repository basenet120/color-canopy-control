"""/ops controls, desired-vs-reported heads, the 7 am checklist and the log tail (conductor/routes/ops.py).

The canopy engine (P1) is built in parallel, so these run against tests/fake_engine.py: `RecordingEngine` proves
each control reaches the engine with source="ops"; `FakeEngine` drives the real executor on the simulator so the
heads and the test mode change see real (simulated) readback.
"""

from __future__ import annotations

import logging
import time

import pytest
from fake_engine import FakeEngine, RecordingEngine

from conductor import logring, redact
from conductor.engine_api import PlanError
from conductor.geometry import build_layout, load_canopy_config, panel_order
from conductor.routes import ops as ops_routes

CONTROL_CALLS = [
    ("/api/canopy/reassert", {}, "reassert_all", {"source": "ops"}),
    ("/api/canopy/pause", {"paused": True}, "pause_guest_input", {"paused": True, "source": "ops"}),
    ("/api/canopy/pause", {"paused": False}, "pause_guest_input", {"paused": False, "source": "ops"}),
    ("/api/canopy/safe-look", {}, "safe_look", {"source": "ops"}),
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
    assert ids[0] == "lowcountry-calm" and "golden-hour" in ids and len(ids) == 6
    assert all(m["label"] for m in body["modes"])


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
    assert body["feature"] == "Each Hubspace strand set to its own shade of amber."
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
    # left column deeper amber than the right (the modes.yaml columns), so colours really come per module
    left = next(h for h in body["heads"] if h["module"] == "r0c0")["reported"]
    right = next(h for h in body["heads"] if h["module"] == "r0c2")["reported"]
    assert left["g"] < right["g"]


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
    assert clock["status"] == "warn" and "check the box's clock" in clock["fix"].lower()
    clock = _items(app_client.get(f"/api/ops/checklist?client_ms={far}").json())["clock"]
    assert clock["status"] == "fail"

    async def unsynced():
        return False, "timedatectl says NTPSynchronized=no"

    monkeypatch.setattr(ops_routes, "ntp_status", unsynced)
    clock = _items(app_client.get("/api/ops/checklist").json())["clock"]
    assert clock["status"] == "fail" and "chrony" in clock["fix"]
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
    assert engine.calls[-1] == ("request_mode", {"mode_id": "lowcountry-calm", "source": "ops"})


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
    for asset in ("/ops/ops.js", "/ops/ops.css", "/ops/calibrate/calibrate.js", "/visualizer/"):
        text = app_client.get(asset).text
        assert "http://" not in text and "https://" not in text, f"{asset} references an external URL"
    visualizer = app_client.get("/visualizer/").text
    assert 'connectLive({\n  topics: ["canopy"]' in visualizer and "/api/canopy/heads" in visualizer
    assert 'id="schemes"' in visualizer and 'id="wire"' in visualizer and "pitch_violations" in visualizer
