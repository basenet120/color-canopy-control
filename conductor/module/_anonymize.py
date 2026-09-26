"""A small private anonymizer for the module page's "Copy anonymized JSON" (07 §9, §10).

The shared `conductor/anonymize.py` is being written in parallel; this one only does what the module page needs:
the device's driver address (a metadevice id on the Afero driver) becomes "device-1", and account ids, emails and
lock-holder names are dropped. Swap it for the shared one once that lands.
"""

from __future__ import annotations

import re
from typing import Any

DROP_KEYS = frozenset(
    {"account", "account_id", "accountId", "accounts", "email", "owner", "session_id", "lease", "address"}
)
_EMAIL = re.compile(r"[^@\s\"']+@[^@\s\"']+\.[A-Za-z]{2,}")


def anonymize(value: Any, address: str, *, alias: str = "device-1") -> Any:
    """A deep copy of `value` with `address` replaced by `alias` everywhere and identifying keys dropped."""
    if isinstance(value, dict):
        return {
            anonymize(k, address, alias=alias): anonymize(v, address, alias=alias)
            for k, v in value.items()
            if k not in DROP_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [anonymize(v, address, alias=alias) for v in value]
    if isinstance(value, str):
        out = value.replace(address, alias) if address else value
        return _EMAIL.sub("person@example.invalid", out)
    return value
