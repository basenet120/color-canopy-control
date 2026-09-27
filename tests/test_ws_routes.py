"""Core routes + the extended auth middleware (07 §2, D4, D5): tickets, /ws/live, enrollment, cookie principals."""

import base64
from types import SimpleNamespace
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conductor.auth import BasicAuthMiddleware, _open_without_credentials
from conductor.roles import RoleStore
from conductor.roles_api import OPS, SESSION_COOKIE, Principal, Role
from conductor.routes import core
from conductor.routes.deps import principal_of, require_device_access, require_ops, services_of
from conductor.ws import WsHub
from conductor.wsauth import TicketBook

BASIC = {"Authorization": "Basic " + base64.b64encode(b"crew:canopy").decode()}


def make_app(tmp_path, resolver=None):
    store = RoleStore(tmp_path)
    tickets = TicketBook()
    hub = WsHub(tickets, hello=lambda: {"mode": "sim", "simulated": True})
    hub.register_snapshot("module", lambda topic: {"snapshot": topic})
    app = FastAPI()
    app.state.services = SimpleNamespace(roles=store, tickets=tickets, hub=hub)
    app.include_router(core.router)

    @app.get("/ops/accounts")
    async def ops_accounts() -> dict:
        return {"page": "accounts"}

    @app.post("/api/module/{device_id}/paint")
    async def paint(device_id: str, who: Annotated[Principal, Depends(require_device_access)]) -> dict:
        return {"device": device_id, "role": who.role.value}

    @app.get("/api/module/{device_id}/ops-only")
    async def ops_only(device_id: str, _ops: Annotated[Principal, Depends(require_ops)]) -> dict:
        return {"device": device_id}

    @app.get("/api/whoami")
    async def whoami(request: Request) -> dict:
        return {"role": principal_of(request).role.value}

    app.add_middleware(
        BasicAuthMiddleware,
        principal_resolver=resolver or (lambda sid: app.state.services.roles.principal(sid)),
    )
    return app, store


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("CONDUCTOR_BASIC_AUTH", "crew:canopy")


def enroll(client: TestClient, store: RoleStore, role="tester", grants=("bench-kit",)):
    _, path = store.create_enrollment(role, "VWBD shop phone", grants)
    return path, client.post(path, follow_redirects=False)


# ---- /ws/live ---------------------------------------------------------------------------------------------------
def test_live_socket_without_a_ticket_is_closed_1008(tmp_path, auth_on):
    app, _ = make_app(tmp_path)
    client = TestClient(app)
    for first in (
        {"type": "subscribe", "topics": ["canopy"]},
        {"type": "auth"},
        {"type": "auth", "ticket": "x"},
    ):
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json(first)
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_json()
            assert closed.value.code == 1008


def test_live_socket_from_a_foreign_origin_is_refused(tmp_path, auth_on):
    app, _ = make_app(tmp_path)
    client = TestClient(app)
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        client.websocket_connect("/ws/live", headers={"origin": "https://evil.example"}),
    ):
        pass
    assert refused.value.code == 1008


def test_ops_ticket_opens_the_socket_once(tmp_path, auth_on):
    app, _ = make_app(tmp_path)
    client = TestClient(app)
    assert client.get("/api/ws-ticket").status_code == 401
    response = client.get("/api/ws-ticket", headers=BASIC)
    assert response.headers["cache-control"] == "no-store"
    ticket = response.json()
    assert ticket["expires_in"] == 30
    with client.websocket_connect("/ws/live", headers={"origin": "http://testserver"}) as ws:
        ws.send_json({"type": "auth", "ticket": ticket["ticket"]})
        assert ws.receive_json() == {"type": "hello", "role": "ops", "mode": "sim", "simulated": True}
    with client.websocket_connect("/ws/live") as ws:  # single use
        ws.send_json({"type": "auth", "ticket": ticket["ticket"]})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


def test_tester_ticket_only_watches_granted_modules(tmp_path, auth_on):
    app, store = make_app(tmp_path)
    client = TestClient(app)
    enroll(client, store)
    ticket = client.get("/api/ws-ticket").json()["ticket"]
    with client.websocket_connect("/ws/live") as ws:
        ws.send_json({"type": "auth", "ticket": ticket})
        assert ws.receive_json()["role"] == "tester"
        ws.send_json({"type": "subscribe", "topics": ["module:other", "module:bench-kit"]})
        assert ws.receive_json() == {"type": "error", "message": "not allowed to watch module:other"}
        assert ws.receive_json() == {
            "type": "event",
            "topic": "module:bench-kit",
            "data": {"snapshot": "module:bench-kit"},
        }


# ---- enrollment -------------------------------------------------------------------------------------------------
def test_enrollment_link_confirms_then_sets_an_httponly_cookie_and_redirects(tmp_path, auth_on):
    app, store = make_app(tmp_path)
    client = TestClient(app)
    _, path = store.create_enrollment("tester", "VWBD shop phone", ["bench-kit"])
    # Opening the link (or a link preview fetching it) only shows what it will do; it doesn't use it up.
    for _ in range(2):
        page = client.get(path)
        assert page.status_code == 200 and "Set up this device" in page.text and "bench-kit" in page.text
        assert page.headers["cache-control"] == "no-store"
        assert page.headers["referrer-policy"] == "no-referrer"
    response = client.post(path, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/ops/module/"
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    for attribute in ("HttpOnly", "SameSite=strict", "Path=/", "Max-Age=2592000"):
        assert attribute in cookie
    assert "Secure" not in cookie  # plain http here; https requests get Secure
    assert client.post("/api/module/bench-kit/paint").json()["role"] == "tester"
    assert client.get("/api/whoami").status_code == 401  # not in the tester's path table


def test_enrollment_cookie_is_secure_on_https(tmp_path, auth_on):
    app, store = make_app(tmp_path)
    client = TestClient(app, base_url="https://canopy.example")
    _, response = enroll(client, store, role="kiosk", grants=())
    assert response.headers["location"] == "/kiosk/"
    assert "Secure" in response.headers["set-cookie"]


def test_a_used_or_bogus_link_shows_a_friendly_page(tmp_path, auth_on):
    app, store = make_app(tmp_path)
    client = TestClient(app)
    path, first = enroll(client, store)
    assert first.status_code == 303
    for method in (client.get, client.post):
        for url in (path, "/enroll/not-a-real-link"):
            page = method(url, follow_redirects=False)
            assert page.status_code == 404
            assert "This link can't be used" in page.text
            assert "set-cookie" not in page.headers
            assert page.headers["cache-control"] == "no-store"


# ---- cookie principals and the path table -----------------------------------------------------------------------
def test_tester_cookie_reaches_only_its_paths(tmp_path, auth_on):
    app, store = make_app(tmp_path)
    client = TestClient(app)
    enroll(client, store)
    assert client.post("/api/module/bench-kit/paint").json() == {"device": "bench-kit", "role": "tester"}
    assert client.post("/api/module/other/paint").status_code == 401
    assert client.get("/api/module/bench-kit/ops-only").status_code == 403  # the route's own ops check
    denied = client.get("/ops/accounts")
    assert denied.status_code == 401
    assert denied.headers["www-authenticate"].startswith("Basic")  # ops can still sign in on this device
    assert client.get("/api/roles/sessions").status_code == 401
    assert client.post("/api/roles/enrollments", json={"role": "kiosk"}).status_code == 401
    # The ops password on the same device still gets everything.
    assert client.get("/ops/accounts", headers=BASIC).status_code == 200


def test_revoked_session_loses_access(tmp_path, auth_on):
    app, store = make_app(tmp_path)
    client = TestClient(app)
    enroll(client, store)
    fp = client.get("/api/roles/sessions", headers=BASIC).json()["sessions"][0]["fingerprint"]
    assert client.delete(f"/api/roles/sessions/{fp}", headers=BASIC).json() == {"revoked": fp}
    assert client.delete(f"/api/roles/sessions/{fp}", headers=BASIC).status_code == 404
    assert client.get("/api/ws-ticket").status_code == 401


def test_a_failing_resolver_fails_closed(tmp_path, auth_on):
    def broken(_sid):
        raise RuntimeError("store unavailable")

    app, _ = make_app(tmp_path, resolver=broken)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "anything")
    assert client.get("/api/ws-ticket").status_code == 401


# ---- ops management -----------------------------------------------------------------------------------------------
def test_ops_creates_lists_and_cancels_links(tmp_path, auth_on):
    app, _ = make_app(tmp_path)
    client = TestClient(app)
    created = client.post(
        "/api/roles/enrollments",
        json={"role": "tester", "name": "VWBD shop phone", "grants": ["bench-kit"]},
        headers=BASIC,
    )
    assert created.status_code == 201 and created.headers["cache-control"] == "no-store"
    body = created.json()
    assert body["path"].startswith("/enroll/") and body["expires_at"] > 0
    assert body["url"] == "http://testserver" + body["path"]
    listing = client.get("/api/roles/sessions", headers=BASIC).json()
    assert listing["sessions"] == [] and [e["fingerprint"] for e in listing["enrollments"]] == [
        body["fingerprint"]
    ]
    assert body["path"].removeprefix("/enroll/") not in str(listing)
    bad = client.post("/api/roles/enrollments", json={"role": "ops"}, headers=BASIC)
    assert bad.status_code == 400 and "ops password" in bad.json()["detail"]
    assert client.delete(f"/api/roles/enrollments/{body['fingerprint']}", headers=BASIC).status_code == 200
    assert client.post(body["path"], follow_redirects=False).status_code == 404


def test_open_prefixes_dont_open_anything_else(tmp_path, auth_on):
    app, _ = make_app(tmp_path)
    client = TestClient(app)
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guarded = TestClient(BasicAuthMiddleware(inner))
    for path in ("/enroll/../ops/accounts", "/enroll/", "/enroll/a/b", "/ws/live", "/ws/../ops"):
        assert guarded.get(path).status_code == 401, path
    assert guarded.get("/enroll/some-token").status_code == 200
    assert seen == ["/enroll/some-token"]
    # httpx normalizes dot segments before sending, so check the raw-path rule directly too.
    assert not _open_without_credentials("http", "/enroll/../ops/accounts")
    assert not _open_without_credentials("http", "/enroll/%2e%2e/ops")
    assert not _open_without_credentials("websocket", "/ws/../ops")
    assert not _open_without_credentials("http", "/ws/live")  # only WebSockets skip the password under /ws/
    assert _open_without_credentials("websocket", "/ws/live")
    # A cross-site form can't create links with the browser's cached ops password: JSON only.
    form = client.post(
        "/api/roles/enrollments",
        content='{"role": "kiosk"}',
        headers={**BASIC, "content-type": "text/plain"},
    )
    assert form.status_code == 422


def test_auth_off_means_everyone_is_ops(tmp_path, monkeypatch):
    monkeypatch.delenv("CONDUCTOR_BASIC_AUTH", raising=False)
    app, _ = make_app(tmp_path)
    client = TestClient(app)
    ticket = client.get("/api/ws-ticket").json()["ticket"]
    with client.websocket_connect("/ws/live") as ws:
        ws.send_json({"type": "auth", "ticket": ticket})
        assert ws.receive_json()["role"] == "ops"
    assert client.get("/api/module/other/ops-only").status_code == 200


def test_deps_without_middleware(tmp_path, monkeypatch):
    monkeypatch.setenv("CONDUCTOR_BASIC_AUTH", "crew:canopy")
    app = FastAPI()
    tester = Principal(Role.TESTER, "t", frozenset({"bench-kit"}))

    def request(principal=None):
        scope = {"type": "http", "app": app, "headers": [], "state": {}}
        if principal is not None:
            scope["state"]["principal"] = principal
        return Request(scope)

    assert principal_of(request(tester)) is tester
    assert require_device_access(request(tester), "bench-kit") is tester
    assert require_device_access(request(OPS), "anything") is OPS
    for call in (
        lambda: principal_of(request()),
        lambda: require_ops(request(tester)),
        lambda: require_device_access(request(tester), "other"),
        lambda: services_of(request(OPS)),
    ):
        with pytest.raises(HTTPException, match=r"Sign in|Only ops|isn't one|still starting"):
            call()
