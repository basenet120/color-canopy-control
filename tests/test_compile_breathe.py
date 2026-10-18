"""Breathe and shimmer stepped looks (Tier.STEPPED, D75) compiled from tests/fixtures_modes_breathe.yaml: the design
stays in place and each module's colour (`shift`) and level (`brightness_swing`) breathe, one breath of
`steps_per_cycle` steps per module at its own lag; step 0 is the still design; every step is one full state vector
per driven module with its brightness in it; the moving copy says what changes. Travel (D65) is
tests/test_compile_stepped.py's."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conductor.config import RecipesFile, load_recipes
from conductor.geometry import load_canopy_config
from conductor.looks.composer import evaluate
from conductor.looks.schema import FieldCanvas

PATH = Path(__file__).with_name("fixtures_modes_breathe.yaml")
FROZEN = Path(__file__).with_name("fixtures_recipes_frozen.yaml")


def _frozen_recipes() -> RecipesFile:
    """config's recipes with today's scene colours frozen over them (Design Lab PLAN §3.7)."""
    live = load_recipes().model_dump()
    frozen = yaml.safe_load(FROZEN.read_text(encoding="utf-8"))
    return RecipesFile.model_validate({**live, "colors": {**live["colors"], **frozen["colors"]}})


RECIPES = _frozen_recipes()
CANOPY = load_canopy_config()
STOPS = [
    {"at": 0.0, "color": "ocean-blue"},
    {"at": 0.3333333333333333, "color": "aqua"},
    {"at": 0.6666666666666666, "color": "seafoam"},
    {"at": 1.0, "color": "foam-white"},
]


def _linear(**field) -> FieldCanvas:
    return FieldCanvas.model_validate(
        {
            "kind": "field",
            "field": {"kind": "linear", "angle_deg": 0, **field},
            "stops": STOPS,
            "recipe": "rgb_only",
        }
    )


def _at(canvas, x: float, y: float = 60.0, **kw):
    return evaluate(canvas, x, y, canopy=CANOPY, recipes=RECIPES, **kw)


# ---- the colour offset (composer) -----------------------------------------------------------------------------------
def test_a_colour_offset_is_clamped_then_mirrored_then_inverted():
    folded, plain = _linear(transform="mirror", invert=True), _linear()
    # t = x / 225: at x = 45, u = 0.2; + 0.1 = 0.3; mirror → 0.6; invert → 0.4, the plain field's colour at x = 90
    assert _at(folded, 45, offset=0.1) == _at(plain, 90)
    assert _at(folded, 45, offset=0.1) != _at(folded, 45)
    # clamped, never wrapped: −0.5 at x = 0 stays at u = 0 (a wrap would give u = 0.5)
    assert _at(plain, 0, offset=-0.5) == _at(plain, 0) != _at(plain, 112.5)
    assert _at(folded, 0, offset=-0.5) == _at(folded, 0)
    assert _at(plain, 225, offset=0.4) == _at(plain, 225)
    # an offset of 0 is today's evaluation, byte for byte
    assert _at(folded, 45, offset=0.0) == _at(folded, 45)
    with pytest.raises(AssertionError):
        _at(plain, 45, phase=0.1, offset=0.1)
