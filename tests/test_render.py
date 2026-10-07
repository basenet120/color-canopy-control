"""Head renderer: state rows → what each head shows, per mode, and the look described in words."""

from conductor.capabilities import Capabilities
from conductor.drivers.base import StateRow
from conductor.render import PROGRAMS, describe_look, head_colors, kelvin_to_rgb, preset_palette
from conductor.rows import FRAME_CLASS, FrameEntry, Look, frame_value, full_state_vector, slot_write

BLACK = {"r": 0, "g": 0, "b": 0}


def scaled(rgb, k):
    return {"r": round(rgb[0] * k), "g": round(rgb[1] * k), "b": round(rgb[2] * k)}


def slot_rows(entries, slot="custom-1", level=100):
    return [
        slot_write(slot, frame_value(None, entries, expected_length=None)),
        *full_state_vector(Look("slot", level, slot=slot)),
    ]


def test_kelvin_to_rgb_runs_warm_to_cool():
    warm, neutral, cool = kelvin_to_rgb(2700), kelvin_to_rgb(4000), kelvin_to_rgb(6500)
    assert warm[0] == 255 and warm[1] < 200 and warm[2] < 120
    assert warm[2] < neutral[2] < cool[2]
    assert min(cool) > 240  # 6500 K is close to white
    assert kelvin_to_rgb(500) == kelvin_to_rgb(1000)  # clamped


def test_power_off_is_black():
    rows = full_state_vector(Look("off"))
    assert head_colors(rows, 30) == [BLACK] * 30
    assert describe_look(rows) == "off"
    assert head_colors(rows, 0) == []


def test_white_mode_scales_kelvin_by_brightness():
    rows = full_state_vector(Look("white", 60, cct=2700))
    heads = head_colors(rows, 30)
    assert heads == [scaled(kelvin_to_rgb(2700), 0.6)] * 30
    assert describe_look(rows) == "white 2700 K at 60%"


def test_colour_mode_scales_rgb_by_brightness():
    rows = full_state_vector(Look("rgb", 50, rgb=(249, 99, 2)))
    assert head_colors(rows, 3) == [scaled((249, 99, 2), 0.5)] * 3
    assert describe_look(rows) == "colour #F96302 at 50%"


def test_individual_mode_paints_each_head_from_the_selected_slot():
    entries = [
        FrameEntry(255, 0, 0),  # pure red
        FrameEntry(0, 0, 0, 2700, 0, 100),  # per-puck warm white only
        FrameEntry(0, 0, 255, 2700, 50, 50),  # half blue + half warm white
        FrameEntry(255, 255, 255, 6500, 100, 100),  # additive → clamped
    ] + [FrameEntry(0, 255, 0)] * 26
    rows = slot_rows(entries)
    heads = head_colors(rows, 30)
    ww = kelvin_to_rgb(2700)
    assert heads[0] == {"r": 255, "g": 0, "b": 0}
    assert heads[1] == {"r": ww[0], "g": ww[1], "b": ww[2]}
    assert heads[2] == {"r": round(ww[0] * 0.5), "g": round(ww[1] * 0.5), "b": round(127.5 + ww[2] * 0.5)}
    assert heads[3] == {"r": 255, "g": 255, "b": 255}
    assert heads[4:] == [{"r": 0, "g": 255, "b": 0}] * 26
    assert describe_look(rows, 30) == "slot custom-1 (30 painted heads)"
    dim = slot_rows(entries, level=50)
    assert head_colors(dim, 30)[0] == {"r": 128, "g": 0, "b": 0}
    assert describe_look(dim) == "slot custom-1 (30 painted heads) at 50%"


def test_individual_mode_respects_no_white_and_short_frames():
    rows = slot_rows([FrameEntry(0, 0, 0, 2700, 0, 100)] * 20, slot="custom-2")
    no_white = head_colors(rows, 30, Capabilities(no_white_hint=True))
    assert no_white == [BLACK] * 30
    heads = head_colors(rows, 30)
    assert heads[19] != BLACK and heads[20:] == [BLACK] * 10  # heads past the frame stay dark
    # a frame length that differs from the module map is named as controller entries, not painted heads
    assert describe_look(rows, 30) == "slot custom-2 (20 controller entries; 30 heads on the map)"


def test_individual_mode_with_no_stored_pattern():
    rows = [StateRow("color-mode", None, "individual"), StateRow("color-individual", "custom", "custom-3")]
    assert head_colors(rows, 5) == [BLACK] * 5
    assert describe_look(rows) == "slot custom-3 (no pattern stored)"


def test_programs_use_their_decoded_cct_and_level():
    rows = full_state_vector(Look("program", 100, program="dinner-party"))
    assert head_colors(rows, 4) == [scaled(kelvin_to_rgb(2800), 0.62)] * 4
    assert describe_look(rows) == "program dinner-party: 2800 K at 62% (on-screen preview is approximate)"
    assert PROGRAMS["nightlight"] == (2700, 5) and PROGRAMS["moonlight"] == (5000, 10)
    assert set(PROGRAMS) == {
        "nightlight",
        "dinner-party",
        "chill",
        "focus",
        "clarity",
        "moonlight",
        "getting-ready",
        "sleep",
        "wake-up",
    }


def test_presets_are_a_deterministic_three_colour_approximation():
    rows = full_state_vector(Look("preset", 100, preset="snowfall"))
    heads = head_colors(rows, 30)
    palette = preset_palette("snowfall")
    assert heads[:3] == [{"r": c[0], "g": c[1], "b": c[2]} for c in palette]
    assert heads[3:6] == heads[:3]  # the palette repeats along the strand
    assert head_colors(rows, 30) == heads  # no time, no randomness
    assert describe_look(rows) == "preset snowfall (on-screen preview is approximate)"
    # a preset with no colour model is neutral grey, never colours invented from its name
    unknown = preset_palette("effervescent")
    assert unknown == preset_palette("prism") == ((96, 96, 96),) * 3
    dim = full_state_vector(Look("preset", 50, preset="effervescent"))
    assert head_colors(dim, 1) == [scaled(unknown[0], 0.5)]
    assert describe_look(dim) == "preset effervescent at 50% (on-screen preview is approximate)"


def test_render_ignores_telemetry_rows_and_accepts_a_mapping():
    rows = [*full_state_vector(Look("white", 100, cct=6500)), StateRow("available", None, False)]
    by_key = {r.key: r.value for r in rows}
    assert head_colors(rows, 2) == head_colors(by_key, 2)
    assert describe_look(by_key) == "white 6500 K at 100%"
    assert FRAME_CLASS == "color-sequence-v2"
