"""Helpers for the live Lab's tests (D68, docs/live-lab/PLAN.md §5.2): the show on virtual time with its live looks
built in the lifespan's order, a small ASGI app over its services, a saved Lab version, and a publish through the
real routes (409 confirm_required, then the confirmed POST).

The harness follows tests/test_lab_trial.py's `show` (build_services + ShowWiring over the shipped config/, the
simulator on the virtual clock, its own temporary data dir), with `build_live_looks` between them as app.py does.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any

import anyio.to_thread
import httpx
import yaml
from lab_edits import other_hex

from conductor.clock import VirtualClock
from conductor.config import CONFIG_DIR
from conductor.design_lab import draft as D
from conductor.design_lab import service as SVC
from conductor.looks import live as L
from conductor.sim.settings import SimSettings

QUIET_S = 70.0  # start-up (capability reads, simulator commissioning, the idle look) is over
GOLDEN = "golden-hour"
CALM = "lowcountry-calm"
TIDES = "coastal-tides"
DEV = "canopy-r0c0"
GIT = L.git_trio(CONFIG_DIR)
PINKER = "Pinker Hour"
# The Done-means pink (#FF8FBF), unless config/ already approves it (after `make lab-roundtrip` applies an export, or
# once Alon approves that edit into git): an edit must differ from the hex it replaces (tests/lab_edits.py).
NEW_PINK = other_hex(GIT.scenes.get(GOLDEN).palette[2].hex)


def seed(scene_id: str, trio: L.ConfigTrio = GIT) -> dict:
    return D.seed_draft(trio, scene_id).model_dump(mode="json")


def edited(scene_id: str, **changes: Any) -> dict:
    """The approved scene's seed draft (JSON) with dotted-path changes."""
    data = seed(scene_id)
    for key, value in changes.items():
        parts = key.split(".")
        target = data
        for p in parts[:-1]:
            target = target[int(p)] if p.isdigit() else target[p]
        target[parts[-1]] = value
    return D.SceneDraft.model_validate(data).model_dump(mode="json")


def pinker_hour() -> dict:
    """The checklist edit (§5.2): Golden Hour renamed "Pinker Hour", Sunset pink → #FF8FBF, a layout change."""
    data = seed(GOLDEN)
    assert data["palette"][2]["hex"].upper() != NEW_PINK
    angle = 45 if data["layout"] == {"kind": "linear", "angle_deg": 30} else 30
    return edited(
        GOLDEN,
        name=PINKER,
        **{"palette.2.hex": NEW_PINK, "layout": {"kind": "linear", "angle_deg": angle}},
    )


def brighter(scene_id: str) -> dict:
    level = seed(scene_id)["brightness"]
    return edited(scene_id, brightness=level - 10 if level > 50 else level + 10)


class Show:
    def __init__(self, services, wiring, engine, lab, data: Path, owned: bool):
        self.services, self.wiring, self.engine, self.lab, self.data = services, wiring, engine, lab, data
        self.clock = services.clock
        self.owned = owned

    @property
    def looks(self) -> L.LiveLooks:
        return self.services.extras[L.LOOKS_KEY]

    @property
    def overlay(self) -> Path:
        return self.data / "overrides" / L.LOOKS_FILE

    def events(self, kind: str) -> list[dict]:
        return [e for e in self.services.telemetry.ring if e["kind"] == kind]

    def commands(self) -> list[dict]:
        return self.events("command")

    async def run_for(self, seconds: float) -> None:
        await self.clock.sleep(seconds)

    def alerts(self) -> list[Any]:
        from conductor.alerts import AlertBoard

        return AlertBoard.of(self.services).active()

    async def close(self) -> None:
        await self.wiring.close()
        await self.services.close()
        if self.owned:
            shutil.rmtree(self.data, ignore_errors=True)


async def show(
    *,
    live: bool = False,
    monkeypatch=None,
    data: Path | None = None,
    prepare=None,
    quiet: bool = True,
    settings=None,
    config_dir: Path | None = None,
) -> Show:
    """The show on virtual time, its live looks built before ShowWiring (the lifespan's order). `live`: the show
    box's tier rules over the simulator's transport (no automatic commissioning, no bench flags)."""
    from conductor.alerts import AlertBoard
    from conductor.services import build_services
    from conductor.show import ShowWiring

    owned = data is None
    data = data or Path(tempfile.mkdtemp(prefix="canopy-live-lab-"))
    sim = SimSettings.from_acceptance().with_(throttle_429_rate=0.0, silent_drop_rate=0.0)
    services = build_services(
        {"CONDUCTOR_DATA_DIR": str(data)},
        clock=VirtualClock(),
        config_dir=config_dir or CONFIG_DIR,
        sim_settings=sim,
    )
    if settings is not None:
        services.settings = settings
    services.telemetry.dir = None
    AlertBoard.of(services)
    if live:
        monkeypatch.setattr(services.engine_driver, "simulated", False)
    await services.start()
    if prepare is not None:
        prepare(services)
    L.build_live_looks(services)
    wiring = ShowWiring(services)
    services.extras["show"] = wiring
    engine = await wiring.start()
    lab = SVC.LabService(services)
    services.extras[SVC.KEY] = lab
    w = Show(services, wiring, engine, lab, data, owned)
    if quiet:
        await w.run_for(QUIET_S)
    return w


# ---- routes ---------------------------------------------------------------------------------------------------------
def lab_app(services):
    from fastapi import FastAPI

    from conductor.routes import design, design_lab, guest, kiosk, ops

    app = FastAPI()
    for router in (design_lab.router, kiosk.router, guest.router, ops.router, design.router):
        app.include_router(router)
    app.state.services = services
    return app


def client_of(w: Show) -> httpx.AsyncClient:
    assert getattr(anyio.to_thread.run_sync, "inline", False), "a route test on virtual time needs no_threads"
    transport = httpx.ASGITransport(app=lab_app(w.services))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def install_no_threads(monkeypatch) -> None:
    """tests/test_lab_trial.py's `no_threads`: every threadpool hop runs inline on the loop (after one yield), and
    no real thread may start, so a request costs exactly the virtual time its own awaits take."""
    import threading

    async def inline(fn, *args, **_options):
        await asyncio.sleep(0)
        return fn(*args)

    inline.inline = True  # type: ignore[attr-defined]
    monkeypatch.setattr(anyio.to_thread, "run_sync", inline)

    def refuse(self, *a, **k):
        raise AssertionError(f"a real thread started on virtual time: {self!r}")

    monkeypatch.setattr(threading.Thread, "start", refuse)
    monkeypatch.delenv("CONDUCTOR_BASIC_AUTH", raising=False)


async def save(c: httpx.AsyncClient, drafts: dict[str, dict], note: str = "") -> str:
    r = await c.post("/api/lab/versions", json={"drafts": drafts, "note": note})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def live_id(c: httpx.AsyncClient) -> str:
    r = await c.get("/api/lab/live")
    assert r.status_code == 200, r.text
    current = r.json()["current"]
    return current["id"] if current is not None else "approved"


async def ask(c: httpx.AsyncClient, version: str, **extra: Any) -> httpx.Response:
    body = {
        "version": version,
        "expected_live": extra.pop("expected_live", None) or await live_id(c),
        **extra,
    }
    return await c.post("/api/lab/publish", json=body)


async def publish(c: httpx.AsyncClient, version: str, **extra: Any) -> httpx.Response:
    """POST without confirm (409 confirm_required, the sentence), then confirmed with it."""
    expected = extra.pop("expected_live", None) or await live_id(c)
    first = await ask(c, version, expected_live=expected, **extra)
    if first.status_code != 409 or not first.json().get("confirm_required"):
        return first
    return await ask(c, version, expected_live=expected, confirm=first.json()["confirm"], **extra)


# ---- overlay files ----------------------------------------------------------------------------------------------------
def publication_document(
    published: L.ConfigTrio, *, name: str = "Client Oct 22 v3 – warmer sunset", **over: Any
) -> dict:
    """A show-looks.yaml document whose `current` is `published`, as write_looks would write it (`over`: fields of
    `current` to replace, e.g. trio=None)."""
    trio = published
    current = {
        "id": "p0001",
        "kind": "version",
        "version": "v0001",
        "name": name,
        "published_at": 1792700000.0,
        "by": "Alon",
        "base_config_sha": L.config_sha(CONFIG_DIR),
        "trio_sha": L.trio_sha(trio),
        "changed": L.changed_scenes(GIT, trio),
        "drafts": {},
        "trio": trio.dump(),
    }
    current.update(over)
    return {
        "format": 1,
        "seq": 1,
        "current": current,
        "previous": None,
        "history": [
            {
                "id": "p0001",
                "kind": "version",
                "version": "v0001",
                "name": name,
                "published_at": 1792700000.0,
                "by": "Alon",
                "changed": list(current["changed"]),
                "repaint": None,
                "outcome": "live",
            }
        ],
        "repaints": {},
        "queued": {},
    }


def write_document(data: Path, document: Any) -> Path:
    path = data / "overrides" / L.LOOKS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        document
        if isinstance(document, str)
        else yaml.dump(document, Dumper=L._NoAliasDumper, sort_keys=False)
    )
    path.write_text(text, encoding="utf-8")
    return path


def patched_trio(*drafts: dict) -> L.ConfigTrio:
    from conductor.config import load_settings
    from conductor.geometry import load_canopy_config

    return D.apply_drafts(
        GIT,
        {d["scene_id"]: D.SceneDraft.model_validate(d) for d in drafts},
        canopy=load_canopy_config(CONFIG_DIR / "canopy.yaml"),
        settings=load_settings(CONFIG_DIR),
    )
