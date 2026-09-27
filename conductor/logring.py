"""The /ops log tail: the last N log records, kept in memory (07 §10 "log tail").

`install()` puts one `LogRing` handler on the root logger (and on uvicorn's own logger, which doesn't propagate
under uvicorn's logging config) and returns it; the lead calls it in the app lifespan, after `redact.install()`, and
routes read it back with `installed()` (or `services.extras["logring"]`).

Secrets: every record is already scrubbed process-wide when it is created (`conductor/redact.py`, D16). The ring
still runs `RedactingFilter` and `redact()` over the formatted text before storing it, so a record built some other
way (`logging.makeLogRecord`) can't carry a registered secret, bearer token, JWT or enrollment token into /ops.

Records are plain dicts: `{seq, t, level, logger, message, exc}` — `seq` rises by one per stored record, so a page
polls `records(since=<last seq it saw>)` and gets only what's new. Nothing is written to disk here.
"""

from __future__ import annotations

import logging
import threading
from collections import deque

from conductor.redact import RedactingFilter, redact

DEFAULT_CAPACITY = 1000
MAX_MESSAGE_CHARS = 2000
MAX_EXC_CHARS = 4000

_install_lock = threading.Lock()
_installed: LogRing | None = None
_raised_app_level = False  # install() set the `conductor` logger to INFO (uninstall() puts it back)


class LogRing(logging.Handler):
    """A logging handler that keeps the last `capacity` records, redacted, for the /ops log tail."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY, level: int = logging.INFO) -> None:
        super().__init__(level)
        self.capacity = capacity
        self._records: deque[dict] = deque(maxlen=capacity)
        self._seq = 0
        self.addFilter(RedactingFilter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = redact(record.getMessage())
            exc = record.exc_text
            if not exc and record.exc_info and record.exc_info[1] is not None:
                exc = logging.Formatter().formatException(record.exc_info)
            entry = {
                "t": round(float(record.created), 3),
                "level": record.levelname,
                "logger": record.name,
                "message": _clip(message, MAX_MESSAGE_CHARS),
                "exc": _clip(redact(exc), MAX_EXC_CHARS) if exc else None,
            }
            # Handler.handle() holds self.lock around emit(), so seq and the deque change together.
            self._seq += 1
            entry["seq"] = self._seq
            self._records.append(entry)
        except Exception:  # noqa: BLE001 - logging must never take the app down
            self.handleError(record)

    @property
    def last_seq(self) -> int:
        return self._seq

    def records(self, since: int | None = None, limit: int = 200) -> list[dict]:
        """Records with `seq > since` (all kept records when None), oldest first, at most the newest `limit`."""
        self.acquire()
        try:
            items = list(self._records)
        finally:
            self.release()
        if since is not None:
            items = [r for r in items if r["seq"] > since]
        limit = max(0, int(limit))
        return [dict(r) for r in items[-limit:]] if limit else []

    def clear(self) -> None:
        self.acquire()
        try:
            self._records.clear()
        finally:
            self.release()


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def install(capacity: int = DEFAULT_CAPACITY, level: int = logging.INFO) -> LogRing:
    """Attach the ring (once per process) and return it. Safe to call more than once: later calls return the
    same handler.

    Also lets the Conductor's own INFO records through (the `conductor` logger is set to INFO when it has no
    level of its own; under plain uvicorn the root logger stays at WARNING, so nothing extra reaches stderr).
    """
    global _installed, _raised_app_level
    with _install_lock:
        if _installed is not None:
            return _installed
        ring = LogRing(capacity, level)
        logging.getLogger().addHandler(ring)
        uv = logging.getLogger("uvicorn")
        if not uv.propagate:  # uvicorn's dictConfig: its records never reach the root logger
            uv.addHandler(ring)
        app_logger = logging.getLogger("conductor")
        if app_logger.level == logging.NOTSET:
            app_logger.setLevel(logging.INFO)
            _raised_app_level = True
        _installed = ring
        return ring


def installed() -> LogRing | None:
    """The ring `install()` attached, or None."""
    return _installed


def uninstall() -> None:
    """Detach the ring (tests)."""
    global _installed, _raised_app_level
    with _install_lock:
        ring, _installed = _installed, None
        if ring is None:
            return
        for name in ("", "uvicorn"):
            logging.getLogger(name).removeHandler(ring)
        if _raised_app_level:
            logging.getLogger("conductor").setLevel(logging.NOTSET)
            _raised_app_level = False
