"""A stand-in for the looks compiler (P2) and a small SimDriver world for engine tests.

`FakeCompiler(module_ids)` compiles four modes for any set of logical module ids:
- `lowcountry-calm` — strand white 2700 K (the idle and safe mode), ladder [strand];
- `holiday`         — strand preset `snowfall`, ladder [strand];
- `sunset`          — ladder [canvas, slot, modules]: T1 = select custom-1 + a per-module frame (expect includes the
                      slot object), T2 = select custom-1 with a recorded hand-paint hash, T3 = one RGB per module;
- `golden`          — a motion mode, ladder [canvas, modules], T1 selects custom-3.
Faults: `broken` = {(mode, tier)} compiles rows the simulated cloud refuses (HTTP 400) for `broken_module`;
`uncompilable` = {(mode, tier)} raises PlanError.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from conductor import rows as R
from conductor.canopy import CanopyEngine, EngineDeps
from conductor.clock import VirtualClock
from conductor.config import ConductorSettings, OverlayFile, Registry, RegistryFile
from conductor.drivers.base import StateRow
from conductor.drivers.sim import SimDriver
from conductor.engine_api import ModePlan, ModuleTarget, PlanError, Tier
from conductor.executor import CommandExecutor
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager
from conductor.services import Target
from conductor.sim.settings import SimSettings
from conductor.state import DeviceStateTracker
from conductor.telemetry import Telemetry

MODULES = [f"canopy-r{r}c{c}" for r in range(3) for c in range(3)]
LADDERS = {
    "lowcountry-calm": [Tier.STRAND],
    "holiday": [Tier.STRAND],
    "sunset": [Tier.T1, Tier.T2, Tier.T3],
    "golden": [Tier.T1, Tier.T3],
}
SLOTS = {"sunset": "custom-1", "golden": "custom-3"}


def frame_for(mode_id: str, index: int, length: int) -> dict:
    base = {"sunset": (249, 99, 2), "golden": (255, 140, 0)}[mode_id]
    entries = [
        R.FrameEntry(base[0], max(0, base[1] - 3 * index), (i * 7 + index) % 256, 0, 100, 0)
        for i in range(length)
    ]
    return R.frame_value(None, entries, expected_length=length)


@dataclass
class FakeCompiler:
    module_ids: list[str] = field(default_factory=lambda: list(MODULES))
    frame_length: int = 30
    t2_hashes: dict[tuple[str, str], str] = field(default_factory=dict)
    broken: set[tuple[str, Tier]] = field(default_factory=set)
    broken_module: str | None = None
    uncompilable: set[tuple[str, Tier]] = field(default_factory=set)
    calls: list[tuple[str, Tier]] = field(default_factory=list)

    def modes(self) -> list[str]:
        return list(LADDERS)

    def ladder(self, mode_id: str) -> list[Tier]:
        return list(LADDERS[mode_id])  # KeyError for an unknown mode

    def compile(self, mode_id: str, tier: Tier) -> ModePlan:
        self.calls.append((mode_id, tier))
        if tier not in LADDERS[mode_id] or (mode_id, tier) in self.uncompilable:
            raise PlanError(f"{mode_id} can't compile at {tier} (fake)")
        modules = {}
        for index, device_id in enumerate(self.module_ids):
            modules[device_id] = self._target(mode_id, tier, index, device_id)
        feature = {
            Tier.STRAND: "Tunable white on every strand (fake).",
            Tier.T1: "Every light set individually, saved as a custom pattern (fake).",
            Tier.T2: "A pattern painted in the Hubspace app (fake).",
            Tier.T3: "Each strand set to its own colour (fake).",
        }[tier]
        return ModePlan(
            mode_id=mode_id,
            label=mode_id.replace("-", " ").title(),
            tier=tier,
            feature=feature,
            motion=mode_id == "golden" and tier is Tier.T1,
            modules=modules,
        )

    def _target(self, mode_id: str, tier: Tier, index: int, device_id: str) -> ModuleTarget:
        if mode_id == "lowcountry-calm":
            rows = R.full_state_vector(R.Look("white", cct=2700, brightness=60))
        elif mode_id == "holiday":
            rows = R.full_state_vector(R.Look("preset", preset="snowfall", speed=-8, brightness=70))
        elif tier is Tier.T3:
            rows = R.full_state_vector(R.Look("rgb", rgb=(250, 60 + 20 * index, 10), brightness=90))
        else:
            slot = SLOTS[mode_id]
            rows = R.full_state_vector(R.Look("slot", slot=slot, brightness=90))
            if (mode_id, tier) in self.broken and device_id == self.broken_module:
                rows = [*rows[:-1], StateRow("toggle", "party-mode", "sideways")]  # the cloud answers 400
            if tier is Tier.T1:
                value = frame_for(mode_id, index, self.frame_length)
                return ModuleTarget(
                    device_id,
                    rows,
                    [*rows, R.slot_write(slot, value)],
                    slot=slot,
                    slot_value=value,
                    slot_hash=R.slot_hash(value),
                )
            return ModuleTarget(
                device_id, rows, list(rows), slot=slot, slot_hash=self.t2_hashes.get((device_id, slot))
            )
        if (mode_id, tier) in self.broken and device_id == self.broken_module:
            rows = [*rows[:-1], StateRow("toggle", "party-mode", "sideways")]
        return ModuleTarget(device_id, rows, list(rows))


def canopy_registry(module_ids: list[str] = MODULES) -> Registry:
    devices = [
        {
            "id": d,
            "role": "canopy_module",
            "account": "canopy",
            "module": d.removeprefix("canopy-"),
            "label": f"M{i + 1}",
        }
        for i, d in enumerate(module_ids)
    ]
    return Registry(
        RegistryFile.model_validate({"accounts": {"canopy": {"label": "Canopy"}}, "devices": devices}),
        OverlayFile(),
        None,
    )


class Hub:
    """Records what the engine publishes."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []
        self.snapshots: dict[str, Any] = {}

    @property
    def client_count(self) -> int:
        return 1

    def publish(self, topic: str, data: dict) -> None:
        self.messages.append((topic, data))

    def register_snapshot(self, prefix: str, fn: Any) -> None:
        self.snapshots[prefix] = fn


@dataclass
class World:
    clock: VirtualClock
    registry: Registry
    sim: SimDriver
    executor: CommandExecutor
    tracker: DeviceStateTracker
    telemetry: Telemetry
    locks: DeviceLockManager
    compiler: FakeCompiler
    engine: CanopyEngine
    hub: Hub
    recorded: dict[tuple[str, str], str]

    def address(self, device_id: str) -> str:
        return f"sim-{device_id}"

    def correct(self, device_id: str, plan: ModePlan | None = None) -> bool:
        plan = plan or self.engine.current_plan()
        mt = plan.modules[device_id]
        return not R.compare(mt.expect or mt.rows, self.sim.device_view(self.address(device_id)))

    def all_correct(self, plan: ModePlan | None = None) -> bool:
        plan = plan or self.engine.current_plan()
        return all(self.correct(d, plan) for d in plan.modules)

    async def run_for(self, seconds: float) -> None:
        await self.clock.sleep(seconds)

    async def until(self, predicate, timeout: float, step: float = 0.1) -> float | None:
        """Seconds until `predicate()` holds (polled every `step`), or None after `timeout`."""
        start = self.clock.monotonic()
        while self.clock.monotonic() - start <= timeout:
            if predicate():
                return self.clock.monotonic() - start
            await self.clock.sleep(step)
        return None

    async def close(self) -> None:
        await self.engine.stop()
        await self.executor.close()

    def events(self, kind: str) -> list[dict]:
        return self.telemetry.recent(kind=kind, limit=5000)


def make_world(
    *,
    sim_settings: SimSettings | None = None,
    settings: ConductorSettings | None = None,
    compiler: FakeCompiler | None = None,
    tier_allowed=None,
    seed: int = 0,
) -> World:
    """Build on a running VirtualTimeLoop (inside run_virtual)."""
    clock = VirtualClock()
    registry = canopy_registry()
    sim = SimDriver(registry, clock=clock, settings=sim_settings or SimSettings.dev(), seed=seed)
    telemetry = Telemetry(None, clock)
    locks = DeviceLockManager(clock=clock)
    settings = settings or ConductorSettings()
    executor = CommandExecutor(sim, locks=locks, telemetry=telemetry, clock=clock, policy=settings.executor)
    tracker = DeviceStateTracker(clock, telemetry)
    tracker.attach(executor)
    compiler = compiler or FakeCompiler()
    hub = Hub()
    recorded: dict[tuple[str, str], str] = {}

    def target(device_id: str, purpose: Purpose) -> Target:
        return Target(device_id, registry.address(device_id, "sim"), sim, executor)

    deps = EngineDeps(
        registry=lambda: registry,
        target=target,
        tracker=tracker,
        telemetry=telemetry,
        hub=hub,
        clock=clock,
        settings=settings,
        locks=locks,
        compile=compiler.compile,
        ladder=compiler.ladder,
        modes=compiler.modes,
        record_slot_hash=lambda d, s, h: recorded.__setitem__((d, s), h),
    )
    if tier_allowed is not None:
        deps.tier_allowed = tier_allowed
    engine = CanopyEngine(deps)
    return World(clock, registry, sim, executor, tracker, telemetry, locks, compiler, engine, hub, recorded)


def settings_with(**sections: dict) -> ConductorSettings:
    """ConductorSettings with some fields changed, e.g. settings_with(kiosk={"idle_return_s": 5})."""
    base = ConductorSettings().model_dump()
    for section, values in sections.items():
        base[section].update(values)
    return ConductorSettings.model_validate(base)
