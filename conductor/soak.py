"""`make soak` / `python -m conductor.soak`: 8 simulated hours of the canopy engine on the SimDriver (07 §7).

Runs the real CanopyEngine, Reconciler, CommandExecutor and DeviceStateTracker on a `VirtualTimeLoop` against
`SimSettings.from_acceptance()` — the pinned conditions in `config/sim/acceptance.yaml` (PUT ack 150–800 ms lognormal,
device apply +0.3–1.5 s, 5% of requests → 429, 1% silent drops). Nothing here tunes them.

Schedule (fractions of the run, so a shorter run keeps the same shape):
- commissioning (T1 slot contents), then the engine starts on the idle mode;
- `changes` kiosk mode changes at seeded random times, at least one cooldown apart, clear of the fault windows;
- a button-mash burst: 40 kiosk taps 0.25 s apart, then 20 ops taps 0.2 s apart (ops bypass the cooldown);
- one window of HTTP 500s (the pinned 43 min) and one internet outage (the pinned 30 min), each with an ops mode
  change in the middle, so recovery has to re-apply a look the lights aren't showing.

Measured (ground truth = `SimDriver.device_view`, what the lights actually show):
- time from each accepted change (kiosk, ops, idle) to all nine modules correct — p50/p95/max, target p95 ≤ 5 s;
- the executor's peak queue depth (never more than 2 per device: pending + in flight);
- self-heal time after every silent drop that mattered (the look the module should show was dropped) — ≤ 10 s;
- tier downgrades (0 at 5% throttling);
- time from each fault ending to all nine modules showing the current look — ≤ 60 s;
- the smallest gap between two writes to one device (≥ the 1 s budget) and the final look.

Writes `docs/reports/sim-acceptance.md` and exits non-zero when any target fails.

The world (07 §7 soak validity, review F11): by default the soak runs **the show's own wiring** — `build_services`
on the virtual clock with a temporary `CONDUCTOR_DATA_DIR` and `conductor.show.ShowWiring`, exactly as the app's
lifespan builds it: capabilities read from each module's (simulated) profile in the background, the looks compiler,
the commissioning gate on T1, slot records (hash, origin, effect fields) in the registry overlay, the same ladder and
tier rules. Only the simulator's conditions (pinned) and one soak-only setting differ: `config/sim/soak.yaml` enables a
SIMULATED on-device motion code for Golden Hour, so the all-or-none motion re-trigger (07 §6) is measured too. Nothing
real has been benched; the report says so.

`run_soak(compiler=...)` still takes any object with `compile(mode_id, tier)`, `ladder(mode_id)` and `modes()` and
then runs the bare engine on it (the older world: every tier allowed, no overlay); `BuiltinCompiler` below is kept for
that path.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import shutil
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from conductor import rows as R
from conductor.clock import RealClock, VirtualClock, run_virtual
from conductor.config import CONFIG_DIR, Registry, load_registry, load_settings
from conductor.drivers.base import StateRow
from conductor.engine_api import ModePlan, ModuleTarget, PlanError, Tier
from conductor.executor import CommandExecutor
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager
from conductor.services import Target
from conductor.sim.settings import ACCEPTANCE_FILE, SimSettings
from conductor.state import DeviceStateTracker
from conductor.telemetry import Telemetry, percentile

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "docs" / "reports" / "sim-acceptance.md"
SOAK_SETTINGS = CONFIG_DIR / "sim" / "soak.yaml"
SHOW_DESCRIPTION = (
    "the show's own wiring (build_services + conductor.show.ShowWiring → conductor.looks): profiles read in the "
    "background, the commissioning gate on T1, slot records in the overlay"
)
POLL_S = 0.1  # how often a measurement looks at the lights
WATCH_LIMIT_S = 120.0  # a measurement gives up (and fails) after this long

_TIER_NAMES = {"canvas": Tier.T1, "slot": Tier.T2, "modules": Tier.T3, "strand": Tier.STRAND}


# ---- the built-in compiler (used when conductor.looks isn't available) ------------------------------------------
class BuiltinCompiler:
    """Compiles `config/modes.yaml` without the looks composer: strand looks as written, module-level looks per
    canopy position, and canvas (T1) as one colour per module with a brightness ramp along the wire. T2 is left
    out (nothing is hand-painted in the simulator)."""

    description = "built-in soak compiler (strand + module-level + one-colour-per-module canvas frames)"
    CCT_FLOOR = 2700  # the simulator profile's range is 2700–6500 K

    def __init__(self, registry: Registry, config_dir: Path = CONFIG_DIR, frame_length: int = 30) -> None:
        with open(config_dir / "modes.yaml", encoding="utf-8") as f:
            self._modes: dict[str, dict] = (yaml.safe_load(f) or {})["modes"]
        self._modules = registry.canopy_modules()
        self._frame_length = frame_length

    def modes(self) -> list[str]:
        return list(self._modes)

    def ladder(self, mode_id: str) -> list[Tier]:
        mode = self._modes[mode_id]
        return [_TIER_NAMES[t] for t in mode["ladder"] if t in _TIER_NAMES and t != "slot"]

    def compile(self, mode_id: str, tier: Tier) -> ModePlan:
        mode = self._modes[mode_id]
        name = {v: k for k, v in _TIER_NAMES.items()}[tier]
        technique = (mode.get("techniques") or {}).get(name)
        if technique is None or tier not in self.ladder(mode_id):
            raise PlanError(f"{mode.get('label', mode_id)} has no {name} technique.")
        notes: list[str] = []
        modules: dict[str, ModuleTarget] = {}
        for index, entry in enumerate(self._modules):
            look = self._module_look(mode, name, technique, entry.module or "r0c0", notes)
            if tier is Tier.T1:
                slot = mode["slot"]
                value = R.frame_value(None, self._frame(look, index), expected_length=self._frame_length)
                rows = R.full_state_vector(
                    R.Look("slot", slot=slot, brightness=technique.get("brightness", 90))
                )
                expect = [*rows, R.slot_write(slot, value)]
                modules[entry.id] = ModuleTarget(
                    entry.id, rows, expect, slot=slot, slot_value=value, slot_hash=R.slot_hash(value)
                )
            else:
                rows = R.full_state_vector(look)
                modules[entry.id] = ModuleTarget(entry.id, rows, list(rows))
        return ModePlan(
            mode_id=mode_id,
            label=mode.get("label", mode_id),
            tier=tier,
            feature=technique.get("feature", ""),
            motion=bool(mode.get("motion")) and tier is Tier.T1,
            modules=modules,
            notes=sorted(set(notes)),
        )

    def _module_look(self, mode: dict, name: str, technique: dict, position: str, notes: list[str]) -> R.Look:
        row, col = int(position[1]), int(position[3])
        if name == "strand":
            raw = dict(technique["look"])
            if raw.get("kind") == "preset" and not raw.get("preset"):
                raw["preset"] = (technique.get("presets") or ["snowfall"])[0]
        elif "rows" in technique:
            raw = dict(technique["rows"][row % len(technique["rows"])])
        elif "columns" in technique:
            raw = dict(technique["columns"][col % len(technique["columns"])])
        elif "middle_row" in technique:
            raw = dict(technique["middle_row"] if row == 1 else technique["others"])
        else:  # canvas: borrow the module-level look for this position as the frame's colour
            modules = (mode.get("techniques") or {}).get("modules") or {}
            return (
                self._module_look(mode, "modules", modules, position, notes)
                if modules
                else R.Look("white", cct=2700)
            )
        if raw.get("kind") == "white" and raw.get("cct", self.CCT_FLOOR) < self.CCT_FLOOR:
            notes.append(f"{raw['cct']} K clamped to the device floor {self.CCT_FLOOR} K")
            raw["cct"] = self.CCT_FLOOR
        return R.Look.from_json(raw)

    def _frame(self, look: R.Look, module_index: int) -> list[R.FrameEntry]:
        out = []
        for i in range(self._frame_length):
            level = 60 + (40 * i) // max(1, self._frame_length - 1)
            if look.kind == "rgb" and look.rgb:
                r, g, b = look.rgb
                out.append(R.FrameEntry(r, g, b, 0, level, 0))
            else:
                out.append(R.FrameEntry(0, 0, 0, look.cct or 2700, 0, level))
        return out


def default_compiler(registry: Registry) -> tuple[Any, str]:
    """The looks compiler when `conductor.looks` offers one the engine can call, else the built-in compiler."""
    try:
        from conductor import looks  # type: ignore[attr-defined]
    except ImportError:
        return BuiltinCompiler(
            registry
        ), BuiltinCompiler.description + " — conductor.looks isn't in this build"
    factory = getattr(looks, "engine_compiler", None)
    if callable(factory):
        try:
            return factory(registry=registry), "conductor.looks.engine_compiler"
        except Exception as e:  # pragma: no cover - depends on the looks package
            log.exception("conductor.looks.engine_compiler failed; using the built-in compiler")
            return BuiltinCompiler(registry), f"{BuiltinCompiler.description} — engine_compiler failed: {e}"
    compile_mode = getattr(looks, "compile_mode", None)
    context = next(
        (
            getattr(looks, n)
            for n in ("build_context", "default_context", "make_context")
            if hasattr(looks, n)
        ),
        None,
    )
    ladder = next(
        (getattr(looks, n) for n in ("ladder", "mode_ladder", "ladder_for") if hasattr(looks, n)), None
    )
    if callable(compile_mode) and callable(context) and callable(ladder):
        try:
            ctx = context(registry=registry)
            fallback = BuiltinCompiler(registry)

            class _Looks:
                def compile(self, mode_id: str, tier: Tier) -> ModePlan:
                    return compile_mode(mode_id, tier, ctx)

                def ladder(self, mode_id: str) -> list[Tier]:
                    return list(ladder(mode_id))

                def modes(self) -> list[str]:
                    return fallback.modes()

            return _Looks(), "conductor.looks.compile_mode"
        except Exception as e:  # pragma: no cover - depends on the looks package
            log.exception("conductor.looks.compile_mode wiring failed; using the built-in compiler")
            return BuiltinCompiler(registry), f"{BuiltinCompiler.description} — conductor.looks failed: {e}"
    return BuiltinCompiler(registry), (
        f"{BuiltinCompiler.description} — conductor.looks has no engine_compiler(registry=...) or "
        "compile_mode + context + ladder the soak can call"
    )


# ---- the soak world ------------------------------------------------------------------------------------------------
class _Hub:
    client_count = 0

    def publish(self, topic: str, data: dict) -> None:  # nobody is watching in the soak
        return None


@dataclass
class SoakConfig:
    hours: float = 8.0
    changes: int = 100
    seed: int = 0
    mash_kiosk_taps: int = 40
    mash_ops_taps: int = 20
    sim_settings: SimSettings = field(default_factory=SimSettings.from_acceptance)
    targets: dict = field(default_factory=dict)  # from acceptance.yaml
    report_path: Path | None = REPORT_PATH


@dataclass
class _World:
    clock: VirtualClock
    registry: Registry
    sim: Any
    executor: CommandExecutor
    tracker: DeviceStateTracker
    telemetry: Telemetry
    engine: Any
    modules: list[str]
    addresses: dict[str, str]
    wiring: Any = None  # ShowWiring (the show's own wiring), when the soak runs it
    services: Any = None
    cleanup: Any = None  # a callable removing the temporary data/config directories
    motion: dict | None = None  # the SIMULATED motion setting in force


def _soak_config_dir(motion: dict | None) -> Path:
    """A copy of config/ with the soak-only SIMULATED motion code applied (modes.yaml + conductor.yaml)."""
    root = Path(tempfile.mkdtemp(prefix="canopy-soak-config-"))
    config = root / "config"
    shutil.copytree(CONFIG_DIR, config)
    if motion:
        modes_path = config / "modes.yaml"
        modes = yaml.safe_load(modes_path.read_text(encoding="utf-8"))
        canvas = modes["modes"][motion["mode"]]["techniques"]["canvas"]
        canvas["motion_effect"] = {
            **(canvas.get("motion_effect") or {}),
            "motionEffect": int(motion["motionEffect"]),
        }
        modes_path.write_text(yaml.safe_dump(modes, sort_keys=False, allow_unicode=True), encoding="utf-8")
        settings_path = config / "conductor.yaml"
        settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        enabled = settings.setdefault("effects", {}).setdefault("enabled", {})
        enabled.setdefault("motionEffect", {})[int(motion["motionEffect"])] = str(
            motion.get("note", "SIMULATED")
        )
        enabled.setdefault("brightnessEffect", {})
        settings_path.write_text(
            yaml.safe_dump(settings, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    return config


def load_soak_motion(path: Path = SOAK_SETTINGS) -> dict | None:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        motion = (yaml.safe_load(f) or {}).get("motion")
    return motion or None


async def _build_show(cfg: SoakConfig) -> _World:
    """The app's own wiring on the virtual clock: build_services + ShowWiring, a temp data dir (the overlay)."""
    from conductor.services import build_services
    from conductor.show import ShowWiring

    motion = load_soak_motion()
    config_dir = _soak_config_dir(motion)
    data = Path(tempfile.mkdtemp(prefix="canopy-soak-data-"))
    clock = VirtualClock()
    services = build_services(
        {"CONDUCTOR_DATA_DIR": str(data)}, clock=clock, config_dir=config_dir, sim_settings=cfg.sim_settings
    )
    services.telemetry.dir = None  # events stay in memory: the soak measures the engine, not disk appends
    sim = services.sim
    sim.seed = cfg.seed
    for rng, name in ((sim._fault_rng, "faults"), (sim._latency_rng, "latency"), (sim._world_rng, "world")):
        rng.seed(f"{cfg.seed}:{name}")  # the same seeded streams the bare world uses
    await services.start()
    wiring = ShowWiring(services)
    services.extras["show"] = wiring
    engine = await wiring.start()
    modules = [d.id for d in services.registry.canopy_modules()]

    def cleanup() -> None:
        shutil.rmtree(config_dir.parent, ignore_errors=True)
        shutil.rmtree(data, ignore_errors=True)

    return _World(
        clock,
        services.registry,
        sim,
        services.executor_for(sim),
        services.tracker,
        services.telemetry,
        engine,
        modules,
        {d: f"sim-{d}" for d in modules},
        wiring=wiring,
        services=services,
        cleanup=cleanup,
        motion=motion,
    )


def _build(cfg: SoakConfig, compiler: Any) -> _World:
    from conductor.canopy import CanopyEngine, EngineDeps
    from conductor.drivers.sim import SimDriver

    clock = VirtualClock()
    registry = load_registry(CONFIG_DIR, None)
    settings = load_settings(CONFIG_DIR)
    sim = SimDriver(registry, clock=clock, settings=cfg.sim_settings, seed=cfg.seed)
    telemetry = Telemetry(None, clock)
    locks = DeviceLockManager(clock=clock)
    executor = CommandExecutor(sim, locks=locks, telemetry=telemetry, clock=clock, policy=settings.executor)
    tracker = DeviceStateTracker(clock, telemetry)
    tracker.attach(executor)

    def target(device_id: str, purpose: Purpose) -> Target:
        return Target(device_id, registry.address(device_id, "sim"), sim, executor)

    engine = CanopyEngine(
        EngineDeps(
            registry=lambda: registry,
            target=target,
            tracker=tracker,
            telemetry=telemetry,
            hub=_Hub(),
            clock=clock,
            settings=settings,
            locks=locks,
            compile=compiler.compile,
            ladder=compiler.ladder,
            modes=compiler.modes,
            simulated=lambda: True,
        )
    )
    modules = [d.id for d in registry.canopy_modules()]
    return _World(
        clock, registry, sim, executor, tracker, telemetry, engine, modules, {d: f"sim-{d}" for d in modules}
    )


def _correct(world: _World, plan: ModePlan, device_id: str) -> bool:
    mt = plan.modules[device_id]
    view = world.sim.device_view(world.addresses[device_id])
    return not R.compare(mt.expect or mt.rows, view)


def _all_correct(world: _World, plan: ModePlan) -> bool:
    return all(_correct(world, plan, d) for d in plan.modules)


async def _soak(cfg: SoakConfig, compiler: Any) -> dict:
    world = await _build_show(cfg) if compiler is None else _build(cfg, compiler)
    engine, clock, sim = world.engine, world.clock, world.sim
    if compiler is None:
        compiler = SimpleNamespace(modes=world.wiring.mode_ids)  # the schedule only needs the mode ids
        # the show commissions its slots once every profile is read (as the app does in sim): wait for it
        while world.wiring.last_commission is None and clock.monotonic() < 600:
            await clock.sleep(1.0)
    rng = random.Random(f"{cfg.seed}:schedule")
    duration = cfg.hours * 3600.0
    window_s = cfg.sim_settings.http_500_window_s or 43 * 60
    outage_s = cfg.sim_settings.internet_outage_s or 30 * 60
    if cfg.hours < 8:  # short runs (tests) scale the fault windows with the run
        window_s = max(120.0, window_s * cfg.hours / 8)
        outage_s = max(120.0, outage_s * cfg.hours / 8)
    t0 = clock.monotonic()
    windows = {
        "http_500": (t0 + 0.30 * duration, t0 + 0.30 * duration + window_s),
        "outage": (t0 + 0.65 * duration, t0 + 0.65 * duration + outage_s),
    }
    mash_at = t0 + 0.15 * duration

    results: dict[str, Any] = {
        "changes": [],  # {source, mode, seconds|None, fault}
        "abandoned": 0,
        "refused": {"kiosk": 0, "during_faults": 0},
        "heals": [],
        "heal_timeouts": 0,
        "recoveries": {},
        "max_queue": 0,
        "min_write_gap_s": None,
        "wiring": {
            "show_wiring": world.wiring is not None,
            "motion": world.motion,
            "motion_plans": 0,  # accepted changes that installed a motion plan (all-or-none re-trigger)
            "tiers": {},  # accepted changes per tier
        },
    }
    tasks: set[asyncio.Task] = set()

    def spawn(coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def in_fault(t: float, pad_after: float = 0.0) -> str | None:
        for name, (a, b) in windows.items():
            if a <= t < b + pad_after:
                return name
        return None

    # -- instruments ----------------------------------------------------------------------------------------------
    def sample_queue() -> None:
        results["max_queue"] = max(results["max_queue"], world.executor.queue_depth())

    world.executor.add_submit_listener(lambda _cmd: asyncio.get_running_loop().call_soon(sample_queue))

    last_write: dict[str, float] = {}
    original_write = sim.write

    async def timed_write(device_id: str, rows: list[StateRow], *, purpose: Purpose):
        now = clock.monotonic()
        if device_id in last_write:
            gap = now - last_write[device_id]
            if results["min_write_gap_s"] is None or gap < results["min_write_gap_s"]:
                results["min_write_gap_s"] = gap
        last_write[device_id] = now
        return await original_write(device_id, rows, purpose=purpose)

    sim.write = timed_write  # type: ignore[method-assign]

    async def watch_change(plan: ModePlan, source: str, started: float, fault: str | None) -> None:
        while clock.monotonic() - started < WATCH_LIMIT_S:
            if engine.current_plan() is not plan:
                results["abandoned"] += 1  # superseded before it settled (button mash)
                return
            if _all_correct(world, plan):
                results["changes"].append(
                    {
                        "source": source,
                        "mode": plan.mode_id,
                        "seconds": clock.monotonic() - started,
                        "fault": fault,
                    }
                )
                return
            await clock.sleep(POLL_S)
        results["changes"].append({"source": source, "mode": plan.mode_id, "seconds": None, "fault": fault})

    def on_event(event: dict) -> None:
        if event.get("kind") != "mode":
            return
        plan = engine.current_plan()
        source = event.get("source", "")
        if plan is None or source in ("reconciler", "recovery"):
            return
        results["wiring"]["motion_plans"] += int(bool(plan.motion))
        tiers = results["wiring"]["tiers"]
        tiers[str(plan.tier)] = tiers.get(str(plan.tier), 0) + 1
        now = clock.monotonic()
        spawn(watch_change(plan, source, now, in_fault(now, pad_after=60.0) or engine.outage and "resting"))

    world.telemetry.subscribe(on_event)

    original_accept = sim.cloud.accept_wire

    def accept_wire(body: Any, *, silent_drop: bool = False):
        response = original_accept(body, silent_drop=silent_drop)
        if silent_drop and response.ok:
            address = str(body.get("metadeviceId"))
            dropped = [StateRow.from_wire(v) for v in body.get("values", [])]
            spawn(watch_heal(address, dropped, clock.monotonic()))
        return response

    sim.cloud.accept_wire = accept_wire  # type: ignore[method-assign]

    async def watch_heal(address: str, dropped: list[StateRow], started: float) -> None:
        plan = engine.current_plan()
        device_id = address.removeprefix("sim-")
        if plan is None or device_id not in plan.modules:
            return
        mt = plan.modules[device_id]
        if R.compare(mt.rows, dropped) or not R.compare(dropped, sim.device_view(address)):
            return  # not the current look (a superseded command or a slot write), or already showing it
        while clock.monotonic() - started < WATCH_LIMIT_S:
            if engine.current_plan() is not plan:
                return
            if _correct(world, plan, device_id):
                results["heals"].append(clock.monotonic() - started)
                return
            await clock.sleep(POLL_S)
        results["heal_timeouts"] += 1

    async def watch_recovery(name: str, ended: float) -> None:
        while clock.monotonic() - ended < WATCH_LIMIT_S * 2:
            plan = engine.current_plan()
            if engine.outage is None and plan is not None and _all_correct(world, plan):
                results["recoveries"][name] = clock.monotonic() - ended
                return
            await clock.sleep(POLL_S)
        results["recoveries"][name] = None

    # -- schedule -------------------------------------------------------------------------------------------------
    cooldown = world.engine.deps.settings.kiosk.cooldown_s
    times: list[float] = []
    lo, hi = t0 + 60.0, t0 + duration - 600.0
    attempts = 0
    while len(times) < cfg.changes and attempts < cfg.changes * 1000:
        attempts += 1
        t = rng.uniform(lo, hi)
        if in_fault(t - 30.0, pad_after=150.0) or in_fault(t, pad_after=150.0) or abs(t - mash_at) < 120.0:
            continue
        if any(abs(t - other) < cooldown + 2.0 for other in times):
            continue
        times.append(t)
    times.sort()
    modes = compiler.modes()

    async def at(t: float) -> None:
        delay = t - clock.monotonic()
        if delay > 0:
            await clock.sleep(delay)

    async def kiosk_changes() -> None:
        for t in times:
            await at(t)
            current = engine.current_plan()
            choices = [m for m in modes if current is None or m != current.mode_id] or modes
            state = await engine.request_mode(rng.choice(choices), source="kiosk")
            if state.refused:
                results["refused"]["kiosk"] += 1

    async def mash() -> None:
        await at(mash_at)
        for i in range(cfg.mash_kiosk_taps):
            state = await engine.request_mode(modes[i % len(modes)], source="kiosk")
            if state.refused:
                results["refused"]["kiosk_mash"] = results["refused"].get("kiosk_mash", 0) + 1
            await clock.sleep(0.25)
        for i in range(cfg.mash_ops_taps):
            await engine.request_mode(modes[(i * 7 + 3) % len(modes)], source="ops")
            await clock.sleep(0.2)

    async def faults() -> None:
        start, end = windows["http_500"]
        await at(start)
        sim.faults.start_500_window(end - start)
        await at(start + (end - start) / 2)
        await engine.request_mode(_other(modes, engine), source="ops")
        await at(end)
        spawn(watch_recovery("http_500", clock.monotonic()))
        start, end = windows["outage"]
        await at(start)
        sim.faults.set_outage(True)
        await at(start + (end - start) / 3)
        state = await engine.request_mode(_other(modes, engine), source="kiosk")
        if state.refused:
            results["refused"]["during_faults"] += 1
        await engine.request_mode(_other(modes, engine), source="ops")
        await at(end)
        sim.faults.set_outage(False)
        spawn(watch_recovery("outage", clock.monotonic()))

    # -- run ------------------------------------------------------------------------------------------------------
    if world.wiring is not None:
        commission = world.wiring.last_commission or {"ok": False, "message": "never ran", "modes": {}}
    else:
        commission = await engine.commission(source="soak")
        await engine.start()
    drivers = [asyncio.get_running_loop().create_task(c) for c in (kiosk_changes(), mash(), faults())]
    await at(t0 + duration)
    for task in drivers:
        if not task.done():
            task.cancel()
    await asyncio.gather(*drivers, return_exceptions=True)
    # let the last measurements finish, then look at the lights one last time
    for _ in range(int(WATCH_LIMIT_S * 2 / 5)):
        if not tasks:
            break
        await clock.sleep(5.0)
    final_plan = engine.current_plan()
    results["final_correct"] = final_plan is not None and _all_correct(world, final_plan)
    results["final_mode"] = final_plan.mode_id if final_plan else None
    results["final_tier"] = str(final_plan.tier) if final_plan else None
    if world.wiring is not None:
        await world.wiring.close()
        await world.services.close()
        world.cleanup()
    else:
        await engine.stop()
        await world.executor.close()
    for task in list(tasks):
        task.cancel()
    results["commission"] = {
        "ok": commission.get("ok"),
        "message": commission.get("message"),
        "modes": sorted(commission.get("modes", {})),
    }
    results["tier_changes"] = list(engine.stats["tier_changes"])
    results["outages"] = list(engine.stats["outages"])
    results["early_reasserts"] = engine.stats["early_reasserts"]
    results["reconciler"] = dict(engine.reconciler.stats)
    results["sim"] = {
        k: v for k, v in sim.stats().items() if k not in ("writes_per_device", "min_interval_violations")
    }
    results["windows_s"] = {"http_500": window_s, "outage": outage_s}
    results["duration_s"] = duration
    return results


def _other(modes: list[str], engine: Any) -> str:
    current = engine.current_plan()
    return next((m for m in modes if current is None or m != current.mode_id), modes[0])


# ---- verdicts and the report ---------------------------------------------------------------------------------------
def evaluate(results: dict, targets: dict) -> list[dict]:
    """One row per target: name, p50/p95/max (where they apply), target text, PASS/FAIL."""
    p95_target = float(targets.get("all_modules_correct_p95_s", 5))
    heal_target = float(targets.get("self_heal_max_s", 10))
    recovery_target = float(targets.get("recovery_after_outage_max_s", 60))
    downgrade_target = int(targets.get("tier_downgrades_at_5pct_throttle", 0))
    clean = [c for c in results["changes"] if not c["fault"]]
    times = [c["seconds"] for c in clean if c["seconds"] is not None]
    unsettled = [c for c in clean if c["seconds"] is None]
    rows = []

    def stats(values: list[float]) -> tuple[float | None, float | None, float | None]:
        return percentile(values, 50), percentile(values, 95), (max(values) if values else None)

    p50, p95, mx = stats(times)
    rows.append(
        {
            "name": f"All nine modules correct after a mode change ({len(times)} changes)",
            "p50": p50,
            "p95": p95,
            "max": mx,
            "target": f"p95 ≤ {p95_target:g} s, every change settles",
            "pass": bool(times) and p95 is not None and p95 <= p95_target and not unsettled,
        }
    )
    rows.append(
        {
            "name": "Executor queue depth (pending + in flight, all devices)",
            "max": results["max_queue"],
            "target": "≤ 18 (2 per device) — never grows",
            "pass": results["max_queue"] <= 18,
        }
    )
    heals = results["heals"]
    p50, p95, mx = stats(heals)
    rows.append(
        {
            "name": f"Self-heal after a silent drop ({len(heals)} drops that mattered)",
            "p50": p50,
            "p95": p95,
            "max": mx,
            "target": f"every one ≤ {heal_target:g} s",
            "pass": results["heal_timeouts"] == 0 and (mx is None or mx <= heal_target),
        }
    )
    downgrades = [t for t in results["tier_changes"] if t["direction"] == "down"]
    rows.append(
        {
            "name": "Tier downgrades at 5% throttling (whole soak)",
            "max": len(downgrades),
            "target": f"= {downgrade_target}",
            "pass": len(downgrades) <= downgrade_target,
        }
    )
    for name, label in (("outage", "internet outage"), ("http_500", "window of HTTP 500s")):
        value = results["recoveries"].get(name)
        minutes = results["windows_s"][name] / 60
        rows.append(
            {
                "name": f"Current look re-applied after the {minutes:.0f}-min {label}",
                "max": value,
                "target": f"≤ {recovery_target:g} s after it ends",
                "pass": value is not None and value <= recovery_target,
            }
        )
    gap = results["min_write_gap_s"]
    rows.append(
        {
            "name": "Smallest gap between two writes to one device",
            "max": gap,
            "target": "≥ 1.0 s (rate budget)",
            "pass": gap is None or gap >= 1.0 - 1e-6,
        }
    )
    rows.append(
        {
            "name": "Final look correct on all nine modules",
            "max": results["final_mode"],
            "target": "yes",
            "pass": bool(results["final_correct"]),
        }
    )
    return rows


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def render_report(results: dict, verdicts: list[dict], meta: dict) -> str:
    passed = all(v["pass"] for v in verdicts)
    s = meta["sim_settings"]
    windows = results["windows_s"]
    commission = results["commission"]
    lines = [
        "# Simulator acceptance soak",
        "",
        f"_Generated by `python -m conductor.soak` on {meta['generated']} — {'**PASS**' if passed else '**FAIL**'}._",
        "",
        (
            f"- Simulated time: {results['duration_s'] / 3600:g} h on `VirtualTimeLoop` "
            f"({meta['wall_s']:.1f} s wall). Seed {meta['seed']}."
        ),
        (
            f"- Conditions: `{meta['acceptance_file']}` (pinned, not tuned): PUT ack {s.ack_ms_min:g}–"
            f"{s.ack_ms_max:g} ms {s.ack_distribution}, device apply +{s.device_apply_extra_s_min:g}–"
            f"{s.device_apply_extra_s_max:g} s, {s.throttle_429_rate:.0%} of requests → 429, "
            f"{s.silent_drop_rate:.0%} silent drops, readback semantics `{s.readback_semantics}`."
        ),
        f"- World: {meta['compiler']}.",
        _motion_line(results),
        (
            f"- Faults: one {windows['http_500'] / 60:.0f}-min window of HTTP 500s and one "
            f"{windows['outage'] / 60:.0f}-min internet outage, each with an ops mode change in the middle; "
            "a button-mash burst of kiosk and ops taps."
        ),
        f"- Commissioning: {commission['message']} (modes: {', '.join(commission['modes']) or 'none'}).",
        "",
        "| Measure | p50 | p95 | max | Target | Result |",
        "|---|---|---|---|---|---|",
    ]
    for v in verdicts:
        lines.append(
            f"| {v['name']} | {_fmt(v.get('p50'))} | {_fmt(v.get('p95'))} | {_fmt(v.get('max'))} | "
            f"{v['target']} | {'PASS' if v['pass'] else 'FAIL'} |"
        )
    by_source: dict[str, int] = {}
    for c in results["changes"]:
        by_source[c["source"]] = by_source.get(c["source"], 0) + 1
    faulted = [c for c in results["changes"] if c["fault"]]
    sim, rec, refused = results["sim"], results["reconciler"], results["refused"]
    outages = (
        "; ".join(f"{o['duration_s'] / 60:.1f} min ({o['reason']})" for o in results["outages"]) or "none"
    )
    tiers = "; ".join(f"{t['mode']} {t['from']}→{t['to']}" for t in results["tier_changes"])
    lines += [
        "",
        (
            "Times are seconds from the request to the moment every module's lights (the simulator's "
            "`device_view`) show the mode's expected rows. Changes made while a fault was active are left out of "
            "the percentiles and listed below."
        ),
        "",
        "## Details",
        (
            f"- Changes measured by source: {', '.join(f'{k} {n}' for k, n in sorted(by_source.items()))}; "
            f"superseded before settling (button mash): {results['abandoned']}; kiosk taps refused (cooldown): "
            f"{refused.get('kiosk', 0)} scheduled, {refused.get('kiosk_mash', 0)} during the mash; kiosk taps "
            f"refused while resting: {refused['during_faults']}."
        ),
        f"- Changes during a fault (not in the percentiles): {len(faulted)}.",
        f"- Outages the engine detected (resting → recovered): {outages}.",
        (
            f"- Tier changes: {len(results['tier_changes'])}{' — ' + tiers if tiers else ''}. "
            f"Final look: {results['final_mode']} at {results['final_tier']}."
        ),
        (
            f"- Reconciler: {rec['reasserts']} module re-asserts, {results['early_reasserts']} early re-asserts "
            f"after a verification readback differed, {rec['true_ups']} true-ups, {rec['probes']} outage probes, "
            f"{rec['slot_rewrites']} slot rewrites."
        ),
        (
            f"- Simulator: {sim['writes']} writes, {sim['reads']} reads, {sim['http_429']} × 429, "
            f"{sim['http_500']} × 500, {sim['connection_failures']} connection failures, "
            f"{sim['silent_drops']} silent drops, {sim['http_400']} × 400."
        ),
        "",
        (
            "Re-run with `make soak`. The acceptance conditions live in `config/sim/acceptance.yaml` and need "
            "Alon's sign-off to change."
        ),
        "",
    ]
    return "\n".join(lines)


def _motion_line(results: dict) -> str:
    wiring = results.get("wiring") or {}
    motion = wiring.get("motion")
    tiers = ", ".join(f"{k} {n}" for k, n in sorted((wiring.get("tiers") or {}).items())) or "none"
    if not motion:
        return f"- Motion: no motion code enabled; accepted changes by tier: {tiers}."
    return (
        f"- Motion: **SIMULATED** — `config/sim/soak.yaml` enables motionEffect {motion['motionEffect']} for "
        f"{motion['mode']} (soak only; nothing has been benched and the simulator doesn't animate). "
        f"{wiring.get('motion_plans', 0)} accepted changes installed a motion plan (all-or-none re-trigger). "
        f"Accepted changes by tier: {tiers}."
    )


def load_targets(path: Path = ACCEPTANCE_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("targets") or {}


def run_soak(cfg: SoakConfig | None = None, compiler: Any = None) -> tuple[dict, list[dict], dict]:
    """Run the soak on a fresh VirtualTimeLoop. Returns (results, verdicts, meta) and writes the report."""
    cfg = cfg or SoakConfig()
    wall = RealClock()
    started = wall.monotonic()
    description = "the compiler passed to run_soak (the bare engine: every tier allowed, no overlay)"
    results = run_virtual(lambda: _soak(cfg, compiler))
    if results["wiring"]["show_wiring"]:
        description = SHOW_DESCRIPTION
    verdicts = evaluate(results, cfg.targets or load_targets())
    meta = {
        "generated": datetime.fromtimestamp(wall.now(), UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "wall_s": wall.monotonic() - started,
        "seed": cfg.seed,
        "compiler": description,
        "sim_settings": cfg.sim_settings,
        "acceptance_file": ACCEPTANCE_FILE.relative_to(ROOT).as_posix()
        if ACCEPTANCE_FILE.is_relative_to(ROOT)
        else str(ACCEPTANCE_FILE),
    }
    if cfg.report_path is not None:
        cfg.report_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.report_path.write_text(render_report(results, verdicts, meta), encoding="utf-8")
    return results, verdicts, meta


def main(argv: list[str] | None = None, *, compiler_factory: Callable[[], Any] | None = None) -> int:
    parser = argparse.ArgumentParser(description="8 simulated hours of the canopy engine (docs/reports).")
    parser.add_argument(
        "--hours", type=float, default=None, help="simulated hours (default: acceptance.yaml)"
    )
    parser.add_argument("--changes", type=int, default=None, help="mode changes (default: acceptance.yaml)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    with open(ACCEPTANCE_FILE, encoding="utf-8") as f:
        soak_cfg = (yaml.safe_load(f) or {}).get("soak") or {}
    cfg = SoakConfig(
        hours=args.hours if args.hours is not None else float(soak_cfg.get("simulated_hours", 8)),
        changes=args.changes if args.changes is not None else int(soak_cfg.get("mode_changes", 100)),
        seed=args.seed,
        report_path=args.report,
    )
    _results, verdicts, meta = run_soak(cfg, compiler_factory() if compiler_factory else None)
    width = max(len(v["name"]) for v in verdicts)
    for v in verdicts:
        numbers = " ".join(f"{k}={_fmt(v.get(k))}" for k in ("p50", "p95", "max") if v.get(k) is not None)
        print(f"{'PASS' if v['pass'] else 'FAIL'}  {v['name']:<{width}}  {numbers}  (target {v['target']})")
    print(f"Report: {args.report} — {meta['wall_s']:.1f} s wall, compiler: {meta['compiler']}")
    return 0 if all(v["pass"] for v in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
