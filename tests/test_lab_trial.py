"""The Design Lab's try-on (D66 Slice B, docs/design-lab/PLAN.md §6): a draft of one scene on the real lights at module
level, through the canopy engine, on the virtual clock.

Two harnesses:
- the engine on `tests/fake_compiler.py` (`make_world`), with a compile wrapper that marks a plan compiled while the
  try-on of its mode stands at T3/strand ("(try-on)" in its label), exactly where `ShowWiring.compile` uses the
  trial's patched config — so these tests see whether a look was compiled from the trial or from git;
- the app's own show wiring (`build_services` + `ShowWiring` over the shipped `config/`, the simulator on virtual
  time, as tests/test_stepped_rehearsal.py builds it), with the Lab's routes on a small ASGI app over the same
  services where a route is under test.

What must hold (PLAN §6, D66): the try-on compiles the draft (its own modes AND recipes) only at the modules and
strand tiers of its scene; every slot path keeps the git config, so no slot is written or judged stale; it is one
burst; it is refused while stopped, commissioning, paused, resting or rehearsing; the server keeps applies
>= lab.apply_min_gap_s apart; guests are refused with a true sentence; it ends at min(lab.trial_max_s,
kiosk.idle_return_s), on "Back to approved", and before anything else compiles its own look; a failed clear puts the
approved look back; it is never persisted; and the show's cached config is never mutated.
"""

from __future__ import annotations

import asyncio
import dataclasses
import math
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import anyio.to_thread
import httpx
import pytest
from fake_compiler import MODULES, make_world, settings_with
from lab_edits import NEW_PINK, done_means, other_hex
from pydantic import ValidationError

from conductor import rows as R
from conductor.canopy import (
    COMMISSIONING_MESSAGE,
    RECOMPILE_SOURCES,
    RESTING_MESSAGE,
    STOPPED_MESSAGE,
    TRIAL_CHANGED_MESSAGE,
    TRIAL_ENDING_MESSAGE,
    TRIAL_MESSAGE,
    TRIAL_PAUSED_MESSAGE,
    TRIAL_REHEARSAL_MESSAGE,
    TRIAL_SKIP,
    Trial,
)
from conductor.clock import VirtualClock, run_virtual
from conductor.config import CONFIG_DIR, ConfigError, load_settings, trial_bound_s
from conductor.design_lab import draft as D
from conductor.design_lab import service as SVC
from conductor.design_lab import trial as T
from conductor.engine_api import ModePlan, ModuleTarget, PlanError, Program, ProgramStep, Tier
from conductor.looks import compile_mode
from conductor.rehearsal import RehearsalRefused
from conductor.scenes import load_scenes
from conductor.sim.settings import SimSettings

TRIO = D.load_trio(CONFIG_DIR)
QUIET_S = 70.0  # start-up (capability reads, simulator commissioning, the idle look) is over
TRIAL_LABEL = " (try-on)"
CALM = "lowcountry-calm"
GOLDEN = "golden-hour"


def draft(scene_id: str, **changes: Any) -> D.SceneDraft:
    """The approved scene's seed draft with dotted-path changes (`palette.2.hex`, `layout`, …)."""
    data = D.seed_draft(TRIO, scene_id).model_dump(mode="json")
    for key, value in changes.items():
        parts = key.split(".")
        target = data
        for p in parts[:-1]:
            target = target[int(p)] if p.isdigit() else target[p]
        target[parts[-1]] = value
    return D.SceneDraft.model_validate(data)


def golden_both() -> D.SceneDraft:
    """Golden Hour with a hex AND the layout changed (radial → linear): a T1 wrongly compiled from it hashes
    differently (a hex-only draft keeps the stable recipe keys, and the same T1 hash would hide that mutation)."""
    return draft(GOLDEN, **done_means(D.seed_draft(TRIO, GOLDEN).model_dump(mode="json")))


def golden_pink() -> D.SceneDraft:
    """The Done-means hex edit only: Sunset pink → #FF8FBF, its recipe key kept."""
    seed = D.seed_draft(TRIO, GOLDEN).model_dump(mode="json")
    return draft(GOLDEN, **{"palette.2.hex": other_hex(seed["palette"][2]["hex"])})


def calm_solid() -> D.SceneDraft:
    """Lowcountry Calm with its solid colour (Soft ivory, index 0) changed: its strand rung shows the draft's."""
    return draft(CALM, **{"palette.0.hex": "#F2E2C4"})


# ---- the engine harness (fake compiler) --------------------------------------------------------------------------
def trial_of(mode_id: str, now: float, *, bound: float = 150.0, label: str = "Draft") -> Trial:
    return Trial(
        mode_id,
        label,
        modes=None,
        recipes=None,
        started_mono=now,
        ends_mono=now + bound,
        started_at=1000.0 + now,
    )


class Compiles:
    """Wraps the fake compiler the way ShowWiring.compile seams the try-on: a T3/strand plan of the trial's mode
    compiled while the trial stands is marked, and every compile is logged with whether a trial stood."""

    def __init__(self, world):
        self.world = world
        self.base = world.engine.deps.compile
        self.log: list[tuple[str, Tier, str | None]] = []
        self.fail: dict[tuple[str, Tier], int] = {}  # (mode, tier) → how many more compiles fail
        # (mode, tier, trial standing?) → how many more compiles raise RuntimeError (a compiler bug, not a PlanError)
        self.boom: dict[tuple[str, Tier, bool], int] = {}
        world.engine.deps.compile = self

    def __call__(self, mode_id: str, tier: Tier) -> ModePlan:
        trial = self.world.engine.trial
        self.log.append((mode_id, tier, trial.mode_id if trial is not None else None))
        if self.fail.get((mode_id, tier), 0) > 0:
            self.fail[(mode_id, tier)] -= 1
            raise PlanError(f"{mode_id} at {tier} can't compile (test)")
        key = (mode_id, tier, trial is not None)
        if self.boom.get(key, 0) > 0:
            self.boom[key] -= 1
            raise RuntimeError(f"{mode_id} at {tier}: a compiler bug (test)")
        plan = self.base(mode_id, tier)
        if trial is not None and trial.mode_id == mode_id and tier in (Tier.T3, Tier.STRAND):
            plan = dataclasses.replace(plan, label=plan.label + TRIAL_LABEL)
        return plan


def from_trial(plan: ModePlan | None) -> bool:
    return plan is not None and plan.label.endswith(TRIAL_LABEL)


async def started_world(**kw):
    w = make_world(**kw)
    compiles = Compiles(w)
    await w.engine.start()
    await w.engine.wait_idle()
    return w, compiles


def commands(w) -> list[dict]:
    return [e for e in w.events("command")]


def stepped_compile(device_ids: list[str]):
    """A stand-in stepped plan (the rehearsal's) for the fake world."""

    def look(rgb):
        return R.full_state_vector(R.Look("rgb", rgb=rgb, brightness=80))

    def compile(mode_id: str) -> ModePlan:
        steps = [
            ProgramStep(index=i, rows={d: look(rgb) for d in device_ids}, colours={"r0c0": "#000000"})
            for i, rgb in enumerate([(250, 120, 40), (40, 120, 250)])
        ]
        program = Program("stepped", {}, "forward", 4.0, 8.0, steps, "Colour through the cloud (test).")
        targets = {d: ModuleTarget(d, look((250, 120, 40)), look((250, 120, 40))) for d in device_ids}
        return ModePlan(mode_id, "Stepped", Tier.STEPPED, "test", False, targets, program=program)

    return compile


# ---- settings -----------------------------------------------------------------------------------------------------
def test_trial_max_s_is_validated_against_idle_return_s(tmp_path):
    with pytest.raises(
        ValidationError, match=r"lab.trial_max_s \(150 s\) must be at most kiosk.idle_return_s"
    ):
        settings_with(kiosk={"idle_return_s": 100}, lab={"trial_max_s": 150})
    with pytest.raises(ValidationError):
        settings_with(lab={"trial_max_s": 0})
    text = (CONFIG_DIR / "conductor.yaml").read_text().replace("idle_return_s: 180", "idle_return_s: 120")
    (tmp_path / "conductor.yaml").write_text(text)
    with pytest.raises(ConfigError, match="trial_max_s"):
        load_settings(tmp_path)
    assert settings_with(kiosk={"idle_return_s": 150}, lab={"trial_max_s": 150}).lab.trial_max_s == 150


def test_the_bound_is_the_smaller_of_the_two():
    assert trial_bound_s(load_settings(CONFIG_DIR)) == 150  # the shipped setting (≤ idle_return_s 180)
    assert trial_bound_s(settings_with()) == 180  # None = kiosk.idle_return_s
    assert trial_bound_s(settings_with(kiosk={"idle_return_s": 30})) == 30  # old tests' settings stay valid
    assert trial_bound_s(settings_with(lab={"trial_max_s": 45})) == 45


# ---- the engine: refusals ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("state", ["stopped", "commissioning", "paused", "outage"])
def test_start_is_refused_in_outage_pause_commissioning_stopped_and_rehearsal(state):
    """(The rehearsal case runs on the show: test_start_is_refused_while_a_stepped_rehearsal_runs.)"""
    expected = {
        "stopped": (STOPPED_MESSAGE, None),
        "commissioning": (COMMISSIONING_MESSAGE, 30.0),
        "paused": (TRIAL_PAUSED_MESSAGE, None),
        "outage": (RESTING_MESSAGE, 10),
    }[state]

    async def main():
        w, _ = await started_world()
        engine = w.engine
        task = None
        if state == "stopped":
            await engine.stop()
        elif state == "commissioning":
            task = asyncio.ensure_future(engine.commission(source="ops"))
            await asyncio.sleep(0)
            assert engine.commissioning
        elif state == "paused":
            engine.pause_guest_input(True, source="ops")
        else:
            engine.enter_outage("test")
        before = (len(commands(w)), engine.current_plan())
        st = await engine.start_trial(trial_of("sunset", w.clock.monotonic()))
        after = (len(commands(w)), engine.current_plan(), engine.trial)
        lab = w.events("lab")
        if task is not None:
            await task
        await w.close()
        return st, before, after, lab

    st, before, after, lab = run_virtual(main)
    assert st.refused and (st.message, st.retry_after_s) == expected
    assert after == (*before, None)  # nothing installed, nothing sent, no trial
    assert lab == []  # refused up front: no try-on was ever set (and cleared)


# ---- the engine: one burst, the bound, re-applies ----------------------------------------------------------------
def test_a_trial_lands_as_one_burst_with_the_ripple_at_1_2_s():
    async def main():
        w, compiles = await started_world(settings=settings_with(kiosk={"ripple_stagger_s": 1.2}))
        base = compiles.base

        def with_rings(mode_id, tier):  # the fake plans have no rings: give them three
            plan = base(mode_id, tier)
            return dataclasses.replace(plan, waves=[MODULES[:3], MODULES[3:6], MODULES[6:]])

        compiles.base = with_rings
        await w.engine.request_mode("holiday", source="ops")  # control: an ops mood does ripple here
        rippled = w.engine._ripple is not None
        await w.run_for(5)
        await w.engine.wait_idle()
        sends: list[tuple[Any, str]] = []
        original = w.engine.send

        def spy(device_ids, *, source, label):
            sends.append((None if device_ids is None else list(device_ids), source))
            return original(device_ids, source=source, label=label)

        w.engine.send = spy
        n = len(commands(w))
        st = await w.engine.start_trial(trial_of("sunset", w.clock.monotonic()))
        one_burst = (w.engine._ripple, dict(w.engine._pending))
        await w.run_for(3)  # past every ring's time
        await w.engine.wait_idle()
        lab = [e for e in commands(w)[n:] if e.get("source") == "lab"]
        # "Back to approved" is a lab-source request too, and so is any request_mode with source "lab"
        await w.engine.end_trial(source="ops")
        back = (w.engine._ripple, dict(w.engine._pending))
        await w.run_for(3)
        await w.engine.request_mode("holiday", source="lab")
        direct = (w.engine._ripple, dict(w.engine._pending))
        await w.close()
        return rippled, st, one_burst, sends, lab, back, direct

    rippled, st, one_burst, sends, lab, back, direct = run_virtual(main)
    assert rippled, "the control: the ripple is on, and the plans have rings"
    assert not st.refused and one_burst == back == direct == (None, {})
    # the try-on, "Back to approved" and the direct request: each one send to every module (one burst)
    assert [s for s in sends if s[1] == "lab"] == [(None, "lab")] * 3
    assert sorted(e["device_id"] for e in lab) == sorted(f"sim-{d}" for d in MODULES)  # every module, once


def test_reapplying_keeps_the_sessions_bound():
    async def main():
        w, _ = await started_world()
        engine = w.engine
        t0 = w.clock.monotonic()
        await engine.start_trial(trial_of("sunset", t0, bound=60, label="A"))
        first, task = engine.trial, engine._trial_task
        await w.run_for(10)
        st = await engine.start_trial(trial_of("sunset", w.clock.monotonic(), bound=60, label="B"))
        second, task2 = engine.trial, engine._trial_task
        await w.run_for(49)
        mid = engine.trial
        await w.run_for(2)
        end = (engine.trial, engine.current_plan())
        await w.close()
        return t0, first, task, st, second, task2, mid, end

    t0, first, task, st, second, task2, mid, end = run_virtual(main)
    assert not st.refused
    assert second.label == "B" and second.applies == 2 and second.session == first.session
    assert (second.started_mono, second.ends_mono) == (t0, t0 + 60)  # never extended by the re-apply
    assert task2 is task  # the same bound task
    assert mid is not None and mid.label == "B"
    trial, plan = end
    assert trial is None and plan.mode_id == "sunset" and not from_trial(plan) and plan.tier is Tier.T1


def test_the_bound_task_never_cancels_itself_and_stop_awaits_it():
    async def main():
        w, _ = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic(), bound=20))
        task = engine._trial_task
        during = engine.current_plan()
        await w.run_for(21)
        await engine.wait_idle()
        ended = (task.done(), task.cancelled(), engine.trial, engine.current_plan(), engine._trial_task)
        await engine.start_trial(trial_of("sunset", w.clock.monotonic(), bound=20))
        task2 = engine._trial_task
        await engine.stop()
        stopped = (task2.done(), task2.cancelled(), engine.trial)
        await w.executor.close()
        return during, ended, stopped, w.events("lab")

    during, ended, stopped, events = run_virtual(main)
    assert from_trial(during) and during.tier is Tier.T3
    done, cancelled, trial, plan, left = ended
    assert done and not cancelled, "the bound's own task ran to its end (it never cancelled itself mid-way)"
    assert (
        trial is None and left is None and plan.tier is Tier.T1 and not from_trial(plan)
    )  # the approved look
    assert stopped == (True, True, None)  # stop() cancelled and awaited the second session's bound
    ends = [e["why"] for e in events if e["action"] == "trial_end"]
    assert ends == ["it reached its time limit (20 s)", "the canopy engine stopped"]


def test_stop_awaits_the_bound_task_itself():
    """stop() cancels AND awaits the bound task: with nothing else left to yield (the burst drained, a reconciler
    stop that never yields), the task is finished the moment stop() returns."""

    async def main():
        w, _ = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic(), bound=20))
        await engine.wait_idle()
        task = engine._trial_task

        async def quiet_stop() -> None:
            return None

        engine.reconciler.stop = quiet_stop
        await engine.stop()
        out = (task.done(), task.cancelled(), engine.trial, engine._trial_task)
        await w.executor.close()
        return out

    assert run_virtual(main) == (True, True, None, None)


def test_a_stale_bound_task_ignores_a_new_session():
    async def main():
        w, _ = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic(), label="A"))
        old = engine.trial.session
        await engine.end_trial(source="ops")
        await engine.start_trial(trial_of("sunset", w.clock.monotonic(), label="B"))
        await engine._end_trial_at_bound(old)  # an ended session's bound fires late
        still = engine.trial
        await w.close()
        return old, still

    old, still = run_virtual(main)
    assert still is not None and still.label == "B" and still.session == old + 1


def test_a_failed_reapply_keeps_the_running_trial():
    async def main():
        w, compiles = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic(), label="A"))
        await engine.wait_idle()
        plan = engine.current_plan()
        compiles.fail[("sunset", Tier.T3)] = 1  # the re-applied draft doesn't compile at module level
        st = await engine.start_trial(trial_of("sunset", w.clock.monotonic(), label="B"))
        out = (st, engine.trial, engine.current_plan() is plan, engine._trial_task is not None)
        await w.close()
        return out

    st, trial, same_plan, armed = run_virtual(main)
    assert st.refused
    assert trial is not None and trial.label == "A" and trial.applies == 1  # as it was
    assert same_plan and armed


def test_a_first_start_refused_by_the_engine_leaves_no_trial():
    """The override is set before `_request`; when the engine then refuses (neither module level nor strand
    compiles: T1/T2/stepped are skipped for the trial scene), a FIRST start clears it again: no trial, no bound task,
    the approved look as it was, and guests aren't told a try-on runs."""

    async def main():
        w, compiles = await started_world()
        engine = w.engine
        plan = engine.current_plan()
        compiles.fail[("sunset", Tier.T3)] = 1
        compiles.fail[("sunset", Tier.STRAND)] = 1
        st = await engine.start_trial(trial_of("sunset", w.clock.monotonic()))
        after = (engine.trial, engine._trial_task, engine.current_plan() is plan, engine.trial_active)
        lab = w.events("lab")
        await w.run_for(30)  # past lab.apply_min_gap_s
        kiosk = await engine.request_mode("holiday", source="kiosk")
        await w.close()
        return st, after, lab, kiosk

    st, (trial, task, same_plan, active), lab, kiosk = run_virtual(main)
    assert st.refused
    assert trial is None and task is None and same_plan and active is False
    assert [e["action"] for e in lab] == ["trial_end"]
    assert lab[0]["why"].startswith("it couldn't start")
    assert kiosk.message != TRIAL_MESSAGE and not kiosk.refused


@pytest.mark.parametrize("source", sorted(RECOMPILE_SOURCES))
def test_a_re_pick_keeps_the_trial(source):
    """A re-pick (the reconciler after new capabilities, recovery, start-up) compiles the current mode again: it is
    not another source choosing a look, so it keeps the try-on (PLAN §6.2; D66 lists what ends one)."""

    async def main():
        w, _ = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic()))
        await engine.wait_idle()
        before = engine.trial
        st = await engine.request_mode("sunset", source=source)
        await engine.wait_idle()
        out = (st, before, engine.trial, engine.current_plan(), w.events("lab"))
        await w.close()
        return out

    st, before, trial, plan, lab = run_virtual(main)
    assert not st.refused
    assert trial is not None and trial.session == before.session and trial.ends_mono == before.ends_mono
    assert from_trial(plan) and plan.tier is Tier.T3
    assert "trial_end" not in [e["action"] for e in lab]


def test_the_kiosk_is_refused_by_the_engine_during_a_trial():
    async def main():
        w, _ = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic(), bound=100))
        await w.run_for(30)
        mood = await engine.request_mode("holiday", source="kiosk")
        level = await engine.set_guest_brightness(40, source="kiosk")
        power = await engine.set_guest_power(False, source="kiosk")
        out = (mood, level, power, engine.trial, engine.current_plan())
        await w.close()
        return out

    mood, level, power, trial, plan = run_virtual(main)
    for st in (mood, level, power):
        assert st.refused and st.message == TRIAL_MESSAGE and st.retry_after_s == pytest.approx(70, abs=0.6)
    assert trial is not None and from_trial(plan)  # the try-on carries on


# ---- the engine: clearing ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("how", ["request", "rehearse", "safe look"])
def test_a_refused_request_after_clearing_restores_the_approved_look(how):
    async def main():
        w, compiles = await started_world()
        engine = w.engine
        mode = CALM if how == "safe look" else "sunset"
        await engine.start_trial(trial_of(mode, w.clock.monotonic()))
        await engine.wait_idle()
        during = engine.current_plan()
        error = None
        if how == "request":  # ops chose a look that doesn't compile: refused after the override was cleared
            compiles.fail[("holiday", Tier.STRAND)] = 1
            st = await engine.request_mode("holiday", source="ops")
            assert st.refused
        elif (
            how == "rehearse"
        ):  # the preview passes; the stepped tier isn't on sunset's ladder → refused after
            engine.deps.rehearsal_compile = stepped_compile(MODULES)
            try:
                await engine.rehearse("sunset", 1, source="ops")
            except RehearsalRefused as e:
                error = e
            assert error is not None
        else:  # the safe look can't compile (twice: its strand rung, then its best plan)
            compiles.fail[(CALM, Tier.STRAND)] = 2
            st = await engine.safe_look(source="ops")
            assert st.refused
        await engine.wait_idle()
        out = (during, engine.trial, engine.current_plan())
        await w.close()
        return out

    during, trial, plan = run_virtual(main)
    assert from_trial(during)
    assert trial is None
    assert plan.mode_id == during.mode_id and not from_trial(plan), "the approved look is back"


@pytest.mark.parametrize("source", ["ops", "idle"])
def test_another_source_choosing_the_trials_own_mode_compiles_it_from_git(source):
    """The override is cleared BEFORE compile_best: ops (or the idle return) choosing the very mode on trial gets its
    approved look at its best tier, never the trial's module-level plan."""

    async def main():
        w, compiles = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic()))
        n = len(compiles.log)
        st = await engine.request_mode("sunset", source=source)
        out = (st, engine.trial, engine.current_plan(), compiles.log[n:])
        await w.close()
        return out

    st, trial, plan, log = run_virtual(main)
    assert not st.refused and trial is None
    assert plan.tier is Tier.T1 and not from_trial(plan)
    assert log and all(standing is None for _m, _t, standing in log), log


def test_the_idle_return_clears_the_override():
    """The reconciler's idle hold asks for the idle mode with source "idle", which clears the try-on in the same step.
    (The route bounds a try-on to ≤ idle_return_s, so on the show the bound comes first; the engine itself doesn't
    rely on it.)"""

    async def main():
        w, _compiles = await started_world(settings=settings_with(kiosk={"idle_return_s": 30}))
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic(), bound=120))
        during = engine.current_plan()
        await w.run_for(31)
        out = (during, engine.trial, engine.current_plan(), w.events("lab"), w.events("mode"))
        await w.close()
        return out

    during, trial, plan, lab, modes = run_virtual(main)
    assert from_trial(during) and during.mode_id == "sunset"
    assert trial is None and plan.mode_id == CALM and not from_trial(plan)
    assert [e["why"] for e in lab if e["action"] == "trial_end"] == [f"idle chose {CALM}"]
    assert modes[-1]["source"] == "idle"


def test_pause_clears_the_trial_and_puts_the_approved_look_back():
    async def main():
        w, _ = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic()))
        engine.pause_guest_input(True, source="ops")
        out = (engine.trial, engine.current_plan())
        await w.close()
        return out

    trial, plan = run_virtual(main)
    assert trial is None and plan.mode_id == "sunset" and plan.tier is Tier.T1 and not from_trial(plan)


def test_commission_clears_the_trial_and_repicks_without_t1_until_its_replan():
    async def main():
        w, _compiles = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic()))
        installed: list[tuple[Tier, bool]] = []
        original = engine._install

        def spy(plan, source):
            installed.append((plan.tier, engine.commissioning))
            return original(plan, source)

        engine._install = spy
        report = await engine.commission(source="ops")
        await w.run_for(2)  # _replan_soon
        out = (report, engine.trial, engine.current_plan(), installed)
        await w.close()
        return out

    report, trial, plan, installed = run_virtual(main)
    assert report["ok"] and trial is None
    assert installed[0][0] is Tier.T3, "the approved look is re-picked without T1/T2/stepped"
    assert plan.tier is Tier.T1 and not from_trial(plan)  # lifted by the replan afterwards


def test_the_trial_skips_t1_t2_and_stepped_for_its_mode_only():
    async def main():
        w, _ = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic()))
        best_trial, _ = engine.compile_best("sunset")
        best_other, _ = engine.compile_best("golden")
        out = (best_trial, best_other)
        await w.close()
        return out

    best_trial, best_other = run_virtual(main)
    assert TRIAL_SKIP == {Tier.T1, Tier.T2, Tier.STEPPED}
    assert best_trial.tier is Tier.T3 and from_trial(best_trial)
    assert best_other.tier is Tier.T1  # another mode keeps its ladder


# ---- the show harness (the shipped config/, ShowWiring, the simulator on virtual time) ---------------------------
class Show:
    def __init__(self, services, wiring, engine, lab, data: Path, owned: bool):
        self.services, self.wiring, self.engine, self.lab, self.data = services, wiring, engine, lab, data
        self.clock = services.clock
        self.owned = owned

    def events(self, kind: str) -> list[dict]:
        return [e for e in self.services.telemetry.ring if e["kind"] == kind]

    async def run_for(self, seconds: float) -> None:
        await self.clock.sleep(seconds)

    def alerts(self) -> list[Any]:
        from conductor.alerts import AlertBoard

        return AlertBoard.of(self.services).active()

    async def close(self) -> None:
        await self.wiring.close()
        await self.services.close()
        if self.owned:
            shutil.rmtree(self.data, ignore_errors=True)


async def show(
    *, live: bool = False, monkeypatch=None, data: Path | None = None, prepare=None, quiet: bool = True
) -> Show:
    """The show on virtual time; `quiet=False` returns right after `ShowWiring.start()`, before any capability read
    (and so before the start-up idle look)."""
    from conductor.alerts import AlertBoard
    from conductor.services import build_services
    from conductor.show import ShowWiring

    owned = data is None
    data = data or Path(tempfile.mkdtemp(prefix="canopy-trial-"))
    sim = SimSettings.from_acceptance().with_(throttle_429_rate=0.0, silent_drop_rate=0.0)
    services = build_services(
        {"CONDUCTOR_DATA_DIR": str(data)}, clock=VirtualClock(), config_dir=CONFIG_DIR, sim_settings=sim
    )
    services.telemetry.dir = None
    AlertBoard.of(services)
    if live:  # the show box's tier rules over the simulator's transport
        monkeypatch.setattr(services.engine_driver, "simulated", False)
    await services.start()
    if prepare is not None:
        prepare(services)
    wiring = ShowWiring(services)
    services.extras["show"] = wiring
    engine = await wiring.start()
    lab = SVC.LabService(services)
    services.extras[SVC.KEY] = lab
    w = Show(services, wiring, engine, lab, data, owned)
    if quiet:
        await w.run_for(QUIET_S)
    return w


async def try_on(w: Show, d: D.SceneDraft):
    trial = T.build_trial(w.lab, d, version=None, now_mono=w.clock.monotonic(), now=w.clock.now())
    st = await w.engine.start_trial(trial)
    await w.engine.wait_idle()
    return st


def patched(w: Show, d: D.SceneDraft) -> D.ConfigTrio:
    return D.apply_draft(TRIO, d, canopy=w.lab.canopy, settings=w.services.settings)


def rows_of(plan: ModePlan) -> dict[str, list]:
    return {d: list(mt.rows) for d, mt in plan.modules.items()}


def git_rows(w: Show, mode_id: str, tier: Tier) -> dict[str, list]:
    return rows_of(compile_mode(mode_id, tier, w.wiring.context()))


def draft_rows(w: Show, d: D.SceneDraft, tier: Tier) -> dict[str, list]:
    trio = patched(w, d)
    ctx = dataclasses.replace(w.wiring.context(trio.modes), recipes=trio.recipes)
    return rows_of(compile_mode(d.scene_id, tier, ctx))


DEV = "canopy-r0c0"


def t1_hash(w: Show) -> str:
    owner = w.engine.t1_target(DEV, "custom-3")
    assert owner is not None, "custom-3 has no T1 owner"
    return owner[0].modules[DEV].slot_hash


def test_a_golden_hour_trial_makes_no_slot_write_and_no_stale_slot_alert_over_20_virtual_minutes():
    """Sim start-up commissioned custom-1..3 (origin "api"). A try-on of Golden Hour whose draft changes a hex AND
    the layout, run across its bound, a second try-on and "Back to approved", for 20 virtual minutes: not one slot
    object goes to a module, nothing is judged stale, custom-3's T1 design stays git's. Capabilities are re-read
    during each try-on (the T1 plans are compiled again then), so a T1 wrongly compiled from the draft would show."""

    async def main():
        w = await show()
        engine = w.engine
        writes: list[tuple[float, str, list[str]]] = []
        driver = w.services.engine_driver
        original = driver.write

        async def spy(address, rows, **kw):
            writes.append((w.clock.monotonic(), address, [r.function_class for r in rows]))
            return await original(address, rows, **kw)

        driver.write = spy
        t0 = w.clock.monotonic()
        records = {e.id: engine.recorded_slot(e.id, "custom-3") for e in w.services.registry.canopy_modules()}
        before = (t1_hash(w), dict(engine.slot_written))
        st = await try_on(w, golden_both())
        tier = engine.current_plan().tier
        await w.run_for(60)
        engine.invalidate_t1_plans()  # a capability re-read during the try-on
        await w.run_for(30)
        during = t1_hash(w)
        await w.run_for(70)  # past the 150 s bound
        ended = (engine.trial, engine.current_plan().tier)
        await w.run_for(140)
        st2 = await try_on(w, golden_both())
        engine.invalidate_t1_plans()
        await w.run_for(100)
        back = await engine.end_trial(source="ops")
        await w.run_for(1200 - (w.clock.monotonic() - t0))
        after = (t1_hash(w), dict(engine.slot_written))
        alerts = [e["message"] for e in w.events("alert")]
        board = [a.key for a in w.alerts()]
        await w.close()
        return records, before, st, tier, during, ended, st2, back, after, writes, alerts, board

    records, before, st, tier, during, ended, st2, back, after, writes, alerts, board = run_virtual(main)
    assert all(r is not None and r["origin"] == "api" for r in records.values()), "sim start-up commissioned"
    assert not st.refused and not st2.refused and not back.refused
    assert tier is Tier.T3
    assert ended == (None, Tier.T1)  # the bound ended it; the approved look is back at canvas (T1)
    assert during == before[0] and after[0] == before[0]  # custom-3's T1 design is git's, during and after
    assert after[1] == before[1]  # engine.slot_written unchanged
    slot_writes = [w for w in writes if R.FRAME_CLASS in w[2]]
    assert slot_writes == [], slot_writes
    assert writes, "the control: the try-ons and the approved looks did go out"
    assert not [m for m in alerts if re.search(r"pattern in custom-", m)], alerts
    assert not [k for k in board if k.startswith("slot:")], board


def test_the_trial_ladder_is_modules_then_strand_and_t1_keeps_the_git_design():
    async def main():
        w = await show()
        engine = w.engine
        before = t1_hash(w)
        d = golden_both()
        await try_on(w, d)
        engine.invalidate_t1_plans()
        rungs = [t for t in w.wiring.ladder(GOLDEN) if t not in TRIAL_SKIP]
        during = (t1_hash(w), engine.current_plan().tier, rungs)
        down = engine.downgrade(
            "test: throttled"
        )  # the next rung down is strand, compiled from the draft too
        strand = (down, engine.current_plan(), T.trial_json(w.lab))
        expected = draft_rows(w, d, Tier.STRAND), git_rows(w, GOLDEN, Tier.STRAND)
        await w.close()
        return before, during, strand, expected

    before, during, strand, expected = run_virtual(main)
    hash_during, tier, rungs = during
    assert hash_during == before and tier is Tier.T3
    assert rungs == [Tier.T3, Tier.STRAND]
    down, plan, trial = strand
    assert down and plan.tier is Tier.STRAND
    assert rows_of(plan) == expected[0] != expected[1]  # the draft's solid, not git's
    assert trial["tier"] == "strand" and trial["message"].startswith(
        "Trying on as one colour on every strand"
    )


def test_the_trial_shows_the_drafts_colours_not_the_approved_ones():
    async def main():
        w = await show()
        d = golden_pink()
        approved = git_rows(w, GOLDEN, Tier.T3)
        st = await try_on(w, d)
        plan = w.engine.current_plan()
        out = (st, plan, approved, draft_rows(w, d, Tier.T3), T.trial_json(w.lab), d)
        await w.close()
        return out

    st, plan, approved, wanted, trial, d = run_virtual(main)
    approved_pink = D.seed_draft(TRIO, GOLDEN).palette[2]
    assert d.palette[2].name == approved_pink.name == "Sunset pink"  # the recipe key is kept
    assert d.palette[2].hex != approved_pink.hex and NEW_PINK in (d.palette[2].hex, approved_pink.hex)
    assert not st.refused and plan.tier is Tier.T3
    assert rows_of(plan) == wanted and wanted != approved  # the draft's new pink, not the approved one
    assert trial["tier"] == "modules" and trial["message"] == T.PAINTED_MESSAGE
    assert trial["scene_id"] == GOLDEN and len(trial["modules"]) == 9 and trial["applies"] == 1
    assert trial["bound_s"] == 150 and trial["cost"] == "sends 9 writes now, up to ~36 over the next 10 s"


def test_a_renamed_colour_trial_lands_on_modules_not_strand():
    """A renamed colour gets a new recipe key, which only the draft's recipes have: without them the module-level
    rung wouldn't compile and the try-on would drop to strand."""

    async def main():
        w = await show()
        d = draft(GOLDEN, **{"palette.2.name": "Coral blush"})
        st = await try_on(w, d)
        out = (st, w.engine.current_plan(), draft_rows(w, d, Tier.T3), T.trial_json(w.lab))
        await w.close()
        return out

    st, plan, wanted, trial = run_virtual(main)
    assert not st.refused and plan.tier is Tier.T3 and rows_of(plan) == wanted
    assert trial["tier"] == "modules"


def test_a_trial_never_steps():
    async def main():
        w = await show()
        d = draft("coastal-tides", **{"palette.1.hex": "#6FD0D8"})
        st = await try_on(w, d)
        await w.run_for(20)
        out = (st, w.engine.current_plan().tier, w.engine.runner, w.engine.state().program)
        await w.close()
        return out

    st, tier, runner, program = run_virtual(main)
    assert not st.refused and tier is Tier.T3 and runner is None and program is None


def test_a_capability_re_read_on_the_show_keeps_the_trial():
    """A module whose profile changes (it came back power-cycled) makes the show re-pick the current mode with source
    "reconciler" through the real capability loop: the try-on stays, at module level with the draft's colours, until
    its bound ends it."""

    async def main():
        w = await show()
        d = golden_pink()
        await try_on(w, d)
        before = w.engine.trial
        w.wiring.caps.pop(DEV)  # the next read is "changed"
        w.wiring.request_refresh([DEV])
        await w.run_for(5)
        await w.engine.wait_idle()
        during = (w.engine.trial, w.engine.current_plan(), [e for e in w.events("mode")])
        ends_during = [e["why"] for e in w.events("lab") if e["action"] == "trial_end"]
        await w.run_for(150)
        await w.engine.wait_idle()
        ends = [e["why"] for e in w.events("lab") if e["action"] == "trial_end"]
        out = (before, during, ends_during, ends, draft_rows(w, d, Tier.T3))
        await w.close()
        return out

    before, (trial, plan, modes), ends_during, ends, drafted = run_virtual(main)
    assert [m for m in modes if m.get("source") == "reconciler"], "the capability change re-picked the mode"
    assert trial is not None and trial.session == before.session
    assert plan.tier is Tier.T3 and rows_of(plan) == drafted
    assert ends_during == []
    assert ends == ["it reached its time limit (150 s)"]


def test_a_calm_trial_then_the_safe_look_shows_approved_calm():
    async def main():
        w = await show()
        d = calm_solid()
        approved = git_rows(w, CALM, Tier.STRAND)
        await try_on(w, d)
        during = (w.engine.current_plan().tier, draft_rows(w, d, Tier.STRAND))
        st = await w.engine.safe_look(source="ops")
        await w.engine.wait_idle()
        out = (approved, during, st, w.engine.trial, w.engine.current_plan())
        await w.close()
        return out

    approved, during, st, trial, plan = run_virtual(main)
    assert during[0] is Tier.T3 and during[1] != approved  # the draft's own solid differs (the control)
    assert not st.refused and trial is None
    assert plan.tier is Tier.STRAND and rows_of(plan) == approved


def test_rehearsing_the_trials_own_scene_starts_the_stepped_runner():
    async def main():
        w = await show()
        await try_on(w, golden_both())
        result = await w.engine.rehearse(GOLDEN, 1, source="ops")
        out = (result, w.engine.trial, w.engine.runner, w.engine.state().tier)
        w.engine.stop_rehearsal(source="ops")
        await w.run_for(10)
        await w.close()
        return out

    result, trial, runner, tier = run_virtual(main)
    assert result["ok"] and trial is None and runner is not None and tier == "stepped"


def test_start_is_refused_while_a_stepped_rehearsal_runs():
    async def main():
        w = await show()
        await w.engine.rehearse(GOLDEN, 1, source="ops")
        await w.run_for(5)
        st = await try_on(w, golden_pink())
        out = (st, w.engine.trial, w.engine.rehearsal_active)
        w.engine.stop_rehearsal(source="ops")
        await w.run_for(10)
        await w.close()
        return out

    st, trial, rehearsing = run_virtual(main)
    assert st.refused and st.message == TRIAL_REHEARSAL_MESSAGE and st.retry_after_s > 0
    assert trial is None and rehearsing


@pytest.mark.parametrize("source", ["ops", "idle", "safe look", "commission", "rehearse", "pause", "stop"])
def test_any_other_source_clears_the_override_before_compiling(source):
    """Each source's own look is compiled with the override already gone: the plan it installs is git's at its tier
    (a cleared-after-compile override would leave the draft's module-level plan up)."""

    async def main():
        w = await show()
        engine = w.engine
        d = calm_solid() if source in ("idle", "safe look") else golden_pink()
        await try_on(w, d)
        trial_rows = rows_of(engine.current_plan())
        if source == "ops":
            st = await engine.request_mode(GOLDEN, source="ops")
        elif source == "idle":
            st = await engine.request_mode(CALM, source="idle")
        elif source == "safe look":
            st = await engine.safe_look(source="ops")
        elif source == "commission":
            await engine.commission(source="ops")
            await w.run_for(5)
            st = engine.state()
        elif source == "rehearse":
            await engine.rehearse(GOLDEN, 1, source="ops")
            st = engine.state()
        elif source == "pause":
            st = engine.pause_guest_input(True, source="ops")
        else:
            await engine.stop()
            st = engine.state()
        await engine.wait_idle()
        plan = engine.current_plan()
        git = None if source in ("rehearse", "stop") else git_rows(w, plan.mode_id, plan.tier)
        out = (st, engine.trial, plan, trial_rows, git, engine.runner, w.events("lab"))
        if source == "rehearse":
            engine.stop_rehearsal(source="ops")
            await w.run_for(10)
        await w.close()
        return out

    st, trial, plan, trial_rows, git, runner, lab = run_virtual(main)
    assert trial is None
    assert [e["action"] for e in lab if e["action"].startswith("trial_")] == ["trial_start", "trial_end"]
    if source == "stop":
        return
    assert not getattr(st, "refused", False)
    if source == "rehearse":
        assert plan.tier is Tier.STEPPED and runner is not None
        return
    assert rows_of(plan) == git and rows_of(plan) != trial_rows
    expected_tier = {"idle": Tier.T3, "safe look": Tier.STRAND}.get(source, Tier.T1)
    assert plan.tier is expected_tier, (source, plan.tier)


def test_commissioning_during_a_trial_never_installs_t1_before_the_records_exist(monkeypatch):
    """Live tier rules (Gate C flags on every module, no slot records yet): commissioning re-picks the try-on's scene
    without T1 (the show's commissioning flag lets T1 through without records), and only the replan after it lifts
    the look to T1, once every module's custom-3 record exists."""

    def gate_c(services) -> None:
        for e in services.registry.canopy_modules():
            address = services.registry.address(e.id, services.engine_driver.name)
            services.update_registry(e.id, {"flags": {"t1_frames": True}, "flags_proven_on": address})

    async def main():
        w = await show(live=True, monkeypatch=monkeypatch, prepare=gate_c)
        engine = w.engine
        ids = [e.id for e in w.services.registry.canopy_modules()]

        def recorded() -> bool:
            return all(
                (r := engine.recorded_slot(d, "custom-3")) is not None and r["origin"] == "api" for d in ids
            )

        start = (recorded(), engine.current_plan().tier)
        st = await try_on(w, golden_pink())
        installed: list[tuple[str, Tier, bool]] = []
        original = engine._install

        def spy(plan, source):
            installed.append((plan.mode_id, plan.tier, recorded()))
            return original(plan, source)

        engine._install = spy
        report = await engine.commission(source="ops")
        await w.run_for(5)
        out = (start, st, report, installed, engine.trial, engine.current_plan().tier, recorded())
        await w.close()
        return out

    start, st, report, installed, trial, tier, done = run_virtual(main)
    assert start == (False, Tier.T3) and not st.refused  # live: no records, so nothing at T1 before
    assert report["ok"] and trial is None and done
    assert installed and installed[0][:2] == (GOLDEN, Tier.T3)
    for mode_id, t, had_records in installed:
        assert t is not Tier.T1 or had_records, f"{mode_id} installed at T1 before the records existed"
    assert tier is Tier.T1  # _replan_soon lifted it once the records existed


def test_a_lowcountry_calm_trial_ends_at_its_bound():
    """The idle hold skips the idle mode, so a Calm try-on would hold forever: its bound (150 s) ends it."""

    async def main():
        w = await show()
        d = calm_solid()
        approved = git_rows(w, CALM, Tier.T3)
        t0, m0 = w.clock.now(), w.clock.monotonic()
        await try_on(w, d)  # (waits for the burst's results: a few seconds)
        await w.run_for(149 - (w.clock.monotonic() - m0))
        mid = w.engine.trial
        await w.run_for(2)
        await w.engine.wait_idle()
        idle = [e for e in w.events("mode") if e["source"] == "idle" and e["t"] > t0]
        out = (approved, mid, w.engine.trial, w.engine.current_plan(), idle, w.events("lab"))
        await w.close()
        return out

    approved, mid, trial, plan, idle, lab = run_virtual(main)
    assert mid is not None and trial is None
    assert plan.mode_id == CALM and plan.tier is Tier.T3 and rows_of(plan) == approved
    assert idle == []  # the idle hold never ran: the bound ended it
    assert [e["why"] for e in lab if e["action"] == "trial_end"] == ["it reached its time limit (150 s)"]


def test_apply_never_mutates_its_inputs_or_the_show():
    """B part of the purity rule (§3.0): the show's modes file, the cached scene catalog the guest panel serves, the
    show's recipes and the Lab's approved trio are unchanged by a try-on, a re-apply and its end."""

    async def main():
        w = await show()

        def dumps():
            return (
                w.wiring.modes_file.model_dump(),
                load_scenes(CONFIG_DIR).model_dump(),
                w.wiring.context().recipes.model_dump(),
                w.lab.approved.dump(),
            )

        before = dumps()
        await try_on(w, golden_both())
        await w.run_for(10)
        await try_on(w, draft(GOLDEN, **{"palette.2.name": "Coral blush"}))
        await w.engine.end_trial(source="ops")
        await w.engine.wait_idle()
        after = dumps()
        await w.close()
        return before, after

    before, after = run_virtual(main)
    assert after == before


# ---- the routes (a small app over the show's services, on the virtual clock) ----------------------------------------
def lab_app(services):
    from fastapi import FastAPI

    from conductor.routes import design_lab, guest, kiosk

    app = FastAPI()
    for router in (design_lab.router, kiosk.router, guest.router):
        app.include_router(router)
    app.state.services = services
    return app


def client_of(w: Show) -> httpx.AsyncClient:
    assert getattr(anyio.to_thread.run_sync, "inline", False), "a route test on virtual time needs no_threads"
    transport = httpx.ASGITransport(app=lab_app(w.services))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def no_threads(monkeypatch):
    """Every threadpool hop of a request runs inline on the loop (after one yield), and no real thread may start.

    The virtual loop jumps its clock to the next timer whenever nothing is ready, so while a worker thread computes,
    virtual time runs on for as long as the thread takes in REAL time: on a slow machine a request then costs virtual
    seconds. FastAPI runs every sync dependency (`require_ops`, `_no_cross_site`, `require_kiosk`, …) in the
    threadpool, as starlette's `run_in_threadpool` does the Lab's CPU work; both go through
    `anyio.to_thread.run_sync`, which is patched here (patching only the Lab's own `run_in_threadpool` left two hops
    per request on real threads). `threading.Thread.start` refuses, so a hop that bypasses anyio fails loudly here
    instead of drifting the clock on CI. With no thread, a request costs exactly the virtual time its own awaits take.

    One yield is NOT enough for a second request to reach the route meanwhile; a test that needs two requests to
    overlap gives the build virtual time (test_two_concurrent_starts_give_one_200_and_one_409)."""
    import threading

    async def inline(fn, *args, **_options):
        await asyncio.sleep(0)
        return fn(*args)

    inline.inline = True  # type: ignore[attr-defined]

    def no_start(thread):
        raise AssertionError(f"a real thread ({thread.name}) under virtual time would drift the clock")

    monkeypatch.setattr(anyio.to_thread, "run_sync", inline)
    monkeypatch.setattr(threading.Thread, "start", no_start)
    monkeypatch.delenv("CONDUCTOR_BASIC_AUTH", raising=False)


def start_body(d: D.SceneDraft, confirm: str | None = None) -> dict:
    body: dict[str, Any] = {"scene_id": d.scene_id, "draft": d.model_dump(mode="json")}
    if confirm is not None:
        body["confirm"] = confirm
    return body


async def confirmed(c: httpx.AsyncClient, d: D.SceneDraft) -> httpx.Response:
    asked = await c.post("/api/lab/trial/start", json=start_body(d))
    assert asked.status_code == 409 and asked.json()["confirm_required"], asked.text
    return await c.post("/api/lab/trial/start", json=start_body(d, asked.json()["confirm"]))


def test_the_server_side_apply_gap_holds(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            asked = await c.post("/api/lab/trial/start", json=start_body(golden_pink()))
            sentence = asked.json()["confirm"]
            first_sent = w.clock.monotonic()
            first = await c.post("/api/lab/trial/start", json=start_body(golden_pink(), sentence))
            first_back = w.clock.monotonic()
            await w.run_for(3)
            # a re-apply is confirmed against the running session's time left (B-TRUTH-1), then the gap holds
            again = (await c.post("/api/lab/trial/start", json=start_body(golden_both()))).json()["confirm"]
            second_sent = w.clock.monotonic()
            second = await c.post("/api/lab/trial/start", json=start_body(golden_both(), again))
            second_back = w.clock.monotonic()
            await w.run_for(5.01)
            third = await c.post("/api/lab/trial/start", json=start_body(golden_both(), again))
            got = await c.get("/api/lab/trial")
        out = (asked, first, second, third, got, w.events("lab"))
        out += ((first_sent, first_back, second_sent, second_back),)
        await w.close()
        return out

    asked, first, second, third, got, lab, (first_sent, first_back, second_sent, second_back) = run_virtual(
        main
    )
    body = asked.json()
    assert (
        body["detail"] == body["confirm"]
        and body["cost"] == "sends 9 writes now, up to ~36 over the next 10 s"
    )
    assert body["confirm"].startswith(
        "Try on your draft of Golden Hour at module level on 9 modules (Module 1,"
    )
    assert body["confirm"].endswith("for up to 150 s. The painted design isn't on the lights.")
    assert asked.headers["cache-control"] == "no-store"
    assert first.status_code == 200, first.text
    trial = first.json()["trial"]
    assert trial["tier"] == "modules" and trial["applies"] == 1 and trial["next_apply_in_s"] == 8
    assert first.json()["state"]["mode"] == GOLDEN
    # the gap was stamped during the first start and checked during the second: 8 − 3 is what's left, give or take
    # only the virtual time those two requests took themselves (none today: they await no timer), rounded to 0.01
    left = second.json()["retry_after_s"]
    assert second.status_code == 409, second.text
    assert 8 - (second_back - first_sent) - 0.005 <= left <= 8 - (second_sent - first_back) + 0.005
    assert second_sent - first_back >= 3
    assert second.headers["retry-after"] == str(math.ceil(left))
    assert third.status_code == 200, third.text
    assert third.json()["trial"]["applies"] == 2 and got.json()["applies"] == 2
    assert [e.get("why") for e in lab if e["action"] == "trial_refused"] == ["apply gap"]
    starts = [e["action"] for e in lab if e["action"] in ("trial_start", "trial_apply")]
    assert starts == ["trial_start", "trial_apply"]


def test_two_concurrent_starts_give_one_200_and_one_409(no_threads, monkeypatch):
    """The two POSTs really overlap: the build takes virtual time, so the second request reaches the gate while the
    first is still inside build_trial (a gap checked before the build and stamped after it lets both through)."""
    from conductor.routes import design_lab as routes

    async def slow(fn, *args, **kwargs):
        await asyncio.sleep(0.05)  # the virtual loop runs every ready request before it moves the clock
        return fn(*args, **kwargs)

    monkeypatch.setattr(routes, "run_in_threadpool", slow)
    monkeypatch.setattr(SVC, "run_in_threadpool", slow)

    async def main():
        w = await show()
        async with client_of(w) as c:
            sentence = (await c.post("/api/lab/trial/start", json=start_body(golden_pink()))).json()[
                "confirm"
            ]
            body = start_body(golden_pink(), sentence)
            a, b = await asyncio.gather(
                c.post("/api/lab/trial/start", json=body), c.post("/api/lab/trial/start", json=body)
            )
        out = (a, b, w.engine.trial)
        await w.close()
        return out

    a, b, trial = run_virtual(main)
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    refused = a if a.status_code == 409 else b
    # the refused request reached the gate while the other was inside its 0.05 s build: at most that much is gone
    assert 7.95 <= refused.json()["retry_after_s"] <= 8 and refused.headers["retry-after"] == "8"
    assert trial is not None and trial.applies == 1


def test_a_refused_or_invalid_start_never_uses_up_the_gap(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            sentence = (await c.post("/api/lab/trial/start", json=start_body(golden_pink()))).json()[
                "confirm"
            ]
            w.engine.pause_guest_input(True, source="ops")
            paused = await c.post("/api/lab/trial/start", json=start_body(golden_pink(), sentence))
            w.engine.pause_guest_input(False, source="ops")
            locked = start_body(golden_pink(), sentence)
            locked["draft"]["layout"]["cycles"] = 2  # D63: cycles is locked to 1 in v1 (refused by the patch)
            invalid = await c.post("/api/lab/trial/start", json=locked)
            ok = await c.post("/api/lab/trial/start", json=start_body(golden_pink(), sentence))
        out = (paused, invalid, ok, w.events("lab"))
        await w.close()
        return out

    paused, invalid, ok, lab = run_virtual(main)
    assert paused.status_code == 409 and paused.json()["detail"] == TRIAL_PAUSED_MESSAGE
    assert invalid.status_code == 422 and invalid.json()["problems"], invalid.text
    assert ok.status_code == 200, ok.text  # neither refusal stamped the gap
    assert [e["why"] for e in lab if e["action"] == "trial_refused"] == [TRIAL_PAUSED_MESSAGE, "problems"]


def test_the_lab_state_catalog_and_stop_show_the_trial(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            idle = (await c.get("/api/lab/trial")).json()
            started = await confirmed(c, golden_pink())
            state = (await c.get("/api/lab/state")).json()
            catalog = (await c.get("/api/lab/catalog")).json()
            stopped = await c.post("/api/lab/trial/stop", json={})
            after = ((await c.get("/api/lab/trial")).json(), (await c.get("/api/lab/state")).json()["trial"])
            again = await c.post("/api/lab/trial/stop", json={})
        out = (idle, started, state, catalog, stopped, after, again, w.engine.current_plan(), w.events("lab"))
        await w.close()
        return out

    idle, started, state, catalog, stopped, after, again, plan, lab = run_virtual(main)
    assert idle is None and started.status_code == 200
    assert state["trial"]["scene_id"] == GOLDEN and state["trial"]["tier"] == "modules"
    states = {s["id"]: s["state"] for s in catalog["scenes"]}
    assert states[GOLDEN] == "in try-on" and states[CALM] == "approved"
    assert stopped.status_code == 200 and stopped.json()["trial"] is None
    assert stopped.json()["state"]["mode"] == GOLDEN
    assert after == (None, None) and again.status_code == 200
    assert plan.tier is Tier.T1  # the approved look (sim: canvas)
    actions = [e["action"] for e in lab if e["action"].startswith("trial_")]
    assert actions == ["trial_start", "trial_end", "trial_stop"]


def test_a_restart_mid_trial_comes_up_clean(no_threads):
    async def main():
        data = Path(tempfile.mkdtemp(prefix="canopy-trial-restart-"))
        try:
            w = await show(data=data)
            async with client_of(w) as c:
                drafts = {GOLDEN: golden_pink().model_dump(mode="json")}
                saved = await c.post("/api/lab/versions", json={"drafts": drafts})
                looks = (data / "overrides" / "looks.yaml").read_bytes()
                started = await confirmed(c, golden_pink())
            await w.close()  # the Conductor goes down mid-try-on
            w2 = await show(data=data, quiet=False)
            # before the first capability round, whose idle look would clear (and so hide) a carried-over trial
            async with client_of(w2) as c:
                boot = (w2.engine.trial, (await c.get("/api/lab/state")).json()["trial"])
            await w2.run_for(QUIET_S)
            async with client_of(w2) as c:
                state = (await c.get("/api/lab/state")).json()
            ends = [e for e in w2.events("lab") if e["action"] == "trial_end"]
            out = (
                saved,
                started,
                looks,
                w2.engine.trial,
                w2.engine.current_plan().mode_id,
                state,
                boot,
                ends,
            )
            out += ((data / "overrides" / "looks.yaml").read_bytes(),)
            await w2.close()
            return out
        finally:
            shutil.rmtree(data, ignore_errors=True)

    saved, started, looks, trial, mode, state, boot, ends, looks_after = run_virtual(main)
    assert boot == (None, None), "the restarted engine came up with a try-on"
    assert ends == [], "the second process ended a try-on it should never have had"
    assert saved.status_code == 201 and started.status_code == 200
    assert started.json()["trial"]["version"] == saved.json()["id"]  # the try-on was of the saved version
    assert trial is None and mode == CALM  # the idle look, no override
    assert state["trial"] is None and state["current"] == saved.json()["id"]
    assert looks_after == looks  # the try-on never touched the store


def test_the_kiosk_is_refused_during_a_trial_with_retry_after(no_threads):
    from conductor.guest_panel import GuestPanel
    from conductor.routes.kiosk import TRYING_ON

    async def main():
        w = await show()
        async with client_of(w) as c:
            assert (await confirmed(c, golden_pink())).status_code == 200
            await w.run_for(30)
            select = await c.post("/api/kiosk/select", json={"mode": "coastal-tides"})
            scene = await c.post("/api/kiosk/scene", json={"scene": "coastal-tides"})
            panel = GuestPanel.of(w.services)
            target = await panel.target()
            messages = []
            await target.brightness(40)
            await w.run_for(3)
            messages.append(("brightness", target.message))
            await target.power(False)
            await w.run_for(3)
            messages.append(("power", target.message))
        out = (select, scene, target.kind, messages, w.engine.trial, w.engine.current_plan().mode_id)
        await w.close()
        return out

    select, scene, kind, messages, trial, mode = run_virtual(main)
    for r in (select, scene):
        assert r.status_code == 503 and r.json()["detail"] == TRYING_ON, r.text
        assert 0 < r.json()["retry_after_s"] <= 60 and r.headers["retry-after"]
    assert kind == "canopy"
    assert messages == [("brightness", TRYING_ON), ("power", TRYING_ON)]
    assert trial is not None and mode == GOLDEN  # the try-on carries on


@pytest.mark.parametrize("how", ["back to approved", "bound", "ops look"])
def test_the_ipad_stops_saying_try_on_once_the_try_on_ends(no_threads, how):
    """A guest's brightness during a try-on is refused on the lane with TRYING_ON (the panel shows it under the
    title, and `waiting` says why guests wait). Once the try-on ends — Back to approved, its bound, an ops look —
    neither stays: the canopy is the guests' again (kiosk copy must be true). An accepted scene clears any older
    lane refusal too."""
    from conductor.guest_panel import NOT_REACHED, GuestPanel
    from conductor.routes.kiosk import TRYING_ON

    async def main():
        w = await show()
        async with client_of(w) as c:
            assert (await confirmed(c, golden_pink())).status_code == 200
            await w.run_for(10)
            level = await c.post("/api/kiosk/brightness", json={"level": 50})
            await w.run_for(3)
            during = (await c.get("/api/kiosk/panel")).json()["state"]
            if how == "back to approved":
                assert (await c.post("/api/lab/trial/stop", json={})).status_code == 200
            elif how == "bound":
                await w.run_for(150)
            else:
                assert not (await w.engine.request_mode("coastal-tides", source="ops")).refused
            await w.engine.wait_idle()
            await w.run_for(2)
            after = (await c.get("/api/kiosk/panel")).json()["state"]
            await w.run_for(30)
            target = await GuestPanel.of(w.services).target()
            target.message = NOT_REACHED  # an older lane refusal
            scene = await c.post("/api/kiosk/scene", json={"scene": "garden-gathering"})
            await w.engine.wait_idle()
            await w.run_for(10)
            later = (await c.get("/api/kiosk/panel")).json()["state"]
        out = (level, during, after, w.engine.trial, scene, later)
        await w.close()
        return out

    level, during, after, trial, scene, later = run_virtual(main)
    assert level.status_code == 200  # the lane takes it; the engine refuses it
    assert (during["phase"], during["message"], during["waiting"]) == ("ready", TRYING_ON, "try-on")
    assert trial is None
    assert after["phase"] == "ready" and after["message"] == "" and after["waiting"] is None, after
    assert scene.status_code == 200, scene.text
    assert later["scene"] == "garden-gathering" and later["message"] == "" and later["waiting"] is None, later


def test_the_ipad_stops_saying_testing_once_the_rehearsal_ends(no_threads):
    """The same for the stepped rehearsal's TESTING: true while it runs, gone once it stops."""
    from conductor.routes.kiosk import TESTING

    async def main():
        w = await show()
        async with client_of(w) as c:
            await w.engine.rehearse(GOLDEN, 1, source="ops")
            await w.run_for(5)
            level = await c.post("/api/kiosk/brightness", json={"level": 50})
            await w.run_for(3)
            during = (await c.get("/api/kiosk/panel")).json()["state"]
            w.engine.stop_rehearsal(source="ops")
            await w.run_for(10)
            after = (await c.get("/api/kiosk/panel")).json()["state"]
        out = (level, during, after, w.engine.rehearsal_active)
        await w.close()
        return out

    level, during, after, rehearsing = run_virtual(main)
    assert level.status_code == 200
    assert (during["message"], during["waiting"]) == (TESTING, "testing")
    assert not rehearsing and after["message"] == "" and after["waiting"] is None, after


def test_the_trial_routes_need_an_engine(app_client):
    for method, path, body in (
        ("GET", "/api/lab/trial", None),
        ("POST", "/api/lab/trial/start", start_body(golden_pink())),
        ("POST", "/api/lab/trial/stop", {}),
    ):
        r = (
            app_client.request(method, path, json=body)
            if body is not None
            else app_client.request(method, path)
        )
        assert r.status_code == 503 and r.json()["detail"] == T.NO_ENGINE, (method, path, r.text)


# ---- the confirm sentence and the cost line ----------------------------------------------------------------------
def test_the_confirm_sentence_names_the_driven_modules_and_the_cost_line():
    from types import SimpleNamespace

    from conductor.config import load_registry

    registry = load_registry(CONFIG_DIR)
    wiring = SimpleNamespace(
        context=lambda: SimpleNamespace(addresses={"canopy-r0c0": "a", "canopy-r0c1": "b"})
    )
    lab = SimpleNamespace(approved=TRIO, show=lambda: wiring, services=SimpleNamespace(registry=registry))
    modules = T.driven_modules(lab)
    assert [m.id for m in modules] == ["canopy-r0c0", "canopy-r0c1"]
    assert T.confirm_sentence(lab, golden_pink(), modules, 150) == (
        "Try on your draft of Golden Hour at module level on 2 modules (Module 1, Module 2) for up to 150 s. "
        "The painted design isn't on the lights."
    )
    renamed = draft(CALM, name="Calm evening")
    assert T.confirm_sentence(lab, renamed, modules[:1], 90) == (
        "Try on Calm evening for Lowcountry Calm at module level on 1 module (Module 1) for up to 90 s."
    )
    assert T.cost_line(2) == "sends 2 writes now, up to ~8 over the next 10 s"
    assert T.cost_line(1) == "sends 1 write now, up to ~4 over the next 10 s"


def test_a_renamed_drafts_trial_json_names_the_approved_scene_and_the_draft():
    """TrialJSON's `label` is the approved scene's name (what "Back to approved" puts back), `draft_name` the
    draft's: the installed plan is compiled from the patched modes, whose label is the draft's name."""

    async def main():
        w = await show()
        await try_on(w, draft(GOLDEN, name="Pinker Hour"))
        out = (T.trial_json(w.lab), w.engine.current_plan().label)
        await w.close()
        return out

    body, plan_label = run_virtual(main)
    assert plan_label == "Pinker Hour"  # why `label` can't be the installed plan's
    assert (body["label"], body["draft_name"], body["tier"]) == ("Golden Hour", "Pinker Hour", "modules")


def test_the_apply_gate_reserves_and_releases():
    gate = T.ApplyGate(8)
    assert gate.reserve(100.0) == 0.0
    assert gate.reserve(103.0) == 5.0 and gate.remaining(103.0) == 5.0
    gate.release()  # the apply at 100 was refused after all: no gap
    assert gate.reserve(103.0) == 0.0 and gate.reserve(110.0) == 1.0 and gate.reserve(111.0) == 0.0


def test_today_during_a_try_on_shows_the_installed_plan_not_the_painted_slots():
    """The preview's "today" must be true while a try-on runs: in the simulator a painted scene's today is the
    painted rung (the slots' approved design), but during its try-on the lights are at module level from the applied
    draft. Another scene's today is untouched, and "Back to approved" puts the painted rung back."""
    from conductor.design_lab import preview as P

    async def main():
        w = await show()
        d = golden_pink()
        before = P.today_view(w.lab, patched(w, d), GOLDEN)
        await try_on(w, d)
        plan = w.engine.current_plan()
        during = P.today_view(w.lab, patched(w, d), GOLDEN)
        other = P.today_view(w.lab, D.load_trio(CONFIG_DIR), CALM)
        await w.engine.end_trial(source="test")
        await w.engine.wait_idle()
        after = P.today_view(w.lab, patched(w, d), GOLDEN)
        out = (before, plan, during, other, after, w.lab.canopy, list(w.services.registry.canopy_modules()))
        await w.close()
        return out

    before, plan, during, other, after, canopy, modules = run_virtual(main)
    assert before["tier"] == "canvas" and before["heads"] == "approved"
    assert plan.tier is Tier.T3
    assert during["tier"] == "modules" and during["heads"] == "draft"
    assert during["note"].startswith(P.TRYING_ON_NOTE.format(how="at module level"))
    assert P.TRYING_ON_PAINTED in during["note"]
    from conductor.canopy_view import module_colours

    by_pos = {m["pos"]: m for m in during["lit"]["modules"]}
    for entry in modules:
        hexes = module_colours(canopy, entry, plan.modules[entry.id].colors)
        shown = by_pos[entry.module]
        assert shown["state"] == "lit"
        assert (shown["colors"] or [shown["color"]] * len(hexes)) == hexes
    assert P.TRYING_ON_NOTE.split("{")[0] not in (other["note"] or "")
    assert after["tier"] == "canvas" and after["heads"] == "approved"


# ---- a compile error that isn't a PlanError never strands the override or the try-on's plan (B-ENG-2) -----------
def alerts_of(w) -> list[str]:
    return [e["message"] for e in w.events("alert")]


@pytest.mark.parametrize("mode", ["sunset", CALM])
def test_a_compile_error_at_start_leaves_no_override_and_no_trial_plan(mode):
    """compile_best catches PlanError only: a compiler bug (here a RuntimeError) raised while the override stands
    must not leave it set with no bound task (guests told a try-on runs forever; a Calm try-on is never ended by the
    idle hold). The override is cleared, nothing of the try-on stays installed, an alert says so, and it re-raises."""
    tier = Tier.STRAND if mode == CALM else Tier.T3

    async def main():
        w, compiles = await started_world()
        engine = w.engine
        await w.run_for(30)
        plan = engine.current_plan()
        compiles.boom[(mode, tier, True)] = 1
        with pytest.raises(RuntimeError):
            await engine.start_trial(trial_of(mode, w.clock.monotonic()))
        after = (engine.trial, engine._trial_task, engine.current_plan(), alerts_of(w))
        await w.run_for(30)  # past lab.apply_min_gap_s
        kiosk = await engine.request_mode("holiday", source="kiosk")
        await w.close()
        return plan, after, kiosk

    plan, (trial, task, now, alerts), kiosk = run_virtual(main)
    assert trial is None and task is None
    assert not from_trial(now) and now.mode_id == plan.mode_id and now.tier is plan.tier
    assert any("try-on" in a and "RuntimeError" in a for a in alerts), alerts
    assert not kiosk.refused and kiosk.message != TRIAL_MESSAGE


def test_a_compile_error_on_a_reapply_ends_the_try_on_with_the_approved_look_back():
    async def main():
        w, compiles = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic(), label="A"))
        await engine.wait_idle()
        task = engine._trial_task
        assert from_trial(engine.current_plan())
        await w.run_for(10)
        compiles.boom[("sunset", Tier.T3, True)] = 1
        with pytest.raises(RuntimeError):
            await engine.start_trial(trial_of("sunset", w.clock.monotonic(), label="B"))
        await asyncio.sleep(0)
        out = (
            engine.trial,
            engine._trial_task,
            task.cancelled() or task.done(),
            engine.current_plan(),
            alerts_of(w),
        )
        await w.close()
        return out

    trial, task, old_task_done, plan, alerts = run_virtual(main)
    assert trial is None and task is None and old_task_done  # the bound task is cleared first
    assert plan.mode_id == "sunset" and not from_trial(plan) and plan.tier is Tier.T1
    assert any("try-on" in a and "RuntimeError" in a for a in alerts), alerts


@pytest.mark.parametrize("mode", ["sunset", CALM])
def test_a_compile_error_at_the_bound_still_puts_the_approved_look_back(mode):
    """At the bound end_trial clears the override, then the approved look's compile raises once: the approved look is
    put back by the fallback (no try-on plan stays installed with no override), an alert says so, and the bound task
    ends cleanly (no "Task exception was never retrieved")."""
    tier = Tier.STRAND if mode == CALM else Tier.T1

    async def main():
        w, compiles = await started_world()
        engine = w.engine
        await w.run_for(30)
        st = await engine.start_trial(trial_of(mode, w.clock.monotonic(), bound=60))
        assert not st.refused
        await engine.wait_idle()
        task = engine._trial_task
        compiles.boom[(mode, tier, False)] = 1
        await w.run_for(61)
        await engine.wait_idle()
        out = (engine.trial, engine.current_plan(), task, alerts_of(w), w.events("lab"))
        await w.run_for(600)  # nothing re-asserts a try-on plan meanwhile
        out += (engine.current_plan(),)
        await w.close()
        return out

    trial, plan, task, alerts, lab, later = run_virtual(main)
    assert trial is None
    assert plan.mode_id == mode and not from_trial(plan) and plan.tier is tier
    assert task.done() and not task.cancelled() and task.exception() is None
    assert any("try-on" in a and "RuntimeError" in a for a in alerts), alerts
    assert [e["why"] for e in lab if e["action"] == "trial_end"] == ["it reached its time limit (60 s)"]
    assert not from_trial(later)


def test_when_the_approved_look_cant_come_back_either_the_safe_look_does():
    async def main():
        w, compiles = await started_world()
        engine = w.engine
        await w.run_for(30)
        await engine.start_trial(trial_of("sunset", w.clock.monotonic(), bound=60))
        await engine.wait_idle()
        compiles.boom[("sunset", Tier.T1, False)] = 99  # the approved Sunset never compiles again
        await w.run_for(61)
        await engine.wait_idle()
        out = (engine.trial, engine.current_plan(), alerts_of(w))
        compiles.boom.clear()
        await w.close()
        return out

    trial, plan, alerts = run_virtual(main)
    assert trial is None and plan.mode_id == CALM and not from_trial(plan)
    assert any("safe look" in a for a in alerts), alerts


# ---- a re-apply never outlives the running try-on unsaid (B-TRUTH-1): the engine's guards ------------------------
def test_a_reapply_too_close_to_the_bound_is_refused_by_the_engine():
    async def main():
        w, _ = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic(), bound=60, label="A"))
        await engine.wait_idle()
        await w.run_for(54 - (w.clock.monotonic() - engine.trial.started_mono))
        sent = len([e for e in commands(w) if e.get("source") == "lab"])
        left = engine.trial.ends_mono - w.clock.monotonic()
        st = await engine.start_trial(trial_of("sunset", w.clock.monotonic(), label="B"), min_left_s=8)
        out = (left, st, engine.trial, len([e for e in commands(w) if e.get("source") == "lab"]) - sent)
        await w.close()
        return out

    left, st, trial, more = run_virtual(main)
    assert left == pytest.approx(6, abs=0.01)
    assert st.refused and st.message == TRIAL_ENDING_MESSAGE.format(left=6)
    assert st.retry_after_s == pytest.approx(6, abs=0.01)
    assert trial is not None and trial.label == "A" and trial.applies == 1 and more == 0


def test_a_reapply_of_a_session_that_ended_meanwhile_is_refused():
    """The route reads the running session before it builds the trial (in the threadpool): when that session ended,
    or another began, before start_trial, the apply is refused instead of becoming a fresh try-on its confirm never
    described (or a re-apply of one it never named)."""

    async def main():
        w, _ = await started_world()
        engine = w.engine
        await engine.start_trial(trial_of("sunset", w.clock.monotonic(), bound=60, label="A"))
        session = engine.trial.session
        other = await engine.start_trial(
            trial_of("sunset", w.clock.monotonic(), label="B"), expect_session=None
        )
        await w.run_for(61)
        await engine.wait_idle()
        gone = await engine.start_trial(
            trial_of("sunset", w.clock.monotonic(), label="C"), expect_session=session
        )
        out = (other, gone, engine.trial)
        fresh = await engine.start_trial(
            trial_of("sunset", w.clock.monotonic(), label="D"), expect_session=None
        )
        out += (fresh, engine.trial)
        await w.close()
        return out

    other, gone, trial, fresh, last = run_virtual(main)
    assert other.refused and gone.refused and trial is None
    assert other.message == gone.message == TRIAL_CHANGED_MESSAGE
    assert not fresh.refused and last.label == "D" and last.applies == 1


# ---- a re-apply's confirm and cost name the time left; none lands just before the bound (B-TRUTH-1) -------------
def lab_sends(w: Show) -> int:
    return len([e for e in w.events("command") if e.get("source") == "lab"])


def test_a_reapply_near_the_bound_is_refused_and_never_uses_up_the_gap(no_threads):
    """With less than lab.apply_min_gap_s of the bound left, a re-apply would land a moment before the bound puts the
    approved look back (8 of 9 draft writes superseded): it is refused with a true sentence and Retry-After, nothing
    is sent, and the gap isn't stamped. Once the try-on has ended, an apply starts a fresh one."""

    async def main():
        w = await show()
        async with client_of(w) as c:
            first = await confirmed(c, golden_pink())
            trial = w.engine.trial
            gate = T.apply_gate(w.lab)
            stamped = gate._last
            await w.run_for(trial.ends_mono - 5 - w.clock.monotonic())
            sent = lab_sends(w)
            sentence = T.confirm_sentence(w.lab, golden_both(), T.driven_modules(w.lab), 150.0)
            near_sent = w.clock.monotonic()
            near = await c.post("/api/lab/trial/start", json=start_body(golden_both(), sentence))
            near_left = (trial.ends_mono - w.clock.monotonic(), trial.ends_mono - near_sent)
            during = (w.engine.trial, lab_sends(w) - sent, gate._last == stamped)
            await w.run_for(10)
            await w.engine.wait_idle()
            again = await confirmed(c, golden_both())
            out = (first, trial, near, during, again, w.engine.trial, w.events("lab"), near_left)
        await w.close()
        return out

    first, trial, near, (still, more, unstamped), again, fresh, lab, (least, most) = run_virtual(main)
    assert first.status_code == 200
    assert near.status_code == 409, near.text
    body = near.json()
    assert "confirm_required" not in body
    assert (
        body["detail"].startswith("This try-on ends in ") and "too soon for another apply" in body["detail"]
    )
    # the time left when the route read the clock, which is 5 s less only the virtual time the request took (none)
    assert most == pytest.approx(5) and least - 0.005 <= body["retry_after_s"] <= most + 0.005
    assert near.headers["retry-after"] == str(math.ceil(body["retry_after_s"]))
    assert still is trial and more == 0 and unstamped
    assert again.status_code == 200, again.text
    assert fresh.session == trial.session + 1 and fresh.applies == 1
    assert fresh.ends_mono - fresh.started_mono == pytest.approx(150)
    assert [e["why"] for e in lab if e["action"] == "trial_refused"] == ["too close to its time limit"]


def test_a_reapplys_confirm_and_cost_name_the_time_left(no_threads):
    """A re-apply (same scene or another) is confirmed against the running session's time left, never "for up to
    150 s": the first start's sentence no longer confirms it. The confirm still matches a few seconds later (its
    number is an upper bound by then), and the session's end never moves."""

    async def main():
        w = await show()
        async with client_of(w) as c:
            first_ask = await c.post("/api/lab/trial/start", json=start_body(golden_pink()))
            first_sentence = first_ask.json()["confirm"]
            assert (
                await c.post("/api/lab/trial/start", json=start_body(golden_pink(), first_sentence))
            ).status_code == 200
            trial = w.engine.trial
            await w.run_for(trial.started_mono + 40 - w.clock.monotonic())
            stale_sent = w.clock.monotonic()
            stale = await c.post("/api/lab/trial/start", json=start_body(golden_both(), first_sentence))
            stale_left = (trial.ends_mono - w.clock.monotonic(), trial.ends_mono - stale_sent)
            other = await c.post("/api/lab/trial/start", json=start_body(calm_solid()))
            await w.run_for(3)
            ok = await c.post("/api/lab/trial/start", json=start_body(golden_both(), stale.json()["confirm"]))
            out = (first_sentence, trial, stale, other, ok, w.engine.trial, stale_left)
        await w.close()
        return out

    first_sentence, trial, stale, other, ok, after, (least, most) = run_virtual(main)
    assert first_sentence.endswith("for up to 150 s. The painted design isn't on the lights.")
    assert stale.status_code == 409 and stale.json()["confirm_required"], stale.text
    sentence, cost = stale.json()["confirm"], stale.json()["cost"]
    left = re.search(r"for the (\d+) s left of the running try-on", sentence)
    # 150 − 40 = 110 s left as the request was sent, less only the virtual time the request took before the route
    # read the clock (none: it awaits no timer), named rounded up
    assert (
        most == pytest.approx(110)
        and left is not None
        and math.ceil(least) <= int(left.group(1)) <= math.ceil(most)
    ), sentence
    assert "150 s" not in sentence and "(a re-apply never extends it)" in sentence
    assert sentence.startswith("Try on your draft of Golden Hour at module level on 9 modules (Module 1,")
    assert (
        cost.startswith("sends 9 writes now, up to ~36 over the next 10 s")
        and f"ends in {left.group(1)} s" in cost
    )
    calm = other.json()["confirm"]
    assert other.status_code == 409 and "of the running try-on (a re-apply never extends it)." in calm
    assert calm.startswith("Try on your draft of Lowcountry Calm at module level on 9 modules")
    assert ok.status_code == 200, ok.text
    assert after.session == trial.session and after.applies == 2 and after.ends_mono == trial.ends_mono


def test_an_engine_error_answers_a_plain_500_and_never_strands_the_try_on(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            compile = w.engine.deps.compile

            def broken(mode_id, tier):
                if w.engine.trial is not None:
                    raise RuntimeError("a compiler bug (test)")
                return compile(mode_id, tier)

            w.engine.deps.compile = broken
            failed = await confirmed(c, golden_pink())
            state = (
                w.engine.trial,
                T.apply_gate(w.lab).remaining(w.clock.monotonic()),
                w.engine.current_plan(),
            )
            w.engine.deps.compile = compile
            ok = await confirmed(c, golden_pink())
            out = (failed, state, ok, [e["message"] for e in w.events("alert")])
        await w.close()
        return out

    failed, (trial, gap, plan), ok, alerts = run_virtual(main)
    assert failed.status_code == 500, failed.text
    assert failed.json()["detail"].startswith("The try-on failed on the server (RuntimeError)")
    assert trial is None and gap == 0 and plan.mode_id == CALM
    assert any("RuntimeError" in a for a in alerts)
    assert ok.status_code == 200, ok.text


# ---- a module the confirm never named never gets the draft (B-ENG-1) ------------------------------------------------
NEW = "canopy-r0c2"


def only_registered(monkeypatch, ids: set[str]) -> Any:
    """Registry.address answers only for `ids` (a mutable set: add to it to register a module mid-test, D62)."""
    from conductor.config import Registry

    real = Registry.address

    def address(self, device_id, driver_name):
        return real(self, device_id, driver_name) if device_id in ids else None

    monkeypatch.setattr(Registry, "address", address)
    return real


def spy_writes(w: Show) -> list[tuple[float, str, list]]:
    writes: list[tuple[float, str, list]] = []
    driver = w.services.engine_driver
    original = driver.write

    async def write(device_id, rows, **kw):
        writes.append((w.clock.monotonic(), device_id, list(rows)))
        return await original(device_id, rows, **kw)

    driver.write = write
    return writes


def test_a_module_registered_mid_try_on_ends_it_instead_of_getting_the_draft(monkeypatch):
    """Two modules registered (the VWBD kits), a try-on confirmed on those two; ops registers a third meanwhile
    (/ops/accounts: caps.pop + request_refresh). The show's re-pick would compile the trial's plan with the third in
    it — a module the confirm never named. The try-on ends instead (why recorded): every module, the new one too,
    gets the scene's approved look, and the new one never gets a draft row."""
    ids = {"canopy-r0c0", "canopy-r0c1"}
    real = only_registered(monkeypatch, ids)

    async def main():
        w = await show()
        d = golden_both()
        sentence = T.confirm_sentence(w.lab, d, T.driven_modules(w.lab), 150.0)
        await try_on(w, d)
        before = (w.engine.trial, sorted(w.engine.current_plan().modules))
        writes = spy_writes(w)
        new_address = real(w.services.registry, NEW, w.services.engine_driver.name)
        ids.add(NEW)
        w.wiring.caps.pop(NEW, None)
        w.wiring.request_refresh([NEW])
        await w.run_for(5)
        await w.engine.wait_idle()
        drafted = draft_rows(w, d, Tier.T3)
        out = (
            sentence,
            before,
            w.engine.trial,
            w.engine.current_plan(),
            writes,
            new_address,
            drafted,
            w.events("lab"),
        )
        await w.close()
        return out

    sentence, (trial, modules), after, plan, writes, new_address, drafted, lab = run_virtual(main)
    assert "on 2 modules (Module 1, Module 2)" in sentence and trial is not None
    assert modules == ["canopy-r0c0", "canopy-r0c1"]
    assert after is None, "the try-on ended"
    assert NEW in plan.modules and not plan.label.endswith(TRIAL_LABEL)
    assert all(rows != drafted[NEW] for _t, device, rows in writes if device == new_address)
    assert [w for w in writes if w[1] == new_address], "the new module got the approved look"
    ends = [e["why"] for e in lab if e["action"] == "trial_end"]
    assert len(ends) == 1 and "module" in ends[0] and "Module 3" in ends[0], ends


def test_a_module_read_mid_try_on_that_would_join_its_plan_ends_it(monkeypatch):
    """A registered module whose profile couldn't be read was left out of the try-on's plan; when its read comes back
    mid-try-on, the re-pick would add it: the try-on ends rather than write the draft to it."""

    async def main():
        w = await show()
        failing = {NEW}
        read_one = w.wiring._read_one

        async def flaky(entry):
            if entry.id in failing:
                w.wiring.caps_errors[entry.id] = "unreachable (test)"
                return None
            return await read_one(entry)

        monkeypatch.setattr(w.wiring, "_read_one", flaky)
        w.wiring.caps.pop(NEW)
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.run_for(10)
        await w.engine.wait_idle()
        await try_on(w, golden_both())
        before = (w.engine.trial, sorted(w.engine.current_plan().modules))
        failing.clear()
        w.wiring.request_refresh([NEW])
        await w.run_for(5)
        await w.engine.wait_idle()
        out = (before, w.engine.trial, w.engine.current_plan(), w.events("lab"))
        await w.close()
        return out

    (trial, modules), after, plan, lab = run_virtual(main)
    assert trial is not None and NEW not in modules and len(modules) == 8
    assert after is None and NEW in plan.modules and not plan.label.endswith(TRIAL_LABEL)
    assert len([e for e in lab if e["action"] == "trial_end"]) == 1


def test_a_module_unregistered_mid_try_on_keeps_it_on_the_others(monkeypatch):
    """The mirror: a module unregistered mid-try-on drops out of the plan; the rest were all named in the confirm, so
    the try-on carries on there with the draft's rows (no module it never named is written)."""
    ids = {e for e in MODULES}
    only_registered(monkeypatch, ids)

    async def main():
        w = await show()
        d = golden_both()
        await try_on(w, d)
        before = w.engine.trial
        ids.discard(NEW)
        w.wiring.request_refresh([NEW])
        await w.run_for(5)
        await w.engine.wait_idle()
        out = (before, w.engine.trial, w.engine.current_plan(), draft_rows(w, d, Tier.T3))
        await w.close()
        return out

    before, after, plan, drafted = run_virtual(main)
    assert after is not None and after.session == before.session
    assert NEW not in plan.modules and len(plan.modules) == 8
    assert plan.tier is Tier.T3 and rows_of(plan) == drafted
