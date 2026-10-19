"""Breathe and shimmer stepped looks (Tier.STEPPED, D75) compiled from tests/fixtures_modes_breathe.yaml: the design
stays in place and each module's colour (`shift`) and level (`brightness_swing`) breathe, one breath of
`steps_per_cycle` steps per module at its own lag; step 0 is the still design; every step is one full state vector
per driven module with its brightness in it; the moving copy says what changes. Travel (D65) is
tests/test_compile_stepped.py's."""

from __future__ import annotations

import copy
import hashlib
import itertools
import math
from pathlib import Path

import pytest
import yaml
from fake_compiler import make_world

from conductor import rows as R
from conductor.capabilities import detect
from conductor.clock import run_virtual
from conductor.config import RecipesFile, load_recipes, load_registry
from conductor.engine_api import PlanError, Tier
from conductor.geometry import load_canopy_config
from conductor.looks import CompileContext, compile_mode, ladder, parse_modes
from conductor.looks import field as F
from conductor.looks.compile import (
    _hex,
    _sampled_rgb,
    breath_wave,
    module_lags,
    program_cycle,
    step_looks,
)
from conductor.looks.composer import evaluate, recipe_entry
from conductor.looks.schema import FieldCanvas, ModuleSample
from conductor.sequence import GRID
from conductor.sim.profiles import profile_parts

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


# ---- the steps (compile) --------------------------------------------------------------------------------------------
STEPPED_PATH = Path(__file__).with_name("fixtures_modes_stepped.yaml")
RAW = yaml.safe_load(PATH.read_text(encoding="utf-8"))
BREATHE = parse_modes(RAW, recipes=RECIPES, canopy=CANOPY, path=PATH)
TRAVEL = parse_modes(
    yaml.safe_load(STEPPED_PATH.read_text(encoding="utf-8")),
    recipes=RECIPES,
    canopy=CANOPY,
    path=STEPPED_PATH,
)
REGISTRY = load_registry()
MODULES = REGISTRY.canopy_modules()
BY_POSITION = {m.module: m for m in MODULES}
CAPS = detect(*profile_parts("sqhdk03-sim"))
METADEVICE_ID = "0" * 8 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 12
ROW_KEYS = [
    ("power", None),
    ("color-mode", None),
    ("color-rgb", None),
    ("brightness", None),
    ("speed", "color-sequence"),
    ("toggle", "party-mode"),
]
EDGES = ("r0c1", "r1c0", "r1c2", "r2c1")
CORNERS = ("r0c0", "r0c2", "r2c0", "r2c2")


def _ctx(modes=BREATHE, **kw) -> CompileContext:
    return CompileContext(
        registry=REGISTRY, capabilities={m.id: CAPS for m in MODULES}, modes=modes, recipes=RECIPES, **kw
    )


def _partial(driven=("r0c0", "r0c1"), modes=BREATHE) -> CompileContext:
    return _ctx(modes, addresses={m.id: (f"md-{m.id}" if m.module in driven else None) for m in MODULES})


def _with(mode_id: str, **over):
    """The breathe fixture with one mode's stepped technique changed."""
    raw = copy.deepcopy(RAW)
    raw["modes"][mode_id]["techniques"]["stepped"].update(over)
    return parse_modes(raw, recipes=RECIPES, canopy=CANOPY, path=PATH)


def _tech(mode_id: str, modes=BREATHE):
    return modes.mode(mode_id).techniques.stepped


def _rgb(entry) -> tuple[int, int, int]:
    """A frame entry as the module's one RGB colour (rgb_only: the colour level dims the RGB itself)."""
    return tuple(math.floor(v * entry.colorBrightness / 100 + 0.5) for v in (entry.r, entry.g, entry.b))


def _brightness(rows) -> int:
    return next(r.value for r in rows if r.key == ("brightness", None))


@pytest.mark.parametrize("n", range(3, 13))
def test_the_breath_wave_peaks_on_a_whole_step_and_never_repeats_a_step(n):
    wave = [breath_wave(k, n) for k in range(n)]
    assert wave[0] == 0 and max(wave) == 1 and min(wave) == -1
    assert all(a != b for a, b in itertools.pairwise(wave)) and wave[-1] != 0
    assert breath_wave(-1, n) == breath_wave(n, n) == 0
    if n == 4:
        assert wave == [0, 1, 0, -1]
    if n == 6:
        assert wave == [0, 0.5, 1, 0, -1, -0.5]


@pytest.mark.parametrize("mode_id", list(BREATHE.modes))
def test_step_0_of_every_breathe_mode_is_the_still_design(mode_id):
    plan = compile_mode(mode_id, Tier.STEPPED, _ctx())
    hold = compile_mode(mode_id, Tier.T3, _ctx())
    tech = _tech(mode_id)
    assert plan.program.steps[0].rows == {d: t.rows for d, t in hold.modules.items()}
    assert set(plan.program.steps[0].levels.values()) == {tech.brightness}
    assert plan.program.mode == tech.mode and plan.program.to_json()["mode"] == tech.mode
    assert plan.motion is False  # the Conductor holds the phase (07 §6's burst is for on-device motion)
    assert plan.feature == hold.feature and plan.program.moving_feature == tech.moving_feature
    assert plan.to_json()["program"]["steps"][1]["levels"] == plan.program.steps[1].levels


def test_each_breathe_step_is_one_full_state_vector_carrying_its_brightness():
    plan = compile_mode("breathe-level", Tier.STEPPED, _ctx())
    steps = plan.program.steps
    assert [s.index for s in steps] == [0, 1, 2, 3]
    largest = 0
    for step, level in zip(steps, [28, 32, 28, 24], strict=True):
        assert sorted(step.rows) == sorted(m.id for m in MODULES)
        for m in MODULES:
            rows = step.rows[m.id]
            assert [r.key for r in rows] == ROW_KEYS  # never a slot object (no color-sequence-v2)
            assert all(r.function_class != "color-sequence-v2" for r in rows)
            assert _brightness(rows) == level == step.levels[m.module]
            largest = max(largest, R.payload_bytes(METADEVICE_ID, rows, 1_790_000_000_000))
        assert step.colours == steps[0].colours  # shift 0: every strand keeps its colour
    assert largest < 700
    assert any(
        "Stepped, breathing: 4 steps a breath of 6 s (up to 0 steps apart), a cycle of 4" in n
        for n in plan.notes
    )


def test_a_colour_shift_is_clamped_never_wrapped():
    tech = _tech("breathe-rings").model_copy(update={"shift": 0.5})
    looks = step_looks(tech, MODULES, _ctx())
    coral, blush = (_rgb(recipe_entry(RECIPES, name, "rgb_only")) for name in ("pastel-coral", "blush-pink"))
    assert coral != blush
    # r1c1 sits at t = 0 with lag 0. Step 1 (w = +1, forward: offset −0.5) clamps to t = 0, pastel-coral; a wrap
    # would have given t = ½, blush-pink
    assert looks[1]["r1c1"].rgb == coral == looks[0]["r1c1"].rgb
    # step 3 (w = −1: offset +0.5) is t = ½ exactly, blush-pink
    assert looks[3]["r1c1"].rgb == blush


def test_forward_and_reverse_move_the_colour_opposite_ways():
    tech = _tech("breathe-level").model_copy(update={"shift": 0.15})
    canvas = tech.canvas
    middle = BY_POSITION["r1c1"]  # the middle column, t = ½
    forward = step_looks(tech, MODULES, _ctx())
    reverse = step_looks(tech.model_copy(update={"direction": "reverse"}), MODULES, _ctx())
    assert forward[1]["r1c1"].rgb == _rgb(_at(canvas, 0.35 * 225))  # w = +1: t − 0.15
    assert reverse[1]["r1c1"].rgb == _rgb(_at(canvas, 0.65 * 225))  # w = +1: t + 0.15
    assert forward[3]["r1c1"].rgb == reverse[1]["r1c1"].rgb  # w = −1 forward is w = +1 reverse
    assert forward[1][middle.module].brightness == 32  # the level swings the same way in both


def test_the_design_start_leads_and_the_cycle_grows_by_the_largest_lag():
    tech = _tech("breathe-rings")
    lags = module_lags(tech, GRID, CANOPY)
    assert lags == {"r1c1": 0, **dict.fromkeys(EDGES, 2), **dict.fromkeys(CORNERS, 3)}
    plan = compile_mode("breathe-rings", Tier.STEPPED, _ctx())
    steps = plan.program.steps
    assert len(steps) == 7 == program_cycle(tech, lags)
    corner = BY_POSITION["r0c0"].id
    assert next(n for n, s in enumerate(steps) if s.rows[corner] != steps[0].rows[corner]) == 4
    for n, step in enumerate(steps):
        before = steps[n - 1]  # step 0 follows the cycle's last step
        assert any(step.rows[d] != before.rows[d] for d in step.rows), n
    assert any(
        "Stepped, breathing: 4 steps a breath of 6 s (up to 3 steps apart), a cycle of 7" in n
        for n in plan.notes
    )


def test_a_lagged_breaths_starts_count_on_the_design_before_repeat_mirror_and_invert():
    """D75 (review B-R2-TS-5): lags come from the design's own t before repeat, mirror and invert (`module_lags`).
    That is what lets a lagged breathe's copy name its forward start words (schema._stepped_problems): a design
    repeated twice, mirrored and inverted still starts its breath from one edge, column by column. Lags read off the
    folded t would start both outer columns together and the middle last."""
    folded = {"kind": "linear", "angle_deg": 0, "transform": "mirror", "invert": True, "cycles": 2}
    tech = _tech("breathe-level").model_copy(update={"lag_steps": 4, "canvas": _linear(**folded)})
    column = {pos: int(pos[-1]) for pos in GRID}
    lags = module_lags(tech, GRID, CANOPY)
    assert lags == column  # t = 1/6, 1/2, 5/6: 4t half-up is 1, 2, 3, less the first
    plain = tech.model_copy(update={"canvas": _linear()})
    assert lags == module_lags(plain, GRID, CANOPY)  # the plain design's
    centres = F.module_centres(CANOPY, GRID)
    raw = {p: math.floor(4 * F.value(tech.canvas.field, x, y, CANOPY) + 0.5) for p, (x, y) in centres.items()}
    on_folded = {p: v - min(raw.values()) for p, v in raw.items()}
    assert on_folded == {p: 3 if c == 1 else 0 for p, c in column.items()} != lags  # folded t: 1/3, 1, 1/3
    # compiled, with the copy the schema allows: each column starts one step after the one before, from c0
    raw_modes = copy.deepcopy(RAW)
    techniques = raw_modes["modes"]["breathe-level"]["techniques"]
    canvas = {**techniques["stepped"]["canvas"], "field": folded}
    techniques["stepped"].update(canvas=canvas, lag_steps=4)
    techniques["modules"]["sample"]["canvas"] = canvas
    modes = parse_modes(raw_modes, recipes=RECIPES, canopy=CANOPY, path=PATH)
    steps = compile_mode("breathe-level", Tier.STEPPED, _ctx(modes)).program.steps
    ids = {p: BY_POSITION[p].id for p in GRID}
    first = {p: next(n for n, s in enumerate(steps) if s.rows[d] != steps[0].rows[d]) for p, d in ids.items()}
    assert first == {p: 1 + c for p, c in column.items()}


def _shimmer_lags(seed: int, lag: int, positions) -> dict[str, int]:
    raw = {p: int(hashlib.sha256(f"{seed}:{p}".encode()).hexdigest(), 16) % (lag + 1) for p in positions}
    return {p: v - min(raw.values()) for p, v in raw.items()}


def test_shimmer_lags_are_seeded_repeatable_and_start_at_zero():
    tech = _tech("shimmer-seeded")
    assert compile_mode("shimmer-seeded", Tier.STEPPED, _ctx()).program == (
        compile_mode("shimmer-seeded", Tier.STEPPED, _ctx()).program
    )
    seven = module_lags(tech, GRID, CANOPY)
    eight = module_lags(tech.model_copy(update={"seed": 8}), GRID, CANOPY)
    assert seven != eight
    assert all(0 <= v <= 3 for v in seven.values()) and min(seven.values()) == 0
    assert seven == _shimmer_lags(7, 3, GRID)
    plan = compile_mode("shimmer-seeded", Tier.STEPPED, _ctx())
    assert len(plan.program.steps) == 5 + max(seven.values())
    assert plan.program.mode == "shimmer"
    assert any(n.startswith("Stepped, shimmering: 5 steps a breath") for n in plan.notes)


def test_on_the_two_kits_lags_count_from_the_kits_and_the_copy_names_what_changes():
    tech = _tech("breathe-rings")
    assert module_lags(tech, ["r0c0", "r0c1"], CANOPY) == {"r0c0": 1, "r0c1": 0}
    plan = compile_mode("breathe-rings", Tier.STEPPED, _partial())
    assert len(plan.program.steps) == 5
    assert plan.program.moving_feature == (
        "Colour and dimming through the Hubspace cloud: both Hubspace strands, each showing one colour from the scene "
        "and sent its next colour and a brighter or softer level every few seconds."
    )
    level = compile_mode("breathe-level", Tier.STEPPED, _partial())
    assert level.program.moving_feature == (
        "Colour and dimming through the Hubspace cloud: both Hubspace strands, each showing one colour from the scene "
        "and sent a brighter or softer level every few seconds."
    )
    one = compile_mode("breathe-level", Tier.STEPPED, _partial(("r1c1",)))
    assert one.program.moving_feature == (
        "Colour and dimming through the Hubspace cloud: one Hubspace strand showing one colour from the scene, sent "
        "a brighter or softer level every few seconds."
    )
    travel = compile_mode("radial-outward", Tier.STEPPED, _partial(modes=TRAVEL))
    assert travel.program.moving_feature.endswith("and sent its next colour every few seconds.")


def _resting_seed() -> int:
    """The first seed whose two kit lags (steps_per_cycle 3, lag_steps 6) are at least 4 apart: their breaths of 3
    steps never meet, so some step sends neither kit anything."""
    for seed in range(1000):
        lags = _shimmer_lags(seed, 6, ["r0c0", "r0c1"])
        if abs(lags["r0c0"] - lags["r0c1"]) >= 4:
            return seed
    raise AssertionError("no seed in range(1000)")


def test_a_breath_that_would_rest_on_every_driven_strand_holds_still():
    modes = _with("shimmer-seeded", steps_per_cycle=3, lag_steps=6, seed=_resting_seed())
    with pytest.raises(PlanError, match="would send none of them anything"):
        compile_mode("shimmer-seeded", Tier.STEPPED, _partial(modes=modes))
    ctx = _partial(modes=modes)

    async def main():
        w = make_world()
        w.engine.deps.compile = lambda mode_id, tier: compile_mode(mode_id, tier, ctx)
        w.engine.deps.ladder = lambda mode_id: ladder(mode_id, modes)
        w.engine.deps.modes = lambda: list(modes.modes)
        best, _ = w.engine.compile_best("shimmer-seeded")
        await w.engine.request_mode("shimmer-seeded", source="ops")
        out = (best.tier, w.engine.current_plan().tier, w.engine.runner)
        await w.close()
        return out

    best, shown, runner = run_virtual(main)
    assert best is Tier.T3 and shown is Tier.T3 and runner is None


@pytest.mark.parametrize("mode_id", list(TRAVEL.modes))
def test_travel_steps_are_exactly_the_phase_samplers(mode_id):
    tech = _tech(mode_id, TRAVEL)
    plan = compile_mode(mode_id, Tier.STEPPED, _ctx(TRAVEL))
    ctx = _ctx(TRAVEL)
    sample = ModuleSample(canvas=tech.canvas, at="centre", brightness=tech.brightness)
    sign = -1.0 if tech.direction == "forward" else 1.0
    assert len(plan.program.steps) == tech.steps_per_cycle and plan.program.mode == "travel"
    for n, step in enumerate(plan.program.steps):
        for m in MODULES:
            want = _sampled_rgb(sample, m, ctx, phase=sign * n / tech.steps_per_cycle)
            assert step.colours[m.module] == _hex(want)
        assert set(step.levels.values()) == {tech.brightness}
