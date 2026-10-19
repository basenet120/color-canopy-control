"""Stepped looks in motion (Tier.STEPPED, D65; conductor/sequence.py): the program runner on the real engine,
reconciler, executor and SimDriver (virtual time, the pinned acceptance latencies), with the real looks compiler over
tests/fixtures_modes_stepped.yaml plus a still idle mode. Sim-first: the stepped tier is off on live hardware."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fake_compiler import make_world, settings_with

from conductor import canopy_view
from conductor import rows as R
from conductor.capabilities import detect
from conductor.clock import run_virtual
from conductor.config import (
    CONFIG_DIR,
    ConductorSettings,
    ConfigError,
    RecipesFile,
    load_recipes,
    load_settings,
)
from conductor.engine_api import CanopyPhase, Tier
from conductor.executor_api import CommandStatus
from conductor.geometry import load_canopy_config
from conductor.looks import CompileContext, compile_mode, ladder, parse_modes
from conductor.sequence import GRID
from conductor.show import STEPPED_LIVE_OFF, ShowWiring, _check_stepped
from conductor.sim.profiles import profile_parts
from conductor.sim.settings import SimSettings

PATH = Path(__file__).with_name("fixtures_modes_stepped.yaml")
BREATHE_PATH = Path(__file__).with_name("fixtures_modes_breathe.yaml")  # D75: breathe and shimmer rungs
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


FROZEN = Path(__file__).with_name("fixtures_recipes_frozen.yaml")


def _frozen_recipes() -> RecipesFile:
    """config's recipes with today's scene colours frozen over them (Design Lab PLAN §3.7): the fixture names the
    scenes' recipe keys, so a Design Lab export that renames or drops one can't break these tests."""
    live = load_recipes().model_dump()
    frozen = yaml.safe_load(FROZEN.read_text(encoding="utf-8"))
    return RecipesFile.model_validate({**live, "colors": {**live["colors"], **frozen["colors"]}})


RECIPES = _frozen_recipes()


def stepped_modes():
    raw = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    raw["modes"].update(yaml.safe_load(BREATHE_PATH.read_text(encoding="utf-8"))["modes"])
    raw["modes"]["lowcountry-calm"] = CALM  # a still idle mode
    return parse_modes(raw, recipes=RECIPES, canopy=load_canopy_config(), path=PATH)


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
        recipes=RECIPES,
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
    """Writes from elsewhere on the account (the module page, a mood chosen mid-run) leave too little room: the step is
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
        account_writes = w.executor.account_writes
        crowd = {"extra": 75}  # 75 writes by others on the account in the last 60 s: 9 + 9 + 75 + 9 > 90

        def crowded(address, **kw):
            return account_writes(address, **kw) + crowd["extra"]

        w.executor.account_writes = crowded
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
    assert state.program["frames"][2] == state.program["frames"][1]  # the held step is drawn as held (D75)
    assert after[:2] == (3, 1) and after[2] == 9  # step 3 went out to every module
    [hold] = holds
    assert hold["account"] == "canopy" and hold["step"] == 2 and hold["cap"] == 90
    assert hold["room"] == 9 and hold["counted"] + hold["step_writes"] + hold["room"] > 90


def test_a_run_chosen_mid_run_holds_its_first_step_until_a_guest_has_room_again():
    """B-R1-1 (D75): a guest's mood chosen mid-run runs too, so the account's 60 s window holds the old run's steps,
    the new mood's burst and a guest brightness when the new run's first step comes due. The old rule (the window
    plus the step within the cap) sent it at exactly 90, leaving nothing for a 429 retry or a re-assert; a step now
    keeps one guest burst of room (one write per module) and waits, and the run then goes on to its end."""

    async def main():
        w = stepped_world(shipped=True)  # cap 90 less 20 %: 7.5 s steps on nine
        await quiet_start(w)
        sends: list[tuple[float, str, int]] = []
        original = w.engine.send

        def spy(device_ids, *, source, label):
            sent = original(device_ids, source=source, label=label)
            if source == "program" and label.startswith("step") and label.endswith("linear-rows"):
                sends.append((w.clock.monotonic(), label, len(sent)))
            return sent

        w.engine.send = spy
        t0 = w.clock.monotonic()
        await w.engine.request_mode("linear-columns", source="ops")  # run A
        await w.run_for(61.5)  # A's step 8 went out at t0 + 60
        t_choice = w.clock.monotonic()
        # a guest's mood mid-run: it runs too (D75)
        await w.engine.request_mode("linear-rows", source="kiosk")
        runner = w.engine.runner
        await w.run_for(3.0)
        await w.engine.set_guest_brightness(50, source="kiosk")  # not folded: step 1 is 4.5 s away
        await w.run_for(runner.next_step_in() + 0.5)  # step 1 comes due
        after_1 = (runner.shown, runner.held_steps, runner.running)
        await w.run_for(runner.next_step_in() + 0.5)  # step 2
        after_2 = (runner.shown, runner.held_steps, runner.running)
        await w.run_for(100)
        holds = [e for e in w.events("program") if e.get("action") == "budget_hold"]
        out = (t0, t_choice, after_1, after_2, runner.ended, holds, sends, list(w.writes))
        await w.close()
        return out

    _t0, t_choice, after_1, after_2, ended, holds, sends, writes = run_virtual(main)
    assert after_1 == (0, 1, True)  # step 1 held: the step shown is still step 0
    assert after_2 == (2, 0, True)  # step 2 went out; a sent step resets the count
    assert ended == "the run is over"
    [hold] = holds
    assert hold["mode"] == "linear-rows" and hold["step"] == 1 and hold["room"] == 9
    # the old rule (the window plus the step within the cap) would have sent it, leaving no room
    assert hold["writes_60s"] + hold["step_writes"] <= 90
    assert hold["counted"] + hold["step_writes"] + hold["room"] > 90
    # from the simulator's own log: every step of the new run left room for a guest's burst in the window it shares
    # (writes that leave it within the 1 s per-device gap aren't shared)
    assert sends and sends[0][1].startswith("step 2 ")
    for t, label, n in sends:
        shared = sum(1 for wt, _, _ in writes if t - 59.0 < wt < t)  # before the step's own
        assert shared + n + 9 <= 90, (t - t_choice, label, shared, n)


def test_writes_about_to_leave_the_window_dont_hold_a_step_when_sends_land_after_the_boundary():
    """Review B-R3-TS-5 (B-R1-1): the cap check leaves out the writes older than 60 s less the per-device gap, which
    are gone before the step's own writes are out. In virtual time a step's sends are stamped exactly on its boundary,
    so the step from 60 s before leaves the window just as the next is due and the rule can't be seen. On hardware
    they land a few ms after it: here 5 ms. Then the whole 60 s window still holds eight steps (72), and one 429
    retry in it makes 72 + 1 + 9 (the step) + 9 (a guest's room) = 91 > 90, so counting it held a step that the 60 s
    cap never needed held. With the gap rule it counts 63 + 1, and every step goes out."""
    from conductor.drivers.base import Outcome, WriteResult

    late_s = 0.005

    async def main():
        w = stepped_world(shipped=True)  # nine modules, 7.5 s steps, the cap of 90
        await quiet_start(w)
        wait_rate = w.executor._wait_rate

        async def late(slot):  # a send leaves a few ms after the step's boundary, as on hardware
            await wait_rate(slot)
            await w.clock.sleep(late_s)

        w.executor._wait_rate = late
        t0 = w.clock.monotonic()
        await w.engine.request_mode("linear-columns", source="ops")
        runner = w.engine.runner
        write, throttle = w.sim.write, {"at": t0 + 6 * runner.step_s}

        async def throttled_once(device_id, rows, *, purpose, **kw):  # step 6's write to r1c1 is answered 429
            if (
                "at" in throttle
                and w.clock.monotonic() >= throttle["at"]
                and device_id.endswith("canopy-r1c1")
            ):
                del throttle["at"]
                return WriteResult(
                    device_id,
                    Outcome.THROTTLED,
                    http_status=429,
                    sent_at=w.clock.now(),
                    error="HTTP 429 (test)",
                )
            return await write(device_id, rows, purpose=purpose, **kw)

        w.sim.write = throttled_once
        await w.run_for(100)
        out = {
            "runner": runner,
            "holds": [e for e in w.events("program") if e.get("action") == "budget_hold"],
            "throttled": [e for e in w.events("attempt") if e.get("http_status") == 429],
            "sends": [t - t0 for t, _, _ in w.writes if t >= t0],
        }
        await w.close()
        return out

    out = run_virtual(main)
    runner, sends = out["runner"], out["sends"]
    assert len(out["throttled"]) == 1  # the one 429, retried once
    assert runner.step_s == 7.5 and runner.count == 12
    assert out["holds"] == []  # no step held back
    for n in range(12):  # every step's sends landed 5 ms after its boundary, not on it
        landed = [t - n * 7.5 for t in sends if n * 7.5 <= t < n * 7.5 + 0.5]
        assert landed and all(abs(d - late_s) < 1e-6 for d in landed), (n, landed)
    # by send time the whole 60 s before step 8 held steps 0–7 (the 429's retry among them; the 429 itself is the
    # executor's 73rd): counted in full, 73 + 9 + 9 = 91
    assert sum(1 for t in sends if 0.0 < t < 60.0) == 72
    assert runner.ended == "the run is over" and runner.steps_sent == 12  # 11 steps and the hold


def test_two_held_steps_apart_never_end_a_run_a_sent_step_resets_the_count():
    """B4 / TS-3: only HELD_STEPS_END (2) holds *in a row* end a run. A step held, the next sent, a later one held
    again: the run goes on (with fast guest pacing holds come and go on a busy account; they mustn't add up)."""

    async def main():
        w = stepped_world(shipped=True)
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="ops")
        runner = w.engine.runner
        real = runner._over_cap

        def over_cap(wanted):  # the account's cap holds steps 2 and 4 back, and only them
            if runner.index in (2, 4):
                return {"account": "canopy", "writes_60s": 90, "counted": 90, "step_writes": 9, "room": 9}
            return real(wanted)

        runner._over_cap = over_cap
        seen = []
        for _ in range(5):  # steps 1–5 (7.5 s apart; None once the run is over)
            wait = runner.next_step_in()
            await w.run_for((7.5 if wait is None else wait) + 0.5)
            seen.append((runner.index, runner.shown, runner.held_steps, runner.running))
        await w.run_for(100)
        out = (seen, runner.budget_holds, runner.ended)
        await w.close()
        return out

    seen, holds, ended = run_virtual(main)
    assert seen == [
        (1, 1, 0, True),
        (2, 1, 1, True),  # held: step 1 is still shown
        (3, 3, 0, True),  # sent: the count is back to 0
        (4, 3, 1, True),  # held again, not twice in a row: still running
        (5, 5, 0, True),
    ]
    assert holds == 2 and ended == "the run is over"


def test_a_breath_whose_every_changing_step_is_held_ends_a_step_with_nothing_to_write_resets_nothing():
    """Review round 3 (D75, B4): a step with nothing to write isn't a sent step. breathe-level comes back to its hold's
    level every other step (28, 32, 28, 24), so on an account with no room for any step that writes, step 1 is
    held, step 2 has nothing to write (it is what the canopy shows), and step 3 is held: two holds in a row, and the
    run ends on its still copy. Counted as sent, step 2 reset the count, and the breath ran on to its end under
    "swells and softens" without a single level change on the lights."""

    async def main():
        w = stepped_world(shipped=True)
        await quiet_start(w)
        await w.engine.request_mode("breathe-level", source="ops")
        runner, plan = w.engine.runner, w.engine.current_plan()
        real = runner._over_cap

        def full(wanted):  # the account has no room for any step that writes
            if wanted:
                return {"account": "canopy", "writes_60s": 90, "counted": 90, "step_writes": 9, "room": 9}
            return real(wanted)

        runner._over_cap = full
        await w.run_for(1.0)  # the mode's burst (step 0) is out
        n = len(w.writes)
        seen = []
        for _ in range(3):  # steps 1–3
            await w.run_for(runner.next_step_in() + 0.5)
            seen.append((runner.index, runner.shown, runner.held_steps, runner.running))
        out = (plan, seen, runner.ended, w.writes[n:], w.engine.state())
        await w.close()
        return out

    plan, seen, ended, writes, state = run_virtual(main)
    assert (
        step_rows(plan, 2) == step_rows(plan, 0) != step_rows(plan, 1)
    )  # the breath is back at its hold's level
    assert seen == [
        (1, 0, 1, True),  # held
        (2, 2, 1, True),  # nothing to write: shown, the count kept
        (3, None, 2, False),  # held again: two in a row end the run
    ]
    assert ended == "the account's write budget held back 2 steps in a row"
    assert writes == []  # the lights never left the hold's level, and the end's hold was already showing
    assert state.program is None and state.feature == plan.feature  # the still copy


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
    modes = parse_modes(raw, recipes=RECIPES, canopy=load_canopy_config(), path=PATH)

    async def main():
        w = stepped_world(motion={"stepped": {"account_writes_per_min": 90}})  # 90 less 25 % → 8 s steps
        ctx = CompileContext(
            registry=w.registry,
            capabilities={e.id: CAPS for e in w.registry.canopy_modules()},
            modes=modes,
            recipes=RECIPES,
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
    for t in dropped:  # each drop is re-sent at its second verification readback (D71), not after the third
        assert any(t + 2.0 < r <= t + 3.6 for r in resends), (t, resends)


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


def test_a_guest_brightness_change_the_cap_would_hold_back_goes_out_at_once_never_folded():
    """Review B-R3-1 (D75): a guest's level is never folded into a step the account's cap will hold back. A guest
    moving the brightness every ~2 s (D72's pace on nine) through a guest's run at 7.5 s steps fills the window, so
    the step after the last change is held. Folded into it, that change waited for the run's end (8 s here) while
    the panel drew it on lights still at the old level. It goes out at once instead, as its own burst (the room the
    cap keeps is for it), and the level the panel draws is always the one the lights were sent."""

    async def main():
        w = stepped_world(shipped=True)  # the shipped budget: 7.5 s steps on nine
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="kiosk")
        runner = w.engine.runner
        await w.run_for(5 * runner.step_s + 0.5)  # step 5 is out
        refused = []
        for level in (50, 60, 40):  # each goes out at once: step 6 is more than fold_guest_within_s away
            refused.append((await w.engine.set_guest_brightness(level, source="kiosk")).refused)
            await w.run_for(2.1)
        due = runner.t0 + 6 * runner.step_s
        wait = due - w.clock.monotonic()  # 0.7 s: close enough to step 6 to fold into it
        refused.append((await w.engine.set_guest_brightness(30, source="kiosk")).refused)
        at = w.clock.monotonic()
        drawn_not_sent = []
        while w.clock.monotonic() < at + 3 * runner.step_s:
            program = w.engine.state().program
            if program is not None:
                drawn = {v for v in program["levels"][program["step"]] if v is not None}
                sent = {level_of(w.engine.sent_rows[d]) for d in runner.plan.modules}
                if drawn != sent:
                    drawn_not_sent.append((round(w.clock.monotonic() - at, 2), drawn, sent))
            await w.run_for(0.1)
        holds = [e["step"] for e in w.events("program") if e.get("action") == "budget_hold"]
        first = min((t for t, _, rows in w.writes if t >= at and level_of(rows) == 30), default=None)
        out = (refused, wait, at, due, first, drawn_not_sent, holds, runner.ended)
        await w.close()
        return out

    refused, wait, at, due, first, drawn_not_sent, holds, ended = run_virtual(main)
    assert refused == [False] * 4 and 0 < wait <= 2.0
    assert holds[:1] == [6]  # the step it would have folded into was held back
    assert first is not None and first < due and first - at < 0.5  # out at once, not with a held step
    assert drawn_not_sent == []  # the panel never drew a level the lights weren't sent
    assert ended == "the account's write budget held back 2 steps in a row"


def test_a_guest_level_folded_into_a_step_the_cap_then_holds_back_goes_out_at_that_step():
    """Review B-R3-1: there was room when the guest's level was folded into the next step, but writes from
    elsewhere filled the window in the moments before it, so the step is held. The level goes out then, on the step
    last sent (one PUT per module), not a step or a run later: the lights, the frozen frame and the level the panel
    draws agree, and the next step carries it on."""

    async def main():
        w = stepped_world(shipped=True)
        await quiet_start(w)
        await w.engine.request_mode("linear-columns", source="ops")
        runner, plan = w.engine.runner, w.engine.current_plan()
        await w.run_for(runner.next_step_in() + 0.5)  # step 1 is out
        await w.run_for(runner.next_step_in() - 1.5)  # 1.5 s before step 2
        n, at = len(w.writes), w.clock.monotonic()
        await w.engine.set_guest_brightness(40, source="ops")
        await w.run_for(1.0)
        before = w.writes[n:]  # folded: nothing of its own yet
        account_writes, crowd = w.executor.account_writes, {"extra": 75}

        def crowded(address, **kw):  # others fill the account's window just before step 2
            return account_writes(address, **kw) + crowd["extra"]

        w.executor.account_writes = crowded
        await w.run_for(1.0)  # step 2 came due 0.5 s ago: held
        burst = w.writes[n:]
        held = (runner.index, runner.shown, runner.budget_holds, runner.running)
        program = w.engine.state().program
        await w.run_for(2.0)  # the burst has landed
        lights = {level_of(w.sim.device_view(w.address(d))) for d in plan.modules}
        crowd["extra"] = 0
        m = len(w.writes)
        await w.run_for(runner.next_step_in() + 0.5)  # step 3: room again
        step_3 = (runner.shown, w.writes[m:])
        labels = {
            c["label"] for c in w.events("command") if c["source"] == "ops" and c["t"] >= w.clock.epoch + at
        }
        await w.close()
        return plan, at, before, burst, held, program, lights, step_3, labels

    plan, at, before, burst, held, program, lights, (shown_3, writes_3), labels = run_virtual(main)
    assert before == []
    assert held == (2, 1, 1, True)  # step 2 held: step 1 is still the step shown
    # the guest's level went out when step 2 was held: one PUT per module, on step 1's colours, within the fold window
    assert sorted(d for _, d, _ in burst) == sorted(plan.modules)
    assert all(t - at <= 2.0 for t, _, _ in burst)
    step_1 = step_rows(plan, 1)
    for _, d, rows in burst:
        assert level_of(rows) == 40
        assert [r for r in rows if r.key != ("brightness", None)] == [
            r for r in step_1[d] if r.key != ("brightness", None)
        ]
    assert labels == {"guest brightness: linear-columns"}
    # the frozen frame is drawn at the level sent, and the lights read it
    assert program["frames"][2] == program["frames"][1]
    assert {v for v in program["levels"][2] if v is not None} == {40} == lights
    assert shown_3 == 3 and len(writes_3) == 9 and all(level_of(rows) == 40 for _, _, rows in writes_3)


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


# ---- D75: a breath on the real engine, held steps, a choice runs once, the idle look ------------------------------
BREATH_LEVELS = [28, 32, 28, 24]  # breathe-level's step levels (brightness 28 ± a swing of 4, lag 0)


def program_groups(w, since: float = 0.0) -> dict[str, list[dict]]:
    """The runner's commands, by label ("step n (k of c): mode"), in send order."""
    groups: dict[str, list[dict]] = {}
    for c in w.events("command"):
        if c["source"] == "program" and c["t"] >= since:
            groups.setdefault(c["label"], []).append(c)
    return groups


def level_of(rows) -> int | None:
    return next((r.value for r in rows if r.key == ("brightness", None)), None)


def program_since(w, since: float) -> list[dict]:
    """The runner's commands queued at or after wall time `since` (a command's event is recorded when it ends)."""
    return [c for c in w.events("command") if c["source"] == "program" and c["timings"]["queued_at"] >= since]


def test_a_breath_sends_each_step_as_one_put_per_changed_module_with_its_brightness():
    async def main():
        w = stepped_world(shipped=True)
        await quiet_start(w)
        t0 = w.clock.monotonic()
        await w.engine.request_mode("breathe-level", source="ops")
        plan, runner = w.engine.current_plan(), w.engine.runner
        await w.run_for(100)
        out = (plan, runner, [(t - t0, d, rows) for t, d, rows in w.writes if t >= t0], w.events("command"))
        await w.close()
        return out

    plan, runner, writes, commands = run_virtual(main)
    assert runner.ended == "the run is over" and runner.budget_holds == 0 and runner.count == 12
    steps = plan.program.steps
    by_label: dict[str, list[str]] = {}
    for c in commands:
        if c["source"] == "program":
            by_label.setdefault(c["label"].split(":")[0], []).append(c["device_id"])
    for label, ids in by_label.items():
        if label == "hold":
            continue
        n = int(label.split()[1])
        changed = [d for d in plan.modules if steps[n % 4].rows[d] != steps[(n - 1) % 4].rows[d]]
        assert sorted(ids) == sorted(f"sim-{d}" for d in changed), label  # one command per changed module
    # every write: the full state vector, its brightness the breath's level for that step, step by step
    for n in range(1, 12):
        sent = [rows for t, _, rows in writes if n * 7.5 - 1e-6 <= t < n * 7.5 + 1.0]
        assert len(sent) == 9 and {level_of(rows) for rows in sent} == {BREATH_LEVELS[n % 4]}, n
        assert all([r.key for r in rows] == ROW_KEYS for rows in sent)
    end = [rows for t, _, rows in writes if 90 - 1e-6 <= t < 91.0]
    assert end and {level_of(rows) for rows in end} == {28}  # the run ends on the hold (step 0's level)


def test_a_lagged_breath_writes_only_the_modules_that_change():
    async def main():
        w = stepped_world()  # 6 s steps
        await quiet_start(w)
        t0 = w.clock.monotonic()
        await w.engine.request_mode("breathe-rings", source="ops")
        plan = w.engine.current_plan()
        await w.run_for(95)
        out = (plan, program_groups(w), [(t - t0, d) for t, d, _ in w.writes if t >= t0])
        await w.close()
        return out

    plan, groups, writes = run_virtual(main)
    steps = plan.program.steps
    assert len(steps) == 7
    sizes = []
    for label, cmds in groups.items():
        if label.startswith("hold"):
            continue
        n = int(label.split()[1])
        changed = {d for d in plan.modules if steps[n % 7].rows[d] != steps[(n - 1) % 7].rows[d]}
        assert {c["device_id"].removeprefix("sim-") for c in cmds} == changed, label
        # a module whose rows are unchanged gets nothing that step
        at = [d for t, d in writes if n * 6.0 - 1e-6 <= t < n * 6.0 + 1.0]
        assert sorted(at) == sorted(changed), label
        sizes.append(len(changed))
    assert len(sizes) == 14  # steps 1–14 of a 90 s run at 6 s
    assert min(sizes) >= 1 and min(sizes) < 9  # every step changes something; resting modules get nothing


def test_a_breath_under_a_guest_mash_draws_held_steps_as_held_and_ends_after_two():
    async def main():
        w = stepped_world(shipped=True, kiosk={"fast_pacing": True, "cooldown_min_s": 2})
        await quiet_start(w)
        sends: list[tuple[float, str, int]] = []
        original = w.engine.send

        def spy(device_ids, *, source, label):
            sent = original(device_ids, source=source, label=label)
            sends.append((w.clock.monotonic(), label, len(sent)))
            return sent

        w.engine.send = spy
        t0 = w.clock.monotonic()
        await w.engine.request_mode("breathe-level", source="ops")
        runner, plan = w.engine.runner, w.engine.current_plan()
        frozen, level = [], 40
        while runner.running and w.clock.monotonic() < t0 + 90:
            await w.engine.set_guest_brightness(level, source="kiosk")
            level = 100 - level  # 40, 60, 40, …
            await w.run_for(2.5)
            state = w.engine.state()
            if runner.running and runner.shown is not None and runner.shown < runner.index and state.program:
                frozen.append((runner.shown, runner.index, state.program))
        await w.run_for(1)
        out = (runner, plan, frozen, w.engine.state(), sends, list(w.writes), t0)
        await w.close()
        return out

    runner, plan, frozen, after, sends, writes, t0 = run_virtual(main)
    assert runner.ended == "the account's write budget held back 2 steps in a row"
    assert frozen  # a held step was drawn while the run went on
    for shown, index, program in frozen:
        for k in range(shown + 1, index + 1):
            assert program["frames"][k] == program["frames"][shown], (shown, index, k)
            assert program["levels"][k] == program["levels"][shown], (shown, index, k)
    assert after.feature == plan.feature and after.program is None  # the still sentence afterwards
    # never past the cap: the writes on the account in the 60 s before each step went out, plus the step's own
    for t, label, n in sends:
        if label.startswith("step"):
            recent = sum(1 for wt, _, _ in writes if t - 60 < wt < t)
            assert recent + n <= 90, (t - t0, label, recent, n)
    by_device: dict[str, list[float]] = {}
    for wt, d, _ in writes:
        by_device.setdefault(d, []).append(wt)
    assert all(b - a >= 1.0 - 1e-6 for times in by_device.values() for a, b in pairwise(times))


@pytest.mark.parametrize(
    ("guest", "centre"),
    [
        (50, 50),  # mid-range: the breath swings 46–54 around the guest's level
        (
            100,
            96,
        ),  # B-R1-1: full brightness. Clamped level by level it read 100, 100, 100, 96: half the steps silent
        (
            10,
            14,
        ),  # TS-1: kiosk.guest_brightness_min (10). Never 6, and not 10, 14, 10, 10 (a silent step either)
    ],
)
def test_a_guest_brightness_moves_the_breath_instead_of_flattening_it(guest, centre):
    """D75 (B3): a guest level during a breath keeps the breath, swung around the guest's level, and the moving copy
    stays. The breath's centre moves so its whole swing (± 4 here) fits [kiosk.guest_brightness_min, 100]: every step
    still sends every module a new level, so the copy ("sent a brighter or softer level every few seconds") stays
    true at 100 and at the minimum. The iPad's levels (state.program) are the levels sent, the guest's applied (TS-2);
    the last frame (the hold) is the guest's own level."""

    async def main():
        w = stepped_world()  # 6 s steps
        await quiet_start(w)
        await w.engine.request_mode("breathe-level", source="ops")
        plan, runner = w.engine.current_plan(), w.engine.runner
        await w.run_for(8.5)  # step 1 is out; 3.5 s before step 2
        sent: list[tuple[str, str, int, set]] = []
        original = w.engine.send

        def spy(device_ids, *, source, label):
            ids = original(device_ids, source=source, label=label)
            sent.append((source, label, len(ids), {level_of(w.engine.sent_rows[d]) for d in ids}))
            return ids

        w.engine.send = spy
        state = await w.engine.set_guest_brightness(guest, source="ops")
        program = state.program
        await w.run_for(40)
        out = (plan, runner.running, runner.count, state, program, list(sent), w.engine.state())
        await w.run_for(50)  # the run ends at 90 s
        hold = [entry for entry in sent if entry[1].startswith("hold")]
        after = {level_of(w.engine.desired_rows(d)) for d in plan.modules}
        out = (*out, runner.ended, hold, after)
        await w.close()
        return out

    plan, running, count, state, program, sent, later, ended, hold, after = run_virtual(main)
    swing = [centre + level - 28 for level in BREATH_LEVELS]  # 28 ± 4: offsets 0, +4, 0, −4
    assert min(swing) >= 10 and max(swing) <= 100 and len({*swing}) == 3
    burst, *steps = sent
    assert burst[0] == "ops" and burst[2] == 9 and burst[3] == {swing[1]}  # sent at once, at step 1's offset
    assert [label.split()[1] for _, label, _, _ in steps][:6] == ["2", "3", "4", "5", "6", "7"]
    for source, label, n, levels in steps:
        # every step sends every module its new level: no silent step under the moving copy
        assert source == "program" and n == 9 and levels == {swing[int(label.split()[1]) % 4]}, label
    assert state.feature == plan.program.moving_feature and later.feature == plan.program.moving_feature
    assert running
    # TS-2: the levels the guest panel draws, from the current step on, are the levels sent (the guest's applied)
    levels = program["levels"]
    assert program["step"] == 1 and len(levels) == count + 1
    for n in range(1, count):
        assert levels[n] == [swing[n % 4]] * 9, n
    assert levels[count] == [guest] * 9  # the hold after the run: the guest's own level
    # after the run the still look is at the guest's own level: the hold goes out where the moved centre left the
    # last step elsewhere (at 50 the last step, offset 0, already is the hold)
    assert ended == "the run is over" and after == {guest}
    assert [(n, levels) for _, _, n, levels in hold] == ([(9, {guest})] if centre != guest else [])


def test_the_state_carries_each_frames_levels():
    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("breathe-level", source="ops")
        await w.run_for(8)
        state, plan = w.engine.state(), w.engine.current_plan()
        lit = canopy_view.lit_state(CANOPY_CFG, w.registry, plan, state, now=w.clock.now())
        await w.close()
        # a partial canopy (two kits driven): undriven positions carry no level
        w = stepped_world()
        kits = {
            e.id: (w.registry.address(e.id, "sim") if e.module in ("r0c0", "r0c1") else None)
            for e in w.registry.canopy_modules()
        }
        ctx = CompileContext(
            registry=w.registry,
            capabilities={e.id: CAPS for e in w.registry.canopy_modules()},
            modes=MODES,
            recipes=RECIPES,
            canopy=load_canopy_config(),
            addresses=kits,
        )
        w.engine.deps.compile = lambda mode_id, tier: compile_mode(mode_id, tier, ctx)
        await quiet_start(w)
        await w.engine.request_mode("breathe-level", source="ops")
        await w.run_for(8)
        partial = (w.engine.state(), w.engine.current_plan())
        partial_lit = canopy_view.lit_state(CANOPY_CFG, w.registry, partial[1], partial[0], now=w.clock.now())
        await w.close()
        return state, lit, partial, partial_lit

    state, lit, (partial, partial_plan), partial_lit = run_virtual(main)
    levels = state.program["levels"]
    assert len(levels) == len(state.program["frames"]) == 16  # 90 s at 6 s, and the hold
    assert levels[:5] == [[28] * 9, [32] * 9, [28] * 9, [24] * 9, [28] * 9] and levels[-1] == [28] * 9
    assert lit["motion"]["levels"] == levels  # every position lit
    assert state.to_json()["program"]["levels"] == levels
    driven = {GRID.index("r0c0"), GRID.index("r0c1")}
    assert len(partial_plan.modules) == 2
    for row in partial.program["levels"]:
        assert all((v is not None) == (i in driven) for i, v in enumerate(row))
    for row in partial_lit["motion"]["levels"]:
        assert all((v is not None) == (i in driven) for i, v in enumerate(row))


def test_start_up_and_the_idle_return_never_start_a_run_on_an_idle_look_with_a_rung():
    async def main():
        w = stepped_world(kiosk={"idle_mode": "breathe-level"})
        await w.engine.start()
        await w.run_for(1)
        first = (w.engine.current_plan().tier, w.engine.runner)
        await w.run_for(QUIET_S)
        await w.engine.request_mode("radial-outward", source="kiosk")
        await w.run_for(185)
        back = (w.engine.current_plan().mode_id, w.engine.current_plan().tier, w.engine.runner)
        returned = w.clock.now()
        await w.run_for(60)
        program_after = program_since(w, returned)
        await w.engine.request_mode("breathe-level", source="startup")
        repick = (w.engine.current_plan().tier, w.engine.runner)
        await w.close()
        return first, back, program_after, repick

    first, back, program_after, repick = run_virtual(main)
    tier, runner = first
    assert tier is Tier.T3 or (runner is not None and not runner.running)
    mode, tier, runner = back
    assert mode == "breathe-level" and tier is Tier.T3 and (runner is None or not runner.running)
    assert program_after == []
    assert repick[0] is Tier.T3 and (repick[1] is None or not repick[1].running)


def test_a_guest_choosing_the_idle_scene_gets_its_run():
    from conductor.canopy import RESTORE_SOURCE

    async def main():
        w = stepped_world(kiosk={"idle_mode": "breathe-level"})
        await quiet_start(w)
        await w.engine.request_mode("breathe-level", source="kiosk")
        kiosk = (w.engine.runner.running, w.engine.runner.program.run_s, w.engine.current_plan().tier)
        await w.run_for(10)
        await w.engine.request_mode("breathe-level", source="ops")
        ops = (w.engine.runner.running, w.engine.current_plan().tier)
        await w.run_for(10)
        await w.engine.request_mode("breathe-level", source=RESTORE_SOURCE)
        restore = (w.engine.current_plan().tier, w.engine.runner)
        await w.close()
        return kiosk, ops, restore

    kiosk, ops, restore = run_virtual(main)
    assert kiosk == (True, 90.0, Tier.STEPPED)
    assert ops == (True, Tier.STEPPED)  # stepped is first in its ladder
    assert restore == (Tier.T3, None)  # the checklist's change back never starts a run


IDLE_BREATH = {"idle_mode": "breathe-level"}
BREATH_30 = {"idle": {"breathe_run_s": 30, "every_s": 120}}


def test_an_idle_breath_yields_at_once_to_a_guest():
    async def main():
        w = stepped_world(kiosk=IDLE_BREATH, motion=BREATH_30)
        await quiet_start(w)
        await w.engine.idle_breath()
        runner = w.engine.runner
        breathing = (runner.running, w.engine.breathing, runner.program.run_s)
        await w.run_for(8)  # mid-breath
        n = len(w.writes)
        since = w.clock.now()
        await w.engine.set_guest_brightness(40, source="kiosk")
        await w.run_for(40)
        after = w.writes[n:]
        program = program_since(w, since)
        ends = [e for e in w.events("program") if e.get("action") == "end"]
        out = (breathing, runner, after, program, ends, w.engine.breathing)
        await w.close()
        return out

    breathing, runner, after, program, ends, still = run_virtual(main)
    assert breathing == (True, True, 30.0)
    assert not runner.running and runner.ended == "a guest changed the canopy" and not still
    assert len(after) == 9 and all(R.brightness(40) in rows for _, _, rows in after)  # one burst at 40
    assert program == []  # nothing more of the breath went out
    assert ends[-1]["reason"] == "a guest changed the canopy"


def test_motion_idle_breathes_every_every_s_and_never_moves_the_idle_clocks():
    async def main():
        w = stepped_world(kiosk=IDLE_BREATH, motion=BREATH_30)
        breaths = []
        original = w.engine.idle_breath

        async def spy():
            before = (w.engine.last_change_mono, w.engine.last_guest_input_mono, len(w.writes))
            state = await original()
            runner = w.engine.runner
            breaths.append(
                (w.clock.monotonic(), before, state, runner, runner is not None and runner.running)
            )
            return state

        w.engine.idle_breath = spy
        await w.engine.start()
        await w.run_for(400)
        clocks = (w.engine.last_change_mono, w.engine.last_guest_input_mono)
        out = (breaths, clocks, list(w.writes), w.events("program"))
        await w.close()
        return out

    breaths, clocks, writes, events = run_virtual(main)
    started = [b for b in breaths if b[4]]
    assert len(started) >= 3
    first_t, (change, guest, n), state, runner, _ = started[0]
    assert first_t >= 120.0  # every_s after the first look (start-up at 0)
    assert runner.count == int(30 / runner.step_s + 1e-6) == 5 and runner.program.run_s == 30.0
    assert (
        clocks == (change, guest) == (0.0, 0.0)
    )  # the idle hold and the idle reset count as they always did
    assert not [t for t, _, _ in writes[n:] if first_t <= t < first_t + 1.0]  # step 0 is the look showing
    assert state.look_phase is CanopyPhase.READY and state.feature == runner.program.moving_feature
    assert all(b[0] - a[0] >= 120.0 for a, b in pairwise(started))
    assert [e["run_s"] for e in events if e.get("action") == "idle_breath"] == [30.0] * len(started)


D54_NOTE = "the controller reports 60 entries for 30 heads and this kit has no proven map yet (D54): test"


@pytest.mark.parametrize("idle_mode", ["breathe-level", "breathe-rings"])
def test_an_idle_breath_on_flagged_modules_never_reads_changing_while_nothing_is_sent(idle_mode):
    """D73 on the idle breath (review B-R4-C1). On the show box Modules 1–2 carry only D54's "no proven map yet" note
    (`tracker.flagged_only`: needs attention, look confirmed). An idle breath sends nothing at its start (step 0 is
    the hold), so a module showing the hold counts as verified when the runner is installed, flagged or not. Before
    the fix a flagged module waited for its own first breath write to verify: the guest panel said "Changing the
    light…" for 8 s (breathe-level) or 26 s (breathe-rings, whose lag-3 corners are first written at step 4)."""

    async def main():
        w = stepped_world(kiosk={"idle_mode": idle_mode}, motion=BREATH_30)
        await quiet_start(w)
        modules = list(w.engine.current_plan().modules)
        for d in modules:
            w.tracker.flag_problem(w.address(d), D54_NOTE)
        flagged = all(w.tracker.flagged_only(w.address(d)) for d in modules)
        await w.until(lambda: w.engine.runner is not None and w.engine.runner.running, timeout=300, step=0.25)
        start, n = w.clock.monotonic(), len(w.writes)
        runner = w.engine.runner
        seen = []
        while w.clock.monotonic() - start < 30.0:
            state = w.engine.state()
            seen.append((round(w.clock.monotonic() - start, 2), state.look_phase, w.engine.breathing))
            await w.run_for(0.25)
        writes = [t - start for t, _, _ in w.writes[n:]]
        await w.close()
        return flagged, start, runner, seen, writes

    flagged, start, runner, seen, writes = run_virtual(main)
    assert flagged  # every module carries only the flagged note, its look confirmed
    assert start >= 120.0 and runner.program.mode == "breathe" and seen[0][2]  # the reconciler's idle breath
    assert writes and not [t for t in writes if t < 1.0]  # it steps later, but sends nothing at its start
    changing = [(t, phase) for t, phase, _ in seen if phase is CanopyPhase.UPDATING]
    assert not changing, changing  # was 0–8.25 s (breathe-level), 0–26.25 s (breathe-rings)


def test_with_motion_idle_on_the_idle_return_from_another_look_lands_as_one_breath():
    """D75 (review B-R2-TS-2): with motion.idle on, the idle return from a guest's look is one breath of
    breathe_run_s, not the rung's 90 s run, and it counts as the period's breath (`last_breath_mono`), as the Lab's
    staff copy says (IDLE_RUNS_ON)."""

    async def main():
        w = stepped_world(kiosk=IDLE_BREATH, motion=BREATH_30)
        await quiet_start(w)
        await w.engine.request_mode("radial-outward", source="kiosk")
        chosen = w.clock.monotonic()
        await w.until(lambda: w.engine.current_plan().mode_id == "breathe-level", timeout=200, step=0.5)
        returned = w.clock.monotonic()
        plan, runner = w.engine.current_plan(), w.engine.runner
        back = (plan.tier, runner.running, runner.program.run_s, runner.count, w.engine.breathing)
        breath_at = w.engine.last_breath_mono
        await w.run_for(60)
        program = [
            c["timings"]["queued_at"] - w.clock.epoch - returned
            for c in program_since(w, w.clock.epoch + returned)
        ]
        out = (returned - chosen, back, breath_at - returned, program, runner.ended)
        await w.close()
        return out

    after, back, breath_at, program, ended = run_virtual(main)
    assert 180.0 <= after <= 182.0  # the idle hold (kiosk.idle_return_s)
    assert back == (Tier.STEPPED, True, 30.0, 5, True)  # one breath: 30 s of 6 s steps
    assert abs(breath_at) <= 0.5  # the period's breath starts here
    assert program and max(program) <= 30.0 + 1e-3  # it stepped, and stopped once its 30 s were up
    assert ended == "the run is over"


def test_a_guest_choosing_the_idle_look_mid_breath_gets_the_full_run():
    """D75 (review B-R2-TS-2): a guest's choice of the idle look during its idle breath is a choice, so it runs the
    rung in full (90 s), never the breath's leftover breathe_run_s."""

    async def main():
        w = stepped_world(kiosk=IDLE_BREATH, motion=BREATH_30)
        await quiet_start(w)
        await w.engine.idle_breath()
        breath = (w.engine.breathing, w.engine.runner.program.run_s)
        await w.run_for(8)  # mid-breath
        await w.engine.request_mode("breathe-level", source="kiosk")
        runner = w.engine.runner
        out = (breath, runner.running, runner.program.run_s, runner.count, w.engine.breathing)
        await w.close()
        return out

    breath, running, run_s, count, breathing = run_virtual(main)
    assert breath == (True, 30.0)
    assert running and run_s == 90.0 and count == 15 and not breathing


def test_an_idle_breath_that_couldnt_run_when_it_landed_never_starts_on_a_re_pick():
    """D75 (review B-R2-1): with motion.idle on, an idle return that lands while the account cools off holds still
    (breathe-level has no canvas or slot, so its plan stays stepped). The breath counts as spent: once the cool-off
    lifts, inside the 30 s it would have taken, a re-pick neither starts it mid-way nor reads as breathing."""

    async def main():
        w = stepped_world(kiosk=IDLE_BREATH, motion=BREATH_30)
        await quiet_start(w)
        await w.engine.request_mode("radial-outward", source="kiosk")
        chosen = w.clock.monotonic()
        await w.run_for(170)  # the guest's run is long over
        account = w.engine._account_of(w.engine.target_of("canopy-r0c0").address)
        w.engine.stepped_cooloff[account] = w.clock.monotonic() + 20.0  # lifts 10 s after the idle return
        await w.until(lambda: w.engine.current_plan().mode_id == "breathe-level", timeout=20, step=0.5)
        plan, runner = w.engine.current_plan(), w.engine.runner
        back = (w.clock.monotonic() - chosen, plan.tier, runner.running, runner.ended, w.engine.breathing)
        spent = w.engine._breath_run_s  # the breath's run_s is spent with it
        await w.run_for(12)  # the cool-off is over
        since = w.clock.now()
        await w.engine.request_mode("breathe-level", source="reconciler")
        repick = (w.engine.current_plan().tier, w.engine.runner.running, w.engine.breathing)
        await w.run_for(25)
        out = (back, spent, repick, program_since(w, since))
        await w.close()
        return out

    back, spent, repick, program = run_virtual(main)
    after, tier, running, ended, breathing = back
    assert 180.0 <= after <= 182.0 and tier is Tier.STEPPED and not running and not breathing
    assert "cool-off" in ended and spent is None
    assert repick == (Tier.STEPPED, False, False)
    assert program == []  # nothing of the breath went out


def test_the_idle_breath_waits_while_anything_else_holds_the_canopy():
    from conductor.canopy import Trial

    async def held(case: str) -> list:
        sections = {"kiosk": {**IDLE_BREATH, "idle_reset_s": 3600}, "motion": BREATH_30}
        w = stepped_world(**sections)
        await w.engine.start()
        await w.run_for(5)
        if case == "paused":
            w.engine.pause_guest_input(True, source="ops")
        elif case == "try-on":
            now = w.clock.monotonic()
            trial = Trial("breathe-level", "Breathe (draft)", None, None, now, now + 700, w.clock.now())
            assert not (await w.engine.start_trial(trial)).refused
        elif case == "rehearsal":
            await w.engine.rehearse("breathe-level", 10, source="ops")
        elif case == "guest":
            await w.engine.set_guest_brightness(50, source="ops")
        elif case == "outage":
            w.sim.faults.set_outage(True)
            w.engine.enter_outage("test outage")
        await w.run_for(590)
        out = [e for e in w.events("program") if e.get("action") == "idle_breath"]
        await w.close()
        return out

    async def main():
        return {case: await held(case) for case in ("paused", "try-on", "rehearsal", "guest", "outage")}

    out = run_virtual(main)
    assert out == {case: [] for case in out}


def test_motion_idle_with_the_live_gate_shut_never_breathes():
    async def main():
        tier_allowed = _wiring(simulated=False).tier_allowed
        w = stepped_world(kiosk=IDLE_BREATH, motion=BREATH_30, tier_allowed=tier_allowed)
        await w.engine.start()
        await w.run_for(600)
        out = (
            [c for c in w.events("command") if c["source"] == "program"],
            w.engine.current_plan(),
            w.engine.runner,
            w.engine.last_breath_mono,
        )
        await w.close()
        return out

    program, plan, runner, last = run_virtual(main)
    assert program == [] and plan.program is None and plan.tier is Tier.T3 and runner is None
    assert last >= 480.0  # it kept asking, once a period, and the gate kept saying no


def test_a_resting_module_is_still_read_and_healed_within_10_s():
    """D75: a lagged breath leaves a module resting for steps at a time. Its rolling reads go on (the global
    stand-down is gone), so a change made outside the Conductor heals within 10 s; a module the next step writes is
    never read (the read would race that step's verified write)."""

    async def main():
        w = stepped_world()  # 6 s steps
        await quiet_start(w)
        t0 = w.clock.monotonic()
        await w.engine.request_mode("breathe-rings", source="ops")
        plan, runner = w.engine.current_plan(), w.engine.runner
        reads: list[tuple[float, str, bool]] = []
        original = w.engine.reconciler._read_one

        async def spy(device_id, target):
            steps, n = runner.program.steps, runner.index
            if runner.running:  # what the next step sends it, against what it has now
                now_rows = steps[(runner.shown if runner.shown is not None else n) % 7].rows[device_id]
                reads.append(
                    (w.clock.monotonic() - t0, device_id, steps[(n + 1) % 7].rows[device_id] == now_rows)
                )
            return await original(device_id, target)

        w.engine.reconciler._read_one = spy
        await w.run_for(
            30.5 - (w.clock.monotonic() - t0)
        )  # step 5 is out; r1c1 rests through steps 5, 6 and 0
        response = w.sim.faults.change_out_of_band(
            w.address("canopy-r1c1"), [R.color_mode("white"), R.color_temperature(5000)]
        )
        changed = w.clock.monotonic()
        wrong = await w.until(lambda: not w.correct("canopy-r1c1"), timeout=3)
        healed = await w.until(lambda: w.correct("canopy-r1c1"), timeout=15)
        healed_at = w.clock.monotonic() - changed
        running = runner.running
        await w.close()
        return plan, response, wrong, healed, healed_at, running, reads

    plan, response, wrong, healed, healed_at, running, reads = run_virtual(main)
    lags = plan.program.steps
    assert len(lags) == 7 and response.ok and running
    assert wrong is not None and healed is not None and healed_at <= 10.0, healed_at
    assert any(d == "canopy-r1c1" and 30.5 <= t <= 42.0 for t, d, _ in reads)  # read while it rests
    assert reads and all(resting for _, _, resting in reads)  # never a module the next step writes


def test_a_finished_breath_or_run_never_restarts_on_a_re_pick():
    from conductor.canopy import LOOKS_SOURCE

    async def check(w, mode_id: str) -> list:
        """Every kind of re-pick after the run is over: none starts a run, none sends a program command, and a
        clean interval after throttling (downgrade, then upgrade) eases back to the still rung, never the stepped one
        (review B-R3-TS-1: without the downgrade the upgrade re-picked a stepped plan as stepped, so it couldn't
        fail)."""
        since = w.clock.now()
        running = []
        await w.engine.request_mode(mode_id, source="reconciler")
        running.append(w.engine.runner is not None and w.engine.runner.running)
        w.engine._replan_soon()
        await w.run_for(2)
        running.append(w.engine.runner is not None and w.engine.runner.running)
        compile = w.engine.deps.compile

        def longer(m, tier):  # a publish whose run is longer than the one that ran
            plan = compile(m, tier)
            if plan.program is not None:
                plan.program.run_s = 150.0
            return plan

        w.engine.deps.compile = longer
        await w.engine.request_mode(mode_id, source=LOOKS_SOURCE)
        running.append(w.engine.runner is not None and w.engine.runner.running)
        down = w.engine.downgrade("3 commands ended throttled on account canopy")  # the modules rung
        w.engine.upgrade("clean")  # the floor lifts: a re-pick
        running.append(w.engine.runner is not None and w.engine.runner.running)
        eased = (down, w.engine.current_plan().tier, dict(w.engine.floors))
        await w.run_for(20)
        w.engine.deps.compile = compile
        program = program_since(w, since)
        return [running, program, eased]

    async def main():
        w = stepped_world(kiosk=IDLE_BREATH, motion=BREATH_30)
        await quiet_start(w)
        await w.engine.idle_breath()  # a breath that runs out
        await w.run_for(35)
        out = {"breath": await check(w, "breathe-level")}
        await w.engine.idle_breath()  # a breath a guest ends
        await w.run_for(8)
        await w.engine.set_guest_brightness(40, source="ops")
        await w.run_for(1)
        out["yield"] = await check(w, "breathe-level")
        await w.engine.request_mode("radial-outward", source="kiosk")  # a guest's run that runs out
        await w.run_for(95)
        out["kiosk"] = await check(w, "radial-outward")
        await w.close()
        return out

    out = run_virtual(main)
    for case, (running, program, eased) in out.items():
        assert running == [False, False, False, False], case
        assert program == [], case
        assert eased == (True, Tier.T3, {}), case  # back up to the still rung, never the stepped one


def test_a_run_that_ends_on_its_own_marks_its_choice_run_before_any_re_pick():
    """D75 (spec summary point 3, review B-R5-TS-2): a guest's run that runs out marks its choice as run when it ends
    (`finish` → `note_run_over`), so the first re-pick after it — here a Lab publish whose run is longer (150 s) than
    the one that ran — only holds. Without the mark, that re-pick's runner would count its 150 s from the choice and
    start the steps again 95 s in. (The test above always re-picks through the reconciler first, and `_install` marks
    a choice whose runner can't run itself, so it can't see this.)"""
    from conductor.canopy import LOOKS_SOURCE

    async def main():
        w = stepped_world()
        await quiet_start(w)
        await w.engine.request_mode("radial-outward", source="kiosk")
        runner = w.engine.runner
        first = (runner.running, runner.count * runner.step_s)
        await w.run_for(95)  # its 90 s run ends on its own
        ended = (runner.running, runner.ended, w.engine.runner is runner)
        compile = w.engine.deps.compile

        def longer(m, tier):  # a publish whose run is longer than the one that ran
            plan = compile(m, tier)
            if plan.program is not None:
                plan.program.run_s = 150.0
            return plan

        w.engine.deps.compile = longer
        since = w.clock.now()
        await w.engine.request_mode("radial-outward", source=LOOKS_SOURCE)  # the first re-pick after the end
        plan, after = w.engine.current_plan(), w.engine.runner
        repick = (plan.tier, plan.program.run_s, after.running, after.ended)
        await w.run_for(20)
        out = (first, ended, repick, program_since(w, since), w.engine.runner.running)
        w.engine.deps.compile = compile
        await w.close()
        return out

    first, ended, repick, program, running = run_virtual(main)
    assert first == (True, 90.0)
    assert ended == (False, "the run is over", True)  # it ran out: no guest, no re-pick ended it
    assert repick == (Tier.STEPPED, 150.0, False, "this choice already ran (a re-pick only holds)")
    assert program == [] and not running  # nothing steps again after the publish


def _motion_t1_world(keep: dict[str, list]):
    """radial-outward on the ladder [T1, STEPPED, T3], its T1 a painted, animating "canvas" stand-in (motion True):
    the modules in `keep` (filled in by the test once it knows what they were last sent) get exactly those rows, so
    nothing but 07 §6 sends them anything; the others a level (55) no step or hold of it sends."""
    from dataclasses import replace

    w = stepped_world()
    compile = w.engine.deps.compile

    def with_t1(mode_id, tier):
        if mode_id == "radial-outward" and tier is Tier.T1:
            hold = compile(mode_id, Tier.T3)
            modules = {}
            for d, mt in hold.modules.items():
                rows = keep.get(d) or [
                    R.brightness(55) if r.key == ("brightness", None) else r for r in mt.rows
                ]
                modules[d] = replace(mt, rows=list(rows), expect=list(rows))
            return replace(hold, tier=Tier.T1, motion=True, modules=modules, waves=None, feature="T1 (test)")
        return compile(mode_id, tier)

    w.engine.deps.compile = with_t1
    w.engine.deps.ladder = lambda m: (
        [Tier.T1, Tier.STEPPED, Tier.T3] if m == "radial-outward" else ladder(m, MODES)
    )
    return w


def test_a_settle_onto_a_motion_plan_re_triggers_every_module():
    """07 §6 (D75, review B-R5-TS-3): a run that ends on its own settles onto a motion plan by re-triggering all nine
    modules, the two whose T1 rows are exactly what the run's last step sent them included. (A run ends on its last
    step, not on the hold: 90 s of 6 s steps is 15 steps, and step 14 is cycle step 2, so the two modules' T1 rows are
    built from that step; built from the hold, they'd differ from what was sent and count as changed anyway.)"""

    async def main():
        keep: dict[str, list] = {}
        w = _motion_t1_world(keep)
        await quiet_start(w)
        await w.engine.request_mode("radial-outward", source="kiosk")
        runner = w.engine.runner
        first = (w.engine.current_plan().tier, runner.running)
        kept = list(runner.plan.modules)[:2]
        last = step_rows(runner.plan, runner.count - 1)  # the step the run ends on
        keep.update({d: list(last[d]) for d in kept})
        before: dict[str, list | None] = {}
        settle = w.engine.settle_after_run

        def spy(r):  # what the two modules were last sent, as the run hands over to the settle
            before.update({d: w.engine.sent_rows.get(d) for d in kept})
            return settle(r)

        w.engine.settle_after_run = spy
        await w.run_for(92)
        settles = [c for c in w.events("command") if c["source"] == "settle"]
        out = (first, runner.ended, w.engine.current_plan(), w.engine.runner, kept, keep, before, settles)
        await w.close()
        return out

    first, ended, plan, runner, kept, keep, before, settles = run_virtual(main)
    assert first == (Tier.STEPPED, True)  # a guest's choice runs the rung first
    assert ended == "the run is over" and plan.tier is Tier.T1 and runner is None
    assert all(plan.modules[d].rows == keep[d] for d in kept)
    assert before == {d: keep[d] for d in kept}  # those two already show their T1 rows: only 07 §6 sends them
    assert len(settles) == 9 and {c["label"] for c in settles} == {"after the run: radial-outward"}
    assert {c["device_id"].removeprefix("sim-") for c in settles} == set(plan.modules)


def test_a_deferred_settle_onto_a_motion_plan_re_triggers_every_module():
    """07 §6 through `_settle_later` (D75, review B-R5-TS-3): a run the write budget ended (HELD_STEPS_END holds in a
    row) sends its hold at once, and one cap window later `_settle` moves it to a motion plan by re-triggering all nine
    modules, the two whose T1 rows are exactly the hold they were just sent included."""

    async def main():
        keep: dict[str, list] = {}
        w = _motion_t1_world(keep)
        await quiet_start(w)
        await w.engine.request_mode("radial-outward", source="kiosk")
        runner = w.engine.runner
        kept = list(runner.plan.modules)[:2]
        keep.update({d: list(runner.plan.modules[d].rows) for d in kept})  # the hold: the plan's own rows
        await w.run_for(20)  # a few steps are out
        real = w.executor.account_writes
        w.executor.account_writes = lambda address, **kw: real(address, **kw) + 120  # the 60 s window is full
        while runner.running:
            await w.run_for(0.5)
        end = w.clock.monotonic()
        w.executor.account_writes = real
        ended = (runner.ended, w.engine.current_plan().tier)
        await w.run_for(30)  # the hold is out; the settle waits one cap window
        before = (
            {d: w.engine.sent_rows.get(d) for d in kept},
            [c for c in w.events("command") if c["source"] == "settle"],
        )
        await w.run_for(end + 65 - w.clock.monotonic())
        settles = [c for c in w.events("command") if c["source"] == "settle"]
        out = (ended, before, w.engine.current_plan(), w.engine.runner, kept, keep, settles)
        await w.close()
        return out

    ended, (sent, early), plan, runner, kept, keep, settles = run_virtual(main)
    assert ended == ("the account's write budget held back 2 steps in a row", Tier.STEPPED)
    assert early == []  # nothing settles inside the cap window
    # those two already show their T1 rows (the hold they were just sent): only 07 §6 sends them
    assert sent == {d: keep[d] for d in kept}
    assert plan.tier is Tier.T1 and runner is None and all(plan.modules[d].rows == keep[d] for d in kept)
    assert len(settles) == 9 and {c["label"] for c in settles} == {"after the run: radial-outward"}
    assert {c["device_id"].removeprefix("sim-") for c in settles} == set(plan.modules)


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


def test_motion_idle_settings_load_off_and_refuse_nonsense(tmp_path):
    """motion.idle (D75): the idle look's breath, off (null) as shipped; only Alon turns it on."""
    assert load_settings().motion.idle is None
    (tmp_path / "conductor.yaml").write_text("motion: {idle: {breathe_run_s: 60, every_s: 300}}\n")
    idle = load_settings(tmp_path).motion.idle
    assert (idle.breathe_run_s, idle.every_s) == (60.0, 300.0)
    (tmp_path / "conductor.yaml").write_text("motion: {idle: {breathe_run_s: 60, every_s: 30}}\n")
    with pytest.raises(ConfigError, match="must be longer than breathe_run_s"):
        load_settings(tmp_path)
    (tmp_path / "conductor.yaml").write_text("motion: {idle: {breathe_run_s: 200, every_s: 300}}\n")
    with pytest.raises(ConfigError, match="breathe_run_s"):
        load_settings(tmp_path)
    (tmp_path / "conductor.yaml").write_text(
        "motion: {idle: {breathe_run_s: 60, every_s: 300, burst: true}}\n"
    )
    with pytest.raises(ConfigError, match="burst"):
        load_settings(tmp_path)
    # against the modes (show._check_stepped): an idle look with a rung, and room for two paced steps on nine
    idle = {"idle_mode": "breathe-level"}
    _check_stepped(settings_with(kiosk=idle, motion={"idle": {"breathe_run_s": 60, "every_s": 300}}), MODES)
    _check_stepped(settings_with(kiosk=idle, motion={"idle": {"breathe_run_s": 15, "every_s": 300}}), MODES)
    too_short = settings_with(kiosk=idle, motion={"idle": {"breathe_run_s": 10, "every_s": 300}})
    with pytest.raises(ConfigError, match="breathe_run_s: 10 s fits fewer than two 7.5 s steps") as e:
        _check_stepped(too_short, MODES)
    assert "make it at least 15 s" in str(e.value)
    still = settings_with(motion={"idle": {"breathe_run_s": 60, "every_s": 300}})  # Calm, no rung
    with pytest.raises(ConfigError, match="has no stepped rung to breathe with"):
        _check_stepped(still, MODES)


def test_a_run_must_end_before_the_idle_hold_and_motion_idle_needs_an_idle_rung():
    _check_stepped(ConductorSettings(), MODES)  # 90 s runs, idle_return_s 180, a still idle mode
    short = settings_with(kiosk={"idle_return_s": 60})
    with pytest.raises(ConfigError, match="run_s: 90 s is longer than kiosk.idle_return_s"):
        _check_stepped(short, MODES)
    # D75: the idle look may carry a rung (start-up and an idle return still show it still, motion.idle off)
    _check_stepped(settings_with(kiosk={"idle_mode": "radial-outward"}), MODES)
    breathing_calm = settings_with(motion={"idle": {"breathe_run_s": 60, "every_s": 300}})
    with pytest.raises(ConfigError, match="'lowcountry-calm' .* has no stepped rung to breathe with"):
        _check_stepped(breathing_calm, MODES)


def test_the_show_checks_stepped_runs_against_the_kiosk_settings_when_it_loads(tmp_path):
    import shutil

    from conductor.config import CONFIG_DIR

    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    raw = yaml.safe_load(PATH.read_text(encoding="utf-8"))
    raw["modes"]["lowcountry-calm"] = CALM
    (config / "modes.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    # the fixture names today's scene colours: frozen over config's (Design Lab PLAN §3.7)
    recipes = yaml.safe_dump(RECIPES.model_dump(mode="json"), sort_keys=False)
    (config / "recipes.yaml").write_text(recipes, encoding="utf-8")
    ok = SimpleNamespace(config_dir=config, settings=ConductorSettings())
    assert "radial-outward" in ShowWiring(ok).modes_file.modes
    short = SimpleNamespace(config_dir=config, settings=settings_with(kiosk={"idle_return_s": 60}))
    with pytest.raises(ConfigError, match="idle_return_s"):
        ShowWiring(short)
