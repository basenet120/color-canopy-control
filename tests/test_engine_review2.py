"""Regression tests for the second adversarial review of the canopy engine (findings F1–F11), on virtual time.

Each test reproduces the review's scenario: capabilities read only once at boot (F1), one unread module blocking
every look (F2), 503/504 outages downgrading tiers (F3), T1 falling to "hand-painted" T2 copy (F4), T1 judged against
a freshly compiled frame (F5), motion copy without the motion code on the device (F6), no way to record a
hand-painted slot (F7), 10+ minute upgrades (F8), Mode.motion ignored at T2 (F9), Calm claiming 2700 K when clamped
(F10), and a soak that doesn't run the show's wiring (F11)."""

from __future__ import annotations

import dataclasses
import time

from fake_compiler import FakeCompiler, make_world, settings_with
from live_path import refuse_stepped
from technique_modes import TECHNIQUE_MODES as TM  # the pre-D59 recipes, a test fixture

from conductor import rows as R
from conductor.capabilities import detect
from conductor.clock import VirtualClock, run_virtual
from conductor.config import DeviceFlags, Registry, load_registry
from conductor.drivers import sim as simmod
from conductor.drivers.base import Outcome
from conductor.engine_api import CanopyPhase, PlanError, Tier
from conductor.gate import Purpose
from conductor.looks import CompileContext, compile_first, compile_mode, load_modes
from conductor.looks.compile import _counted, _generic_feature
from conductor.looks.schema import MotionEffect
from conductor.services import build_services
from conductor.show import ShowWiring
from conductor.sim.profiles import profile_parts

QUIET = settings_with(kiosk={"idle_return_s": 100_000})
MODES = load_modes()
REGISTRY = load_registry()
CANOPY = REGISTRY.canopy_modules()
FUNCTIONS, STATES = profile_parts("sqhdk03-sim")


# ---- helpers -------------------------------------------------------------------------------------------------------
async def _services(tmp_path):
    services = build_services(
        {"CONDUCTOR_DATA_DIR": str(tmp_path / "data"), "CONDUCTOR_SIM_SPEED": "fast"}, clock=VirtualClock()
    )
    await services.start()
    return services


async def _until(clock, predicate, timeout: float, step: float = 0.5) -> float | None:
    start = clock.monotonic()
    while clock.monotonic() - start <= timeout:
        if predicate():
            return clock.monotonic() - start
        await clock.sleep(step)
    return None


async def _boot(tmp_path, *, before_start=None):
    """The app's show wiring on the fast simulator, commissioned at startup, Lowcountry Calm ready."""
    services = await _services(tmp_path)
    if before_start is not None:
        before_start(services)
    wiring = ShowWiring(services)
    services.extras["show"] = wiring
    engine = await wiring.start()
    return services, wiring, engine


async def _settled(services, wiring, engine, timeout: float = 120.0) -> None:
    ok = await _until(
        services.clock,
        lambda: wiring.last_commission is not None and engine.state().phase is CanopyPhase.READY,
        timeout,
    )
    assert ok is not None, engine.state()


async def _hold(services, engine, seconds: float, step: float = 5.0) -> None:
    """Let time pass as if ops were holding the current mode (keeps the idle return out of the way)."""
    end = services.clock.monotonic() + seconds
    while services.clock.monotonic() < end:
        engine.last_change_mono = services.clock.monotonic()
        await services.clock.sleep(step)


def _device_slot(services, device_id: str, slot: str):
    view = {r.key: r.value for r in services.sim.device_view(f"sim-{device_id}")}
    return view.get((R.FRAME_CLASS, slot))


def _all_correct(services, plan) -> bool:
    return all(
        not R.compare(mt.expect or mt.rows, services.sim.device_view(f"sim-{d}"))
        for d, mt in plan.modules.items()
    )


def _app_paint(services, device_id: str, slot: str, color: tuple[int, int, int], effects=None) -> str:
    """Someone paints `slot` in the Hubspace app: the cloud stores a new pattern (a normal PUT from the app)."""
    value = R.frame_value(None, [R.FrameEntry(*color, 0, 100, 0)] * 30, expected_length=30, effects=effects)
    stamp = int(services.clock.now() * 1000)
    row = R.slot_write(slot, value)
    response = services.sim.cloud.accept_wire(
        {"metadeviceId": f"sim-{device_id}", "values": [row.to_wire(stamp)]}
    )
    assert response.ok, response.error
    return R.slot_hash(value)


def _caps(**changes):
    caps = detect(FUNCTIONS, STATES)
    return dataclasses.replace(caps, **changes) if changes else caps


# ---- F1 ------------------------------------------------------------------------------------------------------------
def test_f1_profiles_that_fail_at_boot_are_read_again_and_the_canopy_gets_a_look(tmp_path):
    """The live box boots before the Afero link connects: every profile read fails for the first 20 s."""

    async def main():
        services = await _services(tmp_path)
        clock = services.clock
        real = services.sim.fetch_profile
        calls: list[float] = []

        async def fetch_profile(device_id, *, purpose=Purpose.ENGINE):
            calls.append(clock.monotonic())
            if clock.monotonic() < 20:
                raise RuntimeError("Not connected to Hubspace for this account yet.")
            return await real(device_id, purpose=purpose)

        services.sim.fetch_profile = fetch_profile
        wiring = ShowWiring(services)
        services.extras["show"] = wiring
        t0 = clock.monotonic()
        engine = await wiring.start()
        start_took = clock.monotonic() - t0
        await clock.sleep(5)
        early = {"caps": len(wiring.caps), "attention": wiring.attention()}
        ready = await _until(
            clock,
            lambda: (
                len(wiring.caps) == 9
                and engine.state().phase is CanopyPhase.READY
                and wiring.last_commission is not None
            ),
            120,
        )
        state = engine.state()
        ops = await engine.request_mode("rainbow-row-sunset", source="ops")
        await wiring.close()
        await services.close()
        return start_took, early, ready, state, ops, len(calls)

    start_took, early, ready, state, ops, calls = run_virtual(main)
    assert start_took < 0.5  # startup never waits on Afero (07 §2)
    assert early["caps"] == 0 and len(early["attention"]) == 9
    assert "profile hasn't been read" in early["attention"][0]
    assert ready is not None and ready < 60, "capabilities were never read again"
    assert state.mode == "lowcountry-calm" and state.phase is CanopyPhase.READY
    assert not ops.refused and ops.tier == "canvas"  # commissioned once the profiles arrived
    assert calls > 9  # retried with backoff


def test_f1_recovery_ops_refresh_and_a_module_back_online_read_profiles_again(tmp_path):
    async def main():
        box = {"down": True}
        reads: list[tuple[float, str]] = []

        def prepare(services):
            services.sim.on_recovery = []  # the driver hook the Afero adapter's watchdog fires
            real = services.sim.fetch_profile

            async def fetch_profile(device_id, *, purpose=Purpose.ENGINE):
                reads.append((services.clock.monotonic(), device_id))
                if box["down"] and device_id == "sim-canopy-r2c2":
                    raise RuntimeError("Hubspace answered 503 for this profile")
                return await real(device_id, purpose=purpose)

            services.sim.fetch_profile = fetch_profile

        services, wiring, engine = await _boot(tmp_path, before_start=prepare)
        clock = services.clock
        await clock.sleep(200)  # backoff has grown to its 60 s ceiling
        box["down"] = False
        for callback in list(services.sim.on_recovery):
            callback("canopy")
        hooked = await _until(clock, lambda: "canopy-r2c2" in wiring.caps, 5, step=0.1)
        # ops "Refresh" re-reads on demand
        before = sum(1 for _, d in reads if d == "sim-canopy-r0c0")
        wiring.request_refresh(["canopy-r0c0"])
        await clock.sleep(2)
        on_demand = sum(1 for _, d in reads if d == "sim-canopy-r0c0") - before
        # a module that comes back online is read again (it may have been power-cycled)
        before = sum(1 for _, d in reads if d == "sim-canopy-r1c0")
        services.sim.faults.set_available("sim-canopy-r1c0", False)
        await _hold(services, engine, 70)
        services.sim.faults.set_available("sim-canopy-r1c0", True)
        await _hold(services, engine, 70)
        online = sum(1 for _, d in reads if d == "sim-canopy-r1c0") - before
        await wiring.close()
        await services.close()
        return hooked, on_demand, online

    hooked, on_demand, online = run_virtual(main)
    assert hooked is not None, "the driver's on_recovery hook didn't trigger a profile read"
    assert on_demand == 1
    assert online >= 1


# ---- F2 ------------------------------------------------------------------------------------------------------------
def test_f2_one_unread_module_leaves_the_other_eight_working(tmp_path):
    async def main():
        def prepare(services):
            real = services.sim.fetch_profile

            async def fetch_profile(device_id, *, purpose=Purpose.ENGINE):
                if device_id == "sim-canopy-r1c1":
                    raise RuntimeError("the controller didn't answer")
                return await real(device_id, purpose=purpose)

            services.sim.fetch_profile = fetch_profile

        services, wiring, engine = await _boot(tmp_path, before_start=prepare)
        # the subject is the unread module, not motion: Sunset's stepped rung (D77) refused as live hardware does
        refuse_stepped(engine, "rainbow-row-sunset")
        clock = services.clock
        calm = await _until(clock, lambda: engine.current_plan() is not None, 30)
        calm_plan = engine.current_plan()
        safe = await engine.safe_look(source="ops")
        await clock.sleep(10)
        state = await engine.request_mode("rainbow-row-sunset", source="ops")
        await _hold(services, engine, 20)
        plan = engine.current_plan()
        final = engine.state()
        others_correct = all(
            not R.compare(mt.expect or mt.rows, services.sim.device_view(f"sim-{d}"))
            for d, mt in plan.modules.items()
        )
        attention = wiring.attention()
        await wiring.close()
        await services.close()
        return calm, calm_plan, safe, state, plan, final, others_correct, attention

    calm, calm_plan, safe, state, plan, final, others_correct, attention = run_virtual(main)
    assert calm is not None and len(calm_plan.modules) == 9  # the safe look assumes conservative capabilities
    assert not safe.refused
    assert not state.refused and state.mode == "rainbow-row-sunset"
    assert "canopy-r1c1" not in plan.modules and "canopy-r1c1" in plan.skipped and len(plan.modules) == 8
    assert others_correct
    assert final.phase is CanopyPhase.NEEDS_ATTENTION
    assert final.modules["canopy-r1c1"]["status"] == "needs_attention"
    assert "profile hasn't been read" in final.modules["canopy-r1c1"]["reason"]
    assert len(attention) == 1 and "the controller didn't answer" in attention[0]


def test_f2_partial_plans_count_only_the_modules_they_drive():
    caps = {m.id: _caps() for m in CANOPY if m.id != "canopy-r1c1"}
    ctx = CompileContext(registry=REGISTRY, capabilities=caps)
    canvas = compile_mode("rainbow-row-sunset", Tier.T1, ctx)
    assert sorted(canvas.skipped) == ["canopy-r1c1"] and len(canvas.modules) == 8
    assert "240 lights" in canvas.feature and "eight strands" in canvas.feature
    assert "270" not in canvas.feature and "nine" not in canvas.feature
    calm = compile_mode("lowcountry-calm", Tier.STRAND, ctx)
    assert len(calm.modules) == 9 and not calm.skipped
    try:
        compile_mode("goodnight-glow", Tier.STRAND, CompileContext(registry=REGISTRY, capabilities={}))
    except PlanError as e:
        assert "hasn't been read yet" in str(e)
    else:  # pragma: no cover
        raise AssertionError("a look other than the safe one can't be planned without any profile")


# ---- F2 / D62: a partial canopy (only some positions have a Hubspace device registered) ----------------------------
LIVE = ("canopy-r0c0", "canopy-r0c1")  # the two shop kits registered at canopy positions on the Mac
OTHERS = sorted(m.id for m in CANOPY if m.id not in LIVE)
PARTIAL_T3 = "Colour and dimming: two Hubspace strands, each set to a single colour from the scene."


def _partial_ctx(*, caps_on=LIVE, addressed=LIVE, flags=True, profile="sqhdk03-sim-60"):
    """The show's compile context with only `addressed` registered (live: no bench flags proven on any module)."""
    caps = detect(*profile_parts(profile))
    return CompileContext(
        registry=REGISTRY,
        capabilities={i: caps for i in caps_on},
        flags={m.id: DeviceFlags() for m in CANOPY} if flags else None,
        addresses={m.id: (f"md-{m.id}" if m.id in addressed else None) for m in CANOPY},
    )


def _unregister(monkeypatch, ids) -> set[str]:
    """Registry.address answers None for these ids (what live has before a kit is registered there); the set it
    returns can be edited to register one mid-run."""
    gone = set(ids)
    real = Registry.address

    def address(self, device_id, driver_name):
        return None if device_id in gone else real(self, device_id, driver_name)

    monkeypatch.setattr(Registry, "address", address)
    return gone


def _record_writes(services) -> list[str]:
    writes: list[str] = []
    real = services.sim._exchange

    async def exchange(device_id, *, write):
        if write:
            writes.append(device_id)
        return await real(device_id, write=write)

    services.sim._exchange = exchange
    return writes


def test_d62_a_partial_canopy_compiles_every_mode_at_module_level_on_the_registered_modules_only():
    ctx = _partial_ctx()
    for mode_id, mode in MODES.modes.items():
        # live hardware refuses the stepped rung in the show's tier_allowed until Gate R (D65; the compiler doesn't
        # gate it, tests/test_d65_modes.py), so the ladder lands on module level
        plan = compile_first(mode_id, ctx, skip=[Tier.STEPPED])
        assert plan.tier is Tier.T3, (mode_id, plan.notes)
        if (
            "stepped" in mode.ladder
        ):  # where it runs (sim, or after Gate R) it holds the same partial sentence
            stepped = compile_mode(mode_id, Tier.STEPPED, ctx)
            assert stepped.feature == PARTIAL_T3 and sorted(stepped.modules) == list(LIVE)
            assert stepped.program.moving_feature.startswith(
                "Colour and dimming through the Hubspace cloud: both Hubspace strands"
            )
        assert sorted(plan.modules) == list(LIVE)
        assert sorted(plan.skipped) == OTHERS
        assert all("no Hubspace device registered" in why for why in plan.skipped.values())
        assert "/ops/accounts" in plan.skipped[OTHERS[0]]
        assert plan.feature == PARTIAL_T3
        assert "column by column" not in plan.feature and "nine" not in plan.feature
        # each strand's colour is the gradient sampled at its column, usually not one of the palette's colours
        assert "palette" not in plan.feature
        assert any(n.startswith("Left out (no Hubspace device registered)") for n in plan.notes)
        assert not any("capabilities yet" in n for n in plan.notes)
        if "canvas" in mode.ladder:
            for tier, gate in ((Tier.T1, "Gate C"), (Tier.T2, "Gate B")):
                try:
                    compile_mode(mode_id, tier, ctx)
                except PlanError as e:
                    assert gate in str(e), e
                else:  # pragma: no cover
                    raise AssertionError(f"{mode_id} compiled at {tier} without its bench gate")
    calm = compile_mode("lowcountry-calm", Tier.STRAND, ctx)
    assert sorted(calm.modules) == list(LIVE) and sorted(calm.skipped) == OTHERS
    assert calm.feature == "Colour and dimming: both Hubspace strands set to soft ivory."
    assert not any("assumes" in n for n in calm.notes), (
        "the safe look assumes nothing for an unregistered module"
    )


def test_d62_keeps_d36_for_registered_modules_whose_profile_hasnt_been_read():
    ctx = _partial_ctx(addressed=[m.id for m in CANOPY])
    calm = compile_mode("lowcountry-calm", Tier.STRAND, ctx)
    assert len(calm.modules) == 9 and not calm.skipped and any("assumes" in n for n in calm.notes)
    assert calm.feature == "Colour and dimming: all nine Hubspace strands set to soft ivory."
    tides = compile_mode("coastal-tides", Tier.T3, ctx)
    assert sorted(tides.modules) == list(LIVE) and sorted(tides.skipped) == OTHERS
    assert all("profile hasn't been read" in why for why in tides.skipped.values())
    assert any(n.startswith("Left out (no capabilities yet)") for n in tides.notes)
    assert tides.feature == PARTIAL_T3


def test_d62_the_no_profile_refusal_names_only_the_registered_modules():
    try:
        compile_mode("coastal-tides", Tier.T3, _partial_ctx(caps_on=()))  # two registered, neither read yet
    except PlanError as e:
        message = str(e)
    else:  # pragma: no cover
        raise AssertionError("coastal-tides compiled with no profile read")
    assert all(d in message for d in LIVE), message
    assert not any(d in message for d in OTHERS), "an unregistered position is not 'not read yet'"


def test_d62_with_nothing_registered_no_look_can_be_planned_and_stale_capabilities_are_never_used():
    every = [m.id for m in CANOPY]
    ctx = _partial_ctx(caps_on=every, addressed=())  # capabilities read before the registration went away
    for mode_id, mode in MODES.modes.items():
        for tier in mode.ladder:
            try:
                compile_mode(mode_id, tier, ctx)
            except PlanError as e:
                assert "register one on /ops/accounts" in str(e), (mode_id, tier, e)
            else:  # pragma: no cover
                raise AssertionError(f"{mode_id} compiled at {tier} with no module registered")
    one = _partial_ctx(caps_on=every, addressed=["canopy-r0c0"], flags=False, profile="sqhdk03-sim")
    calm = compile_mode("lowcountry-calm", Tier.STRAND, one)
    assert list(calm.modules) == ["canopy-r0c0"] and len(calm.skipped) == 8
    assert calm.feature == "Colour and dimming: one Hubspace strand set to soft ivory."
    canvas = compile_mode("rainbow-row-sunset", Tier.T1, one)
    assert list(canvas.modules) == ["canopy-r0c0"]
    assert canvas.feature == (
        "Every one of the 30 lights set individually, saved as a Hubspace custom pattern on its one strand."
    )
    assert compile_mode("goodnight-glow", Tier.T3, one).feature == (
        "Colour and dimming: one Hubspace strand set to a single colour from the scene."
    )


def test_d62_counted_copy_says_both_and_one_and_leaves_eight_alone():
    two, one, eight = CANOPY[:2], CANOPY[:1], CANOPY[:8]
    calm = "Colour and dimming: all nine Hubspace strands set to soft ivory."
    canvas = "Every one of the 270 lights set individually, saved as a Hubspace custom pattern on each of nine strands."
    assert _counted(calm, two, CANOPY) == "Colour and dimming: both Hubspace strands set to soft ivory."
    assert (
        _counted("All nine Hubspace strands switched off.", two, CANOPY)
        == "Both Hubspace strands switched off."
    )
    assert _counted(canvas, two, CANOPY).startswith("Every one of the 60 lights")
    assert _counted(calm, one, CANOPY) == "Colour and dimming: one Hubspace strand set to soft ivory."
    assert _counted(canvas, one, CANOPY).endswith("on its one strand.")
    assert _counted("A built-in Hubspace lighting program on all nine strands.", one, CANOPY) == (
        "A built-in Hubspace lighting program on one strand."
    )
    eight_copy = _counted(canvas, eight, CANOPY)
    assert "240 lights" in eight_copy and "each of eight strands" in eight_copy
    spec = MODES.mode("coastal-tides").techniques.strand.look
    assert _generic_feature(spec, 2) == "Colour and dimming: both Hubspace strands set to one colour."
    assert _generic_feature(spec, 1) == "Colour and dimming: one Hubspace strand set to one colour."
    assert _generic_feature(spec, 9) == "Colour and dimming: all nine Hubspace strands set to one colour."


def test_d62_the_engine_drives_only_the_registered_modules_and_stops_asking_for_the_others(
    tmp_path, monkeypatch, caplog
):
    _unregister(monkeypatch, OTHERS)

    async def main():
        box: dict = {}
        services, wiring, engine = await _boot(
            tmp_path, before_start=lambda s: box.setdefault("writes", _record_writes(s))
        )
        writes = box["writes"]
        # the show box is live hardware: no stepped run until Gate R (D77 gives every scene a stepped rung), so each
        # guest choice and the brightness are one command per driven module
        refuse_stepped(engine)
        clock = services.clock
        assert await _until(clock, lambda: engine.current_plan() is not None and not engine.commissioning, 60)
        idle = engine.current_plan()
        await _hold(services, engine, 20)
        scenes = []
        for mode_id in MODES.modes:
            await clock.sleep(engine.guest_gap_s(engine.current_plan()) + 1)
            state = await engine.request_mode(mode_id, source="kiosk")
            await _hold(services, engine, 10)
            scenes.append((mode_id, state.refused, engine.current_plan(), engine.state().feature))
        await clock.sleep(engine.guest_gap_s(engine.current_plan()) + 1)
        before = len(writes)
        await engine.set_guest_brightness(50, source="kiosk")
        await _hold(services, engine, 10)
        brightness = writes[before:]
        wiring.request_refresh()  # what an outage's end or /api/devices?refresh=1 asks for: every module
        await _hold(services, engine, 600)  # ten minutes of the capability loop's retries
        out = (
            idle,
            scenes,
            brightness,
            sorted(set(writes)),
            wiring.missing(),
            engine.state(),
            wiring.attention(),
        )
        await wiring.close()
        await services.close()
        return out

    with caplog.at_level("WARNING", logger="conductor.show"):
        idle, scenes, brightness, written, missing, final, attention = run_virtual(main)
    assert sorted(idle.modules) == list(LIVE) and sorted(idle.skipped) == OTHERS
    for mode_id, refused, plan, feature in scenes:
        assert not refused, mode_id
        assert plan.mode_id == mode_id and sorted(plan.modules) == list(LIVE), mode_id
        assert "nine" not in feature and "270" not in feature, (mode_id, feature)
    assert sorted(brightness) == [f"sim-{d}" for d in LIVE]  # one command per driven module
    assert written == [f"sim-{d}" for d in LIVE]
    assert missing == []
    assert not [r for r in caplog.records if "Couldn't read canopy-" in r.getMessage()]
    assert final.phase is CanopyPhase.NEEDS_ATTENTION
    assert len(attention) == 7 and all("/ops/accounts" in line for line in attention)


def test_d62_registering_a_module_mid_run_brings_the_canopy_out_of_rest(tmp_path, monkeypatch):
    gone = _unregister(monkeypatch, [m.id for m in CANOPY])

    async def live_commissions_by_hand(self):
        return None  # live never commissions at startup (the simulator's would write the slots too)

    monkeypatch.setattr(ShowWiring, "_sim_commission", live_commissions_by_hand)

    async def main():
        box: dict = {}
        services, wiring, engine = await _boot(
            tmp_path, before_start=lambda s: box.setdefault("writes", _record_writes(s))
        )
        writes = box["writes"]
        clock = services.clock
        await _hold(services, engine, 30)
        resting = (engine.state().phase, engine.current_plan(), await engine.safe_look(source="ops"))
        before = len(writes)
        gone.discard("canopy-r0c0")  # registered on /ops/accounts (which asks for a refresh)
        wiring.request_refresh(["canopy-r0c0"])
        shown = await _until(clock, lambda: engine.current_plan() is not None, 30)
        await _hold(services, engine, 20)
        out = (resting, shown, engine.current_plan(), writes[before:], wiring.attention())
        await wiring.close()
        await services.close()
        return out

    (phase, plan_before, safe), shown, plan, sent, attention = run_virtual(main)
    assert phase is CanopyPhase.RESTING and plan_before is None
    assert safe.refused and "register one on /ops/accounts" in safe.message
    assert shown is not None
    assert plan.mode_id == "lowcountry-calm" and list(plan.modules) == ["canopy-r0c0"]
    assert sent == ["sim-canopy-r0c0"]  # Calm, as one command
    assert len(attention) == 8


def test_d62_a_module_registered_mid_run_joins_the_safe_look_even_when_its_profile_cant_be_read(
    tmp_path, monkeypatch
):
    gone = _unregister(monkeypatch, [m.id for m in CANOPY if m.id != "canopy-r0c0"])

    async def live_commissions_by_hand(self):
        return None

    monkeypatch.setattr(ShowWiring, "_sim_commission", live_commissions_by_hand)

    def before_start(services):
        writes = _record_writes(services)
        real = services.sim.fetch_profile

        async def fetch_profile(address, **kwargs):
            if address == "sim-canopy-r0c1":
                raise RuntimeError("simulated profile read failure")
            return await real(address, **kwargs)

        services.sim.fetch_profile = fetch_profile
        return writes

    async def main():
        box: dict = {}
        services, wiring, engine = await _boot(
            tmp_path, before_start=lambda s: box.setdefault("writes", before_start(s))
        )
        writes = box["writes"]
        clock = services.clock
        assert await _until(clock, lambda: engine.current_plan() is not None, 60)
        calm = engine.current_plan()
        before = len(writes)
        gone.discard("canopy-r0c1")  # registered on /ops/accounts, as conductor/routes/accounts.py does it
        wiring.caps.pop("canopy-r0c1", None)
        wiring.request_refresh(["canopy-r0c1"])
        await _hold(services, engine, 120)
        out = (
            calm,
            engine.current_plan(),
            writes[before:],
            engine.state().modules["canopy-r0c1"],
            wiring.attention(),
        )
        await wiring.close()
        await services.close()
        return out

    calm, plan, sent, module, attention = run_virtual(main)
    assert calm.mode_id == "lowcountry-calm" and list(calm.modules) == ["canopy-r0c0"]
    assert plan.mode_id == "lowcountry-calm"
    assert sorted(plan.modules) == list(LIVE) and len(plan.skipped) == 7
    assert any("assumes" in n and "canopy-r0c1" in n for n in plan.notes), plan.notes
    assert "sim-canopy-r0c1" in sent
    assert "no Hubspace device registered" not in (module.get("reason") or "")
    line = next(a for a in attention if "simulated profile read failure" in a)
    assert "hasn't been read" in line


# ---- F3 ------------------------------------------------------------------------------------------------------------
def _answer(w, outcome: Outcome, status: int, window: dict) -> None:
    real = w.sim._exchange

    async def exchange(device_id, *, write):
        if window["on"]:
            await w.clock.sleep(0.3)
            return simmod._Exchange(outcome, status, f"HTTP {status} (simulated)")
        return await real(device_id, write=write)

    w.sim._exchange = exchange


def test_f3_a_503_outage_rests_the_canopy_instead_of_downgrading():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("sunset", source="ops")
        await w.until(w.all_correct, timeout=10)
        window = {"on": False}
        _answer(w, Outcome.THROTTLED, 503, window)
        window["on"] = True
        await w.engine.request_mode("holiday", source="ops")
        rested = await w.until(lambda: w.engine.outage is not None, timeout=30)
        await w.run_for(20)
        kiosk = await w.engine.request_mode("sunset", source="kiosk")
        await w.run_for(150)
        during = w.engine.state()
        window["on"] = False
        recovered = await w.until(lambda: w.engine.outage is None and w.all_correct(), timeout=60)
        out = (rested, kiosk, during, recovered, list(w.engine.stats["tier_changes"]), dict(w.engine.floors))
        await w.close()
        return out

    rested, kiosk, during, recovered, tier_changes, floors = run_virtual(main)
    assert rested is not None, "503s never rested the canopy"
    assert kiosk.refused and "resting" in kiosk.message
    assert during.phase is CanopyPhase.RESTING
    assert tier_changes == [] and floors == {}
    assert recovered is not None


def test_f3_persistent_429s_still_downgrade():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("sunset", source="ops")
        await w.until(w.all_correct, timeout=10)
        window = {"on": True}
        _answer(w, Outcome.THROTTLED, 429, window)
        await w.engine.reassert_all(source="ops")
        await w.run_for(60)
        out = (w.engine.outage, list(w.engine.stats["tier_changes"]))
        window["on"] = False
        await w.close()
        return out

    outage, changes = run_virtual(main)
    assert outage is None
    assert changes and changes[0]["direction"] == "down" and "throttled" in changes[0]["reason"]


# ---- F4 ------------------------------------------------------------------------------------------------------------
def test_f4_a_t1_downgrade_never_lands_on_hand_painted_copy(tmp_path):
    async def main():
        services, wiring, engine = await _boot(tmp_path)
        await _settled(services, wiring, engine)
        await engine.request_mode("rainbow-row-sunset", source="ops")
        await _hold(services, engine, 15)
        before = engine.state()
        engine.downgrade("a module stayed wrong (test)")
        after = engine.state()
        try:
            wiring.compile("rainbow-row-sunset", Tier.T2)
            t2 = None
        except PlanError as e:
            t2 = str(e)
        origin = services.registry.get("canopy-r0c0").slot_origin
        await wiring.close()
        await services.close()
        return before, after, t2, origin

    before, after, t2, origin = run_virtual(main)
    assert before.tier == "canvas"
    assert after.tier == "modules", after.tier
    assert "Hubspace app" not in after.feature
    assert t2 is not None and "commissioned pattern" in t2
    assert origin == {"custom-1": "api", "custom-2": "api", "custom-3": "api"}


# ---- F5 ------------------------------------------------------------------------------------------------------------
def test_f5_a_recalibrated_module_stays_t1_and_its_stale_slots_are_rewritten_once(tmp_path):
    async def main():
        services, wiring, engine = await _boot(tmp_path)
        await _settled(services, wiring, engine)
        await engine.request_mode("rainbow-row-sunset", source="ops")
        await _hold(services, engine, 20)
        before = dict(services.registry.get("canopy-r0c0").slot_hashes)
        services.update_registry(
            "canopy-r0c0", {"cable_path": "serpentine_cols", "start_corner": "bottom-right"}
        )
        await engine.request_mode("rainbow-row-sunset", source="ops")
        phases = []
        for _ in range(25 * 12):  # 25 minutes
            engine.last_change_mono = services.clock.monotonic()
            await services.clock.sleep(5)
            phases.append(engine.state().phase)
        slots = {"rainbow-row-sunset": "custom-1", "garden-gathering": "custom-2", "golden-hour": "custom-3"}
        compiled = {
            slot: wiring.compile(mode, Tier.T1).modules["canopy-r0c0"].slot_hash
            for mode, slot in slots.items()
        }
        on_device = {
            slot: R.slot_hash(_device_slot(services, "canopy-r0c0", slot)) for slot in slots.values()
        }
        recorded = dict(services.registry.get("canopy-r0c0").slot_hashes)
        alerts = [a["message"] for a in services.telemetry.recent(kind="alert", limit=5000)]
        out = (
            list(engine.stats["tier_changes"]),
            engine.state(),
            engine.reconciler.stats["slot_rewrites"],
            before,
            compiled,
            on_device,
            recorded,
            phases,
            alerts,
        )
        await wiring.close()
        await services.close()
        return out

    changes, final, rewrites, before, compiled, on_device, recorded, phases, alerts = run_virtual(main)
    assert changes == [], changes  # no downgrade/upgrade oscillation
    assert final.tier == "canvas" and final.phase is CanopyPhase.READY
    stale = [slot for slot in compiled if compiled[slot] != before[slot]]
    assert "custom-1" in stale
    assert rewrites == len(stale)  # each stale slot once (rate-limited), never a loop
    assert on_device == compiled == recorded
    assert phases.count(CanopyPhase.NEEDS_ATTENTION) == 0
    assert not any("didn't take" in a for a in alerts), alerts


# ---- F6 ------------------------------------------------------------------------------------------------------------
MOTION_COPY = "A saved Hubspace custom pattern with an animated effect (test only)."


def test_f6_motion_copy_only_once_the_device_holds_the_benched_code(tmp_path):
    async def main():
        services, wiring, engine = await _boot(tmp_path)
        await _settled(services, wiring, engine)
        # later, the bench enables a motion code (config) and the Conductor picks it up. Every show scene is static
        # (D59), so the motion technique is added here, as the soak adds it.
        modes = wiring.modes_file.model_copy(deep=True)
        canvas = modes.modes["golden-hour"].techniques.canvas
        canvas.motion_effect = MotionEffect(motionEffect=1, motionSpeed=10)
        canvas.motion_feature = MOTION_COPY
        wiring.modes_file = modes
        services.settings.effects.enabled["motionEffect"][1] = "SIMULATED scroll"
        engine.invalidate_t1_plans()
        await engine.request_mode("golden-hour", source="ops")
        await _hold(services, engine, 20)
        early = engine.state()
        early_motion = engine.current_plan().motion
        early_effect = _device_slot(services, "canopy-r0c0", "custom-3")[R.FRAME_CLASS]["motionEffect"]
        await _hold(services, engine, 12 * 60)
        late = engine.state()
        late_effect = _device_slot(services, "canopy-r0c0", "custom-3")[R.FRAME_CLASS]["motionEffect"]
        recorded = services.registry.get("canopy-r0c0").slot_effects.get("custom-3")
        # an app edit that only switches the animation off: the hash can't see it, the effect check does
        value = _device_slot(services, "canopy-r0c0", "custom-3")
        _app_paint_value = R.frame_value(
            value, R.frame_entries(value), expected_length=30, effects={"motionEffect": 0}
        )
        stamp = int(services.clock.now() * 1000)
        services.sim.cloud.accept_wire(
            {
                "metadeviceId": "sim-canopy-r0c0",
                "values": [R.slot_write("custom-3", _app_paint_value).to_wire(stamp)],
            }
        )
        await _hold(services, engine, 90)
        alerts = [
            a["message"]
            for a in services.telemetry.recent(kind="alert", limit=5000)
            if "custom-3" in a["message"]
        ]
        await wiring.close()
        await services.close()
        return early, early_motion, early_effect, late, late_effect, recorded, alerts

    early, early_motion, early_effect, late, late_effect, recorded, alerts = run_virtual(main)
    golden = MODES.mode("golden-hour").techniques.canvas
    assert early_effect == 0
    assert early.feature == golden.feature, "claimed motion while the device's slot holds motionEffect 0"
    assert early_motion is True  # re-triggered as one burst anyway
    assert late_effect == 1 and recorded and recorded["motionEffect"] == 1
    assert late.feature == MOTION_COPY and late.tier == "canvas"
    assert any("effect fields of custom-3 changed" in a for a in alerts), alerts


# ---- F7 ------------------------------------------------------------------------------------------------------------
def _open(client, device_id: str) -> str:
    r = client.post(f"/api/module/{device_id}/open", json={})
    assert r.status_code == 200, r.text
    return r.json()["lease"]


def test_f7_record_a_hand_painted_slot_on_the_module_page_and_t2_selects_it(app_client_show):
    client = app_client_show
    services = client.app.state.services
    wiring = services.extras["show"]

    def wait(predicate, seconds: float = 30.0) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    assert wait(lambda: wiring.last_commission is not None)
    # the API-commissioned slot is refused: it isn't an app painting
    lease = _open(client, "canopy-r0c0")
    r = client.post(
        "/api/module/canopy-r0c0/slot", json={"lease": lease, "slot": "custom-2", "action": "record_app"}
    )
    assert r.status_code == 409 and "commissioned" in r.json()["detail"]
    client.post("/api/module/canopy-r0c0/close", json={"lease": lease})

    painted = {}
    for i, entry in enumerate(services.registry.canopy_modules()):
        painted[entry.id] = client.portal.call(
            _paint_async, services, entry.id, "custom-2", (200, 40 + 10 * i, 10)
        )
        lease = _open(client, entry.id)
        r = client.post(
            f"/api/module/{entry.id}/slot", json={"lease": lease, "slot": "custom-2", "action": "record_app"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["slot"]["hash"] == painted[entry.id] and body["slot"]["origin"] == "app"
        assert "Hubspace app" in body["slot"]["message"] and "Nothing was sent" in body["slot"]["message"]
        assert (
            "once every canopy module with a Hubspace device registered has it recorded (all nine on the full "
            in (body["slot"]["message"])
        )
        client.post(f"/api/module/{entry.id}/close", json={"lease": lease})
    entry = services.registry.get("canopy-r2c2")
    assert entry.slot_hashes["custom-2"] == painted["canopy-r2c2"] and entry.slot_origin["custom-2"] == "app"
    assert wiring.tier_allowed("garden-gathering", Tier.T1)[0] is False
    plan = wiring.compile("garden-gathering", Tier.T2)
    assert plan.tier is Tier.T2 and "Hubspace app" in plan.feature
    r = client.post("/api/canopy/mode", json={"mode": "garden-gathering"})
    assert r.status_code == 200, r.text
    assert r.json()["state"]["tier"] == "slot"


async def _paint_async(services, device_id, slot, color):
    return _app_paint(services, device_id, slot, color)


# ---- F8 ------------------------------------------------------------------------------------------------------------
def test_f8_upgrade_back_skips_rungs_that_cant_compile():
    async def main():
        compiler = FakeCompiler(uncompilable={("sunset", Tier.T2)})
        w = make_world(settings=QUIET, compiler=compiler)
        await w.engine.commission(source="ops")
        await w.engine.start(apply_initial=False)
        compiler.broken = {("sunset", Tier.T1)}
        compiler.broken_module = "canopy-r1c1"
        await w.engine.request_mode("sunset", source="ops")
        down = await w.until(lambda: w.engine.state().tier == "modules", timeout=60)
        compiler.broken = set()
        t_down = w.clock.monotonic()
        up = await w.until(lambda: w.engine.state().tier == "canvas", timeout=900, step=1.0)
        out = (down, up, w.clock.monotonic() - t_down, list(w.engine.stats["tier_changes"]))
        await w.close()
        return out

    down, up, _elapsed, changes = run_virtual(main)
    assert down is not None
    assert up is not None and up <= 320, f"T1 came back after {up} s (07 §6: 5 clean minutes)"
    assert [c["to"] for c in changes] == ["modules", "canvas"]


# ---- F9 ------------------------------------------------------------------------------------------------------------
def _painted_ctx(header: dict | None, *, effects=None, modes=TM) -> CompileContext:
    readback = {r.function_instance: r.value for r in STATES if r.function_class == R.FRAME_CLASS}
    if header is not None:
        readback["custom-3"] = {R.FRAME_CLASS: {**readback["custom-3"][R.FRAME_CLASS], **header}}
    else:
        readback.pop("custom-3", None)
    return CompileContext(
        registry=REGISTRY,
        capabilities={m.id: _caps() for m in CANOPY},
        slot_readback={m.id: readback for m in CANOPY},
        recorded_hashes={m.id: {"custom-3": "c3" * 8} for m in CANOPY},
        recorded_origins={m.id: {"custom-3": "app"} for m in CANOPY},
        recorded_effects=effects,
        modes=modes,
    )


def test_f9_a_hand_painted_golden_hour_that_may_animate_is_a_motion_plan():
    animated = compile_mode("golden-hour", Tier.T2, _painted_ctx({"motionEffect": 3}))
    unknown = compile_mode("golden-hour", Tier.T2, _painted_ctx(None))
    static = compile_mode("golden-hour", Tier.T2, _painted_ctx({"motionEffect": 0, "brightnessEffect": 0}))
    recorded = compile_mode(
        "golden-hour",
        Tier.T2,
        _painted_ctx(None, effects={m.id: {"custom-3": {"motionEffect": 2}} for m in CANOPY}),
    )
    assert animated.motion is True and unknown.motion is True and recorded.motion is True
    assert static.motion is False
    # a mode without `motion: true` never re-triggers as one burst at T2
    sunset_ctx = _painted_ctx({"motionEffect": 3})
    sunset_ctx.recorded_hashes = {m.id: {"custom-1": "a1" * 8} for m in CANOPY}
    sunset_ctx.recorded_origins = {m.id: {"custom-1": "app"} for m in CANOPY}
    assert compile_mode("rainbow-row-sunset", Tier.T2, sunset_ctx).motion is False
    # every show scene is static (D59): the show's Golden Hour has no `motion: true` either
    assert (
        compile_mode("golden-hour", Tier.T2, _painted_ctx({"motionEffect": 3}, modes=MODES)).motion is False
    )


# ---- F10 -----------------------------------------------------------------------------------------------------------
def test_f10_calm_never_claims_2700_k_when_the_device_floor_is_higher():
    # on the pre-D59 recipes (TM): the show's Calm is an RGB look since D59, but white copy that names Kelvin stays
    warm_floor = CompileContext(
        registry=REGISTRY, capabilities={m.id: _caps(cct_range=(3000, 6500)) for m in CANOPY}, modes=TM
    )
    calm = compile_mode("lowcountry-calm", Tier.STRAND, warm_floor)
    assert "2700" not in calm.feature
    assert R.color_rgb(255, 190, 120) in calm.modules["canopy-r0c0"].rows  # the configured fallback look
    assert any("outside its colour temperature range" in n for n in calm.notes)
    # Goodnight's copy names no Kelvin value: 2200 K is clamped to the floor and the copy stands
    goodnight = compile_mode(
        "goodnight-glow",
        Tier.STRAND,
        CompileContext(registry=REGISTRY, capabilities={m.id: _caps() for m in CANOPY}, modes=TM),
    )
    assert goodnight.feature == TM.mode("goodnight-glow").techniques.strand.feature
    assert R.StateRow("color-temperature", None, "2700") in goodnight.modules["canopy-r0c0"].rows


# ---- F11 -----------------------------------------------------------------------------------------------------------
def test_f11_the_soak_runs_the_shows_wiring_and_a_motion_plan(tmp_path):
    from conductor import soak

    # Oct 22: an hour and 30 changes, so the latency row's p95 is a p95. Over 20 minutes and 8 changes (6–8 timed) it
    # was the slowest change, and one change caught by the pinned 429s or a silent drop on one of nine modules (5–7 s)
    # failed it for about one seed in four — any config edit moves the seeded path. Measured at 1 h / 30 changes over
    # seeds 0–11: p95 3.2–4.6 s (per-light and module-level changes alike; docs/STATUS.md)
    cfg = soak.SoakConfig(hours=1, changes=30, seed=5, report_path=tmp_path / "r.md")
    results, verdicts, meta = soak.run_soak(cfg)
    # rows the soak reports without gating on (the ripple's and stepped looks' spread, D64/D65) never fail it
    failed = [v["name"] for v in verdicts if not v["pass"] and v.get("gating", True)]
    assert not failed, failed
    assert soak.passed(verdicts)
    assert "ShowWiring" in meta["compiler"]
    assert results["wiring"]["show_wiring"] is True
    assert results["wiring"]["motion_plans"] >= 1  # Golden Hour ran as a motion plan (SIMULATED code)
    assert results["commission"]["ok"]
    assert "SIMULATED" in (tmp_path / "r.md").read_text()
