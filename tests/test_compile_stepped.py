"""A stepped look (Tier.STEPPED, D65) compiled from tests/fixtures_modes_stepped.yaml: the hold is the static
module-level look, each step is one full state vector per driven module, the design walks across the 3×3 grid in
the direction its copy names, and the moving copy is true of the strands actually driven. The show's own stepped rungs
(Coastal Tides, Golden Hour) are tested in tests/test_d65_modes.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conductor import rows as R
from conductor.capabilities import detect
from conductor.config import RecipesFile, load_recipes, load_registry
from conductor.engine_api import PlanError, Tier
from conductor.geometry import load_canopy_config
from conductor.looks import CompileContext, compile_mode, load_modes, parse_modes
from conductor.looks.compile import STEPPED_STILL
from conductor.sim.profiles import profile_parts

PATH = Path(__file__).with_name("fixtures_modes_stepped.yaml")
REGISTRY = load_registry()
MODULES = REGISTRY.canopy_modules()
BY_POSITION = {m.module: m for m in MODULES}
FROZEN = Path(__file__).with_name("fixtures_recipes_frozen.yaml")


def _frozen_recipes() -> RecipesFile:
    """config's recipes with today's scene colours frozen over them (Design Lab PLAN §3.7): the fixture names the
    scenes' recipe keys, so a Design Lab export that renames or drops one can't break these compiler tests."""
    live = load_recipes().model_dump()
    frozen = yaml.safe_load(FROZEN.read_text(encoding="utf-8"))
    return RecipesFile.model_validate({**live, "colors": {**live["colors"], **frozen["colors"]}})


RECIPES = _frozen_recipes()
CANOPY = load_canopy_config()
STEPPED = parse_modes(
    yaml.safe_load(PATH.read_text(encoding="utf-8")), recipes=RECIPES, canopy=CANOPY, path=PATH
)
CAPS = detect(*profile_parts("sqhdk03-sim"))
METADEVICE_ID = "0" * 8 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 12  # a UUID-length id
ROW_KEYS = [
    ("power", None),
    ("color-mode", None),
    ("color-rgb", None),
    ("brightness", None),
    ("speed", "color-sequence"),
    ("toggle", "party-mode"),
]


def _ctx(modes=STEPPED, **kw) -> CompileContext:
    return CompileContext(
        registry=REGISTRY, capabilities={m.id: CAPS for m in MODULES}, modes=modes, recipes=RECIPES, **kw
    )


def _grid(step) -> list[list[str]]:
    """A step's colours as rows × columns of the canopy grid."""
    return [[step.colours[f"r{r}c{c}"] for c in range(3)] for r in range(3)]


def _partial(driven=("r0c0", "r0c1"), modes=STEPPED) -> CompileContext:
    return _ctx(modes, addresses={m.id: (f"md-{m.id}" if m.module in driven else None) for m in MODULES})


@pytest.mark.parametrize("mode_id", list(STEPPED.modes))
def test_a_stepped_mode_compiles_one_full_state_vector_per_module_per_step(mode_id):
    plan = compile_mode(mode_id, "stepped", _ctx())
    tech = STEPPED.mode(mode_id).techniques.stepped
    assert (
        plan.tier is Tier.STEPPED and plan.motion is False
    )  # the Conductor holds the phase (one-module resends ok)
    program = plan.program
    assert program is not None and program.kind == "stepped"
    assert (program.step_s, program.run_s, program.direction) == (6.0, 90.0, "forward")
    assert program.field == tech.canvas.field.model_dump(mode="json")
    assert [s.index for s in program.steps] == list(range(6))
    for step in program.steps:
        assert sorted(step.rows) == sorted(
            m.id for m in MODULES
        )  # one ModuleTarget's worth per driven module
        assert sorted(step.colours) == sorted(m.module for m in MODULES)
        for m in MODULES:
            rows = step.rows[m.id]
            assert [r.key for r in rows] == ROW_KEYS  # never a slot object (no color-sequence-v2)
            assert all(r.function_class != "color-sequence-v2" for r in rows)
            assert R.payload_bytes(METADEVICE_ID, rows, 1_790_000_000_000) <= 700
            assert R.brightness(tech.brightness) in rows and R.power(True) in rows
            hex_ = step.colours[m.module]
            assert R.color_rgb(int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16)) in rows
    assert plan.waves is not None  # the hold still lands in the design's order (D64)
    assert plan.to_json()["program"]["steps"][1]["colours"] == program.steps[1].colours


def test_the_largest_step_payload_is_still_under_700_bytes():
    rows = R.full_state_vector(R.Look("rgb", rgb=(255, 255, 255), brightness=100))
    assert R.payload_bytes(METADEVICE_ID, rows, 1_790_000_000_000) <= 700


@pytest.mark.parametrize("mode_id", list(STEPPED.modes))
def test_the_hold_is_the_first_step_and_the_static_module_level_look(mode_id):
    plan = compile_mode(mode_id, Tier.STEPPED, _ctx())
    static = compile_mode(mode_id, Tier.T3, _ctx())
    assert plan.modules == static.modules  # rows, expectations and previews: the static look, byte for byte
    assert plan.program.steps[0].rows == {d: t.rows for d, t in static.modules.items()}
    assert plan.feature == static.feature  # nothing moves until a runner steps it: the hold's copy
    assert plan.program.moving_feature == STEPPED.mode(mode_id).techniques.stepped.moving_feature
    assert plan.waves == static.waves


def test_compiling_at_stepped_gives_a_stepped_plan_not_the_static_one():
    plan = compile_mode("radial-outward", "stepped", _ctx())
    assert plan.tier is Tier.STEPPED and plan.program is not None
    assert compile_mode("radial-outward", "modules", _ctx()).program is None


def test_columns_walk_column_by_column():
    steps = compile_mode("linear-columns", Tier.STEPPED, _ctx()).program.steps
    for n, step in enumerate(steps):
        earlier = _grid(steps[(n - 2) % 6])  # the column centres are 2 steps apart (t = 1/6, 1/2, 5/6)
        for row, before in zip(_grid(step), earlier, strict=True):
            assert row[1] == before[0] and row[2] == before[1]
            assert len(set(row)) == 3
    assert len({step.colours["r0c0"] for step in steps}) == 6  # the palette really moves through each module


def test_rows_walk_row_by_row():
    steps = compile_mode("linear-rows", Tier.STEPPED, _ctx()).program.steps
    for n, step in enumerate(steps):
        grid, earlier = _grid(step), _grid(steps[(n - 2) % 6])
        assert grid[1] == earlier[0] and grid[2] == earlier[1]
        assert all(len(set(row)) == 1 for row in grid)  # a row is one colour: the design runs top to bottom


def test_a_radial_design_moves_out_from_the_centre():
    steps = compile_mode("radial-outward", Tier.STEPPED, _ctx()).program.steps
    edges = ("r0c1", "r1c0", "r1c2", "r2c1")
    for n, step in enumerate(steps):
        # r1c1 leads: the edge-middle modules (t = ½) show what the centre showed 3 steps earlier
        assert {step.colours[p] for p in edges} == {steps[(n - 3) % 6].colours["r1c1"]}
        assert step.colours["r0c0"] != step.colours["r1c1"]
    coral = RECIPES.colors["pastel-coral"].hex  # the frozen recipe, so a Design Lab export can't move it
    assert steps[0].colours["r1c1"] == coral == "#FF9E96"  # pastel coral, the first stop, at the centre


def test_reverse_flows_back_towards_where_the_design_starts():
    modes = STEPPED.model_copy(deep=True)
    modes.modes["linear-columns"].techniques.stepped.direction = "reverse"
    steps = compile_mode("linear-columns", Tier.STEPPED, _ctx(modes=modes)).program.steps
    forward = compile_mode("linear-columns", Tier.STEPPED, _ctx()).program.steps
    assert steps[0].rows == forward[0].rows  # the same hold
    for n, step in enumerate(steps):
        later = _grid(steps[(n - 2) % 6])
        for row, before in zip(_grid(step), later, strict=True):
            assert row[0] == before[1] and row[1] == before[2]  # right to left now
    assert [s.colours for s in steps[1:]] == [s.colours for s in reversed(forward[1:])]


def test_a_partial_canopy_counts_the_driven_strands_and_claims_no_canopy_pattern():
    plan = compile_mode("radial-outward", Tier.STEPPED, _partial())
    assert sorted(plan.modules) == sorted(BY_POSITION[p].id for p in ("r0c0", "r0c1"))
    assert all(sorted(s.rows) == sorted(plan.modules) for s in plan.program.steps)
    assert all(sorted(s.colours) == ["r0c0", "r0c1"] for s in plan.program.steps)
    moving = plan.program.moving_feature
    assert "both Hubspace strands" in moving and "every few seconds" in moving
    assert "nine" not in moving and "canopy" not in moving and "centre" not in moving
    assert (
        plan.feature
        == "Colour and dimming: two Hubspace strands, each set to a single colour from the scene."
    )
    one = compile_mode("radial-outward", Tier.STEPPED, _partial(("r1c1",)))
    assert "one Hubspace strand showing one colour" in one.program.moving_feature
    assert "canopy" not in one.program.moving_feature
    five = compile_mode("radial-outward", Tier.STEPPED, _partial(("r0c0", "r0c1", "r0c2", "r1c0", "r1c1")))
    assert five.program.moving_feature.startswith(
        "Colour and dimming through the Hubspace cloud: five Hubspace"
    )


def test_a_design_where_nothing_moves_keeps_the_holds_copy():
    modes = STEPPED.model_copy(
        deep=True
    )  # the load check refuses one colour; compile must still tell the truth
    techniques = modes.modes["radial-outward"].techniques
    for canvas in (techniques.stepped.canvas, techniques.modules.sample.canvas):
        for stop in canvas.stops:
            stop.color = "lilac"
    plan = compile_mode("radial-outward", Tier.STEPPED, _ctx(modes=modes))
    assert all(s.rows == plan.program.steps[0].rows for s in plan.program.steps)
    assert plan.program.moving_feature == plan.feature and "moves" not in plan.program.moving_feature
    assert STEPPED_STILL in plan.notes
    partial = compile_mode("radial-outward", Tier.STEPPED, _partial(modes=modes))
    assert partial.program.moving_feature == partial.feature  # the D62 sentence, no "every few seconds"
    assert "every few seconds" not in partial.feature


def test_a_design_whose_strands_all_change_together_claims_no_layout():
    modes = STEPPED.model_copy(deep=True)
    # three repeats across the width: every column centre is half way through a repeat, so all nine strands show
    # one colour at each step (the load check refuses the static look; compile must still tell the truth)
    for canvas in (
        modes.modes["linear-columns"].techniques.stepped.canvas,
        modes.modes["linear-columns"].techniques.modules.sample.canvas,
    ):
        canvas.field.cycles = 3
    plan = compile_mode("linear-columns", Tier.STEPPED, _ctx(modes=modes))
    assert all(len(set(s.colours.values())) == 1 for s in plan.program.steps)
    assert len({s.colours["r0c0"] for s in plan.program.steps}) > 1
    moving = plan.program.moving_feature
    assert moving == (
        "Colour and dimming through the Hubspace cloud: all nine Hubspace strands set to one colour from the "
        "scene, sent the next colour every few seconds."
    )
    assert "column by column" not in moving


def test_a_first_step_that_isnt_the_static_look_is_refused():
    modes = STEPPED.model_copy(
        deep=True
    )  # bypasses the schema's ladder rule (the same canvas and brightness)
    modes.modes["radial-outward"].techniques.stepped.brightness = 50
    with pytest.raises(PlanError, match="first step isn't the static module-level look it holds"):
        compile_mode("radial-outward", Tier.STEPPED, _ctx(modes=modes))


def test_a_mode_without_a_stepped_rung_cant_compile_stepped_and_the_show_steps_only_two():
    shipped = load_modes()
    with pytest.raises(PlanError, match="has no stepped technique"):
        compile_mode("lowcountry-calm", Tier.STEPPED, _ctx(modes=shipped))  # the idle look never steps
    # D65: only Coastal Tides and Golden Hour have a stepped rung (tests/test_d65_modes.py)
    stepping = [m for m, mode in shipped.modes.items() if mode.techniques.stepped is not None]
    assert stepping == [m for m, mode in shipped.modes.items() if "stepped" in mode.ladder]
    assert stepping == ["coastal-tides", "golden-hour"]


def test_ops_and_alerts_have_words_for_every_tier():
    from conductor import alerts
    from conductor.routes import ops

    for words in (ops.TIER_WORDS, alerts.TIER_WORDS):
        assert set(words) == {str(t) for t in Tier}
        assert "step" in words["stepped"]
