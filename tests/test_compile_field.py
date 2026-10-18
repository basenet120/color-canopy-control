"""Module level (T3) sampled from a 2-D design field (tests/fixtures_modes_field.yaml): the x-linear field at the
module centres is D59's literal columns byte for byte (pinned here since D63 replaced them in config/modes.yaml), and
a radial design is truly 2-D."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conductor import rows as R
from conductor.capabilities import detect
from conductor.config import ColorRecipe, RecipesFile, load_recipes, load_registry
from conductor.engine_api import Tier
from conductor.geometry import load_canopy_config
from conductor.looks import CompileContext, compile_mode, module_look, parse_modes
from conductor.looks.compile import FINER_THAN_GRID
from conductor.looks.schema import ModesFile
from conductor.scenes import PaletteColor, load_scenes
from conductor.sim.profiles import profile_parts

PATH = Path(__file__).with_name("fixtures_modes_field.yaml")
REGISTRY = load_registry()
MODULES = REGISTRY.canopy_modules()
BY_POSITION = {m.module: m for m in MODULES}
SCENES = load_scenes()
CANOPY = load_canopy_config()
EDGE_MIDDLES = ["r0c1", "r1c0", "r1c2", "r2c1"]
CORNERS = ["r0c0", "r0c2", "r2c0", "r2c2"]


# D59's module-level columns (config/modes.yaml before D63): each scene's x / 225 gradient at x = 37.5, 112.5, 187.5 in
D59_COLUMNS = {
    "lowcountry-calm": [(234, 239, 230), (209, 234, 227), (234, 239, 230)],
    "rainbow-row-sunset": [(255, 175, 175), (255, 210, 224), (216, 189, 232)],
    "garden-gathering": [(172, 207, 174), (230, 238, 209), (241, 241, 224)],
    "coastal-tides": [(111, 181, 213), (150, 219, 211), (206, 238, 221)],
    "golden-hour": [(255, 194, 156), (255, 155, 196), (214, 132, 193)],
    "goodnight-glow": [(156, 149, 216), (190, 169, 230), (194, 199, 239)],
}


FROZEN = Path(__file__).with_name("fixtures_recipes_frozen.yaml")


def _frozen_recipes() -> RecipesFile:
    """config's recipes with today's scene colours frozen over them (Design Lab PLAN §3.7): the fixture names the
    scenes' recipe keys, so a Design Lab export that renames or drops one can't break these compiler tests."""
    live = load_recipes().model_dump()
    frozen = yaml.safe_load(FROZEN.read_text(encoding="utf-8"))
    return RecipesFile.model_validate({**live, "colors": {**live["colors"], **frozen["colors"]}})


RECIPES = _frozen_recipes()  # every scene's palette is a recipe since D63
FIELD = parse_modes(
    yaml.safe_load(PATH.read_text(encoding="utf-8")), recipes=RECIPES, canopy=CANOPY, path=PATH
)


def _literal_columns() -> ModesFile:
    """The fixture's six scenes with D59's literal columns in place of the sample (same brightness and copy)."""
    data = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    for mode_id, columns in D59_COLUMNS.items():
        modules = data["modes"][mode_id]["techniques"]["modules"]
        level = modules.pop("sample")["brightness"]
        modules["columns"] = [{"kind": "rgb", "rgb": list(rgb), "brightness": level} for rgb in columns]
    return parse_modes(data, recipes=RECIPES, canopy=CANOPY, path=PATH)


LITERAL = _literal_columns()
CAPS = detect(*profile_parts("sqhdk03-sim"))


def _ctx(modes=FIELD, **kw) -> CompileContext:
    return CompileContext(
        registry=REGISTRY, capabilities={m.id: CAPS for m in MODULES}, modes=modes, recipes=RECIPES, **kw
    )


def _colours(mode_id: str, ctx: CompileContext) -> dict[str, tuple[int, int, int]]:
    mode = ctx.modes.mode(mode_id)
    return {m.module: module_look(mode, m, ctx).rgb for m in MODULES}


def _brightness(target) -> int:
    (row,) = [r for r in target.rows if r.function_class == "brightness"]
    return int(row.value)


@pytest.mark.parametrize("mode_id", [s.id for s in SCENES.scenes])
def test_an_x_linear_field_at_the_module_centres_is_d59s_literal_columns(mode_id):
    sampled = compile_mode(mode_id, Tier.T3, _ctx())
    literal = compile_mode(mode_id, Tier.T3, _ctx(modes=LITERAL))
    assert sampled.modules == literal.modules  # every row, expectation and preview, byte for byte
    assert (sampled.feature, sampled.motion, sampled.notes) == (
        literal.feature,
        literal.motion,
        literal.notes,
    )
    for m in MODULES:
        assert R.color_rgb(*D59_COLUMNS[mode_id][int(m.module[3])]) in sampled.modules[m.id].rows


@pytest.mark.parametrize("mode_id", [s.id for s in SCENES.scenes])
def test_d59s_columns_are_each_scenes_x_gradient_at_the_column_centres(mode_id):
    """The pinned anchor is no typo: each column is the scene catalog's own gradient at its centre (the palette is
    the fixture's own preview, so a Design Lab colour change in config/ can't move this pin)."""
    preview = FIELD.mode(mode_id).preview
    scene = SCENES.get(mode_id).model_copy(
        update={"palette": [PaletteColor(name=f"stop {i}", hex=h) for i, h in enumerate(preview)]}
    )
    assert D59_COLUMNS[mode_id] == [scene.color_at((c + 0.5) * 75 / 225) for c in range(3)]


def test_a_radial_design_gives_four_levels_from_the_centre_out():
    plan = compile_mode("radial-sunset", Tier.T3, _ctx())
    colours = _colours("radial-sunset", _ctx())
    assert colours["r1c1"] != colours["r0c1"] != colours["r0c0"] != colours["r1c1"]
    assert colours["r0c1"] == colours["r2c1"] and colours["r1c0"] == colours["r1c2"]
    assert len({colours[p] for p in ("r0c0", "r0c2", "r2c0", "r2c2")}) == 1
    assert len(set(colours.values())) == 4  # 0 / 40 / 75 / 85 in from the centre
    assert (
        colours["r1c1"] == RECIPES.colors["pastel-coral"].strand.rgb
    )  # pastel coral, the first stop, at the centre
    for m in MODULES:
        assert R.color_rgb(*colours[m.module]) in plan.modules[m.id].rows
    assert FINER_THAN_GRID not in " ".join(plan.notes)


def test_every_sampled_module_gets_the_same_brightness():
    for mode_id in FIELD.modes:
        plan = compile_mode(mode_id, Tier.T3, _ctx())
        level = FIELD.mode(mode_id).techniques.modules.sample.brightness
        assert {_brightness(t) for t in plan.modules.values()} == {level}


def test_the_mean_of_a_uniform_field_is_its_centre():
    modes = FIELD.model_copy(deep=True)
    sample = modes.modes["radial-sunset"].techniques.modules.sample
    for stop in sample.canvas.stops:
        stop.color = "lilac"
    centre = compile_mode("radial-sunset", Tier.T3, _ctx(modes=modes))
    sample.at = "mean"
    mean = compile_mode("radial-sunset", Tier.T3, _ctx(modes=modes))
    assert mean.modules == centre.modules
    assert set(_colours("radial-sunset", _ctx(modes=modes)).values()) == {RECIPES.colors["lilac"].strand.rgb}


def test_the_mean_averages_the_modules_heads():
    modes = FIELD.model_copy(deep=True)
    modes.modes["radial-sunset"].techniques.modules.sample.at = "mean"
    centre, mean = _colours("radial-sunset", _ctx()), _colours("radial-sunset", _ctx(modes=modes))
    # the centre module's heads are all away from the centre point, so their mean is further along the palette
    assert centre["r1c1"] == RECIPES.colors["pastel-coral"].strand.rgb != mean["r1c1"]
    # r0c0 and r0c2 are A panels mirrored left to right about the centre: their heads sit at the same distances
    assert mean["r0c0"] == mean["r0c2"]


def test_a_design_that_repeats_faster_than_the_grid_says_so():
    modes = FIELD.model_copy(deep=True)
    modes.modes["radial-sunset"].techniques.modules.sample.canvas.field.cycles = 2
    plan = compile_mode("radial-sunset", Tier.T3, _ctx(modes=modes))
    assert FINER_THAN_GRID in plan.notes
    modes.modes["radial-sunset"].techniques.modules.sample.at = "mean"
    assert FINER_THAN_GRID not in compile_mode("radial-sunset", Tier.T3, _ctx(modes=modes)).notes
    # a whole canopy that samples to a single colour says so too
    flat = FIELD.model_copy(deep=True)
    for stop in flat.modes["radial-sunset"].techniques.modules.sample.canvas.stops:
        stop.color = "lilac"
    assert FINER_THAN_GRID in compile_mode("radial-sunset", Tier.T3, _ctx(modes=flat)).notes


def test_a_partial_canopy_keeps_the_d62_copy():
    driven = {"r0c0", "r0c1"}
    addresses = {m.id: (f"md-{m.id}" if m.module in driven else None) for m in MODULES}
    plan = compile_mode("radial-sunset", Tier.T3, _ctx(addresses=addresses))
    assert (
        plan.feature
        == "Colour and dimming: two Hubspace strands, each set to a single colour from the scene."
    )
    assert sorted(plan.modules) == sorted(BY_POSITION[p].id for p in driven)
    assert len(plan.skipped) == 7
    full = _colours("radial-sunset", _ctx())
    for p in driven:
        assert R.color_rgb(*full[p]) in plan.modules[BY_POSITION[p].id].rows
    # two driven modules of one colour aren't "a pattern finer than the grid": that note is for a whole canopy
    flat = FIELD.model_copy(deep=True)
    for stop in flat.modes["radial-sunset"].techniques.modules.sample.canvas.stops:
        stop.color = "lilac"
    one_colour = compile_mode("radial-sunset", Tier.T3, _ctx(modes=flat, addresses=addresses))
    assert all(R.color_rgb(196, 179, 236) in t.rows for t in one_colour.modules.values())
    assert FINER_THAN_GRID not in one_colour.notes


def _flat(colour: str):
    """The fixture with radial-sunset's stops all one colour (bypassing the load check that refuses it)."""
    modes = FIELD.model_copy(deep=True)
    for stop in modes.modes["radial-sunset"].techniques.modules.sample.canvas.stops:
        stop.color = colour
    return modes


def test_a_whole_canopy_of_one_colour_doesnt_claim_a_layout():
    # all stops one colour: nine strands of lilac aren't "the palette steps from the centre out"
    plan = compile_mode("radial-sunset", Tier.T3, _ctx(modes=_flat("lilac")))
    assert plan.feature == "Colour and dimming: all nine Hubspace strands set to one colour from the scene."
    assert FINER_THAN_GRID in plan.notes
    # three repeats across the width: every module column's centre lands half way through a repeat
    data = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    data["modes"]["radial-sunset"]["techniques"]["modules"]["sample"]["canvas"]["field"] = {
        "kind": "linear",
        "cycles": 3,
    }
    modes = ModesFile.model_validate(data)  # the load check refuses this; compile must still tell the truth
    thrice = compile_mode("radial-sunset", Tier.T3, _ctx(modes=modes))
    assert len(set(_colours("radial-sunset", _ctx(modes=modes)).values())) == 1
    assert "one colour" in thrice.feature and "from the centre out" not in thrice.feature
    # a design with a layout keeps its copy
    assert compile_mode("radial-sunset", Tier.T3, _ctx()).feature.endswith("steps from the centre out.")


def test_a_sampled_gradient_down_the_canopy_steps_row_by_row():
    data = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    techniques = data["modes"]["rainbow-row-sunset"]["techniques"]["modules"]
    techniques["sample"]["canvas"] = {
        "kind": "gradient",
        "axis": "y",
        "stops": techniques["sample"]["canvas"]["stops"],
        "recipe": "rgb_only",
    }
    techniques["feature"] = techniques["feature"].replace("column by column", "row by row")
    modes = parse_modes(data, recipes=RECIPES, canopy=CANOPY, path=PATH)
    colours = _colours("rainbow-row-sunset", _ctx(modes=modes))
    for row in "012":
        assert len({colours[f"r{row}c{c}"] for c in "012"}) == 1
    assert len({colours[f"r{r}c0"] for r in "012"}) == 3
    # the same stops as the x-linear columns, turned: row r is what column c = r was
    across = _colours("rainbow-row-sunset", _ctx())
    assert [colours[f"r{i}c1"] for i in range(3)] == [across[f"r1c{i}"] for i in range(3)]
    plan = compile_mode("rainbow-row-sunset", Tier.T3, _ctx(modes=modes))
    assert plan.feature.endswith("row by row.")


def _recipes_with(name: str, r: int, g: int, b: int, level: int) -> RecipesFile:
    entry = {"r": r, "g": g, "b": b, "colorBrightness": level}
    colours = dict(RECIPES.colors)
    colours[name] = ColorRecipe(
        label=name, hex="#000000", rgb_only=entry, blend=entry, strand={"rgb": [r, g, b]}
    )
    return RecipesFile(colors=colours)


def _sampled_with(recipes: RecipesFile, at: str) -> tuple[int, int, int]:
    modes = _flat("test-colour")
    modes.modes["radial-sunset"].techniques.modules.sample.at = at
    ctx = CompileContext(
        registry=REGISTRY, capabilities={m.id: CAPS for m in MODULES}, modes=modes, recipes=recipes
    )
    (colour,) = set(_colours("radial-sunset", ctx).values())
    return colour


def test_a_colour_level_below_100_dims_the_sampled_rgb():
    full = _recipes_with("test-colour", 196, 179, 236, 100)
    half = _recipes_with("test-colour", 196, 179, 236, 50)
    assert _sampled_with(full, "centre") == (196, 179, 236)
    assert _sampled_with(half, "centre") == (98, 90, 118)  # 89.5 rounds up
    assert _sampled_with(half, "mean") == (98, 90, 118)


def test_a_channel_exactly_on_a_half_rounds_up_at_the_centre_and_in_the_mean():
    # 1, 3, 5 at level 50 are 0.5, 1.5, 2.5: half up gives 1, 2, 3 (round() would give 0, 2, 2)
    halves = _recipes_with("test-colour", 1, 3, 5, 50)
    assert _sampled_with(halves, "centre") == (1, 2, 3)
    assert _sampled_with(halves, "mean") == (1, 2, 3)


def test_a_fins_design_compiles_at_module_level_with_three_ring_ripple():
    """D74: the fins' rings at module level read three rings (the centre, the edge-middles, the corners), and the
    D64 ripple lands them centre first."""
    data = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    modules = data["modes"]["coastal-tides"]["techniques"]["modules"]
    modules["sample"]["canvas"]["field"] = {"kind": "fins"}
    modules["feature"] = (
        "Colour and dimming: each Hubspace strand set to a single colour, so the palette steps from the centre out."
    )
    modes = parse_modes(data, recipes=RECIPES, canopy=CANOPY, path=PATH)
    plan = compile_mode("coastal-tides", Tier.T3, _ctx(modes=modes))
    colours = _colours("coastal-tides", _ctx(modes=modes))
    assert colours["r1c1"] == RECIPES.colors["ocean-blue"].strand.rgb  # the first stop, on the deep centre
    assert colours["r0c1"] == colours["r2c1"]
    for m in MODULES:
        assert R.color_rgb(*colours[m.module]) in plan.modules[m.id].rows
    rings = [{BY_POSITION[p].id for p in ring} for ring in (["r1c1"], EDGE_MIDDLES, CORNERS)]
    assert [set(ring) for ring in plan.waves] == rings
