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
