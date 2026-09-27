"""The Conductor app for the /ops and /visualizer e2e tests (`uvicorn tests_e2e.ops_server:app`).

Wraps the real app's lifespan: after it has built the services, installs the /ops log tail (idempotent) and — only
when no real canopy engine was registered at `services.extras["canopy"]` — the test-only FakeEngine from
tests/fake_engine.py, which drives the real executor on the simulator. Test-only; never shipped (the Dockerfile
copies conductor/, config/ and web/ only).
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from fake_engine import FakeEngine

from conductor import logring
from conductor.app import app

log = logging.getLogger("conductor.e2e")
_inner = app.router.lifespan_context


@asynccontextmanager
async def _lifespan(a):
    async with _inner(a) as inner_state:
        services = a.state.services
        a.state.services.extras.setdefault("logring", logring.install())
        if "canopy" not in services.extras:
            services.extras["canopy"] = FakeEngine(services)
            log.info("e2e: no canopy engine registered; using the test FakeEngine (SIMULATED)")
        log.info("e2e: Conductor ready on the simulator")
        yield inner_state


app.router.lifespan_context = _lifespan
