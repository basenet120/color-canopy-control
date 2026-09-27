"""Looks (P2): the Lowcountry Modes as data, the canvas composer and the compiler to per-module plans.

config/modes.yaml ──load_modes()──▶ ModesFile
ModesFile + CompileContext ──compile_mode(mode, tier)──▶ engine_api.ModePlan (one PUT's rows per module)
canvas spec + placements ──compose_canvas()──▶ one frame per module (frame index = wire index)
"""

from conductor.looks.compile import (
    CompileContext,
    available_tiers,
    compile_first,
    compile_mode,
    ladder,
    module_look,
)
from conductor.looks.composer import (
    CanvasHead,
    Placement,
    canvas_heads,
    compose_canvas,
    evaluate,
    head_positions,
    placement_for,
    positions,
)
from conductor.looks.schema import ModesFile, load_modes, parse_modes

__all__ = [
    "CanvasHead",
    "CompileContext",
    "ModesFile",
    "Placement",
    "available_tiers",
    "canvas_heads",
    "compile_first",
    "compile_mode",
    "compose_canvas",
    "evaluate",
    "head_positions",
    "ladder",
    "load_modes",
    "module_look",
    "parse_modes",
    "placement_for",
    "positions",
]
