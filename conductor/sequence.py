"""The program runner: a stepped look (Tier.STEPPED, D65) sent a step at a time (sim-first; live off until Gate R).

    compile_mode(.., stepped) ──▶ ModePlan{modules: the hold (step 0), program.steps}
                                        │ installed by the CanopyEngine
                                        ▼
    ProgramRunner (one Clock task per installed stepped plan) ──step n──▶ engine.send(changed modules, "program")

Rules (binding text: `config/conductor.yaml` → motion.stepped, 07 §6):
- **Schedule.** Step 0 is the hold, sent by the mode change itself. Step n is due at `t0 + n × step` on the injected
  clock, t0 = when the look was last chosen (`engine.last_change_mono` as a mood choice set it; a guest's
  brightness or on/off doesn't move it) — so a re-pick of the look now showing (a tier
  change, new capabilities) keeps the run's phase, and a re-pick after the run is over only holds. The schedule is
  absolute: a step that comes due late is skipped (never queued) and the next one is the schedule's.
- **Step length.** step = max(the design's step_s, `min_step_s`, modules on one account × 60 / the planned rate,
  the ripple's span + `min_step_s`), the planned rate being `account_writes_per_min` (the account's cap on writes of
  any kind in any 60 s) less `budget_reserve` — the room 429 retries, silent-drop re-sends and a mood chosen mid-run
  need (`planned_writes_per_min`). Longer than `max_step_s`: the look holds still and an alert says why.
- **The cap.** A step must leave the account room for a guest's next burst (D75): it is held back when the writes
  still in the 60 s window it shares (the executor's own count, every write on the account, retries included, less
  those about to leave it: older than 60 s less the per-device write gap, one per device at most) plus the step's
  own plus one write per module of the plan on that account (a guest's mood or brightness, the share of
  `budget_reserve` a mood chosen mid-run needs) would pass `account_writes_per_min`. So the step itself never takes
  the account past the cap, and a run that starts while another run's steps are still in the window (a guest chose
  a mood mid-run, and since D75 that mood runs too) holds its first steps until the room is back. What the runner
  can't hold back — a guest's burst, 429 retries, re-asserts — lands in the rest of the reserve. A held step sends
  nothing, the step shown stays the last one sent — and is what the state draws (`to_state` repeats it: a held step
  is never drawn) — and the next step tries again (`budget_holds`). `HELD_STEPS_END` holds in a row end the run
  (D75): the look then holds still, with its still copy, rather than claim motion the account can't pay for.
- **A step** clears the engine's early re-assert counts and sends each driven module whose rows changed (its colour,
  its level, or a guest brightness folded into the step), plus the modules the reconciler deferred to it: one
  full-state PUT per module, never a slot object, through `engine.send(…, source="program")`. A breathe or shimmer
  step (D75) is the same: one PUT per changed module carrying its colour and its brightness together, and a module
  resting between breaths isn't sent anything. Nothing else: the runner never touches `last_change_mono` or
  `last_guest_input_mono`, so the idle hold and the idle reset count as they always do.
- **Held.** During an outage, commissioning, a stop or a guest-input pause the step index keeps advancing but
  nothing is sent; a guest who turned the canopy off gets no step writes either (the desired state is "off", so no
  step's rows differ from those sent, while the step itself stays current). On recovery, and when a guest turns the
  canopy back on, the current step is what goes out.
- **Folding.** A guest brightness change within `fold_guest_within_s` of the next step isn't sent on its own: the
  desired rows of every module now differ from those sent (the brightness row), so that step sends each one with
  the new level (one PUT each, no extra burst).
- **The end.** At the last step boundary within the design's run_s the run ends on the mode's best still tier
  (D75, `engine.settle_after_run`: its canvas or slot where that can run, one write per module that changes), else
  the hold is sent once (to the modules not already showing it), and the runner stops. The plan's `feature` (the
  still design's sentence) is the copy again. A choice runs once: a re-pick after its run builds the runner `over`
  (DONE at once, nothing sent), whatever its run_s; a choice whose runner couldn't run when it was installed (a
  cool-off, a step too long) counts as run. A run stopped by a 403 sends nothing (`finish(send=False)`): the
  step last sent stays, as `held_plan()`. A guest's input during the idle breath ends it without a write of its own
  (`yield_to_guest`): the guest's burst carries the still look.
- **Governor.** Every write attempt to the run's modules (from the executor's `attempt` events, superseded commands
  included — latest-wins would otherwise hide them) is counted per account over `governor_window_s`. At
  ≥ `governor_min_throttled` attempts answered 429/403 that are ≥ `governor_throttled_share` of the attempts, the run
  stops, the still look is sent, a warn alert is recorded and stepped runs on that account wait `governor_cooloff_s`
  (they hold still). It never rotates accounts or IPs. 503/504 are an outage (the engine rests), not throttling.
- **Phase.** While the run is moving a module reads UPDATING until its first verified write, READY while the writes
  sent to it verify, and NEEDS_ATTENTION when a write sent to it has gone unverified for max(2 steps, 10 s) — so
  each step's write doesn't flicker the look back to "changing".
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections import Counter, deque
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from conductor.drivers.base import Outcome
from conductor.engine_api import ModePlan, Program, Tier
from conductor.executor import WINDOW_S
from conductor.executor_api import DeviceStatus

if TYPE_CHECKING:
    from conductor.canopy import CanopyEngine

log = logging.getLogger(__name__)

GRID = [f"r{r}c{c}" for r in range(3) for c in range(3)]  # the order of CanopyState.program frames
THROTTLE_STATUSES = frozenset({429, 403})
NEEDS_ATTENTION_MIN_S = 10.0  # a write unverified this long (or 2 steps, if longer) needs attention
HELD_STEPS_END = 2  # D75: this many budget-held steps in a row end the run (the look holds still, still copy)
_EPS = 1e-6

RUNNING, DONE = "running", "done"


def planned_writes_per_min(settings: Any) -> float:
    """The write rate stepped runs plan for on one account: the cap less the reserve (`motion.stepped`)."""
    return settings.account_writes_per_min * (1.0 - settings.budget_reserve)


class ProgramRunner:
    """Steps one installed stepped plan (see the module docstring). Built and cancelled by the CanopyEngine."""

    def __init__(self, engine: CanopyEngine, plan: ModePlan, *, t0: float, over: bool = False) -> None:
        assert plan.program is not None
        self.engine = engine
        self.plan = plan
        self.program: Program = plan.program
        self.settings = engine.deps.settings.motion.stepped
        self.clock = engine.clock
        self.t0 = t0  # monotonic: when step 0 (the hold) was sent
        self.t0_wall = self.clock.now() - (self.clock.monotonic() - t0)
        self.cycle = len(self.program.steps)
        self.state = RUNNING
        self.ended: str | None = None  # why the run stopped (or never ran)
        self.shown: int | None = 0  # the step number whose rows are the desired state; None = the hold
        self.index = 0  # the step number the schedule is at
        self.deferred: set[str] = set()  # modules the reconciler left to the next step
        self.steps_sent = 0
        self.skipped = 0
        self.budget_holds = 0  # steps held back because they would have taken an account past the cap
        self.held_steps = 0  # budget holds in a row (a sent step resets it); HELD_STEPS_END end the run
        # the governor's window: (when, account, throttled) per write attempt to the run's modules
        self.attempts: deque[tuple[float, str, bool]] = deque()
        self._pending: dict[str, float] = {}  # device id → when the oldest write not yet verified was sent
        self._verified: set[str] = set()  # modules with a verified write since the plan was installed
        self._task: asyncio.Task | None = None
        self._swings: dict[str, tuple[int, int] | None] = {}  # device id → its breath's swing (`swing`)
        self._tripped = False
        self._unsubscribe: Any = None
        self._sleep = self.clock.sleep  # tests make a step late by wrapping it
        self.step_s, why = self._effective_step()
        self.count = self.count_for(self.step_s)  # the step number the hold goes out at
        if over:  # this choice already ran (D75): a re-pick only holds, whatever its run_s; no alert
            self.state, self.shown, self.ended = DONE, None, "this choice already ran (a re-pick only holds)"
            return
        if why is not None:
            self._never(why)
            return
        self.index = self._schedule_index()
        if self.index >= self.count:
            self.state, self.shown, self.ended = DONE, None, "the run was over before this look was installed"
            return
        self.shown = self.index

    # ---- set-up -----------------------------------------------------------------------------------------------
    def _accounts(self) -> Counter[str]:
        engine = self.engine
        counts: Counter[str] = Counter()
        for device_id in self.plan.modules:
            target = engine.target_of(device_id)
            if target is not None:
                counts[engine._account_of(target.address)] += 1
        return counts

    def _effective_step(self) -> tuple[float, str | None]:
        """The runner's step and, when the look can't step, why (it then holds still)."""
        s = self.settings
        accounts = self._accounts()
        busiest = max(accounts.values(), default=len(self.plan.modules))
        rings = self.engine._rings(self.plan)  # the ripple a mood choice would land with (D64)
        span = (len(rings) - 1) * self.engine.deps.settings.kiosk.ripple_stagger_s if rings else 0.0
        # the ripple's span is 0 without a ripple, so the last term is also the min_step_s floor
        planned = planned_writes_per_min(s)
        step = max(self.program.step_s, busiest * 60.0 / planned, span + s.min_step_s)
        step = math.ceil(step * 1000 - _EPS) / 1000  # whole milliseconds: 6.0, not 6.000000000001
        if step > s.max_step_s + _EPS:
            return step, (
                f"stepping {busiest} modules on one account at {planned:g} writes a minute (the account's "
                f"{s.account_writes_per_min:g} less a {s.budget_reserve:.0%} reserve for retries and re-sends) "
                f"needs {step:g} s steps, longer than motion.stepped.max_step_s ({s.max_step_s:g} s)"
            )
        cooling = {a: until for a, until in self.engine.stepped_cooloff.items() if a in accounts}
        until = max(cooling.values(), default=None)
        if until is not None and until > self.clock.monotonic():
            left = until - self.clock.monotonic()
            return step, (
                f"Hubspace throttled (429/403) a stepped run on account {', '.join(sorted(cooling))}; stepped looks "
                f"there hold still for another {math.ceil(left)} s (the throttle governor's cool-off)"
            )
        if self.count_for(step) < 1:
            return step, f"its run ({self.program.run_s:g} s) is shorter than one {step:g} s step"
        return step, None

    def count_for(self, step: float) -> int:
        return int(self.program.run_s / step + _EPS)

    def _never(self, why: str) -> None:
        self.state, self.shown, self.ended = DONE, None, why
        self.engine.telemetry.record(
            "alert",
            level="warn" if "governor" not in why else "info",
            message=f"{self.plan.label} holds its still design instead of stepping: {why}.",
            source="program",
        )

    def _schedule_index(self) -> int:
        return max(0, math.floor((self.clock.monotonic() - self.t0) / self.step_s + _EPS))

    def start(self) -> None:
        """Start the step task (a running look only; one that never ran or is over just holds)."""
        if self.state is not RUNNING or self._task is not None:
            return
        self._unsubscribe = self.engine.telemetry.subscribe(self._on_event)
        self.engine.telemetry.record(
            "program",
            action="start",
            mode=self.plan.mode_id,
            step_s=self.step_s,
            design_step_s=self.program.step_s,
            steps=self.count,
            run_s=round(self.count * self.step_s, 3),
            at_step=self.index,
        )
        self._task = asyncio.get_running_loop().create_task(
            self._run(), name=f"canopy:program:{self.plan.mode_id}"
        )

    def cancel(self) -> None:
        """Stop without sending anything (a newer look replaced this one, or the engine stops)."""
        if self.state is RUNNING:
            self.state, self.ended = DONE, self.ended or "replaced"
        self._stop_listening()
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _stop_listening(self) -> None:
        if self._unsubscribe is not None:
            with contextlib.suppress(Exception):
                self._unsubscribe()
            self._unsubscribe = None

    # ---- the loop ---------------------------------------------------------------------------------------------
    async def _run(self) -> None:
        try:
            n = self.index
            while True:
                n += 1
                if n >= self.count:
                    break
                await self._sleep_until(self.t0 + n * self.step_s)
                due = self._schedule_index()
                if due > n:  # late: the steps that fell due meanwhile are skipped, never queued
                    self.skipped += due - n
                    n = due
                    if n >= self.count:
                        break
                self.index = n
                self._step(n)
                if self.state is not RUNNING:  # the step ended the run (held steps) or something else did
                    return
            await self._sleep_until(self.t0 + self.count * self.step_s)
            self.index = self.count
            self.finish("the run is over")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("stepped program failed")
            self.finish("the program runner failed (see the logs)")

    async def _sleep_until(self, at: float) -> None:
        delay = at - self.clock.monotonic()
        if delay > 0:
            await self._sleep(delay)

    def _writes_held(self) -> bool:
        """No step writes now (the index still advances)."""
        engine = self.engine
        return engine.outage is not None or engine.commissioning or engine.stopped or engine.paused

    def _step(self, n: int) -> None:
        if self.state is not RUNNING or self._writes_held():
            return
        previous, self.shown = self.shown, n
        over = self._over_cap(self._wanted())
        # it would leave a guest no room under the cap: held back, the next one tries again
        if over is not None:
            self.shown = previous
            self.budget_holds += 1
            self.held_steps += 1
            self.engine.telemetry.record(
                "program",
                action="budget_hold",
                mode=self.plan.mode_id,
                step=n,
                **over,
                cap=self.settings.account_writes_per_min,
            )
            self.engine._publish_soon()  # the panel draws the step last sent, frozen (to_state)
            if self.held_steps >= HELD_STEPS_END:
                self.finish(f"the account's write budget held back {HELD_STEPS_END} steps in a row")
            return
        self._send(f"step {n} ({n % self.cycle + 1} of {self.cycle})")
        self.held_steps = 0

    def _wanted(self) -> list[str]:
        """The modules a send now writes: those whose desired rows differ from the rows sent, plus the deferred."""
        engine = self.engine
        return [
            d
            for d in self.plan.modules
            if d in self.deferred or engine.desired_rows(d) != engine.sent_rows.get(d)
        ]

    def _over_cap(self, wanted: list[str]) -> dict[str, Any] | None:
        """The hold's numbers ({account, writes_60s, counted, step_writes, room}) when sending `wanted` now would
        leave an account no room for a guest's next burst under `account_writes_per_min` (D75), else None.

        - `counted`: the writes still in the 60 s window this step's own share — the executor's count (every write
          on the account, retries included) less those older than 60 s less the per-device write gap
          (`executor.min_write_interval_s`), which leave the window before this step's writes are all out. That gap
          lets each device take one write at most, so what isn't counted is never more than one write per device.
        - `room`: one write per module of this plan on the account — what a guest's mood or brightness sends, the
          share of `budget_reserve` a mood chosen mid-run needs. The runner never spends it: a run that starts while
          another run's steps are still in the window holds its first steps until that room is back.
        - `writes_60s`: the account's whole 60 s count, as /ops shows it."""
        engine = self.engine
        per_account: dict[str, list[Any]] = {}
        for device_id in wanted:
            target = engine.target_of(device_id)
            if target is not None:
                per_account.setdefault(engine._account_of(target.address), []).append(target)
        cap = self.settings.account_writes_per_min
        rooms = self._accounts()
        for account, targets in sorted(per_account.items()):
            executor, address = targets[0].executor, targets[0].address
            gap = float(executor.policy.min_write_interval_s)
            counted = executor.account_writes(address, within_s=WINDOW_S - gap)
            room = rooms.get(account, len(targets))
            if counted + len(targets) + room > cap + _EPS:
                return {
                    "account": account,
                    "writes_60s": executor.account_writes(address),
                    "counted": counted,
                    "step_writes": len(targets),
                    "room": room,
                }
        return None

    def _send(self, what: str) -> list[str]:
        engine = self.engine
        engine._early_counts.clear()  # this step's silent drops get their early re-sends too
        wanted = self._wanted()
        self.deferred.clear()
        sent = engine.send(wanted, source="program", label=f"{what}: {self.plan.mode_id}") if wanted else []
        if sent:
            self.steps_sent += 1
            engine.reconciler.on_step([engine.target_of(d).address for d in sent if engine.target_of(d)])
        return sent

    def finish(self, why: str, *, alert: str | None = None, send: bool = True) -> None:
        """End the run: the mode's best still tier when the engine can settle onto it now (`settle_after_run`, D75),
        else the still look (the hold) is the desired state again, sent once to the modules not showing it.

        `send=False` (a 403: the venue IP may be blocked) sends nothing: the step last sent — a still, one colour per
        module look — stays the desired state, so nothing re-asserts either, until the next look replaces it."""
        if self.state is not RUNNING:
            return
        self.state, self.ended = DONE, why
        self._stop_listening()
        engine = self.engine
        engine.note_run_over(self)  # this choice has run: a re-pick only holds (D75)
        if not send:
            self.deferred.clear()
        else:
            self.shown = None
            if engine.outage is None and not engine.commissioning and not engine.stopped:
                if not engine.settle_after_run(self):
                    self._send("hold")
            else:
                engine.reconciler.schedule_reassert()  # it goes out once the engine can send again
        engine.telemetry.record(
            "program",
            action="end",
            mode=self.plan.mode_id,
            reason=why,
            steps_sent=self.steps_sent,
            skipped=self.skipped,
            budget_holds=self.budget_holds,
        )
        if alert:
            engine.telemetry.record("alert", level="warn", message=alert, source="program")
        engine._publish_soon()

    def held_plan(self) -> ModePlan | None:
        """A still plan of the step a stopped run holds (`finish(send=False)`): each module's rows, readback and
        colours as that step sent them, no program, so the kiosk draws what the canopy shows and nothing re-asserts.
        None while running, after the hold, or when the step held is step 0 (the plan's own look)."""
        if self.running or self.shown is None or self.shown % self.cycle == 0:
            return None
        step = self.program.steps[self.shown % self.cycle]
        modules = {}
        for device_id, mt in self.plan.modules.items():
            rows = step.rows.get(device_id)
            entry = self.engine._entry(device_id)
            colour = step.colours.get(str(entry.module)) if entry is not None and entry.module else None
            if rows is None or colour is None:
                modules[device_id] = mt
                continue
            rgb = {k: int(colour[1 + 2 * i : 3 + 2 * i], 16) for i, k in enumerate(("r", "g", "b"))}
            level = next(
                (r.value for r in rows if r.key == ("brightness", None) and isinstance(r.value, int)), 100
            )
            modules[device_id] = replace(
                mt,
                rows=list(rows),
                expect=list(rows),
                colors=[dict(rgb) for _ in mt.colors],
                preview=[{k: round(v * level / 100) for k, v in rgb.items()} for _ in mt.preview],
            )
        return replace(
            self.plan,
            tier=Tier.T3,  # a still module-level look, like the hold
            modules=modules,
            program=None,
            waves=None,
            notes=[*self.plan.notes, f"Held at step {self.shown} of a stepped run stopped by a 403"],
        )

    def yield_to_guest(self) -> None:
        """A guest's input during the idle breath (D75): the breath ends now and nothing is sent for it — the guest's
        own burst carries the still look with the guest's settings. The 'program end' event says why."""
        if self.state is not RUNNING:
            return
        self.state, self.ended, self.shown = DONE, "a guest changed the canopy", None
        self.deferred.clear()
        self._stop_listening()
        self.engine.note_run_over(self)
        self.engine.telemetry.record(
            "program",
            action="end",
            mode=self.plan.mode_id,
            reason=self.ended,
            steps_sent=self.steps_sent,
            skipped=self.skipped,
            budget_holds=self.budget_holds,
        )

    # ---- what the engine asks ---------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.state is RUNNING

    @property
    def tripped(self) -> bool:
        """Did the throttle governor stop this run?"""
        return self._tripped

    def resting(self, device_id: str) -> bool:
        """Is this module resting (D75: a breath's module between breaths)? The run is going, no write sent to it is
        unverified, the reconciler hasn't deferred it, and the next step (or the hold at the end) sends it the rows
        it has now — so a true-up read of it races nothing."""
        if not self.running or device_id in self._pending or device_id in self.deferred:
            return False
        now = self.rows_for(device_id)
        if now is None:
            now = self.plan.modules[device_id].rows if device_id in self.plan.modules else None
        n = self.index + 1
        upcoming = (
            self.plan.modules[device_id].rows
            if n >= self.count and device_id in self.plan.modules
            else self.program.steps[n % self.cycle].rows.get(device_id)
        )
        return now is not None and upcoming is not None and list(now) == list(upcoming)

    @property
    def moving(self) -> bool:
        """Is the canopy stepping now (running, its writes not held, the canopy on)?"""
        return self.running and not self._writes_held() and self.engine.guest_power

    def rows_for(self, device_id: str) -> list | None:
        """The rows the current step sends this module (None = the plan's own rows: the hold)."""
        if self.shown is None:
            return None
        return self.program.steps[self.shown % self.cycle].rows.get(device_id)

    def swing(self, device_id: str) -> tuple[int, int] | None:
        """How far this module's steps go below and above its hold's brightness (a breath's swing, D75), or None
        when no step changes its level (travel, a colour-only breath). The engine keeps a guest's level inside the
        device's range by moving the breath's centre, not by flattening its steps (`CanopyEngine._guest_rows`)."""
        if device_id not in self._swings:
            mt = self.plan.modules.get(device_id)
            hold = _level(mt.rows) if mt is not None else None
            offsets = [
                level - hold
                for step in self.program.steps
                if hold is not None and (level := _level(step.rows.get(device_id) or [])) is not None
            ]
            down, up = max(0, -min(offsets, default=0)), max(0, max(offsets, default=0))
            self._swings[device_id] = (down, up) if down or up else None
        return self._swings[device_id]

    def sync(self) -> None:
        """Recovery: the step the schedule is at becomes the desired state (the outage held it back)."""
        if self.running:
            self.shown = self.index

    def next_step_in(self) -> float | None:
        """Seconds until the next step (or the hold) is sent; None once the run is over."""
        if not self.running:
            return None
        n = min(self.index + 1, self.count)
        return max(0.0, self.t0 + n * self.step_s - self.clock.monotonic())

    def fold_guest(self) -> bool:
        """A guest brightness change: True when the next step is close enough to carry it (sent with it)."""
        # The step's rows then differ from those sent (the brightness row), so every module goes with it.
        wait = self.next_step_in()
        return wait is not None and self.moving and wait <= self.settings.fold_guest_within_s

    def defer(self, device_id: str) -> None:
        self.deferred.add(device_id)

    def note_sent(self, device_ids: list[str]) -> None:
        now = self.clock.monotonic()
        for device_id in device_ids:
            self._pending.setdefault(device_id, now)

    def note_verified(self, device_id: str) -> None:
        self._pending.pop(device_id, None)
        self._verified.add(device_id)

    def status(self, device_id: str) -> DeviceStatus:
        """The module's status while the run moves (see Phase in the module docstring)."""
        since = self._pending.get(device_id)
        if since is not None and self.clock.monotonic() - since > max(2 * self.step_s, NEEDS_ATTENTION_MIN_S):
            return DeviceStatus.NEEDS_ATTENTION
        if device_id not in self._verified:
            return DeviceStatus.UPDATING
        return DeviceStatus.READY

    def status_reason(self, device_id: str, status: DeviceStatus) -> str:
        if status is DeviceStatus.READY:
            return "Stepping: each step's write is verified."
        if status is DeviceStatus.UPDATING:
            return "Stepping: waiting for the first verified write."
        since = self._pending.get(device_id)
        age = self.clock.monotonic() - since if since is not None else 0.0
        return f"Stepping: a write sent {age:.0f} s ago hasn't been verified."

    def to_state(self) -> dict:
        """CanopyState.program: {t0, step_s, step, ends_at, frames, levels} (see engine_api.CanopyState). Frames
        after the step last sent, up to the schedule's step, repeat the step last sent: a held step is never drawn."""
        positions = self._positions()
        frames, levels = [], []
        for n in range(self.count + 1):
            k = self.shown if self.shown is not None and self.shown < n <= self.index else n
            step = self.program.steps[0 if k == self.count else k % self.cycle]
            frames.append([step.colours.get(pos) if pos in positions else None for pos in GRID])
            row: list[int | None] = []
            for pos in GRID:
                device_id = positions.get(pos)
                rows = step.rows.get(device_id) if device_id is not None else None
                if rows is None:
                    row.append(None)
                    continue
                # a step keeps its breath's swing around a guest's level; the last frame is the hold (the guest's own)
                swing = self.swing(device_id) if k != self.count else None
                row.append(self.engine.level_sent(device_id, rows, swing=swing))
            levels.append(row)
        return {
            "t0": self.t0_wall,
            "step_s": self.step_s,
            "step": self.index,
            "ends_at": self.t0_wall + self.count * self.step_s,
            "frames": frames,
            "levels": levels,
        }

    def _positions(self) -> dict[str, str]:
        """The canopy positions this plan drives → their device ids."""
        out: dict[str, str] = {}
        for device_id in self.plan.modules:
            entry = self.engine._entry(device_id)
            if entry is not None and entry.module:
                out[str(entry.module)] = device_id
        return out

    # ---- the governor -----------------------------------------------------------------------------------------
    def _on_event(self, event: dict) -> None:
        if event.get("kind") != "attempt" or self.state is not RUNNING or self._tripped:
            return
        engine = self.engine
        device_id = engine.device_of(str(event.get("device_id")))
        if device_id is None or device_id not in self.plan.modules:
            return
        now = self.clock.monotonic()
        account = engine._account_of(str(event.get("device_id")))
        throttled = (
            event.get("outcome") == str(Outcome.THROTTLED) and event.get("http_status") in THROTTLE_STATUSES
        )
        self.attempts.append((now, account, throttled))
        window = self.settings.governor_window_s
        while self.attempts and now - self.attempts[0][0] > window:
            self.attempts.popleft()
        if not throttled:
            return
        mine = [t for _, a, t in self.attempts if a == account]
        bad = sum(mine)
        if (
            bad < self.settings.governor_min_throttled
            or bad / len(mine) < self.settings.governor_throttled_share
        ):
            return
        engine.stepped_cooloff[account] = now + self.settings.governor_cooloff_s
        engine.stats["governor_trips"] = engine.stats.get("governor_trips", 0) + 1
        message = (
            f"{self.plan.label}: the stepped look stopped and holds still — {bad} of {len(mine)} write attempts on "
            f"account {account} in the last {window:g} s were throttled (429/403). Stepped looks there wait "
            f"{self.settings.governor_cooloff_s / 60:g} min."
        )
        engine.telemetry.record(
            "governor", account=account, mode=self.plan.mode_id, throttled=bad, attempts=len(mine)
        )

        def stop() -> None:  # not from inside the executor's attempt bookkeeping
            self.finish("the throttle governor stopped it", alert=message)

        self._tripped = True
        asyncio.get_running_loop().call_soon(stop)


def _level(rows: list) -> int | None:
    """The brightness row's level in `rows` (None without one)."""
    return next((r.value for r in rows if r.key == ("brightness", None) and isinstance(r.value, int)), None)
