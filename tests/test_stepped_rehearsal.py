"""The stepped rehearsal (Gate R, conductor/rehearsal.py): one operator-started, bounded stepped run of a show look, on
the show's own wiring (build_services + ShowWiring, the shipped config/) and the simulator on virtual time, with its
report under <data>/bench/rehearsals and in telemetry. The ops route and its confirm sentence: tests/test_ops_routes.py;
the writer-path allowlist: tests/test_writer_paths.py."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from conductor.canopy import REHEARSAL_MESSAGE
from conductor.clock import VirtualClock, run_virtual
from conductor.config import CONFIG_DIR, load_recipes
from conductor.engine_api import Tier
from conductor.looks import parse_modes
from conductor.rehearsal import MAX_MINUTES, RehearsalRefused
from conductor.show import REHEARSAL_FEATURE, STEPPED_LIVE_OFF, ShowWiring
from conductor.sim.settings import SimSettings

REPORT_FIELDS = {
    "kind",
    "run_id",
    "mode",
    "label",
    "minutes",
    "run_s",
    "source",
    "simulated",
    "note",
    "started",
    "ended_at",
    "ended",
    "completed",
    "modules",
    "accounts",
    "cap_writes_per_min",
    "steps",
    "attempts",
    "puts",
    "throttled_429",
    "forbidden_403",
    "throttled_share",
    "max_writes_60s",
    "commands",
    "failed_throttled",
    "superseded",
    "skipped_steps",
    "verification_s",
    "governor_trips",
    "outages",
    "vignette",
    "alerts",
    "criteria",
    "verdict",
    "summary",
}
CRITERIA = ["completed", "failed_throttled", "throttled_share", "no_403", "vignettes_clean"]
QUIET_S = (
    70.0  # start-up (capability reads, simulator commissioning, the idle look) is over and out of the window
)


class Show:
    """The app's own show wiring on the virtual clock (as the soak builds it), over the shipped config/."""

    def __init__(self, services, wiring, engine, data: Path):
        self.services, self.wiring, self.engine, self.data = services, wiring, engine, data
        self.clock = services.clock

    @property
    def reports(self) -> list[Path]:
        return sorted((self.data / "bench" / "rehearsals").glob("*.json"))

    def report(self) -> dict:
        (path,) = self.reports
        return json.loads(path.read_text(encoding="utf-8"))

    def events(self, kind: str) -> list[dict]:
        return [e for e in self.services.telemetry.ring if e["kind"] == kind]

    async def run_for(self, seconds: float) -> None:
        await self.clock.sleep(seconds)

    async def ended(self, timeout: float = 60.0) -> None:
        """Until the last rehearsal's report is written."""
        waited = 0.0
        while (self.engine.last_rehearsal is None or self.engine._rehearsals) and waited < timeout:
            await self.clock.sleep(0.5)
            waited += 0.5
        assert self.engine.last_rehearsal is not None, "the rehearsal's report was never written"

    async def close(self) -> None:
        await self.wiring.close()
        await self.services.close()
        shutil.rmtree(self.data, ignore_errors=True)


async def show(*, live: bool = False, sim: SimSettings | None = None, monkeypatch=None) -> Show:
    from conductor.services import build_services

    data = Path(tempfile.mkdtemp(prefix="canopy-rehearsal-"))
    settings = sim or SimSettings.from_acceptance().with_(throttle_429_rate=0.0, silent_drop_rate=0.0)
    services = build_services(
        {"CONDUCTOR_DATA_DIR": str(data)}, clock=VirtualClock(), config_dir=CONFIG_DIR, sim_settings=settings
    )
    services.telemetry.dir = None
    if live:  # the venue box's tier rules (no Gate B/C flags) over the simulator's transport
        monkeypatch.setattr(services.engine_driver, "simulated", False)
    await services.start()
    wiring = ShowWiring(services)
    services.extras["show"] = wiring
    engine = await wiring.start()
    w = Show(services, wiring, engine, data)
    await w.run_for(QUIET_S)
    return w


# ---- the acceptance: a 2-minute rehearsal in sim -------------------------------------------------------------------
def test_a_two_minute_rehearsal_writes_a_complete_report_and_returns_to_the_still_look():
    async def main():
        w = await show()
        before = w.engine.state()
        preview = w.engine.rehearsal_preview("golden-hour", 2)
        result = await w.engine.rehearse("golden-hour", 2, source="ops")
        runner = w.engine.runner
        during = (w.engine.state(), runner.count, runner.step_s, w.engine.rehearsal_mode)
        await w.run_for(60)
        mid = w.engine.state()
        await w.run_for(65)
        await w.ended()
        await w.run_for(10)
        after = w.engine.state()
        out = (before, preview, result, during, mid, after, w.report(), w.events("rehearsal"), w.engine)
        out += (w.engine.runner, w.reports[0].name)
        await w.close()
        return out

    before, preview, result, during, mid, after, report, events, engine, runner, name = run_virtual(main)
    assert before.mode == "lowcountry-calm" and before.program is None
    assert result["ok"] and result["rehearsal"]["minutes"] == 2
    state, count, step_s, mode = during
    assert state.tier == "stepped" and mode == "golden-hour"
    assert count == int(120 / step_s) == 20  # run_s = minutes · 60, at the design's 6 s steps
    assert state.feature == REHEARSAL_FEATURE.format(words="from one edge")  # the copy says staff are testing
    assert mid.program is not None and mid.program["step"] == 10  # moving, half way
    # the report: every field, a pass in sim (no faults), labelled SIMULATED
    assert set(report) == REPORT_FIELDS
    assert [c["id"] for c in report["criteria"]] == CRITERIA and all(c["ok"] for c in report["criteria"])
    assert report["verdict"] == "pass" and report["completed"] and report["ended"] == "the run is over"
    assert report["simulated"] and "SIMULATED" in report["note"]
    assert report["modules"] == preview["modules"] and len(report["modules"]) == 9
    assert report["accounts"] == ["canopy"] and report["minutes"] == 2 and report["run_s"] == 120.0
    assert report["steps"] == {"step_s": 6.0, "design_step_s": 6.0, "planned": 20, "reached": 20, "sent": 20,
                               "skipped": 0}  # fmt: skip
    # the hold, 19 steps and the hold again: one PUT per module each, all verified, nothing throttled
    assert report["attempts"] == report["puts"] == 9 * 21
    assert report["commands"] == {"applied": 189} and report["failed_throttled"] == report["superseded"] == 0
    assert report["throttled_429"] == report["forbidden_403"] == 0 and report["throttled_share"] == 0.0
    assert report["verification_s"]["count"] == 189 and 0 < report["verification_s"]["p50"] <= 3.0
    assert set(report["max_writes_60s"]) == {"canopy"} and report["max_writes_60s"]["canopy"] >= 90
    assert report["governor_trips"] == 0 and report["outages"] == 0
    assert report["vignette"]["clean"] and report["vignette"]["accounts"] == [
        "bedroom",
        "entry",
        "garden",
        "living",
    ]
    assert name == f"{report['run_id']}.json" and report["run_id"].endswith("-golden-hour")
    # telemetry carries it too
    assert [e["action"] for e in events] == ["start", "end"]
    assert events[-1]["verdict"] == "pass" and events[-1]["file"] == name
    assert engine.telemetry.recent(kind="alert")[-1]["level"] == "info"  # a pass is news, not a problem
    # static afterwards: the gate closed, nothing steps, the look at its usual tier (sim: T1)
    assert engine.rehearsal_mode is None and engine.last_rehearsal["verdict"] == "pass"
    assert after.mode == "golden-hour" and after.tier == "canvas" and after.program is None
    assert runner is None and after.phase == "ready"
    assert engine.rehearsal_status()["running"] is None


def test_the_tier_gate_opens_for_the_rehearsals_mode_only_and_only_while_it_runs(monkeypatch):
    async def main():
        w = await show(live=True, monkeypatch=monkeypatch)
        gate = w.wiring.tier_allowed
        before = (gate("golden-hour", Tier.STEPPED), w.wiring.ladder("golden-hour"))
        await w.engine.rehearse("golden-hour", 1, source="ops")
        during = (
            gate("golden-hour", Tier.STEPPED),
            gate("coastal-tides", Tier.STEPPED),
            w.wiring.ladder("golden-hour"),
            w.wiring.ladder("coastal-tides"),
            w.engine.current_plan().tier,
        )
        await w.run_for(62)
        await w.ended()
        await w.run_for(5)
        after = (gate("golden-hour", Tier.STEPPED), w.wiring.ladder("golden-hour"), w.engine.current_plan())
        settle = [c for c in w.events("command") if c["label"].startswith("after the stepped rehearsal")]
        out = (before, during, after, settle, w.report())
        await w.close()
        return out

    before, during, after, settle, report = run_virtual(main)
    assert before[0] == (False, STEPPED_LIVE_OFF) and Tier.STEPPED not in before[1]
    allowed, other, ladder, other_ladder, tier = during
    assert allowed == (True, "") and other == (False, STEPPED_LIVE_OFF)
    assert ladder[0] is Tier.STEPPED and Tier.STEPPED not in other_ladder and tier is Tier.STEPPED
    gate, ladder, plan = after
    assert gate == (False, STEPPED_LIVE_OFF) and Tier.STEPPED not in ladder
    # live runs T3 before Gate C: the still look is the hold itself, so settling sends nothing more
    assert plan.tier is Tier.T3 and plan.program is None and settle == []
    assert report["completed"] and not report["simulated"] and report["note"] is None


def test_the_rehearsal_design_is_the_modes_module_level_design_stepped_and_modes_yaml_is_untouched():
    async def main():
        w = await show()
        modes = w.wiring.rehearsal_modes("coastal-tides")
        plan = w.wiring.rehearsal_plan("coastal-tides")
        shipped = w.wiring.modes_file.modes["coastal-tides"]
        out = (modes.modes["coastal-tides"], shipped, plan, w.wiring.ladder("coastal-tides"))
        await w.close()
        return out

    mode, shipped, plan, ladder = run_virtual(main)
    stepped = mode.techniques.stepped
    assert mode.ladder == ["stepped", *shipped.ladder] and shipped.techniques.stepped is None
    assert stepped.canvas == shipped.techniques.modules.sample.canvas
    assert stepped.brightness == shipped.techniques.modules.sample.brightness
    assert (stepped.steps_per_cycle, stepped.step_s, stepped.direction) == (6, 6.0, "forward")  # D65 defaults
    assert stepped.moving_feature == REHEARSAL_FEATURE.format(words="diagonally")
    assert plan.tier is Tier.STEPPED and len(plan.program.steps) == 6 and len(plan.modules) == 9
    assert Tier.STEPPED not in ladder  # outside a rehearsal the show's ladder is modes.yaml's


# ---- it stops ------------------------------------------------------------------------------------------------------
def test_it_stops_at_the_first_403_and_fails():
    async def main():
        w = await show()
        await w.engine.rehearse("golden-hour", 5, source="ops")
        await w.run_for(20)
        # a 403 from Hubspace (the simulator doesn't make them): the attempt the executor would record
        for address in ("sim-canopy-r1c1", "sim-canopy-r1c2"):  # two at once: it stops once
            w.services.telemetry.record(
                "attempt", device_id=address, n=1, outcome="throttled", http_status=403, elapsed_s=0.4
            )
        await w.ended()
        out = (w.report(), w.engine.runner, w.engine.state(), w.events("alert"))
        await w.close()
        return out

    report, runner, state, alerts = run_virtual(main)
    assert report["ended"] == "Hubspace answered 403 on account canopy" and not report["completed"]
    assert report["forbidden_403"] == 2 and report["verdict"] == "fail"
    assert {c["id"]: c["ok"] for c in report["criteria"]} == {
        "completed": False,
        "failed_throttled": True,
        "throttled_share": True,
        "no_403": False,
        "vignettes_clean": True,
    }
    assert report["steps"]["reached"] < report["steps"]["planned"]
    assert len([a for a in alerts if "403 (forbidden)" in a["message"]]) == 1
    verdict = [a for a in alerts if a["message"].startswith("Stepped rehearsal: SIMULATED")]
    assert len(verdict) == 1 and verdict[0]["level"] == "warn"
    assert state.program is None and (runner is None or not runner.running)


def test_a_governor_trip_ends_it_and_the_report_says_so():
    async def main():
        storm = SimSettings.from_acceptance().with_(throttle_429_rate=0.35, silent_drop_rate=0.0)
        w = await show(sim=storm)
        await w.engine.rehearse("golden-hour", 5, source="ops")
        await w.run_for(120)
        await w.ended()
        out = (w.report(), w.engine.stepped_cooloff)
        await w.close()
        return out

    report, cooloff = run_virtual(main)
    assert report["governor_trips"] == 1 and "canopy" in cooloff
    assert report["ended"] == "the throttle governor stopped it" and not report["completed"]
    assert report["throttled_429"] > 0 and report["throttled_share"] >= 0.05 and report["verdict"] == "fail"
    assert any("throttle" in a["message"] for a in report["alerts"])


def test_ops_choosing_another_look_ends_it_with_what_it_measured():
    async def main():
        w = await show()
        await w.engine.rehearse("golden-hour", 3, source="ops")
        await w.run_for(15)
        await w.engine.request_mode("coastal-tides", source="ops")
        await w.ended()
        out = (w.report(), w.engine.current_plan(), w.engine.rehearsal_mode)
        await w.close()
        return out

    report, plan, mode = run_virtual(main)
    assert report["ended"] == "ops chose coastal-tides" and not report["completed"]
    assert report["verdict"] == "fail" and report["attempts"] >= 9 * 3
    assert plan.mode_id == "coastal-tides" and plan.program is None and mode is None


def test_ops_can_stop_it_and_a_pause_stops_it():
    async def main():
        w = await show()
        await w.engine.rehearse("golden-hour", 3, source="ops")
        await w.run_for(13)
        w.engine.floors["golden-hour"] = 1  # set during it (its ladder's index): it doesn't outlive it
        stopped = w.engine.stop_rehearsal(source="ops")
        at_once = (w.engine.rehearsal_mode, w.engine.runner.running, dict(w.engine.floors))  # gate closed
        await w.engine.request_mode("coastal-tides", source="ops")  # chosen before the report is written
        await w.ended()
        await w.run_for(5)
        chosen = w.engine.current_plan().mode_id  # the settle never undoes a later choice
        first = w.report()
        for path in w.reports:
            path.unlink()
        w.engine.last_rehearsal = None
        await w.engine.rehearse("garden-gathering", 3, source="ops")
        await w.run_for(7)
        w.engine.pause_guest_input(True, source="ops")
        await w.ended()
        second = w.report()
        out = (stopped, first, second, w.engine.state(), at_once, chosen)
        w.engine.pause_guest_input(False, source="ops")
        await w.close()
        return out

    stopped, first, second, state, at_once, chosen = run_virtual(main)
    assert at_once == (None, False, {}) and chosen == "coastal-tides"
    assert stopped["ok"] and first["ended"] == "stopped by ops" and not first["completed"]
    assert first["steps"]["reached"] == 2
    assert (
        second["ended"] == "guest input was paused (ops), which holds every step" and not second["completed"]
    )
    assert state.program is None


# ---- while it runs ---------------------------------------------------------------------------------------------------
def test_guests_wait_the_idle_hold_waits_and_the_run_lasts_its_minutes():
    async def main():
        w = await show()
        await w.engine.set_guest_brightness(40, source="ops")
        await w.engine.rehearse("golden-hour", 4, source="ops")
        cleared = w.engine.state().guest_brightness
        kiosk = await w.engine.request_mode("coastal-tides", source="kiosk")
        dim = await w.engine.set_guest_brightness(40, source="kiosk")
        await w.run_for(200)  # past kiosk.idle_return_s (180 s)
        held = (w.engine.state().mode, w.engine.rehearsal_mode, w.engine.runner.running)
        await w.run_for(45)
        await w.ended()
        await w.run_for(20)
        after = w.engine.state().mode  # the still look holds for idle_return_s from the end, not the start
        out = (kiosk, dim, held, w.report(), cleared, after)
        await w.close()
        return out

    kiosk, dim, held, report, cleared, after = run_virtual(main)
    assert cleared is None and after == "golden-hour"
    assert kiosk.refused and kiosk.message == REHEARSAL_MESSAGE and 230 < kiosk.retry_after_s <= 240
    assert dim.refused and dim.message == REHEARSAL_MESSAGE
    assert held == ("golden-hour", "golden-hour", True)
    assert report["completed"] and report["steps"]["planned"] == 40 and report["run_s"] == 240.0


def test_problems_on_a_vignette_account_fail_it():
    async def main():
        w = await show()
        await w.engine.rehearse("golden-hour", 1, source="ops")
        await w.run_for(10)
        telemetry = w.services.telemetry
        telemetry.record("attempt", device_id="sim-living-lamp-1", n=1, outcome="throttled", http_status=429)
        telemetry.record("alert", level="error", account="bedroom", message="Bedroom account signed out.")
        telemetry.record("token", account="entry", action="ensure_fresh", outcome="auth")
        telemetry.record(
            "alert", level="warn", device_id="sim-living-lamp-2", message="Wash left is offline."
        )
        telemetry.record("alert", level="info", account="living", message="Living room reconnected.")
        telemetry.record("attempt", device_id="sim-canopy-r0c0", n=1, outcome="denied", http_status=None)
        telemetry.record("command", device_id="sim-living-lamp-1", status="failed", timings={})
        await w.run_for(55)
        await w.ended()
        out = w.report()
        await w.close()
        return out

    report = run_virtual(main)
    vignette = report["vignette"]
    assert vignette["attempts"] == 1 and vignette["throttled_429"] == 1 and not vignette["clean"]
    assert (
        vignette["failed_commands"] == 1 and "failed" not in report["commands"]
    )  # theirs, not the rehearsal's
    assert vignette["alerts"] == ["Bedroom account signed out.", "Wash left is offline."]
    assert vignette["token_problems"] == ["entry: auth"]
    # the vignette's attempt isn't the rehearsal's; a denied one never left the process (not a PUT)
    assert report["attempts"] == 9 * 11 + 1 and report["puts"] == 9 * 11
    assert report["completed"] and report["verdict"] == "fail"
    assert [c["id"] for c in report["criteria"] if not c["ok"]] == ["vignettes_clean"]


def test_stopping_the_engine_ends_it_and_still_writes_the_report():
    async def main():
        w = await show()
        await w.engine.rehearse("golden-hour", 2, source="ops")
        await w.run_for(8)
        await w.engine.stop()
        out = (w.report(), w.engine.rehearsal_mode)
        await w.close()
        return out

    report, mode = run_virtual(main)
    assert report["ended"] == "the canopy engine stopped" and not report["completed"] and mode is None


# ---- refusals ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("mode", "minutes", "setup", "says"),
    [
        ("golden-hour", 0, None, "1 to 10 whole minutes"),
        ("golden-hour", MAX_MINUTES + 1, None, "1 to 10 whole minutes"),
        ("golden-hour", True, None, "1 to 10 whole minutes"),
        ("lowcountry-calm", 2, None, "idle mode, which never steps"),
        ("golden-hour", 2, "paused", "Resume guest input first"),
        ("golden-hour", 2, "running", "already running"),
        ("golden-hour", 2, "outage", "Hubspace isn't answering"),
        ("golden-hour", 2, "cooloff", "throttle governor stopped a stepped run on account canopy"),
        ("no-such-mode", 2, None, "can't be rehearsed stepped"),
        ("golden-hour", 2, "stopped", "engine is stopped"),
        ("golden-hour", 2, "commissioning", "Commissioning is writing"),
        ("golden-hour", 2, "flat", "has no stepped design to rehearse"),
        ("golden-hour", 2, "gate", "would show canvas, not a stepped look"),
        ("golden-hour", 2, "no-sample", "no module-level design sampled at the module centres"),
        ("golden-hour", 2, "mean", "no module-level design sampled at the module centres"),
    ],
)
def test_it_refuses_plainly_and_sends_nothing(mode, minutes, setup, says):
    async def main():
        w = await show()
        if setup == "paused":
            w.engine.pause_guest_input(True, source="ops")
        elif setup == "running":
            await w.engine.rehearse("coastal-tides", 1, source="ops")
        elif setup == "outage":
            w.engine.enter_outage("test")
        elif setup == "cooloff":
            w.engine.stepped_cooloff["canopy"] = w.clock.monotonic() + 120
        elif setup == "stopped":
            await w.engine.stop()
        elif setup == "commissioning":
            w.engine._commissioning = True
        elif setup == "flat":  # a "rehearsal" design that doesn't step
            w.engine.deps.rehearsal_compile = lambda mode_id: w.wiring.compile(mode_id, Tier.T3)
        elif setup == "gate":  # the stepped tier stays shut: the ladder would land on the still look
            w.engine.floors["golden-hour"] = 2
            w.engine.deps.tier_allowed = lambda mode_id, tier: (tier is not Tier.STEPPED, "shut (test)")
        elif setup in ("no-sample", "mean"):
            raw = w.wiring.modes_file.model_dump(mode="json", exclude_unset=True)
            gh = raw["modes"]["golden-hour"]
            if setup == "mean":
                gh["techniques"]["modules"]["sample"]["at"] = "mean"
            else:
                gh["ladder"] = [t for t in gh["ladder"] if t != "modules"]
                del gh["techniques"]["modules"]
            canopy = w.wiring.context().canopy
            w.wiring.modes_file = parse_modes(raw, recipes=load_recipes(CONFIG_DIR), canopy=canopy)
        await w.engine.wait_idle()  # (a rehearsal already running: its hold has its results)
        sent = len(w.events("command"))
        with pytest.raises(RehearsalRefused) as refused:
            await w.engine.rehearse(mode, minutes, source="ops")
        await w.engine.wait_idle()
        out = (str(refused.value), len(w.events("command")) - sent, w.engine.rehearsal_mode, w.engine.floors)
        if setup == "paused":
            w.engine.pause_guest_input(False, source="ops")
        await w.close()
        return out

    message, sent, rehearsing, floors = run_virtual(main)
    assert says in message
    assert sent == 0 and floors == ({"golden-hour": 2} if setup == "gate" else {})  # a floor is put back
    assert rehearsing == ("coastal-tides" if setup == "running" else None)


def test_a_floor_set_before_the_rehearsal_is_back_afterwards():
    async def main():
        w = await show(live=False)
        w.engine.floors["golden-hour"] = 1  # e.g. a throttle downgrade earlier: slot (T2) or below
        await w.engine.rehearse("golden-hour", 1, source="ops")
        during = dict(w.engine.floors)
        w.engine.floors["golden-hour"] = (
            2  # a downgrade during it indexes the rehearsal's ladder: dropped after
        )
        await w.run_for(62)
        await w.ended()
        out = (during, dict(w.engine.floors), w.engine.current_plan().tier)
        await w.close()
        return out

    during, after, tier = run_virtual(main)
    # from the floor down, as before it: T2 needs a slot hand-painted in the app (there's none), so modules (T3), not
    # the T1 the mode shows in sim without a floor
    assert during == {} and after == {"golden-hour": 1} and tier is Tier.T3


def test_the_safe_look_ends_it_and_a_look_that_cant_step_never_starts_one():
    async def main():
        w = await show()
        await w.engine.rehearse("golden-hour", 2, source="ops")
        await w.run_for(10)
        await w.engine.safe_look(source="ops")
        await w.ended()
        first = w.report()
        for path in w.reports:
            path.unlink()
        w.engine.last_rehearsal = None
        # 9 modules on one account within 40 writes a minute needs 13.5 s steps, past max_step_s (12 s)
        w.services.settings.motion.stepped.account_writes_per_min = 40
        result = await w.engine.rehearse("coastal-tides", 2, source="ops")
        await w.ended()
        out = (first, result, w.report(), w.engine.rehearsal_mode, w.engine.state())
        await w.close()
        return out

    first, result, second, mode, state = run_virtual(main)
    assert first["ended"] == "Lowcountry Calm (strand) replaced it (ops)" and not first["completed"]
    assert not result["ok"] and result["message"].startswith(
        "The rehearsal didn't start: the look holds still"
    )
    assert "max_step_s" in second["ended"] and not second["completed"] and mode is None
    assert state.program is None


def test_an_ops_choice_that_cant_be_shown_still_ends_the_run():
    async def main():
        w = await show()
        await w.engine.rehearse("golden-hour", 3, source="ops")
        await w.run_for(10)
        refused = await w.engine.request_mode("no-such-mode", source="ops")
        out = (refused, w.engine.runner.running, w.engine.rehearsal_mode)
        await w.ended()
        out += (w.report(), w.engine.state())
        await w.close()
        return out

    refused, running, mode, report, state = run_virtual(main)
    assert refused.refused and not running and mode is None  # never steps on without its gate
    assert report["ended"] == "ops chose no-such-mode" and state.program is None


def test_a_stale_end_of_the_last_rehearsal_never_ends_the_next_one():
    async def main():
        w = await show()
        await w.engine.rehearse("golden-hour", 2, source="ops")
        await w.run_for(10)
        w.services.telemetry.record(  # a 403 for this rehearsal: its stop is queued for the next tick
            "attempt", device_id="sim-canopy-r0c0", n=1, outcome="throttled", http_status=403, elapsed_s=0.3
        )
        w.engine.stop_rehearsal(source="ops")  # its runner's end event still has a call queued too
        await w.engine.rehearse("coastal-tides", 2, source="ops")  # in the same tick
        await w.run_for(1)
        out = (w.engine.rehearsal_mode, w.engine.runner.running)
        await w.engine.stop()
        await w.close()
        return out

    assert run_virtual(main) == ("coastal-tides", True)


def test_the_settle_never_picks_the_stepped_rung_of_a_mode_that_has_one():
    """A mode with a stepped rung of its own (modes.yaml after D65): after a 1-minute rehearsal its own 90 s run would
    still be going; the settle shows the still rung instead."""
    from test_engine_program import quiet_start, stepped_world

    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.rehearse("radial-outward", 1, source="ops")
        await w.run_for(65)
        out = (w.engine.current_plan(), w.engine.runner, w.engine.last_rehearsal)
        await w.close()
        return out

    plan, runner, last = run_virtual(main)
    assert last["ended"] == "the run is over" and last["file"] is None  # (no data dir: telemetry only)
    assert plan.tier is Tier.T3 and plan.program is None and runner is None


def test_writes_per_minute_are_counted_in_a_half_open_60_s_window():
    from conductor.rehearsal import Rehearsal

    r = object.__new__(Rehearsal)
    r._sent = {"canopy": [60.0, 0.0, 59.9, 30.0], "living": [0.0, 60.0], "entry": []}
    assert r.max_writes() == {"canopy": 3, "entry": 0, "living": 1}


def test_a_command_that_failed_throttled_after_retries_fails_it_and_an_outage_doesnt():
    async def main():
        w = await show()
        await w.engine.rehearse("golden-hour", 1, source="ops")
        await w.run_for(1)
        t = w.services.telemetry
        for address, outcome, status in (
            ("sim-canopy-r0c0", "throttled", 429),
            ("sim-canopy-r0c1", "throttled", 503),  # an outage signature, not throttling
            ("sim-canopy-r0c2", "client_error", 400),  # refused, not throttled
        ):
            t.record("attempt", device_id=address, n=5, outcome=outcome, http_status=status, elapsed_s=0.2)
            t.record("command", device_id=address, status="failed", timings={}, source="program")
        await w.run_for(64)
        await w.ended()
        out = w.report()
        await w.close()
        return out

    report = run_virtual(main)
    assert report["failed_throttled"] == 1 and report["commands"]["failed"] == 3
    assert report["throttled_share"] < 0.05  # 1 of 102
    assert [c["id"] for c in report["criteria"] if not c["ok"]] == ["failed_throttled"]


def test_throttled_attempts_at_5_percent_fail_it_and_writes_count_by_send_time():
    async def main():
        w = await show()
        await w.engine.rehearse("golden-hour", 2, source="ops")
        await w.run_for(5)
        t = w.services.telemetry
        t.record("attempt", device_id="sim-living-lamp-1", n=1, outcome="ok", http_status=200, elapsed_s=0.0)
        w.engine.enter_outage("test")
        w.engine.exit_outage("test")
        await w.run_for(62)
        # 11 throttled among about 200 attempts (over 5 %), late, so the governor's 60 s window holds ~90 (≈ 11 %)
        for _ in range(11):
            t.record("attempt", device_id="sim-canopy-r1c1", n=2, outcome="throttled", http_status=429)
        # answered 4 s after it was sent, 58 s after the first: both within one minute on the living account
        t.record("attempt", device_id="sim-living-lamp-2", n=1, outcome="ok", http_status=200, elapsed_s=4.0)
        await w.run_for(60)
        await w.ended()
        out = w.report()
        await w.close()
        return out

    report = run_virtual(main)
    assert report["throttled_429"] == 11 and report["throttled_share"] >= 0.05
    assert [c["id"] for c in report["criteria"] if not c["ok"]] == ["throttled_share"]
    assert report["max_writes_60s"]["living"] == 2 and report["outages"] == 1


def test_a_report_that_cant_be_saved_says_so():
    async def main():
        w = await show()
        blocker = w.data / "not-a-folder"
        blocker.write_text("x")
        w.engine.deps.rehearsal_dir = blocker / "rehearsals"
        await w.engine.rehearse("golden-hour", 1, source="ops")
        w.engine.stop_rehearsal(source="ops")
        await w.ended()
        out = (w.engine.last_rehearsal, w.events("alert"), w.events("rehearsal"))
        await w.close()
        return out

    last, alerts, events = run_virtual(main)
    assert last["file"] is None and events[-1]["file"] is None
    assert any("report couldn't be saved" in a["message"] for a in alerts)
