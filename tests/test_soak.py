"""`python -m conductor.soak` in miniature: the same schedule shape over 20 simulated minutes, and the verdicts."""

from __future__ import annotations

from conductor import soak
from conductor.sim.settings import SimSettings


def test_short_soak_passes_and_writes_the_report(tmp_path):
    report = tmp_path / "sim-acceptance.md"
    stepped = {
        **soak.load_soak_stepped(),
        "runs": 4,
    }  # one of them cut short by a still mood (interrupt_every 4)
    cfg = soak.SoakConfig(hours=1 / 3, changes=6, seed=3, report_path=report, stepped=stepped)
    results, verdicts, meta = soak.run_soak(cfg)
    failed = [v["name"] for v in verdicts if not v["pass"] and v.get("gating", True)]
    assert not failed, failed
    text = report.read_text()
    assert "| Measure | p50 | p95 | max | Target | Result |" in text and "**PASS**" in text
    assert "config/sim/acceptance.yaml" in text and "ShowWiring" in meta["compiler"]
    assert results["commission"]["ok"] and len(results["outages"]) == 2  # the 500 window and the outage
    assert results["max_queue"] <= 18
    # out-of-band changes (the Hubspace app) found by the rolling true-up and undone within 10 s (review gap)
    assert results["drift_heals"] and max(results["drift_heals"]) <= 10 and results["drift_timeouts"] == 0
    assert results["read_load"]["true_up_interval_s"] == 5 and "GET/s on the canopy account" in text
    # the ripple scenario (D64): its own run at 1.2 s, ring by ring, one PUT per module, none early
    ripple = results["ripple"]
    assert ripple["stagger_s"] == 1.2 and ripple["changes"] >= 10 and ripple["rings_max"] == 3
    assert set(ripple["puts_per_module"]) == {1} and ripple["early"] == 0 and ripple["downgrades"] == 0
    assert len(ripple["ring_spreads"]) >= 3 * ripple["changes"] - ripple["changes"]  # rings of 1–3
    assert "Ripple, SIMULATED 1.2 s stagger: each ring's landing spread" in text
    assert "`kiosk.ripple_stagger_s` 1.2 (the show's setting stays 0" in text
    # the stepped scenario (D65 draft): its own run with a SIMULATED stepped rung, each step measured from the lights
    st = results["stepped"]
    assert st["tiers"] == {"stepped": 4} and st["runs"] == 4 and st["interrupted"] == 1
    assert st["ended"] == {"the run is over": 3, "replaced": 1} and set(st["step_s"]) == {6.0}
    assert st["steps_landed"] >= 3 * 14 and st["step_spreads"] and st["step_landings"]  # the metrics exist
    assert st["steps_superseded"] == st["steps_failed"] == st["steps_skipped"] == 0
    assert (
        set(st["puts_per_module_step"]) == {1} and st["missing"] == 0
    )  # one PUT per changed module per step
    assert st["governor_trips"] == 0 and st["downgrades"] == 0  # at the pinned 5% throttling
    assert st["max_account_program_puts_60s"] <= 90  # the runner's pace: ≤ 90 a minute on the account
    assert st["max_account_writes_60s"] >= st["max_account_program_puts_60s"]  # retries and re-sends on top
    assert len(st["holds"]) == 3 and st["holds_unsettled"] == 0
    assert st["idle_return"]["mode"] == "lowcountry-calm" and 180 <= st["idle_return"]["after_s"] <= 190
    assert "Stepped, SIMULATED: each step's landing spread" in text and "Stepped scenario (D65 draft" in text


def test_the_acceptance_conditions_are_the_pinned_ones():
    s = soak.SoakConfig().sim_settings
    assert s == SimSettings.from_acceptance()
    assert (s.ack_ms_min, s.ack_ms_max, s.throttle_429_rate, s.silent_drop_rate) == (150, 800, 0.05, 0.01)
    assert s.http_500_window_s == 43 * 60 and s.internet_outage_s == 30 * 60


def test_verdicts_fail_when_a_target_is_missed():
    results = {
        "changes": [
            {"source": "kiosk", "mode": "m", "seconds": s, "fault": None} for s in [2.0] * 18 + [9.0, 9.5]
        ],
        "max_queue": 19,
        "heals": [3.0, 12.0],
        "heal_timeouts": 0,
        "tier_changes": [{"direction": "down"}],
        "recoveries": {"outage": 75.0, "http_500": None},
        "windows_s": {"outage": 1800, "http_500": 2580},
        "min_write_gap_s": 0.8,
        "final_correct": False,
        "final_mode": "m",
    }
    verdicts = {v["name"].split(" (")[0]: v["pass"] for v in soak.evaluate(results, soak.load_targets())}
    assert not any(verdicts.values()), verdicts


def test_builtin_compiler_covers_every_mode_on_its_ladder(tmp_path):
    import shutil

    from technique_modes import PATH as TECHNIQUES

    from conductor import rows as R
    from conductor.config import CONFIG_DIR, load_registry
    from conductor.engine_api import Tier

    registry = load_registry(CONFIG_DIR, None)
    compiler = soak.BuiltinCompiler(registry)
    for mode_id in compiler.modes():
        for tier in compiler.ladder(mode_id):
            plan = compiler.compile(mode_id, tier)
            assert len(plan.modules) == 9 and plan.feature
            if tier is Tier.T1:
                assert all(mt.slot and mt.slot_value and mt.slot_hash for mt in plan.modules.values())
    goodnight = compiler.compile("goodnight-glow", Tier.STRAND)
    assert R.color_rgb(139, 139, 209) in goodnight.modules["canopy-r0c0"].rows  # periwinkle
    # the white CCT clamp, on the pre-D59 recipes (a test fixture)
    shutil.copy(TECHNIQUES, tmp_path / "modes.yaml")
    legacy = soak.BuiltinCompiler(registry, config_dir=tmp_path)
    assert "2200 K clamped to the device floor 2700 K" in legacy.compile("goodnight-glow", Tier.STRAND).notes


def _ripple(**kw) -> dict:
    base = {
        "stagger_s": 1.2,
        "changes": 2,
        "rings_max": 3,
        "ring_spreads": [0.5, 1.0, 1.5, 0.4, 0.9, 1.2],
        "settles": [4.0, 5.1],
        "puts_per_module": [1] * 18,
        "early": 0,
        "unsettled": 0,
        "downgrades": 0,
        "bursts": 1,
        "refused": 0,
        "abandoned": 0,
        "settings": soak.load_soak_ripple(),
    }
    return {**base, **kw}


def test_the_ripple_scenario_is_configured_and_its_verdicts_hold_the_targets():
    assert soak.SoakConfig().ripple == soak.load_soak_ripple()
    assert soak.load_soak_ripple()["stagger_s"] == 1.2  # the show itself stays at 0 (conductor.yaml)
    rows = soak._ripple_rows(_ripple())
    assert [r["pass"] for r in rows] == [True] * 4
    assert rows[1]["target"].startswith("p95 ≤ 7.4 s")  # (3 − 1) × 1.2 + 5
    failing = [
        _ripple(ring_spreads=[2.5] * 6),  # a ring that lands over more than 2 s
        _ripple(settles=[7.0, 7.6]),  # the last ring later than 7.4 s
        _ripple(unsettled=1),
        _ripple(puts_per_module=[1] * 17 + [2]),  # a module sent twice for one change
        _ripple(early=1),  # a module sent before its ring
        _ripple(downgrades=1),
    ]
    for index, ripple in zip((0, 1, 1, 2, 2, 3), failing, strict=True):
        rows = soak._ripple_rows(ripple)
        assert not rows[index]["pass"], ripple
        # the per-ring spread is reported for D64, never a reason the soak fails; the others are
        assert soak.passed(rows) is (index == 0), ripple
    assert [r.get("gating", True) for r in soak._ripple_rows(_ripple())] == [False, True, True, True]
    assert soak._result(soak._ripple_rows(failing[0])[0]) == "FAIL (reported)"
    results = {"ripple": _ripple(early=1)}
    assert soak._ripple_line(results["ripple"]).count("1 commands reached a module before its ring") == 1


def test_builtin_compiler_samples_each_design_like_the_looks_compiler(tmp_path):
    """D63: module level is a 2-D design sampled at each module's centre; the soak's fallback compiler sends the
    same colours as conductor.looks, and refuses (so the engine steps down) a canvas it can't sample."""
    import shutil

    import pytest
    import yaml

    from conductor.config import CONFIG_DIR, load_registry
    from conductor.engine_api import PlanError, Tier
    from conductor.looks import engine_compiler

    registry = load_registry(CONFIG_DIR, None)
    builtin, looks = soak.BuiltinCompiler(registry), engine_compiler(registry=registry)
    for mode_id in builtin.modes():
        ours, theirs = builtin.compile(mode_id, Tier.T3), looks.compile(mode_id, Tier.T3)
        assert {d: t.rows for d, t in ours.modules.items()} == {d: t.rows for d, t in theirs.modules.items()}
    for name in ("modes.yaml", "recipes.yaml"):  # no canopy.yaml: the default canopy, as CompileContext does
        shutil.copy(CONFIG_DIR / name, tmp_path / name)
    data = yaml.safe_load((tmp_path / "modes.yaml").read_text())
    data["modes"]["coastal-tides"]["techniques"]["modules"]["sample"]["at"] = "mean"
    calm = data["modes"]["lowcountry-calm"]["techniques"]["modules"]["sample"]["canvas"]
    calm.update(kind="gradient", axis="x")
    (tmp_path / "modes.yaml").write_text(yaml.safe_dump(data))
    edited = soak.BuiltinCompiler(registry, config_dir=tmp_path)
    assert any("sampled at each module's centre" in n for n in edited.compile("coastal-tides", Tier.T3).notes)
    with pytest.raises(PlanError, match="samples only `field` canvases"):
        edited.compile("lowcountry-calm", Tier.T3)


# ---- the stepped scenario (D65 draft) -------------------------------------------------------------------------------
def _stepped(**kw) -> dict:
    base = {
        "modes": ["golden-hour", "coastal-tides"],
        "settings": soak.load_soak_stepped(),
        "cap": 90.0,
        "requests": 3,
        "refused": 0,
        "runs": 3,
        "not_stepping": 0,
        "tiers": {"stepped": 3},
        "ended": {"the run is over": 3},
        "interrupted": 0,
        "step_s": [6.0, 6.0, 6.0],
        "step_spreads": [0.5, 1.0, 1.5, 1.9],
        "step_landings": [1.5, 2.0, 3.0, 4.0],
        "steps_landed": 4,
        "steps_superseded": 0,
        "steps_cut": 0,
        "steps_failed": 0,
        "steps_skipped": 0,
        "puts_per_module_step": [1] * 36,
        "missing": 0,
        "holds": [2.0, 2.5, 3.0],
        "holds_unsettled": 0,
        "max_account_writes_60s": 88,
        "max_account_program_puts_60s": 81,
        "idle_return": {"after_s": 180.3, "mode": "lowcountry-calm", "settled_s": 2.0},
        "idle_return_s": 180.0,
        "governor_trips": 0,
        "downgrades": 0,
    }
    return {**base, **kw}


def test_the_stepped_scenario_is_configured_and_its_verdicts_hold_the_targets():
    settings = soak.load_soak_stepped()
    assert soak.SoakConfig().stepped == settings
    assert settings["modes"] == ["golden-hour", "coastal-tides"]  # the lead's D65 candidates
    assert settings["technique"] == {"direction": "forward", "steps_per_cycle": 6, "step_s": 6, "run_s": 90}
    rows = soak._stepped_rows(_stepped())
    assert [r["pass"] for r in rows] == [True] * 9
    # the spread (05's target, per step) and the account's writes of every kind are reported, not gating
    assert [r.get("gating", True) for r in rows] == [False, True, True, True, False, True, True, True, True]
    failing = [
        _stepped(step_spreads=[2.5] * 4),
        _stepped(steps_superseded=1),
        _stepped(steps_failed=1),
        _stepped(steps_skipped=1),
        _stepped(step_landings=[]),
        _stepped(puts_per_module_step=[1] * 35 + [2]),  # a module sent twice for one step
        _stepped(missing=1),  # a module whose colour changed but wasn't sent
        _stepped(max_account_program_puts_60s=91),
        _stepped(max_account_writes_60s=95),
        _stepped(governor_trips=1),
        _stepped(downgrades=1),
        _stepped(holds_unsettled=1),
        _stepped(holds=[]),
        _stepped(idle_return=None),
        _stepped(idle_return={"after_s": 191.0, "mode": "lowcountry-calm", "settled_s": 2.0}),  # late
        _stepped(
            idle_return={"after_s": 120.0, "mode": "lowcountry-calm", "settled_s": 2.0}
        ),  # a run moved it
        _stepped(idle_return={"after_s": 180.3, "mode": "lowcountry-calm", "settled_s": None}),
    ]
    rows_failing = (0, 1, 1, 1, 1, 2, 2, 3, 4, 5, 6, 7, 7, 8, 8, 8, 8)
    for index, stepped in zip(rows_failing, failing, strict=True):
        rows = soak._stepped_rows(stepped)
        assert [i for i, r in enumerate(rows) if not r["pass"]] == [index], stepped
        assert soak.passed(rows) is (index in (0, 4)), stepped  # only the two reported rows never fail it
    line = soak._stepped_line(_stepped(steps_cut=2, interrupted=1))
    assert "2 cut by a newer mood" in line and "`config/sim/acceptance.yaml` is pinned" in line


def test_most_in_60s_counts_a_half_open_window():
    assert soak._most_in_60s([]) == 0
    steps = [6.0 * n for n in range(1, 16)]  # every 6 s: a window holds 10 steps, never the 11th 60 s later
    assert soak._most_in_60s(steps) == 10
    assert soak._most_in_60s([*steps, 6.0, 30.0]) == 12  # a step sent twice and a re-send inside it
    assert soak._most_in_60s([0.0, 59.9, 60.0, 119.95]) == 2


def test_the_soak_config_copy_gives_the_candidates_a_simulated_stepped_rung(tmp_path):
    """The stepped rung is the mode's own module-level design (the hold is that rung), names its flow, says
    SIMULATED, and parses under the binding schema; the show's own modes.yaml keeps none."""
    import yaml

    from conductor.config import CONFIG_DIR, load_recipes
    from conductor.geometry import load_canopy_config
    from conductor.looks import parse_modes

    config = soak._soak_config_dir(None, stepped=soak.load_soak_stepped())
    try:
        raw = yaml.safe_load((config / "modes.yaml").read_text(encoding="utf-8"))
        parsed = parse_modes(raw, recipes=load_recipes(config), canopy=load_canopy_config(), path=config)
    finally:
        import shutil

        shutil.rmtree(config.parent, ignore_errors=True)
    for mode_id, words in (("golden-hour", "from one edge"), ("coastal-tides", "diagonally")):
        mode = raw["modes"][mode_id]
        stepped, sample = mode["techniques"]["stepped"], mode["techniques"]["modules"]["sample"]
        assert mode["ladder"][0] == "stepped" and mode["ladder"].count("stepped") == 1
        assert stepped["canvas"] == sample["canvas"] and stepped["brightness"] == sample["brightness"]
        assert (
            stepped["moving_feature"].startswith("SIMULATED")
            and f"moves {words}." in stepped["moving_feature"]
        )
        assert (stepped["step_s"], stepped["run_s"], stepped["steps_per_cycle"]) == (6, 90, 6)
        assert parsed.modes[mode_id].techniques.stepped is not None
    others = [m for m, v in raw["modes"].items() if "stepped" in v["ladder"]]
    assert sorted(others) == ["coastal-tides", "golden-hour"]  # idle and Goodnight Glow stay static
    show = yaml.safe_load((CONFIG_DIR / "modes.yaml").read_text(encoding="utf-8"))
    assert not any("stepped" in m["ladder"] for m in show["modes"].values())


def test_a_module_is_correct_when_it_shows_the_step_the_run_is_at():
    """`_correct` is program-aware: a stepped plan's module is judged against the current step, the hold after."""
    from types import SimpleNamespace

    from conductor import rows as R
    from conductor.engine_api import ModePlan, ModuleTarget, Program, ProgramStep, Tier

    hold = [R.power(True), R.color_mode("color"), R.color_rgb(255, 0, 0)]
    step = [R.power(True), R.color_mode("color"), R.color_rgb(0, 0, 255)]
    program = Program(
        "stepped",
        {},
        "forward",
        6.0,
        90.0,
        [ProgramStep(0, {"m": hold}, {}), ProgramStep(1, {"m": step}, {})],
        "x",
    )
    plan = ModePlan(
        "p", "P", Tier.STEPPED, "f", False, {"m": ModuleTarget("m", hold, list(hold))}, program=program
    )
    view = {"rows": step}
    runner = SimpleNamespace(plan=plan, shown=1, rows_for=lambda d: program.steps[runner.shown].rows[d])
    world = SimpleNamespace(
        engine=SimpleNamespace(runner=runner),
        sim=SimpleNamespace(device_view=lambda address: view["rows"]),
        addresses={"m": "sim-m"},
    )
    assert soak._correct(world, plan, "m")  # it shows step 1, the step the run is at
    view["rows"] = hold
    assert not soak._correct(world, plan, "m")
    runner.shown = None  # the run is over: the hold
    runner.rows_for = lambda d: None
    assert soak._correct(world, plan, "m")
    world.engine.runner = None  # another look's runner (or none): the plan's own rows
    assert soak._correct(world, plan, "m")


def test_the_stepped_scenario_catches_steps_that_never_land_before_the_next(tmp_path):
    """The instruments can fail: with the lights taking 7–8 s to apply a write and 6 s steps (the account cap's
    pace), every step is superseded by the next before it shows, and the verdict says so."""
    from conductor.clock import run_virtual
    from conductor.sim.settings import SimSettings

    stepped = {
        **soak.load_soak_stepped(),
        "runs": 1,
        "interrupt_every": 0,
        "technique": {"direction": "forward", "steps_per_cycle": 6, "step_s": 6, "run_s": 36},
    }
    slow = SimSettings.from_acceptance().with_(
        device_apply_extra_s_min=7.0,
        device_apply_extra_s_max=8.0,
        throttle_429_rate=0.0,
        silent_drop_rate=0.0,
    )
    cfg = soak.SoakConfig(seed=1, report_path=None, stepped=stepped, sim_settings=slow)
    out = run_virtual(lambda: soak._stepped_soak(cfg))
    assert out["runs"] == 1 and set(out["step_s"]) == {6.0}
    assert out["steps_superseded"] >= 4 and out["steps_landed"] <= 1  # only the last step (the hold) can land
    landing = soak._stepped_rows(out)[1]
    assert not landing["pass"] and f"{out['steps_superseded']} superseded" in landing["name"]


def test_missing_steps_counts_a_changed_module_a_step_didnt_send():
    from types import SimpleNamespace

    from conductor import rows as R
    from conductor.engine_api import ModePlan, ModuleTarget, Program, ProgramStep, Tier

    red, blue = R.color_rgb(255, 0, 0), R.color_rgb(0, 0, 255)
    steps = [ProgramStep(0, {"a": [red], "b": [red]}, {}), ProgramStep(1, {"a": [blue], "b": [red]}, {})]
    steps.append(ProgramStep(2, {"a": [red], "b": [blue]}, {}))
    program = Program("stepped", {}, "forward", 6.0, 90.0, steps, "x")
    targets = {d: ModuleTarget(d, [red], [red]) for d in "ab"}
    plan = ModePlan("p", "P", Tier.STEPPED, "f", False, targets, program=program)
    runner = SimpleNamespace(plan=plan, program=program, cycle=3)

    def group(t: float, label: str, rows: dict) -> dict:
        return {"t": t, "plan": plan, "runner": runner, "label": label, "rows": rows}

    sent = {
        (1, "step 1 (2 of 3): p"): group(6.0, "step 1 (2 of 3): p", {"a": [blue], "b": [red]}),
        (1, "step 2 (3 of 3): p"): group(12.0, "step 2 (3 of 3): p", {"a": [red], "b": [blue]}),
    }
    assert soak._missing_steps(sent) == 0
    del sent[(1, "step 2 (3 of 3): p")]["rows"][
        "b"
    ]  # b's colour changed at step 2, but step 2 didn't send it
    sent[(1, "step 1 (2 of 3): p")]["rows"]["b"] = [red]
    assert soak._missing_steps(sent) == 1
    assert soak._step_rows(runner, "hold: p", "a") == [red] and soak._step_rows(runner, "x", "a") is None
