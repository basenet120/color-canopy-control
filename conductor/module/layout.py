"""The module map: where each of a module's heads sits, in wire order, as the page draws it (02, 04).

Positions come from `conductor/geometry.py` (the 8/7/8/7 staggered lattice, 10" pitch) and the device's own
calibration in the registry (`DeviceEntry.cable_path / start_corner / orientation / wire_order`):

- Calibration is stored in the panel's own **orientation A frame** (the frame `wire_order` pairs are written in).
- The map is drawn in the **display frame**: the panel as it hangs. Orientation B is A rotated 180°, so a B panel's
  heads (and its wiring) are rotated before drawing — exactly as `build_layout` does for the canopy.
- Positional tools (fill by area, gradients, halves, thirds) use display-frame x/y, i.e. what the person at the
  bench sees on the map.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from conductor.geometry import CABLE_PATHS, START_CORNERS, longest_hop, panel_lattice, panel_order

ORIENTATIONS = ("A", "B")
# A corner as seen on a B panel is the opposite corner of its A frame.
_OPPOSITE = {
    "top-left": "bottom-right",
    "top-right": "bottom-left",
    "bottom-left": "top-right",
    "bottom-right": "top-left",
}


@dataclass(frozen=True)
class Calibration:
    orientation: str = "A"
    cable_path: str = "serpentine_rows"
    start_corner: str = "top-left"  # in the A frame
    wire_order: tuple[tuple[float, float], ...] | None = None  # A-frame [x, y] per index, when calibrated
    calibrated: bool = False  # True once anything was saved for this device

    def to_json(self) -> dict:
        return {
            "orientation": self.orientation,
            "cable_path": self.cable_path,
            "start_corner": self.start_corner,
            "start_corner_as_shown": shown_corner(self.start_corner, self.orientation),
            "wire_order": [list(p) for p in self.wire_order] if self.wire_order else None,
            "calibrated": self.calibrated,
        }


def shown_corner(corner: str, orientation: str) -> str:
    """An A-frame corner as it appears on the map for this orientation (and back: the mapping is its own
    inverse)."""
    return _OPPOSITE[corner] if orientation == "B" else corner


def default_orientation(cfg: dict, module: str | None) -> str:
    """A canopy module's orientation from the canopy scheme; anything else hangs as A."""
    if module and len(module) == 4 and module[0] == "r" and module[2] == "c":
        grid = cfg["orientations"][cfg["orientation_scheme"]]
        r, c = int(module[1]), int(module[3])
        if r < len(grid) and c < len(grid[r]):
            return str(grid[r][c])
    return "A"


def calibration_for(cfg: dict, entry: Any) -> Calibration:
    """The calibration a registry entry says (with defaults for anything not set)."""
    wire = tuple((float(x), float(y)) for x, y in entry.wire_order) if entry.wire_order else None
    return Calibration(
        orientation=entry.orientation or default_orientation(cfg, entry.module),
        cable_path=entry.cable_path or cfg.get("cable_path") or "serpentine_rows",
        start_corner=entry.start_corner or "top-left",
        wire_order=wire,
        calibrated=bool(entry.wire_order or entry.cable_path or entry.start_corner),
    )


def validate_calibration(
    cfg: dict,
    *,
    orientation: str,
    cable_path: str,
    start_corner: str,
    wire_order: list | None = None,
) -> Calibration:
    """A checked Calibration, or ValueError with a plain message."""
    if orientation not in ORIENTATIONS:
        raise ValueError("orientation must be A or B")
    if cable_path not in CABLE_PATHS:
        raise ValueError(f"cable path must be one of {', '.join(CABLE_PATHS)}")
    if start_corner not in START_CORNERS:
        raise ValueError(f"start corner must be one of {', '.join(START_CORNERS)}")
    wire = None
    if wire_order:
        try:
            wire = tuple((float(x), float(y)) for x, y in wire_order)
        except (TypeError, ValueError):
            raise ValueError("wire_order must be a list of [x, y] pairs") from None
        panel_order(cfg, wire_order=list(wire))  # raises ValueError unless it covers every head once
    return Calibration(orientation, cable_path, start_corner, wire, True)


def a_frame_order(cfg: dict, cal: Calibration) -> list[tuple[float, float]]:
    return panel_order(
        cfg, cal.cable_path, cal.start_corner, list(cal.wire_order) if cal.wire_order else None
    )


def to_display(cfg: dict, p: tuple[float, float], orientation: str) -> tuple[float, float]:
    if orientation != "B":
        return p
    module = cfg["module"]
    return (module["width_in"] - p[0], module["height_in"] - p[1])


def module_map(cfg: dict, cal: Calibration) -> dict:
    """Map JSON for the page: per index {index, x, y, row, col, ax, ay}, the wire polyline and calibration.

    x/y are display-frame panel inches (origin top-left as the panel hangs); ax/ay the A-frame position the
    calibration is stored in; `row` counts head rows from the top (0–3) and `col` half-pitch columns from the
    left (0–14).
    """
    module = cfg["module"]
    order = a_frame_order(cfg, cal)
    shown = [to_display(cfg, p, cal.orientation) for p in order]
    ys = sorted({y for _, y in shown})
    half = module["pitch_in"] / 2
    x0 = min(x for x, _ in shown)
    heads = [
        {
            "index": i,
            "x": x,
            "y": y,
            "ax": order[i][0],
            "ay": order[i][1],
            "row": ys.index(y),
            "col": round((x - x0) / half),
        }
        for i, (x, y) in enumerate(shown)
    ]
    hop = longest_hop(order) if len(order) > 1 else 0.0
    spacing = float(cfg.get("puck_spacing_in") or 0)
    return {
        "width_in": module["width_in"],
        "height_in": module["height_in"],
        "pitch_in": module["pitch_in"],
        "puck_spacing_in": spacing,
        "heads": heads,
        "polyline": [[x, y] for x, y in shown],
        "longest_hop_in": round(hop, 2),
        "hops_fit": not spacing or hop <= spacing + 1e-9,
        "calibration": cal.to_json(),
        "cable_paths": list(CABLE_PATHS),
        "start_corners": list(START_CORNERS),
        "lattice_size": len(panel_lattice(cfg)),
    }


def display_positions(map_json: dict) -> list[tuple[float, float]]:
    """(x, y) per index, display frame."""
    return [(h["x"], h["y"]) for h in map_json["heads"]]
