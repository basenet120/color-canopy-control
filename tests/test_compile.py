"""compile_mode: every mode at every tier of its ladder → one PUT's rows per module, honest copy, bench gating."""

from __future__ import annotations

import dataclasses

import pytest
from technique_modes import TECHNIQUE_MODES as TM  # the pre-D59 recipes, a test fixture

from conductor import rows as R
from conductor.capabilities import detect
from conductor.config import DeviceFlags, load_registry
from conductor.drivers.base import TELEMETRY_CLASSES
from conductor.engine_api import ModePlan, PlanError, Tier
from conductor.looks import CompileContext, available_tiers, compile_first, compile_mode, ladder, load_modes
from conductor.looks.schema import MOTION_WORDS
from conductor.sim.profiles import profile_parts

REGISTRY = load_registry()
MODULES = REGISTRY.canopy_modules()
MODES = load_modes()
FUNCTIONS, STATES = profile_parts("sqhdk03-sim")
METADEVICE_ID = "0" * 8 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 4 + "-" + "0" * 12  # a UUID-length id


def _caps(**changes):
    caps = detect(FUNCTIONS, STATES)
    return dataclasses.replace(caps, **changes) if changes else caps


def _slot_readback():
    return {r.function_instance: r.value for r in STATES if r.function_class == R.FRAME_CLASS}


def _ctx(caps=None, **kw) -> CompileContext:
    caps = caps or _caps()
    kw.setdefault("slot_readback", {m.id: _slot_readback() for m in MODULES})
    return CompileContext(registry=REGISTRY, capabilities={m.id: caps for m in MODULES}, **kw)


def _recorded():
    return {m.id: {"custom-1": "a1" * 8, "custom-2": "b2" * 8, "custom-3": "c3" * 8} for m in MODULES}


def _app_origin(hashes: dict) -> dict:
    """Every recorded hash marked as hand-painted in the Hubspace app (what T2 needs)."""
    return {d: {slot: "app" for slot in slots} for d, slots in hashes.items()}


def _painted(hashes: dict | None = None) -> dict:
    hashes = _recorded() if hashes is None else hashes
    return {"recorded_hashes": hashes, "recorded_origins": _app_origin(hashes)}


def _golden_with_code(code: int):
    modes = TM.model_copy(deep=True)
    modes.modes["golden-hour"].techniques.canvas.motion_effect.motionEffect = code
    return modes


def _check_plan(plan: ModePlan) -> None:
    assert sorted(plan.modules) == sorted(m.id for m in MODULES)
    for device_id, target in plan.modules.items():
        keys = [r.key for r in target.rows]
        assert len(keys) == len(set(keys)), f"{device_id}: a row appears twice in one PUT"
        assert not any(r.function_class in TELEMETRY_CLASSES for r in target.rows)
        assert not any(r.function_class == R.FRAME_CLASS for r in target.rows), (
            "slot objects go at commissioning"
        )
        assert R.power(True) in target.rows
        assert (
            ("toggle", "party-mode") in keys
            and ("brightness", None) in keys
            and ("speed", "color-sequence") in keys
        )
        assert len(target.preview) == 30 and all(set(c) == {"r", "g", "b"} for c in target.preview)
        assert R.payload_bytes(METADEVICE_ID, target.rows, 1_790_000_000_000) < 1024  # show time: a select
    if MOTION_WORDS.search(
        plan.feature
    ):  # copy may claim motion only for a motion plan (the converse needn't hold:
        assert plan.motion, (
            plan.feature
        )  # a slot that may animate is re-triggered as one burst under static copy)


@pytest.mark.parametrize(
    ("mode_id", "tier"), [(m, t) for m, mode in MODES.modes.items() for t in (Tier(x) for x in mode.ladder)]
)
def test_every_mode_compiles_at_every_tier_of_its_ladder(mode_id, tier):
    plan = compile_mode(mode_id, tier, _ctx(**_painted()))
    assert plan.mode_id == mode_id and plan.tier is tier and plan.label == MODES.mode(mode_id).label
    _check_plan(plan)
    if tier is Tier.T1:
        slot = MODES.mode(mode_id).slot
        for target in plan.modules.values():
            assert target.slot == slot and target.slot_hash == R.slot_hash(target.slot_value)
            assert ("color-individual", "custom") in [r.key for r in target.rows]
            assert R.slot_select(slot)[0] in target.rows
            # readback of exactly this plan (rows + the slot object) matches `expect`, slot object included
            assert R.compare(target.expect, [*target.rows, R.slot_write(slot, target.slot_value)]) == []
            drifted = R.slot_write(slot, R.frame_value(None, [R.FrameEntry()] * 30, expected_length=30))
            assert R.compare(target.expect, [*target.rows, drifted]), "a drifted slot must be a mismatch"
            write = [R.slot_write(slot, target.slot_value), *R.slot_select(slot), R.power(True)]
            assert R.payload_bytes(METADEVICE_ID, write, 1_790_000_000_000) < 4096  # 04: ~3.4 KB


def test_ladder_is_read_from_the_modes_file():
    assert ladder("golden-hour") == [Tier.T1, Tier.T2, Tier.T3, Tier.STRAND]
    assert ladder("lowcountry-calm") == [Tier.T3, Tier.STRAND]
    assert ladder("lowcountry-calm", TM) == [Tier.STRAND]
    with pytest.raises(PlanError, match="no mode 'disco'"):
        ladder("disco")
    with pytest.raises(PlanError, match="has no canvas technique"):
        compile_mode("lowcountry-calm", Tier.T1, _ctx())


# ---- techniques no show mode uses since D59, on the pre-D59 recipes (tests/fixtures_modes_techniques.yaml) ----------
def test_goodnight_clamps_2200_k_to_the_device_floor_with_a_note():
    plan = compile_mode("goodnight-glow", Tier.STRAND, _ctx(modes=TM))
    rows = plan.modules["canopy-r1c1"].rows
    assert R.StateRow("color-temperature", None, "2700") in rows and R.brightness(10) in rows
    assert any("2200 K clamped to the device floor 2700 K" in n and "all 9 modules" in n for n in plan.notes)
    assert plan.feature.startswith("Dimming and the warmest white")
    # a device that goes down to 2200 K gets 2200 K and no note
    wide = compile_mode("goodnight-glow", Tier.STRAND, _ctx(_caps(cct_range=(2200, 6500)), modes=TM))
    assert (
        R.StateRow("color-temperature", None, "2200") in wide.modules["canopy-r0c0"].rows and not wide.notes
    )


def test_calm_falls_back_to_rgb_ivory_with_true_copy_when_white_is_unsupported():
    plan = compile_mode("lowcountry-calm", Tier.STRAND, _ctx(_caps(cct_range=None), modes=TM))
    rows = plan.modules["canopy-r0c0"].rows
    assert R.color_mode("color") in rows and R.color_rgb(255, 190, 120) in rows
    assert "2700" not in plan.feature and "nine" in plan.feature and "one colour" in plan.feature
    assert any("fallback look" in n for n in plan.notes)


def test_holiday_picks_the_first_preset_every_module_lists():
    plan = compile_mode("holiday-at-home", Tier.STRAND, _ctx(modes=TM))
    rows = plan.modules["canopy-r2c2"].rows
    assert R.preset("snowfall") in rows and R.speed(-8) in rows and R.brightness(70) in rows
    assert R.color_mode("sequence") in rows
    assert plan.feature == TM.mode("holiday-at-home").techniques.strand.feature
    fewer = _caps(presets=["mistletoe", "christmas", "rainbow"])
    assert (
        R.preset("mistletoe")
        in compile_mode("holiday-at-home", Tier.STRAND, _ctx(fewer, modes=TM)).modules["canopy-r0c0"].rows
    )
    # one module lacking mistletoe: the canopy stays uniform on the next preset all nine list
    mixed = {m.id: fewer for m in MODULES}
    mixed["canopy-r1c1"] = _caps(presets=["christmas"])
    uniform = compile_mode(
        "holiday-at-home", Tier.STRAND, CompileContext(registry=REGISTRY, capabilities=mixed, modes=TM)
    )
    assert {t.rows[2] for t in uniform.modules.values()} == {R.preset("christmas")}


def test_holiday_without_any_listed_preset_uses_its_fallback_look_and_copy():
    plan = compile_mode("holiday-at-home", Tier.STRAND, _ctx(_caps(presets=["rainbow"]), modes=TM))
    strand = TM.mode("holiday-at-home").techniques.strand
    assert plan.feature == strand.fallback_feature
    assert R.StateRow("color-temperature", None, "2700") in plan.modules["canopy-r0c0"].rows
    assert any("none of the presets" in n for n in plan.notes)


def test_golden_hour_is_a_static_glow_until_a_motion_code_is_bench_enabled():
    canvas = TM.mode("golden-hour").techniques.canvas
    static = compile_mode("golden-hour", Tier.T1, _ctx(modes=TM))
    assert static.motion is False and static.feature == canvas.feature
    assert not MOTION_WORDS.search(static.feature)
    header = static.modules["canopy-r0c0"].slot_value[R.FRAME_CLASS]
    assert header["motionEffect"] == 0 and header["brightnessEffect"] == 0
    assert any("Motion is off" in n for n in static.notes)

    # the code is configured but not enabled on the bench: still static
    configured = compile_mode("golden-hour", Tier.T1, _ctx(modes=_golden_with_code(4)))
    assert configured.motion is False and configured.feature == canvas.feature
    assert any("isn't bench-enabled" in n for n in configured.notes)

    # enabled on the bench but not saved in the slots yet (F6): the code goes into every module's header (for
    # commissioning / the slot rewrite), the plan re-triggers as one burst, but the copy doesn't claim motion
    enabled = {"motionEffect": {4: "scroll left→right (benched)"}, "brightnessEffect": {}}
    pending = compile_mode("golden-hour", Tier.T1, _ctx(modes=_golden_with_code(4), effects_enabled=enabled))
    assert pending.motion is True and pending.feature == canvas.feature
    assert any("not saved in custom-3" in n for n in pending.notes)
    # every slot holds the code (recorded from readback at commissioning): motion copy
    held = {m.id: {"custom-3": "c3" * 8} for m in MODULES}
    moving = compile_mode(
        "golden-hour",
        Tier.T1,
        _ctx(
            modes=_golden_with_code(4),
            effects_enabled=enabled,
            recorded_hashes=held,
            recorded_effects={
                m.id: {"custom-3": {"motionEffect": 4, "brightnessEffect": 0}} for m in MODULES
            },
        ),
    )
    assert moving.motion is True and moving.feature == canvas.motion_feature
    assert MOTION_WORDS.search(moving.feature)
    for target in moving.modules.values():
        inner = target.slot_value[R.FRAME_CLASS]
        assert inner["motionEffect"] == 4 and inner["motionSpeed"] == canvas.motion_effect.motionSpeed
    # T2 and T3 never claim motion
    assert compile_mode("golden-hour", Tier.T3, _ctx(modes=TM)).motion is False


def test_static_canvas_resets_effects_copied_from_the_readback_header():
    readback = _slot_readback()
    animated = dict(readback["custom-1"][R.FRAME_CLASS], id=7, motionEffect=5, brightnessEffect=2)
    readback["custom-1"] = {R.FRAME_CLASS: animated}
    plan = compile_mode("rainbow-row-sunset", Tier.T1, _ctx(slot_readback={m.id: readback for m in MODULES}))
    inner = plan.modules["canopy-r0c0"].slot_value[R.FRAME_CLASS]
    assert inner["id"] == 7  # header copied from the device's readback
    assert (
        inner["motionEffect"] == 0 and inner["brightnessEffect"] == 0
    )  # but the look is static, as the copy says
    no_readback = compile_mode("rainbow-row-sunset", Tier.T1, _ctx(slot_readback={}))
    assert any("default slot header" in n for n in no_readback.notes)


def test_t2_refuses_until_the_hand_painted_slot_is_recorded():
    with pytest.raises(PlanError, match="hand-painted slot not verified yet"):
        compile_mode("garden-gathering", Tier.T2, _ctx())
    recorded = _recorded()
    recorded["canopy-r2c1"].pop("custom-2")
    with pytest.raises(PlanError, match="custom-2 on canopy-r2c1"):
        compile_mode("garden-gathering", Tier.T2, _ctx(**_painted(recorded)))
    plan = compile_mode("garden-gathering", Tier.T2, _ctx(**_painted()))
    target = plan.modules["canopy-r0c0"]
    assert target.slot == "custom-2" and target.slot_hash == "b2" * 8 and target.slot_value is None
    assert R.slot_select("custom-2")[0] in target.rows and R.brightness(75) in target.rows
    # the registry's own recorded hashes are used when the context doesn't pass any
    entries = [
        m.model_copy(update={"slot_hashes": {"custom-2": "d4" * 8}, "slot_origin": {"custom-2": "app"}})
        for m in MODULES
    ]
    ctx = CompileContext(registry=entries, capabilities={m.id: _caps() for m in MODULES})
    assert compile_mode("garden-gathering", Tier.T2, ctx).modules["canopy-r0c0"].slot_hash == "d4" * 8


def test_t2_preview_shows_the_readback_only_when_it_matches_the_recorded_hash():
    readback = _slot_readback()
    real = R.slot_hash(readback["custom-2"])
    recorded = {m.id: {"custom-2": real} for m in MODULES}
    plan = compile_mode("garden-gathering", Tier.T2, _ctx(**_painted(recorded)))
    assert plan.modules["canopy-r0c0"].preview[0] == {"r": 0, "g": 191, "b": 0}  # green profile slot × 75 %
    # a stale readback isn't shown as if it were the painted look: the preview falls back to the canvas design
    stale = compile_mode("garden-gathering", Tier.T2, _ctx(**_painted()))
    design = compile_mode("garden-gathering", Tier.T1, _ctx())
    assert stale.modules["canopy-r0c0"].preview == design.modules["canopy-r0c0"].preview
    assert any("Preview shows the design" in n for n in stale.notes)


def test_features_differ_per_tier_and_only_motion_copy_mentions_movement():
    enabled = {"motionEffect": {4: "scroll"}, "brightnessEffect": {}}
    for mode_id in MODES.modes:
        features = []
        for tier in ladder(mode_id):
            plan = compile_mode(mode_id, tier, _ctx(**_painted()))
            assert not MOTION_WORDS.search(plan.feature), (mode_id, tier)
            features.append(plan.feature)
        assert len(set(features)) == len(features), mode_id
    moving = compile_mode("golden-hour", Tier.T1, _ctx(modes=_golden_with_code(4), effects_enabled=enabled))
    assert not MOTION_WORDS.search(moving.feature)  # the code isn't saved in the slots yet: static copy


def test_module_level_looks_follow_canopy_position():
    # each module = the scene's gradient at its column centre (config/modes.yaml), at the scene's brightness
    sunset = compile_mode("rainbow-row-sunset", Tier.T3, _ctx())
    for row in range(3):
        assert R.color_rgb(255, 175, 175) in sunset.modules[f"canopy-r{row}c0"].rows
        assert R.color_rgb(255, 210, 224) in sunset.modules[f"canopy-r{row}c1"].rows
        assert R.color_rgb(216, 189, 232) in sunset.modules[f"canopy-r{row}c2"].rows
    assert all(R.brightness(85) in t.rows for t in sunset.modules.values())
    calm = compile_mode("lowcountry-calm", Tier.T3, _ctx())  # mirrored: the side columns match
    assert R.color_rgb(209, 234, 227) in calm.modules["canopy-r1c1"].rows
    assert R.color_rgb(234, 239, 230) in calm.modules["canopy-r0c0"].rows
    assert R.color_rgb(234, 239, 230) in calm.modules["canopy-r2c2"].rows


def test_module_level_rows_and_middle_row_layouts_follow_canopy_position():
    sunset = compile_mode("rainbow-row-sunset", Tier.T3, _ctx(modes=TM))
    assert R.color_rgb(249, 99, 2) in sunset.modules["canopy-r0c2"].rows
    assert R.StateRow("color-temperature", None, "2700") in sunset.modules["canopy-r2c0"].rows
    garden = compile_mode("garden-gathering", Tier.T3, _ctx(modes=TM))
    assert R.brightness(100) in garden.modules["canopy-r1c0"].rows  # the centre row is the path
    assert R.color_rgb(255, 90, 0) in garden.modules["canopy-r0c1"].rows
    golden = compile_mode("golden-hour", Tier.T3, _ctx(modes=TM))
    assert R.color_rgb(255, 170, 60) in golden.modules["canopy-r1c2"].rows


def test_the_strand_fallback_is_the_scenes_solid_colour_on_every_module():
    plan = compile_mode("coastal-tides", Tier.STRAND, _ctx())
    for target in plan.modules.values():  # aqua, as RGB, at the scene's brightness
        assert R.color_rgb(121, 206, 212) in target.rows
        assert R.color_mode("color") in target.rows and R.brightness(85) in target.rows
    assert plan.feature == "Colour and dimming: all nine Hubspace strands set to aqua."


def test_bench_flags_gate_t1_and_t2_on_live_hardware():
    unproven = {m.id: DeviceFlags() for m in MODULES}
    with pytest.raises(PlanError, match="Gate C"):
        compile_mode("rainbow-row-sunset", Tier.T1, _ctx(flags=unproven))
    with pytest.raises(PlanError, match="Gate B"):
        compile_mode("rainbow-row-sunset", Tier.T2, _ctx(flags=unproven, **_painted()))
    proven = {m.id: DeviceFlags(t1_frames=True, t2_select=True) for m in MODULES}
    assert compile_mode("rainbow-row-sunset", Tier.T1, _ctx(flags=proven)).tier is Tier.T1
    # live before the bench: the ladder lands on T3 and says why
    plan = compile_first("rainbow-row-sunset", _ctx(flags=unproven))
    assert plan.tier is Tier.T3 and any("Gate C" in n for n in plan.notes)
    assert available_tiers("rainbow-row-sunset", _ctx(flags=unproven)) == [Tier.T3, Tier.STRAND]
    assert compile_first("golden-hour", _ctx(), skip=[Tier.T1]).tier is Tier.T3


def test_a_wrong_head_count_refuses_t1_but_t3_still_compiles():
    short = _caps(framebuffer_lengths={"custom-1": 12, "custom-2": 12, "custom-3": 12})
    with pytest.raises(PlanError, match="reports 12 heads but this module should have 30"):
        compile_mode("rainbow-row-sunset", Tier.T1, _ctx(short))
    assert compile_first("rainbow-row-sunset", _ctx(short)).tier is Tier.T3


def test_a_device_without_capabilities_or_individual_mode_is_refused_plainly():
    with pytest.raises(PlanError, match="hasn't been read yet"):
        compile_mode("goodnight-glow", Tier.STRAND, CompileContext(registry=REGISTRY, capabilities={}))
    # the safe look never waits for a profile (F2): conservative capabilities, and the plan says so
    safe = compile_mode("lowcountry-calm", Tier.STRAND, CompileContext(registry=REGISTRY, capabilities={}))
    assert len(safe.modules) == 9 and not safe.skipped and any("assumes" in n for n in safe.notes)
    no_individual = _caps(color_modes=["white", "color", "sequence"])
    with pytest.raises(PlanError, match="no per-head"):
        compile_mode("garden-gathering", Tier.T1, _ctx(no_individual))


def test_plan_json_is_serialisable():
    import json

    plan = compile_mode("rainbow-row-sunset", Tier.T1, _ctx())
    body = json.loads(json.dumps(plan.to_json()))
    assert body["tier"] == "canvas" and body["modules"]["canopy-r0c0"]["slot"] == "custom-1"


@pytest.mark.parametrize(
    ("mode_id", "tier"), [(m, t) for m, mode in MODES.modes.items() for t in (Tier(x) for x in mode.ladder)]
)
def test_each_target_carries_its_device_brightness_range_for_guest_dimming(mode_id, tier):
    plan = compile_mode(mode_id, tier, _ctx(_caps(brightness_range=(20, 80)), **_painted()))
    assert {mt.brightness_range for mt in plan.modules.values()} == {(20, 80)}
