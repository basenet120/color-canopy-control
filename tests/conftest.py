"""Shared fixtures. `app_client` runs the real app (lifespan included) on the SimDriver with fast simulated latency,
a temporary data dir and auth off (every request is ops). Use `app_client_auth` for Basic-auth behaviour."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/fake_*.py helpers


def _env(monkeypatch, tmp_path, **extra):
    for key in ("CONDUCTOR_BASIC_AUTH", "HUBSPACE_LIVE", "CONDUCTOR_MODE", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CONDUCTOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONDUCTOR_SIM_SPEED", "fast")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    from conductor.app import app

    _env(monkeypatch, tmp_path, CONDUCTOR_AUTH="off")
    with TestClient(app) as client:
        yield client


@pytest.fixture
def app_client_auth(monkeypatch, tmp_path):
    """Basic auth on (ops = crew:canopy). Pass auth=("crew", "canopy") for ops requests."""
    from conductor.app import app

    _env(monkeypatch, tmp_path, CONDUCTOR_BASIC_AUTH="crew:canopy")
    with TestClient(app) as client:
        yield client
