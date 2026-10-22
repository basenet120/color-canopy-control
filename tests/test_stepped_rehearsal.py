"""The stepped rehearsal (Gate R, conductor/rehearsal.py): one operator-started, bounded stepped run of a show look, on
the show's own wiring (build_services + ShowWiring, the shipped config/) and the simulator on virtual time, with its
report under <data>/bench/rehearsals and in telemetry. The ops route and its confirm sentence: tests/test_ops_routes.py;
the writer-path allowlist: tests/test_writer_paths.py."""

from __future__ import annotations

import dataclasses
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
from conductor.scenes import load_scenes
from conductor.show import REHEARSAL_FEATURE, STEPPED_LIVE_OFF, ShowWiring
from conductor.sim.settings import SimSettings

REPORT_FIELDS = {
    "kind",
    "lights",  # D80: a per-light run
    "rungs",  # D80: the rate test's rungs (None for a rehearsal)
    "fastest_clean_step_s",
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
    "alerts",
    "criteria",
    "verdict",
    "summary",
}
CRITERIA = ["completed", "failed_throttled", "throttled_share", "no_403", "max_writes_60s"]
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


async def show(
    *,
    live: bool = False,
    sim: SimSettings | None = None,
    monkeypatch=None,
    stepped_live: bool | None = None,
    per_light: bool = True,
) -> Show:
    """`stepped_live`: motion.stepped.live_enabled for this show (None = as shipped, on since D79); False = before
    Gate R, where the live gate refuses the stepped rung outside a rehearsal. `per_light=False`: compile without
    per-light motion (as a live kit without Gate C), so a rehearsal steps the rung's module-level design (D65) although
    every look carries a `lights` block since Oct 21 (a #1-#3 look's is a still picture)."""
    from conductor.services import build_services

    data = Path(tempfile.mkdtemp(prefix="canopy-rehearsal-"))
    settings = sim or SimSettings.from_acceptance().with_(throttle_429_rate=0.0, silent_drop_rate=0.0)
    services = build_services(
        {"CONDUCTOR_DATA_DIR": str(data)}, clock=VirtualClock(), config_dir=CONFIG_DIR, sim_settings=settings
    )
    services.telemetry.dir = None
    if live:  # the show box's tier rules (no Gate B/C flags) over the simulator's transport
        monkeypatch.setattr(services.engine_driver, "simulated", False)
    if stepped_live is not None:
        services.settings.motion.stepped.live_enabled = stepped_live
    await services.start()
    wiring = ShowWiring(services)
    if not per_light:
        context = wiring.context
        wiring.context = lambda *a, **kw: dataclasses.replace(context(*a, **kw), lights=False)
    services.extras["show"] = wiring
    engine = await wiring.start()
    w = Show(services, wiring, engine, data)
    await w.run_for(QUIET_S)
    return w


def engine_moving(mode_id: str) -> str:
    """The shipped mode's own stepped moving copy (config/modes.yaml, D65)."""
    from conductor.looks import load_modes

    return load_modes(CONFIG_DIR).mode(mode_id).techniques.stepped.moving_feature


def without_rung(wiring, *mode_ids: str) -> None:
    """The show's modes with `mode_ids`' own stepped rungs taken off (D77 gives every scene one, a Lab edit could take
    one off): the path where the rehearsal steps a look's module-level design at the D65 defaults."""
    raw = wiring.modes_file.model_dump(mode="json", exclude_unset=True)
    for mode_id in mode_ids:
        entry = raw["modes"][mode_id]
        entry["ladder"] = [t for t in entry["ladder"] if t != "stepped"]
        entry["techniques"].pop("stepped", None)
        entry.pop("motion", None)
    ctx = wiring.context()
    modes = parse_modes(raw, recipes=ctx.recipes, canopy=ctx.canopy, path="a test without a rung")
    wiring.install_looks(modes, ctx.recipes)


SCENE_NAME = {s.id: s.name for s in load_scenes(CONFIG_DIR).scenes}  # D77: "Lowcountry Calm #1"


# ---- the acceptance: a 2-minute rehearsal in sim -------------------------------------------------------------------
def test_a_two_minute_rehearsal_writes_a_complete_report_and_returns_to_the_still_look():
    async def main():
        w = await show(per_light=False)
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
    # run_s = minutes · 60, at 7.5 s steps: the design's 6 s stretched to 9 modules at 72 writes a minute (the
    # account's cap of 90 less the 20 % reserve)
    assert step_s == 7.5 and count == int(120 / step_s) == 16
    # D65: Golden Hour has its own stepped rung, so the rehearsal runs it with its own moving copy (a mode without
    # one says staff are testing: test_the_rehearsal_design_is_the_modes_module_level_design_stepped_...)
    assert state.feature == engine_moving("golden-hour")
    assert mid.program is not None and mid.program["step"] == 8  # moving, half way
    # the report: every field, a pass in sim (no faults), labelled SIMULATED
    assert set(report) == REPORT_FIELDS
    assert [c["id"] for c in report["criteria"]] == CRITERIA and all(c["ok"] for c in report["criteria"])
    assert report["verdict"] == "pass" and report["completed"] and report["ended"] == "the run is over"
    assert report["simulated"] and "SIMULATED" in report["note"]
    assert report["modules"] == preview["modules"] and len(report["modules"]) == 9
    assert report["accounts"] == ["canopy"] and report["minutes"] == 2 and report["run_s"] == 120.0
    assert report["steps"] == {"step_s": 7.5, "design_step_s": 6.0, "planned": 16, "reached": 16, "sent": 16,
                               "skipped": 0, "budget_holds": 0}  # fmt: skip
    # the hold, 15 steps and the hold again: one PUT per module each, all verified, nothing throttled
    assert report["attempts"] == report["puts"] == 9 * 17
    assert report["commands"] == {"applied": 153} and report["failed_throttled"] == report["superseded"] == 0
    assert report["throttled_429"] == report["forbidden_403"] == 0 and report["throttled_share"] == 0.0
    assert report["verification_s"]["count"] == 153 and 0 < report["verification_s"]["p50"] <= 3.0
    # what ops confirmed is what happened: paced at 72 a minute, never past the cap of 90 in any 60 s
    assert "paced at up to 72 writes a minute" in preview["confirm"]
    room = "less than one write per module of room under 90 writes in a minute is held back"  # D75
    assert room in preview["confirm"]
    assert report["cap_writes_per_min"] == 90
    assert set(report["max_writes_60s"]) == {"canopy"} and 72 <= report["max_writes_60s"]["canopy"] <= 81
    assert report["governor_trips"] == 0 and report["outages"] == 0
    assert "vignette" not in report  # the vignettes are outside this system (D67)
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
        w = await show(live=True, monkeypatch=monkeypatch, stepped_live=False)  # before Gate R
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
    # the shipped ladders have a stepped rung (D65) that the live gate refuses outside a rehearsal of that mode
    golden = [Tier.T1, Tier.T2, Tier.STEPPED, Tier.T3, Tier.STRAND]
    assert before == ((False, STEPPED_LIVE_OFF), golden)
    allowed, other, ladder, other_ladder, tier = during
    assert allowed == (True, "") and other == (False, STEPPED_LIVE_OFF)
    assert ladder[0] is Tier.STEPPED and ladder.count(Tier.STEPPED) == 1 and tier is Tier.STEPPED
    assert other_ladder == [Tier.STEPPED, Tier.T3, Tier.STRAND]  # Coastal Tides' own, still refused live
    gate, ladder, plan = after
    assert gate == (False, STEPPED_LIVE_OFF) and ladder == golden
    # live runs T3 before Gate C: the still look is the hold itself, so settling sends nothing more
    assert plan.tier is Tier.T3 and plan.program is None and settle == []
    assert report["completed"] and not report["simulated"] and report["note"] is None


def test_the_rehearsal_design_is_the_modes_module_level_design_stepped_and_modes_yaml_is_untouched():
    async def main():
        w = await show()
        # Rainbow Row Sunset #1 without its own stepped rung (D77 gives it one; a Lab edit could take it off)
        without_rung(w.wiring, "rainbow-row-sunset")
        modes = w.wiring.rehearsal_modes("rainbow-row-sunset")
        plan = w.wiring.rehearsal_plan("rainbow-row-sunset")
        shipped = w.wiring.modes_file.modes["rainbow-row-sunset"]
        out = (modes.modes["rainbow-row-sunset"], shipped, plan, w.wiring.ladder("rainbow-row-sunset"))
        tides = w.wiring.rehearsal_modes("coastal-tides").modes["coastal-tides"]
        out += (tides, w.wiring.modes_file.modes["coastal-tides"])
        await w.close()
        return out

    mode, shipped, plan, ladder, tides, shipped_tides = run_virtual(main)
    stepped = mode.techniques.stepped
    assert mode.ladder == ["stepped", *shipped.ladder] and shipped.techniques.stepped is None
    assert stepped.canvas == shipped.techniques.modules.sample.canvas
    assert stepped.brightness == shipped.techniques.modules.sample.brightness
    assert (stepped.steps_per_cycle, stepped.step_s, stepped.direction) == (6, 6.0, "forward")  # D65 defaults
    assert stepped.moving_feature == REHEARSAL_FEATURE.format(words="diagonally")
    assert plan.tier is Tier.STEPPED and len(plan.program.steps) == 6 and len(plan.modules) == 9
    assert Tier.STEPPED not in ladder  # outside a rehearsal the show's ladder is modes.yaml's
    # a mode with its own stepped rung (D65) rehearses exactly that: its design, cadence and copy
    assert tides.techniques.stepped == shipped_tides.techniques.stepped
    assert tides.ladder == shipped_tides.ladder == ["stepped", "modules", "strand"]


# ---- it stops ------------------------------------------------------------------------------------------------------
def test_it_stops_at_the_first_403_and_fails():
    async def main():
        w = await show(per_light=False)
        await w.engine.rehearse("golden-hour", 5, source="ops")
        await w.run_for(40)
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
        "max_writes_60s": True,
    }
    assert report["steps"]["reached"] < report["steps"]["planned"]
    assert len([a for a in alerts if "403 (forbidden)" in a["message"]]) == 1
    verdict = [a for a in alerts if a["message"].startswith("Stepped rehearsal: SIMULATED")]
    assert len(verdict) == 1 and verdict[0]["level"] == "warn"
    assert state.program is None and (runner is None or not runner.running)


def test_after_a_403_nothing_more_is_sent_and_ops_cant_rehearse_again_until_the_cool_off_ends(monkeypatch):
    """403 is Hubspace's throttling signature (the venue IP may be blocked): no hold burst after it, the canopy keeps
    the step it was sent (installed as a still T3 plan, so the kiosk draws what is there), no re-run and no stepped
    look on that account for governor_cooloff_s, then the still look — only where it differs."""

    async def main():
        w = await show(live=True, monkeypatch=monkeypatch)  # the show box's rules: T3 is the still look
        w.services.settings.motion.stepped.governor_cooloff_s = 90  # shorter than the idle return (180 s)
        await w.engine.rehearse("golden-hour", 5, source="ops")
        await w.run_for(20)  # steps 1 and 2 are out and verified
        runner = w.engine.runner
        step, hold = runner.program.steps[runner.shown % runner.cycle], runner.program.steps[0]
        t403 = w.clock.now()
        w.services.telemetry.record(
            "attempt", device_id="sim-canopy-r1c1", n=1, outcome="throttled", http_status=403, elapsed_s=0.4
        )
        await w.ended()
        await w.run_for(30)
        quiet = [e for e in w.events("attempt") if e["t"] > t403]
        plan, cooloff, mono = w.engine.current_plan(), dict(w.engine.stepped_cooloff), w.clock.monotonic()
        try:
            again = w.engine.rehearsal_preview("golden-hour", 5)["confirm"]
        except RehearsalRefused as e:
            again = f"refused: {e}"
        await w.run_for(
            65
        )  # the cool-off is over: the still look goes out (the idle return is still to come)
        settle = [c for c in w.events("command") if c["label"].startswith("after the stepped rehearsal")]
        out = (step, hold, quiet, plan, cooloff, mono, again, settle, w.engine.current_plan(), w.report())
        await w.close()
        return out

    step, hold, quiet, plan, cooloff, mono, again, settle, final, report = run_virtual(main)
    assert report["ended"] == "Hubspace answered 403 on account canopy" and report["verdict"] == "fail"
    assert quiet == []  # not one write in the 30 s after the 403: no hold burst, no re-assert
    assert plan.tier is Tier.T3 and plan.program is None and plan.mode_id == "golden-hour"
    assert {d: t.rows for d, t in plan.modules.items()} == step.rows  # the step the canopy shows
    assert step.rows != hold.rows  # (a step that isn't the still look, or this proves nothing)
    for (
        device_id,
        target,
    ) in plan.modules.items():  # one colour per module: that step's, for the kiosk preview
        assert target.colors and {json.dumps(c, sort_keys=True) for c in target.colors} == {
            json.dumps(
                {k: int(step.colours[pos][1 + 2 * i : 3 + 2 * i], 16) for i, k in enumerate("rgb")},
                sort_keys=True,
            )
            for pos in [device_id.removeprefix("canopy-")]
        }
    assert 90 - 31 <= cooloff["canopy"] - mono <= 90 - 29  # governor_cooloff_s from the 403
    assert again.startswith("refused: Hubspace throttled (429/403) a stepped run on account canopy")
    # after the cool-off: the still design, sent only to the modules whose step differs from it
    assert (
        final.tier is Tier.T3
        and final.program is None
        and {d: t.rows for d, t in final.modules.items()} == hold.rows
    )
    assert 0 < len(settle) == sum(step.rows[d] != hold.rows[d] for d in hold.rows)


def test_a_governor_trip_ends_it_and_the_report_says_so():
    async def main():
        storm = SimSettings.from_acceptance().with_(throttle_429_rate=0.35, silent_drop_rate=0.0)
        w = await show(sim=storm, per_light=False)
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
        w = await show(per_light=False)
        await w.engine.rehearse("golden-hour", 3, source="ops")
        await w.run_for(16)  # the hold and steps 1 and 2 (7.5 s apart)
        await w.engine.request_mode("goodnight-glow", source="ops")  # another mood
        await w.ended()
        out = (w.report(), w.engine.current_plan(), w.engine.rehearsal_mode)
        await w.close()
        return out

    report, plan, mode = run_virtual(main)
    assert report["ended"] == "ops chose goodnight-glow" and not report["completed"]
    assert report["verdict"] == "fail" and report["attempts"] >= 9 * 3
    assert plan.mode_id == "goodnight-glow" and mode is None
    # on purpose: in sim an ops choice keeps the ladder order, so The Goodnight Glow #1 runs its own breath (D77)
    assert plan.tier is Tier.STEPPED and plan.program.moving_feature == engine_moving("goodnight-glow")


def test_ops_can_stop_it_and_a_pause_stops_it():
    async def main():
        w = await show(per_light=False)
        await w.engine.rehearse("golden-hour", 3, source="ops")
        await w.run_for(16)  # steps 1 and 2 (7.5 s apart) are out
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
        w = await show(per_light=False)
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
    assert report["completed"] and report["steps"]["planned"] == 32 and report["run_s"] == 240.0


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


def test_the_idle_look_can_be_rehearsed_with_its_own_rung(monkeypatch):
    """D75: the idle look may be rehearsed (its refusal is gone). Lowcountry Calm #1 has its own breath since D77, so
    it rehearses that rung, with the copy guests would see; the live gate opens for that mode only, while it runs."""

    async def main():
        w = await show(live=True, monkeypatch=monkeypatch, stepped_live=False)  # before Gate R
        preview = w.engine.rehearsal_preview("lowcountry-calm", 2)
        plan = w.wiring.rehearsal_plan("lowcountry-calm")
        result = await w.engine.rehearse("lowcountry-calm", 2, source="ops")
        gate = w.wiring.tier_allowed
        during = (
            gate("lowcountry-calm", Tier.STEPPED),
            gate("golden-hour", Tier.STEPPED),
            w.engine.current_plan().tier,
            w.engine.rehearsal_mode,
        )
        w.engine.stop_rehearsal(source="ops")
        await w.ended()
        after = gate("lowcountry-calm", Tier.STEPPED)
        await w.close()
        return preview, plan, result, during, after

    preview, plan, result, during, after = run_virtual(main)
    assert preview["mode"] == "lowcountry-calm" and result["ok"], result
    assert plan.program.moving_feature == engine_moving("lowcountry-calm")
    assert plan.program.mode == "breathe"
    assert during == ((True, ""), (False, STEPPED_LIVE_OFF), Tier.STEPPED, "lowcountry-calm")
    assert after == (False, STEPPED_LIVE_OFF)


# ---- refusals ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("mode", "minutes", "setup", "says"),
    [
        ("golden-hour", 0, None, "1 to 10 whole minutes"),
        ("golden-hour", MAX_MINUTES + 1, None, "1 to 10 whole minutes"),
        ("golden-hour", True, None, "1 to 10 whole minutes"),
        ("golden-hour", 2, "paused", "Resume guest input first"),
        ("golden-hour", 2, "running", "already running"),
        ("golden-hour", 2, "outage", "Hubspace isn't answering"),
        ("golden-hour", 2, "cooloff", "Hubspace throttled (429/403) a stepped run on account canopy"),
        ("no-such-mode", 2, None, "can't be rehearsed stepped"),
        ("golden-hour", 2, "stopped", "engine is stopped"),
        ("golden-hour", 2, "commissioning", "Commissioning is writing"),
        ("golden-hour", 2, "flat", "has no stepped design to rehearse"),
        ("golden-hour", 2, "gate", "would show canvas, not a stepped look"),
        # (a mode without its own stepped rung: D77 gives every scene one, so the setup takes Garden's off first)
        ("garden-gathering", 2, "no-sample", "no module-level design sampled at the module centres"),
        ("garden-gathering", 2, "mean", "no module-level design sampled at the module centres"),
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
            gh = raw["modes"][mode]
            gh["ladder"] = [t for t in gh["ladder"] if t != "stepped"]  # its own rung off (D77 gives it one)
            gh["techniques"].pop("stepped", None)
            gh.pop("motion", None)
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
        w = await show(per_light=False)
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
    assert (
        first["ended"] == f"{SCENE_NAME['lowcountry-calm']} (strand) replaced it (ops)"
        and not first["completed"]
    )
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
        w = await show(per_light=False)
        await w.engine.rehearse("golden-hour", 2, source="ops")
        await w.run_for(5)
        t = w.services.telemetry
        t.record("attempt", device_id="sim-bench-kit", n=1, outcome="ok", http_status=200, elapsed_s=0.0)
        w.engine.enter_outage("test")
        w.engine.exit_outage("test")
        await w.run_for(62)
        # 11 throttled among about 200 attempts (over 5 %), late, so the governor's 60 s window holds ~90 (≈ 11 %)
        for _ in range(11):
            t.record("attempt", device_id="sim-canopy-r1c1", n=2, outcome="throttled", http_status=429)
        # answered 4 s after it was sent, 58 s after the first: both within one minute on the bench account
        t.record("attempt", device_id="sim-bench-kit", n=1, outcome="ok", http_status=200, elapsed_s=4.0)
        await w.run_for(60)
        await w.ended()
        out = w.report()
        await w.close()
        return out

    report = run_virtual(main)
    assert report["throttled_429"] == 11 and report["throttled_share"] >= 0.05
    assert [c["id"] for c in report["criteria"] if not c["ok"]] == ["throttled_share"]
    assert report["max_writes_60s"]["bench"] == 2 and report["outages"] == 1


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


def test_the_runbook_says_which_feature_line_the_kiosk_shows_during_a_rehearsal():
    """Step 5 of docs/runbooks/stepped-rehearsal.md tells the observer what the kiosk reads: a scene with its own
    stepped rung (every scene and option since D77) rehearses with its guest-facing moving copy; only a look without
    one gets REHEARSAL_FEATURE."""
    from conductor.looks import load_modes

    text = (Path(__file__).resolve().parent.parent / "docs" / "runbooks" / "stepped-rehearsal.md").read_text()
    step5 = " ".join(text.split("\n5. ", 1)[1].split("\n\n", 1)[0].split())
    stepping = {i: m for i, m in load_modes(CONFIG_DIR).modes.items() if m.techniques.stepped is not None}
    assert len(stepping) == 36 + 6  # D77 + D80, plus the Light Show scene's six (Oct 21)
    for prefix in {m.techniques.stepped.moving_feature.split(", so ")[0] for m in stepping.values()}:
        assert prefix in step5  # every kind of sentence a rehearsal can show
    for scene in load_scenes(CONFIG_DIR).scenes:
        if scene.option_of is None:  # the six scenes by name; their options are "#2" and "#3"
            assert scene.name.removesuffix(" #1") in step5, scene.name
    staff = REHEARSAL_FEATURE.split(", so ")[0]
    assert f'without a stepped rung of its own shows "{staff}' in step5
