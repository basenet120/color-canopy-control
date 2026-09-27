"""Contract for connecting Hubspace accounts (07 §3). Implemented by the Afero adapter's `AferoAccounts` and,
for the simulator, `SimAccounts` (so the flow can be exercised and screenshotted without Hubspace).

Rules every implementation keeps:
- `LiveGate.check_account(email)` runs **before** a password is sent anywhere.
- Passwords are never stored or logged; they live only for the duration of the call.
- An emailed-code challenge keeps the pending `AferoAuth` object in memory under a random nonce for
  `PENDING_TTL_S`; `submit_otp` must use that same object. `InvalidOTP` → re-prompt on the same nonce.
  A restart loses pending logins — pages say so.
- On success only the refresh token is persisted (TokenStore), and every rotation is persisted after that.
- A dead token surfaces as `needs_reauth=True` in `status()`; nothing ever loops logins.
- Messages in `LoginOutcome.message` are plain English and never echo submitted fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

PENDING_TTL_S = 600.0


class LoginState(StrEnum):
    CONNECTED = "connected"
    OTP_REQUIRED = "otp_required"
    FAILED = "failed"


@dataclass
class LoginOutcome:
    state: LoginState
    message: str
    nonce: str | None = None  # present when state is OTP_REQUIRED
    account: str | None = None  # logical account name


@dataclass
class AccountStatus:
    account: str  # logical account name
    connected: bool = False
    needs_reauth: bool = False
    simulated: bool = False
    token_generation: int | None = None
    token_age_s: float | None = None
    last_refresh_at: float | None = None
    device_count: int = 0
    unknown_devices: list[str] = field(default_factory=list)  # visible but not registered → read-only + alert
    last_error: str | None = None

    def to_json(self) -> dict:
        return dict(self.__dict__)


class AccountManager(Protocol):
    simulated: bool

    async def start_login(self, account: str, email: str, password: str) -> LoginOutcome: ...

    async def submit_otp(self, nonce: str, code: str) -> LoginOutcome: ...

    def status(self) -> list[AccountStatus]: ...

    async def disconnect(self, account: str) -> None: ...
