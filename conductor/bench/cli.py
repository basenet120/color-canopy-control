"""`python -m conductor.bench` — bench jobs from a terminal (the same jobs as /ops/bench).

    python -m conductor.bench list                       jobs and devices (which run SIMULATED)
    python -m conductor.bench run gate_a --device bench-kit [--param k=v ...] [--json]
    python -m conductor.bench runs                       recent results
    python -m conductor.bench export <run_id>            the anonymized JSON to paste into a build session
    python -m conductor.bench run gate_c --local         try a job in-process on the simulator (no server)

It talks to the running Conductor (`--server`, default $CONDUCTOR_URL or http://localhost:8000) with the ops
Basic credential from $CONDUCTOR_BASIC_AUTH ("user:password"), or `--user NAME` and a password prompt that is
never echoed. It starts the run, prints each step as it happens and the verdict, asks the questions at the bench
and posts the answers. The CLI never opens its own Hubspace session (07 §3): the server owns the tokens.
Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

DEFAULT_SERVER = "http://localhost:8000"
POLL_S = 0.5
TIMEOUT_S = 20.0

# (method, url, headers, body) → (status, body bytes). Tests swap in an adapter.
Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


class CliError(RuntimeError):
    """Something to tell the person, then exit 1. Never contains credentials."""


def urllib_transport(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(
            request, timeout=TIMEOUT_S
        ) as response:  # http(s) only: BenchClient checks
            return response.status, response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise CliError(f"Can't reach the Conductor at {url.split('/api/')[0]} ({reason}).") from None


class BenchClient:
    """The /api/bench/* endpoints over HTTP with the ops Basic credential."""

    def __init__(self, server: str, auth: tuple[str, str] | None, transport: Transport = urllib_transport):
        if not server.startswith(("http://", "https://")):
            raise CliError(f"--server must start with http:// or https:// (got {server!r}).")
        self.server = server.rstrip("/")
        self.transport = transport
        self._headers = {"Accept": "application/json"}
        if auth is not None:
            token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
            self._headers["Authorization"] = f"Basic {token}"

    def call(self, method: str, path: str, body: Any = None) -> Any:
        headers = dict(self._headers)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        status, raw = self.transport(method, self.server + path, headers, data)
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        if not 200 <= status < 300:
            raise CliError(_describe(status, parsed))
        return parsed

    def jobs(self) -> dict:
        return self.call("GET", "/api/bench/jobs")

    def start(self, job: str, device: str, params: dict[str, str]) -> dict:
        return self.call("POST", "/api/bench/run", {"job": job, "device_id": device, "params": params})

    def get(self, run_id: str) -> dict:
        return self.call("GET", f"/api/bench/runs/{run_id}")

    def runs(self) -> dict:
        return self.call("GET", "/api/bench/runs")

    def anonymized(self, run_id: str) -> dict:
        return self.call("GET", f"/api/bench/runs/{run_id}/anonymized")

    def answer(self, run_id: str, answers: dict[str, str]) -> dict:
        return self.call("POST", f"/api/bench/runs/{run_id}/answers", {"answers": answers})

    def apply_flags(self, run_id: str) -> dict:
        return self.call("POST", f"/api/bench/runs/{run_id}/flags", {})


def _describe(status: int, body: Any) -> str:
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and detail:
        return detail
    if isinstance(detail, list) and detail:
        return "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
    if status == 401:
        return "The Conductor wants the ops password: set CONDUCTOR_BASIC_AUTH or pass --user."
    if status == 403:
        return "Only ops can run bench jobs."
    return f"The Conductor answered HTTP {status}."


# ---- arguments -------------------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m conductor.bench", description="Run bench jobs.")
    parser.add_argument("--server", default=os.environ.get("CONDUCTOR_URL", DEFAULT_SERVER))
    parser.add_argument("--user", help="ops user name (asks for the password; else $CONDUCTOR_BASIC_AUTH)")
    parser.add_argument("--json", action="store_true", help="print JSON only (no questions)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="jobs and devices")
    run = sub.add_parser("run", help="run a job and follow it")
    run.add_argument("job")
    run.add_argument("--device", default="bench-kit")
    run.add_argument("--param", action="append", default=[], metavar="K=V", help="job option (repeatable)")
    run.add_argument("--local", action="store_true", help="run in-process on the simulator, no server")
    run.add_argument("--data", help="with --local: where to save the result (default: a temporary folder)")
    run.add_argument("--no-questions", action="store_true", help="don't ask the bench questions")
    sub.add_parser("runs", help="recent results")
    export = sub.add_parser("export", help="print a run's anonymized JSON")
    export.add_argument("run_id")
    return parser


def parse_params(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise CliError(f"--param needs name=value (got {item!r}).")
        out[key.strip()] = value.strip()
    return out


def credentials(user: str | None, env: dict[str, str], ask: Callable[[str], str]) -> tuple[str, str] | None:
    if user:
        return user, ask(f"Ops password for {user}: ")
    raw = env.get("CONDUCTOR_BASIC_AUTH", "")
    if raw:
        name, sep, password = raw.partition(":")
        if not sep:
            raise CliError("CONDUCTOR_BASIC_AUTH must look like user:password.")
        return name, password
    return None


# ---- output ----------------------------------------------------------------------------------------------------
class Printer:
    def __init__(self, out: TextIO, as_json: bool):
        self.out = out
        self.as_json = as_json

    def line(self, text: str = "") -> None:
        if not self.as_json:
            print(text, file=self.out, flush=True)

    def data(self, obj: Any) -> None:
        print(json.dumps(obj, indent=2, default=str), file=self.out, flush=True)

    def step(self, step: dict) -> None:
        self.line(f"  [{step.get('elapsed_s', 0):7.2f} s] {step.get('message', '')}")

    def result(self, record: dict) -> None:
        result = record.get("result") or {}
        tag = " (SIMULATED)" if record.get("simulated") else ""
        self.line()
        self.line(f"Verdict: {str(result.get('verdict', '?')).upper()}{tag}")
        self.line(f"  {result.get('summary', '')}")
        for key, value in (result.get("observations") or {}).items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                self.line(f"  {key}: {value}")
            elif isinstance(value, list) and all(isinstance(v, (str, int, float)) for v in value):
                self.line(f"  {key}: {', '.join(str(v) for v in value) or '–'}")
        if result.get("flags"):
            self.line(f"  proposed flags: {result['flags']}")
        self.line(f"  run id: {record.get('run_id')}")


def _ask_questions(record: dict, ask: Callable[[str], str]) -> dict[str, str]:
    answers: dict[str, str] = {}
    for question in (record.get("result") or {}).get("ask_human") or []:
        reply = ask(f"{question} [y/n/or type a note, Enter to skip] ").strip()
        if not reply:
            continue
        answers[question] = {"y": "yes", "yes": "yes", "n": "no", "no": "no"}.get(reply.lower(), reply)
    return answers


# ---- commands --------------------------------------------------------------------------------------------------
def cmd_list(client: BenchClient, p: Printer) -> None:
    data = client.jobs()
    if p.as_json:
        p.data(data)
        return
    p.line("Devices:")
    for d in data.get("devices", []):
        where = "SIMULATED" if d.get("simulated") else (d.get("driver") or "not available")
        lock = f" · in use by {d['locked_by']}" if d.get("locked_by") else ""
        p.line(f"  {d['device_id']:<16} {where}{lock}")
    p.line("Jobs:")
    for j in data.get("jobs", []):
        opts = " ".join(f"{k}={v}" for k, v in (j.get("params") or {}).items())
        p.line(f"  {j['name']:<17} {j['title']}" + (f"   [{opts}]" if opts else ""))


def cmd_run(
    client: BenchClient,
    p: Printer,
    job: str,
    device: str,
    params: dict[str, str],
    ask: Callable[[str], str] | None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    started = client.start(job, device, params)
    run_id = started["run_id"]
    if started.get("simulated"):
        p.line("SIMULATED: this runs on the simulator. Nothing here touches the real kit.")
    p.line(f"Started {job} on {device} (run {run_id})")
    seen = 0
    try:
        while True:
            record = client.get(run_id)
            steps = record.get("steps") or []
            for step in steps[seen:]:
                p.step(step)
            seen = len(steps)
            if record.get("status") != "running":
                break
            sleep(POLL_S)
    except KeyboardInterrupt:
        raise CliError(
            f"Stopped following. The run continues on the server: `export {run_id}` later."
        ) from None
    p.result(record)
    if ask is not None:
        answers = _ask_questions(record, ask)
        if answers:
            record = client.answer(run_id, answers)
            p.line(
                f"Answers saved. Verdict after your answers: {record.get('confirmed_verdict') or 'pending'}"
            )
        flags = (record.get("result") or {}).get("flags")
        offer = bool(flags) and not record.get("simulated")
        if offer and ask(f"Apply {flags} to this device's registry entry? [y/N] ").strip().lower() in (
            "y",
            "yes",
        ):
            try:
                client.apply_flags(run_id)
                p.line("Flags applied.")
            except CliError as e:
                p.line(f"Flags not applied: {e}")
    if p.as_json:
        p.data(record)
    return record


def cmd_runs(client: BenchClient, p: Printer) -> None:
    data = client.runs()
    if p.as_json:
        p.data(data)
        return
    for r in data.get("runs", []):
        sim = " SIM" if r.get("simulated") else ""
        verdict = r.get("confirmed_verdict") or r.get("verdict") or r.get("status")
        p.line(f"{r['run_id']:<44} {r.get('device_id', ''):<14} {verdict}{sim}")


def run_local(
    job: str,
    device: str,
    params: dict[str, str],
    data: str | None,
    p: Printer,
    ask: Callable[[str], str] | None,
    env: dict[str, str] | None = None,
) -> dict:
    """Run a job in-process on the SimDriver (virtual time: no waiting, no network, no server)."""
    from conductor.bench.runner import BenchError, BenchRunner
    from conductor.clock import VirtualClock, run_virtual
    from conductor.services import build_services

    folder = data or tempfile.mkdtemp(prefix="bench-local-")
    local_env = {
        k: v for k, v in (env if env is not None else os.environ).items() if not k.startswith("HUBSPACE_")
    }
    local_env.update({"CONDUCTOR_MODE": "sim", "CONDUCTOR_DATA_DIR": folder})

    async def main() -> dict:
        services = build_services(local_env, clock=VirtualClock(epoch=time.time()))
        try:
            runner = BenchRunner(services)
            try:
                record = await runner.run(job, device, params)
            except BenchError as e:
                raise CliError(str(e)) from None
            p.line("SIMULATED (--local): nothing here touches the real kit.")
            for step in record["steps"]:
                p.step(step)
            p.result(record)
            if ask is not None:
                answers = _ask_questions(record, ask)
                if answers:
                    record = runner.answer(record["run_id"], answers)
                    p.line(f"Verdict after your answers: {record.get('confirmed_verdict') or 'pending'}")
            return record
        finally:
            await services.close()

    record = run_virtual(main)
    p.line(f"Saved under {Path(folder) / 'bench'}")
    if p.as_json:
        p.data(record)
    return record


def main(
    argv: list[str] | None = None,
    *,
    out: TextIO | None = None,
    transport: Transport = urllib_transport,
    env: dict[str, str] | None = None,
    ask: Callable[[str], str] | None = None,
    ask_secret: Callable[[str], str] = getpass.getpass,
    interactive: bool | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    out = out or sys.stdout
    env = dict(os.environ) if env is None else env
    args = build_parser().parse_args(argv)
    p = Printer(out, args.json)
    if interactive is None:
        interactive = sys.stdin.isatty()
    questions = ask or input
    try:
        if args.command == "run":
            params = parse_params(args.param)
            wants_questions = interactive and not args.json and not args.no_questions
            asker = questions if wants_questions else None
            if args.local:
                run_local(args.job, args.device, params, args.data, p, asker, env)
                return 0
        client = BenchClient(args.server, credentials(args.user, env, ask_secret), transport)
        if args.command == "list":
            cmd_list(client, p)
        elif args.command == "run":
            cmd_run(client, p, args.job, args.device, params, asker, sleep)
        elif args.command == "runs":
            cmd_runs(client, p)
        elif args.command == "export":
            p.data(client.anonymized(args.run_id))
    except CliError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
