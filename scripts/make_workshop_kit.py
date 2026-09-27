"""Build the workshop kit: a zip VWBD (or anyone testing one module) unzips and starts with one command.

    uv run python scripts/make_workshop_kit.py            → dist/color-canopy-workshop-kit-<commit>.zip

Only committed files go in (git ls-files at HEAD), so local settings, tokens, data dirs and caches can never leak.
Only what the program needs to run is included — no research notes, context docs or internal planning.
"""

from __future__ import annotations

import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = "color-canopy-workshop"
INCLUDE_DIRS = ("conductor/", "config/", "web/", "workshop/")
INCLUDE_FILES = ("pyproject.toml", "uv.lock", ".python-version")
EXTRA = {  # repo path → path in the zip
    "workshop/README.md": "README.md",
    "workshop/root/start.bat": "start.bat",
    "workshop/root/start.command": "start.command",
    "workshop/root/start-simulator.command": "start-simulator.command",
    "docs/runbooks/bench-prototype-01.md": "docs/bench-prototype-01.md",
}
EXECUTABLE = (".sh", ".command")


def committed_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line]


def kit_entries(files: list[str]) -> dict[str, str]:
    """zip path → repo path."""
    entries = {}
    for f in files:
        if f.startswith("workshop/root/") or f == "workshop/README.md":
            continue
        if f.startswith(INCLUDE_DIRS) or f in INCLUDE_FILES:
            entries[f"{TOP}/{f}"] = f
    for src, dst in EXTRA.items():
        if src in files:
            entries[f"{TOP}/{dst}"] = src
    return entries


def build(out_dir: Path = ROOT / "dist") -> Path:
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"color-canopy-workshop-kit-{commit}.zip"
    entries = kit_entries(committed_files())
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, src in sorted(entries.items()):
            data = subprocess.run(
                ["git", "show", f"HEAD:{src}"], cwd=ROOT, check=True, capture_output=True
            ).stdout
            info = zipfile.ZipInfo(arc, date_time=(2026, 9, 27, 12, 0, 0))
            mode = 0o755 if arc.endswith(EXECUTABLE) else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return target


if __name__ == "__main__":
    path = build()
    print(f"{path} ({path.stat().st_size // 1024} KB)", file=sys.stdout)
