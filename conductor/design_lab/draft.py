"""Scene drafts: a patch over the parsed config that moves every rung of one scene's mode in lockstep (PLAN §3.1–§3.3).

A `SceneDraft` is keyed by an existing scene id and holds only what the client may change: the client copy (name,
mood, description), 2–6 named palette colours, the 2-D layout, the module sample point, the brightness, the solid
colour and (for scenes with a stepped rung) the stepped timing. Ids, ladders and slots are not fields, so a draft
can't create a scene, rename an id, add or drop a rung or move a slot.

`apply_drafts` is pure (no I/O, no clock, no engine) and follows the purity rule (§3.0): it never assigns into a trio
object; every output is built by `Model.model_validate(<edited dict from model_dump()>)` and re-validated through the
show's own schema (`parse_modes`, `_check_stepped`, `_check_idle_scene`) and `conductor.looks.scene_rules`.

Owners: the models, `ConfigTrio`, `Problem`, `DraftError`, `load_trio`, `slug` and the constants are the contracts
commit (L); the function bodies marked `S1` are stream S1's.
"""

from __future__ import annotations

import math
import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator

from conductor.config import ConductorSettings, ConfigError, RecipesFile, _explain, load_recipes
from conductor.looks import field as F
from conductor.looks import scene_rules
from conductor.looks.live import ConfigTrio
from conductor.looks.schema import (
    MOTION_PHRASES,
    MOTION_WORDS,
    STEPPED_FALSE_CLAIMS,
    FieldCanvas,
    Mode,
    ModesFile,
    ModuleSample,
    _colour_refs,
    load_modes,
    motion_words,
    parse_modes,
)
from conductor.scenes import HEX, SceneCatalog, load_scenes

GRID: tuple[str, ...] = tuple(f"r{r}c{c}" for r in range(3) for c in range(3))
PALETTE_MIN, PALETTE_MAX = 2, 6  # Mode.preview caps at 6 (schema.py Mode.preview max_length=6)
NUMBER_NAMES = re.compile(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|both)\b", re.IGNORECASE)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- the draft -----------------------------------------------------------------------------------------------------
class DraftColour(_Strict):
    name: str = Field(min_length=1, max_length=40)  # = PaletteColor.name bounds
    hex: str  # HEX (#RRGGBB), upper-cased

    @field_validator("hex")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not HEX.match(v):
            raise ValueError("colours are #RRGGBB")
        return v.upper()

    @field_validator("name")
    @classmethod
    def _plain_name(cls, v: str) -> str:
        # Colour names go into generated copy (§3.3), which the schema checks for motion and layout words and which
        # `compile._counted` rewrites by number words (D62): a colour called "Nine" would be miscounted.
        if m := MOTION_WORDS.search(v):
            raise ValueError(f"a colour name can't say {m.group(0)!r}: the kiosk copy would claim movement")
        if m := STEPPED_FALSE_CLAIMS.search(v):
            raise ValueError(f"a colour name can't say {m.group(0)!r}: the kiosk copy would claim an effect")
        lower = v.lower()
        for phrase in MOTION_PHRASES:
            if phrase in lower:
                raise ValueError(f"a colour name can't say {phrase!r}: it reads as a layout or a direction")
        if m := NUMBER_NAMES.search(v):
            raise ValueError(
                f"a colour name can't have a number in it ({m.group(0)!r}): the kiosk copy counts strands in words"
            )
        return v


STEP_S_INCREMENT = 0.5  # the Lab's editor moves step_s in half seconds (Slice C, PLAN §7)
STEP_S_OFF_INCREMENT = "step_s goes in {inc:g} s steps (4, 4.5, … 12); {v:g} s isn't one (the approved {approved:g} s is kept as it is)"


def on_step_increment(v: float) -> bool:
    steps = v / STEP_S_INCREMENT
    return abs(steps - round(steps)) <= 1e-9


class DraftStepped(_Strict):  # = SteppedTechnique's bounds (schema.py), exactly
    direction: Literal["forward", "reverse"]
    steps_per_cycle: int = Field(ge=3, le=12)
    # any value SteppedTechnique takes, so an approved modes.yaml always seeds; an EDIT moves in STEP_S_INCREMENT
    # steps (`_lock_problems`)
    step_s: float = Field(ge=4.0, le=12.0)
    run_s: float = Field(gt=0, le=180.0)  # ≥ step_s here; ≤ kiosk.idle_return_s in apply (`_check_stepped`)

    @field_validator("run_s")
    @classmethod
    def _at_least_one_step(cls, v: float, info: ValidationInfo) -> float:
        step_s = info.data.get("step_s")
        if step_s is not None and v < step_s:
            raise ValueError(
                f"run_s ({v:g} s) is shorter than one step ({step_s:g} s), so a run would never move while the "
                "moving copy shows"
            )
        return v


class DraftCopy(_Strict):
    # "auto" (the default): the approved sentence while §3.3 says it is still true, else the generated one; the
    # server resolves it and reports {chosen, why}. An explicit "approved" that is no longer true → 422 on save only.
    modules: Literal["auto", "approved", "generated"] = "auto"  # the T3 sentence
    stepped: Literal["auto", "approved", "generated"] = (
        "auto"  # the moving sentence (scenes with a stepped rung)
    )


with warnings.catch_warnings():
    # The wire name is `copy` (PLAN §3.1, §4). It shadows BaseModel's deprecated `.copy()` method, which nothing in
    # the Lab calls (use `model_copy`); pydantic warns about the shadowing at class creation only.
    warnings.filterwarnings("ignore", message='Field name "copy"', category=UserWarning)

    class SceneDraft(_Strict):
        scene_id: str = Field(
            pattern=r"^[a-z][a-z0-9-]{1,40}$"
        )  # must be an existing scene (checked in apply)
        name: str = Field(min_length=1, max_length=40)  # client copy → Scene.name and Mode.label
        mood: str = Field(min_length=1, max_length=60)  # client copy → Scene.mood and Mode.blurb
        description: str = Field(min_length=1, max_length=200)  # client copy → Scene.description
        palette: list[DraftColour] = Field(min_length=PALETTE_MIN, max_length=PALETTE_MAX)
        layout: F.FieldSpec  # LinearField | RadialField | SweepField | FinsField, reused
        sample_at: Literal["centre", "mean"] = "centre"
        brightness: int = Field(ge=1, le=100)  # brightness_draft and every rung's brightness
        solid: int = Field(ge=0)  # index into palette (default_solid)
        stepped: DraftStepped | None = None  # present iff the approved ladder has "stepped"
        copy: DraftCopy = Field(default_factory=DraftCopy)

        @field_validator("name", "mood", "description")
        @classmethod
        def _true_client_copy(cls, v: str, info: ValidationInfo) -> str:
            # The client copy shows on the guest panel at every tier (the name is the kiosk card's title, the mood
            # the mode's blurb, the description the Now-showing card's line) and on a partial canopy too, and nothing
            # rewrites it: it never says "moves" or counts the lights (scene_rules.copy_problem, PROMPT A1, D62).
            why = scene_rules.copy_problem(str(info.field_name), v)
            if why is not None:
                raise ValueError(why)
            return v

        @field_validator("layout")
        @classmethod
        def _finite_layout(cls, v: F.FieldSpec) -> F.FieldSpec:
            # field.py's own check runs before coercion, so a quoted "nan" or "1e400" got through as nan/inf and
            # failed later as a 500; refused here, after coercion, on the layout field.
            for name in ("centre_in", "centres_in", "angle_deg", "start_deg", "radius_in"):
                raw = getattr(v, name, None)
                points = raw if name == "centres_in" and raw is not None else [raw]
                for point in points:
                    values = point if isinstance(point, tuple | list) else [point]
                    if any(isinstance(n, float) and not math.isfinite(n) for n in values):
                        raise ValueError(f"{name} must be finite numbers (canopy inches or degrees)")
            return v

        @field_validator("palette")
        @classmethod
        def _unique_names(cls, v: list[DraftColour]) -> list[DraftColour]:
            seen: set[str] = set()
            for colour in v:
                key = colour.name.casefold()
                if key in seen:
                    raise ValueError(
                        f"two colours are called {colour.name!r}; each colour needs its own name"
                    )
                seen.add(key)
            return v

        @field_validator("solid")
        @classmethod
        def _solid_in_palette(cls, v: int, info: ValidationInfo) -> int:
            palette = info.data.get("palette")
            if palette is not None and v >= len(palette):
                raise ValueError("the solid colour must be one of the palette's colours")
            return v


# ---- the trio and its problems ---------------------------------------------------------------------------------------
# ConfigTrio lives with the show's live looks (conductor/looks/live.py, D68); re-exported here for the Lab.


@dataclass(frozen=True)
class Problem:
    path: str  # the config path as ConfigError/scene_rules say it: "mode 'golden-hour' → techniques → modules → feature"
    field: (
        str  # the SceneDraft field: "palette", "palette.2.hex", "layout", "brightness", "copy.modules", ...
    )
    message: str

    def to_json(self) -> dict:
        return {"path": self.path, "field": self.field, "message": self.message}


class DraftError(ValueError):
    """A draft that doesn't apply: every problem found, each with the draft field it belongs to."""

    def __init__(self, problems: list[Problem], message: str | None = None) -> None:
        self.problems: list[Problem] = list(problems)
        super().__init__(message or f"The draft has {len(self.problems)} problem(s).")


def load_trio(config_dir: Path) -> ConfigTrio:
    """The approved (git) trio through the show's own loaders, deep-copied: `load_scenes` is `lru_cache`d and is the
    very catalog the guest panel serves, and the pydantic models are mutable (purity rule, §3.0)."""
    config_dir = Path(config_dir)
    return ConfigTrio(
        scenes=load_scenes(config_dir).model_copy(deep=True),
        modes=load_modes(config_dir).model_copy(deep=True),
        recipes=load_recipes(config_dir).model_copy(deep=True),
    )


# ---- seed and equality (S1) ------------------------------------------------------------------------------------------
MODULE_PAGE = "the module page"  # the owner of recipe keys no mode names (the colour-study colours, §3.2.1)
LOCKED_CYCLES = (
    "cycles is locked to 1 in v1 (D63: one static design, no repeats); unlocking it is a D63 change for Alon"
)
LOCKED_SAMPLE = (
    "the module sample is locked to the module centre in v1 (D63: every sample at: centre); at: mean is a D63 "
    "change for Alon"
)


def _design_sample(mode: Mode, scene_id: str) -> ModuleSample:
    """The mode's module-level sample of a 2-D field: the one design every rung of the scene draws."""
    mods = mode.techniques.modules
    sample = mods.sample if mods is not None else None
    if sample is None or not isinstance(sample.canvas, FieldCanvas):
        raise DraftError(
            [
                Problem(
                    f"mode {scene_id!r} → techniques → modules → sample",
                    "draft",
                    "this scene has no 2-D design at module level (a modules sample of a field canvas), so the "
                    "Lab can't draft it",
                )
            ]
        )
    return sample


def seed_draft(trio: ConfigTrio, scene_id: str) -> SceneDraft:
    """The draft that reproduces the approved scene exactly: Scene (name, mood, description, palette,
    brightness_draft, default_solid), the mode's design (modules.sample.canvas.field, .at), its stepped technique (if
    any); copy "auto"/"auto". KeyError for an unknown scene."""
    scene = trio.scenes.get(scene_id)
    mode = trio.modes.mode(scene_id)
    sample = _design_sample(mode, scene_id)
    stepped = mode.techniques.stepped
    return SceneDraft.model_validate(
        {
            "scene_id": scene.id,
            "name": scene.name,
            "mood": scene.mood,
            "description": scene.description,
            "palette": [{"name": c.name, "hex": c.hex} for c in scene.palette],
            "layout": sample.canvas.field.model_dump(),
            "sample_at": sample.at,
            "brightness": scene.brightness_draft,
            "solid": scene.default_solid,
            "stepped": (
                {
                    "direction": stepped.direction,
                    "steps_per_cycle": stepped.steps_per_cycle,
                    "step_s": stepped.step_s,
                    "run_s": stepped.run_s,
                }
                if stepped is not None
                else None
            ),
        }
    )


def is_approved(trio: ConfigTrio, draft: SceneDraft) -> bool:
    """draft == seed_draft(trio, draft.scene_id) (compared as dumps: numbers numerically, hexes upper-cased)."""
    return draft.model_dump() == seed_draft(trio, draft.scene_id).model_dump()


def _same_layout(a: SceneDraft, b: SceneDraft) -> bool:
    """Model equality of the 2-D field (not equal layout words: §3.3)."""
    return a.layout.model_dump() == b.layout.model_dump()


def painted_change(seed: SceneDraft, draft: SceneDraft) -> Literal["palette", "layout"] | None:
    """What of a painted scene's frames the draft changes: "palette" when the hexes in stop order differ (a hex edit, a
    reorder, an added or removed colour), "layout" when the 2-D field or the module sample does, else None. A rename
    alone gives a new recipe key but the same frames, so the same slot hash: nothing to repaint. One predicate for
    the T1 lock (preview.check_t1_lock) and `needs_slot_repaint`, so they never disagree."""
    if [c.hex for c in seed.palette] != [c.hex for c in draft.palette]:
        return "palette"
    if not _same_layout(draft, seed) or draft.sample_at != seed.sample_at:
        return "layout"
    return None


def _names(draft: SceneDraft) -> list[str]:
    return [c.name.casefold() for c in draft.palette]


# ---- copy (§3.3) -----------------------------------------------------------------------------------------------------
T3_TEMPLATE = (
    "Colour and dimming: each Hubspace strand set to a single colour, so the palette is laid out "
    "{words}: {colours}."
)
STEPPED_TEMPLATE = (
    "Colour and dimming through the Hubspace cloud: each Hubspace strand set to a single colour and "
    "sent its next colour every few seconds, so the palette's colours move {flow} across the "
    "canopy: {colours}."
)
STRAND_TEMPLATE = "Colour and dimming: all nine Hubspace strands set to {solid}."
APPROVED_WHY = "approved wording: the design and its colour names are as approved"


def colour_phrase(names: list[str], *, invert: bool, transform: str) -> str:
    """Lower-cased names in the order they sit from where the layout starts: reversed when invert; with transform
    "mirror" → "<first> at both ends through <last> in the middle"; otherwise "<a>, then <b>, <c> and <d>" (two
    colours: "<a>, then <b>")."""
    ordered = [n.lower() for n in names]
    if invert:
        ordered.reverse()
    if not ordered:
        return ""
    if transform == "mirror" and len(ordered) > 1:
        return f"{ordered[0]} at both ends through {ordered[-1]} in the middle"
    if len(ordered) == 1:
        return ordered[0]
    first, rest = ordered[0], ordered[1:]
    if len(rest) == 1:
        return f"{first}, then {rest[0]}"
    return f"{first}, then {', '.join(rest[:-1])} and {rest[-1]}"


def _canvas_of(layout: F.FieldSpec, keys: list[str] | None = None) -> FieldCanvas:
    """The design as a field canvas: the layout with evenly spaced stops at i / (n − 1) (placeholder colours
    without keys: for layout and flow words only)."""
    keys = keys or ["colour-0", "colour-1"]
    return FieldCanvas.model_validate(
        {
            "kind": "field",
            "field": layout.model_dump(),
            "stops": [{"at": i / (len(keys) - 1), "color": k} for i, k in enumerate(keys)],
            "recipe": "rgb_only",
        }
    )


def reached_stops(layout: F.FieldSpec, n: int, canopy: dict) -> list[int]:
    """The palette stops (n of them, at i / (n − 1)) the module level actually shows: stop i is reached when the
    design's t at some module centre (D63: every sample at: centre) is within half a stop spacing of it. In stop
    order; never empty. A sweep centred on an edge or a corner covers only part of the circle, and a line misses the
    far ends, so a colour no strand shows is never named in the T3 sentence."""
    if n < 2:
        return list(range(n))
    centres = F.module_centres(canopy, GRID)
    ts = [F.value(layout, x, y, canopy) for x, y in centres.values()]
    half = 0.5 / (n - 1)
    return [i for i in range(n) if any(abs(t - i / (n - 1)) <= half + 1e-9 for t in ts)]


def generated_copy(mode: Mode, draft: SceneDraft, canopy: dict) -> dict[str, str]:
    """{"modules": T3_TEMPLATE(...), "strand": STRAND_TEMPLATE(...), "stepped": STEPPED_TEMPLATE(...)} — the stepped
    sentence only when the mode has a stepped rung and the draft its timing. The T3 sentence names only the colours
    the module level reaches (`reached_stops`); over a run every strand steps through the whole palette, so the moving
    sentence names them all."""
    layout = draft.layout
    names = [c.name for c in draft.palette]
    shown = [names[i] for i in reached_stops(layout, len(names), canopy)]
    still = colour_phrase(shown, invert=layout.invert, transform=layout.transform)
    colours = colour_phrase(names, invert=layout.invert, transform=layout.transform)
    out = {
        "modules": T3_TEMPLATE.format(words=F.layout_words(layout, canopy), colours=still),
        "strand": STRAND_TEMPLATE.format(solid=draft.palette[draft.solid].name.lower()),
    }
    if mode.techniques.stepped is not None and draft.stepped is not None:
        flow = motion_words(_canvas_of(layout), canopy, draft.stepped.direction)
        out["stepped"] = STEPPED_TEMPLATE.format(flow=flow, colours=colours)
    return out


@dataclass(frozen=True)
class CopyChoice:
    """One rung's resolved sentence: the approved one while it is still true, else the generated one."""

    chosen: Literal["approved", "generated"]
    why: str
    sentence: str
    refused: str | None = None  # an explicit "approved" that no longer holds: why (a save refuses it, §3.3)

    def to_json(self) -> dict:
        return {"chosen": self.chosen, "why": self.why, "sentence": self.sentence}


def _choose(asked: str, approved: str, generated: str, reasons: list[str]) -> CopyChoice:
    because = " and ".join(reasons)
    if asked == "generated":
        return CopyChoice("generated", "generated on request", generated)
    if not reasons:
        return CopyChoice("approved", APPROVED_WHY, approved)
    if asked == "approved":
        refused = f"the approved sentence no longer describes this design ({because})"
        return CopyChoice("generated", f"{refused}, so the generated one is shown", generated, refused)
    return CopyChoice("generated", because, generated)


def resolve_copy(approved: ConfigTrio, draft: SceneDraft, canopy: dict) -> dict[str, CopyChoice | None]:
    """{"modules", "stepped" (None without a stepped rung), "strand"}: each rung's sentence by the rules of §3.3. T3
    keeps the approved sentence only while the layout is model-equal and the colour names and their order are
    unchanged; stepped also needs the direction unchanged; strand only needs the solid colour's name unchanged."""
    seed = seed_draft(approved, draft.scene_id)
    mode = approved.modes.mode(draft.scene_id)
    generated = generated_copy(mode, draft, canopy)
    reasons = []
    if not _same_layout(draft, seed):
        reasons.append("the layout changed")
    if _names(draft) != _names(seed):
        reasons.append("the colour names or their order changed")
    tech = mode.techniques
    assert tech.modules is not None
    out: dict[str, CopyChoice | None] = {
        "modules": _choose(draft.copy.modules, tech.modules.feature, generated["modules"], reasons),
        "stepped": None,
        "strand": None,
    }
    if tech.stepped is not None and "stepped" in generated:
        moving = list(reasons)
        if seed.stepped is None or draft.stepped is None or draft.stepped.direction != seed.stepped.direction:
            moving.append("the stepped direction changed")
        out["stepped"] = _choose(
            draft.copy.stepped, tech.stepped.moving_feature, generated["stepped"], moving
        )
    if tech.strand is not None:
        solid_same = draft.palette[draft.solid].name.casefold() == seed.palette[seed.solid].name.casefold()
        out["strand"] = _choose(
            "auto",
            tech.strand.feature,
            generated["strand"],
            [] if solid_same else ["the solid colour changed"],
        )
    return out


# The description is free client copy: nothing regenerates it, so when the colours or the layout move under it the
# Lab says so (a note in the preview and the export's review notes, never a refusal).
DESCRIPTION_NAMES_GONE = (
    "The description still names {names}, which the palette no longer has — check it still reads true."
)
DESCRIPTION_UNCHANGED = "The description is unchanged but the {what} changed — check it still reads true."


def description_notes(seed: SceneDraft, draft: SceneDraft) -> list[str]:
    """Non-blocking notes on a description the draft's colours or layout may have made stale: it names a colour the
    palette dropped or renamed, or it is the approved text while the colour names, their order or the layout moved."""
    kept = {c.name.casefold() for c in draft.palette}
    gone = [
        c.name.lower()
        for c in seed.palette
        if c.name.casefold() not in kept
        and re.search(rf"(?<!\w){re.escape(c.name)}(?!\w)", draft.description, re.IGNORECASE)
    ]
    if gone:
        names = gone[0] if len(gone) == 1 else f"{', '.join(gone[:-1])} and {gone[-1]}"
        return [DESCRIPTION_NAMES_GONE.format(names=names)]
    if draft.description != seed.description:
        return []
    what = [
        w
        for w, moved in (
            ("colours", _names(draft) != _names(seed)),
            ("layout", not _same_layout(draft, seed)),
        )
        if moved
    ]
    return [DESCRIPTION_UNCHANGED.format(what=" and the ".join(what))] if what else []


# ---- recipe keys (§3.2.1) --------------------------------------------------------------------------------------------
def slug(name: str) -> str:
    """lower, [^a-z0-9]+ → "-", strip "-"; "" → "colour"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "colour"


def owners_of(modes: ModesFile, recipes: RecipesFile) -> dict[str, str]:
    """Recipe key → the mode whose design names it (first one); keys no mode names belong to the module page."""
    owners: dict[str, str] = {}
    for mode_id, mode in modes.modes.items():
        for _where, key in _colour_refs(mode):
            owners.setdefault(key, mode_id)
    for key in recipes.colors:
        owners.setdefault(key, MODULE_PAGE)
    return owners


def _palette_path(scene_id: str, i: int, what: str = "name") -> str:
    return f"the draft of {scene_id} → palette → [{i}] → {what}"


def recipe_keys(
    approved_mode: Mode, draft: SceneDraft, recipes: RecipesFile, owners: Mapping[str, str]
) -> list[str]:
    """One recipe key per draft colour, by the exact allocation of §3.2.1 (stable keys first, then the name, then
    `<scene id>-<name>`); a key nobody can have → DraftError on `palette.<i>.name`. `owners` is every taken key →
    its owner (this scene's own keys are free to reuse; keys other drafts of the same apply took count as theirs)."""
    sid = draft.scene_id
    names = [c.name for c in draft.palette]
    slugs = [slug(n) for n in names]
    old_keys = [s.color for s in _design_sample(approved_mode, sid).canvas.stops]
    old_names = [recipes.colors[k].label.casefold() if k in recipes.colors else None for k in old_keys]

    def kept(i: int) -> bool:  # the approved colour at this place, name unchanged
        return i < len(old_names) and old_names[i] == names[i].casefold()

    problems: list[Problem] = []
    first: dict[str, int] = {}
    for i, s in enumerate(slugs):  # 1. unique within the draft
        if s not in first:
            first[s] = i
            continue
        j = first[s]
        blame, other = (j, i) if kept(i) and not kept(j) else (i, j)  # point at the colour that was edited
        problems.append(
            Problem(
                _palette_path(sid, blame),
                f"palette.{blame}.name",
                f"{names[blame]!r} and {names[other]!r} would share one recipe key ({s!r}); give it a more "
                "distinct name",
            )
        )
    if problems:
        raise DraftError(problems)
    keys: list[str | None] = [None] * len(names)
    assigned: set[str] = set()
    for i, name in enumerate(names):  # 2. stable keys: the same name in the same place ...
        if i < len(old_names) and old_names[i] == name.casefold():
            keys[i] = old_keys[i]
            assigned.add(old_keys[i])
    for i, name in enumerate(names):  # ... and a colour moved elsewhere keeps its key (a reorder)
        if keys[i] is None and name.casefold() in old_names:
            j = old_names.index(name.casefold())
            if old_keys[j] not in assigned:
                keys[i] = old_keys[j]
                assigned.add(old_keys[j])
    others = {k: o for k, o in owners.items() if o != sid}
    for i, name in enumerate(names):  # 3. the name, else <scene id>-<name>
        if keys[i] is not None:
            continue
        candidates = (slugs[i], f"{sid}-{slugs[i]}")
        free = next((c for c in candidates if c not in others and c not in assigned), None)
        if free is not None:
            keys[i] = free
            assigned.add(free)
            continue
        holder = others.get(slugs[i])
        who = holder if holder is not None else "another colour of this draft"
        problems.append(
            Problem(
                _palette_path(sid, i),
                f"palette.{i}.name",
                f"the colour name {name!r} is already used by another scene's colour ({who}) and "
                f"{candidates[1]!r} is taken too; choose another name",
            )
        )
    if problems:
        raise DraftError(problems)
    return [k for k in keys if k is not None]


def _rgb(hex_: str) -> list[int]:
    return [int(hex_[i : i + 2], 16) for i in (1, 3, 5)]


def _recipe(colour: DraftColour) -> dict:
    """A palette colour's recipe entry in the house style (recipes.yaml): the hex at full strength, RGB only (per-puck
    white is unverified, gate 6), `blend` the same, the strand fallback the same RGB."""
    r, g, b = _rgb(colour.hex)
    entry = {"r": r, "g": g, "b": b, "colorBrightness": 100}
    return {
        "label": colour.name,
        "hex": colour.hex,
        "rgb_only": dict(entry),
        "blend": dict(entry),
        "strand": {"rgb": [r, g, b]},
    }


# ---- the patch (S1) --------------------------------------------------------------------------------------------------
IDLE_NEVER_STEPS = (
    "{label} is the idle look (kiosk.idle_mode), and the idle look never steps (D65): it has no stepped rung to "
    "edit"
)


def _lock_problems(mode: Mode, draft: SceneDraft, *, idle: bool = False) -> list[Problem]:
    """The D63 locks and the stepped rung's presence: a draft can't repeat the design, sample at the mean, or add or
    drop a stepped rung (the idle look, `idle`, says why it has none); an edited step_s moves in half seconds (an
    approved one off the increment, which SteppedTechnique allows, is kept)."""
    sid = draft.scene_id
    out = []
    if draft.layout.cycles != 1:
        out.append(Problem(f"the draft of {sid} → layout → cycles", "layout.cycles", LOCKED_CYCLES))
    if draft.sample_at != "centre":
        out.append(Problem(f"the draft of {sid} → sample_at", "sample_at", LOCKED_SAMPLE))
    has = mode.techniques.stepped is not None
    if has and draft.stepped is None:
        out.append(
            Problem(
                f"the draft of {sid} → stepped",
                "stepped",
                f"{mode.label} has a stepped rung, so its draft needs the stepped timing (direction, "
                "steps_per_cycle, step_s, run_s)",
            )
        )
    if has and draft.stepped is not None:
        approved_step = mode.techniques.stepped.step_s
        step_s = draft.stepped.step_s
        if step_s != approved_step and not on_step_increment(step_s):
            out.append(
                Problem(
                    f"the draft of {sid} → stepped → step_s",
                    "stepped.step_s",
                    STEP_S_OFF_INCREMENT.format(inc=STEP_S_INCREMENT, v=step_s, approved=approved_step),
                )
            )
    if not has and draft.stepped is not None:
        out.append(
            Problem(
                f"the draft of {sid} → stepped",
                "stepped",
                IDLE_NEVER_STEPS.format(label=mode.label)
                if idle
                else f"{mode.label} has no stepped rung, and a draft can't add one",
            )
        )
    return out


def _fin_map_problems(sid: str, draft: SceneDraft, canopy: Mapping) -> list[Problem]:
    """A fins layout over a canopy with no fin map (D74): one plain problem, found before any field is evaluated
    (resolve_copy reads the design through reached_stops, and the fins field has no value without the map)."""
    if isinstance(draft.layout, F.FinsField) and not F.has_fins(canopy):
        return [Problem(f"the draft of {sid} → layout", "layout", F.NO_FIN_MAP)]
    return []


def _patched_mode(
    mode: Mode, draft: SceneDraft, keys: list[str], copy: Mapping[str, CopyChoice | None]
) -> dict:
    """The mode as a dict with every rung moved in lockstep (§3.2.2); its ladder, slot and motion untouched."""
    data = mode.model_dump()
    b = draft.brightness
    design = _canvas_of(draft.layout, keys).model_dump()
    t = data["techniques"]
    if t.get("canvas") is not None:
        t["canvas"]["canvas"] = design
        t["canvas"]["brightness"] = b
    if t.get("slot") is not None:
        t["slot"]["brightness"] = b
    modules = copy["modules"]
    assert modules is not None
    t["modules"]["sample"] = {"canvas": design, "at": draft.sample_at, "brightness": b}
    t["modules"]["feature"] = modules.sentence
    moving = copy["stepped"]
    if t.get("stepped") is not None and draft.stepped is not None and moving is not None:
        t["stepped"].update(
            canvas=design,
            direction=draft.stepped.direction,
            steps_per_cycle=draft.stepped.steps_per_cycle,
            step_s=draft.stepped.step_s,
            run_s=draft.stepped.run_s,
            brightness=b,
            moving_feature=moving.sentence,
        )
    strand = copy["strand"]
    if t.get("strand") is not None and strand is not None:
        t["strand"]["look"] = {"kind": "rgb", "rgb": _rgb(draft.palette[draft.solid].hex), "brightness": b}
        t["strand"]["fallback_look"] = None
        t["strand"]["feature"] = strand.sentence
    data["label"] = draft.name
    data["blurb"] = draft.mood
    data["preview"] = [c.hex for c in draft.palette]
    return data


def _patch_scene(scenes: dict, draft: SceneDraft) -> None:
    """In the catalog dump (the Lab's own dict, never a model): the scene's client copy, palette, brightness, solid."""
    for scene in scenes["scenes"]:
        if scene["id"] == draft.scene_id:
            scene.update(
                name=draft.name,
                mood=draft.mood,
                description=draft.description,
                brightness_draft=draft.brightness,
                default_solid=draft.solid,
                palette=[{"name": c.name, "hex": c.hex} for c in draft.palette],
            )


# What a scene_rules problem points at when its path doesn't say (the rule's own subject).
RULE_FIELD = {
    "catalog": "draft",
    "brightness": "brightness",
    "static": "layout",
    "ladder-end": "solid",
    "slots": "draft",
    "one-design": "palette",
    "recipes": "palette",
    "stepped": "stepped",
    "two-d": "layout",
    "kits-differ": "layout",
}


def problem_of(found: scene_rules.SceneProblem) -> Problem:
    """A scene_rules problem as a draft Problem (its field from the path, else the rule's subject; the D63
    design-quality rules are always the layout)."""
    path = f"mode {found.mode!r} → {found.path}"
    field = field_for(path)
    if field == "draft" or found.rule in scene_rules.WARNINGS:
        field = RULE_FIELD.get(found.rule, "draft")
    return Problem(path, field, found.message)


def _shape_problems(approved: ConfigTrio, scenes: SceneCatalog, modes: ModesFile) -> list[Problem]:
    """Belt and braces (§3.2.4): ids, ladders and slots never move."""
    out = []
    if [s.id for s in scenes.scenes] != [s.id for s in approved.scenes.scenes]:
        out.append(Problem("scenes", "scene_id", "the draft would change the scene ids"))
    if list(modes.modes) != list(approved.modes.modes):
        out.append(Problem("modes", "scene_id", "the draft would change the mode ids"))
    for mode_id, mode in approved.modes.modes.items():
        patched = modes.modes.get(mode_id)
        if patched is not None and (list(patched.ladder) != list(mode.ladder) or patched.slot != mode.slot):
            out.append(
                Problem(
                    f"mode {mode_id!r} → ladder",
                    "draft",
                    "the draft would move a rung or a slot; ladders and slots never move",
                )
            )
    return out


def apply_drafts(
    trio: ConfigTrio, drafts: Mapping[str, SceneDraft], *, canopy: dict, settings: ConductorSettings
) -> ConfigTrio:
    """The patched trio, scenes processed in catalog order and validated once at the end (§3.2.2, §3.2.3). Raises
    DraftError with every problem (an unknown scene → field "scene_id"; an explicit "approved" copy that is no longer
    true → field "copy.modules"/"copy.stepped")."""
    from conductor.show import _check_idle_scene, _check_stepped  # the show's own load checks (not edited)

    order = [s.id for s in trio.scenes.scenes]
    problems: list[Problem] = []
    for key, draft in drafts.items():
        if draft.scene_id != key:
            problems.append(
                Problem(
                    f"the draft of {key} → scene_id",
                    "scene_id",
                    f"the draft is for {draft.scene_id!r}, not {key!r}",
                )
            )
        elif key not in order or key not in trio.modes.modes:
            problems.append(
                Problem(
                    f"the draft of {key}",
                    "scene_id",
                    f"there is no scene called {key!r}; a draft only changes one of the {len(order)} scenes",
                )
            )
    if problems:
        raise DraftError(problems)
    ids = [sid for sid in order if sid in drafts]
    where = "the draft of " + (", ".join(ids) or "nothing")
    # The purity rule (§3.0): edit dumps, never the models.
    scenes_dump = trio.scenes.model_dump()
    modes_dump = trio.modes.model_dump()
    recipes_dump = trio.recipes.model_dump()
    owners = owners_of(trio.modes, trio.recipes)
    claimed: dict[str, str] = {}  # keys given out in this apply → the scene that has them now
    choices: dict[str, dict[str, CopyChoice | None]] = {}
    for sid in ids:
        draft = drafts[sid]
        mode = trio.modes.mode(sid)
        found = _lock_problems(mode, draft, idle=sid == settings.kiosk.idle_mode)
        found += _fin_map_problems(sid, draft, canopy)
        try:
            keys = recipe_keys(mode, draft, trio.recipes, {**owners, **claimed})
        except DraftError as exc:
            found += exc.problems
            keys = []
        if found:
            problems += found
            continue
        claimed.update(dict.fromkeys(keys, sid))
        copy = resolve_copy(trio, draft, canopy)
        choices[sid] = copy
        modes_dump["modes"][sid] = _patched_mode(mode, draft, keys, copy)
        _patch_scene(scenes_dump, draft)
        for key, colour in zip(keys, draft.palette, strict=True):
            recipes_dump["colors"][key] = _recipe(colour)
    if problems:
        raise DraftError(problems)
    # Keys a drafted scene owned that no mode names any more go (the colour-study colours are nobody's to drop).
    named = set(claimed)
    for mode_id, mode in trio.modes.modes.items():
        if mode_id not in drafts:
            named |= {k for _w, k in _colour_refs(mode)}
    for sid in ids:
        for _w, key in _colour_refs(trio.modes.mode(sid)):
            if key not in named:
                recipes_dump["colors"].pop(key, None)

    # 1–2. scenes.yaml and recipes.yaml
    try:
        scenes = SceneCatalog.model_validate(scenes_dump)
    except ValidationError as exc:
        raise DraftError(problems_from_config_error(_explain(where, exc))) from None
    try:
        recipes = RecipesFile.model_validate(recipes_dump)
    except ValidationError as exc:
        raise DraftError(problems_from_config_error(_explain(where, exc))) from None
    # 3. the real schema + cross_check (copy honesty, layout words, one colour, sweep centre, slots)
    try:
        modes = parse_modes(modes_dump, recipes=recipes, canopy=canopy, path=where)
    except ConfigError as exc:
        raise DraftError(problems_from_config_error(str(exc))) from None
    # 4. the show's own load checks: no run outlasts idle_return_s, the idle mode never steps, idle_scene is a mode
    try:
        _check_stepped(settings, modes)
        _check_idle_scene(settings.kiosk.idle_scene, list(modes.modes))
    except ConfigError as exc:
        raise DraftError(problems_from_config_error(str(exc))) from None
    if shape := _shape_problems(trio, scenes, modes):
        raise DraftError(shape)
    # 5. the scene ↔ mode invariants that refuse a draft (D63's design-quality rules only warn: preview reports them)
    blocking = scene_rules.scene_mode_problems(
        scenes, modes, recipes, canopy, approved=trio.modes, rules=scene_rules.BLOCKING
    )
    if blocking:
        raise DraftError([problem_of(p) for p in blocking])
    # 6. copy: an explicit "approved" sentence that is no longer true
    for sid, copy in choices.items():
        for rung, at in (("modules", "modules → feature"), ("stepped", "stepped → moving_feature")):
            choice = copy[rung]
            if choice is not None and choice.refused:
                problems.append(Problem(f"mode {sid!r} → techniques → {at}", f"copy.{rung}", choice.refused))
    if problems:
        raise DraftError(problems)
    return ConfigTrio(scenes=scenes, modes=modes, recipes=recipes)


def apply_draft(
    trio: ConfigTrio, draft: SceneDraft, *, canopy: dict, settings: ConductorSettings
) -> ConfigTrio:
    """= apply_drafts(trio, {draft.scene_id: draft}, …)."""
    return apply_drafts(trio, {draft.scene_id: draft}, canopy=canopy, settings=settings)


def draft_changes(approved: ConfigTrio, draft: SceneDraft, *, canopy: dict) -> dict:
    """What the draft changes against the approved scene (§3.2.5): approved_colours, name, mood, description, layout,
    brightness, solid, sample_at, stepped, copy, and the flags needs_d59 / needs_d63 / needs_slot_repaint.

    `needs_slot_repaint` follows the painted design's frames: the hexes in stop order (a hex edit, a reorder, an added
    or removed colour) or the layout. A rename alone gives a new recipe key but the same frames, so the same slot
    hash: no repaint. A fins draft over a canopy with no fin map is a DraftError (D74), before anything is read."""
    if problems := _fin_map_problems(draft.scene_id, draft, canopy):
        raise DraftError(problems)
    seed = seed_draft(approved, draft.scene_id)
    mode = approved.modes.mode(draft.scene_id)

    def pair(a: object, b: object) -> dict | None:
        return None if a == b else {"from": a, "to": b}

    colours = []
    for i in range(max(len(seed.palette), len(draft.palette))):
        a = seed.palette[i].model_dump() if i < len(seed.palette) else None
        b = draft.palette[i].model_dump() if i < len(draft.palette) else None
        if a != b:
            colours.append({"index": i, "from": a, "to": b})
    layout = None
    if not _same_layout(draft, seed):
        layout = {
            "from": seed.layout.model_dump(mode="json"),
            "to": draft.layout.model_dump(mode="json"),
            "words_from": F.layout_words(seed.layout, canopy),
            "words_to": F.layout_words(draft.layout, canopy),
        }
    solid_from = {"index": seed.solid, **seed.palette[seed.solid].model_dump()}
    solid_to = {"index": draft.solid, **draft.palette[draft.solid].model_dump()}
    copy = resolve_copy(approved, draft, canopy)
    name = pair(seed.name, draft.name)
    frames_differ = painted_change(seed, draft) is not None
    return {
        "approved_colours": colours,
        "name": name,
        "mood": pair(seed.mood, draft.mood),
        "description": pair(seed.description, draft.description),
        "layout": layout,
        "brightness": pair(seed.brightness, draft.brightness),
        "solid": pair(solid_from, solid_to),
        "sample_at": pair(seed.sample_at, draft.sample_at),
        "stepped": pair(
            seed.stepped.model_dump() if seed.stepped else None,
            draft.stepped.model_dump() if draft.stepped else None,
        ),
        "copy": [rung for rung, c in copy.items() if c is not None and c.chosen == "generated"],
        "needs_d59": bool(colours) or name is not None,
        "needs_d63": layout is not None,
        "needs_slot_repaint": mode.slot is not None and frames_differ,
    }


# ---- ConfigError text → problems (§3.2.3) ----------------------------------------------------------------------------
# Ordered: the first pattern the path matches names the draft field.
FIELD_FOR: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"→ label\b|scenes → \[?\d+\]? → name\b"), "name"),
    (re.compile(r"→ (blurb|mood)\b"), "mood"),
    (re.compile(r"→ description\b"), "description"),
    (re.compile(r"→ preview\b|→ stops\b|palette|recipes"), "palette"),  # palette.<i> when an index is known
    (re.compile(r"→ field\b"), "layout"),  # any depth
    (re.compile(r"→ sample → at\b"), "sample_at"),
    (re.compile(r"brightness"), "brightness"),
    (re.compile(r"→ modules → feature\b"), "copy.modules"),
    (re.compile(r"→ stepped → moving_feature\b"), "copy.stepped"),
    (re.compile(r"→ stepped → (direction|steps_per_cycle|step_s|run_s)\b"), "stepped"),  # stepped.<that>
    (re.compile(r"→ strand\b"), "solid"),
)
_PALETTE_INDEX = re.compile(r"→ (?:palette|stops|preview) → \[?(\d+)\]?(?: → (name|hex)\b)?")
_STEPPED_FIELD = re.compile(r"→ stepped → (direction|steps_per_cycle|step_s|run_s)\b")
_LAYOUT_FIELD = re.compile(r"→ field → ([a-z_]+)\s*$")
_BULLET = re.compile(r"^\s*•\s*(.*\S)\s*$")
# Paths FIELD_FOR leaves at "draft" that do name a field: the sampled design itself (one colour at every module, a
# sweep on a module centre) is the layout; the stepped technique's own checks are its timing; a recipe is a colour.
_DRAFT_REFINED = (
    (re.compile(r"→ modules → sample\s*$"), "layout"),
    (re.compile(r"→ stepped\s*$"), "stepped"),
    (re.compile(r"^colors → "), "palette"),  # recipes.yaml's own paths (a recipe is a palette colour)
)


def field_for(path: str) -> str:
    """The SceneDraft field a config path names (FIELD_FOR, refined: `palette.<i>[.name|.hex]`, `stepped.<name>`,
    `layout.<name>`)."""
    field = next((f for pattern, f in FIELD_FOR if pattern.search(path)), "draft")
    if field == "palette" and (m := _PALETTE_INDEX.search(path)):
        field = f"palette.{m.group(1)}" + (f".{m.group(2)}" if m.group(2) else "")
    elif field == "stepped" and (m := _STEPPED_FIELD.search(path)):
        field = f"stepped.{m.group(1)}"
    elif field == "layout" and (m := _LAYOUT_FIELD.search(path)):
        field = f"layout.{m.group(1)}"
    elif field == "draft":
        field = next((f for pattern, f in _DRAFT_REFINED if pattern.search(path)), "draft")
    return field


def problems_from_config_error(text: str) -> list[Problem]:
    """Each `  • <path>: <message>` bullet of a ConfigError becomes one Problem; `field` from FIELD_FOR ("draft"
    when nothing matches; `palette.<i>` and `stepped.<name>` when the path says which). A message with no bullets
    (the show's `_check_stepped`) is one problem."""
    lines = [m.group(1) for line in text.splitlines() if (m := _BULLET.match(line))]
    if not lines:
        lines = [text.strip()]
    out = []
    for line in lines:
        path, sep, message = line.partition(": ")
        if not sep:
            path, message = "", line
        out.append(Problem(path, field_for(path), message.removeprefix("Value error, ")))
    return out


__all__ = [
    "FIELD_FOR",
    "GRID",
    "IDLE_NEVER_STEPS",
    "MODULE_PAGE",
    "NUMBER_NAMES",
    "PALETTE_MAX",
    "PALETTE_MIN",
    "STEPPED_TEMPLATE",
    "STEP_S_INCREMENT",
    "STRAND_TEMPLATE",
    "T3_TEMPLATE",
    "ConfigTrio",
    "CopyChoice",
    "DraftColour",
    "DraftCopy",
    "DraftError",
    "DraftStepped",
    "Problem",
    "SceneDraft",
    "apply_draft",
    "apply_drafts",
    "colour_phrase",
    "description_notes",
    "draft_changes",
    "field_for",
    "generated_copy",
    "is_approved",
    "load_trio",
    "owners_of",
    "painted_change",
    "problem_of",
    "problems_from_config_error",
    "reached_stops",
    "recipe_keys",
    "resolve_copy",
    "seed_draft",
    "slug",
]
