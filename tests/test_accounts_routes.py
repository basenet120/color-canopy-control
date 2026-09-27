"""Account connect over HTTP on the simulator (07 §3, D2): the JSON API, the hardened /ops/accounts form, device
discovery and registration. Everything here runs on SimAccounts through the real app (lifespan included)."""

from __future__ import annotations

import html
import re

import pytest
import yaml

from conductor.accounts_sim import OTP_CODE, OTP_PASSWORD
from conductor.routes.accounts import FORM_MAX_ATTEMPTS

EMAIL = "email-sentinel-4d2c@example.invalid"
PASSWORD = "PW-SENTINEL-19be7a"
ORIGIN = {"Origin": "http://testserver"}


def _no_sentinel(response, *extra: str) -> None:
    text = response.text + " ".join(f"{k}: {v}" for k, v in response.headers.items())
    for sentinel in (EMAIL, PASSWORD, *extra):
        assert sentinel not in text, f"{sentinel} echoed in a response"


def _form(client, data: dict, headers: dict | None = None):
    return client.post(
        "/ops/accounts", data=data, headers=ORIGIN if headers is None else headers, follow_redirects=False
    )


@pytest.fixture
def form_client(app_client, monkeypatch):
    monkeypatch.setenv("CONDUCTOR_ACCOUNT_CONNECT", "1")
    return app_client


# ---- JSON API ----------------------------------------------------------------------------------------------------
def test_status_is_simulated_and_no_store(app_client):
    r = app_client.get("/api/accounts")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["simulated"] is True and body["connect_allowed"] is True
    assert body["form_enabled"] is False and "CONDUCTOR_ACCOUNT_CONNECT=1" in body["form_reason"]
    bench = next(a for a in body["accounts"] if a["account"] == "bench")
    assert bench["simulated"] and not bench["connected"] and bench["label"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/accounts/login",
        "/api/accounts/otp",
        "/api/accounts/bench/disconnect",
        "/api/accounts/bench/register",
    ],
)
def test_json_api_refuses_form_encoded_posts(app_client, path):
    r = app_client.post(path, data={"account": "bench", "email": EMAIL, "password": PASSWORD})
    assert r.status_code == 415 and r.headers["cache-control"] == "no-store"
    _no_sentinel(r)
    r = app_client.post(path, content=b'{"account": "bench"}', headers={"Content-Type": "text/plain"})
    assert r.status_code == 415


def test_login_connects_and_never_echoes_fields(app_client):
    r = app_client.post(
        "/api/accounts/login", json={"account": "bench", "email": EMAIL, "password": PASSWORD}
    )
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json()["state"] == "connected" and r.json()["simulated"] is True
    _no_sentinel(r)
    status = app_client.get("/api/accounts").json()
    assert next(a for a in status["accounts"] if a["account"] == "bench")["connected"]
    _no_sentinel(app_client.get("/api/accounts"))


def test_login_with_emailed_code_reprompts_then_connects(app_client):
    r = app_client.post(
        "/api/accounts/login", json={"account": "bench", "email": EMAIL, "password": OTP_PASSWORD}
    )
    first = r.json()
    assert first["state"] == "otp_required" and first["nonce"]
    wrong = app_client.post("/api/accounts/otp", json={"nonce": first["nonce"], "code": "111111"}).json()
    assert wrong["state"] == "otp_required" and wrong["nonce"] == first["nonce"]
    done = app_client.post("/api/accounts/otp", json={"nonce": first["nonce"], "code": OTP_CODE}).json()
    assert done["state"] == "connected" and done["account"] == "bench"
    gone = app_client.post("/api/accounts/otp", json={"nonce": first["nonce"], "code": OTP_CODE}).json()
    assert gone["state"] == "failed" and "restart" in gone["message"]


def test_bad_json_bodies_are_refused_without_echo(app_client):
    cases = [
        {"account": "bench", "email": EMAIL},  # no password
        {"account": "bench", "email": EMAIL, "password": 12345},
        {"account": "bench", "email": EMAIL, "password": PASSWORD * 200},
    ]
    for body in cases:
        r = app_client.post("/api/accounts/login", json=body)
        assert r.status_code == 400 and r.headers["cache-control"] == "no-store"
        _no_sentinel(r)
    r = app_client.post(
        "/api/accounts/login",
        content=b"{not json " + PASSWORD.encode(),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    _no_sentinel(r)
    r = app_client.post("/api/accounts/login", json={"account": "nope", "email": EMAIL, "password": PASSWORD})
    assert r.status_code == 404
    _no_sentinel(r)


def test_failed_sign_in_never_echoes(app_client):
    wrong = "stranger-sentinel@realcorp.example"
    r = app_client.post(
        "/api/accounts/login", json={"account": "bench", "email": wrong, "password": PASSWORD}
    )
    assert r.json()["state"] == "failed"
    _no_sentinel(r, wrong, "realcorp")


def test_disconnect(app_client):
    app_client.post("/api/accounts/login", json={"account": "bench", "email": EMAIL, "password": PASSWORD})
    r = app_client.post("/api/accounts/bench/disconnect", json={})
    assert r.status_code == 200 and r.json()["disconnected"] and "Simulator" in r.json()["message"]
    assert not next(a for a in app_client.get("/api/accounts").json()["accounts"] if a["account"] == "bench")[
        "connected"
    ]
    assert app_client.post("/api/accounts/nope/disconnect", json={}).status_code == 404


def test_devices_need_a_connected_account_then_list_simulated_devices(app_client):
    r = app_client.get("/api/accounts/bench/devices")
    assert r.status_code == 409 and "Sign in" in r.json()["detail"]
    app_client.post("/api/accounts/login", json={"account": "bench", "email": EMAIL, "password": PASSWORD})
    body = app_client.get("/api/accounts/bench/devices").json()
    assert body["simulated"] is True and "Simulator" in body["note"]
    [device] = body["devices"]
    assert device["metadevice_id"] == "sim-bench-kit" and device["registered_as"] == "bench-kit"
    assert device["simulated"] and not device["read_only"]
    assert [r["id"] for r in body["registry"]] == ["bench-kit"]
    assert app_client.get("/api/accounts/nope/devices").status_code == 404


def test_register_writes_the_overlay_and_refuses_mismatches_and_duplicates(app_client, tmp_path):
    r = app_client.post(
        "/api/accounts/bench/register", json={"metadevice_id": "md-test-0001", "device_id": "bench-kit"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["device_id"] == "bench-kit"
        and body["simulated"]
        and "HUBSPACE_DEVICE_ALLOWLIST" in body["message"]
    )
    overlay = yaml.safe_load((tmp_path / "data" / "overrides" / "registry.yaml").read_text())
    assert overlay["devices"]["bench-kit"]["metadevice_id"] == "md-test-0001"
    services = app_client.app.state.services
    assert services.registry.get("bench-kit").metadevice_id == "md-test-0001"

    # the device belongs to another account
    r = app_client.post(
        "/api/accounts/canopy/register", json={"metadevice_id": "md-test-0002", "device_id": "bench-kit"}
    )
    assert r.status_code == 409 and "belongs to the bench account" in r.json()["detail"]
    # the id is already someone else's (a metadevice id, or another device's simulator address)
    for md in ("md-test-0001", "sim-bench-kit"):
        r = app_client.post(
            "/api/accounts/canopy/register", json={"metadevice_id": md, "device_id": "canopy-r0c0"}
        )
        assert r.status_code == 409 and "already registered as bench-kit" in r.json()["detail"]
    # unknown device, missing field
    assert (
        app_client.post(
            "/api/accounts/bench/register", json={"metadevice_id": "x1", "device_id": "nope"}
        ).status_code
        == 404
    )
    assert app_client.post("/api/accounts/bench/register", json={"device_id": "bench-kit"}).status_code == 400
    # an id the registry refuses (slashes) is a plain 400, not a crash
    r = app_client.post(
        "/api/accounts/bench/register", json={"metadevice_id": "a/b", "device_id": "bench-kit"}
    )
    assert r.status_code == 400
    # re-registering the same device replaces its id and says so
    r = app_client.post(
        "/api/accounts/bench/register", json={"metadevice_id": "md-test-0003", "device_id": "bench-kit"}
    )
    assert r.status_code == 200 and r.json()["replaced"] == "md-test-0001"


# ---- the /ops/accounts form --------------------------------------------------------------------------------------
def test_form_is_off_without_the_flag(app_client):
    r = _form(app_client, {"account": "bench", "email": EMAIL, "password": PASSWORD})
    assert r.status_code == 403 and "CONDUCTOR_ACCOUNT_CONNECT=1" in r.text and "command line" in r.text
    assert r.headers["cache-control"] == "no-store"
    _no_sentinel(r)
    assert app_client.app.state.services.accounts.status()[0].connected is False


def test_accounts_page_forbids_framing(app_client):
    r = app_client.get("/ops/accounts/")
    assert r.status_code == 200 and "Hubspace accounts" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"
    assert app_client.get("/ops/accounts/accounts.js").status_code == 200


def test_form_same_origin_is_enforced(form_client):
    data = {"account": "bench", "email": EMAIL, "password": PASSWORD}
    for headers in (
        {},  # neither Origin nor Referer
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Referer": "https://evil.example/ops/accounts/"},
        {"Origin": "https://evil.example", "Referer": "http://testserver/ops/accounts/"},
    ):
        r = _form(form_client, data, headers)
        assert r.status_code == 403 and "refused" in r.text, headers
        _no_sentinel(r)
    services = form_client.app.state.services
    assert not any(s.connected for s in services.accounts.status())
    r = _form(form_client, data, {"Referer": "http://testserver/ops/accounts/"})
    assert r.status_code == 303


def test_form_headers_and_no_echo_on_every_answer(form_client):
    answers = [
        _form(form_client, {"account": "bench", "email": EMAIL}),  # missing password
        _form(
            form_client, {"account": "bench", "email": "x-sentinel@realcorp.example", "password": PASSWORD}
        ),
        _form(form_client, {"step": "otp", "nonce": "made-up", "code": "PW-SENTINEL-code"}),
    ]
    assert answers[0].status_code == 400 and "Choose an account" in answers[0].text
    assert "didn't work" in html.unescape(answers[1].text)
    assert "expired" in answers[2].text
    for r in answers:
        assert r.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-frame-options"] == "DENY"
        assert 'class="sim-badge"' in r.text
        _no_sentinel(r, "x-sentinel", "realcorp", "PW-SENTINEL-code")


def test_form_otp_step_then_success_turns_the_form_off(form_client):
    r = _form(form_client, {"step": "login", "account": "bench", "email": EMAIL, "password": OTP_PASSWORD})
    assert r.status_code == 200
    nonce = re.search(r'name="nonce" value="([^"]+)"', r.text).group(1)
    assert 'name="step" value="otp"' in r.text and "redeployed" in r.text
    _no_sentinel(r, OTP_PASSWORD)
    r = _form(form_client, {"step": "otp", "nonce": nonce, "code": "999999"})
    assert r.status_code == 200 and f'value="{nonce}"' in r.text  # re-prompt on the same pending sign-in
    r = _form(form_client, {"step": "otp", "nonce": nonce, "code": OTP_CODE})
    assert r.status_code == 303 and r.headers["location"] == "/ops/accounts/?result=connected"
    assert r.headers["cache-control"] == "no-store"
    assert form_client.app.state.services.accounts.status()[0].connected

    # turned itself off: the next attempt is refused and the page says why
    r = _form(form_client, {"step": "login", "account": "bench", "email": EMAIL, "password": PASSWORD})
    assert r.status_code == 403 and "turned itself off" in r.text
    status = form_client.get("/api/accounts").json()
    assert status["form_enabled"] is False and "turned itself off" in status["form_reason"]


def test_form_attempt_limit(form_client):
    data = {"account": "bench", "email": "nobody@realcorp.example", "password": PASSWORD}
    for _ in range(FORM_MAX_ATTEMPTS):
        assert _form(form_client, data).status_code == 200
    r = _form(form_client, data)
    assert r.status_code == 429 and "15 minutes" in r.text and r.headers["cache-control"] == "no-store"
    _no_sentinel(r, "realcorp")
    # the limit holds even for a correct sign-in until the window passes
    assert _form(form_client, {"account": "bench", "email": EMAIL, "password": PASSWORD}).status_code == 429


def test_form_rejects_other_content_types(form_client):
    r = form_client.post(
        "/ops/accounts",
        json={"account": "bench", "email": EMAIL, "password": PASSWORD},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert r.status_code == 415
    _no_sentinel(r)


# ---- who may reach it -------------------------------------------------------------------------------------------
def test_accounts_need_the_ops_credential(app_client_auth):
    ops = ("crew", "canopy")
    assert app_client_auth.get("/api/accounts").status_code == 401
    assert app_client_auth.get("/ops/accounts/").status_code == 401
    assert app_client_auth.get("/api/accounts", auth=ops).status_code == 200
    # a tester's enrolled phone can't reach account connect
    link = app_client_auth.post(
        "/api/roles/enrollments", json={"role": "tester", "name": "shop", "grants": ["bench-kit"]}, auth=ops
    ).json()
    r = app_client_auth.post(link["path"], follow_redirects=False)
    cookie = r.cookies.get("cc_session")
    assert cookie
    tester = {"Cookie": f"cc_session={cookie}"}
    assert app_client_auth.get("/ops/module/", headers=tester).status_code in (200, 404)
    for path in ("/api/accounts", "/ops/accounts/", "/api/devices", "/api/ops/summary"):
        assert app_client_auth.get(path, headers=tester).status_code == 401, path
    r = app_client_auth.post(
        "/api/accounts/login", json={"account": "bench", "email": EMAIL, "password": PASSWORD}, headers=tester
    )
    assert r.status_code == 401
