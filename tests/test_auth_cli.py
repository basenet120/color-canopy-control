"""`python -m conductor.auth` (07 §3): the CLI talks to the running server's JSON API, prompts for the emailed code
(re-prompting when it's refused), and never prints the password. urllib is replaced by fakes — or bridged to the real
app on the simulator — so nothing touches the network."""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from conductor import auth_cli

ROOT = Path(__file__).resolve().parent.parent
PASSWORD = "PW-SENTINEL-cli-83f1"
OPS_PASSWORD = "OPS-SENTINEL-55aa"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeServer:
    """Scripted answers per path; records every request (method, path, headers, parsed JSON body)."""

    def __init__(self, answers: dict[str, list]):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.requests: list[dict] = []

    def __call__(self, request, timeout=None):
        path = request.full_url.removeprefix("http://127.0.0.1:8000")
        body = json.loads(request.data) if request.data else None
        self.requests.append(
            {
                "method": request.get_method(),
                "path": path,
                "headers": dict(request.header_items()),
                "body": body,
            }
        )
        answer = self.answers[path].pop(0)
        if isinstance(answer, tuple):
            status, payload = answer
            raise urllib.error.HTTPError(
                request.full_url, status, "error", {}, io.BytesIO(json.dumps(payload).encode())
            )
        return FakeResponse(json.dumps(answer).encode())


def _prompts(*answers):
    queue = list(answers)
    asked = []

    def ask(prompt=""):
        asked.append(prompt)
        return queue.pop(0)

    return ask, asked


def _run(argv, server, *, inputs=(), secrets=(PASSWORD,), env=None):
    ask, asked = _prompts(*inputs)
    ask_secret, secret_prompts = _prompts(*secrets)
    out, err = io.StringIO(), io.StringIO()
    code = auth_cli.run(argv, env=env or {}, ask=ask, ask_secret=ask_secret, opener=server, out=out, err=err)
    return code, out.getvalue(), err.getvalue(), asked, secret_prompts


def test_login_with_emailed_code_reprompts_and_never_prints_the_password():
    server = FakeServer(
        {
            "/api/accounts/login": [{"state": "otp_required", "nonce": "n1", "message": "Code emailed."}],
            "/api/accounts/otp": [
                {"state": "otp_required", "nonce": "n1", "message": "Hubspace didn't accept that code."},
                {"state": "connected", "message": "Connected to Hubspace.", "account": "bench"},
            ],
        }
    )
    code, out, err, asked, secret_prompts = _run(
        ["login", "--account", "bench"], server, inputs=("bench@example.invalid", "111111", "222222")
    )
    assert code == 0, err
    assert "Connected to Hubspace." in out and "didn't accept" in out
    assert asked == ["Hubspace email: ", "Code from Hubspace's email: ", "Code from Hubspace's email: "]
    assert secret_prompts == ["Hubspace password (not shown): "]
    login, otp1, otp2 = server.requests
    assert login["method"] == "POST" and login["headers"]["Content-type"] == "application/json"
    assert login["body"] == {"account": "bench", "email": "bench@example.invalid", "password": PASSWORD}
    assert otp1["body"] == {"nonce": "n1", "code": "111111"} and otp2["body"]["code"] == "222222"
    assert "Authorization" not in login["headers"]  # no credential configured → none sent
    assert PASSWORD not in out + err


def test_ops_credential_from_env_or_user_prompt():
    answer = {"/api/accounts": [{"mode": "sim", "simulated": True, "accounts": []}]}
    server = FakeServer(answer)
    code, out, err, _, _ = _run(["status"], server, env={"CONDUCTOR_BASIC_AUTH": f"crew:{OPS_PASSWORD}"})
    expected = "Basic " + base64.b64encode(f"crew:{OPS_PASSWORD}".encode()).decode()
    assert code == 0 and server.requests[0]["headers"]["Authorization"] == expected
    assert "[SIMULATED]" in out and OPS_PASSWORD not in out + err

    server = FakeServer(answer)
    code, out, err, _, secret_prompts = _run(["status", "--user", "alon"], server, secrets=(OPS_PASSWORD,))
    assert code == 0 and secret_prompts == ["Ops password for alon (not shown): "]
    assert (
        server.requests[0]["headers"]["Authorization"]
        == "Basic " + base64.b64encode(f"alon:{OPS_PASSWORD}".encode()).decode()
    )
    assert OPS_PASSWORD not in out + err


def test_failures_print_plain_messages_and_exit_nonzero():
    server = FakeServer(
        {
            "/api/accounts/login": [
                {"state": "failed", "message": "Hubspace didn't accept that email and password."}
            ]
        }
    )
    code, out, err, _, _ = _run(["login"], server, inputs=("bench@example.invalid",))
    assert code == 1 and "didn't accept" in err and PASSWORD not in out + err

    server = FakeServer(
        {"/api/accounts/login": [(403, {"detail": "That account is not in HUBSPACE_ACCOUNT_ALLOWLIST"})]}
    )
    code, out, err, _, _ = _run(["login"], server, inputs=("bench@example.invalid",))
    assert code == 1 and "HTTP 403" in err and "ALLOWLIST" in err and PASSWORD not in out + err

    server = FakeServer({"/api/accounts": [(401, {})]})
    code, _, err, _, _ = _run(["status"], server)
    assert code == 1 and "CONDUCTOR_BASIC_AUTH" in err

    def unreachable(request, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError(111, "refused"))

    code, _, err, _, _ = _run(["login"], unreachable, inputs=("bench@example.invalid",))
    assert code == 2 and "Can't reach the Conductor" in err and PASSWORD not in err

    code, _, err, _, _ = _run(["login"], FakeServer({}), inputs=("",))
    assert code == 1 and "No email" in err

    code, _, err, _, _ = _run(["status", "--server", "ftp://x"], FakeServer({}))
    assert code == 2 and "--server" in err


def test_devices_command():
    server = FakeServer(
        {
            "/api/accounts/bench/devices": [
                {
                    "simulated": False,
                    "devices": [
                        {
                            "metadevice_id": "md-1",
                            "name": "Kit",
                            "model": "SQHDK03",
                            "registered_as": "bench-kit",
                        },
                        {"metadevice_id": "md-2", "name": "Other", "registered_as": None, "read_only": True},
                    ],
                    "note": "Unregistered devices stay read-only.",
                }
            ]
        }
    )
    code, out, _, _, _ = _run(["devices"], server)
    assert code == 0 and "registered as bench-kit" in out and "not registered, read-only" in out


def test_cli_against_the_running_app_on_the_simulator(app_client, capsys):
    """The CLI's requests, bridged into the real app: simulated sign-in with the emailed-code step."""

    def bridge(request, timeout=None):
        path = request.full_url.removeprefix("http://127.0.0.1:8000")
        r = app_client.request(
            request.get_method(), path, content=request.data, headers=dict(request.header_items())
        )
        if r.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, r.status_code, "error", {}, io.BytesIO(r.content))
        return FakeResponse(r.content)

    ask, _ = _prompts("bench@example.invalid", "123456", "000000")
    ask_secret, _ = _prompts("needs-code")
    code = auth_cli.run(["login"], env={}, ask=ask, ask_secret=ask_secret, opener=bridge)
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "connected (simulated)" in captured.out and "[SIMULATED]" in captured.out
    assert "needs-code" not in captured.out + captured.err
    code = auth_cli.run(["status"], env={}, opener=bridge)
    assert code == 0 and "bench: connected" in capsys.readouterr().out


def test_python_dash_m_conductor_auth_runs_the_cli():
    result = subprocess.run(
        [sys.executable, "-m", "conductor.auth", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0 and "running Conductor" in result.stdout


@pytest.mark.parametrize("argv", [["login"], ["status"], ["devices"]])
def test_keyboard_interrupt_is_a_clean_cancel(argv):
    def interrupted(prompt=""):
        raise KeyboardInterrupt

    def opener(request, timeout=None):
        raise KeyboardInterrupt

    err = io.StringIO()
    code = auth_cli.run(
        argv, env={}, ask=interrupted, ask_secret=interrupted, opener=opener, out=io.StringIO(), err=err
    )
    assert code == 130 and "Cancelled" in err.getvalue()
