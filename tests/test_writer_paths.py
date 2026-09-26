"""07 §1: every writer path is denied for a non-allowlisted device — proven on the wire.

Each entry in WRITER_PATHS drives one production code path that can write to a device, pointed at SPARE_MD: a device
that is registered and writable but NOT in HUBSPACE_DEVICE_ALLOWLIST. The runtime is `live` (the most permissive
mode) with a real AferoDriver on the fake transport. The test fails if any PUT for that device reaches the wire.

**Every new writer path must add itself here** (reconciler, idle hold, slot manager, bench jobs, module page,
vignettes, ...). `test_every_writer_module_is_listed` fails when a module that submits commands isn't covered.
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


WriterPath = Callable[[Env, str], Awaitable[str]]
WRITER_PATHS: dict[str, WriterPath] = {
    "executor.submit (engine)": _executor_engine,
    "executor.submit (bench, with lock)": _executor_bench_with_lease,
    "executor.burst (mode change)": _executor_burst,
    "driver.write (anything bypassing the executor)": _driver_direct,
    "bench runner (gate_c)": _bench_runner_gate_c,
}
# Modules that submit commands or call driver.write, and the WRITER_PATHS entry covering each.
COVERED_MODULES = {
    "conductor/executor.py": "executor.submit (engine)",
    "conductor/bench/runner.py": "bench runner (gate_c)",
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
