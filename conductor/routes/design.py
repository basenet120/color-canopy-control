"""GET /api/canopy/design — one mode's 2-D design over the canopy, for the Studio's canopy illustration (ops only).

The Studio (`web/ops/showcase/`) used to draw a scene as its palette spread left to right (x / 225, D59). The scenes
are 2-D designs now (D63), so it asks the Conductor for the design instead and paints that, head for head:

- `GET /api/canopy/design?mode=<id>` →
  `{mode, label, ladder, design, heads: [{module, x, y, rgb}] × 270 | null, modules: [{module, rgb}] × 9 | null,
  sample_at}`

  - `heads`: every lattice head of the canopy (canopy inches, reflected-ceiling view, per module in lattice order:
    `canopy_view.canopy_geometry`) in the colour of the mode's design at that point, at full strength (the level is
    drawn separately, as the kiosk does). The design is the mode's T1 `canvas` technique when it has one (what T1
    paints on all 270 heads), else its module-level `sample` canvas (`design: "canvas" | "sample"`; both null for
    a mode without a design, e.g. one whole-strand look). Each head's colour goes through the composer's own
    `evaluate` and render's own per-head rule (`render.head_colors` at brightness 100), so it is the colour the
    compiled plan gives that head — the /visualizer's desired colour and the kiosk grid's head — not a second
    implementation of the palette.
  - `modules`: the module-level (T3) colour of each of the nine positions (r0c0 … r2c2) through the compiler's own
    `module_look` (the sample at the module's centre or its heads' mean, `sample_at`), or None when the mode's
    module level isn't one RGB colour per module.
  - `ladder`: the mode's techniques in order; its first is what the show tries first (`modules` for the
    modules-first scenes, so the Studio opens those at module level).

It reads config only: no engine, no device, no registry (a position draws the design whether or not a device is
registered there). Ops only: the auth middleware already keeps kiosk and tester out of /api/canopy/*.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from conductor import render
from conductor import rows as R
from conductor.canopy_view import canopy_geometry
from conductor.config import DeviceEntry
from conductor.engine_api import PlanError
from conductor.looks import CompileContext, evaluate, module_look
from conductor.roles_api import Principal
from conductor.routes.deps import require_ops, services_of

router = APIRouter()
Ops = Annotated[Principal, Depends(require_ops)]
NO_STORE = {"Cache-Control": "no-store"}
_SLOT = "custom-3"  # any slot: only the frame's entries matter to the per-head rule


def _context(services: Any) -> CompileContext:
    return CompileContext(registry=[], capabilities={}, config_dir=services.config_dir)


def _design_canvas(mode: Any) -> tuple[Any, str | None]:
    """The canvas the mode's design is drawn from: its T1 canvas, else its module-level sample's."""
    techniques = mode.techniques
    if techniques.canvas is not None:
        return techniques.canvas.canvas, "canvas"
    sample = techniques.modules.sample if techniques.modules is not None else None
    if sample is not None:
        return sample.canvas, "sample"
    return None, None


def _full_strength(entries: list[R.FrameEntry]) -> list[list[int]]:
    """The frame's heads as a lit strand shows them at brightness 100 (render's per-head rule, as the plan's
    `ModuleTarget.colors`)."""
    rows = R.full_state_vector(R.Look("slot", brightness=100, slot=_SLOT))
    value = {R.FRAME_CLASS: {"frameBuffer": {"framebuffer": [e.to_json() for e in entries]}}}
    colours = render.head_colors([*rows, R.slot_write(_SLOT, value)], len(entries))
    return [[c["r"], c["g"], c["b"]] for c in colours]


def _heads(canvas: Any, ctx: CompileContext) -> list[dict]:
    out = []
    for module in canopy_geometry(ctx.canopy)["modules"]:
        points = module["heads"]
        entries = [evaluate(canvas, x, y, canopy=ctx.canopy, recipes=ctx.recipes) for x, y in points]
        for (x, y), rgb in zip(points, _full_strength(entries), strict=True):
            out.append({"module": module["pos"], "x": x, "y": y, "rgb": rgb})
    return out


def _modules(mode: Any, ctx: CompileContext) -> list[dict] | None:
    if mode.techniques.modules is None:
        return None
    out = []
    for module in canopy_geometry(ctx.canopy)["modules"]:
        pos = module["pos"]
        entry = DeviceEntry(id=f"design-{pos}", role="canopy_module", account="design", module=pos)
        look = module_look(mode, entry, ctx)
        if look.kind != "rgb" or look.rgb is None:
            return None  # a white or preset module look has no one RGB colour to draw
        out.append({"module": pos, "rgb": [int(v) for v in look.rgb]})
    return out


def mode_design(mode_id: str, ctx: CompileContext) -> dict:
    """One mode's design (see the module docstring), from `ctx`'s modes, recipes and canopy."""
    assert ctx.modes is not None
    mode = ctx.modes.modes[mode_id]
    canvas, source = _design_canvas(mode)
    sample = mode.techniques.modules.sample if mode.techniques.modules is not None else None
    return {
        "mode": mode_id,
        "label": mode.label,
        "ladder": [str(t) for t in mode.ladder],
        "design": source,
        "heads": _heads(canvas, ctx) if canvas is not None else None,
        "modules": _modules(mode, ctx),
        "sample_at": sample.at if sample is not None else None,
    }


def design_json(services: Any, mode_id: str) -> dict | None:
    """The mode's design from the Conductor's config, or None for a mode that isn't in its modes.yaml."""
    ctx = _context(services)
    assert ctx.modes is not None
    return mode_design(mode_id, ctx) if mode_id in ctx.modes.modes else None


@router.get("/api/canopy/design")
def design(
    request: Request, _ops: Ops, mode: Annotated[str, Query(min_length=1, max_length=64)]
) -> JSONResponse:
    services = services_of(request)
    try:
        body = design_json(services, mode)
    except PlanError as exc:
        raise HTTPException(status_code=409, detail=f"The design of {mode!r} can't be drawn: {exc}") from exc
    if body is None:
        raise HTTPException(status_code=404, detail=f"There is no mode called {mode!r}.")
    return JSONResponse(body, headers=NO_STORE)
