"""Desired vs reported state per device, and the honest status every surface shows (05 #7, 07 §5).

Reported state comes only from REST readback — the executor's verification GETs (carried on each
`CommandResult`) and the reconciler's true-ups (`note_readback`). Status rules, first match wins:

1. **UNKNOWN** — no desired state has been set for the device.
2. **UPDATING** — a command for it is pending, in flight or verifying (counted from executor submissions).
3. **NEEDS_ATTENTION** — a flagged problem (e.g. head count), an AUTH outcome (re-sign-in required), the latest
   readback says `available` is False, the latest command ended FAILED / DENIED / MISMATCH (until a later readback
   matches), or a fresh readback differs from desired.
4. **READY** — the latest successful readback, taken after the desired state was set, matches desired
   (`compare(desired, reported)` is empty).
5. otherwise **UNKNOWN** — waiting for a readback (e.g. a command sent with `verify=False`).

Never faked: without a readback that matches, nothing is READY.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from conductor.clock import Clock
from conductor.drivers.base import Outcome, ReadResult, StateRow
from conductor.executor_api import Command, CommandResult, CommandStatus, DeviceStatus
from conductor.rows import Mismatch, compare
from conductor.telemetry import Telemetry

if TYPE_CHECKING:
    from conductor.executor import CommandExecutor

log = logging.getLogger(__name__)

SnapshotListener = Callable[[str, dict], None]

AUTH_REASON = "The Hubspace sign-in has expired: re-sign-in required on /ops/accounts."
OFFLINE_REASON = "Offline: Hubspace reports the device unavailable."
_PROBLEM_STATUSES = (CommandStatus.FAILED, CommandStatus.DENIED, CommandStatus.MISMATCH)


@dataclass
class _Device:
    address: str
    label: str = ""
    source: str = ""
    look: Any = None
    meta: dict = field(default_factory=dict)
    desired: list[StateRow] | None = None
    desired_seq: int = 0
    reported: list[StateRow] | None = None
    reported_at: float | None = None
    readback_seq: int = 0
    available: bool | None = None
    last_command: CommandResult | None = None
    command_problem: str | None = None
    auth_problem: bool = False
    problems: list[str] = field(default_factory=list)
    outstanding: int = 0
    updated_at: float | None = None
    last_status: DeviceStatus = DeviceStatus.UNKNOWN


class DeviceStateTracker:
    """Keeps desired and reported rows per driver address and derives Ready / Updating / Needs attention."""

    def __init__(self, clock: Clock, telemetry: Telemetry) -> None:
        self.clock = clock
        self.telemetry = telemetry
        self._devices: dict[str, _Device] = {}
        self._listeners: list[SnapshotListener] = []
        self._seq = itertools.count(1)

    # ---- wiring ------------------------------------------------------------------------------------------------
    def attach(self, executor: CommandExecutor) -> None:
        """Follow an executor: submissions mark UPDATING, results update the command and reported state."""
        executor.add_submit_listener(self.note_submitted)
        executor.add_listener(self.note_command)

    def add_listener(self, fn: SnapshotListener) -> Callable[[], None]:
        """Call `fn(address, snapshot)` whenever a device's record changes (WebSocket push)."""
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    # ---- inputs ------------------------------------------------------------------------------------------------
    def set_desired(
        self,
        address: str,
        rows: Iterable[StateRow],
        *,
        label: str = "",
        source: str = "",
        look: Any = None,
        meta: Mapping[str, Any] | None = None,
    ) -> None:
        """Record the look `address` should show. Earlier readbacks no longer count toward READY."""
        dev = self._dev(address)
        dev.desired = list(rows)
        dev.desired_seq = next(self._seq)
        dev.label, dev.source, dev.look = label, source, look
        dev.meta = dict(meta or {})
        self._changed(dev)

    def note_submitted(self, command: Command) -> None:
        """A command for the device was submitted (executor submit listener)."""
        dev = self._dev(command.device_id)
        dev.outstanding += 1
        self._changed(dev)

    def note_command(self, result: CommandResult) -> None:
        """A command finished (executor result listener)."""
        dev = self._dev(result.command.device_id)
        dev.outstanding = max(0, dev.outstanding - 1)
        outcomes = [a.outcome for a in result.attempts]
        if Outcome.AUTH in outcomes:
            dev.auth_problem = True
        elif Outcome.OK in outcomes:
            dev.auth_problem = False
        if result.readback is not None:
            self._apply_readback(dev, result.readback)
        if result.status is not CommandStatus.SUPERSEDED or dev.last_command is None:
            dev.last_command = result
        if result.status is CommandStatus.APPLIED:
            dev.command_problem = None
        elif result.status in _PROBLEM_STATUSES:
            dev.command_problem = self._command_reason(result)
        self._changed(dev)

    def note_readback(self, address: str, readback: ReadResult) -> None:
        """A readback taken outside the executor (reconciler true-up, outage probe). Records `readback` telemetry."""
        dev = self._dev(address)
        self._apply_readback(dev, readback)
        mismatches = None
        if readback.ok and dev.desired is not None:
            mismatches = len(compare(dev.desired, readback.rows))
        self.telemetry.record(
            "readback",
            device_id=address,
            outcome=str(readback.outcome),
            http_status=readback.http_status,
            elapsed_s=round(readback.elapsed_s, 3),
            mismatches=mismatches,
            available=readback.available,
            error=readback.error,
            simulated=readback.simulated,
        )
        self._changed(dev)

    def flag_problem(self, address: str, reason: str) -> None:
        """Mark a device-level problem (e.g. "Only 12 of 30 heads detected") until `clear_problem`."""
        dev = self._dev(address)
        if reason not in dev.problems:
            dev.problems.append(reason)
            self._changed(dev)

    def clear_problem(self, address: str, reason: str | None = None) -> None:
        """Clear one flagged problem, or all of them when `reason` is None."""
        dev = self._devices.get(address)
        if dev is None:
            return
        before = list(dev.problems)
        dev.problems = [] if reason is None else [p for p in dev.problems if p != reason]
        if dev.problems != before:
            self._changed(dev)

    # ---- queries -----------------------------------------------------------------------------------------------
    def status(self, address: str) -> DeviceStatus:
        dev = self._devices.get(address)
        return self._evaluate(dev)[0] if dev else DeviceStatus.UNKNOWN

    def flagged_only(self, address: str) -> bool:
        """Needs attention only for a flagged problem (`flag_problem`, e.g. D54's "no proven map yet"), with the
        current look sent, read back and matching: nothing is changing, and no re-send can clear a flag."""
        dev = self._devices.get(address)
        if dev is None or not dev.problems or dev.desired is None or dev.outstanding:
            return False
        if dev.auth_problem or dev.available is False or dev.command_problem:
            return False
        if dev.reported is None or dev.readback_seq < dev.desired_seq:
            return False
        return not self._mismatches(dev)

    def snapshot(self, address: str) -> dict:
        """JSON-safe record for /ops, the module page, the visualizer and WebSocket push."""
        dev = self._devices.get(address) or _Device(address)
        status, reason = self._evaluate(dev)
        look = dev.look.to_json() if hasattr(dev.look, "to_json") else dev.look
        return {
            "address": dev.address,
            "label": dev.label,
            "source": dev.source,
            "status": str(status),
            "reason": reason,
            "desired": _rows_json(dev.desired),
            "reported": _rows_json(dev.reported),
            "mismatches": [m.to_json() for m in self._mismatches(dev)],
            "available": dev.available,
            "problems": list(dev.problems),
            "look": look,
            "meta": dict(dev.meta),
            "last_command": dev.last_command.to_json() if dev.last_command else None,
            "updated_at": dev.updated_at,
            "reported_at": dev.reported_at,
        }

    def snapshot_all(self) -> dict[str, dict]:
        return {address: self.snapshot(address) for address in sorted(self._devices)}

    def mismatched(self, addresses: Iterable[str]) -> list[str]:
        """Addresses whose latest successful readback differs from desired — what a re-assert should resend.

        Skips devices with no desired state, no readback yet, or a command still outstanding (that command's own
        readback will answer).
        """
        out = []
        for address in addresses:
            dev = self._devices.get(address)
            if dev is None or dev.desired is None or dev.reported is None or dev.outstanding:
                continue
            if self._mismatches(dev):
                out.append(address)
        return out

    # ---- internals ---------------------------------------------------------------------------------------------
    def _dev(self, address: str) -> _Device:
        dev = self._devices.get(address)
        if dev is None:
            dev = self._devices[address] = _Device(address)
        return dev

    def _apply_readback(self, dev: _Device, readback: ReadResult) -> None:
        if readback.outcome is Outcome.AUTH:
            dev.auth_problem = True
        if not readback.ok:
            return
        dev.auth_problem = False
        dev.reported = list(readback.rows)
        dev.reported_at = readback.fetched_at or self.clock.now()
        dev.readback_seq = next(self._seq)
        dev.available = readback.available
        if dev.desired is not None and not compare(dev.desired, dev.reported):
            dev.command_problem = None  # the device shows the look now, whatever the last command said

    @staticmethod
    def _mismatches(dev: _Device) -> list[Mismatch]:
        if dev.desired is None or dev.reported is None:
            return []
        return compare(dev.desired, dev.reported)

    @staticmethod
    def _command_reason(result: CommandResult) -> str:
        what = result.command.label or "The last command"
        if result.status is CommandStatus.DENIED:
            return f"{what} was refused by the live gate: {result.error or 'no reason given'}"
        if result.status is CommandStatus.MISMATCH:
            return f"{what}: {result.error or 'readback differs from what was sent.'}"
        return f"{what} failed: {result.error or 'no reason given'}"

    def _evaluate(self, dev: _Device) -> tuple[DeviceStatus, str]:
        if dev.desired is None:
            return DeviceStatus.UNKNOWN, "No look has been set for this device yet."
        if dev.outstanding:
            return DeviceStatus.UPDATING, "Sending the look to Hubspace and checking the readback."
        if dev.problems:
            return DeviceStatus.NEEDS_ATTENTION, "; ".join(dev.problems)
        if dev.auth_problem:
            return DeviceStatus.NEEDS_ATTENTION, AUTH_REASON
        if dev.available is False:
            return DeviceStatus.NEEDS_ATTENTION, OFFLINE_REASON
        if dev.command_problem:
            return DeviceStatus.NEEDS_ATTENTION, dev.command_problem
        if dev.reported is None or dev.readback_seq < dev.desired_seq:
            return DeviceStatus.UNKNOWN, "Waiting for a readback of the current look."
        if mismatches := self._mismatches(dev):
            names = ", ".join(sorted({m.function_class for m in mismatches}))
            return DeviceStatus.NEEDS_ATTENTION, f"Readback differs from the look: {names}."
        return DeviceStatus.READY, "Readback matches the look."

    def _changed(self, dev: _Device) -> None:
        dev.updated_at = self.clock.now()
        status, reason = self._evaluate(dev)
        if status is DeviceStatus.NEEDS_ATTENTION and dev.last_status is not DeviceStatus.NEEDS_ATTENTION:
            self.telemetry.record("alert", device_id=dev.address, level="warn", message=reason)
        dev.last_status = status
        if not self._listeners:
            return
        snap = self.snapshot(dev.address)
        for fn in list(self._listeners):
            try:
                fn(dev.address, snap)
            except Exception:
                log.exception("state listener failed")


def _rows_json(rows: list[StateRow] | None) -> list[dict] | None:
    return None if rows is None else [r.to_json() for r in rows]
