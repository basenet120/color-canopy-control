"""Can this checkout's code load the data folder it is about to run over? (D67)

    python -m conductor.preflight

`deploy/mac/run-conductor.sh --check` runs it with the show box's settings exported and the data folder pinned, and
`canopy-mac install` runs `--check` after `uv sync` and **before** it removes the hold and reloads launchd: new code
that can't load the live data stops there. The running Conductor keeps going for now, but the checkout is the
rejected code — any restart (a crash, the watchdog, a reboot, `canopy-mac start`/`restart`) would start it — so
install exits 3 and prints the rollback; roll the checkout back at once.

What it does — offline (no Hubspace, no network), binds no port, writes nothing in the data folder:
- the runtime settings from the environment (`GateSettings.from_env`, the same checks as a start);
- `config/conductor.yaml` (`load_settings`), and the iPad's scene menu `<data>/overrides/settings.yaml` (D76), read
  only: how many scenes it hides, the ids in it that hide nothing (not a scene now, or the idle scene), or why it
  can't be used (the iPad would offer every scene git doesn't hide) — named, never a failure, as at a start;
- the registry: `config/devices.yaml` + `<data>/overrides/registry.yaml` (`load_registry`), naming the old data the
  canopy-only code ignores (removed vignette devices, removed `vignette`/`kind` fields, a removed simulator
  `profile`), and building every entry's simulator profile (startup's SimDriver does, in live mode too);
- the saved enrollments and sessions (`<data>/roles/*.json`), loaded by the real `RoleStore` from a temporary copy
  (so nothing is set aside), naming any record of a removed role it skips. A file this code would set aside as
  corrupt is a failure: after the restart the guest iPad would need a new link;
- the published looks (`<data>/overrides/show-looks.yaml`, D68), read only: a file the new code can't use is named
  (the show would start on the approved looks with an /ops alert), not a failure.

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
from typing import Any, TextIO

from conductor.config import (
    CONFIG_DIR,
    SETTINGS_OVERLAY_PATH,
    apply_settings_overlay,
    data_dir,
    load_registry,
    load_settings,
    load_settings_overlay,
)
from conductor.gate import GateSettings
from conductor.roles import RoleStore
from conductor.sim.profiles import DEFAULT_PROFILE, load_profile

ROLE_FILES = ("enrollments.json", "sessions.json")


class PreflightFailed(Exception):
    """A check found data this code can't use. The message is for the operator."""


def _settings(env: Mapping[str, str], config_dir: Path, data: Path) -> list[str]:
    gate = GateSettings.from_env(env)
    settings = load_settings(config_dir)
    lines = [f"settings   ok (mode {gate.mode}, environment {gate.environment})"]
    menu_line = _ipad_menu(config_dir, data, settings)
    if menu_line is not None:
        lines.append(menu_line)
    return lines


def _ipad_menu(config_dir: Path, data: Path, settings: Any) -> str | None:
    """The iPad's scene menu overlay (D76), read only: None when there is no file. Never a failure: a start over a
    file it can't use offers every scene git doesn't hide, with an /ops line."""
    from conductor.ipad_menu import menu
    from conductor.scenes import load_scenes

    state = load_settings_overlay(data)
    if state.problem:
        return (
            f"settings   {SETTINGS_OVERLAY_PATH} couldn't be used ({state.problem}); the iPad would offer every "
            "scene config/conductor.yaml doesn't hide"
        )
    if state.overlay is None:
        return None
    in_force = settings.model_copy(deep=True)
    apply_settings_overlay(in_force, state)
    m = menu(load_scenes(config_dir), in_force.kiosk)
    line = f"settings   {SETTINGS_OVERLAY_PATH} hides {len(m.hidden)} scene(s)"
    if m.ignored_unknown:
        line += f"; {len(m.ignored_unknown)} listed id(s) aren't scenes now (ignored)"
    if m.ignored_idle:
        line += "; it lists the idle scene, which the iPad offers anyway"
    return line


def _registry(env: Mapping[str, str], config_dir: Path, data: Path) -> list[str]:
    registry = load_registry(config_dir, data)
    # Startup builds a SimDriver over the registry (in live mode too), which loads every enabled entry's simulator
    # profile; one it can't build is a failed start. Check them all — a disabled entry too, since /ops can enable it.
    for entry in registry.file.devices:
        name = entry.profile or DEFAULT_PROFILE
        try:
            load_profile(name)
        except Exception as exc:  # noqa: BLE001 - whatever SimDriver would raise
            raise PreflightFailed(f"device {entry.id!r}: {str(exc).strip() or type(exc).__name__}") from None
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


def _looks(env: Mapping[str, str], config_dir: Path, data: Path) -> list[str]:
    # Read-only (D68): never the equals-git rewrite the show's own start may make. A file the new code can't use is
    # not a failure — the show would start on the approved (git) looks with an /ops alert — so it is named, not fatal.
    # A broken git modes/scenes/recipes config raises here, as it would refuse the start.
    from conductor.looks.live import LOOKS_PATH, preflight_line

    problem = preflight_line(config_dir, data, load_settings(config_dir))
    if problem is not None:
        return [f"looks      {problem}"]
    if (data / LOOKS_PATH).is_file():
        return [f"looks      ok ({LOOKS_PATH} loads)"]
    return ["looks      ok (nothing published: the approved looks)"]


# Each check returns the lines it prints, or raises.
CHECKS: tuple[Callable[[Mapping[str, str], Path, Path], list[str]], ...] = (
    _settings,
    _registry,
    _roles,
    _looks,
)


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
