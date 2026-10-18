"""Guest pacing by the account's write budget (D70; replaces D21's fixed 8 s cooldown).

Proves: the gap after an accepted guest input is the time the guests' write budget takes to pay for what it sent —
one write per driven module, counted on the account that carries the most, at `kiosk.guest_writes_per_min` (never
above the account cap) — and never less than `kiosk.cooldown_min_s`: two strands wait 2 s, nine wait 7.5 s; a mood,
a brightness and an on/off all buy it; ops bypass it; a guest mashing every control for two minutes keeps the
account within its cap and the accepted inputs a full gap apart; the Design Lab keeps its own 8 s apply gap.
"""

from __future__ import annotations

from itertools import pairwise
from types import SimpleNamespace

import pytest
from fake_compiler import MODULES, FakeCompiler, make_world, settings_with

from conductor.clock import run_virtual
from conductor.config import load_settings

QUIET = {"idle_return_s": 100_000, "idle_reset_s": 100_000}
EPS = 1e-6


def _plan(n: int) -> SimpleNamespace:
    return SimpleNamespace(modules={d: None for d in MODULES[:n]})


def _gaps(settings=None, accounts=None) -> dict:
    async def main():
        w = make_world(settings=settings)
        await w.engine.start(
            apply_initial=False
        )  # the engine knows each module's target (and account) once started
        if accounts is not None:
            w.engine._account_of = lambda address: accounts[address.removeprefix("sim-")]
        out = {n: w.engine.guest_gap_s(_plan(n)) for n in (0, 1, 2, 3, 9)}
        out[None] = w.engine.guest_gap_s(None)
        await w.close()
        return out

    return run_virtual(main)


def test_the_gap_is_the_budget_over_the_writes_and_never_under_the_floor():
    gaps = _gaps()
    assert gaps[None] == gaps[0] == gaps[1] == gaps[2] == 2.0  # 2 × 60 / 72 = 1.67 s: the floor
    assert gaps[3] == pytest.approx(2.5)  # 3 × 60 / 72
    assert gaps[9] == pytest.approx(7.5)  # the nine-module canopy: 9 × 60 / 72


def test_the_account_carrying_the_most_modules_sets_the_gap():
    accounts = {d: ("canopy" if i < 5 else "second") for i, d in enumerate(MODULES)}
    assert _gaps(accounts=accounts)[9] == pytest.approx(5 * 60 / 72)


def test_a_lower_account_cap_lowers_the_guests_budget_with_it():
    lower = settings_with(motion={"stepped": {"account_writes_per_min": 40}})
    assert _gaps(lower)[9] == pytest.approx(9 * 60 / 40)  # 13.5 s, not 7.5: never budgeted above the account
    floor = settings_with(kiosk={"cooldown_min_s": 4})
    assert _gaps(floor)[2] == 4.0 and _gaps(floor)[9] == pytest.approx(7.5)


@pytest.mark.parametrize(("strands", "gap"), [(2, 2.0), (9, 7.5)])
def test_the_next_guest_choice_waits_exactly_the_gap_and_ops_never_wait(strands, gap):
    async def main():
        w = make_world(
            settings=settings_with(kiosk=QUIET), compiler=FakeCompiler(module_ids=MODULES[:strands])
        )
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.run_for(15)
        first = await w.engine.request_mode("holiday", source="kiosk")
        first_wait = first.cooldown_until - w.clock.now()
        await w.run_for(gap - 0.3)
        early = await w.engine.request_mode("lowcountry-calm", source="kiosk")
        ops = await w.engine.request_mode("sunset", source="ops")  # ops bypass the guests' pacing
        await w.run_for(0.35)
        on_time = await w.engine.request_mode("lowcountry-calm", source="kiosk")
        await w.close()
        return first, first_wait, early, ops, on_time

    first, first_wait, early, ops, on_time = run_virtual(main)
    assert not first.refused and first_wait == pytest.approx(gap, abs=0.01)
    assert early.refused and early.retry_after_s == pytest.approx(0.3, abs=0.01)
    assert not ops.refused and ops.mode == "sunset"
    assert not on_time.refused and on_time.mode == "lowcountry-calm"


def test_brightness_and_on_off_buy_the_same_gap():
    async def main():
        w = make_world(settings=settings_with(kiosk=QUIET), compiler=FakeCompiler(module_ids=MODULES[:2]))
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.run_for(15)
        dim = await w.engine.set_guest_brightness(40, source="kiosk")
        dim_wait = dim.cooldown_until - w.clock.now()
        await w.run_for(1.0)
        too_soon = await w.engine.set_guest_power(False, source="kiosk")
        await w.run_for(1.05)
        off = await w.engine.set_guest_power(False, source="kiosk")
        await w.close()
        return dim, dim_wait, too_soon, off

    dim, dim_wait, too_soon, off = run_virtual(main)
    assert not dim.refused and dim_wait == pytest.approx(2.0, abs=0.01)  # two strands: the 2 s floor, not 8 s
    assert too_soon.refused and too_soon.guest_power is True
    assert not off.refused and off.guest_power is False


def test_a_guest_mashing_every_control_keeps_the_account_within_its_cap():
    """Two minutes of a control every 0.25 s on the nine-module canopy: accepted guest inputs a full 7.5 s apart,
    and the account never past its 90 writes in any 60 s (the reserve covers the re-asserts)."""

    async def main():
        w = make_world(settings=settings_with(kiosk=QUIET))
        sent: list[float] = []
        real_write = w.sim.write

        async def counted(device_id, rows, **kw):
            sent.append(w.clock.monotonic())
            return await real_write(device_id, rows, **kw)

        w.sim.write = counted
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.run_for(15)
        sent.clear()
        accepted: list[float] = []
        modes = ["holiday", "sunset", "lowcountry-calm"]
        for i in range(480):
            kind = i % 3
            if kind == 0:
                state = await w.engine.request_mode(modes[(i // 3) % 3], source="kiosk")
            elif kind == 1:
                state = await w.engine.set_guest_brightness(20 + (i % 70), source="kiosk")
            else:
                state = await w.engine.set_guest_power(bool(i % 2), source="kiosk")
            if not state.refused:
                accepted.append(w.clock.monotonic())
            await w.run_for(0.25)
        await w.run_for(10)
        await w.close()
        return sent, accepted

    sent, accepted = run_virtual(main)
    spacing = [b - a for a, b in pairwise(accepted)]
    assert len(accepted) >= 14 and min(spacing) >= 7.5 - EPS, spacing
    most = max(sum(1 for t in sent if start <= t < start + 60) for start in sent)
    assert most <= 90, most  # the account cap (motion.stepped.account_writes_per_min)


def test_the_design_lab_keeps_its_own_eight_second_apply_gap():
    settings = load_settings()
    assert settings.lab.apply_min_gap_s == 8.0 and settings.kiosk.cooldown_min_s == 2.0
