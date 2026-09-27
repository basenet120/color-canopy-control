"""The `DeviceDriver` interface and the wire-level types every driver speaks (05, 07 §4–§6).

A driver sends exactly what it's given, once. It never retries (the executor owns retries), never raises for
HTTP or transport problems (it classifies them into an `Outcome`), and never splits one module's update across
requests: one `write()` is one PUT.

Implementations:
- `SimDriver` (`conductor/drivers/sim.py`) — in-memory devices with the pinned latency and fault model.
- `AferoDriver` (`conductor/drivers/afero.py`) — the only aioafero importer; every request passes the live gate.
- `SupplementalDriver` (`conductor/drivers/supplemental.py`) — interface stub only (v1 seam).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from conductor.gate import Purpose

# Rows that describe the device, not the look. Never part of a state vector, never compared (07 §5).
TELEMETRY_CLASSES = frozenset(
    {
        "available",
        "visible",
        "direct",
        "wifi-ssid",
        "wifi-rssi",
        "wifi-steady-state",
        "wifi-setup-state",
        "wifi-mac-address",
        "ble-mac-address",
        "geo-coordinates",
        "scheduler-flags",
    }
)


@dataclass(frozen=True)
class StateRow:
    """One (functionClass, functionInstance) = value row. `lastUpdateTime` is stamped by the driver on send."""

    function_class: str
    function_instance: str | None
    value: Any

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.function_class, self.function_instance)

    def to_wire(self, last_update_ms: int) -> dict:
        return {
            "functionClass": self.function_class,
            "functionInstance": self.function_instance,
            "value": self.value,
            "lastUpdateTime": last_update_ms,
        }

    @classmethod
    def from_wire(cls, row: Mapping[str, Any]) -> StateRow:
        return cls(row["functionClass"], row.get("functionInstance"), row.get("value"))

    def to_json(self) -> dict:
        return {
            "functionClass": self.function_class,
            "functionInstance": self.function_instance,
            "value": self.value,
        }


class Outcome(StrEnum):
    OK = "ok"  # 2xx with a well-formed body
    THROTTLED = "throttled"  # 429, 403, or exhausted 429/503/504 retries; http_status says which
    SERVER_ERROR = "server_error"  # 5xx (other than exhausted 503/504 retries)
    CLIENT_ERROR = "client_error"  # 400 and other 4xx not listed here — the payload was refused
    NOT_FOUND = "not_found"  # 404: unknown device id
    AUTH = "auth"  # a dead token (InvalidAuth), or a 401 that survives a token refresh: re-sign-in required
    AUTH_STALE = "auth_stale"  # a 401 on a healthy account: the bearer went stale; it is refreshed, so retry
    TIMEOUT = "timeout"
    CONNECTION = "connection"  # transport failure: DNS, reset, internet down
    MALFORMED = "malformed"  # 2xx but the body isn't the expected shape (silent degradation)
    DENIED = "denied"  # the live gate refused it; nothing left the process

    @property
    def retryable(self) -> bool:
        return self in RETRYABLE


RETRYABLE = frozenset(
    {
        Outcome.THROTTLED,
        Outcome.SERVER_ERROR,
        Outcome.TIMEOUT,
        Outcome.CONNECTION,
        Outcome.MALFORMED,
        Outcome.AUTH_STALE,
    }
)


@dataclass
class WriteResult:
    device_id: str
    outcome: Outcome
    http_status: int | None = None
    sent_at: float = 0.0  # wall clock, epoch s
    acked_at: float | None = None  # wall clock when the HTTP response arrived
    elapsed_s: float = 0.0
    bytes_sent: int = 0
    error: str | None = None  # plain English, safe for /ops (never contains secrets or payloads)
    simulated: bool = False
    echoed: list[StateRow] = field(default_factory=list)  # rows the cloud echoed in the PUT response, if any

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK


@dataclass
class ReadResult:
    device_id: str
    outcome: Outcome
    rows: list[StateRow] = field(default_factory=list)
    http_status: int | None = None
    fetched_at: float = 0.0
    elapsed_s: float = 0.0
    error: str | None = None
    simulated: bool = False

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK

    def get(self, function_class: str, function_instance: str | None = None) -> Any:
        for row in self.rows:
            if row.function_class == function_class and row.function_instance == function_instance:
                return row.value
        return None

    @property
    def available(self) -> bool | None:
        value = self.get("available")
        return value if isinstance(value, bool) else None


@dataclass
class DeviceInfo:
    """A device as discovery or a profile fetch sees it.

    `functions` is the device's function list in Afero's raw shape (`functionClass`, `functionInstance`, `type`,
    `values[{name, hints?, range?}]`) — capability detection (`conductor/capabilities.py`) reads it.
    `raw` is the unmodified discovery object (for Gate A dumps; anonymize before it leaves the process).
    """

    device_id: str  # the metadevice id — what PUT/GET URLs and the allowlist use
    account: str  # logical account name from the registry (e.g. "canopy", "bench", "sim")
    name: str = ""
    model: str = ""
    device_class: str = ""
    functions: list[dict] = field(default_factory=list)
    states: list[StateRow] = field(default_factory=list)
    raw: dict | None = None
    simulated: bool = False


@runtime_checkable
class DeviceDriver(Protocol):
    name: str  # "sim" | "afero" | "supplemental"
    simulated: bool  # True → every surface labels results SIMULATED

    async def discover(self, account: str | None = None) -> list[DeviceInfo]:
        """Every device the driver can see (optionally on one account). Read-only."""

    async def fetch_profile(self, device_id: str, *, purpose: Purpose = Purpose.ENGINE) -> DeviceInfo:
        """Functions + current state for one device. Read-only. Raises LookupError for an unknown device."""

    async def read_state(self, device_id: str, *, purpose: Purpose = Purpose.ENGINE) -> ReadResult:
        """REST readback of the device's state rows. Never raises for HTTP/transport problems."""

    async def write(
        self, device_id: str, rows: Sequence[StateRow], *, purpose: Purpose, stamp_offset_s: float = 0.0
    ) -> WriteResult:
        """Exactly one PUT of `rows` (lastUpdateTime = the driver clock's epoch ms, plus `stamp_offset_s` — only the
        stale-clock bench job sets it, through the executor). No retries. Never raises for HTTP/transport problems;
        a gate refusal comes back as Outcome.DENIED with nothing sent."""

    def account_of(self, device_id: str) -> str:
        """The logical account a device belongs to (the executor bounds concurrency per account)."""

    def health(self) -> dict:
        """JSON-safe status for /ops: accounts, token state, last error. No secrets."""

    async def close(self) -> None: ...


def rows_by_key(rows: Iterable[StateRow]) -> dict[tuple[str, str | None], Any]:
    return {r.key: r.value for r in rows}
