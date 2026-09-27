"""/vignette/<room>/ and /api/vignette/<room>/* — the four staff vignette tablets (P4; 01, 06 #11, 07 §2).

Who: ops, or a vignette tablet enrolled for that room. The auth middleware already keeps a vignette principal to
`/vignette/<granted>/*`, `/api/vignette/<granted>/*`, `/api/ws-ticket`, `/api/version` and `/static/*` (401
elsewhere, including /ops); `require_room` below is the belt to its braces (403 for any other principal or room).

- `GET  /vignette/<room>/`            the shared page (`web/vignette/index.html`); its CSS/JS are served beside it
- `GET  /api/vignette/<room>[?refresh=1]`  room title, scenes, colour swatches, white shortcuts, and per device:
                                      controls from its own profile, desired look, what it reports now, the
                                      tracker's status (Ready / Updating / Needs attention) and `simulated`
- `POST /api/vignette/<room>/action`  `{device_id | group, action, value?, outlet?, confirm?}` → one PUT per device
- `POST /api/vignette/<room>/scene`   `{scene}` → one PUT per device in the scene

Writes are JSON only and same-origin (a form on another site can't send JSON without a preflight we never grant; a
foreign Origin/Referer is refused), like the module and kiosk routes. A lock or unlock without `"confirm": true`
answers 409 with `confirm_required: true` and sends nothing. A value the device can't take answers 422 with a plain
sentence. Live updates for the page go out on the WebSocket topic `vignette:<room>`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from conductor.config import ConfigError
from conductor.roles_api import Principal, Role
from conductor.routes.accounts import same_origin
from conductor.routes.deps import principal_of, services_of
from conductor.vignettes import ConfirmRequired, VignetteError, VignetteService

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent.parent / "web" / "vignette"
PAGE_FILES = {"index.html": "text/html", "vignette.css": "text/css", "vignette.js": "text/javascript"}
NO_STORE = {"Cache-Control": "no-store"}
PAGE_HEADERS = {"Cache-Control": "no-cache", "X-Frame-Options": "DENY"}
NOT_THIS_ROOM = "This tablet isn't set up for that room."
NOT_SET_UP = "The vignette pages aren't set up correctly. Ops: check config/vignettes.yaml."


def require_room(request: Request, room: str) -> Principal:
    """Ops, or a vignette tablet granted `room`."""
    principal = principal_of(request)
    if principal.role is Role.OPS or (principal.role is Role.VIGNETTE and room in principal.grants):
        return principal
    raise HTTPException(status_code=403, detail=NOT_THIS_ROOM)


def _json_same_site(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    headers = request.headers
    if ("origin" in headers or "referer" in headers) and not same_origin(headers):
        raise HTTPException(status_code=403, detail="Refused: this request came from another site.")
    media_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="Send JSON (Content-Type: application/json).")


router = APIRouter(dependencies=[Depends(_json_same_site)])


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionBody(_Body):
    device_id: str | None = Field(None, max_length=64)
    group: str | None = Field(None, max_length=16)  # every device of this kind in the room ("bulb")
    action: str = Field(min_length=1, max_length=32)
    value: Any = None
    outlet: str | None = Field(None, max_length=32)
    confirm: bool = False


class SceneBody(_Body):
    scene: str = Field(min_length=1, max_length=64)


def _service(request: Request) -> VignetteService:
    services = services_of(request)
    try:
        return VignetteService.of(services)
    except ConfigError as exc:
        log.error("vignettes: %s", exc)
        raise HTTPException(status_code=503, detail=NOT_SET_UP) from None


def _known_room(service: VignetteService, room: str) -> None:
    if room not in service.rooms.rooms:
        raise HTTPException(status_code=404, detail="There's no vignette with that name.")


async def _body(request: Request, model: type[_Body]) -> Any:
    try:
        return model.model_validate_json(await request.body())
    except ValidationError:
        raise HTTPException(status_code=422, detail="That request isn't one this page sends.") from None


def _refused(exc: Exception) -> JSONResponse:
    if isinstance(exc, ConfirmRequired):
        return JSONResponse({"detail": str(exc), "confirm_required": True}, status_code=409, headers=NO_STORE)
    if isinstance(exc, LookupError):
        return JSONResponse({"detail": str(exc).strip("'\"")}, status_code=404, headers=NO_STORE)
    return JSONResponse({"detail": str(exc)}, status_code=422, headers=NO_STORE)


# ---- API -----------------------------------------------------------------------------------------------------------
@router.get("/api/vignette/{room}")
async def room_state(request: Request, room: str, refresh: bool = False) -> JSONResponse:
    require_room(request, room)
    service = _service(request)
    _known_room(service, room)
    return JSONResponse(await service.room_state(room, refresh=refresh), headers=NO_STORE)


@router.post("/api/vignette/{room}/action")
async def action(request: Request, room: str) -> JSONResponse:
    require_room(request, room)
    service = _service(request)
    _known_room(service, room)
    body: ActionBody = await _body(request, ActionBody)
    try:
        report = await service.action(
            room,
            action=body.action,
            value=body.value,
            device_id=body.device_id,
            group=body.group,
            outlet=body.outlet,
            confirm=body.confirm,
        )
    except (VignetteError, LookupError) as exc:
        return _refused(exc)
    return JSONResponse(report, headers=NO_STORE)


@router.post("/api/vignette/{room}/scene")
async def scene(request: Request, room: str) -> JSONResponse:
    require_room(request, room)
    service = _service(request)
    _known_room(service, room)
    body: SceneBody = await _body(request, SceneBody)
    try:
        report = await service.scene(room, body.scene)
    except (VignetteError, LookupError) as exc:
        return _refused(exc)
    return JSONResponse(report, headers=NO_STORE)


# ---- the page ------------------------------------------------------------------------------------------------------
def _page_room(request: Request, room: str) -> None:
    require_room(request, room)
    if room not in _service(request).rooms.rooms:
        raise HTTPException(status_code=404, detail="There's no vignette with that name.")


@router.get("/vignette/{room}", include_in_schema=False)
def page_redirect(request: Request, room: str) -> RedirectResponse:
    _page_room(request, room)
    return RedirectResponse(f"/vignette/{room}/")


@router.get("/vignette/{room}/", include_in_schema=False)
def page(request: Request, room: str) -> FileResponse:
    _page_room(request, room)
    return FileResponse(WEB_DIR / "index.html", media_type="text/html", headers=PAGE_HEADERS)


@router.get("/vignette/{room}/{name}", include_in_schema=False)
def page_file(request: Request, room: str, name: str) -> FileResponse:
    _page_room(request, room)
    media_type = PAGE_FILES.get(name)
    if media_type is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return FileResponse(WEB_DIR / name, media_type=media_type, headers=PAGE_HEADERS)
