"""Encrypted refresh-token store (07 §3).

- Only refresh tokens are stored, and only here: `<CONDUCTOR_DATA_DIR>/tokens/<account-key>.token`, mode 0600.
- Encrypted with Fernet using `CONDUCTOR_SECRET_KEY`. The ciphertext is bound to the runtime's environment name
  and the account, so a staging token file won't load on the show box.
- Every rotation is persisted atomically (temp file, fsync, `os.replace`), keeping the previous generation
  as `<account-key>.token.prev`.
- A missing or invalid key disables the store (and with it live features) — it never crashes startup.

The encryption only protects the volume and its backups against casual reading: the key lives in the same
runtime's environment. Treat the data dir as secret.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from conductor import redact
from conductor.clock import Clock, RealClock


class TokenStoreUnavailable(RuntimeError):
    """The store can't be used (no/invalid key). The reason is safe to show on /ops."""


@dataclass(frozen=True)
class TokenStatus:
    account_key: str
    generation: int
    saved_at: float
    age_s: float


def account_key(environment: str, account: str) -> str:
    """Stable, non-reversible file name for an account in an environment."""
    return hashlib.sha256(f"{environment}\0{account.strip().lower()}".encode()).hexdigest()[:24]


def validate_secret_key(value: str | None) -> tuple[Fernet | None, str]:
    if not value:
        return None, "CONDUCTOR_SECRET_KEY is not set, so Hubspace sign-in is disabled on this runtime."
    try:
        return Fernet(value.encode()), ""
    except (ValueError, TypeError):
        return None, (
            "CONDUCTOR_SECRET_KEY is not a valid Fernet key (44 url-safe base64 characters), so Hubspace "
            'sign-in is disabled. Generate one with: python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write `data` to `path` atomically: temp file in the same dir, fsync, chmod, replace, fsync the dir."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class TokenStore:
    def __init__(self, data_dir: Path, environment: str, secret_key: str | None, clock: Clock | None = None):
        self.dir = Path(data_dir) / "tokens"
        self.environment = environment
        self.clock = clock or RealClock()
        self._fernet, self.problem = validate_secret_key(secret_key)

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def _require(self) -> Fernet:
        if self._fernet is None:
            raise TokenStoreUnavailable(self.problem)
        return self._fernet

    def _path(self, account: str) -> Path:
        return self.dir / f"{account_key(self.environment, account)}.token"

    def save(self, account: str, refresh_token: str) -> int:
        """Persist a (rotated) refresh token. Returns the new generation number."""
        fernet = self._require()
        if not refresh_token:
            raise ValueError("refusing to store an empty refresh token")
        redact.add_secret(refresh_token)
        path = self._path(account)
        previous = self._read_envelope(path)
        generation = (previous or {}).get("generation", 0) + 1
        if previous is not None:
            atomic_write(path.with_suffix(".token.prev"), json.dumps(previous).encode())
        inner = json.dumps(
            {"refresh_token": refresh_token, "environment": self.environment, "account": account.lower()}
        ).encode()
        envelope = {
            "v": 1,
            "generation": generation,
            "saved_at": self.clock.now(),
            "ciphertext": fernet.encrypt(inner).decode(),
        }
        atomic_write(path, json.dumps(envelope).encode())
        return generation

    def load(self, account: str) -> str | None:
        """The current refresh token, or None when none is stored. Raises if the file is foreign or corrupt."""
        fernet = self._require()
        envelope = self._read_envelope(self._path(account))
        if envelope is None:
            return None
        try:
            inner = json.loads(fernet.decrypt(envelope["ciphertext"].encode()))
        except (InvalidToken, KeyError, ValueError):
            raise TokenStoreUnavailable(
                "The stored Hubspace token can't be decrypted with this runtime's CONDUCTOR_SECRET_KEY. "
                "Sign in again."
            ) from None
        if inner.get("environment") != self.environment or inner.get("account") != account.lower():
            raise TokenStoreUnavailable(
                "The stored Hubspace token belongs to a different runtime or account. Sign in again on this one."
            )
        token = inner["refresh_token"]
        redact.add_secret(token)
        return token

    def status(self, account: str) -> TokenStatus | None:
        envelope = self._read_envelope(self._path(account))
        if envelope is None:
            return None
        return TokenStatus(
            account_key=account_key(self.environment, account),
            generation=envelope.get("generation", 0),
            saved_at=envelope.get("saved_at", 0.0),
            age_s=max(0.0, self.clock.now() - envelope.get("saved_at", 0.0)),
        )

    def delete(self, account: str) -> None:
        for p in (self._path(account), self._path(account).with_suffix(".token.prev")):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _read_envelope(path: Path) -> dict | None:
        try:
            return json.loads(path.read_bytes())
        except FileNotFoundError:
            return None
        except ValueError:
            raise TokenStoreUnavailable(f"The token file {path.name} is corrupt. Sign in again.") from None
