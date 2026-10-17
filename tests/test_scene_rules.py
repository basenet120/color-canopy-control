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


def _layout(mode_id: str, field: dict) -> Mutation:
    """One design (every rung that draws it) moved to another layout."""

    def mutate(scenes: dict, modes: dict, recipes: dict) -> None:
        t = _technique(modes, mode_id)
        for rung in ("canvas", "stepped"):
            if t.get(rung):
                t[rung]["canvas"]["field"] = field
        t["modules"]["sample"]["canvas"]["field"] = field

    return mutate


def _preview_off_by_one(scenes: dict, modes: dict, recipes: dict) -> None:
    modes["modes"]["golden-hour"]["preview"][0] = "#FFDB9C"  # soft gold is #FFDB9B


def _slot_at_94(scenes: dict, modes: dict, recipes: dict) -> None:
    _technique(modes, "golden-hour")["slot"]["brightness"] = 94  # the scene's is 95


def _slot_speed(scenes: dict, modes: dict, recipes: dict) -> None:
    _technique(modes, "garden-gathering")["slot"]["speed"] = 3


def _two_cycles(scenes: dict, modes: dict, recipes: dict) -> None:
    _technique(modes, "lowcountry-calm")["modules"]["sample"]["canvas"]["field"]["cycles"] = 2


def _no_strand_rung(scenes: dict, modes: dict, recipes: dict) -> None:
    modes["modes"]["rainbow-row-sunset"]["ladder"] = ["canvas", "slot", "modules"]


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
    _technique(modes, "goodnight-glow")["modules"]["sample"]["canvas"]["stops"][1]["at"] = 0.4


def _blend_with_white(scenes: dict, modes: dict, recipes: dict) -> None:
    recipes["colors"]["sea-glass"]["blend"]["whiteBrightness"] = 20


def _moving_copy_names_another_flow(scenes: dict, modes: dict, recipes: dict) -> None:
    stepped = _technique(modes, "coastal-tides")["stepped"]
    stepped["moving_feature"] = stepped["moving_feature"].replace("diagonally", "row by row")


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
    "linear-0": ("two-d", _layout("rainbow-row-sunset", {"kind": "linear", "angle_deg": 0}), False),
    "kits-equal": (
        "kits-differ",
        _layout("garden-gathering", {"kind": "radial", "centre_in": [75, 0]}),
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
    assert set(golden["stepped"]) == {"direction", "steps_per_cycle", "step_s", "run_s"}
    assert "stepped" not in pins["lowcountry-calm"] and "flow_words" not in pins["lowcountry-calm"]
