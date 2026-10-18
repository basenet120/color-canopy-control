"""/api/module/* — the single-module test interface (BUILD_PLAN P0b, 07 §1–§2).

Who: ops, or a tester granted the device (`require_device_access`; the auth middleware already keeps testers to
`/ops/module` and `/api/module/<granted>/*`). `GET /api/module` lists only what the caller may open.

- `GET  /api/module`                       devices this principal may open, with lock holders
- `POST /api/module/{id}/open`             `{steal?}` → take the device's lock → `{lease, state}` (steal: ops only)
- `POST /api/module/{id}/heartbeat`        `{lease, active}` → lock status (409 once the lock is gone)
- `POST /api/module/{id}/close`            `{lease}` (JSON or a sendBeacon text body) → release
- `GET  /api/module/{id}[?refresh=1]`      map, capabilities, desired/reported per head, slots, flags, telemetry
- `GET  /api/module/{id}/export`           the anonymized JSON behind "Copy anonymized JSON"
- `POST /api/module/{id}/paint|whole|card|sweep|slot|effects|calibration|hold`  (each needs `lease`)

Cross-site requests (review F5): every POST here is refused (403) when its Origin — or, without one, its Referer —
names another host, and every POST except `close` must be `application/json` (415 otherwise), which a page on
another site can't send without a CORS preflight we never grant. `close` also takes a sendBeacon text body, but
releases only the lease it names, which another site can't know.

Every mutating call needs the lease from `open` (409 with a plain message when the lock is gone), returns the
command result (timings, HTTP result, readback) plus the updated state, and says `simulated: true` — with a
"SIMULATED" notice — whenever the SimDriver did the work.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from conductor.module import ModuleError, ModuleManager, ModuleSession
from conductor.roles_api import Principal, Role
from conductor.routes.accounts import same_origin
from conductor.routes.deps import principal_of, require_device_access, services_of


def _no_cross_site(request: Request) -> None:
    """Refuse cross-site writes before anything runs (a form on another site could otherwise take a lock)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    headers = request.headers
    if ("origin" in headers or "referer" in headers) and not same_origin(headers):
        raise HTTPException(status_code=403, detail="Refused: this request came from another site.")
    if request.url.path.endswith("/close"):
        return  # the sendBeacon release: text/plain, and it can only release the lease it names
    media_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="Send JSON (Content-Type: application/json).")


router = APIRouter(dependencies=[Depends(_no_cross_site)])
log = logging.getLogger(__name__)
NO_STORE = {"Cache-Control": "no-store"}
SIM_NOTICE = "SIMULATED — no real lights were driven."


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LeaseBody(_Body):
    lease: str | None = Field(None, max_length=200)


class OpenBody(_Body):
    steal: bool = False


class HeartbeatBody(LeaseBody):
    active: bool = True


class PaintBody(LeaseBody):
    slot: str | None = Field(None, max_length=20)
    heads: list[dict[str, Any]] | None = Field(None, max_length=90)
    fill: dict[str, Any] | None = None
    gradient: dict[str, Any] | None = None
    base: Literal["current", "off", "slot"] = "current"


class WholeBody(LeaseBody):
    look: dict[str, Any] | None = None
    power: bool | None = None
    brightness: int | None = None


class CardBody(LeaseBody):
    card: str = Field(max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)


class SweepBody(LeaseBody):
    action: Literal["start", "pause", "resume", "stop"]
    step_s: float | None = Field(None, gt=0, le=60)
    from_index: int | None = None


class SlotBody(LeaseBody):
    slot: str = Field(max_length=20)
    action: Literal["select", "write_current", "read", "load", "record_app"]


class EffectsBody(LeaseBody):
    slot: str = Field(max_length=20)
    action: Literal["start", "restart"]
    motionEffect: int | None = None
    brightnessEffect: int | None = None
    speeds: dict[str, int] | None = None


class CalibrationBody(LeaseBody):
    cable_path: str = Field(max_length=40)
    start_corner: str = Field(max_length=40)
    orientation: str = Field(max_length=4)
    wire_order: list[list[float]] | None = Field(None, max_length=90)
    flip: bool = False


class HoldBody(LeaseBody):
    on: bool
    name: str | None = Field(None, max_length=80)


Access = Annotated[Principal, Depends(require_device_access)]


def _manager(request: Request) -> ModuleManager:
    return ModuleManager.of(services_of(request))


def _fail(e: ModuleError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.message)


def _session(request: Request, device_id: str, lease: str | None) -> ModuleSession:
    try:
        return _manager(request).require(device_id, lease)
    except ModuleError as e:
        raise _fail(e) from None


def _reply(session: ModuleSession, **payload: Any) -> JSONResponse:
    body = {"ok": True, "simulated": session.target.simulated, **payload, "state": session.snapshot()}
    if session.target.simulated:
        body["notice"] = SIM_NOTICE
    return JSONResponse(body, headers=NO_STORE)


async def _run(fn: Callable[..., Awaitable[Any]], /, **kw: Any) -> Any:
    try:
        return await fn(**kw)
    except ModuleError as e:
        raise _fail(e) from None


# ---- listing, open, close ---------------------------------------------------------------------------------------
@router.get("/api/module")
async def list_devices(request: Request) -> JSONResponse:
    principal = principal_of(request)
    services = services_of(request)
    return JSONResponse(
        {
            "devices": _manager(request).devices(principal),
            "role": principal.role.value,
            "can_take_over": principal.role is Role.OPS,
            "simulated": bool(services.version().get("simulated", True)),
        },
        headers=NO_STORE,
    )


@router.post("/api/module/{device_id}/open")
async def open_device(
    request: Request, device_id: str, principal: Access, body: OpenBody | None = None
) -> JSONResponse:
    session = await _run(
        _manager(request).open, device_id=device_id, principal=principal, steal=bool(body and body.steal)
    )
    return _reply(session, lease=session.lease.token)


@router.post("/api/module/{device_id}/heartbeat")
async def heartbeat(request: Request, device_id: str, _who: Access, body: HeartbeatBody) -> JSONResponse:
    session = _session(request, device_id, body.lease)
    try:
        lock = session.heartbeat(body.active)
    except ModuleError as e:
        raise _fail(e) from None
    return JSONResponse({"ok": True, "lock": lock, "session_id": session.session_id}, headers=NO_STORE)


@router.post("/api/module/{device_id}/close")
async def close_device(request: Request, device_id: str, _who: Access) -> JSONResponse:
    """Accepts JSON or a `navigator.sendBeacon` text body (`{"lease": "..."}`): a beacon can't send JSON headers."""
    raw = await request.body()
    lease = None
    try:
        parsed = json.loads(raw or b"{}")
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("lease"), str):
        lease = parsed["lease"]
    released = await _manager(request).close(device_id, lease)
    return JSONResponse({"ok": True, "released": released}, headers=NO_STORE)


# ---- state ------------------------------------------------------------------------------------------------------
@router.get("/api/module/{device_id}")
async def device_state(request: Request, device_id: str, _who: Access, refresh: bool = False) -> JSONResponse:
    manager = _manager(request)
    session = manager.get(device_id)
    if session is None:
        try:
            return JSONResponse({"ok": True, "state": manager.closed_view(device_id)}, headers=NO_STORE)
        except ModuleError as e:  # unknown id (404)
            raise _fail(e) from None
        except KeyError:
            raise HTTPException(status_code=404, detail=f"There's no device called {device_id}.") from None
        except LookupError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
    if refresh:
        await session.refresh()
    return _reply(session)


@router.get("/api/module/{device_id}/export")
async def export(request: Request, device_id: str, _who: Access) -> JSONResponse:
    session = _manager(request).get(device_id)
    if session is None:
        raise HTTPException(status_code=409, detail="Open the device first.")
    return JSONResponse(session.export(), headers=NO_STORE)


# ---- actions ----------------------------------------------------------------------------------------------------
@router.post("/api/module/{device_id}/paint")
async def paint(request: Request, device_id: str, _who: Access, body: PaintBody) -> JSONResponse:
    session = _session(request, device_id, body.lease)
    result = await _run(
        session.paint_heads,
        slot=body.slot,
        heads=body.heads,
        fill=body.fill,
        gradient=body.gradient,
        base=body.base,
    )
    return _reply(session, result=result)


@router.post("/api/module/{device_id}/whole")
async def whole(request: Request, device_id: str, _who: Access, body: WholeBody) -> JSONResponse:
    session = _session(request, device_id, body.lease)
    result = await _run(session.whole, look=body.look, power=body.power, brightness=body.brightness)
    return _reply(session, result=result)


@router.post("/api/module/{device_id}/card")
async def card(request: Request, device_id: str, _who: Access, body: CardBody) -> JSONResponse:
    session = _session(request, device_id, body.lease)
    result = await _run(session.card, card=body.card, params=body.params)
    if "sweep" in result:
        return _reply(session, sweep=result["sweep"])
    return _reply(session, result=result)


@router.post("/api/module/{device_id}/sweep")
async def sweep(request: Request, device_id: str, _who: Access, body: SweepBody) -> JSONResponse:
    session = _session(request, device_id, body.lease)
    state = await _run(session.sweep, action=body.action, step_s=body.step_s, from_index=body.from_index)
    return _reply(session, sweep=state)


@router.post("/api/module/{device_id}/slot")
async def slot(request: Request, device_id: str, _who: Access, body: SlotBody) -> JSONResponse:
    session = _session(request, device_id, body.lease)
    out = await _run(session.slot, slot=body.slot, action=body.action)
    if body.action in ("select", "write_current"):
        return _reply(session, result=out)
    return _reply(session, slot=out)


@router.post("/api/module/{device_id}/effects")
async def effects(request: Request, device_id: str, _who: Access, body: EffectsBody) -> JSONResponse:
    session = _session(request, device_id, body.lease)
    result = await _run(
        session.effects,
        slot=body.slot,
        action=body.action,
        motion_effect=body.motionEffect,
        brightness_effect=body.brightnessEffect,
        speeds=body.speeds,
    )
    return _reply(session, result=result)


@router.post("/api/module/{device_id}/calibration")
async def calibration(request: Request, device_id: str, _who: Access, body: CalibrationBody) -> JSONResponse:
    session = _session(request, device_id, body.lease)
    try:
        saved = session.calibrate(
            orientation=body.orientation,
            cable_path=body.cable_path,
            start_corner=body.start_corner,
            wire_order=body.wire_order,
            flip=body.flip,
        )
    except ModuleError as e:
        raise _fail(e) from None
    return _reply(session, map=saved)


@router.post("/api/module/{device_id}/hold")
async def hold(request: Request, device_id: str, _who: Access, body: HoldBody) -> JSONResponse:
    session = _session(request, device_id, body.lease)
    try:
        state = session.set_hold(body.on, body.name)
    except ModuleError as e:
        raise _fail(e) from None
    return _reply(session, hold=state)
