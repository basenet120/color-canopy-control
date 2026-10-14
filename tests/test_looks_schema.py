"""config/modes.yaml as validated data: the real file loads; bad files give errors a technician can act on."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
import yaml

from conductor.config import CONFIG_DIR, ConfigError, load_recipes
from conductor.geometry import load_canopy_config
from conductor.looks import load_modes, parse_modes
from conductor.looks.schema import MOTION_WORDS, FieldCanvas

RAW = yaml.safe_load((CONFIG_DIR / "modes.yaml").read_text())
RECIPES = load_recipes()
CANOPY = load_canopy_config()


def _parse(data):
    return parse_modes(data, recipes=RECIPES, canopy=CANOPY, path="config/modes.yaml")


def _bad(mutate) -> str:
    data = copy.deepcopy(RAW)
    mutate(data)
    with pytest.raises(ConfigError) as e:
        _parse(data)
    return str(e.value)


def test_the_real_modes_file_loads_with_six_modes_and_unique_slots():
    modes = load_modes()
    assert list(modes.modes) == [
        "lowcountry-calm",
        "rainbow-row-sunset",
        "garden-gathering",
        "coastal-tides",
        "golden-hour",
        "goodnight-glow",
    ]
    slots = [m.slot for m in modes.modes.values() if m.slot]
    assert sorted(slots) == ["custom-1", "custom-2", "custom-3"]
    assert modes.kiosk.title == "The Color Canopy"
    sunset = modes.mode("rainbow-row-sunset")
    assert isinstance(sunset.techniques.canvas.canvas, FieldCanvas)  # a 2-D design since D63
    assert [s.color for s in sunset.techniques.canvas.canvas.stops] == ["pastel-coral", "blush-pink", "lilac"]
    assert sunset.techniques.modules.sample.canvas == sunset.techniques.canvas.canvas  # one design, two tiers
    # every scene is static (D59): no mode carries motion, and no copy claims movement
    for mode in modes.modes.values():
        assert mode.motion is False
        if mode.techniques.canvas is not None:
            assert (
                mode.techniques.canvas.motion_effect is None and mode.techniques.canvas.motion_feature is None
            )
        for name in mode.ladder:
            assert not MOTION_WORDS.search(getattr(mode.techniques, name).feature)


def test_load_modes_reads_a_config_dir(tmp_path):
    for name in ("modes.yaml", "recipes.yaml", "canopy.yaml"):
        shutil.copy(CONFIG_DIR / name, tmp_path / name)
    assert len(load_modes(tmp_path).modes) == 6
    (tmp_path / "modes.yaml").unlink()
    with pytest.raises(ConfigError, match="modes.yaml is missing"):
        load_modes(tmp_path)


def test_unknown_colour_names_the_mode_the_field_and_the_known_colours():
    msg = _bad(
        lambda d: d["modes"]["rainbow-row-sunset"]["techniques"]["canvas"]["canvas"]["stops"][1].update(
            color="salmon"
        )
    )
    assert "mode 'rainbow-row-sunset'" in msg and "stops → [1] → color" in msg
    assert "unknown colour 'salmon'" in msg and "hd-orange" in msg and "recipes.yaml" in msg


def test_two_modes_sharing_a_slot_is_refused():
    msg = _bad(lambda d: d["modes"]["garden-gathering"].update(slot="custom-1"))
    assert "mode 'garden-gathering' → slot" in msg and "already used by mode 'rainbow-row-sunset'" in msg


def test_a_slot_outside_custom_1_to_3_is_refused():
    msg = _bad(lambda d: d["modes"]["golden-hour"].update(slot="custom-4"))
    assert "mode 'golden-hour' → slot" in msg


def test_every_ladder_entry_needs_its_technique():
    msg = _bad(lambda d: d["modes"]["garden-gathering"]["techniques"].pop("modules"))
    assert "mode 'garden-gathering'" in msg and "ladder lists 'modules'" in msg


def test_a_canvas_tier_needs_a_slot():
    msg = _bad(lambda d: d["modes"]["garden-gathering"].pop("slot"))
    assert "mode 'garden-gathering'" in msg and "needs slot" in msg


def test_static_copy_that_claims_movement_is_refused():
    msg = _bad(
        lambda d: d["modes"]["golden-hour"]["techniques"]["canvas"].update(
            feature="An amber wave moving across nine Hubspace strands."
        )
    )
    assert "mode 'golden-hour'" in msg and "mentions movement" in msg and "motion_feature" in msg
    blurb = _bad(lambda d: d["modes"]["golden-hour"].update(blurb="Amber light moving across the canopy."))
    assert "blurb mentions movement" in blurb


def test_a_motion_effect_needs_motion_copy():
    msg = _bad(
        lambda d: d["modes"]["golden-hour"]["techniques"]["canvas"].update(motion_effect={"motionEffect": 1})
    )
    assert "mode 'golden-hour' → techniques → canvas" in msg and "motion_feature" in msg


def test_out_of_range_values_and_typos_name_the_mode_and_field():
    msg = _bad(lambda d: d["modes"]["lowcountry-calm"]["techniques"]["strand"]["look"].update(brightness=150))
    assert "mode 'lowcountry-calm' → techniques → strand → look → brightness" in msg
    typo = _bad(lambda d: d["modes"]["goodnight-glow"]["techniques"]["strand"]["look"].update(ctt=2200))
    assert "mode 'goodnight-glow'" in typo and "ctt" in typo
    kind = _bad(
        lambda d: d["modes"]["garden-gathering"]["techniques"]["canvas"]["canvas"].update(kind="spiral")
    )
    assert "mode 'garden-gathering' → techniques → canvas → canvas" in kind
    stops = _bad(
        lambda d: d["modes"]["rainbow-row-sunset"]["techniques"]["canvas"]["canvas"]["stops"].reverse()
    )
    assert "increasing" in stops


def test_white_look_needs_a_colour_temperature_and_preset_needs_a_name():
    msg = _bad(lambda d: d["modes"]["lowcountry-calm"]["techniques"]["strand"].update(look={"kind": "white"}))
    assert "white look needs cct" in msg
    preset = _bad(
        lambda d: d["modes"]["coastal-tides"]["techniques"]["strand"].update(look={"kind": "preset"})
    )
    assert "mode 'coastal-tides'" in preset and "preset look needs" in preset
    colour = _bad(lambda d: d["modes"]["coastal-tides"]["techniques"]["strand"]["look"].pop("rgb"))
    assert "rgb look needs rgb" in colour


def _literal(d, layout: str, n: int) -> None:
    """Rainbow Row Sunset's module level as `n` literal looks by `layout` (rows or columns) instead of its sample."""
    modules = d["modes"]["rainbow-row-sunset"]["techniques"]["modules"]
    modules.pop("sample")
    modules[layout] = [{"kind": "rgb", "rgb": [255, 158, 150], "brightness": 85}] * n


def test_module_level_rows_must_match_the_canopy_grid():
    msg = _bad(lambda d: _literal(d, "columns", 2))
    assert "lists 2 looks but the canopy has 3 module columns" in msg

    def two_rows(d):
        _literal(d, "rows", 2)

    assert "lists 2 looks but the canopy has 3 module rows" in _bad(two_rows)


def test_invalid_yaml_is_a_config_error(tmp_path):
    for name in ("recipes.yaml", "canopy.yaml"):
        shutil.copy(CONFIG_DIR / name, tmp_path / name)
    Path(tmp_path / "modes.yaml").write_text("modes:\n  calm: [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_modes(tmp_path)


# ---- module level sampled from a 2-D design field (P3) ------------------------------------------------------------
def _sampled(
    field: dict, feature: str = "Each strand set to one colour, so the palette steps from the centre out."
):
    """modes.yaml with Coastal Tides' module level sampled from a field canvas (a valid radial design by default)."""

    def mutate(d):
        d["modes"]["coastal-tides"]["techniques"]["modules"] = {
            "sample": {
                "canvas": {
                    "kind": "field",
                    "field": field,
                    "stops": [{"at": 0, "color": "pastel-coral"}, {"at": 1, "color": "lilac"}],
                    "recipe": "rgb_only",
                },
                "brightness": 85,
            },
            "feature": feature,
        }

    return mutate


RADIAL = {"kind": "radial", "centre_in": [112.5, 60]}


def test_a_sampled_field_design_loads():
    data = copy.deepcopy(RAW)
    _sampled(RADIAL)(data)
    sample = _parse(data).mode("coastal-tides").techniques.modules.sample
    assert sample.at == "centre" and sample.canvas.field.radius_in == "auto"


def test_sampled_copy_must_name_the_designs_layout():
    msg = _bad(
        _sampled(RADIAL, feature="Each strand set to one colour, so the palette steps column by column.")
    )
    assert "mode 'coastal-tides' → techniques → modules → feature" in msg
    assert "laid out 'from the centre out'" in msg
    silent = _bad(_sampled(RADIAL, feature="Each strand set to one colour from the scene's palette."))
    assert "laid out 'from the centre out', so the copy must say 'from the centre out'" in silent
    diagonal = _bad(
        _sampled({"kind": "linear", "angle_deg": 30}, feature="Steps across the canopy row by row.")
    )
    assert "mode 'coastal-tides'" in diagonal and "'diagonally'" in diagonal
    rows = copy.deepcopy(RAW)
    _sampled({"kind": "linear", "angle_deg": 90}, feature="Each strand one colour, stepping row by row.")(
        rows
    )
    assert _parse(rows).mode("coastal-tides").techniques.modules.sample is not None


def test_a_wave_in_sampled_static_copy_is_still_refused():
    msg = _bad(_sampled(RADIAL, feature="A wave of colour from the centre out."))
    assert "mode 'coastal-tides': techniques → modules → feature mentions movement ('wave')" in msg


@pytest.mark.parametrize(
    ("field", "where", "message"),
    [
        ({"kind": "spiral"}, "sample → canvas → field", "does not match any of the expected tags"),
        (
            {**RADIAL, "cycles": 0},
            "sample → canvas → field → cycles",
            "cycles must be a whole number from 1 to 6",
        ),
        (
            {**RADIAL, "radius_in": 0},
            "sample → canvas → field → radius_in",
            "radius_in must be auto or a distance",
        ),
        ({**RADIAL, "radius_in": -10}, "sample → canvas → field → radius_in", "greater than 0"),
        ({**RADIAL, "radius_in": True}, "sample → canvas → field → radius_in", "greater than 0"),
        ({**RADIAL, "radius_in": float("inf")}, "sample → canvas → field → radius_in", "and finite"),
    ],
)
def test_bad_field_specs_name_the_mode_and_field_plainly(field, where, message):
    msg = _bad(_sampled(field))
    assert f"mode 'coastal-tides' → techniques → modules → {where}:" in msg and message in msg


def test_a_sampled_canvas_is_rgb_only_and_one_layout_at_a_time():
    def blend(d):
        _sampled(RADIAL)(d)
        d["modes"]["coastal-tides"]["techniques"]["modules"]["sample"]["canvas"]["recipe"] = "blend"

    assert "needs recipe: rgb_only" in _bad(blend)

    def both(d):
        _sampled(RADIAL)(d)
        aqua = {"kind": "rgb", "rgb": [121, 206, 212]}
        d["modes"]["coastal-tides"]["techniques"]["modules"]["columns"] = [aqua] * 3

    assert "give exactly one of rows, columns, middle_row (+ others), or sample" in _bad(both)

    def unknown(d):
        _sampled(RADIAL)(d)
        d["modes"]["coastal-tides"]["techniques"]["modules"]["sample"]["canvas"]["stops"][1]["color"] = "teal"

    msg = _bad(unknown)
    assert "mode 'coastal-tides' → techniques → modules → sample → canvas → stops → [1] → color" in msg
    assert "unknown colour 'teal'" in msg


def test_a_sweep_centred_on_a_module_centre_is_refused_at_the_centre_only():
    sweep = {"kind": "sweep", "centre_in": [112.5, 60]}
    words = "Each strand set to one colour, so the palette steps around the centre."
    msg = _bad(_sampled(sweep, feature=words))
    assert "mode 'coastal-tides' → techniques → modules → sample" in msg and "module r1c1's centre" in msg
    data = copy.deepcopy(RAW)
    _sampled(sweep, feature=words)(data)
    data["modes"]["coastal-tides"]["techniques"]["modules"]["sample"]["at"] = "mean"
    assert _parse(data).mode("coastal-tides").techniques.modules.sample.at == "mean"
    off_centre = copy.deepcopy(RAW)
    _sampled(
        {"kind": "sweep", "centre_in": [112.5, 50]},
        feature="Each strand set to one colour, so the palette steps around one spot.",
    )(off_centre)
    assert _parse(off_centre).mode("coastal-tides").techniques.modules.sample is not None


def _loads(mutate) -> bool:
    data = copy.deepcopy(RAW)
    mutate(data)
    return _parse(data).mode("coastal-tides").techniques.modules.sample is not None


def test_an_off_centre_radial_design_cant_say_from_the_centre_out():
    sun = {
        "kind": "radial",
        "centre_in": [0, 60],
    }  # Golden Hour's draft: a sun at the middle of the left edge
    msg = _bad(_sampled(sun, feature="Each strand one colour, so the palette steps from the centre out."))
    assert "mode 'coastal-tides' → techniques → modules → feature" in msg
    assert "laid out 'from one edge'" in msg and "also says 'from the centre out'" in msg
    assert _loads(_sampled(sun, feature="Each strand one colour, so the sun glows from one edge across it."))
    moon = {"kind": "radial", "centre_in": [187.5, 20]}  # Goodnight Glow's draft: a moon over r0c2
    assert "laid out 'from one spot'" in _bad(_sampled(moon))
    assert _loads(_sampled(moon, feature="Each strand one colour, so the moonlight spreads from one spot."))
    two = {"kind": "radial", "centres_in": [[0, 0], [225, 120]]}
    assert "laid out 'from two points'" in _bad(_sampled(two))
    assert _loads(_sampled(two, feature="Each strand one colour, so the palette steps from two points."))


def test_layout_words_are_matched_without_case_and_no_other_layouts_words_are_allowed():
    assert _loads(
        _sampled(RADIAL, feature="From the centre out, each Hubspace strand is set to a single colour.")
    )
    mixed = _bad(_sampled(RADIAL, feature="Each strand one colour: from the centre out, column by column."))
    assert "laid out 'from the centre out', but the copy also says 'column by column'" in mixed
    linear = _bad(
        _sampled(
            {"kind": "linear", "angle_deg": 0}, feature="Column by column and Row By Row, one colour each."
        )
    )
    assert "laid out 'column by column', but the copy also says 'row by row'" in linear


def test_a_sampled_gradient_names_its_axis():
    def gradient(axis, feature):
        def mutate(d):
            _sampled(RADIAL, feature=feature)(d)
            d["modes"]["coastal-tides"]["techniques"]["modules"]["sample"]["canvas"] = {
                "kind": "gradient",
                "axis": axis,
                "stops": [{"at": 0, "color": "pastel-coral"}, {"at": 1, "color": "lilac"}],
                "recipe": "rgb_only",
            }

        return mutate

    across = "Each strand set to one colour, so the palette steps column by column."
    down = "Each strand set to one colour, so the palette steps row by row."
    assert "laid out 'row by row'" in _bad(gradient("y", across))
    assert _loads(gradient("y", down))
    assert "laid out 'column by column'" in _bad(gradient("x", down))
    assert _loads(gradient("x", across))


def test_a_design_with_one_colour_at_every_module_centre_is_refused():
    across = "Each strand set to one colour, so the palette steps column by column."
    # three repeats across the width: every module column's centre lands half way through a repeat
    thrice = {"kind": "linear", "angle_deg": 0, "cycles": 3}
    msg = _bad(_sampled(thrice, feature=across))
    assert (
        "mode 'coastal-tides' → techniques → modules → sample: the design has the same colour at every" in msg
    )

    def mean(d):
        _sampled(thrice, feature=across)(d)
        d["modes"]["coastal-tides"]["techniques"]["modules"]["sample"]["at"] = "mean"

    assert _loads(mean)  # the heads' mean differs per module; compile says so if it doesn't

    def flat(d):
        _sampled(RADIAL)(d)
        for stop in d["modes"]["coastal-tides"]["techniques"]["modules"]["sample"]["canvas"]["stops"]:
            stop["color"] = "lilac"

    assert "the same colour at every module centre" in _bad(flat)


def test_a_sampled_field_canvas_needs_ordered_stops_and_a_real_brightness():
    def stops(*ats):
        def mutate(d):
            _sampled(RADIAL)(d)
            d["modes"]["coastal-tides"]["techniques"]["modules"]["sample"]["canvas"]["stops"] = [
                {"at": a, "color": c}
                for a, c in zip(ats, ("pastel-coral", "lilac", "blush-pink"), strict=False)
            ]

        return mutate

    assert "stops must be listed in increasing `at` order" in _bad(stops(1, 0))
    assert "stops must be listed in increasing `at` order" in _bad(stops(0, 0.5, 0.5))
    single = _bad(stops(0))
    assert "sample → canvas → stops:" in single and "at least 2 items" in single

    def dark(d):
        _sampled(RADIAL)(d)
        d["modes"]["coastal-tides"]["techniques"]["modules"]["sample"]["brightness"] = 0

    assert "sample → brightness:" in _bad(dark)


def test_without_a_canopy_only_the_layouts_that_dont_need_one_are_checked():
    def parse(mutate):
        data = copy.deepcopy(RAW)
        mutate(data)
        return parse_modes(data, recipes=RECIPES, canopy=None, path="config/modes.yaml")

    # where a radial centre sits needs the canopy's size: unknown, so its words can't be checked
    assert parse(_sampled({"kind": "radial", "centre_in": [0, 60]})).mode("coastal-tides") is not None
    with pytest.raises(ConfigError, match="laid out 'diagonally'"):
        parse(_sampled({"kind": "linear", "angle_deg": 30}))


# ---- stepped (D65 draft; config/modes.yaml has none, P8 is dormant) ------------------------------------------------
TIDES_MOVING = (
    "Colour and dimming through the Hubspace cloud: each of the nine Hubspace strands shows one colour and is sent "
    "its next colour every few seconds, so the palette moves diagonally like a tide."
)


def _stepped(ladder=("stepped", "modules", "strand"), **over):
    """modes.yaml with Coastal Tides stepping its own sampled design (valid by default), `over` merged in."""

    def mutate(d):
        tides = d["modes"]["coastal-tides"]
        tides["ladder"] = list(ladder)
        tides["techniques"]["stepped"] = {
            "canvas": copy.deepcopy(tides["techniques"]["modules"]["sample"]["canvas"]),
            "brightness": tides["techniques"]["modules"]["sample"]["brightness"],
            "moving_feature": TIDES_MOVING,
            **over,
        }

    return mutate


def _stepped_ok(mutate):
    data = copy.deepcopy(RAW)
    mutate(data)
    return _parse(data).mode("coastal-tides").techniques.stepped


def test_a_stepped_technique_loads_with_its_defaults():
    stepped = _stepped_ok(_stepped())
    assert (stepped.direction, stepped.steps_per_cycle, stepped.step_s, stepped.run_s) == (
        "forward",
        6,
        6.0,
        90.0,
    )
    assert stepped.canvas == _parse(copy.deepcopy(RAW)).mode("coastal-tides").techniques.modules.sample.canvas


@pytest.mark.parametrize(
    ("over", "where", "message"),
    [
        ({"step_s": 3}, "stepped → step_s", "greater than or equal to 4"),
        ({"step_s": 13}, "stepped → step_s", "less than or equal to 12"),
        ({"run_s": 181}, "stepped → run_s", "less than or equal to 180"),
        ({"steps_per_cycle": 2}, "stepped → steps_per_cycle", "greater than or equal to 3"),
        ({"steps_per_cycle": 13}, "stepped → steps_per_cycle", "less than or equal to 12"),
        ({"direction": "inwards"}, "stepped → direction", "'forward' or 'reverse'"),
        ({"wavelength": 2}, "stepped → wavelength", "Extra inputs are not permitted"),
    ],
)
def test_stepped_ranges_name_the_mode_and_field(over, where, message):
    msg = _bad(_stepped(**over))
    assert f"mode 'coastal-tides' → techniques → {where}:" in msg and message in msg


def test_a_run_shorter_than_one_step_is_refused():
    msg = _bad(_stepped(step_s=8, run_s=5))
    assert "run_s (5 s) is shorter than one step (8 s)" in msg
    assert _stepped_ok(_stepped(step_s=8, run_s=8)).run_s == 8


@pytest.mark.parametrize(
    "claim", ["animation", "Animated", "effect", "controller", "each light", "every light", "270"]
)
def test_moving_copy_that_claims_an_effect_or_per_light_animation_is_refused(claim):
    msg = _bad(_stepped(moving_feature=f"{TIDES_MOVING} An {claim} you can see."))
    assert "mode 'coastal-tides' → techniques → stepped:" in msg
    assert "moving_feature says" in msg and "one colour at a time through the Hubspace cloud" in msg


def test_moving_copy_may_say_moves_but_static_copy_still_may_not():
    assert "moves" in _stepped_ok(_stepped()).moving_feature

    def static_moves(d):
        _stepped()(d)
        d["modes"]["coastal-tides"]["techniques"]["modules"]["feature"] = (
            "Each strand one colour, so the palette moves diagonally."
        )

    assert "techniques → modules → feature mentions movement ('moves')" in _bad(static_moves)


def test_a_stepped_rung_needs_a_later_modules_rung_with_the_same_design():
    for ladder in (("stepped", "strand"), ("modules", "stepped", "strand")):
        msg = _bad(_stepped(ladder=ladder))
        assert "mode 'coastal-tides'" in msg and "needs a later 'modules' rung" in msg

    def other_canvas(d):
        _stepped()(d)
        d["modes"]["coastal-tides"]["techniques"]["stepped"]["canvas"]["field"]["angle_deg"] = 80

    assert "stepped → canvas must be the modules sample's canvas" in _bad(other_canvas)
    assert "brightness (50) must be the modules sample's (85)" in _bad(_stepped(brightness=50))

    def mean(d):
        _stepped()(d)
        d["modes"]["coastal-tides"]["techniques"]["modules"]["sample"]["at"] = "mean"

    assert "needs at: centre" in _bad(mean)

    def literal(d):
        _stepped()(d)
        modules = d["modes"]["coastal-tides"]["techniques"]["modules"]
        level = modules.pop("sample")["brightness"]
        modules["columns"] = [{"kind": "rgb", "rgb": [121, 206, 212], "brightness": level}] * 3

    assert "needs a later 'modules' rung" in _bad(literal)


def test_moving_copy_names_the_flow_and_no_other():
    silent = _bad(_stepped(moving_feature="Each strand is sent its next colour every few seconds."))
    assert (
        "stepped → moving_feature: the design flows 'diagonally' (forward), so the copy must say 'diagonally'"
        in silent
    )
    other = _bad(_stepped(moving_feature=f"{TIDES_MOVING} It moves column by column."))
    assert "but the copy also says 'column by column'" in other

    def radial(direction, words):
        def mutate(d):
            _sampled(RADIAL)(d)
            _stepped(
                direction=direction, moving_feature=f"Each strand's colour moves {words} every few seconds."
            )(d)

        return mutate

    assert _stepped_ok(radial("forward", "from the centre out")).direction == "forward"
    assert _stepped_ok(radial("reverse", "toward the centre")).direction == "reverse"
    backwards = _bad(radial("reverse", "from the centre out"))
    assert (
        "the design flows 'toward the centre' (reverse), so the copy must say 'toward the centre'"
        in backwards
    )
    assert "also says 'from the centre out'" in backwards


def test_a_stepped_canvas_names_known_colours():
    def unknown(d):
        _stepped(ladder=("modules", "strand"))(d)  # not on the ladder: only the colour check can catch it
        d["modes"]["coastal-tides"]["techniques"]["stepped"]["canvas"]["stops"][0]["color"] = "salmon"

    msg = _bad(unknown)
    assert "techniques → stepped → canvas → stops → [0] → color: unknown colour 'salmon'" in msg
