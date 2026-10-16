"""The guest panel's canopy picture (conductor/canopy_view.py): the 270-light geometry and the per-position truth table
over a compiled plan, in the simulator's world (sim capabilities on all nine modules).

Proves: the geometry is the ceiling in lattice order (never the wiring); a T1 canvas lands on the lattice slot at each
head's own canopy point (no serpentine), a B module's wire 0 at its rotated corner; module level and whole-strand looks
are flat; colours are full strength whatever the level; off / dark / unknown carry no colour and every rule of the
table holds; nothing names a device; `kiosk.preview` validates with a clear message.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest
from pydantic import ValidationError

from conductor import render
from conductor import rows as R
from conductor.canopy_view import (
    canopy_geometry,
    lattice_slots,
    lit_state,
    load_canopy,
    module_colours,
)
from conductor.capabilities import detect
from conductor.config import CONFIG_DIR, ConductorSettings, ConfigError, PreviewSettings, load_settings
from conductor.engine_api import CanopyPhase, CanopyState, ModePlan, ModuleTarget, Tier
from conductor.looks import CompileContext, compile_mode, module_look
from conductor.looks.composer import evaluate
from conductor.scenes import load_scenes
from conductor.sim.profiles import profile_parts

CFG = load_canopy(str(CONFIG_DIR))
CAPS = detect(*profile_parts("sqhdk03-sim"))


class _Registry:
    def __init__(self, entries):
        self.entries = list(entries)

    def canopy_modules(self):
        return list(self.entries)


def _ctx(**kw) -> CompileContext:
    from conductor.config import load_registry

    registry = load_registry()
    return CompileContext(
        registry=registry, capabilities={m.id: CAPS for m in registry.canopy_modules()}, **kw
    )


CTX = _ctx()
REGISTRY = CTX.registry
MODULES = REGISTRY.canopy_modules()
BY_POSITION = {m.module: m for m in MODULES}
GEOMETRY = canopy_geometry(CFG)
LATTICE = {m["pos"]: m["heads"] for m in GEOMETRY["modules"]}


def _state(plan: ModePlan, *, status: str = "ready", **kw) -> CanopyState:
    """The engine's state over `plan`: every planned module at `status`, the others left out."""
    modules = {
        m.id: {
            "status": status if m.id in plan.modules else "needs_attention",
            "reason": f"{m.label} reason text",
            "label": m.label,
            "module": m.module,
            "locked": False,
            "simulated": True,
            "left_out": m.id not in plan.modules,
            "stuck": False,
        }
        for m in MODULES
    }
    state = CanopyState(
        mode=plan.mode_id,
        label=plan.label,
        tier=str(plan.tier),
        feature=plan.feature,
        phase=CanopyPhase.READY,
        guest_input=True,
        cooldown_until=None,
        changed_at=0.0,
        modules=modules,
        simulated=True,
        brightness=70,
    )
    return replace(state, **kw)


def _hex(rgb) -> str:
    r, g, b = rgb
    return f"#{r:02X}{g:02X}{b:02X}"


def _by_pos(lit: dict) -> dict[str, dict]:
    return {m["pos"]: m for m in lit["modules"]}


def _full_strength(entry: R.FrameEntry) -> str:
    """One canvas entry as a lit head at device brightness 100 (render's own per-head rule)."""
    rows = R.full_state_vector(R.Look("slot", brightness=100, slot="custom-3"))
    value = {R.FRAME_CLASS: {"frameBuffer": {"framebuffer": [entry.to_json()]}}}
    (c,) = render.head_colors([*rows, R.slot_write("custom-3", value)], 1, CAPS)
    return _hex((c["r"], c["g"], c["b"]))


# ---- geometry -------------------------------------------------------------------------------------------------------
def test_the_geometry_is_270_heads_in_nine_modules_of_30_in_lattice_order():
    g = GEOMETRY
    assert (g["width_in"], g["height_in"], g["module_w_in"], g["module_h_in"]) == (225, 120, 75, 40)
    assert g["view"] == {"mirror_x": False, "rotate_deg": 0, "dim_floor": 0.35}
    assert [m["pos"] for m in g["modules"]] == [f"r{r}c{c}" for r in range(3) for c in range(3)]
    heads = [tuple(h) for m in g["modules"] for h in m["heads"]]
    assert len(heads) == 270 and len(set(heads)) == 270
    for m in g["modules"]:
        assert len(m["heads"]) == 30
        x0, y0 = m["col"] * 75, m["row"] * 40
        assert all(x0 < x < x0 + 75 and y0 < y < y0 + 40 for x, y in m["heads"])
        assert m["heads"] == sorted(m["heads"], key=lambda p: (p[1], p[0]))  # top to bottom, left to right
    view = PreviewSettings(mirror_x=True, rotate_deg=180, dim_floor=0.5)
    assert canopy_geometry(CFG, view)["view"] == {"mirror_x": True, "rotate_deg": 180, "dim_floor": 0.5}


def test_a_b_modules_wire_0_lands_in_its_rotated_corner():
    entry = BY_POSITION["r0c1"]  # p14: the middle column hangs B (A rotated 180°)
    slots = lattice_slots(CFG, entry)
    assert sorted(slots) == list(range(30))
    assert LATTICE["r0c1"][slots[0]] == [147.5, 35.0]
    assert LATTICE["r0c0"][lattice_slots(CFG, BY_POSITION["r0c0"])[0]] == [2.5, 5.0]


def test_a_wiring_that_isnt_one_to_one_on_the_lattice_falls_back_to_one_flat_mean(caplog):
    # an A-hung kit placed where the scheme hangs B: its heads don't sit on that position's lattice
    entry = BY_POSITION["r0c1"].model_copy(update={"id": "flat-fallback-test", "orientation": "A"})
    assert lattice_slots(CFG, entry) is None
    colors = [{"r": 200, "g": 0, "b": 0}] * 15 + [{"r": 0, "g": 0, "b": 100}] * 15
    with caplog.at_level(logging.WARNING, logger="conductor.canopy_view"):
        out = module_colours(CFG, entry, colors)
    assert out == ["#640032"] * 30
    assert "one flat colour" in caplog.text and "flat-fallback-test" in caplog.text


# ---- the truth table over compiled plans ----------------------------------------------------------------------------
def test_a_t1_canvas_is_on_each_heads_own_lattice_point_not_along_the_wire():
    plan = compile_mode("golden-hour", Tier.T1, CTX)
    canvas = CTX.modes.mode("golden-hour").techniques.canvas.canvas
    lit = lit_state(CFG, REGISTRY, plan, _state(plan))
    scene = load_scenes().get("golden-hour")
    along_wire = [_hex(rgb) for rgb in scene.frame(30)]
    for pos, m in _by_pos(lit).items():
        assert m["state"] == "lit" and m["color"] is None, (pos, m)
        expected = [
            _full_strength(evaluate(canvas, x, y, canopy=CFG, recipes=CTX.recipes)) for x, y in LATTICE[pos]
        ]
        assert m["colors"] == expected, pos
        # the picture is the ceiling, not the scene's 30-light serpentine
        assert m["colors"] != along_wire, pos
    assert lit["level"] == 70 and lit["motion"] is None


def test_module_level_is_nine_flat_colours_from_the_plan():
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    lit = lit_state(CFG, REGISTRY, plan, _state(plan))
    mode = CTX.modes.mode("coastal-tides")
    for pos, m in _by_pos(lit).items():
        assert m == {
            "pos": pos,
            "state": "lit",
            "color": _hex(module_look(mode, BY_POSITION[pos], CTX).rgb),
            "colors": None,
        }


def test_a_whole_strand_look_is_one_colour_on_all():
    plan = compile_mode("lowcountry-calm", Tier.STRAND, CTX)
    lit = lit_state(CFG, REGISTRY, plan, _state(plan))
    assert {m["state"] for m in lit["modules"]} == {"lit"}
    assert {m["color"] for m in lit["modules"]} == {"#F7F2E7"}  # soft ivory at full strength
    assert all(m["colors"] is None for m in lit["modules"])


def test_a_guest_level_never_changes_a_colour():
    """The level rides separately; colours are full strength (never the plan's preview at its designed 85%)."""
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    designed = lit_state(CFG, REGISTRY, plan, _state(plan, brightness=85))
    dimmed = lit_state(CFG, REGISTRY, plan, _state(plan, brightness=30, guest_brightness=30))
    assert designed["level"] == 85 and dimmed["level"] == 30
    assert designed["modules"] == dimmed["modules"]
    r0c0 = _by_pos(dimmed)["r0c0"]["color"]
    assert r0c0 == "#6FB5D5"  # [111, 181, 213], not dimmed to the look's 85%


def test_the_guest_turning_the_canopy_off_is_all_off_with_no_level():
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    lit = lit_state(CFG, REGISTRY, plan, _state(plan, guest_power=False))
    assert lit["level"] is None
    assert all(m == {"pos": m["pos"], "state": "off", "color": None, "colors": None} for m in lit["modules"])


def test_a_partial_plan_is_two_lit_and_seven_dark():
    live = [BY_POSITION["r0c0"], BY_POSITION["r0c1"]]
    plan = compile_mode("golden-hour", Tier.T3, _ctx(addresses={m.id: f"addr-{m.id}" for m in live}))
    assert sorted(plan.modules) == ["canopy-r0c0", "canopy-r0c1"]
    lit = _by_pos(lit_state(CFG, REGISTRY, plan, _state(plan, status="updating")))
    assert [lit[p]["state"] for p in ("r0c0", "r0c1")] == ["changing", "changing"]
    assert all(lit[p]["color"] for p in ("r0c0", "r0c1"))  # a change in flight shows its target colours
    dark = [m for p, m in lit.items() if p not in ("r0c0", "r0c1")]
    assert len(dark) == 7 and all(m["state"] == "dark" and m["color"] is None for m in dark)


def _with(state: CanopyState, device_id: str, **record) -> CanopyState:
    modules = {k: dict(v) for k, v in state.modules.items()}
    modules[device_id].update(record)
    return replace(state, modules=modules)


@pytest.mark.parametrize(
    "record",
    [{"locked": True, "status": "locked"}, {"stuck": True, "status": "needs_attention"}],
    ids=["a lease", "stuck"],
)
def test_a_locked_or_stuck_module_makes_no_colour_claim(record):
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    lit = _by_pos(lit_state(CFG, REGISTRY, plan, _with(_state(plan), "canopy-r1c1", **record)))
    assert lit["r1c1"] == {"pos": "r1c1", "state": "unknown", "color": None, "colors": None}
    assert all(m["state"] == "lit" for p, m in lit.items() if p != "r1c1")


def test_off_is_claimed_only_where_it_reads_back_off_never_on_a_locked_or_stuck_module():
    """The engine skips a locked module and a stuck one didn't take the last write, so turning the canopy off from the
    guest panel leaves them as they were: they make no claim, and only a position that reads back off says off."""
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    state = _state(plan, guest_power=False)
    state = _with(state, "canopy-r1c1", locked=True, status="locked")
    state = _with(state, "canopy-r0c2", stuck=True, status="needs_attention")
    state = _with(state, "canopy-r2c0", status="updating")  # the power-off is on its way
    lit = _by_pos(lit_state(CFG, REGISTRY, plan, state))
    no_claim = {"state": "unknown", "color": None, "colors": None}
    for pos in ("r1c1", "r0c2", "r2c0"):
        assert lit[pos] == {"pos": pos, **no_claim}, pos
    assert all(m["state"] == "off" for p, m in lit.items() if p not in ("r1c1", "r0c2", "r2c0"))
    # resting (an outage, say) while off: nothing is sent, so nothing is known either
    resting = lit_state(CFG, REGISTRY, plan, _state(plan, guest_power=False, phase=CanopyPhase.RESTING))
    assert {m["state"] for m in resting["modules"]} == {"unknown"}
    # a partial canopy turned off: the positions out of the look are unlit too
    live = [BY_POSITION["r0c0"], BY_POSITION["r0c1"]]
    partial = compile_mode("golden-hour", Tier.T3, _ctx(addresses={m.id: f"addr-{m.id}" for m in live}))
    off = lit_state(CFG, REGISTRY, partial, _state(partial, guest_power=False))
    assert {m["state"] for m in off["modules"]} == {"off"}


def test_a_module_not_yet_confirmed_is_changing_with_its_target_colour():
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    ready = _by_pos(lit_state(CFG, REGISTRY, plan, _state(plan)))
    state = _with(_state(plan), "canopy-r2c2", status="needs_attention")
    lit = _by_pos(lit_state(CFG, REGISTRY, plan, state))
    assert lit["r2c2"]["state"] == "changing" and lit["r2c2"]["color"] == ready["r2c2"]["color"]


def test_a_slot_whose_content_isnt_known_makes_no_colour_claim():
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    # as T2 leaves a slot whose readback doesn't match and that has no design to show
    plan.modules["canopy-r0c2"] = replace(plan.modules["canopy-r0c2"], preview=[], colors=[])
    lit = _by_pos(lit_state(CFG, REGISTRY, plan, _state(plan)))
    assert lit["r0c2"]["state"] == "unknown" and lit["r0c2"]["color"] is None


def test_a_resting_canopy_makes_no_colour_claim_but_paused_guest_input_still_shows_the_look():
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    # an outage, the engine stopped or starting, slots being set up
    resting = lit_state(CFG, REGISTRY, plan, _state(plan, phase=CanopyPhase.RESTING))
    assert {m["state"] for m in resting["modules"]} == {"unknown"}
    paused = lit_state(CFG, REGISTRY, plan, _state(plan, phase=CanopyPhase.RESTING, guest_input=False))
    assert {m["state"] for m in paused["modules"]} == {"lit"}


def test_a_preset_the_strand_plays_on_its_own_makes_no_colour_claim():
    rows = R.full_state_vector(R.Look("preset", brightness=60, preset="christmas"))
    target = ModuleTarget("canopy-r0c0", rows, rows, colors=render.head_colors(rows, 30))
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    plan.modules["canopy-r0c0"] = target
    lit = _by_pos(lit_state(CFG, REGISTRY, plan, _state(plan)))
    assert lit["r0c0"]["state"] == "unknown" and lit["r0c0"]["colors"] is None


def test_no_plan_is_no_picture():
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    assert lit_state(CFG, REGISTRY, None, _state(plan)) is None


def test_the_picture_names_no_device_label_reason_or_vendor():
    plan = compile_mode("golden-hour", Tier.T1, CTX)
    states = [_state(plan), _with(_state(plan), "canopy-r1c1", locked=True), _state(plan, guest_power=False)]
    text = json.dumps([canopy_geometry(CFG), *(lit_state(CFG, REGISTRY, plan, s) for s in states)])
    for word in ("canopy-r", "sim-", "Module ", "reason", "Hubspace", "label", "device"):
        assert word not in text, word


# ---- the ripple (D64) -----------------------------------------------------------------------------------------------
RINGS = [["r1c1"], ["r0c1", "r1c0", "r1c2", "r2c1"], ["r0c0", "r0c2", "r2c0", "r2c2"]]
RIPPLE = {"t0": 1000.0, "stagger_s": 1.2, "rings": RINGS}


def test_a_ripple_in_progress_is_the_panels_motion_ring_by_ring():
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    lit = lit_state(CFG, REGISTRY, plan, _state(plan, status="updating", ripple=RIPPLE))
    assert lit["motion"] == {"kind": "ripple", "t0": 1000.0, "stagger_s": 1.2, "rings": RINGS}
    assert {m["state"] for m in lit["modules"]} == {"changing"}  # every ring shows its target colour
    # no ring left to send (or one burst): a still picture
    assert lit_state(CFG, REGISTRY, plan, _state(plan))["motion"] is None


def test_a_ripple_only_moves_the_positions_it_really_changes():
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    state = _with(_state(plan, ripple=RIPPLE), "canopy-r1c1", locked=True, status="locked")
    motion = lit_state(CFG, REGISTRY, plan, state)["motion"]
    # the locked centre makes no claim, so it isn't in a ring — and the empty ring keeps its place (the times hold)
    assert motion["rings"] == [[], RINGS[1], RINGS[2]]
    # a partial canopy: the dark positions are drawn as they are
    live = [BY_POSITION["r0c0"], BY_POSITION["r0c1"]]
    partial = compile_mode("golden-hour", Tier.T3, _ctx(addresses={m.id: f"addr-{m.id}" for m in live}))
    motion = lit_state(CFG, REGISTRY, partial, _state(partial, ripple=RIPPLE))["motion"]
    assert motion["rings"] == [[], ["r0c1"], ["r0c0"]]


@pytest.mark.parametrize(
    "kw",
    [{"guest_power": False}, {"phase": CanopyPhase.RESTING}],
    ids=["off", "resting"],
)
def test_no_ripple_is_drawn_while_off_or_resting(kw):
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    assert lit_state(CFG, REGISTRY, plan, _state(plan, ripple=RIPPLE, **kw))["motion"] is None
    # nor when no position in it is lit or changing
    every = _state(plan, ripple=RIPPLE)
    for m in MODULES:
        every = _with(every, m.id, locked=True, status="locked")
    assert lit_state(CFG, REGISTRY, plan, every)["motion"] is None


# ---- full-strength colours in the plan -------------------------------------------------------------------------------
def test_the_plan_keeps_preview_at_the_look_and_colors_at_full_strength():
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    target = plan.modules["canopy-r0c0"]
    assert target.colors == [{"r": 111, "g": 181, "b": 213}] * 30
    assert target.preview == [{"r": 94, "g": 154, "b": 181}] * 30  # 85% for /ops and the visualizer
    t1 = compile_mode("golden-hour", Tier.T1, CTX).modules["canopy-r0c0"]
    assert len(t1.colors) == len(t1.preview) == 30 and t1.colors != t1.preview


# ---- kiosk.preview -------------------------------------------------------------------------------------------------
def test_the_preview_view_defaults_and_validation(tmp_path):
    assert load_settings().kiosk.preview == PreviewSettings()
    assert PreviewSettings().model_dump() == {"mirror_x": False, "rotate_deg": 0, "dim_floor": 0.35}
    for bad in ({"rotate_deg": 45}, {"dim_floor": 1.5}, {"dim_floor": -0.1}, {"tilt": 3}):
        with pytest.raises(ValidationError):
            ConductorSettings.model_validate({"kiosk": {"preview": bad}})
    (tmp_path / "conductor.yaml").write_text("kiosk:\n  preview:\n    rotate_deg: 45\n")
    with pytest.raises(ConfigError) as e:
        load_settings(tmp_path)
    assert "kiosk → preview → rotate_deg" in str(e.value) and "0, 90, 180 or 270" in str(e.value)
