"""The Afero adapter against a fake transport (07 §3–§5): wire shapes, stamps, classification, tokens, outages."""

import asyncio
import json

import aiohttp
import pytest
from aioafero.v1 import v1_const
from fake_afero import (
    BENCH_MD,
    FIXTURE,
    HANG,
    SPARE_MD,
    STRANGER_MD,
    FakeResponse,
    make_world,
    query,
)

from conductor import rows as R
from conductor.capabilities import detect
from conductor.clock import run_virtual
from conductor.drivers.afero import AferoRequestFailed, parse_state_body
from conductor.drivers.base import DeviceDriver, Outcome, StateRow
from conductor.gate import Purpose
from conductor.rows import payload_bytes

STATE_PATH = f"/metadevices/{BENCH_MD}/state"


def run(main):
    return run_virtual(main)


def select_rows() -> list[StateRow]:
    return [*R.slot_select("custom-1"), R.power(True)]


def puts(world):
    return [r for r in world.fake.requests if r.method == "PUT"]


def test_driver_satisfies_the_protocol(tmp_path):
    async def main():
        w = await make_world(tmp_path, connect=False)
        assert isinstance(w.driver, DeviceDriver)
        assert w.driver.name == "afero" and w.driver.simulated is False
        await w.driver.close()

    run(main)


# ---- wire shape --------------------------------------------------------------------------------------------------
def test_write_is_one_put_with_epoch_ms_stamps_and_the_data_host_header(tmp_path):
    async def main():
        w = await make_world(tmp_path)
        await asyncio.sleep(12.3456)  # virtual time: the stamp must come from the injected clock
        expected_ms = round(w.clock.now() * 1000)
        rows = select_rows()
        result = await w.driver.write(BENCH_MD, rows, purpose=Purpose.ENGINE)
        await w.driver.close()
        return w, rows, result, expected_ms

    w, rows, result, expected_ms = run(main)
    assert result.outcome is Outcome.OK and result.http_status == 200
    assert result.sent_at == pytest.approx(expected_ms / 1000)
    [put] = puts(w)
    assert (
        str(put.url) == f"https://api2.afero.net/v1/accounts/{w.fake.account_id}/metadevices/{BENCH_MD}/state"
    )
    assert put.header_host == "semantics2.afero.net"
    assert put.headers["content-type"] == "application/json; charset=utf-8"
    assert put.headers["authorization"].startswith("Bearer ") and put.headers["authorization"][7:] in (
        w.fake.valid_bearer
    )
    assert put.json == {"metadeviceId": BENCH_MD, "values": [r.to_wire(expected_ms) for r in rows]}
    stamps = {v["lastUpdateTime"] for v in put.json["values"]}
    assert stamps == {expected_ms} and expected_ms > 10**12  # milliseconds, not seconds (aioafero #65)
    assert result.bytes_sent == payload_bytes(BENCH_MD, rows, expected_ms)
    assert result.echoed == rows
    assert put.allow_redirects is False


def test_max_retries_is_forced_to_one_before_every_send(tmp_path):
    async def main():
        w = await make_world(tmp_path)
        v1_const.MAX_RETRIES = 3  # something else put it back
        w.fake.script("PUT", STATE_PATH, FakeResponse(429), FakeResponse(429), FakeResponse(429))
        result = await w.driver.write(BENCH_MD, select_rows(), purpose=Purpose.ENGINE)
        await w.driver.close()
        return w, result

    w, result = run(main)
    assert v1_const.MAX_RETRIES == 1
    assert result.outcome is Outcome.THROTTLED
    assert len(puts(w)) == 1  # aioafero didn't retry on its own; the executor owns retries


# ---- classification ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("scripted", "outcome", "status"),
    [
        (FakeResponse(200, {"metadeviceId": BENCH_MD, "values": []}), Outcome.OK, 200),
        (FakeResponse(204, b""), Outcome.OK, 204),
        (FakeResponse(400, {"error": "bad"}), Outcome.CLIENT_ERROR, 400),
        (FakeResponse(401, {"error": "unauthorized"}), Outcome.AUTH, 401),
        (FakeResponse(403, {"error": "forbidden"}), Outcome.THROTTLED, None),  # aioafero raises HTTPForbidden
        (FakeResponse(404, {"error": "nope"}), Outcome.NOT_FOUND, 404),
        (FakeResponse(409, {"error": "conflict"}), Outcome.CLIENT_ERROR, 409),
        (FakeResponse(429), Outcome.THROTTLED, None),  # ExceededMaximumRetries
        (FakeResponse(500, "oops"), Outcome.SERVER_ERROR, 500),
        (FakeResponse(502, "bad gateway"), Outcome.SERVER_ERROR, 502),
        (FakeResponse(503), Outcome.THROTTLED, None),
        (FakeResponse(504), Outcome.THROTTLED, None),
        (TimeoutError(), Outcome.TIMEOUT, None),
        (HANG, Outcome.TIMEOUT, None),  # the adapter's own 8 s wait_for
        (aiohttp.ClientConnectionError("reset"), Outcome.CONNECTION, None),
        (aiohttp.ServerDisconnectedError(), Outcome.CONNECTION, None),
        (ConnectionResetError(), Outcome.CONNECTION, None),
    ],
)
def test_every_write_outcome_is_classified_and_nothing_raises(tmp_path, scripted, outcome, status):
    async def main():
        w = await make_world(tmp_path)
        w.fake.script("PUT", STATE_PATH, scripted)
        start = w.clock.monotonic()
        result = await w.driver.write(BENCH_MD, select_rows(), purpose=Purpose.ENGINE)
        took = w.clock.monotonic() - start
        await w.driver.close()
        return w, result, took

    w, result, took = run(main)
    assert result.outcome is outcome, result.error
    assert result.http_status == status
    assert len(puts(w)) == 1
    if outcome is not Outcome.OK:
        assert result.error and "Bearer" not in result.error
    if scripted is HANG:
        assert took == pytest.approx(8.0, abs=0.01)


def test_a_401_forces_a_refresh_next_time_but_is_not_a_dead_token(tmp_path):
    async def main():
        w = await make_world(tmp_path)
        w.fake.script("PUT", STATE_PATH, FakeResponse(401))
        first = await w.driver.write(BENCH_MD, select_rows(), purpose=Purpose.ENGINE)
        refreshes_before = w.fake.count("POST", "/token")
        second = await w.driver.write(BENCH_MD, select_rows(), purpose=Purpose.ENGINE)
        refreshes = w.fake.count("POST", "/token") - refreshes_before
        status = w.driver.account_status("bench")
        await w.driver.close()
        return first, second, refreshes, status

    first, second, refreshes, status = run(main)
    assert first.outcome is Outcome.AUTH and second.outcome is Outcome.OK
    assert refreshes == 1 and not status.needs_reauth


def test_a_dead_refresh_token_marks_needs_reauth_and_stops_using_the_account(tmp_path):
    async def main():
        w = await make_world(tmp_path)
        w.fake.valid_refresh.clear()  # e.g. "sign out of all other devices" in the app
        w.fake.valid_bearer.clear()
        w.wall[0] += 3600  # the bearer is past its expiry → aioafero refreshes → invalid_grant
        bridge = w.driver._links["bench"].bridge
        bridge.set_token_data(bridge.token_data._replace(expiration=0))
        first = await w.driver.write(BENCH_MD, select_rows(), purpose=Purpose.ENGINE)
        sent = len(w.fake.requests)
        second = await w.driver.write(BENCH_MD, select_rows(), purpose=Purpose.ENGINE)
        read = await w.driver.read_state(BENCH_MD)
        after = len(w.fake.requests)
        await asyncio.sleep(120)  # nothing retries a login in the background
        later = len(w.fake.requests)
        status = w.driver.account_status("bench")
        await w.driver.close()
        return first, second, read, sent, after, later, status

    first, second, read, sent, after, later, status = run(main)
    assert first.outcome is Outcome.AUTH and "re-sign-in" in first.error
    assert second.outcome is Outcome.AUTH and read.outcome is Outcome.AUTH
    assert sent == after == later
    assert status.needs_reauth and not status.connected


def test_unregistered_ids_are_not_found_without_a_request(tmp_path):
    async def main():
        w = await make_world(tmp_path)
        before = len(w.fake.requests)
        write = await w.driver.write(STRANGER_MD, select_rows(), purpose=Purpose.ENGINE)
        read = await w.driver.read_state("sim-bench-kit")
        with pytest.raises(LookupError):
            w.driver.account_of(STRANGER_MD)
        with pytest.raises(LookupError):
            await w.driver.fetch_profile(STRANGER_MD)
        after = len(w.fake.requests)
        await w.driver.close()
        return write, read, before, after, w.driver.account_of(BENCH_MD)

    write, read, before, after, account = run(main)
    assert write.outcome is Outcome.NOT_FOUND and read.outcome is Outcome.NOT_FOUND
    assert before == after and account == "bench"


# ---- readback ------------------------------------------------------------------------------------------------------
GOOD_ROW = {"functionClass": "power", "functionInstance": None, "value": "on", "lastUpdateTime": 1}


@pytest.mark.parametrize(
    ("body", "outcome", "n_rows"),
    [
        ({"metadeviceId": BENCH_MD, "values": [GOOD_ROW]}, Outcome.OK, 1),
        ([GOOD_ROW], Outcome.OK, 1),  # a bare list
        ([], Outcome.OK, 0),  # [] on a 200 is OK with no rows; compare() calls every expected row missing
        (
            {"metadeviceId": BENCH_MD, "values": [GOOD_ROW, {"value": 1}, "junk", {"functionClass": ""}]},
            Outcome.OK,
            1,
        ),
        (b"", Outcome.MALFORMED, 0),
        ("<html>busy</html>", Outcome.MALFORMED, 0),
        ({"metadeviceId": BENCH_MD}, Outcome.MALFORMED, 0),
        ({"values": "nope"}, Outcome.MALFORMED, 0),
        ({"metadeviceId": SPARE_MD, "values": [GOOD_ROW]}, Outcome.MALFORMED, 0),
        (42, Outcome.MALFORMED, 0),
    ],
)
def test_readback_shape_is_validated(tmp_path, body, outcome, n_rows):
    raw = json.dumps(body).encode() if isinstance(body, (int, dict, list)) else body

    async def main():
        w = await make_world(tmp_path)
        w.fake.script("GET", STATE_PATH, FakeResponse(200, raw, content_type="application/json"))
        result = await w.driver.read_state(BENCH_MD)
        health = w.driver.health()
        await w.driver.close()
        return result, health

    result, health = run(main)
    assert result.outcome is outcome, result.error
    assert len(result.rows) == n_rows
    if n_rows and len(body.get("values", body) if isinstance(body, dict) else body) > n_rows:
        assert health["rows_skipped"] == 3


def test_readback_statuses_are_classified_too(tmp_path):
    async def main():
        w = await make_world(tmp_path)
        w.fake.script("GET", STATE_PATH, FakeResponse(403), FakeResponse(500, "x"), FakeResponse(404))
        out = [(await w.driver.read_state(BENCH_MD)).outcome for _ in range(3)]
        ok = await w.driver.read_state(BENCH_MD)
        await w.driver.close()
        return out, ok

    out, ok = run(main)
    assert out == [Outcome.THROTTLED, Outcome.SERVER_ERROR, Outcome.NOT_FOUND]
    assert ok.ok and ok.get("power") is not None and len(ok.rows) == 25


def test_parse_state_body_counts_skipped_rows():
    rows, skipped, error = parse_state_body(json.dumps([GOOD_ROW, {"x": 1}]).encode(), BENCH_MD)
    assert rows == [StateRow("power", None, "on")] and skipped == 1 and error is None


# ---- profile + discovery -------------------------------------------------------------------------------------------
def test_fetch_profile_parses_the_raw_api_shape(tmp_path):
    fixture = json.loads(FIXTURE.read_text())

    async def main():
        w = await make_world(tmp_path)
        info = await w.driver.fetch_profile(BENCH_MD, purpose=Purpose.BENCH)
        get = [
            r for r in w.fake.requests if r.method == "GET" and r.path.endswith(f"/metadevices/{BENCH_MD}")
        ]
        await w.driver.close()
        return info, get

    info, [get] = run(main)
    assert get.header_host == "semantics2.afero.net"
    assert query(get.url)["expansions"] == ["state,capabilities,semantics"]
    assert info.device_id == BENCH_MD and info.account == "bench"
    assert info.model == fixture["model"] and info.name == fixture["friendly_name"]
    assert info.device_class == "light"
    assert info.functions == fixture["functions"]
    assert [s.to_json() for s in info.states] == [StateRow.from_wire(s).to_json() for s in fixture["states"]]
    assert info.raw["id"] == BENCH_MD and not info.simulated
    caps = detect(info.functions, info.states)
    assert caps.segment_hint == 20 and caps.slots == ["custom-1", "custom-2", "custom-3"]


def test_fetch_profile_failures(tmp_path):
    async def main():
        w = await make_world(tmp_path)
        del w.fake.devices[BENCH_MD]
        with pytest.raises(LookupError):
            await w.driver.fetch_profile(BENCH_MD)
        w.fake.script("GET", f"/metadevices/{SPARE_MD}", FakeResponse(503))
        with pytest.raises(AferoRequestFailed) as exc:
            await w.driver.fetch_profile(SPARE_MD)
        await w.driver.close()
        return exc.value

    err = run(main)
    assert err.outcome is Outcome.THROTTLED


def test_discover_lists_devices_and_flags_unregistered_ones(tmp_path):
    async def main():
        w = await make_world(tmp_path)
        w.fake.devices["room-1"] = {"id": "room-1", "typeId": "metadevice.room"}
        infos = await w.driver.discover()
        only = await w.driver.discover("bench")
        status = w.driver.account_status("bench")
        with pytest.raises(LookupError):
            await w.driver.discover("nobody")
        events = w.telemetry.recent(kind="alert")
        await w.driver.close()
        return infos, only, status, events

    infos, only, status, events = run(main)
    assert sorted(i.device_id for i in infos) == [BENCH_MD, SPARE_MD, STRANGER_MD]
    assert len(only) == 3
    assert status.unknown_devices == [STRANGER_MD] and status.device_count == 3
    assert any("read-only" in e["message"] for e in events)


# ---- tokens --------------------------------------------------------------------------------------------------------
def test_token_rotation_is_persisted_and_ensure_fresh_token_forces_a_refresh(tmp_path):
    async def main():
        w = await make_world(tmp_path, connect=False)
        seeded = w.store.status("bench").generation
        await w.driver.start()  # returns at once; connects in the background
        immediately = len(w.fake.requests)
        await asyncio.sleep(1)
        connected = w.driver.account_status("bench")
        after_connect = (w.store.status("bench").generation, w.store.load("bench"))
        rotated_ok = after_connect[1] in w.fake.valid_refresh

        fresh = await w.driver.ensure_fresh_token("bench")  # ~116 s left: nothing to do
        no_calls = w.fake.count("POST", "/token")

        w.wall[0] += 110  # aioafero's clock: 6 s left < 15 s
        forced = await w.driver.ensure_fresh_token("bench")
        after_force = (w.store.status("bench").generation, w.store.load("bench"))
        calls = (w.fake.count("POST", "/token") - no_calls, w.fake.count("GET", "/users/me"))
        await w.driver.close()
        return seeded, immediately, connected, after_connect, rotated_ok, fresh, forced, after_force, calls, w

    seeded, immediately, connected, after_connect, rotated_ok, fresh, forced, after_force, calls, w = run(
        main
    )
    assert seeded == 1 and immediately == 0
    assert connected.connected and connected.token_generation == 2
    assert after_connect[0] == 2 and rotated_ok
    assert fresh is Outcome.OK and forced is Outcome.OK
    assert (
        after_force[0] == 3 and after_force[1] in w.fake.valid_refresh and after_force[1] != after_connect[1]
    )
    assert calls == (1, 2)  # one refresh, and users/me twice (connect + the cheap GET)
    assert any(p.name.endswith(".prev") for p in w.store.dir.iterdir())  # previous generation kept


def test_startup_never_waits_and_connect_errors_surface_in_health(tmp_path):
    async def main():
        w = await make_world(tmp_path, connect=False)
        w.fake.script("GET", "/users/me", FakeResponse(503))
        await w.driver.start()
        await asyncio.sleep(1)
        failing = w.driver.health()["accounts"]["bench"]
        write = await w.driver.write(BENCH_MD, select_rows(), purpose=Purpose.ENGINE)
        await asyncio.sleep(10)  # backoff, then the next attempt succeeds
        ok = w.driver.health()["accounts"]["bench"]
        await w.driver.close()
        return failing, write, ok

    failing, write, ok = run(main)
    assert not failing["connected"] and "throttling" in failing["last_error"]
    assert write.outcome is Outcome.CONNECTION
    assert ok["connected"] and ok["last_error"] is None


def test_open_is_capped_at_15_seconds_when_discovery_hangs(tmp_path):
    async def main():
        w = await make_world(tmp_path, connect=False)
        w.fake.script("GET", "/metadevices", HANG)
        start = w.clock.monotonic()
        connected = await w.driver.connect("bench")
        took = w.clock.monotonic() - start
        status = w.driver.account_status("bench")
        rotated = w.store.status("bench").generation  # the refresh during the failed open is still saved
        await asyncio.sleep(10)
        later = w.driver.account_status("bench").connected
        await w.driver.close()
        return connected, took, status, rotated, later

    connected, took, status, rotated, later = run(main)
    assert not connected and took == pytest.approx(15, abs=0.5)
    assert "15 s" in status.last_error
    assert rotated == 2
    assert later  # retried in the background


def test_no_stored_token_means_auth_without_requests(tmp_path):
    async def main():
        w = await make_world(tmp_path, seed_token=False, connect=False)
        await w.driver.start()
        await asyncio.sleep(1)
        result = await w.driver.write(BENCH_MD, select_rows(), purpose=Purpose.ENGINE)
        await w.driver.close()
        return w, result

    w, result = run(main)
    assert result.outcome is Outcome.AUTH and "/ops" in result.error
    assert w.fake.requests == []


# ---- outages -------------------------------------------------------------------------------------------------------
def test_outage_marks_degraded_and_recovery_notifies_subscribers(tmp_path):
    async def main():
        w = await make_world(tmp_path)
        recovered: list[str] = []
        w.driver.on_recovery.append(recovered.append)

        async def async_sub(account):
            recovered.append(f"async:{account}")

        w.driver.on_recovery.append(async_sub)
        down = aiohttp.ClientConnectionError("down")
        w.fake.script("PUT", STATE_PATH, down, down)
        w.fake.script("GET", "/users/me", down)  # the first watchdog probe fails too
        one = await w.driver.write(BENCH_MD, select_rows(), purpose=Purpose.ENGINE)
        after_one = w.driver.health()["accounts"]["bench"]["degraded"]
        await w.driver.write(BENCH_MD, select_rows(), purpose=Purpose.ENGINE)
        degraded = w.driver.health()["accounts"]["bench"]
        await asyncio.sleep(10.5)
        still = w.driver.health()["accounts"]["bench"]["degraded"]
        await asyncio.sleep(10)
        healthy = w.driver.health()["accounts"]["bench"]["degraded"]
        await asyncio.sleep(0)
        await w.driver.close()
        return one, after_one, degraded, still, healthy, recovered

    one, after_one, degraded, still, healthy, recovered = run(main)
    assert one.outcome is Outcome.CONNECTION and not after_one
    assert degraded["degraded"] and degraded["consecutive_failures"] == 2
    assert still and not healthy
    assert recovered == ["bench", "async:bench"]


def test_health_is_json_and_has_no_secrets(tmp_path):
    async def main():
        w = await make_world(tmp_path)
        await w.driver.write(BENCH_MD, select_rows(), purpose=Purpose.ENGINE)
        health = w.driver.health()
        statuses = [w.driver.account_status(a).to_json() for a in w.driver.accounts()]
        await w.driver.close()
        return w, json.dumps({"h": health, "s": statuses})

    w, text = run(main)
    for secret in [*w.fake.issued, w.fake.account_id]:
        assert secret not in text
