from datetime import UTC, datetime

import pytest

from conductor.gate import (
    GateConfigError,
    GateDenied,
    GateSettings,
    LiveGate,
    Purpose,
    RuntimeMode,
    metadevice_id_from_path,
)

BENCH_ID = "0f00-bench-kit"


class FixedClock:
    def __init__(self, when: datetime):
        self.t = when.timestamp()

    def now(self):
        return self.t

    def monotonic(self):
        return self.t

    async def sleep(self, s):
        return None


def gate(env: dict, writable=(BENCH_ID,), when=datetime(2026, 10, 1, tzinfo=UTC)) -> LiveGate:
    return LiveGate(GateSettings.from_env(env), lambda: writable, FixedClock(when))


def test_defaults_are_sim_and_offline():
    s = GateSettings.from_env({})
    assert s.mode is RuntimeMode.SIM and not s.live and s.engine_driver == "sim"
    g = LiveGate(s)
    for p in Purpose:
        assert not g.purpose_allowed(p)[0]


@pytest.mark.parametrize("mode", ["bench", "live"])
def test_bench_and_live_refuse_to_start_without_live_flag(mode):
    with pytest.raises(GateConfigError, match="HUBSPACE_LIVE=1"):
        GateSettings.from_env({"CONDUCTOR_MODE": mode})


def test_unknown_mode_and_bad_date_are_plain_errors():
    with pytest.raises(GateConfigError, match="sim, bench or live"):
        GateSettings.from_env({"CONDUCTOR_MODE": "show"})
    with pytest.raises(GateConfigError, match="YYYY-MM-DD"):
        GateSettings.from_env({"HUBSPACE_LIVE_UNTIL": "Oct 19"})


def test_registry_allowlist_only_on_live():
    with pytest.raises(GateConfigError, match="venue box"):
        GateSettings.from_env(
            {"CONDUCTOR_MODE": "bench", "HUBSPACE_LIVE": "1", "HUBSPACE_DEVICE_ALLOWLIST": "registry"}
        )


# The truth table in 07 §1.
@pytest.mark.parametrize(
    ("mode", "live", "engine", "bench", "account"),
    [
        ("sim", "", False, False, False),
        ("sim", "1", False, True, False),
        ("bench", "1", False, True, True),
        ("live", "1", True, True, True),
    ],
)
def test_truth_table(mode, live, engine, bench, account):
    g = gate({"CONDUCTOR_MODE": mode, "HUBSPACE_LIVE": live})
    assert g.purpose_allowed(Purpose.ENGINE)[0] is engine
    assert g.purpose_allowed(Purpose.BENCH)[0] is bench
    assert g.purpose_allowed(Purpose.ACCOUNT)[0] is account
    assert g.settings.engine_driver == ("afero" if mode == "live" else "sim")


def test_empty_allowlist_denies_every_write():
    g = gate({"CONDUCTOR_MODE": "bench", "HUBSPACE_LIVE": "1"})
    with pytest.raises(GateDenied, match="allowlist"):
        g.check_write(BENCH_ID, Purpose.BENCH)


def test_effective_allowlist_is_env_intersect_registry_writable():
    env = {"CONDUCTOR_MODE": "bench", "HUBSPACE_LIVE": "1", "HUBSPACE_DEVICE_ALLOWLIST": f"{BENCH_ID},other"}
    g = gate(env, writable=(BENCH_ID, "not-in-env"))
    assert g.effective_allowlist() == {BENCH_ID}
    g.check_write(BENCH_ID, Purpose.BENCH)
    for denied in ("other", "not-in-env", ""):
        with pytest.raises(GateDenied):
            g.check_write(denied, Purpose.BENCH)


def test_registry_keyword_means_all_writable_on_live():
    env = {"CONDUCTOR_MODE": "live", "HUBSPACE_LIVE": "1", "HUBSPACE_DEVICE_ALLOWLIST": "registry"}
    g = gate(env, writable=("a", "b"))
    assert g.effective_allowlist() == {"a", "b"}


def test_live_until_blocks_writes_after_the_date():
    env = {
        "CONDUCTOR_MODE": "bench",
        "HUBSPACE_LIVE": "1",
        "HUBSPACE_DEVICE_ALLOWLIST": BENCH_ID,
        "HUBSPACE_LIVE_UNTIL": "2026-10-19",
    }
    gate(env, when=datetime(2026, 10, 19, 23, 0, tzinfo=UTC)).check_write(BENCH_ID, Purpose.BENCH)
    with pytest.raises(GateDenied, match="2026-10-19"):
        gate(env, when=datetime(2026, 10, 20, 0, 1, tzinfo=UTC)).check_write(BENCH_ID, Purpose.BENCH)


def test_account_allowlist_is_checked_before_any_password():
    env = {
        "CONDUCTOR_MODE": "bench",
        "HUBSPACE_LIVE": "1",
        "HUBSPACE_ACCOUNT_ALLOWLIST": "Bench@Example.invalid",
    }
    g = gate(env)
    g.check_account("bench@example.invalid ")
    with pytest.raises(GateDenied, match="no password was sent"):
        g.check_account("canopy@example.invalid")
    with pytest.raises(GateDenied):
        gate({"HUBSPACE_LIVE": "1"}).check_account("bench@example.invalid")  # sim mode: no account connect


def test_check_request_rules():
    env = {"CONDUCTOR_MODE": "bench", "HUBSPACE_LIVE": "1", "HUBSPACE_DEVICE_ALLOWLIST": BENCH_ID}
    g = gate(env)
    put = f"/v1/accounts/acc/metadevices/{BENCH_ID}/state"
    g.check_request("PUT", "api2.afero.net", put, Purpose.BENCH)
    g.check_request("GET", "api2.afero.net", "/v1/accounts/acc/metadevices/anything/state", Purpose.BENCH)
    g.check_request("POST", "accounts.hubspaceconnect.com", "/auth/realms/thd/token", Purpose.ACCOUNT)
    g.check_request("POST", "api2.afero.net", "/v1/accounts/acc/conclaveAccess", Purpose.ACCOUNT)
    with pytest.raises(GateDenied):
        g.check_request("PUT", "api2.afero.net", "/v1/accounts/acc/metadevices/other/state", Purpose.BENCH)
    with pytest.raises(GateDenied):  # the raw attribute path names no metadevice → always refused
        g.check_request(
            "POST", "api2.afero.net", f"/v1/accounts/acc/devices/{BENCH_ID}/actions", Purpose.BENCH
        )
    with pytest.raises(GateDenied):  # bench mode never lets the engine out, even for reads
        g.check_request("GET", "api2.afero.net", put, Purpose.ENGINE)


def test_metadevice_id_from_path():
    assert metadevice_id_from_path("/v1/accounts/a/metadevices/xyz/state?x=1") == "xyz"
    assert metadevice_id_from_path("/v1/accounts/a/metadevices/xyz") == "xyz"
    assert metadevice_id_from_path("/v1/accounts/a/metadevices") == ""
    assert metadevice_id_from_path("/v1/users/me") == ""
