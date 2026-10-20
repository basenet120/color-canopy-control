"""The guest panel's picture of the canopy: where its 270 lights hang, and what each position may truthfully claim
it shows right now (PR #19 follow-up 2, server half; the kiosk draws it, `web/kiosk/`).

Pure: no network, no engine calls, nothing written. Two parts, both served with GET /api/kiosk/panel:

- **Geometry** (`canopy_geometry`): the canopy in inches, every head of every module from `geometry.build_layout`,
  per module sorted top to bottom then left to right — the module's *lattice order*. It never depends on how a strand
  is wired, so the picture is the ceiling, not the cable. `view` is how the iPad turns that picture to match what a
  guest at the stand sees overhead (`kiosk.preview` in config/conductor.yaml, set at install). With the canopy's fin
  map (D74, config/fins.json through canopy.yaml → canopy → fins) it also lists the 650 fins (centre, plan axis,
  drop) and the outline the canopy plus its 6 in apron makes; never the file's free-text fields. Without one the
  apron is 0, the outline is the canopy and the fins are []. `lit_state` is unchanged by the fins: the kiosk mixes
  each fin's colour from the heads around it.
- **Lit state** (`lit_state`): per canopy position, from the current plan and the engine's own state, the first rule
  that matches:

  | a planned position locked by a test page, stuck past the window  | `unknown`  | no colour claim      |
  |   (not one stuck only for a flagged note on a confirmed look:    |            |                      |
  |   that one reads as confirmed, D78), or the canopy resting       |            |                      |
  |   (outage, stopped, starting, being set up), whether the guest   |            |                      |
  |   turned the canopy off or not                                   |            |                      |
  | the guest turned the canopy off: read back off, or not planned   | `off`      | no colour            |
  |   (a power-off still on its way reads `unknown` until then)      |            |                      |
  | the position isn't in the plan (left out, nothing registered)    | `dark`     | no colour            |
  | no known colours (a T2 slot whose content isn't known), or a     | `unknown`  | no colour claim      |
  |   look the strand runs on its own (a preset or program: its      |            |                      |
  |   preview is a guess)                                            |            |                      |
  | readback matches the look (with or without a flagged note: the   | `lit`      | the target colours   |
  |   record's `flagged_only`)                                       |            |                      |
  | anything else (a change in flight, a retry)                      | `changing` | the target colours   |

  Colours are the plan's full-strength `ModuleTarget.colors` (the level is `level`, drawn separately), moved from wire
  order into lattice order through the module's own placement (`lattice_slots`). A module whose wiring doesn't map
  one to one onto the lattice shows one flat mean colour instead, and the server logs why. `color` is set when every
  head is the same; otherwise `colors` lists the module's heads in lattice order.

- **Motion** (`motion`): while a new mood is landing ring by ring (D64, the engine's `CanopyState.ripple`), `{kind:
  "ripple", t0, stagger_s, rings: [[pos]]}` — ring j is sent at t0 + j × stagger_s (wall seconds, the panel's
  `server_now` clock), so the kiosk switches each ring's positions from the previous picture when its turn comes. Only
  positions that are `lit` or `changing` are in a ring (an off, dark or unknown one is drawn as it is); a ring left
  empty keeps its place so the times stay right. None once the last ring is sent, when no ring has such a position
  (off, resting), or when the look went out as one burst.

  While a stepped look moves (Tier.STEPPED, the engine's `CanopyState.program`), `{kind: "steps", t0, step_s,
  frames: [[hex | null] × 9 in grid order r0c0…r2c2], ends_at, levels?}` (wall seconds): frame n is what step n
  sent, from t0 + n × step_s, and the last frame is the still look the run ends on, sent at the program's `ends_at`.
  Exactly the frames the engine sends, nothing in between (the kiosk switches, it doesn't blend); a budget-held step
  repeats the step last sent (D75). `levels` (D75, when the program carries them: [[int | null] × 9] alongside
  frames) is the brightness each panel is sent at that frame, the guest's level applied — the panel draws it as that
  panel's opacity, while colours stay full strength (D59). A position not `lit` or `changing` (left out, locked,
  stuck, unknown) is null in every frame and level, so the picture never moves a panel nobody can vouch for. The motion's `ends_at` is one step after the still look is sent, so a page that hasn't heard the
  run is over draws that last frame (the kiosk clamps its time to just before `ends_at`) and not the step before it.
  None once the still look is sent (the plan's own colours are then the picture), while the run's writes are held
  (the engine reports no program then: paused, resting, off), or when no position is lit or changing.

Nothing in either part names a device, a label, a reason or the vendor: positions only ("r0c0" … "r2c2").
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from conductor.engine_api import CanopyPhase, ModePlan, ModuleTarget
from conductor.geometry import build_layout, load_canopy_config
from conductor.looks.composer import head_positions, placement_for
from conductor.render import sequence_preview

log = logging.getLogger(__name__)

OFF, DARK, UNKNOWN, LIT, CHANGING = "off", "dark", "unknown", "lit", "changing"
GRID = [
    f"r{r}c{c}" for r in range(3) for c in range(3)
]  # the order of a stepped run's frames (sequence.GRID)
# (module, why) already logged: a flat fallback is logged once, not on every request
_warned: set[tuple[str, str]] = set()


@lru_cache(maxsize=4)
def load_canopy(config_dir: str) -> dict:
    """config/canopy.yaml's `canopy` mapping from `config_dir` (the repo's own when the directory has none)."""
    path = Path(config_dir) / "canopy.yaml"
    return load_canopy_config(path) if path.exists() else load_canopy_config()


def _key(cfg: Mapping[str, Any]) -> str:
    """The canopy mapping as a cache key, without its fin map (the heads never depend on it, and its str is short
    but its contents aren't JSON)."""
    return json.dumps({k: v for k, v in cfg.items() if k != "fin_map"}, sort_keys=True, default=str)


def _point(x: float, y: float) -> tuple[int, int]:
    """A head's lattice key: half-inch resolution, so float noise can never split or merge two heads."""
    return round(2 * x), round(2 * y)


@lru_cache(maxsize=8)
def _modules(cfg_key: str) -> tuple[tuple[str, int, int, tuple[tuple[float, float], ...]], ...]:
    """(pos, row, col, heads in lattice order) per module, row-major."""
    heads = build_layout(json.loads(cfg_key))
    out: dict[str, list] = {}
    for h in heads:
        out.setdefault(h.module, [h.row, h.col, []])[2].append((float(h.x), float(h.y)))
    modules = [
        (pos, row, col, tuple(sorted(points, key=lambda p: (p[1], p[0]))))
        for pos, (row, col, points) in out.items()
    ]
    return tuple(sorted(modules, key=lambda m: (m[1], m[2])))


def _fins_part(cfg: Mapping[str, Any]) -> dict:
    """The fins and the outline (D74): `{apron_in, outline: {x: [lo, hi], y: [lo, hi]}, fin_width_in, fins: [{x, y,
    angle_deg, drop_in}]}`; apron 0, the canopy's own outline, width 0 and no fins without a fin map."""
    fm = cfg.get("fin_map")
    w, h = float(cfg["width_in"]), float(cfg["height_in"])
    a = float(fm.apron_in) if fm is not None else 0.0
    return {
        "apron_in": a,
        "outline": {"x": [-a, w + a], "y": [-a, h + a]},
        "fin_width_in": float(fm.fin_width_in) if fm is not None else 0.0,
        "fins": fm.payload() if fm is not None else [],
    }


def canopy_geometry(cfg: Mapping[str, Any], view: Any = None) -> dict:
    """`{width_in, height_in, module_w_in, module_h_in, view: {mirror_x, rotate_deg, dim_floor}, modules: [{pos, row,
    col, heads: [[x, y] × 30]} × 9], apron_in, outline: {x: [lo, hi], y: [lo, hi]}, fin_width_in, fins: [{x, y,
    angle_deg, drop_in}] (650; [] without a fin map)}`, heads in canopy inches in lattice order. `view` is
    `kiosk.preview` (a `PreviewSettings`; None = the defaults: unmirrored, unrotated, floor 0.35)."""
    modules = _modules(_key(cfg))
    return {
        "width_in": cfg["width_in"],
        "height_in": cfg["height_in"],
        "module_w_in": cfg["module"]["width_in"],
        "module_h_in": cfg["module"]["height_in"],
        "view": {
            "mirror_x": bool(getattr(view, "mirror_x", False)),
            "rotate_deg": int(getattr(view, "rotate_deg", 0)),
            "dim_floor": float(getattr(view, "dim_floor", 0.35)),
        },
        "modules": [
            {"pos": pos, "row": row, "col": col, "heads": [[x, y] for x, y in heads]}
            for pos, row, col, heads in modules
        ],
        **_fins_part(cfg),
    }


def _lattice(cfg: Mapping[str, Any], pos: str) -> tuple[tuple[float, float], ...] | None:
    for p, _row, _col, heads in _modules(_key(cfg)):
        if p == pos:
            return heads
    return None


def _entry_key(entry: Any) -> str:
    fields = ("id", "module", "orientation", "cable_path", "start_corner", "wire_order", "expected_heads")
    return json.dumps({f: getattr(entry, f, None) for f in fields}, sort_keys=True, default=str)


@lru_cache(maxsize=64)
def _slots(cfg_key: str, entry_key: str) -> tuple[tuple[int, ...] | None, str | None]:
    cfg = json.loads(cfg_key)
    entry = SimpleNamespace(**json.loads(entry_key))
    lattice = _lattice(cfg, str(entry.module))
    if lattice is None:
        return None, f"{entry.module!r} isn't a canopy position"
    try:
        wire = head_positions(cfg, placement_for(cfg, entry, frame_length=None))
    except Exception as e:  # noqa: BLE001 - PlanError from a calibration that can't be used: the flat fallback
        return None, f"its placement can't be used ({e})"
    where = {_point(x, y): j for j, (x, y) in enumerate(lattice)}
    slots = tuple(where.get(_point(x, y), -1) for x, y in wire)
    if len(wire) != len(lattice) or -1 in slots or len(set(slots)) != len(slots):
        return None, "its wiring doesn't map one to one onto the panel lattice"
    return slots, None


def lattice_slots(cfg: Mapping[str, Any], entry: Any) -> list[int] | None:
    """Wire index → lattice slot for a placed registry entry (its own orientation and calibration, as the compiler
    places it), or None when that isn't a one-to-one map onto the position's lattice (the module is then shown as one
    flat mean colour; the reason is logged once)."""
    slots, why = _slots(_key(cfg), _entry_key(entry))
    if slots is None:
        _warn(str(getattr(entry, "id", "?")), str(why))
        return None
    return list(slots)


def _warn(module: str, why: str) -> None:
    if (module, why) not in _warned:
        _warned.add((module, why))
        log.warning("Guest preview: %s is shown as one flat colour: %s.", module, why)


def _hex(c: Mapping[str, Any]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(float(c.get(k, 0))))):02X}" for k in ("r", "g", "b"))


def _mean(colors: Sequence[Mapping[str, Any]]) -> dict:
    n = len(colors)
    return {k: round(sum(float(c.get(k, 0)) for c in colors) / n) for k in ("r", "g", "b")}


def module_colours(cfg: Mapping[str, Any], entry: Any, colors: Sequence[Mapping[str, Any]]) -> list[str]:
    """The module's full-strength head colours as "#RRGGBB" in lattice order (one flat mean on every head when its
    wiring doesn't map one to one, or the plan has a different number of heads than the lattice)."""
    lattice = _lattice(cfg, str(entry.module)) or ()
    slots = lattice_slots(cfg, entry)
    if slots is not None and len(colors) != len(slots):
        _warn(str(entry.id), f"the plan has {len(colors)} head colours for {len(slots)} lattice heads")
        slots = None
    if slots is None:
        return [_hex(_mean(colors))] * len(lattice)
    out = [""] * len(slots)
    for i, j in enumerate(slots):
        out[j] = _hex(colors[i])
    return out


def _runs_its_own(target: ModuleTarget) -> bool:
    """A preset or program: the strand's own controller plays it, so its preview is a guess, not a colour claim."""
    return sequence_preview(target.rows) is not None


def _claim(
    cfg: Mapping[str, Any], entry: Any, target: ModuleTarget
) -> tuple[str | None, list[str] | None] | None:
    """The colours a planned position claims, as `(color, colors)`: one "#RRGGBB" when every head is the same, else
    the heads in lattice order. None when there is no known colour (a T2 slot whose content isn't known) or the
    strand plays the look on its own (a preset or program: its preview is a guess)."""
    if not target.colors or _runs_its_own(target):
        return None
    colours = module_colours(cfg, entry, target.colors)
    if len(set(colours)) == 1:
        return colours[0], None
    return None, colours


def plan_picture(cfg: Mapping[str, Any], registry: Any, plan: ModePlan) -> list[dict] | None:
    """The still picture `plan` puts on the canopy, as Now Showing draws it once every position is lit:
    `[{pos, color, colors}] × 9` in grid order, a position the plan doesn't drive dark (both None). The same colour
    claim `lit_state` makes (`_claim`), without the engine's readback states. None when a driven position makes no
    colour claim: there is then no truthful still picture of the look. The iPad's review card draws this for a
    whole-strand plan (guest_panel.thumb_plans, D76)."""
    by_position = {e.module: e for e in registry.canopy_modules()}
    out: list[dict] = []
    for pos, _row, _col, _heads in _modules(_key(cfg)):
        entry = by_position.get(pos)
        target = plan.modules.get(entry.id) if entry is not None else None
        item: dict[str, Any] = {"pos": pos, "color": None, "colors": None}
        if target is not None:
            claim = _claim(cfg, entry, target)
            if claim is None:
                return None
            item["color"], item["colors"] = claim
        out.append(item)
    return out


def lit_state(
    cfg: Mapping[str, Any], registry: Any, plan: ModePlan | None, state: Any, now: float | None = None
) -> dict | None:
    """`{level, modules: [{pos, state, color, colors} × 9], motion}` for the guest panel (see the module docstring),
    or None before the first look. `state` is the engine's CanopyState as the kiosk reads it (`kiosk_view`); `now`
    is the server's wall clock (a stepped run's motion ends once its still look is sent; None: never judged)."""
    if plan is None:
        return None
    on = getattr(state, "guest_power", True) is not False
    # the engine is stopped, in an outage, starting or being set up (paused guest input still shows the look)
    resting = state.phase == CanopyPhase.RESTING and bool(getattr(state, "guest_input", True))
    by_position = {e.module: e for e in registry.canopy_modules()}
    records = getattr(state, "modules", None) or {}
    modules = []
    for pos, _row, _col, _heads in _modules(_key(cfg)):
        entry = by_position.get(pos)
        target = plan.modules.get(entry.id) if entry is not None else None
        record = (records.get(entry.id) or {}) if entry is not None else {}
        item: dict[str, Any] = {"pos": pos, "state": OFF, "color": None, "colors": None}
        # D78: needing attention only for a flagged note (D54's "no proven map yet") with the look sent, read back and
        # matching is stuck for /ops (D73), but the lights show the look: it reads as confirmed
        flagged_only = record.get("flagged_only") is True
        confirmed = record.get("status") == "ready" or flagged_only
        stuck = bool(record.get("stuck")) and not flagged_only
        if target is not None and (record.get("locked") or stuck or resting):
            # the engine skips a locked module, a stuck one didn't take the last write, and a resting canopy sends
            # nothing: whatever the guest asked for, on or off, isn't known to be there
            item["state"] = UNKNOWN
        elif not on:
            # off only once the readback says so; until then the power-off is on its way and makes no claim
            if target is not None and not confirmed:
                item["state"] = UNKNOWN
        elif target is None:
            item["state"] = DARK
        elif (claim := _claim(cfg, entry, target)) is None:
            item["state"] = UNKNOWN
        else:
            item["state"] = LIT if confirmed else CHANGING
            item["color"], item["colors"] = claim
        modules.append(item)
    return {
        "level": getattr(state, "brightness", None) if on else None,
        "modules": modules,
        # off or resting: nothing is lit or changing, so no ring has a position and there is no motion
        "motion": _ripple_motion(getattr(state, "ripple", None), modules)
        or _steps_motion(getattr(state, "program", None), modules, now),
    }


def _ripple_motion(ripple: Any, modules: Sequence[Mapping[str, Any]]) -> dict | None:
    """The engine's ripple in progress as the panel's motion, over the positions it really changes (lit/changing)."""
    if not isinstance(ripple, Mapping) or not isinstance(ripple.get("rings"), list):
        return None
    moving = {m["pos"] for m in modules if m["state"] in (LIT, CHANGING)}
    rings = [[pos for pos in ring if pos in moving] for ring in ripple["rings"]]
    if not any(rings):
        return None
    return {"kind": "ripple", "t0": ripple["t0"], "stagger_s": ripple["stagger_s"], "rings": rings}


def _steps_motion(program: Any, modules: Sequence[Mapping[str, Any]], now: float | None) -> dict | None:
    """The engine's stepped run as the panel's motion: its frames over the positions really lit or changing."""
    if (
        not isinstance(program, Mapping)
        or not isinstance(program.get("frames"), list)
        or not program["frames"]
    ):
        return None
    step_s, ends_at = float(program["step_s"]), float(program["ends_at"])
    if now is not None and now >= ends_at:
        return None  # the still look is sent: the plan's own colours are the picture
    moving = {m["pos"] for m in modules if m["state"] in (LIT, CHANGING)}
    if not moving:
        return None
    frames = [
        [hex_ if pos in moving else None for pos, hex_ in zip(GRID, frame, strict=False)]
        for frame in program["frames"]
    ]
    motion = {
        "kind": "steps",
        "t0": program["t0"],
        "step_s": step_s,
        "frames": frames,
        # one step past the still look's send time, so the page ends on that frame (see the module docstring)
        "ends_at": ends_at + step_s,
    }
    levels = program.get("levels")
    if isinstance(levels, list) and len(levels) == len(frames):  # D75: each frame's levels, where it is drawn
        motion["levels"] = [
            [
                lv
                if pos in moving and hex_ is not None and isinstance(lv, int) and not isinstance(lv, bool)
                else None
                for pos, hex_, lv in zip(GRID, frame, row, strict=False)
            ]
            for frame, row in zip(frames, levels, strict=False)
        ]
    times = program.get("times")
    if isinstance(times, list) and len(times) == len(frames):  # the rate test's uneven steps (D80)
        motion["times"] = list(times)
    heads, head_steps = program.get("heads"), program.get("head_steps")
    if isinstance(heads, list) and isinstance(head_steps, list) and len(head_steps) == len(frames):
        # D80: a per-light run — each step's lights per position ("RGBL" per head), drawn where the panel is lit
        motion["head_steps"] = list(head_steps)
        motion["heads"] = [
            [h if pos in moving and isinstance(h, str) else None for pos, h in zip(GRID, step, strict=False)]
            for step in heads
        ]
    return motion
