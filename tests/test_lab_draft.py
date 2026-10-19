"""The Design Lab's drafts (conductor/design_lab/draft.py, PLAN §3.1–§3.3): a draft is a patch over the parsed config
that moves every rung of one scene's mode in lockstep, keeps ids, ladders and slots, allocates recipe keys exactly,
and keeps the kiosk copy true (approved while it still describes the design, else generated from the layout's own
words), all re-validated through the show's own schema and load checks.

Pure: no I/O beyond reading config/ once, no clock, no engine. Settings come from `settings_with` (tests/fake_compiler.py).
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from fake_compiler import settings_with
from pydantic import ValidationError

from conductor.config import CONFIG_DIR, ConductorSettings, load_recipes
from conductor.design_lab import draft as D
from conductor.engine_api import Tier
from conductor.geometry import load_canopy_config
from conductor.looks import CompileContext, compile_mode, load_modes, scene_rules
from conductor.looks import field as F
from conductor.looks.schema import (
    MOTION_PHRASES,
    MOTION_WORDS,
    STEPPED_FALSE_CLAIMS,
    STEPPED_PACE,
    motion_words,
)
from conductor.scenes import load_scenes

# The approved config of 2026-10-16, frozen (tests/fixtures_lab_baseline/): these tests pin its values, so they keep
# passing after a Lab export changes config/ (make lab-roundtrip). The live config is checked where it matters.
BASELINE = Path(__file__).resolve().parent / "fixtures_lab_baseline"
TRIO = D.load_trio(BASELINE)
CANOPY = load_canopy_config(CONFIG_DIR / "canopy.yaml")
SETTINGS = ConductorSettings()
IDS = [s.id for s in TRIO.scenes.scenes]
PAINTED = [m for m in IDS if TRIO.modes.mode(m).slot is not None]
STEPPED = [m for m in IDS if TRIO.modes.mode(m).techniques.stepped is not None]
DONE_MEANS = {  # the brief's Done-means edit of Golden Hour: one colour, the layout's angle, the brightness
    "palette.2.hex": "#FF8FBF",
    "layout": {"kind": "linear", "angle_deg": 30},
    "brightness": 90,
}


def edit(scene_id: str, **changes) -> D.SceneDraft:
    """The approved scene's draft with some fields changed (`palette.<i>.<f>` and `stepped.<f>` reach inside)."""
    data = D.seed_draft(TRIO, scene_id).model_dump()
    for key, value in changes.items():
        parts = key.split(".")
        target = data
        for p in parts[:-1]:
            target = target[int(p)] if p.isdigit() else target[p]
        target[parts[-1]] = value
    return D.SceneDraft.model_validate(data)


def with_palette(scene_id: str, palette: list[tuple[str, str]], **changes) -> D.SceneDraft:
    return edit(scene_id, palette=[{"name": n, "hex": h} for n, h in palette], **changes)


def apply(*drafts: D.SceneDraft, settings: ConductorSettings = SETTINGS) -> D.ConfigTrio:
    return D.apply_drafts(TRIO, {d.scene_id: d for d in drafts}, canopy=CANOPY, settings=settings)


def problems(*drafts: D.SceneDraft, settings: ConductorSettings = SETTINGS) -> list[D.Problem]:
    with pytest.raises(D.DraftError) as exc:
        apply(*drafts, settings=settings)
    return exc.value.problems


def stop_keys(trio: D.ConfigTrio, scene_id: str) -> list[str]:
    return [s.color for s in trio.modes.mode(scene_id).techniques.modules.sample.canvas.stops]


def others_equal(patched: D.ConfigTrio, *changed: str) -> None:
    """Every scene and mode not drafted dumps equal to the approved one."""
    for sid in IDS:
        if sid in changed:
            continue
        assert patched.scenes.get(sid).model_dump() == TRIO.scenes.get(sid).model_dump(), sid
        assert patched.modes.mode(sid).model_dump() == TRIO.modes.mode(sid).model_dump(), sid
        for key in stop_keys(TRIO, sid):
            assert patched.recipes.colors[key] == TRIO.recipes.colors[key], key


# ---- seed and apply ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("scene_id", IDS)
def test_the_seed_of_every_scene_applies_to_the_approved_trio_exactly(scene_id):
    draft = D.seed_draft(TRIO, scene_id)
    assert D.is_approved(TRIO, draft)
    patched = D.apply_draft(TRIO, draft, canopy=CANOPY, settings=SETTINGS)
    assert patched.scenes.model_dump() == TRIO.scenes.model_dump()
    assert patched.modes.model_dump() == TRIO.modes.model_dump()
    assert patched.recipes.model_dump() == TRIO.recipes.model_dump()


def test_all_six_seeds_at_once_reproduce_the_approved_trio_and_the_shows_own_loaders():
    patched = apply(*(D.seed_draft(TRIO, s) for s in IDS))
    assert patched.dump() == TRIO.dump()
    # the live config too, which is exactly what the show compiles (load_trio reads through the show's own loaders)
    live = D.load_trio(CONFIG_DIR)
    seeds = {s.id: D.seed_draft(live, s.id) for s in live.scenes.scenes}
    assert all(D.is_approved(live, d) for d in seeds.values())
    patched = D.apply_drafts(live, seeds, canopy=CANOPY, settings=SETTINGS)
    assert patched.dump() == live.dump()
    assert patched.modes.model_dump() == load_modes(CONFIG_DIR).model_dump()
    assert patched.scenes.model_dump() == load_scenes(CONFIG_DIR).model_dump()
    assert patched.recipes.model_dump() == load_recipes(CONFIG_DIR).model_dump()


def test_the_seed_is_the_scene_and_its_design():
    draft = D.seed_draft(TRIO, "golden-hour")
    assert [c.name for c in draft.palette] == ["Soft gold", "Peach", "Sunset pink", "Rose", "Lavender"]
    assert draft.layout.model_dump()["centre_in"] == (0.0, 60.0)
    assert (draft.sample_at, draft.brightness, draft.solid) == ("centre", 95, 2)
    assert draft.stepped is not None and draft.stepped.model_dump() == {
        "direction": "forward",
        "steps_per_cycle": 6,
        "step_s": 6.0,
        "run_s": 90.0,
        # D75: the kind of motion, at SteppedTechnique's defaults (Golden Hour travels)
        "mode": "travel",
        "shift": 0.0,
        "brightness_swing": 0,
        "lag_steps": 0,
        "seed": None,
    }
    assert draft.copy.model_dump() == {"modules": "auto", "stepped": "auto"}
    assert D.seed_draft(TRIO, "lowcountry-calm").stepped is None
    with pytest.raises(KeyError):
        D.seed_draft(TRIO, "disco-night")


def test_is_approved_ignores_hex_case_and_number_formatting_but_not_edits():
    assert D.is_approved(TRIO, edit("golden-hour", **{"palette.2.hex": "#ff9bc4", "stepped.step_s": 6}))
    assert not D.is_approved(TRIO, edit("golden-hour", **{"palette.2.hex": "#FF8FBF"}))
    assert not D.is_approved(TRIO, edit("golden-hour", copy={"modules": "generated", "stepped": "auto"}))


# ---- every rung in lockstep ------------------------------------------------------------------------------------------
def test_golden_hours_pink_changes_every_rung_in_lockstep():
    patched = apply(edit("golden-hour", **{"palette.2.hex": "#FF8FBF"}))
    mode = patched.modes.mode("golden-hour")
    t = mode.techniques
    # the recipe key stays (the name is unchanged) and its entry is the new hex, house style
    assert stop_keys(patched, "golden-hour") == stop_keys(TRIO, "golden-hour")
    pink = patched.recipes.colors["sunset-pink"]
    assert pink.hex == "#FF8FBF" and pink.label == "Sunset pink"
    assert pink.rgb_only.model_dump() == {
        "r": 255,
        "g": 143,
        "b": 191,
        "cct": 0,
        "colorBrightness": 100,
        "whiteBrightness": 0,
    }
    assert pink.blend == pink.rgb_only and pink.strand.rgb == (255, 143, 191)
    # one design on every rung
    assert t.canvas.canvas == t.modules.sample.canvas == t.stepped.canvas
    assert mode.preview[2] == "#FF8FBF"
    assert patched.scenes.get("golden-hour").palette[2].hex == "#FF8FBF"
    assert t.strand.look.rgb == (255, 143, 191)  # Sunset pink is the solid
    # the copy still describes the design: approved wording on every rung
    assert t.modules.feature == TRIO.modes.mode("golden-hour").techniques.modules.feature
    assert t.stepped.moving_feature == TRIO.modes.mode("golden-hour").techniques.stepped.moving_feature
    assert t.strand.feature == TRIO.modes.mode("golden-hour").techniques.strand.feature
    others_equal(patched, "golden-hour")


def test_the_done_means_edit_moves_the_design_on_every_rung_and_regenerates_the_layout_copy():
    patched = apply(edit("golden-hour", **DONE_MEANS))
    mode = patched.modes.mode("golden-hour")
    t = mode.techniques
    for design in (t.canvas.canvas, t.modules.sample.canvas, t.stepped.canvas):
        assert design.field.model_dump()["angle_deg"] == 30.0
        assert [s.at for s in design.stops] == [0.0, 0.25, 0.5, 0.75, 1.0]
        assert design.recipe == "rgb_only"
    assert t.modules.feature == (
        "Colour and dimming: each Hubspace strand set to a single colour, so the palette is laid out diagonally: "
        "peach, then sunset pink and rose."  # no strand reaches soft gold or lavender at 30° (reached_stops)
    )
    assert "move diagonally across the canopy" in t.stepped.moving_feature
    assert mode.ladder == TRIO.modes.mode("golden-hour").ladder and mode.slot == "custom-3"
    others_equal(patched, "golden-hour")


def test_brightness_reaches_every_rung_and_the_scene():
    patched = apply(edit("golden-hour", brightness=60))
    t = patched.modes.mode("golden-hour").techniques
    assert t.canvas.brightness == 60
    assert t.slot.brightness == 60
    assert t.modules.sample.brightness == 60
    assert t.stepped.brightness == 60
    assert t.strand.look.brightness == 60
    assert patched.scenes.get("golden-hour").brightness_draft == 60
    # a level, never multiplied into a colour (D59)
    assert patched.recipes.model_dump() == TRIO.recipes.model_dump()
    assert t.strand.look.rgb == TRIO.modes.mode("golden-hour").techniques.strand.look.rgb


def test_client_copy_reaches_the_scene_and_the_mode():
    patched = apply(
        edit("coastal-tides", name="Tidewater", mood="Find your shore", description="Blue to foam.")
    )
    scene, mode = patched.scenes.get("coastal-tides"), patched.modes.mode("coastal-tides")
    assert (scene.name, scene.mood, scene.description) == ("Tidewater", "Find your shore", "Blue to foam.")
    assert (mode.label, mode.blurb) == ("Tidewater", "Find your shore")


def test_a_renamed_colour_still_compiles_at_every_tier():
    draft = with_palette(
        "golden-hour",
        [("Soft gold", "#FFDB9B"), ("Apricot", "#FFB69D"), ("Sunset pink", "#FF9BC4"), ("Rose", "#E779B0")],
        solid=2,
    )
    patched = apply(draft)
    assert stop_keys(patched, "golden-hour")[1] == "apricot"
    assert "golden-hour-peach" not in patched.recipes.colors and "lavender" not in patched.recipes.colors
    assert "goodnight-glow-lavender" in patched.recipes.colors  # another scene's key stays
    from conductor.design_lab.preview import design_caps, design_registry

    registry = design_registry()
    ctx = CompileContext(
        registry=registry,
        capabilities={d.id: design_caps() for d in registry},
        modes=patched.modes,
        recipes=patched.recipes,
        canopy=CANOPY,
    )
    for tier in patched.modes.mode("golden-hour").ladder:
        if tier == "slot":
            continue  # T2 needs a hash recorded as painted in the app
        plan = compile_mode("golden-hour", Tier(tier), ctx)
        assert plan.tier == Tier(tier) and len(plan.modules) == 9


# ---- ids, ladders and slots never move --------------------------------------------------------------------------------
def test_ladder_slot_and_ids_never_move():
    assert not {"id", "ladder", "slot", "slots", "motion", "techniques"} & set(D.SceneDraft.model_fields)
    with pytest.raises(ValidationError):
        D.SceneDraft.model_validate({**D.seed_draft(TRIO, "golden-hour").model_dump(), "ladder": ["modules"]})
    drafts = [
        edit("golden-hour", **DONE_MEANS),
        edit("lowcountry-calm", brightness=50),
        with_palette("rainbow-row-sunset", [("Coral", "#FF9E96"), ("Lilac", "#C4B3EC")]),
    ]
    patched = apply(*drafts)
    assert list(patched.modes.modes) == list(TRIO.modes.modes)
    assert [s.id for s in patched.scenes.scenes] == IDS
    for sid in IDS:
        assert patched.modes.mode(sid).ladder == TRIO.modes.mode(sid).ladder
        assert patched.modes.mode(sid).slot == TRIO.modes.mode(sid).slot
        assert patched.modes.mode(sid).motion is False


def test_an_unknown_scene_or_a_mismatched_key_is_a_scene_id_problem():
    draft = D.SceneDraft.model_validate(
        {**D.seed_draft(TRIO, "golden-hour").model_dump(), "scene_id": "disco"}
    )
    with pytest.raises(D.DraftError) as exc:
        D.apply_drafts(TRIO, {"disco": draft}, canopy=CANOPY, settings=SETTINGS)
    assert [p.field for p in exc.value.problems] == ["scene_id"]
    with pytest.raises(D.DraftError) as exc:
        D.apply_drafts(TRIO, {"coastal-tides": edit("golden-hour")}, canopy=CANOPY, settings=SETTINGS)
    assert [p.field for p in exc.value.problems] == ["scene_id"]


def test_a_stepped_rung_is_neither_added_nor_dropped():
    no_steps = D.SceneDraft.model_validate(
        {**D.seed_draft(TRIO, "coastal-tides").model_dump(), "stepped": None}
    )
    [missing] = problems(no_steps)
    assert missing.field == "stepped" and "needs the stepped timing" in missing.message
    steps = {"direction": "forward", "steps_per_cycle": 6, "step_s": 6, "run_s": 90}
    calm = D.SceneDraft.model_validate(
        {**D.seed_draft(TRIO, "lowcountry-calm").model_dump(), "stepped": steps}
    )
    assert [p.field for p in problems(calm)] == ["stepped"]


def test_the_stepped_timing_reaches_the_stepped_rung():
    patched = apply(
        edit(
            "coastal-tides",
            stepped={"direction": "reverse", "steps_per_cycle": 8, "step_s": 7.5, "run_s": 60},
        )
    )
    st = patched.modes.mode("coastal-tides").techniques.stepped
    assert (st.direction, st.steps_per_cycle, st.step_s, st.run_s) == ("reverse", 8, 7.5, 60.0)
    # the direction changed, so the moving copy is generated and names the reverse flow
    words = motion_words(st.canvas, CANOPY, "reverse")
    assert words in st.moving_feature and STEPPED_PACE in st.moving_feature


# ---- the D63 locks -------------------------------------------------------------------------------------------------
def test_cycles_and_sample_at_are_locked_to_d63():
    found = problems(edit("golden-hour", layout={"kind": "linear", "angle_deg": 30, "cycles": 2}))
    assert [p.field for p in found] == ["layout.cycles"]
    assert "D63" in found[0].message
    found = problems(edit("lowcountry-calm", sample_at="mean"))
    assert [p.field for p in found] == ["sample_at"]
    assert "D63" in found[0].message


# ---- recipe keys -----------------------------------------------------------------------------------------------------
def test_recipe_keys_follow_the_name_or_scene_id_rule():
    # Golden Hour's "Peach" keeps golden-hour-peach with a new hex
    patched = apply(edit("golden-hour", **{"palette.1.hex": "#FFAA90"}))
    assert stop_keys(patched, "golden-hour")[1] == "golden-hour-peach"
    assert patched.recipes.colors["golden-hour-peach"].hex == "#FFAA90"
    # a new "Lavender" in Garden Gathering: "lavender" is Golden Hour's, so it is <scene id>-lavender
    garden = with_palette(
        "garden-gathering",
        [("Sage green", "#8FBF9D"), ("Pale lime", "#E6EED1"), ("Ivory", "#F7F2E7"), ("Lavender", "#C9B8E8")],
    )
    patched = apply(garden)
    assert stop_keys(patched, "garden-gathering") == [
        "sage-green",
        "pale-lime",
        "ivory",
        "garden-gathering-lavender",
    ]
    assert patched.recipes.colors["garden-gathering-lavender"].hex == "#C9B8E8"
    assert patched.recipes.colors["lavender"] == TRIO.recipes.colors["lavender"]
    # a name nobody has keeps its own slug; the key it replaced, no longer named by any mode, is removed
    goodnight = with_palette(
        "goodnight-glow", [("Periwinkle", "#8B8BD1"), ("Lavender", "#BEA9E6"), ("Night mist", "#C4D6F3")]
    )
    patched = apply(goodnight)
    assert stop_keys(patched, "goodnight-glow") == ["periwinkle", "goodnight-glow-lavender", "night-mist"]
    assert "moonlight-blue" not in patched.recipes.colors
    # "Peach" is the module page's colour-study key: a new Peach gets the scene id
    peach = with_palette("coastal-tides", [("Ocean blue", "#659BD5"), ("Peach", "#FFB08A")], solid=0)
    patched = apply(peach)
    assert stop_keys(patched, "coastal-tides") == ["ocean-blue", "coastal-tides-peach"]
    assert patched.recipes.colors["peach"] == TRIO.recipes.colors["peach"]  # the module page's is untouched
    assert not {"aqua", "seafoam", "foam-white"} & set(patched.recipes.colors)


def test_recipe_key_allocation_is_exact():
    # a reorder keeps every colour's key
    approved = dict(
        zip([c.name for c in TRIO.scenes.get("golden-hour").palette], stop_keys(TRIO, "golden-hour"))
    )
    reversed_ = list(reversed([(c.name, c.hex) for c in TRIO.scenes.get("golden-hour").palette]))
    patched = apply(with_palette("golden-hour", reversed_, solid=2))
    assert dict(zip([n for n, _ in reversed_], stop_keys(patched, "golden-hour"))) == approved
    assert patched.recipes.model_dump() == TRIO.recipes.model_dump()
    # "Sea glass" and "sea-glass" would share a key: the second is refused
    calm = with_palette(
        "lowcountry-calm", [("Soft ivory", "#F7F2E7"), ("Sea glass", "#D1EAE3"), ("sea-glass", "#D1EAE4")]
    )
    assert [p.field for p in problems(calm)] == ["palette.2.name"]
    # ... and when the edited colour comes first, it is the one blamed (not the unchanged stable colour)
    renamed = edit("golden-hour", **{"palette.0.name": "Peach!"})
    assert [p.field for p in problems(renamed)] == ["palette.0.name"]
    # the name and <scene id>-<name> both taken (here by keys another draft of the same apply took) → refused
    rainbow = with_palette("rainbow-row-sunset", [("Mint", "#BDEBD2"), ("Golden hour mint", "#A8E0C4")])
    golden = edit("golden-hour", **{"palette.3.name": "Mint"})
    found = problems(rainbow, golden)
    assert [p.field for p in found] == ["palette.3.name"]
    assert "rainbow-row-sunset" in found[0].message and "golden-hour-mint" in found[0].message
    # recipe_keys itself, against a crafted owner map: a stable key collides with nothing
    mode = TRIO.modes.mode("golden-hour")
    owners = {
        "mint": "rainbow-row-sunset",
        "golden-hour-mint": "garden-gathering",
        "soft-gold": "golden-hour",
    }
    with pytest.raises(D.DraftError) as exc:
        D.recipe_keys(mode, edit("golden-hour", **{"palette.0.name": "Mint"}), TRIO.recipes, owners)
    assert [p.field for p in exc.value.problems] == ["palette.0.name"]
    keys = D.recipe_keys(mode, edit("golden-hour", **{"palette.0.name": "Mint green"}), TRIO.recipes, owners)
    assert keys[0] == "mint-green" and keys[1:] == stop_keys(TRIO, "golden-hour")[1:]


def _with_key(trio: D.ConfigTrio, old: str, new: str) -> D.ConfigTrio:
    """The trio with one recipe key renamed everywhere (a key that isn't its colour name's slug)."""
    import json

    from conductor.config import RecipesFile
    from conductor.looks.schema import ModesFile

    modes = json.loads(json.dumps(trio.modes.model_dump(mode="json")).replace(f'"{old}"', f'"{new}"'))
    recipes = trio.recipes.model_dump(mode="json")
    recipes["colors"][new] = recipes["colors"].pop(old)
    return D.ConfigTrio(trio.scenes, ModesFile.model_validate(modes), RecipesFile.model_validate(recipes))


def test_a_stable_key_is_kept_even_when_it_isnt_the_names_slug():
    trio = _with_key(TRIO, "sunset-pink", "pink-01")
    hexed = edit("golden-hour", **{"palette.2.hex": "#FF8FBF"})
    patched = D.apply_draft(trio, hexed, canopy=CANOPY, settings=SETTINGS)
    assert stop_keys(patched, "golden-hour")[2] == "pink-01"
    assert patched.recipes.colors["pink-01"].hex == "#FF8FBF" and "sunset-pink" not in patched.recipes.colors
    colours = [(c.name, c.hex) for c in TRIO.scenes.get("golden-hour").palette]
    colours[2], colours[3] = colours[3], colours[2]  # Sunset pink moves after Rose
    patched = D.apply_draft(
        trio, with_palette("golden-hour", colours, solid=3), canopy=CANOPY, settings=SETTINGS
    )
    assert stop_keys(patched, "golden-hour") == [
        "soft-gold",
        "golden-hour-peach",
        "rose",
        "pink-01",
        "lavender",
    ]
    assert (
        D.apply_draft(trio, D.seed_draft(trio, "golden-hour"), canopy=CANOPY, settings=SETTINGS).dump()
        == trio.dump()
    )


def test_two_drafts_never_take_one_new_key():
    a = with_palette("rainbow-row-sunset", [("Mint", "#BDEBD2"), ("Lilac", "#C4B3EC")])
    b = with_palette("garden-gathering", [("Sage green", "#8FBF9D"), ("Mint", "#A8E0C4")])
    patched = apply(a, b)
    assert stop_keys(patched, "rainbow-row-sunset")[0] == "mint"
    assert stop_keys(patched, "garden-gathering")[1] == "garden-gathering-mint"
    assert patched.recipes.colors["mint"].hex == "#BDEBD2"
    assert patched.recipes.colors["garden-gathering-mint"].hex == "#A8E0C4"


# ---- copy --------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "scene_id, layout",
    [
        ("coastal-tides", {"kind": "linear", "angle_deg": 30}),  # 78° → 30°: both "diagonally"
        (
            "golden-hour",
            {"kind": "radial", "centre_in": [0, 100], "metric": "canopy"},
        ),  # both "from one edge"
        (
            "goodnight-glow",
            {"kind": "radial", "centre_in": [100, 50], "invert": True},
        ),  # both "from one spot"
    ],
)
def test_any_layout_change_forces_generated_copy_even_with_the_same_words(scene_id, layout):
    seed = D.seed_draft(TRIO, scene_id)
    draft = edit(scene_id, layout=layout)
    assert F.layout_words(draft.layout, CANOPY) == F.layout_words(seed.layout, CANOPY)
    copy = D.resolve_copy(TRIO, draft, CANOPY)
    assert copy["modules"].chosen == "generated" and "layout changed" in copy["modules"].why
    patched = apply(draft)
    mode = patched.modes.mode(scene_id)
    assert (
        mode.techniques.modules.feature
        == D.generated_copy(TRIO.modes.mode(scene_id), draft, CANOPY)["modules"]
    )
    assert mode.techniques.modules.feature != TRIO.modes.mode(scene_id).techniques.modules.feature
    if mode.techniques.stepped is not None:
        assert copy["stepped"].chosen == "generated"


def test_auto_copy_resolves_without_a_422():
    draft = D.SceneDraft.model_validate(
        {k: v for k, v in edit("golden-hour", **DONE_MEANS).model_dump().items() if k != "copy"}
    )
    assert draft.copy.model_dump() == {"modules": "auto", "stepped": "auto"}
    patched = apply(draft)  # no DraftError
    copy = D.resolve_copy(TRIO, draft, CANOPY)
    assert copy["modules"].chosen == "generated" and copy["stepped"].chosen == "generated"
    assert patched.modes.mode("golden-hour").techniques.modules.feature == copy["modules"].sentence


def test_an_unchanged_design_keeps_the_approved_sentences_even_with_new_hexes():
    copy = D.resolve_copy(TRIO, edit("golden-hour", **{"palette.2.hex": "#FF8FBF", "brightness": 40}), CANOPY)
    assert {k: c.chosen for k, c in copy.items()} == {
        "modules": "approved",
        "stepped": "approved",
        "strand": "approved",
    }


def test_an_explicit_approved_copy_that_is_false_is_refused_on_save():
    """Save validates with apply_drafts (LookStore.save_version, §3.5): an explicit "approved" sentence that no longer
    describes the design is a 422 on its copy field. Preview resolves it as auto (tests/test_lab_preview.py)."""
    draft = edit("golden-hour", **DONE_MEANS, copy={"modules": "approved", "stepped": "auto"})
    found = problems(draft)
    assert [p.field for p in found] == ["copy.modules"]
    assert "no longer describes this design" in found[0].message and "layout changed" in found[0].message
    reverse = edit(
        "coastal-tides",
        stepped={"direction": "reverse", "steps_per_cycle": 6, "step_s": 6, "run_s": 90},
        copy={"modules": "approved", "stepped": "approved"},
    )
    found = problems(reverse)
    assert [p.field for p in found] == ["copy.stepped"]
    assert "direction changed" in found[0].message
    # while it is still true, an explicit "approved" is fine
    apply(edit("golden-hour", brightness=50, copy={"modules": "approved", "stepped": "approved"}))


def test_the_strand_sentence_follows_the_solid_colour():
    hex_only = apply(edit("golden-hour", **{"palette.2.hex": "#FF8FBF"}))
    assert hex_only.modes.mode("golden-hour").techniques.strand.feature.endswith("set to sunset pink.")
    other = apply(edit("golden-hour", solid=4))
    t = other.modes.mode("golden-hour").techniques.strand
    assert t.feature == "Colour and dimming: all nine Hubspace strands set to lavender."
    assert t.look.rgb == (181, 154, 226) and t.fallback_look is None
    renamed = apply(edit("golden-hour", **{"palette.2.name": "Flamingo"}))
    assert renamed.modes.mode("golden-hour").techniques.strand.feature.endswith("set to flamingo.")


def test_colour_phrase_reads_from_where_the_layout_starts():
    names = ["Soft gold", "Peach", "Rose"]
    assert D.colour_phrase(names, invert=False, transform="linear") == "soft gold, then peach and rose"
    assert D.colour_phrase(names, invert=True, transform="linear") == "rose, then peach and soft gold"
    assert D.colour_phrase(names[:2], invert=False, transform="linear") == "soft gold, then peach"
    assert (
        D.colour_phrase(names, invert=False, transform="mirror")
        == "soft gold at both ends through rose in the middle"
    )
    assert (
        D.colour_phrase(names, invert=True, transform="mirror")
        == "rose at both ends through soft gold in the middle"
    )
    assert D.colour_phrase(["A", "B", "C", "D"], invert=False, transform="linear") == "a, then b, c and d"


LAYOUTS = [
    {"kind": "linear", "angle_deg": 0},
    {"kind": "linear", "angle_deg": 90},
    {"kind": "linear", "angle_deg": 180},
    {"kind": "linear", "angle_deg": 30},
    {"kind": "linear", "angle_deg": 135},
    {"kind": "radial", "centre_in": [112.5, 60]},  # the canopy's centre
    {"kind": "radial", "centre_in": [0, 60], "metric": "canopy"},  # an edge
    {"kind": "radial", "centre_in": [0, 0]},  # a corner
    {"kind": "radial", "centre_in": [100, 50]},  # a spot
    {"kind": "radial", "centres_in": [[0, 0], [225, 120]]},  # two points
    {"kind": "sweep", "centre_in": [0, 60]},
    {"kind": "sweep", "centre_in": [0, 0], "start_deg": 45},
    {"kind": "sweep", "centre_in": [100, 50]},
    {"kind": "fins"},  # the drawing's rings (D74); mirror is refused for it
]


def _check_generated(scene_id: str, layout: dict, invert: bool, transform: str, direction: str) -> None:
    """The draft's copy, forced to the generated sentences, passes parse_modes; any refusal is never about copy."""
    changes = {"layout": {**layout, "invert": invert, "transform": transform}}
    changes["copy"] = {"modules": "generated", "stepped": "generated"}
    if scene_id in STEPPED:
        changes["stepped.direction"] = direction
    draft = edit(scene_id, **changes)
    generated = D.generated_copy(TRIO.modes.mode(scene_id), draft, CANOPY)
    words = F.layout_words(draft.layout, CANOPY)
    t3 = generated["modules"]
    assert words in t3 and not MOTION_WORDS.search(t3)
    assert not [p for p in F.LAYOUT_PHRASES if p != words and p in t3]
    strand = generated["strand"]
    assert "nine" in strand and strand.endswith(f"set to {draft.palette[draft.solid].name.lower()}.")
    assert not MOTION_WORDS.search(strand)
    if "stepped" in generated:
        moving = generated["stepped"]
        flow = motion_words(D._canvas_of(draft.layout), CANOPY, direction)
        assert flow in moving and STEPPED_PACE in moving and not STEPPED_FALSE_CLAIMS.search(moving)
        assert not [p for p in MOTION_PHRASES if p != flow and p in moving]
    try:
        patched = apply(draft)
    except D.DraftError as exc:
        # a design can be refused (one colour at every module, a sweep on a module centre), never its copy
        assert not [p for p in exc.problems if p.field.startswith("copy")], exc.problems
        assert {p.field for p in exc.problems} <= {"layout", "palette"}, exc.problems
        return
    mode = patched.modes.mode(scene_id)
    assert mode.techniques.modules.feature == t3
    assert mode.techniques.strand.feature == generated["strand"]


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda d: "-".join(str(v) for v in d.values()))
@pytest.mark.parametrize("scene_id", IDS)
def test_generated_copy_passes_parse_modes_for_every_layout_kind_invert_and_direction(scene_id, layout):
    for invert, transform, direction in itertools.product(
        (False, True), ("linear", "mirror"), ("forward", "reverse")
    ):
        if direction == "reverse" and scene_id not in STEPPED:
            continue
        if layout["kind"] == "fins" and transform == "mirror":
            with pytest.raises(ValidationError, match="can't be mirrored"):
                edit(scene_id, layout={**layout, "invert": invert, "transform": transform})
            continue
        _check_generated(scene_id, layout, invert, transform, direction)


def test_most_generated_designs_do_apply():
    """The generated-copy sweep above isn't vacuous: almost every layout applies outright."""
    applied = 0
    for scene_id, layout in itertools.product(IDS, LAYOUTS):
        try:
            apply(edit(scene_id, layout=layout, copy={"modules": "generated", "stepped": "generated"}))
            applied += 1
        except D.DraftError:
            pass
    assert applied >= len(IDS) * len(LAYOUTS) - 6


@pytest.mark.parametrize(
    "name",
    [
        "Moving gold",
        "Wave blue",
        "Animated",
        "Nine",
        "2 tone",
        "Both pinks",
        "One rose",
        "Shimmer",
        "Every light",
        "Effect pink",
        "Diagonally pink",
        "Row by row",
        "From one edge",
        "Toward the centre",
        "Controller",
    ],
)
def test_colour_names_with_motion_or_number_words_are_refused(name):
    with pytest.raises(ValidationError) as exc:
        edit("golden-hour", **{"palette.1.name": name})
    assert exc.value.errors()[0]["loc"][:3] == ("palette", 1, "name")


def test_a_mood_with_movement_is_refused():
    with pytest.raises(ValidationError):
        edit("golden-hour", mood="Watch it move")


# ---- re-validation and field mapping -----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path, field",
    [
        ("mode 'golden-hour' → label", "name"),
        ("scenes → 4 → name", "name"),
        ("mode 'golden-hour' → blurb", "mood"),
        ("scenes → 4 → mood", "mood"),
        ("scenes → 4 → description", "description"),
        ("mode 'golden-hour' → preview → [2]", "palette.2"),
        ("scenes → 4 → palette → 2 → hex", "palette.2.hex"),
        ("scenes → 4 → palette → 1 → name", "palette.1.name"),
        ("mode 'golden-hour' → techniques → canvas → canvas → stops → [3] → color", "palette.3"),
        ("colors → sunset-pink → hex", "palette"),
        ("recipes → colors → x", "palette"),
        ("mode 'golden-hour' → techniques → canvas → canvas → field → angle_deg", "layout.angle_deg"),
        ("mode 'golden-hour' → techniques → modules → sample → canvas → field", "layout"),
        ("mode 'golden-hour' → techniques → modules → sample → at", "sample_at"),
        ("mode 'golden-hour' → techniques → slot → brightness", "brightness"),
        ("mode 'golden-hour' → techniques → modules → feature", "copy.modules"),
        ("mode 'golden-hour' → techniques → stepped → moving_feature", "copy.stepped"),
        ("config/modes.yaml → golden-hour → techniques → stepped → run_s", "stepped.run_s"),
        ("mode 'golden-hour' → techniques → stepped → step_s", "stepped.step_s"),
        ("mode 'golden-hour' → techniques → stepped", "stepped"),
        ("mode 'golden-hour' → techniques → strand → feature", "solid"),
        ("mode 'golden-hour' → techniques → modules → sample", "layout"),
        ("config/conductor.yaml → kiosk → idle_scene", "draft"),
        ("mode 'golden-hour'", "draft"),
    ],
)
def test_config_error_paths_map_to_draft_fields(path, field):
    assert D.field_for(path) == field


def test_config_error_text_splits_into_one_problem_per_bullet():
    text = (
        "the draft of golden-hour has 2 problem(s):\n"
        "  • mode 'golden-hour' → techniques → modules → feature: the sampled design is laid out 'diagonally', "
        "so the copy must say 'diagonally'\n"
        "  • scenes → 4 → palette → 2 → hex: Value error, colours are #RRGGBB\n"
        "Fix the file and restart."
    )
    found = D.problems_from_config_error(text)
    assert [(p.field, p.message) for p in found] == [
        ("copy.modules", "the sampled design is laid out 'diagonally', so the copy must say 'diagonally'"),
        ("palette.2.hex", "colours are #RRGGBB"),
    ]
    (one,) = D.problems_from_config_error(
        "config/modes.yaml → coastal-tides → techniques → stepped → run_s: 90 s is longer than kiosk.idle_return_s"
    )
    assert one.field == "stepped.run_s" and one.message.startswith("90 s is longer")


def test_a_run_longer_than_the_idle_return_is_a_stepped_run_s_problem():
    found = problems(edit("coastal-tides"), settings=settings_with(kiosk={"idle_return_s": 60}))
    assert [p.field for p in found] == ["stepped.run_s"]
    assert "idle_return_s" in found[0].message


def test_a_design_with_one_colour_at_every_module_is_a_layout_problem():
    same = with_palette("lowcountry-calm", [("Soft ivory", "#F7F2E7"), ("Pale ivory", "#F7F2E7")])
    found = problems(same)
    assert [p.field for p in found] == ["layout"]
    assert "same colour at every module centre" in found[0].message


def test_a_draft_validates_with_an_idle_scene_set():
    settings = settings_with(kiosk={"idle_scene": "golden-hour"})
    patched = apply(edit("golden-hour", **DONE_MEANS), settings=settings)
    assert patched.modes.mode("golden-hour").techniques.modules.sample.brightness == 90
    # D75: an idle look with a stepped rung is fine (start-up and an idle return show it still)
    stepping_idle = settings_with(kiosk={"idle_mode": "coastal-tides"})
    assert apply(edit("lowcountry-calm"), settings=stepping_idle).modes.mode("coastal-tides").moves
    # and the check still runs: motion.idle on with an idle look that has no rung refuses every draft
    breathing = settings_with(motion={"idle": {"breathe_run_s": 60, "every_s": 300}})
    found = problems(edit("golden-hour"), settings=breathing)
    assert found and all(p.field == "draft" for p in found)
    assert [p.path for p in found] == ["config/conductor.yaml → motion → idle"]
    assert "has no stepped rung to breathe with" in found[0].message


def test_blocking_scene_rules_refuse_and_warnings_never_do(monkeypatch):
    calls = []

    def rules(scenes, modes, recipes, canopy, *, approved=None, rules=scene_rules.RULES):
        rules = tuple(rules)
        calls.append(rules)
        if "brightness" in rules:
            return [
                scene_rules.SceneProblem("brightness", "golden-hour", "techniques → slot → brightness", "94")
            ]
        return []

    monkeypatch.setattr(scene_rules, "scene_mode_problems", rules)
    found = problems(edit("golden-hour", brightness=94))
    assert [p.field for p in found] == ["brightness"]
    assert calls and all(
        set(c) == set(scene_rules.BLOCKING) for c in calls
    )  # apply never asks for the warnings

    def warns(scenes, modes, recipes, canopy, *, approved=None, rules=scene_rules.RULES):
        rules = tuple(rules)
        return (
            [scene_rules.SceneProblem("two-d", "golden-hour", "techniques → modules", "stripes")]
            if "two-d" in rules
            else []
        )

    monkeypatch.setattr(scene_rules, "scene_mode_problems", warns)
    apply(edit("golden-hour", layout={"kind": "linear", "angle_deg": 0}))  # a warning never refuses a draft


# ---- what a draft changes --------------------------------------------------------------------------------------------
def test_draft_changes_flags_d59_d63_and_the_slot_repaint():
    changes = D.draft_changes(TRIO, edit("golden-hour", **DONE_MEANS), canopy=CANOPY)
    assert changes["approved_colours"] == [
        {
            "index": 2,
            "from": {"name": "Sunset pink", "hex": "#FF9BC4"},
            "to": {"name": "Sunset pink", "hex": "#FF8FBF"},
        }
    ]
    assert (
        changes["layout"]["words_from"] == "from one edge" and changes["layout"]["words_to"] == "diagonally"
    )
    assert changes["brightness"] == {"from": 95, "to": 90}
    assert changes["needs_d59"] and changes["needs_d63"] and changes["needs_slot_repaint"]
    assert set(changes["copy"]) == {"modules", "stepped"}
    quiet = D.draft_changes(TRIO, edit("golden-hour", brightness=80), canopy=CANOPY)
    assert quiet["brightness"] == {"from": 95, "to": 80} and quiet["copy"] == []
    assert not (quiet["needs_d59"] or quiet["needs_d63"] or quiet["needs_slot_repaint"])
    # a rename alone: an approved colour changes (D59), the frames don't (same hexes, same layout: no repaint)
    rename = D.draft_changes(TRIO, edit("golden-hour", **{"palette.1.name": "Apricot"}), canopy=CANOPY)
    assert rename["needs_d59"] and not rename["needs_slot_repaint"]
    # a module-level scene never needs a slot repaint
    tides = D.draft_changes(
        TRIO, edit("coastal-tides", layout={"kind": "linear", "angle_deg": 60}), canopy=CANOPY
    )
    assert tides["needs_d63"] and not tides["needs_slot_repaint"]
    assert D.draft_changes(TRIO, D.seed_draft(TRIO, "garden-gathering"), canopy=CANOPY)["copy"] == []


# ---- purity -----------------------------------------------------------------------------------------------------------
def test_apply_never_mutates_its_inputs_or_the_show():
    """The purity rule (§3.0), A part: the input trio, the drafts, the show's cached catalog and modes are unchanged
    by applies, previews and change reports (B's part covers the show's wiring during a try-on)."""
    from conductor.design_lab.preview import tiers_json

    trio_before = TRIO.dump()
    cached = load_scenes(CONFIG_DIR)  # lru_cached: the very catalog the guest panel serves
    cached_before = cached.model_dump()
    modes_cached = load_modes(CONFIG_DIR).model_dump()
    drafts = [
        edit("golden-hour", **DONE_MEANS),
        edit("golden-hour", solid=4, brightness=40, name="Golden Glow"),
        with_palette("goodnight-glow", [("Periwinkle", "#8B8BD1"), ("Night mist", "#C4D6F3")]),
        edit(
            "coastal-tides", stepped={"direction": "reverse", "steps_per_cycle": 4, "step_s": 5, "run_s": 40}
        ),
        edit("lowcountry-calm", layout={"kind": "radial", "centre_in": [0, 0]}),
    ]
    drafts_before = [d.model_dump() for d in drafts]
    for d in drafts:
        patched = apply(d)
        tiers_json(patched, d.scene_id, CANOPY)
        D.draft_changes(TRIO, d, canopy=CANOPY)
        D.resolve_copy(TRIO, d, CANOPY)
    apply(*drafts[2:])
    assert TRIO.dump() == trio_before
    assert [d.model_dump() for d in drafts] == drafts_before
    assert load_scenes(CONFIG_DIR) is cached and cached.model_dump() == cached_before
    assert load_modes(CONFIG_DIR).model_dump() == modes_cached
    # load_trio hands out copies, never the cached objects themselves
    fresh = D.load_trio(CONFIG_DIR)
    assert fresh.scenes is not cached and fresh.scenes.scenes[0] is not cached.scenes[0]


# ---- the kind of motion (D75) ----------------------------------------------------------------------------------------
BREATHE_GOLDEN = {
    "stepped.mode": "breathe",
    "stepped.brightness_swing": 5,
    "stepped.lag_steps": 2,
}  # 95 ± 5 fits


def test_a_draft_carries_the_kind_of_motion_and_its_parameters_to_the_rung():
    draft = edit("golden-hour", **BREATHE_GOLDEN)
    patched = apply(draft)
    st = patched.modes.mode("golden-hour").techniques.stepped
    assert (st.mode, st.shift, st.brightness_swing, st.lag_steps, st.seed) == ("breathe", 0.0, 5, 2, None)
    copy = D.resolve_copy(TRIO, draft, CANOPY)["stepped"]
    assert copy.chosen == "generated" and "the kind of motion changed" in copy.why
    # the colours a breath without a shift reaches are exactly the still design's (the T3 sentence's stops)
    names = [c.name for c in draft.palette]
    still = [names[i] for i in D.reached_stops(draft.layout, len(names), CANOPY)]
    assert len(still) < len(names)  # Golden Hour's module level shows neither soft gold nor lavender
    assert (
        st.moving_feature
        == copy.sentence
        == D.BREATHE_TEMPLATE.format(
            what="a brighter or softer level",
            verb="swells and softens",
            lead=" from one edge",
            colours=D.colour_phrase(still, invert=draft.layout.invert, transform=draft.layout.transform),
        )
    )
    # it parses through the show's own schema (apply did) and passes the blocking scene rules
    assert not scene_rules.scene_mode_problems(
        patched.scenes,
        patched.modes,
        patched.recipes,
        CANOPY,
        approved=TRIO.modes,
        rules=scene_rules.BLOCKING,
    )
    # the patched rung seeds back to the same draft (the breathe parameters round-trip through seed_draft)
    reseeded = D.seed_draft(patched, "golden-hour")
    assert D.is_approved(patched, reseeded) and reseeded.stepped.model_dump() == draft.stepped.model_dump()
    # a shimmer's sentence says the strands breathe on their own
    shimmer = edit("golden-hour", **{**BREATHE_GOLDEN, "stepped.mode": "shimmer", "stepped.seed": 7})
    moving = apply(shimmer).modes.mode("golden-hour").techniques.stepped.moving_feature
    assert "so the strands glow and soften on their own: " in moving and STEPPED_PACE in moving


def test_a_breaths_copy_names_only_the_colours_it_reaches():
    """Golden Hour's module level never shows soft gold or lavender (its T3 sentence leaves them out): a breath
    without a shift keeps every strand's colour, so its sentence doesn't name them either; a shift of 0.5 carries
    the strands at the ends of the design onto them, so it does, and says why the approved sentence went."""
    level_only = apply(edit("golden-hour", **BREATHE_GOLDEN)).modes.mode("golden-hour").techniques.stepped
    assert "soft gold" not in level_only.moving_feature and "lavender" not in level_only.moving_feature
    shifted = edit("golden-hour", **{**BREATHE_GOLDEN, "stepped.shift": 0.5})
    st = apply(shifted).modes.mode("golden-hour").techniques.stepped
    assert "soft gold" in st.moving_feature and "lavender" in st.moving_feature
    assert "sent its next colour and a brighter or softer level every few seconds" in st.moving_feature
    copy = D.resolve_copy(TRIO, shifted, CANOPY)["stepped"]
    assert "the colours the breath reaches changed" in copy.why
    assert D.breath_offsets(shifted.stepped) == [-0.5, -0.25, 0.0, 0.25, 0.5]
    assert D.breath_offsets(D.seed_draft(TRIO, "golden-hour").stepped) == [0.0]  # travel: the still design


def test_breathe_problems_land_on_the_stepped_fields(app_client):
    # the schema's own checks of the kind of motion name the stepped rung
    [nothing] = problems(edit("golden-hour", **{"stepped.mode": "breathe"}))  # no shift, no swing
    assert nothing.field == "stepped" and "needs a shift (up to 0.5) or a brightness_swing" in nothing.message
    [seeded] = problems(edit("golden-hour", **{**BREATHE_GOLDEN, "stepped.seed": 3}))
    assert seeded.field == "stepped" and "seed is for mode: shimmer only" in seeded.message
    # a bound pydantic refuses names its field, through the route too
    live = D.load_trio(CONFIG_DIR)
    for name, value in (("brightness_swing", 21), ("mode", "sparkle")):
        body = D.seed_draft(live, "golden-hour").model_dump(mode="json")
        body["stepped"][name] = value
        r = app_client.post("/api/lab/preview", json={"scene_id": "golden-hour", "draft": body})
        assert r.status_code == 422, r.text[:300]
        assert [p["field"] for p in r.json()["problems"]] == [f"stepped.{name}"]
    assert (
        D.field_for("mode 'golden-hour' → techniques → stepped → brightness_swing")
        == "stepped.brightness_swing"
    )
    assert D.field_for("mode 'golden-hour' → techniques → stepped → lag_steps") == "stepped.lag_steps"
    assert D.field_for("mode 'golden-hour' → techniques → stepped → brightness") == "brightness"


def test_a_flat_stepped_frame_is_blamed_on_the_layout_not_the_read_only_run():
    """S3's real stepped rule: an axis-aligned mirror leaves a stepped scene's frames with two colours. The run's
    timing is read-only in Slice A, so the problem names the layout the client just changed."""
    draft = edit("golden-hour", layout={"kind": "linear", "angle_deg": 0, "transform": "mirror"})
    with pytest.raises(D.DraftError) as caught:
        apply(draft)
    assert [(p.field, "each step shows more than two" in p.message) for p in caught.value.problems] == [
        ("layout", True)
    ]


# ---- the fins' rings (D74) ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("n", [2, 3, 4, 5, 6])
def test_fins_module_level_reaches_these_stops(n):
    """Module level reads each module's centre: t 0 / ≈ 0.39 / ≈ 0.67, never the rim (PR D designs for this)."""
    plain = {2: [0, 1], 3: [0, 1], 4: [0, 1, 2], 5: [0, 2, 3], 6: [0, 2, 3]}
    inverted = {2: [0, 1], 3: [1, 2], 4: [1, 2, 3], 5: [1, 2, 4], 6: [2, 3, 5]}
    assert D.reached_stops(F.FinsField(kind="fins"), n, CANOPY) == plain[n]
    assert D.reached_stops(F.FinsField(kind="fins", invert=True), n, CANOPY) == inverted[n]


FIN_NAMES = ["c0", "c1", "c2", "c3", "c4", "c5"]
FIN_HEXES = ["#FF0000", "#00FF00", "#0000FF", "#FFFF00", "#FF00FF", "#00FFFF"]
FIN_RINGS = [["r1c1"], ["r0c1", "r1c0", "r1c2", "r2c1"], ["r0c0", "r0c2", "r2c0", "r2c2"]]


@pytest.mark.parametrize("invert", [False, True])
@pytest.mark.parametrize("n", [2, 3, 4, 5, 6])
def test_the_fins_t3_sentence_names_colours_in_the_order_the_strands_show_them(n, invert):
    """The T3 sentence reads from the centre out: the stop nearest each module's t, centre panel first, then the
    edge-middles, then the corners, each colour once; a stop no module is nearest to is never named."""
    names = FIN_NAMES[:n]
    draft = with_palette(
        "golden-hour",
        list(zip(names, FIN_HEXES[:n], strict=True)),
        solid=0,
        layout={"kind": "fins", "invert": invert},
    )
    t3 = D.generated_copy(TRIO.modes.mode("golden-hour"), draft, CANOPY)["modules"]
    head = "from the centre out: "
    assert head in t3
    said = [
        w.strip(" ,.")
        for w in t3.split(head, 1)[1].replace(" and ", ", ").replace(", then ", ", ").split(",")
    ]
    centres = F.module_centres(CANOPY, [p for ring in FIN_RINGS for p in ring])
    expected: list[str] = []
    for ring in FIN_RINGS:
        for pos in ring:
            t = F.value(draft.layout, *centres[pos], CANOPY)
            nearest = min(range(n), key=lambda i: abs(t - i / (n - 1)))
            if names[nearest] not in expected:
                expected.append(names[nearest])
    assert said == expected, t3
    assert set(said) <= {names[i] for i in D.reached_stops(draft.layout, n, CANOPY)}


def test_a_fins_draft_over_a_canopy_without_fins_is_a_draft_error():
    """The frozen baseline canopy.yaml has no fins section: the Lab says so plainly, before any field is read."""
    bare = load_canopy_config(BASELINE / "canopy.yaml")
    assert not F.has_fins(bare)
    draft = edit("golden-hour", layout={"kind": "fins"})
    want = [D.Problem("the draft of golden-hour → layout", "layout", F.NO_FIN_MAP)]
    with pytest.raises(D.DraftError) as applied:
        D.apply_drafts(TRIO, {"golden-hour": draft}, canopy=bare, settings=SETTINGS)
    assert applied.value.problems == want
    with pytest.raises(D.DraftError) as changed:
        D.draft_changes(TRIO, draft, canopy=bare)
    assert changed.value.problems == want
    # the preview calls resolve_copy before apply_draft: it refuses the same way, not with the field's ValueError
    with pytest.raises(D.DraftError) as copied:
        D.resolve_copy(TRIO, draft, bare)
    assert copied.value.problems == want
    # over the real canopy the same draft applies and reads from the centre out
    patched = apply(draft)
    assert "from the centre out" in patched.modes.mode("golden-hour").techniques.modules.feature
    assert D.draft_changes(TRIO, draft, canopy=CANOPY)["layout"]["words_to"] == "from the centre out"
