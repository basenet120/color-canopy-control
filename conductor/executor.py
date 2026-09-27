"""The command executor: every device write goes through here (05 #6, 07 §6; semantics in executor_api.py).

Rules implemented (binding text in `conductor/executor_api.py`):
- **One command = one PUT per attempt.** Rows are handed to the driver as given; nothing is split.
- **Per-device rate budget.** Each device has one worker. A write is never sent less than
  `min_write_interval_s` after the previous write to that device was sent, retries included.
- **Per-account bounded concurrency.** Every request (writes and readbacks) holds a slot of an
  `asyncio.Semaphore(account_concurrency)` for its account while it is on the wire. The default of 9 lets a
  9-module burst go out in one wave.
- **Latest-wins coalescing.** A device has at most one command in flight (sending, retrying or verifying) and at
  most one pending. A new submission replaces the pending one, which resolves SUPERSEDED without being sent. A
  newer submission also ends an older command that is waiting between retries, or waiting to verify its
  readback: that older command resolves SUPERSEDED (its attempts are kept on the result). So the queue never
  holds more than 2 commands per device, however fast guests tap.
- **Retries** only for retryable outcomes, waiting `backoff_s[n]` (and never less than the rate budget), up to
  `max_attempts`. DENIED, CLIENT_ERROR, NOT_FOUND and AUTH resolve at once.
- **Readback.** After a 2xx with `verify`, GET after `readback_delay_s`, then every `readback_interval_s`, up to
  `readback_attempts` GETs, until `compare(expect or rows, readback)` is empty → APPLIED, else MISMATCH.
- **Locks.** An ENGINE command to a locked device resolves LOCKED; a BENCH command needs the current lease for
  that device (and a BENCH command to an unlocked device is refused too). Checked at submit and again before
  every attempt, so a lock taken while a command waits still stops it. Nothing is sent when LOCKED.
- **Gate.** A DENIED write resolves DENIED at once and records `gate_denied` telemetry.
- **Telemetry.** One `attempt` event per write attempt, one `readback` event per verification GET, one `command`
  event per finished command (timings are wall-clock epoch seconds from the injected clock).
- **Fresh token before a burst (07 §6).** `burst()` first calls the driver's optional
  `ensure_fresh_token(account, purpose=…)` once per account in the burst (holding an account slot, since it may send
  one GET), records a `token` event per account, then enqueues the PUTs. A failed check doesn't stop the burst:
  the PUTs report what really happened. Drivers without the hook (SimDriver, test fakes) skip this step.
- **Withdraw.** `withdraw(address, lease_token=…)` (the module page's Hold) resolves the pending command SUPERSEDED
  and stops an in-flight one before its next send; a write already on the wire can't be recalled, and a command
  that is only verifying keeps verifying (readback sends nothing).
- **Shutdown.** `close()` resolves every unfinished command FAILED (pending, sending, retrying or verifying).

Decision (recorded here, see the final report): a command waiting to verify is "in flight". When a newer command
for the same device arrives, the older one stops verifying (resolves SUPERSEDED) instead of spending account
slots on GETs whose answer no longer matters; the newer command's own readback is the one that counts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from conductor.clock import Clock
from conductor.config import ExecutorSettings
from conductor.drivers.base import TELEMETRY_CLASSES, DeviceDriver, Outcome, ReadResult, StateRow, WriteResult
from conductor.executor_api import Command, CommandResult, CommandStatus, CommandTimings
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager, LockError
from conductor.rows import compare
from conductor.telemetry import Telemetry

log = logging.getLogger(__name__)

WINDOW_S = 60.0  # the rate gauges count writes in this trailing window
_EPS = 1e-9

ResultListener = Callable[[CommandResult], None]
SubmitListener = Callable[[Command], None]

# Plain-English defaults when a driver gives no error text of its own.
_OUTCOME_TEXT = {
    Outcome.THROTTLED: "Hubspace is throttling requests (429/403)",
    Outcome.SERVER_ERROR: "Hubspace had a server error",
    Outcome.CLIENT_ERROR: "Hubspace refused the request as malformed",
    Outcome.NOT_FOUND: "Hubspace doesn't know this device id",
    Outcome.AUTH: "the Hubspace sign-in has expired: re-sign-in required on /ops/accounts",
    Outcome.TIMEOUT: "the request timed out",
    Outcome.CONNECTION: "couldn't reach Hubspace (network problem)",
    Outcome.MALFORMED: "Hubspace answered with an unexpected response",
    Outcome.DENIED: "the live gate refused the write; nothing was sent",
}


def _describe(outcome: Outcome, http_status: int | None, error: str | None) -> str:
    if error:
        return error
    text = _OUTCOME_TEXT.get(outcome, str(outcome))
    return f"{text} (HTTP {http_status})" if http_status else text


@dataclass
class _Job:
    command: Command
    future: asyncio.Future[CommandResult]
    result: CommandResult
    withdrawn: asyncio.Event = field(default_factory=asyncio.Event)  # set by withdraw(): send nothing more
    withdraw_reason: str | None = None


@dataclass
class _Slot:
    """Per-device state: the worker, the one pending and one in-flight command, and the rate history."""

    address: str
    account: str
    pending: _Job | None = None
    current: _Job | None = None
    worker: asyncio.Task | None = None
    newer: asyncio.Event = field(default_factory=asyncio.Event)
    last_sent: float | None = None  # monotonic time the last write was sent
    sent: deque[float] = field(default_factory=deque)  # monotonic send times within WINDOW_S


class CommandExecutor:
    """Sends commands to one driver's devices under the rate, concurrency, coalescing and lock rules."""

    def __init__(
        self,
        driver: DeviceDriver,
        *,
        locks: DeviceLockManager,
        telemetry: Telemetry,
        clock: Clock,
        policy: ExecutorSettings,
    ) -> None:
        self.driver = driver
        self.locks = locks
        self.telemetry = telemetry
        self.clock = clock
        self.policy = policy
        self._slots: dict[str, _Slot] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._account_sent: dict[str, deque[float]] = {}
        self._account_active: dict[str, int] = {}
        self._listeners: list[ResultListener] = []
        self._submit_listeners: list[SubmitListener] = []
        self._closed = False

    # ---- public API ------------------------------------------------------------------------------------------
    async def submit(self, cmd: Command) -> CommandResult:
        """Queue `cmd` (replacing any pending command for the device) and wait for its final result."""
        future = self._enqueue(cmd)
        # Shield: a caller that goes away (HTTP client disconnect) must not cancel the device's command.
        return await asyncio.shield(future)

    async def burst(self, cmds: list[Command]) -> list[CommandResult]:
        """Submit every command at once (one PUT per device, concurrently) and wait for all results, in order.

        First makes sure each account's bearer has >= 15 s left (07 §6), when the driver offers that hook."""
        await self._prepare_burst(cmds)
        futures = [self._enqueue(c) for c in cmds]
        return list(await asyncio.gather(*(asyncio.shield(f) for f in futures)))

    def withdraw(self, address: str, *, lease_token: str | None = None, reason: str | None = None) -> int:
        """Send nothing more for `address`'s commands (those presenting `lease_token`, when given).

        The pending command resolves SUPERSEDED unsent; an in-flight one resolves SUPERSEDED before its next
        attempt (a PUT already on the wire can't be recalled). A command that is already verifying is left to
        finish: its write was sent and readback is read-only. Returns how many commands were withdrawn."""
        slot = self._slots.get(address)
        if slot is None:
            return 0

        def mine(job: _Job | None) -> bool:
            return job is not None and (lease_token is None or job.command.lease_token == lease_token)

        count = 0
        if mine(slot.pending):
            job, slot.pending = slot.pending, None
            self._finish(job, CommandStatus.SUPERSEDED, reason)
            count += 1
        current = slot.current
        if mine(current) and not current.future.done() and current.result.timings.acked_at is None:
            current.withdraw_reason = reason
            current.withdrawn.set()
            count += 1
        return count

    def queue_depth(self) -> int:
        """Commands pending or in flight across all devices (never more than 2 per device)."""
        return sum((s.pending is not None) + (s.current is not None) for s in self._slots.values())

    def in_flight(self, address: str) -> bool:
        """True while a command for `address` is pending, being sent, retrying or verifying."""
        slot = self._slots.get(address)
        return slot is not None and (slot.pending is not None or slot.current is not None)

    def budget(self, address: str | None = None) -> dict:
        """Rate gauges for /ops: writes in the last 60 s per device and per account, and time to the next write.

        With an address: that device's gauge. Without: every account and every device seen so far.
        """
        now = self.clock.monotonic()
        if address is not None:
            slot = self._slots.get(address)
            if slot is None:
                account = self._account_of(address)
                return {
                    "device_id": address,
                    "account": account,
                    "writes_60s": 0,
                    "account_writes_60s": self._account_count(account, now),
                    "next_write_in_s": 0.0,
                    "min_write_interval_s": self.policy.min_write_interval_s,
                    "pending": False,
                    "in_flight": False,
                }
            return self._device_budget(slot, now)
        accounts = sorted({s.account for s in self._slots.values()} | set(self._account_sent))
        return {
            "window_s": WINDOW_S,
            "min_write_interval_s": self.policy.min_write_interval_s,
            "account_concurrency": self.policy.account_concurrency,
            "queue_depth": self.queue_depth(),
            "accounts": {
                a: {
                    "writes_60s": self._account_count(a, now),
                    "requests_in_flight": self._account_active.get(a, 0),
                }
                for a in accounts
            },
            "devices": {addr: self._device_budget(s, now) for addr, s in sorted(self._slots.items())},
        }

    def add_listener(self, fn: ResultListener) -> Callable[[], None]:
        """Call `fn(result)` for every finished command (tracker, WebSocket push). Returns an unsubscribe."""
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def add_submit_listener(self, fn: SubmitListener) -> Callable[[], None]:
        """Call `fn(command)` for every submission, before its result (the tracker's UPDATING state)."""
        self._submit_listeners.append(fn)
        return lambda: self._submit_listeners.remove(fn) if fn in self._submit_listeners else None

    async def close(self) -> None:
        """Stop the workers. Commands not yet finished (pending, sending, retrying or verifying) resolve FAILED;
        nothing further is sent."""
        self._closed = True
        workers = []
        for slot in self._slots.values():
            if slot.pending is not None:
                job, slot.pending = slot.pending, None
                self._finish(
                    job, CommandStatus.FAILED, "The executor shut down before this command was sent."
                )
            if slot.worker is not None and not slot.worker.done():
                slot.worker.cancel()
                workers.append(slot.worker)
        for w in workers:
            with contextlib.suppress(asyncio.CancelledError):
                await w

    # ---- submission --------------------------------------------------------------------------------------------
    def _enqueue(self, cmd: Command) -> asyncio.Future[CommandResult]:
        loop = asyncio.get_running_loop()
        job = _Job(
            cmd,
            loop.create_future(),
            CommandResult(
                command=cmd,
                status=CommandStatus.FAILED,
                timings=CommandTimings(queued_at=self.clock.now()),
                simulated=bool(getattr(self.driver, "simulated", False)),
            ),
        )
        self._emit_submit(cmd)
        if self._closed:
            self._finish(job, CommandStatus.FAILED, "The executor has shut down; nothing was sent.")
            return job.future
        if invalid := self._invalid(cmd):
            self._finish(job, CommandStatus.INVALID, invalid)
            return job.future
        if refusal := self._lock_refusal(cmd):
            self._finish(job, CommandStatus.LOCKED, refusal)
            return job.future
        slot = self._slot(cmd.device_id)
        if slot.pending is not None:
            old, slot.pending = slot.pending, job
            self._finish(old, CommandStatus.SUPERSEDED, None)
        else:
            slot.pending = job
        slot.newer.set()
        if slot.worker is None or slot.worker.done():
            slot.worker = asyncio.create_task(self._run(slot), name=f"executor:{slot.address}")
        return job.future

    @staticmethod
    def _invalid(cmd: Command) -> str | None:
        if not cmd.rows:
            return "Nothing to send: the command has no rows."
        if not all(isinstance(r, StateRow) for r in cmd.rows):
            return "The command's rows aren't state rows; nothing was sent."
        telemetry_rows = sorted({r.function_class for r in cmd.rows if r.function_class in TELEMETRY_CLASSES})
        if telemetry_rows:
            return (
                f"{', '.join(telemetry_rows)} describe the device, not the look, and are never written "
                "(07 §5); nothing was sent."
            )
        return None

    def _lock_refusal(self, cmd: Command) -> str | None:
        address = cmd.device_id
        if cmd.purpose is Purpose.BENCH:
            if not self.locks.is_locked(address):
                return f"Bench and module-page writes need {address}'s lock; open the device first. Nothing was sent."
            try:
                self.locks.validate(address, cmd.lease_token)
            except LockError as e:
                return f"{e} Nothing was sent."
            return None
        holder = self.locks.holder(address)
        if holder is not None:
            return (
                f"{address} is held by {holder.owner}; the show engine leaves it alone until that lock is "
                "released. Nothing was sent."
            )
        return None

    # ---- the per-device worker ---------------------------------------------------------------------------------
    async def _run(self, slot: _Slot) -> None:
        try:
            while slot.pending is not None and not self._closed:
                await self._wait_rate(slot)
                async with self._request_slot(slot.account):
                    job = slot.pending
                    if job is None:  # pragma: no cover - only this worker pops
                        break
                    slot.pending = None
                    slot.newer.clear()
                    slot.current = job
                    first = await self._attempt(slot, job)
                try:
                    if first is not None:
                        await self._complete(slot, job, first)
                except Exception:
                    log.exception("executor: unexpected error on %s", slot.address)
                    self._finish(job, CommandStatus.FAILED, "Internal error in the executor; see the logs.")
                # Cleared only on the way out of a finished command: when close() cancels this worker mid-command
                # (sending, retrying, verifying), `current` must still be set for the handler below to resolve it.
                slot.current = None
        except asyncio.CancelledError:
            if slot.current is not None:
                self._finish(
                    slot.current, CommandStatus.FAILED, "The executor shut down during this command."
                )
                slot.current = None
            raise

    async def _attempt(self, slot: _Slot, job: _Job) -> WriteResult | None:
        """One PUT (caller holds the account slot and has waited for the rate budget). None = resolved without
        sending (LOCKED, or withdrawn)."""
        cmd = job.command
        if refusal := self._lock_refusal(cmd):
            self._finish(job, CommandStatus.LOCKED, refusal)
            return None
        if job.withdrawn.is_set():
            self._finish(job, CommandStatus.SUPERSEDED, job.withdraw_reason)
            return None
        n = len(job.result.attempts) + 1
        now_mono, now = self.clock.monotonic(), self.clock.now()
        previous_sent = slot.last_sent
        slot.last_sent = now_mono
        slot.sent.append(now_mono)
        account_sent = self._account_sent.setdefault(slot.account, deque())
        account_sent.append(now_mono)
        # Keep only the gauge window, whether or not anyone reads the budget (the show runs for 12 days).
        _prune(slot.sent, now_mono)
        _prune(account_sent, now_mono)
        if job.result.timings.sent_at is None:
            job.result.timings.sent_at = now
        try:
            wr = await self.driver.write(cmd.device_id, cmd.rows, purpose=cmd.purpose)
        except Exception as e:
            log.exception("driver.write raised for %s (drivers must classify, not raise)", cmd.device_id)
            wr = WriteResult(
                cmd.device_id,
                Outcome.CLIENT_ERROR,
                sent_at=now,
                error=f"Internal driver error ({type(e).__name__}); not retried.",
            )
        if wr.outcome is Outcome.DENIED:
            # Nothing left the process, so it doesn't count against the device's budget.
            slot.last_sent = previous_sent
            with contextlib.suppress(ValueError):
                slot.sent.remove(now_mono)
                self._account_sent[slot.account].remove(now_mono)
        job.result.attempts.append(wr)
        self.telemetry.record(
            "attempt",
            device_id=cmd.device_id,
            label=cmd.label,
            n=n,
            outcome=str(wr.outcome),
            http_status=wr.http_status,
            elapsed_s=round(wr.elapsed_s, 3),
            error=wr.error,
            simulated=wr.simulated,
        )
        return wr

    async def _complete(self, slot: _Slot, job: _Job, wr: WriteResult) -> None:
        """Retries, then readback, then the final status."""
        cmd = job.command
        while not wr.ok:
            if wr.outcome is Outcome.DENIED:
                reason = _describe(wr.outcome, wr.http_status, wr.error)
                self.telemetry.record(
                    "gate_denied", device_id=cmd.device_id, reason=reason, purpose=str(cmd.purpose)
                )
                self._finish(job, CommandStatus.DENIED, reason)
                return
            attempts = len(job.result.attempts)
            if not wr.outcome.retryable or attempts >= self.policy.max_attempts:
                why = _describe(wr.outcome, wr.http_status, wr.error)
                if wr.outcome.retryable:
                    why = f"Gave up after {attempts} attempts: {why}"
                self._finish(job, CommandStatus.FAILED, why)
                return
            backoff = self._backoff(attempts)
            wait = max(backoff, self._rate_remaining(slot))
            if await self._wait_unless_newer(slot, wait, job.withdrawn):
                self._finish(job, CommandStatus.SUPERSEDED, job.withdraw_reason)
                return
            await self._wait_rate(slot)
            async with self._request_slot(slot.account):
                if slot.pending is not None:  # a newer command arrived while we queued for the account
                    self._finish(job, CommandStatus.SUPERSEDED, None)
                    return
                nxt = await self._attempt(slot, job)
            if nxt is None:
                return
            wr = nxt
        job.result.timings.acked_at = self.clock.now()
        if not cmd.verify:
            self._finish(job, CommandStatus.APPLIED, None)
            return
        await self._verify(slot, job)

    async def _verify(self, slot: _Slot, job: _Job) -> None:
        cmd = job.command
        expect = cmd.expect if cmd.expect is not None else cmd.rows
        last_error: str | None = None
        mismatches = []
        for i in range(self.policy.readback_attempts):
            delay = self.policy.readback_delay_s if i == 0 else self.policy.readback_interval_s
            if await self._wait_unless_newer(slot, delay):
                self._finish(job, CommandStatus.SUPERSEDED, None)
                return
            rr = await self._read(slot, cmd)
            job.result.readback = rr
            if not rr.ok:
                last_error = _describe(rr.outcome, rr.http_status, rr.error)
                self._record_readback(cmd, rr, None)
                continue
            mismatches = compare(expect, rr.rows)
            self._record_readback(cmd, rr, len(mismatches))
            if not mismatches:
                job.result.mismatches = []
                job.result.timings.verified_at = self.clock.now()
                self._finish(job, CommandStatus.APPLIED, None)
                return
            last_error = None
        job.result.mismatches = mismatches
        if last_error is not None and not mismatches:
            error = f"The write was accepted but readback failed ({last_error}); the state is not verified."
        else:
            names = ", ".join(sorted({m.function_class for m in mismatches}))
            error = f"The write was accepted but readback still differs after verification: {names}."
        self._finish(job, CommandStatus.MISMATCH, error)

    async def _read(self, slot: _Slot, cmd: Command) -> ReadResult:
        async with self._request_slot(slot.account):
            try:
                return await self.driver.read_state(cmd.device_id, purpose=cmd.purpose)
            except Exception as e:
                log.exception("driver.read_state raised for %s", cmd.device_id)
                return ReadResult(
                    cmd.device_id,
                    Outcome.CONNECTION,
                    fetched_at=self.clock.now(),
                    error=f"Internal driver error ({type(e).__name__}) during readback.",
                )

    # ---- timing helpers ----------------------------------------------------------------------------------------
    def _backoff(self, failed_attempts: int) -> float:
        table = self.policy.backoff_s or [self.policy.min_write_interval_s]
        return float(table[min(failed_attempts - 1, len(table) - 1)])

    def _rate_remaining(self, slot: _Slot) -> float:
        if slot.last_sent is None:
            return 0.0
        return slot.last_sent + self.policy.min_write_interval_s - self.clock.monotonic()

    async def _wait_rate(self, slot: _Slot) -> None:
        while (remaining := self._rate_remaining(slot)) > _EPS:
            await self.clock.sleep(remaining)

    async def _wait_unless_newer(
        self, slot: _Slot, seconds: float, stop: asyncio.Event | None = None
    ) -> bool:
        """Wait `seconds` on the clock; return True early if a newer command for the device arrives (or `stop`
        is set)."""

        def interrupted() -> bool:
            return slot.pending is not None or (stop is not None and stop.is_set())

        if interrupted():
            return True
        if seconds <= 0:
            return False
        waits = {asyncio.ensure_future(self.clock.sleep(seconds)), asyncio.ensure_future(slot.newer.wait())}
        if stop is not None:
            waits.add(asyncio.ensure_future(stop.wait()))
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waits:
                w.cancel()
        return interrupted()

    async def _prepare_burst(self, cmds: list[Command]) -> None:
        """07 §6: before a burst, ask the driver (if it can) for a bearer with >= 15 s left, once per account."""
        hook = getattr(self.driver, "ensure_fresh_token", None)
        if hook is None or self._closed:
            return
        purposes: dict[str, Purpose] = {}
        for c in cmds:
            purposes.setdefault(self._account_of(c.device_id), c.purpose)

        async def one(account: str, purpose: Purpose) -> None:
            started = self.clock.monotonic()
            try:
                async with self._request_slot(account):
                    outcome = await hook(account, purpose=purpose)
                error = None
            except Exception as e:
                log.exception("ensure_fresh_token raised for account %s", account)
                outcome, error = Outcome.CONNECTION, f"Internal driver error ({type(e).__name__})."
            self.telemetry.record(
                "token",
                account=account,
                action="ensure_fresh",
                outcome=str(outcome),
                elapsed_s=round(self.clock.monotonic() - started, 3),
                error=error,
            )

        await asyncio.gather(*(one(a, p) for a, p in purposes.items()))

    @contextlib.asynccontextmanager
    async def _request_slot(self, account: str):
        sem = self._semaphores.get(account)
        if sem is None:
            sem = self._semaphores[account] = asyncio.Semaphore(self.policy.account_concurrency)
        async with sem:
            self._account_active[account] = self._account_active.get(account, 0) + 1
            try:
                yield
            finally:
                self._account_active[account] -= 1

    # ---- bookkeeping -------------------------------------------------------------------------------------------
    def _slot(self, address: str) -> _Slot:
        slot = self._slots.get(address)
        if slot is None:
            slot = self._slots[address] = _Slot(address, self._account_of(address))
        return slot

    def _account_of(self, address: str) -> str:
        try:
            return self.driver.account_of(address)
        except LookupError:
            return "unknown"

    def _account_count(self, account: str, now: float) -> int:
        dq = self._account_sent.get(account)
        if dq is None:
            return 0
        _prune(dq, now)
        return len(dq)

    def _device_budget(self, slot: _Slot, now: float) -> dict:
        _prune(slot.sent, now)
        return {
            "device_id": slot.address,
            "account": slot.account,
            "writes_60s": len(slot.sent),
            "account_writes_60s": self._account_count(slot.account, now),
            "next_write_in_s": round(max(0.0, self._rate_remaining(slot)), 3),
            "min_write_interval_s": self.policy.min_write_interval_s,
            "pending": slot.pending is not None,
            "in_flight": slot.current is not None,
        }

    def _record_readback(self, cmd: Command, rr: ReadResult, mismatches: int | None) -> None:
        self.telemetry.record(
            "readback",
            device_id=cmd.device_id,
            label=cmd.label,
            outcome=str(rr.outcome),
            http_status=rr.http_status,
            elapsed_s=round(rr.elapsed_s, 3),
            mismatches=mismatches,
            available=rr.available,
            error=rr.error,
            simulated=rr.simulated,
        )

    def _finish(self, job: _Job, status: CommandStatus, error: str | None) -> None:
        if job.future.done():
            return
        result = job.result
        result.status = status
        result.error = error
        cmd = job.command
        self.telemetry.record(
            "command",
            device_id=cmd.device_id,
            label=cmd.label,
            purpose=str(cmd.purpose),
            source=cmd.source,
            rows=len(cmd.rows),
            bytes=max((a.bytes_sent for a in result.attempts), default=0),
            status=str(status),
            timings=result.timings.to_json(),
            attempts=len(result.attempts),
            mismatches=len(result.mismatches),
            error=error,
            simulated=result.simulated,
        )
        job.future.set_result(result)
        for fn in list(self._listeners):
            try:
                fn(result)
            except Exception:
                log.exception("executor result listener failed")

    def _emit_submit(self, cmd: Command) -> None:
        for fn in list(self._submit_listeners):
            try:
                fn(cmd)
            except Exception:
                log.exception("executor submit listener failed")


def _prune(dq: deque[float], now: float) -> None:
    while dq and now - dq[0] >= WINDOW_S:
        dq.popleft()
