"""The reconciler: keeps the canopy showing the current mode (05 #6–#7, 07 §5–§6).

One loop on the injected Clock (a tick every `TICK_S`), owned by the CanopyEngine (`conductor/canopy.py`):

- **Re-assert.** `reassert_after_s` after each change, after any command that didn't verify, and on every true-up,
  re-send the FULL state vector only to modules whose readback differs from desired (`tracker.mismatched`, or whose
  last command FAILED / MISMATCHed without a readback). Motion plans (`plan.motion`) re-trigger every module as one
  burst or none — and only when no module has a command in flight, so it really is one burst. A slot *object* that
  differs is drift, not something a re-send fixes (see Slots).
- **True-up, rolling** (review gap: "any wrong module self-heals within 10 s", including a change made outside
  the Conductor — the Hubspace app, a controller that lost its look, a cloud-side revert). Each non-locked module is
  read (REST, `tracker.note_readback`) once every `true_up_interval_s`, the reads spread evenly across the modules
  (9 modules at 5 s → one GET every ~0.56 s, 1.8 GET/s on the canopy account; at most `TRUE_UP_CONCURRENCY` in
  flight). Reads never count against the write budget. A read that shows the module differs re-sends that module at
  once (a motion plan: all nine as one burst, via the re-assert); its slot hashes are checked too. A read that
  failed (a 429, a timeout) is retried once `READ_RETRY_S` later, then waits its turn. A module with a command in
  flight is skipped (its own verification readback answers). `true_up()` still reads every module at once (the
  driver's recovery watchdog asks for it).
- **Hysteresis (07 §6).** Retry first. A module "wrong" (tracker NEEDS_ATTENTION for a reason a tier change could
  fix — not offline, not a dead sign-in, not a live-gate refusal) longer than `downgrade_after_wrong_s` downgrades
  the current mode one rung. So do `downgrade_throttles` **commands that still ended throttled after the executor's
  retries** within `downgrade_throttle_window_s` on one account. Individual 429s that a retry cleared never count —
  that is what keeps 5% random throttling from ever downgrading. Floors lift one rung after `upgrade_after_clean_s`
  with no wrong module and no throttled command — straight to the next rung that is allowed and compiles (a rung that
  can't be shown is skipped, so T3 → T1 takes one clean interval when T2 isn't available). A stepped rung below the
  current one is never a downgrade target, and a floor never lifts onto one (D65): a downgrade from Golden Hour's
  per-head sun lands on a still rung, not on the rung that writes most.
- **Outage.** While the engine rests, only the probe runs: one module's readback every `outage_probe_interval_s`
  (rotating through the modules); the first good one ends the outage and the engine re-applies the look.
- **Locks.** A module whose lock was released gets the current look again (all nine for a motion plan).
- **The ripple (D64).** A module still waiting for its ring (`engine.wave_pending`) is left alone: never re-asserted,
  never re-sent after a true-up read, never sent on a lock release — its ring sends it.
- **Stepped looks (D65, conductor/sequence.py).** While a stepped run moves (`engine.stepping`), every step is
  a verified write, so the rolling true-up reads stand down (each would race the next step's write); the slot
  patterns are checked from the steps' verification readbacks instead, at most once per `SLOT_CHECK_INTERVAL_S` per
  module (`note_verification`). A module that differs is left to the next step (`runner.defer`) — unless its last
  command FAILED or MISMATCHed and the next step is more than `STEP_DEFER_S` away, then it is re-sent now. Each step
  resets the hysteresis timers of the modules it sends (a new desired state); the stepped phase judges a module that
  stops verifying (`ProgramRunner.status`). Commands that still ended throttled downgrade a stepped look to its still
  module-level rung like any other. A run that ended while nothing could be sent (commissioning) gets its still look
  from the next re-assert (`engine.program_unsent`).
- **Idle hold.** After `kiosk.idle_return_s` with no accepted change (and guest input not paused, no stepped
  rehearsal running), request `kiosk.idle_mode` with source "idle".
- **Guest settings (D60).** "Differs" means readback differs from `engine.desired_rows` — the look with the guest's
  brightness / power applied — so a re-assert restores the guest's choice and never undoes it. **Idle reset:** while
  a guest setting is in force, after `kiosk.idle_reset_s` with no guest input (and guest input not paused, no outage,
  not commissioning), clear the guest's brightness and power (on, designed brightness) and, when `kiosk.idle_scene`
  is set, show that mood (source "idle"), so the canopy is never left dark or dim. Without a guest setting in force
  it does nothing, so it never undoes a later mood choice and never alternates with the idle hold. The idle hold goes
  first in a tick; a reset that fell due during an outage goes out with the recovery burst. One attempt per idle
  period.
- **Slots.** During a true-up each module's slot objects are hashed (canonical projection of the readback, 07 §6)
  and compared with the recorded hash; the effect fields that make a pattern animate (motionEffect, brightnessEffect
  — outside the hash) are compared with those recorded, separately. A drifted T1 slot (commissioned, origin "api") is
  rewritten — at most once per `slot_rewrite_min_interval_s` per slot — and an alert is recorded either way; so is a
  *stale* one (it matches its record, but the look now compiles to another pattern or other effect codes), once per
  compiled content. A slot that already holds exactly the current content is recorded, not written. A drifted T2 slot
  (hand-painted in the app, origin "app") is only alerted. Alerts are recorded once per drifted content.

Nothing here writes directly: sends go through the engine (`CanopyEngine.send` / `rewrite_slot`), which submits to
the Target's executor.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from typing import TYPE_CHECKING, Any

from conductor import rows as R
from conductor.drivers.base import StateRow
from conductor.engine_api import Tier
from conductor.executor_api import CommandStatus, DeviceStatus
from conductor.gate import Purpose
from conductor.state import AUTH_REASON, OFFLINE_REASON

if TYPE_CHECKING:
    from conductor.canopy import CanopyEngine

log = logging.getLogger(__name__)

TICK_S = 0.5
TRUE_UP_CONCURRENCY = 3  # readbacks in flight at once during a true-up (the executor's burst keeps the rest)
READ_RETRY_S = 1.0  # a failed rolling true-up read is tried again this soon, once
# A rolling read checks a module's slot patterns when any of them changed since the last check, and at least this
# often otherwise (the stale check follows calibration/design edits; hashing three framebuffers every 5 s is waste).
SLOT_CHECK_INTERVAL_S = 60.0
# A stepped look's module that differs waits for the next step unless its write failed and that step is further off.
STEP_DEFER_S = 2.0


class Reconciler:
    def __init__(self, engine: CanopyEngine) -> None:
        self.engine = engine
        self.clock = engine.clock
        self._task: asyncio.Task | None = None
        self._reassert_at: float | None = None
        self._next_true_up: float | None = None
        self._next_probe: float | None = None
        self._probe_index = 0
        # address → first seen wrong in a way a tier change could fix (hysteresis)
        self._wrong_since: dict[str, float] = {}
        self._attention_since: dict[str, float] = {}  # address → first seen NEEDS_ATTENTION (any reason)
        self._throttle_failures: deque[tuple[float, str]] = deque()
        self._clean_since = self.clock.monotonic()
        self._no_downgrade_alerted: set[tuple[str, str]] = set()
        self._drift_alerted: dict[tuple[str, str], str] = {}
        self._rewrites: set[asyncio.Task] = set()
        # rolling true-up: device id → monotonic time its next read is due; reads in flight; failed reads in a row
        self._read_due: dict[str, float] = {}
        self._reading: set[str] = set()
        self._read_failures: dict[str, int] = {}
        self._reads: set[asyncio.Task] = set()
        # device id → (when its slots were last checked, {slot: readback value} then)
        self._slots_seen: dict[str, tuple[float, dict]] = {}
        self.stats: dict[str, int] = {
            "reasserts": 0,
            "true_ups": 0,
            "true_up_reads": 0,  # rolling true-up GETs (the read load; never counted against the write budget)
            "drift_resends": 0,  # modules re-sent because a true-up read showed they'd changed
            "probes": 0,
            "slot_rewrites": 0,
            "drift": 0,
            "slot_checks": 0,  # modules whose slot patterns were compared with their records
        }

    @property
    def settings(self) -> Any:
        return self.engine.deps.settings.reconciler

    # ---- lifecycle --------------------------------------------------------------------------------------------
    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        now = self.clock.monotonic()
        # A full pass only on request (on_driver_recovery); the rolling reads do the rest.
        self._next_true_up = None
        self._read_due.clear()
        self._clean_since = now
        self._task = asyncio.get_running_loop().create_task(self._run(), name="canopy:reconciler")

    async def stop(self) -> None:
        tasks = [t for t in (self._task, *self._rewrites, *self._reads) if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._task = None

    async def _run(self) -> None:
        while True:
            await self.clock.sleep(TICK_S)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("reconciler tick failed")

    # ---- hooks from the engine --------------------------------------------------------------------------------
    def on_new_plan(self) -> None:
        self._wrong_since.clear()
        self._attention_since.clear()
        self.engine.dirty.update(t.address for _, t in self.engine.active_modules())
        self.schedule_reassert()

    def schedule_reassert(self, delay: float | None = None) -> None:
        at = self.clock.monotonic() + (self.settings.reassert_after_s if delay is None else delay)
        if self._reassert_at is None or at < self._reassert_at:
            self._reassert_at = at

    def note_throttle_failure(self, account: str) -> None:
        now = self.clock.monotonic()
        self._throttle_failures.append((now, account))
        self._clean_since = now

    def on_outage(self) -> None:
        self._wrong_since.clear()
        self._attention_since.clear()
        self._reassert_at = None
        self._next_probe = self.clock.monotonic() + self.settings.outage_probe_interval_s

    def on_recovery(self) -> None:
        now = self.clock.monotonic()
        self._wrong_since.clear()
        self._attention_since.clear()
        self._throttle_failures.clear()
        self._clean_since = now
        self._next_probe = None
        self._next_true_up = None
        self._read_due.clear()  # the look goes to every module now; the rolling reads start over, staggered
        self._read_failures.clear()

    def on_driver_recovery(self) -> None:
        """The driver's own watchdog saw Hubspace answer again: probe now (or true-up now when not resting)."""
        now = self.clock.monotonic()
        if self.engine.outage is not None:
            self._next_probe = now
        else:
            self._next_true_up = now

    def on_step(self, addresses: list[str]) -> None:
        """A stepped look's step was sent to these modules: a new desired state, so their wrong-timers start over."""
        for address in addresses:
            self._wrong_since.pop(address, None)
            self._attention_since.pop(address, None)

    def note_verification(self, device_id: str, target: Any, rows: list[StateRow]) -> None:
        """A moving stepped look's verification readback: check the module's slot patterns, at most once per
        `SLOT_CHECK_INTERVAL_S` (the rolling reads that usually do it stand down while it moves)."""
        now = self.clock.monotonic()
        seen = self._slots_seen.get(device_id)
        if seen is not None and now - seen[0] < SLOT_CHECK_INTERVAL_S:
            return
        slots = {r.function_instance: r.value for r in rows if r.function_class == R.FRAME_CLASS}
        self._slots_seen[device_id] = (now, slots)
        self.check_slots(device_id, target, rows)

    def wrong_too_long(self, address: str) -> bool:
        """Has this module needed attention (for any reason: wrong, offline, signed out) longer than the window?"""
        since = self._attention_since.get(address)
        return since is not None and self.clock.monotonic() - since > self.settings.downgrade_after_wrong_s

    # ---- the loop ---------------------------------------------------------------------------------------------
    async def tick(self) -> None:
        engine = self.engine
        if engine.stopped:
            return
        now = self.clock.monotonic()
        if engine.outage is not None:
            if self._next_probe is None or now >= self._next_probe:
                await self.probe()
            return
        if engine.commissioning or engine.current_plan() is None:
            await self._idle_hold(now)
            return
        self._check_locks()
        self._update_wrong(now)
        if await self._hysteresis(now):
            return
        if self._reassert_at is not None and now >= self._reassert_at:
            self._reassert_at = None
            await self.reassert()
        if self._next_true_up is not None and now >= self._next_true_up:
            await self.true_up()
        else:
            self._rolling_reads(now)
        # The idle hold first: its mood clears the guest settings, so the reset then has nothing left to do.
        await self._idle_hold(now)
        await self._idle_reset(now)

    # ---- re-assert --------------------------------------------------------------------------------------------
    async def reassert(self, *, source: str = "reconciler") -> list[str]:
        """Re-send the full state vector to the modules whose readback differs (motion: all or none)."""
        engine = self.engine
        plan = engine.current_plan()
        if plan is None or engine.outage is not None or engine.commissioning:
            return []
        needs, busy, active = [], [], engine.active_modules()
        runner = engine.runner if engine.stepping else None
        for device_id, target in active:
            if target.executor.in_flight(target.address):
                busy.append(device_id)
                continue
            if engine.wave_pending(target.address):
                continue  # its ring of the ripple sends it
            if self._differs(device_id, target):
                if runner is not None and not self._resend_before_step(target.address, runner):
                    runner.defer(device_id)  # the next step sends it (one write, not two)
                    continue
                needs.append(device_id)
        if not needs:
            return []
        if plan.motion:
            if busy:  # all or none, as ONE burst: wait until nothing is in flight
                self.schedule_reassert(1.0)
                return []
            needs = [device_id for device_id, _ in active]
        sent = engine.send(needs, source=source, label=f"re-assert: {plan.mode_id}")
        self.stats["reasserts"] += len(sent)
        return sent

    def _resend_before_step(self, address: str, runner: Any) -> bool:
        """A stepped look's module that differs: re-send it now only when its write failed outright and the next step
        is more than STEP_DEFER_S away; otherwise that step carries it."""
        wait = runner.next_step_in()
        failed = self.engine.last_status.get(address) in (CommandStatus.FAILED, CommandStatus.MISMATCH)
        return failed and (wait is None or wait > STEP_DEFER_S)

    def _readback_differs(self, device_id: str, target: Any) -> bool:
        """Does the latest readback differ from the look in a row a re-send can fix (not a slot object: drift)?"""
        engine = self.engine
        if not engine.tracker.mismatched([target.address]):
            return False
        snap = engine.tracker.snapshot(target.address)
        reported = [StateRow.from_wire(r) for r in snap["reported"] or []]
        return _rows_differ(engine.desired_rows(device_id), reported)

    def _differs(self, device_id: str, target: Any) -> bool:
        engine = self.engine
        address = target.address
        if engine.tracker.mismatched([address]):
            snap = engine.tracker.snapshot(address)
            reported = [StateRow.from_wire(r) for r in snap["reported"] or []]
            return _rows_differ(engine.desired_rows(device_id), reported)
        if engine.program_unsent(device_id):
            return True  # a stepped run ended (its still look desired) while nothing could be sent
        return engine.last_status.get(address) in (CommandStatus.FAILED, CommandStatus.MISMATCH)

    # ---- true-up ----------------------------------------------------------------------------------------------
    def _rolling_reads(self, now: float) -> None:
        """Start the true-up reads that are due: each module once per `true_up_interval_s`, staggered."""
        if self.engine.stepping:
            # each step's write is verified: a read now would only race the next step. After the run the reads
            # start over, staggered.
            self._read_due.clear()
            return
        interval = self.settings.true_up_interval_s
        active = self.engine.active_modules()
        ids = [device_id for device_id, _ in active]
        for gone in [d for d in self._read_due if d not in ids]:
            del self._read_due[gone]
            self._read_failures.pop(gone, None)
        for index, device_id in enumerate(ids):
            if device_id not in self._read_due:  # new (start, a new plan's module, a released lock): stagger
                self._read_due[device_id] = now + interval * (index + 1) / len(ids)
        for device_id, target in active:
            if len(self._reading) >= TRUE_UP_CONCURRENCY:
                return
            due = self._read_due[device_id]
            if due > now or device_id in self._reading:
                continue
            following = due + interval
            self._read_due[device_id] = following if following > now else now + interval
            if target.executor.in_flight(target.address):
                continue  # its own verification readback answers
            self._reading.add(device_id)
            task = asyncio.get_running_loop().create_task(
                self._read_one(device_id, target), name=f"canopy:true-up:{device_id}"
            )
            self._reads.add(task)
            task.add_done_callback(self._reads.discard)

    async def _read_one(self, device_id: str, target: Any) -> None:
        engine = self.engine
        try:
            self.stats["true_up_reads"] += 1
            rr = await target.driver.read_state(target.address, purpose=Purpose.ENGINE)
            engine.tracker.note_readback(target.address, rr)
            if not rr.ok:
                failures = self._read_failures[device_id] = self._read_failures.get(device_id, 0) + 1
                if failures == 1 and device_id in self._read_due:  # one quick retry, then its normal turn
                    self._read_due[device_id] = min(
                        self._read_due[device_id], self.clock.monotonic() + READ_RETRY_S
                    )
                return
            self._read_failures.pop(device_id, None)
            slots = {r.function_instance: r.value for r in rr.rows if r.function_class == R.FRAME_CLASS}
            seen = self._slots_seen.get(device_id)
            now = self.clock.monotonic()
            if seen is None or seen[1] != slots or now - seen[0] >= SLOT_CHECK_INTERVAL_S:
                self._slots_seen[device_id] = (now, slots)
                self.check_slots(device_id, target, rr.rows)
            plan = engine.current_plan()
            if (
                plan is None
                or device_id not in plan.modules
                or engine.outage is not None
                or engine.commissioning
                or engine.stopped
                or engine.locks.holder(target.address) is not None
                or target.executor.in_flight(target.address)
                or engine.wave_pending(target.address)  # its ring of the ripple sends it
            ):
                return
            if not self._readback_differs(device_id, target):
                # This fresh readback shows the look: an older command's FAILED/MISMATCH no longer means "re-send".
                if engine.last_status.get(target.address) in (CommandStatus.FAILED, CommandStatus.MISMATCH):
                    engine.last_status.pop(target.address, None)
                return
            self.stats["drift_resends"] += 1
            if plan.motion:
                self.schedule_reassert(0.0)  # all nine as one burst, from the loop
                return
            sent = engine.send([device_id], source="reconciler", label=f"re-assert (true-up): {plan.mode_id}")
            self.stats["reasserts"] += len(sent)
        finally:
            self._reading.discard(device_id)

    async def true_up(self) -> None:
        """Read every module now (the driver's recovery watchdog asks for it), then re-assert what differs."""
        engine = self.engine
        self._next_true_up = None
        self.stats["true_ups"] += 1
        sem = asyncio.Semaphore(TRUE_UP_CONCURRENCY)

        async def one(device_id: str, target: Any) -> None:
            async with sem:
                if target.executor.in_flight(target.address):
                    return  # its own verification readback answers
                rr = await target.driver.read_state(target.address, purpose=Purpose.ENGINE)
            engine.tracker.note_readback(target.address, rr)
            if rr.ok:
                self.check_slots(device_id, target, rr.rows)

        await asyncio.gather(*(one(d, t) for d, t in engine.active_modules()))
        if engine.outage is None:
            await self.reassert()

    def check_module_soon(self, device_id: str, target: Any) -> None:
        """Read one module now (outside the true-up) and check its slots — after a verification whose last
        readback failed, so drift doesn't wait a whole true-up interval."""

        async def check() -> None:
            if target.executor.in_flight(target.address):
                return
            rr = await target.driver.read_state(target.address, purpose=Purpose.ENGINE)
            self.engine.tracker.note_readback(target.address, rr)
            if rr.ok:
                self.check_slots(device_id, target, rr.rows)

        task = asyncio.get_running_loop().create_task(check(), name=f"canopy:check:{device_id}")
        self._rewrites.add(task)
        task.add_done_callback(self._rewrites.discard)

    # ---- outage probe -----------------------------------------------------------------------------------------
    async def probe(self) -> bool:
        """One readback of one module (rotating). True (and the engine re-applies the look) when it answers."""
        engine = self.engine
        self._next_probe = self.clock.monotonic() + self.settings.outage_probe_interval_s
        active = engine.active_modules()
        if not active:
            return False
        device_id, target = active[self._probe_index % len(active)]
        self._probe_index += 1
        self.stats["probes"] += 1
        rr = await target.driver.read_state(target.address, purpose=Purpose.ENGINE)
        engine.tracker.note_readback(target.address, rr)
        if rr.ok:
            # An idle reset that fell due during the outage goes out with the recovery burst, not right after it.
            await self._idle_reset(self.clock.monotonic(), recovering=True)
            engine.exit_outage(f"{device_id} answered a readback")
            return True
        return False

    # ---- locks ------------------------------------------------------------------------------------------------
    def _check_locks(self) -> None:
        engine = self.engine
        plan = engine.current_plan()
        released = []
        for device_id in plan.modules:
            target = engine.target_of(device_id)
            if target is None:
                continue
            locked = engine.locks.holder(target.address) is not None
            if locked:
                engine._locked.add(target.address)
            elif target.address in engine._locked:
                engine._locked.discard(target.address)
                if not engine.wave_pending(target.address):  # else its ring of the ripple sends it
                    released.append(device_id)
        if not released:
            return
        if plan.motion:
            engine.send(None, source="reconciler", label=f"lock released: {plan.mode_id}")
        else:
            engine.send(released, source="reconciler", label=f"lock released: {plan.mode_id}")

    # ---- hysteresis -------------------------------------------------------------------------------------------
    def _update_wrong(self, now: float) -> None:
        engine = self.engine
        active = {t.address for _, t in engine.active_modules()}
        for timers in (self._wrong_since, self._attention_since):
            for address in [a for a in timers if a not in active]:
                del timers[address]
        # Only modules with news (a command, attempt or readback since the last look) can have changed status.
        changed, engine.dirty = engine.dirty, set()
        for address in active & (changed | set(self._attention_since)):
            status = engine.tracker.status(address)
            if status is DeviceStatus.READY:
                self._wrong_since.pop(address, None)
                self._attention_since.pop(address, None)
            elif status is DeviceStatus.NEEDS_ATTENTION:
                self._attention_since.setdefault(address, now)
                if address not in self._wrong_since and self._tier_relevant(address):
                    self._wrong_since[address] = now
                    self._clean_since = now

    def _tier_relevant(self, address: str) -> bool:
        """Could a different tier fix this? Not when the module is offline, signed out, flagged or gate-refused."""
        snap = self.engine.tracker.snapshot(address)
        if snap["available"] is False or snap["problems"]:
            return False
        if snap["reason"] in (AUTH_REASON, OFFLINE_REASON):
            return False
        return self.engine.last_status.get(address) not in (CommandStatus.DENIED, CommandStatus.LOCKED)

    async def _hysteresis(self, now: float) -> bool:
        """Downgrade or upgrade the current mode's tier. True when the look was changed."""
        s = self.settings
        engine = self.engine
        plan = engine.current_plan()
        while self._throttle_failures and now - self._throttle_failures[0][0] > s.downgrade_throttle_window_s:
            self._throttle_failures.popleft()
        reason = None
        stuck = [a for a in self._wrong_since if now - self._wrong_since[a] > s.downgrade_after_wrong_s]
        if stuck:
            reason = f"a module stayed wrong for more than {s.downgrade_after_wrong_s:g} s after re-asserts"
        else:
            per_account: dict[str, int] = {}
            for _, account in self._throttle_failures:
                per_account[account] = per_account.get(account, 0) + 1
            hot = [a for a, n in per_account.items() if n >= s.downgrade_throttles]
            if hot:
                reason = (
                    f"{per_account[hot[0]]} commands on account {hot[0]} were still throttled after retries within "
                    f"{s.downgrade_throttle_window_s:g} s"
                )
        if reason is not None:
            key = (plan.mode_id, str(plan.tier))
            if engine.downgrade(reason):
                self._wrong_since.clear()
                self._attention_since.clear()
                self._throttle_failures.clear()
                self._clean_since = now
                return True
            if key not in self._no_downgrade_alerted:
                self._no_downgrade_alerted.add(key)
                engine.telemetry.record(
                    "alert",
                    level="warn",
                    message=f"{plan.label} has no lower tier to fall back to ({reason}); still re-asserting.",
                )
            return False
        self._no_downgrade_alerted.clear()
        if engine.floors and now - self._clean_since >= s.upgrade_after_clean_s:
            self._clean_since = now
            before = plan.tier
            engine.upgrade(f"{s.upgrade_after_clean_s:g} s without a wrong module or a throttled command")
            after = engine.current_plan()
            return after is not None and after.tier != before
        return False

    # ---- idle hold --------------------------------------------------------------------------------------------
    async def _idle_hold(self, now: float) -> None:
        engine = self.engine
        kiosk = engine.deps.settings.kiosk
        if engine.paused or engine.outage is not None or engine.commissioning or engine.stopped:
            return
        if engine.rehearsal_active:  # the stepped rehearsal (Gate R) runs its full minutes
            return
        plan = engine.current_plan()
        if plan is not None and plan.mode_id == kiosk.idle_mode:
            return
        if now - engine.last_change_mono < kiosk.idle_return_s:
            return
        engine.last_change_mono = now  # one attempt per idle period, even if it's refused
        state = await engine.request_mode(kiosk.idle_mode, source="idle")
        if state.refused:
            engine.telemetry.record("alert", level="warn", message=f"Idle return refused: {state.message}")

    async def _idle_reset(self, now: float, *, recovering: bool = False) -> None:
        """No guest input for `kiosk.idle_reset_s` while a guest setting is in force: clear the guest's
        brightness/power (and show `idle_scene`). Nothing else — a mood chosen since (by ops or anyone) already
        cleared them, so it is never undone. `recovering`: called just before an outage ends, so the change goes
        out with the recovery burst instead of a second burst right after it."""
        engine = self.engine
        kiosk = engine.deps.settings.kiosk
        if engine.paused or engine.commissioning or engine.stopped:
            return
        if engine.outage is not None and not recovering:
            return
        if not engine.guest_overrides_active or now - engine.last_guest_input_mono < kiosk.idle_reset_s:
            return
        engine.last_guest_input_mono = now  # one attempt per idle period, even if it's refused
        scene = kiosk.idle_scene
        plan = engine.current_plan()
        if scene is not None and (plan is None or plan.mode_id != scene):  # choosing a mood clears them too
            state = await engine.request_mode(scene, source="idle")
            if not state.refused:
                return
            engine.telemetry.record(
                "alert", level="warn", message=f"Idle reset to {scene} refused: {state.message}"
            )
        state = await engine.clear_guest_overrides(source="idle")
        if state.refused:
            engine.telemetry.record("alert", level="warn", message=f"Idle reset refused: {state.message}")

    # ---- slots ------------------------------------------------------------------------------------------------
    def check_slots(self, device_id: str, target: Any, rows: list[StateRow]) -> None:
        """Compare each recorded slot's readback with its record: the canonical hash (07 §6) and, separately, the
        effect fields that make it animate (the hash leaves them out). A commissioned (T1) slot is rewritten when it
        drifted — or when it still matches its record but the look now compiles to something else (stale: a
        recalibration, a design edit, an effect code enabled or disabled) — at most once per
        `slot_rewrite_min_interval_s`; a hand-painted (T2) slot is only alerted."""
        engine = self.engine
        self.stats["slot_checks"] += 1
        got_rows = {r.key: r.value for r in rows}
        for slot, expected, tier, _mode in engine.slot_expectations(device_id):
            if (device_id, slot) in engine.repaint_pending:  # an operator repaint writes it (D68)
                continue
            key = (device_id, slot)
            value = got_rows.get((R.FRAME_CLASS, slot))
            digest = R.slot_hash(value)
            if digest is None:
                self._drift_alerted.pop(key, None)
                continue
            record = engine.recorded_slot(device_id, slot)
            want_effects = record["effects"] if record is not None and record["hash"] == expected else None
            fields = R.effects_differ(want_effects, R.slot_effects(value))
            label = engine._entry(device_id).label if engine._entry(device_id) else device_id
            painted = tier is not Tier.T1 and (record is None or record["origin"] == "app")
            kind = "hand-painted" if painted else "saved"
            if digest == expected and not fields:
                stale = engine.stale_slot(device_id, slot, value) if tier is Tier.T1 else None
                if stale is None:
                    self._drift_alerted.pop(key, None)
                    continue
                why, content = stale
                token = f"stale:{content}"
                if self._drift_alerted.get(key) == token:
                    continue  # one rewrite per compiled content, never a loop (07 §6)
                what = f"the saved pattern in {slot} is out of date: {why}"
            else:
                token = digest + (":" + ",".join(fields) if fields else "")
                if self._drift_alerted.get(key) == token and tier is not Tier.T1:
                    continue
                what = (
                    f"the {kind} pattern in {slot} changed (hash {digest}, expected {expected})"
                    if digest != expected
                    else f"the effect fields of {slot} changed ({', '.join(fields)} differ from what was recorded)"
                )
            self.stats["drift"] += 1
            if tier is not Tier.T1:
                self._drift_alerted[key] = token
                engine.telemetry.record(
                    "alert",
                    level="warn",
                    device_id=device_id,
                    message=f"{label}: {what}. "
                    + (
                        "It was hand-painted in the Hubspace app, so the Conductor leaves it alone: repaint it in "
                        "the app, or record it again on the module page's Slots tab."
                        if painted
                        else "No canvas mode owns it here, so it's left alone."
                    ),
                )
                continue
            if engine.adopt_slot(device_id, slot, value):
                # It already holds what the look compiles to now (e.g. a rewrite whose verification a newer
                # command superseded): recorded from this readback, nothing written.
                self._drift_alerted.pop(key, None)
                engine.telemetry.record(
                    "alert",
                    level="info",
                    device_id=device_id,
                    message=f"{label}: {slot} already holds the current pattern; recorded its hash (nothing sent).",
                )
                continue
            last = engine.slot_written.get(key)
            min_interval = self.settings.slot_rewrite_min_interval_s
            if last is not None and self.clock.monotonic() - last < min_interval:
                if self._drift_alerted.get(key) != "waiting:" + token:
                    self._drift_alerted[key] = "waiting:" + token
                    engine.telemetry.record(
                        "alert",
                        level="warn",
                        device_id=device_id,
                        message=f"{label}: {what}, but it was written less than {round(min_interval / 60)} min "
                        "ago, so it's left alone until then.",
                    )
                continue
            self._drift_alerted[key] = token
            self.stats["slot_rewrites"] += 1
            engine.slot_written[key] = self.clock.monotonic()  # the limit counts from the attempt
            task = asyncio.get_running_loop().create_task(
                self._rewrite(device_id, slot, label, what), name=f"canopy:rewrite:{device_id}:{slot}"
            )
            self._rewrites.add(task)
            task.add_done_callback(self._rewrites.discard)

    async def _rewrite(self, device_id: str, slot: str, label: str, what: str = "") -> None:
        status = await self.engine.rewrite_slot(device_id, slot, source="reconciler")
        what = what or f"the saved pattern in {slot} had drifted"
        if status == str(CommandStatus.APPLIED):
            if not self._drift_alerted.get((device_id, slot), "").startswith("stale:"):
                self._drift_alerted.pop((device_id, slot), None)  # a new drift after this is news again
            message = f"{label}: {what}; rewrote it (applied)."
        else:
            message = f"{label}: {what}; the rewrite didn't take: {status}."
        self.engine.telemetry.record("alert", level="warn", device_id=device_id, message=message)


def _rows_differ(expected: list[StateRow], reported: list[StateRow]) -> bool:
    """A re-send can fix any differing row except a slot object (that is drift, handled by the slot check)."""
    return any(m.function_class != R.FRAME_CLASS for m in R.compare(expected, reported))
