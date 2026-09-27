"""The anonymizer (07 §9, §10): stable placeholders, nothing identifying left — and fixtures and docs pass it."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from conductor.anonymize import MAC_RE, UUID_RE, ZERO_MAC, anonymize, check_clean

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = sorted((ROOT / "conductor" / "sim" / "profiles").glob("*.json"))
DOCS = sorted([*(ROOT / "docs").rglob("*.md"), *(ROOT / "research").rglob("*.md")])

U1 = "3f2c1d9e-8b7a-4c6d-9e0f-1a2b3c4d5e6f"
U2 = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


def _fixture() -> dict:
    return json.loads((ROOT / "conductor/sim/profiles/hampton-bay-string-20seg.json").read_text())


def test_device_ids_get_stable_placeholders_everywhere():
    obj = {
        "device_id": U1,
        "log": f"PUT /v1/accounts/acct-9/metadevices/{U1}/state",
        "nested": [{"metadeviceId": U1}, {"deviceId": U2}],
        "counts": {U1: 3},
    }
    out = anonymize(obj)
    assert out["device_id"] == "device-1"
    assert out["log"] == "PUT /v1/accounts/acct-9/metadevices/device-1/state"
    assert out["nested"] == [{"metadeviceId": "device-1"}, {"deviceId": "device-2"}]
    assert out["counts"] == {"device-1": 3}


def test_a_profile_dump_is_scrubbed_and_still_shaped_like_a_fixture():
    dump = _fixture()
    original = copy.deepcopy(dump)
    out = anonymize(dump)
    assert dump == original, "anonymize must not mutate its input"
    assert out["id"] == "device-1" and out["device_id"] == "device-2"
    assert out["friendly_name"] == "<name>" and out["default_name"] == "<name>"
    text = json.dumps(out)
    assert not UUID_RE.search(text), "a UUID survived"
    rows = {s["functionClass"]: s["value"] for s in out["states"]}
    assert rows["wifi-ssid"] == "<ssid>"
    assert rows["wifi-mac-address"] == ZERO_MAC and rows["ble-mac-address"] == ZERO_MAC
    assert rows["geo-coordinates"] == {"geo-coordinates": {"latitude": "0", "longitude": "0"}}
    # the look survives: functions, slots and values are untouched apart from ids
    assert [f["functionClass"] for f in out["functions"]] == [
        f["functionClass"] for f in original["functions"]
    ]
    assert rows["color-mode"] == "sequence"
    assert out["functions"][0]["id"].startswith("id-")


def test_macs_emails_tokens_accounts_and_geo():
    obj = {
        "note": "kit AA:BB:CC:DD:EE:0F on alon@example.org, fixture bench@example.invalid",
        "wifi-mac-address": "a1b2c3d4e5f6",
        "headers": {"Authorization": "Bearer abc.def-ghijklmnop"},
        "log": "sent Bearer abcdefghijklmnop and eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.c2lnbmF0dXJl",
        "refresh_token": "rt-123",
        "accountId": "acct-live-7",
        "url": "/v1/accounts/acct-live-7/metadevices",
        "geo-coordinates": {"geo-coordinates": {"latitude": "32.78", "longitude": "-79.93"}},
        "friendlyName": "Alon's porch",
        "wifi-ssid": "HomeNet",
        "rows": [{"functionClass": "wifi-ssid", "functionInstance": None, "value": "HomeNet"}],
    }
    out = anonymize(obj)
    assert out["note"] == f"kit {ZERO_MAC} on person@example.invalid, fixture bench@example.invalid"
    assert out["wifi-mac-address"] == ZERO_MAC
    assert out["headers"]["Authorization"] == "<redacted>"
    assert "abcdefghijklmnop" not in out["log"] and "eyJ" not in out["log"]
    assert out["refresh_token"] == "<redacted>"
    assert out["accountId"] == "account-1" and out["url"] == "/v1/accounts/account-1/metadevices"
    assert out["geo-coordinates"] == {"geo-coordinates": {"latitude": "0", "longitude": "0"}}
    assert out["friendlyName"] == "<name>" and out["wifi-ssid"] == "<ssid>"
    assert out["rows"][0]["value"] == "<ssid>"
    assert check_clean(json.dumps(out)) == []


def test_keep_and_extra_ids():
    record = {"device_id": "bench-kit", "address": "sim-bench-kit", "error": "sim-bench-kit is held by bench"}
    out = anonymize(record, keep=["bench-kit"])
    assert out == {"device_id": "bench-kit", "address": "device-1", "error": "device-1 is held by bench"}
    out = anonymize({"msg": "PUT md-bench-0001 ok"}, extra_ids=["md-bench-0001"])
    assert out == {"msg": "PUT device-1 ok"}


def test_check_clean_flags_what_it_should():
    assert check_clean(f"mac {ZERO_MAC}, fixture bench@example.invalid, docs person@example.com") == []
    problems = check_clean("mac 0A:1B:2C:3D:4E:5F and someone@gmail.com and Bearer abcdefghijkl1234")
    assert len(problems) == 3
    assert MAC_RE.search("time 12:03:04.512") is None  # a clock isn't a MAC


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_simulator_fixtures_are_clean(path):
    assert check_clean(path.read_text()) == [], path


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_docs_and_research_are_clean(path):
    assert check_clean(path.read_text()) == [], path
