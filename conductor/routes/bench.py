"""Bench routes (ops only): run Gates A–C and friends, read results, answer questions, apply flags (bench_api.py).

- `GET  /api/bench/jobs` — job specs, plus per device: simulated?, driver, who holds its lock.
- `POST /api/bench/run` `{job, device_id, params}` → `{run_id}`; progress on WebSocket topic "bench".
  409 with a plain message when the device is locked or already running a job.
- `GET  /api/bench/runs`, `GET /api/bench/runs/{id}` — saved results.
- `GET  /api/bench/runs/{id}/anonymized` — the copyable JSON for a build session.
- `GET  /api/bench/runs/{id}/profile.json` — Gate A's scrubbed profile (candidate simulator fixture).
- `POST /api/bench/runs/{id}/answers` `{answers: {question: "yes"|"no"|note}}`.
- `POST /api/bench/runs/{id}/flags` — apply the proposed DeviceFlags to the ops overlay. Refused (409) for simulated
  runs and until the matching question is answered "yes".
- `GET  /api/bench/time` — the server's clock in epoch ms (the clock page estimates its offset from it).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from conductor.bench.runner import BenchBusy, BenchError, BenchRefused, runner_of
from conductor.config import ConfigError
from conductor.roles_api import Principal
from conductor.routes.deps import require_ops, services_of

router = APIRouter(prefix="/api/bench")

NO_STORE = {"Cache-Control": "no-store"}
Ops = Annotated[Principal, Depends(require_ops)]


class RunRequest(BaseModel):
    job: str = Field(max_length=40)
    device_id: str = Field(max_length=60)
    params: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=20)


class AnswersRequest(BaseModel):
    answers: dict[str, str] = Field(max_length=20)


def _runner(request: Request):
    return runner_of(services_of(request))


def _json(data: Any, status_code: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status_code, headers=NO_STORE)


def _get(request: Request, run_id: str) -> dict:
    try:
        return _runner(request).get(run_id)
    except BenchError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None


@router.get("/jobs")
async def jobs(request: Request, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    runner = _runner(request)
    return _json(
        {
            "jobs": runner.specs(),
            "devices": runner.devices(),
            "mode": str(services.gate_settings.mode),
            "bench_live": services.version().get("bench_live"),
        }
    )


@router.post("/run", status_code=202)
async def run(request: Request, body: RunRequest, _ops: Ops) -> JSONResponse:
    runner = _runner(request)
    try:
        run_id = runner.start(body.job, body.device_id, dict(body.params))
    except BenchBusy as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except BenchError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    record = runner.get(run_id)
    return _json({"run_id": run_id, "simulated": record["simulated"], "driver": record["driver"]}, 202)


@router.get("/runs")
async def runs(request: Request, _ops: Ops, limit: int = 50) -> JSONResponse:
    return _json({"runs": _runner(request).list_runs(max(1, min(limit, 200)))})


@router.get("/runs/{run_id}")
async def run_detail(request: Request, run_id: str, _ops: Ops) -> JSONResponse:
    return _json(_get(request, run_id))


@router.get("/runs/{run_id}/anonymized")
async def run_anonymized(request: Request, run_id: str, _ops: Ops) -> JSONResponse:
    _get(request, run_id)
    return _json(_runner(request).anonymized(run_id))


@router.get("/runs/{run_id}/profile.json")
async def run_profile(request: Request, run_id: str, _ops: Ops) -> JSONResponse:
    _get(request, run_id)
    profile = _runner(request).profile(run_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="This run has no profile to download (only Gate A does).")
    return JSONResponse(
        profile,
        headers={**NO_STORE, "Content-Disposition": 'attachment; filename="sqhdk03.json"'},
    )


@router.post("/runs/{run_id}/answers")
async def run_answers(request: Request, run_id: str, body: AnswersRequest, _ops: Ops) -> JSONResponse:
    _get(request, run_id)
    try:
        record = _runner(request).answer(run_id, body.answers)
    except BenchRefused as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except BenchError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return _json(record)


@router.post("/runs/{run_id}/flags")
async def run_flags(request: Request, run_id: str, _ops: Ops) -> JSONResponse:
    _get(request, run_id)
    try:
        record = _runner(request).apply_flags(run_id)
    except BenchRefused as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return _json(record)


@router.get("/time")
async def server_time(request: Request, _ops: Ops) -> JSONResponse:
    return _json({"epoch_ms": round(services_of(request).clock.now() * 1000, 1)})
