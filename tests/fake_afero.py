"""A scriptable fake of the Afero cloud + Hubspace Keycloak, for `GatedClientSession(fake_transport=...)`.

Nothing here touches the network. `FakeAfero` is the transport callable: it receives `(method, url, kwargs)` after
the live gate has passed the request, records it, and answers like the real services:

- Keycloak (`accounts.hubspaceconnect.com`): the login page, the password POST (optionally an emailed-code page),
  the code POST, and the token endpoint (authorization_code + refresh_token grants; refresh tokens rotate).
- API (`api2.afero.net`): `users/me`, metadevice discovery, one metadevice, state GET and PUT. Every API call
  needs the current id_token as bearer, else 401.

`script(method, path_part, *responses)` queues overrides that win over the default behaviour, in order: a
`FakeResponse`, an exception instance (raised), `HANG` (never answers), or a callable `(req) -> response`.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Self
from urllib.parse import parse_qs

import aiohttp
from cryptography.fernet import Fernet
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from conductor.clock import Clock, VirtualClock
from conductor.config import OverlayFile, Registry, RegistryFile
from conductor.drivers.afero import AferoDriver
from conductor.drivers.afero_transport import GatedClientSession
from conductor.gate import GateSettings, LiveGate, RuntimeMode
from conductor.telemetry import Telemetry
from conductor.tokens import TokenStore

FIXTURE = Path(__file__).resolve().parent.parent / "conductor/sim/profiles/hampton-bay-string-20seg.json"
AUTH_HOST = "accounts.hubspaceconnect.com"
HANG = object()
_FORM_ACTION = "https://accounts.hubspaceconnect.com/auth/realms/thd/login-actions/authenticate"


class FakeResponse:
    """Just enough of aiohttp.ClientResponse for aioafero and the adapter."""

    def __init__(
        self,
        status: int = 200,
        body: Any = None,
        *,
        headers: dict[str, str] | None = None,
        content_type: str | None = None,
        url: URL | str = "https://api2.afero.net/",
        method: str = "GET",
    ):
        self.status = status
        if isinstance(body, (dict, list)):
            raw = json.dumps(body).encode()
            content_type = content_type or "application/json"
        elif isinstance(body, str):
            raw = body.encode()
            content_type = content_type or "text/html"
        else:
            raw = body or b""
            content_type = content_type or "application/octet-stream"
        self._body = raw
        self.content_type = content_type
        hdrs = CIMultiDict(headers or {})
        hdrs.setdefault("Content-Type", content_type)
        self.headers = CIMultiDictProxy(hdrs)
        self.url = URL(str(url))
        self.method = method
        self.history: tuple = ()
        self.reason = "OK" if status < 400 else "Error"
        self.released = False

    @property
    def request_info(self) -> aiohttp.RequestInfo:
        return aiohttp.RequestInfo(self.url, self.method, CIMultiDictProxy(CIMultiDict()), self.url)

    @property
    def ok(self) -> bool:
        return self.status < 400

    async def read(self) -> bytes:
        return self._body

    async def text(self, encoding: str | None = None) -> str:
        return self._body.decode(encoding or "utf-8")

    async def json(self, **_: Any) -> Any:
        if "json" not in self.content_type:
            raise aiohttp.ContentTypeError(
                self.request_info, self.history, status=self.status, message="not json", headers=self.headers
            )
        if not self._body.strip():
            return None
        return json.loads(self._body)

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                self.request_info, self.history, status=self.status, message=self.reason, headers=self.headers
            )

    def release(self) -> None:
        self.released = True

    def close(self) -> None:
        self.released = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.release()


@dataclass
class FakeRequest:
    method: str
    url: URL
    headers: dict[str, str]
    json: Any = None
    data: Any = None
    allow_redirects: Any = None

    @property
    def host(self) -> str:
        return self.url.host or ""

    @property
    def path(self) -> str:
        return self.url.path

    @property
    def header_host(self) -> str | None:
        return self.headers.get("host")


def raw_device_from_fixture(metadevice_id: str, fixture: Path = FIXTURE, *, name: str | None = None) -> dict:
    """The sim fixture (aioafero's post-processed AferoDevice shape) rewrapped in Afero's raw API shape."""
    dev = json.loads(fixture.read_text())
    capabilities = []
    for cap in dev.get("capabilities", []):
        flat = {k: v for k, v in cap.items() if k != "_opts"}
        flat.update(cap.get("_opts") or {})
        capabilities.append(flat)
    return {
        "id": metadevice_id,
        "deviceId": f"dev-{metadevice_id}",
        "typeId": "metadevice.device",
        "friendlyName": name or dev.get("friendly_name"),
        "description": {
            "defaultImage": dev.get("default_image"),
            "device": {
                "model": dev.get("model"),
                "deviceClass": dev.get("device_class"),
                "defaultName": dev.get("default_name"),
                "manufacturerName": dev.get("manufacturerName"),
            },
            "functions": copy.deepcopy(dev.get("functions", [])),
        },
        "state": {"metadeviceId": metadevice_id, "values": copy.deepcopy(dev.get("states", []))},
        "capabilities": capabilities,
        "children": [],
    }


def _page(form_id: str, session_code: str, error: str | None = None) -> str:
    action = f"{_FORM_ACTION}?session_code={session_code}&amp;execution=exec-1&amp;client_id=hubspace_android&amp;tab_id=tab-1"
    feedback = f'<span class="kc-feedback-text">{error}</span>' if error else ""
    return f'<html><body>{feedback}<form id="{form_id}" action="{action}" method="post"></form></body></html>'


@dataclass
class FakeAfero:
    account_id: str = "acct-fake-0001"
    email: str = "bench@example.invalid"
    password: str = "correct horse battery staple"
    otp_code: str | None = None  # set → the password step asks for an emailed code
    token_prefix: str = "tok"
    expires_in: int = 118
    devices: dict[str, dict] = field(default_factory=dict)
    requests: list[FakeRequest] = field(default_factory=list)
    issued: list[str] = field(default_factory=list)  # every token/code this fake ever minted
    valid_refresh: set[str] = field(default_factory=set)
    valid_bearer: set[str] = field(default_factory=set)
    _scripts: dict[tuple[str, str], deque] = field(default_factory=dict)
    _codes: set[str] = field(default_factory=set)
    _n: int = 0

    # ---- setup -------------------------------------------------------------------------------------------------
    def add_device(self, metadevice_id: str, raw: dict | None = None, **kw: Any) -> dict:
        raw = raw or raw_device_from_fixture(metadevice_id, **kw)
        self.devices[metadevice_id] = raw
        return raw

    def _mint(self, kind: str) -> str:
        self._n += 1
        value = f"{self.token_prefix}-{kind}-{self._n:04d}-zq"
        self.issued.append(value)
        return value

    def seed_refresh_token(self) -> str:
        """A refresh token this fake accepts, as if from an earlier sign-in (put it in the TokenStore)."""
        token = self._mint("refresh")
        self.valid_refresh.add(token)
        return token

    def script(self, method: str, path_part: str, *responses: Any) -> None:
        self._scripts.setdefault((method.upper(), path_part), deque()).extend(responses)

    def count(self, method: str | None = None, path_part: str = "") -> int:
        return sum(
            1
            for r in self.requests
            if (method is None or r.method == method.upper()) and path_part in str(r.url)
        )

    def api_requests(self) -> list[FakeRequest]:
        return [r for r in self.requests if r.host != AUTH_HOST]

    # ---- transport ---------------------------------------------------------------------------------------------
    def __call__(self, method: str, url: URL, kwargs: dict[str, Any]) -> Any:
        headers = {str(k).lower(): str(v) for k, v in (kwargs.get("headers") or {}).items()}
        req = FakeRequest(
            method.upper(),
            url,
            headers,
            copy.deepcopy(kwargs.get("json")),
            kwargs.get("data"),
            kwargs.get("allow_redirects"),
        )
        self.requests.append(req)
        for (m, part), queue in self._scripts.items():
            if m == req.method and part in str(url) and queue:
                item = queue.popleft()
                if item is HANG:
                    return self._hang()
                if isinstance(item, BaseException):
                    raise item
                if callable(item):
                    return item(req)
                return item
        if req.host == AUTH_HOST:
            return self._keycloak(req)
        return self._api(req)

    @staticmethod
    async def _hang() -> Any:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    # ---- Keycloak ----------------------------------------------------------------------------------------------
    def _keycloak(self, req: FakeRequest) -> FakeResponse:
        path = req.path
        if req.method == "GET" and path.endswith("/protocol/openid-connect/auth"):
            return FakeResponse(200, _page("kc-form-login", "sess-1"), url=req.url)
        if req.method == "POST" and path.endswith("/login-actions/authenticate"):
            data = req.data or {}
            if "emailCode" in data:
                if data["emailCode"] != self.otp_code:
                    return FakeResponse(
                        200, _page("kc-otp-login-form", "sess-3", "Invalid code."), url=req.url
                    )
                return self._redirect_with_code(req)
            username = str(data.get("username") or "").lower()  # Keycloak usernames are case-insensitive
            if username != self.email.lower() or data.get("password") != self.password:
                return FakeResponse(
                    200, _page("kc-form-login", "sess-2", "Invalid username or password."), url=req.url
                )
            if self.otp_code is not None:
                return FakeResponse(200, _page("kc-otp-login-form", "sess-2"), url=req.url)
            return self._redirect_with_code(req)
        if req.method == "POST" and path.endswith("/protocol/openid-connect/token"):
            data = req.data or {}
            grant = data.get("grant_type")
            if grant == "authorization_code" and data.get("code") in self._codes:
                self._codes.discard(data["code"])
                return self._tokens(req)
            if grant == "refresh_token" and data.get("refresh_token") in self.valid_refresh:
                self.valid_refresh.discard(data["refresh_token"])  # rotation: the old one is spent
                return self._tokens(req)
            return FakeResponse(400, {"error": "invalid_grant"}, url=req.url)
        return FakeResponse(404, {"error": "not found"}, url=req.url)

    def _redirect_with_code(self, req: FakeRequest) -> FakeResponse:
        code = self._mint("code")
        self._codes.add(code)
        return FakeResponse(
            302, "", headers={"Location": f"hubspace-app://loginredirect?code={code}&state=x"}, url=req.url
        )

    def _tokens(self, req: FakeRequest) -> FakeResponse:
        refresh, access, id_token = self._mint("refresh"), self._mint("access"), self._mint("id")
        self.valid_refresh.add(refresh)
        self.valid_bearer.add(id_token)
        body = {
            "refresh_token": refresh,
            "access_token": access,
            "id_token": id_token,
            "expires_in": self.expires_in,
        }
        return FakeResponse(200, body, url=req.url)

    # ---- API ---------------------------------------------------------------------------------------------------
    def _api(self, req: FakeRequest) -> FakeResponse:
        auth = req.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in self.valid_bearer:
            return FakeResponse(401, {"error": "unauthorized"}, url=req.url)
        parts = [p for p in req.path.split("/") if p]
        if parts == ["v1", "users", "me"]:
            return FakeResponse(
                200, {"accountAccess": [{"account": {"accountId": self.account_id}}]}, url=req.url
            )
        if len(parts) < 4 or parts[:2] != ["v1", "accounts"] or parts[2] != self.account_id:
            return FakeResponse(404, {"error": "not found"}, url=req.url)
        rest = parts[3:]
        if rest == ["metadevices"] and req.method == "GET":
            return FakeResponse(200, [copy.deepcopy(d) for d in self.devices.values()], url=req.url)
        if len(rest) >= 2 and rest[0] == "metadevices":
            device = self.devices.get(rest[1])
            if device is None:
                return FakeResponse(404, {"error": "not found"}, url=req.url)
            if len(rest) == 2 and req.method == "GET":
                return FakeResponse(200, copy.deepcopy(device), url=req.url)
            if rest[2:] == ["state"] and req.method == "GET":
                return FakeResponse(200, copy.deepcopy(device["state"]), url=req.url)
            if rest[2:] == ["state"] and req.method == "PUT":
                values = (req.json or {}).get("values", [])
                current = {
                    (v["functionClass"], v.get("functionInstance")): v for v in device["state"]["values"]
                }
                for v in values:
                    current[(v["functionClass"], v.get("functionInstance"))] = v
                device["state"]["values"] = list(current.values())
                return FakeResponse(200, {"metadeviceId": rest[1], "values": values}, url=req.url)
        return FakeResponse(404, {"error": "not found"}, url=req.url)


def query(url: URL) -> dict[str, list[str]]:
    return parse_qs(url.query_string)


# ---- a small world for adapter tests ------------------------------------------------------------------------------
BENCH_MD = "md-bench-0001"  # registered, writable, allowlisted
SPARE_MD = "md-spare-0002"  # registered and writable, but not in HUBSPACE_DEVICE_ALLOWLIST
STRANGER_MD = "md-stranger-0003"  # visible on the account, not registered
BENCH_EMAIL = "bench@example.invalid"


def make_registry() -> Registry:
    return Registry(
        RegistryFile.model_validate(
            {
                "accounts": {"bench": {"label": "Bench"}},
                "devices": [
                    {"id": "bench-kit", "role": "bench", "account": "bench", "metadevice_id": BENCH_MD},
                    {"id": "spare-kit", "role": "spare", "account": "bench", "metadevice_id": SPARE_MD},
                    {"id": "unassigned", "role": "spare", "account": "bench"},
                ],
            }
        ),
        OverlayFile(),
        None,
    )


def make_gate(
    registry: Registry,
    clock: Clock,
    *,
    mode: RuntimeMode = RuntimeMode.LIVE,
    live: bool = True,
    allow: tuple[str, ...] = (BENCH_MD,),
    emails: tuple[str, ...] = (BENCH_EMAIL,),
    live_until: date | None = None,
) -> LiveGate:
    settings = GateSettings(
        mode=mode,
        live=live,
        live_until=live_until,
        device_allowlist=frozenset(allow),
        account_allowlist=frozenset(e.lower() for e in emails),
        environment="test",
    )
    return LiveGate(settings, registry.writable_metadevice_ids, clock)


@dataclass
class World:
    fake: FakeAfero
    registry: Registry
    gate: LiveGate
    store: TokenStore
    telemetry: Telemetry
    clock: VirtualClock
    driver: AferoDriver
    wall: list[float]

    def session(self) -> GatedClientSession:
        return GatedClientSession(self.gate, clock=self.clock, fake_transport=self.fake)


async def make_world(
    data_dir: Path,
    *,
    fake: FakeAfero | None = None,
    seed_token: bool = True,
    connect: bool = True,
    secret_key: str | None = None,
    **gate_kw: Any,
) -> World:
    """Must be awaited on a running loop (run_virtual). Seeds a stored refresh token and connects by default."""
    fake = fake or FakeAfero()
    for md in (BENCH_MD, SPARE_MD, STRANGER_MD):
        if md not in fake.devices:
            fake.add_device(md)
    clock = VirtualClock()
    registry = make_registry()
    gate = make_gate(registry, clock, **gate_kw)
    store = TokenStore(data_dir, "test", secret_key or Fernet.generate_key().decode(), clock)
    telemetry = Telemetry(data_dir, clock)
    if seed_token:
        store.save("bench", fake.seed_refresh_token())
    wall = [time.time()]
    driver = AferoDriver(
        registry,
        gate,
        store,
        clock=clock,
        telemetry=telemetry,
        session_factory=lambda: GatedClientSession(gate, clock=clock, fake_transport=fake),
        afero_wall=lambda: wall[0],
    )
    world = World(fake, registry, gate, store, telemetry, clock, driver, wall)
    if connect:
        assert await driver.connect("bench"), driver.health()
    return world
