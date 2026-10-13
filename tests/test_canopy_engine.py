"""CanopyEngine on the real SimDriver, executor and tracker (virtual time): requests, cooldown, pause, latest-wins,
locks, tiers, safe look, commissioning and the published state."""

from __future__ import annotations

from fake_compiler import MODULES, FakeCompiler, make_world, settings_with

from conductor import rows as R
from conductor.clock import run_virtual
from conductor.engine_api import CanopyPhase, Tier
from conductor.executor_api import CommandStatus


def cloud_slot_hash(w, device_id: str, slot: str) -> str | None:
    stored = {r.key: r.value for r in w.sim.cloud_view(w.address(device_id))}
    return R.slot_hash(stored.get((R.FRAME_CLASS, slot)))


def test_mode_change_bursts_one_command_per_module_and_all_nine_become_correct():
    async def main():
        w = make_world()
        await w.engine.start(apply_initial=False)
        state = await w.engine.request_mode("holiday", source="kiosk")
        assert not state.refused
        assert state.phase is CanopyPhase.UPDATING and state.mode == "holiday"
        assert state.feature == "Tunable white on every strand (fake)."
        settled = await w.until(w.all_correct, timeout=10)
        await w.engine.wait_idle()
        await w.run_for(1)
        final = w.engine.state()
        commands = [c for c in w.events("command") if c["label"] == "mode: holiday"]
        await w.close()
        return settled, final, commands, w.sim.stats()["writes"], w.hub

    settled, final, commands, writes, hub = run_virtual(main)
    assert settled is not None and settled <= 5.0
    assert writes == 9  # exactly one PUT per module (no faults in the dev simulator)
    assert sorted(c["device_id"] for c in commands) == sorted(f"sim-{m}" for m in MODULES)
    assert all(
        c["purpose"] == "engine" and c["source"] == "kiosk" and c["status"] == "applied" for c in commands
    )
    assert final.phase is CanopyPhase.READY
    assert all(m["status"] == "ready" and not m["locked"] and m["simulated"] for m in final.modules.values())
    assert final.to_json()["phase"] == "ready"
    assert "canopy" in hub.snapshots and hub.snapshots["canopy"]("canopy")["mode"] == "holiday"
    assert hub.messages and hub.messages[-1][0] == "canopy" and hub.messages[-1][1]["phase"] == "ready"


def test_kiosk_cooldown_and_paused_guest_input():
    async def main():
        w = make_world()
        await w.engine.start(apply_initial=False)
        out = {"first": await w.engine.request_mode("holiday", source="kiosk")}
        await w.run_for(2)
        out["second"] = await w.engine.request_mode("lowcountry-calm", source="kiosk")
        out["ops"] = await w.engine.request_mode("sunset", source="ops")  # ops bypass the cooldown
        await w.run_for(6.5)
        out["after"] = await w.engine.request_mode("lowcountry-calm", source="kiosk")
        await w.run_for(10)
        out["paused"] = w.engine.pause_guest_input(True, source="ops")
        out["kiosk_paused"] = await w.engine.request_mode("holiday", source="kiosk")
        out["ops_paused"] = await w.engine.request_mode("holiday", source="ops")
        await w.run_for(5)
        out["still_paused"] = w.engine.state()
        out["resumed"] = w.engine.pause_guest_input(False, source="ops")
        out["kiosk_resumed"] = await w.engine.request_mode("lowcountry-calm", source="kiosk")
        await w.close()
        return out

    out = run_virtual(main)
    assert not out["first"].refused and out["first"].cooldown_until is not None
    second = out["second"]
    assert second.refused and second.mode == "holiday"  # nothing changed
    assert "Try again in 6 s" in second.message and 5.9 <= second.retry_after_s <= 6.0
    assert second.to_json()["refused"] is True
    assert not out["ops"].refused and out["ops"].mode == "sunset"
    assert not out["after"].refused and out["after"].mode == "lowcountry-calm"
    assert out["paused"].phase is CanopyPhase.RESTING and out["paused"].guest_input is False
    assert out["kiosk_paused"].refused and "paused" in out["kiosk_paused"].message
    assert not out["ops_paused"].refused and out["ops_paused"].mode == "holiday"
    assert out["still_paused"].phase is CanopyPhase.RESTING  # lights keep going; the kiosk rests
    assert out["resumed"].guest_input is True
    assert not out["kiosk_resumed"].refused


def test_button_mashing_latest_wins_and_the_queue_never_grows():
    async def main():
        w = make_world()
        await w.engine.start(apply_initial=False)
        depths, refused = [], 0
        modes = ["holiday", "lowcountry-calm", "sunset", "golden"]
        for i in range(60):  # ops taps bypass the cooldown: the executor must coalesce
            await w.engine.request_mode(modes[i % 4], source="ops")
            depths.append(w.executor.queue_depth())
            await w.run_for(0.05)
        for i in range(20):  # kiosk taps during the cooldown are refused outright
            state = await w.engine.request_mode(modes[i % 4], source="kiosk")
            refused += state.refused
            depths.append(w.executor.queue_depth())
            await w.run_for(0.1)
        last = w.engine.current_plan()
        settled = await w.until(lambda: w.all_correct(last), timeout=15)
        await w.engine.wait_idle()
        tasks_left = len(w.engine._tasks)
        writes = w.sim.stats()["writes"]
        await w.close()
        return depths, refused, last.mode_id, settled, writes, tasks_left

    depths, refused, last, settled, writes, tasks_left = run_virtual(main)
    assert max(depths) <= 18  # ≤ 2 per device (one in flight, one pending), however fast the taps
    assert refused == 19  # the first kiosk tap goes through, the rest land in its cooldown
    assert last == "holiday" and settled is not None  # the one kiosk tap that got through is the newest
    assert writes < 60 * 9 / 3  # superseded commands were never sent
    assert tasks_left == 0


def test_unknown_mode_and_uncompilable_tiers():
    async def main():
        compiler = FakeCompiler(uncompilable={("sunset", Tier.T1)})
        w = make_world(compiler=compiler, tier_allowed=lambda m, t: (t is not Tier.T2, "not benched"))
        await w.engine.start(apply_initial=False)
        unknown = await w.engine.request_mode("disco", source="kiosk")
        sunset = await w.engine.request_mode("sunset", source="ops")
        compiler.uncompilable |= {("sunset", Tier.T3)}
        none = await w.engine.request_mode("sunset", source="ops")
        await w.close()
        return unknown, sunset, none

    unknown, sunset, none = run_virtual(main)
    assert unknown.refused and "no mode called 'disco'" in unknown.message
    # T1 can't compile, T2 isn't allowed → T3, and the kiosk copy follows the tier actually in use
    assert not sunset.refused and sunset.tier == "modules" and sunset.feature.startswith("Each strand")
    assert none.refused and "slot: not benched" in none.message and "canvas:" in none.message


def test_locked_module_is_skipped_shown_locked_and_restored_on_release():
    async def main():
        w = make_world()
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.until(w.all_correct, timeout=10)
        await w.engine.wait_idle()
        locked = "canopy-r1c1"
        lease = w.locks.acquire(w.address(locked), "module page · test")
        before = w.sim.device_view(w.address(locked))
        await w.engine.request_mode("holiday", source="ops")
        others = await w.until(lambda: all(w.correct(d) for d in MODULES if d != locked), timeout=10)
        await w.run_for(2)
        during = w.engine.state()
        untouched = not R.compare(before, w.sim.device_view(w.address(locked)))
        w.locks.release(w.address(locked), lease.token)
        healed = await w.until(w.all_correct, timeout=10)
        await w.run_for(2)
        after = w.engine.state()
        await w.close()
        return others, during, untouched, healed, after

    others, during, untouched, healed, after = run_virtual(main)
    assert others is not None and untouched
    module = during.modules["canopy-r1c1"]
    assert module["locked"] is True and module["status"] == "locked" and "module page" in module["reason"]
    assert during.phase is CanopyPhase.READY and "in use on a test page" in during.message
    assert healed is not None and healed <= 3.0
    assert after.phase is CanopyPhase.READY and not after.modules["canopy-r1c1"]["locked"]


def test_safe_look_bypasses_cooldown_and_pause():
    async def main():
        w = make_world()
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("holiday", source="kiosk")
        w.engine.pause_guest_input(True, source="ops")
        state = await w.engine.safe_look(source="ops")
        settled = await w.until(w.all_correct, timeout=10)
        await w.close()
        return state, settled

    state, settled = run_virtual(main)
    assert not state.refused and state.mode == "lowcountry-calm" and state.tier == "strand"
    assert settled is not None


def test_start_shows_the_idle_mode_and_stop_rests():
    async def main():
        w = make_world()
        before = w.engine.state()
        await w.engine.start()
        settled = await w.until(w.all_correct, timeout=10)
        started = w.engine.state()
        await w.run_for(2)
        ready = w.engine.state()
        await w.close()
        return before, started, settled, ready, w.engine.state()

    before, started, settled, ready, stopped = run_virtual(main)
    assert before.phase is CanopyPhase.RESTING and before.mode is None and before.simulated
    assert started.mode == "lowcountry-calm" and settled is not None
    assert ready.phase is CanopyPhase.READY
    assert stopped.phase is CanopyPhase.RESTING


def test_commission_writes_t1_slots_records_hashes_and_is_rate_limited():
    async def main():
        w = make_world()
        report = await w.engine.commission(source="ops")
        slots = {(d, slot): cloud_slot_hash(w, d, slot) for d in MODULES for slot in ("custom-1", "custom-3")}
        again = await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("sunset", source="ops")
        settled = await w.until(w.all_correct, timeout=10)
        await w.run_for(2)
        state = w.engine.state()
        await w.close()
        return report, slots, again, settled, state, w.recorded, w.compiler

    report, slots, again, settled, state, recorded, compiler = run_virtual(main)
    assert report["ok"] and report["simulated"] and set(report["modes"]) == {"sunset", "golden"}
    for mode_id, slot in (("sunset", "custom-1"), ("golden", "custom-3")):
        plan = compiler.compile(mode_id, Tier.T1)
        for d in MODULES:
            entry = report["modes"][mode_id]["modules"][d]
            assert entry["status"] == "applied" and entry["slot"] == slot
            # the recorded hash is the readback's, and it's what the frame asked for
            assert recorded[(d, slot)] == entry["hash"] == slots[(d, slot)] == plan.modules[d].slot_hash
    assert all(
        m["status"] == "skipped" and "at most once every 10 min" in m["reason"]
        for mode in again["modes"].values()
        for m in mode["modules"].values()
    )
    assert settled is not None and state.tier == "canvas" and state.phase is CanopyPhase.READY


def test_commission_retries_a_slot_write_the_cloud_dropped():
    async def main():
        w = make_world()
        original = w.sim.cloud.accept_wire
        dropped = []

        def accept(body, *, silent_drop=False):
            rows = body.get("values", [])
            if not dropped and any(r["functionClass"] == R.FRAME_CLASS for r in rows):
                dropped.append(body["metadeviceId"])
                silent_drop = True
            return original(body, silent_drop=silent_drop)

        w.sim.cloud.accept_wire = accept
        report = await w.engine.commission(source="ops")
        await w.close()
        return report, dropped

    report, dropped = run_virtual(main)
    device_id = dropped[0].removeprefix("sim-")
    assert report["ok"]
    assert report["modes"]["sunset"]["modules"][device_id]["status"] == "applied"


def test_commission_skips_locked_modules_and_modes_without_t1():
    async def main():
        w = make_world(tier_allowed=lambda m, t: (not (m == "golden" and t is Tier.T1), "Gate C not passed"))
        w.locks.acquire("sim-canopy-r0c0", "bench · gate_c")
        report = await w.engine.commission(source="ops")
        await w.close()
        return report

    report = run_virtual(main)
    assert set(report["modes"]) == {"sunset"}
    assert {"mode": "golden", "reason": "Gate C not passed"} in report["skipped"]
    assert report["modes"]["sunset"]["modules"]["canopy-r0c0"]["status"] == "locked"
    assert report["ok"]  # locked is not a failure


def test_every_engine_command_is_an_engine_purpose_verified_full_vector():
    async def main():
        w = make_world()
        await w.engine.commission(source="ops")
        seen = []
        w.executor.add_submit_listener(seen.append)
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("sunset", source="kiosk")
        await w.engine.wait_idle()
        await w.close()
        return seen, w.engine.current_plan()

    seen, plan = run_virtual(main)
    assert len(seen) == 9
    for cmd in seen:
        mt = plan.modules[cmd.device_id.removeprefix("sim-")]
        assert str(cmd.purpose) == "engine" and cmd.verify and cmd.rows == mt.rows and cmd.expect == mt.expect


def test_superseded_by_an_older_burst_is_resent():
    """If an older burst's command lands after a newer one (it queued late), the reconciler re-sends the look."""

    async def main():
        w = make_world()
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("holiday", source="ops")
        await w.until(w.all_correct, timeout=10)
        await w.engine.wait_idle()
        # an out-of-band older look for one module, as if a stale burst enqueued after the current one
        from conductor.executor_api import Command
        from conductor.gate import Purpose

        stale = R.full_state_vector(R.Look("white", cct=4000, brightness=20))
        await w.executor.submit(Command("sim-canopy-r0c0", stale, Purpose.ENGINE, "stale"))
        healed = await w.until(w.all_correct, timeout=70)
        await w.close()
        return healed

    healed = run_virtual(main)
    assert healed is not None and healed <= 65  # the next true-up at the latest


def test_last_status_tracks_the_current_look():
    async def main():
        w = make_world()
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("holiday", source="ops")
        await w.engine.wait_idle()
        await w.close()
        return dict(w.engine.last_status)

    status = run_virtual(main)
    assert set(status.values()) == {CommandStatus.APPLIED} and len(status) == 9


def test_engine_from_services_runs_on_the_app_and_saves_slot_hashes(app_client):
    """The lead's wiring helper on the real Services (sim, fast latency): a commission saves hashes to the overlay,
    and a kiosk request reaches all nine simulated modules."""
    from conductor.canopy import engine_from_services

    class SunsetOnly(FakeCompiler):
        def modes(self) -> list[str]:
            return ["sunset"]

    services = app_client.app.state.services
    compiler = SunsetOnly()

    async def go():
        engine = engine_from_services(
            services, compile=compiler.compile, ladder=compiler.ladder, modes=compiler.modes
        )
        report = await engine.commission(source="ops")
        await engine.start(apply_initial=False)
        state = await engine.request_mode("holiday", source="kiosk")
        await engine.wait_idle()
        final = engine.state()
        await engine.stop()
        return report, state, final

    report, state, final = app_client.portal.call(go)
    assert report["ok"], report
    for device_id in MODULES:
        saved = services.registry.get(device_id).slot_hashes
        assert saved["custom-1"] == report["modes"]["sunset"]["modules"][device_id]["hash"]
    assert not state.refused and state.simulated
    assert final.phase is CanopyPhase.READY and final.mode == "holiday"


# ---- a partial canopy: the look's own phase over the modules it drives (look_phase, D62) ---------------------------
DRIVEN = MODULES[:2]  # the positions with a device registered (the Mac show box's two test kits)
LEFT_OUT = MODULES[2:]
QUIET = settings_with(kiosk={"idle_return_s": 100_000})  # no idle hold


class PartialCompiler(FakeCompiler):
    """Compiles for DRIVEN only and leaves the other positions out, as compile_mode does for a position with no
    device registered on the engine's driver."""

    def compile(self, mode_id: str, tier: Tier):
        plan = super().compile(mode_id, tier)
        plan.skipped = {d: f"{d} has no Hubspace device registered on this runtime (fake)" for d in LEFT_OUT}
        return plan


def partial_world(**kw):
    w = make_world(settings=QUIET, compiler=PartialCompiler(module_ids=list(DRIVEN), **kw))
    real = w.engine.deps.target

    def target(device_id, purpose):
        if device_id in LEFT_OUT:  # services.target: no address on the engine's driver
            raise LookupError(f"{device_id} has no sim address yet (connect the account and register it)")
        return real(device_id, purpose)

    w.engine.deps.target = target
    return w


def test_a_partial_canopy_changes_and_settles_its_look_while_ops_needs_attention():
    async def main():
        w = partial_world()
        await w.engine.start(apply_initial=False)
        changing = await w.engine.request_mode("holiday", source="ops")
        settled = await w.until(w.all_correct, timeout=10)
        await w.engine.wait_idle()
        await w.run_for(1)
        ready = w.engine.state()
        await w.close()
        return changing, settled, ready

    changing, settled, ready = run_virtual(main)
    assert changing.phase is CanopyPhase.NEEDS_ATTENTION and changing.look_phase is CanopyPhase.UPDATING
    assert settled is not None
    assert ready.phase is CanopyPhase.NEEDS_ATTENTION and ready.look_phase is CanopyPhase.READY
    assert ready.to_json()["look_phase"] == "ready" and ready.to_json()["phase"] == "needs_attention"
    assert "M3" in ready.message and "M9" in ready.message  # /ops still names the positions left out
    assert all(ready.modules[d]["left_out"] and not ready.modules[d]["stuck"] for d in LEFT_OUT)
    assert all(not ready.modules[d]["left_out"] and not ready.modules[d]["stuck"] for d in DRIVEN)


def test_a_driven_module_stuck_past_the_window_is_the_looks_problem_too():
    async def main():
        w = partial_world(broken={("holiday", Tier.STRAND)}, broken_module=DRIVEN[1])
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("holiday", source="ops")
        await w.run_for(5)
        early = w.engine.state()
        await w.run_for(12)  # past reconciler.downgrade_after_wrong_s (10 s)
        late = w.engine.state()
        await w.close()
        return early, late

    early, late = run_virtual(main)
    assert early.look_phase is CanopyPhase.UPDATING  # still re-asserting inside the window
    assert late.phase is CanopyPhase.NEEDS_ATTENTION and late.look_phase is CanopyPhase.NEEDS_ATTENTION
    assert late.modules[DRIVEN[1]]["stuck"] and not late.modules[DRIVEN[1]]["left_out"]
    assert not late.modules[DRIVEN[0]]["stuck"]


def test_a_driven_module_with_no_address_is_the_looks_problem_too():
    # in the plan (not left out), but its address lookup fails: nothing reaches that strand, so the look isn't Ready
    async def main():
        w = partial_world()
        lookup = w.engine.deps.target

        def target(device_id, purpose):
            if device_id == DRIVEN[1]:
                raise LookupError(f"{device_id} lost its address (test)")
            return lookup(device_id, purpose)

        w.engine.deps.target = target
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("holiday", source="ops")
        await w.engine.wait_idle()
        await w.run_for(5)
        state = w.engine.state()
        plan = w.engine.current_plan()
        await w.close()
        return state, plan

    state, plan = run_virtual(main)
    assert DRIVEN[1] in plan.modules and DRIVEN[1] not in plan.skipped
    assert state.modules[DRIVEN[0]]["status"] == "ready"
    assert state.phase is CanopyPhase.NEEDS_ATTENTION and state.look_phase is CanopyPhase.NEEDS_ATTENTION
    assert state.modules[DRIVEN[1]]["left_out"] is False
    assert "lost its address" in state.modules[DRIVEN[1]]["reason"]


def test_on_the_full_canopy_the_look_phase_is_the_phase():
    async def main():
        w = make_world(settings=QUIET, compiler=FakeCompiler(broken={("holiday", Tier.STRAND)}))
        seen = [w.engine.state()]  # before the first look: resting
        await w.engine.start(apply_initial=False)
        seen.append(await w.engine.request_mode("lowcountry-calm", source="ops"))  # updating
        await w.until(w.all_correct, timeout=10)
        await w.engine.wait_idle()
        await w.run_for(1)
        seen.append(w.engine.state())  # ready
        lease = w.locks.acquire(w.address("canopy-r1c1"), "module page · test")
        seen.append(w.engine.state())  # ready, one module on a test page
        w.locks.release(w.address("canopy-r1c1"), lease.token)
        w.compiler.broken_module = "canopy-r2c2"
        await w.engine.request_mode("holiday", source="ops")
        await w.run_for(17)
        seen.append(w.engine.state())  # needs attention: r2c2 stuck
        seen.append(w.engine.pause_guest_input(True, source="ops"))  # resting
        await w.close()
        return seen

    seen = run_virtual(main)
    phases = [s.phase for s in seen]
    assert phases == [
        CanopyPhase.RESTING,
        CanopyPhase.UPDATING,
        CanopyPhase.READY,
        CanopyPhase.READY,
        CanopyPhase.NEEDS_ATTENTION,
        CanopyPhase.RESTING,
    ]
    assert [s.look_phase for s in seen] == phases
    assert seen[4].modules["canopy-r2c2"]["stuck"] and not any(
        m["left_out"] for m in seen[4].modules.values()
    )


def test_a_partial_canopys_commission_report_and_alerts_say_what_was_driven():
    async def main():
        w = partial_world()
        report = await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("holiday", source="ops")
        await w.engine.wait_idle()
        w.engine._commissioning = True  # the /ops line while slots are written
        writing = w.engine.state().message
        w.engine._commissioning = False
        w.engine.enter_outage("test")
        w.engine.exit_outage("test")
        await w.engine.wait_idle()
        alerts = [a["message"] for a in w.events("alert")]
        await w.close()
        return report, writing, alerts

    report, writing, alerts = run_virtual(main)
    assert report["ok"] and set(report["modes"]["sunset"]["modules"]) == set(DRIVEN)
    assert report["message"].endswith(
        "; M3, M4, M5, M6, M7, M8, M9 have no Hubspace device registered, so nothing was written there."
    ), report["message"]
    assert writing == "Writing the saved patterns to every registered strand…"
    assert any(a.endswith("re-applying Holiday to every driven module.") for a in alerts), alerts


def test_a_full_canopys_commission_report_names_nothing_left_out():
    async def main():
        w = make_world()
        report = await w.engine.commission(source="ops")
        await w.close()
        return report

    report = run_virtual(main)
    assert report["message"] == "Wrote 18 slot(s)."
