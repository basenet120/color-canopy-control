"""Row builders, readback comparison and the canonical slot hash — the payload vocabulary (04, 07 §4–§6).

Nothing here invents a payload shape: every builder mirrors a shape from `04` (the verified offline PUT) or
aioafero 9.3.0's wire encodings. Frame writes copy the header from the device's own readback and change only
colours and bench-enabled effect fields.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from conductor.drivers.base import TELEMETRY_CLASSES, StateRow

SLOTS = ("custom-1", "custom-2", "custom-3")
FRAME_CLASS = "color-sequence-v2"
FRAME_FIELDS = ("r", "g", "b", "cct", "colorBrightness", "whiteBrightness")
# Header fields copied verbatim from readback; effect fields may change only when bench-enabled.
HEADER_COPY_FIELDS = ("id", "version", "headerFlags", "sequenceFlags")
EFFECT_FIELDS = ("brightnessDepth", "brightnessEffect", "brightnessSpeed", "motionEffect", "motionSpeed")
DEFAULT_HEADER = {
    "id": 0,
    "version": 1,
    "headerFlags": 128,
    "sequenceFlags": 0,
    "brightnessDepth": 100,
    "brightnessEffect": 0,
    "brightnessSpeed": 50,
    "motionEffect": 0,
    "motionSpeed": 50,
}


class PayloadError(ValueError):
    """A value that must not be sent (out of range, wrong length, unknown slot). Message is operator-safe."""


# ---- frame entries ---------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class FrameEntry:
    """One puck. r/g/b 0–255; cct 0 (no white) or a Kelvin value; brightnesses 0–100."""

    r: int = 0
    g: int = 0
    b: int = 0
    cct: int = 0
    colorBrightness: int = 100
    whiteBrightness: int = 0

    def validated(self, cct_range: tuple[int, int] | None = None) -> FrameEntry:
        for name in ("r", "g", "b"):
            v = getattr(self, name)
            if not isinstance(v, int) or not 0 <= v <= 255:
                raise PayloadError(f"{name}={v!r} must be a whole number 0–255")
        for name in ("colorBrightness", "whiteBrightness"):
            v = getattr(self, name)
            if not isinstance(v, int) or not 0 <= v <= 100:
                raise PayloadError(f"{name}={v!r} must be a whole number 0–100")
        if not isinstance(self.cct, int) or self.cct < 0:
            raise PayloadError(f"cct={self.cct!r} must be 0 (no white) or a Kelvin value")
        if self.cct and cct_range and not cct_range[0] <= self.cct <= cct_range[1]:
            raise PayloadError(
                f"cct={self.cct} is outside this device's range {cct_range[0]}–{cct_range[1]} K"
            )
        return self

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> FrameEntry:
        return cls(**{k: int(d.get(k, getattr(cls, k))) for k in FRAME_FIELDS})


# An entry no puck shows: every field 0. Frame entries outside a module's physical heads are written as this, so a
# padded frame is deterministic (same heads in → same slot hash out).
OFF_ENTRY = FrameEntry(0, 0, 0, 0, 0, 0)


@dataclass(frozen=True)
class FrameLayout:
    """Where a module's physical heads sit in the device's framebuffer.

    The controller's framebuffer can be longer than the strand (the real SQHDK03 reports 60 entries — the most
    pucks one controller supports — with 30 pucks attached, VWBD workshop Sep 27 2026). Puck i is driven by entry
    `offset + i`; every other entry is written OFF. Which entries drive the pucks is UNVERIFIED beyond the working
    assumption (entries 0–29): `frame_offset` in the registry corrects it from the bench without code.
    """

    length: int  # framebuffer entries the device reports
    heads: int  # physical heads (pucks) on the module
    offset: int = 0  # the entry that drives puck 0

    def __post_init__(self) -> None:
        if self.heads < 1 or self.offset < 0 or self.offset + self.heads > self.length:
            raise PayloadError(
                f"{self.heads} pucks from entry {self.offset} don't fit a {self.length}-entry framebuffer"
            )

    @property
    def end(self) -> int:
        """One past the last entry that drives a puck."""
        return self.offset + self.heads

    @property
    def padded(self) -> bool:
        """The frame holds entries no puck uses."""
        return self.length != self.heads

    def place(self, heads: Sequence[FrameEntry]) -> list[FrameEntry]:
        """A full-length frame: `heads` (one per puck, wire order) at [offset, offset+heads), OFF elsewhere."""
        if len(heads) != self.heads:
            raise PayloadError(f"{len(heads)} head colours for a module with {self.heads} pucks; refusing")
        out = [OFF_ENTRY] * self.length
        out[self.offset : self.end] = list(heads)
        return out

    def physical(self, entries: Sequence[FrameEntry]) -> list[FrameEntry]:
        """The entries that drive the pucks, puck 0 first. PayloadError when the frame is too short."""
        if len(entries) < self.end:
            raise PayloadError(
                f"the frame holds {len(entries)} entries but this module's pucks use entries "
                f"{self.offset}–{self.end - 1}"
            )
        return list(entries[self.offset : self.end])

    def describe(self) -> str:
        """e.g. "60-entry pattern; pucks use 0–29" (the page's small note)."""
        return f"{self.length}-entry pattern; pucks use {self.offset}–{self.end - 1}"


def frame_value(
    header_from_readback: Mapping[str, Any] | None,
    entries: Sequence[FrameEntry],
    *,
    expected_length: int | None,
    effects: Mapping[str, int] | None = None,
    cct_range: tuple[int, int] | None = None,
) -> dict:
    """A `{"color-sequence-v2": {...}}` value for one slot.

    - The header (and `frameBuffer.flags`) is copied from the device's own readback of that slot.
    - `expected_length` is the device's framebuffer length; a frame of any other length is refused.
    - `effects` may set only EFFECT_FIELDS — callers pass only bench-enabled codes (config `effects.enabled`).
    """
    source = header_from_readback or {"color-sequence-v2": {**DEFAULT_HEADER, "frameBuffer": {"flags": 0}}}
    inner = source.get(FRAME_CLASS, source)
    if expected_length is not None and len(entries) != expected_length:
        raise PayloadError(
            f"frame has {len(entries)} entries but this device's framebuffer has {expected_length}; refusing"
        )
    header = {k: inner.get(k, DEFAULT_HEADER[k]) for k in (*HEADER_COPY_FIELDS, *EFFECT_FIELDS)}
    for k, v in (effects or {}).items():
        if k not in EFFECT_FIELDS:
            raise PayloadError(f"{k} is not an effect field")
        header[k] = int(v)
    fb_flags = (inner.get("frameBuffer") or {}).get("flags", 0)
    return {
        FRAME_CLASS: {
            **header,
            "frameBuffer": {
                "flags": fb_flags,
                "framebuffer": [e.validated(cct_range).to_json() for e in entries],
            },
        }
    }


def frame_entries(value: Any) -> list[FrameEntry] | None:
    """Entries of a slot object from readback, or None when the value isn't a slot object."""
    if not isinstance(value, Mapping):
        return None
    inner = value.get(FRAME_CLASS, value)
    fb = (inner.get("frameBuffer") or {}).get("framebuffer") if isinstance(inner, Mapping) else None
    if not isinstance(fb, list):
        return None
    try:
        return [FrameEntry.from_json(e) for e in fb]
    except (TypeError, ValueError):
        return None


# ---- row builders ----------------------------------------------------------------------------------------------
def power(on: bool = True) -> StateRow:
    return StateRow("power", None, "on" if on else "off")


def color_mode(mode: str) -> StateRow:
    return StateRow("color-mode", None, mode)


def brightness(pct: int) -> StateRow:
    if not isinstance(pct, int) or not 1 <= pct <= 100:
        raise PayloadError(f"brightness={pct!r} must be a whole number 1–100")
    return StateRow("brightness", None, pct)


def color_temperature(kelvin: int, cct_range: tuple[int, int] | None = None) -> StateRow:
    """aioafero's wire encoding is a string ("2700"). Checked against the device range — never snapped."""
    if cct_range and not cct_range[0] <= kelvin <= cct_range[1]:
        raise PayloadError(f"{kelvin} K is outside this device's range {cct_range[0]}–{cct_range[1]} K")
    return StateRow("color-temperature", None, str(int(kelvin)))


def color_rgb(r: int, g: int, b: int) -> StateRow:
    FrameEntry(r, g, b).validated()
    return StateRow("color-rgb", None, {"color-rgb": {"r": r, "g": g, "b": b}})


def preset(name: str) -> StateRow:
    return StateRow("color-sequence", "preset", name)


def program(name: str) -> list[StateRow]:
    """Named on-device programs (attr 300): preset `custom` + color-sequence/custom `<name>`."""
    return [preset("custom"), StateRow("color-sequence", "custom", name)]


def speed(value: int) -> StateRow:
    if not isinstance(value, int) or not -10 <= value <= 10:
        raise PayloadError(f"speed={value!r} must be a whole number −10…+10")
    return StateRow("speed", "color-sequence", value)


def party_mode_off() -> StateRow:
    return StateRow("toggle", "party-mode", "off")


def slot_select(slot: str) -> list[StateRow]:
    if slot not in SLOTS:
        raise PayloadError(f"unknown slot {slot!r}; use one of {', '.join(SLOTS)}")
    return [StateRow("color-individual", "custom", slot), color_mode("individual")]


def slot_write(slot: str, value: dict) -> StateRow:
    if slot not in SLOTS:
        raise PayloadError(f"unknown slot {slot!r}; use one of {', '.join(SLOTS)}")
    return StateRow(FRAME_CLASS, slot, value)


def dedupe(rows: Iterable[StateRow]) -> list[StateRow]:
    """Last write wins per (class, instance); order of first appearance kept (row order inside one PUT)."""
    out: dict[tuple[str, str | None], StateRow] = {}
    for r in rows:
        out.pop(r.key, None)
        out[r.key] = r
    return list(out.values())


# ---- looks → full state vectors ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class Look:
    """A whole-module target. `kind` picks the selector; the rest are the look's parameters.

    kinds: "white" (cct, brightness), "rgb" (rgb, brightness), "preset" (preset, speed, brightness),
    "program" (program, brightness), "slot" (slot, brightness, speed), "off".
    """

    kind: str
    brightness: int = 100
    cct: int | None = None
    rgb: tuple[int, int, int] | None = None
    preset: str | None = None
    program: str | None = None
    slot: str | None = None
    speed: int = 0

    def to_json(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> Look:
        d = dict(d)
        if d.get("rgb") is not None:
            d["rgb"] = tuple(d["rgb"])
        return cls(**d)


def full_state_vector(look: Look, *, cct_range: tuple[int, int] | None = None) -> list[StateRow]:
    """The writable rows that define a look (07 §5): power, color-mode, the selector, brightness, speed, party
    off. Telemetry rows never appear. Slot objects are not included — only a slot *write* carries one."""
    if look.kind == "off":
        return [power(False)]
    rows: list[StateRow] = [power(True)]
    if look.kind == "white":
        if look.cct is None:
            raise PayloadError("a white look needs a colour temperature")
        rows += [color_mode("white"), color_temperature(look.cct, cct_range)]
    elif look.kind == "rgb":
        if look.rgb is None:
            raise PayloadError("an RGB look needs a colour")
        rows += [color_mode("color"), color_rgb(*look.rgb)]
    elif look.kind == "preset":
        if not look.preset:
            raise PayloadError("a preset look needs a preset name")
        rows += [color_mode("sequence"), preset(look.preset)]
    elif look.kind == "program":
        if not look.program:
            raise PayloadError("a program look needs a program name")
        rows += [color_mode("sequence"), *program(look.program)]
    elif look.kind == "slot":
        if not look.slot:
            raise PayloadError("a slot look needs a slot")
        rows += slot_select(look.slot)
    else:
        raise PayloadError(f"unknown look kind {look.kind!r}")
    rows += [brightness(look.brightness), speed(look.speed), party_mode_off()]
    return dedupe(rows)


# ---- comparison ------------------------------------------------------------------------------------------------
def _norm(function_class: str, value: Any) -> Any:
    if function_class in ("color-temperature", "brightness", "speed"):
        try:
            return int(str(value).rstrip("K"))
        except (TypeError, ValueError):
            return value
    if function_class == "color-rgb" and isinstance(value, Mapping):
        inner = value.get("color-rgb", value)
        return tuple(int(inner.get(k, -1)) for k in ("r", "g", "b"))
    if function_class == FRAME_CLASS:
        if isinstance(value, Mapping) and isinstance(value.get(SLOT_HASH_KEY), str):
            return value[SLOT_HASH_KEY]  # an expectation that names only the canonical hash
        return slot_hash(value)
    return value


@dataclass(frozen=True)
class Mismatch:
    function_class: str
    function_instance: str | None
    expected: Any
    reported: Any  # None when the row is missing
    missing: bool = False

    def to_json(self) -> dict:
        return {
            "functionClass": self.function_class,
            "functionInstance": self.function_instance,
            "expected": self.expected,
            "reported": self.reported,
            "missing": self.missing,
        }


def compare(expected: Iterable[StateRow], reported: Iterable[StateRow]) -> list[Mismatch]:
    """Mismatches between what we sent and what readback shows (07 §5).

    Every expected row must be present — a missing row is a mismatch, never a match (fetch_device_states can
    drop malformed rows or return [] on a 200). Extra reported rows are tolerated. Telemetry rows are ignored.
    Values are normalized (CCT as int, RGB as a tuple, slot objects by canonical hash).
    """
    got = {r.key: r.value for r in reported}
    out = []
    for row in expected:
        if row.function_class in TELEMETRY_CLASSES:
            continue
        if row.key not in got:
            out.append(Mismatch(row.function_class, row.function_instance, row.value, None, missing=True))
            continue
        if _norm(row.function_class, row.value) != _norm(row.function_class, got[row.key]):
            out.append(Mismatch(row.function_class, row.function_instance, row.value, got[row.key]))
    return out


# ---- canonical slot hash ----------------------------------------------------------------------------------------
def canonical_frame(value: Any) -> list[list[int]] | None:
    """The canonical projection of a slot object: framebuffer entries in order, fields r,g,b,cct,
    colorBrightness,whiteBrightness (07 §6). Header fields are deliberately excluded."""
    entries = frame_entries(value)
    if entries is None:
        return None
    return [[getattr(e, f) for f in FRAME_FIELDS] for e in entries]


def slot_hash(value: Any) -> str | None:
    proj = canonical_frame(value)
    if proj is None:
        return None
    return hashlib.sha256(json.dumps(proj, separators=(",", ":")).encode()).hexdigest()[:16]


# An expected slot row that names only the canonical hash (a slot commissioned earlier: the readback hash recorded
# then is what "correct" means, not a frame compiled now). Never sent — `compare` reads it; it lives in `expect`.
SLOT_HASH_KEY = "canonicalHash"
# The effect fields that make a saved pattern animate (0 = static). Not part of the hash (07 §6): verified apart.
ANIMATING_FIELDS = ("motionEffect", "brightnessEffect")


def slot_hash_expectation(slot: str, digest: str) -> StateRow:
    if slot not in SLOTS:
        raise PayloadError(f"unknown slot {slot!r}; use one of {', '.join(SLOTS)}")
    return StateRow(FRAME_CLASS, slot, {SLOT_HASH_KEY: digest})


def slot_effects(value: Any) -> dict[str, int] | None:
    """The header effect fields of a slot object (readback or compiled), or None when it isn't a slot object."""
    if not isinstance(value, Mapping):
        return None
    inner = value.get(FRAME_CLASS, value)
    if not isinstance(inner, Mapping) or not isinstance(inner.get("frameBuffer"), Mapping):
        return None
    return {k: int(inner[k]) for k in EFFECT_FIELDS if isinstance(inner.get(k), int | float)}


def animates(effects: Mapping[str, Any] | None) -> bool | None:
    """Whether these effect fields make the pattern move or pulse (None when unknown)."""
    if effects is None:
        return None
    return any(int(effects.get(k) or 0) != 0 for k in ANIMATING_FIELDS)


def effects_differ(expected: Mapping[str, Any] | None, got: Mapping[str, Any] | None) -> list[str]:
    """The animating effect fields (ANIMATING_FIELDS) whose values differ. [] when either side is unknown."""
    if not expected or got is None:
        return []
    return [k for k in ANIMATING_FIELDS if int(expected.get(k) or 0) != int(got.get(k) or 0)]


def payload_bytes(device_id: str, rows: Sequence[StateRow], last_update_ms: int) -> int:
    body = {"metadeviceId": device_id, "values": [r.to_wire(last_update_ms) for r in rows]}
    return len(json.dumps(body, separators=(",", ":")).encode())


def deep_copy_value(value: Any) -> Any:
    return copy.deepcopy(value)
