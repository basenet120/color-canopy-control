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

Owners: the models, constants, `LookStore.__init__`/`file`/`get`/`unreadable`, `default_name` and `config_sha` are
the contracts commit (L); the bodies marked `S2` are stream S2's.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from conductor.clock import Clock
from conductor.config import ConductorSettings
from conductor.design_lab.draft import ConfigTrio, SceneDraft
from conductor.telemetry import Telemetry

LOOKS_FILE = "looks.yaml"
MAX_VERSIONS = 200
MAX_BYTES = 1_000_000
CONFIG_FILES = ("scenes.yaml", "modes.yaml", "recipes.yaml")
READ_ONLY = "The Lab's store couldn't be set aside: {reason}; saved versions are kept as they are."


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Version(_Strict):
    id: str = Field(pattern=r"^v\d{4,}$")
    name: str = Field(min_length=1, max_length=80)
    note: str = Field("", max_length=60)
    created_at: float
    parent: str | None = None
    config_sha: str = Field(pattern=r"^[0-9a-f]{12}$")
    drafts: dict[str, SceneDraft] = Field(default_factory=dict)


class LooksFile(_Strict):
    format: Literal[1] = 1
    seq: int = Field(0, ge=0)
    current: str | None = None
    versions: list[Version] = Field(default_factory=list, max_length=MAX_VERSIONS)


class StoreReadOnly(RuntimeError):
    """The store couldn't read or set aside a bad looks.yaml, so it won't write over it (a 409 on every write)."""


class LookStore:
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
        self._unreadable: list[dict] = []  # {"id", "unreadable": True, "error"} per version that didn't parse
        self._lock = threading.Lock()

    def load(self) -> LooksFile:
        """Read looks.yaml; never raises (§3.5). Contracts-commit behaviour: no file → an empty store; anything
        else is S2's."""
        if not self.path.exists():
            self._file = LooksFile()
            self._unreadable = []
            return self._file
        raise NotImplementedError("S2: LookStore.load of an existing looks.yaml")

    def file(self) -> LooksFile:
        """The loaded state."""
        return self._file

    def get(self, version_id: str) -> Version:
        """The saved version; KeyError when there is none (or it is unreadable)."""
        for version in self._file.versions:
            if version.id == version_id:
                return version
        raise KeyError(version_id)

    def unreadable(self) -> list[dict]:
        """The versions that didn't parse, as `{"id", "unreadable": true, "error"}` (kept in the file verbatim)."""
        return list(self._unreadable)

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
        """Validate (apply_drafts against `approved`), history copy, atomic 0600 write; the new version becomes
        `current`. DraftError (422), StoreReadOnly (409), KeyError for an unknown parent (404)."""
        raise NotImplementedError("S2: LookStore.save_version")

    def delete_version(self, version_id: str) -> None:
        """KeyError; clears `current` if it pointed there. StoreReadOnly."""
        raise NotImplementedError("S2: LookStore.delete_version")

    def set_current(self, version_id: str | None) -> None:
        """The version the Lab opens on (None = approved). KeyError; StoreReadOnly."""
        raise NotImplementedError("S2: LookStore.set_current")

    def version_summaries(
        self, *, approved: ConfigTrio, canopy: dict, settings: ConductorSettings, config_sha: str
    ) -> list[dict]:
        """Every version as a VersionSummary (§4: id, name, note, created_at, parent, scenes, needs_d59, stale,
        problems, older_config), newest first, then the unreadable ones as `{"id", "unreadable": true, "error"}`.
        Staleness is computed lazily (threadpool) and cached by (version id, config_sha)."""
        raise NotImplementedError("S2: LookStore.version_summaries")

    def version_json(
        self,
        version_id: str,
        *,
        approved: ConfigTrio,
        canopy: dict,
        settings: ConductorSettings,
        config_sha: str,
    ) -> dict:
        """VersionJSON = VersionSummary + {"drafts": {scene_id: SceneDraft JSON}}. KeyError."""
        raise NotImplementedError("S2: LookStore.version_json")


_NAME_N = re.compile(r"^Client (?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2}) v(?P<n>\d+)")


def default_name(note: str, now: float, existing: Iterable[str]) -> str:
    """`Client <Mon> <D> v<N>[ – <note>]` in the box's local date; N = 1 + the largest N already used that day (not a
    count, so deleting v1 of v1, v2 doesn't reissue "v2")."""
    dt = datetime.fromtimestamp(now, UTC).astimezone()  # the box's local date
    mon, day = f"{dt:%b}", str(dt.day)
    used = [
        int(m["n"])
        for name in existing
        if (m := _NAME_N.match(name)) is not None and m["mon"] == mon and m["day"] == day
    ]
    base = f"Client {mon} {day} v{max(used, default=0) + 1}"
    return f"{base} – {note}" if note else base


def config_sha(config_dir: Path) -> str:
    """sha256[:12] of config/{scenes,modes,recipes}.yaml's bytes, in that order: what a version was saved against."""
    h = hashlib.sha256()
    for name in CONFIG_FILES:
        h.update((Path(config_dir) / name).read_bytes())
    return h.hexdigest()[:12]
