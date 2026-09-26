"""Roles and enrollment (07 §2, D5): one-time links, sessions that survive restarts, and the path table."""

import asyncio
import json
import stat

import pytest

from conductor.clock import VirtualClock, run_virtual
from conductor.roles import SESSION_TTL_S, RoleError, RoleStore, home_path, normalize_path, path_allowed
from conductor.roles_api import ENROLLMENT_TTL_S, OPS, Principal, Role

TESTER = Principal(Role.TESTER, "VWBD shop phone", frozenset({"bench-kit"}))
KIOSK = Principal(Role.KIOSK, "canopy kiosk")
VIGNETTE = Principal(Role.VIGNETTE, "kitchen iPad", frozenset({"kitchen"}))


# ---- enrollment ---------------------------------------------------------------------------------------------
def test_enrollment_is_single_use_and_creates_a_session(tmp_path):
    store = RoleStore(tmp_path, VirtualClock())
    token, path = store.create_enrollment("tester", "VWBD shop phone", ["bench-kit"])
    assert path == f"/enroll/{token}"
    assert store.peek_enrollment(token) == Principal(Role.TESTER, "VWBD shop phone", frozenset({"bench-kit"}))
    redeemed = store.redeem_enrollment(token)
    assert redeemed is not None
    session_id, principal = redeemed
    assert principal.role is Role.TESTER and principal.grants == {"bench-kit"}
    assert principal.session_id != session_id and len(principal.session_id) == 12  # fingerprint, not the id
    assert store.principal(session_id) == principal
    # Single use: the second attempt (and a peek) fail.
    assert store.redeem_enrollment(token) is None
    assert store.peek_enrollment(token) is None
    assert store.principal("not-a-session") is None
    assert store.principal(None) is None


def test_enrollment_and_session_expire_on_the_clock(tmp_path):
    async def main():
        store = RoleStore(tmp_path, VirtualClock())
        late, _ = store.create_enrollment("kiosk", "canopy kiosk")
        on_time, _ = store.create_enrollment("kiosk", "canopy kiosk")
        await asyncio.sleep(ENROLLMENT_TTL_S - 60)
        session_id, _ = store.redeem_enrollment(on_time)
        await asyncio.sleep(120)
        assert store.redeem_enrollment(late) is None  # 24 h + 1 min after creation
        assert store.principal(session_id) is not None
        await asyncio.sleep(SESSION_TTL_S)
        assert store.principal(session_id) is None
        assert store.list_sessions() == []

    run_virtual(main)


def test_sessions_survive_a_restart_and_only_hashes_are_stored(tmp_path):
    clock = VirtualClock()
    store = RoleStore(tmp_path, clock)
    token, _ = store.create_enrollment("tester", "VWBD shop phone", ["bench-kit"])
    pending, _ = store.create_enrollment("vignette", "kitchen iPad", ["kitchen"])
    session_id, principal = store.redeem_enrollment(token)

    again = RoleStore(tmp_path, clock)
    assert again.principal(session_id) == principal
    assert again.peek_enrollment(pending) is not None
    assert again.redeem_enrollment(token) is None  # still used after the restart

    files = sorted((tmp_path / "roles").iterdir())
    assert [f.name for f in files] == ["enrollments.json", "sessions.json"]
    for f in files:
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
        text = f.read_text()
        for secret in (token, pending, session_id):
            assert secret not in text


def test_listings_show_fingerprints_and_revoke_works(tmp_path):
    store = RoleStore(tmp_path, VirtualClock())
    token, _ = store.create_enrollment("tester", "VWBD shop phone", ["bench-kit"])
    pending, _ = store.create_enrollment("kiosk", "")
    session_id, principal = store.redeem_enrollment(token)

    sessions = store.list_sessions()
    enrollments = store.list_enrollments()
    assert [s["fingerprint"] for s in sessions] == [principal.session_id]
    assert sessions[0] | {"created_at": 0, "expires_at": 0, "expires_in_s": 0} == {
        "fingerprint": principal.session_id,
        "role": "tester",
        "name": "VWBD shop phone",
        "grants": ["bench-kit"],
        "created_at": 0,
        "expires_at": 0,
        "expires_in_s": 0,
    }
    assert enrollments[0]["name"] == "kiosk"  # an empty name falls back to the role
    dumped = json.dumps([sessions, enrollments])
    for secret in (token, pending, session_id):
        assert secret not in dumped

    assert not store.revoke_session("000000000000")
    assert store.revoke_session(principal.session_id)
    assert store.principal(session_id) is None
    assert RoleStore(tmp_path, VirtualClock()).principal(session_id) is None  # revocation is persisted

    session_2, _ = RoleStore(tmp_path, VirtualClock()).redeem_enrollment(pending)
    fresh = RoleStore(tmp_path, VirtualClock())
    assert fresh.revoke_session(session_2)  # the raw id works too
    assert fresh.list_sessions() == []

    token_3, _ = fresh.create_enrollment("kiosk", "spare")
    assert fresh.revoke_enrollment(fresh.list_enrollments()[0]["fingerprint"])
    assert fresh.redeem_enrollment(token_3) is None


def test_in_memory_store_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = RoleStore(None, VirtualClock())
    session_id, _ = store.redeem_enrollment(store.create_enrollment("kiosk", "k")[0])
    assert store.principal(session_id) is not None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("role", "grants", "fragment"),
    [
        ("ops", [], "ops password"),
        ("admin", [], "isn't a role"),
        ("tester", [], "at least one device"),
        ("vignette", [], "at least one vignette"),
        ("kiosk", ["bench-kit"], "doesn't take grants"),
        ("tester", ["../accounts"], "don't look like"),
        ("tester", "bench-kit", "must be a list"),
    ],
)
def test_bad_enrollments_are_refused_in_plain_english(role, grants, fragment):
    with pytest.raises(RoleError, match=fragment):
        RoleStore(None, VirtualClock()).create_enrollment(role, "x", grants)


def test_a_corrupt_file_is_set_aside_not_fatal(tmp_path):
    (tmp_path / "roles").mkdir()
    (tmp_path / "roles" / "sessions.json").write_text("{not json")
    store = RoleStore(tmp_path, VirtualClock())
    assert "set aside" in store.problem
    assert store.list_sessions() == []
    assert (tmp_path / "roles" / "sessions.json.corrupt").exists()


# ---- path table ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/ops/module"),
        ("GET", "/ops/module/"),
        ("GET", "/ops/module/app.js"),
        ("GET", "/api/module"),
        ("GET", "/api/module/bench-kit"),
        ("POST", "/api/module/bench-kit/paint"),
        ("POST", "/api/module/bench-kit/open"),
        ("GET", "/api/ws-ticket"),
        ("GET", "/api/version"),
        ("GET", "/static/base.css"),
    ],
)
def test_tester_reaches_its_module_page(method, path):
    assert path_allowed(TESTER, method, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/module/other/paint"),
        ("GET", "/api/module/other"),
        ("POST", "/api/module"),
        ("GET", "/ops/bench"),
        ("GET", "/ops/accounts"),
        ("POST", "/ops/accounts"),
        ("POST", "/api/accounts/login"),
        ("GET", "/api/accounts"),
        ("POST", "/api/bench/run"),
        ("GET", "/api/devices"),
        ("GET", "/api/roles/sessions"),
        ("POST", "/api/roles/enrollments"),
        ("GET", "/ops"),
        ("GET", "/ops/"),
        ("GET", "/kiosk"),
        ("GET", "/api/kiosk/select"),
        ("GET", "/ops/modules"),  # a prefix that only looks similar
        ("GET", "/api/module/bench-kitchen"),
        ("GET", "/ops/module/../accounts"),
        ("GET", "/ops/module/%2e%2e/accounts"),
        ("GET", "/ops/module/%252e%252e/accounts"),
        ("POST", "/api/module/bench-kit/../../accounts/login"),
        ("GET", "/ops//module"),
        ("GET", "/ops/module/.."),
        ("GET", "/ops/module\\..\\accounts"),
        ("GET", "/visualizer/"),
        ("GET", "/"),
        ("GET", "relative/path"),
    ],
)
def test_tester_is_kept_out_of_everything_else(method, path):
    assert not path_allowed(TESTER, method, path)


def test_kiosk_paths():
    for method, path in [
        ("GET", "/kiosk"),
        ("GET", "/kiosk/"),
        ("GET", "/kiosk/app.js"),
        ("POST", "/api/kiosk/select"),
        ("GET", "/api/kiosk/state"),
        ("GET", "/api/ws-ticket"),
        ("GET", "/static/app.js"),
    ]:
        assert path_allowed(KIOSK, method, path), path
    for method, path in [
        ("GET", "/ops/module"),
        ("GET", "/api/module"),
        ("POST", "/api/module/bench-kit/paint"),
        ("GET", "/ops/accounts"),
        ("POST", "/api/accounts/login"),
        ("POST", "/api/bench/run"),
        ("POST", "/kiosk/"),
        ("GET", "/kiosk/../ops/accounts"),
        ("GET", "/kiosks"),
        ("GET", "/vignette/kitchen/"),
    ]:
        assert not path_allowed(KIOSK, method, path), path


def test_vignette_paths():
    assert path_allowed(VIGNETTE, "GET", "/vignette/kitchen/")
    assert path_allowed(VIGNETTE, "GET", "/vignette/kitchen/app.js")
    assert path_allowed(VIGNETTE, "POST", "/api/vignette/kitchen/scene")
    assert not path_allowed(VIGNETTE, "GET", "/vignette/porch/")
    assert not path_allowed(VIGNETTE, "POST", "/api/vignette/porch/scene")
    assert not path_allowed(VIGNETTE, "GET", "/kiosk/")
    assert not path_allowed(VIGNETTE, "GET", "/ops/module")


def test_ops_reaches_everything_and_homes_are_per_role():
    assert path_allowed(OPS, "POST", "/api/accounts/login")
    assert home_path(KIOSK) == "/kiosk/"
    assert home_path(TESTER) == "/ops/module/"
    assert home_path(Principal(Role.VIGNETTE, "x", frozenset({"porch", "kitchen"}))) == "/vignette/kitchen/"


def test_normalize_path():
    assert normalize_path("/ops/module/") == "/ops/module"
    assert normalize_path("/static/a%20b.css") == "/static/a b.css"
    assert normalize_path("/") == "/"
    assert normalize_path("/a/./b") is None
    assert normalize_path("/a/%2E%2E/b") is None
    assert normalize_path("/a\x00b") is None
