"""Simulator device profiles: device dumps in aioafero's `AferoDevice` shape (see `profiles/README.md`).

Rule being implemented (04 "Cloud profile", 06 #1): until bench Gate A dumps a real SQHDK03, the canopy strand is
simulated from its closest public sibling — the Hampton Bay 20-segment string — **resized to 30 heads in code**,
changing every place the head count appears together: the six `"20-segment"` hints (three under
`capabilities[].color-individual._opts.categoryValues[].hints`, three under
`functions[].color-individual.values[].hints`), all three `color-sequence-v2` framebuffers, `default_image` and
the model name. A profile that doesn't have exactly those six hints is refused rather than half-resized.

Profiles:
- `sqhdk03-sim`  — the sibling string at 30 heads (the canopy default, `DEFAULT_PROFILE`).
- `string-20seg` — the fixture as-is (the 20-segment sibling string).
- `sqhdk03-sim-60` — the sibling string at 60 entries: the frame length the real SQHDK03 reports with 30
  pucks attached (VWBD bench). Opt-in (a registry entry's `profile:`); the default stays `sqhdk03-sim`. A module
  on it paints per head only through its own device-bound `framebuffer_mapping` (D25, D54); without one, per-head
  frames are refused.

Every call returns a fresh deep copy, so callers may mutate what they get.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

from conductor.drivers.base import DeviceInfo, StateRow
from conductor.rows import FRAME_CLASS

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"
SIBLING_FILE = "hampton-bay-string-20seg.json"
DEFAULT_PROFILE = "sqhdk03-sim"
SQHDK03_SIM_HEADS = 30
SQHDK03_REAL_ENTRIES = 60  # framebuffer entries the real kit reports (VWBD bench, 30 pucks attached)
SQHDK03_SIM_MODEL = "SQHDK03 (simulated from sibling string)"
HINTS_PER_PROFILE = 6  # 3 capabilities categoryValues + 3 function values

_SEGMENT = re.compile(r"(\d+)-segment")


class ProfileError(LookupError):
    """An unknown profile name, or a profile that can't be resized safely. Message is operator-safe."""


@cache
def _read(filename: str) -> dict:
    with open(PROFILE_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


def _individual_hint_lists(dump: Mapping[str, Any]) -> list[list]:
    """Every `hints` list attached to the color-individual function, in capabilities and in functions."""
    out: list[list] = []
    for cap in dump.get("capabilities") or []:
        if cap.get("functionClass") == "color-individual":
            for cv in (cap.get("_opts") or {}).get("categoryValues") or []:
                if isinstance(cv.get("hints"), list):
                    out.append(cv["hints"])
    for fn in dump.get("functions") or []:
        if fn.get("functionClass") == "color-individual":
            for v in fn.get("values") or []:
                if isinstance(v.get("hints"), list):
                    out.append(v["hints"])
    return out


def _frame_states(dump: Mapping[str, Any]) -> list[dict]:
    return [s for s in dump.get("states") or [] if s.get("functionClass") == FRAME_CLASS]


def resize_profile(dump: Mapping[str, Any], heads: int, *, model: str | None = None) -> dict:
    """A copy of `dump` with every head-count reference changed to `heads`.

    Framebuffers are extended by repeating the existing pattern (or truncated), so a slot keeps its look.
    Raises ProfileError unless exactly six segment hints and three slot framebuffers are found.
    """
    if not isinstance(heads, int) or not 1 <= heads <= 90:
        raise ProfileError(f"a simulated strand needs 1–90 heads, not {heads!r}")
    out = copy.deepcopy(dict(dump))
    hint_lists = _individual_hint_lists(out)
    changed = 0
    for hints in hint_lists:
        for i, hint in enumerate(hints):
            if isinstance(hint, str) and _SEGMENT.fullmatch(hint):
                hints[i] = f"{heads}-segment"
                changed += 1
    if changed != HINTS_PER_PROFILE:
        raise ProfileError(
            f"expected {HINTS_PER_PROFILE} segment hints on color-individual, found {changed}; "
            "this profile can't be resized safely"
        )
    frames = _frame_states(out)
    if len(frames) != 3:
        raise ProfileError(f"expected 3 slot framebuffers, found {len(frames)}; can't resize this profile")
    for state in frames:
        fb = state["value"][FRAME_CLASS]["frameBuffer"]["framebuffer"]
        if not fb:
            raise ProfileError(f"slot {state.get('functionInstance')} has an empty framebuffer; can't resize")
        fb[:] = [copy.deepcopy(fb[i % len(fb)]) for i in range(heads)]
    if isinstance(out.get("default_image"), str):
        out["default_image"] = _SEGMENT.sub(f"{heads}-segment", out["default_image"])
    if model is not None:
        out["model"] = model
    return out


def _build(name: str) -> dict:
    if name == "string-20seg":
        return copy.deepcopy(_read(SIBLING_FILE))
    if name == "sqhdk03-sim":
        return resize_profile(_read(SIBLING_FILE), SQHDK03_SIM_HEADS, model=SQHDK03_SIM_MODEL)
    if name == "sqhdk03-sim-60":
        return resize_profile(_read(SIBLING_FILE), SQHDK03_REAL_ENTRIES, model=SQHDK03_SIM_MODEL)
    raise ProfileError(f"no simulator profile called {name!r}; use one of {', '.join(profile_names())}")


@cache
def _built(name: str) -> dict:
    return _build(name)


def profile_names() -> list[str]:
    return ["sqhdk03-sim", "string-20seg", "sqhdk03-sim-60"]


def load_profile(name: str = DEFAULT_PROFILE) -> dict:
    """The device dump for a profile, in aioafero's AferoDevice shape. A fresh copy on every call."""
    return copy.deepcopy(_built(name))


def functions_and_states(dump: Mapping[str, Any]) -> tuple[list[dict], list[StateRow]]:
    """(functions, states) from a dump: functions in Afero's raw shape, states as StateRows."""
    functions = copy.deepcopy(list(dump.get("functions") or []))
    states = [StateRow.from_wire(copy.deepcopy(s)) for s in dump.get("states") or []]
    return functions, states


def profile_parts(name: str = DEFAULT_PROFILE) -> tuple[list[dict], list[StateRow]]:
    """(functions, states) for a named profile."""
    return functions_and_states(_built(name))


def frame_length(dump: Mapping[str, Any]) -> int | None:
    """The profile's framebuffer length (from its slot states), or None when slots disagree or are missing."""
    lengths = {
        len(s["value"][FRAME_CLASS]["frameBuffer"]["framebuffer"])
        for s in _frame_states(dump)
        if isinstance(s.get("value"), Mapping)
    }
    return lengths.pop() if len(lengths) == 1 else None


def device_info(
    name: str = DEFAULT_PROFILE,
    *,
    device_id: str = "",
    account: str = "sim",
    label: str = "",
    dump: Mapping[str, Any] | None = None,
) -> DeviceInfo:
    """A simulated DeviceInfo for a profile (or for `dump`, e.g. one whose states reflect current cloud state)."""
    raw = copy.deepcopy(dict(dump)) if dump is not None else load_profile(name)
    if device_id:
        raw["id"] = device_id
        raw["device_id"] = device_id
    if label:
        raw["friendly_name"] = label
    functions, states = functions_and_states(raw)
    return DeviceInfo(
        device_id=device_id or str(raw.get("id", "")),
        account=account,
        name=label or str(raw.get("friendly_name") or raw.get("default_name") or ""),
        model=str(raw.get("model", "")),
        device_class=str(raw.get("device_class", "")),
        functions=functions,
        states=states,
        raw=raw,
        simulated=True,
    )
