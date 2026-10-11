"""A stand-in for the canopy engine (`CanopyEngineAPI`, conductor/engine_api.py) for /ops and /visualizer tests.

Two flavours:
- `RecordingEngine` — records every call and answers with a fixed state; nothing is sent anywhere.
- `FakeEngine` — a small, honest engine on the real services: each mode compiles to one whole-strand look per module
  (the T3 / strand techniques from config/modes.yaml, with their real "Hubspace feature" sentence), sets the
  tracker's desired rows and bursts one command per module through the ENGINE executor (the SimDriver in sim).
  Status comes from the tracker, so READY means the simulated readback matched.

Test-only: the real engine is conductor/canopy.py (P1). This file never runs in production.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import yaml

from conductor import rows as R
from conductor.engine_api import CanopyPhase, CanopyState, ModePlan, ModuleTarget, PlanError, Tier
from conductor.executor_api import Command
from conductor.gate import Purpose
from conductor.render import head_colors

MODES_PATH = Path(__file__).resolve().parent.parent / "config" / "modes.yaml"
CALM = "lowcountry-calm"


def _modes() -> dict:
    return yaml.safe_load(MODES_PATH.read_text())["modes"]


def _look(d: dict, presets: list[str] | None = None) -> R.Look:
    kind = d["kind"]
    if kind == "preset":
        return R.Look(
            "preset",
            brightness=d.get("brightness", 100),
            preset=(presets or ["snowfall"])[0],
            speed=d.get("speed", 0),
        )
    if kind == "rgb":
        return R.Look("rgb", brightness=d.get("brightness", 100), rgb=tuple(d["rgb"]))
    return R.Look("white", brightness=d.get("brightness", 100), cct=max(2700, int(d.get("cct", 2700))))


def compile_t3(mode_id: str, module: str) -> tuple[R.Look, Tier, str]:
    """The mode's look for one canopy position (`r1c2`) at its strand / module-level technique."""
    mode = _modes()[mode_id]
    tech = mode["techniques"]
    row, col = int(module[1]), int(module[3])
    if "strand" in tech:
        t = tech["strand"]
        return _look(t["look"], t.get("presets")), Tier.STRAND, t["feature"]
    t = tech["modules"]
    if "rows" in t:
        look = t["rows"][row]
    elif "columns" in t:
        look = t["columns"][col]
    else:
        look = t["middle_row"] if row == 1 else t["others"]
    return _look(look), Tier.T3, t["feature"]


class RecordingEngine:
    """Records calls; state is fixed unless a test changes it. Nothing is sent to any device."""

    def __init__(self, modules: dict[str, dict] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.guest_input = True
        self.mode: str | None = CALM
        self.modules = modules or {}
        self.raise_on: dict[str, Exception] = {}

    def _call(self, name: str, **kw: Any) -> None:
        self.calls.append((name, kw))
        if name in self.raise_on:
            raise self.raise_on[name]

    def state(self) -> CanopyState:
        return CanopyState(
            mode=self.mode,
            label="Lowcountry Calm" if self.mode == CALM else self.mode,
            tier="strand",
            feature="Tunable white and dimming: all nine Hubspace strands set to a warm 2700 K.",
            phase=CanopyPhase.READY if self.guest_input else CanopyPhase.RESTING,
            guest_input=self.guest_input,
            cooldown_until=None,
            changed_at=None,
            modules=self.modules,
            simulated=True,
        )

    def current_plan(self) -> ModePlan | None:
        return None

    async def request_mode(self, mode_id: str, *, source: str) -> CanopyState:
        self._call("request_mode", mode_id=mode_id, source=source)
        self.mode = mode_id
        return self.state()

    async def reassert_all(self, *, source: str) -> CanopyState:
        self._call("reassert_all", source=source)
        return self.state()

    def pause_guest_input(self, paused: bool, *, source: str) -> CanopyState:
        self._call("pause_guest_input", paused=paused, source=source)
        self.guest_input = not paused
        return self.state()

    async def safe_look(self, *, source: str) -> CanopyState:
        self._call("safe_look", source=source)
        self.mode = CALM
        return self.state()

    async def commission(self, *, source: str) -> dict:
        self._call("commission", source=source)
        return {"written": [], "message": "nothing to commission (recording engine)"}

    async def clear_guest_overrides(self, *, source: str) -> CanopyState:
        self._call("clear_guest_overrides", source=source)
        return self.state()


class FakeEngine:
    """A minimal working engine over the real services (see the module docstring)."""

    def __init__(self, services: Any) -> None:
        self.services = services
        self.mode: str | None = None
        self.plan: ModePlan | None = None
        self.guest_input = True
        self.changed_at: float | None = None
        self.calls: list[tuple[str, dict]] = []
        self._tasks: set[asyncio.Task] = set()
        services.tracker.add_listener(self._changed)  # push CanopyState on WS topic "canopy", like the engine

    def _changed(self, address: str, snapshot: dict) -> None:
        self.services.hub.publish("canopy", self.state().to_json())

    # -- queries
    def _address(self, device_id: str) -> str:
        return self.services.target(device_id, Purpose.ENGINE).address

    def state(self) -> CanopyState:
        modules: dict[str, dict] = {}
        statuses = []
        for entry in self.services.registry.canopy_modules():
            address = self._address(entry.id)
            snap = self.services.tracker.snapshot(address)
            locked = self.services.locks.holder(address) is not None
            status = snap["status"]
            if locked:
                status, reason = (
                    "needs_attention",
                    "Held by the module page: the canopy engine won't write it.",
                )
            else:
                reason = snap["reason"]
            statuses.append(status)
            modules[entry.id] = {
                "status": status,
                "reason": reason,
                "label": entry.label,
                "module": entry.module,
                "locked": locked,
                "simulated": True,
            }
        if not self.guest_input:
            phase, message = CanopyPhase.RESTING, "Guest input is paused by ops."
        elif "updating" in statuses:
            phase, message = CanopyPhase.UPDATING, ""
        elif "needs_attention" in statuses:
            phase, message = CanopyPhase.NEEDS_ATTENTION, "Some modules need attention."
        elif statuses and all(s == "ready" for s in statuses):
            phase, message = CanopyPhase.READY, ""
        else:
            phase, message = CanopyPhase.UPDATING, ""
        plan = self.plan
        return CanopyState(
            mode=self.mode,
            label=plan.label if plan else None,
            tier=str(plan.tier) if plan else None,
            feature=plan.feature if plan else None,
            phase=phase,
            guest_input=self.guest_input,
            cooldown_until=None,
            changed_at=self.changed_at,
            modules=modules,
            simulated=True,
            message=message,
        )

    def current_plan(self) -> ModePlan | None:
        return self.plan

    # -- actions
    def _compile(self, mode_id: str) -> ModePlan:
        modes = _modes()
        if mode_id not in modes:
            raise PlanError(f"There's no mode called {mode_id!r}.")
        targets: dict[str, ModuleTarget] = {}
        tier, feature = Tier.STRAND, ""
        for entry in self.services.registry.canopy_modules():
            look, tier, feature = compile_t3(mode_id, entry.module)
            rows = R.full_state_vector(look)
            targets[entry.id] = ModuleTarget(entry.id, rows, rows, preview=head_colors(rows, 30))
        return ModePlan(mode_id, modes[mode_id]["label"], tier, feature, False, targets)

    def _send(self, plan: ModePlan, ids: list[str] | None = None, source: str = "ops") -> None:
        commands = []
        for device_id, target in plan.modules.items():
            if ids is not None and device_id not in ids:
                continue
            address = self._address(device_id)
            self.services.tracker.set_desired(
                address, target.rows, label=f"mode: {plan.mode_id}", source=source
            )
            commands.append(
                Command(address, target.rows, Purpose.ENGINE, f"mode: {plan.mode_id}", source=source)
            )
        if not commands:
            return
        executor = self.services.executor_for(self.services.engine_driver)
        task = asyncio.get_running_loop().create_task(executor.burst(commands))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def request_mode(self, mode_id: str, *, source: str) -> CanopyState:
        self.calls.append(("request_mode", {"mode_id": mode_id, "source": source}))
        if source == "kiosk" and not self.guest_input:
            raise PlanError("Guest input is paused.")
        self.plan = self._compile(mode_id)
        self.mode = mode_id
        self.changed_at = self.services.clock.now()
        self._send(self.plan, source=source)
        return self.state()

    async def reassert_all(self, *, source: str) -> CanopyState:
        self.calls.append(("reassert_all", {"source": source}))
        if self.plan is not None:
            self._send(self.plan, source=source)
        return self.state()

    def pause_guest_input(self, paused: bool, *, source: str) -> CanopyState:
        self.calls.append(("pause_guest_input", {"paused": paused, "source": source}))
        self.guest_input = not paused
        return self.state()

    async def safe_look(self, *, source: str) -> CanopyState:
        self.calls.append(("safe_look", {"source": source}))
        return await self.request_mode(CALM, source=source)

    async def commission(self, *, source: str) -> dict:
        self.calls.append(("commission", {"source": source}))
        return {"written": [], "message": "The fake engine runs T3/strand looks only: no slots to write."}
