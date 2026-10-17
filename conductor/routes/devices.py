"""Devices and the ops summary (ops only; BUILD_PLAN "HTTP API", 07 §1, §6, §10).

- `GET /api/devices[?refresh=1]` — every enabled registry device: identity (id, label, role, account, module,
  vignette room + kind, orientation, expected heads, flags, metadevice id), where each purpose's writes go ({driver,
  simulated, address}), the tracker's status + reason for the engine address, who holds its lock, capabilities
  detected from the device's own profile, and any head-count problem (`conductor.framemap.head_check`; `mapped` is
  true when the kit's own controller map applies, D54, and `frame_length` is the controller's). Plus `groups`: the
  /ops device list's sections in order — canopy modules, bench, spares, then each vignette room (with its
  /vignette/<room>/ link).
- `GET /api/devices/{id}[?refresh=1]` — the same record plus the tracker's full snapshot.
- `GET /api/ops/summary` — version, "Needs attention" items, accounts, locks, the executor's rate budget per
  driver, and simulator statistics when the engine runs on the simulator. "Needs attention" includes the canopy
  resting while guest input is on, and the engine's and reconciler's alerts (conductor/alerts.py) — each alert item
  carries `alert` (its key) for `POST /api/ops/alerts/ack {"key"}`, which hides it until it happens again.

Capabilities are fetched lazily (a profile read through the bench target's driver, Purpose.BENCH — a GET, never a
write) and cached in `services.extras["capabilities"]`; `refresh=1` re-fetches. A failed fetch is cached for
`CAPS_ERROR_RETRY_S` so a broken connection isn't hammered by page refreshes. The summary never fetches.
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from conductor import __version__
from conductor.alerts import AlertBoard
from conductor.capabilities import Capabilities, detect
from conductor.framemap import HeadCheck, head_check, module_heads
from conductor.gate import GateDenied, Purpose
from conductor.roles_api import Principal
from conductor.routes.accounts import accounts_summary, same_origin
from conductor.routes.deps import require_ops, services_of

router = APIRouter()
Ops = Annotated[Principal, Depends(require_ops)]

CAPS_KEY = "capabilities"
CAPS_FETCH_TIMEOUT_S = 15.0
CAPS_ERROR_RETRY_S = 60.0


# ---- per-device pieces ---------------------------------------------------------------------------------------
def _purpose_target(services: Any, device_id: str, purpose: Purpose) -> dict:
    try:
        target = services.target(device_id, purpose)
    except LookupError as exc:
        return {"driver": None, "simulated": None, "address": None, "error": str(exc)}
    return {"driver": target.driver.name, "simulated": target.simulated, "address": target.address}


def _lock_holder(services: Any, entry: Any, addresses: list[str | None]) -> dict | None:
    for address in dict.fromkeys([*addresses, entry.sim_address, entry.metadevice_id]):
        if address and (lease := services.locks.holder(address)):
            return {**lease.to_json(), "address": address}
    return None


async def _fetch_capabilities(services: Any, device_id: str) -> dict:
    """Fetch (or reuse) one device's capabilities. Never raises: failures come back as `error`."""
    cache: dict[str, dict] = services.extras.setdefault(CAPS_KEY, {})
    try:
        target = services.target(device_id, Purpose.BENCH)
    except LookupError as exc:
        return {"caps": None, "error": str(exc), "simulated": None, "driver": None, "address": None}
    record = {
        "caps": None,
        "error": None,
        "simulated": target.simulated,
        "driver": target.driver.name,
        "address": target.address,
        "fetched_at": services.clock.now(),
        "fetched_mono": services.clock.monotonic(),
    }
    try:
        info = await asyncio.wait_for(
            target.driver.fetch_profile(target.address, purpose=Purpose.BENCH), CAPS_FETCH_TIMEOUT_S
        )
        record["caps"] = detect(info.functions, info.states)
    except GateDenied as denied:
        record["error"] = denied.reason
    except TimeoutError:
        record["error"] = "Reading the device's profile took too long. Try Refresh."
    except LookupError as exc:
        record["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - AferoRequestFailed and friends carry operator-safe messages
        record["error"] = f"Couldn't read the device's profile: {exc}"
    cache[device_id] = record
    return record


def _cached_capabilities(services: Any, device_id: str) -> dict | None:
    """The cached record if it still describes where the bench target points now, else None."""
    record = services.extras.get(CAPS_KEY, {}).get(device_id)
    if record is None:
        return None
    try:
        target = services.target(device_id, Purpose.BENCH)
    except LookupError:
        return None
    if (record["driver"], record["address"]) != (target.driver.name, target.address):
        return None
    if record["caps"] is None and services.clock.monotonic() - record["fetched_mono"] >= CAPS_ERROR_RETRY_S:
        return None
    return record


async def _capabilities(services: Any, device_ids: list[str], refresh: bool) -> dict[str, dict]:
    wiring = services.extras.get("show")
    if refresh and wiring is not None and hasattr(wiring, "request_refresh"):
        wiring.request_refresh(
            device_ids
        )  # the canopy engine reads the profiles again too (in the background)
    records: dict[str, dict] = {}
    todo = []
    for device_id in device_ids:
        cached = None if refresh else _cached_capabilities(services, device_id)
        if cached is None:
            todo.append(device_id)
        else:
            records[device_id] = cached
    fetched = await asyncio.gather(*(_fetch_capabilities(services, d) for d in todo))
    records.update(zip(todo, fetched, strict=True))
    return records


def _device_record(services: Any, entry: Any, caps_record: dict | None) -> dict:
    engine = _purpose_target(services, entry.id, Purpose.ENGINE)
    bench = _purpose_target(services, entry.id, Purpose.BENCH)
    snapshot = services.tracker.snapshot(engine["address"]) if engine["address"] else None
    caps: Capabilities | None = (caps_record or {}).get("caps")
    check = _head_check(services, entry, caps_record)
    md = entry.metadevice_id
    allowlist = services.gate.effective_allowlist()
    return {
        "id": entry.id,
        "label": entry.label or entry.id,
        "role": entry.role,
        "account": entry.account,
        "model": entry.model,
        "module": entry.module,
        "vignette": entry.vignette,
        "kind": entry.kind,
        "orientation": entry.orientation,
        "expected_heads": entry.expected_heads,
        "writable": entry.writable,
        "flags": entry.flags.model_dump(),
        "has_metadevice_id": bool(md),
        "metadevice_id": md,
        "allowlisted": bool(md) and md in allowlist,
        "purposes": {"engine": engine, "bench": bench},
        "simulated": engine["simulated"] is not False and bench["simulated"] is not False,
        "status": snapshot["status"] if snapshot else "unknown",
        "reason": (snapshot or {}).get("reason") or engine.get("error"),
        "problems": (snapshot or {}).get("problems", []),
        "lock": _lock_holder(services, entry, [engine["address"], bench["address"]]),
        "capabilities": caps.to_json() if caps else None,
        "capabilities_error": (caps_record or {}).get("error"),
        "capabilities_simulated": (caps_record or {}).get("simulated"),
        "capabilities_fetched_at": (caps_record or {}).get("fetched_at"),
        "head_count_problem": check.problem if check else None,
        "mapped": bool(check and check.mapped),
        "frame_length": caps.framebuffer_length if caps else None,
    }


def _head_check(services: Any, entry: Any, caps_record: dict | None) -> HeadCheck | None:
    """The mapping-aware head check for the device the capabilities were read from (None without capabilities)."""
    caps = (caps_record or {}).get("caps")
    if caps is None:
        return None
    return head_check(entry, caps_record.get("address"), caps, module_heads(services.config_dir))


STRAND_GROUPS = (
    ("canopy_module", "canopy", "Canopy modules"),
    ("bench", "bench", "Bench kit"),
    ("spare", "spares", "Spares"),
)


def _room_titles(services: Any) -> dict[str, str]:
    """Vignette room id → its space name, in config/vignettes.yaml order ({} when that file can't be read)."""
    try:
        from conductor.vignettes import VignetteService

        rooms = VignetteService.of(services).rooms.rooms
    except Exception:  # noqa: BLE001 - a bad vignettes.yaml is already a "Needs attention" line
        return {}
    return {room_id: (getattr(room, "space", None) or room_id) for room_id, room in rooms.items()}


def device_groups(services: Any, entries: list[Any]) -> list[dict]:
    """The /ops device list's sections: canopy modules, bench, spares (strands: module page, bench, head count),
    then each vignette room (its /vignette/<room>/ page)."""
    groups = []
    for role, key, title in STRAND_GROUPS:
        ids = [e.id for e in entries if e.role == role]
        if role == "canopy_module":
            ids = [e.id for e in sorted((e for e in entries if e.role == role), key=lambda e: e.module or "")]
        if ids:
            groups.append({"id": key, "title": title, "strand": True, "link": None, "devices": ids})
    titles = _room_titles(services)
    rooms = list(titles) + sorted(
        {e.vignette for e in entries if e.role == "vignette" and e.vignette} - set(titles)
    )
    for room in rooms:
        ids = [e.id for e in entries if e.role == "vignette" and e.vignette == room]
        if ids:
            groups.append(
                {
                    "id": f"vignette-{room}",
                    "title": titles.get(room) or room,
                    "strand": False,
                    "link": f"/vignette/{room}/",
                    "devices": ids,
                }
            )
    placed = {d for g in groups for d in g["devices"]}
    rest = [e.id for e in entries if e.id not in placed]
    if rest:
        groups.append(
            {"id": "other", "title": "Other devices", "strand": False, "link": None, "devices": rest}
        )
    return groups


# ---- routes --------------------------------------------------------------------------------------------------
@router.get("/api/devices")
async def list_devices(request: Request, _ops: Ops, refresh: bool = False) -> dict:
    services = services_of(request)
    entries = list(services.registry.devices)
    caps = await _capabilities(services, [e.id for e in entries], refresh)
    return {
        "simulated": bool(services.engine_driver.simulated),
        "mode": str(services.gate_settings.mode),
        "devices": [_device_record(services, e, caps.get(e.id)) for e in entries],
        "groups": device_groups(services, entries),
    }


@router.get("/api/devices/{device_id}")
async def get_device(request: Request, device_id: str, _ops: Ops, refresh: bool = False) -> dict:
    services = services_of(request)
    try:
        entry = services.registry.get(device_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="There's no registry device with that id.") from None
    caps = await _capabilities(services, [entry.id], refresh)
    record = _device_record(services, entry, caps.get(entry.id))
    engine_address = record["purposes"]["engine"]["address"]
    record["enabled"] = entry.enabled
    record["tracker"] = services.tracker.snapshot(engine_address) if engine_address else None
    return record


def runtime_problems(services: Any) -> list[str]:
    """Startup problems, the token store's (when this runtime could go live) and the role store's."""
    problems = list(services.problems)
    if services.gate_settings.live and not services.token_store.available:
        problems.append(services.token_store.problem)
    if services.roles.problem:
        problems.append(services.roles.problem)
    # The published looks (D68): read live, so a fixed problem leaves /ops at once (never a static startup line).
    from conductor.looks.live import runtime_problem_lines

    problems += runtime_problem_lines(services)
    return list(dict.fromkeys(p for p in problems if p))


def device_page(entry: Any) -> str:
    """Where /ops sends staff for one device (D34): a vignette device's room page, never the module page — opening
    that would take the device's exclusive lock away from the room's tablet (review V1). Strands get the module page."""
    if entry.role == "vignette":
        return f"/vignette/{entry.vignette}/" if entry.vignette else "/ops/"
    return f"/ops/module/?device={entry.id}"


def _attention(services: Any, accounts: dict) -> list[dict]:
    """Plain-English "Needs attention" items for the /ops landing page, each with a link when there's a fix."""
    items: list[dict] = []

    def add(message: str, link: str | None = None) -> None:
        if message and all(i["message"] != message for i in items):
            items.append({"message": message, "link": link})

    for problem in runtime_problems(services):
        add(problem, "/ops/accounts/" if problem == services.token_store.problem else None)
    engine = services.extras.get("canopy")
    if engine is not None:
        try:
            state = engine.state()
        except Exception:  # noqa: BLE001
            state = None
            add("The canopy engine can't report its state. The log tail below says why.")
        if state is not None and state.guest_input and str(state.phase) == "resting":
            add(f"The canopy is resting, so guests can't change it: {state.message or 'no reason given'}")
    for account in accounts["accounts"]:
        label = account.get("label") or account["account"]
        if account.get("needs_reauth"):
            add(f"{label}: Hubspace needs a new sign-in.", "/ops/accounts/")
        elif (
            not account.get("simulated") and account.get("token_generation") and not account.get("connected")
        ):
            add(f"{label}: not connected ({account.get('last_error') or 'still trying'}).", "/ops/accounts/")
        unknown = account.get("unknown_devices") or []
        if unknown:
            add(
                f"{label}: can see {len(unknown)} device(s) that aren't registered. They stay read-only; register "
                "them or find out why they're shared with this account.",
                "/ops/accounts/",
            )
    wiring = services.extras.get("show")
    if wiring is not None and hasattr(wiring, "attention_items"):
        # canopy modules with no device registered (fixed on /ops/accounts) or whose profile hasn't been read yet
        for line, link in wiring.attention_items():
            add(line, link)
    caps_cache = services.extras.get(CAPS_KEY, {})
    for entry in services.registry.devices:
        try:
            address = services.target(entry.id, Purpose.ENGINE).address
        except LookupError:
            address = None
        name = entry.label or entry.id
        link = device_page(entry)
        if address:
            snap = services.tracker.snapshot(address)
            if snap["status"] == "needs_attention":
                add(f"{name}: {snap['reason'] or 'needs attention'}", link)
            else:  # flagged problems count even before the device has a look
                for problem in snap["problems"]:
                    add(f"{name}: {problem}", link)
        check = _head_check(services, entry, caps_cache.get(entry.id))
        if check is not None and check.problem:
            add(f"{name}: {check.problem}", link)
    for alert in AlertBoard.of(services).active():
        link = None
        if alert.device_id:
            entry = services.registry.logical_for(alert.device_id)
            if entry is None:
                try:
                    entry = services.registry.get(alert.device_id)
                except KeyError:
                    entry = None
            if entry is not None:
                link = device_page(entry)
        message = alert.message + (f" ({alert.count} times)" if alert.count > 1 else "")
        if all(i["message"] != message for i in items):
            items.append(
                {
                    "message": message,
                    "link": link,
                    "alert": alert.key,
                    "level": alert.level,
                    "at": alert.last_t,
                }
            )
    return items


class AckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=2000)


@router.post("/api/ops/alerts/ack")
async def ack_alert(request: Request, body: AckBody, _ops: Ops) -> dict:
    """Hide one engine alert from "Needs attention" until it happens again (JSON from this site only, D17)."""
    headers = request.headers
    if ("origin" in headers or "referer" in headers) and not same_origin(headers):
        raise HTTPException(status_code=403, detail="Refused: this request came from another site.")
    if not AlertBoard.of(services_of(request)).ack(body.key):
        raise HTTPException(status_code=404, detail="That alert isn't showing any more.")
    return {"ok": True, "message": "Acknowledged. It comes back if it happens again."}


@router.get("/api/ops/summary")
async def ops_summary(request: Request, _ops: Ops) -> dict:
    services = services_of(request)
    accounts = accounts_summary(services)
    budgets = []
    for driver in (services.sim, services.afero):
        if driver is None or id(driver) not in services.executors:
            continue
        budgets.append(
            {
                "driver": driver.name,
                "simulated": bool(driver.simulated),
                "budget": services.executor_for(driver).budget(),
            }
        )
    locks = []
    for lease in services.locks.snapshot():
        entry = services.registry.logical_for(lease["device_id"])
        locks.append({**lease, "logical_id": entry.id if entry else None})
    simulated = bool(services.engine_driver.simulated)
    return {
        "version": {
            "version": __version__,
            "commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "local"),
            **services.version(),
        },
        "attention": _attention(services, accounts),
        "problems": runtime_problems(services),
        "accounts": accounts,
        "locks": locks,
        "budget": budgets,
        "sim": {"stats": services.sim.stats(), "faults": services.sim.faults.snapshot()}
        if simulated
        else None,
    }
