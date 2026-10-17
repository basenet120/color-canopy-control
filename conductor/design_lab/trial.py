"""The Lab's try-on (D66 Slice B, docs/design-lab/PLAN.md §6.4): a draft of one scene on the real lights, at module
level, through the canopy engine — never past it.

- `build_trial(lab, draft, …)` patches the show's OWN trio (deep copies: `wiring.modes_file` is the very object the
  ladder, every T1 compile, `t1_target` and `stale_slot` read, and `load_scenes` is the guest panel's cached catalog;
  purity rule, §3.0) and wraps the patched modes and recipes in an engine `Trial`. The engine shows it with
  `start_trial` (source "lab", one burst); the show compiles only that scene's `modules`/`strand` rungs from it.
- `confirm_sentence` names the driven modules and the bound; `cost_line` says what one apply sends (one write per
  module now, plus the engine's re-assert and up to `EARLY_REASSERT_LIMIT` early re-sends per module).
- `ApplyGate`: the server-side minimum gap between accepted applies (`kiosk.cooldown_s`): "lab" bypasses the kiosk
  cooldown like ops, so the Lab keeps its own. `reserve()` runs on the loop thread before any await, so a
  double-tapped second POST is refused even while the first is still being built.
- `trial_json(lab)`: TrialJSON (§4), from the engine's trial and the INSTALLED plan (its tier is never assumed).

Nothing here writes to a device or touches the executor, a driver or a lock: every write is the engine's.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

from conductor.canopy import EARLY_REASSERT_LIMIT, Trial
from conductor.config import trial_bound_s
from conductor.design_lab.draft import ConfigTrio, SceneDraft, apply_draft
from conductor.engine_api import PlanError, Tier
from conductor.looks import load_modes
from conductor.scenes import load_scenes

if TYPE_CHECKING:
    from conductor.design_lab.service import LabService

GATE_KEY = "design_lab:apply_gate"  # services.extras: the ApplyGate (one per process)
PAINTED_SUFFIX = "The painted design isn't on the lights."
PAINTED_MESSAGE = "Trying on at module level: one colour per strand; the painted design isn't on the lights."
MODULES_MESSAGE = "Trying on at module level."
STRAND_MESSAGE = "Trying on as one colour on every strand (module level couldn't be compiled: {why})."
NO_ENGINE = "The canopy engine isn't running here, so nothing can be tried on the lights."
GAP_MESSAGE = "One apply every {gap:g} s: the lights are still taking the last one. Apply again in {left} s."
REASSERT_WINDOW_S = (
    10  # what the cost line's "over the next 10 s" covers: the re-assert and the early re-sends
)


# ---- the trial -------------------------------------------------------------------------------------------------------
def show_trio(lab: LabService) -> ConfigTrio:
    """The trio the running show compiles from, deep-copied (never the show's own objects)."""
    config_dir = Path(lab.services.config_dir)
    wiring = lab.show()
    if wiring is not None:
        modes = wiring.modes_file
        recipes = wiring.context().recipes  # what the show compiles with (git, cached)
    else:
        from conductor.config import load_recipes

        modes, recipes = load_modes(config_dir), load_recipes(config_dir)
    return ConfigTrio(
        scenes=load_scenes(config_dir).model_copy(deep=True),
        modes=modes.model_copy(deep=True),
        recipes=recipes.model_copy(deep=True),
    )


def build_trial(
    lab: LabService, draft: SceneDraft, *, version: str | None, now_mono: float, now: float
) -> Trial:
    """The engine's Trial for `draft`: its patched modes and recipes, bounded by `trial_bound_s`. Raises DraftError
    (422) when the draft doesn't apply. CPU work: call it in the threadpool."""
    patched = apply_draft(show_trio(lab), draft, canopy=lab.canopy, settings=lab.settings)
    bound = trial_bound_s(lab.settings)
    return Trial(
        mode_id=draft.scene_id,
        label=draft.name,
        modes=patched.modes,
        recipes=patched.recipes,
        started_mono=now_mono,
        ends_mono=now_mono + bound,
        started_at=now,
        version=version,
    )


def painted(lab: LabService, scene_id: str) -> bool:
    """Has the scene a painted rung (a slot)? Its try-on shows module level only, never the painted design."""
    return lab.approved.modes.mode(scene_id).slot is not None


def driven_modules(lab: LabService) -> list[Any]:
    """The canopy modules a try-on writes to: those with a device registered on this runtime (D62), registry order."""
    wiring = lab.show()
    if wiring is None:
        return []
    addresses = wiring.context().addresses or {}
    return [e for e in lab.services.registry.canopy_modules() if addresses.get(e.id)]


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def confirm_sentence(lab: LabService, draft: SceneDraft, modules: list[Any], bound_s: float) -> str:
    """What ops confirms before a try-on: the draft, the scene, the tier, every module written to and the bound."""
    scene = lab.approved.scenes.get(draft.scene_id).name
    labels = ", ".join((m.label or m.id) for m in modules) or "none"
    sentence = (
        f"Try on {draft.name} for {scene} at module level on {_count(len(modules), 'module', 'modules')} "
        f"({labels}) for up to {bound_s:g} s."
    )
    return f"{sentence} {PAINTED_SUFFIX}" if painted(lab, draft.scene_id) else sentence


def cost_line(n: int) -> str:
    """What one apply sends: one write per module now; with the re-assert and up to EARLY_REASSERT_LIMIT early
    re-sends per module, up to n × (1 + EARLY_REASSERT_LIMIT) over the next 10 s."""
    return (
        f"sends {_count(n, 'write', 'writes')} now, up to ~{n * (1 + EARLY_REASSERT_LIMIT)} over the next "
        f"{REASSERT_WINDOW_S} s"
    )


# ---- the apply gap ---------------------------------------------------------------------------------------------------
class ApplyGate:
    """The minimum gap between accepted applies (`kiosk.cooldown_s`). Used on the loop thread only."""

    def __init__(self, gap_s: float) -> None:
        self.gap_s = float(gap_s)
        self._last: float | None = None
        self._before: float | None = None

    def remaining(self, now_mono: float) -> float:
        if self._last is None:
            return 0.0
        return round(max(0.0, self._last + self.gap_s - now_mono), 2)

    def reserve(self, now_mono: float) -> float:
        """Seconds left (> 0: refuse), or 0 having stamped `now_mono`: call before any await."""
        left = self.remaining(now_mono)
        if left > 0:
            return left
        self._before, self._last = self._last, now_mono
        return 0.0

    def release(self) -> None:
        """Undo the last stamp (validation or the engine refused that apply, so it never went out)."""
        self._last, self._before = self._before, None


def apply_gate(lab: LabService) -> ApplyGate:
    gate = lab.services.extras.get(GATE_KEY)
    if gate is None:
        gate = lab.services.extras[GATE_KEY] = ApplyGate(lab.settings.kiosk.cooldown_s)
    return gate


def gap_sentence(gate: ApplyGate, left: float) -> str:
    return GAP_MESSAGE.format(gap=gate.gap_s, left=math.ceil(left))


# ---- TrialJSON -------------------------------------------------------------------------------------------------------
def trial_json(lab: LabService) -> dict | None:
    """TrialJSON (§4), or None when no try-on runs. `tier` is the installed plan's, never assumed."""
    engine = lab.engine()
    trial = getattr(engine, "trial", None) if engine is not None else None
    if trial is None:
        return None
    clock = lab.services.clock
    plan = engine.current_plan()
    mine = plan if plan is not None and plan.mode_id == trial.mode_id else None
    modules = list(mine.modules) if mine is not None else []
    registry = lab.services.registry
    positions = []
    for device_id in modules:
        try:
            positions.append(registry.get(device_id).module or device_id)
        except (KeyError, LookupError):
            positions.append(device_id)
    tier = str(mine.tier) if mine is not None else None
    if mine is not None and mine.tier is Tier.STRAND:
        message = STRAND_MESSAGE.format(why=_modules_why(lab, trial.mode_id))
    elif painted(lab, trial.mode_id):
        message = PAINTED_MESSAGE
    else:
        message = MODULES_MESSAGE
    bound = trial.ends_mono - trial.started_mono
    now_mono = clock.monotonic()
    return {
        "scene_id": trial.mode_id,
        "label": mine.label if mine is not None else trial.label,
        "version": trial.version,
        "draft_name": trial.label,
        "started_at": trial.started_at,
        "ends_at": trial.started_at + bound,
        "left_s": round(max(0.0, trial.ends_mono - now_mono), 2),
        "bound_s": round(bound, 3),
        "modules": modules,
        "positions": positions,
        "applies": trial.applies,
        "tier": tier,
        "message": message,
        "cost": cost_line(len(modules)),
        "next_apply_in_s": apply_gate(lab).remaining(now_mono),
    }


def _modules_why(lab: LabService, scene_id: str) -> str:
    """Why the try-on's module-level rung didn't compile (the show compiles it from the trial, as it did)."""
    wiring = lab.show()
    if wiring is None:
        return "the canopy engine isn't running"
    try:
        wiring.compile(scene_id, Tier.T3)
    except (PlanError, KeyError, LookupError, ValueError) as e:
        return str(e).strip("'\"") or type(e).__name__
    return "it wasn't allowed or available when the try-on was applied"
