"""Tests for the contract modules: clock, tokens, redaction, tickets, rows, capabilities, locks, config."""

import asyncio
import json
import logging
import stat
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from conductor import redact
from conductor.capabilities import detect
from conductor.clock import VirtualClock, run_virtual
from conductor.config import ConfigError, load_registry, load_settings, save_overlay_patch
from conductor.drivers.base import StateRow
from conductor.locks import DeviceLockManager, LockError
from conductor.rows import (
    FrameEntry,
    Look,
    PayloadError,
    compare,
    frame_value,
    full_state_vector,
    slot_hash,
    slot_select,
)
from conductor.tokens import TokenStore, TokenStoreUnavailable
from conductor.wsauth import TicketBook, origin_allowed

FIXTURE = Path(__file__).resolve().parent.parent / "conductor/sim/profiles/hampton-bay-string-20seg.json"


class ManualClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def now(self):
        return self.t

    def monotonic(self):
        return self.t

    async def sleep(self, s):
        self.t += s


# ---- clock ------------------------------------------------------------------------------------------------------
def test_virtual_loop_runs_hours_in_milliseconds():
    clock = VirtualClock()

    async def main():
        start = clock.now()
        await asyncio.gather(*(asyncio.sleep(3600) for _ in range(3)))
        try:
            await asyncio.wait_for(asyncio.sleep(99), 5)
        except TimeoutError:
            pass
        return clock.now() - start

    t0 = time.monotonic()
    assert run_virtual(main) == pytest.approx(3605)
    assert time.monotonic() - t0 < 1.0


# ---- tokens -----------------------------------------------------------------------------------------------------
def test_token_store_round_trip_rotation_and_binding(tmp_path):
    key = Fernet.generate_key().decode()
    store = TokenStore(tmp_path, "staging", key, ManualClock())
    assert store.load("bench@example.invalid") is None
    assert store.save("bench@example.invalid", "refresh-token-generation-1") == 1
    assert store.save("bench@example.invalid", "refresh-token-generation-2") == 2
    assert store.load("Bench@example.invalid") == "refresh-token-generation-2"
    files = list((tmp_path / "tokens").iterdir())
    assert {f.suffix for f in files} == {".token", ".prev"}
    for f in files:
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
        assert b"refresh-token-generation" not in f.read_bytes()
    # File names are bound to the environment too: another runtime doesn't even see staging's file.
    assert TokenStore(tmp_path, "venue", key, ManualClock()).load("bench@example.invalid") is None
    assert store.status("bench@example.invalid").generation == 2


def test_token_store_foreign_ciphertext_is_refused(tmp_path):
    key = Fernet.generate_key().decode()
    staging = TokenStore(tmp_path, "staging", key, ManualClock())
    staging.save("bench@example.invalid", "refresh-token-abc123")
    venue = TokenStore(tmp_path, "venue", key, ManualClock())
    venue._path("bench@example.invalid").write_bytes(staging._path("bench@example.invalid").read_bytes())
    with pytest.raises(TokenStoreUnavailable, match="different runtime"):
        venue.load("bench@example.invalid")
    other_key = TokenStore(tmp_path, "staging", Fernet.generate_key().decode(), ManualClock())
    with pytest.raises(TokenStoreUnavailable, match="decrypted"):
        other_key.load("bench@example.invalid")


@pytest.mark.parametrize("key", [None, "", "not-a-fernet-key"])
def test_token_store_without_valid_key_is_disabled_not_crashing(tmp_path, key):
    store = TokenStore(tmp_path, "local", key)
    assert not store.available and "CONDUCTOR_SECRET_KEY" in store.problem
    with pytest.raises(TokenStoreUnavailable):
        store.save("a@example.invalid", "x" * 20)


# ---- redaction --------------------------------------------------------------------------------------------------
def test_redaction_filter_scrubs_registered_secrets_and_bearers(caplog):
    redact.add_secret("sentinel-refresh-token-777")
    logger = logging.getLogger("test.redact")
    logger.addFilter(redact.RedactingFilter())
    with caplog.at_level(logging.INFO):
        logger.info("token %s and Bearer abc.def.ghi and password=hunter22", "sentinel-refresh-token-777")
    text = caplog.text
    assert "sentinel-refresh-token-777" not in text and "abc.def.ghi" not in text and "hunter22" not in text


# ---- tickets / origin -------------------------------------------------------------------------------------------
def test_tickets_are_single_use_and_expire():
    clock = ManualClock()
    book = TicketBook(clock)
    t = book.issue("tester", ["bench-kit"])
    assert book.redeem(t.value).grants == {"bench-kit"}
    assert book.redeem(t.value) is None
    t2 = book.issue("ops")
    clock.t += 31
    assert book.redeem(t2.value) is None
    assert book.redeem(None) is None and book.redeem(123) is None


def test_origin_check():
    assert origin_allowed({"origin": "https://canopy.local", "host": "canopy.local"})
    assert not origin_allowed({"origin": "https://evil.example", "host": "canopy.local"})
    assert not origin_allowed({"origin": "null", "host": "canopy.local"})
    assert origin_allowed({"host": "canopy.local"})  # non-browser clients still need a ticket


# ---- rows -------------------------------------------------------------------------------------------------------
def test_frame_value_copies_header_and_enforces_length():
    readback = {
        "color-sequence-v2": {
            "id": 7,
            "version": 3,
            "headerFlags": 129,
            "sequenceFlags": 2,
            "brightnessDepth": 90,
            "brightnessEffect": 0,
            "brightnessSpeed": 50,
            "motionEffect": 0,
            "motionSpeed": 50,
            "frameBuffer": {"flags": 5, "framebuffer": [{}] * 30},
        }
    }
    entries = [FrameEntry(255, 110, 15)] * 30
    v = frame_value(readback, entries, expected_length=30)["color-sequence-v2"]
    assert (v["id"], v["version"], v["headerFlags"], v["sequenceFlags"], v["frameBuffer"]["flags"]) == (
        7,
        3,
        129,
        2,
        5,
    )
    assert len(v["frameBuffer"]["framebuffer"]) == 30
    with pytest.raises(PayloadError, match="29 entries"):
        frame_value(readback, entries[:29], expected_length=30)
    with pytest.raises(PayloadError, match="0–255"):
        frame_value(readback, [FrameEntry(256)] * 30, expected_length=30)
    with pytest.raises(PayloadError, match="effect field"):
        frame_value(readback, entries, expected_length=30, effects={"id": 1})


def test_full_state_vector_shapes():
    rows = full_state_vector(Look("white", brightness=60, cct=2700), cct_range=(2700, 6500))
    keys = [(r.function_class, r.function_instance) for r in rows]
    assert keys == [
        ("power", None),
        ("color-mode", None),
        ("color-temperature", None),
        ("brightness", None),
        ("speed", "color-sequence"),
        ("toggle", "party-mode"),
    ]
    assert rows[2].value == "2700"
    with pytest.raises(PayloadError, match="outside"):
        full_state_vector(Look("white", cct=2200), cct_range=(2700, 6500))
    prog = full_state_vector(Look("program", program="dinner-party", brightness=62))
    assert ("color-sequence", "preset") in [r.key for r in prog] and ("color-sequence", "custom") in [
        r.key for r in prog
    ]
    assert [r.value for r in slot_select("custom-2")] == ["custom-2", "individual"]


def test_compare_missing_rows_are_mismatches_and_extras_tolerated():
    expected = full_state_vector(Look("white", brightness=60, cct=2700))
    reported = [StateRow(r.function_class, r.function_instance, r.value) for r in expected]
    reported[2] = StateRow("color-temperature", None, 2700)  # int readback vs string sent → equal
    reported.append(StateRow("wifi-rssi", None, -50))
    assert compare(expected, reported) == []
    assert [m.missing for m in compare(expected, reported[1:])] == [True]
    assert compare(expected, []) and all(m.missing for m in compare(expected, []))


def test_slot_hash_ignores_header_and_orders_entries():
    a = frame_value(None, [FrameEntry(1, 2, 3)] * 2 + [FrameEntry(9, 9, 9)], expected_length=3)
    b = json.loads(json.dumps(a))
    b["color-sequence-v2"]["headerFlags"] = 0
    assert slot_hash(a) == slot_hash(b)
    c = frame_value(None, [FrameEntry(9, 9, 9)] + [FrameEntry(1, 2, 3)] * 2, expected_length=3)
    assert slot_hash(a) != slot_hash(c)
    assert slot_hash("custom-1") is None


# ---- capabilities -----------------------------------------------------------------------------------------------
def test_capabilities_from_fixture():
    dump = json.loads(FIXTURE.read_text())
    states = [StateRow(s["functionClass"], s.get("functionInstance"), s["value"]) for s in dump["states"]]
    caps = detect(dump["functions"], states)
    assert caps.slots == ["custom-1", "custom-2", "custom-3"]
    assert caps.segment_hint == 20 and caps.framebuffer_length == 20
    assert caps.individual and caps.rgb and caps.power and caps.party_mode
    assert caps.cct_range == (2700, 6500) and caps.speed_range == (-10, 10)
    assert "individual" in caps.color_modes
    assert "snowfall" in caps.presets and "custom" not in caps.presets
    assert "dinner-party" in caps.programs
    assert caps.head_count_problem(30) == "the device reports 20 heads but this module should have 30"
    assert caps.head_count_problem(20) is None


# ---- locks ------------------------------------------------------------------------------------------------------
def test_locks_exclusive_idle_expiry_and_allowlist():
    clock = ManualClock()
    locks = DeviceLockManager(lambda d: (d != "outside", "not allowlisted"), clock, idle_timeout_s=600)
    lease = locks.acquire("kit", "module page")
    with pytest.raises(LockError, match="in use by module page"):
        locks.acquire("kit", "bench")
    with pytest.raises(LockError, match="not allowlisted"):
        locks.acquire("outside", "bench")
    clock.t += 599
    locks.validate("kit", lease.token)
    clock.t += 601
    assert not locks.is_locked("kit")
    with pytest.raises(LockError):
        locks.validate("kit", lease.token)
    lease2 = locks.acquire("kit", "bench")
    assert not locks.release("kit", "wrong") and locks.release("kit", lease2.token)


# ---- config -----------------------------------------------------------------------------------------------------
def test_settings_and_registry_load():
    settings = load_settings()
    assert settings.executor.min_write_interval_s == 1.0 and settings.kiosk.cooldown_s == 8
    reg = load_registry()
    assert len(reg.canopy_modules()) == 9
    kit = reg.get("bench-kit")
    assert kit.expected_heads == 30 and reg.address("bench-kit", "sim") == "sim-bench-kit"
    assert reg.address("bench-kit", "afero") is None and reg.writable_metadevice_ids() == []


def test_overlay_patch_validates_and_persists(tmp_path):
    reg = load_registry(data=tmp_path)
    reg = save_overlay_patch(
        reg, "bench-kit", {"metadevice_id": "md-123", "flags": {"t2_select": True}}, now=0
    )
    assert reg.get("bench-kit").metadevice_id == "md-123" and reg.get("bench-kit").flags.t2_select
    assert reg.writable_metadevice_ids() == ["md-123"]
    assert list((tmp_path / "overrides" / "history").iterdir())
    with pytest.raises(ConfigError, match="bench-kit"):
        save_overlay_patch(reg, "bench-kit", {"orientation": "C"}, now=1)
    with pytest.raises(ConfigError, match="new device needs"):
        save_overlay_patch(reg, "ghost", {"label": "x"}, now=2)
    reg = save_overlay_patch(reg, "spare-1", {"role": "spare", "account": "canopy"}, now=3)
    assert reg.get("spare-1").role == "spare"


def test_frame_offset_defaults_to_0_and_is_validated(tmp_path):
    """frame_offset: the framebuffer entry that drives puck 0 (backward-compatible default 0; never negative)."""
    reg = load_registry(data=tmp_path)
    assert all(d.frame_offset == 0 for d in reg.devices)
    reg = save_overlay_patch(reg, "bench-kit", {"frame_offset": 30}, now=0)
    assert reg.get("bench-kit").frame_offset == 30
    assert load_registry(data=tmp_path).get("bench-kit").frame_offset == 30  # persisted in the overlay
    for bad in (-1, 90, "thirty"):
        with pytest.raises(ConfigError, match="frame_offset"):
            save_overlay_patch(reg, "bench-kit", {"frame_offset": bad}, now=1)


def test_bad_overlay_yaml_is_explained(tmp_path):
    (tmp_path / "overrides").mkdir()
    (tmp_path / "overrides" / "registry.yaml").write_text("devices:\n  bench-kit: {orientation: [\n")
    with pytest.raises(ConfigError, match="not valid YAML at line"):
        load_registry(data=tmp_path)


def test_lock_changes_say_acquire_steal_release_or_expire_with_the_real_expiry_time(tmp_path):
    """LCK-1: lock telemetry distinguishes a take-over and an idle expiry, and dates the expiry correctly."""
    from conductor.services import build_services

    clock = ManualClock()
    services = build_services({"CONDUCTOR_DATA_DIR": str(tmp_path)}, clock=clock)
    locks, timeout = services.locks, services.locks.idle_timeout_s
    first = locks.acquire("sim-bench-kit", "bench · gate_b")
    locks.acquire("sim-bench-kit", "module page · ops", steal=True)
    clock.t += 5
    stolen_at = clock.t
    taken = locks.acquire("sim-bench-kit", "module page · alon", steal=True)
    assert taken.token != first.token
    clock.t += timeout + 300  # nobody looks for 5 minutes after it idled out
    assert locks.holder("sim-bench-kit") is None
    again = locks.acquire("sim-bench-kit", "bench · gate_c")
    locks.release("sim-bench-kit", again.token)

    events = [(e["action"], e["owner"]) for e in services.telemetry.recent(kind="lock")]
    assert events == [
        ("acquire", "bench · gate_b"),
        ("steal", "module page · ops"),
        ("steal", "module page · alon"),
        ("expire", "module page · alon"),
        ("acquire", "bench · gate_c"),
        ("release", "bench · gate_c"),
    ]
    lock_events = services.telemetry.recent(kind="lock")
    assert lock_events[1]["previous_owner"] == "bench · gate_b"
    assert lock_events[2]["previous_owner"] == "module page · ops"
    expire = lock_events[3]
    assert expire["expired_at"] == pytest.approx(stolen_at + timeout)
    assert expire["t"] > expire["expired_at"]  # noticed later than it happened, but dated when it happened
