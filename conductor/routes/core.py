"""Core routes: WebSocket tickets, the live socket, enrollment links and enrolled-device sessions (07 §2, D4, D5).

- `GET /api/ws-ticket` — any signed-in principal. A single-use, 30 s ticket carrying the caller's role and grants,
  sent by the page as the first WebSocket message (never in a URL). `no-store`.
- `WS /ws/live` — `WsHub.serve` checks Origin, then the ticket (close 1008 otherwise).
- `GET /enroll/<token>` — open (the token is the credential). Shows what the link will set up and a button; it
  does NOT use the link up, so link previews (Messages, Slack, mail scanners) can't burn it or catch the cookie.
- `POST /enroll/<token>` — uses the link once: sets the HttpOnly, SameSite=Strict `cc_session` cookie (Secure on
  https, Path=/, 30 days) and redirects (303) to the role's home page. Because the redirect follows a same-origin
  form post, the Strict cookie is sent on the very next request. Invalid, used or expired links get a plain,
  friendly page instead of an error.
- `POST /api/roles/enrollments` (ops, JSON `{role, name, grants}`) → the one-time link path and its expiry.
- `GET /api/roles/sessions`, `DELETE /api/roles/sessions/<fingerprint>`, `DELETE /api/roles/enrollments/<fp>`
  (ops) — list and revoke. Listings show fingerprints only, never tokens or session ids.
"""

from __future__ import annotations

import html
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from conductor.roles import SESSION_TTL_S, RoleError, fingerprint, home_path
from conductor.roles_api import SESSION_COOKIE, Principal, Role
from conductor.routes.deps import principal_of, require_ops, services_of
from conductor.wsauth import WS_CLOSE_POLICY

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}
# The enrollment pages carry a credential in their URL: never cache them, never leak them as a Referer, never
# load anything from elsewhere, never allow framing (the button must not be clickjacked).
PAGE_HEADERS = {
    **NO_STORE,
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; "
        "base-uri 'none'"
    ),
}
ROLE_LABELS = {
    Role.KIOSK: "the canopy kiosk",
    Role.TESTER: "a module tester's phone",
}


class EnrollmentRequest(BaseModel):
    role: str
    name: str = Field("", max_length=200)
    grants: list[str] = Field(default_factory=list, max_length=100)


# ---- WebSocket tickets and the live socket --------------------------------------------------------------------
@router.get("/api/ws-ticket")
async def ws_ticket(request: Request) -> JSONResponse:
    principal = principal_of(request)
    tickets = services_of(request).tickets
    # Principal.session_id is the session's fingerprint (enrolled devices) or None (ops): revoking the session
    # closes the sockets opened with it.
    ticket = tickets.issue(principal.role.value, principal.grants, session=principal.session_id)
    return JSONResponse({"ticket": ticket.value, "expires_in": tickets.ttl_s}, headers=NO_STORE)


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    services = getattr(websocket.app.state, "services", None)
    if services is None:
        await websocket.close(code=WS_CLOSE_POLICY)
        return
    await services.hub.serve(websocket)


# ---- enrollment links -----------------------------------------------------------------------------------------
@router.get("/enroll/{token}", response_class=HTMLResponse)
async def enroll_confirm(request: Request, token: str) -> HTMLResponse:
    principal = services_of(request).roles.peek_enrollment(token)
    if principal is None:
        return _invalid_link_page()
    what = ROLE_LABELS.get(principal.role, principal.role.value)
    grants = ", ".join(sorted(principal.grants))
    detail = f"<p>Access to: <strong>{html.escape(grants)}</strong></p>" if grants else ""
    body = f"""
      <h1>Set up this device</h1>
      <p>This link sets up this browser as <strong>{html.escape(what)}</strong>
         ({html.escape(principal.name)}).</p>
      {detail}
      <p>It works once. Open it on the device that will use it.</p>
      <form method="post" action=""><button type="submit">Set up this device</button></form>
    """
    return HTMLResponse(_page("Set up this device", body), headers=PAGE_HEADERS)


@router.post("/enroll/{token}", response_model=None)
async def enroll_redeem(request: Request, token: str) -> HTMLResponse | RedirectResponse:
    redeemed = services_of(request).roles.redeem_enrollment(token)
    if redeemed is None:
        return _invalid_link_page()
    session_id, principal = redeemed
    response = RedirectResponse(home_path(principal), status_code=303, headers=PAGE_HEADERS)
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=SESSION_TTL_S,
        path="/",
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return response


def _invalid_link_page() -> HTMLResponse:
    body = """
      <h1>This link can't be used</h1>
      <p>It has already been used, has expired, or was cancelled. Setup links work once and last 24 hours.</p>
      <p>Ask the operations team for a new link.</p>
    """
    return HTMLResponse(_page("Link can't be used", body), status_code=404, headers=PAGE_HEADERS)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{html.escape(title)} · Color Canopy</title>
<style>
  body {{ margin: 0; background: #F7F1E6; color: #2E2A26;
         font: 17px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }}
  main {{ max-width: 32rem; margin: 0 auto; padding: 32px 20px; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 16px; }}
  button {{ font: inherit; font-weight: 600; min-height: 48px; padding: 0 24px; border: 0; border-radius: 12px;
           background: #F96302; color: #fff; }}
</style></head>
<body><main>{body}</main></body></html>"""


# ---- ops: manage links and sessions ---------------------------------------------------------------------------
@router.post("/api/roles/enrollments", status_code=201)
async def create_enrollment(
    request: Request, body: EnrollmentRequest, _ops: Annotated[Principal, Depends(require_ops)]
) -> JSONResponse:
    roles = services_of(request).roles
    try:
        token, path = roles.create_enrollment(body.role, body.name, body.grants)
    except RoleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    fp = fingerprint(token)
    record = next((e for e in roles.list_enrollments() if e["fingerprint"] == fp), {})
    return JSONResponse(
        {
            "path": path,
            "url": str(request.base_url).rstrip("/") + path,
            "expires_at": record.get("expires_at"),
            "fingerprint": fp,
            "role": record.get("role"),
            "name": record.get("name"),
            "grants": record.get("grants", []),
        },
        status_code=201,
        headers=NO_STORE,
    )


@router.get("/api/roles/sessions")
async def list_sessions(request: Request, _ops: Annotated[Principal, Depends(require_ops)]) -> JSONResponse:
    roles = services_of(request).roles
    return JSONResponse(
        {
            "sessions": roles.list_sessions(),
            "enrollments": roles.list_enrollments(),
            "problem": roles.problem,
        },
        headers=NO_STORE,
    )


@router.delete("/api/roles/sessions/{fp}")
async def revoke_session(request: Request, fp: str, _ops: Annotated[Principal, Depends(require_ops)]) -> dict:
    if not services_of(request).roles.revoke_session(fp):
        raise HTTPException(status_code=404, detail="No enrolled device with that fingerprint.")
    return {"revoked": fp}


@router.delete("/api/roles/enrollments/{fp}")
async def revoke_enrollment(
    request: Request, fp: str, _ops: Annotated[Principal, Depends(require_ops)]
) -> dict:
    if not services_of(request).roles.revoke_enrollment(fp):
        raise HTTPException(status_code=404, detail="No unused setup link with that fingerprint.")
    return {"revoked": fp}
