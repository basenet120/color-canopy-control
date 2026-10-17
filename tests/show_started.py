"""Waiting for the show engine of a running app (TestClient, real time) to be past startup.

"Has a plan and isn't commissioning" is not that moment: the first look goes out from the capability loop before
the simulator's startup commissioning begins, so that condition holds for an instant first (longer on a slow box),
and a rehearsal or a guest tap made then lands in, or is refused by, the commissioning that follows. Wait on
`ShowWiring.started` instead (commissioning run and the look re-picked after it)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


def wait_until(what: str, predicate: Callable[[], Any], timeout: float = 60.0, describe=lambda: "") -> Any:
    """Poll `predicate` until it is truthy (returned) or `timeout` real seconds pass (AssertionError naming
    `what`). The timeout only bounds a failure: nothing asserts on how long it took."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"{what} never happened within {timeout:g} s: {describe()}")
        time.sleep(0.02)


def past_startup(client, timeout: float = 60.0) -> Any:
    """The show engine once startup is over and a look is showing."""
    services = client.app.state.services
    wiring, engine = services.extras["show"], services.extras["canopy"]
    wait_until(
        "the show's startup (commissioning, then the look re-picked)",
        lambda: wiring.started and engine.current_plan() is not None and not engine.commissioning,
        timeout,
        describe=lambda: f"started={wiring.started} commissioning={engine.commissioning} {engine.state()}",
    )
    return engine
