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
>= kiosk.cooldown_s apart; guests are refused with a true sentence; it ends at min(lab.trial_max_s,
kiosk.idle_return_s), on "Back to approved", and before anything else compiles its own look; a failed clear puts the
approved look back; it is never persisted; and the show's cached config is never mutated.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from fake_compiler import MODULES, make_world, settings_with
from lab_edits import NEW_PINK, done_means, other_hex
from pydantic import ValidationError

from conductor import rows as R
from conductor.canopy import (
    COMMISSIONING_MESSAGE,
    RESTING_MESSAGE,
    STOPPED_MESSAGE,
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
        world.engine.deps.compile = self

    def __call__(self, mode_id: str, tier: Tier) -> ModePlan:
        trial = self.world.engine.trial
        self.log.append((mode_id, tier, trial.mode_id if trial is not None else None))
        if self.fail.get((mode_id, tier), 0) > 0:
            self.fail[(mode_id, tier)] -= 1
            raise PlanError(f"{mode_id} at {tier} can't compile (test)")
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
        if task is not None:
            await task
        await w.close()
        return st, before, after

    st, before, after = run_virtual(main)
    assert st.refused and (st.message, st.retry_after_s) == expected
    assert after == (*before, None)  # nothing installed, nothing sent, no trial


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
        await w.close()
        return rippled, st, one_burst, sends, lab

    rippled, st, one_burst, sends, lab = run_virtual(main)
    assert rippled, "the control: the ripple is on, and the plans have rings"
    assert not st.refused and one_burst == (None, {})
    assert [s for s in sends if s[1] == "lab"] == [(None, "lab")]  # one send to every module: one burst
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


async def show(*, live: bool = False, monkeypatch=None, data: Path | None = None, prepare=None) -> Show:
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
    if live:  # the venue box's tier rules over the simulator's transport
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
    transport = httpx.ASGITransport(app=lab_app(w.services))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture
def no_threads(monkeypatch):
    """The Lab's threadpool calls run inline (after one yield, so a second request can interleave): the virtual loop
    would otherwise jump its clock while a worker thread computes."""
    from conductor.routes import design_lab as routes

    async def inline(fn, *args, **kwargs):
        await asyncio.sleep(0)
        return fn(*args, **kwargs)

    monkeypatch.setattr(routes, "run_in_threadpool", inline)
    monkeypatch.setattr(SVC, "run_in_threadpool", inline)
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
            first = await c.post("/api/lab/trial/start", json=start_body(golden_pink(), sentence))
            await w.run_for(3)
            second = await c.post("/api/lab/trial/start", json=start_body(golden_both(), sentence))
            await w.run_for(5.01)
            third = await c.post("/api/lab/trial/start", json=start_body(golden_both(), sentence))
            got = await c.get("/api/lab/trial")
        out = (asked, first, second, third, got, w.events("lab"))
        await w.close()
        return out

    asked, first, second, third, got, lab = run_virtual(main)
    body = asked.json()
    assert (
        body["detail"] == body["confirm"]
        and body["cost"] == "sends 9 writes now, up to ~36 over the next 10 s"
    )
    assert body["confirm"].startswith(
        "Try on Golden Hour for Golden Hour at module level on 9 modules (Module 1,"
    )
    assert body["confirm"].endswith("for up to 150 s. The painted design isn't on the lights.")
    assert asked.headers["cache-control"] == "no-store"
    assert first.status_code == 200, first.text
    trial = first.json()["trial"]
    assert trial["tier"] == "modules" and trial["applies"] == 1 and trial["next_apply_in_s"] == 8
    assert first.json()["state"]["mode"] == GOLDEN
    # ≈ 8 − 3 (the request round trips themselves take a little virtual time)
    assert second.status_code == 409 and second.json()["retry_after_s"] == pytest.approx(5, abs=0.5)
    assert second.headers["retry-after"] == "5"
    assert third.status_code == 200, third.text
    assert third.json()["trial"]["applies"] == 2 and got.json()["applies"] == 2
    assert [e.get("why") for e in lab if e["action"] == "trial_refused"] == ["apply gap"]
    starts = [e["action"] for e in lab if e["action"] in ("trial_start", "trial_apply")]
    assert starts == ["trial_start", "trial_apply"]


def test_two_concurrent_starts_give_one_200_and_one_409(no_threads):
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
    assert refused.json()["retry_after_s"] > 7 and refused.headers["retry-after"] == "8"
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
            w2 = await show(data=data)
            async with client_of(w2) as c:
                state = (await c.get("/api/lab/state")).json()
            out = (saved, started, looks, w2.engine.trial, w2.engine.current_plan().mode_id, state)
            out += ((data / "overrides" / "looks.yaml").read_bytes(),)
            await w2.close()
            return out
        finally:
            shutil.rmtree(data, ignore_errors=True)

    saved, started, looks, trial, mode, state, looks_after = run_virtual(main)
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
        "Try on Golden Hour for Golden Hour at module level on 2 modules (Module 1, Module 2) for up to 150 s. "
        "The painted design isn't on the lights."
    )
    renamed = draft(CALM, name="Calm evening")
    assert T.confirm_sentence(lab, renamed, modules[:1], 90) == (
        "Try on Calm evening for Lowcountry Calm at module level on 1 module (Module 1) for up to 90 s."
    )
    assert T.cost_line(2) == "sends 2 writes now, up to ~8 over the next 10 s"
    assert T.cost_line(1) == "sends 1 write now, up to ~4 over the next 10 s"


def test_the_apply_gate_reserves_and_releases():
    gate = T.ApplyGate(8)
    assert gate.reserve(100.0) == 0.0
    assert gate.reserve(103.0) == 5.0 and gate.remaining(103.0) == 5.0
    gate.release()  # the apply at 100 was refused after all: no gap
    assert gate.reserve(103.0) == 0.0 and gate.reserve(110.0) == 1.0 and gate.reserve(111.0) == 0.0
