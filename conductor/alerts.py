"""Operator alerts for the /ops "Needs attention" list (07 §1 "alert on /ops", §6 "rewrite … then alert").

The canopy engine and the reconciler report operator problems only as telemetry events — `alert` (level info / warn /
error, a plain sentence, sometimes a `device_id`) and `tier` (a hysteresis downgrade or upgrade). This board
subscribes to telemetry and keeps the warn/error ones until they are resolved or someone acknowledges them on /ops,
and logs every alert (warn/error at WARNING, info at INFO) so it also reaches the log tail and the process log.

One line per problem, not per event: an entry has a key, and a newer event with the same key replaces its sentence
and bumps its count.
- `tier` down → "tier:<mode>" (the matching `alert` sentence is folded into it); `tier` up clears it.
- "<module>: the … pattern in <slot> …" → "slot:<device>:<slot>"; "… rewrote it (applied)" clears it.
- "Canopy resting: …" → "outage"; "Hubspace is answering again …" clears it.
- "Commissioning: …" and "7 am test mode change: …" → one key each; an info one (all good) clears it.
- "Published looks: …" (D68) → its event's `alert_key`: "looks:load" (the overlay couldn't be used), "looks:git"
  (a published look masks a deployed config/ change), "looks:repaint:<mode>" (one scene's repaint — keyed by the
  event's `mode_id`, so scene B's success never clears scene A's warn), "looks:show:<mode>" (a look a publish's
  re-pick couldn't show; cleared when the engine shows that mode again), "looks:publish" (a publish rolled back;
  cleared by the next publish that succeeds). An info event clears its own key and every key in its `resolves` list
  (an install clears looks:load and looks:git).
- anything else: its own sentence is the key.
Nothing here writes anything anywhere except the in-memory list and the log. All times are the telemetry's clock.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("conductor.alerts")

KEY = "alerts"  # services.extras key
MAX_ENTRIES = 50
PROBLEM_LEVELS = ("warn", "warning", "error")

_SLOT = re.compile(r"pattern in (custom-\d+)")
_TIER_ALERT = re.compile(r"^[\w-]+: (down|back up) from \w+ to \w+ — ")
TIER_WORDS = {
    "stepped": "T3 stepped, one colour per module a step at a time",
    "canvas": "T1, every head painted",
    "slot": "T2, a slot painted in the app",
    "modules": "T3, one colour per module",
    "strand": "a whole-strand look",
}


@dataclass
class Alert:
    key: str
    level: str
    message: str
    first_t: float
    last_t: float
    count: int = 1
    device_id: str | None = None

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "level": self.level,
            "message": self.message,
            "first_t": self.first_t,
            "last_t": self.last_t,
            "count": self.count,
            "device_id": self.device_id,
        }


class AlertBoard:
    """The warn/error alerts nobody has resolved or acknowledged yet, newest last."""

    def __init__(self, labels: Callable[[str], str] | None = None) -> None:
        self._labels = labels or (lambda mode_id: mode_id)
        self._entries: dict[str, Alert] = {}
        self._lock = threading.Lock()
        self._unsubscribe: Callable[[], Any] | None = None

    # ---- wiring ----------------------------------------------------------------------------------------------
    @classmethod
    def of(cls, services: Any) -> AlertBoard:
        """The app's board, created (and fed the telemetry ring's history) on first use."""
        board = services.extras.get(KEY)
        if board is None:
            board = services.extras[KEY] = cls(labels=_mode_labels(services))
            board.attach(services.telemetry)
        return board

    def attach(self, telemetry: Any) -> None:
        """Replay the telemetry ring's alert history (quietly), then follow new events."""
        for event in telemetry.recent(limit=len(telemetry.ring) or 1):
            if event.get("kind") in ("alert", "tier"):
                self.feed(event, quiet=True)
        self._unsubscribe = telemetry.subscribe(self.feed)

    def detach(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    # ---- events ----------------------------------------------------------------------------------------------
    def feed(self, event: dict, *, quiet: bool = False) -> None:
        kind = event.get("kind")
        if kind == "tier":
            self._tier(event, quiet)
        elif kind == "alert":
            self._alert(event, quiet)

    def _tier(self, event: dict, quiet: bool) -> None:
        mode_id = str(event.get("mode") or "?")
        key = f"tier:{mode_id}"
        if event.get("direction") != "down":
            self._resolve(key)
            return
        label = self._labels(mode_id)
        to, frm = str(event.get("to") or "?"), str(event.get("from") or "?")
        message = (
            f"{label} is running at a lower tier: {TIER_WORDS.get(to, to)} instead of {TIER_WORDS.get(frm, frm)} "
            f"({event.get('reason') or 'no reason given'}). The kiosk copy follows it; it moves back up by itself "
            "after 5 clean minutes."
        )
        self._raise(key, "warn", message, event, quiet)

    def _alert(self, event: dict, quiet: bool) -> None:
        level = str(event.get("level") or "info").lower()
        message = str(event.get("message") or "").strip()
        if not message:
            return
        problem = level in PROBLEM_LEVELS
        if not quiet:
            log.log(logging.WARNING if problem else logging.INFO, "Alert: %s", message)
        if _TIER_ALERT.match(message):
            return  # the `tier` event just before it carries this, with the mode's label
        device_id = event.get("device_id")
        key, resolves = _key_of(message, device_id, event)
        if resolves or not problem:
            if key != message:
                self._resolve(key)
            for other in event.get("resolves") or ():
                self._resolve(str(other))
            return
        self._raise(key, level, message, event, quiet=True)

    def _raise(self, key: str, level: str, message: str, event: dict, quiet: bool) -> None:
        t = float(event.get("t") or 0.0)
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                entry = Alert(key, level, message, t, t, 1, event.get("device_id"))
            else:
                entry.level, entry.message, entry.last_t = level, message, t
                entry.count += 1
                entry.device_id = event.get("device_id") or entry.device_id
            self._entries[key] = entry  # newest last
            while len(self._entries) > MAX_ENTRIES:
                self._entries.pop(next(iter(self._entries)))
        if not quiet:
            log.warning("Needs attention: %s", message)

    def _resolve(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)

    # ---- reads -----------------------------------------------------------------------------------------------
    def active(self) -> list[Alert]:
        with self._lock:
            return list(self._entries.values())

    def ack(self, key: str) -> bool:
        """Acknowledge (hide) one alert until it happens again. False when there's no such alert."""
        with self._lock:
            return self._entries.pop(key, None) is not None


LOOKS_PREFIX = (
    "Published looks:"  # = conductor.looks.live.ALERT_PREFIX (not imported: this module stays leaf)
)


def _key_of(message: str, device_id: Any, event: dict | None = None) -> tuple[str, bool]:
    """(key, resolves?) for an alert sentence (see the module docstring)."""
    event = event or {}
    if message.startswith(LOOKS_PREFIX):  # before the generic rules (a repaint sentence names a slot)
        key = event.get("alert_key")
        if not key:
            mode_id = event.get("mode_id")
            if "couldn't be used" in message:
                key = "looks:load"
            elif "masks config/" in message:
                key = "looks:git"
            else:
                key = f"looks:repaint:{mode_id}" if mode_id else "looks"
        return str(key), False
    slot = _SLOT.search(message)
    if slot and device_id:
        return f"slot:{device_id}:{slot.group(1)}", "rewrote it (applied)" in message
    if message.startswith("Canopy resting:"):
        return "outage", False
    if message.startswith("Hubspace is answering again"):
        return "outage", True
    if message.startswith("Commissioning:"):
        return "commissioning", False
    if message.startswith("7 am test mode change:"):
        return "test_mode_change", False
    return message, False


def _mode_labels(services: Any) -> Callable[[str], str]:
    """A mode's label as the show runs it (the live looks, D68: approved or published)."""

    def label(mode_id: str) -> str:
        try:
            from conductor.looks.live import live_trio

            return str(live_trio(services).modes.mode(mode_id).label or mode_id)
        except Exception:  # noqa: BLE001 - the id is a fine fallback
            return mode_id

    return label
