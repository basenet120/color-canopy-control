"""State rows → what each head of a strand shows: the visualizer's and the module page's approximation.

Rules being implemented (04 "Cloud profile", 07 §6):
- **Power off** → every head black.
- **white** → the whole strand at `color-temperature` (Kelvin → RGB) × `brightness`.
- **color** → the whole strand at `color-rgb` × `brightness`.
- **individual** → the selected slot's framebuffer (`color-individual/custom` names the slot), entry i on head i:
  `rgb × colorBrightness` blended additively with `kelvin_to_rgb(cct) × whiteBrightness`, × strand brightness,
  clamped. Per-puck white on this RGBW product is UNVERIFIED; a device with a `no-white` hint gets no white term.
  Heads beyond the frame stay dark (a short frame lights only its own entries).
- **sequence** → deterministic starting frames for known presets, with separate metadata for the browser's
  illustrative animation. RGB fades/jumps begin red; rainbow gradients use matching adjacent pairs observed
  on the bench. Unknown presets/programs are neutral grey, never invented name-derived colors. The browser
  identifies these as unsynchronized previews, not measured LED frames.

Colours are `{"r", "g", "b"}` dicts of 0–255 ints. No animation, no time: the same rows always render the same.
"""

from __future__ import annotations

import colorsys
import math
from collections.abc import Iterable, Mapping
from typing import Any

from conductor.capabilities import Capabilities
from conductor.drivers.base import TELEMETRY_CLASSES, StateRow
from conductor.rows import FRAME_CLASS, frame_entries

Key = tuple[str, str | None]
RGB = tuple[int, int, int]

# Decoded attr-300 programs (research/permanent-lights/api-model.md): (Kelvin, level %). Sleep/wake-up are fades;
# the still shows their starting level.
PROGRAMS: dict[str, tuple[int, int]] = {
    "nightlight": (2700, 5),
    "dinner-party": (2800, 62),
    "chill": (3200, 40),
    "focus": (4400, 85),
    "clarity": (5000, 100),
    "moonlight": (5000, 10),
    "getting-ready": (4300, 91),
    "sleep": (2700, 40),
    "wake-up": (2700, 50),
}

# Approximate palettes for well-known presets (decoded holiday fades: christmas red/green, rainbow r/g/b).
KNOWN_PRESETS: dict[str, tuple[RGB, RGB, RGB]] = {
    "rainbow-road": ((255, 0, 0), (0, 255, 0), (0, 0, 255)),
    "rainbow-roll": ((255, 0, 0), (0, 255, 0), (0, 0, 255)),
    "gradient-rainbow": ((255, 0, 0), (0, 255, 0), (0, 0, 255)),
    "fade-3": ((255, 0, 0), (0, 255, 0), (0, 0, 255)),
    "jump-3": ((255, 0, 0), (0, 255, 0), (0, 0, 255)),
    "christmas": ((255, 0, 0), (0, 200, 0), (255, 180, 110)),
    "rainbow": ((255, 0, 0), (0, 255, 0), (0, 0, 255)),
    "halloween": ((255, 100, 0), (140, 0, 255), (255, 60, 0)),
    "july-4th": ((255, 0, 0), (255, 255, 255), (0, 0, 255)),
    "snowfall": ((255, 255, 255), (170, 210, 255), (120, 170, 255)),
    "hanukkah": ((0, 60, 255), (255, 255, 255), (0, 120, 255)),
    "valentines-day": ((255, 0, 60), (255, 120, 170), (255, 255, 255)),
    "st-patricks-day": ((0, 200, 0), (120, 255, 60), (255, 255, 255)),
}

BLACK = {"r": 0, "g": 0, "b": 0}


def _clamp(v: float) -> int:
    return max(0, min(255, round(v)))


def _rgb(c: Iterable[float]) -> dict:
    r, g, b = (_clamp(x) for x in c)
    return {"r": r, "g": g, "b": b}


def _scale(c: Iterable[float], k: float) -> tuple[float, float, float]:
    r, g, b = c
    return (r * k, g * k, b * k)


def kelvin_to_rgb(kelvin: float) -> RGB:
    """Approximate sRGB of a black-body source (Tanner Helland's fit), clamped to 1000–40000 K."""
    t = max(1000.0, min(40000.0, float(kelvin))) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
    else:
        r = 329.698727446 * (t - 60) ** -0.1332047592
        g = 288.1221695283 * (t - 60) ** -0.0755148492
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    return (_clamp(r), _clamp(g), _clamp(b))


def _by_key(rows: Iterable[StateRow] | Mapping[Key, Any]) -> dict[Key, Any]:
    if isinstance(rows, Mapping):
        return dict(rows)
    return {r.key: r.value for r in rows if r.function_class not in TELEMETRY_CLASSES}


def _int(value: Any, default: int) -> int:
    try:
        return int(str(value).rstrip("K"))
    except (TypeError, ValueError):
        return default


def _level(state: Mapping[Key, Any]) -> int:
    return max(0, min(100, _int(state.get(("brightness", None)), 100)))


def _rgb_value(value: Any) -> RGB:
    inner = value.get("color-rgb", value) if isinstance(value, Mapping) else {}
    return (_int(inner.get("r"), 0), _int(inner.get("g"), 0), _int(inner.get("b"), 0))


def preset_palette(name: str) -> tuple[RGB, RGB, RGB]:
    """Known palette, or neutral grey when no color model exists. Never invent colors from a name."""
    return KNOWN_PRESETS.get(name, ((96, 96, 96),) * 3)


def sequence_preview(rows: Iterable[StateRow] | Mapping[Key, Any]) -> dict | None:
    """Describe an illustrative preview, never a stream of measured LED animation frames."""
    state = _by_key(rows)
    if str(state.get(("power", None), "on")) == "off" or _mode(state) != "sequence":
        return None
    name = str(state.get(("color-sequence", "preset")) or "unknown")
    styles = {"gradient-rainbow": "gradient", "rainbow-road": "travel",
              "rainbow-roll": "pulse-rainbow", "fade-3": "fade-black", "jump-3": "jump"}
    style = styles.get(name, "palette" if name in KNOWN_PRESETS else "unavailable")
    if name == "custom":
        name = str(state.get(("color-sequence", "custom")) or "unknown program")
        style = "program" if name in PROGRAMS else "unavailable"
    return {"name": name, "style": style, "brightness": _level(state),
            "speed": max(-10, min(10, _int(state.get(("speed", "color-sequence")), 0))),
            "animated": style in {"travel", "pulse-rainbow", "fade-black", "jump"},
            "available": style != "unavailable", "approximate": True, "synchronized": False}


def _mode(state: Mapping[Key, Any]) -> str:
    return str(state.get(("color-mode", None)) or "white")


def head_colors(
    rows: Iterable[StateRow] | Mapping[Key, Any], n_heads: int, caps: Capabilities | None = None
) -> list[dict]:
    """Per-head `{"r","g","b"}` approximating what a strand shows for these state rows (see module rules)."""
    if n_heads <= 0:
        return []
    state = _by_key(rows)
    if str(state.get(("power", None), "on")) == "off":
        return [dict(BLACK) for _ in range(n_heads)]
    level = _level(state) / 100.0
    mode = _mode(state)
    if mode == "color":
        c = _rgb(_scale(_rgb_value(state.get(("color-rgb", None))), level))
        return [dict(c) for _ in range(n_heads)]
    if mode == "individual":
        return _individual(state, n_heads, level, caps)
    if mode == "sequence":
        preset = str(state.get(("color-sequence", "preset")) or "")
        if preset == "custom":
            program = str(state.get(("color-sequence", "custom")) or "")
            if program not in PROGRAMS:
                return [_rgb(_scale((96, 96, 96), level)) for _ in range(n_heads)]
            kelvin, pct = PROGRAMS[program]
            c = _rgb(_scale(kelvin_to_rgb(kelvin), pct / 100.0))  # the program sets its own level
            return [dict(c) for _ in range(n_heads)]
        palette = preset_palette(preset or "unknown")
        if preset in {"gradient-rainbow", "rainbow-road", "rainbow-roll"}:
            pairs = max(1, math.ceil(n_heads / 2))
            return [_rgb(_scale(tuple(c * 255 for c in colorsys.hsv_to_rgb(
                (i // 2) / pairs, 1.0, 1.0)), level)) for i in range(n_heads)]
        if preset in {"fade-3", "jump-3"}:
            return [_rgb(_scale(palette[0], level)) for _ in range(n_heads)]
        return [_rgb(_scale(palette[i % 3], level)) for i in range(n_heads)]
    # white (and any mode we don't model) → tunable white
    kelvin = _int(state.get(("color-temperature", None)), 2700)
    c = _rgb(_scale(kelvin_to_rgb(kelvin), level))
    return [dict(c) for _ in range(n_heads)]


def _individual(
    state: Mapping[Key, Any], n_heads: int, level: float, caps: Capabilities | None
) -> list[dict]:
    slot = state.get(("color-individual", "custom"))
    entries = frame_entries(state.get((FRAME_CLASS, slot))) or []
    white = not (caps and caps.no_white_hint)
    out = []
    for i in range(n_heads):
        if i >= len(entries):
            out.append(dict(BLACK))
            continue
        e = entries[i]
        r, g, b = _scale((e.r, e.g, e.b), e.colorBrightness / 100.0)
        if white and e.cct and e.whiteBrightness:
            wr, wg, wb = _scale(kelvin_to_rgb(e.cct), e.whiteBrightness / 100.0)
            r, g, b = r + wr, g + wg, b + wb
        out.append(_rgb(_scale((r, g, b), level)))
    return out


def describe_look(
    rows: Iterable[StateRow] | Mapping[Key, Any], n_heads: int | None = None, caps: Capabilities | None = None
) -> str:
    """The look in words, e.g. "white 2700 K at 60%", "slot custom-1 (30 painted heads)",
    "preset snowfall (on-screen preview is approximate)"."""
    state = _by_key(rows)
    if str(state.get(("power", None), "on")) == "off":
        return "off"
    level = _level(state)
    at = f" at {level}%" if level != 100 else ""
    mode = _mode(state)
    if mode == "color":
        r, g, b = _rgb_value(state.get(("color-rgb", None)))
        return f"colour #{r:02X}{g:02X}{b:02X} at {level}%"
    if mode == "individual":
        slot = state.get(("color-individual", "custom"))
        entries = frame_entries(state.get((FRAME_CLASS, slot)))
        if not entries:
            return f"slot {slot} (no pattern stored){at}"
        if n_heads is not None and n_heads != len(entries):
            return f"slot {slot} ({len(entries)} controller entries; {n_heads} heads on the map){at}"
        return f"slot {slot} ({len(entries)} painted heads){at}"
    if mode == "sequence":
        preset = str(state.get(("color-sequence", "preset")) or "unknown")
        if preset == "custom":
            program = str(state.get(("color-sequence", "custom")) or "unknown")
            if program in PROGRAMS:
                kelvin, pct = PROGRAMS[program]
                return f"program {program}: {kelvin} K at {pct}% (on-screen preview is approximate)"
            return f"program {program} (on-screen preview is approximate)"
        return f"preset {preset}{at} (on-screen preview is approximate)"
    kelvin = _int(state.get(("color-temperature", None)), 2700)
    return f"white {kelvin} K at {level}%"
