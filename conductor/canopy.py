"""The canopy engine: guest/ops mode requests → one PUT per module, and an honest phase (05 #6–#8, 07 §5–§6).

    request_mode ──compile(mode, tier)──▶ ModePlan ──▶ tracker desired + executor.burst (one PUT per module)
                                                            │ results, readbacks (telemetry)
                                  Reconciler (reconciler.py) ◀┘ re-assert · true-up · hysteresis · outage · idle

Rules implemented here (binding text: `conductor/engine_api.py`, 07 §5–§6):
- **Requests.** A kiosk request is refused (a plain sentence + retry-after) during the cooldown
  (`kiosk.cooldown_s` after the last accepted kiosk change), while guest input is paused, while the canopy is
  resting (outage) and while commissioning runs. Ops, idle and reconciler requests bypass the cooldown and the
  pause. Refusals come back as a `CanopyState` with `refused=True` (never an exception).
- **Tier.** The first tier on the mode's ladder that `tier_allowed` permits and that compiles, starting at any
  hysteresis floor currently in force for that mode (the reconciler sets floors). If nothing at or below the floor
  compiles, tiers above it are tried before refusing. The kiosk copy is the compiled plan's `feature` sentence, so
  it always follows the tier in use.
- **Apply.** Every module's tracker desired state becomes the plan's `expect` rows, and exactly one Command per
  module (Purpose.ENGINE, verify=True, expect=target.expect) goes out through the executor's `burst`. The call
  returns at once (phase UPDATING); a background task awaits the results. Latest wins: a newer request supersedes
  the older one's pending commands (the executor coalesces per device).
- **Locks.** A module whose driver address is locked (bench job, /ops/module) is skipped and shown as `locked`, never
  as failed. When its lock is released the reconciler sends it the current look.
- **Phase.** RESTING while guest input is paused, during an outage, before the first mode and after stop();
  NEEDS_ATTENTION when a module has needed attention (wrong after re-asserts, offline, signed out) longer than
  `reconciler.downgrade_after_wrong_s`, can't be addressed, or every module is locked; UPDATING while any command is pending, verifying or
  waiting for a readback; READY when every non-locked module's readback matches (tracker READY).
- **Outage.** When `CONNECTION` / `TIMEOUT` / `SERVER_ERROR` outcomes hit at least `min(3, modules)` different modules
  with no success in between, the engine rests ("The canopy is resting…"); the reconciler probes one module every
  `outage_probe_interval_s` and, on the first good readback, re-applies the current mode to every module in one
  burst. Ops requests during an outage become the current mode and go out on recovery. A driver's optional
  `on_recovery` list (the Afero adapter's watchdog) triggers an immediate probe.
- **Early re-assert.** When a verification readback of the current command already differs from what was sent in a
  row a re-send can fix (the cloud stored nothing: a silent drop), the module is re-sent at once instead of after the
  executor's remaining readbacks — at most `EARLY_REASSERT_LIMIT` times per module per change. A motion plan
  re-triggers every module as one burst instead, once per change. A command that ends MISMATCH is re-asserted at the
  next reconciler tick; one that ends FAILED after `reassert_after_s`.
- **Commissioning.** `commission()` writes each T1 mode's slot content (the slot object only, one PUT per module,
  header copied from the device's own readback of that slot, verified on the slot row) and records the canonical
  hash of the readback per (device, slot) through `record_slot_hash`. A write that didn't land (silent drop,
  throttling past the executor's retries, a throttled header read) is retried within the same run, up to
  `COMMISSION_ROUNDS`; a slot that did land is written again at most once per `reconciler.slot_rewrite_min_interval_s`.
  Guest requests, the reconciler and idle hold wait while it runs.
- **Throttling.** Only a command that still ended THROTTLED after the executor's retries counts toward the
  hysteresis throttle downgrade (see reconciler.py) — a 429 that a retry cleared never does.

All timing comes from the injected Clock (safe on VirtualTimeLoop); nothing here touches the network except through a
Target's driver/executor.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from conductor import rows as R
from conductor.clock import Clock
from conductor.config import ConductorSettings, Registry
from conductor.drivers.base import Outcome
from conductor.engine_api import CanopyPhase, CanopyState, ModePlan, ModuleTarget, PlanError, Tier
from conductor.executor import CommandExecutor
from conductor.executor_api import Command, CommandResult, CommandStatus, DeviceStatus
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager
from conductor.reconciler import Reconciler
from conductor.state import DeviceStateTracker
from conductor.telemetry import Telemetry

log = logging.getLogger(__name__)

TOPIC = "canopy"
SAFE_MODE = "lowcountry-calm"
RESTING_MESSAGE = "The canopy is resting. It will be back shortly."
PAUSED_MESSAGE = "Guest input is paused by staff. The canopy is resting."
COMMISSIONING_MESSAGE = "The canopy is being set up. Try again in a minute."
STOPPED_MESSAGE = "The canopy engine is stopped."
STARTING_MESSAGE = "The canopy is getting ready."
OUTAGE_OUTCOMES = frozenset({str(Outcome.CONNECTION), str(Outcome.TIMEOUT), str(Outcome.SERVER_ERROR)})
OUTAGE_MIN_MODULES = 3  # distinct modules failing (no success in between) before the canopy rests
_RESEND_STATUSES = (CommandStatus.FAILED, CommandStatus.MISMATCH)
_SLOT_RETRY = (CommandStatus.FAILED, CommandStatus.MISMATCH, CommandStatus.SUPERSEDED)
COMMISSION_ROUNDS = (
    3  # a slot write that didn't land (silent drop, throttling) is retried within one commission
)
PUBLISH_MIN_INTERVAL_S = 0.25  # state pushes are coalesced to at most 4 per second
EARLY_REASSERT_LIMIT = 3  # per module per change
HEADER_READ_ATTEMPTS = 3  # the readback a slot write copies its header from

Compiler = Callable[[str, Tier], ModePlan]
Ladder = Callable[[str], list[Tier]]
TierAllowed = Callable[[str, Tier], tuple[bool, str]]
SlotHashRecorder = Callable[[str, str, str], None]


def _all_allowed(_mode: str, _tier: Tier) -> tuple[bool, str]:
    return True, ""


@dataclass
class EngineDeps:
    """What the engine needs from the Services container (the lead wires these; tests build them directly)."""

    registry: Callable[[], Registry]  # the current registry (ops edits replace it at runtime)
    target: Callable[[str, Purpose], Any]  # services.target(device_id, purpose) → Target
    tracker: DeviceStateTracker
    telemetry: Telemetry
    hub: Any  # WsHub-like: publish(topic, data); optional register_snapshot / client_count
    clock: Clock
    settings: ConductorSettings
    locks: DeviceLockManager
    compile: Compiler  # (mode_id, tier) → ModePlan, raising PlanError when that tier can't compile
    ladder: Ladder  # mode_id → tiers to try, best first (KeyError for an unknown mode)
    modes: Callable[[], list[str]] = list  # every mode id (commissioning walks them)
    tier_allowed: TierAllowed = _all_allowed  # capabilities + bench flags → (allowed, why not)
    record_slot_hash: SlotHashRecorder | None = None  # lead: → registry overlay slot_hashes
    simulated: Callable[[], bool] | None = None  # lead: lambda: services.engine_driver.simulated


@dataclass
class _Sent:
    """Book-keeping for one command the engine submitted."""

    command: Command
    device_id: str
    generation: int
    kind: str  # "show" (a mode's state vector) or "slot" (a slot object write)


class CanopyEngine:
    """Implements `CanopyEngineAPI`. Lives at `services.extras["canopy"]`."""

    def __init__(self, deps: EngineDeps) -> None:
        self.deps = deps
        self.clock = deps.clock
        self.telemetry = deps.telemetry
        self.tracker = deps.tracker
        self.locks = deps.locks
        self.reconciler = Reconciler(self)
        self._plan: ModePlan | None = None
        self._source: str | None = None
        self._changed_at: float | None = None
        self._generation = 0
        self._targets: dict[str, Any] = {}  # device id → Target (resolved at each apply)
        self._by_address: dict[str, str] = {}  # driver address → device id
        self._unresolved: dict[str, str] = {}  # device id → why it has no address
        self._locked: set[str] = set()  # addresses locked at the last look
        self._paused = False
        self._outage: dict | None = None
        self._commissioning = False
        self._stopped = False
        self._started = False
        self._last_kiosk_mono: float | None = None
        self.last_change_mono: float = self.clock.monotonic()  # any accepted change (idle hold)
        self.floors: dict[str, int] = {}  # mode id → ladder index floor set by a hysteresis downgrade
        self._sent: dict[int, _Sent] = {}  # id(command) → record, until its result arrives
        self._latest: dict[str, Command] = {}  # address → the newest show command the engine sent
        # address → final status of the current look's newest command
        self.last_status: dict[str, CommandStatus] = {}
        self._early_counts: dict[str, int] = {}  # address (or "*" for a motion plan) → early re-sends
        self._bad_streak: set[str] = set()
        self.dirty: set[str] = set()  # addresses with news since the reconciler last looked at their status
        self._tasks: set[asyncio.Task] = set()
        self._executors: dict[int, CommandExecutor] = {}
        self._unsubscribe: list[Callable[[], Any]] = []
        self._recovery_lists: list[list] = []
        self._publish_scheduled = False
        self._last_published: dict | None = None
        self._last_publish_mono = float("-inf")
        # (device id, slot) → monotonic time the slot was last written (the rewrite limit counts from it)
        self.slot_written: dict[tuple[str, str], float] = {}
        self.slot_records: dict[tuple[str, str], dict] = {}  # (device id, slot) → {hash, mode, tier}
        self._t1_plans: dict[str, ModePlan] = {}  # T1 plans compiled for commissioning and drift rewrites
        self._t1_failed: set[str] = set()
        self.stats: dict[str, Any] = {"outages": [], "tier_changes": [], "early_reasserts": 0}
        self._unsubscribe.append(self.telemetry.subscribe(self._on_event))

    # ---- lifecycle -------------------------------------------------------------------------------------------
    async def start(self, *, apply_initial: bool = True) -> None:
        """Start the reconciler loop (and, by default, show the idle mode — the canopy's resting look)."""
        if self._started:
            return
        self._started = True
        self._stopped = False
        register = getattr(self.deps.hub, "register_snapshot", None)
        if register is not None:
            register(TOPIC, lambda _topic: self.state().to_json())
        for device_id in self._module_ids():
            self._resolve(device_id)
        self.reconciler.start()
        if apply_initial:
            await self.request_mode(self.deps.settings.kiosk.idle_mode, source="idle")
        self._publish_soon()

    async def stop(self) -> None:
        """Stop the reconciler and wait for the engine's own background tasks. Call before closing executors."""
        self._stopped = True
        await self.reconciler.stop()
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        for callbacks in self._recovery_lists:
            with contextlib.suppress(ValueError):
                callbacks.remove(self._on_driver_recovery)
        self._recovery_lists.clear()

    close = stop  # Services.close() calls `close` on extras it knows about

    async def wait_idle(self, timeout: float | None = None) -> None:
        """Wait until every burst the engine started has its results (tests, the soak, writer-path proofs)."""

        async def drain() -> None:
            while self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)

        if timeout is None:
            await drain()
        else:
            await asyncio.wait_for(drain(), timeout)

    # ---- CanopyEngineAPI --------------------------------------------------------------------------------------
    def current_plan(self) -> ModePlan | None:
        return self._plan

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def outage(self) -> dict | None:
        return self._outage

    @property
    def commissioning(self) -> bool:
        return self._commissioning

    @property
    def stopped(self) -> bool:
        return self._stopped

    async def request_mode(self, mode_id: str, *, source: str) -> CanopyState:
        """Guest/ops/idle mode request. Kiosk requests honour the cooldown, the pause and the resting state."""
        now = self.clock.monotonic()
        if self._stopped:
            return self._refused(STOPPED_MESSAGE, None)
        if self._commissioning:
            return self._refused(COMMISSIONING_MESSAGE, 30.0)
        if source == "kiosk":
            if self._paused:
                return self._refused(PAUSED_MESSAGE, None)
            if self._outage is not None:
                return self._refused(RESTING_MESSAGE, self.deps.settings.reconciler.outage_probe_interval_s)
            remaining = self._cooldown_remaining(now)
            if remaining > 0:
                return self._refused(
                    f"One moment: the canopy is still changing. Try again in {math.ceil(remaining)} s.",
                    round(remaining, 2),
                )
        plan, why = self.compile_best(mode_id)
        if plan is None:
            return self._refused(why, None)
        if source == "kiosk":
            self._last_kiosk_mono = now
        if source not in ("reconciler", "recovery"):
            self.last_change_mono = now
        self._show(plan, source)
        return self.state()

    async def reassert_all(self, *, source: str) -> CanopyState:
        """Send the full state vector to every non-locked module, now (one burst). During an outage, probe instead:
        the first good readback re-applies the look to every module."""
        if self._outage is not None:
            await self.reconciler.probe()
            return self.state()
        if self._plan is not None and not self._commissioning:
            self.send(None, source=source, label=f"re-assert all: {self._plan.mode_id}")
        return self.state()

    def pause_guest_input(self, paused: bool, *, source: str) -> CanopyState:
        if paused != self._paused:
            self._paused = paused
            self.telemetry.record(
                "alert",
                level="info",
                message=f"Guest input {'paused' if paused else 'resumed'} ({source}).",
                source=source,
            )
        self._publish_soon()
        return self.state()

    async def safe_look(self, *, source: str) -> CanopyState:
        """Lowcountry Calm at its strand technique, every module, now (bypasses cooldown, pause and floors)."""
        try:
            plan = self.deps.compile(SAFE_MODE, Tier.STRAND)
        except (PlanError, KeyError, LookupError) as e:
            plan, why = self.compile_best(SAFE_MODE)
            if plan is None:
                return self._refused(f"The safe look can't be compiled: {e}; {why}", None)
        self.last_change_mono = self.clock.monotonic()
        self._show(plan, source)
        return self.state()

    async def commission(self, *, source: str) -> dict:
        """Write each T1 mode's slot content to every module and record the readback hashes (see module doc)."""
        if self._commissioning:
            return {"ok": False, "message": "Commissioning is already running.", "modes": {}, "skipped": []}
        if self._outage is not None:
            return {"ok": False, "message": RESTING_MESSAGE, "modes": {}, "skipped": []}
        report: dict[str, Any] = {
            "ok": True,
            "simulated": self._simulated(),
            "started_at": self.clock.now(),
            "modes": {},
            "skipped": [],
        }
        self._commissioning = True
        self._t1_plans.clear()
        self._t1_failed.clear()
        self._publish_soon()
        try:
            # Let show commands already on their way finish, so slot writes don't supersede them (or vice versa).
            with contextlib.suppress(TimeoutError):
                await self.wait_idle(timeout=30.0)
            for mode_id in self.deps.modes():
                try:
                    ladder = list(self.deps.ladder(mode_id))
                except (KeyError, LookupError, PlanError) as e:
                    report["skipped"].append({"mode": mode_id, "reason": str(e)})
                    continue
                if Tier.T1 not in ladder:
                    continue
                allowed, why = self.deps.tier_allowed(mode_id, Tier.T1)
                if not allowed:
                    report["skipped"].append({"mode": mode_id, "reason": why or "canvas tier not allowed"})
                    continue
                try:
                    plan = self.deps.compile(mode_id, Tier.T1)
                except PlanError as e:
                    report["skipped"].append({"mode": mode_id, "reason": str(e)})
                    continue
                self._t1_plans[mode_id] = plan
                report["modes"][mode_id] = await self._commission_plan(plan, source)
        finally:
            self._commissioning = False
        statuses = [m["status"] for mode in report["modes"].values() for m in mode["modules"].values()]
        written = statuses.count("applied")
        problems = [s for s in statuses if s not in ("applied", "skipped", "locked")]
        report["ok"] = not problems
        report["finished_at"] = self.clock.now()
        report["message"] = (
            f"Wrote {written} slot(s)"
            + (f"; {len(problems)} need attention" if problems else "")
            + (
                f"; {statuses.count('skipped')} skipped (recently written or no slot content)"
                if "skipped" in statuses
                else ""
            )
            + "."
        )
        self.telemetry.record(
            "alert", level="info" if not problems else "warn", message=f"Commissioning: {report['message']}"
        )
        self._publish_soon()
        return report

    # ---- state ------------------------------------------------------------------------------------------------
    def state(self) -> CanopyState:
        plan = self._plan
        modules: dict[str, dict[str, Any]] = {}
        simulated = self._simulated()
        active_statuses: list[tuple[str, DeviceStatus]] = []
        unresolved = []
        locked_count = 0
        for device_id in self._module_ids():
            entry = self._entry(device_id)
            record: dict[str, Any] = {
                "label": (entry.label if entry else "") or device_id,
                "module": entry.module if entry else None,
                "locked": False,
                "simulated": simulated,
            }
            target = self._targets.get(device_id)
            if target is None:
                record.update(
                    status=str(DeviceStatus.NEEDS_ATTENTION),
                    reason=self._unresolved.get(device_id, "No address for this module yet."),
                )
                unresolved.append(device_id)
                modules[device_id] = record
                continue
            record["simulated"] = bool(target.simulated)
            holder = self.locks.holder(target.address)
            if holder is not None:
                locked_count += 1
                record.update(
                    status="locked",
                    locked=True,
                    reason=f"In use by {holder.owner}; the show engine leaves it alone until it's released.",
                )
            else:
                status = self.tracker.status(target.address)
                reason = (
                    "Readback matches the look."
                    if status is DeviceStatus.READY
                    else self.tracker.snapshot(target.address)["reason"]
                )
                record.update(status=str(status), reason=reason)
                active_statuses.append((device_id, status))
            modules[device_id] = record

        phase, message = self._phase(plan, modules, active_statuses, unresolved, locked_count)
        remaining = self._cooldown_remaining(self.clock.monotonic())
        return CanopyState(
            mode=plan.mode_id if plan else None,
            label=plan.label if plan else None,
            tier=str(plan.tier) if plan else None,
            feature=plan.feature if plan else None,
            phase=phase,
            guest_input=not self._paused,
            cooldown_until=(self.clock.now() + remaining) if remaining > 0 else None,
            changed_at=self._changed_at,
            modules=modules,
            simulated=simulated,
            message=message,
        )

    def _phase(
        self,
        plan: ModePlan | None,
        modules: dict[str, dict],
        active: list[tuple[str, DeviceStatus]],
        unresolved: list[str],
        locked_count: int,
    ) -> tuple[CanopyPhase, str]:
        if self._stopped:
            return CanopyPhase.RESTING, STOPPED_MESSAGE
        if self._outage is not None:
            return CanopyPhase.RESTING, RESTING_MESSAGE
        if self._paused:
            return CanopyPhase.RESTING, PAUSED_MESSAGE
        if plan is None:
            return CanopyPhase.RESTING, STARTING_MESSAGE
        if self._commissioning:
            return CanopyPhase.UPDATING, "Writing the saved patterns to every strand…"
        stuck = [
            d
            for d, s in active
            if s is DeviceStatus.NEEDS_ATTENTION and self.reconciler.wrong_too_long(self._targets[d].address)
        ]
        if stuck or unresolved:
            names = [modules[d]["label"] for d in [*stuck, *unresolved]]
            first = modules[(stuck or unresolved)[0]]["reason"]
            return (
                CanopyPhase.NEEDS_ATTENTION,
                f"{len(names)} module(s) need attention ({', '.join(names)}): {first}",
            )
        if not active and locked_count:
            return CanopyPhase.NEEDS_ATTENTION, "Every module is in use on a test page."
        if any(s is not DeviceStatus.READY for _, s in active):
            return CanopyPhase.UPDATING, "Changing the light…"
        suffix = f" ({locked_count} module(s) in use on a test page)" if locked_count else ""
        return CanopyPhase.READY, "Ready" + suffix

    def _refused(self, message: str, retry_after_s: float | None) -> CanopyState:
        state = self.state()
        state.refused = True
        state.message = message
        state.retry_after_s = retry_after_s
        return state

    def _cooldown_remaining(self, now: float) -> float:
        if self._last_kiosk_mono is None:
            return 0.0
        return max(0.0, self._last_kiosk_mono + self.deps.settings.kiosk.cooldown_s - now)

    # ---- compiling --------------------------------------------------------------------------------------------
    def compile_best(self, mode_id: str, *, floor: int | None = None) -> tuple[ModePlan | None, str]:
        """The best plan for `mode_id`: the first allowed tier that compiles, from the floor down, then above it."""
        try:
            ladder = list(self.deps.ladder(mode_id))
        except (KeyError, LookupError, PlanError, ValueError):
            return None, f"There's no mode called {mode_id!r}."
        if not ladder:
            return None, f"There's no mode called {mode_id!r}."
        start = self.floors.get(mode_id, 0) if floor is None else floor
        start = min(max(0, start), len(ladder) - 1)
        order = list(range(start, len(ladder))) + list(range(start))
        reasons = []
        for i in order:
            tier = ladder[i]
            allowed, why = self.deps.tier_allowed(mode_id, tier)
            if not allowed:
                reasons.append(f"{tier}: {why or 'not allowed'}")
                continue
            try:
                return self.deps.compile(mode_id, tier), ""
            except PlanError as e:
                reasons.append(f"{tier}: {e}")
        return None, f"{mode_id} can't be shown right now ({'; '.join(reasons)})."

    # ---- applying ---------------------------------------------------------------------------------------------
    def _show(self, plan: ModePlan, source: str) -> None:
        """Make `plan` the current look and (unless resting) send it to every module."""
        self._install(plan, source)
        if self._outage is None:
            self.send(None, source=source, label=f"mode: {plan.mode_id}")
        self._publish_soon()

    def _install(self, plan: ModePlan, source: str) -> None:
        self._generation += 1
        self._plan = plan
        self._source = source
        self._changed_at = self.clock.now()
        self._early_counts.clear()
        self.last_status.clear()
        for device_id in plan.modules:
            self._resolve(device_id)
        self.reconciler.on_new_plan()
        self.telemetry.record("mode", mode=plan.mode_id, tier=str(plan.tier), source=source)

    def send(self, device_ids: Iterable[str] | None, *, source: str, label: str) -> list[str]:
        """Send the current plan's full state vector to `device_ids` (None = every module) as one burst.

        Locked modules are skipped (and remembered, so the reconciler re-sends on release). Returns the device ids
        a command was queued for."""
        plan = self._plan
        if plan is None:
            return []
        wanted = list(plan.modules) if device_ids is None else [d for d in device_ids if d in plan.modules]
        groups: dict[int, tuple[CommandExecutor, list[Command]]] = {}
        sent = []
        for device_id in wanted:
            target = self._targets.get(device_id) or self._resolve(device_id)
            if target is None:
                continue
            if self.locks.holder(target.address) is not None:
                self._locked.add(target.address)
                continue
            mt = plan.modules[device_id]
            expect = list(mt.expect) if mt.expect else list(mt.rows)
            self.tracker.set_desired(
                target.address,
                expect,
                label=f"{plan.label} ({plan.tier})",
                source=source,
                meta={"mode": plan.mode_id, "tier": str(plan.tier), "device_id": device_id},
            )
            cmd = Command(
                target.address,
                list(mt.rows),
                Purpose.ENGINE,
                label=label,
                verify=True,
                expect=expect,
                source=source,
            )
            self._track(cmd, device_id, "show")
            self._latest[target.address] = cmd
            self.dirty.add(target.address)
            self.last_status.pop(target.address, None)
            groups.setdefault(id(target.executor), (target.executor, []))[1].append(cmd)
            sent.append(device_id)
        for executor, cmds in groups.values():
            self._spawn(self._burst(executor, cmds), name=f"canopy:{label}")
        if sent:
            self._publish_soon()
        return sent

    def _track(self, cmd: Command, device_id: str, kind: str) -> None:
        self._sent[id(cmd)] = _Sent(cmd, device_id, self._generation, kind)

    async def _burst(self, executor: CommandExecutor, cmds: list[Command]) -> list[CommandResult]:
        try:
            return await executor.burst(cmds)
        except Exception:
            log.exception("canopy burst failed")
            for cmd in cmds:
                self._sent.pop(id(cmd), None)
            self.reconciler.schedule_reassert()
            return []

    def _spawn(self, coro, *, name: str) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ---- targets ----------------------------------------------------------------------------------------------
    def _resolve(self, device_id: str) -> Any:
        try:
            target = self.deps.target(device_id, Purpose.ENGINE)
        except (LookupError, KeyError) as e:
            self._targets.pop(device_id, None)
            self._unresolved[device_id] = str(e).strip("'\"")
            return None
        old = self._targets.get(device_id)
        if old is not None and old.address != target.address:
            self._by_address.pop(old.address, None)
        self._targets[device_id] = target
        self._by_address[target.address] = device_id
        self._unresolved.pop(device_id, None)
        self._attach(target)
        return target

    def _attach(self, target: Any) -> None:
        executor = target.executor
        if id(executor) not in self._executors:
            self._executors[id(executor)] = executor
            self._unsubscribe.append(executor.add_listener(self._on_result))
        callbacks = getattr(target.driver, "on_recovery", None)
        if isinstance(callbacks, list) and not any(c is callbacks for c in self._recovery_lists):
            callbacks.append(self._on_driver_recovery)
            self._recovery_lists.append(callbacks)

    def active_modules(self) -> list[tuple[str, Any]]:
        """(device id, Target) for every module of the current plan (or, before the first plan, every module the
        engine knows) that has an address and isn't locked."""
        plan = self._plan
        out = []
        for device_id in plan.modules if plan is not None else list(self._targets):
            target = self._targets.get(device_id)
            if target is not None and self.locks.holder(target.address) is None:
                out.append((device_id, target))
        return out

    def target_of(self, device_id: str) -> Any:
        return self._targets.get(device_id)

    def _module_ids(self) -> list[str]:
        if self._plan is not None:
            return list(self._plan.modules)
        return [d.id for d in self.deps.registry().canopy_modules()]

    def _entry(self, device_id: str) -> Any:
        try:
            return self.deps.registry().get(device_id)
        except (KeyError, LookupError):
            return None

    def _simulated(self) -> bool:
        if self.deps.simulated is not None:
            return bool(self.deps.simulated())
        if self._targets:
            return any(bool(t.simulated) for t in self._targets.values())
        return True  # nothing resolved yet: never claim real hardware

    # ---- results and events -----------------------------------------------------------------------------------
    def _on_result(self, result: CommandResult) -> None:
        record = self._sent.pop(id(result.command), None)
        if record is None:
            return
        cmd = result.command
        address = cmd.device_id
        if (
            result.status is CommandStatus.FAILED
            and result.attempts
            and result.attempts[-1].outcome is Outcome.THROTTLED  # the executor's retries didn't clear it
        ):
            self.reconciler.note_throttle_failure(self._account_of(address))
        current = record.kind == "show" and record.generation == self._generation
        if current and self._latest.get(address) is cmd:
            self.last_status[address] = result.status
            if result.status is CommandStatus.SUPERSEDED:
                # Something other than a newer engine command replaced it (an older burst that queued late):
                # check again soon.
                self.reconciler.schedule_reassert(1.0)
            elif result.status is CommandStatus.MISMATCH:
                self.reconciler.schedule_reassert(0.0)  # accepted but not stored (a silent drop): re-send now
            elif result.status in _RESEND_STATUSES:
                self.reconciler.schedule_reassert()
            target = self._targets.get(record.device_id)
            if result.status is CommandStatus.MISMATCH and target is not None:
                # If a slot's pattern is what differs, that's drift, not something a re-send fixes: check now.
                if result.readback is not None and result.readback.ok:
                    self.reconciler.check_slots(record.device_id, target, result.readback.rows)
                else:
                    self.reconciler.check_module_soon(record.device_id, target)
        self._publish_soon()

    def _on_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind not in ("attempt", "readback", "command"):
            return
        address = event.get("device_id")
        if address not in self._by_address:
            return
        self.dirty.add(address)
        self._publish_soon()
        if kind == "command":
            return
        outcome = event.get("outcome")
        if outcome == str(Outcome.OK):
            self._bad_streak.clear()
            # A verification readback that differs in a row a re-send can fix (not only a slot's stored pattern,
            # which is drift): the cloud didn't store the write.
            differs = [c for c in event.get("mismatched") or () if c != R.FRAME_CLASS]
            if kind == "readback" and differs:
                self._maybe_early_reassert(address)
        elif outcome in OUTAGE_OUTCOMES and self._outage is None and not self._stopped:
            self._bad_streak.add(address)
            if len(self._bad_streak) >= min(OUTAGE_MIN_MODULES, max(1, len(self._by_address))):
                self.enter_outage(
                    f"Hubspace isn't answering ({outcome.replace('_', ' ')}) for "
                    f"{len(self._bad_streak)} module(s)."
                )

    def _maybe_early_reassert(self, address: str) -> None:
        plan = self._plan
        cmd = self._latest.get(address)
        if plan is None or cmd is None or self._outage is not None or self._commissioning:
            return
        record = self._sent.get(id(cmd))
        if record is None or record.generation != self._generation or record.kind != "show":
            return  # that command already finished (a true-up readback, not a verification)
        # Motion plans re-trigger every module as one burst (07 §6), once per change; others re-send the one module,
        # at most EARLY_REASSERT_LIMIT times per change (a module that keeps disagreeing falls back to the
        # reconciler's slower cadence and, eventually, the hysteresis downgrade).
        key = "*" if plan.motion else address
        if self._early_counts.get(key, 0) >= (1 if plan.motion else EARLY_REASSERT_LIMIT):
            return
        self._early_counts[key] = self._early_counts.get(key, 0) + 1
        self.stats["early_reasserts"] += 1
        targets = None if plan.motion else [self._by_address[address]]

        def resend() -> None:
            if self._plan is plan and self._outage is None:
                self.send(targets, source="reconciler", label=f"re-assert: {plan.mode_id}")

        asyncio.get_running_loop().call_soon(resend)

    def _account_of(self, address: str) -> str:
        device_id = self._by_address.get(address)
        target = self._targets.get(device_id) if device_id else None
        try:
            return target.driver.account_of(address) if target is not None else "unknown"
        except LookupError:
            return "unknown"

    # ---- outage -----------------------------------------------------------------------------------------------
    def enter_outage(self, reason: str) -> None:
        if self._outage is not None:
            return
        self._outage = {"since": self.clock.now(), "since_mono": self.clock.monotonic(), "reason": reason}
        self._bad_streak.clear()
        self.telemetry.record("alert", level="warn", message=f"Canopy resting: {reason}")
        self.reconciler.on_outage()
        self._publish_soon()

    def exit_outage(self, why: str) -> None:
        """Hubspace answers again: re-apply the current look to every module in one burst."""
        if self._outage is None:
            return
        outage, self._outage = self._outage, None
        record = {
            "since": outage["since"],
            "recovered_at": self.clock.now(),
            "duration_s": round(self.clock.monotonic() - outage["since_mono"], 3),
            "reason": outage["reason"],
        }
        self.stats["outages"].append(record)
        self.telemetry.record(
            "alert",
            level="info",
            message=f"Hubspace is answering again ({why}); re-applying "
            f"{self._plan.label if self._plan else 'nothing'} to every module.",
        )
        self.reconciler.on_recovery()
        if self._plan is not None:
            self.send(None, source="recovery", label=f"recovery: {self._plan.mode_id}")
        self._publish_soon()

    def _on_driver_recovery(self, _account: str = "") -> None:
        self.reconciler.on_driver_recovery()

    # ---- hysteresis (called by the reconciler) -----------------------------------------------------------------
    def downgrade(self, reason: str) -> bool:
        """Move the current mode one allowed, compilable rung down its ladder. False when there's none."""
        plan = self._plan
        if plan is None:
            return False
        try:
            ladder = list(self.deps.ladder(plan.mode_id))
        except (KeyError, LookupError, PlanError):
            return False
        if plan.tier not in ladder:
            return False
        for index in range(ladder.index(plan.tier) + 1, len(ladder)):
            tier = ladder[index]
            if not self.deps.tier_allowed(plan.mode_id, tier)[0]:
                continue
            try:
                lower = self.deps.compile(plan.mode_id, tier)
            except PlanError:
                continue
            self.floors[plan.mode_id] = index
            self._tier_change(plan.mode_id, plan.tier, lower.tier, reason, "down")
            self._show(lower, "reconciler")
            return True
        return False

    def upgrade(self, reason: str) -> list[str]:
        """Lift every hysteresis floor one rung; re-apply the current mode if its tier can rise. Returns the modes."""
        lifted = []
        for mode_id in list(self.floors):
            self.floors[mode_id] -= 1
            if self.floors[mode_id] <= 0:
                del self.floors[mode_id]
            lifted.append(mode_id)
        plan = self._plan
        if plan is not None and plan.mode_id in lifted:
            better, _ = self.compile_best(plan.mode_id)
            if better is not None and better.tier != plan.tier:
                self._tier_change(plan.mode_id, plan.tier, better.tier, reason, "up")
                self._show(better, "reconciler")
        return lifted

    def _tier_change(self, mode_id: str, old: Tier, new: Tier, reason: str, direction: str) -> None:
        self.stats["tier_changes"].append(
            {
                "t": self.clock.now(),
                "mode": mode_id,
                "from": str(old),
                "to": str(new),
                "direction": direction,
                "reason": reason,
            }
        )
        self.telemetry.record(
            "tier", mode=mode_id, reason=reason, direction=direction, **{"from": str(old), "to": str(new)}
        )
        self.telemetry.record(
            "alert",
            level="warn" if direction == "down" else "info",
            message=f"{mode_id}: {'down' if direction == 'down' else 'back up'} from {old} to {new} — {reason}",
        )

    # ---- slots ------------------------------------------------------------------------------------------------
    async def _commission_plan(self, plan: ModePlan, source: str) -> dict:
        min_interval = self.deps.settings.reconciler.slot_rewrite_min_interval_s
        now = self.clock.monotonic()
        modules: dict[str, dict] = {}
        pending: list[tuple[str, ModuleTarget, Any]] = []
        for device_id, mt in plan.modules.items():
            if not mt.slot or mt.slot_value is None:
                modules[device_id] = {"status": "skipped", "reason": "No slot content at this tier."}
                continue
            target = self._targets.get(device_id) or self._resolve(device_id)
            if target is None:
                modules[device_id] = {"status": "failed", "reason": self._unresolved.get(device_id, "")}
                continue
            last = self.slot_written.get((device_id, mt.slot))
            if last is not None and now - last < min_interval:
                modules[device_id] = {
                    "status": "skipped",
                    "slot": mt.slot,
                    "reason": f"Written {round((now - last) / 60, 1)} min ago; each slot is rewritten at most "
                    f"once every {round(min_interval / 60)} min.",
                }
                continue
            pending.append((device_id, mt, target))
        # A write that didn't land (a silent drop, throttling that outlasted the executor's retries, a throttled
        # header read) never changed the slot, so trying it again within this commission is a retry, not a rewrite.
        for _round in range(COMMISSION_ROUNDS):
            batch: list[tuple[str, ModuleTarget, Any, Command]] = []
            retry: list[tuple[str, ModuleTarget, Any]] = []
            for device_id, mt, target in pending:
                if self.locks.holder(target.address) is not None:
                    modules[device_id] = {
                        "status": "locked",
                        "slot": mt.slot,
                        "reason": "In use on a test page.",
                    }
                    continue
                cmd, error, retryable = await self._slot_command(
                    device_id, target, mt, source, f"commission: {plan.label}"
                )
                if cmd is None:
                    modules[device_id] = {"status": "failed", "slot": mt.slot, "reason": error}
                    if retryable:
                        retry.append((device_id, mt, target))
                    continue
                batch.append((device_id, mt, target, cmd))
            results = await self._slot_burst(batch) if batch else {}
            for device_id, mt, target, cmd in batch:
                result = results.get(id(cmd))
                modules[device_id] = self._record_slot_result(device_id, mt.slot, plan, result)
                if modules[device_id]["status"] == "applied":
                    self.slot_written[(device_id, mt.slot)] = self.clock.monotonic()
                elif result is not None and result.status in _SLOT_RETRY:
                    retry.append((device_id, mt, target))
            pending = retry
            if not pending:
                break
        return {"tier": str(plan.tier), "label": plan.label, "modules": modules}

    def _record_slot_result(
        self, device_id: str, slot: str, plan: ModePlan, result: CommandResult | None
    ) -> dict:
        if result is None:
            return {"status": "failed", "slot": slot, "reason": "No result from the executor."}
        entry: dict[str, Any] = {"status": str(result.status), "slot": slot}
        if result.status is not CommandStatus.APPLIED:
            entry["reason"] = result.error or str(result.status)
            return entry
        value = result.readback.get(R.FRAME_CLASS, slot) if result.readback is not None else None
        digest = R.slot_hash(value)
        if digest is None:
            entry.update(status="mismatch", reason="The readback didn't include the slot's pattern.")
            return entry
        entry["hash"] = digest
        self.slot_records[(device_id, slot)] = {"hash": digest, "mode": plan.mode_id, "tier": str(plan.tier)}
        if self.deps.record_slot_hash is not None:
            try:
                self.deps.record_slot_hash(device_id, slot, digest)
            except Exception as e:  # the overlay refused it: the write happened, the record didn't
                log.exception("record_slot_hash failed for %s %s", device_id, slot)
                entry["reason"] = f"Written, but the hash couldn't be saved: {e}"
        return entry

    async def _slot_burst(
        self, batch: list[tuple[str, ModuleTarget, Any, Command]]
    ) -> dict[int, CommandResult]:
        groups: dict[int, tuple[CommandExecutor, list[Command]]] = {}
        for device_id, _mt, target, cmd in batch:
            self._track(cmd, device_id, "slot")
            groups.setdefault(id(target.executor), (target.executor, []))[1].append(cmd)
        results: dict[int, CommandResult] = {}
        for executor, cmds in groups.values():
            for result in await executor.burst(cmds):
                results[id(result.command)] = result
        return results

    async def _slot_command(
        self, device_id: str, target: Any, mt: ModuleTarget, source: str, label: str
    ) -> tuple[Command | None, str, bool]:
        """One slot-object write for `mt.slot`, with the header copied from the device's own readback (04).
        Returns (command, why not, whether trying again later could help)."""
        rr = None
        for attempt in range(HEADER_READ_ATTEMPTS):
            if attempt:
                await self.clock.sleep(self.deps.settings.executor.min_write_interval_s)
            rr = await target.driver.read_state(target.address, purpose=Purpose.ENGINE)
            if rr.ok or not rr.outcome.retryable:
                break
        if not rr.ok:
            why = rr.error or str(rr.outcome)
            return (
                None,
                f"Couldn't read the slot's current header first: {why}. Nothing sent.",
                rr.outcome.retryable,
            )
        current = rr.get(R.FRAME_CLASS, mt.slot)
        entries = R.frame_entries(mt.slot_value)
        if entries is None:
            return None, "The compiled slot content isn't a pattern object. Nothing sent.", False
        existing = R.frame_entries(current)
        inner = (mt.slot_value or {}).get(R.FRAME_CLASS, mt.slot_value) or {}
        effects = {k: inner[k] for k in R.EFFECT_FIELDS if isinstance(inner.get(k), int)}
        try:
            value = R.frame_value(
                current if existing is not None else mt.slot_value,
                entries,
                expected_length=len(existing) if existing is not None else None,
                effects=effects,
            )
        except R.PayloadError as e:
            return None, f"{e}. Nothing sent.", False
        row = R.slot_write(mt.slot, value)
        cmd = Command(
            target.address, [row], Purpose.ENGINE, label=f"{label} → {mt.slot}", expect=[row], source=source
        )
        return cmd, "", False

    def slot_expectations(self, device_id: str) -> list[tuple[str, str, Tier, str]]:
        """(slot, expected hash, tier, mode) the true-up checks on this module: T1 slots with a recorded hash
        (commissioned here, or saved in the registry overlay), and the current plan's T2 slot."""
        out: dict[str, tuple[str, str, Tier, str]] = {}
        entry = self._entry(device_id)
        for slot, digest in ((entry.slot_hashes if entry is not None else {}) or {}).items():
            # A hash saved in the overlay: ours to rewrite only when a canvas (T1) mode owns that slot here.
            owner = self.t1_target(device_id, slot)
            out[slot] = (slot, digest, Tier.T1 if owner else Tier.T2, owner[0].mode_id if owner else "")
        for (dev, slot), record in self.slot_records.items():
            if dev == device_id:
                out[slot] = (slot, record["hash"], Tier.T1, record["mode"])
        plan = self._plan
        if plan is not None and plan.tier in (Tier.T1, Tier.T2) and device_id in plan.modules:
            mt = plan.modules[device_id]
            if mt.slot and mt.slot_hash and (plan.tier is Tier.T2 or mt.slot not in out):
                # T1 before a recorded readback hash: the compiled content's hash. T2: the recorded hand-paint.
                out[mt.slot] = (mt.slot, mt.slot_hash, plan.tier, plan.mode_id)
        return list(out.values())

    def t1_target(self, device_id: str, slot: str) -> tuple[ModePlan, ModuleTarget] | None:
        """The T1 module target that owns `slot` on `device_id` (compiling T1 plans lazily after a restart)."""
        for mode_id in self.deps.modes():
            plan = self._t1_plans.get(mode_id)
            if plan is None:
                if mode_id in self._t1_failed:
                    continue
                try:
                    if (
                        Tier.T1 not in self.deps.ladder(mode_id)
                        or not self.deps.tier_allowed(mode_id, Tier.T1)[0]
                    ):
                        continue
                    plan = self._t1_plans[mode_id] = self.deps.compile(mode_id, Tier.T1)
                except (PlanError, KeyError, LookupError):
                    self._t1_failed.add(mode_id)  # until the next commissioning
                    continue
            mt = plan.modules.get(device_id)
            if mt is not None and mt.slot == slot and mt.slot_value is not None:
                return plan, mt
        return None

    async def rewrite_slot(self, device_id: str, slot: str, *, source: str) -> str:
        """Rewrite one drifted T1 slot (rate-limited by the caller). Returns the command status or why not."""
        found = self.t1_target(device_id, slot)
        target = self._targets.get(device_id) or self._resolve(device_id)
        if found is None or target is None:
            return "no canvas content is known for this slot"
        if self.locks.holder(target.address) is not None:
            return "locked"
        plan, mt = found
        cmd, error, _retryable = await self._slot_command(
            device_id, target, mt, source, f"slot rewrite: {plan.label}"
        )
        if cmd is None:
            return error
        self.slot_written[(device_id, slot)] = self.clock.monotonic()
        self._track(cmd, device_id, "slot")
        [result] = await target.executor.burst([cmd])
        entry = self._record_slot_result(device_id, slot, plan, result)
        return entry["status"]

    # ---- publishing -------------------------------------------------------------------------------------------
    def _publish_soon(self) -> None:
        """Push the state on topic "canopy" soon: coalesced, at most every PUBLISH_MIN_INTERVAL_S."""
        if self._publish_scheduled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._publish_scheduled = True
        wait = self._last_publish_mono + PUBLISH_MIN_INTERVAL_S - self.clock.monotonic()
        if wait > 0:
            loop.call_later(wait, self._publish)
        else:
            loop.call_soon(self._publish)

    def _publish(self) -> None:
        self._publish_scheduled = False
        self._last_publish_mono = self.clock.monotonic()
        hub = self.deps.hub
        if hub is None or getattr(hub, "client_count", 1) == 0:
            self._last_published = None  # a subscriber gets the snapshot on subscribe
            return
        try:
            data = self.state().to_json()
        except Exception:
            log.exception("canopy state failed")
            return
        if data != self._last_published:
            self._last_published = data
            hub.publish(TOPIC, data)


def engine_from_services(
    services: Any,
    *,
    compile: Compiler,
    ladder: Ladder,
    modes: Callable[[], list[str]],
    tier_allowed: TierAllowed | None = None,
) -> CanopyEngine:
    """The engine wired to the Services container (the lead's one-liner). Slot hashes are saved to the registry
    overlay (`slot_hashes` per device, merged), which needs CONDUCTOR_DATA_DIR."""

    def record_slot_hash(device_id: str, slot: str, digest: str) -> None:
        entry = services.registry.get(device_id)
        services.update_registry(device_id, {"slot_hashes": {**(entry.slot_hashes or {}), slot: digest}})

    return CanopyEngine(
        EngineDeps(
            registry=lambda: services.registry,
            target=services.target,
            tracker=services.tracker,
            telemetry=services.telemetry,
            hub=services.hub,
            clock=services.clock,
            settings=services.settings,
            locks=services.locks,
            compile=compile,
            ladder=ladder,
            modes=modes,
            tier_allowed=tier_allowed or _all_allowed,
            record_slot_hash=record_slot_hash,
            simulated=lambda: bool(services.engine_driver.simulated),
        )
    )
