"""A scriptable in-memory `DeviceDriver` for executor and tracker tests (no SimDriver, no network).

- Writes and reads take `latency_s` of clock time (via `clock.sleep`) unless a scripted step says otherwise.
- `script_write(device, *steps)` / `script_read(device, *steps)` queue per-call outcomes; unscripted calls are OK.
- Every write is recorded with its send time; a successful write merges its rows into the device's state (unless
  the step says `apply=False`, i.e. a silent drop), and reads return that state plus an `available` row.
- `active` / `max_active` count concurrent requests per account, for the concurrency tests.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from conductor.clock import Clock
from conductor.drivers.base import DeviceInfo, Outcome, ReadResult, StateRow, WriteResult
from conductor.gate import Purpose

HTTP_FOR = {
    Outcome.OK: 200,
    Outcome.THROTTLED: 429,
    Outcome.SERVER_ERROR: 500,
    Outcome.CLIENT_ERROR: 400,
    Outcome.NOT_FOUND: 404,
    Outcome.AUTH: 401,
    Outcome.MALFORMED: 200,
}


@dataclass
class Step:
    outcome: Outcome = Outcome.OK
    latency_s: float | None = None
    apply: bool = True  # write: False = 200 but the cloud silently drops it
    rows: list[StateRow] | None = None  # read: return exactly these rows instead of the device state
    drop: frozenset[tuple[str, str | None]] = frozenset()  # read: omit these keys from the device state
    error: str | None = None


@dataclass
class WriteRecord:
    device_id: str
    rows: list[StateRow]
    purpose: Purpose
    sent_mono: float
    acked_mono: float = 0.0
    outcome: Outcome = Outcome.OK


@dataclass
class ReadRecord:
    device_id: str
    sent_mono: float


@dataclass
class FakeDriver:
    clock: Clock
    latency_s: float = 0.2
    read_latency_s: float = 0.1
    accounts: dict[str, str] = field(default_factory=dict)  # device → account; default "acct"
    name: str = "fake"
    simulated: bool = True

    def __post_init__(self) -> None:
        self.writes: list[WriteRecord] = []
        self.reads: list[ReadRecord] = []
        self.state: dict[str, dict[tuple[str, str | None], StateRow]] = defaultdict(dict)
        self.available: dict[str, bool] = {}
        self.active: dict[str, int] = defaultdict(int)
        self.max_active: dict[str, int] = defaultdict(int)
        self._write_script: dict[str, deque[Step]] = defaultdict(deque)
        self._read_script: dict[str, deque[Step]] = defaultdict(deque)
        self.closed = False

    # ---- scripting ---------------------------------------------------------------------------------------------
    def script_write(self, device_id: str, *steps: Step | Outcome) -> None:
        self._write_script[device_id].extend(s if isinstance(s, Step) else Step(s) for s in steps)

    def script_read(self, device_id: str, *steps: Step | Outcome) -> None:
        self._read_script[device_id].extend(s if isinstance(s, Step) else Step(s) for s in steps)

    def writes_to(self, device_id: str) -> list[WriteRecord]:
        return [w for w in self.writes if w.device_id == device_id]

    # ---- DeviceDriver ------------------------------------------------------------------------------------------
    def account_of(self, device_id: str) -> str:
        return self.accounts.get(device_id, "acct")

    def _enter(self, device_id: str) -> str:
        account = self.account_of(device_id)
        self.active[account] += 1
        self.max_active[account] = max(self.max_active[account], self.active[account])
        return account

    async def write(self, device_id: str, rows: Sequence[StateRow], *, purpose: Purpose) -> WriteResult:
        step = self._write_script[device_id].popleft() if self._write_script[device_id] else Step()
        record = WriteRecord(device_id, list(rows), purpose, self.clock.monotonic())
        self.writes.append(record)
        sent = self.clock.now()
        if step.outcome is Outcome.DENIED:
            record.outcome = Outcome.DENIED
            return WriteResult(
                device_id, Outcome.DENIED, sent_at=sent, error=step.error or "not allowlisted", simulated=True
            )
        account = self._enter(device_id)
        try:
            await self.clock.sleep(self.latency_s if step.latency_s is None else step.latency_s)
        finally:
            self.active[account] -= 1
        record.acked_mono = self.clock.monotonic()
        record.outcome = step.outcome
        if step.outcome is Outcome.OK and step.apply:
            for r in rows:
                self.state[device_id][r.key] = r
        return WriteResult(
            device_id,
            step.outcome,
            http_status=HTTP_FOR.get(step.outcome),
            sent_at=sent,
            acked_at=self.clock.now(),
            elapsed_s=self.clock.now() - sent,
            bytes_sent=100 * len(rows),
            error=step.error,
            simulated=True,
        )

    async def read_state(self, device_id: str, *, purpose: Purpose = Purpose.ENGINE) -> ReadResult:
        step = self._read_script[device_id].popleft() if self._read_script[device_id] else Step()
        self.reads.append(ReadRecord(device_id, self.clock.monotonic()))
        account = self._enter(device_id)
        try:
            await self.clock.sleep(self.read_latency_s if step.latency_s is None else step.latency_s)
        finally:
            self.active[account] -= 1
        if step.outcome is not Outcome.OK:
            return ReadResult(
                device_id,
                step.outcome,
                http_status=HTTP_FOR.get(step.outcome),
                fetched_at=self.clock.now(),
                error=step.error,
                simulated=True,
            )
        if step.rows is not None:
            rows = list(step.rows)
        else:
            rows = [r for k, r in self.state[device_id].items() if k not in step.drop]
        rows.append(StateRow("available", None, self.available.get(device_id, True)))
        return ReadResult(
            device_id, Outcome.OK, rows=rows, http_status=200, fetched_at=self.clock.now(), simulated=True
        )

    async def discover(self, account: str | None = None) -> list[DeviceInfo]:
        return [
            DeviceInfo(d, self.account_of(d), simulated=True)
            for d in sorted(self.state)
            if account is None or self.account_of(d) == account
        ]

    async def fetch_profile(self, device_id: str, *, purpose: Purpose = Purpose.ENGINE) -> DeviceInfo:
        return DeviceInfo(
            device_id, self.account_of(device_id), states=list(self.state[device_id].values()), simulated=True
        )

    def health(self) -> dict:
        return {"driver": self.name, "simulated": True}

    async def close(self) -> None:
        self.closed = True
