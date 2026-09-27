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
  every module; without flags (the simulator) every tier is allowed (07 §6 "sim runs T1"). T2 needs a recorded hash
  of the hand-painted slot on every module.
- `ModuleTarget.preview` is `render.head_colors` of the intended result, per head in wire order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from conductor import render
from conductor import rows as R
from conductor.capabilities import Capabilities
from conductor.config import CONFIG_DIR, DeviceEntry, DeviceFlags, RecipesFile, load_recipes
from conductor.drivers.base import StateRow
from conductor.engine_api import ModePlan, ModuleTarget, PlanError, Tier
from conductor.geometry import load_canopy_config
from conductor.looks.composer import compose_canvas, parse_position, placement_for
from conductor.looks.schema import LookSpec, Mode, ModesFile, load_modes

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
      registry entry's `slot_hashes`.
    - `flags`: device id → bench `DeviceFlags` (already resolved for the kit at that address). None = simulator:
      every tier allowed.
    - `modes`, `recipes`, `canopy`: loaded from `config_dir` when not given.
    """

    registry: Any
    capabilities: Mapping[str, Capabilities]
    slot_readback: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    effects_enabled: Mapping[str, Mapping[int, str]] | None = None
    recorded_hashes: Mapping[str, Mapping[str, str]] | None = None
    flags: Mapping[str, DeviceFlags] | None = None
    modes: ModesFile | None = None
    recipes: RecipesFile | None = None
    canopy: dict | None = None
    config_dir: Path = CONFIG_DIR

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

    def recorded(self, entry: DeviceEntry, slot: str) -> str | None:
        if self.recorded_hashes is not None:
            return (self.recorded_hashes.get(entry.id) or {}).get(slot)
        return entry.slot_hashes.get(slot)

    def readback(self, device_id: str, slot: str) -> Any:
        return (self.slot_readback.get(device_id) or {}).get(slot)


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
    if tier is Tier.STRAND:
        return _strand(mode_id, mode, ctx, devices)
    if tier is Tier.T1:
        return _canvas(mode_id, mode, ctx, devices)
    if tier is Tier.T2:
        return _slot(mode_id, mode, ctx, devices)
    return _modules(mode_id, mode, ctx, devices)


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


def _generic_feature(spec: LookSpec, count: int) -> str:
    """A true sentence for a fallback look the mode's config gave no copy for."""
    n = _count_words(count)
    if spec.kind == "white":
        return f"Tunable white and dimming: all {n} Hubspace strands set to one white."
    if spec.kind == "rgb":
        return f"Colour and dimming: all {n} Hubspace strands set to one colour."
    if spec.kind == "program":
        return f"A built-in Hubspace lighting program on all {n} strands."
    if spec.kind == "preset":
        return "A built-in Hubspace preset, running on each strand's own controller."
    return f"All {n} Hubspace strands switched off."


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
) -> ModuleTarget:
    rows = R.dedupe(rows)
    preview = render.head_colors(
        preview_rows if preview_rows is not None else rows, _heads(entry, caps), caps
    )
    return ModuleTarget(
        device_id=entry.id,
        rows=rows,
        expect=list(expect if expect is not None else rows),
        slot=slot,
        slot_value=slot_value,
        slot_hash=slot_hash,
        preview=preview,
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
        modules[d.id] = _target(d, caps[d.id], _vector(look, caps[d.id]))
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
    placements = [placement_for(ctx.canopy, d, frame_length=caps[d.id].framebuffer_length) for d in devices]
    frames = compose_canvas(tech.canvas, canopy=ctx.canopy, recipes=ctx.recipes, placements=placements)

    # Motion only when the configured code is one the bench has enabled; otherwise static, and the copy says so.
    effects: dict[str, int] = dict(STATIC_EFFECTS)
    motion, feature = False, tech.feature
    me = tech.motion_effect
    enabled = (ctx.effects_enabled or {}).get("motionEffect") or {}
    if me is not None:
        if me.motionEffect and me.motionEffect in enabled:
            effects = {"motionEffect": me.motionEffect, "motionSpeed": me.motionSpeed, "brightnessEffect": 0}
            motion, feature = True, tech.motion_feature or tech.feature
            notes.add(f"On-device motion: motionEffect {me.motionEffect} ({enabled[me.motionEffect]})")
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
        if not ctx.recorded(d, slot):
            unverified.append(d.id)
    if unverified:
        raise PlanError(
            f"hand-painted slot not verified yet: {slot} on {', '.join(unverified)} has no recorded hash "
            "(paint it in the Hubspace app, then record its hash on /ops)"
        )
    notes = _Notes()
    design = _design_frames(mode, ctx, devices, caps)
    modules = {}
    for d in devices:
        c = caps[d.id]
        lo, hi = c.brightness_range or (1, 100)
        rows = _vector(
            R.Look("slot", brightness=max(lo, min(hi, tech.brightness)), slot=slot, speed=tech.speed), c
        )
        recorded = ctx.recorded(d, slot)
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
        modules[d.id] = _target(d, c, rows, slot=slot, slot_hash=recorded, preview_rows=preview_rows)
        if preview_rows is None:
            modules[d.id].preview = []
    return ModePlan(mode_id, mode.label, Tier.T2, tech.feature, False, modules, notes.lines(len(devices)))


def _design_frames(
    mode: Mode, ctx: CompileContext, devices: list[DeviceEntry], caps: Mapping[str, Capabilities]
) -> dict[str, list[R.FrameEntry]]:
    """The mode's canvas design per module (for previews only), or {} when it has none or can't be composed."""
    tech = mode.techniques.canvas
    if tech is None or ctx.canopy is None or ctx.recipes is None:
        return {}
    try:
        placements = [
            placement_for(ctx.canopy, d, frame_length=caps[d.id].framebuffer_length) for d in devices
        ]
        return compose_canvas(tech.canvas, canopy=ctx.canopy, recipes=ctx.recipes, placements=placements)
    except PlanError:
        return {}


# ---- modules (T3) ------------------------------------------------------------------------------------------------
def module_look(mode: Mode, row: int, col: int, n_rows: int) -> LookSpec:
    """The T3 strand look for the module at (row, col)."""
    tech = mode.techniques.modules
    assert tech is not None
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
    for d in devices:
        c = ctx.caps(d.id)
        row, col = parse_position(d.module, ctx.canopy)
        spec = module_look(mode, row, col, len(grid))
        if problem := _look_problem(spec, c):
            raise PlanError(f"{mode.label} (module level) can't run on {d.id}: {problem}")
        look = _to_look(spec, c, notes, d.id)
        modules[d.id] = _target(d, c, _vector(look, c))
    return ModePlan(mode_id, mode.label, Tier.T3, tech.feature, False, modules, notes.lines(len(devices)))


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
