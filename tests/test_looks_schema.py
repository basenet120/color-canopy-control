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
from conductor.looks.schema import MOTION_WORDS, GradientCanvas

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
    assert isinstance(sunset.techniques.canvas.canvas, GradientCanvas)
    assert [s.color for s in sunset.techniques.canvas.canvas.stops] == ["pastel-coral", "blush-pink", "lilac"]
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


def test_module_level_rows_must_match_the_canopy_grid():
    msg = _bad(lambda d: d["modes"]["rainbow-row-sunset"]["techniques"]["modules"]["columns"].pop())
    assert "lists 2 looks but the canopy has 3 module columns" in msg

    def two_rows(d):
        modules = d["modes"]["rainbow-row-sunset"]["techniques"]["modules"]
        modules["rows"] = modules.pop("columns")[:2]

    assert "lists 2 looks but the canopy has 3 module rows" in _bad(two_rows)


def test_invalid_yaml_is_a_config_error(tmp_path):
    for name in ("recipes.yaml", "canopy.yaml"):
        shutil.copy(CONFIG_DIR / name, tmp_path / name)
    Path(tmp_path / "modes.yaml").write_text("modes:\n  calm: [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_modes(tmp_path)
