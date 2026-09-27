"""SimDriver — the simulated Hubspace world the engine, bench and pages run against by default (05 #4, 07 §7).

Rules being implemented:
- **One simulated device per enabled registry entry**, addressed `sim-<id>` (D1), on the entry's account, serving
  the entry's profile (default `sqhdk03-sim`: the sibling string resized to 30 heads).
- **One write = one PUT.** `write()` stamps `lastUpdateTime` with the driver clock in epoch **milliseconds**, builds
  the exact wire body the Afero adapter would send, and hands it to the cloud model (`SimCloud.accept_wire`). It
  never retries and never raises for HTTP/transport problems — every failure is an `Outcome` (drivers/base.py).
- **The cloud validates like a strict API**: unknown (class, instance) → 400; a category value the device doesn't
  list → 400; a number outside the declared range → 400; a malformed `color-sequence-v2` object → 400; a frame of
  the wrong length → 400 (or stored, per `wrong_length_policy`). A whole PUT is refused if any row is bad.
  A row whose `lastUpdateTime` looks like **seconds** (< 1e11) gets HTTP 200 and is silently dropped (aioafero #65).
- **Faults, in order**: internet outage → CONNECTION (no HTTP status) · expired token → AUTH 401 · 500 window →
  SERVER_ERROR 500 · per-device min interval (optional) → 429 + a counted violation · random throttle → 429 ·
  validation → 400 · random silent drop → 200 but nothing stored. Reads get the same outage/auth/500/throttle
  model, plus optional silent degradation (one random row missing from a 200).
- **Cloud vs device (07 §5).** The cloud stores accepted rows when it acks; the device applies them
  `device_apply_extra_s` later — and only if it's powered (`faults.set_available`). Readback shows the cloud's rows
  or the device's rows per `readback_semantics`, so the reconciler can be tested against both possibilities.
- **Devices that settle** (`DEVICE_SETTLES`): a lock applies `locking`, then reports `locked` to the cloud.
- All timing comes from the injected Clock; all randomness from seeded `random.Random` streams, so a run on
  `VirtualTimeLoop` is reproducible. Device application is evaluated lazily from the clock (no background tasks).

Seams (07 §7, not modelled until the bench says they matter): animation clocks and drift (only a per-apply phase
origin within `start_skew_max_s` is recorded), speed reset on power loss, the 12-puck fault, whether a device pulls
cloud state when it comes back online (here it keeps what it last applied), and faults on discover/fetch_profile.
"""

from __future__ import annotations

import copy
import json
import random
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from conductor.clock import Clock
from conductor.config import Registry
from conductor.drivers.base import (
    TELEMETRY_CLASSES,
    DeviceInfo,
    Outcome,
    ReadResult,
    StateRow,
    WriteResult,
)
from conductor.gate import Purpose
from conductor.rows import EFFECT_FIELDS, FRAME_CLASS, FRAME_FIELDS, HEADER_COPY_FIELDS
from conductor.sim.profiles import DEFAULT_PROFILE, device_info, frame_length, load_profile
from conductor.sim.settings import READBACK_SEMANTICS, SimSettings
from conductor.telemetry import percentile

Key = tuple[str, str | None]

SECONDS_TIMESTAMP_LIMIT = 1e11  # below this, a lastUpdateTime is seconds, not ms (aioafero #65)
# Values a device replaces with its own settled state once it has applied them, and reports back to the cloud:
# aioafero's LockController writes `locking` / `unlocking`; the lock then reports `locked` / `unlocked`
# (CurrentPositionEnum). UNVERIFIED on a real Defiant lock: how long the bolt takes and what readback shows meanwhile.
DEVICE_SETTLES: dict[str, dict[str, str]] = {"lock-control": {"locking": "locked", "unlocking": "unlocked"}}
MAX_ENTRY_CCT = 10000
ANIMATED_MODES = frozenset({"sequence", "individual"})
_ACK_HISTORY = 20000


class SimRejected(ValueError):
    """The simulated cloud refused a row (→ HTTP 400). Message is operator-safe and never echoes a payload."""


@dataclass
class WireResponse:
    """What the simulated cloud answered to one PUT body."""

    status: int
    error: str | None = None
    stored: list[StateRow] = field(default_factory=list)
    dropped: list[StateRow] = field(default_factory=list)  # answered 200 but never stored

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


# ---- one simulated device ---------------------------------------------------------------------------------------
class SimDevice:
    """A strand as the cloud and the controller each see it."""

    def __init__(self, address: str, *, account: str, profile: str, label: str, rssi: int):
        self.address = address
        self.account = account
        self.profile = profile
        self.label = label
        self.dump = load_profile(profile)
        self.functions: list[dict] = self.dump.get("functions") or []
        self.fn_index: dict[Key, dict] = {
            (fn.get("functionClass"), fn.get("functionInstance")): fn for fn in self.functions
        }
        self.frame_length = frame_length(self.dump)
        self.cloud: dict[Key, Any] = {}
        self.cloud_times: dict[Key, int] = {}
        for s in self.dump.get("states") or []:
            key = (s["functionClass"], s.get("functionInstance"))
            if key[0] not in TELEMETRY_CLASSES:
                self.cloud[key] = copy.deepcopy(s.get("value"))
                self.cloud_times[key] = int(s.get("lastUpdateTime") or 0)
        self.applied: dict[Key, Any] = copy.deepcopy(self.cloud)
        self.available = True
        self.rssi = rssi
        self.pending: deque[tuple[float, list[tuple[Key, Any]]]] = deque()
        self.last_write_mono: float | None = None
        self.applied_at: float | None = None  # wall clock of the last apply
        self.animation_origin: float | None = None  # wall clock the on-device animation phase started (seam)


def _rows(values: Mapping[Key, Any]) -> list[StateRow]:
    return [StateRow(k[0], k[1], copy.deepcopy(v)) for k, v in values.items()]


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _label(key: Key) -> str:
    return f"{key[0]}/{key[1]}" if key[1] else key[0]


def _range_of(fn: Mapping[str, Any]) -> tuple[int, int] | None:
    for v in fn.get("values") or []:
        r = v.get("range") if isinstance(v, Mapping) else None
        if isinstance(r, Mapping) and "min" in r and "max" in r:
            return int(r["min"]), int(r["max"])
    return None


def _validate_frame(value: Any, expected_length: int | None, wrong_length_policy: str) -> None:
    inner = value.get(FRAME_CLASS) if isinstance(value, Mapping) else None
    if not isinstance(inner, Mapping):
        raise SimRejected('a slot object must be {"color-sequence-v2": {…}}')
    for name in (*HEADER_COPY_FIELDS, *EFFECT_FIELDS):
        if not _is_int(inner.get(name)):
            raise SimRejected(f"the slot header field {name} is missing or not a whole number")
    fb = inner.get("frameBuffer")
    if (
        not isinstance(fb, Mapping)
        or not _is_int(fb.get("flags"))
        or not isinstance(fb.get("framebuffer"), list)
    ):
        raise SimRejected("the slot object has no well-formed frameBuffer {flags, framebuffer[]}")
    entries = fb["framebuffer"]
    for i, e in enumerate(entries):
        if not isinstance(e, Mapping) or not all(_is_int(e.get(f)) for f in FRAME_FIELDS):
            raise SimRejected(f"frame entry {i} needs whole-number {', '.join(FRAME_FIELDS)}")
        if not all(0 <= e[c] <= 255 for c in ("r", "g", "b")):
            raise SimRejected(f"frame entry {i} has a colour outside 0–255")
        if not all(0 <= e[c] <= 100 for c in ("colorBrightness", "whiteBrightness")):
            raise SimRejected(f"frame entry {i} has a brightness outside 0–100")
        if not 0 <= e["cct"] <= MAX_ENTRY_CCT:
            raise SimRejected(f"frame entry {i} has a colour temperature outside 0–{MAX_ENTRY_CCT} K")
    if expected_length is not None and len(entries) != expected_length and wrong_length_policy == "reject":
        raise SimRejected(
            f"the frame has {len(entries)} entries but this device's framebuffer has {expected_length}"
        )


def _validate_value(fn: Mapping[str, Any], key: Key, value: Any, device: SimDevice, policy: str) -> Any:
    """The value as the cloud would store it, or SimRejected."""
    kind = fn.get("type")
    names = [str(v.get("name")) for v in fn.get("values") or [] if isinstance(v, Mapping)]
    if kind == "category":
        if not isinstance(value, str) or value not in names:
            shown = value if isinstance(value, str) and len(value) <= 40 else "that value"
            raise SimRejected(f"{_label(key)}: {shown!r} isn't one of this device's values")
        return value
    if kind == "numeric":
        number: int | None = None
        if _is_int(value):
            number = value
        elif isinstance(value, float) and value.is_integer():
            number = int(value)
        elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
            number = int(value.strip())
        if number is None:
            raise SimRejected(f"{_label(key)} needs a whole number")
        rng = _range_of(fn)
        if rng and not rng[0] <= number <= rng[1]:
            raise SimRejected(f"{_label(key)}={number} is outside this device's range {rng[0]}–{rng[1]}")
        return number  # the cloud reads numbers back as ints (the fixture does; D7)
    if kind == "object":
        if key[0] == FRAME_CLASS:
            _validate_frame(value, device.frame_length, policy)
        elif key[0] == "color-rgb":
            inner = value.get("color-rgb") if isinstance(value, Mapping) else None
            if not isinstance(inner, Mapping) or not all(
                _is_int(inner.get(c)) and 0 <= inner[c] <= 255 for c in ("r", "g", "b")
            ):
                raise SimRejected('color-rgb needs {"color-rgb": {"r", "g", "b"}} with whole numbers 0–255')
        elif not isinstance(value, Mapping):
            raise SimRejected(f"{_label(key)} needs an object value")
        return copy.deepcopy(value)
    if kind == "boolean":
        if not isinstance(value, bool):
            raise SimRejected(f"{_label(key)} needs true or false")
        return value
    return copy.deepcopy(value)


# ---- the cloud ---------------------------------------------------------------------------------------------------
class SimCloud:
    """The simulated Afero semantic API: validates and stores PUT bodies, and delivers them to devices."""

    def __init__(self, *, clock: Clock, settings: SimSettings, rng: random.Random):
        self.clock = clock
        self.settings = settings
        self.rng = rng
        self.devices: dict[str, SimDevice] = {}
        self.seconds_rows_dropped = 0

    def device(self, address: str) -> SimDevice:
        try:
            return self.devices[address]
        except KeyError:
            raise LookupError(f"the simulator has no device {address!r}") from None

    def accept_wire(self, body: Any, *, silent_drop: bool = False) -> WireResponse:
        """Handle one PUT body `{"metadeviceId", "values": [...]}` exactly as the cloud would (no transport
        faults — those live in SimDriver). `silent_drop` answers 200 and stores nothing."""
        if not isinstance(body, Mapping) or not isinstance(body.get("values"), list):
            return WireResponse(400, "the request body isn't a state update (no values list)")
        device = self.devices.get(str(body.get("metadeviceId")))
        if device is None:
            return WireResponse(404, "the cloud doesn't know this device (HTTP 404)")
        accepted: list[tuple[Key, Any, int]] = []
        dropped: list[StateRow] = []
        for i, row in enumerate(body["values"]):
            if not isinstance(row, Mapping) or not isinstance(row.get("functionClass"), str):
                return WireResponse(400, f"the cloud refused the update (HTTP 400): row {i + 1} is malformed")
            key: Key = (row["functionClass"], row.get("functionInstance"))
            fn = device.fn_index.get(key)
            if fn is None:
                return WireResponse(
                    400, f"the cloud refused the update (HTTP 400): this device has no {_label(key)} function"
                )
            stamp = row.get("lastUpdateTime")
            if not _is_int(stamp):
                return WireResponse(
                    400, f"the cloud refused the update (HTTP 400): {_label(key)} has no lastUpdateTime"
                )
            try:
                value = _validate_value(fn, key, row.get("value"), device, self.settings.wrong_length_policy)
            except SimRejected as e:
                return WireResponse(400, f"the cloud refused the update (HTTP 400): {e}")
            if stamp < SECONDS_TIMESTAMP_LIMIT:
                dropped.append(StateRow(key[0], key[1], copy.deepcopy(row.get("value"))))
                continue
            accepted.append((key, value, stamp))
        if silent_drop:
            dropped += [StateRow(k[0], k[1], v) for k, v, _ in accepted]
            return WireResponse(200, None, [], dropped)
        self.seconds_rows_dropped += len(dropped)
        for key, value, stamp in accepted:
            device.cloud[key] = value
            device.cloud_times[key] = stamp
        if accepted:
            self._deliver(device, [(k, v) for k, v, _ in accepted])
        return WireResponse(
            200, None, [StateRow(k[0], k[1], copy.deepcopy(v)) for k, v, _ in accepted], dropped
        )

    # -- device side
    def _deliver(self, device: SimDevice, rows: list[tuple[Key, Any]]) -> None:
        self.settle(device)
        if not device.available:
            return  # a powered-off controller never receives it
        at = self.clock.monotonic() + self.settings.sample_apply_extra_s(self.rng)
        if device.pending:
            at = max(
                at, device.pending[-1][0]
            )  # the controller applies updates in the order the cloud sent them
        device.pending.append((at, copy.deepcopy(rows)))

    def settle(self, device: SimDevice) -> None:
        """Apply every delivered update that is due by now."""
        now = self.clock.monotonic()
        while device.pending and device.pending[0][0] <= now:
            at, rows = device.pending.popleft()
            if not device.available:
                continue
            for key, value in rows:
                device.applied[key] = value
            device.applied_at = self.clock.now() - (now - at)
            for key, value in rows:
                settled = DEVICE_SETTLES.get(key[0], {}).get(value) if isinstance(value, str) else None
                if settled is None:
                    continue
                device.applied[key] = settled
                if (
                    device.cloud.get(key) == value
                ):  # the device reports back, unless a newer write is in the cloud
                    device.cloud[key] = settled
                    device.cloud_times[key] = round(device.applied_at * 1000)
            if device.applied.get(("color-mode", None)) in ANIMATED_MODES:
                device.animation_origin = device.applied_at + self.settings.sample_start_skew_s(self.rng)

    def set_available(self, address: str, available: bool) -> None:
        device = self.device(address)
        self.settle(device)
        if not available:
            device.pending.clear()  # in-flight updates die with the power
        device.available = available


# ---- fault controls ----------------------------------------------------------------------------------------------
class SimFaults:
    """Live fault controls for tests, the soak and /ops demos. Starts from the driver's SimSettings."""

    def __init__(self, driver: SimDriver):
        self._driver = driver
        s = driver.settings
        self.outage = False
        self.throttle_rate = s.throttle_429_rate
        self.silent_drop_rate = s.silent_drop_rate
        self.read_row_drop_rate = s.read_row_drop_rate
        self.readback_semantics: str = s.readback_semantics
        self.expired_accounts: set[str] = set()
        self._500_windows: list[tuple[float, float]] = []  # (start, end) on the clock's monotonic scale

    @staticmethod
    def _rate(name: str, rate: float) -> float:
        if not 0 <= rate <= 1:
            raise ValueError(f"{name} must be between 0 and 1")
        return float(rate)

    def set_outage(self, on: bool) -> None:
        self.outage = bool(on)

    def start_500_window(self, duration_s: float) -> None:
        if duration_s <= 0:
            raise ValueError("a 500 window needs a positive duration")
        now = self._driver.clock.monotonic()
        self._500_windows.append((now, now + duration_s))

    def end_500_windows(self) -> None:
        self._500_windows.clear()

    def set_throttle_rate(self, rate: float) -> None:
        self.throttle_rate = self._rate("throttle rate", rate)

    def set_silent_drop_rate(self, rate: float) -> None:
        self.silent_drop_rate = self._rate("silent drop rate", rate)

    def set_read_row_drop_rate(self, rate: float) -> None:
        self.read_row_drop_rate = self._rate("read row drop rate", rate)

    def set_available(self, address: str, available: bool) -> None:
        """Cut (False) or restore (True) a controller's power. Unpowered: available=false, nothing applied."""
        self._driver.cloud.set_available(address, available)

    def expire_token(self, account: str) -> None:
        self.expired_accounts.add(account)

    def restore_token(self, account: str) -> None:
        self.expired_accounts.discard(account)

    def set_readback_semantics(self, semantics: str) -> None:
        if semantics not in READBACK_SEMANTICS:
            raise ValueError(f"readback semantics must be one of {', '.join(READBACK_SEMANTICS)}")
        self.readback_semantics = semantics

    def in_500_window(self) -> bool:
        now = self._driver.clock.monotonic()
        if any(start <= now < end for start, end in self._500_windows):
            return True
        return self._driver.settings.in_scheduled_500_window(now - self._driver.origin)

    def snapshot(self) -> dict:
        return {
            "outage": self.outage,
            "http_500_window": self.in_500_window(),
            "throttle_rate": self.throttle_rate,
            "silent_drop_rate": self.silent_drop_rate,
            "read_row_drop_rate": self.read_row_drop_rate,
            "readback_semantics": self.readback_semantics,
            "expired_accounts": sorted(self.expired_accounts),
            "unavailable_devices": sorted(
                a for a, d in self._driver.cloud.devices.items() if not d.available
            ),
        }


# ---- the driver --------------------------------------------------------------------------------------------------
@dataclass
class _Exchange:
    outcome: Outcome
    status: int | None
    error: str | None


class SimDriver:
    """DeviceDriver over the simulated cloud. See the module docstring for the rules."""

    name = "sim"
    simulated = True

    def __init__(
        self,
        registry: Registry,
        *,
        clock: Clock,
        settings: SimSettings | None = None,
        seed: int = 0,
    ):
        self.clock = clock
        self.settings = settings or SimSettings.dev()
        self.seed = seed
        self._fault_rng = random.Random(f"{seed}:faults")
        self._latency_rng = random.Random(f"{seed}:latency")
        self._world_rng = random.Random(f"{seed}:world")
        self.cloud = SimCloud(clock=clock, settings=self.settings, rng=self._world_rng)
        self.origin = clock.monotonic()
        self.faults = SimFaults(self)
        self._counts: Counter[str] = Counter()
        self._violations: Counter[str] = Counter()
        self._writes_per_device: Counter[str] = Counter()
        self._ack_s: deque[float] = deque(maxlen=_ACK_HISTORY)
        self._last_error: str | None = None
        for entry in registry.devices:
            self.add_device(
                entry.sim_address,
                account=entry.account,
                profile=entry.profile or DEFAULT_PROFILE,
                label=entry.label or entry.id,
            )

    # -- world
    def add_device(
        self, address: str, *, account: str, profile: str = DEFAULT_PROFILE, label: str = ""
    ) -> None:
        """Add one simulated device (spares, tests). Replaces any device already at `address`."""
        rssi = -57 + self._world_rng.randint(-8, 3)
        self.cloud.devices[address] = SimDevice(
            address, account=account, profile=profile, label=label or address, rssi=rssi
        )

    def addresses(self) -> list[str]:
        return list(self.cloud.devices)

    def account_of(self, device_id: str) -> str:
        return self.cloud.device(device_id).account

    # -- transport-level fault model
    async def _exchange(self, device_id: str, *, write: bool) -> _Exchange | None:
        """Sleep the request's latency and apply the transport faults. None = the request reached the API."""
        kind = "write" if write else "read"
        if self.faults.outage:
            await self.clock.sleep(self.settings.outage_fail_s)
            return _Exchange(
                Outcome.CONNECTION, None, "no connection to the Hubspace cloud (simulated outage)"
            )
        send_mono = self.clock.monotonic()
        latency = self.settings.sample_ack_s(self._latency_rng)
        # Draw every fault roll up front so one fault never shifts another's random stream.
        throttle_roll = self._fault_rng.random()
        await self.clock.sleep(latency)
        if write:
            self._ack_s.append(latency)
        device = self.cloud.devices.get(device_id)
        # Every write that reaches the API counts against the device's budget, whatever it's answered.
        # Spacing is measured at send time: that is what the executor controls.
        too_soon = False
        gap = self.settings.per_device_min_interval_s
        if write and device is not None:
            previous, device.last_write_mono = device.last_write_mono, send_mono
            too_soon = gap is not None and previous is not None and send_mono - previous < gap - 1e-9
        if device is not None and device.account in self.faults.expired_accounts:
            return _Exchange(
                Outcome.AUTH, 401, "the account's sign-in expired; re-sign-in required (simulated)"
            )
        if self.faults.in_500_window():
            return _Exchange(Outcome.SERVER_ERROR, 500, "the Hubspace cloud returned HTTP 500 (simulated)")
        if too_soon:
            self._violations[device_id] += 1
            return _Exchange(
                Outcome.THROTTLED,
                429,
                f"HTTP 429: a second write to this device within {gap:g} s (simulated rate budget)",
            )
        if throttle_roll < self.faults.throttle_rate:
            return _Exchange(
                Outcome.THROTTLED, 429, f"the Hubspace cloud is throttling {kind}s (HTTP 429, simulated)"
            )
        if device is None:
            return _Exchange(Outcome.NOT_FOUND, 404, "the cloud doesn't know this device (HTTP 404)")
        return None

    def _note(self, prefix: str, outcome: Outcome, status: int | None, error: str | None) -> None:
        self._counts[f"{prefix}_{outcome.value}"] += 1
        if status is not None:
            self._counts[f"{prefix}_http_{status}"] += 1
        if error:
            self._last_error = error

    # -- DeviceDriver
    async def write(self, device_id: str, rows: Sequence[StateRow], *, purpose: Purpose) -> WriteResult:
        sent_at = self.clock.now()
        t0 = self.clock.monotonic()
        stamp = round(sent_at * 1000)
        self._counts["writes"] += 1
        self._writes_per_device[device_id] += 1
        result = WriteResult(device_id, Outcome.OK, sent_at=sent_at, simulated=True)
        try:
            body = {"metadeviceId": device_id, "values": [r.to_wire(stamp) for r in rows]}
            result.bytes_sent = len(json.dumps(body, separators=(",", ":")).encode())
        except (TypeError, ValueError):
            result.outcome, result.error = (
                Outcome.CLIENT_ERROR,
                "the rows can't be encoded as JSON; nothing sent",
            )
            self._note("write", result.outcome, None, result.error)
            return result
        drop_roll = self._fault_rng.random()
        failure = await self._exchange(device_id, write=True)
        if failure is not None:
            result.outcome, result.http_status, result.error = failure.outcome, failure.status, failure.error
        else:
            response = self.cloud.accept_wire(body, silent_drop=drop_roll < self.faults.silent_drop_rate)
            result.http_status = response.status
            if response.ok:
                if drop_roll < self.faults.silent_drop_rate:
                    self._counts["silent_drops"] += 1
            else:
                result.outcome = Outcome.NOT_FOUND if response.status == 404 else Outcome.CLIENT_ERROR
                result.error = response.error
        if result.http_status is not None:
            result.acked_at = self.clock.now()
        result.elapsed_s = self.clock.monotonic() - t0
        self._note("write", result.outcome, result.http_status, result.error)
        return result

    async def read_state(self, device_id: str, *, purpose: Purpose = Purpose.ENGINE) -> ReadResult:
        fetched_at = self.clock.now()
        t0 = self.clock.monotonic()
        self._counts["reads"] += 1
        drop_roll = self._fault_rng.random()
        failure = await self._exchange(device_id, write=False)
        result = ReadResult(device_id, Outcome.OK, fetched_at=fetched_at, simulated=True)
        if failure is not None:
            result.outcome, result.http_status, result.error = failure.outcome, failure.status, failure.error
        else:
            result.http_status = 200
            result.rows = self._readback_rows(self.cloud.devices[device_id])
            if drop_roll < self.faults.read_row_drop_rate and result.rows:
                result.rows.pop(self._fault_rng.randrange(len(result.rows)))
                self._counts["read_rows_dropped"] += 1
        result.elapsed_s = self.clock.monotonic() - t0
        self._note("read", result.outcome, result.http_status, result.error)
        return result

    async def fetch_profile(self, device_id: str, *, purpose: Purpose = Purpose.ENGINE) -> DeviceInfo:
        device = self.cloud.device(device_id)
        await self.clock.sleep(self.settings.sample_ack_s(self._latency_rng))
        return self._info(device)

    async def discover(self, account: str | None = None) -> list[DeviceInfo]:
        await self.clock.sleep(self.settings.sample_ack_s(self._latency_rng))
        return [self._info(d) for d in self.cloud.devices.values() if account is None or d.account == account]

    def health(self) -> dict:
        devices = list(self.cloud.devices.values())
        accounts: dict[str, dict] = {}
        for d in devices:
            a = accounts.setdefault(d.account, {"devices": 0, "token": "ok"})
            a["devices"] += 1
            if d.account in self.faults.expired_accounts:
                a["token"] = "expired (simulated): re-sign-in required"
        return {
            "driver": self.name,
            "simulated": True,
            "settings": self.settings.label,
            "devices": len(devices),
            "available": sum(1 for d in devices if d.available),
            "accounts": accounts,
            "faults": self.faults.snapshot(),
            "last_error": self._last_error,
            "writes": self._counts["writes"],
            "reads": self._counts["reads"],
        }

    async def close(self) -> None:
        return None

    # -- ground truth and statistics
    def device_view(self, address: str) -> list[StateRow]:
        """What the lights actually show (ground truth for tests and the visualizer). Unpowered = off."""
        device = self.cloud.device(address)
        self.cloud.settle(device)
        view = dict(device.applied)
        if not device.available:
            view[("power", None)] = "off"
        return _rows(view)

    def cloud_view(self, address: str) -> list[StateRow]:
        """What the cloud has stored for a device (no faults, no latency)."""
        return _rows(self.cloud.device(address).cloud)

    def device_debug(self, address: str) -> dict:
        device = self.cloud.device(address)
        self.cloud.settle(device)
        return {
            "address": address,
            "account": device.account,
            "profile": device.profile,
            "frame_length": device.frame_length,
            "available": device.available,
            "pending_updates": len(device.pending),
            "applied_at": device.applied_at,
            "animation_origin": device.animation_origin,
        }

    def stats(self) -> dict:
        c = self._counts
        acks = list(self._ack_s)
        return {
            "writes": c["writes"],
            "reads": c["reads"],
            "writes_ok": c["write_ok"],
            "http_429": c["write_http_429"] + c["read_http_429"],
            "write_429": c["write_http_429"],
            "read_429": c["read_http_429"],
            "http_500": c["write_http_500"] + c["read_http_500"],
            "http_400": c["write_http_400"],
            "http_404": c["write_http_404"] + c["read_http_404"],
            "auth_failures": c["write_auth"] + c["read_auth"],
            "connection_failures": c["write_connection"] + c["read_connection"],
            "silent_drops": c["silent_drops"],
            "seconds_rows_dropped": self.cloud.seconds_rows_dropped,
            "read_rows_dropped": c["read_rows_dropped"],
            "min_interval_violations": dict(self._violations),
            "min_interval_violations_total": sum(self._violations.values()),
            "writes_per_device": dict(self._writes_per_device),
            "ack_s": {
                "n": len(acks),
                "min": min(acks) if acks else None,
                "p50": percentile(acks, 50),
                "p95": percentile(acks, 95),
                "max": max(acks) if acks else None,
            },
        }

    # -- helpers
    def _telemetry(self, device: SimDevice) -> list[StateRow]:
        return [
            StateRow("available", None, device.available),
            StateRow("wifi-rssi", None, device.rssi),
            StateRow("wifi-steady-state", None, "connected" if device.available else "not-connected"),
        ]

    def _readback_rows(self, device: SimDevice) -> list[StateRow]:
        self.cloud.settle(device)
        source = device.cloud if self.faults.readback_semantics == "cloud" else device.applied
        return _rows(source) + self._telemetry(device)

    def _info(self, device: SimDevice) -> DeviceInfo:
        self.cloud.settle(device)
        dump = copy.deepcopy(device.dump)
        dump["states"] = [r.to_wire(device.cloud_times.get(r.key, 0)) for r in _rows(device.cloud)] + [
            r.to_wire(int(self.clock.now() * 1000)) for r in self._telemetry(device)
        ]
        return device_info(
            device.profile, device_id=device.address, account=device.account, label=device.label, dump=dump
        )
