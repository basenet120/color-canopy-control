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

from conductor.design_lab.draft import ConfigTrio
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
        from conductor.looks.live import live_looks

        self.services = services
        config_dir = Path(services.config_dir)
        # One git per process (D68): the approved trio, its sha and the canopy are the ones the running show loaded
        # and validated at start (LiveLooks.git), never config/ re-read when the Lab is first opened — a deploy's
        # `git checkout` before its restart would otherwise give the Lab (and Revert) a git the show never checked.
        looks = live_looks(services)
        self.approved: ConfigTrio = looks.git.copy()
        self.config_sha: str = looks.git_sha or config_sha(config_dir)  # what `approved` was read from
        self.canopy: dict = (
            looks.canopy if looks.canopy is not None else load_canopy_config(config_dir / "canopy.yaml")
        )
        self.store = LookStore(Path(services.data), clock=services.clock, telemetry=services.telemetry)
        self.store.load()  # never raises (§3.5)
        # (generation, scene id) → the live look's seed draft (D68): read off the live looks once per publish
        self._live_seeds: dict[tuple[int, str], Any] = {}

    def live_seed(self, scene_id: str) -> Any:
        """The SceneDraft that reproduces the scene as the show runs it now (the live looks: approved or published).
        Keyed by the install generation read BEFORE the trio, so a seed of the old trio is never served after a
        publish (threadpool readers may race one)."""
        from conductor.design_lab.draft import seed_draft
        from conductor.looks.live import live_looks

        looks = live_looks(self.services)
        key = (looks.generation, scene_id)
        seed = self._live_seeds.get(key)
        if seed is None:
            seed = self._live_seeds[key] = seed_draft(looks.trio, scene_id)
        return seed

    def live_changed(self) -> None:
        """A publish installed new live looks (conductor.looks.live.install_looks): drop what came from the old."""
        self._live_seeds = {}

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
