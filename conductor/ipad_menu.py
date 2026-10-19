"""The iPad's scene menu (D76): which scenes the guest panel offers, the /ops switch that takes one off, its "Needs
attention" lines, the conductor.yaml fold for the overlay download, and the review cards' thumbnails.

- **The menu** (`menu`, `live_menu`). The scenes the iPad offers are the live catalog (the show's live looks, D68) in
  catalog order, less `kiosk.hidden_scenes`. That list is git's (config/conductor.yaml) or, once /ops has switched a
  scene, `<data>/overrides/settings.yaml`'s (config.py's settings overlay). The idle scene (`kiosk.idle_mode`, and
  `kiosk.idle_scene` when set) is always offered: a listed idle id hides nothing, and neither does an id that isn't a
  scene now. Both are named on /ops (`problem_lines`) and dropped by the next switch. A hidden scene stays in the
  catalog: ops can still choose it, the Design Lab still edits it, and the iPad still names it while it is showing.
  More than `GRID_MAX` visible scenes is the review menu (one row per scene group, its options side by side).
- **The switch** (`set_on_ipad`, behind POST /api/ops/ipad-scenes). Synchronous on the loop thread (no await, no
  thread), so two switches never interleave. The overlay is saved BEFORE the in-place change, so a save that fails
  hides nothing. `services.settings` is changed in place and never rebound (the engine keeps the object).
- **The fold** (`conductor_yaml_diff`): the list in force as a `git apply` hunk for config/conductor.yaml's one-line
  `hidden_scenes: [...]`, appended to the /ops overlay download (07 §8: "downloadable as a diff to commit back").
- **Card thumbnails** (`design_thumb`, `catalog_thumbs`, `plan_thumb`, `mask_thumb`): a still picture of each scene
  for the review menu's cards, on the basis the caller chose: `"heads"` (the design at every light, for a scene the
  engine would show per light) or `"modules"` (its nine module colours), both from `routes.design.mode_design`; or
  `"strand"` (a whole-strand plan: the colour it puts on each panel it drives, from the plan itself through
  `canopy_view.plan_picture`, the claim Now Showing makes). The caller (guest_panel.thumb_plans) picks the basis
  from the tier the engine would use now, because a picture is copy too, and masks each card to the positions that
  plan drives (`mask_thumb`): on a partial canopy a card lights only the panels Now Showing lights (D62), never all
  nine.

Imports: conductor.config and conductor.scenes only at module level; looks.live, routes.design and canopy_view
inside functions.
Never guest_panel or routes.kiosk: routes.kiosk imports this module, and guest_panel imports routes.kiosk.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from conductor.config import (
    SCENE_ID,
    SETTINGS_OVERLAY_KEY,
    SETTINGS_OVERLAY_PATH,
    ConductorSettings,
    ConfigError,
    SettingsOverlayState,
    idle_keys,
    load_settings_overlay,
    save_settings_overlay,
)
from conductor.scenes import SceneCatalog

log = logging.getLogger(__name__)

GRID_MAX = (
    6  # more visible scenes than this: the review menu (web/kiosk/panel-model.mjs and web/ops/ipad-model.mjs)
)
IDLE_ALWAYS = "{name} is the idle scene ({key}), so the iPad always offers it."
CONDUCTOR_YAML = "config/conductor.yaml"
HIDDEN_LINE = re.compile(r"^(?P<i>[ ]+)hidden_scenes:[ ]*\[[^\]]*\][ ]*$")
Basis = Literal["heads", "modules", "strand"]
_THUMB_LOGGED: set[tuple[str, str]] = set()
_THUMB_LOGGED_MAX = 256


# ---- the menu ---------------------------------------------------------------------------------------------------------
def idle_ids(kiosk: Any) -> dict[str, str]:
    """The idle scene id(s) → the setting that names them ("kiosk.idle_mode" / "kiosk.idle_scene")."""
    return idle_keys(kiosk)


@dataclass(frozen=True)
class Menu:
    catalog: SceneCatalog  # the whole live catalog
    visible: SceneCatalog  # what the iPad offers (built with model_construct: a visible option of a hidden base is fine)
    hidden: tuple[str, ...]  # catalog order
    ignored_unknown: tuple[str, ...]  # listed ids that aren't scenes now (they hide nothing)
    ignored_idle: tuple[str, ...]  # listed ids that are the idle scene (always offered)
    trio: Any = None  # the ConfigTrio the catalog came from (live_menu), or None
    generation: int = 0  # the live looks' install generation the trio was read at

    @property
    def review(self) -> bool:
        """More than GRID_MAX visible: the iPad shows the review menu."""
        return len(self.visible.scenes) > GRID_MAX

    @property
    def ignored(self) -> tuple[str, ...]:
        return self.ignored_unknown + self.ignored_idle


def menu(catalog: SceneCatalog, kiosk: Any, *, trio: Any = None, generation: int = 0) -> Menu:
    """The iPad's menu over `catalog` with `kiosk.hidden_scenes` in force (pure)."""
    idle = idle_ids(kiosk)
    requested = list(dict.fromkeys(getattr(kiosk, "hidden_scenes", None) or ()))
    known = {s.id for s in catalog.scenes}
    asked = set(requested)
    hidden = tuple(s.id for s in catalog.scenes if s.id in asked and s.id not in idle)
    gone = set(hidden)
    visible = SceneCatalog.model_construct(scenes=[s for s in catalog.scenes if s.id not in gone])
    return Menu(
        catalog=catalog,
        visible=visible,
        hidden=hidden,
        ignored_unknown=tuple(x for x in requested if x not in known),
        ignored_idle=tuple(x for x in requested if x in known and x in idle),
        trio=trio,
        generation=generation,
    )


def live_menu(services: Any) -> Menu:
    """The menu over the show's live looks (approved or published) and the settings in force now."""
    from conductor.looks.live import live_looks

    looks = live_looks(services)
    generation = looks.generation  # read before the trio: a racing install is seen as stale, never as current
    trio = looks.trio
    return menu(trio.scenes, services.settings.kiosk, trio=trio, generation=generation)


def settings_overlay_of(services: Any) -> SettingsOverlayState:
    """The settings overlay's state (build_services keeps it in extras). A Services built another way gets it loaded
    (and kept) here; a bare stand-in without extras gets one with no data directory (saving refused)."""
    extras = getattr(services, "extras", None)
    if extras is None:
        return SettingsOverlayState(None)
    state = extras.get(SETTINGS_OVERLAY_KEY)
    if state is None:
        state = load_settings_overlay(getattr(services, "data", None))
        extras[SETTINGS_OVERLAY_KEY] = state
    return state


def _quoted(ids: Sequence[str]) -> str:
    return ", ".join(repr(x) for x in ids)


def problem_lines(services: Any) -> list[str]:
    """The /ops "Needs attention" lines about the iPad's menu, computed live (a fixed problem leaves /ops at once)."""
    lines: list[str] = []
    state = settings_overlay_of(services)
    if state.problem:
        lines.append(
            f"The iPad's scene list ({SETTINGS_OVERLAY_PATH}) couldn't be used ({state.problem}), so the iPad offers "
            "every scene config/conductor.yaml doesn't hide. Flip a “Show on the iPad” switch on /ops to write it "
            "again."
        )
    try:
        m = live_menu(services)
    except Exception:  # /ops must render; the looks have their own lines
        log.exception("iPad menu: the live menu couldn't be read for /ops")
        return lines
    if len(m.ignored_unknown) == 1:
        lines.append(
            f"kiosk.hidden_scenes names {_quoted(m.ignored_unknown)}, which isn't a scene now, so it hides nothing; "
            "the next “Show on the iPad” switch on /ops drops it."
        )
    elif m.ignored_unknown:
        lines.append(
            f"kiosk.hidden_scenes names {_quoted(m.ignored_unknown)}, which aren't scenes now, so they hide nothing; "
            "the next “Show on the iPad” switch on /ops drops them."
        )
    idle = idle_ids(services.settings.kiosk)
    for sid in m.ignored_idle:
        lines.append(
            f"kiosk.hidden_scenes names {sid!r}, the idle scene ({idle[sid]}): the iPad offers it anyway."
        )
    return lines


def menu_json(services: Any) -> dict:
    """GET /api/ops/ipad-scenes: every scene with its switch, the counts, and the notes."""
    m = live_menu(services)
    idle = idle_ids(services.settings.kiosk)
    hidden = set(m.hidden)
    return {
        "scenes": [
            {
                "id": s.id,
                "name": s.name,
                "group": s.group,
                "option_of": s.option_of,
                "on_ipad": s.id not in hidden,
                "idle": s.id in idle,
            }
            for s in m.catalog.scenes
        ],
        "visible": len(m.visible.scenes),
        "total": len(m.catalog.scenes),
        "grid_max": GRID_MAX,
        "review": m.review,
        "notes": problem_lines(services),
        "file": SETTINGS_OVERLAY_PATH,
        "writable": settings_overlay_of(services).path is not None,
    }


# ---- the /ops switch ----------------------------------------------------------------------------------------------------
class MenuRefused(Exception):
    """A switch the menu can't make: `status` is the HTTP status, `message` the words for /ops."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class MenuChange:
    changed: bool
    message: str


def _showing(services: Any, scene_id: str) -> bool:
    engine = (getattr(services, "extras", None) or {}).get("canopy")
    if engine is None:
        return False
    try:
        return engine.state().mode == scene_id
    except Exception:  # noqa: BLE001 - the message only; never a failed switch
        return False


def set_on_ipad(services: Any, scene_id: str, show: bool) -> MenuChange:
    """Show or hide one scene on the iPad. Synchronous (no await): the save comes before the in-place change, so a
    failed save changes nothing. MenuRefused 404 (no such scene), 409 (the idle scene, or no data directory), 503 (the
    file couldn't be written)."""
    m = live_menu(services)
    try:
        scene = m.catalog.get(scene_id)
    except KeyError:
        raise MenuRefused(404, f"There is no scene called {scene_id!r}.") from None
    idle = idle_ids(services.settings.kiosk)
    if not show and scene_id in idle:
        raise MenuRefused(409, IDLE_ALWAYS.format(name=scene.name, key=idle[scene_id]))
    if (scene_id not in m.hidden) == show:
        return MenuChange(False, f"{scene.name} is already {'on' if show else 'off'} the iPad.")
    # The new list in catalog order; this also drops the ids that hide nothing (unknown or idle).
    new = [
        s.id
        for s in m.catalog.scenes
        if (s.id in m.hidden or s.id == scene_id) and not (show and s.id == scene_id)
    ]
    try:
        save_settings_overlay(settings_overlay_of(services), new, now=services.clock.now())
    except ConfigError as exc:
        raise MenuRefused(409, str(exc)) from None
    except OSError as exc:
        reason = exc.strerror or str(exc) or type(exc).__name__
        raise MenuRefused(
            503, f"The iPad's scene list couldn't be saved ({reason}); nothing changed."
        ) from None
    services.settings.kiosk.hidden_scenes = new  # in place: services.settings is never rebound
    log.info(
        "iPad menu: %s %s (hidden now: %s)", "showed" if show else "hid", scene_id, ", ".join(new) or "none"
    )
    services.telemetry.record("ops", action="ipad_scenes", scene=scene_id, show=show, hidden=list(new))
    if show:
        message = f"{scene.name} is back on the iPad."
    else:
        message = (
            f"{scene.name} is off the iPad: guests no longer see its card. Ops can still choose it here and the "
            "Design Lab still edits it."
        )
        if _showing(services, scene_id):
            # Power-off, ops, the idle return and a Lab try-on can all end it: never predict when.
            message += " It is still showing; guests can't choose it again from the iPad."
    after = menu(m.catalog, services.settings.kiosk)
    if after.review and not m.review:
        message += " The iPad now shows the review menu (more than six scenes)."
    elif m.review and not after.review:
        message += " The iPad is back to its 3 × 2 grid."
    return MenuChange(True, message)


# ---- card thumbnails ------------------------------------------------------------------------------------------------------
def _hex(rgb: Sequence[int]) -> str:
    r, g, b = (int(v) for v in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def design_thumb(design: Mapping[str, Any], basis: Basis | None) -> dict | None:
    """A review card's picture from `mode_design`'s answer: `{sig, basis, modules: [{pos, color, colors}] × 9}`.

    - basis "heads": each module's 30 head colours (`colors`), or one `color` when all 30 are equal;
    - basis "modules": each module's one module-level colour (`color`), `colors` None;
    - None when the basis is None, "strand" (a whole-strand card comes from its plan: `plan_thumb`) or the design has
      no such data.
    `sig` is sha256[:12] of the compact JSON of basis and modules: the kiosk redraws a card only when it changes."""
    items: list[dict[str, Any]]
    if basis == "heads" and design.get("heads"):
        by_module: dict[str, list[str]] = {}
        for head in design["heads"]:
            by_module.setdefault(str(head["module"]), []).append(_hex(head["rgb"]))
        items = []
        for pos, hexes in by_module.items():
            same = all(h == hexes[0] for h in hexes)
            items.append({"pos": pos, "color": hexes[0] if same else None, "colors": None if same else hexes})
    elif basis == "modules" and design.get("modules"):
        items = [
            {"pos": str(m["module"]), "color": _hex(m["rgb"]), "colors": None} for m in design["modules"]
        ]
    else:
        return None
    return _signed(basis, items)


def _signed(basis: str, items: list[dict[str, Any]]) -> dict:
    compact = json.dumps({"basis": basis, "modules": items}, separators=(",", ":"))
    return {"sig": hashlib.sha256(compact.encode()).hexdigest()[:12], "basis": basis, "modules": items}


def mask_thumb(thumb: Mapping[str, Any] | None, driven: Collection[str]) -> dict | None:
    """`thumb` with every module whose canopy position isn't in `driven` drawn dark (`color` and `colors` None), as
    Now Showing (`state.lit`) leaves a position the engine doesn't drive: a position with no device registered is
    left out of every look (D62), so a card on a partial canopy lights only the panels the ceiling lights. The `sig`
    follows the mask. Every module driven: `thumb` as it is (a shallow copy, the same sig); None stays None."""
    if thumb is None:
        return None
    modules = thumb["modules"]
    if all(m["pos"] in driven for m in modules):
        return dict(thumb)
    items = [m if m["pos"] in driven else {"pos": m["pos"], "color": None, "colors": None} for m in modules]
    return _signed(thumb["basis"], items)


def plan_thumb(scene_id: str, canopy: Mapping[str, Any], registry: Any, plan: Any) -> dict | None:
    """A whole-strand card's picture (basis "strand"): what `plan` puts on each canopy position, the colour claim Now
    Showing makes for it (`canopy_view.plan_picture`: one flat colour per driven panel for a colour or white look,
    the panels it doesn't drive dark). None when the strand plays the look on its own (a preset or program) or its
    colours aren't known: no still picture is true then, so the card keeps its palette strip. Never raises."""
    from conductor import canopy_view

    try:
        picture = canopy_view.plan_picture(canopy, registry, plan)
    except Exception as exc:  # noqa: BLE001 - a card without a picture, never a failed panel
        _log_thumb_failure(scene_id, exc)
        return None
    return _signed("strand", picture) if picture is not None else None


def _log_thumb_failure(scene_id: str, exc: BaseException) -> None:
    reason = f"{type(exc).__name__}: {exc}"[:200]
    if (scene_id, reason) in _THUMB_LOGGED:
        return
    if len(_THUMB_LOGGED) >= _THUMB_LOGGED_MAX:
        _THUMB_LOGGED.clear()
    _THUMB_LOGGED.add((scene_id, reason))
    log.warning(
        "iPad card for %s can't draw its picture (%s); the card shows its palette strip instead",
        scene_id,
        reason,
    )


def catalog_thumbs(
    trio: Any, canopy: Mapping[str, Any], config_dir: Path, basis: Mapping[str, str | None]
) -> dict[str, dict | None]:
    """Each id in `basis` → its card thumbnail (`design_thumb`) on that basis, from `trio`'s modes and recipes over
    `canopy` (pass the panel geometry's own canopy, so the heads line up with its modules' heads). A scene that can't
    be drawn is None (logged once per reason), never a failed panel. It draws all nine positions, as the design
    does; the caller masks each card to the positions the engine drives (`mask_thumb`, D62)."""
    from conductor.looks import CompileContext
    from conductor.routes import design as design_route  # looked up per call: tests count mode_design

    ctx = CompileContext(
        registry=[],
        capabilities={},
        modes=trio.modes,
        recipes=trio.recipes,
        canopy=canopy,  # type: ignore[arg-type] - read only
        config_dir=Path(config_dir),
    )
    out: dict[str, dict | None] = {}
    for sid, b in basis.items():
        if b not in ("heads", "modules") or sid not in trio.modes.modes:
            out[sid] = None
            continue
        try:
            design = design_route.mode_design(sid, ctx, heads=(b == "heads"))
            out[sid] = design_thumb(design, b)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 - a card without a picture, never a failed panel
            _log_thumb_failure(sid, exc)
            out[sid] = None
    return out


# ---- the conductor.yaml fold ------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ConductorYamlDiff:
    diff: str  # a unified diff for config/conductor.yaml ("" when there is nothing to change, or a problem)
    after: str | None  # the file with the list folded in (None on a problem)
    problem: str | None  # why there is no diff


def conductor_yaml_diff(config_dir: Path, hidden: Sequence[str]) -> ConductorYamlDiff:
    """`hidden` folded into config/conductor.yaml's one-line `hidden_scenes: [...]`, as a `git apply`-able diff.
    The edited text is re-read: it must parse as the same file with kiosk.hidden_scenes == hidden, and validate."""
    path = Path(config_dir) / "conductor.yaml"
    hidden = list(hidden)
    try:
        before = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        reason = getattr(exc, "strerror", None) or type(exc).__name__
        return ConductorYamlDiff("", None, f"it can't be read ({reason})")
    bad = [x for x in hidden if not SCENE_ID.match(x)]
    if bad:
        return ConductorYamlDiff("", None, f"{_quoted(bad)} isn't a scene id")
    lines = before.splitlines(keepends=True)
    found = [i for i, line in enumerate(lines) if HIDDEN_LINE.match(line.rstrip("\r\n"))]
    if len(found) != 1:
        return ConductorYamlDiff(
            "", None, f"{CONDUCTOR_YAML} has no one-line `hidden_scenes: [...]` under kiosk; set it by hand"
        )
    i = found[0]
    old = lines[i].rstrip("\r\n")
    ending = lines[i][len(old) :]
    match = HIDDEN_LINE.match(old)
    assert match is not None
    new = f"{match['i']}hidden_scenes: [{', '.join(hidden)}]"
    if new == old:
        return ConductorYamlDiff("", before, None)
    if not before.endswith("\n"):
        return ConductorYamlDiff("", None, f"{CONDUCTOR_YAML} doesn't end with a newline; set it by hand")
    edited = [*lines[:i], new + ending, *lines[i + 1 :]]
    after = "".join(edited)
    try:
        expected = yaml.safe_load(before) or {}
        reread = yaml.safe_load(after) or {}
        expected["kiosk"]["hidden_scenes"] = hidden
        same = reread == expected and reread["kiosk"]["hidden_scenes"] == hidden
        ConductorSettings.model_validate(reread)
    except (yaml.YAMLError, KeyError, TypeError, ValidationError) as exc:
        return ConductorYamlDiff(
            "", None, f"the edited {CONDUCTOR_YAML} didn't validate ({type(exc).__name__})"
        )
    if not same:
        return ConductorYamlDiff("", None, f"the edited {CONDUCTOR_YAML} re-read differently; set it by hand")
    diff = "".join(difflib.unified_diff(lines, edited, f"a/{CONDUCTOR_YAML}", f"b/{CONDUCTOR_YAML}"))
    return ConductorYamlDiff(diff, after, None)
