"""The canvas composer: a look designed in canopy inches over all 270 heads, split into nine frames (04, 02).

Rules implemented:
- **Where each head is.** Every module's heads come from `conductor/geometry.py`: the 8/7/8/7 panel lattice in
  wire order (`geometry.panel_order` — the registry's calibrated `wire_order`, or its `cable_path` +
  `start_corner`, else the canopy default, exactly what `/ops/module` draws), rotated 180° for a B panel and offset
  to the module's canopy position. Uncalibrated, this is `geometry.build_layout` head for head.
- **Frame index = wire index.** A module's frame entry i is the canvas colour at the canopy position of the head
  that is i-th along that module's cable, so a recalibrated wire order moves colours between frame indices, never
  on the canopy.
- **Head-count guard (07 §6).** A frame is exactly the device's framebuffer length, which must equal the registry's
  `expected_heads` and the panel lattice (30). Anything else is refused with `PlanError` — per-head writes to a
  strand that lost pucks would paint the wrong heads.
- **A kit's controller map (D54).** The real SQHDK03 reports 60 entries with 30 pucks attached. A placement with
  `indices` (the kit's proven `framebuffer_mapping`, `conductor.framemap.frame_indices`) gets a full-length frame:
  wire index i in entry `indices[i]`, every other entry OFF (all fields 0), so frames and slot hashes stay
  deterministic. A longer frame without one is refused as controller capacity with no proven map, not lost pucks.
- **Colours are recipes** (`config/recipes.yaml`, `rgb_only` or `blend`); between recipe colours every frame field
  is interpolated (a side with no white keeps the other side's Kelvin and only fades white brightness) and rounded
  half up with the scene catalog's formula (`conductor/scenes.py`, JavaScript's Math.round), so a design lands on
  the same colour as the scene it renders even on an exact half. A colour level (`colorBrightness` in modes.yaml)
  scales the recipe entry's colour *and* white brightness.
- **Deterministic.** Same config in, same frames out: no clock, no randomness.

Canvas kinds (config/modes.yaml `techniques.canvas.canvas`):
- `gradient`        colour stops at 0..1 along the canopy's x (0 = left edge) or y (0 = top edge).
- `field`           colour stops at 0..1 over a 2-D design field (`conductor/looks/field.py`): linear at any angle,
                    radial (rings) or a sweep, with mirror / invert / repeat. Linear at 0° is the x `gradient`.
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
from conductor.framemap import capacity_problem, unmapped_capacity
from conductor.geometry import panel_lattice, panel_order
from conductor.looks import field as F
from conductor.looks.schema import BandCanvas, ColorLevel, FieldCanvas, GradientCanvas, PathwayCanvas
from conductor.rows import FrameEntry

Point = tuple[float, float]
PATH_SAMPLE_IN = 0.5  # the pathway's centre line is sampled every half inch for the distance to each head
OFF = FrameEntry(0, 0, 0, 0, 0, 0)  # a controller entry no head is mapped to


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
    indices: tuple[int, ...] | None = None  # the kit's proven controller map: wire index i → entry indices[i]


@dataclass(frozen=True)
class CanvasHead:
    device_id: str
    module: str
    index: int  # wire index = frame index
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
    indices: Sequence[int] | None = None,
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
        indices=tuple(indices) if indices is not None else None,
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
    """Why this module can't take a per-head frame, or None (the 07 §6 head-count guard)."""
    n, expected = placement.frame_length, placement.expected_heads
    if n is None:
        return f"{placement.device_id} reports no frame length, so per-head frames are refused"
    if placement.indices is not None:
        return None  # a proven map (framemap.frame_indices checked its frame length and head count)
    if unmapped_capacity(n, expected, lattice_size):
        return f"{placement.device_id}: {capacity_problem(n, expected)}"
    if expected is not None and n != expected:
        return (
            f"{placement.device_id} reports {n} heads but this module should have {expected}; per-head frames "
            "are refused until the strand is checked (all pucks lit? cable cut?)"
        )
    if n != lattice_size:
        return (
            f"{placement.device_id} has {n} heads but a panel holds {lattice_size}; per-head frames refused"
        )
    return None


# ---- colour maths ------------------------------------------------------------------------------------------------
def _lerp(a: int, b: int, t: float) -> int:
    # The scene catalog's rounding (conductor/scenes.py `color_at`): the same formula, rounded half up.
    return math.floor(a * (1 - t) + b * t + 0.5)


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
        self.canopy = canopy
        self.recipes = recipes
        kind = canvas.recipe
        if isinstance(canvas, GradientCanvas | FieldCanvas):
            self.stops = [(s.at, recipe_entry(recipes, s.color, kind)) for s in canvas.stops]
            if isinstance(canvas, FieldCanvas):
                # the design's own t, before mirror and invert (where a breath's colour offset applies, D75)
                self.plain = canvas.field.model_copy(update={"transform": "linear", "invert": False})
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

    def __call__(self, x: float, y: float, phase: float = 0.0, offset: float = 0.0) -> FrameEntry:
        assert not (phase and offset), "a step shifts the design (travel) or offsets its colour (breathe)"
        c = self.canvas
        if isinstance(c, GradientCanvas):
            t = (x / self.w) if c.axis == "x" else (y / self.h)
            return self._gradient(min(1.0, max(0.0, t)))
        if isinstance(c, FieldCanvas):
            if not offset:
                return self._gradient(F.value(c.field, x, y, self.canopy, phase))
            return self._gradient(self._offset_t(x, y, offset))
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

    def _offset_t(self, x: float, y: float, offset: float) -> float:
        """A breath's t (D75): the design's own t before mirror and invert (`field.value` of a linear, uninverted
        copy, so its repeat still applies), moved by `offset` and clamped, never wrapped (a rim module never jumps
        to the centre's colour); then mirror, then invert, as `field.value` does. The offset is added before the fold,
        so its sign means what travel's phase means (`forward` sends a negative one). field.py is not involved."""
        spec = self.canvas.field
        u = min(1.0, max(0.0, F.value(self.plain, x, y, self.canopy) + offset))
        if spec.transform == "mirror":
            u = 1.0 - abs(2.0 * u - 1.0)
        if spec.invert:
            u = 1.0 - u
        return u

    def _gradient(self, t: float) -> FrameEntry:
        stops = self.stops
        if t <= stops[0][0]:
            return stops[0][1]
        for (a_at, a), (b_at, b) in itertools.pairwise(stops):
            if t <= b_at:
                return mix(a, b, (t - a_at) / (b_at - a_at))
        return stops[-1][1]


def evaluate(
    canvas: Any,
    x: float,
    y: float,
    *,
    canopy: Mapping[str, Any],
    recipes: RecipesFile,
    phase: float = 0.0,
    offset: float = 0.0,
) -> FrameEntry:
    """The canvas colour at one canopy point (inches from the top-left, reflected-ceiling view). `phase` shifts a
    `field` canvas's design (`field.value`, which wraps; a travel look's steps). `offset` moves a `field` canvas's
    colour along its t instead (a breathe/shimmer look's steps, D75): clamped to [0, 1], never wrapped, so a rim
    module never jumps to the centre's colour, and applied before mirror and invert, so `forward` means what
    travel's phase means. Never both at once. The other canvas kinds have neither."""
    return _Evaluator(canvas, canopy, recipes)(x, y, phase, offset)


def compose_canvas(
    canvas: Any,
    *,
    canopy: Mapping[str, Any],
    recipes: RecipesFile,
    placements: Sequence[Placement],
) -> dict[str, list[FrameEntry]]:
    """One frame per placed module (device id → entries in wire order, the device's frame length; through a kit's
    controller map, the heads at their mapped entries and OFF everywhere else).

    PlanError when a module fails the head-count guard or two placements share a canopy position."""
    lattice = len(panel_lattice(dict(canopy)))
    seen: dict[str, str] = {}
    for p in placements:
        if p.module in seen:
            raise PlanError(f"{p.device_id} and {seen[p.module]} both claim canopy position {p.module}")
        seen[p.module] = p.device_id
        if problem := head_count_problem(p, lattice):
            raise PlanError(problem)
    colour = _Evaluator(canvas, canopy, recipes)
    frames = {}
    for p in placements:
        heads = [colour(x, y) for x, y in head_positions(canopy, p)]
        if p.indices is not None:
            assert p.frame_length is not None
            frame = [OFF] * p.frame_length
            for i, entry in zip(p.indices, heads, strict=True):
                frame[i] = entry
            heads = frame
        frames[p.device_id] = heads
    return frames
