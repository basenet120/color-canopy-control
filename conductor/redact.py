"""Secret redaction for every log record in the process (07 §3).

aioafero's securelogging only redacts its own loggers. `install()` makes redaction process-wide: it wraps the
LogRecord factory, so every record from every logger is scrubbed the moment it is created — before any filter,
handler or `logging.lastResort` sees it, and whether or not the root logger has handlers (under plain
`uvicorn conductor.app:app` it has none) — and it also puts `RedactingFilter` on the root logger, its handlers,
uvicorn's handlers and `logging.lastResort` for records built without the factory (`logging.makeLogRecord`).

What is masked: any registered secret (refresh/access/id tokens, the Afero account id), anything that looks like a
bearer token or a JWT, `password=`/`refresh_token=`-style fields, and the account segment of Afero API URLs.
String arguments are scrubbed one by one so formatters that unpack `record.args` (uvicorn's access log) keep
working; a record whose message still carries a secret through a non-string argument is flattened to its
scrubbed text.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from typing import Any

_lock = threading.Lock()
_secrets: set[str] = set()
_MIN_LEN = 6  # never register something short enough to mangle ordinary words

_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWTs
    re.compile(r'(?i)("?(?:refresh_token|access_token|id_token|password|otp|code)"?\s*[:=]\s*"?)[^",\s}&]+'),
    # aioafero logs full Afero URLs, and the account id is secret there (it is known only after the first call).
    # (`{aid}` is the transport's own mask and stays readable.)
    re.compile(r"(?i)(\.afero\.net(?::\d+)?/v1/accounts/)(?!\{aid\}/)[^/\s'\"?&]+"),
]
MASK = "***"
# One-time enrollment links carry their credential in the path (`/enroll/<token>`, D5/D10).
_ENROLL = re.compile(r"(/enroll/)[^/?#\s\"']+")


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
    for pattern in (*_PATTERNS, _ENROLL):
        text = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + MASK, text)
    return text


def redact_record(record: logging.LogRecord) -> None:
    """Scrub one record in place. Idempotent; never raises."""
    if getattr(record, "_redacted", False):
        return
    try:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict) and record.args:
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        if isinstance(record.msg, str) and not record.args:
            record.msg = redact(record.msg)  # never rewrite a format string that still has arguments
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - a broken format string must not crash logging
            message = None
        if message is not None and redact(message) != message:
            record.msg, record.args = redact(message), None  # a secret hid in a non-string argument
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        record._redacted = True
    except Exception:  # noqa: BLE001 - redaction must never take logging down
        record.msg, record.args, record.exc_info, record.exc_text = (
            "[log record dropped by redaction]",
            None,
            None,
            None,
        )


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        redact_record(record)
        return True


class AccessLogFilter(logging.Filter):
    """For uvicorn's access logger, which has its own handler (the root filter never sees it) and whose formatter
    unpacks `record.args` as (client, method, path, http_version, status): redact each text argument in place and
    keep the tuple's shape. Masks `/enroll/<token>` and anything else `redact` knows."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


ACCESS_LOGGERS = ("uvicorn.access",)


def _redacting_factory(inner: Callable[..., logging.LogRecord]) -> Callable[..., logging.LogRecord]:
    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = inner(*args, **kwargs)
        redact_record(record)
        return record

    factory._conductor_redacting = True  # type: ignore[attr-defined]
    return factory


def _add_filter(target: logging.Filterer) -> None:
    if not any(isinstance(x, RedactingFilter) for x in target.filters):
        target.addFilter(RedactingFilter())


def install() -> None:
    """Make redaction process-wide (see the module docstring). Safe to call more than once."""
    current = logging.getLogRecordFactory()
    if not getattr(current, "_conductor_redacting", False):
        logging.setLogRecordFactory(_redacting_factory(current))
    root = logging.getLogger()
    _add_filter(root)
    for logger in (root, *(logging.getLogger(n) for n in ("uvicorn", "uvicorn.error", "uvicorn.access"))):
        for handler in logger.handlers:
            _add_filter(handler)
    if logging.lastResort is not None:
        _add_filter(logging.lastResort)
    for name in ACCESS_LOGGERS:  # uvicorn's access logger doesn't propagate to the root
        logger = logging.getLogger(name)
        if not any(isinstance(x, AccessLogFilter) for x in logger.filters):
            logger.addFilter(AccessLogFilter())
