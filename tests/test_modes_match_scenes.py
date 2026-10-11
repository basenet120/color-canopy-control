"""The show's six modes are the six approved scenes (D59): config/modes.yaml matches config/scenes.yaml.

One catalog: each mode's id, label, blurb and preview are the scene's id, name, mood and palette (in order), and every
tier renders the scene's palette — the canvas (T1) head for head as the scene's gradient across the canopy (x / 225,
the Studio's illustration), module level (T3) as the gradient at each module's column centre, the whole-strand look
as the scene's solid colour — at the scene's brightness, with no motion.
"""

from __future__ import annotations

import pytest

from conductor.config import load_recipes, load_registry
from conductor.geometry import load_canopy_config
from conductor.looks import compose_canvas, head_positions, load_modes, placement_for
from conductor.looks.schema import GradientCanvas
from conductor.rows import FrameEntry
from conductor.scenes import load_scenes

MODES = load_modes()
SCENES = load_scenes()
RECIPES = load_recipes()
CANOPY = load_canopy_config()
MODULES = load_registry().canopy_modules()
IDS = [s.id for s in SCENES.scenes]


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
    if t.modules is not None:
        levels += [look.brightness for look in t.modules.columns or []]
    if t.strand is not None:
        levels.append(t.strand.look.brightness)
    assert levels and set(levels) == {scene.brightness_draft}
    assert mode.motion is False


@pytest.mark.parametrize("mode_id", IDS)
def test_every_ladder_ends_on_the_solid_colour_and_t3_samples_the_column_centres(mode_id):
    scene, mode = SCENES.get(mode_id), MODES.mode(mode_id)
    assert mode.ladder[-2:] == ["modules", "strand"]
    strand = mode.techniques.strand
    assert strand.look.kind == "rgb" and tuple(strand.look.rgb) == scene.solid.rgb
    assert strand.fallback_look is None
    assert strand.feature.endswith(f"set to {scene.solid.name.lower()}.")
    columns = mode.techniques.modules.columns
    width, module_w = float(CANOPY["width_in"]), float(CANOPY["module"]["width_in"])
    centres = [(c + 0.5) * module_w / width for c in range(len(columns))]
    assert [look.kind for look in columns] == ["rgb"] * 3
    assert [tuple(look.rgb) for look in columns] == [scene.color_at(t) for t in centres]


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


@pytest.mark.parametrize("mode_id", ["rainbow-row-sunset", "garden-gathering", "golden-hour"])
def test_the_canvas_is_the_scenes_gradient_across_the_canopy_head_for_head(mode_id):
    scene = SCENES.get(mode_id)
    canvas = MODES.mode(mode_id).techniques.canvas.canvas
    assert isinstance(canvas, GradientCanvas) and canvas.axis == "x" and canvas.recipe == "rgb_only"
    # the palette's colours, evenly spaced, named by recipes holding exactly the palette hex
    n = len(scene.palette)
    assert [s.at for s in canvas.stops] == [pytest.approx(i / (n - 1)) for i in range(n)]
    assert [RECIPES.colors[s.color].hex.upper() for s in canvas.stops] == [c.hex for c in scene.palette]
    assert [RECIPES.colors[s.color].label for s in canvas.stops] == [c.name for c in scene.palette]
    placements = [placement_for(CANOPY, e, frame_length=30) for e in MODULES]
    frames = compose_canvas(canvas, canopy=CANOPY, recipes=RECIPES, placements=placements)
    width = float(CANOPY["width_in"])
    heads = 0
    for p in placements:
        for (x, _y), entry in zip(head_positions(CANOPY, p), frames[p.device_id], strict=True):
            r, g, b = scene.color_at(x / width)
            assert entry == FrameEntry(
                r, g, b, 0, 100, 0
            )  # full strength, RGB only: brightness is the device's
            heads += 1
    assert heads == 270


def test_painted_palette_recipes_are_rgb_only_with_no_white():
    names = {
        s.color for m in MODES.modes.values() if m.techniques.canvas for s in m.techniques.canvas.canvas.stops
    }
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
