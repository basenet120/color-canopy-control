"""Export: a saved version as a reviewed git patch of four files, with a drafted DECISIONS entry (PLAN §3.6).

The edited texts are rewritten in place (comments and anchors kept, as `config.overlay_diff`), re-read through the
real loaders and compared with the patched trio; any mismatch sets `problem` and offers no patch. The JSON fixture
(`tests/fixtures_scene_frames.json`, frames + `_pins`) is built in memory, so an export from a runtime without a
`tests/` directory (Docker/staging) is still complete.

Owners: `EXPORT_FILES` and `LabExport` are the contracts commit (L); the bodies marked `S3` are stream S3's.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from conductor.design_lab.draft import ConfigTrio
from conductor.design_lab.store import Version
from conductor.scenes import SceneCatalog

EXPORT_FILES = (
    "config/scenes.yaml",
    "config/modes.yaml",
    "config/recipes.yaml",
    "tests/fixtures_scene_frames.json",
)


@dataclass
class LabExport:
    patch: str  # unified diff, "a/<path>" / "b/<path>" headers, git-apply-able ("" = nothing changed)
    files: list[str]  # which of EXPORT_FILES changed
    scenes: list[str]  # scene ids rewritten
    decision: str  # the drafted DECISIONS entry (markdown)
    approved_changes: list[str]  # D59 sentences ("Golden Hour: Sunset pink #FF9BC4 → #FF8FBF")
    layout_changes: list[str]  # D63 sentences
    review_notes: list[str]  # e.g. comments kept in a rewritten block that may now be stale
    problem: str = ""  # set when the edited files didn't re-read as the patched trio: then patch == ""


def export_patch(
    approved: ConfigTrio,
    patched: ConfigTrio,
    *,
    root: Path,
    canopy: dict,
    version: Version | None,
    now: float,
) -> LabExport:
    raise NotImplementedError("S3: export_patch")


def rewrite_scenes_yaml(text: str, patched: ConfigTrio, scene_ids: list[str]) -> str:
    raise NotImplementedError("S3: rewrite_scenes_yaml")


def rewrite_modes_yaml(
    text: str, approved: ConfigTrio, patched: ConfigTrio, scene_ids: list[str]
) -> tuple[str, list[str]]:
    raise NotImplementedError("S3: rewrite_modes_yaml")


def rewrite_recipes_yaml(text: str, approved: ConfigTrio, patched: ConfigTrio) -> str:
    raise NotImplementedError("S3: rewrite_recipes_yaml")


def frames_fixture(scenes: SceneCatalog, pins: dict) -> str:
    """json.dumps({**{s.id: [list(c) for c in s.frame(30)] for s in scenes}, "_pins": pins}, indent=0), no trailing
    newline."""
    raise NotImplementedError("S3: frames_fixture")


def reread(texts: Mapping[str, str], canopy: dict) -> ConfigTrio:
    """The edited texts through the real loaders (SceneCatalog / RecipesFile / parse_modes)."""
    raise NotImplementedError("S3: reread")
