"""The 18-scene review catalog (D76): an in-memory fixture for the iPad review menu's tests, never shipped.

Each of the six approved scenes keeps its place and gains two options, `<id>-2` and `<id>-3` ("<name> #2", "#3",
`option_of: <id>`), in catalog order: base, #2, #3. An option's mode is its base's with the design re-placed (#2: every
field canvas's `invert` flipped; #3: its `transform` toggled linear ↔ mirror) and no canvas or slot rung, the
module-level shape PR D ships (plan README:120: options never own a slot; ladders [stepped, modules, strand] or
[modules, strand]). The base's stepped rung is kept where it has one.

What it is and isn't:
- It passes the Scene and Mode schemas (`review_scenes` validates the catalog, so `option_of`'s checks run) but not
  the scene↔mode rules: the options share their base's recipes and copy. So it is installed with
  `looks.live.install_looks`, which doesn't validate, and only the panel, /ops, the kiosk and the export tests read it.
- The options' feature, description and mood copy is the base's, so it isn't true for the re-placed layout. It is
  fixture data only, as are any screenshots taken over it (07 §9).

Once git carries its own options (D77: the 18-option catalog, every `<id>-2`/`<id>-3` with `option_of`), the git
catalog is the review catalog: `review_scenes` and `review_trio` return it unchanged (integration C13), so the tests
over this fixture run over the real 18 and never build `<id>-2-2` or chain `option_of`.

Imports are `conductor.*` only, never a tests module: tests_e2e imports this file through pytest's
`pythonpath = ["."]` (`from tests.review_fixture import …`), and tests/conftest.py — which puts tests/ on sys.path —
is never loaded by `make e2e`.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from conductor.looks.live import ConfigTrio, git_trio, install_looks
from conductor.looks.schema import Mode
from conductor.scenes import SceneCatalog

APPROVED = [
    "lowcountry-calm",
    "rainbow-row-sunset",
    "garden-gathering",
    "coastal-tides",
    "golden-hour",
    "goodnight-glow",
]
# Oct 21: the seventh scene, after the six approved ones, with its own five per-light options (#2-#6)
LIGHT_SHOW = "light-show"
BASES = [*APPROVED, LIGHT_SHOW]
OPTIONS = (2, 3, 4, 5, 6)  # D77's #2/#3 and D80's per-light #4–#6 (git's own catalog)
REVIEW_IDS = [x for s in BASES for x in (s, *(f"{s}-{n}" for n in OPTIONS))]
OPTION_IDS = [x for x in REVIEW_IDS if x not in BASES]
# hide these and the six approved #1s are left: every option, and the Light Show scene with its options
BEYOND_SIX = [x for x in REVIEW_IDS if x not in APPROVED]
PER_LIGHT_RUNGS = ("canvas", "slot")


def option_id(base_id: str, n: int) -> str:
    return f"{base_id}-{n}"


def has_options(catalog: SceneCatalog) -> bool:
    """True when the catalog already groups options under their bases (git's own since D77)."""
    return any(scene.option_of for scene in catalog.scenes)


def review_scenes(base: SceneCatalog) -> SceneCatalog:
    """`base` with two options after each scene: `<id>-n`, "<name> #n", `option_of` = the base's id. Validated, so
    the catalog's option_of checks run. A catalog that already has its options (git's, D77) is returned as it is."""
    if has_options(base):
        return base
    scenes: list[dict[str, Any]] = []
    for scene in base.scenes:
        dump = scene.model_dump(mode="json")
        scenes.append(dump)
        for n in OPTIONS:
            scenes.append(
                {**dump, "id": option_id(scene.id, n), "name": f"{scene.name} #{n}", "option_of": scene.id}
            )
    return SceneCatalog.model_validate({"scenes": scenes})


def _replace_fields(node: Any, n: int) -> None:
    """Re-place every `kind: field` canvas's design in place: #2 flips `invert`, #3 toggles linear ↔ mirror (never on
    a fins design, which refuses mirror, D74: it keeps its design)."""
    if isinstance(node, dict):
        if node.get("kind") == "field" and isinstance(node.get("field"), dict):
            spec = node["field"]
            if n == 2:
                spec["invert"] = not spec.get("invert", False)
            elif spec.get("kind") == "fins":
                pass
            else:
                spec["transform"] = "mirror" if spec.get("transform", "linear") == "linear" else "linear"
        for value in node.values():
            _replace_fields(value, n)
    elif isinstance(node, list):
        for value in node:
            _replace_fields(value, n)


def _varied(mode: Mode, n: int, name: str) -> Mode:
    """The base's mode for option #n: its designs re-placed, no canvas or slot rung (and no slot), labelled `name`."""
    d = mode.model_dump(mode="json", exclude_unset=True)
    _replace_fields(d, n)
    d["ladder"] = [t for t in d["ladder"] if t not in PER_LIGHT_RUNGS]
    for rung in PER_LIGHT_RUNGS:
        d["techniques"].pop(rung, None)
    d.pop("slot", None)
    d["label"] = name
    return Mode.model_validate(d)


def review_trio(git: ConfigTrio) -> ConfigTrio:
    """`git` with the 18 review scenes and their modes (REVIEW_IDS order); the recipes are git's. A trio whose
    catalog already has its options (git's, D77) is returned as it is."""
    if has_options(git.scenes):
        return git
    scenes = review_scenes(git.scenes)
    modes: dict[str, Mode] = {}
    for base in git.scenes.scenes:
        base_mode = git.modes.modes[base.id]
        modes[base.id] = base_mode
        for n in OPTIONS:
            oid = option_id(base.id, n)
            modes[oid] = _varied(base_mode, n, scenes.get(oid).name)
    return ConfigTrio(
        scenes=scenes,
        modes=git.modes.model_copy(update={"modes": modes}),
        recipes=git.recipes,
    )


def install_review(services: Any, *, hide: Iterable[str] | None = None) -> ConfigTrio:
    """Install the review trio as the live looks (`install_looks`: no validation; any engine at extras['canopy']
    gets `invalidate_t1_plans()`). `hide` sets `kiosk.hidden_scenes` in place; None keeps the current list, so a list
    loaded from the settings overlay survives."""
    trio = review_trio(git_trio(Path(services.config_dir)))
    install_looks(services, trio, why="the 18-scene review fixture")
    if hide is not None:
        services.settings.kiosk.hidden_scenes = list(hide)
    return trio
