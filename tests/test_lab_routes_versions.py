"""The Design Lab's version routes over the store (PLAN §4: /api/lab/versions, /restore, /state), and a corrupt or
unwritable looks.yaml through the real app: the Lab answers with the store's error, the Studio and the Conductor keep
working, and /ops gets one attention line.

S1's draft functions and the T1-lock check are stand-ins here (`test_lab_store.install_fakes`), so these tests pin
the store side of the routes whichever way S1 stands.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml
from test_lab_store import REFUSE, TRIO, FakeS1, install_fakes, pink, seed

from conductor.design_lab import preview as P

SCENES = [s.id for s in TRIO.scenes.scenes]
LAB_ALERT = "Design Lab: overrides/looks.yaml couldn't be read ("


@pytest.fixture
def fakes(monkeypatch) -> FakeS1:
    fake = install_fakes(monkeypatch)
    monkeypatch.setattr(P, "check_t1_lock", lambda lab, draft: None)
    return fake


def data_dir(client) -> Path:
    return Path(client.app.state.services.data)


def looks(client) -> Path:
    return data_dir(client) / "overrides" / "looks.yaml"


def body(draft) -> dict:
    return draft.model_dump(mode="json")


def save(client, drafts: dict | None = None, **extra):
    drafts = drafts if drafts is not None else {"golden-hour": pink()}
    return client.post("/api/lab/versions", json={"drafts": {k: body(v) for k, v in drafts.items()}, **extra})


def attention(client) -> list[str]:
    return [i["message"] for i in client.get("/api/ops/summary").json()["attention"]]


# ---- the round trip --------------------------------------------------------------------------------------------------
def test_save_list_open_restore_and_delete(app_client, fakes):
    r = save(app_client, note="warmer sunset")
    assert r.status_code == 201, r.text
    v1 = r.json()
    assert (
        v1["id"] == "v0001"
        and v1["name"].startswith("Client ")
        and v1["name"].endswith(" v1 – warmer sunset")
    )
    assert v1["scenes"] == ["golden-hour"] and v1["drafts"] == {"golden-hour": body(pink())}
    assert v1["needs_d59"] is True and v1["stale"] is False and v1["older_config"] is False
    assert stat.S_IMODE(looks(app_client).stat().st_mode) == 0o600

    r = save(app_client, {"golden-hour": pink("#FF80B0")}, parent="v0001", name="Paris's pick")
    v2 = r.json()
    assert r.status_code == 201 and v2["parent"] == "v0001" and v2["name"] == "Paris's pick"

    listing = app_client.get("/api/lab/versions").json()
    assert [v["id"] for v in listing["versions"]] == ["v0002", "v0001"] and listing["current"] == "v0002"
    assert app_client.get("/api/lab/versions/v0001").json() == v1
    assert app_client.get("/api/lab/state").json()["current"] == "v0002"

    r = app_client.post("/api/lab/restore", json={"version": "v0001"})  # "Back to previous version"
    assert r.status_code == 200
    restored = r.json()
    assert restored["current"] == "v0001" and list(restored["drafts"]) == SCENES
    assert restored["drafts"]["golden-hour"] == body(pink())
    assert restored["drafts"]["coastal-tides"] == body(seed(TRIO, "coastal-tides"))
    assert yaml.safe_load(looks(app_client).read_text())["current"] == "v0001"

    r = app_client.post("/api/lab/restore", json={"version": "approved"})
    assert r.json()["current"] is None and r.json()["drafts"]["golden-hour"] == body(
        seed(TRIO, "golden-hour")
    )
    assert app_client.get("/api/lab/versions").json()["current"] is None

    app_client.post("/api/lab/restore", json={"version": "v0002"})
    r = app_client.request("DELETE", "/api/lab/versions/v0002", json={})
    assert r.status_code == 200 and r.json() == {"deleted": "v0002", "current": None}
    assert app_client.get("/api/lab/versions/v0002").status_code == 404
    assert app_client.request("DELETE", "/api/lab/versions/v0002", json={}).status_code == 404
    assert app_client.post("/api/lab/restore", json={"version": "v0002"}).status_code == 404
    assert save(app_client, parent="v0002").status_code == 404
    assert [v["id"] for v in app_client.get("/api/lab/versions").json()["versions"]] == ["v0001"]


def test_a_refused_save_is_a_422_by_field_and_stores_nothing(app_client, fakes):
    from test_lab_store import edited

    r = save(app_client, {"golden-hour": edited("golden-hour", description=REFUSE)})
    assert r.status_code == 422
    assert r.json()["problems"] == [
        {"path": "mode 'golden-hour' → description", "field": "description", "message": REFUSE}
    ]
    assert not looks(app_client).exists()
    assert app_client.get("/api/lab/versions").json() == {"versions": [], "current": None}


def test_restoring_a_stale_version_is_a_409_with_its_problems(app_client, fakes):
    save(app_client)
    app_client.post("/api/lab/restore", json={"version": "approved"})
    fakes.refuse_all = True  # the approved config moved on
    r = app_client.post("/api/lab/restore", json={"version": "v0001"})
    assert r.status_code == 409
    answer = r.json()
    assert answer["stale"] is True and answer["problems"][0]["field"] == "description"
    assert app_client.get("/api/lab/versions").json()["current"] is None  # not opened


# ---- a corrupt store -------------------------------------------------------------------------------------------------
def _corrupt(client, text: str = "versions: [unclosed\n") -> Path:
    path = looks(client)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    "text",
    ["versions: [unclosed\n", "format: 2\nversions: []\n", "#" * 1_000_001],
    ids=["garbage", "format-2", "oversize"],
)
def test_a_corrupt_looks_yaml_leaves_the_studio_and_conductor_working(app_client, fakes, text):
    path = _corrupt(app_client, text)  # before the Lab's first request: the store loads on first use
    state = app_client.get("/api/lab/state")
    assert state.status_code == 200
    store = state.json()["store"]
    assert store["ok"] is False and store["read_only"] is False and store["error"]
    assert store["set_aside"].startswith("looks.corrupt-") and store["versions"] == 0
    assert (path.parent / store["set_aside"]).read_text() == text
    assert app_client.get("/api/lab/versions").json() == {"versions": [], "current": None}

    assert app_client.get("/healthz").status_code == 200
    assert app_client.get("/ops/showcase/").status_code == 200
    assert app_client.get("/api/devices").status_code == 200
    (line,) = [m for m in attention(app_client) if m.startswith(LAB_ALERT)]
    assert f"set aside as {store['set_aside']}" in line
    app_client.get("/api/lab/state")
    assert sum(m.startswith(LAB_ALERT) for m in attention(app_client)) == 1  # raised once, not per request

    assert save(app_client).status_code == 201  # the Lab saves again, into a fresh file
    assert yaml.safe_load(path.read_text())["versions"][0]["id"] == "v0001"


def test_a_corrupt_looks_yaml_never_stops_the_show(app_client_show, fakes):
    _corrupt(app_client_show)
    assert app_client_show.get("/api/lab/state").json()["store"]["set_aside"].startswith("looks.corrupt-")
    assert app_client_show.get("/healthz").status_code == 200
    assert app_client_show.get("/api/kiosk/state").status_code == 200
    assert app_client_show.get("/api/lab/state").json()["engine"]["running"] is True


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_read_only_store_refuses_every_write_with_409(app_client, fakes):
    path = _corrupt(app_client)
    path.parent.chmod(0o500)
    try:
        store = app_client.get("/api/lab/state").json()["store"]
        assert store["read_only"] is True and store["ok"] is False and store["set_aside"] is None
        for method, url, payload in [
            ("POST", "/api/lab/versions", {"drafts": {"golden-hour": body(pink())}}),
            ("DELETE", "/api/lab/versions/v0001", {}),
            ("POST", "/api/lab/restore", {"version": "approved"}),
        ]:
            r = app_client.request(method, url, json=payload)
            assert r.status_code == 409, (method, url, r.text)
            assert r.json()["read_only"] is True
            assert r.json()["detail"].startswith("The Lab's store couldn't be set aside: ")
        assert app_client.get("/api/lab/versions").status_code == 200  # reading still works
        assert any("the Lab won't save until it is moved by hand" in m for m in attention(app_client))
    finally:
        path.parent.chmod(0o700)
    assert path.read_text() == "versions: [unclosed\n"  # the evidence is untouched
