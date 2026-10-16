"""One 2-D design field over the canopy (02, D63): where a canopy point sits along a design, as t in [0, 1].

A field turns a point in canopy inches (reflected-ceiling view, x from the left edge, y from the top edge) into t;
the canvas's colour stops turn t into a colour. The T1 composer evaluates it at every head and module level (T3)
samples it per module, so one design reads the same at both tiers.

Rules implemented:
- **Pure.** No I/O, no clock, no randomness: the same spec, point and phase always give the same t.
- **Kinds** (`kind`):
  - `linear{angle_deg}`: a straight gradient in the canopy's own proportions. 0 runs along x from x = 0 and 90 along
    y from y = 0 (the top edge); 45 runs corner to corner. It is normalised so the canopy's four corners project onto
    [0, 1]: angle 0 is exactly x / width and 90 exactly y / height, the existing `gradient` canvas.
  - `radial{centre_in | centres_in, radius_in, metric}`: distance from a centre (1–4 centres: the nearest wins) over
    `radius_in` (a finite distance > 0). `radius_in: auto` is the largest distance any canopy point has from its
    nearest centre (one centre: the farthest corner; several: often where two centres' regions meet an edge), so
    the rings fill the canopy exactly.
    `metric: inches` measures true inches; `metric: canopy` measures in the canopy's proportions (a vertical inch
    counts as width / height inches, so a ring is an ellipse that fills the canopy and `radius_in` is along the width).
  - `sweep{centre_in, start_deg}`: the angle around a centre, clockwise in the reflected-ceiling view from
    `start_deg` (0 = towards +x). Exactly at the centre it has no direction, so it reads as `start_deg`.
- **Common fields.** `cycles` (1–6) repeats the design (rings for a radial field, a sawtooth for the others);
  `transform: mirror` folds it (t → 1 − |2t − 1|, the scenes' `mirror`); `invert` reverses it. Order: repeat, then
  mirror, then invert.
- **Clamp or wrap.** t wraps (sawtooth) only when `cycles` > 1 or a runtime `phase` ≠ 0; with the defaults it clamps
  to [0, 1] exactly like the `gradient` canvas. `phase` is a runtime argument only: no data sets it.
- `bands` groups modules by the field at their centres (equal-width bins of t, empty bins dropped) and
  `layout_words` is how the copy must describe the layout (the modes schema checks module-level copy against it):
  a radial or sweep design is only "from the centre out" / "around the centre" when its centre is the canopy's.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from itertools import combinations
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Point = tuple[float, float]
BAND_EPSILON = 1e-9  # a centre whose t lands on a bin edge up to float error joins the higher bin
PLACE_EPSILON = 1e-6  # inches: a centre this close to the canopy's centre, an edge or a corner is on it
_POSITION = re.compile(r"^r(\d)c(\d)$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _finite_point(v: Any, name: str) -> Any:
    if isinstance(v, list | tuple) and any(
        isinstance(c, int | float) and not isinstance(c, bool) and not math.isfinite(c) for c in v
    ):
        raise ValueError(f"{name} must be [x, y] in canopy inches (finite numbers)")
    return v


class _FieldBase(_Strict):
    transform: Literal["linear", "mirror"] = "linear"
    invert: bool = False
    cycles: int = 1

    @field_validator("cycles")
    @classmethod
    def _cycles(cls, v: int) -> int:
        if not 1 <= v <= 6:
            raise ValueError("cycles must be a whole number from 1 to 6 (how many times the design repeats)")
        return v


def _angle(v: float, name: str) -> float:
    if not 0 <= v < 360:
        raise ValueError(f"{name} must be at least 0 and less than 360 (degrees)")
    return v


class LinearField(_FieldBase):
    kind: Literal["linear"]
    angle_deg: float = 0.0

    @field_validator("angle_deg")
    @classmethod
    def _angle_range(cls, v: float) -> float:
        return _angle(v, "angle_deg")


class RadialField(_FieldBase):
    kind: Literal["radial"]
    centre_in: Point | None = None
    centres_in: list[Point] | None = Field(None, min_length=1, max_length=4)
    radius_in: float | Literal["auto"] = "auto"
    metric: Literal["inches", "canopy"] = "inches"

    @field_validator("radius_in", mode="before")
    @classmethod
    def _radius(cls, v: Any) -> Any:
        if v == "auto":
            return v
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v) or not v > 0:
            raise ValueError("radius_in must be auto or a distance in inches greater than 0 (and finite)")
        return float(v)

    @field_validator("centre_in", mode="before")
    @classmethod
    def _centre(cls, v: Any) -> Any:
        return _finite_point(v, "centre_in")

    @field_validator("centres_in", mode="before")
    @classmethod
    def _centres(cls, v: Any) -> Any:
        if isinstance(v, list):
            for p in v:
                _finite_point(p, "each of centres_in")
        return v

    @model_validator(mode="after")
    def _one_centre(self) -> RadialField:
        if (self.centre_in is None) == (self.centres_in is None):
            raise ValueError("give exactly one of centre_in: [x, y] or centres_in: a list of 1 to 4 [x, y]")
        return self

    @property
    def centres(self) -> list[Point]:
        return [self.centre_in] if self.centre_in is not None else list(self.centres_in or [])


class SweepField(_FieldBase):
    kind: Literal["sweep"]
    centre_in: Point
    start_deg: float = 0.0

    @field_validator("centre_in", mode="before")
    @classmethod
    def _centre(cls, v: Any) -> Any:
        return _finite_point(v, "centre_in")

    @field_validator("start_deg")
    @classmethod
    def _angle_range(cls, v: float) -> float:
        return _angle(v, "start_deg")


FieldSpec = Annotated[LinearField | RadialField | SweepField, Field(discriminator="kind")]
FIELD_KINDS = ("linear", "radial", "sweep")


# ---- evaluation --------------------------------------------------------------------------------------------------
def _size(canopy: Mapping[str, Any]) -> tuple[float, float]:
    return float(canopy["width_in"]), float(canopy["height_in"])


def _cos_sin(deg: float) -> tuple[float, float]:
    """Exact for multiples of 90° (so angle 0 is x / width and 90 is y / height to the last bit)."""
    exact = {0.0: (1.0, 0.0), 90.0: (0.0, 1.0), 180.0: (-1.0, 0.0), 270.0: (0.0, -1.0)}
    if deg in exact:
        return exact[deg]
    rad = math.radians(deg)
    return math.cos(rad), math.sin(rad)


def _stretch(spec: RadialField, canopy: Mapping[str, Any]) -> float:
    w, h = _size(canopy)
    return w / h if spec.metric == "canopy" else 1.0


def _distance(spec: RadialField, x: float, y: float, canopy: Mapping[str, Any]) -> float:
    stretch = _stretch(spec, canopy)
    return min(math.hypot(x - cx, (y - cy) * stretch) for cx, cy in spec.centres)


def _farthest_from_nearest(centres: Sequence[Point], w: float, h: float) -> float:
    """max over the w × h rectangle of the distance to the nearest centre. The maximum sits on a vertex of the
    centres' nearest-centre regions clipped to the rectangle: a corner, a point where two centres' perpendicular
    bisector meets an edge, or a point equidistant from three centres inside it. Evaluating the nearest-centre
    distance at every such candidate (whether or not that pair or triple is actually nearest there) gives exactly
    the maximum, since every candidate is a canopy point."""
    cs = list(dict.fromkeys(centres))  # duplicate centres add nothing
    candidates: list[Point] = [(0.0, 0.0), (w, 0.0), (0.0, h), (w, h)]

    def inside(px: float, py: float) -> bool:
        return -PLACE_EPSILON <= px <= w + PLACE_EPSILON and -PLACE_EPSILON <= py <= h + PLACE_EPSILON

    for (ax, ay), (bx, by) in combinations(cs, 2):
        # the bisector: nx·x + ny·y = c
        nx, ny, c = bx - ax, by - ay, (bx * bx + by * by - ax * ax - ay * ay) / 2
        if ny:
            candidates += [(x, (c - nx * x) / ny) for x in (0.0, w) if inside(x, (c - nx * x) / ny)]
        if nx:
            candidates += [((c - ny * y) / nx, y) for y in (0.0, h) if inside((c - ny * y) / nx, y)]
    for (ax, ay), (bx, by), (qx, qy) in combinations(cs, 3):
        d = 2 * (ax * (by - qy) + bx * (qy - ay) + qx * (ay - by))
        if not d:
            continue  # collinear: no point is equidistant from all three
        a2, b2, q2 = ax * ax + ay * ay, bx * bx + by * by, qx * qx + qy * qy
        px = (a2 * (by - qy) + b2 * (qy - ay) + q2 * (ay - by)) / d
        py = (a2 * (qx - bx) + b2 * (ax - qx) + q2 * (bx - ax)) / d
        if inside(px, py):
            candidates.append((px, py))
    clamped = [(min(w, max(0.0, px)), min(h, max(0.0, py))) for px, py in candidates]
    return max(min(math.hypot(px - cx, py - cy) for cx, cy in cs) for px, py in clamped)


def radius(spec: RadialField, canopy: Mapping[str, Any]) -> float:
    """The radial field's radius in its metric: `radius_in`, or (auto) the largest distance any canopy point has
    from its nearest centre, so the rings just fill the canopy. ValueError when that is 0 (no canopy to fill)."""
    if spec.radius_in != "auto":
        return float(spec.radius_in)
    w, h = _size(canopy)
    stretch = _stretch(spec, canopy)
    r = _farthest_from_nearest([(cx, cy * stretch) for cx, cy in spec.centres], w, h * stretch)
    if not r > 0:
        raise ValueError(
            "radius_in: auto has no distance to fill (every canopy point is a centre); give radius_in in inches"
        )
    return r


def _raw(spec: Any, x: float, y: float, canopy: Mapping[str, Any]) -> float:
    if isinstance(spec, LinearField):
        w, h = _size(canopy)
        c, s = _cos_sin(spec.angle_deg)
        projections = [0.0, c, s, c + s]  # the unit canopy's corners
        lo, hi = min(projections), max(projections)
        return ((x / w) * c + (y / h) * s - lo) / (hi - lo)
    if isinstance(spec, RadialField):
        return _distance(spec, x, y, canopy) / radius(spec, canopy)
    if isinstance(spec, SweepField):
        cx, cy = spec.centre_in
        deg = math.degrees(
            math.atan2(y - cy, x - cx)
        )  # y runs down the canopy, so this is clockwise seen from below
        return ((deg - spec.start_deg) % 360.0) / 360.0
    raise ValueError(
        f"unknown field kind {getattr(spec, 'kind', spec)!r}; use one of {', '.join(FIELD_KINDS)}"
    )


def value(spec: Any, x: float, y: float, canopy: Mapping[str, Any], phase: float = 0.0) -> float:
    """t in [0, 1] of the field at canopy point (x, y) inches. `phase` (runtime only) shifts a repeating design."""
    t = _raw(spec, x, y, canopy)
    if spec.cycles > 1 or phase != 0:
        t = (t * spec.cycles + phase) % 1.0
    else:
        t = min(1.0, max(0.0, t))
    if spec.transform == "mirror":
        t = 1.0 - abs(2.0 * t - 1.0)
    if spec.invert:
        t = 1.0 - t
    return t


# ---- modules -----------------------------------------------------------------------------------------------------
def _row_col(position: str) -> tuple[int, int]:
    m = _POSITION.match(position)
    if m is None:
        raise ValueError(f"{position!r} isn't a canopy position; use r0c0 … r2c2")
    return int(m.group(1)), int(m.group(2))


def module_centres(canopy: Mapping[str, Any], positions: Iterable[str]) -> dict[str, Point]:
    """Canopy-inch centre of each module position ("r{row}c{col}"): ((col + ½)·module width, (row + ½)·height)."""
    mw, mh = float(canopy["module"]["width_in"]), float(canopy["module"]["height_in"])
    out = {}
    for pos in positions:
        row, col = _row_col(pos)
        out[pos] = ((col + 0.5) * mw, (row + 0.5) * mh)
    return out


def bands(
    spec: Any, canopy: Mapping[str, Any], positions: Sequence[str], max_bands: int = 3
) -> list[list[str]]:
    """The modules grouped by the field at their centres: `max_bands` equal-width bins of t over [0, 1], lowest t
    first, empty bins dropped, positions in the order given within a bin."""
    if max_bands < 1:
        raise ValueError("max_bands must be at least 1")
    bins: list[list[str]] = [[] for _ in range(max_bands)]
    for pos, (x, y) in module_centres(canopy, positions).items():
        t = value(spec, x, y, canopy)
        bins[min(max_bands - 1, int(t * max_bands + BAND_EPSILON))].append(pos)
    return [b for b in bins if b]


_COUNTS = {2: "two", 3: "three", 4: "four"}
# Every phrase `layout_words` can return: copy for one layout may not use another layout's words.
LAYOUT_PHRASES = (
    "column by column",
    "row by row",
    "diagonally",
    "from the centre out",
    "from one corner",
    "from one edge",
    "from one spot",
    *(f"from {n} points" for n in _COUNTS.values()),
    "around the centre",
    "around one corner",
    "around one edge",
    "around one spot",
)


def _place(point: Point, canopy: Mapping[str, Any]) -> str:
    """Where a centre sits, as copy can say it: "the centre", or "one corner" / "one edge" (a point beyond the canopy
    counts as the corner or edge it lies beyond) / "one spot" (anywhere else inside)."""
    w, h = _size(canopy)
    x, y = point
    if abs(x - w / 2) <= PLACE_EPSILON and abs(y - h / 2) <= PLACE_EPSILON:
        return "the centre"
    on_x = x <= PLACE_EPSILON or x >= w - PLACE_EPSILON  # on or beyond the left or right edge
    on_y = y <= PLACE_EPSILON or y >= h - PLACE_EPSILON
    if on_x and on_y:
        return "one corner"
    if on_x or on_y:
        return "one edge"
    return "one spot"


def layout_words(spec: Any, canopy: Mapping[str, Any]) -> str:
    """How copy must describe this field's layout over this canopy: the words module-level copy has to contain (one
    of `LAYOUT_PHRASES`). A radial or sweep design names where its centre is: only one centred on the canopy is
    "from the centre out" / "around the centre"; several radial centres are "from {n} points"."""
    if isinstance(spec, LinearField):
        if spec.angle_deg in (0.0, 180.0):
            return "column by column"
        if spec.angle_deg in (90.0, 270.0):
            return "row by row"
        return "diagonally"
    if isinstance(spec, RadialField):
        centres = list(dict.fromkeys(spec.centres))
        if len(centres) > 1:
            return f"from {_COUNTS[len(centres)]} points"
        place = _place(centres[0], canopy)
        return "from the centre out" if place == "the centre" else f"from {place}"
    if isinstance(spec, SweepField):
        return f"around {_place(spec.centre_in, canopy)}"
    raise ValueError(
        f"unknown field kind {getattr(spec, 'kind', spec)!r}; use one of {', '.join(FIELD_KINDS)}"
    )
