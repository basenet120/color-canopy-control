"""The 2-D design field (conductor/looks/field.py): t over canopy inches for linear, radial and sweep designs."""

from __future__ import annotations

import math

import pytest
from pydantic import TypeAdapter, ValidationError

from conductor.config import load_registry
from conductor.geometry import load_canopy_config
from conductor.looks import canvas_heads, placement_for, positions
from conductor.looks import field as F

CANOPY = load_canopy_config()
NINE = positions(CANOPY)
CENTRES = F.module_centres(CANOPY, NINE)
HEADS = canvas_heads(
    CANOPY, [placement_for(CANOPY, e, frame_length=30) for e in load_registry().canopy_modules()]
)
SPEC = TypeAdapter(F.FieldSpec)
EDGE_MIDDLES = ["r0c1", "r1c0", "r1c2", "r2c1"]
CORNERS = ["r0c0", "r0c2", "r2c0", "r2c2"]


def _spec(**data):
    return SPEC.validate_python(data)


def _at(spec, pos, phase=0.0):
    x, y = CENTRES[pos]
    return F.value(spec, x, y, CANOPY, phase)


def test_module_centres_are_the_middle_of_each_75_by_40_module():
    assert CENTRES["r0c0"] == (37.5, 20.0)
    assert CENTRES["r1c1"] == (112.5, 60.0)
    assert CENTRES["r2c2"] == (187.5, 100.0)
    assert len(CENTRES) == 9 and len(HEADS) == 270


def test_linear_0_is_x_over_225_and_90_is_y_over_120_at_every_head():
    along_x, along_y = _spec(kind="linear", angle_deg=0), _spec(kind="linear", angle_deg=90)
    for h in HEADS:
        assert F.value(along_x, h.x, h.y, CANOPY) == h.x / 225
        assert F.value(along_y, h.x, h.y, CANOPY) == h.y / 120
    back = _spec(kind="linear", angle_deg=180)
    assert F.value(back, 0, 0, CANOPY) == 1.0 and F.value(back, 225, 0, CANOPY) == 0.0
    # the canopy's corners project onto [0, 1] at any angle
    for angle in (0, 30, 45, 90, 135, 200, 300):
        spec = _spec(kind="linear", angle_deg=angle)
        ts = [F.value(spec, x, y, CANOPY) for x in (0, 225) for y in (0, 120)]
        assert min(ts) == pytest.approx(0.0) and max(ts) == pytest.approx(1.0)


def test_45_degrees_runs_corner_to_corner_with_five_values_at_the_module_centres():
    spec = _spec(kind="linear", angle_deg=45)
    values = {round(_at(spec, p), 9) for p in NINE}
    assert len(values) == 5
    # the anti-diagonals share a colour; the top-left corner is lowest, the bottom-right highest
    assert _at(spec, "r0c1") == pytest.approx(_at(spec, "r1c0"))
    assert _at(spec, "r0c0") < _at(spec, "r1c1") < _at(spec, "r2c2")
    assert F.value(spec, 0, 0, CANOPY) == pytest.approx(0) and F.value(
        spec, 225, 120, CANOPY
    ) == pytest.approx(1)


def test_radial_in_inches_from_the_canopy_centre():
    spec = _spec(kind="radial", centre_in=[112.5, 60])
    assert F.radius(spec, CANOPY) == 127.5  # the farthest corner: hypot(112.5, 60)
    distances = {p: round(_at(spec, p) * 127.5, 9) for p in NINE}
    assert distances["r1c1"] == 0
    assert {distances[p] for p in ["r0c1", "r2c1"]} == {40}
    assert {distances[p] for p in ["r1c0", "r1c2"]} == {75}
    assert {distances[p] for p in CORNERS} == {85}
    assert F.value(spec, 0, 0, CANOPY) == 1.0
    corner = _spec(kind="radial", centre_in=[0, 0])
    assert F.radius(corner, CANOPY) == 255.0  # from a corner, auto reaches the opposite one: hypot(225, 120)
    assert F.value(corner, 225, 120, CANOPY) == 1.0 and F.value(corner, 225, 0, CANOPY) == 225 / 255
    fixed = _spec(kind="radial", centre_in=[112.5, 60], radius_in=80)
    assert _at(fixed, "r0c1") == 0.5
    assert _at(fixed, "r0c0") == 1.0  # beyond the radius it clamps (no repeat asked for)


def test_canopy_metric_makes_the_edge_middles_tie():
    spec = _spec(kind="radial", centre_in=[112.5, 60], metric="canopy")
    assert len({_at(spec, p) for p in EDGE_MIDDLES}) == 1
    assert len({_at(spec, p) for p in CORNERS}) == 1
    assert 0 == _at(spec, "r1c1") < _at(spec, "r0c1") < _at(spec, "r0c0") < 1
    assert F.value(spec, 0, 0, CANOPY) == 1.0  # auto radius: the farthest corner in the canopy's proportions


def test_the_nearest_of_several_centres_wins():
    spec = _spec(kind="radial", centres_in=[[37.5, 20], [187.5, 100]], radius_in=100)
    assert _at(spec, "r0c0") == 0 and _at(spec, "r2c2") == 0
    assert _at(spec, "r0c1") == pytest.approx(0.75)  # 75 in from r0c0's centre, much farther from r2c2's
    assert _at(spec, "r2c1") == pytest.approx(0.75)
    one = _spec(kind="radial", centre_in=[37.5, 20], radius_in=100)
    assert _at(one, "r2c2") == 1.0


def test_sweep_goes_round_the_centre_from_its_start_angle():
    spec = _spec(kind="sweep", centre_in=[112.5, 60])
    assert _at(spec, "r1c2") == 0.0  # straight along +x
    assert _at(spec, "r2c1") == 0.25  # a quarter turn (y runs down the canopy)
    assert _at(spec, "r1c0") == 0.5
    assert _at(spec, "r0c1") == 0.75
    turned = _spec(kind="sweep", centre_in=[112.5, 60], start_deg=90)
    assert _at(turned, "r2c1") == 0.0 and _at(turned, "r1c2") == 0.75


def test_mirror_invert_and_cycles():
    plain = _spec(kind="linear")
    mirror = _spec(kind="linear", transform="mirror")
    invert = _spec(kind="linear", invert=True)
    both = _spec(kind="linear", transform="mirror", invert=True)
    twice = _spec(kind="linear", cycles=2)
    for x in (0, 30, 56.25, 112.5, 150, 200, 225):
        t = x / 225
        assert F.value(plain, x, 7, CANOPY) == t
        assert F.value(mirror, x, 7, CANOPY) == pytest.approx(1 - abs(2 * t - 1))
        assert F.value(invert, x, 7, CANOPY) == pytest.approx(1 - t)
        assert F.value(both, x, 7, CANOPY) == pytest.approx(abs(2 * t - 1))
    # cycles: a sawtooth that wraps (and only then; the default clamps like the gradient canvas)
    assert F.value(twice, 56.25, 0, CANOPY) == pytest.approx(0.5)
    assert F.value(twice, 112.5, 0, CANOPY) == pytest.approx(0.0)
    assert F.value(twice, 168.75, 0, CANOPY) == pytest.approx(0.5)
    assert F.value(twice, 225, 0, CANOPY) == pytest.approx(0.0)
    rings = _spec(kind="radial", centre_in=[112.5, 60], radius_in=80, cycles=2)
    assert _at(rings, "r0c1") == 0.0 and _at(rings, "r1c0") == pytest.approx(0.875)
    # a runtime phase shifts (and wraps) even a single cycle; no data can set it
    assert F.value(plain, 0, 0, CANOPY, phase=0.25) == 0.25
    assert F.value(plain, 225, 0, CANOPY, phase=0.25) == 0.25
    with pytest.raises(ValidationError):
        _spec(kind="linear", phase=0.5)


def test_bands_group_modules_by_the_field_at_their_centres():
    canopy_rings = _spec(kind="radial", centre_in=[112.5, 60], metric="canopy")
    assert F.bands(canopy_rings, CANOPY, NINE) == [["r1c1"], EDGE_MIDDLES, CORNERS]
    assert F.bands(canopy_rings, CANOPY, ["r0c0", "r0c1"]) == [["r0c1"], ["r0c0"]]
    # in true inches the near edge-middles (40 in) and the far ones (75 in) fall in different bins
    inches = _spec(kind="radial", centre_in=[112.5, 60])
    assert F.bands(inches, CANOPY, NINE) == [["r0c1", "r1c1", "r2c1"], ["r1c0", "r1c2"], CORNERS]
    assert F.bands(inches, CANOPY, ["r0c0", "r0c1"]) == [["r0c1"], ["r0c0"]]
    columns = _spec(kind="linear")
    assert F.bands(columns, CANOPY, NINE) == [
        ["r0c0", "r1c0", "r2c0"],
        ["r0c1", "r1c1", "r2c1"],
        ["r0c2", "r1c2", "r2c2"],
    ]
    assert F.bands(columns, CANOPY, NINE, max_bands=1) == [NINE]
    # a centre exactly on a bin edge stays with its equals even when float maths lands a hair below it: the
    # anti-diagonal (t = 0.5 at r0c2, r1c1, r2c0) is one band both ways round
    anti = ["r0c2", "r1c1", "r2c0"]
    assert F.bands(_spec(kind="linear", angle_deg=45), CANOPY, NINE, max_bands=2) == [
        ["r0c0", "r0c1", "r1c0"],
        ["r0c2", "r1c1", "r1c2", "r2c0", "r2c1", "r2c2"],
    ]
    assert F.bands(_spec(kind="linear", angle_deg=225), CANOPY, anti, max_bands=2) == [anti]


def test_layout_words():
    def words(**data):
        return F.layout_words(_spec(**data), CANOPY)

    assert words(kind="linear") == "column by column"
    assert words(kind="linear", angle_deg=180) == "column by column"
    assert words(kind="linear", angle_deg=90) == "row by row"
    assert words(kind="linear", angle_deg=270) == "row by row"
    assert words(kind="linear", angle_deg=45) == "diagonally"
    # a radial design is only "from the centre out" when it is centred on the canopy (rings or not)
    assert words(kind="radial", centre_in=[112.5, 60]) == "from the centre out"
    assert words(kind="radial", centre_in=[112.5, 60], cycles=3, invert=True) == "from the centre out"
    # the D63 drafts: Garden Gathering from a long edge, Golden Hour's sun at the left edge, a moon over r0c2
    assert words(kind="radial", centre_in=[112.5, 120]) == "from one edge"
    assert words(kind="radial", centre_in=[0, 60]) == "from one edge"
    assert words(kind="radial", centre_in=[-40, 60]) == "from one edge"  # beyond the edge it rises from
    assert words(kind="radial", centre_in=[187.5, 20]) == "from one spot"
    assert words(kind="radial", centre_in=[112.5, 59]) == "from one spot"
    assert words(kind="radial", centre_in=[0, 0]) == "from one corner"
    assert words(kind="radial", centre_in=[260, -5]) == "from one corner"
    assert words(kind="radial", centres_in=[[0, 0], [225, 120]]) == "from two points"
    assert words(kind="radial", centres_in=[[0, 0], [225, 0], [0, 120], [225, 120]]) == "from four points"
    assert words(kind="radial", centres_in=[[112.5, 60], [112.5, 60]]) == "from the centre out"
    assert words(kind="sweep", centre_in=[112.5, 60]) == "around the centre"
    assert words(kind="sweep", centre_in=[0, 0]) == "around one corner"
    assert words(kind="sweep", centre_in=[30, 50]) == "around one spot"
    for spec in (
        _spec(kind="radial", centre_in=[5, 5]),
        _spec(kind="radial", centres_in=[[1, 2], [3, 4], [5, 6]]),
        _spec(kind="sweep", centre_in=[225, 7]),
        _spec(kind="linear", angle_deg=10),
    ):
        assert F.layout_words(spec, CANOPY) in F.LAYOUT_PHRASES


def _dense_max(spec) -> float:
    """The largest raw t over a 2.5-inch grid of the whole canopy (a brute-force check on the auto radius)."""
    return max(F._raw(spec, x * 2.5, y * 2.5, CANOPY) for x in range(91) for y in range(49))


def test_auto_radius_with_several_centres_is_the_farthest_canopy_point_from_its_nearest_centre():
    # two centres at the short edges' middles: the corners are only 60 in from one, but the long edges' middles are
    # 127.5 in from both (where their regions meet), so that is the radius and raw t never passes 1
    two = _spec(kind="radial", centres_in=[[0, 60], [225, 60]])
    assert F.radius(two, CANOPY) == pytest.approx(127.5)
    assert F.value(two, 112.5, 0, CANOPY) == pytest.approx(1.0)
    assert max(F._raw(two, h.x, h.y, CANOPY) for h in HEADS) <= 1 + 1e-12
    rings = _spec(kind="radial", centres_in=[[0, 60], [225, 60]], cycles=2)
    assert F._raw(rings, 112.5, 0, CANOPY) == pytest.approx(1.0)  # the outer ring, not 2.125 radii out
    # one centre per corner: the farthest point is the canopy's centre, not 0 (no division by zero)
    corners = _spec(kind="radial", centres_in=[[0, 0], [225, 0], [0, 120], [225, 120]])
    assert F.radius(corners, CANOPY) == pytest.approx(127.5)
    assert F.value(corners, 112.5, 60, CANOPY) == pytest.approx(1.0)
    # not measured from the first centre only: the far corner is 255 in from (0, 0) but much nearer the second
    assert F.radius(_spec(kind="radial", centres_in=[[0, 0], [187.5, 100]]), CANOPY) < 200
    for spec in (
        two,
        corners,
        _spec(kind="radial", centres_in=[[0, 0], [187.5, 100]]),
        _spec(kind="radial", centres_in=[[37.5, 20], [187.5, 100], [187.5, 20]]),
        _spec(kind="radial", centres_in=[[10, 110], [200, 15], [90, 70], [150, 100]]),
        _spec(kind="radial", centres_in=[[-30, 60], [300, 60]]),
        _spec(kind="radial", centres_in=[[0, 60], [225, 60]], metric="canopy"),
        _spec(kind="radial", centre_in=[112.5, 60]),
    ):
        top = _dense_max(spec)
        assert 0.98 <= top <= 1 + 1e-12


def test_a_canopy_with_nothing_to_fill_refuses_auto_plainly():
    point = {"width_in": 0, "height_in": 0}
    with pytest.raises(ValueError, match="radius_in: auto has no distance to fill"):
        F.radius(_spec(kind="radial", centre_in=[0, 0]), point)


def test_the_field_is_deterministic():
    specs = [
        _spec(kind="linear", angle_deg=33, cycles=3, transform="mirror"),
        _spec(kind="radial", centres_in=[[10, 10], [200, 90]], invert=True),
        _spec(kind="sweep", centre_in=[100, 50], start_deg=12.5),
    ]
    first = [[F.value(s, h.x, h.y, CANOPY, 0.3) for h in HEADS] for s in specs]
    again = [[F.value(s, h.x, h.y, CANOPY, 0.3) for h in HEADS] for s in specs]
    assert first == again
    assert all(0 <= t <= 1 and not math.isnan(t) for row in first for t in row)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"kind": "spiral"}, "does not match any of the expected tags: 'linear', 'radial', 'sweep'"),
        ({"kind": "linear", "cycles": 0}, "cycles must be a whole number from 1 to 6"),
        ({"kind": "linear", "cycles": 7}, "cycles must be a whole number from 1 to 6"),
        ({"kind": "linear", "angle_deg": 360}, "angle_deg must be at least 0 and less than 360"),
        ({"kind": "radial", "centre_in": [0, 0], "radius_in": 0}, "radius_in must be auto or a distance"),
        ({"kind": "radial", "centre_in": [0, 0], "radius_in": -5}, "radius_in must be auto or a distance"),
        (
            {"kind": "radial", "centre_in": [0, 0], "radius_in": math.inf},
            "radius_in must be auto or a distance",
        ),
        (
            {"kind": "radial", "centre_in": [0, 0], "radius_in": math.nan},
            "radius_in must be auto or a distance",
        ),
        ({"kind": "radial", "centre_in": [0, 0], "radius_in": True}, "radius_in must be auto or a distance"),
        ({"kind": "radial", "centre_in": [math.inf, 0]}, "centre_in must be \\[x, y\\] in canopy inches"),
        ({"kind": "radial", "centres_in": [[0, 0], [0, math.nan]]}, "each of centres_in must be"),
        ({"kind": "sweep", "centre_in": [0, -math.inf]}, "centre_in must be \\[x, y\\] in canopy inches"),
        ({"kind": "radial"}, "give exactly one of centre_in"),
        ({"kind": "radial", "centre_in": [0, 0], "centres_in": [[1, 1]]}, "give exactly one of centre_in"),
        ({"kind": "radial", "centres_in": [[0, 0]] * 5}, "at most 4"),
        ({"kind": "sweep", "centre_in": [0, 0], "start_deg": -1}, "start_deg must be at least 0"),
    ],
)
def test_bad_specs_are_refused_with_plain_messages(data, message):
    with pytest.raises(ValidationError, match=message):
        _spec(**data)
