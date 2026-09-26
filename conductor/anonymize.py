"""Scrub device dumps and bench results before they leave the process (07 §9, §10; bench_api.py).

`anonymize(obj)` returns a deep copy with stable placeholders, consistent within one call:

- metadevice ids, device ids and UUIDs → "device-1", "device-2", … (the same id always gets the same
  placeholder, wherever it appears — as a value, inside a longer string such as a URL or an error, or as a key).
  UUIDs that never appear as a device id (function and value ids inside a profile) → "id-1", "id-2", … so a
  scrubbed profile still reads sensibly as a simulator fixture.
- account ids → "account-1", …
- MAC-like strings, and any value of ble-mac-address / wifi-mac-address → "00:00:00:00:00:00"
- wifi-ssid values → "<ssid>"
- emails → "person@example.invalid" (fixture identities under example.invalid / example.com are kept)
- geo-coordinates values → zeros
- friendly / default names → "<name>"
- bearer tokens, JWT-looking strings and anything under a token/password/secret key → "<redacted>"

Row-shaped values (`{"functionClass": "wifi-ssid", "value": …}`) are scrubbed by their function class, so a
device's own state dump is covered as well as plain keys.

`check_clean(text)` lists what still looks identifying in a piece of text (MACs, emails, bearer tokens, JWTs); the
anonymizer test runs it over the simulator profiles and every Markdown file under docs/ and research/.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

ZERO_MAC = "00:00:00:00:00:00"
PLACEHOLDER_EMAIL = "person@example.invalid"
REDACTED = "<redacted>"
SAFE_EMAIL_DOMAINS = ("example.invalid", "example.com")
# Public vendor contact addresses quoted in the research notes (not personal data).
ALLOWED_EMAILS = frozenset({"support@nanoleaf.me", "legal@afero.io"})

UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
MAC_RE = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)

# "id" names a device only on a device-shaped object (see _DEVICE_SHAPE); elsewhere (function and value ids
# inside a profile) it is just an id.
DEVICE_KEYS = frozenset(
    {
        "device_id",
        "deviceId",
        "deviceid",
        "metadeviceId",
        "metadevice_id",
        "metadevice",
        "address",
        "parentDeviceId",
        "parent_id",
        "hubId",
    }
)
_DEVICE_SHAPE = frozenset(
    {"deviceId", "device_id", "typeId", "friendlyName", "friendly_name", "children", "description", "states"}
)
MIN_SUBSTRING_ID = 6  # shorter known ids are replaced only as whole values, never inside other text
ACCOUNT_KEYS = frozenset({"accountId", "account_id", "accountID", "userId", "user_id"})
NAME_KEYS = frozenset({"friendlyName", "friendly_name", "defaultName", "default_name", "friendlyDescription"})
SECRET_KEY_RE = re.compile(r"(token|password|secret|authorization|cookie|otp|passcode)", re.IGNORECASE)
MAC_CLASSES = frozenset({"ble-mac-address", "wifi-mac-address"})
SSID_CLASSES = frozenset({"wifi-ssid"})
GEO_CLASSES = frozenset({"geo-coordinates"})


class _Scrubber:
    def __init__(self, extra_ids: Iterable[str], keep: Iterable[str]):
        self.keep = {k for k in keep if k}
        self.device: dict[str, str] = {}
        self.account: dict[str, str] = {}
        self.other: dict[str, str] = {}
        for value in extra_ids:
            if value and value not in self.keep:
                self._device_placeholder(value)

    # ---- placeholders ------------------------------------------------------------------------------------------
    def _device_placeholder(self, value: str) -> str:
        if value not in self.device:
            self.device[value] = f"device-{len(self.device) + 1}"
        return self.device[value]

    def _account_placeholder(self, value: str) -> str:
        if value not in self.account:
            self.account[value] = f"account-{len(self.account) + 1}"
        return self.account[value]

    # ---- pass 1: collect ids in traversal order so numbering is stable ------------------------------------------
    def collect(self, obj: Any, depth: int = 0) -> None:
        if isinstance(obj, Mapping):
            device_shaped = depth == 0 or any(k in obj for k in _DEVICE_SHAPE)
            for k, v in obj.items():
                key = str(k)
                if isinstance(v, str) and v and v not in self.keep:
                    if key in DEVICE_KEYS or (key == "id" and device_shaped):
                        self._device_placeholder(v)
                    elif key in ACCOUNT_KEYS:
                        self._account_placeholder(v)
                self.collect(v, depth + 1)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                self.collect(v, depth + 1)

    # ---- pass 2: rewrite ---------------------------------------------------------------------------------------
    def scrub(self, obj: Any, key: str | None = None) -> Any:
        if isinstance(obj, Mapping):
            return self._scrub_mapping(obj)
        if isinstance(obj, (list, tuple)):
            return [self.scrub(v, key) for v in obj]
        if isinstance(obj, str):
            return self._scrub_keyed_string(obj, key)
        return obj

    def _scrub_mapping(self, obj: Mapping) -> dict:
        row_class = obj.get("functionClass") if isinstance(obj.get("functionClass"), str) else None
        out: dict = {}
        for k, v in obj.items():
            key = str(k)
            new_key = self.text(key) if isinstance(k, str) else k
            if key == "value" and row_class is not None:
                out[new_key] = self._scrub_row_value(row_class, v)
            else:
                out[new_key] = self._scrub_by_key(key, v)
        return out

    def _scrub_row_value(self, function_class: str, value: Any) -> Any:
        if function_class in MAC_CLASSES:
            return ZERO_MAC
        if function_class in SSID_CLASSES:
            return "<ssid>"
        if function_class in GEO_CLASSES:
            return _zero_geo(value)
        return self.scrub(value, "value")

    def _scrub_by_key(self, key: str, value: Any) -> Any:
        if SECRET_KEY_RE.search(key) and isinstance(value, str) and value:
            return REDACTED
        if key in MAC_CLASSES:
            return ZERO_MAC
        if key in SSID_CLASSES:
            return "<ssid>"
        if key in GEO_CLASSES:
            return _zero_geo(value)
        if key in NAME_KEYS and isinstance(value, str) and value:
            return "<name>"
        return self.scrub(value, key)

    def _scrub_keyed_string(self, value: str, key: str | None) -> str:
        if value in self.keep:
            return value
        if value in self.device:
            return self.device[value]
        if value in self.account:
            return self.account[value]
        return self.text(value)

    def text(self, value: str) -> str:
        """Scrub identifying substrings of free text (URLs, error messages, keys)."""
        if not value:
            return value
        out = value
        for known, placeholder in sorted(
            [*self.device.items(), *self.account.items()], key=lambda kv: -len(kv[0])
        ):
            if len(known) >= MIN_SUBSTRING_ID and known in out and known not in self.keep:
                out = out.replace(known, placeholder)
        out = BEARER_RE.sub(REDACTED, out)
        out = JWT_RE.sub(REDACTED, out)
        out = EMAIL_RE.sub(_email_sub, out)
        out = MAC_RE.sub(ZERO_MAC, out)
        out = UUID_RE.sub(self._uuid_sub, out)
        return out

    def _uuid_sub(self, m: re.Match) -> str:
        raw = m.group(0)
        if raw in self.device:
            return self.device[raw]
        if raw in self.account:
            return self.account[raw]
        if raw not in self.other:
            self.other[raw] = f"id-{len(self.other) + 1}"
        return self.other[raw]


def _email_sub(m: re.Match) -> str:
    email = m.group(0)
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in SAFE_EMAIL_DOMAINS:
        return email
    return PLACEHOLDER_EMAIL


def _zero_geo(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _zero_geo(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_zero_geo(v) for v in value]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return 0
    if isinstance(value, str):
        return "0"
    return value


def anonymize(obj: Any, *, extra_ids: Iterable[str] = (), keep: Iterable[str] = ()) -> Any:
    """A scrubbed deep copy of `obj` (JSON-like: dicts, lists, strings, numbers).

    `extra_ids` are device ids/addresses known to the caller (e.g. the bench target's driver address) that must
    become placeholders even where no key names them. `keep` lists strings that are safe to leave as they are
    (logical registry ids such as "bench-kit", which live in git).
    """
    scrubber = _Scrubber(extra_ids, keep)
    scrubber.collect(obj)
    return scrubber.scrub(obj)


def check_clean(text: str, *, allowed_emails: Iterable[str] = ALLOWED_EMAILS) -> list[str]:
    """Things in `text` that look identifying: MAC addresses (other than all zeros), emails outside the fixture
    domains and the small allowlist, bearer tokens and JWTs. Empty when clean."""
    allowed = {e.lower() for e in allowed_emails}
    problems: list[str] = []
    for m in MAC_RE.finditer(text):
        if m.group(0).replace("-", ":") != ZERO_MAC:
            problems.append(f"MAC address {m.group(0)}")
    for m in EMAIL_RE.finditer(text):
        email = m.group(0).lower()
        if email.rsplit("@", 1)[-1] not in SAFE_EMAIL_DOMAINS and email not in allowed:
            problems.append(f"email {m.group(0)}")
    for m in BEARER_RE.finditer(text):
        problems.append(f"bearer token {m.group(0)[:14]}…")
    for m in JWT_RE.finditer(text):
        problems.append(f"JWT-like string {m.group(0)[:10]}…")
    return problems
