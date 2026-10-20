"""The Design Lab's stepped editor and ripple preview (Slice C, docs/design-lab/PLAN.md §7; D64, D65; screen only).

What these pin:
- the pace the Lab shows (`stepped.effective_step_s`, `pace.nine` / `pace.today`) equals a REAL ProgramRunner's step on
  the engine, for nine modules and for the Mac's two kits, at the designed step and at edited ones (and with a ripple);
- the preview's steps are the compiled stepped plan's steps exactly (`compile_mode(scene, Tier.STEPPED, …)`), for both
  stepped scenes and several edits, on the design registry and on the shipped registry's positions;
- every stepped bound is refused with its draft field path (`stepped.<name>`), through the route too; a run never
  outlasts `kiosk.idle_return_s`; scenes without a stepped rung (and the idle look) refuse stepped edits;
- the ripple rings are the show's own ripple bands (the plan's `waves` and the engine's `_rings`);
- the moving copy is the schema-checked one; nothing changes `motion.stepped.live_enabled` or `kiosk.ripple_stagger_s`;
- the export's D65 lines say the pace, and the step_s comment in modes.yaml follows it.

Unit tests run against the frozen Oct 16 baseline (tests/fixtures_lab_baseline/), so they hold after an export
changes config/; the route tests read config/ (the app's Lab does) and edit relative to it.
"""

from __future__ import annotations

import ast
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fake_compiler import make_world, settings_with
from pydantic import ValidationError

from conductor.clock import VirtualClock, run_virtual
from conductor.config import CONFIG_DIR, RIPPLE_MAX_WAVES, ConductorSettings, load_registry
from conductor.design_lab import draft as D
from conductor.design_lab import export as X
from conductor.design_lab import preview as P
from conductor.design_lab import stepped as ST
from conductor.design_lab.service import LabService
from conductor.engine_api import Tier
from conductor.geometry import load_canopy_config
from conductor.looks import CompileContext, compile_mode, ladder, scene_rules
from conductor.looks.compile import design_field, module_lags, program_cycle
from conductor.looks.schema import STEPPED_PACE, SteppedTechnique, _stepped_problems
from conductor.show import STEPPED_LIVE_OFF

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).resolve().parent / "fixtures_lab_baseline"
TRIO = D.load_trio(BASELINE)
CANOPY = load_canopy_config(BASELINE / "canopy.yaml")
IDS = [s.id for s in TRIO.scenes.scenes]
STEPPED = [m for m in IDS if TRIO.modes.mode(m).techniques.stepped is not None]
STILL = [m for m in IDS if m not in STEPPED]
SHIPPED = ConductorSettings()
CAPS = P.design_caps()
KITS = list(scene_rules.LIVE_KITS)  # the Mac show box's two kits (D62)
LIVE = D.load_trio(CONFIG_DIR)  # the app's Lab reads config/: route tests edit that
LIVE_STEPPED = [m for m in (s.id for s in LIVE.scenes.scenes) if LIVE.modes.mode(m).techniques.stepped]


def edit(scene_id: str, trio: D.ConfigTrio = TRIO, **changes) -> D.SceneDraft:
    return D.SceneDraft.model_validate(edited_json(scene_id, trio, **changes))


def edited_json(scene_id: str, trio: D.ConfigTrio = TRIO, **changes) -> dict:
    """The approved draft's JSON with some fields changed (`stepped.<f>` reaches inside), not validated."""
    data = D.seed_draft(trio, scene_id).model_dump(mode="json")
    for key, value in changes.items():
        parts = key.split(".")
        target = data
        for p in parts[:-1]:
            target = target[int(p)] if p.isdigit() else target[p]
        target[parts[-1]] = value
    return data


def patched_of(draft: D.SceneDraft, settings: ConductorSettings = SHIPPED) -> D.ConfigTrio:
    return D.apply_draft(TRIO, draft, canopy=CANOPY, settings=settings)


def make_lab(settings: ConductorSettings = SHIPPED, config_dir: Path = BASELINE) -> LabService:
    services = SimpleNamespace(
        config_dir=config_dir,
        data=Path(tempfile.mkdtemp()),
        clock=VirtualClock(),
        telemetry=SimpleNamespace(record=lambda *a, **k: None),
        settings=settings,
        engine_driver=SimpleNamespace(simulated=True, name="sim"),
        extras={},
        registry=load_registry(),
    )
    return LabService(services)


# Edits of both stepped scenes the Lab offers: the designed timing, each stepped field, the design they move, a colour.
EDITS = {
    "approved": {},
    "reverse": {"stepped.direction": "reverse"},
    "eight steps": {"stepped.steps_per_cycle": 8},
    "three steps": {"stepped.steps_per_cycle": 3},
    "twelve steps, slow": {"stepped.steps_per_cycle": 12, "stepped.step_s": 11.5, "stepped.run_s": 150},
    "fast short run": {"stepped.step_s": 4.0, "stepped.run_s": 30},
    "a colour": {"palette.1.hex": "#5FB8C8"},
    "the design": {"layout": {"kind": "linear", "angle_deg": 60}},
    "radial reverse": {
        "layout": {"kind": "radial", "centre_in": [112.5, 60]},
        "stepped.direction": "reverse",
    },
}


# ---- the pace is the runner's ----------------------------------------------------------------------------------------
def runner_step(
    scene_id: str, draft: D.SceneDraft, positions: list[str], settings: ConductorSettings
) -> tuple[float, int, bool]:
    """A real ProgramRunner's (step_s, count, running) for the draft's stepped rung on the engine (the world's real
    executor and SimDriver on virtual time), driving only `positions` (the rest left out, D62)."""
    patched = patched_of(draft, settings)

    async def main():
        w = make_world(settings=settings, tier_allowed=lambda m, t: (t not in (Tier.T1, Tier.T2), "test"))
        ctx = CompileContext(
            registry=w.registry,
            capabilities={e.id: CAPS for e in w.registry.canopy_modules()},
            modes=patched.modes,
            recipes=patched.recipes,
            canopy=CANOPY,
            addresses={
                e.id: (w.address(e.id) if e.module in positions else None)
                for e in w.registry.canopy_modules()
            },
        )
        w.engine.deps.compile = lambda mode_id, tier: compile_mode(mode_id, tier, ctx)
        w.engine.deps.ladder = lambda mode_id: ladder(mode_id, patched.modes)
        w.engine.deps.modes = lambda: list(patched.modes.modes)
        await w.engine.request_mode(scene_id, source="ops")
        plan, runner = w.engine.current_plan(), w.engine.runner
        assert plan.tier is Tier.STEPPED and sorted(
            m.removeprefix("canopy-") for m in plan.modules
        ) == sorted(positions)
        out = (runner.step_s, runner.count, runner.running)
        await w.close()
        return out

    return run_virtual(main)


BUDGETS = {
    "shipped": SHIPPED,  # 90 a minute less 20 %: 72 planned
    "120 less 25 %": settings_with(
        motion={"stepped": {"account_writes_per_min": 120, "budget_reserve": 0.25}}
    ),
    "60 less 25 %": settings_with(motion={"stepped": {"account_writes_per_min": 60, "budget_reserve": 0.25}}),
    # not shipped (D64 is off): the runner's ripple term, the span of the rings + min_step_s
    "a 1.2 s ripple": settings_with(kiosk={"ripple_stagger_s": 1.2}),
    # ... where it is the longest term: nine modules at 90 planned need 6 s, three rings 1.2 s apart 6.4 s
    "a 1.2 s ripple at 120 less 25 %": settings_with(
        kiosk={"ripple_stagger_s": 1.2},
        motion={"stepped": {"account_writes_per_min": 120, "budget_reserve": 0.25}},
    ),
    # 70 planned: nine modules need 7.714285… s, which the runner rounds UP to whole ms (7.715)
    "100 less 30 %": settings_with(
        motion={"stepped": {"account_writes_per_min": 100, "budget_reserve": 0.3}}
    ),
}


@pytest.mark.parametrize("budget", list(BUDGETS))
@pytest.mark.parametrize("step_s", [6.0, 4.0, 8.5, 12.0])
@pytest.mark.parametrize("modules", ["nine", "two"])
@pytest.mark.parametrize("scene_id", STEPPED)
def test_effective_step_matches_the_program_runner_for_nine_and_two(scene_id, modules, step_s, budget):
    settings = BUDGETS[budget]
    positions = list(D.GRID) if modules == "nine" else KITS
    draft = edit(scene_id, **{"stepped.step_s": step_s})
    step, count, running = runner_step(scene_id, draft, positions, settings)
    patched = patched_of(draft, settings)
    pace = ST.canopy_pace(patched, scene_id, CANOPY, settings, positions)
    assert pace["step_s"] == step  # exactly (the "100 less 30 %" budget exercises the whole-ms ceil)
    if pace["still"] is None:
        assert running and pace["hold_at"] == count
    else:  # longer than max_step_s: the runner holds the still design too (a one-step run: see the runner-gap test)
        assert not running or count == 1
    if "ripple" not in budget:
        assert ST.effective_step_s(step_s, len(positions), settings) == step
    # the preview's own numbers are these (today = the show box's two kits)
    shown = ST.stepped_preview(patched, scene_id, CANOPY, settings, KITS)
    assert shown["pace"][{"nine": "nine", "two": "today"}[modules]] == step


def test_a_ripple_lengthens_the_step_as_the_runner_does():
    rippled = BUDGETS["a 1.2 s ripple at 120 less 25 %"]
    for scene_id in STEPPED:
        draft = edit(scene_id, **{"stepped.step_s": 4.0})
        rings = len(ST.ripple_rings(patched_of(draft, rippled), scene_id, CANOPY))
        expected = 4.0 + (rings - 1) * 1.2 if rings > 1 else 6.0
        assert runner_step(scene_id, draft, list(D.GRID), rippled)[0] == max(6.0, expected)
        pace = ST.canopy_pace(patched_of(draft, rippled), scene_id, CANOPY, rippled)
        assert pace["step_s"] == max(6.0, expected)


def test_the_shipped_pace_is_seven_and_a_half_on_nine_and_six_on_the_two_kits():
    for scene_id in STEPPED:
        shown = P.preview(make_lab(), D.seed_draft(TRIO, scene_id))["stepped"]
        assert shown["design_step_s"] == 6.0
        assert shown["pace"] == {
            "nine": 7.5,
            "today": 6.0,
            "today_modules": 2,
            "today_positions": KITS,
            "planned_writes_per_min": 72.0,
            "account_writes_per_min": 90.0,
        }
        assert shown["runs"]["nine"]["hold_at"] == 12 and shown["runs"]["today"]["hold_at"] == 15


def test_a_pace_past_max_step_s_or_a_run_shorter_than_a_step_holds_still():
    tight = settings_with(motion={"stepped": {"account_writes_per_min": 40}})  # 9 × 60 / 32 = 16.875 s > 12 s
    run = ST.run_pace(6.0, 90.0, 9, tight)
    assert run["step_s"] == 16.875 and run["hold_at"] == 0 and "max_step_s" in run["still"]
    short = ST.run_pace(6.0, 7.0, 9, SHIPPED)  # a 7 s run at 7.5 s steps never moves
    assert short["hold_at"] == 0 and "shorter than one 7.5 s step" in short["still"]
    # ... and a run that fits only step 0 (the still design itself) never changes colour either
    one = ST.run_pace(6.0, 7.0, 2, SHIPPED)
    assert one["hold_at"] == 0 and "fits only the first step" in one["still"]
    assert ST.run_pace(6.0, 12.0, 2, SHIPPED)["still"] is None  # two steps: one colour change at 6 s


def test_the_lab_rounds_the_pace_up_to_whole_ms_as_the_runner_does():
    budget = BUDGETS["100 less 30 %"]
    assert ST.effective_step_s(6.0, 9, budget) == 7.715  # 9 × 60 / 70 = 7.714285… s
    assert ST.run_pace(6.0, 90.0, 9, budget)["moves_s"] == round(11 * 7.715, 3)


def test_a_one_step_run_is_still_in_the_lab_and_the_runners_gap_is_pinned():
    """A run that fits one step sends nothing but the hold: the Lab says it holds still. The runner (a show-path
    file this slice doesn't edit) still counts it as running, so the iPad would show the moving copy for one step
    while nothing moves; this pins that gap until Alon decides on the show-path fix (STATUS)."""
    draft = edit("golden-hour", **{"stepped.run_s": 7.0})
    step, count, running = runner_step("golden-hour", draft, KITS, SHIPPED)
    assert (step, count, running) == (6.0, 1, True)
    pace = ST.canopy_pace(patched_of(draft), "golden-hour", CANOPY, SHIPPED, KITS)
    assert pace["step_s"] == 6.0 and pace["hold_at"] == 0 and "fits only the first step" in pace["still"]
    shown = P.preview(make_lab(), draft)["stepped"]["runs"]
    assert shown["today"]["still"] and shown["nine"]["still"]


# ---- the preview's steps are the compiled plan's -----------------------------------------------------------------------
@pytest.mark.parametrize("name", list(EDITS))
@pytest.mark.parametrize("scene_id", STEPPED)
def test_the_preview_steps_are_compile_modes_steps(scene_id, name):
    draft = edit(scene_id, **EDITS[name])
    answer = P.preview(make_lab(), draft)
    shown = answer["stepped"]
    patched = patched_of(draft)
    design = CompileContext(
        registry=P.design_registry(),
        capabilities={d.id: CAPS for d in P.design_registry()},
        modes=patched.modes,
        recipes=patched.recipes,
        canopy=CANOPY,
    )
    plan = compile_mode(scene_id, Tier.STEPPED, design)
    assert shown["steps"] == [
        {"index": s.index, "colours": s.colours, "levels": dict(s.levels)} for s in plan.program.steps
    ]
    tech = patched.modes.mode(scene_id).techniques.stepped
    # a travel scene's program is one cycle of steps_per_cycle steps (a breath's adds its largest lag: PR D's)
    assert len(shown["steps"]) == tech.steps_per_cycle == draft.stepped.steps_per_cycle == shown["cycle"]
    assert shown["mode"] == "travel" and {v for s in shown["steps"] for v in s["levels"].values()} == {
        tech.brightness
    }
    assert (shown["direction"], shown["design_step_s"], shown["run_s"]) == (
        draft.stepped.direction,
        draft.stepped.step_s,
        draft.stepped.run_s,
    )
    # ... and the show's own registry at the same positions compiles the same colours step for step
    registry = load_registry()
    show = CompileContext(
        registry=registry,
        capabilities={e.id: CAPS for e in registry.canopy_modules()},
        modes=patched.modes,
        recipes=patched.recipes,
        canopy=CANOPY,
    )
    shipped = compile_mode(scene_id, Tier.STEPPED, show)
    assert [s["colours"] for s in shown["steps"]] == [s.colours for s in shipped.program.steps]
    # step 0 is the still design the run starts from and ends on (the hold): the preview's own module colours
    assert shown["feature"] == plan.feature
    still = {m["module"]: "#" + "".join(f"{v:02X}" for v in m["rgb"]) for m in answer["design"]["modules"]}
    assert shown["steps"][0]["colours"] == still


def test_an_edit_moves_the_steps():
    approved = P.preview(make_lab(), D.seed_draft(TRIO, "coastal-tides"))["stepped"]["steps"]
    reverse = P.preview(make_lab(), edit("coastal-tides", **EDITS["reverse"]))["stepped"]["steps"]
    assert reverse[0] == approved[0]  # the same still design ...
    assert reverse[1] != approved[1] and reverse[1]["colours"] == approved[-1]["colours"]  # ... flowing back


def test_a_breathe_preview_plays_the_compilers_steps_with_their_levels():
    """D75: a breathe draft previews the compiled program's own steps, each with the level every module is sent; the
    cycle is a breath plus its largest lag."""
    draft = edit(
        "coastal-tides", **{"stepped.mode": "breathe", "stepped.brightness_swing": 6, "stepped.lag_steps": 2}
    )
    shown = P.preview(make_lab(), draft)["stepped"]
    patched = patched_of(draft)
    design = CompileContext(
        registry=P.design_registry(),
        capabilities={d.id: CAPS for d in P.design_registry()},
        modes=patched.modes,
        recipes=patched.recipes,
        canopy=CANOPY,
    )
    plan = compile_mode("coastal-tides", Tier.STEPPED, design)
    assert shown["steps"] == [
        {"index": s.index, "colours": dict(s.colours), "levels": dict(s.levels)} for s in plan.program.steps
    ]
    tech = patched.modes.mode("coastal-tides").techniques.stepped
    lags = module_lags(tech, list(D.GRID), CANOPY)
    assert (shown["mode"], shown["shift"], shown["brightness_swing"], shown["lag_steps"], shown["seed"]) == (
        "breathe",
        0.0,
        6,
        2,
        None,
    )
    assert max(lags.values()) == 2
    assert shown["cycle"] == len(shown["steps"]) == 6 + max(lags.values()) == program_cycle(tech, lags)
    # the levels breathe around the rung's 85 (85 ± 6); without a shift every step keeps the still design's colours
    levels = [v for s in shown["steps"] for v in s["levels"].values()]
    assert (min(levels), max(levels)) == (79, 91)
    assert all(s["colours"] == shown["steps"][0]["colours"] for s in shown["steps"])
    assert shown["moving_feature"] == tech.moving_feature and "swells and softens" in tech.moving_feature
    assert shown["trial_note"] == ST.TRIAL_NEVER_STEPS  # D66 unchanged: a try-on stays still
    # the catalog's idle scene (Lowcountry Calm) has no rung, so no scene carries an idle note
    from conductor.routes.design_lab import catalog_json

    assert [s["idle_note"] for s in catalog_json(make_lab())["scenes"]] == [None] * len(IDS)
    # an idle look with a rung says when it runs: never on start-up; on an idle return only with motion.idle on
    tides_idle = settings_with(kiosk={"idle_mode": "coastal-tides"})
    notes = {s["id"]: s["idle_note"] for s in catalog_json(make_lab(tides_idle))["scenes"]}
    assert notes["coastal-tides"] == ST.IDLE_RUNS_OFF.format(label="Coastal Tides")
    assert "start-up and an idle return show it still" in notes["coastal-tides"]
    assert {k for k, v in notes.items() if v is not None} == {"coastal-tides"}
    breathing = settings_with(
        kiosk={"idle_mode": "coastal-tides"}, motion={"idle": {"breathe_run_s": 60, "every_s": 300}}
    )
    assert ST.idle_note(breathing, "coastal-tides", TRIO.modes.mode("coastal-tides")) == (
        "Coastal Tides is the idle look: start-up shows it still; an idle return lands as one 60 s breath and it "
        "breathes again every 300 s while it shows (motion.idle); a guest's choice runs it in full."
    )
    assert catalog_json(make_lab(breathing))["live"]["idle_breath"] == {
        "breathe_run_s": 60.0,
        "every_s": 300.0,
    }


def _kits_plan(patched: D.ConfigTrio, scene_id: str):
    """The show box's own compile: the rung over the two kits it drives (D62), as the engine compiles it there."""
    context = P._design_context(
        patched, CANOPY, addresses={f"design-{p}": (f"design-{p}" if p in KITS else None) for p in D.GRID}
    )
    return compile_mode(scene_id, Tier.STEPPED, context)


def test_the_show_box_pace_plays_the_show_boxs_own_program_or_says_it_holds_still():
    """B-R1-3 (D75): the show box compiles its rung over the modules it drives, so a breath's lags, cycle and rhythm
    there differ from the nine's. The Lab's `today` pace plays that program's steps (`today_steps`); when that compile
    refuses (a breath resting on both kits for a step), the preview says the show box holds still instead of playing
    the nine-module steps under the nine-module sentence."""
    from conductor.engine_api import PlanError

    draft = edit(
        "coastal-tides", **{"stepped.mode": "breathe", "stepped.brightness_swing": 6, "stepped.lag_steps": 2}
    )
    shown = P.preview(make_lab(), draft)["stepped"]
    kits = _kits_plan(patched_of(draft), "coastal-tides")
    assert shown["pace"]["today_positions"] == KITS and shown["today_error"] is None
    assert shown["today_steps"] == [
        {"index": s.index, "colours": dict(s.colours), "levels": dict(s.levels)} for s in kits.program.steps
    ]
    assert len(shown["today_steps"]) == 6 < len(shown["steps"]) == 8  # lags over the two kits, not over nine
    assert shown["today_moving_feature"] == kits.program.moving_feature
    assert shown["runs"]["today"]["still"] is None and shown["runs"]["today"]["hold_at"] > 1
    # a shimmer the nine modules can run but whose two kits would both rest for a whole step
    for seed in range(200):
        refused = edit(
            "coastal-tides",
            **{
                "stepped.mode": "shimmer",
                "stepped.brightness_swing": 6,
                "stepped.steps_per_cycle": 3,
                "stepped.lag_steps": 6,
                "stepped.seed": seed,
            },
        )
        try:
            _kits_plan(patched_of(refused), "coastal-tides")
        except PlanError as exc:
            why = str(exc)
            break
    else:
        pytest.fail("no seed in 0–199 rests both kits")
    shown = P.preview(make_lab(), refused)["stepped"]
    assert shown["error"] is None and len(shown["steps"]) > 3  # the nine modules step
    assert "would send none of them anything" in why
    assert shown["today_error"] == f"{why}." and shown["today_steps"] is None
    assert shown["today_moving_feature"] is None
    assert shown["runs"]["today"]["still"] == shown["today_error"] and shown["runs"]["today"]["hold_at"] == 0


@pytest.mark.parametrize("scene_id", STILL)
def test_scenes_without_a_stepped_rung_have_no_stepped_preview_and_refuse_stepped_edits(scene_id):
    seed = D.seed_draft(TRIO, scene_id)
    shown = P.preview(make_lab(), seed)
    assert shown["stepped"] is None
    timing = {"direction": "forward", "steps_per_cycle": 6, "step_s": 6.0, "run_s": 90.0}
    with pytest.raises(D.DraftError) as refused:
        patched_of(D.SceneDraft.model_validate({**seed.model_dump(), "stepped": timing}))
    [problem] = refused.value.problems
    assert problem.field == "stepped"
    if scene_id == SHIPPED.kiosk.idle_mode:  # D75: the idle look may carry a rung, but a draft can't add one
        assert problem.message == D.IDLE_NO_RUNG.format(label=TRIO.modes.mode(scene_id).label)
        assert (
            "has no stepped rung to edit" in problem.message and "modes.yaml change (D75)" in problem.message
        )
    else:
        assert "has no stepped rung" in problem.message


# ---- bounds -----------------------------------------------------------------------------------------------------------
REFUSED = [
    ("steps_per_cycle", 2),
    ("steps_per_cycle", 13),
    ("steps_per_cycle", 6.5),
    ("step_s", 3.5),
    ("step_s", 12.5),
    ("direction", "sideways"),
    ("run_s", 0),
    ("run_s", 181),
    ("run_s", 5.5),  # shorter than one 6 s step
    # D75: the kind of motion and a breath's bounds
    ("mode", "sparkle"),
    ("shift", 0.6),
    ("shift", -0.1),
    ("brightness_swing", 21),
    ("lag_steps", 7),
    ("seed", -1),
]
# SteppedTechnique takes any step_s in [4, 12]; the Lab's editor moves it in half seconds, so an EDIT off the
# increment is refused when the draft applies (an approved value off it is kept as it is)
OFF_INCREMENT = [("step_s", 6.25), ("step_s", 7.3)]


@pytest.mark.parametrize(("name", "value"), REFUSED)
def test_stepped_bounds_are_refused_with_their_field_path(name, value):
    with pytest.raises(ValidationError) as refused:
        edit("golden-hour", **{f"stepped.{name}": value})
    assert {".".join(str(p) for p in e["loc"]) for e in refused.value.errors()} == {f"stepped.{name}"}


@pytest.mark.parametrize(("name", "value"), OFF_INCREMENT)
def test_an_edit_off_the_half_second_increment_is_refused_with_its_field_path(name, value):
    draft = edit("golden-hour", **{f"stepped.{name}": value})  # the model keeps SteppedTechnique's bounds
    with pytest.raises(D.DraftError) as refused:
        patched_of(draft)
    [problem] = refused.value.problems
    assert problem.field == f"stepped.{name}" and "0.5 s steps" in problem.message


@pytest.mark.parametrize("step_s", [4.0, 4.5, 6.0, 7.5, 11.5, 12.0])
def test_step_s_goes_in_half_seconds_from_4_to_12(step_s):
    draft = edit("coastal-tides", **{"stepped.step_s": step_s, "stepped.run_s": 90})
    assert patched_of(draft).modes.mode("coastal-tides").techniques.stepped.step_s == step_s


@pytest.mark.parametrize(("name", "value"), REFUSED + OFF_INCREMENT)
def test_the_route_refuses_stepped_bounds_with_field_paths(app_client, name, value):
    scene_id = LIVE_STEPPED[0]
    r = app_client.post(
        "/api/lab/preview",
        json={"scene_id": scene_id, "draft": edited_json(scene_id, LIVE, **{f"stepped.{name}": value})},
    )
    assert r.status_code == 422, r.text[:300]
    assert [p["field"] for p in r.json()["problems"]] == [f"stepped.{name}"]


def test_run_s_is_bounded_by_idle_return():
    short_idle = settings_with(kiosk={"idle_return_s": 120})
    assert ST.run_s_max(short_idle) == 120.0 and ST.run_s_max(SHIPPED) == 180.0
    for scene_id in STEPPED:
        assert patched_of(edit(scene_id, **{"stepped.run_s": 120}), short_idle)  # at the bound: fine
        with pytest.raises(D.DraftError) as refused:
            patched_of(edit(scene_id, **{"stepped.run_s": 120.5}), short_idle)
        [problem] = refused.value.problems
        assert problem.field == "stepped.run_s" and "kiosk.idle_return_s (120 s)" in problem.message
        assert patched_of(edit(scene_id, **{"stepped.run_s": 180}))  # the shipped idle return is 180 s


def test_the_catalog_offers_the_editor_its_limits_and_live_lines(app_client):
    body = app_client.get("/api/lab/catalog").json()
    stepped = body["limits"]["stepped"]
    assert stepped == {
        "steps_per_cycle": [3, 12],
        "step_s": [4, 12],
        "step_s_increment": 0.5,
        "run_s_min": "step_s",
        "run_s_max": 180.0,
        "directions": ["forward", "reverse"],
        "modes": ["travel", "breathe", "shimmer"],
        "shift": [0.0, 0.5],
        "shift_increment": 0.05,
        "brightness_swing": [0, 20],
        "lag_steps": [0, 6],
    }
    assert body["limits"]["ripple"] == {"stagger_s": [0.0, 1.2], "max_rings": RIPPLE_MAX_WAVES}
    live = body["live"]
    assert live["stepped_live_enabled"] is False and live["stepped_live_sentence"] == STEPPED_LIVE_OFF
    assert live["stepped_live_note"] == "Alon sets motion.stepped.live_enabled after Gate R"
    assert live["ripple_stagger_s"] == 0 and live["ripple_note"] == ST.RIPPLE_OFF_NOTE
    assert live["effects_note"] == "Effects — not enabled: no code has passed the bench (06 #4)"
    assert live["effects_enabled"] is False
    assert live["idle_breath"] is None  # motion.idle: off as shipped (D75)
    assert {s["id"] for s in body["scenes"] if s["stepped"]} == set(LIVE_STEPPED)


def test_the_catalog_says_stepped_is_on_once_alon_sets_it():
    from conductor.routes.design_lab import catalog_json

    live = catalog_json(make_lab(settings_with(motion={"stepped": {"live_enabled": True}})))["live"]
    assert live["stepped_live_enabled"] is True
    assert live["stepped_live_note"] == ST.STEPPED_LIVE_ON
    assert live["stepped_live_sentence"] is None  # STEPPED_LIVE_OFF would be false now
    off = catalog_json(make_lab())["live"]
    assert (
        off["stepped_live_note"] == ST.STEPPED_LIVE_NOTE and off["stepped_live_sentence"] == STEPPED_LIVE_OFF
    )


# ---- an approved step_s off the Lab's half-second increment (SteppedTechnique accepts any in [4, 12]) ----------------
def _off_increment_config() -> Path:
    """The baseline with Coastal Tides at step_s 6.25 and run_s 90.25: valid for the schema and the show."""
    root = Path(tempfile.mkdtemp(prefix="lab-off-increment-"))
    cfg = root / "config"
    import shutil

    shutil.copytree(BASELINE, cfg)
    modes = cfg / "modes.yaml"
    text = modes.read_text(encoding="utf-8")
    block = text.index("coastal-tides:", text.index("\nmodes:"))
    head, tail = text[:block], text[block:]
    tail = tail.replace("        step_s: 6 ", "        step_s: 6.25 ", 1).replace(
        "run_s: 90\n", "run_s: 90.25\n", 1
    )
    modes.write_text(head + tail, encoding="utf-8")
    return cfg


def test_an_approved_step_s_off_the_increment_loads_the_whole_lab():
    from conductor.routes.design_lab import catalog_json
    from conductor.show import _check_stepped

    cfg = _off_increment_config()
    trio = D.load_trio(cfg)
    tech = trio.modes.mode("coastal-tides").techniques.stepped
    assert (tech.step_s, tech.run_s) == (6.25, 90.25)
    _check_stepped(SHIPPED, trio.modes)  # the show takes it
    seed = D.seed_draft(trio, "coastal-tides")
    assert (seed.stepped.step_s, seed.stepped.run_s) == (6.25, 90.25)
    lab = make_lab(config_dir=cfg)
    body = catalog_json(lab)  # every scene seeds: the whole Lab loads
    scene = next(s for s in body["scenes"] if s["id"] == "coastal-tides")
    assert scene["approved"]["stepped"]["step_s"] == 6.25 and scene["state"] == "approved"
    canopy = load_canopy_config(cfg / "canopy.yaml")
    # unchanged, it previews and applies (a colour edit keeps the approved timing)
    assert P.preview(lab, seed)["stepped"]["design_step_s"] == 6.25
    recoloured = D.SceneDraft.model_validate(
        {**seed.model_dump(mode="json"), "brightness": max(1, seed.brightness - 5)}
    )
    D.apply_draft(trio, recoloured, canopy=canopy, settings=SHIPPED)
    # an edit to another value off the increment is refused at its field; onto the increment is fine
    off = D.SceneDraft.model_validate(
        {**seed.model_dump(mode="json"), "stepped": {**seed.stepped.model_dump(), "step_s": 6.75}}
    )
    with pytest.raises(D.DraftError) as refused:
        D.apply_draft(trio, off, canopy=canopy, settings=SHIPPED)
    assert [p.field for p in refused.value.problems] == ["stepped.step_s"]
    on = D.SceneDraft.model_validate(
        {**seed.model_dump(mode="json"), "stepped": {**seed.stepped.model_dump(), "step_s": 6.5}}
    )
    assert (
        D.apply_draft(trio, on, canopy=canopy, settings=SHIPPED)
        .modes.mode("coastal-tides")
        .techniques.stepped.step_s
        == 6.5
    )


def test_the_route_previews_a_stepped_edit(app_client):
    scene_id = LIVE_STEPPED[0]
    draft = edited_json(scene_id, LIVE, **{"stepped.direction": "reverse", "stepped.steps_per_cycle": 8})
    r = app_client.post("/api/lab/preview", json={"scene_id": scene_id, "draft": draft})
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    # a travel program is one cycle of steps_per_cycle steps; a breath's adds its largest lag (D75)
    tech = LIVE.modes.mode(scene_id).techniques.stepped.model_copy(update={"steps_per_cycle": 8})
    cycle = program_cycle(tech, module_lags(tech, list(D.GRID), CANOPY))
    assert len(body["stepped"]["steps"]) == cycle == body["stepped"]["cycle"]
    assert body["stepped"]["steps_per_cycle"] == 8 and body["stepped"]["direction"] == "reverse"
    assert body["stepped"]["live_sentence"] == STEPPED_LIVE_OFF
    assert body["copy"]["stepped"]["chosen"] == "generated"
    assert body["stepped"]["moving_feature"] == body["copy"]["stepped"]["sentence"]


# ---- copy -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", list(EDITS))
@pytest.mark.parametrize("scene_id", STEPPED)
def test_the_moving_copy_is_the_schema_checked_one(scene_id, name):
    draft = edit(scene_id, **EDITS[name])
    lab = make_lab()
    answer = P.preview(lab, draft)
    shown = answer["stepped"]
    patched = patched_of(draft)
    tech = patched.modes.mode(scene_id).techniques.stepped
    # the full canopy's moving copy is the mode's own: approved, or STEPPED_TEMPLATE when the design or flow moved
    assert shown["moving_feature"] == tech.moving_feature == answer["copy"]["stepped"]["sentence"]
    tier = next(t for t in answer["tiers"] if t["tier"] == "stepped")
    assert tier["moving_feature"] == shown["moving_feature"]
    SteppedTechnique.model_validate(tech.model_dump())  # its own honesty checks
    assert _stepped_problems(scene_id, tech, CANOPY) == []  # flow words, layout words (schema cross-check)
    assert STEPPED_PACE in shown["moving_feature"].lower()
    seed = D.seed_draft(TRIO, scene_id)
    generated = name in ("reverse", "the design", "radial reverse")
    assert answer["copy"]["stepped"]["chosen"] == ("generated" if generated else "approved")
    if generated:
        assert shown["moving_feature"].startswith(D.STEPPED_TEMPLATE.split("{flow}")[0])
    else:
        assert shown["moving_feature"] == TRIO.modes.mode(seed.scene_id).techniques.stepped.moving_feature
    # on the show box's two kits the moving copy counts its strands and claims no layout (D62)
    assert shown["today_moving_feature"].startswith(
        "Colour and dimming through the Hubspace cloud: both Hubspace strands"
    )


# ---- the ripple -------------------------------------------------------------------------------------------------------
RIPPLE_EDITS = {
    "approved": {},
    "linear 60": {"layout": {"kind": "linear", "angle_deg": 60}},
    "radial centre": {"layout": {"kind": "radial", "centre_in": [112.5, 60]}},
    "radial inverted": {"layout": {"kind": "radial", "centre_in": [112.5, 60], "invert": True}},
    "columns": {"layout": {"kind": "linear", "angle_deg": 0}},
}


def _positions(rings, devices) -> list[list[str]]:
    module = {d.id: str(d.module) for d in devices}
    return [[module[d] for d in ring] for ring in rings]


@pytest.mark.parametrize("name", list(RIPPLE_EDITS))
@pytest.mark.parametrize("scene_id", IDS)
def test_ripple_rings_are_the_shows_ripple_bands(scene_id, name):
    try:
        draft = edit(scene_id, **RIPPLE_EDITS[name])
        patched = patched_of(draft)
    except D.DraftError:
        pytest.skip(f"{scene_id} can't take the {name} layout (the validator says why)")
    rings = ST.ripple_rings(patched, scene_id, CANOPY)
    assert rings is not None and 1 <= len(rings) <= RIPPLE_MAX_WAVES
    assert sorted(p for ring in rings for p in ring) == sorted(D.GRID)
    registry = load_registry()
    ctx = CompileContext(
        registry=registry,
        capabilities={e.id: CAPS for e in registry.canopy_modules()},
        modes=patched.modes,
        recipes=patched.recipes,
        canopy=CANOPY,
    )
    for tier in ladder(scene_id, patched.modes):
        if tier is Tier.T2:
            continue  # T2 compiles only with a hand-painted hash recorded
        plan = compile_mode(scene_id, tier, ctx)
        assert plan.waves is not None, tier
        assert _positions(plan.waves, ctx.devices) == rings, (
            tier
        )  # every tier lands in the design's own order
    assert P.preview(make_lab(), draft)["rings"] == rings


def test_ripple_rings_are_what_the_engine_would_send_ring_by_ring():
    settings = settings_with(kiosk={"ripple_stagger_s": 1.2})
    for scene_id in IDS:
        patched = patched_of(D.seed_draft(TRIO, scene_id), settings)

        async def main(scene_id=scene_id, patched=patched):
            w = make_world(
                settings=settings, tier_allowed=lambda m, t: (t is Tier.T3 or t is Tier.STRAND, "")
            )
            ctx = CompileContext(
                registry=w.registry,
                capabilities={e.id: CAPS for e in w.registry.canopy_modules()},
                modes=patched.modes,
                recipes=patched.recipes,
                canopy=CANOPY,
            )
            plan = compile_mode(scene_id, Tier.T3, ctx)
            rings = w.engine._rings(plan)
            await w.close()
            return _positions(rings, ctx.devices) if rings else None

        assert run_virtual(main) == ST.ripple_rings(patched, scene_id, CANOPY), scene_id


def test_the_ripple_preview_says_the_canopys_ripple_truly():
    seed = D.seed_draft(TRIO, "golden-hour")
    off = P.preview(make_lab(), seed)["ripple"]
    assert off["note"] == (
        "Screen preview only — the canopy's ripple is off (kiosk.ripple_stagger_s: 0) until Alon signs off (D64)"
    )
    assert off["stagger_s"] == [0.0, 1.2] and off["max_rings"] == 3 and off["live_stagger_s"] == 0.0
    on = P.preview(make_lab(settings_with(kiosk={"ripple_stagger_s": 0.8})), seed)["ripple"]
    assert on["live_stagger_s"] == 0.8 and "set to 0.8 s" in on["note"] and "off" not in on["note"]
    assert ST.RIPPLE_STAGGER_MAX_S == 1.2
    spec = design_field(TRIO.modes.mode("golden-hour"), Tier.T3)
    assert spec is not None and off["rings"] == P.preview(make_lab(), seed)["rings"]


# ---- the live gates stay where Alon put them ----------------------------------------------------------------------------
def test_the_preview_says_the_live_gate_as_it_stands():
    seed = D.seed_draft(TRIO, "coastal-tides")
    off = P.preview(make_lab(), seed)["stepped"]
    assert off["live_enabled"] is False and off["live_sentence"] == STEPPED_LIVE_OFF
    assert off["live_note"] == "Alon sets motion.stepped.live_enabled after Gate R"
    on = P.preview(make_lab(settings_with(motion={"stepped": {"live_enabled": True}})), seed)["stepped"]
    assert (
        on["live_enabled"] is True and on["live_sentence"] is None and on["live_note"] == ST.STEPPED_LIVE_ON
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_lab_never_flips_live_enabled_or_ripple(app_client):
    conductor_yaml = CONFIG_DIR / "conductor.yaml"
    before = _sha(conductor_yaml)
    settings = app_client.app.state.services.settings
    dumped = settings.model_dump()
    for scene_id in LIVE_STEPPED:
        for changes in ({}, {"stepped.direction": "reverse"}, {"stepped.step_s": 10.0, "stepped.run_s": 120}):
            draft = edited_json(scene_id, LIVE, **changes)
            assert (
                app_client.post("/api/lab/preview", json={"scene_id": scene_id, "draft": draft}).status_code
                == 200
            )
        saved = app_client.post(
            "/api/lab/versions",
            json={
                "drafts": {scene_id: edited_json(scene_id, LIVE, **{"stepped.step_s": 10.0})},
                "note": "steps",
            },
        )
        assert saved.status_code == 201, saved.text[:300]
        assert app_client.get(f"/api/lab/export?version={saved.json()['id']}&format=json").status_code == 200
    assert app_client.get("/api/lab/catalog").status_code == 200
    assert settings.model_dump() == dumped
    assert settings.motion.stepped.live_enabled is False and settings.kiosk.ripple_stagger_s == 0
    assert _sha(conductor_yaml) == before
    # no draft field can carry either setting
    for model in (D.SceneDraft, D.DraftStepped):
        assert not {"live_enabled", "ripple_stagger_s"} & set(model.model_fields)


def test_no_lab_source_assigns_either_live_setting():
    """Beyond the routes: no Lab module stores into `live_enabled`, `ripple_stagger_s` or `hidden_scenes` (an
    attribute, a subscript or a model_copy/update key), whatever the receiver is called. The Lab never hides a scene
    on the iPad; /ops does (D76)."""
    forbidden = ("live_enabled", "ripple_stagger_s", "hidden_scenes")
    sources = sorted((ROOT / "conductor" / "design_lab").glob("*.py")) + [
        ROOT / "conductor/routes/design_lab.py"
    ]
    assert ROOT / "conductor/design_lab/stepped.py" in sources
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign | ast.AugAssign | ast.AnnAssign):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                name = None
                if isinstance(t, ast.Attribute):
                    name = t.attr
                elif isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant):
                    name = t.slice.value
                assert name not in forbidden, f"{path.name}:{node.lineno}"
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "setattr":
                names = {a.value for a in node.args if isinstance(a, ast.Constant)}
                assert not names & set(forbidden), f"{path.name}:{node.lineno}"
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("model_copy", "update"):
                for kw in node.keywords:
                    assert kw.arg not in forbidden, f"{path.name}:{node.lineno}"
                    if isinstance(kw.value, ast.Dict):
                        keys = {k.value for k in kw.value.keys if isinstance(k, ast.Constant)}
                        assert not keys & set(forbidden), f"{path.name}:{node.lineno}"


# ---- the export (D65) -----------------------------------------------------------------------------------------------------
def _export(scene_id: str, **changes) -> X.LabExport:
    from test_lab_export import ROOT as EXPORT_ROOT

    patched = patched_of(edit(scene_id, **changes))
    return X.export_patch(
        TRIO, patched, root=EXPORT_ROOT, canopy=CANOPY, version=None, now=1792270800.0, settings=SHIPPED
    )


def test_an_exported_stepped_timing_says_its_pace_and_keeps_the_comment_true():
    result = _export("golden-hour", **{"stepped.step_s": 10.0, "stepped.run_s": 60})
    line = (
        "Golden Hour: stepped run step_s 6.0 → 10.0, run_s 90.0 → 60.0 (nine strands on one account step every "
        "10 s (6 steps, then the still design holds))"
    )
    assert result.problem == "" and result.stepped_changes == [line]
    assert result.layout_changes == [] and result.approved_changes == []
    section = next(x for x in result.decision.splitlines() if x.startswith("Stepped timing (D65"))
    assert line in section and "live off until Gate R" in section
    added = [x for x in result.patch.splitlines() if x.startswith("+") and "step_s:" in x]
    assert added == [
        (
            "+        step_s: 10               # design pace; nine strands on one account step every 10 s "
            "(motion.stepped budget)"
        )
    ]
    # a faster design than the budget allows keeps nine strands at the budget's 7.5 s
    quick = _export("coastal-tides", **{"stepped.step_s": 5.0})
    assert quick.stepped_changes == [
        (
            "Coastal Tides: stepped run step_s 6.0 → 5.0 (nine strands on one account step every 7.5 s (12 steps, "
            "then the still design holds))"
        )
    ]
    assert "step every 7.5 s (motion.stepped budget)" in next(
        x for x in quick.patch.splitlines() if x.startswith("+") and "step_s:" in x
    )


def test_a_direction_only_change_has_no_pace_clause():
    result = _export("golden-hour", **{"stepped.direction": "reverse"})
    assert result.stepped_changes == ["Golden Hour: stepped run direction forward → reverse"]


# ---- the export's pace follows the Conductor's settings, and the comment says what the D65 line says ----------------
def _export_with(scene_id: str, settings: ConductorSettings, **changes) -> X.LabExport:
    from test_lab_export import ROOT as EXPORT_ROOT

    patched = patched_of(edit(scene_id, **changes), settings)
    return X.export_patch(
        TRIO, patched, root=EXPORT_ROOT, canopy=CANOPY, version=None, now=1792270800.0, settings=settings
    )


def _step_comment(result: X.LabExport) -> str:
    [line] = [x for x in result.patch.splitlines() if x.startswith("+") and "step_s:" in x]
    return line


FAST_BUDGET = settings_with(motion={"stepped": {"account_writes_per_min": 120, "budget_reserve": 0.25}})


def test_an_overlay_budget_reaches_both_the_d65_line_and_the_comment():
    result = _export_with("coastal-tides", FAST_BUDGET, **{"stepped.step_s": 5.0})
    assert result.problem == ""
    assert result.stepped_changes == [
        (
            "Coastal Tides: stepped run step_s 6.0 → 5.0 (nine strands on one account step every 6 s (15 steps, "
            "then the still design holds))"
        )
    ]
    assert "step every 6 s (motion.stepped budget)" in _step_comment(result)


def test_with_a_ripple_the_comment_counts_its_rings_as_the_d65_line_does():
    rippled = settings_with(
        kiosk={"ripple_stagger_s": 1.2},
        motion={"stepped": {"account_writes_per_min": 120, "budget_reserve": 0.25}},
    )
    result = _export_with("coastal-tides", rippled, **{"stepped.step_s": 4.0})
    assert result.problem == ""
    [line] = result.stepped_changes
    pace = ST.canopy_pace(
        patched_of(edit("coastal-tides", **{"stepped.step_s": 4.0}), rippled),
        "coastal-tides",
        CANOPY,
        rippled,
    )
    assert pace["step_s"] > 6.0  # the ripple's span is the longest term here
    assert f"step every {pace['step_s']:g} s (" in line
    assert f"step every {pace['step_s']:g} s (motion.stepped budget)" in _step_comment(result)


def test_a_budget_past_max_step_s_says_the_still_design_holds_in_the_comment_too():
    tight = settings_with(motion={"stepped": {"account_writes_per_min": 40}})
    result = _export_with("coastal-tides", tight, **{"stepped.step_s": 4.0})
    assert result.problem == ""
    [line] = result.stepped_changes
    assert "hold the still design" in line and "16.875 s steps" in line
    comment = _step_comment(result)
    assert "hold the still design" in comment and "step every" not in comment


def test_a_run_shorter_than_a_nine_module_step_says_still_in_the_line_and_the_comment():
    result = _export_with("golden-hour", SHIPPED, **{"stepped.step_s": 4.0, "stepped.run_s": 5.0})
    assert result.problem == ""
    [line] = result.stepped_changes
    assert line == (
        "Golden Hour: stepped run step_s 6.0 → 4.0, run_s 90.0 → 5.0 (nine strands on one account would hold the "
        "still design: its run (5 s) is shorter than one 7.5 s step)"
    )
    assert "0 steps" not in line and line.count("instead") == 0
    comment = _step_comment(result)
    assert "hold the still design" in comment and "step every 7.5 s" not in comment


def test_a_run_length_edit_alone_rewrites_the_step_s_comment():
    result = _export_with("golden-hour", SHIPPED, **{"stepped.run_s": 7.0})
    assert result.problem == ""
    comment = _step_comment(result)  # step_s itself is unchanged (6), but its pace comment is no longer true
    assert comment.startswith("+        step_s: 6 ") and "hold the still design" in comment


def test_a_colour_only_edit_leaves_the_step_s_comment_alone():
    result = _export_with("coastal-tides", SHIPPED, **{"palette.1.hex": "#5FB8C8"})
    assert result.problem == "" and not [
        x for x in result.patch.splitlines() if x.startswith("+") and "step_s:" in x
    ]


def test_the_export_route_passes_the_conductors_settings(app_client, monkeypatch):
    seen = {}
    real = X.export_patch

    def spy(*a, **k):
        seen["settings"] = k.get("settings")
        return real(*a, **k)

    monkeypatch.setattr(X, "export_patch", spy)
    scene_id = LIVE_STEPPED[0]
    saved = app_client.post(
        "/api/lab/versions",
        json={"drafts": {scene_id: edited_json(scene_id, LIVE, **{"stepped.step_s": 10.0})}, "note": "pace"},
    )
    assert saved.status_code == 201, saved.text[:300]
    r = app_client.get(f"/api/lab/export?version={saved.json()['id']}&format=json")
    assert r.status_code == 200, r.text[:300]
    assert seen["settings"] is app_client.app.state.services.settings
    assert "step every 10 s" in " ".join(r.json()["stepped_changes"])


def test_the_d65_section_says_where_the_live_gate_stands():
    off = _export_with("golden-hour", SHIPPED, **{"stepped.step_s": 10.0})
    assert off.stepped_live_clause == X.STEPPED_SECTION_OFF == "live off until Gate R"
    assert "Stepped timing (D65; live off until Gate R): " in off.decision
    on_settings = settings_with(motion={"stepped": {"live_enabled": True}})
    on = _export_with("golden-hour", on_settings, **{"stepped.step_s": 10.0})
    assert on.problem == "" and on.stepped_live_clause == X.STEPPED_SECTION_ON
    section = next(x for x in on.decision.splitlines() if x.startswith("Stepped timing (D65"))
    assert section.startswith("Stepped timing (D65; live on (motion.stepped.live_enabled)): ")
    assert "Gate R" not in section


# ---- a scene whose stepped rung sits under painted rungs says when it steps ---------------------------------------------
def test_a_stepped_rung_under_higher_rungs_says_it_runs_when_a_guest_chooses_it_then_holds_on_its_best_still_rung():
    golden = P.preview(make_lab(), D.seed_draft(TRIO, "golden-hour"))["stepped"]
    assert golden["steps_when"] == (
        "When a guest chooses Golden Hour on the iPad it moves for its run, then holds on its best still rung "
        "(Painted (all 270 lights), Painted in the app where available, else module level); chosen from /ops it "
        "shows the first of its rungs that can run, and a re-pick keeps a run going or holds its best still rung"
    )
    tides = P.preview(make_lab(), D.seed_draft(TRIO, "coastal-tides"))["stepped"]
    assert TRIO.modes.mode("coastal-tides").ladder[0] == "stepped" and tides["steps_when"] is None
