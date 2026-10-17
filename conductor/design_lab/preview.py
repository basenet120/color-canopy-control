"""The Lab's preview: a draft through the real compiler, every tier, and what the lights can show today (PLAN §4).

Zero writes: it reads config and (read-only) the running show's registry, capabilities, flags and addresses.

- `preview(lab, draft)` → PreviewJSON: the patched mode's design (`routes/design.py` `mode_design`, heads ×270 and
  modules ×9), each tier's sentence (`tiers_json`, `compile_mode`), `today` (`today_view`), the pinned show-box
  view, the D63 warnings, the resolved copy, the draft's changes and the T1 lock. Raises DraftError (422) or
  T1Locked (409).
- `t1_lock(lab, scene_id)` (§3.8): after commissioning, a painted scene's design is read-only on live hardware.

Truth rules:
- Every colour comes from the real compiler over the draft's OWN patched modes and recipes (never the git recipes,
  never the Studio's `SCENES`): a hex edit shows the new colour at every tier, a renamed colour still compiles.
- "Today" walks the patched ladder the way the running show would (`ShowWiring.tier_allowed`, the show's registry,
  capabilities, flags, addresses and D62 left-outs) and lights only what that tier lights. At a painted tier the
  slots hold the APPROVED design, so its heads are compiled from the approved trio and say so. While a try-on of
  the scene runs, "today" is the plan the engine installed for it (module level or strand), with TRYING_ON_NOTE.
- Brightness is a level (`brightness`, `lit.level`), never multiplied into a colour (D59).

Owners: `design_registry`, `design_caps`, `T1_LOCKED` and `T1Locked` are the contracts commit (L); the bodies are
stream S1's (C1 later adds `stepped`/`rings`).
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from conductor.canopy_view import module_colours
from conductor.capabilities import Capabilities, detect
from conductor.config import DeviceEntry, DeviceFlags
from conductor.design_lab import draft as D
from conductor.design_lab.draft import GRID, ConfigTrio, SceneDraft
from conductor.engine_api import ModePlan, PlanError, Tier
from conductor.looks import CompileContext, compile_mode, ladder, scene_rules
from conductor.looks import field as F
from conductor.routes.design import mode_design
from conductor.show import STEPPED_LIVE_OFF
from conductor.sim.profiles import profile_parts

if TYPE_CHECKING:
    from conductor.design_lab.service import LabService

T1_LOCKED = "changing this needs a slot repaint at commissioning"
DESIGN_PROFILE = "sqhdk03-sim"
TIERS: dict[str, tuple[str, str]] = {  # tier → (label, what the Lab draws)
    "canvas": ("Painted (all 270 lights)", "heads"),
    "slot": ("Painted in the app", "heads"),
    "stepped": ("Module level, stepped", "modules"),
    "modules": ("Module level (one colour per strand)", "modules"),
    "strand": ("Strand (one colour)", "solid"),
}
SLOT_NOTE = "hand-painted in the Hubspace app to match; only after it is recorded"
SIM_TITLE = "What this simulator shows"
LIVE_TITLE = "What the lights can show today"
SHOW_BOX_TITLE = "Show box today (Modules 1–2, module level)"
NO_ENGINE = "The canopy engine isn't running here."
HEADS_APPROVED = (
    "The slots hold the approved painted design; your draft's painted design reaches the lights only after export, "
    "merge and commissioning."
)
SHOW_BOX_NOTE = (
    "Until the Oct 19 undo the Mac show box drives VWBD's two test kits as Modules 1–2 only (D62), with no painted "
    "slot commissioned: nothing painted."
)
# The stepped clause comes from the gate itself, so the note never says stepped is off while the view steps.
SHOW_BOX_STEPPED_ON = "Stepped looks are enabled on live hardware (motion.stepped.live_enabled)."
TRYING_ON_NOTE = (
    "A try-on of this scene is on the lights now, {how}: this is the draft that was applied, as the engine installed "
    "it. Edits made since reach the lights only when applied."
)
TRYING_ON_HOW = {Tier.T3: "at module level", Tier.STRAND: "as one colour on every strand"}
TRYING_ON_PAINTED = "The painted design isn't on the lights."
SIM_LOCK_SENTENCE = (
    "In the simulator the slots are commissioned at start-up; on the show box this scene's painted design is "
    "read-only once commissioned."
)


class T1Locked(Exception):
    """A design edit to a painted scene after commissioning on live hardware: a 409 `{detail, field}`."""

    def __init__(self, detail: str, field: str) -> None:
        self.detail = detail
        self.field = field  # "palette" | "layout"
        super().__init__(detail)


def design_registry() -> list[DeviceEntry]:
    """Nine stand-in canopy modules, one per position, for compiling the full canopy's sentences."""
    return [
        DeviceEntry(id=f"design-{pos}", role="canopy_module", account="design", module=pos, expected_heads=30)
        for pos in GRID
    ]


@lru_cache(maxsize=1)
def design_caps() -> Capabilities:
    """The simulated SQHDK03's capabilities (every design module has them)."""
    return detect(*profile_parts(DESIGN_PROFILE))


def _design_context(patched: ConfigTrio, canopy: dict, **kw: Any) -> CompileContext:
    registry = design_registry()
    caps = design_caps()
    return CompileContext(
        registry=registry,
        capabilities={d.id: caps for d in registry},
        modes=patched.modes,
        recipes=patched.recipes,
        canopy=canopy,
        **kw,
    )


# ---- tiers -----------------------------------------------------------------------------------------------------------
def tiers_json(patched: ConfigTrio, scene_id: str, canopy: dict) -> list[dict]:
    """Per ladder rung: {tier, label, draws, feature[, note, moving_feature, solid]} from compile_mode over the
    design registry (the slot rung's sentence is techniques.slot.feature)."""
    mode = patched.modes.mode(scene_id)
    ctx = _design_context(patched, canopy)
    out = []
    for name in mode.ladder:
        label, draws = TIERS[name]
        item: dict[str, Any] = {"tier": name, "label": label, "draws": draws}
        if name == "slot":
            assert mode.techniques.slot is not None
            # T2 compiles only with a hash recorded as painted in the app: its sentence is the technique's own
            item["feature"] = mode.techniques.slot.feature
            item["note"] = SLOT_NOTE
        else:
            try:
                plan = compile_mode(scene_id, Tier(name), ctx)
            except PlanError as exc:
                item.update(feature=None, error=str(exc))
                out.append(item)
                continue
            item["feature"] = plan.feature
            if name == "stepped":
                item["moving_feature"] = plan.program.moving_feature if plan.program is not None else None
        if name == "strand" and mode.techniques.strand is not None:
            rgb = mode.techniques.strand.look.rgb
            item["solid"] = list(rgb) if rgb is not None else None
        out.append(item)
    return out


# ---- what the lights show ----------------------------------------------------------------------------------------------
def _first_plan(
    scene_id: str, modes: Any, ctx: CompileContext, allowed: Callable[[Tier], tuple[bool, str]]
) -> tuple[ModePlan | None, list[str]]:
    """The first rung of the patched ladder this runtime allows and that compiles (floors ignored: a temporary
    downgrade isn't "today")."""
    reasons: list[str] = []
    for tier in ladder(scene_id, modes):
        ok, why = allowed(tier)
        if not ok:
            reasons.append(f"{tier}: {why}")
            continue
        try:
            return compile_mode(scene_id, tier, ctx), reasons
        except PlanError as exc:
            reasons.append(f"{tier}: {exc}")
    return None, reasons


def _lit(canopy: Mapping[str, Any], devices: Sequence[DeviceEntry], plan: ModePlan, level: int) -> dict:
    """`lit_state`'s module shape (canopy_view.py) for a plan: every driven module lit in the plan's full-strength
    head colours (the guest panel's own per-head expansion), every other position dark."""
    by_position = {d.module: d for d in devices}
    modules = []
    for pos in GRID:
        entry = by_position.get(pos)
        target = plan.modules.get(entry.id) if entry is not None else None
        item: dict[str, Any] = {"pos": pos, "state": "dark", "color": None, "colors": None}
        if target is not None and entry is not None:
            if not target.colors:  # a T2 slot whose content isn't known: no colour claim
                item["state"] = "unknown"
            else:
                hexes = module_colours(canopy, entry, target.colors)
                item["state"] = "lit"
                if len(set(hexes)) == 1:
                    item["color"] = hexes[0]
                else:
                    item["colors"] = hexes
        modules.append(item)
    return {"level": level, "modules": modules}


def _positions(devices: Sequence[DeviceEntry], plan: ModePlan) -> dict:
    driven = {d.module for d in devices if d.id in plan.modules}
    return {"driven": [p for p in GRID if p in driven], "left_out": [p for p in GRID if p not in driven]}


def _view(
    title: str,
    plan: ModePlan | None,
    *,
    lit: dict | None = None,
    heads: str | None = None,
    positions: dict | None = None,
    note: str = "",
) -> dict:
    tier = str(plan.tier) if plan is not None else None
    view: dict[str, Any] = {
        "title": title,
        "tier": tier,
        "label": TIERS[tier][0] if tier is not None else None,
        "feature": plan.feature if plan is not None else None,
        "lit": lit,
        "heads": heads,
        "positions": positions,
        "note": note,
    }
    if plan is not None and plan.program is not None:
        view["moving_feature"] = plan.program.moving_feature
    return view


def _left_out_note(plan: ModePlan) -> str:
    if not plan.skipped:
        return ""
    return f"{len(plan.skipped)} module(s) left out of this look: " + "; ".join(plan.skipped.values())


def today_view(lab: LabService, patched: ConfigTrio, scene_id: str) -> dict:
    """TodayJSON: what this runtime's lights would show (the running show's registry, flags, addresses, D62
    left-outs; the draft's recipes via dataclasses.replace on the show's CompileContext)."""
    title = SIM_TITLE if lab.simulated else LIVE_TITLE
    wiring = lab.show()
    if wiring is None:
        return _view(title, None, note=NO_ENGINE)
    # ShowWiring.context passes no recipes, so without the replace the draft's T3 colours would come from git's
    # recipes: a hex edit would show the approved colour, and a renamed colour would fail modules and drop to strand.
    ctx = dataclasses.replace(wiring.context(modes=patched.modes), recipes=patched.recipes)
    trying = _trial_view(lab, wiring, title, scene_id, patched)
    if trying is not None:
        return trying
    plan, reasons = _first_plan(
        scene_id, patched.modes, ctx, lambda tier: wiring.tier_allowed(scene_id, tier)
    )
    if plan is None:
        return _view(title, None, note="No rung of this scene can run here now: " + "; ".join(reasons))
    level = patched.modes.mode(scene_id).techniques.modules.sample.brightness  # type: ignore[union-attr]
    devices = ctx.devices
    positions = _positions(devices, plan)
    if plan.tier in (Tier.T1, Tier.T2):
        # The slots hold the approved painted design: the heads are the approved trio's, never the draft's.
        actx = dataclasses.replace(wiring.context(modes=lab.approved.modes), recipes=lab.approved.recipes)
        try:
            shown = compile_mode(scene_id, plan.tier, actx)
        except PlanError as exc:
            return _view(title, plan, heads="approved", positions=positions, note=f"{HEADS_APPROVED} ({exc})")
        return _view(
            title,
            shown,
            lit=_lit(ctx.canopy or lab.canopy, devices, shown, level),
            heads="approved",
            positions=positions,
            note=HEADS_APPROVED,
        )
    return _view(
        title,
        plan,
        lit=_lit(ctx.canopy or lab.canopy, devices, plan, level),
        heads="draft",
        positions=positions,
        note=_left_out_note(plan),
    )


def _trial_view(lab: LabService, wiring: Any, title: str, scene_id: str, patched: ConfigTrio) -> dict | None:
    """While a try-on of `scene_id` runs, "today" is the plan the engine installed for it (D66 Slice B: module level
    or strand, compiled from the applied draft), never a painted rung the lights aren't showing."""
    engine = lab.engine()
    trial = getattr(engine, "trial", None) if engine is not None else None
    if trial is None or trial.mode_id != scene_id:
        return None
    plan = engine.current_plan()
    if plan is None or plan.mode_id != scene_id:
        return None
    ctx = wiring.context()
    modes = trial.modes if trial.modes is not None else patched.modes
    level = modes.mode(scene_id).techniques.modules.sample.brightness  # type: ignore[union-attr]
    painted = lab.approved.modes.mode(scene_id).slot is not None
    note = " ".join(
        n
        for n in (
            TRYING_ON_NOTE.format(how=TRYING_ON_HOW.get(plan.tier, f"at the {plan.tier} tier")),
            TRYING_ON_PAINTED if painted else "",
            _left_out_note(plan),
        )
        if n
    )
    return _view(
        title,
        plan,
        lit=_lit(ctx.canopy or lab.canopy, ctx.devices, plan, level),
        heads="draft",
        positions=_positions(ctx.devices, plan),
        note=note,
    )


def show_box_today(lab: LabService, patched: ConfigTrio, scene_id: str) -> dict:
    """The Mac show box until the Oct 19 undo (pinned; delete after it): only r0c0 and r0c1 have a device (D62), no
    bench gate is proven (so nothing painted), stepped is refused as live unless Alon has enabled it."""
    registry = design_registry()
    live_kits = set(scene_rules.LIVE_KITS)
    ctx = _design_context(
        patched,
        lab.canopy,
        flags={d.id: DeviceFlags() for d in registry},
        addresses={d.id: (f"show-box-{d.module}" if d.module in live_kits else None) for d in registry},
    )
    live_stepped = bool(lab.settings.motion.stepped.live_enabled)

    def allowed(tier: Tier) -> tuple[bool, str]:
        if tier is Tier.STEPPED and not live_stepped:
            return False, STEPPED_LIVE_OFF
        return True, ""

    plan, reasons = _first_plan(scene_id, patched.modes, ctx, allowed)
    if plan is None:
        return _view(SHOW_BOX_TITLE, None, note="No rung of this scene can run there: " + "; ".join(reasons))
    level = patched.modes.mode(scene_id).techniques.modules.sample.brightness  # type: ignore[union-attr]
    note = SHOW_BOX_NOTE
    if Tier.STEPPED in ladder(scene_id, patched.modes):
        note += " " + (
            SHOW_BOX_STEPPED_ON if live_stepped else STEPPED_LIVE_OFF[0].upper() + STEPPED_LIVE_OFF[1:] + "."
        )
    return _view(
        SHOW_BOX_TITLE,
        plan,
        lit=_lit(lab.canopy, registry, plan, level),
        heads="draft",
        positions=_positions(registry, plan),
        note=note,
    )


# ---- the T1 lock (§3.8) ------------------------------------------------------------------------------------------------
def t1_lock(lab: LabService, scene_id: str) -> dict:
    """{"locked", "simulated", "slot", "positions", "sentence"} (§3.8). Enforced only on a non-simulated driver."""
    mode = lab.approved.modes.mode(scene_id)
    slot = mode.slot
    simulated = lab.simulated
    if slot is None:
        return {"locked": False, "simulated": simulated, "slot": None, "positions": [], "sentence": None}
    if simulated:
        return {
            "locked": False,
            "simulated": True,
            "slot": slot,
            "positions": [],
            "sentence": SIM_LOCK_SENTENCE,
        }
    engine = lab.engine()
    positions = []
    for entry in lab.services.registry.canopy_modules():
        # ShowWiring._record's two lines (it is private): the engine's view when it runs, else the registry's
        record = engine.recorded_slot(entry.id, slot) if engine is not None else entry.slot_record(slot)
        if record is not None and record.get("origin") == "api":
            positions.append(entry.module or entry.id)
    locked = bool(positions)
    name = lab.approved.scenes.get(scene_id).name
    return {
        "locked": locked,
        "simulated": False,
        "slot": slot,
        "positions": positions,
        "sentence": f"{name}: {T1_LOCKED}." if locked else None,
    }


def check_t1_lock(lab: LabService, draft: SceneDraft) -> None:
    """Raise T1Locked when `t1_lock` is on for the draft's scene and the draft changes its painted frames (the
    palette's hexes, their order and count, the layout, sample_at: `D.painted_change`, the same predicate as
    `needs_slot_repaint`); colour names, brightness, solid, copy and stepped timing stay editable (a rename keeps the
    slot hash, so it needs no repaint). Used by preview and by the save route."""
    if draft.scene_id not in lab.approved.modes.modes:
        return  # an unknown scene is apply_drafts' problem (field scene_id)
    lock = t1_lock(lab, draft.scene_id)
    if not lock["locked"]:
        return
    changed = D.painted_change(D.seed_draft(lab.approved, draft.scene_id), draft)
    if changed is not None:
        raise T1Locked(lock["sentence"], changed)


# ---- the preview ---------------------------------------------------------------------------------------------------------
def _unknown(scene_id: str) -> D.DraftError:
    return D.DraftError(
        [D.Problem(f"the draft of {scene_id}", "scene_id", f"there is no scene called {scene_id!r}")]
    )


def preview(lab: LabService, draft: SceneDraft) -> dict:
    """PreviewJSON (§4). Raises DraftError / T1Locked. An explicit "approved" copy that no longer holds is resolved
    as "auto" here and says why (only a save refuses it)."""
    started = time.perf_counter()
    approved, canopy, scene_id = lab.approved, lab.canopy, draft.scene_id
    if scene_id not in approved.modes.modes:
        raise _unknown(scene_id)
    check_t1_lock(lab, draft)
    copy = D.resolve_copy(approved, draft, canopy)
    relaxed = {rung: "auto" for rung in ("modules", "stepped") if (c := copy[rung]) is not None and c.refused}
    applied = draft.model_copy(update={"copy": draft.copy.model_copy(update=relaxed)}) if relaxed else draft
    patched = D.apply_draft(approved, applied, canopy=canopy, settings=lab.settings)
    mode = patched.modes.mode(scene_id)
    ctx = CompileContext(
        registry=[], capabilities={}, modes=patched.modes, recipes=patched.recipes, canopy=canopy
    )
    try:
        design: dict = mode_design(scene_id, ctx)
    except PlanError as exc:
        design = {"mode": scene_id, "error": f"The design of {mode.label} can't be drawn: {exc}"}
    warnings = [
        {"rule": p.rule, "field": D.problem_of(p).field, "message": p.message}
        for p in scene_rules.scene_mode_problems(
            patched.scenes,
            patched.modes,
            patched.recipes,
            canopy,
            approved=approved.modes,
            rules=scene_rules.WARNINGS,
        )
        if p.mode == scene_id
    ]
    stepped = copy["stepped"]
    answer = {
        "scene_id": scene_id,
        "label": mode.label,
        "approved": D.is_approved(approved, draft),
        "design": design,
        "brightness": draft.brightness,
        "layout_words": F.layout_words(draft.layout, canopy),  # the "Reads as:" line (S4), edited or not
        "tiers": tiers_json(patched, scene_id, canopy),
        "today": today_view(lab, patched, scene_id),
        "show_box_today": show_box_today(lab, patched, scene_id),
        "warnings": warnings,
        "copy": {
            "modules": copy["modules"].to_json() if copy["modules"] is not None else None,
            "stepped": stepped.to_json() if stepped is not None else None,
        },
        "changes": D.draft_changes(approved, draft, canopy=canopy),
        # notes on the free client copy (never a refusal): a description the colours or layout moved under
        "copy_notes": D.description_notes(D.seed_draft(approved, scene_id), draft),
        "t1_lock": t1_lock(lab, scene_id),
        "stepped": None,  # Slice C (§7)
        "rings": None,  # Slice C (§7)
    }
    answer["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return answer


__all__ = [
    "DESIGN_PROFILE",
    "LIVE_TITLE",
    "SHOW_BOX_TITLE",
    "SIM_TITLE",
    "T1_LOCKED",
    "TIERS",
    "T1Locked",
    "check_t1_lock",
    "design_caps",
    "design_registry",
    "preview",
    "show_box_today",
    "t1_lock",
    "tiers_json",
    "today_view",
]
