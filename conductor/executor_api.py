"""Contract types for the command executor and the desired-vs-reported tracker (05 #6–#7, 07 §5–§6).

Implementations: `conductor/executor.py` (`CommandExecutor`) and `conductor/state.py` (`DeviceStateTracker`).

Executor semantics (binding):
- **One command = one PUT** per attempt, through the driver. Rows for one module are never split.
- **Per-device rate budget**: a write to a device is never sent less than `min_write_interval_s` after the previous
  write to that device was sent (retries included).
- **Per-account bounded concurrency**: at most `account_concurrency` requests in flight per account.
- **Latest-wins coalescing**: each device has at most one in-flight command and at most one pending command. A new
  submission replaces the pending one (which resolves SUPERSEDED without being sent). If a newer command arrives
  while an older one is between retry attempts, the older one stops retrying and resolves SUPERSEDED. The queue
  therefore never grows, however fast guests tap.
- **Retries** (executor-owned): outcomes in `RETRYABLE` retry with `backoff_s`, up to `max_attempts`. DENIED,
  CLIENT_ERROR, NOT_FOUND and AUTH never retry.
- **Readback**: after a 2xx, when `verify` is set, GET after `readback_delay_s`, then every `readback_interval_s`
  up to `readback_attempts` times, until `compare(expect, rows)` is empty → APPLIED, else MISMATCH.
  Readbacks don't count against the write budget but do take an account concurrency slot.
- **Locks**: a device held in the lock manager refuses ENGINE commands (LOCKED, nothing sent); BENCH commands
  need `lease_token` for the current lease, and a BENCH command to an unlocked device is refused too (bench
  and module-page writes always hold the device's lock).
- **Gate**: a DENIED write resolves DENIED immediately; the executor records `gate_denied` telemetry.
- Every command records one `command` telemetry event and one `attempt` event per attempt (see telemetry.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from conductor.drivers.base import ReadResult, StateRow, WriteResult
from conductor.gate import Purpose
from conductor.rows import Mismatch


class CommandStatus(StrEnum):
    APPLIED = "applied"  # 2xx and readback matches (or verify=False and 2xx)
    MISMATCH = "mismatch"  # 2xx but readback still differs after the verification window
    FAILED = "failed"  # attempts exhausted or a non-retryable outcome
    SUPERSEDED = "superseded"  # a newer command for the same device replaced this one
    DENIED = "denied"  # the live gate refused it
    LOCKED = "locked"  # the device is locked by someone else (or a bench write without a lease)
    INVALID = "invalid"  # the rows failed validation before sending


@dataclass
class Command:
    device_id: str  # the driver-level address (sim-… or metadevice id)
    rows: list[StateRow]
    purpose: Purpose
    label: str = ""  # human label for telemetry and /ops ("mode: golden-hour", "module: fill", "gate_b")
    lease_token: str | None = None
    verify: bool = True
    expect: list[StateRow] | None = None  # rows readback must show; default = rows
    source: str = ""  # kiosk / ops / reconciler / bench / module / idle
    # Bench only (refused INVALID for any other purpose). `stamp_offset_s` shifts the lastUpdateTime the driver
    # stamps (send time + offset, −3600…0 s): the stale-clock bench job's "PUT stamped 10 min ago" (07 §10).
    # `max_attempts` lowers the policy's attempt limit: the rate ramp stops at the first 429/403, never retries.
    stamp_offset_s: float = 0.0
    max_attempts: int | None = None


@dataclass
class CommandTimings:
    queued_at: float = 0.0
    sent_at: float | None = None  # first attempt
    acked_at: float | None = None  # the successful attempt's response
    verified_at: float | None = None  # readback matched

    def to_json(self) -> dict:
        return {
            "queued_at": self.queued_at,
            "sent_at": self.sent_at,
            "acked_at": self.acked_at,
            "verified_at": self.verified_at,
        }


@dataclass
class CommandResult:
    command: Command
    status: CommandStatus
    attempts: list[WriteResult] = field(default_factory=list)
    readback: ReadResult | None = None
    mismatches: list[Mismatch] = field(default_factory=list)
    timings: CommandTimings = field(default_factory=CommandTimings)
    error: str | None = None
    simulated: bool = False

    @property
    def ok(self) -> bool:
        return self.status is CommandStatus.APPLIED

    def to_json(self) -> dict:
        return {
            "device_id": self.command.device_id,
            "label": self.command.label,
            "purpose": str(self.command.purpose),
            "status": str(self.status),
            "attempts": [
                {"outcome": str(a.outcome), "http_status": a.http_status, "elapsed_s": round(a.elapsed_s, 3)}
                for a in self.attempts
            ],
            "mismatches": [m.to_json() for m in self.mismatches],
            "timings": self.timings.to_json(),
            "error": self.error,
            "simulated": self.simulated,
        }


class DeviceStatus(StrEnum):
    """What surfaces show — honest, never faked (05 #7)."""

    READY = "ready"  # readback matches desired
    UPDATING = "updating"  # a command is queued/in flight/verifying
    NEEDS_ATTENTION = "needs_attention"  # mismatch, failure, offline, head-count problem, auth
    UNKNOWN = "unknown"  # no desired state yet, or never read back
