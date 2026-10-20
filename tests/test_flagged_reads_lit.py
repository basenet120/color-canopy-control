"""A module whose only problem is a flagged note still shows its look on the guest panel's picture (D78).

Since D73 a module needing attention only for a flagged note (D54's "no proven map yet"), with its look sent, read
back and matching, is "stuck" at once, and `canopy_view.lit_state` drew every stuck module as `unknown` (an outline,
no colour). Live, the iPad therefore drew Modules 1–2 as outlines right after every confirmed scene (before D73, after
10 s), although their lights showed the scene.

Proves: the engine's module record carries `flagged_only` (`tracker.flagged_only`); a confirmed flagged-only module
is `lit` with exactly the colours an unflagged one shows (and `off` once a guest's power-off reads back off), through
the engine and through GET /api/kiosk/panel; a module stuck on a readback that really differs (a silent drop), with or
without the note, a locked one and a resting canopy still make no colour claim.
"""

from __future__ import annotations

import time
from dataclasses import replace

import pytest
from fake_compiler import MODULES, make_world, settings_with
from partial_canopy import LIVE, partial_show, showing
from test_canopy_view import CTX, REGISTRY, _by_pos, _state, _with

from conductor import render
from conductor.canopy_view import lit_state, load_canopy
from conductor.clock import run_virtual
from conductor.config import CONFIG_DIR
from conductor.engine_api import CanopyPhase, ModePlan, Tier
from conductor.gate import Purpose
from conductor.looks import compile_mode

CFG = load_canopy(str(CONFIG_DIR))
QUIET = {"idle_return_s": 100_000, "idle_reset_s": 100_000}
NOTE = "the controller reports 60 entries for 30 heads and this kit has no proven map yet (D54): test"
NO_CLAIM = {"state": "unknown", "color": None, "colors": None}
# the engine's record for a confirmed module whose only problem is a flagged note (D73 + D78)
FLAGGED = {"stuck": True, "status": "needs_attention", "flagged_only": True}


def _with_colours(plan: ModePlan) -> ModePlan:
    """The fake compiler leaves `colors` empty; the real one fills each module's head colours."""
    modules = {d: replace(t, colors=render.head_colors(t.rows, 30)) for d, t in plan.modules.items()}
    return replace(plan, modules=modules)


def _picture(*, flag: bool, drop_rows: bool = False, lock: str | None = None, until=None):
    """(seconds, the engine's state, the guest picture) into a change to a T3 look on nine modules: 5 s in, or the
    first moment `until(state)` holds (None when it never did within 40 s)."""

    async def main():
        w = make_world(
            settings=settings_with(kiosk=QUIET), tier_allowed=lambda m, t: (t is not Tier.T1, "Gate C (test)")
        )
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.run_for(15)
        if flag:
            for d in MODULES:
                w.tracker.flag_problem(w.address(d), NOTE)
        if drop_rows:  # the cloud silently drops every write from here on: the readback really differs
            w.sim.faults.set_silent_drop_rate(1.0)
        if lock is not None:
            w.locks.acquire(w.address(lock), "module page · test")
        await w.engine.request_mode("golden", source="kiosk")
        if until is None:
            await w.run_for(5)
            seconds = 5.0
        else:
            seconds = await w.until(lambda: until(w.engine.state()), timeout=40)
        state = w.engine.state()  # the same instant (nothing awaited since)
        plan = w.engine.current_plan()
        w.sim.faults.set_silent_drop_rate(0.0)
        await w.close()
        assert plan is not None and plan.mode_id == "golden" and plan.tier is Tier.T3
        return seconds, state, lit_state(CFG, w.registry, _with_colours(plan), state)

    return run_virtual(main)


def _stuck(state) -> list[str]:
    return [d for d, r in state.modules.items() if r["stuck"]]


# ---- through the engine (virtual time) -------------------------------------------------------------------------
def test_a_confirmed_flagged_only_module_is_lit_with_its_colours():
    _, _, plain = _picture(flag=False)
    _, state, flagged = _picture(flag=True)
    assert {m["state"] for m in plain["modules"]} == {"lit"}
    assert all(m["color"] or m["colors"] for m in plain["modules"])
    # was nine outlines ("unknown", no colour) the moment the look was confirmed (D73 made the module stuck at once)
    assert flagged["modules"] == plain["modules"], flagged["modules"]
    for d in MODULES:
        record = state.modules[d]
        # /ops still shows the note (D73: stuck at once, the phase needs attention); the picture shows the look
        assert record["stuck"] and record["flagged_only"] is True, record
        assert record["status"] == "needs_attention" and NOTE in record["reason"], record
    assert state.look_phase is CanopyPhase.NEEDS_ATTENTION


@pytest.mark.parametrize("flag", [False, True], ids=["no note", "with the note"])
def test_a_module_stuck_on_a_readback_that_really_differs_still_makes_no_colour_claim(flag):
    # the reconciler re-sends a stuck module at once, so the picture is taken the moment one is stuck
    seconds, state, lit = _picture(flag=flag, drop_rows=True, until=_stuck)
    # past the 10 s retry window: never stuck at once, as a flagged-only module is (D73)
    assert seconds is not None and seconds > 9.5, seconds
    by_pos = _by_pos(lit)
    for d in _stuck(state):
        record = state.modules[d]
        assert not record.get("flagged_only"), record
        assert by_pos[record["module"]] == {"pos": record["module"], **NO_CLAIM}, by_pos[record["module"]]


def test_a_locked_flagged_module_still_makes_no_colour_claim():
    _, state, lit = _picture(flag=True, lock="canopy-r1c1")
    assert state.modules["canopy-r1c1"]["locked"] and not state.modules["canopy-r1c1"].get("flagged_only")
    assert _by_pos(lit)["r1c1"] == {"pos": "r1c1", **NO_CLAIM}


# ---- the truth table (canopy_view.lit_state over a compiled plan) ------------------------------------------------
def test_the_picture_reads_a_flagged_only_record_as_its_readback():
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    ready = lit_state(CFG, REGISTRY, plan, _state(plan))
    flagged = lit_state(CFG, REGISTRY, plan, _with(_state(plan), "canopy-r1c1", **FLAGGED))
    assert flagged == ready  # lit, with the colours a ready module shows
    # the guest turned the canopy off: the power-off was sent and reads back off, so the flagged panel says off
    off = lit_state(CFG, REGISTRY, plan, _with(_state(plan, guest_power=False), "canopy-r1c1", **FLAGGED))
    assert {m["state"] for m in off["modules"]} == {"off"}


@pytest.mark.parametrize(
    "record",
    [
        {"locked": True, "status": "locked", "flagged_only": True},
        {"stuck": True, "status": "needs_attention", "flagged_only": False},
        {"stuck": True, "status": "failed"},
    ],
    ids=["locked", "stuck, the readback differs", "stuck, the write failed"],
)
def test_a_locked_or_really_stuck_record_makes_no_claim_whatever_the_note(record):
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    for kw in ({}, {"guest_power": False}):
        lit = _by_pos(lit_state(CFG, REGISTRY, plan, _with(_state(plan, **kw), "canopy-r1c1", **record)))
        assert lit["r1c1"] == {"pos": "r1c1", **NO_CLAIM}, (kw, lit["r1c1"])


def test_a_resting_canopy_makes_no_claim_on_a_flagged_only_module():
    plan = compile_mode("coastal-tides", Tier.T3, CTX)
    state = _with(_state(plan, phase=CanopyPhase.RESTING), "canopy-r1c1", **FLAGGED)
    assert {m["state"] for m in lit_state(CFG, REGISTRY, plan, state)["modules"]} == {"unknown"}


# ---- the live setup, through the guest panel ------------------------------------------------------------------
def test_the_guest_panel_draws_flagged_kits_lit_after_a_confirmed_scene(monkeypatch, tmp_path):
    """Two kits registered, both carrying the D54 note; a guest taps a scene. Once the kits confirm it, the iPad's
    picture shows them lit in the scene's colours (it drew two outlines)."""
    live = {d.removeprefix("canopy-") for d in LIVE}
    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        engine = showing(c)
        services = c.app.state.services
        for device_id in LIVE:
            services.tracker.flag_problem(services.target(device_id, Purpose.ENGINE).address, NOTE)
        panel = c.get("/api/kiosk/panel").json()
        scene = next(s["id"] for s in panel["scenes"] if s["id"] != panel["state"]["scene"])
        assert c.post("/api/kiosk/scene", json={"scene": scene}).status_code == 200
        t0, picture = time.monotonic(), None
        while time.monotonic() - t0 < 10:
            state = c.get("/api/kiosk/panel").json()["state"]
            picture = _by_pos(state["lit"]) if state["lit"] else None
            if state["scene"] == scene and picture and all(picture[p]["state"] != "changing" for p in live):
                break
            time.sleep(0.1)
        records = {d: engine.state().modules[d] for d in LIVE}
    assert picture is not None
    for pos in live:
        m = picture[pos]
        assert m["state"] == "lit" and (m["color"] or m["colors"]), m
    assert all(m["state"] == "dark" for p, m in picture.items() if p not in live)
    assert all(r["stuck"] for r in records.values()), records  # /ops still shows the note
