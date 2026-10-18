"""07 §1: every writer path is denied for a non-allowlisted device — proven on the wire.

Each entry in WRITER_PATHS drives one production code path that can write to a device, pointed at SPARE_MD: a device
that is registered and writable but NOT in HUBSPACE_DEVICE_ALLOWLIST. The runtime is `live` (the most permissive
mode) with a real AferoDriver on the fake transport. The test fails if any PUT for that device reaches the wire.

**Every new writer path must add itself here** (reconciler, idle hold, slot manager, bench jobs, module page,
the Lab's try-on, the Lab publish's slot repaint, ...). `test_every_writer_module_is_listed` fails when a module that
submits commands isn't covered.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from fake_afero import BENCH_MD, SPARE_MD, make_world

from conductor import rows as R
from conductor.bench.runner import BenchBusy, BenchRunner
from conductor.clock import run_virtual
from conductor.config import ExecutorSettings
from conductor.engine_api import Tier
from conductor.executor import CommandExecutor
from conductor.executor_api import Command
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager, LockError
from conductor.services import Target
from conductor.telemetry import Telemetry

ROOT = Path(__file__).resolve().parent.parent


class Env:
    """What a writer path gets: the live world plus an executor and lock manager wired like production."""

    def __init__(self, world):
        self.world = world
        self.locks = DeviceLockManager(
            lambda a: (world.gate.device_writable(a, Purpose.BENCH), "not in the allowlist"), world.clock
        )
        self.executor = CommandExecutor(
            world.driver,
            locks=self.locks,
            telemetry=Telemetry(None, world.clock),
            clock=world.clock,
            policy=ExecutorSettings(),
        )


async def _executor_engine(env: Env, address: str) -> str:
    result = await env.executor.submit(
        Command(
            address, R.full_state_vector(R.Look("white", brightness=50, cct=2700)), Purpose.ENGINE, "engine"
        )
    )
    return str(result.status)


async def _executor_bench_with_lease(env: Env, address: str) -> str:
    try:
        lease = env.locks.acquire(address, "bench")
    except LockError:
        return "lock_refused"
    result = await env.executor.submit(
        Command(address, R.slot_select("custom-1"), Purpose.BENCH, "bench", lease_token=lease.token)
    )
    return str(result.status)


async def _executor_burst(env: Env, address: str) -> str:
    rows = R.full_state_vector(R.Look("white", brightness=50, cct=2700))
    results = await env.executor.burst([Command(address, rows, Purpose.ENGINE, "burst")])
    return str(results[0].status)


async def _driver_direct(env: Env, address: str) -> str:
    result = await env.world.driver.write(address, R.slot_select("custom-2"), purpose=Purpose.ENGINE)
    return str(result.outcome)


class _BenchServices:
    """The slice of `Services` the bench runner uses, wired to the live world (AferoDriver on the fake)."""

    def __init__(self, env: Env, data: Path):
        self.env = env
        self.clock = env.world.clock
        self.registry = env.world.registry
        self.locks = env.locks
        self.telemetry = env.executor.telemetry
        self.gate_settings = env.world.gate.settings
        self.data = data
        self.extras: dict = {}
        self.hub = type("Hub", (), {"publish": staticmethod(lambda topic, data: None)})()

    def target(self, device_id: str, purpose: Purpose) -> Target:
        address = self.registry.address(device_id, "afero")
        return Target(device_id, address, self.env.world.driver, self.env.executor)

    def lockable(self, address: str) -> tuple[bool, str]:
        return self.env.world.gate.device_writable(address, Purpose.BENCH), "not in the allowlist"

    def version(self) -> dict:
        return {"bench_live": True}


async def _bench_runner_gate_c(env: Env, address: str) -> str:
    device_id = env.world.registry.logical_for(address).id
    with tempfile.TemporaryDirectory() as data:
        runner = BenchRunner(_BenchServices(env, Path(data)))  # type: ignore[arg-type]
        try:
            record = await runner.run("gate_c", device_id, {})
        except BenchBusy:
            return "lock_refused"
    return str(record["result"]["observations"].get("write", {}).get("status", record["result"]["verdict"]))


async def _bench_runner_stale_clock(env: Env, address: str) -> str:
    """The stale-clock job: its select carries a shifted lastUpdateTime (Command.stamp_offset_s) — same gate."""
    device_id = env.world.registry.logical_for(address).id
    with tempfile.TemporaryDirectory() as data:
        runner = BenchRunner(_BenchServices(env, Path(data)))  # type: ignore[arg-type]
        try:
            record = await runner.run("stale_clock_put", device_id, {})
        except BenchBusy:
            return "lock_refused"
    return str(record["result"]["observations"].get("write", {}).get("status", record["result"]["verdict"]))


class _T1Proven:
    """The registry as the bench runner sees it, with Gate C (T1) recorded on each kit: only the gate and the lock
    can stop the jobs that need T1."""

    def __init__(self, registry):
        self.registry = registry

    def __getattr__(self, name: str):
        return getattr(self.registry, name)

    def get(self, device_id: str):
        from conductor.config import DeviceFlags

        entry = self.registry.get(device_id)
        return entry.model_copy(
            update={"flags": DeviceFlags(t1_frames=True), "flags_proven_on": entry.metadevice_id}
        )


async def _bench_runner_job(env: Env, address: str, job: str, params: dict, *, t1: bool = False) -> str:
    """A VWBD commissioning job through the runner: refused at the lock for a non-allowlisted device. On the bench
    kit, the status of its last write — the restore its `finally` sends through the same runner context."""
    device_id = env.world.registry.logical_for(address).id
    with tempfile.TemporaryDirectory() as data:
        services = _BenchServices(env, Path(data))
        if t1:
            services.registry = _T1Proven(services.registry)  # type: ignore[assignment]
        try:
            record = await BenchRunner(services).run(job, device_id, params)  # type: ignore[arg-type]
        except BenchBusy:
            return "lock_refused"
    writes = [s["detail"]["status"] for s in record["steps"] if s["detail"].get("kind") == "write"]
    return writes[-1] if writes else str(record["result"]["verdict"])


async def _bench_runner_gate_c_safe(env: Env, address: str) -> str:
    return await _bench_runner_job(env, address, "gate_c_safe", {"source": "custom-2", "target": "custom-1"})


async def _bench_runner_address_probe_safe(env: Env, address: str) -> str:
    return await _bench_runner_job(env, address, "address_probe_safe", {"duration_s": 4.0}, t1=True)


async def _bench_runner_effect_probe(env: Env, address: str) -> str:
    return await _bench_runner_job(env, address, "effect_probe", {"duration_s": 2.0}, t1=True)


async def _module_page_paint(env: Env, address: str) -> str:
    """/ops/module: open the device (takes its lock) → paint three heads (a T1 frame write + select, one PUT)."""
    import json
    import tempfile

    from fake_afero import raw_device_from_fixture

    from conductor.config import ConductorSettings, DeviceFlags, load_recipes
    from conductor.geometry import load_canopy_config
    from conductor.module import ModuleSession, SessionDeps
    from conductor.services import Target
    from conductor.sim.profiles import load_profile

    world = env.world
    with tempfile.TemporaryDirectory() as tmp:  # the fake device as a 30-head strand (the module map's size)
        fixture = Path(tmp) / "sqhdk03.json"
        fixture.write_text(json.dumps(load_profile("sqhdk03-sim")))
        world.fake.devices[address] = raw_device_from_fixture(address, fixture)
    device_id = world.registry.logical_for(address).id
    # Gate C recorded for the device, so the page may paint: only the gate/lock can stop the spare
    entry = world.registry.get(device_id).model_copy(
        update={"flags": DeviceFlags(t1_frames=True), "flags_proven_on": address}
    )
    deps = SessionDeps(
        clock=world.clock,
        locks=env.locks,
        telemetry=Telemetry(None, world.clock),
        settings=ConductorSettings(),
        recipes=load_recipes(),
        canopy=load_canopy_config(),
        entry=lambda _id: entry,
    )
    try:
        session = await ModuleSession.open(
            deps, Target(device_id, address, world.driver, env.executor), owner="module page · test"
        )
    except LockError:
        return "lock_refused"
    result = await session.paint_heads(heads=[{"index": i, "r": 255} for i in (0, 7, 22)])
    await session.close()
    return str(result["status"])


async def _module_page_lowcountry_preview(env: Env, address: str) -> str:
    """/ops/module "Preview a Lowcountry Mode": a compiled T1 canvas slice written + selected through the page."""
    import json
    import tempfile

    from fake_afero import raw_device_from_fixture

    from conductor.config import ConductorSettings, DeviceFlags, load_recipes
    from conductor.geometry import load_canopy_config
    from conductor.looks import load_modes
    from conductor.module import ModuleSession, SessionDeps
    from conductor.sim.profiles import load_profile

    world = env.world
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp) / "sqhdk03.json"
        fixture.write_text(json.dumps(load_profile("sqhdk03-sim")))
        world.fake.devices[address] = raw_device_from_fixture(address, fixture)
    device_id = world.registry.logical_for(address).id
    entry = world.registry.get(device_id).model_copy(
        update={"flags": DeviceFlags(t1_frames=True), "flags_proven_on": address}
    )
    deps = SessionDeps(
        clock=world.clock,
        locks=env.locks,
        telemetry=Telemetry(None, world.clock),
        settings=ConductorSettings(),
        recipes=load_recipes(),
        canopy=load_canopy_config(),
        entry=lambda _id: entry,
        modes=load_modes(),
    )
    try:
        session = await ModuleSession.open(
            deps, Target(device_id, address, world.driver, env.executor), owner="module page · test"
        )
    except LockError:
        return "lock_refused"
    result = await session.card("lowcountry-preview", {"mode": "rainbow-row-sunset", "position": "r0c1"})
    await session.close()
    return str(result["status"])


def _canopy_engine(env: Env, address: str, **kiosk: float):
    """The canopy engine wired to the live world, with a stand-in compiler whose only module is `address`."""
    from fake_compiler import FakeCompiler, settings_with

    from conductor.canopy import CanopyEngine, EngineDeps
    from conductor.state import DeviceStateTracker

    world = env.world
    device_id = world.registry.logical_for(address).id
    compiler = FakeCompiler(
        module_ids=[device_id], frame_length=20
    )  # the fake devices are 20-segment strings
    tracker = DeviceStateTracker(world.clock, env.executor.telemetry)
    tracker.attach(env.executor)
    deps = EngineDeps(
        registry=lambda: world.registry,
        target=lambda d, _purpose: Target(d, world.registry.address(d, "afero"), world.driver, env.executor),
        tracker=tracker,
        telemetry=env.executor.telemetry,
        hub=None,
        clock=world.clock,
        settings=settings_with(kiosk=dict(kiosk)),
        locks=env.locks,
        compile=compiler.compile,
        ladder=compiler.ladder,
        modes=compiler.modes,
    )
    return CanopyEngine(deps), device_id


async def _canopy_mode_change(env: Env, address: str) -> str:
    """Kiosk mode request → one ENGINE command per module through the executor's burst."""
    engine, _ = _canopy_engine(env, address)
    await engine.request_mode("holiday", source="kiosk")
    await engine.wait_idle()
    await engine.stop()
    return str(engine.last_status[address])


async def _canopy_reassert(env: Env, address: str) -> str:
    """The reconciler's re-assert of a module whose last command failed."""
    from conductor.executor_api import CommandStatus

    engine, _ = _canopy_engine(env, address)
    await engine.request_mode("lowcountry-calm", source="ops")
    await engine.wait_idle()
    engine.last_status[address] = CommandStatus.FAILED
    sent = await engine.reconciler.reassert()
    await engine.wait_idle()
    await engine.stop()
    assert sent, "the re-assert didn't try to send"
    return str(engine.last_status[address])


async def _canopy_idle_hold(env: Env, address: str) -> str:
    """Idle hold: no kiosk change for idle_return_s → the idle mode, from the reconciler loop."""
    engine, _ = _canopy_engine(env, address, idle_return_s=1)
    await engine.start(apply_initial=False)
    await env.world.clock.sleep(2)
    await engine.wait_idle()
    plan = engine.current_plan()
    await engine.stop()
    assert plan is not None and plan.mode_id == "lowcountry-calm"
    return str(engine.last_status[address])


async def _canopy_commission(env: Env, address: str) -> str:
    """Commissioning: a T1 slot-object write per module."""
    engine, device_id = _canopy_engine(env, address)
    report = await engine.commission(source="ops")
    await engine.stop()
    return str(report["modes"]["sunset"]["modules"][device_id]["status"])


async def _program_runner(env: Env, address: str) -> str:
    """A stepped look's runner (conductor/sequence.py): step 1, four seconds after the mode change, one ENGINE command
    per module through engine.send (source "program")."""
    from conductor.engine_api import ModePlan, ModuleTarget, Program, ProgramStep, Tier

    engine, device_id = _canopy_engine(env, address)

    def look(rgb):
        return R.full_state_vector(R.Look("rgb", rgb=rgb, brightness=80))

    def compile(mode_id, tier):
        steps = [
            ProgramStep(index=i, rows={device_id: look(rgb)}, colours={"r0c0": "#000000"})
            for i, rgb in enumerate([(250, 120, 40), (40, 120, 250)])
        ]
        program = Program("stepped", {}, "forward", 4.0, 8.0, steps, "Colour through the cloud (test).")
        target = ModuleTarget(device_id, look((250, 120, 40)), look((250, 120, 40)))
        return ModePlan(mode_id, "Stepped", Tier.STEPPED, "test", False, {device_id: target}, program=program)

    engine.deps.compile, engine.deps.ladder = compile, lambda _mode: [Tier.STEPPED]
    await engine.request_mode("stepped-test", source="ops")
    await engine.wait_idle()
    before = engine.last_status.get(address)
    await env.world.clock.sleep(4.5)  # step 1
    await engine.wait_idle()
    commands = [c for c in env.executor.telemetry.recent(kind="command") if c["source"] == "program"]
    await engine.stop()
    assert before is not None and commands, "the runner didn't try to send its step"
    return str(commands[-1]["status"])


async def _stepped_rehearsal(env: Env, address: str) -> str:
    """The stepped rehearsal (Gate R, conductor/rehearsal.py): ops starts one bounded stepped run; its hold and
    steps go through engine.send like any look, to the plan's modules only."""
    from conductor.engine_api import ModePlan, ModuleTarget, Program, ProgramStep, Tier

    engine, device_id = _canopy_engine(env, address)

    def look(rgb):
        return R.full_state_vector(R.Look("rgb", rgb=rgb, brightness=80))

    def compile(mode_id, tier):
        steps = [
            ProgramStep(index=i, rows={device_id: look(rgb)}, colours={"r0c0": "#000000"})
            for i, rgb in enumerate([(250, 120, 40), (40, 120, 250)])
        ]
        program = Program("stepped", {}, "forward", 4.0, 8.0, steps, "Colour through the cloud (test).")
        target = ModuleTarget(device_id, look((250, 120, 40)), look((250, 120, 40)))
        return ModePlan(mode_id, "Stepped", Tier.STEPPED, "test", False, {device_id: target}, program=program)

    engine.deps.compile, engine.deps.ladder = compile, lambda _mode: [Tier.STEPPED]
    result = await engine.rehearse("stepped-test", 1, source="ops")
    await engine.wait_idle()
    await env.world.clock.sleep(4.5)  # step 1
    await engine.wait_idle()
    commands = [c for c in env.executor.telemetry.recent(kind="command") if c["source"] == "program"]
    await engine.stop()
    assert result["ok"] and commands, "the rehearsal didn't try to send its step"
    return str(commands[-1]["status"])


async def _lab_trial(env: Env, address: str) -> str:
    """The Design Lab's try-on (D66 Slice B): ops applies a draft; the engine shows it at module level (sunset's T3
    rung: the try-on skips T1/T2/stepped) as one burst through engine.send, like any look. The Lab itself never writes."""
    from conductor.canopy import Trial

    engine, _ = _canopy_engine(env, address)
    now = env.world.clock.monotonic()
    trial = Trial(
        "sunset",
        "Sunset (draft)",
        modes=None,
        recipes=None,
        started_mono=now,
        ends_mono=now + 150,
        started_at=0.0,
    )
    state = await engine.start_trial(trial)
    await engine.wait_idle()
    plan = engine.current_plan()
    await engine.stop()
    assert not state.refused and plan is not None and plan.tier is Tier.T3, (state.message, plan)
    return str(engine.last_status[address])


async def _lab_publish_repaint(env: Env, address: str) -> str:
    """The Design Lab publish's operator slot repaint (D68 PR B2): the engine's own job (`start_repaint`) writes the
    slot object through `_commission_plan` and the executor, like commissioning. The Lab never writes."""
    from types import SimpleNamespace

    from conductor.canopy import RepaintJob

    engine, device_id = _canopy_engine(env, address)
    plan = engine.deps.compile("golden", Tier.T1)
    engine.deps.repaint_target = lambda _mode: SimpleNamespace(listed=[device_id], plan=plan)
    engine.start_repaint([RepaintJob("golden", "custom-3", (device_id,), "Golden", 0.0)], generation=0)
    await engine.wait_repaint()
    status = engine.repaint_json()
    await engine.stop()
    assert status is not None and status["modules"], "the repaint didn't try to write"
    return str(status["modules"][0]["status"])


WriterPath = Callable[[Env, str], Awaitable[str]]
WRITER_PATHS: dict[str, WriterPath] = {
    "executor.submit (engine)": _executor_engine,
    "executor.submit (bench, with lock)": _executor_bench_with_lease,
    "executor.burst (mode change)": _executor_burst,
    "driver.write (anything bypassing the executor)": _driver_direct,
    "bench runner (gate_c)": _bench_runner_gate_c,
    "bench runner (stale_clock_put, shifted stamp)": _bench_runner_stale_clock,
    "bench runner (gate_c_safe, with restore)": _bench_runner_gate_c_safe,
    "bench runner (address_probe_safe, with restore)": _bench_runner_address_probe_safe,
    "bench runner (effect_probe, with restore)": _bench_runner_effect_probe,
    "module page (paint)": _module_page_paint,
    "module page (lowcountry preview)": _module_page_lowcountry_preview,
    "canopy engine (mode change)": _canopy_mode_change,
    "reconciler (re-assert)": _canopy_reassert,
    "idle hold": _canopy_idle_hold,
    "slot manager (commission)": _canopy_commission,
    "program runner (stepped look)": _program_runner,
    "stepped rehearsal (Gate R)": _stepped_rehearsal,
    "lab try-on (module level)": _lab_trial,
    "lab publish (slot repaint)": _lab_publish_repaint,
}
# Modules that submit commands or call driver.write, and the WRITER_PATHS entry covering each.
COVERED_MODULES = {
    "conductor/executor.py": "executor.submit (engine)",
    "conductor/bench/runner.py": "bench runner (gate_c)",
    "conductor/module/session.py": "module page (paint)",
    "conductor/canopy.py": "canopy engine (mode change)",
    "conductor/reconciler.py": "reconciler (re-assert)",
    "conductor/sequence.py": "program runner (stepped look)",  # sends through CanopyEngine.send
    # the try-on's routes never write; every write is the engine's (the entry proves it on the wire). Listing a file
    # here exempts it from test_every_writer_module_is_listed's scan, so the Lab must stay a non-writer by its own
    # check: tests/test_lab_access.py test_no_lab_module_calls_a_writer (an AST walk over every Lab source).
    "conductor/routes/design_lab.py": "lab try-on (module level)",
}
ALLOWED = {"denied", "lock_refused", "locked"}


@pytest.mark.parametrize("name", sorted(WRITER_PATHS))
def test_writer_path_is_denied_for_a_non_allowlisted_device(tmp_path, name):
    async def main():
        world = await make_world(tmp_path)
        env = Env(world)
        before = len(world.fake.requests)
        outcome = await WRITER_PATHS[name](env, SPARE_MD)
        sent = [r for r in world.fake.requests[before:] if SPARE_MD in r.path and r.method != "GET"]
        # control: the same path to the allowlisted bench kit does reach the wire
        control = await WRITER_PATHS[name](Env(world), BENCH_MD)
        control_sent = [r for r in world.fake.requests if BENCH_MD in r.path and r.method == "PUT"]
        await world.driver.close()
        return outcome, sent, control, control_sent

    outcome, sent, control, control_sent = run_virtual(main)
    assert outcome in ALLOWED, f"{name}: {outcome}"
    assert sent == [], f"{name} sent a write for a non-allowlisted device"
    assert control not in ALLOWED and control_sent, f"{name}: the allowlisted control write didn't go out"


def test_every_writer_module_is_listed():
    """A module that submits commands or writes to a driver must be covered by a WRITER_PATHS entry."""
    pattern = re.compile(r"\.(submit|burst)\(|driver\.write\(|\.write\(\s*address")
    writers = set()
    for path in (ROOT / "conductor").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("conductor/drivers/") or rel == "conductor/executor.py":
            continue
        if pattern.search(path.read_text()):
            writers.add(rel)
    missing = sorted(w for w in writers if w not in COVERED_MODULES)
    assert not missing, f"writer modules without a WRITER_PATHS entry: {missing}"
    for entry in COVERED_MODULES.values():
        assert entry in WRITER_PATHS
