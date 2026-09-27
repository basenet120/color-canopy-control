"""/ops controls, the canopy's desired-vs-reported heads, the 7 am checklist and the log tail (ops only).

Spec: the build prompt's P3 `/ops` bullet and 07 §10. The canopy engine is `services.extras["canopy"]`
(`CanopyEngineAPI`, conductor/engine_api.py); every control here is one engine call with `source="ops"`, and
answers 503 with a plain sentence when the engine isn't running. Nothing here writes to a device itself.

- `POST /api/canopy/reassert`        re-assert: the engine resends only modules whose readback differs (07 §6)
- `POST /api/canopy/pause`           `{paused}` pause / resume guest input (the kiosk shows "resting")
- `POST /api/canopy/safe-look`       Lowcountry Calm at its strand technique, all modules, now
- `POST /api/canopy/commission`      write T1 slot contents and record readback hashes (the engine rate-limits)
- `POST /api/canopy/mode`            `{mode}` an ops mode change (no guest cooldown)
- `GET  /api/canopy/state`           CanopyState + the mode list for the picker
- `GET  /api/canopy/heads`           all 270 heads: position (per-device calibration), desired and reported colour
- `GET  /api/ops/calibration`        the nine modules' wire-order calibration (the /ops/calibrate/ hub)
- `GET  /api/ops/checklist[?client_ms=]`  the 7 am checklist, each item pass / warn / fail with a plain fix
- `POST /api/ops/checklist/test-mode-change`  `{mode?, restore?}` one ops mode change, timed to READY (10 s)
- `GET  /api/ops/logs[?since=&limit=]`  the log tail (redacted; see conductor/logring.py)

Every POST must be JSON from this site (same rule as the module page, D17): a cross-site form can't pause the
canopy with the ops password cached in someone's browser. Every control publishes `{"kind": "control", ...}` on
WS topic "ops" so other open dashboards refresh.

"Reported" colours are the cloud's REST readback of each strand rendered per head (`render.head_colors`): they
prove what Hubspace stores, not what the pucks show (07 §5). The payload says so, and says SIMULATED in sim.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from conductor.drivers.base import StateRow
from conductor.engine_api import PlanError
from conductor.gate import Purpose
from conductor.geometry import build_layout, load_canopy_config
from conductor.logring import installed as installed_logring
from conductor.module.layout import calibration_for, module_map, shown_corner
from conductor.redact import redact
from conductor.render import head_colors
from conductor.roles_api import Principal
from conductor.routes.accounts import same_origin
from conductor.routes.deps import require_ops, services_of
from conductor.rows import FRAME_CLASS, SLOTS, slot_hash

log = logging.getLogger(__name__)

ENGINE_KEY = "canopy"
LOGRING_KEY = "logring"
TEST_RESULT_KEY = "ops_checklist_test"
NO_ENGINE = (
    "The canopy engine isn't running, so the canopy can't be controlled from here. Restart the Conductor; if "
    "this stays, the log tail below says why."
)
NO_LOGRING = "The log tail isn't running on this Conductor (it starts with the app)."
NO_STORE = {"Cache-Control": "no-store"}

MATCH_TOLERANCE = 12  # per channel, 0–255: rendering and rounding differences aren't a mismatch
TEST_WINDOW_S = 10.0  # 05 acceptance: every change within 10 s
TEST_POLL_S = 0.25
TEST_FRESH_S = 12 * 3600  # a test mode change older than this doesn't count for today's checklist
CLOCK_WARN_MS = 2000
CLOCK_FAIL_MS = 30000
TOKEN_STALE_S = (
    600  # a live sign-in that hasn't refreshed for this long is suspect (it refreshes ~every 2 min)
)
THROTTLE_WINDOW_S = 600
TIMESYNC_FLAG = Path("/run/systemd/timesync/synchronized")
LOG_LIMIT_MAX = 500

STATUS_ORDER = {"pass": 0, "warn": 1, "fail": 2}


def _no_cross_site(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    headers = request.headers
    if ("origin" in headers or "referer" in headers) and not same_origin(headers):
        raise HTTPException(status_code=403, detail="Refused: this request came from another site.")
    media_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(status_code=415, detail="Send JSON (Content-Type: application/json).")


router = APIRouter(dependencies=[Depends(_no_cross_site)])
Ops = Annotated[Principal, Depends(require_ops)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PauseBody(_Body):
    paused: bool


class ModeBody(_Body):
    mode: str = Field(min_length=1, max_length=64)


class TestModeBody(_Body):
    mode: str | None = Field(None, max_length=64)
    restore: bool = True


# ---- helpers -------------------------------------------------------------------------------------------------
def engine_or_none(services: Any) -> Any | None:
    return services.extras.get(ENGINE_KEY)


def engine_of(services: Any) -> Any:
    engine = engine_or_none(services)
    if engine is None:
        raise HTTPException(status_code=503, detail=NO_ENGINE)
    return engine


def load_modes(config_dir: Path) -> dict:
    """modes.yaml (the six Lowcountry Modes as data)."""
    with open(Path(config_dir) / "modes.yaml") as f:
        return yaml.safe_load(f) or {}


def mode_list(services: Any) -> list[dict]:
    try:
        modes = load_modes(services.config_dir).get("modes") or {}
    except (OSError, yaml.YAMLError):
        return []
    return [
        {"id": mode_id, "label": m.get("label", mode_id), "blurb": m.get("blurb", ""), "slot": m.get("slot")}
        for mode_id, m in modes.items()
    ]


def _canopy_cfg(services: Any) -> dict:
    path = Path(services.config_dir) / "canopy.yaml"
    return load_canopy_config(path) if path.exists() else load_canopy_config()


def _engine_address(services: Any, device_id: str) -> tuple[str | None, str | None]:
    try:
        return services.target(device_id, Purpose.ENGINE).address, None
    except LookupError as exc:
        return None, str(exc)


def _rows(snapshot_rows: list[dict] | None) -> list[StateRow] | None:
    if snapshot_rows is None:
        return None
    return [StateRow.from_wire(r) for r in snapshot_rows]


def _rgb(c: Any) -> dict | None:
    if not isinstance(c, dict):
        return None
    try:
        return {k: max(0, min(255, round(float(c.get(k, 0))))) for k in ("r", "g", "b")}
    except (TypeError, ValueError):
        return None


def _matches(a: dict | None, b: dict | None) -> bool | None:
    if a is None or b is None:
        return None
    return all(abs(a[k] - b[k]) <= MATCH_TOLERANCE for k in ("r", "g", "b"))


def _lock_owner(services: Any, entry: Any, address: str | None) -> str | None:
    for candidate in dict.fromkeys([address, entry.sim_address, entry.metadevice_id]):
        if candidate and (lease := services.locks.holder(candidate)):
            return lease.owner
    return None


def _caps(services: Any, device_id: str) -> Any:
    return ((services.extras.get("capabilities") or {}).get(device_id) or {}).get("caps")


def _state_json(engine: Any) -> dict | None:
    if engine is None:
        return None
    try:
        return engine.state().to_json()
    except Exception:
        log.exception("canopy engine state() failed")
        return None


def _publish(services: Any, action: str, **fields: Any) -> None:
    try:
        services.hub.publish(
            "ops", {"kind": "control", "action": action, "at": services.clock.now(), **fields}
        )
    except Exception:
        log.exception("ops publish failed")


async def _engine_call(what: str, call: Callable[[], Any]) -> Any:
    """Run one engine call (sync or async); refusals become plain 409s, anything unexpected a plain 500."""
    try:
        result = call()
        return await result if inspect.isawaitable(result) else result
    except HTTPException:
        raise
    except (PlanError, ValueError, LookupError) as exc:
        raise HTTPException(status_code=409, detail=str(exc) or f"The engine refused to {what}.") from None
    except Exception:
        log.exception("canopy engine failed to %s", what)
        raise HTTPException(
            status_code=500,
            detail=f"The canopy engine hit an unexpected error trying to {what}. The log tail on /ops has details.",
        ) from None


def _known_mode(services: Any, mode: str) -> None:
    known = [m["id"] for m in mode_list(services)]
    if known and mode not in known:
        raise HTTPException(
            status_code=400, detail=f"There's no mode called {mode!r}. Choose one of: {', '.join(known)}."
        )


# ---- controls ------------------------------------------------------------------------------------------------
@router.post("/api/canopy/reassert")
async def reassert(request: Request, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    engine = engine_of(services)
    state = await _engine_call("re-assert the canopy", lambda: engine.reassert_all(source="ops"))
    _publish(services, "reassert")
    return JSONResponse(
        {
            "ok": True,
            "message": "Re-asserting: any module whose readback differs from its look gets the look again.",
            "state": state.to_json(),
        },
        headers=NO_STORE,
    )


@router.post("/api/canopy/pause")
async def pause(request: Request, body: PauseBody, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    engine = engine_of(services)
    state = await _engine_call(
        "pause guest input" if body.paused else "resume guest input",
        lambda: engine.pause_guest_input(body.paused, source="ops"),
    )
    _publish(services, "pause" if body.paused else "resume")
    message = (
        "Guest input paused: the kiosk shows the canopy is resting and can't change it."
        if body.paused
        else "Guest input resumed: guests can choose a mood again."
    )
    return JSONResponse({"ok": True, "message": message, "state": state.to_json()}, headers=NO_STORE)


@router.post("/api/canopy/safe-look")
async def safe_look(request: Request, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    engine = engine_of(services)
    state = await _engine_call("set the safe look", lambda: engine.safe_look(source="ops"))
    _publish(services, "safe_look")
    return JSONResponse(
        {
            "ok": True,
            "message": "Safe look sent: Lowcountry Calm on all nine modules.",
            "state": state.to_json(),
        },
        headers=NO_STORE,
    )


@router.post("/api/canopy/commission")
async def commission(request: Request, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    engine = engine_of(services)
    result = await _engine_call("commission the slots", lambda: engine.commission(source="ops"))
    _publish(services, "commission")
    return JSONResponse(
        {"ok": True, "result": result, "state": _state_json(engine)},
        headers=NO_STORE,
    )


@router.post("/api/canopy/mode")
async def change_mode(request: Request, body: ModeBody, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    engine = engine_of(services)
    _known_mode(services, body.mode)
    state = await _engine_call("change the mode", lambda: engine.request_mode(body.mode, source="ops"))
    _publish(services, "mode", mode=body.mode)
    return JSONResponse({"ok": True, "state": state.to_json()}, headers=NO_STORE)


@router.get("/api/canopy/state")
async def canopy_state(request: Request, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    engine = engine_of(services)
    state = await _engine_call("report its state", engine.state)
    return JSONResponse({**state.to_json(), "modes": mode_list(services)}, headers=NO_STORE)


# ---- heads: desired vs reported ------------------------------------------------------------------------------
def readback_note(simulated: bool) -> str:
    if simulated:
        return (
            "SIMULATED: 'reported' is the simulator's readback. On the real canopy it is what the Hubspace cloud "
            "reports each strand stores (REST readback) — proof the cloud stored the look, not that the lights "
            "show it."
        )
    return (
        "'Reported' is what the Hubspace cloud reports each strand stores (REST readback), drawn per head. It "
        "proves the cloud stored the look; whether every light applied it is not verified — look up to be sure."
    )


def canopy_heads(services: Any) -> dict:
    """All heads with positions (honouring each module's calibration), desired and reported colours, and a
    per-module status. Works without the engine (desired then comes from the tracker, if anything was sent)."""
    cfg = _canopy_cfg(services)
    module_cfg = cfg["module"]
    mw, mh = module_cfg["width_in"], module_cfg["height_in"]
    engine = engine_or_none(services)
    state = _state_json(engine)
    plan = None
    if engine is not None:
        try:
            plan = engine.current_plan()
        except Exception:
            log.exception("canopy engine current_plan() failed")
    engine_modules = (state or {}).get("modules") or {}
    entries = {e.module: e for e in services.registry.canopy_modules()}
    default_heads = build_layout(cfg)
    grid: dict[str, dict] = {}
    for h in default_heads:
        grid.setdefault(h.module, {"row": h.row, "col": h.col, "orientation": h.orientation, "heads": []})
        grid[h.module]["heads"].append(h)

    modules_out: list[dict] = []
    heads_out: list[dict] = []
    totals = {"match": 0, "mismatch": 0, "unknown": 0}
    for module_id in sorted(grid, key=lambda m: (grid[m]["row"], grid[m]["col"])):
        g = grid[module_id]
        entry = entries.get(module_id)
        ox, oy = g["col"] * mw, g["row"] * mh
        if entry is None:
            positions = [(h.x, h.y) for h in sorted(g["heads"], key=lambda h: h.index)]
            orientation, calibrated = g["orientation"], False
            record = {
                "device_id": None,
                "label": module_id,
                "status": "unknown",
                "reason": "No device is registered here.",
            }
            desired: list[dict | None] = [None] * len(positions)
            reported: list[dict | None] = [None] * len(positions)
            desired_source, reported_at, lock_owner, simulated = None, None, None, None
        else:
            cal = calibration_for(cfg, entry)
            mmap = module_map(cfg, cal)
            positions = [(ox + hd["x"], oy + hd["y"]) for hd in mmap["heads"]]
            orientation, calibrated = cal.orientation, cal.calibrated
            n = len(positions)
            address, address_error = _engine_address(services, entry.id)
            snap = services.tracker.snapshot(address) if address else None
            desired, desired_source = [None] * n, None
            target = plan.modules.get(entry.id) if plan is not None else None
            if target is not None and target.preview:
                desired = [_rgb(c) for c in list(target.preview)[:n]]
                desired += [None] * (n - len(desired))
                desired_source = "plan"
            elif snap and snap["desired"] is not None:
                desired = head_colors(_rows(snap["desired"]), n, _caps(services, entry.id))
                desired_source = "tracker"
            reported = [None] * n
            reported_at = None
            if snap and snap["reported"] is not None:
                reported = head_colors(_rows(snap["reported"]), n, _caps(services, entry.id))
                reported_at = snap["reported_at"]
            engine_record = engine_modules.get(entry.id) or {}
            status = engine_record.get("status") or (snap or {}).get("status") or "unknown"
            reason = engine_record.get("reason") or (snap or {}).get("reason") or address_error or ""
            record = {
                "device_id": entry.id,
                "label": entry.label or entry.id,
                "status": status,
                "reason": reason,
            }
            lock_owner = _lock_owner(services, entry, address)
            simulated = bool(services.engine_driver.simulated)
        m_counts = {"match": 0, "mismatch": 0, "unknown": 0}
        for i, (x, y) in enumerate(positions):
            d, r = desired[i], reported[i]
            match = _matches(d, r)
            key = "unknown" if match is None else ("match" if match else "mismatch")
            m_counts[key] += 1
            heads_out.append(
                {
                    "module": module_id,
                    "device_id": record["device_id"],
                    "index": i,
                    "x": round(x, 3),
                    "y": round(y, 3),
                    "desired": d,
                    "reported": r,
                    "match": match,
                }
            )
        for k, v in m_counts.items():
            totals[k] += v
        modules_out.append(
            {
                "id": module_id,
                **record,
                "row": g["row"],
                "col": g["col"],
                "orientation": orientation,
                "calibrated": calibrated,
                "lock_owner": lock_owner,
                "desired_source": desired_source,
                "reported_at": reported_at,
                "heads": len(positions),
                "counts": m_counts,
                "simulated": simulated,
            }
        )
    simulated = bool(services.engine_driver.simulated)
    return {
        "simulated": simulated,
        "engine": engine is not None,
        "engine_message": None if engine is not None else NO_ENGINE,
        "mode": (state or {}).get("mode"),
        "label": (state or {}).get("label"),
        "tier": (state or {}).get("tier"),
        "feature": (state or {}).get("feature"),
        "phase": (state or {}).get("phase"),
        "scheme": cfg["orientation_scheme"],
        "canopy": {
            "width_in": cfg["width_in"],
            "height_in": cfg["height_in"],
            "module_width_in": mw,
            "module_height_in": mh,
            "pitch_in": module_cfg["pitch_in"],
        },
        "readback_note": readback_note(simulated),
        "match_tolerance": MATCH_TOLERANCE,
        "counts": {"heads": len(heads_out), **totals},
        "modules": modules_out,
        "heads": heads_out,
    }


@router.get("/api/canopy/heads")
async def heads(request: Request, _ops: Ops) -> JSONResponse:
    return JSONResponse(canopy_heads(services_of(request)), headers=NO_STORE)


# ---- calibration hub -----------------------------------------------------------------------------------------
@router.get("/api/ops/calibration")
async def calibration(request: Request, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    cfg = _canopy_cfg(services)
    out = []
    for entry in services.registry.canopy_modules():
        cal = calibration_for(cfg, entry)
        mmap = module_map(cfg, cal)
        row, col = int(entry.module[1]), int(entry.module[3])
        out.append(
            {
                "device_id": entry.id,
                "label": entry.label or entry.id,
                "module": entry.module,
                "row": row,
                "col": col,
                "orientation": cal.orientation,
                "cable_path": cal.cable_path,
                "start_corner": cal.start_corner,
                "start_corner_as_shown": shown_corner(cal.start_corner, cal.orientation),
                "explicit_wire_order": cal.wire_order is not None,
                "calibrated": cal.calibrated,
                "longest_hop_in": mmap["longest_hop_in"],
                "hops_fit": mmap["hops_fit"],
                "head0": {"x": mmap["heads"][0]["x"], "y": mmap["heads"][0]["y"]},
                "polyline": mmap["polyline"],  # display frame (as the panel hangs), index order
                "link": f"/ops/module/?device={entry.id}&tab=calibrate",
                "lock_owner": _lock_owner(services, entry, _engine_address(services, entry.id)[0]),
            }
        )
    done = sum(1 for m in out if m["calibrated"])
    return JSONResponse(
        {
            "modules": out,
            "calibrated": done,
            "total": len(out),
            "simulated": bool(services.engine_driver.simulated),
            "default_cable_path": cfg.get("cable_path"),
            "module_width_in": cfg["module"]["width_in"],
            "module_height_in": cfg["module"]["height_in"],
        },
        headers=NO_STORE,
    )


# ---- the 7 am checklist (07 §10) -----------------------------------------------------------------------------
def _item(item_id: str, title: str, status: str, detail: str, fix: str = "", note: str = "") -> dict:
    return {"id": item_id, "title": title, "status": status, "detail": detail, "fix": fix, "note": note}


async def ntp_status() -> tuple[bool | None, str]:
    """(synced?, how we know). None when this box can't tell (e.g. inside a container without the flag)."""
    try:
        if TIMESYNC_FLAG.exists():
            return True, "systemd-timesyncd reports the clock synchronized"
    except OSError:
        pass
    exe = shutil.which("timedatectl")
    if exe is None:
        return None, "the box's NTP status isn't visible to the Conductor"
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "show",
            "-p",
            "NTPSynchronized",
            "--value",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 3.0)
    except (OSError, TimeoutError):
        return None, "timedatectl didn't answer"
    value = out.decode("utf-8", "replace").strip().lower()
    if value in ("yes", "no"):
        return value == "yes", f"timedatectl says NTPSynchronized={value}"
    return None, "timedatectl gave no NTP status"


async def _check_clock(services: Any, client_ms: float | None) -> dict:
    synced, how = await ntp_status()
    server_ms = services.clock.now() * 1000.0
    skew_ms = None if client_ms is None else server_ms - client_ms
    title = "Clock synced"
    parts = []
    if skew_ms is not None:
        parts.append(f"The box and this browser differ by {abs(skew_ms) / 1000:.1f} s.")
    parts.append(f"NTP: {how}.")
    detail = " ".join(parts)
    ntp_fix = (
        "On the box run `timedatectl` and make sure 'System clock synchronized: yes' (fix with "
        "`sudo timedatectl set-ntp true` and check the network). A lagging clock may get writes accepted and dropped."
    )
    if synced is False:
        return _item("clock", title, "fail", detail, ntp_fix)
    big = skew_ms is not None and abs(skew_ms) > CLOCK_FAIL_MS
    small = skew_ms is not None and abs(skew_ms) > CLOCK_WARN_MS
    if big and synced:
        return _item(
            "clock",
            title,
            "warn",
            detail,
            "The box is synced, so this phone or tablet's clock is off: set it to automatic time.",
        )
    if big:
        return _item("clock", title, "fail", detail, "Check the box's clock first: " + ntp_fix)
    if synced is None:
        return _item(
            "clock",
            title,
            "warn",
            detail,
            "Check the box's clock: run `timedatectl` on the box (inside Docker, mount /run/systemd/timesync "
            "read-only so the Conductor can see it).",
        )
    if small:
        return _item(
            "clock", title, "warn", detail, "A few seconds apart: check this device's automatic time."
        )
    if skew_ms is None:
        return _item(
            "clock", title, "pass", detail, note="Open this page from a phone to compare its clock too."
        )
    return _item("clock", title, "pass", detail)


def _check_accounts(services: Any) -> list[dict]:
    try:
        statuses = services.accounts.status()
    except Exception as exc:  # noqa: BLE001
        return [
            _item(
                "token",
                "Hubspace sign-in",
                "fail",
                f"Couldn't read account status: {exc}",
                "Restart the Conductor.",
            )
        ]
    if getattr(services.accounts, "simulated", False) or services.engine_driver.simulated:
        return [
            _item(
                "token",
                "Hubspace sign-in",
                "pass",
                "Simulator: the show engine runs on the simulator here, so no Hubspace sign-in is needed.",
            )
        ]
    labels = services.registry.accounts()
    used = {e.account for e in services.registry.devices}
    canopy_accounts = {e.account for e in services.registry.canopy_modules()}
    now = services.clock.now()
    items = []
    for s in statuses:
        if s.account not in used:
            continue
        label = (labels.get(s.account) or {}).get("label", s.account)
        title = f"Sign-in: {label}"
        key = f"token-{s.account}"
        critical = s.account in canopy_accounts
        if s.needs_reauth:
            items.append(
                _item(
                    key,
                    title,
                    "fail",
                    "Hubspace needs a new sign-in for this account.",
                    "Sign in again on /ops/accounts (needs the password and the account's inbox for the emailed code).",
                )
            )
            continue
        if not s.connected:
            items.append(
                _item(
                    key,
                    title,
                    "fail" if critical else "warn",
                    f"Not connected: {s.last_error or 'still trying'}.",
                    "Open /ops/accounts; if it doesn't reconnect in a minute, sign in again.",
                )
            )
            continue
        age = f"signed in {_ago(s.token_age_s)} ago" if s.token_age_s is not None else "signed in"
        stale = s.last_refresh_at is not None and now - float(s.last_refresh_at) > TOKEN_STALE_S
        if stale:
            items.append(
                _item(
                    key,
                    title,
                    "warn",
                    f"Connected ({age}), but the sign-in hasn't refreshed for "
                    f"{_ago(now - float(s.last_refresh_at))}.",
                    "Watch it for two minutes; if it doesn't refresh, sign in again on /ops/accounts.",
                )
            )
        else:
            items.append(_item(key, title, "pass", f"Connected; {age}."))
    if not items:
        items.append(
            _item(
                "token",
                "Hubspace sign-in",
                "warn",
                "No account has registered devices yet.",
                "Register devices on /ops/accounts.",
            )
        )
    return items


def _ago(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    s = max(0, round(float(seconds)))
    if s < 120:
        return f"{s} s"
    if s < 7200:
        return f"{round(s / 60)} min"
    return f"{round(s / 3600)} h"


def _module_names(names: list[str]) -> str:
    return ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else (names[0] if names else "")


def _check_modules(services: Any, state: dict | None) -> dict:
    entries = services.registry.canopy_modules()
    engine_modules = (state or {}).get("modules") or {}
    offline, attention, waiting, ok = [], [], [], []
    for entry in entries:
        name = entry.label or entry.id
        address, error = _engine_address(services, entry.id)
        if address is None:
            attention.append(f"{name} ({error})")
            continue
        snap = services.tracker.snapshot(address)
        engine_record = engine_modules.get(entry.id) or {}
        holder = _lock_owner(services, entry, address)
        if snap["available"] is False:
            offline.append(name)
        elif holder:
            # the tracker would say "ready" for the module page's own look; the canopy can't drive it
            attention.append(f"{name} (open on the module page by {holder}: close that page to hand it back)")
        elif engine_record.get("status") == "needs_attention":
            attention.append(f"{name} ({engine_record.get('reason') or 'needs attention'})")
        elif snap["status"] == "needs_attention":
            attention.append(f"{name} ({snap['reason']})")
        elif snap["reported_at"] is None or snap["status"] != "ready":
            waiting.append(name)
        else:
            ok.append(name)
    total = 9
    detail = f"{len(ok)} of {total} modules answer readback and show their look."
    if len(entries) < total:
        return _item(
            "modules",
            f"{len(ok)}/{total} modules online",
            "fail",
            f"Only {len(entries)} canopy modules are registered.",
            "Register the missing modules on /ops/accounts (one device per canopy position).",
        )
    if offline:
        return _item(
            "modules",
            f"{len(ok)}/{total} modules online",
            "fail",
            f"{detail} Offline: {_module_names(offline)}.",
            "Check those controllers' power and Wi-Fi (the controller's light), then press Re-assert all.",
        )
    if attention:
        return _item(
            "modules",
            f"{len(ok)}/{total} modules online",
            "fail",
            f"{detail} Needs attention: {'; '.join(attention)}.",
            "Open the module page for each one (the canopy grid above), then press Re-assert all.",
        )
    if waiting:
        return _item(
            "modules",
            f"{len(ok)}/{total} modules online",
            "warn",
            f"{detail} No confirmed readback yet: {_module_names(waiting)}.",
            "Press Re-assert all (or run the test mode change below): that reads every module back.",
        )
    return _item("modules", f"{total}/{total} modules online", "pass", detail)


async def _check_heads(services: Any) -> dict:
    from conductor.routes.devices import _capabilities  # cached profile reads (a GET, never a write)

    entries = services.registry.canopy_modules()
    records = await _capabilities(services, [e.id for e in entries], False)
    problems, unread = [], []
    for entry in entries:
        name = entry.label or entry.id
        record = records.get(entry.id) or {}
        caps = record.get("caps")
        if caps is None:
            unread.append(f"{name} ({record.get('error') or 'not read'})")
            continue
        if problem := caps.head_count_problem(entry.expected_heads):
            problems.append(f"{name}: {problem}")
    visual = (
        "Whether a dark string changes what Hubspace reports is unverified, so also look up: all 270 heads "
        "should light."
    )
    if problems:
        return _item(
            "heads",
            "Head counts",
            "fail",
            "; ".join(problems) + ".",
            "Check that every string is plugged in, then power-cycle that controller (it counts pucks at power-up). "
            "Never cut the strings.",
            visual,
        )
    if unread:
        return _item(
            "heads",
            "Head counts",
            "warn",
            f"Couldn't read the profile of {'; '.join(unread)}.",
            "Press Refresh on the device list; if it stays, check the account on /ops/accounts.",
            visual,
        )
    return _item(
        "heads",
        "Head counts",
        "pass",
        f"All {len(entries)} modules report their expected heads.",
        note=visual,
    )


def _needed_slots(services: Any) -> list[str]:
    slots = {m["slot"] for m in mode_list(services) if m.get("slot") in SLOTS}
    return sorted(slots)


def _check_slots(services: Any) -> dict:
    needed = _needed_slots(services)
    missing, unread, drifted = [], [], []
    for entry in services.registry.canopy_modules():
        name = entry.label or entry.id
        recorded = dict(entry.slot_hashes or {})
        if any(s not in recorded for s in needed):
            missing.append(name)
        address, _ = _engine_address(services, entry.id)
        snap = services.tracker.snapshot(address) if address else None
        reported = {
            r["functionInstance"]: r["value"]
            for r in ((snap or {}).get("reported") or [])
            if r.get("functionClass") == FRAME_CLASS
        }
        for slot, want in sorted(recorded.items()):
            if slot not in reported:
                unread.append(f"{name} {slot}")
                continue
            if slot_hash(reported[slot]) != want:
                drifted.append(f"{name} {slot}")
    title = "Slot hashes"
    if drifted:
        return _item(
            "slots",
            title,
            "fail",
            f"Slots differ from what was commissioned: {', '.join(drifted)}.",
            "Someone may have edited them in the Hubspace app. Press Commission slots (it rewrites a slot at most "
            "once per 10 min); for hand-painted (T2) slots, repaint them in the app and record them again.",
        )
    if missing:
        return _item(
            "slots",
            title,
            "warn",
            f"No recorded slot hash for {_module_names(missing)} ({', '.join(needed)}).",
            "Press Commission slots. (Fine to skip if Sunset, Garden and Golden Hour run at module level (T3).)",
        )
    if unread:
        return _item(
            "slots",
            title,
            "warn",
            f"Hashes recorded, but no readback of: {', '.join(unread[:6])}{'…' if len(unread) > 6 else ''}.",
            "Show Rainbow Row Sunset, Garden Gathering and Golden Hour once each (or press Re-assert all) so every "
            "slot is read back.",
        )
    return _item("slots", title, "pass", "Every commissioned slot reads back with its recorded hash.")


def _check_budget(services: Any) -> dict:
    executor = services.executor_for(services.engine_driver)
    budget = executor.budget()
    interval = float(budget.get("min_write_interval_s") or 1.0)
    cap = max(1, round(budget.get("window_s", 60.0) / interval))
    engine_addresses = {}
    for entry in services.registry.canopy_modules():
        address, _ = _engine_address(services, entry.id)
        if address:
            engine_addresses[address] = entry.label or entry.id
    busiest_name, busiest = None, 0
    for address, gauge in (budget.get("devices") or {}).items():
        if address in engine_addresses and gauge["writes_60s"] >= busiest:
            busiest, busiest_name = gauge["writes_60s"], engine_addresses[address]
    since = services.clock.now() - THROTTLE_WINDOW_S
    throttles = [
        e
        for e in services.telemetry.recent(kind="attempt", since=since, limit=5000)
        if e.get("outcome") == "throttled" and e.get("device_id") in engine_addresses
    ]
    queue = budget.get("queue_depth", 0)
    detail = (
        f"Busiest module: {busiest} of {cap} writes in the last minute"
        f"{f' ({busiest_name})' if busiest_name and busiest else ''}. "
        f"{len(throttles)} throttled request(s) in the last 10 min. Queue: {queue}."
    )
    if busiest >= 0.9 * cap:
        return _item(
            "budget",
            "Rate budget headroom",
            "fail",
            detail,
            "Something is writing constantly: pause guest input, check the log tail and the module pages.",
        )
    if busiest >= 0.5 * cap or len(throttles) >= 3:
        return _item(
            "budget",
            "Rate budget headroom",
            "warn",
            detail,
            "Hubspace is pushing back. Let the canopy settle for a few minutes before the next test; never try "
            "to work around throttling.",
        )
    return _item("budget", "Rate budget headroom", "pass", detail)


def _check_test_result(services: Any) -> dict:
    last = services.extras.get(TEST_RESULT_KEY)
    title = "Test mode change"
    if not last or services.clock.now() - last["at"] > TEST_FRESH_S:
        return _item(
            "test_mode_change",
            title,
            "warn",
            "Not run yet today.",
            "Press Run test mode change: it changes the mode once and times all nine modules to Ready.",
        )
    return _item(
        "test_mode_change",
        title,
        "pass" if last["ok"] else "fail",
        last["message"],
        "" if last["ok"] else "Open the modules that didn't reach Ready, then run the test again.",
    )


def _check_guest_input(services: Any, state: dict | None) -> dict:
    title = "Guest input"
    if state is None:
        return _item("guest_input", title, "fail", NO_ENGINE, "Restart the Conductor.")
    if not state.get("guest_input"):
        return _item(
            "guest_input",
            title,
            "warn",
            "Guest input is paused: the kiosk shows the canopy is resting.",
            "Press Resume guest input before doors open.",
        )
    detail = "On: guests can choose a mood."
    if state.get("phase") == "needs_attention":
        return _item(
            "guest_input", title, "warn", f"{detail} But the canopy needs attention: {state.get('message')}"
        )
    return _item("guest_input", title, "pass", detail)


async def build_checklist(services: Any, client_ms: float | None) -> dict:
    engine = engine_or_none(services)
    state = _state_json(engine)
    items = [await _check_clock(services, client_ms)]
    items += _check_accounts(services)
    items.append(_check_modules(services, state))
    items.append(await _check_heads(services))
    items.append(_check_slots(services))
    items.append(_check_budget(services))
    items.append(_check_test_result(services))
    items.append(_check_guest_input(services, state))
    worst = max((STATUS_ORDER[i["status"]] for i in items), default=0)
    overall = next(k for k, v in STATUS_ORDER.items() if v == worst)
    counts = {k: sum(1 for i in items if i["status"] == k) for k in STATUS_ORDER}
    return {
        "checked_at": services.clock.now(),
        "server_ms": round(services.clock.now() * 1000),
        "overall": overall,
        "counts": counts,
        "items": items,
        "simulated": bool(services.engine_driver.simulated),
        "engine": engine is not None,
    }


@router.get("/api/ops/checklist")
async def checklist(request: Request, _ops: Ops, client_ms: float | None = None) -> JSONResponse:
    return JSONResponse(await build_checklist(services_of(request), client_ms), headers=NO_STORE)


def _pick_test_mode(services: Any, current: str | None) -> str:
    modes = [m["id"] for m in mode_list(services)]
    idle = services.settings.kiosk.idle_mode
    if current != idle and idle in modes:
        return idle
    for candidate in ("goodnight-glow", *modes):
        if candidate in modes and candidate != current:
            return candidate
    raise HTTPException(status_code=409, detail="There's no other mode to change to.")


@router.post("/api/ops/checklist/test-mode-change")
async def test_mode_change(request: Request, body: TestModeBody, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    engine = engine_of(services)
    clock = services.clock
    before = engine.state()
    target = body.mode or _pick_test_mode(services, before.mode)
    _known_mode(services, target)
    if target == before.mode:
        raise HTTPException(
            status_code=400, detail="That mode is already showing; choose a different one to test a change."
        )
    start = clock.monotonic()
    await _engine_call("change the mode", lambda: engine.request_mode(target, source="ops"))
    ready_after: dict[str, float] = {}
    ids = [e.id for e in services.registry.canopy_modules()]
    state = engine.state()
    while True:
        elapsed = clock.monotonic() - start
        state = engine.state()
        if state.mode == target:
            for device_id in ids:
                status = (state.modules.get(device_id) or {}).get("status")
                if status == "ready" and device_id not in ready_after:
                    ready_after[device_id] = round(elapsed, 2)
        if (len(ready_after) == len(ids) and ids) or elapsed >= TEST_WINDOW_S:
            break
        await clock.sleep(min(TEST_POLL_S, max(0.0, TEST_WINDOW_S - elapsed)))
    elapsed = round(clock.monotonic() - start, 2)
    ok = bool(ids) and len(ready_after) == len(ids) and state.mode == target
    modules = []
    for entry in services.registry.canopy_modules():
        rec = state.modules.get(entry.id) or {}
        modules.append(
            {
                "device_id": entry.id,
                "label": entry.label or entry.id,
                "status": rec.get("status", "unknown"),
                "reason": rec.get("reason", ""),
                "ready_after_s": ready_after.get(entry.id),
            }
        )
    label = state.label or target
    if ok:
        slowest = max(ready_after.values())
        message = f"All {len(ids)} modules reached Ready on {label} within {slowest:.1f} s."
    else:
        late = [m["label"] for m in modules if m["ready_after_s"] is None]
        message = (
            f"{_module_names(late)} didn't reach Ready within {TEST_WINDOW_S:.0f} s of changing to {label}."
            if late
            else f"The canopy didn't settle on {label} within {TEST_WINDOW_S:.0f} s."
        )
    restored_to = None
    if body.restore and before.mode and before.mode != target and target != services.settings.kiosk.idle_mode:
        try:
            await engine.request_mode(before.mode, source="ops")
            restored_to = before.mode
        except Exception as exc:  # noqa: BLE001 - the test result stands; say the restore failed
            message += f" (Couldn't change back to {before.label or before.mode}: {exc})"
    result = {
        "ok": ok,
        "mode": target,
        "label": label,
        "elapsed_s": elapsed,
        "window_s": TEST_WINDOW_S,
        "modules": modules,
        "message": message,
        "restored_to": restored_to,
        "at": clock.now(),
        "simulated": bool(services.engine_driver.simulated),
    }
    services.extras[TEST_RESULT_KEY] = result
    services.telemetry.record(
        "alert", level="info" if ok else "warn", message=f"7 am test mode change: {message}"
    )
    _publish(services, "test_mode_change", ok=ok, message=message)
    return JSONResponse(result, headers=NO_STORE)


# ---- log tail ------------------------------------------------------------------------------------------------
@router.get("/api/ops/logs")
async def logs(request: Request, _ops: Ops, since: int | None = None, limit: int = 200) -> JSONResponse:
    ring = installed_logring()  # the process-wide ring (extras may hold one that was uninstalled)
    if ring is None:
        raise HTTPException(status_code=503, detail=NO_LOGRING)
    limit = max(1, min(LOG_LIMIT_MAX, limit))
    records = ring.records(since=since, limit=limit)
    for r in records:  # belt and braces: the ring already stores redacted text
        r["message"] = redact(r["message"])
        if r.get("exc"):
            r["exc"] = redact(r["exc"])
    return JSONResponse(
        {"records": records, "last_seq": ring.last_seq, "capacity": ring.capacity},
        headers=NO_STORE,
    )
