"""Per-light motion and the rate test (D80): a stepped rung's `lights` block, its gate, its frames, and the rehearsal's
ladder. The 18 options' catalog rules are tests/test_scene_rules.py's; the panel and Lab drawing are their own tests'."""

from __future__ import annotations

from conductor import rows as R
from conductor.clock import run_virtual
from conductor.config import CONFIG_DIR, DeviceFlags
from conductor.design_lab.preview import design_caps, design_registry
from conductor.engine_api import Tier
from conductor.looks import load_modes
from conductor.looks.compile import CompileContext, compile_mode, light_level
from conductor.looks.schema import LightsMotion
from conductor.rehearsal import RATE_RUNG_S, RATE_RUNGS, rate_schedule

MODES = load_modes(CONFIG_DIR)
REGISTRY = design_registry()
CAPS = design_caps()
PER_LIGHT = [
    m for m, mode in MODES.modes.items() if mode.techniques.stepped and mode.techniques.stepped.lights
]


def _ctx(**kw) -> CompileContext:
    return CompileContext(registry=REGISTRY, capabilities={d.id: CAPS for d in REGISTRY}, modes=MODES, **kw)


def test_eighteen_options_carry_a_per_light_rung():
    # D80: the six scenes' #4-#6; then the Light Show scene (Oct 21), every one of its six looks per-light
    light_show = [m for m in PER_LIGHT if m.rsplit("-", 1)[0] == "light-show" or m == "light-show"]
    d80 = [m for m in PER_LIGHT if m not in light_show]
    assert len(d80) == 18 and len(PER_LIGHT) == 24
    assert {m.rsplit("-", 1)[1] for m in d80} == {"4", "5", "6"}
    assert light_show == ["light-show", *(f"light-show-{n}" for n in range(2, 7))]


def test_per_light_is_opt_in_and_falls_back_to_the_module_level_steps():
    mode_id = "coastal-tides-5"
    off = compile_mode(mode_id, Tier.STEPPED, _ctx())
    assert off.program is not None and not off.program.lights and off.program.mode == "breathe"
    assert any("Per-light motion not used" in n for n in off.notes)
    on = compile_mode(mode_id, Tier.STEPPED, _ctx(lights=True))
    assert on.program is not None and on.program.lights and on.program.mode == "lights-ripple"


def test_on_live_hardware_each_module_needs_gate_c():
    mode_id = "lowcountry-calm-5"
    unproven = {d.id: DeviceFlags() for d in REGISTRY}
    plan = compile_mode(mode_id, Tier.STEPPED, _ctx(lights=True, flags=unproven))
    assert not plan.program.lights and any("Gate C" in n for n in plan.notes)
    proven = {d.id: DeviceFlags(t1_frames=True) for d in REGISTRY}
    assert compile_mode(mode_id, Tier.STEPPED, _ctx(lights=True, flags=proven)).program.lights


def test_every_step_is_one_put_per_module_with_the_whole_frame_and_the_select():
    plan = compile_mode("lowcountry-calm-5", Tier.STEPPED, _ctx(lights=True, lights_slot="custom-3"))
    steps = plan.program.steps
    assert len(steps) == MODES.mode("lowcountry-calm-5").techniques.stepped.lights.steps_per_cycle
    assert steps[0].rows == {d: t.rows for d, t in plan.modules.items()}  # step 0 is the hold
    levels = set()
    for step in steps:
        for rows in step.rows.values():
            keys = [r.key for r in rows]
            assert (R.FRAME_CLASS, "custom-3") in keys and ("color-individual", "custom") in keys
            frame = R.frame_entries(next(r.value for r in rows if r.key[0] == R.FRAME_CLASS))
            levels |= {e.colorBrightness for e in frame}
        assert all(len(h) == 4 * 30 for h in step.heads.values())
    assert 0 in levels and max(levels) == 100  # the glow switches lights off and brings them to full


def test_a_ripple_rolls_outward_step_by_step():
    lights = LightsMotion(
        effect="ripple",
        rings=1.0,
        steps_per_cycle=8,
        floor=0,
        peak=100,
        moving_feature="Colour and dimming through the Hubspace cloud: every light set on its own, sent every few "
        "seconds.",
    )
    crest = [max(range(11), key=lambda i, p=p: light_level(lights, i / 10, p)) for p in (0.0, 0.25, 0.5)]
    assert crest == sorted(crest) and crest[0] < crest[-1]  # the brightest ring moves out as p grows


def test_the_rate_test_steps_4_3_2_1_5_then_1_s_and_reports_each_rung():
    schedule = rate_schedule()
    assert schedule[0] == RATE_RUNGS[0] and schedule[-1] == RATE_RUNGS[-1]
    assert abs(sum(schedule) - RATE_RUNG_S * len(RATE_RUNGS)) < 1e-6

    from tests.test_stepped_rehearsal import show

    async def main():
        w = await show()
        result = await w.engine.rehearse("garden-gathering", 1, source="ops", ladder=True)
        await w.run_for(RATE_RUNG_S * len(RATE_RUNGS) + 30)
        await w.ended()
        report = w.report()
        await w.close()
        return result, report

    result, report = run_virtual(main)
    assert result["ok"] and report["kind"] == "rate_test" and report["completed"]
    assert [r["step_s"] for r in report["rungs"]] == list(RATE_RUNGS)
    assert all(r["attempts"] > 0 and r["clean"] for r in report["rungs"])
    assert report["fastest_clean_step_s"] == min(RATE_RUNGS)  # the simulator has no account limit
    assert "max_writes_60s" not in {c["id"] for c in report["criteria"]}  # the rate test measures the account


def test_a_rate_test_can_run_its_own_paces_each_for_its_own_time():
    # Oct 21 (venue): 4 s and 3 s were known good, so only 2, 1.5 and 1 s, half a minute each
    from tests.test_stepped_rehearsal import show

    async def main():
        w = await show()
        preview = w.engine.rehearsal_preview(
            "garden-gathering", 1, ladder=True, rungs=(2.0, 1.5, 1.0), rung_s=30.0
        )
        result = await w.engine.rehearse(
            "garden-gathering", 1, source="ops", ladder=True, rungs=(2.0, 1.5, 1.0), rung_s=30.0
        )
        await w.run_for(90 + 30)
        await w.ended()
        report = w.report()
        await w.close()
        return preview, result, report

    preview, result, report = run_virtual(main)
    assert preview["ladder"] == [2.0, 1.5, 1.0] and preview["rung_s"] == 30.0
    assert "every 2, 1.5, 1 s, 30 s each" in preview["confirm"]
    assert result["ok"] and report["kind"] == "rate_test" and report["completed"]
    assert [r["step_s"] for r in report["rungs"]] == [2.0, 1.5, 1.0]
    assert all(r["attempts"] > 0 for r in report["rungs"])
    assert rate_schedule((2.0, 1.5, 1.0), 30.0) == [2.0] * 15 + [1.5] * 20 + [1.0] * 30


def test_per_light_runs_pace_by_their_own_cap_and_module_level_keeps_its_own():
    """D81: on nine kits a per-light run steps every 2 s (motion.lights cap 337.5 less the 20 % reserve, Oct 21), while a
    module-level run keeps the stepped cap's 7.5 s (its nine-kit Gate R pace, what the soak validates)."""
    from conductor.config import load_settings
    from conductor.design_lab.stepped import effective_step_s

    settings = load_settings()
    assert effective_step_s(1.5, 9, settings, lights=True) == 2.0
    assert effective_step_s(6.0, 9, settings) == 7.5
    assert effective_step_s(1.5, 1, settings, lights=True) == 1.5  # one kit: its designed pace


def test_on_at_sends_each_light_at_peak_or_off():
    # Oct 21 (venue): level 5 looks about as bright as 100, so a Light Show look sends a light fully on or off
    lights = LightsMotion(
        effect="ripple",
        rings=1.0,
        steps_per_cycle=8,
        floor=0,
        peak=100,
        on_at=55,
        moving_feature="Colour and dimming through the Hubspace cloud: every light set on its own, sent every few "
        "seconds.",
    )
    levels = {light_level(lights, i / 20, p) for i in range(21) for p in (0.0, 0.25, 0.5)}
    assert levels == {0, 100}
    plan = compile_mode("light-show", Tier.STEPPED, _ctx(lights=True))
    sent = {
        e.colorBrightness
        for step in plan.program.steps
        for rows in step.rows.values()
        for e in R.frame_entries(next(r.value for r in rows if r.key[0] == R.FRAME_CLASS))
    }
    assert sent == {0, 100}


def test_on_at_needs_floor_zero():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="needs floor 0"):
        LightsMotion(
            effect="glow",
            floor=10,
            on_at=50,
            moving_feature="Colour and dimming through the Hubspace cloud: every light set on its own, sent every "
            "few seconds.",
        )
