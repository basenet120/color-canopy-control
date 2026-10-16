"""The Studio's routes on the real app + the fast simulator: the home redirect, ops-only access, cross-site refusal,
a missing second module, gradient preflight (T1 proof and each kit's own map), and the paired commands going
through the real executor — including exact restoration of what the gradient's dark reset overwrote; and the
canopy illustration's design (GET /api/canopy/design, ops only) is each scene's 2-D design head for head, equal in
sim to what the /visualizer and the kiosk grid draw."""

from __future__ import annotations

import json
import time
from pathlib import Path

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
    """Short readback waits and rate budget, as in test_module_api's `fast` (these tests aren't about timing).
    The simulated cloud enforces the same budget (429 + a counted violation for a write that comes too soon)."""
    services = _services(c)
    policy = services.settings.executor
    policy.readback_delay_s, policy.readback_interval_s, policy.min_write_interval_s = 0.05, 0.05, 0.1
    services.sim.settings = services.sim.settings.with_(per_device_min_interval_s=policy.min_write_interval_s)


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


# ---- the canopy illustration paints the show's design (GET /api/canopy/design, P12) --------------------------------
SCENE_IDS = (
    "lowcountry-calm",
    "rainbow-row-sunset",
    "garden-gathering",
    "coastal-tides",
    "golden-hour",
    "goodnight-glow",
)


def _point(x: float, y: float) -> tuple[int, int]:
    return round(2 * x), round(2 * y)


def _palette_at(canvas, x: float, y: float, canopy: dict, recipes) -> list[int]:
    """An independent oracle: field.value → the canvas's stops (recipe hex, mixed half up), at full strength."""
    import itertools
    import math

    from conductor.looks import field as F

    t = F.value(canvas.field, x, y, canopy)
    stops = [(s.at, recipes.colors[s.color].rgb_only) for s in canvas.stops]
    assert all(
        c.colorBrightness == 100 and not c.whiteBrightness for _, c in stops
    )  # RGB designs, full strength
    a = b = stops[0][1] if t <= stops[0][0] else stops[-1][1]
    u = 0.0
    if stops[0][0] < t < stops[-1][0]:
        (a_at, a), (b_at, b) = next(pair for pair in itertools.pairwise(stops) if t <= pair[1][0])
        u = (t - a_at) / (b_at - a_at)
    return [math.floor(getattr(a, k) * (1 - u) + getattr(b, k) * u + 0.5) for k in ("r", "g", "b")]


def test_the_design_route_is_ops_only(monkeypatch, tmp_path):
    path = "/api/canopy/design?mode=golden-hour"
    with _client(monkeypatch, tmp_path, CONDUCTOR_BASIC_AUTH="crew:canopy") as c:
        assert c.get(path).status_code == 401  # nobody signed in
        assert c.get(path, auth=OPS).status_code == 200
        for role, grants in (("kiosk", []), ("tester", ["bench-kit"])):
            c.cookies.clear()
            link = c.post(
                "/api/roles/enrollments",
                json={"role": role, "name": f"test {role}", "grants": grants},
                auth=OPS,
            ).json()
            assert c.post(link["path"], follow_redirects=False).status_code == 303
            assert c.get(path).status_code in (401, 403), role


def test_the_design_route_requires_ops_even_past_the_path_table():
    """The route's own guard, not only the auth middleware's path table (which already keeps other roles out of
    /api/canopy/*): its dependencies include `require_ops`, which answers a non-ops principal 403."""
    from fastapi.routing import APIRoute

    from conductor.routes import design
    from conductor.routes.deps import require_ops

    def calls(dependant) -> set:
        return {dependant.call} | set().union(*(calls(d) for d in dependant.dependencies))

    (route,) = [r for r in design.router.routes if isinstance(r, APIRoute) and r.path == "/api/canopy/design"]
    assert route.methods == {"GET"}
    assert require_ops in calls(route.dependant)


def test_an_unknown_mode_is_a_plain_404(plain):
    r = plain.get("/api/canopy/design?mode=disco")
    assert r.status_code == 404 and "disco" in r.json()["detail"]
    assert plain.get("/api/canopy/design").status_code == 422


@pytest.mark.parametrize("mode_id", SCENE_IDS)
def test_the_design_is_the_scenes_2d_design_at_every_head(plain, mode_id):
    from conductor.canopy_view import canopy_geometry
    from conductor.config import DeviceEntry
    from conductor.looks import CompileContext, module_look

    services = _services(plain)
    ctx = CompileContext(registry=[], capabilities={}, config_dir=services.config_dir)
    mode = ctx.modes.modes[mode_id]
    r = plain.get(f"/api/canopy/design?mode={mode_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert r.headers["cache-control"] == "no-store"
    assert body["mode"] == mode_id and body["ladder"] == list(mode.ladder)
    # the T1 canvas when the scene has one, else its module-level sample's canvas (same design, D63)
    canvas = (
        mode.techniques.canvas.canvas if mode.techniques.canvas else mode.techniques.modules.sample.canvas
    )
    assert body["design"] == ("canvas" if mode.techniques.canvas else "sample")
    heads = body["heads"]
    assert len(heads) == 270
    lattice = canopy_geometry(ctx.canopy)["modules"]
    assert [(h["module"], h["x"], h["y"]) for h in heads] == [
        (m["pos"], x, y) for m in lattice for x, y in m["heads"]
    ]
    for h in heads:
        assert h["rgb"] == _palette_at(canvas, h["x"], h["y"], ctx.canopy, ctx.recipes), h
    # a 2-D design: some column of lights isn't one colour top to bottom (x / 225 always was)
    columns: dict[float, set] = {}
    for h in heads:
        columns.setdefault(h["x"], set()).add(tuple(h["rgb"]))
    assert any(len(c) > 1 for c in columns.values())
    # module level: the compiler's own T3 sample per position
    assert body["sample_at"] == mode.techniques.modules.sample.at
    assert [m["module"] for m in body["modules"]] == [m["pos"] for m in lattice]
    for m in body["modules"]:
        entry = DeviceEntry(id=f"t-{m['module']}", role="canopy_module", account="t", module=m["module"])
        assert m["rgb"] == list(module_look(mode, entry, ctx).rgb)


def test_a_mode_without_one_rgb_colour_per_module_or_without_a_design_draws_no_such_level():
    """The pre-D59 technique modes: a canvas with white module rows has heads but no module level; a whole-strand
    mode has neither (the Studio says so and draws the palette)."""
    from technique_modes import TECHNIQUE_MODES

    from conductor.looks import CompileContext
    from conductor.routes.design import mode_design

    ctx = CompileContext(registry=[], capabilities={}, modes=TECHNIQUE_MODES)
    sunset = mode_design("rainbow-row-sunset", ctx)  # module rows include a white look
    assert sunset["design"] == "canvas" and len(sunset["heads"]) == 270 and sunset["modules"] is None
    calm = mode_design("lowcountry-calm", ctx)  # one white look on every strand
    assert calm["design"] is None and calm["heads"] is None and calm["modules"] is None
    assert calm["sample_at"] is None


def test_the_designs_heads_are_the_studios_saved_geometry():
    """The Studio keys the design by canopy point onto its generated CANOPY heads: all 270 must meet one to one, or
    the Studio would fall back to the palette left to right."""
    from types import SimpleNamespace

    from conductor.config import CONFIG_DIR
    from conductor.routes.design import design_json

    text = (Path(__file__).resolve().parent.parent / "web/ops/showcase/demo-model.mjs").read_text()
    start = text.index("export const CANOPY = ") + len("export const CANOPY = ")
    studio = json.loads(text[start : text.index("\n", start)].rstrip().rstrip(";"))
    design = design_json(SimpleNamespace(config_dir=CONFIG_DIR), "golden-hour")
    server = {(h["module"], _point(h["x"], h["y"])) for h in design["heads"]}
    assert {(h["module"], _point(h["x"], h["y"])) for h in studio["heads"]} == server
    assert len(server) == 270


def test_in_sim_the_studio_design_matches_the_visualizer_and_the_kiosk_head_for_head(app_client_show):
    """Acceptance (P12): each scene through the real engine on the fast simulator. The Studio's default picture (the
    level the show tries first) equals, head for head, the plan the /visualizer draws (`/api/canopy/heads`: each
    head's desired colour at full strength, at its own calibrated point) and the kiosk grid (`/api/kiosk/panel`'s lit
    picture, lattice order)."""
    from partial_canopy import showing

    c = app_client_show
    engine = showing(c)
    for mode_id in SCENE_IDS:
        r = c.post("/api/canopy/mode", json={"mode": mode_id})
        assert r.status_code == 200, r.text
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            panel = c.get("/api/kiosk/panel").json()
            lit = panel["state"]["lit"]
            plan = engine.current_plan()
            if plan.mode_id == mode_id and lit and all(m["state"] == "lit" for m in lit["modules"]):
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"{mode_id} never settled: {engine.state()}")
        design = c.get(f"/api/canopy/design?mode={mode_id}").json()
        first = design["ladder"][0]
        assert str(plan.tier) == first, (mode_id, plan.tier)  # the simulator runs the first rung
        by_point = {_point(h["x"], h["y"]): h["rgb"] for h in design["heads"]}
        by_module = {m["module"]: m["rgb"] for m in design["modules"]}

        def studio(module: str, x: float, y: float, first=first, by_point=by_point, by_module=by_module):
            return by_module[module] if first == "modules" else by_point[_point(x, y)]

        heads = c.get("/api/canopy/heads").json()
        assert len(heads["heads"]) == 270
        for h in heads["heads"]:
            assert h["desired"] is not None
            full = plan.modules[h["device_id"]].colors[h["index"]]
            assert [full["r"], full["g"], full["b"]] == studio(h["module"], h["x"], h["y"]), (mode_id, h)
        geometry = {m["pos"]: m["heads"] for m in panel["canopy"]["modules"]}
        for m in lit["modules"]:
            colours = m["colors"] or [m["color"]] * len(geometry[m["pos"]])
            for hexa, (x, y) in zip(colours, geometry[m["pos"]], strict=True):
                assert [int(hexa[i : i + 2], 16) for i in (1, 3, 5)] == studio(m["pos"], x, y), (
                    mode_id,
                    m["pos"],
                )
