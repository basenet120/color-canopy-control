"""Capability detection from a device's own function list (build implication 2 in the synthesis; 06 #1).

Everything the engine is allowed to send is derived from what the device declares — never hard-coded:
segment count, slot names, color modes, CCT range, presets, programs, speed range, party mode, restore-values.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from conductor.drivers.base import StateRow
from conductor.rows import FRAME_CLASS, SLOTS, frame_entries

_SEGMENT_HINT = re.compile(r"^(\d+)-segment$")


@dataclass
class Capabilities:
    power: bool = False
    brightness_range: tuple[int, int] | None = None
    cct_range: tuple[int, int] | None = None
    cct_step: int | None = None
    rgb: bool = False
    color_modes: list[str] = field(default_factory=list)
    presets: list[str] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    speed_range: tuple[int, int] | None = None
    slots: list[str] = field(default_factory=list)  # e.g. custom-1..3 (declared by color-individual)
    segment_hint: int | None = None  # from the color-individual "N-segment" hint
    framebuffer_lengths: dict[str, int] = field(default_factory=dict)  # per slot, from state readback
    no_white_hint: bool = False  # a "no-white" hint: per-puck white is not driven
    party_mode: bool = False
    restore_values: list[str] = field(default_factory=list)
    lock: bool = False
    lock_values: list[str] = field(default_factory=list)  # lock-control values the device lists
    outlets: list[str] = field(default_factory=list)  # power instances on multi-outlet plugs
    fan: bool = False
    fan_speeds: list[str] = field(
        default_factory=list
    )  # fan-speed steps, slowest first, without the "-000" off step

    @property
    def individual(self) -> bool:
        """Per-head paint is possible at all: an `individual` mode plus at least one slot."""
        return "individual" in self.color_modes and bool(self.slots)

    @property
    def framebuffer_length(self) -> int | None:
        """The device's frame length: the slots' readback length if consistent, else the segment hint."""
        lengths = set(self.framebuffer_lengths.values())
        if len(lengths) == 1:
            return lengths.pop()
        return self.segment_hint

    def head_count_problem(self, expected_heads: int | None) -> str | None:
        """Plain-English problem if the device's frame length doesn't match the registry's expected heads."""
        if expected_heads is None:
            return None
        lengths = set(self.framebuffer_lengths.values())
        if len(lengths) > 1:
            return f"slots report different frame lengths {sorted(lengths)} — per-head writes refused"
        n = self.framebuffer_length
        if n is None:
            return "the device reports no frame length — per-head writes refused"
        if n != expected_heads:
            return f"the device reports {n} heads but this module should have {expected_heads}"
        return None

    def to_json(self) -> dict:
        d = asdict(self)
        d["individual"] = self.individual
        d["framebuffer_length"] = self.framebuffer_length
        return d


def _values(fn: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [v for v in fn.get("values") or [] if isinstance(v, Mapping)]


def _names(fn: Mapping[str, Any]) -> list[str]:
    return [str(v["name"]) for v in _values(fn) if "name" in v]


def _range(fn: Mapping[str, Any]) -> tuple[int, int, int | None] | None:
    for v in _values(fn):
        r = v.get("range")
        if isinstance(r, Mapping) and "min" in r and "max" in r:
            return int(r["min"]), int(r["max"]), int(r["step"]) if r.get("step") else None
    r = fn.get("range")
    if isinstance(r, Mapping) and "min" in r:
        return int(r["min"]), int(r["max"]), int(r["step"]) if r.get("step") else None
    return None


def detect(functions: Iterable[Mapping[str, Any]], states: Iterable[StateRow] = ()) -> Capabilities:
    caps = Capabilities()
    for fn in functions:
        cls, inst = fn.get("functionClass"), fn.get("functionInstance")
        if cls == "power":
            caps.power = True
            if inst:
                caps.outlets.append(inst)
        elif cls == "brightness":
            r = _range(fn)
            caps.brightness_range = (r[0], r[1]) if r else (1, 100)
        elif cls == "color-temperature":
            r = _range(fn)
            if r:
                caps.cct_range, caps.cct_step = (r[0], r[1]), r[2]
        elif cls == "color-rgb":
            caps.rgb = True
        elif cls == "color-mode":
            caps.color_modes = _names(fn)
        elif cls == "color-sequence" and inst == "preset":
            caps.presets = [n for n in _names(fn) if n != "custom"]
        elif cls == "color-sequence" and inst == "custom":
            caps.programs = _names(fn)
        elif cls == "speed":
            r = _range(fn)
            if r:
                caps.speed_range = (r[0], r[1])
        elif cls == "color-individual":
            for v in _values(fn):
                name = str(v.get("name", ""))
                if name in SLOTS:
                    caps.slots.append(name)
                for hint in v.get("hints") or []:
                    if m := _SEGMENT_HINT.match(str(hint)):
                        caps.segment_hint = int(m.group(1))
                    if hint == "no-white":
                        caps.no_white_hint = True
        elif cls == "toggle" and inst == "party-mode":
            caps.party_mode = True
        elif cls == "restore-values":
            caps.restore_values = _names(fn)
        elif cls == "lock-control":
            caps.lock = True
            caps.lock_values = _names(fn)
        elif cls == "fan-speed":
            caps.fan = True
            # aioafero's FanController: every value except the "…-000" step, sorted (slowest first)
            caps.fan_speeds = sorted(n for n in _names(fn) if not n.endswith("-000"))
    caps.slots = sorted(set(caps.slots))
    for row in states:
        if row.function_class == FRAME_CLASS and row.function_instance in SLOTS:
            entries = frame_entries(row.value)
            if entries is not None:
                caps.framebuffer_lengths[row.function_instance] = len(entries)
    return caps
