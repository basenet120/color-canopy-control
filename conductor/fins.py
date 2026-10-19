"""The canopy's 650 Mylar fins (D74) as validated data: `config/fins.json`, named by `canopy.yaml → canopy → fins`.

The fins hang under the lights. VWBD drawing A-20-1 ('Fin diagram with lights', Oct 18) gives each one's centre, plan
axis and drop; `fins.json` is its extract in the canopy frame (reflected-ceiling view, inches, origin top-left): the
sheet turned 90° counter-clockwise, x = v − 6, y = 126 − u (u, v = inches from the sheet's top-left corner). The
fins reach 6 in beyond the canopy's edge on every side (the apron).

The fins field (`conductor/looks/field.py`, kind `fins`) reads the drop around a canopy point:
  D = Σ w·drop / Σ w over the fins whose centre is within FIN_CUT_IN (12 in) of it, with w = exp(−(d / 6)²),
and t = (deepest − D) / (deepest − shallowest), clamped to [0, 1], so the deep centre is 0 and the 14 in rim is 1.
The weight is exp(−(d / 6)²), NOT the textbook Gaussian exp(−d² / (2·6²)): it is the form
docs/plans/preset-options/lights.json was made with (it matches that file's fin_drop_in to 0.0102 in at all 270
heads, measured; exp(−d² / 72) misses by up to 0.192 in on 119 of them). Don't "fix" it; the plan's "σ 6″" wording
has an erratum (D74).

Rules implemented:
- **Strict file.** Unknown keys are refused at every level, numbers must be finite, angles are 0–180°, drops
  0–60 in. Then the drawing's own rules: 650 fins; the count and the drop key agree, drop by drop; at least two
  different drops (else the field has no depth); the apron and the fin width agree with canopy.yaml; the outline is
  the canopy plus its apron and every fin hangs inside it; no two fins share a centre; and every light has a fin
  within 12 in (else the field has no value there). Problems are ConfigError sentences naming the file, the entry
  and the fix: a bad file stops the start and the deploy preflight.
- **Strict resolution.** `file` is a bare name next to canopy.yaml; there is no fallback to the repo's config/.
- **Loaded once.** The map is cached per (file, mtime, size, section, canopy size, heads): a later load costs a stat.
- **Pure after load.** A `FinMap` is immutable; the drop at every head is a table, other points are computed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from conductor.config import ConfigError

FIN_SIGMA_IN = 6.0  # w = exp(−(d / FIN_SIGMA_IN)²): the form lights.json was made with (see the docstring)
FIN_CUT_IN = 12.0  # fins farther than this from a point don't count towards its drop
FINS_EXPECTED = 650  # drawing A-20-1
MAX_LISTED = 10  # problem bullets shown before "… and K more"
_BUCKET_IN = (
    FIN_CUT_IN  # buckets as wide as the cut: every fin within it sits in the 3 × 3 buckets around a point
)
_PANEL = re.compile(r"^P-0[1-9]$")
_RESTORE = "Restore it from git (VWBD drawing A-20-1, extracted) and restart."
_Strict = ConfigDict(extra="forbid", allow_inf_nan=False)


# ---- the files' shapes ---------------------------------------------------------------------------------------------
class FinsSection(BaseModel):
    """canopy.yaml → canopy → fins. Frozen: it is part of the loader's cache key."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)

    file: str = Field(min_length=1, max_length=200)
    apron_in: float = Field(ge=0, le=24)
    fin_width_in: float = Field(gt=0, le=24)

    @field_validator("file")
    @classmethod
    def _bare_name(cls, v: str) -> str:
        if "/" in v or "\\" in v or v.startswith("."):
            raise ValueError("file must be a file name next to canopy.yaml (no folders), e.g. fins.json")
        return v


class Fin(BaseModel):
    model_config = _Strict

    x: float
    y: float
    angle_deg: float = Field(ge=0, le=180)
    drop_in: float = Field(gt=0, le=60)


class DropKeyEntry(BaseModel):
    model_config = _Strict

    drawing_colour: str = Field(min_length=1, max_length=40)
    drop_in: float = Field(gt=0, le=60)
    count: int = Field(ge=1)


class Outline(BaseModel):
    model_config = _Strict

    x: tuple[float, float]
    y: tuple[float, float]


class FinsFile(BaseModel):
    model_config = _Strict

    source: str
    frame: str
    apron_in: float
    outer_in: Outline
    fin_plan_width_in: float
    angle: str
    drop_key: list[DropKeyEntry] = Field(min_length=1)
    count: int
    fins: list[Fin] = Field(min_length=1)


# ---- the map -------------------------------------------------------------------------------------------------------
class FinPoint(NamedTuple):
    x: float
    y: float
    angle_deg: float
    drop_in: float


def _bucket(x: float, y: float) -> tuple[int, int]:
    return math.floor(x / _BUCKET_IN), math.floor(y / _BUCKET_IN)


@dataclass(frozen=True, eq=False, repr=False)
class FinMap:
    """The drawing's fins, immutable, with the drop at every head precomputed. Built by `build_fin_map`.

    Equal (and hashed) by the file's digest, the apron and the fin width; copying returns the map itself, so a canopy
    mapping that carries one stays cheap to copy, compare and key."""

    fins: tuple[FinPoint, ...]
    apron_in: float
    fin_width_in: float
    deep_in: float  # the deepest drop in the key (24.5 in)
    shallow_in: float  # the shallowest (14 in); always < deep_in, since a one-drop key is refused
    digest: str  # sha256[:12] of the file's bytes
    _buckets: Mapping[tuple[int, int], tuple[int, ...]] = field(init=False)
    _heads: Mapping[tuple[float, float], float | None] = field(init=False)

    def __post_init__(self) -> None:
        buckets: dict[tuple[int, int], list[int]] = {}
        for i, f in enumerate(self.fins):
            buckets.setdefault(_bucket(f.x, f.y), []).append(i)
        object.__setattr__(self, "_buckets", MappingProxyType({k: tuple(v) for k, v in buckets.items()}))
        object.__setattr__(self, "_heads", MappingProxyType({}))

    def _drop(self, x: float, y: float) -> float | None:
        cx, cy = _bucket(x, y)
        total = weighted = 0.0
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for i in self._buckets.get((gx, gy), ()):
                    f = self.fins[i]
                    d = math.hypot(f.x - x, f.y - y)
                    if d <= FIN_CUT_IN:
                        w = math.exp(-((d / FIN_SIGMA_IN) ** 2))
                        total += w
                        weighted += w * f.drop_in
        return weighted / total if total else None

    def drop_at(self, x: float, y: float) -> float | None:
        """The weighted mean drop (inches) of the fins within 12 in of (x, y); None when there are none."""
        key = (float(x), float(y))
        if key in self._heads:
            return self._heads[key]
        return self._drop(*key)

    def t_at(self, x: float, y: float) -> float:
        """The fins field at (x, y): 0 at the deepest drop, 1 at the shallowest, clamped. 1.0 (the rim) where no fin
        is within 12 in, which happens only beyond the outline."""
        d = self.drop_at(x, y)
        if d is None:
            return 1.0
        return min(1.0, max(0.0, (self.deep_in - d) / (self.deep_in - self.shallow_in)))

    def payload(self) -> list[dict[str, float]]:
        """Every fin as {x, y, angle_deg, drop_in} (floats) in file order; never the file's free text."""
        return [
            {"x": float(f.x), "y": float(f.y), "angle_deg": float(f.angle_deg), "drop_in": float(f.drop_in)}
            for f in self.fins
        ]

    def __repr__(self) -> str:
        return f"FinMap({len(self.fins)} fins, sha256 {self.digest})"

    __str__ = __repr__

    def _identity(self) -> tuple[str, float, float]:
        return (self.digest, self.apron_in, self.fin_width_in)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FinMap):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash(self._identity())

    def __copy__(self) -> FinMap:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> FinMap:
        return self


def build_fin_map(
    data: FinsFile,
    *,
    digest: str,
    apron_in: float,
    fin_width_in: float,
    heads: Sequence[tuple[float, float]],
) -> FinMap:
    """The immutable map of a checked file, with the drop precomputed at every head."""
    drops = [k.drop_in for k in data.drop_key]
    fin_map = FinMap(
        fins=tuple(FinPoint(float(f.x), float(f.y), float(f.angle_deg), float(f.drop_in)) for f in data.fins),
        apron_in=float(apron_in),
        fin_width_in=float(fin_width_in),
        deep_in=float(max(drops)),
        shallow_in=float(min(drops)),
        digest=digest,
    )
    table = {(float(x), float(y)): fin_map._drop(float(x), float(y)) for x, y in heads}
    object.__setattr__(fin_map, "_heads", MappingProxyType(table))
    return fin_map


# ---- checks --------------------------------------------------------------------------------------------------------
def head_problems(fin_map: FinMap, heads: Sequence[tuple[float, float]]) -> list[str]:
    """A light with no fin within 12 in has no fins-field value: one sentence for all of them, or []."""
    bare = [(x, y) for x, y in heads if fin_map.drop_at(x, y) is None]
    if not bare:
        return []
    x, y = bare[0]
    return [
        (
            f"{len(bare)} light(s) have no fin within {FIN_CUT_IN:g} in, e.g. ({x:g}, {y:g}): "
            "the fins field has no value there"
        )
    ]


def _file_problems(data: FinsFile, section: FinsSection, width_in: float, height_in: float) -> list[str]:
    """Rules 1–11: the file against the drawing and canopy.yaml (everything but the lights' coverage)."""
    out: list[str] = []
    n = len(data.fins)
    if n != data.count:
        out.append(f"count says {data.count} fins but fins lists {n}")
    if n != FINS_EXPECTED:
        out.append(f"fins lists {n} fins but drawing A-20-1 has {FINS_EXPECTED}")
    keyed = sum(k.count for k in data.drop_key)
    if keyed != data.count:
        out.append(f"the drop key adds up to {keyed} fins but count says {data.count}")
    in_key: Counter[float] = Counter()
    listed: Counter[float] = Counter()
    for k in data.drop_key:
        in_key[k.drop_in] += k.count
        listed[k.drop_in] += 1
    out += [f"the drop key lists {d:g} in more than once" for d, times in listed.items() if times > 1]
    if len(listed) < 2:
        out.append(
            f"the drop key needs at least two different drops (it has only {next(iter(listed)):g} in): "
            "the fins field has no depth to read"
        )
    seen = Counter(f.drop_in for f in data.fins)
    for drop in sorted(set(in_key) | set(seen)):
        if in_key[drop] != seen[drop]:
            out.append(f"the drop key has {in_key[drop]} fins of {drop:g} in but fins lists {seen[drop]}")
    a, w, h = float(section.apron_in), float(width_in), float(height_in)
    if data.apron_in != a:
        out.append(f"apron_in is {data.apron_in:g} but canopy.yaml → canopy → fins → apron_in is {a:g}")
    if data.fin_plan_width_in != section.fin_width_in:
        out.append(
            f"fin_plan_width_in is {data.fin_plan_width_in:g} but canopy.yaml → canopy → fins → fin_width_in "
            f"is {section.fin_width_in:g}"
        )
    (x0, x1), (y0, y1) = data.outer_in.x, data.outer_in.y
    if (x0, x1, y0, y1) != (-a, w + a, -a, h + a):
        out.append(
            f"outer_in is x [{x0:g}, {x1:g}], y [{y0:g}, {y1:g}] but the canopy plus its {a:g} in apron is "
            f"x [{-a:g}, {w + a:g}], y [{-a:g}, {h + a:g}]"
        )
    first_at: dict[tuple[float, float], int] = {}
    for i, f in enumerate(data.fins):
        if not (-a <= f.x <= w + a and -a <= f.y <= h + a):
            out.append(f"fins → [{i}] at ({f.x:g}, {f.y:g}) hangs outside the canopy and its {a:g} in apron")
        if (f.x, f.y) in first_at:
            out.append(
                f"fins → [{i}] at ({f.x:g}, {f.y:g}) has the same centre as fins → [{first_at[f.x, f.y]}]"
            )
        else:
            first_at[f.x, f.y] = i
    return out


def _checked(
    data: FinsFile,
    section: FinsSection,
    width_in: float,
    height_in: float,
    heads: Sequence[tuple[float, float]],
    digest: str,
) -> tuple[list[str], FinMap | None]:
    """The problems and, when the file rules are all clear, the map they were measured on."""
    problems = _file_problems(data, section, width_in, height_in)
    if problems:
        return problems, None
    fin_map = build_fin_map(
        data, digest=digest, apron_in=section.apron_in, fin_width_in=section.fin_width_in, heads=heads
    )
    return head_problems(fin_map, heads), fin_map


def fin_problems(
    data: FinsFile,
    section: FinsSection,
    width_in: float,
    height_in: float,
    heads: Sequence[tuple[float, float]],
) -> list[str]:
    """Everything wrong with a schema-valid fins file against canopy.yaml, as plain sentences (empty = fine). Pure.
    The lights' coverage (head_problems) is checked only when every file rule is clear: it needs a map to measure."""
    return _checked(data, section, width_in, height_in, heads, digest="")[0]


# ---- loading -------------------------------------------------------------------------------------------------------
def _where(loc: Iterable[Any]) -> str:
    return " → ".join(f"[{p}]" if isinstance(p, int) else str(p) for p in loc) or "(top level)"


def _validation(err: ValidationError) -> list[str]:
    return [f"{_where(e['loc'])}: {e['msg'].removeprefix('Value error, ')}" for e in err.errors()]


def _file_error(path: str, problems: list[str]) -> ConfigError:
    lines = [f"{path} has {len(problems)} problem(s):", *(f"  • {p}" for p in problems[:MAX_LISTED])]
    if len(problems) > MAX_LISTED:
        lines.append(f"  … and {len(problems) - MAX_LISTED} more")
    lines.append(_RESTORE)
    return ConfigError("\n".join(lines))


@lru_cache(maxsize=8)
def _load(
    path: str,
    mtime_ns: int,
    size: int,
    section: FinsSection,
    width_in: float,
    height_in: float,
    heads: tuple[tuple[float, float], ...],
) -> FinMap:
    """One version of the file, as a map. mtime_ns and size are only cache key: they notice an edit."""
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        raise ConfigError(f"{path} can't be read ({e.strerror or e}). {_RESTORE}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(
            f"{path} is not valid JSON (line {e.lineno}, column {e.colno}). Restore it from git."
        ) from None
    except UnicodeDecodeError:
        raise ConfigError(f"{path} is not valid JSON (it isn't UTF-8 text). Restore it from git.") from None
    try:
        parsed = FinsFile.model_validate(data)
    except ValidationError as e:
        raise _file_error(path, _validation(e)) from None
    problems, fin_map = _checked(
        parsed, section, width_in, height_in, heads, digest=hashlib.sha256(raw).hexdigest()[:12]
    )
    if problems or fin_map is None:
        raise _file_error(path, problems)
    return fin_map


def load_fin_map(
    canopy_path: Path,
    section: Any,
    width_in: float,
    height_in: float,
    heads: Iterable[tuple[float, float]],
) -> tuple[dict, FinMap]:
    """canopy.yaml's checked `fins` section (a plain dict) and the map of the file it names next to canopy.yaml.
    ConfigError, naming the file, the entry and the fix, for any problem. Cached: an unchanged file is read once."""
    try:
        sec = FinsSection.model_validate(section)
    except ValidationError as e:
        problems = _validation(e)
        raise ConfigError(
            "\n".join(
                [
                    f"{canopy_path} → canopy → fins has {len(problems)} problem(s):",
                    *(f"  • {p}" for p in problems),
                    "Fix the file and restart.",
                ]
            )
        ) from None
    path = Path(canopy_path).resolve().parent / sec.file
    try:
        st = os.stat(path)
    except FileNotFoundError:
        raise ConfigError(
            f"{path} is missing: {canopy_path} → canopy → fins → file names it. Restore it from git "
            "(VWBD drawing A-20-1, extracted), or remove the fins section."
        ) from None
    points = tuple((float(x), float(y)) for x, y in heads)
    fin_map = _load(str(path), st.st_mtime_ns, st.st_size, sec, float(width_in), float(height_in), points)
    return sec.model_dump(), fin_map


def parse_vwbd_panels(canopy_path: Path, raw: Any, positions: list[str]) -> dict[str, str]:
    """canopy.yaml → canopy → vwbd_panels: VWBD's panel number (P-01 … P-09) for every canopy position, each number
    given once, returned in position order. ConfigError listing every problem."""
    span = f"{positions[0]} … {positions[-1]}" if positions else "r0c0 … r2c2"
    if not isinstance(raw, Mapping):
        raise ConfigError(
            f"{canopy_path} → canopy → vwbd_panels must map each canopy position ({span}) to VWBD's panel number "
            "(P-01 … P-09)."
        )
    problems: list[str] = []
    if missing := [p for p in positions if p not in raw]:
        problems.append(f"no panel for {', '.join(missing)}")
    problems += [f"{k} isn't a canopy position; use {span}" for k in raw if k not in positions]
    ours = {pos: value for pos, value in raw.items() if pos in positions}
    good = {pos: value for pos, value in ours.items() if isinstance(value, str) and _PANEL.match(value)}
    if bad := [f"{pos}: {value!r}" for pos, value in ours.items() if pos not in good]:
        problems.append(f"panel numbers must read P-01 … P-09 ({'; '.join(bad)})")
    problems += [
        f"{panel} is given to more than one position"
        for panel, n in sorted(Counter(good.values()).items())
        if n > 1
    ]
    if problems:
        raise ConfigError(
            "\n".join(
                [
                    f"{canopy_path} → canopy → vwbd_panels has {len(problems)} problem(s):",
                    *(f"  • {p}" for p in problems),
                    "Fix the file and restart.",
                ]
            )
        )
    return {pos: str(raw[pos]) for pos in positions}
