"""`python -m conductor.auth` — connect a Hubspace account through the RUNNING Conductor (07 §3, D2).

The CLI never opens its own Afero session: one process owns an account's token, so this talks to the server's
JSON API (`/api/accounts/*`) with the ops credential, and the server does the sign-in.

    python -m conductor.auth login   [--server URL] [--account bench] [--user NAME]
    python -m conductor.auth status  [--server URL] [--user NAME]
    python -m conductor.auth devices [--server URL] [--account bench] [--user NAME]

The ops credential comes from CONDUCTOR_BASIC_AUTH ("user:password") or `--user` plus a hidden prompt; with
neither, no credential is sent (fine for a local server with auth off). The Hubspace password is read with
getpass (never shown), sent once in a POST body, and never printed or logged. When Hubspace emails a code, the CLI
asks for it and re-asks when the code isn't accepted. Uses urllib only.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any, NoReturn, TextIO
from urllib.parse import quote, urlsplit

DEFAULT_SERVER = "http://127.0.0.1:8000"
DEFAULT_ACCOUNT = "bench"
TIMEOUT_S = 60.0  # a sign-in can take up to ~30 s on the server
MAX_CODE_PROMPTS = 6


class CliError(Exception):
    """A problem to print as-is (plain English, never containing a secret), with an exit code."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.message = message
        self.code = code


class Client:
    """A tiny JSON client for the Conductor. Never includes request bodies in errors."""

    def __init__(self, server: str, authorization: str | None, *, opener: Any = None):
        self.server = server.rstrip("/")
        self.authorization = authorization
        self._open = opener or urllib.request.urlopen

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode()
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.authorization:
            headers["Authorization"] = self.authorization
        request = urllib.request.Request(self.server + path, data=data, headers=headers, method=method)
        try:
            with self._open(request, timeout=TIMEOUT_S) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise CliError(_http_error(exc)) from None
        except urllib.error.URLError as exc:
            raise CliError(
                f"Can't reach the Conductor at {self.server} ({_reason(exc)}). Is it running? Use --server to "
                "point at it.",
                2,
            ) from None
        except (TimeoutError, OSError) as exc:
            raise CliError(
                f"The Conductor at {self.server} didn't answer ({type(exc).__name__}).", 2
            ) from None
        try:
            parsed = json.loads(raw or b"{}")
        except ValueError:
            raise CliError("The Conductor sent something that isn't JSON. Check --server.") from None
        if not isinstance(parsed, dict):
            raise CliError("The Conductor sent an unexpected answer. Check --server.")
        return parsed


def _reason(exc: urllib.error.URLError) -> str:
    reason = exc.reason
    return type(reason).__name__ if isinstance(reason, BaseException) else str(reason)


def _http_error(exc: urllib.error.HTTPError) -> str:
    detail = ""
    try:
        body = json.loads(exc.read() or b"{}")
        if isinstance(body, dict):
            value = body.get("detail") or body.get("message")
            detail = value if isinstance(value, str) else ""
    except (ValueError, OSError):
        detail = ""
    if exc.code == 401:
        return (
            "The Conductor wants the ops password. Set CONDUCTOR_BASIC_AUTH=user:password, or run with --user NAME "
            "and type the password when asked."
        )
    if detail:
        return f"Refused (HTTP {exc.code}): {detail}"
    return f"Refused (HTTP {exc.code})."


def _authorization(args: argparse.Namespace, env: dict[str, str], ask_secret: Any) -> str | None:
    if args.user:
        password = ask_secret(f"Ops password for {args.user} (not shown): ")
        pair = f"{args.user}:{password}"
    elif env.get("CONDUCTOR_BASIC_AUTH"):
        pair = env["CONDUCTOR_BASIC_AUTH"]
    else:
        return None
    return "Basic " + base64.b64encode(pair.encode()).decode()


def _check_server(server: str, err: TextIO) -> None:
    parts = urlsplit(server)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise CliError("--server must look like http://127.0.0.1:8000 or https://conductor.example.org", 2)
    host = (parts.hostname or "").lower()
    if (
        parts.scheme == "http"
        and host not in ("127.0.0.1", "localhost", "::1")
        and not host.startswith("100.")
    ):
        print(
            "Warning: this server address is plain http, so passwords cross the network unencrypted. Prefer "
            "https or Tailscale.",
            file=err,
        )


def _sim(answer: dict) -> str:
    return " [SIMULATED]" if answer.get("simulated") else ""


# ---- commands ------------------------------------------------------------------------------------------------
def cmd_login(client: Client, account: str, *, ask: Any, ask_secret: Any, out: TextIO) -> int:
    print(f"Connecting the '{account}' account through the Conductor at {client.server}.", file=out)
    email = ask("Hubspace email: ").strip()
    if not email:
        raise CliError("No email entered; nothing was sent.")
    password = ask_secret("Hubspace password (not shown): ")
    if not password:
        raise CliError("No password entered; nothing was sent.")
    answer = client.call(
        "POST", "/api/accounts/login", {"account": account, "email": email, "password": password}
    )
    del password
    prompts = 0
    while answer.get("state") == "otp_required" and answer.get("nonce"):
        print(str(answer.get("message", "")) + _sim(answer), file=out)
        prompts += 1
        if prompts > MAX_CODE_PROMPTS:
            raise CliError("Too many tries. Start the sign-in again.")
        code = ask("Code from Hubspace's email: ").strip()
        answer = client.call("POST", "/api/accounts/otp", {"nonce": answer["nonce"], "code": code})
    message = str(answer.get("message", "")) + _sim(answer)
    if answer.get("state") == "connected":
        print(message, file=out)
        return 0
    raise CliError(message or "The sign-in didn't work.")


def cmd_status(client: Client, *, out: TextIO) -> int:
    answer = client.call("GET", "/api/accounts")
    tag = _sim(answer)
    print(f"Accounts on {client.server} (mode {answer.get('mode', '?')}){tag}:", file=out)
    for row in answer.get("accounts", []):
        name = row.get("account", "?")
        if row.get("needs_reauth"):
            state = "needs a new sign-in"
        elif row.get("connected"):
            age = row.get("token_age_s")
            state = "connected" + (f", token saved {_age(age)} ago" if age is not None else "")
        else:
            state = "not connected"
        if row.get("last_error") and not row.get("connected"):
            state += f" ({row['last_error']})"
        unknown = row.get("unknown_devices") or []
        if unknown:
            state += f"; {len(unknown)} unregistered device(s) visible (read-only)"
        print(f"  {name}: {state}", file=out)
    if not answer.get("connect_allowed", True):
        print(f"Sign-in isn't allowed here: {answer.get('connect_reason')}", file=out)
    return 0


def cmd_devices(client: Client, account: str, *, out: TextIO) -> int:
    answer = client.call("GET", f"/api/accounts/{quote(account, safe='')}/devices")
    print(f"Devices the '{account}' account can see{_sim(answer)}:", file=out)
    devices = answer.get("devices", [])
    if not devices:
        print("  (none)", file=out)
    for d in devices:
        where = f"registered as {d['registered_as']}" if d.get("registered_as") else "not registered"
        if d.get("read_only"):
            where += ", read-only"
        name = d.get("name") or "unnamed"
        model = f" ({d['model']})" if d.get("model") else ""
        print(f"  {name}{model}  id {d.get('metadevice_id')}  — {where}", file=out)
    if answer.get("note"):
        print(answer["note"], file=out)
    return 0


def _age(seconds: float) -> str:
    s = int(seconds)
    if s < 120:
        return f"{s} s"
    if s < 7200:
        return f"{s // 60} min"
    return f"{s // 3600} h"


# ---- entry points --------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m conductor.auth",
        description="Connect a Hubspace account through the running Conductor (the CLI never talks to "
        "Hubspace itself).",
    )
    parser.add_argument("command", nargs="?", default="login", choices=("login", "status", "devices"))
    parser.add_argument(
        "--server", default=DEFAULT_SERVER, help=f"Conductor address (default {DEFAULT_SERVER})"
    )
    parser.add_argument("--account", default=DEFAULT_ACCOUNT, help="registry account name (default bench)")
    parser.add_argument("--user", help="ops user name (asks for the password); else CONDUCTOR_BASIC_AUTH")
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    ask: Any = input,
    ask_secret: Any = getpass.getpass,
    opener: Any = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Run one command; returns the exit code. Every dependency is injectable for tests."""
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    env = dict(os.environ) if env is None else env
    try:
        _check_server(args.server, err)
        client = Client(args.server, _authorization(args, env, ask_secret), opener=opener)
        if args.command == "status":
            return cmd_status(client, out=out)
        if args.command == "devices":
            return cmd_devices(client, args.account, out=out)
        return cmd_login(client, args.account, ask=ask, ask_secret=ask_secret, out=out)
    except CliError as exc:
        print(exc.message, file=err)
        return exc.code
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. Nothing more was sent.", file=err)
        return 130


def main(argv: Sequence[str] | None = None) -> NoReturn:
    raise SystemExit(run(argv))
