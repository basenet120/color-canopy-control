"""Module-page sessions on virtual time (no HTTP): open/take-over races and Hold vs commands in the executor."""

from __future__ import annotations

import asyncio

import pytest

from conductor.clock import VirtualClock, run_virtual
from conductor.module.manager import ModuleManager
from conductor.module.session import ModuleError
from conductor.roles_api import OPS
from conductor.services import build_services

BENCH = "bench-kit"
ADDRESS = f"sim-{BENCH}"


def _services(tmp_path):
    return build_services({"CONDUCTOR_DATA_DIR": str(tmp_path)}, clock=VirtualClock())


def test_take_over_during_a_slow_open_leaves_the_taker_in_charge(tmp_path):
    """MOD-1: page A's slow profile read finishes after ops page B took over; B must keep the device."""
    services = _services(tmp_path)

    async def main():
        manager = ModuleManager.of(services)
        original = services.sim.fetch_profile
        delays = iter([2.0, 0.1])

        async def slow(device_id, *, purpose):
            await asyncio.sleep(next(delays))
            return await original(device_id, purpose=purpose)

        services.sim.fetch_profile = slow
        page_a = asyncio.create_task(manager.open(BENCH, OPS))
        await asyncio.sleep(0.01)
        page_b = await manager.open(BENCH, OPS, steal=True)
        with pytest.raises(ModuleError) as refused:
            await page_a
        assert refused.value.status == 409 and "taken over" in refused.value.message
        try:
            assert services.locks.holder(ADDRESS).token == page_b.lease.token
            assert manager.sessions[BENCH] is page_b
            assert manager.require(BENCH, page_b.lease.token) is page_b
            assert manager.get(BENCH) is page_b
        finally:
            await services.close()

    run_virtual(main)


def test_hold_stops_a_retrying_sweep_write(tmp_path):
    """MOD-2: once Hold is on, no write from this page is sent, including executor retries of an earlier step."""
    services = _services(tmp_path)

    async def main():
        manager = ModuleManager.of(services)
        session = await manager.open(BENCH, OPS)
        await session.refresh(force=True)
        sent: list[tuple[float, bool]] = []
        original = services.sim.write

        async def spy(device_id, rows, *, purpose):
            sent.append((services.clock.monotonic(), session.hold["on"]))
            return await original(device_id, rows, purpose=purpose)

        services.sim.write = spy
        services.sim.faults.set_throttle_rate(1.0)  # every write is throttled, so the executor retries
        await session.sweep("start")
        await asyncio.sleep(0.5)
        while not sent:  # the head-0 write has gone out and been throttled
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)
        session.set_hold(True, "photo")
        await asyncio.sleep(15)
        try:
            return sent, session.sweep_state.to_json(), list(session.results)
        finally:
            await services.close()

    sent, sweep, results = run_virtual(main)
    assert [on for _, on in sent] == [False]  # nothing was sent after Hold went on
    assert sweep["state"] == "paused"
    assert sweep["next_index"] == 0  # head 0 never landed, so resuming starts there
    assert results[0]["status"] == "superseded" and "Hold" in results[0]["error"]
