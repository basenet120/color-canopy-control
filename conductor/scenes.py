"""The six approved Color Canopy scenes (config/scenes.yaml, D59): one catalog for the guest panel, the show's modes
and Paris's Studio.

A scene is a named palette. On one strand of `heads` lights its look is a static gradient: head i sits at
t = i / (heads - 1), the palette's stops are evenly spaced over 0..1, each RGB channel is interpolated linearly and
rounded (`mirror` first replaces t with 1 - |2t - 1|, so the palette's last colour sits in the middle). This is the
same rule as `sceneColorAt` in web/ops/showcase/demo-model.mjs; tests pin both to Paris's handoff frames. Colours are
full strength: brightness is a separate device setting, never multiplied into the frame.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from conductor.config import CONFIG_DIR, ConfigError, _explain, _read_yaml, _Strict

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
RGB = tuple[int, int, int]


class PaletteColor(_Strict):
    name: str = Field(min_length=1, max_length=40)
    hex: str

    @field_validator("hex")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not HEX.match(v):
            raise ValueError("colours are #RRGGBB")
        return v.upper()

    @property
    def rgb(self) -> RGB:
        return tuple(int(self.hex[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


class Scene(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,40}$")
    name: str = Field(min_length=1, max_length=40)
    mood: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=1, max_length=200)
    transform: Literal["linear", "mirror"] = "linear"
    brightness_draft: int = Field(ge=1, le=100)
    default_solid: int = Field(ge=0)
    palette: list[PaletteColor] = Field(min_length=2, max_length=8)

    @field_validator("default_solid")
    @classmethod
    def _solid_in_palette(cls, v: int, info) -> int:
        palette = info.data.get("palette")
        if palette is not None and v >= len(palette):
            raise ValueError("default_solid must index a palette colour")
        return v

    def color_at(self, t: float) -> RGB:
        t = min(1.0, max(0.0, t))
        if self.transform == "mirror":
            t = 1 - abs(2 * t - 1)
        colors = [c.rgb for c in self.palette]
        at = t * (len(colors) - 1)
        a = int(at)
        b = min(a + 1, len(colors) - 1)
        f = at - a
        return tuple(_round(colors[a][c] * (1 - f) + colors[b][c] * f) for c in range(3))  # type: ignore[return-value]

    def frame(self, heads: int = 30) -> list[RGB]:
        """The scene on one strand of `heads` lights, full strength, head 0 nearest the controller."""
        return [self.color_at(i / max(1, heads - 1)) for i in range(heads)]

    @property
    def solid(self) -> PaletteColor:
        """The palette colour used when a strand can only take one colour (whole-strand fallback)."""
        return self.palette[self.default_solid]


class SceneCatalog(_Strict):
    scenes: list[Scene] = Field(min_length=1)

    @field_validator("scenes")
    @classmethod
    def _unique(cls, v: list[Scene]) -> list[Scene]:
        ids = [s.id for s in v]
        if len(set(ids)) != len(ids):
            raise ValueError("scene ids must be unique")
        return v

    def get(self, scene_id: str) -> Scene:
        for s in self.scenes:
            if s.id == scene_id:
                return s
        raise KeyError(scene_id)


def _round(x: float) -> int:
    """JavaScript's Math.round (half up), so frames match demo-model.mjs exactly."""
    return int(x + 0.5)


@lru_cache(maxsize=4)
def load_scenes(config_dir: Path = CONFIG_DIR) -> SceneCatalog:
    path = config_dir / "scenes.yaml"
    try:
        return SceneCatalog.model_validate(_read_yaml(path))
    except ValidationError as e:
        raise ConfigError(_explain(path, e)) from None
