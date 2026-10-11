"""Bench routes (ops only): run Gates A–C and friends, read results, answer questions, apply flags and maps
(bench_api.py).

- `GET  /api/bench/jobs` — job specs, plus per device: simulated?, driver, who holds its lock.
- `POST /api/bench/run` `{job, device_id, params}` → `{run_id}`; progress on WebSocket topic "bench".
  409 with a plain message when the device is locked or already running a job.
- `GET  /api/bench/runs`, `GET /api/bench/runs/{id}` — saved results.
- `GET  /api/bench/runs/{id}/anonymized` — the copyable JSON for a build session.
- `GET  /api/bench/runs/{id}/profile.json` — Gate A's scrubbed profile (candidate simulator fixture).
- `POST /api/bench/runs/{id}/stop` — stop a run that is going (verdict ERROR, lock released). 409 if it isn't.
- `POST /api/bench/runs/{id}/answers` `{answers: {question: "yes"|"no"|note}}`. Taking back a "yes" after its flags
  (or a map) were applied puts them back to what they were (the response's `flags_revoked` / `map_revoked` says what
  changed).
- `POST /api/bench/runs/{id}/flags` — apply the proposed DeviceFlags to the ops overlay. Refused (409) for simulated
  runs and until the matching question is answered "yes".
- `GET  /api/bench/runs/{id}/map` — what Apply map would record from this address probe and why, or why not
  (`{ready, reason, mapping, runs}`). `POST` records it: the kit's identity controller map, in the ops overlay, from
  its own two answered probes (D58). Refused (409) unless the proof rule holds, and (403/415) unless it is JSON from
  this site. It sends nothing to the lights itself; a running show then re-reads the module (and re-sends its look).
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
from conductor.routes.accounts import same_origin
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


def _same_site_json(request: Request) -> None:
    """Refuse a cross-site or non-JSON write (403/415), as the module, ops and vignette routes do."""
    headers = request.headers
    if ("origin" in headers or "referer" in headers) and not same_origin(headers):
        raise HTTPException(status_code=403, detail="Refused: this request came from another site.")
    media_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="Send JSON (Content-Type: application/json).")


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


@router.post("/runs/{run_id}/stop")
async def run_stop(request: Request, run_id: str, _ops: Ops) -> JSONResponse:
    _get(request, run_id)
    try:
        record = await _runner(request).stop(run_id)
    except BenchRefused as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return _json(record)


@router.post("/runs/{run_id}/answers")
async def run_answers(request: Request, run_id: str, body: AnswersRequest, _ops: Ops) -> JSONResponse:
    _get(request, run_id)
    try:
        record = _runner(request).answer(run_id, body.answers)
    except BenchRefused as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except ConfigError as e:  # undoing applied flags couldn't be saved: the answer isn't saved either
        raise HTTPException(status_code=400, detail=str(e)) from None
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


@router.get("/runs/{run_id}/map")
async def run_map_proposal(request: Request, run_id: str, _ops: Ops) -> JSONResponse:
    _get(request, run_id)
    return _json(_runner(request).map_proposal(run_id))


@router.post("/runs/{run_id}/map")
async def run_map(request: Request, run_id: str, _ops: Ops) -> JSONResponse:
    _same_site_json(request)
    _get(request, run_id)
    try:
        record = await _runner(request).apply_map(run_id)
    except BenchRefused as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return _json(record)


@router.get("/time")
async def server_time(request: Request, _ops: Ops) -> JSONResponse:
    return _json({"epoch_ms": round(services_of(request).clock.now() * 1000, 1)})
