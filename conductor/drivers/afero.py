"""The Afero adapter: `AferoDriver` and `AferoAccounts` — the ONLY module in the Conductor that imports aioafero
(07 §1, §3, §4, §5; pinned aioafero 9.3.0, behaviour verified against its source).

Rules implemented here:
- **Every request goes through the choke point.** Each account's `AferoBridgeV1` is built on a
  `GatedClientSession` (`afero_transport.py`), and every operation runs inside `purpose_scope(purpose)`, so the
  live gate sees the right purpose for writes, reads, token refreshes and aioafero's own background polling.
  Writes are also pre-checked with `LiveGate.check_write` so a refused write sends *nothing* — not even a token
  refresh.
- **One `write()` = one PUT**, sent with `bridge.request("put", …/metadevices/{id}/state, json=…,
  headers={host: semantics2.afero.net, content-type: application/json; charset=utf-8})` inside
  `asyncio.wait_for(…, 8)`. `lastUpdateTime` is stamped in epoch **milliseconds** from the injected clock.
- **No hidden retries.** `aioafero.v1.v1_const.MAX_RETRIES` is forced to 1 at import and again before every send
  (aioafero reads it at call time); the executor owns retries.
- **Nothing raises for HTTP or transport problems.** Results are classified into `Outcome`: gate refusal →
  DENIED; `ExceededMaximumRetries`, 403 (`HTTPForbidden`, the observed throttling signature), 429/503/504 →
  THROTTLED; 401 or `InvalidAuth` → AUTH; 404 → NOT_FOUND; other 4xx → CLIENT_ERROR; 5xx → SERVER_ERROR;
  timeouts → TIMEOUT; `aiohttp.ClientError`/`OSError` → CONNECTION; a 2xx readback that isn't a list of rows →
  MALFORMED.
- **Startup never waits on Afero.** `start()` schedules background connects (the `open()` equivalent — account
  id, first discovery — under a 15 s `wait_for`, retried with backoff) and returns at once; problems show in
  `health()` / `AferoAccounts.status()`.
- **Tokens.** Only refresh tokens are stored (TokenStore). aioafero rotates the refresh token on every refresh
  with no callback, so the adapter compares `bridge.refresh_token` with the last persisted value after every
  operation, every few seconds in the background, and on close, and saves any change. A dead token
  (`InvalidAuth`) marks the account `needs_reauth` and the adapter stops using it — it never loops logins.
- **Outages.** Consecutive CONNECTION/TIMEOUT/SERVER_ERROR outcomes on an account mark it degraded; a watchdog
  probes every ~10 s and, on the first OK, calls the `on_recovery` subscribers (the reconciler re-asserts).
  aioafero's DISCONNECTED/RECONNECTED events are never used as the outage signal.
- **Sign-in** (`AferoAccounts`): `LiveGate.check_account(email)` runs before anything touches the network; an
  emailed-code challenge keeps the same `AferoAuth` object (and its session's cookie jar) in memory under a
  random nonce for `PENDING_TTL_S`. Messages never echo what was submitted.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import secrets
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from aioafero.errors import AferoError, ExceededMaximumRetries, InvalidAuth, InvalidOTP, OTPRequired
from aioafero.types import EventType
from aioafero.v1 import AferoBridgeV1, v1_const
from aioafero.v1.auth import AferoAuth, TokenData
from aiohttp import web_exceptions

from conductor import redact
from conductor.accounts_api import PENDING_TTL_S, AccountStatus, LoginOutcome, LoginState
from conductor.clock import Clock
from conductor.config import Registry, unknown_devices
from conductor.drivers.afero_transport import GatedClientSession, purpose_scope
from conductor.drivers.base import DeviceInfo, Outcome, ReadResult, StateRow, WriteResult
from conductor.gate import GateDenied, LiveGate, Purpose
from conductor.rows import payload_bytes
from conductor.telemetry import Telemetry
from conductor.tokens import TokenStore, TokenStoreUnavailable

log = logging.getLogger(__name__)

API_BASE = "https://api2.afero.net"
API_HOST = "api2.afero.net"
DATA_HOST = "semantics2.afero.net"
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
EXPANSIONS = "state,capabilities,semantics"
DEVICE_TYPE_ID = "metadevice.device"

SEND_TIMEOUT_S = 8.0
CONNECT_TIMEOUT_S = 15.0
LOGIN_TIMEOUT_S = 30.0
CONNECT_BACKOFF_S = (5.0, 10.0, 30.0, 60.0, 120.0, 300.0)
POLLING_INTERVAL_S = 30
DEGRADED_AFTER = 2  # consecutive outage outcomes on one account
OUTAGE_PROBE_INTERVAL_S = 10.0
TOKEN_CHECK_INTERVAL_S = 5.0
FRESH_TOKEN_MIN_LEFT_S = 15.0
MAX_OTP_ATTEMPTS = 5

OUTAGE_OUTCOMES = frozenset({Outcome.CONNECTION, Outcome.TIMEOUT, Outcome.SERVER_ERROR})
# Everything aioafero, aiohttp or the gate can raise on a request path; the adapter classifies these instead of
# raising. (HTTPForbidden is an aiohttp *web* exception, not a ClientError; GateDenied is an OSError.)
REQUEST_ERRORS: tuple[type[BaseException], ...] = (
    AferoError,
    aiohttp.ClientError,
    web_exceptions.HTTPException,
    OSError,
    TimeoutError,
    RuntimeError,
    LookupError,
    ValueError,
    TypeError,
    AttributeError,
)
_WRITE_HEADERS = {"host": DATA_HOST, "content-type": JSON_CONTENT_TYPE}


def force_single_attempt() -> None:
    """aioafero retries 429/503/504 up to MAX_RETRIES times in ~1.25 s; one attempt keeps the ≤ 1 write/s budget."""
    v1_const.MAX_RETRIES = 1


force_single_attempt()


class AferoRequestFailed(RuntimeError):
    """A read-only call (`fetch_profile`, `discover`) that couldn't complete. `message` is safe for /ops."""

    def __init__(self, outcome: Outcome, message: str, http_status: int | None = None):
        super().__init__(message)
        self.outcome = outcome
        self.message = message
        self.http_status = http_status


# ---- classification ------------------------------------------------------------------------------------------
def classify_status(status: int) -> tuple[Outcome, str | None]:
    """HTTP status → Outcome (07 §4, 03: 403 is the observed throttling signature)."""
    if 200 <= status < 300:
        return Outcome.OK, None
    if status in (429, 503, 504):
        return Outcome.THROTTLED, f"Hubspace is throttling or busy (HTTP {status})."
    if status == 403:
        return Outcome.THROTTLED, "Hubspace answered HTTP 403, its usual throttling signature."
    if status == 401:
        return Outcome.AUTH, "Hubspace rejected the sign-in token (HTTP 401)."
    if status == 404:
        return Outcome.NOT_FOUND, "Hubspace doesn't know that device on this account (HTTP 404)."
    if 400 <= status < 500:
        return Outcome.CLIENT_ERROR, f"Hubspace refused the request (HTTP {status})."
    if status >= 500:
        return Outcome.SERVER_ERROR, f"Hubspace had a server error (HTTP {status})."
    return Outcome.SERVER_ERROR, f"Hubspace answered with an unexpected HTTP {status}."


def classify_exception(exc: BaseException) -> tuple[Outcome, str]:
    """Exception from aioafero/aiohttp/the gate → Outcome + plain-English error. Never includes secrets."""
    # Order matters: GateDenied is a PermissionError (an OSError); HTTPForbidden is not a ClientError.
    if isinstance(exc, GateDenied):
        return Outcome.DENIED, exc.reason
    if isinstance(exc, ExceededMaximumRetries):
        return Outcome.THROTTLED, "Hubspace is throttling or busy (429/503/504)."
    if isinstance(exc, web_exceptions.HTTPForbidden):
        return Outcome.THROTTLED, "Hubspace answered HTTP 403, its usual throttling signature."
    if isinstance(exc, InvalidAuth):
        return Outcome.AUTH, "Hubspace no longer accepts this account's token: re-sign-in required."
    if isinstance(exc, TimeoutError):
        return Outcome.TIMEOUT, "Hubspace didn't answer in time."
    if isinstance(exc, (aiohttp.ClientError, OSError)):
        return Outcome.CONNECTION, f"Couldn't reach Hubspace ({type(exc).__name__})."
    if isinstance(exc, RuntimeError) and "Session is closed" in str(exc):
        return Outcome.CONNECTION, "The Hubspace connection was closed."
    if isinstance(exc, AferoError):
        return Outcome.SERVER_ERROR, f"Hubspace answered in an unexpected way ({type(exc).__name__})."
    return Outcome.SERVER_ERROR, f"Unexpected error talking to Hubspace ({type(exc).__name__})."


# ---- parsing ---------------------------------------------------------------------------------------------------
def parse_state_body(body: bytes, device_id: str) -> tuple[list[StateRow] | None, int, str | None]:
    """A state GET body → (rows, skipped, error). rows is None when the body isn't a list of rows (MALFORMED).

    Accepts `{"metadeviceId", "values": [...]}` or a bare list. Rows without a functionClass are skipped and
    counted; an empty list is valid (compare() then reports every expected row as missing).
    """
    if not body or not body.strip():
        return None, 0, "Hubspace answered 2xx with an empty body."
    try:
        data = json.loads(body)
    except ValueError:
        return None, 0, "Hubspace answered 2xx but the body isn't JSON."
    if isinstance(data, dict):
        named = data.get("metadeviceId")
        if named is not None and named != device_id:
            return None, 0, "Hubspace's readback named a different device."
        values = data.get("values")
    else:
        values = data
    if not isinstance(values, list):
        return None, 0, "Hubspace's readback doesn't contain a list of state rows."
    rows: list[StateRow] = []
    skipped = 0
    for value in values:
        if isinstance(value, dict) and isinstance(value.get("functionClass"), str) and value["functionClass"]:
            rows.append(StateRow.from_wire(value))
        else:
            skipped += 1
    return rows, skipped, None


def device_info_from_raw(raw: dict, account: str) -> DeviceInfo:
    """A metadevice object in Afero's raw API shape → DeviceInfo (functions/state as the device declares them)."""
    description = raw.get("description") or {}
    device = description.get("device") or {}
    values = (raw.get("state") or {}).get("values") or []
    states = [
        StateRow.from_wire(v)
        for v in values
        if isinstance(v, dict) and isinstance(v.get("functionClass"), str)
    ]
    return DeviceInfo(
        device_id=str(raw.get("id") or ""),
        account=account,
        name=raw.get("friendlyName") or device.get("defaultName") or "",
        model=device.get("model") or "",
        device_class=device.get("deviceClass") or "",
        functions=list(description.get("functions") or []),
        states=states,
        raw=raw,
    )


# ---- per-account link ----------------------------------------------------------------------------------------
@dataclass
class _Reply:
    outcome: Outcome
    status: int | None = None
    body: bytes = b""
    error: str | None = None
    elapsed_s: float = 0.0


@dataclass
class _Link:
    account: str
    bridge: AferoBridgeV1 | None = None
    account_id: str | None = None
    purpose: Purpose | None = None
    connected: bool = False
    connecting: bool = False
    needs_reauth: bool = False
    last_error: str | None = None
    persisted_token: str | None = field(default=None, repr=False)  # never leaves this object
    last_refresh_at: float | None = None
    connected_at: float | None = None
    failures: int = 0
    degraded: bool = False
    degraded_since: float | None = None
    device_ids: set[str] = field(default_factory=set)
    unknown: list[str] = field(default_factory=list)
    task: asyncio.Task | None = None
    watchdog: asyncio.Task | None = None
    attempted: asyncio.Event | None = None


RegistrySource = Registry | Callable[[], Registry]


class AferoDriver:
    """`DeviceDriver` over the Afero cloud. Addresses are metadevice ids from the registry overlay."""

    name = "afero"
    simulated = False

    def __init__(
        self,
        registry: RegistrySource,
        gate: LiveGate,
        token_store: TokenStore,
        *,
        clock: Clock,
        telemetry: Telemetry | None,
        session_factory: Callable[[], GatedClientSession] | None = None,
        afero_wall: Callable[[], float] = time.time,
    ):
        """`afero_wall` is aioafero's own wall clock (it stamps token expiry with `datetime.now()`); it is used
        only to read how long the current bearer has left. Everything else uses `clock`."""
        self._registry = registry
        self.gate = gate
        self.token_store = token_store
        self.clock = clock
        self.telemetry = telemetry
        self._session_factory = session_factory or (lambda: GatedClientSession(gate, clock=clock))
        self._afero_wall = afero_wall
        self._session: GatedClientSession | None = None
        self._links: dict[str, _Link] = {}
        self._tasks: set[asyncio.Task] = set()
        self._keeper: asyncio.Task | None = None
        self._closed = False
        self.stats: Counter[str] = Counter()
        self.on_recovery: list[Callable[[str], Any]] = []

    # ---- registry / accounts ---------------------------------------------------------------------------------
    @property
    def registry(self) -> Registry:
        return self._registry() if callable(self._registry) else self._registry

    def accounts(self) -> list[str]:
        return list(self.registry.accounts())

    def account_of(self, device_id: str) -> str:
        entry = self.registry.logical_for(device_id)
        if entry is None or entry.metadevice_id != device_id:
            raise LookupError(
                f"{device_id} isn't a registered Hubspace device (no registry entry has that metadevice id)."
            )
        return entry.account

    def _link(self, account: str) -> _Link:
        if account not in self.registry.accounts():
            raise LookupError(f"{account!r} isn't one of the registry's accounts.")
        link = self._links.get(account)
        if link is None:
            link = self._links[account] = _Link(account)
        return link

    def connect_purpose(self) -> Purpose | None:
        """The purpose a background connection (and aioafero's polling) runs under, or None when the gate allows
        no device traffic at all on this runtime."""
        for purpose in (Purpose.ENGINE, Purpose.BENCH):
            if self.gate.purpose_allowed(purpose)[0]:
                return purpose
        return None

    def new_session(self) -> GatedClientSession:
        """A fresh gated session (sign-ins use their own, so Keycloak's cookie jar isn't shared)."""
        return self._session_factory()

    async def _get_session(self) -> GatedClientSession:
        if self._session is None or self._session.closed:
            self._session = self.new_session()
        return self._session

    def _spawn(self, coro: Any) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _record(self, kind: str, **fields: Any) -> None:
        if self.telemetry is not None:
            self.telemetry.record(kind, source="afero", **fields)

    # ---- lifecycle -------------------------------------------------------------------------------------------
    async def start(self) -> None:
        """Schedule background connects for accounts with stored tokens and return immediately."""
        if self._closed:
            return
        purpose = self.connect_purpose()
        for account in self.accounts():
            link = self._link(account)
            if purpose is None:
                link.last_error = self.gate.purpose_allowed(Purpose.ENGINE)[1]
                continue
            if self._has_token(link):
                self._ensure_connecting(link)
        if self._keeper is None or self._keeper.done():
            self._keeper = self._spawn(self._token_keeper())

    def _has_token(self, link: _Link) -> bool:
        if not self.token_store.available:
            link.last_error = self.token_store.problem
            return False
        try:
            return self.token_store.status(link.account) is not None
        except TokenStoreUnavailable as exc:
            link.last_error = str(exc)
            return False

    def _ensure_connecting(self, link: _Link, token_data: TokenData | None = None) -> None:
        if self._closed or (link.task is not None and not link.task.done()):
            return
        link.attempted = asyncio.Event()
        link.task = self._spawn(self._connect_loop(link, token_data))

    async def connect(self, account: str, *, token_data: TokenData | None = None) -> bool:
        """(Re)connect one account from its stored token and wait for the first attempt (≤ ~15 s).

        `token_data` (fresh from a sign-in) lets the bridge skip an immediate refresh. Returns whether the link is
        connected; on False, `account_status(account).last_error` says why and retries continue in the background.
        """
        link = self._link(account)
        await self._drop_link(link)
        link.needs_reauth = False
        link.last_error = None
        if self.connect_purpose() is None:
            link.last_error = self.gate.purpose_allowed(Purpose.ENGINE)[1]
            return False
        self._ensure_connecting(link, token_data)
        if link.attempted is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(link.attempted.wait(), CONNECT_TIMEOUT_S + 5)
        return link.connected

    async def disconnect(self, account: str, *, forget: bool = True) -> None:
        """Stop using an account. With `forget`, its stored token is deleted too (a new sign-in is needed)."""
        link = self._link(account)
        await self._drop_link(link)
        if forget:
            self.token_store.delete(account)
            link.persisted_token = None
            link.last_error = "Disconnected on /ops. Sign in again to reconnect."

    async def _drop_link(self, link: _Link) -> None:
        for task in (link.task, link.watchdog):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)  # our own cancellation still propagates
        link.task = link.watchdog = None
        bridge, link.bridge = link.bridge, None
        link.connected = link.degraded = False
        link.failures = 0
        if bridge is not None:
            self._persist_rotation(link, bridge)
            await self._close_bridge(bridge)

    async def close(self) -> None:
        self._closed = True
        if self._keeper is not None:
            self._keeper.cancel()
        for link in self._links.values():
            await self._drop_link(link)
        tasks = [t for t in self._tasks if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._session is not None and not self._session.closed:
            await self._session.close()

    # ---- connecting ------------------------------------------------------------------------------------------
    async def _connect_loop(self, link: _Link, token_data: TokenData | None) -> None:
        attempt = 0
        try:
            while not self._closed and not link.needs_reauth:
                purpose = self.connect_purpose()
                if purpose is None:
                    link.last_error = self.gate.purpose_allowed(Purpose.ENGINE)[1]
                    return
                ok = await self._connect_once(link, purpose, token_data)
                token_data = None
                if link.attempted is not None:
                    link.attempted.set()
                if ok or link.needs_reauth or link.persisted_token is None:
                    return
                delay = CONNECT_BACKOFF_S[min(attempt, len(CONNECT_BACKOFF_S) - 1)]
                attempt += 1
                await self.clock.sleep(delay)
        finally:
            if link.attempted is not None:
                link.attempted.set()

    async def _connect_once(self, link: _Link, purpose: Purpose, token_data: TokenData | None) -> bool:
        """The `AferoBridgeV1.open()` equivalent: account id + first discovery, under a 15 s cap."""
        try:
            token = self.token_store.load(link.account)
        except TokenStoreUnavailable as exc:
            self._mark_reauth(link, str(exc))
            return False
        if not token:
            link.persisted_token = None
            link.last_error = "No Hubspace sign-in is stored for this account. Connect it on /ops/accounts."
            return False
        extra: dict[str, Any] = {}
        if token_data is not None and token_data.refresh_token == token and token_data.token:
            extra = {"token": token_data.token, "token_expiration": token_data.expiration}
        session = await self._get_session()
        bridge = AferoBridgeV1(
            link.account,  # the logical account name — never the email
            token,
            session,
            polling_interval=POLLING_INTERVAL_S,
            poll_version=False,
            hide_secrets=True,
            **extra,
        )
        bridge.events.subscribe(
            lambda *_: self._mark_reauth(
                link, "Hubspace no longer accepts this account's token: re-sign-in required.", bridge
            ),
            event_filter=EventType.INVALID_AUTH,
        )
        link.persisted_token = token
        link.connecting = True
        link.purpose = purpose
        ok = False
        try:
            force_single_attempt()
            with purpose_scope(purpose):
                await asyncio.wait_for(self._open(bridge), CONNECT_TIMEOUT_S)
            ok = not link.needs_reauth
        except REQUEST_ERRORS as exc:
            outcome, error = classify_exception(exc)
            if isinstance(exc, TimeoutError):
                error = "Hubspace didn't finish the first discovery within 15 s; retrying in the background."
            if outcome is Outcome.AUTH:
                self._mark_reauth(link, error)
            else:
                link.last_error = error
            log.warning("afero connect for %s failed: %s", link.account, error)
        finally:
            link.connecting = False
            self._persist_rotation(link, bridge)
            if not ok:
                await self._close_bridge(bridge)
        if not ok:
            return False
        link.bridge = bridge
        link.account_id = bridge.account_id
        redact.add_secret(link.account_id)  # aioafero logs full URLs (with the account id) at INFO
        link.connected = True
        link.connected_at = self.clock.now()
        link.last_error = None
        link.failures = 0
        link.degraded = False
        link.device_ids = {
            d for d in bridge.known_afero_device_ids if bridge.get_afero_device(d).split is None
        }
        self._check_unknown(link)
        log.info("afero account %s connected (%d devices visible)", link.account, len(link.device_ids))
        return True

    @staticmethod
    async def _open(bridge: AferoBridgeV1) -> None:
        await bridge.initialize()
        await bridge.async_block_until_done()

    @staticmethod
    async def _close_bridge(bridge: AferoBridgeV1) -> None:
        try:
            await asyncio.wait_for(bridge.close(), 5)
        except REQUEST_ERRORS as exc:
            log.warning("afero bridge didn't close cleanly (%s)", type(exc).__name__)

    def _mark_reauth(self, link: _Link, message: str, bridge: AferoBridgeV1 | None = None) -> None:
        """A dead token: stop using the account (never loop logins) and say so on /ops."""
        if bridge is not None and link.bridge is not None and bridge is not link.bridge:
            return  # an old bridge's late event
        first = not link.needs_reauth
        link.needs_reauth = True
        link.connected = False
        link.last_error = message
        old, link.bridge = link.bridge, None
        if old is not None:
            self._persist_rotation(link, old)
            self._spawn(self._close_bridge(old))
        if first:
            log.warning("afero account %s needs a fresh sign-in", link.account)
            self._record("alert", level="error", account=link.account, message=message)

    def _check_unknown(self, link: _Link) -> None:
        unknown = unknown_devices(self.registry, link.device_ids)
        if unknown and unknown != link.unknown:
            self._record(
                "alert",
                level="warn",
                account=link.account,
                message=f"{len(unknown)} device(s) on this account aren't in the registry; they stay read-only.",
            )
        link.unknown = unknown

    # ---- tokens ----------------------------------------------------------------------------------------------
    def _persist_rotation(self, link: _Link, bridge: AferoBridgeV1 | None = None) -> None:
        bridge = bridge or link.bridge
        if bridge is None or not self.token_store.available:
            return
        current = bridge.refresh_token
        if not current or current == link.persisted_token:
            return
        try:
            generation = self.token_store.save(link.account, current)
        except (OSError, TokenStoreUnavailable, ValueError) as exc:
            link.last_error = (
                f"Couldn't save the rotated Hubspace token ({type(exc).__name__}); check the data dir."
            )
            log.error(
                "afero token for %s rotated but could not be saved: %s", link.account, type(exc).__name__
            )
            return
        link.persisted_token = current
        link.last_refresh_at = self.clock.now()
        log.info("afero token for %s rotated; saved generation %d", link.account, generation)

    async def _token_keeper(self) -> None:
        """aioafero's background polling can rotate the token between our calls; persist it within seconds."""
        while not self._closed:
            await self.clock.sleep(TOKEN_CHECK_INTERVAL_S)
            for link in list(self._links.values()):
                if link.bridge is not None:
                    self._persist_rotation(link)

    async def ensure_fresh_token(
        self, account: str, min_left_s: float = FRESH_TOKEN_MIN_LEFT_S, *, purpose: Purpose | None = None
    ) -> Outcome:
        """Make sure the account's bearer has ≥ `min_left_s` left before a burst (07 §6).

        If it expires sooner, force aioafero to refresh (`expiration=0`) and send one cheap GET (users/me).
        Returns OK when the bearer is fresh, else the outcome that stopped it.
        """
        link = self._ready_link(account)
        if isinstance(link, _Reply):
            return link.outcome
        bridge = link.bridge
        data = bridge.token_data
        if data is not None and data.token and data.expiration - self._afero_wall() >= min_left_s:
            return Outcome.OK
        if data is not None:
            bridge.set_token_data(data._replace(expiration=0))
        reply = await self._send(
            link,
            purpose or link.purpose or Purpose.ENGINE,
            "get",
            f"{API_BASE}/v1/users/me",
            headers={"host": API_HOST},
        )
        return reply.outcome

    # ---- the request path ------------------------------------------------------------------------------------
    def _ready_link(self, account: str) -> _Link | _Reply:
        link = self._link(account)
        if link.needs_reauth:
            return _Reply(
                Outcome.AUTH, error=link.last_error or "Hubspace needs a fresh sign-in for this account."
            )
        if link.bridge is not None and link.connected:
            return link
        if not self._has_token(link):
            return _Reply(
                Outcome.AUTH,
                error=link.last_error
                or "No Hubspace sign-in is stored for this account. Connect it on /ops.",
            )
        if self.connect_purpose() is None:
            return _Reply(Outcome.DENIED, error=self.gate.purpose_allowed(Purpose.ENGINE)[1])
        self._ensure_connecting(link)
        why = f" (last error: {link.last_error})" if link.last_error else ""
        return _Reply(Outcome.CONNECTION, error=f"Not connected to Hubspace for this account yet{why}.")

    async def _send(self, link: _Link, purpose: Purpose, method: str, url: str, **kwargs: Any) -> _Reply:
        bridge = link.bridge
        force_single_attempt()
        start = self.clock.monotonic()
        invalid_auth = False
        try:
            with purpose_scope(purpose):
                response = await asyncio.wait_for(bridge.request(method, url, **kwargs), SEND_TIMEOUT_S)
                body = await response.read()
        except REQUEST_ERRORS as exc:
            outcome, error = classify_exception(exc)
            invalid_auth = isinstance(exc, InvalidAuth)
            reply = _Reply(outcome, None, b"", error, self.clock.monotonic() - start)
        else:
            outcome, error = classify_status(response.status)
            reply = _Reply(outcome, response.status, body or b"", error, self.clock.monotonic() - start)
        finally:
            self._persist_rotation(link, bridge)
        if invalid_auth:
            self._mark_reauth(link, reply.error or "re-sign-in required", bridge)
        elif reply.status == 401 and bridge.token_data is not None:
            # A stale bearer: make the next call refresh it. A dead refresh token then shows up as InvalidAuth.
            bridge.set_token_data(bridge.token_data._replace(expiration=0))
        self._note_outcome(link, reply.outcome)
        return reply

    def _state_url(self, link: _Link, device_id: str) -> str:
        return f"{API_BASE}/v1/accounts/{link.account_id}/metadevices/{device_id}/state"

    def _denied(self, device_id: str, purpose: Purpose, reason: str) -> None:
        self.stats["denied"] += 1
        self._record("gate_denied", device_id=device_id, reason=reason, purpose=str(purpose))

    # ---- outages ---------------------------------------------------------------------------------------------
    def _note_outcome(self, link: _Link, outcome: Outcome) -> None:
        if outcome in OUTAGE_OUTCOMES:
            link.failures += 1
            if link.failures >= DEGRADED_AFTER and not link.degraded:
                link.degraded = True
                link.degraded_since = self.clock.now()
                self._record(
                    "alert",
                    level="warn",
                    account=link.account,
                    message="Hubspace looks unreachable for this account; probing every 10 s.",
                )
                if link.watchdog is None or link.watchdog.done():
                    link.watchdog = self._spawn(self._watchdog(link))
        elif outcome is Outcome.OK:
            link.failures = 0
            if link.degraded:
                link.degraded = False
                link.degraded_since = None
                self._record(
                    "alert", level="info", account=link.account, message="Hubspace is reachable again."
                )
                self._fire_recovery(link.account)
        elif outcome is not Outcome.DENIED:
            link.failures = 0  # the cloud answered: not an outage, even if it didn't like the request

    async def _watchdog(self, link: _Link) -> None:
        while link.degraded and not self._closed and link.bridge is not None:
            await self.clock.sleep(OUTAGE_PROBE_INTERVAL_S)
            if not link.degraded or link.bridge is None:
                return
            await self._send(
                link,
                link.purpose or Purpose.ENGINE,
                "get",
                f"{API_BASE}/v1/users/me",
                headers={"host": API_HOST},
            )

    def _fire_recovery(self, account: str) -> None:
        for callback in list(self.on_recovery):
            try:
                result = callback(account)
                if inspect.isawaitable(result):
                    self._spawn(result)
            except Exception:
                log.exception("afero on_recovery subscriber failed")

    # ---- DeviceDriver ----------------------------------------------------------------------------------------
    async def write(self, device_id: str, rows: Sequence[StateRow], *, purpose: Purpose) -> WriteResult:
        """Exactly one PUT of `rows`; never raises for HTTP/transport problems; DENIED sends nothing."""
        sent_at = self.clock.now()
        result = WriteResult(device_id=device_id, outcome=Outcome.OK, sent_at=sent_at)
        try:
            account = self.account_of(device_id)
        except LookupError as exc:
            result.outcome, result.error = Outcome.NOT_FOUND, str(exc)
            return result
        try:
            self.gate.check_write(device_id, purpose)
        except GateDenied as denied:
            self._denied(device_id, purpose, denied.reason)
            result.outcome, result.error = Outcome.DENIED, denied.reason
            return result
        link = self._ready_link(account)
        if isinstance(link, _Reply):
            result.outcome, result.error = link.outcome, link.error
            return result
        stamp_ms = round(sent_at * 1000)
        body = {"metadeviceId": device_id, "values": [row.to_wire(stamp_ms) for row in rows]}
        result.bytes_sent = payload_bytes(device_id, rows, stamp_ms)
        reply = await self._send(
            link, purpose, "put", self._state_url(link, device_id), json=body, headers=dict(_WRITE_HEADERS)
        )
        result.outcome, result.http_status, result.error = reply.outcome, reply.status, reply.error
        result.elapsed_s = reply.elapsed_s
        if reply.status is not None:
            result.acked_at = self.clock.now()
        if reply.outcome is Outcome.DENIED:
            result.bytes_sent = 0
            self._denied(device_id, purpose, reply.error or "")
        elif reply.outcome is Outcome.OK and reply.body.strip():
            echoed, _, _ = parse_state_body(reply.body, device_id)
            result.echoed = echoed or []
        return result

    async def read_state(self, device_id: str, *, purpose: Purpose = Purpose.ENGINE) -> ReadResult:
        """REST readback. Never raises for HTTP/transport problems; a 2xx that isn't rows is MALFORMED."""
        fetched_at = self.clock.now()
        result = ReadResult(device_id=device_id, outcome=Outcome.OK, fetched_at=fetched_at)
        try:
            account = self.account_of(device_id)
        except LookupError as exc:
            result.outcome, result.error = Outcome.NOT_FOUND, str(exc)
            return result
        allowed, why = self.gate.purpose_allowed(purpose)
        if not allowed:
            self._denied(device_id, purpose, why)
            result.outcome, result.error = Outcome.DENIED, why
            return result
        link = self._ready_link(account)
        if isinstance(link, _Reply):
            result.outcome, result.error = link.outcome, link.error
            return result
        reply = await self._send(
            link, purpose, "get", self._state_url(link, device_id), headers={"host": DATA_HOST}
        )
        result.outcome, result.http_status, result.error = reply.outcome, reply.status, reply.error
        result.elapsed_s = reply.elapsed_s
        result.fetched_at = self.clock.now()
        if reply.outcome is Outcome.DENIED:
            self._denied(device_id, purpose, reply.error or "")
        if reply.outcome is not Outcome.OK:
            return result
        rows, skipped, error = parse_state_body(reply.body, device_id)
        if rows is None:
            self.stats["malformed_reads"] += 1
            result.outcome, result.error = Outcome.MALFORMED, error
            return result
        if skipped:
            self.stats["rows_skipped"] += skipped
            log.warning("afero readback for %s skipped %d malformed row(s)", device_id, skipped)
        result.rows = rows
        return result

    async def fetch_profile(self, device_id: str, *, purpose: Purpose = Purpose.ENGINE) -> DeviceInfo:
        """Functions + state for one device (Gate A). Raises LookupError for an unknown device, GateDenied when
        the gate refuses, and AferoRequestFailed for anything else."""
        account = self.account_of(device_id)
        allowed, why = self.gate.purpose_allowed(purpose)
        if not allowed:
            self._denied(device_id, purpose, why)
            raise GateDenied(why)
        link = self._ready_link(account)
        if isinstance(link, _Reply):
            raise AferoRequestFailed(link.outcome, link.error or "Hubspace is not connected.")
        url = f"{API_BASE}/v1/accounts/{link.account_id}/metadevices/{device_id}"
        reply = await self._send(
            link, purpose, "get", url, params={"expansions": EXPANSIONS}, headers={"host": DATA_HOST}
        )
        if reply.outcome is Outcome.NOT_FOUND:
            raise LookupError(f"Hubspace doesn't know {device_id} on this account.")
        if reply.outcome is Outcome.DENIED:
            raise GateDenied(reply.error or "refused")
        if reply.outcome is not Outcome.OK:
            raise AferoRequestFailed(reply.outcome, reply.error or "failed", reply.status)
        try:
            raw = json.loads(reply.body)
        except ValueError:
            raw = None
        if not isinstance(raw, dict):
            raise AferoRequestFailed(
                Outcome.MALFORMED, "Hubspace's device profile isn't a JSON object.", reply.status
            )
        return device_info_from_raw(raw, account)

    async def discover(self, account: str | None = None) -> list[DeviceInfo]:
        """Every metadevice.device the connected account(s) can see. Read-only. With `account`, failures raise;
        without it, accounts that aren't connected are skipped."""
        if account is not None:
            self._link(account)
            accounts = [account]
        else:
            accounts = [a for a in self.accounts() if a in self._links and self._links[a].connected]
        found: list[DeviceInfo] = []
        for name in accounts:
            link = self._ready_link(name)
            if isinstance(link, _Reply):
                if account is not None:
                    raise AferoRequestFailed(link.outcome, link.error or "Hubspace is not connected.")
                continue
            url = f"{API_BASE}/v1/accounts/{link.account_id}/metadevices"
            reply = await self._send(
                link,
                link.purpose or Purpose.ENGINE,
                "get",
                url,
                params={"expansions": EXPANSIONS},
                headers={"host": DATA_HOST},
            )
            data: Any = None
            if reply.outcome is Outcome.OK:
                with contextlib.suppress(ValueError):
                    data = json.loads(reply.body)
                if not isinstance(data, list):
                    reply = _Reply(
                        Outcome.MALFORMED, reply.status, error="Hubspace's device list isn't a list."
                    )
            if reply.outcome is not Outcome.OK:
                if account is not None:
                    raise AferoRequestFailed(reply.outcome, reply.error or "failed", reply.status)
                log.warning("afero discovery for %s failed: %s", name, reply.error)
                continue
            infos = [
                device_info_from_raw(d, name)
                for d in data
                if isinstance(d, dict) and d.get("typeId") == DEVICE_TYPE_ID
            ]
            link.device_ids = {i.device_id for i in infos}
            self._check_unknown(link)
            found.extend(infos)
        return found

    # ---- status ----------------------------------------------------------------------------------------------
    def account_status(self, account: str) -> AccountStatus:
        link = self._link(account)
        status = AccountStatus(
            account=account,
            connected=link.connected,
            needs_reauth=link.needs_reauth,
            simulated=False,
            last_refresh_at=link.last_refresh_at,
            device_count=len(link.device_ids),
            unknown_devices=list(link.unknown),
            last_error=link.last_error,
        )
        try:
            token = self.token_store.status(account)
        except TokenStoreUnavailable as exc:
            status.last_error = status.last_error or str(exc)
            token = None
        if token is not None:
            status.token_generation = token.generation
            status.token_age_s = round(token.age_s, 1)
        return status

    def health(self) -> dict:
        """JSON-safe status for /ops. No tokens, emails or account ids."""
        accounts = {}
        for account in self.accounts():
            link = self._link(account)
            accounts[account] = {
                **self.account_status(account).to_json(),
                "connecting": link.connecting
                or (link.task is not None and not link.task.done() and not link.connected),
                "degraded": link.degraded,
                "degraded_since": link.degraded_since,
                "consecutive_failures": link.failures,
                "connected_at": link.connected_at,
            }
        purpose = self.connect_purpose()
        return {
            "driver": self.name,
            "simulated": False,
            "purpose": str(purpose) if purpose else None,
            "token_store": "ready" if self.token_store.available else self.token_store.problem,
            "requests": dict(self._session.stats) if self._session is not None else {},
            "denied": self.stats["denied"],
            "rows_skipped": self.stats["rows_skipped"],
            "malformed_reads": self.stats["malformed_reads"],
            "accounts": accounts,
        }


# ---- sign-in -------------------------------------------------------------------------------------------------
@dataclass
class _Pending:
    account: str
    auth: AferoAuth = field(repr=False)
    session: GatedClientSession = field(repr=False)
    expires_at: float
    attempts: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


EXPIRED_MESSAGE = (
    "That sign-in has expired or isn't known. A pending sign-in lasts 10 minutes and is lost if the Conductor "
    "restarts; start the sign-in again."
)


class AferoAccounts:
    """`AccountManager` for real Hubspace accounts (07 §3). Stores only refresh tokens, via the driver's store."""

    simulated = False

    def __init__(self, driver: AferoDriver, *, pending_ttl_s: float = PENDING_TTL_S):
        self.driver = driver
        self.ttl = pending_ttl_s
        self._pending: dict[str, _Pending] = {}

    @property
    def clock(self) -> Clock:
        return self.driver.clock

    async def start_login(self, account: str, email: str, password: str) -> LoginOutcome:
        if account not in self.driver.accounts():
            return LoginOutcome(LoginState.FAILED, "That isn't one of the registry's accounts.")
        if not (email or "").strip() or not password:
            return LoginOutcome(LoginState.FAILED, "Enter the account's email and password.", account=account)
        try:
            self.driver.gate.check_account(email)  # before anything can reach the network (07 §1)
        except GateDenied as denied:
            return LoginOutcome(LoginState.FAILED, denied.reason, account=account)
        if not self.driver.token_store.available:
            return LoginOutcome(LoginState.FAILED, self.driver.token_store.problem, account=account)
        await self._purge()
        session = self.driver.new_session()
        auth = AferoAuth.for_login(session, email.strip(), password, hide_secrets=True)
        try:
            with purpose_scope(Purpose.ACCOUNT):
                token_data = await asyncio.wait_for(auth.login(), LOGIN_TIMEOUT_S)
        except OTPRequired:
            nonce = secrets.token_urlsafe(24)
            self._pending[nonce] = _Pending(account, auth, session, self.clock.monotonic() + self.ttl)
            minutes = round(self.ttl / 60)
            return LoginOutcome(
                LoginState.OTP_REQUIRED,
                f"Hubspace emailed a sign-in code to the account's inbox. Enter it within {minutes} minutes; "
                "a restart of the Conductor before then loses this sign-in.",
                nonce=nonce,
                account=account,
            )
        except REQUEST_ERRORS as exc:
            await _close_session(session)
            return LoginOutcome(LoginState.FAILED, _login_error(exc), account=account)
        await _close_session(session)
        return await self._finish(account, token_data)

    async def submit_otp(self, nonce: str, code: str) -> LoginOutcome:
        await self._purge()
        pending = self._pending.get(nonce or "")
        if pending is None:
            return LoginOutcome(LoginState.FAILED, EXPIRED_MESSAGE)
        code = (code or "").strip()
        if not code:
            return LoginOutcome(
                LoginState.OTP_REQUIRED,
                "Enter the code from Hubspace's email.",
                nonce=nonce,
                account=pending.account,
            )
        async with pending.lock:
            if self._pending.get(nonce) is not pending:
                return LoginOutcome(LoginState.FAILED, EXPIRED_MESSAGE)
            pending.attempts += 1
            try:
                with purpose_scope(Purpose.ACCOUNT):
                    token_data = await asyncio.wait_for(pending.auth.submit_otp(code), LOGIN_TIMEOUT_S)
            except InvalidOTP:
                if pending.attempts >= MAX_OTP_ATTEMPTS:
                    await self._drop(nonce)
                    return LoginOutcome(
                        LoginState.FAILED,
                        "Too many codes were refused. Start the sign-in again to get a new code.",
                        account=pending.account,
                    )
                return LoginOutcome(
                    LoginState.OTP_REQUIRED,
                    "Hubspace didn't accept that code. Check the newest email from Hubspace and try again.",
                    nonce=nonce,
                    account=pending.account,
                )
            except (TimeoutError, aiohttp.ClientError, OSError) as exc:
                if isinstance(exc, GateDenied):
                    await self._drop(nonce)
                    return LoginOutcome(LoginState.FAILED, exc.reason, account=pending.account)
                return LoginOutcome(
                    LoginState.OTP_REQUIRED,
                    "Couldn't reach Hubspace to check the code. Try the same code again.",
                    nonce=nonce,
                    account=pending.account,
                )
            except REQUEST_ERRORS as exc:
                await self._drop(nonce)
                return LoginOutcome(LoginState.FAILED, _login_error(exc), account=pending.account)
            await self._drop(nonce)
        return await self._finish(pending.account, token_data)

    async def _finish(self, account: str, token_data: TokenData) -> LoginOutcome:
        try:
            self.driver.token_store.save(account, token_data.refresh_token)
        except TokenStoreUnavailable as exc:
            return LoginOutcome(
                LoginState.FAILED, f"Signed in, but the token couldn't be saved: {exc}", account=account
            )
        except OSError as exc:
            return LoginOutcome(
                LoginState.FAILED,
                f"Signed in, but the token couldn't be saved ({type(exc).__name__}). Check the data directory.",
                account=account,
            )
        connected = await self.driver.connect(account, token_data=token_data)
        if connected:
            return LoginOutcome(
                LoginState.CONNECTED,
                "Connected to Hubspace. Only the refresh token is stored, encrypted; the password was not kept.",
                account=account,
            )
        why = self.driver.account_status(account).last_error or "no answer yet"
        return LoginOutcome(
            LoginState.CONNECTED,
            f"Signed in and the token is saved, but the first connection hasn't finished ({why}). It keeps "
            "retrying in the background; watch the account on /ops.",
            account=account,
        )

    def status(self) -> list[AccountStatus]:
        return [self.driver.account_status(a) for a in self.driver.accounts()]

    async def disconnect(self, account: str) -> None:
        for nonce, pending in list(self._pending.items()):
            if pending.account == account:
                await self._drop(nonce)
        await self.driver.disconnect(account)

    async def close(self) -> None:
        for nonce in list(self._pending):
            await self._drop(nonce)

    async def _drop(self, nonce: str) -> None:
        pending = self._pending.pop(nonce, None)
        if pending is not None:
            await _close_session(pending.session)

    async def _purge(self) -> None:
        now = self.clock.monotonic()
        for nonce, pending in list(self._pending.items()):
            if pending.expires_at <= now:
                await self._drop(nonce)


def _login_error(exc: BaseException) -> str:
    """Plain-English sign-in failure. Never echoes the email, password or code."""
    if isinstance(exc, GateDenied):
        return exc.reason
    if isinstance(exc, InvalidAuth):
        return "Hubspace didn't accept that email and password."
    if isinstance(exc, web_exceptions.HTTPForbidden):
        return (
            "Hubspace refused the sign-in (HTTP 403), which usually means it is rate limiting. Wait a few minutes "
            "before trying again."
        )
    if isinstance(exc, TimeoutError):
        return "Hubspace's sign-in didn't answer within 30 seconds. Nothing was saved; try again."
    if isinstance(exc, (aiohttp.ClientError, OSError)):
        return (
            "Couldn't reach Hubspace's sign-in service. Nothing was saved; check the internet and try again."
        )
    if isinstance(exc, AferoError):
        return (
            "Hubspace's sign-in pages answered in a way this version doesn't understand. Nothing was saved; try "
            "again, and if it keeps failing, send the Conductor logs."
        )
    log.error("afero sign-in failed unexpectedly: %s", type(exc).__name__)
    return f"Sign-in failed unexpectedly ({type(exc).__name__}). Nothing was saved."


async def _close_session(session: GatedClientSession) -> None:
    with contextlib.suppress(Exception):
        if not session.closed:
            await session.close()
