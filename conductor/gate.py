"""The live gate: runtime modes and the rules for when anything may reach the Afero cloud (07 §1).

This module holds the *policy*. The *enforcement point* is the Afero adapter's own aiohttp session
(`conductor/drivers/afero_transport.py`), which calls `LiveGate.check_request` for every request it sends; nothing
else in the process can reach Afero because nothing else imports aioafero or aiohttp write methods (a grep test
proves it).

Truth table (07 §1):

| CONDUCTOR_MODE | engine drives | Afero calls allowed                                          |
|----------------|---------------|--------------------------------------------------------------|
| sim (default)  | SimDriver     | none — unless HUBSPACE_LIVE=1, then bench jobs only           |
| bench          | SimDriver     | bench jobs + account connect, allowlisted devices only       |
| live           | AferoDriver   | everything, allowlisted devices only                         |
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum

from conductor.clock import Clock, RealClock


class RuntimeMode(StrEnum):
    SIM = "sim"
    BENCH = "bench"
    LIVE = "live"


class Purpose(StrEnum):
    """Why a caller wants to reach Afero. The gate allows or denies per purpose and mode."""

    ENGINE = "engine"  # the show engine: executor, reconciler, idle hold, slot manager
    BENCH = "bench"  # bench jobs and the single-module test page (/ops/module)
    ACCOUNT = "account"  # account connect: login, OTP, token refresh, account id lookup


class GateConfigError(RuntimeError):
    """The environment asks for an unsafe or impossible combination. The process must not start."""


class GateDenied(PermissionError):
    """A call the gate refused. `reason` is plain English and safe to show on /ops."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# Hosts whose non-GET requests are part of signing in, not device writes (07 §1).
AUTH_HOSTS = frozenset({"accounts.hubspaceconnect.com"})
AUTH_PATH_SUFFIXES = ("/conclaveAccess",)


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").replace(";", ",").split(",") if v.strip()]


@dataclass(frozen=True)
class GateSettings:
    mode: RuntimeMode = RuntimeMode.SIM
    live: bool = False  # HUBSPACE_LIVE=1
    live_until: date | None = None  # HUBSPACE_LIVE_UNTIL: after this date, refuse every write
    device_allowlist: frozenset[str] = frozenset()  # HUBSPACE_DEVICE_ALLOWLIST (metadevice ids)
    device_allowlist_registry: bool = False  # HUBSPACE_DEVICE_ALLOWLIST=registry (venue box only)
    account_allowlist: frozenset[str] = frozenset()  # HUBSPACE_ACCOUNT_ALLOWLIST (emails, lower-cased)
    environment: str = "local"  # binds token ciphertext to a runtime (07 §3)

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> GateSettings:
        raw_mode = env.get("CONDUCTOR_MODE", "sim").strip().lower() or "sim"
        try:
            mode = RuntimeMode(raw_mode)
        except ValueError:
            raise GateConfigError(
                f"CONDUCTOR_MODE={raw_mode!r} is not a mode. Use sim, bench or live."
            ) from None
        live = env.get("HUBSPACE_LIVE", "") == "1"
        if mode in (RuntimeMode.BENCH, RuntimeMode.LIVE) and not live:
            raise GateConfigError(
                f"CONDUCTOR_MODE={mode} needs HUBSPACE_LIVE=1 to talk to Hubspace. Set HUBSPACE_LIVE=1 to go live, "
                "or set CONDUCTOR_MODE=sim to run on the simulator."
            )
        until = None
        if raw := env.get("HUBSPACE_LIVE_UNTIL", "").strip():
            try:
                until = date.fromisoformat(raw)
            except ValueError:
                raise GateConfigError(
                    f"HUBSPACE_LIVE_UNTIL={raw!r} is not a date. Use YYYY-MM-DD, e.g. 2026-10-19."
                ) from None
        devices = _split(env.get("HUBSPACE_DEVICE_ALLOWLIST"))
        use_registry = devices == ["registry"]
        if use_registry and mode is not RuntimeMode.LIVE:
            raise GateConfigError(
                "HUBSPACE_DEVICE_ALLOWLIST=registry is only for the venue box (CONDUCTOR_MODE=live). "
                "On staging, list the bench kit's metadevice id instead."
            )
        environment = (
            env.get("CONDUCTOR_ENV") or env.get("RAILWAY_ENVIRONMENT_NAME") or "local"
        ).strip() or "local"
        return cls(
            mode=mode,
            live=live,
            live_until=until,
            device_allowlist=frozenset() if use_registry else frozenset(devices),
            device_allowlist_registry=use_registry,
            account_allowlist=frozenset(e.lower() for e in _split(env.get("HUBSPACE_ACCOUNT_ALLOWLIST"))),
            environment=environment,
        )

    @property
    def engine_driver(self) -> str:
        """Which driver the show engine uses: 'sim' or 'afero'."""
        return "afero" if self.mode is RuntimeMode.LIVE else "sim"


@dataclass
class LiveGate:
    """Answers "may this call reach Afero?" for every purpose, device and account.

    `writable_devices` returns the registry's writable metadevice ids; the effective device allowlist is
    env allowlist ∩ registry writable (or, with HUBSPACE_DEVICE_ALLOWLIST=registry, all registry writable ids).
    """

    settings: GateSettings = field(default_factory=GateSettings)
    writable_devices: Callable[[], Iterable[str]] = lambda: ()
    clock: Clock = field(default_factory=RealClock)

    # ---- purposes -------------------------------------------------------------------------------------------
    def purpose_allowed(self, purpose: Purpose) -> tuple[bool, str]:
        s = self.settings
        if not s.live:
            return False, "Hubspace is off: HUBSPACE_LIVE is not 1 (simulator only)."
        if s.mode is RuntimeMode.SIM:
            if purpose is Purpose.BENCH:
                return True, "sim mode with HUBSPACE_LIVE=1: bench jobs only"
            return False, f"CONDUCTOR_MODE=sim allows only bench jobs to reach Hubspace, not {purpose}."
        if s.mode is RuntimeMode.BENCH:
            if purpose in (Purpose.BENCH, Purpose.ACCOUNT):
                return True, "bench mode"
            return False, "CONDUCTOR_MODE=bench: the show engine stays on the simulator."
        return True, "live mode"

    def cloud_reads_allowed(self, purpose: Purpose) -> bool:
        return self.purpose_allowed(purpose)[0]

    # ---- devices ---------------------------------------------------------------------------------------------
    def effective_allowlist(self) -> frozenset[str]:
        writable = frozenset(self.writable_devices())
        if self.settings.device_allowlist_registry:
            return writable
        return self.settings.device_allowlist & writable

    def live_window_open(self) -> tuple[bool, str]:
        until = self.settings.live_until
        if until is None:
            return True, ""
        today = datetime.fromtimestamp(self.clock.now(), UTC).date()
        if today > until:
            return (
                False,
                f"HUBSPACE_LIVE_UNTIL={until.isoformat()} has passed: this runtime no longer writes.",
            )
        return True, ""

    def check_write(self, device_id: str, purpose: Purpose) -> None:
        """Raise GateDenied unless a write to `device_id` for `purpose` may reach Afero."""
        ok, why = self.purpose_allowed(purpose)
        if not ok:
            raise GateDenied(why)
        ok, why = self.live_window_open()
        if not ok:
            raise GateDenied(why)
        if not device_id:
            raise GateDenied("No device id in the request, so it can't be checked against the allowlist.")
        if device_id not in self.effective_allowlist():
            raise GateDenied(
                f"Device {device_id} is not in the effective allowlist (HUBSPACE_DEVICE_ALLOWLIST ∩ writable "
                "registry devices). Writes to it are refused."
            )

    def device_writable(self, device_id: str, purpose: Purpose) -> bool:
        try:
            self.check_write(device_id, purpose)
        except GateDenied:
            return False
        return True

    # ---- accounts --------------------------------------------------------------------------------------------
    def check_account(self, email: str) -> None:
        """Checked before a password is sent anywhere (07 §1)."""
        ok, why = self.purpose_allowed(Purpose.ACCOUNT)
        if not ok:
            raise GateDenied(why)
        if (email or "").strip().lower() not in self.settings.account_allowlist:
            raise GateDenied(
                "That account is not in HUBSPACE_ACCOUNT_ALLOWLIST for this runtime, so no password was sent. "
                "Show accounts connect only on the venue box."
            )

    # ---- the transport check ---------------------------------------------------------------------------------
    def check_request(self, method: str, host: str, path: str, purpose: Purpose) -> None:
        """The choke point's rule for one HTTP request (called by the adapter's session for every request).

        - Every request needs the purpose to be allowed at all (so sim mode without bench makes no calls).
        - GETs are reads: allowed once the purpose is.
        - Non-GETs to the sign-in host or conclaveAccess are sign-in traffic: allowed once the purpose is.
        - Every other non-GET must name a metadevice in its path, and that id must pass `check_write`.
        """
        ok, why = self.purpose_allowed(purpose)
        if not ok:
            raise GateDenied(why)
        if method.upper() in ("GET", "HEAD", "OPTIONS"):
            return
        if host in AUTH_HOSTS or path.endswith(AUTH_PATH_SUFFIXES):
            return
        self.check_write(metadevice_id_from_path(path), purpose)


def metadevice_id_from_path(path: str) -> str:
    """The id after `/metadevices/` in an Afero URL path, or "" when there isn't one.

    Paths like `/v1/accounts/{aid}/devices/{id}/actions` (the raw attribute write) deliberately return "",
    so they are always refused: that path is documented, not built (04).
    """
    parts = [p for p in path.split("?")[0].split("/") if p]
    for i, part in enumerate(parts[:-1]):
        if part == "metadevices":
            return parts[i + 1]
    return ""
