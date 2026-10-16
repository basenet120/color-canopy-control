"""scripts/dev_design_lab.py (`make lab-dev`): the Design Lab's dev server is safe to start on the show box.

It runs the simulator only, never inherits a Hubspace, Railway or secret variable, never binds port 8000 (the show
Conductor's), binds loopback only while auth is off, never uses the show box's data, and the tracked launch entry
`design-lab` is on 8200 while the `conductor` entry is left as it was.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _launcher():
    spec = importlib.util.spec_from_file_location("dev_design_lab", ROOT / "scripts" / "dev_design_lab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _no_server(monkeypatch):
    """Fail the test if anything would start uvicorn."""
    import uvicorn

    def boom(*a, **kw):
        raise AssertionError("the dev server must not start in this test")

    monkeypatch.setattr(uvicorn, "run", boom)


def test_the_environment_strips_hubspace_and_secrets(tmp_path):
    lab = _launcher()
    base = {
        "PATH": "/usr/bin",
        "HOME": "/Users/someone",
        "HUBSPACE_LIVE": "1",
        "HUBSPACE_LIVE_UNTIL": "2026-10-30",
        "HUBSPACE_DEVICE_ALLOWLIST": "a,b",
        "HUBSPACE_ANYTHING_NEW": "x",
        "CONDUCTOR_BASIC_AUTH": "crew:canopy",
        "CONDUCTOR_SECRET_KEY": "k",
        "CONDUCTOR_ACCOUNT_CONNECT": "1",
        "RAILWAY_ENVIRONMENT_NAME": "staging",
        "RAILWAY_GIT_COMMIT_SHA": "abc",
        "GITHUB_TOKEN": "t",
        "SOME_PASSWORD": "p",
        "CONDUCTOR_ENV": "workshop",
        "CONDUCTOR_ENGINE": "off",
        "CONDUCTOR_MODE": "live",
        "CONDUCTOR_DATA_DIR": "/somewhere/else",
    }
    env = lab.environment(tmp_path / "data", base)
    assert env["CONDUCTOR_MODE"] == "sim"
    assert env["CONDUCTOR_AUTH"] == "off"
    assert env["CONDUCTOR_DATA_DIR"] == str(tmp_path / "data")
    assert env["PATH"] == "/usr/bin" and env["HOME"] == "/Users/someone"
    assert not [k for k in env if k.startswith(("HUBSPACE_", "RAILWAY_"))]
    for gone in (
        "CONDUCTOR_BASIC_AUTH",
        "CONDUCTOR_SECRET_KEY",
        "CONDUCTOR_ACCOUNT_CONNECT",
        "GITHUB_TOKEN",
        "SOME_PASSWORD",
        "CONDUCTOR_ENV",
        "CONDUCTOR_ENGINE",
    ):
        assert gone not in env, gone
    # Everything the guest-panel launcher strips is stripped here too.
    assert not set(lab.UNSAFE_ENV) & set(env)


def test_port_8000_is_refused(monkeypatch, capsys):
    lab = _launcher()
    _no_server(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        lab.main(["--port", "8000"])
    assert exc.value.code == 2
    assert "port 8000 belongs to the show Conductor" in capsys.readouterr().err
    assert lab.refusal(port=8200, host="127.0.0.1", data=None) is None
    assert lab.DEFAULT_PORT == 8200


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::", "mac-mini.local"])
def test_a_non_loopback_host_is_refused_with_auth_off(monkeypatch, capsys, host):
    lab = _launcher()
    _no_server(monkeypatch)
    with pytest.raises(SystemExit):
        lab.main(["--host", host])
    assert "auth is off, so this server is for this machine only" in capsys.readouterr().err
    for ok in ("127.0.0.1", "localhost", "::1"):
        assert lab.refusal(port=8200, host=ok, data=None) is None


def test_the_launch_entry_is_design_lab_on_8200():
    config = json.loads((ROOT / ".claude" / "launch.json").read_text())
    entries = {c["name"]: c for c in config["configurations"]}
    lab = entries["design-lab"]
    assert lab["port"] == 8200
    assert lab["runtimeExecutable"] == "uv"
    assert lab["runtimeArgs"] == ["run", "--offline", "python", "scripts/dev_design_lab.py", "--port", "8200"]
    assert "8000" not in lab["runtimeArgs"]
    # The show Conductor's entry is kept untouched (and never started from a session on the show box).
    assert entries["conductor"] == {
        "name": "conductor",
        "runtimeExecutable": "uv",
        "runtimeArgs": ["run", "uvicorn", "conductor.app:app", "--port", "8000"],
        "port": 8000,
    }
    makefile = (ROOT / "Makefile").read_text()
    for target in ("lab-dev", "lab-roundtrip", "scene-pins"):
        assert f"\n{target}:" in makefile, target
    phony = makefile.splitlines()[0]
    assert phony.startswith(".PHONY:") and {"lab-dev", "lab-roundtrip", "scene-pins"} <= set(phony.split())
    assert "scripts/dev_design_lab.py" in makefile


def test_the_data_dir_is_never_the_show_box(monkeypatch, capsys, tmp_path):
    lab = _launcher()
    _no_server(monkeypatch)
    home = tmp_path / "home"
    for show in lab.show_box_dirs(home):
        show.mkdir(parents=True)
        for data in (show, show / "data", show / "a" / ".." / "b"):
            assert "show box's data" in lab.refusal(port=8200, host="127.0.0.1", data=data, home=home)
    assert lab.refusal(port=8200, host="127.0.0.1", data=tmp_path / "scratch", home=home) is None
    # Through main(), with HOME pointed at the fake home (the real show box's dirs are never touched).
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(SystemExit):
        lab.main(["--data", str(home / "canopy-show" / "lab")])
    assert "show box's data" in capsys.readouterr().err
    assert not (home / "canopy-show" / "lab").exists()
