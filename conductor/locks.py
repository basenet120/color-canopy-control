"""Exclusive per-device locks for bench jobs and the single-module test page (07 §1).

While a device is locked, the show engine (executor with Purpose.ENGINE, reconciler, idle hold, slot manager)
leaves it alone, and only writes that present the lease go through. A lease expires after `idle_timeout_s`
without use (default 10 min). A lock can never be taken on a device the `lockable` check refuses — in bench/live
that is the live gate's effective allowlist; on the simulator it is the registry.

Every change is reported to `on_change(LockEvent)`: `acquire`, `steal` (with the lease it replaced), `release`, or
`expire` (with the wall-clock time it really expired: expiry is noticed lazily, on the next look at the lock).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from conductor.clock import Clock, RealClock

IDLE_TIMEOUT_S = 600.0


class LockError(RuntimeError):
    """The lock can't be taken or used. Message is operator-safe."""


LockAction = Literal["acquire", "steal", "release", "expire"]


@dataclass
class Lease:
    device_id: str
    owner: str  # who holds it, for /ops ("module page · alon", "bench · gate_b")
    token: str
    acquired_at: float
    last_used: float

    def to_json(self) -> dict:
        return {
            "device_id": self.device_id,
            "owner": self.owner,
            "acquired_at": self.acquired_at,
            "last_used": self.last_used,
        }


@dataclass(frozen=True)
class LockEvent:
    """One change to a device's lock, for telemetry."""

    device_id: str
    action: LockAction
    lease: Lease  # the lease taken (acquire/steal) or the one that ended (release/expire)
    previous: Lease | None = None  # steal: the lease that was taken over
    expired_at: float | None = None  # expire: wall-clock epoch s when the idle timeout actually ran out


@dataclass
class DeviceLockManager:
    lockable: Callable[[str], tuple[bool, str]] = lambda _id: (True, "")
    clock: Clock = field(default_factory=RealClock)
    idle_timeout_s: float = IDLE_TIMEOUT_S
    on_change: Callable[[LockEvent], None] | None = None
    _leases: dict[str, Lease] = field(default_factory=dict)

    def holder(self, device_id: str) -> Lease | None:
        lease = self._leases.get(device_id)
        if lease:
            overdue = self.clock.monotonic() - (lease.last_used + self.idle_timeout_s)
            if overdue > 0:
                del self._leases[device_id]
                expired_at = self.clock.now() - overdue
                self._notify(LockEvent(device_id, "expire", lease, expired_at=expired_at))
                return None
        return lease

    def is_locked(self, device_id: str) -> bool:
        return self.holder(device_id) is not None

    def acquire(self, device_id: str, owner: str, *, steal: bool = False) -> Lease:
        ok, why = self.lockable(device_id)
        if not ok:
            raise LockError(why or f"{device_id} can't be locked: it isn't in the allowlist")
        current = self.holder(device_id)
        if current and not steal:
            raise LockError(
                f"{device_id} is in use by {current.owner}. Wait for it to finish, or take it over."
            )
        now = self.clock.monotonic()
        lease = Lease(device_id, owner, secrets.token_urlsafe(18), now, now)
        self._leases[device_id] = lease
        self._notify(LockEvent(device_id, "steal" if current else "acquire", lease, previous=current))
        return lease

    def validate(self, device_id: str, token: str | None) -> Lease:
        """The live lease for `token` on `device_id` (touching it), or LockError."""
        lease = self.holder(device_id)
        if lease is None or token is None or not secrets.compare_digest(lease.token, token):
            raise LockError(f"You no longer hold {device_id} (it was released, timed out or taken over).")
        lease.last_used = self.clock.monotonic()
        return lease

    def touch(self, lease: Lease) -> None:
        self.validate(lease.device_id, lease.token)

    def release(self, device_id: str, token: str | None) -> bool:
        lease = self._leases.get(device_id)
        if lease and token and secrets.compare_digest(lease.token, token):
            del self._leases[device_id]
            self._notify(LockEvent(device_id, "release", lease))
            return True
        return False

    def release_all(self, owner_prefix: str | None = None) -> None:
        for device_id, lease in list(self._leases.items()):
            if owner_prefix is None or lease.owner.startswith(owner_prefix):
                del self._leases[device_id]
                self._notify(LockEvent(device_id, "release", lease))

    def snapshot(self) -> list[dict]:
        return [lease.to_json() for d in list(self._leases) if (lease := self.holder(d))]

    def _notify(self, event: LockEvent) -> None:
        if self.on_change:
            self.on_change(event)
