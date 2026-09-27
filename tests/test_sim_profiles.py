"""Simulator profiles: the sibling string resized in code to the real SQHDK03's shape (60-entry frames, 30 pucks —
VWBD workshop, Sep 27 2026), the pre-workshop 30-entry variant, and the 20-segment fixture as-is."""

import json

import pytest

from conductor.capabilities import detect
from conductor.rows import FRAME_CLASS, SLOTS
from conductor.sim.profiles import (
    DEFAULT_PROFILE,
    PROFILE_DIR,
    SIBLING_FILE,
    ProfileError,
    device_info,
    frame_length,
    load_profile,
    profile_names,
    profile_parts,
    pucks,
    resize_profile,
)


def _hints(dump):
    caps = [
        h
        for c in dump["capabilities"]
        if c["functionClass"] == "color-individual"
        for cv in c["_opts"]["categoryValues"]
        for h in cv.get("hints", [])
    ]
    fns = [
        h
        for f in dump["functions"]
        if f["functionClass"] == "color-individual"
        for v in f["values"]
        for h in v.get("hints", [])
    ]
    return caps, fns


def _frames(dump):
    return {
        s["functionInstance"]: s["value"][FRAME_CLASS]["frameBuffer"]["framebuffer"]
        for s in dump["states"]
        if s["functionClass"] == FRAME_CLASS
    }


@pytest.mark.parametrize(("name", "entries"), [("sqhdk03-sim", 60), ("sqhdk03-sim-30", 30)])
def test_sqhdk03_sim_is_the_sibling_resized_everywhere(name, entries):
    dump = load_profile(name)
    caps_hints, fn_hints = _hints(dump)
    assert caps_hints == [f"{entries}-segment"] * 3 and fn_hints == [f"{entries}-segment"] * 3
    frames = _frames(dump)
    assert sorted(frames) == list(SLOTS)
    assert all(len(fb) == entries for fb in frames.values())
    assert dump["default_image"] == f"novolink-{entries}-segment-holiday-string-lights-icon"
    assert dump["model"] == "SQHDK03 (simulated from sibling string)"
    assert "20-segment" not in json.dumps(dump)  # nothing half-resized
    assert frame_length(dump) == entries


def test_the_default_profile_is_the_real_kit_60_entries_driving_30_pucks_from_entry_0():
    assert DEFAULT_PROFILE == "sqhdk03-sim"
    assert pucks("sqhdk03-sim") == (30, 0)
    assert pucks("sqhdk03-sim-30") == (30, 0) and pucks("string-20seg") == (20, 0)


def test_resize_repeats_the_existing_pattern_so_a_slot_keeps_its_look():
    sibling = load_profile("string-20seg")
    resized = load_profile("sqhdk03-sim")
    for slot, fb in _frames(resized).items():
        original = _frames(sibling)[slot]
        assert fb == original * 3


def test_capability_detection_sees_a_60_entry_frame_that_fits_30_pucks():
    functions, states = profile_parts("sqhdk03-sim")
    caps = detect(functions, states)
    assert caps.framebuffer_length == 60 and caps.segment_hint == 60
    assert caps.individual and caps.slots == list(SLOTS)
    assert caps.cct_range == (2700, 6500) and "dinner-party" in caps.programs
    # frame length ≠ head count: 60 entries hold 30 pucks (from entry 0 or 30), never 31 from entry 30
    assert caps.head_count_problem(30) is None and caps.head_count_problem(30, 30) is None
    assert (
        caps.head_count_problem(30, 31)
        == "the device's frame holds 60 entries but this module's 30 pucks need entries 31–60"
    )
    assert caps.head_count_problem(61) == "the device reports 60 heads but this module should have 61"
    assert caps.frame_note(30) == (
        "This controller's saved patterns hold 60 entries; this module's 30 pucks use entries 0–29 (confirm once "
        "with the index sweep or the hue ramp)."
    )
    assert caps.frame_layout(30).describe() == "60-entry pattern; pucks use 0–29"
    assert caps.frame_layout(30, 31) is None
    thirty = detect(*profile_parts("sqhdk03-sim-30"))
    assert (
        thirty.framebuffer_length == 30
        and thirty.frame_note(30) is None
        and thirty.head_count_problem(30) is None
    )


def test_string_20seg_is_the_fixture_unchanged():
    with open(PROFILE_DIR / SIBLING_FILE, encoding="utf-8") as f:
        raw = json.load(f)
    assert load_profile("string-20seg") == raw
    functions, states = profile_parts("string-20seg")
    assert detect(functions, states).framebuffer_length == 20


def test_profiles_are_fresh_copies():
    a = load_profile(DEFAULT_PROFILE)
    a["states"].clear()
    a["functions"][0]["functionClass"] = "tampered"
    b = load_profile(DEFAULT_PROFILE)
    assert b["states"] and b["functions"][0]["functionClass"] != "tampered"


def test_unknown_profile_and_unsafe_resize_are_refused():
    assert profile_names()[:3] == ["sqhdk03-sim", "string-20seg", "sqhdk03-sim-30"]
    with pytest.raises(ProfileError, match="no simulator profile"):
        load_profile("nope")
    with pytest.raises(LookupError):
        load_profile("nope")
    broken = load_profile("string-20seg")
    broken["functions"] = [f for f in broken["functions"] if f["functionClass"] != "color-individual"]
    with pytest.raises(ProfileError, match="expected 6 segment hints"):
        resize_profile(broken, 30)
    with pytest.raises(ProfileError, match="1–90 heads"):
        resize_profile(load_profile("string-20seg"), 0)


def test_device_info_is_simulated_with_functions_and_state_rows():
    info = device_info("sqhdk03-sim", device_id="sim-bench-kit", account="bench", label="Bench kit")
    assert info.simulated and info.device_id == "sim-bench-kit" and info.account == "bench"
    assert info.name == "Bench kit" and info.model.startswith("SQHDK03")
    assert info.raw["id"] == "sim-bench-kit"
    assert {f["functionClass"] for f in info.functions} >= {"power", "color-mode", FRAME_CLASS}
    assert any(r.function_class == FRAME_CLASS for r in info.states)
    assert detect(info.functions, info.states).framebuffer_length == 60
