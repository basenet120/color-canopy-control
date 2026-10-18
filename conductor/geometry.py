"""Canopy geometry: where every head sits, in canopy inches, and its index along its module's strand."""

from __future__ import annotations

import itertools
from collections import defaultdict
from dataclasses import dataclass
from math import dist
from pathlib import Path
from typing import Any

from conductor.config import ConfigError, _read_yaml
from conductor.fins import load_fin_map, parse_vwbd_panels

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
    """canopy.yaml's `canopy` mapping, checked. Every device path loads the canopy through here.

    Besides the file's own keys (unchanged), the mapping carries:
    - `fins` (with a `fins:` section): the checked section as a plain dict, e.g.
      {'file': 'fins.json', 'apron_in': 6.0, 'fin_width_in': 5.0};
    - `fin_map`: the `conductor.fins.FinMap` of the file it names, next to canopy.yaml (D74);
    - `vwbd_panels` (with a `vwbd_panels:` section): VWBD's panel number per canopy position, all nine.

    ConfigError, naming the file and the fix, when canopy.yaml is missing, isn't valid YAML or has no `canopy:`
    section, when the fins section, its file or the panel numbers are wrong (`conductor.fins`), or when
    `orientation_scheme` isn't one the file lists while it has either section. A broken fins.json therefore stops
    the start and the deploy preflight. A canopy.yaml without the two sections loads exactly as before.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"{path} is missing. The canopy's geometry lives there; restore it from git.")
    data = _read_yaml(path)  # bad YAML: a ConfigError naming the file, the line and the column
    cfg = data.get("canopy") if isinstance(data, dict) else None
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path} has no canopy: section (the canopy's geometry). Restore it from git.")
    if "fins" in cfg:
        _check_scheme(path, cfg)
        cfg["fins"], cfg["fin_map"] = load_fin_map(
            path, cfg["fins"], cfg["width_in"], cfg["height_in"], lattice_heads(cfg)
        )
    if "vwbd_panels" in cfg:
        _check_scheme(path, cfg)
        cfg["vwbd_panels"] = parse_vwbd_panels(path, cfg["vwbd_panels"], _positions(cfg))
    return cfg


def _check_scheme(path: Path, cfg: dict[str, Any]) -> None:
    orientations = cfg.get("orientations")
    known = orientations if isinstance(orientations, dict) else {}
    scheme = cfg.get("orientation_scheme")
    if scheme not in known:
        raise ConfigError(f"{path} → canopy → orientation_scheme {scheme!r} isn't one of {sorted(known)}")


def _positions(cfg: dict[str, Any]) -> list[str]:
    """Every canopy position from the scheme grid, row-major: r0c0, r0c1, …"""
    grid = cfg["orientations"][cfg["orientation_scheme"]]
    return [f"r{r}c{c}" for r, row in enumerate(grid) for c in range(len(row))]


def lattice_heads(cfg: dict) -> list[tuple[float, float]]:
    """Every head's canopy point, module by module in row-major order, each module's panel lattice in its scheme
    orientation (B = A rotated 180°). Unlike `build_layout` it doesn't depend on the wiring: the same set of points."""
    mw, mh = float(cfg["module"]["width_in"]), float(cfg["module"]["height_in"])
    lattice = panel_lattice(cfg)
    out: list[tuple[float, float]] = []
    for r, grid_row in enumerate(cfg["orientations"][cfg["orientation_scheme"]]):
        for c, orientation in enumerate(grid_row):
            for px, py in lattice:
                if orientation == "B":
                    px, py = mw - px, mh - py
                out.append((c * mw + px, r * mh + py))
    return out


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


CABLE_PATHS = ("serpentine_rows", "serpentine_cols")
START_CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
_SEARCH_BUDGET = 200_000  # node expansions for the hop-repair search; far more than any 30-head panel needs


def panel_lattice(cfg: dict) -> list[tuple[float, float]]:
    """Every head position of a panel in its own (orientation A) frame, row by row, left to right."""
    module = cfg["module"]
    pitch = module["pitch_in"]
    return [
        (float(row["x0"] + pitch * i), float(row["y"])) for row in module["rows"] for i in range(row["count"])
    ]


def _naive_order(
    lattice: list[tuple[float, float]], cable_path: str, start_corner: str, pitch: float
) -> list[tuple[float, float]]:
    """Serpentine order from `start_corner`: rows (by y) or column groups (by x), alternating direction.

    In the staggered lattice a "column" is a pair of half-pitch x positions (x = 2.5 and 7.5 form one group),
    so walking down a group zigzags diagonally (11.2") instead of jumping 20" between same-x heads.
    """
    left = start_corner.endswith("left")
    top = start_corner.startswith("top")
    if cable_path == "serpentine_rows":
        ys = sorted({y for _, y in lattice}, reverse=not top)
        order: list[tuple[float, float]] = []
        for n, y in enumerate(ys):
            row = sorted((p for p in lattice if p[1] == y), reverse=not left)
            order += row[::-1] if n % 2 else row
        return order
    # Group from the starting side, so a right-hand start mirrors a left-hand one.
    edge = min(x for x, _ in lattice) if left else max(x for x, _ in lattice)
    groups: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for p in lattice:
        groups[int((abs(p[0] - edge) + 1e-6) // pitch)].append(p)
    order = []
    for n, key in enumerate(sorted(groups)):
        column = sorted(groups[key], key=lambda p: (p[1], p[0]), reverse=not top)
        order += column[::-1] if n % 2 else column
    return order


def _repair_hops(order: list[tuple[float, float]], max_hop: float) -> list[tuple[float, float]] | None:
    """The order closest to `order` (depth-first, always preferring the earliest remaining head) whose every
    hop is at most `max_hop`, starting from the same head. None when none is found within the search budget."""
    n = len(order)
    rank = {p: i for i, p in enumerate(order)}
    near = {
        p: sorted((q for q in order if q != p and dist(p, q) <= max_hop + 1e-9), key=rank.__getitem__)
        for p in order
    }
    path = [order[0]]
    used = {order[0]}
    stack = [iter(near[order[0]])]
    budget = _SEARCH_BUDGET
    while stack:
        if len(path) == n:
            return path
        budget -= 1
        if budget <= 0:
            return None
        nxt = next((q for q in stack[-1] if q not in used), None)
        if nxt is None:
            stack.pop()
            used.discard(path.pop())
            continue
        path.append(nxt)
        used.add(nxt)
        stack.append(iter(near[nxt]))
    return None


def panel_order(
    cfg: dict,
    cable_path: str | None = None,
    start_corner: str | None = None,
    wire_order: list[tuple[float, float]] | None = None,
) -> list[tuple[float, float]]:
    """A calibrated wire order for one panel in its own (orientation A) frame: entry i is head i's position.

    - `wire_order` (calibrated [x, y] pairs) wins when given; it must list every lattice point exactly once.
    - Otherwise a serpentine along rows or column groups (`cable_path`, default the canopy's) starting at
      `start_corner` (default top-left). Where the plain serpentine would need a hop longer than the puck
      spacing (`puck_spacing_in`), the order is re-routed locally so every hop fits; if no such order exists,
      the plain serpentine is returned and `longest_hop` shows the problem.

    `panel_order(cfg, "serpentine_rows", "top-left")` equals `panel_positions(cfg)` for a config without a
    wire_order.
    """
    lattice = panel_lattice(cfg)
    if wire_order:
        order = [(float(x), float(y)) for x, y in wire_order]
        if sorted(order) != sorted(lattice):
            raise ValueError("wire_order must list every head position of the panel exactly once")
        return order
    cable_path = cable_path or cfg.get("cable_path") or "serpentine_rows"
    start_corner = start_corner or "top-left"
    if cable_path not in CABLE_PATHS:
        raise ValueError(f"unknown cable_path {cable_path!r}; use {' or '.join(CABLE_PATHS)}")
    if start_corner not in START_CORNERS:
        raise ValueError(f"unknown start corner {start_corner!r}; use one of {', '.join(START_CORNERS)}")
    order = _naive_order(lattice, cable_path, start_corner, float(cfg["module"]["pitch_in"]))
    max_hop = float(cfg.get("puck_spacing_in") or 0)
    if max_hop and len(order) > 1 and longest_hop(order) > max_hop:
        order = _repair_hops(order, max_hop) or order
    return order


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
