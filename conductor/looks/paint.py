"""The iPad's Paint tab (Oct 21): a guest's painting as a look the engine shows like any other.

A painting is every canopy position's lights in lattice order (`canopy_view.canopy_geometry`'s order, each panel as
it hangs), each "#RRGGBB" or None (off). The Paint tab's three ways — one colour for the whole canopy, one colour
per panel, or drawing light by light — all send this one shape. It compiles under the mode id `PAINT_ID`, which the
show wiring answers itself (it is not in the catalog, so it never has a card):

- **stepped** (per light): a still per-light picture — every light its painted colour, an unpainted light off — sent
  once into `motion.lights.slot` and held, exactly as a still look (`effect: still`); needs per-light (sim, or live
  with `motion.lights.live_enabled`, Gate C and a map);
- **modules** (T3): each panel one colour, the average of its painted lights (off when none is painted);
- **strand**: every panel the average of the whole painting.

Copy is true of each tier. Pure: no I/O."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from conductor import rows as R
from conductor.engine_api import ModePlan, PlanError, Tier
from conductor.looks.compile import (
    OFF,
    CompileContext,
    _count_words,
    _lights_plan,
    _Notes,
    _target,
    _to_look,
    _vector,
    lights_problem,
)
from conductor.looks.schema import LightsMotion, LookSpec

PAINT_ID = "paint"
PAINT_LABEL = "Your painting"
PAINT_KINDS = ("whole", "modules", "lights")
LADDER = [Tier.STEPPED, Tier.T3, Tier.STRAND]
HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
STEPPED_COPY = (
    "Colour and dimming through the Hubspace cloud: every light on the canopy set on its own to the colours painted "
    "on the iPad."
)


class PaintError(ValueError):
    """A painting that can't be shown (a plain sentence for the guest panel)."""


@dataclass(frozen=True)
class Painting:
    kind: str
    heads: Mapping[str, tuple[str | None, ...]]  # position → its lights in lattice order
    brightness: int = 100
    lit: int = field(default=0)

    def colours(self) -> list[str]:
        return [c for heads in self.heads.values() for c in heads if c is not None]


def parse_painting(body: Mapping[str, Any], geometry: Mapping[str, Any]) -> Painting:
    """The Paint tab's request (`{kind, modules: [{pos, colors: [hex | null] × the panel's lights}]}`) checked
    against the panel geometry it was drawn on. PaintError (a plain sentence) when it doesn't fit."""
    kind = body.get("kind")
    if kind not in PAINT_KINDS:
        raise PaintError(f"Paint as one of {', '.join(PAINT_KINDS)}.")
    sizes = {m["pos"]: len(m["heads"]) for m in geometry["modules"]}
    heads: dict[str, tuple[str | None, ...]] = {}
    for item in body.get("modules") or []:
        pos = item.get("pos") if isinstance(item, Mapping) else None
        if pos not in sizes:
            raise PaintError("That painting names a panel the canopy doesn't have.")
        colors = item.get("colors")
        if not isinstance(colors, list) or len(colors) != sizes[pos]:
            raise PaintError(
                "That painting doesn't match the canopy's lights; reload the page and paint again."
            )
        clean: list[str | None] = []
        for c in colors:
            if c is None:
                clean.append(None)
            elif isinstance(c, str) and HEX.match(c):
                clean.append(c.upper())
            else:
                raise PaintError("A colour in that painting isn't a colour.")
        heads[pos] = tuple(clean)
    if not heads:
        raise PaintError("Paint something first.")
    lit = sum(1 for colors in heads.values() for c in colors if c is not None)
    if lit == 0:
        raise PaintError("Paint something first: every light is off.")
    return Painting(kind=kind, heads=heads, lit=lit)


def _rgb(hexs: str) -> tuple[int, int, int]:
    return int(hexs[1:3], 16), int(hexs[3:5], 16), int(hexs[5:7], 16)


def _mean(colours: Sequence[str]) -> tuple[int, int, int] | None:
    if not colours:
        return None
    rgbs = [_rgb(c) for c in colours]
    return tuple(round(sum(ch) / len(rgbs)) for ch in zip(*rgbs, strict=True))  # type: ignore[return-value]


def _devices(ctx: CompileContext) -> tuple[list[Any], dict[str, str]]:
    """The modules a painting can drive now (a device registered and read), and why the others were left out."""
    drivable, skipped = [], {}
    for d in ctx.devices:
        if ctx.addresses is not None and not ctx.addresses.get(d.id):
            skipped[d.id] = f"{d.label or d.id} has no Hubspace device registered on this runtime"
        elif d.id not in ctx.capabilities:
            skipped[d.id] = f"{d.label or d.id}'s profile hasn't been read yet"
        else:
            drivable.append(d)
    if not drivable:
        raise PlanError("there are no canopy modules that can be painted right now")
    return drivable, skipped


def compile_paint(painting: Painting, tier: Tier | str, ctx: CompileContext) -> ModePlan:
    """The plan that shows `painting` at `tier` (see the module docstring), or PlanError."""
    from conductor.canopy_view import lattice_slots

    tier = Tier(tier)
    if tier not in LADDER:
        raise PlanError(f"{PAINT_LABEL} has no {tier} technique")
    assert ctx.canopy is not None
    devices, skipped = _devices(ctx)
    if tier is Tier.STEPPED:
        if not ctx.lights:
            raise PlanError("per-light pictures aren't on for this canopy")
        if (why := lights_problem(ctx, devices)) is not None:
            raise PlanError(why)
        paint: dict[str, list[R.FrameEntry]] = {}
        for d in devices:
            colors = painting.heads.get(str(d.module))
            slots = lattice_slots(ctx.canopy, d)
            if colors is None or slots is None or len(slots) != len(colors):
                raise PlanError(f"{d.label or d.id} can't be painted light by light")
            paint[d.id] = [
                OFF if colors[j] is None else R.FrameEntry(*_rgb(colors[j]), 0, 100, 0)  # type: ignore[arg-type]
                for j in slots
            ]
        lights = LightsMotion(
            effect="still",
            steps_per_cycle=4,
            step_s=2.0,
            run_s=8,
            floor=0,
            peak=100,
            brightness=painting.brightness,
            moving_feature=STEPPED_COPY,
        )
        plan = _lights_plan(PAINT_ID, None, ctx, devices, lights, paint=paint, label=PAINT_LABEL)
        assert plan.program is not None
        plan.program.moving_feature = STEPPED_COPY
        plan.skipped = dict(skipped)
        return plan
    notes = _Notes()
    whole = _mean(painting.colours())
    modules = {}
    for d in devices:
        c = ctx.caps(d.id)
        if tier is Tier.T3:
            rgb = _mean([x for x in painting.heads.get(str(d.module), ()) if x is not None])
        else:
            rgb = whole
        spec = (
            LookSpec(kind="rgb", rgb=rgb, brightness=painting.brightness)
            if rgb is not None
            else LookSpec(kind="off")
        )
        look = _to_look(spec, c, notes, d.id)
        modules[d.id] = _target(d, c, _vector(look, c), indices=ctx.frame_indices(d, c))
    n = len(devices)
    strands = f"all {_count_words(n)} Hubspace strands" if n > 1 else "the Hubspace strand"
    feature = (
        "Colour and dimming: each Hubspace strand set to a single colour, the average of the colours painted on its "
        "panel."
        if tier is Tier.T3
        else f"Colour and dimming: {strands} set to one colour, the average of the painting."
    )
    plan = ModePlan(PAINT_ID, PAINT_LABEL, tier, feature, False, modules, notes.lines(n))
    plan.skipped = dict(skipped)
    return plan
