"""Can this checkout's code load the data folder it is about to run over? (D67)

    python -m conductor.preflight

`deploy/mac/run-conductor.sh --check` runs it with the show box's settings exported and the data folder pinned, and
`canopy-mac install` runs `--check` after `uv sync` and **before** it removes the hold and reloads launchd: new code
that can't load the live data stops there, and the running Conductor keeps the show going.

What it does — offline (no Hubspace, no network), binds no port, writes nothing in the data folder:
- the runtime settings from the environment (`GateSettings.from_env`, the same checks as a start);
- `config/conductor.yaml` (`load_settings`);
- the registry: `config/devices.yaml` + `<data>/overrides/registry.yaml` (`load_registry`), naming the old data the
  canopy-only code ignores (removed vignette devices, removed `vignette`/`kind` fields);
- the saved enrollments and sessions (`<data>/roles/*.json`), loaded by the real `RoleStore` from a temporary copy
  (so nothing is set aside), naming any record of a removed role it skips. A file this code would set aside as
  corrupt is a failure: after the restart the guest iPad would need a new link.

Exit 0 when everything loads (old data that is ignored is listed, not a failure), 1 otherwise. The output names
device ids, roles and short fingerprints only — never a token, a password or a session id.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

from conductor.config import CONFIG_DIR, data_dir, load_registry, load_settings
from conductor.gate import GateSettings
from conductor.roles import RoleStore

ROLE_FILES = ("enrollments.json", "sessions.json")


class PreflightFailed(Exception):
    """A check found data this code can't use. The message is for the operator."""


def _settings(env: Mapping[str, str], config_dir: Path, data: Path) -> list[str]:
    gate = GateSettings.from_env(env)
    load_settings(config_dir)
    return [f"settings   ok (mode {gate.mode}, environment {gate.environment})"]


def _registry(env: Mapping[str, str], config_dir: Path, data: Path) -> list[str]:
    registry = load_registry(config_dir, data)
    overlay = "an overlay" if registry.overlay_path and registry.overlay_path.exists() else "no overlay"
    lines = [f"registry   ok ({len(registry.devices)} devices, {overlay})"]
    if registry.dropped:
        lines.append(
            f"registry   ignored removed vignette devices (D67): {', '.join(sorted(registry.dropped))}"
        )
    for device_id, keys in sorted(registry.dropped_fields.items()):
        lines.append(f"registry   ignored removed fields on {device_id} (D67): {', '.join(keys)}")
    return lines


def _roles(env: Mapping[str, str], config_dir: Path, data: Path) -> list[str]:
    source = data / "roles"
    with tempfile.TemporaryDirectory(prefix="canopy-preflight-") as tmp:
        copy = Path(tmp)
        (copy / "roles").mkdir(mode=0o700)
        for name in ROLE_FILES:
            if (source / name).is_file():
                shutil.copyfile(source / name, copy / "roles" / name)
        store = RoleStore(copy)
        if store.problem:
            raise PreflightFailed(f"roles: {store.problem}")
        lines = [
            f"roles      ok ({len(store.list_sessions())} sessions, {len(store.list_enrollments())} open links)"
        ]
        for name, role, fingerprint in store.skipped:
            lines.append(
                f"roles      skipped a {name.removesuffix('.json')} record of the removed role {role!r} "
                f"({fingerprint or 'no fingerprint'}; D67)"
            )
        return lines


# Each check returns the lines it prints, or raises. (B1 adds the published looks here.)
CHECKS: tuple[Callable[[Mapping[str, str], Path, Path], list[str]], ...] = (_settings, _registry, _roles)


def run(
    env: Mapping[str, str] = os.environ,
    config_dir: Path = CONFIG_DIR,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    """Run every check; 0 when this code can load the data folder, 1 when it can't (the reason on `err`)."""
    data = data_dir(env)
    print(f"preflight  {data}", file=out)
    for check in CHECKS:
        try:
            lines = check(env, config_dir, data)
        except Exception as exc:  # noqa: BLE001 - any failure means "don't restart onto this code"
            message = str(exc).strip() or type(exc).__name__
            print(f"preflight  FAILED in {check.__name__.lstrip('_')}: {message}", file=err)
            return 1
        for line in lines:
            print(line, file=out)
    print("preflight  ok: this checkout can load the data folder", file=out)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through run()
    raise SystemExit(run())
