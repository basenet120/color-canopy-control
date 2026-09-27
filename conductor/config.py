"""Config: git YAML defaults + an ops overlay under the data dir, validated on load (05 #3, 07 §8).

Errors name the file, the entry and the fix, in words a tired technician can act on — never a stack trace.

Files:
- `config/conductor.yaml` → `ConductorSettings` (executor, reconciler, kiosk, module page, effect codes)
- `config/devices.yaml`   → the device registry defaults
- `<data>/overrides/registry.yaml` → ops edits per device (real metadevice ids, calibration, bench flags,
  slot hashes) and extra devices (spares). Written atomically; every version kept under `overrides/history/`.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from conductor.tokens import atomic_write

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class ConfigError(ValueError):
    """A config file is wrong. The message says which file, which entry and what to do."""


def data_dir(env: Mapping[str, str] = os.environ) -> Path:
    return Path(env.get("CONDUCTOR_DATA_DIR") or Path(__file__).resolve().parent.parent / "var")


def _explain(path: Path | str, err: ValidationError, data: Any = None) -> str:
    """Operator-readable validation errors; list positions under `devices` are shown as the device id."""
    lines = [f"{path} has {err.error_count()} problem(s):"]
    devices = data.get("devices") if isinstance(data, dict) else None
    for e in err.errors():
        loc = list(e["loc"])
        if len(loc) >= 2 and loc[0] == "devices" and isinstance(loc[1], int) and isinstance(devices, list):
            entry = devices[loc[1]] if loc[1] < len(devices) else {}
            loc[1] = f"device {entry.get('id', loc[1])!r}" if isinstance(entry, dict) else loc[1]
        where = " → ".join(str(p) for p in loc) or "(top level)"
        lines.append(f"  • {where}: {e['msg']}")
    lines.append("Fix the file (or remove the override) and restart.")
    return "\n".join(lines)


def _read_yaml(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        at = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise ConfigError(f"{path} is not valid YAML{at}. Check indentation and colons.") from None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- conductor.yaml --------------------------------------------------------------------------------------------
class ExecutorSettings(_Strict):
    min_write_interval_s: float = Field(1.0, ge=0.5)
    account_concurrency: int = Field(9, ge=1, le=20)
    max_attempts: int = Field(3, ge=1, le=6)
    backoff_s: list[float] = Field(default_factory=lambda: [1.0, 2.0, 4.0])
    readback_delay_s: float = Field(1.0, ge=0)
    readback_attempts: int = Field(3, ge=1, le=10)
    readback_interval_s: float = Field(1.0, ge=0.2)


class ReconcilerSettings(_Strict):
    true_up_interval_s: float = 60
    reassert_after_s: float = 4
    downgrade_after_wrong_s: float = 10
    downgrade_throttles: int = 3
    downgrade_throttle_window_s: float = 60
    upgrade_after_clean_s: float = 300
    slot_rewrite_min_interval_s: float = 600
    outage_probe_interval_s: float = 10


class KioskSettings(_Strict):
    cooldown_s: float = 8
    idle_return_s: float = 180
    idle_mode: str = "lowcountry-calm"


class ModulePageSettings(_Strict):
    lock_idle_timeout_s: float = 600
    sweep_step_s: float = Field(2.0, ge=1.0)


class EffectSettings(_Strict):
    enabled: dict[Literal["motionEffect", "brightnessEffect"], dict[int, str]] = Field(
        default_factory=lambda: {"motionEffect": {}, "brightnessEffect": {}}
    )


class ConductorSettings(_Strict):
    executor: ExecutorSettings = Field(default_factory=ExecutorSettings)
    reconciler: ReconcilerSettings = Field(default_factory=ReconcilerSettings)
    kiosk: KioskSettings = Field(default_factory=KioskSettings)
    module_page: ModulePageSettings = Field(default_factory=ModulePageSettings)
    effects: EffectSettings = Field(default_factory=EffectSettings)


def load_settings(config_dir: Path = CONFIG_DIR) -> ConductorSettings:
    path = config_dir / "conductor.yaml"
    try:
        return ConductorSettings.model_validate(_read_yaml(path))
    except ValidationError as e:
        raise ConfigError(_explain(path, e)) from None


# ---- registry ----------------------------------------------------------------------------------------------------
Role = Literal["bench", "canopy_module", "vignette", "spare"]
VIGNETTE_KINDS = ("bulb", "plug", "lock", "fan", "string", "path")
Corner = Literal["top-left", "top-right", "bottom-left", "bottom-right"]
SlotOrigin = Literal["api", "app"]


class DeviceFlags(_Strict):
    """What the bench has proven for this device. None = not benched yet (live treats it as not proven)."""

    t2_select: bool | None = None  # Gate B: slot select by API works
    t1_frames: bool | None = None  # Gate C: frames written by API work
    readback_device_level: bool | None = None  # Gate B unplugged test: does readback reflect the device?


class DeviceEntry(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,40}$")
    role: Role
    account: str
    label: str = ""
    model: str = ""
    profile: str | None = None  # simulator profile name
    module: str | None = Field(None, pattern=r"^r\dc\d$")  # canopy position, e.g. r1c2
    vignette: str | None = None  # vignette room id for role=vignette
    kind: str | None = None  # vignette device kind: bulb, plug, lock, fan, string, path
    metadevice_id: str | None = None  # the Afero id; set only in the ops overlay
    expected_heads: int | None = Field(None, ge=1, le=90)
    orientation: Literal["A", "B"] | None = None
    writable: bool = True
    enabled: bool = True
    # Calibration (single module): wire order as [x, y] panel positions (orientation-A frame), or a path rule.
    wire_order: list[tuple[float, float]] | None = None
    cable_path: Literal["serpentine_rows", "serpentine_cols"] | None = None
    start_corner: Corner | None = None
    flags: DeviceFlags = Field(default_factory=DeviceFlags)
    flags_proven_on: str | None = None  # the driver address (metadevice id) of the kit that proved `flags`
    slot_hashes: dict[str, str] = Field(default_factory=dict)
    # Where each recorded slot hash came from: "api" = written by the Conductor's commissioning (T1), "app" =
    # hand-painted in the Hubspace app and recorded on the module page (T2). A hash without an entry is "api"
    # (before this field existed, commissioning was the only writer).
    slot_origin: dict[str, SlotOrigin] = Field(default_factory=dict)
    # The slot header's effect fields as read back when the hash was recorded (motionEffect, brightnessEffect, …).
    # The canonical hash deliberately leaves them out (07 §6), so they are verified separately.
    slot_effects: dict[str, dict[str, int]] = Field(default_factory=dict)
    notes: str = ""

    def slot_patch(self, slot: str, *, digest: str, origin: str, effects: Mapping[str, int] | None) -> dict:
        """The overlay patch that records one slot (hash, origin, effect fields), merged with the other slots."""
        all_effects = {k: dict(v) for k, v in (self.slot_effects or {}).items()}
        if effects:
            all_effects[slot] = {k: int(v) for k, v in effects.items()}
        else:
            all_effects.pop(slot, None)
        return {
            "slot_hashes": {**(self.slot_hashes or {}), slot: digest},
            "slot_origin": {**(self.slot_origin or {}), slot: origin},
            "slot_effects": all_effects,
        }

    def slot_record(self, slot: str) -> dict[str, Any] | None:
        """The recorded {hash, origin, effects} of `slot`, or None when nothing is recorded."""
        digest = (self.slot_hashes or {}).get(slot)
        if not digest:
            return None
        return {
            "hash": digest,
            "origin": (self.slot_origin or {}).get(slot, "api"),
            "effects": dict((self.slot_effects or {}).get(slot) or {}) or None,
        }

    def flags_for(self, address: str | None) -> DeviceFlags:
        """The bench flags, but only when they were proven on the kit at `address`; else none are proven.
        A different kit registered under this logical id (or flags saved without a kit) proves nothing."""
        if address and self.flags_proven_on == address:
            return self.flags
        return DeviceFlags()

    @field_validator("metadevice_id")
    @classmethod
    def _id_shape(cls, v: str | None) -> str | None:
        if v is not None and (not v.strip() or "/" in v or len(v) > 80):
            raise ValueError("metadevice_id must be the id copied from /ops (no slashes, ≤ 80 characters)")
        return v.strip() if v else v

    @property
    def sim_address(self) -> str:
        return f"sim-{self.id}"


class RegistryFile(_Strict):
    accounts: dict[str, dict[str, str]] = Field(default_factory=dict)
    device_defaults: dict[str, dict[str, Any]] = Field(default_factory=dict)
    devices: list[DeviceEntry]

    @model_validator(mode="after")
    def _checks(self) -> RegistryFile:
        seen, modules = set(), set()
        for d in self.devices:
            if d.id in seen:
                raise ValueError(f"device id {d.id!r} appears twice")
            seen.add(d.id)
            if d.account not in self.accounts:
                raise ValueError(
                    f"device {d.id!r} uses account {d.account!r}, which isn't listed under accounts"
                )
            if d.role == "canopy_module":
                if not d.module:
                    raise ValueError(f"canopy module {d.id!r} needs a module position like r0c1")
                if d.module in modules and d.enabled:
                    raise ValueError(f"two enabled devices claim canopy position {d.module}")
                if d.enabled:
                    modules.add(d.module)
            if d.role == "vignette":
                if not d.vignette:
                    raise ValueError(f"vignette device {d.id!r} needs `vignette:` (the room id, e.g. living)")
                if d.kind not in VIGNETTE_KINDS:
                    raise ValueError(
                        f"vignette device {d.id!r} needs `kind:` set to one of {', '.join(VIGNETTE_KINDS)}"
                    )
        ids = [d.metadevice_id for d in self.devices if d.metadevice_id]
        if len(ids) != len(set(ids)):
            raise ValueError("the same metadevice_id is assigned to two devices")
        return self


class OverlayFile(_Strict):
    devices: dict[str, dict[str, Any]] = Field(default_factory=dict)  # id → patch (or a full new entry)


@dataclass
class Registry:
    file: RegistryFile
    overlay: OverlayFile
    overlay_path: Path | None

    # -- queries
    @property
    def devices(self) -> list[DeviceEntry]:
        return [d for d in self.file.devices if d.enabled]

    def get(self, device_id: str) -> DeviceEntry:
        for d in self.file.devices:
            if d.id == device_id:
                return d
        raise KeyError(f"no device {device_id!r} in the registry")

    def by_role(self, role: str) -> list[DeviceEntry]:
        return [d for d in self.devices if d.role == role]

    def canopy_modules(self) -> list[DeviceEntry]:
        return sorted(self.by_role("canopy_module"), key=lambda d: d.module or "")

    def writable_metadevice_ids(self) -> list[str]:
        return [d.metadevice_id for d in self.devices if d.writable and d.metadevice_id]

    def address(self, device_id: str, driver_name: str) -> str | None:
        d = self.get(device_id)
        return d.sim_address if driver_name == "sim" else d.metadevice_id

    def logical_for(self, address: str) -> DeviceEntry | None:
        for d in self.file.devices:
            if address in (d.sim_address, d.metadevice_id):
                return d
        return None

    def accounts(self) -> dict[str, dict[str, str]]:
        return self.file.accounts


def _merge(base: dict, overlay: Mapping[str, dict[str, Any]], path: Path | str) -> dict:
    merged = dict(base)
    by_id = {d["id"]: dict(d) for d in merged.get("devices", [])}
    order = [d["id"] for d in merged.get("devices", [])]
    for device_id, patch in overlay.items():
        if device_id in by_id:
            by_id[device_id].update(patch)
        elif "role" in patch and "account" in patch:
            by_id[device_id] = {"id": device_id, **patch}
            order.append(device_id)
        else:
            raise ConfigError(
                f"{path}: override for {device_id!r} doesn't match a registry device, and isn't a full new entry "
                "(a new device needs at least role and account). Remove it or fix the id."
            )
    merged["devices"] = [by_id[i] for i in order]
    return merged


def _apply_defaults(raw: dict) -> dict:
    defaults = raw.get("device_defaults") or {}
    out = []
    for d in raw.get("devices") or []:
        out.append({**defaults.get(d.get("role"), {}), **d})
    return {**raw, "devices": out}


def load_registry(config_dir: Path = CONFIG_DIR, data: Path | None = None) -> Registry:
    base_path = config_dir / "devices.yaml"
    raw = _read_yaml(base_path)
    overlay_path = (data / "overrides" / "registry.yaml") if data else None
    try:
        overlay = OverlayFile.model_validate(_read_yaml(overlay_path) if overlay_path else {})
    except ValidationError as e:
        raise ConfigError(_explain(overlay_path, e)) from None
    merged = _apply_defaults(_merge(raw, overlay.devices, overlay_path or base_path))
    try:
        file = RegistryFile.model_validate(merged)
    except ValidationError as e:
        src = f"{base_path} + {overlay_path}" if overlay_path else str(base_path)
        raise ConfigError(_explain(src, e, merged)) from None
    return Registry(file, overlay, overlay_path)


def save_overlay_patch(
    registry: Registry, device_id: str, patch: Mapping[str, Any], *, config_dir: Path = CONFIG_DIR, now: float
) -> Registry:
    """Validate and persist an ops edit for one device; returns the reloaded registry. Keeps a history copy."""
    if registry.overlay_path is None:
        raise ConfigError(
            "No data directory is configured, so ops edits can't be saved (set CONDUCTOR_DATA_DIR)."
        )
    devices = {k: dict(v) for k, v in registry.overlay.devices.items()}
    devices[device_id] = {**devices.get(device_id, {}), **patch}
    candidate = {"devices": devices}
    raw = _read_yaml(config_dir / "devices.yaml")
    proposed = _apply_defaults(_merge(raw, devices, registry.overlay_path))
    try:
        RegistryFile.model_validate(proposed)
    except ValidationError as e:
        raise ConfigError(_explain("the proposed change", e, proposed)) from None
    text = yaml.safe_dump(candidate, sort_keys=True, allow_unicode=True).encode()
    stamp = datetime.fromtimestamp(now, UTC).strftime("%Y%m%dT%H%M%SZ")
    atomic_write(registry.overlay_path.parent / "history" / f"registry-{stamp}.yaml", text, mode=0o600)
    atomic_write(registry.overlay_path, text, mode=0o600)
    return load_registry(config_dir, registry.overlay_path.parent.parent)


def overlay_text(registry: Registry) -> str:
    """The overlay as YAML, for "download as a diff to commit back to git" (07 §8). Callers anonymize ids."""
    return yaml.safe_dump(registry.overlay.model_dump(), sort_keys=True, allow_unicode=True)


def unknown_devices(registry: Registry, seen_addresses: Iterable[str]) -> list[str]:
    """Metadevice ids an account can see that the registry doesn't list (stay read-only + alert, 07 §1)."""
    known = {d.metadevice_id for d in registry.file.devices if d.metadevice_id}
    return sorted(a for a in seen_addresses if a not in known)


# ---- colour recipes ------------------------------------------------------------------------------------------------
class RecipeEntry(_Strict):
    r: int = Field(0, ge=0, le=255)
    g: int = Field(0, ge=0, le=255)
    b: int = Field(0, ge=0, le=255)
    cct: int = Field(0, ge=0, le=10000)
    colorBrightness: int = Field(100, ge=0, le=100)
    whiteBrightness: int = Field(0, ge=0, le=100)


class StrandRecipe(_Strict):
    rgb: tuple[int, int, int] | None = None
    cct: int | None = None

    @model_validator(mode="after")
    def _one(self) -> StrandRecipe:
        if (self.rgb is None) == (self.cct is None):
            raise ValueError("a strand recipe needs exactly one of rgb or cct")
        return self


class ColorRecipe(_Strict):
    label: str
    hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    rgb_only: RecipeEntry
    blend: RecipeEntry
    strand: StrandRecipe


class RecipesFile(_Strict):
    colors: dict[str, ColorRecipe]


def load_recipes(config_dir: Path = CONFIG_DIR) -> RecipesFile:
    path = config_dir / "recipes.yaml"
    try:
        return RecipesFile.model_validate(_read_yaml(path))
    except ValidationError as e:
        raise ConfigError(_explain(path, e)) from None
