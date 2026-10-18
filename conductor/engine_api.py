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
    # Module level, stepped (D65): the T3 design re-sent a step at a time, each module one colour per step, at
    # a few seconds a step through the cloud. Sim-first; the plan's `modules` are the hold (step 0, the static T3
    # look), the steps are `ModePlan.program`.
    STEPPED = "stepped"


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
    )  # per-head {r,g,b} as it should look at the look's brightness (visualizer, /ops)
    # Per-head {r,g,b} at full strength (the same rows with brightness 100), in wire order like `preview`: the guest
    # panel's preview draws these and shows the level separately, so a guest brightness never changes a colour.
    # Empty when the compiler can't say what the heads show (a T2 slot whose content isn't known).
    colors: list[dict] = field(default_factory=list)
    # The device's own brightness range: a guest level is clamped to it, as the designed one was (D60).
    brightness_range: tuple[int, int] | None = None


@dataclass
class ProgramStep:
    """One step of a stepped program: one full state vector per driven module (one PUT each, never a slot object),
    colour and brightness together."""

    index: int
    rows: dict[str, list[StateRow]]  # logical device id → the full state vector this step sends
    # canopy position ("r0c0") → "#RRGGBB" at full strength (the level is shown on its own)
    colours: dict[str, str]
    # canopy position → the device brightness this step sends (after the device's range clamp, before any guest
    # level): the rung's brightness on every step of a travel look, the breath's level on a breathe/shimmer one (D75)
    levels: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "index": self.index,
            "rows": {d: [r.to_json() for r in rows] for d, rows in self.rows.items()},
            "colours": self.colours,
            "levels": self.levels,
        }


@dataclass
class Program:
    """A look the Conductor moves itself, a step at a time (`kind` "stepped"): step n is the mode's design field
    shifted by n / len(steps) of one repeat, sampled at each module's centre. The Conductor holds the phase, so a
    single-module re-send of the current step is correct (07 §6's one-burst rule is for on-device motion):
    `ModePlan.motion` stays False. `moving_feature` is the copy shown only while a run is going; the plan's
    `feature` is the hold's (the static design), true before and after a run.

    `mode` is the technique's kind of motion (D75): "travel" shifts the design as above; for "breathe" and
    "shimmer", step n is each module's breath at its own lag (`looks.compile.step_looks`: the design in place, its
    colour offset and its level swung), and `steps` is one whole cycle (`looks.compile.program_cycle`). Every step's
    brightness is in its rows (one full state vector per module, colour and level in the same PUT)."""

    kind: str
    field: dict[str, Any]  # the design field spec (conductor/looks/field.py) as data
    direction: str  # "forward": the palette flows the way the layout words read (from the centre out, …)
    step_s: float  # the designed seconds per step (the runner may stretch it, never shorten it)
    run_s: float  # how long one run lasts before the hold is sent again
    steps: list[ProgramStep]
    moving_feature: str
    mode: str = "travel"  # last: tests build Program by position

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "mode": self.mode,
            "field": self.field,
            "direction": self.direction,
            "step_s": self.step_s,
            "run_s": self.run_s,
            "moving_feature": self.moving_feature,
            "steps": [s.to_json() for s in self.steps],
        }


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
    # The ripple order (D64): the driven modules' device ids grouped into at most three rings by the mode's design
    # field at their centres, the ring the design starts from first (`looks.compile._waves`). The engine sends a
    # kiosk/ops/idle mood ring by ring, `kiosk.ripple_stagger_s` apart; still one PUT per module. None = one burst
    # (a motion plan, a mode with no design, or modules with no canopy position).
    waves: list[list[str]] | None = None
    # A stepped look's steps (Tier.STEPPED); None for every other tier. `modules` is then step 0, the hold.
    program: Program | None = None

    def to_json(self) -> dict:
        return {
            "mode": self.mode_id,
            "label": self.label,
            "tier": str(self.tier),
            "feature": self.feature,
            "motion": self.motion,
            "notes": self.notes,
            "skipped": self.skipped,
            "waves": self.waves,
            "program": self.program.to_json() if self.program is not None else None,
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
    # Wall time guest input was paused (None while it's on). A pause survives a restart (saved under <data>/state/).
    paused_since: float | None = None
    # Guest overrides (D60), part of the engine's desired state. `guest_brightness`: the device brightness a guest set
    # for every module (None = the look's designed brightness); `guest_power`: False while a guest has the canopy off.
    # `brightness`: the level the canopy is set to now (the guest's, else the designed one — the brightest module's
    # when modules differ; None before the first look). `guest_brightness_min`: the lowest level a guest may set.
    guest_brightness: int | None = None
    guest_power: bool = True
    brightness: int | None = None
    guest_brightness_min: int | None = None
    # The phase of the look itself, judged over the modules the current plan drives (D62 follow-up): the modules it
    # left out (`ModePlan.skipped`, e.g. no device registered) keep `phase` at NEEDS_ATTENTION for /ops, but never
    # hide a change or a finished look from the guest. None = the same as `phase` (an engine that doesn't set it).
    look_phase: CanopyPhase | None = None
    # A ripple in progress (D64): {t0: wall time ring 0 was sent, stagger_s, rings: [[position]]} — ring j goes out
    # at t0 + j × stagger_s. None when no ring is left to send (or the look went out as one burst).
    ripple: dict[str, Any] | None = None
    # A stepped run in progress (Tier.STEPPED, conductor/sequence.py): {t0: wall time step 0 was sent, step_s (the
    # runner's step, which may be longer than the design's), step: the step number showing now, ends_at: wall time
    # the still look is sent again, frames: [[hex | None] × 9 in grid order r0c0…r2c2] — frame n is step n's colours
    # at full strength, the last frame the still look; None where no module is driven, levels: [[int | None] × 9]
    # alongside frames — the brightness each position is sent at that frame, guest level applied (D75)}. Frames (and
    # levels) after the step last sent, up to the schedule's step, repeat the step last sent: a budget-held step is
    # never drawn. None while nothing steps (no run, the run is over, or its writes are held: paused, resting,
    # set-up, the guest turned the canopy off).
    program: dict[str, Any] | None = None

    def to_json(self) -> dict:
        return {
            "mode": self.mode,
            "label": self.label,
            "tier": self.tier,
            "feature": self.feature,
            "phase": str(self.phase),
            "look_phase": str(self.look_phase) if self.look_phase is not None else None,
            "guest_input": self.guest_input,
            "paused_since": self.paused_since,
            "cooldown_until": self.cooldown_until,
            "changed_at": self.changed_at,
            "modules": self.modules,
            "simulated": self.simulated,
            "message": self.message,
            "refused": self.refused,
            "retry_after_s": self.retry_after_s,
            "guest_brightness": self.guest_brightness,
            "guest_power": self.guest_power,
            "brightness": self.brightness,
            "guest_brightness_min": self.guest_brightness_min,
            "ripple": self.ripple,
            "program": self.program,
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

    async def set_guest_brightness(self, level: int, *, source: str) -> CanopyState:
        """Absolute device brightness for every canopy module, clamped to [kiosk.guest_brightness_min, 100] (D60). It
        becomes part of the desired state (the reconciler keeps it); it turns the canopy on; one write per module (the
        brightness row of the full state vector, never a frame). Kiosk requests are refused like `request_mode`
        (cooldown, paused, resting); the refusal comes back as a state with `refused=True`."""

    async def set_guest_power(self, on: bool, *, source: str) -> CanopyState:
        """Turn the whole canopy off (one `power off` write per module) or back on (the current look's full state
        vector, with any guest brightness). Same refusals as `set_guest_brightness`."""

    async def clear_guest_overrides(self, *, source: str) -> CanopyState:
        """Ops and the idle reset: drop the guest's brightness and power settings (on, designed brightness)."""

    async def reassert_all(self, *, source: str) -> CanopyState: ...

    def pause_guest_input(self, paused: bool, *, source: str) -> CanopyState: ...

    async def safe_look(self, *, source: str) -> CanopyState:
        """Ops 'safe look': Lowcountry Calm at its strand technique, all modules, now."""

    async def commission(self, *, source: str) -> dict:
        """Write T1 slot contents to every module (≤ once per slot per 10 min) and record readback hashes."""
