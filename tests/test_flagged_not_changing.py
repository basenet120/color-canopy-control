"""A module whose only problem is a flagged note isn't "changing" (found live on Modules 1–2, Oct 18).

The show flags a capability note on a kit with no proven controller map (D54: "the controller reports 60 entries for
30 heads … per-head frames (T1) are refused; whole-strand looks … still work"). The engine used to count such a
module as "changing" for `reconciler.downgrade_after_wrong_s` (10 s) after every new look — the retry window meant
for a readback that still differs — although no retry can clear a flag and its lights already show the look. The
guest panel then said "One moment, the lights are changing" for ~10 s after each scene, whatever the cooldown.

Proves: once a flagged module's look is confirmed it reads needs attention at once (never "changing"), so the guest
panel takes the next scene after the cooldown alone; a module whose readback really differs still gets its 10 s of
"changing" while the reconciler retries.
"""

from __future__ import annotations

import time

from fake_compiler import MODULES, make_world, settings_with
from partial_canopy import LIVE, partial_show, showing

from conductor.clock import run_virtual
from conductor.engine_api import CanopyPhase
from conductor.gate import Purpose

QUIET = {"idle_return_s": 100_000, "idle_reset_s": 100_000}
NOTE = "the controller reports 60 entries for 30 heads and this kit has no proven map yet (D54): test"


def _phases_after_a_change(flag: bool, drop_rows: bool) -> list[tuple[float, CanopyPhase]]:
    async def main():
        w = make_world(settings=settings_with(kiosk=QUIET))
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.run_for(15)
        if flag:
            for d in MODULES:
                w.tracker.flag_problem(w.address(d), NOTE)
        if drop_rows:  # the cloud silently drops the next writes to one module: its readback really differs
            w.sim.faults.set_silent_drop_rate(1.0)
        await w.engine.request_mode("holiday", source="kiosk")
        seen, start = [], w.clock.monotonic()
        while w.clock.monotonic() - start < 14:
            phase = w.engine.state().look_phase
            if not seen or seen[-1][1] is not phase:
                seen.append((round(w.clock.monotonic() - start, 1), phase))
            await w.run_for(0.1)
        w.sim.faults.set_silent_drop_rate(0.0)
        await w.close()
        return seen

    return run_virtual(main)


def test_a_flagged_module_whose_look_is_confirmed_is_never_held_as_changing():
    seen = _phases_after_a_change(flag=True, drop_rows=False)
    updating_until = max((t for t, p in seen if p is CanopyPhase.UPDATING), default=0.0)
    settled = next(t for t, p in seen if p is CanopyPhase.NEEDS_ATTENTION)
    assert updating_until < 3.0 and settled < 3.0, seen  # was ~11 s: the 10 s retry window after confirmation
    assert seen[-1][1] is CanopyPhase.NEEDS_ATTENTION  # the note still shows on /ops


def test_a_readback_that_really_differs_still_gets_its_retry_window():
    seen = _phases_after_a_change(flag=False, drop_rows=True)
    assert seen[0][1] is CanopyPhase.UPDATING  # the reconciler is still re-sending: honestly "changing"
    assert all(p is not CanopyPhase.NEEDS_ATTENTION for t, p in seen if t < 9.5), seen  # never settled early


def test_the_guest_panel_takes_the_next_scene_after_the_cooldown_on_flagged_kits(monkeypatch, tmp_path):
    """The live setup: two kits registered, both carrying the D54 note; a guest taps a scene, then keeps tapping
    another — it goes through about 2 s later, not ~12 s."""
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        engine = showing(c)
        services = c.app.state.services
        for device_id in LIVE:
            services.tracker.flag_problem(services.target(device_id, Purpose.ENGINE).address, NOTE)
        time.sleep(1)
        scenes = [s["id"] for s in c.get("/api/kiosk/panel").json()["scenes"]]
        current = c.get("/api/kiosk/panel").json()["state"]["scene"]
        a, b = [s for s in scenes if s != current][:2]
        t0 = time.monotonic()
        assert c.post("/api/kiosk/scene", json={"scene": a}).status_code == 200
        accepted = None
        while time.monotonic() - t0 < 15:
            state = c.get("/api/kiosk/panel").json()["state"]
            if (
                state["phase"] != "changing"
                and c.post("/api/kiosk/scene", json={"scene": b}).status_code == 200
            ):
                accepted = time.monotonic() - t0
                break
            time.sleep(0.1)
        assert engine is not None
    assert accepted is not None and accepted < 4.0, accepted


def test_flagged_kits_read_unknown_in_the_picture_after_a_confirmed_scene(monkeypatch, tmp_path):
    """What the live iPad picture shows for Modules 1–2 since D73: once a scene is confirmed on the two flagged kits
    they read stuck, so lit_state calls them unknown (outline dots and, from D74, dashed fins), while the seven
    positions with no device are dark. The fins add nothing to state.lit; the kiosk mixes them from these states."""
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        engine = showing(c)
        services = c.app.state.services
        for device_id in LIVE:
            services.tracker.flag_problem(services.target(device_id, Purpose.ENGINE).address, NOTE)
        time.sleep(1)
        body = c.get("/api/kiosk/panel").json()
        current = body["state"]["scene"]
        scene = next(s["id"] for s in body["scenes"] if s["id"] != current)
        assert c.post("/api/kiosk/scene", json={"scene": scene}).status_code == 200
        states, t0 = None, time.monotonic()
        while time.monotonic() - t0 < 15:
            body = c.get("/api/kiosk/panel").json()
            lit = body["state"]["lit"]
            if body["state"]["phase"] != "changing" and body["state"]["scene"] == scene and lit is not None:
                states = {m["pos"]: m["state"] for m in lit["modules"]}
                if states["r0c0"] == states["r0c1"] == "unknown":
                    break
            time.sleep(0.1)
        assert engine is not None
        assert len(body["canopy"]["fins"]) == 650
    assert states is not None
    assert {pos: st for pos, st in states.items() if pos in ("r0c0", "r0c1")} == {
        "r0c0": "unknown",
        "r0c1": "unknown",
    }
    assert {st for pos, st in states.items() if pos not in ("r0c0", "r0c1")} == {"dark"}, states
