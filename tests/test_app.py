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


def test_the_layout_names_vwbd_panels(monkeypatch):
    """VWBD's panel number per position (D74), for labelling kits at install: per position, so any scheme."""
    from conductor.geometry import load_canopy_config

    monkeypatch.delenv("CONDUCTOR_BASIC_AUTH", raising=False)
    want = load_canopy_config()["vwbd_panels"]
    for url in ("/api/canopy/layout", "/api/canopy/layout?scheme=p18"):
        modules = client.get(url).json()["modules"]
        assert {m["id"]: m["vwbd_panel"] for m in modules} == want
    assert want["r0c0"] == "P-03" and want["r2c0"] == "P-01"


def test_visualizer_and_version_are_served(monkeypatch):
    monkeypatch.delenv("CONDUCTOR_BASIC_AUTH", raising=False)
    assert "The Color Canopy" in client.get("/visualizer/").text
    assert client.get("/api/version").json()["mode"] == "sim"


def test_uvicorn_access_log_never_records_an_enrollment_token(app_client):
    """F6: uvicorn's access log writes the request line through its own logger; the one-time link's token (a
    credential for 24 h under D10) must be masked there, with the log line otherwise intact."""
    import io
    import logging

    from uvicorn.logging import AccessFormatter

    token = "Zq3-enrollment_token_ABCDEFGHIJ"
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    )
    access = logging.getLogger("uvicorn.access")  # the app's lifespan (app_client) has installed the filters
    access.addHandler(handler)
    old_level, access.level = access.level, logging.INFO
    try:
        for method, path in (("GET", f"/enroll/{token}"), ("POST", f"/enroll/{token}?x=1"), ("GET", "/ops/")):
            access.info('%s - "%s %s HTTP/%s" %d', "10.0.0.5:5555", method, path, "1.1", 200)
    finally:
        access.removeHandler(handler)
        access.level = old_level
    text = stream.getvalue()
    assert token not in text
    assert '"GET /enroll/*** HTTP/1.1" 200' in text and '"POST /enroll/***?x=1 HTTP/1.1" 200' in text
    assert '"GET /ops/ HTTP/1.1" 200' in text
