"""The operator repaint (D68 PR B2, docs/live-lab/PLAN.md §2.6–§2.7, §5.2 B2), on the virtual clock over the shipped
config/ (tests/live_lab.py's show; the simulator's nine modules have Golden Hour's custom-3 commissioned at start).

- A publish of a new painted design repaints the slot once per module (≥ 1 s between writes to one device), records
  each readback like commissioning (the reconciler never calls the published design stale or reverts it), then one
  T1 burst re-selects it.
- The quiet window: guests, ops, the reconciler and Gate R wait; the commands in flight finish first; a MISMATCH on a
  pending module never starts a reconciler rewrite.
- A failed module: marks cleared, `slot_written` stamped at the attempt, the reconciler's rewrite 600 s later.
- Partly commissioned, a module held by the module page, a hand-painted slot, a kit without Gate C / map.
- The 2-min gate: queued (never refused), enforced inside `start_repaint`, persisted and re-queued after a restart,
  clamped against wall-clock steps; Revert and Undo are never blocked; a newer publish replaces a queued job.
"""

from __future__ import annotations

import dataclasses
import itertools
import shutil
from typing import Any

import pytest
import yaml
from live_lab import (
    DEV,
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
    show,
)

from conductor import rows as R
from conductor.canopy import REPAINT_MESSAGE, RepaintJob
from conductor.clock import run_virtual
from conductor.drivers.base import Outcome, WriteResult
from conductor.engine_api import Tier
from conductor.executor_api import CommandStatus
from conductor.looks import compile_mode
from conductor.sim.settings import SimSettings

SLOT = "custom-3"
GOODNIGHT = "goodnight-glow"
INTERVAL = 120.0


@pytest.fixture
def no_threads(monkeypatch):
    install_no_threads(monkeypatch)


def paced_sim() -> SimSettings:
    """The simulator answering 429 (and counting a violation) for a second write to a device within 1 s."""
    return SimSettings.from_acceptance().with_(
        throttle_429_rate=0.0, silent_drop_rate=0.0, per_device_min_interval_s=1.0
    )


def spy_writes(w) -> list[tuple[float, str, list]]:
    writes: list[tuple[float, str, list]] = []
    driver = w.services.engine_driver
    original = driver.write

    async def write(address, rows, **kw):
        writes.append((w.clock.monotonic(), address, list(rows)))
        return await original(address, rows, **kw)

    driver.write = write
    return writes


def slot_writes(writes, slot: str = SLOT) -> list:
    return [
        x
        for x in writes
        if any(r.function_class == R.FRAME_CLASS and r.function_instance == slot for r in x[2])
    ]


def look_writes(writes) -> list:
    return [x for x in writes if not any(r.function_class == R.FRAME_CLASS for r in x[2])]


def overlay_hashes(w, slot: str = SLOT) -> dict[str, str]:
    path = w.data / "overrides" / "registry.yaml"
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    devices = (raw or {}).get("devices") or {}
    return {d: (p.get("slot_hashes") or {}).get(slot) for d, p in devices.items() if isinstance(p, dict)}


def live_hashes(w, mode: str = GOLDEN) -> dict[str, str]:
    plan = compile_mode(mode, Tier.T1, w.wiring.context())
    return {d: mt.slot_hash for d, mt in plan.modules.items()}


async def readback_stale(w, devices) -> list:
    """`stale_slot` of custom-3 on each module, judged from the module's own readback now."""
    out = []
    for device_id in devices:
        target = w.engine.target_of(device_id)
        rr = await target.driver.read_state(target.address)
        out.append(w.engine.stale_slot(device_id, SLOT, rr.get(R.FRAME_CLASS, SLOT)))
    return out


def golden_layout(angle: int) -> dict:
    return edited(GOLDEN, layout={"kind": "linear", "angle_deg": angle})


async def wait_quiet(w, limit: float = 30.0) -> bool:
    """Run the clock until the repaint's quiet window opens (True) or `limit` virtual seconds pass."""
    waited = 0.0
    while not w.engine.repaint_quiet and waited < limit:
        await w.run_for(0.05)
        waited += 0.05
    return w.engine.repaint_quiet


# ---- the repaint itself -------------------------------------------------------------------------------------------------
def test_a_painted_publish_repaints_each_module_once_and_the_reconciler_never_reverts_it(no_threads):
    """Golden Hour at T1, a layout change published: one slot write per module (9), ≥ 1 s between writes to any one
    device (the simulator counts a violation otherwise), each readback recorded in the registry overlay as the live
    compile's hash, then exactly one T1 burst; over 30 virtual minutes the reconciler rewrites nothing, raises no
    "waiting"/stale alert and never sends the approved design back."""

    async def main():
        w = await show(sim=paced_sim())
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.engine.wait_idle()
        before = overlay_hashes(w)
        rewrites0 = w.engine.reconciler.stats["slot_rewrites"]
        writes = spy_writes(w)
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            first = (await ask(c, vid)).json()
            r = await publish(c, vid)
        held_now = (w.engine.current_plan().tier, GOLDEN in w.engine.t1_hold, w.engine.repainting)
        await w.engine.wait_repaint()
        await w.run_for(5)
        await w.engine.wait_idle()
        repainted = list(writes)
        after = overlay_hashes(w)
        expected = live_hashes(w)
        stale = await readback_stale(w, expected)
        tier_after = w.engine.current_plan().tier
        mark = len(writes)
        for _ in range(
            10
        ):  # 30 virtual minutes, Golden Hour chosen again every 3 min (the idle hold's period)
            await w.run_for(150)
            await w.engine.request_mode(GOLDEN, source="ops")
            await w.engine.wait_idle()
            await w.run_for(30)
        later = writes[mark:]
        stale_later = await readback_stale(w, expected)
        alerts = [
            a.message for a in w.alerts() if "custom-3" in a.message and "Published looks" not in a.message
        ]
        out = {
            "first": first,
            "status": r.status_code,
            "body": r.json(),
            "held_now": held_now,
            "repainted": repainted,
            "before": before,
            "after": after,
            "expected": expected,
            "stale": stale + stale_later,
            "tier_after": tier_after,
            "later": later,
            "rewrites": w.engine.reconciler.stats["slot_rewrites"] - rewrites0,
            "alerts": alerts,
            "violations": w.services.engine_driver.stats()["min_interval_violations_total"],
            "record": w.services.registry.get(DEV).slot_record(SLOT),
            "final": (w.engine.current_plan().mode_id, w.engine.current_plan().tier),
            "repaints": dict(w.looks.file.repaints),
            "queued": dict(w.looks.file.queued),
            "history": w.looks.file.history[0].repaint,
        }
        await w.close()
        return out

    out = run_virtual(main)
    first = out["first"]
    assert (
        first["confirm_required"] and first["repaint"][0]["writes"] == 9 and first["repaint"][0]["in_s"] == 0
    )
    assert (
        "Golden Hour's painted design is repainted into custom-3 on 9 modules (one slot write each)."
        in first["confirm"]
    )
    assert first["cost"].endswith(
        "repaints custom-3 on 9 modules: up to 27 slot writes (one each unless a write doesn't land), 9 reads, then "
        "one T1 burst"
    )
    assert out["status"] == 200, out["body"]
    assert out["body"]["report"]["repaint"] == [{"scene": GOLDEN, "slot": SLOT, "modules": 9, "in_s": 0}]
    tier, held, repainting = out["held_now"]
    assert tier is not Tier.T1 and held and repainting  # the publish answers held, before the repaint
    slots = slot_writes(out["repainted"])
    assert len(slots) == 9 and len({a for _t, a, _r in slots}) == 9  # exactly one slot write per module
    by_device: dict[str, list[float]] = {}
    for t, a, _r in out["repainted"]:
        by_device.setdefault(a, []).append(t)
    assert all(b - a >= 1.0 - 1e-9 for ts in by_device.values() for a, b in itertools.pairwise(ts))
    assert out["violations"] == 0
    assert set(out["after"]) >= set(out["expected"])
    assert all(out["after"][d] == h for d, h in out["expected"].items()), (out["after"], out["expected"])
    assert all(
        out["before"][d] != h for d, h in out["expected"].items()
    )  # they held the approved design before
    assert out["record"]["origin"] == "api" and out["record"]["hash"] == out["expected"][DEV]
    assert out["stale"] == [None] * 18  # current right after, and after 30 minutes
    assert out["tier_after"] is Tier.T1 and out["final"] == (GOLDEN, Tier.T1)
    last_slot = max(t for t, _a, _r in slots)
    reselect = [x for x in look_writes(out["repainted"]) if x[0] > last_slot]
    assert len(reselect) == 9 and len({a for _t, a, _r in reselect}) == 9  # one T1 burst: the re-select
    assert slot_writes(out["later"]) == [] and slot_writes(out["later"], "custom-1") == []
    assert out["rewrites"] == 0 and out["alerts"] == []  # never stale, never "waiting", never reverted
    assert GOLDEN in out["repaints"] and out["queued"] == {}
    assert out["history"] == {GOLDEN: {"slot": SLOT, "applied": 9, "skipped": 0, "failed": 0}}


def test_the_quiet_window_holds_guests_the_reconciler_and_gate_r(no_threads):
    """Guest brightness taps every 0.5 s through a nine-module repaint: exactly 9 slot writes, no slot result
    SUPERSEDED, each tap refused with "One moment: the canopy is changing." and a retry-after (the kiosk route says
    the same, 503); a reconciler tick sends nothing; Gate R and a try-on are refused; the re-pick burst the publish
    sent (one look PUT per module) had all its results before the first slot header read; an overdue idle return
    waits too."""
    from conductor.design_lab import trial as T
    from conductor.design_lab.draft import SceneDraft

    async def main():
        w = await show()
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.engine.wait_idle()
        writes = spy_writes(w)
        in_flight: list[int] = []  # the engine's show bursts still running at each slot header read
        original_command = w.engine._slot_command

        async def slot_command(*args, **kw):
            in_flight.append(len(w.engine._tasks))
            return await original_command(*args, **kw)

        w.engine._slot_command = slot_command
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            await publish(c, vid)
            repick = len(w.engine._tasks)
            opened = await wait_quiet(w)
            taps, kiosk = [], None
            sends_before = len(writes)
            # the idle return is overdue: outside the window the reconciler's tick would ask for the idle mode now
            changed = w.engine.last_change_mono
            w.engine.last_change_mono = -1e9
            await w.engine.reconciler.tick()
            idle_alerts = [a.message for a in w.alerts() if a.message.startswith("Idle return refused")]
            w.engine.last_change_mono = changed
            tick_sent = len(writes) - sends_before + len(idle_alerts)
            from conductor.rehearsal import RehearsalRefused

            try:
                w.engine.rehearsal_preview(GOLDEN, 1)
                gate_r = None
            except RehearsalRefused as e:
                gate_r = str(e)
            trial = T.build_trial(
                w.lab,
                SceneDraft.model_validate(brighter(TIDES)),
                version=None,
                now_mono=w.clock.monotonic(),
                now=w.clock.now(),
            )
            tried = await w.engine.start_trial(trial)
            level = 40
            while w.engine.repaint_quiet:
                st = await w.engine.set_guest_brightness(level, source="kiosk")
                taps.append((st.refused, st.message, st.retry_after_s))
                if kiosk is None:
                    kiosk = await c.post("/api/kiosk/scene", json={"scene": TIDES})
                level = 41 if level == 40 else 40
                await w.run_for(0.5)
        await w.engine.wait_repaint()
        slot_results = [
            e
            for e in w.services.telemetry.ring
            if e["kind"] == "command" and "publish repaint" in str(e.get("label", ""))
        ]
        out = (opened, repick, in_flight, writes, taps, kiosk, tick_sent, gate_r, tried, slot_results)
        await w.close()
        return out

    opened, repick, in_flight, writes, taps, kiosk, tick_sent, gate_r, tried, results = run_virtual(main)
    assert opened and taps, "no tap landed inside the quiet window"
    assert len(slot_writes(writes)) == 9
    assert all(r for r, _m, _s in taps) and {m for _r, m, _s in taps} == {REPAINT_MESSAGE}
    assert all(s is not None and s > 0 for _r, _m, s in taps)
    assert kiosk.status_code == 503 and kiosk.json()["detail"] == REPAINT_MESSAGE
    assert int(kiosk.headers["Retry-After"]) >= 1
    assert tick_sent == 0
    assert gate_r is not None and "repainting" in gate_r
    assert tried.refused and tried.message == REPAINT_MESSAGE
    statuses = [e.get("status") for e in results]
    assert statuses and str(CommandStatus.SUPERSEDED) not in statuses, statuses
    assert repick >= 1 and in_flight and set(in_flight) == {0}  # the re-pick's burst was over first
    last_slot = max(t for t, _a, _r in slot_writes(writes))
    looks_before = look_writes([x for x in writes if x[0] <= last_slot])
    assert len(looks_before) == 9  # only the re-pick (one per module) and the slots until the last slot write


def test_a_mismatch_on_a_pending_module_never_starts_a_reconciler_rewrite(no_threads):
    """While a repaint is pending (queued behind the gate), a readback whose slot differs from its record — on any
    path that reaches check_slots — is skipped: no rewrite, no drift alert. Mutation: drop the guard → it rewrites."""

    async def main():
        w = await show()
        async with client_of(w) as c:
            v1 = await save(c, {GOLDEN: golden_layout(45)})
            await publish(c, v1)
            await w.engine.wait_repaint()
            v2 = await save(c, {GOLDEN: golden_layout(60)})
            r = await publish(c, v2)  # queued: the gate has ~120 s left
        pending = w.engine.repaint_pending
        target = w.engine.target_of(DEV)
        rr = await target.driver.read_state(target.address)
        value = rr.get(R.FRAME_CLASS, SLOT)
        entries = R.frame_entries(value)
        changed = [dataclasses.replace(entries[0], r=(entries[0].r + 40) % 256), *entries[1:]]
        drifted_value = R.frame_value(value, changed, expected_length=len(entries))
        assert R.slot_hash(drifted_value) not in (None, R.slot_hash(value))
        drifted = [
            R.slot_write(SLOT, drifted_value) if row.key == (R.FRAME_CLASS, SLOT) else row for row in rr.rows
        ]
        w.engine.slot_written.pop((DEV, SLOT), None)  # the 10-min limit isn't what holds it back
        before = (w.engine.reconciler.stats["slot_rewrites"], w.engine.reconciler.stats["drift"])
        w.engine.reconciler.check_slots(DEV, target, drifted)
        after = (w.engine.reconciler.stats["slot_rewrites"], w.engine.reconciler.stats["drift"])
        rewriting = [t for t in w.engine.reconciler._rewrites if "rewrite" in t.get_name()]
        await w.close()
        return r.status_code, pending, before, after, rewriting

    status, pending, before, after, rewriting = run_virtual(main)
    assert status == 200 and (DEV, SLOT) in pending
    assert after == before and rewriting == []


def test_a_failed_module_is_left_to_the_reconciler_600_s_after_the_attempt(no_threads):
    """One module's slot write is refused (HTTP 400, every attempt): the others are repainted; its marks are
    cleared and `slot_written` stamped at the attempt, so the reconciler leaves it for 600 s, then rewrites it once;
    the scene stays at module level until then, then T1."""
    victim = "canopy-r1c1"

    async def main():
        w = await show()
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.engine.wait_idle()
        driver = w.services.engine_driver
        address = w.engine.target_of(victim).address
        original = driver.write
        broken = {"on": True}
        attempts: list[float] = []

        async def write(addr, rows, **kw):
            if broken["on"] and addr == address and any(r.function_class == R.FRAME_CLASS for r in rows):
                attempts.append(w.clock.monotonic())
                return WriteResult(addr, Outcome.CLIENT_ERROR, 400, error="HTTP 400 (test)")
            return await original(addr, rows, **kw)

        driver.write = write
        writes = spy_writes(w)
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            await publish(c, vid)
        await w.engine.wait_repaint()
        broken["on"] = False
        done = w.clock.monotonic()
        pending = w.engine.repaint_pending
        stamped = w.engine.slot_written.get((victim, SLOT))
        status = w.engine.repaint_json()
        alerts = [a.message for a in w.alerts() if a.message.startswith("Published looks: Pinker Hour:")]
        tiers = []
        for _ in range(66):  # 11 virtual minutes; Golden Hour chosen again before each idle return
            await w.run_for(10)
            if w.clock.monotonic() - w.engine.last_change_mono > 150:
                await w.engine.request_mode(GOLDEN, source="ops")
            tiers.append((w.clock.monotonic(), w.engine.current_plan().mode_id, w.engine.current_plan().tier))
        # D75: the hold's lift re-picks the look, and a re-pick never cuts a run short: a stepped run going then
        # settles onto T1 when it ends
        runner = w.engine.runner
        if runner is not None and runner.running:
            await w.run_for(runner.t0 + runner.count * runner.step_s - w.clock.monotonic() + 1)
        tiers.append((w.clock.monotonic(), w.engine.current_plan().mode_id, w.engine.current_plan().tier))
        rewrites = [t for t, a, _r in slot_writes(writes) if a == address and t > done]
        out = (attempts, done, pending, stamped, status, alerts, rewrites, tiers, w.engine.t1_hold)
        await w.close()
        return out

    attempts, _done, pending, stamped, status, alerts, rewrites, tiers, hold = run_virtual(main)
    assert len(attempts) == 3  # one, then at most twice more within the job (it never landed)
    assert pending == frozenset() and attempts[-2] < stamped <= attempts[-1]  # stamped at the last attempt
    rows = {m["id"]: m["status"] for m in status["modules"]}
    assert rows[victim] == "failed" and list(rows.values()).count("applied") == 8
    assert (
        alerts and "repainted on 8 of 9 modules (Module 5:" in alerts[0] and "reconciler retries" in alerts[0]
    )
    assert (
        len(rewrites) == 1 and rewrites[0] >= stamped + 600
    )  # the reconciler, once, ≥ 600 s after the attempt
    held = [tier for t, mode, tier in tiers if mode == GOLDEN and t < rewrites[0]]
    assert held and all(tier is not Tier.T1 for tier in held)
    assert tiers[-1][2] is Tier.T1 and GOLDEN not in hold


# ---- who gets repainted -------------------------------------------------------------------------------------------------
GATE_C = ["canopy-r0c0", "canopy-r0c1", "canopy-r0c2", "canopy-r1c0", "canopy-r1c1"]


MAPLESS = "canopy-r1c2"


def _partly_commissioned(services) -> None:
    """Hardware rules over the simulator: five modules with their own Gate C (their records come below), one more
    with Gate C whose saved map isn't valid for its kit (it can't take T1), the other three nothing."""
    registry = services.registry
    for device_id in [*GATE_C, MAPLESS]:
        address = registry.address(device_id, services.engine_driver.name)
        patch: dict[str, Any] = {"flags": {"t1_frames": True}, "flags_proven_on": address}
        if device_id == MAPLESS:
            patch["framebuffer_mapping"] = {
                "frame_length": 30,
                "indices": list(range(30)),
                "proven_on": "another-kit",
            }
        services.update_registry(device_id, patch)


def _restricted(wiring, ids, **kw):
    """The show's compile context (the live looks, or `modes`/`recipes`) over only these modules."""
    import dataclasses

    ctx = wiring.context(**kw)
    entries = [e for e in wiring.services.registry.canopy_modules() if e.id in ids]
    return dataclasses.replace(ctx, registry=entries)


def test_a_partly_commissioned_canopy_repaints_only_its_own_commissioned_modules(no_threads, monkeypatch):
    """5 of 9 modules commissioned with their own Gate C (one more with Gate C but no valid map, three nothing):
    the confirm's sentence (b) names the five; the repaint writes exactly those five, their records equal the live
    compile; the map-less module (even with an api record) never blocks them; a sixth commissioned later gets the
    published design."""

    async def main():
        w = await show(live=True, monkeypatch=monkeypatch, prepare=_partly_commissioned)
        wiring = w.wiring
        git_plan = compile_mode(GOLDEN, Tier.T1, _restricted(wiring, GATE_C))
        for device_id in [*GATE_C, MAPLESS]:  # the approved design, as commissioning recorded it
            entry = w.services.registry.get(device_id)
            digest = git_plan.modules[device_id].slot_hash if device_id in GATE_C else "0123456789abcdef"
            w.services.update_registry(
                device_id, entry.slot_patch(SLOT, digest=digest, origin="api", effects=None)
            )
        writes = spy_writes(w)
        async with client_of(w) as c:
            info = next(s for s in (await c.get("/api/lab/catalog")).json()["scenes"] if s["id"] == GOLDEN)[
                "repaint"
            ]
            vid = await save(c, {GOLDEN: pinker_hour()})
            first = (await ask(c, vid)).json()
            r = await publish(c, vid)
        await w.engine.wait_repaint()
        await w.engine.wait_idle()
        writes = list(writes)  # the repaint's (the sixth module's commissioning below writes too)
        live_plan = compile_mode(GOLDEN, Tier.T1, _restricted(wiring, GATE_C))
        records = {d: w.services.registry.get(d).slot_record(SLOT) for d in GATE_C}
        # a sixth module commissioned later (its own Gate C now): commissioning compiles from the live looks
        sixth = "canopy-r2c0"
        address = w.services.registry.address(sixth, w.services.engine_driver.name)
        w.services.update_registry(sixth, {"flags": {"t1_frames": True}, "flags_proven_on": address})
        plan6 = compile_mode(GOLDEN, Tier.T1, _restricted(wiring, [sixth]))
        report6 = await w.engine._commission_plan(plan6, "ops")
        record6 = w.services.registry.get(sixth).slot_record(SLOT)
        git6 = compile_mode(
            GOLDEN,
            Tier.T1,
            _restricted(wiring, [sixth], modes=w.looks.git.modes, recipes=w.looks.git.recipes),
        )
        out = (
            info,
            first,
            r.status_code,
            writes,
            records,
            live_plan,
            git_plan,
            report6,
            record6,
            plan6,
            git6,
        )
        await w.close()
        return out

    info, first, status, writes, records, live_plan, git_plan, report6, record6, plan6, git6 = run_virtual(
        main
    )
    assert info["eligible"] == 5 and info["available"] is True and len(info["commissioned"]) == 6
    assert info["sentence"] == (
        "custom-3 is commissioned on 5 of 9 modules (Module 1, Module 2, Module 3, Module 4, Module 5); publishing "
        "repaints those of the 5 whose design changes (one slot write each; the confirm names them). The other 4 get "
        "the published design when they are commissioned."
    )
    [job] = first["repaint"]
    assert job["writes"] == 5 and job["modules"] == [
        "Module 1",
        "Module 2",
        "Module 3",
        "Module 4",
        "Module 5",
    ]
    assert status == 200
    slots = slot_writes(writes)
    addresses = {a for _t, a, _r in slots}
    assert len(slots) == 5 and addresses == {f"sim-{d}" for d in GATE_C}
    for device_id in GATE_C:
        assert records[device_id]["hash"] == live_plan.modules[device_id].slot_hash
        assert records[device_id]["hash"] != git_plan.modules[device_id].slot_hash
    assert report6["modules"]["canopy-r2c0"]["status"] == "applied"
    assert record6["hash"] == plan6.modules["canopy-r2c0"].slot_hash != git6.modules["canopy-r2c0"].slot_hash


def test_a_module_held_by_the_module_page_is_left_locked_then_the_reconciler_takes_it(no_threads):
    """A module held by /ops/module during the repaint: `locked`, never written by it (its `slot_written` stays the
    start-up commissioning's); after the page lets go, the reconciler's stale rule rewrites it once its 600 s limit
    from that commissioning is up, and the hold lifts."""
    victim = "canopy-r2c2"

    async def main():
        w = await show()
        address = w.engine.target_of(victim).address
        commissioned = w.engine.slot_written.get((victim, SLOT))
        lease = w.services.locks.acquire(address, "module page (test)")
        writes = spy_writes(w)
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            await publish(c, vid)
        await w.engine.wait_repaint()
        status = {m["id"]: m["status"] for m in w.engine.repaint_json()["modules"]}
        during = [a for _t, a, _r in slot_writes(writes)]
        held = GOLDEN in w.engine.t1_hold
        stamped = w.engine.slot_written.get((victim, SLOT))
        w.services.locks.release(address, lease.token)
        await w.run_for(commissioned + 600 + 90 - w.clock.monotonic())  # the slot check runs once a minute
        after = [a for _t, a, _r in slot_writes(writes)]
        record = w.services.registry.get(victim).slot_record(SLOT)
        stamped = (commissioned, stamped)
        out = (status, during, held, stamped, after, record, live_hashes(w)[victim], w.engine.t1_hold)
        await w.close()
        return out

    status, during, held, stamped, after, record, expected, hold = run_virtual(main)
    assert status[victim] == "locked" and list(status.values()).count("applied") == 8
    assert f"sim-{victim}" not in during and len(during) == 8 and held and stamped[0] == stamped[1]
    assert after.count(f"sim-{victim}") == 1 and record["hash"] == expected and GOLDEN not in hold


def test_the_repaint_never_writes_over_a_hand_painted_slot():
    """Unit: `_commission_plan(min_interval=0, verb="publish repaint")` skips an app record (FakeCompiler world)."""
    from fake_compiler import make_world

    async def main():
        w = make_world()
        engine = w.engine
        await engine.commission(source="ops")
        device = "canopy-r0c0"
        engine.slot_records[(device, "custom-3")] = {
            "hash": "abc",
            "mode": "golden",
            "tier": "canvas",
            "origin": "app",
            "effects": None,
        }
        plan = engine.deps.compile("golden", Tier.T1)
        sent: list[str] = []
        original = w.sim.write

        async def write(address, rows, **kw):
            if any(r.function_class == R.FRAME_CLASS for r in rows):
                sent.append(address)
            return await original(address, rows, **kw)

        w.sim.write = write
        report = await engine._commission_plan(
            plan, "looks", min_interval=0.0, verb="publish repaint", locked=True
        )
        await w.close()
        return report, sent

    report, sent = run_virtual(main)
    assert report["modules"]["canopy-r0c0"]["status"] == "skipped"
    assert "hand-painted" in report["modules"]["canopy-r0c0"]["reason"]
    assert "sim-canopy-r0c0" not in sent and len(sent) == 8  # min_interval 0: the others are written again


# ---- the 2-min gate: queued, never refused ------------------------------------------------------------------------------
def test_a_republish_within_the_gate_installs_at_once_and_queues_the_repaint(no_threads):
    """Publish a new Golden Hour design (repainted at once); 60 s later another design: 200, installed at once
    (held below T1), its repaint queued `in_s` ≈ 60 (the confirm says so), no slot write before 120 s, one per
    module at 120 s. Revert to approved inside the gate: installed at once (never 409), its repaint queued too. A
    brightness-only republish repaints nothing."""

    async def main():
        w = await show()
        await w.engine.request_mode(GOLDEN, source="ops")
        await w.engine.wait_idle()
        writes = spy_writes(w)
        async with client_of(w) as c:
            v1 = await save(c, {GOLDEN: golden_layout(45)})
            t0 = w.clock.monotonic()
            await publish(c, v1)
            await w.engine.wait_repaint()
            first_slots = [t for t, _a, _r in slot_writes(writes)]
            await w.run_for(60 - (w.clock.monotonic() - t0))
            v2 = await save(c, {GOLDEN: golden_layout(60)})
            asked = (await ask(c, v2)).json()
            r2 = await publish(c, v2)
            installed = (
                w.looks.trio.modes.modes[GOLDEN].techniques.canvas.model_dump(),
                w.engine.current_plan().tier,
            )
            queued_status = w.engine.repaint_json()
            persisted = dict(w.looks.file.queued)
            await w.run_for(20)
            revert_ask = (await ask(c, "approved")).json()
            r3 = await publish(c, "approved")
            after_revert = (w.looks.source, w.engine.current_plan().tier, w.engine.queued_repaints())
            undo = await publish(c, "previous")  # Undo inside the gate: never refused either; still queued
            after_undo = (w.looks.source, dict(w.engine.queued_repaints()), w.looks.file.queued)
            await w.engine.wait_repaint()
            second_slots = [t for t, _a, _r in slot_writes(writes)][len(first_slots) :]
            await w.run_for(200)
            design = golden_layout(60)  # what Undo put back, only brighter
            v4 = await save(c, {GOLDEN: {**design, "brightness": design["brightness"] - 10}})
            asked4 = (await ask(c, v4)).json()
            r4 = await publish(c, v4)
            jobs4 = w.engine.queued_repaints()
        out = {
            "t0": t0,
            "first": first_slots,
            "asked": asked,
            "r2": (r2.status_code, r2.json()),
            "installed": installed,
            "queued_status": queued_status,
            "persisted": persisted,
            "revert_ask": revert_ask,
            "r3": (r3.status_code, r3.json()),
            "undo": (undo.status_code, undo.json()),
            "after_undo": after_undo,
            "after_revert": after_revert,
            "second": second_slots,
            "asked4": asked4,
            "r4": r4.status_code,
            "jobs4": jobs4,
            "now": w.clock.now(),
        }
        await w.close()
        return out

    out = run_virtual(main)
    t0 = out["t0"]
    assert len(out["first"]) == 9 and max(out["first"]) - t0 < 20
    [job] = out["asked"]["repaint"]
    assert 50 <= job["in_s"] <= 70 and ", in " in out["asked"]["confirm"]
    status, body = out["r2"]
    assert status == 200, body
    [queued] = body["report"]["repaint"]
    assert 50 <= queued["in_s"] <= 70
    assert out["installed"][1] is not Tier.T1  # installed at once, held at module level
    assert out["queued_status"]["queued_until"] is not None and out["queued_status"]["started_at"] is None
    assert set(out["persisted"]) == {GOLDEN}
    assert out["r3"][0] == 200, out["r3"][1]  # Revert inside the gate: never refused
    assert out["undo"][0] == 200, out["undo"][1]  # nor Undo
    source, jobs_after_undo, file_queued = out["after_undo"]
    assert source == "published" and set(jobs_after_undo) == {GOLDEN} and set(file_queued) == {GOLDEN}
    assert out["revert_ask"]["repaint"] and out["revert_ask"]["repaint"][0]["in_s"] > 0
    source, tier, jobs = out["after_revert"]
    assert source == "approved" and tier is not Tier.T1 and set(jobs) == {GOLDEN}
    assert len(out["second"]) == 9 and min(out["second"]) >= t0 + INTERVAL - 1e-6
    assert max(out["second"]) - t0 < INTERVAL + 20
    assert out["asked4"]["repaint"] == [] and out["r4"] == 200 and out["jobs4"] == {}


def test_a_newer_publish_cancels_a_queued_job_and_its_marks():
    """Engine unit: a queued job's marks go with it when a newer job replaces it; the due time stays; a job that
    lists nothing for a scene drops its marks and lifts its hold."""
    from fake_compiler import make_world

    async def main():
        w = make_world()
        engine = w.engine
        engine._repaint_last["golden"] = engine.clock.monotonic()  # repainted just now: the gate is closed
        first = engine.start_repaint(
            [RepaintJob("golden", "custom-3", ("canopy-r0c0", "canopy-r0c1"), "Golden", 0.0)], generation=1
        )
        pending1 = engine.repaint_pending
        task1 = engine._repaint_task
        await engine.clock.sleep(10)
        second = engine.start_repaint(
            [RepaintJob("golden", "custom-3", ("canopy-r1c1",), "Golden", 0.0)], generation=1
        )
        await engine.clock.sleep(0)
        pending2 = engine.repaint_pending
        cancelled = task1.cancelled() or task1.done()
        third = engine.start_repaint([], generation=1)
        pending3 = engine.repaint_pending
        repainting = engine.repainting
        await w.close()
        return first, second, third, pending1, pending2, pending3, cancelled, repainting

    first, second, third, p1, p2, p3, cancelled, repainting = run_virtual(main)
    assert first[0].due_mono == pytest.approx(INTERVAL)  # never sooner than the gate
    assert p1 == {("canopy-r0c0", "custom-3"), ("canopy-r0c1", "custom-3")}
    assert cancelled and p2 == {("canopy-r1c1", "custom-3")}
    assert second[0].due_mono == pytest.approx(first[0].due_mono)  # the due time stays
    assert third == [] and p3 == frozenset() and not repainting


@pytest.mark.parametrize("jump", [3600.0, -3600.0])
def test_the_gate_survives_a_restart_and_a_wall_clock_step_never_stretches_it(no_threads, monkeypatch, jump):
    """A queued repaint (republished inside the gate) is persisted; the show restarts before it is due (no start-up
    commissioning: the show box), with the wall clock stepped ±1 h: the repaint is re-queued and runs within one
    interval of the restart — never sooner than the gate allows here, never later than one interval — and its
    records equal the live design."""
    from conductor.clock import VirtualClock
    from conductor.show import ShowWiring

    async def main():
        w = await show()
        async with client_of(w) as c:
            v1 = await save(c, {GOLDEN: golden_layout(45)})
            await publish(c, v1)
            await w.engine.wait_repaint()
            await w.run_for(30)
            v2 = await save(c, {GOLDEN: golden_layout(60)})
            r = await publish(c, v2)
        queued = dict(w.looks.file.queued)
        data = w.data
        w.owned = False
        await w.close()

        async def no_commission(self):
            return None

        monkeypatch.setattr(ShowWiring, "_sim_commission", no_commission)
        epoch = VirtualClock().epoch + jump
        monkeypatch.setattr(VirtualClock, "__init__", lambda self, epoch_=None: setattr(self, "epoch", epoch))
        restarted = None
        w2 = await show(data=data, quiet=False)
        writes = spy_writes(w2)
        restarted = w2.clock.monotonic()
        for _ in range(60):
            await w2.run_for(5)
            if slot_writes(writes) and not w2.engine.repainting:
                break
        await w2.engine.wait_repaint()
        slots = [t for t, _a, _r in slot_writes(writes)]
        records = {d: w2.services.registry.get(d).slot_record(SLOT)["hash"] for d in live_hashes(w2)}
        expected = live_hashes(w2)
        out = (r.status_code, queued, restarted, slots, records, expected, dict(w2.looks.file.queued))
        await w2.close()
        shutil.rmtree(data, ignore_errors=True)
        return out

    status, queued, restarted, slots, records, expected, queued_after = run_virtual(main)
    assert status == 200 and set(queued) == {GOLDEN}
    assert len(slots) == 9
    assert min(slots) - restarted <= INTERVAL + 10  # never stretched past one interval by the step
    if (
        jump < 0
    ):  # the wall clock went back: the persisted times look "in the future" — clamped to the interval
        assert min(slots) - restarted >= INTERVAL - 30
    assert records == expected and queued_after == {}


def test_after_a_restart_in_the_simulator_its_commissioning_settles_the_queue(no_threads):
    """In the simulator the start-up commissioning writes the live design itself: the queued repaint finds nothing
    left to repaint after it, leaves the overlay's queue, and writes nothing more."""

    async def main():
        w = await show()
        async with client_of(w) as c:
            v1 = await save(c, {GOLDEN: golden_layout(45)})
            await publish(c, v1)
            await w.engine.wait_repaint()
            v2 = await save(c, {GOLDEN: golden_layout(60)})
            await publish(c, v2)  # queued behind the gate
        queued = dict(w.looks.file.queued)
        data = w.data
        w.owned = False
        await w.close()
        w2 = await show(data=data)  # start-up commissioning (sim) included
        writes = spy_writes(w2)
        await w2.run_for(INTERVAL + 30)
        out = (
            queued,
            dict(w2.looks.file.queued),
            w2.engine.repainting,
            slot_writes(writes),
            {d: w2.services.registry.get(d).slot_record(SLOT)["hash"] for d in live_hashes(w2)},
            live_hashes(w2),
        )
        await w2.close()
        shutil.rmtree(data, ignore_errors=True)
        return out

    queued, queued_after, repainting, slots, records, expected = run_virtual(main)
    assert set(queued) == {GOLDEN}
    assert queued_after == {} and not repainting and slots == [] and records == expected


# ---- job bookkeeping ----------------------------------------------------------------------------------------------------
def test_a_refusal_at_job_start_clears_the_marks_and_keeps_the_hold(no_threads):
    """The job's scene comes due while Hubspace isn't answering: it reports not started (the reason), its marks are
    cleared (nothing pending), nothing is written, and the scene stays held at module level."""

    async def main():
        w = await show()
        async with client_of(w) as c:
            v1 = await save(c, {GOLDEN: golden_layout(45)})
            await publish(c, v1)
            await w.engine.wait_repaint()
            v2 = await save(c, {GOLDEN: golden_layout(60)})
            await publish(c, v2)  # queued behind the gate
        writes = spy_writes(w)
        w.services.engine_driver.faults.set_outage(True)  # Hubspace stops answering
        w.engine.enter_outage("a test outage")
        await w.engine.wait_repaint()
        out = (
            w.engine.repaint_pending,
            slot_writes(writes),
            GOLDEN in w.engine.t1_hold,
            [a.message for a in w.alerts() if "didn't start" in a.message],
            dict(w.looks.file.queued),
        )
        await w.close()
        return out

    pending, slots, held, alerts, queued = run_virtual(main)
    assert pending == frozenset() and slots == [] and held
    assert alerts and "Hubspace wasn't answering" in alerts[0]
    assert queued == {}


def test_two_painted_scenes_in_one_publish_are_one_job(no_threads):
    """Golden Hour and another painted scene changed in one version: one job, the scenes in sequence; `repainting`
    from the publish until the second scene's finally; nine slot writes each; both records current."""

    async def main():
        w = await show()
        writes = spy_writes(w)
        painted = [m for m, mode in w.looks.trio.modes.modes.items() if mode.slot is not None]
        other = next(m for m in painted if m != GOLDEN)
        other_slot = w.looks.trio.modes.modes[other].slot
        from live_lab import seed

        draft = seed(other)
        draft["layout"] = (
            {"kind": "linear", "angle_deg": 15}
            if draft["layout"] != {"kind": "linear", "angle_deg": 15}
            else {"kind": "linear", "angle_deg": 75}
        )
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour(), other: draft})
            first = (await ask(c, vid)).json()
            await publish(c, vid)
        flags = []
        while w.engine.repainting:
            flags.append((w.engine.repainting, w.engine.repaint_json()["scene"]))
            await w.run_for(0.5)
        golden = slot_writes(writes, SLOT)
        others = slot_writes(writes, other_slot)
        out = (first, flags, golden, others, w.engine.repaint_pending, other, w.engine.t1_hold)
        await w.close()
        return out

    first, flags, golden, others, pending, other, hold = run_virtual(main)
    assert {j["scene"] for j in first["repaint"]} == {GOLDEN, other}
    assert len(golden) == 9 and len(others) == 9
    g, o = [t for t, _a, _r in golden], [t for t, _a, _r in others]
    assert max(o) < min(g) or max(g) < min(o)  # in sequence, never interleaved
    assert {scene for _r, scene in flags} == {GOLDEN, other} and all(r for r, _s in flags)
    assert pending == frozenset() and GOLDEN not in hold and other not in hold


def test_commissioning_is_refused_while_a_repaint_job_runs(no_threads):
    async def main():
        w = await show()
        async with client_of(w) as c:
            v1 = await save(c, {GOLDEN: golden_layout(45)})
            await publish(c, v1)
            await w.engine.wait_repaint()
            v2 = await save(c, {GOLDEN: golden_layout(60)})
            await publish(c, v2)  # queued
        report = await w.engine.commission(source="ops")
        await w.engine.wait_repaint()
        later = await w.engine.commission(source="ops")
        await w.close()
        return report, later

    report, later = run_virtual(main)
    assert report["ok"] is False and report["message"].startswith("A publish is repainting custom-3 (about ")
    assert report["message"].endswith("s left); commission once it ends.") and report["retry_after_s"] > 60
    assert "repainting" not in later.get("message", "")


def test_a_publish_is_refused_only_while_a_repaint_writes(no_threads):
    """`looks_refusal` says "repainting" with the Lab's sentence only inside the quiet window; a queued repaint is
    never a refusal (the publish replaces it)."""

    async def main():
        w = await show()
        async with client_of(w) as c:
            v1 = await save(c, {GOLDEN: pinker_hour()})
            await publish(c, v1)
            await wait_quiet(w)
            v2 = await save(c, {TIDES: brighter(TIDES)})
            refused = await ask(c, v2)
            await w.engine.wait_repaint()
            ok = await publish(c, v2)
        await w.close()
        return refused.status_code, refused.json(), ok.status_code

    status, body, ok = run_virtual(main)
    assert status == 409 and body["detail"].startswith("Pinker Hour's slots are being repainted (about ")
    assert body["retry_after_s"] >= 1 and ok == 200


def test_a_confirm_shown_earlier_still_matches_while_its_wait_ticks_down(no_threads):
    """The confirm names the queued repaint's seconds; ops reads it for 30 s: the sentence they were shown still
    publishes (its number is an upper bound, still true); one that names a longer wait than one interval doesn't."""

    async def main():
        w = await show()
        async with client_of(w) as c:
            v1 = await save(c, {GOLDEN: golden_layout(45)})
            await publish(c, v1)
            await w.engine.wait_repaint()
            v2 = await save(c, {GOLDEN: golden_layout(60)})
            shown = (await ask(c, v2)).json()["confirm"]
            await w.run_for(30)
            forged = shown.replace(", in ", ", in 9").replace("9", "9", 1)
            bad = await ask(c, v2, confirm=forged)
            ok = await ask(c, v2, confirm=shown)
        await w.close()
        return shown, bad.status_code, bad.json(), ok.status_code, ok.text

    shown, bad, bad_body, ok, ok_text = run_virtual(main)
    assert ", in " in shown
    assert bad == 409 and bad_body.get("confirm_required")
    assert ok == 200, ok_text


# ---- what each guard holds (mutation targets) -----------------------------------------------------------------------
def test_a_module_whose_record_already_holds_the_design_is_not_written(no_threads):
    """`only`: the repaint writes the listed modules, never an eligible one whose record already holds the new
    design (here Module 1, recorded with the published design's hash before the publish)."""
    from live_lab import patched_trio

    async def main():
        w = await show()
        trio = patched_trio(pinker_hour())
        new_hash = (
            compile_mode(GOLDEN, Tier.T1, w.wiring.context(trio.modes, trio.recipes)).modules[DEV].slot_hash
        )
        entry = w.services.registry.get(DEV)
        w.services.update_registry(DEV, entry.slot_patch(SLOT, digest=new_hash, origin="api", effects=None))
        w.engine.slot_records.pop((DEV, SLOT), None)
        writes = spy_writes(w)
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            first = (await ask(c, vid)).json()
            await publish(c, vid)
        await w.engine.wait_repaint()
        out = first, slot_writes(writes)
        await w.close()
        return out

    first, slots = run_virtual(main)
    assert first["repaint"][0]["writes"] == 8 and "Module 1" not in first["repaint"][0]["modules"]
    assert len(slots) == 8 and f"sim-{DEV}" not in {a for _t, a, _r in slots}


def test_a_reconciler_rewrite_on_the_same_module_finishes_before_the_repaint_writes_it(no_threads):
    """The `_slot_lock`: a reconciler slot rewrite of another slot still running on Module 1 when the quiet window
    opens holds the module's lock (the window waits for show bursts, not for it), so the repaint's write there waits
    for it — neither is SUPERSEDED, both land."""
    import asyncio

    async def main():
        w = await show()
        await w.run_for(600)  # the start-up commissioning's 600 s limit is over
        writes = spy_writes(w)
        driver = w.services.engine_driver
        original = driver.write
        slow = {"on": True}

        async def write(address, rows, **kw):
            if (
                slow["on"]
                and address == f"sim-{DEV}"
                and any(r.key == (R.FRAME_CLASS, "custom-1") for r in rows)
            ):
                await w.clock.sleep(4.0)  # a slow PUT: the rewrite still holds the lock when the window opens
            return await original(address, rows, **kw)

        driver.write = write
        async with client_of(w) as c:
            vid = await save(c, {GOLDEN: pinker_hour()})
            rewrite = asyncio.get_running_loop().create_task(
                w.engine.rewrite_slot(DEV, "custom-1", source="reconciler")
            )
            await w.run_for(1.0)  # its PUT is on its way (slowly): it holds Module 1's slot lock
            await publish(c, vid)
            await w.engine.wait_repaint()
            rewritten = await rewrite
        results = [
            (e.get("label"), e.get("status"))
            for e in w.services.telemetry.ring
            if e["kind"] == "command"
            and e.get("device_id") == f"sim-{DEV}"
            and "→ custom-" in str(e.get("label"))
        ]
        status = {m["id"]: m["status"] for m in w.engine.repaint_json()["modules"]}
        out = rewritten, results, status, slot_writes(writes, "custom-1"), slot_writes(writes)
        await w.close()
        return out

    rewritten, results, status, ones, threes = run_virtual(main)
    assert rewritten == str(CommandStatus.APPLIED) and status[DEV] == "applied"
    assert str(CommandStatus.SUPERSEDED) not in [s for _l, s in results], results
    one = [t for t, a, _r in ones if a == f"sim-{DEV}"]
    three = [t for t, a, _r in threes if a == f"sim-{DEV}"]
    assert one and three and min(three) > max(one)  # the repaint's write waited for the rewrite


def test_a_newer_install_without_a_job_aborts_a_queued_one():
    """The generation check: a queued scene whose live looks were replaced (a rollback's install, with no new job)
    is abandoned when it comes due — nothing is written and its marks are gone."""
    from fake_compiler import make_world

    async def main():
        w = make_world()
        engine = w.engine
        generation = {"n": 1}
        engine.deps.looks_generation = lambda: generation["n"]
        plan = engine.deps.compile("golden", Tier.T1)
        engine.deps.repaint_target = lambda _mode: type(
            "T", (), {"listed": list(plan.modules), "plan": plan}
        )()
        engine._repaint_last["golden"] = engine.clock.monotonic()
        engine.start_repaint(
            [RepaintJob("golden", "custom-3", tuple(plan.modules), "Golden", 0.0)], generation=1
        )
        sent: list[str] = []
        original = w.sim.write

        async def write(address, rows, **kw):
            sent.append(address)
            return await original(address, rows, **kw)

        w.sim.write = write
        generation["n"] = 2  # installed again, no new job
        await engine.wait_repaint()
        out = sent, engine.repaint_pending, engine.repaint_json()
        await w.close()
        return out

    sent, pending, status = run_virtual(main)
    assert sent == [] and pending == frozenset()
    assert status is not None and status["running"] is False and status["started_at"] is None
