"""The ripple (D64): a new mood lands ring by ring in its design's order, one PUT per module, on the real engine,
reconciler, executor and SimDriver (virtual time) with the real looks compiler over a radial and a column design."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fake_compiler import make_world, settings_with

from conductor import rows as R
from conductor.capabilities import detect
from conductor.clock import run_virtual
from conductor.config import ConductorSettings, ConfigError, load_recipes, load_settings
from conductor.engine_api import CanopyPhase, Tier
from conductor.geometry import load_canopy_config
from conductor.looks import CompileContext, compile_mode, ladder, load_modes, parse_modes
from conductor.looks.compile import available_tiers
from conductor.sim.profiles import profile_parts

STAGGER = 1.2
EPS = 0.05
CENTRE = ["canopy-r1c1"]
EDGES = ["canopy-r0c1", "canopy-r1c0", "canopy-r1c2", "canopy-r2c1"]
CORNERS = ["canopy-r0c0", "canopy-r0c2", "canopy-r2c0", "canopy-r2c2"]
COLUMNS = [[f"canopy-r{r}c{c}" for r in range(3)] for c in range(3)]
LIVE = ("canopy-r0c0", "canopy-r0c1")

KIOSK = {
    "title": "The Color Canopy",
    "prompt": "Choose a mood.",
    "tagline": "Make it your mood.",
    "resting": "The canopy is resting.",
    "updating": "Changing the light…",
    "ready": "Ready",
}
STRAND = {
    "look": {"kind": "rgb", "rgb": [247, 242, 231], "brightness": 70},
    "feature": "Colour and dimming: all nine Hubspace strands set to one soft ivory.",
}


def _mode(label: str, field: dict, words: str) -> dict:
    return {
        "label": label,
        "blurb": "A test design",
        "preview": ["#FF9E96", "#C4B3EC"],
        "ladder": ["modules", "strand"],
        "techniques": {
            "modules": {
                "sample": {
                    "canvas": {
                        "kind": "field",
                        "field": field,
                        "stops": [{"at": 0.0, "color": "pastel-coral"}, {"at": 1.0, "color": "lilac"}],
                        "recipe": "rgb_only",
                    },
                    "brightness": 70,
                },
                "feature": f"Colour and dimming: each Hubspace strand set to a single colour, so the palette "
                f"spreads {words}.",
            },
            "strand": STRAND,
        },
    }


CANOPY = load_canopy_config()
# "Radial Calm": from the canopy's centre out in the canopy's proportions, inverted (the palette runs inwards, like
# the P6 draft) — the ripple still starts at the centre, where the layout words say the design starts.
MODES = parse_modes(
    {
        "kiosk": KIOSK,
        "modes": {
            "lowcountry-calm": _mode(
                "Radial Calm",
                {"kind": "radial", "centre_in": [112.5, 60], "metric": "canopy", "invert": True},
                "from the centre out",
            ),
            "column-sunset": _mode("Column Sunset", {"kind": "linear", "angle_deg": 0}, "column by column"),
            # a painted canvas laid out row by row over a module level laid out column by column
            "row-canvas": {
                **_mode("Row Canvas", {"kind": "linear", "angle_deg": 0}, "column by column"),
                "ladder": ["canvas", "modules"],
                "slot": "custom-1",
                "techniques": {
                    **_mode("Row Canvas", {"kind": "linear", "angle_deg": 0}, "column by column")[
                        "techniques"
                    ],
                    "canvas": {
                        "canvas": {
                            "kind": "field",
                            "field": {"kind": "linear", "angle_deg": 90},
                            "stops": [{"at": 0.0, "color": "pastel-coral"}, {"at": 1.0, "color": "lilac"}],
                        },
                        "brightness": 90,
                        "feature": "Every light set individually, saved as a custom Hubspace pattern.",
                    },
                },
            },
        },
    },
    recipes=load_recipes(),
    canopy=CANOPY,
    path=Path("ripple-test-modes.yaml"),
)
CAPS = detect(*profile_parts("sqhdk03-sim"))


class RealCompiler:
    """The looks compiler over the test modes, in the shape the engine calls (compile, ladder, modes)."""

    def __init__(self, registry, *, addresses=None, strip_waves=False):
        self.ctx = CompileContext(
            registry=registry,
            capabilities={e.id: CAPS for e in registry.canopy_modules()},
            modes=MODES,
            recipes=load_recipes(),
            canopy=CANOPY,
            addresses=addresses,
        )
        self.strip_waves = strip_waves

    def compile(self, mode_id, tier):
        plan = compile_mode(mode_id, tier, self.ctx)
        if self.strip_waves:
            plan.waves = None  # what the engine did before the ripple existed
        return plan

    def ladder(self, mode_id):
        return ladder(mode_id, MODES)

    def modes(self):
        return list(MODES.modes)


def ripple_world(stagger: float = STAGGER, *, addresses=None, strip_waves=False, **kiosk):
    settings = settings_with(
        kiosk={"ripple_stagger_s": stagger, "idle_mode": "column-sunset", **kiosk},
    )
    w = make_world(settings=settings)
    w.compiler = RealCompiler(w.registry, addresses=addresses, strip_waves=strip_waves)
    w.engine.deps.compile = w.compiler.compile
    w.engine.deps.ladder = w.compiler.ladder
    w.engine.deps.modes = w.compiler.modes
    writes: list[tuple[float, str, list]] = []
    original = w.sim.write

    async def timed_write(device_id, rows, *, purpose):
        writes.append((w.clock.monotonic(), device_id.removeprefix("sim-"), list(rows)))
        return await original(device_id, rows, purpose=purpose)

    w.sim.write = timed_write
    w.writes = writes
    return w


def since(w, t0: float) -> list[tuple[float, str, list]]:
    return [(t - t0, d, rows) for t, d, rows in w.writes if t >= t0 - 1e-9]


def times_of(writes, device_ids) -> list[float]:
    return [t for t, d, _ in writes if d in device_ids]


def min_gap(writes) -> float:
    last: dict[str, float] = {}
    gap = float("inf")
    for t, d, _ in sorted(writes, key=lambda x: x[0]):
        if d in last:
            gap = min(gap, t - last[d])
        last[d] = t
    return gap


async def settled(w, timeout: float = 20.0) -> float:
    took = await w.until(w.all_correct, timeout=timeout)
    assert took is not None, w.engine.state()
    await w.run_for(2)
    return took


# ---- the compiler ------------------------------------------------------------------------------------------------
def test_compile_orders_the_driven_modules_into_rings_by_the_design():
    from fake_compiler import canopy_registry

    registry = canopy_registry()
    calm = RealCompiler(registry).compile("lowcountry-calm", Tier.T3)
    assert calm.waves == [CENTRE, EDGES, CORNERS]
    assert calm.to_json()["waves"] == calm.waves
    # whole-strand tier: the same look everywhere, still landing in the mode's design order
    assert RealCompiler(registry).compile("lowcountry-calm", Tier.STRAND).waves == [CENTRE, EDGES, CORNERS]
    assert RealCompiler(registry).compile("column-sunset", Tier.T3).waves == COLUMNS
    # partial LIVE: only the driven modules, the one nearer the centre first
    partial = RealCompiler(registry, addresses={d: f"sim-{d}" for d in LIVE}).compile(
        "lowcountry-calm", Tier.T3
    )
    assert set(partial.modules) == set(LIVE) and partial.waves == [["canopy-r0c1"], ["canopy-r0c0"]]


def test_the_show_modes_get_rings_and_a_motion_plan_gets_none():
    from conductor.config import load_registry

    registry = load_registry()
    modes = load_modes()
    ctx = CompileContext(
        registry=registry, capabilities={e.id: CAPS for e in registry.canopy_modules()}, modes=modes
    )
    for mode_id in modes.modes:
        for tier in available_tiers(mode_id, ctx):  # T2 needs a hand-painted slot recorded
            plan = compile_mode(mode_id, tier, ctx)
            assert plan.waves is not None and len(plan.waves) <= 3, (mode_id, tier)
            assert sorted(d for ring in plan.waves for d in ring) == sorted(plan.modules)
    calm = compile_mode("lowcountry-calm", Tier.T3, ctx)
    assert [sorted(ring) for ring in calm.waves] == [sorted(c) for c in COLUMNS]  # its columns: left to right
    # the soak's SIMULATED motion code on Golden Hour's painted slot (config/sim/soak.yaml) makes a motion plan
    from conductor import soak

    motion_config = soak._soak_config_dir(soak.load_soak_motion())
    try:
        moving = load_modes(motion_config)
        motion = compile_mode(
            "golden-hour",
            Tier.T1,
            CompileContext(
                registry=registry,
                capabilities={e.id: CAPS for e in registry.canopy_modules()},
                modes=moving,
                effects_enabled={"motionEffect": {1: "test"}, "brightnessEffect": {}},
                config_dir=motion_config,
            ),
        )
    finally:
        shutil.rmtree(motion_config.parent, ignore_errors=True)
    assert motion.motion and motion.waves is None  # all nine as one burst (07 §6)


def test_the_ring_order_follows_the_technique_compiled():
    from fake_compiler import canopy_registry

    compiler = RealCompiler(canopy_registry())
    rows = [[f"canopy-r{r}c{c}" for c in range(3)] for r in range(3)]
    assert compiler.compile("row-canvas", Tier.T1).waves == rows  # the painted canvas: row by row
    assert compiler.compile("row-canvas", Tier.T3).waves == COLUMNS  # its module level: column by column


def test_modules_with_no_order_to_land_in_go_out_as_one_burst():
    from fake_compiler import canopy_registry

    entries = canopy_registry().canopy_modules()
    # two modules registered at one position: the rings can't say which is which
    doubled = [*entries[:8], entries[8].model_copy(update={"module": entries[0].module})]
    ctx = CompileContext(
        registry=doubled,
        capabilities={e.id: CAPS for e in doubled},
        modes=MODES,
        recipes=load_recipes(),
        canopy=CANOPY,
    )
    assert compile_mode("lowcountry-calm", Tier.STRAND, ctx).waves is None
    # a whole-strand look on a module placed at no canopy position (a bench kit)
    unplaced = [entries[0].model_copy(update={"module": None})]
    ctx = CompileContext(
        registry=unplaced,
        capabilities={e.id: CAPS for e in unplaced},
        modes=MODES,
        recipes=load_recipes(),
        canopy=CANOPY,
    )
    assert compile_mode("lowcountry-calm", Tier.STRAND, ctx).waves is None


def test_design_field_drops_repeat_mirror_and_invert_and_follows_the_tier():
    from conductor.looks.compile import design_field

    calm = MODES.mode("lowcountry-calm")
    spec = design_field(calm, Tier.T3)
    assert spec.kind == "radial" and spec.invert is False and spec.transform == "linear" and spec.cycles == 1
    assert calm.techniques.modules.sample.canvas.field.invert is True  # the mode itself is untouched


# ---- the engine --------------------------------------------------------------------------------------------------
def test_radial_calm_lands_centre_then_edges_then_corners_one_put_each():
    async def main():
        w = ripple_world()
        await w.engine.start()  # the idle look (Column Sunset): start-up is one burst
        await settled(w)
        start = w.clock.monotonic()
        state = await w.engine.request_mode("lowcountry-calm", source="kiosk")
        out = {"accepted": state, "start": start}
        await w.run_for(0.6)
        out["mid"] = w.engine.state()
        corner = w.address("canopy-r0c0")
        out["corner_desired_mid"] = w.tracker.snapshot(corner)["desired"]
        await w.run_for(0.5)  # +1.1: the edges are still waiting
        out["corner_desired_late"] = w.tracker.snapshot(corner)["desired"]
        await w.run_for(1.4)  # +2.5: every ring is out
        out["corner_desired_after"] = w.tracker.snapshot(corner)["desired"]
        out["after_last"] = w.engine.state()
        out["settled"] = await settled(w)
        await w.run_for(10)  # the reconciler's re-assert and rolling reads find nothing to send
        out["final"] = w.engine.state()
        out["plan"] = w.engine.current_plan()
        out["startup"] = since(w, 0.0)[:9]
        out["writes"] = since(w, start)
        out["commands"] = [c for c in w.events("command") if c["label"].startswith("mode: lowcountry-calm")]
        await w.close()
        return out

    out = run_virtual(main)
    writes = out["writes"]
    assert len(writes) == 9 and sorted(d for _, d, _ in writes) == sorted(CENTRE + EDGES + CORNERS)
    assert all(t < EPS for t in times_of(writes, CENTRE))
    assert all(abs(t - STAGGER) < EPS for t in times_of(writes, EDGES))
    assert all(abs(t - 2 * STAGGER) < EPS for t in times_of(writes, CORNERS))
    assert len(out["commands"]) == 9 and {c["label"] for c in out["commands"]} == {
        "mode: lowcountry-calm (ring 1 of 3)",
        "mode: lowcountry-calm (ring 2 of 3)",
        "mode: lowcountry-calm (ring 3 of 3)",
    }
    # start-up went out as one burst
    assert len(out["startup"]) == 9 and max(t for t, _, _ in out["startup"]) < EPS
    # while rings wait, the look is changing and they say why; the ripple says when each ring goes
    mid = out["mid"]
    assert mid.look_phase is CanopyPhase.UPDATING and mid.phase is CanopyPhase.UPDATING
    assert mid.modules["canopy-r0c0"]["status"] == "updating"
    assert mid.modules["canopy-r0c0"]["reason"] == "Waiting for its turn in the ripple."
    assert mid.ripple["stagger_s"] == STAGGER and mid.ripple["rings"] == [
        ["r1c1"],
        ["r0c1", "r1c0", "r1c2", "r2c1"],
        ["r0c0", "r0c2", "r2c0", "r2c2"],
    ]
    assert mid.ripple["t0"] == out["accepted"].changed_at and mid.to_json()["ripple"] == mid.ripple
    # a waiting module keeps its old desired state until its ring is sent (the reconciler can't send it early)
    calm_rows = [r.to_json() for r in out["plan"].modules["canopy-r0c0"].rows]
    assert out["corner_desired_mid"] == out["corner_desired_late"] != calm_rows
    assert out["corner_desired_after"] == calm_rows
    assert out["after_last"].ripple is None and out["after_last"].to_json()["ripple"] is None
    assert out["final"].phase is CanopyPhase.READY and out["final"].ripple is None
    assert min_gap(writes) >= 1.0 - 1e-6


def test_a_newer_mood_drops_the_rings_not_yet_sent():
    async def main():
        w = ripple_world()
        await w.engine.start()
        await settled(w)
        start = w.clock.monotonic()
        await w.engine.request_mode("lowcountry-calm", source="kiosk")
        calm = w.engine.current_plan()
        await w.run_for(1.0)
        state = await w.engine.request_mode("column-sunset", source="ops")
        await settled(w)
        await w.run_for(8)
        out = (calm, state, since(w, start), w.engine.current_plan(), w.all_correct())
        await w.close()
        return out

    calm, state, writes, final, correct = run_virtual(main)
    assert not state.refused and final.mode_id == "column-sunset" and correct

    def colours(rows) -> set[str]:
        return {json.dumps(r.to_json(), sort_keys=True) for r in rows if r.function_class == "color-rgb"}

    calm_colours = set().union(*(colours(mt.rows) for mt in calm.modules.values()))
    late = [rows for t, _, rows in writes if t > 1.0 + 1e-9]
    assert late and not any(
        colours(rows) & calm_colours for rows in late
    )  # no PUT of the old look after +1.0
    assert [d for t, d, _ in writes if t < 1.0 - 1e-9] == CENTRE  # only the centre got Radial Calm
    # the column ripple: column 0 at +1.0, 1 at +2.2, 2 at +3.4 — nine more PUTs, one per module
    newer = [(t, d) for t, d, _ in writes if t >= 1.0 - 1e-9]
    assert len(newer) == 9
    for k, column in enumerate(COLUMNS):
        assert all(abs(t - (1.0 + k * STAGGER)) < EPS for t, d in newer if d in column)
    assert min_gap(writes) >= 1.0 - 1e-6


@pytest.mark.parametrize("how", ["safe_look", "downgrade", "recovery", "reassert_all"])
def test_safe_look_downgrade_recovery_and_reassert_all_are_one_burst(how):
    async def main():
        w = ripple_world()
        await w.engine.start()
        await settled(w)
        await w.engine.request_mode("lowcountry-calm", source="ops")  # a ripple
        await settled(w)
        start = w.clock.monotonic()
        if how == "safe_look":
            await w.engine.safe_look(source="ops")
        elif how == "downgrade":
            assert w.engine.downgrade("test") and w.engine.current_plan().tier is Tier.STRAND
        elif how == "recovery":
            w.engine.enter_outage("test")
            await w.run_for(3)
            w.engine.exit_outage("test")
        else:
            await w.engine.reassert_all(source="ops")
        state = w.engine.state()
        await w.run_for(6)
        out = (since(w, start), state)
        await w.close()
        return out

    writes, state = run_virtual(main)
    first = writes[0][0]
    assert len(writes) == 9 and all(t - first < EPS for t, _, _ in writes), writes
    assert state.ripple is None


@pytest.mark.parametrize("newer", [False, True], ids=["same look", "a look chosen while resting"])
def test_an_outage_mid_ripple_holds_the_rings_and_recovery_sends_the_rest_at_once(newer):
    async def main():
        w = ripple_world()
        await w.engine.start()
        await settled(w)
        start = w.clock.monotonic()
        await w.engine.request_mode("lowcountry-calm", source="kiosk")
        await w.run_for(0.5)
        w.engine.enter_outage("test")
        assert w.engine.state().ripple is not None
        await w.run_for(4.5)  # +5.0: rings 2 and 3 fell due while resting
        resting = since(w, start)
        if newer:  # a look chosen while resting is installed (sent on recovery): the old rings are gone
            await w.engine.request_mode("column-sunset", source="ops")
            mid = w.engine.state()
            assert mid.ripple is None and not w.engine.wave_pending(w.address("canopy-r0c0"))
            assert all("ripple" not in m["reason"] for m in mid.modules.values())
        w.engine.exit_outage("test")
        await settled(w)
        out = (resting, since(w, start), w.engine.state())
        await w.close()
        return out

    resting, writes, state = run_virtual(main)
    assert [d for _, d, _ in resting] == CENTRE  # nothing went out while resting
    after = [(t, d) for t, d, _ in writes if t >= 5.0 - 1e-9]
    assert len(after) == 9 and all(t - 5.0 < EPS for t, _ in after)  # recovery: one burst, every module
    assert state.ripple is None and state.phase is CanopyPhase.READY


def test_a_guest_brightness_mid_ripple_sends_the_waiting_rings_with_it():
    async def main():
        w = ripple_world()
        await w.engine.start()
        await settled(w)
        await w.run_for(10)  # past the start-up cooldown
        start = w.clock.monotonic()
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.run_for(0.5)
        await w.engine.set_guest_brightness(40, source="ops")
        state = w.engine.state()
        await w.run_for(8)
        out = (since(w, start), state)
        await w.close()
        return out

    writes, state = run_virtual(main)
    assert state.ripple is None
    waiting = [t for t, d, _ in writes if d not in CENTRE]
    assert len(waiting) == 8 and all(abs(t - 0.5) < EPS for t in waiting)  # with the brightness, at once
    assert min_gap(writes) >= 1.0 - 1e-6


def test_partial_live_radial_sends_r0c1_first():
    async def main():
        w = ripple_world(addresses={d: f"sim-{d}" for d in LIVE})
        await w.engine.start()
        await w.run_for(5)
        start = w.clock.monotonic()
        await w.engine.request_mode("lowcountry-calm", source="kiosk")
        mid = w.engine.state()
        await w.run_for(8)
        out = (since(w, start), mid)
        await w.close()
        return out

    writes, mid = run_virtual(main)
    assert [(d, round(t, 1)) for t, d, _ in writes] == [("canopy-r0c1", 0.0), ("canopy-r0c0", STAGGER)]
    assert mid.ripple["rings"] == [["r0c1"], ["r0c0"]]
    assert (
        mid.look_phase is CanopyPhase.UPDATING and mid.phase is CanopyPhase.NEEDS_ATTENTION
    )  # /ops: left out


def test_the_idle_hold_still_fires_at_180_s_and_ripples():
    async def main():
        w = ripple_world()
        await w.engine.start()
        await settled(w)
        start = w.clock.monotonic()
        await w.engine.request_mode("lowcountry-calm", source="kiosk")
        await w.run_for(185)
        modes = [e for e in w.events("mode") if e.get("source") == "idle"]
        out = (start, modes, since(w, start), w.engine.current_plan().mode_id)
        await w.close()
        return out

    _start, modes, writes, final = run_virtual(main)
    assert final == "column-sunset" and len(modes) == 2  # start-up, then the hold
    idle = [(t, d) for t, d, _ in writes if t >= 180 - 1e-9]
    assert len(idle) == 9
    held = min(t for t, _ in idle)
    assert 180 <= held <= 180.6  # the reconciler ticks every 0.5 s
    for k, column in enumerate(COLUMNS):
        assert all(abs(t - (held + k * STAGGER)) < EPS for t, d in idle if d in column)


def test_stagger_zero_is_todays_single_burst_exactly():
    async def run(stagger: float, strip: bool):
        w = ripple_world(stagger, strip_waves=strip)
        await w.engine.start()
        await settled(w)
        start = w.clock.monotonic()
        await w.engine.request_mode("lowcountry-calm", source="kiosk")
        mid = w.engine.state()
        await w.run_for(10)
        await w.engine.request_mode("column-sunset", source="ops")
        await w.run_for(10)
        out = (
            [(round(t, 6), d, [r.to_json() for r in rows]) for t, d, rows in since(w, start)],
            [(c["device_id"], c["label"]) for c in w.events("command")],
            mid.ripple,
        )
        await w.close()
        return out

    zero = run_virtual(lambda: run(0, False))
    before = run_virtual(lambda: run(STAGGER, True))  # no rings at all: what the engine did before D64
    assert zero == before and zero[2] is None
    burst = [t for t, _, _ in zero[0] if t < 10]
    assert len(burst) == 9 and max(burst) < EPS
    assert {label for _, label in zero[1]} == {"mode: column-sunset", "mode: lowcountry-calm"}


class _Edited(RealCompiler):
    """The real plans, edited the way another compiler might hand them over."""

    def __init__(self, registry, edit):
        super().__init__(registry)
        self.edit = edit

    def compile(self, mode_id, tier):
        plan = super().compile(mode_id, tier)
        self.edit(plan)
        return plan


@pytest.mark.parametrize(
    "case",
    ["motion plan", "one ring", "a module in no ring"],
)
def test_the_engine_only_ripples_what_it_should(case):
    def edit(plan):
        if case == "motion plan":  # a plan that re-triggers as one burst (07 §6), whatever its rings say
            plan.motion = True
        elif case == "one ring":
            plan.waves = [list(plan.modules)]
        else:  # a ring list that leaves the corners out: they go with the first ring, never not at all
            plan.waves = [w for w in plan.waves if "canopy-r0c0" not in w] if plan.waves else None

    async def main():
        w = ripple_world()
        w.compiler = _Edited(w.registry, edit)
        w.engine.deps.compile = w.compiler.compile
        await w.engine.start()
        await w.run_for(8)
        start = w.clock.monotonic()
        await w.engine.request_mode("lowcountry-calm", source="ops")
        state = w.engine.state()
        await w.run_for(8)
        out = (since(w, start), state, w.engine.state(), w.all_correct())
        await w.close()
        return out

    writes, state, final, correct = run_virtual(main)
    assert len(writes) == 9 and correct and final.ripple is None
    if case == "a module in no ring":
        assert all(t < EPS for t in times_of(writes, CENTRE + CORNERS))  # the corners joined the first ring
        assert all(abs(t - STAGGER) < EPS for t in times_of(writes, EDGES))
        assert state.ripple["rings"][0] == ["r1c1", "r0c0", "r0c2", "r2c0", "r2c2"]
    else:
        assert all(t < EPS for t, _, _ in writes) and state.ripple is None  # one burst


@pytest.mark.parametrize("source", ["reconciler", "recovery", "startup"])
def test_a_re_pick_of_the_look_is_one_burst(source):
    async def main():
        w = ripple_world()
        await w.engine.start()
        await settled(w)
        start = w.clock.monotonic()
        state = await w.engine.request_mode("lowcountry-calm", source=source)
        await w.run_for(6)
        out = (since(w, start), state)
        await w.close()
        return out

    writes, state = run_virtual(main)
    assert len(writes) == 9 and all(t < EPS for t, _, _ in writes) and state.ripple is None


# ---- the reconciler leaves a waiting module alone ---------------------------------------------------------------
async def _drifted_corner_mid_ripple(w):
    """Radial Calm requested just after corner r0c0 was changed outside the Conductor (and read back), so the tracker
    sees it differ from its old desired state while it waits for the last ring."""
    await w.engine.start()
    await settled(w)
    address = w.address("canopy-r0c0")
    w.sim.faults.change_out_of_band(address, [R.power(True), R.color_mode("color"), R.color_rgb(0, 0, 255)])
    start = w.clock.monotonic()
    await w.engine.request_mode("lowcountry-calm", source="ops")
    w.tracker.note_readback(address, await w.sim.read_state(address))
    assert w.tracker.mismatched([address]) and w.engine.wave_pending(address)
    return start


@pytest.mark.parametrize("path", ["reassert", "rolling_read"])
def test_a_waiting_module_is_never_re_sent_early(path):
    async def main():
        w = ripple_world()
        start = await _drifted_corner_mid_ripple(w)
        await w.run_for(0.3)
        if path == "reassert":
            await w.engine.reconciler.reassert()
        else:
            await w.engine.reconciler._read_one("canopy-r0c0", w.engine.target_of("canopy-r0c0"))
        await w.run_for(6)
        out = since(w, start)
        await w.close()
        return out

    writes = run_virtual(main)
    corner = times_of(writes, ["canopy-r0c0"])
    assert corner and min(corner) >= 2 * STAGGER - EPS, writes


def test_a_lock_released_mid_ripple_waits_for_its_ring():
    async def main():
        w = ripple_world()
        await w.engine.start()
        await settled(w)
        address = w.address("canopy-r0c0")
        lease = w.locks.acquire(address, "module page · test")
        await w.run_for(1)  # the reconciler notes the lock
        start = w.clock.monotonic()
        await w.engine.request_mode("lowcountry-calm", source="kiosk")
        await w.run_for(0.6)
        w.locks.release(address, lease.token)
        await w.run_for(8)
        out = (since(w, start), w.all_correct())
        await w.close()
        return out

    writes, correct = run_virtual(main)
    corner = times_of(writes, ["canopy-r0c0"])
    assert corner and abs(min(corner) - 2 * STAGGER) < EPS and correct, writes


# ---- the setting -------------------------------------------------------------------------------------------------
def test_ripple_stagger_setting_is_validated(tmp_path):
    assert load_settings().kiosk.ripple_stagger_s == 0  # one burst until Alon flips it (D64)
    for ok in (0, 0.5, 1.2):
        assert (
            ConductorSettings.model_validate({"kiosk": {"ripple_stagger_s": ok}}).kiosk.ripple_stagger_s == ok
        )
    for bad, words in ((-0.1, "from 0"), (1.6, "from 0"), (1.3, "within 2.4 s"), (float("nan"), "from 0")):
        with pytest.raises(ValueError, match=words):
            ConductorSettings.model_validate({"kiosk": {"ripple_stagger_s": bad}})
    (tmp_path / "conductor.yaml").write_text("kiosk:\n  ripple_stagger_s: 1.5\n")
    with pytest.raises(ConfigError) as e:
        load_settings(tmp_path)
    assert "kiosk → ripple_stagger_s" in str(e.value) and "ripple_stagger_s ≤ 1.2" in str(e.value)
