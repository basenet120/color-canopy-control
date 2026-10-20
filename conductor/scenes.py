"""The approved scenes (config/scenes.yaml, D59) and, from D76, options grouped under them by option_of: one catalog
for the guest panel, the show's modes and Paris's Studio.

A scene may name `option_of: <base id>` (D76): it is one of that base scene's options for the client review, and the
iPad's review menu shows the base and its options side by side in one row. One level deep (an option's base is never
itself an option), and the base must be a scene in the same catalog. The key is left out of every dump while it is
unset, so a catalog without options dumps exactly as before (`trio_sha`, published looks).

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

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    field_validator,
    model_serializer,
)

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
    # The scene this one is an option of (D76): set in git only, never by the Lab. Left out of every dump while None.
    option_of: str | None = Field(None, pattern=r"^[a-z][a-z0-9-]{1,40}$")

    @model_serializer(mode="wrap")
    def _omit_unset_option(self, handler: SerializerFunctionWrapHandler) -> dict:
        """Drop `option_of` while it is None, so a catalog without options dumps byte-identically to before D76:
        `ConfigTrio.dump()` feeds `trio_sha` and the stored publication, and an older Conductor's strict Scene would
        refuse a file that carries `option_of: null`."""
        data = handler(self)
        if isinstance(data, dict) and "option_of" in data and data["option_of"] is None:
            del data["option_of"]
        return data

    @property
    def group(self) -> str:
        """The scene group this scene belongs to on the iPad's review menu: its base's id, or its own."""
        return self.option_of or self.id

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

    @field_validator("scenes")
    @classmethod
    def _options(cls, v: list[Scene]) -> list[Scene]:
        """`option_of` names a base scene in this catalog, one level deep (D76). Runs after `_unique`, so a duplicate
        id is reported first. No ordering rule and no cap on options per base: the iPad wraps more than three."""
        by_id = {s.id: s for s in v}
        for s in v:
            base_id = s.option_of
            if base_id is None:
                continue
            if base_id == s.id:
                raise ValueError(f"scene {s.id!r} → option_of: a scene can't be an option of itself")
            base = by_id.get(base_id)
            if base is None:
                raise ValueError(
                    f"scene {s.id!r} → option_of: {base_id!r} isn't a scene in this catalog; name the id of the "
                    "scene it is an option of"
                )
            if base.option_of is not None:
                raise ValueError(
                    f"scene {s.id!r} → option_of: {base_id!r} is itself an option of {base.option_of!r}; options "
                    "group under a base scene, one level deep"
                )
        return v

    def get(self, scene_id: str) -> Scene:
        for s in self.scenes:
            if s.id == scene_id:
                return s
        raise KeyError(scene_id)

    def options_of(self, base_id: str) -> list[Scene]:
        """The scenes that name `base_id` as their option_of, in catalog order (D76)."""
        return [s for s in self.scenes if s.option_of == base_id]


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
