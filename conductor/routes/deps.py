"""Shared route dependencies: who is asking, and what they may touch (07 §2, `roles_api.py`).

The auth middleware (`conductor/auth.py`) decides whether a request reaches a route at all and records the
principal in `scope["state"]["principal"]`. Routes then check finer rights here:
- `principal_of`  — the caller (OPS when auth is off for local development).
- `require_ops`   — ops-only endpoints; anything else gets a plain 403.
- `require_device_access` — ops, or a tester granted that device id.
- `services_of`   — the Services container the lead builds in the app lifespan (`app.state.services`).
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection

from conductor.roles_api import OPS, Principal, Role

NOT_SIGNED_IN = "Sign in first: this page needs the ops password or an enrolled device."
OPS_ONLY = "Only ops can do this. Sign in with the ops password."
NO_DEVICE_ACCESS = "This device isn't one you've been given access to."


def auth_enabled() -> bool:
    return bool(os.environ.get("CONDUCTOR_BASIC_AUTH"))


def principal_of(conn: HTTPConnection) -> Principal:
    """The principal the auth middleware attached to this request (HTTP or WebSocket).

    With auth off (CONDUCTOR_BASIC_AUTH unset — local development only) everyone is ops. With auth on and no
    principal (an open path such as /enroll/ or /ws/), the caller isn't signed in: 401.
    """
    principal = conn.scope.get("state", {}).get("principal")
    if isinstance(principal, Principal):
        return principal
    if not auth_enabled():
        return OPS
    raise HTTPException(status_code=401, detail=NOT_SIGNED_IN)


def require_ops(request: Request) -> Principal:
    """Dependency for ops-only endpoints."""
    principal = principal_of(request)
    if principal.role is not Role.OPS:
        raise HTTPException(status_code=403, detail=OPS_ONLY)
    return principal


def require_device_access(request: Request, device_id: str) -> Principal:
    """Ops, or a tester granted `device_id`. Works as a dependency on routes with a `{device_id}` path
    parameter, or called directly with the id."""
    principal = principal_of(request)
    if not principal.can_device(device_id):
        raise HTTPException(status_code=403, detail=NO_DEVICE_ACCESS)
    return principal


def services_of(conn: HTTPConnection) -> Any:
    """The Services container (`app.state.services`). Attributes used by the core routes: roles (RoleStore),
    tickets (TicketBook), hub (WsHub)."""
    services = getattr(conn.app.state, "services", None)
    if services is None:
        raise HTTPException(status_code=503, detail="The Conductor is still starting. Try again in a moment.")
    return services
