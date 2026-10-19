"""The guest panel (/api/kiosk/panel|scene|brightness|power|debug; conductor/guest_panel.py) on the real app and the
fast simulator.

Proves: the target is the canopy engine on every runtime (D67, also with CONDUCTOR_ENV=workshop or workshop-sim and
two shop kits registered); the engine target asks the engine and reports capabilities by what the engine has; the
partial canopy (D62) and the canopy picture; the kiosk role reaches the panel but not the debug view; the ops entry
points.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from live_path import refuse_stepped
from partial_canopy import LIVE, partial_show, showing

from conductor.engine_api import CanopyPhase
from conductor.guest_panel import CHANGING, EngineTarget, GuestPanel
from conductor.scenes import load_scenes

ROOT = Path(__file__).resolve().parent.parent
SCENES = load_scenes()
CALM, COASTAL, GOLDEN = (SCENES.get(i) for i in ("lowcountry-calm", "coastal-tides", "golden-hour"))
OPS = ("crew", "canopy")


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


def _shop_kits() -> dict:
    """The two shop kits the old workshop target looked for: bench-kit plus a second strand registered as a spare."""
    return {
        "devices": {
            "shop-string-1": {
                "role": "spare",
                "account": "bench",
                "label": "String light 1 (shop module)",
                "model": "SQHDK03",
                "profile": "sqhdk03-sim",
                "expected_heads": 30,
            }
        }
    }


def _client(monkeypatch, tmp_path, overlay=None, **extra):
    from conductor.app import app

    _env(monkeypatch, tmp_path, overlay, **extra)
    return TestClient(app, raise_server_exceptions=False)


def _services(c):
    return c.app.state.services


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


# ---- which target ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "env",
    [
        {"CONDUCTOR_ENV": "workshop-sim"},
        {"CONDUCTOR_ENV": "workshop"},
        {"CONDUCTOR_ENV": "venue"},
        {"RAILWAY_ENVIRONMENT_NAME": "staging"},
        {},
    ],
    ids=["workshop-sim", "workshop", "venue", "staging", "local"],
)
def test_the_target_is_the_canopy_engine_even_with_conductor_env_workshop(monkeypatch, tmp_path, env):
    """D67: one target. Even the old workshop runtime with both shop kits registered gets the canopy engine."""
    with _client(monkeypatch, tmp_path, _shop_kits(), CONDUCTOR_BASIC_AUTH="crew:canopy", **env) as c:
        r = c.get("/api/kiosk/panel", auth=OPS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target"] == "canopy"
        assert isinstance(GuestPanel.of(_services(c)).engine_target, EngineTarget)
        debug = c.get("/api/kiosk/debug", auth=OPS).json()
    # no engine in these runs: the canopy rests and offers nothing
    assert body["capabilities"] == {"scene": False, "brightness": False, "power": False}
    assert body["state"]["phase"] == "resting" and body["state"]["message"]
    assert debug["target"] == "canopy"
    assert "studio-gradients" not in json.dumps(debug)


def test_the_panel_lists_the_catalogs_scenes(app_client):
    """The six scenes and their 12 review options (D77), in catalog order: nothing is hidden by default."""
    body = _panel(app_client)
    assert [s["id"] for s in body["scenes"]] == [s.id for s in SCENES.scenes]
    for got, scene in zip(body["scenes"], SCENES.scenes):
        assert got["name"] == scene.name and got["mood"] == scene.mood
        assert got["description"] == scene.description and got["brightness_draft"] == scene.brightness_draft
        assert got["palette"] == [{"name": p.name, "hex": p.hex} for p in scene.palette]
        assert got["frame"] == [list(rgb) for rgb in scene.frame(30)] and len(got["frame"]) == 30
    assert body["brightness_range"] == [10, 100] and body["viewer"] == "ops" and body["simulated"] is True
    assert body["state"]["scene"] is None and body["state"]["feature"] is None
    assert isinstance(body["server_now"], float)
    assert app_client.get("/api/kiosk/panel").headers["cache-control"] == "no-store"


def test_writes_are_json_and_same_site_only(app_client):
    _engine(app_client)
    r = app_client.post(
        "/api/kiosk/scene", content=json.dumps({"scene": COASTAL.id}), headers={"Content-Type": "text/plain"}
    )
    assert r.status_code == 415
    r = app_client.post(
        "/api/kiosk/scene", json={"scene": COASTAL.id}, headers={"Origin": "https://evil.example"}
    )
    assert r.status_code == 403
    assert app_client.post("/api/kiosk/scene", json={"scene": COASTAL.id, "x": 1}).status_code == 422
    r = app_client.post("/api/kiosk/scene", json={"scene": "disco-inferno"})
    assert r.status_code == 404 and "isn't one of" in r.json()["detail"]


# ---- the engine target (venue, staging, local) ----------------------------------------------------------------------
def _engine(client, cls=None, **kw):
    """tests/test_kiosk_routes.py's FakeEngine: it knows the modes.yaml ids (the scenes, D59; 18 since D77)."""
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
    r = app_client.post("/api/kiosk/scene", json={"scene": GOLDEN.id})  # the (fake) engine's 8 s cooldown
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


# ---- the canopy picture (conductor/canopy_view.py) -----------------------------------------------------------------
def _lit_settled(c, timeout: float = 15.0, want=("lit",), driven=None) -> dict:
    """The panel once every driven position of its picture reads one of `want`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = _panel(c)
        lit = body["state"]["lit"]
        if lit is not None and all(
            m["state"] in want for m in lit["modules"] if driven is None or m["pos"] in driven
        ):
            return body
        time.sleep(0.05)
    raise AssertionError(f"the picture never settled on {want}: {body['state']}")


def _no_device_names(body: dict) -> None:
    text = json.dumps({"canopy": body["canopy"], "lit": body["state"]["lit"]})
    for word in ("canopy-r", "sim-", "Module ", "reason", "Hubspace", "label", "VWBD", "A-20-1", "drawing"):
        assert word not in text, word


def test_the_engine_panel_serves_the_canopy_and_the_lit_state_of_the_installed_plan(app_client_show):
    from conductor.canopy_view import load_canopy, module_colours

    c = app_client_show
    engine = showing(c)
    body = _lit_settled(c)
    canopy = body["canopy"]
    assert [m["pos"] for m in canopy["modules"]] == [f"r{r}c{col}" for r in range(3) for col in range(3)]
    assert sum(len(m["heads"]) for m in canopy["modules"]) == 270
    assert canopy["view"] == {"mirror_x": False, "rotate_deg": 0, "dim_floor": 0.35}
    # the fins under the lights and the apron's outline (D74); state.lit below is as it always was
    assert len(canopy["fins"]) == 650 and canopy["apron_in"] == 6.0 and canopy["fin_width_in"] == 5.0
    assert canopy["outline"] == {"x": [-6.0, 231.0], "y": [-6.0, 126.0]}
    assert set(canopy["fins"][0]) == {"x", "y", "angle_deg", "drop_in"}
    plan = engine.current_plan()
    cfg = load_canopy(str(_services(c).config_dir))
    by_position = {e.module: e for e in _services(c).registry.canopy_modules()}
    lit = body["state"]["lit"]
    assert lit["level"] == engine.state().brightness and lit["motion"] is None
    for m in lit["modules"]:
        entry = by_position[m["pos"]]
        colours = module_colours(cfg, entry, plan.modules[entry.id].colors)
        assert (m["colors"] or [m["color"]] * 30) == colours, m
    _no_device_names(body)

    assert c.post("/api/kiosk/power", json={"on": False}).status_code == 200
    body = _lit_settled(c, want=("off",))
    assert body["state"]["lit"]["level"] is None and body["state"]["power"] is False
    assert all(m["color"] is None and m["colors"] is None for m in body["state"]["lit"]["modules"])


def test_a_mood_landing_ring_by_ring_is_the_pictures_ripple_until_the_last_ring_is_sent(app_client_show):
    """D64 through the real app: with a stagger set, a scene from the panel goes out ring by ring and the picture
    carries the ripple (positions and times only) until the last ring is sent, then a still picture."""
    c = app_client_show
    engine = showing(c)
    _lit_settled(c)
    _services(c).settings.kiosk.ripple_stagger_s = 1.2
    # every scene steps in sim since D77 (its picture carries steps, not a ripple): the live path, where the stepped
    # rung is refused until Gate R, keeps the ripple this test is about
    scene = next(s for s in ("garden-gathering", "goodnight-glow") if s != engine.current_plan().mode_id)
    refuse_stepped(engine, scene)
    r = c.post("/api/kiosk/scene", json={"scene": scene})
    assert r.status_code == 200, r.text
    body = _panel(c)
    motion = body["state"]["lit"]["motion"]
    plan = engine.current_plan()
    by_id = {e.id: e.module for e in _services(c).registry.canopy_modules()}
    assert motion["kind"] == "ripple" and motion["stagger_s"] == 1.2
    assert motion["rings"] == [[by_id[d] for d in ring] for ring in plan.waves] and len(motion["rings"]) == 3
    assert abs(motion["t0"] - body["server_now"]) < 1.0 and body["state"]["phase"] == "changing"
    _no_device_names(body)
    time.sleep(2 * 1.2 + 0.3)
    assert _panel(c)["state"]["lit"]["motion"] is None


def test_a_stepped_runs_frames_are_the_picture_until_its_still_look_is_sent(app_client_show, monkeypatch):
    """Tier.STEPPED (D65) through the real app: the engine's `program` reaches the picture as `steps` frames
    in grid order, judged at the server's own time, so once the still look is sent (`ends_at`) the picture is still
    even if the engine's state hasn't caught up."""
    import dataclasses

    c = app_client_show
    engine = showing(c)
    lit = _lit_settled(c)["state"]["lit"]
    colours = [m["color"] for m in lit["modules"]]
    real_state = engine.state
    now = _services(c).clock.now()
    program = {"t0": now - 10.0, "step_s": 6.0, "step": 1, "ends_at": now + 80.0, "frames": [colours] * 16}
    monkeypatch.setattr(engine, "state", lambda: dataclasses.replace(real_state(), program=program))
    body = _panel(c)
    motion = body["state"]["lit"]["motion"]
    assert motion["kind"] == "steps" and motion["frames"] == [colours] * 16
    assert motion["ends_at"] == now + 86.0  # one step past the still look's send
    assert abs(motion["t0"] - (body["server_now"] - 10.0)) < 1.0
    _no_device_names(body)
    program["ends_at"] = _services(c).clock.now() - 0.5  # the still look went out half a second ago
    assert _panel(c)["state"]["lit"]["motion"] is None


def test_a_partial_canopy_picture_is_two_lit_and_seven_dark(monkeypatch, tmp_path):
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        engine = showing(c)
        body = _lit_settled(c, want=("lit",), driven={"r0c0", "r0c1"})
        lit = {m["pos"]: m for m in body["state"]["lit"]["modules"]}
        assert [lit[p]["state"] for p in ("r0c0", "r0c1")] == ["lit", "lit"]
        assert all(lit[p]["color"] or lit[p]["colors"] for p in ("r0c0", "r0c1"))
        dark = [m for p, m in lit.items() if p not in ("r0c0", "r0c1")]
        assert len(dark) == 7 and all(m["state"] == "dark" and m["color"] is None for m in dark)
        assert engine.state().phase == CanopyPhase.NEEDS_ATTENTION  # /ops stays loud (D62)
        _no_device_names(body)


def test_a_picture_that_cant_be_worked_out_is_no_picture_never_a_failed_panel(monkeypatch, app_client_show):
    from conductor import canopy_view

    showing(app_client_show)

    def broken(*_a, **_kw):
        raise RuntimeError("test")

    monkeypatch.setattr(canopy_view, "lit_state", broken)
    body = _panel(app_client_show)
    assert body["state"]["lit"] is None and body["state"]["phase"] in ("ready", "cooldown", "changing")


def test_an_engine_with_no_plan_serves_the_canopy_but_no_picture(app_client):
    _engine(app_client)  # tests/test_kiosk_routes.py's FakeEngine: current_plan() is None
    body = _panel(app_client)
    assert len(body["canopy"]["modules"]) == 9 and body["state"]["lit"] is None


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
    # the kiosk reads the canopy picture
    assert len(body["canopy"]["modules"]) == 9 and "lit" in body["state"]
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


# ---- ops entry points --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("page", ["web/ops/index.html", "web/ops/showcase/index.html"])
def test_ops_pages_have_a_guest_panel_button(page):
    html = (ROOT / page).read_text()
    assert 'href="/kiosk/?debug=1"' in html and 'target="_blank"' in html and "Guest panel" in html


def test_a_scene_tapped_during_the_stepped_rehearsal_says_the_canopy_is_being_tested(app_client_show):
    from test_kiosk_routes import _rehearsing

    from conductor.routes.kiosk import TESTING

    c = app_client_show
    route = _rehearsing(c)
    try:
        r = c.post("/api/kiosk/scene", json={"scene": COASTAL.id})
        assert r.status_code == 503 and r.json()["detail"] == TESTING, r.text
        assert 0 < r.json()["retry_after_s"] <= 60
    finally:
        assert c.post(f"{route}/stop", json={}).status_code == 200
