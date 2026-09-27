"""SimDriver: wire validation, cloud-vs-device semantics, the pinned fault model and its rates (07 §5, §7).

Everything runs on the virtual-time loop; nothing sleeps for real and nothing touches a network.
"""

import asyncio
import json

import pytest

from conductor.capabilities import detect
from conductor.clock import VirtualClock, run_virtual
from conductor.config import ConfigError, load_registry
from conductor.drivers.base import DeviceDriver, Outcome, StateRow, rows_by_key
from conductor.drivers.sim import SimDriver
from conductor.gate import Purpose
from conductor.render import head_colors, kelvin_to_rgb
from conductor.rows import (
    FRAME_CLASS,
    FrameEntry,
    Look,
    brightness,
    color_mode,
    color_temperature,
    compare,
    frame_value,
    full_state_vector,
    payload_bytes,
    power,
    slot_hash,
    slot_select,
    slot_write,
)
from conductor.sim.settings import SimSettings

REG = load_registry()
KIT = "sim-bench-kit"
MODULE = "sim-canopy-r1c1"
ENGINE = Purpose.ENGINE


def make(settings=None, seed=0):
    clock = VirtualClock()
    return SimDriver(REG, clock=clock, settings=settings, seed=seed), clock


def painted(n=30, slot="custom-2"):
    entries = [
        FrameEntry(255, 99, 2) if i % 2 == 0 else FrameEntry(0, 0, 255, 2700, 50, 40) for i in range(n)
    ]
    value = frame_value(None, entries, expected_length=None)
    return [slot_write(slot, value), *slot_select(slot), power(True)]


def paint_body(driver_clock, rows, address=KIT):
    ms = round(driver_clock.now() * 1000)
    return {"metadeviceId": address, "values": [r.to_wire(ms) for r in rows]}


# ---- world + protocol -------------------------------------------------------------------------------------------
def test_one_device_per_enabled_registry_entry_and_protocol_shape():
    driver, _ = make()
    assert isinstance(driver, DeviceDriver)
    assert driver.name == "sim" and driver.simulated
    assert sorted(driver.addresses()) == sorted(d.sim_address for d in REG.devices)
    assert driver.account_of(KIT) == "bench" and driver.account_of(MODULE) == "canopy"
    with pytest.raises(LookupError):
        driver.account_of("sim-nope")
    health = driver.health()
    json.dumps(health)
    assert health["simulated"] and health["devices"] == len(REG.devices)
    assert health["accounts"]["canopy"] == {"devices": 12, "token": "ok"}  # nine modules + three spares


def test_discover_and_fetch_profile_reflect_current_cloud_state():
    driver, _ = make()

    async def main():
        everything = await driver.discover()
        canopy = await driver.discover("canopy")
        await driver.write(KIT, painted(), purpose=Purpose.BENCH)
        info = await driver.fetch_profile(KIT)
        with pytest.raises(LookupError):
            await driver.fetch_profile("sim-nope")
        return everything, canopy, info

    everything, canopy, info = run_virtual(main)
    assert len(everything) == len(REG.devices) and len(canopy) == 12  # nine modules + three spares
    assert all(i.simulated and i.account == "canopy" for i in canopy)
    assert info.device_id == KIT and info.raw["id"] == KIT and info.model.startswith("SQHDK03")
    caps = detect(info.functions, info.states)
    assert caps.framebuffer_length == 30 and caps.individual
    state = rows_by_key(info.states)
    assert state[("color-individual", "custom")] == "custom-2"
    raw_rows = {(s["functionClass"], s["functionInstance"]): s for s in info.raw["states"]}
    assert raw_rows[("color-mode", None)]["value"] == "individual"
    assert raw_rows[("color-mode", None)]["lastUpdateTime"] > 1e12  # epoch ms, stamped by the driver


# ---- write → readback -------------------------------------------------------------------------------------------
def test_full_frame_select_mode_and_power_in_one_write_round_trips():
    driver, clock = make()
    rows = painted()

    async def main():
        before = driver.device_view(KIT)
        w = await driver.write(KIT, rows, purpose=Purpose.BENCH)
        r = await driver.read_state(KIT)
        await clock.sleep(2.0)
        return before, w, r, driver.device_view(KIT)

    before, w, r, applied = run_virtual(main)
    assert w.ok and w.http_status == 200 and w.simulated
    assert w.bytes_sent == payload_bytes(KIT, rows, round(w.sent_at * 1000))
    assert 2500 < w.bytes_sent < 4000  # ~3 KB compact for 30 entries (04)
    assert w.acked_at is not None and w.acked_at - w.sent_at == pytest.approx(w.elapsed_s)
    assert r.ok and r.simulated and r.available is True
    assert compare(rows, r.rows) == []
    assert r.get("wifi-rssi") is not None
    assert compare(rows, applied) == []
    assert rows_by_key(before)[("color-mode", None)] == "sequence"
    heads = head_colors(applied, 30)
    assert heads[0] == {"r": 255, "g": 99, "b": 2}
    ww = kelvin_to_rgb(2700)  # half blue + 40% warm white
    assert heads[1] == {"r": round(ww[0] * 0.4), "g": round(ww[1] * 0.4), "b": round(127.5 + ww[2] * 0.4)}


def test_numbers_are_stored_as_ints_and_readback_is_a_copy():
    driver, _ = make()

    async def main():
        await driver.write(KIT, full_state_vector(Look("white", 60, cct=2700)), purpose=ENGINE)
        first = await driver.read_state(KIT)
        for row in first.rows:
            if isinstance(row.value, dict):
                row.value.clear()
        return first, await driver.read_state(KIT)

    _first, second = run_virtual(main)
    assert second.get("color-temperature") == 2700  # sent "2700", read back as an int (D7)
    assert second.get("brightness") == 60
    frames = [r for r in second.rows if r.function_class == FRAME_CLASS]
    assert all(slot_hash(r.value) for r in frames)  # mutating a readback didn't touch the cloud


# ---- validation (HTTP 400) ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([StateRow("disco-ball", None, "on")], "no disco-ball function"),
        ([StateRow("color-mode", None, "disco")], "isn't one of this device's values"),
        ([StateRow("brightness", None, 150)], "outside this device's range"),
        ([StateRow("color-temperature", None, "2000")], "outside this device's range 2700–6500"),
        ([StateRow("speed", "color-sequence", "fast")], "needs a whole number"),
        ([StateRow("color-rgb", None, {"r": 1, "g": 2, "b": 3})], "color-rgb needs"),
        ([StateRow("available", None, True)], "no available function"),
        ([StateRow(FRAME_CLASS, "custom-1", {FRAME_CLASS: {"id": 0}})], "header field"),
        ([StateRow(FRAME_CLASS, "custom-9", {})], "no color-sequence-v2/custom-9 function"),
        ([power(True), StateRow("color-mode", None, 4)], "isn't one of"),
    ],
)
def test_bad_rows_are_refused_with_400_and_nothing_is_stored(rows, reason):
    driver, _ = make()

    async def main():
        before = driver.cloud_view(KIT)
        w = await driver.write(KIT, rows, purpose=ENGINE)
        return before, w, driver.cloud_view(KIT)

    before, w, after = run_virtual(main)
    assert w.outcome is Outcome.CLIENT_ERROR and w.http_status == 400
    assert reason in w.error and "HTTP 400" in w.error
    assert before == after


def test_malformed_frame_entries_are_refused():
    driver, clock = make()
    good = frame_value(None, [FrameEntry()] * 30, expected_length=30)
    bad_colour = json.loads(json.dumps(good))
    bad_colour[FRAME_CLASS]["frameBuffer"]["framebuffer"][3]["r"] = 300
    missing_field = json.loads(json.dumps(good))
    del missing_field[FRAME_CLASS]["frameBuffer"]["framebuffer"][0]["cct"]
    no_fb = json.loads(json.dumps(good))
    del no_fb[FRAME_CLASS]["frameBuffer"]
    for value, reason in ((bad_colour, "outside 0–255"), (missing_field, "entry 0"), (no_fb, "frameBuffer")):
        resp = driver.cloud.accept_wire(paint_body(clock, [slot_write("custom-1", value)]))
        assert resp.status == 400 and reason in resp.error
    assert driver.cloud.accept_wire(paint_body(clock, [slot_write("custom-1", good)])).status == 200
    assert driver.cloud.accept_wire({"values": "nope"}).status == 400
    assert driver.cloud.accept_wire(paint_body(clock, [power(True)], address="sim-nope")).status == 404


def test_wrong_frame_length_rejected_by_default_and_stored_under_accept_policy():
    short = [slot_write("custom-1", frame_value(None, [FrameEntry(9, 9, 9)] * 29, expected_length=None))]
    strict, _ = make()
    lenient, _ = make(SimSettings.dev().with_(wrong_length_policy="accept"))

    async def main():
        return await strict.write(KIT, short, purpose=ENGINE), await lenient.write(KIT, short, purpose=ENGINE)

    refused, stored = run_virtual(main)
    assert refused.http_status == 400 and "29 entries" in refused.error and "30" in refused.error
    assert stored.ok
    fb = rows_by_key(lenient.cloud_view(KIT))[(FRAME_CLASS, "custom-1")][FRAME_CLASS]["frameBuffer"]
    assert len(fb["framebuffer"]) == 29


def test_seconds_timestamps_get_200_but_are_silently_dropped():
    driver, clock = make()
    ms = round(clock.now() * 1000)
    seconds = int(clock.now())
    body = {
        "metadeviceId": KIT,
        "values": [
            color_mode("white").to_wire(seconds),
            brightness(42).to_wire(ms),
        ],
    }
    resp = driver.cloud.accept_wire(body)
    assert resp.status == 200 and resp.ok
    assert [r.function_class for r in resp.dropped] == ["color-mode"]
    assert [r.function_class for r in resp.stored] == ["brightness"]
    cloud = rows_by_key(driver.cloud_view(KIT))
    assert cloud[("color-mode", None)] == "sequence"  # unchanged
    assert cloud[("brightness", None)] == 42
    assert driver.stats()["seconds_rows_dropped"] == 1


def test_write_stamps_epoch_milliseconds():
    driver, clock = make()

    async def main():
        await driver.write(KIT, [brightness(33)], purpose=ENGINE)
        return driver.cloud.device(KIT).cloud_times[("brightness", None)], clock.now()

    stamp, now = run_virtual(main)
    assert stamp > 1e12 and abs(stamp / 1000 - now) < 1.0


# ---- cloud vs device ---------------------------------------------------------------------------------------------
def test_readback_semantics_with_an_unplugged_device():
    driver, clock = make()
    select = [*slot_select("custom-3"), power(True)]

    async def main():
        driver.faults.set_available(KIT, False)
        w = await driver.write(KIT, select, purpose=Purpose.BENCH)
        await clock.sleep(3)
        cloud_rb = await driver.read_state(KIT)
        driver.faults.set_readback_semantics("device")
        device_rb = await driver.read_state(KIT)
        view = driver.device_view(KIT)
        driver.faults.set_available(KIT, True)
        await clock.sleep(3)
        replugged = driver.device_view(KIT)
        return w, cloud_rb, device_rb, view, replugged

    w, cloud_rb, device_rb, view, replugged = run_virtual(main)
    assert w.ok  # the cloud takes the write although the controller is off
    assert cloud_rb.available is False and device_rb.available is False
    assert compare(select, cloud_rb.rows) == []  # cloud readback can't see the miss (07 §5)
    assert compare(select, device_rb.rows) != []  # device readback can
    assert device_rb.get("color-mode") == "sequence"
    assert rows_by_key(view)[("power", None)] == "off"  # the lights are dark
    assert rows_by_key(replugged)[("color-mode", None)] == "sequence"  # doesn't pull the missed update
    with pytest.raises(ValueError, match="readback semantics"):
        driver.faults.set_readback_semantics("psychic")


def test_power_cut_before_apply_loses_the_in_flight_update():
    driver, clock = make(SimSettings.dev().with_(readback_semantics="device"))

    async def main():
        await driver.write(KIT, [brightness(10)], purpose=ENGINE)
        driver.faults.set_available(KIT, False)  # within 0.3 s of the ack: not applied yet
        driver.faults.set_available(KIT, True)
        await clock.sleep(5)
        return await driver.read_state(KIT)

    r = run_virtual(main)
    assert r.get("brightness") == 100 and r.available is True
    assert rows_by_key(driver.cloud_view(KIT))[("brightness", None)] == 10


def test_device_applies_between_0_3_and_1_5_s_after_the_ack():
    driver, clock = make(seed=7)

    async def main():
        out = []
        for level in (20, 30, 40, 50, 60):
            await driver.write(KIT, [brightness(level)], purpose=ENGINE)
            await clock.sleep(0.29)
            early = rows_by_key(driver.device_view(KIT))[("brightness", None)]
            await clock.sleep(1.22)
            late = rows_by_key(driver.device_view(KIT))[("brightness", None)]
            out.append((level, early, late))
        return out

    for level, early, late in run_virtual(main):
        assert early != level and late == level


def test_state_is_unchanged_until_applied_and_animation_origin_is_recorded():
    driver, clock = make()

    async def main():
        await driver.write(KIT, full_state_vector(Look("preset", 70, preset="snowfall")), purpose=ENGINE)
        await clock.sleep(2)
        return driver.device_debug(KIT), clock.now()

    debug, now = run_virtual(main)
    assert debug["pending_updates"] == 0 and debug["applied_at"] <= now
    assert 0 <= debug["animation_origin"] - debug["applied_at"] <= 1.5


# ---- transport faults ---------------------------------------------------------------------------------------------
def test_outage_500_window_and_token_expiry_outcomes():
    driver, clock = make()

    async def main():
        out = {}
        driver.faults.set_outage(True)
        out["outage_w"] = await driver.write(KIT, [brightness(50)], purpose=ENGINE)
        out["outage_r"] = await driver.read_state(KIT)
        driver.faults.set_outage(False)
        driver.faults.start_500_window(60)
        out["500_w"] = await driver.write(KIT, [brightness(51)], purpose=ENGINE)
        out["500_r"] = await driver.read_state(KIT)
        await clock.sleep(61)
        out["after_500"] = await driver.write(KIT, [brightness(52)], purpose=ENGINE)
        driver.faults.expire_token("canopy")
        out["auth_w"] = await driver.write(MODULE, [brightness(53)], purpose=ENGINE)
        out["auth_r"] = await driver.read_state(MODULE)
        out["other_account"] = await driver.write(KIT, [brightness(54)], purpose=ENGINE)
        out["health"] = driver.health()
        driver.faults.restore_token("canopy")
        out["restored"] = await driver.write(MODULE, [brightness(55)], purpose=ENGINE)
        out["unknown"] = await driver.write("sim-nope", [brightness(55)], purpose=ENGINE)
        return out

    out = run_virtual(main)
    assert out["outage_w"].outcome is Outcome.CONNECTION and out["outage_w"].http_status is None
    assert out["outage_w"].acked_at is None and out["outage_w"].elapsed_s == pytest.approx(3.0)
    assert out["outage_r"].outcome is Outcome.CONNECTION
    assert out["500_w"].outcome is Outcome.SERVER_ERROR and out["500_w"].http_status == 500
    assert out["500_r"].outcome is Outcome.SERVER_ERROR
    assert out["after_500"].ok
    assert out["auth_w"].outcome is Outcome.AUTH and out["auth_w"].http_status == 401
    assert out["auth_r"].outcome is Outcome.AUTH
    assert out["other_account"].ok
    assert "expired" in out["health"]["accounts"]["canopy"]["token"]
    assert out["health"]["accounts"]["bench"]["token"] == "ok"
    assert out["restored"].ok
    assert out["unknown"].outcome is Outcome.NOT_FOUND and out["unknown"].http_status == 404
    cloud = rows_by_key(driver.cloud_view(KIT))
    assert cloud[("brightness", None)] == 54  # only the successful writes landed
    s = driver.stats()
    assert s["connection_failures"] == 2 and s["http_500"] == 2 and s["auth_failures"] == 2
    json.dumps(s)


def test_scheduled_500_window_from_settings():
    driver, clock = make(SimSettings.dev().with_(http_500_windows=((10.0, 5.0),)))

    async def main():
        a = await driver.write(KIT, [brightness(20)], purpose=ENGINE)
        await clock.sleep(10)
        b = await driver.write(KIT, [brightness(21)], purpose=ENGINE)
        await clock.sleep(5)
        c = await driver.write(KIT, [brightness(22)], purpose=ENGINE)
        return a, b, c

    a, b, c = run_virtual(main)
    assert a.ok and b.outcome is Outcome.SERVER_ERROR and c.ok


def test_silent_drop_answers_200_but_stores_nothing():
    driver, _ = make(SimSettings.dev().with_(silent_drop_rate=1.0))

    async def main():
        w = await driver.write(KIT, [brightness(12)], purpose=ENGINE)
        return w, await driver.read_state(KIT)

    w, r = run_virtual(main)
    assert w.ok and w.http_status == 200
    assert compare([brightness(12)], r.rows) != []
    assert driver.stats()["silent_drops"] == 1


def test_read_silent_degradation_drops_a_row_from_a_200():
    driver, _ = make()
    driver.faults.set_read_row_drop_rate(1.0)

    async def main():
        full = len(driver._readback_rows(driver.cloud.device(KIT)))
        r = await driver.read_state(KIT)
        return full, r

    full, r = run_virtual(main)
    assert r.ok and len(r.rows) == full - 1
    with pytest.raises(ValueError):
        driver.faults.set_throttle_rate(1.5)


def test_acceptance_rates_and_latency_over_many_writes():
    driver, _ = make(SimSettings.from_acceptance(), seed=1234)
    n = 4000
    select = slot_select("custom-1")

    async def main():
        return [await driver.write(MODULE, select, purpose=ENGINE) for _ in range(n)]

    results = run_virtual(main)
    s = driver.stats()
    throttled = sum(r.outcome is Outcome.THROTTLED for r in results)
    assert throttled == s["write_429"]
    assert 0.04 <= throttled / n <= 0.06  # 5% of PUTs → 429
    assert 0.005 <= s["silent_drops"] / n <= 0.016  # ~1% silent drops
    assert all(r.outcome in (Outcome.OK, Outcome.THROTTLED) for r in results)
    elapsed = [r.elapsed_s for r in results]
    assert min(elapsed) >= 0.15 - 1e-9 and max(elapsed) <= 0.8 + 1e-9  # PUT ack 150–800 ms
    assert 0.28 <= s["ack_s"]["p50"] <= 0.42  # lognormal, median ≈ √(150·800) ms
    assert s["ack_s"]["p95"] < 0.8 and s["ack_s"]["n"] == n
    assert s["min_interval_violations_total"] == 0  # not enforced unless configured


def test_same_seed_same_world_different_seed_different_world():
    def run(seed):
        driver, _ = make(SimSettings.from_acceptance(), seed=seed)

        async def main():
            return [
                (r.outcome, round(r.elapsed_s, 6))
                for r in [await driver.write(KIT, [brightness(50)], purpose=ENGINE) for _ in range(200)]
            ]

        return run_virtual(main)

    assert run(5) == run(5)
    assert run(5) != run(6)


def test_per_device_min_interval_is_enforced_and_counted():
    driver, clock = make(SimSettings.dev().with_(per_device_min_interval_s=1.0))

    async def main():
        first = await driver.write(KIT, [brightness(20)], purpose=ENGINE)
        too_soon = await driver.write(KIT, [brightness(21)], purpose=ENGINE)  # sent ~0.35 s later
        other = await driver.write(MODULE, [brightness(22)], purpose=ENGINE)  # other device: its own budget
        await clock.sleep(1.0)
        spaced = await driver.write(KIT, [brightness(23)], purpose=ENGINE)
        # Two concurrent writes to one device: the second is a violation.
        pair = await asyncio.gather(
            driver.write(MODULE, [brightness(24)], purpose=ENGINE),
            driver.write(MODULE, [brightness(25)], purpose=ENGINE),
        )
        return first, too_soon, other, spaced, pair

    first, too_soon, other, spaced, pair = run_virtual(main)
    assert first.ok and other.ok and spaced.ok
    assert too_soon.outcome is Outcome.THROTTLED and too_soon.http_status == 429
    assert "within 1 s" in too_soon.error
    assert sorted(r.outcome for r in pair) == sorted([Outcome.OK, Outcome.THROTTLED])
    s = driver.stats()
    assert s["min_interval_violations"] == {KIT: 1, MODULE: 1}
    assert s["min_interval_violations_total"] == 2
    assert rows_by_key(driver.cloud_view(KIT))[("brightness", None)] == 23


# ---- settings -------------------------------------------------------------------------------------------------
def test_acceptance_settings_map_the_pinned_file_exactly():
    s = SimSettings.from_acceptance()
    assert (s.ack_ms_min, s.ack_ms_max, s.ack_distribution) == (150, 800, "lognormal")
    assert (s.device_apply_extra_s_min, s.device_apply_extra_s_max) == (0.3, 1.5)
    assert s.start_skew_max_s == 1.5
    assert s.throttle_429_rate == 0.05 and s.silent_drop_rate == 0.01
    assert s.http_500_window_s == 43 * 60 and s.internet_outage_s == 30 * 60
    assert s.readback_semantics == "cloud" and s.wrong_length_policy == "reject"
    assert s.per_device_min_interval_s is None
    dev = SimSettings.dev()
    assert dev.throttle_429_rate == dev.silent_drop_rate == 0 and not dev.http_500_windows
    assert (dev.ack_ms_min, dev.ack_ms_max) == (150, 800)
    json.dumps(s.to_json())


def test_bad_settings_are_refused_in_plain_english(tmp_path):
    with pytest.raises(ValueError, match="throttle_429_rate must be between 0 and 1"):
        SimSettings(throttle_429_rate=2)
    with pytest.raises(ValueError, match="readback_semantics"):
        SimSettings(readback_semantics="psychic")
    with pytest.raises(ConfigError, match="missing"):
        SimSettings.from_acceptance(tmp_path / "nope.yaml")
    bad = tmp_path / "acceptance.yaml"
    bad.write_text("latency: {}\nfaults: {}\n")
    with pytest.raises(ConfigError, match="pinned"):
        SimSettings.from_acceptance(bad)
    bad.write_text("latency: [\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        SimSettings.from_acceptance(bad)


def test_color_temperature_row_from_rows_module_is_accepted():
    driver, _ = make()

    async def main():
        return await driver.write(KIT, [color_mode("white"), color_temperature(6500)], purpose=ENGINE)

    assert run_virtual(main).ok
