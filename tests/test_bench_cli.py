"""`python -m conductor.bench`: argument parsing, --local on the simulator, and the HTTP path against the real app."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

import pytest

from conductor.bench import cli
from conductor.bench.jobs import Q_GATE_C

ROOT = Path(__file__).resolve().parent.parent


def over(client):
    """A transport that sends the CLI's requests to a TestClient instead of the network."""

    def send(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        r = client.request(method, path, headers=headers, content=body)
        return r.status_code, r.content

    return send


def run_cli(argv, **kw) -> tuple[int, str]:
    out = io.StringIO()
    kw.setdefault("env", {})
    kw.setdefault("interactive", False)
    kw.setdefault("sleep", lambda s: time.sleep(0.02))
    code = cli.main(argv, out=out, **kw)
    return code, out.getvalue()


# ---- parsing -------------------------------------------------------------------------------------------------------
def test_argument_parsing():
    args = cli.build_parser().parse_args(
        ["--server", "http://box:8000", "run", "gate_b", "--device", "bench-kit", "--param", "slot=custom-3"]
    )
    assert (args.command, args.job, args.device, args.param) == (
        "run",
        "gate_b",
        "bench-kit",
        ["slot=custom-3"],
    )
    assert args.server == "http://box:8000" and not args.local and not args.json
    assert cli.build_parser().parse_args(["export", "abc"]).run_id == "abc"
    assert cli.parse_params(["n=4", "spacing_s = 2"]) == {"n": "4", "spacing_s": "2"}
    with pytest.raises(cli.CliError):
        cli.parse_params(["oops"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["frobnicate"])


def test_credentials_come_from_env_or_a_hidden_prompt():
    assert cli.credentials(None, {"CONDUCTOR_BASIC_AUTH": "crew:canopy"}, input) == ("crew", "canopy")
    prompts = []
    got = cli.credentials("crew", {}, lambda p: prompts.append(p) or "s3cret")
    assert got == ("crew", "s3cret") and prompts == ["Ops password for crew: "]
    assert cli.credentials(None, {}, input) is None
    with pytest.raises(cli.CliError):
        cli.credentials(None, {"CONDUCTOR_BASIC_AUTH": "no-colon"}, input)
    with pytest.raises(cli.CliError):
        cli.BenchClient("ftp://box", None)


def test_module_entry_point_prints_help():
    result = subprocess.run(
        [sys.executable, "-m", "conductor.bench", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and "list" in result.stdout and "export" in result.stdout


# ---- --local -------------------------------------------------------------------------------------------------------
def test_local_run_on_the_simulator(tmp_path):
    code, out = run_cli(["run", "gate_c", "--local", "--data", str(tmp_path)], env={"HUBSPACE_LIVE": "1"})
    assert code == 0
    assert "SIMULATED" in out and "Verdict: NEEDS_EYES (SIMULATED)" in out and "t1_frames" in out
    saved = list((tmp_path / "bench").glob("*.json"))
    assert len(saved) == 1 and json.loads(saved[0].read_text())["result"]["verdict"] == "needs_eyes"


def test_local_run_asks_the_questions(tmp_path):
    asked = []
    code, out = run_cli(
        ["run", "gate_c", "--local", "--data", str(tmp_path)],
        interactive=True,
        ask=lambda q: asked.append(q) or "y",
    )
    assert code == 0 and asked and asked[0].startswith(Q_GATE_C)
    assert "Verdict after your answers: pass" in out


def test_local_json_output(tmp_path):
    code, out = run_cli(["--json", "run", "gate_a", "--local", "--data", str(tmp_path)])
    assert code == 0
    record = json.loads(out)
    assert record["result"]["verdict"] == "pass" and record["simulated"] is True


def test_local_bad_job_is_a_plain_error(tmp_path, capsys):
    code, _ = run_cli(["run", "gate_z", "--local", "--data", str(tmp_path)])
    assert code == 1 and "no bench job" in capsys.readouterr().err


# ---- the HTTP path -------------------------------------------------------------------------------------------------
def test_http_list_run_runs_export(app_client):
    send = over(app_client)
    code, out = run_cli(["--server", "http://testserver", "list"], transport=send)
    assert code == 0 and "gate_a" in out and "bench-kit" in out and "SIMULATED" in out

    code, out = run_cli(["--server", "http://testserver", "run", "gate_a"], transport=send)
    assert code == 0
    assert "SIMULATED: this runs on the simulator" in out and "Verdict: PASS (SIMULATED)" in out
    assert "Read" in out or "Profile read" in out
    run_id = next(line.split("run id: ")[1] for line in out.splitlines() if "run id: " in line)

    code, out = run_cli(["--server", "http://testserver", "runs"], transport=send)
    assert code == 0 and run_id in out

    code, out = run_cli(["--server", "http://testserver", "export", run_id], transport=send)
    assert code == 0
    exported = json.loads(out)
    assert exported["run_id"] == run_id and "sim-bench-kit" not in out


def test_http_run_answers_questions(app_client):
    asked = []
    code, out = run_cli(
        ["--server", "http://testserver", "run", "gate_c"],
        transport=over(app_client),
        interactive=True,
        ask=lambda q: asked.append(q) or "yes",
    )
    assert code == 0 and asked[0].startswith(Q_GATE_C)
    assert "Verdict after your answers: pass" in out
    assert not any("Apply" in q for q in asked)  # never offered for a simulated run


def test_http_errors_are_plain(app_client, capsys):
    services = app_client.app.state.services
    lease = services.locks.acquire("sim-bench-kit", "module page · alon")
    try:
        code, _ = run_cli(["--server", "http://testserver", "run", "gate_a"], transport=over(app_client))
    finally:
        services.locks.release("sim-bench-kit", lease.token)
    assert code == 1 and "module page" in capsys.readouterr().err


def test_http_basic_auth(app_client_auth, capsys):
    send = over(app_client_auth)
    code, _ = run_cli(["--server", "http://testserver", "list"], transport=send)
    assert code == 1 and "ops password" in capsys.readouterr().err
    code, out = run_cli(
        ["--server", "http://testserver", "list"], transport=send, env={"CONDUCTOR_BASIC_AUTH": "crew:canopy"}
    )
    assert code == 0 and "gate_a" in out
    prompts = []
    code, out = run_cli(
        ["--server", "http://testserver", "--user", "crew", "list"],
        transport=send,
        ask_secret=lambda p: prompts.append(p) or "canopy",
    )
    assert code == 0 and prompts == ["Ops password for crew: "]
    assert "crew:canopy" not in out and "Basic " not in out and "Y3Jld" not in out


def test_unreachable_server_is_a_plain_error(monkeypatch, capsys):
    def refuse(*a, **kw):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(cli.urllib.request, "urlopen", refuse)
    code, _ = run_cli(["--server", "http://127.0.0.1:9", "list"])
    assert code == 1 and "Can't reach the Conductor" in capsys.readouterr().err
