"""HTTP Basic auth for every HTTP and WebSocket request, and the rule for when it is mandatory (07 §2).

Who gets through the middleware, in order:
- `/healthz` — always (content-free health check).
- The ops Basic credential (CONDUCTOR_BASIC_AUTH) — everything; the request's principal is `OPS`.
- WebSockets under `/ws/` — always; they authenticate in the endpoint with a single-use ticket sent as the first
  message (iOS Safari is unreliable with Basic on upgrades; D4). `/ws` itself is not under `/ws/`.
- `/enroll/...` HTTP requests — always; the one-time link token is the credential (D5).
- An enrolled device's `cc_session` cookie — only for the paths its role may use (`roles.path_allowed`).
- The kiosk page (`/kiosk`, `/kiosk/...`) without an enrolled kiosk's cookie gets 401 with a calm "not set up yet"
  page and **no** Basic challenge, so Chromium never puts a sign-in dialog on the guest touchscreen (a cold-spare
  takeover, a revoked or expired kiosk session). A request carrying the ops credential still gets through, but a
  browser is never asked for it here.
- Everything else gets 401 with the Basic challenge (a WebSocket is closed with 1008 before the handshake).

With CONDUCTOR_BASIC_AUTH unset (local development only; `check_auth_configuration` refuses it elsewhere), auth is
off and every request's principal is `OPS`. The principal is put in `scope["state"]["principal"]`, where
`request.state.principal` and `conductor.routes.deps.principal_of` read it.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import secrets
from collections.abc import Callable, Mapping

from starlette.requests import cookie_parser

from conductor.roles import normalize_path, path_allowed
from conductor.roles_api import OPS, SESSION_COOKIE, Principal

log = logging.getLogger(__name__)

OPEN_PATHS = frozenset({"/healthz"})  # the platform health check must pass without credentials
TICKET_WS_PREFIX = "/ws/"  # browser WebSockets: ticket auth in the endpoint (D4)
ENROLL_PREFIX = "/enroll/"  # one-time enrollment links: the token is the credential (D5)
PrincipalResolver = Callable[[str], Principal | None]
CHALLENGE = [(b"www-authenticate", b'Basic realm="Color Canopy"'), (b"content-length", b"0")]
KIOSK_PREFIX = "/kiosk"
# Self-contained (nothing else loads without a session). It re-checks the page every 30 s and reloads once it is
# allowed, so a kiosk enrolled meanwhile (or a session lookup that failed once) comes back by itself.
NOT_SET_UP_PAGE = b"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>The Color Canopy</title>
<style>
html, body { margin: 0; height: 100%; }
body { display: grid; place-items: center; background: #F7F1E6; color: #2E2A26; overflow: hidden;
  user-select: none; -webkit-user-select: none; touch-action: none;
  font-family: "Iowan Old Style", "Bitstream Charter", Charter, "Palatino Linotype", Palatino, Georgia, serif; }
main { text-align: center; padding: 0 8vw; }
h1 { margin: 0; font-weight: 400; font-size: min(6.4vw, 11vh); }
p { margin: 1.2em 0 0; color: #6A6158; font-size: min(2.3vw, 4vh); }
</style>
</head>
<body>
<main>
<h1>The Color Canopy</h1>
<p>This screen is not set up yet &mdash; ask the canopy team.</p>
</main>
<script>
setInterval(function () {
  fetch(location.href, { cache: "no-store" }).then(function (r) { if (r.ok) location.reload(); }, function () {});
}, 30000);
</script>
</body>
</html>
"""
NOT_SET_UP_HEADERS = [
    (b"content-type", b"text/html; charset=utf-8"),
    (b"content-length", str(len(NOT_SET_UP_PAGE)).encode()),
    (b"cache-control", b"no-store"),
]


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
    OPEN_PATHS must carry it, or be one of the exceptions in the module docstring. WebSockets without it are
    refused before the handshake completes.

    `principal_resolver(session_id)` maps a `cc_session` cookie to its principal (None when unknown or revoked).
    It is a plain callable so this module never imports the services container.
    """

    def __init__(self, app, principal_resolver: PrincipalResolver | None = None):
        self.app = app
        self.principal_resolver = principal_resolver

    async def __call__(self, scope, receive, send):
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        expected = os.environ.get("CONDUCTOR_BASIC_AUTH")
        path = scope["path"]
        if not expected or _authorized(scope, expected.encode()):
            scope.setdefault("state", {})["principal"] = OPS
            return await self.app(scope, receive, send)
        if path in OPEN_PATHS or _open_without_credentials(kind, path):
            return await self.app(scope, receive, send)
        if kind == "http":
            principal = self._cookie_principal(scope)
            if principal is not None and path_allowed(principal, scope.get("method", "GET"), path):
                scope.setdefault("state", {})["principal"] = principal
                return await self.app(scope, receive, send)
        if kind == "websocket":
            await receive()  # the websocket.connect event
            await send({"type": "websocket.close", "code": 1008})
            return
        if _is_kiosk_page(path):
            await send({"type": "http.response.start", "status": 401, "headers": NOT_SET_UP_HEADERS})
            await send({"type": "http.response.body", "body": NOT_SET_UP_PAGE})
            return
        await send({"type": "http.response.start", "status": 401, "headers": CHALLENGE})
        await send({"type": "http.response.body", "body": b""})

    def _cookie_principal(self, scope) -> Principal | None:
        if self.principal_resolver is None:
            return None
        header = dict(scope["headers"]).get(b"cookie")
        if not header:
            return None
        session_id = cookie_parser(header.decode("latin-1")).get(SESSION_COOKIE)
        if not session_id:
            return None
        try:
            return self.principal_resolver(session_id)
        except Exception:
            # Fail closed: a broken resolver means "no principal", never "let it through".
            log.exception("session lookup failed; treating the request as signed out")
            return None


def _open_without_credentials(kind: str, path: str) -> bool:
    """Ticket WebSockets under /ws/ and one-segment /enroll/<token> links, in normal form only — so a path
    like /enroll/../ops/accounts can never slip past the password into a mount that resolves '..'."""
    normal = normalize_path(path)
    if normal is None:
        return False
    if kind == "websocket":
        return normal.startswith(TICKET_WS_PREFIX)
    token = normal.removeprefix(ENROLL_PREFIX)
    return normal.startswith(ENROLL_PREFIX) and bool(token) and "/" not in token


def _is_kiosk_page(path: str) -> bool:
    """`/kiosk` or anything under `/kiosk/`, in normal form (the guest touchscreen's page and its assets)."""
    normal = normalize_path(path)
    return normal is not None and (normal == KIOSK_PREFIX or normal.startswith(KIOSK_PREFIX + "/"))


def _authorized(scope, expected: bytes) -> bool:
    header = dict(scope["headers"]).get(b"authorization", b"")
    if not header.startswith(b"Basic "):
        return False
    try:
        supplied = base64.b64decode(header.removeprefix(b"Basic "), validate=True)
    except binascii.Error:
        return False
    return secrets.compare_digest(supplied, expected)


if __name__ == "__main__":
    from conductor.auth_cli import main

    main()
