"""The Lab's stepped-motion editor and ripple preview (Slice C, PLAN §7; D64, D65). Screen only: nothing here writes.

- `effective_step_s(design_step_s, n_modules, settings)` — the seconds a step really takes: `ProgramRunner`'s rule
  (`conductor/sequence.py` `_effective_step`) for one account. It is a copy, because the runner is a show-path file
  this slice doesn't edit; `tests/test_lab_stepped.py` pins it equal to a real runner on the engine for nine modules
  and for the Mac's two kits, at the designed and at edited step lengths.
- `stepped_preview(patched, scene_id, canopy, settings, today_positions)` — the EXACT steps the compiler gives the
  draft (`compile_mode(scene, Tier.STEPPED, <the design context>)`, the same nine stand-in modules as every other
  tier's sentence), the timing, the real pacing (`pace.nine`, `pace.today`), the schema-checked moving copy and the
  live gate's sentence. None for a scene without a stepped rung (the idle look never has one: the show refuses it).
- `ripple_rings(patched, scene_id, canopy)` — the rings a new mood would land in (D64): `field.bands` of the mode's
  design (`compile.design_field` at module level) over the module centres, as the compiler orders a plan's `waves`.
- `ripple_json(...)` — those rings plus the stagger slider's range and a true sentence about the live ripple.

The Lab shows `motion.stepped.live_enabled` and `kiosk.ripple_stagger_s`; it never changes either (no route, no
setting, no draft field touches them).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from conductor.config import RIPPLE_MAX_WAVES, RIPPLE_SPAN_MAX_S, ConductorSettings
from conductor.design_lab.draft import GRID, STEP_S_INCREMENT, ConfigTrio
from conductor.engine_api import ModePlan, PlanError, Tier
from conductor.looks import compile_mode
from conductor.looks import field as F
from conductor.looks.compile import design_field
from conductor.sequence import planned_writes_per_min
from conductor.show import STEPPED_LIVE_OFF

RUN_S_MAX = 180.0  # SteppedTechnique.run_s's own cap; the Lab's is min(this, kiosk.idle_return_s)
RIPPLE_STAGGER_MAX_S = RIPPLE_SPAN_MAX_S / (
    RIPPLE_MAX_WAVES - 1
)  # 1.2 s: the slider's top (config.py's bound)
_EPS = 1e-6  # = sequence._EPS

STEPPED_LIVE_NOTE = "Alon sets motion.stepped.live_enabled after Gate R"
STEPPED_LIVE_ON = "Stepped looks are on for live hardware (motion.stepped.live_enabled)."
RIPPLE_OFF_NOTE = (
    "Screen preview only — the canopy's ripple is off (kiosk.ripple_stagger_s: 0) until Alon signs off (D64)"
)
RIPPLE_ON_NOTE = (
    "Screen preview only — the canopy's ripple is set to {stagger:g} s (kiosk.ripple_stagger_s); the Lab never "
    "changes it (D64)"
)
EFFECTS_OFF = "Effects — not enabled: no code has passed the bench (06 #4)"
TRIAL_NEVER_STEPS = "A try-on never steps: the lights hold the still module-level look."


def run_s_max(settings: ConductorSettings) -> float:
    """The longest run the Lab offers: SteppedTechnique's 180 s, and never past the idle return (`_check_stepped`)."""
    return min(RUN_S_MAX, float(settings.kiosk.idle_return_s))


def _ripple_span_s(settings: ConductorSettings, rings: int) -> float:
    """The ripple's span a mood choice lands with: (rings − 1) × stagger, 0 without a ripple (`CanopyEngine._rings`
    is None for no stagger or a single ring)."""
    stagger = float(settings.kiosk.ripple_stagger_s)
    if stagger <= 0 or rings < 2:
        return 0.0
    return (rings - 1) * stagger


def effective_step_s(
    design_step_s: float, n_modules: int, settings: ConductorSettings, *, rings: int = 1
) -> float:
    """`ProgramRunner._effective_step`'s step for `n_modules` on one account: max(the design's step_s, n × 60 / the
    planned rate (the account cap less the reserve), the ripple's span + min_step_s), rounded up to whole ms. With
    no ripple (`kiosk.ripple_stagger_s: 0`, the shipped value) the last term is min_step_s."""
    s = settings.motion.stepped
    planned = planned_writes_per_min(s)
    span = _ripple_span_s(settings, rings)
    step = max(float(design_step_s), n_modules * 60.0 / planned, span + s.min_step_s)
    return math.ceil(step * 1000 - _EPS) / 1000


def run_pace(
    design_step_s: float, run_s: float, n_modules: int, settings: ConductorSettings, *, rings: int = 1
) -> dict:
    """One pace: the step, the step number the hold goes out at (`ProgramRunner.count_for`), and why the look would
    hold still instead of stepping: the runner's own two set-up reasons (a throttle cool-off is runtime only), and a
    run that fits only step 0, which IS the still design (the mode change sends it as the hold, and the runner sends
    steps 1 … count − 1 only), so no colour would ever change."""
    s = settings.motion.stepped
    step = effective_step_s(design_step_s, n_modules, settings, rings=rings)
    hold_at = int(run_s / step + _EPS)
    still = None
    if step > s.max_step_s + _EPS:
        planned = planned_writes_per_min(s)
        still = (
            f"stepping {n_modules} modules on one account at {planned:g} writes a minute needs {step:g} s steps, "
            f"longer than motion.stepped.max_step_s ({s.max_step_s:g} s)"
        )
    elif hold_at < 1:
        still = f"its run ({run_s:g} s) is shorter than one {step:g} s step"
    elif hold_at < 2:
        still = (
            f"its run ({run_s:g} s) fits only the first step, which is the still design ({step:g} s steps), so no "
            "colour would change"
        )
    return {
        "modules": n_modules,
        "step_s": step,
        "hold_at": hold_at if still is None else 0,
        "moves_s": round(hold_at * step, 3) if still is None else 0.0,
        "still": still,
    }


def ripple_rings(
    patched: ConfigTrio, scene_id: str, canopy: dict, positions: Sequence[str] = GRID
) -> list[list[str]] | None:
    """The canopy positions in the rings a new look of this scene lands in (D64): `field.bands` of the mode's design
    at module level (`compile.design_field`, its geometry only) over `positions`, at most RIPPLE_MAX_WAVES rings,
    the ring the design starts from first. None when the design has no direction (one burst)."""
    spec = design_field(patched.modes.mode(scene_id), Tier.T3)
    if spec is None:
        return None
    try:
        return F.bands(spec, canopy, list(positions), RIPPLE_MAX_WAVES)
    except (ValueError, KeyError):
        return None


def _stepped_rings(patched: ConfigTrio, scene_id: str, canopy: dict, positions: Sequence[str]) -> int:
    """How many rings the runner's ripple term counts for a stepped plan over `positions` (its `waves` are compiled
    at the plan's own tier, Tier.STEPPED)."""
    spec = design_field(patched.modes.mode(scene_id), Tier.STEPPED)
    if spec is None or not positions:
        return 1
    try:
        return len(F.bands(spec, canopy, list(positions), RIPPLE_MAX_WAVES))
    except (ValueError, KeyError):
        return 1


def canopy_pace(
    patched: ConfigTrio,
    scene_id: str,
    canopy: dict,
    settings: ConductorSettings,
    positions: Sequence[str] = GRID,
) -> dict | None:
    """`run_pace` of the scene's stepped rung driven on `positions` (one account, the ripple a mood choice would
    land with over them); None without a stepped rung or with no position driven."""
    tech = patched.modes.mode(scene_id).techniques.stepped
    if tech is None or not positions:
        return None
    rings = _stepped_rings(patched, scene_id, canopy, positions)
    return run_pace(tech.step_s, tech.run_s, len(positions), settings, rings=rings)


def steps_when(patched: ConfigTrio, scene_id: str) -> str | None:
    """When a scene whose stepped rung sits under higher rungs (Golden Hour: canvas, slot) actually steps: only where
    none of those can run (D65: a downgrade passes over the stepped rung). None when stepped is the ladder's first."""
    from conductor.design_lab.preview import TIERS  # (preview imports this module)

    mode = patched.modes.mode(scene_id)
    ladder = [str(t) for t in mode.ladder]
    if "stepped" not in ladder or ladder.index("stepped") == 0:
        return None
    above = ", ".join(TIERS[t][0] if t in TIERS else t for t in ladder[: ladder.index("stepped")])
    return (
        f"{mode.label} steps only where its higher rungs ({above}) can't run; where one of them runs, that look "
        "shows instead and nothing steps"
    )


def ripple_note(settings: ConductorSettings) -> str:
    """The ripple preview's label, true of the canopy's own setting (off unless Alon has set it, D64)."""
    live = float(settings.kiosk.ripple_stagger_s)
    return RIPPLE_OFF_NOTE if live <= 0 else RIPPLE_ON_NOTE.format(stagger=live)


def ripple_json(patched: ConfigTrio, scene_id: str, canopy: dict, settings: ConductorSettings) -> dict:
    """The ripple preview's data: the rings, the stagger slider's range (0 to 1.2 s) and the live value, said truly."""
    return {
        "rings": ripple_rings(patched, scene_id, canopy),
        "stagger_s": [0.0, RIPPLE_STAGGER_MAX_S],
        "max_rings": RIPPLE_MAX_WAVES,
        "live_stagger_s": float(settings.kiosk.ripple_stagger_s),
        "note": ripple_note(settings),
    }


def _compile(patched: ConfigTrio, scene_id: str, canopy: dict, **kw: Any) -> ModePlan:
    from conductor.design_lab.preview import _design_context  # the design context every tier compiles with

    return compile_mode(scene_id, Tier.STEPPED, _design_context(patched, canopy, **kw))


def stepped_preview(
    patched: ConfigTrio,
    scene_id: str,
    canopy: dict,
    settings: ConductorSettings,
    today_positions: Sequence[str],
) -> dict | None:
    """SteppedPreviewJSON for a scene with a stepped rung, else None. `steps` are the compiled program's steps exactly
    (step 0 is the still design, the hold); `pace.nine` / `pace.today` the seconds a step really takes on the full
    canopy and on `today_positions` (the show box's driven kits); `moving_feature` the schema-checked moving copy
    the full canopy would show while a run moves (`today_moving_feature`: the partial canopy's, D62)."""
    mode = patched.modes.mode(scene_id)
    tech = mode.techniques.stepped
    if tech is None or "stepped" not in mode.ladder:
        return None
    live = bool(settings.motion.stepped.live_enabled)
    out: dict[str, Any] = {
        "design_step_s": tech.step_s,
        "steps_per_cycle": tech.steps_per_cycle,
        "run_s": tech.run_s,
        "direction": tech.direction,
        "steps": [],
        "feature": None,
        "moving_feature": None,
        "today_moving_feature": None,
        "pace": None,
        "runs": None,
        "live_enabled": live,
        "live_sentence": None if live else STEPPED_LIVE_OFF,
        "live_note": STEPPED_LIVE_ON if live else STEPPED_LIVE_NOTE,
        "trial_note": TRIAL_NEVER_STEPS,
        "steps_when": steps_when(patched, scene_id),
        "error": None,
    }
    try:
        plan = _compile(patched, scene_id, canopy)
    except PlanError as exc:
        out["error"] = f"{mode.label} can't step: {exc}"
        return out
    assert plan.program is not None
    out["steps"] = [{"index": s.index, "colours": dict(s.colours)} for s in plan.program.steps]
    out["feature"] = plan.feature
    out["moving_feature"] = plan.program.moving_feature
    today = [p for p in GRID if p in set(today_positions)]
    if today:
        try:
            partial = _compile(
                patched,
                scene_id,
                canopy,
                addresses={f"design-{p}": (f"design-{p}" if p in today else None) for p in GRID},
            )
            out["today_moving_feature"] = partial.program.moving_feature if partial.program else None
        except PlanError:
            pass
    nine = canopy_pace(patched, scene_id, canopy, settings, GRID)
    here = canopy_pace(patched, scene_id, canopy, settings, today)
    assert nine is not None
    out["runs"] = {"nine": nine, "today": here}
    out["pace"] = {
        "nine": nine["step_s"],
        "today": here["step_s"] if here is not None else None,
        "today_modules": len(today),
        "today_positions": today,
        "planned_writes_per_min": planned_writes_per_min(settings.motion.stepped),
        "account_writes_per_min": settings.motion.stepped.account_writes_per_min,
    }
    return out


__all__ = [
    "EFFECTS_OFF",
    "RIPPLE_OFF_NOTE",
    "RIPPLE_STAGGER_MAX_S",
    "RUN_S_MAX",
    "STEPPED_LIVE_NOTE",
    "STEP_S_INCREMENT",
    "canopy_pace",
    "effective_step_s",
    "ripple_json",
    "ripple_note",
    "ripple_rings",
    "run_pace",
    "run_s_max",
    "stepped_preview",
    "steps_when",
]
