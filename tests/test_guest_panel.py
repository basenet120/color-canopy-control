"""The guest panel (/api/kiosk/panel|scene|brightness|power|debug; conductor/guest_panel.py) on the real app and the
fast simulator.

Proves: the target is the workshop only with CONDUCTOR_ENV=workshop* and both shop modules registered; a workshop
scene is the 30-light gradient on both strands when both have their own map (Custom 1 only, Custom 3 untouched, the
user's original Custom 1 kept as the one backup across repeated scenes), and the scene's solid colour otherwise —
including when Custom 1 was changed outside the app, which is never overwritten; brightness, power and the guest floor;
a dragging slider is coalesced inside the rate budget; the scene cooldown; a module held by the module page is a calm
refusal with nothing sent, the panel never steals a lock and lets go when idle; the engine target asks the engine and
reports capabilities by what the engine has; the kiosk role reaches the panel but not the debug view; the ops entry
points and the dev launcher.
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from partial_canopy import LIVE, partial_show, showing

from conductor import rows as R
from conductor.engine_api import CanopyPhase
from conductor.guest_panel import (
    BUSY,
    CHANGING,
    FEATURE_GRADIENT,
    FEATURE_SOLID,
    GUEST_OWNER,
    GuestPanel,
)
from conductor.module.pair import PAIR_IDS
from conductor.scenes import load_scenes

ROOT = Path(__file__).resolve().parent.parent
SCENES = load_scenes()
CALM, COASTAL, GOLDEN = (SCENES.get(i) for i in ("lowcountry-calm", "coastal-tides", "golden-hour"))
OPS = ("crew", "canopy")


def _launcher():
    spec = importlib.util.spec_from_file_location("dev_guest_panel", ROOT / "scripts" / "dev_guest_panel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---- harness ------------------------------------------------------------------------------------------------------
def _env(monkeypatch, tmp_path, overlay=None, **extra):
    for key in (
        "CONDUCTOR_BASIC_AUTH",
        "CONDUCTOR_AUTH",
        "HUBSPACE_LIVE",
        "CONDUCTOR_MODE",
        "RAILWAY_ENVIRONMENT_NAME",
        "CONDUCTOR_ENV",
    ):
        monkeypatch.delenv(key, raising=False)
    data = tmp_path / "data"
    monkeypatch.setenv("CONDUCTOR_DATA_DIR", str(data))
    monkeypatch.setenv("CONDUCTOR_SIM_SPEED", "fast")
    monkeypatch.setenv("CONDUCTOR_ENGINE", "off")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)
    if overlay is not None:
        (data / "overrides").mkdir(parents=True)
        (data / "overrides" / "registry.yaml").write_text(yaml.safe_dump(overlay))


def _mapping(device_id: str) -> dict:
    return {"frame_length": 30, "indices": list(range(30)), "proven_on": f"sim-{device_id}"}


def _overlay(*, string: bool = True, string_mapped: bool = True) -> dict:
    devices: dict = {"bench-kit": {"framebuffer_mapping": _mapping("bench-kit")}}
    if string:
        devices["shop-string-1"] = {
            "role": "spare",
            "account": "bench",
            "label": "String light 1 (shop module)",
            "model": "SQHDK03",
            "profile": "sqhdk03-sim",
            "expected_heads": 30,
            "orientation": "A",
        }
        if string_mapped:
            devices["shop-string-1"]["framebuffer_mapping"] = _mapping("shop-string-1")
    return {"devices": devices}


def _client(monkeypatch, tmp_path, overlay=None, **extra):
    from conductor.app import app

    _env(monkeypatch, tmp_path, overlay, **extra)
    return TestClient(app, raise_server_exceptions=False)


def _services(c):
    return c.app.state.services


def _fast(c, cooldown_s: float = 0.2) -> None:
    """Short readback waits and rate budget (as test_studio_routes) and a short scene cooldown. The simulated cloud
    enforces the same budget: a write that comes too soon is a 429 and a counted violation."""
    services = _services(c)
    policy = services.settings.executor
    policy.readback_delay_s, policy.readback_interval_s, policy.min_write_interval_s = 0.05, 0.05, 0.1
    services.sim.settings = services.sim.settings.with_(per_device_min_interval_s=policy.min_write_interval_s)
    services.settings.kiosk.cooldown_s = cooldown_s


def _workshop(monkeypatch, tmp_path, **kw):
    overlay = kw.pop("overlay", None) or _overlay()
    c = _client(monkeypatch, tmp_path, overlay, CONDUCTOR_AUTH="off", CONDUCTOR_ENV="workshop-sim", **kw)
    return c


@pytest.fixture
def shop(monkeypatch, tmp_path):
    with _workshop(monkeypatch, tmp_path) as c:
        _fast(c)
        yield c


def _panel(c) -> dict:
    r = c.get("/api/kiosk/panel")
    assert r.status_code == 200, r.text
    return r.json()


def _settle(c, timeout: float = 15.0) -> dict:
    """The panel once nothing is changing (the lane has finished)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = _panel(c)
        if body["state"]["phase"] != "changing":
            return body
        time.sleep(0.05)
    raise AssertionError(f"still changing after {timeout} s: {body}")


def _cooled(c) -> None:
    time.sleep(_services(c).settings.kiosk.cooldown_s + 0.05)


def _cloud(c, device_id: str) -> dict:
    """What the simulated cloud stores for the module (what readback reports)."""
    return {r.key: r.value for r in _services(c).sim.cloud_view(f"sim-{device_id}")}


def _frame_rgb(c, device_id: str, slot: str = "custom-1") -> list[list[int]]:
    return [[e.r, e.g, e.b] for e in R.frame_entries(_cloud(c, device_id)[(R.FRAME_CLASS, slot)])]


def _writes(c) -> int:
    return _services(c).sim.stats()["writes"]


def _violations(c) -> int:
    return _services(c).sim.stats()["min_interval_violations_total"]


def _record(c) -> dict:
    return json.loads((_services(c).data / "studio-gradients" / "active.json").read_text())


def _holder(c, device_id: str) -> str | None:
    lease = _services(c).locks.holder(f"sim-{device_id}")
    return lease.owner if lease else None


# ---- which target ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "env, overlay, target",
    [
        ({"CONDUCTOR_ENV": "workshop-sim"}, _overlay(), "workshop"),
        ({"CONDUCTOR_ENV": "workshop"}, _overlay(), "workshop"),
        ({"CONDUCTOR_ENV": "workshop-sim"}, _overlay(string=False), "canopy"),
        ({"CONDUCTOR_ENV": "venue"}, _overlay(), "canopy"),
        ({"RAILWAY_ENVIRONMENT_NAME": "staging"}, _overlay(), "canopy"),
        ({}, _overlay(), "canopy"),
    ],
    ids=["workshop-sim", "workshop", "workshop-without-string-light", "venue", "staging", "local"],
)
def test_the_target_is_the_workshop_only_there_with_both_modules(monkeypatch, tmp_path, env, overlay, target):
    with _client(monkeypatch, tmp_path, overlay, CONDUCTOR_BASIC_AUTH="crew:canopy", **env) as c:
        r = c.get("/api/kiosk/panel", auth=OPS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == target
    if target == "workshop":
        assert body["capabilities"] == {"scene": True, "brightness": True, "power": True}
        assert body["target_label"] == "Two shop strands (60 lights)"
    else:  # no engine in these runs: the canopy rests and offers nothing
        assert body["capabilities"] == {"scene": False, "brightness": False, "power": False}
        assert body["state"]["phase"] == "resting" and body["state"]["message"]


def test_the_panel_lists_the_six_approved_scenes(shop):
    body = _panel(shop)
    assert [s["id"] for s in body["scenes"]] == [s.id for s in SCENES.scenes]
    for got, scene in zip(body["scenes"], SCENES.scenes):
        assert got["name"] == scene.name and got["mood"] == scene.mood
        assert got["description"] == scene.description and got["brightness_draft"] == scene.brightness_draft
        assert got["palette"] == [{"name": p.name, "hex": p.hex} for p in scene.palette]
        assert got["frame"] == [list(rgb) for rgb in scene.frame(30)] and len(got["frame"]) == 30
    assert body["brightness_range"] == [10, 100] and body["viewer"] == "ops" and body["simulated"] is True
    assert body["state"]["scene"] is None and body["state"]["feature"] is None
    assert body["state"]["phase"] == "ready" and isinstance(body["server_now"], float)
    assert shop.get("/api/kiosk/panel").headers["cache-control"] == "no-store"


# ---- workshop scenes -----------------------------------------------------------------------------------------------
def test_a_scene_is_the_gradient_on_both_mapped_strands(shop):
    custom3 = {d: _cloud(shop, d)[(R.FRAME_CLASS, "custom-3")] for d in PAIR_IDS}
    r = shop.post("/api/kiosk/scene", json={"scene": COASTAL.id})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["state"]["phase"] == "changing" and r.json()["state"]["scene"] == COASTAL.id
    assert r.json()["state"]["feature"] is None  # nothing is claimed until the lights confirm
    assert r.json()["state"]["look"] is None
    state = _settle(shop)["state"]
    assert state["scene"] == COASTAL.id and state["feature"] == FEATURE_GRADIENT
    assert state["look"] == "gradient"  # the panel previews every light
    assert state["power"] is True and state["brightness"] == COASTAL.brightness_draft
    for d in PAIR_IDS:
        cloud = _cloud(shop, d)
        assert _frame_rgb(shop, d) == [list(rgb) for rgb in COASTAL.frame(30)]  # full strength, not dimmed
        assert cloud[("color-mode", None)] == "individual"
        assert cloud[("color-individual", "custom")] == "custom-1"
        assert cloud[("brightness", None)] == COASTAL.brightness_draft
        assert cloud[(R.FRAME_CLASS, "custom-3")] == custom3[d]  # Custom 3 is never written
        assert _holder(shop, d) == GUEST_OWNER
    assert _record(shop)["pending"] is True
    assert _violations(shop) == 0


def test_repeated_scenes_keep_the_users_original_custom_1_as_the_one_backup(shop):
    original = {d: _cloud(shop, d)[(R.FRAME_CLASS, "custom-1")] for d in PAIR_IDS}
    for scene in (COASTAL, GOLDEN, CALM):
        assert shop.post("/api/kiosk/scene", json={"scene": scene.id}).status_code == 200
        assert _settle(shop)["state"]["feature"] == FEATURE_GRADIENT
        _cooled(shop)
    record = _record(shop)
    assert record["pending"] is True
    for d in PAIR_IDS:
        assert record["modules"][d]["original"] == original[d]
        assert record["modules"][d]["allowed"] == [_cloud(shop, d)[(R.FRAME_CLASS, "custom-1")]]
        assert _frame_rgb(shop, d) == [list(rgb) for rgb in CALM.frame(30)]
    backups = list((_services(shop).data / "studio-gradients").glob("backup-*.json"))
    assert len(backups) == 1  # one backup of the original; later scenes never replace it
    assert _violations(shop) == 0


def test_custom_1_changed_outside_the_app_is_kept_and_the_scene_shows_solid(shop):
    assert shop.post("/api/kiosk/scene", json={"scene": COASTAL.id}).status_code == 200
    _settle(shop)
    _cooled(shop)
    # Someone paints Custom 1 on String light 1 in the Hubspace app.
    current = _cloud(shop, "shop-string-1")[(R.FRAME_CLASS, "custom-1")]
    painted = R.frame_value(current, [R.FrameEntry(9, 99, 9, 0, 100, 0)] * 30, expected_length=30)
    _services(shop).sim.cloud.change_out_of_band("sim-shop-string-1", [R.slot_write("custom-1", painted)])
    original = _record(shop)["modules"]["shop-string-1"]["original"]
    assert shop.post("/api/kiosk/scene", json={"scene": GOLDEN.id}).status_code == 200
    state = _settle(shop)["state"]
    assert state["scene"] == GOLDEN.id and state["feature"] == FEATURE_SOLID  # never stuck
    for d in PAIR_IDS:
        cloud = _cloud(shop, d)
        assert cloud[("color-mode", None)] == "color"
        rgb = cloud[("color-rgb", None)]["color-rgb"]
        assert [rgb["r"], rgb["g"], rgb["b"]] == list(GOLDEN.solid.rgb)
    assert R.frame_entries(_cloud(shop, "shop-string-1")[(R.FRAME_CLASS, "custom-1")]) == R.frame_entries(
        painted
    )
    assert _record(shop)["modules"]["shop-string-1"]["original"] == original
    debug = shop.get("/api/kiosk/debug").json()
    assert "outside" in debug["internals"]["gradient_note"]
    assert _violations(shop) == 0


def test_without_both_maps_a_scene_is_one_solid_colour(monkeypatch, tmp_path):
    with _workshop(monkeypatch, tmp_path, overlay=_overlay(string_mapped=False)) as c:
        _fast(c)
        custom1 = {d: _cloud(c, d)[(R.FRAME_CLASS, "custom-1")] for d in PAIR_IDS}
        assert c.post("/api/kiosk/scene", json={"scene": GOLDEN.id}).status_code == 200
        state = _settle(c)["state"]
        assert state["scene"] == GOLDEN.id and state["feature"] == FEATURE_SOLID
        assert state["look"] == "solid"  # the panel previews one colour, the one lit
        golden = next(s for s in c.get("/api/kiosk/panel").json()["scenes"] if s["id"] == GOLDEN.id)
        assert golden["solid"] == GOLDEN.solid.hex
        assert state["brightness"] == GOLDEN.brightness_draft
        for d in PAIR_IDS:
            cloud = _cloud(c, d)
            rgb = cloud[("color-rgb", None)]["color-rgb"]
            assert cloud[("color-mode", None)] == "color"
            assert [rgb["r"], rgb["g"], rgb["b"]] == list(GOLDEN.solid.rgb)  # full strength
            assert cloud[("brightness", None)] == GOLDEN.brightness_draft
            assert cloud[(R.FRAME_CLASS, "custom-1")] == custom1[d]
        assert not (_services(c).data / "studio-gradients").exists()
        debug = c.get("/api/kiosk/debug").json()["internals"]
        assert debug["gradient_ready"] is False and "map" in debug["gradient_ready_reason"]
        assert _violations(c) == 0


def test_the_dev_launchers_60_entry_controllers_take_the_gradient(monkeypatch, tmp_path):
    """scripts/dev_guest_panel.py's overlay: each kit's own map on a 60-entry sim controller (the real SQHDK03)."""
    with _workshop(monkeypatch, tmp_path, overlay=_launcher().overlay()) as c:
        _fast(c)
        assert c.post("/api/kiosk/scene", json={"scene": COASTAL.id}).status_code == 200
        assert _settle(c)["state"]["feature"] == FEATURE_GRADIENT
        for d in PAIR_IDS:
            frame = _frame_rgb(c, d)
            assert len(frame) == 60
            assert frame[:30] == [list(rgb) for rgb in COASTAL.frame(30)]
            assert all(rgb == [0, 0, 0] for rgb in frame[30:])  # unmapped controller entries are OFF


# ---- brightness and power ------------------------------------------------------------------------------------------
def test_brightness_and_power_reach_both_strands(shop):
    r = shop.post("/api/kiosk/brightness", json={"level": 55})
    assert r.status_code == 200 and r.json()["level"] == 55, r.text
    assert _settle(shop)["state"]["brightness"] == 55
    assert all(_cloud(shop, d)[("brightness", None)] == 55 for d in PAIR_IDS)
    r = shop.post("/api/kiosk/brightness", json={"level": 3})  # below the guest floor
    assert r.status_code == 200 and r.json()["level"] == 10
    assert _settle(shop)["state"]["brightness"] == 10
    assert all(_cloud(shop, d)[("brightness", None)] == 10 for d in PAIR_IDS)
    for bad in ({"level": 0}, {"level": 101}, {"level": "50"}, {"level": True}, {"level": 5.5}, {"lvl": 5}):
        assert shop.post("/api/kiosk/brightness", json=bad).status_code == 422, bad
    assert shop.post("/api/kiosk/power", json={"on": False}).status_code == 200
    state = _settle(shop)["state"]
    assert state["power"] is False and state["feature"] is None
    assert all(_cloud(shop, d)[("power", None)] == "off" for d in PAIR_IDS)
    assert shop.post("/api/kiosk/power", json={"on": "yes"}).status_code == 422
    assert shop.post("/api/kiosk/power", json={"on": True}).status_code == 200
    assert _settle(shop)["state"]["power"] is True
    assert all(_cloud(shop, d)[("power", None)] == "on" for d in PAIR_IDS)
    assert _violations(shop) == 0


def test_the_scene_stays_shown_while_a_brightness_change_runs(shop):
    assert shop.post("/api/kiosk/scene", json={"scene": COASTAL.id}).status_code == 200
    assert _settle(shop)["state"]["look"] == "gradient"
    _cooled(shop)
    _services(shop).settings.executor.readback_delay_s = 0.3  # a job long enough to poll inside
    assert shop.post("/api/kiosk/brightness", json={"level": 40}).status_code == 200
    seen = []
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        state = _panel(shop)["state"]
        seen.append(state)
        if state["phase"] != "changing":
            break
        time.sleep(0.01)
    assert len(seen) > 3 and seen[-1]["brightness"] == 40
    for state in seen:  # the gradient never stopped showing: never 'no scene' or a missing Hubspace feature
        shown = (state["scene"], state["look"], state["feature"])
        assert shown == (COASTAL.id, "gradient", FEATURE_GRADIENT), state


def test_a_dragging_slider_is_coalesced_inside_the_rate_budget(shop):
    levels = list(range(20, 100, 4))  # 20 requests, as fast as the client can send them
    before = _writes(shop)
    for level in levels:
        r = shop.post("/api/kiosk/brightness", json={"level": level})
        assert r.status_code == 200 and r.json()["state"]["brightness"] == level
    state = _settle(shop)["state"]
    assert state["brightness"] == levels[-1]
    assert all(_cloud(shop, d)[("brightness", None)] == levels[-1] for d in PAIR_IDS)
    writes = _writes(shop) - before
    assert writes % 2 == 0 and writes <= len(levels), writes  # one write per module per job, far fewer jobs
    assert _violations(shop) == 0


def test_a_second_scene_inside_the_cooldown_is_a_429(monkeypatch, tmp_path):
    with _workshop(monkeypatch, tmp_path) as c:
        _fast(c, cooldown_s=8.0)
        assert c.post("/api/kiosk/scene", json={"scene": COASTAL.id}).status_code == 200
        r = c.post("/api/kiosk/scene", json={"scene": GOLDEN.id})
        assert r.status_code == 429, r.text
        assert 7 < r.json()["retry_after_s"] <= 8 and r.headers["retry-after"] == "8"
        assert "moment" in r.json()["detail"]
        assert c.post("/api/kiosk/brightness", json={"level": 40}).status_code == 200  # still works
        state = _settle(c)["state"]
        assert state["phase"] == "cooldown" and 0 < state["cooldown_remaining_s"] <= 8
        assert state["scene"] == COASTAL.id and state["brightness"] == 40
        commands = c.get("/api/kiosk/debug").json()["commands"]
        assert any(x["action"] == "scene" and x["outcome"] == "refused" for x in commands)


def test_two_scenes_arriving_while_the_modules_open_are_one_200_and_one_429(monkeypatch, tmp_path):
    import threading

    with _workshop(monkeypatch, tmp_path) as c:
        _fast(c, cooldown_s=8.0)
        barrier, codes = threading.Barrier(2), []

        def choose(scene_id):
            barrier.wait()
            codes.append(c.post("/api/kiosk/scene", json={"scene": scene_id}).status_code)

        threads = [threading.Thread(target=choose, args=(s.id,)) for s in (COASTAL, GOLDEN)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(codes) == [200, 429], codes
        _settle(c)
        commands = c.get("/api/kiosk/debug").json()["commands"]
        assert [x["outcome"] for x in commands if x["action"] == "scene"].count("accepted") == 1


# ---- leases --------------------------------------------------------------------------------------------------------
def test_a_module_held_by_the_module_page_is_a_calm_refusal_with_nothing_sent(shop):
    lease = shop.post("/api/module/bench-kit/open", json={}).json()["lease"]
    before = _writes(shop)
    for path, body in (
        ("/api/kiosk/scene", {"scene": COASTAL.id}),
        ("/api/kiosk/brightness", {"level": 50}),
        ("/api/kiosk/power", {"on": False}),
    ):
        r = shop.post(path, json=body)
        assert r.status_code == 409 and r.json()["detail"] == BUSY, (path, r.text)
    state = _panel(shop)["state"]
    assert state["phase"] == "resting" and state["message"] == BUSY
    assert _writes(shop) == before
    assert _holder(shop, "shop-string-1") is None  # nothing half-taken
    debug = shop.get("/api/kiosk/debug").json()
    assert debug["lease_holders"]["bench-kit"].startswith("module page")
    shop.post("/api/module/bench-kit/close", json={"lease": lease})
    assert shop.post("/api/kiosk/scene", json={"scene": COASTAL.id}).status_code == 200


def test_the_panel_never_steals_a_lock_and_lets_go_when_idle(shop):
    _panel(shop)  # creates the workshop target
    GuestPanel.of(_services(shop)).workshop.idle_release_s = 0.3
    assert shop.post("/api/kiosk/scene", json={"scene": COASTAL.id}).status_code == 200
    _settle(shop)
    r = shop.post("/api/module/bench-kit/open", json={})
    assert r.status_code == 409 and GUEST_OWNER in r.json()["detail"]
    deadline = time.monotonic() + 5
    while any(_holder(shop, d) for d in PAIR_IDS) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert all(_holder(shop, d) is None for d in PAIR_IDS)
    r = shop.post("/api/module/bench-kit/open", json={})
    assert r.status_code == 200, r.text
    assert _panel(shop)["state"]["scene"] == COASTAL.id  # opening changes nothing on the lights
    whole = shop.post("/api/module/bench-kit/whole", json={"lease": r.json()["lease"], "brightness": 20})
    assert whole.status_code == 200, whole.text
    assert (
        _panel(shop)["state"]["scene"] is None
    )  # the module page changed the lights: the scene isn't claimed


def test_ops_taking_a_module_over_stops_the_panel_calmly(shop):
    assert shop.post("/api/kiosk/power", json={"on": True}).status_code == 200
    _settle(shop)
    r = shop.post("/api/module/shop-string-1/open", json={"steal": True})
    assert r.status_code == 200, r.text
    before = _writes(shop)
    r = shop.post("/api/kiosk/power", json={"on": False})
    assert r.status_code == 409 and r.json()["detail"] == BUSY
    assert _writes(shop) == before
    assert _holder(shop, "bench-kit") is None  # the panel let go of its other module too


# ---- debug ---------------------------------------------------------------------------------------------------------
def test_debug_shows_internals_leases_and_the_last_commands(shop):
    assert shop.post("/api/kiosk/scene", json={"scene": COASTAL.id}).status_code == 200
    _settle(shop)
    body = shop.get("/api/kiosk/debug").json()
    assert body["target"] == "workshop" and body["environment"] == "workshop-sim"
    assert body["capabilities"] == {"scene": True, "brightness": True, "power": True}
    assert body["lease_holders"] == {"bench-kit": GUEST_OWNER, "shop-string-1": GUEST_OWNER}
    internals = body["internals"]
    assert internals["holding_both"] is True and internals["gradient_ready"] is True
    assert internals["gradient_record"]["pending"] is True
    assert all(internals["modules"][d]["mapped"] for d in PAIR_IDS)
    job = next(x for x in body["commands"] if x["action"] == "scene" and x["outcome"].startswith("applied"))
    assert job["outcome"] == "applied: gradient" and isinstance(job["latency_ms"], int)
    assert job["params"] == {"scene": COASTAL.id, "brightness": COASTAL.brightness_draft}
    assert {"at", "action", "params", "outcome", "latency_ms", "error"} <= set(job)
    assert body["last_state"]["scene"] == COASTAL.id
    assert len(body["commands"]) <= 50
    assert "sim-" not in json.dumps(body)  # no device addresses


def test_writes_are_json_and_same_site_only(shop):
    before = _writes(shop)
    r = shop.post(
        "/api/kiosk/scene", content=json.dumps({"scene": COASTAL.id}), headers={"Content-Type": "text/plain"}
    )
    assert r.status_code == 415
    r = shop.post("/api/kiosk/scene", json={"scene": COASTAL.id}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert shop.post("/api/kiosk/scene", json={"scene": COASTAL.id, "x": 1}).status_code == 422
    r = shop.post("/api/kiosk/scene", json={"scene": "disco-inferno"})
    assert r.status_code == 404 and "isn't one of" in r.json()["detail"]
    assert _writes(shop) == before


# ---- the engine target (venue, staging, local) ----------------------------------------------------------------------
def _engine(client, cls=None, **kw):
    """tests/test_kiosk_routes.py's FakeEngine: it knows the modes.yaml ids (the six scenes, D59)."""
    from test_kiosk_routes import FakeEngine

    services = _services(client)
    engine = (cls or FakeEngine)(services.clock, **kw)
    services.extras["canopy"] = engine
    return engine


def _guest_engine_class():
    from test_kiosk_routes import FakeEngine

    class GuestEngine(FakeEngine):
        """With the engine's set_guest_* methods (D60): one async, one plain (the panel takes either). Like the
        real engine, its state reports what it was set to (`brightness`, `guest_power`)."""

        def __init__(self, clock, **kw):
            super().__init__(clock, **kw)
            self.guest_calls: list[tuple] = []
            self.guest = {"guest_power": True, "brightness": None}

        def state(self):
            import dataclasses

            return dataclasses.replace(super().state(), **self.guest)

        async def set_guest_brightness(self, level, *, source):
            self.guest_calls.append(("brightness", level, source))
            self.guest["brightness"] = level
            return self.state()

        def set_guest_power(self, on, *, source):
            self.guest_calls.append(("power", on, source))
            self.guest["guest_power"] = on
            return self.state()

    return GuestEngine


def test_engine_scene_asks_the_engine_and_reports_its_feature(app_client):
    engine = _engine(app_client)
    body = _panel(app_client)
    assert body["target"] == "canopy" and body["capabilities"] == {
        "scene": True,
        "brightness": False,
        "power": False,
    }
    assert body["state"]["scene"] == CALM.id and body["state"]["feature"] == engine.state().feature
    r = app_client.post("/api/kiosk/scene", json={"scene": "rainbow-row-sunset"})
    assert r.status_code == 200, r.text
    assert engine.calls == [("rainbow-row-sunset", "kiosk")]
    state = r.json()["state"]
    assert state["scene"] == "rainbow-row-sunset" and state["phase"] == "changing"
    assert state["feature"] is None  # not until the new scene is showing
    r = app_client.post("/api/kiosk/scene", json={"scene": GOLDEN.id})  # the engine's 8 s cooldown
    assert r.status_code == 429 and 7 < r.json()["retry_after_s"] <= 8
    assert engine.calls == [("rainbow-row-sunset", "kiosk")]


def test_a_scene_the_engine_doesnt_know_is_a_calm_409(app_client):
    from test_kiosk_routes import MODES_YAML

    # an engine whose mode list lacks one scene (e.g. an older modes.yaml on the box)
    engine = _engine(app_client, known=set(MODES_YAML["modes"]) - {COASTAL.id})
    r = app_client.post("/api/kiosk/scene", json={"scene": COASTAL.id})
    assert (
        r.status_code == 409 and r.json()["detail"] == "That scene isn't available right now. Choose another."
    )
    assert engine.calls == [(COASTAL.id, "kiosk")]


def test_engine_without_guest_brightness_or_power_refuses_them_calmly(app_client):
    engine = _engine(app_client)
    for path, body in (("/api/kiosk/brightness", {"level": 50}), ("/api/kiosk/power", {"on": False})):
        r = app_client.post(path, json=body)
        assert r.status_code == 409 and r.json()["detail"] == "That control isn't available right now."
    assert engine.calls == []


def test_engine_with_guest_brightness_and_power(app_client):
    _services(app_client).settings.executor.min_write_interval_s = 0.1
    engine = _engine(app_client, _guest_engine_class())
    assert _panel(app_client)["capabilities"] == {"scene": True, "brightness": True, "power": True}
    for level in (30, 35, 40, 45, 50, 55, 60):
        assert app_client.post("/api/kiosk/brightness", json={"level": level}).status_code == 200
    r = app_client.post("/api/kiosk/brightness", json={"level": 4})
    assert r.status_code == 200 and r.json()["level"] == 10
    state = _settle(app_client)["state"]
    assert state["brightness"] == 10
    brightness = [c for c in engine.guest_calls if c[0] == "brightness"]
    assert brightness[-1] == ("brightness", 10, "kiosk") and len(brightness) < 8  # coalesced
    assert app_client.post("/api/kiosk/power", json={"on": False}).status_code == 200
    state = _settle(app_client)["state"]
    assert state["power"] is False
    assert state["feature"] is None  # no technique sentence for lights that are off
    assert engine.guest_calls[-1] == ("power", False, "kiosk")


def test_the_panel_shows_the_engines_own_power_and_brightness_not_its_last_request(app_client):
    """At the venue the engine's idle reset turns the canopy back on and restores the designed brightness (D60):
    the iPad must show that, not the off / dim it last asked for."""
    _services(app_client).settings.executor.min_write_interval_s = 0.1
    base = _guest_engine_class()

    engine = _engine(app_client, base)
    assert app_client.post("/api/kiosk/power", json={"on": False}).status_code == 200
    assert app_client.post("/api/kiosk/brightness", json={"level": 20}).status_code == 200
    assert _settle(app_client)["state"]["power"] is False
    engine.guest = {"guest_power": True, "brightness": 70}  # the engine's idle reset ran
    state = _panel(app_client)["state"]
    assert state["power"] is True and state["brightness"] == 70


def test_engine_paused_resting_and_missing(app_client):
    engine = _engine(app_client, _guest_engine_class())
    engine.pause_guest_input(True, source="ops")
    assert _panel(app_client)["state"]["phase"] == "paused"
    for path, body in (("/api/kiosk/scene", {"scene": CALM.id}), ("/api/kiosk/brightness", {"level": 50})):
        assert app_client.post(path, json=body).status_code == 423
    engine.pause_guest_input(False, source="ops")
    engine.phase = CanopyPhase.RESTING
    assert _panel(app_client)["state"]["phase"] == "resting"
    assert app_client.post("/api/kiosk/power", json={"on": True}).status_code == 503
    assert engine.calls == [] and engine.guest_calls == []
    _services(app_client).extras.pop("canopy")
    body = _panel(app_client)
    assert body["state"]["phase"] == "resting" and body["capabilities"]["scene"] is False
    assert app_client.post("/api/kiosk/scene", json={"scene": CALM.id}).status_code == 503


# ---- the engine target on a partial canopy (D62) -----------------------------------------------------------------
def _canopy_ids() -> list[str]:
    from conductor.config import load_registry

    return [m.id for m in load_registry().canopy_modules()]


TWO_STRANDS = "Colour and dimming: two Hubspace strands, each set to a single colour from the scene."


def _settled_panel(c, engine, timeout: float = 10.0) -> dict:
    """The panel's state once it stops reading "changing" (≤ 10 s in the fast simulator). The engine's own phase
    (/ops) stays at needs attention the whole time: seven positions have no device registered (D62)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert engine.state().phase == CanopyPhase.NEEDS_ATTENTION
        state = _panel(c)["state"]
        if state["phase"] != "changing":
            return state
        time.sleep(0.05)
    raise AssertionError(f"the panel never settled: {_panel(c)['state']} / {engine.state()}")


def test_a_partial_canopy_takes_every_scene_and_counts_only_its_strands(monkeypatch, tmp_path):
    """The panel follows the look over the strands it drives (`look_phase`), not the engine's /ops phase: each
    scene reads "Changing the lights…" with no feature line, then settles with the two-strand sentence, while /ops
    stays needs attention naming the positions left out."""
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        engine = showing(c)
        body = _panel(c)
        assert body["target"] == "canopy" and body["target_label"] == "The Color Canopy (2 of 9 strands)"
        for scene in SCENES.scenes:
            engine._last_kiosk_mono = None  # no guest cooldown left over from the last scene
            r = c.post("/api/kiosk/scene", json={"scene": scene.id})
            assert r.status_code == 200, (scene.id, r.text)
            changing = r.json()["state"]
            assert changing["phase"] == "changing" and changing["message"] == CHANGING, (scene.id, changing)
            assert changing["feature"] is None, (scene.id, changing)
            assert sorted(engine.current_plan().modules) == list(LIVE)
            assert engine.state().phase == CanopyPhase.NEEDS_ATTENTION
            settled = _settled_panel(c, engine)
            assert settled["phase"] in ("ready", "cooldown"), (scene.id, settled)
            assert settled["feature"] == TWO_STRANDS, (scene.id, settled)
            ops = engine.state()
            assert ops.phase == CanopyPhase.NEEDS_ATTENTION and ops.look_phase == CanopyPhase.READY
            assert all(m in ops.message for m in ("Module 3", "Module 9")), ops.message
        assert _panel(c)["target_label"] == "The Color Canopy (2 of 9 strands)"


def test_a_driven_strand_that_loses_its_address_hides_the_feature_line(monkeypatch, tmp_path):
    """Module 2 stays in the look's plan but its address lookup fails: nothing reaches that strand, so the panel
    shows no feature sentence for it (look_phase needs attention, not Ready)."""
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        engine = showing(c)
        _settled_panel(c, engine)
        assert _panel(c)["state"]["feature"] == TWO_STRANDS
        lookup = engine.deps.target

        def target(device_id, purpose):
            if device_id == "canopy-r0c1":
                raise LookupError(f"{device_id} lost its address (test)")
            return lookup(device_id, purpose)

        engine.deps.target = target
        engine._resolve("canopy-r0c1")
        ops = engine.state()
        state = _panel(c)["state"]
    assert "canopy-r0c1" in engine.current_plan().modules
    assert ops.look_phase == CanopyPhase.NEEDS_ATTENTION and ops.modules["canopy-r0c1"]["left_out"] is False
    assert state["feature"] is None, state


def test_before_the_first_look_the_label_counts_the_registered_strands(monkeypatch, tmp_path):
    """With no plan yet (an engine that hasn't shown a look) the label counts what is registered (D62)."""
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        showing(c)
        _engine(c)  # tests/test_kiosk_routes.py's FakeEngine: current_plan() is None
        assert _panel(c)["target_label"] == "The Color Canopy (2 of 9 strands)"


def test_the_full_canopy_label_without_a_plan_says_nine_strands(app_client):
    _engine(app_client)
    assert _panel(app_client)["target_label"] == "The Color Canopy (nine strands)"


def test_an_engine_without_look_phase_still_drives_the_panel_by_its_phase(app_client):
    """tests/test_kiosk_routes.py's FakeEngine never sets `look_phase`: the panel reads its `phase` as before, and
    hides the feature line while it needs attention (a stuck strand) as well as while it changes."""
    engine = _engine(app_client)
    state = _panel(app_client)["state"]
    assert state["phase"] == "ready" and state["feature"] is not None
    engine.phase = CanopyPhase.UPDATING
    state = _panel(app_client)["state"]
    assert state["phase"] == "changing" and state["feature"] is None
    engine.phase = CanopyPhase.NEEDS_ATTENTION
    state = _panel(app_client)["state"]
    assert state["phase"] == "ready" and state["feature"] is None


def test_the_full_canopy_label_says_nine_strands(app_client_show):
    deadline = time.monotonic() + 30
    engine = _services(app_client_show).extras["canopy"]
    while engine.current_plan() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert engine.current_plan() is not None and not engine.current_plan().skipped
    assert _panel(app_client_show)["target_label"] == "The Color Canopy (nine strands)"


def test_while_commissioning_the_kiosk_view_rests_the_look_phase_too(app_client):
    """kiosk_view rests the guest's view while slots are written: `look_phase` with `phase`, so nothing reading
    either says the look is changing for minutes."""
    engine = _engine(app_client)
    engine.phase = CanopyPhase.UPDATING
    engine.commissioning = True
    state = app_client.get("/api/kiosk/state").json()
    assert state["phase"] == "resting" and state["look_phase"] == "resting"
    assert _panel(app_client)["state"]["phase"] == "resting"


def test_with_no_strand_registered_a_scene_is_a_503_resting(monkeypatch, tmp_path):
    with partial_show(monkeypatch, tmp_path, ()) as c:
        time.sleep(1.0)  # the capability loop's first round (it has nothing to read)
        engine = _services(c).extras["canopy"]
        assert engine.current_plan() is None and engine.state().phase == CanopyPhase.RESTING
        body = _panel(c)
        assert body["state"]["phase"] == "resting"
        assert body["target_label"] == "The Color Canopy"  # no strand to count (D62)
        r = c.post("/api/kiosk/scene", json={"scene": CALM.id})
        assert r.status_code == 503, r.text


def test_an_unregistered_position_needing_attention_links_to_the_accounts_page(monkeypatch, tmp_path):
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        showing(c)
        items = c.get("/api/ops/summary").json()["attention"]
        unregistered = [i for i in items if "no Hubspace device registered" in i["message"]]
        assert len(unregistered) == 7, items
        assert all(i["link"] == "/ops/accounts/" for i in unregistered)


def test_the_ops_safe_look_help_doesnt_promise_all_nine_modules():
    html = (ROOT / "web" / "ops" / "index.html").read_text()
    start = html.index("Safe look = ")
    help_text = " ".join(html[start : html.index(".", start)].split())
    assert "nine" not in help_text and "warm white" not in help_text, help_text  # D62; Calm isn't warm white
    assert "registered" in help_text


# ---- who may call --------------------------------------------------------------------------------------------------
def _enroll(client, role: str, grants: list[str] | None = None) -> None:
    link = client.post(
        "/api/roles/enrollments",
        json={"role": role, "name": f"test {role}", "grants": grants or []},
        auth=OPS,
    ).json()
    assert client.post(link["path"], follow_redirects=False).status_code == 303


def test_the_kiosk_reaches_the_panel_but_not_its_debug_view(app_client_auth):
    client = app_client_auth
    engine = _engine(client, _guest_engine_class())
    assert client.get("/api/kiosk/panel").status_code == 401  # not enrolled yet
    _enroll(client, "kiosk")
    body = _panel(client)
    assert body["viewer"] == "kiosk"
    assert client.post("/api/kiosk/scene", json={"scene": "rainbow-row-sunset"}).status_code == 200
    assert client.post("/api/kiosk/brightness", json={"level": 40}).status_code == 200
    assert client.post("/api/kiosk/power", json={"on": True}).status_code == 200
    assert engine.calls == [("rainbow-row-sunset", "kiosk")]
    assert client.get("/api/kiosk/debug").status_code in (401, 403)
    assert client.get("/api/kiosk/debug", auth=OPS).status_code == 200


def test_a_tester_cant_use_the_panel(app_client_auth):
    _engine(app_client_auth)
    _enroll(app_client_auth, "tester", ["bench-kit"])
    assert app_client_auth.get("/api/kiosk/panel").status_code == 401
    assert app_client_auth.post("/api/kiosk/scene", json={"scene": CALM.id}).status_code == 401
    assert app_client_auth.get("/api/kiosk/debug").status_code == 401


# ---- ops entry points and the dev launcher ---------------------------------------------------------------------------
@pytest.mark.parametrize("page", ["web/ops/index.html", "web/ops/showcase/index.html"])
def test_ops_pages_have_a_guest_panel_button(page):
    html = (ROOT / page).read_text()
    assert 'href="/kiosk/?debug=1"' in html and 'target="_blank"' in html and "Guest panel" in html


def test_the_dev_launcher_is_simulator_only(tmp_path):
    launcher = _launcher()
    env = launcher.environment(
        tmp_path,
        base={"HUBSPACE_LIVE": "1", "CONDUCTOR_MODE": "live", "CONDUCTOR_BASIC_AUTH": "a:b", "PATH": "/x"},
    )
    assert env["CONDUCTOR_MODE"] == "sim" and "HUBSPACE_LIVE" not in env and "CONDUCTOR_BASIC_AUTH" not in env
    assert env["CONDUCTOR_ENV"] == "workshop-sim" and env["CONDUCTOR_AUTH"] == "off" and env["PATH"] == "/x"
    path = launcher.write_overlay(tmp_path)
    overlay = yaml.safe_load(path.read_text())
    assert set(overlay["devices"]) == set(PAIR_IDS)
    for d in PAIR_IDS:
        assert overlay["devices"][d]["profile"] == "sqhdk03-sim-60"
        assert overlay["devices"][d]["framebuffer_mapping"]["proven_on"] == f"sim-{d}"
    assert "framebuffer_mapping" not in launcher.overlay(solid=True)["devices"]["shop-string-1"]
    makefile = (ROOT / "Makefile").read_text()
    assert "guest-dev:" in makefile and "scripts/dev_guest_panel.py" in makefile
