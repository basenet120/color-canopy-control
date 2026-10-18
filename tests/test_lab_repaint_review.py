"""Review 5 of the operator repaint (D68 PR B2): regression tests, each failing before its fix (or on the named mutant).

- B2-1 (restart): the persisted gate and queue are back as the engine starts, before any profile is read — a module
  whose profile read fails at boot never opens the gate early, never drops the queue, and never lets the reconciler
  rewrite a queued scene's slots inside the gate; a restart after the due time still repaints (never "already holds"
  before the profiles are read). Mutant S2 (no gate seed) is killed by the nothing-queued case.
- B2-2 (order): the scenes of a job run in due order, so a scene due now never waits behind another scene's gate.
- B2-3 / UI-3: the quiet window is named a repaint — never "commissioning" — by ops' Re-assert all and the Lab's state.
- UI-6: the editor's repaint line is a maximum (the confirm names the modules whose design changes).
- Quiet-window guards (mutants Q3, Q5, Q7, Q30, Q31), the carry-over of a queued scene (P3), and no history copy for
  the repaint's bookkeeping (L3).
"""

from __future__ import annotations

import asyncio
import shutil
from types import SimpleNamespace

import pytest
from live_lab import (
    CALM,
    GOLDEN,
    TIDES,
    ask,
    brighter,
    client_of,
    edited,
    install_no_threads,
    pinker_hour,
    publish,
    save,
    seed,
    show,
)

from conductor import rows as R
from conductor.canopy import REPAINT_MESSAGE
from conductor.clock import VirtualClock, run_virtual
from conductor.config import CONFIG_DIR, load_settings
from conductor.engine_api import Tier
from conductor.executor_api import CommandStatus
from conductor.geometry import load_canopy_config
from conductor.looks import live as L

SLOT = "custom-3"
INTERVAL = 120.0
RRS = "rainbow-row-sunset"
UNREAD = "canopy-r2c2"


@pytest.fixture
def no_threads(monkeypatch):
    install_no_threads(monkeypatch)


def spy_writes(w) -> list[tuple[float, str, list]]:
    writes: list[tuple[float, str, list]] = []
    driver = w.services.engine_driver
    original = driver.write

    async def write(address, rows, **kw):
        writes.append((w.clock.monotonic(), address, list(rows)))
        return await original(address, rows, **kw)

    driver.write = write
    return writes


def is_slot(x, slot: str | None = None) -> bool:
    return any(
        r.function_class == R.FRAME_CLASS and (slot is None or r.function_instance == slot) for r in x[2]
    )


def slot_times(writes, slot: str = SLOT) -> list[float]:
    return [t for t, _a, rows in writes if is_slot((t, _a, rows), slot)]


def golden_layout(angle: int) -> dict:
    return edited(GOLDEN, layout={"kind": "linear", "angle_deg": angle})


def layout_of(scene_id: str, angle: int) -> dict:
    d = seed(scene_id)
    d["layout"] = {"kind": "linear", "angle_deg": angle}
    return d


def repaint_results(w) -> list[str]:
    return [
        str(e.get("status"))
        for e in w.services.telemetry.ring
        if e["kind"] == "command" and "publish repaint" in str(e.get("label", ""))
    ]


async def wait_quiet(w, limit: float = 200.0) -> bool:
    waited = 0.0
    while not w.engine.repaint_quiet and waited < limit:
        await w.run_for(0.05)
        waited += 0.05
    return w.engine.repaint_quiet


async def wait_first_slot_write(w, writes, limit: float = 200.0) -> bool:
    waited = 0.0
    while not any(is_slot(x) for x in writes) and waited < limit:
        await w.run_for(0.01)
        waited += 0.01
    return any(is_slot(x) for x in writes)


def looks_in(writes, mark: int, end_t: float) -> list:
    return [x for x in writes[mark:] if not is_slot(x) and x[0] <= end_t]


# ---- B2-1: the persisted gate and queue after a restart ----------------------------------------------------------------
async def restart(
    w, monkeypatch, *, box: bool = True, unread: str | set[str] | None = None, gap: float = 0.0
):
    """Close `w` and start the show again over its data folder (`gap` seconds later), keeping the simulator's devices
    (their slots) as a real restart would. `box`: no start-up commissioning (the show box). `unread`: the profile
    reads of that module (or of a set of them, which a test may empty later: `services.extras["unreadable"]`) fail —
    a cloud error at boot."""
    from conductor.show import ShowWiring

    data = w.data
    devices = w.services.sim.cloud.devices
    w.owned = False
    await w.close()
    if gap:
        await asyncio.sleep(gap)  # the show is down (virtual time)
    if box:

        async def no_commission(self):
            return None

        monkeypatch.setattr(ShowWiring, "_sim_commission", no_commission)

    def prepare(services):
        services.sim.cloud.devices = devices
        if unread is None:
            return
        blocked = unread if isinstance(unread, set) else {unread}
        services.extras["unreadable"] = blocked
        driver = services.engine_driver
        real = driver.fetch_profile

        async def fetch_profile(address, **kw):
            if any(d.removeprefix("canopy-") in str(address) for d in blocked):
                raise ConnectionError("the cloud answered 503")
            return await real(address, **kw)

        driver.fetch_profile = fetch_profile

    return await show(data=data, quiet=False, prepare=prepare)


@pytest.mark.parametrize("case", ["box", "box-one-unread", "sim"])
def test_a_publish_after_a_restart_honours_the_persisted_gate(no_threads, monkeypatch, case):
    """Golden Hour repainted at t0, nothing queued; the show restarts 30 s later (on the show box, with one module's
    profile unreadable at boot, or in the simulator) and Golden Hour is republished: the confirm names the wait and no
    slot is written before t0 + 120 (before the fix: in_s 0 and a repaint at once with a module unread; mutant S2 the
    same in every case)."""

    async def main():
        w = await show()
        writes0 = spy_writes(w)
        async with client_of(w) as c:
            v1 = await save(c, {GOLDEN: golden_layout(45)})
            await publish(c, v1)
            await w.engine.wait_repaint()
        t0 = min(slot_times(writes0))
        await w.run_for(30)
        w2 = await restart(
            w, monkeypatch, box=case != "sim", unread=UNREAD if case == "box-one-unread" else None
        )
        for _ in range(40):
            await w2.run_for(0.5)
            if w2.wiring.started and w2.engine.current_plan() is not None:
                break
        writes = spy_writes(w2)
        async with client_of(w2) as c:
            v2 = await save(c, {GOLDEN: golden_layout(60)})
            asked = (await ask(c, v2)).json()
            tp = w2.clock.monotonic()
            r = await publish(c, v2)
        await w2.engine.wait_repaint()
        out = (
            t0,
            tp,
            asked["repaint"],
            r.status_code,
            slot_times(writes),
            w2.engine.stats["repaints"],
            w2.engine.reconciler.stats.get("slot_rewrites", 0),
        )
        data = w2.data
        await w2.close()
        shutil.rmtree(data, ignore_errors=True)
        return out

    t0, tp, asked, status, slots, repaints, rewrites = run_virtual(main)
    assert status == 200 and [j["scene"] for j in asked] == [GOLDEN]
    assert asked[0]["in_s"] == pytest.approx(t0 + INTERVAL - tp, abs=2)
    assert slots and min(slots) >= t0 + INTERVAL - 0.5
    assert repaints == 1 and rewrites == 0


async def _queued_then_restart(monkeypatch, *, unread: str | set[str] | None = UNREAD, gap: float = 0.0):
    """v1 repainted at t0; v2 republished 30 s later (queued, due t0 + 120); the show box restarts after `gap` more
    seconds with `unread`'s profile read failing."""
    w = await show()
    writes0 = spy_writes(w)
    async with client_of(w) as c:
        v1 = await save(c, {GOLDEN: golden_layout(45)})
        await publish(c, v1)
        await w.engine.wait_repaint()
        await w.run_for(30)
        v2 = await save(c, {GOLDEN: golden_layout(60)})
        r2 = await publish(c, v2)
    assert r2.status_code == 200 and set(w.looks.file.queued) == {GOLDEN}
    t0 = min(slot_times(writes0))
    w2 = await restart(w, monkeypatch, box=True, unread=unread, gap=gap)
    return w2, t0


def test_a_queued_repaint_survives_a_restart_with_one_module_unread(no_threads, monkeypatch):
    """The queued repaint is pending from the engine's start (every commissioned module, the unread one too), shows as
    queued, and runs at its due time on the modules read; the reconciler never rewrites a slot inside the gate, and
    the queue empties (before the fix: nothing pending, 8 reconciler rewrites 36-41 s after the last repaint, no
    operator repaint, and `queued` left in the file)."""

    async def main():
        w2, t0 = await _queued_then_restart(monkeypatch)
        writes = spy_writes(w2)
        await w2.run_for(1)
        pending = {d for d, s in w2.engine.repaint_pending if s == SLOT}
        status = w2.engine.repaint_json() or {}
        held = GOLDEN in w2.engine.t1_hold
        await w2.run_for(t0 + INTERVAL + 60 - w2.clock.monotonic())
        await w2.engine.wait_repaint()
        out = (
            t0,
            pending,
            status.get("queued_until"),
            held,
            slot_times(writes),
            w2.engine.stats["repaints"],
            w2.engine.reconciler.stats.get("slot_rewrites", 0),
            dict(w2.looks.file.queued),
            w2.wiring.caps_ready.is_set(),
        )
        data = w2.data
        await w2.close()
        shutil.rmtree(data, ignore_errors=True)
        return out

    t0, pending, queued_until, held, slots, repaints, rewrites, queued, caps_ready = run_virtual(main)
    assert not caps_ready  # one module stays unread throughout
    assert len(pending) == 9 and UNREAD in pending and queued_until is not None and held
    assert len(slots) == 8 and min(slots) >= t0 + INTERVAL - 0.5
    assert repaints == 1 and rewrites == 0 and queued == {}


@pytest.mark.parametrize("unread", ["one", "all"])
def test_a_publish_right_after_a_restart_keeps_the_queued_scene(no_threads, monkeypatch, unread):
    """One second after a restart (one module's profile unreadable, or every module's), a version that changes Coastal
    Tides and keeps Golden Hour's queued draft is published: Golden Hour stays queued with its due time (in the engine
    and the file), the confirm names it, and its modules — the unread ones too — stay pending, so when they answer
    5 s later the reconciler leaves their slots alone and the repaint writes all nine at the due time (before the fix:
    dropped from the queue, its slots left to the reconciler inside the gate)."""
    blocked = {UNREAD} if unread == "one" else {f"canopy-r{r}c{c}" for r in range(3) for c in range(3)}

    async def main():
        w2, t0 = await _queued_then_restart(monkeypatch, unread=set(blocked))
        writes = spy_writes(w2)
        await w2.run_for(1)
        due = w2.engine.queued_repaints()[GOLDEN].due_mono
        async with client_of(w2) as c:
            v3 = await save(c, {GOLDEN: golden_layout(60), TIDES: brighter(TIDES)})
            asked = (await ask(c, v3)).json()
            r = await publish(c, v3)
        after = {m: j.due_mono for m, j in w2.engine.queued_repaints().items()}
        persisted = dict(w2.looks.file.queued)
        pending = {d for d, s in w2.engine.repaint_pending if s == SLOT}
        await w2.run_for(5)
        w2.services.extras["unreadable"].clear()  # the cloud answers again
        w2.wiring.request_refresh()
        await w2.run_for(t0 + INTERVAL + 60 - w2.clock.monotonic())
        await w2.engine.wait_repaint()
        out = (
            t0,
            due,
            [c["scene"] for c in asked["changes"]],
            [j["scene"] for j in asked["repaint"]],
            r.status_code,
            after,
            persisted,
            pending,
            slot_times(writes),
            w2.engine.stats["repaints"],
            w2.engine.reconciler.stats.get("slot_rewrites", 0),
            w2.wiring.caps_ready.is_set(),
        )
        data = w2.data
        await w2.close()
        shutil.rmtree(data, ignore_errors=True)
        return out

    t0, due, changes, repaint, status, after, persisted, pending, slots, repaints, rewrites, caps = (
        run_virtual(main)
    )
    assert changes == [TIDES] and repaint == [GOLDEN] and status == 200
    assert after == {GOLDEN: pytest.approx(due)} and set(persisted) == {GOLDEN}
    assert len(pending) == 9 and caps
    assert len(slots) == 9 and min(slots) >= t0 + INTERVAL - 0.5
    assert repaints == 1 and rewrites == 0


def test_a_restart_after_the_due_time_still_repaints(no_threads, monkeypatch):
    """The show was down past the queued scene's due time: its window waits for the first capability reads (never
    compiles before any profile is read, which would say the slots "already hold" the design) and repaints all nine."""

    async def main():
        w2, _t0 = await _queued_then_restart(monkeypatch, unread=None, gap=300.0)
        writes = spy_writes(w2)
        await w2.run_for(60)
        await w2.engine.wait_repaint()
        records = {d: w2.services.registry.get(d).slot_record(SLOT)["hash"] for d in w2.engine._module_ids()}
        from conductor.looks import compile_mode

        plan = compile_mode(GOLDEN, Tier.T1, w2.wiring.context())
        out = (
            len(slot_times(writes)),
            w2.engine.stats["repaints"],
            dict(w2.looks.file.queued),
            records == {d: mt.slot_hash for d, mt in plan.modules.items()},
            GOLDEN in w2.engine.t1_hold,
        )
        data = w2.data
        await w2.close()
        shutil.rmtree(data, ignore_errors=True)
        return out

    writes, repaints, queued, current, held = run_virtual(main)
    assert writes == 9 and repaints == 1 and queued == {} and current and not held


def test_a_due_repaint_with_no_profile_read_says_so_and_never_claims_the_slots_hold_it(
    no_threads, monkeypatch
):
    """Past its due time with every module's profile unreadable: nothing can be compiled, so the repaint doesn't start
    and says why — never "its slots already hold the live design" — and nothing is written."""
    blocked = {f"canopy-r{r}c{c}" for r in range(3) for c in range(3)}

    async def main():
        w2, _t0 = await _queued_then_restart(monkeypatch, unread=set(blocked), gap=300.0)
        writes = spy_writes(w2)
        await w2.run_for(60)
        await w2.engine.wait_repaint()
        alerts = [a.message for a in w2.alerts() if "didn't start" in a.message]
        out = alerts, slot_times(writes), w2.engine.stats["repaints"]
        data = w2.data
        await w2.close()
        shutil.rmtree(data, ignore_errors=True)
        return out

    alerts, slots, repaints = run_virtual(main)
    assert slots == [] and repaints == 0
    assert alerts and "profile of 9 modules hadn't been read yet" in alerts[0]


# ---- B2-2: a job's scenes in due order ---------------------------------------------------------------------------------
def test_a_scene_due_now_never_waits_behind_another_scenes_gate(no_threads):
    """Rainbow Row Sunset (first in mode order) is queued behind its gate; a publish that also changes Golden Hour
    (never repainted: due now) repaints Golden Hour at once and Rainbow at its due time, never interleaved (before the
    fix Golden Hour's writes came ~80 s later, after Rainbow's, while the confirm named no wait)."""

    async def main():
        w = await show()
        writes = spy_writes(w)
        rslot = w.looks.trio.modes.modes[RRS].slot
        async with client_of(w) as c:
            v1 = await save(c, {RRS: layout_of(RRS, 20)})
            await publish(c, v1)
            await w.engine.wait_repaint()
            t1 = min(slot_times(writes, rslot))
            await w.run_for(30)
            v2 = await save(c, {RRS: layout_of(RRS, 40)})
            await publish(c, v2)
            await w.run_for(10)
            v3 = await save(c, {RRS: layout_of(RRS, 40), GOLDEN: golden_layout(45)})
            asked = (await ask(c, v3)).json()
            tp = w.clock.monotonic()
            r = await publish(c, v3)
            await w.run_for(1)
            status = w.engine.repaint_json() or {}
        await w.engine.wait_repaint()
        g = slot_times(writes, SLOT)
        q = [t for t in slot_times(writes, rslot) if t > tp]
        out = (asked["repaint"], r.status_code, tp, t1, g, q, status.get("scene"))
        await w.close()
        return out

    repaint, status, tp, t1, g, q, scene = run_virtual(main)
    shown = {j["scene"]: j["in_s"] for j in repaint}
    assert status == 200 and shown[GOLDEN] == 0 and shown[RRS] > 60
    assert len(g) == 9 and min(g) - tp < 5 and scene == GOLDEN
    assert len(q) == 9 and min(q) >= t1 + INTERVAL - 0.5
    assert max(g) < min(q)  # in sequence, never interleaved


# ---- B2-3 / UI-3 / Q7: the quiet window is a repaint, never "commissioning" -------------------------------------------
def test_in_the_quiet_window_ops_and_the_lab_name_a_repaint_and_reassert_sends_nothing(no_threads):
    async def main():
        w = await show()
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.engine.wait_idle()
        writes = spy_writes(w)
        async with client_of(w) as c:
            before = (await c.get("/api/lab/state")).json()["engine"]
            vid = await save(c, {GOLDEN: pinker_hour()})
            await publish(c, vid)
            opened = await wait_quiet(w)
            route = await c.post("/api/canopy/reassert", json={})
            state = (await c.get("/api/lab/state")).json()["engine"]
            mark = len(writes)
            direct = await w.engine.reassert_all(source="ops")
            await w.engine.wait_repaint()
            after = (await c.post("/api/canopy/reassert", json={})).status_code
        last = max(slot_times(writes))
        out = (
            before,
            opened,
            route.status_code,
            route.json().get("detail"),
            route.headers.get("retry-after"),
            state,
            (direct.refused, direct.message),
            len(looks_in(writes, mark, last)),
            len(slot_times(writes)),
            after,
        )
        await w.close()
        return out

    before, opened, code, detail, retry, state, direct, looks, slots, after = run_virtual(main)
    assert before["repaint_quiet"] is False and before["commissioning"] is False
    assert opened and code == 409 and "Commissioning" not in detail and "repainting" in detail
    assert retry is not None and int(retry) >= 1
    assert state["repaint_quiet"] is True and state["commissioning"] is False
    assert direct == (True, REPAINT_MESSAGE) and looks == 0 and slots == 9  # Q7: the engine itself refuses
    assert after == 200


# ---- UI-6: the editor's repaint line is a maximum ---------------------------------------------------------------------
def test_the_editor_repaint_line_says_up_to_and_the_confirm_counts_the_changed_modules(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            catalog = (await c.get("/api/lab/catalog")).json()
            d = seed(GOLDEN)
            d["palette"] = [{**d["palette"][0], "hex": "#FFB070"}, *d["palette"][1:]]
            vid = await save(c, {GOLDEN: d})
            asked = (await ask(c, vid)).json()
        scene = next(s for s in catalog["scenes"] if s["id"] == GOLDEN)
        out = scene["repaint"]["sentence"], asked["repaint"]
        await w.close()
        return out

    sentence, repaint = run_virtual(main)
    assert sentence == (
        "Publishing a new painted design repaints custom-3 on up to 9 modules: only the modules whose design changes "
        "(one slot write each; the confirm names them)."
    )
    [job] = repaint
    assert 0 < job["writes"] < 9  # one palette stop: only some modules' frames change


# ---- the quiet window's guards (mutants Q3, Q5, Q30, Q31) ---------------------------------------------------------------
@pytest.mark.parametrize("when", ["window", "mid-writes"])
def test_a_guest_reset_in_the_quiet_window_is_refused(no_threads, when):
    """Q3: ops' Reset guest settings during the window (a guest dim in force) is refused with the repaint sentence and
    sends nothing; the nine slot writes land with none superseded."""

    async def main():
        w = await show()
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.engine.wait_idle()
        await w.run_for(10)
        await w.engine.set_guest_brightness(40, source="kiosk")
        await w.engine.wait_idle()
        await w.run_for(10)
        writes = spy_writes(w)
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            await publish(c, vid)
            opened = await wait_quiet(w)
            if when == "mid-writes":
                await wait_first_slot_write(w, writes)
            active = w.engine.guest_overrides_active
            mark = len(writes)
            r = await c.post("/api/canopy/guest-reset", json={})
            await w.engine.wait_repaint()
        last = max(slot_times(writes))
        out = (
            opened,
            active,
            r.status_code,
            r.json().get("detail"),
            len(looks_in(writes, mark, last)),
            len(slot_times(writes)),
            repaint_results(w).count(str(CommandStatus.SUPERSEDED)),
        )
        await w.close()
        return out

    opened, active, code, detail, looks, slots, superseded = run_virtual(main)
    assert opened and active and code == 409 and detail == REPAINT_MESSAGE
    assert looks == 0 and slots == 9 and superseded == 0


def test_a_try_on_start_in_the_quiet_window_leaves_no_trace(no_threads):
    """Q5: a try-on refused in the window is refused before it is a session: no lab trial_end row, no session used."""
    from conductor.design_lab import trial as T
    from conductor.design_lab.draft import SceneDraft

    async def main():
        w = await show()
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            await publish(c, vid)
            opened = await wait_quiet(w)
            ring0 = len(w.services.telemetry.ring)
            sessions0 = w.engine._trial_sessions
            trial = T.build_trial(
                w.lab,
                SceneDraft.model_validate(brighter(TIDES)),
                version=None,
                now_mono=w.clock.monotonic(),
                now=w.clock.now(),
            )
            tried = await w.engine.start_trial(trial)
            rows = [e for e in list(w.services.telemetry.ring)[ring0:] if e["kind"] == "lab"]
            out = (opened, tried.refused, tried.message, rows, sessions0, w.engine._trial_sessions)
            await w.engine.wait_repaint()
        await w.close()
        return out

    opened, refused, message, rows, s0, s1 = run_virtual(main)
    assert opened and refused and message == REPAINT_MESSAGE
    assert rows == [] and s0 == s1


@pytest.mark.parametrize("action", ["none", "stop", "pause"])
def test_a_try_on_running_when_the_window_opens_ends_first(no_threads, action):
    """Q30: a try-on started while the repaint is queued ends when the window opens (before it, not in it), so
    stopping it or pausing guest input in the window sends nothing; nine slot writes, none superseded."""
    from conductor.design_lab import trial as T
    from conductor.design_lab.draft import SceneDraft

    async def main():
        w = await show()
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.engine.wait_idle()
        async with client_of(w) as c:
            v1 = await save(c, {GOLDEN: golden_layout(45)})
            await publish(c, v1)
            await w.engine.wait_repaint()
            await w.run_for(30)
            v2 = await save(c, {GOLDEN: golden_layout(60)})
            await publish(c, v2)
            await w.run_for(60)  # inside the gate: still queued
            trial = T.build_trial(
                w.lab,
                SceneDraft.model_validate(brighter(TIDES)),
                version=None,
                now_mono=w.clock.monotonic(),
                now=w.clock.now(),
            )
            started = await w.engine.start_trial(trial)
            await w.engine.wait_idle()
            writes = spy_writes(w)
            opened = await wait_quiet(w)
            if action != "none":
                await wait_first_slot_write(w, writes)
            in_window = w.engine.trial is not None
            mark = len(writes)
            if action == "stop":
                await c.post("/api/lab/trial/stop", json={})
            elif action == "pause":
                w.engine.pause_guest_input(True, source="ops")
            await w.engine.wait_repaint()
        last = max(slot_times(writes))
        out = (
            started.refused,
            opened,
            in_window,
            len(looks_in(writes, mark, last)),
            len(slot_times(writes)),
            repaint_results(w).count(str(CommandStatus.SUPERSEDED)),
        )
        await w.close()
        return out

    refused, opened, in_window, looks, slots, superseded = run_virtual(main)
    assert not refused and opened and not in_window
    assert looks == 0 and slots == 9 and superseded == 0


def test_two_painted_scenes_with_the_first_showing_never_supersede_a_slot_write(no_threads):
    """Q31: Rainbow Row Sunset shows at T1 and one publish changes it and Golden Hour: its T1 re-pick after its own
    repaint waits for Golden Hour's window, so no look write lands among Golden Hour's slot writes (the mutant: 9
    SUPERSEDED and 18 writes)."""

    async def main():
        w = await show()
        await w.engine.request_mode(RRS, source="ops")
        await w.engine.wait_idle()
        tier = w.engine.current_plan().tier
        writes = spy_writes(w)
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour(), RRS: layout_of(RRS, 15)})
            await publish(c, vid)
        await w.engine.wait_repaint()
        await w.run_for(5)
        await w.engine.wait_idle()
        golden = slot_times(writes, SLOT)
        looks = [t for t, a, rows in writes if not is_slot((t, a, rows)) and min(golden) <= t <= max(golden)]
        out = tier, len(golden), looks, repaint_results(w).count(str(CommandStatus.SUPERSEDED))
        await w.close()
        return out

    tier, golden, looks, superseded = run_virtual(main)
    assert tier is Tier.T1 and golden == 9 and looks == [] and superseded == 0


# ---- P3: a later publish carries a queued scene over -------------------------------------------------------------------
def test_a_later_publish_that_leaves_a_queued_scene_alone_carries_its_repaint_over(no_threads):
    async def main():
        w = await show()
        writes = spy_writes(w)
        async with client_of(w) as c:
            v1 = await save(c, {GOLDEN: golden_layout(45)})
            await publish(c, v1)
            await w.engine.wait_repaint()
            t0 = min(slot_times(writes))
            first = len(writes)
            await w.run_for(60 - (w.clock.monotonic() - t0))
            v2 = await save(c, {GOLDEN: golden_layout(60)})
            await publish(c, v2)  # queued behind the gate
            due2 = w.engine.queued_repaints()[GOLDEN].due_mono
            await w.run_for(5)
            # Lowcountry Calm's brightness only; Golden Hour's queued draft kept (a version applies over git)
            v3 = await save(c, {GOLDEN: golden_layout(60), CALM: brighter(CALM)})
            asked = (await ask(c, v3)).json()
            r3 = (await publish(c, v3)).json()
            due3 = {m: j.due_mono for m, j in w.engine.queued_repaints().items()}
            persisted = dict(w.looks.file.queued)
        await w.engine.wait_repaint()
        second = slot_times(writes[first:])
        out = (t0, asked, r3["report"]["repaint"], due2, due3, persisted, second, GOLDEN in w.engine.t1_hold)
        await w.close()
        return out

    t0, asked, report, due2, due3, persisted, second, held = run_virtual(main)
    assert [c["scene"] for c in asked["changes"]] == [CALM]
    assert [j["scene"] for j in asked["repaint"]] == [GOLDEN] and [j["scene"] for j in report] == [GOLDEN]
    assert 45 <= asked["repaint"][0]["in_s"] <= 60
    assert due3 == {GOLDEN: pytest.approx(due2)} and set(persisted) == {GOLDEN}
    assert len(second) == 9 and min(second) >= t0 + INTERVAL - 1e-6 and not held


# ---- L3: the repaint's bookkeeping keeps no history copy ----------------------------------------------------------------
def test_a_painted_publish_and_its_repaint_add_no_history_copy_of_their_own(no_threads):
    """The first publish has no file to copy; its repaint's bookkeeping (started, finished) keeps none either; a
    second painted publish keeps exactly one copy and its repaint none (the mutant: 2, then 5)."""

    async def main():
        w = await show()
        history = w.data / "overrides" / "history"

        def copies() -> int:
            return len(list(history.glob("show-looks-*.yaml")))

        async with client_of(w) as c:
            v1 = await save(c, {GOLDEN: golden_layout(45)})
            await publish(c, v1)
            await w.engine.wait_repaint()
            first = copies()
            await w.run_for(INTERVAL + 5)
            v2 = await save(c, {GOLDEN: golden_layout(60)})
            await publish(c, v2)
            published = copies()
            await w.engine.wait_repaint()
            repainted = copies()
        out = first, published, repainted, w.engine.stats["repaints"]
        await w.close()
        return out

    first, published, repainted, repaints = run_virtual(main)
    assert repaints == 2 and first == 0 and published == 1 and repainted == 1


def test_bookkeeping_writes_keep_no_history_copy(tmp_path):
    settings = load_settings(CONFIG_DIR)
    canopy = load_canopy_config(CONFIG_DIR / "canopy.yaml")
    looks = L.load_live_looks(CONFIG_DIR, tmp_path, settings=settings, canopy=canopy, now=1792800000.0)
    history = tmp_path / "overrides" / "history"
    row = L.HistoryEntry(id="p0001", kind="approved", name="the approved looks", published_at=1.0)
    L.write_looks(looks, L.ShowLooksFile(seq=1, history=[row], queued={GOLDEN: 5.0, TIDES: 6.0}), now=1.0)
    L.write_looks(looks, looks.file.model_copy(update={"seq": 2}), now=2.0)  # a publish over a file: one copy

    def copies() -> list[str]:
        return sorted(p.name for p in history.glob("show-looks-*.yaml"))

    before = copies()
    assert len(before) == 1
    services = SimpleNamespace(extras={L.LOOKS_KEY: looks}, clock=VirtualClock())
    L.note_repaint(services, "started", {"mode": GOLDEN, "at": 1792800010.0})
    L.note_repaint(
        services,
        "finished",
        {"mode": GOLDEN, "publication": "p0001", "started": True, "slot": SLOT, "applied": 9},
    )
    L.forget_queued(looks, [TIDES], now=1792800020.0)
    on_disk = L.load_live_looks(CONFIG_DIR, tmp_path, settings=settings, canopy=canopy, now=1792800030.0).file
    assert GOLDEN in on_disk.repaints and on_disk.queued == {}
    assert on_disk.history[0].repaint == {GOLDEN: {"slot": SLOT, "applied": 9, "skipped": 0, "failed": 0}}
    assert copies() == before
