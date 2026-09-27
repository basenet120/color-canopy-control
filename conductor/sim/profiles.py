"""Simulator device profiles: device dumps in aioafero's `AferoDevice` shape (see `profiles/README.md`).

Rule being implemented (04 "Cloud profile", 06 #1): until bench Gate A dumps a real SQHDK03, the canopy strand is
simulated from its closest public sibling — the Hampton Bay 20-segment string — **resized to 30 heads in code**,
changing every place the head count appears together: the six `"20-segment"` hints (three under
`capabilities[].color-individual._opts.categoryValues[].hints`, three under
`functions[].color-individual.values[].hints`), all three `color-sequence-v2` framebuffers, `default_image` and
the model name. A profile that doesn't have exactly those six hints is refused rather than half-resized.

Profiles:
- `sqhdk03-sim`  — the sibling string at 30 heads (the canopy default, `DEFAULT_PROFILE`).
- `string-20seg` — the fixture as-is (the Glow Garden string).

Vignette profiles (P4), **synthesized from aioafero 9.3.0 controller source — UNVERIFIED until a real dump**. No
public dump exists for these exact models, so each is built in code from what aioafero's controllers read and write
(`aioafero/v1/controllers/light.py`, `switch.py`, `lock.py`, `fan.py`) and the device facts in docs/context/03:
- `ecosmart-a19-rgb` — EcoSmart A19 colour bulb (LightController): power, brightness 1–100, color-temperature
  (numeric; the A21 dump's 2200–6500 K / 100 K range), color-rgb, color-mode white|color|sequence, color-sequence
  preset + custom effects with speed −10…+10, restore-values. `default_image` is the one aioafero maps to
  12A19060WRGBWH2.
- `outdoor-plug-2` — Defiant HPPA52CWB (SwitchController): two independently switchable outlets, `power` with
  functionInstance `outlet-1` / `outlet-2` (instance names UNVERIFIED).
- `indoor-plug` — a single-outlet plug (SwitchController, `power` with no instance) for the bedroom humidifier.
- `defiant-deadbolt` — Defiant Wi-Fi deadbolt (LockController): `lock-control`. aioafero writes `locking` /
  `unlocking` and reads `locked` / `locking` / `unlocked` / `unlocking` as the reported position (anything else is
  "unknown"); the SimDriver models the bolt settling from `locking` to `locked` once the device applies it.
- `ceiling-fan` — Hampton Bay Zandra-style fan (FanController): `power`/`fan-power` and `fan-speed`/`fan-speed` with
  the six `fan-speed-6-NNN` steps (+ `-000`, which aioafero leaves out), and `fan-reverse`.
- `path-lights` — a plain dimmable light (LightController with only power + brightness).

Every call returns a fresh deep copy, so callers may mutate what they get.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable, Mapping
from functools import cache
from pathlib import Path
from typing import Any

from conductor.drivers.base import DeviceInfo, StateRow
from conductor.rows import FRAME_CLASS

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"
SIBLING_FILE = "hampton-bay-string-20seg.json"
DEFAULT_PROFILE = "sqhdk03-sim"
SQHDK03_SIM_HEADS = 30
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


# ---- vignette profiles, synthesized in code (UNVERIFIED until a real dump) --------------------------------------
SYNTHESIZED_NOTE = "synthesized from aioafero 9.3.0 controller source — UNVERIFIED until a real dump"
A19_PRESETS = ("flash", "fade-3", "fade-7", "jump-3", "jump-7", "custom")  # the A21 dump (docs/context/03)
A19_PROGRAMS = (
    "sleep",
    "july-4th",
    "clarity",
    "focus",
    "wake-up",
    "getting-ready",
    "rainbow",
    "chill",
    "dinner-party",
    "moonlight",
    "valentines-day",
    "christmas",
    "nightlight",
)
FAN_SPEEDS = (
    "fan-speed-6-000",
    "fan-speed-6-016",
    "fan-speed-6-033",
    "fan-speed-6-050",
    "fan-speed-6-066",
    "fan-speed-6-083",
    "fan-speed-6-100",
)
LOCK_POSITIONS = (
    "locked",
    "locking",
    "unlocked",
    "unlocking",
)  # aioafero CurrentPositionEnum minus "unknown"


def _category(cls: str, names: tuple[str, ...], instance: str | None = None) -> dict:
    fn: dict[str, Any] = {
        "functionClass": cls,
        "type": "category",
        "values": [{"name": n, "range": {}} for n in names],
    }
    if instance is not None:
        fn["functionInstance"] = instance
    return fn


def _numeric(cls: str, lo: int, hi: int, step: int, instance: str | None = None) -> dict:
    fn: dict[str, Any] = {
        "functionClass": cls,
        "type": "numeric",
        "values": [{"name": cls, "range": {"min": lo, "max": hi, "step": step}}],
    }
    if instance is not None:
        fn["functionInstance"] = instance
    return fn


def _object(cls: str) -> dict:
    return {"functionClass": cls, "type": "object", "values": [{"name": cls, "range": {}}]}


def _state(cls: str, value: Any, instance: str | None = None) -> dict:
    return {"functionClass": cls, "functionInstance": instance, "value": value, "lastUpdateTime": 0}


def _device(name: str, *, functions: list[dict], states: list[dict], **identity: str) -> dict:
    """A dump in aioafero's AferoDevice shape. Ids are fixture placeholders, never real UUIDs."""
    return {
        "id": f"sim-profile-{name}",
        "device_id": f"sim-profile-{name}-device",
        "model": identity["model"],
        "device_class": identity["device_class"],
        "default_name": identity["default_name"],
        "default_image": identity["default_image"],
        "friendly_name": identity["default_name"],
        "capabilities": [],
        "functions": functions,
        "states": [
            *states,
            _state("available", True),
            _state("visible", True),
            _state("direct", True),
            _state("wifi-rssi", -55),
            _state("wifi-steady-state", "connected"),
        ],
        "children": [],
        "manufacturerName": identity["manufacturer"],
        "split_identifier": None,
        "version_data": None,
    }


def _ecosmart_a19_rgb() -> dict:
    return _device(
        "ecosmart-a19-rgb",
        model="12A19060WRGBWH1 (synthesized)",
        device_class="light",
        default_name="A19 Color Bulb",
        default_image="a19-e26-color-cct-60w-smd-frosted-icon",  # aioafero maps this image to 12A19060WRGBWH2
        manufacturer="EcoSmart",
        functions=[
            _category("power", ("off", "on")),
            _numeric("brightness", 1, 100, 1),
            _numeric("color-temperature", 2200, 6500, 100),
            _object("color-rgb"),
            _category("color-mode", ("white", "color", "sequence")),
            _category("color-sequence", A19_PRESETS, "preset"),
            _category("color-sequence", A19_PROGRAMS, "custom"),
            _numeric("speed", -10, 10, 1, "color-sequence"),
            _category("restore-values", ("partial-restore", "not-restored", "restored")),
        ],
        states=[
            _state("power", "on"),
            _state("brightness", 100),
            _state("color-temperature", 2700),
            _state("color-rgb", {"color-rgb": {"r": 249, "g": 99, "b": 2}}),
            _state("color-mode", "white"),
            _state("color-sequence", "fade-3", "preset"),
            _state("color-sequence", "focus", "custom"),
            _state("speed", 0, "color-sequence"),
            _state("restore-values", "partial-restore"),
        ],
    )


def _outdoor_plug_2() -> dict:
    return _device(
        "outdoor-plug-2",
        model="HPPA52CWB (synthesized)",
        device_class="power-outlet",
        default_name="Outdoor Plug",
        default_image="smart-outdoor-plug-2-outlet-icon",
        manufacturer="Defiant",
        functions=[
            _category("power", ("off", "on"), "outlet-1"),
            _category("power", ("off", "on"), "outlet-2"),
        ],
        states=[_state("power", "off", "outlet-1"), _state("power", "off", "outlet-2")],
    )


def _indoor_plug() -> dict:
    return _device(
        "indoor-plug",
        model="HPPA11CWB (synthesized)",
        device_class="power-outlet",
        default_name="Smart Plug",
        default_image="smart-plug-icon",
        manufacturer="Defiant",
        functions=[_category("power", ("off", "on"))],
        states=[_state("power", "off")],
    )


def _defiant_deadbolt() -> dict:
    return _device(
        "defiant-deadbolt",
        model="HSGC9X2D01AJ (synthesized)",
        device_class="door-lock",
        default_name="Deadbolt",
        default_image="door-lock-icon",
        manufacturer="Defiant",
        functions=[_category("lock-control", LOCK_POSITIONS)],
        states=[_state("lock-control", "locked")],
    )


def _ceiling_fan() -> dict:
    return _device(
        "ceiling-fan",
        model="Zandra (synthesized)",
        device_class="fan",
        default_name="Ceiling Fan",
        default_image="ceiling-fan-chandra-icon",
        manufacturer="Hampton Bay",
        functions=[
            _category("power", ("off", "on"), "fan-power"),
            _category("fan-speed", FAN_SPEEDS, "fan-speed"),
            _category("fan-reverse", ("forward", "reverse"), "fan-reverse"),
        ],
        states=[
            _state("power", "off", "fan-power"),
            _state("fan-speed", "fan-speed-6-033", "fan-speed"),
            _state("fan-reverse", "forward", "fan-reverse"),
        ],
    )


def _path_lights() -> dict:
    return _device(
        "path-lights",
        model="Path light set (synthesized)",
        device_class="light",
        default_name="Path Lights",
        default_image="landscape-path-light-icon",
        manufacturer="Hampton Bay",
        functions=[_category("power", ("off", "on")), _numeric("brightness", 1, 100, 1)],
        states=[_state("power", "off"), _state("brightness", 100)],
    )


SYNTHESIZED: dict[str, Callable[[], dict]] = {
    "ecosmart-a19-rgb": _ecosmart_a19_rgb,
    "outdoor-plug-2": _outdoor_plug_2,
    "indoor-plug": _indoor_plug,
    "defiant-deadbolt": _defiant_deadbolt,
    "ceiling-fan": _ceiling_fan,
    "path-lights": _path_lights,
}


def _build(name: str) -> dict:
    if name == "string-20seg":
        return copy.deepcopy(_read(SIBLING_FILE))
    if name == "sqhdk03-sim":
        return resize_profile(_read(SIBLING_FILE), SQHDK03_SIM_HEADS, model=SQHDK03_SIM_MODEL)
    if name in SYNTHESIZED:
        return SYNTHESIZED[name]()
    raise ProfileError(f"no simulator profile called {name!r}; use one of {', '.join(profile_names())}")


@cache
def _built(name: str) -> dict:
    return _build(name)


def profile_names() -> list[str]:
    return ["sqhdk03-sim", "string-20seg", *SYNTHESIZED]


def is_synthesized(name: str) -> bool:
    """True for the vignette profiles built in code from aioafero's controller source (UNVERIFIED)."""
    return name in SYNTHESIZED


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
