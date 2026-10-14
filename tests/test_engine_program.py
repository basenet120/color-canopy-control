"""Stepped looks in motion (Tier.STEPPED, D65; conductor/sequence.py): the program runner on the real engine,
reconciler, executor and SimDriver (virtual time, the pinned acceptance latencies), with the real looks compiler over
tests/fixtures_modes_stepped.yaml plus a still idle mode. Sim-first: the stepped tier is off on live hardware."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fake_compiler import make_world, settings_with

from conductor import canopy_view
from conductor import rows as R
from conductor.capabilities import detect
from conductor.clock import run_virtual
from conductor.config import CONFIG_DIR, ConductorSettings, ConfigError, load_recipes, load_settings
from conductor.engine_api import CanopyPhase, Tier
from conductor.executor_api import CommandStatus
from conductor.geometry import load_canopy_config
from conductor.looks import CompileContext, compile_mode, ladder, parse_modes
from conductor.sequence import GRID
from conductor.show import STEPPED_LIVE_OFF, ShowWiring, _check_stepped
from conductor.sim.profiles import profile_parts
from conductor.sim.settings import SimSettings

PATH = Path(__file__).with_name("fixtures_modes_stepped.yaml")
CALM = {
    "label": "Lowcountry Calm",
    "blurb": "The resting look",
    "preview": ["#F7F2E7"],
    "ladder": ["strand"],
    "techniques": {
        "strand": {
            "look": {"kind": "rgb", "rgb": [247, 242, 231], "brightness": 70},
            "feature": "Colour and dimming: all nine Hubspace strands set to one soft ivory.",
        }
    },
}


def stepped_modes():
    raw = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    raw["modes"]["lowcountry-calm"] = CALM  # a still idle mode (the idle mode never steps)
    return parse_modes(raw, recipes=load_recipes(), canopy=load_canopy_config(), path=PATH)


MODES = stepped_modes()
CANOPY_CFG = canopy_view.load_canopy(str(CONFIG_DIR))
CAPS = detect(*profile_parts("sqhdk03-sim"))
ROW_KEYS = [
    ("power", None),
    ("color-mode", None),
    ("color-rgb", None),
    ("brightness", None),
    ("speed", "color-sequence"),
    ("toggle", "party-mode"),
]
QUIET_S = 65.0  # long enough after start-up that its burst is out of the 60 s rate window


def pinned(**faults) -> SimSettings:
    """The pinned acceptance latencies (07 §7), with the random faults set here (none unless given)."""
    return SimSettings.from_acceptance().with_(
        **{"throttle_429_rate": 0.0, "silent_drop_rate": 0.0, **faults}
    )


# The mechanics tests step at 6 s: an account cap of 120 less a 25 % reserve plans 90 writes a minute, so 9 modules
# step every 6 s and their timings read in whole steps. The budget tests pass `shipped=True`: the shipped
# motion.stepped (a cap of 90 less 20 % plans 72 a minute → 7.5 s steps).
SIX_S = {"account_writes_per_min": 120, "budget_reserve": 0.25}


def stepped_world(*, sim=None, tier_allowed=None, seed: int = 0, shipped: bool = False, **sections):
    if not shipped:
        motion = dict(sections.get("motion", {}))
        motion["stepped"] = {**SIX_S, **motion.get("stepped", {})}
        sections["motion"] = motion
    settings = settings_with(**sections) if sections else ConductorSettings()
    w = make_world(sim_settings=sim or pinned(), settings=settings, tier_allowed=tier_allowed, seed=seed)
    ctx = CompileContext(
        registry=w.registry,
        capabilities={e.id: CAPS for e in w.registry.canopy_modules()},
        modes=MODES,
        recipes=load_recipes(),
        canopy=load_canopy_config(),
    )
    w.engine.deps.compile = lambda mode_id, tier: compile_mode(mode_id, tier, ctx)
    w.engine.deps.ladder = lambda mode_id: ladder(mode_id, MODES)
    w.engine.deps.modes = lambda: list(MODES.modes)
    writes: list[tuple[float, str, list]] = []
    original = w.sim.write

    async def timed_write(device_id, rows, *, purpose):
        writes.append((w.clock.monotonic(), device_id.removeprefix("sim-"), list(rows)))
        return await original(device_id, rows, purpose=purpose)

    w.sim.write = timed_write
    w.writes = writes
    return w


async def quiet_start(w) -> None:
    await w.engine.start()  # the idle look (Lowcountry Calm), one burst
    await w.run_for(QUIET_S)


def step_rows(plan, n: int) -> dict[str, list]:
    return plan.program.steps[n % len(plan.program.steps)].rows


def shows(w, rows: dict[str, list]) -> bool:
    return all(not R.compare(rows[d], w.sim.device_view(w.address(d))) for d in rows)


def labelled(w, prefix: str, since: float = 0.0) -> list[dict]:
    return [c for c in w.events("command") if c["label"].startswith(prefix) and c["t"] >= since]


# ---- the step and the budget ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("cap", "reserve", "step", "extra"),
    [
        (90, 0.2, 7.5, {}),  # the shipped budget: 9 × 60 / 72
        (90, 0.25, 8.0, {}),  # 67.5 planned
        (120, 0.15, 6.0, {}),  # 102 planned: the design's 6 s
        (60, 0.25, 12.0, {}),  # 45 planned: max_step_s exactly
        (120, 0.15, 8.0, {"motion": {"min_step_s": 8.0}}),  # never faster than min_step_s
        (
            120,
            0.15,
            6.4,
            {"kiosk": {"ripple_stagger_s": 1.2}},
        ),  # 3 rings 1.2 s apart: span 2.4 s + min_step_s 4 s
    ],
)
def test_the_step_is_the_designs_unless_the_budget_or_the_ripple_needs_longer(cap, reserve, step, extra):
    async def main():
        w = stepped_world(
            shipped=True,
            motion={
                "stepped": {
                    "account_writes_per_min": cap,
                    "budget_reserve": reserve,
                    **extra.get("motion", {}),
                }
            },
            kiosk=extra.get("kiosk", {}),
        )
        await w.engine.request_mode("linear-columns", source="ops")
        runner = w.engine.runner
        out = (runner.step_s, runner.running, runner.count)
        await w.close()
        return out

    got, running, count = run_virtual(main)
    assert got == step and running
    assert count == int(90 / step + 1e-6)  # the hold goes out at the last step boundary within the 90 s run


def test_a_step_longer_than_max_step_s_holds_the_still_design_and_says_why():
    async def main():
        # 9 × 60 / (40 less 20 %) = 16.875 s > 12 s
        w = stepped_world(shipped=True, motion={"stepped": {"account_writes_per_min": 40}})
        await quiet_start(w)
        start = w.clock.monotonic()
        state = await w.engine.request_mode("linear-columns", source="ops")
        await w.run_for(30)
        out = {
            "state": state,
            "later": w.engine.state(),
            "runner": w.engine.runner,
            "plan": w.engine.current_plan(),
            "writes": [x for x in w.writes if x[0] >= start],
            "alerts": [a["message"] for a in w.events("alert")],
            "correct": w.all_correct(),
        }
        await w.close()
        return out

    out = run_virtual(main)
    assert out["runner"].running is False and "max_step_s" in out["runner"].ended
    assert any(
        "holds its still design" in a and "16.875 s" in a and "20% reserve" in a for a in out["alerts"]
    )
    assert out["state"].feature == out["plan"].feature and out["state"].program is None  # no moving claim
    assert len(out["writes"]) == 9 and out["correct"]  # the hold, once, and nothing after
    assert out["later"].tier == "stepped" and out["later"].phase is CanopyPhase.READY


def test_a_90_s_run_steps_below_the_cap_and_ends_on_the_hold():
    async def main():
        w = stepped_world(shipped=True)  # 9 modules at 90 a minute less 20 %: 7.5 s steps
        await quiet_start(w)
        t0 = w.clock.monotonic()
        await w.engine.request_mode("radial-outward", source="ops")
        plan = w.engine.current_plan()
        samples, phases, depth = [], [], 0
        while w.clock.monotonic() < t0 + 100:
            await w.run_for(0.5)
            budget = w.executor.budget()
            samples.append(budget)
            depth = max(
                depth,
                max((d["pending"] + d["in_flight"] for d in budget["devices"].values()), default=0),
            )
            phases.append((round(w.clock.monotonic() - t0, 1), w.engine.state()))
        out = {
            "plan": plan,
            "t0": t0,
            "samples": samples,
            "phases": phases,
            "depth": depth,
            "writes": [(t - t0, d, rows) for t, d, rows in w.writes if t >= t0],
            "commands": [c for c in w.events("command") if c["source"] == "program"],
            "runner": w.engine.runner,
            "final": w.engine.state(),
            "correct": w.all_correct(),
        }
        await w.close()
        return out

    out = run_virtual(main)
    plan, runner, writes = out["plan"], out["runner"], out["writes"]
    step = 7.5
    assert runner.step_s == step and runner.count == 12 and runner.state == "done" and runner.skipped == 0
    assert runner.budget_holds == 0  # planned below the cap, so the cap never had to hold a step back
    # the budget: ≤ 9 writes a minute per device, ≤ 81 on the canopy account (8 steps in any 60 s and part of a
    # ninth: the cap of 90 less the reserve), one command per device at a time
    assert max(s["accounts"]["canopy"]["writes_60s"] for s in out["samples"]) <= 81
    assert max(d["writes_60s"] for s in out["samples"] for d in s["devices"].values()) <= 9
    assert out["depth"] <= 1
    # the mode change (the hold, step 0) then 12 × 9 PUTs: 11 steps and the hold at 90 s
    after_mode = [x for x in writes if x[0] > 1.0]
    assert len(writes) - len(after_mode) == 9
    assert len(after_mode) == 12 * 9  # every module's colour changes at every step of this design
    # each step: exactly one command per changed driven module, one whole state vector each (never split)
    labels: dict[str, list[str]] = {}
    for c in out["commands"]:
        labels.setdefault(c["label"], []).append(c["device_id"])
    assert len(labels) == 12
    assert all(sorted(ids) == sorted(f"sim-{d}" for d in plan.modules) for ids in labels.values())
    assert all([r.key for r in rows] == ROW_KEYS for _, _, rows in writes)
    assert all(c["status"] == str(CommandStatus.APPLIED) for c in out["commands"])
    # on the absolute schedule: step n's writes go out at t0 + n × step (within the executor's own scheduling)
    for n in range(1, 13):
        times = [t for t, _, _ in after_mode if n * step - 1e-6 <= t < n * step + 1.0]
        assert len(times) == 9, n
    # the run ends on the hold rows with the hold's sentence and no motion
    assert out["correct"] and shows_rows_equal(plan)
    final = out["final"]
    assert final.feature == plan.feature and final.program is None and final.phase is CanopyPhase.READY
    assert final.feature != plan.program.moving_feature
    # phase: UPDATING on arrival, then READY for the rest of the run (no flicker each step)
    run = [(t, s) for t, s in out["phases"] if t < 12 * step]
    first_ready = next(i for i, (_, s) in enumerate(run) if s.look_phase is CanopyPhase.READY)
    assert all(s.look_phase is CanopyPhase.UPDATING for _, s in run[:first_ready])
    assert run[first_ready][0] <= 4.0
    assert all(s.look_phase is CanopyPhase.READY for _, s in run[first_ready:])
    assert all(s.phase is CanopyPhase.READY for _, s in run[first_ready:])
    # while it moves: the moving sentence and what each step shows
    moving = run[first_ready][1]
    assert moving.feature == plan.program.moving_feature
    assert moving.program is not None and moving.program["step_s"] == step


@pytest.mark.parametrize("seed", range(8))
def test_under_the_pinned_faults_a_run_never_takes_the_account_past_the_cap(seed):
    """The pinned acceptance conditions (07 §7: 5 % 429s, 1 % silent drops) on top of the shipped budget: the 429
    retries and the silent-drop re-sends fit in the reserve, so no 60 s on the account passes the cap of 90 — by the
    executor's own count and by send time."""

    async def main():
        w = stepped_world(sim=SimSettings.from_acceptance(), seed=seed, shipped=True)
        await quiet_start(w)
        t0 = w.clock.monotonic()
        await w.engine.request_mode("radial-outward", source="ops")
        runner, most = w.engine.runner, 0
        while w.clock.monotonic() < t0 + 100:
            await w.run_for(0.5)
            most = max(most, w.executor.budget()["accounts"]["canopy"]["writes_60s"])
        sends = sorted(t for t, _, _ in w.writes if t >= t0)
        window = max(sum(1 for t in sends if a <= t < a + 60) for a in sends)
        throttled = sum(1 for e in w.events("attempt") if e.get("http_status") == 429)
        await w.close()
        return most, window, runner, throttled

    most, window, runner, throttled = run_virtual(main)
    assert most <= 90 and window <= 90, (most, window)
    assert throttled > 0  # the faults really were on
    assert runner.count == 12 and runner.ended == "the run is over"


def test_a_step_that_would_take_the_account_past_the_cap_is_held_back():
    """Writes from elsewhere on the account (a vignette, a mood chosen mid-run) leave too little room: the step is
    held back — nothing is sent and the step shown stays the last one sent — and the next step goes out once there
    is room again."""

    async def main():
        w = stepped_world(shipped=True)
        await quiet_start(w)
        t0 = w.clock.monotonic()
        await w.engine.request_mode("linear-columns", source="ops")
        runner = w.engine.runner
        await w.run_for(runner.next_step_in() + 0.5)  # step 1 is out
        sent_before = len(w.writes)
        budget = w.executor.budget
        crowd = {"extra": 75}  # 75 writes by others on the account in the last 60 s: 9 + 9 + 75 > 90

        def crowded(address=None):
            out = budget(address)
            if address is not None:
                out = {**out, "account_writes_60s": out["account_writes_60s"] + crowd["extra"]}
            return out

        w.executor.budget = crowded
        shown = runner.shown
        await w.run_for(runner.next_step_in() + 0.5)  # step 2 comes due
        held = (len(w.writes) - sent_before, runner.index, runner.shown, runner.budget_holds)
        state = w.engine.state()
        crowd["extra"] = 0
        await w.run_for(runner.next_step_in() + 0.5)  # step 3: room again
        after = (runner.shown, runner.budget_holds, len(w.writes) - sent_before)
        holds = [e for e in w.events("program") if e.get("action") == "budget_hold"]
        await w.close()
        return shown, held, state, after, holds, t0

    shown, held, state, after, holds, _ = run_virtual(main)
    assert shown == 1
    assert held == (0, 2, 1, 1)  # nothing sent; the index advanced, the step shown is still step 1
    assert state.program is not None and state.program["step"] == 2
    assert after[:2] == (3, 1) and after[2] == 9  # step 3 went out to every module
    [hold] = holds
    assert hold["account"] == "canopy" and hold["step"] == 2 and hold["cap"] == 90
    assert hold["writes_60s"] + hold["step_writes"] > 90


def shows_rows_equal(plan) -> bool:
    return all(plan.modules[d].rows == plan.program.steps[0].rows[d] for d in plan.modules)


def test_the_state_says_what_each_step_shows_in_grid_order():
    async def main():
        w = stepped_world()
        await w.engine.request_mode("linear-columns", source="ops")
        await w.run_for(13)
        out = (w.engine.state(), w.engine.current_plan(), w.engine.runner.t0_wall)
        await w.close()
        return out

    state, plan, t0_wall = run_virtual(main)
    program = state.program
    assert program["step"] == 2 and program["step_s"] == 6.0 and program["t0"] == t0_wall
    assert program["ends_at"] == pytest.approx(t0_wall + 90.0)
    frames = program["frames"]
    assert len(frames) == 16 and all(len(f) == 9 for f in frames)
    for n, frame in enumerate(frames):
        step = plan.program.steps[0 if n == 15 else n % 6]
        assert frame == [step.colours[pos] for pos in GRID]  # the last frame is the hold
    assert frames[0] == frames[15] and frames[1] != frames[0]
    assert state.to_json()["program"] == program


def test_a_run_shorter_than_the_step_the_budget_needs_holds_still():
    raw = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    raw["modes"]["lowcountry-calm"] = CALM
    raw["modes"]["linear-rows"]["techniques"]["stepped"].update(step_s=6, run_s=6)
    modes = parse_modes(raw, recipes=load_recipes(), canopy=load_canopy_config(), path=PATH)

    async def main():
        w = stepped_world(motion={"stepped": {"account_writes_per_min": 90}})  # 90 less 25 % → 8 s steps
        ctx = CompileContext(
            registry=w.registry,
            capabilities={e.id: CAPS for e in w.registry.canopy_modules()},
            modes=modes,
            recipes=load_recipes(),
            canopy=load_canopy_config(),
        )
        w.engine.deps.compile = lambda mode_id, tier: compile_mode(mode_id, tier, ctx)
        state = await w.engine.request_mode("linear-rows", source="ops")
        out = (w.engine.runner.running, w.engine.runner.ended, state.program)
        await w.close()
        return out

    running, ended, program = run_virtual(main)
    assert not running and "shorter than one 8 s step" in ended and program is None


def test_stopping_the_engine_stops_the_run():
    async def main():
        w = stepped_world()
        await w.engine.request_mode("linear-rows", source="ops")
        runner = w.engine.runner
        await w.run_for(7)
        await w.engine.stop()
        n = len(w.writes)
        await w.run_for(20)
        out = (runner.running, runner._task, len(w.writes) - n)
        await w.executor.close()
        return out

    running, task, after = run_virtual(main)
    assert not running and task is None and after == 0


# ---- timing -------------------------------------------------------------------------------------------------------
def test_a_late_step_is_skipped_never_queued():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        t0 = w.clock.monotonic()
        await w.engine.request_mode("linear-rows", source="ops")
        runner = w.engine.runner
        sleep, late = runner._sleep, {"once": True}

        async def oversleep(seconds):  # the wait for step 3 wakes 13 s late (a stalled box)
            stall = 13.0 if w.clock.monotonic() > t0 + 7 and late.pop("once", False) else 0.0
            await sleep(seconds + stall)

        runner._sleep = oversleep
        await w.run_for(40)
        out = {
            "runner": runner,
            "labels": sorted(
                {c["label"] for c in w.events("command") if c["source"] == "program"},
                key=lambda label: int(label.split()[1]),
            ),
        }
        await w.close()
        return out

    out = run_virtual(main)
    assert out["runner"].skipped == 2
    steps = [int(label.split()[1]) for label in out["labels"]]
    # due at 18 s, woken at 31 s: steps 3 and 4 are skipped and step 5 goes out alone, on the schedule's time
    assert steps[:4] == [1, 2, 5, 6] and steps == sorted(set(steps))


def test_the_idle_hold_still_returns_to_the_idle_mode_after_idle_return_s():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("radial-outward", source="kiosk")
        await w.run_for(179)
        before = w.engine.current_plan().mode_id
        await w.run_for(2.5)
        after = w.engine.current_plan().mode_id
        await w.close()
        return before, after

    before, after = run_virtual(main)
    assert (before, after) == ("radial-outward", "lowcountry-calm")


def test_each_steps_silent_drop_gets_its_own_early_re_send():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        t0 = w.clock.monotonic()
        await w.engine.request_mode("linear-columns", source="ops")
        address = w.address("canopy-r1c1")
        original = w.sim.cloud.accept_wire
        dropped: list[float] = []

        def drop(body, *, silent_drop=False):  # each step's write to r1c1 is answered 200 and not stored
            t = w.clock.monotonic() - t0
            if body.get("metadeviceId") == address and t > 3 and (t % 6.0) < 0.9:
                dropped.append(t)
                silent_drop = True
            return original(body, silent_drop=silent_drop)

        w.sim.cloud.accept_wire = drop
        await w.run_for(29)
        resends = sorted(t - t0 for t, d, _ in w.writes if d == "canopy-r1c1" and t > t0 + 3)
        await w.close()
        return dropped, resends

    dropped, resends = run_virtual(main)
    assert len(dropped) == 4  # steps 1–4
    for t in dropped:  # each drop is re-sent at its first verification readback, not after three
        assert any(t < r <= t + 2.6 for r in resends), (t, resends)


# ---- pauses and folding -------------------------------------------------------------------------------------------
def test_an_outage_holds_the_steps_and_recovery_sends_the_step_the_run_is_at():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="ops")
        await w.run_for(14)  # step 2 is out
        w.sim.faults.set_outage(True)
        w.engine.enter_outage("test outage")
        n = len(w.writes)
        await w.run_for(20)  # steps 3–5 fall due while the probes fail
        index, state, during = w.engine.runner.index, w.engine.state(), w.writes[n:]
        w.sim.faults.set_outage(False)
        await w.until(lambda: w.engine.outage is None, timeout=15, step=0.05)
        recovered_at = w.engine.runner.index
        await w.run_for(0.5)
        burst = {d: rows for _, d, rows in w.writes[n + len(during) :]}
        out = {
            "during": during,
            "index": index,
            "state": state,
            "recovered_at": recovered_at,
            "burst": burst,
            "step": step_rows(w.engine.current_plan(), recovered_at),
        }
        await w.close()
        return out

    out = run_virtual(main)
    assert out["during"] == [] and out["index"] == 5  # no writes; the index kept advancing
    assert out["state"].program is None and out["state"].phase is CanopyPhase.RESTING
    assert len(out["burst"]) == 9 and out["recovered_at"] >= 5
    assert out["burst"] == out["step"]  # the step the run was at, not step 2 (the last one sent)


def test_with_the_canopy_off_no_step_writes_and_back_on_at_the_current_step():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="ops")
        await w.run_for(8)
        await w.engine.set_guest_power(False, source="ops")
        await w.run_for(3)
        n = len(w.writes)
        await w.run_for(20)
        during = w.writes[n:]
        off_state = w.engine.state()
        await w.engine.set_guest_power(True, source="ops")
        await w.run_for(3)
        out = {
            "during": during,
            "off": off_state,
            "on": shows(w, step_rows(w.engine.current_plan(), w.engine.runner.index)),
        }
        await w.close()
        return out

    out = run_virtual(main)
    assert out["during"] == []
    assert out["off"].program is None and out["off"].feature != ""
    assert out["on"]


def test_a_guest_brightness_change_just_before_a_step_goes_out_with_it():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="ops")
        await w.run_for(3)
        runner = w.engine.runner
        await w.run_for(runner.next_step_in() + 6.0 - 1.5)  # 1.5 s before step 2
        n = len(w.writes)
        state = await w.engine.set_guest_brightness(40, source="ops")
        await w.run_for(1.4)
        before_step = w.writes[n:]
        await w.run_for(1.5)
        out = {"state": state, "before": before_step, "burst": w.writes[n:], "index": runner.index}
        await w.close()
        return out

    out = run_virtual(main)
    assert out["state"].brightness == 40 and out["before"] == []  # no burst of its own
    burst = out["burst"]
    assert out["index"] == 2 and len(burst) == 9 and len({d for _, d, _ in burst}) == 9  # one PUT per module
    assert all(R.brightness(40) in rows for _, _, rows in burst)


def test_a_guest_brightness_change_mid_step_is_sent_at_once():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="ops")
        await w.run_for(8.5)  # 3.5 s before step 2
        n = len(w.writes)
        await w.engine.set_guest_brightness(40, source="ops")
        await w.run_for(1.0)
        out = w.writes[n:]
        await w.close()
        return out

    burst = run_virtual(main)
    assert len(burst) == 9 and all(R.brightness(40) in rows for _, _, rows in burst)


def test_a_lock_release_sends_the_one_module():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="ops")
        await w.run_for(3)
        address = w.address("canopy-r1c1")
        lease = w.locks.acquire(address, "module page · test")
        await w.run_for(7)  # step 1 skips the locked module
        n = len(w.writes)
        w.locks.release(address, lease.token)
        await w.run_for(1.5)
        out = {
            "released": w.writes[n:],
            "now_showing": shows(
                w, {"canopy-r1c1": step_rows(w.engine.current_plan(), w.engine.runner.index)["canopy-r1c1"]}
            ),
        }
        await w.close()
        return out

    out = run_virtual(main)
    assert [d for _, d, _ in out["released"]] == ["canopy-r1c1"] and out["now_showing"]


def test_a_guest_input_pause_holds_the_steps_and_resume_steps_on():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="ops")
        await w.run_for(8)
        w.engine.pause_guest_input(True, source="ops")
        n = len(w.writes)
        await w.run_for(20)
        during, index = w.writes[n:], w.engine.runner.index
        w.engine.pause_guest_input(False, source="ops")
        await w.run_for(w.engine.runner.next_step_in() + 0.5)
        resumed = w.writes[n:]
        out = (during, index, resumed, w.engine.runner.index)
        await w.close()
        return out

    during, index, resumed, after = run_virtual(main)
    assert during == [] and index == 4  # held, the index advancing
    assert after == 5 and len(resumed) == 9  # the next step goes out on the schedule


def test_a_run_that_ends_during_commissioning_holds_once_it_is_over():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="ops")
        plan = w.engine.current_plan()
        await w.run_for(86)
        w.engine._commissioning = True  # the steps and the end of the run fall inside it
        await w.run_for(8)
        held = shows(w, plan.program.steps[14 % 6].rows)  # still the last step sent
        w.engine._commissioning = False
        await w.run_for(10)  # the reconciler's next re-assert sends the still look
        out = (held, w.engine.runner.running, w.all_correct())
        await w.close()
        return out

    held, running, correct = run_virtual(main)
    assert held and not running and correct


def test_a_module_whose_steps_stop_verifying_needs_attention_after_two_steps():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="ops")
        await w.run_for(5)
        ready = w.engine.state()
        address = w.address("canopy-r2c2")
        original = w.sim.cloud.accept_wire

        def drop(body, *, silent_drop=False):  # the cloud answers 200 and stores nothing for this module
            return original(body, silent_drop=silent_drop or body.get("metadeviceId") == address)

        w.sim.cloud.accept_wire = drop
        phases = []
        for _ in range(40):
            await w.run_for(0.5)
            phases.append(
                (w.clock.monotonic(), w.engine.runner._pending.get("canopy-r2c2"), w.engine.state())
            )
        await w.close()
        return ready, phases, w.engine.stats["tier_changes"]

    ready, phases, tiers = run_virtual(main)
    assert ready.look_phase is CanopyPhase.READY
    late = phases[-1][2]
    assert late.look_phase is CanopyPhase.NEEDS_ATTENTION and late.modules["canopy-r2c2"]["stuck"]
    assert "hasn't been verified" in late.modules["canopy-r2c2"]["reason"]
    assert all(r["status"] == "ready" for d, r in late.modules.items() if d != "canopy-r2c2")
    t, since, _ = next(p for p in phases if p[2].look_phase is CanopyPhase.NEEDS_ATTENTION)
    assert since is not None and 12.0 < t - since <= 12.5  # after 2 steps (12 s) unverified, not before
    assert tiers == [] and late.tier == "stepped"


def test_a_module_whose_step_writes_are_refused_is_flagged_without_pulling_the_look_down_a_tier():
    from conductor.drivers.base import Outcome, WriteResult

    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="ops")
        await w.run_for(5)
        address = w.address("canopy-r2c2")
        original = w.sim.write

        async def refuse(device_id, rows, *, purpose):  # HTTP 400 for this module, every time
            if device_id == address:
                return WriteResult(device_id, Outcome.CLIENT_ERROR, sent_at=w.clock.now(), http_status=400)
            return await original(device_id, rows, purpose=purpose)

        w.sim.write = refuse
        await w.run_for(30)
        out = (w.engine.state(), w.engine.stats["tier_changes"])
        await w.close()
        return out

    state, tiers = run_virtual(main)
    # Each step is a new desired state, so the hysteresis timer starts over at every step: a module that won't take
    # its steps is flagged, not a reason to pull the whole look down a tier every 10 s (the still look is the same
    # kind of write, so it wouldn't take either).
    assert tiers == [] and state.tier == "stepped"
    assert state.look_phase is CanopyPhase.NEEDS_ATTENTION and state.modules["canopy-r2c2"]["stuck"]


# ---- the governor -------------------------------------------------------------------------------------------------
def test_a_throttle_storm_trips_the_governor_holds_the_still_look_and_cools_off():
    async def main():
        w = stepped_world(sim=pinned(throttle_429_rate=0.30), seed=3)
        await quiet_start(w)
        t0 = w.clock.monotonic()
        await w.engine.request_mode("radial-outward", source="ops")
        runner = w.engine.runner
        tripped = await w.until(lambda: w.engine.stats["governor_trips"] > 0, timeout=60)
        await w.run_for(20)
        plan = w.engine.current_plan()
        out = {
            "tripped": tripped,
            "runner": runner,
            "plan": plan,
            "state": w.engine.state(),
            "alerts": [a for a in w.events("alert") if a.get("source") == "program"],
            "cooloff": dict(w.engine.stepped_cooloff),
            "now": w.clock.monotonic(),
            "hold_sent": labelled(w, "hold"),
            "t0": t0,
            "governor": w.events("governor"),
        }
        # during the cool-off another stepped mood holds still
        await w.engine.request_mode("linear-columns", source="ops")
        out["again"] = w.engine.runner
        out["again_state"] = w.engine.state()
        await w.close()
        return out

    out = run_virtual(main)
    assert out["tripped"] is not None and out["tripped"] < 60
    assert out["runner"].running is False and "governor" in out["runner"].ended
    assert (
        out["plan"].tier is Tier.STEPPED and out["runner"].shown is None
    )  # the still look is the desired one
    assert out["hold_sent"]  # sent once, to the modules not already showing it
    assert out["state"].program is None and out["state"].feature == out["plan"].feature
    [alert] = out["alerts"]
    assert alert["level"] == "warn" and "throttled" in alert["message"] and "5 min" in alert["message"]
    [event] = out["governor"]
    assert event["throttled"] >= 8 and event["throttled"] / event["attempts"] >= 0.25
    assert out["cooloff"]["canopy"] - out["now"] > 200  # 300 s from the trip
    assert out["again"].running is False and "governor" in out["again"].ended
    assert out["again_state"].program is None


def test_the_governor_counts_a_sliding_window_of_429_and_403_attempts_on_the_runs_modules():
    async def main():
        w = stepped_world(motion={"stepped": {"governor_window_s": 10}})
        await w.engine.request_mode("linear-rows", source="ops")
        await w.run_for(3)
        runner, telemetry = w.engine.runner, w.telemetry

        def attempts(n, outcome="ok", status=200, device="sim-canopy-r0c0"):
            for _ in range(n):
                telemetry.record("attempt", device_id=device, outcome=outcome, http_status=status)

        attempts(40)  # a clean stretch …
        await w.run_for(11)  # … that slides out of the 10 s window
        attempts(8, "throttled", 503)  # an outage signature, not throttling
        attempts(8, "throttled", 429, device="sim-not-a-module")  # not the run's
        await w.run_for(0.1)
        quiet = (runner.running, w.engine.stats["governor_trips"])
        attempts(6, "throttled", 429)
        attempts(6, "throttled", 403)
        await w.run_for(0.1)
        out = (quiet, runner.running, w.engine.stats["governor_trips"], runner.ended)
        await w.close()
        return out

    quiet, running, trips, ended = run_virtual(main)
    assert quiet == (True, 0)
    assert (
        not running and trips == 1 and "governor" in ended
    )  # 8 of 16 in the window (the 503s count as attempts)


def test_five_percent_random_429s_for_an_hour_trip_nothing_and_downgrade_nothing():
    async def main():
        w = stepped_world(sim=pinned(throttle_429_rate=0.05), seed=11)
        await quiet_start(w)
        start = w.clock.monotonic()
        runs = []
        while w.clock.monotonic() - start < 3600:
            await w.engine.request_mode(
                ["radial-outward", "linear-columns", "linear-rows"][len(runs) % 3], source="ops"
            )
            runs.append(w.engine.runner)
            await w.run_for(120)
        throttled = [a for a in w.events("attempt") if a["outcome"] == "throttled"]
        out = {
            "trips": w.engine.stats["governor_trips"],
            "tier_changes": w.engine.stats["tier_changes"],
            "runs": runs,
            "throttled": len(throttled),
        }
        await w.close()
        return out

    out = run_virtual(main)
    assert out["throttled"] > 50  # the 429s really happened
    assert out["trips"] == 0 and out["tier_changes"] == []
    assert all(r.ended == "the run is over" for r in out["runs"])


def test_commands_still_throttled_after_retries_downgrade_a_stepped_look_to_its_still_rung():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("radial-outward", source="ops")
        await w.run_for(8)
        runner = w.engine.runner
        for _ in range(3):
            w.engine.reconciler.note_throttle_failure("canopy")
        await w.run_for(1)
        out = (w.engine.current_plan(), runner, w.engine.runner, w.engine.state())
        await w.close()
        return out

    plan, old, new, state = run_virtual(main)
    assert plan.tier is Tier.T3 and plan.program is None and new is None
    assert old.running is False  # the run stopped with the look it belonged to
    assert state.program is None and state.feature == plan.feature


def test_a_re_pick_of_the_look_now_showing_keeps_the_runs_phase():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("linear-rows", source="ops")
        first = w.engine.runner
        await w.run_for(15)
        await w.engine.set_guest_brightness(50, source="ops")  # a guest setting is not a new run
        await w.run_for(5)
        before = view(w)
        await w.engine.request_mode("linear-rows", source="reconciler")  # e.g. new capabilities
        second = w.engine.runner
        after = view(w)
        out = (first, second, first.t0, second.t0, second.index, second.shown, first.running, second.running)
        await w.close()
        return out, before, after

    def view(w):
        """What the kiosk is told: the run's wall times (CanopyState.program) and the guest panel's motion."""
        state = w.engine.state()
        lit = canopy_view.lit_state(CANOPY_CFG, w.registry, w.engine.current_plan(), state, now=w.clock.now())
        return state.program["t0"], state.program["ends_at"], state.program["step"], lit["motion"]["t0"]

    out, before, after = run_virtual(main)
    first, second, t0a, t0b, index, shown, first_running, second_running = out
    assert first is not second and not first_running and second_running
    assert t0a == t0b and index == 3 and shown == 3
    # the kiosk draws the frames the canopy is sent: the run's wall start and end don't move with the re-pick
    assert before == after and before[2] == 3


# ---- gates and config ---------------------------------------------------------------------------------------------
def _wiring(*, simulated: bool, live_enabled: bool = False, rehearsal: bool = False) -> ShowWiring:
    wiring = object.__new__(ShowWiring)  # tier_allowed needs only services, engine and the commissioning flag
    wiring.services = SimpleNamespace(
        engine_driver=SimpleNamespace(simulated=simulated),
        settings=settings_with(motion={"stepped": {"live_enabled": live_enabled}}),
    )
    wiring.engine = SimpleNamespace(rehearsal_mode="radial-outward" if rehearsal else None)
    wiring.commissioning = False
    return wiring


def test_the_stepped_tier_is_off_on_live_hardware_until_gate_r():
    assert _wiring(simulated=False).tier_allowed("radial-outward", Tier.STEPPED) == (False, STEPPED_LIVE_OFF)
    assert _wiring(simulated=True).tier_allowed("radial-outward", Tier.STEPPED) == (True, "")
    assert _wiring(simulated=False, live_enabled=True).tier_allowed("radial-outward", Tier.STEPPED)[0]
    assert _wiring(simulated=False, rehearsal=True).tier_allowed("radial-outward", Tier.STEPPED)[0]
    assert _wiring(simulated=False).tier_allowed("radial-outward", Tier.T3) == (True, "")

    async def main():
        w = stepped_world(tier_allowed=_wiring(simulated=False).tier_allowed)
        state = await w.engine.request_mode("radial-outward", source="ops")
        out = (state, w.engine.runner, w.engine.current_plan())
        await w.close()
        return out

    state, runner, plan = run_virtual(main)
    assert plan.tier is Tier.T3 and state.tier == "modules" and runner is None  # the ladder lands on modules


def test_the_write_budget_can_not_be_set_below_one_second(tmp_path):
    (tmp_path / "conductor.yaml").write_text("executor: {min_write_interval_s: 0.5}\n")
    with pytest.raises(ConfigError, match="min_write_interval_s"):
        load_settings(tmp_path)


def test_motion_stepped_settings_load_with_the_lead_defaults_and_refuse_nonsense(tmp_path):
    stepped = load_settings().motion.stepped
    assert stepped.live_enabled is False
    assert (stepped.min_step_s, stepped.max_step_s, stepped.account_writes_per_min) == (4.0, 12.0, 90.0)
    assert (stepped.governor_window_s, stepped.governor_min_throttled, stepped.governor_throttled_share) == (
        60.0,
        8,
        0.25,
    )
    assert (stepped.governor_cooloff_s, stepped.fold_guest_within_s) == (300.0, 2.0)
    for bad in (
        "{min_step_s: 3}",
        "{min_step_s: 8, max_step_s: 6}",
        "{fold_guest_within_s: 4}",  # not shorter than min_step_s (4)
        "{governor_throttled_share: 0}",
    ):
        (tmp_path / "conductor.yaml").write_text(f"motion: {{stepped: {bad}}}\n")
        with pytest.raises(ConfigError):
            load_settings(tmp_path)


def test_a_run_must_end_before_the_idle_hold_and_the_idle_mode_never_steps():
    _check_stepped(ConductorSettings(), MODES)  # 90 s runs, idle_return_s 180, a still idle mode
    short = settings_with(kiosk={"idle_return_s": 60})
    with pytest.raises(ConfigError, match="run_s: 90 s is longer than kiosk.idle_return_s"):
        _check_stepped(short, MODES)
    stepping_idle = settings_with(kiosk={"idle_mode": "radial-outward"})
    with pytest.raises(ConfigError, match="idle mode .* can't have a stepped rung"):
        _check_stepped(stepping_idle, MODES)


def test_the_show_checks_stepped_runs_against_the_kiosk_settings_when_it_loads(tmp_path):
    import shutil

    from conductor.config import CONFIG_DIR

    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    raw = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    raw["modes"]["lowcountry-calm"] = CALM
    (config / "modes.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    ok = SimpleNamespace(config_dir=config, settings=ConductorSettings())
    assert "radial-outward" in ShowWiring(ok).modes_file.modes
    short = SimpleNamespace(config_dir=config, settings=settings_with(kiosk={"idle_return_s": 60}))
    with pytest.raises(ConfigError, match="idle_return_s"):
        ShowWiring(short)
