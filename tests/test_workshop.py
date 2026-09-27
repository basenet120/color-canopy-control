"""The workshop kit: launcher settings, the allow step, simulator isolation, and what the zip may contain."""

from __future__ import annotations

import io
import os
import stat
import sys
from pathlib import Path

import pytest

from conductor import workshop

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import make_workshop_kit


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setattr(workshop, "ENV_FILE", tmp_path / "workshop.env")
    monkeypatch.setattr(workshop, "DATA_DIR", tmp_path / "workshop-data")
    monkeypatch.setattr(workshop, "SIM_DATA_DIR", tmp_path / "workshop-data-sim")
    return tmp_path


def test_setup_writes_a_private_bench_only_env_and_keeps_secrets_on_rerun(ws):
    out = io.StringIO()
    first = workshop.setup("Crew@Example.invalid", "2026-10-19", out=out)
    assert stat.S_IMODE((ws / "workshop.env").stat().st_mode) == 0o600
    assert first["CONDUCTOR_MODE"] == "bench" and first["HUBSPACE_LIVE"] == "1"
    assert first["HUBSPACE_ACCOUNT_ALLOWLIST"] == "crew@example.invalid"
    assert first["HUBSPACE_DEVICE_ALLOWLIST"] == ""  # nothing writable until the crew allows their module
    assert first["HUBSPACE_LIVE_UNTIL"] == "2026-10-19"
    second = workshop.setup("crew@example.invalid", "2026-10-19", out=io.StringIO())
    assert (
        second["CONDUCTOR_SECRET_KEY"] == first["CONDUCTOR_SECRET_KEY"]
    )  # the saved sign-in stays decryptable
    assert second["CONDUCTOR_BASIC_AUTH"] == first["CONDUCTOR_BASIC_AUTH"]
    assert first["CONDUCTOR_SECRET_KEY"] not in out.getvalue()  # the key is never printed


def test_the_settings_pass_the_live_gate(ws):
    from conductor.gate import GateSettings, LiveGate, Purpose

    values = workshop.setup("crew@example.invalid", "2026-10-19", out=io.StringIO())
    gate = LiveGate(GateSettings.from_env(values))
    assert gate.purpose_allowed(Purpose.BENCH)[0] and gate.purpose_allowed(Purpose.ACCOUNT)[0]
    assert not gate.purpose_allowed(Purpose.ENGINE)[0]  # the show engine stays on the simulator


def test_allow_needs_a_registered_module_then_writes_it(ws):
    workshop.setup("crew@example.invalid", "2026-10-19", out=io.StringIO())
    assert workshop.allow(assume_yes=True, out=io.StringIO()) is False
    overrides = ws / "workshop-data" / "overrides"
    overrides.mkdir(parents=True)
    (overrides / "registry.yaml").write_text("devices:\n  bench-kit:\n    metadevice_id: md-kit-777777\n")
    assert workshop.allow(assume_yes=True, out=io.StringIO()) is True
    assert workshop.read_env()["HUBSPACE_DEVICE_ALLOWLIST"] == "md-kit-777777"


def test_simulator_mode_drops_every_hubspace_setting(ws, monkeypatch):
    values = workshop.setup("crew@example.invalid", "2026-10-19", out=io.StringIO())
    values["HUBSPACE_DEVICE_ALLOWLIST"] = "md-kit-777777"
    for key in values:
        monkeypatch.setenv(key, "x")  # monkeypatch restores them afterwards
    workshop._apply(values, sim=True)
    assert os.environ["CONDUCTOR_MODE"] == "sim"
    for key in ("HUBSPACE_LIVE", "HUBSPACE_DEVICE_ALLOWLIST", "HUBSPACE_ACCOUNT_ALLOWLIST"):
        assert key not in os.environ
    assert os.environ["CONDUCTOR_DATA_DIR"].endswith("workshop-data-sim")


def test_the_kit_contains_the_program_and_nothing_private():
    files = make_workshop_kit.committed_files()
    entries = make_workshop_kit.kit_entries(files)
    names = set(entries)
    top = make_workshop_kit.TOP
    for needed in (
        "README.md",
        "start.bat",
        "start.command",
        "start-simulator.command",
        "workshop/start.sh",
        "workshop/start.ps1",
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "conductor/app.py",
        "conductor/workshop.py",
        "config/devices.yaml",
        "web/ops/module/index.html",
    ):
        assert f"{top}/{needed}" in names, needed
    for path in names:
        rel = path.removeprefix(f"{top}/")
        assert not rel.startswith(("research/", "docs/context/", "tests", ".github/", "deploy/")), rel
        assert not rel.endswith((".env", ".token", ".sqlite")), rel
        assert "ULTRACODE" not in rel and "DECISIONS" not in rel, rel


def test_the_kit_zip_builds_with_executable_starters(tmp_path):
    import zipfile

    path = make_workshop_kit.build(tmp_path)
    with zipfile.ZipFile(path) as z:
        info = z.getinfo(f"{make_workshop_kit.TOP}/workshop/start.sh")
        assert (info.external_attr >> 16) & 0o111
        readme = z.read(f"{make_workshop_kit.TOP}/README.md").decode()
        assert "Prototype 01" in readme and "never one of the show accounts" in readme
