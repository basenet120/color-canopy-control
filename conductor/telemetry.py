"""Telemetry from day one (05 #10): every command, attempt, ack, readback and error, with timestamps.

Events go to an in-memory ring (for /ops, the module page and WebSocket push) and to JSONL files under
`<CONDUCTOR_DATA_DIR>/telemetry/YYYY-MM-DD.jsonl`. Events never carry secrets or full payloads — counts,
statuses, timings, ids and short plain-English errors only.

Event kinds (field `kind`), all with `t` (wall epoch s) and usually `device_id`:
- `command`        label, purpose, rows (count), bytes, status (final CommandStatus), timings
                    {queued_at, sent_at, acked_at, verified_at}, attempts, mismatches (count), simulated
- `attempt`        attempt n, outcome, http_status, elapsed_s, error
- `readback`       outcome, http_status, elapsed_s, mismatches, available
- `gate_denied`    reason, purpose
- `lock`           action (acquire/release/expire/steal), owner (steal: previous_owner; expire: expired_at)
- `token`          account, action (ensure_fresh: the pre-burst check, 07 §6), outcome, elapsed_s, error
- `mode`           mode, tier, source (kiosk/ops/idle/reconciler)
- `tier`           mode, from, to, reason
- `alert`          level (info/warn/error), message
- `bench`          job, step, detail
"""

from __future__ import annotations

import json
import logging
from collections import deque
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conductor.clock import Clock, RealClock

log = logging.getLogger(__name__)
RING_SIZE = 5000


def percentile(values: Iterable[float], pct: float) -> float | None:
    data = sorted(values)
    if not data:
        return None
    k = (len(data) - 1) * pct / 100.0
    lo, hi = int(k), min(int(k) + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


class Telemetry:
    def __init__(self, data_dir: Path | None, clock: Clock | None = None, ring_size: int = RING_SIZE):
        self.clock = clock or RealClock()
        self.dir = Path(data_dir) / "telemetry" if data_dir else None
        self.ring: deque[dict] = deque(maxlen=ring_size)
        self._subscribers: list[Callable[[dict], None]] = []
        self._write_failed = False

    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        self._subscribers.append(fn)
        return lambda: self._subscribers.remove(fn) if fn in self._subscribers else None

    def record(self, kind: str, **fields: Any) -> dict:
        event = {"t": round(self.clock.now(), 3), "kind": kind, **fields}
        self.ring.append(event)
        self._persist(event)
        for fn in list(self._subscribers):
            try:
                fn(event)
            except Exception:
                log.exception("telemetry subscriber failed")
        return event

    def _persist(self, event: dict) -> None:
        if self.dir is None:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            day = datetime.fromtimestamp(event["t"], UTC).strftime("%Y-%m-%d")
            with open(self.dir / f"{day}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str, separators=(",", ":")) + "\n")
            self._write_failed = False
        except OSError:
            if not self._write_failed:
                log.exception("telemetry file write failed; keeping events in memory only")
            self._write_failed = True

    def recent(
        self,
        *,
        kind: str | None = None,
        device_id: str | None = None,
        since: float | None = None,
        limit: int = 200,
    ) -> list[dict]:
        out = [
            e
            for e in self.ring
            if (kind is None or e["kind"] == kind)
            and (device_id is None or e.get("device_id") == device_id)
            and (since is None or e["t"] >= since)
        ]
        return out[-limit:]

    def command_stats(self, *, device_id: str | None = None, since: float | None = None) -> dict:
        """p50/p95 of send→ack and send→verified for commands, plus counts and the last error."""
        cmds = self.recent(kind="command", device_id=device_id, since=since, limit=RING_SIZE)
        ack, verified = [], []
        for c in cmds:
            t = c.get("timings") or {}
            if t.get("sent_at") and t.get("acked_at"):
                ack.append(t["acked_at"] - t["sent_at"])
            if t.get("sent_at") and t.get("verified_at"):
                verified.append(t["verified_at"] - t["sent_at"])
        errors = [c for c in cmds if c.get("status") not in ("applied", "superseded")]
        return {
            "count": len(cmds),
            "ack_p50_s": percentile(ack, 50),
            "ack_p95_s": percentile(ack, 95),
            "verified_p50_s": percentile(verified, 50),
            "verified_p95_s": percentile(verified, 95),
            "failures": len(errors),
            "last_error": (errors[-1].get("error") or errors[-1].get("status")) if errors else None,
        }
