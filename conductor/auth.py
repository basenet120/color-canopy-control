"""HTTP Basic auth for every HTTP and WebSocket request, and the rule for when it is mandatory."""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from collections.abc import Mapping

OPEN_PATHS = frozenset({"/healthz"})  # the platform health check must pass without credentials
CHALLENGE = [(b"www-authenticate", b'Basic realm="Color Canopy"'), (b"content-length", b"0")]


def check_auth_configuration(env: Mapping[str, str] = os.environ) -> None:
    """Refuse to start a deployed, live or non-sim runtime without credentials, so auth never fails open.

    Local development may opt out with CONDUCTOR_AUTH=off, but never on Railway or with HUBSPACE_LIVE=1.
    """
    credentials = env.get("CONDUCTOR_BASIC_AUTH", "")
    if credentials and ":" not in credentials:
        raise RuntimeError("CONDUCTOR_BASIC_AUTH must look like user:password")
    deployed_or_live = bool(env.get("RAILWAY_ENVIRONMENT_NAME")) or env.get("HUBSPACE_LIVE") == "1"
    if credentials or (env.get("CONDUCTOR_AUTH") == "off" and not deployed_or_live):
        return
    if deployed_or_live or env.get("CONDUCTOR_MODE", "sim") != "sim":
        raise RuntimeError(
            "Refusing to start without CONDUCTOR_BASIC_AUTH: this runtime is deployed, live, or not in sim mode"
        )


class BasicAuthMiddleware:
    """When CONDUCTOR_BASIC_AUTH ("user:password") is set, every HTTP and WebSocket request outside
    OPEN_PATHS must carry it. WebSockets without it are refused before the handshake completes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        expected = os.environ.get("CONDUCTOR_BASIC_AUTH")
        if (
            not expected
            or scope["type"] not in ("http", "websocket")
            or scope["path"] in OPEN_PATHS
            or _authorized(scope, expected.encode())
        ):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await receive()  # the websocket.connect event
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": 401, "headers": CHALLENGE})
        await send({"type": "http.response.body", "body": b""})


def _authorized(scope, expected: bytes) -> bool:
    header = dict(scope["headers"]).get(b"authorization", b"")
    if not header.startswith(b"Basic "):
        return False
    try:
        supplied = base64.b64decode(header.removeprefix(b"Basic "), validate=True)
    except binascii.Error:
        return False
    return secrets.compare_digest(supplied, expected)
