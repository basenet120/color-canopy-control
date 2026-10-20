"""The Design Lab (D66, docs/design-lab/PLAN.md): edit the scenes on screen, save named versions, compare them,
present them, and export the approved result as a reviewed git patch.

Slice A is screen only: nothing here writes to a device, flips `motion.stepped.live_enabled` or
`kiosk.ripple_stagger_s`, takes a slot or binds port 8000. The show never reads the Lab's store.

- `draft`   — SceneDraft, ConfigTrio, the patch (`apply_drafts`), recipe keys, copy (S1)
- `preview` — the real-compiler preview, tiers, today, the T1 lock (S1); its `preview()` is not re-exported here,
  so `conductor.design_lab.preview` stays the module
- `store`   — saved versions in `<data>/overrides/looks.yaml` (S2)
- `export`  — the git patch of the four files (S3)
- `service` — LabService and `lab_of`, the per-process state (L)
- `conductor/looks/scene_rules.py` — the shared scene ↔ mode validator (S3)
- `conductor/routes/design_lab.py` — `/api/lab/*` (L)
"""

from conductor.design_lab.draft import (
    ConfigTrio,
    DraftColour,
    DraftCopy,
    DraftError,
    DraftStepped,
    Problem,
    SceneDraft,
    apply_draft,
    apply_drafts,
    draft_changes,
    is_approved,
    load_trio,
    seed_draft,
)
from conductor.design_lab.export import EXPORT_FILES, LabExport, export_patch
from conductor.design_lab.preview import T1_LOCKED, T1Locked, t1_lock
from conductor.design_lab.service import KEY, LabService, lab_of
from conductor.design_lab.store import LooksFile, LookStore, StoreReadOnly, Version, config_sha, default_name

__all__ = [
    "EXPORT_FILES",
    "KEY",
    "T1_LOCKED",
    "ConfigTrio",
    "DraftColour",
    "DraftCopy",
    "DraftError",
    "DraftStepped",
    "LabExport",
    "LabService",
    "LookStore",
    "LooksFile",
    "Problem",
    "SceneDraft",
    "StoreReadOnly",
    "T1Locked",
    "Version",
    "apply_draft",
    "apply_drafts",
    "config_sha",
    "default_name",
    "draft_changes",
    "export_patch",
    "is_approved",
    "lab_of",
    "load_trio",
    "seed_draft",
    "t1_lock",
]
