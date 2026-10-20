"""/api/kiosk/panel|scene|brightness|power|debug — the guest panel's API (the design is in conductor/guest_panel.py).

Who: an enrolled kiosk or ops, with the kiosk router's own checks (kiosk role or ops; writes are JSON-only and
same-site) — except `/api/kiosk/debug`, which is ops only (a kiosk gets 403).

- `GET  /api/kiosk/panel`       `{target, target_label, simulated, viewer, scenes, showing, capabilities,
                                brightness_range, canopy, state, server_now}`. `target` is always "canopy" (the engine,
                                D67; the field stays so the iPad's code is unchanged). `scenes` are the scenes the iPad
                                offers, in catalog order (kiosk.hidden_scenes left out, D76). Each scene is `{id, name,
                                mood, description, palette: [{name, hex}], frame: [[r, g, b] × 30], solid: "#RRGGBB",
                                brightness_draft, option_of: id | null, group: id, thumb}`: `group` is the scene's base
                                (its `option_of`) or its own id; `thumb` is null, or — only while more than six scenes
                                are visible (the review menu) — the card's still picture at the tier the engine would
                                show it at now: `{sig: 12 hex, basis: "heads" | "modules" | "strand", modules: [{pos,
                                color: "#RRGGBB" | null, colors: ["#RRGGBB" × 30] | null} × 9]}` ("heads": per light,
                                for a scene the engine would show as canvas or slot; "modules": its nine module
                                colours; "strand": a whole-strand plan's colour on each panel it drives; null for a
                                look the strand plays on its own, so the card keeps its palette strip).
                                `showing` is null, or the scene showing now when it is hidden (Now Showing still
                                names it; `state.scene` is checked against the whole catalog). `state` is `{power,
                                brightness, scene, look, phase, message, feature, cooldown_remaining_s, lit,
                                waiting, next}`:
                                power/brightness/scene are null when unknown; look is null (the engine's techniques
                                aren't a 30-light frame: `lit` is what the lights show); phase is ready | changing | resting |
                                paused | cooldown ("cooldown": the lights are steady and a new scene can be chosen in
                                cooldown_remaining_s; brightness and power still work: a change made now waits for
                                the cooldown's end, then goes out); message is a calm sentence for
                                the guest (or ""); feature is the true Hubspace sentence for what is showing, or null;
                                waiting is why every mood waits now — "try-on" (a Design Lab try-on, D66), "testing"
                                (the stepped rehearsal) — or null (the panel drops a waiting toast once it is null);
                                next is `{brightness?, power?}` the guest asked for and that hasn't gone out yet
                                (queued, or waiting out the cooldown), or null — the controls show it, not the
                                engine's `brightness`/`power`, until it lands.

The canopy picture (`conductor/canopy_view.py`; an older server may send `canopy: null` and `state.lit: null`,
and the panel then draws its 30-light serpentine instead). A fixed contract:

    canopy    = null | {width_in, height_in, module_w_in, module_h_in,
                        view: {mirror_x, rotate_deg: 0|90|180|270, dim_floor},          (kiosk.preview, set at install)
                        modules: [{pos: "r0c0"…"r2c2", row, col, heads: [[x, y] × 30]} × 9],
                        apron_in, outline: {x: [lo, hi], y: [lo, hi]}, fin_width_in,
                        fins: [{x, y, angle_deg, drop_in}]}
                (canopy inches, reflected-ceiling view, origin top-left; each module's heads top to bottom, then left
                to right: its lattice order, never its wiring. The fins (D74): 650 from config/fins.json, each a
                centre, a plan axis (0 = +x, 90 = +y) and a drop; the outline is the canopy plus its 6 in apron.
                Without a fin map: fins [], apron_in 0, fin_width_in 0, the outline the canopy itself)
    state.lit = null (no look yet, or an older server)
              | {level: 1–100 | null (off),
                 modules: [{pos, state: off|dark|unknown|lit|changing,
                            color: "#RRGGBB" | null, colors: ["#RRGGBB" × 30 in lattice order] | null} × 9],
                 motion: null | {kind: "ripple", t0, stagger_s, rings}
                              | {kind: "steps", t0, step_s, frames: [[hex | null × 9]],
                                 levels?: [[int | null × 9]], ends_at}}

Colours are full strength (the level is drawn separately); `color` is set when every head is the same, else `colors`;
`off`, `dark` and `unknown` carry no colour. `off` is claimed only once a panel reads back off; a locked or stuck panel,
or one whose power-off hasn't read back yet, is `unknown` even while the guest has the canopy off. A panel stuck only
for a flagged note on a confirmed look (D78) reads as its readback (`lit` or `off`). Neither part carries
a device id, label, reason or the vendor's name.
- `POST /api/kiosk/scene`       `{"scene": "<scene id>"}` → `{ok, state, server_now}`
- `POST /api/kiosk/brightness`  `{"level": 1–100}` (raised to the guest floor, 10; coalesced — the last level wins)
                                → `{ok, level, state, server_now}`
- `POST /api/kiosk/power`       `{"on": true|false}` → `{ok, state, server_now}`
- `GET  /api/kiosk/debug`       ops only: target internals, capabilities, lease holders, the last 50 commands
                                `{at, action, params, outcome, target, latency_ms, error}`, the last state served, and
                                `menu: {hidden, ignored}` (kiosk.hidden_scenes: what it hides, and what it names that
                                hides nothing).

Writes are accepted and then applied in the background (`state.phase` is "changing" until the lights confirm), so a
200 means "accepted", never "the lights changed". Refusals are the kiosk's — `{"detail": "<calm sentence>",
"retry_after_s"?}`, never an error code on the guest's screen: 429 cooldown (+ Retry-After) · 423 guest input paused ·
404 not one of the scenes the iPad offers (unknown, or hidden: kiosk.hidden_scenes) · 409 not available right now,
or someone is using the module page · 503 resting · 415/422/403 a malformed or cross-site request.
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
from conductor.ipad_menu import Menu
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


def _menu(panel: GuestPanel) -> Menu:
    try:
        return panel.menu()
    except (OSError, ConfigError) as exc:
        log.error("The guest panel couldn't read config/scenes.yaml: %s", exc)
        raise HTTPException(status_code=503, detail=RESTING) from None


def _state(panel: GuestPanel, target: Any, menu: Menu | None = None, *, remember: bool = True) -> dict:
    # The WHOLE catalog's ids: a hidden scene that is showing is still `state.scene` (Now Showing names it).
    menu = menu if menu is not None else _menu(panel)
    state = target.state({s.id for s in menu.catalog.scenes})
    if remember:  # the last state a guest-facing response carried (the debug view shows it)
        panel.last_state = state
    return state


def _thumbs(panel: GuestPanel, menu: Menu) -> dict[str, dict | None]:
    """The review cards' thumbnails; a thumbnail that can't be worked out is none, never a failed panel."""
    try:
        return panel.thumbs(menu)
    except Exception:
        log.exception("The guest panel couldn't draw the review cards' pictures.")
        return {}


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
    menu = _menu(panel)
    state = _state(panel, target, menu)
    thumbs = _thumbs(panel, menu)
    sid = state["scene"]
    showing = scene_json(menu.catalog.get(sid), thumbs.get(sid)) if sid in menu.hidden else None
    return JSONResponse(
        {
            "target": target.kind,
            "target_label": target.label,
            "simulated": target.simulated,
            "viewer": principal_of(request).role.value,
            "scenes": [scene_json(s, thumbs.get(s.id)) for s in menu.visible.scenes],
            "showing": showing,
            "capabilities": target.capabilities(),
            "brightness_range": [GUEST_FLOOR, 100],
            "canopy": target.canopy(),
            "state": state,
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
    menu = _menu(panel)
    try:
        scene = menu.visible.get(body.scene)
    except KeyError:
        why = (
            "hidden on the iPad (kiosk.hidden_scenes)" if body.scene in menu.hidden else "not in scenes.yaml"
        )
        return _refused(panel, target, "scene", params, GuestRefused(404, UNKNOWN_SCENE, why=why))
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
    menu = _menu(panel)
    return JSONResponse(
        {
            "target": target.kind,
            "target_label": target.label,
            "environment": services.gate_settings.environment,
            "simulated": target.simulated,
            "capabilities": target.capabilities(),
            "lease_holders": target.lease_holders(),
            "internals": target.debug(),
            "state": _state(panel, target, menu, remember=False),
            "last_state": panel.last_state,
            "commands": panel.log.recent(),
            "menu": {"hidden": list(menu.hidden), "ignored": list(menu.ignored)},
            "server_now": services.clock.now(),
        },
        headers=NO_STORE,
    )
