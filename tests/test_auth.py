import base64

import pytest
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conductor.auth import BasicAuthMiddleware, check_auth_configuration

CREDENTIALS = {"Authorization": "Basic " + base64.b64encode(b"crew:canopy").decode()}


async def hello(websocket):
    await websocket.accept()
    await websocket.send_text("hello")
    await websocket.close()


ws_client = TestClient(BasicAuthMiddleware(Starlette(routes=[WebSocketRoute("/ws", hello)])))


def test_websocket_without_credentials_is_refused(monkeypatch):
    monkeypatch.setenv("CONDUCTOR_BASIC_AUTH", "crew:canopy")
    with pytest.raises(WebSocketDisconnect) as refused, ws_client.websocket_connect("/ws"):
        pass
    assert refused.value.code == 1008


def test_websocket_with_credentials_is_accepted(monkeypatch):
    monkeypatch.setenv("CONDUCTOR_BASIC_AUTH", "crew:canopy")
    with ws_client.websocket_connect("/ws", headers=CREDENTIALS) as ws:
        assert ws.receive_text() == "hello"


@pytest.mark.parametrize(
    "env",
    [
        {"RAILWAY_ENVIRONMENT_NAME": "staging"},
        {"RAILWAY_ENVIRONMENT_NAME": "staging", "CONDUCTOR_AUTH": "off"},
        {"HUBSPACE_LIVE": "1", "CONDUCTOR_AUTH": "off"},
        {"CONDUCTOR_MODE": "bench"},
        {"CONDUCTOR_BASIC_AUTH": "no-colon"},
    ],
)
def test_refuses_to_start_without_credentials_when_deployed_live_or_not_sim(env):
    with pytest.raises(RuntimeError):
        check_auth_configuration(env)


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"CONDUCTOR_MODE": "bench", "CONDUCTOR_AUTH": "off"},
        {"RAILWAY_ENVIRONMENT_NAME": "staging", "CONDUCTOR_BASIC_AUTH": "crew:canopy"},
    ],
)
def test_starts_when_credentials_are_set_or_not_needed(env):
    check_auth_configuration(env)


# ---- venue F9: an unenrolled kiosk never shows Chromium's sign-in dialog ---------------------------------------------
async def _ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"canopy"})


http_client = TestClient(BasicAuthMiddleware(_ok, principal_resolver=lambda session_id: None))


@pytest.mark.parametrize("path", ["/kiosk", "/kiosk/", "/kiosk/kiosk.js", "/kiosk/index.html"])
def test_an_unenrolled_kiosk_gets_a_calm_page_without_a_basic_challenge(monkeypatch, path):
    monkeypatch.setenv("CONDUCTOR_BASIC_AUTH", "crew:canopy")
    for cookies in ({}, {"cc_session": "revoked-or-expired"}):
        http_client.cookies.clear()
        for name, value in cookies.items():
            http_client.cookies.set(name, value)
        page = http_client.get(path)
        assert page.status_code == 401
        assert "www-authenticate" not in page.headers, (
            "Chromium would show a sign-in dialog on the touchscreen"
        )
        assert page.headers["content-type"].startswith("text/html")
        assert page.headers["cache-control"] == "no-store"
        assert "This screen is not set up yet" in page.text and "ask the canopy team" in page.text
        assert "canopy" != page.text  # the app never ran
        assert not any(word in page.text for word in ("href=", "src=", "http://", "https://")), (
            "self-contained"
        )
    http_client.cookies.clear()


@pytest.mark.parametrize(
    "path", ["/ops/", "/api/kiosk/state", "/kiosker", "/visualizer/", "/", "/kiosk/../ops/", "/KIOSK/"]
)
def test_every_other_path_keeps_the_basic_challenge(monkeypatch, path):
    monkeypatch.setenv("CONDUCTOR_BASIC_AUTH", "crew:canopy")
    denied = http_client.get(path)
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"] == 'Basic realm="Color Canopy"'
    assert denied.text == ""


def test_the_ops_credential_still_reaches_the_kiosk_page(monkeypatch):
    monkeypatch.setenv("CONDUCTOR_BASIC_AUTH", "crew:canopy")
    assert http_client.get("/kiosk/", headers=CREDENTIALS).text == "canopy"
