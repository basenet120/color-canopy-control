"""The scene ↔ mode invariants, as one validator the tests and the Design Lab share (PLAN §3.4).

Each rule is one of the old `tests/test_modes_match_scenes.py` invariants (and `tests/test_d65_modes.py`'s stepped
one), moved here so `/api/lab/preview` can run them on a patched trio. The old tests keep their assertions verbatim
and gain one line calling this validator alongside.

- BLOCKING rules refuse a draft (422).
- WARNINGS are D63 design-quality rules: a preview answers 200 with them, a save stores them, an export is refused
  (409) while any stands, because the suite would fail.

| rule        | invariant                                                                                            |
|-------------|------------------------------------------------------------------------------------------------------|
| catalog     | mode ids == scene ids in order; label == name; blurb == mood; preview (upper) == palette hexes;     |
|             | the client copy (name, mood, description) never claims motion or a count of lights (`copy_problem`) |
| brightness  | canvas / slot / modules / stepped / strand brightness == the scene's `brightness_draft`              |
| static      | no motion_effect / motion_feature, slot speed 0, `motion: false`, the design's `cycles == 1`;       |
|             | a module-level stepped rung through the cloud is allowed on every scene (D75)                        |
| ladder-end  | the ladder ends [modules, strand]; the strand is the scene's solid in RGB, no fallback, copy ends on |
|             | it; a stepped rung sits just before [modules, strand] (D75)                                          |
| slots       | a canvas technique ⇔ a slot, each slot once; with `approved`: same ids, ladders and slots           |
| one-design  | one `field` canvas (rgb_only, at: centre), stops at i/(n−1) naming the palette, shared by every rung |
| recipes     | each design colour's recipe is its hex at 100 with no white, blend == rgb_only, one key per colour   |
| stepped     | the hold is module level; travel: steps_per_cycle distinct frames; breathe/shimmer: every step       |
|             | changes something (colours or brightness); > 2 colours a step; copy names its flow or breath + pace |
| two-d       | (warning) 4–9 module colours, no uniform rows or columns, columns change ≥ a third of rows           |
| kits-differ | (warning) the Mac box's two kits (r0c0, r0c1) show different colours                                 |

Pure: no I/O. Module-level colours come from the real compiler (`compile.module_look`, the step sampler).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from conductor.config import DeviceEntry, RecipesFile
from conductor.looks import field as F
from conductor.looks.schema import (
    MOTION_WORDS,
    STEPPED_PACE,
    FieldCanvas,
    Mode,
    ModesFile,
    ModuleSample,
    _stepped_problems,
    breathe_words,
    motion_words,
)
from conductor.scenes import Scene, SceneCatalog

BLOCKING = ("catalog", "brightness", "static", "ladder-end", "slots", "one-design", "recipes", "stepped")
WARNINGS = ("two-d", "kits-differ")  # D63 design-quality: a 200 preview with warnings; save OK; export 409
RULES = BLOCKING + WARNINGS
LIVE_KITS = ("r0c0", "r0c1")  # the Mac box's two shop kits (D62)
# The test that fails while a warning stands (named in the export's 409).
WARNING_TESTS: dict[str, str] = {
    "two-d": "test_no_scene_reads_as_three_columns_at_module_level",
    "kits-differ": "test_on_the_partial_live_canopy_the_two_kits_show_different_colours",
}
STRIPES = "Reads as stripes at module level (D63)"
SAME_KITS = "Modules 1 and 2 would show the same colour"
_DESIGN = "techniques → modules → sample → canvas"
RGB = tuple[int, int, int]
# The client copy (a scene's name, mood and description) shows on the guest panel at every tier, on a partial canopy
# too, and `compile._counted` never rewrites it (it counts only the rung sentences, D62): so it may not claim motion,
# and it may not count the canopy's lights or strands.
COUNT_CLAIMS = re.compile(r"\b(270|nine)\b", re.IGNORECASE)
CLIENT_COPY = ("name", "mood", "description")


def copy_problem(what: str, text: str) -> str | None:
    """Why a scene's client copy (`what`: name, mood or description) can't say `text`, or None. Shared by the
    Design Lab's draft model and the catalog rule, so a hand edit of scenes.yaml is held to the same words."""
    if m := MOTION_WORDS.search(text):
        return f"the {what} can't say {m.group(0)!r}: it shows at every tier, also when nothing moves"
    if m := COUNT_CLAIMS.search(text):
        return (
            f"the {what} can't say {m.group(0)!r}: it shows on a partial canopy too, where that count isn't true "
            "and nothing rewrites it (D62)"
        )
    return None


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


# ---- helpers -------------------------------------------------------------------------------------------------------
def _positions(canopy: Mapping[str, Any]) -> list[str]:
    grid = canopy["orientations"][canopy["orientation_scheme"]]
    return [f"r{r}c{c}" for r, row in enumerate(grid) for c in range(len(row))]


def _rgb(hex_: str) -> RGB:
    return int(hex_[1:3], 16), int(hex_[3:5], 16), int(hex_[5:7], 16)


def _design(mode: Mode) -> FieldCanvas | None:
    """The scene's one design: its module-level sample canvas, when it is a `field` canvas."""
    mods = mode.techniques.modules
    if mods is None or mods.sample is None or not isinstance(mods.sample.canvas, FieldCanvas):
        return None
    return mods.sample.canvas


def _context(recipes: RecipesFile, canopy: Mapping[str, Any]) -> Any:
    from conductor.looks.compile import (
        CompileContext,
    )  # compile imports schema; keep this module light to load

    positions = _positions(canopy)
    entries = [
        DeviceEntry(id=f"rules-{pos}", role="canopy_module", account="rules", module=pos) for pos in positions
    ]
    # `modes` is given (unvalidated and empty) so the context never reads config/ from disk: module_look takes the
    # mode itself and needs only the canopy and the recipes.
    return entries, CompileContext(
        registry=entries,
        capabilities={},
        modes=ModesFile.model_construct(kiosk=None, modes={}),
        recipes=recipes,
        canopy=dict(canopy),
    )


def module_colours(
    mode: Mode, recipes: RecipesFile, canopy: Mapping[str, Any]
) -> dict[str, tuple[int, int, int]]:
    """Position → T3 RGB via compile.module_look over DeviceEntry(id=f"rules-{pos}", role="canopy_module",
    account="rules", module=pos) — module_look needs no capabilities."""
    from conductor.looks.compile import module_look

    entries, ctx = _context(recipes, canopy)
    out: dict[str, tuple[int, int, int]] = {}
    for entry in entries:
        look = module_look(mode, entry, ctx)
        if look.rgb is None:
            raise ValueError(f"module level at {entry.module} isn't an RGB colour")
        out[str(entry.module)] = tuple(look.rgb)  # type: ignore[assignment]
    return out


def step_frames(mode: Mode, recipes: RecipesFile, canopy: Mapping[str, Any]) -> list[dict[str, RGB]]:
    """Each step of the mode's stepped rung as position → RGB, sampled as the compiler's stepped tier does. Travel
    keeps its own phase formula (the stepped canvas at each module centre, shifted by the step's phase: an
    independent cross-check of the compiler); breathe/shimmer (D75) are `compile.step_looks`' colours over all nine.
    [] for a mode that doesn't step."""
    from conductor.looks.compile import _sampled_rgb

    tech = mode.techniques.stepped
    if tech is None:
        return []
    if tech.mode != "travel":
        return [{pos: rgb for pos, (rgb, _) in step.items()} for step in step_pictures(mode, recipes, canopy)]
    entries, ctx = _context(recipes, canopy)
    sample = ModuleSample(canvas=tech.canvas, at="centre", brightness=tech.brightness)
    sign = -1.0 if tech.direction == "forward" else 1.0  # = compile._stepped
    return [
        {str(e.module): _sampled_rgb(sample, e, ctx, phase=sign * n / tech.steps_per_cycle) for e in entries}
        for n in range(tech.steps_per_cycle)
    ]


def step_pictures(
    mode: Mode, recipes: RecipesFile, canopy: Mapping[str, Any]
) -> list[dict[str, tuple[RGB, int]]]:
    """Each step of the mode's stepped rung as position → (RGB, brightness), from `compile.step_looks` over all nine
    positions (lags counted over the nine), for every kind of motion: what a step changes is its colours, its
    levels, or both (D75). [] for a mode that doesn't step."""
    from conductor.looks.compile import step_looks

    tech = mode.techniques.stepped
    if tech is None:
        return []
    entries, ctx = _context(recipes, canopy)
    out: list[dict[str, tuple[RGB, int]]] = []
    for step in step_looks(tech, entries, ctx):
        picture: dict[str, tuple[RGB, int]] = {}
        for pos, look in step.items():
            assert look.rgb is not None and look.brightness is not None
            picture[pos] = (tuple(look.rgb), int(look.brightness))  # type: ignore[assignment]
        out.append(picture)
    return out


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


# ---- the rules -----------------------------------------------------------------------------------------------------
class _Found:
    def __init__(self) -> None:
        self.items: list[SceneProblem] = []

    def add(self, rule: str, mode: str, path: str, message: str) -> None:
        self.items.append(SceneProblem(rule, mode, path, message))


def _catalog(scenes: SceneCatalog, modes: ModesFile, out: _Found) -> None:
    ids = [s.id for s in scenes.scenes]
    if list(modes.modes) != ids:
        out.add(
            "catalog",
            "",
            "modes",
            f"the modes must be the scenes, in order: modes.yaml has {', '.join(modes.modes)}; scenes.yaml has "
            f"{', '.join(ids)}",
        )
    for scene in scenes.scenes:
        mode = modes.modes.get(scene.id)
        if mode is None:
            continue
        if mode.label != scene.name:
            out.add(
                "catalog",
                scene.id,
                "label",
                f"the label {mode.label!r} isn't the scene's name {scene.name!r}",
            )
        if mode.blurb != scene.mood:
            out.add(
                "catalog",
                scene.id,
                "blurb",
                f"the blurb {mode.blurb!r} isn't the scene's mood {scene.mood!r}",
            )
        for what, path in (("name", "label"), ("mood", "blurb"), ("description", "description")):
            why = copy_problem(what, getattr(scene, what))
            if why is not None:
                out.add("catalog", scene.id, path, why)
        preview = [c.upper() for c in mode.preview]
        palette = [c.hex for c in scene.palette]
        if preview != palette:
            out.add(
                "catalog",
                scene.id,
                "preview",
                f"the preview {preview} isn't the scene's palette {palette}, in order",
            )


def _brightness(scene: Scene, mode: Mode, out: _Found) -> None:
    t = mode.techniques
    levels: list[tuple[str, int]] = []
    if t.canvas is not None:
        levels.append(("techniques → canvas → brightness", t.canvas.brightness))
    if t.slot is not None:
        levels.append(("techniques → slot → brightness", t.slot.brightness))
    if t.modules is not None and t.modules.sample is not None:
        levels.append(("techniques → modules → sample → brightness", t.modules.sample.brightness))
    if t.stepped is not None:
        levels.append(("techniques → stepped → brightness", t.stepped.brightness))
    if t.strand is not None:
        levels.append(("techniques → strand → look → brightness", t.strand.look.brightness))
    for path, level in levels:
        if level != scene.brightness_draft:
            out.add(
                "brightness",
                scene.id,
                path,
                f"brightness {level} isn't the scene's brightness {scene.brightness_draft} (every tier uses it)",
            )


def _static(scene: Scene, mode: Mode, out: _Found) -> None:
    t = mode.techniques
    if t.canvas is not None and (t.canvas.motion_effect is not None or t.canvas.motion_feature is not None):
        out.add(
            "static", scene.id, "techniques → canvas → motion_effect", "a scene's painted design never moves"
        )
    if t.slot is not None and t.slot.speed != 0:
        out.add(
            "static", scene.id, "techniques → slot → speed", f"speed {t.slot.speed}: a scene's slot is static"
        )
    if mode.motion:
        out.add("static", scene.id, "motion", "a scene's look never re-triggers as motion")
    designs = [(f"{_DESIGN} → field → cycles", _design(mode))]
    if t.canvas is not None and isinstance(t.canvas.canvas, FieldCanvas):
        designs.append(("techniques → canvas → canvas → field → cycles", t.canvas.canvas))
    for path, design in designs:
        if design is not None and design.field.cycles != 1:
            out.add(
                "static",
                scene.id,
                path,
                f"cycles {design.field.cycles}: a scene is one static design, no repeats (D63)",
            )
            break


def _ladder_end(scene: Scene, mode: Mode, out: _Found) -> None:
    if mode.ladder[-2:] != ["modules", "strand"]:
        out.add("ladder-end", scene.id, "ladder", f"the ladder {mode.ladder} must end [modules, strand]")
    if "stepped" in mode.ladder and mode.ladder.index("stepped") != len(mode.ladder) - 3:
        out.add(
            "ladder-end",
            scene.id,
            "ladder",
            f"the stepped rung in {mode.ladder} must sit just before [modules, strand] (D75: a run holds on the "
            "best still rung above it, or on modules)",
        )
    strand = mode.techniques.strand
    if strand is None:
        out.add(
            "ladder-end",
            scene.id,
            "techniques → strand",
            "every scene ends on its solid colour (a strand look)",
        )
        return
    solid = scene.solid
    if strand.look.kind != "rgb" or strand.look.rgb is None or tuple(strand.look.rgb) != solid.rgb:
        out.add(
            "ladder-end",
            scene.id,
            "techniques → strand → look",
            f"the strand look must be the solid colour {solid.name} {solid.hex} in RGB",
        )
    if strand.fallback_look is not None:
        out.add(
            "ladder-end", scene.id, "techniques → strand → fallback_look", "the solid colour has no fallback"
        )
    if not strand.feature.endswith(f"set to {solid.name.lower()}."):
        out.add(
            "ladder-end",
            scene.id,
            "techniques → strand → feature",
            f"the strand copy must end 'set to {solid.name.lower()}.'",
        )


def _slots(scenes: SceneCatalog, modes: ModesFile, approved: ModesFile | None, out: _Found) -> None:
    taken: dict[str, str] = {}
    for mode_id, mode in modes.modes.items():
        if (mode.techniques.canvas is not None) != (mode.slot is not None):
            out.add(
                "slots",
                mode_id,
                "slot",
                "a painted (canvas) scene owns exactly one slot, and only a painted scene has one",
            )
        if mode.slot is not None:
            if mode.slot in taken:
                out.add("slots", mode_id, "slot", f"{mode.slot} is already {taken[mode.slot]}'s")
            taken.setdefault(mode.slot, mode_id)
    if approved is None:
        return
    if list(modes.modes) != list(approved.modes):
        out.add(
            "slots", "", "modes", "the mode ids never change (the kiosk, the API and the slots key on them)"
        )
    for mode_id, mode in modes.modes.items():
        before = approved.modes.get(mode_id)
        if before is None:
            continue
        if mode.ladder != before.ladder:
            out.add(
                "slots", mode_id, "ladder", f"the ladder {mode.ladder} isn't the approved {before.ladder}"
            )
        if mode.slot != before.slot:
            out.add("slots", mode_id, "slot", f"slot {mode.slot} isn't the approved {before.slot}")


def _one_design(scene: Scene, mode: Mode, recipes: RecipesFile, out: _Found) -> None:
    mods = mode.techniques.modules
    if mods is None or mods.sample is None:
        out.add(
            "one-design", scene.id, "techniques → modules → sample", "module level samples the scene's design"
        )
        return
    design = mods.sample.canvas
    if not isinstance(design, FieldCanvas):
        out.add("one-design", scene.id, _DESIGN, "the scene's design is one `field` canvas (D63)")
        return
    if design.recipe != "rgb_only":
        out.add("one-design", scene.id, f"{_DESIGN} → recipe", "the design is painted with rgb_only recipes")
    if mods.sample.at != "centre":
        out.add(
            "one-design", scene.id, "techniques → modules → sample → at", "module level samples at: centre"
        )
    n = len(scene.palette)
    if [s.at for s in design.stops] != [i / (n - 1) for i in range(n)]:
        out.add(
            "one-design",
            scene.id,
            f"{_DESIGN} → stops",
            f"the stops must be the {n} palette colours evenly spaced (at i/{n - 1})",
        )
    hexes, names = [], []
    for i, stop in enumerate(design.stops):
        recipe = recipes.colors.get(stop.color)
        if recipe is None:
            out.add("one-design", scene.id, f"{_DESIGN} → stops → [{i}] → color", f"no recipe {stop.color!r}")
            return
        hexes.append(recipe.hex.upper())
        names.append(recipe.label)
    if hexes != [c.hex for c in scene.palette]:
        out.add(
            "one-design", scene.id, f"{_DESIGN} → stops", f"the stops' colours {hexes} aren't the palette's"
        )
    if names != [c.name for c in scene.palette]:
        out.add(
            "one-design",
            scene.id,
            f"{_DESIGN} → stops",
            f"the stops' recipe labels {names} aren't the palette's names",
        )
    t = mode.techniques
    if t.canvas is not None and t.canvas.canvas != design:
        out.add(
            "one-design",
            scene.id,
            "techniques → canvas → canvas",
            "the painted canvas is the same design as module level",
        )
    if t.stepped is not None and t.stepped.canvas != design:
        out.add(
            "one-design",
            scene.id,
            "techniques → stepped → canvas",
            "the stepped canvas is the same design as module level",
        )


def _recipes(scenes: SceneCatalog, modes: ModesFile, recipes: RecipesFile, out: _Found) -> None:
    users: dict[str, list[str]] = {}
    for scene in scenes.scenes:
        mode = modes.modes.get(scene.id)
        design = _design(mode) if mode is not None else None
        if design is None:
            continue
        for i, stop in enumerate(design.stops):
            users.setdefault(stop.color, []).append(scene.id)
            recipe = recipes.colors.get(stop.color)
            if recipe is None:
                continue  # one-design (and the schema's cross-check) say so
            path = f"{_DESIGN} → stops → [{i}] → color"
            r, g, b = _rgb(recipe.hex)
            if recipe.rgb_only.model_dump() != {
                "r": r,
                "g": g,
                "b": b,
                "cct": 0,
                "colorBrightness": 100,
                "whiteBrightness": 0,
            }:
                out.add(
                    "recipes",
                    scene.id,
                    path,
                    f"recipe {stop.color!r}: rgb_only must be its hex {recipe.hex} at full strength, no white",
                )
            if recipe.blend != recipe.rgb_only:
                out.add(
                    "recipes",
                    scene.id,
                    path,
                    f"recipe {stop.color!r}: blend must equal rgb_only (per-puck white is unverified, gate 6)",
                )
            if recipe.strand.rgb is None or tuple(recipe.strand.rgb) != (r, g, b):
                out.add("recipes", scene.id, path, f"recipe {stop.color!r}: strand must be rgb {[r, g, b]}")
    for key, owners in users.items():
        if len(owners) > 1:
            for scene_id in dict.fromkeys(owners):
                out.add(
                    "recipes",
                    scene_id,
                    _DESIGN + " → stops",
                    f"recipe {key!r} is used by {len(owners)} palette colours; every palette colour has its own",
                )


def _stepped(scene: Scene, mode: Mode, recipes: RecipesFile, canopy: Mapping[str, Any], out: _Found) -> None:
    tech = mode.techniques.stepped
    design = _design(mode)
    if tech is None or design is None:
        return
    if not all(s.color in recipes.colors for s in tech.canvas.stops):
        return
    frames = step_frames(mode, recipes, canopy)
    positions = _positions(canopy)
    if frames[0] != module_colours(mode, recipes, canopy):
        out.add(
            "stepped",
            scene.id,
            "techniques → stepped",
            "the first step must be the module-level look it holds",
        )
    pictures = [tuple(f[p] for p in positions) for f in frames]
    if tech.mode == "travel":
        if len(set(pictures)) != tech.steps_per_cycle:
            out.add(
                "stepped",
                scene.id,
                "techniques → stepped → steps_per_cycle",
                f"{tech.steps_per_cycle} steps give only {len(set(pictures))} different pictures of the design",
            )
    else:  # D75: a breath may come back to a picture, but no step may send nothing new
        full = step_pictures(mode, recipes, canopy)
        for n in range(len(full)):
            m = (n + 1) % len(full)
            if full[n] == full[m]:
                out.add(
                    "stepped",
                    scene.id,
                    "techniques → stepped → steps_per_cycle",
                    f"steps {n} and {m} show the same picture (colours and brightness); every step of a {tech.mode} "
                    "changes something — use more steps, a larger shift or a larger brightness_swing",
                )
                break
    flat = [n for n, p in enumerate(pictures) if len(set(p)) <= 2]
    if flat:
        out.add(
            "stepped",
            scene.id,
            f"{_DESIGN} → field",  # a flat step is the design's doing (its layout), not the run's timing
            f"step {flat[0]} shows {len(set(pictures[flat[0]]))} colour(s) across the canopy; each step shows more than two",
        )
    words = motion_words(tech.canvas, dict(canopy), tech.direction) if tech.mode == "travel" else None
    # a breath's words for what changes are the schema's (SteppedTechnique) to check
    moving = tech.moving_feature
    if words is not None and words not in moving:
        out.add(
            "stepped",
            scene.id,
            "techniques → stepped → moving_feature",
            f"the moving copy must name the flow {words!r}",
        )
    if tech.mode != "travel":
        # a breath names no flow but its own start, and a lagged one doesn't start every module together
        # (parse_modes' cross-check, here too: the rules stand on their own over a trio built without it, as the
        # Lab's and the tests' are); each problem keeps the field its path names (moving_feature, seed, lag_steps)
        for problem in _stepped_problems(scene.id, tech, dict(canopy)):
            where, _, message = problem.partition(": ")
            out.add("stepped", scene.id, where.removeprefix(f"mode {scene.id!r} → "), message)
    if STEPPED_PACE not in moving:
        out.add(
            "stepped",
            scene.id,
            "techniques → stepped → moving_feature",
            f"the moving copy must say the strands change {STEPPED_PACE!r}",
        )


def _two_d(scene: Scene, colours: dict[str, RGB], canopy: Mapping[str, Any], out: _Found) -> None:
    grid = canopy["orientations"][canopy["orientation_scheme"]]
    rows = [[f"r{r}c{c}" for c in range(len(grid[r]))] for r in range(len(grid))]
    cols = [[f"r{r}c{c}" for r in range(len(grid))] for c in range(len(grid[0]))]
    path = f"{_DESIGN} → field"
    distinct = len(set(colours.values()))
    if not 4 <= distinct <= 9:
        out.add(
            "two-d",
            scene.id,
            path,
            f"{STRIPES}: only {distinct} colour(s) across the nine strands (4–9 make a 2-D design)",
        )
        return
    if all(len({colours[p] for p in col}) == 1 for col in cols):
        out.add("two-d", scene.id, path, f"{STRIPES}: every column is one colour")
        return
    if all(len({colours[p] for p in row}) == 1 for row in rows):
        out.add("two-d", scene.id, path, f"{STRIPES}: every row is one colour")
        return

    def spread(cells: list[str]) -> float:
        return max(delta_e76(colours[a], colours[b]) for a in cells for b in cells)

    down = max(spread(col) for col in cols)
    across = max(spread(row) for row in rows)
    if down < across / 3:
        out.add(
            "two-d",
            scene.id,
            path,
            f"{STRIPES}: the columns barely change top to bottom (ΔE {down:.1f} against {across:.1f} along a row)",
        )


def _kits_differ(scene: Scene, colours: dict[str, RGB], out: _Found) -> None:
    a, b = LIVE_KITS
    if a in colours and b in colours and colours[a] == colours[b]:
        out.add("kits-differ", scene.id, f"{_DESIGN} → field", f"{SAME_KITS} (both {colours[a]})")


def scene_mode_problems(
    scenes: SceneCatalog,
    modes: ModesFile,
    recipes: RecipesFile,
    canopy: Mapping[str, Any],
    *,
    approved: ModesFile | None = None,
    rules: Iterable[str] = RULES,
) -> list[SceneProblem]:
    """Every problem the named rules find, in catalog order (the whole-catalog checks first)."""
    wanted = set(rules)
    unknown = wanted - set(RULES)
    if unknown:
        raise ValueError(f"unknown rule(s) {', '.join(sorted(unknown))}; use {', '.join(RULES)}")
    out = _Found()
    if "catalog" in wanted:
        _catalog(scenes, modes, out)
    if "slots" in wanted:
        _slots(scenes, modes, approved, out)
    if "recipes" in wanted:
        _recipes(scenes, modes, recipes, out)
    for scene in scenes.scenes:
        mode = modes.modes.get(scene.id)
        if mode is None:
            continue
        if "brightness" in wanted:
            _brightness(scene, mode, out)
        if "static" in wanted:
            _static(scene, mode, out)
        if "ladder-end" in wanted:
            _ladder_end(scene, mode, out)
        if "one-design" in wanted:
            _one_design(scene, mode, recipes, out)
        if "stepped" in wanted:
            _stepped(scene, mode, recipes, canopy, out)
        if wanted & {"two-d", "kits-differ"}:
            design = _design(mode)
            if design is None or not all(s.color in recipes.colors for s in design.stops):
                continue  # one-design says why
            colours = module_colours(mode, recipes, canopy)
            if "two-d" in wanted:
                _two_d(scene, colours, canopy, out)
            if "kits-differ" in wanted:
                _kits_differ(scene, colours, out)
    order = {s.id: i for i, s in enumerate(scenes.scenes)}
    return sorted(out.items, key=lambda p: order.get(p.mode, -1))


def scene_pins(
    scenes: SceneCatalog, modes: ModesFile, recipes: RecipesFile, canopy: Mapping[str, Any]
) -> dict:
    """The approved values the tests pin (§3.7: layout, layout_words, sample_at, solid, brightness, t3, and for
    stepped scenes stepped + flow_words), per scene in catalog order. A scene without a sampled field design (which
    the validator refuses) has no pins."""
    pins: dict[str, dict] = {}
    for scene in scenes.scenes:
        mode = modes.modes.get(scene.id)
        design = _design(mode) if mode is not None else None
        if mode is None or design is None or mode.techniques.modules is None:
            continue
        sample = mode.techniques.modules.sample
        assert sample is not None
        t3 = module_colours(mode, recipes, canopy)
        pin: dict[str, Any] = {
            "layout": design.field.model_dump(mode="json"),
            "layout_words": F.layout_words(design.field, canopy),
            "sample_at": sample.at,
            "solid": scene.solid.name,
            "brightness": scene.brightness_draft,
            "t3": {pos: list(rgb) for pos, rgb in t3.items()},
        }
        stepped = mode.techniques.stepped
        if stepped is not None:
            pin["stepped"] = {
                "direction": stepped.direction,
                "mode": stepped.mode,
                "steps_per_cycle": stepped.steps_per_cycle,
                "step_s": stepped.step_s,
                "run_s": stepped.run_s,
                "shift": stepped.shift,
                "brightness_swing": stepped.brightness_swing,
                "lag_steps": stepped.lag_steps,
                "seed": stepped.seed,
            }
            pin["flow_words"] = (
                motion_words(stepped.canvas, dict(canopy), stepped.direction)
                if stepped.mode == "travel"
                else breathe_words(stepped)
            )
        pins[scene.id] = pin
    return pins
