"""WebSocket authentication: single-use tickets sent as the first message, plus an Origin check (07 §2).

iOS Safari (every vignette iPad) is unreliable with Basic auth on WebSocket upgrades, so browser sockets don't
rely on it. A page fetches `GET /api/ws-ticket` (an ordinary authenticated request — Basic or a role cookie),
opens the socket, and sends `{"type": "auth", "ticket": "..."}` as its first message. Tickets are never put in
URLs (URLs land in logs), live 30 s, and work once. The ticket carries the caller's role and grants, so the
socket gets exactly the rights of the page that asked for it.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from conductor.clock import Clock, RealClock

TICKET_TTL_S = 30.0
WS_CLOSE_POLICY = 1008  # policy violation: no/bad ticket, bad Origin


@dataclass(frozen=True)
class Ticket:
    value: str
    role: str
    grants: frozenset[str] = frozenset()  # e.g. device ids a tester may reach
    expires_at: float = 0.0


@dataclass
class TicketBook:
    clock: Clock = field(default_factory=RealClock)
    ttl_s: float = TICKET_TTL_S
    _tickets: dict[str, Ticket] = field(default_factory=dict)

    def issue(self, role: str, grants: Iterable[str] = ()) -> Ticket:
        self._sweep()
        ticket = Ticket(
            secrets.token_urlsafe(24), role, frozenset(grants), self.clock.monotonic() + self.ttl_s
        )
        self._tickets[ticket.value] = ticket
        return ticket

    def redeem(self, value: object) -> Ticket | None:
        """Consume a ticket. Returns None for unknown, reused or expired tickets."""
        self._sweep()
        if not isinstance(value, str):
            return None
        ticket = self._tickets.pop(value, None)
        if ticket is None or ticket.expires_at < self.clock.monotonic():
            return None
        return ticket

    def _sweep(self) -> None:
        now = self.clock.monotonic()
        for k in [k for k, t in self._tickets.items() if t.expires_at < now]:
            del self._tickets[k]


def origin_allowed(headers: Mapping[str, str], extra_allowed: Iterable[str] = ()) -> bool:
    """Accept a WebSocket only from our own origin (same host as the request) or an explicitly allowed one.

    A missing Origin is allowed: browsers always send one on WebSocket upgrades, so its absence means a
    non-browser client (the CLI, tests), which still needs a ticket from an authenticated request.
    """
    origin = headers.get("origin")
    if origin is None:
        return True
    if origin in set(extra_allowed):
        return True
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https"):
        return False
    return parts.netloc.lower() == (headers.get("host") or "").strip().lower()
