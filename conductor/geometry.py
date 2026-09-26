"""Canopy geometry: where every head sits, in canopy inches, and its index along its module's strand."""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass
from math import dist
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "canopy.yaml"


@dataclass(frozen=True)
class Head:
    module: str  # "r{row}c{col}", reflected-ceiling view
    row: int
    col: int
    orientation: str  # "A" or "B" (A rotated 180°)
    index: int  # position along the module's strand; 0 = first head on the wire
    x: float  # inches from the canopy's left edge
    y: float  # inches from the canopy's top edge


def load_canopy_config(path: Path = CONFIG_PATH) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)["canopy"]


def panel_positions(cfg: dict) -> list[tuple[float, float]]:
    """Head positions in the panel's own frame (orientation A), listed in wire order."""
    module = cfg["module"]
    pitch = module["pitch_in"]
    lattice = [(row["x0"] + pitch * i, row["y"]) for row in module["rows"] for i in range(row["count"])]
    if cfg.get("wire_order"):
        order = [(float(x), float(y)) for x, y in cfg["wire_order"]]
        if sorted(order) != sorted(lattice):
            raise ValueError("wire_order must list every head position of the panel exactly once")
        return order
    if cfg["cable_path"] != "serpentine_rows":
        raise ValueError(f"unknown cable_path {cfg['cable_path']!r}; use serpentine_rows or a wire_order")
    ys = sorted({y for _, y in lattice})
    return [
        p for n, y in enumerate(ys) for p in sorted((p for p in lattice if p[1] == y), reverse=n % 2 == 1)
    ]


def longest_hop(order: list[tuple[float, float]]) -> float:
    """Longest straight-line distance between consecutive heads on the wire."""
    return max(dist(a, b) for a, b in itertools.pairwise(order))


def build_layout(cfg: dict, scheme: str | None = None) -> list[Head]:
    """Every head on the canopy. B panels are A panels rotated 180°, so their wiring rotates too."""
    scheme = scheme or cfg["orientation_scheme"]
    if scheme not in cfg["orientations"]:
        raise ValueError(f"unknown orientation scheme {scheme!r}; known: {sorted(cfg['orientations'])}")
    mw, mh = cfg["module"]["width_in"], cfg["module"]["height_in"]
    base = panel_positions(cfg)
    heads = []
    for r, grid_row in enumerate(cfg["orientations"][scheme]):
        for c, orientation in enumerate(grid_row):
            for i, (px, py) in enumerate(base):
                if orientation == "B":
                    px, py = mw - px, mh - py
                heads.append(Head(f"r{r}c{c}", r, c, orientation, i, c * mw + px, r * mh + py))
    return heads


def pitch_violations(heads: list[Head], pitch: float) -> list[dict]:
    """Neighbouring heads in the same canopy row whose spacing isn't the design pitch."""
    rows: dict[float, list[float]] = defaultdict(list)
    for h in heads:
        rows[h.y].append(h.x)
    bad = []
    for y, xs in sorted(rows.items()):
        xs.sort()
        for left, right in itertools.pairwise(xs):
            if abs(right - left - pitch) > 1e-9:
                bad.append({"y": y, "x_left": left, "x_right": right, "gap": right - left})
    return bad
