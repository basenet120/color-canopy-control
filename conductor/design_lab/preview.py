"""The Lab's preview: a draft through the real compiler, every tier, and what the lights can show today (PLAN §4).

Zero writes: it reads config and (read-only) the running show's registry, capabilities, flags and addresses.

- `preview(lab, draft)` → PreviewJSON: the patched mode's design (`routes/design.py` `mode_design`, heads ×270 and
  modules ×9), each tier's sentence (`tiers_json`, `compile_mode`), `today` (`today_view`), the pinned show-box
  view, the D63 warnings, the resolved copy, the draft's changes and the T1 lock. Raises DraftError (422) or
  T1Locked (409).
- `t1_lock(lab, scene_id)` (§3.8): after commissioning, a painted scene's design is read-only on live hardware.

Owners: `design_registry`, `design_caps`, `T1_LOCKED` and `T1Locked` are the contracts commit (L); the bodies marked
`S1` are stream S1's (C1 later adds `stepped`/`rings`).
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from conductor.capabilities import Capabilities, detect
from conductor.config import DeviceEntry
from conductor.design_lab.draft import GRID, ConfigTrio, SceneDraft
from conductor.sim.profiles import profile_parts

if TYPE_CHECKING:
    from conductor.design_lab.service import LabService

T1_LOCKED = "changing this needs a slot repaint at commissioning"
DESIGN_PROFILE = "sqhdk03-sim"


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


def preview(lab: LabService, draft: SceneDraft) -> dict:
    """PreviewJSON (§4). Raises DraftError / T1Locked. An explicit "approved" copy that no longer holds is resolved
    as "auto" here and says why (only a save refuses it)."""
    raise NotImplementedError("S1: preview")


def tiers_json(patched: ConfigTrio, scene_id: str, canopy: dict) -> list[dict]:
    """Per ladder rung: {tier, label, draws, feature[, note, moving_feature, solid]} from compile_mode over the
    design registry (the slot rung's sentence is techniques.slot.feature)."""
    raise NotImplementedError("S1: tiers_json")


def today_view(lab: LabService, patched: ConfigTrio, scene_id: str) -> dict:
    """TodayJSON: what this runtime's lights would show (the running show's registry, flags, addresses, D62
    left-outs; the draft's recipes via dataclasses.replace on the show's CompileContext)."""
    raise NotImplementedError("S1: today_view")


def t1_lock(lab: LabService, scene_id: str) -> dict:
    """{"locked", "simulated", "slot", "positions", "sentence"} (§3.8). Enforced only on a non-simulated driver."""
    raise NotImplementedError("S1: t1_lock")


def check_t1_lock(lab: LabService, draft: SceneDraft) -> None:
    """Raise T1Locked when `t1_lock` is on for the draft's scene and the draft changes its painted design (palette
    hexes/names, layout, sample_at); brightness, solid, copy and stepped timing stay editable. Used by preview and
    by the save route."""
    raise NotImplementedError("S1: check_t1_lock")
