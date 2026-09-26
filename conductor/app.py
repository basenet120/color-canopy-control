"""The Conductor's web app. Walking skeleton: health, version, canopy layout, visualizer."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from conductor import __version__
from conductor.auth import BasicAuthMiddleware, check_auth_configuration
from conductor.geometry import build_layout, load_canopy_config, pitch_violations

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    check_auth_configuration()
    yield


app = FastAPI(title="Color Canopy Conductor", version=__version__, lifespan=lifespan)
app.add_middleware(BasicAuthMiddleware)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/api/version")
def version() -> dict:
    return {
        "version": __version__,
        "mode": os.environ.get("CONDUCTOR_MODE", "sim"),
        "live": os.environ.get("HUBSPACE_LIVE") == "1",
        "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "local"),
    }


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


app.mount("/visualizer", StaticFiles(directory=WEB_DIR / "visualizer", html=True), name="visualizer")
