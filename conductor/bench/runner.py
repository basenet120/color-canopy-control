"""BenchRunner: runs one bench job on one device, holding its lock, and records everything (bench_api.py).

- One run per device at a time. The target comes from `services.target(device_id, Purpose.BENCH)` — the AferoDriver
  when the gate allows bench jobs and the device has a metadevice id, else the SimDriver (then SIMULATED).
- The device lock (`services.locks.acquire(address, "bench · <job>")`) is taken before the run starts and held for
  the whole run; it is released in `finally`, whatever happens. A device that is locked (module page, another
  run) or outside the allowlist is refused with a plain message (`BenchBusy`).
- `RunContext` implements BenchContext over the target: read → `driver.read_state(purpose=BENCH)`, profile →
  `driver.fetch_profile(purpose=BENCH)`, write → `executor.submit(Command(..., Purpose.BENCH, lease_token=…))`.
  It never calls `driver.write` itself.
- Every step goes to the WebSocket topic "bench" and to telemetry (kind "bench"), and is saved with the result.
- Results are written atomically to `<data>/bench/<run_id>.json` after every step (results.py).
- The lease is touched on every read, profile and sleep (in chunks), so a long wait inside a run never lets the
  lock idle out mid-run; if the lock was taken over, the run stops with an ERROR at its next step.
- `stop(run_id)` cancels a run started in the background; the lock is released in `execute`'s `finally`.
- `writes_sent` counts writes that actually left the process, so an ERROR says whether the kit may have changed.
- Flags (DeviceFlags) are applied only from a real run whose kit is still the one registered under that id, and
  are saved with `flags_proven_on` = that kit's address. Changing a confirming answer away from "yes" afterwards
  puts the flags this run applied back to what they were before (if nothing else changed them since).
- A kit's controller map (`framebuffer_mapping`, D54) is recorded only by `apply_map`, from that kit's own two
  answered `address_probe_safe` runs (the proof rule, D58), and only ever as the identity map. It writes the ops
  overlay and reads the kit's profile once; it sends nothing to the lights. Taking back a "yes" on either run puts
  the previous map back (if nothing else changed it since).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from conductor import __version__
from conductor.anonymize import anonymize
from conductor.bench.jobs import (
    CONFIRM_LABELS,
    JOBS,
    PARAM_BOUNDS,
    Q_MARKERS,
    Job,
    JobError,
    address_question,
    coerce_params,
    flag_questions,
)
from conductor.bench.results import BenchResults, fixture_shape, is_run_id
from conductor.bench_api import JobResult, Verdict
from conductor.capabilities import Capabilities, detect
from conductor.drivers.base import DeviceInfo, Outcome, ReadResult, StateRow
from conductor.executor_api import Command, CommandResult
from conductor.framemap import module_heads
from conductor.gate import GateDenied, Purpose
from conductor.locks import Lease, LockError
from conductor.services import Services, Target

log = logging.getLogger(__name__)

SAFE_ERRORS = (JobError, LookupError, GateDenied, LockError, ValueError)
MAX_ANSWER_CHARS = 2000
LEASE_TOUCH_S = 60.0  # a sleep inside a run touches the lease at least this often (idle timeout is 10 min)
MAP_JOB = "address_probe_safe"  # the only job whose runs can record a kit's map (D58)
MAP_SEARCH_RUNS = 1000  # saved runs searched, newest first, for a kit's latest two map probes
PROFILE_TIMEOUT_S = 15.0


class BenchError(ValueError):
    """A request the runner can't act on (unknown job/device/run, bad params). Message is operator-safe."""


class BenchBusy(RuntimeError):
    """The device is locked or already running a job. Message is operator-safe."""


class BenchRefused(RuntimeError):
    """An action that isn't allowed yet (flags on a simulated run, before the human said yes). Operator-safe."""


def _stamp(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y%m%dT%H%M%S")


@dataclass
class RunContext:
    """BenchContext over one Target, for one run (plus budget_s, artifacts and sim_power)."""

    runner: BenchRunner
    run: Run
    target: Target
    lease: Lease
    artifacts: dict[str, Any] = field(default_factory=dict)

    @property
    def simulated(self) -> bool:
        return self.target.simulated

    @property
    def device_id(self) -> str:
        return self.target.device_id

    @property
    def address(self) -> str:
        return self.target.address

    @property
    def budget_s(self) -> float:
        return float(self.target.executor.policy.min_write_interval_s)

    @property
    def t1_allowed(self) -> bool:
        """Per-head frames may be written: always on the simulator; on a real kit only once Gate C is proven on
        this very kit (flags bound to its address)."""
        if self.target.simulated:
            return True
        try:
            entry = self.runner.services.registry.get(self.device_id)
        except KeyError:
            return False
        return entry.flags_for(self.address).t1_frames is True

    def _touch(self) -> None:
        self.runner.services.locks.touch(self.lease)  # LockError if released, timed out or taken over

    def step(self, message: str, **detail: Any) -> None:
        self.runner._step(self.run, message, detail)

    def now(self) -> float:
        return self.runner.services.clock.now()

    async def sleep(self, seconds: float) -> None:
        remaining = max(0.0, float(seconds))
        while True:
            chunk = min(remaining, LEASE_TOUCH_S)
            await self.runner.services.clock.sleep(chunk)
            remaining -= chunk
            self._touch()
            if remaining <= 0:
                return

    async def read(self) -> ReadResult:
        self._touch()
        rr = await self.target.driver.read_state(self.address, purpose=Purpose.BENCH)
        self.step(
            "Read the device's state" if rr.ok else f"Read failed: {rr.error or rr.outcome}",
            kind="read",
            outcome=str(rr.outcome),
            http_status=rr.http_status,
            elapsed_ms=round(rr.elapsed_s * 1000),
        )
        return rr

    async def profile(self) -> DeviceInfo:
        self._touch()
        return await self.target.driver.fetch_profile(self.address, purpose=Purpose.BENCH)

    @property
    def expected_heads(self) -> int | None:
        try:
            return self.runner.services.registry.get(self.device_id).expected_heads
        except KeyError:
            return None

    async def write(
        self,
        rows: list[StateRow],
        *,
        label: str,
        verify: bool = True,
        expect: list | None = None,
        stamp_offset_s: float = 0.0,
        max_attempts: int | None = None,
    ) -> CommandResult:
        detail: dict[str, Any] = {"stamp_offset_s": stamp_offset_s} if stamp_offset_s else {}
        self.step(
            f"Sending: {label}", kind="write_start", label=label, t=self.now(), rows=len(rows), **detail
        )
        result = await self.target.executor.submit(
            Command(
                self.address,
                list(rows),
                Purpose.BENCH,
                label=f"bench · {label}",
                lease_token=self.lease.token,
                verify=verify,
                expect=expect,
                source="bench",
                stamp_offset_s=stamp_offset_s,
                max_attempts=max_attempts,
            )
        )
        if any(a.outcome is not Outcome.DENIED for a in result.attempts):  # it left the process
            self.run.record["writes_sent"] = self.run.record.get("writes_sent", 0) + 1
        t = result.timings
        last = result.attempts[-1] if result.attempts else None
        self.step(
            f"{label}: {result.status}" + (f" — {result.error}" if result.error else ""),
            kind="write",
            label=label,
            status=str(result.status),
            http_status=last.http_status if last else None,
            attempts=len(result.attempts),
            sent_at=t.sent_at,
            acked_at=t.acked_at,
            verified_at=t.verified_at,
        )
        return result

    async def sim_power(self, on: bool) -> bool:
        if not self.target.simulated:
            return False
        self.target.driver.faults.set_available(self.address, on)
        return True


@dataclass
class Run:
    run_id: str
    job: Job
    target: Target
    params: dict[str, Any]
    lease: Lease
    record: dict[str, Any]
    started_mono: float
    task: asyncio.Task | None = None
    stop_requested: bool = False


class BenchRunner:
    """Runs bench jobs. Lives in `services.extras["bench"]` (see `runner_of`)."""

    def __init__(self, services: Services, jobs: dict[str, Job] | None = None):
        self.services = services
        self.jobs = jobs or JOBS
        self.results = BenchResults(services.data / "bench")
        self._active: dict[str, Run] = {}  # device_id → the run holding it
        self._runs: dict[str, Run] = {}  # run_id → live runs (in memory until finished)

    # ---- catalogue -------------------------------------------------------------------------------------------
    def specs(self) -> list[dict[str, Any]]:
        out = []
        for job in self.jobs.values():
            s = job.spec
            out.append(
                {
                    "name": s.name,
                    "title": s.title,
                    "summary": s.summary,
                    "writes": s.writes,
                    "look_for": s.look_for,
                    "params": dict(s.params),
                    "bounds": {k: list(v) for k, v in PARAM_BOUNDS.get(s.name, {}).items()},
                    "confirm_label": CONFIRM_LABELS.get(s.name),
                }
            )
        return out

    def devices(self) -> list[dict[str, Any]]:
        """Every device a bench job could target, with the driver it would use and who holds its lock."""
        out = []
        entries = [d for d in self.services.registry.devices if d.role != "vignette"]
        entries.sort(key=lambda d: (d.role != "bench", d.role != "spare", d.id))
        for entry in entries:
            row: dict[str, Any] = {
                "device_id": entry.id,
                "label": entry.label or entry.id,
                "role": entry.role,
            }
            try:
                target = self.services.target(entry.id, Purpose.BENCH)
            except LookupError as e:
                row.update({"available": False, "problem": str(e), "simulated": None, "driver": None})
                out.append(row)
                continue
            holder = self.services.locks.holder(target.address)
            lockable, why = self.services.lockable(target.address)
            row.update(
                {
                    "available": lockable,
                    "problem": why or None,
                    "simulated": target.simulated,
                    "driver": target.driver.name,
                    "locked_by": holder.owner if holder else None,
                    "running": self._active[entry.id].run_id if entry.id in self._active else None,
                }
            )
            out.append(row)
        return out

    # ---- running -----------------------------------------------------------------------------------------------
    def prepare(self, job_name: str, device_id: str, params: dict[str, Any] | None = None) -> Run:
        """Validate, take the device lock and create the run record. Raises BenchError / BenchBusy."""
        job = self.jobs.get(job_name)
        if job is None:
            raise BenchError(f"There is no bench job called {job_name!r}.")
        try:
            self.services.registry.get(device_id)
        except KeyError:
            raise BenchError(f"There is no device {device_id!r} in the registry.") from None
        try:
            coerced = coerce_params(job.spec, params)
        except ValueError as e:
            raise BenchError(str(e)) from None
        try:
            target = self.services.target(device_id, Purpose.BENCH)
        except LookupError as e:
            raise BenchError(str(e)) from None
        if device_id in self._active:
            other = self._active[device_id]
            raise BenchBusy(f"{device_id} is already running {other.job.spec.name}. Wait for it to finish.")
        try:
            lease = self.services.locks.acquire(target.address, f"bench · {job_name}")
        except LockError as e:
            raise BenchBusy(str(e)) from None
        clock = self.services.clock
        started = clock.now()
        run_id = f"{_stamp(started)}-{job_name}-{secrets.token_hex(3)}"
        record: dict[str, Any] = {
            "run_id": run_id,
            "job": job_name,
            "title": job.spec.title,
            "device_id": device_id,
            "address": target.address,
            "simulated": target.simulated,
            "driver": target.driver.name,
            "status": "running",
            "started": started,
            "finished": None,
            "params": coerced,
            "steps": [],
            "result": None,
            "answers": {},
            "flag_questions": flag_questions(job_name, coerced),
            "flags_applied": None,
            "flags_revoked": None,
            "map_applied": None,
            "map_revoked": None,
            "writes_sent": 0,
            "confirmed_verdict": None,
            "environment": {
                "mode": str(self.services.gate_settings.mode),
                "version": __version__,
                "bench_live": self.services.version().get("bench_live"),
            },
            "artifacts": [],
        }
        run = Run(run_id, job, target, coerced, lease, record, clock.monotonic())
        self._active[device_id] = run
        self._runs[run_id] = run
        self._publish(run, "started")
        self.results.save(record)
        return run

    async def execute(self, run: Run) -> dict[str, Any]:
        """Run the job to completion (the lock is released whatever happens) and return the saved record."""
        ctx = RunContext(self, run, run.target, run.lease)
        result: JobResult | None = None
        try:
            label = "SIMULATED — nothing here touches the real kit. " if run.target.simulated else ""
            self._step(run, f"{label}Started {run.job.spec.title} on {run.target.device_id}", {})
            result = await run.job.run(ctx, dict(run.params))
        except asyncio.CancelledError:
            why = (
                "Stopped from the bench page before it finished."
                if run.stop_requested
                else "The run was stopped before it finished (the server stopped or restarted)."
            )
            result = JobResult(Verdict.ERROR, why)
            raise
        except SAFE_ERRORS as e:
            result = JobResult(Verdict.ERROR, str(e))
        except Exception as e:
            log.exception("bench job %s failed", run.job.spec.name)
            result = JobResult(
                Verdict.ERROR, f"The job hit an internal error ({type(e).__name__}); see the logs."
            )
        finally:
            self.services.locks.release(run.target.address, run.lease.token)
            self._active.pop(run.target.device_id, None)
            self._finish(run, result or JobResult(Verdict.ERROR, "The run ended unexpectedly."), ctx)
        return run.record

    def start(self, job_name: str, device_id: str, params: dict[str, Any] | None = None) -> str:
        """Prepare and run in the background (must be called on the running loop). Returns the run id."""
        run = self.prepare(job_name, device_id, params)
        run.task = asyncio.get_running_loop().create_task(self.execute(run), name=f"bench:{run.run_id}")
        return run.run_id

    async def run(
        self, job_name: str, device_id: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Prepare and run to completion (the CLI's --local path and tests)."""
        return await self.execute(self.prepare(job_name, device_id, params))

    async def wait(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if run is not None and run.task is not None:
            await asyncio.shield(run.task)
        return self.get(run_id)

    async def stop(self, run_id: str) -> dict[str, Any]:
        """Stop a run that is going in the background; returns its saved record (verdict ERROR, lock released)."""
        record = self.get(run_id)  # BenchError for an unknown run
        run = self._runs.get(run_id)
        if run is None or run.task is None or run.task.done():
            raise BenchRefused(
                f"That run isn't running (it is {record.get('status')}), so there is nothing to stop."
            )
        run.stop_requested = True
        run.task.cancel()
        await asyncio.wait({run.task})
        return self.get(run_id)

    async def close(self) -> None:
        """Stop runs still in progress (their locks are released and the result says so)."""
        tasks = [r.task for r in self._runs.values() if r.task is not None and not r.task.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

    # ---- records -------------------------------------------------------------------------------------------------
    def get(self, run_id: str) -> dict[str, Any]:
        if not is_run_id(run_id):
            raise BenchError("That isn't a bench run id.")
        run = self._runs.get(run_id)
        if run is not None:
            return run.record
        record = self.results.load(run_id)
        if record is None:
            raise BenchError(f"There is no bench run {run_id}.")
        if record.get("status") == "running":  # saved mid-run by a process that has since stopped
            record["status"] = "interrupted"
        return record

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        out = self.results.list(limit)
        for summary in out:
            if summary.get("status") == "running" and summary["run_id"] not in self._runs:
                summary["status"] = "interrupted"
        return out

    def answer(self, run_id: str, answers: dict[str, str]) -> dict[str, Any]:
        record = self.get(run_id)
        result = record.get("result")
        if record.get("status") != "finished" or not result:
            raise BenchRefused("The run hasn't finished yet; answer when it has.")
        questions = result.get("ask_human") or []
        for question, answer in answers.items():
            if question not in questions:
                raise BenchError("That question isn't one this run asked.")
            if not isinstance(answer, str) or not answer.strip() or len(answer) > MAX_ANSWER_CHARS:
                raise BenchError("Answer with yes, no, or a short note.")
        given = {}
        for question, answer in answers.items():
            text = answer.strip()
            given[question] = text.lower() if text.lower() in ("yes", "no") else text
        # Flags already applied on the strength of a "yes" that is now taken back are undone first (a failure
        # to save the registry refuses the answer, so the record and the registry never disagree).
        applied = record.get("flags_applied") or {}
        needed = record.get("flag_questions") or {}
        revoke = {
            flag: (applied.get("previous") or {}).get(flag)
            for flag in applied.get("flags") or {}
            if needed.get(flag) in given and given[needed[flag]] != "yes"
        }
        if revoke:
            self._revoke_flags(record, revoke)
        # So is a map this run's yes recorded (address_probe_safe asks one question).
        if record.get("map_applied") and any(a != "yes" for a in given.values()):
            self._revoke_map(record)
        record["answers"].update(given)
        record["confirmed_verdict"] = self._confirmed(record)
        self.results.save(record)
        self._publish_record(record, "answered")
        return record

    def apply_flags(self, run_id: str) -> dict[str, Any]:
        record = self.get(run_id)
        result = record.get("result") or {}
        proposed: dict[str, bool] = result.get("flags") or {}
        if record.get("simulated"):
            raise BenchRefused(
                "This run was on the simulator, so it proves nothing about the real kit. Flags are never applied "
                "from a simulated run."
            )
        if record.get("status") != "finished" or not proposed:
            raise BenchRefused("This run didn't propose any flags.")
        needed = record.get("flag_questions") or {}
        for flag in proposed:
            question = needed.get(flag)
            if question is None:
                raise BenchRefused(f"{flag} has no confirming question, so it can't be applied from here.")
            if record["answers"].get(question) != "yes":
                raise BenchRefused(f"Answer “{question}” with Yes first.")
        device_id = record["device_id"]
        address = record.get("address")
        try:
            entry = self.services.registry.get(device_id)
        except KeyError:
            raise BenchRefused(f"{device_id} isn't in the registry any more.") from None
        # Bound to the kit that proved them: the device must still resolve to the address this run used.
        if not address or self.services.registry.address(device_id, record.get("driver") or "") != address:
            raise BenchRefused(
                f"This run was on a different kit from the one registered as {device_id} now (it was re-registered "
                "since). Flags belong to the kit that proved them: run the gate again on this kit."
            )
        current = entry.flags_for(address).model_dump()  # flags proven on another kit don't carry over
        previous = {flag: current.get(flag) for flag in proposed}
        self.services.update_registry(
            device_id, {"flags": {**current, **proposed}, "flags_proven_on": address}
        )
        record["flags_applied"] = {"flags": proposed, "previous": previous, "at": self.services.clock.now()}
        record["flags_revoked"] = None
        self.results.save(record)
        self.services.telemetry.record(
            "bench", device_id=device_id, job=record["job"], step="flags applied", detail=proposed
        )
        self._publish_record(record, "flags")
        return record

    def _revoke_flags(self, record: dict[str, Any], revoke: dict[str, bool | None]) -> None:
        """Put flags this run applied back to their previous values (only where the registry still holds what
        this run applied, on this kit) and record what was undone."""
        device_id, address = record["device_id"], record.get("address")
        applied = record["flags_applied"]
        try:
            entry = self.services.registry.get(device_id)
        except KeyError:
            entry = None
        current = entry.flags_for(address).model_dump() if entry else {}
        undone: dict[str, bool | None] = {}
        left: dict[str, bool | None] = {}
        for flag, previous in revoke.items():
            if entry is not None and current.get(flag) == applied["flags"][flag]:
                current[flag] = previous
                undone[flag] = previous
            else:  # changed since (another run, another kit): not ours to undo
                left[flag] = current.get(flag)
        if undone:
            self.services.update_registry(device_id, {"flags": current, "flags_proven_on": address})
        remaining = {f: v for f, v in applied["flags"].items() if f not in revoke}
        record["flags_applied"] = {**applied, "flags": remaining} if remaining else None
        record["flags_revoked"] = {"flags": undone, "left_as_is": left, "at": self.services.clock.now()}
        self.services.telemetry.record(
            "bench", device_id=device_id, job=record["job"], step="flags revoked", detail=undone
        )
        self._publish_record(record, "flags")

    # ---- the controller map (D58) ----------------------------------------------------------------------------
    def map_proposal(self, run_id: str) -> dict[str, Any]:
        """What Apply map would record from this run and why, or why not (`ready`). Reads saved runs and the
        registry only; the kit's frame length is read when the map is applied."""
        record = self.get(run_id)
        try:
            mapping, runs = self._map_proof(record)
            self._not_recorded_yet(record["device_id"], mapping)
        except BenchRefused as e:
            return {"ready": False, "reason": str(e), "mapping": None, "runs": None}
        heads, length = len(mapping["indices"]), mapping["frame_length"]
        return {
            "ready": True,
            "reason": (
                f"Apply map records this kit's map: entries 0–{heads - 1} drive heads 1–{heads} in order and "
                f"entries {heads}–{length - 1} drive nothing ({length}-entry controller), for this kit only. "
                f"Proof: Gate C on this kit, and its markers probe ({runs['markers']}) and its "
                f"{_probe_name('dark', heads)} ({runs['dark']}), both answered Yes. It reads the kit's frame length "
                "once more first, and sends nothing to the lights."
            ),
            "mapping": mapping,
            "runs": runs,
        }

    async def apply_map(self, run_id: str) -> dict[str, Any]:
        """Record the kit's identity map from its two answered probes (D58) in the ops overlay. One profile read
        checks the kit still reports the probes' frame length; nothing is sent to the lights."""
        record = self.get(run_id)
        self._map_proof(record)  # refused before the kit is read
        caps = await self._current_caps(record["device_id"], record["address"], record.get("driver") or "")
        record = self.get(run_id)  # from here on nothing awaits, so the proof below is the one saved
        mapping, runs = self._map_proof(record)
        device_id, length = record["device_id"], mapping["frame_length"]
        if len(set(caps.framebuffer_lengths.values())) != 1 or caps.framebuffer_length != length:
            raise BenchRefused(
                f"The kit now reports {caps.framebuffer_length or 'no'} entries (slots: "
                f"{sorted(set(caps.framebuffer_lengths.values())) or 'none'}), not the {length} its probes saw. "
                "Nothing was saved: run both probes again."
            )
        previous = self._not_recorded_yet(device_id, mapping)
        self.services.update_registry(device_id, {"framebuffer_mapping": mapping})
        applied = {
            "mapping": mapping,
            "previous": previous,
            "runs": [runs["markers"], runs["dark"]],
            "at": self.services.clock.now(),
        }
        for other in [record] + [self.get(r) for r in applied["runs"] if r != record["run_id"]]:
            other["map_applied"] = applied
            other["map_revoked"] = None
            self.results.save(other)
            self._publish_record(other, "map")
        self.services.telemetry.record(
            "bench",
            device_id=device_id,
            job=MAP_JOB,
            step="map applied",
            detail={"frame_length": length, "heads": len(mapping["indices"]), "runs": applied["runs"]},
        )
        self._reread_show(device_id)
        return record

    def _not_recorded_yet(self, device_id: str, mapping: dict[str, Any]) -> dict[str, Any] | None:
        """The device's current map (to restore on a revoke); refused when it already is exactly `mapping`."""
        current = self.services.registry.get(device_id).framebuffer_mapping
        previous = current.model_dump() if current is not None else None
        if previous == mapping:
            raise BenchRefused(
                "This kit's map is already recorded exactly as these probes show. Nothing to change."
            )
        return previous

    def _map_proof(self, record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """The identity map this run and the kit's other probe prove, or BenchRefused saying why not (D58).

        All on the kit at this run's address: Gate C (T1) applied there; its latest markers probe answered Yes
        (head 1 red, head 2 green, head 30 blue, nothing else); its latest probe of entries heads…L−1 answered Yes
        (nothing lit); both saw the same frame length L (the job checked every slot agreed); and the module's head
        count = the registry's `expected_heads`. Anything else is refused: no permutation is ever inferred."""
        if record.get("job") != MAP_JOB:
            raise BenchRefused("Only the bounded address probe (address_probe_safe) can record a map.")
        if record.get("simulated"):
            raise BenchRefused(
                "This run was on the simulator, so it proves nothing about the real kit. A map is never recorded "
                "from a simulated run."
            )
        if record.get("status") != "finished" or not record.get("result"):
            raise BenchRefused("The run hasn't finished yet.")
        device_id, address, driver = record["device_id"], record.get("address"), record.get("driver") or ""
        try:
            entry = self.services.registry.get(device_id)
        except KeyError:
            raise BenchRefused(f"{device_id} isn't in the registry any more.") from None
        if not address or self.services.registry.address(device_id, driver) != address:
            raise BenchRefused(
                f"This run was on a different kit from the one registered as {device_id} now (it was re-registered "
                "since). A map belongs to the kit that proved it: probe this kit again."
            )
        if entry.flags_for(address).t1_frames is not True:
            raise BenchRefused(
                "Gate C isn't proven on this kit: run gate_c_safe, answer Yes and Apply flags before recording a map."
            )
        heads = module_heads(self.services.config_dir)
        if entry.expected_heads != heads:
            raise BenchRefused(
                f"{device_id} is set to {entry.expected_heads or 'no'} expected heads but a module has {heads}, so "
                "these probes can't be read as its map. Tell Alon."
            )
        kind = _probe_kind(record, heads)
        if kind is None:
            raise BenchRefused(
                f"A map is recorded from two probes: the markers pattern, and a range from entry {heads} to the "
                "controller's last entry. This run is neither."
            )
        latest = self._latest_probes(device_id, address, driver, heads)
        newest = latest[kind[0]]
        if newest is not None and newest["run_id"] != record["run_id"]:
            raise BenchRefused(
                f"A newer {_probe_name(kind[0], heads)} ran on this kit ({newest['run_id']}), and the newest one is "
                "the one that counts. Open it under Recent runs."
            )
        for name, run in latest.items():
            probe = _probe_name(name, heads)
            if run is None:
                raise BenchRefused(f"Run the {probe} on this kit too, and answer it.")
            if run.get("simulated"):
                raise BenchRefused(f"The {probe} ({run['run_id']}) was simulated, so it proves nothing.")
            question = _probe_kind(run, heads)[1]
            answer = run["answers"].get(question)
            if answer is None:
                raise BenchRefused(f"Answer “{question}” on the {probe} ({run['run_id']}) first.")
            if answer != "yes":
                raise BenchRefused(
                    f"The {probe} ({run['run_id']}) was answered “{answer}”, so this kit doesn't show entries "
                    f"0–{heads - 1} on heads 1–{heads} in order with the rest dark. No map can be recorded from "
                    "here: tell Alon."
                )
        markers, dark = latest["markers"], latest["dark"]
        length = markers["result"]["observations"].get("length")
        if dark["result"]["observations"].get("length") != length:
            raise BenchRefused(
                f"The two probes saw different frame lengths ({length} and "
                f"{dark['result']['observations'].get('length')}): run both again."
            )
        lit = {int(k) for k in markers["result"]["observations"].get("markers") or {}}
        if lit != {0, 1, heads - 1}:
            raise BenchRefused(
                f"The markers probe lit entries {sorted(lit)}, not 0, 1 and {heads - 1}. Tell Alon."
            )
        # The dark probe covers entries heads…length−1 (its kind), so length > heads.
        mapping = {"frame_length": length, "indices": list(range(heads)), "proven_on": address}
        return mapping, {"markers": markers["run_id"], "dark": dark["run_id"]}

    def _latest_probes(self, device_id: str, address: str, driver: str, heads: int) -> dict[str, Any]:
        """The newest finished markers probe and dark-range probe on the kit at `address` (None if there is none)."""
        latest: dict[str, Any] = {"markers": None, "dark": None}
        for summary in self.results.list(MAP_SEARCH_RUNS):
            if summary.get("job") != MAP_JOB or summary.get("device_id") != device_id:
                continue
            run = self.results.load(summary["run_id"])
            if run is None or (run.get("address"), run.get("driver")) != (address, driver):
                continue
            kind = _probe_kind(run, heads)
            if kind is not None and latest[kind[0]] is None:
                latest[kind[0]] = run
        return latest

    async def _current_caps(self, device_id: str, address: str, driver: str) -> Capabilities:
        """The kit's capabilities now, from one profile read through the bench driver (a GET, never a write)."""
        try:
            target = self.services.target(device_id, Purpose.BENCH)
        except LookupError as e:
            raise BenchRefused(str(e)) from None
        if (target.driver.name, target.address) != (driver, address):
            raise BenchRefused(
                "Bench jobs here don't reach the kit these probes ran on, so its frame length can't be checked. "
                "Apply the map on the Conductor that ran the probes."
            )
        try:
            info = await asyncio.wait_for(
                target.driver.fetch_profile(address, purpose=Purpose.BENCH), PROFILE_TIMEOUT_S
            )
        except GateDenied as e:
            raise BenchRefused(e.reason) from None
        except TimeoutError:
            raise BenchRefused(
                "Reading the kit's profile took too long. Nothing was saved; try again."
            ) from None
        except Exception as e:  # noqa: BLE001 - AferoRequestFailed and friends carry operator-safe messages
            raise BenchRefused(f"Couldn't read the kit's profile, so nothing was saved: {e}") from None
        return detect(info.functions, info.states)

    def _revoke_map(self, record: dict[str, Any]) -> None:
        """Put back the map this run's yes recorded: only if the registry still holds exactly that map (proven on
        this kit, nothing changed since). Both runs that justified it record what was undone."""
        applied = record["map_applied"]
        device_id = record["device_id"]
        try:
            current = self.services.registry.get(device_id).framebuffer_mapping
        except KeyError:
            current = None
        undone = current is not None and current.model_dump() == applied["mapping"]
        if undone:
            self.services.update_registry(device_id, {"framebuffer_mapping": applied["previous"]})
        revoked = {"mapping": applied["mapping"], "undone": undone, "at": self.services.clock.now()}
        for run_id in applied["runs"]:
            run = record if run_id == record["run_id"] else self.results.load(run_id)
            if run is None:
                continue
            run["map_applied"] = None
            run["map_revoked"] = revoked
            if run is not record:  # answer() saves and publishes this run
                self.results.save(run)
                self._publish_record(run, "map")
        self.services.telemetry.record(
            "bench", device_id=device_id, job=MAP_JOB, step="map revoked", detail={"undone": undone}
        )
        if undone:
            self._reread_show(device_id)

    def _reread_show(self, device_id: str) -> None:
        """The show caches each module's profile and judges its map from it: drop the cache and read it again now,
        as a controller swap does, so /ops, the 7 am check and T1 pick the change up at once."""
        if (wiring := self.services.extras.get("show")) is not None:
            wiring.caps.pop(device_id, None)
            wiring.request_refresh([device_id])

    def anonymized(self, run_id: str) -> dict[str, Any]:
        record = self.get(run_id)
        return self.results.anonymized(record, keep=self._logical_ids(), extra=[record.get("address") or ""])

    def profile(self, run_id: str) -> dict[str, Any] | None:
        """Gate A's profile, scrubbed, in the simulator fixture shape (candidate `sqhdk03.json`)."""
        record = self.get(run_id)
        raw = self.results.load_artifact(record["run_id"], "profile_raw")
        if raw is None:
            return None
        return anonymize(fixture_shape(raw))

    # ---- internals ---------------------------------------------------------------------------------------------
    def _logical_ids(self) -> list[str]:
        return [d.id for d in self.services.registry.file.devices]

    def _step(self, run: Run, message: str, detail: dict[str, Any]) -> None:
        clock = self.services.clock
        step = {
            "n": len(run.record["steps"]) + 1,
            "t": round(clock.now(), 3),
            "elapsed_s": round(clock.monotonic() - run.started_mono, 3),
            "message": message,
            "detail": _jsonable(detail),
        }
        run.record["steps"].append(step)
        self.services.telemetry.record(
            "bench",
            device_id=run.target.address,
            run_id=run.run_id,
            job=run.job.spec.name,
            step=message,
            detail=step["detail"],
        )
        self._publish(run, "step", step=step)
        self.results.save(run.record)

    def _finish(self, run: Run, result: JobResult, ctx: RunContext) -> None:
        record = run.record
        summary = result.summary
        if result.verdict is Verdict.ERROR:  # say truthfully whether the kit may have changed
            sent = record.get("writes_sent", 0)
            if sent:
                summary += (
                    f" {sent} write{'s' if sent != 1 else ''} had already gone out before it stopped, so the kit "
                    "may have changed: check the steps before running it again."
                )
            elif "Nothing was sent" not in summary:
                summary += " Nothing was sent."
        record["result"] = {
            "verdict": str(result.verdict),
            "summary": summary,
            "observations": _jsonable(result.observations),
            "ask_human": list(result.ask_human),
            "flags": dict(result.flags),
        }
        record["status"] = "finished"
        record["finished"] = self.services.clock.now()
        record["confirmed_verdict"] = self._confirmed(record)
        for name, value in ctx.artifacts.items():  # stored scrubbed: raw dumps carry MACs, SSIDs, names
            self.results.save_artifact(run.run_id, name, anonymize(value, extra_ids=[run.target.address]))
            record["artifacts"].append(name)
        self.results.save(record)
        self.services.telemetry.record(
            "bench",
            device_id=run.target.address,
            run_id=run.run_id,
            job=run.job.spec.name,
            step="finished",
            detail={"verdict": str(result.verdict)},
        )
        self._publish(run, "finished", result=record["result"])
        self._runs.pop(run.run_id, None)

    @staticmethod
    def _confirmed(record: dict[str, Any]) -> str | None:
        """The verdict after the human answers: every question yes → pass, any no → fail, else pending (None)."""
        result = record.get("result") or {}
        verdict = result.get("verdict")
        questions = result.get("ask_human") or []
        if verdict != str(Verdict.NEEDS_EYES) or not questions:
            return verdict
        answers = [record["answers"].get(q) for q in questions]
        if any(a == "no" for a in answers):
            return str(Verdict.FAIL)
        if all(a == "yes" for a in answers):
            return str(Verdict.PASS)
        return None

    def _publish(self, run: Run, event: str, **extra: Any) -> None:
        payload = {
            "event": event,
            "run_id": run.run_id,
            "job": run.job.spec.name,
            "device_id": run.target.device_id,
            "simulated": run.target.simulated,
            **extra,
        }
        self.services.hub.publish("bench", payload)

    def _publish_record(self, record: dict[str, Any], event: str) -> None:
        self.services.hub.publish(
            "bench",
            {
                "event": event,
                "run_id": record["run_id"],
                "job": record["job"],
                "device_id": record["device_id"],
                "simulated": record["simulated"],
                "answers": record["answers"],
                "confirmed_verdict": record["confirmed_verdict"],
                "flags_applied": record["flags_applied"],
                "flags_revoked": record.get("flags_revoked"),
                "map_applied": record.get("map_applied"),
                "map_revoked": record.get("map_revoked"),
            },
        )


def _probe_name(kind: str, heads: int) -> str:
    return "markers probe" if kind == "markers" else f"probe of entries {heads} onwards"


def _probe_kind(record: dict[str, Any], heads: int) -> tuple[str, str] | None:
    """("markers" | "dark", the question it asked) for a finished address_probe_safe run that can count towards a
    map: the markers pattern, or a range from entry `heads` to the frame's last entry; else None."""
    result = record.get("result") or {}
    obs = result.get("observations") or {}
    params = record.get("params") or {}
    if params.get("pattern") == "markers":
        kind = "markers", Q_MARKERS
    elif obs.get("start") == heads and obs.get("start", 0) + obs.get("count", 0) == obs.get("length"):
        kind = "dark", address_question("range", heads, obs["count"], heads)
    else:
        return None
    if result.get("verdict") != str(Verdict.NEEDS_EYES) or kind[1] not in (result.get("ask_human") or []):
        return None  # the probe wasn't shown and restored exactly, or it asked another question
    return kind


def _jsonable(value: Any) -> Any:
    """A JSON-safe deep copy (tuples → lists, unknown objects → str)."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return copy.deepcopy(str(value))


def runner_of(services: Services) -> BenchRunner:
    """The services' bench runner, created on first use."""
    runner = services.extras.get("bench")
    if runner is None:
        runner = services.extras["bench"] = BenchRunner(services)
    return runner
