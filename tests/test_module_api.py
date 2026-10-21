"""/api/module/* on the real app + the fast SimDriver (BUILD_PLAN P0b): lock, paint, T1 gating, cards, sweep,
hold, slots, effects, calibration, telemetry export and the tester role."""

from __future__ import annotations

import re
import time

import pytest

from conductor import render
from conductor.config import load_recipes

BENCH = "bench-kit"
THREE = [
    {"index": 0, "r": 255, "g": 0, "b": 0},
    {"index": 7, "r": 0, "g": 255, "b": 0},
    {"index": 22, "r": 0, "g": 0, "b": 255},
]


def _open(client, device=BENCH, **body):
    r = client.post(f"/api/module/{device}/open", json=body)
    assert r.status_code == 200, r.text
    return r.json()["lease"], r.json()["state"]


def _post(client, endpoint, lease, /, device=BENCH, **body):
    return client.post(f"/api/module/{device}/{endpoint}", json={"lease": lease, **body})


def _services(client):
    return client.app.state.services


def _module_look(mode_id: str, position: str):
    """The live mode's module-level (T3) look at a canopy position: what the module page sends for that position."""
    from conductor.config import DeviceEntry
    from conductor.looks import CompileContext, load_modes, module_look

    entry = DeviceEntry(id="module-look", role="canopy_module", account="test", module=position)
    return module_look(load_modes().mode(mode_id), entry, CompileContext(registry=[entry], capabilities={}))


@pytest.fixture
def fast(app_client):
    """Shorter readback waits and rate budget for tests that aren't about timing (the sweep test keeps the
    real 1 write/s budget)."""
    policy = _services(app_client).settings.executor
    policy.readback_delay_s, policy.readback_interval_s, policy.min_write_interval_s = 0.05, 0.05, 0.1
    return app_client


def _wait(predicate, timeout=8.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def _ground_truth(client, device=BENCH):
    """What the simulated lights actually show, per head."""
    services = _services(client)
    return render.head_colors(services.sim.device_view(f"sim-{device}"), 30)


# ---- lock: open / conflict / take over / close ----------------------------------------------------------------
def test_open_holds_the_lock_and_close_releases_it(app_client):
    listing = app_client.get("/api/module").json()
    assert listing["devices"][0]["id"] == BENCH and listing["devices"][0]["locked"] is False
    assert listing["can_take_over"] is True
    lease, state = _open(app_client)
    assert state["simulated"] is True and state["lock"]["held"] is True
    assert state["t1"]["allowed"] is True and "Simulator" in state["t1"]["reason"]
    assert len(state["heads"]) == 30 and len(state["map"]["heads"]) == 30
    locked = app_client.get("/api/module").json()["devices"][0]
    assert locked["locked"] is True and locked["holder"] == "module page · ops"

    # someone else (another ops page) can't open it without taking over
    conflict = app_client.post(f"/api/module/{BENCH}/open", json={})
    assert conflict.status_code == 409 and "in use by module page · ops" in conflict.json()["detail"]

    # ops may take over; the old lease stops working at once
    new_lease, _ = _open(app_client, steal=True)
    assert new_lease != lease
    stale = _post(app_client, "paint", lease, heads=THREE)
    assert stale.status_code == 409 and "Open it again" in stale.json()["detail"]
    assert _post(app_client, "heartbeat", lease).status_code == 409
    assert _post(app_client, "heartbeat", new_lease, active=True).json()["lock"]["held"] is True

    # a mutating call without any lease is refused in plain words
    assert _post(app_client, "whole", None, power=True).status_code == 409

    # close with the old lease does nothing; with the live one it releases
    assert app_client.post(f"/api/module/{BENCH}/close", json={"lease": lease}).json()["released"] is False
    beacon = app_client.post(
        f"/api/module/{BENCH}/close",
        content=f'{{"lease": "{new_lease}"}}',
        headers={"content-type": "text/plain;charset=UTF-8"},  # what navigator.sendBeacon sends
    )
    assert beacon.json()["released"] is True
    assert app_client.get("/api/module").json()["devices"][0]["locked"] is False
    assert app_client.get(f"/api/module/{BENCH}").json()["state"]["open"] is False


def test_idle_lock_expires_and_only_active_heartbeats_count_as_use(app_client):
    lease, _ = _open(app_client)
    services = _services(app_client)
    holder = services.locks.holder(f"sim-{BENCH}")
    holder.last_used -= 100
    before = holder.last_used
    assert _post(app_client, "heartbeat", lease, active=False).status_code == 200
    assert services.locks.holder(f"sim-{BENCH}").last_used == before  # a forgotten phone still idles out
    assert _post(app_client, "heartbeat", lease, active=True).status_code == 200
    assert services.locks.holder(f"sim-{BENCH}").last_used > before
    # while a Hold is on (photos being taken) even an idle page keeps the module
    _post(app_client, "hold", lease, on=True)
    services.locks.holder(f"sim-{BENCH}").last_used -= 100
    held = services.locks.holder(f"sim-{BENCH}").last_used
    assert _post(app_client, "heartbeat", lease, active=False).status_code == 200
    assert services.locks.holder(f"sim-{BENCH}").last_used > held
    services.locks.holder(f"sim-{BENCH}").last_used -= services.locks.idle_timeout_s + 1
    gone = _post(app_client, "heartbeat", lease, active=True)
    assert gone.status_code == 409 and "idle" in gone.json()["detail"]
    assert app_client.get("/api/module").json()["devices"][0]["locked"] is False


# ---- paint ------------------------------------------------------------------------------------------------------
def test_paint_three_heads_then_readback_shows_them(app_client):
    lease, state = _open(app_client)
    assert state["paint_slot"] == "custom-3"
    r = _post(app_client, "paint", lease, heads=THREE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["simulated"] is True and body["notice"].startswith("SIMULATED")
    result = body["result"]
    assert result["status"] == "applied" and result["simulated"] is True and result["http_status"] == 200
    assert result["device_id"] == BENCH  # never the driver address
    assert result["send_to_ack_ms"] is not None and result["send_to_readback_ms"] >= result["send_to_ack_ms"]
    expected = {h["index"]: {k: h[k] for k in ("r", "g", "b")} for h in THREE}

    def reported_matches():
        s = app_client.get(f"/api/module/{BENCH}", params={"refresh": 1}).json()["state"]
        return s if all(s["heads"][i]["reported"] == c for i, c in expected.items()) else None

    state = _wait(reported_matches)
    assert state["diff"] == []
    assert all(state["heads"][i]["desired"] == c for i, c in expected.items())
    dark = [h for h in state["heads"] if h["index"] not in expected]
    assert all(h["reported"] == {"r": 0, "g": 0, "b": 0} for h in dark)

    # the lights themselves (simulated ground truth) show the three heads after the simulated apply latency
    def lights_show_them():
        truth = _ground_truth(app_client)
        return truth if all(truth[i] == c for i, c in expected.items()) else None

    truth = _wait(lights_show_them)
    assert truth[0] == expected[0] and truth[22] == expected[22]
    # the telemetry strip has this command and session stats
    telemetry = state["telemetry"]
    assert telemetry["recent"][0]["label"] == "paint → custom-3"
    assert telemetry["session"]["count"] >= 1 and telemetry["session"]["ack_p50_s"] is not None


def test_fill_and_gradient_paint(app_client, fast):
    lease, state = _open(app_client)
    xs = {h["index"]: h["x"] for h in state["map"]["heads"]}
    orange = {"r": 249, "g": 99, "b": 2}
    r = _post(app_client, "paint", lease, base="off", fill={"indices": [1, 2, 3], "color": orange})
    assert r.json()["result"]["status"] == "applied"
    heads = r.json()["state"]["heads"]
    assert [heads[i]["reported"] for i in (1, 2, 3)] == [orange] * 3 and heads[4]["reported"]["r"] == 0
    g = _post(
        app_client,
        "paint",
        lease,
        gradient={"axis": "x", "from": {"r": 255, "g": 0, "b": 0}, "to": {"r": 0, "g": 0, "b": 255}},
    )
    heads = g.json()["state"]["heads"]
    left, right = min(xs, key=xs.get), max(xs, key=xs.get)
    assert heads[left]["reported"] == {"r": 255, "g": 0, "b": 0}
    assert heads[right]["reported"] == {"r": 0, "g": 0, "b": 255}
    bad = _post(app_client, "paint", lease, heads=[{"index": 30, "r": 1}])
    assert bad.status_code == 400 and "0–29" in bad.json()["detail"]
    bad = _post(app_client, "paint", lease, heads=[{"index": 0, "r": 300}])
    assert bad.status_code == 400


def test_t1_is_refused_without_gate_c_on_a_real_driver_but_slot_select_still_works(
    app_client, fast, monkeypatch
):
    services = _services(app_client)
    lease, _ = _open(app_client)
    monkeypatch.setattr(services.sim, "simulated", False)  # looks like the AferoDriver: not simulated
    state = app_client.get(f"/api/module/{BENCH}").json()["state"]
    assert state["t1"]["allowed"] is False and "Gate C" in state["t1"]["reason"]
    refused = _post(app_client, "paint", lease, heads=THREE)
    assert refused.status_code == 409 and "Gate C" in refused.json()["detail"]
    painted = _post(app_client, "card", lease, card="hue-ramp")
    assert painted.status_code == 409
    sweep = _post(app_client, "sweep", lease, action="start")
    assert sweep.status_code == 409
    # T2: selecting a slot painted in the Hubspace app still works
    selected = _post(app_client, "slot", lease, slot="custom-1", action="select")
    assert selected.status_code == 200 and selected.json()["result"]["status"] == "applied"
    whole = _post(app_client, "card", lease, card="white", params={"kelvin": 3000})
    assert whole.json()["result"]["status"] == "applied"
    # a Gate C flag proven on another kit doesn't unlock painting on this one
    services.update_registry(BENCH, {"flags": {"t1_frames": True}, "flags_proven_on": "some-other-kit"})
    assert _post(app_client, "paint", lease, heads=THREE).status_code == 409
    # once bench Gate C is recorded for this kit, painting is allowed
    services.update_registry(BENCH, {"flags_proven_on": "sim-bench-kit"})
    assert _post(app_client, "paint", lease, heads=THREE).json()["result"]["status"] == "applied"


# ---- whole-module controls ----------------------------------------------------------------------------------------
def test_whole_module_controls_validate_against_capabilities(app_client, fast):
    lease, state = _open(app_client)
    caps = state["capabilities"]
    assert caps["cct_range"] == [2700, 6500] and "snowfall" in caps["presets"]
    ok = [
        {"look": {"kind": "white", "cct": 3000, "brightness": 60}},
        {"look": {"kind": "rgb", "rgb": [249, 99, 2], "brightness": 80}},
        {"look": {"kind": "preset", "preset": "snowfall", "speed": -10, "brightness": 70}},
        {"look": {"kind": "program", "program": "dinner-party"}},
        {"brightness": 40},
        {"power": False},
        {"power": True},
    ]
    for body in ok:
        r = _post(app_client, "whole", lease, **body)
        assert r.status_code == 200 and r.json()["result"]["status"] == "applied", (body, r.text)
    off = _post(app_client, "whole", lease, power=False).json()["state"]
    assert all(h["reported"] == {"r": 0, "g": 0, "b": 0} for h in off["heads"])
    bad = [
        {"look": {"kind": "white", "cct": 2200}},
        {"look": {"kind": "preset", "preset": "not-a-preset"}},
        {"look": {"kind": "program", "program": "disco"}},
        {"look": {"kind": "preset", "preset": "snowfall", "speed": 11}},
        {"look": {"kind": "sparkle"}},
        {"brightness": 0},
        {},
    ]
    for body in bad:
        assert _post(app_client, "whole", lease, **body).status_code == 400, body


# ---- cards ------------------------------------------------------------------------------------------------------
def test_every_card_compiles_and_applies(app_client, fast):
    lease, state = _open(app_client)
    cards = {c["id"]: c for c in state["cards"]}
    recipes = load_recipes()
    cases = [("white", {"kelvin": k}) for k in (2700, 3000, 4000)]
    for color in ("hd-orange", "amber", "peach", "warm-ivory"):
        cases += [
            ("color", {"color": color}),
            ("color", {"color": color, "painted": True, "recipe": "rgb_only"}),
        ]
        cases += [("color", {"color": color, "painted": True, "recipe": "blend"})]
    cases += [
        ("recipe-compare", {"color": "amber"}),
        ("thirds", {}),
        (
            "thirds",
            {
                "bands": [
                    {"color": "hd-orange", "recipe": "rgb_only", "brightness": 100},
                    {"color": "peach", "recipe": "blend", "brightness": 60},
                    {"color": "warm-ivory", "recipe": "blend", "brightness": 30},
                ]
            },
        ),
        ("hue-ramp", {}),
        ("single-head", {"index": 12}),
    ]
    assert {c for c, _ in cases} | {"index-sweep", "lowcountry-preview"} == set(cards)
    for card, params in cases:
        r = _post(app_client, "card", lease, card=card, params=params)
        assert r.status_code == 200, (card, params, r.text)
        assert r.json()["result"]["status"] == "applied", (card, params)
    # the last card (single head 12) is what the lights show
    heads = r.json()["state"]["heads"]
    assert [h["index"] for h in heads if h["reported"] != {"r": 0, "g": 0, "b": 0}] == [12]
    # hue ramp: head 0 red, head 10 (120°) green, head 20 (240°) blue
    ramp = _post(app_client, "card", lease, card="hue-ramp").json()["state"]["heads"]
    assert (ramp[0]["reported"], ramp[10]["reported"], ramp[20]["reported"]) == (
        {"r": 255, "g": 0, "b": 0},
        {"r": 0, "g": 255, "b": 0},
        {"r": 0, "g": 0, "b": 255},
    )
    # whole-strand orange is the recipe's strand colour
    strand = _post(app_client, "card", lease, card="color", params={"color": "hd-orange"}).json()
    rgb = recipes.colors["hd-orange"].strand.rgb
    assert strand["state"]["heads"][3]["reported"] == dict(zip("rgb", rgb, strict=True))
    # out-of-range white is clamped with a note, not refused
    clamped = _post(app_client, "card", lease, card="white", params={"kelvin": 2200}).json()["result"]
    assert clamped["status"] == "applied" and "sent 2700 K" in clamped["notes"][0]
    no_mode = _post(app_client, "card", lease, card="lowcountry-preview")
    assert no_mode.status_code == 400 and "choose one of the modes" in no_mode.json()["detail"].lower()
    assert _post(app_client, "card", lease, card="nope").status_code == 400
    assert _post(app_client, "card", lease, card="single-head", params={"index": 99}).status_code == 400


def test_recipe_compare_splits_the_module_in_halves(app_client, fast):
    lease, state = _open(app_client)
    xs = [h["x"] for h in state["map"]["heads"]]
    r = _post(app_client, "card", lease, card="recipe-compare", params={"color": "hd-orange"}).json()
    paint = [h["paint"] for h in r["state"]["heads"]]
    recipes = load_recipes().colors["hd-orange"]
    for x, entry in zip(xs, paint, strict=True):
        if x < 37.5:
            assert (
                entry["whiteBrightness"] == recipes.rgb_only.whiteBrightness
                and entry["g"] == recipes.rgb_only.g
            )
        elif x > 37.5:
            assert entry["whiteBrightness"] == recipes.blend.whiteBrightness and entry["g"] == recipes.blend.g
        else:
            assert entry["colorBrightness"] == 0 and entry["whiteBrightness"] == 0  # the dark divider


def test_lowcountry_preview_shows_a_modes_slice_for_a_canopy_position(app_client, fast):
    from conductor.geometry import load_canopy_config
    from conductor.looks import compose_canvas, load_modes, placement_for

    lease, state = _open(app_client)
    card = {c["id"]: c for c in state["cards"]}["lowcountry-preview"]
    assert (
        not card.get("placeholder") and len(card["options"]["modes"]) == 36 + 6
    )  # D77: the six scenes and their options, plus the Light Show scene and its five
    assert [[p["id"] for p in row] for row in card["options"]["positions"]][1] == ["r1c0", "r1c1", "r1c2"]
    modes, canopy = load_modes(), load_canopy_config()
    bench = _services(app_client).registry.get(BENCH)

    def expected_frame(mode_id, position):
        canvas = modes.mode(mode_id).techniques.canvas.canvas
        placement = placement_for(canopy, bench, frame_length=30, module=position)
        return compose_canvas(canvas, canopy=canopy, recipes=load_recipes(), placements=[placement])[BENCH]

    # T1 on the simulator: that position's frame, written into the page's scratch slot and selected (one PUT)
    r = _post(
        app_client,
        "card",
        lease,
        card="lowcountry-preview",
        params={"mode": "rainbow-row-sunset", "position": "r0c1"},
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["status"] == "applied" and result["tier"] == "canvas" and result["motion"] is False
    assert result["feature"] == modes.mode("rainbow-row-sunset").techniques.canvas.feature
    assert any("scratch slot" in n for n in result["notes"])
    heads = r.json()["state"]["heads"]
    top_row = [h["paint"] for h in heads]
    assert top_row == [e.to_json() for e in expected_frame("rainbow-row-sunset", "r0c1")]
    view = {row.key: row.value for row in _services(app_client).sim.cloud_view(f"sim-{BENCH}")}
    assert view[("color-individual", "custom")] == "custom-3" and view[("brightness", None)] == 85
    _wait(lambda: [h["reported"] for h in heads] == _ground_truth(app_client))  # the lights show it too
    # the right-hand column of the canopy is a different slice of the same gradient (it runs across the canopy)
    right = _post(
        app_client,
        "card",
        lease,
        card="lowcountry-preview",
        params={"mode": "rainbow-row-sunset", "position": "r2c2"},
    )
    assert [h["paint"] for h in right.json()["state"]["heads"]] == [
        e.to_json() for e in expected_frame("rainbow-row-sunset", "r2c2")
    ]
    assert right.json()["state"]["heads"][0]["paint"] != top_row[0]

    # a mode without a canvas shows its module-level look for that position (the moon design at r1c1, D63)
    night = _post(
        app_client,
        "card",
        lease,
        card="lowcountry-preview",
        params={"mode": "goodnight-glow", "position": "r1c1"},
    ).json()
    assert night["result"]["tier"] == "modules" and night["result"]["status"] == "applied"

    # the live mode's module-level look at r1c1 ({191, 178, 233} at 28 % as approved): plumbing, not a pin
    night_look = _module_look("goodnight-glow", "r1c1")

    def night_lavender():
        view = {row.key: row.value for row in _services(app_client).sim.device_view(f"sim-{BENCH}")}
        rgb = view.get(("color-rgb", None)) or {}
        r, g, b = night_look.rgb
        return (
            rgb.get("color-rgb") == {"r": r, "g": g, "b": b}
            and view[("brightness", None)] == night_look.brightness
        )

    _wait(night_lavender)

    # where per-head frames aren't allowed, the module-level (T3) look for that position is shown instead
    session = _services(app_client).extras["module_sessions"][BENCH]
    session.t1_status = lambda: (False, "bench Gate C hasn't passed")
    golden = _post(
        app_client,
        "card",
        lease,
        card="lowcountry-preview",
        params={"mode": "golden-hour", "position": "r1c2"},
    ).json()
    assert golden["result"]["tier"] == "modules" and golden["result"]["status"] == "applied"
    assert any("Gate C" in n for n in golden["result"]["notes"])
    # the design's colour at r1c2, the far side from the sun (approved: 231, 122, 176, rose), at Golden Hour's 95 %
    # ({219, 116, 167} as approved): the live mode's module-level look there, as a strand shows it
    look = _module_look("golden-hour", "r1c2")
    r, g, b = look.rgb
    shown = {
        ("power", None): "on",
        ("color-mode", None): "color",
        ("color-rgb", None): {"color-rgb": {"r": r, "g": g, "b": b}},
        ("brightness", None): look.brightness,
    }
    assert golden["state"]["heads"][4]["reported"] == render.head_colors(shown, 1)[0]

    bad = _post(
        app_client,
        "card",
        lease,
        card="lowcountry-preview",
        params={"mode": "golden-hour", "position": "r5c5"},
    )
    assert bad.status_code == 400 and "outside the canopy" in bad.json()["detail"]
    nope = _post(
        app_client, "card", lease, card="lowcountry-preview", params={"mode": "disco", "position": "r0c0"}
    )
    assert nope.status_code == 400


# ---- sweep ------------------------------------------------------------------------------------------------------
def test_index_sweep_advances_one_head_per_step_within_the_rate_budget(app_client):
    services = _services(app_client)
    services.sim.settings = services.sim.settings.with_(per_device_min_interval_s=1.0)
    lease, state = _open(app_client)
    assert state["settings"]["sweep_step_s"] == 2.0
    r = _post(app_client, "sweep", lease, action="start", step_s=0.2)  # asks for faster than the budget
    assert r.status_code == 200
    assert r.json()["sweep"]["step_s"] == 1.0  # clamped to min_write_interval_s
    writes_before = services.sim.stats()["writes_per_device"].get(f"sim-{BENCH}", 0)

    def sweep_at(n):
        s = app_client.get(f"/api/module/{BENCH}").json()["state"]
        return s if (s["sweep"]["index"] or 0) >= n else None

    s = _wait(lambda: sweep_at(2))
    lit = [h["index"] for h in s["heads"] if h["reported"] != {"r": 0, "g": 0, "b": 0}]
    assert lit == [s["sweep"]["index"]]
    paused = _post(app_client, "sweep", lease, action="pause").json()["sweep"]
    assert paused["state"] == "paused"
    # a step already in flight finishes; after that nothing more is written
    idle = _wait(
        lambda: (s := app_client.get(f"/api/module/{BENCH}").json()["state"]) and not s["busy"] and s
    )
    held_at = idle["sweep"]["index"]
    time.sleep(1.5)
    assert app_client.get(f"/api/module/{BENCH}").json()["state"]["sweep"]["index"] == held_at
    assert _post(app_client, "sweep", lease, action="resume").json()["sweep"]["state"] == "running"
    _wait(lambda: sweep_at(held_at + 1))
    stopped = _post(app_client, "sweep", lease, action="stop").json()["sweep"]
    assert stopped["state"] == "stopped"
    stats = services.sim.stats()
    written = stats["writes_per_device"][f"sim-{BENCH}"] - writes_before
    assert written >= 3
    assert stats["min_interval_violations_total"] == 0  # never faster than 1 write/s to the device
    assert stats["write_429"] == 0
    # sweep writes are recorded one per step, each a full frame + select in one PUT
    labels = [c["label"] for c in s["telemetry"]["recent"] if c["label"].startswith("index sweep")]
    assert labels and all(lbl.startswith("index sweep: head ") for lbl in labels)
    _post(app_client, "close", lease)


def test_sweep_card_starts_the_sweep(app_client):
    lease, _ = _open(app_client)
    r = _post(app_client, "card", lease, card="index-sweep")
    assert r.status_code == 200 and r.json()["sweep"]["state"] == "running"
    assert _post(app_client, "sweep", lease, action="stop").json()["sweep"]["state"] == "stopped"


# ---- hold ---------------------------------------------------------------------------------------------------------
def test_hold_blocks_every_write_until_released(app_client, fast):
    lease, _ = _open(app_client)
    _post(app_client, "card", lease, card="color", params={"color": "amber"})
    held = _post(app_client, "hold", lease, on=True).json()
    assert held["hold"]["on"] is True and held["hold"]["name"] == "Amber whole strand"
    writes = [
        ("paint", {"heads": THREE}),
        ("whole", {"power": False}),
        ("card", {"card": "hue-ramp"}),
        ("sweep", {"action": "start"}),
        ("slot", {"slot": "custom-1", "action": "select"}),
        ("effects", {"slot": "custom-1", "action": "restart"}),
    ]
    for action, body in writes:
        r = _post(app_client, action, lease, **body)
        assert r.status_code == 409 and "Hold is on" in r.json()["detail"], action
    assert _post(app_client, "hold", lease, on=True, name="Mylar B · frosted").json()["hold"]["name"] == (
        "Mylar B · frosted"
    )
    assert _post(app_client, "hold", lease, on=False).json()["hold"]["on"] is False
    assert _post(app_client, "paint", lease, heads=THREE).json()["result"]["status"] == "applied"


def test_hold_pauses_a_running_sweep(app_client):
    lease, _ = _open(app_client)
    _post(app_client, "sweep", lease, action="start")
    state = _post(app_client, "hold", lease, on=True).json()["state"]
    assert state["sweep"]["state"] == "paused"
    assert _post(app_client, "sweep", lease, action="resume").status_code == 409
    _post(app_client, "hold", lease, on=False)
    assert _post(app_client, "sweep", lease, action="resume").json()["sweep"]["state"] == "running"
    _post(app_client, "sweep", lease, action="stop")


# ---- slots and effects ---------------------------------------------------------------------------------------------
def test_slots_read_load_write_and_select(app_client, fast):
    lease, state = _open(app_client)
    assert [s["slot"] for s in state["slots"]] == ["custom-1", "custom-2", "custom-3"]
    assert all(len(s["heads"]) == 30 and s["length"] == 30 and s["hash"] for s in state["slots"])
    read = _post(app_client, "slot", lease, slot="custom-1", action="read").json()["slot"]
    assert read["value"]["color-sequence-v2"]["frameBuffer"]["framebuffer"] and read["hash"]
    loaded = _post(app_client, "slot", lease, slot="custom-1", action="load").json()["state"]
    assert loaded["paint_slot"] == "custom-1"
    _post(app_client, "paint", lease, base="off", heads=THREE, slot="custom-3")
    written = _post(app_client, "slot", lease, slot="custom-2", action="write_current").json()
    assert written["result"]["status"] == "applied"
    slots = {s["slot"]: s for s in written["state"]["slots"]}
    assert slots["custom-2"]["hash"] == slots["custom-3"]["hash"]  # same frame, same canonical hash
    assert slots["custom-3"]["active"] is True  # write_current doesn't select
    selected = _post(app_client, "slot", lease, slot="custom-1", action="select").json()
    assert {s["slot"]: s for s in selected["state"]["slots"]}["custom-1"]["active"] is True
    assert _post(app_client, "slot", lease, slot="custom-9", action="select").status_code == 400


def test_effects_are_off_until_the_bench_enables_codes(app_client, fast):
    services = _services(app_client)
    lease, state = _open(app_client)
    assert state["effects"]["any_enabled"] is False
    assert state["effects"]["message"].startswith("No effect codes enabled yet")
    refused = _post(app_client, "effects", lease, slot="custom-1", action="start", motionEffect=1)
    assert refused.status_code == 409 and "No effect codes enabled yet" in refused.json()["detail"]
    restart = _post(app_client, "effects", lease, slot="custom-1", action="restart")
    assert restart.json()["result"]["status"] == "applied"
    services.settings.effects.enabled["motionEffect"][1] = "scroll (benched)"
    started = _post(
        app_client,
        "effects",
        lease,
        slot="custom-1",
        action="start",
        motionEffect=1,
        speeds={"motionSpeed": 20},
    )
    assert started.json()["result"]["status"] == "applied", started.text
    slot = {s["slot"]: s for s in started.json()["state"]["slots"]}["custom-1"]
    assert slot["effects"]["motionEffect"] == 1 and slot["effects"]["motionSpeed"] == 20
    not_enabled = _post(app_client, "effects", lease, slot="custom-1", action="start", motionEffect=2)
    assert not_enabled.status_code == 400
    bad_speed = _post(
        app_client, "effects", lease, slot="custom-1", action="start", speeds={"motionSpeed": 500}
    )
    assert bad_speed.status_code == 400


# ---- calibration ------------------------------------------------------------------------------------------------
def test_calibration_is_saved_to_the_overlay_and_reflected_in_the_map(app_client):
    services = _services(app_client)
    lease, state = _open(app_client)
    assert state["map"]["calibration"]["calibrated"] is False
    r = _post(
        app_client,
        "calibration",
        lease,
        cable_path="serpentine_cols",
        start_corner="top-right",
        orientation="B",
    )
    assert r.status_code == 200, r.text
    cal = r.json()["map"]["calibration"]
    assert cal == {**cal, "orientation": "B", "cable_path": "serpentine_cols", "start_corner": "top-right"}
    assert cal["start_corner_as_shown"] == "bottom-left"
    first = r.json()["state"]["map"]["heads"][0]
    assert (first["ax"], first["ay"]) == (72.5, 5.0) and (first["x"], first["y"]) == (2.5, 35.0)
    entry = services.registry.get(BENCH)
    assert (entry.cable_path, entry.start_corner, entry.orientation) == ("serpentine_cols", "top-right", "B")
    overlay = (services.data / "overrides" / "registry.yaml").read_text()
    assert "serpentine_cols" in overlay and "top-right" in overlay
    # the map a fresh GET builds (no session) reads the same calibration
    _post(app_client, "close", lease)
    closed = app_client.get(f"/api/module/{BENCH}").json()["state"]
    assert closed["open"] is False and closed["map"]["heads"][0]["x"] == 2.5
    # flip: index 0 moves to the other end, saved as an explicit wire order
    lease, state = _open(app_client)
    order = [(h["ax"], h["ay"]) for h in state["map"]["heads"]]
    flipped = _post(
        app_client,
        "calibration",
        lease,
        cable_path="serpentine_cols",
        start_corner="top-right",
        orientation="B",
        flip=True,
    ).json()["map"]
    assert [(h["ax"], h["ay"]) for h in flipped["heads"]] == order[::-1]
    assert services.registry.get(BENCH).wire_order is not None and flipped["hops_fit"] is True
    bad = _post(
        app_client, "calibration", lease, cable_path="zigzag", start_corner="top-left", orientation="A"
    )
    assert bad.status_code == 400
    bad = _post(
        app_client,
        "calibration",
        lease,
        cable_path="serpentine_rows",
        start_corner="top-left",
        orientation="A",
        wire_order=[[2.5, 5.0]],
    )
    assert bad.status_code == 400 and "exactly once" in bad.json()["detail"]


def test_a_frame_shorter_than_the_pucks_still_refuses_per_head_painting(app_client, fast):
    """A 20-entry frame can't hold a module's 30 pucks: per-head painting and every per-head card are refused;
    whole-strand looks still work."""
    services = _services(app_client)
    services.update_registry("bench-kit", {"profile": "string-20seg"})
    services.sim.add_device(f"sim-{BENCH}", account="bench", profile="string-20seg")
    lease, state = _open(app_client)
    assert state["head_count"]["problem"] == "the device reports 20 heads but this module should have 30"
    assert state["t1"]["allowed"] is False and "Per-head painting is off" in state["t1"]["reason"]
    for card, params in (("hue-ramp", {}), ("single-head", {"index": 0}), ("recipe-compare", {})):
        r = _post(app_client, "card", lease, card=card, params=params)
        assert r.status_code == 409 and "should have 30" in r.json()["detail"], card
    assert _post(app_client, "paint", lease, heads=THREE).status_code == 409
    assert _post(app_client, "card", lease, card="white", params={"kelvin": 2700}).status_code == 200


# ---- telemetry export and the live socket ------------------------------------------------------------------------
def test_export_is_anonymized(app_client, fast):
    lease, _ = _open(app_client)
    _post(app_client, "paint", lease, heads=THREE)
    export = app_client.get(f"/api/module/{BENCH}/export")
    text = export.text
    assert export.status_code == 200 and "sim-bench-kit" not in text and "device-1" in text
    assert '"owner"' not in text and '"account"' not in text
    body = export.json()
    assert body["simulated"] is True and body["telemetry"]["recent"][0]["status"] == "applied"


def test_live_socket_gets_a_snapshot_and_updates(app_client):
    lease, _ = _open(app_client)
    ticket = app_client.get("/api/ws-ticket").json()["ticket"]
    with app_client.websocket_connect("/ws/live") as ws:
        ws.send_json({"type": "auth", "ticket": ticket})
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "subscribe", "topics": [f"module:{BENCH}"]})
        snap = ws.receive_json()
        assert snap["topic"] == f"module:{BENCH}" and snap["data"]["state"]["open"] is True
        _post(app_client, "hold", lease, on=True, name="photo")
        event = ws.receive_json()
        assert event["data"]["event"] == "hold" and event["data"]["state"]["hold"]["name"] == "photo"


# ---- the tester role ----------------------------------------------------------------------------------------------
@pytest.fixture
def tester(app_client_auth):
    ops = ("crew", "canopy")
    link = app_client_auth.post(
        "/api/roles/enrollments",
        json={"role": "tester", "name": "VWBD shop phone", "grants": [BENCH]},
        auth=ops,
    ).json()
    assert app_client_auth.post(link["path"], follow_redirects=False).status_code == 303
    return app_client_auth


def test_tester_reaches_only_its_granted_device(tester):
    listing = tester.get("/api/module").json()
    assert [d["id"] for d in listing["devices"]] == [BENCH] and listing["can_take_over"] is False
    assert tester.get("/ops/module/").status_code == 200
    lease, state = _open(tester)
    assert state["lock"]["owner"] == "module page · VWBD shop phone"
    assert _post(tester, "paint", lease, heads=THREE).json()["result"]["status"] == "applied"
    # the same enrolled phone re-opening (a reload before the close beacon lands) reclaims its own lock
    again, _ = _open(tester)
    assert again != lease
    # other devices, ops pages and take-over are out of reach
    assert tester.post("/api/module/canopy-r0c0/open", json={}).status_code in (401, 403)
    assert tester.get("/api/module/canopy-r0c0").status_code in (401, 403)
    assert tester.get("/ops/bench/").status_code == 401
    assert tester.get("/ops/accounts/").status_code == 401
    assert tester.get("/api/bench/jobs").status_code == 401
    assert tester.post(f"/api/module/{BENCH}/open", json={"steal": True}).status_code == 403
    # ops sees who holds it
    ops_view = tester.get("/api/module", auth=("crew", "canopy")).json()["devices"]
    assert {d["id"]: d for d in ops_view}[BENCH]["holder"] == "module page · VWBD shop phone"
    conflict = tester.post(f"/api/module/{BENCH}/open", json={}, auth=("crew", "canopy"))
    assert conflict.status_code == 409 and "VWBD shop phone" in conflict.json()["detail"]


def test_ops_page_needs_the_ops_password(app_client_auth):
    assert app_client_auth.get("/ops/module/").status_code == 401
    assert app_client_auth.get("/api/module").status_code == 401
    assert app_client_auth.get("/ops/module/", auth=("crew", "canopy")).status_code == 200


# ---- the page itself ------------------------------------------------------------------------------------------------
def test_module_page_is_served_vendored_and_badged(app_client):
    html = app_client.get("/ops/module/")
    assert html.status_code == 200 and "Single module" in html.text
    for asset in ("/static/base.css", "/ops/module/module.css", "/ops/module/module.js"):
        assert asset in html.text
        assert app_client.get(asset).status_code == 200
    js = app_client.get("/ops/module/module.js").text
    css = app_client.get("/ops/module/module.css").text
    # no CDN, no web fonts: the imports are the shared kit and the page's own vendored preview module (served
    # next to it, with no imports of its own); the only URL is the SVG namespace
    imports = re.findall(r"^import .* from \"([^\"]+)\";", js, re.MULTILINE)
    assert imports[0] == "/static/app.js" and len(imports) == 2
    assert re.fullmatch(r"\./preset-preview\.js(\?v=[\w.-]+)?", imports[1])
    preview = app_client.get("/ops/module/" + imports[1].removeprefix("./"))
    assert preview.status_code == 200 and not re.findall(r"^import ", preview.text, re.MULTILINE)
    urls = re.findall(r"https?://[^\s\"'`]+", html.text + js + css + preview.text)
    assert set(urls) <= {"http://www.w3.org/2000/svg"}
    assert "@import" not in css and "@font-face" not in css
    # the Simulator badge, the lease-on-exit beacon, the heartbeat and the live topic are wired up
    for needle in (
        "setSimBadge(st.simulated === true)",  # the badge follows the open device (review F4)
        "sendBeacon",
        "pagehide",
        "heartbeat",
        "connectLive",
        "`module:${S.deviceId}`",
    ):
        assert needle in js


# ---- cross-site requests (review F5) -----------------------------------------------------------------------------
def test_cross_site_form_post_cannot_take_a_lock(app_client):
    """F5: a plain HTML form on another site (text/plain or urlencoded, empty body) must not take a device lock."""
    services = _services(app_client)
    url = f"/api/module/{BENCH}/open"
    form = {"content-type": "application/x-www-form-urlencoded"}
    assert (
        app_client.post(url, content=b"", headers={**form, "origin": "https://evil.example"}).status_code
        == 403
    )
    assert app_client.post(url, content=b"", headers=form).status_code == 415
    assert app_client.post(url, content=b"", headers={"content-type": "text/plain"}).status_code == 415
    assert app_client.post(url, content=b"").status_code == 415  # no content type at all
    json_foreign = app_client.post(url, json={}, headers={"origin": "https://evil.example"})
    assert json_foreign.status_code == 403
    assert app_client.post(url, json={}, headers={"referer": "https://evil.example/x"}).status_code == 403
    assert app_client.post(url, json={}, headers={"origin": "null"}).status_code == 403
    assert not services.locks.is_locked(f"sim-{BENCH}")

    same = app_client.post(url, json={}, headers={"origin": "http://testserver"})
    assert same.status_code == 200
    lease = same.json()["lease"]
    # every other mutating call needs JSON too
    for endpoint in ("heartbeat", "hold", "paint"):
        r = app_client.post(
            f"/api/module/{BENCH}/{endpoint}",
            content=f'{{"lease": "{lease}", "on": true}}'.encode(),
            headers={"content-type": "text/plain"},
        )
        assert r.status_code == 415, endpoint
    assert _services(app_client).extras["module_sessions"][BENCH].hold["on"] is False

    # the close beacon may be text/plain (sendBeacon can't send JSON headers) but needs the lease and a same-site
    # origin; a wrong lease releases nothing
    close = f"/api/module/{BENCH}/close"
    beacon = f'{{"lease": "{lease}"}}'.encode()
    foreign = app_client.post(
        close, content=beacon, headers={"content-type": "text/plain", "origin": "https://evil.example"}
    )
    assert foreign.status_code == 403 and services.locks.is_locked(f"sim-{BENCH}")
    wrong = app_client.post(close, content=b'{"lease": "nope"}', headers={"content-type": "text/plain"})
    assert wrong.json()["released"] is False and services.locks.is_locked(f"sim-{BENCH}")
    ok = app_client.post(
        close,
        content=beacon,
        headers={"content-type": "text/plain;charset=UTF-8", "origin": "http://testserver"},
    )
    assert ok.status_code == 200 and ok.json()["released"] is True
    assert (
        app_client.get(f"/api/module/{BENCH}", headers={"origin": "https://evil.example"}).status_code == 200
    )
