"""A partial canopy on the real app (D62): only some canopy positions have a device registered on the engine's driver,
as on the Mac show box before install (the two VWBD test kits at r0c0 and r0c1). Shared by the tests that need it; a
plain helper module rather than conftest.py so it never conflicts with the shared fixtures.

    from partial_canopy import LIVE, partial_show, showing

    with partial_show(monkeypatch, tmp_path, LIVE) as c:
        engine = showing(c)
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

LIVE = ("canopy-r0c0", "canopy-r0c1")  # the two shop kits registered at canopy positions


def partial_show(monkeypatch, tmp_path, registered, *, t3_only: bool = True) -> TestClient:
    """The real engine with only `registered` canopy positions having a device on the engine's driver. With
    `t3_only` (the default) it runs as live does before Gate B/C and Gate R: T1 and T2 are refused (as
    tests/test_surfaces_review_fixes.py's t3_show), and so is the stepped tier (D65; motion.stepped.live_enabled
    false). `t3_only=False` keeps
    the show's own tier rules (startup commissioning unlocks T1; the simulator steps Coastal Tides)."""
    from conftest import _env as _show_env

    from conductor.app import app
    from conductor.config import Registry
    from conductor.engine_api import Tier
    from conductor.show import STEPPED_LIVE_OFF, ShowWiring

    def only_t3(self, mode_id, tier):
        if tier in (Tier.T1, Tier.T2):
            return False, "Gate C isn't proven on this kit (test)"
        if tier is Tier.STEPPED:
            return False, STEPPED_LIVE_OFF
        return True, ""

    real = Registry.address

    def address(self, device_id, driver_name):
        return real(self, device_id, driver_name) if device_id in registered else None

    if t3_only:
        monkeypatch.setattr(ShowWiring, "tier_allowed", only_t3)
    monkeypatch.setattr(Registry, "address", address)
    _show_env(monkeypatch, tmp_path, engine=True, CONDUCTOR_AUTH="off")
    return TestClient(app)


def showing(c, timeout: float = 30.0):
    """The engine once its first look is out and startup commissioning is over (a partial canopy never reads
    READY on /ops: the modules left out keep it at needs attention)."""
    engine = c.app.state.services.extras["canopy"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if engine.current_plan() is not None and not engine.commissioning:
            return engine
        time.sleep(0.05)
    raise AssertionError(f"the canopy never showed a look: {engine.state()}")
