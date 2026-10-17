"""Contract for roles and device enrollment (07 §2). Implemented in `conductor/roles.py`.

Principals:
- `ops`      — the Basic credential (CONDUCTOR_BASIC_AUTH). Everything.
- `kiosk`    — the guest iPad. Only the kiosk page, its API and the `canopy` topic.
- `tester`   — a crew phone, granted device ids. Only `/ops/module` for those devices —
               never account connect, bench gates, other devices, show accounts or the Lab.
There is no vignette role any more (D67): a saved session or enrollment of it is skipped when the store loads.

Enrollment: ops creates a one-time link (`/enroll/<token>`, valid 24 h, single use). Opening it sets an HttpOnly,
SameSite=Strict (Secure on https) cookie `cc_session` holding a random session id; the server keeps the session →
principal map under `<data>/roles/sessions.json` so sessions survive restarts and can be revoked from /ops.

Path rules (enforced by the auth middleware for cookie principals; Basic = ops passes everything):
  kiosk    : /kiosk, /kiosk/*, /api/kiosk/*, /api/ws-ticket, /api/version, /static/*
  tester   : /ops/module, /ops/module/*, /api/module/<granted>/*, /api/module (list: granted only),
             /api/ws-ticket, /api/version, /static/*
Every other path returns 401 (with the Basic challenge, so an ops user can still sign in on that device).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

SESSION_COOKIE = "cc_session"
ENROLLMENT_TTL_S = 24 * 3600


class Role(StrEnum):
    OPS = "ops"
    KIOSK = "kiosk"
    TESTER = "tester"


@dataclass(frozen=True)
class Principal:
    role: Role
    name: str = ""  # label shown on /ops ("Crew phone")
    grants: frozenset[str] = field(default_factory=frozenset)  # device ids (tester)
    session_id: str | None = None

    def can_device(self, device_id: str) -> bool:
        return self.role is Role.OPS or (self.role is Role.TESTER and device_id in self.grants)


OPS = Principal(Role.OPS, "ops")
