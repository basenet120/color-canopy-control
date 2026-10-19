"""The live path in the simulator (D77): every scene has a stepped rung now, and the simulator runs it. A test whose
subject is a still tier (a ripple, a painted slot, a flagged note, guest pacing, a re-pick) refuses that rung the way
live hardware does until the stepped rehearsal passes (show.py's tier_allowed answers STEPPED_LIVE_OFF until Gate R
and motion.stepped.live_enabled), so the ladder lands on the next rung, exactly as it would on the show box.

Only the stepped rung is refused, and only for the modes named (every mode when none is): the live-gate assertions
of show.py stay as they are (tests/test_d65_modes.py proves them)."""

from __future__ import annotations

from typing import Any

from conductor.engine_api import Tier
from conductor.show import STEPPED_LIVE_OFF


def refuse_stepped(engine: Any, *mode_ids: str) -> Any:
    """Wrap the engine's tier_allowed so Tier.STEPPED is refused with STEPPED_LIVE_OFF for `mode_ids` (all modes when
    none are given); every other answer is the show's own. Returns the engine."""
    allowed = engine.deps.tier_allowed

    def tier_allowed(mode_id: str, tier: Tier | str) -> tuple[bool, str]:
        if Tier(tier) is Tier.STEPPED and (not mode_ids or mode_id in mode_ids):
            return False, STEPPED_LIVE_OFF
        return allowed(mode_id, tier)

    engine.deps.tier_allowed = tier_allowed
    return engine
