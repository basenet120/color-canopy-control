"""The stepped rehearsal (Gate R, D65): one operator-started, bounded stepped run that measures the write rate.

    /ops "Rehearse…" ──confirm sentence (modules, minutes)──▶ POST /api/canopy/stepped-rehearsal
        ──▶ CanopyEngine.rehearse(mode, minutes, source="ops") ──▶ one stepped run of that mode, run_s = minutes·60
                                │ telemetry (attempt / command / alert / governor / program)
                                ▼
            Rehearsal (this module) counts the window ──▶ report: telemetry "rehearsal" + <data>/bench/rehearsals/*.json

Rules (binding text: docs/runbooks/stepped-rehearsal.md; 07 §1, §6, §8, §9):
- **Operator-started only.** Nothing schedules a rehearsal: the only caller is the ops route, behind a confirm
  sentence that names the modules it writes to and the minutes (`confirm_sentence`). It never runs from cron, a
  routine or CI (07 §9). Runs are 1–`MAX_MINUTES` minutes.
- **Gate.** While it runs, `CanopyEngine.rehearsal_mode` names the mode, and the show opens the stepped tier on live
  hardware for that mode only (`ShowWiring._stepped_allowed`); everything else keeps `motion.stepped.live_enabled`.
  The design is the mode's own stepped technique, or else its module-level design (the `modules` sample) stepped at
  the D65 defaults (`ShowWiring.rehearsal_modes`). Writes go only through the engine to the canopy modules the plan
  drives, so the live gate's allowlist (07 §1) applies to every one of them.
- **It stops** at the first HTTP 403 on any account (the venue IP may be blocked: nothing more is sent for it, and
  no re-run or stepped look on that account for `governor_cooloff_s`; `CanopyEngine._rehearsal_403`), when the
  throttle governor trips
  (conductor/sequence.py), when ops stops it, pauses guest input or chooses another look, when the reconciler moves
  the look down its ladder, and when the engine stops. Kiosk requests are refused while it runs (a plain sentence),
  and the idle hold waits for it.
- **The window** runs from the start to the end of the run plus the results of the writes still in flight then (at
  most `REPORT_GRACE_S`); the engine settles the look only after that, so its writes are never counted. Every count comes from the telemetry the executor and the engine record anyway:
  - `attempts` / `puts`: write attempts to the rehearsal's modules (retries included) / those that left the process;
  - `throttled_429`, `forbidden_403`, `throttled_share` (429 + 403 of the attempts);
  - `max_writes_60s`: per account, the most write attempts that left the process within any 60 s (every device on
    the account, not just the rehearsal's), by when each was sent (its answer's time less its `elapsed_s`), against
    `cap_writes_per_min` (`motion.stepped.account_writes_per_min`; the runner plans its steps below it);
  - `commands` by final status; `failed_throttled`: commands that still ended 429/403 after the executor's retries;
    `superseded`: step writes replaced by a newer one before they finished; `skipped_steps`: steps the runner skipped
    because they came due late;
  - `verification_s`: send → verified readback, p50 / p95, over the rehearsal's applied commands;
  - `governor_trips`, `outages`.
  The venue IP's other Hubspace traffic is now only the bench account's (the vignettes are outside this system,
  D67), so the report no longer has a `vignette` section.
- **Verdict** (Gate R's pass criteria): the run went its full length, no command failed throttled after retries,
  throttled attempts < `MAX_THROTTLED_SHARE`, no 403, the rehearsal's accounts within the cap in every 60 s
  (`max_writes_60s` ≤ `cap_writes_per_min`). In the simulator the verdict is
  labelled SIMULATED and never counts for Gate R. Only Alon flips `motion.stepped.live_enabled`.
- The report file is written atomically under `<data>/bench/rehearsals/` (07 §8), beside the bench results but not
  among them (the bench page lists only `<data>/bench/*.json`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from conductor.drivers.base import Outcome
from conductor.executor_api import CommandStatus
from conductor.telemetry import percentile
from conductor.tokens import atomic_write

if TYPE_CHECKING:
    from conductor.canopy import CanopyEngine
    from conductor.sequence import ProgramRunner

log = logging.getLogger(__name__)

MAX_MINUTES = 10
MAX_THROTTLED_SHARE = 0.05  # Gate R: throttled attempts must stay under 5 %
WINDOW_S = 60.0  # max_writes_60s
REPORT_GRACE_S = 20.0  # wait at most this long for the writes still in flight when the run ends
THROTTLE_STATUSES = frozenset({429, 403})
OUTAGE_HTTP_STATUSES = frozenset({503, 504})
REHEARSAL_DIR = "rehearsals"  # under <data>/bench
SIMULATED_NOTE = (
    "SIMULATED: the simulator's latencies and faults, not Hubspace's. This never counts for Gate R; run it on the real "
    "kits from the show box (docs/runbooks/stepped-rehearsal.md)."
)


class RehearsalRefused(ValueError):
    """A rehearsal can't start (or stop) now: the message is a plain sentence for /ops."""


def _names(labels: list[str]) -> str:
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def confirm_sentence(
    *,
    label: str,
    minutes: int,
    modules: list[str],
    accounts: list[str],
    simulated: bool,
    cap: float,
    planned: float,
) -> str:
    """What ops confirms before a rehearsal starts: the look, the minutes and every module it writes to, and the
    write rate as the runner enforces it (steps paced at `planned` a minute; a step that would take the account past
    `cap` in 60 s is held back; Gate R fails when retries and re-sends take it past `cap` anyway)."""
    n = len(modules)
    where = f"account {accounts[0]}" if len(accounts) == 1 else f"accounts {_names(accounts)}"
    sim = " (simulator: no real lights)" if simulated else ""
    return (
        f"Rehearse {label} stepped for {minutes} {'minute' if minutes == 1 else 'minutes'} on "
        f"{_names(modules)} ({n} {'module' if n == 1 else 'modules'} on {where}){sim}? Each step writes every "
        f"module whose colour changes, paced at up to {planned:g} writes a minute; a step that would take the "
        f"account past {cap:g} writes in a minute is held back, and the report fails if retries take it past "
        f"{cap:g}. The run stops at the first 403 or if the throttle governor trips, then the still look holds. "
        "Guests can't change the canopy meanwhile."
    )


def _wall(t: float | None) -> str | None:
    if t is None:
        return None
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Rehearsal:
    """One rehearsal's window: started by `CanopyEngine.rehearse`, ended by `CanopyEngine._end_rehearsal`."""

    def __init__(
        self,
        engine: CanopyEngine,
        *,
        mode: str,
        label: str,
        minutes: int,
        modules: dict[str, str],  # device id → label
        accounts: list[str],
        source: str,
        report_dir: Path | None,
    ) -> None:
        self.engine = engine
        self.clock = engine.clock
        self.mode = mode
        self.label = label
        self.minutes = minutes
        self.run_s = float(minutes * 60)
        self.modules = dict(modules)
        self.accounts = list(accounts)
        self.source = source
        self.report_dir = report_dir
        self.simulated = engine._simulated()
        self.started = self.clock.now()
        self.started_mono = self.clock.monotonic()
        self.run_id = f"rehearsal-{datetime.fromtimestamp(self.started, UTC):%Y%m%dT%H%M%SZ}-{mode}"
        self.saved_floor: int | None = None  # the mode's hysteresis floor before it (restored after)
        self.runner: ProgramRunner | None = None  # the newest runner of this rehearsal's look
        self.ended: str | None = None  # why it ended
        self.ended_at: float | None = None
        self.completed = False
        self.report: dict | None = None
        self.path: Path | None = None
        self._task: asyncio.Task | None = None
        self._then: Callable[[], Any] | None = None
        self._unsubscribe: Callable[[], Any] | None = None
        self._outages0 = len(engine.stats.get("outages") or [])
        # counters
        self.attempts = 0
        self.puts = 0
        self.throttled_429 = 0
        self.forbidden_403 = 0
        self.forbidden_any: list[str] = []  # accounts that answered 403 (any device)
        self.commands: Counter[str] = Counter()
        self.failed_throttled = 0
        self.verified: list[float] = []
        self.governor_trips = 0
        self._last_attempt: dict[str, tuple[str, int | None]] = {}  # address → (outcome, http status)
        # account → monotonic send times of the writes that left the process
        self._sent: dict[str, list[float]] = {}
        self.alerts: list[dict] = []

    # ---- the window ---------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._unsubscribe = self.engine.telemetry.subscribe(self._on_event)
        self.engine.telemetry.record(
            "rehearsal",
            action="start",
            run_id=self.run_id,
            mode=self.mode,
            minutes=self.minutes,
            modules=len(self.modules),
            source=self.source,
            simulated=self.simulated,
        )

    def _entry(self, address: str) -> Any:
        try:
            return self.engine.deps.registry().logical_for(address)
        except Exception:  # noqa: BLE001 - an address the registry can't place is just "unknown"
            return None

    def _account(self, address: str, entry: Any) -> str:
        if self.engine.device_of(address) is not None:
            return self.engine._account_of(address)
        return str(getattr(entry, "account", None) or "unknown")

    def _mine(self, address: str) -> bool:
        return self.engine.device_of(address) in self.modules

    def _on_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind == "attempt":
            self._on_attempt(event)
        elif kind == "command":
            self._on_command(event)
        elif kind == "alert":
            self._on_alert(event)
        elif kind == "governor":
            self.governor_trips += 1
        elif kind == "program" and event.get("action") == "end":  # the run ended (only this look's runs here)
            self._call_soon(lambda: self.engine._end_rehearsal(str(event.get("reason")), rehearsal=self))

    def _on_attempt(self, event: dict) -> None:
        address = str(event.get("device_id"))
        outcome, status = str(event.get("outcome")), event.get("http_status")
        left = outcome != str(Outcome.DENIED)  # a denied write never left the process
        entry = self._entry(address)
        account = self._account(address, entry)
        self._last_attempt[address] = (outcome, status)
        if left:  # when it was sent: its answer came back `elapsed_s` later
            sent_at = self.clock.monotonic() - float(event.get("elapsed_s") or 0.0)
            self._sent.setdefault(account, []).append(sent_at)
        if self._mine(address):
            self.attempts += 1
            self.puts += left
            self.throttled_429 += status == 429
            self.forbidden_403 += status == 403
        if status == 403:  # the engine stops the run at the first one (once: later ones find it ended)
            self.forbidden_any.append(account)
            self._call_soon(lambda: self.engine._rehearsal_403(self, account))

    def _on_command(self, event: dict) -> None:
        address = str(event.get("device_id"))
        status = str(event.get("status"))
        if self._mine(address):
            self.commands[status] += 1
            last = self._last_attempt.get(address)
            if (
                status == str(CommandStatus.FAILED)
                and last is not None
                and last[0] == str(Outcome.THROTTLED)
                and last[1] not in OUTAGE_HTTP_STATUSES  # 503/504: an outage, not a throttle
            ):
                self.failed_throttled += 1
            timings = event.get("timings") or {}
            if status == str(CommandStatus.APPLIED) and timings.get("sent_at") and timings.get("verified_at"):
                self.verified.append(float(timings["verified_at"]) - float(timings["sent_at"]))

    def _on_alert(self, event: dict) -> None:
        level = str(event.get("level") or "info")
        message = str(event.get("message") or "")
        self.alerts.append({"t": event.get("t"), "level": level, "message": message})

    def _call_soon(self, fn: Callable[[], Any]) -> None:
        """Act after the event that triggered it (never from inside the executor's or the runner's bookkeeping)."""
        try:
            asyncio.get_running_loop().call_soon(fn)
        except RuntimeError:
            fn()

    # ---- the end ------------------------------------------------------------------------------------------------
    def end(self, reason: str, *, waiting: list[asyncio.Task], then: Callable[[], Any] | None = None) -> None:
        """The run is over (any reason): the report is written once the writes in flight have their results, and
        then `then` runs (the engine's settle, so its writes never count)."""
        if self.ended is not None:
            return
        self._then = then
        self.ended, self.ended_at = reason, self.clock.now()
        runner = self.runner
        self.completed = runner is not None and runner.index >= runner.count  # the run reached its own end
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._close()
            return
        self._task = asyncio.ensure_future(self._finish(waiting))

    async def _finish(self, waiting: list[asyncio.Task]) -> None:
        pending = [t for t in waiting if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=REPORT_GRACE_S)
        self._close()

    async def wait(self) -> None:
        """Until the report is written (tests, the engine's stop)."""
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    def _close(self) -> None:
        if self._unsubscribe is not None:
            with contextlib.suppress(Exception):
                self._unsubscribe()
            self._unsubscribe = None
        self.report = self.build_report()
        self.path = self._save(self.report)
        r = self.report
        self.engine.telemetry.record(
            "rehearsal",
            action="end",
            run_id=self.run_id,
            mode=self.mode,
            minutes=self.minutes,
            reason=self.ended,
            completed=self.completed,
            verdict=r["verdict"],
            attempts=r["attempts"],
            puts=r["puts"],
            throttled_429=r["throttled_429"],
            forbidden_403=r["forbidden_403"],
            failed_throttled=r["failed_throttled"],
            governor_trips=r["governor_trips"],
            file=self.path.name if self.path else None,
            simulated=self.simulated,
        )
        level = "info" if r["verdict"] == "pass" else "warn"
        sim = "SIMULATED " if self.simulated else ""
        self.engine.telemetry.record(
            "alert",
            level=level,
            message=f"Stepped rehearsal: {sim}{r['summary']}",
            source="rehearsal",
        )
        self.engine._rehearsal_reported(self)
        then, self._then = self._then, None
        if then is not None:
            try:
                then()
            except Exception:
                log.exception("after the stepped rehearsal")

    def build_report(self) -> dict:
        runner = self.runner
        share = (self.throttled_429 + self.forbidden_403) / self.attempts if self.attempts else 0.0
        cap = self.engine.deps.settings.motion.stepped.account_writes_per_min
        most = self.max_writes()
        over = {a: n for a, n in most.items() if a in self.accounts and n > cap}
        criteria = [
            {
                "id": "completed",
                "title": "The run went its full length",
                "ok": self.completed,
                "detail": f"ended: {self.ended}",
            },
            {
                "id": "failed_throttled",
                "title": "No command failed throttled after retries",
                "ok": self.failed_throttled == 0,
                "detail": f"{self.failed_throttled} failed throttled",
            },
            {
                "id": "throttled_share",
                "title": f"Throttled attempts under {MAX_THROTTLED_SHARE:.0%}",
                "ok": share < MAX_THROTTLED_SHARE,
                "detail": f"{self.throttled_429 + self.forbidden_403} of {self.attempts} attempts ({share:.1%})",
            },
            {
                "id": "no_403",
                "title": "No 403",
                "ok": not self.forbidden_any,
                "detail": (
                    f"403 on {', '.join(sorted(set(self.forbidden_any)))}" if self.forbidden_any else "none"
                ),
            },
            {
                "id": "max_writes_60s",
                "title": f"At most {cap:g} writes on the account in any 60 s",
                "ok": not over,
                "detail": ", ".join(
                    f"{a}: {most.get(a, 0)}{' (over)' if a in over else ''}" for a in self.accounts
                )
                or "no writes",
            },
        ]
        verdict = "pass" if all(c["ok"] for c in criteria) else "fail"
        failed = [c["title"] for c in criteria if not c["ok"]]
        summary = (
            f"{self.label}, {self.minutes} min on {len(self.modules)} modules: "
            + ("pass." if verdict == "pass" else f"fail ({'; '.join(failed)}).")
            + f" {self.attempts} attempts, {self.throttled_429} × 429, {self.forbidden_403} × 403; ended: {self.ended}."
        )
        steps = None
        if runner is not None:
            steps = {
                "step_s": runner.step_s,
                "design_step_s": runner.program.step_s,
                "planned": runner.count,
                "reached": min(runner.index, runner.count),
                "sent": runner.steps_sent,
                "skipped": runner.skipped,
                "budget_holds": runner.budget_holds,
            }
        return {
            "kind": "stepped_rehearsal",
            "run_id": self.run_id,
            "mode": self.mode,
            "label": self.label,
            "minutes": self.minutes,
            "run_s": self.run_s,
            "source": self.source,
            "simulated": self.simulated,
            "note": SIMULATED_NOTE if self.simulated else None,
            "started": _wall(self.started),
            "ended_at": _wall(self.ended_at),
            "ended": self.ended,
            "completed": self.completed,
            "modules": [self.modules[d] for d in self.modules],
            "accounts": self.accounts,
            "cap_writes_per_min": cap,
            "steps": steps,
            "attempts": self.attempts,
            "puts": self.puts,
            "throttled_429": self.throttled_429,
            "forbidden_403": self.forbidden_403,
            "throttled_share": round(share, 4),
            "max_writes_60s": most,
            "commands": dict(sorted(self.commands.items())),
            "failed_throttled": self.failed_throttled,
            "superseded": self.commands.get(str(CommandStatus.SUPERSEDED), 0),
            "skipped_steps": runner.skipped if runner is not None else 0,
            "verification_s": {
                "count": len(self.verified),
                "p50": _round(percentile(self.verified, 50)),
                "p95": _round(percentile(self.verified, 95)),
            },
            "governor_trips": self.governor_trips,
            "outages": len(self.engine.stats.get("outages") or []) - self._outages0,
            "alerts": self.alerts,
            "criteria": criteria,
            "verdict": verdict,
            "summary": summary,
        }

    def max_writes(self) -> dict[str, int]:
        """Per account, the most writes sent within any 60 s (a half-open window, by send time)."""
        out = {}
        for account, times in sorted(self._sent.items()):
            times = sorted(times)
            best, lo = 0, 0
            for hi, t in enumerate(times):
                while t - times[lo] >= WINDOW_S:
                    lo += 1
                best = max(best, hi - lo + 1)
            out[account] = best
        return out

    def _save(self, report: dict) -> Path | None:
        if self.report_dir is None:
            return None
        path = Path(self.report_dir) / f"{self.run_id}.json"
        try:
            atomic_write(path, json.dumps(report, indent=1, default=str).encode(), mode=0o600)
        except OSError:
            log.exception("couldn't save the stepped rehearsal report %s", path)
            self.engine.telemetry.record(
                "alert",
                level="warn",
                message=f"The stepped rehearsal report couldn't be saved to {path}: check the data volume. Its "
                "counts are in the telemetry ('rehearsal' event).",
                source="rehearsal",
            )
            return None
        return path

    # ---- what /ops shows ------------------------------------------------------------------------------------------
    def status(self) -> dict:
        runner = self.runner
        ends_at = None
        if runner is not None and runner.running:
            ends_at = runner.t0_wall + runner.count * runner.step_s
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "label": self.label,
            "minutes": self.minutes,
            "modules": len(self.modules),
            "started": self.started,
            "ends_at": ends_at,
            "simulated": self.simulated,
        }

    def summary(self) -> dict:
        r = self.report or {}
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "label": self.label,
            "minutes": self.minutes,
            "simulated": self.simulated,
            "verdict": r.get("verdict"),
            "summary": r.get("summary"),
            "ended": self.ended,
            "file": str(self.path) if self.path else None,
        }


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 3)
