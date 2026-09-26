"""The injectable clock. All Conductor timing goes through a `Clock` (07 §7).

Two implementations:
- `RealClock` — wall time and the running event loop's monotonic time.
- `VirtualClock` — for tests and `make soak`: run the code under `VirtualTimeLoop`, an asyncio event loop whose
  time jumps straight to the next scheduled timer whenever nothing is ready to run. Every `asyncio.sleep`,
  `call_later` and `wait_for` timeout then costs no real time, so 8 simulated hours take seconds, and code under
  test needs no special sleep function — it only has to read time from the clock, never from `time.time()`.

Rule for every module: take a `Clock` (default `RealClock()`), use `clock.now()` for wall time (epoch seconds,
e.g. `lastUpdateTime`), `clock.monotonic()` for intervals, and `await clock.sleep(s)` (or plain asyncio timers)
for waiting.
"""

from __future__ import annotations

import asyncio
import selectors
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Wall-clock time, epoch seconds."""

    def monotonic(self) -> float:
        """Monotonic seconds for measuring intervals."""

    async def sleep(self, seconds: float) -> None:
        """Wait `seconds` of this clock's time."""


class RealClock:
    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))


class _InstantSelector(selectors.DefaultSelector):
    """A selector that never blocks: waiting for timers is replaced by advancing virtual time."""

    def __init__(self, loop_ref: list[VirtualTimeLoop]):
        super().__init__()
        self._loop_ref = loop_ref

    def select(self, timeout=None):
        loop = self._loop_ref[0]
        if timeout is None or timeout > 0:
            # Nothing is ready: jump to the next timer instead of sleeping. The loop computes `timeout` as the
            # distance to its earliest scheduled handle, so advancing by it makes that handle due.
            if timeout is not None:
                loop._virtual_now += timeout
            elif not loop._scheduled and not loop._ready:
                # No timers and nothing ready: real I/O could still arrive (e.g. the self-pipe), so poll it.
                return super().select(0.001)
        return super().select(0)


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    """An event loop on virtual time. Only for code that does no real network I/O (the SimDriver world)."""

    def __init__(self, start: float = 0.0):
        ref: list[VirtualTimeLoop] = []
        super().__init__(selector=_InstantSelector(ref))
        ref.append(self)
        self._virtual_now = start

    def time(self) -> float:
        return self._virtual_now


class VirtualClock:
    """Clock for code running on a `VirtualTimeLoop`. Wall time = `epoch` + the loop's virtual seconds."""

    def __init__(self, epoch: float | None = None):
        # Default epoch: a fixed show-week date, so tests are deterministic.
        self.epoch = epoch if epoch is not None else datetime(2026, 10, 23, 10, 0, tzinfo=UTC).timestamp()

    def _loop_time(self) -> float:
        try:
            return asyncio.get_running_loop().time()
        except RuntimeError:
            return 0.0

    def now(self) -> float:
        return self.epoch + self._loop_time()

    def monotonic(self) -> float:
        return self._loop_time()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))


def run_virtual[T](main: Callable[[], Awaitable[T]], start: float = 0.0) -> T:
    """Run `main()` to completion on a fresh `VirtualTimeLoop`."""
    loop = VirtualTimeLoop(start)
    try:
        return loop.run_until_complete(main())
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
