"""Contracts between the look compiler (P2), the canopy engine + reconciler (P1) and the surfaces (P3).

    modes.yaml ──compile_mode()──▶ ModePlan ──CanopyEngine──▶ executor.burst (one PUT per module)
                                                 │
                                   CanopyState ──▶ WS topic "canopy" ──▶ /kiosk, /ops, /visualizer

Implementations: `conductor/looks/` (compile_mode, composer, slot manager), `conductor/canopy.py` (CanopyEngine),
`conductor/reconciler.py` (Reconciler).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from conductor.drivers.base import StateRow


class Tier(StrEnum):
    """The per-mode fallback ladder (04). Strand-technique modes are always STRAND."""

    T1 = "canvas"  # frames written by API into a slot at commissioning; selected at show time
    T2 = "slot"  # slots hand-painted in the Hubspace app; selected by API
    T3 = "modules"  # whole-strand looks composed at module level (each module = one coarse pixel)
    STRAND = "strand"  # the mode's primary technique is a whole-strand look (Calm, Holiday, Goodnight)


@dataclass
class ModuleTarget:
    """What one module must show for a mode. `rows` is the full state vector sent at show time (07 §5)."""

    device_id: str  # logical registry id
    rows: list[StateRow]  # show-time PUT: select/strand look + brightness + speed + party off (+ power)
    expect: list[StateRow]  # rows readback must show (normally == rows)
    slot: str | None = None  # the slot this mode selects (T1/T2)
    slot_value: dict | None = (
        None  # T1 only: the {"color-sequence-v2": ...} commissioning writes (header from readback)
    )
    slot_hash: str | None = None  # canonical hash of slot_value (or the recorded hash for T2)
    preview: list[dict] = field(
        default_factory=list
    )  # per-head {r,g,b} as it should look (visualizer / kiosk)


@dataclass
class ModePlan:
    mode_id: str
    label: str
    tier: Tier
    feature: str  # the "Hubspace feature" sentence for THIS tier/technique (kiosk copy)
    motion: bool  # re-trigger all modules as one burst or none (07 §6)
    modules: dict[str, ModuleTarget]  # logical device id → target
    notes: list[str] = field(default_factory=list)  # e.g. "2200 K clamped to the device floor 2700 K"
    # Canopy modules left out of this plan and why (e.g. their profile hasn't been read yet): the engine shows them
    # as needing attention and drives the others (07 §6: one module's problem never blocks the rest).
    skipped: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "mode": self.mode_id,
            "label": self.label,
            "tier": str(self.tier),
            "feature": self.feature,
            "motion": self.motion,
            "notes": self.notes,
            "skipped": self.skipped,
            "modules": {
                d: {"slot": t.slot, "slot_hash": t.slot_hash, "rows": [r.to_json() for r in t.rows]}
                for d, t in self.modules.items()
            },
        }


class PlanError(ValueError):
    """The mode can't compile at this tier (missing capability, slot hash unknown, ...). Operator-safe message."""


class RefusalReason(StrEnum):
    """Why `request_mode` declined a guest request (the kiosk route maps each to a plain HTTP refusal)."""

    COOLDOWN = "cooldown"  # inside the post-selection cooldown → 429 with retry_after_s
    PAUSED = "paused"  # ops paused guest input → 423
    UNKNOWN_MODE = "unknown_mode"  # no such mode → 404
    RESTING = "resting"  # the engine is stopped / outage → 503 "The canopy is resting"


class ModeRefused(Exception):
    """Raised by `request_mode` when a kiosk request is declined. `message` is guest-safe; `retry_after_s` is set
    for COOLDOWN (seconds until the next selection is accepted, on the engine's clock)."""

    def __init__(
        self, reason: RefusalReason | str, message: str = "", *, retry_after_s: float | None = None
    ) -> None:
        super().__init__(message or str(reason))
        self.reason = RefusalReason(reason)
        self.message = message
        self.retry_after_s = retry_after_s


class CanopyPhase(StrEnum):
    READY = "ready"  # every module's readback matches the current mode
    UPDATING = "updating"  # a mode change or re-assert is in flight
    NEEDS_ATTENTION = "needs_attention"  # some module is wrong past the window, offline, or locked
    RESTING = (
        "resting"  # guest input paused, outage, or the engine is stopped — kiosk shows "canopy is resting"
    )


@dataclass
class CanopyState:
    """Pushed on WS topic "canopy" and returned by GET /api/canopy/state. JSON-safe via to_json()."""

    mode: str | None
    label: str | None
    tier: str | None
    feature: str | None
    phase: CanopyPhase
    guest_input: bool  # False while ops paused guest input
    cooldown_until: float | None  # wall time; the kiosk greys out buttons until then
    changed_at: float | None
    modules: dict[str, dict[str, Any]]  # device id → {status, reason, label, module, locked, simulated}
    simulated: bool
    message: str = ""  # operator/guest-safe sentence explaining RESTING / NEEDS_ATTENTION
    # Set only on the state returned by a refused request_mode (cooldown, paused, resting, unknown mode): the
    # route answers 429/409 with `message` and a Retry-After of `retry_after_s` (None = no point retrying soon).
    refused: bool = False
    retry_after_s: float | None = None

    def to_json(self) -> dict:
        return {
            "mode": self.mode,
            "label": self.label,
            "tier": self.tier,
            "feature": self.feature,
            "phase": str(self.phase),
            "guest_input": self.guest_input,
            "cooldown_until": self.cooldown_until,
            "changed_at": self.changed_at,
            "modules": self.modules,
            "simulated": self.simulated,
            "message": self.message,
            "refused": self.refused,
            "retry_after_s": self.retry_after_s,
        }


class CanopyEngineAPI(Protocol):
    """What routes (kiosk, ops) call. Implemented by conductor/canopy.py CanopyEngine and reached as
    `services.extras["canopy"]` (routes: 503 with a plain message when it's missing)."""

    def state(self) -> CanopyState: ...

    def current_plan(self) -> ModePlan | None:
        """The plan for the mode now showing (per-module rows + per-head `preview`), for /visualizer and /ops."""

    async def request_mode(self, mode_id: str, *, source: str) -> CanopyState:
        """Guest/ops mode request. Kiosk requests honour the cooldown and paused guest input (refused with a
        plain message: raise `ModeRefused`); latest wins; returns immediately after the burst is queued (status
        via state()/WS)."""

    async def reassert_all(self, *, source: str) -> CanopyState: ...

    def pause_guest_input(self, paused: bool, *, source: str) -> CanopyState: ...

    async def safe_look(self, *, source: str) -> CanopyState:
        """Ops 'safe look': Lowcountry Calm at its strand technique, all modules, now."""

    async def commission(self, *, source: str) -> dict:
        """Write T1 slot contents to every module (≤ once per slot per 10 min) and record readback hashes."""
