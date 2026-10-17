"""Design Lab edits written against whatever config/ approves today.

Tests that run the real app read config/, and a Lab export changes config/ (make lab-roundtrip applies one and runs
the whole suite). So an edit those tests make must differ from the approved value it replaces, whatever that is:
these helpers pick the brief's Done-means values and step aside when config/ already holds them.
"""

from __future__ import annotations

from typing import Any

NEW_PINK = "#FF8FBF"  # the brief's Done-means colour for Golden Hour's Sunset pink
OTHER_PINK = "#FF7FB0"
DONE_ANGLE = 30  # linear 30°: no D63 warning for Golden Hour (§3.4)
OTHER_ANGLE = 45


def other_hex(current: str, prefer: str = NEW_PINK, fallback: str = OTHER_PINK) -> str:
    """`prefer`, unless it is already the approved hex."""
    return fallback if current.upper() == prefer.upper() else prefer


def rgb(hex_: str) -> list[int]:
    return [int(hex_[i : i + 2], 16) for i in (1, 3, 5)]


def done_means(seed: dict[str, Any], index: int = 2) -> dict[str, Any]:
    """The Done-means edit of a seeded draft (model_dump(mode="json") shape), as changes for the tests' `edit`
    helpers: one colour, the layout's angle and the brightness, each different from the seed's."""
    layout = seed["layout"]
    angle = (
        OTHER_ANGLE
        if layout.get("kind") == "linear" and layout.get("angle_deg") == DONE_ANGLE
        else DONE_ANGLE
    )
    return {
        f"palette.{index}.hex": other_hex(seed["palette"][index]["hex"]),
        "layout": {"kind": "linear", "angle_deg": angle},
        "brightness": 89 if seed["brightness"] == 90 else 90,
    }


def recoloured(seed: dict[str, Any], hex_: str | None = None, index: int = 2) -> dict[str, Any]:
    """A seeded draft (model_dump(mode="json") shape) with one colour's hex changed: `hex_`, or the Done-means pink
    unless that is already approved (index 2 is Golden Hour's Sunset pink today)."""
    palette = [dict(c) for c in seed["palette"]]
    palette[index]["hex"] = hex_ or other_hex(palette[index]["hex"])
    return {**seed, "palette": palette}
