"""Guest brightness and power on the real SimDriver, executor, tracker and reconciler (virtual time), D60.

The overrides are part of the engine's desired state: one write per module, kept by the reconciler (re-asserted
after drift, never fought), cleared by choosing a mood, the safe look, the idle reset and ops; refused like a mood
choice (cooldown, pause, resting, commissioning); the rate budget holds at every tier."""

from __future__ import annotations

import asyncio

import pytest
from fake_compiler import MODULES, FakeCompiler, frame_for, make_world, settings_with
from pydantic import ValidationError

from conductor import rows as R
from conductor.clock import run_virtual
from conductor.config import CONFIG_DIR, ConductorSettings, ConfigError, KioskSettings, load_settings
from conductor.engine_api import CanopyPhase, Tier
from conductor.gate import Purpose
from conductor.show import ShowWiring

QUIET = settings_with(kiosk={"idle_return_s": 100_000, "idle_reset_s": 100_000})
CALM = R.full_state_vector(R.Look("white", cct=2700, brightness=60))  # FakeCompiler's lowcountry-calm


def view(w, device_id: str) -> dict:
    return {r.key: r.value for r in w.sim.device_view(w.address(device_id))}


def level(w, device_id: str):
    return view(w, device_id).get(("brightness", None))


def power(w, device_id: str):
    return view(w, device_id).get(("power", None))


def everywhere(w, fn, value) -> bool:
    return all(fn(w, d) == value for d in MODULES)


def writes_per_device(w) -> dict[str, int]:
    return dict(w.sim.stats()["writes_per_device"])


def app_edit(w, address: str, rows: list) -> None:
    """A change made outside the Conductor (the Hubspace app), stored by the cloud as a normal PUT."""
    stamp = int(w.clock.now() * 1000)
    response = w.sim.cloud.accept_wire({"metadeviceId": address, "values": [r.to_wire(stamp) for r in rows]})
    assert response.ok, response.error


async def settled_on(w, mode: str, *, source: str = "ops") -> None:
    await w.engine.request_mode(mode, source=source)
    assert await w.until(w.all_correct, timeout=15) is not None
    await w.engine.wait_idle()


def ready(w) -> bool:
    return w.engine.state().phase is CanopyPhase.READY


def shows_desired(w) -> bool:
    """Every module's lights match the engine's desired state: the look with the guest's settings applied (World's
    `all_correct` checks the plan's designed rows, which a guest override changes on purpose)."""
    plan = w.engine.current_plan()
    return all(
        not R.compare(w.engine._desired_expect(d, plan.modules[d], plan), w.sim.device_view(w.address(d)))
        for d in plan.modules
    )


def test_brightness_is_one_write_per_module_the_full_vector_and_the_reconciler_keeps_it():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        await w.run_for(10)
        before = writes_per_device(w)
        sent = []
        w.executor.add_submit_listener(sent.append)
        state = await w.engine.set_guest_brightness(30, source="kiosk")
        settled = await w.until(lambda: everywhere(w, level, 30) and ready(w), timeout=10)
        await w.engine.wait_idle()
        await w.run_for(60)  # a minute of rolling true-ups: they read 30, which is what the engine wants
        after = writes_per_device(w)
        final = w.engine.state()
        guest_events = w.events("guest")
        await w.close()
        return state, settled, before, after, sent, final, guest_events

    state, settled, before, after, sent, final, guest_events = run_virtual(main)
    assert not state.refused and state.guest_brightness == 30 and state.guest_power is True
    assert state.brightness == 30 and state.guest_brightness_min == 10
    assert settled is not None and settled <= 5
    assert all(after[a] - before[a] == 1 for a in after)  # exactly one write per module, never fought
    assert len(sent) == 9 and all(c.label == "guest brightness: lowcountry-calm" for c in sent)
    for (
        cmd
    ) in sent:  # the look's full state vector with only the brightness row changed, in place; never a frame
        assert [r.key for r in cmd.rows] == [r.key for r in CALM]
        assert {r.key: r.value for r in cmd.rows} == {
            **{r.key: r.value for r in CALM},
            ("brightness", None): 30,
        }
        assert all(r.function_class != R.FRAME_CLASS for r in cmd.rows)
    assert final.phase is CanopyPhase.READY
    data = final.to_json()
    assert data["guest_brightness"] == 30 and data["guest_power"] is True and data["brightness"] == 30
    assert guest_events[-1]["action"] == "brightness" and guest_events[-1]["source"] == "kiosk"


def test_brightness_is_clamped_to_the_guest_floor_and_100_and_a_repeat_sends_nothing():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        low = await w.engine.set_guest_brightness(3, source="ops")
        high = await w.engine.set_guest_brightness(250, source="ops")
        await w.until(lambda: everywhere(w, level, 100), timeout=10)
        await w.engine.wait_idle()
        writes = w.sim.stats()["writes"]
        again = await w.engine.set_guest_brightness(100, source="kiosk")
        await w.run_for(3)
        repeat_writes = w.sim.stats()["writes"] - writes
        await w.close()

        w2 = make_world(settings=settings_with(kiosk={"guest_brightness_min": 25, "idle_reset_s": 100_000}))
        await w2.engine.start(apply_initial=False)
        await settled_on(w2, "lowcountry-calm")
        floor = await w2.engine.set_guest_brightness(5, source="ops")
        await w2.close()
        return low, high, again, repeat_writes, floor

    low, high, again, repeat_writes, floor = run_virtual(main)
    assert low.guest_brightness == 10 and high.guest_brightness == 100
    assert not again.refused and again.cooldown_until is None  # nothing changed: no write, no cooldown
    assert repeat_writes == 0
    assert floor.guest_brightness == 25 and floor.guest_brightness_min == 25


def test_drift_is_re_asserted_to_the_guest_level_not_the_designed_one():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        await w.engine.set_guest_brightness(30, source="kiosk")
        await w.until(lambda: everywhere(w, level, 30) and ready(w), timeout=10)
        await w.engine.wait_idle()
        before = writes_per_device(w)
        brightness_sent = []
        w.executor.add_submit_listener(
            lambda c: brightness_sent.extend(r.value for r in c.rows if r.key == ("brightness", None))
        )
        # Someone dims it up in the Hubspace app, to exactly the look's designed level: still wrong for the guest.
        app_edit(w, "sim-canopy-r2c0", [R.brightness(60)])
        drifted = await w.until(lambda: level(w, "canopy-r2c0") == 60, timeout=5, step=0.05)
        healed = await w.until(lambda: level(w, "canopy-r2c0") == 30, timeout=30)
        await w.engine.wait_idle()
        await w.run_for(30)
        after = writes_per_device(w)
        await w.close()
        return drifted, healed, before, after, brightness_sent

    drifted, healed, before, after, brightness_sent = run_virtual(main)
    assert drifted is not None
    assert (
        healed is not None and drifted + healed <= 10
    )  # the 10 s self-heal target holds with an override in force
    assert after["sim-canopy-r2c0"] - before["sim-canopy-r2c0"] == 1
    assert all(after[a] == before[a] for a in after if a != "sim-canopy-r2c0")
    assert brightness_sent == [30]  # the re-assert sent the guest's level, never the designed 60


def test_power_off_is_one_power_row_per_module_kept_through_drift_and_on_brings_the_look_back():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        before = writes_per_device(w)
        sent = []
        w.executor.add_submit_listener(sent.append)
        off = await w.engine.set_guest_power(False, source="kiosk")
        dark = await w.until(lambda: everywhere(w, power, "off") and ready(w), timeout=10)
        await w.engine.wait_idle()
        await w.run_for(30)
        quiet = writes_per_device(w)
        # Switched back on in the app: every row now matches the designed look, but not the guest's "off".
        app_edit(w, "sim-canopy-r0c1", [R.power(True)])
        drifted = await w.until(lambda: power(w, "canopy-r0c1") == "on", timeout=5, step=0.05)
        healed = await w.until(lambda: power(w, "canopy-r0c1") == "off", timeout=30)
        off_cmds = list(sent)
        await w.engine.set_guest_brightness(40, source="ops")  # turns it on at 40 ...
        await w.engine.set_guest_power(False, source="ops")  # ... and off again, keeping 40 for later
        await w.until(lambda: everywhere(w, power, "off"), timeout=10)
        await w.run_for(9)  # past the guest cooldown
        sent.clear()
        on = await w.engine.set_guest_power(True, source="kiosk")
        lit = await w.until(lambda: everywhere(w, power, "on") and shows_desired(w) and ready(w), timeout=10)
        levels = {level(w, d) for d in MODULES}
        on_state = w.engine.state()
        await w.close()
        return off, dark, before, quiet, (drifted, healed), off_cmds, on, lit, sent, levels, on_state

    off, dark, before, quiet, (drifted, healed), off_cmds, on, lit, on_cmds, levels, on_state = run_virtual(
        main
    )
    assert not off.refused and off.guest_power is False and off.guest_brightness is None
    assert dark is not None and dark <= 5
    assert all(quiet[a] - before[a] == 1 for a in quiet)  # one write each, then left alone while off
    assert [c.rows for c in off_cmds[:9]] == [[R.power(False)]] * 9
    assert drifted is not None and healed is not None and drifted + healed <= 10
    assert not on.refused and on.guest_power is True
    assert lit is not None and len(on_cmds) == 9
    assert all(
        {r.key: r.value for r in c.rows}[("brightness", None)] == 40 for c in on_cmds
    )  # the guest's level
    assert levels == {40} and on_state.to_json()["guest_power"] is True


def test_choosing_a_mood_clears_brightness_and_turns_on_but_re_picking_the_same_look_keeps_both():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "sunset")  # T1 in the fake world
        await w.engine.set_guest_brightness(20, source="ops")
        recompiled = await w.engine.request_mode("sunset", source="reconciler")  # new capabilities, same look
        startup = await w.engine.request_mode(
            "sunset", source="startup"
        )  # the simulator's post-commission pick
        down = w.engine.downgrade("test: a module stayed wrong")  # a tier change keeps the guest's level too
        lowered = await w.until(lambda: everywhere(w, level, 20) and shows_desired(w), timeout=15)
        lowered_state = w.engine.state()
        await w.engine.set_guest_power(False, source="ops")
        await w.until(lambda: everywhere(w, power, "off"), timeout=10)
        await w.run_for(9)
        chosen = await w.engine.request_mode("holiday", source="kiosk")
        shown = await w.until(lambda: w.all_correct() and everywhere(w, power, "on"), timeout=10)
        levels = {level(w, d) for d in MODULES}
        await w.close()
        return recompiled, startup, down, lowered, lowered_state, chosen, shown, levels

    recompiled, startup, down, lowered, lowered_state, chosen, shown, levels = run_virtual(main)
    assert recompiled.guest_brightness == 20 and startup.guest_brightness == 20
    assert down and lowered_state.tier == str(Tier.T2) and lowered_state.guest_brightness == 20
    assert lowered is not None
    assert not chosen.refused and chosen.guest_brightness is None and chosen.guest_power is True
    assert shown is not None and levels == {70}  # holiday's designed brightness
    assert chosen.brightness == 70


@pytest.mark.parametrize(
    "mode,allowed,tier",
    [
        ("lowcountry-calm", None, Tier.STRAND),
        ("holiday", None, Tier.STRAND),
        ("sunset", None, Tier.T1),
        ("sunset", {Tier.T2, Tier.T3}, Tier.T2),
        ("sunset", {Tier.T3}, Tier.T3),
        ("golden", None, Tier.T1),  # a motion plan: every module, one burst
    ],
)
def test_brightness_works_at_every_tier_one_write_per_module(mode, allowed, tier):
    async def main():
        tier_allowed = None
        if allowed is not None:
            tier_allowed = lambda m, t: (t in allowed or t is Tier.STRAND, "not benched")
        w = make_world(settings=QUIET, tier_allowed=tier_allowed)
        await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        await settled_on(w, mode)
        await w.run_for(10)
        before = writes_per_device(w)
        sent: list[tuple[float, object]] = []
        w.executor.add_submit_listener(lambda c: sent.append((w.clock.monotonic(), c)))
        state = await w.engine.set_guest_brightness(25, source="kiosk")
        settled = await w.until(
            lambda: everywhere(w, level, 25) and shows_desired(w) and ready(w), timeout=10
        )
        await w.engine.wait_idle()
        await w.run_for(20)
        after = writes_per_device(w)
        plan = w.engine.current_plan()
        await w.close()
        return state, settled, before, after, sent, plan

    state, settled, before, after, sent, plan = run_virtual(main)
    assert plan.tier is tier and state.tier == str(tier)
    assert settled is not None and settled <= 5
    assert all(after[a] - before[a] == 1 for a in after)
    assert len(sent) == 9 and max(t for t, _ in sent) - min(t for t, _ in sent) < 1e-6  # one burst
    for _, cmd in sent:
        rows = {r.key: r.value for r in cmd.rows}
        assert rows[("brightness", None)] == 25 and ("power", None) in rows
        assert all(r.function_class != R.FRAME_CLASS for r in cmd.rows)  # a control row, never a frame
        if tier in (Tier.T1, Tier.T2):
            assert rows[("color-individual", "custom")] == plan.modules[_id(cmd)].slot


def _id(cmd) -> str:
    return cmd.device_id.removeprefix("sim-")


def test_idle_reset_puts_the_canopy_back_on_at_its_designed_brightness():
    async def main():
        w = make_world(settings=settings_with(kiosk={"idle_return_s": 100_000, "idle_reset_s": 20}))
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        await w.engine.set_guest_brightness(30, source="kiosk")
        await w.run_for(9)
        await w.engine.set_guest_power(False, source="kiosk")  # the last guest input
        await w.run_for(19)
        held = w.engine.state()
        dark = everywhere(w, power, "off")
        reset = await w.until(lambda: everywhere(w, power, "on") and everywhere(w, level, 60), timeout=10)
        after = w.engine.state()
        events = [e for e in w.events("guest") if e["source"] == "idle"]
        await w.close()
        return held, dark, reset, after, events

    held, dark, reset, after, events = run_virtual(main)
    assert held.guest_power is False and dark
    assert reset is not None and reset <= 4  # 20 s after the last input, plus a tick and a write
    assert after.guest_power is True and after.guest_brightness is None and after.brightness == 60
    assert after.mode == "lowcountry-calm"  # idle_scene unset: the look stays
    assert len(events) == 1 and events[0]["action"] == "clear"


def test_idle_reset_can_return_to_the_idle_scene_and_waits_while_paused():
    async def main():
        w = make_world(
            settings=settings_with(
                kiosk={"idle_return_s": 100_000, "idle_reset_s": 20, "idle_scene": "holiday"}
            )
        )
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm", source="kiosk")
        await w.engine.set_guest_power(False, source="ops")
        w.engine.pause_guest_input(True, source="ops")
        await w.run_for(60)
        paused = w.engine.state()
        w.engine.pause_guest_input(False, source="ops")
        await w.run_for(2)
        resumed = w.engine.state()
        shown = await w.until(lambda: w.all_correct() and everywhere(w, power, "on"), timeout=10)
        await w.close()
        return paused, resumed, shown

    paused, resumed, shown = run_virtual(main)
    assert (
        paused.guest_power is False and paused.mode == "lowcountry-calm"
    )  # staff own the canopy while paused
    assert resumed.mode == "holiday" and resumed.guest_power is True and resumed.guest_brightness is None
    assert shown is not None


def test_guest_input_restarts_the_idle_hold_and_the_idle_hold_clears_the_guest_settings():
    async def main():
        w = make_world(settings=settings_with(kiosk={"idle_return_s": 30, "idle_reset_s": 100_000}))
        await w.engine.start(apply_initial=False)
        await settled_on(w, "holiday", source="kiosk")
        await w.run_for(25)
        await w.engine.set_guest_brightness(40, source="kiosk")
        await w.run_for(10)  # 35 s after the mood, 10 s after the dimming
        held = w.engine.state()
        await w.run_for(22)
        returned = w.engine.state()
        shown = await w.until(lambda: w.all_correct() and everywhere(w, level, 60), timeout=10)
        await w.close()
        return held, returned, shown

    held, returned, shown = run_virtual(main)
    assert held.mode == "holiday" and held.guest_brightness == 40
    assert returned.mode == "lowcountry-calm" and returned.guest_brightness is None and returned.guest_power
    assert shown is not None


def test_guest_input_shares_the_cooldown_and_is_refused_while_paused():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        out = {"select": await w.engine.request_mode("holiday", source="kiosk")}
        await w.run_for(2)
        out["dim_in_cooldown"] = await w.engine.set_guest_brightness(30, source="kiosk")
        out["off_in_cooldown"] = await w.engine.set_guest_power(False, source="kiosk")
        out["ops_in_cooldown"] = await w.engine.set_guest_brightness(50, source="ops")  # ops bypass it
        await w.run_for(6.5)
        out["dim"] = await w.engine.set_guest_brightness(30, source="kiosk")
        await w.run_for(2)
        out["select_after_dim"] = await w.engine.request_mode("lowcountry-calm", source="kiosk")
        await w.run_for(10)
        w.engine.pause_guest_input(True, source="ops")
        out["dim_paused"] = await w.engine.set_guest_brightness(70, source="kiosk")
        out["off_paused"] = await w.engine.set_guest_power(False, source="kiosk")
        out["ops_paused"] = await w.engine.set_guest_brightness(70, source="ops")
        await w.close()
        return out

    out = run_virtual(main)
    assert not out["select"].refused
    dim = out["dim_in_cooldown"]
    assert dim.refused and "Try again in 6 s" in dim.message and 5.9 <= dim.retry_after_s <= 6.0
    assert dim.guest_brightness is None  # nothing changed
    assert out["off_in_cooldown"].refused and out["off_in_cooldown"].guest_power is True
    assert not out["ops_in_cooldown"].refused and out["ops_in_cooldown"].guest_brightness == 50
    assert (
        not out["dim"].refused and out["dim"].guest_brightness == 30 and out["dim"].cooldown_until is not None
    )
    assert (
        out["select_after_dim"].refused and out["select_after_dim"].mode == "holiday"
    )  # one shared cooldown
    assert out["dim_paused"].refused and "paused" in out["dim_paused"].message
    assert out["off_paused"].refused and out["off_paused"].guest_power is True
    assert not out["ops_paused"].refused and out["ops_paused"].guest_brightness == 70


def test_resting_starting_and_stopped_refusals_and_an_outage_keeps_an_ops_change_for_recovery():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        out = {"starting": await w.engine.set_guest_brightness(30, source="ops")}  # no look yet
        await settled_on(w, "lowcountry-calm")
        w.sim.faults.set_outage(True)
        await w.until(lambda: w.engine.outage is not None, timeout=90)
        sent = []
        w.executor.add_submit_listener(sent.append)
        out["kiosk_resting"] = await w.engine.set_guest_power(False, source="kiosk")
        out["ops_resting"] = await w.engine.set_guest_brightness(30, source="ops")
        await w.run_for(0.5)  # a burst runs on the loop: give one the chance to go out before counting
        out["sent_while_resting"] = len(sent)
        await w.run_for(120)
        w.sim.faults.set_outage(False)
        out["recovered"] = await w.until(lambda: everywhere(w, level, 30) and ready(w), timeout=120)
        await w.close()
        out["stopped"] = await w.engine.set_guest_power(False, source="ops")
        return out

    out = run_virtual(main)
    assert out["starting"].refused and out["starting"].message == "The canopy is getting ready."
    assert out["kiosk_resting"].refused and out["kiosk_resting"].message.startswith("The canopy is resting")
    assert not out["ops_resting"].refused and out["ops_resting"].guest_brightness == 30
    assert out["sent_while_resting"] == 0  # stored; recovery re-applies the look with it
    assert out["recovered"] is not None and out["recovered"] <= 60
    assert out["stopped"].refused and out["stopped"].guest_power is True


def test_safe_look_and_ops_clear_turn_the_canopy_back_on_at_the_designed_brightness():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "holiday")
        await w.engine.set_guest_brightness(15, source="ops")
        await w.engine.set_guest_power(False, source="ops")
        await w.until(lambda: everywhere(w, power, "off"), timeout=10)
        safe = await w.engine.safe_look(source="ops")
        safe_shown = await w.until(lambda: w.all_correct() and everywhere(w, level, 60), timeout=10)
        await w.engine.set_guest_brightness(15, source="ops")
        await w.until(lambda: everywhere(w, level, 15), timeout=10)
        await w.engine.wait_idle()
        sent = []
        w.executor.add_submit_listener(sent.append)
        cleared = await w.engine.clear_guest_overrides(source="ops")
        clear_shown = await w.until(lambda: everywhere(w, level, 60) and ready(w), timeout=10)
        nothing = await w.engine.clear_guest_overrides(source="ops")
        await w.run_for(3)
        await w.close()
        return safe, safe_shown, cleared, clear_shown, nothing, sent

    safe, safe_shown, cleared, clear_shown, nothing, sent = run_virtual(main)
    assert safe.mode == "lowcountry-calm" and safe.guest_power is True and safe.guest_brightness is None
    assert safe_shown is not None
    assert cleared.guest_brightness is None and cleared.guest_power is True and clear_shown is not None
    assert not nothing.refused
    assert len(sent) == 9  # clearing again sent nothing


def test_commissioning_refuses_guest_changes_and_keeps_the_ones_in_force():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        await w.engine.set_guest_brightness(30, source="ops")
        await w.until(lambda: everywhere(w, level, 30) and ready(w), timeout=10)
        await w.engine.wait_idle()
        task = asyncio.get_running_loop().create_task(w.engine.commission(source="ops"))
        await w.run_for(0.1)
        during = w.engine.commissioning
        refused = {
            "brightness": await w.engine.set_guest_brightness(60, source="ops"),
            "power": await w.engine.set_guest_power(False, source="kiosk"),
            "clear": await w.engine.clear_guest_overrides(source="ops"),
        }
        await task
        await w.run_for(10)
        after = w.engine.state()
        levels = {level(w, d) for d in MODULES}
        await w.close()
        return during, refused, after, levels

    during, refused, after, levels = run_virtual(main)
    assert during
    for state in refused.values():
        assert state.refused and state.message == "The canopy is being set up. Try again in a minute."
    assert after.guest_brightness == 30 and levels == {30}  # slot writes never touch the show rows


def test_a_locked_module_gets_the_guest_look_when_it_is_released():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        locked = "canopy-r1c1"
        lease = w.locks.acquire(w.address(locked), "module page · test")
        await w.engine.set_guest_power(False, source="ops")
        others = await w.until(lambda: all(power(w, d) == "off" for d in MODULES if d != locked), timeout=10)
        await w.run_for(5)
        held = power(w, locked)
        w.locks.release(w.address(locked), lease.token)
        released = await w.until(lambda: power(w, locked) == "off", timeout=10)
        await w.close()
        return others, held, released

    others, held, released = run_virtual(main)
    assert others is not None and held == "on" and released is not None and released <= 3


def test_rapid_ops_brightness_changes_keep_the_rate_budget_and_end_on_the_last_level():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        gaps: list[float] = []
        last: dict[str, float] = {}
        original = w.sim.write

        async def timed(device_id, rows, *, purpose, **kw):
            now = w.clock.monotonic()
            if device_id in last:
                gaps.append(now - last[device_id])
            last[device_id] = now
            return await original(device_id, rows, purpose=purpose, **kw)

        w.sim.write = timed
        depths = []
        for i in range(
            40
        ):  # ops (or a debug panel) bypasses the guest cooldown: the executor must hold the budget
            await w.engine.set_guest_brightness(10 + 2 * i, source="ops")
            depths.append(w.executor.queue_depth())
            await w.run_for(0.2)
        settled = await w.until(lambda: everywhere(w, level, 88) and ready(w), timeout=20)
        await w.close()
        return gaps, depths, settled

    gaps, depths, settled = run_virtual(main)
    assert min(gaps) >= 1.0 - 1e-6  # ≤ 1 write/s per device
    assert max(depths) <= 18  # ≤ 2 per device however fast the changes come
    assert settled is not None


def test_guest_settings_config_defaults_and_validation():
    kiosk = KioskSettings()
    assert (kiosk.guest_brightness_min, kiosk.idle_reset_s, kiosk.idle_scene) == (10, 300, None)
    shipped = load_settings().kiosk
    assert (shipped.guest_brightness_min, shipped.idle_reset_s, shipped.idle_scene) == (10, 300, None)
    for bad in ({"guest_brightness_min": 0}, {"guest_brightness_min": 101}, {"idle_reset_s": 0}):
        with pytest.raises(ValidationError):
            ConductorSettings.model_validate({"kiosk": bad})


def test_idle_reset_does_nothing_without_a_guest_setting_so_it_never_alternates_with_the_idle_hold():
    async def main():
        w = make_world(
            settings=settings_with(kiosk={"idle_return_s": 30, "idle_reset_s": 50, "idle_scene": "holiday"})
        )
        await w.engine.start(apply_initial=False)
        await settled_on(
            w, "lowcountry-calm", source="kiosk"
        )  # the idle hold's mood: the hold has nothing to do
        await w.engine.set_guest_brightness(30, source="ops")
        await w.run_for(600)
        idle = [e["mode"] for e in w.events("mode") if e["source"] == "idle"]
        final = w.engine.state()
        await w.close()
        return idle, final

    idle, final = run_virtual(main)
    # The reset shows the idle scene once (clearing the dimming); the hold returns to its mood 30 s later; then quiet.
    assert idle == ["holiday", "lowcountry-calm"]
    assert final.mode == "lowcountry-calm" and final.guest_brightness is None and final.guest_power


def test_idle_reset_never_undoes_a_mood_ops_chose_after_the_last_guest_input():
    async def main():
        w = make_world(
            settings=settings_with(
                kiosk={"idle_return_s": 100_000, "idle_reset_s": 20, "idle_scene": "holiday"}
            )
        )
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        await w.engine.set_guest_brightness(30, source="kiosk")
        await w.run_for(10)
        await w.engine.request_mode("sunset", source="ops")
        await w.run_for(120)
        final = w.engine.state()
        await w.close()
        return final

    final = run_virtual(main)
    assert final.mode == "sunset" and final.guest_brightness is None


def test_an_idle_reset_due_during_an_outage_waits_and_rides_the_recovery_burst():
    async def main():
        w = make_world(settings=settings_with(kiosk={"idle_return_s": 100_000, "idle_reset_s": 20}))
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        await w.engine.set_guest_power(False, source="ops")
        await w.until(lambda: everywhere(w, power, "off") and ready(w), timeout=10)
        w.sim.faults.set_outage(True)
        await w.until(lambda: w.engine.outage is not None, timeout=90)
        await w.run_for(60)  # well past idle_reset_s: nothing may change while the canopy rests
        resting = w.engine.state()
        sent = []
        w.executor.add_submit_listener(sent.append)
        w.sim.faults.set_outage(False)
        lit = await w.until(lambda: everywhere(w, power, "on") and everywhere(w, level, 60) and ready(w), 120)
        await w.engine.wait_idle()
        await w.run_for(10)
        final = w.engine.state()
        await w.close()
        return resting, sent, lit, final

    resting, sent, lit, final = run_virtual(main)
    assert resting.guest_power is False
    assert lit is not None and final.guest_power is True
    # One recovery burst, already back on: no "off" burst followed by a second "on" one.
    assert len(sent) == 9 and all(c.rows != [R.power(False)] for c in sent)


def test_resuming_with_both_the_idle_hold_and_the_idle_reset_due_is_one_burst():
    async def main():
        w = make_world(settings=settings_with(kiosk={"idle_return_s": 30, "idle_reset_s": 20}))
        await w.engine.start(apply_initial=False)
        await settled_on(w, "holiday", source="kiosk")
        await w.engine.set_guest_power(False, source="ops")
        await w.until(lambda: everywhere(w, power, "off") and ready(w), timeout=10)
        w.engine.pause_guest_input(True, source="ops")
        await w.run_for(60)
        await w.engine.wait_idle()
        sent = []
        w.executor.add_submit_listener(sent.append)
        w.engine.pause_guest_input(False, source="ops")
        shown = await w.until(lambda: w.all_correct() and everywhere(w, power, "on"), timeout=10)
        await w.engine.wait_idle()
        await w.run_for(5)
        final = w.engine.state()
        await w.close()
        return sent, shown, final

    sent, shown, final = run_virtual(main)
    assert shown is not None and final.mode == "lowcountry-calm" and final.guest_power is True
    assert len(sent) == 9


def test_a_refused_idle_reset_is_tried_once_per_idle_period():
    async def main():
        w = make_world(settings=settings_with(kiosk={"idle_return_s": 100_000, "idle_reset_s": 20}))
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        await w.engine.set_guest_brightness(30, source="ops")
        calls = []

        async def refuse(*, source):
            calls.append(w.clock.monotonic())
            return w.engine._refused("no (test)", None)

        w.engine.clear_guest_overrides = refuse
        await w.run_for(35)
        alerts = [e for e in w.events("alert") if "Idle reset refused" in e["message"]]
        await w.close()
        return calls, alerts

    calls, alerts = run_virtual(main)
    assert len(calls) == 1 and len(alerts) == 1


def test_switching_off_again_sends_nothing_and_starts_no_cooldown():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        await w.engine.set_guest_power(False, source="ops")
        await w.until(lambda: everywhere(w, power, "off") and ready(w), timeout=10)
        await w.engine.wait_idle()
        writes = w.sim.stats()["writes"]
        again = await w.engine.set_guest_power(False, source="kiosk")
        await w.run_for(3)
        repeat_writes = w.sim.stats()["writes"] - writes
        events = [e for e in w.events("guest") if e["source"] == "kiosk"]
        await w.close()
        return again, repeat_writes, events

    again, repeat_writes, events = run_virtual(main)
    assert not again.refused and again.cooldown_until is None and again.guest_power is False
    assert repeat_writes == 0 and events == []


@pytest.mark.parametrize("bad", [True, False, float("nan"), 30.0, "30", None])
def test_brightness_that_is_not_a_whole_number_is_refused_not_coerced(bad):
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        writes = w.sim.stats()["writes"]
        state = await w.engine.set_guest_brightness(bad, source="ops")
        await w.run_for(3)
        repeat_writes = w.sim.stats()["writes"] - writes
        await w.close()
        return state, repeat_writes

    state, repeat_writes = run_virtual(main)
    assert state.refused and state.message == "Brightness must be a whole number from 10 to 100."
    assert state.guest_brightness is None and repeat_writes == 0


class _RangedCompiler(FakeCompiler):
    """Modules whose profiles only allow 20–80 % (the compiler records each device's range on its target)."""

    def _target(self, *args):
        mt = super()._target(*args)
        mt.brightness_range = (20, 80)
        return mt


def test_a_guest_level_is_clamped_to_each_device_s_own_range():
    async def main():
        w = make_world(settings=QUIET, compiler=_RangedCompiler())
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        await w.engine.set_guest_brightness(10, source="ops")
        low = await w.until(lambda: everywhere(w, level, 20) and ready(w), timeout=10)
        await w.engine.set_guest_brightness(100, source="ops")
        high = await w.until(lambda: everywhere(w, level, 80) and ready(w), timeout=10)
        await w.engine.wait_idle()
        await w.run_for(30)  # the reconciler expects the clamped level too: nothing more is sent
        stable = everywhere(w, level, 80) and ready(w)
        await w.close()
        return low, high, stable

    low, high, stable = run_virtual(main)
    assert low is not None and high is not None and stable


def test_a_new_slot_record_keeps_the_guest_level_in_what_the_tracker_expects():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        await settled_on(w, "sunset")  # T1: the expectation follows the slot's recorded hash
        await w.engine.set_guest_brightness(20, source="ops")
        await w.until(lambda: everywhere(w, level, 20) and ready(w), timeout=10)
        await w.engine.wait_idle()
        plan = w.engine.current_plan()
        mt = plan.modules["canopy-r1c1"]
        other = frame_for("sunset", 7, 30)  # a recalibration re-recorded the slot with another frame
        assert R.slot_hash(other) != R.slot_hash(mt.slot_value)
        w.engine._record_slot_value("canopy-r1c1", mt.slot, plan, other)
        snap = w.engine.tracker.snapshot(w.address("canopy-r1c1"))
        await w.close()
        return snap

    snap = run_virtual(main)
    desired = {(r["functionClass"], r.get("functionInstance")): r["value"] for r in snap["desired"]}
    assert desired[("brightness", None)] == 20


def test_an_idle_scene_that_is_not_a_mode_refuses_to_start():
    services = type("S", (), {"config_dir": CONFIG_DIR})()
    services.settings = settings_with(kiosk={"idle_scene": "lowcountry-clam"})
    with pytest.raises(ConfigError, match="idle_scene: 'lowcountry-clam' isn't a mode"):
        ShowWiring(services)
    services.settings = settings_with(kiosk={"idle_scene": "lowcountry-calm"})
    ShowWiring(services)


def test_a_scheduled_re_assert_judges_readback_against_the_guest_level():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await settled_on(w, "lowcountry-calm")
        await w.engine.set_guest_brightness(30, source="ops")
        await w.until(lambda: everywhere(w, level, 30) and ready(w), timeout=10)
        await w.engine.wait_idle()
        target = w.engine.target_of("canopy-r2c0")
        for d in MODULES:  # fresh readback everywhere: every module shows the guest's 30
            t = w.engine.target_of(d)
            w.engine.tracker.note_readback(
                t.address, await t.driver.read_state(t.address, purpose=Purpose.ENGINE)
            )
        clean = await w.engine.reconciler.reassert()
        app_edit(w, target.address, [R.brightness(60)])  # drift onto exactly the designed level
        rr = await target.driver.read_state(target.address, purpose=Purpose.ENGINE)
        w.engine.tracker.note_readback(target.address, rr)
        drifted = await w.engine.reconciler.reassert()
        await w.close()
        return clean, drifted

    clean, drifted = run_virtual(main)
    assert clean == []  # 30 everywhere is what the engine wants: nothing to resend
    assert drifted == ["canopy-r2c0"]
