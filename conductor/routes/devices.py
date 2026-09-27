"""Devices and the ops summary (ops only; BUILD_PLAN "HTTP API", 07 §1, §6, §10).

- `GET /api/devices[?refresh=1]` — every enabled registry device: identity (id, label, role, account, module,
  orientation, expected heads, flags, metadevice id), where each purpose's writes go ({driver, simulated,
  address}), the tracker's status + reason for the engine address, who holds its lock, capabilities detected from
  the device's own profile, and any head-count problem.
- `GET /api/devices/{id}[?refresh=1]` — the same record plus the tracker's full snapshot.
- `GET /api/ops/summary` — version, "Needs attention" items, accounts, locks, the executor's rate budget per
  driver, and simulator statistics when the engine runs on the simulator.

Capabilities are fetched lazily (a profile read through the bench target's driver, Purpose.BENCH — a GET, never a
write) and cached in `services.extras["capabilities"]`; `refresh=1` re-fetches. A failed fetch is cached for
`CAPS_ERROR_RETRY_S` so a broken connection isn't hammered by page refreshes. The summary never fetches.
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from conductor import __version__
from conductor.capabilities import Capabilities, detect
from conductor.gate import GateDenied, Purpose
from conductor.roles_api import Principal
from conductor.routes.accounts import accounts_summary
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
    md = entry.metadevice_id
    allowlist = services.gate.effective_allowlist()
    return {
        "id": entry.id,
        "label": entry.label or entry.id,
        "role": entry.role,
        "account": entry.account,
        "model": entry.model,
        "module": entry.module,
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
        "head_count_problem": caps.head_count_problem(entry.expected_heads) if caps else None,
    }


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
    return list(dict.fromkeys(p for p in problems if p))


def _attention(services: Any, accounts: dict) -> list[dict]:
    """Plain-English "Needs attention" items for the /ops landing page, each with a link when there's a fix."""
    items: list[dict] = []

    def add(message: str, link: str | None = None) -> None:
        if message and all(i["message"] != message for i in items):
            items.append({"message": message, "link": link})

    for problem in runtime_problems(services):
        add(problem, "/ops/accounts/" if problem == services.token_store.problem else None)
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
    if wiring is not None and hasattr(wiring, "attention"):
        for line in wiring.attention():  # canopy modules whose profile the engine hasn't read yet
            add(line, "/ops/")
    caps_cache = services.extras.get(CAPS_KEY, {})
    for entry in services.registry.devices:
        try:
            address = services.target(entry.id, Purpose.ENGINE).address
        except LookupError:
            address = None
        name = entry.label or entry.id
        if address:
            snap = services.tracker.snapshot(address)
            link = f"/ops/module/?device={entry.id}"
            if snap["status"] == "needs_attention":
                add(f"{name}: {snap['reason'] or 'needs attention'}", link)
            else:  # flagged problems count even before the device has a look
                for problem in snap["problems"]:
                    add(f"{name}: {problem}", link)
        caps = (caps_cache.get(entry.id) or {}).get("caps")
        if caps is not None and (problem := caps.head_count_problem(entry.expected_heads)):
            add(f"{name}: {problem}", f"/ops/module/?device={entry.id}")
    return items


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
