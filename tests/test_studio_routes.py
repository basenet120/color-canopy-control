"""The Studio's routes on the real app + the fast simulator: the home redirect, ops-only access, and the canopy
illustration's design (GET /api/canopy/design, ops only): each scene's 2-D design head for head, equal in sim to what
the /visualizer and the kiosk grid draw. The Studio is screen only (D67): it has no write routes of its own (the
paired /api/studio/pair/* commands went with the shop kits; tests/test_canopy_only.py proves they answer 404)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from conductor.scenes import load_scenes

OPS = ("crew", "canopy")


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


def _client(monkeypatch, tmp_path, overlay=None, **extra):
    from conductor.app import app

    _env(monkeypatch, tmp_path, overlay, **extra)
    return TestClient(app, raise_server_exceptions=False)


def _services(c):
    return c.app.state.services


@pytest.fixture
def plain(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, CONDUCTOR_AUTH="off") as c:
        yield c


# ---- the home redirect (D67: the visualizer everywhere) ----------------------------------------------------------
@pytest.mark.parametrize(
    "env",
    [
        {"CONDUCTOR_ENV": "workshop"},
        {"CONDUCTOR_ENV": "workshop-sim"},
        {"CONDUCTOR_ENV": "venue"},
        {"RAILWAY_ENVIRONMENT_NAME": "staging"},
        {},
    ],
    ids=["workshop", "workshop-sim", "venue", "staging", "local"],
)
def test_home_is_the_visualizer_everywhere(monkeypatch, tmp_path, env):
    with _client(monkeypatch, tmp_path, CONDUCTOR_BASIC_AUTH="crew:canopy", **env) as c:
        r = c.get("/", auth=OPS, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/visualizer/"


# ---- access ---------------------------------------------------------------------------------------------------------
def test_the_studio_is_ops_only(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, CONDUCTOR_BASIC_AUTH="crew:canopy") as c:
        link = c.post(
            "/api/roles/enrollments",
            json={"role": "tester", "name": "Crew phone", "grants": ["bench-kit"]},
            auth=OPS,
        ).json()
        # Following the link gives this client the tester's session cookie.
        assert c.post(link["path"], follow_redirects=False).status_code == 303
        assert c.get("/api/module").json()["role"] == "tester"  # what /ops/module hides the Studio link on
        for path in ("/ops/showcase/", "/ops/showcase/studio.js", "/api/canopy/design?mode=golden-hour"):
            assert c.get(path).status_code in (401, 403), path
        assert c.get("/ops/showcase/", auth=OPS).status_code == 200


# ---- the canopy illustration paints the show's design (GET /api/canopy/design, P12) --------------------------------
SCENE_IDS = tuple(
    s.id for s in load_scenes().scenes
)  # D77: the six scenes and their 12 options (eight new fins)


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
        # a stepped rung (D65) moves the module-level design; the Studio opens it at module level (its still look)
        module_first = first in ("modules", "stepped")

        def studio(
            module: str, x: float, y: float, module_first=module_first, by_point=by_point, by_module=by_module
        ):
            return by_module[module] if module_first else by_point[_point(x, y)]

        heads = c.get("/api/canopy/heads").json()
        assert len(heads["heads"]) == 270
        for h in heads["heads"]:
            assert h["desired"] is not None
            full = plan.modules[h["device_id"]].colors[h["index"]]
            assert [full["r"], full["g"], full["b"]] == studio(h["module"], h["x"], h["y"]), (mode_id, h)
        if first == "stepped":
            # the picture moves: the kiosk gets the run's frames, and frame 0 (the hold) is the Studio's picture
            motion = lit["motion"]
            assert motion and motion["kind"] == "steps", (mode_id, motion)
            grid = [f"r{r}c{c}" for r in range(3) for c in range(3)]
            hold = [[int(hexa[i : i + 2], 16) for i in (1, 3, 5)] for hexa in motion["frames"][0]]
            assert hold == [by_module[pos] for pos in grid], mode_id
            continue
        geometry = {m["pos"]: m["heads"] for m in panel["canopy"]["modules"]}
        for m in lit["modules"]:
            colours = m["colors"] or [m["color"]] * len(geometry[m["pos"]])
            for hexa, (x, y) in zip(colours, geometry[m["pos"]], strict=True):
                assert [int(hexa[i : i + 2], 16) for i in (1, 3, 5)] == studio(m["pos"], x, y), (
                    mode_id,
                    m["pos"],
                )
