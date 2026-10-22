"""The extracted scene ↔ mode validator (conductor/looks/scene_rules.py, Design Lab PLAN §3.4).

The shipped config has no problem; each rule catches a crafted mutation of the shipped trio and only that rule does;
the validator's module-level colours and steps are the compiler's own; the two D63 design-quality rules only warn;
and the pins it computes are the committed fixture's."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from conductor.capabilities import detect
from conductor.config import RecipesFile, load_recipes, load_registry
from conductor.engine_api import Tier
from conductor.geometry import load_canopy_config
from conductor.looks import CompileContext, compile_mode, load_modes
from conductor.looks import scene_rules as R
from conductor.looks.schema import ModesFile
from conductor.scenes import SceneCatalog, load_scenes
from conductor.sim.profiles import profile_parts

SCENES, MODES, RECIPES, CANOPY = load_scenes(), load_modes(), load_recipes(), load_canopy_config()
IDS = [s.id for s in SCENES.scenes]
FIXTURE = Path(__file__).with_name("fixtures_scene_frames.json")
Mutation = Callable[[dict, dict, dict], None]


def _mutated(mutation: Mutation) -> tuple[SceneCatalog, ModesFile, RecipesFile]:
    """The shipped trio with one edit, built by model_validate of edited dumps (the modes schema only, not
    parse_modes' cross-check, so the validator is what finds it)."""
    scenes, modes, recipes = (
        SCENES.model_dump(mode="json"),
        MODES.model_dump(mode="json"),
        RECIPES.model_dump(mode="json"),
    )
    mutation(scenes, modes, recipes)
    return (
        SceneCatalog.model_validate(scenes),
        ModesFile.model_validate(modes),
        RecipesFile.model_validate(recipes),
    )


def _rules(mutation: Mutation, *, approved: ModesFile | None = None) -> set[str]:
    scenes, modes, recipes = _mutated(mutation)
    return {p.rule for p in R.scene_mode_problems(scenes, modes, recipes, CANOPY, approved=approved)}


def _technique(modes: dict, mode_id: str) -> dict:
    return modes["modes"][mode_id]["techniques"]


def _designs(modes: dict, mode_id: str) -> list[dict]:
    """Every canvas that draws the mode's one design (its canvas, stepped and modules rungs share it, D63/D65)."""
    t = _technique(modes, mode_id)
    return [t[rung]["canvas"] for rung in ("canvas", "stepped") if t.get(rung)] + [
        t["modules"]["sample"]["canvas"]
    ]


def _layout(mode_id: str, field: dict, moving: tuple[str, str] | None = None) -> Mutation:
    """One design (every rung that draws it) moved to another layout; `moving` (old, new) keeps its stepped rung's
    copy naming the new flow, so only the rule the layout breaks fires."""

    def mutate(scenes: dict, modes: dict, recipes: dict) -> None:
        for canvas in _designs(modes, mode_id):
            canvas["field"] = field
        if moving is not None:
            stepped = _technique(modes, mode_id)["stepped"]
            assert moving[0] in stepped["moving_feature"]
            stepped["moving_feature"] = stepped["moving_feature"].replace(*moving)

    return mutate


def _preview_off_by_one(scenes: dict, modes: dict, recipes: dict) -> None:
    modes["modes"]["golden-hour"]["preview"][0] = "#FFDB9C"  # soft gold is #FFDB9B


def _slot_at_94(scenes: dict, modes: dict, recipes: dict) -> None:
    _technique(modes, "golden-hour")["slot"]["brightness"] = 94  # the scene's is 95


def _slot_speed(scenes: dict, modes: dict, recipes: dict) -> None:
    _technique(modes, "garden-gathering")["slot"]["speed"] = 3


def _two_cycles(scenes: dict, modes: dict, recipes: dict) -> None:
    for canvas in _designs(
        modes, "lowcountry-calm-3"
    ):  # every rung: a stepped rung must step its modules design
        canvas["field"]["cycles"] = 2


def _no_strand_rung(scenes: dict, modes: dict, recipes: dict) -> None:
    modes["modes"]["rainbow-row-sunset"]["ladder"] = ["canvas", "slot", "stepped", "modules"]


def _strand_not_the_solid(scenes: dict, modes: dict, recipes: dict) -> None:
    _technique(modes, "coastal-tides")["strand"]["look"]["rgb"] = [
        101,
        155,
        213,
    ]  # ocean blue; the solid is aqua


def _slots_swapped(scenes: dict, modes: dict, recipes: dict) -> None:
    modes["modes"]["rainbow-row-sunset"]["slot"] = "custom-2"
    modes["modes"]["garden-gathering"]["slot"] = "custom-1"


def _stop_at_0_4(scenes: dict, modes: dict, recipes: dict) -> None:
    for canvas in _designs(modes, "garden-gathering-2"):  # its four stops: 1/3 → 0.4, on every rung
        canvas["stops"][1]["at"] = 0.4


def _blend_with_white(scenes: dict, modes: dict, recipes: dict) -> None:
    recipes["colors"]["sea-glass"]["blend"]["whiteBrightness"] = 20


def _moving_copy_names_another_flow(scenes: dict, modes: dict, recipes: dict) -> None:
    stepped = _technique(modes, "rainbow-row-sunset")[
        "stepped"
    ]  # a travel rung (Coastal Tides #1 breathes, D77)
    stepped["moving_feature"] = stepped["moving_feature"].replace("diagonally", "row by row")


def _stepped_above_the_canvas(scenes: dict, modes: dict, recipes: dict) -> None:
    modes["modes"]["golden-hour"]["ladder"] = ["stepped", "canvas", "slot", "modules", "strand"]


LEVEL_BREATH_COPY = (
    "Colour and dimming through the Hubspace cloud: each Hubspace strand set to a single colour and sent a brighter "
    "or softer level every few seconds, so the canopy swells and softens."
)


def _breathe_tides(**params) -> Mutation:
    """Coastal Tides' rung as a level breath (D75): no colour shift, every strand together (lag 0)."""

    def mutate(scenes: dict, modes: dict, recipes: dict) -> None:
        stepped = _technique(modes, "coastal-tides")["stepped"]
        stepped.update(mode="breathe", shift=0.0, lag_steps=0, moving_feature=LEVEL_BREATH_COPY, **params)

    return mutate


SHIMMER_COPY = (
    "Colour and dimming through the Hubspace cloud: each Hubspace strand set to a single colour and sent a brighter "
    "or softer level every few seconds, so the strands glow and soften on their own."
)


def _shimmer_tides(**params) -> Mutation:
    """Coastal Tides' rung as a level shimmer (D75): each module on its own seeded start."""

    def mutate(scenes: dict, modes: dict, recipes: dict) -> None:
        stepped = _technique(modes, "coastal-tides")["stepped"]
        stepped.update(mode="shimmer", shift=0.0, moving_feature=SHIMMER_COPY, **params)

    return mutate


MUTATIONS: dict[
    str, tuple[str, Mutation, bool]
] = {  # id → (rule, mutation, check against the approved modes)
    "preview-hex-off-by-one": ("catalog", _preview_off_by_one, False),
    "slot-brightness-94": ("brightness", _slot_at_94, False),
    "slot-speed": ("static", _slot_speed, False),
    "cycles-2": ("static", _two_cycles, False),
    "ladder-without-strand": ("ladder-end", _no_strand_rung, False),
    "strand-not-the-solid": ("ladder-end", _strand_not_the_solid, False),
    "slots-swapped": ("slots", _slots_swapped, True),
    "stop-at-0.4": ("one-design", _stop_at_0_4, False),
    "blend-with-white": ("recipes", _blend_with_white, False),
    "moving-copy-another-flow": ("stepped", _moving_copy_names_another_flow, False),
    "stepped-above-the-canvas": ("ladder-end", _stepped_above_the_canvas, False),
    # levels 85, 86, 86, 85, 84, 85: steps 1 and 2 send nothing new
    "breath-repeats-a-step": ("stepped", _breathe_tides(brightness_swing=1, steps_per_cycle=6), False),
    # seed 183 at lag_steps 1 starts all nine modules together: one breath, under "on their own" (review B-R6-1)
    "shimmer-starts-together": ("stepped", _shimmer_tides(brightness_swing=6, lag_steps=1, seed=183), False),
    "linear-0": (
        "two-d",
        _layout("rainbow-row-sunset", {"kind": "linear", "angle_deg": 0}, ("diagonally", "column by column")),
        False,
    ),
    "kits-equal": (
        "kits-differ",
        _layout(
            "garden-gathering",
            {"kind": "radial", "centre_in": [75, 0]},
            ("toward one spot", "toward one edge"),
        ),
        False,
    ),
}


def test_the_shipped_config_has_no_problem():
    assert R.scene_mode_problems(SCENES, MODES, RECIPES, CANOPY, approved=MODES) == []
    assert set(R.RULES) == set(R.BLOCKING) | set(R.WARNINGS) and not set(R.BLOCKING) & set(R.WARNINGS)


def test_every_rule_has_a_mutation():
    assert {rule for rule, _, _ in MUTATIONS.values()} == set(R.RULES)


@pytest.mark.parametrize("case", list(MUTATIONS))
def test_each_rule_catches_its_mutation(case):
    rule, mutation, with_approved = MUTATIONS[case]
    assert _rules(mutation, approved=MODES if with_approved else None) == {rule}
    scenes, modes, recipes = _mutated(mutation)
    found = R.scene_mode_problems(scenes, modes, recipes, CANOPY, rules={rule}, approved=MODES)
    assert found and all(p.rule == rule and str(p).startswith(f"mode {p.mode!r} → ") for p in found)


def test_the_new_d75_mutations_say_why():
    scenes, modes, recipes = _mutated(MUTATIONS["stepped-above-the-canvas"][1])
    (ladder,) = R.scene_mode_problems(scenes, modes, recipes, CANOPY, rules={"ladder-end"})
    assert ladder.mode == "golden-hour" and "must sit just before [modules, strand]" in ladder.message
    scenes, modes, recipes = _mutated(MUTATIONS["breath-repeats-a-step"][1])
    (stepped,) = R.scene_mode_problems(scenes, modes, recipes, CANOPY, rules={"stepped"})
    assert stepped.mode == "coastal-tides" and "steps 0 and 1 show the same picture" in stepped.message
    levels = [p["r1c1"][1] for p in R.step_pictures(modes.mode("coastal-tides"), recipes, CANOPY)]
    assert levels == [100, 100, 99, 100, 99, 99]  # at 100 % the breath dips (compile.breath_level)


def test_a_shimmer_whose_modules_all_start_together_is_blamed_on_its_seed():
    """Review B-R6-1: the blocking rules refuse a lagged breath whose nine starts are equal (parse_modes' check, run
    here over a trio built without it), on the field that collapses them; another seed is the control."""
    scenes, modes, recipes = _mutated(MUTATIONS["shimmer-starts-together"][1])
    (together,) = R.scene_mode_problems(scenes, modes, recipes, CANOPY, rules=R.BLOCKING)
    assert (together.rule, together.mode, together.path) == (
        "stepped",
        "coastal-tides",
        "techniques → stepped → seed",
    )
    assert together.message.startswith("lag_steps 1 with seed 183 starts every module together")
    # every step shows all nine at one level: the canopy breathes as one
    pictures = R.step_pictures(modes.mode("coastal-tides"), recipes, CANOPY)
    assert all(len({level for _, level in step.values()}) == 1 for step in pictures)
    spread = _mutated(_shimmer_tides(brightness_swing=6, lag_steps=1, seed=7))
    assert R.scene_mode_problems(*spread, CANOPY, rules=R.BLOCKING) == []


def test_a_breathe_variant_steps_its_brightness_and_keeps_its_colours():
    scenes, modes, recipes = _mutated(_breathe_tides(brightness_swing=6, steps_per_cycle=6))
    assert R.scene_mode_problems(scenes, modes, recipes, CANOPY, rules=R.BLOCKING) == []
    mode = modes.mode("coastal-tides")
    pictures = R.step_pictures(mode, recipes, CANOPY)
    colours = R.module_colours(mode, recipes, CANOPY)
    assert [{p[1] for p in step.values()} for step in pictures] == [{100}, {98}, {97}, {100}, {94}, {97}]
    assert all({pos: rgb for pos, (rgb, _) in step.items()} == colours for step in pictures)
    assert R.step_frames(mode, recipes, CANOPY) == [
        {pos: rgb for pos, (rgb, _) in s.items()} for s in pictures
    ]


def test_moved_slots_need_the_approved_modes_to_be_seen():
    """Slots swapped between two painted scenes still give each painted scene one slot: only the comparison with
    the approved (git) modes catches it. The old literal test (test_modes_match_scenes) pins the map as well."""
    assert _rules(_slots_swapped) == set()
    assert _rules(_slots_swapped, approved=MODES) == {"slots"}


def test_a_ladder_change_against_the_approved_modes_is_a_slots_problem():
    scenes, modes, recipes = _mutated(_no_strand_rung)
    found = R.scene_mode_problems(scenes, modes, recipes, CANOPY, approved=MODES, rules={"slots"})
    assert [(p.mode, p.path) for p in found] == [("rainbow-row-sunset", "ladder")]


def test_two_d_and_kits_differ_are_warnings_not_problems():
    stripes = _mutated(MUTATIONS["linear-0"][1])
    assert R.scene_mode_problems(*stripes, CANOPY, approved=MODES, rules=R.BLOCKING) == []
    (warning,) = R.scene_mode_problems(*stripes, CANOPY, approved=MODES, rules=R.WARNINGS)
    assert warning.rule == "two-d" and warning.message.startswith(R.STRIPES)
    assert warning.path.endswith("→ field") and warning.mode == "rainbow-row-sunset"
    kits = _mutated(MUTATIONS["kits-equal"][1])
    assert R.scene_mode_problems(*kits, CANOPY, rules=R.BLOCKING) == []
    (warning,) = R.scene_mode_problems(*kits, CANOPY, rules=R.WARNINGS)
    assert warning.rule == "kits-differ" and warning.message.startswith(R.SAME_KITS)
    assert set(R.WARNING_TESTS) == set(R.WARNINGS)


def test_an_unknown_rule_is_refused():
    with pytest.raises(ValueError, match="unknown rule"):
        R.scene_mode_problems(SCENES, MODES, RECIPES, CANOPY, rules={"catalogue"})


def test_problems_come_in_catalog_order_and_serialise():
    def two(scenes: dict, modes: dict, recipes: dict) -> None:
        _slot_at_94(scenes, modes, recipes)
        _preview_off_by_one(scenes, modes, recipes)
        _technique(modes, "rainbow-row-sunset")["slot"]["brightness"] = 50

    found = R.scene_mode_problems(*_mutated(two), CANOPY)
    assert [p.mode for p in found] == ["rainbow-row-sunset", "golden-hour", "golden-hour"]
    assert found[0].to_json() == {
        "rule": "brightness",
        "mode": "rainbow-row-sunset",
        "path": "techniques → slot → brightness",
        "message": found[0].message,
    }


# ---- the validator and the old assertions agree ---------------------------------------------------------------------
def _compiled_t3(mode_id: str) -> dict[str, tuple[int, int, int]]:
    """Module position → the RGB the compiled module-level plan sends there (test_modes_match_scenes' _t3)."""
    registry = load_registry()
    caps = detect(*profile_parts("sqhdk03-sim"))
    ctx = CompileContext(registry=registry, capabilities={m.id: caps for m in registry.canopy_modules()})
    plan = compile_mode(mode_id, Tier.T3, ctx)
    out = {}
    for m in registry.canopy_modules():
        (rgb,) = [r.value for r in plan.modules[m.id].rows if r.function_class == "color-rgb"]
        out[m.module] = (rgb["color-rgb"]["r"], rgb["color-rgb"]["g"], rgb["color-rgb"]["b"])
    return out


@pytest.mark.parametrize("mode_id", IDS)
def test_the_validator_and_the_old_assertions_agree(mode_id):
    import test_modes_match_scenes as old

    mode = MODES.mode(mode_id)
    colours = R.module_colours(mode, RECIPES, CANOPY)
    assert colours == _compiled_t3(mode_id) == old._t3(mode_id)
    for a in colours.values():
        for b in colours.values():
            assert R.delta_e76(a, b) == old._delta_e(a, b)
    if mode.techniques.stepped is not None:
        registry = load_registry()
        caps = detect(*profile_parts("sqhdk03-sim"))
        ctx = CompileContext(registry=registry, capabilities={m.id: caps for m in registry.canopy_modules()})
        steps = compile_mode(mode_id, Tier.STEPPED, ctx).program.steps
        frames = R.step_frames(mode, RECIPES, CANOPY)
        assert [{p: "#{:02X}{:02X}{:02X}".format(*rgb) for p, rgb in f.items()} for f in frames] == [
            s.colours for s in steps
        ]
    else:
        assert R.step_frames(mode, RECIPES, CANOPY) == []


def test_delta_e76_is_zero_for_equal_colours_and_large_for_black_and_white():
    assert R.delta_e76((10, 20, 30), (10, 20, 30)) == 0
    assert R.delta_e76((0, 0, 0), (255, 255, 255)) == pytest.approx(100, abs=0.1)


# ---- pins ----------------------------------------------------------------------------------------------------------
def test_the_pins_are_the_committed_fixtures():
    pins = R.scene_pins(SCENES, MODES, RECIPES, CANOPY)
    assert json.loads(FIXTURE.read_text())["_pins"] == json.loads(json.dumps(pins))
    assert list(pins) == IDS
    golden = pins["golden-hour"]
    assert golden["solid"] == SCENES.get("golden-hour").solid.name
    assert golden["brightness"] == SCENES.get("golden-hour").brightness_draft
    assert golden["t3"] == {
        p: list(c) for p, c in R.module_colours(MODES.mode("golden-hour"), RECIPES, CANOPY).items()
    }
    # keys only: no approved "travel" value is pinned outside _pins (the Lab round-trip rule)
    assert set(golden["stepped"]) == {
        "direction",
        "mode",
        "steps_per_cycle",
        "step_s",
        "run_s",
        "shift",
        "brightness_swing",
        "lag_steps",
        "seed",
    }
    # D77: every scene moves, the idle look's breath included (its pin is its own rung's, not an approved value)
    assert all("stepped" in pin and "flow_words" in pin for pin in pins.values())
    calm = MODES.mode("lowcountry-calm").techniques.stepped
    assert pins["lowcountry-calm"]["stepped"]["mode"] == calm.mode
