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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from conductor.tokens import atomic_write

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
# The ripple (D64): a mood lands in at most this many rings, and the last ring goes out at most this long after the
# first ((rings − 1) × kiosk.ripple_stagger_s), so every module is still sent within 05's 2.4 s spread.
RIPPLE_MAX_WAVES = 3
RIPPLE_SPAN_MAX_S = 2.4


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
    # CLAUDE.md: at most one write per second per device, so the budget can't be set below a second.
    min_write_interval_s: float = Field(1.0, ge=1.0)
    account_concurrency: int = Field(9, ge=1, le=20)
    max_attempts: int = Field(3, ge=1, le=6)
    backoff_s: list[float] = Field(default_factory=lambda: [1.0, 2.0, 4.0])
    readback_delay_s: float = Field(1.0, ge=0)
    readback_attempts: int = Field(3, ge=1, le=10)
    readback_interval_s: float = Field(1.0, ge=0.2)


class ReconcilerSettings(_Strict):
    # each module is read once per interval, the reads spread across the modules (reconciler.py); ≥ 1 s
    true_up_interval_s: float = Field(5, ge=1)
    reassert_after_s: float = 4
    downgrade_after_wrong_s: float = 10
    downgrade_throttles: int = 3
    downgrade_throttle_window_s: float = 60
    upgrade_after_clean_s: float = 300
    slot_rewrite_min_interval_s: float = 600
    outage_probe_interval_s: float = 10


class PreviewSettings(_Strict):
    """How the guest panel draws the canopy's 270 lights (the reflected-ceiling view, as config/canopy.yaml lists it:
    looking up, origin at the canopy's top-left). Set at install so the iPad's picture matches what a guest standing
    at the stand sees overhead: `mirror_x` flips it left to right, `rotate_deg` turns it by quarter turns, and
    `dim_floor` is the opacity of the dimmest guest level (the level is drawn as opacity, from this up to 1)."""

    mirror_x: bool = False
    rotate_deg: int = 0
    dim_floor: float = Field(0.35, ge=0.0, le=1.0)

    @field_validator("rotate_deg")
    @classmethod
    def _quarter_turn(cls, v: int) -> int:
        if v not in (0, 90, 180, 270):
            raise ValueError(
                f"rotate_deg must be 0, 90, 180 or 270 (a quarter turn of the ceiling view), not {v}"
            )
        return v


class KioskSettings(_Strict):
    cooldown_s: float = 8
    idle_return_s: float = 180
    idle_mode: str = "lowcountry-calm"
    # Guest brightness / power (D60): the lowest brightness a guest can set, and how long after the last guest input
    # the guest's brightness and power settings are cleared (power on, the look's designed brightness), optionally
    # also returning to `idle_scene` (None = stay on the current look).
    guest_brightness_min: int = Field(10, ge=1, le=100)
    idle_reset_s: float = Field(300, gt=0)
    idle_scene: str | None = None
    preview: PreviewSettings = Field(default_factory=PreviewSettings)
    # The ripple (D64): a mood chosen on the kiosk, by ops or by the idle return lands ring by ring in its design's
    # order, this many seconds between rings (each module still gets one PUT). 0 = one burst (today's behaviour).
    ripple_stagger_s: float = 0.0

    @field_validator("ripple_stagger_s")
    @classmethod
    def _ripple_stagger(cls, v: float) -> float:
        if not 0 <= v <= 1.5:
            raise ValueError(
                f"ripple_stagger_s must be from 0 (one burst) to 1.5 seconds between rings, not {v}"
            )
        span = (RIPPLE_MAX_WAVES - 1) * v
        if span > RIPPLE_SPAN_MAX_S + 1e-9:
            raise ValueError(
                f"ripple_stagger_s {v:g} s would send the last of {RIPPLE_MAX_WAVES} rings {span:g} s after the first; "
                f"keep that within {RIPPLE_SPAN_MAX_S:g} s (ripple_stagger_s ≤ "
                f"{RIPPLE_SPAN_MAX_S / (RIPPLE_MAX_WAVES - 1):g})"
            )
        return v


class ModulePageSettings(_Strict):
    lock_idle_timeout_s: float = 600
    sweep_step_s: float = Field(2.0, ge=1.0)


class EffectSettings(_Strict):
    enabled: dict[Literal["motionEffect", "brightnessEffect"], dict[int, str]] = Field(
        default_factory=lambda: {"motionEffect": {}, "brightnessEffect": {}}
    )


class SteppedMotionSettings(_Strict):
    """Stepped looks (D65, conductor/sequence.py): the Conductor re-sends a module-level design a step at a time.

    Sim-first: `live_enabled` stays false until the stepped rehearsal (Gate R) passes on real kits; only Alon flips it.
    `account_writes_per_min` is the account's cap on writes of any kind in any 60 s. The runner plans its steps at
    the cap less `budget_reserve` (the room left for 429 retries, silent-drop re-sends and a mood chosen mid-run), so
    its step is never shorter than `min_step_s`, nor than the account's share of that planned rate (9 modules at
    90/min less 20 % → 72/min → 7.5 s), and it holds a step back rather than send one that would take the
    account past the cap (`sequence.ProgramRunner._step`); above `max_step_s` the look holds still. The governor stops a run when, within
    `governor_window_s`, at least `governor_min_throttled` of the run's write attempts on one account came back 429/403
    and they are at least `governor_throttled_share` of its attempts; stepped runs on that account then wait
    `governor_cooloff_s`. A guest brightness change within `fold_guest_within_s` of the next step goes out with it."""

    live_enabled: bool = False
    min_step_s: float = Field(4.0, ge=4.0)
    max_step_s: float = Field(12.0, ge=4.0, le=60.0)
    account_writes_per_min: float = Field(90.0, gt=0, le=600)
    budget_reserve: float = Field(0.2, ge=0.05, le=0.5)
    governor_window_s: float = Field(60.0, ge=10)
    governor_min_throttled: int = Field(8, ge=1)
    governor_throttled_share: float = Field(0.25, gt=0, le=1)
    governor_cooloff_s: float = Field(300.0, ge=0)
    fold_guest_within_s: float = Field(2.0, ge=0, le=4)

    @model_validator(mode="after")
    def _range(self) -> SteppedMotionSettings:
        if self.max_step_s < self.min_step_s:
            raise ValueError(
                f"max_step_s ({self.max_step_s:g}) must be at least min_step_s ({self.min_step_s:g})"
            )
        if self.fold_guest_within_s >= self.min_step_s:
            raise ValueError(
                f"fold_guest_within_s ({self.fold_guest_within_s:g}) must be shorter than min_step_s "
                f"({self.min_step_s:g}), or every guest change would wait for a step"
            )
        return self


class MotionSettings(_Strict):
    stepped: SteppedMotionSettings = Field(default_factory=SteppedMotionSettings)


class LabSettings(_Strict):
    """The Design Lab's try-on (D66 Slice B, docs/design-lab/PLAN.md §6): a draft shown at module level on the real
    lights for at most `trial_max_s` (None = `kiosk.idle_return_s`). Validated to be at most `kiosk.idle_return_s`,
    so the idle hold never swaps the look under a try-on; `trial_bound_s` is the bound in force. A publish's operator
    slot repaint (D68): at most once per `repaint_min_interval_s` per scene."""

    trial_max_s: float | None = Field(None, gt=0, le=600)
    # The operator repaint (D68, PR B2): a publish of a new painted design repaints that scene's slot at most this
    # often (a sooner publish installs at once and queues the repaint; a revert is never blocked). The reconciler's
    # own drift/stale rewrite stays at reconciler.slot_rewrite_min_interval_s.
    repaint_min_interval_s: float = Field(120, ge=60, le=600)


class ConductorSettings(_Strict):
    executor: ExecutorSettings = Field(default_factory=ExecutorSettings)
    reconciler: ReconcilerSettings = Field(default_factory=ReconcilerSettings)
    kiosk: KioskSettings = Field(default_factory=KioskSettings)
    module_page: ModulePageSettings = Field(default_factory=ModulePageSettings)
    effects: EffectSettings = Field(default_factory=EffectSettings)
    motion: MotionSettings = Field(default_factory=MotionSettings)
    lab: LabSettings = Field(default_factory=LabSettings)

    @model_validator(mode="after")
    def _trial_within_idle(self) -> ConductorSettings:
        limit = self.lab.trial_max_s
        if limit is not None and limit > self.kiosk.idle_return_s:
            raise ValueError(
                f"lab.trial_max_s ({limit:g} s) must be at most kiosk.idle_return_s ({self.kiosk.idle_return_s:g} s), "
                "so the idle hold never swaps the look under a try-on"
            )
        return self


def trial_bound_s(settings: ConductorSettings) -> float:
    """How long a Design Lab try-on may run: min(lab.trial_max_s or kiosk.idle_return_s, kiosk.idle_return_s)."""
    idle = float(settings.kiosk.idle_return_s)
    limit = settings.lab.trial_max_s
    return min(float(limit), idle) if limit is not None else idle


def load_settings(config_dir: Path = CONFIG_DIR) -> ConductorSettings:
    path = config_dir / "conductor.yaml"
    try:
        return ConductorSettings.model_validate(_read_yaml(path))
    except ValidationError as e:
        raise ConfigError(_explain(path, e)) from None


# ---- registry ----------------------------------------------------------------------------------------------------
Role = Literal["bench", "canopy_module", "spare"]
Corner = Literal["top-left", "top-right", "bottom-left", "bottom-right"]
SlotOrigin = Literal["api", "app"]


class DeviceFlags(_Strict):
    """What the bench has proven for this device. None = not benched yet (live treats it as not proven)."""

    t2_select: bool | None = None  # Gate B: slot select by API works
    t1_frames: bool | None = None  # Gate C: frames written by API work
    readback_device_level: bool | None = None  # Gate B unplugged test: does readback reflect the device?


class FramebufferMapping(_Strict):
    """Observed physical-head to controller-entry mapping, valid only for the commissioned kit."""

    frame_length: int = Field(ge=1, le=256)
    indices: list[int] = Field(min_length=1, max_length=90)
    proven_on: str = Field(min_length=1, max_length=80)

    @field_validator("indices", mode="before")
    @classmethod
    def _whole_indices(cls, values):
        if not isinstance(values, list) or any(type(v) is not int for v in values):
            raise ValueError("indices must be whole controller addresses")
        return values

    @model_validator(mode="after")
    def _valid_indices(self):
        if len(set(self.indices)) != len(self.indices) or any(
            i < 0 or i >= self.frame_length for i in self.indices
        ):
            raise ValueError("indices must be unique addresses inside the controller frame")
        return self


class DeviceEntry(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,40}$")
    role: Role
    account: str
    label: str = ""
    model: str = ""
    profile: str | None = None  # simulator profile name
    module: str | None = Field(None, pattern=r"^r\dc\d$")  # canopy position, e.g. r1c2
    metadevice_id: str | None = None  # the Afero id; set only in the ops overlay
    expected_heads: int | None = Field(None, ge=1, le=90)
    framebuffer_mapping: FramebufferMapping | None = None
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
    # Old data tolerated, not migrated (D67): overlay entries for the removed vignette devices, ignored …
    dropped: list[str] = field(default_factory=list)
    # … and removed fields (`vignette`, `kind`, a removed `profile`) found on surviving entries, ignored: id → fields.
    dropped_fields: dict[str, list[str]] = field(default_factory=dict)

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


# The Conductor controls the canopy only (D67). These are the vignette devices config/devices.yaml listed until then
# (6ce971b): an overlay entry for one of them — or any entry that says `role: vignette` — is old data from before
# the removal. It is ignored (a "Needs attention" line on /ops says so) and pruned by the next ops registry edit,
# never a failed start: launchd would restart a Conductor that refuses its own data folder forever.
REMOVED_DEVICE_IDS = frozenset(
    {
        "entry-deadbolt",
        "living-lamp-1",
        "living-lamp-2",
        "living-lamp-3",
        "living-lamp-4",
        "living-plug",
        "bedroom-lamp-1",
        "bedroom-lamp-2",
        "bedroom-lamp-3",
        "bedroom-lamp-4",
        "bedroom-plug",
        "bedroom-fan",
        "garden-string",
        "garden-path",
        "garden-plug",
    }
)
REMOVED_ROLES = frozenset({"vignette"})
# Device fields that went with the vignettes; on a surviving entry (e.g. a spare copied from an older overlay) they are
# ignored rather than refused by `extra="forbid"`.
REMOVED_FIELDS = ("vignette", "kind")
# Simulator profiles that went with the vignettes (conductor/sim/profiles, 6ce971b). A surviving entry naming one would
# make SimDriver refuse to start (it builds every enabled entry, on the live box too), so the overlay's `profile` is
# ignored when its value is one of these — the devices.yaml or role default applies — and pruned like the fields above.
REMOVED_PROFILES = frozenset(
    {"ecosmart-a19-rgb", "outdoor-plug-2", "indoor-plug", "defiant-deadbolt", "ceiling-fan", "path-lights"}
)


@dataclass
class Dropped:
    """What `_merge` ignored from the overlay as old data (D67)."""

    ids: list[str] = field(default_factory=list)  # whole entries: removed device ids or role: vignette
    fields: dict[str, list[str]] = field(
        default_factory=dict
    )  # device id → removed field names (or `profile`)


def _is_removed(device_id: str, patch: Mapping[str, Any]) -> bool:
    return device_id in REMOVED_DEVICE_IDS or patch.get("role") in REMOVED_ROLES


def _merge(base: dict, overlay: Mapping[str, dict[str, Any]], path: Path | str) -> tuple[dict, Dropped]:
    merged = dict(base)
    by_id = {d["id"]: dict(d) for d in merged.get("devices", [])}
    order = [d["id"] for d in merged.get("devices", [])]
    dropped = Dropped()
    for device_id, raw_patch in overlay.items():
        if _is_removed(device_id, raw_patch):
            dropped.ids.append(device_id)
            continue
        patch = dict(raw_patch)
        gone = [k for k in REMOVED_FIELDS if k in patch]
        if patch.get("profile") in REMOVED_PROFILES:
            gone.append("profile")
        for key in gone:
            patch.pop(key)
        if gone:
            dropped.fields[device_id] = gone
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
    return merged, dropped


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
    combined, dropped = _merge(raw, overlay.devices, overlay_path or base_path)
    merged = _apply_defaults(combined)
    try:
        file = RegistryFile.model_validate(merged)
    except ValidationError as e:
        src = f"{base_path} + {overlay_path}" if overlay_path else str(base_path)
        raise ConfigError(_explain(src, e, merged)) from None
    return Registry(file, overlay, overlay_path, dropped=dropped.ids, dropped_fields=dropped.fields)


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
    raw = _read_yaml(config_dir / "devices.yaml")
    combined, dropped = _merge(raw, devices, registry.overlay_path)
    # The first ops edit after the canopy-only deploy prunes the old data `_merge` ignored (D67), so the written
    # overlay is exactly what the registry uses and the "Needs attention" line clears.
    for gone in dropped.ids:
        devices.pop(gone, None)
    for kept, keys in dropped.fields.items():
        for key in keys:
            devices[kept].pop(key, None)
    candidate = {"devices": devices}
    proposed = _apply_defaults(combined)
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


# Fields the overlay may hold that never go into git (devices.yaml: "never with real ids in git"): the Afero
# metadevice id, and the address of the kit that proved the bench flags (also a metadevice id).
NOT_FOR_GIT = ("metadevice_id", "flags_proven_on", "framebuffer_mapping")


@dataclass
class OverlayDiff:
    """The ops overlay folded into config/devices.yaml, as text (07 §8: "downloadable as a diff to commit back")."""

    before: str  # config/devices.yaml as it is in git
    after: str  # the same file with every overlay edit applied, line by line (comments and layout kept)
    changed: list[str]  # device ids whose entry changed or was added
    left_out: list[str]  # device ids with a field left out for git (NOT_FOR_GIT)
    problem: str = (
        ""  # set when the edited file didn't re-read as the same registry (then `after` is `before`)
    )


def _flow(entry: Mapping[str, Any]) -> str:
    return yaml.safe_dump(
        dict(entry), default_flow_style=True, sort_keys=False, width=10**9, allow_unicode=True
    ).strip()


def overlay_diff(registry: Registry, config_dir: Path = CONFIG_DIR) -> OverlayDiff:
    """Apply the overlay's device edits to devices.yaml's own lines: a one-line `- {id: …}` entry is rewritten in
    place as one line, a block entry as a block, a new device is appended to the list. Fields in NOT_FOR_GIT are left
    out. The result is re-read and checked against the merged registry before it is offered."""
    import re

    path = config_dir / "devices.yaml"
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    raw = _read_yaml(path)
    base = {d["id"]: d for d in raw.get("devices") or [] if isinstance(d, dict) and "id" in d}
    lines = before.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    changed: list[str] = []
    left_out: list[str] = []
    expected: dict[str, dict] = {}
    appended: list[str] = []
    for device_id, patch in sorted(registry.overlay.devices.items()):
        if device_id in registry.dropped:
            continue  # old data the registry ignores (D67): never offered to git
        ignored = set(registry.dropped_fields.get(device_id, ()))  # removed fields the registry ignores (D67)
        patch = {k: v for k, v in patch.items() if k not in ignored}
        clean = {k: v for k, v in patch.items() if k not in NOT_FOR_GIT}
        if len(clean) != len(patch):
            left_out.append(device_id)
        if device_id not in base:
            if not clean:
                continue
            entry = {"id": device_id, **clean}
            expected[device_id] = entry
            appended.append(f"  - {_flow(entry)}\n")
            changed.append(device_id)
            continue
        merged = {**base[device_id], **clean}
        if merged == base[device_id]:
            continue
        expected[device_id] = merged
        flow = re.compile(rf"^(\s*)-\s*\{{\s*id:\s*{re.escape(device_id)}\s*[,}}]")
        block = re.compile(rf"^(\s*)-\s+id:\s*{re.escape(device_id)}\s*(#.*)?$")
        for i, line in enumerate(lines):
            if m := flow.match(line):
                lines[i] = f"{m.group(1)}- {_flow(merged)}\n"
                break
            if m := block.match(line.rstrip("\n")):
                indent = m.group(1)
                end = i + 1
                while (
                    end < len(lines)
                    and lines[end].strip()
                    and (len(lines[end]) - len(lines[end].lstrip()) > len(indent))
                ):
                    end += 1
                dumped = yaml.safe_dump(
                    [merged], default_flow_style=False, sort_keys=False, allow_unicode=True
                )
                lines[i:end] = [f"{indent}{text}\n" for text in dumped.splitlines()]
                break
        else:
            return OverlayDiff(before, before, [], left_out, f"couldn't find {device_id} in {path.name}")
        changed.append(device_id)
    after = "".join(lines + appended)
    # Re-read the edited file: every changed entry must come back exactly as merged, and the whole file must
    # still validate — otherwise offer no diff rather than a wrong one.
    try:
        reread = yaml.safe_load(after) or {}
        RegistryFile.model_validate(_apply_defaults(reread))
        got = {d["id"]: d for d in reread.get("devices") or []}
        wrong = [d for d, entry in expected.items() if got.get(d) != entry]
        unchanged = [d for d in base if d not in expected and got.get(d) != base[d]]
    except (yaml.YAMLError, ValidationError) as e:
        return OverlayDiff(before, before, [], left_out, f"the edited {path.name} didn't validate: {e}")
    if wrong or unchanged:
        return OverlayDiff(
            before,
            before,
            [],
            left_out,
            f"the edited {path.name} re-read differently for {', '.join(wrong + unchanged)}",
        )
    return OverlayDiff(before, after, changed, left_out)


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
