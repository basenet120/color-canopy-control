"""The pre-D59 modes as a test fixture (tests/fixtures_modes_techniques.yaml): every compiler technique that no show
mode uses since the approved scenes replaced the old recipes (D59) — white CCT clamps and fallbacks, preset
candidates, pathway and band canvases, row / middle-row module layouts, motion copy — stays tested on it."""

from __future__ import annotations

from pathlib import Path

import yaml

from conductor.config import load_recipes
from conductor.geometry import load_canopy_config
from conductor.looks import parse_modes

PATH = Path(__file__).with_name("fixtures_modes_techniques.yaml")
TECHNIQUE_MODES = parse_modes(
    yaml.safe_load(PATH.read_text(encoding="utf-8")),
    recipes=load_recipes(),
    canopy=load_canopy_config(),
    path=PATH,
)
