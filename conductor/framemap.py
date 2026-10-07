"""A kit's controller map (D54): which frame entries drive its physical heads, and the head-count check built on it.

The real SQHDK03 reports a 60-entry frame with 30 pucks attached: the frame length is the controller's capacity,
not a count of heads. Per-head frames reach a kit's heads only through its own `framebuffer_mapping` (ops overlay,
NOT_FOR_GIT), and only on the kit it was proven on (D25). `frame_indices` is the one rule for "this device has a
valid map" (the module page, the Studio, the show's compiler and the visualizer use it); `head_check` is the one
head-count check (the show's capability reads, /api/devices, the attention list and the 7 am check).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from conductor.geometry import load_canopy_config, panel_lattice

MAP_MISMATCH = (
    "the saved controller map doesn't match this kit (D54): per-head looks are refused, whole-strand looks still "
    "work"
)


def capacity_problem(frame_length: int, heads: int) -> str:
    """A frame longer than the heads with no proven map: the controller's capacity, not extra or missing pucks."""
    return (
        f"the controller reports {frame_length} entries for {heads} heads and this kit has no proven map yet (D54): "
        "per-head looks are refused, whole-strand looks still work"
    )


def frame_indices(entry: Any, address: str | None, caps: Any, heads: int) -> list[int] | None:
    """The controller entry of each wire index (head i → entry `indices[i]`), or None when this kit has no valid map.

    Valid only when the map was proven on this device (`address`: the Afero metadevice id live, the sim address in
    sim — the address its capabilities were read from), its frame length is the one every slot reports now, and it
    maps exactly `heads` heads (the module map's), which the registry's `expected_heads` must equal when set."""
    mapping = entry.framebuffer_mapping
    if mapping is None:
        return None
    if (
        mapping.proven_on == address
        and mapping.frame_length == caps.framebuffer_length
        and len(mapping.indices) == heads == (entry.expected_heads or heads)
        and len(set(caps.framebuffer_lengths.values())) == 1
    ):
        return list(mapping.indices)
    return None


def unmapped_capacity(frame_length: int | None, expected_heads: int | None, heads: int) -> bool:
    """A frame longer than the module's heads that a proven map could serve (the registry agrees with the map)."""
    return frame_length is not None and expected_heads is not None and frame_length > expected_heads == heads


@dataclass(frozen=True)
class HeadCheck:
    """One module's head count. `kind`: None (fine), "mapped", "no_map" (a longer frame, no map), "map_mismatch",
    "short" (fewer entries than heads) or "other" (slots disagree, no length, a registry/map disagreement)."""

    problem: str | None = None
    kind: str | None = None

    @property
    def mapped(self) -> bool:
        return self.kind == "mapped"


def head_check(entry: Any, address: str | None, caps: Any, heads: int) -> HeadCheck:
    """The mapping-aware head-count check for the device at `address` (see `frame_indices`)."""
    if frame_indices(entry, address, caps, heads) is not None:
        return HeadCheck(kind="mapped")
    if entry.framebuffer_mapping is not None:
        return HeadCheck(MAP_MISMATCH, "map_mismatch")
    n, expected = caps.framebuffer_length, entry.expected_heads
    mixed = len(set(caps.framebuffer_lengths.values())) > 1
    if not mixed and unmapped_capacity(n, expected, heads):
        return HeadCheck(capacity_problem(n, expected), "no_map")
    problem = caps.head_count_problem(expected)
    if problem is None:
        return HeadCheck()
    short = not mixed and n is not None and expected is not None and n < expected
    return HeadCheck(problem, "short" if short else "other")


@lru_cache(maxsize=4)
def module_heads(config_dir: str | Path) -> int:
    """The heads on one module's map (the panel lattice in config/canopy.yaml)."""
    path = Path(config_dir) / "canopy.yaml"
    return len(panel_lattice(load_canopy_config(path) if path.exists() else load_canopy_config()))
