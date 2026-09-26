"""Contract for bench jobs (Gates A–C, the hue ramp and friends) — shared by `/ops/bench` and the CLI.

A job runs against the **bench driver**: the AferoDriver when the live gate allows Purpose.BENCH (bench mode, or
sim mode with HUBSPACE_LIVE=1) and an account is connected; otherwise the SimDriver, and then every step and
result is labelled SIMULATED. Jobs never touch Afero except through the executor/driver, so the choke point and
the per-device rate budget apply. A job holds the device's exclusive lock for its whole run.

Results are saved to `<data>/bench/<result-id>.json` and are downloadable/copyable as anonymized JSON
(ids → placeholders, no MACs, SSIDs, emails, names, geo) so Alon can paste them back into a build session.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NEEDS_EYES = "needs_eyes"  # the API side passed; a human must confirm what the lights did
    INFO = "info"  # read-only or exploratory: nothing to pass
    ERROR = "error"  # the job itself couldn't run (no device, gate denied, lock busy)


@dataclass(frozen=True)
class JobSpec:
    name: str  # "gate_a", "gate_b", "gate_c", "hue_ramp", ...
    title: str  # "Gate A — read-only profile dump"
    summary: str  # one sentence for the bench page
    writes: bool  # False → read-only (Gate A)
    look_for: str = ""  # what the person at the bench should watch for
    params: dict[str, Any] = field(default_factory=dict)  # name → default


@dataclass
class JobResult:
    verdict: Verdict
    summary: str  # plain English, one or two sentences
    observations: dict[str, Any] = field(default_factory=dict)  # measured facts (lengths, statuses, timings)
    ask_human: list[str] = field(
        default_factory=list
    )  # questions to answer at the bench ("Did all 30 change?")
    flags: dict[str, bool] = field(default_factory=dict)  # proposed DeviceFlags (t2_select, t1_frames, ...)


class BenchContext(Protocol):
    simulated: bool
    device_id: str  # logical id
    address: str  # driver-level address

    def step(self, message: str, **detail: Any) -> None:
        """Record a progress step (pushed to the page live, saved with the result)."""

    async def read(self) -> Any:
        """REST readback (ReadResult) through the bench driver."""

    async def profile(self) -> Any:
        """Functions + state (DeviceInfo) through the bench driver."""

    async def write(self, rows: list, *, label: str, verify: bool = True, expect: list | None = None) -> Any:
        """One PUT through the bench executor with this job's lease (CommandResult)."""

    async def sleep(self, seconds: float) -> None: ...

    def now(self) -> float: ...


JobFn = Callable[[BenchContext, dict[str, Any]], Awaitable[JobResult]]


class BenchJob(Protocol):
    spec: JobSpec

    async def run(self, ctx: BenchContext, params: dict[str, Any]) -> JobResult: ...
