"""The reconciler on the real SimDriver (virtual time): self-heal, true-up, motion all-or-none, hysteresis (5% throttling
never downgrades; persistent failure downgrades and later upgrades), outage → resting → recovery, idle hold, and
slot drift with its rewrite limit."""

from __future__ import annotations

import itertools

from fake_compiler import MODULES, FakeCompiler, make_world, settings_with

from conductor import rows as R
from conductor.clock import run_virtual
from conductor.drivers.base import StateRow
from conductor.engine_api import CanopyPhase, Tier
from conductor.sim.settings import SimSettings

QUIET = settings_with(kiosk={"idle_return_s": 100_000})  # no idle hold unless a test wants it


def drop_next_write(w, address: str, count: int = 1) -> list[float]:
    """Make the simulated cloud answer 200 to the next `count` writes to `address` and store nothing."""
    original = w.sim.cloud.accept_wire
    dropped: list[float] = []

    def accept(body, *, silent_drop=False):
        if body.get("metadeviceId") == address and len(dropped) < count:
            dropped.append(w.clock.monotonic())
            silent_drop = True
        return original(body, silent_drop=silent_drop)

    w.sim.cloud.accept_wire = accept
    return dropped


def app_edit(w, address: str, rows: list[StateRow]) -> None:
    """A change made outside the Conductor (the Hubspace app), stored by the cloud as a normal PUT."""
    stamp = int(w.clock.now() * 1000)
    response = w.sim.cloud.accept_wire({"metadeviceId": address, "values": [r.to_wire(stamp) for r in rows]})
    assert response.ok, response.error


def writes_per_device(w) -> dict[str, int]:
    return dict(w.sim.stats()["writes_per_device"])


def test_silent_drop_self_heals_within_10_s_and_only_that_module_is_resent():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        dropped = drop_next_write(w, "sim-canopy-r1c2")
        await w.engine.request_mode("holiday", source="kiosk")
        healed = await w.until(w.all_correct, timeout=30)
        await w.engine.wait_idle()
        await w.run_for(10)
        writes = writes_per_device(w)
        await w.close()
        return dropped, healed, writes

    dropped, healed, writes = run_virtual(main)
    assert len(dropped) == 1
    assert healed is not None and healed <= 10.0
    assert writes["sim-canopy-r1c2"] == 2
    assert all(n == 1 for a, n in writes.items() if a != "sim-canopy-r1c2")


def test_repeated_drops_on_one_module_still_heal_within_10_s():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        drop_next_write(w, "sim-canopy-r0c0", count=3)
        await w.engine.request_mode("holiday", source="kiosk")
        healed = await w.until(w.all_correct, timeout=30)
        await w.close()
        return healed

    healed = run_virtual(main)
    assert healed is not None and healed <= 10.0


def test_an_app_edit_is_undone_by_the_rolling_true_up_within_10_s():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.until(w.all_correct, timeout=10)
        await w.engine.wait_idle()
        app_edit(w, "sim-canopy-r2c0", [R.color_mode("color"), R.color_rgb(0, 0, 255)])
        await w.run_for(3)
        wrong = not w.correct("canopy-r2c0")
        healed = await w.until(w.all_correct, timeout=90)
        await w.close()
        return wrong, healed, w.engine.reconciler.stats

    wrong, healed, stats = run_virtual(main)
    assert wrong
    # each module is read every 5 s (rolling), then re-sent at once: within the 10 s self-heal target (review gap)
    assert healed is not None and healed + 3 <= 10
    assert stats["true_up_reads"] >= 1 and stats["drift_resends"] >= 1


def test_motion_plan_retriggers_all_modules_as_one_burst():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        before = writes_per_device(w)
        sends: list[tuple[float, str, str]] = []
        w.executor.add_submit_listener(lambda c: sends.append((w.clock.monotonic(), c.device_id, c.label)))
        drop_next_write(w, "sim-canopy-r1c1")
        await w.engine.request_mode("golden", source="kiosk")
        healed = await w.until(w.all_correct, timeout=30)
        await w.engine.wait_idle()
        await w.run_for(10)
        after = writes_per_device(w)
        await w.close()
        return before, after, sends, healed, w.engine.current_plan()

    before, after, sends, healed, plan = run_virtual(main)
    assert plan.motion and healed is not None and healed <= 10.0
    resends = [s for s in sends if s[2].startswith("re-assert")]
    assert sorted(a for _, a, _ in resends) == sorted(f"sim-{m}" for m in MODULES)  # all nine, not one
    assert max(t for t, _, _ in resends) - min(t for t, _, _ in resends) < 1e-6  # in one burst
    assert all(after[a] - before.get(a, 0) == 2 for a in after)


def test_motion_plan_never_resends_a_single_module():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("golden", source="ops")
        await w.until(w.all_correct, timeout=10)
        await w.engine.wait_idle()
        sends: list[str] = []
        w.executor.add_submit_listener(lambda c: sends.append(c.device_id))
        app_edit(w, "sim-canopy-r0c2", [R.color_mode("white"), R.color_temperature(4000)])
        await w.run_for(2)  # the controller applies it
        assert not w.correct("canopy-r0c2")
        healed = await w.until(w.all_correct, timeout=90)
        await w.close()
        return sends, healed

    sends, healed = run_virtual(main)
    assert healed is not None
    assert sorted(sends) == sorted(f"sim-{m}" for m in MODULES)


def test_five_percent_throttling_never_downgrades():
    async def main():
        w = make_world(
            settings=QUIET,
            sim_settings=SimSettings.dev().with_(throttle_429_rate=0.05, silent_drop_rate=0.01),
            seed=7,
        )
        await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        modes = ["sunset", "golden", "holiday", "lowcountry-calm"]
        for i in range(200):  # an ops change every 9 s for 30 min, on top of true-ups and re-asserts
            await w.engine.request_mode(modes[i % 4], source="ops")
            await w.run_for(9)
        await w.until(w.all_correct, timeout=30)
        throttled = [e["t"] for e in w.events("attempt") if e["outcome"] == "throttled"]
        await w.close()
        return w.events("tier"), throttled, w.engine.floors, w.engine.current_plan().tier

    tiers, throttled, floors, tier = run_virtual(main)
    busiest = max(sum(1 for t in throttled if s <= t < s + 60) for s in throttled)
    assert busiest >= 3  # plenty of individual 429s landed within a minute ...
    assert tiers == [] and floors == {}  # ... and none of them downgraded anything
    assert tier is Tier.STRAND or tier is Tier.T1


def test_persistent_failure_downgrades_after_10_s_and_upgrades_after_5_clean_minutes():
    async def main():
        compiler = FakeCompiler(broken={("sunset", Tier.T1)}, broken_module="canopy-r1c1")
        w = make_world(settings=QUIET, compiler=compiler)
        await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        first = await w.engine.request_mode("sunset", source="ops")
        down = await w.until(lambda: w.engine.current_plan().tier is not Tier.T1, timeout=60)
        during = w.engine.state()
        settled = await w.until(w.all_correct, timeout=15)
        await w.run_for(2)
        downgraded = w.engine.state()
        compiler.broken.clear()  # the cause went away (say, a bench flag was fixed)
        up = await w.until(lambda: w.engine.current_plan().tier is Tier.T1, timeout=400)
        await w.until(lambda: w.engine.state().phase is CanopyPhase.READY, timeout=15)
        upgraded = w.engine.state()
        await w.close()
        return first, down, during, settled, downgraded, up, upgraded, w.events("tier")

    first, down, during, settled, downgraded, up, upgraded, tiers = run_virtual(main)
    assert first.tier == "canvas" and during.tier == "slot"
    assert down is not None and 10 <= down <= 16  # retried first, downgraded only once wrong > 10 s
    assert settled is not None
    assert downgraded.tier == "slot" and downgraded.phase is CanopyPhase.READY
    assert downgraded.feature == "A pattern painted in the Hubspace app (fake)."  # the copy follows the tier
    assert up is not None and 295 <= up <= 310
    assert upgraded.tier == "canvas" and upgraded.phase is CanopyPhase.READY
    assert [(t["from"], t["to"], t["direction"]) for t in tiers] == [
        ("canvas", "slot", "down"),
        ("slot", "canvas", "up"),
    ]


def test_a_module_wrong_past_the_window_shows_needs_attention_when_there_is_no_lower_tier():
    async def main():
        compiler = FakeCompiler(broken={("holiday", Tier.STRAND)}, broken_module="canopy-r0c1")
        w = make_world(settings=QUIET, compiler=compiler)
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("holiday", source="ops")
        await w.run_for(5)
        early = w.engine.state()
        await w.run_for(12)
        late = w.engine.state()
        await w.close()
        return early, late, w.events("alert")

    early, late, alerts = run_virtual(main)
    assert early.phase is CanopyPhase.UPDATING  # still re-asserting inside the window
    assert late.phase is CanopyPhase.NEEDS_ATTENTION and late.tier == "strand"
    assert "M2" in late.message
    assert any("no lower tier" in a["message"] for a in alerts)


def test_persistent_throttling_downgrades_and_clean_minutes_upgrade():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("sunset", source="ops")
        await w.until(w.all_correct, timeout=10)
        await w.engine.wait_idle()
        w.sim.faults.set_throttle_rate(1.0)  # the account is being throttled hard
        await w.engine.request_mode("golden", source="ops")
        down = await w.until(lambda: w.engine.floors.get("golden"), timeout=60)
        w.sim.faults.set_throttle_rate(0.0)
        settled = await w.until(w.all_correct, timeout=30)
        tier_after_down = w.engine.current_plan().tier
        up = await w.until(lambda: not w.engine.floors, timeout=400)
        await w.close()
        return down, settled, tier_after_down, up, w.events("tier")

    down, settled, tier_after_down, up, tiers = run_virtual(main)
    assert down is not None and settled is not None
    assert tier_after_down is Tier.T3
    assert "still throttled after retries" in tiers[0]["reason"]
    assert up is not None and tiers[-1]["direction"] == "up"


def test_outage_rests_the_canopy_and_recovery_reapplies_within_60_s():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("holiday", source="ops")
        await w.until(w.all_correct, timeout=10)
        await w.engine.wait_idle()
        w.sim.faults.set_outage(True)
        resting_after = await w.until(lambda: w.engine.state().phase is CanopyPhase.RESTING, timeout=90)
        resting = w.engine.state()
        kiosk = await w.engine.request_mode("lowcountry-calm", source="kiosk")
        ops = await w.engine.request_mode("sunset", source="ops")  # becomes current; goes out on recovery
        plan = w.engine.current_plan()
        await w.run_for(30 * 60)
        w.sim.faults.set_outage(False)
        recovered = await w.until(lambda: w.all_correct(plan), timeout=120)
        await w.run_for(3)
        after = w.engine.state()
        await w.close()
        return (
            resting_after,
            resting,
            kiosk,
            ops,
            recovered,
            after,
            w.engine.stats["outages"],
            w.events("tier"),
        )

    resting_after, resting, kiosk, ops, recovered, after, outages, tiers = run_virtual(main)
    assert resting_after is not None and resting_after <= 65  # the next true-up notices
    assert resting.message == "The canopy is resting. It will be back shortly."
    assert kiosk.refused and kiosk.message == resting.message and kiosk.retry_after_s == 10
    assert not ops.refused and ops.mode == "sunset" and ops.phase is CanopyPhase.RESTING
    assert recovered is not None and recovered <= 60
    assert after.phase is CanopyPhase.READY and after.mode == "sunset"
    assert len(outages) == 1 and tiers == []


def test_a_window_of_500s_is_an_outage_too():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.until(w.all_correct, timeout=10)
        w.sim.faults.start_500_window(20 * 60)
        await w.run_for(1)
        await w.engine.request_mode("holiday", source="ops")  # fails with 500s on every module
        resting = await w.until(lambda: w.engine.outage is not None, timeout=10)
        plan = w.engine.current_plan()
        await w.run_for(20 * 60)
        recovered = await w.until(lambda: w.all_correct(plan), timeout=120)
        await w.close()
        return resting, recovered, w.engine.stats["outages"][0]["reason"], w.events("tier")

    resting, recovered, reason, tiers = run_virtual(main)
    assert resting is not None and resting <= 5
    assert "server error" in reason
    assert recovered is not None and recovered <= 60
    assert tiers == []


def test_driver_recovery_callback_probes_at_once():
    async def main():
        w = make_world(
            settings=settings_with(
                kiosk={"idle_return_s": 100_000}, reconciler={"outage_probe_interval_s": 600}
            )
        )
        w.sim.on_recovery = []  # the Afero adapter's watchdog list
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("holiday", source="ops")
        await w.until(w.all_correct, timeout=10)
        w.sim.faults.set_outage(True)
        await w.until(lambda: w.engine.outage is not None, timeout=90)
        await w.run_for(60)
        w.sim.faults.set_outage(False)
        for callback in w.sim.on_recovery:
            callback("canopy")
        back = await w.until(lambda: w.engine.outage is None, timeout=30)
        await w.close()
        registered = len(w.sim.on_recovery)
        return back, registered

    back, registered = run_virtual(main)
    assert back is not None and back <= 5  # not the 600 s probe interval
    assert registered == 0  # stop() unsubscribes


def test_idle_hold_returns_to_calm_and_not_while_paused():
    async def main():
        w = make_world(settings=settings_with(kiosk={"idle_return_s": 180}))
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("holiday", source="kiosk")
        await w.run_for(170)
        before = w.engine.current_plan().mode_id
        await w.run_for(15)
        after = w.engine.current_plan().mode_id
        idle_events = [e for e in w.events("mode") if e["source"] == "idle"]
        await w.engine.request_mode("sunset", source="kiosk")
        w.engine.pause_guest_input(True, source="ops")
        await w.run_for(400)
        paused = w.engine.current_plan().mode_id
        await w.close()
        return before, after, idle_events, paused

    before, after, idle_events, paused = run_virtual(main)
    assert before == "holiday" and after == "lowcountry-calm"
    assert len(idle_events) == 1 and idle_events[0]["mode"] == "lowcountry-calm"
    assert paused == "sunset"


def test_drifted_t1_slot_is_rewritten_at_most_once_per_10_min_then_alerted():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode(
            "holiday", source="ops"
        )  # a strand look; the slot drifts in the background
        await w.run_for(10 * 60)  # commissioning counts as a write: the limit starts there
        address, slot = "sim-canopy-r2c2", "custom-1"
        recorded = w.recorded[("canopy-r2c2", slot)]
        other = R.frame_value(None, [R.FrameEntry(0, 0, 255)] * 30, expected_length=30)

        def stored_hash():
            stored = {r.key: r.value for r in w.sim.cloud_view(address)}
            return R.slot_hash(stored[(R.FRAME_CLASS, slot)])

        app_edit(w, address, [R.slot_write(slot, other)])
        first = await w.until(lambda: stored_hash() == recorded, timeout=75)
        await w.run_for(10)  # let the rewrite's verification finish
        app_edit(w, address, [R.slot_write(slot, other)])  # drifts again soon after
        await w.run_for(5 * 60)
        still_drifted = stored_hash() != recorded
        second = await w.until(lambda: stored_hash() == recorded, timeout=6 * 60)
        await w.run_for(10)
        alerts = [a["message"] for a in w.events("alert") if a.get("device_id") == "canopy-r2c2"]
        slot_writes = [
            c
            for c in w.events("command")
            if c["device_id"] == address and c["label"].startswith("slot rewrite")
        ]
        await w.close()
        return first, still_drifted, second, alerts, slot_writes, w.engine.reconciler.stats

    first, still_drifted, second, alerts, slot_writes, stats = run_virtual(main)
    assert first is not None and first <= 70
    assert still_drifted  # inside the 10-minute limit: left alone
    assert second is not None
    assert len(slot_writes) == 2 and stats["slot_rewrites"] == 2
    assert any("rewrote it (applied)" in a for a in alerts)
    assert any("left alone" in a for a in alerts)


def test_drifted_t2_slot_is_only_alerted():
    async def main():
        compiler = FakeCompiler()
        w = make_world(
            settings=QUIET, compiler=compiler, tier_allowed=lambda m, t: (t is not Tier.T1, "no Gate C")
        )
        address = "sim-canopy-r0c0"
        stored = {r.key: r.value for r in w.sim.cloud_view(address)}
        compiler.t2_hashes[("canopy-r0c0", "custom-1")] = R.slot_hash(stored[(R.FRAME_CLASS, "custom-1")])
        await w.engine.start(apply_initial=False)
        state = await w.engine.request_mode("sunset", source="ops")
        await w.until(w.all_correct, timeout=10)
        app_edit(
            w,
            address,
            [R.slot_write("custom-1", R.frame_value(None, [R.FrameEntry(9, 9, 9)] * 30, expected_length=30))],
        )
        await w.run_for(130)
        alerts = [a["message"] for a in w.events("alert") if a.get("device_id") == "canopy-r0c0"]
        slot_writes = [c for c in w.events("command") if c["device_id"] == address and c["rows"] == 1]
        await w.close()
        return state, alerts, slot_writes

    state, alerts, slot_writes = run_virtual(main)
    assert state.tier == "slot"
    assert sum("hand-painted pattern in custom-1 changed" in a for a in alerts) == 1  # once, not every minute
    assert slot_writes == []


def test_an_offline_module_needs_attention_but_never_downgrades():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("sunset", source="ops")
        await w.until(w.all_correct, timeout=10)
        w.sim.faults.set_available("sim-canopy-r2c2", False)  # the controller lost power
        await w.run_for(80)  # a true-up sees `available: false`, then the 10 s window passes
        offline = w.engine.state()
        w.sim.faults.set_available("sim-canopy-r2c2", True)
        back = await w.until(lambda: w.engine.state().phase is CanopyPhase.READY, timeout=90)
        await w.close()
        return offline, back, w.events("tier")

    offline, back, tiers = run_virtual(main)
    assert (
        offline.phase is CanopyPhase.NEEDS_ATTENTION
        and "Offline" in offline.message
        and "M9" in offline.message
    )
    assert offline.tier == "canvas" and tiers == []
    assert back is not None


# ---- stepped looks (D65): the reconciler stands down for what each step already verifies ----------------------
def test_a_moving_stepped_look_has_no_true_up_reads_but_its_slots_are_still_checked_every_minute():
    from test_engine_program import quiet_start, stepped_world

    async def main():
        w = stepped_world()
        await quiet_start(w)
        checks: list[tuple[float, str]] = []
        original = w.engine.reconciler.check_slots

        def spy(device_id, target, rows):
            checks.append((w.clock.monotonic(), device_id))
            return original(device_id, target, rows)

        w.engine.reconciler.check_slots = spy
        t0 = w.clock.monotonic()
        await w.engine.request_mode("radial-outward", source="ops")
        await w.run_for(4)
        reads_early = w.engine.reconciler.stats["true_up_reads"]
        await w.run_for(80)
        reads_late = w.engine.reconciler.stats["true_up_reads"]
        moving = w.engine.stepping
        await w.run_for(30)  # the run is over: the rolling reads come back
        reads_after = w.engine.reconciler.stats["true_up_reads"]
        await w.close()
        return t0, checks, reads_early, reads_late, reads_after, moving

    t0, checks, early, late, after, moving = run_virtual(main)
    assert moving and late == early  # flat while it moves: every step is a verified write
    assert after > late
    for device_id in MODULES:
        times = [t - t0 for t, d in checks if d == device_id and t < t0 + 84]
        during = [t for t in times if t >= 4]
        assert during, device_id  # checked from the steps' verification readbacks …
        gaps = [b - a for a, b in itertools.pairwise(times)]
        # … never more than a minute (and a step's verification) after the last check, at most once a minute
        assert all(60 <= g <= 60 + 6 + 3 for g in gaps if g >= 4), (device_id, times)


def test_a_stepped_module_that_differs_waits_for_the_next_step_unless_its_write_failed_well_before_it():
    from test_engine_program import quiet_start, stepped_world

    from conductor.executor_api import CommandStatus

    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="ops")
        await w.run_for(9.0)  # step 1 is out and verified: the next is 3 s away
        runner, rec = w.engine.runner, w.engine.reconciler
        a, b, c = w.address("canopy-r0c0"), w.address("canopy-r2c2"), w.address("canopy-r1c1")
        w.engine.last_status[a] = CommandStatus.FAILED
        app_edit(
            w, c, [R.color_rgb(1, 2, 3)]
        )  # c differs on its readback, but its write didn't fail: the step fixes it
        w.tracker.note_readback(c, await w.sim.read_state(c))
        now_sent = await rec.reassert()
        deferred_early = set(runner.deferred)
        await w.run_for(runner.next_step_in() - 1.5)  # 1.5 s before step 2
        w.engine.last_status[b] = CommandStatus.FAILED
        n = len(w.writes)
        deferred_sent = await rec.reassert()
        deferred = set(runner.deferred)
        # a deferred module goes out with the step even when the step leaves its rows as they were sent
        w.engine.sent_rows["canopy-r2c2"] = (
            w.engine.current_plan().program.steps[runner.index + 1].rows["canopy-r2c2"]
        )
        await w.run_for(2.5)
        b_writes = [x for x in w.writes[n:] if x[1] == "canopy-r2c2"]
        await w.close()
        return now_sent, deferred_early, deferred_sent, deferred, b_writes

    now_sent, deferred_early, deferred_sent, deferred, b_writes = run_virtual(main)
    assert now_sent == ["canopy-r0c0"]  # failed, and the next step is 3 s off: re-sent now
    assert deferred_early == {"canopy-r1c1"}  # differs without a failed write: the step 3 s away carries it
    # failed, but the step 1.5 s away carries it (and still the one that differs)
    assert deferred_sent == [] and deferred == {"canopy-r1c1", "canopy-r2c2"}
    assert len(b_writes) == 1  # one write, with the step


def freeze_readback(w, addresses: set[str], seconds: float) -> None:
    """Oct 21 live (D82): Hubspace answers 200 to every write to `addresses` for `seconds` while their readback
    doesn't move. From the Conductor's side that is a run of silent drops."""
    original = w.sim.cloud.accept_wire
    until = w.clock.monotonic() + seconds

    def accept(body, *, silent_drop=False):
        if body.get("metadeviceId") in addresses and w.clock.monotonic() < until:
            silent_drop = True
        return original(body, silent_drop=silent_drop)

    w.sim.cloud.accept_wire = accept


def test_a_stale_readback_is_resent_slowly_and_heals_when_it_moves():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.until(w.all_correct, timeout=10)
        await w.engine.wait_idle()
        before = writes_per_device(w)
        freeze_readback(w, {"sim-canopy-r2c1"}, 180)
        await w.engine.request_mode("holiday", source="ops")
        await w.run_for(60)
        stale = w.engine.tracker.readback_stale("sim-canopy-r2c1")
        reason = w.engine.tracker.snapshot("sim-canopy-r2c1")["reason"]
        await w.run_for(120)
        frozen = writes_per_device(w)
        healed = await w.until(w.all_correct, timeout=120)
        await w.close()
        return before, frozen, stale, reason, healed, w.engine.reconciler.stats

    before, frozen, stale, reason, healed, stats = run_virtual(main)
    assert stale and reason.startswith("Readback stale:")
    # 180 s frozen: the change, its early re-sends and re-asserts until it reads stale (≈ 4 writes), then about one a
    # minute — not one every few seconds (≈ 50 on Oct 21's code)
    assert frozen["sim-canopy-r2c1"] - before["sim-canopy-r2c1"] <= 10
    assert stats["stale_holds"] >= 1
    # once the readback moves (the freeze ends) the next slow re-send lands: within one interval and a true-up
    assert healed is not None and healed <= 60 + 10


def test_reconciler_resends_across_the_canopy_stay_under_the_cap():
    settings = settings_with(kiosk={"idle_return_s": 100_000}, reconciler={"reassert_max_per_min": 12})

    async def main():
        w = make_world(settings=settings)
        await w.engine.start(apply_initial=False)
        sends: list[float] = []
        w.executor.add_submit_listener(
            lambda c: sends.append(w.clock.monotonic()) if c.label.startswith("re-assert") else None
        )
        freeze_readback(w, {f"sim-{m}" for m in MODULES}, 120)
        await w.engine.request_mode("holiday", source="ops")
        await w.run_for(120)
        healed = await w.until(w.all_correct, timeout=120)
        await w.close()
        return sends, healed, w.engine.reconciler.stats

    sends, healed, stats = run_virtual(main)
    assert sends and stats["cap_holds"] >= 1
    assert max(sum(1 for t in sends if s <= t < s + 60) for s in sends) <= 12
    assert healed is not None
