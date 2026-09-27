"""The canvas composer: 270 heads in canopy inches → nine frames in wire order, with the head-count guard."""

from __future__ import annotations

import itertools

import pytest

from conductor.config import load_recipes, load_registry
from conductor.engine_api import PlanError
from conductor.geometry import build_layout, load_canopy_config
from conductor.looks import canvas_heads, compose_canvas, evaluate, head_positions, load_modes, placement_for
from conductor.looks.schema import BandCanvas, GradientCanvas, PathwayCanvas
from conductor.rows import FrameEntry

CANOPY = load_canopy_config()
RECIPES = load_recipes()
MODES = load_modes()
MODULES = load_registry().canopy_modules()


def _placements(entries=MODULES, frame_length=30):
    return [placement_for(CANOPY, e, frame_length=frame_length) for e in entries]


def _canvas(mode_id):
    return MODES.mode(mode_id).techniques.canvas.canvas


def _two_stop(axis, recipe="rgb_only"):
    return GradientCanvas.model_validate(
        {
            "kind": "gradient",
            "axis": axis,
            "stops": [{"at": 0, "color": "hd-orange"}, {"at": 1, "color": "peach"}],
            "recipe": recipe,
        }
    )


def test_every_one_of_the_270_heads_is_covered_exactly_once():
    heads = canvas_heads(CANOPY, _placements())
    assert len(heads) == 270
    assert len({(h.x, h.y) for h in heads}) == 270
    assert len({(h.device_id, h.index) for h in heads}) == 270
    # uncalibrated, the canvas is geometry.build_layout head for head (same scheme, same wire order)
    layout = {(f"canopy-{h.module}", h.index): (h.x, h.y) for h in build_layout(CANOPY)}
    assert {(h.device_id, h.index): (h.x, h.y) for h in heads} == layout


@pytest.mark.parametrize("mode_id", ["rainbow-row-sunset", "garden-gathering", "golden-hour"])
def test_each_module_gets_a_30_entry_frame_and_composition_is_deterministic(mode_id):
    frames = compose_canvas(_canvas(mode_id), canopy=CANOPY, recipes=RECIPES, placements=_placements())
    assert sorted(frames) == sorted(e.id for e in MODULES)
    assert all(len(f) == 30 and all(isinstance(x, FrameEntry) for x in f) for f in frames.values())
    again = compose_canvas(_canvas(mode_id), canopy=CANOPY, recipes=RECIPES, placements=_placements())
    assert again == frames
    for frame in frames.values():
        for e in frame:
            e.validated((2700, 6500))  # every entry is sendable


@pytest.mark.parametrize("axis", ["x", "y"])
def test_gradient_is_monotonic_along_its_axis(axis):
    canvas = _two_stop(axis)
    frames = compose_canvas(canvas, canopy=CANOPY, recipes=RECIPES, placements=_placements())
    placements = {p.device_id: p for p in _placements()}
    k = 0 if axis == "x" else 1
    points = sorted(
        (head_positions(CANOPY, placements[d])[i][k], e)
        for d, frame in frames.items()
        for i, e in enumerate(frame)
    )
    # hd-orange (249, 99, 2) → peach (255, 160, 110): every channel rises along the axis
    for (_, a), (_, b) in itertools.pairwise(points):
        assert a.r <= b.r and a.g <= b.g and a.b <= b.b
    first, last = points[0][1], points[-1][1]
    assert first.g < last.g
    # perpendicular to the axis nothing changes: heads at the same coordinate share a colour
    same = {}
    for coord, e in points:
        same.setdefault(coord, set()).add(e)
    assert all(len(v) == 1 for v in same.values())


def test_sunset_runs_orange_at_the_top_to_warm_white_at_the_bottom():
    canvas = _canvas("rainbow-row-sunset")
    top = evaluate(canvas, 100, 0, canopy=CANOPY, recipes=RECIPES)
    bottom = evaluate(canvas, 100, 120, canopy=CANOPY, recipes=RECIPES)
    assert top == FrameEntry(**RECIPES.colors["hd-orange"].blend.model_dump())
    assert bottom == FrameEntry(**RECIPES.colors["warm-white"].blend.model_dump())
    middle = evaluate(canvas, 100, 0.55 * 120, canopy=CANOPY, recipes=RECIPES)
    assert middle == FrameEntry(**RECIPES.colors["peach"].blend.model_dump())


def test_b_modules_map_through_their_rotated_positions():
    placements = {p.device_id: p for p in _placements()}
    a, b = placements["canopy-r0c0"], placements["canopy-r0c1"]
    assert (a.orientation, b.orientation) == ("A", "B")
    # wire index 0 sits at the A-frame (2.5, 5); on a B panel that is (75 - 2.5, 40 - 5), offset by one module
    assert head_positions(CANOPY, a)[0] == (2.5, 5.0)
    assert head_positions(CANOPY, b)[0] == (75 + 72.5, 35.0)
    canvas = _two_stop("x")
    frames = compose_canvas(canvas, canopy=CANOPY, recipes=RECIPES, placements=list(placements.values()))
    assert frames["canopy-r0c1"][0] == evaluate(canvas, 147.5, 35.0, canopy=CANOPY, recipes=RECIPES)
    # a B module's first wire index is at its right edge, so it is further along the x gradient than index 7
    assert frames["canopy-r0c1"][0].g > frames["canopy-r0c1"][7].g


def test_a_calibrated_wire_order_changes_which_frame_index_holds_which_colour():
    canvas = _two_stop("x")
    entry = MODULES[0]  # canopy-r0c0, orientation A
    plain = compose_canvas(canvas, canopy=CANOPY, recipes=RECIPES, placements=_placements([entry]))[entry.id]
    flipped_entry = entry.model_copy(update={"start_corner": "top-right"})
    flipped = compose_canvas(canvas, canopy=CANOPY, recipes=RECIPES, placements=_placements([flipped_entry]))[
        entry.id
    ]
    assert plain != flipped
    assert sorted(plain, key=lambda e: (e.r, e.g, e.b)) == sorted(flipped, key=lambda e: (e.r, e.g, e.b))
    # index 0 is now the top-right head (x = 72.5), which is further along the x gradient than x = 2.5
    assert flipped[0] == evaluate(canvas, 72.5, 5.0, canopy=CANOPY, recipes=RECIPES)
    assert plain[0] == evaluate(canvas, 2.5, 5.0, canopy=CANOPY, recipes=RECIPES)
    # an explicit calibrated wire_order wins: reverse the default order
    order = [list(p) for p in placement_for(CANOPY, entry, frame_length=30).order][::-1]
    reversed_entry = entry.model_copy(update={"wire_order": order})
    reversed_frame = compose_canvas(
        canvas, canopy=CANOPY, recipes=RECIPES, placements=_placements([reversed_entry])
    )[entry.id]
    assert reversed_frame == plain[::-1]


def test_head_count_guard_refuses_a_frame_of_the_wrong_length():
    canvas = _canvas("rainbow-row-sunset")
    with pytest.raises(PlanError, match="reports 29 heads but this module should have 30"):
        compose_canvas(canvas, canopy=CANOPY, recipes=RECIPES, placements=_placements(frame_length=29))
    with pytest.raises(PlanError, match="reports no frame length"):
        compose_canvas(canvas, canopy=CANOPY, recipes=RECIPES, placements=_placements(frame_length=None))
    sixty = MODULES[0].model_copy(update={"expected_heads": 60})
    with pytest.raises(PlanError, match="a panel holds 30"):
        compose_canvas(
            canvas, canopy=CANOPY, recipes=RECIPES, placements=_placements([sixty], frame_length=60)
        )
    twice = [placement_for(CANOPY, MODULES[0], frame_length=30)] * 2
    with pytest.raises(PlanError, match="both claim canopy position r0c0"):
        compose_canvas(canvas, canopy=CANOPY, recipes=RECIPES, placements=twice)


def test_pathway_is_bright_on_the_path_and_dim_on_the_base():
    canvas = _canvas("garden-gathering")
    assert isinstance(canvas, PathwayCanvas)
    on_path = evaluate(
        canvas, 0, 60, canopy=CANOPY, recipes=RECIPES
    )  # sin(0) = 0 → the centre line at y = 60
    off_path = evaluate(canvas, 0, 5, canopy=CANOPY, recipes=RECIPES)
    assert on_path == FrameEntry(0, 0, 0, 2700, 0, 100)  # warm-white blend at 100
    base = RECIPES.colors["deep-amber"].blend
    assert off_path == FrameEntry(
        base.r, base.g, base.b, 2700, 25, 5
    )  # deep amber at 25 % (colour and white)
    frames = compose_canvas(canvas, canopy=CANOPY, recipes=RECIPES, placements=_placements())
    lit = sum(e.whiteBrightness >= 50 for f in frames.values() for e in f)
    assert 20 <= lit <= 120  # a band, not a flood and not nothing


def test_band_per_module_repeats_the_same_band_in_every_module_column():
    canvas = _canvas("golden-hour")
    assert isinstance(canvas, BandCanvas)
    for y in (5, 15, 55):
        row = [evaluate(canvas, c * 75 + 37.5, y, canopy=CANOPY, recipes=RECIPES) for c in range(3)]
        assert row[0] == row[1] == row[2]
    centre = evaluate(canvas, 37.5, 15, canopy=CANOPY, recipes=RECIPES)
    edge = evaluate(canvas, 2.5, 5, canopy=CANOPY, recipes=RECIPES)
    band = RECIPES.colors["amber"].blend
    assert centre == FrameEntry(band.r, band.g, band.b, 2700, 100, band.whiteBrightness)
    base = RECIPES.colors["deep-amber"].blend
    assert edge == FrameEntry(base.r, base.g, base.b, 2700, 60, round(base.whiteBrightness * 0.6))


def test_a_bench_kit_placed_at_a_canopy_position_samples_that_slice():
    """The module page's preview: the bench kit's own (A, uncalibrated) heads, offset into position r1c2."""
    bench = load_registry().get("bench-kit")
    p = placement_for(CANOPY, bench, frame_length=30, module="r1c2")
    assert (p.row, p.col, p.orientation) == (1, 2, "A")
    assert head_positions(CANOPY, p)[0] == (150 + 2.5, 40 + 5.0)
    with pytest.raises(PlanError, match="outside the canopy"):
        placement_for(CANOPY, bench, frame_length=30, module="r3c0")
    with pytest.raises(PlanError, match="isn't a canopy position"):
        placement_for(CANOPY, bench, frame_length=30, module="middle")
