"""Saved versions: `<data>/overrides/looks.yaml` (PLAN §3.5).

```yaml
format: 1
seq: 7                       # last version number issued
current: v0007               # the version the Lab opens on (null = approved)
versions:
  - id: v0007                # f"v{seq:04d}"
    name: "Client Oct 17 v3 – warmer sunset"
    note: "warmer sunset"
    created_at: 1792270800.0 # services.clock.now()
    parent: v0006            # the version it was edited from (null = approved)
    config_sha: 3f9a1c0e2b7d # sha256[:12] of config/{scenes,modes,recipes}.yaml bytes at save
    drafts:                  # only the scenes that differ from approved at save time
      golden-hour: {...}     # SceneDraft.model_dump(mode="json")
```

Writes follow `save_overlay_patch`: validate the whole proposed file, history copy, `tokens.atomic_write` at 0600,
one lock around read-modify-write; never into `config/`; the show never reads this file. A corrupt file is set aside
and flagged on /ops (`pending_alert`, recorded by `service.lab_of` on the loop thread); a file that can't be read or
set aside makes the store read-only (`StoreReadOnly`, a 409).

Loading is per version: the top level (`format`, `seq`, `current`, `versions` as a list) is validated first and only
it can make the file corrupt; each version is then validated on its own, and one that doesn't parse is listed as
unreadable and written back verbatim on the next save. The size is checked before the file is read.

Staleness (does a version still apply to the approved trio?) is computed lazily, in the routes' threadpool, and cached
by `(version id, config_sha)`; loading never re-applies a version.

Owners: the models, constants, `LookStore.__init__`/`file`/`get`/`unreadable`, `default_name` and `config_sha` are
the contracts commit (L); the rest is stream S2's.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from conductor.clock import Clock
from conductor.config import ConductorSettings
from conductor.design_lab import draft as D
from conductor.design_lab.draft import ConfigTrio, Problem, SceneDraft
from conductor.looks import live as _live
from conductor.telemetry import Telemetry
from conductor.tokens import atomic_write

log = logging.getLogger(__name__)

LOOKS_FILE = "looks.yaml"
# config_sha and CONFIG_FILES moved to conductor/looks/live.py (D68: the show reads them too); re-exported
CONFIG_FILES = _live.CONFIG_FILES
config_sha = _live.config_sha
MAX_VERSIONS = 200
MAX_BYTES = 1_000_000
READ_ONLY = "The Lab's store couldn't be set aside: {reason}; saved versions are kept as they are."
NOT_SAVED = "The Lab couldn't save overrides/looks.yaml ({reason}); nothing was changed."
CORRUPT_ALERT = (
    "Design Lab: overrides/looks.yaml couldn't be read ({reason}), so it was set aside as {set_aside} and the Lab "
    "started with no saved versions. The show doesn't use this file."
)
READ_ONLY_ALERT = (
    "Design Lab: overrides/looks.yaml couldn't be read or set aside ({reason}); the Lab won't save until it is "
    "moved by hand."
)
REASON_MAX = 120  # an alert's reason: the first line of the error, at most this long
STORE_PATH = "overrides/looks.yaml"
MAX_DEPTH = (
    64  # a real looks.yaml nests ~7 deep (versions → version → drafts → scene → palette → colour → field)
)
_VERSION_N = re.compile(r"^v(\d+)$")


class _Strict(BaseModel):
    # NaN and ±inf never validate: a non-finite number (`created_at: .nan`) would pass and then break every JSON
    # answer that carries it (Starlette refuses non-finite floats), instead of being listed as one unreadable version.
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Version(_Strict):
    id: str = Field(pattern=r"^v\d{4,}$")
    name: str = Field(min_length=1, max_length=80)
    note: str = Field("", max_length=60)
    created_at: float = Field(ge=0)
    parent: str | None = None
    config_sha: str = Field(pattern=r"^[0-9a-f]{12}$")
    drafts: dict[str, SceneDraft] = Field(default_factory=dict)


class LooksFile(_Strict):
    format: Literal[1] = 1
    seq: int = Field(0, ge=0)
    current: str | None = None
    # the highest "Client <Mon> <D> v<N>" number issued per local date ("2026-10-17" → 3), so deleting the newest
    # version of a day never reissues its name (as `seq` never reissues its id)
    names: dict[str, int] = Field(default_factory=dict)
    versions: list[Version] = Field(default_factory=list, max_length=MAX_VERSIONS)


class _TopLevel(_Strict):
    """The file's top level, validated apart from its versions: only this failing makes the file corrupt."""

    format: Literal[1]
    seq: int = Field(0, ge=0)
    current: str | None = None
    names: dict[str, int] = Field(default_factory=dict)
    versions: list[Any] = Field(default_factory=list, max_length=MAX_VERSIONS)


class NoSuchVersion(KeyError):
    """No saved version has this id (or it is unreadable): a 404. A KeyError, so older `except KeyError` callers keep
    working, but the routes catch only this one, so an internal KeyError is never reported as a missing version."""


class StoreReadOnly(RuntimeError):
    """The store couldn't read or set aside a bad looks.yaml, so it won't write over it (a 409 on every write)."""


class _Corrupt(Exception):
    """looks.yaml can't be used as it stands: set it aside and start empty."""


@dataclass(frozen=True)
class _Unreadable:
    """A saved version that didn't parse: written back exactly as it was read. `id` is its label in the list, unique
    among the entries (a missing id is "?1", "?2", …; a second copy of an id is "<id> (copy 2)"), so each one can be
    deleted on its own."""

    id: str
    raw: Any
    error: str = ""

    def to_json(self) -> dict:
        return {"id": self.id, "unreadable": True, "error": self.error}


Entry = Version | _Unreadable


def _short(text: str) -> str:
    line = (text.strip().splitlines() or [""])[0].strip()
    return line if len(line) <= REASON_MAX else line[: REASON_MAX - 1] + "…"


def _reason(exc: BaseException) -> str:
    """The first line of an error, at most REASON_MAX characters: what the alert and the Lab's banner say."""
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        where = ".".join(str(p) for p in first.get("loc", ())) or "the file"
        return _short(f"{where}: {first.get('msg', 'invalid')}")
    return _short(str(exc)) or type(exc).__name__


def _too_deep(data: Any, limit: int = MAX_DEPTH) -> bool:
    """True when `data` nests deeper than `limit` (walked with a stack, never recursion): such a file is corrupt,
    and one that loads must also dump back (yaml.safe_dump recurses, and fails sooner than safe_load)."""
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


def _number(version_id: Any) -> int:
    m = _VERSION_N.match(version_id) if isinstance(version_id, str) else None
    return int(m.group(1)) if m else 0


def _free_name(directory: Path, stem: str, suffix: str = ".yaml") -> Path:
    """`<stem><suffix>`, or `<stem>-2<suffix>`, `-3`, … when that name is taken."""
    candidate = directory / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def _utc_stamp(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).strftime("%Y%m%dT%H%M%SZ")


class LookStore:
    """The Lab's saved versions. One lock around every read-modify-write, and the in-memory state changes only after
    the file is written, so a failed write leaves both as they were."""

    def __init__(self, data: Path, *, clock: Clock, telemetry: Telemetry) -> None:
        self.data = Path(data)
        self.clock = clock
        self.telemetry = telemetry
        self.path: Path = self.data / "overrides" / LOOKS_FILE
        self.error: str | None = None  # why the last load failed (None = fine)
        self.set_aside: str | None = None  # the name the bad file was moved to
        self.read_only: bool = False  # the bad file couldn't be read or moved: writes answer 409
        self.pending_alert: str | None = None  # the /ops alert `lab_of` records on the loop thread
        self._file = LooksFile()
        self._entries: list[Entry] = []  # the file's order, unreadable versions included
        self._checks: dict[
            tuple[str, str], dict
        ] = {}  # (version id, config_sha) → {stale, problems, needs_d59}
        self._lock = threading.Lock()

    # ---- loading -------------------------------------------------------------------------------------------------
    def load(self) -> LooksFile:
        """Read looks.yaml; never raises (§3.5). No file → an empty store. A corrupt file (bad YAML, a bad top level,
        an unknown format, over MAX_BYTES — checked before reading and bounded while reading —, nested deeper than
        MAX_DEPTH, or not a regular file: a FIFO or a device would block or never end) is set aside and the store
        starts empty; a file that can't be read (OSError) or set aside stays where it is and the store goes
        read-only."""
        with self._lock:
            self.error = self.set_aside = self.pending_alert = None
            self.read_only = False
            self._checks.clear()
            self._set(LooksFile(), [])
            try:
                self._load_file()
            except _Corrupt as exc:
                self._quarantine(str(exc))
            except Exception as exc:  # noqa: BLE001 - never raises: an unreadable file is left alone, read-only
                self._go_read_only(_reason(exc))
            return self._file

    def _load_file(self) -> None:
        try:
            st = self.path.stat()  # follows a symlink: what a read would open
        except FileNotFoundError:
            return
        if not stat.S_ISREG(
            st.st_mode
        ):  # a FIFO blocks a read, a device (/dev/zero) never ends: st_size is 0
            raise _Corrupt("it isn't a regular file")
        if st.st_size > MAX_BYTES:  # never read it
            raise _Corrupt(f"it is {st.st_size} bytes, over the {MAX_BYTES}-byte limit")
        with open(self.path, "rb") as f:
            data = f.read(MAX_BYTES + 1)  # bounded, whatever the size said
        if len(data) > MAX_BYTES:
            raise _Corrupt(f"it is over the {MAX_BYTES}-byte limit")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _Corrupt(f"it isn't UTF-8 text ({exc.reason})") from None
        try:
            raw = yaml.safe_load(text)
        except (yaml.YAMLError, RecursionError) as exc:
            raise _Corrupt(f"it isn't valid YAML ({_reason(exc)})") from None
        if _too_deep(raw):
            raise _Corrupt(f"it is nested more than {MAX_DEPTH} levels deep")
        if not isinstance(raw, dict):
            raise _Corrupt("it isn't a mapping of format, seq, current and versions")
        if "format" in raw and raw["format"] != 1:
            raise _Corrupt(f"format {raw['format']!r} isn't one this Lab reads (it reads format 1)")
        try:
            top = _TopLevel.model_validate(raw)
        except ValidationError as exc:
            raise _Corrupt(_reason(exc)) from None
        entries: list[Entry] = []
        seen: set[str] = set()  # real string ids
        labels: set[str] = set()  # every entry's label (unique, so each can be deleted on its own)
        nameless = 0
        for item in top.versions:
            raw_id = item.get("id") if isinstance(item, dict) else None
            label = raw_id if isinstance(raw_id, str) else None
            try:
                if not isinstance(item, dict):
                    raise TypeError("a version must be a mapping")
                if label is not None and label in seen:
                    raise ValueError(f"a second version with the id {label!r}")
                entries.append(Version.model_validate(item))
            except (ValidationError, ValueError, TypeError) as exc:
                if label is None:
                    nameless += 1
                    shown = f"?{nameless}"
                else:
                    shown, n = label, 1
                    while shown in labels:
                        n += 1
                        shown = f"{label} (copy {n})"
                entries.append(_Unreadable(shown, item, _reason(exc)))
            if label is not None:
                seen.add(label)
            labels.add(entries[-1].id)
        versions = [e for e in entries if isinstance(e, Version)]
        current = top.current if top.current in {v.id for v in versions} else None
        self._set(LooksFile(seq=top.seq, current=current, names=top.names, versions=versions), entries)

    def _quarantine(self, reason: str) -> None:
        """Move the corrupt file aside (it is never overwritten), start empty and raise the /ops alert."""
        target = _free_name(self.path.parent, f"looks.corrupt-{_utc_stamp(self.clock.now())}")
        try:
            os.replace(self.path, target)
        except OSError as exc:
            self._go_read_only(f"{reason}; moving it aside failed: {_reason(exc)}")
            return
        try:
            if not target.is_symlink():  # chmod follows a link: never change the mode of a file elsewhere
                os.chmod(target, 0o600)
        except OSError:
            log.warning("Design Lab: couldn't make %s 0600", target)
        self.error = _short(reason)
        self.set_aside = target.name
        self.pending_alert = CORRUPT_ALERT.format(reason=self.error, set_aside=target.name)
        log.warning("Design Lab: %s couldn't be read (%s); set aside as %s", self.path, reason, target.name)

    def _go_read_only(self, reason: str) -> None:
        self.error = _short(reason)
        self.read_only = True
        self.pending_alert = READ_ONLY_ALERT.format(reason=self.error)
        log.warning(
            "Design Lab: %s couldn't be read or set aside (%s); the store is read-only", self.path, reason
        )

    def _set(self, looks: LooksFile, entries: list[Entry]) -> None:
        self._file = looks
        self._entries = list(entries)

    # ---- reading -------------------------------------------------------------------------------------------------
    def file(self) -> LooksFile:
        """The loaded state."""
        return self._file

    def get(self, version_id: str) -> Version:
        """The saved version; NoSuchVersion (a KeyError) when there is none (or it is unreadable)."""
        for version in self._file.versions:
            if version.id == version_id:
                return version
        raise NoSuchVersion(version_id)

    def unreadable(self) -> list[dict]:
        """The versions that didn't parse, as `{"id", "unreadable": true, "error"}` (kept in the file verbatim)."""
        return [e.to_json() for e in self._entries if isinstance(e, _Unreadable)]

    # ---- writing (the caller holds the lock) ---------------------------------------------------------------------
    def _writable(self) -> None:
        if self.read_only:
            raise StoreReadOnly(READ_ONLY.format(reason=self.error or "the file couldn't be read or moved"))

    @staticmethod
    def _refuse(message: str) -> D.DraftError:
        return D.DraftError([Problem(path=STORE_PATH, field="draft", message=message)])

    def _write(
        self, seq: int, current: str | None, entries: list[Entry], names: Mapping[str, int] | None = None
    ) -> None:
        """Validate the whole proposed file, write the history copy, then the file (atomic, 0600); only then
        change the in-memory state."""
        if len(entries) > MAX_VERSIONS:
            raise self._refuse(f"The Lab keeps at most {MAX_VERSIONS} versions; delete an old one first.")
        versions = [e for e in entries if isinstance(e, Version)]
        names = dict(self._file.names if names is None else names)
        looks = LooksFile.model_validate(
            {"seq": seq, "current": current, "names": names, "versions": [v.model_dump() for v in versions]}
        )
        if current is not None and current not in {v.id for v in versions}:
            raise NoSuchVersion(current)
        document: dict[str, Any] = {"format": 1, "seq": seq, "current": current}
        if names:
            document["names"] = names
        document["versions"] = [
            e.model_dump(mode="json") if isinstance(e, Version) else e.raw for e in entries
        ]
        _TopLevel.model_validate(document)  # the top level exactly as load() will read it
        try:
            text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True).encode()
        except RecursionError:
            raise self._refuse(
                "A saved version is nested too deeply to write back; delete it first."
            ) from None
        if len(text) > MAX_BYTES:
            raise self._refuse(
                f"The saved versions would be {len(text)} bytes, over the {MAX_BYTES}-byte limit; delete an old "
                "version first."
            )
        try:
            history = _free_name(self.path.parent / "history", f"looks-{_utc_stamp(self.clock.now())}")
            atomic_write(history, text, mode=0o600)
            atomic_write(self.path, text, mode=0o600)
        except OSError as exc:
            raise StoreReadOnly(NOT_SAVED.format(reason=_reason(exc))) from None
        self._set(looks, entries)

    def _next_seq(self) -> int:
        return max([self._file.seq, *(_number(e.id) for e in self._entries)]) + 1

    def save_version(
        self,
        *,
        drafts: Mapping[str, SceneDraft],
        note: str,
        name: str | None,
        parent: str | None,
        approved: ConfigTrio,
        canopy: dict,
        settings: ConductorSettings,
        config_sha: str,
    ) -> Version:
        """Validate (apply_drafts of the new version against `approved`; older versions aren't re-validated), history
        copy, atomic 0600 write; the new version becomes `current`. Only the scenes that differ from approved are
        kept, in catalog order. DraftError (422), StoreReadOnly (409), NoSuchVersion for an unknown parent (404)."""
        order = [s.id for s in approved.scenes.scenes]
        ranked = sorted(drafts.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else len(order))
        with self._lock:
            self._writable()
            if parent is not None:
                self.get(parent)
            kept = {sid: d for sid, d in ranked if sid not in order or not D.is_approved(approved, d)}
            D.apply_drafts(approved, kept, canopy=canopy, settings=settings)
            now = self.clock.now()
            seq = self._next_seq()
            names = [e.name for e in self._entries if isinstance(e, Version)] + [
                e.raw["name"]
                for e in self._entries
                if isinstance(e, _Unreadable)
                and isinstance(e.raw, dict)
                and isinstance(e.raw.get("name"), str)
            ]
            day = local_day(now)
            issued = dict(self._file.names)
            chosen = name or default_name(note, now, names, issued=issued.get(day, 0))
            if not name and (n := day_number(chosen)) is not None:
                issued[day] = max(issued.get(day, 0), n)
            version = Version(
                id=f"v{seq:04d}",
                name=chosen,
                note=note,
                created_at=now,
                parent=parent,
                config_sha=config_sha,
                drafts=kept,
            )
            self._write(seq, version.id, [*self._entries, version], issued)
            self._check(
                version,
                approved=approved,
                canopy=canopy,
                settings=settings,
                config_sha=config_sha,
                applies=True,
            )
            return version

    def delete_version(self, version_id: str) -> None:
        """Delete exactly one entry: the readable version with this id, else the unreadable one with this label (an
        unreadable twin of a readable id stays, written back verbatim). NoSuchVersion; clears `current` if it pointed
        there. StoreReadOnly. Its number is never issued again (`seq` stays)."""
        with self._lock:
            self._writable()
            match = next(
                (i for i, e in enumerate(self._entries) if isinstance(e, Version) and e.id == version_id),
                None,
            )
            if match is None:
                match = next((i for i, e in enumerate(self._entries) if e.id == version_id), None)
            if match is None:
                raise NoSuchVersion(version_id)
            entries = [e for i, e in enumerate(self._entries) if i != match]
            current = None if self._file.current == version_id else self._file.current
            self._write(self._next_seq() - 1, current, entries)
            for key in [k for k in self._checks if k[0] == version_id]:
                self._checks.pop(key, None)

    def set_current(self, version_id: str | None) -> None:
        """The version the Lab opens on (None = approved). NoSuchVersion; StoreReadOnly. Nothing is written when it
        is already current."""
        with self._lock:
            self._writable()
            if version_id is not None:
                self.get(version_id)
            if version_id == self._file.current:
                return
            self._write(self._file.seq, version_id, list(self._entries))

    # ---- answers -------------------------------------------------------------------------------------------------
    def _check(
        self,
        version: Version,
        *,
        approved: ConfigTrio,
        canopy: dict,
        settings: ConductorSettings,
        config_sha: str,
        applies: bool = False,
    ) -> dict:
        """Does the version still apply to the approved trio? Cached by (version id, config_sha); a version that
        doesn't is stale (with its problems), never a load failure. `applies`: the caller has just applied it (a
        save), so it isn't applied again."""
        key = (version.id, config_sha)
        cached = self._checks.get(key)
        if cached is not None:
            return cached
        problems: list[dict] = []
        try:
            if not applies:
                D.apply_drafts(approved, version.drafts, canopy=canopy, settings=settings)
        except D.DraftError as exc:
            problems = [p.to_json() for p in exc.problems]
        except (
            ValueError
        ) as exc:  # pydantic's ValidationError too: one version that can't apply is stale, never fatal
            problems = [Problem(path=STORE_PATH, field="draft", message=_reason(exc)).to_json()]
        needs_d59 = False
        for draft in version.drafts.values():
            try:
                needs_d59 = needs_d59 or bool(
                    D.draft_changes(approved, draft, canopy=canopy).get("needs_d59")
                )
            except (KeyError, ValueError):  # a scene the approved config no longer has: the problems say so
                continue
        result = {"stale": bool(problems), "problems": problems, "needs_d59": needs_d59}
        self._checks[key] = result
        return result

    def _summary(self, version: Version, **ctx: Any) -> dict:
        check = self._check(version, **ctx)
        return {
            "id": version.id,
            "name": version.name,
            "note": version.note,
            "created_at": version.created_at,
            "parent": version.parent,
            "scenes": list(version.drafts),
            "needs_d59": check["needs_d59"],
            "stale": check["stale"],
            "problems": list(check["problems"]),
            "older_config": version.config_sha != ctx["config_sha"],
        }

    def version_summaries(
        self, *, approved: ConfigTrio, canopy: dict, settings: ConductorSettings, config_sha: str
    ) -> list[dict]:
        """Every version as a VersionSummary (§4: id, name, note, created_at, parent, scenes, needs_d59, stale,
        problems, older_config), newest first, then the unreadable ones as `{"id", "unreadable": true, "error"}`.
        Staleness is computed lazily (threadpool) and cached by (version id, config_sha)."""
        ctx = {"approved": approved, "canopy": canopy, "settings": settings, "config_sha": config_sha}
        newest_first = sorted(self._file.versions, key=lambda v: (_number(v.id), v.created_at), reverse=True)
        return [self._summary(v, **ctx) for v in newest_first] + self.unreadable()

    def version_json(
        self,
        version_id: str,
        *,
        approved: ConfigTrio,
        canopy: dict,
        settings: ConductorSettings,
        config_sha: str,
    ) -> dict:
        """VersionJSON = VersionSummary + {"drafts": {scene_id: SceneDraft JSON}}. NoSuchVersion."""
        return self.version_answer(
            self.get(version_id), approved=approved, canopy=canopy, settings=settings, config_sha=config_sha
        )

    def version_answer(
        self,
        version: Version,
        *,
        approved: ConfigTrio,
        canopy: dict,
        settings: ConductorSettings,
        config_sha: str,
    ) -> dict:
        """VersionJSON of a Version in hand (a save answers with what it saved, even if a delete lands right after)."""
        ctx = {"approved": approved, "canopy": canopy, "settings": settings, "config_sha": config_sha}
        return {
            **self._summary(version, **ctx),
            "drafts": {sid: d.model_dump(mode="json") for sid, d in version.drafts.items()},
        }


_NAME_N = re.compile(r"^Client (?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2}) v(?P<n>\d+)")


def local_day(now: float) -> str:
    """The box's local date of an epoch time, as the store's `names` keys it ("2026-10-17")."""
    return f"{datetime.fromtimestamp(now, UTC).astimezone():%Y-%m-%d}"


def day_number(name: str) -> int | None:
    """N of a default name ("Client Oct 17 v3 – x" → 3), else None."""
    m = _NAME_N.match(name)
    return int(m["n"]) if m is not None else None


def default_name(note: str, now: float, existing: Iterable[str], *, issued: int = 0) -> str:
    """`Client <Mon> <D> v<N>[ – <note>]` in the box's local date; N = 1 + the largest N already used that day, among
    the names still there and `issued` (the store's high-water mark for the day), so a deleted version's name is never
    reissued, not even the newest one's."""
    dt = datetime.fromtimestamp(now, UTC).astimezone()  # the box's local date
    mon, day = f"{dt:%b}", str(dt.day)
    used = [
        int(m["n"])
        for name in existing
        if (m := _NAME_N.match(name)) is not None and m["mon"] == mon and m["day"] == day
    ]
    base = f"Client {mon} {day} v{max([*used, issued], default=0) + 1}"
    room = 80 - len(base) - 3  # Version.name is at most 80 characters
    return f"{base} – {note[:room]}" if note else base
