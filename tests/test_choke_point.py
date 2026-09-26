"""The choke point (07 §1): nothing reaches Afero unless the live gate allows it — proven on the wire."""

import asyncio
import logging
from datetime import date

import pytest
from fake_afero import BENCH_EMAIL, BENCH_MD, SPARE_MD, FakeAfero, make_gate, make_registry, make_world

from conductor import rows as R
from conductor.accounts_api import LoginState
from conductor.clock import VirtualClock, run_virtual
from conductor.drivers.afero import AferoAccounts
from conductor.drivers.afero_transport import (
    GatedClientSession,
    current_purpose,
    loggable_path,
    purpose_scope,
)
from conductor.drivers.base import Outcome
from conductor.gate import GateDenied, Purpose, RuntimeMode

API = "https://api2.afero.net"
AID = "acct-fake-0001"


def state_url(md: str) -> str:
    return f"{API}/v1/accounts/{AID}/metadevices/{md}/state"


def body(md: str) -> dict:
    return {"metadeviceId": md, "values": [r.to_wire(1) for r in R.slot_select("custom-1")]}


def session_for(fake: FakeAfero, **gate_kw) -> GatedClientSession:
    clock = VirtualClock()
    return GatedClientSession(make_gate(make_registry(), clock, **gate_kw), clock=clock, fake_transport=fake)


# ---- the session itself ------------------------------------------------------------------------------------------
def test_non_allowlisted_put_is_refused_by_the_session_and_nothing_reaches_the_wire():
    fake = FakeAfero()

    async def main():
        s = session_for(fake)
        with pytest.raises(GateDenied, match="allowlist"):
            await s.put(state_url(SPARE_MD), json=body(SPARE_MD))
        with pytest.raises(GateDenied):
            async with s.request("PUT", state_url(SPARE_MD), json=body(SPARE_MD)):
                pass
        # the same request to an allowlisted device goes out (401 here: no bearer — but it reached the fake)
        async with s.put(state_url(BENCH_MD), json=body(BENCH_MD)) as resp:
            status = resp.status
        stats = dict(s.stats)
        await s.close()
        return status, stats

    status, stats = run_virtual(main)
    assert stats["denied"] == 2 and stats["sent"] == 1
    assert [r.path for r in fake.requests] == [f"/v1/accounts/{AID}/metadevices/{BENCH_MD}/state"]
    assert status == 401


@pytest.mark.parametrize(
    ("method", "url", "headers", "match"),
    [
        ("GET", "https://example.com/v1/users/me", None, "not a Hubspace host"),
        ("GET", "https://api2.afero.net.evil.example/v1/users/me", None, "not a Hubspace host"),
        ("GET", "http://api2.afero.net/v1/users/me", None, "https"),
        ("GET", f"{API}/v1/users/me", {"host": "evil.example"}, "Host header"),
        ("POST", f"{API}/v1/accounts/{AID}/devices/{BENCH_MD}/actions", None, "No device id"),
        ("PATCH", f"{API}/v1/accounts/{AID}/metadevices", None, "No device id"),
        ("DELETE", f"{API}/v1/accounts/{AID}/metadevices/{SPARE_MD}", None, "allowlist"),
    ],
)
def test_foreign_hosts_plain_http_and_the_raw_actions_path_are_refused(method, url, headers, match):
    fake = FakeAfero()

    async def main():
        s = session_for(fake)
        with pytest.raises(GateDenied, match=match):
            async with s.request(method, url, headers=headers or {}, json={"type": "attribute_write"}):
                pass
        denied = s.stats["denied"]
        await s.close()
        return denied

    assert run_virtual(main) == 1
    assert fake.requests == []


def test_sign_in_and_conclave_hosts_are_allowed_for_account_traffic():
    fake = FakeAfero()

    async def main():
        s = session_for(fake, mode=RuntimeMode.BENCH)
        with purpose_scope(Purpose.ACCOUNT):
            async with s.post(
                "https://accounts.hubspaceconnect.com/auth/realms/thd/protocol/openid-connect/token"
            ):
                pass
            async with s.post(f"{API}/v1/accounts/{AID}/conclaveAccess", json={"user": True}):
                pass
        await s.close()

    run_virtual(main)
    assert [r.method for r in fake.requests] == ["POST", "POST"]


def test_sim_mode_without_live_denies_every_request_including_gets():
    fake = FakeAfero()

    async def main():
        s = session_for(fake, mode=RuntimeMode.SIM, live=False)
        for purpose in Purpose:
            with purpose_scope(purpose):
                for method, url in [("GET", f"{API}/v1/users/me"), ("PUT", state_url(BENCH_MD))]:
                    with pytest.raises(GateDenied, match="HUBSPACE_LIVE"):
                        async with s.request(method, url):
                            pass
        await s.close()

    run_virtual(main)
    assert fake.requests == []


def test_sim_mode_with_live_allows_bench_purpose_only():
    fake = FakeAfero()

    async def main():
        s = session_for(fake, mode=RuntimeMode.SIM, live=True)
        with pytest.raises(GateDenied, match="only bench jobs"):
            async with s.get(f"{API}/v1/users/me"):  # default purpose: ENGINE
                pass
        with purpose_scope(Purpose.BENCH):
            async with s.get(f"{API}/v1/users/me"):
                pass
        await s.close()

    run_virtual(main)
    assert len(fake.requests) == 1


def test_purpose_defaults_to_engine_and_scopes_nest():
    assert current_purpose.get() is Purpose.ENGINE
    with purpose_scope(Purpose.BENCH):
        assert current_purpose.get() is Purpose.BENCH
        with purpose_scope(Purpose.ACCOUNT):
            assert current_purpose.get() is Purpose.ACCOUNT
        assert current_purpose.get() is Purpose.BENCH
    assert current_purpose.get() is Purpose.ENGINE


def test_request_log_has_no_query_string_headers_or_account_id(caplog):
    fake = FakeAfero()

    async def main():
        s = session_for(fake)
        headers = {"Authorization": "Bearer secret-bearer-value", "host": "semantics2.afero.net"}
        async with s.get(
            f"{API}/v1/accounts/{AID}/metadevices", params={"expansions": "state"}, headers=headers
        ):
            pass
        await s.close()

    with caplog.at_level(logging.DEBUG, logger="conductor.drivers.afero_transport"):
        run_virtual(main)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "GET api2.afero.net/v1/accounts/{aid}/metadevices -> 401" in text
    assert "expansions" not in text and "Bearer" not in text and AID not in text
    assert (
        loggable_path("/v1/accounts/abc/metadevices/x/state?y=1") == "/v1/accounts/{aid}/metadevices/x/state"
    )


# ---- through the driver (every writer path ends here) --------------------------------------------------------------
def test_driver_write_to_non_allowlisted_device_is_denied_with_zero_requests(tmp_path):
    async def main():
        w = await make_world(tmp_path)
        w.wall[0] += 3600  # even with an expired bearer, a refused write must not trigger a refresh
        before = len(w.fake.requests)
        results = [
            await w.driver.write(SPARE_MD, R.slot_select("custom-1"), purpose=purpose)
            for purpose in (Purpose.ENGINE, Purpose.BENCH)
        ]
        after = len(w.fake.requests)
        ok = await w.driver.write(BENCH_MD, R.slot_select("custom-1"), purpose=Purpose.ENGINE)
        events = w.telemetry.recent(kind="gate_denied")
        await w.driver.close()
        return before, after, results, ok, events

    before, after, results, ok, events = run_virtual(main)
    assert before == after
    assert all(r.outcome is Outcome.DENIED and r.bytes_sent == 0 and "allowlist" in r.error for r in results)
    assert ok.outcome is Outcome.OK
    assert [e["device_id"] for e in events] == [SPARE_MD, SPARE_MD]


def test_driver_in_sim_mode_without_live_makes_no_requests_at_all(tmp_path):
    async def main():
        w = await make_world(tmp_path, connect=False, mode=RuntimeMode.SIM, live=False)
        await w.driver.start()
        await asyncio.sleep(60)
        write = await w.driver.write(BENCH_MD, R.slot_select("custom-1"), purpose=Purpose.BENCH)
        read = await w.driver.read_state(BENCH_MD, purpose=Purpose.BENCH)
        with pytest.raises(GateDenied):
            await w.driver.fetch_profile(BENCH_MD)
        connected = await w.driver.connect("bench")
        login = await AferoAccounts(w.driver).start_login("bench", BENCH_EMAIL, "pw")
        health = w.driver.health()
        await w.driver.close()
        return w, write, read, connected, login, health

    w, write, read, connected, login, health = run_virtual(main)
    assert write.outcome is Outcome.DENIED and read.outcome is Outcome.DENIED
    assert not connected and login.state is LoginState.FAILED
    assert "HUBSPACE_LIVE" in health["accounts"]["bench"]["last_error"]
    assert w.fake.requests == []


def test_bench_mode_denies_the_engine_and_allows_bench_including_background_polling(tmp_path):
    async def main():
        w = await make_world(tmp_path, mode=RuntimeMode.BENCH)
        before = len(w.fake.requests)
        engine = await w.driver.write(BENCH_MD, R.slot_select("custom-1"), purpose=Purpose.ENGINE)
        engine_read = await w.driver.read_state(BENCH_MD, purpose=Purpose.ENGINE)
        mid = len(w.fake.requests)
        bench = await w.driver.write(BENCH_MD, R.slot_select("custom-1"), purpose=Purpose.BENCH)
        await asyncio.sleep(35)  # aioafero's own state poll runs under the purpose the link connected with
        polls = w.fake.count("GET", "/state")
        denied = w.driver._session.stats["denied"]
        await w.driver.close()
        return before, mid, engine, engine_read, bench, polls, denied, w

    before, mid, engine, engine_read, bench, polls, denied, w = run_virtual(main)
    assert engine.outcome is Outcome.DENIED and engine_read.outcome is Outcome.DENIED and before == mid
    assert bench.outcome is Outcome.OK
    assert w.driver.health()["purpose"] == "bench"
    assert polls >= 3 and denied == 0


def test_live_until_passed_refuses_writes_but_not_reads(tmp_path):
    async def main():
        # VirtualClock's epoch is 2026-10-23; staging's LIVE_UNTIL is 2026-10-19.
        w = await make_world(tmp_path, live_until=date(2026, 10, 19))
        before = len(w.fake.requests)
        write = await w.driver.write(BENCH_MD, R.slot_select("custom-1"), purpose=Purpose.ENGINE)
        after = len(w.fake.requests)
        read = await w.driver.read_state(BENCH_MD)
        await w.driver.close()
        return before, after, write, read

    before, after, write, read = run_virtual(main)
    assert write.outcome is Outcome.DENIED and "HUBSPACE_LIVE_UNTIL" in write.error
    assert before == after
    assert read.outcome is Outcome.OK


def test_a_write_that_slips_past_the_driver_is_still_refused_by_the_session(tmp_path):
    """Even aioafero's own write path (bridge.lights.update) can't reach a non-allowlisted device."""

    async def main():
        w = await make_world(tmp_path)
        bridge = w.driver._links["bench"].bridge
        before = len(w.fake.requests)
        with pytest.raises(GateDenied):
            await bridge.request("put", state_url(SPARE_MD), json=body(SPARE_MD))
        with pytest.raises(GateDenied):
            await bridge.lights.update(SPARE_MD, states=[{"functionClass": "power", "value": "off"}])
        after = len(w.fake.requests)
        await w.driver.close()
        return before, after

    before, after = run_virtual(main)
    assert before == after
