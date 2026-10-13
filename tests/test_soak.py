"""`python -m conductor.soak` in miniature: the same schedule shape over 20 simulated minutes, and the verdicts."""

from __future__ import annotations

from conductor import soak
from conductor.sim.settings import SimSettings


def test_short_soak_passes_and_writes_the_report(tmp_path):
    report = tmp_path / "sim-acceptance.md"
    cfg = soak.SoakConfig(hours=1 / 3, changes=6, seed=3, report_path=report)
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
