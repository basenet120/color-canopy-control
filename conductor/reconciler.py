"""The reconciler: keeps the canopy showing the current mode (05 #6–#7, 07 §5–§6).

One loop on the injected Clock (a tick every `TICK_S`), owned by the CanopyEngine (`conductor/canopy.py`):

- **Re-assert.** `reassert_after_s` after each change, after any command that didn't verify, and on every true-up,
  re-send the FULL state vector only to modules whose readback differs from desired (`tracker.mismatched`, or whose
  last command FAILED / MISMATCHed without a readback). Motion plans (`plan.motion`) re-trigger every module as one
  burst or none — and only when no module has a command in flight, so it really is one burst. A slot *object* that
  differs is drift, not something a re-send fixes (see Slots).
- **True-up** every `true_up_interval_s`: read each non-locked module's state (REST, `tracker.note_readback`), check
  its slot hashes, then re-assert what differs.
- **Hysteresis (07 §6).** Retry first. A module "wrong" (tracker NEEDS_ATTENTION for a reason a tier change could
  fix — not offline, not a dead sign-in, not a live-gate refusal) longer than `downgrade_after_wrong_s` downgrades
  the current mode one rung. So do `downgrade_throttles` **commands that still ended throttled after the executor's
  retries** within `downgrade_throttle_window_s` on one account. Individual 429s that a retry cleared never count —
  that is what keeps 5% random throttling from ever downgrading. Floors lift one rung after `upgrade_after_clean_s`
  with no wrong module and no throttled command.
- **Outage.** While the engine rests, only the probe runs: one module's readback every `outage_probe_interval_s`
  (rotating through the modules); the first good one ends the outage and the engine re-applies the look.
- **Locks.** A module whose lock was released gets the current look again (all nine for a motion plan).
- **Idle hold.** After `kiosk.idle_return_s` with no accepted change (and guest input not paused), request
  `kiosk.idle_mode` with source "idle".
- **Slots.** During a true-up each module's slot objects are hashed (canonical projection of the readback, 07 §6)
  and compared with the recorded hash. A drifted T1 slot is rewritten — at most once per
  `slot_rewrite_min_interval_s` per slot — and an alert is recorded either way; a drifted T2 slot (hand-painted in the
  app) is only alerted. Alerts are recorded once per drifted content, not every minute.

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
        self.stats: dict[str, int] = {
            "reasserts": 0,
            "true_ups": 0,
            "probes": 0,
            "slot_rewrites": 0,
            "drift": 0,
        }

    @property
    def settings(self) -> Any:
        return self.engine.deps.settings.reconciler

    # ---- lifecycle --------------------------------------------------------------------------------------------
    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        now = self.clock.monotonic()
        self._next_true_up = now + self.settings.true_up_interval_s
        self._clean_since = now
        self._task = asyncio.get_running_loop().create_task(self._run(), name="canopy:reconciler")

    async def stop(self) -> None:
        tasks = [t for t in (self._task, *self._rewrites) if t is not None]
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
        self._next_true_up = now + self.settings.true_up_interval_s

    def on_driver_recovery(self) -> None:
        """The driver's own watchdog saw Hubspace answer again: probe now (or true-up now when not resting)."""
        now = self.clock.monotonic()
        if self.engine.outage is not None:
            self._next_probe = now
        else:
            self._next_true_up = now

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
        await self._idle_hold(now)

    # ---- re-assert --------------------------------------------------------------------------------------------
    async def reassert(self, *, source: str = "reconciler") -> list[str]:
        """Re-send the full state vector to the modules whose readback differs (motion: all or none)."""
        engine = self.engine
        plan = engine.current_plan()
        if plan is None or engine.outage is not None or engine.commissioning:
            return []
        needs, busy, active = [], [], engine.active_modules()
        for device_id, target in active:
            if target.executor.in_flight(target.address):
                busy.append(device_id)
                continue
            if self._differs(device_id, target):
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

    def _differs(self, device_id: str, target: Any) -> bool:
        engine = self.engine
        plan = engine.current_plan()
        address = target.address
        if engine.tracker.mismatched([address]):
            snap = engine.tracker.snapshot(address)
            reported = [StateRow.from_wire(r) for r in snap["reported"] or []]
            return _rows_differ(list(plan.modules[device_id].rows), reported)
        return engine.last_status.get(address) in (CommandStatus.FAILED, CommandStatus.MISMATCH)

    # ---- true-up ----------------------------------------------------------------------------------------------
    async def true_up(self) -> None:
        engine = self.engine
        self._next_true_up = self.clock.monotonic() + self.settings.true_up_interval_s
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
        plan = engine.current_plan()
        if plan is not None and plan.mode_id == kiosk.idle_mode:
            return
        if now - engine.last_change_mono < kiosk.idle_return_s:
            return
        engine.last_change_mono = now  # one attempt per idle period, even if it's refused
        state = await engine.request_mode(kiosk.idle_mode, source="idle")
        if state.refused:
            engine.telemetry.record("alert", level="warn", message=f"Idle return refused: {state.message}")

    # ---- slots ------------------------------------------------------------------------------------------------
    def check_slots(self, device_id: str, target: Any, rows: list[StateRow]) -> None:
        engine = self.engine
        got_rows = {r.key: r.value for r in rows}
        for slot, expected, tier, _mode in engine.slot_expectations(device_id):
            key = (device_id, slot)
            value = got_rows.get((R.FRAME_CLASS, slot))
            digest = R.slot_hash(value)
            if digest is None or digest == expected:
                self._drift_alerted.pop(key, None)
                continue
            if self._drift_alerted.get(key) == digest and tier is not Tier.T1:
                continue
            self.stats["drift"] += 1
            label = engine._entry(device_id).label if engine._entry(device_id) else device_id
            if tier is not Tier.T1:
                self._drift_alerted[key] = digest
                engine.telemetry.record(
                    "alert",
                    level="warn",
                    device_id=device_id,
                    message=f"{label}: the hand-painted pattern in {slot} changed (hash {digest}, expected "
                    f"{expected}). Repaint it in the Hubspace app, or record the new hash on /ops.",
                )
                continue
            last = engine.slot_written.get(key)
            min_interval = self.settings.slot_rewrite_min_interval_s
            if last is not None and self.clock.monotonic() - last < min_interval:
                if self._drift_alerted.get(key) != digest:
                    self._drift_alerted[key] = digest
                    engine.telemetry.record(
                        "alert",
                        level="warn",
                        device_id=device_id,
                        message=f"{label}: the saved pattern in {slot} changed (hash {digest}), but it was written "
                        f"less than {round(min_interval / 60)} min ago, so it's left alone until then.",
                    )
                continue
            self._drift_alerted[key] = digest
            self.stats["slot_rewrites"] += 1
            engine.slot_written[key] = self.clock.monotonic()  # the limit counts from the attempt
            task = asyncio.get_running_loop().create_task(
                self._rewrite(device_id, slot, label), name=f"canopy:rewrite:{device_id}:{slot}"
            )
            self._rewrites.add(task)
            task.add_done_callback(self._rewrites.discard)

    async def _rewrite(self, device_id: str, slot: str, label: str) -> None:
        status = await self.engine.rewrite_slot(device_id, slot, source="reconciler")
        if status == str(CommandStatus.APPLIED):
            self._drift_alerted.pop((device_id, slot), None)  # a new drift after this is news again
            message = f"{label}: the saved pattern in {slot} had drifted; rewrote it (applied)."
        else:
            message = f"{label}: the saved pattern in {slot} had drifted; the rewrite didn't take: {status}."
        self.engine.telemetry.record("alert", level="warn", device_id=device_id, message=message)


def _rows_differ(expected: list[StateRow], reported: list[StateRow]) -> bool:
    """A re-send can fix any differing row except a slot object (that is drift, handled by the slot check)."""
    return any(m.function_class != R.FRAME_CLASS for m in R.compare(expected, reported))
