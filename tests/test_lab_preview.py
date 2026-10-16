"""The Design Lab's preview (conductor/design_lab/preview.py, PLAN §4): a draft through the REAL compiler with the
draft's OWN recipes — the design (`mode_design`), every tier's sentence (`compile_mode`), what this runtime's lights
would show today (the running show's registry, flags, addresses and D62 left-outs), the pinned show-box view — plus
the T1 lock after commissioning. Zero writes.

Most tests build a LabService over a stand-in Services (no app); the "today" tests that need a real show run the app's
own ShowWiring on the simulator on virtual time (tests/test_stepped_rehearsal.py's harness).
"""

from __future__ import annotations

import shutil
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_compiler import settings_with

from conductor.canopy_view import module_colours
from conductor.clock import VirtualClock, run_virtual
from conductor.config import CONFIG_DIR, ConductorSettings, DeviceEntry, DeviceFlags, load_registry
from conductor.design_lab import draft as D
from conductor.design_lab import preview as P
from conductor.design_lab.service import LabService
from conductor.engine_api import Tier
from conductor.looks import CompileContext, compile_mode, load_modes, scene_rules
from conductor.routes.design import mode_design
from conductor.show import ShowWiring
from conductor.sim.settings import SimSettings

REGISTRY = load_registry()
MODULES = REGISTRY.canopy_modules()
CAPS = P.design_caps()
TRIO = D.load_trio(CONFIG_DIR)
IDS = [s.id for s in TRIO.scenes.scenes]
LIVE = ("r0c0", "r0c1")
D62_TWO = "Colour and dimming: two Hubspace strands, each set to a single colour from the scene."
QUIET_S = 70.0  # start-up (capability reads, simulator commissioning, the idle look) is over
NEW_PINK = "#FF8FBF"
DONE_MEANS = {"palette.2.hex": NEW_PINK, "layout": {"kind": "linear", "angle_deg": 30}, "brightness": 90}


def edit(scene_id: str, **changes) -> D.SceneDraft:
    data = D.seed_draft(TRIO, scene_id).model_dump()
    for key, value in changes.items():
        parts = key.split(".")
        target = data
        for p in parts[:-1]:
            target = target[int(p)] if p.isdigit() else target[p]
        target[parts[-1]] = value
    return D.SceneDraft.model_validate(data)


def services(tmp: Path, *, simulated: bool = True, registry=REGISTRY, settings=None, show=None, engine=None):
    """The parts of Services a LabService reads."""
    extras = {}
    if show is not None:
        extras["show"] = show
    if engine is not None:
        extras["canopy"] = engine
    return SimpleNamespace(
        config_dir=CONFIG_DIR,
        data=tmp,
        clock=VirtualClock(),
        telemetry=SimpleNamespace(record=lambda *a, **k: None),
        settings=settings or ConductorSettings(),
        engine_driver=SimpleNamespace(simulated=simulated, name="sim"),
        extras=extras,
        registry=registry,
    )


def make_lab(tmp: Path, **kw) -> LabService:
    return LabService(services(tmp, **kw))


class FakeWiring:
    """ShowWiring's read-only surface the Lab uses: `context(modes=)` (no recipes: like the real one, it compiles with
    git's recipes unless the Lab replaces them) and `tier_allowed`."""

    def __init__(self, *, addresses: dict | None = None, flags: dict | None = None, refuse=()):
        self.addresses = addresses or {m.id: f"md-{m.id}" for m in MODULES}
        self.flags = flags
        self.refuse = {Tier(t) for t in refuse}

    def context(self, modes=None):
        return CompileContext.from_settings(
            registry=REGISTRY,
            capabilities={m.id: CAPS for m in MODULES},
            settings=ConductorSettings(),
            flags=self.flags,
            modes=modes if modes is not None else load_modes(CONFIG_DIR),
            config_dir=CONFIG_DIR,
            addresses=self.addresses,
        )

    def tier_allowed(self, mode_id, tier):
        return (False, f"{tier} refused here") if tier in self.refuse else (True, "")


def partial_live() -> FakeWiring:
    """The Mac show box: devices only at r0c0/r0c1 (D62), no bench gate proven, stepped off live."""
    return FakeWiring(
        addresses={m.id: (f"md-{m.id}" if m.module in LIVE else None) for m in MODULES},
        flags={m.id: DeviceFlags() for m in MODULES},
        refuse=("canvas", "stepped"),
    )


def hexes(design_modules: list[dict]) -> dict[str, str]:
    return {m["module"]: "#" + "".join(f"{c:02X}" for c in m["rgb"]) for m in design_modules}


def lit_colours(view: dict) -> dict[str, str | None]:
    return {m["pos"]: m["color"] for m in view["lit"]["modules"]}


# ---- the design and the tiers -------------------------------------------------------------------------------------------
def test_preview_heads_are_mode_design_of_the_patched_modes(tmp_path):
    lab = make_lab(tmp_path)
    draft = edit("golden-hour", **DONE_MEANS)
    answer = P.preview(lab, draft)
    patched = D.apply_draft(TRIO, draft, canopy=lab.canopy, settings=lab.settings)
    ctx = CompileContext(
        registry=[], capabilities={}, modes=patched.modes, recipes=patched.recipes, canopy=lab.canopy
    )
    assert answer["design"] == mode_design("golden-hour", ctx)
    assert len(answer["design"]["heads"]) == 270 and len(answer["design"]["modules"]) == 9
    # ... and not the approved design
    approved = CompileContext(
        registry=[], capabilities={}, modes=TRIO.modes, recipes=TRIO.recipes, canopy=lab.canopy
    )
    assert answer["design"]["heads"] != mode_design("golden-hour", approved)["heads"]
    assert answer["scene_id"] == "golden-hour" and answer["label"] == "Golden Hour"
    assert answer["approved"] is False and answer["brightness"] == 90
    assert answer["stepped"] is None and answer["rings"] is None


def _design_heads(answer: dict) -> dict[tuple[float, float], list[int]]:
    return {(h["x"], h["y"]): h["rgb"] for h in answer["design"]["heads"]}


def test_the_new_colour_shows_at_t1_and_t3_exactly():
    """Golden Hour's Sunset pink re-hexed, laid out left to right (linear 0°): every head and every module centre at
    x = 112.5 in (t = 0.5, the middle stop) shows exactly the new pink at full strength, at T1 and at T3."""
    lab = make_lab(Path(tempfile.mkdtemp()))
    answer = P.preview(
        lab,
        edit(
            "golden-hour",
            **{"palette.2.hex": NEW_PINK, "brightness": 80},
            layout={"kind": "linear", "angle_deg": 0},
        ),
    )
    middle = [rgb for (x, _y), rgb in _design_heads(answer).items() if x == 112.5]
    assert len(middle) == 6 and all(
        rgb == [255, 143, 191] for rgb in middle
    )  # T1: the heads under the centre line
    t3 = hexes(answer["design"]["modules"])
    assert t3["r0c1"] == t3["r1c1"] == t3["r2c1"] == NEW_PINK  # T3: the middle column's centres
    strand = next(t for t in answer["tiers"] if t["tier"] == "strand")
    assert strand["solid"] == [255, 143, 191]


def test_the_done_means_edit_shows_the_new_colour_at_t1_and_t3(tmp_path):
    """The brief's edit (Sunset pink → #FF8FBF, linear 30°, brightness 90): against the same layout with the approved
    pink, exactly the heads and modules the middle stop reaches change, every one toward the new pink (less green,
    less blue), and nothing else changes."""
    lab = make_lab(tmp_path)
    old = P.preview(lab, edit("golden-hour", **{**DONE_MEANS, "palette.2.hex": "#FF9BC4"}))
    new = P.preview(lab, edit("golden-hour", **DONE_MEANS))
    old_heads, new_heads = _design_heads(old), _design_heads(new)
    changed = [p for p in new_heads if new_heads[p] != old_heads[p]]
    assert len(changed) > 60, "the middle third of the design reaches many heads"
    for p in changed:
        (r0, g0, b0), (r1, g1, b1) = old_heads[p], new_heads[p]
        assert r1 == r0 and g1 <= g0 and b1 <= b0, p
    old_t3, new_t3 = hexes(old["design"]["modules"]), hexes(new["design"]["modules"])
    assert [m for m in new_t3 if new_t3[m] != old_t3[m]], "module level shows the new pink too"
    # each tier's own colours come from the same patched recipes: the compiled T1 frames and T3 rows agree
    patched = D.apply_draft(TRIO, edit("golden-hour", **DONE_MEANS), canopy=lab.canopy, settings=lab.settings)
    ctx = P._design_context(patched, lab.canopy)
    t3_plan = compile_mode("golden-hour", Tier.T3, ctx)
    for d in P.design_registry():
        (rgb,) = [r.value["color-rgb"] for r in t3_plan.modules[d.id].rows if r.function_class == "color-rgb"]
        assert "#{r:02X}{g:02X}{b:02X}".format(**rgb) == new_t3[d.module]
    t1_plan = compile_mode("golden-hour", Tier.T1, ctx)
    for d in P.design_registry():
        drawn = sorted(
            "#{:02X}{:02X}{:02X}".format(*h["rgb"]) for h in new["design"]["heads"] if h["module"] == d.module
        )
        sent = sorted(module_colours(lab.canopy, d, t1_plan.modules[d.id].colors))
        assert drawn == sent, d.module


@pytest.mark.parametrize("scene_id", IDS)
def test_each_tier_sentence_is_compile_modes(tmp_path, scene_id):
    lab = make_lab(tmp_path)
    for draft in (
        D.seed_draft(TRIO, scene_id),
        edit(scene_id, layout={"kind": "radial", "centre_in": [0, 0]}),
    ):
        answer = P.preview(lab, draft)
        patched = D.apply_draft(TRIO, draft, canopy=lab.canopy, settings=lab.settings)
        ctx = P._design_context(patched, lab.canopy)
        mode = patched.modes.mode(scene_id)
        assert [t["tier"] for t in answer["tiers"]] == list(mode.ladder)
        for tier in answer["tiers"]:
            assert tier["label"] == P.TIERS[tier["tier"]][0]
            if tier["tier"] == "slot":
                assert tier["feature"] == mode.techniques.slot.feature
                continue
            plan = compile_mode(scene_id, Tier(tier["tier"]), ctx)
            assert tier["feature"] == plan.feature
            if tier["tier"] == "stepped":
                assert tier["moving_feature"] == plan.program.moving_feature
            if tier["tier"] == "strand":
                assert tier["solid"] == list(mode.techniques.strand.look.rgb)


def test_the_resolved_copy_is_reported_and_an_explicit_approved_is_never_refused_in_preview(tmp_path):
    lab = make_lab(tmp_path)
    answer = P.preview(lab, edit("golden-hour", **{"palette.2.hex": NEW_PINK}))
    assert answer["copy"]["modules"]["chosen"] == "approved"
    assert answer["copy"]["stepped"]["chosen"] == "approved"
    stale = edit("golden-hour", **DONE_MEANS, copy={"modules": "approved", "stepped": "approved"})
    answer = P.preview(lab, stale)  # no DraftError: preview resolves it as auto and says why
    modules = answer["copy"]["modules"]
    assert modules["chosen"] == "generated" and "no longer describes this design" in modules["why"]
    feature = next(t["feature"] for t in answer["tiers"] if t["tier"] == "modules")
    assert modules["sentence"] == feature and "diagonally" in feature
    assert P.preview(lab, D.seed_draft(TRIO, "lowcountry-calm"))["copy"]["stepped"] is None


def test_the_changes_are_the_drafts(tmp_path):
    lab = make_lab(tmp_path)
    answer = P.preview(lab, edit("golden-hour", **DONE_MEANS))
    assert answer["changes"] == D.draft_changes(TRIO, edit("golden-hour", **DONE_MEANS), canopy=lab.canopy)
    assert answer["changes"]["needs_slot_repaint"] is True
    assert P.preview(lab, D.seed_draft(TRIO, "golden-hour"))["approved"] is True


def test_golden_hour_at_linear_0_is_a_200_with_a_two_d_warning(tmp_path):
    """D63's design-quality rules warn in the preview and never refuse it (§3.4); S3's real rules, no stand-in:
    linear 0° reads as stripes at module level, the Done-means edit (linear 30°) doesn't."""
    lab = make_lab(tmp_path)
    answer = P.preview(lab, edit("golden-hour", layout={"kind": "linear", "angle_deg": 0}))
    assert [(w["rule"], w["field"]) for w in answer["warnings"]] == [("two-d", "layout")]
    assert answer["warnings"][0]["message"].startswith(scene_rules.STRIPES)
    assert P.preview(lab, edit("golden-hour", **DONE_MEANS))["warnings"] == []


def test_the_layout_words_are_the_drafts_own_edited_or_not(tmp_path):
    from conductor.looks import field as F

    lab = make_lab(tmp_path)
    seed = D.seed_draft(TRIO, "golden-hour")
    assert P.preview(lab, seed)["layout_words"] == F.layout_words(seed.layout, lab.canopy)
    edited = edit("golden-hour", **DONE_MEANS)
    words = P.preview(lab, edited)["layout_words"]
    assert (
        words
        == F.layout_words(edited.layout, lab.canopy)
        == D.draft_changes(TRIO, edited, canopy=lab.canopy)["layout"]["words_to"]
    )


def test_brightness_is_a_level_never_multiplied_into_colours(tmp_path):
    lab = make_lab(tmp_path, show=FakeWiring(refuse=("canvas",)))
    dim = P.preview(lab, edit("coastal-tides", brightness=20))
    bright = P.preview(lab, edit("coastal-tides", brightness=95))
    assert dim["design"] == bright["design"]
    assert [t.get("solid") for t in dim["tiers"]] == [t.get("solid") for t in bright["tiers"]]
    assert lit_colours(dim["today"]) == lit_colours(bright["today"])
    assert (dim["brightness"], bright["brightness"]) == (20, 95)
    assert (dim["today"]["lit"]["level"], bright["today"]["lit"]["level"]) == (20, 95)
    assert dim["show_box_today"]["lit"]["level"] == 20


# ---- today -------------------------------------------------------------------------------------------------------------
def test_today_without_an_engine_says_so(tmp_path):
    today = P.preview(make_lab(tmp_path), D.seed_draft(TRIO, "golden-hour"))["today"]
    assert today["tier"] is None and today["lit"] is None and today["note"] == P.NO_ENGINE
    assert today["title"] == P.SIM_TITLE


def test_today_on_a_partial_live_canopy_lights_only_the_driven_modules(tmp_path):
    lab = make_lab(tmp_path, simulated=False, show=partial_live())
    answer = P.preview(lab, edit("golden-hour", **DONE_MEANS))
    today = answer["today"]
    assert today["title"] == P.LIVE_TITLE
    assert today["tier"] == "modules" and today["heads"] == "draft"
    assert today["feature"] == D62_TWO
    assert today["positions"] == {"driven": list(LIVE), "left_out": [p for p in D.GRID if p not in LIVE]}
    states = {m["pos"]: m["state"] for m in today["lit"]["modules"]}
    assert [p for p, s in states.items() if s == "lit"] == list(LIVE)
    assert [p for p, s in states.items() if s == "dark"] == [p for p in D.GRID if p not in LIVE]
    assert all(
        m["color"] is None and m["colors"] is None for m in today["lit"]["modules"] if m["state"] == "dark"
    )
    t3 = hexes(answer["design"]["modules"])
    assert {p: c for p, c in lit_colours(today).items() if c} == {p: t3[p] for p in LIVE}
    assert "left out" in today["note"]


def test_todays_lit_colours_are_the_drafts_recipes(tmp_path):
    lab = make_lab(tmp_path, show=FakeWiring(refuse=("canvas", "stepped")))
    approved = P.preview(lab, D.seed_draft(TRIO, "coastal-tides"))
    hexed = P.preview(lab, edit("coastal-tides", **{"palette.1.hex": "#6FD0D8"}))
    assert hexed["today"]["tier"] == "modules"
    assert lit_colours(hexed["today"]) == hexes(hexed["design"]["modules"])  # the T3 tab's colours
    assert lit_colours(hexed["today"]) != lit_colours(approved["today"])
    # a renamed colour has a recipe key git's recipes don't know: still module level, never the strand fallback
    renamed = P.preview(
        lab, edit("coastal-tides", **{"palette.1.name": "Lagoon", "palette.1.hex": "#6FD0D8"})
    )
    assert renamed["today"]["tier"] == "modules"
    assert lit_colours(renamed["today"]) == lit_colours(hexed["today"])


def test_the_show_box_view_lights_only_r0c0_and_r0c1_with_the_d62_sentence(tmp_path):
    lab = make_lab(tmp_path)
    for draft in [D.seed_draft(TRIO, s) for s in IDS] + [edit("golden-hour", **DONE_MEANS)]:
        answer = P.preview(lab, draft)
        view = answer["show_box_today"]
        assert view["title"] == P.SHOW_BOX_TITLE
        assert view["tier"] == "modules", draft.scene_id
        assert view["feature"] == D62_TWO
        assert view["positions"]["driven"] == list(LIVE)
        states = {m["pos"]: m["state"] for m in view["lit"]["modules"]}
        assert states == {p: ("lit" if p in LIVE else "dark") for p in D.GRID}
        t3 = hexes(answer["design"]["modules"])
        assert {p: c for p, c in lit_colours(view).items() if c} == {p: t3[p] for p in LIVE}


def test_the_show_box_steps_only_once_alon_enables_it(tmp_path):
    settings = settings_with(
        motion={"stepped": {**ConductorSettings().motion.stepped.model_dump(), "live_enabled": True}}
    )
    view = P.preview(make_lab(tmp_path, settings=settings), D.seed_draft(TRIO, "coastal-tides"))[
        "show_box_today"
    ]
    assert view["tier"] == "stepped"


# ---- today on the app's own show wiring (simulator, virtual time) -----------------------------------------------------
async def _show():
    from conductor.services import build_services

    data = Path(tempfile.mkdtemp(prefix="lab-preview-"))
    sim = SimSettings.from_acceptance().with_(throttle_429_rate=0.0, silent_drop_rate=0.0)
    services = build_services(
        {"CONDUCTOR_DATA_DIR": str(data)}, clock=VirtualClock(), config_dir=CONFIG_DIR, sim_settings=sim
    )
    services.telemetry.dir = None
    await services.start()
    wiring = ShowWiring(services)
    services.extras["show"] = wiring
    await wiring.start()
    await services.clock.sleep(QUIET_S)

    async def close():
        await wiring.close()
        await services.close()
        shutil.rmtree(data, ignore_errors=True)

    return services, wiring, data, close


def test_today_in_sim_is_titled_what_this_simulator_shows_and_t1_shows_the_approved_heads():
    async def main():
        services, wiring, _data, close = await _show()
        try:
            lab = LabService(services)
            golden = P.preview(lab, edit("golden-hour", **DONE_MEANS))
            tides = P.preview(lab, edit("coastal-tides", **{"palette.1.hex": "#6FD0D8"}))
            approved_t1 = wiring.compile("golden-hour", Tier.T1)  # the git config: what the slots hold
            return golden, tides, approved_t1
        finally:
            await close()

    golden, tides, approved_t1 = run_virtual(main)
    today = golden["today"]
    assert today["title"] == P.SIM_TITLE
    assert today["tier"] == "canvas" and today["heads"] == "approved"
    assert today["note"] == P.HEADS_APPROVED
    for m in today["lit"]["modules"]:
        entry = next(e for e in MODULES if e.module == m["pos"])
        expected = module_colours(_canopy(), entry, approved_t1.modules[entry.id].colors)
        assert m["state"] == "lit" and m["colors"] == expected, m["pos"]
    draft_heads = {}
    for h in golden["design"]["heads"]:
        draft_heads.setdefault(h["module"], []).append("#{:02X}{:02X}{:02X}".format(*h["rgb"]))
    assert any(sorted(m["colors"]) != sorted(draft_heads[m["pos"]]) for m in today["lit"]["modules"])
    assert today["lit"]["level"] == 90
    # a module-level scene in sim: the draft's own stepped hold on all nine modules
    assert tides["today"]["title"] == P.SIM_TITLE
    assert tides["today"]["tier"] == "stepped" and tides["today"]["heads"] == "draft"
    assert lit_colours(tides["today"]) == hexes(tides["design"]["modules"])
    assert tides["today"]["moving_feature"]


def _canopy() -> dict:
    from conductor.geometry import load_canopy_config

    return load_canopy_config(CONFIG_DIR / "canopy.yaml")


def test_preview_writes_nothing_and_is_fast():
    """No driver write, no telemetry event, no file while previewing every scene on the full simulated canopy; each
    preview well inside the 300 ms budget (asserted < 1 s for CI; the time is printed)."""

    async def main():
        services, _wiring, data, close = await _show()
        try:
            lab = LabService(services)
            writes = []
            original = services.engine_driver.write

            async def counting(*a, **k):
                writes.append(a)
                return await original(*a, **k)

            services.engine_driver.write = counting
            files = sorted(str(p) for p in data.rglob("*"))
            events = len(services.telemetry.ring)
            timings = {}
            for sid in IDS:
                draft = edit(sid, brightness=50) if sid != "golden-hour" else edit(sid, **DONE_MEANS)
                started = time.perf_counter()
                answer = P.preview(lab, draft)
                timings[sid] = ((time.perf_counter() - started) * 1000, answer["elapsed_ms"])
            after_files = sorted(str(p) for p in data.rglob("*"))
            return writes, files, after_files, events, len(services.telemetry.ring), timings
        finally:
            await close()

    writes, files, after_files, events, after_events, timings = run_virtual(main)
    assert writes == [] and files == after_files and events == after_events
    print(
        "\npreview on the full simulated canopy (ms, wall / elapsed_ms):",
        {k: (round(a), b) for k, (a, b) in timings.items()},
    )
    for sid, (wall, reported) in timings.items():
        assert wall < 1000, (sid, wall)
        assert 0 < reported <= wall + 1


# ---- the T1 lock ---------------------------------------------------------------------------------------------------------
def _commissioned(slot: str = "custom-3", origin: str = "api") -> SimpleNamespace:
    entries = [
        DeviceEntry(
            id=m.id,
            role="canopy_module",
            account="a",
            module=m.module,
            slot_hashes={slot: "abc123"},
            slot_origin={slot: origin},
        )
        for m in MODULES
    ]
    return SimpleNamespace(canopy_modules=lambda: entries)


def test_t1_lock_refuses_design_edits_after_commissioning_on_live_only(tmp_path):
    lab = make_lab(tmp_path, simulated=False, registry=_commissioned())
    lock = P.t1_lock(lab, "golden-hour")
    assert lock["locked"] is True and lock["slot"] == "custom-3" and lock["positions"] == list(D.GRID)
    assert lock["sentence"] == f"Golden Hour: {P.T1_LOCKED}."
    for changes, field in (
        ({"palette.2.hex": NEW_PINK}, "palette"),
        ({"palette.1.name": "Apricot"}, "palette"),
        ({"layout": {"kind": "linear", "angle_deg": 30}}, "layout"),
    ):
        with pytest.raises(P.T1Locked) as exc:
            P.preview(lab, edit("golden-hour", **changes))
        assert exc.value.field == field and exc.value.detail == lock["sentence"]
    # brightness, the solid, the client copy and the stepped timing stay editable
    P.preview(
        lab,
        edit(
            "golden-hour",
            brightness=60,
            solid=0,
            name="Golden Glow",
            stepped={"direction": "forward", "steps_per_cycle": 8, "step_s": 6, "run_s": 60},
        ),
    )
    # a slot nobody commissioned, a module-level scene, an app-painted slot: no lock
    assert P.t1_lock(lab, "rainbow-row-sunset")["locked"] is False
    assert P.t1_lock(lab, "coastal-tides") == {
        "locked": False,
        "simulated": False,
        "slot": None,
        "positions": [],
        "sentence": None,
    }
    painted = make_lab(tmp_path, simulated=False, registry=_commissioned(origin="app"))
    assert P.t1_lock(painted, "golden-hour")["locked"] is False


def test_the_t1_lock_reads_the_engines_records_when_it_runs(tmp_path):
    engine = SimpleNamespace(
        recorded_slot=lambda device_id, slot: (
            {"hash": "h", "origin": "api", "effects": None} if device_id.endswith("r1c1") else None
        )
    )
    lab = make_lab(tmp_path, simulated=False, engine=engine)
    assert P.t1_lock(lab, "golden-hour")["positions"] == ["r1c1"]
    with pytest.raises(P.T1Locked):
        P.check_t1_lock(lab, edit("golden-hour", **{"palette.0.hex": "#FFFFFF"}))


def test_the_t1_lock_is_never_on_in_the_simulator(tmp_path):
    lab = make_lab(tmp_path, simulated=True, registry=_commissioned())
    lock = P.t1_lock(lab, "golden-hour")
    assert lock == {
        "locked": False,
        "simulated": True,
        "slot": "custom-3",
        "positions": [],
        "sentence": P.SIM_LOCK_SENTENCE,
    }
    P.check_t1_lock(lab, edit("golden-hour", **DONE_MEANS))  # no T1Locked
    assert P.preview(lab, edit("golden-hour", **DONE_MEANS))["t1_lock"] == lock


def test_an_unknown_scene_is_a_draft_error_not_a_crash(tmp_path):
    draft = D.SceneDraft.model_validate(
        {**D.seed_draft(TRIO, "golden-hour").model_dump(), "scene_id": "disco"}
    )
    with pytest.raises(D.DraftError) as exc:
        P.preview(make_lab(tmp_path), draft)
    assert [p.field for p in exc.value.problems] == ["scene_id"]
