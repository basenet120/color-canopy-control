"""Vignettes (P4): the four staff rooms' device actions and scenes, compiled to rows and sent (01, 06 #11/#18, 07).

Rules being implemented:
- **Stock-app parity only** (01 scope): power, brightness, colour, white temperature, the device's own effects and
  presets (with speed), a string's saved patterns, outlet on/off, fan speed, lock/unlock. Nothing more.
- **Validated against the device's own profile** before anything is sent (capability detection, 06 #1): a category
  value must be one the device lists, a number must be inside its range and on its step. Otherwise a plain
  `VignetteError` and nothing is sent. A colour temperature is range-checked, never snapped (07 §4).
- **Wire encodings are aioafero 9.3.0's** (07 §4, D7), read from its controllers: power `"on"`/`"off"` on the
  function's own instance (each outlet of a plug is one instance — SwitchController); colour temperature as a string
  (`"2700"`); `{"color-rgb": {r, g, b}}` with color-mode `color`; effects as color-sequence preset `<name>`, or
  preset `custom` + color-sequence/custom `<name>` (EffectFeature); fan speed as the fan-speed value name
  (FanController); lock-control `"locking"` / `"unlocking"` (LockController). Row order follows aioafero's put
  models (power, colour, color-mode, colour temperature, brightness, effect, speed).
- **One command per device = one PUT of the device's full rows** (07 §5 full state vector: every writable row that
  defines what the device is showing, so a re-send restores the whole look), through
  `target(device_id, Purpose.ENGINE).executor.submit(…, verify=True)`: vignettes are show traffic on their own
  accounts, under the same live gate, rate budget and readback as the canopy. A scene or a group action is one
  command per device, sent together.
- **Locks**: `lock` / `unlock` need `confirm=True` (else `ConfirmRequired`), are never part of a scene, and are
  verified against the settled position the lock reports (`locked` / `unlocked`), not the `locking` command.
- **Honest status**: the page shows the state tracker's Ready / Updating / Needs attention per device. When the page
  first reads a device, the look it is already showing becomes the desired look (so Ready means the device shows
  what the page shows), unless that look can't be expressed in this page's rows — then the device stays "not
  checked" until someone changes it. A device re-registered to a new address (a swapped bulb) is read again before
  anything is shown or sent: its old address's status and look never stand in for the new one (review V2).
- **Group actions skip what can't take them** (review V3): the "All lamps" card sends to every lamp that can take the
  action and lists the rest as skipped with the reason (like a scene); it refuses only when no lamp can.
- **Tablets see only look rows** (review V7, 07 §4–§5): the page's `desired` / `reported` carry only the rows that
  define a look (`LOOK_CLASSES`) — never telemetry (wifi-ssid, MAC addresses, geo-coordinates, …), slot pattern
  objects or driver addresses.

Seams: scenes are data (`config/vignettes.yaml`); the synthesized simulator profiles are UNVERIFIED until real dumps.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from conductor import rows as R
from conductor.capabilities import Capabilities, detect
from conductor.config import (
    CONFIG_DIR,
    VIGNETTE_KINDS,
    ConfigError,
    DeviceEntry,
    Registry,
    _explain,
    _read_yaml,
)
from conductor.drivers.base import TELEMETRY_CLASSES, Outcome, ReadResult, StateRow
from conductor.executor_api import Command, CommandResult, CommandStatus
from conductor.gate import Purpose

log = logging.getLogger(__name__)

SERVICE_KEY = "vignettes"
TOPIC_PREFIX = "vignette"
Key = tuple[str, str | None]
Kind = Literal["bulb", "plug", "lock", "fan", "string", "path"]

# What each kind can do on the page — the stock Hubspace app's controls for that device, nothing more (01).
ACTIONS: dict[str, tuple[str, ...]] = {
    "bulb": ("power", "brightness", "color_temperature", "white", "color", "effect", "speed"),
    "string": ("power", "brightness", "color_temperature", "white", "color", "effect", "speed", "slot"),
    "path": ("power", "brightness"),
    "plug": ("power", "outlets"),
    "fan": ("power", "fan_speed"),
    "lock": ("lock", "unlock"),
}
LOCK_ACTIONS = ("lock", "unlock")
LOCK_WRITE = {"lock": "locking", "unlock": "unlocking"}  # aioafero LockController.lock() / .unlock()
LOCK_SETTLED = {"lock": "locked", "unlock": "unlocked"}  # what the lock reports once the bolt has moved
MAIN_OUTLET = "power"  # the outlet key for a plug whose power function has no instance
DEFAULT_RGB = (249, 99, 2)  # Home Depot orange
DEFAULT_CCT = 2700
# The rows a vignette look is made of — all a tablet (and the vignette:<room> topic) is ever shown (review V7).
LOOK_CLASSES = frozenset(
    {
        "power",
        "color-mode",
        "color-temperature",
        "color-rgb",
        "brightness",
        "color-sequence",
        "color-individual",
        "speed",
        "fan-speed",
        "lock-control",
        "toggle",
    }
)
KIND_NOUN = {
    "bulb": "Lamps",
    "string": "String lights",
    "path": "Path lights",
    "plug": "Plugs",
    "fan": "Fans",
    "lock": "Locks",
}


class VignetteError(ValueError):
    """An action or scene that can't be sent. The message is plain English, safe to show on the tablet."""


class ConfirmRequired(VignetteError):
    """A lock action without `confirm: true`."""


# ---- config: config/vignettes.yaml ------------------------------------------------------------------------------
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Swatch(_Strict):
    label: str
    hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class WhitePreset(_Strict):
    label: str
    kelvin: int = Field(ge=1000, le=10000)


class SceneStep(_Strict):
    device: str | None = None
    kind: Kind | None = None
    set: dict[str, Any] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_target(self) -> SceneStep:
        if (self.device is None) == (self.kind is None):
            raise ValueError("a scene step needs exactly one of `device:` or `kind:`")
        if any(a in LOCK_ACTIONS for a in self.set) or self.kind == "lock":
            raise ValueError("locks can't be part of a scene: they need a confirm on the tablet")
        for action, value in self.set.items():
            problem = _static_problem(action, value)
            if problem:
                raise ValueError(f"`{action}: {value!r}` — {problem}")
        return self


class Scene(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")
    label: str = Field(min_length=1, max_length=40)
    steps: list[SceneStep] = Field(min_length=1)


class RoomConfig(_Strict):
    space: str
    title: str
    outlets: dict[str, dict[str, str]] = Field(default_factory=dict)  # device id → {outlet key: label}
    scenes: list[Scene] = Field(default_factory=list)


class VignettesFile(_Strict):
    palette: list[Swatch] = Field(default_factory=list)
    whites: list[WhitePreset] = Field(default_factory=list)
    rooms: dict[str, RoomConfig]

    @model_validator(mode="after")
    def _unique_scenes(self) -> VignettesFile:
        for room_id, room in self.rooms.items():
            ids = [s.id for s in room.scenes]
            if len(ids) != len(set(ids)):
                raise ValueError(f"room {room_id!r} has two scenes with the same id")
        return self


def _static_problem(action: str, value: Any) -> str | None:
    """Type checks that don't need the device (its ranges and lists are checked when the scene runs)."""
    known = {a for acts in ACTIONS.values() for a in acts}
    if action not in known:
        return f"not an action; use one of {', '.join(sorted(known - set(LOCK_ACTIONS)))}"
    if action in ("power", "white") and not isinstance(value, bool):
        return "needs true or false"
    if action in ("brightness", "color_temperature", "speed") and not _is_int(value):
        return "needs a whole number"
    if action == "color" and _parse_rgb(value) is None:
        return 'needs a colour like "#F96302"'
    if action in ("effect", "slot", "fan_speed") and not isinstance(value, str):
        return "needs a name"
    if action == "outlets" and not (
        isinstance(value, Mapping) and value and all(isinstance(v, bool) for v in value.values())
    ):
        return "needs {<outlet>: true|false}"
    return None


def load_vignettes(config_dir: Path = CONFIG_DIR) -> VignettesFile:
    path = Path(config_dir) / "vignettes.yaml"
    raw = _read_yaml(path)
    try:
        return VignettesFile.model_validate(raw)
    except ValidationError as e:
        raise ConfigError(_explain(path, e)) from None


def check_against_registry(
    rooms: VignettesFile, registry: Registry, path: str = "config/vignettes.yaml"
) -> None:
    """Cross-check the rooms file with the registry: every named device is a vignette device in that room, and
    every vignette device's room is listed. Raises ConfigError with a message a technician can act on."""
    problems: list[str] = []
    vignette_devices = {d.id: d for d in registry.file.devices if d.role == "vignette"}
    for d in vignette_devices.values():
        if d.vignette not in rooms.rooms:
            problems.append(
                f"device {d.id!r} is in room {d.vignette!r}, which {path} doesn't list under rooms"
            )
    for room_id, room in rooms.rooms.items():
        for device_id in room.outlets:
            d = vignette_devices.get(device_id)
            if d is None or d.vignette != room_id or d.kind != "plug":
                problems.append(
                    f"{room_id}: outlet names are given for {device_id!r}, which isn't a plug in this room"
                )
        for scene in room.scenes:
            for step in scene.steps:
                if step.device is not None:
                    d = vignette_devices.get(step.device)
                    if d is None or d.vignette != room_id:
                        problems.append(
                            f"{room_id} → scene {scene.id!r}: {step.device!r} isn't a vignette device in this room"
                        )
                        continue
                    kind = d.kind
                else:
                    kind = step.kind
                bad = [a for a in step.set if a not in ACTIONS.get(kind or "", ())]
                if bad:
                    problems.append(
                        f"{room_id} → scene {scene.id!r}: a {kind} can't do {', '.join(bad)} "
                        f"(it can do {', '.join(ACTIONS.get(kind or '', ()))})"
                    )
    if problems:
        raise ConfigError(
            f"{path} doesn't match config/devices.yaml:\n" + "\n".join(f"  • {p}" for p in problems)
        )


# ---- the device model: what this device's own profile says it can do ------------------------------------------
def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _parse_rgb(value: Any) -> tuple[int, int, int] | None:
    if isinstance(value, str) and re.fullmatch(r"#?[0-9A-Fa-f]{6}", value.strip()):
        h = value.strip().lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    if (
        isinstance(value, Sequence)
        and not isinstance(value, str)
        and len(value) == 3
        and all(_is_int(c) and 0 <= c <= 255 for c in value)
    ):
        return int(value[0]), int(value[1]), int(value[2])
    if isinstance(value, Mapping) and all(k in value for k in ("r", "g", "b")):
        return _parse_rgb([value["r"], value["g"], value["b"]])
    return None


def _hex(rgb: Sequence[int]) -> str:
    return "#" + "".join(f"{int(c):02X}" for c in rgb)


def pretty(name: str) -> str:
    """ "fade-3" → "Fade 3", "july-4th" → "July 4th", "wake-up" → "Wake up"."""
    words = str(name).replace("_", "-").split("-")
    text = " ".join(w for w in words if w)
    return text[:1].upper() + text[1:]


def _names(fn: Mapping[str, Any]) -> list[str]:
    return [str(v.get("name")) for v in fn.get("values") or [] if isinstance(v, Mapping) and "name" in v]


def _range(fn: Mapping[str, Any]) -> tuple[int, int, int | None] | None:
    for v in fn.get("values") or []:
        r = v.get("range") if isinstance(v, Mapping) else None
        if isinstance(r, Mapping) and "min" in r and "max" in r:
            step = r.get("step")
            return int(r["min"]), int(r["max"]), int(step) if step else None
    return None


@dataclass
class DeviceModel:
    """One vignette device as its own profile describes it. Every row is checked here before it's sent."""

    device_id: str
    kind: str
    functions: dict[Key, dict]
    caps: Capabilities
    simulated: bool = True

    @classmethod
    def from_profile(
        cls, device_id: str, kind: str, functions: Iterable[Mapping[str, Any]], simulated: bool = True
    ) -> DeviceModel:
        fns = [dict(f) for f in functions if isinstance(f, Mapping)]
        index = {(f.get("functionClass"), f.get("functionInstance")): f for f in fns}
        return cls(device_id, kind, index, detect(fns), simulated)

    # -- lookups
    def has(self, cls: str, instance: str | None = None) -> bool:
        return (cls, instance) in self.functions

    def key(self, cls: str) -> Key | None:
        """The one (class, instance) for `cls` (e.g. the fan's power/fan-power); None when absent or ambiguous."""
        keys = [k for k in self.functions if k[0] == cls]
        if len(keys) == 1:
            return keys[0]
        return (cls, None) if (cls, None) in self.functions else None

    def values(self, cls: str, instance: str | None = None) -> list[str]:
        fn = self.functions.get((cls, instance))
        return _names(fn) if fn else []

    def range(self, cls: str, instance: str | None = None) -> tuple[int, int, int | None] | None:
        fn = self.functions.get((cls, instance))
        return _range(fn) if fn else None

    @property
    def effects(self) -> list[str]:
        """The device's own effects: its presets (without `custom`) then its programs, in the device's order."""
        return [*self.caps.presets, *self.caps.programs]

    @property
    def outlets(self) -> list[tuple[str, str | None]]:
        """(outlet key, power instance) for every switchable outlet — one per `power` function (SwitchController)."""
        instances = [k[1] for k in self.functions if k[0] == "power"]
        return [(i if i is not None else MAIN_OUTLET, i) for i in instances]

    # -- validation
    def check(self, row: StateRow) -> None:
        """Refuse a row the device doesn't declare, or a value outside what it lists (like the cloud's 400)."""
        fn = self.functions.get(row.key)
        what = (
            f"{row.function_class}/{row.function_instance}" if row.function_instance else row.function_class
        )
        if fn is None:
            raise VignetteError(f"This device has no {what} control.")
        kind = fn.get("type")
        value = row.value
        if kind == "category":
            if value not in _names(fn):
                raise VignetteError(f"{pretty(str(value))!s} isn't one of this device's {what} choices.")
        elif kind == "numeric":
            try:
                number = int(str(value).rstrip("K"))
            except ValueError:
                raise VignetteError(f"{what} needs a whole number.") from None
            rng = _range(fn)
            if rng:
                lo, hi, step = rng
                if not lo <= number <= hi:
                    unit = " K" if row.function_class == "color-temperature" else ""
                    raise VignetteError(f"{number}{unit} is outside this device's range {lo}–{hi}{unit}.")
                if step and (number - lo) % step:
                    raise VignetteError(
                        f"{number} isn't a step this device takes (steps of {step} from {lo})."
                    )

    def check_all(self, rows: Iterable[StateRow]) -> list[StateRow]:
        out = list(rows)
        for row in out:
            self.check(row)
        return out

    # -- what the page draws
    def controls(self, outlet_labels: Mapping[str, str] | None = None) -> dict:
        c = self.caps
        out: dict[str, Any] = {"actions": list(ACTIONS.get(self.kind, ()))}
        if self.kind in ("bulb", "string", "path"):
            out["brightness"] = list(c.brightness_range) if c.brightness_range else None
        if self.kind in ("bulb", "string"):
            cct = self.range("color-temperature")
            out["cct"] = {"min": cct[0], "max": cct[1], "step": cct[2]} if cct else None
            out["rgb"] = c.rgb and "color" in c.color_modes
            out["effects"] = [{"name": e, "label": pretty(e)} for e in self.effects]
            out["speed"] = list(c.speed_range) if c.speed_range else None
        if self.kind == "string":
            out["slots"] = [{"name": s, "label": f"Pattern {s.rsplit('-', 1)[-1]}"} for s in c.slots]
        if self.kind == "plug":
            labels = outlet_labels or {}
            out["outlets"] = [
                {
                    "key": key,
                    "label": labels.get(key) or (f"Outlet {n}" if len(self.outlets) > 1 else "Power"),
                }
                for n, (key, _) in enumerate(self.outlets, 1)
            ]
        if self.kind == "fan":
            out["speeds"] = [{"name": s, "label": str(n)} for n, s in enumerate(c.fan_speeds, 1)]
        if self.kind == "lock":
            out["positions"] = c.lock_values
        return out


# ---- looks: a device's whole state as the page sees it -----------------------------------------------------------
def default_look(model: DeviceModel) -> dict:
    kind = model.kind
    if kind == "lock":
        return {"position": None}
    if kind == "plug":
        return {"outlets": {key: False for key, _ in model.outlets}}
    if kind == "fan":
        return {"on": False, "speed": model.caps.fan_speeds[0] if model.caps.fan_speeds else None}
    if kind == "path":
        return {"on": False, "brightness": 100}
    cct = model.range("color-temperature")
    kelvin = min(max(DEFAULT_CCT, cct[0]), cct[1]) if cct else None
    look = {
        "on": False,
        "mode": "white",
        "cct": kelvin,
        "rgb": list(DEFAULT_RGB),
        "effect": model.effects[0] if model.effects else None,
        "brightness": 100,
        "speed": 0,
    }
    if kind == "string":
        look["slot"] = model.caps.slots[0] if model.caps.slots else None
    return look


def look_from_rows(
    model: DeviceModel, rows: Iterable[StateRow], base: Mapping[str, Any] | None = None
) -> dict:
    """The look a device is showing, from its reported rows (fields it doesn't report keep `base`/defaults)."""
    got = {r.key: r.value for r in rows}
    look = copy.deepcopy(dict(base)) if base else default_look(model)
    kind = model.kind

    def num(key: Key) -> int | None:
        try:
            return int(str(got[key]).rstrip("K")) if key in got else None
        except ValueError:
            return None

    if kind == "lock":
        key = model.key("lock-control")
        look["position"] = got.get(key) if key else None
        return look
    if kind == "plug":
        look["outlets"] = {
            k: got.get(("power", inst)) == "on" if ("power", inst) in got else look["outlets"].get(k, False)
            for k, inst in model.outlets
        }
        return look
    power_key = model.key("power")
    if power_key and power_key in got:
        look["on"] = got[power_key] == "on"
    if kind == "fan":
        speed_key = model.key("fan-speed")
        if speed_key and got.get(speed_key) in model.caps.fan_speeds:
            look["speed"] = got[speed_key]
        return look
    if (b := num(("brightness", None))) is not None:
        look["brightness"] = b
    if kind == "path":
        return look
    if (k := num(("color-temperature", None))) is not None:
        look["cct"] = k
    raw_rgb = got.get(("color-rgb", None))
    rgb = _parse_rgb(raw_rgb.get("color-rgb", raw_rgb)) if isinstance(raw_rgb, Mapping) else None
    if rgb:
        look["rgb"] = list(rgb)
    if (s := num(("speed", "color-sequence"))) is not None:
        look["speed"] = s
    preset = got.get(("color-sequence", "preset"))
    effect = got.get(("color-sequence", "custom")) if preset == "custom" else preset
    if isinstance(effect, str) and effect in model.effects:
        look["effect"] = effect
    slot = got.get(("color-individual", "custom"))
    if kind == "string" and isinstance(slot, str) and slot in model.caps.slots:
        look["slot"] = slot
    mode = got.get(("color-mode", None))
    look["mode"] = {"white": "white", "color": "color", "sequence": "effect", "individual": "slot"}.get(
        mode, "unsupported" if mode else look["mode"]
    )
    return look


def _effect_rows(model: DeviceModel, effect: str) -> list[StateRow]:
    if effect in model.caps.presets:
        return [R.preset(effect)]
    if effect in model.caps.programs:
        return R.program(effect)
    raise VignetteError(f"{pretty(effect)} isn't one of this device's effects.")


def _light_vector(model: DeviceModel, look: Mapping[str, Any]) -> list[StateRow]:
    """A colour bulb's full rows, in aioafero's LightPut order: power, color-rgb, color-mode, color-temperature,
    brightness, effect, speed. Rows the device doesn't declare are left out (a white-only bulb has no color-mode)."""
    power_key = model.key("power") or ("power", None)
    if not look.get("on"):
        return [StateRow(*power_key, "off")]
    rows: list[StateRow] = [StateRow(*power_key, "on")]
    mode = look.get("mode")
    if mode == "color":
        rows += [R.color_rgb(*look["rgb"]), R.color_mode("color")]
    elif mode == "white":
        if model.has("color-mode"):
            rows.append(R.color_mode("white"))
        if model.has("color-temperature") and look.get("cct") is not None:
            rows.append(R.color_temperature(int(look["cct"])))
    elif mode == "effect":
        rows.append(R.color_mode("sequence"))
    else:
        raise VignetteError(
            "This device is in a mode this page can't show. Choose a colour, a white or an effect."
        )
    if model.has("brightness"):
        rows.append(R.brightness(int(look["brightness"])))
    if mode == "effect":
        if not look.get("effect"):
            raise VignetteError("Choose an effect first.")
        rows += _effect_rows(model, look["effect"])
        if model.has("speed", "color-sequence"):
            rows.append(R.speed(int(look.get("speed") or 0)))
    return rows


def _string_vector(model: DeviceModel, look: Mapping[str, Any]) -> list[StateRow]:
    """A string light's whole-strand look through `rows.full_state_vector` (the canopy's own vocabulary)."""
    if not look.get("on"):
        return R.full_state_vector(R.Look("off"))
    mode = look.get("mode")
    common = {"brightness": int(look["brightness"]), "speed": int(look.get("speed") or 0)}
    if mode == "white":
        target = R.Look("white", cct=int(look["cct"]), **common)
    elif mode == "color":
        target = R.Look("rgb", rgb=tuple(look["rgb"]), **common)
    elif mode == "effect":
        effect = look.get("effect")
        if effect in model.caps.presets:
            target = R.Look("preset", preset=effect, **common)
        elif effect in model.caps.programs:
            target = R.Look("program", program=effect, **common)
        else:
            raise VignetteError(f"{pretty(str(effect))} isn't one of this string's effects.")
    elif mode == "slot":
        if look.get("slot") not in model.caps.slots:
            raise VignetteError("This string has no saved pattern with that name.")
        target = R.Look("slot", slot=look["slot"], **common)
    else:
        raise VignetteError(
            "The string is in a mode this page can't show. Choose a colour, a white or an effect."
        )
    return R.full_state_vector(target, cct_range=model.caps.cct_range)


def vector(model: DeviceModel, look: Mapping[str, Any]) -> tuple[list[StateRow], list[StateRow]]:
    """(rows to send, rows readback must show) for a device's whole look, checked against its profile."""
    try:
        rows = _vector_rows(model, look)
        return model.check_all(rows), list(rows)
    except R.PayloadError as e:
        raise VignetteError(str(e)) from None


def _vector_rows(model: DeviceModel, look: Mapping[str, Any]) -> list[StateRow]:
    kind = model.kind
    if kind == "lock":
        raise VignetteError("A lock is only ever locked or unlocked, with a confirm.")
    if kind == "plug":
        outlets = look.get("outlets") or {}
        rows = [StateRow("power", inst, "on" if outlets.get(key) else "off") for key, inst in model.outlets]
    elif kind == "fan":
        power_key = model.key("power") or ("power", None)
        speed_key = model.key("fan-speed")
        if not look.get("on"):
            rows = [StateRow(*power_key, "off")]
        else:
            rows = [StateRow(*power_key, "on")]
            if speed_key and look.get("speed"):
                rows.append(StateRow(*speed_key, look["speed"]))
    elif kind == "path":
        power_key = model.key("power") or ("power", None)
        rows = [StateRow(*power_key, "on" if look.get("on") else "off")]
        if look.get("on") and model.has("brightness"):
            rows.append(R.brightness(int(look["brightness"])))
    elif kind == "string":
        rows = _string_vector(model, look)
    elif kind == "bulb":
        rows = _light_vector(model, look)
    else:
        raise VignetteError(f"{kind!r} isn't a vignette device kind.")
    return rows


@dataclass
class Compiled:
    device_id: str
    look: dict
    rows: list[StateRow]
    expect: list[StateRow]
    label: str


def _int(value: Any, what: str) -> int:
    if _is_int(value):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise VignetteError(f"{what} needs a whole number.")


def apply_action(
    model: DeviceModel, look: Mapping[str, Any], action: str, value: Any, *, outlet: str | None = None
) -> dict:
    """The device's look after one page action. Raises VignetteError for anything the device can't do."""
    kind = model.kind
    if action not in ACTIONS.get(kind, ()):
        raise VignetteError(
            f"{KIND_NOUN.get(kind, 'This device')} can't do “{pretty(action)}” from this page."
        )
    new = copy.deepcopy(dict(look))
    if action == "power":
        if not isinstance(value, bool):
            raise VignetteError("Power needs on (true) or off (false).")
        if kind == "plug":
            keys = [k for k, _ in model.outlets]
            if outlet is not None and outlet not in keys:
                raise VignetteError("This plug has no outlet with that name.")
            outlets = dict(new.get("outlets") or {})
            for key in [outlet] if outlet is not None else keys:
                outlets[key] = value
            new["outlets"] = outlets
        else:
            new["on"] = value
        return new
    if action == "outlets":
        if not isinstance(value, Mapping) or not all(isinstance(v, bool) for v in value.values()):
            raise VignetteError("Outlets need {outlet: true|false}.")
        keys = {k for k, _ in model.outlets}
        unknown = sorted(set(value) - keys)
        if unknown:
            raise VignetteError(f"This plug has no outlet called {', '.join(unknown)}.")
        new["outlets"] = {**(new.get("outlets") or {}), **value}
        return new
    if action == "fan_speed":
        if value not in model.caps.fan_speeds:
            raise VignetteError("That isn't one of this fan's speeds.")
        new.update(on=True, speed=value)  # aioafero FanController.set_speed turns the fan on
        return new
    # Light actions all turn the light on, like the stock app and aioafero's set_* helpers.
    new["on"] = True
    if action == "brightness":
        new["brightness"] = _int(value, "Brightness")
        if not model.has("brightness"):
            raise VignetteError("This device can't be dimmed.")
        model.check(StateRow("brightness", None, new["brightness"]))
    elif action == "color_temperature":
        kelvin = _int(value, "Colour temperature")
        if not model.has("color-temperature"):
            raise VignetteError("This device has no white-temperature control.")
        model.check(StateRow("color-temperature", None, str(kelvin)))
        new.update(mode="white", cct=kelvin)
    elif action == "white":
        if value is not True:
            raise VignetteError("White needs true.")
        if not model.has("color-temperature") and "white" not in model.caps.color_modes:
            raise VignetteError("This device has no white mode.")
        new["mode"] = "white"
    elif action == "color":
        rgb = _parse_rgb(value)
        if rgb is None:
            raise VignetteError('Colour needs "#RRGGBB".')
        if not (model.caps.rgb and "color" in model.caps.color_modes):
            raise VignetteError("This device can't show colours.")
        new.update(mode="color", rgb=list(rgb))
    elif action == "effect":
        if not isinstance(value, str) or value not in model.effects:
            raise VignetteError(f"{pretty(str(value))} isn't one of this device's effects.")
        new.update(mode="effect", effect=value)
    elif action == "speed":
        speed = _int(value, "Speed")
        rng = model.caps.speed_range
        if rng is None:
            raise VignetteError("This device's effects have no speed control.")
        if not rng[0] <= speed <= rng[1]:
            raise VignetteError(f"Speed goes from {rng[0]} to {rng[1]}.")
        new["speed"] = speed
        if new.get("mode") not in ("effect", "slot"):
            raise VignetteError("Speed applies to an effect. Choose an effect first.")
    elif action == "slot":
        if value not in model.caps.slots:
            raise VignetteError("This string has no saved pattern with that name.")
        new.update(mode="slot", slot=value)
    return new


def _describe(action: str, value: Any, outlet: str | None) -> str:
    if action == "power":
        return f"{outlet or 'power'} {'on' if value else 'off'}"
    if action in LOCK_ACTIONS:
        return action
    return f"{action.replace('_', ' ')} {value}" if not isinstance(value, Mapping) else action


def compile_action(
    model: DeviceModel,
    look: Mapping[str, Any],
    action: str,
    value: Any = None,
    *,
    outlet: str | None = None,
    confirm: bool = False,
) -> Compiled:
    """One page action → the device's new look and the one PUT that shows it (full rows, 07 §5)."""
    if model.kind == "lock":
        if action not in LOCK_ACTIONS:
            raise VignetteError("A lock can only be locked or unlocked.")
        if not confirm:
            raise ConfirmRequired(f"Confirm to {action} the door.")
        key = model.key("lock-control")
        if key is None:
            raise VignetteError("This device has no lock control.")
        rows = model.check_all([StateRow(*key, LOCK_WRITE[action])])
        expect = [StateRow(*key, LOCK_SETTLED[action])]
        return Compiled(model.device_id, {"position": LOCK_SETTLED[action]}, rows, expect, action)
    new = apply_action(model, look, action, value, outlet=outlet)
    rows, expect = vector(model, new)
    return Compiled(model.device_id, new, rows, expect, _describe(action, value, outlet))


def compile_step(model: DeviceModel, look: Mapping[str, Any], settings: Mapping[str, Any]) -> Compiled:
    """A scene step's `set:` for one device: every action in order, `power` last (so `power: false` wins)."""
    items = sorted(settings.items(), key=lambda kv: kv[0] == "power")
    new = dict(look)
    for action, value in items:
        if action in LOCK_ACTIONS:
            raise VignetteError("Locks can't be part of a scene.")
        new = apply_action(model, new, action, value)
    rows, expect = vector(model, new)
    return Compiled(model.device_id, new, rows, expect, "scene")


# ---- the service --------------------------------------------------------------------------------------------------
@dataclass
class VignetteDeps:
    """What the service needs from the app (built from `Services` in production, by hand in tests)."""

    registry: Callable[[], Registry]
    target: Callable[[str, Purpose], Any]  # (device_id, purpose) → services.Target
    rooms: VignettesFile
    tracker: Any = None  # DeviceStateTracker
    publish: Callable[[str, dict], None] | None = None
    clock: Any = None


@dataclass
class _Record:
    model: DeviceModel | None = None
    error: str | None = None
    address: str | None = None
    simulated: bool | None = None


@dataclass
class _Sent:
    device_id: str
    status: str
    error: str | None = None
    result: dict | None = None


class VignetteService:
    """The four rooms' devices, looks, actions and scenes. One per app, at `services.extras["vignettes"]`."""

    def __init__(self, deps: VignetteDeps):
        self.deps = deps
        self.rooms = deps.rooms
        self.looks: dict[str, dict] = {}
        self.records: dict[str, _Record] = {}
        self._addresses: dict[str, tuple[str, str]] = {}  # engine address → (room, device id)
        self._addresses_for: Any = None  # the registry the map was built from (identity, holding a reference)
        self._locks: dict[str, asyncio.Lock] = {}
        self._quiet: set[str] = set()

    # -- wiring
    @classmethod
    def from_services(cls, services: Any) -> VignetteService:
        rooms = load_vignettes(services.config_dir)
        check_against_registry(rooms, services.registry)
        service = cls(
            VignetteDeps(
                registry=lambda: services.registry,
                target=services.target,
                rooms=rooms,
                tracker=services.tracker,
                publish=services.hub.publish,
                clock=services.clock,
            )
        )
        services.tracker.add_listener(service.on_tracker)
        services.hub.register_snapshot(TOPIC_PREFIX, service.topic_snapshot)
        return service

    @classmethod
    def of(cls, services: Any) -> VignetteService:
        """The app's service, created on first use. Raises ConfigError when vignettes.yaml is wrong."""
        service = services.extras.get(SERVICE_KEY)
        if service is None:
            service = services.extras[SERVICE_KEY] = cls.from_services(services)
        return service

    # -- rooms and devices
    def room(self, room_id: str) -> RoomConfig:
        try:
            return self.rooms.rooms[room_id]
        except KeyError:
            raise LookupError(f"There's no vignette called {room_id!r}.") from None

    def devices(self, room_id: str) -> list[DeviceEntry]:
        """The room's enabled devices, in registry order (a disabled device — the unconfirmed fan — isn't shown)."""
        self.room(room_id)
        return [d for d in self.deps.registry().by_role("vignette") if d.vignette == room_id]

    def device(self, room_id: str, device_id: str) -> DeviceEntry:
        for d in self.devices(room_id):
            if d.id == device_id:
                return d
        raise LookupError(f"{device_id!r} isn't a device in this room.")

    def _lock(self, device_id: str) -> asyncio.Lock:
        return self._locks.setdefault(device_id, asyncio.Lock())

    def _current(self, entry: DeviceEntry) -> tuple[str, bool] | None:
        """(address, simulated) the device resolves to now, or None when it has no address."""
        try:
            target = self.deps.target(entry.id, Purpose.ENGINE)
        except LookupError:
            return None
        return target.address, bool(target.simulated)

    def _fresh(self, entry: DeviceEntry, record: _Record | None) -> bool:
        """A cached record still describes the device the registry names (not re-registered since: review V2)."""
        if record is None or record.model is None:
            return False
        return self._current(entry) == (record.address, bool(record.simulated))

    async def load(self, entry: DeviceEntry, *, refresh: bool = False) -> _Record:
        """Read the device's profile once (functions + current state) through the engine driver — a GET. Read again
        when the device was re-registered to another address since (a swapped bulb)."""
        record = self.records.get(entry.id)
        if not refresh and self._fresh(entry, record):
            return record
        async with self._lock(entry.id):
            record = self.records.get(entry.id)
            if not refresh and self._fresh(entry, record):
                return record
            if record is not None and record.model is not None and not self._fresh(entry, record):
                # Re-registered: the old device's look and profile say nothing about the new one.
                log.info("vignette: %s was re-registered; reading the new device", entry.id)
                self.looks.pop(entry.id, None)
            record = _Record()
            try:
                target = self.deps.target(entry.id, Purpose.ENGINE)
                record.address, record.simulated = target.address, target.simulated
                info = await asyncio.wait_for(
                    target.driver.fetch_profile(target.address, purpose=Purpose.ENGINE), 15
                )
            except LookupError as exc:
                record.error = str(exc)
            except TimeoutError:
                record.error = "Hubspace didn't answer in time. Tap Check again."
            except Exception as exc:  # noqa: BLE001 - a device that can't be read shows its problem, not a crash
                log.warning("vignette: couldn't read %s's profile: %s", entry.id, exc)
                record.error = str(exc) or "Couldn't read this device from Hubspace."
            else:
                record.model = DeviceModel.from_profile(
                    entry.id, entry.kind or "", info.functions, target.simulated
                )
                self.records[entry.id] = (
                    record  # before the tracker hears of it, so its push carries the device
                )
                self._adopt(
                    entry, record, [r for r in info.states if r.function_class not in TELEMETRY_CLASSES]
                )
                if self.deps.tracker is not None:
                    fetched_at = self.deps.clock.now() if self.deps.clock else 0.0
                    readback = ReadResult(
                        device_id=record.address,
                        outcome=Outcome.OK,
                        rows=list(info.states),
                        http_status=200,
                        fetched_at=fetched_at,
                        simulated=target.simulated,
                    )
                    self.deps.tracker.note_readback(record.address, readback)
            self.records[entry.id] = record
            return record

    def _adopt(self, entry: DeviceEntry, record: _Record, reported: list[StateRow]) -> None:
        """First read: the look the device already shows becomes its desired look (when it round-trips)."""
        model = record.model
        assert model is not None
        look = look_from_rows(model, reported, self.looks.get(entry.id))
        self.looks[entry.id] = look
        tracker = self.deps.tracker
        if tracker is None or record.address is None:
            return
        if tracker.snapshot(record.address).get("desired") is not None:
            return  # someone already set a look this run
        if model.kind == "lock":
            key = model.key("lock-control")
            if key and look.get("position") in ("locked", "unlocked"):
                self._set_desired(entry, record.address, [StateRow(*key, look["position"])], look, "as found")
            return
        try:
            rows, _ = vector(model, look)
        except VignetteError:
            return
        if not R.compare(rows, reported):
            self._set_desired(entry, record.address, rows, look, "as found")

    def _set_desired(
        self, entry: DeviceEntry, address: str, rows: list[StateRow], look: dict, what: str
    ) -> None:
        if self.deps.tracker is None:
            return
        self.deps.tracker.set_desired(
            address,
            rows,
            label=f"{entry.label or entry.id}: {what}",
            source="vignette",
            look={"kind": entry.kind, **look},
            meta={"room": entry.vignette, "device_id": entry.id, "kind": entry.kind},
        )

    # -- state for the page
    def device_json(self, entry: DeviceEntry) -> dict:
        record = self.records.get(entry.id) or _Record()
        if record.model is not None and not self._fresh(entry, record):
            # Re-registered since it was read (review V2): show the new address's status, never the old device's
            # look or readiness. The next page load or tap reads the new device.
            current = self._current(entry)
            record = _Record(
                error=f"{entry.label or entry.id} was re-registered to a new device. Tap Check again to read it.",
                address=current[0] if current else None,
                simulated=current[1] if current else record.simulated,
            )
        model = record.model
        snap = self.deps.tracker.snapshot(record.address) if self.deps.tracker and record.address else {}
        reported = [StateRow.from_wire(r) for r in snap.get("reported") or []]
        now = look_from_rows(model, reported, default_look(model)) if model and reported else None
        room = self.rooms.rooms.get(entry.vignette or "")
        outlet_labels = room.outlets.get(entry.id, {}) if room else {}
        look = self.looks.get(entry.id) if model is not None else None
        status = snap.get("status", "unknown")
        reason = snap.get("reason", "Not checked yet.")
        if model is None and record.error and status in ("unknown", "ready"):
            status, reason = "needs_attention", record.error  # never "Ready" for a device the page can't read
        return {
            "id": entry.id,
            "label": entry.label or entry.id,
            "kind": entry.kind,
            "model": entry.model,
            "simulated": record.simulated,
            "ready": model is not None,
            "error": record.error,
            "controls": model.controls(outlet_labels) if model else None,
            "look": _look_json(look),
            "now": _look_json(now),
            "status": status,
            "reason": reason,
            "desired": _look_rows(snap.get("desired")),
            "reported": _look_rows(snap.get("reported")),
            "available": snap.get("available"),
            "last_command": _command_json(snap.get("last_command")),
            "updated_at": snap.get("updated_at"),
        }

    def room_json(self, room_id: str) -> dict:
        room = self.room(room_id)
        devices = self.devices(room_id)
        return {
            "room": room_id,
            "space": room.space,
            "title": room.title,
            "scenes": [{"id": s.id, "label": s.label} for s in room.scenes],
            "palette": [s.model_dump() for s in self.rooms.palette],
            "whites": [w.model_dump() for w in self.rooms.whites],
            "devices": [self.device_json(d) for d in devices],
            "simulated": _any_simulated(self.records.get(d.id, _Record()).simulated for d in devices),
        }

    async def room_state(self, room_id: str, *, refresh: bool = False) -> dict:
        devices = self.devices(room_id)
        await asyncio.gather(*(self.load(d, refresh=refresh) for d in devices))
        return self.room_json(room_id)

    def topic_snapshot(self, topic: str) -> dict:
        room_id = topic.partition(":")[2]
        try:
            return self.room_json(room_id)
        except LookupError:
            return {"room": room_id, "devices": [], "error": "unknown room"}

    def on_tracker(self, address: str, _snapshot: dict) -> None:
        """Tracker listener: push a vignette device's new status to its room's topic."""
        where = self._address_map().get(address)
        if where is None or self.deps.publish is None or address in self._quiet:
            return
        room_id, device_id = where
        try:
            entry = self.device(room_id, device_id)
        except LookupError:
            return
        self.deps.publish(
            f"{TOPIC_PREFIX}:{room_id}", {"room": room_id, "devices": [self.device_json(entry)]}
        )

    def _address_map(self) -> dict[str, tuple[str, str]]:
        registry = self.deps.registry()
        if self._addresses_for is not registry:  # identity with a reference held: a bare id() can be reused
            self._addresses = {}
            for entry in registry.by_role("vignette"):
                try:
                    address = self.deps.target(entry.id, Purpose.ENGINE).address
                except LookupError:
                    continue
                self._addresses[address] = (entry.vignette or "", entry.id)
            self._addresses_for = registry
        for device_id, record in self.records.items():  # addresses seen since (an overlay edit, a new id)
            if record.address and record.address not in self._addresses and record.model is not None:
                entry = registry.get(device_id)
                self._addresses[record.address] = (entry.vignette or "", device_id)
        return self._addresses

    # -- writes
    async def _ready(self, entry: DeviceEntry) -> tuple[DeviceModel, str]:
        record = await self.load(entry)
        if record.model is None or record.address is None:
            raise VignetteError(record.error or f"{entry.label or entry.id} can't be reached yet.")
        return record.model, record.address

    async def _send(self, entry: DeviceEntry, compiled: Compiled, source: str) -> _Sent:
        target = self.deps.target(entry.id, Purpose.ENGINE)
        what = f"vignette {entry.vignette}: {entry.label or entry.id} {compiled.label}"
        self.looks[entry.id] = compiled.look
        # The tracker would say "waiting for a readback" between the new desired look and the submit; the page
        # should go straight to Updating, so that one intermediate push is skipped.
        self._quiet.add(target.address)
        try:
            self._set_desired(entry, target.address, compiled.expect, compiled.look, compiled.label)
        finally:
            self._quiet.discard(target.address)
        result: CommandResult = await target.executor.submit(
            Command(
                target.address,
                compiled.rows,
                Purpose.ENGINE,
                label=what,
                verify=True,
                expect=compiled.expect,
                source=source,
            )
        )
        return _Sent(entry.id, str(result.status), result.error, result.to_json())

    async def action(
        self,
        room_id: str,
        *,
        action: str,
        value: Any = None,
        device_id: str | None = None,
        group: str | None = None,
        outlet: str | None = None,
        confirm: bool = False,
    ) -> dict:
        """One page action on a device (or on every device of a kind: `group`) — one PUT per device."""
        if (device_id is None) == (group is None):
            raise VignetteError("Name a device (or a group of devices) to change.")
        if group is not None:
            entries = [d for d in self.devices(room_id) if d.kind == group]
            if not entries:
                raise LookupError(f"This room has no {KIND_NOUN.get(group, group).lower()}.")
            if group == "lock":
                raise VignetteError("Locks are changed one at a time, with a confirm.")
        else:
            entries = [self.device(room_id, device_id)]
        compiled: list[tuple[DeviceEntry, Compiled]] = []
        skipped: list[dict] = []
        for entry in entries:
            try:
                model, _ = await self._ready(entry)
                look = self.looks.get(entry.id) or default_look(model)
                compiled.append(
                    (entry, compile_action(model, look, action, value, outlet=outlet, confirm=confirm))
                )
            except VignetteError as exc:
                if group is None:
                    raise
                # One lamp that can't be read (or can't take this value) never stops the rest (review V3).
                skipped.append({"device_id": entry.id, "reason": str(exc)})
        if not compiled:
            reasons = "; ".join(f"{self._label(s['device_id'])}: {s['reason']}" for s in skipped)
            raise VignetteError(
                f"None of the {KIND_NOUN.get(group or '', 'devices').lower()} can do that: {reasons}"
            )
        outcomes = await asyncio.gather(*(self._send(e, c, "vignette") for e, c in compiled))
        return self._report(room_id, list(outcomes), skipped)

    def _label(self, device_id: str) -> str:
        try:
            entry = self.deps.registry().get(device_id)
        except (KeyError, LookupError):
            return device_id
        return entry.label or device_id

    async def scene(self, room_id: str, scene_id: str) -> dict:
        """A room scene: each step's settings on its devices, merged per device, then one PUT per device."""
        room = self.room(room_id)
        scene = next((s for s in room.scenes if s.id == scene_id), None)
        if scene is None:
            raise LookupError(f"This room has no scene called {scene_id!r}.")
        devices = {d.id: d for d in self.devices(room_id)}
        pending: dict[str, tuple[DeviceEntry, DeviceModel, dict]] = {}
        skipped: list[dict] = []
        for step in scene.steps:
            if step.device is not None:
                targets = [devices[step.device]] if step.device in devices else []
                if not targets:
                    skipped.append({"device_id": step.device, "reason": "Not in use in this room right now."})
            else:
                targets = [d for d in devices.values() if d.kind == step.kind]
            for entry in targets:
                if entry.id in {s["device_id"] for s in skipped}:
                    continue
                try:
                    if entry.id not in pending:
                        model, _ = await self._ready(entry)
                        pending[entry.id] = (entry, model, self.looks.get(entry.id) or default_look(model))
                    _, model, look = pending[entry.id]
                    pending[entry.id] = (entry, model, compile_step(model, look, step.set).look)
                except VignetteError as exc:
                    pending.pop(entry.id, None)
                    skipped.append({"device_id": entry.id, "reason": str(exc)})
        compiled: list[tuple[DeviceEntry, Compiled]] = []
        for entry, model, look in pending.values():
            try:
                rows, expect = vector(model, look)
            except VignetteError as exc:
                skipped.append({"device_id": entry.id, "reason": str(exc)})
                continue
            compiled.append((entry, Compiled(entry.id, look, rows, expect, f"scene {scene.label}")))
        if not compiled:
            reasons = "; ".join(f"{s['device_id']}: {s['reason']}" for s in skipped) or "nothing to change"
            raise VignetteError(f"{scene.label} couldn't run: {reasons}")
        outcomes = await asyncio.gather(*(self._send(e, c, "vignette scene") for e, c in compiled))
        return self._report(room_id, list(outcomes), skipped, scene=scene.id)

    def _report(self, room_id: str, outcomes: list[_Sent], skipped: list[dict], **extra: Any) -> dict:
        ok_statuses = {str(CommandStatus.APPLIED), str(CommandStatus.SUPERSEDED)}
        by_id = {d.id: d for d in self.devices(room_id)}
        return {
            "ok": all(o.status in ok_statuses for o in outcomes) and not skipped,
            "results": [
                {
                    "device_id": o.device_id,
                    "status": o.status,
                    "error": o.error,
                    "command": _command_json(o.result),
                }
                for o in outcomes
            ],
            "skipped": skipped,
            "devices": [self.device_json(by_id[o.device_id]) for o in outcomes if o.device_id in by_id],
            "simulated": all(bool(self.records.get(o.device_id, _Record()).simulated) for o in outcomes),
            **extra,
        }


def _any_simulated(flags: Iterable[bool | None]) -> bool | None:
    """True when any device of the room runs on the simulator; None when none has been read yet."""
    known = [f for f in flags if f is not None]
    return any(known) if known else None


def _look_rows(rows: Any) -> list[dict] | None:
    """Only the rows a look is made of (LOOK_CLASSES): never telemetry, slot patterns or anything else the device
    reports (review V7)."""
    if rows is None:
        return None
    return [r for r in rows if isinstance(r, Mapping) and r.get("functionClass") in LOOK_CLASSES]


def _command_json(command: Mapping[str, Any] | None) -> dict | None:
    """A command result for a tablet: status, timings, error and mismatched look rows — not the driver address."""
    if command is None:
        return None
    out = {k: v for k, v in command.items() if k != "device_id"}
    out["mismatches"] = [
        m
        for m in out.get("mismatches") or []
        if isinstance(m, Mapping) and m.get("functionClass") in LOOK_CLASSES
    ]
    return out


def _look_json(look: Mapping[str, Any] | None) -> dict | None:
    if look is None:
        return None
    out = dict(look)
    if out.get("rgb") is not None:
        out["hex"] = _hex(out["rgb"])
    return out


__all__ = [
    "ACTIONS",
    "LOOK_CLASSES",
    "VIGNETTE_KINDS",
    "Compiled",
    "ConfirmRequired",
    "DeviceModel",
    "VignetteDeps",
    "VignetteError",
    "VignetteService",
    "VignettesFile",
    "apply_action",
    "check_against_registry",
    "compile_action",
    "compile_step",
    "default_look",
    "load_vignettes",
    "look_from_rows",
    "vector",
]
