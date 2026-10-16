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

import re
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from conductor.config import ConductorSettings, RecipesFile, load_recipes
from conductor.looks import field as F
from conductor.looks.schema import (
    MOTION_PHRASES,
    MOTION_WORDS,
    STEPPED_FALSE_CLAIMS,
    Mode,
    ModesFile,
    load_modes,
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


class DraftStepped(_Strict):  # = SteppedTechnique's bounds (schema.py)
    direction: Literal["forward", "reverse"]
    steps_per_cycle: int = Field(ge=3, le=12)
    step_s: float = Field(ge=4.0, le=12.0)
    run_s: float = Field(gt=0, le=180.0)  # ≥ step_s here; ≤ kiosk.idle_return_s in apply

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
        layout: F.FieldSpec  # LinearField | RadialField | SweepField, reused
        sample_at: Literal["centre", "mean"] = "centre"
        brightness: int = Field(ge=1, le=100)  # brightness_draft and every rung's brightness
        solid: int = Field(ge=0)  # index into palette (default_solid)
        stepped: DraftStepped | None = None  # present iff the approved ladder has "stepped"
        copy: DraftCopy = Field(default_factory=DraftCopy)

        @field_validator("mood")
        @classmethod
        def _still_mood(cls, v: str) -> str:
            # The mood is the mode's blurb, shown at every tier (schema.py Mode._ladder), so it never says "moves".
            if m := MOTION_WORDS.search(v):
                raise ValueError(
                    f"the mood can't say {m.group(0)!r}: it shows at every tier, also when nothing moves"
                )
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
@dataclass(frozen=True)
class ConfigTrio:
    scenes: SceneCatalog
    modes: ModesFile
    recipes: RecipesFile

    def dump(self) -> dict:
        return {
            "scenes": self.scenes.model_dump(mode="json"),
            "modes": self.modes.model_dump(mode="json"),
            "recipes": self.recipes.model_dump(mode="json"),
        }


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
def seed_draft(trio: ConfigTrio, scene_id: str) -> SceneDraft:
    """The draft that reproduces the approved scene exactly: Scene (name, mood, description, palette,
    brightness_draft, default_solid), the mode's design (modules.sample.canvas.field, .at), its stepped technique (if
    any); copy "auto"/"auto". KeyError for an unknown scene."""
    raise NotImplementedError("S1: seed_draft")


def is_approved(trio: ConfigTrio, draft: SceneDraft) -> bool:
    """draft == seed_draft(trio, draft.scene_id)."""
    raise NotImplementedError("S1: is_approved")


# ---- the patch (S1) --------------------------------------------------------------------------------------------------
def apply_draft(
    trio: ConfigTrio, draft: SceneDraft, *, canopy: dict, settings: ConductorSettings
) -> ConfigTrio:
    """= apply_drafts(trio, {draft.scene_id: draft}, …)."""
    return apply_drafts(trio, {draft.scene_id: draft}, canopy=canopy, settings=settings)


def apply_drafts(
    trio: ConfigTrio, drafts: Mapping[str, SceneDraft], *, canopy: dict, settings: ConductorSettings
) -> ConfigTrio:
    """The patched trio, scenes processed in catalog order and validated once at the end (§3.2.2, §3.2.3). Raises
    DraftError with every problem (an unknown scene → field "scene_id"; an explicit "approved" copy that is no longer
    true → field "copy.modules"/"copy.stepped")."""
    raise NotImplementedError("S1: apply_drafts")


def draft_changes(approved: ConfigTrio, draft: SceneDraft, *, canopy: dict) -> dict:
    """What the draft changes against the approved scene (§3.2.5): approved_colours, name, mood, description, layout,
    brightness, solid, sample_at, stepped, copy, and the flags needs_d59 / needs_d63 / needs_slot_repaint."""
    raise NotImplementedError("S1: draft_changes")


# ---- recipe keys (§3.2.1) --------------------------------------------------------------------------------------------
def slug(name: str) -> str:
    """lower, [^a-z0-9]+ → "-", strip "-"; "" → "colour"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "colour"


def recipe_keys(
    approved_mode: Mode, draft: SceneDraft, recipes: RecipesFile, owners: Mapping[str, str]
) -> list[str]:
    """One recipe key per draft colour, by the exact allocation of §3.2.1 (stable keys first, then the name, then
    `<scene id>-<name>`); a key nobody can have → DraftError on `palette.<i>.name`."""
    raise NotImplementedError("S1: recipe_keys")


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


def problems_from_config_error(text: str) -> list[Problem]:
    """Each `  • <path>: <message>` bullet of a ConfigError becomes one Problem; `field` from FIELD_FOR ("draft"
    when nothing matches; `palette.<i>` and `stepped.<name>` when the path says which)."""
    raise NotImplementedError("S1: problems_from_config_error")


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


def colour_phrase(names: list[str], *, invert: bool, transform: str) -> str:
    """Lower-cased names in the order they sit from where the layout starts: reversed when invert; with transform
    "mirror" → "<first> at both ends through <last> in the middle"; otherwise "<a>, then <b>, <c> and <d>" (two
    colours: "<a>, then <b>")."""
    raise NotImplementedError("S1: colour_phrase")


def generated_copy(mode: Mode, draft: SceneDraft, canopy: dict) -> dict[str, str]:
    """{"modules": T3_TEMPLATE(...), "strand": STRAND_TEMPLATE(...), "stepped": STEPPED_TEMPLATE(...)}."""
    raise NotImplementedError("S1: generated_copy")


__all__ = [
    "FIELD_FOR",
    "GRID",
    "NUMBER_NAMES",
    "PALETTE_MAX",
    "PALETTE_MIN",
    "STEPPED_TEMPLATE",
    "STRAND_TEMPLATE",
    "T3_TEMPLATE",
    "ConfigTrio",
    "DraftColour",
    "DraftCopy",
    "DraftError",
    "DraftStepped",
    "Problem",
    "SceneDraft",
    "apply_draft",
    "apply_drafts",
    "colour_phrase",
    "draft_changes",
    "generated_copy",
    "is_approved",
    "load_trio",
    "problems_from_config_error",
    "recipe_keys",
    "seed_draft",
    "slug",
]
