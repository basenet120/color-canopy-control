"""The venue box (07 §10, P3.5): deploy/venue/ and scripts/release.sh.

Nothing here needs root, Docker, systemd or the network: the shell scripts run in their dry-run / --help modes or
against stub commands on PATH. `docker compose config` runs only when Compose is installed (it needs no daemon).
"""

from __future__ import annotations

import configparser
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from cryptography.fernet import Fernet

from conductor.anonymize import check_clean

ROOT = Path(__file__).resolve().parent.parent
VENUE = ROOT / "deploy" / "venue"
COMPOSE = VENUE / "docker-compose.yml"
ENV_EXAMPLE = VENUE / "conductor.env.example"
BOX_EXAMPLE = VENUE / "box.env.example"
UNITS = VENUE / "systemd"
HELPERS = sorted((VENUE / "bin").glob("canopy-*"))
LIB_SCRIPTS = sorted(p for p in (VENUE / "lib").glob("*.sh") if p.name != "common.sh")  # run by systemd
SCRIPTS = [VENUE / "install.sh", ROOT / "scripts" / "release.sh", *HELPERS, *LIB_SCRIPTS]
BASH = shutil.which("bash")

# Made up per test run (a real Fernet key's shape), so nothing key-shaped sits in git.
TEST_KEY = Fernet.generate_key().decode()
TEST_AUTH = "ops:not-a-real-password-42"


def _active(text: str) -> dict[str, str]:
    """KEY=value lines that are not comments."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _documented(text: str) -> set[str]:
    """Every KEY=... the file shows, set or commented out."""
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", text, flags=re.MULTILINE))


def _good_env(tmp_path: Path, **changes: str | None) -> Path:
    values = _active(ENV_EXAMPLE.read_text())
    values.update(
        HUBSPACE_ACCOUNT_ALLOWLIST="canopy@example.invalid",
        CONDUCTOR_BASIC_AUTH=TEST_AUTH,
        CONDUCTOR_SECRET_KEY=TEST_KEY,
    )
    for key, value in changes.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    path = tmp_path / "etc" / "conductor.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    path.chmod(0o600)
    return path


def _run(args: list[str], env: dict[str, str] | None = None, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=60, env=env, check=False, **kw)


def _dry_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "CANOPY_ETC": str(tmp_path / "etc"),
        "CANOPY_ENV_FILE": str(tmp_path / "etc" / "conductor.env"),
        "CANOPY_BOX_FILE": str(tmp_path / "etc" / "box.env"),
        "CANOPY_HOME": str(tmp_path / "opt"),
        "CANOPY_STATE": str(tmp_path / "state"),
        "CANOPY_BACKUP_DIR": str(tmp_path / "backups"),
    }


# ---- docker-compose.yml ------------------------------------------------------------------------------------------
def test_compose_runs_one_always_restarting_conductor_with_the_volume_and_env_file():
    doc = yaml.safe_load(COMPOSE.read_text())
    assert list(doc["services"]) == ["conductor"], "exactly one Conductor (state lives in its memory)"
    svc = doc["services"]["conductor"]
    assert svc["restart"] == "always"
    assert svc["image"].startswith("canopy-conductor:${CONDUCTOR_TAG"), "an image pinned by release tag"
    assert svc["pull_policy"] == "never" and "build" not in svc
    assert svc["env_file"] == ["${CANOPY_ENV_FILE:-/etc/canopy/conductor.env}"]
    assert "canopy-data:/data" in svc["volumes"]
    assert doc["volumes"]["canopy-data"] == {"external": True, "name": "canopy-data"}, (
        "external, so no compose command can delete the sign-ins and the overlay"
    )
    assert "/run/canopy/timesync:/run/systemd/timesync:ro" in svc["volumes"]
    # only the kiosk (loopback) and the show LAN; never every interface
    assert svc["ports"] == [
        "127.0.0.1:8000:8000",
        "${CONDUCTOR_LAN_IP:?set CONDUCTOR_LAN_IP in /etc/canopy/box.env}:8000:8000",
    ]
    assert not any(str(p).startswith(("0.0.0.0", "8000")) for p in svc["ports"])
    # the healthcheck can't use curl (the image has none)
    test = " ".join(svc["healthcheck"]["test"])
    assert "urllib" in test and "/healthz" in test and "curl" not in test
    assert svc["logging"]["driver"] == "json-file" and {"max-size", "max-file"} <= set(
        svc["logging"]["options"]
    )
    assert not {"deploy", "scale"} & set(svc)


def test_compose_holds_no_secrets_and_no_forwarded_headers():
    svc = yaml.safe_load(COMPOSE.read_text())["services"]["conductor"]
    assert svc["environment"] == {"CONDUCTOR_DATA_DIR": "/data", "PORT": "8000"}
    text = COMPOSE.read_text()
    for word in ("CONDUCTOR_BASIC_AUTH", "CONDUCTOR_SECRET_KEY", "FORWARDED_ALLOW_IPS", "password"):
        assert word not in text


@pytest.mark.skipif(
    shutil.which("docker") is None or _run(["docker", "compose", "version"]).returncode != 0,
    reason="docker compose isn't installed here (CI has it)",
)
def test_docker_compose_config_accepts_the_file_and_insists_on_a_tag(tmp_path):
    env_file = _good_env(tmp_path)
    box = tmp_path / "box.env"
    box.write_text("CONDUCTOR_TAG=v2026.10.12-1\nCONDUCTOR_LAN_IP=192.0.2.10\n")
    base = ["docker", "compose", "--project-name", "canopy", "--file", str(COMPOSE)]
    env = {**os.environ, "CANOPY_ENV_FILE": str(env_file)}
    for key in ("CONDUCTOR_TAG", "CONDUCTOR_LAN_IP"):
        env.pop(key, None)
    ok = _run([*base, "--env-file", str(box), "config", "--quiet"], env=env)
    assert ok.returncode == 0, ok.stderr
    empty = tmp_path / "empty.env"
    empty.write_text("")
    missing = _run([*base, "--env-file", str(empty), "config", "--quiet"], env=env)
    assert missing.returncode != 0 and "CONDUCTOR_TAG" in missing.stderr


# ---- conductor.env.example ---------------------------------------------------------------------------------------
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
    read = _variables_the_code_reads()
    assert {"CONDUCTOR_MODE", "HUBSPACE_LIVE", "CONDUCTOR_SECRET_KEY", "CONDUCTOR_ACCOUNT_CONNECT"} <= read
    missing = read - _documented(ENV_EXAMPLE.read_text())
    assert not missing, f"document these in {ENV_EXAMPLE.name}: {sorted(missing)}"


def test_env_example_is_the_venue_configuration_with_empty_secrets():
    active = _active(ENV_EXAMPLE.read_text())
    assert active["CONDUCTOR_MODE"] == "live" and active["HUBSPACE_LIVE"] == "1"
    assert active["HUBSPACE_DEVICE_ALLOWLIST"] == "registry"
    assert active["CONDUCTOR_ENV"] == "venue" and active["CONDUCTOR_DATA_DIR"] == "/data"
    assert "HUBSPACE_ACCOUNT_ALLOWLIST" in active
    assert active["CONDUCTOR_BASIC_AUTH"] == "" and active["CONDUCTOR_SECRET_KEY"] == ""
    for never in ("FORWARDED_ALLOW_IPS", "CONDUCTOR_AUTH", "CONDUCTOR_ENGINE", "CONDUCTOR_SIM_SPEED", "PORT"):
        assert never not in active, f"{never} must stay commented out on the venue box"
    assert not any(k.startswith("RAILWAY_") for k in active)


def test_deploy_files_hold_nothing_secret_or_identifying():
    files = [p for p in (ROOT / "deploy").rglob("*") if p.is_file()] + [ROOT / "scripts" / "release.sh"]
    fernet = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}=(?![A-Za-z0-9_=-])")
    for path in files:
        text = path.read_text()
        assert check_clean(text) == [], path
        assert not fernet.search(text), f"{path} contains something shaped like a Fernet key"
        assert not re.search(r"^(CONDUCTOR_BASIC_AUTH|CONDUCTOR_SECRET_KEY)=\S", text, re.MULTILINE), path


def test_box_example_is_not_secret_and_matches_the_compose_variables():
    active = _active(BOX_EXAMPLE.read_text())
    assert {"CONDUCTOR_TAG", "CONDUCTOR_LAN_IP", "CANOPY_KIOSK_USER", "CANOPY_BACKUP_KEEP_DAYS"} <= set(
        active
    )
    assert re.fullmatch(r"v\d{4}\.\d{2}\.\d{2}-\d+", active["CONDUCTOR_TAG"])
    assert not {"CONDUCTOR_BASIC_AUTH", "CONDUCTOR_SECRET_KEY"} & set(active)


# ---- systemd units -----------------------------------------------------------------------------------------------
def _unit(name: str) -> dict[str, dict[str, list[str]]]:
    path = UNITS / name
    # A plain INI parser must accept it (systemd allows repeated keys, hence strict=False)...
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(path.read_text(), source=str(path))
    # ...and every key is kept, repeated ones included.
    sections: dict[str, dict[str, list[str]]] = {}
    current = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        assert current is not None and "=" in line, f"{name}: {raw!r}"
        key, value = line.split("=", 1)
        current.setdefault(key.strip(), []).append(value.strip())
    assert set(sections) == set(parser.sections())
    return sections


def test_conductor_unit_runs_compose_with_restart_always_and_a_preflight():
    u = _unit("canopy-conductor.service")
    assert "docker.service" in u["Unit"]["Requires"][0] and "network-online.target" in u["Unit"]["After"][0]
    assert u["Unit"]["StartLimitIntervalSec"] == ["0"]
    assert u["Unit"]["ConditionPathExists"] == ["!/var/lib/canopy/hold"], "break-glass hold keeps it stopped"
    svc = u["Service"]
    assert svc["Restart"] == ["always"] and svc["Type"] == ["simple"]
    assert svc["ExecStartPre"] == ["/opt/canopy/lib/preflight.sh"]
    [start], [stop] = svc["ExecStart"], svc["ExecStop"]
    for cmd in (start, stop):
        assert cmd.startswith("/usr/bin/docker compose --project-name canopy --env-file /etc/canopy/box.env")
        assert "--file /opt/canopy/docker-compose.yml" in cmd
    assert start.endswith("up --no-build --remove-orphans") and " -d" not in start, (
        "foreground, so systemd watches it"
    )
    assert " down " in stop and "-v" not in stop.split(), "never deletes the volume"
    assert u["Install"]["WantedBy"] == ["multi-user.target"]


def test_watchdog_and_backup_timers():
    wd, wd_timer = _unit("canopy-watchdog.service"), _unit("canopy-watchdog.timer")
    assert wd["Service"]["Type"] == ["oneshot"] and wd["Service"]["ExecStart"] == [
        "/opt/canopy/lib/watchdog.sh"
    ]
    assert wd_timer["Timer"]["OnUnitActiveSec"] == ["30s"] and wd_timer["Timer"]["Unit"] == [
        "canopy-watchdog.service"
    ]
    assert wd_timer["Install"]["WantedBy"] == ["timers.target"]
    bk, bk_timer = _unit("canopy-backup.service"), _unit("canopy-backup.timer")
    assert bk["Service"]["Type"] == ["oneshot"]
    assert bk["Service"]["ExecStart"] == ["/opt/canopy/bin/canopy-backup --nightly"]
    assert bk_timer["Timer"]["OnCalendar"] == ["*-*-* 04:30:00"] and bk_timer["Timer"]["Persistent"] == [
        "true"
    ]
    assert bk_timer["Install"]["WantedBy"] == ["timers.target"]


def test_kiosk_user_unit_runs_chromium_full_screen_with_a_persistent_profile():
    u = _unit("canopy-kiosk.service")
    svc = u["Service"]
    [start] = svc["ExecStart"]
    assert start.startswith("/snap/bin/chromium ")
    for flag in (
        "--kiosk",
        "--noerrdialogs",
        "--disable-session-crashed-bubble",
        "--disable-infobars",
        "--overscroll-history-navigation=0",
        "--disable-pinch",
        "--check-for-update-interval=31536000",
        "--user-data-dir=${CANOPY_KIOSK_PROFILE}",
        "--app=${CANOPY_KIOSK_URL}",
    ):
        assert flag in start.split(), flag
    assert "CANOPY_KIOSK_URL=http://localhost:8000/kiosk/" in svc["Environment"]
    profile = next(e for e in svc["Environment"] if e.startswith("CANOPY_KIOSK_PROFILE="))
    assert profile.startswith("CANOPY_KIOSK_PROFILE=%h/"), (
        "a persistent profile, so the enrollment cookie survives"
    )
    assert svc["ExecStartPre"] == ["/opt/canopy/lib/kiosk-prestart.sh"], "waits for /healthz"
    assert svc["Restart"] == ["always"] and svc["TimeoutStartSec"] == ["infinity"]
    assert u["Install"]["WantedBy"] == ["graphical-session.target"]


def test_every_unit_is_installed_by_the_installer():
    installer = (VENUE / "install.sh").read_text()
    for unit in UNITS.iterdir():
        assert unit.name in installer, f"install.sh never installs {unit.name}"


# ---- shell scripts -----------------------------------------------------------------------------------------------
needs_bash = pytest.mark.skipif(BASH is None, reason="bash isn't installed")


@needs_bash
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_shell_scripts_parse(script):
    result = _run([BASH, "-n", str(script)])
    assert result.returncode == 0, result.stderr
    assert script.read_text().startswith("#!/usr/bin/env bash\n")
    assert script.stat().st_mode & stat.S_IXUSR, f"{script.name} must be executable"


def test_common_library_is_sourced_not_run():
    common = VENUE / "lib" / "common.sh"
    assert _run([BASH or "bash", "-n", str(common)]).returncode == 0
    assert not common.stat().st_mode & stat.S_IXUSR


@needs_bash
@pytest.mark.parametrize(
    "script",
    [*HELPERS, *LIB_SCRIPTS, VENUE / "install.sh", ROOT / "scripts" / "release.sh"],
    ids=lambda p: p.name,
)
def test_every_script_explains_itself_with_help(script):
    result = _run([BASH, str(script), "--help"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), f"{script.name} --help printed nothing"
    if script.parent.name == "bin" or script.name in ("install.sh", "release.sh"):
        assert result.stdout.startswith("Usage: "), result.stdout[:80]


@needs_bash
def test_helpers_refuse_unknown_options_with_their_usage():
    for helper in HELPERS:
        bad = _run([BASH, str(helper), "--no-such-option"])
        assert bad.returncode == 2 and "Usage: " in bad.stderr, helper.name


# ---- install.sh (dry run) ----------------------------------------------------------------------------------------
@needs_bash
def test_installer_refuses_without_the_env_file(tmp_path):
    env = _dry_env(tmp_path)
    result = _run(
        [BASH, str(VENUE / "install.sh"), "--dry-run", "--lan-ip", "192.0.2.10", "--no-kiosk"], env=env
    )
    assert result.returncode != 0
    assert "conductor.env doesn't exist" in result.stdout and "never writes secrets" in result.stderr
    assert "would run" not in result.stdout, "it must stop before planning anything"
    assert not (tmp_path / "etc" / "box.env").exists() and not (tmp_path / "opt").exists()


@needs_bash
@pytest.mark.parametrize(
    ("changes", "mode", "expected"),
    [
        ({}, 0o644, "must be 0600"),
        ({"CONDUCTOR_MODE": "sim"}, 0o600, "CONDUCTOR_MODE must be live"),
        ({"HUBSPACE_DEVICE_ALLOWLIST": "abc"}, 0o600, "must be registry"),
        ({"CONDUCTOR_SECRET_KEY": "short"}, 0o600, "must be a Fernet key"),
        ({"CONDUCTOR_BASIC_AUTH": "ops"}, 0o600, "must be user:password"),
        ({"HUBSPACE_ACCOUNT_ALLOWLIST": ""}, 0o600, "HUBSPACE_ACCOUNT_ALLOWLIST must list"),
        ({"FORWARDED_ALLOW_IPS": "*"}, 0o600, "Remove FORWARDED_ALLOW_IPS"),
        ({"CONDUCTOR_ENV": "staging"}, 0o600, "CONDUCTOR_ENV must be venue"),
    ],
)
def test_installer_refuses_an_unsafe_env_file_without_echoing_secrets(tmp_path, changes, mode, expected):
    path = _good_env(tmp_path, **changes)
    path.chmod(mode)
    result = _run(
        [BASH, str(VENUE / "install.sh"), "--dry-run", "--lan-ip", "192.0.2.10", "--no-kiosk"],
        env=_dry_env(tmp_path),
    )
    assert result.returncode != 0 and expected in result.stdout, result.stdout
    output = result.stdout + result.stderr
    assert TEST_KEY not in output and TEST_AUTH.split(":", 1)[1] not in output


@needs_bash
def test_installer_dry_run_plans_everything_and_changes_nothing(tmp_path):
    _good_env(tmp_path)
    result = _run(
        [BASH, str(VENUE / "install.sh"), "--dry-run", "--lan-ip", "192.0.2.10", "--no-kiosk"],
        env=_dry_env(tmp_path),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "DRY RUN" in out and "Values not shown" in out
    for planned in (
        "systemctl enable canopy-conductor.service canopy-watchdog.timer canopy-backup.timer",
        "docker volume create canopy-data",
        "set_env_value",
        "/etc/tmpfiles.d/canopy.conf",
    ):
        assert planned in out, planned
    assert TEST_KEY not in out + result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["etc"] and [
        p.name for p in (tmp_path / "etc").iterdir()
    ] == ["conductor.env"], "a dry run writes nothing"


@needs_bash
def test_installer_insists_on_a_show_lan_address(tmp_path):
    _good_env(tmp_path)
    result = _run([BASH, str(VENUE / "install.sh"), "--dry-run", "--no-kiosk"], env=_dry_env(tmp_path))
    assert result.returncode != 0 and "--lan-ip" in result.stderr
    result = _run(
        [BASH, str(VENUE / "install.sh"), "--dry-run", "--no-kiosk", "--lan-ip", "0.0.0.0"],
        env=_dry_env(tmp_path),
    )
    assert result.returncode != 0


# ---- the watchdog, with stub systemctl / curl / timedatectl ------------------------------------------------------
def _stubs(tmp_path: Path, *, healthy: bool, active: bool = True, synced: bool = True) -> dict[str, str]:
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    calls = tmp_path / "calls.log"
    calls.touch()
    scripts = {
        "systemctl": f"""#!/usr/bin/env bash
echo "$*" >> {calls}
case "$1" in
is-active) exit {0 if active else 3} ;;
show) echo 1 ;;  # ActiveEnterTimestampMonotonic: started long ago (no start-up grace)
esac
exit 0
""",
        "curl": f"#!/usr/bin/env bash\nexit {0 if healthy else 7}\n",
        "timedatectl": f"#!/usr/bin/env bash\necho {'yes' if synced else 'no'}\n",
    }
    for name, body in scripts.items():
        (stub / name).write_text(body)
        (stub / name).chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{stub}:{os.environ['PATH']}",
        "CANOPY_STATE": str(tmp_path / "state"),
        "CANOPY_TIMESYNC_DIR": str(tmp_path / "timesync"),
    }


@needs_bash
def test_watchdog_restarts_the_conductor_after_three_failed_checks_in_a_row(tmp_path):
    env = _stubs(tmp_path, healthy=False)
    watchdog = [BASH, str(VENUE / "lib" / "watchdog.sh")]
    calls = tmp_path / "calls.log"
    for n in (1, 2):
        result = _run(watchdog, env=env)
        assert result.returncode == 0 and f"({n} of 3 before a restart)" in result.stdout
        assert "restart" not in calls.read_text()
    result = _run(watchdog, env=env)
    assert result.returncode == 0 and result.stdout.startswith("<3>Restarting the Conductor")
    assert "restart --no-block canopy-conductor.service" in calls.read_text()
    assert (
        "restarted the Conductor after 3 failed health checks"
        in (tmp_path / "state" / "watchdog.log").read_text()
    )
    assert (tmp_path / "state" / "watchdog.failures").read_text().strip() == "0"
    assert (tmp_path / "timesync" / "synchronized").exists(), "NTP synced → the checklist's flag file"


@needs_bash
def test_watchdog_stands_down_while_held_or_stopped_and_resets_when_healthy(tmp_path):
    watchdog = [BASH, str(VENUE / "lib" / "watchdog.sh")]
    env = _stubs(tmp_path, healthy=False, synced=False)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "hold").touch()  # break-glass
    for _ in range(4):
        assert _run(watchdog, env=env).returncode == 0
    assert "restart" not in (tmp_path / "calls.log").read_text()
    assert not (tmp_path / "timesync" / "synchronized").exists(), "not synced → no flag"
    (tmp_path / "state" / "hold").unlink()
    env = _stubs(tmp_path, healthy=False, active=False)  # stopped on purpose
    for _ in range(4):
        assert _run(watchdog, env=env).returncode == 0
    assert "restart" not in (tmp_path / "calls.log").read_text()
    env = _stubs(tmp_path, healthy=False)
    _run(watchdog, env=env)
    _run(watchdog, env=env)
    env = _stubs(tmp_path, healthy=True)
    result = _run(watchdog, env=env)
    assert "answers /healthz again" in result.stdout
    assert (tmp_path / "state" / "watchdog.failures").read_text().strip() == "0"


# ---- scripts/release.sh ------------------------------------------------------------------------------------------
@needs_bash
@pytest.mark.skipif(shutil.which("git") is None, reason="git isn't installed")
def test_release_dry_run_numbers_tags_by_day_and_changes_nothing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    _run(["git", "init", "-q", "-b", "main", str(repo)])
    (repo / "README.md").write_text("x\n")
    _run([*git, "add", "README.md"])
    assert _run([*git, "commit", "-qm", "first"]).returncode == 0
    release = [BASH, str(ROOT / "scripts" / "release.sh"), "--dry-run"]
    first = _run(release, cwd=repo)
    assert first.returncode == 0, first.stderr
    tag = re.search(r"would tag (v\d{4}\.\d{2}\.\d{2})-1\b", first.stdout)
    assert tag, first.stdout
    assert _run([*git, "tag", f"{tag.group(1)}-1"]).returncode == 0
    second = _run(release, cwd=repo)
    assert f"would tag {tag.group(1)}-2" in second.stdout
    assert _run([*git, "tag", "-l"]).stdout.split() == [f"{tag.group(1)}-1"], "a dry run makes no tag"
    (repo / "README.md").write_text("changed\n")
    dirty = _run(release, cwd=repo)
    assert dirty.returncode != 0 and "local changes" in dirty.stderr
