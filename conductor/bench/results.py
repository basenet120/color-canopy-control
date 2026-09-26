"""Bench results on disk: `<data>/bench/<run_id>.json`, written atomically (07 §8), plus scrubbed artifacts.

- `save` / `load` / `list` — the run records the runner writes after every step.
- `save_artifact` / `load_artifact` — big outputs kept beside a run (Gate A's profile, already scrubbed) under
  `<data>/bench/artifacts/<run_id>.<name>.json`.
- `anonymized(record)` — the "copy anonymized JSON" export: the record with ids, MACs, SSIDs, emails, names and
  geo scrubbed (conductor/anonymize.py), so Alon can paste it into a build session.
- `fixture_shape(raw)` — a device dump in the simulator fixture's shape (aioafero's AferoDevice layout, as in
  `conductor/sim/profiles/README.md`), so Gate A's download can become `sqhdk03.json`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from conductor.anonymize import anonymize
from conductor.tokens import atomic_write

log = logging.getLogger(__name__)

RUN_ID_RE = re.compile(r"^[0-9A-Za-z_-]{1,80}$")
ARTIFACT_RE = re.compile(r"^[a-z_]{1,40}$")
EXPORT_NOTE = (
    "Anonymized bench result from the Color Canopy Conductor: ids, MACs, SSIDs, emails, names and locations are "
    "replaced with placeholders."
)


def is_run_id(value: str) -> bool:
    return bool(RUN_ID_RE.match(value or ""))


class BenchResults:
    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def _path(self, run_id: str) -> Path:
        if not is_run_id(run_id):
            raise ValueError("not a bench run id")
        return self.dir / f"{run_id}.json"

    def _artifact_path(self, run_id: str, name: str) -> Path:
        if not is_run_id(run_id) or not ARTIFACT_RE.match(name):
            raise ValueError("not a bench artifact")
        return self.dir / "artifacts" / f"{run_id}.{name}.json"

    # ---- records -------------------------------------------------------------------------------------------------
    def save(self, record: dict[str, Any]) -> None:
        data = json.dumps(record, indent=1, default=str).encode()
        try:
            atomic_write(self._path(record["run_id"]), data, mode=0o600)
        except OSError:
            log.exception("couldn't save bench result %s", record.get("run_id"))

    def load(self, run_id: str) -> dict[str, Any] | None:
        try:
            path = self._path(run_id)
        except ValueError:
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        """Summaries of saved runs, newest first."""
        if not self.dir.is_dir():
            return []
        out = []
        for path in self.dir.glob("*.json"):
            record = self.load(path.stem)
            if record is None:
                continue
            result = record.get("result") or {}
            out.append(
                {
                    "run_id": record.get("run_id"),
                    "job": record.get("job"),
                    "title": record.get("title"),
                    "device_id": record.get("device_id"),
                    "simulated": record.get("simulated"),
                    "status": record.get("status"),
                    "started": record.get("started"),
                    "finished": record.get("finished"),
                    "verdict": result.get("verdict"),
                    "confirmed_verdict": record.get("confirmed_verdict"),
                    "summary": result.get("summary"),
                }
            )
        out.sort(key=lambda r: (r.get("started") or 0, r.get("run_id") or ""), reverse=True)
        return out[:limit]

    # ---- artifacts -----------------------------------------------------------------------------------------------
    def save_artifact(self, run_id: str, name: str, value: Any) -> None:
        data = json.dumps(value, indent=1, default=str).encode()
        try:
            atomic_write(self._artifact_path(run_id, name), data, mode=0o600)
        except OSError:
            log.exception("couldn't save bench artifact %s/%s", run_id, name)

    def load_artifact(self, run_id: str, name: str) -> Any:
        try:
            with open(self._artifact_path(run_id, name), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    # ---- export --------------------------------------------------------------------------------------------------
    @staticmethod
    def anonymized(record: dict[str, Any], *, keep: Iterable[str] = (), extra: Iterable[str] = ()) -> dict:
        """The copyable export: the record scrubbed. Logical registry ids in `keep` stay readable (they are in
        git); driver addresses in `extra` become placeholders wherever they appear."""
        export = {"note": EXPORT_NOTE, **record}
        return anonymize(export, extra_ids=[e for e in extra if e], keep=keep)


def fixture_shape(raw: dict[str, Any]) -> dict[str, Any]:
    """A device dump in the simulator fixture's layout. Dumps already in that layout (the SimDriver's) pass
    through; Afero's raw metadevice shape (`description.functions`, `state.values`) is rearranged."""
    if "functions" in raw and "states" in raw:
        return dict(raw)
    description = raw.get("description") or {}
    device = description.get("device") or {}
    capabilities = []
    for cap in raw.get("capabilities") or []:
        if isinstance(cap, dict):
            base = {k: cap.get(k) for k in ("functionClass", "type", "schedulable", "functionInstance")}
            opts = {k: v for k, v in cap.items() if k not in base}
            capabilities.append({**base, "_opts": opts})
    return {
        "id": raw.get("id"),
        "device_id": raw.get("deviceId"),
        "model": device.get("model"),
        "device_class": device.get("deviceClass"),
        "default_name": device.get("defaultName"),
        "default_image": description.get("defaultImage"),
        "friendly_name": raw.get("friendlyName"),
        "capabilities": capabilities,
        "functions": list(description.get("functions") or []),
        "states": list((raw.get("state") or {}).get("values") or []),
        "children": list(raw.get("children") or []),
        "manufacturerName": device.get("manufacturerName"),
        "split_identifier": None,
        "version_data": raw.get("version_data"),
    }
