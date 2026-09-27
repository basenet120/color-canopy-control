"""Credential canary through the HTTP routes (07 §3): the full sign-in, emailed code included, via the /ops/accounts
form AND the JSON API the CLI uses, against a real AferoDriver + AferoAccounts over the fake Keycloak/Afero
transport (tests/fake_afero.py), with sentinel password/code/token values and every logger at DEBUG.

The app runs in `bench` mode (the mode that allows account connect, D3) with Basic auth, a real Fernet key and a
temporary data dir; after startup its Afero adapter is swapped for one whose sessions use the fake transport, so
nothing touches the network. No sentinel may appear in any response (body or headers), in any log line, or in any
file under the data dir."""

from __future__ import annotations

import logging
import re

import pytest
from cryptography.fernet import Fernet
from fake_afero import BENCH_MD, SPARE_MD, STRANGER_MD, FakeAfero
from fastapi.testclient import TestClient

from conductor.drivers.afero import AferoAccounts, AferoDriver
from conductor.drivers.afero_transport import GatedClientSession
from conductor.executor import CommandExecutor

EMAIL = "canary@example.invalid"
PASSWORD = "PW-SENTINEL-2b7e41d9c0"
OTP = "OTP-SENTINEL-66120"
WRONG_OTP = "OTP-WRONG-SENTINEL-31"
TOKEN_PREFIX = "TOKSENTINEL"
OPS = ("crew", "canopy")
ORIGIN = {"Origin": "http://testserver"}


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info:
            text += "\n" + logging.Formatter().formatException(record.exc_info)
        self.lines.append(text)


@pytest.fixture
def all_debug_logs():
    root = logging.getLogger()
    handler = Capture()
    saved = {name: logging.getLogger(name).level for name in list(logging.root.manager.loggerDict)}
    saved_root = root.level
    root.setLevel(logging.DEBUG)
    for name in saved:
        logging.getLogger(name).setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(saved_root)
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)


@pytest.fixture
def live_app(monkeypatch, tmp_path):
    """The real app in bench mode, with its Afero adapter pointed at a FakeAfero. Yields (client, fake, data)."""
    from conductor.app import app

    for key in ("CONDUCTOR_AUTH", "RAILWAY_ENVIRONMENT_NAME", "HUBSPACE_LIVE_UNTIL"):
        monkeypatch.delenv(key, raising=False)
    data = tmp_path / "data"
    env = {
        "CONDUCTOR_MODE": "bench",
        "HUBSPACE_LIVE": "1",
        "CONDUCTOR_BASIC_AUTH": "crew:canopy",
        "CONDUCTOR_SECRET_KEY": Fernet.generate_key().decode(),
        "CONDUCTOR_ENV": "test",
        "CONDUCTOR_DATA_DIR": str(data),
        "CONDUCTOR_SIM_SPEED": "fast",
        "HUBSPACE_ACCOUNT_ALLOWLIST": EMAIL,
        "HUBSPACE_DEVICE_ALLOWLIST": BENCH_MD,
        "CONDUCTOR_ACCOUNT_CONNECT": "1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    fake = FakeAfero(email=EMAIL, password=PASSWORD, otp_code=OTP, token_prefix=TOKEN_PREFIX)
    for md in (BENCH_MD, SPARE_MD, STRANGER_MD):
        fake.add_device(md)

    with TestClient(app) as client:
        services = app.state.services
        assert services.afero is not None and not services.accounts.simulated

        async def swap() -> None:
            old = services.afero
            executor = services.executors.pop(id(old))
            await executor.close()
            await old.close()
            driver = AferoDriver(
                lambda: services.registry,
                services.gate,
                services.token_store,
                clock=services.clock,
                telemetry=services.telemetry,
                session_factory=lambda: GatedClientSession(
                    services.gate, clock=services.clock, fake_transport=fake
                ),
            )
            executor = CommandExecutor(
                driver,
                locks=services.locks,
                telemetry=services.telemetry,
                clock=services.clock,
                policy=services.settings.executor,
            )
            services.tracker.attach(executor)
            services.executors[id(driver)] = executor
            services.afero = driver
            services.accounts = AferoAccounts(driver)
            await driver.start()

        client.portal.call(swap)
        yield client, fake, data


def test_http_sign_in_with_emailed_code_leaks_no_secret(live_app, all_debug_logs):
    client, fake, data = live_app
    responses = []

    def keep(r):
        responses.append(r)
        return r

    def form(fields: dict):
        return keep(
            client.post("/ops/accounts", data=fields, headers=ORIGIN, auth=OPS, follow_redirects=False)
        )

    # 1. The page form: password, a wrong code (re-prompt), the right code.
    r = form({"step": "login", "account": "bench", "email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    nonce = re.search(r'name="nonce" value="([^"]+)"', r.text).group(1)
    r = form({"step": "otp", "nonce": nonce, "code": WRONG_OTP})
    assert r.status_code == 200 and "didn" in r.text and f'value="{nonce}"' in r.text
    r = form({"step": "otp", "nonce": nonce, "code": OTP})
    assert r.status_code == 303, r.text
    status = keep(client.get("/api/accounts", auth=OPS)).json()
    bench = next(a for a in status["accounts"] if a["account"] == "bench")
    assert bench["connected"] and bench["token_generation"] >= 1 and not bench["simulated"]
    assert status["form_enabled"] is False  # turned itself off

    # 2. Discovery through the account, then register the bench kit (read-only until allowlisted + registered).
    listed = keep(client.get("/api/accounts/bench/devices", auth=OPS)).json()
    assert {d["metadevice_id"] for d in listed["devices"]} == {BENCH_MD, SPARE_MD, STRANGER_MD}
    assert all(d["read_only"] and d["registered_as"] is None for d in listed["devices"])
    r = keep(
        client.post(
            "/api/accounts/bench/register",
            json={"metadevice_id": BENCH_MD, "device_id": "bench-kit"},
            auth=OPS,
        )
    )
    assert r.status_code == 200 and r.json()["allowlisted"] is True and r.json()["simulated"] is False
    r = keep(
        client.post(
            "/api/accounts/bench/register",
            json={"metadevice_id": "md-never-seen", "device_id": "bench-kit"},
            auth=OPS,
        )
    )
    assert r.status_code == 409 and "latest device list" in r.json()["detail"]
    listed = keep(client.get("/api/accounts/bench/devices", auth=OPS)).json()
    by_id = {d["metadevice_id"]: d for d in listed["devices"]}
    assert by_id[BENCH_MD]["registered_as"] == "bench-kit" and not by_id[BENCH_MD]["read_only"]
    assert by_id[STRANGER_MD]["read_only"]

    # 3. The device list now reads the kit's profile through Hubspace (a GET, Purpose.BENCH).
    device = keep(client.get("/api/devices/bench-kit", auth=OPS)).json()
    assert device["purposes"]["bench"] == {"driver": "afero", "simulated": False, "address": BENCH_MD}
    assert device["capabilities"] is not None and device["allowlisted"] is True
    keep(client.get("/api/ops/summary", auth=OPS))

    # 4. Disconnect, then the JSON API (what the CLI uses): password, wrong code, right code.
    assert keep(client.post("/api/accounts/bench/disconnect", json={}, auth=OPS)).status_code == 200
    first = keep(
        client.post(
            "/api/accounts/login", json={"account": "bench", "email": EMAIL, "password": PASSWORD}, auth=OPS
        )
    ).json()
    assert first["state"] == "otp_required"
    again = keep(
        client.post("/api/accounts/otp", json={"nonce": first["nonce"], "code": WRONG_OTP}, auth=OPS)
    ).json()
    assert again["state"] == "otp_required" and again["nonce"] == first["nonce"]
    done = keep(
        client.post("/api/accounts/otp", json={"nonce": first["nonce"], "code": OTP}, auth=OPS)
    ).json()
    assert done["state"] == "connected"
    keep(client.get("/api/accounts", auth=OPS))

    # 5. A refused email never reaches the wire and comes back as the gate's 403.
    before = len(fake.requests)
    r = keep(
        client.post(
            "/api/accounts/login",
            json={"account": "bench", "email": "show@realcorp.example", "password": PASSWORD},
            auth=OPS,
        )
    )
    assert r.status_code == 403 and "HUBSPACE_ACCOUNT_ALLOWLIST" in r.json()["detail"]
    assert len(fake.requests) == before

    # Nothing secret anywhere: responses, logs, files under the data dir.
    assert len(fake.issued) >= 6
    sentinels = [PASSWORD, OTP, WRONG_OTP, *fake.issued]
    logs = "\n".join(all_debug_logs.lines)
    assert len(all_debug_logs.lines) > 20  # the capture really saw DEBUG traffic
    files = {p: p.read_bytes() for p in data.rglob("*") if p.is_file()}
    assert any(p.suffix == ".token" for p in files)
    for r in responses:
        text = r.text + repr(dict(r.headers))
        for sentinel in sentinels:
            assert sentinel not in text, f"{sentinel[:6]}… leaked into a response from {r.request.url.path}"
        assert EMAIL not in text and "realcorp" not in text  # never echoes submitted fields
    for sentinel in sentinels:
        assert sentinel not in logs, f"{sentinel[:6]}… leaked into the logs"
        for path, content in files.items():
            assert sentinel.encode() not in content, f"{sentinel[:6]}… leaked into {path.name}"


def test_account_connect_refused_by_the_gate_is_a_403_with_its_reason(live_app, monkeypatch):
    client, fake, _data = live_app
    services = client.app.state.services
    from conductor.gate import GateSettings, RuntimeMode

    # sim + HUBSPACE_LIVE=1 allows bench jobs only, not account connect (D3)
    services.gate.settings = GateSettings(
        mode=RuntimeMode.SIM, live=True, account_allowlist=frozenset({EMAIL}), environment="test"
    )
    r = client.post(
        "/api/accounts/login", json={"account": "bench", "email": EMAIL, "password": PASSWORD}, auth=OPS
    )
    assert r.status_code == 403 and "only bench jobs" in r.json()["detail"]
    r = client.post("/api/accounts/otp", json={"nonce": "x", "code": "1"}, auth=OPS)
    assert r.status_code == 403
    r = client.post(
        "/ops/accounts",
        data={"step": "login", "account": "bench", "email": EMAIL, "password": PASSWORD},
        headers=ORIGIN,
        auth=OPS,
    )
    assert r.status_code == 403 and "only bench jobs" in r.text
    assert fake.requests == []
    assert client.get("/api/accounts", auth=OPS).json()["connect_allowed"] is False
