"""`make soak` / `python -m conductor.soak`: 8 simulated hours of the canopy engine on the SimDriver (07 §7).

Runs the real CanopyEngine, Reconciler, CommandExecutor and DeviceStateTracker on a `VirtualTimeLoop` against
`SimSettings.from_acceptance()` — the pinned conditions in `config/sim/acceptance.yaml` (PUT ack 150–800 ms lognormal,
device apply +0.3–1.5 s, 5% of requests → 429, 1% silent drops). Nothing here tunes them.

Schedule (fractions of the run, so a shorter run keeps the same shape):
- commissioning (T1 slot contents), then the engine starts on the idle mode;
- `changes` kiosk mode changes at seeded random times, at least one cooldown apart, clear of the fault windows;
- a button-mash burst: 40 kiosk taps 0.25 s apart, then 20 ops taps 0.2 s apart (ops bypass the cooldown);
- `drift_events` changes made outside the Conductor (the Hubspace app): one module's look changed straight in the
  simulated cloud (`SimDriver.faults.change_out_of_band` — no executor, no rate budget), at seeded times while the
  canopy is settled, clear of mode changes and the fault windows;
- one window of HTTP 500s (the pinned 43 min) and one internet outage (the pinned 30 min), each with an ops mode
  change in the middle, so recovery has to re-apply a look the lights aren't showing.

Measured (ground truth = `SimDriver.device_view`, what the lights actually show):
- time from each accepted change (kiosk, ops, idle) to all nine modules correct — p50/p95/max, target p95 ≤ 5 s;
- the executor's peak queue depth (never more than 2 per device: pending + in flight);
- self-heal time after every silent drop that mattered (the look the module should show was dropped) — ≤ 10 s;
- detect + heal time after every out-of-band change (from the change until the lights show the look again; the
  rolling true-up has to notice it — no command of ours failed) — ≤ 10 s, and the true-up's read load (GET/s);
- tier downgrades (0 at 5% throttling);
- time from each fault ending to all nine modules showing the current look — ≤ 60 s;
- the smallest gap between two writes to one device (≥ the 1 s budget) and the final look.

**The ripple scenario (D64).** The show runs with `kiosk.ripple_stagger_s` 0 (one burst) until Alon flips it, and the
pinned targets above assume one burst. So the soak also runs a separate, shorter show (`ripple:` in
`config/sim/soak.yaml`, same pinned conditions) with the stagger set (1.2 s): seeded kiosk mood changes, each landing
ring by ring. Measured per change from the lights: each ring's landing spread (first to last module of that ring
correct — 05's "spread ≤ 2 s at p95", per ring: **reported, not gating**, since it is the target D64 asks Alon to
redefine and the show runs one burst until then), the time until the last ring is correct (≤ (rings − 1) × stagger
+ 5 s), the PUTs each module got for the change (exactly one, none before its ring is due) and tier downgrades (0 at
5% throttling); those three are part of the verdict.

**The stepped scenario (D65, D75).** Coastal Tides and Golden Hour have a stepped rung in `config/modes.yaml` (the
simulator steps them; `motion.stepped.live_enabled` stays false, so live hardware doesn't). Since D75 a guest's
(kiosk) choice runs a mode's rung first and then settles onto its best still tier (Golden Hour's commissioned canvas),
so the main run steps both whenever a guest chooses them. A third, separate run (`stepped:` in
`config/sim/soak.yaml`, same pinned conditions) steps every scene with all three kinds of motion: a mode's own rung
stays where modes.yaml puts it (design, cadence and copy as the show has them), and a mode without one gets a
SIMULATED rung just before `modules`, made from its module-level design (so the still look it holds is that rung),
with the scenario's `technique` and its per-mode `simulated:` overrides (a breathe, a shimmer) and copy that says what
changes. It makes seeded kiosk runs of them, some cut short by another mood (never the idle look, so the idle return
stays measurable), then leaves the canopy alone until the idle return. Each run's end is counted by the tier it
settled on (`settled`; a settle goes out with the source `settle`, so it is never grouped with a run's steps), and the
idle return must not start a run. Correctness is program-aware (`_wanted`): while a run shows a step, a module is
right when its lights show that step. Measured per step from the lights (every program command, grouped by step):
each step's landing spread (first to last of its modules showing it — reported, not gating, like the ripple's) and
send → last module; steps superseded (a module sent the next step before it showed this one), failed, skipped by a
late schedule or cut by a newer mood; the program commands per module per step (exactly one, and none missing for a
module whose rows changed); the most program PUTs on one account in any 60 s and the most writes of any kind there
(both ≤ `account_writes_per_min`: the runner paces its steps at the cap less `budget_reserve`, the room retries,
re-sends and a mood chosen mid-run need, and holds back a step that would pass the cap); governor trips and
tier downgrades at 5% throttling (0); the still look after each run; and the idle return after the last choice
(`idle_return_s` to `idle_return_s` + slack — a run never moves the idle clock). Its targets are proposals in
soak.yaml: `config/sim/acceptance.yaml` is pinned and gains them only with Alon's sign-off.

Writes `docs/reports/sim-acceptance.md` and exits non-zero when any target fails.

The world (07 §7 soak validity, review F11): by default the soak runs **the show's own wiring** — `build_services`
on the virtual clock with a temporary `CONDUCTOR_DATA_DIR` and `conductor.show.ShowWiring`, exactly as the app's
lifespan builds it: capabilities read from each module's (simulated) profile in the background, the looks compiler,
the commissioning gate on T1, slot records (hash, origin, effect fields) in the registry overlay, the same ladder and
tier rules. Only the simulator's conditions (pinned) and one soak-only setting differ: `config/sim/soak.yaml` enables a
SIMULATED on-device motion code for one painted mode (Golden Hour; no show scene has an on-device effect), so the
all-or-none motion re-trigger (07 §6) is measured too. On real hardware code 1 has only bounded bench runs on Kingston
(one physically observed, Oct 2) and `effects.enabled` stays empty; the report says so.

`run_soak(compiler=...)` still takes any object with `compile(mode_id, tier)`, `ladder(mode_id)` and `modes()` and
then runs the bare engine on it (the older world: every tier allowed, no overlay); `BuiltinCompiler` below is kept for
that path.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import itertools
import logging
import random
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from conductor import rows as R
from conductor.clock import RealClock, VirtualClock, run_virtual
from conductor.config import CONFIG_DIR, Registry, load_registry, load_settings
from conductor.drivers.base import StateRow
from conductor.engine_api import ModePlan, ModuleTarget, PlanError, Tier
from conductor.executor import CommandExecutor
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager
from conductor.services import Target
from conductor.sim.settings import ACCEPTANCE_FILE, SimSettings
from conductor.state import DeviceStateTracker
from conductor.telemetry import Telemetry, percentile

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "docs" / "reports" / "sim-acceptance.md"
SOAK_SETTINGS = CONFIG_DIR / "sim" / "soak.yaml"
SHOW_DESCRIPTION = (
    "the show's own wiring (build_services + conductor.show.ShowWiring → conductor.looks): profiles read in the "
    "background, the commissioning gate on T1, slot records in the overlay"
)
POLL_S = 0.1  # how often a measurement looks at the lights
WATCH_LIMIT_S = 120.0  # a measurement gives up (and fails) after this long

# The tiers the built-in compiler can compile ("stepped" isn't one: it has no program to step, BuiltinCompiler).
_TIER_NAMES = {"canvas": Tier.T1, "slot": Tier.T2, "modules": Tier.T3, "strand": Tier.STRAND}


# ---- the built-in compiler (used when conductor.looks isn't available) ------------------------------------------
class BuiltinCompiler:
    """Compiles `config/modes.yaml` without the looks composer: strand looks as written, module-level looks per
    canopy position, and canvas (T1) as one colour per module with a brightness ramp along the wire. T2 is left
    out (nothing is hand-painted in the simulator), and so is a stepped rung (D65): this compiler has no program to step,
    so a stepped mode shows its still module-level rung (the look a stepped run holds)."""

    description = "built-in soak compiler (strand + module-level + one-colour-per-module canvas frames)"
    CCT_FLOOR = 2700  # the simulator profile's range is 2700–6500 K

    def __init__(self, registry: Registry, config_dir: Path = CONFIG_DIR, frame_length: int = 30) -> None:
        with open(config_dir / "modes.yaml", encoding="utf-8") as f:
            self._modes: dict[str, dict] = (yaml.safe_load(f) or {})["modes"]
        self._config_dir = config_dir
        self._modules = registry.canopy_modules()
        self._frame_length = frame_length

    def modes(self) -> list[str]:
        return list(self._modes)

    def ladder(self, mode_id: str) -> list[Tier]:
        mode = self._modes[mode_id]
        return [_TIER_NAMES[t] for t in mode["ladder"] if t in _TIER_NAMES and t != "slot"]

    def compile(self, mode_id: str, tier: Tier) -> ModePlan:
        mode = self._modes[mode_id]
        name = {v: k for k, v in _TIER_NAMES.items()}[tier]
        technique = (mode.get("techniques") or {}).get(name)
        if technique is None or tier not in self.ladder(mode_id):
            raise PlanError(f"{mode.get('label', mode_id)} has no {name} technique.")
        notes: list[str] = []
        modules: dict[str, ModuleTarget] = {}
        for index, entry in enumerate(self._modules):
            look = self._module_look(mode, name, technique, entry.module or "r0c0", notes)
            if tier is Tier.T1:
                slot = mode["slot"]
                value = R.frame_value(None, self._frame(look, index), expected_length=self._frame_length)
                rows = R.full_state_vector(
                    R.Look("slot", slot=slot, brightness=technique.get("brightness", 90))
                )
                expect = [*rows, R.slot_write(slot, value)]
                modules[entry.id] = ModuleTarget(
                    entry.id, rows, expect, slot=slot, slot_value=value, slot_hash=R.slot_hash(value)
                )
            else:
                rows = R.full_state_vector(look)
                modules[entry.id] = ModuleTarget(entry.id, rows, list(rows))
        return ModePlan(
            mode_id=mode_id,
            label=mode.get("label", mode_id),
            tier=tier,
            feature=technique.get("feature", ""),
            motion=bool(mode.get("motion")) and tier is Tier.T1,
            modules=modules,
            notes=sorted(set(notes)),
        )

    def _module_look(self, mode: dict, name: str, technique: dict, position: str, notes: list[str]) -> R.Look:
        row, col = int(position[1]), int(position[3])
        if name == "strand":
            raw = dict(technique["look"])
            if raw.get("kind") == "preset" and not raw.get("preset"):
                raw["preset"] = (technique.get("presets") or ["snowfall"])[0]
        elif "sample" in technique:
            return self._sampled(technique["sample"], position, notes)
        elif "rows" in technique:
            raw = dict(technique["rows"][row % len(technique["rows"])])
        elif "columns" in technique:
            raw = dict(technique["columns"][col % len(technique["columns"])])
        elif "middle_row" in technique:
            raw = dict(technique["middle_row"] if row == 1 else technique["others"])
        else:  # canvas: borrow the module-level look for this position as the frame's colour
            modules = (mode.get("techniques") or {}).get("modules") or {}
            return (
                self._module_look(mode, "modules", modules, position, notes)
                if modules
                else R.Look("white", cct=2700)
            )
        if raw.get("kind") == "white" and raw.get("cct", self.CCT_FLOOR) < self.CCT_FLOOR:
            notes.append(f"{raw['cct']} K clamped to the device floor {self.CCT_FLOOR} K")
            raw["cct"] = self.CCT_FLOOR
        return R.Look.from_json(raw)

    def _sampled(self, sample: dict, position: str, notes: list[str]) -> R.Look:
        """A module-level design (D63): the field at the module's centre between its stops' `rgb_only` colours, rounded
        half up. PlanError (so the engine steps down the ladder) when the field module can't be loaded."""
        try:
            from pydantic import TypeAdapter

            from conductor.config import load_recipes
            from conductor.geometry import load_canopy_config
            from conductor.looks import field as F
        except ImportError as e:  # pragma: no cover - this compiler is the fallback for a broken looks
            raise PlanError(f"a sampled module look needs conductor.looks.field ({e})") from None
        canvas = sample["canvas"]
        if canvas.get("kind") != "field":
            raise PlanError("the built-in compiler samples only `field` canvases")
        path = self._config_dir / "canopy.yaml"
        canopy = load_canopy_config(path) if path.exists() else load_canopy_config()
        recipes = load_recipes(self._config_dir)
        spec = TypeAdapter(F.FieldSpec).validate_python(canvas["field"])
        if sample.get("at", "centre") != "centre":
            notes.append("module-level designs are sampled at each module's centre (built-in compiler)")
        ((x, y),) = F.module_centres(canopy, [position]).values()
        t = F.value(spec, x, y, canopy)
        stops = [(float(s["at"]), recipes.colors[s["color"]].rgb_only) for s in canvas["stops"]]
        (a_at, a), (b_at, b) = stops[0], stops[-1]
        for (lo_at, lo), (hi_at, hi) in itertools.pairwise(stops):
            if lo_at <= t <= hi_at:
                (a_at, a), (b_at, b) = (lo_at, lo), (hi_at, hi)
                break
        f = 0.0 if b_at == a_at else min(1.0, max(0.0, (t - a_at) / (b_at - a_at)))
        rgb = tuple(int(getattr(a, c) * (1 - f) + getattr(b, c) * f + 0.5) for c in "rgb")
        return R.Look("rgb", brightness=sample.get("brightness", 100), rgb=rgb)

    def _frame(self, look: R.Look, module_index: int) -> list[R.FrameEntry]:
        out = []
        for i in range(self._frame_length):
            level = 60 + (40 * i) // max(1, self._frame_length - 1)
            if look.kind == "rgb" and look.rgb:
                r, g, b = look.rgb
                out.append(R.FrameEntry(r, g, b, 0, level, 0))
            else:
                out.append(R.FrameEntry(0, 0, 0, look.cct or 2700, 0, level))
        return out


def default_compiler(registry: Registry) -> tuple[Any, str]:
    """The looks compiler when `conductor.looks` offers one the engine can call, else the built-in compiler."""
    try:
        from conductor import looks  # type: ignore[attr-defined]
    except ImportError:
        return BuiltinCompiler(
            registry
        ), BuiltinCompiler.description + " — conductor.looks isn't in this build"
    factory = getattr(looks, "engine_compiler", None)
    if callable(factory):
        try:
            return factory(registry=registry), "conductor.looks.engine_compiler"
        except Exception as e:  # pragma: no cover - depends on the looks package
            log.exception("conductor.looks.engine_compiler failed; using the built-in compiler")
            return BuiltinCompiler(registry), f"{BuiltinCompiler.description} — engine_compiler failed: {e}"
    compile_mode = getattr(looks, "compile_mode", None)
    context = next(
        (
            getattr(looks, n)
            for n in ("build_context", "default_context", "make_context")
            if hasattr(looks, n)
        ),
        None,
    )
    ladder = next(
        (getattr(looks, n) for n in ("ladder", "mode_ladder", "ladder_for") if hasattr(looks, n)), None
    )
    if callable(compile_mode) and callable(context) and callable(ladder):
        try:
            ctx = context(registry=registry)
            fallback = BuiltinCompiler(registry)

            class _Looks:
                def compile(self, mode_id: str, tier: Tier) -> ModePlan:
                    return compile_mode(mode_id, tier, ctx)

                def ladder(self, mode_id: str) -> list[Tier]:
                    return list(ladder(mode_id))

                def modes(self) -> list[str]:
                    return fallback.modes()

            return _Looks(), "conductor.looks.compile_mode"
        except Exception as e:  # pragma: no cover - depends on the looks package
            log.exception("conductor.looks.compile_mode wiring failed; using the built-in compiler")
            return BuiltinCompiler(registry), f"{BuiltinCompiler.description} — conductor.looks failed: {e}"
    return BuiltinCompiler(registry), (
        f"{BuiltinCompiler.description} — conductor.looks has no engine_compiler(registry=...) or "
        "compile_mode + context + ladder the soak can call"
    )


# ---- the soak world ------------------------------------------------------------------------------------------------
class _Hub:
    client_count = 0

    def publish(self, topic: str, data: dict) -> None:  # nobody is watching in the soak
        return None


SCHEDULE_GAP_S = 8.0  # the seeded kiosk changes keep at least this apart (the fixed cooldown before D70)


@dataclass
class SoakConfig:
    hours: float = 8.0
    changes: int = 100
    seed: int = 0
    mash_kiosk_taps: int = 40
    mash_ops_taps: int = 20
    drift_events: int = 12  # out-of-band changes (the Hubspace app) the reconciler must find and undo
    # the ripple scenario (config/sim/soak.yaml `ripple:`; changes 0 = skip it)
    ripple: dict = field(default_factory=lambda: load_soak_ripple())
    # the stepped scenario (config/sim/soak.yaml `stepped:`; runs 0 = skip it)
    stepped: dict = field(default_factory=lambda: load_soak_stepped())
    sim_settings: SimSettings = field(default_factory=SimSettings.from_acceptance)
    targets: dict = field(default_factory=dict)  # from acceptance.yaml
    report_path: Path | None = REPORT_PATH


@dataclass
class _World:
    clock: VirtualClock
    registry: Registry
    sim: Any
    executor: CommandExecutor
    tracker: DeviceStateTracker
    telemetry: Telemetry
    engine: Any
    modules: list[str]
    addresses: dict[str, str]
    wiring: Any = None  # ShowWiring (the show's own wiring), when the soak runs it
    services: Any = None
    cleanup: Any = None  # a callable removing the temporary data/config directories
    motion: dict | None = None  # the SIMULATED motion setting in force


def _soak_config_dir(
    motion: dict | None, ripple_stagger_s: float | None = None, stepped: dict | None = None
) -> Path:
    """A copy of config/ with the soak-only SIMULATED motion code applied (modes.yaml + conductor.yaml), with
    `ripple_stagger_s` as kiosk.ripple_stagger_s when given (the ripple scenario), and with `stepped` modes given a
    SIMULATED stepped rung first in their ladder (the stepped scenario, `_apply_stepped`)."""
    root = Path(tempfile.mkdtemp(prefix="canopy-soak-config-"))
    config = root / "config"
    shutil.copytree(CONFIG_DIR, config)
    if stepped:
        _apply_stepped(config, stepped)
    if ripple_stagger_s is not None:
        settings_path = config / "conductor.yaml"
        settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        settings.setdefault("kiosk", {})["ripple_stagger_s"] = float(ripple_stagger_s)
        settings_path.write_text(
            yaml.safe_dump(settings, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    if motion:
        modes_path = config / "modes.yaml"
        modes = yaml.safe_load(modes_path.read_text(encoding="utf-8"))
        canvas = modes["modes"][motion["mode"]]["techniques"]["canvas"]
        canvas["motion_effect"] = {
            **(canvas.get("motion_effect") or {}),
            "motionEffect": int(motion["motionEffect"]),
        }
        # No show scene has an on-device effect (D59), so the show's modes carry no motion copy: the soak adds its own.
        canvas.setdefault(
            "motion_feature", str(motion.get("feature") or "SIMULATED on-device motion (soak only)")
        )
        modes_path.write_text(yaml.safe_dump(modes, sort_keys=False, allow_unicode=True), encoding="utf-8")
        settings_path = config / "conductor.yaml"
        settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        enabled = settings.setdefault("effects", {}).setdefault("enabled", {})
        enabled.setdefault("motionEffect", {})[int(motion["motionEffect"])] = str(
            motion.get("note", "SIMULATED")
        )
        enabled.setdefault("brightnessEffect", {})
        settings_path.write_text(
            yaml.safe_dump(settings, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    return config


RIPPLE_DEFAULTS = {
    "stagger_s": 1.2,
    "changes": 30,
    "spacing_s": 12.0,
    "ring_spread_p95_s": 2.0,
    "settle_extra_s": 5.0,
}


def load_soak_ripple(path: Path = SOAK_SETTINGS) -> dict:
    """The ripple scenario's settings (`ripple:` in config/sim/soak.yaml) over RIPPLE_DEFAULTS."""
    ripple: dict = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            ripple = (yaml.safe_load(f) or {}).get("ripple") or {}
    return {**RIPPLE_DEFAULTS, **ripple}


STEPPED_DEFAULTS = {
    # every scene; Golden Hour first, so the short soak's first run settles onto its canvas (D75)
    "modes": [
        "golden-hour",
        "coastal-tides",
        "lowcountry-calm",
        "garden-gathering",
        "rainbow-row-sunset",
        "goodnight-glow",
    ],
    "technique": {"direction": "forward", "steps_per_cycle": 6, "step_s": 6.0, "run_s": 90.0},
    # per-mode overrides of `technique` for the SIMULATED rungs (D75): a breathe, a shimmer and a level-only breath;
    # each swing fits its scene's brightness (75, 85, 28)
    "simulated": {
        "garden-gathering": {"mode": "breathe", "brightness_swing": 6, "lag_steps": 2},
        "rainbow-row-sunset": {"mode": "shimmer", "brightness_swing": 6, "lag_steps": 3, "seed": 7},
        "goodnight-glow": {"mode": "breathe", "brightness_swing": 4},
    },
    "runs": 12,
    "hold_s": 20.0,
    "interrupt_every": 4,
    "step_spread_p95_s": 2.0,
    "idle_return_slack_s": 10.0,
}


def load_soak_stepped(path: Path = SOAK_SETTINGS) -> dict:
    """The stepped scenario's settings (`stepped:` in config/sim/soak.yaml) over STEPPED_DEFAULTS (`technique` merged
    key by key, `simulated` mode by mode)."""
    stepped: dict = {}
    if path.exists():
        with open(path, encoding="utf-8") as f:
            stepped = (yaml.safe_load(f) or {}).get("stepped") or {}
    technique = {**STEPPED_DEFAULTS["technique"], **(stepped.get("technique") or {})}
    simulated = {**copy.deepcopy(STEPPED_DEFAULTS["simulated"]), **(stepped.get("simulated") or {})}
    return {**copy.deepcopy(STEPPED_DEFAULTS), **stepped, "technique": technique, "simulated": simulated}


SIMULATED_STEPPED = "SIMULATED stepped look (soak only; never shown to guests): each Hubspace strand is sent "


def _simulated_copy(tech: dict, canvas: Any, canopy: dict) -> str:
    """A SIMULATED rung's moving copy, true of what its steps change (the schema's rules, D65/D75): travel names the
    design's flow (`motion_words`); a breath says what each strand is sent and how it breathes (a lagged breathe its
    start too); a shimmer that the strands breathe on their own."""
    from conductor.looks.schema import motion_words

    kind = tech.get("mode", "travel")
    if kind == "travel":
        words = motion_words(canvas, canopy, tech.get("direction", "forward"))
        if words is None:  # pragma: no cover - a stepped design always has a field canvas over a canopy here
            raise ValueError("its design has no layout words to name the stepped flow")
        return f"{SIMULATED_STEPPED}its next colour every few seconds, so the palette moves {words}."
    shift, swing = float(tech.get("shift") or 0) > 0, int(tech.get("brightness_swing") or 0) > 0
    level = "a brighter or softer level"
    what = f"its next colour and {level}" if shift and swing else ("its next colour" if shift else level)
    if kind == "shimmer":
        verb = "glow and soften" if swing else "drift and settle"
        return f"{SIMULATED_STEPPED}{what} every few seconds, so the strands {verb} on their own."
    verb = "swells and softens" if swing else "drifts and settles"
    lead = ""
    if int(tech.get("lag_steps") or 0) > 0:
        start = motion_words(canvas, canopy, "forward")
        lead = f" {start}" if start else ""
    return f"{SIMULATED_STEPPED}{what} every few seconds, so the palette {verb}{lead}."


def _apply_stepped(config: Path, stepped: dict) -> None:
    """Give each of `stepped["modes"]` a stepped rung, in the config copy. A mode with its own (config/modes.yaml)
    keeps it where the show has it, design, cadence and copy (D75: a guest's choice runs it first wherever it sits).
    A mode without one gets a SIMULATED rung just before `modules`: its own module-level design (the `modules`
    sample's canvas and brightness, so the hold is that rung exactly) with the scenario's `technique` and the mode's
    `simulated` overrides (its kind of motion), and moving copy that says SIMULATED and what changes."""
    from pydantic import TypeAdapter

    from conductor.geometry import load_canopy_config
    from conductor.looks.schema import FieldCanvas

    modes_path = config / "modes.yaml"
    modes = yaml.safe_load(modes_path.read_text(encoding="utf-8"))
    canopy_path = config / "canopy.yaml"
    canopy = load_canopy_config(canopy_path) if canopy_path.exists() else load_canopy_config()
    simulated = stepped.get("simulated") or {}
    for mode_id in stepped["modes"]:
        mode = modes["modes"][mode_id]
        if mode["techniques"].get("stepped") is not None and "stepped" in mode["ladder"]:
            continue  # its own rung, where the show has it
        tech = {**stepped["technique"], **(simulated.get(mode_id) or {})}
        sample = mode["techniques"]["modules"]["sample"]
        canvas = copy.deepcopy(sample["canvas"])
        try:
            moving = _simulated_copy(tech, TypeAdapter(FieldCanvas).validate_python(canvas), canopy)
        except ValueError as exc:  # pragma: no cover - see _simulated_copy
            raise ValueError(f"{mode_id}: {exc}") from None
        mode["techniques"]["stepped"] = {
            "canvas": canvas,
            "brightness": sample.get("brightness", 100),
            **tech,
            "moving_feature": moving,
        }
        ladder = [t for t in mode["ladder"] if t != "stepped"]
        at = ladder.index("modules") if "modules" in ladder else len(ladder)
        mode["ladder"] = [*ladder[:at], "stepped", *ladder[at:]]
    modes_path.write_text(yaml.safe_dump(modes, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load_soak_motion(path: Path = SOAK_SETTINGS) -> dict | None:
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        motion = (yaml.safe_load(f) or {}).get("motion")
    return motion or None


async def _build_show(
    cfg: SoakConfig, *, ripple_stagger_s: float | None = None, stepped: dict | None = None
) -> _World:
    """The app's own wiring on the virtual clock: build_services + ShowWiring, a temp data dir (the overlay)."""
    from conductor.services import build_services
    from conductor.show import ShowWiring

    motion = load_soak_motion()
    config_dir = _soak_config_dir(motion, ripple_stagger_s, stepped)
    data = Path(tempfile.mkdtemp(prefix="canopy-soak-data-"))
    clock = VirtualClock()
    services = build_services(
        {"CONDUCTOR_DATA_DIR": str(data)}, clock=clock, config_dir=config_dir, sim_settings=cfg.sim_settings
    )
    services.telemetry.dir = None  # events stay in memory: the soak measures the engine, not disk appends
    sim = services.sim
    sim.seed = cfg.seed
    for rng, name in ((sim._fault_rng, "faults"), (sim._latency_rng, "latency"), (sim._world_rng, "world")):
        rng.seed(f"{cfg.seed}:{name}")  # the same seeded streams the bare world uses
    await services.start()
    wiring = ShowWiring(services)
    services.extras["show"] = wiring
    engine = await wiring.start()
    modules = [d.id for d in services.registry.canopy_modules()]

    def cleanup() -> None:
        shutil.rmtree(config_dir.parent, ignore_errors=True)
        shutil.rmtree(data, ignore_errors=True)

    return _World(
        clock,
        services.registry,
        sim,
        services.executor_for(sim),
        services.tracker,
        services.telemetry,
        engine,
        modules,
        {d: f"sim-{d}" for d in modules},
        wiring=wiring,
        services=services,
        cleanup=cleanup,
        motion=motion,
    )


def _build(cfg: SoakConfig, compiler: Any) -> _World:
    from conductor.canopy import CanopyEngine, EngineDeps
    from conductor.drivers.sim import SimDriver

    clock = VirtualClock()
    registry = load_registry(CONFIG_DIR, None)
    settings = load_settings(CONFIG_DIR)
    sim = SimDriver(registry, clock=clock, settings=cfg.sim_settings, seed=cfg.seed)
    telemetry = Telemetry(None, clock)
    locks = DeviceLockManager(clock=clock)
    executor = CommandExecutor(sim, locks=locks, telemetry=telemetry, clock=clock, policy=settings.executor)
    tracker = DeviceStateTracker(clock, telemetry)
    tracker.attach(executor)

    def target(device_id: str, purpose: Purpose) -> Target:
        return Target(device_id, registry.address(device_id, "sim"), sim, executor)

    engine = CanopyEngine(
        EngineDeps(
            registry=lambda: registry,
            target=target,
            tracker=tracker,
            telemetry=telemetry,
            hub=_Hub(),
            clock=clock,
            settings=settings,
            locks=locks,
            compile=compiler.compile,
            ladder=compiler.ladder,
            modes=compiler.modes,
            simulated=lambda: True,
        )
    )
    modules = [d.id for d in registry.canopy_modules()]
    return _World(
        clock, registry, sim, executor, tracker, telemetry, engine, modules, {d: f"sim-{d}" for d in modules}
    )


def _wanted(world: _World, plan: ModePlan, device_id: str) -> list[StateRow]:
    """The rows `device_id` should show for `plan` now: while a stepped run of that plan has a step showing, that
    step's (a stepped look's lights are right when they show the step the engine is at, not the hold); else the
    plan's own expected rows."""
    runner = getattr(world.engine, "runner", None)
    if plan.program is not None and runner is not None and runner.plan is plan:
        step = runner.rows_for(device_id)
        if step is not None:
            return step
    mt = plan.modules[device_id]
    return mt.expect or mt.rows


def _correct(world: _World, plan: ModePlan, device_id: str) -> bool:
    view = world.sim.device_view(world.addresses[device_id])
    return not R.compare(_wanted(world, plan, device_id), view)


def _all_correct(world: _World, plan: ModePlan) -> bool:
    return all(_correct(world, plan, d) for d in plan.modules)


async def _soak(cfg: SoakConfig, compiler: Any) -> dict:
    world = await _build_show(cfg) if compiler is None else _build(cfg, compiler)
    engine, clock, sim = world.engine, world.clock, world.sim
    if compiler is None:
        compiler = SimpleNamespace(modes=world.wiring.mode_ids)  # the schedule only needs the mode ids
        # the show commissions its slots once every profile is read (as the app does in sim): wait for it
        while world.wiring.last_commission is None and clock.monotonic() < 600:
            await clock.sleep(1.0)
    rng = random.Random(f"{cfg.seed}:schedule")
    duration = cfg.hours * 3600.0
    window_s = cfg.sim_settings.http_500_window_s or 43 * 60
    outage_s = cfg.sim_settings.internet_outage_s or 30 * 60
    if cfg.hours < 8:  # short runs (tests) scale the fault windows with the run
        window_s = max(120.0, window_s * cfg.hours / 8)
        outage_s = max(120.0, outage_s * cfg.hours / 8)
    t0 = clock.monotonic()
    windows = {
        "http_500": (t0 + 0.30 * duration, t0 + 0.30 * duration + window_s),
        "outage": (t0 + 0.65 * duration, t0 + 0.65 * duration + outage_s),
    }
    mash_at = t0 + 0.15 * duration

    results: dict[str, Any] = {
        "changes": [],  # {source, mode, seconds|None, fault}
        "abandoned": 0,
        "refused": {"kiosk": 0, "during_faults": 0},
        "heals": [],
        "heal_timeouts": 0,
        "drift_heals": [],  # seconds from an out-of-band change until the lights show the look again
        "drift_timeouts": 0,
        "drift_skipped": 0,  # a change that didn't make the module wrong, or a look change that superseded it
        "drift_attempted": 0,
        "recoveries": {},
        "max_queue": 0,
        "min_write_gap_s": None,
        "wiring": {
            "show_wiring": world.wiring is not None,
            "motion": world.motion,
            # accepted changes and run-end settles that installed a motion plan (all-or-none re-trigger)
            "motion_plans": 0,
            "tiers": {},  # accepted changes per tier
            "settles": 0,  # D75: run ends that settled onto the mode's canvas or slot (not guest changes)
        },
    }
    tasks: set[asyncio.Task] = set()

    def spawn(coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def in_fault(t: float, pad_after: float = 0.0) -> str | None:
        for name, (a, b) in windows.items():
            if a <= t < b + pad_after:
                return name
        return None

    # -- instruments ----------------------------------------------------------------------------------------------
    def sample_queue() -> None:
        results["max_queue"] = max(results["max_queue"], world.executor.queue_depth())

    world.executor.add_submit_listener(lambda _cmd: asyncio.get_running_loop().call_soon(sample_queue))

    last_write: dict[str, float] = {}
    original_write = sim.write

    async def timed_write(device_id: str, rows: list[StateRow], *, purpose: Purpose):
        now = clock.monotonic()
        if device_id in last_write:
            gap = now - last_write[device_id]
            if results["min_write_gap_s"] is None or gap < results["min_write_gap_s"]:
                results["min_write_gap_s"] = gap
        last_write[device_id] = now
        return await original_write(device_id, rows, purpose=purpose)

    sim.write = timed_write  # type: ignore[method-assign]

    async def watch_change(plan: ModePlan, source: str, started: float, fault: str | None) -> None:
        while clock.monotonic() - started < WATCH_LIMIT_S:
            if engine.current_plan() is not plan:
                results["abandoned"] += 1  # superseded before it settled (button mash)
                return
            if _all_correct(world, plan):
                results["changes"].append(
                    {
                        "source": source,
                        "mode": plan.mode_id,
                        "seconds": clock.monotonic() - started,
                        "fault": fault,
                    }
                )
                return
            await clock.sleep(POLL_S)
        results["changes"].append({"source": source, "mode": plan.mode_id, "seconds": None, "fault": fault})

    def on_event(event: dict) -> None:
        if event.get("kind") != "mode":
            return
        plan = engine.current_plan()
        source = event.get("source", "")
        if plan is None or source in ("reconciler", "recovery"):
            return
        results["wiring"]["motion_plans"] += int(bool(plan.motion))
        # D75: a run's end onto the best still tier, not a guest change: nothing to time
        if source == "settle":
            results["wiring"]["settles"] += 1
            return
        tiers = results["wiring"]["tiers"]
        tiers[str(plan.tier)] = tiers.get(str(plan.tier), 0) + 1
        now = clock.monotonic()
        spawn(watch_change(plan, source, now, in_fault(now, pad_after=60.0) or engine.outage and "resting"))

    world.telemetry.subscribe(on_event)

    original_accept = sim.cloud.accept_wire

    def accept_wire(body: Any, *, silent_drop: bool = False):
        response = original_accept(body, silent_drop=silent_drop)
        if silent_drop and response.ok:
            address = str(body.get("metadeviceId"))
            dropped = [StateRow.from_wire(v) for v in body.get("values", [])]
            spawn(watch_heal(address, dropped, clock.monotonic()))
        return response

    sim.cloud.accept_wire = accept_wire  # type: ignore[method-assign]

    async def watch_heal(address: str, dropped: list[StateRow], started: float) -> None:
        plan = engine.current_plan()
        device_id = address.removeprefix("sim-")
        if plan is None or device_id not in plan.modules:
            return
        mt = plan.modules[device_id]
        if R.compare(mt.rows, dropped) or not R.compare(dropped, sim.device_view(address)):
            return  # not the current look (a superseded command or a slot write), or already showing it
        while clock.monotonic() - started < WATCH_LIMIT_S:
            if engine.current_plan() is not plan:
                return
            if _correct(world, plan, device_id):
                results["heals"].append(clock.monotonic() - started)
                return
            await clock.sleep(POLL_S)
        results["heal_timeouts"] += 1

    # A look other than the module's own: blue if it isn't showing blue already, else warm white.
    def out_of_band_rows(mt: Any) -> list[StateRow]:
        blue = [R.power(True), R.color_mode("color"), R.color_rgb(0, 0, 255)]
        if not R.compare(blue, mt.expect or mt.rows):
            return [R.power(True), R.color_mode("white"), R.color_temperature(4000)]
        return blue

    async def drift(t: float) -> None:
        """Change one module outside the Conductor, then time until the lights show the look again."""
        await at(t)
        for _ in range(6):  # wait (up to 30 s) for a settled canopy, so this measures drift, not a change
            plan = engine.current_plan()
            if plan is not None and engine.outage is None and _all_correct(world, plan):
                break
            await clock.sleep(5.0)
        else:
            results["drift_skipped"] += 1
            return
        results["drift_attempted"] += 1
        device_id = drift_rng.choice(sorted(plan.modules))
        address = world.addresses[device_id]
        response = sim.faults.change_out_of_band(address, out_of_band_rows(plan.modules[device_id]))
        if not response.ok:
            results["drift_skipped"] += 1
            return
        started = clock.monotonic()
        seen_wrong = False
        while clock.monotonic() - started < WATCH_LIMIT_S:
            if engine.current_plan() is not plan:
                results["drift_skipped"] += 1  # a look change (idle return, ops) superseded it
                return
            correct = _correct(world, plan, device_id)
            if not correct:
                seen_wrong = True
            elif seen_wrong or clock.monotonic() - started > 3.0:  # the edit applied (or the fix beat it)
                results["drift_heals"].append(clock.monotonic() - started)
                return
            await clock.sleep(POLL_S)
        results["drift_timeouts"] += 1

    async def watch_recovery(name: str, ended: float) -> None:
        while clock.monotonic() - ended < WATCH_LIMIT_S * 2:
            plan = engine.current_plan()
            if engine.outage is None and plan is not None and _all_correct(world, plan):
                results["recoveries"][name] = clock.monotonic() - ended
                return
            await clock.sleep(POLL_S)
        results["recoveries"][name] = None

    # -- schedule -------------------------------------------------------------------------------------------------
    kiosk = world.engine.deps.settings.kiosk
    # changes at least a guest gap apart (D70: what a nine-module look buys), and never closer than the 8 s the
    # schedule always used, so the seeded schedule (and every pinned row's sample) stays the one it was
    nine = len(world.registry.canopy_modules()) * 60.0 / kiosk.guest_writes_per_min
    cooldown = max(SCHEDULE_GAP_S, kiosk.cooldown_min_s, nine)
    times: list[float] = []
    lo, hi = t0 + 60.0, t0 + duration - 600.0
    attempts = 0
    while len(times) < cfg.changes and attempts < cfg.changes * 1000:
        attempts += 1
        t = rng.uniform(lo, hi)
        if in_fault(t - 30.0, pad_after=150.0) or in_fault(t, pad_after=150.0) or abs(t - mash_at) < 120.0:
            continue
        if any(abs(t - other) < cooldown + 2.0 for other in times):
            continue
        times.append(t)
    times.sort()
    modes = compiler.modes()
    drift_rng = random.Random(f"{cfg.seed}:drift")  # its own stream: the rest of the schedule stays as it was
    drift_times: list[float] = []
    attempts = 0
    while len(drift_times) < cfg.drift_events and attempts < max(1, cfg.drift_events) * 1000:
        attempts += 1
        t = drift_rng.uniform(lo, t0 + duration - 120.0)
        # clear of the fault windows (and the minute after), the mash, and every mode change: 30 s after one
        # (settled) and at least 20 s before the next; drift() also waits for a settled canopy first
        if in_fault(t, pad_after=60.0) or in_fault(t + 20.0) or mash_at - 20.0 < t < mash_at + 90.0:
            continue
        if any(t - 30.0 < c < t + 20.0 for c in times):
            continue
        if any(abs(t - other) < 30.0 for other in drift_times):
            continue
        drift_times.append(t)
    drift_times.sort()

    async def at(t: float) -> None:
        delay = t - clock.monotonic()
        if delay > 0:
            await clock.sleep(delay)

    async def kiosk_changes() -> None:
        for t in times:
            await at(t)
            current = engine.current_plan()
            choices = [m for m in modes if current is None or m != current.mode_id] or modes
            state = await engine.request_mode(rng.choice(choices), source="kiosk")
            if state.refused:
                results["refused"]["kiosk"] += 1

    async def mash() -> None:
        await at(mash_at)
        for i in range(cfg.mash_kiosk_taps):
            state = await engine.request_mode(modes[i % len(modes)], source="kiosk")
            if state.refused:
                results["refused"]["kiosk_mash"] = results["refused"].get("kiosk_mash", 0) + 1
            await clock.sleep(0.25)
        for i in range(cfg.mash_ops_taps):
            await engine.request_mode(modes[(i * 7 + 3) % len(modes)], source="ops")
            await clock.sleep(0.2)

    async def faults() -> None:
        start, end = windows["http_500"]
        await at(start)
        sim.faults.start_500_window(end - start)
        await at(start + (end - start) / 2)
        await engine.request_mode(_other(modes, engine), source="ops")
        await at(end)
        spawn(watch_recovery("http_500", clock.monotonic()))
        start, end = windows["outage"]
        await at(start)
        sim.faults.set_outage(True)
        await at(start + (end - start) / 3)
        state = await engine.request_mode(_other(modes, engine), source="kiosk")
        if state.refused:
            results["refused"]["during_faults"] += 1
        await engine.request_mode(_other(modes, engine), source="ops")
        await at(end)
        sim.faults.set_outage(False)
        spawn(watch_recovery("outage", clock.monotonic()))

    # -- run ------------------------------------------------------------------------------------------------------
    if world.wiring is not None:
        commission = world.wiring.last_commission or {"ok": False, "message": "never ran", "modes": {}}
    else:
        commission = await engine.commission(source="soak")
        await engine.start()
    drivers = [asyncio.get_running_loop().create_task(c) for c in (kiosk_changes(), mash(), faults())]
    drivers += [asyncio.get_running_loop().create_task(drift(t)) for t in drift_times]
    await at(t0 + duration)
    for task in drivers:
        if not task.done():
            task.cancel()
    await asyncio.gather(*drivers, return_exceptions=True)
    # let the last measurements finish, then look at the lights one last time
    for _ in range(int(WATCH_LIMIT_S * 2 / 5)):
        if not tasks:
            break
        await clock.sleep(5.0)
    final_plan = engine.current_plan()
    results["final_correct"] = final_plan is not None and _all_correct(world, final_plan)
    results["final_mode"] = final_plan.mode_id if final_plan else None
    results["final_tier"] = str(final_plan.tier) if final_plan else None
    if world.wiring is not None:
        await world.wiring.close()
        await world.services.close()
        world.cleanup()
    else:
        await engine.stop()
        await world.executor.close()
    for task in list(tasks):
        task.cancel()
    results["commission"] = {
        "ok": commission.get("ok"),
        "message": commission.get("message"),
        "modes": sorted(commission.get("modes", {})),
    }
    results["tier_changes"] = list(engine.stats["tier_changes"])
    results["outages"] = list(engine.stats["outages"])
    results["early_reasserts"] = engine.stats["early_reasserts"]
    results["reconciler"] = dict(engine.reconciler.stats)
    results["sim"] = {
        k: v for k, v in sim.stats().items() if k not in ("writes_per_device", "min_interval_violations")
    }
    results["windows_s"] = {"http_500": window_s, "outage": outage_s}
    results["duration_s"] = duration
    reads = results["reconciler"].get("true_up_reads", 0)
    modules = len(world.modules) or 1
    interval = engine.deps.settings.reconciler.true_up_interval_s
    results["read_load"] = {
        "true_up_reads": reads,
        "true_up_interval_s": interval,
        "per_s": reads / duration if duration else 0.0,
        "nominal_per_s": modules / interval,  # one account: all nine canopy modules
        "all_reads": results["sim"].get("reads", 0),
        "all_writes": results["sim"].get("writes", 0),
    }
    return results


async def _ripple_soak(cfg: SoakConfig) -> dict:
    """The ripple scenario (D64): the show's own wiring with `kiosk.ripple_stagger_s` set, seeded kiosk mood changes
    `spacing_s` (+ up to 2 s) apart, each measured ring by ring from the lights (see the module docstring)."""
    ripple = cfg.ripple
    stagger = float(ripple["stagger_s"])
    world = await _build_show(cfg, ripple_stagger_s=stagger)
    engine, clock = world.engine, world.clock
    while world.wiring.last_commission is None and clock.monotonic() < 600:
        await clock.sleep(1.0)
    by_position = {str(e.module): e.id for e in world.registry.canopy_modules()}
    by_address = {a: d for d, a in world.addresses.items()}
    submits: list[tuple[float, str, str]] = []  # (when, device id, label): every command the engine sent
    world.executor.add_submit_listener(
        lambda cmd: submits.append(
            (clock.monotonic(), by_address.get(cmd.device_id, cmd.device_id), cmd.label)
        )
    )
    rng = random.Random(f"{cfg.seed}:ripple")
    modes = world.wiring.mode_ids()
    out: dict[str, Any] = {
        "stagger_s": stagger,
        "changes": 0,  # accepted changes that landed as a ripple
        "bursts": 0,  # accepted changes that went out as one burst (a motion plan, or one ring)
        "refused": 0,
        "abandoned": 0,
        "unsettled": 0,
        "ring_spreads": [],  # per ring: first to last module of the ring correct (s)
        "settles": [],  # per change: request → every module correct (s)
        "rings_max": 0,
        "puts_per_module": [],  # per (change, module): the commands of that change it got
        "early": 0,  # commands to a module before its ring was due
        "settings": {k: ripple[k] for k in RIPPLE_DEFAULTS},
    }
    downgrades_before = len([t for t in engine.stats["tier_changes"] if t["direction"] == "down"])
    for _ in range(int(ripple["changes"])):
        await clock.sleep(float(ripple["spacing_s"]) + rng.uniform(0.0, 2.0))
        current = engine.current_plan()
        mode_id = rng.choice([m for m in modes if current is None or m != current.mode_id] or modes)
        started = clock.monotonic()
        state = await engine.request_mode(mode_id, source="kiosk")
        if state.refused:
            out["refused"] += 1
            continue
        plan = engine.current_plan()
        if state.ripple is None:
            out["bursts"] += 1
            continue
        rings = [[by_position[p] for p in ring] for ring in state.ripple["rings"]]
        ring_of = {d: k for k, ring in enumerate(rings) for d in ring}
        out["rings_max"] = max(out["rings_max"], len(rings))
        landed: dict[str, float] = {}
        while clock.monotonic() - started < WATCH_LIMIT_S and len(landed) < len(plan.modules):
            if engine.current_plan() is not plan:
                break
            elapsed = clock.monotonic() - started
            for d in plan.modules:
                # a module counts from its ring's turn (its old look may already match the new one)
                if d not in landed and elapsed >= ring_of.get(d, 0) * stagger and _correct(world, plan, d):
                    landed[d] = elapsed
            await clock.sleep(POLL_S)
        if engine.current_plan() is not plan:
            out["abandoned"] += 1
            continue
        if len(landed) < len(plan.modules):
            out["unsettled"] += 1
            continue
        out["changes"] += 1
        out["settles"].append(max(landed.values()))
        for ring in rings:
            times = [landed[d] for d in ring]
            out["ring_spreads"].append(max(times) - min(times))
        settled_at = started + max(landed.values())
        label = f"mode: {mode_id}"
        for d in plan.modules:
            mine = [(t, lab) for t, dev, lab in submits if dev == d and started - 1e-9 <= t <= settled_at]
            out["puts_per_module"].append(sum(1 for _, lab in mine if lab.startswith(label)))
            due = started + ring_of.get(d, 0) * stagger
            out["early"] += sum(1 for t, _ in mine if t < due - 1e-6)
    out["downgrades"] = (
        len([t for t in engine.stats["tier_changes"] if t["direction"] == "down"]) - downgrades_before
    )
    await world.wiring.close()
    await world.services.close()
    world.cleanup()
    return out


async def _stepped_soak(cfg: SoakConfig) -> dict:
    """The stepped scenario (D65, D75): the show's own wiring with a stepped rung on every one of `stepped["modes"]`
    (their own where the show has it, or a SIMULATED one just before `modules`), kiosk runs of them under the pinned
    conditions, each step measured from the lights and each run's end by the tier it settled on (see the module
    docstring)."""
    st = cfg.stepped
    world = await _build_show(cfg, stepped=st)
    engine, clock, sim = world.engine, world.clock, world.sim
    while world.wiring.last_commission is None and clock.monotonic() < 600:
        await clock.sleep(1.0)
    settings = engine.deps.settings
    stepped_settings = settings.motion.stepped
    by_address = {a: d for d, a in world.addresses.items()}
    rng = random.Random(f"{cfg.seed}:stepped")
    stepped_modes = list(st["modes"])
    still = [m for m in world.wiring.mode_ids() if m not in stepped_modes and m != settings.kiosk.idle_mode]
    from conductor.looks import load_modes

    show_modes = load_modes(CONFIG_DIR)  # the show's own modes.yaml: which candidates bring their own rung
    out: dict[str, Any] = {
        "modes": stepped_modes,
        "techniques": {  # what each candidate steps with in this run (D75: its kind of motion too)
            m: {
                "own": show_modes.modes[m].techniques.stepped is not None,
                **{
                    k: getattr(world.wiring.modes_file.mode(m).techniques.stepped, k)
                    for k in ("steps_per_cycle", "step_s", "run_s", "mode", "brightness_swing", "lag_steps")
                },
            }
            for m in stepped_modes
        },
        "settings": {k: st[k] for k in STEPPED_DEFAULTS},
        "cap": stepped_settings.account_writes_per_min,
        "requests": 0,
        "refused": 0,
        "runs": 0,  # accepted choices that started a run
        "not_stepping": 0,  # accepted choices that installed a stepped look that held still, or another tier
        "tiers": {},
        "ended": {},  # why each run stopped: "the run is over", "replaced" (cut short by a newer mood), …
        "interrupted": 0,
        "step_s": [],  # the runner's step per run
        "step_spreads": [],  # per landed step: first to last of its modules showing it (s)
        "step_landings": [],  # per landed step: its send → the last of its modules showing it (s)
        "steps_landed": 0,
        "steps_superseded": 0,  # a module was sent the next step before it showed this one
        "steps_cut": 0,  # a newer mood replaced the look before the step landed (not a failure)
        "steps_failed": 0,  # never shown within WATCH_LIMIT_S
        "steps_skipped": 0,  # steps the runner's schedule skipped (it came due late)
        "puts_per_module_step": [],  # per (step, module sent): the program commands that step sent it
        "missing": 0,  # modules whose rows changed from the step before but weren't sent
        "holds": [],  # per finished run: the still look sent → every module showing it (s)
        "holds_unsettled": 0,
        "settled": {},  # D75: per finished run, the tier its look settled on (a canvas or slot, else its own hold)
        "max_account_writes_60s": 0,  # every write the simulator received on one account, any 60 s
        "max_account_program_puts_60s": 0,  # the stepped program's commands on one account, by send time, any 60 s
        "idle_return": None,  # {after_s, mode, settled_s, ran}
        "idle_return_s": settings.kiosk.idle_return_s,
    }
    # every write the simulator receives, per account: the sliding 60 s maximum
    writes: dict[str, list[float]] = {}
    original_write = sim.write

    async def counted_write(device_id: str, rows: list[StateRow], *, purpose: Purpose):
        writes.setdefault(sim.account_of(device_id), []).append(clock.monotonic())
        return await original_write(device_id, rows, purpose=purpose)

    sim.write = counted_write  # type: ignore[method-assign]
    program_puts: dict[str, list[float]] = {}  # account → when each program command was sent
    # every program command, grouped by step: (runner, label) → the step's record
    groups: dict[tuple[int, str], dict] = {}
    latest: dict[str, dict] = {}  # device id → the group that last sent it a program command
    watchers: set[asyncio.Task] = set()

    def on_submit(cmd: Any) -> None:
        if cmd.source != "program":
            return
        runner, device_id = engine.runner, by_address.get(cmd.device_id, cmd.device_id)
        key = (id(runner), cmd.label)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "t": clock.monotonic(),
                "plan": engine.current_plan(),
                "runner": runner,
                "label": cmd.label,
                "rows": {},
                "counts": Counter(),
                "landed": {},
                "superseded": False,
            }
            task = asyncio.get_running_loop().create_task(watch_step(group))
            watchers.add(task)
            task.add_done_callback(watchers.discard)
        previous = latest.get(device_id)
        if previous is not None and previous is not group and device_id not in previous["landed"]:
            previous["superseded"] = True
        latest[device_id] = group
        program_puts.setdefault(sim.account_of(cmd.device_id), []).append(clock.monotonic())
        group["rows"][device_id] = list(cmd.rows)
        group["counts"][device_id] += 1

    world.executor.add_submit_listener(on_submit)

    async def watch_step(group: dict) -> None:
        await asyncio.sleep(0)  # the step's other modules are submitted in the same call
        while True:
            now = clock.monotonic()
            for device_id, rows in group["rows"].items():
                if device_id not in group["landed"]:
                    view = sim.device_view(world.addresses[device_id])
                    if not R.compare(rows, view):
                        group["landed"][device_id] = now - group["t"]
            if len(group["landed"]) == len(group["rows"]):
                times = list(group["landed"].values())
                out["steps_landed"] += 1
                out["step_spreads"].append(max(times) - min(times))
                out["step_landings"].append(max(times))
                return
            if group["superseded"]:
                out["steps_superseded"] += 1
                return
            if engine.current_plan() is not group["plan"]:
                out["steps_cut"] += 1
                return
            if now - group["t"] > WATCH_LIMIT_S:
                out["steps_failed"] += 1
                return
            await clock.sleep(POLL_S)

    tier_downs = len([t for t in engine.stats["tier_changes"] if t["direction"] == "down"])
    trips = engine.stats.get("governor_trips", 0)
    last_choice = clock.monotonic()
    for i in range(int(st["runs"])):
        await clock.sleep(float(st["hold_s"]) + rng.uniform(0.0, 2.0))
        mode_id = stepped_modes[i % len(stepped_modes)]
        out["requests"] += 1
        state = await engine.request_mode(mode_id, source="kiosk")
        if state.refused:
            out["refused"] += 1
            continue
        last_choice = clock.monotonic()
        out["tiers"][str(state.tier)] = out["tiers"].get(str(state.tier), 0) + 1
        runner = engine.runner
        if runner is None or not runner.running:
            out["not_stepping"] += 1
            continue
        out["runs"] += 1
        out["step_s"].append(runner.step_s)
        plan = engine.current_plan()
        run_s = runner.count * runner.step_s
        every = int(st.get("interrupt_every") or 0)
        interrupting = None
        if every and (i + 1) % every == 0:
            mood = _interrupting_mood(rng, still, stepped_modes, mode_id, settings.kiosk.idle_mode)
            if mood is not None:
                # a guest chooses another mood mid-run: the run stops there, its steps not yet landed are cut
                await clock.sleep(
                    rng.uniform(2 * runner.step_s, max(2 * runner.step_s, run_s - 2 * runner.step_s))
                )
                state = await engine.request_mode(mood, source="kiosk")
                if not state.refused:
                    out["interrupted"] += 1
                    last_choice = clock.monotonic()
                    interrupting = engine.runner
        while runner.running and clock.monotonic() - last_choice < run_s + WATCH_LIMIT_S:
            await clock.sleep(POLL_S)
        out["ended"][runner.ended or "?"] = out["ended"].get(runner.ended or "?", 0) + 1
        out["steps_skipped"] += runner.skipped
        # D75: every scene steps, so the mood that cut the run short runs too; the next guest comes `hold_s` after
        # that run ends, as after any other (two choices around a full-pace run would crowd one 60 s window)
        while (
            interrupting is not None
            and interrupting.running
            and clock.monotonic() - last_choice < interrupting.count * interrupting.step_s + WATCH_LIMIT_S
        ):
            await clock.sleep(POLL_S)
        current = engine.current_plan()
        # D75: the run's end settles onto the mode's canvas or slot (a new plan of the same mode), else holds its own
        if current is not None and current.mode_id == plan.mode_id and not runner.running:
            ended = clock.monotonic()  # the still look was sent when the run ended
            while clock.monotonic() - ended < WATCH_LIMIT_S and not _all_correct(world, current):
                await clock.sleep(POLL_S)
            if _all_correct(world, current):
                out["holds"].append(clock.monotonic() - ended)
                tier = str(current.tier)
                out["settled"][tier] = out["settled"].get(tier, 0) + 1
            else:
                out["holds_unsettled"] += 1
    # no input after the last choice: the idle hold returns to the idle mode, the runs never moving its clock
    idle_mode = settings.kiosk.idle_mode
    deadline = last_choice + settings.kiosk.idle_return_s + 2 * float(st["idle_return_slack_s"])
    while clock.monotonic() < deadline:
        plan = engine.current_plan()
        if plan is not None and plan.mode_id == idle_mode:
            back = clock.monotonic()
            while clock.monotonic() - back < WATCH_LIMIT_S and not _all_correct(world, plan):
                await clock.sleep(POLL_S)
            out["idle_return"] = {
                "after_s": back - last_choice,
                "mode": idle_mode,
                "settled_s": clock.monotonic() - back if _all_correct(world, plan) else None,
                # D75: an idle return shows the idle look still (motion.idle is off), even with a rung of its own
                "ran": engine.runner is not None and engine.runner.running,
            }
            break
        await clock.sleep(POLL_S)
    for task in list(watchers):
        await task
    out["max_account_writes_60s"] = max((_most_in_60s(times) for times in writes.values()), default=0)
    out["max_account_program_puts_60s"] = max((_most_in_60s(t) for t in program_puts.values()), default=0)
    for group in groups.values():
        out["puts_per_module_step"] += list(group["counts"].values())
    out["missing"] = _missing_steps(groups)
    out["governor_trips"] = engine.stats.get("governor_trips", 0) - trips
    out["downgrades"] = (
        len([t for t in engine.stats["tier_changes"] if t["direction"] == "down"]) - tier_downs
    )
    await world.wiring.close()
    await world.services.close()
    world.cleanup()
    return out


def _interrupting_mood(
    rng: random.Random, still: list[str], stepped_modes: list[str], mode_id: str, idle_mode: str
) -> str | None:
    """The mood a guest cuts a run short with: a still one, else (every scene steps, D75) another of the scenario's
    modes — never the running one and never the idle look, so the idle return after the last choice stays
    measurable. None when there is none."""
    pool = still or [m for m in stepped_modes if m not in (mode_id, idle_mode)]
    return rng.choice(pool) if pool else None


def _most_in_60s(times: list[float]) -> int:
    """The most of `times` in any half-open 60 s window (a step 60 s after another starts the next window)."""
    times = sorted(times)
    most = lo = 0
    for hi, t in enumerate(times):
        while t - times[lo] >= 60.0 - 1e-6:
            lo += 1
        most = max(most, hi - lo + 1)
    return most


def _missing_steps(groups: dict) -> int:
    """Modules whose rows changed from one step to the next of the same run but that the later step didn't send."""
    missing = 0
    by_runner: dict[int, list[dict]] = {}
    for (runner_id, _label), group in groups.items():
        by_runner.setdefault(runner_id, []).append(group)
    for runs in by_runner.values():
        runs.sort(key=lambda g: g["t"])
        for before, after in itertools.pairwise(runs):
            runner = after["runner"]
            for device_id in after["plan"].modules:
                old = before["rows"].get(device_id)
                if old is None or device_id in after["rows"]:
                    continue
                new = _step_rows(runner, after["label"], device_id)
                if new is not None and R.compare(new, old):
                    missing += 1
    return missing


def _step_rows(runner: Any, label: str, device_id: str) -> list[StateRow] | None:
    """The rows step `label` ("step 7 (2 of 6): …", "hold: …") was to send `device_id`."""
    if label.startswith("hold"):
        mt = runner.plan.modules[device_id]
        return mt.rows
    try:
        n = int(label.split()[1])
    except (IndexError, ValueError):
        return None
    return runner.program.steps[n % runner.cycle].rows.get(device_id)


def _other(modes: list[str], engine: Any) -> str:
    current = engine.current_plan()
    return next((m for m in modes if current is None or m != current.mode_id), modes[0])


# ---- verdicts and the report ---------------------------------------------------------------------------------------
def evaluate(results: dict, targets: dict) -> list[dict]:
    """One row per target: name, p50/p95/max (where they apply), target text, PASS/FAIL."""
    p95_target = float(targets.get("all_modules_correct_p95_s", 5))
    heal_target = float(targets.get("self_heal_max_s", 10))
    recovery_target = float(targets.get("recovery_after_outage_max_s", 60))
    downgrade_target = int(targets.get("tier_downgrades_at_5pct_throttle", 0))
    clean = [c for c in results["changes"] if not c["fault"]]
    times = [c["seconds"] for c in clean if c["seconds"] is not None]
    unsettled = [c for c in clean if c["seconds"] is None]
    rows = []

    def stats(values: list[float]) -> tuple[float | None, float | None, float | None]:
        return percentile(values, 50), percentile(values, 95), (max(values) if values else None)

    p50, p95, mx = stats(times)
    rows.append(
        {
            "name": f"All nine modules correct after a mode change ({len(times)} changes)",
            "p50": p50,
            "p95": p95,
            "max": mx,
            "target": f"p95 ≤ {p95_target:g} s, every change settles",
            "pass": bool(times) and p95 is not None and p95 <= p95_target and not unsettled,
        }
    )
    rows.append(
        {
            "name": "Executor queue depth (pending + in flight, all devices)",
            "max": results["max_queue"],
            "target": "≤ 18 (2 per device) — never grows",
            "pass": results["max_queue"] <= 18,
        }
    )
    heals = results["heals"]
    p50, p95, mx = stats(heals)
    rows.append(
        {
            "name": f"Self-heal after a silent drop ({len(heals)} drops that mattered)",
            "p50": p50,
            "p95": p95,
            "max": mx,
            "target": f"every one ≤ {heal_target:g} s",
            "pass": results["heal_timeouts"] == 0 and (mx is None or mx <= heal_target),
        }
    )
    drift_heals = results.get("drift_heals") or []
    p50, p95, mx = stats(drift_heals)
    rows.append(
        {
            "name": f"Detect + heal a change made outside the Conductor ({len(drift_heals)} out-of-band changes)",
            "p50": p50,
            "p95": p95,
            "max": mx,
            "target": f"every one ≤ {heal_target:g} s",
            "pass": bool(drift_heals)
            and results.get("drift_timeouts", 0) == 0
            and mx is not None
            and mx <= heal_target,
        }
    )
    downgrades = [t for t in results["tier_changes"] if t["direction"] == "down"]
    rows.append(
        {
            "name": "Tier downgrades at 5% throttling (whole soak)",
            "max": len(downgrades),
            "target": f"= {downgrade_target}",
            "pass": len(downgrades) <= downgrade_target,
        }
    )
    for name, label in (("outage", "internet outage"), ("http_500", "window of HTTP 500s")):
        value = results["recoveries"].get(name)
        minutes = results["windows_s"][name] / 60
        rows.append(
            {
                "name": f"Current look re-applied after the {minutes:.0f}-min {label}",
                "max": value,
                "target": f"≤ {recovery_target:g} s after it ends",
                "pass": value is not None and value <= recovery_target,
            }
        )
    gap = results["min_write_gap_s"]
    rows.append(
        {
            "name": "Smallest gap between two writes to one device",
            "max": gap,
            "target": "≥ 1.0 s (rate budget)",
            "pass": gap is None or gap >= 1.0 - 1e-6,
        }
    )
    rows.append(
        {
            "name": "Final look correct on all nine modules",
            "max": results["final_mode"],
            "target": "yes",
            "pass": bool(results["final_correct"]),
        }
    )
    if results.get("ripple"):
        rows += _ripple_rows(results["ripple"])
    if results.get("stepped"):
        rows += _stepped_rows(results["stepped"])
    return rows


def _stepped_rows(stepped: dict) -> list[dict]:
    """The stepped scenario's verdict rows (D65). Each step's landing spread is reported, not gating (05's
    spread target, which D65 asks Alon to sign off per step); the rest gate: every step lands before the next one
    (none superseded, none failed), exactly one PUT per changed module per step, the account inside the cap, no
    governor trip and no downgrade at 5% throttling, every run's still look shown, the idle return on time."""
    s = stepped["settings"]
    spread_target = float(s["step_spread_p95_s"])
    spreads, landings, puts = (
        stepped["step_spreads"],
        stepped["step_landings"],
        stepped["puts_per_module_step"],
    )
    holds = stepped["holds"]
    step_s = min(stepped["step_s"]) if stepped["step_s"] else None
    cap = stepped["cap"]
    idle = stepped.get("idle_return") or {}
    idle_limit = float(s["idle_return_slack_s"])
    tag = "Stepped, SIMULATED"
    runs = f"{stepped['runs']} runs of {', '.join(stepped['modes'])}"
    return [
        {
            "name": f"{tag}: each step's landing spread ({len(spreads)} steps in {runs})",
            "p50": percentile(spreads, 50),
            "p95": percentile(spreads, 95),
            "max": max(spreads) if spreads else None,
            "target": f"p95 ≤ {spread_target:g} s (05's spread, per step; reported for D65, not gating until Alon "
            "signs a stepped target off)",
            "pass": bool(spreads) and (percentile(spreads, 95) or 0) <= spread_target,
            "gating": False,
        },
        {
            "name": f"{tag}: each step shown on all its modules, send → last module ({stepped['steps_landed']} "
            f"steps; {stepped['steps_superseded']} superseded, {stepped['steps_failed']} failed, "
            f"{stepped['steps_skipped']} skipped)",
            "p50": percentile(landings, 50),
            "p95": percentile(landings, 95),
            "max": max(landings) if landings else None,
            "target": f"every step on all its modules before the next is sent ({_fmt(step_s)} s steps): 0 "
            "superseded, failed or skipped",
            "pass": bool(landings)
            and stepped["steps_superseded"] == stepped["steps_failed"] == stepped["steps_skipped"] == 0,
        },
        {
            "name": f"{tag}: PUTs per changed module per step ({len(puts)} module steps)",
            "max": max(puts) if puts else None,
            "target": "exactly 1, none missing",
            "pass": bool(puts) and set(puts) == {1} and stepped["missing"] == 0,
        },
        {
            "name": f"{tag}: most stepped program PUTs on one account in any 60 s (by send time)",
            "max": stepped["max_account_program_puts_60s"],
            "target": f"≤ {cap:g} (motion.stepped.account_writes_per_min; the runner plans below it)",
            "pass": stepped["max_account_program_puts_60s"] <= cap,
        },
        {
            "name": f"{tag}: most writes of any kind on one account in any 60 s (steps, 429 retries, re-sends, "
            "mood changes)",
            "max": stepped["max_account_writes_60s"],
            "target": f"≤ {cap:g} (motion.stepped.account_writes_per_min: the steps are paced at the cap less "
            "motion.stepped.budget_reserve, the room retries, re-sends and a mood chosen mid-run need)",
            "pass": stepped["max_account_writes_60s"] <= cap,
        },
        {
            "name": f"{tag}: throttle governor trips at 5% throttling",
            "max": stepped["governor_trips"],
            "target": "= 0",
            "pass": stepped["governor_trips"] == 0,
        },
        {
            "name": f"{tag}: tier downgrades at 5% throttling",
            "max": stepped["downgrades"],
            "target": "= 0",
            "pass": stepped["downgrades"] == 0,
        },
        {
            "name": f"{tag}: the still look shown after each run ({len(holds)} runs ran to the end)",
            "p50": percentile(holds, 50),
            "p95": percentile(holds, 95),
            "max": max(holds) if holds else None,
            "target": "every one settles",
            "pass": bool(holds) and stepped["holds_unsettled"] == 0,
        },
        {
            "name": f"{tag}: idle return after the last choice",
            "max": idle.get("after_s"),
            "target": f"{idle.get('mode') or 'the idle mode'} {stepped['idle_return_s']:g}–"
            f"{stepped['idle_return_s'] + idle_limit:g} s after it (idle_return_s + {idle_limit:g}), still (no run: "
            "motion.idle is off, D75), then settles",
            "pass": bool(idle)
            and stepped["idle_return_s"] - 1e-6 <= idle["after_s"] <= stepped["idle_return_s"] + idle_limit
            and idle["settled_s"] is not None
            and not idle.get("ran"),
        },
    ]


def _ripple_rows(ripple: dict) -> list[dict]:
    """The ripple scenario's verdict rows (D64): per-ring spread (reported, not gating: `gating` False), last-ring
    settle, one PUT each and none early, no downgrade."""
    settings = ripple["settings"]
    stagger = float(ripple["stagger_s"])
    spread_target = float(settings["ring_spread_p95_s"])
    settle_target = (max(1, ripple["rings_max"]) - 1) * stagger + float(settings["settle_extra_s"])
    spreads, settles = ripple["ring_spreads"], ripple["settles"]
    puts = ripple["puts_per_module"]
    tag = f"Ripple, SIMULATED {stagger:g} s stagger"
    return [
        {
            "name": f"{tag}: each ring's landing spread ({len(spreads)} rings in {ripple['changes']} changes)",
            "p50": percentile(spreads, 50),
            "p95": percentile(spreads, 95),
            "max": max(spreads) if spreads else None,
            "target": f"p95 ≤ {spread_target:g} s (05's spread, per ring; reported for D64, not gating while the "
            "show's stagger is 0)",
            "pass": bool(spreads) and (percentile(spreads, 95) or 0) <= spread_target,
            # 05's spread is what D64 redefines per ring, and the show runs one burst until Alon flips it: the
            # number is reported for that decision, never a reason `make soak` fails
            "gating": False,
        },
        {
            "name": f"{tag}: every module correct after the request (last ring settled)",
            "p50": percentile(settles, 50),
            "p95": percentile(settles, 95),
            "max": max(settles) if settles else None,
            "target": f"p95 ≤ {settle_target:g} s ((rings − 1) × stagger + {float(settings['settle_extra_s']):g}), "
            "every change settles",
            "pass": bool(settles)
            and (percentile(settles, 95) or 0) <= settle_target
            and not ripple["unsettled"],
        },
        {
            "name": f"{tag}: PUTs per module per change ({len(puts)} module changes)",
            "max": max(puts) if puts else None,
            "target": "exactly 1, none before its ring is due",
            "pass": bool(puts) and set(puts) == {1} and ripple["early"] == 0,
        },
        {
            "name": f"{tag}: tier downgrades at 5% throttling",
            "max": ripple["downgrades"],
            "target": "= 0",
            "pass": ripple["downgrades"] == 0,
        },
    ]


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def passed(verdicts: list[dict]) -> bool:
    """Every gating row passed (a row with `gating` False is reported only)."""
    return all(v["pass"] for v in verdicts if v.get("gating", True))


def _result(v: dict) -> str:
    return ("PASS" if v["pass"] else "FAIL") + ("" if v.get("gating", True) else " (reported)")


def render_report(results: dict, verdicts: list[dict], meta: dict) -> str:
    ok = passed(verdicts)
    s = meta["sim_settings"]
    windows = results["windows_s"]
    commission = results["commission"]
    lines = [
        "# Simulator acceptance soak",
        "",
        f"_Generated by `python -m conductor.soak` on {meta['generated']} — {'**PASS**' if ok else '**FAIL**'}._",
        "",
        (
            f"- Simulated time: {results['duration_s'] / 3600:g} h on `VirtualTimeLoop` "
            f"({meta['wall_s']:.1f} s wall). Seed {meta['seed']}."
        ),
        (
            f"- Conditions: `{meta['acceptance_file']}` (pinned, not tuned): PUT ack {s.ack_ms_min:g}–"
            f"{s.ack_ms_max:g} ms {s.ack_distribution}, device apply +{s.device_apply_extra_s_min:g}–"
            f"{s.device_apply_extra_s_max:g} s, {s.throttle_429_rate:.0%} of requests → 429, "
            f"{s.silent_drop_rate:.0%} silent drops, readback semantics `{s.readback_semantics}`."
        ),
        f"- World: {meta['compiler']}.",
        _motion_line(results),
        (
            f"- Faults: one {windows['http_500'] / 60:.0f}-min window of HTTP 500s and one "
            f"{windows['outage'] / 60:.0f}-min internet outage, each with an ops mode change in the middle; "
            "a button-mash burst of kiosk and ops taps."
        ),
        f"- Commissioning: {commission['message']} (modes: {', '.join(commission['modes']) or 'none'}).",
        "",
        "| Measure | p50 | p95 | max | Target | Result |",
        "|---|---|---|---|---|---|",
    ]
    for v in verdicts:
        lines.append(
            f"| {v['name']} | {_fmt(v.get('p50'))} | {_fmt(v.get('p95'))} | {_fmt(v.get('max'))} | "
            f"{v['target']} | {_result(v)} |"
        )
    by_source: dict[str, int] = {}
    for c in results["changes"]:
        by_source[c["source"]] = by_source.get(c["source"], 0) + 1
    faulted = [c for c in results["changes"] if c["fault"]]
    sim, rec, refused = results["sim"], results["reconciler"], results["refused"]
    outages = (
        "; ".join(f"{o['duration_s'] / 60:.1f} min ({o['reason']})" for o in results["outages"]) or "none"
    )
    tiers = "; ".join(f"{t['mode']} {t['from']}→{t['to']}" for t in results["tier_changes"])
    lines += [
        "",
        (
            "Times are seconds from the request to the moment every module's lights (the simulator's "
            "`device_view`) show the mode's expected rows. Changes made while a fault was active are left out of "
            "the percentiles and listed below."
        ),
        "",
        "## Details",
        (
            f"- Changes measured by source: {', '.join(f'{k} {n}' for k, n in sorted(by_source.items()))}; "
            f"superseded before settling (button mash): {results['abandoned']}; kiosk taps refused (cooldown): "
            f"{refused.get('kiosk', 0)} scheduled, {refused.get('kiosk_mash', 0)} during the mash; kiosk taps "
            f"refused while resting: {refused['during_faults']}."
        ),
        f"- Changes during a fault (not in the percentiles): {len(faulted)}.",
        _drift_line(results),
        *([_ripple_line(results["ripple"])] if results.get("ripple") else []),
        *([_stepped_line(results["stepped"])] if results.get("stepped") else []),
        f"- Outages the engine detected (resting → recovered): {outages}.",
        (
            f"- Tier changes: {len(results['tier_changes'])}{' — ' + tiers if tiers else ''}. "
            f"Final look: {results['final_mode']} at {results['final_tier']}."
        ),
        (
            f"- Reconciler: {rec['reasserts']} module re-asserts, {results['early_reasserts']} early re-asserts "
            f"after a verification readback differed, {rec.get('drift_resends', 0)} modules re-sent after a true-up "
            f"read showed they'd changed, {rec['true_ups']} full true-ups, {rec['probes']} outage probes, "
            f"{rec['slot_rewrites']} slot rewrites."
        ),
        (
            f"- Simulator: {sim['writes']} writes, {sim['reads']} reads, {sim['http_429']} × 429, "
            f"{sim['http_500']} × 500, {sim['connection_failures']} connection failures, "
            f"{sim['silent_drops']} silent drops, {sim['http_400']} × 400."
        ),
        "",
        (
            "Re-run with `make soak`. The acceptance conditions live in `config/sim/acceptance.yaml` and need "
            "Alon's sign-off to change."
        ),
        "",
    ]
    return "\n".join(lines)


def _drift_line(results: dict) -> str:
    load = results.get("read_load") or {}
    return (
        f"- Out-of-band changes (`SimDriver.faults.change_out_of_band`, i.e. the Hubspace app): "
        f"{results.get('drift_attempted', 0)} made, {len(results.get('drift_heals') or [])} measured, "
        f"{results.get('drift_skipped', 0)} skipped (the canopy wasn't settled, or a look change superseded it), "
        f"{results.get('drift_timeouts', 0)} never healed. Found by the rolling true-up: each module read once every "
        f"{load.get('true_up_interval_s', 0):g} s, spread across the modules — {load.get('true_up_reads', 0)} reads "
        f"in the run = {load.get('per_s', 0):.2f} GET/s on the canopy account (nominal "
        f"{load.get('nominal_per_s', 0):.2f}/s; reads skip a module whose command is still verifying and pause "
        f"while resting). All reads {load.get('all_reads', 0)}, all writes {load.get('all_writes', 0)}; reads are "
        "never counted against the 1 write/s per device budget."
    )


def _ripple_line(ripple: dict) -> str:
    s = ripple["settings"]
    return (
        f"- Ripple scenario (D64, SIMULATED): a separate run of the show's own wiring with `kiosk.ripple_stagger_s` "
        f"{ripple['stagger_s']:g} (the show's setting stays {load_settings(CONFIG_DIR).kiosk.ripple_stagger_s:g} "
        f"until Alon flips it), {s['changes']} kiosk mood changes about {s['spacing_s']:g} s apart under the same "
        f"pinned conditions: {ripple['changes']} landed ring by ring (up to {ripple['rings_max']} rings), "
        f"{ripple['bursts']} went out as one burst (a motion plan or one ring), {ripple['refused']} refused, "
        f"{ripple['abandoned']} superseded, {ripple['unsettled']} never settled; {ripple['early']} commands reached "
        "a module before its ring was due."
    )


def _technique_words(mode_id: str, t: dict) -> str:
    """One candidate's rung as the report says it: travel by its cycle (D65's text), a breath by its kind, swing and
    lag (D75)."""
    timing = f"{t['step_s']:g} s steps designed, {t['run_s']:g} s runs"
    kind = t.get("mode", "travel")
    if kind == "travel":
        whose = "its own (config/modes.yaml)" if t["own"] else "a SIMULATED one"
        return f"{mode_id}: {whose}, {t['steps_per_cycle']} steps a cycle, {timing}"
    whose = f"its own {kind} (config/modes.yaml)" if t["own"] else f"a SIMULATED {kind}"
    params = f"swing {t.get('brightness_swing', 0)}, lag {t.get('lag_steps', 0)}"
    return f"{mode_id}: {whose} ({params}), {t['steps_per_cycle']} steps a breath, {timing}"


def _stepped_line(stepped: dict) -> str:
    tiers = ", ".join(f"{k} {n}" for k, n in sorted(stepped["tiers"].items())) or "none"
    ended = "; ".join(f"{why} {n}" for why, n in sorted(stepped["ended"].items())) or "none"
    settled = ", ".join(f"{k} {n}" for k, n in sorted((stepped.get("settled") or {}).items())) or "none"
    steps = sorted(set(stepped["step_s"]))
    used = "; ".join(_technique_words(m, t) for m, t in stepped["techniques"].items())
    idle = stepped.get("idle_return") or {}
    ran = " (it started a run)" if idle.get("ran") else ""
    return (
        f"- Stepped scenario (D65, D75, SIMULATED): a separate run of the show's own wiring with a stepped rung on "
        f"{', '.join(stepped['modes'])} in the soak's config copy, a mode's own where config/modes.yaml puts it and "
        f"a SIMULATED one just before `modules` otherwise ({used}; `motion.stepped.live_enabled` stays false, so "
        f"live hardware never steps), under the same pinned conditions. {stepped['requests']} kiosk choices, "
        f"{stepped['refused']} refused; by tier: {tiers}; {stepped['runs']} runs at "
        f"{', '.join(f'{x:g}' for x in steps) or '—'} s a step ({stepped['not_stepping']} held still instead), "
        f"{stepped['interrupted']} cut short by another mood chosen mid-run (never the idle look); how runs ended: "
        f"{ended}; the look each finished run settled on, by tier: {settled}; the idle return showed the idle look "
        f"still{ran}. Steps: {stepped['steps_landed']} landed, {stepped['steps_cut']} cut by a newer mood, "
        f"{stepped['steps_superseded']} superseded, {stepped['steps_failed']} failed, {stepped['steps_skipped']} "
        f"skipped; {stepped['missing']} changed modules a step didn't send. The 60 s account maximum counts every "
        f"write the simulator received on that account (steps, re-sends, retries) against the "
        f"{stepped['cap']:g}/min cap. Targets live in `config/sim/soak.yaml` until Alon signs them off "
        "(`config/sim/acceptance.yaml` is pinned)."
    )


def _motion_line(results: dict) -> str:
    wiring = results.get("wiring") or {}
    motion = wiring.get("motion")
    tiers = ", ".join(f"{k} {n}" for k, n in sorted((wiring.get("tiers") or {}).items())) or "none"
    # D75: a guest's choice of a mode with a rung runs it first, then settles onto its canvas or slot
    settles = wiring.get("settles", 0)
    settled = f"; {settles} stepped runs then settled onto their canvas or slot (D75)" if settles else ""
    if not motion:
        return f"- Motion: no motion code enabled; accepted changes by tier: {tiers}{settled}."
    return (
        f"- Motion: **SIMULATED** — `config/sim/soak.yaml` enables motionEffect {motion['motionEffect']} for "
        f"{motion['mode']} (soak only; on real hardware code 1 has only bounded bench runs on Kingston, one "
        "physically observed on Oct 2, and `effects.enabled` stays empty; the simulator doesn't animate). "
        f"{wiring.get('motion_plans', 0)} accepted changes and run-end settles installed a motion plan (all-or-none "
        f"re-trigger). Accepted changes by tier: {tiers}{settled}."
    )


def load_targets(path: Path = ACCEPTANCE_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("targets") or {}


def run_soak(cfg: SoakConfig | None = None, compiler: Any = None) -> tuple[dict, list[dict], dict]:
    """Run the soak on a fresh VirtualTimeLoop. Returns (results, verdicts, meta) and writes the report."""
    cfg = cfg or SoakConfig()
    wall = RealClock()
    started = wall.monotonic()
    description = "the compiler passed to run_soak (the bare engine: every tier allowed, no overlay)"
    results = run_virtual(lambda: _soak(cfg, compiler))
    if results["wiring"]["show_wiring"]:
        description = SHOW_DESCRIPTION
        if int(cfg.ripple.get("changes", 0)) > 0:
            results["ripple"] = run_virtual(lambda: _ripple_soak(cfg))
        if int(cfg.stepped.get("runs", 0)) > 0:
            results["stepped"] = run_virtual(lambda: _stepped_soak(cfg))
    verdicts = evaluate(results, cfg.targets or load_targets())
    meta = {
        "generated": datetime.fromtimestamp(wall.now(), UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "wall_s": wall.monotonic() - started,
        "seed": cfg.seed,
        "compiler": description,
        "sim_settings": cfg.sim_settings,
        "acceptance_file": ACCEPTANCE_FILE.relative_to(ROOT).as_posix()
        if ACCEPTANCE_FILE.is_relative_to(ROOT)
        else str(ACCEPTANCE_FILE),
    }
    if cfg.report_path is not None:
        cfg.report_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.report_path.write_text(render_report(results, verdicts, meta), encoding="utf-8")
    return results, verdicts, meta


def main(argv: list[str] | None = None, *, compiler_factory: Callable[[], Any] | None = None) -> int:
    parser = argparse.ArgumentParser(description="8 simulated hours of the canopy engine (docs/reports).")
    parser.add_argument(
        "--hours", type=float, default=None, help="simulated hours (default: acceptance.yaml)"
    )
    parser.add_argument("--changes", type=int, default=None, help="mode changes (default: acceptance.yaml)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    args = parser.parse_args(argv)
    with open(ACCEPTANCE_FILE, encoding="utf-8") as f:
        soak_cfg = (yaml.safe_load(f) or {}).get("soak") or {}
    cfg = SoakConfig(
        hours=args.hours if args.hours is not None else float(soak_cfg.get("simulated_hours", 8)),
        changes=args.changes if args.changes is not None else int(soak_cfg.get("mode_changes", 100)),
        seed=args.seed,
        report_path=args.report,
    )
    _results, verdicts, meta = run_soak(cfg, compiler_factory() if compiler_factory else None)
    width = max(len(v["name"]) for v in verdicts)
    for v in verdicts:
        numbers = " ".join(f"{k}={_fmt(v.get(k))}" for k in ("p50", "p95", "max") if v.get(k) is not None)
        print(f"{_result(v)}  {v['name']:<{width}}  {numbers}  (target {v['target']})")
    print(f"Report: {args.report} — {meta['wall_s']:.1f} s wall, compiler: {meta['compiler']}")
    return 0 if passed(verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
