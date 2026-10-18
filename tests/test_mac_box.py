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

from conductor.anonymize import check_clean

ROOT = Path(__file__).resolve().parent.parent
MAC = ROOT / "deploy" / "mac"
HELPER = MAC / "canopy-mac"
RUNNER = MAC / "run-conductor.sh"
EXAMPLE = MAC / "conductor.env.example"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def _active(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _stub_python(path: Path, calls: Path, exit_code: int = 0) -> Path:
    """A stand-in for the checkout's Python: records how it was run (args, cwd, the data folder) and exits."""
    path.write_text(
        f'#!/bin/sh\necho "ARGS $* CWD $(pwd) DATA $CONDUCTOR_DATA_DIR" >> "{calls}"\n'
        f'[ "$1 $2" = "-m conductor.preflight" ] && echo "preflight stub output"\nexit {exit_code}\n'
    )
    path.chmod(0o755)
    return path


@pytest.fixture
def box(tmp_path: Path) -> dict:
    home = tmp_path / "home"
    calls = tmp_path / "python-calls.txt"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "CANOPY_MAC_HOME": str(home / "ColorCanopy"),
        "CANOPY_MAC_AGENTS": str(tmp_path / "agents"),
        "CANOPY_MAC_LOG_DIR": str(tmp_path / "logs"),
        # --check runs the checkout's `python -m conductor.preflight`; here a stub (the preflight itself is tested
        # in-process by tests/test_canopy_only.py, never with the show box's settings exported)
        "CANOPY_MAC_PYTHON": str(_stub_python(tmp_path / "stub-python", calls)),
    }
    return {
        "tmp": tmp_path,
        "env": env,
        "home": home / "ColorCanopy",
        "agents": tmp_path / "agents",
        "python_calls": calls,
    }


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


def _documented(text: str) -> set[str]:
    """Every KEY=... the file shows, set or commented out."""
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE))


ENV_ACCESS = re.compile(
    r"""(?:environ(?:\.get)?|getenv|\benv(?:\.get)?)\s*[\[(]\s*["']([A-Z][A-Z0-9_]*)["']"""
)
PREFIXED = re.compile(r"""["']((?:CONDUCTOR|HUBSPACE|RAILWAY)_[A-Z0-9_]+)["']""")
# Read by the image rather than by conductor/: the Dockerfile's CMD (PORT) and uvicorn (FORWARDED_ALLOW_IPS).
IMAGE_VARIABLES = {"PORT", "FORWARDED_ALLOW_IPS"}


def _variables_the_code_reads() -> set[str]:
    names = set(IMAGE_VARIABLES)
    for path in (ROOT / "conductor").rglob("*.py"):
        text = path.read_text()
        names |= set(ENV_ACCESS.findall(text)) | set(PREFIXED.findall(text))
    return names


def test_env_example_documents_every_variable_the_code_reads():
    """Moved here from tests/test_deploy.py (D67): the show box's template names every variable, set or commented."""
    read = _variables_the_code_reads()
    assert {"CONDUCTOR_MODE", "HUBSPACE_LIVE", "CONDUCTOR_SECRET_KEY", "CONDUCTOR_ACCOUNT_CONNECT"} <= read
    missing = read - _documented(EXAMPLE.read_text())
    assert not missing, f"document these in {EXAMPLE.name}: {sorted(missing)}"


def test_deploy_files_hold_nothing_secret_or_identifying():
    """Moved here from tests/test_deploy.py (D67): nothing under deploy/ holds a key, a password or an identifier."""
    files = [p for p in (ROOT / "deploy").rglob("*") if p.is_file()]
    assert HELPER in files and EXAMPLE in files
    fernet = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}=(?![A-Za-z0-9_=-])")
    for path in files:
        text = path.read_text()
        assert check_clean(text) == [], path
        assert not fernet.search(text), f"{path} contains something shaped like a Fernet key"
        assert not re.search(r"^(CONDUCTOR_BASIC_AUTH|CONDUCTOR_SECRET_KEY)=\S", text, re.MULTILINE), path


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


def test_runner_check_runs_the_preflight_from_the_checkout_over_the_pinned_data_folder(box):
    install(box)
    box["python_calls"].unlink()  # install --no-load ran --check once already
    result = run([RUNNER, "--check"], box["env"])
    assert result.returncode == 0, result.stderr
    assert "preflight stub output" in result.stdout
    calls = box["python_calls"].read_text().splitlines()
    assert calls == [f"ARGS -m conductor.preflight CWD {ROOT} DATA {box['home'] / 'data'}"]


def test_runner_check_stops_when_the_preflight_fails_and_install_goes_no_further(box):
    install(box)
    failing = _stub_python(box["tmp"] / "failing-python", box["python_calls"], exit_code=1)
    env = {**box["env"], "CANOPY_MAC_PYTHON": str(failing)}
    result = run([RUNNER, "--check"], env)
    assert result.returncode != 0
    assert "conductor.preflight" in result.stderr and "Nothing was restarted" in result.stderr
    # canopy-mac install runs --check before it writes the agents, removes the hold or reloads launchd
    (box["agents"] / "com.colorcanopy.conductor.plist").unlink()
    again = run([HELPER, "install", "--no-load"], env)
    assert again.returncode != 0
    assert not (box["agents"] / "com.colorcanopy.conductor.plist").exists()


@pytest.mark.parametrize("known", ["installed", "paste-block", "none"])
def test_install_exits_3_and_prints_the_rollback_when_the_preflight_rejects_the_checkout(box, known):
    """The checkout is already the rejected code and the old Conductor is a KeepAlive job: any restart would start
    it. Install says so, prints the rollback (no restart needed) and exits 3, so the paste block can roll back."""
    install(box)
    if known == "installed":
        (box["home"] / "state" / "installed_sha").write_text("1111111aaaaaaa\n")
    if known in ("installed", "paste-block"):
        (box["tmp"] / "canopy-prev-sha.txt").write_text("2222222bbbbbbb\n")  # HOME is tmp
    failing = _stub_python(box["tmp"] / "failing-python", box["python_calls"], exit_code=1)
    result = run([HELPER, "install", "--no-load"], {**box["env"], "CANOPY_MAC_PYTHON": str(failing)})
    assert result.returncode == 3, result.stderr
    prev = {"installed": "1111111aaaaaaa", "paste-block": "2222222bbbbbbb", "none": "<the previous release>"}[known]
    assert f"checkout --detach {prev}" in result.stderr
    assert "uv sync --frozen --no-dev --extra live" in result.stderr
    assert "would start" in result.stderr and "canopy-mac start/restart" in result.stderr


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
    # Models launchd: the Conductor starts loaded; bootout takes `linger` more `print`s to finish (it returns before
    # the process has exited); bootstrap loads it again.
    unloaded, linger = box["tmp"] / "unloaded", box["tmp"] / "linger"
    linger.write_text("0")
    (bin_dir / "launchctl").write_text(
        f"""#!/bin/sh
echo "$*" >> "{calls}"
case "$1" in
print)
  n=$(cat "{linger}")
  if [ "$n" -gt 0 ]; then echo $((n - 1)) > "{linger}"; exit 0; fi
  [ -f "{unloaded}" ] && exit 113
  exit 0 ;;
bootout) touch "{unloaded}"; echo "$LINGER" > "{linger}" ;;
bootstrap) rm -f "{unloaded}" ;;
esac
exit 0
"""
    )
    health = box["tmp"] / "health"  # contains the exit code curl should return
    health.write_text("0")
    (bin_dir / "curl").write_text(f'#!/bin/sh\nexit "$(cat "{health}")"\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {**box["env"], "PATH": f"{bin_dir}:{os.environ['PATH']}", "LINGER": "0"}
    install(box)
    state = box["home"] / "state"
    state.mkdir(exist_ok=True)
    (state / "started_at").write_text(str(int(time.time()) - 600))
    return {"env": env, "calls": calls, "health": health, "state": state, "unloaded": unloaded}


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


def test_watchdog_loads_a_conductor_that_is_neither_loaded_nor_held(stubs, box):
    stubs["unloaded"].touch()
    watchdog(stubs)
    plist = box["agents"] / "com.colorcanopy.conductor.plist"
    assert f"bootstrap gui/{os.getuid()} {plist}" in stubs["calls"].read_text().splitlines()
    assert not stubs["unloaded"].exists()


def test_watchdog_leaves_a_held_conductor_unloaded(stubs):
    stubs["unloaded"].touch()
    (stubs["state"] / "hold").write_text("held")
    watchdog(stubs)
    assert not stubs["calls"].exists()  # a hold means the watchdog doesn't even ask launchd


def test_stop_waits_until_launchd_has_let_go(stubs):
    # bootout returns while the Conductor is still saving its sign-ins; "Stopped" must only be said after that.
    env = {**stubs["env"], "LINGER": "3"}
    result = run([HELPER, "stop", "--yes"], env)
    assert result.returncode == 0, result.stderr
    calls = stubs["calls"].read_text().splitlines()
    after = calls[calls.index(f"bootout gui/{os.getuid()}/com.colorcanopy.conductor") + 1 :]
    assert sum(c.startswith("print ") for c in after) == 4  # three while it lingers, one that finds it gone


def test_start_after_a_hold_loads_it_again(stubs, box):
    assert run([HELPER, "stop", "--yes"], stubs["env"]).returncode == 0
    result = run([HELPER, "start"], stubs["env"])
    assert result.returncode == 0, result.stderr
    calls = stubs["calls"].read_text().splitlines()
    assert f"enable gui/{os.getuid()}/com.colorcanopy.conductor" in calls
    assert calls[-1] == f"bootstrap gui/{os.getuid()} {box['agents'] / 'com.colorcanopy.conductor.plist'}"
    assert not (stubs["state"] / "hold").exists()
