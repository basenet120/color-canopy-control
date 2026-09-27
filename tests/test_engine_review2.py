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

from conductor import rows as R
from conductor.capabilities import detect
from conductor.clock import VirtualClock, run_virtual
from conductor.config import load_registry
from conductor.drivers import sim as simmod
from conductor.drivers.base import Outcome
from conductor.engine_api import CanopyPhase, PlanError, Tier
from conductor.gate import Purpose
from conductor.looks import CompileContext, compile_mode, load_modes
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
    """Someone paints `slot` in the Hubspace app: the cloud stores a new pattern (a normal PUT from the app) — the
    device's full 60-entry frame (what the app writes into the entries no puck uses is UNVERIFIED)."""
    value = R.frame_value(None, [R.FrameEntry(*color, 0, 100, 0)] * 60, expected_length=60, effects=effects)
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
        compile_mode("holiday-at-home", Tier.STRAND, CompileContext(registry=REGISTRY, capabilities={}))
    except PlanError as e:
        assert "hasn't been read yet" in str(e)
    else:  # pragma: no cover
        raise AssertionError("a preset look can't be planned without any profile")


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
def test_f6_motion_copy_only_once_the_device_holds_the_benched_code(tmp_path):
    async def main():
        services, wiring, engine = await _boot(tmp_path)
        await _settled(services, wiring, engine)
        # later, the bench enables a motion code (config) and the Conductor picks it up
        modes = wiring.modes_file.model_copy(deep=True)
        modes.modes["golden-hour"].techniques.canvas.motion_effect.motionEffect = 1
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
            value, R.frame_entries(value), expected_length=60, effects={"motionEffect": 0}
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
    assert late.feature == golden.motion_feature and late.tier == "canvas"
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
def _painted_ctx(header: dict | None, *, effects=None) -> CompileContext:
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


# ---- F10 -----------------------------------------------------------------------------------------------------------
def test_f10_calm_never_claims_2700_k_when_the_device_floor_is_higher():
    warm_floor = CompileContext(
        registry=REGISTRY, capabilities={m.id: _caps(cct_range=(3000, 6500)) for m in CANOPY}
    )
    calm = compile_mode("lowcountry-calm", Tier.STRAND, warm_floor)
    assert "2700" not in calm.feature
    assert R.color_rgb(255, 190, 120) in calm.modules["canopy-r0c0"].rows  # the configured fallback look
    assert any("outside its colour temperature range" in n for n in calm.notes)
    # Goodnight's copy names no Kelvin value: 2200 K is clamped to the floor and the copy stands
    goodnight = compile_mode(
        "goodnight-glow",
        Tier.STRAND,
        CompileContext(registry=REGISTRY, capabilities={m.id: _caps() for m in CANOPY}),
    )
    assert goodnight.feature == MODES.mode("goodnight-glow").techniques.strand.feature
    assert R.StateRow("color-temperature", None, "2700") in goodnight.modules["canopy-r0c0"].rows


# ---- F11 -----------------------------------------------------------------------------------------------------------
def test_f11_the_soak_runs_the_shows_wiring_and_a_motion_plan(tmp_path):
    from conductor import soak

    cfg = soak.SoakConfig(hours=1 / 3, changes=8, seed=5, report_path=tmp_path / "r.md")
    results, verdicts, meta = soak.run_soak(cfg)
    failed = [v["name"] for v in verdicts if not v["pass"]]
    assert not failed, failed
    assert "ShowWiring" in meta["compiler"]
    assert results["wiring"]["show_wiring"] is True
    assert results["wiring"]["motion_plans"] >= 1  # Golden Hour ran as a motion plan (SIMULATED code)
    assert results["commission"]["ok"]
    assert "SIMULATED" in (tmp_path / "r.md").read_text()
