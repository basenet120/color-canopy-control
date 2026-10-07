"""Account connect (07 §3 — binding; D2, D3): the JSON API the CLI uses, and the hardened /ops/accounts form.

JSON API (ops only). Every POST must be `Content-Type: application/json` (else 415): a cross-site page can't send
that without a CORS preflight, which we never grant, so the ops Basic credential a browser caches can't be ridden
(D2). Bodies are parsed here, not by pydantic, so a validation error can never echo a submitted field.
- `GET  /api/accounts` — `AccountStatus` per account, whether this runtime is simulated, whether the page form is on.
- `POST /api/accounts/login` `{account, email, password}` → `LoginOutcome`.
- `POST /api/accounts/otp` `{nonce, code}` → `LoginOutcome` (re-prompts on a wrong code, same nonce).
- `POST /api/accounts/{account}/disconnect` — stop using the account and forget its stored token.
- `GET  /api/accounts/{account}/devices` — what the connected account can see (read-only discovery), each device
  marked with the registry id it is registered as, or read-only when it isn't registered/allowlisted (07 §1).
- `POST /api/accounts/{account}/register` `{metadevice_id, device_id}` — put the id in the ops overlay. An id
  registered to a spare kit moves to a non-spare entry (controller swap), taking the bench flags that kit proved
  and its controller map (`framebuffer_mapping`, D54) when it was proven on that kit.
Gate refusals come back as 403 with the gate's plain reason. Every response here is `Cache-Control: no-store`.
A client that goes away while its request body is still being read (`starlette.requests.ClientDisconnect`) is a
quiet no-op: nothing was acted on (every route reads its body before doing anything; the form still counts it as
one of its attempts), one INFO line is logged, and the answer — which nobody receives — is an empty 499.

The HTML form (`POST /ops/accounts`, urlencoded) works only with `CONDUCTOR_ACCOUNT_CONNECT=1`, turns itself off
(in memory) after any successful connect, checks that Origin/Referer is this host, allows 5 attempts per 15 minutes
per process, never echoes what was submitted, forbids framing, and handles the emailed-code step with the nonce in
a hidden field. `GET /ops/accounts/` serves the static page with the same anti-framing headers.

Passwords and codes live only for the duration of one request: they are passed straight to the AccountManager and
never logged, stored, or put in a response.
"""

from __future__ import annotations

import html
import json
import logging
import os
from collections import deque
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.routing import APIRoute
from starlette.requests import ClientDisconnect

from conductor.accounts_api import PENDING_TTL_S, LoginOutcome, LoginState
from conductor.config import ConfigError
from conductor.gate import GateDenied, Purpose
from conductor.roles_api import Principal
from conductor.routes.deps import require_ops, services_of

FORM_FLAG = "CONDUCTOR_ACCOUNT_CONNECT"
FORM_MAX_ATTEMPTS = 5
FORM_WINDOW_S = 15 * 60
MAX_BODY_BYTES = 16 * 1024
MAX_EMAIL = 320
MAX_SECRET = 1024
MAX_CODE = 64
MAX_ID = 120
DISCOVERED_KEY = "discovered"  # services.extras: account → metadevice ids its latest discovery showed
FORM_KEY = "account_form"  # services.extras: AccountForm
ACCOUNTS_PAGE = "/ops/accounts/"
ACCOUNTS_HTML = Path(__file__).resolve().parents[2] / "web" / "ops" / "accounts" / "index.html"
CLI_HINT = "python -m conductor.auth login"

NO_STORE = {"Cache-Control": "no-store"}
# Referrer-Policy stays same-origin (not no-referrer): with no-referrer, browsers send `Origin: null` on a form
# post and the same-origin check would refuse the emailed-code step.
_FRAME = {"X-Frame-Options": "DENY", "Referrer-Policy": "same-origin"}
# Server-rendered form responses: no scripts at all; styles only from here; posts only back to here; never framed.
FORM_HEADERS = {
    **NO_STORE,
    **_FRAME,
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; "
        "base-uri 'none'"
    ),
}
PAGE_HEADERS = {
    **NO_STORE,
    **_FRAME,
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}

RESTART_WARNING = (
    "Keep this page open. If the Conductor restarts or is redeployed before you enter the code, this sign-in is "
    "lost and you'll need to start again (Hubspace will email a new code)."
)


CLIENT_GONE_STATUS = 499  # "client closed request": the client left before its request body arrived


class _NoStoreRoute(APIRoute):
    """Adds `Cache-Control: no-store` to every response from this router, including refusals, and turns a client
    that disconnected mid-request into a quiet no-op instead of an unhandled traceback."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except HTTPException as exc:
                raise HTTPException(
                    exc.status_code, exc.detail, headers={**(exc.headers or {}), **NO_STORE}
                ) from None
            except ClientDisconnect:
                # Every route reads its body before acting, so nothing happened. Nobody receives this answer.
                log.info(
                    "%s %s: the client went away before its request arrived; nothing was done",
                    request.method,
                    request.url.path,
                )
                return Response(status_code=CLIENT_GONE_STATUS, headers=dict(NO_STORE))
            response.headers.update(NO_STORE)
            return response

        return handler


log = logging.getLogger(__name__)
router = APIRouter(route_class=_NoStoreRoute)
Ops = Annotated[Principal, Depends(require_ops)]


# ---- in-memory form state ------------------------------------------------------------------------------------
@dataclass
class AccountForm:
    """The /ops/accounts form's state for this process: whether it turned itself off, and recent attempts."""

    turned_off: bool = False
    attempts: deque[float] = field(default_factory=deque)

    def enabled(self, env: Mapping[str, str] = os.environ) -> tuple[bool, str]:
        if env.get(FORM_FLAG) != "1":
            return False, (
                f"Signing in from this page is off on this runtime. Use the command line ({CLI_HINT}), or set "
                f"{FORM_FLAG}=1 and restart the Conductor."
            )
        if self.turned_off:
            return False, (
                "Signing in from this page turned itself off after a successful sign-in. Use the command line "
                f"({CLI_HINT}), or restart the Conductor (with {FORM_FLAG}=1) to turn it on again."
            )
        return True, ""

    def take_attempt(self, now: float) -> bool:
        """Count one attempt; False (nothing counted) when the window's limit is already used up."""
        while self.attempts and now - self.attempts[0] >= FORM_WINDOW_S:
            self.attempts.popleft()
        if len(self.attempts) >= FORM_MAX_ATTEMPTS:
            return False
        self.attempts.append(now)
        return True


def account_form(services: Any) -> AccountForm:
    """This process's form state (kept in `services.extras`)."""
    return services.extras.setdefault(FORM_KEY, AccountForm())


# ---- helpers -------------------------------------------------------------------------------------------------
def _refuse(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail, headers=NO_STORE)


def _publish_accounts(services: Any) -> None:
    try:
        services.hub.publish("accounts", {"accounts": [s.to_json() for s in services.accounts.status()]})
    except Exception:  # a push failure must never fail a sign-in
        log.warning("couldn't push the account status to live /ops pages", exc_info=True)


def _after(services: Any, outcome: LoginOutcome) -> None:
    """A successful connect turns the page form off (07 §3) and tells live /ops pages."""
    if outcome.state is LoginState.CONNECTED:
        account_form(services).turned_off = True
        services.extras.get(DISCOVERED_KEY, {}).pop(outcome.account, None)
        _publish_accounts(services)


def _outcome_json(services: Any, outcome: LoginOutcome) -> dict:
    return {
        "state": str(outcome.state),
        "message": outcome.message,
        "nonce": outcome.nonce,
        "account": outcome.account,
        "simulated": bool(services.accounts.simulated),
    }


def _check_login_gate(services: Any, email: str) -> None:
    """Real accounts only: the gate's account rules, before a password goes anywhere (07 §1, D3). The simulator's
    sign-in never leaves the process and accepts only fixture identities, so the gate doesn't apply to it."""
    if not services.accounts.simulated:
        services.gate.check_account(email)


def _check_account_traffic(services: Any) -> None:
    if not services.accounts.simulated:
        ok, why = services.gate.purpose_allowed(Purpose.ACCOUNT)
        if not ok:
            raise GateDenied(why)


def _known_account(services: Any, account: str) -> None:
    if account not in services.registry.accounts():
        raise _refuse(404, "There's no account with that name in the registry.")


async def _json_body(request: Request) -> dict:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise _refuse(415, "Send this as JSON (Content-Type: application/json).")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise _refuse(413, "That request is too large.")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        raise _refuse(400, "The request body isn't valid JSON.") from None
    if not isinstance(data, dict):
        raise _refuse(400, "Send a JSON object.")
    return data


def _text(data: dict, name: str, max_len: int, *, required: bool = True) -> str:
    """A string field, or a 400 that names the field but never repeats its value."""
    value = data.get(name)
    if value is None or value == "":
        if required:
            raise _refuse(400, f"Missing {name}.")
        return ""
    if not isinstance(value, str):
        raise _refuse(400, f"{name} must be text.")
    if len(value) > max_len:
        raise _refuse(400, f"{name} is too long.")
    return value


def accounts_summary(services: Any) -> dict:
    """Account status for /ops (also used by the ops summary). No emails, tokens or account ids."""
    labels = services.registry.accounts()
    form_on, form_why = account_form(services).enabled()
    if services.accounts.simulated:
        connect_ok, connect_why = True, "Simulator: sign-in is simulated and nothing is sent to Hubspace."
    else:
        connect_ok, connect_why = services.gate.purpose_allowed(Purpose.ACCOUNT)
    accounts = []
    for status in services.accounts.status():
        row = status.to_json()
        row["label"] = (labels.get(status.account) or {}).get("label", status.account)
        accounts.append(row)
    return {
        "simulated": bool(services.accounts.simulated),
        "mode": str(services.gate_settings.mode),
        "accounts": accounts,
        "form_enabled": form_on,
        "form_reason": form_why or None,
        "connect_allowed": connect_ok,
        "connect_reason": connect_why,
        "pending_minutes": round(PENDING_TTL_S / 60),
        "token_store": None if services.token_store.available else services.token_store.problem,
    }


# ---- JSON API ------------------------------------------------------------------------------------------------
@router.get("/api/accounts")
async def list_accounts(request: Request, _ops: Ops) -> JSONResponse:
    return JSONResponse(accounts_summary(services_of(request)))


@router.post("/api/accounts/login")
async def api_login(request: Request, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    data = await _json_body(request)
    account = _text(data, "account", MAX_ID)
    email = _text(data, "email", MAX_EMAIL)
    password = _text(data, "password", MAX_SECRET)
    _known_account(services, account)
    try:
        _check_login_gate(services, email)
    except GateDenied as denied:
        raise _refuse(403, denied.reason) from None
    outcome = await services.accounts.start_login(account, email, password)
    _after(services, outcome)
    return JSONResponse(_outcome_json(services, outcome))


@router.post("/api/accounts/otp")
async def api_otp(request: Request, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    data = await _json_body(request)
    nonce = _text(data, "nonce", MAX_ID)
    code = _text(data, "code", MAX_CODE, required=False)
    try:
        _check_account_traffic(services)
    except GateDenied as denied:
        raise _refuse(403, denied.reason) from None
    outcome = await services.accounts.submit_otp(nonce, code)
    _after(services, outcome)
    return JSONResponse(_outcome_json(services, outcome))


@router.post("/api/accounts/{account}/disconnect")
async def api_disconnect(request: Request, account: str, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    await _json_body(request)  # JSON only, so a cross-site form can't disconnect an account
    _known_account(services, account)
    await services.accounts.disconnect(account)
    services.extras.get(DISCOVERED_KEY, {}).pop(account, None)
    _publish_accounts(services)
    simulated = bool(services.accounts.simulated)
    return JSONResponse(
        {
            "account": account,
            "disconnected": True,
            "simulated": simulated,
            "message": "Simulator: disconnected (simulated)."
            if simulated
            else "Disconnected, and the stored token was deleted. Sign in again to reconnect.",
        }
    )


def _registered_as(services: Any, address: str, simulated: bool) -> str | None:
    for entry in services.registry.file.devices:
        if entry.metadevice_id == address or (simulated and entry.sim_address == address):
            return entry.id
    return None


@router.get("/api/accounts/{account}/devices")
async def api_account_devices(request: Request, account: str, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    _known_account(services, account)
    status = next((s for s in services.accounts.status() if s.account == account), None)
    if status is None or not status.connected:
        raise _refuse(409, "Sign in to this account first; then its devices can be listed.")
    driver = services.sim if services.accounts.simulated else services.afero
    if driver is None:
        raise _refuse(409, "Hubspace isn't available on this runtime, so there's nothing to list.")
    try:
        infos = await driver.discover(account)
    except GateDenied as denied:
        raise _refuse(403, denied.reason) from None
    except LookupError as exc:
        raise _refuse(404, str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - the adapter's AferoRequestFailed; its message is operator-safe
        raise _refuse(502, f"Couldn't list this account's devices: {exc}") from None
    simulated = bool(driver.simulated)
    allowlist = services.gate.effective_allowlist()
    services.extras.setdefault(DISCOVERED_KEY, {})[account] = {i.device_id for i in infos}
    devices = []
    for info in infos:
        registered = _registered_as(services, info.device_id, simulated)
        allowlisted = simulated or info.device_id in allowlist
        devices.append(
            {
                "metadevice_id": info.device_id,
                "name": info.name,
                "model": info.model,
                "registered_as": registered,
                "allowlisted": allowlisted,
                "read_only": registered is None or not allowlisted,
                "simulated": simulated,
            }
        )
    return JSONResponse(
        {
            "account": account,
            "simulated": simulated,
            "devices": devices,
            # Registry devices on this account: what a discovered device can be registered as.
            "registry": [
                {
                    "id": e.id,
                    "label": e.label or e.id,
                    "role": e.role,
                    "metadevice_id": e.metadevice_id,
                }
                for e in services.registry.devices
                if e.account == account
            ],
            "note": "Simulator: these are simulated devices."
            if simulated
            else "Devices that aren't registered, or aren't in HUBSPACE_DEVICE_ALLOWLIST, stay read-only.",
        }
    )


@router.post("/api/accounts/{account}/register")
async def api_register(request: Request, account: str, _ops: Ops) -> JSONResponse:
    services = services_of(request)
    data = await _json_body(request)
    metadevice_id = _text(data, "metadevice_id", 80).strip()
    device_id = _text(data, "device_id", MAX_ID)
    _known_account(services, account)
    try:
        entry = services.registry.get(device_id)
    except KeyError:
        raise _refuse(404, "There's no registry device with that id.") from None
    if entry.account != account:
        raise _refuse(
            409,
            f"{entry.id} belongs to the {entry.account} account, not {account}. Register it from its own account.",
        )
    spare = None  # a spare kit handing its Hubspace device to this entry (controller swap)
    for other in services.registry.file.devices:
        if other.id != entry.id and metadevice_id in (other.metadevice_id, other.sim_address):
            if other.role == "spare" and entry.role != "spare":
                spare = other
                continue
            raise _refuse(409, f"That Hubspace device is already registered as {other.id}.")
    simulated = bool(services.accounts.simulated)
    if not simulated and metadevice_id not in services.extras.get(DISCOVERED_KEY, {}).get(account, ()):
        raise _refuse(
            409,
            "That id isn't one this account showed in its latest device list. List the account's devices first, "
            "then register one of them.",
        )
    previous = entry.metadevice_id
    patch: dict[str, Any] = {"metadevice_id": metadevice_id}
    if spare is not None and spare.flags_proven_on == metadevice_id:
        # Bench flags belong to the kit that proved them (D19), so they move with it.
        patch["flags"] = spare.flags.model_dump()
        patch["flags_proven_on"] = metadevice_id
    mapping = spare.framebuffer_mapping if spare is not None else None
    if mapping is not None and mapping.proven_on == metadevice_id:
        # So does its controller map (D54): the show reads each kit's own (D25).
        patch["framebuffer_mapping"] = mapping.model_dump()
    try:
        if spare is not None and spare.metadevice_id == metadevice_id:
            # Two entries can't hold one id: release it from the spare first. If the next step fails, the kit is
            # simply unregistered (register it again); nothing else changes.
            services.update_registry(spare.id, {"metadevice_id": None})
        services.update_registry(entry.id, patch)
    except ConfigError as exc:
        raise _refuse(400, str(exc)) from None
    services.extras.get("capabilities", {}).pop(entry.id, None)
    if spare is not None:
        services.extras.get("capabilities", {}).pop(spare.id, None)
    allowlisted = metadevice_id in services.gate.effective_allowlist()
    if simulated:
        message = (
            "Simulator: saved to the ops overlay. On a live runtime you would now add the id to "
            "HUBSPACE_DEVICE_ALLOWLIST."
        )
    elif allowlisted:
        message = "Saved. It is already in HUBSPACE_DEVICE_ALLOWLIST, so bench jobs can write to it."
    else:
        message = (
            f"Saved. Add {metadevice_id} to HUBSPACE_DEVICE_ALLOWLIST on this runtime and restart before bench "
            "jobs can write to it; until then it stays read-only."
        )
    if spare is not None:
        carried = "its bench flags came with it" if "flags" in patch else "it has no bench flags yet"
        if "framebuffer_mapping" in patch:
            carried += "; its controller map came with it"
        message = (
            f"Moved from {spare.id} to {entry.id} ({carried}). Calibrate it and press Commission slots. "
            + message
        )
    return JSONResponse(
        {
            "device_id": entry.id,
            "metadevice_id": metadevice_id,
            "replaced": previous if previous and previous != metadevice_id else None,
            "moved_from": spare.id if spare is not None else None,
            "flags_carried": "flags" in patch,
            "map_carried": "framebuffer_mapping" in patch,
            "allowlisted": allowlisted,
            "simulated": simulated,
            "message": message,
        }
    )


# ---- the /ops/accounts page and its form ---------------------------------------------------------------------
@router.get("/ops/accounts/", include_in_schema=False)
async def accounts_page(_ops: Ops) -> FileResponse:
    """The static page, served here only to add the anti-framing headers (the sign-in form lives on it)."""
    return FileResponse(ACCOUNTS_HTML, media_type="text/html", headers=PAGE_HEADERS)


def same_origin(headers: Mapping[str, str]) -> bool:
    """Origin (or, without one, Referer) must name this host (07 §3). Missing both → refused."""
    host = (headers.get("host") or "").strip().lower()
    if not host:
        return False
    origin = headers.get("origin")
    if origin is not None:
        return origin != "null" and urlsplit(origin).netloc.lower() == host
    referer = headers.get("referer")
    return bool(referer) and urlsplit(referer).netloc.lower() == host


async def _form_fields(request: Request) -> dict[str, str]:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("too large")
    parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=20)
    return {k: v[0] for k, v in parsed.items() if v}


def _page(title: str, body: str, *, simulated: bool) -> str:
    badge = '<div class="sim-badge" role="status">Simulator</div>' if simulated else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · Color Canopy</title>
<link rel="stylesheet" href="/static/base.css"><link rel="stylesheet" href="/ops/ops.css">
</head>
<body class="ops"><main class="narrow stack">{body}</main>{badge}</body></html>"""


def _form_page(services: Any, title: str, body: str, status: int = 200) -> HTMLResponse:
    page = _page(title, body, simulated=bool(services.accounts.simulated))
    return HTMLResponse(page, status_code=status, headers=FORM_HEADERS)


def _message_page(services: Any, title: str, message: str, status: int) -> HTMLResponse:
    body = f"""
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(message)}</p>
      <p><a class="btn secondary" href="{ACCOUNTS_PAGE}">Back to accounts</a></p>
    """
    return _form_page(services, title, body, status)


def _otp_page(services: Any, outcome: LoginOutcome) -> HTMLResponse:
    body = f"""
      <h1>Enter the emailed code</h1>
      <p class="notice">{html.escape(outcome.message)}</p>
      <form method="post" action="/ops/accounts" class="stack card">
        <input type="hidden" name="step" value="otp">
        <input type="hidden" name="nonce" value="{html.escape(outcome.nonce or "", quote=True)}">
        <div>
          <label for="code">Code from Hubspace's email</label>
          <input id="code" name="code" autocomplete="one-time-code" inputmode="numeric" required autofocus>
        </div>
        <button type="submit">Check code</button>
      </form>
      <p class="muted">{html.escape(RESTART_WARNING)}</p>
      <p><a href="{ACCOUNTS_PAGE}">Cancel and go back</a></p>
    """
    return _form_page(services, "Enter the emailed code", body)


def _outcome_response(services: Any, outcome: LoginOutcome) -> Response:
    if outcome.state is LoginState.CONNECTED:
        _after(services, outcome)
        return RedirectResponse(f"{ACCOUNTS_PAGE}?result=connected", status_code=303, headers=FORM_HEADERS)
    if outcome.state is LoginState.OTP_REQUIRED and outcome.nonce:
        return _otp_page(services, outcome)
    return _message_page(services, "Sign-in didn't work", outcome.message, 200)


@router.post("/ops/accounts", include_in_schema=False)
async def form_post(request: Request, _ops: Ops) -> Response:
    services = services_of(request)
    form = account_form(services)
    on, why = form.enabled()
    if not on:
        return _message_page(services, "Sign-in from this page is off", why, 403)
    if not same_origin(request.headers):
        return _message_page(
            services,
            "Sign-in refused",
            "This sign-in didn't come from this Conductor's own accounts page, so it was refused. Open "
            f"{ACCOUNTS_PAGE} on this server and try again.",
            403,
        )
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/x-www-form-urlencoded":
        return _message_page(services, "Sign-in refused", "Use the form on the accounts page.", 415)
    if not form.take_attempt(services.clock.monotonic()):
        return _message_page(
            services,
            "Too many attempts",
            f"This page allows {FORM_MAX_ATTEMPTS} sign-in attempts every {FORM_WINDOW_S // 60} minutes. Wait "
            f"and try again, or use the command line ({CLI_HINT}).",
            429,
        )
    try:
        fields = await _form_fields(request)
    except ValueError:
        return _message_page(services, "Sign-in refused", "That form submission couldn't be read.", 400)
    if fields.get("step") == "otp":
        nonce, code = fields.get("nonce", ""), fields.get("code", "")
        if not nonce or len(nonce) > MAX_ID or len(code) > MAX_CODE:
            return _message_page(
                services, "Sign-in didn't work", "That code form is incomplete. Start the sign-in again.", 400
            )
        try:
            _check_account_traffic(services)
        except GateDenied as denied:
            return _message_page(services, "Sign-in refused", denied.reason, 403)
        return _outcome_response(services, await services.accounts.submit_otp(nonce, code))
    account, email, password = fields.get("account", ""), fields.get("email", ""), fields.get("password", "")
    if (
        account not in services.registry.accounts()
        or not email.strip()
        or not password
        or len(email) > MAX_EMAIL
        or len(password) > MAX_SECRET
    ):
        return _message_page(
            services,
            "Sign-in didn't work",
            "Choose an account and enter its email and password, then try again.",
            400,
        )
    try:
        _check_login_gate(services, email)
    except GateDenied as denied:
        return _message_page(services, "Sign-in refused", denied.reason, 403)
    return _outcome_response(services, await services.accounts.start_login(account, email, password))
