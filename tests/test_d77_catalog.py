"""D77: the client-review catalog is 18 options, three per scene, and every one moves.

The six scenes keep their ids, approved palettes, moods, slots and the idle role; each gets two options, `<id>-2` and
`<id>-3`, with `option_of: <id>` and the names "<Scene name> #n". Every option has a stepped rung (eleven travel, five
breathe, two shimmer), which live hardware refuses until Gate R. The designs sit on VWBD's fins (D74): nine on the fin
rings, Calm #1 and Garden #1 on rings tilted along the current, five along the current, Golden Hour #1's sun
stretched so the deep centre is sunset pink, and Coastal Tides #3's lighthouse sweep.

Two kinds of test:
- Invariants, always asserted: ids and order, option_of, slots, ladders, the idle role, the copy rules, the fins'
  geometry, and what the compiled plans and the engine do.
- D77's draft values (layouts, palette keys, stepped parameters, names, prefixes, the three-ring counts), asserted
  only while the pinned fixture and the design's colours still hold them (`skip_unless_drafted`). A Design Lab
  export at the client session then can't fail CI, and `make lab-roundtrip` stays green
  (tests/test_lab_roundtrip.py). This is the pattern of tests/test_modes_match_scenes.py's Golden Hour sun test.

Every DRAFT value is what config/ holds as built, not spec_D's step-6 table: run_s is a whole number of program cycles
(steps_per_cycle plus the largest lag), the shimmers lag 2 with seeds 31 and 25, the colour-only breaths drift and
settle, and Golden Hour #2's stops follow decision D3 (warm white, honey, Home Depot orange, amber)."""

from __future__ import annotations

import itertools
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pytest
from fake_compiler import make_world, settings_with
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from test_d65_modes import _show_wiring

from conductor import ipad_menu as IM
from conductor import rows as RW
from conductor.capabilities import detect
from conductor.clock import run_virtual
from conductor.config import (
    CONFIG_DIR,
    SETTINGS_OVERLAY_PATH,
    KioskSettings,
    load_recipes,
    load_registry,
    load_settings,
)
from conductor.design_lab import draft as D
from conductor.engine_api import PlanError, Tier
from conductor.geometry import load_canopy_config
from conductor.looks import CompileContext, compile_mode, load_modes
from conductor.looks import field as F
from conductor.looks import scene_rules as R
from conductor.looks.compile import SAFE_MODES, design_field, module_lags
from conductor.looks.schema import BREATHE_PHRASES, MOTION_WORDS, STEPPED_PACE, breathe_words
from conductor.scenes import load_scenes
from conductor.sim.profiles import profile_parts

SCENES = load_scenes()
MODES = load_modes()
RECIPES = load_recipes()
CANOPY = load_canopy_config()
SETTINGS = load_settings()
REGISTRY = load_registry()
MODULES = REGISTRY.canopy_modules()
FUNCTIONS, STATES = profile_parts("sqhdk03-sim")
CAPS = detect(FUNCTIONS, STATES)
PINS = json.loads((Path(__file__).parent / "fixtures_scene_frames.json").read_text())["_pins"]
GRID = [f"r{r}c{c}" for r in range(3) for c in range(3)]
CORNERS = ("r0c0", "r0c2", "r2c0", "r2c2")
EDGE_MIDDLES = ("r0c1", "r1c0", "r1c2", "r2c1")

BASE_NAMES = {
    "lowcountry-calm": "Lowcountry Calm",
    "rainbow-row-sunset": "Rainbow Row Sunset",
    "garden-gathering": "Garden Gathering",
    "coastal-tides": "Coastal Tides",
    "golden-hour": "Golden Hour",
    "goodnight-glow": "The Goodnight Glow",
}
CATALOG = [x for base in BASE_NAMES for x in (base, f"{base}-2", f"{base}-3")]
OPTIONS = [x for x in CATALOG if x not in BASE_NAMES]
BASE_OF = {x: x if x in BASE_NAMES else x.rsplit("-", 1)[0] for x in CATALOG}
PAINTED = {"rainbow-row-sunset": "custom-1", "garden-gathering": "custom-2", "golden-hour": "custom-3"}
IDLE = "lowcountry-calm"
# /api/kiosk/panel, which the iPad polls every 2 s: 62.7 KB on D77's tree with no engine (PR A's canopy geometry
# 44 KB; the 18 scenes 27 KB, PR C's thumbnails 9.6 KB of it); the ceiling leaves room for the engine's lit state
PANEL_MAX_BYTES = 80_000

# The copy prefixes. T3: module level. A: every strand is sent its next colour (travel, and the colour-only breaths of
# Calm #1 and Tides #1). B: every strand is sent a level (the level-only breaths and shimmers). The plan's prefix C
# ("the strands around the middle") is dropped (integration C4): PR B's breath swings both ways, so Tides #1's centre
# strand changes too.
T3 = "Colour and dimming: each Hubspace strand set to a single colour, so the palette is laid out "
PREFIX = {
    "A": (
        "Colour and dimming through the Hubspace cloud: each Hubspace strand set to a single colour and sent its "
        "next colour every few seconds, so "
    ),
    "B": (
        "Colour and dimming through the Hubspace cloud: each Hubspace strand set to a single colour and sent a "
        "brighter or softer level every few seconds, so "
    ),
}
LEVEL = "a brighter or softer level"
HEDGES = ("a touch of", "a hint of", "toward")

# The 46 colours of the 12 options, one recipe key each (D77). rainbow-row-sunset-2-apricot, not the free 'apricot':
# the Lab round-trip's rename case writes 'apricot' into the live recipes (tests/test_lab_export.py STRUCTURAL).
NEW_KEYS = frozenset(
    {
        "porch-light", "warm-linen", "haint-blue", "oyster-shell", "spartina", "mist-blue", "facade-pink",
        "butter-yellow", "mint", "sky-blue", "rainbow-row-sunset-2-lavender", "rainbow-row-sunset-2-apricot",
        "apricot-glow", "coral", "rainbow-row-sunset-3-rose", "dusk-violet", "magnolia-cream", "blossom-pink",
        "leaf-green", "moss", "edison-amber", "lantern-gold", "fern-green", "garden-gathering-3-warm-white",
        "deep-teal", "marsh-green", "tidewater-blue", "oyster-white", "night-sea", "harbor-blue", "lamp-gold",
        "beam-white", "golden-hour-2-warm-white", "honey", "golden-hour-2-amber", "home-depot-orange",
        "golden-hour-3-deep-amber", "burnt-orange", "dusty-rose", "smoky-violet", "night-blue", "deep-moss",
        "firefly", "candle-amber", "dusky-mauve", "goodnight-glow-3-night-blue",
    }
)  # fmt: skip
# The colour study's keys (config/recipes.yaml): no scene uses them, so no Lab export rewrites them. D77 takes none.
COLOUR_STUDY = {
    "hd-orange": {
        "label": "Home Depot orange",
        "hex": "#F96302",
        "rgb_only": {"r": 249, "g": 99, "b": 2, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
        "blend": {"r": 249, "g": 80, "b": 0, "cct": 2700, "colorBrightness": 100, "whiteBrightness": 25},
        "strand": {"rgb": (249, 99, 2), "cct": None},
    },
    "amber": {
        "label": "Amber",
        "hex": "#FF8C1A",
        "rgb_only": {"r": 255, "g": 126, "b": 0, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
        "blend": {"r": 255, "g": 100, "b": 0, "cct": 2700, "colorBrightness": 100, "whiteBrightness": 40},
        "strand": {"rgb": (255, 126, 0), "cct": None},
    },
    "deep-amber": {
        "label": "Deep amber",
        "hex": "#E0600A",
        "rgb_only": {"r": 255, "g": 90, "b": 0, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
        "blend": {"r": 255, "g": 70, "b": 0, "cct": 2700, "colorBrightness": 100, "whiteBrightness": 20},
        "strand": {"rgb": (255, 90, 0), "cct": None},
    },
    "peach": {
        "label": "Peach",
        "hex": "#FFB08A",
        "rgb_only": {"r": 255, "g": 160, "b": 110, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
        "blend": {"r": 255, "g": 120, "b": 70, "cct": 2700, "colorBrightness": 100, "whiteBrightness": 45},
        "strand": {"rgb": (255, 160, 110), "cct": None},
    },
    "warm-ivory": {
        "label": "Warm ivory",
        "hex": "#FFE4BC",
        "rgb_only": {"r": 255, "g": 214, "b": 160, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
        "blend": {"r": 255, "g": 170, "b": 90, "cct": 2700, "colorBrightness": 40, "whiteBrightness": 100},
        "strand": {"rgb": None, "cct": 2700},
    },
    "warm-white": {
        "label": "Warm white",
        "hex": "#FFD9A8",
        "rgb_only": {"r": 255, "g": 200, "b": 140, "cct": 0, "colorBrightness": 100, "whiteBrightness": 0},
        "blend": {"r": 0, "g": 0, "b": 0, "cct": 2700, "colorBrightness": 0, "whiteBrightness": 100},
        "strand": {"rgb": None, "cct": 2700},
    },
}


# ---- D77's draft, as built -------------------------------------------------------------------------------------------
class Draft(NamedTuple):
    field: dict
    keys: list[str]
    stepped: dict  # the nine keys of scene_pins' stepped pin (PR B)
    brightness: int
    name: str
    prefix: str  # the moving copy's prefix: "A" or "B"


def _travel(steps_per_cycle: int, run_s: float, direction: str = "forward") -> dict:
    return {
        "mode": "travel",
        "direction": direction,
        "steps_per_cycle": steps_per_cycle,
        "step_s": 6.0,
        "run_s": run_s,
        "shift": 0.0,
        "brightness_swing": 0,
        "lag_steps": 0,
        "seed": None,
    }


def _breath(
    mode: str, spc: int, step_s: float, run_s: float, *, shift=0.0, swing=0, lag=0, seed=None
) -> dict:
    return {
        "mode": mode,
        "direction": "forward",
        "steps_per_cycle": spc,
        "step_s": step_s,
        "run_s": run_s,
        "shift": shift,
        "brightness_swing": swing,
        "lag_steps": lag,
        "seed": seed,
    }


FINS = {"kind": "fins"}
TILTED = {"kind": "radial", "centre_in": [122.5, 67.5], "metric": "canopy", "invert": True}
CURRENT = {"kind": "linear", "angle_deg": 18.0}
GOLDEN_SUN = {"kind": "radial", "centre_in": [0.0, 60.0], "metric": "canopy", "radius_in": 225.0}
DRAFT: dict[str, Draft] = {
    "lowcountry-calm": Draft(
        {**TILTED, "radius_in": 130.0},
        ["soft-ivory", "sea-glass"],
        _breath("breathe", 4, 8.0, 120.0, shift=0.4, lag=1),
        70,
        "Lowcountry Calm #1",
        "A",
    ),
    "lowcountry-calm-2": Draft(
        FINS,
        ["porch-light", "warm-linen", "haint-blue"],
        _breath("breathe", 4, 8.0, 120.0, swing=10, lag=1),
        70,
        "Lowcountry Calm #2",
        "B",
    ),
    "lowcountry-calm-3": Draft(
        {"kind": "linear", "angle_deg": 22.0, "transform": "mirror"},
        ["oyster-shell", "spartina", "mist-blue"],
        _travel(8, 120.0),
        70,
        "Lowcountry Calm #3",
        "A",
    ),
    "rainbow-row-sunset": Draft(
        CURRENT,
        ["pastel-coral", "blush-pink", "lilac"],
        _travel(6, 90.0),
        85,
        "Rainbow Row Sunset #1",
        "A",
    ),
    "rainbow-row-sunset-2": Draft(
        CURRENT,
        [
            "facade-pink",
            "butter-yellow",
            "mint",
            "sky-blue",
            "rainbow-row-sunset-2-lavender",
            "rainbow-row-sunset-2-apricot",
        ],
        _travel(6, 90.0),
        85,
        "Rainbow Row Sunset #2",
        "A",
    ),
    "rainbow-row-sunset-3": Draft(
        FINS,
        ["apricot-glow", "coral", "rainbow-row-sunset-3-rose", "dusk-violet"],
        _travel(6, 90.0, "reverse"),
        85,
        "Rainbow Row Sunset #3",
        "A",
    ),
    "garden-gathering": Draft(
        TILTED,
        ["sage-green", "pale-lime", "ivory"],
        _travel(6, 90.0, "reverse"),
        75,
        "Garden Gathering #1",
        "A",
    ),
    "garden-gathering-2": Draft(
        FINS,
        ["magnolia-cream", "blossom-pink", "leaf-green", "moss"],
        _travel(6, 90.0),
        75,
        "Garden Gathering #2",
        "A",
    ),
    "garden-gathering-3": Draft(
        FINS,
        ["edison-amber", "lantern-gold", "fern-green", "garden-gathering-3-warm-white"],
        _breath("shimmer", 6, 6.0, 120.0, swing=10, lag=2, seed=31),
        75,
        "Garden Gathering #3",
        "B",
    ),
    "coastal-tides": Draft(
        FINS,
        ["ocean-blue", "aqua", "seafoam", "foam-white"],
        _breath("breathe", 6, 6.0, 105.0, shift=0.35, lag=1),
        85,
        "Coastal Tides #1",
        "A",
    ),
    "coastal-tides-2": Draft(
        {"kind": "linear", "angle_deg": 140.0, "transform": "mirror", "invert": True},
        ["deep-teal", "marsh-green", "tidewater-blue", "oyster-white"],
        _travel(6, 90.0),
        85,
        "Coastal Tides #2",
        "A",
    ),
    "coastal-tides-3": Draft(
        {"kind": "sweep", "centre_in": [107.5, 55.0], "start_deg": 0.0, "transform": "mirror"},
        ["night-sea", "harbor-blue", "lamp-gold", "beam-white"],
        _travel(8, 120.0),
        85,
        "Coastal Tides #3",
        "A",
    ),
    "golden-hour": Draft(
        GOLDEN_SUN,
        ["soft-gold", "golden-hour-peach", "sunset-pink", "rose", "lavender"],
        _travel(6, 90.0),
        95,
        "Golden Hour #1",
        "A",
    ),
    "golden-hour-2": Draft(  # decision D3: Home Depot orange on the four corner strands, amber on the apron
        FINS,
        ["golden-hour-2-warm-white", "honey", "home-depot-orange", "golden-hour-2-amber"],
        _travel(6, 90.0),
        95,
        "Golden Hour #2",
        "A",
    ),
    "golden-hour-3": Draft(
        CURRENT,
        ["golden-hour-3-deep-amber", "burnt-orange", "dusty-rose", "smoky-violet"],
        _travel(6, 90.0),
        95,
        "Golden Hour #3",
        "A",
    ),
    "goodnight-glow": Draft(
        {**FINS, "invert": True},
        ["periwinkle", "goodnight-glow-lavender", "moonlight-blue"],
        _breath("breathe", 4, 10.0, 120.0, swing=4),
        28,
        "The Goodnight Glow #1",
        "B",
    ),
    "goodnight-glow-2": Draft(
        FINS,
        ["night-blue", "deep-moss", "firefly"],
        _breath("shimmer", 6, 8.0, 128.0, swing=6, lag=2, seed=25),
        25,
        "The Goodnight Glow #2",
        "B",
    ),
    "goodnight-glow-3": Draft(
        FINS,
        ["candle-amber", "dusky-mauve", "goodnight-glow-3-night-blue"],
        _breath("breathe", 4, 10.0, 150.0, swing=4, lag=1),
        26,
        "The Goodnight Glow #3",
        "B",
    ),
}
FIELD = TypeAdapter(F.FieldSpec)
# The nine fin-ring options (D77, question 1): module level reads each as three rings
FIN_RINGS = [x for x in CATALOG if DRAFT[x].field["kind"] == "fins"]
# The perceptual count of the other nine (the largest set of module colours pairwise ΔE76 ≥ 2.3), computed once.
SEEN = {
    "lowcountry-calm": 4,
    "lowcountry-calm-3": 5,
    "rainbow-row-sunset": 9,
    "rainbow-row-sunset-2": 9,
    "garden-gathering": 5,
    "coastal-tides-2": 5,
    "coastal-tides-3": 8,
    "golden-hour": 6,
    "golden-hour-3": 9,
}
# The level-only breaths and shimmers (shift 0, a swing): Calm #2, Garden #3 and the three Goodnight options
STEPPED = {x: MODES.mode(x).techniques.stepped for x in CATALOG}
BRIGHTNESS_ONLY = [x for x, tech in STEPPED.items() if tech.shift == 0 and tech.brightness_swing > 0]


def drafted(scene_id: str) -> bool:
    """Whether the pinned fixture still holds D77's draft of `scene_id` (its layout, brightness and stepped pin) and
    its design still names D77's colours (a Lab rename, an added, removed or reordered colour changes the keys)."""
    pin, draft = PINS.get(scene_id), DRAFT[scene_id]
    return (
        pin is not None
        and pin["layout"] == FIELD.validate_python(draft.field).model_dump(mode="json")
        and pin["brightness"] == draft.brightness
        and pin.get("stepped") == draft.stepped
        and [s.color for s in MODES.mode(scene_id).techniques.modules.sample.canvas.stops] == draft.keys
    )


def skip_unless_drafted(scene_id: str) -> None:
    """The pattern of tests/test_modes_match_scenes.py: a value the Lab can edit is pinned only while drafted."""
    if not drafted(scene_id):
        pytest.skip("redesigned in the Design Lab; see DECISIONS")


# ---- helpers -------------------------------------------------------------------------------------------------------
def _ctx(**kw) -> CompileContext:
    return CompileContext(
        registry=REGISTRY,
        capabilities={m.id: CAPS for m in MODULES},
        modes=MODES,
        recipes=RECIPES,
        canopy=CANOPY,
        **kw,
    )


def _two_kits() -> dict[str, str | None]:
    """The show box's canopy (D62): only r0c0 and r0c1 have a device."""
    return {m.id: (f"md-{m.id}" if m.module in R.LIVE_KITS else None) for m in MODULES}


def _design(scene_id: str):
    return MODES.mode(scene_id).techniques.modules.sample.canvas


def _colours(scene_id: str) -> dict[str, tuple[int, int, int]]:
    return R.module_colours(MODES.mode(scene_id), RECIPES, CANOPY)


def _t(scene_id: str) -> dict[str, float]:
    """The design's t at each module centre."""
    field = _design(scene_id).field
    return {p: F.value(field, x, y, CANOPY) for p, (x, y) in F.module_centres(CANOPY, GRID).items()}


def _seen(colours: dict[str, tuple[int, int, int]]) -> int:
    """The largest number of module colours pairwise ΔE76 ≥ 2.3 apart: how many the eye tells apart."""
    distinct = sorted(set(colours.values()))
    for k in range(len(distinct), 0, -1):
        for group in itertools.combinations(distinct, k):
            if all(R.delta_e76(a, b) >= 2.3 for a, b in itertools.combinations(group, 2)):
                return k
    return 0


def _rgb(hex_: str) -> tuple[int, int, int]:
    return int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16)


def _named(text: str, names: list[str]) -> list[str]:
    """The palette names `text` says, matched whole and longest first (so "night blue" isn't also "blue")."""
    low, found = text.lower(), []
    for name in sorted(names, key=len, reverse=True):
        pattern = rf"\b{re.escape(name.lower())}\b"
        if re.search(pattern, low):
            found.append(name)
            low = re.sub(pattern, "#" * len(name), low)
    return found


def _stepped_plan(scene_id: str, **kw):
    return compile_mode(scene_id, Tier.STEPPED, _ctx(**kw))


def _level(rows) -> int:
    (row,) = [r for r in rows if r.function_class == "brightness"]
    return int(row.value)


# ---- the catalog -----------------------------------------------------------------------------------------------------
def test_eighteen_options_three_per_scene_in_catalog_order():
    assert [s.id for s in SCENES.scenes] == CATALOG == list(MODES.modes)
    for base in BASE_NAMES:
        i = CATALOG.index(base)
        assert CATALOG[i : i + 3] == [base, f"{base}-2", f"{base}-3"]
    for scene in SCENES.scenes:
        assert scene.option_of == (None if scene.id in BASE_NAMES else BASE_OF[scene.id]), scene.id
        assert scene.group == BASE_OF[scene.id]
        mode = MODES.mode(scene.id)
        assert mode.blurb == scene.mood == SCENES.get(BASE_OF[scene.id]).mood
        assert mode.label == scene.name
    names = [s.name for s in SCENES.scenes]
    assert len(set(names)) == len(names) == 18


def test_the_firsts_keep_their_slots_and_idle_role():
    painted = {m: mode.slot for m, mode in MODES.modes.items() if mode.slot is not None}
    assert painted == PAINTED
    for option in OPTIONS:
        mode = MODES.mode(option)
        assert mode.slot is None and mode.techniques.canvas is None and mode.techniques.slot is None, option
    assert SETTINGS.kiosk.idle_mode == IDLE and SAFE_MODES == frozenset({IDLE})


@pytest.mark.parametrize("scene_id", CATALOG)
def test_every_option_has_a_stepped_rung_within_the_bounds(scene_id):
    mode = MODES.mode(scene_id)
    if scene_id in PAINTED:
        assert mode.ladder == ["canvas", "slot", "stepped", "modules", "strand"]
    else:
        assert mode.ladder == ["stepped", "modules", "strand"]
    # D75 (integration C1): a scene moves through its rung (Mode.moves); `motion` is 07 §6's burst flag, never set
    assert mode.moves and mode.motion is False
    tech = mode.techniques.stepped
    assert tech.mode in {"travel", "breathe", "shimmer"}
    assert (tech.seed is not None) == (tech.mode == "shimmer")
    assert 1 <= tech.brightness - tech.brightness_swing and tech.brightness + tech.brightness_swing <= 100
    assert tech.run_s <= SETTINGS.kiosk.idle_return_s
    sample = mode.techniques.modules.sample
    assert tech.canvas == sample.canvas and tech.brightness == sample.brightness  # the hold is module level


@pytest.mark.parametrize("scene_id", CATALOG)
def test_the_catalog_is_d77s_draft(scene_id):
    skip_unless_drafted(scene_id)
    draft, scene, mode = DRAFT[scene_id], SCENES.get(scene_id), MODES.mode(scene_id)
    design = _design(scene_id)
    assert design.field == FIELD.validate_python(draft.field)
    n = len(design.stops)
    assert [s.at for s in design.stops] == [i / (n - 1) for i in range(n)]
    assert [s.color for s in design.stops] == draft.keys
    tech = mode.techniques.stepped
    assert {k: getattr(tech, k) for k in draft.stepped} == draft.stepped
    assert scene.brightness_draft == draft.brightness
    number = 1 if scene_id in BASE_NAMES else int(scene_id.rsplit("-", 1)[1])
    assert scene.name == draft.name == f"{BASE_NAMES[BASE_OF[scene_id]]} #{number}"
    assert mode.techniques.modules.feature.startswith(T3)
    assert tech.moving_feature.startswith(PREFIX[draft.prefix])


def test_the_stepped_rungs_are_eleven_travel_five_breathe_two_shimmer():
    if not all(drafted(x) for x in CATALOG):
        pytest.skip("redesigned in the Design Lab; see DECISIONS")
    kinds = Counter(MODES.mode(x).techniques.stepped.mode for x in CATALOG)
    assert kinds == {"travel": 11, "breathe": 5, "shimmer": 2}
    assert FIN_RINGS == [
        "lowcountry-calm-2",
        "rainbow-row-sunset-3",
        "garden-gathering-2",
        "garden-gathering-3",
        "coastal-tides",
        "golden-hour-2",
        "goodnight-glow",
        "goodnight-glow-2",
        "goodnight-glow-3",
    ]
    assert BRIGHTNESS_ONLY == [
        "lowcountry-calm-2",
        "garden-gathering-3",
        "goodnight-glow",
        "goodnight-glow-2",
        "goodnight-glow-3",
    ]


# ---- the fins ------------------------------------------------------------------------------------------------------
def test_the_fin_geometry_at_the_module_centres():
    """PR A's fin rings at the module centres (docs/plans/preset-options/fins.json). A change of the weight formula
    shows here: exp(−d²/72) instead of exp(−(d/6)²) moves the corner to 0.67757."""
    fins = F.FinsField(kind="fins")
    expected = {
        "r1c1": 0.0,
        "r0c1": 0.39046,
        "r2c1": 0.39046,
        "r1c0": 0.39661,
        "r1c2": 0.39699,
        "r0c2": 0.66663,
        "r2c0": 0.66667,
        "r0c0": 0.67454,
        "r2c2": 0.67442,
    }
    t = {p: F.value(fins, x, y, CANOPY) for p, (x, y) in F.module_centres(CANOPY, GRID).items()}
    for pos, value in expected.items():
        assert t[pos] == pytest.approx(value, abs=1e-4), pos
    assert min(t, key=t.get) == "r1c1"
    assert min(t[c] for c in CORNERS) > max(t[e] for e in EDGE_MIDDLES)


@pytest.mark.parametrize("scene_id", [x for x in CATALOG if _design(x).field.kind == "fins"])
def test_fin_ring_designs_start_where_their_t_is_lowest(scene_id):
    """A fins design reads "from the centre out" whatever `invert` says, and its ripple (D64) lands ring by ring from
    the deep centre: the ripple's order drops invert (compile.design_field), so it starts where the layout words do.
    PR A rounds the fins' t inside F.bands, so the three rings stay whole (r0c2's 0.66663 is a corner)."""
    assert F.layout_words(_design(scene_id).field, CANOPY) == "from the centre out"
    assert design_field(MODES.mode(scene_id), Tier.T3).invert is False
    waves = compile_mode(scene_id, Tier.T3, _ctx()).waves
    position = {m.id: m.module for m in MODULES}
    assert [sorted(position[d] for d in ring) for ring in waves] == [
        ["r1c1"],
        sorted(EDGE_MIDDLES),
        sorted(CORNERS),
    ]


@pytest.mark.parametrize(
    "scene_id, angle, tolerance",
    [
        ("rainbow-row-sunset", 18, 7),
        ("rainbow-row-sunset-2", 18, 7),
        ("golden-hour-3", 18, 7),
        ("lowcountry-calm-3", 22, 7),
        ("coastal-tides-2", 140, 3),
    ],
)
def test_the_current_runs_along_the_fins(scene_id, angle, tolerance):
    """The fins' current runs at about 32° in true inches. A linear field measures its angle in the canopy's
    proportions (225 × 120 in), so 18° is 31.4° in inches. Coastal Tides #2's channel lies along the current, so
    there it is the bands (the gradient turned 90°) that run at 32°."""
    skip_unless_drafted(scene_id)
    field = _design(scene_id).field
    assert field.kind == "linear" and field.angle_deg == angle
    w, h = float(CANOPY["width_in"]), float(CANOPY["height_in"])
    rad = math.radians(field.angle_deg)
    gradient = math.degrees(math.atan2(math.sin(rad) / h, math.cos(rad) / w))  # the colours change this way
    heading = gradient if angle < 90 else gradient - 90
    assert abs(heading - 32) <= tolerance, round(heading, 1)


def test_golden_hour_first_puts_sunset_pink_on_the_deep_centre():
    if PINS["golden-hour"]["layout"] != FIELD.validate_python(GOLDEN_SUN).model_dump(mode="json"):
        pytest.skip("Golden Hour's sun was redesigned in the Design Lab; see DECISIONS")
    colours = _colours("golden-hour")
    pink = SCENES.get("golden-hour").palette[2]
    assert pink.name == "Sunset pink" and colours["r1c1"] == pink.rgb == (255, 155, 196)
    t = _t("golden-hour")
    assert min(t, key=t.get) == "r1c0"  # the sun at the middle of the left edge
    assert [colours[f"r0c{c}"] for c in range(3)] == [colours[f"r2c{c}"] for c in range(3)]
    assert len(set(colours.values())) == 6


# ---- module level as the eye sees it ---------------------------------------------------------------------------------
@pytest.mark.parametrize("scene_id", CATALOG)
def test_module_colours_as_the_eye_sees_them(scene_id):
    colours = _colours(scene_id)
    assert 4 <= len(set(colours.values())) <= 9  # the two-d warning
    assert colours["r0c0"] != colours["r0c1"]  # the kits-differ warning: the show box's two kits differ
    skip_unless_drafted(scene_id)
    if scene_id in FIN_RINGS:
        # D77, question 1: the nine fin-ring options pass two-d only on near-ties (ΔE76 0.33–1.23: r0c0/r2c2 at t
        # 0.6745 against r0c2/r2c0 at 0.6666). To the eye each is three rings: the centre, the edge-middles and the
        # corners. D77 and the PR body say so; Alon chooses whether to keep them on the fins.
        assert _seen(colours) == 3
    else:
        assert _seen(colours) == SEEN[scene_id]


# ---- recipes and copy ------------------------------------------------------------------------------------------------
def test_every_new_colour_has_its_own_recipe_and_the_colour_study_keys_are_untouched():
    used = Counter(s.color for x in CATALOG for s in _design(x).stops)
    assert set(used.values()) == {1}, [k for k, n in used.items() if n > 1]  # one palette colour per key
    for key, recipe in COLOUR_STUDY.items():
        assert RECIPES.colors[key].model_dump() == recipe, key
        assert key not in used
    assert R.scene_mode_problems(SCENES, MODES, RECIPES, CANOPY, rules={"recipes"}) == []
    options = [x for x in OPTIONS if drafted(x)]
    option_keys = {s.color for x in options for s in _design(x).stops}
    assert option_keys <= NEW_KEYS
    if options == OPTIONS:
        assert option_keys == NEW_KEYS and len(NEW_KEYS) == 46


def test_client_copy_and_colour_names_pass_the_copy_rules():
    strings = 0
    for scene in SCENES.scenes:
        for what in R.CLIENT_COPY:
            assert R.copy_problem(what, getattr(scene, what)) is None, (scene.id, what)
            strings += 1
        for colour in scene.palette:
            D.DraftColour(name=colour.name, hex=colour.hex)
        mode = MODES.mode(scene.id)
        assert len(mode.label) <= 40 and len(mode.blurb) <= 120 and len(mode.preview) <= 6
        assert "deepening" not in scene.description.lower()
    assert strings == 54


def test_hash_names_are_quoted_in_the_yaml():
    """An unquoted " #" starts a YAML comment and would cut "Golden Hour #2" to "Golden Hour". Every name with a "#"
    (all 18 while D77's names stand; a Lab rename at the session may drop a "#1", C27) is double-quoted."""
    scenes_text = (CONFIG_DIR / "scenes.yaml").read_text(encoding="utf-8")
    modes_text = (CONFIG_DIR / "modes.yaml").read_text(encoding="utf-8")
    hashed = [s for s in SCENES.scenes if "#" in s.name]
    for scene in hashed:
        assert re.fullmatch(r".+ #\d", scene.name), scene.name
        assert f'name: "{scene.name}"' in scenes_text
        assert f'label: "{scene.name}"' in modes_text
    if all(drafted(x) for x in CATALOG):
        assert len(hashed) == 18


@pytest.mark.parametrize("scene_id", CATALOG)
def test_named_colours_are_on_the_strands(scene_id):
    """Module-level copy names a colour only where some strand shows at least about a third of it (the weight of stop
    i at t is max(0, 1 − |t − i/(n−1)|·(n−1))), and hedges one under half ("a touch of", "a hint of", "toward"):
    integration C28, recorded in D77. The moving copy names only colours some strand is nearest to at some step.
    D77's hand copy, so pinned while drafted: the Lab's generated copy names only the colours module level reaches."""
    skip_unless_drafted(scene_id)
    scene, mode = SCENES.get(scene_id), MODES.mode(scene_id)
    names = [c.name for c in scene.palette]
    n = len(names)
    t = _t(scene_id)
    weight = {
        name: max(max(0.0, 1 - abs(v - i / (n - 1)) * (n - 1)) for v in t.values())
        for i, name in enumerate(names)
    }
    feature = mode.techniques.modules.feature
    clauses = re.split(r"[,:]", feature.lower())
    for name in _named(feature, names):
        assert weight[name] >= 0.3, (name, round(weight[name], 3))
        if weight[name] < 0.49:
            (clause,) = [c for c in clauses if name.lower() in c]
            assert any(h in clause for h in HEDGES), (name, clause)
    steps = _stepped_plan(scene_id).program.steps
    nearest = {
        min(scene.palette, key=lambda c: R.delta_e76(c.rgb, _rgb(hex_))).name
        for step in steps
        for hex_ in step.colours.values()
    }
    moving = _named(mode.techniques.stepped.moving_feature, names)
    assert set(moving) <= nearest, sorted(set(moving) - nearest)


# ---- what the strands are sent ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("scene_id", CATALOG)
def test_moving_copy_says_what_the_strands_are_sent(scene_id):
    """Derived from the compiled stepped plan on the full canopy: the copy says "next colour" exactly when some step
    changes a strand's colour, and "a brighter or softer level" exactly when some step changes a level (integration
    C4, C5; D77's level breaths change only levels). Every strand is sent something: PR B's breath swings both ways,
    so no strand is left out."""
    plan = _stepped_plan(scene_id)
    steps = plan.program.steps
    tech = MODES.mode(scene_id).techniques.stepped
    moving = plan.program.moving_feature
    never = [d for d in plan.modules if all(s.rows[d] == steps[0].rows[d] for s in steps)]
    colour_changes = any(s.colours != steps[0].colours for s in steps)
    level_changes = any(s.levels != steps[0].levels for s in steps)
    assert never == []
    assert "each Hubspace strand set to a single colour and sent " in moving
    assert ("next colour" in moving) == colour_changes
    assert (LEVEL in moving) == level_changes
    assert "through the Hubspace cloud" in moving and STEPPED_PACE in moving
    if tech.mode == "travel":
        assert not any(p in moving for p in BREATHE_PHRASES)
    else:
        assert breathe_words(tech) in moving.lower() and not MOTION_WORDS.search(moving)
    if drafted(scene_id):
        assert moving.startswith(PREFIX[DRAFT[scene_id].prefix])


@pytest.mark.parametrize("scene_id", [x for x in CATALOG if STEPPED[x].mode != "travel"])
def test_breathe_and_shimmer_hold_their_still_design_and_breathe_within_their_swing(scene_id):
    plan, hold = _stepped_plan(scene_id), compile_mode(scene_id, Tier.T3, _ctx())
    tech = MODES.mode(scene_id).techniques.stepped
    steps = plan.program.steps
    assert steps[0].rows == {d: m.rows for d, m in hold.modules.items()}  # step 0 is the still design
    for step in steps:
        assert all(abs(level - tech.brightness) <= tech.brightness_swing for level in step.levels.values())
    if tech.shift == 0:
        assert all(s.colours == steps[0].colours for s in steps)
    else:
        assert any(s.colours != steps[0].colours for s in steps)
    assert any(s.rows != steps[0].rows for s in steps)
    if not drafted(scene_id):
        return  # D77's seeds and lags (below) are draft values a Lab edit may change
    if tech.mode == "shimmer":  # each strand keeps its own rhythm: two corners apart at some step
        assert any(len({s.levels[c] for c in CORNERS}) > 1 for s in steps)
    elif tech.lag_steps > 0:
        # a lagged breath starts where the design does (its t before invert): the deep centre leads the corner
        lags = module_lags(tech, GRID, CANOPY)
        assert lags["r1c1"] == 0 < lags["r0c0"]
        if tech.shift == 0:
            # a level breath: the centre's level moves first (a colour shift can clamp at a stop instead)
            def first_change(pos: str) -> int:
                return next(i for i, s in enumerate(steps) if s.levels[pos] != steps[0].levels[pos])

            assert first_change("r1c1") < first_change("r0c0")


@pytest.mark.parametrize("scene_id", CATALOG)
def test_the_partial_canopy_moving_copy_says_brightness_when_only_brightness_changes(scene_id):
    """The show box runs two kits (r0c0, r0c1), and so does Gate R's first step: the partial sentence says what the
    two strands are sent (PR B's compile._moving_feature), and neither kit pair ever rests a whole step."""
    plan = _stepped_plan(scene_id, addresses=_two_kits())
    moving = plan.program.moving_feature
    assert sorted(plan.modules) == ["canopy-r0c0", "canopy-r0c1"]
    assert moving.startswith("Colour and dimming through the Hubspace cloud: both Hubspace strands")
    if scene_id in BRIGHTNESS_ONLY:
        assert LEVEL in moving and "next colour" not in moving
    else:
        assert "next colour" in moving


@pytest.mark.parametrize("scene_id", list(PAINTED))
@pytest.mark.parametrize("header", [{"motionEffect": 0, "brightnessEffect": 0}, None])
def test_the_painted_firsts_t2_slot_is_never_a_motion_burst(scene_id, header):
    """The painted #1s with their slot hand-painted in the Hubspace app: T2 is never 07 §6's motion burst, whatever
    the slot's header reads (static, or unknown), because Mode.motion stays false (integration C1: a scene moves
    through its stepped rung instead), and it lands ring by ring (D64). tests/test_engine_review2.py's F9 keeps the
    burst cases on the fixture modes."""
    slot = PAINTED[scene_id]
    readback = {r.function_instance: r.value for r in STATES if r.function_class == RW.FRAME_CLASS}
    if header is None:
        readback.pop(slot)
    else:
        readback[slot] = {RW.FRAME_CLASS: {**readback[slot][RW.FRAME_CLASS], **header}}
    ctx = _ctx(
        slot_readback={m.id: readback for m in MODULES},
        recorded_hashes={m.id: {slot: "ab" * 8} for m in MODULES},
        recorded_origins={m.id: {slot: "app"} for m in MODULES},
    )
    plan = compile_mode(scene_id, Tier.T2, ctx)
    assert plan.tier is Tier.T2 and plan.motion is False and plan.waves is not None


# ---- the engine (the show's own wiring, the simulator's writes) ------------------------------------------------------
async def _show_world(*, simulated: bool = True, live_enabled: bool = False):
    """The real engine with the show's wiring (the shipped modes, its tier rules) on the simulator's writes, its driver
    named as the simulator or as live hardware. Writes are logged as (monotonic time, device id, rows)."""
    w = make_world(settings=settings_with(motion={"stepped": {"live_enabled": live_enabled}}))
    wiring = _show_wiring(w.registry, simulated=simulated, live_enabled=live_enabled)
    wiring.services.settings = w.engine.deps.settings
    w.engine.deps.compile = wiring.compile
    w.engine.deps.ladder = wiring.ladder
    w.engine.deps.modes = wiring.mode_ids
    w.engine.deps.tier_allowed = wiring.tier_allowed
    writes: list[tuple[float, str, list]] = []
    original = w.sim.write

    async def logged(device_id, rows, *, purpose):
        writes.append((w.clock.monotonic(), device_id.removeprefix("sim-"), list(rows)))
        return await original(device_id, rows, purpose=purpose)

    w.sim.write = logged
    w.writes = writes
    await w.engine.start()
    await w.run_for(70)  # start-up's burst is out of the 60 s rate window
    return w


@pytest.mark.parametrize("scene_id", ["coastal-tides", "lowcountry-calm-2"])
def test_a_breath_sends_one_full_state_put_per_module_per_step_and_reads_back_ready(scene_id):
    """A whole run in sim (Coastal Tides #1, a colour breath, and Calm #2, the first level-only one): every send is one
    full-state PUT holding the colour and the level together (07 §6), at most one per module per step and never
    closer than the 4 s write budget on one device; after the run every module reads back READY."""
    tech = MODES.mode(scene_id).techniques.stepped

    async def main():
        w = await _show_world()
        since = w.clock.monotonic()
        await w.engine.request_mode(scene_id, source="kiosk")
        plan, runner = w.engine.current_plan(), w.engine.runner
        running = runner is not None and runner.running
        paced = (
            runner.step_s if runner is not None else tech.step_s
        )  # the account's pace (7.5 s on nine strands)
        await w.run_for(tech.run_s + 2 * paced + 10)
        out = (plan.tier, running, paced)
        out += ([x for x in w.writes if x[0] >= since], w.engine.stepping, w.engine.state(), w.all_correct())
        await w.close()
        return out

    tier, running, paced, writes, stepping, state, correct = run_virtual(main)
    assert tier is Tier.STEPPED and running
    times: dict[str, list[float]] = {}
    for at, device, rows in writes:
        classes = {r.function_class for r in rows}
        # one full-state PUT: the colour and the level together
        assert {"color-rgb", "brightness"} <= classes, classes
        times.setdefault(device, []).append(at)
    assert sorted(times) == sorted(m.id for m in MODULES)
    steps = math.ceil(tech.run_s / paced)
    for device, sent in times.items():
        assert all(b - a >= 4.0 - 1e-6 for a, b in itertools.pairwise(sent)), device
        # one per step of the run, plus the settle after it
        assert len(sent) <= steps + 1, (device, len(sent))
    assert not stepping and correct  # the run is over: its hold (the still design) is on every module
    assert {m["status"] for m in state.modules.values()} == {"ready"}


@pytest.mark.parametrize("scene_id", BRIGHTNESS_ONLY)
def test_a_guest_brightness_never_leaves_moving_copy_with_nothing_sent(scene_id):
    """D75 (decision B3, integration C7): a guest level during a level-only breath keeps the breath, swung around the
    guest's level (clamped to kiosk.guest_brightness_min), so the program sends go on with changing levels and the
    moving copy stays true. Never moving copy for three steps with nothing sent."""
    tech = MODES.mode(scene_id).techniques.stepped
    guest = 50

    async def main():
        w = await _show_world()
        await w.engine.request_mode(scene_id, source="kiosk")
        runner = w.engine.runner
        await w.run_for(2 * tech.step_s + 0.5)  # step 2 is out
        state = await w.engine.set_guest_brightness(guest, source="kiosk")
        since = w.clock.monotonic()
        await w.run_for(3 * tech.step_s)
        sent = [_level(rows) for at, _, rows in w.writes if at > since]
        out = (state.feature, w.engine.state().feature, runner.running, w.engine.stepping, sent)
        await w.close()
        return out

    feature, later, running, stepping, sent = run_virtual(main)
    assert running and stepping
    assert feature == later == tech.moving_feature
    assert len(sent) >= len(MODULES) and len(set(sent)) > 1  # the sends go on, with changing levels
    low = max(SETTINGS.kiosk.guest_brightness_min, guest - tech.brightness_swing)
    assert all(low <= level <= guest + tech.brightness_swing for level in sent), sorted(set(sent))


def test_the_idle_look_never_starts_a_run_on_its_own():
    """Calm #1 has its own breath, but start-up, an idle return and the safe look show it still, its compile_best
    fallback included (D75, B5/B6); a guest choosing it gets its run. In sim, and on a live-named driver once Gate R
    has passed (motion.stepped.live_enabled), alike."""

    async def scenario(simulated: bool, live_enabled: bool):
        w = await _show_world(simulated=simulated, live_enabled=live_enabled)
        plan = w.engine.current_plan()
        started = (plan.mode_id, plan.tier, plan.program, w.engine.runner)
        await w.engine.request_mode("golden-hour", source="kiosk")
        await w.run_for(SETTINGS.kiosk.idle_return_s + 120)
        plan = w.engine.current_plan()
        returned = (plan.mode_id, plan.tier, plan.program, w.engine.runner)
        state = await w.engine.safe_look(source="ops")
        safe = (state.mode, w.engine.current_plan().tier, w.engine.runner)
        real = w.engine.deps.compile

        def no_strand(mode_id, tier):
            if Tier(tier) is Tier.STRAND:
                raise PlanError("the strand look can't be compiled (forced)")
            return real(mode_id, tier)

        w.engine.deps.compile = no_strand
        state = await w.engine.safe_look(source="ops")
        fallback = (state.refused, w.engine.current_plan().tier, w.engine.runner)
        w.engine.deps.compile = real
        await w.run_for(w.engine.guest_gap_s(w.engine.current_plan()) + 0.1)
        await w.engine.request_mode(IDLE, source="kiosk")
        runner = w.engine.runner
        chosen = (w.engine.current_plan().tier, runner is not None and runner.running)
        await w.close()
        return started, returned, safe, fallback, chosen

    for simulated, live_enabled in ((True, False), (False, True)):
        started, returned, safe, fallback, chosen = run_virtual(
            lambda s=simulated, e=live_enabled: scenario(s, e)
        )
        assert started == (IDLE, Tier.T3, None, None), simulated
        assert returned == (IDLE, Tier.T3, None, None), simulated
        assert safe == (IDLE, Tier.STRAND, None), simulated
        assert fallback == (False, Tier.T3, None), simulated  # the safe look never steps
        assert chosen == (Tier.STEPPED, True), simulated  # a guest's choice of the idle scene runs its breath


# ---- the iPad --------------------------------------------------------------------------------------------------------
def _panel(client: TestClient) -> dict:
    r = client.get("/api/kiosk/panel")
    assert r.status_code == 200, r.text
    return r.json()


def test_the_review_menu_sees_the_real_catalog(app_client):
    """CI runs only pytest (guest-panel.test.mjs isn't run from it), so the 18-card menu is proven here too."""
    r = app_client.get("/api/kiosk/panel")
    body = r.json()
    assert [s["id"] for s in body["scenes"]] == CATALOG
    for s in body["scenes"]:
        assert s["option_of"] == (None if s["id"] in BASE_NAMES else BASE_OF[s["id"]])
        assert s["group"] == BASE_OF[s["id"]]
        assert s["thumb"] is not None  # the review menu: every card carries its picture
    # the iPad polls this every 2 s: PR A's fin geometry and PR C's 18 thumbnails together, pinned
    assert len(r.content) < PANEL_MAX_BYTES, len(r.content)
    for option in OPTIONS:  # /ops "Show on the iPad" off for every option: the six #1s, in order
        hidden = app_client.post("/api/ops/ipad-scenes", json={"scene": option, "show": False})
        assert hidden.status_code == 200, hidden.text
    six = _panel(app_client)["scenes"]
    assert [s["id"] for s in six] == list(BASE_NAMES) and all(s["thumb"] is None for s in six)
    idle = app_client.post("/api/ops/ipad-scenes", json={"scene": IDLE, "show": False})
    assert idle.status_code == 409
    assert idle.json()["detail"] == IM.IDLE_ALWAYS.format(name=SCENES.get(IDLE).name, key="kiosk.idle_mode")
    with pytest.raises(ValidationError, match="it is the idle scene"):
        KioskSettings(hidden_scenes=[IDLE])


def test_an_overlay_that_hides_the_idle_scene_keeps_it_on_the_ipad_and_says_so(monkeypatch, tmp_path):
    from conftest import _env

    from conductor.app import app

    overlay = tmp_path / "data" / SETTINGS_OVERLAY_PATH
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(f"kiosk: {{hidden_scenes: [{IDLE}]}}\n", encoding="utf-8")
    _env(monkeypatch, tmp_path, CONDUCTOR_AUTH="off")
    with TestClient(app) as client:
        ids = [s["id"] for s in _panel(client)["scenes"]]
        lines = IM.problem_lines(client.app.state.services)
    assert ids == CATALOG
    kept = f"kiosk.hidden_scenes names '{IDLE}', the idle scene (kiosk.idle_mode): the iPad offers it anyway."
    assert kept in lines, lines


def test_the_7am_test_mode_change_on_the_real_engine(app_client_show):
    """The checklist's test mode change on the show's own engine in sim (not FakeEngine). With Calm #1 showing after
    start-up it changes to The Goodnight Glow #1 and every module reaches Ready within the window; then it changes
    back to Calm #1, still (D75: the change back never starts a run). On purpose: in sim the change itself lands on
    Goodnight's first rung, its breath (ops keep ladder order). On live hardware that rung is refused until Gate R and
    motion.stepped.live_enabled, and the change lands on module level."""
    from show_started import past_startup

    past_startup(app_client_show)
    services = app_client_show.app.state.services
    since = services.clock.now()
    result = app_client_show.post("/api/ops/checklist/test-mode-change", json={}).json()
    engine = services.extras["canopy"]
    plan, runner = engine.current_plan(), engine.runner
    changes = services.telemetry.recent(kind="mode", since=since, limit=5000)
    assert result["ok"] is True, result["message"]
    assert result["mode"] == "goodnight-glow" and result["elapsed_s"] <= 10
    assert all(m["ready_after_s"] is not None for m in result["modules"]), result["modules"]
    assert ("goodnight-glow", "stepped") in [(c["mode"], c["tier"]) for c in changes], changes
    assert result["restored_to"] == IDLE
    assert plan.mode_id == IDLE and plan.tier is Tier.T3 and plan.program is None
    assert runner is None or not runner.running


@pytest.mark.parametrize("option", OPTIONS)
def test_each_option_can_be_made_its_scene_without_a_recipe_key_clash(option):
    """'Make this the scene' (PR C's promote): the option's palette and solid on its base allocate one recipe key per
    colour without a clash (the Lab's own allocation, draft.recipe_keys)."""
    skip_unless_drafted(option)
    base, scene = BASE_OF[option], SCENES.get(option)
    data = D.seed_draft(D.load_trio(CONFIG_DIR), base).model_dump()
    data["palette"] = [{"name": c.name, "hex": c.hex} for c in scene.palette]
    data["solid"] = scene.default_solid
    promoted = D.SceneDraft.model_validate(data)
    keys = D.recipe_keys(MODES.mode(base), promoted, RECIPES, D.owners_of(MODES, RECIPES))
    assert len(keys) == len(set(keys)) == len(scene.palette)
    spot = {
        "golden-hour-2": [
            "golden-hour-warm-white",
            "golden-hour-honey",
            "golden-hour-home-depot-orange",
            "golden-hour-amber",
        ],
        "goodnight-glow-3": [
            "goodnight-glow-candle-amber",
            "goodnight-glow-dusky-mauve",
            "goodnight-glow-night-blue",
        ],
    }
    if option in spot:
        assert keys == spot[option]
