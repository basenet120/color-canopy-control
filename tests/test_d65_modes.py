"""D65 in the shipped config: Coastal Tides and Golden Hour step through the cloud (sim-first, live off).

Coastal Tides starts stepped ([stepped, modules, strand]); Golden Hour steps where its per-head sun isn't available
([canvas, slot, stepped, modules, strand]). Each stepped rung moves the scene's own design (the hold is its modules
rung), says so only while it moves, and stays true on a partial canopy. Lowcountry Calm (idle), The Goodnight Glow,
Rainbow Row Sunset and Garden Gathering stay still. On live hardware the show's own tier_allowed refuses the stepped
rung until Gate R (motion.stepped.live_enabled), so the ladder lands on the next rung and nothing steps."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_compiler import make_world, settings_with

from conductor import soak
from conductor.capabilities import detect
from conductor.clock import run_virtual
from conductor.config import CONFIG_DIR, load_recipes, load_registry
from conductor.engine_api import Tier
from conductor.geometry import load_canopy_config
from conductor.looks import CompileContext, compile_mode, load_modes
from conductor.looks import field as F
from conductor.looks import scene_rules as R
from conductor.looks.compile import EngineCompiler, module_lags, program_cycle
from conductor.looks.scene_rules import scene_mode_problems
from conductor.looks.schema import MOTION_WORDS, STEPPED_PACE, breathe_words
from conductor.routes import kiosk
from conductor.scenes import load_scenes
from conductor.show import STEPPED_LIVE_OFF, ShowWiring
from conductor.sim.profiles import profile_parts

MODES = load_modes()
REGISTRY = load_registry()
MODULES = REGISTRY.canopy_modules()
CAPS = detect(*profile_parts("sqhdk03-sim"))
GRID = [f"r{r}c{c}" for r in range(3) for c in range(3)]
# The approved values live in the pinned fixture, which the Design Lab's export and `make scene-pins` regenerate
# (Design Lab PLAN §3.7). D65's: Coastal Tides flows "diagonally", Golden Hour "from one edge"; forward, 6 steps of
# 6 s a cycle, 90 s runs.
PINS = json.loads((Path(__file__).parent / "fixtures_scene_frames.json").read_text())["_pins"]
PIN_CHANGED = "approved value changed: run make scene-pins and review the fixture diff"
STEPPING = {m: p["flow_words"] for m, p in PINS.items() if "stepped" in p}  # mode → the flow its copy names
D63_SUN = F.RadialField.model_validate({"kind": "radial", "centre_in": (0, 60), "metric": "canopy"})


def _ctx(**kw) -> CompileContext:
    return CompileContext(
        registry=REGISTRY,
        capabilities={m.id: CAPS for m in MODULES},
        modes=MODES,
        recipes=load_recipes(),
        canopy=load_canopy_config(),
        **kw,
    )


# ---- what moves ----------------------------------------------------------------------------------------------------
def test_coastal_tides_and_golden_hour_step_their_own_designs_and_the_rest_stay_still():
    assert MODES.mode("coastal-tides").ladder == ["stepped", "modules", "strand"]
    assert MODES.mode("golden-hour").ladder == ["canvas", "slot", "stepped", "modules", "strand"]
    for mode_id, mode in MODES.modes.items():
        stepped = mode.techniques.stepped
        if mode_id not in STEPPING:  # idle, Goodnight Glow, Rainbow Row Sunset, Garden Gathering
            assert stepped is None and "stepped" not in mode.ladder, mode_id
            continue
        sample = mode.techniques.modules.sample
        assert stepped.canvas == sample.canvas and stepped.brightness == sample.brightness  # the hold is T3
        if mode.techniques.canvas is not None:  # Golden Hour: the same sun as its per-head canvas
            assert stepped.canvas == mode.techniques.canvas.canvas
        # the lead's D65 defaults: 6 s design steps, 6 a cycle, bounded 90 s runs, flowing the way the copy reads
        pinned = PINS[mode_id]["stepped"]
        assert (stepped.direction, stepped.steps_per_cycle, stepped.step_s, stepped.run_s) == (
            pinned["direction"],
            pinned["steps_per_cycle"],
            pinned["step_s"],
            pinned["run_s"],
        ), PIN_CHANGED


@pytest.mark.parametrize("mode_id", list(STEPPING))
def test_a_stepped_rung_moves_the_design_with_copy_true_to_it(mode_id):
    plan = compile_mode(mode_id, Tier.STEPPED, _ctx())
    hold = compile_mode(mode_id, Tier.T3, _ctx())
    steps = plan.program.steps
    tech = MODES.mode(mode_id).techniques.stepped
    per_cycle = program_cycle(tech, module_lags(tech, GRID, load_canopy_config()))  # D65's 6 for travel (D75)
    assert plan.tier is Tier.STEPPED and plan.motion is False and len(steps) == per_cycle
    # the hold is the still module-level look, exactly; every step is a different picture of the design
    assert (
        steps[0].rows
        == {d: t.rows for d, t in hold.modules.items()}
        == {d: t.rows for d, t in plan.modules.items()}
    )
    frames = [tuple(s.colours[p] for p in GRID) for s in steps]
    assert all(len(set(f)) > 2 for f in frames)
    if tech.mode == "travel":
        assert len(set(frames)) == per_cycle
    else:  # D75: a breath may come back to a picture, but every step changes something
        pictures = R.step_pictures(MODES.mode(mode_id), load_recipes(), load_canopy_config())
        assert all(pictures[n] != pictures[(n + 1) % len(pictures)] for n in range(len(pictures)))
    # still copy while it holds; the moving copy only while a run moves: the mode's own, naming its flow and pace
    assert plan.feature == hold.feature and not MOTION_WORDS.search(plan.feature)
    moving = plan.program.moving_feature
    assert moving == tech.moving_feature
    assert STEPPED_PACE in moving and "through the Hubspace cloud" in moving
    if tech.mode == "travel":
        assert STEPPING[mode_id] in moving
    else:
        assert breathe_words(tech) in moving.lower()
    assert not any(
        w in moving.lower() for w in ("light by light", "each light", "270", "smooth", "animation")
    )
    rules = {"stepped"}
    assert scene_mode_problems(load_scenes(), MODES, load_recipes(), load_canopy_config(), rules=rules) == []


def test_golden_hours_rays_move_out_from_the_middle_of_the_left_edge():
    if PINS["golden-hour"]["layout"] != D63_SUN.model_dump(mode="json"):  # any other kind or place
        pytest.skip("Golden Hour's sun was redesigned in the Design Lab; see DECISIONS")
    steps = compile_mode("golden-hour", Tier.STEPPED, _ctx()).program.steps
    for (
        step
    ) in steps:  # rings around the sun at (0, 60): the top and bottom rows mirror each other every step
        assert [step.colours[f"r0c{c}"] for c in range(3)] == [step.colours[f"r2c{c}"] for c in range(3)]
    # the strand at the sun shows a new colour every step (which way the rings flow: tests/test_compile_stepped.py)
    assert (
        len({s.colours["r1c0"] for s in steps})
        == MODES.mode("golden-hour").techniques.stepped.steps_per_cycle
    )


def test_on_a_partial_canopy_the_moving_copy_counts_its_strands_and_claims_no_layout():
    ctx = _ctx(addresses={m.id: (f"md-{m.id}" if m.module in ("r0c0", "r0c1") else None) for m in MODULES})
    for mode_id, words in STEPPING.items():
        plan = compile_mode(mode_id, Tier.STEPPED, ctx)
        moving = plan.program.moving_feature
        assert sorted(plan.modules) == ["canopy-r0c0", "canopy-r0c1"]
        assert moving.startswith("Colour and dimming through the Hubspace cloud: both Hubspace strands")
        assert words not in moving and "canopy" not in moving and "nine" not in moving


# ---- live safety ---------------------------------------------------------------------------------------------------
class _Addressed:
    """The registry as the show box sees it: every canopy position has a device on the driver (an Afero metadevice id
    live, the sim address in sim), no bench flag proven, no slot commissioned."""

    def __init__(self, registry):
        self._registry = registry

    def canopy_modules(self):
        return self._registry.canopy_modules()

    def address(self, device_id: str, driver_name: str) -> str | None:
        return f"md-{device_id}" if driver_name == "afero" else self._registry.address(device_id, driver_name)


def _show_wiring(registry, *, simulated: bool, live_enabled: bool = False) -> ShowWiring:
    """The show's own wiring (the shipped modes, its tier rules) on a driver named as live or the simulator."""
    services = SimpleNamespace(
        registry=_Addressed(registry),
        engine_driver=SimpleNamespace(name="sim" if simulated else "afero", simulated=simulated),
        settings=settings_with(motion={"stepped": {"live_enabled": live_enabled}}),
        config_dir=CONFIG_DIR,
    )
    wiring = ShowWiring(
        services
    )  # loads config/modes.yaml and checks the stepped runs against the kiosk settings
    wiring.caps = {e.id: CAPS for e in registry.canopy_modules()}
    return wiring


def _shown(*, simulated: bool, live_enabled: bool = False, source: str = "ops") -> dict[str, tuple]:
    """Each D65 mode chosen through the real engine (compile, ladder and tier_allowed from the show's wiring), by
    ops (ladder order) or a guest (the stepped rung first, D75)."""

    async def main():
        w = make_world(settings=settings_with(motion={"stepped": {"live_enabled": live_enabled}}))
        wiring = _show_wiring(w.registry, simulated=simulated, live_enabled=live_enabled)
        w.engine.deps.compile = wiring.compile
        w.engine.deps.ladder = wiring.ladder
        w.engine.deps.modes = wiring.mode_ids
        w.engine.deps.tier_allowed = wiring.tier_allowed
        out = {}
        for mode_id in STEPPING:
            if source == "kiosk":  # past the guest gap the last choice bought
                await w.run_for(w.engine.guest_gap_s(w.engine.current_plan()) + 0.1)
            state = await w.engine.request_mode(mode_id, source=source)
            assert not state.refused, state.message
            plan, runner = w.engine.current_plan(), w.engine.runner
            out[mode_id] = (plan.tier, plan.program is not None, runner is not None and runner.running)
            out[mode_id] += (wiring.tier_allowed(mode_id, Tier.STEPPED),)
        await w.close()
        return out

    return run_virtual(main)


@pytest.mark.parametrize("source", ["ops", "kiosk"])
def test_live_hardware_refuses_the_stepped_rung_whichever_path_asks(source):
    shown = _shown(simulated=False, source=source)
    # Coastal Tides: [stepped, modules, strand] → modules. Golden Hour: canvas (no commissioned slot, no Gate C),
    # slot (nothing hand-painted, no Gate B), stepped (Gate R) → modules. Nothing steps, nothing is sent a program.
    for mode_id in STEPPING:
        assert shown[mode_id] == (Tier.T3, False, False, (False, STEPPED_LIVE_OFF)), mode_id


@pytest.mark.parametrize("source", ["ops", "kiosk"])
def test_after_gate_r_live_enabled_opens_the_stepped_rung_on_live_hardware(source):
    shown = _shown(simulated=False, live_enabled=True, source=source)  # Alon flips it after the rehearsal
    for mode_id in STEPPING:
        assert shown[mode_id] == (Tier.STEPPED, True, True, (True, "")), mode_id


@pytest.mark.parametrize("source", ["ops", "kiosk"])
def test_the_simulator_steps_both_where_their_better_rungs_arent_available(source):
    shown = _shown(simulated=True, source=source)  # uncommissioned: Golden Hour's canvas isn't available
    for mode_id in STEPPING:
        assert shown[mode_id] == (Tier.STEPPED, True, True, (True, "")), mode_id


def test_an_idle_return_with_motion_idle_on_never_breathes_on_live_hardware():
    """motion.idle on (an idle look with a rung: Coastal Tides here), on live hardware before Gate R: the idle return
    is still (the show's tier rule refuses the rung), and no program command goes out."""

    async def main():
        sections = {
            "kiosk": {"idle_mode": "coastal-tides"},
            "motion": {"idle": {"breathe_run_s": 60, "every_s": 300}},
        }
        w = make_world(settings=settings_with(**sections))
        wiring = _show_wiring(w.registry, simulated=False)
        wiring.services.settings = w.engine.deps.settings  # its load checks see motion.idle too
        w.engine.deps.compile = wiring.compile
        w.engine.deps.ladder = wiring.ladder
        w.engine.deps.modes = wiring.mode_ids
        w.engine.deps.tier_allowed = wiring.tier_allowed
        await w.engine.start()
        await w.run_for(5)
        await w.engine.request_mode("golden-hour", source="kiosk")
        await w.run_for(w.engine.deps.settings.kiosk.idle_return_s + 400)
        plan = w.engine.current_plan()
        out = (plan.mode_id, plan.tier, plan.program, w.engine.runner)
        out += ([c for c in w.events("command") if c["source"] == "program"], w.events("program"))
        await w.close()
        return out

    mode, tier, program, runner, commands, events = run_virtual(main)
    assert mode == "coastal-tides" and tier is Tier.T3 and program is None and runner is None
    assert commands == [] and events == []


def test_a_throttle_downgrade_from_golden_hours_sun_eases_off_to_a_still_rung_never_the_stepped_one():
    """Golden Hour's ladder puts the stepped rung below its per-head sun, but a downgrade (throttling, a module that
    stays wrong) must ease off: it passes over the stepped rung, which writes most, to the still modules rung, and
    the floor lifts back past it to the sun without a stepped run in between."""

    async def main():
        w = make_world()
        looks = EngineCompiler(w.registry)  # the shipped modes at the simulator's profile: the sun compiles
        w.engine.deps.compile = looks.compile
        w.engine.deps.ladder = looks.ladder
        w.engine.deps.modes = looks.modes
        await w.engine.request_mode("golden-hour", source="ops")
        first = w.engine.current_plan().tier
        down = w.engine.downgrade("3 commands ended throttled on account canopy")
        plan, runner = w.engine.current_plan(), w.engine.runner
        after_down = (plan.tier, plan.program is not None, runner is not None and runner.running)
        floors_down = dict(w.engine.floors)
        await w.engine.request_mode("golden-hour", source="ops")  # chosen again while the floor holds: still
        again = w.engine.current_plan().tier
        lifted = w.engine.upgrade("clean interval")
        plan, runner = w.engine.current_plan(), w.engine.runner
        after_up = (plan.tier, runner is not None and runner.running, dict(w.engine.floors))
        await w.close()
        return first, down, after_down, floors_down, again, lifted, after_up

    first, down, after_down, floors_down, again, lifted, after_up = run_virtual(main)
    assert first is Tier.T1 and down
    assert after_down == (Tier.T3, False, False)  # the still module-level look: no program, no runner
    assert floors_down == {"golden-hour": 3}  # the modules rung, not the stepped one (2)
    assert again is Tier.T3
    # one clean interval lifts past the stepped rung (and the uncommissioned slot) straight back to the sun
    assert lifted == ["golden-hour"] and after_up == (Tier.T1, False, {})


# ---- D75: a guest's choice runs the rung, then the run holds on the best still tier ----------------------------------
async def _golden_world(**sections):
    """The shipped modes through the real engine at the simulator's profile (Golden Hour's sun compiles), started and
    commissioned (its slots recorded), with the sim's writes logged as (monotonic time, device id, rows)."""
    w = make_world(settings=settings_with(**sections) if sections else None)
    looks = EngineCompiler(w.registry)
    w.engine.deps.compile, w.engine.deps.ladder, w.engine.deps.modes = (
        looks.compile,
        looks.ladder,
        looks.modes,
    )
    writes: list[tuple[float, str, list]] = []
    original = w.sim.write

    async def logged(device_id, rows, *, purpose):
        writes.append((w.clock.monotonic(), device_id.removeprefix("sim-"), list(rows)))
        return await original(device_id, rows, purpose=purpose)

    w.sim.write = logged
    w.writes = writes
    await w.engine.start()
    await w.engine.commission(source="ops")
    await w.run_for(70)  # start-up's burst and the slot writes are out of the 60 s rate window
    return w


def _run_end(w) -> float:
    runner = w.engine.runner
    return runner.t0 + runner.count * runner.step_s


def _queued_since(w, since: float, source: str) -> list[dict]:
    return [
        c
        for c in w.events("command")
        if c["source"] == source and c["timings"]["queued_at"] >= w.clock.epoch + since
    ]


def test_a_guest_choice_of_golden_hour_runs_its_steps_then_holds_on_its_per_head_sun():
    async def main():
        w = await _golden_world()
        await w.engine.request_mode("golden-hour", source="kiosk")
        first = (w.engine.current_plan().tier, w.engine.runner.running)
        end = _run_end(w)
        await w.run_for(end - w.clock.monotonic() + 10)
        plan, runner, state = w.engine.current_plan(), w.engine.runner, w.engine.state()
        after = [d for t, d, _ in w.writes if t >= end - 1e-6]
        settles = _queued_since(w, end - 1e-6, "settle")
        correct = w.all_correct()
        n = len(w.writes)
        await w.run_for(10)
        again = w.writes[n:]
        t1 = w.engine.deps.compile("golden-hour", Tier.T1)
        await w.close()
        return first, plan, runner, state, after, settles, correct, again, t1

    first, plan, runner, state, after, settles, correct, again, t1 = run_virtual(main)
    assert first == (Tier.STEPPED, True)  # a guest's choice runs the rung first (D75)
    assert plan.tier is Tier.T1 and runner is None and state.feature == t1.feature and state.program is None
    assert sorted(after) == sorted(m.id for m in MODULES)  # exactly one write per module after the run
    assert {c["label"] for c in settles} == {"after the run: golden-hour"} and len(settles) == 9
    assert correct and again == []  # the per-head sun holds, nothing re-sent


def test_a_run_ending_under_the_fast_back_off_still_settles_at_once():
    """D72's fast back-off puts guest pacing back on the budget rule, the pace the run already kept; it doesn't hold
    the settle (its 120 s after a 90 s run would land past the 180 s idle return, so the per-head sun would never
    show). 5% throttling on nine strands (the soak's pinned conditions) keeps the back-off on through most runs."""

    async def main():
        w = await _golden_world()
        await w.engine.request_mode("golden-hour", source="kiosk")
        end = _run_end(w)
        await w.run_for(end - w.clock.monotonic() - 5)
        account = w.engine._account_of(w.engine.target_of(MODULES[0].id).address)
        w.engine._fast_backoff_until[account] = w.clock.monotonic() + 120.0  # as _note_throttle sets it
        await w.run_for(10)
        out = (
            w.engine.fast_backoff(),
            w.engine.current_plan().tier,
            w.engine.runner,
            _queued_since(w, end - 1e-6, "settle"),
        )
        await w.close()
        return out, account

    (backoff, tier, runner, settles), account = run_virtual(main)
    assert backoff == [account]  # still backed off when the run ended
    assert tier is Tier.T1 and runner is None and len(settles) == 9


def test_an_ops_choice_keeps_the_ladder_order():
    async def main():
        w = await _golden_world()
        await w.engine.request_mode("golden-hour", source="ops")
        golden = (w.engine.current_plan().tier, w.engine.runner)
        await w.engine.request_mode("coastal-tides", source="ops")
        tides = (w.engine.current_plan().tier, w.engine.runner.running)
        await w.close()
        return golden, tides

    golden, tides = run_virtual(main)
    assert golden == (Tier.T1, None)  # its canvas first, at once (D65's ladder order)
    assert tides == (Tier.STEPPED, True)  # stepped is Coastal Tides' first rung


def test_a_kiosk_choice_under_a_floor_never_steps():
    async def main():
        w = await _golden_world()
        await w.engine.request_mode("golden-hour", source="ops")
        assert w.engine.downgrade("3 commands ended throttled on account canopy")
        floors = dict(w.engine.floors)
        since = w.clock.monotonic()
        await w.engine.request_mode("golden-hour", source="kiosk")
        out = (floors, w.engine.current_plan().tier, w.engine.runner)
        await w.run_for(20)
        out += (_queued_since(w, since, "program"),)
        await w.close()
        return out

    floors, tier, runner, program = run_virtual(main)
    assert floors == {"golden-hour": 3} and tier is Tier.T3 and runner is None and program == []


def test_a_re_pick_mid_run_keeps_stepping_and_after_it_lifts_to_the_per_head_sun():
    from conductor.canopy import LOOKS_SOURCE

    async def main():
        w = await _golden_world()
        await w.engine.request_mode("golden-hour", source="kiosk")
        t0 = w.engine.runner.t0
        await w.run_for(20)
        seen = []
        for source in ("reconciler", LOOKS_SOURCE):
            await w.engine.request_mode("golden-hour", source=source)
            seen.append((w.engine.current_plan().tier, w.engine.runner.running, w.engine.runner.t0))
        w.engine._replan_soon()
        await w.run_for(2)
        seen.append((w.engine.current_plan().tier, w.engine.runner.running, w.engine.runner.t0))
        await w.run_for(_run_end(w) - w.clock.monotonic() + 5)
        settled = (w.engine.current_plan().tier, w.engine.runner)
        await w.engine.request_mode("golden-hour", source="reconciler")
        repick = (w.engine.current_plan().tier, w.engine.runner)
        await w.close()
        return t0, seen, settled, repick

    t0, seen, settled, repick = run_virtual(main)
    assert seen == [(Tier.STEPPED, True, t0)] * 3  # the run keeps its phase; nothing cuts it short
    assert settled == (Tier.T1, None) and repick == (Tier.T1, None)


def test_a_choice_that_couldnt_run_when_it_was_installed_never_starts_on_a_re_pick():
    """D75 (review B-R2-1): a re-pick never starts a run. A guest's Coastal Tides (no canvas or slot, so the plan
    stays stepped and only holds) chosen while the account cools off counts as run: once the cool-off lifts, still
    inside the 90 s its run would have taken, no re-pick of it (the reconciler's, recovery's, start-up's, a publish's)
    starts its steps."""
    from conductor.canopy import LOOKS_SOURCE

    async def main():
        w = await _golden_world()
        account = w.engine._account_of(w.engine.target_of(MODULES[0].id).address)
        w.engine.stepped_cooloff[account] = w.clock.monotonic() + 10.0  # its cool-off: 10 s left
        await w.engine.request_mode("coastal-tides", source="kiosk")
        runner = w.engine.runner
        first = (w.engine.current_plan().tier, runner.running, runner.ended)
        await w.run_for(20)  # the cool-off is over
        since = w.clock.monotonic()
        seen = []
        for source in ("reconciler", "recovery", "startup", LOOKS_SOURCE):
            await w.engine.request_mode("coastal-tides", source=source)
            seen.append((source, w.engine.current_plan().tier, w.engine.runner.running))
        await w.run_for(30)
        out = (first, seen, _queued_since(w, since, "program"), w.engine.runner.running)
        await w.close()
        return out

    first, seen, program, running = run_virtual(main)
    tier, ran, ended = first
    assert tier is Tier.STEPPED and not ran and "cool-off" in ended  # it held still, as it should
    assert seen == [(s, Tier.STEPPED, False) for s in ("reconciler", "recovery", "startup", "looks")]
    assert program == [] and not running  # nothing stepped after the cool-off


def test_a_clean_interval_mid_run_never_restarts_the_steps_a_downgrade_stopped():
    """Review B-R3-TS-1 (D65, D75): throttling eases a guest's Coastal Tides run (its first rung is stepped) down to
    the modules rung mid-run, and a clean interval lifts the floor while the run's 90 s would still be going. The lift
    is a re-pick: it never starts a run, so the look stays on the still modules rung and nothing steps on the account
    that was just throttled. (Re-compiling the best rung instead put the plan back on the stepped rung and its steps
    picked up mid-schedule: 36 program commands in the next 30 s.)"""

    async def main():
        w = await _golden_world()
        await w.engine.request_mode("coastal-tides", source="kiosk")
        runner = w.engine.runner
        first = (w.engine.current_plan().tier, runner.running)
        end = _run_end(w)
        await w.run_for(15)
        down = w.engine.downgrade("3 commands ended throttled on account canopy")
        after_down = (w.engine.current_plan().tier, dict(w.engine.floors), runner.running)
        await w.run_for(10)
        since = w.clock.monotonic()
        lifted = w.engine.upgrade("clean")
        after_up = (w.engine.current_plan().tier, dict(w.engine.floors), w.engine.runner)
        await w.run_for(30)
        out = (first, end, since, down, after_down, lifted, after_up, w.engine.current_plan().tier)
        out += (_queued_since(w, since, "program"), w.engine.runner)
        await w.close()
        return out

    first, end, since, down, after_down, lifted, after_up, tier, program, runner = run_virtual(main)
    assert first == (Tier.STEPPED, True) and since < end  # the run would still be going when the floor lifts
    assert down and after_down == (Tier.T3, {"coastal-tides": 1}, False)
    tier_up, floors_up, runner_up = after_up
    assert lifted == ["coastal-tides"] and tier_up is Tier.T3 and floors_up == {}
    assert runner_up is None or not runner_up.running
    assert tier is Tier.T3 and (runner is None or not runner.running)
    assert program == []  # nothing stepped after the lift


def test_a_governor_trip_holds_module_level_and_lifts_after_the_cool_off():
    async def main():
        w = await _golden_world(motion={"stepped": {"governor_cooloff_s": 120}})
        await w.engine.request_mode("golden-hour", source="kiosk")
        hold = w.engine.current_plan()
        await w.run_for(10)
        for _ in range(12):  # throttled attempts on the run's modules (the governor counts every attempt)
            w.telemetry.record("attempt", device_id="sim-canopy-r0c0", outcome="throttled", http_status=429)
        await w.run_for(0.5)
        tripped = (w.engine.runner.running, w.engine.runner.ended, w.engine.current_plan().tier)
        trip = w.clock.monotonic()
        await w.run_for(110)
        hold_sent = _queued_since(w, trip - 1, "program")
        before = (w.engine.current_plan().tier, _queued_since(w, trip, "settle"))
        await w.run_for(15)
        settles = _queued_since(w, trip, "settle")
        out = (hold, tripped, hold_sent, before, settles, w.engine.current_plan().tier, w.engine.state())
        await w.close()
        return out

    hold, tripped, hold_sent, before, settles, tier, state = run_virtual(main)
    running, ended, at = tripped
    assert not running and "governor" in ended and at is Tier.STEPPED  # the T3 hold, not the sun yet
    assert hold_sent and {c["label"].split(":")[0] for c in hold_sent} == {"hold"}
    assert before == (Tier.STEPPED, [])  # nothing selects a slot before the cool-off
    assert tier is Tier.T1 and len(settles) == 9
    assert {c["label"] for c in settles} == {"after the run: golden-hour"}
    assert state.program is None and hold.modules  # (the hold was the module-level design)


def test_a_run_ended_by_held_steps_holds_module_level_and_settles_one_cap_window_later():
    """D75 (review B-R2-TS-1): a run the account's write budget ended (HELD_STEPS_END holds in a row) sends its hold
    at once and waits one cap window (60 s) before the settle's nine slot selects, so that burst never lands on an
    account that has just twice failed to find room for a step."""

    async def main():
        w = await _golden_world()
        await w.engine.request_mode("golden-hour", source="kiosk")
        runner = w.engine.runner
        await w.run_for(20)  # steps 1 and 2 are out
        sent = runner.steps_sent
        real = w.executor.account_writes
        w.executor.account_writes = lambda address, **kw: real(address, **kw) + 90  # the 60 s window is full
        while runner.running:
            await w.run_for(0.5)
        end = w.clock.monotonic()
        w.executor.account_writes = real
        ended = (runner.ended, runner.budget_holds, w.engine.current_plan().tier)
        await w.run_for(2)
        hold_sent = _queued_since(w, end - 1, "program")
        await w.run_for(end + 59 - w.clock.monotonic())
        before = (w.engine.current_plan().tier, _queued_since(w, end - 1, "settle"))
        await w.run_for(11)
        settles = _queued_since(w, end - 1, "settle")
        at = sorted(c["timings"]["queued_at"] - w.clock.epoch - end for c in settles)  # seconds after the end
        out = (sent, ended, hold_sent, before, settles, at, w.engine.current_plan().tier, w.engine.runner)
        await w.close()
        return out

    sent, ended, hold_sent, before, settles, at, tier, runner = run_virtual(main)
    assert sent >= 2
    assert ended == ("the account's write budget held back 2 steps in a row", 2, Tier.STEPPED)
    assert hold_sent and {c["label"].split(":")[0] for c in hold_sent} == {"hold"}  # the hold, at once
    assert before == (Tier.STEPPED, [])  # nothing selects a slot inside the cap window
    assert len(settles) == 9 and {c["label"] for c in settles} == {"after the run: golden-hour"}
    assert 60.0 - 1e-3 <= at[0] <= at[-1] <= 61.0  # one cap window after the run's end
    assert tier is Tier.T1 and runner is None


def test_a_run_that_ends_inside_an_outage_settles_at_recovery():
    async def main():
        w = await _golden_world()
        await w.engine.request_mode("golden-hour", source="kiosk")
        end = _run_end(w)
        await w.run_for(end - w.clock.monotonic() - 10)
        w.sim.faults.set_outage(True)
        w.engine.enter_outage("test outage")
        await w.run_for(20)  # the run ends while resting: nothing goes out
        ended = (w.engine.runner.running, w.engine.current_plan().tier)
        n = len(w.writes)
        w.sim.faults.set_outage(False)
        await w.until(lambda: w.engine.outage is None, timeout=30, step=0.05)
        await w.run_for(3)
        burst = w.writes[n:]
        plan = w.engine.current_plan()
        out = (ended, plan, burst, {d: list(mt.rows) for d, mt in plan.modules.items()})
        await w.close()
        return out

    ended, plan, burst, rows = run_virtual(main)
    assert ended == (False, Tier.STEPPED)
    assert plan.tier is Tier.T1
    assert sorted(d for _, d, _ in burst) == sorted(m.id for m in MODULES)  # one write per module: the burst
    assert all(sent == rows[d] for _, d, sent in burst)  # carrying the sun's rows


# ---- surfaces ------------------------------------------------------------------------------------------------------
def test_the_kiosk_pictures_a_stepped_mode_at_module_level():
    specs = kiosk._read_yaml(kiosk.MODES_FILE)["modes"]
    assert (
        kiosk._pattern(specs["coastal-tides"]) == "modules"
    )  # stepped first: module level, not a per-head design
    assert kiosk._pattern(specs["golden-hour"]) == "design"  # its per-head canvas first
    for mode_id in STEPPING:
        assert kiosk._pattern(specs[mode_id], str(Tier.STEPPED)) == "modules"
    services = SimpleNamespace(registry=REGISTRY)
    # before any plan, a stepped-first mode's tile is its design at each module: the still look a run holds
    listed = {m["id"]: m for m in kiosk.load_kiosk_modes()}
    first = compile_mode("coastal-tides", Tier.STEPPED, _ctx()).program.steps[0]  # full-strength colours
    assert listed["coastal-tides"]["module_colours"] == [first.colours[p] for p in GRID]
    assert len(set(listed["coastal-tides"]["module_colours"])) > 2
    # a stepped plan's flat colours are its still design (the hold it ends on); the panel's motion carries the steps
    plan = compile_mode("golden-hour", Tier.STEPPED, _ctx())
    assert kiosk.module_colours(services, plan) == kiosk.module_colours(
        services, compile_mode("golden-hour", Tier.T3, _ctx())
    )
    assert len(kiosk.module_colours(services, plan)) == 9 and None not in kiosk.module_colours(services, plan)


def test_the_builtin_soak_compiler_leaves_the_stepped_rung_out():
    builtin = soak.BuiltinCompiler(REGISTRY)  # it has no program to step: the still rungs only
    assert builtin.ladder("coastal-tides") == [Tier.T3, Tier.STRAND]
    assert builtin.ladder("golden-hour") == [Tier.T1, Tier.T3, Tier.STRAND]


def test_the_kiosk_tile_of_a_stepped_mode_falls_back_to_its_still_design(app_client, monkeypatch):
    """/api/kiosk/modes for a mode the engine would show stepped: drawn at module level, and when the plan's own
    colours can't be read the tile keeps the mode's design at each module (the still look a run holds)."""
    plan = compile_mode("coastal-tides", Tier.STEPPED, _ctx())

    class Engine:  # only what the modes route asks of the engine
        def state(self):
            return None

        def current_plan(self):
            return None

        def compile_best(self, mode_id):
            return (plan, "") if mode_id == "coastal-tides" else (None, "not in this test")

    app_client.app.state.services.extras["canopy"] = Engine()
    monkeypatch.setattr(kiosk, "module_colours", lambda services, plan: None)  # e.g. no registry to read
    tides = {m["id"]: m for m in app_client.get("/api/kiosk/modes").json()}["coastal-tides"]
    assert tides["tier"] == "stepped" and tides["pattern"] == "modules"
    assert tides["module_colours"] == [plan.program.steps[0].colours[p] for p in GRID]
