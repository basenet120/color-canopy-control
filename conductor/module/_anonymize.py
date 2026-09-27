"""The module page's "Copy anonymized JSON" (07 §9, §10): drops identifying keys (accounts, emails, lock holders,
leases), maps the device's driver address to "device-1", then runs the shared scrubber `conductor.anonymize`
(MACs, SSIDs, UUIDs, emails, names, geo, bearer strings)."""

from __future__ import annotations

import re
from typing import Any

DROP_KEYS = frozenset(
    {"account", "account_id", "accountId", "accounts", "email", "owner", "session_id", "lease", "address"}
)
_EMAIL = re.compile(r"[^@\s\"']+@[^@\s\"']+\.[A-Za-z]{2,}")


def anonymize(value: Any, address: str, *, alias: str = "device-1") -> Any:
    """Module-page export: local key drops + address alias, then the shared scrubber."""
    from conductor import anonymize as shared

    return shared.anonymize(_local(value, address, alias=alias), keep=(alias,))


def _local(value: Any, address: str, *, alias: str = "device-1") -> Any:
    """A deep copy of `value` with `address` replaced by `alias` everywhere and identifying keys dropped."""
    if isinstance(value, dict):
        return {
            _local(k, address, alias=alias): _local(v, address, alias=alias)
            for k, v in value.items()
            if k not in DROP_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_local(v, address, alias=alias) for v in value]
    if isinstance(value, str):
        out = value.replace(address, alias) if address else value
        return _EMAIL.sub("person@example.invalid", out)
    return value
