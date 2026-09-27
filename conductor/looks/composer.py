"""The canvas composer: a look designed in canopy inches over all 270 heads, split into nine frames (04, 02).

Rules implemented:
- **Where each head is.** Every module's heads come from `conductor/geometry.py`: the 8/7/8/7 panel lattice in
  wire order (`geometry.panel_order` — the registry's calibrated `wire_order`, or its `cable_path` +
  `start_corner`, else the canopy default, exactly what `/ops/module` draws), rotated 180° for a B panel and offset
  to the module's canopy position. Uncalibrated, this is `geometry.build_layout` head for head.
- **Frame index = frame_offset + wire index.** A module's puck i is the canvas colour at the canopy position of the
  head that is i-th along that module's cable, written into frame entry `frame_offset + i` (registry, default 0), so
  a recalibrated wire order moves colours between frame indices, never on the canopy.
- **Frame length ≠ head count.** A frame is exactly the device's framebuffer length, which can be longer than the
  module (the real SQHDK03 reports 60 entries with 30 pucks). The pucks' entries sit at
  [frame_offset, frame_offset + heads); every other entry is OFF (all fields 0), so frames stay deterministic.
- **Head-count guard (07 §6).** The module's physical heads (`expected_heads`, else the lattice) must equal the
  panel lattice (30), and the frame must be long enough to hold them from `frame_offset`. Anything else is refused
  with `PlanError`. A shorter frame than expected means a strand that lost pucks; a longer one is fine (and then a
  lost-puck fault can't be seen from the length — 07 §6: the daily check is visual).
- **Colours are recipes** (`config/recipes.yaml`, `rgb_only` or `blend`); between recipe colours every frame field
  is interpolated (a side with no white keeps the other side's Kelvin and only fades white brightness). A colour
  level (`colorBrightness` in modes.yaml) scales the recipe entry's colour *and* white brightness.
- **Deterministic.** Same config in, same frames out: no clock, no randomness.

Canvas kinds (config/modes.yaml `techniques.canvas.canvas`):
- `gradient`        colour stops at 0..1 along the canopy's x (0 = left edge) or y (0 = top edge).
- `pathway`         a meandering band `width_in` wide around y = H/2 + amplitude·sin(2π·periods·x/W), over a base.
- `band_per_module` one soft band per module width, centred in each module column, over a base: every module shows
                    the same band at the same canopy-local x, so in-phase on-device motion reads as waves. With
                    motion off it is a static glow.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from conductor.config import RecipesFile
from conductor.engine_api import PlanError
from conductor.geometry import panel_lattice, panel_order
from conductor.looks.schema import BandCanvas, ColorLevel, GradientCanvas, PathwayCanvas
from conductor.rows import FrameEntry, FrameLayout, PayloadError

Point = tuple[float, float]
PATH_SAMPLE_IN = 0.5  # the pathway's centre line is sampled every half inch for the distance to each head


@dataclass(frozen=True)
class Placement:
    """One module on the canvas: which device, where it hangs, how it's wired and how long its frame is."""

    device_id: str
    module: str  # canopy position "r{row}c{col}"
    row: int
    col: int
    orientation: str  # "A" or "B" (A rotated 180°) as the panel hangs
    order: tuple[Point, ...]  # A-frame panel position of wire index i
    frame_length: int | None  # the device's framebuffer length (None = the device reports none)
    expected_heads: int | None  # the registry's expected head count
    frame_offset: int = 0  # the framebuffer entry that drives puck 0 (registry `frame_offset`)


@dataclass(frozen=True)
class CanvasHead:
    device_id: str
    module: str
    index: int  # wire index (frame entry = the placement's frame_offset + index)
    x: float  # canopy inches from the left edge
    y: float  # canopy inches from the top edge


# ---- placement ---------------------------------------------------------------------------------------------------
def parse_position(module: str | None, canopy: Mapping[str, Any] | None = None) -> tuple[int, int]:
    """(row, col) of "r{row}c{col}", checked against the canopy grid when given. PlanError otherwise."""
    if not isinstance(module, str) or len(module) != 4 or module[0] != "r" or module[2] != "c":
        raise PlanError(f"{module!r} isn't a canopy position; use r0c0 … r2c2")
    if not (module[1].isdigit() and module[3].isdigit()):
        raise PlanError(f"{module!r} isn't a canopy position; use r0c0 … r2c2")
    row, col = int(module[1]), int(module[3])
    if canopy is not None:
        grid = canopy["orientations"][canopy["orientation_scheme"]]
        if row >= len(grid) or col >= len(grid[row]):
            raise PlanError(f"{module} is outside the canopy ({len(grid)} rows × {len(grid[0])} columns)")
    return row, col


def positions(canopy: Mapping[str, Any]) -> list[str]:
    """Every canopy position, row-major: r0c0, r0c1, …"""
    grid = canopy["orientations"][canopy["orientation_scheme"]]
    return [f"r{r}c{c}" for r, row in enumerate(grid) for c in range(len(row))]


def scheme_orientation(canopy: Mapping[str, Any], row: int, col: int) -> str:
    return str(canopy["orientations"][canopy["orientation_scheme"]][row][col])


def placement_for(
    canopy: Mapping[str, Any],
    entry: Any,
    *,
    frame_length: int | None,
    module: str | None = None,
) -> Placement:
    """The placement of a registry entry (a `DeviceEntry`) at its canopy position — or at `module`, e.g. when the
    module page previews a position on the bench kit. Orientation: the entry's own (how that panel hangs), else the
    canopy scheme's for the position. Wire order: the entry's calibration, else the canopy default."""
    pos = module or entry.module
    row, col = parse_position(pos, canopy)
    orientation = entry.orientation or scheme_orientation(canopy, row, col)
    wire = [tuple(p) for p in entry.wire_order] if entry.wire_order else None
    try:
        order = panel_order(dict(canopy), entry.cable_path, entry.start_corner, wire)
    except ValueError as e:
        raise PlanError(f"{entry.id}: its calibration can't be used ({e})") from None
    return Placement(
        device_id=entry.id,
        module=str(pos),
        row=row,
        col=col,
        orientation=orientation,
        order=tuple((float(x), float(y)) for x, y in order),
        frame_length=frame_length,
        expected_heads=entry.expected_heads,
        frame_offset=int(getattr(entry, "frame_offset", 0) or 0),
    )


def head_positions(canopy: Mapping[str, Any], placement: Placement) -> list[Point]:
    """Canopy-inch (x, y) of each wire index of a placed module."""
    mw, mh = float(canopy["module"]["width_in"]), float(canopy["module"]["height_in"])
    out = []
    for px, py in placement.order:
        if placement.orientation == "B":
            px, py = mw - px, mh - py
        out.append((placement.col * mw + px, placement.row * mh + py))
    return out


def canvas_heads(canopy: Mapping[str, Any], placements: Iterable[Placement]) -> list[CanvasHead]:
    """Every placed head on the canvas (the visualizer's and the tests' view of the 270)."""
    return [
        CanvasHead(p.device_id, p.module, i, x, y)
        for p in placements
        for i, (x, y) in enumerate(head_positions(canopy, p))
    ]


def head_count_problem(placement: Placement, lattice_size: int) -> str | None:
    """Why this module can't take a per-head frame, or None (the 07 §6 head-count guard).

    The frame may be longer than the module's heads (the controller reports its capacity); it may not be shorter
    than the entries the pucks need from `frame_offset`."""
    n, expected, offset = placement.frame_length, placement.expected_heads, placement.frame_offset
    if n is None:
        return f"{placement.device_id} reports no frame length, so per-head frames are refused"
    heads = expected if expected is not None else lattice_size
    if heads != lattice_size:
        return (
            f"{placement.device_id} should have {heads} heads but a panel holds {lattice_size}; per-head frames "
            "refused"
        )
    if n < heads + offset:
        where = f" from entry {offset}" if offset else ""
        return (
            f"{placement.device_id} reports {n} heads but this module should have {heads}{where}; per-head "
            "frames are refused until the strand is checked (all pucks lit? cable cut?)"
        )
    return None


def frame_layout(placement: Placement, lattice_size: int) -> FrameLayout:
    """Where this placement's heads sit in its device's frame. PlanError when the head-count guard refuses it."""
    if problem := head_count_problem(placement, lattice_size):
        raise PlanError(problem)
    assert placement.frame_length is not None
    heads = placement.expected_heads if placement.expected_heads is not None else lattice_size
    return FrameLayout(placement.frame_length, heads, placement.frame_offset)


# ---- colour maths ------------------------------------------------------------------------------------------------
def _lerp(a: int, b: int, t: float) -> int:
    return round(a + (b - a) * t)


def mix(a: FrameEntry, b: FrameEntry, t: float) -> FrameEntry:
    """Every field interpolated from `a` (t=0) to `b` (t=1). White: when only one side has a colour temperature,
    that temperature is kept and only its white brightness fades (never a bogus Kelvin between 0 and 2700)."""
    t = min(1.0, max(0.0, t))
    cct = _lerp(a.cct, b.cct, t) if a.cct and b.cct else a.cct or b.cct
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


def at_level(entry: FrameEntry, pct: int) -> FrameEntry:
    """The entry at `pct`% of its own colour and white brightness."""
    return FrameEntry(
        entry.r,
        entry.g,
        entry.b,
        entry.cct,
        round(entry.colorBrightness * pct / 100),
        round(entry.whiteBrightness * pct / 100),
    )


def recipe_entry(recipes: RecipesFile, name: str, kind: str) -> FrameEntry:
    try:
        recipe = recipes.colors[name]
    except KeyError:
        raise PlanError(f"unknown colour {name!r}; use one of {', '.join(recipes.colors)}") from None
    return FrameEntry(**getattr(recipe, kind).model_dump())


def _level(recipes: RecipesFile, spec: ColorLevel, kind: str) -> FrameEntry:
    return at_level(recipe_entry(recipes, spec.color, kind), spec.colorBrightness)


def _soft(d: float, half_width: float) -> float:
    """1 at the centre line, falling smoothly (cosine) to 0 at `half_width`."""
    if half_width <= 0 or d >= half_width:
        return 0.0
    return 0.5 * (1.0 + math.cos(math.pi * d / half_width))


class _Evaluator:
    """Canvas colour at any canopy point, with per-canvas precomputation (the pathway's sampled centre line)."""

    def __init__(self, canvas: Any, canopy: Mapping[str, Any], recipes: RecipesFile):
        self.canvas = canvas
        self.w = float(canopy["width_in"])
        self.h = float(canopy["height_in"])
        self.mw = float(canopy["module"]["width_in"])
        self.recipes = recipes
        kind = canvas.recipe
        if isinstance(canvas, GradientCanvas):
            self.stops = [(s.at, recipe_entry(recipes, s.color, kind)) for s in canvas.stops]
        elif isinstance(canvas, PathwayCanvas):
            self.base = _level(recipes, canvas.base, kind)
            self.top = _level(recipes, canvas.path, kind)
            p = canvas.path
            n = max(2, int(self.w / PATH_SAMPLE_IN) + 1)
            self.curve = [
                (
                    x,
                    self.h / 2 + p.amplitude_in * math.sin(2 * math.pi * p.periods * x / self.w),
                )
                for x in (self.w * i / (n - 1) for i in range(n))
            ]
        elif isinstance(canvas, BandCanvas):
            self.base = _level(recipes, canvas.base, kind)
            self.top = _level(recipes, canvas.band, kind)
        else:  # pragma: no cover - the schema's discriminator admits nothing else
            raise PlanError(f"unknown canvas kind {getattr(canvas, 'kind', canvas)!r}")

    def __call__(self, x: float, y: float) -> FrameEntry:
        c = self.canvas
        if isinstance(c, GradientCanvas):
            t = (x / self.w) if c.axis == "x" else (y / self.h)
            return self._gradient(min(1.0, max(0.0, t)))
        if isinstance(c, PathwayCanvas):
            d = min(math.hypot(x - cx, y - cy) for cx, cy in self.curve)
            half = c.path.width_in / 2
            # Full path colour across the band's width, then a soft edge as wide again.
            weight = 1.0 if d <= half else _soft(d - half, half)
            return mix(self.base, self.top, weight)
        assert isinstance(c, BandCanvas)
        local = x - math.floor(x / self.mw) * self.mw
        weight = _soft(abs(local - self.mw / 2), c.band.width_in / 2)
        return mix(self.base, self.top, weight)

    def _gradient(self, t: float) -> FrameEntry:
        stops = self.stops
        if t <= stops[0][0]:
            return stops[0][1]
        for (a_at, a), (b_at, b) in itertools.pairwise(stops):
            if t <= b_at:
                return mix(a, b, (t - a_at) / (b_at - a_at))
        return stops[-1][1]


def evaluate(
    canvas: Any, x: float, y: float, *, canopy: Mapping[str, Any], recipes: RecipesFile
) -> FrameEntry:
    """The canvas colour at one canopy point (inches from the top-left, reflected-ceiling view)."""
    return _Evaluator(canvas, canopy, recipes)(x, y)


def compose_canvas(
    canvas: Any,
    *,
    canopy: Mapping[str, Any],
    recipes: RecipesFile,
    placements: Sequence[Placement],
) -> dict[str, list[FrameEntry]]:
    """One frame per placed module: device id → the device's full-length frame, the pucks' colours in wire order at
    [frame_offset, frame_offset + heads) and OFF in every other entry.

    PlanError when a module fails the head-count guard or two placements share a canopy position."""
    lattice = len(panel_lattice(dict(canopy)))
    seen: dict[str, str] = {}
    layouts: dict[str, FrameLayout] = {}
    for p in placements:
        if p.module in seen:
            raise PlanError(f"{p.device_id} and {seen[p.module]} both claim canopy position {p.module}")
        seen[p.module] = p.device_id
        layouts[p.device_id] = frame_layout(p, lattice)
    colour = _Evaluator(canvas, canopy, recipes)
    out: dict[str, list[FrameEntry]] = {}
    for p in placements:
        try:
            out[p.device_id] = layouts[p.device_id].place(
                [colour(x, y) for x, y in head_positions(canopy, p)]
            )
        except PayloadError as e:
            raise PlanError(f"{p.device_id}: {e}") from None
    return out
