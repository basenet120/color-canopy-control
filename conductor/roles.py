"""Roles, one-time enrollment links and cookie sessions (07 §2; contract in `conductor/roles_api.py`, D5).

Rules implemented here:
- Ops creates a one-time link for a kiosk (the guest iPad) or a tester phone. The link is valid for
  ENROLLMENT_TTL_S (24 h) and works once. Redeeming it creates a session; the session id goes into the HttpOnly
  `cc_session` cookie and lasts SESSION_TTL_S (30 days, the cookie's Max-Age).
- Only sha256 hashes of link tokens and session ids are kept — in memory and on disk — so reading
  `<data>/roles/*.json` (or a backup of it) never yields a working link or cookie. Listings show a short
  fingerprint (the first FINGERPRINT_LEN hex characters of the hash), never a raw value.
- Sessions and pending links survive restarts: every change is written atomically (`tokens.atomic_write`, 0600)
  to `<data>/roles/sessions.json` and `<data>/roles/enrollments.json`. With no data dir the store is in-memory.
- A corrupt file never stops the Conductor from starting: it is set aside as `*.corrupt`, the store starts
  empty (enrolled devices must be re-enrolled) and `problem` says so for /ops.
- A record of a role that no longer exists (the vignette tablets, D67) is old data, not corruption: it is skipped
  (logged once with its role and fingerprint, listed in `skipped`), the other records load, and the next save of
  that file leaves it out. So the guest iPad's kiosk session survives the canopy-only deploy.
- `path_allowed` is the path table from `roles_api.py`, applied to cookie principals by the auth middleware.
  Anything not in the table is denied, and so is any path that isn't already in normal form (dot segments,
  empty segments, encoded characters, backslashes) — browsers never send those, so only a trick would.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

from conductor.clock import Clock, RealClock
from conductor.roles_api import ENROLLMENT_TTL_S, Principal, Role
from conductor.tokens import atomic_write

log = logging.getLogger(__name__)

SESSION_TTL_S = 30 * 24 * 3600  # matches the cc_session cookie's Max-Age
FINGERPRINT_LEN = 12
NAME_MAX = 80
GRANT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")  # registry id shape
ENROLLABLE = (Role.KIOSK, Role.TESTER)  # ops signs in with the Basic credential, never a link
ROLE_VALUES = frozenset(r.value for r in Role)
READ_METHODS = frozenset({"GET", "HEAD"})

# Paths every enrolled role may use (read-only).
COMMON_READ_PATHS = ("/api/ws-ticket", "/api/version")
COMMON_READ_PREFIXES = ("/static",)


class RoleError(ValueError):
    """A request to create or change an enrollment that can't be honoured. The message is safe to show."""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def fingerprint(value: str) -> str:
    """The short, non-reversible label for a link token or session id shown on /ops."""
    return _hash(value)[:FINGERPRINT_LEN]


@dataclass(frozen=True)
class _Record:
    digest: str  # sha256 hex of the token or session id
    role: Role
    name: str
    grants: frozenset[str]
    created_at: float
    expires_at: float

    @property
    def fingerprint(self) -> str:
        return self.digest[:FINGERPRINT_LEN]

    def to_json(self) -> dict:
        return {
            "digest": self.digest,
            "role": self.role.value,
            "name": self.name,
            "grants": sorted(self.grants),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_json(cls, raw: dict) -> _Record:
        role = Role(raw["role"])
        if role not in ENROLLABLE:
            raise ValueError(f"role {role} can't be enrolled")
        digest = str(raw["digest"])
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("digest is not a sha256 hex string")
        return cls(
            digest=digest,
            role=role,
            name=str(raw.get("name", "")),
            grants=frozenset(str(g) for g in raw.get("grants", [])),
            created_at=float(raw["created_at"]),
            expires_at=float(raw["expires_at"]),
        )

    def public(self, now: float) -> dict:
        """JSON-safe view for /ops: no token, no session id, no hash — only the fingerprint."""
        return {
            "fingerprint": self.fingerprint,
            "role": self.role.value,
            "name": self.name,
            "grants": sorted(self.grants),
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "expires_in_s": max(0.0, round(self.expires_at - now, 1)),
        }


def _validate(role: Role | str, name: str, grants: Iterable[str]) -> tuple[Role, str, frozenset[str]]:
    try:
        role = Role(role)
    except ValueError:
        raise RoleError(f"{role!r} isn't a role that can be enrolled. Use kiosk or tester.") from None
    if role not in ENROLLABLE:
        raise RoleError("Ops can't be enrolled with a link: ops devices sign in with the ops password.")
    name = " ".join(str(name or "").split())
    if len(name) > NAME_MAX:
        raise RoleError(f"The name is too long (at most {NAME_MAX} characters).")
    if isinstance(grants, str):
        raise RoleError('grants must be a list of ids, e.g. ["bench-kit"].')
    cleaned = frozenset(str(g).strip() for g in grants)
    bad = sorted(g for g in cleaned if not GRANT_PATTERN.fullmatch(g))
    if bad:
        raise RoleError(
            "These ids don't look like device ids (lower-case letters, digits and dashes): "
            + ", ".join(repr(b[:50]) for b in bad)
        )
    if role is Role.KIOSK and cleaned:
        raise RoleError("A kiosk link doesn't take grants: the kiosk only ever drives the canopy.")
    if role is Role.TESTER and not cleaned:
        raise RoleError("A tester link needs at least one device id the tester may open, e.g. bench-kit.")
    return role, name or role.value, cleaned


def _drop_expired(records: dict[str, _Record], now: float) -> None:
    for digest in [d for d, r in records.items() if r.expires_at <= now]:
        del records[digest]


class RoleStore:
    """Enrollment links and the sessions they create. Thread-safe; file writes are small and atomic."""

    def __init__(self, data_dir: Path | None, clock: Clock | None = None):
        self.clock = clock or RealClock()
        self.dir = Path(data_dir) / "roles" if data_dir is not None else None
        self.problem = ""  # plain English for /ops when a file couldn't be read
        # (file, role, fingerprint) of records skipped as old data: a role that no longer exists (D67)
        self.skipped: list[tuple[str, str, str]] = []
        self._lock = threading.Lock()
        self._enrollments: dict[str, _Record] = self._load("enrollments.json")
        self._sessions: dict[str, _Record] = self._load("sessions.json")
        self._revoke_listeners: list[Callable[[str], None]] = []

    def on_session_revoked(self, fn: Callable[[str], None]) -> None:
        """Call `fn(fingerprint)` after a session is revoked (the WebSocket hub closes that session's sockets)."""
        self._revoke_listeners.append(fn)

    # ---- enrollment -----------------------------------------------------------------------------------------
    def create_enrollment(
        self, role: Role | str, name: str, grants: Iterable[str] = (), ttl_s: float = ENROLLMENT_TTL_S
    ) -> tuple[str, str]:
        """Create a one-time link. Returns (token, "/enroll/<token>"). The raw token is never stored."""
        role, name, grants = _validate(role, name, grants)
        if ttl_s <= 0:
            raise RoleError("An enrollment link needs a positive lifetime.")
        token = secrets.token_urlsafe(32)
        now = self.clock.now()
        record = _Record(_hash(token), role, name, grants, now, now + ttl_s)
        with self._lock:
            _drop_expired(self._enrollments, now)
            self._enrollments[record.digest] = record
            self._save("enrollments.json", self._enrollments)
        log.info("enrollment link %s created for %s %r", record.fingerprint, role.value, name)
        return token, f"/enroll/{token}"

    def peek_enrollment(self, token: object) -> Principal | None:
        """The principal a link would create, without using it up (for the confirmation page)."""
        if not isinstance(token, str) or not token:
            return None
        now = self.clock.now()
        with self._lock:
            record = self._enrollments.get(_hash(token))
        if record is None or record.expires_at <= now:
            return None
        return Principal(record.role, record.name, record.grants)

    def redeem_enrollment(self, token: object) -> tuple[str, Principal] | None:
        """Use a link: returns (session_id, principal), or None for unknown, used or expired links."""
        if not isinstance(token, str) or not token:
            return None
        now = self.clock.now()
        with self._lock:
            record = self._enrollments.pop(_hash(token), None)
            if record is None:
                return None
            if record.expires_at <= now:
                self._save("enrollments.json", self._enrollments)
                return None
            session_id = secrets.token_urlsafe(32)
            session = _Record(
                _hash(session_id), record.role, record.name, record.grants, now, now + SESSION_TTL_S
            )
            _drop_expired(self._sessions, now)
            self._sessions[session.digest] = session
            # Sessions first: if the process dies between the two writes, the link is still usable once more
            # rather than the device being locked out with a consumed link and no session.
            self._save("sessions.json", self._sessions)
            self._save("enrollments.json", self._enrollments)
        log.info("enrollment link %s used; session %s created", record.fingerprint, session.fingerprint)
        return session_id, self._principal(session)

    def revoke_enrollment(self, fingerprint_or_token: str) -> bool:
        """Cancel a link that hasn't been used yet."""
        with self._lock:
            digest = self._find(self._enrollments, fingerprint_or_token)
            if digest is None:
                return False
            del self._enrollments[digest]
            self._save("enrollments.json", self._enrollments)
        return True

    # ---- sessions -------------------------------------------------------------------------------------------
    def principal(self, session_id: object) -> Principal | None:
        """The principal behind a `cc_session` cookie, or None when unknown, revoked or expired."""
        if not isinstance(session_id, str) or not session_id:
            return None
        with self._lock:
            record = self._sessions.get(_hash(session_id))
        if record is None or record.expires_at <= self.clock.now():
            return None
        return self._principal(record)

    def session_active(self, fingerprint: str) -> bool:
        """Is the enrolled session with this fingerprint still valid (not revoked, not expired)? The WebSocket hub
        asks before a ticket issued to it opens a socket (review V5)."""
        now = self.clock.now()
        with self._lock:
            return any(r.fingerprint == fingerprint and r.expires_at > now for r in self._sessions.values())

    def revoke_session(self, fingerprint_or_id: str) -> bool:
        """Revoke by the fingerprint shown on /ops (or the raw session id). Returns False when not found."""
        with self._lock:
            digest = self._find(self._sessions, fingerprint_or_id)
            if digest is None:
                return False
            record = self._sessions.pop(digest)
            self._save("sessions.json", self._sessions)
        log.info("session %s (%s %r) revoked", record.fingerprint, record.role.value, record.name)
        for fn in list(self._revoke_listeners):
            try:
                fn(record.fingerprint)
            except Exception:
                log.exception("session revoke listener failed")
        return True

    def list_sessions(self) -> list[dict]:
        now = self.clock.now()
        with self._lock:
            records = [r for r in self._sessions.values() if r.expires_at > now]
        return [r.public(now) for r in sorted(records, key=lambda r: r.created_at)]

    def list_enrollments(self) -> list[dict]:
        now = self.clock.now()
        with self._lock:
            records = [r for r in self._enrollments.values() if r.expires_at > now]
        return [r.public(now) for r in sorted(records, key=lambda r: r.created_at)]

    # ---- internals ------------------------------------------------------------------------------------------
    @staticmethod
    def _principal(record: _Record) -> Principal:
        # session_id carries the fingerprint, never the raw cookie value, so a Principal is safe to log or echo.
        return Principal(record.role, record.name, record.grants, session_id=record.fingerprint)

    @staticmethod
    def _find(records: dict[str, _Record], key: str) -> str | None:
        if not isinstance(key, str) or not key:
            return None
        key = key.strip()
        if len(key) == FINGERPRINT_LEN and re.fullmatch(r"[0-9a-f]+", key):
            matches = [d for d in records if d.startswith(key)]
            if len(matches) == 1:
                return matches[0]
            return None
        digest = _hash(key)
        return digest if digest in records else None

    def _load(self, name: str) -> dict[str, _Record]:
        if self.dir is None:
            return {}
        path = self.dir / name
        try:
            raw = json.loads(path.read_bytes())
            records = [_Record.from_json(r) for r in raw["records"] if not self._removed_role(name, r)]
        except FileNotFoundError:
            return {}
        except (ValueError, KeyError, TypeError) as e:
            aside = path.with_name(path.name + ".corrupt")
            try:
                path.replace(aside)
            except OSError:
                log.exception("could not set aside %s", path)
            self.problem = (
                f"The saved {name.removesuffix('.json')} file couldn't be read ({type(e).__name__}), so it was "
                f"set aside as {aside.name}. The enrolled guest iPad and tester phones need new links."
            )
            log.error("%s", self.problem)
            return {}
        return {r.digest: r for r in records}

    def _removed_role(self, name: str, raw: object) -> bool:
        """A record whose role no longer exists (D67) is skipped, never a corrupt file. Only that: an unreadable
        record of a role that does exist still sets the whole file aside."""
        if not isinstance(raw, dict) or not isinstance(raw.get("role"), str) or raw["role"] in ROLE_VALUES:
            return False
        role, fingerprint = raw["role"][:40], str(raw.get("digest", ""))[:FINGERPRINT_LEN]
        self.skipped.append((name, role, fingerprint))
        log.warning(
            "roles: skipped a saved %s record of the removed role %r (%s); the next save drops it (D67)",
            name.removesuffix(".json"),
            role,
            fingerprint or "no fingerprint",
        )
        return True

    def _save(self, name: str, records: dict[str, _Record]) -> None:
        if self.dir is None:
            return
        body = {"v": 1, "records": [r.to_json() for r in records.values()]}
        atomic_write(self.dir / name, json.dumps(body, indent=1).encode())


# ---- path rules ---------------------------------------------------------------------------------------------
def normalize_path(path: str) -> str | None:
    """The path in normal form, or None when it isn't one a browser would send for a real page.

    Decodes percent-encoding once; rejects anything that still contains '%', a backslash, a control
    character, an empty interior segment ('//') or a '.'/'..' segment. One trailing slash is dropped.
    """
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    decoded = unquote(path)
    if "%" in decoded or "\\" in decoded or any(ord(c) < 0x20 or ord(c) == 0x7F for c in decoded):
        return None
    if decoded != "/" and decoded.endswith("/"):
        decoded = decoded[:-1]
    segments = decoded.split("/")[1:]
    if decoded != "/" and any(s in ("", ".", "..") for s in segments):
        return None
    return decoded


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _granted_target(path: str, prefix: str, grants: frozenset[str]) -> bool:
    """`path` is `<prefix>/<g>` or below it, for some granted id g."""
    if not path.startswith(prefix + "/"):
        return False
    target = path[len(prefix) + 1 :].split("/", 1)[0]
    return target in grants


def path_allowed(principal: Principal, method: str, path: str) -> bool:
    """The roles_api path table. Ops may use everything; every other role only what's listed for it.

    Pages, /static, /api/version, /api/ws-ticket and the tester's /api/module list are read-only (GET/HEAD);
    the role's own API prefixes accept any method.
    """
    if principal.role is Role.OPS:
        return True
    normal = normalize_path(path)
    if normal is None:
        return False
    method = (method or "").upper()
    read = method in READ_METHODS
    if read and (normal in COMMON_READ_PATHS or any(_under(normal, p) for p in COMMON_READ_PREFIXES)):
        return True
    grants = principal.grants
    if principal.role is Role.KIOSK:
        return (read and _under(normal, "/kiosk")) or normal.startswith("/api/kiosk/")
    if principal.role is Role.TESTER:
        if read and (_under(normal, "/ops/module") or normal == "/api/module"):
            return True
        return _granted_target(normal, "/api/module", grants)
    return False


def home_path(principal: Principal) -> str:
    """Where an enrolled device lands after using its link."""
    if principal.role is Role.KIOSK:
        return "/kiosk/"
    if principal.role is Role.TESTER:
        return "/ops/module/"
    return "/"
