"""`python -m conductor.soak` in miniature: the same schedule shape over 20 simulated minutes, and the verdicts."""

from __future__ import annotations

from conductor import soak
from conductor.sim.settings import SimSettings


def test_short_soak_passes_and_writes_the_report(tmp_path):
    report = tmp_path / "sim-acceptance.md"
    cfg = soak.SoakConfig(hours=1 / 3, changes=6, seed=3, report_path=report)
    results, verdicts, meta = soak.run_soak(cfg)
    failed = [v["name"] for v in verdicts if not v["pass"]]
    assert not failed, failed
    text = report.read_text()
    assert "| Measure | p50 | p95 | max | Target | Result |" in text and "**PASS**" in text
    assert "config/sim/acceptance.yaml" in text and "ShowWiring" in meta["compiler"]
    assert results["commission"]["ok"] and len(results["outages"]) == 2  # the 500 window and the outage
    assert results["max_queue"] <= 18


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


def test_builtin_compiler_covers_every_mode_on_its_ladder():
    from conductor.config import CONFIG_DIR, load_registry
    from conductor.engine_api import Tier

    compiler = soak.BuiltinCompiler(load_registry(CONFIG_DIR, None))
    for mode_id in compiler.modes():
        for tier in compiler.ladder(mode_id):
            plan = compiler.compile(mode_id, tier)
            assert len(plan.modules) == 9 and plan.feature
            if tier is Tier.T1:
                assert all(mt.slot and mt.slot_value and mt.slot_hash for mt in plan.modules.values())
    goodnight = compiler.compile("goodnight-glow", Tier.STRAND)
    assert "2200 K clamped to the device floor 2700 K" in goodnight.notes
