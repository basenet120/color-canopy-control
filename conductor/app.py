"""The Conductor's web app: services built in the lifespan, routers, and the static surfaces."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from conductor import __version__, redact
from conductor.auth import BasicAuthMiddleware, check_auth_configuration
from conductor.geometry import build_layout, load_canopy_config, pitch_violations
from conductor.routes import core

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger("conductor")


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_auth_configuration()
    redact.install()
    from conductor.services import build_services

    # Bad mode/gate settings or config refuse to start with a plain-English error (07 §1, §8).
    services = build_services()
    app.state.services = services
    for problem in services.problems:
        log.warning("Needs attention: %s", problem)
    await services.start()
    try:
        yield
    finally:
        await services.close()


app = FastAPI(title="Color Canopy Conductor", version=__version__, lifespan=lifespan)


def _resolve_principal(session_id: str):
    services = getattr(app.state, "services", None)
    return services.roles.principal(session_id) if services is not None else None


app.add_middleware(BasicAuthMiddleware, principal_resolver=_resolve_principal)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/api/version")
def version(request: Request) -> dict:
    services = getattr(request.app.state, "services", None)
    info = {
        "version": __version__,
        "mode": os.environ.get("CONDUCTOR_MODE", "sim"),
        "live": os.environ.get("HUBSPACE_LIVE") == "1",
        "simulated": True,
        "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "local"),
    }
    if services is not None:
        info.update(services.version())
    return info


@app.get("/api/canopy/layout")
def canopy_layout(scheme: str | None = None) -> dict:
    cfg = load_canopy_config()
    try:
        heads = build_layout(cfg, scheme)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    module = cfg["module"]
    modules = {
        (h.row, h.col): {"id": h.module, "row": h.row, "col": h.col, "orientation": h.orientation}
        for h in heads
    }
    return {
        "canopy": {
            "name": cfg["name"],
            "width_in": cfg["width_in"],
            "height_in": cfg["height_in"],
            "module_width_in": module["width_in"],
            "module_height_in": module["height_in"],
            "pitch_in": module["pitch_in"],
            "cable_path": cfg["cable_path"],
        },
        "scheme": scheme or cfg["orientation_scheme"],
        "schemes": sorted(cfg["orientations"]),
        "modules": list(modules.values()),
        "heads": [asdict(h) for h in heads],
        "pitch_violations": pitch_violations(heads, module["pitch_in"]),
    }


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/visualizer/")


app.include_router(core.router)

app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
app.mount("/visualizer", StaticFiles(directory=WEB_DIR / "visualizer", html=True), name="visualizer")
