"""`config/modes.yaml` as validated data: the kiosk copy block and the Lowcountry Modes, one per scene in the catalog
(02, 04, 06 #12–13; D77).

Rules implemented:
- Every field the file uses is modelled; unknown fields are refused (a typo must not silently do nothing).
- Cross-checks against the rest of the config: every colour a mode names exists in `config/recipes.yaml`; each
  `slot` is one of custom-1..3 and no two modes share one (store-and-select, 04); every ladder entry has its
  technique; a canvas/slot tier needs the mode's slot; module-level (T3) row/column lists match the canopy grid.
- **A sampled module look (T3 `sample`)** paints each module one colour from a canvas (a 2-D `field` or a `gradient`,
  `conductor/looks/field.py`). Its `feature` must use the design's layout words (`field.layout_words`: "column by
  column", "row by row", "diagonally", "from the centre out" only for a radial design centred on the canopy, "from
  one edge", "from two points", …) and no other layout's words, compared without regard to case, so a radial design
  can't say "column by column" and an off-centre one can't say "from the centre out". A design whose module
  centres all sample to one colour (`at: centre`) on the full canopy is refused (it has no layout to describe). A sweep centred
  exactly on a module's centre has no colour there and is refused when sampled at the centre. A fins design (the
  fins' rings, D74) is always "from the centre out" and needs the canopy's fin map: over a canopy without one, each
  canvas that uses it is a plain problem and the checks that would evaluate it are skipped.
- **Copy honesty (05 #12, 07 §6):** a technique's `feature` sentence is shown while that technique runs, so it may
  never promise movement. Only `motion_feature` — used when a bench-enabled motion code is actually running — may
  say "moving" or "animated", and a stepped look's `moving_feature` — shown only while a run is going — may say
  "moves" (never "effect", "animation", "controller", "each/every light" or "270": it is one colour per strand).
- **A stepped look (D65, D75)** re-sends the mode's sampled design a step at a time. It holds on the static design
  between runs, so the ladder needs a later `modules` rung whose sample is the same canvas at the module centres at
  the same brightness (its first step is that look exactly). `mode: travel` (D65) moves the design: its
  `moving_feature` names the flow (`motion_words`: the layout words forward, "toward …" in reverse) and no other
  layout's or flow's words. `mode: breathe` / `shimmer` (D75) keep the design in place and breathe each module's
  colour (`shift`) and/or level (`brightness_swing`): the copy says what changes (`BREATHE_LEVEL_PHRASES` with a
  swing, `BREATHE_COLOUR_PHRASES` without one; never "next colour" when `shift` is 0, never "brighter"/"softer"
  when the swing is 0) and no flow, except a lagged breathe's own start ("from the centre out"). "This scene moves"
  is `Mode.moves` (a stepped rung in the ladder); `Mode.motion` keeps its on-device meaning (07 §6) and is refused
  beside a stepped rung.
- Errors are `ConfigError`s that name the file, the mode and the field, in words a technician can act on.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from conductor.config import CONFIG_DIR, ConfigError, RecipesFile, _read_yaml, load_recipes
from conductor.geometry import load_canopy_config
from conductor.looks import field as F
from conductor.rows import SLOTS

TechniqueName = Literal["stepped", "strand", "canvas", "slot", "modules"]
RecipeKind = Literal["rgb_only", "blend"]
# Words a static look's copy must never use (the kiosk would claim movement that isn't happening).
MOTION_WORDS = re.compile(
    r"\b(mov(e|es|ing)|animat\w*|motion|scroll\w*|chas(e|es|ing)|wave\w*)\b", re.IGNORECASE
)
# What stepped copy may never claim: stepped motion is one colour per strand re-sent through the cloud every few
# seconds, never an on-device effect, a controller animation, a light-by-light (head-by-head, bulb-by-bulb) pattern
# or smooth, continuous movement (whether a strand cuts or fades between steps is bench item 6, not yet settled).
STEPPED_FALSE_CLAIMS = re.compile(
    r"\b(effects?|animat\w*|controllers?|270"
    r"|(?:each|every|all(?: the| \w+)?) (?:heads?|bulbs?|lights?|pixels?|LEDs?)"
    r"|individual\w*|continuous\w*|smooth\w*|seamless\w*|fluid\w*|fad(?:e|es|ed|ing)|glid(?:e|es|ed|ing)"
    r"|shimmer\w*|twinkl\w*|sparkl\w*|chas(?:e|es|ing))\b",
    re.IGNORECASE,
)
# ... and what it must say: the pace, as the generated partial and one-colour copy and the rehearsal copy say it.
STEPPED_PACE = "every few seconds"
# How a breathe/shimmer look's moving copy says what changes (D75): a brightness_swing changes each strand's level,
# a colour-only breath (brightness_swing 0) drifts its colour. None of these matches STEPPED_FALSE_CLAIMS.
BREATHE_LEVEL_PHRASES = ("swells and softens", "swell and soften", "glows and softens", "glow and soften")
BREATHE_COLOUR_PHRASES = ("drifts and settles", "drift and settle")
BREATHE_PHRASES = BREATHE_LEVEL_PHRASES + BREATHE_COLOUR_PHRASES
StepMode = Literal["travel", "breathe", "shimmer"]
_MODE_ID = r"^[a-z0-9][a-z0-9-]{1,40}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---- looks (whole strand) ----------------------------------------------------------------------------------------
class LookSpec(_Strict):
    """A whole-strand look as written in modes.yaml → `rows.Look`. A preset look may leave `preset` out when the
    technique lists candidate `presets` (the first one the device lists is used)."""

    kind: Literal["white", "rgb", "preset", "program", "off"]
    brightness: int = Field(100, ge=1, le=100)
    cct: int | None = Field(None, ge=1000, le=10000)
    rgb: tuple[int, int, int] | None = None
    preset: str | None = None
    program: str | None = None
    speed: int = Field(0, ge=-10, le=10)

    @field_validator("rgb")
    @classmethod
    def _rgb_range(cls, v: tuple[int, int, int] | None) -> tuple[int, int, int] | None:
        if v is not None and not all(0 <= c <= 255 for c in v):
            raise ValueError("rgb values must be whole numbers 0–255")
        return v

    @model_validator(mode="after")
    def _fields_for_kind(self) -> LookSpec:
        if self.kind == "white" and self.cct is None:
            raise ValueError("a white look needs cct (a Kelvin value, e.g. 2700)")
        if self.kind == "rgb" and self.rgb is None:
            raise ValueError("an rgb look needs rgb: [r, g, b]")
        if self.kind == "program" and not self.program:
            raise ValueError("a program look needs program: <name>")
        return self


# ---- canvas (T1) -------------------------------------------------------------------------------------------------
class ColorStop(_Strict):
    at: float = Field(ge=0.0, le=1.0)
    color: str


class ColorLevel(_Strict):
    """A named recipe colour at a level: `colorBrightness` scales the recipe entry's colour AND white brightness."""

    color: str
    colorBrightness: int = Field(100, ge=0, le=100)


class PathSpec(ColorLevel):
    width_in: float = Field(gt=0, le=120)
    amplitude_in: float = Field(0, ge=0, le=120)
    periods: float = Field(1.0, gt=0, le=20)


class BandSpec(ColorLevel):
    width_in: float = Field(gt=0, le=75)


def _ordered_stops(v: list[ColorStop]) -> list[ColorStop]:
    ats = [s.at for s in v]
    if ats != sorted(ats) or len(set(ats)) != len(ats):
        raise ValueError("stops must be listed in increasing `at` order (0.0 … 1.0), each position once")
    return v


class GradientCanvas(_Strict):
    kind: Literal["gradient"]
    axis: Literal["x", "y"]
    stops: list[ColorStop] = Field(min_length=2)
    recipe: RecipeKind = "blend"

    @field_validator("stops")
    @classmethod
    def _ordered(cls, v: list[ColorStop]) -> list[ColorStop]:
        return _ordered_stops(v)


class FieldCanvas(_Strict):
    """Colour stops over a 2-D design field (`conductor/looks/field.py`): linear at any angle, radial, a sweep, or the
    fins' rings."""

    kind: Literal["field"]
    field: F.FieldSpec
    stops: list[ColorStop] = Field(min_length=2)
    recipe: RecipeKind = "blend"

    @field_validator("stops")
    @classmethod
    def _ordered(cls, v: list[ColorStop]) -> list[ColorStop]:
        return _ordered_stops(v)


class PathwayCanvas(_Strict):
    kind: Literal["pathway"]
    base: ColorLevel
    path: PathSpec
    recipe: RecipeKind = "blend"


class BandCanvas(_Strict):
    kind: Literal["band_per_module"]
    base: ColorLevel
    band: BandSpec
    recipe: RecipeKind = "blend"


CanvasSpec = Annotated[GradientCanvas | PathwayCanvas | BandCanvas | FieldCanvas, Field(discriminator="kind")]
SampleCanvas = Annotated[FieldCanvas | GradientCanvas, Field(discriminator="kind")]
# Discriminator tags pydantic puts in an error's location after a union-typed field; `_where` leaves them out.
_UNION_TAGS = {
    "canvas": {"gradient", "pathway", "band_per_module", "field"},
    "field": set(F.FIELD_KINDS),
}


class MotionEffect(_Strict):
    """The on-device animation for a canvas slot. `motionEffect: null` = no code chosen yet (static)."""

    motionEffect: int | None = Field(None, ge=0, le=255)
    motionSpeed: int = Field(50, ge=0, le=100)


# ---- techniques --------------------------------------------------------------------------------------------------
class StrandTechnique(_Strict):
    look: LookSpec
    fallback_look: LookSpec | None = None
    presets: list[str] = Field(default_factory=list)  # candidates, in order, for a preset look
    feature: str = Field(min_length=10)
    fallback_feature: str | None = None

    @model_validator(mode="after")
    def _preset_named(self) -> StrandTechnique:
        if self.look.kind == "preset" and not (self.look.preset or self.presets):
            raise ValueError("a preset look needs preset: <name> or a presets list to choose from")
        if self.fallback_look and self.fallback_look.kind == "preset" and not self.fallback_look.preset:
            raise ValueError("fallback_look: a preset fallback must name its preset")
        return self


class CanvasTechnique(_Strict):
    canvas: CanvasSpec
    brightness: int = Field(100, ge=1, le=100)
    feature: str = Field(min_length=10)
    motion_effect: MotionEffect | None = None
    motion_feature: str | None = None

    @model_validator(mode="after")
    def _motion_copy(self) -> CanvasTechnique:
        if self.motion_effect is not None and not self.motion_feature:
            raise ValueError("motion_effect needs a motion_feature (the copy shown while the motion runs)")
        return self


class SlotTechnique(_Strict):
    brightness: int = Field(100, ge=1, le=100)
    speed: int = Field(0, ge=-10, le=10)
    feature: str = Field(min_length=10)


class ModuleSample(_Strict):
    """T3 sampled from a canvas: each module set to the canvas colour at its centre (`at: centre`) or the mean of its
    30 heads' colours (`at: mean`), as one RGB colour at `brightness` — the same brightness on every module (D60)."""

    canvas: SampleCanvas
    at: Literal["centre", "mean"] = "centre"
    brightness: int = Field(100, ge=1, le=100)

    @model_validator(mode="after")
    def _rgb_only(self) -> ModuleSample:
        if self.canvas.recipe != "rgb_only":
            raise ValueError(
                "a module-level sample sets each strand to one RGB colour (no per-puck white), so its canvas "
                "needs recipe: rgb_only"
            )
        return self


class ModulesTechnique(_Strict):
    """T3: one strand look per canopy module, by row, by column, a middle row against the others, or sampled from a
    canvas at each module (`sample`)."""

    rows: list[LookSpec] | None = None
    columns: list[LookSpec] | None = None
    middle_row: LookSpec | None = None
    others: LookSpec | None = None
    sample: ModuleSample | None = None
    feature: str = Field(min_length=10)

    @model_validator(mode="after")
    def _one_layout(self) -> ModulesTechnique:
        layouts = [
            self.rows is not None,
            self.columns is not None,
            self.middle_row is not None,
            self.sample is not None,
        ]
        if sum(layouts) != 1:
            raise ValueError("give exactly one of rows, columns, middle_row (+ others), or sample")
        if (self.middle_row is None) != (self.others is None):
            raise ValueError("middle_row and others go together")
        if self.rows is not None and not self.rows:
            raise ValueError("rows must list one look per canopy row")
        if self.columns is not None and not self.columns:
            raise ValueError("columns must list one look per canopy column")
        return self


# Per-light moving copy (D80): it says the lights are set one by one, and the pace; never an on-device effect, a
# controller animation or continuous motion (each step is a frame sent through the cloud).
LIGHTS_FALSE_CLAIMS = re.compile(
    r"\b(effects?|animat\w*|controllers?|continuous\w*|seamless\w*|fluid\w*|fad(?:e|es|ed|ing))\b",
    re.IGNORECASE,
)
LIGHTS_PHRASES = ("each light", "every light", "light by light")
LightsEffect = Literal["ripple", "glow", "breathe", "twinkle"]


class LightsMotion(_Strict):
    """Per-light motion (D80): each step sends every driven module a whole frame — each light's colour (the rung's
    canvas at that light, moved `colour_travel` repeats a cycle) and its own level — through the Hubspace cloud. The
    level follows `effect` over `field` (the envelope's geometry; default the canvas's own field), t = the field at
    the light, p = n / steps_per_cycle:

    - `ripple`: rings of brightness rolling `direction` across the field, `rings` of them at once;
    - `glow`: a front that grows from t = 0 out to the far edge and back, lights past it at `floor` (0 = off), its
      edge `softness` wide;
    - `breathe`: every light swells and softens, the far edge `lag` cycles behind the near one (a rolling breath);
    - `twinkle`: each light on its own seeded rhythm (`seed`).

    A light's level runs `floor`…`peak` (its colorBrightness; 0 = off), the module's brightness row is `brightness`.
    `on_at` makes the lights on or off: a light whose level would reach it is sent at `peak`, any other at 0 (Oct 21,
    venue: level 5 looks about as bright as 100 on the canopy, so in-between levels all read as on; `floor` must
    then be 0).
    It runs only where every driven module can take per-light frames (sim; live: `motion.lights`, Gate C and a map),
    else the rung's module-level steps run. `moving_feature` is shown while it runs."""

    effect: LightsEffect
    field: F.FieldSpec | None = None
    direction: Literal["outward", "inward"] = "outward"
    rings: float = Field(1.0, ge=0.25, le=6.0)
    steps_per_cycle: int = Field(16, ge=4, le=64)
    step_s: float = Field(2.0, ge=1.0, le=12.0)
    run_s: float = Field(90.0, gt=0, le=180.0)
    brightness: int = Field(100, ge=1, le=100)
    peak: int = Field(100, ge=1, le=100)
    floor: int = Field(10, ge=0, le=100)
    softness: float = Field(0.25, ge=0.02, le=1.0)
    lag: float = Field(0.5, ge=0.0, le=2.0)
    colour_travel: float = Field(0.0, ge=-2.0, le=2.0)
    on_at: int | None = Field(None, ge=1, le=100)
    seed: int | None = Field(None, ge=0, le=2_147_483_647)
    moving_feature: str = Field(min_length=10)

    @model_validator(mode="after")
    def _honest(self) -> LightsMotion:
        if self.run_s < self.step_s:
            raise ValueError(f"run_s ({self.run_s:g} s) is shorter than one step ({self.step_s:g} s)")
        if self.floor >= self.peak:
            raise ValueError(
                f"floor ({self.floor}) must be below peak ({self.peak}), or no light would change"
            )
        if self.on_at is not None and (self.floor != 0 or self.on_at > self.peak):
            raise ValueError(
                f"on_at ({self.on_at}) sends each light at peak or off: it needs floor 0 (not {self.floor}) and must "
                f"be at most peak ({self.peak})"
            )
        if (self.effect == "twinkle") != (self.seed is not None):
            raise ValueError(
                "seed is for effect: twinkle only, and twinkle needs one (each light's own rhythm)"
            )
        copy = self.moving_feature.lower()
        if m := LIGHTS_FALSE_CLAIMS.search(self.moving_feature):
            raise ValueError(
                f"moving_feature says {m.group(0)!r}: per-light motion is a frame of each light's colour and level "
                "sent through the Hubspace cloud a step at a time, not an on-device effect or continuous motion"
            )
        if not any(p in copy for p in LIGHTS_PHRASES):
            raise ValueError(
                f"moving_feature must say the lights are set one by one ({' or '.join(map(repr, LIGHTS_PHRASES))})"
            )
        if "through the hubspace cloud" not in copy or STEPPED_PACE not in copy:
            raise ValueError(
                f"moving_feature must say it is sent 'through the Hubspace cloud' and {STEPPED_PACE!r}"
            )
        return self


class SteppedTechnique(_Strict):
    """Module level, stepped: the mode's sampled design (`canvas`, the same canvas as its `modules` sample) re-sent
    a step at a time, each step one colour (and one brightness) per module sent through the cloud, `step_s` apart,
    for `run_s` seconds; then the static design (the `modules` rung) holds. Step 0 is the still design in every
    mode. `moving_feature` is shown only while a run is going, so it alone may say "moves"; it may never claim an
    on-device effect or light-by-light animation.

    - `mode: travel` (D65, the default): the design shifted `steps_per_cycle` steps per repeat. `forward` flows the
      way the design's layout words read ("from the centre out" moves outwards); `reverse` flows back ("toward the
      centre"). The copy must name the flow (`motion_words`). There is no separate wavelength: the canvas field's
      own `cycles` is the repeat a cycle of steps moves by, so step 0 stays exactly the static design (the hold).
    - `mode: breathe` (D75): the design stays in place. Each module's colour moves up to `shift` along the design's
      t (the geometry before mirror and invert, clamped, never wrapped; `direction` is the sign: `forward` moves the
      way travel's phase does) and its brightness up to `brightness_swing` points either side of `brightness`: one
      breath of `steps_per_cycle` steps per module (up, back, down, back), the design's start (t = 0) leading by up
      to `lag_steps` steps. The copy says what changes (`breathe_words`).
    - `mode: shimmer` (D75): breathe with each module's start picked by `seed` (the same on every run), up to
      `lag_steps` apart.

    travel never takes the breathe parameters; a value counts as given only when it isn't its default (`shift: 0`
    on a travel rung loads)."""

    canvas: FieldCanvas
    mode: StepMode = "travel"
    direction: Literal["forward", "reverse"] = "forward"  # breathe/shimmer: the sign of the colour offset
    steps_per_cycle: int = Field(6, ge=3, le=12)  # breathe/shimmer: the steps in ONE module's breath
    step_s: float = Field(6.0, ge=4.0, le=12.0)  # 4 s: a step's verification takes about 2.6 s (D65)
    run_s: float = Field(90.0, gt=0, le=180.0)
    brightness: int = Field(100, ge=1, le=100)
    shift: float = Field(0.0, ge=0.0, le=0.5)
    brightness_swing: int = Field(0, ge=0, le=20)
    lag_steps: int = Field(0, ge=0, le=6)
    seed: int | None = Field(None, ge=0, le=2_147_483_647)
    moving_feature: str = Field(min_length=10)
    # D80: per-light motion where every driven module can take frames; the fields above are its module-level steps
    lights: LightsMotion | None = None

    @model_validator(mode="after")
    def _honest(self) -> SteppedTechnique:
        if self.run_s < self.step_s:
            raise ValueError(
                f"run_s ({self.run_s:g} s) is shorter than one step ({self.step_s:g} s), so a run would never "
                "move while the moving copy shows"
            )
        if m := STEPPED_FALSE_CLAIMS.search(self.moving_feature):
            raise ValueError(
                f"moving_feature says {m.group(0)!r}, which stepped motion isn't: each strand is sent one colour "
                "at a time through the Hubspace cloud every few seconds (no on-device effect, no light-by-light or "
                "smooth animation)"
            )
        if STEPPED_PACE not in self.moving_feature.lower():
            raise ValueError(
                f"moving_feature must say the strands change {STEPPED_PACE!r} (one colour per strand, sent through "
                "the Hubspace cloud), so no guest reads it as continuous motion"
            )
        return self

    @model_validator(mode="after")
    def _mode_fields(self) -> SteppedTechnique:
        """Each mode takes only its own parameters, and a breath's copy says what changes (D75)."""
        mode, shift, swing, lag, b = (
            self.mode,
            self.shift,
            self.brightness_swing,
            self.lag_steps,
            self.brightness,
        )
        if mode == "travel":
            # "set" = not the default, so an explicit `shift: 0` on a travel rung loads (the export adds lines)
            if shift != 0 or swing != 0 or lag != 0 or self.seed is not None:
                raise ValueError(
                    "mode: travel moves the design itself, so shift, brightness_swing, lag_steps and seed don't "
                    "apply; leave them out, or use mode: breathe or shimmer"
                )
            return self
        if shift == 0 and swing == 0:
            raise ValueError(
                f"mode: {mode} needs a shift (up to 0.5) or a brightness_swing (up to 20 points), or nothing "
                "would breathe"
            )
        if self.seed is not None and mode != "shimmer":
            raise ValueError(
                "seed is for mode: shimmer only (it picks each module's own rhythm); leave it out"
            )
        if mode == "shimmer" and self.seed is None:
            raise ValueError(
                "mode: shimmer needs a seed (any whole number: it picks each module's own rhythm, the same on "
                "every run)"
            )
        if mode == "shimmer" and lag == 0:
            raise ValueError(
                "mode: shimmer needs lag_steps of at least 1 (how far apart the modules' rhythms start); with 0 "
                "every module breathes together, which is mode: breathe"
            )
        if b - swing < 1 or b + swing > 100:
            raise ValueError(
                f"brightness {b} ± brightness_swing {swing} would run {b - swing}–{b + swing}, outside 1–100; "
                "lower the swing or the brightness"
            )
        copy = self.moving_feature.lower()
        if swing > 0 and not any(p in copy for p in BREATHE_LEVEL_PHRASES):
            raise ValueError(
                "moving_feature must say how the canopy breathes: with a brightness_swing, "
                f"{' or '.join(map(repr, BREATHE_LEVEL_PHRASES))} (mode: {mode}; nothing travels)"
            )
        if swing == 0:
            # a level claim first: it is the more specific mistake when the copy also lacks the colour words
            word = next((w for w in (*BREATHE_LEVEL_PHRASES, "brighter", "softer") if w in copy), None)
            if word is not None:
                raise ValueError(
                    f"moving_feature says {word!r}, but brightness_swing is 0, so no strand gets brighter or softer"
                )
            if not any(p in copy for p in BREATHE_COLOUR_PHRASES):
                raise ValueError(
                    "moving_feature must say how the canopy breathes: a colour-only breath (brightness_swing 0) "
                    f"says {' or '.join(map(repr, BREATHE_COLOUR_PHRASES))} (mode: {mode}; nothing travels)"
                )
        if shift == 0 and "next colour" in copy:
            raise ValueError(
                "moving_feature says 'next colour', but shift is 0, so every strand keeps its colour and only its "
                "level changes; say 'a brighter or softer level'"
            )
        return self


def breathe_words(tech: SteppedTechnique) -> str | None:
    """The phrase a breathe/shimmer look's moving copy uses for what changes (D75): the first of
    `BREATHE_LEVEL_PHRASES` it contains when it has a brightness_swing, else of `BREATHE_COLOUR_PHRASES`. None for
    travel (its copy names a flow instead, `motion_words`) or when the copy has none."""
    if tech.mode == "travel":
        return None
    family = BREATHE_LEVEL_PHRASES if tech.brightness_swing > 0 else BREATHE_COLOUR_PHRASES
    copy = tech.moving_feature.lower()
    return next((p for p in family if p in copy), None)


class Techniques(_Strict):
    stepped: SteppedTechnique | None = None
    strand: StrandTechnique | None = None
    canvas: CanvasTechnique | None = None
    slot: SlotTechnique | None = None
    modules: ModulesTechnique | None = None


# ---- modes -------------------------------------------------------------------------------------------------------
class Mode(_Strict):
    label: str = Field(min_length=1, max_length=40)
    blurb: str = Field(min_length=1, max_length=120)
    preview: list[Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]] = Field(min_length=1, max_length=6)
    ladder: list[TechniqueName] = Field(min_length=1)
    slot: Literal["custom-1", "custom-2", "custom-3"] | None = None
    # the look re-triggers all modules as one burst or none (07 §6): on-device motion only. A stepped look is never
    # one (the Conductor holds its phase), so this stays false beside a stepped rung; "this scene moves" is `moves`.
    motion: bool = False
    techniques: Techniques

    @property
    def moves(self) -> bool:
        """Whether this scene moves (D75): it has a stepped rung in its ladder. Not `motion` (07 §6's burst flag)."""
        return "stepped" in self.ladder

    @model_validator(mode="after")
    def _ladder(self) -> Mode:
        if len(set(self.ladder)) != len(self.ladder):
            raise ValueError("ladder lists a technique twice")
        for name in self.ladder:
            if getattr(self.techniques, name) is None:
                raise ValueError(f"ladder lists {name!r} but techniques has no {name} section")
        if {"canvas", "slot"} & set(self.ladder) and self.slot is None:
            raise ValueError("a canvas or slot tier needs slot: custom-1, custom-2 or custom-3")
        if "stepped" in self.ladder:
            self._stepped_holds()
            if self.motion:
                raise ValueError(
                    "motion: true is the on-device burst flag (07 §6), which a stepped look never uses; a scene "
                    "moves through its stepped rung (D75), so leave motion out"
                )
        if m := MOTION_WORDS.search(self.blurb):
            raise ValueError(
                f"blurb mentions movement ({m.group(0)!r}); the blurb shows at every tier, so it may not"
            )
        for name in ("strand", "canvas", "slot", "modules"):
            tech = getattr(self.techniques, name)
            if tech is None:
                continue
            for field in ("feature", "fallback_feature"):
                text = getattr(tech, field, None)
                if text and (m := MOTION_WORDS.search(text)):
                    raise ValueError(
                        f"techniques → {name} → {field} mentions movement ({m.group(0)!r}); only "
                        "motion_feature may, because it is shown only while a motion effect really runs"
                    )
        return self

    def _stepped_holds(self) -> None:
        """A stepped run ends on the mode's static module-level design, so the hold is that rung exactly: a later
        `modules` rung sampling the same canvas at the module centres at the same brightness."""
        stepped, mods = self.techniques.stepped, self.techniques.modules
        assert stepped is not None
        later = self.ladder[self.ladder.index("stepped") + 1 :]
        if "modules" not in later or mods is None or mods.sample is None:
            raise ValueError(
                "a stepped tier holds the mode's module-level design between runs, so the ladder needs a later "
                "'modules' rung with a sample of the same canvas"
            )
        if mods.sample.canvas != stepped.canvas:
            raise ValueError(
                "techniques → stepped → canvas must be the modules sample's canvas (the same design), so the "
                "first step is the static look it holds"
            )
        if mods.sample.at != "centre":
            raise ValueError(
                "a stepped tier samples each module's centre, so its modules sample needs at: centre (the hold "
                "must be its first step)"
            )
        if mods.sample.brightness != stepped.brightness:
            raise ValueError(
                f"techniques → stepped → brightness ({stepped.brightness}) must be the modules sample's "
                f"({mods.sample.brightness}), so the first step is the static look it holds"
            )


class KioskCopy(BaseModel):
    """Guest-facing strings. Extra string keys are allowed so the kiosk can add copy without a schema change."""

    model_config = ConfigDict(extra="allow")

    title: str
    prompt: str
    tagline: str
    resting: str
    updating: str
    ready: str


class ModesFile(_Strict):
    kiosk: KioskCopy
    modes: dict[Annotated[str, Field(pattern=_MODE_ID)], Mode] = Field(min_length=1)

    def mode(self, mode_id: str) -> Mode:
        try:
            return self.modes[mode_id]
        except KeyError:
            raise KeyError(f"no mode {mode_id!r}; known: {', '.join(self.modes)}") from None


# ---- loading + cross-checks --------------------------------------------------------------------------------------
def _where(loc: tuple[Any, ...]) -> str:
    parts = list(loc)
    # a discriminated union's tag follows the field it belongs to (… canvas → field → field → radial → cycles)
    parts = [p for i, p in enumerate(parts) if not (i and p in _UNION_TAGS.get(parts[i - 1], ()))]
    if len(parts) >= 2 and parts[0] == "modes":
        head = f"mode {parts[1]!r}"
        rest = parts[2:]
        return " → ".join([head, *(f"[{p}]" if isinstance(p, int) else str(p) for p in rest)])
    return " → ".join(f"[{p}]" if isinstance(p, int) else str(p) for p in parts) or "(top level)"


def _explain(path: Path | str, err: ValidationError) -> str:
    lines = [f"{path} has {err.error_count()} problem(s):"]
    for e in err.errors():
        msg = e["msg"].removeprefix("Value error, ")
        lines.append(f"  • {_where(tuple(e['loc']))}: {msg}")
    lines.append("Fix the file and restart.")
    return "\n".join(lines)


def _canvas_refs(c: Any, at: str) -> list[tuple[str, str]]:
    if isinstance(c, GradientCanvas | FieldCanvas):
        return [(f"{at} → stops → [{i}] → color", s.color) for i, s in enumerate(c.stops)]
    if isinstance(c, PathwayCanvas):
        return [(f"{at} → base → color", c.base.color), (f"{at} → path → color", c.path.color)]
    if isinstance(c, BandCanvas):
        return [(f"{at} → base → color", c.base.color), (f"{at} → band → color", c.band.color)]
    return []


def _colour_refs(mode: Mode) -> list[tuple[str, str]]:
    """(where, colour name) for every recipe colour a mode names."""
    out: list[tuple[str, str]] = []
    if mode.techniques.canvas is not None:
        out += _canvas_refs(mode.techniques.canvas.canvas, "techniques → canvas → canvas")
    mods = mode.techniques.modules
    if mods is not None and mods.sample is not None:
        out += _canvas_refs(mods.sample.canvas, "techniques → modules → sample → canvas")
    if mode.techniques.stepped is not None:
        out += _canvas_refs(mode.techniques.stepped.canvas, "techniques → stepped → canvas")
    return out


def layout_words(canvas: FieldCanvas | GradientCanvas, canopy: dict | None) -> str | None:
    """How copy must describe a sampled canvas's layout (`field.layout_words`; a gradient is linear at 0 or 90°).
    None when that depends on the canopy (a radial or sweep design's centre) and there is none to check against; a
    fins design's words ("from the centre out") never do."""
    if isinstance(canvas, GradientCanvas):
        return "column by column" if canvas.axis == "x" else "row by row"
    if isinstance(canvas.field, F.LinearField | F.FinsField) or canopy is not None:
        return F.layout_words(canvas.field, canopy or {})
    return None


def _toward(words: str) -> str:
    """The layout words for a flow back towards where the design starts."""
    if words == "from the centre out":
        return "toward the centre"
    return "toward " + words.removeprefix("from ") if words.startswith("from ") else words


# Every phrase `motion_words` can return: moving copy for one flow may not use another flow's or layout's words.
MOTION_PHRASES = tuple(dict.fromkeys([*F.LAYOUT_PHRASES, *(_toward(p) for p in F.LAYOUT_PHRASES)]))


def motion_words(canvas: FieldCanvas, canopy: dict | None, direction: str) -> str | None:
    """How a stepped look's moving copy must name its flow: the layout words going forward ("from the centre out",
    "from one edge", "column by column", …), and towards the start in reverse ("toward the centre", "toward one
    edge"; a line or a sweep reads the same both ways). None when that depends on a canopy there isn't."""
    words = layout_words(canvas, canopy)
    if words is None or direction == "forward":
        return words
    return _toward(words)


def _lag_problems(mode_id: str, stepped: SteppedTechnique, canopy: dict | None) -> list[str]:
    """A lagged breath (a shimmer, or a breathe with lag_steps > 0) whose starts (`compile.module_lags` over the
    canopy's nine modules) all come out equal breathes in unison, so its copy would be false: a shimmer's "on their
    own", a breathe's lead words (review B-R6-1). The lag_steps-0 refusals' twin: a seed or a layout can collapse the
    lags too. Unchecked without a canopy (a breathe's starts are the design's t at the module centres)."""
    if canopy is None or stepped.mode == "travel" or stepped.lag_steps == 0:
        return []
    from conductor.looks.compile import module_lags  # compile imports this module

    grid = canopy["orientations"][canopy["orientation_scheme"]]
    names = [f"r{r}c{c}" for r, row in enumerate(grid) for c in range(len(row))]
    if len(set(module_lags(stepped, names, canopy).values())) > 1:
        return []
    at = f"mode {mode_id!r} → techniques → stepped"
    lag = stepped.lag_steps
    if stepped.mode == "shimmer":
        return [
            (
                f"{at} → seed: lag_steps {lag} with seed {stepped.seed} starts every module together, so the canopy "
                "breathes as one, not on each module's own rhythm (that is mode: breathe with lag_steps 0); pick "
                "another seed or a larger lag_steps"
            )
        ]
    start = motion_words(stepped.canvas, canopy, "forward")
    return [
        (
            f"{at} → lag_steps: lag_steps {lag} starts every module together on this design (its t rounds to the "
            f"same step at every module centre), so the breath doesn't start {start!r}; raise lag_steps, or set it "
            "to 0 and leave the lead words out of the copy"
        )
    ]


def _stepped_problems(mode_id: str, stepped: SteppedTechnique, canopy: dict | None) -> list[str]:
    """A travel look's moving copy names its flow (`motion_words`) and no other. A breathe/shimmer look doesn't
    travel, so its copy names no flow, except a lagged breathe's own start (its forward layout words: the design's
    start leads the breath); and a lagged breath's modules don't all start together (`_lag_problems`)."""
    at = f"mode {mode_id!r} → techniques → stepped → moving_feature"
    if stepped.mode != "travel":
        lagged = _lag_problems(mode_id, stepped, canopy)
        text = stepped.moving_feature.lower()
        allowed: set[str] = set()
        if stepped.mode == "breathe" and stepped.lag_steps > 0:
            start = motion_words(stepped.canvas, canopy, "forward")
            # a radial or sweep design's start depends on a canopy there isn't: unchecked, as travel's flow is
            if start is None:
                return lagged
            allowed = {start}
        others = [p for p in MOTION_PHRASES if p not in allowed and p in text]
        if others and allowed:
            return [
                *lagged,
                (
                    f"{at}: the breath starts {next(iter(allowed))!r} (lag_steps {stepped.lag_steps}), so the "
                    f"copy may say that but not {', '.join(map(repr, others))}"
                ),
            ]
        if others:
            return [
                *lagged,
                (
                    f"{at}: a {stepped.mode} look doesn't travel, so the copy may not say "
                    f"{', '.join(map(repr, others))} (it reads {stepped.moving_feature!r})"
                ),
            ]
        return lagged
    words = motion_words(stepped.canvas, canopy, stepped.direction)
    if words is None:
        return []
    text = stepped.moving_feature.lower()
    out = []
    if words not in text:
        out.append(
            f"{at}: the design flows {words!r} ({stepped.direction}), so the copy must say {words!r} "
            f"(it reads {stepped.moving_feature!r})"
        )
    others = [p for p in MOTION_PHRASES if p != words and p in text]
    if others:
        out.append(
            f"{at}: the design flows {words!r}, but the copy also says "
            f"{', '.join(repr(p) for p in others)}, which it doesn't"
        )
    return out


def _one_colour(sample: ModuleSample, recipes: RecipesFile, canopy: dict, names: list[str]) -> bool:
    """Whether the canvas has the same colour at every module centre (only asked when its colours are all known)."""
    from conductor.looks.composer import evaluate  # composer imports this module

    colours = {
        evaluate(sample.canvas, x, y, canopy=canopy, recipes=recipes)
        for x, y in F.module_centres(canopy, names).values()
    }
    return len(colours) < 2


def _sample_problems(
    mode_id: str, mods: ModulesTechnique, recipes: RecipesFile, canopy: dict | None
) -> list[str]:
    """A sampled module look's copy names its layout (and no other); its design has more than one colour across the
    module centres; a sweep isn't centred on a module it samples at the centre."""
    sample = mods.sample
    if sample is None:
        return []
    out = []
    at = f"mode {mode_id!r} → techniques → modules"
    words = layout_words(sample.canvas, canopy)
    feature = mods.feature.lower()
    if words is not None and words not in feature:
        out.append(
            f"{at} → feature: the sampled design is laid out {words!r}, so the copy must say {words!r} "
            f"(it reads {mods.feature!r})"
        )
    others = [p for p in F.LAYOUT_PHRASES if p != words and p in feature]
    if words is not None and others:
        out.append(
            f"{at} → feature: the sampled design is laid out {words!r}, but the copy also says "
            f"{', '.join(repr(p) for p in others)}, which it isn't"
        )
    if canopy is None:
        return out
    grid = canopy["orientations"][canopy["orientation_scheme"]]
    names = [f"r{r}c{c}" for r, row in enumerate(grid) for c in range(len(row))]
    known = all(s.color in recipes.colors for s in sample.canvas.stops)
    if sample.at == "centre" and known and _one_colour(sample, recipes, canopy, names):
        out.append(
            f"{at} → sample: the design has the same colour at every module centre, so module level shows one "
            "colour, not a layout; change the field (fewer cycles, another centre), the stops, or use at: mean"
        )
    spec = sample.canvas.field if isinstance(sample.canvas, FieldCanvas) else None
    if isinstance(spec, F.SweepField) and sample.at == "centre":
        for pos, centre in F.module_centres(canopy, names).items():
            if tuple(centre) == tuple(spec.centre_in):
                out.append(
                    f"{at} → sample: the sweep is centred exactly on module {pos}'s centre, where it has no "
                    "direction (that module would just show the first stop); move centre_in or sample with at: mean"
                )
    return out


def _fin_map_problems(mode_id: str, mode: Mode, canopy: dict | None) -> list[str]:
    """A fins design over a canopy that has no fin map can't be evaluated anywhere: one sentence per canvas that
    uses one (none when there is no canopy to check against, or it has fins)."""
    if canopy is None or F.has_fins(canopy):
        return []
    tech = mode.techniques
    mods = tech.modules
    canvases = [
        ("techniques → canvas → canvas", tech.canvas.canvas if tech.canvas is not None else None),
        (
            "techniques → modules → sample → canvas",
            mods.sample.canvas if mods is not None and mods.sample is not None else None,
        ),
        ("techniques → stepped → canvas", tech.stepped.canvas if tech.stepped is not None else None),
    ]
    return [
        f"mode {mode_id!r} → {where} → field: {F.NO_FIN_MAP}"
        for where, canvas in canvases
        if isinstance(canvas, FieldCanvas) and isinstance(canvas.field, F.FinsField)
    ]


def cross_check(modes: ModesFile, recipes: RecipesFile, canopy: dict | None = None) -> list[str]:
    """Problems between modes.yaml and recipes.yaml / canopy.yaml, as plain sentences (empty = fine)."""
    problems: list[str] = []
    known = sorted(recipes.colors)
    slots: dict[str, str] = {}
    grid = None
    if canopy is not None:
        grid = canopy["orientations"][canopy["orientation_scheme"]]
    for mode_id, mode in modes.modes.items():
        for where, name in _colour_refs(mode):
            if name not in recipes.colors:
                problems.append(
                    f"mode {mode_id!r} → {where}: unknown colour {name!r}; "
                    f"use one of {', '.join(known)} (config/recipes.yaml)"
                )
        if mode.slot is not None:
            if mode.slot not in SLOTS:
                problems.append(f"mode {mode_id!r} → slot: {mode.slot!r} isn't one of {', '.join(SLOTS)}")
            elif mode.slot in slots:
                problems.append(
                    f"mode {mode_id!r} → slot: {mode.slot} is already used by mode {slots[mode.slot]!r}; "
                    "each painted look needs its own slot"
                )
            else:
                slots[mode.slot] = mode_id
        mods = mode.techniques.modules
        # a fins design without a fin map: said once, and the checks that would evaluate the field are skipped
        no_fins = _fin_map_problems(mode_id, mode, canopy)
        problems += no_fins
        if mods is not None and not no_fins:
            problems += _sample_problems(mode_id, mods, recipes, canopy)
        if mode.techniques.stepped is not None and not no_fins:
            problems += _stepped_problems(mode_id, mode.techniques.stepped, canopy)
        if mods is not None and grid is not None:
            if mods.rows is not None and len(mods.rows) != len(grid):
                problems.append(
                    f"mode {mode_id!r} → techniques → modules → rows: lists {len(mods.rows)} looks but the "
                    f"canopy has {len(grid)} module rows"
                )
            if mods.columns is not None and len(mods.columns) != len(grid[0]):
                problems.append(
                    f"mode {mode_id!r} → techniques → modules → columns: lists {len(mods.columns)} looks but "
                    f"the canopy has {len(grid[0])} module columns"
                )
    return problems


def parse_modes(
    data: Any, *, recipes: RecipesFile, canopy: dict | None = None, path: Path | str = "modes.yaml"
) -> ModesFile:
    """Validate already-loaded YAML data. ConfigError (naming the mode and field) on any problem."""
    try:
        modes = ModesFile.model_validate(data)
    except ValidationError as e:
        raise ConfigError(_explain(path, e)) from None
    problems = cross_check(modes, recipes, canopy)
    if problems:
        lines = [f"{path} has {len(problems)} problem(s):", *(f"  • {p}" for p in problems)]
        lines.append("Fix the file and restart.")
        raise ConfigError("\n".join(lines))
    return modes


def load_modes(config_dir: Path = CONFIG_DIR) -> ModesFile:
    """Load and check `config/modes.yaml` against `recipes.yaml` (and `canopy.yaml` when present, through the
    canopy loader, so with its fin map; a broken canopy.yaml or fins.json is a ConfigError naming the file)."""
    config_dir = Path(config_dir)
    path = config_dir / "modes.yaml"
    if not path.exists():
        raise ConfigError(f"{path} is missing. The Lowcountry Modes live there; restore it from git.")
    recipes = load_recipes(config_dir)
    canopy = None
    canopy_path = config_dir / "canopy.yaml"
    if canopy_path.exists():
        canopy = load_canopy_config(canopy_path)
    return parse_modes(_read_yaml(path), recipes=recipes, canopy=canopy, path=path)
