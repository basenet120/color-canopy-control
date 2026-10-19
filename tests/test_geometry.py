import itertools
from collections import Counter, defaultdict

import pytest

from conductor.geometry import (
    build_layout,
    lattice_heads,
    load_canopy_config,
    longest_hop,
    panel_positions,
    pitch_violations,
)

CFG = load_canopy_config()


def test_nine_modules_of_thirty_heads_in_8_7_8_7_rows():
    heads = build_layout(CFG)
    assert len(heads) == 270
    by_module = defaultdict(list)
    for h in heads:
        by_module[h.module].append(h.index)
    assert len(by_module) == 9
    assert all(sorted(indexes) == list(range(30)) for indexes in by_module.values())
    rows = Counter(y for _, y in panel_positions(CFG))
    assert [rows[y] for y in sorted(rows)] == [8, 7, 8, 7]


def test_heads_are_unique_and_inside_the_canopy():
    heads = build_layout(CFG)
    assert len({(h.x, h.y) for h in heads}) == 270
    assert all(0 < h.x < CFG["width_in"] and 0 < h.y < CFG["height_in"] for h in heads)


def test_p14_scheme_keeps_10in_pitch_and_the_stagger_across_every_seam():
    heads = build_layout(CFG, "p14")
    assert pitch_violations(heads, 10) == []
    ys = sorted({h.y for h in heads})
    assert all(b - a == 10 for a, b in itertools.pairwise(ys))
    offsets = [{h.x % 10 for h in heads if h.y == y} for y in ys]
    assert all(len(o) == 1 for o in offsets)
    assert all(a != b for a, b in itertools.pairwise(offsets))


def test_p18_appendix_scheme_breaks_the_pitch_at_module_seams():
    assert pitch_violations(build_layout(CFG, "p18"), 10)


def test_b_module_is_module_a_rotated_180_degrees_including_wire_order():
    heads = build_layout(CFG, "p14")
    a = sorted((h for h in heads if h.module == "r0c0"), key=lambda h: h.index)
    b = sorted((h for h in heads if h.module == "r0c1"), key=lambda h: h.index)
    assert [h.orientation for h in (a[0], b[0])] == ["A", "B"]
    for ha, hb in zip(a, b):
        assert (hb.x - 75, hb.y) == (75 - ha.x, 40 - ha.y)


def test_default_wiring_fits_the_fixed_puck_spacing():
    assert longest_hop(panel_positions(CFG)) < CFG["puck_spacing_in"]


def test_same_x_heads_are_too_far_apart_to_be_neighbours_on_the_wire():
    column = [(2.5, 5.0), (2.5, 25.0)]
    assert longest_hop(column) > CFG["puck_spacing_in"]


def test_calibrated_wire_order_must_cover_every_head_exactly_once():
    order = panel_positions(CFG)
    assert panel_positions({**CFG, "wire_order": order[::-1]}) == order[::-1]
    with pytest.raises(ValueError):
        panel_positions({**CFG, "wire_order": order[:-1]})


def test_unknown_scheme_is_rejected():
    with pytest.raises(ValueError):
        build_layout(CFG, "p99")


@pytest.mark.parametrize("scheme", ["p14", "p18"])
def test_lattice_heads_are_the_layouts_heads_for_every_scheme(scheme):
    """The fin map's head table (D74) uses lattice_heads, which doesn't depend on the wiring: the same points."""
    heads = lattice_heads({**CFG, "orientation_scheme": scheme})
    assert len(heads) == 270
    assert set(heads) == {(h.x, h.y) for h in build_layout(CFG, scheme)}
