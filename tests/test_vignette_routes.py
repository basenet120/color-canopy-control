"""/vignette/<room>/ and /api/vignette/<room>/* through the real app on the SimDriver (P4; 07 §2).

Covers: the room listing (titles, devices, controls, tracker status, simulated), actions and scenes reaching the
SimDriver and reading back, the lock confirm, plain refusals, JSON-only + same-origin writes, the WebSocket topic,
and principal scoping (a living-room tablet can't touch the entry, and gets 401 on /ops)."""

from __future__ import annotations

import time

import pytest

OPS = ("crew", "canopy")
LIVING = "/api/vignette/living"


def _sim(client):
    return client.app.state.services.sim


def _view(client, device_id: str) -> dict:
    return {r.key: r.value for r in _sim(client).device_view(f"sim-{device_id}")}


def _wait_ready(client, room: str, ids: list[str], timeout: float = 10.0, **kw) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        data = client.get(f"/api/vignette/{room}", **kw).json()
        status = {d["id"]: d["status"] for d in data["devices"]}
        if all(status[i] == "ready" for i in ids) or time.monotonic() > deadline:
            return data
        time.sleep(0.1)


def _device(data: dict, device_id: str) -> dict:
    return next(d for d in data["devices"] if d["id"] == device_id)


# ---- the room listing ---------------------------------------------------------------------------------------------
def test_each_room_lists_its_devices_with_controls_and_an_honest_status(app_client):
    expected = {
        "entry": ["entry-deadbolt"],
        "living": ["living-lamp-1", "living-lamp-2", "living-lamp-3", "living-lamp-4", "living-plug"],
        "bedroom": ["bedroom-lamp-1", "bedroom-lamp-2", "bedroom-lamp-3", "bedroom-lamp-4", "bedroom-plug"],
        "garden": ["garden-string", "garden-path", "garden-plug"],
    }
    titles = {
        "entry": "A smarter welcome",
        "living": "Set the holiday mood",
        "bedroom": "Find your evening glow",
        "garden": "Bring the garden to life",
    }
    for room, ids in expected.items():
        response = app_client.get(f"/api/vignette/{room}")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        data = response.json()
        assert data["title"] == titles[room] and data["simulated"] is True
        assert [d["id"] for d in data["devices"]] == ids  # the unconfirmed fan stays off the page
        for d in data["devices"]:
            assert d["ready"] and d["error"] is None and d["simulated"] is True
            assert d["status"] == "ready", d  # the look each device already shows, read back
            assert d["controls"]["actions"]
    living = app_client.get(LIVING).json()
    lamp = _device(living, "living-lamp-1")
    assert lamp["controls"]["cct"] == {"min": 2200, "max": 6500, "step": 100} and lamp["controls"]["rgb"]
    assert {"name": "christmas", "label": "Christmas"} in lamp["controls"]["effects"]
    plug = _device(living, "living-plug")
    assert plug["controls"]["outlets"] == [
        {"key": "outlet-1", "label": "Tree"},
        {"key": "outlet-2", "label": "Garland"},
    ]
    assert [s["id"] for s in living["scenes"]] == ["holiday-evening", "festive", "all-off"]
    assert app_client.get("/api/vignette/kitchen").status_code == 404


def test_the_page_is_served_for_each_room(app_client):
    for room in ("entry", "living", "bedroom", "garden"):
        page = app_client.get(f"/vignette/{room}/")
        assert page.status_code == 200 and "vignette.js" in page.text and "<a " not in page.text
        assert app_client.get(f"/vignette/{room}/vignette.js").status_code == 200
        assert app_client.get(f"/vignette/{room}/vignette.css").status_code == 200
    moved = app_client.get("/vignette/living", follow_redirects=False)
    assert moved.status_code == 307 and moved.headers["location"] == "/vignette/living/"
    assert app_client.get("/vignette/living/secrets.txt").status_code == 404
    assert app_client.get("/vignette/kitchen/").status_code == 404


# ---- actions reach the SimDriver and read back ----------------------------------------------------------------------
def test_a_colour_change_reaches_the_simulator_and_reads_back(app_client):
    response = app_client.post(
        LIVING + "/action", json={"device_id": "living-lamp-2", "action": "color", "value": "#E0182D"}
    )
    assert response.status_code == 200
    report = response.json()
    assert report["ok"] and report["simulated"] is True
    [result] = report["results"]
    assert result["status"] == "applied" and result["command"]["timings"]["verified_at"] is not None
    assert result["command"]["purpose"] == "engine" and result["command"]["simulated"] is True
    view = _view(app_client, "living-lamp-2")
    assert view[("color-mode", None)] == "color" and view[("color-rgb", None)] == {
        "color-rgb": {"r": 224, "g": 24, "b": 45}
    }
    lamp = _device(app_client.get(LIVING).json(), "living-lamp-2")
    assert lamp["status"] == "ready" and lamp["now"]["hex"] == "#E0182D" and lamp["look"]["mode"] == "color"
    assert _view(app_client, "living-lamp-1")[("color-mode", None)] == "white"  # the others untouched


def test_a_group_action_is_one_command_per_lamp(app_client):
    report = app_client.post(
        LIVING + "/action", json={"group": "bulb", "action": "brightness", "value": 35}
    ).json()
    assert sorted(r["device_id"] for r in report["results"]) == [f"living-lamp-{n}" for n in (1, 2, 3, 4)]
    assert all(r["status"] == "applied" for r in report["results"])
    for n in (1, 2, 3, 4):
        assert _view(app_client, f"living-lamp-{n}")[("brightness", None)] == 35
    assert _view(app_client, "living-plug")[("power", "outlet-1")] == "off"


def test_an_outlet_and_the_fan_less_bedroom(app_client):
    report = app_client.post(
        LIVING + "/action",
        json={"device_id": "living-plug", "action": "power", "outlet": "outlet-2", "value": True},
    ).json()
    assert report["results"][0]["status"] == "applied"
    view = _view(app_client, "living-plug")
    assert view[("power", "outlet-2")] == "on" and view[("power", "outlet-1")] == "off"
    refused = app_client.post(
        "/api/vignette/bedroom/action", json={"device_id": "bedroom-fan", "action": "power", "value": True}
    )
    assert refused.status_code == 404  # disabled until confirmed: not a device in the room


def test_the_lock_needs_confirm_then_reports_unlocked(app_client):
    body = {"device_id": "entry-deadbolt", "action": "unlock"}
    refused = app_client.post("/api/vignette/entry/action", json=body)
    assert refused.status_code == 409 and refused.json()["confirm_required"] is True
    assert "Confirm" in refused.json()["detail"]
    assert _view(app_client, "entry-deadbolt")[("lock-control", None)] == "locked"  # nothing was sent
    done = app_client.post("/api/vignette/entry/action", json={**body, "confirm": True}).json()
    assert done["results"][0]["status"] == "applied"
    assert _view(app_client, "entry-deadbolt")[("lock-control", None)] == "unlocked"
    lock = _device(app_client.get("/api/vignette/entry").json(), "entry-deadbolt")
    assert lock["now"]["position"] == "unlocked" and lock["status"] == "ready"
    group = app_client.post(
        "/api/vignette/entry/action", json={"group": "lock", "action": "lock", "confirm": True}
    )
    assert group.status_code == 422


def test_scenes_send_one_command_per_device(app_client):
    report = app_client.post(LIVING + "/scene", json={"scene": "holiday-evening"}).json()
    assert report["ok"] and report["scene"] == "holiday-evening" and report["skipped"] == []
    assert sorted(r["device_id"] for r in report["results"]) == sorted(
        ["living-lamp-1", "living-lamp-2", "living-lamp-3", "living-lamp-4", "living-plug"]
    )
    for n in (1, 2, 3, 4):
        view = _view(app_client, f"living-lamp-{n}")
        assert view[("color-temperature", None)] == 2700 and view[("brightness", None)] == 60
    plug = _view(app_client, "living-plug")
    assert plug[("power", "outlet-1")] == plug[("power", "outlet-2")] == "on"
    writes = _sim(app_client).stats()["writes_per_device"]
    assert all(writes[f"sim-living-lamp-{n}"] == 1 for n in (1, 2, 3, 4)) and writes["sim-living-plug"] == 1
    off = app_client.post(LIVING + "/scene", json={"scene": "all-off"}).json()
    assert off["ok"] and _view(app_client, "living-lamp-3")[("power", None)] == "off"
    garden = app_client.post("/api/vignette/garden/scene", json={"scene": "garden-party"}).json()
    assert garden["ok"], garden
    assert _view(app_client, "garden-string")[("color-sequence", "preset")] == "rainbow"
    assert app_client.post(LIVING + "/scene", json={"scene": "disco"}).status_code == 404


# ---- refusals -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        (
            {"device_id": "living-lamp-1", "action": "color_temperature", "value": 1800},
            422,
            "outside this device's range",
        ),
        (
            {"device_id": "living-lamp-1", "action": "effect", "value": "halloween"},
            422,
            "isn't one of this device's effects",
        ),
        ({"device_id": "living-plug", "action": "color", "value": "#FF0000"}, 422, "can't do"),
        (
            {"device_id": "garden-string", "action": "power", "value": True},
            404,
            "isn't a device in this room",
        ),
        ({"action": "power", "value": True}, 422, "Name a device"),
        (
            {"device_id": "living-lamp-1", "action": "power", "value": True, "extra": 1},
            422,
            "isn't one this page sends",
        ),
    ],
)
def test_plain_refusals(app_client, body, status, detail):
    response = app_client.post(LIVING + "/action", json=body)
    assert response.status_code == status and detail in response.json()["detail"]


def test_writes_are_json_and_same_origin_only(app_client):
    form = app_client.post(
        LIVING + "/action", data={"device_id": "living-lamp-1", "action": "power", "value": "true"}
    )
    assert form.status_code == 415
    cross = app_client.post(
        LIVING + "/action",
        json={"device_id": "living-lamp-1", "action": "power", "value": False},
        headers={"origin": "https://evil.example"},
    )
    assert cross.status_code == 403
    assert _view(app_client, "living-lamp-1")[("power", None)] == "on"


# ---- live push ----------------------------------------------------------------------------------------------------
def test_tracker_updates_go_out_on_the_rooms_topic(app_client):
    app_client.get("/api/vignette/garden")  # the page reads the room first
    ticket = app_client.get("/api/ws-ticket").json()["ticket"]
    with app_client.websocket_connect("/ws/live") as ws:
        ws.send_json({"type": "auth", "ticket": ticket})
        assert ws.receive_json()["type"] == "hello"
        ws.send_json({"type": "subscribe", "topics": ["vignette:garden"]})
        snap = ws.receive_json()
        assert snap["topic"] == "vignette:garden" and snap["data"]["title"] == "Bring the garden to life"
        assert {d["id"]: d["status"] for d in snap["data"]["devices"]}["garden-path"] == "ready"
        app_client.post(
            "/api/vignette/garden/action",
            json={"device_id": "garden-path", "action": "brightness", "value": 70},
        )
        statuses = []
        while "ready" not in statuses:
            event = ws.receive_json()
            if event.get("type") != "event":
                continue
            assert event["topic"] == "vignette:garden"
            [device] = event["data"]["devices"]
            assert device["id"] == "garden-path"
            statuses.append(device["status"])
        assert statuses[0] == "updating" and statuses[-1] == "ready"


# ---- who may use it -----------------------------------------------------------------------------------------------
def _enroll(client, role: str, grants: list[str]) -> None:
    link = client.post(
        "/api/roles/enrollments", json={"role": role, "name": f"test {role}", "grants": grants}, auth=OPS
    ).json()
    assert client.post(link["path"], follow_redirects=False).status_code == 303


def test_a_living_room_tablet_reaches_only_the_living_room(app_client_auth):
    client = app_client_auth
    assert client.get(LIVING).status_code == 401  # not enrolled yet
    _enroll(client, "vignette", ["living"])
    assert client.get("/vignette/living/").status_code == 200
    assert client.get("/vignette/living/vignette.js").status_code == 200
    assert client.get(LIVING).status_code == 200
    ok = client.post(
        LIVING + "/action", json={"device_id": "living-lamp-1", "action": "brightness", "value": 50}
    )
    assert ok.status_code == 200 and ok.json()["results"][0]["status"] == "applied"
    assert client.get("/api/ws-ticket").status_code == 200
    # another room, and everything ops
    assert client.get("/vignette/entry/").status_code == 401
    assert client.get("/api/vignette/entry").status_code == 401
    unlock = client.post(
        "/api/vignette/entry/action",
        json={"device_id": "entry-deadbolt", "action": "unlock", "confirm": True},
    )
    assert unlock.status_code == 401
    assert _view(client, "entry-deadbolt")[("lock-control", None)] == "locked"
    for path in (
        "/ops",
        "/ops/",
        "/ops/accounts/",
        "/api/devices",
        "/api/bench/jobs",
        "/kiosk/",
        "/api/kiosk/state",
    ):
        assert client.get(path).status_code == 401, path
    # ops can use every room
    assert client.get("/api/vignette/entry", auth=OPS).status_code == 200


def test_other_roles_cant_use_the_vignette_pages(app_client_auth):
    client = app_client_auth
    _enroll(client, "tester", ["bench-kit"])
    assert client.get(LIVING).status_code == 401
    assert client.get("/vignette/living/").status_code == 401


def test_the_route_refuses_a_principal_without_that_room(app_client):
    """Belt and braces behind the middleware: the route itself checks the room grant."""
    from fastapi import HTTPException

    from conductor.roles_api import Principal, Role
    from conductor.routes.vignette import require_room

    class Req:
        def __init__(self, principal: Principal):
            self.scope = {"state": {"principal": principal}}

    tablet = Req(Principal(Role.VIGNETTE, "entry iPad", frozenset({"entry"})))
    assert require_room(tablet, "entry").role is Role.VIGNETTE
    with pytest.raises(HTTPException) as refused:
        require_room(tablet, "living")
    assert refused.value.status_code == 403
    with pytest.raises(HTTPException):
        require_room(Req(Principal(Role.KIOSK, "kiosk")), "living")


def test_a_broken_vignettes_file_turns_the_pages_off_not_the_canopy(monkeypatch, tmp_path):
    import shutil

    from fastapi.testclient import TestClient

    from conductor import config as config_module

    config_dir = tmp_path / "config"
    shutil.copytree(config_module.CONFIG_DIR, config_dir)
    (config_dir / "vignettes.yaml").write_text("rooms: {living: {space: x, title: y, scenes: [{id: s}]}}\n")
    for key in ("CONDUCTOR_BASIC_AUTH", "HUBSPACE_LIVE", "CONDUCTOR_MODE", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CONDUCTOR_AUTH", "off")
    monkeypatch.setenv("CONDUCTOR_ENGINE", "off")
    monkeypatch.setenv("CONDUCTOR_SIM_SPEED", "fast")
    monkeypatch.setenv("CONDUCTOR_DATA_DIR", str(tmp_path / "data"))
    import conductor.services as services_module

    real = services_module.build_services
    monkeypatch.setattr(services_module, "build_services", lambda: real(config_dir=config_dir))
    from conductor.app import app

    with TestClient(app) as client:
        services = client.app.state.services
        assert any("Vignette pages are off" in p for p in services.problems)
        response = client.get(LIVING)
        assert response.status_code == 503 and "config/vignettes.yaml" in response.json()["detail"]
        assert client.get("/healthz").status_code == 200
