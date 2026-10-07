"""The Studio's routes on the real app + the fast simulator: the home redirect, ops-only access, cross-site refusal,
a missing second module, gradient preflight (T1 proof and each kit's own map), and the paired commands going
through the real executor — including exact restoration of what the gradient's dark reset overwrote."""

from __future__ import annotations

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from conductor.executor_api import CommandStatus
from conductor.gate import Purpose
from conductor.module.pair import PAIR_IDS
from conductor.module.session import ModuleSession

OPS = ("crew", "canopy")
STUDIO = [
    ("POST", "/api/studio/pair/whole"),
    ("POST", "/api/studio/pair/gradient"),
    ("GET", "/api/studio/pair/gradient"),
]


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


def _mapping(proven_on: str) -> dict:
    # The simulated kit has a 30-entry frame; a map counts only for the kit it was proven on (D25, D54).
    return {"frame_length": 30, "indices": list(range(30)), "proven_on": proven_on}


def _pair_overlay(*, string_mapping: dict | None = None) -> dict:
    """VWBD's second shop module (only ever in an ops overlay), with each kit's own map bound to its sim address."""
    string = {
        "role": "spare",
        "account": "bench",
        "label": "String light 1 (shop module)",
        "model": "SQHDK03",
        "profile": "sqhdk03-sim",
        "expected_heads": 30,
        "orientation": "A",
    }
    if string_mapping is not None:
        string["framebuffer_mapping"] = string_mapping
    return {
        "devices": {
            "bench-kit": {"framebuffer_mapping": _mapping("sim-bench-kit")},
            "shop-string-1": string,
        }
    }


def _client(monkeypatch, tmp_path, overlay=None, **extra):
    from conductor.app import app

    _env(monkeypatch, tmp_path, overlay, **extra)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def plain(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, CONDUCTOR_AUTH="off") as c:
        yield c


@pytest.fixture
def pair(monkeypatch, tmp_path):
    overlay = _pair_overlay(string_mapping=_mapping("sim-shop-string-1"))
    with _client(monkeypatch, tmp_path, overlay, CONDUCTOR_AUTH="off") as c:
        _fast(c)
        yield c


def _services(c):
    return c.app.state.services


def _fast(c) -> None:
    """Short readback waits and rate budget, as in test_module_api's `fast` (these tests aren't about timing)."""
    policy = _services(c).settings.executor
    policy.readback_delay_s, policy.readback_interval_s, policy.min_write_interval_s = 0.05, 0.05, 0.1


def _writes(c) -> int:
    return _services(c).sim.stats()["writes"]


def _open_pair(c) -> tuple[dict, dict]:
    leases, states = {}, {}
    for dev in PAIR_IDS:
        r = c.post(f"/api/module/{dev}/open", json={})
        assert r.status_code == 200, r.text
        leases[dev], states[dev] = r.json()["lease"], r.json()["state"]
    return leases, states


# ---- the home redirect (decision: the Studio is home only on the workshop laptop) ---------------------------------
@pytest.mark.parametrize(
    "env, home",
    [
        ({"CONDUCTOR_ENV": "workshop"}, "/ops/showcase/"),
        ({"CONDUCTOR_ENV": "workshop-sim"}, "/ops/showcase/"),
        ({"CONDUCTOR_ENV": "venue"}, "/visualizer/"),
        ({"RAILWAY_ENVIRONMENT_NAME": "staging"}, "/visualizer/"),
        ({}, "/visualizer/"),
    ],
    ids=["workshop", "workshop-sim", "venue", "staging", "local"],
)
def test_home_is_the_studio_only_in_the_workshop(monkeypatch, tmp_path, env, home):
    with _client(monkeypatch, tmp_path, CONDUCTOR_BASIC_AUTH="crew:canopy", **env) as c:
        r = c.get("/", auth=OPS, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == home


# ---- access ---------------------------------------------------------------------------------------------------------
def test_the_studio_is_ops_only(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, _pair_overlay(), CONDUCTOR_BASIC_AUTH="crew:canopy") as c:
        link = c.post(
            "/api/roles/enrollments",
            json={"role": "tester", "name": "VWBD shop phone", "grants": list(PAIR_IDS)},
            auth=OPS,
        ).json()
        # Following the link gives this client the tester's session cookie.
        assert c.post(link["path"], follow_redirects=False).status_code == 303
        assert c.get("/api/module").json()["role"] == "tester"  # what /ops/module hides the Studio link on
        for method, path in [*STUDIO, ("GET", "/ops/showcase/")]:
            body = {"leases": {d: "x" for d in PAIR_IDS}, "power": True} if method == "POST" else None
            assert c.request(method, path, json=body).status_code in (401, 403), (method, path)
        assert c.get("/api/studio/pair/gradient", auth=OPS).json()["pending"] is False
        assert _writes(c) == 0


def test_cross_site_and_non_json_writes_are_refused_before_anything_runs(plain):
    body = {"leases": {d: "x" for d in PAIR_IDS}, "power": True}
    for path in ("/api/studio/pair/whole", "/api/studio/pair/gradient"):
        payload = body if path.endswith("whole") else {"leases": body["leases"], "action": "restore"}
        r = plain.post(path, json=payload, headers={"Origin": "https://elsewhere.example"})
        assert r.status_code == 403, r.text
        r = plain.post(path, content=json.dumps(payload), headers={"Content-Type": "text/plain"})
        assert r.status_code == 415, r.text
    assert _writes(plain) == 0


def test_without_the_second_shop_module_nothing_is_sent(plain):
    lease = plain.post("/api/module/bench-kit/open", json={}).json()["lease"]
    leases = {"bench-kit": lease, "shop-string-1": "unknown"}
    before = _writes(plain)
    r = plain.post("/api/studio/pair/whole", json={"leases": leases, "power": True})
    assert r.status_code == 409 and "shop-string-1" in r.json()["detail"]
    r = plain.post("/api/studio/pair/gradient", json={"leases": leases, "colors": [[1, 2, 3]] * 30})
    assert r.status_code == 409
    assert _writes(plain) == before
    assert plain.get("/api/studio/pair/gradient").json()["pending"] is False


# ---- gradient preflight: both kits must have their own T1 proof and map before either is changed ---------------
# String light 1 with no map at all, and with Kingston's map copied onto it (a map never transfers between kits).
@pytest.mark.parametrize(
    "string_mapping", [None, _mapping("sim-bench-kit")], ids=["no-map", "another-kits-map"]
)
def test_gradient_refuses_a_kit_without_its_own_map(monkeypatch, tmp_path, string_mapping):
    overlay = _pair_overlay(string_mapping=string_mapping)
    with _client(monkeypatch, tmp_path, overlay, CONDUCTOR_AUTH="off") as c:
        leases, _ = _open_pair(c)
        before = _writes(c)
        r = c.post("/api/studio/pair/gradient", json={"leases": leases, "colors": [[9, 9, 9]] * 30})
        assert r.status_code == 409 and "map" in r.json()["detail"], r.text
        assert _writes(c) == before
        assert c.get("/api/studio/pair/gradient").json()["pending"] is False
        assert not (_services(c).data / "studio-gradients").exists()  # no backup either


def test_gradient_refuses_a_kit_without_t1_proof(pair, monkeypatch):
    # The simulator always allows T1; stand in for the uncommissioned String light 1 on real hardware.
    real = ModuleSession.t1_status

    def t1_status(session):
        if session.device_id == "shop-string-1":
            return False, "Painting is off until bench Gate C proves frames show up on this device."
        return real(session)

    monkeypatch.setattr(ModuleSession, "t1_status", t1_status)
    leases, _ = _open_pair(pair)
    before = _writes(pair)
    r = pair.post("/api/studio/pair/gradient", json={"leases": leases, "colors": [[9, 9, 9]] * 30})
    assert r.status_code == 409 and "Gate C" in r.json()["detail"], r.text
    assert _writes(pair) == before
    assert pair.get("/api/studio/pair/gradient").json()["pending"] is False


# ---- the paired commands go through the real executor -------------------------------------------------------------
def test_paired_whole_goes_through_the_executor_with_each_modules_lease(pair):
    services = _services(pair)
    seen = []
    services.executor_for(services.sim).add_listener(seen.append)
    leases, _ = _open_pair(pair)
    r = pair.post("/api/studio/pair/whole", json={"leases": leases, "power": False})
    assert r.status_code == 200 and r.json()["ok"], r.text
    assert {x.command.device_id: x.command.lease_token for x in seen} == {
        f"sim-{d}": leases[d] for d in PAIR_IDS
    }
    assert all(x.command.purpose is Purpose.BENCH and x.status is CommandStatus.APPLIED for x in seen)
    for dev in PAIR_IDS:
        result = r.json()["modules"][dev]["result"]
        assert result["label"] == "power off" and result["timings"]["sent_at"] is not None
        lights = {row.key: row.value for row in services.sim.device_view(f"sim-{dev}")}
        assert lights[("power", None)] == "off"


def test_coordinated_restart_without_a_brightness_is_not_a_server_error(pair):
    leases, states = _open_pair(pair)
    preset = states["bench-kit"]["capabilities"]["presets"][0]
    r = pair.post(
        "/api/studio/pair/whole",
        json={"leases": leases, "look": {"kind": "preset", "preset": preset}, "restart": True},
    )
    assert r.status_code == 200 and r.json()["ok"] and r.json()["coordinated_restart"], r.text
    assert _services(pair).sim.stats()["min_interval_violations_total"] == 0


def test_gradient_restore_brings_back_the_preset_speed_the_dark_reset_overwrote(pair):
    leases, states = _open_pair(pair)
    preset = states["bench-kit"]["capabilities"]["presets"][0]
    look = {"kind": "preset", "preset": preset, "brightness": 50, "speed": 5}
    assert pair.post("/api/studio/pair/whole", json={"leases": leases, "look": look}).json()["ok"]
    colors = [[255 - 8 * i, 0, 8 * i] for i in range(30)]
    r = pair.post(
        "/api/studio/pair/gradient", json={"leases": leases, "colors": colors, "brightness": 40, "label": "T"}
    )
    assert r.status_code == 200 and r.json()["ok"], r.text
    r = pair.post("/api/studio/pair/gradient", json={"leases": leases, "action": "restore"})
    assert r.status_code == 200 and r.json()["ok"], r.text
    for dev in PAIR_IDS:
        controls = r.json()["modules"][dev]["state"]["reported_controls"]
        assert (controls["color_mode"], controls["preset"], controls["speed"]) == ("sequence", preset, 5)
    assert pair.get("/api/studio/pair/gradient").json()["pending"] is False
    assert _services(pair).sim.stats()["min_interval_violations_total"] == 0
