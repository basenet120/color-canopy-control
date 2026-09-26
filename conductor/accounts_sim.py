"""`SimAccounts`: a simulated Hubspace sign-in, so /ops/accounts and the CLI can be exercised and screenshotted in
sim mode without Hubspace (07 §3, §9).

Rules implemented here:
- Nothing reaches the network and **nothing is stored anywhere** — no token files, no passwords; the "connected"
  flag lives in memory for this process only.
- Only fixture identities sign in: an email ending in `@example.invalid` (07 §9 screenshot rule). Any other email
  is refused, the way the live gate refuses a non-allowlisted account.
- Password `needs-code` triggers a simulated emailed-code step; code `000000` completes it, any other code
  re-prompts on the same nonce (like `InvalidOTP`). A pending sign-in expires after `PENDING_TTL_S`.
- Every message says it's simulated, and never echoes what was submitted.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass

from conductor.accounts_api import PENDING_TTL_S, AccountStatus, LoginOutcome, LoginState
from conductor.clock import Clock
from conductor.config import Registry

FIXTURE_DOMAIN = "@example.invalid"
OTP_PASSWORD = "needs-code"
OTP_CODE = "000000"
PREFIX = "Simulator: "


@dataclass
class _SimPending:
    account: str
    expires_at: float


class SimAccounts:
    """`AccountManager` for sim mode. Accepts only fixture identities; stores nothing."""

    simulated = True

    def __init__(self, registry: Registry | Callable[[], Registry], clock: Clock):
        self._registry = registry
        self.clock = clock
        self._connected: dict[str, float] = {}  # account → simulated connect time
        self._pending: dict[str, _SimPending] = {}

    @property
    def registry(self) -> Registry:
        return self._registry() if callable(self._registry) else self._registry

    def _accounts(self) -> list[str]:
        return list(self.registry.accounts())

    def _purge(self) -> None:
        now = self.clock.monotonic()
        for nonce, pending in list(self._pending.items()):
            if pending.expires_at <= now:
                del self._pending[nonce]

    def _connect(self, account: str) -> LoginOutcome:
        self._connected[account] = self.clock.now()
        return LoginOutcome(
            LoginState.CONNECTED,
            PREFIX + "connected (simulated). Nothing was sent to Hubspace and nothing was stored.",
            account=account,
        )

    async def start_login(self, account: str, email: str, password: str) -> LoginOutcome:
        if account not in self._accounts():
            return LoginOutcome(LoginState.FAILED, PREFIX + "that isn't one of the registry's accounts.")
        if not (email or "").strip().lower().endswith(FIXTURE_DOMAIN) or not password:
            return LoginOutcome(
                LoginState.FAILED,
                PREFIX + "only fixture identities ending in @example.invalid can sign in here. "
                "Nothing was sent anywhere.",
                account=account,
            )
        self._purge()
        if password == OTP_PASSWORD:
            nonce = secrets.token_urlsafe(24)
            self._pending[nonce] = _SimPending(account, self.clock.monotonic() + PENDING_TTL_S)
            return LoginOutcome(
                LoginState.OTP_REQUIRED,
                PREFIX + f"a sign-in code would be emailed now. Enter {OTP_CODE} (simulated code).",
                nonce=nonce,
                account=account,
            )
        return self._connect(account)

    async def submit_otp(self, nonce: str, code: str) -> LoginOutcome:
        self._purge()
        pending = self._pending.get(nonce or "")
        if pending is None:
            return LoginOutcome(
                LoginState.FAILED,
                PREFIX
                + "that sign-in has expired or isn't known. A pending sign-in lasts 10 minutes and is lost "
                "if the Conductor restarts; start again.",
            )
        if (code or "").strip() != OTP_CODE:
            return LoginOutcome(
                LoginState.OTP_REQUIRED,
                PREFIX + "that code wasn't accepted (simulated). Try again.",
                nonce=nonce,
                account=pending.account,
            )
        del self._pending[nonce]
        return self._connect(pending.account)

    def status(self) -> list[AccountStatus]:
        now = self.clock.now()
        out = []
        for account in self._accounts():
            since = self._connected.get(account)
            out.append(
                AccountStatus(
                    account=account,
                    connected=since is not None,
                    simulated=True,
                    token_age_s=None if since is None else round(now - since, 1),
                    last_error=None if since is not None else PREFIX + "not connected (simulated).",
                )
            )
        return out

    async def disconnect(self, account: str) -> None:
        self._connected.pop(account, None)
        for nonce, pending in list(self._pending.items()):
            if pending.account == account:
                del self._pending[nonce]
