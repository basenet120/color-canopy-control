"""The show's live looks (D68, docs/live-lab/PLAN.md §2): what the running show compiles from — the approved (git)
scenes/modes/recipes trio, or a trio the Design Lab published — and the one function that swaps it in.

    config/{scenes,modes,recipes}.yaml ──load──▶ git trio ─┐
    <data>/overrides/show-looks.yaml ──load + validate──────┴─▶ LiveLooks.trio ──install_looks──▶ every copy (§2.4)

- **The overlay** `overrides/show-looks.yaml` (07 §8's overlay pattern): `current` (the publication the show runs, its
  trio stored in full), `previous` (the one it replaced, trio included: Undo), `history` (metadata only, newest first),
  `seq` (publication numbers are never reissued). Written atomically at 0600 with a history copy of the file it
  replaces, on the loop thread, BEFORE the trio is installed (`write_looks`).
- **Loading never fails the start** (`load_live_looks`): a missing file is the approved looks; a file that is too big
  (never read), unreadable, not YAML, uses anchors or aliases, nests too deep, has a bad top level or format, a version
  with no trio, a trio whose `trio_sha` doesn't match ("edited by hand or cut short"), or a trio `validate_trio`
  refuses is the approved looks plus `problem` (an /ops line and a warn alert) — and the file is left byte for byte as
  it was. The whole overlay stage is wrapped in one broad `except` (a RecursionError or an alias bomb can't hang or
  stop the lifespan). Only a broken GIT config raises (as it always has).
- **A published trio equal to git's** (approve-into-git merged and deployed) is followed as git: `current` becomes
  `{kind: approved, name: "approved (in git)"}`, persisted with one small write, so a later deploy is never masked.
  One that differs from a git that changed since it was published raises an /ops warn (`git_changed`).
- **`validate_trio`** holds a published trio to everything git's is held to: the schemas, `parse_modes` (schema +
  cross_check: every kiosk sentence's honesty rules), the show's idle/stepped load checks, the blocking scene ↔ mode
  rules, and git's own ids, order, ladders, slots and motion flags (the Lab never changes them).
- **`install_looks`** is THE hot-apply checklist (§2.4): synchronous (no await, so no request sees half of it), deep
  copies, every process-lifetime copy refreshed.
- **The operator repaint's bookkeeping (B2)** lives in the same file, parsed leniently on its own: `repaints` (scene →
  wall time its last operator repaint started: the 2-min gate survives a restart, clamped to the interval) and
  `queued` (scene → wall time a queued repaint is due: re-queued after a restart). A publish writes the queue with
  its publication; the engine's hook (`note_repaint`) writes the gate when a scene starts and drops the scene from the
  queue (and fills its history row's `repaint`) when it ends — without a history copy (the publication didn't change).

This module imports conductor.looks / .scenes / .config only — never conductor.design_lab (pinned by a test).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import stat
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from conductor.config import ConfigError, RecipesFile, _explain, load_recipes
from conductor.looks.schema import ModesFile, load_modes, parse_modes
from conductor.scenes import SceneCatalog, load_scenes

log = logging.getLogger(__name__)

LOOKS_FILE = "show-looks.yaml"
LOOKS_PATH = f"overrides/{LOOKS_FILE}"
LOOKS_KEY = "looks"  # services.extras: the LiveLooks
LOCK_KEY = "looks:lock"  # services.extras: the publish lock (an asyncio.Lock, one event loop per app)
MAX_BYTES = 2_000_000
MAX_HISTORY = 200
MAX_DEPTH = 64
HISTORY_COPIES = 50  # overrides/history/show-looks-*.yaml kept
ALERT_PREFIX = "Published looks:"  # the AlertBoard's key rule (§2.12)
LOAD_KEY, GIT_KEY = "looks:load", "looks:git"
REASON_MAX = 160
CONFIG_FILES = ("scenes.yaml", "modes.yaml", "recipes.yaml")
IN_GIT_NAME = "approved (in git)"
APPROVED_NAME = "the approved looks"
_build_lock = threading.Lock()


# ---- the trio ---------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ConfigTrio:
    """scenes.yaml + modes.yaml + recipes.yaml, parsed (moved here from design_lab/draft.py, which re-exports it)."""

    scenes: SceneCatalog
    modes: ModesFile
    recipes: RecipesFile

    def dump(self) -> dict:
        return {
            "scenes": self.scenes.model_dump(mode="json"),
            "modes": self.modes.model_dump(mode="json"),
            "recipes": self.recipes.model_dump(mode="json"),
        }

    def copy(self) -> ConfigTrio:
        """Deep copies of all three (the live trio never shares an object with the Lab's or a loader's cache)."""
        return ConfigTrio(
            scenes=self.scenes.model_copy(deep=True),
            modes=self.modes.model_copy(deep=True),
            recipes=self.recipes.model_copy(deep=True),
        )


def config_sha(config_dir: Path) -> str:
    """sha256[:12] of config/{scenes,modes,recipes}.yaml's bytes, in that order (moved here from design_lab/store.py,
    which re-exports it): what a Lab version was saved against, and what a publication was published over."""
    h = hashlib.sha256()
    for name in CONFIG_FILES:
        h.update((Path(config_dir) / name).read_bytes())
    return h.hexdigest()[:12]


def _canonical(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(document: Any) -> str:
    return hashlib.sha256(_canonical(document).encode()).hexdigest()[:12]


def trio_sha(trio: ConfigTrio) -> str:
    """sha256[:12] of the trio's canonical JSON dump: what the overlay's `trio_sha` checks its stored trio against."""
    return _sha(trio.dump())


def git_trio(config_dir: Path) -> ConfigTrio:
    """The approved (git) trio through the show's own loaders, deep-copied (load_scenes is lru_cached and the models
    are mutable). ConfigError on a broken git config, as ever."""
    config_dir = Path(config_dir)
    return ConfigTrio(
        scenes=load_scenes(config_dir).model_copy(deep=True),
        modes=load_modes(config_dir).model_copy(deep=True),
        recipes=load_recipes(config_dir).model_copy(deep=True),
    )


# ---- the file -------------------------------------------------------------------------------------------------------
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Publication(_Strict):
    """`current` / `previous`: one publication. `trio` is the full dump the show loads (None for kind approved)."""

    id: str = Field(pattern=r"^p\d{4,}$")
    kind: Literal["version", "approved"]
    version: str | None = None
    name: str = Field(min_length=1, max_length=80)
    published_at: float
    by: str = Field("", max_length=80)
    base_config_sha: str = Field(pattern=r"^[0-9a-f]{12}$")
    trio_sha: str | None = None
    changed: list[str] = Field(default_factory=list)
    drafts: dict[str, Any] = Field(default_factory=dict)  # provenance only: the show never parses them
    trio: dict[str, Any] | None = None


class HistoryEntry(_Strict):
    id: str = Field(pattern=r"^p\d{4,}$")
    kind: Literal["version", "approved"]
    version: str | None = None
    name: str = Field(min_length=1, max_length=80)
    published_at: float
    by: str = Field("", max_length=80)
    changed: list[str] = Field(default_factory=list)
    repaint: dict[str, Any] | None = None
    outcome: str = Field("live", max_length=40)


class ShowLooksFile(_Strict):
    format: Literal[1] = 1
    seq: int = Field(0, ge=0)
    current: Publication | None = None
    previous: Publication | None = None
    history: list[HistoryEntry] = Field(default_factory=list, max_length=MAX_HISTORY)
    repaints: dict[str, float] = Field(default_factory=dict)  # B2
    queued: dict[str, float] = Field(default_factory=dict)  # B2


class _Unusable(Exception):
    """The overlay can't be used as it stands (the show runs the approved looks; the file stays as it is)."""


class _NoAliasLoader(yaml.SafeLoader):
    """SafeLoader that refuses every anchor and alias (no alias bombs; a hand-written anchor is refused too)."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
            raise _Unusable("it uses YAML anchors or aliases, which this file never does")
        return super().compose_node(parent, index)


class _NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def _short(text: str) -> str:
    line = (str(text).strip().splitlines() or [""])[0].strip()
    return line if len(line) <= REASON_MAX else line[: REASON_MAX - 1] + "…"


def _reason(exc: BaseException) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        where = ".".join(str(p) for p in first.get("loc", ())) or "the file"
        return _short(f"{where}: {first.get('msg', 'invalid')}")
    if isinstance(exc, ConfigError):
        lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
        bullets = [ln.lstrip("• ").strip() for ln in lines if ln.startswith("•")]
        return _short(bullets[0] if bullets else (lines[0] if lines else "invalid"))
    if isinstance(exc, RecursionError):
        return "it is nested too deeply to read"
    return _short(str(exc)) or type(exc).__name__


def _too_deep(data: Any, limit: int = MAX_DEPTH) -> bool:
    stack = [(data, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > limit:
            return True
        if isinstance(item, dict):
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list | tuple):
            stack.extend((v, depth + 1) for v in item)
    return False


def _finite(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def lenient_file(raw: Any) -> ShowLooksFile:
    """Whatever of a raw overlay can be kept, each part on its own: `seq`, `repaints`, `queued`, each history row,
    `current`, `previous` (a bad history row never resets `seq` — ids are never reissued — or the repaint gate)."""
    if not isinstance(raw, Mapping):
        return ShowLooksFile()
    seq = raw.get("seq")
    out: dict[str, Any] = {
        "seq": seq if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0 else 0
    }
    for key in ("repaints", "queued"):
        value = raw.get(key)
        if isinstance(value, Mapping):
            out[key] = {str(k): float(v) for k, v in value.items() if isinstance(k, str) and _finite(v)}
    history = []
    for row in raw.get("history") or [] if isinstance(raw.get("history"), list) else []:
        try:
            history.append(HistoryEntry.model_validate(row))
        except (ValidationError, ValueError, TypeError):
            continue
    out["history"] = history[:MAX_HISTORY]
    for key in ("current", "previous"):
        try:
            out[key] = Publication.model_validate(raw.get(key)) if raw.get(key) is not None else None
        except (ValidationError, ValueError, TypeError):
            out[key] = None
    numbers = [_number(p.id) for p in (out.get("current"), out.get("previous")) if p is not None]
    numbers += [_number(h.id) for h in history]
    out["seq"] = max([out["seq"], *numbers])
    return ShowLooksFile.model_validate(out)


def _number(pub_id: str) -> int:
    return int(pub_id[1:]) if pub_id[1:].isdigit() else 0


def _read_overlay(path: Path) -> tuple[Any, bytes]:
    """(raw YAML data, the file's bytes). _Unusable / OSError / RecursionError for every way it can't be used."""
    st = path.stat()  # follows a symlink: what a read would open
    if not stat.S_ISREG(st.st_mode):
        raise _Unusable("it isn't a regular file")
    if st.st_size > MAX_BYTES:  # never read it
        raise _Unusable(f"it is {st.st_size} bytes, over the {MAX_BYTES}-byte limit")
    with open(path, "rb") as f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise _Unusable(f"it is over the {MAX_BYTES}-byte limit")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Unusable(f"it isn't UTF-8 text ({exc.reason})") from None
    try:
        raw = yaml.load(text, Loader=_NoAliasLoader)
    except yaml.YAMLError as exc:
        raise _Unusable(f"it isn't valid YAML ({_short(str(exc))})") from None
    return raw, data


# ---- validation -------------------------------------------------------------------------------------------------------
def validate_trio(
    raw: Mapping[str, Any], *, git: ConfigTrio, settings: Any, canopy: dict | None, path: str = LOOKS_PATH
) -> ConfigTrio:
    """A published trio, held to everything git's is (see the module doc). ConfigError (one, every problem a
    bullet; the first failing step wins)."""
    from conductor.looks import scene_rules  # lazily: scene_rules imports the compiler
    from conductor.show import _check_idle_scene, _check_stepped  # the show's own load checks

    def refuse(problems: list[str]) -> ConfigError:
        lines = [f"{path} has {len(problems)} problem(s):", *(f"  • {p}" for p in problems)]
        return ConfigError("\n".join(lines))

    if not isinstance(raw, Mapping) or not all(
        isinstance(raw.get(k), Mapping) for k in ("scenes", "modes", "recipes")
    ):
        raise refuse(["the published trio needs scenes, modes and recipes mappings"])
    try:
        scenes = SceneCatalog.model_validate(raw["scenes"])
    except ValidationError as exc:
        raise ConfigError(_explain(f"{path} → scenes", exc)) from None
    try:
        recipes = RecipesFile.model_validate(raw["recipes"])
    except ValidationError as exc:
        raise ConfigError(_explain(f"{path} → recipes", exc)) from None
    modes = parse_modes(raw["modes"], recipes=recipes, canopy=canopy, path=f"{path} → modes")
    problems: list[str] = []
    if [s.id for s in scenes.scenes] != [s.id for s in git.scenes.scenes]:
        problems.append("its scene ids (or their order) differ from config/scenes.yaml's")
    if list(modes.modes) != list(git.modes.modes):
        problems.append("its mode ids (or their order) differ from config/modes.yaml's")
    for mode_id, mode in git.modes.modes.items():
        published = modes.modes.get(mode_id)
        if published is None:
            continue
        for what in ("ladder", "slot", "motion"):
            if getattr(published, what) != getattr(mode, what):
                problems.append(
                    f"mode {mode_id!r} → {what}: {getattr(published, what)!r} isn't config/modes.yaml's "
                    f"{getattr(mode, what)!r} (the Lab never changes it; a deploy that moved it makes this file "
                    "unusable)"
                )
    if problems:
        raise refuse(problems)
    _check_idle_scene(settings.kiosk.idle_scene, list(modes.modes))  # list(modes.modes), never list(modes)
    _check_stepped(settings, modes)
    blocking = scene_rules.scene_mode_problems(
        scenes, modes, recipes, canopy or {}, approved=git.modes, rules=scene_rules.BLOCKING
    )
    if blocking:
        raise refuse([p.message for p in blocking])
    return ConfigTrio(scenes=scenes, modes=modes, recipes=recipes)


# ---- the live looks -----------------------------------------------------------------------------------------------------
@dataclass
class LiveLooks:
    git: ConfigTrio  # the approved trio (deep copies of the show's own loaders)
    trio: ConfigTrio  # what the show compiles from NOW (git or published)
    source: Literal["approved", "published"]
    file: ShowLooksFile  # as loaded / last written (an empty one when missing or unusable)
    path: Path | None  # None: no data dir (publishing refused)
    problem: str | None = None  # why the file couldn't be used (the show runs approved)
    generation: int = 0  # +1 on every install_looks
    repaint: dict | None = None  # B2: the repaint job's progress
    equals_approved: bool = False  # a version whose trio equalled git's at load (now followed as approved)
    git_changed: bool = False  # current.base_config_sha != git's sha now (and the trio isn't git's)
    config_dir: Path | None = None
    git_sha: str = ""  # config_sha(config_dir) at load
    # the canopy the trios were validated against (the Lab reuses it: one git per process)
    canopy: dict | None = None

    @property
    def live_id(self) -> str:
        """What `expected_live` names: the current publication's id, or "approved" when there is none."""
        current = self.file.current
        return current.id if current is not None else "approved"

    @property
    def name(self) -> str:
        current = self.file.current
        return current.name if current is not None else APPROVED_NAME


def _empty(
    config_dir: Path, data: Path | None, git: ConfigTrio, sha: str, canopy: dict | None = None
) -> LiveLooks:
    return LiveLooks(
        git=git,
        trio=git,
        source="approved",
        file=ShowLooksFile(),
        path=(Path(data) / "overrides" / LOOKS_FILE) if data is not None else None,
        config_dir=Path(config_dir),
        git_sha=sha,
        canopy=canopy,
    )


def load_live_looks(
    config_dir: Path,
    data: Path | None,
    *,
    settings: Any,
    canopy: dict | None,
    now: float | None = None,
    telemetry: Any = None,
    read_only: bool = False,
) -> LiveLooks:
    """The live looks at start (§2.3). Raises ConfigError only for a broken git config; never for the overlay. The
    one write it may make: a published trio equal to git's is persisted as `current: {kind: approved}`.
    `read_only` (the preflight): the same parse, but no write and no log line."""
    config_dir = Path(config_dir)
    git = git_trio(config_dir)
    sha = config_sha(config_dir)
    looks = _empty(config_dir, data, git, sha, canopy)
    path = looks.path
    if path is None:
        return looks
    raw: Any = None
    try:
        try:
            path.stat()
        except FileNotFoundError:
            return looks
        raw, _data = _read_overlay(path)
        if _too_deep(raw):
            raise _Unusable(f"it is nested more than {MAX_DEPTH} levels deep")
        if not isinstance(raw, dict):
            raise _Unusable("it isn't a mapping of format, seq, current, previous and history")
        if "format" in raw and raw["format"] != 1:
            raise _Unusable(f"format {raw['format']!r} isn't one this Conductor reads (it reads format 1)")
        unknown = set(raw) - set(ShowLooksFile.model_fields)
        if unknown:
            raise _Unusable(
                f"it has keys this Conductor doesn't know ({', '.join(sorted(map(str, unknown)))})"
            )
        # seq, repaints, queued, previous and each history row on their own (a bad row is dropped, never fatal);
        # `current` — what the show runs — strictly
        looks.file = lenient_file(raw)
        if raw.get("current") is not None:
            try:
                Publication.model_validate(raw["current"])
            except ValidationError as exc:
                raise _Unusable(f"current: {_reason(exc)}") from None
        current = looks.file.current
        if current is None or current.kind == "approved":
            return looks
        if current.trio is None:
            raise _Unusable(f"the published version “{current.name}” has no trio stored")
        if current.trio_sha != _sha(current.trio):
            raise _Unusable("its trio doesn't match its trio_sha (edited by hand or cut short)")
        trio = validate_trio(current.trio, git=git, settings=settings, canopy=canopy)
        if trio_sha(trio) == trio_sha(git):
            _follow_git(looks, current, now=now, write=not read_only)
            return looks
        looks.trio = trio
        looks.source = "published"
        looks.git_changed = current.base_config_sha != sha
        return looks
    except Exception as exc:  # noqa: BLE001 - the overlay never stops the show: approved + an /ops line
        looks.trio, looks.source, looks.git_changed, looks.equals_approved = git, "approved", False, False
        looks.problem = _reason(exc)
        try:
            looks.file = lenient_file(raw)
        except Exception:  # noqa: BLE001
            looks.file = ShowLooksFile()
        if not read_only:
            log.warning(
                "Published looks: %s couldn't be used (%s); the show runs the approved looks",
                path,
                looks.problem,
            )
        return looks


def _follow_git(looks: LiveLooks, current: Publication, *, now: float | None, write: bool = True) -> None:
    """A published trio equal to git's: follow git (memory at once; the file with one small write, best effort;
    `write=False`: memory only — the preflight never writes)."""
    looks.equals_approved = True
    looks.source = "approved"
    looks.trio = looks.git
    when = float(now) if now is not None else current.published_at
    seq = looks.file.seq + 1
    in_git = Publication(
        id=f"p{seq:04d}",
        kind="approved",
        version=None,
        name=IN_GIT_NAME,
        published_at=when,
        by="git",
        base_config_sha=looks.git_sha,
        changed=[],
    )
    history = [
        HistoryEntry(
            id=in_git.id, kind="approved", name=IN_GIT_NAME, published_at=when, by="git", outcome="in git"
        ),
        *[
            h.model_copy(update={"outcome": "replaced"}) if h.id == current.id else h
            for h in looks.file.history
        ],
    ][:MAX_HISTORY]
    new_file = looks.file.model_copy(
        update={"seq": seq, "current": in_git, "previous": current, "history": history}
    )
    if not write:
        looks.file = new_file
        return
    try:
        write_looks(looks, new_file, now=when)
    except OSError as exc:
        log.warning(
            "Published looks: couldn't record that “%s” is now in git (%s); following git anyway",
            current.name,
            exc,
        )
        looks.file = new_file  # memory follows git either way


def write_looks(
    looks: LiveLooks, new_file: ShowLooksFile, *, now: float, history: bool = True
) -> bytes | None:
    """Validate, write a history copy of the file being replaced, then the new file (atomic, 0600). SYNC, on the loop
    thread (a small file). Returns the replaced file's bytes (None: there was none) for a rollback. OSError when it
    can't be written (memory untouched). `looks.file` changes only after the write. `history=False`: the operator
    repaint's bookkeeping (its gate, queue and history row) — the publication didn't change, so no copy is kept."""
    from conductor.tokens import atomic_write

    if looks.path is None:
        raise OSError("this runtime has no data folder")
    ShowLooksFile.model_validate(new_file.model_dump())
    document = {"format": 1, **new_file.model_dump(mode="json", exclude={"format"})}
    text = yaml.dump(document, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True).encode()
    if len(text) > MAX_BYTES:
        raise OSError(f"the published looks would be {len(text)} bytes, over the {MAX_BYTES}-byte limit")
    old: bytes | None = None
    try:
        old = looks.path.read_bytes() if looks.path.is_file() else None
    except OSError:
        old = None
    if old is not None and history:
        copies = looks.path.parent / "history"
        target = _free_name(copies, f"show-looks-{_utc_stamp(now)}")
        atomic_write(target, old, mode=0o600)
        _prune(copies)
    atomic_write(looks.path, text, mode=0o600)
    looks.file = new_file
    return old


# ---- the operator repaint's bookkeeping (D68 PR B2) -------------------------------------------------------------------
def _bookkeep(looks: LiveLooks, new_file: ShowLooksFile, *, now: float, what: str) -> None:
    """Write the repaint's bookkeeping (no history copy). A failure is logged; memory follows anyway (the gate and
    the queue are best effort across a restart, never a reason to stop a repaint)."""
    if looks.path is None:
        looks.file = new_file
        return
    try:
        write_looks(looks, new_file, now=now, history=False)
    except (OSError, ValidationError) as exc:
        log.warning("Published looks: couldn't save the repaint's %s (%s); kept in memory", what, exc)
        looks.file = new_file


def note_repaint(services: Any, event: str, info: Any) -> None:
    """The engine's repaint hook (CanopyEngine.deps.on_repaint): "progress" → `LiveLooks.repaint` (RepaintJSON, the
    Lab polls it); "started" → `repaints[mode]` = its wall time (the 2-min gate survives a restart); "finished" →
    the scene leaves `queued` and its publication's history row gets `repaint[mode] = {slot, applied, skipped,
    failed}`."""
    looks = (getattr(services, "extras", None) or {}).get(LOOKS_KEY)
    if looks is None:
        return
    if event == "progress":
        looks.repaint = info
        return
    if not isinstance(info, Mapping):
        return
    mode = str(info.get("mode") or "")
    if not mode:
        return
    clock = getattr(services, "clock", None)
    now = clock.now() if clock is not None else 0.0
    file = looks.file
    if event == "started":
        at = info.get("at")
        stamp = float(at) if _finite(at) else now
        _bookkeep(
            looks, file.model_copy(update={"repaints": {**file.repaints, mode: stamp}}), now=now, what="gate"
        )
        return
    if event != "finished" or info.get("aborted"):
        return
    queued = {k: v for k, v in file.queued.items() if k != mode}
    history = list(file.history)
    publication = info.get("publication")
    # a row when it wrote, or didn't start for a reason; never for "its slots already hold the live design"
    if info.get("started") or (info.get("reason") and not info.get("ok")):
        row = {
            "slot": str(info.get("slot") or ""),
            "applied": int(info.get("applied") or 0),
            "skipped": int(info.get("skipped") or 0),
            "failed": int(info.get("failed") or 0),
        }
        history = [
            h.model_copy(update={"repaint": {**(h.repaint or {}), mode: row}}) if h.id == publication else h
            for h in history
        ]
    _bookkeep(looks, file.model_copy(update={"queued": queued, "history": history}), now=now, what="queue")


def forget_queued(looks: LiveLooks, modes: list[str], *, now: float) -> None:
    """Drop scenes from `queued` that have nothing left to repaint (a restart found their slots current)."""
    file = looks.file
    queued = {k: v for k, v in file.queued.items() if k not in modes}
    if queued != file.queued:
        _bookkeep(looks, file.model_copy(update={"queued": queued}), now=now, what="queue")


def restore_file(looks: LiveLooks, old: bytes | None, file: ShowLooksFile) -> None:
    """A rollback: the replaced file back byte for byte (or gone again when there was none), and its parsed form."""
    from conductor.tokens import atomic_write

    if looks.path is None:
        return
    if old is None:
        try:
            looks.path.unlink()
        except FileNotFoundError:
            pass
    else:
        atomic_write(looks.path, old, mode=0o600)
    looks.file = file


def _free_name(directory: Path, stem: str, suffix: str = ".yaml") -> Path:
    candidate = directory / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def _utc_stamp(now: float) -> str:
    try:
        return datetime.fromtimestamp(now, UTC).strftime("%Y%m%dT%H%M%SZ")
    except (OverflowError, OSError, ValueError):
        return "unknown-time"


def _prune(history: Path) -> None:
    try:
        copies = sorted(p for p in history.glob("show-looks-*.yaml") if p.is_file())
    except OSError:
        return
    for old in copies[: max(0, len(copies) - HISTORY_COPIES)]:
        try:
            old.unlink()
        except OSError:
            log.warning("Published looks: couldn't prune the history copy %s", old)


# ---- on the Services container ------------------------------------------------------------------------------------------
def _canopy_of(config_dir: Path) -> dict:
    from conductor.geometry import load_canopy_config

    path = Path(config_dir) / "canopy.yaml"
    return load_canopy_config(path) if path.exists() else load_canopy_config()


def build_live_looks(services: Any) -> LiveLooks:
    """Load the live looks and keep them on `services.extras[LOOKS_KEY]`. Called ONCE, eagerly, in the lifespan right
    after build_services() (engine or not), on the loop thread. Never raises for the overlay (a broken git config
    raises ConfigError, as the show's own loaders do)."""
    clock = getattr(services, "clock", None)
    looks = load_live_looks(
        services.config_dir,
        getattr(services, "data", None),
        settings=services.settings,
        canopy=_canopy_of(services.config_dir),
        now=clock.now() if clock is not None else None,
    )
    services.extras[LOOKS_KEY] = looks
    _startup_alerts(services, looks)
    return looks


def standing_alerts(services: Any, looks: LiveLooks) -> None:
    """(Re-)raise the looks warns that stand now: the fallback (`problem`) and a published look masking a changed
    git. At start, and after a rollback (whose install_looks resolved them although the state they describe was put
    back)."""
    telemetry = getattr(services, "telemetry", None)
    if telemetry is None:
        return
    if looks.problem:
        telemetry.record("alert", level="warn", message=load_problem_line(looks), alert_key=LOAD_KEY)
    if looks.git_changed and looks.source == "published":
        telemetry.record("alert", level="warn", message=git_changed_line(looks), alert_key=GIT_KEY)


def _startup_alerts(services: Any, looks: LiveLooks) -> None:
    telemetry = getattr(services, "telemetry", None)
    if telemetry is None:
        return
    standing_alerts(services, looks)
    if looks.equals_approved:
        telemetry.record(
            "alert",
            level="info",
            message=f"{ALERT_PREFIX} the published looks now equal the approved (git) looks, so the show follows "
            "git again.",
            alert_key=LOAD_KEY,
        )


def live_looks(services: Any) -> LiveLooks:
    """services.extras[LOOKS_KEY]. The lifespan builds it before anything reads it; a Services built without the
    lifespan (tests, the soak) gets it built here once, under a lock, so concurrent threadpool readers share one."""
    extras = getattr(services, "extras", None)
    if extras is None:  # a bare stand-in for Services (tests): the approved looks, never stored
        sha = config_sha(services.config_dir)
        return _empty(services.config_dir, None, git_trio(services.config_dir), sha)
    looks = extras.get(LOOKS_KEY)
    if looks is not None:
        return looks
    with _build_lock:
        looks = services.extras.get(LOOKS_KEY)
        if looks is None:
            looks = build_live_looks(services)
    return looks


def live_trio(services: Any) -> ConfigTrio:
    """What the show compiles from now. Every live reader calls this ONCE per request/compile and keeps the
    reference (install_looks swaps the whole trio atomically)."""
    return live_looks(services).trio


def load_problem_line(looks: LiveLooks) -> str:
    return (
        f"{ALERT_PREFIX} {LOOKS_PATH} couldn't be used ({looks.problem}), so the show runs the approved (git) looks. "
        "Publish again from the Design Lab, or Revert to approved."
    )


def git_changed_line(looks: LiveLooks) -> str:
    current = looks.file.current
    name = current.name if current is not None else "?"
    when = _date(current.published_at) if current is not None else "?"
    return (
        f"{ALERT_PREFIX} “{name}” is live and masks config/ changes deployed since {when}; Revert to approved or "
        "republish."
    )


def _date(t: float) -> str:
    try:
        return datetime.fromtimestamp(t, UTC).astimezone().strftime("%b %-d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "?"


def runtime_problem_lines(services: Any) -> list[str]:
    """The /ops "Needs attention" lines while they stand (routes/devices.runtime_problems): never a static startup
    line, so a fixed problem leaves /ops at once."""
    looks = (getattr(services, "extras", None) or {}).get(LOOKS_KEY)
    if looks is None:
        return []
    lines = []
    if looks.problem:
        lines.append(load_problem_line(looks))
    if looks.git_changed and looks.source == "published":
        lines.append(git_changed_line(looks))
    return lines


def preflight_line(config_dir: Path, data: Path | None, settings: Any) -> str | None:
    """For conductor.preflight (stream R's file; B1 adds this line): read-only — what the new code makes of the data
    folder's published looks. None when they load (or there are none); the fallback's reason otherwise. Never writes:
    the equals-git rewrite is left to the show's own start."""
    if data is None:
        return None
    # the show's own load (read only: no equals-git rewrite, no log line), so the two can never disagree.
    # _canopy_of runs outside load_live_looks' tolerance on purpose: a canopy.yaml or fins.json this code can't load
    # is a failure (the start would refuse it), not a published-looks problem (D74)
    looks = load_live_looks(
        config_dir, data, settings=settings, canopy=_canopy_of(config_dir), read_only=True
    )
    if looks.problem is None:
        return None
    return f"{LOOKS_PATH} couldn't be used ({looks.problem}); the show would run the approved looks"


# ---- the hot-apply checklist (§2.4) ----------------------------------------------------------------------------------
def install_looks(services: Any, new: ConfigTrio, *, why: str) -> list[str]:
    """Swap every process-lifetime copy of scenes/modes/recipes for `new`, synchronously on the loop thread (no
    await: no request can see half of it). Returns the names of what it refreshed (logged; telemetry
    `lab looks_installed`). The live trio is a deep copy: it never shares an object with the Lab or a loader cache."""
    done: list[str] = []
    looks = live_looks(services)
    new = new.copy()
    looks.trio = new
    looks.generation += 1
    looks.problem = None  # a fixed fallback leaves /ops at once (runtime_problems reads it)
    done.append("live looks")
    wiring = services.extras.get("show")  # ShowWiring
    if wiring is not None:
        wiring.install_looks(new.modes, new.recipes)  # modes_file, recipes, _rehearsal_modes cleared
        done += ["show modes", "show recipes", "rehearsal designs"]
    engine = services.extras.get("canopy")
    if engine is not None:
        engine.invalidate_t1_plans()
        done.append("T1 plans")
    manager = services.extras.get("module_manager")
    if manager is not None:
        manager.install_looks(new.recipes, new.modes)
        done.append("module page")  # + every open session's deps
    lab = services.extras.get("design_lab")
    if lab is not None:
        lab.live_changed()
        done.append("design lab")
    log.info("Published looks installed (%s): %s", why, ", ".join(done))
    services.telemetry.record(
        "lab", action="looks_installed", generation=looks.generation, copies=done, why=why, source="looks"
    )
    services.telemetry.record(
        "alert",
        level="info",
        message=f"{ALERT_PREFIX} installed {why}.",
        alert_key=LOAD_KEY,
        resolves=[LOAD_KEY, GIT_KEY],
    )
    return done


def changed_scenes(a: ConfigTrio, b: ConfigTrio) -> list[str]:
    """The scenes whose look differs between two trios: the scene, its mode, or a recipe colour its mode names."""
    from conductor.looks.schema import _colour_refs

    out = []
    for scene in b.scenes.scenes:
        sid = scene.id
        try:
            before_scene, after_scene = a.scenes.get(sid), b.scenes.get(sid)
            before_mode, after_mode = a.modes.mode(sid), b.modes.mode(sid)
        except KeyError:
            out.append(sid)
            continue
        if (
            before_scene.model_dump() != after_scene.model_dump()
            or before_mode.model_dump() != after_mode.model_dump()
        ):
            out.append(sid)
            continue
        keys = {k for _w, k in _colour_refs(after_mode)}
        if any(
            (a.recipes.colors.get(k).model_dump() if a.recipes.colors.get(k) is not None else None)
            != (b.recipes.colors.get(k).model_dump() if b.recipes.colors.get(k) is not None else None)
            for k in keys
        ):
            out.append(sid)
    return out


def scene_source(looks: LiveLooks, scene_id: str) -> Literal["approved", "published"]:
    if looks.source != "published":
        return "approved"
    return "published" if scene_id in changed_scenes(looks.git, looks.trio) else "approved"


__all__ = [
    "ALERT_PREFIX",
    "CONFIG_FILES",
    "LOCK_KEY",
    "LOOKS_FILE",
    "LOOKS_KEY",
    "ConfigTrio",
    "HistoryEntry",
    "LiveLooks",
    "Publication",
    "ShowLooksFile",
    "build_live_looks",
    "changed_scenes",
    "config_sha",
    "forget_queued",
    "git_trio",
    "install_looks",
    "live_looks",
    "live_trio",
    "load_live_looks",
    "note_repaint",
    "preflight_line",
    "standing_alerts",
    "trio_sha",
    "validate_trio",
    "write_looks",
]
