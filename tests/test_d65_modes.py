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
from conductor.looks.compile import EngineCompiler
from conductor.looks.scene_rules import scene_mode_problems
from conductor.looks.schema import MOTION_WORDS, STEPPED_PACE
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
    per_cycle = MODES.mode(mode_id).techniques.stepped.steps_per_cycle  # D65's 6
    assert plan.tier is Tier.STEPPED and plan.motion is False and len(steps) == per_cycle
    # the hold is the still module-level look, exactly; every step is a different picture of the design
    assert (
        steps[0].rows
        == {d: t.rows for d, t in hold.modules.items()}
        == {d: t.rows for d, t in plan.modules.items()}
    )
    frames = [tuple(s.colours[p] for p in GRID) for s in steps]
    assert len(set(frames)) == per_cycle and all(len(set(f)) > 2 for f in frames)
    # still copy while it holds; the moving copy only while a run moves: the mode's own, naming its flow and pace
    assert plan.feature == hold.feature and not MOTION_WORDS.search(plan.feature)
    moving = plan.program.moving_feature
    assert moving == MODES.mode(mode_id).techniques.stepped.moving_feature
    assert STEPPING[mode_id] in moving and STEPPED_PACE in moving and "through the Hubspace cloud" in moving
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
    """The registry as the venue box sees it: every canopy position has a device on the driver (an Afero metadevice id
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


def _shown(*, simulated: bool, live_enabled: bool = False) -> dict[str, tuple]:
    """Each D65 mode chosen through the real engine (compile, ladder and tier_allowed from the show's wiring)."""

    async def main():
        w = make_world(settings=settings_with(motion={"stepped": {"live_enabled": live_enabled}}))
        wiring = _show_wiring(w.registry, simulated=simulated, live_enabled=live_enabled)
        w.engine.deps.compile = wiring.compile
        w.engine.deps.ladder = wiring.ladder
        w.engine.deps.modes = wiring.mode_ids
        w.engine.deps.tier_allowed = wiring.tier_allowed
        out = {}
        for mode_id in STEPPING:
            await w.engine.request_mode(mode_id, source="ops")
            plan, runner = w.engine.current_plan(), w.engine.runner
            out[mode_id] = (plan.tier, plan.program is not None, runner is not None and runner.running)
            out[mode_id] += (wiring.tier_allowed(mode_id, Tier.STEPPED),)
        await w.close()
        return out

    return run_virtual(main)


def test_live_hardware_refuses_the_stepped_rung_and_the_ladder_lands_on_the_still_module_level_look():
    shown = _shown(simulated=False)
    # Coastal Tides: [stepped, modules, strand] → modules. Golden Hour: canvas (no commissioned slot, no Gate C),
    # slot (nothing hand-painted, no Gate B), stepped (Gate R) → modules. Nothing steps, nothing is sent a program.
    for mode_id in STEPPING:
        assert shown[mode_id] == (Tier.T3, False, False, (False, STEPPED_LIVE_OFF)), mode_id


def test_after_gate_r_live_enabled_opens_the_stepped_rung_on_live_hardware():
    shown = _shown(simulated=False, live_enabled=True)  # Alon flips it once the stepped rehearsal passes
    for mode_id in STEPPING:
        assert shown[mode_id] == (Tier.STEPPED, True, True, (True, "")), mode_id


def test_the_simulator_steps_both_where_their_better_rungs_arent_available():
    shown = _shown(simulated=True)  # uncommissioned: Golden Hour's canvas isn't available, so its rays move
    for mode_id in STEPPING:
        assert shown[mode_id] == (Tier.STEPPED, True, True, (True, "")), mode_id


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
