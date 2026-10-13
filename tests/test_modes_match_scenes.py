"""The show's six modes are the six approved scenes (D59), each laid out as one 2-D design (D63): config/modes.yaml
matches config/scenes.yaml.

One catalog: each mode's id, label, blurb and preview are the scene's id, name, mood and palette (in order). Each scene
is one design — a `field` canvas whose stops are the scene's palette, in order and evenly spaced — and every tier
renders it: the canvas (T1) head for head as the palette at the field's value, module level (T3) as the same design
at each module's centre, the whole-strand look as the scene's solid colour — at the scene's brightness, with no
motion. The layouts are D63's drafts (Paris reviews them); palettes, names, brightness and slots are unchanged.
"""

from __future__ import annotations

import pytest

from conductor.capabilities import detect
from conductor.config import load_recipes, load_registry
from conductor.engine_api import Tier
from conductor.geometry import load_canopy_config
from conductor.looks import (
    CompileContext,
    compile_mode,
    compose_canvas,
    head_positions,
    load_modes,
    placement_for,
)
from conductor.looks import field as F
from conductor.looks.schema import FieldCanvas
from conductor.rows import FrameEntry
from conductor.scenes import load_scenes
from conductor.sim.profiles import profile_parts

MODES = load_modes()
SCENES = load_scenes()
RECIPES = load_recipes()
CANOPY = load_canopy_config()
REGISTRY = load_registry()
MODULES = REGISTRY.canopy_modules()
IDS = [s.id for s in SCENES.scenes]
POSITIONS = [f"r{r}c{c}" for r in range(3) for c in range(3)]
CAPS = detect(*profile_parts("sqhdk03-sim"))
LIVE = ("r0c0", "r0c1")  # the Mac show box's two shop kits (D62)
D62_TWO = "Colour and dimming: two Hubspace strands, each set to a single colour from the scene."

# D63's draft layouts, in canopy inches (reflected-ceiling view: x from the left edge, y from the top edge)
LAYOUTS = {
    "lowcountry-calm": {"kind": "radial", "centre_in": (112.5, 60), "invert": True},  # sea glass, centre
    "rainbow-row-sunset": {"kind": "linear", "angle_deg": 45},  # corner to corner
    "garden-gathering": {"kind": "radial", "centre_in": (112.5, 120), "invert": True},  # from one long edge
    "coastal-tides": {"kind": "linear", "angle_deg": 78},  # a tilted shoreline
    "golden-hour": {"kind": "radial", "centre_in": (0, 60)},  # a sun at the middle of the left edge
    "goodnight-glow": {"kind": "radial", "centre_in": (187.5, 20), "invert": True},  # a moon over r0c2
}


def _ctx(**kw) -> CompileContext:
    return CompileContext(registry=REGISTRY, capabilities={m.id: CAPS for m in MODULES}, **kw)


def _design(mode_id: str) -> FieldCanvas:
    return MODES.mode(mode_id).techniques.modules.sample.canvas


def _palette_at(mode_id: str, t: float) -> tuple[int, int, int]:
    """The scene's palette as evenly spaced stops at t, rounded half up (the scene catalog's own interpolation; the
    design's field does any mirroring, so the scene's `transform` isn't applied twice)."""
    return SCENES.get(mode_id).model_copy(update={"transform": "linear"}).color_at(t)


def _t3(mode_id: str) -> dict[str, tuple[int, int, int]]:
    """Module position → the RGB module level sends there (full canopy, from the compiled plan's rows)."""
    plan = compile_mode(mode_id, Tier.T3, _ctx())
    out = {}
    for m in MODULES:
        (rgb,) = [r.value for r in plan.modules[m.id].rows if r.function_class == "color-rgb"]
        out[m.module] = (rgb["color-rgb"]["r"], rgb["color-rgb"]["g"], rgb["color-rgb"]["b"])
    return out


def test_the_modes_are_the_scenes_ids_labels_blurbs_and_previews_in_order():
    assert list(MODES.modes) == IDS
    for scene in SCENES.scenes:
        mode = MODES.mode(scene.id)
        assert mode.label == scene.name
        assert mode.blurb == scene.mood
        assert [c.upper() for c in mode.preview] == [c.hex for c in scene.palette]


@pytest.mark.parametrize("mode_id", IDS)
def test_every_tier_uses_the_scenes_brightness_and_nothing_moves(mode_id):
    scene, mode = SCENES.get(mode_id), MODES.mode(mode_id)
    t = mode.techniques
    levels = []
    if t.canvas is not None:
        levels.append(t.canvas.brightness)
        assert t.canvas.motion_effect is None and t.canvas.motion_feature is None
    if t.slot is not None:
        levels.append(t.slot.brightness)
        assert t.slot.speed == 0
    levels.append(t.modules.sample.brightness)
    if t.strand is not None:
        levels.append(t.strand.look.brightness)
    assert levels and set(levels) == {scene.brightness_draft}
    assert mode.motion is False
    assert _design(mode_id).field.cycles == 1  # one static design, no repeats


@pytest.mark.parametrize("mode_id", IDS)
def test_every_ladder_ends_on_the_solid_colour(mode_id):
    scene, mode = SCENES.get(mode_id), MODES.mode(mode_id)
    assert mode.ladder[-2:] == ["modules", "strand"]
    strand = mode.techniques.strand
    assert strand.look.kind == "rgb" and tuple(strand.look.rgb) == scene.solid.rgb
    assert strand.fallback_look is None
    assert strand.feature.endswith(f"set to {scene.solid.name.lower()}.")


def test_three_modes_are_painted_one_per_slot_and_the_rest_start_at_module_level():
    painted = {m: mode.slot for m, mode in MODES.modes.items() if mode.techniques.canvas is not None}
    assert painted == {
        "rainbow-row-sunset": "custom-1",
        "garden-gathering": "custom-2",
        "golden-hour": "custom-3",
    }
    for mode_id, mode in MODES.modes.items():
        expected = ["canvas", "slot", "modules", "strand"] if mode_id in painted else ["modules", "strand"]
        assert mode.ladder == expected


@pytest.mark.parametrize("mode_id", IDS)
def test_each_scene_is_one_design_whose_stops_are_its_palette(mode_id):
    scene, mode = SCENES.get(mode_id), MODES.mode(mode_id)
    design = _design(mode_id)
    assert isinstance(design, FieldCanvas) and design.recipe == "rgb_only"
    assert mode.techniques.modules.sample.at == "centre"
    # the palette's colours, in order and evenly spaced, named by recipes holding exactly the palette hex
    n = len(scene.palette)
    assert [s.at for s in design.stops] == [i / (n - 1) for i in range(n)]
    assert [RECIPES.colors[s.color].hex.upper() for s in design.stops] == [c.hex for c in scene.palette]
    assert [RECIPES.colors[s.color].label for s in design.stops] == [c.name for c in scene.palette]
    # the D63 layout
    assert design.field == type(design.field).model_validate(LAYOUTS[mode_id])
    if mode.techniques.canvas is not None:  # one design drives T1 and T3
        assert mode.techniques.canvas.canvas == design


@pytest.mark.parametrize("mode_id", ["rainbow-row-sunset", "garden-gathering", "golden-hour"])
def test_the_canvas_is_the_palette_at_the_design_head_for_head(mode_id):
    canvas = MODES.mode(mode_id).techniques.canvas.canvas
    placements = [placement_for(CANOPY, e, frame_length=30) for e in MODULES]
    frames = compose_canvas(canvas, canopy=CANOPY, recipes=RECIPES, placements=placements)
    heads = 0
    for p in placements:
        for (x, y), entry in zip(head_positions(CANOPY, p), frames[p.device_id], strict=True):
            r, g, b = _palette_at(mode_id, F.value(canvas.field, x, y, CANOPY))
            # full strength, RGB only: brightness is the device's
            assert entry == FrameEntry(r, g, b, 0, 100, 0)
            heads += 1
    assert heads == 270


@pytest.mark.parametrize("mode_id", IDS)
def test_module_level_is_the_design_at_each_modules_centre(mode_id):
    design = _design(mode_id)
    colours = _t3(mode_id)
    for pos, (x, y) in F.module_centres(CANOPY, POSITIONS).items():
        assert colours[pos] == _palette_at(mode_id, F.value(design.field, x, y, CANOPY)), pos
    assert 4 <= len(set(colours.values())) <= 9  # a 2-D design on the grid, not one colour or three columns


@pytest.mark.parametrize("mode_id", IDS)
def test_the_module_level_copy_names_the_designs_layout(mode_id):
    feature = MODES.mode(mode_id).techniques.modules.feature
    words = F.layout_words(_design(mode_id).field, CANOPY)
    assert words in feature
    assert compile_mode(mode_id, Tier.T3, _ctx()).feature == feature  # the full canopy shows the design
    colours = _t3(mode_id)
    uniform_columns = all(len({colours[f"r{r}c{c}"] for r in range(3)}) == 1 for c in range(3))
    uniform_rows = all(len({colours[f"r{r}c{c}"] for c in range(3)}) == 1 for r in range(3))
    assert ("column by column" in feature) == uniform_columns
    assert ("row by row" in feature) == uniform_rows
    assert not uniform_columns and not uniform_rows  # D63: every scene is 2-D on the grid


def test_the_d63_layouts_name_where_each_design_sits():
    words = {m: F.layout_words(_design(m).field, CANOPY) for m in IDS}
    assert words == {
        "lowcountry-calm": "from the centre out",
        "rainbow-row-sunset": "diagonally",
        "garden-gathering": "from one edge",
        "coastal-tides": "diagonally",
        "golden-hour": "from one edge",
        "goodnight-glow": "from one spot",
    }
    calm = _t3("lowcountry-calm")  # radial symmetry: the four corners alike, sea glass in the centre
    assert len({calm[p] for p in ("r0c0", "r0c2", "r2c0", "r2c2")}) == 1
    assert calm["r1c1"] == (209, 234, 227) != calm["r0c0"]
    goodnight = _t3("goodnight-glow")  # the moon over r0c2 is moonlight blue
    assert goodnight["r0c2"] == (196, 214, 243)


@pytest.mark.parametrize("mode_id", IDS)
def test_on_the_partial_live_canopy_every_scene_keeps_the_d62_sentence(mode_id):
    """The Mac show box drives only r0c0 and r0c1 (D62): module-level copy never claims a canopy-wide layout."""
    addresses = {m.id: (f"md-{m.id}" if m.module in LIVE else None) for m in MODULES}
    plan = compile_mode(mode_id, Tier.T3, _ctx(addresses=addresses))
    assert sorted(plan.modules) == ["canopy-r0c0", "canopy-r0c1"]
    assert plan.feature == D62_TWO
    assert not any(p in plan.feature for p in F.LAYOUT_PHRASES)


def test_on_the_partial_live_canopy_the_two_kits_show_different_colours():
    """D63's layouts keep r0c0 and r0c1 visibly different (the plan asked for at least five of six; all six do)."""
    differ = [m for m in IDS if (c := _t3(m))["r0c0"] != c["r0c1"]]
    assert differ == IDS


def test_every_design_recipe_is_rgb_only_with_no_white():
    names = {s.color for m in IDS for s in _design(m).stops}
    assert len(names) == sum(len(s.palette) for s in SCENES.scenes)  # every palette colour has its own recipe
    for name in names:
        recipe = RECIPES.colors[name]
        r, g, b = (int(recipe.hex[i : i + 2], 16) for i in (1, 3, 5))
        assert recipe.rgb_only.model_dump() == {
            "r": r,
            "g": g,
            "b": b,
            "cct": 0,
            "colorBrightness": 100,
            "whiteBrightness": 0,
        }
        assert (
            recipe.blend == recipe.rgb_only
        )  # per-puck white is unverified (gate 6): no white is designed in
        assert recipe.strand.rgb == (r, g, b)
