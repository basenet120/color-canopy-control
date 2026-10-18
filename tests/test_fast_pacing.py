"""Fast guest pacing (D72): the switch `kiosk.fast_pacing` and its throttle back-off.

Proves: with the switch on (the default) every guest input waits only `kiosk.cooldown_min_s` (2 s), on two strands and
on nine; with it off, exactly D70's budget rule (7.5 s on nine); while on, ≥ `kiosk.fast_backoff_throttles` write
attempts answered 429/403 on an account within a minute back the guests there off to the budget gap for
`kiosk.fast_backoff_s` after the last one — a single throttle, a 503/504 (an outage, not a throttle) or two throttles
more than a minute apart don't — with one info alert per back-off; real 429s from the simulator trip it end to end; a
guest mashing every control on nine modules gets an input through every 2 s while each device still gets at most one
write a second.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from itertools import pairwise

import pytest
from fake_compiler import MODULES, FakeCompiler, make_world, settings_with

from conductor.clock import run_virtual
from conductor.config import load_settings
from conductor.drivers.base import Outcome

QUIET = {"idle_return_s": 100_000, "idle_reset_s": 100_000}
EPS = 1e-6


def _throttle(w, device_id: str, status: int = 429) -> None:
    """A write attempt to `device_id` answered `status`, as the executor records it."""
    w.telemetry.record(
        "attempt",
        device_id=w.address(device_id),
        label="test",
        n=1,
        outcome=str(Outcome.THROTTLED),
        http_status=status,
        elapsed_s=0.2,
        error=None,
        simulated=True,
    )


def _plan(n: int):
    from types import SimpleNamespace

    return SimpleNamespace(modules={d: None for d in MODULES[:n]})


def _alerts(w) -> list[str]:
    return [e["message"] for e in w.events("alert") if "Guest pacing backed off" in (e.get("message") or "")]


def test_the_switch_is_on_by_default_and_on_means_two_seconds_whatever_the_size():
    assert load_settings().kiosk.fast_pacing is True

    async def main():
        w = make_world(settings=settings_with(kiosk=QUIET))
        await w.engine.start(apply_initial=False)
        gaps = {n: w.engine.guest_gap_s(_plan(n)) for n in (0, 2, 9)}
        await w.close()
        return gaps

    assert run_virtual(main) == {0: 2.0, 2: 2.0, 9: 2.0}


def test_the_switch_off_is_exactly_the_budget_rule():
    async def main():
        w = make_world(settings=settings_with(kiosk={**QUIET, "fast_pacing": False}))
        await w.engine.start(apply_initial=False)
        gaps = {n: w.engine.guest_gap_s(_plan(n)) for n in (2, 3, 9)}
        await w.close()
        return gaps

    gaps = run_virtual(main)
    assert gaps[2] == 2.0 and gaps[3] == pytest.approx(2.5) and gaps[9] == pytest.approx(7.5)


def test_two_throttles_in_a_minute_back_off_to_the_budget_gap_then_it_lifts():
    async def main():
        w = make_world(settings=settings_with(kiosk=QUIET))
        await w.engine.start(apply_initial=False)
        out = {}
        _throttle(w, MODULES[0])
        out["one"] = w.engine.guest_gap_s(_plan(9))
        await w.run_for(10)
        _throttle(w, MODULES[3], status=403)  # a 403 is throttling too
        out["two"] = w.engine.guest_gap_s(_plan(9))
        out["accounts"] = w.engine.fast_backoff()
        await w.run_for(30)
        _throttle(w, MODULES[5])  # a third, still backed off: no second alert, the hold restarts from here
        await w.run_for(119)
        out["held"] = w.engine.guest_gap_s(_plan(9))
        await w.run_for(2)
        out["lifted"] = w.engine.guest_gap_s(_plan(9))
        out["alerts"] = _alerts(w)
        out["stats"] = w.engine.stats.get("fast_backoffs")
        await w.close()
        return out

    out = run_virtual(main)
    assert out["one"] == 2.0  # a single throttle is not a trend
    assert out["two"] == pytest.approx(7.5) and len(out["accounts"]) == 1
    assert out["held"] == pytest.approx(7.5)  # 120 s after the LAST throttled attempt
    assert out["lifted"] == 2.0
    assert len(out["alerts"]) == 1 and "2 write attempts" in out["alerts"][0] and out["stats"] == 1


def test_outages_and_throttles_a_minute_apart_never_back_off():
    async def main():
        w = make_world(settings=settings_with(kiosk=QUIET))
        await w.engine.start(apply_initial=False)
        for device in MODULES[:2]:  # 503/504 are an outage signature (D15), not rate limiting
            _throttle(w, device, status=503)
            _throttle(w, device, status=504)
        outage = w.engine.guest_gap_s(_plan(9))
        _throttle(w, MODULES[0])
        await w.run_for(61)
        _throttle(w, MODULES[1])
        apart = w.engine.guest_gap_s(_plan(9))
        alerts = _alerts(w)
        await w.close()
        return outage, apart, alerts

    outage, apart, alerts = run_virtual(main)
    assert outage == 2.0 and apart == 2.0 and alerts == []


def test_switched_off_throttles_change_nothing_and_raise_no_alert():
    async def main():
        w = make_world(settings=settings_with(kiosk={**QUIET, "fast_pacing": False}))
        await w.engine.start(apply_initial=False)
        _throttle(w, MODULES[0])
        _throttle(w, MODULES[1])
        gap = w.engine.guest_gap_s(_plan(9))
        alerts = _alerts(w)
        await w.close()
        return gap, alerts

    gap, alerts = run_virtual(main)
    assert gap == pytest.approx(7.5) and alerts == []


def test_real_429s_from_hubspace_back_the_guests_off_end_to_end():
    """The simulator answers every PUT 429 for a moment: the executor's own attempts trip the back-off, and the next
    guest choice is paced by the budget (7.5 s on nine), not the 2 s floor."""

    async def main():
        w = make_world(settings=settings_with(kiosk=QUIET))
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.run_for(15)
        w.sim.faults.set_throttle_rate(1.0)
        first = await w.engine.request_mode("holiday", source="kiosk")
        fast_wait = first.cooldown_until - w.clock.now()  # bought before any 429 came back
        await w.run_for(3)
        w.sim.faults.set_throttle_rate(0.0)
        await w.run_for(10)
        second = await w.engine.request_mode("sunset", source="kiosk")
        backed_off_wait = second.cooldown_until - w.clock.now()
        backoff = w.engine.fast_backoff()
        await w.close()
        return fast_wait, backed_off_wait, backoff

    fast_wait, backed_off_wait, backoff = run_virtual(main)
    assert fast_wait == pytest.approx(2.0, abs=0.01)
    assert backed_off_wait == pytest.approx(7.5, abs=0.01) and backoff


def test_a_guest_mashing_gets_an_input_through_every_two_seconds_and_devices_stay_at_one_write_a_second():
    async def main():
        w = make_world(settings=settings_with(kiosk=QUIET))
        sent: dict[str, list[float]] = defaultdict(list)
        real_write = w.sim.write

        async def counted(device_id, rows, **kw):
            sent[device_id].append(w.clock.monotonic())
            return await real_write(device_id, rows, **kw)

        w.sim.write = counted
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.run_for(15)
        sent.clear()
        accepted: list[float] = []
        modes = ["holiday", "sunset", "lowcountry-calm"]
        for i in range(240):  # a minute of a control every 0.25 s
            if i % 3 == 0:
                state = await w.engine.request_mode(modes[(i // 3) % 3], source="kiosk")
            elif i % 3 == 1:
                state = await w.engine.set_guest_brightness(20 + (i % 70), source="kiosk")
            else:
                state = await w.engine.set_guest_power(bool(i % 2), source="kiosk")
            if not state.refused:
                accepted.append(w.clock.monotonic())
            await w.run_for(0.25)
        await asyncio.sleep(0)
        await w.run_for(10)
        await w.close()
        return sent, accepted

    sent, accepted = run_virtual(main)
    spacing = [b - a for a, b in pairwise(accepted)]
    assert len(accepted) >= 25 and min(spacing) >= 2.0 - EPS, spacing  # ~one every 2 s, never sooner
    assert max(len(v) for v in sent.values()) >= 25
    for times in sent.values():  # the only published limit holds per device (the executor's rate budget)
        assert all(b - a >= 1.0 - EPS for a, b in pairwise(times))


def test_two_strands_are_unchanged_by_the_switch():
    async def gap(fast: bool) -> float:
        w = make_world(
            settings=settings_with(kiosk={**QUIET, "fast_pacing": fast}),
            compiler=FakeCompiler(module_ids=MODULES[:2]),
        )
        await w.engine.start(apply_initial=False)
        g = w.engine.guest_gap_s(_plan(2))
        await w.close()
        return g

    assert run_virtual(lambda: gap(True)) == run_virtual(lambda: gap(False)) == 2.0
