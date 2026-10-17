"""/api/kiosk/panel|scene|brightness|power|debug — the guest panel's API (the design is in conductor/guest_panel.py).

Who: an enrolled kiosk or ops, with the kiosk router's own checks (kiosk role or ops; writes are JSON-only and
same-site) — except `/api/kiosk/debug`, which is ops only (a kiosk gets 403).

- `GET  /api/kiosk/panel`       `{target, target_label, simulated, viewer, scenes, capabilities, brightness_range, canopy,
                                state, server_now}`. `target` is always "canopy" (the engine, D67; the field stays
                                so the iPad's code is unchanged). Each scene is `{id, name, mood, description, palette: [{name, hex}],
                                frame: [[r, g, b] × 30], solid: "#RRGGBB", brightness_draft}`. `state` is `{power,
                                brightness, scene, look, phase, message, feature, cooldown_remaining_s, lit,
                                waiting}`:
                                power/brightness/scene are null when unknown; look is null (the engine's techniques
                                aren't a 30-light frame: `lit` is what the lights show); phase is ready | changing | resting |
                                paused | cooldown ("cooldown": the lights are steady and a new scene can be chosen in
                                cooldown_remaining_s; brightness and power still work); message is a calm sentence for
                                the guest (or ""); feature is the true Hubspace sentence for what is showing, or null;
                                waiting is why every mood waits now — "try-on" (a Design Lab try-on, D66), "testing"
                                (the stepped rehearsal) — or null (the panel drops a waiting toast once it is null).

The canopy picture (`conductor/canopy_view.py`; an older server may send `canopy: null` and `state.lit: null`,
and the panel then draws its 30-light serpentine instead). A fixed contract:

    canopy    = null | {width_in, height_in, module_w_in, module_h_in,
                        view: {mirror_x, rotate_deg: 0|90|180|270, dim_floor},          (kiosk.preview, set at install)
                        modules: [{pos: "r0c0"…"r2c2", row, col, heads: [[x, y] × 30]} × 9]}
                (canopy inches, reflected-ceiling view, origin top-left; each module's heads top to bottom, then left
                to right: its lattice order, never its wiring)
    state.lit = null (no look yet, or an older server)
              | {level: 1–100 | null (off),
                 modules: [{pos, state: off|dark|unknown|lit|changing,
                            color: "#RRGGBB" | null, colors: ["#RRGGBB" × 30 in lattice order] | null} × 9],
                 motion: null | {kind: "ripple", t0, stagger_s, rings}
                              | {kind: "steps", t0, step_s, frames: [[hex | null × 9]], ends_at}}

Colours are full strength (the level is drawn separately); `color` is set when every head is the same, else `colors`;
`off`, `dark` and `unknown` carry no colour. `off` is claimed only once a panel reads back off; a locked or stuck panel,
or one whose power-off hasn't read back yet, is `unknown` even while the guest has the canopy off. Neither part carries
a device id, label, reason or the vendor's name.
- `POST /api/kiosk/scene`       `{"scene": "<scene id>"}` → `{ok, state, server_now}`
- `POST /api/kiosk/brightness`  `{"level": 1–100}` (raised to the guest floor, 10; coalesced — the last level wins)
                                → `{ok, level, state, server_now}`
- `POST /api/kiosk/power`       `{"on": true|false}` → `{ok, state, server_now}`
- `GET  /api/kiosk/debug`       ops only: target internals, capabilities, lease holders, the last 50 commands
                                `{at, action, params, outcome, target, latency_ms, error}`, the last state served.

Writes are accepted and then applied in the background (`state.phase` is "changing" until the lights confirm), so a
200 means "accepted", never "the lights changed". Refusals are the kiosk's — `{"detail": "<calm sentence>",
"retry_after_s"?}`, never an error code on the guest's screen: 429 cooldown (+ Retry-After) · 423 guest input paused ·
404 not one of the six scenes · 409 not available right now, or someone is using the module page · 503 resting ·
415/422/403 a malformed or cross-site request.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from conductor.config import ConfigError
from conductor.guest_panel import (
    GUEST_FLOOR,
    UNKNOWN_SCENE,
    GuestPanel,
    GuestRefused,
    scene_json,
)
from conductor.roles_api import Principal
from conductor.routes.deps import principal_of, require_ops, services_of
from conductor.routes.kiosk import NO_STORE, RESTING, _json_same_site, _refuse, require_kiosk

log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_kiosk), Depends(_json_same_site)])


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SceneBody(_Body):
    scene: str = Field(min_length=1, max_length=64)


class BrightnessBody(_Body):
    level: int = Field(ge=1, le=100, strict=True)


class PowerBody(_Body):
    on: bool = Field(strict=True)


async def _parse(request: Request, model: type[_Body], hint: str) -> Any:
    try:
        return model.model_validate_json(await request.body())
    except ValidationError:
        raise HTTPException(status_code=422, detail=hint) from None


def _catalog(panel: GuestPanel) -> Any:
    try:
        return panel.scenes()
    except (OSError, ConfigError) as exc:
        log.error("The guest panel couldn't read config/scenes.yaml: %s", exc)
        raise HTTPException(status_code=503, detail=RESTING) from None


def _state(panel: GuestPanel, target: Any, *, remember: bool = True) -> dict:
    state = target.state({s.id for s in _catalog(panel).scenes})
    if remember:  # the last state a guest-facing response carried (the debug view shows it)
        panel.last_state = state
    return state


def _ok(panel: GuestPanel, target: Any, **extra: Any) -> JSONResponse:
    services = panel.services
    body = {"ok": True, **extra, "state": _state(panel, target), "server_now": services.clock.now()}
    return JSONResponse(body, headers=NO_STORE)


def _refused(panel: GuestPanel, target: Any, action: str, params: dict, e: GuestRefused) -> JSONResponse:
    panel.log.record(action, params, "refused", target=target.kind, error=e.why)
    return _refuse(e.status, e.message, **e.extra)


@router.get("/api/kiosk/panel")
async def panel_view(request: Request) -> JSONResponse:
    services = services_of(request)
    panel = GuestPanel.of(services)
    target = await panel.target()
    catalog = _catalog(panel)
    return JSONResponse(
        {
            "target": target.kind,
            "target_label": target.label,
            "simulated": target.simulated,
            "viewer": principal_of(request).role.value,
            "scenes": [scene_json(s) for s in catalog.scenes],
            "capabilities": target.capabilities(),
            "brightness_range": [GUEST_FLOOR, 100],
            "canopy": target.canopy(),
            "state": _state(panel, target),
            "server_now": services.clock.now(),
        },
        headers=NO_STORE,
    )


@router.post("/api/kiosk/scene")
async def choose_scene(request: Request) -> JSONResponse:
    body = await _parse(request, SceneBody, 'Send {"scene": "<scene id>"}.')
    panel = GuestPanel.of(services_of(request))
    target = await panel.target()
    params = {"scene": body.scene}
    try:
        scene = _catalog(panel).get(body.scene)
    except KeyError:
        return _refused(
            panel, target, "scene", params, GuestRefused(404, UNKNOWN_SCENE, why="not in scenes.yaml")
        )
    try:
        await target.scene(scene)
    except GuestRefused as e:
        return _refused(panel, target, "scene", params, e)
    panel.log.record("scene", params, "accepted", target=target.kind)
    return _ok(panel, target)


@router.post("/api/kiosk/brightness")
async def set_brightness(request: Request) -> JSONResponse:
    body = await _parse(request, BrightnessBody, 'Send {"level": <a whole number 1–100>}.')
    panel = GuestPanel.of(services_of(request))
    target = await panel.target()
    params = {"level": body.level}
    try:
        level = await target.brightness(body.level)
    except GuestRefused as e:
        return _refused(panel, target, "brightness", params, e)
    panel.log.record(
        "brightness",
        params,
        "accepted" if level == body.level else f"accepted as {level}",
        target=target.kind,
    )
    return _ok(panel, target, level=level)


@router.post("/api/kiosk/power")
async def set_power(request: Request) -> JSONResponse:
    body = await _parse(request, PowerBody, 'Send {"on": true} or {"on": false}.')
    panel = GuestPanel.of(services_of(request))
    target = await panel.target()
    params = {"on": body.on}
    try:
        await target.power(body.on)
    except GuestRefused as e:
        return _refused(panel, target, "power", params, e)
    panel.log.record("power", params, "accepted", target=target.kind)
    return _ok(panel, target)


@router.get("/api/kiosk/debug")
async def debug(request: Request, _ops: Annotated[Principal, Depends(require_ops)]) -> JSONResponse:
    services = services_of(request)
    panel = GuestPanel.of(services)
    target = await panel.target()
    return JSONResponse(
        {
            "target": target.kind,
            "target_label": target.label,
            "environment": services.gate_settings.environment,
            "simulated": target.simulated,
            "capabilities": target.capabilities(),
            "lease_holders": target.lease_holders(),
            "internals": target.debug(),
            "state": _state(panel, target, remember=False),
            "last_state": panel.last_state,
            "commands": panel.log.recent(),
            "server_now": services.clock.now(),
        },
        headers=NO_STORE,
    )
