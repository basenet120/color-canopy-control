"""One-tap test cards for Prototype 01, and the paint helpers they share with the Paint tab (04 §13, 06 #7).

A card compiles to either a whole-strand `Look` (T3, always available) or a frame of per-head `FrameEntry`s
(T1: written into a slot and selected in one PUT). Colours come from `config/recipes.yaml` — never from code — and
every value there is UNVERIFIED until the Prototype 01 colour study.

Cards (ids are stable; the page renders each one):
- `white`          full white at 2700 / 3000 / 4000 K, clamped to the device's range with a note.
- `color`          a show colour (HD orange, amber, peach, warm ivory, …) on the whole strand, or painted into
                   every head (T1) with the `rgb_only` or `blend` recipe.
- `recipe-compare` left half `rgb_only`, right half `blend`, same target colour; the centre column stays dark
                   as a divider (T1).
- `thirds`         three bands across X, each with its own colour and brightness, for Mylar density zones (T1).
- `hue-ramp`       head i at hue 12°·i, for photographing the index map (T1).
- `index-sweep`    lights head i in sequence — run through the sweep endpoint, one frame write per step (T1).
- `single-head`    one head on, the rest off (T1).
- `lowcountry-preview`  one Lowcountry Mode's slice for a chosen canopy position (r0c0 … r2c2), shown on THIS
                   module: the mode's T1 canvas frame for that position (one frame write + select into the page's
                   scratch slot), or its whole-strand / module-level (T3) look when T1 isn't allowed here. The
                   session runs it (`ModuleSession.preview_mode`): it needs the compiler, not just recipes.
"""

from __future__ import annotations

import colorsys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from conductor.capabilities import Capabilities
from conductor.config import ColorRecipe, RecipesFile
from conductor.rows import FRAME_FIELDS, FrameEntry, Look, PayloadError

OFF = FrameEntry(0, 0, 0, 0, 0, 0)
WHITE_HEAD = FrameEntry(255, 255, 255, 0, 100, 0)  # the sweep / single-head marker: plain RGB white
WHITE_CARD_KELVINS = (2700, 3000, 4000)
SHOW_COLORS = ("hd-orange", "amber", "peach", "warm-ivory")
RECIPE_KINDS = ("rgb_only", "blend")
HUE_STEP_DEG = 12


@dataclass
class Compiled:
    """What a card turned into. Exactly one of `look` / `frame` is set (a sweep card sets neither)."""

    label: str  # telemetry label and the Hold name
    look: Look | None = None
    frame: list[FrameEntry] | None = None
    notes: list[str] | None = None
    sweep: dict | None = None  # index-sweep: parameters for the sweep endpoint


# ---- the catalogue ---------------------------------------------------------------------------------------------
def catalog(recipes: RecipesFile, modes: Any = None, canopy: Mapping[str, Any] | None = None) -> list[dict]:
    """Every card with its title, plain-English description, whether it needs T1, and default params.

    `modes` (a `looks.ModesFile`) and `canopy` feed the Lowcountry preview card; without them it is shown as
    unavailable."""
    colors = [{"id": k, "label": v.label, "hex": v.hex} for k, v in recipes.colors.items()]
    show = [c for c in colors if c["id"] in SHOW_COLORS]
    return [
        {
            "id": "white",
            "title": "Full white",
            "description": "The whole strand in tunable white at full brightness.",
            "needs_t1": False,
            "options": {"kelvin": list(WHITE_CARD_KELVINS)},
            "defaults": {"kelvin": 2700},
        },
        {
            "id": "color",
            "title": "Show colours",
            "description": "A show colour on the whole strand, or painted into every head (painted needs T1).",
            "needs_t1": False,
            "options": {"color": show, "all_colors": colors, "recipe": list(RECIPE_KINDS)},
            "defaults": {"color": "hd-orange", "painted": False, "recipe": "blend"},
        },
        {
            "id": "recipe-compare",
            "title": "Recipe compare",
            "description": "Same colour two ways: left half RGB only, right half RGB + warm white. "
            "The middle column stays dark as a divider.",
            "needs_t1": True,
            "options": {"color": colors},
            "defaults": {"color": "hd-orange"},
        },
        {
            "id": "thirds",
            "title": "Thirds split",
            "description": "Three bands across the module, each with its own colour and brightness — "
            "for comparing Mylar density zones.",
            "needs_t1": True,
            "options": {"color": colors, "recipe": list(RECIPE_KINDS)},
            "defaults": {
                "bands": [
                    {"color": "warm-ivory", "recipe": "blend", "brightness": 100},
                    {"color": "warm-ivory", "recipe": "blend", "brightness": 100},
                    {"color": "warm-ivory", "recipe": "blend", "brightness": 100},
                ]
            },
        },
        {
            "id": "hue-ramp",
            "title": "Hue ramp",
            "description": "Head i at hue 12° × i: photograph it to check the wire order.",
            "needs_t1": True,
            "options": {},
            "defaults": {},
        },
        {
            "id": "index-sweep",
            "title": "Index sweep",
            "description": "Lights one head at a time in wire order, to check the wiring. "
            "One write per step, never faster than the rate budget.",
            "needs_t1": True,
            "options": {},
            "defaults": {},
        },
        {
            "id": "single-head",
            "title": "Single head",
            "description": "One head on (choose which), everything else off.",
            "needs_t1": True,
            "options": {},
            "defaults": {"index": 0},
        },
        lowcountry_card(modes, canopy),
    ]


def lowcountry_card(modes: Any, canopy: Mapping[str, Any] | None) -> dict:
    """The "Preview a Lowcountry Mode" card: every mode and the nine canopy positions to choose from."""
    card: dict[str, Any] = {
        "id": "lowcountry-preview",
        "title": "Preview a Lowcountry Mode",
        "needs_t1": False,
    }
    if modes is None or canopy is None:
        return {
            **card,
            "description": "The Lowcountry Modes couldn't be loaded (see the Conductor log).",
            "placeholder": True,
            "options": {},
            "defaults": {},
        }
    grid = canopy["orientations"][canopy["orientation_scheme"]]
    return {
        **card,
        "description": "Choose a mode and where on the canopy this module would hang: it shows that "
        "module's slice of the look — painted per head (T1) where allowed, else the module-level look.",
        "options": {
            "modes": [
                {
                    "id": k,
                    "label": m.label,
                    "blurb": m.blurb,
                    "preview": list(m.preview),
                    "ladder": list(m.ladder),
                }
                for k, m in modes.modes.items()
            ],
            "positions": [
                [{"id": f"r{r}c{c}", "orientation": str(o)} for c, o in enumerate(row)]
                for r, row in enumerate(grid)
            ],
        },
        "defaults": {"mode": next(iter(modes.modes)), "position": f"r{len(grid) // 2}c{len(grid[0]) // 2}"},
    }


# ---- helpers ---------------------------------------------------------------------------------------------------
def clamp_cct(kelvin: int, caps: Capabilities) -> tuple[int, str | None]:
    """`kelvin` clamped to the device's range, and a note when it had to move."""
    if caps.cct_range is None:
        raise PayloadError("this device doesn't list a colour temperature range, so white can't be set")
    lo, hi = caps.cct_range
    clamped = max(lo, min(hi, int(kelvin)))
    if clamped != kelvin:
        return clamped, f"{kelvin} K is outside this device's range ({lo}–{hi} K); sent {clamped} K instead."
    return clamped, None


def _recipe(recipes: RecipesFile, name: Any) -> ColorRecipe:
    if not isinstance(name, str) or name not in recipes.colors:
        known = ", ".join(recipes.colors)
        raise PayloadError(f"unknown colour {name!r}; use one of {known}")
    return recipes.colors[name]


def recipe_entry(
    recipes: RecipesFile, name: Any, kind: Any, caps: Capabilities, notes: list[str]
) -> FrameEntry:
    """The frame entry for a colour recipe (`rgb_only` or `blend`), with its white clamped to the device range."""
    if kind not in RECIPE_KINDS:
        raise PayloadError(f"recipe must be {' or '.join(RECIPE_KINDS)}")
    entry = getattr(_recipe(recipes, name), kind)
    fe = FrameEntry(**entry.model_dump())
    if fe.cct and caps.cct_range:
        cct, note = clamp_cct(fe.cct, caps)
        if note:
            notes.append(f"{name} {kind}: {note}")
            fe = FrameEntry(fe.r, fe.g, fe.b, cct, fe.colorBrightness, fe.whiteBrightness)
    return fe


def scaled(entry: FrameEntry, pct: int) -> FrameEntry:
    """The entry at `pct`% of its own colour and white brightness."""
    if not isinstance(pct, int) or not 0 <= pct <= 100:
        raise PayloadError("brightness must be a whole number 0–100")
    return FrameEntry(
        entry.r,
        entry.g,
        entry.b,
        entry.cct,
        round(entry.colorBrightness * pct / 100),
        round(entry.whiteBrightness * pct / 100),
    )


def hue_entry(index: int) -> FrameEntry:
    r, g, b = colorsys.hsv_to_rgb(((HUE_STEP_DEG * index) % 360) / 360.0, 1.0, 1.0)
    return FrameEntry(round(r * 255), round(g * 255), round(b * 255), 0, 100, 0)


def single_head(n: int, index: int, entry: FrameEntry = WHITE_HEAD) -> list[FrameEntry]:
    if not isinstance(index, int) or not 0 <= index < n:
        raise PayloadError(f"head index must be 0–{n - 1}")
    return [entry if i == index else OFF for i in range(n)]


def entry_from(d: Any) -> FrameEntry:
    """A FrameEntry from request JSON (missing fields take FrameEntry's defaults), validated."""
    if not isinstance(d, Mapping):
        raise PayloadError(
            "a head colour must be an object with r, g, b, cct, colorBrightness, whiteBrightness"
        )
    try:
        return FrameEntry.from_json(d).validated()
    except (TypeError, ValueError) as e:
        if isinstance(e, PayloadError):
            raise
        raise PayloadError("head colour values must be whole numbers") from None


def fill(base: Sequence[FrameEntry], indices: Sequence[int], entry: FrameEntry) -> list[FrameEntry]:
    out = list(base)
    for i in _checked_indices(indices, len(out)):
        out[i] = entry
    return out


def _lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def mix(a: FrameEntry, b: FrameEntry, t: float) -> FrameEntry:
    """Linear blend of two entries. White: when only one side has a colour temperature, that temperature is
    kept and only its white brightness fades (a 0 K "no white" never gets interpolated into a bogus Kelvin)."""
    if a.cct and b.cct:
        cct = _lerp(a.cct, b.cct, t)
    else:
        cct = a.cct or b.cct
    wa = a.whiteBrightness if a.cct else 0
    wb = b.whiteBrightness if b.cct else 0
    return FrameEntry(
        _lerp(a.r, b.r, t),
        _lerp(a.g, b.g, t),
        _lerp(a.b, b.b, t),
        cct,
        _lerp(a.colorBrightness, b.colorBrightness, t),
        _lerp(wa, wb, t) if cct else 0,
    )


def gradient(
    base: Sequence[FrameEntry],
    positions: Sequence[tuple[float, float]],
    indices: Sequence[int] | None,
    start: FrameEntry,
    end: FrameEntry,
    axis: str,
) -> list[FrameEntry]:
    """A two-stop gradient across the selected heads (all heads when `indices` is empty), along x or y of the
    display frame: the head with the smallest coordinate gets `start`, the largest gets `end`."""
    if axis not in ("x", "y"):
        raise PayloadError("gradient axis must be x or y")
    out = list(base)
    chosen = _checked_indices(indices or range(len(out)), len(out))
    k = 0 if axis == "x" else 1
    coords = [positions[i][k] for i in chosen]
    lo, hi = min(coords), max(coords)
    for i in chosen:
        t = 0.0 if hi == lo else (positions[i][k] - lo) / (hi - lo)
        out[i] = mix(start, end, t)
    return out


def _checked_indices(indices: Any, n: int) -> list[int]:
    out = []
    for i in indices:
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < n:
            raise PayloadError(f"head index {i!r} must be a whole number 0–{n - 1}")
        out.append(i)
    return out


def check_look(look: Look, caps: Capabilities) -> None:
    """Refuse a whole-module look the device doesn't declare (modes, CCT range, presets, programs, speed)."""
    modes = set(caps.color_modes)
    need_mode = {"white": "white", "rgb": "color", "preset": "sequence", "program": "sequence"}
    if look.kind == "off":
        return
    mode = need_mode.get(look.kind)
    if mode and modes and mode not in modes:
        raise PayloadError(f"this device has no {mode} mode")
    if look.kind == "white":
        if look.cct is None:
            raise PayloadError("a white look needs a colour temperature")
        if caps.cct_range is None:
            raise PayloadError("this device doesn't list a colour temperature range, so white can't be set")
        lo, hi = caps.cct_range
        if not lo <= look.cct <= hi:
            raise PayloadError(f"{look.cct} K is outside this device's range {lo}–{hi} K")
    elif look.kind == "rgb":
        if not caps.rgb:
            raise PayloadError("this device doesn't take an RGB colour")
    elif look.kind == "preset":
        if look.preset not in caps.presets:
            raise PayloadError(f"this device has no preset called {look.preset!r}")
    elif look.kind == "program":
        if look.program not in caps.programs:
            raise PayloadError(f"this device has no program called {look.program!r}")
    elif look.kind == "slot":
        if look.slot not in caps.slots:
            raise PayloadError(f"this device has no slot called {look.slot!r}")
    lo, hi = caps.brightness_range or (1, 100)
    if not isinstance(look.brightness, int) or not lo <= look.brightness <= hi:
        raise PayloadError(f"brightness must be a whole number {lo}–{hi}")
    if caps.speed_range:
        lo, hi = caps.speed_range
        if not isinstance(look.speed, int) or not lo <= look.speed <= hi:
            raise PayloadError(f"speed must be a whole number {lo}…{hi}")


def look_from_json(d: Any) -> Look:
    if not isinstance(d, Mapping):
        raise PayloadError("look must be an object like {kind: 'white', cct: 2700, brightness: 60}")
    allowed = {"kind", "brightness", "cct", "rgb", "preset", "program", "slot", "speed"}
    extra = sorted(set(d) - allowed)
    if extra:
        raise PayloadError(f"unknown look field(s): {', '.join(extra)}")
    try:
        look = Look.from_json(d)
    except (TypeError, ValueError):
        raise PayloadError("look fields have the wrong type") from None
    if look.rgb is not None and (
        len(look.rgb) != 3 or not all(isinstance(c, int) and 0 <= c <= 255 for c in look.rgb)
    ):
        raise PayloadError("rgb must be three whole numbers 0–255")
    for name in ("cct", "brightness", "speed"):
        v = getattr(look, name)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
            raise PayloadError(f"{name} must be a whole number")
    return look


# ---- the compiler ----------------------------------------------------------------------------------------------
def compile_card(
    card: str,
    params: Mapping[str, Any] | None,
    *,
    recipes: RecipesFile,
    caps: Capabilities,
    positions: Sequence[tuple[float, float]],
    width_in: float,
) -> Compiled:
    """Turn a card + params into a Look or a frame (one entry per head, in wire order). PayloadError on bad
    params. T1 gating is the caller's job (`Compiled.frame` needs T1)."""
    p = dict(params or {})
    n = len(positions)
    notes: list[str] = []
    if card == "white":
        kelvin = p.get("kelvin", 2700)
        if not isinstance(kelvin, int) or isinstance(kelvin, bool):
            raise PayloadError("kelvin must be a whole number")
        cct, note = clamp_cct(kelvin, caps)
        if note:
            notes.append(note)
        return Compiled(f"full white {kelvin} K", look=Look("white", brightness=100, cct=cct), notes=notes)
    if card == "color":
        name = p.get("color", "hd-orange")
        recipe = _recipe(recipes, name)
        if p.get("painted"):
            kind = p.get("recipe", "blend")
            entry = recipe_entry(recipes, name, kind, caps, notes)
            label = f"{recipe.label} painted ({kind.replace('_', ' ')})"
            return Compiled(label, frame=[entry] * n, notes=notes)
        strand = recipe.strand
        if strand.rgb is not None:
            look = Look("rgb", brightness=100, rgb=tuple(strand.rgb))
        else:
            cct, note = clamp_cct(int(strand.cct or 0), caps)
            if note:
                notes.append(note)
            look = Look("white", brightness=100, cct=cct)
        return Compiled(f"{recipe.label} whole strand", look=look, notes=notes)
    if card == "recipe-compare":
        name = p.get("color", "hd-orange")
        left = recipe_entry(recipes, name, "rgb_only", caps, notes)
        right = recipe_entry(recipes, name, "blend", caps, notes)
        mid = width_in / 2
        frame = [OFF if abs(x - mid) < 1e-6 else (left if x < mid else right) for x, _ in positions]
        label = f"{_recipe(recipes, name).label}: RGB only (left) vs blend (right)"
        return Compiled(label, frame=frame, notes=notes)
    if card == "thirds":
        bands = p.get("bands") or catalog(recipes)[3]["defaults"]["bands"]
        if not isinstance(bands, list) or len(bands) != 3:
            raise PayloadError("thirds needs exactly three bands")
        entries = []
        for band in bands:
            if not isinstance(band, Mapping):
                raise PayloadError("each band needs a colour, a recipe and a brightness")
            e = recipe_entry(recipes, band.get("color"), band.get("recipe", "blend"), caps, notes)
            entries.append(scaled(e, band.get("brightness", 100)))
        frame = [entries[min(2, int(3 * x / width_in))] for x, _ in positions]
        label = "thirds: " + " | ".join(
            f"{_recipe(recipes, b.get('color')).label} {b.get('brightness', 100)}%" for b in bands
        )
        return Compiled(label, frame=frame, notes=notes)
    if card == "hue-ramp":
        return Compiled("hue ramp (12° per head)", frame=[hue_entry(i) for i in range(n)])
    if card == "single-head":
        index = p.get("index", 0)
        return Compiled(f"single head {index}", frame=single_head(n, index))
    if card == "index-sweep":
        return Compiled("index sweep", sweep={"step_s": p.get("step_s")})
    if card == "lowcountry-preview":
        raise PayloadError("The Lowcountry Mode preview runs on an open module page (it needs the modes).")
    raise PayloadError(f"unknown card {card!r}")


def frame_json(frame: Sequence[FrameEntry]) -> list[dict]:
    return [{k: getattr(e, k) for k in FRAME_FIELDS} for e in frame]
