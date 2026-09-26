import base64

from fastapi.testclient import TestClient

from conductor.app import app

client = TestClient(app)


def auth(user_pass: str) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(user_pass.encode()).decode()}


def test_health_is_open_even_when_auth_is_on(monkeypatch):
    monkeypatch.setenv("CONDUCTOR_BASIC_AUTH", "crew:canopy")
    assert client.get("/healthz").json() == {"ok": True}


def test_everything_else_requires_the_password_when_auth_is_on(monkeypatch):
    monkeypatch.setenv("CONDUCTOR_BASIC_AUTH", "crew:canopy")
    assert client.get("/api/canopy/layout").status_code == 401
    assert client.get("/api/canopy/layout", headers=auth("crew:wrong")).status_code == 401
    assert client.get("/api/canopy/layout", headers={"Authorization": "Basic %%%"}).status_code == 401
    assert client.get("/api/canopy/layout", headers=auth("crew:canopy")).status_code == 200


def test_layout_reports_heads_and_pitch_checks(monkeypatch):
    monkeypatch.delenv("CONDUCTOR_BASIC_AUTH", raising=False)
    body = client.get("/api/canopy/layout").json()
    assert body["scheme"] == "p14" and len(body["heads"]) == 270 and body["pitch_violations"] == []
    assert client.get("/api/canopy/layout?scheme=p18").json()["pitch_violations"]
    assert client.get("/api/canopy/layout?scheme=nope").status_code == 400


def test_visualizer_and_version_are_served(monkeypatch):
    monkeypatch.delenv("CONDUCTOR_BASIC_AUTH", raising=False)
    assert "The Color Canopy" in client.get("/visualizer/").text
    assert client.get("/api/version").json()["mode"] == "sim"
