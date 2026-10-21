"""Calibrated wire orders (geometry.panel_order), the module map (module/layout.py) and the card/paint compilers
(module/cards.py) — pure functions, no app."""

from __future__ import annotations

import itertools
from math import dist

import pytest

from conductor.capabilities import detect
from conductor.config import load_recipes
from conductor.geometry import (
    CABLE_PATHS,
    START_CORNERS,
    load_canopy_config,
    longest_hop,
    panel_lattice,
    panel_order,
    panel_positions,
)
from conductor.module import cards as C
from conductor.module.layout import (
    Calibration,
    default_orientation,
    display_positions,
    module_map,
    shown_corner,
    validate_calibration,
)
from conductor.rows import FrameEntry, PayloadError
from conductor.sim.profiles import profile_parts

CFG = load_canopy_config()
SPACING = CFG["puck_spacing_in"]
CORNER_POINTS = {
    "top-left": (0.0, 0.0),
    "top-right": (CFG["module"]["width_in"], 0.0),
    "bottom-left": (0.0, CFG["module"]["height_in"]),
    "bottom-right": (CFG["module"]["width_in"], CFG["module"]["height_in"]),
}
CAPS = detect(*profile_parts("sqhdk03-sim"))
RECIPES = load_recipes()


# ---- geometry.panel_order ----------------------------------------------------------------------------------------
@pytest.mark.parametrize(("path", "corner"), list(itertools.product(CABLE_PATHS, START_CORNERS)))
def test_every_calibrated_order_is_a_permutation_whose_hops_fit_the_puck_spacing(path, corner):
    order = panel_order(CFG, path, corner)
    assert len(order) == 30
    assert sorted(order) == sorted(panel_lattice(CFG))  # every lattice point exactly once
    assert longest_hop(order) <= SPACING  # the fixed 19.7" cable between pucks reaches every hop
    # index 0 is the head nearest the chosen start corner
    corner_xy = CORNER_POINTS[corner]
    assert dist(order[0], corner_xy) == min(dist(p, corner_xy) for p in order)
    # reversing ("flip") is still a valid calibrated order
    assert panel_order(CFG, wire_order=order[::-1]) == order[::-1]


def test_rows_from_top_left_is_the_existing_default():
    assert panel_order(CFG) == panel_positions(CFG)
    assert panel_order(CFG, "serpentine_rows", "top-left") == panel_positions(CFG)


def test_column_serpentine_walks_diagonal_pairs_and_reroutes_the_short_last_column():
    order = panel_order(CFG, "serpentine_cols", "top-left")
    assert order[:4] == [(2.5, 5.0), (7.5, 15.0), (2.5, 25.0), (7.5, 35.0)]  # down the first column pair
    assert order[4:8] == [(17.5, 35.0), (12.5, 25.0), (17.5, 15.0), (12.5, 5.0)]  # and back up the next
    # the two x=72.5 heads are 20" apart (longer than the cable), so they are never consecutive
    i, j = order.index((72.5, 5.0)), order.index((72.5, 25.0))
    assert abs(i - j) > 1


def test_where_the_path_cannot_fit_the_plain_serpentine_is_returned():
    tight = {**CFG, "puck_spacing_in": 10.0}  # only row neighbours reachable: no full path exists by columns
    order = panel_order(tight, "serpentine_cols", "top-left")
    assert sorted(order) == sorted(panel_lattice(CFG)) and longest_hop(order) > 10.0


def test_explicit_wire_order_must_cover_every_head_once():
    with pytest.raises(ValueError, match="exactly once"):
        panel_order(CFG, wire_order=panel_positions(CFG)[:-1])
    with pytest.raises(ValueError):
        panel_order(CFG, "zigzag", "top-left")
    with pytest.raises(ValueError):
        panel_order(CFG, "serpentine_rows", "middle")


# ---- module map --------------------------------------------------------------------------------------------------
def test_map_in_orientation_a_and_b():
    a = module_map(CFG, Calibration())
    assert [h["index"] for h in a["heads"]] == list(range(30))
    rows = [sum(1 for h in a["heads"] if h["row"] == r) for r in range(4)]
    assert rows == [8, 7, 8, 7]
    assert a["polyline"][0] == [2.5, 5.0] and a["hops_fit"] is True and a["longest_hop_in"] < SPACING
    b = module_map(CFG, Calibration(orientation="B"))
    for ha, hb in zip(a["heads"], b["heads"], strict=True):
        assert (hb["x"], hb["y"]) == (75 - ha["x"], 40 - ha["y"])  # B = A rotated 180°
        assert (hb["ax"], hb["ay"]) == (ha["ax"], ha["ay"])  # calibration stays in the A frame
    assert [sum(1 for h in b["heads"] if h["row"] == r) for r in range(4)] == [7, 8, 7, 8]
    assert b["calibration"]["start_corner_as_shown"] == "bottom-right"


def test_shown_corner_is_its_own_inverse_and_validation_is_plain():
    for corner in START_CORNERS:
        assert shown_corner(shown_corner(corner, "B"), "B") == corner
        assert shown_corner(corner, "A") == corner
    with pytest.raises(ValueError, match="A or B"):
        validate_calibration(CFG, orientation="C", cable_path="serpentine_rows", start_corner="top-left")
    cal = validate_calibration(
        CFG, orientation="A", cable_path="serpentine_rows", start_corner="top-left", wire_order=None
    )
    assert cal.calibrated and cal.wire_order is None


def test_canopy_modules_take_their_orientation_from_the_scheme():
    assert default_orientation(CFG, "r0c1") == "B" and default_orientation(CFG, "r2c0") == "A"
    assert default_orientation(CFG, None) == "A"


# ---- cards and paint helpers --------------------------------------------------------------------------------------
POSITIONS = display_positions(module_map(CFG, Calibration()))


def _compile(card, **params):
    return C.compile_card(card, params, recipes=RECIPES, caps=CAPS, positions=POSITIONS, width_in=75.0)


def test_white_cards_clamp_to_the_device_range_with_a_note():
    assert _compile("white", kelvin=3000).look.cct == 3000
    low = _compile("white", kelvin=2200)
    assert low.look.cct == 2700 and "2700–6500" in low.notes[0]
    assert _compile("white", kelvin=9000).look.cct == 6500


def test_color_cards_use_the_recipes_file():
    strand = _compile("color", color="hd-orange")
    assert strand.look.kind == "rgb" and strand.look.rgb == (249, 99, 2)
    ivory = _compile("color", color="warm-ivory")
    assert ivory.look.kind == "white" and ivory.look.cct == 2700
    painted = _compile("color", color="amber", painted=True, recipe="rgb_only")
    assert painted.frame == [FrameEntry(**RECIPES.colors["amber"].rgb_only.model_dump())] * 30
    with pytest.raises(PayloadError):
        _compile("color", color="chartreuse")
    with pytest.raises(PayloadError):
        _compile("color", color="amber", painted=True, recipe="neon")


def test_thirds_are_three_bands_across_x():
    bands = [
        {"color": "hd-orange", "recipe": "rgb_only", "brightness": 100},
        {"color": "peach", "recipe": "rgb_only", "brightness": 50},
        {"color": "amber", "recipe": "rgb_only", "brightness": 20},
    ]
    frame = _compile("thirds", bands=bands).frame
    for (x, _), entry in zip(POSITIONS, frame, strict=True):
        band = 0 if x < 25 else 1 if x < 50 else 2
        want = C.scaled(
            FrameEntry(**RECIPES.colors[bands[band]["color"]].rgb_only.model_dump()), [100, 50, 20][band]
        )
        assert entry == want
    with pytest.raises(PayloadError):
        _compile("thirds", bands=bands[:2])


def test_hue_ramp_and_single_head():
    ramp = _compile("hue-ramp").frame
    assert (ramp[0].r, ramp[0].g, ramp[0].b) == (255, 0, 0)
    assert (ramp[15].r, ramp[15].g, ramp[15].b) == (0, 255, 255)  # 180°
    single = _compile("single-head", index=4).frame
    assert [i for i, e in enumerate(single) if e != C.OFF] == [4]
    with pytest.raises(PayloadError):
        _compile("single-head", index=30)


def test_gradient_and_mix():
    red, blue = FrameEntry(255, 0, 0), FrameEntry(0, 0, 255)
    frame = C.gradient([C.OFF] * 30, POSITIONS, [], red, blue, "x")
    xs = [x for x, _ in POSITIONS]
    assert frame[xs.index(min(xs))] == red and frame[xs.index(max(xs))] == blue
    reds = [e.r for _, e in sorted(zip(xs, frame, strict=True))]
    assert reds == sorted(reds, reverse=True)
    # only the selection changes
    part = C.gradient([C.OFF] * 30, POSITIONS, [0, 1], red, blue, "x")
    assert part[2:] == [C.OFF] * 28
    # a white-only stop fades its white brightness instead of inventing a Kelvin value
    warm = FrameEntry(0, 0, 0, 2700, 0, 100)
    mid = C.mix(red, warm, 0.5)
    assert mid.cct == 2700 and mid.whiteBrightness == 50 and mid.r == 128
    with pytest.raises(PayloadError):
        C.gradient([C.OFF] * 30, POSITIONS, [], red, blue, "z")


def test_catalog_lists_every_card_and_marks_t1_and_the_lowcountry_preview():
    cards = {c["id"]: c for c in C.catalog(RECIPES)}
    assert cards["lowcountry-preview"]["placeholder"] is True  # without the modes it is shown as unavailable
    assert not cards["white"]["needs_t1"] and cards["hue-ramp"]["needs_t1"]
    with pytest.raises(PayloadError, match="modes"):
        _compile("lowcountry-preview")  # runs through the module session, which has the compiler
    from conductor.geometry import load_canopy_config
    from conductor.looks import load_modes

    preview = {c["id"]: c for c in C.catalog(RECIPES, load_modes(), load_canopy_config())}[
        "lowcountry-preview"
    ]
    assert "placeholder" not in preview and not preview["needs_t1"]
    assert preview["defaults"] == {"mode": "lowcountry-calm", "position": "r1c1"}
    assert [m["id"] for m in preview["options"]["modes"]] == list(load_modes().modes)  # catalog order (D77)
    assert [m["id"] for m in preview["options"]["modes"]][
        -1
    ] == "light-show-6"  # the Light Show scene follows the six
