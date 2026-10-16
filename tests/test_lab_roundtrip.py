"""The Design Lab's round-trip proof (PLAN §3.6, §8): an edited export, applied to a scratch copy of the working
tree, passes the whole suite there with only the patch's own changes.

Slow (one full suite per case, minutes each), so it is gated: CI's plain `pytest` skips it and
`make lab-roundtrip` (LAB_ROUNDTRIP=1) runs it. The cases are the three canonical edits in one patch (Golden Hour:
Sunset pink → #FF8FBF, linear 30°, brightness 90; Coastal Tides: 60°, Aqua → #6FD0D8; Lowcountry Calm: Sea glass
→ #CDEBE6) and a rename, an added colour, a removed colour and a reorder (each changes recipes.yaml's keys). A
failure here is an approved-value pin still in a test: move it onto the fixture's `_pins` (§3.7).

The scratch copy is `git init`ed and committed first, so `git apply` and test_workshop's `git ls-files` work there.
The suite runs with this interpreter (`python -m pytest`; pytest's `pythonpath = ["."]` makes the copy's own
`conductor/` the one imported), on the same flags as `make test` on the Mac (`--ignore=tests/test_deploy.py`)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("LAB_ROUNDTRIP") != "1", reason="slow: make lab-roundtrip")

ROOT = Path(__file__).resolve().parent.parent
CASES = ["canonical", "rename", "add", "remove", "reorder"]


def _run(cwd: Path, *args: str, env: dict | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout, check=False
    )


def _working_tree_copy(dest: Path) -> Path:
    """The working tree as it is (tracked and untracked, not ignored), committed in a fresh repo."""
    listed = _run(ROOT, "git", "ls-files", "-co", "--exclude-standard", "-z")
    assert listed.returncode == 0, listed.stderr
    for name in filter(None, listed.stdout.split("\0")):
        source = ROOT / name
        if source.is_file():
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    assert _run(dest, "git", "init", "-q").returncode == 0
    assert _run(dest, "git", "add", "-A").returncode == 0
    base = _run(dest, "git", "-c", "user.name=lab", "-c", "user.email=lab@localhost", "commit", "-qm", "base")
    assert base.returncode == 0, base.stderr
    return dest


@pytest.mark.parametrize("case", CASES)
def test_an_edited_export_passes_the_whole_suite(case, tmp_path):
    from test_lab_export import CANOPY, LIVE, NOW, canonical_edits, structural

    from conductor.design_lab.export import export_patch

    # the edits are written for the approved config of 2026-10-16 (tests/fixtures_lab_baseline/), applied to the
    # working tree's: run this before the scenes change
    patched = canonical_edits(LIVE) if case == "canonical" else structural(case, LIVE)
    result = export_patch(LIVE, patched, root=ROOT, canopy=CANOPY, version=None, now=NOW)
    assert result.problem == "" and result.patch, result.problem
    repo = _working_tree_copy(tmp_path / "tree")
    (tmp_path / "lab.patch").write_text(result.patch, encoding="utf-8")
    apply = _run(repo, "git", "apply", str(tmp_path / "lab.patch"))
    assert apply.returncode == 0, apply.stderr
    changed = _run(repo, "git", "diff", "--name-only")
    assert sorted(changed.stdout.split()) == sorted(result.files)  # git lists them alphabetically
    env = {k: v for k, v in os.environ.items() if k != "LAB_ROUNDTRIP" and not k.startswith("HUBSPACE_")}
    started = time.monotonic()
    suite = _run(
        repo,
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--ignore=tests/test_deploy.py",
        "--ignore=tests/test_lab_roundtrip.py",
        "-p",
        "no:cacheprovider",
        env=env,
        timeout=1800,
    )
    took = time.monotonic() - started
    failures = [line for line in suite.stdout.splitlines() if line.startswith(("FAILED", "ERROR"))]
    tail = "\n".join(suite.stdout.splitlines()[-5:])
    assert suite.returncode == 0, (
        f"{case}: {len(failures)} failed in {took:.0f} s\n" + "\n".join(failures) + tail
    )
    print(f"{case}: the whole suite passed on the exported config in {took:.0f} s ({result.files})")
