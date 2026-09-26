"""The choke point: the only aiohttp session that may carry a request to Hubspace (07 §1, §4).

Rule implemented here: **every** request the Afero adapter or aioafero makes — discovery, polling, readback,
writes, token refresh, sign-in pages — goes through `GatedClientSession._request`, and nothing leaves the process
unless all of these hold:

1. the URL is `https://` (never plain http);
2. the host is one of Hubspace's API/sign-in hosts (`API_HOSTS`) or a Conclave stream host
   (`conclave….afero.net`), and so is any `Host:` header the caller set;
3. `LiveGate.check_request(method, host, path, purpose)` passes — the purpose comes from the `current_purpose`
   context variable, which the driver sets around each operation (default `Purpose.ENGINE`). Background tasks
   aioafero starts inherit the purpose that was current when they were created.

A refusal raises `GateDenied` (plain-English reason, safe for /ops), bumps `stats["denied"]`, and nothing reaches
the network. Redirects are never followed: the gate checks exactly one URL per request.

Session settings (07 §4): `TCPConnector(limit_per_host=20, keepalive_timeout=60)`,
`ClientTimeout(total=8, sock_connect=3)`, and a trace hook that logs every attempt's method, host, path (query
string dropped, account id masked), status and elapsed time — never headers, bodies or tokens.

**Per-call capture** (`capture_exchange()`): the adapter wraps each operation in it and reads back what the choke
point saw — the status of the last response for that URL (aioafero turns 429/503/504 into an exception that
carries no status) and a short one-way fingerprint of the bearer that went out (so a 401 can be tied to the token
it refused without keeping the token). Nothing else is recorded.

Test seam: `GatedClientSession(gate, fake_transport=fn)`. After the gate check, `fn(method, url, kwargs)` is called
instead of the network and returns (or awaits to) a response object that behaves like aiohttp's (see
`tests/fake_afero.py`). The gate, counters, logging and listeners behave exactly as they do live.

This module imports aiohttp and never aioafero; `conductor/drivers/afero.py` is the only aioafero importer.
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import logging
import re
import warnings
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import aiohttp
from yarl import URL

from conductor.clock import Clock, RealClock
from conductor.gate import GateDenied, LiveGate, Purpose

log = logging.getLogger(__name__)

# Hubspace's REST API host, the data host aioafero names in its Host header, and the Keycloak sign-in host.
API_HOSTS = frozenset({"api2.afero.net", "semantics2.afero.net", "accounts.hubspaceconnect.com"})
# Conclave push hosts look like conclave-stream1.afero.net (03). Conclave is optional (P4) and off today.
_CONCLAVE_HOST = re.compile(r"^conclave[a-z0-9-]*\.afero\.net$")
_ACCOUNT_SEGMENT = re.compile(r"(/accounts/)[^/]+")

CONNECTOR_LIMIT_PER_HOST = 20
CONNECTOR_KEEPALIVE_S = 60
TIMEOUT_TOTAL_S = 8
TIMEOUT_CONNECT_S = 3

current_purpose: ContextVar[Purpose] = ContextVar("afero_purpose", default=Purpose.ENGINE)


@dataclass
class Exchange:
    """What the choke point saw for the requests made inside one `capture_exchange()` block."""

    last_path: str | None = None  # URL path of the last request that got an answer
    last_status: int | None = None  # its HTTP status
    bearer: str | None = None  # token_fingerprint() of the last bearer sent


current_exchange: ContextVar[Exchange | None] = ContextVar("afero_exchange", default=None)

FakeTransport = Callable[[str, URL, dict[str, Any]], Any]


@contextlib.contextmanager
def purpose_scope(purpose: Purpose) -> Iterator[None]:
    """Run the enclosed Afero calls (and any task created inside) under `purpose`."""
    token = current_purpose.set(purpose)
    try:
        yield
    finally:
        current_purpose.reset(token)


@contextlib.contextmanager
def capture_exchange() -> Iterator[Exchange]:
    """Record status and bearer fingerprint for the requests made inside the block (see the module docstring)."""
    exchange = Exchange()
    token = current_exchange.set(exchange)
    try:
        yield exchange
    finally:
        current_exchange.reset(token)


def token_fingerprint(token: str | None) -> str | None:
    """A short one-way fingerprint of a bearer token: enough to tell two tokens apart, useless to an attacker."""
    if not token:
        return None
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _bearer_of(headers: Any) -> str | None:
    if not headers:
        return None
    items = headers.items() if isinstance(headers, Mapping) else headers
    for key, value in items:
        if str(key).lower() == "authorization" and str(value).lower().startswith("bearer "):
            return token_fingerprint(str(value)[7:].strip())
    return None


def host_allowed(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return host in API_HOSTS or bool(_CONCLAVE_HOST.match(host))


def loggable_path(path: str) -> str:
    """The URL path without its query string and with the account id masked (aioafero treats it as secret)."""
    return _ACCOUNT_SEGMENT.sub(r"\1{aid}", path.split("?", 1)[0])


@dataclass(frozen=True)
class RequestRecord:
    """One request as the choke point saw it. Never holds headers, bodies or query strings."""

    method: str
    host: str
    path: str  # loggable_path()
    purpose: str
    status: int | None = None  # None when refused or when the transport failed
    elapsed_s: float = 0.0
    denied: str | None = None  # the gate's reason when refused
    error: str | None = None  # exception class name on a transport failure


def _host_header(headers: Any) -> str | None:
    if not headers:
        return None
    items = headers.items() if isinstance(headers, Mapping) else headers
    for key, value in items:
        if str(key).lower() == "host":
            return str(value)
    return None


def screen(gate: LiveGate, method: str, url: URL, headers: Any, purpose: Purpose) -> None:
    """Raise GateDenied unless this request may leave the process. Pure policy; no I/O."""
    host = (url.host or "").lower()
    if url.scheme != "https":
        raise GateDenied(
            f"Only https:// requests may reach Hubspace; refused a {url.scheme or 'relative'}:// request to "
            f"{host or 'an unnamed host'}."
        )
    if not host_allowed(host):
        raise GateDenied(f"{host or 'That host'} is not a Hubspace host, so the request was refused.")
    header_host = _host_header(headers)
    if header_host is not None and not host_allowed(header_host.split(":", 1)[0]):
        raise GateDenied("The request named a non-Hubspace Host header, so it was refused.")
    gate.check_request(method, host, url.path, purpose)


async def _trace_start(session: Any, ctx: SimpleNamespace, params: Any) -> None:
    ctx.start = session.clock.monotonic() if isinstance(session, GatedClientSession) else 0.0


async def _trace_end(session: Any, ctx: SimpleNamespace, params: Any) -> None:
    if isinstance(session, GatedClientSession):
        elapsed = session.clock.monotonic() - getattr(ctx, "start", session.clock.monotonic())
        session._finished(params.method, params.url, params.response.status, elapsed, None)


async def _trace_exception(session: Any, ctx: SimpleNamespace, params: Any) -> None:
    if isinstance(session, GatedClientSession):
        elapsed = session.clock.monotonic() - getattr(ctx, "start", session.clock.monotonic())
        session._finished(params.method, params.url, None, elapsed, type(params.exception).__name__)


def request_trace_config() -> aiohttp.TraceConfig:
    """The trace hook that makes every attempt observable (07 §4). Logs no headers, bodies or query strings."""
    trace = aiohttp.TraceConfig()
    trace.on_request_start.append(_trace_start)
    trace.on_request_end.append(_trace_end)
    trace.on_request_exception.append(_trace_exception)
    return trace


with warnings.catch_warnings():
    # aiohttp discourages subclassing ClientSession; we subclass on purpose so that aioafero, which only accepts a
    # ClientSession, can't make a request that skips the gate. Only `_request` is overridden.
    warnings.simplefilter("ignore", DeprecationWarning)

    class GatedClientSession(aiohttp.ClientSession):
        """An aiohttp session that asks the live gate before every request (see the module docstring)."""

        ATTRS = aiohttp.ClientSession.ATTRS | frozenset(
            {"gate", "clock", "stats", "listeners", "_fake_transport"}
        )

        def __init__(
            self,
            gate: LiveGate,
            *,
            clock: Clock | None = None,
            fake_transport: FakeTransport | None = None,
            listeners: list[Callable[[RequestRecord], None]] | None = None,
            **session_kwargs: Any,
        ) -> None:
            session_kwargs.setdefault(
                "connector",
                aiohttp.TCPConnector(
                    limit_per_host=CONNECTOR_LIMIT_PER_HOST, keepalive_timeout=CONNECTOR_KEEPALIVE_S
                ),
            )
            session_kwargs.setdefault(
                "timeout", aiohttp.ClientTimeout(total=TIMEOUT_TOTAL_S, sock_connect=TIMEOUT_CONNECT_S)
            )
            session_kwargs["trace_configs"] = [
                *session_kwargs.get("trace_configs", []),
                request_trace_config(),
            ]
            super().__init__(**session_kwargs)
            self.gate = gate
            self.clock: Clock = clock or RealClock()
            self._fake_transport = fake_transport
            self.stats: Counter[str] = Counter()
            self.listeners: list[Callable[[RequestRecord], None]] = list(listeners or [])

        async def _request(self, method: str, str_or_url: Any, **kwargs: Any) -> Any:
            method = method.upper()
            url = URL(str(str_or_url))
            purpose = current_purpose.get()
            try:
                screen(self.gate, method, url, kwargs.get("headers"), purpose)
            except GateDenied as denied:
                self.stats["denied"] += 1
                log.warning(
                    "afero refused %s %s%s for %s: %s",
                    method,
                    url.host or "",
                    loggable_path(url.path),
                    purpose,
                    denied.reason,
                )
                self._notify(
                    RequestRecord(
                        method, url.host or "", loggable_path(url.path), str(purpose), denied=denied.reason
                    )
                )
                raise
            # Never follow redirects: the gate checked this URL, not wherever a 3xx points.
            kwargs["allow_redirects"] = False
            self.stats["sent"] += 1
            exchange = current_exchange.get()
            if exchange is not None and (bearer := _bearer_of(kwargs.get("headers"))) is not None:
                exchange.bearer = bearer
            if self._fake_transport is None:
                return await super()._request(method, str_or_url, **kwargs)
            return await self._fake_request(method, url, kwargs)

        async def _fake_request(self, method: str, url: URL, kwargs: dict[str, Any]) -> Any:
            if self.closed:
                raise RuntimeError("Session is closed")
            if kwargs.get("params"):
                url = url.update_query(kwargs["params"])
            start = self.clock.monotonic()
            try:
                response = self._fake_transport(method, url, kwargs)
                if inspect.isawaitable(response):
                    response = await response
            except Exception as exc:
                self._finished(method, url, None, self.clock.monotonic() - start, type(exc).__name__)
                raise
            self._finished(method, url, response.status, self.clock.monotonic() - start, None)
            return response

        def _finished(
            self, method: str, url: URL, status: int | None, elapsed_s: float, error: str | None
        ) -> None:
            path = loggable_path(url.path)
            purpose = str(current_purpose.get())
            if status is not None:
                self.stats[f"status_{status}"] += 1
                exchange = current_exchange.get()
                if exchange is not None:
                    exchange.last_path, exchange.last_status = url.path, status
            else:
                self.stats["transport_errors"] += 1
            level = logging.DEBUG if method == "GET" and status is not None and status < 300 else logging.INFO
            log.log(
                level,
                "afero %s %s%s -> %s in %.0f ms [%s]",
                method,
                url.host or "",
                path,
                status if status is not None else error,
                elapsed_s * 1000,
                purpose,
            )
            self._notify(RequestRecord(method, url.host or "", path, purpose, status, elapsed_s, None, error))

        def _notify(self, record: RequestRecord) -> None:
            for fn in list(self.listeners):
                try:
                    fn(record)
                except Exception:
                    log.exception("afero request listener failed")
