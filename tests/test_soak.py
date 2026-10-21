"""`python -m conductor.soak` in miniature: the same schedule shape over 20 simulated minutes, and the verdicts."""

from __future__ import annotations

from conductor import soak
from conductor.config import CONFIG_DIR
from conductor.looks import load_modes
from conductor.sim.settings import SimSettings


def test_short_soak_passes_and_writes_the_report(tmp_path):
    report = tmp_path / "sim-acceptance.md"
    # Golden Hour, Coastal Tides, Lowcountry Calm, then Garden Gathering cut short by another mood (interrupt_every 4),
    # which runs its own rung too (every mood steps since D77)
    stepped = {**soak.load_soak_stepped(), "runs": 4}
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
    # the ripple scenario (D64): its own run at 1.2 s, ring by ring, one PUT per module, none early. Every mood has a
    # stepped rung since D77, and the ripple's config copy strips them (the live path until Gate R), so the moods land
    # still, ring by ring (only the SIMULATED on-device motion plan goes out as one burst)
    ripple = results["ripple"]
    assert ripple["stagger_s"] == 1.2 and ripple["changes"] >= 10 and ripple["rings_max"] == 3
    assert set(ripple["puts_per_module"]) == {1} and ripple["early"] == 0 and ripple["downgrades"] == 0
    assert len(ripple["ring_spreads"]) >= 3 * ripple["changes"] - ripple["changes"]  # rings of 1–3
    assert "Ripple, SIMULATED 1.2 s stagger: each ring's landing spread" in text
    assert "`kiosk.ripple_stagger_s` 1.2 (the show's setting stays 0" in text
    assert "and every stepped rung stripped from its config copy (each mood lands still" in text
    # the stepped scenario (D65, D75, D77): every candidate steps on its own rung, in place; each step measured from the
    # lights, each run's end by the tier it settled on
    st = results["stepped"]
    # every rung exactly as config/modes.yaml has it (no approved value pinned here: make lab-roundtrip)
    keys = ("steps_per_cycle", "step_s", "run_s", "mode", "brightness_swing", "lag_steps")
    own = {m: load_modes(CONFIG_DIR).mode(m).techniques.stepped for m in st["modes"]}
    assert st["techniques"] == {m: {"own": True, **{k: getattr(t, k) for k in keys}} for m, t in own.items()}
    assert st["tiers"] == {"stepped": 4} and st["runs"] == 4 and st["interrupted"] == 1
    # each design's step, stretched to 7.5 s where shorter: 9 modules at 72 a minute (the cap of 90 less the 20 %
    # reserve)
    assert st["ended"] == {"the run is over": 3, "replaced": 1}
    assert set(st["step_s"]) == {max(own[m].step_s, 9 * 60 / 72) for m in st["modes"][:4]}
    assert st["steps_landed"] >= 3 * 11 and st["step_spreads"] and st["step_landings"]  # the metrics exist
    # B-R1-2: none superseded by the runner's own pacing. One a module missed because a pinned fault hit its write
    # (429 on every attempt, or a silent drop; the next step carries it) is the faults' luck, whose random path moves
    # with any config change (a Lab export): bounded by those faulted writes, not pinned at 0
    assert st["steps_superseded"] == st["steps_failed"] == st["steps_skipped"] == 0
    assert st["steps_superseded_faults"] <= st["step_writes_faulted"]
    assert (
        set(st["puts_per_module_step"]) == {1} and st["missing"] == 0
    )  # one PUT per changed module per step
    assert st["governor_trips"] == 0 and st["downgrades"] == 0  # at the pinned 5% throttling
    # the runner's pace (module level 72 a minute; a per-light run its own cap less the reserve, D81: 270 since Oct 21),
    # and part of a ninth step's burst
    from conductor.config import load_settings

    motion = load_settings().motion
    pace = max(72, motion.lights.account_writes_per_min * (1 - motion.stepped.budget_reserve))
    assert st["max_account_program_puts_60s"] <= pace + 9
    assert (
        st["max_account_program_puts_60s"]
        <= st["max_account_writes_60s"]
        <= max(90, motion.lights.account_writes_per_min)
    )  # retries, re-sends: the cap in force (a per-light run's own, D81)
    assert len(st["holds"]) == 3 and st["holds_unsettled"] == 0
    # Golden Hour's run settles onto its commissioned canvas; Coastal Tides and the idle look hold their own rung's
    assert st["settled"] == {"canvas": 1, "stepped": 2}
    idle = st["idle_return"]
    assert idle["mode"] == "lowcountry-calm" and 180 <= idle["after_s"] <= 190
    # the idle return installs the idle look still (motion.idle off), though it has a rung of its own (D77): no run,
    # no runner, its module-level design
    assert idle["ran"] is False and idle["runner"] is False and idle["tier"] == "modules"
    assert (
        "Stepped, SIMULATED: each step's landing spread" in text
        and "Stepped scenario (D65, D75, D77, SIMULATED)" in text
    )
    assert (
        "cut short by another mood chosen mid-run (which starts its own run, D77; never the idle look)"
        in text
    )
    assert "the idle return showed the idle look still at modules." in text
    for mode_id, technique in st["techniques"].items():
        assert soak._technique_words(mode_id, technique) in text
    # the main run steps Coastal Tides (its stepped rung is first, D65) and every pinned row above still holds
    assert results["wiring"]["tiers"].get("stepped", 0) > 0


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
    from conductor.scenes import load_scenes

    solid = load_scenes().get("goodnight-glow").solid  # periwinkle (139, 139, 209): the scene's solid colour
    assert R.color_rgb(*solid.rgb) in goodnight.modules["canopy-r0c0"].rows
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


# ---- the stepped scenario (D65) -------------------------------------------------------------------------------------
def _stepped(**kw) -> dict:
    base = {
        "modes": ["golden-hour", "coastal-tides"],
        "techniques": {
            "golden-hour": {"own": True, "steps_per_cycle": 6, "step_s": 6.0, "run_s": 90.0},
            "coastal-tides": {"own": False, "steps_per_cycle": 6, "step_s": 6.0, "run_s": 90.0},
        },
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
        "steps_superseded_faults": 0,
        "step_writes_faulted": 0,
        "steps_cut": 0,
        "steps_failed": 0,
        "steps_skipped": 0,
        "puts_per_module_step": [1] * 36,
        "missing": 0,
        "holds": [2.0, 2.5, 3.0],
        "holds_unsettled": 0,
        "settled": {"canvas": 1, "stepped": 2},
        "max_account_writes_60s": 88,
        "max_account_program_puts_60s": 81,
        "idle_return": {"after_s": 180.3, "mode": "lowcountry-calm", "settled_s": 2.0, "ran": False},
        "idle_return_s": 180.0,
        "governor_trips": 0,
        "downgrades": 0,
    }
    return {**base, **kw}


def test_the_stepped_scenario_is_configured_and_its_verdicts_hold_the_targets():
    settings = soak.load_soak_stepped()
    assert soak.SoakConfig().stepped == settings
    # every kind of motion on its own rung (D77): travel, a colour breath, a shimmer (Garden Gathering #3, in Rainbow
    # Row Sunset's place) and a level-only breath; Golden Hour first, the idle look never last
    assert settings["modes"] == [
        "golden-hour",
        "coastal-tides",
        "lowcountry-calm",
        "garden-gathering",
        "garden-gathering-3",
        "goodnight-glow",
    ]
    # a SIMULATED rung's cadence, for a candidate without one of its own; no overrides (every scene has a rung, D77)
    assert settings["technique"] == {"direction": "forward", "steps_per_cycle": 6, "step_s": 6, "run_s": 90}
    assert settings["simulated"] == {}
    show = load_modes(CONFIG_DIR)
    assert all(show.mode(m).techniques.stepped is not None for m in settings["modes"])
    # the module's defaults mirror config/sim/soak.yaml
    assert {k: settings[k] for k in soak.STEPPED_DEFAULTS} == soak.STEPPED_DEFAULTS
    rows = soak._stepped_rows(_stepped())
    assert [r["pass"] for r in rows] == [True] * 9
    # B-R1-2: a step a module missed because a pinned fault hit its write (429 on every attempt, or a silent drop) is
    # the faults' luck (the next step carries that module, by design): counted apart, at most once per such write
    lucky = soak._stepped_rows(_stepped(steps_superseded_faults=2, step_writes_faulted=3))
    assert [r["pass"] for r in lucky] == [True] * 9
    assert (
        "0 superseded, 2 carried by the next step after a pinned fault hit a module's write"
        in lucky[1]["name"]
    )
    # the spread (05's target, per step) is reported, not gating; the account's writes of every kind gate (the cap)
    assert [r.get("gating", True) for r in rows] == [False, True, True, True, True, True, True, True, True]
    failing = [
        _stepped(step_spreads=[2.5] * 4),
        _stepped(steps_superseded=1),
        _stepped(steps_superseded_faults=2, step_writes_faulted=1),  # more than the faulted writes explain
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
        # D75: the idle return started a run (motion.idle is off: it shows the idle look still)
        _stepped(idle_return={"after_s": 180.3, "mode": "lowcountry-calm", "settled_s": 2.0, "ran": True}),
        # D77: or installed its stepped rung at all (a runner, even one that never moved)
        _stepped(
            idle_return={
                "after_s": 180.3,
                "mode": "lowcountry-calm",
                "settled_s": 2.0,
                "ran": False,
                "runner": True,
            }
        ),
    ]
    rows_failing = (0, 1, 1, 1, 1, 1, 2, 2, 3, 4, 5, 6, 7, 7, 8, 8, 8, 8, 8, 8)
    for index, stepped in zip(rows_failing, failing, strict=True):
        rows = soak._stepped_rows(stepped)
        assert [i for i, r in enumerate(rows) if not r["pass"]] == [index], stepped
        assert soak.passed(rows) is (index == 0), stepped  # only the reported spread never fails it
    line = soak._stepped_line(
        _stepped(steps_cut=2, interrupted=1, steps_superseded_faults=1, step_writes_faulted=2)
    )
    assert "2 cut by a newer mood" in line and "`config/sim/acceptance.yaml` is pinned" in line
    assert (
        "1 cut short by another mood chosen mid-run (which starts its own run, D77; never the idle look)"
        in line
    )
    assert (
        "1 carried by the next step after a pinned fault hit a module's write (429 on every attempt, or a "
        "silent drop; of 2 step writes hit that way)" in line
    )
    # D65's travel wording in full, from the fixture's techniques (not config: make lab-roundtrip moves config)
    assert "golden-hour: its own (config/modes.yaml), 6 steps a cycle, 6 s steps designed, 90 s runs" in line
    assert "coastal-tides: a SIMULATED one, 6 steps a cycle, 6 s steps designed, 90 s runs" in line
    # D75's breath wording in full: its kind, swing, lag and timing
    timing = {"steps_per_cycle": 6, "step_s": 6.0, "run_s": 90.0}
    breaths = {
        "garden-gathering": {
            "own": False,
            **timing,
            "mode": "breathe",
            "brightness_swing": 6,
            "lag_steps": 2,
        },
        "golden-hour": {"own": True, **timing, "mode": "shimmer", "brightness_swing": 4, "lag_steps": 3},
    }
    breaths["golden-hour"].update(steps_per_cycle=4, step_s=8.0, run_s=60.0)
    line = soak._stepped_line(_stepped(techniques=breaths))
    assert (
        "garden-gathering: a SIMULATED breathe (swing 6, lag 2), 6 steps a breath, 6 s steps designed, 90 s runs; "
        "golden-hour: its own shimmer (config/modes.yaml) (swing 4, lag 3), 4 steps a breath, 8 s steps designed, "
        "60 s runs; `motion.stepped.live_enabled` stays false" in line
    )


def test_most_in_60s_counts_a_half_open_window():
    assert soak._most_in_60s([]) == 0
    steps = [6.0 * n for n in range(1, 16)]  # every 6 s: a window holds 10 steps, never the 11th 60 s later
    assert soak._most_in_60s(steps) == 10
    assert soak._most_in_60s([*steps, 6.0, 30.0]) == 12  # a step sent twice and a re-send inside it
    assert soak._most_in_60s([0.0, 59.9, 60.0, 119.95]) == 2


def _parse_copy(config) -> tuple[dict, object]:
    """The config copy's modes.yaml, raw and parsed (the show's own loader rules: recipes, the canopy with its fins)."""
    import yaml

    from conductor.config import load_recipes
    from conductor.geometry import load_canopy_config
    from conductor.looks import parse_modes

    raw = yaml.safe_load((config / "modes.yaml").read_text(encoding="utf-8"))
    canopy = load_canopy_config(config / "canopy.yaml")
    return raw, parse_modes(raw, recipes=load_recipes(config), canopy=canopy, path=config)


def test_the_soak_config_copy_keeps_every_candidates_own_rung_in_place():
    """D75/D77: every candidate brings its own stepped rung (design, cadence and copy as the show has them) and keeps it
    where the show has it (a guest's choice runs it first anyway); nothing SIMULATED is added, and the copy's modes.yaml
    is the show's own."""
    import shutil

    import yaml

    from conductor.config import ConductorSettings
    from conductor.show import _check_stepped

    settings = soak.load_soak_stepped()
    config = soak._soak_config_dir(None, stepped=settings)
    try:
        raw, parsed = _parse_copy(config)
    finally:
        shutil.rmtree(config.parent, ignore_errors=True)
    show = yaml.safe_load((CONFIG_DIR / "modes.yaml").read_text(encoding="utf-8"))
    assert raw == show  # every rung where config/modes.yaml puts it, none added
    for mode_id in settings["modes"]:
        mode = raw["modes"][mode_id]
        assert "stepped" in mode["ladder"]
        assert not mode["techniques"]["stepped"]["moving_feature"].startswith("SIMULATED")
    assert all(parsed.modes[m].moves and not parsed.modes[m].motion for m in raw["modes"])
    _check_stepped(ConductorSettings(), parsed)  # every run ends before the idle return; motion.idle is off


def test_the_ripple_config_copy_strips_every_stepped_rung():
    """D77: every scene has a stepped rung and a guest's choice runs it first (D75), so the ripple scenario's config copy
    strips them all: each mood lands as its still look, ring by ring, as on live hardware until Gate R. The rest of each
    ladder is the show's, in order; the show's own config is untouched."""
    import shutil

    import yaml

    from conductor.config import load_settings

    config = soak._soak_config_dir(None, ripple_stagger_s=1.2)
    try:
        raw, parsed = _parse_copy(config)
        stagger = load_settings(config).kiosk.ripple_stagger_s
    finally:
        shutil.rmtree(config.parent, ignore_errors=True)
    show = yaml.safe_load((CONFIG_DIR / "modes.yaml").read_text(encoding="utf-8"))
    assert list(raw["modes"]) == list(show["modes"]) and stagger == 1.2
    for mode_id, mode in raw["modes"].items():
        assert mode["ladder"] == [t for t in show["modes"][mode_id]["ladder"] if t != "stepped"]
        assert "stepped" not in mode["techniques"] and "motion" not in mode
        assert not parsed.modes[mode_id].moves
    assert all("stepped" in m["ladder"] for m in show["modes"].values())  # the show's own config keeps them


def _without_rungs(tmp_path, *mode_ids: str):
    """A copy of config/ whose modes.yaml has no stepped rung on `mode_ids` (ladder and technique)."""
    import shutil

    import yaml

    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    modes = yaml.safe_load((config / "modes.yaml").read_text(encoding="utf-8"))
    for mode_id in mode_ids:
        mode = modes["modes"][mode_id]
        mode["ladder"] = [t for t in mode["ladder"] if t != "stepped"]
        mode["techniques"].pop("stepped")
    (config / "modes.yaml").write_text(
        yaml.safe_dump(modes, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return config


def test_a_candidate_without_its_own_rung_gets_a_simulated_one_from_its_module_level_design(tmp_path):
    """The scenario still takes a candidate the show doesn't step (none since D77; Garden Gathering with its rung taken
    out here): a SIMULATED rung just before `modules`, its own module-level design (the hold is that rung), the
    scenario's cadence, copy that names the flow."""
    from pydantic import TypeAdapter

    from conductor.geometry import load_canopy_config
    from conductor.looks.schema import FieldCanvas, motion_words

    config = _without_rungs(tmp_path, "garden-gathering")
    # the scenario's travel technique only (no per-mode override)
    soak._apply_stepped(config, {**soak.load_soak_stepped(), "modes": ["garden-gathering"], "simulated": {}})
    raw, parsed = _parse_copy(config)
    mode = raw["modes"]["garden-gathering"]
    stepped, sample = mode["techniques"]["stepped"], mode["techniques"]["modules"]["sample"]
    assert mode["ladder"] == ["canvas", "slot", "stepped", "modules", "strand"]  # D75: just before modules
    assert stepped["canvas"] == sample["canvas"] and stepped["brightness"] == sample["brightness"]
    words = motion_words(
        TypeAdapter(FieldCanvas).validate_python(sample["canvas"]),
        load_canopy_config(config / "canopy.yaml"),
        "forward",
    )
    assert stepped["moving_feature"].startswith("SIMULATED")
    assert stepped["moving_feature"].endswith(f"so the palette moves {words}.")
    assert (stepped["step_s"], stepped["run_s"], stepped["steps_per_cycle"]) == (6, 90, 6)
    assert parsed.modes["garden-gathering"].techniques.stepped is not None


def test_a_simulated_rung_takes_its_kind_of_motion_from_the_overrides(tmp_path):
    """D75's `simulated:` overrides (none needed since D77, but kept): with the rungs taken out, a candidate gets a
    breathe, a shimmer or a level-only breath of its own design, whose copy says what changes and loads under the
    schema's rules; one with no override keeps the scenario's travel."""
    from conductor.config import ConductorSettings
    from conductor.show import _check_stepped

    modes = ["garden-gathering", "rainbow-row-sunset", "goodnight-glow", "lowcountry-calm"]
    config = _without_rungs(tmp_path, *modes)
    simulated = {  # each swing fits its scene's brightness
        "garden-gathering": {"mode": "breathe", "brightness_swing": 6, "lag_steps": 2},
        "rainbow-row-sunset": {"mode": "shimmer", "brightness_swing": 6, "lag_steps": 3, "seed": 7},
        "goodnight-glow": {"mode": "breathe", "brightness_swing": 4},
    }
    soak._apply_stepped(config, {**soak.load_soak_stepped(), "modes": modes, "simulated": simulated})
    raw, parsed = _parse_copy(config)
    ladders = {m: raw["modes"][m]["ladder"] for m in modes}
    assert ladders["garden-gathering"] == ["canvas", "slot", "stepped", "modules", "strand"]
    assert ladders["rainbow-row-sunset"] == ["canvas", "slot", "stepped", "modules", "strand"]
    assert ladders["lowcountry-calm"] == ["stepped", "modules", "strand"]
    assert ladders["goodnight-glow"] == ["stepped", "modules", "strand"]
    techs = {m: parsed.modes[m].techniques.stepped for m in modes}
    garden, sunset, goodnight, calm = (techs[m] for m in modes)
    assert (garden.mode, garden.brightness_swing, garden.lag_steps) == ("breathe", 6, 2)
    assert garden.moving_feature.startswith("SIMULATED") and "swells and softens" in garden.moving_feature
    assert (sunset.mode, sunset.brightness_swing, sunset.lag_steps, sunset.seed) == ("shimmer", 6, 3, 7)
    assert sunset.moving_feature.endswith("so the strands glow and soften on their own.")
    assert (goodnight.mode, goodnight.brightness_swing, goodnight.lag_steps) == ("breathe", 4, 0)
    assert goodnight.moving_feature.endswith("so the palette swells and softens.")  # no lag: no start named
    assert calm.mode == "travel" and calm.moving_feature.startswith("SIMULATED")
    assert all(parsed.modes[m].moves and not parsed.modes[m].motion for m in modes)
    _check_stepped(ConductorSettings(), parsed)  # every run ends before the idle return; motion.idle is off


def test_the_interrupting_mood_is_never_the_idle_mode():
    """A run is cut short by a mood outside the scenario, else another of its modes: never the running one and never
    the idle look (an interrupt with the idle look would hide the idle return the scenario measures). Every mood steps
    since D77, so the one chosen starts its own run."""
    import random

    from conductor.config import ConductorSettings

    settings = soak.load_soak_stepped()
    idle = ConductorSettings().kiosk.idle_mode
    for seed in range(50):
        rng = random.Random(seed)
        for mode_id in settings["modes"]:
            mood = soak._interrupting_mood(rng, [], settings["modes"], mode_id, idle)
            assert mood is not None and mood not in (idle, mode_id)
            assert mood in settings["modes"]
    # a mood outside the scenario, when there is one, is the one chosen (the soak's pool: every other mood of the
    # catalog but the idle look)
    others = [m for m in load_modes(CONFIG_DIR).modes if m not in settings["modes"] and m != idle]
    assert others and idle not in others
    for seed in range(20):
        mood = soak._interrupting_mood(random.Random(seed), others, settings["modes"], "golden-hour", idle)
        assert mood in others
    chosen = soak._interrupting_mood(
        random.Random(1), ["goodnight-glow"], settings["modes"], "golden-hour", idle
    )
    assert chosen == "goodnight-glow"
    assert soak._interrupting_mood(random.Random(1), [], [idle], idle, idle) is None


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
    """The instruments can fail: with the lights taking 9–10 s to apply a write and 3 s steps (a per-light run's pace
    on nine kits, the motion.lights cap less its reserve, D81), every step is superseded by the next before it shows,
    and the verdict says so. A look that moves: a #1-#3 look's per-light picture is still (Oct 21), nothing to
    supersede."""
    from conductor.clock import run_virtual
    from conductor.config import load_settings
    from conductor.design_lab.stepped import effective_step_s
    from conductor.sim.settings import SimSettings

    mode_id = (
        "coastal-tides-4"  # a ripple: every step a new frame (a glow's plateau repeats one, which lands)
    )
    lights = load_modes(CONFIG_DIR).mode(mode_id).techniques.stepped.lights
    assert lights.effect == "ripple"
    stepped = {
        **soak.load_soak_stepped(),
        "modes": [mode_id],
        "runs": 1,
        "interrupt_every": 0,
        "technique": {"direction": "forward", "steps_per_cycle": 6, "step_s": 6, "run_s": 45},
    }
    slow = SimSettings.from_acceptance().with_(
        device_apply_extra_s_min=9.0,
        device_apply_extra_s_max=10.0,
        throttle_429_rate=0.0,
        silent_drop_rate=0.0,
    )
    cfg = soak.SoakConfig(seed=1, report_path=None, stepped=stepped, sim_settings=slow)
    out = run_virtual(lambda: soak._stepped_soak(cfg))
    pace = effective_step_s(lights.step_s, 9, load_settings(), lights=True)
    assert out["runs"] == 1 and set(out["step_s"]) == {pace} == {3.0}
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
