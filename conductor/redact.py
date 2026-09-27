"""Secret redaction for every log record in the process (07 §3).

aioafero's securelogging only redacts its own loggers. This filter sits on the root handlers and replaces any
registered secret (refresh/access/id tokens, passwords, OTP codes) and anything that looks like a bearer token or
a JWT, in every logger's output.
"""

from __future__ import annotations

import logging
import re
import threading

_lock = threading.Lock()
_secrets: set[str] = set()
_MIN_LEN = 6  # never register something short enough to mangle ordinary words

_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWTs
    re.compile(r'(?i)("?(?:refresh_token|access_token|id_token|password|otp|code)"?\s*[:=]\s*"?)[^",\s}&]+'),
]
MASK = "***"


def add_secret(value: str | None) -> None:
    if value and len(value) >= _MIN_LEN:
        with _lock:
            _secrets.add(value)


def redact(text: str) -> str:
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for s in secrets:
        if s in text:
            text = text.replace(s, MASK)
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + MASK, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string must not crash logging
            return True
        cleaned = redact(message)
        if cleaned != message or record.args:
            record.msg, record.args = cleaned, None
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def install() -> None:
    """Attach the filter to the root logger's handlers (and to the root logger for handler-less setups)."""
    root = logging.getLogger()
    f = RedactingFilter()
    if not any(isinstance(x, RedactingFilter) for x in root.filters):
        root.addFilter(f)
    for handler in root.handlers:
        if not any(isinstance(x, RedactingFilter) for x in handler.filters):
            handler.addFilter(f)
