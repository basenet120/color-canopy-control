"""/api/kiosk/* — the guest canopy touchscreen's API (P3; 05 #12, 07 §2, §6).

Who: an enrolled kiosk, or ops. The auth middleware already keeps kiosk principals to `/kiosk`, `/api/kiosk/*`,
`/api/ws-ticket`, `/api/version` and `/static/*`.

- `GET  /api/kiosk/modes`   the six modes: `[{id, label, blurb, preview, pattern, feature}]`. `preview` is the
                            mode's colour swatch (config/modes.yaml); `pattern` says how the kiosk's illustrated
                            canopy spreads those colours; `feature` is the "Hubspace feature" sentence for the tier
                            actually in use — known only for the mode now showing (from the engine), else null.
- `GET  /api/kiosk/state`   the engine's CanopyState JSON, plus `server_now` and `cooldown_remaining_s` so the page
                            counts the cooldown down in server-relative time (the kiosk's own clock may be off).
- `POST /api/kiosk/select`  `{"mode": "<id>"}` (JSON only) → `engine.request_mode(mode, source="kiosk")` → state.

Plain refusals (never an error code on the guest's screen — the page turns these into calm copy):
  cooldown → 429 {detail, retry_after_s} (+ Retry-After) · guest input paused → 423 · unknown mode → 404 ·
  no engine / engine stopped → 503 "The canopy is resting. It will be back shortly."

The engine lives at `services.extras["canopy"]` (engine_api.CanopyEngineAPI). It signals a refusal with
`engine_api.ModeRefused`; the route also checks the published state first (paused guest input, a running cooldown,
an unknown mode) so the guest gets the right answer whichever way the engine reports it. Nothing here writes to a
device: selections go through the engine, which writes through the executor.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from conductor.config import CONFIG_DIR
from conductor.engine_api import CanopyPhase, CanopyState, ModeRefused, PlanError, RefusalReason
from conductor.roles_api import Principal, Role
from conductor.routes.accounts import same_origin
from conductor.routes.deps import principal_of, services_of

log = logging.getLogger(__name__)

ENGINE_KEY = "canopy"
MODES_FILE = CONFIG_DIR / "modes.yaml"
NO_STORE = {"Cache-Control": "no-store"}

RESTING = "The canopy is resting. It will be back shortly."
PAUSED = "The canopy is resting for a moment. It will be back shortly."
COOLDOWN = "The light is still changing. Choose again in a moment."
UNKNOWN = "That mood isn't one of the canopy's choices."
UNAVAILABLE = "That mood isn't available right now. Choose another."
MAX_RETRY_AFTER_S = 60.0  # a guest never waits longer than this, whatever a bad clock says


# ---- who may call ------------------------------------------------------------------------------------------------
def require_kiosk(request: Request) -> Principal:
    """The kiosk or ops. (The middleware already refuses other enrolled roles; this is the belt to its braces.)"""
    principal = principal_of(request)
    if principal.role not in (Role.KIOSK, Role.OPS):
        raise HTTPException(status_code=403, detail="Only the canopy kiosk can do this.")
    return principal


def _json_same_site(request: Request) -> None:
    """Writes must be JSON (a form on another site can't send it without a preflight we never grant) and, when the
    browser names an Origin/Referer, from this host."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    headers = request.headers
    if ("origin" in headers or "referer" in headers) and not same_origin(headers):
        raise HTTPException(status_code=403, detail="Refused: this request came from another site.")
    media_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="Send JSON (Content-Type: application/json).")


router = APIRouter(dependencies=[Depends(require_kiosk), Depends(_json_same_site)])


class SelectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = Field(min_length=1, max_length=64)


# ---- modes -------------------------------------------------------------------------------------------------------
def _pattern(spec: Mapping[str, Any]) -> str:
    """How the kiosk illustration spreads a mode's preview colours, from the mode's primary technique.

    wash     — one whole-strand look (the colours vary gently, as warm white does across a room)
    scatter  — a preset with mixed colours (Holiday)
    gradient — a canvas gradient down the canopy (Sunset)
    pathway  — a winding bright path over a darker base (Garden Gathering)
    bands    — one soft band per module (Golden Hour)
    """
    ladder = spec.get("ladder") or ["strand"]
    technique = (spec.get("techniques") or {}).get(ladder[0]) or {}
    if ladder[0] == "strand":
        look = technique.get("look") or {}
        return "scatter" if look.get("kind") == "preset" else "wash"
    canvas = technique.get("canvas") or {}
    kind = canvas.get("kind")
    if kind == "gradient":
        return "gradient"
    if kind == "pathway":
        return "pathway"
    if kind == "band_per_module":
        return "bands"
    return "wash"


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("modes"), dict):
        raise ValueError(f"{path.name} has no `modes:` section.")  # noqa: TRY004 - a config error, not a type
    return raw


def _field(obj: Any, name: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _from_looks() -> dict[str, Any] | None:
    """The validated modes from P2's loader, when it exists (it's built in parallel; imported lazily)."""
    try:
        from conductor.looks import load_modes  # type: ignore[attr-defined]
    except ImportError:
        return None
    loaded = load_modes()
    if not isinstance(loaded, Mapping) and isinstance(getattr(loaded, "modes", None), Mapping):
        loaded = loaded.modes  # conductor.looks.schema.ModesFile
    if isinstance(loaded, Mapping) and "modes" in loaded and isinstance(loaded["modes"], Mapping | list):
        loaded = loaded["modes"]
    if isinstance(loaded, Mapping):
        return dict(loaded)
    return {str(_field(m, "id") or _field(m, "mode_id")): m for m in loaded}


def load_kiosk_modes(path: Path = MODES_FILE) -> list[dict[str, Any]]:
    """The six modes as the kiosk shows them, in config order. Raises ValueError with a plain message."""
    raw = _read_yaml(path)
    specs: dict[str, Any] = raw["modes"]
    looks = None
    if path == MODES_FILE:
        try:
            looks = _from_looks()
        except Exception as exc:  # noqa: BLE001 - the yaml still serves the kiosk; ops sees the log line
            log.warning(
                "The looks loader refused config/modes.yaml (%s); the kiosk reads the file directly.", exc
            )
    order = [m for m in looks if m in specs] if looks else list(specs)
    modes = []
    for mode_id in order:
        spec = specs[mode_id] or {}
        loaded = (looks or {}).get(mode_id)
        label = _field(loaded, "label") or spec.get("label") or mode_id
        blurb = _field(loaded, "blurb") or spec.get("blurb") or ""
        preview = _field(loaded, "preview") or spec.get("preview") or []
        modes.append(
            {
                "id": mode_id,
                "label": str(label),
                "blurb": str(blurb),
                "preview": [str(c) for c in preview],
                "pattern": _pattern(spec),
                "feature": None,
            }
        )
    return modes


# ---- engine ------------------------------------------------------------------------------------------------------
def _engine(request: Request) -> Any:
    engine = services_of(request).extras.get(ENGINE_KEY)
    if engine is None:
        raise HTTPException(status_code=503, detail=RESTING)
    return engine


def _engine_state(engine: Any) -> CanopyState:
    try:
        return engine.state()
    except Exception:
        log.exception("The canopy engine couldn't report its state.")
        raise HTTPException(status_code=503, detail=RESTING) from None


def _remaining(until: float | None, now: float) -> float:
    if until is None or not math.isfinite(until):
        return 0.0
    return round(min(MAX_RETRY_AFTER_S, max(0.0, until - now)), 3)


def state_json(services: Any, state: CanopyState) -> dict[str, Any]:
    """CanopyState JSON + `server_now` + `cooldown_remaining_s` (server-relative, so clock skew can't matter)."""
    now = services.clock.now()
    data = state.to_json()
    data["server_now"] = now
    data["cooldown_remaining_s"] = _remaining(state.cooldown_until, now)
    return data


def _refuse(status: int, message: str, **extra: Any) -> JSONResponse:
    """A plain refusal: `{"detail": "<guest-safe sentence>", ...extra}`."""
    headers = dict(NO_STORE)
    if "retry_after_s" in extra:
        headers["Retry-After"] = str(max(1, math.ceil(extra["retry_after_s"])))
    return JSONResponse({"detail": message, **extra}, status_code=status, headers=headers)


def _cooldown(retry_after_s: float | None) -> JSONResponse:
    wait = round(min(MAX_RETRY_AFTER_S, max(0.0, retry_after_s or 0.0)), 3)
    return _refuse(429, COOLDOWN, retry_after_s=wait)


def _refusal(exc: ModeRefused) -> JSONResponse:
    if exc.reason is RefusalReason.COOLDOWN:
        return _cooldown(exc.retry_after_s)
    if exc.reason is RefusalReason.PAUSED:
        return _refuse(423, PAUSED)
    if exc.reason is RefusalReason.UNKNOWN_MODE:
        return _refuse(404, UNKNOWN)
    return _refuse(503, RESTING)


def _modes_or_503() -> list[dict[str, Any]]:
    try:
        return load_kiosk_modes()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        log.error("The kiosk couldn't read config/modes.yaml: %s", exc)
        raise HTTPException(status_code=503, detail=RESTING) from None


# ---- routes ------------------------------------------------------------------------------------------------------
@router.get("/api/kiosk/modes")
def modes(request: Request) -> JSONResponse:
    listing = _modes_or_503()
    engine = services_of(request).extras.get(ENGINE_KEY)
    if engine is not None:
        try:
            state = engine.state()
        except Exception:  # noqa: BLE001 - the list still serves; the state call will say "resting"
            state = None
        if state is not None and state.mode and state.feature:
            for mode in listing:
                if mode["id"] == state.mode:
                    mode["feature"] = state.feature
    return JSONResponse(listing, headers=NO_STORE)


@router.get("/api/kiosk/state")
def state(request: Request) -> JSONResponse:
    engine = _engine(request)
    return JSONResponse(state_json(services_of(request), _engine_state(engine)), headers=NO_STORE)


@router.post("/api/kiosk/select")
async def select(request: Request) -> JSONResponse:
    try:
        body = SelectBody.model_validate_json(await request.body())
    except ValidationError:
        raise HTTPException(status_code=422, detail='Send {"mode": "<mode id>"}.') from None
    services = services_of(request)
    engine = _engine(request)
    if body.mode not in {m["id"] for m in _modes_or_503()}:
        raise HTTPException(status_code=404, detail=UNKNOWN)
    # The published state first: a paused kiosk or a running cooldown is refused whatever the engine does.
    current = _engine_state(engine)
    if not current.guest_input:
        return _refuse(423, PAUSED)
    if current.phase == CanopyPhase.RESTING:
        return _refuse(503, RESTING)
    wait = _remaining(current.cooldown_until, services.clock.now())
    if wait > 0:
        return _cooldown(wait)
    try:
        new_state = await engine.request_mode(body.mode, source="kiosk")
    except ModeRefused as exc:
        return _refusal(exc)
    except PlanError as exc:
        log.warning("Kiosk asked for %s but it can't compile: %s", body.mode, exc)
        return _refuse(409, UNAVAILABLE)
    except LookupError:  # KeyError included: the engine doesn't know the mode
        return _refuse(404, UNKNOWN)
    except Exception:
        log.exception("The canopy engine failed a kiosk request for %s.", body.mode)
        return _refuse(503, RESTING)
    if not isinstance(new_state, CanopyState):
        new_state = _engine_state(engine)
    return JSONResponse(state_json(services, new_state), headers=NO_STORE)
