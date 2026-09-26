"""The single-module session behind /ops/module: one open device, its lock, its paint and its test tools.

Rules implemented (BUILD_PLAN P0b, 04, 07 §1/§5/§6, D8):
- **Every device write goes through the Target's executor** with Purpose.BENCH and this session's lease — never
  `driver.write`. Reads (profile, readback) go through the driver's read methods.
- **The page holds the device's exclusive lock** while it's open (`open` → lease; `close` releases it; the lock
  manager expires it after the configured idle time). A write without the live lease is refused before anything
  is built.
- **T1 (frames by API) needs proof on real hardware**: on the AferoDriver, painting needs registry
  `flags.t1_frames is True` (bench Gate C). On the simulator T1 is allowed (07 §6), and everything is labelled
  SIMULATED. Selecting a slot (T2) always works. A frame write copies the slot header and the framebuffer length
  from the device's own readback (`rows.frame_value`) and writes + selects it in ONE PUT.
- **Desired vs reported per head**: desired = the last readback overlaid with every row this page sent; reported =
  the latest readback. Both are rendered with `render.head_colors`.
- **Index sweep**: one frame write per step, never faster than the per-device rate budget (the step is at least
  the executor's `min_write_interval_s`, and the executor enforces the budget as well).
- **Hold (photo mode)**: no writes from this page until it is released; a running sweep pauses.
- Updates go out on WebSocket topic `module:<device_id>`: `{"event", "state", "result"?}`.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from conductor import render
from conductor import rows as R
from conductor.capabilities import Capabilities, detect
from conductor.clock import Clock
from conductor.config import ConductorSettings, ConfigError, DeviceEntry, RecipesFile
from conductor.drivers.base import TELEMETRY_CLASSES, Outcome, ReadResult, StateRow
from conductor.executor_api import Command, CommandResult, CommandStatus
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager, Lease, LockError
from conductor.module import cards as C
from conductor.module._anonymize import anonymize
from conductor.module.layout import (
    Calibration,
    a_frame_order,
    calibration_for,
    display_positions,
    module_map,
    validate_calibration,
)
from conductor.telemetry import Telemetry

log = logging.getLogger(__name__)

Key = tuple[str, str | None]
DEFAULT_PAINT_SLOT = "custom-3"  # custom-1/2 hold the app-painted patterns bench Gates B/C use
RECENT_RESULTS = 20
REFRESH_MIN_S = 1.0  # a page asking for fresh readback gets at most one GET per second
SPEED_FIELDS = ("motionSpeed", "brightnessSpeed", "brightnessDepth")
SWEEP_STOP_STATUSES = (
    CommandStatus.FAILED,
    CommandStatus.DENIED,
    CommandStatus.LOCKED,
    CommandStatus.INVALID,
)
SESSIONS_KEY = "module_sessions"
HOLD_REFUSAL = "Hold is on: nothing changes on the lights until you release it."
NO_EFFECTS = "No effect codes enabled yet — enable them after the motion bench."


class ModuleError(Exception):
    """Something the page can show as-is. `status` is the HTTP status the route answers with."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class SessionDeps:
    """What a session needs from the running Conductor (built from `Services`; tests may build it by hand)."""

    clock: Clock
    locks: DeviceLockManager
    telemetry: Telemetry
    settings: ConductorSettings
    recipes: RecipesFile
    canopy: dict  # config/canopy.yaml's `canopy` mapping
    entry: Callable[[str], DeviceEntry]  # logical id → the registry entry as it is now
    publish: Callable[[str, dict], None] = lambda _topic, _data: None
    save_calibration: Callable[[str, Mapping[str, Any]], Any] | None = None  # Services.update_registry
    tracker: Any = None  # DeviceStateTracker (optional)


@dataclass
class SweepState:
    state: str = "idle"  # idle | running | paused | stopped | done
    index: int | None = None  # the head lit by the last step
    next_index: int = 0
    step_s: float = 2.0
    slot: str = DEFAULT_PAINT_SLOT
    steps_written: int = 0
    started_at: float | None = None
    message: str | None = None

    def to_json(self) -> dict:
        return asdict(self)


def _ms(a: float | None, b: float | None) -> int | None:
    return round((b - a) * 1000) if a is not None and b is not None else None


class ModuleSession:
    """One open device on the module page. Create with `ModuleSession.open(...)`."""

    def __init__(self, deps: SessionDeps, target: Any, lease: Lease, *, owner_key: str = ""):
        self.deps = deps
        self.target = target  # conductor.services.Target (device_id, address, driver, executor, simulated)
        self.device_id: str = target.device_id
        self.address: str = target.address
        self.lease = lease
        self.owner_key = owner_key
        self.session_id = secrets.token_urlsafe(9)  # public: lets a page notice it was taken over
        self.started_at = deps.clock.now()
        self.functions: list[dict] = []
        self.caps = Capabilities()
        self.reported_rows: list[StateRow] = []
        self.reported_at: float | None = None
        self._reported_mono: float | None = None
        self.last_read_error: str | None = None
        self.desired_state: dict[Key, Any] = {}
        self._desired_seq = 0
        self.cal: Calibration = calibration_for(deps.canopy, deps.entry(self.device_id))
        self.map = module_map(deps.canopy, self.cal)
        self.positions = display_positions(self.map)
        self.paint: list[R.FrameEntry] = [C.OFF] * len(self.positions)
        self.paint_slot = DEFAULT_PAINT_SLOT
        self.results: deque[dict] = deque(maxlen=RECENT_RESULTS)
        self.in_flight = 0
        self.hold: dict = {"on": False, "name": None, "since": None}
        self.last_label: str | None = None
        self.sweep_state = SweepState(step_s=self.sweep_step_s())
        self._sweep_task: asyncio.Task | None = None
        self._sweep_wake = asyncio.Event()
        self.closed = False

    # ---- lifecycle ---------------------------------------------------------------------------------------------
    @classmethod
    async def open(
        cls, deps: SessionDeps, target: Any, *, owner: str, steal: bool = False, owner_key: str = ""
    ) -> ModuleSession:
        """Take the device's lock, then read its profile and state. LockError when it can't be locked (outside
        the allowlist, or held by someone else without `steal`); ModuleError when the device can't be read."""
        lease = deps.locks.acquire(target.address, owner, steal=steal)
        session = cls(deps, target, lease, owner_key=owner_key)
        try:
            await session.load()
        except Exception as e:
            deps.locks.release(target.address, lease.token)
            if isinstance(e, ModuleError):
                raise
            log.warning("module page: reading %s failed: %s", target.device_id, type(e).__name__)
            raise ModuleError(
                f"Couldn't read {target.device_id} from {'the simulator' if target.simulated else 'Hubspace'} "
                f"({_plain(e)}). The lock was released; try again.",
                502,
            ) from None
        return session

    async def load(self) -> None:
        info = await self.target.driver.fetch_profile(self.address, purpose=Purpose.BENCH)
        self.functions = list(info.functions)
        self._take_rows(list(info.states), self.deps.clock.now())
        self.desired_state = self._reported_state()

    async def close(self) -> bool:
        """Stop the sweep and release the lock. True when this session still held it."""
        self.abandon()
        return self.deps.locks.release(self.address, self.lease.token)

    def abandon(self) -> None:
        """Stop background work without touching the lock (it was taken over or expired)."""
        self.closed = True
        if self.sweep_state.state in ("running", "paused"):
            self.sweep_state.state = "stopped"
        self._sweep_wake.set()
        if self._sweep_task is not None and not self._sweep_task.done():
            self._sweep_task.cancel()

    def lease_alive(self) -> bool:
        holder = self.deps.locks.holder(self.address)
        return holder is not None and secrets.compare_digest(holder.token, self.lease.token)

    def heartbeat(self, active: bool) -> dict:
        """Confirm the lease. Only `active` heartbeats (someone used the page since the last one) count as
        activity — or a Hold, while photos are being taken — so a forgotten phone still lets the lock idle out."""
        if active or self.hold["on"]:
            self._validate_lease()
        elif not self.lease_alive():
            raise ModuleError(self.gone_message())
        return self.lock_json()

    # ---- policy ------------------------------------------------------------------------------------------------
    @property
    def entry(self) -> DeviceEntry:
        return self.deps.entry(self.device_id)

    @property
    def n(self) -> int:
        return len(self.positions)

    def sweep_step_s(self, requested: float | None = None) -> float:
        floor = max(1.0, float(self.target.executor.policy.min_write_interval_s))
        return max(floor, float(requested or self.deps.settings.module_page.sweep_step_s))

    def head_count(self) -> dict:
        expected = self.entry.expected_heads or self.n
        reported = self.caps.framebuffer_length
        problem = self.caps.head_count_problem(expected)
        if problem is None and reported != self.n:
            problem = f"the device reports {reported} heads but this module's map has {self.n}"
        return {"expected": expected, "reported": reported, "map": self.n, "problem": problem}

    def t1_status(self) -> tuple[bool, str]:
        """(allowed, plain-English reason) for per-head frames written by this page (T1)."""
        if not self.caps.individual:
            return False, "This device has no per-head (individual) mode, so heads can't be painted."
        problem = self.head_count()["problem"]
        if problem:
            return False, f"Per-head painting is off: {problem}."
        if self.target.simulated:
            return True, "Simulator: per-head frames (T1) are simulated — no real lights change."
        if self.entry.flags.t1_frames is True:
            return (
                True,
                "Bench Gate C passed: frames written by the Conductor (T1) are proven on this device.",
            )
        return False, (
            "Painting is off until bench Gate C proves that frames written by the Conductor show up on this "
            "device. You can still select patterns painted in the Hubspace app (Slots tab)."
        )

    def _require_t1(self) -> None:
        ok, why = self.t1_status()
        if not ok:
            raise ModuleError(why)

    def _require_slot(self, slot: Any) -> str:
        known = self.caps.slots or list(R.SLOTS)
        if slot not in known:
            raise ModuleError(f"unknown slot {slot!r}; this device has {', '.join(known)}", 400)
        return slot

    def gone_message(self) -> str:
        return (
            f"You no longer hold {self.device_id} (it was closed, timed out after "
            f"{round(self.deps.locks.idle_timeout_s / 60)} min idle, or taken over). Open it again."
        )

    def _validate_lease(self) -> None:
        if self.closed:
            raise ModuleError(self.gone_message())
        try:
            self.deps.locks.validate(self.address, self.lease.token)
        except LockError:
            raise ModuleError(self.gone_message()) from None

    def _check_can_write(self) -> None:
        self._validate_lease()
        if self.hold["on"]:
            raise ModuleError(HOLD_REFUSAL)

    # ---- state -------------------------------------------------------------------------------------------------
    def _reported_state(self) -> dict[Key, Any]:
        return {r.key: r.value for r in self.reported_rows if r.function_class not in TELEMETRY_CLASSES}

    def _take_rows(self, rows: list[StateRow], fetched_at: float) -> None:
        if self.reported_at is not None and fetched_at < self.reported_at:
            return  # an older readback arriving late
        self.reported_rows = rows
        self.reported_at = fetched_at
        self._reported_mono = self.deps.clock.monotonic()
        self.caps = detect(self.functions, rows)
        self.last_read_error = None

    def _take_readback(self, rr: ReadResult | None) -> None:
        if rr is None:
            return
        if rr.ok:
            self._take_rows(list(rr.rows), rr.fetched_at)
        else:
            self.last_read_error = rr.error or f"readback failed ({rr.outcome})"

    async def refresh(self, *, force: bool = False) -> None:
        """A fresh REST readback (a read, never a write), at most once per second unless forced."""
        if (
            not force
            and self._reported_mono is not None
            and self.deps.clock.monotonic() - self._reported_mono < REFRESH_MIN_S
        ):
            return
        rr = await self.target.driver.read_state(self.address, purpose=Purpose.BENCH)
        self._take_readback(rr)
        if self.deps.tracker is not None:
            self.deps.tracker.note_readback(self.address, rr)

    def _slot_value(self, slot: str) -> Any:
        return self._reported_state().get((R.FRAME_CLASS, slot))

    def _slot_entries(self, slot: str) -> list[R.FrameEntry]:
        entries = R.frame_entries(self._slot_value(slot))
        if entries is None:
            raise ModuleError(f"There's no readback of {slot} yet. Tap Read on the Slots tab first.")
        if len(entries) != self.n:
            raise ModuleError(f"{slot} holds {len(entries)} heads but this module has {self.n}.")
        return entries

    # ---- sending -----------------------------------------------------------------------------------------------
    async def _send(self, rows: Sequence[StateRow], label: str) -> dict:
        """One command through the executor (Purpose.BENCH + this lease), then readback bookkeeping."""
        self._check_can_write()
        before, seq = dict(self.desired_state), self._desired_seq + 1
        self._desired_seq = seq
        for row in rows:
            self.desired_state[row.key] = row.value
        self.last_label = label
        if self.deps.tracker is not None:
            self.deps.tracker.set_desired(self.address, rows, label=f"module: {label}", source="module")
        self.in_flight += 1
        self.publish("sending", pending=label)
        command = Command(
            self.address,
            list(rows),
            Purpose.BENCH,
            label=f"module: {label}",
            lease_token=self.lease.token,
            verify=True,
            source="module",
        )
        try:
            result = await self.target.executor.submit(command)
        finally:
            self.in_flight -= 1
        sent = any(a.outcome is not Outcome.DENIED for a in result.attempts)
        if not sent and self._desired_seq == seq:
            self.desired_state = before  # nothing reached the device: what we "sent" is unchanged
        self._take_readback(result.readback)
        summary = self._summarize(result, label)
        self.results.appendleft(summary)
        self.publish("result", result=summary)
        return summary

    def _summarize(self, result: CommandResult, label: str) -> dict:
        out = result.to_json()
        out["device_id"] = self.device_id  # never the driver address (a metadevice id on Afero)
        out["label"] = label
        t = result.timings
        last = result.attempts[-1] if result.attempts else None
        out.update(
            ok=result.ok,
            http_status=last.http_status if last else None,
            at=t.queued_at,
            send_to_ack_ms=_ms(t.sent_at, t.acked_at),
            ack_to_readback_ms=_ms(t.acked_at, t.verified_at),
            send_to_readback_ms=_ms(t.sent_at, t.verified_at),
            bytes=max((a.bytes_sent for a in result.attempts), default=0),
        )
        return out

    async def _write_frame(
        self,
        slot: str,
        frame: Sequence[R.FrameEntry],
        label: str,
        *,
        select: bool = True,
        effects: Mapping[str, int] | None = None,
    ) -> dict:
        """T1: one frame into `slot` (header + length from the device's readback), selected in the same PUT."""
        self._check_can_write()
        self._require_t1()
        header = self._slot_value(slot)
        if header is None:
            raise ModuleError(f"There's no readback of {slot} yet, so its header can't be copied. Tap Read.")
        try:
            value = R.frame_value(
                header,
                list(frame),
                expected_length=self.caps.framebuffer_length,
                effects=effects,
                cct_range=self.caps.cct_range,
            )
            rows = [R.slot_write(slot, value)]
            if select:
                rows += [*R.slot_select(slot), R.power(True)]
        except R.PayloadError as e:
            raise ModuleError(str(e), 400) from None
        self.paint = list(frame)
        self.paint_slot = slot
        return await self._send(rows, label)

    def _stop_sweep_for(self, why: str) -> None:
        st = self.sweep_state
        if st.state in ("running", "paused"):
            st.state = "stopped"
            st.message = why
            self._sweep_wake.set()

    # ---- actions -----------------------------------------------------------------------------------------------
    async def paint_heads(
        self,
        *,
        slot: str | None = None,
        heads: Sequence[Mapping[str, Any]] | None = None,
        fill: Mapping[str, Any] | None = None,
        gradient: Mapping[str, Any] | None = None,
        base: str = "current",
    ) -> dict:
        """Compose a frame (per-head colours, a fill of a selection, or a two-stop gradient) and apply it."""
        slot = self._require_slot(slot or self.paint_slot)
        self._check_can_write()
        self._require_t1()
        try:
            if base == "current":
                frame = list(self.paint)
            elif base == "off":
                frame = [C.OFF] * self.n
            elif base == "slot":
                frame = self._slot_entries(slot)
            else:
                raise R.PayloadError("base must be current, off or slot")
            for h in heads or []:
                index = h.get("index") if isinstance(h, Mapping) else None
                frame = C.fill(frame, [index], C.entry_from({k: v for k, v in h.items() if k != "index"}))
            if fill:
                frame = C.fill(frame, list(fill.get("indices") or []), C.entry_from(fill.get("color")))
            if gradient:
                frame = C.gradient(
                    frame,
                    self.positions,
                    list(gradient.get("indices") or []),
                    C.entry_from(gradient.get("from")),
                    C.entry_from(gradient.get("to")),
                    str(gradient.get("axis", "x")),
                )
        except R.PayloadError as e:
            raise ModuleError(str(e), 400) from None
        self._stop_sweep_for("Stopped: you painted the module.")
        return await self._write_frame(slot, frame, f"paint → {slot}")

    async def whole(
        self,
        *,
        look: Mapping[str, Any] | None = None,
        power: bool | None = None,
        brightness: int | None = None,
    ) -> dict:
        """Whole-module controls (stock-app parity): a full look, or just power, or just brightness."""
        self._check_can_write()
        try:
            if look is not None:
                lk = C.look_from_json(look)
                C.check_look(lk, self.caps)
                rows = R.full_state_vector(lk, cct_range=self.caps.cct_range)
                label = f"whole module: {render.describe_look(rows, self.n, self.caps)}"
            elif power is not None:
                rows, label = [R.power(bool(power))], f"power {'on' if power else 'off'}"
            elif brightness is not None:
                lo, hi = self.caps.brightness_range or (1, 100)
                if not isinstance(brightness, int) or not lo <= brightness <= hi:
                    raise R.PayloadError(f"brightness must be a whole number {lo}–{hi}")
                rows, label = [R.brightness(brightness)], f"brightness {brightness}%"
            else:
                raise R.PayloadError("send a look, power or brightness")
        except R.PayloadError as e:
            raise ModuleError(str(e), 400) from None
        self._stop_sweep_for("Stopped: you changed the whole module.")
        return await self._send(rows, label)

    async def card(self, card: str, params: Mapping[str, Any] | None = None) -> dict:
        params = dict(params or {})
        self._check_can_write()
        try:
            compiled = C.compile_card(
                card,
                params,
                recipes=self.deps.recipes,
                caps=self.caps,
                positions=self.positions,
                width_in=float(self.map["width_in"]),
            )
        except R.PayloadError as e:
            raise ModuleError(str(e), 400) from None
        if compiled.sweep is not None:
            return {"sweep": await self.sweep("start", step_s=compiled.sweep.get("step_s"))}
        if compiled.frame is not None:
            slot = self._require_slot(params.get("slot") or self.paint_slot)
            self._stop_sweep_for("Stopped: you ran a test card.")
            out = await self._write_frame(slot, compiled.frame, f"card: {compiled.label}")
        else:
            assert compiled.look is not None
            try:
                C.check_look(compiled.look, self.caps)
                rows = R.full_state_vector(compiled.look, cct_range=self.caps.cct_range)
            except R.PayloadError as e:
                raise ModuleError(str(e), 400) from None
            self._stop_sweep_for("Stopped: you ran a test card.")
            out = await self._send(rows, f"card: {compiled.label}")
        out["notes"] = compiled.notes or []
        out["name"] = compiled.label
        return out

    async def sweep(self, action: str, *, step_s: float | None = None, from_index: int | None = None) -> dict:
        """Index sweep: start | pause | resume | stop. One frame write per step (head i on, the rest off)."""
        st = self.sweep_state
        if action == "start":
            self._check_can_write()
            self._require_t1()
            if from_index is not None and (not isinstance(from_index, int) or not 0 <= from_index < self.n):
                raise ModuleError(f"from_index must be 0–{self.n - 1}", 400)
            self._stop_sweep_for("Restarted.")
            self._sweep_wake = asyncio.Event()
            st = self.sweep_state = SweepState(
                state="running",
                next_index=from_index or 0,
                step_s=self.sweep_step_s(step_s),
                slot=self.paint_slot,
                started_at=self.deps.clock.now(),
            )
            self._sweep_task = asyncio.create_task(
                self._sweep_loop(st, self._sweep_wake), name=f"module-sweep:{self.device_id}"
            )
        elif action == "pause":
            self._validate_lease()
            if st.state == "running":
                st.state, st.message = "paused", "Paused."
                self._sweep_wake.set()
        elif action == "resume":
            self._check_can_write()
            if st.state != "paused":
                raise ModuleError("The sweep isn't paused.")
            st.state, st.message = "running", None
            self._sweep_wake.set()
        elif action == "stop":
            self._validate_lease()
            self._stop_sweep_for("Stopped.")
        else:
            raise ModuleError("action must be start, pause, resume or stop", 400)
        self.publish("sweep")
        return st.to_json()

    async def _sleep_or_wake(self, seconds: float, wake: asyncio.Event) -> None:
        if seconds <= 0:
            return
        wake.clear()
        sleeper = asyncio.ensure_future(self.deps.clock.sleep(seconds))
        waiter = asyncio.ensure_future(wake.wait())
        try:
            await asyncio.wait({sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            waiter.cancel()

    async def _sweep_loop(self, st: SweepState, wake: asyncio.Event) -> None:
        clock = self.deps.clock
        try:
            while st.state in ("running", "paused") and not self.closed:
                if st.state == "paused":
                    wake.clear()
                    await wake.wait()
                    continue
                i = st.next_index
                if i >= self.n:
                    st.state, st.message = "done", f"Done: all {self.n} heads lit in order."
                    break
                t0 = clock.monotonic()
                try:
                    summary = await self._write_frame(
                        st.slot, C.single_head(self.n, i), f"index sweep: head {i}"
                    )
                except ModuleError as e:
                    if self.hold["on"] and st.state == "running":
                        st.state, st.message = "paused", "Paused for Hold."
                        continue
                    st.state, st.message = "stopped", e.message
                    break
                if summary["status"] == CommandStatus.SUPERSEDED and self.hold["on"]:
                    continue  # Hold withdrew this step before it landed: resume from the same head
                st.index, st.next_index = i, i + 1
                st.steps_written += 1
                if CommandStatus(summary["status"]) in SWEEP_STOP_STATUSES:
                    st.state = "stopped"
                    st.message = f"Stopped at head {i}: {summary.get('error') or summary['status']}"
                    break
                self.publish("sweep")
                if st.state == "running":
                    await self._sleep_or_wake(t0 + st.step_s - clock.monotonic(), wake)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("module page: index sweep failed on %s", self.device_id)
            st.state, st.message = "stopped", "Stopped: internal error (see the Conductor log)."
        if not self.closed:
            self.publish("sweep")

    async def slot(self, slot: str, action: str) -> dict:
        """select (T2) | write_current (T1, no select) | read (fresh readback) | load (slot → paint, no write)."""
        slot = self._require_slot(slot)
        if action == "select":
            self._check_can_write()
            self._stop_sweep_for("Stopped: you selected a slot.")
            return await self._send([*R.slot_select(slot), R.power(True)], f"select {slot}")
        if action == "write_current":
            self._stop_sweep_for("Stopped: you wrote a slot.")
            return await self._write_frame(
                slot, self.paint, f"write paint → {slot} (no select)", select=False
            )
        if action == "read":
            self._validate_lease()
            await self.refresh(force=True)
            value = self._slot_value(slot)
            return {"slot": slot, "value": value, "hash": R.slot_hash(value), "error": self.last_read_error}
        if action == "load":
            self._validate_lease()
            self.paint = self._slot_entries(slot)
            self.paint_slot = slot
            self.publish("paint")
            return {"slot": slot, "loaded": True}
        raise ModuleError("action must be select, write_current, read or load", 400)

    def effects_enabled(self) -> dict[str, dict[int, str]]:
        enabled = self.deps.settings.effects.enabled
        return {k: dict(enabled.get(k, {})) for k in ("motionEffect", "brightnessEffect")}

    async def effects(
        self,
        slot: str,
        action: str,
        *,
        motion_effect: int | None = None,
        brightness_effect: int | None = None,
        speeds: Mapping[str, int] | None = None,
    ) -> dict:
        """start: rewrite the slot header with bench-enabled effect codes + select. restart: rewrite the header as
        it is + reselect (select only when T1 isn't proven). 0 = static is always allowed."""
        slot = self._require_slot(slot)
        self._check_can_write()
        if action == "restart":
            self._stop_sweep_for("Stopped: you restarted an effect.")
            if not self.t1_status()[0]:
                out = await self._send(
                    [*R.slot_select(slot), R.power(True)], f"restart effect: reselect {slot}"
                )
                out["notes"] = ["T1 isn't proven here, so only a re-select was sent (no header rewrite)."]
                return out
            return await self._write_frame(slot, self._slot_entries(slot), f"restart effect in {slot}")
        if action != "start":
            raise ModuleError("action must be start or restart", 400)
        enabled = self.effects_enabled()
        if not any(enabled.values()):
            raise ModuleError(NO_EFFECTS)
        chosen: dict[str, int] = {}
        for name, code in (("motionEffect", motion_effect), ("brightnessEffect", brightness_effect)):
            if code is None:
                continue
            if not isinstance(code, int) or (code != 0 and code not in enabled[name]):
                raise ModuleError(
                    f"{name} {code!r} isn't enabled on the bench (config effects.enabled).", 400
                )
            chosen[name] = code
        for name, value in (speeds or {}).items():
            if name not in SPEED_FIELDS:
                raise ModuleError(f"unknown speed field {name!r}; use {', '.join(SPEED_FIELDS)}", 400)
            if not isinstance(value, int) or not 0 <= value <= 100:
                raise ModuleError(f"{name} must be a whole number 0–100", 400)
            chosen[name] = value
        self._stop_sweep_for("Stopped: you started an effect.")
        desc = ", ".join(f"{k}={v}" for k, v in chosen.items()) or "header unchanged"
        return await self._write_frame(
            slot, self._slot_entries(slot), f"start effect in {slot} ({desc})", effects=chosen
        )

    def calibrate(
        self,
        *,
        orientation: str,
        cable_path: str,
        start_corner: str,
        wire_order: list | None = None,
        flip: bool = False,
    ) -> dict:
        """Save the module's calibration to the ops overlay (registry) and rebuild the map. `flip` reverses the
        resulting wire order (index 0 at the other end) and saves it as an explicit wire_order."""
        self._validate_lease()
        try:
            cal = validate_calibration(
                self.deps.canopy,
                orientation=orientation,
                cable_path=cable_path,
                start_corner=start_corner,
                wire_order=wire_order,
            )
        except ValueError as e:
            raise ModuleError(str(e), 400) from None
        order = a_frame_order(self.deps.canopy, cal)
        if flip:
            order = order[::-1]
        explicit = flip or cal.wire_order is not None
        patch = {
            "orientation": cal.orientation,
            "cable_path": cal.cable_path,
            "start_corner": cal.start_corner,
            "wire_order": [[x, y] for x, y in order] if explicit else None,
        }
        if self.deps.save_calibration is None:
            raise ModuleError("Calibration can't be saved here (no registry overlay).", 500)
        try:
            self.deps.save_calibration(self.device_id, patch)
        except ConfigError as e:
            raise ModuleError(str(e), 400) from None
        self.cal = calibration_for(self.deps.canopy, self.entry)
        self.map = module_map(self.deps.canopy, self.cal)
        self.positions = display_positions(self.map)
        self.publish("calibration")
        return self.map

    def set_hold(self, on: bool, name: str | None = None) -> dict:
        self._validate_lease()
        if on:
            if self.sweep_state.state == "running":
                self.sweep_state.state, self.sweep_state.message = "paused", "Paused for Hold."
                self._sweep_wake.set()
            shown = (name or "").strip() or self.look_name()
            self.hold = {"on": True, "name": shown[:80], "since": self.deps.clock.now()}
            # Commands this page already handed to the executor (queued, or waiting to retry after a 429) must
            # not land during the photo: withdraw them. A PUT already on the wire can't be recalled.
            self.target.executor.withdraw(
                self.address,
                lease_token=self.lease.token,
                reason="Withdrawn because Hold went on; nothing more was sent for it.",
            )
        else:
            self.hold = {"on": False, "name": None, "since": None}
        self.publish("hold")
        return dict(self.hold)

    # ---- views -------------------------------------------------------------------------------------------------
    def look_name(self) -> str:
        if self.last_label:
            return self.last_label.removeprefix("card: ")
        return render.describe_look(self.desired_state, self.n, self.caps)

    def lock_json(self) -> dict:
        holder = self.deps.locks.holder(self.address)
        mine = holder is not None and secrets.compare_digest(holder.token, self.lease.token)
        now = self.deps.clock.monotonic()
        return {
            "held": mine,
            "owner": holder.owner if holder else None,
            "idle_s": round(now - holder.last_used, 1) if holder else None,
            "idle_timeout_s": self.deps.locks.idle_timeout_s,
        }

    def _slots_json(self) -> list[dict]:
        reported = self._reported_state()
        mode = reported.get(("color-mode", None))
        selected = reported.get(("color-individual", "custom"))
        recorded = self.entry.slot_hashes
        out = []
        for slot in self.caps.slots or list(R.SLOTS):
            value = reported.get((R.FRAME_CLASS, slot))
            state = {
                ("power", None): "on",
                ("color-mode", None): "individual",
                ("color-individual", "custom"): slot,
                (R.FRAME_CLASS, slot): value,
            }
            inner = value.get(R.FRAME_CLASS, {}) if isinstance(value, Mapping) else {}
            entries = R.frame_entries(value)
            out.append(
                {
                    "slot": slot,
                    "hash": R.slot_hash(value),
                    "recorded_hash": recorded.get(slot),
                    "length": len(entries) if entries is not None else None,
                    "heads": render.head_colors(state, self.n, self.caps),
                    "active": mode == "individual" and selected == slot,
                    "effects": {k: inner.get(k) for k in R.EFFECT_FIELDS}
                    if isinstance(inner, Mapping)
                    else {},
                }
            )
        return out

    def telemetry_json(self) -> dict:
        stats = self.deps.telemetry.command_stats(device_id=self.address, since=self.started_at)
        return {
            "recent": list(self.results),
            "session": stats,
            "last_error": stats.get("last_error") or self.last_read_error,
            "since": self.started_at,
        }

    def snapshot(self) -> dict:
        n = self.n
        reported = self._reported_state()
        desired_colors = render.head_colors(self.desired_state, n, self.caps)
        reported_colors = render.head_colors(reported, n, self.caps)
        heads = [
            {
                "index": i,
                "desired": desired_colors[i],
                "reported": reported_colors[i],
                "paint": self.paint[i].to_json(),
                "match": desired_colors[i] == reported_colors[i],
            }
            for i in range(n)
        ]
        t1_ok, t1_why = self.t1_status()
        entry = self.entry
        enabled = self.effects_enabled()
        caps = self.caps
        return {
            "device_id": self.device_id,
            "label": entry.label or self.device_id,
            "open": True,
            "session_id": self.session_id,
            "simulated": self.target.simulated,
            "driver": self.target.driver.name,
            "lock": self.lock_json(),
            "map": self.map,
            "capabilities": {
                "cct_range": caps.cct_range,
                "brightness_range": caps.brightness_range or (1, 100),
                "speed_range": caps.speed_range,
                "rgb": caps.rgb,
                "color_modes": caps.color_modes,
                "presets": caps.presets,
                "programs": caps.programs,
                "slots": caps.slots,
                "framebuffer_length": caps.framebuffer_length,
                "no_white_hint": caps.no_white_hint,
                "individual": caps.individual,
            },
            "head_count": self.head_count(),
            "t1": {"allowed": t1_ok, "reason": t1_why, "flag": entry.flags.t1_frames},
            "flags": entry.flags.model_dump(),
            "heads": heads,
            "diff": [h["index"] for h in heads if not h["match"]],
            "desired_look": render.describe_look(self.desired_state, n, caps),
            "reported_look": render.describe_look(reported, n, caps) if reported else "no readback yet",
            "reported_at": self.reported_at,
            "read_error": self.last_read_error,
            "busy": self.in_flight > 0,
            "paint_slot": self.paint_slot,
            "slots": self._slots_json(),
            "effects": {
                "enabled": {k: {str(c): label for c, label in v.items()} for k, v in enabled.items()},
                "any_enabled": any(enabled.values()),
                "message": None if any(enabled.values()) else NO_EFFECTS,
            },
            "sweep": self.sweep_state.to_json(),
            "hold": dict(self.hold),
            "look_name": self.look_name(),
            "telemetry": self.telemetry_json(),
            "cards": C.catalog(self.deps.recipes),
            "recipes": {
                name: {
                    "label": r.label,
                    "hex": r.hex,
                    "rgb_only": r.rgb_only.model_dump(),
                    "blend": r.blend.model_dump(),
                }
                for name, r in self.deps.recipes.colors.items()
            },
            "settings": {
                "sweep_step_s": self.sweep_step_s(),
                "min_write_interval_s": self.target.executor.policy.min_write_interval_s,
                "lock_idle_timeout_s": self.deps.locks.idle_timeout_s,
            },
        }

    def export(self) -> dict:
        """The anonymized JSON behind "Copy anonymized JSON": device facts, calibration, slots, telemetry."""
        snap = self.snapshot()
        events = self.deps.telemetry.recent(device_id=self.address, since=self.started_at, limit=100)
        body = {
            "kind": "module-page-session",
            "simulated": snap["simulated"],
            "driver": snap["driver"],
            "device": {"id": self.device_id, "model": self.entry.model},
            "capabilities": snap["capabilities"],
            "head_count": snap["head_count"],
            "t1": snap["t1"],
            "flags": snap["flags"],
            "calibration": snap["map"]["calibration"],
            "desired_look": snap["desired_look"],
            "reported_look": snap["reported_look"],
            "diff": snap["diff"],
            "slots": [
                {k: s[k] for k in ("slot", "hash", "recorded_hash", "length", "effects")}
                for s in snap["slots"]
            ],
            "sweep": snap["sweep"],
            "telemetry": {"session": snap["telemetry"]["session"], "recent": snap["telemetry"]["recent"]},
            "events": events,
        }
        return anonymize(body, self.address)

    def publish(self, event: str, **extra: Any) -> None:
        try:
            self.deps.publish(f"module:{self.device_id}", {"event": event, "state": self.snapshot(), **extra})
        except Exception:
            log.exception("module page: publish failed")


def _plain(e: BaseException) -> str:
    text = str(e).strip()
    return text[:200] if text else type(e).__name__
