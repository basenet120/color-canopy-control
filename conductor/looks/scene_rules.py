"""The scene ↔ mode invariants, as one validator the tests and the Design Lab share (PLAN §3.4).

Each rule is one of the old `tests/test_modes_match_scenes.py` invariants (and `tests/test_d65_modes.py`'s stepped
one), moved here so `/api/lab/preview` can run them on a patched trio. The old tests keep their assertions verbatim
and gain one line calling this validator alongside.

- BLOCKING rules refuse a draft (422).
- WARNINGS are D63 design-quality rules: a preview answers 200 with them, a save stores them, an export is refused
  (409) while any stands, because the suite would fail.

Owners: the constants, `SceneProblem` and `delta_e76` are the contracts commit (L); the bodies marked `S3` are stream
S3's. Until S3 lands, `scene_mode_problems` finds nothing and `scene_pins` is empty, so S1 can integrate.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from conductor.config import RecipesFile
from conductor.looks.schema import Mode, ModesFile
from conductor.scenes import SceneCatalog

BLOCKING = ("catalog", "brightness", "static", "ladder-end", "slots", "one-design", "recipes", "stepped")
WARNINGS = ("two-d", "kits-differ")  # D63 design-quality: a 200 preview with warnings; save OK; export 409
RULES = BLOCKING + WARNINGS
LIVE_KITS = ("r0c0", "r0c1")  # the Mac box's two shop kits (D62)
# The test that fails while a warning stands (named in the export's 409).
WARNING_TESTS: dict[str, str] = {
    "two-d": "test_no_scene_reads_as_three_columns_at_module_level",
    "kits-differ": "test_on_the_partial_live_canopy_the_two_kits_show_different_colours",
}


@dataclass(frozen=True)
class SceneProblem:
    rule: str
    mode: str
    path: str
    message: str

    def __str__(self) -> str:
        return f"mode {self.mode!r} → {self.path}: {self.message}"

    def to_json(self) -> dict:
        return {"rule": self.rule, "mode": self.mode, "path": self.path, "message": self.message}


def scene_mode_problems(
    scenes: SceneCatalog,
    modes: ModesFile,
    recipes: RecipesFile,
    canopy: Mapping[str, Any],
    *,
    approved: ModesFile | None = None,
    rules: Iterable[str] = RULES,
) -> list[SceneProblem]:
    """Every problem the named rules find, in catalog order. Stub until S3: finds nothing."""
    return []  # S3: the ten rules of §3.4


def module_colours(
    mode: Mode, recipes: RecipesFile, canopy: Mapping[str, Any]
) -> dict[str, tuple[int, int, int]]:
    """Position → T3 RGB via compile.module_look over DeviceEntry(id=f"rules-{pos}", role="canopy_module",
    account="rules", module=pos) — module_look needs no capabilities."""
    raise NotImplementedError("S3: module_colours")


def _lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """sRGB (D65) → CIELAB, for ΔE76: how far apart two module colours look."""

    def lin(c: int) -> float:
        v = c / 255
        return ((v + 0.055) / 1.055) ** 2.4 if v > 0.04045 else v / 12.92

    r, g, b = (lin(c) for c in rgb)
    xyz = (
        (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047,
        0.2126 * r + 0.7152 * g + 0.0722 * b,
        (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883,
    )
    fx, fy, fz = (v ** (1 / 3) if v > 0.008856 else 7.787 * v + 16 / 116 for v in xyz)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e76(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """CIE76 ΔE between two sRGB colours (moved from tests/test_modes_match_scenes.py's _lab/_delta_e)."""
    return math.dist(_lab(a), _lab(b))


def scene_pins(
    scenes: SceneCatalog, modes: ModesFile, recipes: RecipesFile, canopy: Mapping[str, Any]
) -> dict:
    """The approved values the tests pin (§3.7: layout, layout_words, sample_at, solid, brightness, t3, and for
    stepped scenes stepped + flow_words), per scene. Stub until S3: empty."""
    return {}  # S3: §3.7
