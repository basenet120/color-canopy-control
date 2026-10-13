"""/api/kiosk/* — the guest canopy touchscreen's API (P3; 05 #12, 07 §2, §6).

Who: an enrolled kiosk, or ops. The auth middleware already keeps kiosk principals to `/kiosk`, `/api/kiosk/*`,
`/api/ws-ticket`, `/api/version` and `/static/*`.

- `GET  /api/kiosk/modes`   the six modes: `[{id, label, blurb, preview, pattern, feature, tier, module_colours}]`.
                            `preview` is the mode's colour swatch (config/modes.yaml); `pattern` says how the kiosk's
                            illustrated canopy spreads those colours *at the tier the engine would use now* ("modules"
                            at T3, and for a one-colour whole-strand look: one flat colour per module, from
                            `module_colours`; a modules-first mode lists its static column colours until a plan says
                            otherwise); `feature` is the "Hubspace feature" sentence for the tier actually in
                            use — known only for the mode now showing (from the engine), else null.
- `GET  /api/kiosk/state`   the engine's CanopyState JSON, plus `server_now` and `cooldown_remaining_s` so the page
                            counts the cooldown down in server-relative time (the kiosk's own clock may be off), and
                            `module_colours` (nine "#RRGGBB", r0c0…r2c2) when the plan in use shows one flat colour per
                            module (T3, or a one-colour whole-strand look).
                            While the engine commissions slots, the kiosk sees "resting" ("being set up"): no look is
                            changing, so it never says "Changing the light…".
- `POST /api/kiosk/select`  `{"mode": "<id>"}` (JSON only) → `engine.request_mode(mode, source="kiosk")` → state.
                            200 only when the engine accepted the choice.

Plain refusals (never an error code on the guest's screen — the page turns these into calm copy):
  cooldown → 429 {detail, retry_after_s} (+ Retry-After) · guest input paused → 423 · unknown mode → 404 ·
  no tier of that mode can be shown → 409 · no engine / engine stopped / outage / commissioning → 503
  "The canopy is resting…" (+ Retry-After when the engine says when to try again).

The engine lives at `services.extras["canopy"]` (engine_api.CanopyEngineAPI). conductor/canopy.py reports a refusal
as a `CanopyState` with `refused=True` (never an exception); older engines and the fakes raise
`engine_api.ModeRefused`. The route handles both, and also checks the published state first (paused guest input, a
running cooldown, an unknown mode). Nothing here writes to a device: selections go through the engine, which writes
through the executor.
"""

from __future__ import annotations

import dataclasses
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
from conductor.engine_api import (
    CanopyPhase,
    CanopyState,
    ModePlan,
    ModeRefused,
    PlanError,
    RefusalReason,
    Tier,
)
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
BEING_SET_UP = "The canopy is being set up. It will be back shortly."
GRID = [f"r{r}c{c}" for r in range(3) for c in range(3)]  # the kiosk illustration's module order
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
def _pattern(spec: Mapping[str, Any], tier: str | None = None) -> str:
    """How the kiosk illustration spreads a mode's preview colours, at `tier` (default: the primary technique).

    wash       — one whole-strand look (the colours vary gently, as warm white does across a room)
    scatter    — a preset with mixed colours
    gradient   — a canvas gradient down the canopy (axis y)
    gradient-x — a canvas gradient across the canopy, left to right (axis x: the scenes, D59)
    pathway    — a winding bright path over a darker base
    bands      — one soft band per module
    modules    — one flat colour per module (`module_colours`): module level (T3), and a whole-strand look that
                 isn't a preset (every module shows one colour)
    A hand-painted slot (T2) copies the canvas picture, so it draws like the canvas technique.
    """
    ladder = spec.get("ladder") or ["strand"]
    techniques = spec.get("techniques") or {}
    if tier == str(Tier.T3):
        return "modules"
    if tier == str(Tier.STRAND):
        look = (techniques.get("strand") or {}).get("look") or {}
        return "scatter" if look.get("kind") == "preset" else "modules"
    if ladder[0] == "modules":
        return "modules"
    technique = techniques.get(ladder[0]) or {}
    if ladder[0] == "strand":
        look = technique.get("look") or {}
        return "scatter" if look.get("kind") == "preset" else "wash"
    canvas = technique.get("canvas") or {}
    kind = canvas.get("kind")
    if kind == "gradient":
        return "gradient-x" if canvas.get("axis") == "x" else "gradient"
    if kind == "pathway":
        return "pathway"
    if kind == "band_per_module":
        return "bands"
    return "wash"


def _column_colours(spec: Mapping[str, Any]) -> list[str] | None:
    """Nine "#RRGGBB" (r0c0…r2c2) from a modules-first mode's static `techniques.modules.columns`, so its picture
    steps column by column like the ceiling even before the engine can compile a plan for it; else None."""
    if (spec.get("ladder") or ["strand"])[0] != "modules":
        return None
    columns = ((spec.get("techniques") or {}).get("modules") or {}).get("columns") or []
    if len(columns) != 3 or any((c or {}).get("kind") != "rgb" for c in columns):
        return None
    try:
        hexes = ["#" + "".join(f"{int(v):02X}" for v in c["rgb"]) for c in columns]
    except (KeyError, TypeError, ValueError):
        return None
    return [hexes[i % 3] for i in range(9)]


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
                "pattern_primary": _pattern(spec),  # the picture above T3 (the kiosk falls back to it)
                "feature": None,
                "tier": None,
                "module_colours": _column_colours(spec),
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


def _flag(engine: Any, name: str) -> Any:
    """An optional engine attribute (conductor/canopy.py has `commissioning`, `paused`, `outage`, `stopped`)."""
    try:
        return getattr(engine, name, None)
    except Exception:  # noqa: BLE001 - a property that fails is just "unknown"
        return None


def _hex(preview: list[dict]) -> str | None:
    """The mean colour of a module's per-head preview as "#RRGGBB" (flat at T3, so the mean is that colour)."""
    try:
        n = len(preview)
        if not n:
            return None
        rgb = [round(sum(float(p.get(k, 0)) for p in preview) / n) for k in ("r", "g", "b")]
    except (TypeError, ValueError, AttributeError):
        return None
    return "#" + "".join(f"{max(0, min(255, v)):02X}" for v in rgb)


def module_colours(services: Any, plan: ModePlan | None) -> list[str | None] | None:
    """Nine colours (r0c0…r2c2) when `plan` shows one flat colour per module — module level (T3), or a whole-strand
    look whose every module previews as one colour — which is what the ceiling shows; else None."""
    if plan is None or str(plan.tier) not in (str(Tier.T3), str(Tier.STRAND)):
        return None
    if str(plan.tier) == str(Tier.STRAND) and not all(_flat(t.preview) for t in plan.modules.values()):
        return None  # e.g. a preset: its heads differ, so the kiosk keeps the mode's own picture
    try:
        by_module = {e.module: e.id for e in services.registry.canopy_modules()}
    except Exception:  # noqa: BLE001 - no registry, no picture
        return None
    out: list[str | None] = []
    for position in GRID:
        target = plan.modules.get(by_module.get(position, ""))
        out.append(_hex(list(target.preview)) if target is not None and target.preview else None)
    return out


def _flat(preview: Any) -> bool:
    """True when every head of a module's preview is the same colour."""
    try:
        return len({(p.get("r"), p.get("g"), p.get("b")) for p in preview}) <= 1
    except (TypeError, AttributeError):
        return False


def _current_plan(engine: Any) -> ModePlan | None:
    try:
        return engine.current_plan()
    except Exception:  # noqa: BLE001 - the picture falls back to the mode's own pattern
        return None


def kiosk_view(engine: Any, state: CanopyState) -> CanopyState:
    """The engine's state as the guest should read it: while slots are being commissioned no look is changing, so
    the kiosk rests ("being set up") instead of saying "Changing the light…" for minutes."""
    if _flag(engine, "commissioning") is True and state.phase != CanopyPhase.RESTING:
        return dataclasses.replace(
            state, phase=CanopyPhase.RESTING, look_phase=CanopyPhase.RESTING, message=BEING_SET_UP
        )
    return state


def _remaining(until: float | None, now: float) -> float:
    if until is None or not math.isfinite(until):
        return 0.0
    return round(min(MAX_RETRY_AFTER_S, max(0.0, until - now)), 3)


def state_json(services: Any, state: CanopyState, engine: Any = None) -> dict[str, Any]:
    """CanopyState JSON + `server_now` + `cooldown_remaining_s` (server-relative, so clock skew can't matter), and
    — given the engine — the kiosk's view of it plus `module_colours` for a plan with one flat colour per module."""
    now = services.clock.now()
    if engine is not None:
        state = kiosk_view(engine, state)
    data = state.to_json()
    data["server_now"] = now
    data["cooldown_remaining_s"] = _remaining(state.cooldown_until, now)
    plan = _current_plan(engine) if engine is not None else None
    data["module_colours"] = (
        module_colours(services, plan) if plan is not None and plan.mode_id == state.mode else None
    )
    return data


def _refuse(status: int, message: str, **extra: Any) -> JSONResponse:
    """A plain refusal: `{"detail": "<guest-safe sentence>", ...extra}`."""
    headers = dict(NO_STORE)
    if "retry_after_s" in extra:
        headers["Retry-After"] = str(max(1, math.ceil(extra["retry_after_s"])))
    return JSONResponse({"detail": message, **extra}, status_code=status, headers=headers)


Refusal = tuple[
    int, str, dict[str, float]
]  # (HTTP status, guest-safe sentence, extra JSON such as retry_after_s)


def cooldown_refusal(retry_after_s: float | None) -> Refusal:
    return 429, COOLDOWN, {"retry_after_s": round(min(MAX_RETRY_AFTER_S, max(0.0, retry_after_s or 0.0)), 3)}


def _cooldown(retry_after_s: float | None) -> JSONResponse:
    status, message, extra = cooldown_refusal(retry_after_s)
    return _refuse(status, message, **extra)


def _retry(seconds: float | None) -> dict[str, float]:
    if seconds is None or not math.isfinite(seconds) or seconds <= 0:
        return {}
    return {"retry_after_s": round(min(MAX_RETRY_AFTER_S, seconds), 3)}


def refusal_for_state(engine: Any, st: CanopyState, now: float) -> Refusal:
    """The plain refusal for a `CanopyState` the engine returned with `refused=True` (conductor/canopy.py never
    raises). The engine's own sentence is for ops (it may name tiers and reasons), so it goes to the log; the guest
    gets the kiosk's calm copy. (Shared with the guest panel, conductor/guest_panel.py.)"""
    log.info("The canopy engine declined a kiosk choice: %s", st.message)
    if _flag(engine, "stopped") is True:
        return 503, RESTING, {}
    if _flag(engine, "commissioning") is True:
        return 503, BEING_SET_UP, _retry(st.retry_after_s or 30.0)
    if _flag(engine, "outage") is not None:
        return 503, RESTING, _retry(st.retry_after_s)
    if _flag(engine, "paused") is True or not st.guest_input:
        return 423, PAUSED, {}
    wait = _remaining(st.cooldown_until, now)
    if wait > 0:
        return cooldown_refusal(wait)
    if st.phase == CanopyPhase.RESTING:
        return 503, RESTING, _retry(st.retry_after_s)
    return 409, UNAVAILABLE, {}


def refusal_for(exc: ModeRefused) -> Refusal:
    """The plain refusal for a `ModeRefused` raised by the engine (older engines and the fakes)."""
    if exc.reason is RefusalReason.COOLDOWN:
        return cooldown_refusal(exc.retry_after_s)
    if exc.reason is RefusalReason.PAUSED:
        return 423, PAUSED, {}
    if exc.reason is RefusalReason.UNKNOWN_MODE:
        return 404, UNKNOWN, {}
    return 503, RESTING, {}


def _refused_state(engine: Any, st: CanopyState, now: float) -> JSONResponse:
    status, message, extra = refusal_for_state(engine, st, now)
    return _refuse(status, message, **extra)


def _refusal(exc: ModeRefused) -> JSONResponse:
    status, message, extra = refusal_for(exc)
    return _refuse(status, message, **extra)


def _modes_or_503() -> list[dict[str, Any]]:
    try:
        return load_kiosk_modes()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        log.error("The kiosk couldn't read config/modes.yaml: %s", exc)
        raise HTTPException(status_code=503, detail=RESTING) from None


# ---- routes ------------------------------------------------------------------------------------------------------
def _likely_plan(engine: Any, mode_id: str, current: ModePlan | None) -> ModePlan | None:
    """The plan the engine shows (or would pick now) for `mode_id`: the tier decides how the kiosk draws it."""
    if current is not None and current.mode_id == mode_id:
        return current
    compile_best = getattr(engine, "compile_best", None)
    if compile_best is None:
        return None
    try:
        plan, _why = compile_best(mode_id)
    except Exception:  # noqa: BLE001 - the mode's primary picture is the fallback
        return None
    return plan if isinstance(plan, ModePlan) else None


@router.get("/api/kiosk/modes")
def modes(request: Request) -> JSONResponse:
    listing = _modes_or_503()
    services = services_of(request)
    engine = services.extras.get(ENGINE_KEY)
    if engine is not None:
        try:
            state = engine.state()
        except Exception:  # noqa: BLE001 - the list still serves; the state call will say "resting"
            state = None
        if state is not None and state.mode and state.feature:
            for mode in listing:
                if mode["id"] == state.mode:
                    mode["feature"] = state.feature
        current = _current_plan(engine)
        try:
            specs = _read_yaml(MODES_FILE)["modes"]
        except (OSError, ValueError, yaml.YAMLError):
            specs = {}
        for mode in listing:
            plan = _likely_plan(engine, mode["id"], current)
            if plan is None:
                continue
            mode["tier"] = str(plan.tier)
            mode["pattern"] = (
                _pattern(specs.get(mode["id"]) or {}, mode["tier"]) if specs else mode["pattern"]
            )
            static = mode["module_colours"] if mode["tier"] == str(Tier.T3) else None
            mode["module_colours"] = module_colours(services, plan) or static
    return JSONResponse(listing, headers=NO_STORE)


@router.get("/api/kiosk/state")
def state(request: Request) -> JSONResponse:
    engine = _engine(request)
    return JSONResponse(state_json(services_of(request), _engine_state(engine), engine), headers=NO_STORE)


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
    elif new_state.refused:  # conductor/canopy.py reports refusals in the state, never by raising
        return _refused_state(engine, new_state, services.clock.now())
    return JSONResponse(state_json(services, new_state, engine), headers=NO_STORE)
