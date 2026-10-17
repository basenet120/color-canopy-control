"""The Lab's per-process state (PLAN §3.0): the approved (git) trio, the canopy and the version store.

Built on first use, off the event loop (`run_in_threadpool`: ~37 ms of YAML parsing measured on the Mac mini), once
per process under a lock — never `extras.setdefault(KEY, LabService(...))`, which would build a throwaway service on
every request. It never touches the engine except to read it (Slice A). The corrupt-store alert is recorded on the
loop thread after construction returns.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from conductor.design_lab.draft import ConfigTrio, load_trio
from conductor.design_lab.store import LookStore, config_sha
from conductor.geometry import load_canopy_config
from conductor.routes.deps import services_of

KEY = "design_lab"  # services.extras key
LOCK_KEY = "design_lab:lock"  # the build lock, per Services (one event loop per app)
ENGINE_KEY = "canopy"  # = routes.ops.ENGINE_KEY
SHOW_KEY = "show"


class LabService:
    """Per-process Lab state: the approved (git) trio, the canopy, the store. Built on first use; never touches the
    engine except to read it (Slice A)."""

    def __init__(self, services: Any) -> None:
        self.services = services
        config_dir = Path(services.config_dir)
        self.approved: ConfigTrio = load_trio(config_dir)
        self.config_sha: str = config_sha(config_dir)  # what `approved` was read from
        self.canopy: dict = load_canopy_config(config_dir / "canopy.yaml")
        self.store = LookStore(Path(services.data), clock=services.clock, telemetry=services.telemetry)
        self.store.load()  # never raises (§3.5)

    def show(self) -> Any | None:
        return self.services.extras.get(SHOW_KEY)  # ShowWiring

    def engine(self) -> Any | None:
        return self.services.extras.get(ENGINE_KEY)  # CanopyEngine

    @property
    def settings(self) -> Any:
        return self.services.settings  # ConductorSettings

    @property
    def simulated(self) -> bool:
        return bool(self.services.engine_driver.simulated)


async def lab_of(request: Request) -> LabService:
    """The app's LabService, built once (get-or-create under a lock, construction in the threadpool)."""
    services = services_of(request)
    lab = services.extras.get(KEY)
    if lab is not None:
        return lab
    lock = services.extras.setdefault(LOCK_KEY, asyncio.Lock())
    async with lock:
        lab = services.extras.get(KEY)
        if lab is None:
            lab = await run_in_threadpool(LabService, services)
            services.extras[KEY] = lab
            _record_pending_alert(lab)
    return lab


def _record_pending_alert(lab: LabService) -> None:
    """The store's corrupt-file alert, on the loop thread: AlertBoard lists warn alerts on /ops "Needs attention"."""
    message = lab.store.pending_alert
    if message:
        lab.store.pending_alert = None
        lab.services.telemetry.record("alert", level="warn", message=message)
