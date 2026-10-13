"""The look compiler: a Lowcountry Mode at a tier → a ModePlan with exactly one PUT's rows per module (04, 07 §5–§6).

Rules implemented:
- **One PUT per module.** `ModuleTarget.rows` is the full state vector for show time (power, the selector,
  brightness, speed, party mode off — never telemetry rows). A T1 slot object is NOT in `rows`: it is written at
  commissioning (`slot_value`) and only selected at show time. `expect` = `rows` plus, for T1, the slot object, so
  readback comparison (`rows.compare`, which compares slot objects by canonical hash) sees a drifted slot.
- **Only what the device declares.** Capabilities come from each device's own profile (`capabilities.detect`):
  CCT is clamped into the device range with a note (Goodnight's 2200 K → 2700 K on the sibling profile), presets
  must be listed by every module (Holiday picks the first candidate all nine list, else its fallback look and
  fallback copy), white/RGB/program looks need their mode.
- **Frames copy the device's header** (`rows.frame_value`): header and `frameBuffer.flags` come from the slot's
  readback (the default header when none was read yet — with a note), the frame length is the device's, and the
  only effect codes written are bench-enabled ones (`settings.effects.enabled`); everything else is 0 = static.
- **Honest copy.** `ModePlan.feature` is the sentence of the technique actually compiled. A canvas look is
  `motion=True` with `motion_feature` only when its `motionEffect` code is bench-enabled; otherwise it is static,
  says so in a note, and uses the static `feature` (the schema refuses movement words there).
- **Bench proof.** With `flags` (live hardware), T1 needs Gate C (`t1_frames`) and T2 needs Gate B (`t2_select`) on
  every module; without flags (the simulator) every tier is allowed (07 §6 "sim runs T1"). T2 needs a hash recorded
  as **hand-painted in the Hubspace app** (origin "app", module page → Slots) on every module: a hash commissioning
  recorded is the Conductor's own canvas and never makes the "painted in the app" copy true.
- **Motion follows the device.** A canvas look's copy claims motion only when every module's slot holds the
  bench-enabled code (the effect fields recorded from readback at commissioning, else the slot header's readback);
  `motion` (re-trigger all nine as one burst) is set whenever a slot may animate. At T2, a `motion: true` mode whose
  hand-painted slot animates — or whose header is unknown — is a motion plan too.
- **Partial plans.** A module without capabilities is left out (`ModePlan.skipped`; the engine shows it as needing
  attention) and copy that counts strands or lights counts the modules actually driven ("both strands", "its one
  strand"); at module level the "column by column across the canopy" sentence becomes one that says what each
  driven strand shows. The safe look (`SAFE_MODES`) never waits: it assumes `SAFE_DEFAULT_CAPS` for an unread
  module. With `addresses` (the show), a module with no device on the engine's driver is left out of every look,
  the safe one included (D62); with none registered at all, no look can be planned.
- **Kelvin in the copy.** A strand look whose copy names its Kelvin value uses the configured fallback look when a
  module's range would clamp that value (Lowcountry Calm never says "2700 K" at 3000 K).
- **A kit's controller map (D54).** With `addresses` (the show), a module whose `framebuffer_mapping` was proven on
  the device at its address (`conductor.framemap.frame_indices`) takes T1 frames of its full controller length: its
  heads at their mapped entries, every other entry OFF. A saved map that doesn't match the kit, or a longer frame with
  no map, refuses T1 with `conductor.framemap.head_check`'s reason (whole-strand tiers and app-painted T2 slots still
  compile). Without `addresses` no map is consulted.
- **A sampled module look (T3 `sample`).** Each module is set to one RGB colour: the canvas (a 2-D field or a
  gradient) at the module's centre ((col + ½)·75, (row + ½)·40 in), or the mean of its 30 heads' colours with
  `at: mean`, at the sample's brightness — the same on every module (D60). An x-linear field sampled at the centres
  is the scene's literal columns (D59). When the design is finer than the module grid (it repeats with
  centre sampling, or a whole canopy samples to one colour) a note says so; when the whole canopy samples to one
  colour the feature says that instead of the design's layout (kiosk copy is true).
- `ModuleTarget.preview` is `render.head_colors` of the intended result, per head in wire order (read through the
  kit's map when it has one); `ModuleTarget.colors` is the same render with the brightness row at 100 (full-strength
  colours for the guest panel, which shows the level on its own).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from conductor import framemap, render
from conductor import rows as R
from conductor.capabilities import Capabilities
from conductor.config import CONFIG_DIR, DeviceEntry, DeviceFlags, RecipesFile, load_recipes
from conductor.drivers.base import StateRow
from conductor.engine_api import ModePlan, ModuleTarget, PlanError, Tier
from conductor.geometry import load_canopy_config, panel_lattice
from conductor.looks import field as F
from conductor.looks.composer import compose_canvas, evaluate, head_positions, parse_position, placement_for
from conductor.looks.schema import FieldCanvas, LookSpec, Mode, ModesFile, ModuleSample, load_modes

NUMBER_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
}
STATIC_EFFECTS = {"motionEffect": 0, "brightnessEffect": 0}
FINER_THAN_GRID = (
    "The pattern repeats faster than the module grid; at module level some bands disappear (try at: mean)"
)
SAFE_MODES = frozenset({"lowcountry-calm"})  # the safe look: must compile even before a profile is read
# What the safe look assumes for a module whose profile hasn't been read (the sibling string's documented white
# range, 04): enough for a whole-strand white or RGB look, nothing painted.
SAFE_DEFAULT_CAPS = Capabilities(
    power=True, brightness_range=(1, 100), cct_range=(2700, 6500), rgb=True, color_modes=["white", "color"]
)


@lru_cache(maxsize=4)
def _default_modes(config_dir: str) -> ModesFile:
    return load_modes(Path(config_dir))


@lru_cache(maxsize=4)
def _default_recipes(config_dir: str) -> RecipesFile:
    return load_recipes(Path(config_dir))


@lru_cache(maxsize=4)
def _default_canopy(config_dir: str) -> dict:
    path = Path(config_dir) / "canopy.yaml"
    return load_canopy_config(path) if path.exists() else load_canopy_config()


@dataclass
class CompileContext:
    """Everything compile_mode needs about the canopy right now.

    - `registry`: a `Registry` (its enabled canopy modules are used) or an explicit list of `DeviceEntry`
      (e.g. one bench kit placed at a chosen canopy position, with `module` set).
    - `capabilities`: device id → `Capabilities` from that device's profile + state readback.
    - `slot_readback`: device id → slot → the slot object as last read back (header source for T1; the
      hand-painted content for a T2 preview). Missing = the default header, with a note.
    - `effects_enabled`: `settings.effects.enabled` ({"motionEffect": {code: note}, "brightnessEffect": {...}}).
    - `recorded_hashes`: device id → slot → the hash recorded after commissioning / hand-painting. None = use each
      registry entry's `slot_hashes` (with its `slot_origin` and `slot_effects`).
    - `recorded_origins` / `recorded_effects`: with `recorded_hashes`, where each hash came from ("api" =
      commissioned by the Conductor, "app" = hand-painted in the Hubspace app; default "api") and the slot header's
      effect fields read back when it was recorded.
    - `safe_modes`: modes that compile with conservative capabilities (`SAFE_DEFAULT_CAPS`) for a module whose
      profile hasn't been read. Every other mode leaves such a module out of the plan (`ModePlan.skipped`).
    - `flags`: device id → bench `DeviceFlags` (already resolved for the kit at that address). None = simulator:
      every tier allowed.
    - `addresses`: device id → the driver address its capabilities were read from (the Afero metadevice id live, the
      sim address in sim). A kit's `framebuffer_mapping` applies only through this (D54, D25). A module whose address
      is missing here has no device registered on this runtime and is left out of every look, the safe one included
      (D62). None = no map is used and no module is left out for want of an address.
    - `modes`, `recipes`, `canopy`: loaded from `config_dir` when not given.
    """

    registry: Any
    capabilities: Mapping[str, Capabilities]
    slot_readback: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    effects_enabled: Mapping[str, Mapping[int, str]] | None = None
    recorded_hashes: Mapping[str, Mapping[str, str]] | None = None
    flags: Mapping[str, DeviceFlags] | None = None
    recorded_origins: Mapping[str, Mapping[str, str]] | None = None
    recorded_effects: Mapping[str, Mapping[str, Mapping[str, int]]] | None = None
    safe_modes: frozenset[str] = SAFE_MODES
    modes: ModesFile | None = None
    recipes: RecipesFile | None = None
    canopy: dict | None = None
    config_dir: Path = CONFIG_DIR
    addresses: Mapping[str, str | None] | None = None

    def __post_init__(self) -> None:
        key = str(self.config_dir)
        if self.modes is None:
            self.modes = _default_modes(key)
        if self.recipes is None:
            self.recipes = _default_recipes(key)
        if self.canopy is None:
            self.canopy = _default_canopy(key)
        if self.effects_enabled is None:
            self.effects_enabled = {"motionEffect": {}, "brightnessEffect": {}}

    @classmethod
    def from_settings(
        cls, *, registry: Any, capabilities: Mapping[str, Capabilities], settings: Any, **kw: Any
    ):
        """Convenience: `effects_enabled` taken from a `ConductorSettings`."""
        return cls(
            registry=registry,
            capabilities=capabilities,
            effects_enabled={k: dict(v) for k, v in settings.effects.enabled.items()},
            **kw,
        )

    @property
    def devices(self) -> list[DeviceEntry]:
        reg = self.registry
        if hasattr(reg, "canopy_modules"):
            return list(reg.canopy_modules())
        return list(reg)

    def caps(self, device_id: str) -> Capabilities:
        caps = self.capabilities.get(device_id)
        if caps is None:
            raise PlanError(f"{device_id} hasn't been read yet (no capabilities), so no look can be planned")
        return caps

    def record(self, entry: DeviceEntry, slot: str) -> dict[str, Any] | None:
        """The recorded {hash, origin, effects} of `slot` on this module, or None."""
        if self.recorded_hashes is None:
            return entry.slot_record(slot)
        digest = (self.recorded_hashes.get(entry.id) or {}).get(slot)
        if not digest:
            return None
        effects = ((self.recorded_effects or {}).get(entry.id) or {}).get(slot)
        return {
            "hash": digest,
            "origin": ((self.recorded_origins or {}).get(entry.id) or {}).get(slot, "api"),
            "effects": dict(effects) if effects else None,
        }

    def recorded(self, entry: DeviceEntry, slot: str, origin: str | None = None) -> str | None:
        """The recorded hash (only one of that origin when `origin` is given)."""
        rec = self.record(entry, slot)
        if rec is None or (origin is not None and rec["origin"] != origin):
            return None
        return rec["hash"]

    def held_effects(self, entry: DeviceEntry, slot: str, origin: str) -> dict[str, int] | None:
        """The effect fields the device's slot holds: recorded with a hash of `origin`, else the slot header's
        readback, else None (unknown)."""
        rec = self.record(entry, slot)
        if rec is not None and rec["origin"] == origin and rec["effects"] is not None:
            return rec["effects"]
        return R.slot_effects(self.readback(entry.id, slot))

    def readback(self, device_id: str, slot: str) -> Any:
        return (self.slot_readback.get(device_id) or {}).get(slot)

    def frame_indices(self, entry: DeviceEntry, caps: Capabilities) -> list[int] | None:
        """The module's proven controller map (D54) for the device at its address, or None (see `addresses`)."""
        if self.addresses is None or self.canopy is None:
            return None
        heads = len(panel_lattice(self.canopy))
        return framemap.frame_indices(entry, self.addresses.get(entry.id), caps, heads)


# ---- public API --------------------------------------------------------------------------------------------------
def _modes(modes: ModesFile | None) -> ModesFile:
    return modes if modes is not None else _default_modes(str(CONFIG_DIR))


def ladder(mode_id: str, modes: ModesFile | None = None) -> list[Tier]:
    """The mode's fallback ladder, best tier first (config/modes.yaml)."""
    try:
        mode = _modes(modes).mode(mode_id)
    except KeyError as e:
        raise PlanError(str(e.args[0])) from None
    return [Tier(name) for name in mode.ladder]


def compile_mode(mode_id: str, tier: Tier | str, ctx: CompileContext) -> ModePlan:
    """The plan for `mode_id` at `tier`, or PlanError with an operator-safe reason."""
    assert ctx.modes is not None
    try:
        mode = ctx.modes.mode(mode_id)
        tier = Tier(tier)
    except (KeyError, ValueError) as e:
        raise PlanError(str(e.args[0]) if e.args else str(e)) from None
    if str(tier) not in mode.ladder:
        raise PlanError(f"{mode.label} has no {tier} technique (its ladder is {', '.join(mode.ladder)})")
    devices = ctx.devices
    if not devices:
        raise PlanError("there are no canopy modules in the registry")
    # D62: a module with no device on the engine's driver can't be written, so it is left out of every look — the
    # safe one included (never assumed capabilities, never stale ones). Without `addresses` nothing is left out here.
    unregistered = {
        d.id: f"{d.label or d.id} has no Hubspace device registered on this runtime, so it was left out of this look "
        "(register one on /ops/accounts)"
        for d in devices
        if ctx.addresses is not None and not ctx.addresses.get(d.id)
    }
    drivable = [d for d in devices if d.id not in unregistered]
    assumed = []
    if mode_id in ctx.safe_modes:
        # The safe look never waits for profiles: a module that hasn't been read gets conservative capabilities.
        assumed = [d for d in drivable if d.id not in ctx.capabilities]
        if assumed:
            ctx = replace(
                ctx, capabilities={**ctx.capabilities, **{d.id: SAFE_DEFAULT_CAPS for d in assumed}}
            )
    ready = [d for d in drivable if d.id in ctx.capabilities]
    unread = [d.id for d in drivable if d.id not in ctx.capabilities]
    skipped = {
        d.id: unregistered.get(d.id)
        or f"{d.label or d.id}'s profile hasn't been read yet (no capabilities), so it was left out of this look"
        for d in devices
        if d.id in unregistered or d.id not in ctx.capabilities
    }
    if not ready:
        if not drivable:
            raise PlanError(
                "no canopy module has a Hubspace device registered on this runtime yet (register one on "
                "/ops/accounts), so no look can be planned"
            )
        raise PlanError(
            f"no module's profile has been read yet ({', '.join(unread)} hasn't been read yet), so no look can "
            "be planned"
        )
    if tier is Tier.STRAND:
        plan = _strand(mode_id, mode, ctx, ready)
    elif tier is Tier.T1:
        plan = _canvas(mode_id, mode, ctx, ready)
    elif tier is Tier.T2:
        plan = _slot(mode_id, mode, ctx, ready)
    else:
        plan = _modules(mode_id, mode, ctx, ready)
    if assumed:
        plan.notes.append(
            "Profile not read yet, so the safe look assumes the sibling string's white range (2700–6500 K): "
            + ", ".join(d.id for d in assumed)
        )
    if skipped:
        plan.skipped = skipped
        if tier is Tier.T3:
            # "the palette steps across the canopy column by column" isn't true of the modules that are left
            plan.feature = _partial_modules_feature(len(ready))
        else:
            plan.feature = _counted(plan.feature, ready, devices)
        if unregistered:
            plan.notes.append(f"Left out (no Hubspace device registered): {', '.join(unregistered)}")
        if unread:
            plan.notes.append(f"Left out (no capabilities yet): {', '.join(unread)}")
    return plan


def _counted(feature: str, ready: Sequence[DeviceEntry], devices: Sequence[DeviceEntry]) -> str:
    """Copy that counts strands or lights, corrected for the modules actually in the plan (kiosk copy is true)."""
    total, n = len(devices), len(ready)
    out = feature
    for word, repl in ((_count_words(total), _count_words(n)), (str(total), str(n))):
        out = re.sub(
            rf"\b{re.escape(word)}\b",
            lambda m, repl=repl: repl.capitalize() if m.group(0)[0].isupper() else repl,
            out,
            flags=re.IGNORECASE,
        )
    heads_all = sum(d.expected_heads or 30 for d in devices)
    heads_ready = sum(d.expected_heads or 30 for d in ready)
    return _small_counts(re.sub(rf"\b{heads_all}\b", str(heads_ready), out), n)


def _small_counts(text: str, n: int) -> str:
    """English for two and one: "all two strands" → "both strands", "all one strands" → "one strand"."""
    if n == 2:
        return re.sub(
            r"\ball two\b", lambda m: "Both" if m.group(0)[0].isupper() else "both", text, flags=re.IGNORECASE
        )
    if n == 1:
        text = re.sub(
            r"\ball one (Hubspace )?strands\b",
            lambda m: ("One " if m.group(0)[0].isupper() else "one ") + (m.group(1) or "") + "strand",
            text,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\beach of one strands\b", "its one strand", text)
    return text


def _partial_modules_feature(n: int) -> str:
    """The module-level (T3) sentence for a plan that leaves modules out: what each driven strand shows."""
    if n == 1:
        return "Colour and dimming: one Hubspace strand set to a single colour from the scene."
    # Not "from the scene's palette": each colour is the scene's gradient sampled at the module's column centre,
    # usually a blend between two palette colours (config/modes.yaml).
    return (
        f"Colour and dimming: {_count_words(n)} Hubspace strands, each set to a single colour from the scene."
    )


def compile_first(mode_id: str, ctx: CompileContext, *, skip: Sequence[Tier | str] = ()) -> ModePlan:
    """The first tier of the ladder that compiles (skipping `skip`, e.g. a tier the engine downgraded from).
    The plan's notes say why better tiers were passed over. PlanError when none compiles."""
    reasons: list[str] = []
    skipped = {str(Tier(t)) for t in skip}
    assert ctx.modes is not None
    for tier in ladder(mode_id, ctx.modes):
        if str(tier) in skipped:
            reasons.append(f"{tier}: skipped")
            continue
        try:
            plan = compile_mode(mode_id, tier, ctx)
        except PlanError as e:
            reasons.append(f"{tier}: {e}")
            continue
        plan.notes = [f"Not at tier {r}" for r in reasons] + plan.notes
        return plan
    raise PlanError(f"{mode_id} can't run at any tier — " + "; ".join(reasons))


def available_tiers(mode_id: str, ctx: CompileContext) -> list[Tier]:
    """The ladder tiers that compile right now, best first."""
    assert ctx.modes is not None
    out = []
    for tier in ladder(mode_id, ctx.modes):
        try:
            compile_mode(mode_id, tier, ctx)
        except PlanError:
            continue
        out.append(tier)
    return out


# ---- helpers -----------------------------------------------------------------------------------------------------
def _count_words(n: int) -> str:
    return NUMBER_WORDS.get(n, str(n))


def _heads(entry: DeviceEntry, caps: Capabilities) -> int:
    return caps.framebuffer_length or entry.expected_heads or 30


class _Notes:
    """Notes that say the same thing for several modules collapse into one line naming how many."""

    def __init__(self) -> None:
        self._by_text: dict[str, list[str]] = {}

    def add(self, text: str, device_id: str | None = None) -> None:
        self._by_text.setdefault(text, [])
        if device_id and device_id not in self._by_text[text]:
            self._by_text[text].append(device_id)

    def lines(self, total: int) -> list[str]:
        out = []
        for text, ids in self._by_text.items():
            if not ids:
                out.append(text)
            elif len(ids) == total and total > 1:
                out.append(f"{text} (all {total} modules)")
            else:
                out.append(f"{text} ({', '.join(ids)})")
        return out


def _look_problem(spec: LookSpec, caps: Capabilities) -> str | None:
    """Why this device can't show this whole-strand look at all (clamping aside), or None."""
    modes = set(caps.color_modes)
    if spec.kind == "white":
        if modes and "white" not in modes:
            return "it has no white mode"
        if caps.cct_range is None:
            return "it lists no colour temperature range"
    elif spec.kind == "rgb":
        if not caps.rgb or (modes and "color" not in modes):
            return "it takes no RGB colour"
    elif spec.kind == "preset":
        if modes and "sequence" not in modes:
            return "it has no preset (sequence) mode"
        if spec.preset and spec.preset not in caps.presets:
            return f"it doesn't list the preset {spec.preset!r}"
    elif spec.kind == "program":
        if modes and "sequence" not in modes:
            return "it has no program (sequence) mode"
        if spec.program not in caps.programs:
            return f"it doesn't list the program {spec.program!r}"
    return None


def _to_look(spec: LookSpec, caps: Capabilities, notes: _Notes, device_id: str, preset: str | None = None):
    """A rows.Look for this device: CCT clamped into its range (with a note), brightness/speed into its ranges."""
    cct = spec.cct
    if spec.kind == "white" and cct is not None and caps.cct_range:
        lo, hi = caps.cct_range
        clamped = max(lo, min(hi, cct))
        if clamped != cct:
            edge = "floor" if clamped == lo else "ceiling"
            notes.add(
                f"{cct} K clamped to the device {edge} {clamped} K — its range is {lo}–{hi} K", device_id
            )
            cct = clamped
    lo, hi = caps.brightness_range or (1, 100)
    level = max(lo, min(hi, spec.brightness))
    speed = spec.speed
    if caps.speed_range:
        speed = max(caps.speed_range[0], min(caps.speed_range[1], speed))
    return R.Look(
        kind=spec.kind,
        brightness=level,
        cct=cct,
        rgb=tuple(spec.rgb) if spec.rgb else None,
        preset=preset or spec.preset,
        program=spec.program,
        speed=speed,
    )


def _named_kelvin_clamped(
    spec: LookSpec, feature: str, devices: Sequence[DeviceEntry], caps: Mapping[str, Capabilities]
) -> str | None:
    """Why the copy can't stand: it names `spec.cct` in Kelvin but a module's range clamps it. None when fine."""
    if spec.kind != "white" or not spec.cct or not re.search(rf"\b{spec.cct}\s?K\b", feature):
        return None
    for d in devices:
        rng = caps[d.id].cct_range
        if rng and not rng[0] <= spec.cct <= rng[1]:
            return f"{d.id}: {spec.cct} K is outside its colour temperature range {rng[0]}–{rng[1]} K"
    return None


def _generic_feature(spec: LookSpec, count: int) -> str:
    """A true sentence for a fallback look the mode's config gave no copy for."""
    n = _count_words(count)
    if spec.kind == "white":
        out = f"Tunable white and dimming: all {n} Hubspace strands set to one white."
    elif spec.kind == "rgb":
        out = f"Colour and dimming: all {n} Hubspace strands set to one colour."
    elif spec.kind == "program":
        out = f"A built-in Hubspace lighting program on all {n} strands."
    elif spec.kind == "preset":
        out = "A built-in Hubspace preset, running on each strand's own controller."
    else:
        out = f"All {n} Hubspace strands switched off."
    return _small_counts(out, count)


def _target(
    entry: DeviceEntry,
    caps: Capabilities,
    rows: list[StateRow],
    *,
    expect: list[StateRow] | None = None,
    slot: str | None = None,
    slot_value: dict | None = None,
    slot_hash: str | None = None,
    preview_rows: list[StateRow] | None = None,
    indices: list[int] | None = None,
) -> ModuleTarget:
    rows = R.dedupe(rows)
    shown = preview_rows if preview_rows is not None else rows
    n = len(indices) if indices is not None else _heads(entry, caps)
    preview = render.head_colors(shown, n, caps, indices)
    # the same heads at full strength: the brightness row replaced by 100 (the device level is shown on its own)
    full = [R.brightness(100) if r.key == ("brightness", None) else r for r in shown]
    colors = render.head_colors(full, n, caps, indices)
    return ModuleTarget(
        device_id=entry.id,
        rows=rows,
        expect=list(expect if expect is not None else rows),
        slot=slot,
        slot_value=slot_value,
        slot_hash=slot_hash,
        brightness_range=caps.brightness_range,
        preview=preview,
        colors=colors,
    )


def _vector(look: R.Look, caps: Capabilities) -> list[StateRow]:
    try:
        return R.full_state_vector(look, cct_range=caps.cct_range)
    except R.PayloadError as e:
        raise PlanError(str(e)) from None


# ---- strand ------------------------------------------------------------------------------------------------------
def _strand(mode_id: str, mode: Mode, ctx: CompileContext, devices: list[DeviceEntry]) -> ModePlan:
    tech = mode.techniques.strand
    assert tech is not None
    notes = _Notes()
    caps = {d.id: ctx.caps(d.id) for d in devices}
    spec, feature, preset = tech.look, tech.feature, None
    reason: str | None = None
    if spec.kind == "preset" and not spec.preset:
        preset = next((p for p in tech.presets if all(p in c.presets for c in caps.values())), None)
        if preset is None:
            reason = f"none of the presets {', '.join(tech.presets)} is listed by every module"
        else:
            notes.add(f"Preset {preset!r} (the first of {', '.join(tech.presets)} that every module lists)")
    if reason is None:
        for d in devices:
            if problem := _look_problem(spec, caps[d.id]):
                reason = f"{d.id}: {problem}"
                break
    if reason is None and (problem := _named_kelvin_clamped(spec, feature, devices, caps)):
        # The copy names a Kelvin value a module can't show: never claim it (kiosk copy is true). With a fallback
        # look the mode's config says what to show instead; without one, the copy names what is actually sent.
        if tech.fallback_look is not None:
            reason = problem
        else:
            sent = {max(c.cct_range[0], min(c.cct_range[1], spec.cct)) for c in caps.values() if c.cct_range}
            if len(sent) == 1:
                feature = re.sub(rf"\b{spec.cct}(\s?)K\b", rf"{sent.pop()}\1K", feature)
            else:
                feature = _generic_feature(spec, len(devices))
    if reason is not None:
        if tech.fallback_look is None:
            raise PlanError(f"{mode.label} can't run: {reason}, and it has no fallback look")
        spec, preset = tech.fallback_look, None
        feature = tech.fallback_feature or _generic_feature(spec, len(devices))
        notes.add(f"Using the fallback look because {reason}")
        for d in devices:
            if problem := _look_problem(spec, caps[d.id]):
                raise PlanError(
                    f"{mode.label} can't run: {reason}, and the fallback look fails too ({problem})"
                )
    modules = {}
    for d in devices:
        look = _to_look(spec, caps[d.id], notes, d.id, preset)
        modules[d.id] = _target(
            d, caps[d.id], _vector(look, caps[d.id]), indices=ctx.frame_indices(d, caps[d.id])
        )
    return ModePlan(mode_id, mode.label, Tier.STRAND, feature, False, modules, notes.lines(len(devices)))


# ---- canvas (T1) -------------------------------------------------------------------------------------------------
def _canvas(mode_id: str, mode: Mode, ctx: CompileContext, devices: list[DeviceEntry]) -> ModePlan:
    tech = mode.techniques.canvas
    slot = mode.slot
    assert tech is not None and slot is not None and ctx.canopy is not None and ctx.recipes is not None
    notes = _Notes()
    caps = {d.id: ctx.caps(d.id) for d in devices}
    for d in devices:
        c = caps[d.id]
        if not c.individual:
            raise PlanError(f"{d.id} has no per-head (individual) mode, so a painted canvas can't run")
        if slot not in c.slots:
            raise PlanError(f"{d.id} has no slot {slot}")
        if ctx.flags is not None and ctx.flags.get(d.id, DeviceFlags()).t1_frames is not True:
            raise PlanError(
                f"frames written by the Conductor (T1) aren't proven on {d.id} yet (bench Gate C)"
            )
    indices = {d.id: ctx.frame_indices(d, caps[d.id]) for d in devices}
    for d in devices:
        if ctx.addresses is not None and d.framebuffer_mapping is not None and indices[d.id] is None:
            check = framemap.head_check(
                d, ctx.addresses.get(d.id), caps[d.id], len(panel_lattice(ctx.canopy))
            )
            raise PlanError(f"{d.id}: {check.problem}")  # a saved map that isn't valid always has a problem
    placements = [
        placement_for(ctx.canopy, d, frame_length=caps[d.id].framebuffer_length, indices=indices[d.id])
        for d in devices
    ]
    frames = compose_canvas(tech.canvas, canopy=ctx.canopy, recipes=ctx.recipes, placements=placements)

    # Motion only when the configured code is one the bench has enabled; otherwise static, and the copy says so.
    # The copy follows what the slots HOLD (07 §6): the effect fields recorded from readback at commissioning, else
    # the slot header's readback. The canonical hash leaves them out, so they are checked here and on readback.
    effects: dict[str, int] = dict(STATIC_EFFECTS)
    motion, feature = False, tech.feature
    me = tech.motion_effect
    enabled = (ctx.effects_enabled or {}).get("motionEffect") or {}
    held = {d.id: ctx.held_effects(d, slot, "api") for d in devices}
    if any(R.animates(h) for h in held.values()):
        motion = True  # a slot still animates: re-trigger all nine as one burst until it's rewritten
    if me is not None:
        if me.motionEffect and me.motionEffect in enabled:
            effects = {"motionEffect": me.motionEffect, "motionSpeed": me.motionSpeed, "brightnessEffect": 0}
            motion = True
            missing = [
                d.id
                for d in devices
                if held[d.id] is None or int(held[d.id].get("motionEffect") or 0) != me.motionEffect
            ]
            if not missing:
                feature = tech.motion_feature or tech.feature
                notes.add(f"On-device motion: motionEffect {me.motionEffect} ({enabled[me.motionEffect]})")
            else:
                notes.add(
                    f"motionEffect {me.motionEffect} is bench-enabled but not saved in {slot} on "
                    f"{', '.join(missing)} yet (commissioning or the slot rewrite puts it there), so the copy "
                    "doesn't claim motion"
                )
        elif me.motionEffect:
            notes.add(
                f"Motion is off: motionEffect {me.motionEffect} isn't bench-enabled, so the pattern is static"
            )
        else:
            notes.add("Motion is off: no motion code has been benched yet, so the pattern is static")

    brightness = tech.brightness
    modules = {}
    for d in devices:
        c = caps[d.id]
        entries = _clamp_frame(frames[d.id], c, notes, d.id)
        header = ctx.readback(d.id, slot)
        if header is None:
            notes.add(f"No readback of {slot} yet: the default slot header is used", d.id)
        if c.no_white_hint:
            notes.add("This device doesn't drive per-puck white (no-white hint): blends show RGB only", d.id)
        try:
            value = R.frame_value(
                header, entries, expected_length=c.framebuffer_length, effects=effects, cct_range=c.cct_range
            )
        except R.PayloadError as e:
            raise PlanError(f"{d.id}: {e}") from None
        lo, hi = c.brightness_range or (1, 100)
        rows = _vector(R.Look("slot", brightness=max(lo, min(hi, brightness)), slot=slot), c)
        slot_row = R.slot_write(slot, value)
        modules[d.id] = _target(
            d,
            c,
            rows,
            expect=[*rows, slot_row],
            slot=slot,
            slot_value=value,
            slot_hash=R.slot_hash(value),
            preview_rows=[*rows, slot_row],
            indices=indices[d.id],
        )
    return ModePlan(mode_id, mode.label, Tier.T1, feature, motion, modules, notes.lines(len(devices)))


def _clamp_frame(entries: list[R.FrameEntry], caps: Capabilities, notes: _Notes, device_id: str):
    if not caps.cct_range:
        return entries
    lo, hi = caps.cct_range
    out = []
    for e in entries:
        if e.cct and not lo <= e.cct <= hi:
            clamped = max(lo, min(hi, e.cct))
            notes.add(f"Per-head white {e.cct} K clamped into the device range {lo}–{hi} K", device_id)
            e = R.FrameEntry(e.r, e.g, e.b, clamped, e.colorBrightness, e.whiteBrightness)
        out.append(e)
    return out


# ---- slot (T2) ---------------------------------------------------------------------------------------------------
def _slot(mode_id: str, mode: Mode, ctx: CompileContext, devices: list[DeviceEntry]) -> ModePlan:
    tech = mode.techniques.slot
    slot = mode.slot
    assert tech is not None and slot is not None
    caps = {d.id: ctx.caps(d.id) for d in devices}
    unverified = []
    for d in devices:
        c = caps[d.id]
        if c.slots and slot not in c.slots:
            raise PlanError(f"{d.id} has no slot {slot}")
        if ctx.flags is not None and ctx.flags.get(d.id, DeviceFlags()).t2_select is not True:
            raise PlanError(f"selecting slots by API (T2) isn't proven on {d.id} yet (bench Gate B)")
        if not ctx.recorded(d, slot, "app"):
            unverified.append(d.id)
    if unverified:
        # Only a hash recorded as painted in the Hubspace app counts (F4): a hash commissioning recorded is the
        # Conductor's own canvas, and calling it "painted in the Hubspace app" would be false.
        commissioned = [d.id for d in devices if d.id in unverified and ctx.recorded(d, slot, "api")]
        why = (
            f"; {', '.join(commissioned)} hold{'s' if len(commissioned) == 1 else ''} the Conductor's commissioned "
            "pattern, not one painted in the app"
            if commissioned
            else ""
        )
        raise PlanError(
            f"hand-painted slot not verified yet: {slot} on {', '.join(unverified)} has no hash recorded as painted "
            f"in the Hubspace app{why} (paint it in the app, then record it on the module page's Slots tab)"
        )
    notes = _Notes()
    # Motion (07 §6, F9): an app-painted pattern can carry an animation. When the mode is a motion mode and any
    # slot animates — or its header is unknown — re-assert all nine as one burst, never one module on its own.
    animated = [R.animates(ctx.held_effects(d, slot, "app")) for d in devices]
    motion = bool(mode.motion) and any(a is None or a for a in animated)
    if motion:
        notes.add("Re-triggered as one burst: the hand-painted slot may animate on the device")
    design = _design_frames(mode, ctx, devices, caps)
    modules = {}
    for d in devices:
        c = caps[d.id]
        lo, hi = c.brightness_range or (1, 100)
        rows = _vector(
            R.Look("slot", brightness=max(lo, min(hi, tech.brightness)), slot=slot, speed=tech.speed), c
        )
        recorded = ctx.recorded(d, slot, "app")
        value = ctx.readback(d.id, slot)
        preview_rows = None
        if value is not None and R.slot_hash(value) == recorded:
            preview_rows = [*rows, R.slot_write(slot, value)]
        elif d.id in design:
            # What the hand-painted pattern is meant to look like (the canvas design); its content isn't read.
            notes.add(
                f"Preview shows the design: the readback of {slot} doesn't match its recorded hash", d.id
            )
            value = {R.FRAME_CLASS: {"frameBuffer": {"framebuffer": [e.to_json() for e in design[d.id]]}}}
            preview_rows = [*rows, R.slot_write(slot, value)]
        indices = ctx.frame_indices(d, c)
        modules[d.id] = _target(
            d, c, rows, slot=slot, slot_hash=recorded, preview_rows=preview_rows, indices=indices
        )
        if preview_rows is None:
            modules[d.id].preview, modules[d.id].colors = [], []
    return ModePlan(mode_id, mode.label, Tier.T2, tech.feature, motion, modules, notes.lines(len(devices)))


def _design_frames(
    mode: Mode, ctx: CompileContext, devices: list[DeviceEntry], caps: Mapping[str, Capabilities]
) -> dict[str, list[R.FrameEntry]]:
    """The mode's canvas design per module (for previews only), or {} when it has none or can't be composed."""
    tech = mode.techniques.canvas
    if tech is None or ctx.canopy is None or ctx.recipes is None:
        return {}
    try:
        placements = [
            placement_for(
                ctx.canopy,
                d,
                frame_length=caps[d.id].framebuffer_length,
                indices=ctx.frame_indices(d, caps[d.id]),
            )
            for d in devices
        ]
        return compose_canvas(tech.canvas, canopy=ctx.canopy, recipes=ctx.recipes, placements=placements)
    except PlanError:
        return {}


# ---- modules (T3) ------------------------------------------------------------------------------------------------
def _half_up(x: float) -> int:
    return math.floor(x + 0.5)


def _sampled_rgb(sample: ModuleSample, entry: DeviceEntry, ctx: CompileContext) -> tuple[int, int, int]:
    """The sample's one RGB colour for this module: the canvas at the module's centre, or its heads' mean."""
    assert ctx.canopy is not None and ctx.recipes is not None
    if sample.at == "centre":
        points = list(F.module_centres(ctx.canopy, [str(entry.module)]).values())
    else:
        points = head_positions(ctx.canopy, placement_for(ctx.canopy, entry, frame_length=None))
    colours = [evaluate(sample.canvas, x, y, canopy=ctx.canopy, recipes=ctx.recipes) for x, y in points]
    # rgb_only (the schema insists): a colour level dims the RGB itself, since a strand look has one brightness
    channels = [
        [c.r * c.colorBrightness / 100, c.g * c.colorBrightness / 100, c.b * c.colorBrightness / 100]
        for c in colours
    ]
    r, g, b = (_half_up(sum(ch[i] for ch in channels) / len(channels)) for i in range(3))
    return r, g, b


def module_look(mode: Mode, entry: DeviceEntry, ctx: CompileContext) -> LookSpec:
    """The T3 strand look for this module at its canopy position."""
    tech = mode.techniques.modules
    assert tech is not None and ctx.canopy is not None
    row, col = parse_position(entry.module, ctx.canopy)
    n_rows = len(ctx.canopy["orientations"][ctx.canopy["orientation_scheme"]])
    if tech.sample is not None:
        rgb = _sampled_rgb(tech.sample, entry, ctx)
        return LookSpec(kind="rgb", rgb=rgb, brightness=tech.sample.brightness)
    if tech.rows is not None:
        return tech.rows[min(row, len(tech.rows) - 1)]
    if tech.columns is not None:
        return tech.columns[min(col, len(tech.columns) - 1)]
    assert tech.middle_row is not None and tech.others is not None
    return tech.middle_row if row == n_rows // 2 else tech.others


def _modules(mode_id: str, mode: Mode, ctx: CompileContext, devices: list[DeviceEntry]) -> ModePlan:
    tech = mode.techniques.modules
    assert tech is not None and ctx.canopy is not None
    grid = ctx.canopy["orientations"][ctx.canopy["orientation_scheme"]]
    notes = _Notes()
    modules = {}
    sampled = set()
    for d in devices:
        c = ctx.caps(d.id)
        spec = module_look(mode, d, ctx)
        if problem := _look_problem(spec, c):
            raise PlanError(f"{mode.label} (module level) can't run on {d.id}: {problem}")
        sampled.add(spec.rgb)
        look = _to_look(spec, c, notes, d.id)
        modules[d.id] = _target(d, c, _vector(look, c), indices=ctx.frame_indices(d, c))
    sample = tech.sample
    feature = tech.feature
    if sample is not None:
        repeats = isinstance(sample.canvas, FieldCanvas) and sample.canvas.field.cycles > 1
        # a partial canopy's copy is D62's, which is true of any colours (compile_mode)
        one_colour = len(devices) == sum(len(row) for row in grid) and len(sampled) < 2
        if (repeats and sample.at == "centre") or one_colour:
            notes.add(FINER_THAN_GRID)
        if one_colour:
            # the copy's layout ("column by column", "from the centre out") isn't what the strands show
            feature = _one_colour_feature(len(devices))
    return ModePlan(mode_id, mode.label, Tier.T3, feature, False, modules, notes.lines(len(devices)))


def _one_colour_feature(n: int) -> str:
    """The module-level (T3) sentence when a sampled design gives every strand of the full canopy the same colour."""
    return f"Colour and dimming: all {_count_words(n)} Hubspace strands set to one colour from the scene."


class EngineCompiler:
    """The looks compiler in the shape the engine and `make soak` call: compile(mode, tier), ladder(mode), modes().

    Capabilities default to the simulator's SQHDK03 profile for every canopy module (the soak's world)."""

    def __init__(self, registry: Any, capabilities: Mapping[str, Capabilities] | None = None):
        if capabilities is None:
            from conductor.capabilities import detect
            from conductor.sim.profiles import profile_parts

            functions, states = profile_parts()
            caps = detect(functions, states)
            capabilities = {e.id: caps for e in registry.canopy_modules()}
        self._ctx = CompileContext(registry=registry, capabilities=capabilities)

    def compile(self, mode_id: str, tier: Tier) -> ModePlan:
        return compile_mode(mode_id, tier, self._ctx)

    def ladder(self, mode_id: str) -> list[Tier]:
        return ladder(mode_id, self._ctx.modes)

    def modes(self) -> list[str]:
        return list(self._ctx.modes.modes)


def engine_compiler(
    *, registry: Any, capabilities: Mapping[str, Capabilities] | None = None
) -> EngineCompiler:
    return EngineCompiler(registry, capabilities)
