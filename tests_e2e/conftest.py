"""Playwright end-to-end fixtures (make e2e; not part of make test — CI has no browser).

Starts the real Conductor with uvicorn in a subprocess on a free port, on the simulator with fast latency, a
temporary data dir and Basic auth (e2e:e2e), and drives it with Chromium from PLAYWRIGHT_BROWSERS_PATH. Fixture
identities only; traces off (07 §9). Nothing here touches the network beyond 127.0.0.1.
"""

from __future__ import annotations

import base64
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS = ROOT / "docs" / "screenshots"
USER, PASSWORD = "e2e", "e2e"
AUTH_HEADER = {"Authorization": "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()}
PHONE = {"width": 390, "height": 844}
TABLET = {"width": 1024, "height": 768}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_healthy(url: str, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"the Conductor exited early (code {proc.returncode})")
        try:
            with urllib.request.urlopen(f"{url}/healthz", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    raise RuntimeError("the Conductor didn't become healthy in time")


@pytest.fixture(scope="session")
def server(tmp_path_factory) -> Iterator[str]:
    port = _free_port()
    data = tmp_path_factory.mktemp("conductor-data")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("HUBSPACE_LIVE", "CONDUCTOR_MODE", "RAILWAY_ENVIRONMENT_NAME", "CONDUCTOR_AUTH")
    }
    env.update(
        CONDUCTOR_SIM_SPEED="fast",
        CONDUCTOR_DATA_DIR=str(data),
        CONDUCTOR_BASIC_AUTH=f"{USER}:{PASSWORD}",
        PYTHONPATH=str(ROOT),
    )
    url = f"http://127.0.0.1:{port}"
    with open(data / "uvicorn.log", "w") as log:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "conductor.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_healthy(url, proc)
            yield url
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        chromium = pw.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture
def context_factory(browser, server):
    contexts = []

    def make(viewport: dict = PHONE, **kw):
        ctx = browser.new_context(
            base_url=server,
            viewport=viewport,
            # A plain header (Network.setExtraHTTPHeaders) rather than http_credentials: Playwright implements
            # credentials with request interception, which can hold a pagehide sendBeacon until the context
            # closes. A real browser sends its cached Basic credentials with the beacon itself.
            extra_http_headers=AUTH_HEADER,
            device_scale_factor=kw.pop("device_scale_factor", 1),
            has_touch=kw.pop("has_touch", viewport["width"] < 800),
            is_mobile=kw.pop("is_mobile", False),
            **kw,
        )
        contexts.append(ctx)
        return ctx

    yield make
    for ctx in contexts:
        ctx.close()


@pytest.fixture
def api(server):
    """A tiny JSON client for checking server state from the test (ops credentials)."""
    import json

    def call(path: str, method: str = "GET", body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"{server}{path}",
            data=data,
            method=method,
            headers={**AUTH_HEADER, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read() or b"null")

    return call


@pytest.fixture(scope="session")
def screenshots() -> Path:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    return SCREENSHOTS
