"""The Mac show box (D61): deploy/mac/.

Nothing here needs launchd, the network or a real Conductor: `install --no-load` writes the settings and the
LaunchAgent files into temporary folders, the launcher runs against a stub Python, and the watchdog against stub
`curl` and `launchctl` commands on PATH. Runs on macOS and on CI's Linux.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
MAC = ROOT / "deploy" / "mac"
HELPER = MAC / "canopy-mac"
RUNNER = MAC / "run-conductor.sh"
EXAMPLE = MAC / "conductor.env.example"
VENUE_EXAMPLE = ROOT / "deploy" / "venue" / "conductor.env.example"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def _active(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


@pytest.fixture
def box(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "CANOPY_MAC_HOME": str(home / "ColorCanopy"),
        "CANOPY_MAC_AGENTS": str(tmp_path / "agents"),
        "CANOPY_MAC_LOG_DIR": str(tmp_path / "logs"),
    }
    return {"tmp": tmp_path, "env": env, "home": home / "ColorCanopy", "agents": tmp_path / "agents"}


def run(cmd: list, env: dict, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, *map(str, cmd)], env=env, capture_output=True, text=True, timeout=60, check=False, **kw
    )


def install(box: dict, *args: str) -> subprocess.CompletedProcess:
    result = run([HELPER, "install", "--no-load", *args], box["env"])
    assert result.returncode == 0, result.stderr
    return result


def settings(box: dict) -> dict[str, str]:
    return _active((box["home"] / "conductor.env").read_text())


# ---- the settings template ----------------------------------------------------------------------------------------
def test_example_is_the_live_show_box_with_no_secrets():
    values = _active(EXAMPLE.read_text())
    assert values["CONDUCTOR_MODE"] == "live"
    assert values["HUBSPACE_LIVE"] == "1"
    assert values["HUBSPACE_DEVICE_ALLOWLIST"] == "registry"
    assert values["CONDUCTOR_ENV"] == "venue"
    assert values["CONDUCTOR_BASIC_AUTH"] == "" and values["CONDUCTOR_SECRET_KEY"] == ""
    assert values["HUBSPACE_ACCOUNT_ALLOWLIST"] == ""
    for unsafe in (
        "CONDUCTOR_AUTH",
        "CONDUCTOR_ACCOUNT_CONNECT",
        "CONDUCTOR_ENGINE",
        "FORWARDED_ALLOW_IPS",
        "RAILWAY_ENVIRONMENT_NAME",
        "CONDUCTOR_DATA_DIR",  # pinned by the launcher
    ):
        assert unsafe not in values, unsafe


def test_example_matches_the_venue_box_where_they_overlap():
    venue = _active(VENUE_EXAMPLE.read_text())
    mac = _active(EXAMPLE.read_text())
    for key in (
        "CONDUCTOR_MODE",
        "HUBSPACE_LIVE",
        "HUBSPACE_DEVICE_ALLOWLIST",
        "HUBSPACE_LIVE_UNTIL",
        "CONDUCTOR_ENV",
    ):
        assert mac[key] == venue[key], key


# ---- install --------------------------------------------------------------------------------------------------------
def test_install_creates_private_settings_with_generated_secrets(box):
    result = install(box, "--accounts", "Canopy@Example.invalid, living@example.invalid")
    env_file = box["home"] / "conductor.env"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(box["home"].stat().st_mode) == 0o700
    values = settings(box)
    assert re.fullmatch(r"ops:[0-9a-f]{24}", values["CONDUCTOR_BASIC_AUTH"])
    Fernet(values["CONDUCTOR_SECRET_KEY"].encode())  # a valid key, or this raises
    assert values["HUBSPACE_ACCOUNT_ALLOWLIST"] == "canopy@example.invalid,living@example.invalid"
    output = result.stdout + result.stderr
    assert values["CONDUCTOR_BASIC_AUTH"].split(":", 1)[1] not in output
    assert values["CONDUCTOR_SECRET_KEY"] not in output


def test_install_again_keeps_secrets_and_accounts(box):
    install(box, "--accounts", "canopy@example.invalid")
    first = settings(box)
    install(box)
    assert settings(box) == first


def test_install_refuses_a_bad_account_list(box):
    result = run([HELPER, "install", "--no-load", "--accounts", "not-an-email"], box["env"])
    assert result.returncode != 0
    assert "emails" in result.stderr


def test_launch_agents(box):
    install(box)
    agents = box["agents"]
    conductor = plistlib.loads((agents / "com.colorcanopy.conductor.plist").read_bytes())
    assert conductor["Label"] == "com.colorcanopy.conductor"
    assert conductor["ProgramArguments"] == [str(RUNNER)]
    assert conductor["RunAtLoad"] is True and conductor["KeepAlive"] is True
    assert conductor["ExitTimeOut"] >= 30  # time to save the rotated sign-in on shutdown
    assert conductor["EnvironmentVariables"]["CANOPY_MAC_HOME"] == str(box["home"])
    watchdog = plistlib.loads((agents / "com.colorcanopy.watchdog.plist").read_bytes())
    assert watchdog["ProgramArguments"] == [str(HELPER), "watchdog"]
    assert watchdog["StartInterval"] == 30
    backup = plistlib.loads((agents / "com.colorcanopy.backup.plist").read_bytes())
    assert backup["ProgramArguments"] == [str(HELPER), "backup", "--nightly"]
    assert backup["StartCalendarInterval"] == {"Hour": 4, "Minute": 30}


# ---- the launcher -------------------------------------------------------------------------------------------------
def test_runner_check_prints_no_secrets(box):
    install(box, "--accounts", "canopy@example.invalid")
    result = run([RUNNER, "--check"], box["env"])
    assert result.returncode == 0, result.stderr
    values = settings(box)
    assert values["CONDUCTOR_SECRET_KEY"] not in result.stdout
    assert values["CONDUCTOR_BASIC_AUTH"].split(":", 1)[1] not in result.stdout
    assert "CONDUCTOR_MODE             live" in result.stdout
    assert f"data       {box['home'] / 'data'}" in result.stdout


def test_runner_refuses_missing_settings(box):
    result = run([RUNNER, "--check"], box["env"])
    assert result.returncode != 0 and "canopy-mac install" in result.stderr


def test_runner_refuses_settings_others_can_read(box):
    install(box)
    (box["home"] / "conductor.env").chmod(0o644)
    result = run([RUNNER, "--check"], box["env"])
    assert result.returncode != 0 and "chmod 600" in result.stderr


@pytest.mark.parametrize("key", ["CONDUCTOR_BASIC_AUTH", "CONDUCTOR_SECRET_KEY"])
def test_runner_refuses_an_empty_secret(box, key):
    install(box)
    env_file = box["home"] / "conductor.env"
    env_file.write_text(re.sub(rf"(?m)^{key}=.*$", f"{key}=", env_file.read_text()))
    result = run([RUNNER, "--check"], box["env"])
    assert result.returncode != 0 and key in result.stderr


def test_runner_refuses_a_malformed_line_without_echoing_it(box):
    install(box)
    env_file = box["home"] / "conductor.env"
    env_file.write_text(env_file.read_text() + "this line has a secret-ish value hunter2\n")
    result = run([RUNNER, "--check"], box["env"])
    assert result.returncode != 0 and "isn't KEY=value" in result.stderr
    assert "hunter2" not in result.stderr + result.stdout


def test_runner_refuses_staging_settings(box):
    install(box)
    env_file = box["home"] / "conductor.env"
    env_file.write_text(env_file.read_text() + "RAILWAY_ENVIRONMENT_NAME=staging\n")
    result = run([RUNNER, "--check"], box["env"])
    assert result.returncode != 0 and "staging" in result.stderr


def test_runner_pins_data_dir_drops_local_overrides_and_execs_one_uvicorn(box):
    install(box)
    env_file = box["home"] / "conductor.env"
    env_file.write_text(env_file.read_text() + "CONDUCTOR_DATA_DIR=/somewhere/else\n")
    stub = box["tmp"] / "python"
    dump = box["tmp"] / "exec.txt"
    stub.write_text(f'#!/bin/sh\n{{ echo "ARGS $*"; env; }} > "{dump}"\n')
    stub.chmod(0o755)
    env = {
        **box["env"],
        "CANOPY_MAC_PYTHON": str(stub),
        "CANOPY_MAC_LOG": str(box["tmp"] / "logs" / "conductor.log"),
        "CONDUCTOR_AUTH": "off",  # a parent's local-only opt-out must not reach the show box
        "CONDUCTOR_SIM_SPEED": "fast",
    }
    result = run([RUNNER], env)
    assert result.returncode == 0, result.stderr
    out = dump.read_text()
    assert "ARGS -m uvicorn conductor.app:app --host 0.0.0.0 --port 8000" in out
    assert "--workers" not in out
    assert f"CONDUCTOR_DATA_DIR={box['home'] / 'data'}\n" in out
    assert "CONDUCTOR_AUTH=" not in out and "CONDUCTOR_SIM_SPEED=" not in out
    assert "CONDUCTOR_MODE=live\n" in out
    assert (box["home"] / "state" / "started_at").read_text().strip().isdigit()


# ---- watchdog -----------------------------------------------------------------------------------------------------
@pytest.fixture
def stubs(box):
    bin_dir = box["tmp"] / "bin"
    bin_dir.mkdir()
    calls = box["tmp"] / "launchctl.calls"
    (bin_dir / "launchctl").write_text(f'#!/bin/sh\necho "$*" >> "{calls}"\n')
    health = box["tmp"] / "health"  # contains the exit code curl should return
    health.write_text("0")
    (bin_dir / "curl").write_text(f'#!/bin/sh\nexit "$(cat "{health}")"\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {**box["env"], "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    install(box)
    state = box["home"] / "state"
    state.mkdir(exist_ok=True)
    (state / "started_at").write_text(str(int(time.time()) - 600))
    return {"env": env, "calls": calls, "health": health, "state": state}


def watchdog(stubs) -> subprocess.CompletedProcess:
    result = run([HELPER, "watchdog"], stubs["env"])
    assert result.returncode == 0, result.stderr
    return result


def kickstarts(stubs) -> list[str]:
    calls = stubs["calls"].read_text().splitlines() if stubs["calls"].exists() else []
    return [c for c in calls if c.startswith("kickstart")]


def test_watchdog_restarts_after_three_failed_checks(stubs):
    stubs["health"].write_text("7")
    watchdog(stubs)
    watchdog(stubs)
    assert kickstarts(stubs) == []
    watchdog(stubs)
    assert kickstarts(stubs) == [f"kickstart -k gui/{os.getuid()}/com.colorcanopy.conductor"]
    assert (stubs["state"] / "watchdog.failures").read_text().strip() == "0"


def test_watchdog_resets_when_healthy(stubs):
    stubs["health"].write_text("7")
    watchdog(stubs)
    watchdog(stubs)
    stubs["health"].write_text("0")
    watchdog(stubs)
    stubs["health"].write_text("7")
    watchdog(stubs)
    watchdog(stubs)
    assert kickstarts(stubs) == []


def test_watchdog_waits_out_the_start_up_grace(stubs):
    (stubs["state"] / "started_at").write_text(str(int(time.time()) - 30))
    stubs["health"].write_text("7")
    for _ in range(4):
        watchdog(stubs)
    assert kickstarts(stubs) == []


def test_watchdog_stands_down_during_a_hold(stubs):
    (stubs["state"] / "hold").write_text("held")
    stubs["health"].write_text("7")
    for _ in range(4):
        watchdog(stubs)
    assert kickstarts(stubs) == []


# ---- the rest of the helper ---------------------------------------------------------------------------------------
def test_password_is_only_shown_in_a_terminal(box):
    install(box)
    result = run([HELPER, "password"], box["env"])
    assert result.returncode != 0
    assert settings(box)["CONDUCTOR_BASIC_AUTH"].split(":", 1)[1] not in result.stdout + result.stderr


def test_stop_asks_for_yes(stubs):
    result = run([HELPER, "stop"], stubs["env"])
    assert result.returncode != 0 and "--yes" in result.stdout
    assert not (stubs["state"] / "hold").exists()
    result = run([HELPER, "stop", "--yes"], stubs["env"])
    assert result.returncode == 0, result.stderr
    assert (stubs["state"] / "hold").exists()
    assert any(c.startswith("disable gui/") for c in stubs["calls"].read_text().splitlines())


def test_backup_keeps_the_data_and_a_checksum(box):
    install(box)
    (box["home"] / "data" / "overrides").mkdir(parents=True)
    (box["home"] / "data" / "overrides" / "registry.yaml").write_text("devices: {}\n")
    result = run([HELPER, "backup"], box["env"])
    assert result.returncode == 0, result.stderr
    backups = sorted((box["home"] / "backups").glob("canopy-data-*-manual.tar.gz"))
    assert len(backups) == 1
    assert Path(str(backups[0]) + ".sha256").exists()
    listing = subprocess.run(["tar", "-tzf", str(backups[0])], capture_output=True, text=True, check=True)
    assert "data/overrides/registry.yaml" in listing.stdout
    assert "conductor.env" not in listing.stdout  # the key never goes into a backup


def test_restore_without_tokens_keeps_the_old_folder_and_hands_back(stubs, box):
    data = box["home"] / "data"
    (data / "overrides").mkdir(parents=True)
    (data / "overrides" / "registry.yaml").write_text("devices: {restored: true}\n")
    (data / "tokens").mkdir()
    (data / "tokens" / "venue-canopy.token").write_text("ciphertext")
    assert run([HELPER, "backup"], stubs["env"]).returncode == 0
    backup = next((box["home"] / "backups").glob("*.tar.gz"))
    (data / "overrides" / "registry.yaml").write_text("devices: {changed: true}\n")

    asked = run([HELPER, "restore", backup, "--skip-tokens"], stubs["env"])
    assert asked.returncode != 0 and "--yes" in asked.stdout
    assert "changed" in (data / "overrides" / "registry.yaml").read_text()

    result = run([HELPER, "restore", backup, "--skip-tokens", "--yes"], stubs["env"])
    assert result.returncode == 0, result.stderr
    assert "restored" in (data / "overrides" / "registry.yaml").read_text()
    assert not (data / "tokens" / "venue-canopy.token").exists()
    kept = list(box["home"].glob("data-before-restore-*"))
    assert len(kept) == 1 and "changed" in (kept[0] / "overrides" / "registry.yaml").read_text()
    assert not (stubs["state"] / "hold").exists()  # handed back


def test_restore_refuses_a_backup_that_fails_its_checksum(stubs, box):
    (box["home"] / "data" / "overrides").mkdir(parents=True)
    assert run([HELPER, "backup"], stubs["env"]).returncode == 0
    backup = next((box["home"] / "backups").glob("*.tar.gz"))
    Path(str(backup) + ".sha256").write_text("0" * 64 + f"  {backup.name}\n")
    result = run([HELPER, "restore", backup, "--yes"], stubs["env"])
    assert result.returncode != 0 and "checksum" in result.stderr
