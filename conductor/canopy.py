"""The canopy engine: guest/ops mode requests → one PUT per module, and an honest phase (05 #6–#8, 07 §5–§6).

    request_mode ──compile(mode, tier)──▶ ModePlan ──▶ tracker desired + executor.burst (one PUT per module)
                                                            │ results, readbacks (telemetry)
                                  Reconciler (reconciler.py) ◀┘ re-assert · true-up · hysteresis · outage · idle

Rules implemented here (binding text: `conductor/engine_api.py`, 07 §5–§6):
- **Requests.** A kiosk request is refused (a plain sentence + retry-after) during the cooldown
  (`kiosk.cooldown_s` after the last accepted kiosk change), while guest input is paused, while the canopy is
  resting (outage) and while commissioning runs. Ops, idle and reconciler requests bypass the cooldown and the
  pause. Refusals come back as a `CanopyState` with `refused=True` (never an exception).
- **Tier.** The first tier on the mode's ladder that `tier_allowed` permits and that compiles, starting at any
  hysteresis floor currently in force for that mode (the reconciler sets floors). If nothing at or below the floor
  compiles, tiers above it are tried before refusing. The kiosk copy is the compiled plan's `feature` sentence, so
  it always follows the tier in use.
- **Apply.** Every module's tracker desired state becomes the plan's `expect` rows, and exactly one Command per
  module (Purpose.ENGINE, verify=True, expect=target.expect) goes out through the executor's `burst`. The call
  returns at once (phase UPDATING); a background task awaits the results. Latest wins: a newer request supersedes
  the older one's pending commands (the executor coalesces per device).
- **Locks.** A module whose driver address is locked (bench job, /ops/module) is skipped and shown as `locked`, never
  as failed. When its lock is released the reconciler sends it the current look.
- **Phase.** RESTING while guest input is paused, during an outage, before the first mode and after stop();
  NEEDS_ATTENTION when a module has needed attention (wrong after re-asserts, offline, signed out) longer than
  `reconciler.downgrade_after_wrong_s`, can't be addressed, or every module is locked; UPDATING while any command is pending, verifying or
  waiting for a readback; READY when every non-locked module's readback matches (tracker READY). `look_phase` is
  the same judgement over the modules the plan drives (the ones it left out don't count), so a partial canopy stays
  NEEDS_ATTENTION on /ops while the guest still sees its look change and settle.
- **Outage.** When `CONNECTION` / `TIMEOUT` / `SERVER_ERROR` outcomes — or THROTTLED with HTTP 503/504, which the
  Afero adapter keeps retryable but counts as an outage signature (D15) — hit at least `min(3, modules)` different
  modules with no success in between, the engine rests ("The canopy is resting…"); the reconciler probes one module every
  `outage_probe_interval_s` and, on the first good readback, re-applies the current mode to every module in one
  burst. Ops requests during an outage become the current mode and go out on recovery. A driver's optional
  `on_recovery` list (the Afero adapter's watchdog) triggers an immediate probe.
- **Early re-assert.** When a verification readback of the current command still differs from what was sent in a
  row a re-send can fix (the cloud stored nothing: a silent drop), the module is re-sent at once instead of after the
  executor's remaining readbacks — at most `EARLY_REASSERT_LIMIT` times per module per change. A motion plan
  re-triggers every module as one burst instead, once per change. A change's first re-send waits for the command's
  second readback (`EARLY_REASSERT_FROM_READBACK`): live, a differing first readback is usually the cloud still
  echoing the old value (D70); later re-sends in the same change go on the first. A command that ends MISMATCH is
  re-asserted at the next reconciler tick; one that ends FAILED after `reassert_after_s`.
- **Commissioning.** `commission()` writes each T1 mode's slot content (the slot object only, one PUT per module,
  header copied from the device's own readback of that slot, verified on the slot row) and records the canonical
  hash of the readback per (device, slot), with origin "api" and the header's effect fields, through `record_slot`
  (or `record_slot_hash`). A slot recorded as hand-painted in the Hubspace app (origin "app") is never overwritten.
  Afterwards the current mode is re-picked (commissioning can unlock T1).
- **Slot expectations.** A commissioned T1 slot is judged by the hash recorded from its readback, not by a frame
  compiled now (`_expect`); when the look now compiles to something else (recalibration, a design edit, an effect
  code enabled or disabled) the reconciler rewrites that stale slot, rate-limited, and the expectation, the copy and
  the motion flag follow the new record. A write that didn't land (silent drop,
  throttling past the executor's retries, a throttled header read) is retried within the same run, up to
  `COMMISSION_ROUNDS`; a slot that did land is written again at most once per `reconciler.slot_rewrite_min_interval_s`.
  Guest requests, the reconciler and idle hold wait while it runs.
- **Throttling.** Only a command that still ended THROTTLED (429/403 — not 503/504, an outage) after the executor's
  retries counts toward the hysteresis throttle downgrade (see reconciler.py) — a 429 that a retry cleared never does.
- **Guest-input pause survives a restart (07 §8, review F3).** Pausing or resuming writes
  `<data>/state/guest_input.json` atomically; the engine reads it when it is built, so a watchdog restart, a reboot,
  a release or break-glass hand-back never quietly turns guest input back on. A pause restored at start is alerted
  on /ops. A file that can't be read counts as paused (staff resume it on /ops), never as resumed.
- **Modules left out.** A module the plan skipped (`ModePlan.skipped`, e.g. its profile hasn't been read) is shown as
  needing attention; the others are driven.
- **Guest brightness and power (D60).** `set_guest_brightness` / `set_guest_power` are part of the DESIRED state:
  every show command sends the current plan's full state vector with them applied (`desired_rows`: the brightness
  row set to the guest's level, or just `power off`), the tracker expects that, and the reconciler compares readback
  against it — so it re-asserts the guest's choice after drift and never fights it. A change is one burst, one write
  per module; frames are never touched. Kiosk changes are refused like a mode (cooldown — shared with mood choices —
  pause, resting); nothing changes while stopped, commissioning or before the first look. Choosing a mood (kiosk,
  ops, idle) clears the brightness and turns the canopy on; re-picking the look now showing (`RECOMPILE_SOURCES`: a
  tier change, new capabilities, recovery) keeps both. The safe look clears both. The reconciler's idle reset clears
  them `kiosk.idle_reset_s` after the last guest input (and optionally shows `kiosk.idle_scene`). Kept in memory
  only: a restart comes back on, at the designed brightness.
- **The ripple (D64).** A mood chosen on the kiosk, by ops or by the idle return (not a source in
  `RECOMPILE_SOURCES`) whose plan has `waves` lands ring by ring when `kiosk.ripple_stagger_s` > 0: ring 0 is sent
  at once, ring k `k × stagger` later on the injected clock — each module still gets exactly one full-state PUT, its
  rows never split. A ring is sent only while its look is still the current one (the generation), so a newer look
  drops the rings not yet sent. The tracker's desired state moves with each ring as it is sent, so a module still
  waiting keeps its old desired state and the reconciler can't send it early; the reconciler also skips a waiting
  module (`wave_pending`) when it re-asserts, true-ups or sees a lock released. `state()` shows a waiting module as
  updating ("Waiting for its turn in the ripple"), so the look reads UPDATING until the last ring is out, and
  `CanopyState.ripple` says when each ring goes (the guest panel draws it ring by ring). Anything that sends every
  module at once (`send(None)`: a re-assert of all, a guest brightness or power change, recovery) sends the waiting
  rings with it. The safe look, start-up (the first look, however it is requested: `start()`, or the show's wiring
  asking for the idle mode once its capability reads are back), re-picks of the look now showing (a tier change,
  recovery, new slot records), lock releases and re-asserts are always one burst; so is a motion plan (`waves` None).
- **Stepped looks (Tier.STEPPED, D65).** Installing a plan with a `program` starts a `ProgramRunner`
  (conductor/sequence.py) on the injected clock; a newer look, or `stop()`, cancels it. While it runs, the desired
  state of each driven module is the current step's full state vector (`desired_rows` / `_desired_expect` ask the
  runner), so every re-send — a re-assert, a lock release, recovery, a guest change — sends the current step. The
  runner's own steps go through `send(…, source="program")`, only to the modules whose rows changed. While the run
  moves, `feature` is the program's moving sentence and `CanopyState.program` says what each step shows; the phase
  judges each module by whether the writes sent to it verify (`ProgramRunner.status`), not by the step in flight.
  A guest brightness change just before a step goes out with that step. After the run, the plan's own sentence and
  the still design (the hold) are back.
- **The stepped rehearsal (Gate R, conductor/rehearsal.py).** `rehearse(mode, minutes, source)` — operator-started
  only, from the ops route behind `rehearsal_preview`'s confirm sentence — runs one stepped look of `mode` whose run
  lasts `minutes` (1–10; `_install` gives the program that run_s), from the mode's own stepped design or its
  module-level design stepped (`EngineDeps.rehearsal_compile`). While it runs `rehearsal_mode` names the mode, and
  the show's tier rule opens the stepped tier on live hardware for that mode only. Kiosk requests are refused
  (`REHEARSAL_MESSAGE`) and the idle hold waits. It ends when the run does, at the first 403, when the governor
  trips, when ops stops it or pauses guest input, when another look replaces it (ops, a downgrade) and on stop();
  after a run that ended on its own (or was stopped) the mode's still look stays at its usual tier (`_settle`, sent
  only where the rows differ, after the report), and the idle hold counts from the end. A 403 (`_rehearsal_403`)
  sends nothing more: no hold burst, the step the canopy was sent installed as a still T3 plan, no stepped look or
  re-run on that account for `governor_cooloff_s`, then `_settle`. Its report goes to telemetry ("rehearsal") and
  `<data>/bench/rehearsals/*.json`.

- **The Design Lab's try-on (D66 Slice B, docs/design-lab/PLAN.md §6).** `start_trial(Trial)` — ops only, from
  `/api/lab/trial/start` behind its confirm sentence — shows a draft of one scene through `_request(source="lab",
  ripple=False)`: one burst, at module level or strand only (`compile_best` skips `TRIAL_SKIP` for the trial's mode
  while the override stands; the show compiles those two tiers from the trial's patched modes and recipes and every
  other tier, T1 slot judging included, from git). Refused while stopped, commissioning, paused, resting or
  rehearsing. Kiosk requests are refused (`TRIAL_MESSAGE`) while it runs. It ends at its bound (`ends_mono`, at most
  `kiosk.idle_return_s`), on "Back to approved" (`end_trial`), and whenever anything else chooses a look (ops, idle,
  the safe look, a rehearsal, commissioning) or guest input is paused or the engine stops: the override is cleared
  before that look is compiled, and a caller that then can't install its own look puts the approved one back
  (`_restore_approved`), so a trial plan never stays installed without its override and bound. Memory only: a
  restart has no trial.

- **The operator repaint (D68 PR B2, docs/live-lab/PLAN.md §2.6–§2.7).** A publish of a new painted design hands
  the engine ONE job (`start_repaint`, a list of `RepaintJob`s: a scene, its slot, the modules whose commissioned
  record doesn't hold the live design, when it is due). The job's (device, slot) pairs are marked pending at once
  (`repaint_pending`: the reconciler's `check_slots` skips them, and the scene is held below T1 while any is pending);
  a newer job cancels one that hasn't started writing, its marks with it. Each scene waits until it is due — at most
  once per `lab.repaint_min_interval_s` per scene, enforced here (`_repaint_last`, monotonic; seeded from the
  overlay's wall times after a restart, clamped to the interval) — then, unless the engine is stopped, commissioning,
  resting or rehearsing, opens a **quiet window** (`quiet`: the public `commissioning` property says so, so the
  reconciler, the idle hold, the stepped runner and the capability re-pick hold still; guest, ops and try-on requests
  are refused with `REPAINT_MESSAGE` and a retry-after; a try-on ends first), waits for the commands in flight, and
  writes the slot on each listed module through `_commission_plan(min_interval=0, only=…, verb="publish repaint",
  locked=True)`: one slot write per module under its `_slot_lock`, header from the device's readback, retried at most
  twice more only when it didn't land, never over a hand-painted ("app") slot or a module held by the module page,
  `slot_written` stamped at submit (the reconciler's 600 s limit counts from the attempt), the readback hash recorded
  exactly as commissioning records it. Its `finally` clears the scene's marks and the window; a fully applied scene's
  hold lifts and the look showing is re-picked once (the re-select after rewriting the active slot: one T1 burst).
  `commission()` is refused while a job runs; a publish is refused only while one is writing (`looks_refusal`).

All timing comes from the injected Clock (safe on VirtualTimeLoop); nothing here touches the network except through a
Target's driver/executor.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from collections.abc import Awaitable, Callable, Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conductor import rows as R
from conductor.clock import Clock
from conductor.config import ConductorSettings, Registry
from conductor.drivers.base import Outcome, StateRow
from conductor.engine_api import CanopyPhase, CanopyState, ModePlan, ModuleTarget, PlanError, Tier
from conductor.executor import CommandExecutor
from conductor.executor_api import Command, CommandResult, CommandStatus, DeviceStatus
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager
from conductor.reconciler import Reconciler
from conductor.rehearsal import MAX_MINUTES, REHEARSAL_DIR, Rehearsal, RehearsalRefused, confirm_sentence
from conductor.sequence import ProgramRunner, planned_writes_per_min
from conductor.state import DeviceStateTracker
from conductor.telemetry import Telemetry
from conductor.tokens import atomic_write

log = logging.getLogger(__name__)

TOPIC = "canopy"
SAFE_MODE = "lowcountry-calm"
RESTING_MESSAGE = "The canopy is resting. It will be back shortly."
PAUSED_MESSAGE = "Guest input is paused by staff. The canopy is resting."
COMMISSIONING_MESSAGE = "The canopy is being set up. Try again in a minute."
STOPPED_MESSAGE = "The canopy engine is stopped."
STARTING_MESSAGE = "The canopy is getting ready."
REHEARSAL_MESSAGE = "Staff are testing the canopy's lights. Try again in a few minutes."
OUTAGE_OUTCOMES = frozenset({str(Outcome.CONNECTION), str(Outcome.TIMEOUT), str(Outcome.SERVER_ERROR)})
# 503/504 come back THROTTLED (retryable, D15) but are an outage signature, not rate limiting: they rest the canopy
# like SERVER_ERROR and never count toward the throttle downgrade. Only 429/403 are throttling.
OUTAGE_HTTP_STATUSES = frozenset({503, 504})
REPLAN_DELAY_S = 1.0  # re-pick the current look this long after its slots' records changed (coalesced)
OUTAGE_MIN_MODULES = 3  # distinct modules failing (no success in between) before the canopy rests
_RESEND_STATUSES = (CommandStatus.FAILED, CommandStatus.MISMATCH)
_SLOT_RETRY = (CommandStatus.FAILED, CommandStatus.MISMATCH, CommandStatus.SUPERSEDED)
COMMISSION_ROUNDS = (
    3  # a slot write that didn't land (silent drop, throttling) is retried within one commission
)
PUBLISH_MIN_INTERVAL_S = 0.25  # state pushes are coalesced to at most 4 per second
EARLY_REASSERT_LIMIT = 3  # per module per change
# The verification readback (1 = first) from which a change's first early re-send may fire. On Oct 17–18 the first
# readback (≈ 1.2 s after the ack) still showed the previous scene's brightness/colour on 33 of 56 live scene changes,
# while re-sends of the same rows read back correct at once: cloud echo lag, not a drop (D70,
# docs/reports/readback-echo-lag-2026-10-18.md).
EARLY_REASSERT_FROM_READBACK = 2
HEADER_READ_ATTEMPTS = 3  # the readback a slot write copies its header from
# The guest-input pause, kept across restarts, under EngineDeps.state_dir (<data>/state).
GUEST_INPUT_FILE = "guest_input.json"
# Sources that re-pick the look now showing (a tier change, new capabilities, recovery, the simulator's start-up
# commissioning) rather than choose one: they keep the guest's brightness and power. Any other source choosing a mode
# (kiosk, ops, idle) clears them, like choosing a scene in the Hubspace app (D60).
# A publish from the Design Lab (D68) re-picks the look now showing under the new live looks: it keeps the guest's
# brightness and power (D60), is one burst (ONE_BURST_SOURCES ⊇ it) and never ends a rehearsal (a publish is refused
# during one). `_request`'s `source not in ("reconciler", "recovery")` line still counts it as a change for the idle
# hold: staff just changed the look. ("publish" already means the state push here, so the name says looks.)
LOOKS_SOURCE = "looks"
RECOMPILE_SOURCES = frozenset({"reconciler", "recovery", "startup", LOOKS_SOURCE})
BRIGHTNESS_KEY = ("brightness", None)
# The Design Lab's try-on (D66 Slice B): its request source, the guest's refusal while it runs, the tiers it never
# uses (a try-on is module level only and never steps), and the ripple seam (a try-on is one burst; RECOMPILE_SOURCES
# itself is unchanged, so a try-on still clears the guest's brightness and counts as a change for the idle hold).
TRIAL_SOURCE = "lab"
TRIAL_MESSAGE = "Staff are trying a new look on the canopy. Try again in a few minutes."
TRIAL_PAUSED_MESSAGE = (
    "Guest input is paused by staff, so a try-on can't start; resume guest input on /ops first."
)
TRIAL_REHEARSAL_MESSAGE = "A stepped rehearsal is running; stop it first."
# A re-apply too close to the running try-on's bound (it would land just before the bound puts the approved look back)
TRIAL_ENDING_MESSAGE = (
    "This try-on ends in {left} s, too soon for another apply; apply again once it has ended."
)
# The try-on the apply was confirmed against ended, or another began, while the apply was being built
TRIAL_CHANGED_MESSAGE = (
    "The try-on changed while this apply was being prepared; apply again to see what it will do."
)
_ANY_SESSION: Any = object()  # start_trial's default: whatever runs (or nothing)
TRIAL_SKIP = frozenset({Tier.T1, Tier.T2, Tier.STEPPED})
ONE_BURST_SOURCES = RECOMPILE_SOURCES | {TRIAL_SOURCE}
# The operator repaint (D68 PR B2): what a guest, ops or a try-on hears during its quiet window (true: the slots are
# being rewritten), the verb on its commands' labels ("publish repaint: Golden Hour → custom-3"), and the window's
# estimate (about M·1.2 + 3 s for M modules: one slot write each, paced by the executor, plus the header reads).
REPAINT_MESSAGE = "One moment: the canopy is changing."
REPAINT_VERB = "publish repaint"
REPAINT_PER_MODULE_S = 1.2
REPAINT_BASE_S = 3.0
# the window waits this long at most for the commands already in flight (as commissioning does)
REPAINT_IDLE_WAIT_S = 30.0

Compiler = Callable[[str, Tier], ModePlan]
Ladder = Callable[[str], list[Tier]]
TierAllowed = Callable[[str, Tier], tuple[bool, str]]
SlotHashRecorder = Callable[[str, str, str], None]
# (device id, slot, {"hash", "origin", "effects"}) — the full record (origin "api" for commissioning)
SlotRecorder = Callable[[str, str, dict], None]


def is_outage_signal(outcome: Any, http_status: int | None) -> bool:
    """Does this attempt/readback outcome say Hubspace is down (rest the canopy), rather than busy or picky?"""
    outcome = str(outcome)
    if outcome in OUTAGE_OUTCOMES:
        return True
    return outcome == str(Outcome.THROTTLED) and http_status in OUTAGE_HTTP_STATUSES


def _all_allowed(_mode: str, _tier: Tier) -> tuple[bool, str]:
    return True, ""


@dataclass
class EngineDeps:
    """What the engine needs from the Services container (the lead wires these; tests build them directly)."""

    registry: Callable[[], Registry]  # the current registry (ops edits replace it at runtime)
    target: Callable[[str, Purpose], Any]  # services.target(device_id, purpose) → Target
    tracker: DeviceStateTracker
    telemetry: Telemetry
    hub: Any  # WsHub-like: publish(topic, data); optional register_snapshot / client_count
    clock: Clock
    settings: ConductorSettings
    locks: DeviceLockManager
    compile: Compiler  # (mode_id, tier) → ModePlan, raising PlanError when that tier can't compile
    ladder: Ladder  # mode_id → tiers to try, best first (KeyError for an unknown mode)
    modes: Callable[[], list[str]] = list  # every mode id (commissioning walks them)
    tier_allowed: TierAllowed = _all_allowed  # capabilities + bench flags → (allowed, why not)
    record_slot_hash: SlotHashRecorder | None = None  # lead: → registry overlay slot_hashes
    simulated: Callable[[], bool] | None = None  # lead: lambda: services.engine_driver.simulated
    # Preferred over record_slot_hash when set: saves hash + origin + effect fields in one overlay write.
    record_slot: SlotRecorder | None = None
    # <data>/state: where the guest-input pause is kept across restarts (None = memory only: tests, the bare soak)
    state_dir: Path | None = None
    # The stepped rehearsal (Gate R, conductor/rehearsal.py): the stepped plan a rehearsal of a mode runs (the show:
    # its own stepped technique or its module-level design stepped; None = `compile(mode, Tier.STEPPED)`), and
    # <data>/bench/rehearsals, where its reports go (None = telemetry only).
    rehearsal_compile: Callable[[str], ModePlan] | None = None
    rehearsal_dir: Path | None = None
    # The operator repaint (D68 PR B2): mode → its RepaintTarget against the live looks NOW (ShowWiring.repaint_plan:
    # `listed`, the modules whose commissioned record doesn't hold the live design, and `plan`, T1 compiled over the
    # eligible modules; None when the mode has no painted slot); the live looks' generation (a newer publish aborts a
    # job); and a hook told of each step of a job — ("progress", RepaintJSON), ("started", {mode, slot, at,
    # publication}), ("finished", {mode, slot, publication, applied, skipped, failed, …}) — for the overlay and the Lab.
    repaint_target: Callable[[str], Any] | None = None
    looks_generation: Callable[[], int] | None = None
    on_repaint: Callable[[str, dict], Any] | None = None
    # Awaited by a due scene before its window opens: the show's start-up is over (hardware: the first round of
    # capability reads; sim: the start-up commissioning), so a job re-queued at engine start never compiles its
    # target before any profile is read. None = no wait.
    repaint_ready: Callable[[], Awaitable[Any]] | None = None


@dataclass(frozen=True)
class RepaintJob:
    """One scene of an operator repaint (D68 PR B2): its slot, the modules to repaint (marked pending at once), its
    label (the Lab), and when it is due (monotonic; `start_repaint` never lets it run sooner than the gate allows)."""

    mode_id: str
    slot: str
    modules: tuple[str, ...]
    label: str = ""
    due_mono: float = 0.0


@dataclass
class Trial:
    """A Design Lab try-on (D66 Slice B): one scene's draft shown at module level until `ends_mono`. Memory only."""

    mode_id: str
    label: str  # the draft's name (telemetry, the Lab)
    modes: Any  # the patched ModesFile: opaque to the engine; the show compiles T3/strand with it
    # the patched RecipesFile: T3/strand colours come from recipes (a hex edit, a renamed colour)
    recipes: Any
    started_mono: float
    ends_mono: float
    started_at: float
    version: str | None = None
    applies: int = 1
    session: int = 0  # set by the engine: a re-apply keeps it (and the bound); a new try-on gets a new one


@dataclass
class _Sent:
    """Book-keeping for one command the engine submitted."""

    command: Command
    device_id: str
    generation: int
    kind: str  # "show" (a mode's state vector) or "slot" (a slot object write)


class CanopyEngine:
    """Implements `CanopyEngineAPI`. Lives at `services.extras["canopy"]`."""

    def __init__(self, deps: EngineDeps) -> None:
        self.deps = deps
        self.clock = deps.clock
        self.telemetry = deps.telemetry
        self.tracker = deps.tracker
        self.locks = deps.locks
        self.reconciler = Reconciler(self)
        self._plan: ModePlan | None = None
        self._source: str | None = None
        self._changed_at: float | None = None
        self._generation = 0
        self._targets: dict[str, Any] = {}  # device id → Target (resolved at each apply)
        self._by_address: dict[str, str] = {}  # driver address → device id
        self._unresolved: dict[str, str] = {}  # device id → why it has no address
        self._locked: set[str] = set()  # addresses locked at the last look
        self._paused = False
        self._paused_since: float | None = None  # wall time the current pause started (kept across restarts)
        self._outage: dict | None = None
        self._commissioning = False
        self._stopped = False
        self._started = False
        self._last_kiosk_mono: float | None = None
        self.last_change_mono: float = self.clock.monotonic()  # any accepted change (idle hold)
        # the last look chosen (not a re-pick, not a guest setting): a stepped look's run counts from it
        self._chosen_mono: float = self.last_change_mono
        # Guest overrides (D60): a brightness for every module (None = designed) and on/off. Desired state, not saved.
        self._guest_brightness: int | None = None
        self._guest_power = True
        self.last_guest_input_mono: float = self.clock.monotonic()  # the idle reset counts from it
        self.floors: dict[str, int] = {}  # mode id → ladder index floor set by a hysteresis downgrade
        self._sent: dict[int, _Sent] = {}  # id(command) → record, until its result arrives
        self._latest: dict[str, Command] = {}  # address → the newest show command the engine sent
        # address → final status of the current look's newest command
        self.last_status: dict[str, CommandStatus] = {}
        self._early_counts: dict[str, int] = {}  # address (or "*" for a motion plan) → early re-sends
        self._bad_streak: set[str] = set()
        self.dirty: set[str] = set()  # addresses with news since the reconciler last looked at their status
        self._tasks: set[asyncio.Task] = set()
        self._executors: dict[int, CommandExecutor] = {}
        self._unsubscribe: list[Callable[[], Any]] = []
        self._recovery_lists: list[list] = []
        self._publish_scheduled = False
        self._last_published: dict | None = None
        self._last_publish_mono = float("-inf")
        # (device id, slot) → monotonic time the slot was last written (the rewrite limit counts from it)
        self.slot_written: dict[tuple[str, str], float] = {}
        # (device id, slot) → {hash, mode, tier, origin, effects}: what this process commissioned
        self.slot_records: dict[tuple[str, str], dict] = {}
        self._t1_plans: dict[str, ModePlan] = {}  # T1 plans compiled for commissioning and drift rewrites
        self._t1_failed: set[str] = set()
        self._t1_key: Any = None  # the registry the cached T1 plans were compiled against
        self._replan_handle: asyncio.TimerHandle | None = None
        self._slot_locks: dict[str, asyncio.Lock] = {}
        self.recovery_listeners: list[Callable[[], Any]] = []  # called when an outage ends (ShowWiring)
        self.stats: dict[str, Any] = {"outages": [], "tier_changes": [], "early_reasserts": 0}
        # The ripple in progress (D64): {t0 (wall), stagger_s, rings: [[device id]]}, and device id → the
        # ring each module still waiting for its turn is in. Both empty when no ring is left to send.
        self._ripple: dict[str, Any] | None = None
        self._pending: dict[str, int] = {}
        # Stepped looks: the runner of the plan showing (None for any other tier); device id → the rows last sent to
        # it (a step re-sends only the modules whose rows changed); account → monotonic time the throttle governor's
        # cool-off ends; and the stepped rehearsal running now (Gate R: one bounded, operator-started run), with the
        # summary of the last one that ended.
        self._runner: ProgramRunner | None = None
        self.sent_rows: dict[str, list[StateRow]] = {}
        self.stepped_cooloff: dict[str, float] = {}
        self._settle_task: asyncio.Task | None = (
            None  # a still look due after a 403's cool-off (`_settle_later`)
        )
        self._rehearsal: Rehearsal | None = None
        self._rehearsals: list[Rehearsal] = []  # ended, report still being written (stop() waits for them)
        self.last_rehearsal: dict | None = None
        # The Design Lab's try-on (D66 Slice B): the override in force, and the task that ends it at its bound (a loop
        # task like _settle_task, not in self._tasks, so wait_idle never waits for the bound). Never persisted.
        self._trial: Trial | None = None
        self._trial_task: asyncio.Task | None = None
        self._trial_sessions = 0
        # The live looks (D68): painted modes kept off T1 because their commissioned slots don't hold the live design
        # (yet). `compile_best` skips T1 for them (never `tier_allowed`: t1_target and stale_slot must still see the
        # slot as stale, or the reconciler would never rewrite it and the hold would never lift); a slot record that
        # makes the mode's slots current lifts it (`_record_slot_value`), with one T1 re-pick.
        self._t1_hold: set[str] = set()
        # The operator repaint (D68 PR B2): (device, slot) → the job that marked it pending; the job's task (not one of
        # the burst tasks: wait_idle never waits for a queued repaint, and the job itself waits for them); the quiet
        # window and its estimated end; mode → monotonic start of its last operator repaint (the 2-min gate); the
        # scenes of the running job not started yet (a newer job keeps their due times); the job's RepaintJSON (the
        # Lab); listeners told when a quiet window ends (the show's capability loop re-picks what it deferred).
        self._repaint_pending: dict[tuple[str, str], int] = {}
        self._repaint_task: asyncio.Task | None = None
        self._repaint_quiet = False
        self._repaint_serial = 0
        self._repaint_last: dict[str, float] = {}
        self._repaint_ends: float | None = None
        self._repaint_waiting: dict[str, RepaintJob] = {}
        self.repaint_status: dict | None = None
        self.quiet_listeners: list[Callable[[], Any]] = []
        self.stats["repaints"] = 0
        # modes a publish's re-pick couldn't show under the published looks (their warn stands until one shows)
        self._unshowable: set[str] = set()
        self.stats["governor_trips"] = 0
        self._unsubscribe.append(self.telemetry.subscribe(self._on_event))
        self._restore_pause()

    # ---- guest-input pause, kept across restarts ------------------------------------------------------------------
    def _pause_path(self) -> Path | None:
        state_dir = self.deps.state_dir
        return Path(state_dir) / GUEST_INPUT_FILE if state_dir is not None else None

    def _restore_pause(self) -> None:
        """Read the saved pause (if any). Unreadable → paused: a restart must never quietly let guests back in."""
        path = self._pause_path()
        if path is None or not path.exists():
            return
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or not isinstance(saved.get("paused"), bool):
                raise TypeError("no paused flag")
        except (OSError, ValueError, TypeError) as e:
            log.warning("guest-input pause file %s is unreadable (%s): keeping guest input paused", path, e)
            self._paused, self._paused_since = True, self.clock.now()
            self.telemetry.record(
                "alert",
                level="warn",
                message="Guest input is paused: the saved pause setting couldn't be read after the restart, so "
                "the Conductor kept guests out to be safe. Press Resume guest input on /ops when ready.",
                source="restart",
            )
            return
        if not saved["paused"]:
            return
        since = saved.get("since")
        self._paused = True
        self._paused_since = float(since) if isinstance(since, int | float) else None
        when = f" since {_clock_time(self._paused_since)}" if self._paused_since else ""
        who = f" by {saved['source']}" if isinstance(saved.get("source"), str) and saved["source"] else ""
        self.telemetry.record(
            "alert",
            level="warn",
            message=f"Guest input is still paused after the restart (paused{who}{when}). Press Resume guest input on "
            "/ops once the checks are done.",
            source="restart",
        )

    def _save_pause(self, source: str) -> None:
        path = self._pause_path()
        if path is None:
            return
        body = {
            "paused": self._paused,
            "since": self._paused_since,
            "source": source,
            "saved_at": self.clock.now(),
        }
        try:
            atomic_write(path, json.dumps(body).encode(), mode=0o600)
        except OSError as e:
            log.exception("couldn't save the guest-input pause to %s", path)
            self.telemetry.record(
                "alert",
                level="warn",
                message=f"Guest input is {'paused' if self._paused else 'on'}, but that couldn't be saved ({e}): a "
                "restart would forget it. Check the data volume.",
                source=source,
            )

    # ---- lifecycle -------------------------------------------------------------------------------------------
    async def start(self, *, apply_initial: bool = True) -> None:
        """Start the reconciler loop (and, by default, show the idle mode — the canopy's resting look)."""
        if self._started:
            return
        self._started = True
        self._stopped = False
        register = getattr(self.deps.hub, "register_snapshot", None)
        if register is not None:
            register(TOPIC, lambda _topic: self.state().to_json())
        for device_id in self._module_ids():
            self._resolve(device_id)
        self.reconciler.start()
        if apply_initial:  # start-up is one burst, never a ripple
            await self._request(self.deps.settings.kiosk.idle_mode, source="idle", ripple=False)
        self._publish_soon()

    async def stop(self) -> None:
        """Stop the reconciler and wait for the engine's own background tasks. Call before closing executors."""
        self._stopped = True
        trial_task = self._trial_task
        # the try-on ends here (memory only: a restart has none)
        self._clear_trial("the canopy engine stopped")
        if self._rehearsal is not None:
            self._end_rehearsal("the canopy engine stopped", rehearsal=self._rehearsal, settle=False)
        if self._runner is not None:
            self._runner.cancel()
        if self._settle_task is not None:
            self._settle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._settle_task
            self._settle_task = None
        if trial_task is not None and trial_task is not _current_task():
            trial_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await trial_task
        repaint_task = self._repaint_task
        if repaint_task is not None and repaint_task is not _current_task():
            repaint_task.cancel()  # its finally clears its marks and the quiet window
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await repaint_task
        await self.reconciler.stop()
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for rehearsal in list(self._rehearsals):
            await rehearsal.wait()  # its report is written (the writes it waited for were cancelled above)
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        for callbacks in self._recovery_lists:
            with contextlib.suppress(ValueError):
                callbacks.remove(self._on_driver_recovery)
        self._recovery_lists.clear()

    close = stop  # Services.close() calls `close` on extras it knows about

    async def wait_idle(self, timeout: float | None = None) -> None:
        """Wait until every burst the engine started has its results (tests, the soak, writer-path proofs)."""

        async def drain() -> None:
            while self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)

        if timeout is None:
            await drain()
        else:
            await asyncio.wait_for(drain(), timeout)

    # ---- CanopyEngineAPI --------------------------------------------------------------------------------------
    def current_plan(self) -> ModePlan | None:
        return self._plan

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def outage(self) -> dict | None:
        return self._outage

    @property
    def quiet(self) -> bool:
        """Commissioning, or an operator repaint's quiet window (D68 PR B2): nothing else writes to the canopy."""
        return self._commissioning or self._repaint_quiet

    @property
    def commissioning(self) -> bool:
        """The PUBLIC flag the reconciler (tick, re-assert, idle hold and reset), the stepped runner, the show's
        capability re-pick and the kiosk read: `quiet`, so they all hold still during a repaint's window too. Internal
        reads of `self._commissioning` are about commissioning itself."""
        return self.quiet

    @property
    def repaint_quiet(self) -> bool:
        """An operator repaint is writing now (its quiet window)."""
        return self._repaint_quiet

    @property
    def repainting(self) -> bool:
        """An operator repaint job is queued or running (from the publish until its last scene's finally)."""
        return self._repaint_task is not None and not self._repaint_task.done()

    @property
    def repaint_pending(self) -> frozenset[tuple[str, str]]:
        """(device, slot) pairs an operator repaint will write: the reconciler never judges or rewrites them."""
        return frozenset(self._repaint_pending)

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def guest_power(self) -> bool:
        return self._guest_power

    @property
    def runner(self) -> ProgramRunner | None:
        """The stepped look's runner (None unless the plan showing is a stepped one)."""
        return self._runner

    @property
    def stepping(self) -> bool:
        """Is a stepped look moving now (its run going, its writes not held, the canopy on)?"""
        return self._runner is not None and self._runner.moving

    def program_unsent(self, device_id: str) -> bool:
        """A stepped plan whose desired rows for this module weren't sent (its run ended while nothing could be)."""
        return self._runner is not None and self.desired_rows(device_id) != self.sent_rows.get(device_id)

    def device_of(self, address: str) -> str | None:
        return self._by_address.get(address)

    @property
    def rehearsal_mode(self) -> str | None:
        """The mode the stepped rehearsal is running (None when none runs): the show opens the stepped tier on live
        hardware for that mode only, for as long as this is set."""
        return self._rehearsal.mode if self._rehearsal is not None else None

    @property
    def rehearsal_active(self) -> bool:
        return self._rehearsal is not None

    @property
    def trial(self) -> Trial | None:
        """The Design Lab try-on in force (None when none): the show compiles its mode's T3/strand from it."""
        return self._trial

    @property
    def trial_active(self) -> bool:
        return self._trial is not None

    async def request_mode(self, mode_id: str, *, source: str) -> CanopyState:
        """Guest/ops/idle mode request. Kiosk requests honour the cooldown, the pause and the resting state. A mood
        chosen (any source but a re-pick, `RECOMPILE_SOURCES`) lands as a ripple when one is configured (D64); the
        first look, with nothing showing yet, is one burst."""
        return await self._request(mode_id, source=source, ripple=source not in ONE_BURST_SOURCES)

    async def _request(self, mode_id: str, *, source: str, ripple: bool) -> CanopyState:
        now = self.clock.monotonic()
        refused = self._refusal(source, now)
        if refused is not None:
            return refused
        # A look chosen by anything but the try-on itself or a re-pick (ops, idle, the safe look's fallback) ends the
        # Design Lab's try-on before the tier is picked, so its override is gone when this mode compiles (D66).
        cleared = None
        if self._trial is not None and source not in RECOMPILE_SOURCES and source != TRIAL_SOURCE:
            cleared = self._clear_trial(f"{source} chose {mode_id}")
        rehearsal = self._rehearsal if source not in RECOMPILE_SOURCES else None
        # A look chosen (by ops) ends the stepped rehearsal before the tier is picked, so its gate is closed.
        if rehearsal is not None:
            self._end_rehearsal(f"{source} chose {mode_id}", rehearsal=rehearsal, settle=False)
        plan, why = self.compile_best(mode_id)
        if plan is None:
            if rehearsal is not None and self._runner is not None and self._runner.running:
                self._runner.finish("the stepped rehearsal ended")  # never step on without it
            self._restore_approved(cleared)  # the try-on's plan never stays without its override
            return self._refused(why, None)
        if source == "kiosk":
            self._last_kiosk_mono = now
            self.last_guest_input_mono = now
        if source not in ("reconciler", "recovery"):
            self.last_change_mono = now
            self._chosen_mono = now  # a stepped look's run counts from here (guest dimming doesn't move it)
        if source not in RECOMPILE_SOURCES:  # a mood was chosen: its designed brightness, and on (D60)
            self._guest_brightness, self._guest_power = None, True
        # The first look (start-up: `start()`, or the show's wiring once its capability reads are back) has no
        # picture to ripple from, so it is one burst whatever its source.
        self._show(plan, source, ripple=ripple and self._plan is not None)
        return self.state()

    def _refusal(self, source: str, now: float) -> CanopyState | None:
        """A refused state when a request from `source` can't be taken now, else None. Nothing goes through while
        stopped or commissioning; ops, idle and reconciler requests bypass the cooldown, the pause and resting."""
        if self._stopped:
            return self._refused(STOPPED_MESSAGE, None)
        if self._commissioning:
            return self._refused(COMMISSIONING_MESSAGE, 30.0)
        # a publish's slot repaint is writing (D68 PR B2): every request waits a moment
        if self._repaint_quiet:
            return self._refused(REPAINT_MESSAGE, self._repaint_left())
        if source == "kiosk":
            if self._paused:
                return self._refused(PAUSED_MESSAGE, None)
            if self._rehearsal is not None:  # guests wait for the stepped rehearsal (Gate R) to finish
                return self._refused(REHEARSAL_MESSAGE, self._rehearsal_left())
            if self._trial is not None:  # guests wait for the Design Lab's try-on to end (D66)
                return self._refused(TRIAL_MESSAGE, self._trial_left())
            if self._outage is not None:
                return self._refused(RESTING_MESSAGE, self.deps.settings.reconciler.outage_probe_interval_s)
            remaining = self._cooldown_remaining(now)
            if remaining > 0:
                return self._refused(
                    f"One moment: the canopy is still changing. Try again in {math.ceil(remaining)} s.",
                    round(remaining, 2),
                )
        return None

    # ---- guest brightness and power (D60) ---------------------------------------------------------------------
    async def set_guest_brightness(self, level: int, *, source: str) -> CanopyState:
        """Absolute device brightness for every canopy module, clamped to [kiosk.guest_brightness_min, 100]. Turns
        the canopy on. One write per module (the full state vector with this brightness row; never a frame)."""
        floor = self.deps.settings.kiosk.guest_brightness_min
        # True, 0.5, NaN, "30": refused, not coerced.
        if isinstance(level, bool) or not isinstance(level, int):
            return self._refused(f"Brightness must be a whole number from {floor} to 100.", None)
        refused = self._guest_refusal(source)
        if refused is not None:
            return refused
        level = max(floor, min(100, level))
        if self._guest_power and level == self._guest_brightness:
            return self.state()  # already so: nothing is sent and no cooldown starts
        self._guest_brightness, self._guest_power = level, True
        self._guest_input(source, "brightness")
        return self.state()

    async def set_guest_power(self, on: bool, *, source: str) -> CanopyState:
        """Off: one `power off` write per module. On: the current look's full state vector (with any guest
        brightness), one write per module."""
        refused = self._guest_refusal(source)
        if refused is not None:
            return refused
        if bool(on) == self._guest_power:
            return self.state()
        self._guest_power = bool(on)
        self._guest_input(source, "on" if on else "off")
        return self.state()

    async def clear_guest_overrides(self, *, source: str) -> CanopyState:
        """Ops and the idle reset: back on, at the look's designed brightness (bypasses the cooldown and the pause).
        During an outage the change is kept and goes to every module with the look on recovery."""
        if self._stopped:
            return self._refused(STOPPED_MESSAGE, None)
        if self._commissioning:
            return self._refused(COMMISSIONING_MESSAGE, 30.0)
        if self._repaint_quiet:
            return self._refused(REPAINT_MESSAGE, self._repaint_left())
        if self.guest_overrides_active:
            self._guest_brightness, self._guest_power = None, True
            self._apply_guest(source, "clear")
        return self.state()

    @property
    def guest_overrides_active(self) -> bool:
        return self._guest_brightness is not None or not self._guest_power

    def _guest_refusal(self, source: str) -> CanopyState | None:
        refused = self._refusal(source, self.clock.monotonic())
        if refused is None and self._plan is None:
            return self._refused(STARTING_MESSAGE, 5.0)  # no look yet: nothing to dim or switch
        return refused

    def _guest_input(self, source: str, action: str) -> None:
        now = self.clock.monotonic()
        if source == "kiosk":
            self._last_kiosk_mono = now  # one cooldown for every guest input, moods included
        self.last_guest_input_mono = now
        self.last_change_mono = now  # the idle hold waits for guest activity too
        self._apply_guest(source, action)

    def _apply_guest(self, source: str, action: str) -> None:
        """Send the current look with the guest's settings to every module: one burst, one write per module. While
        resting, nothing is sent; recovery re-applies the look (with these settings) to every module."""
        plan = self._plan
        self.telemetry.record(
            "guest",
            action=action,
            brightness=self._guest_brightness,
            power=self._guest_power,
            mode=plan.mode_id if plan else None,
            source=source,
        )
        if plan is not None and self._outage is None:
            # A stepped look whose next step is due within moments carries a new level itself, one PUT per module.
            # (Back on: the runner kept its step current while the canopy was off, so this sends that step.)
            runner = self._runner
            folded = runner is not None and action == "brightness" and runner.fold_guest()
            if not folded:
                self._early_counts.clear()  # a silent drop of this change gets its early re-sends too
                self.send(None, source=source, label=f"guest {action}: {plan.mode_id}")
        self._publish_soon()

    def desired_rows(self, device_id: str) -> list[StateRow]:
        """The rows a show command sends `device_id` now: the current plan's full state vector with the guest's
        settings applied. The reconciler judges readback against these, so it keeps the guest's choice."""
        plan = self._plan
        if plan is None:
            return []
        mt = plan.modules[device_id]
        step = self._runner.rows_for(device_id) if self._runner is not None else None
        return self._guest_rows(list(step if step is not None else mt.rows), mt)

    def _guest_rows(self, rows: list[StateRow], mt: ModuleTarget) -> list[StateRow]:
        if not self._guest_power:
            return [R.power(False)]
        if self._guest_brightness is None:
            return rows
        # Within the device's own range, as the compiler clamps a designed level; the brightness row is replaced in
        # place, so the row order inside the PUT is kept.
        lo, hi = mt.brightness_range or (1, 100)
        level = R.brightness(max(lo, min(hi, self._guest_brightness)))
        return [level if r.key == BRIGHTNESS_KEY else r for r in rows]

    def _desired_expect(self, device_id: str, mt: ModuleTarget, plan: ModePlan) -> list[StateRow]:
        step = self._runner.rows_for(device_id) if self._runner is not None and plan is self._plan else None
        if step is not None:  # a stepped look's step: a full state vector, read back as sent
            return self._guest_rows(list(step), mt)
        return self._guest_rows(self._expect(device_id, mt, plan), mt)

    def _desired_label(self, plan: ModePlan) -> str:
        label = f"{plan.label} ({plan.tier})"
        if not self._guest_power:
            return f"{label}, turned off from the guest panel"
        if self._guest_brightness is not None:
            return f"{label} at {self._guest_brightness}% from the guest panel"
        return label

    def _brightness_now(self, plan: ModePlan | None) -> int | None:
        if plan is None:
            return None
        if self._guest_brightness is not None:
            return self._guest_brightness
        levels = [
            r.value
            for mt in plan.modules.values()
            for r in mt.rows
            if r.key == BRIGHTNESS_KEY and isinstance(r.value, int)
        ]
        return max(levels) if levels else None

    async def reassert_all(self, *, source: str) -> CanopyState:
        """Send the full state vector to every non-locked module, now (one burst). During an outage, probe instead:
        the first good readback re-applies the look to every module."""
        if self._commissioning:
            return self._refused(COMMISSIONING_MESSAGE, 30.0)
        if self._repaint_quiet:  # a publish's slot repaint is writing (D68 PR B2): nothing else writes
            return self._refused(REPAINT_MESSAGE, self._repaint_left())
        if self._outage is not None:
            await self.reconciler.probe()
            return self.state()
        if self._plan is not None:
            self.send(None, source=source, label=f"re-assert all: {self._plan.mode_id}")
        return self.state()

    def pause_guest_input(self, paused: bool, *, source: str) -> CanopyState:
        if paused:  # a pause ends the Design Lab's try-on: the approved look is back (D66)
            self._clear_trial("guest input was paused", repick=True)
        if paused and self._rehearsal is not None:  # a pause would hold every step: the rehearsal ends here
            self._stop_rehearsal(f"guest input was paused ({source}), which holds every step")
        if paused != self._paused:
            self._paused = paused
            self._paused_since = self.clock.now() if paused else None
            self._save_pause(source)
            self.telemetry.record(
                "alert",
                level="info",
                message=f"Guest input {'paused' if paused else 'resumed'} ({source}).",
                source=source,
            )
        self._publish_soon()
        return self.state()

    async def safe_look(self, *, source: str) -> CanopyState:
        """Lowcountry Calm at its strand technique, every module, now (bypasses cooldown, pause and floors). Always
        on, at its designed brightness: it clears the guest's settings (D60)."""
        # First: with a Lowcountry Calm try-on still set, the show would compile the draft's solid as the safe look.
        cleared = self._clear_trial("the safe look")
        try:
            plan = self.deps.compile(SAFE_MODE, Tier.STRAND)
        except (PlanError, KeyError, LookupError) as e:
            plan, why = self.compile_best(SAFE_MODE)
            if plan is None:
                self._restore_approved(cleared)
                return self._refused(f"The safe look can't be compiled: {e}; {why}", None)
        self.last_change_mono = self._chosen_mono = self.clock.monotonic()
        self._guest_brightness, self._guest_power = None, True
        self._show(plan, source)
        return self.state()

    async def commission(self, *, source: str) -> dict:
        """Write each T1 mode's slot content to every module and record the readback hashes (see module doc)."""
        if self._commissioning:
            return {"ok": False, "message": "Commissioning is already running.", "modes": {}, "skipped": []}
        if self._outage is not None:
            return {"ok": False, "message": RESTING_MESSAGE, "modes": {}, "skipped": []}
        if self.repainting:  # a publish's slot repaint (D68 PR B2) is queued or writing: never both at once
            left = self.repaint_left_total()
            slot = (self.repaint_status or {}).get("slot") or "a slot"
            return {
                "ok": False,
                "message": f"A publish is repainting {slot} (about {math.ceil(left)} s left); commission once it "
                "ends.",
                "modes": {},
                "skipped": [],
                "retry_after_s": round(max(1.0, left), 2),
            }
        # The try-on ends; its scene's approved look goes back without T1/T2/stepped: the show's wrapper has already
        # set its commissioning flag, under which T1 passes without slot records. _replan_soon lifts it afterwards.
        self._clear_trial("commissioning started", repick=True, skip=TRIAL_SKIP)
        report: dict[str, Any] = {
            "ok": True,
            "simulated": self._simulated(),
            "started_at": self.clock.now(),
            "modes": {},
            "skipped": [],
        }
        self._commissioning = True
        self._t1_plans.clear()
        self._t1_failed.clear()
        self._publish_soon()
        try:
            # Let show commands already on their way finish, so slot writes don't supersede them (or vice versa).
            with contextlib.suppress(TimeoutError):
                await self.wait_idle(timeout=30.0)
            for mode_id in self.deps.modes():
                try:
                    ladder = list(self.deps.ladder(mode_id))
                except (KeyError, LookupError, PlanError) as e:
                    report["skipped"].append({"mode": mode_id, "reason": str(e)})
                    continue
                if Tier.T1 not in ladder:
                    continue
                allowed, why = self.deps.tier_allowed(mode_id, Tier.T1)
                if not allowed:
                    report["skipped"].append({"mode": mode_id, "reason": why or "canvas tier not allowed"})
                    continue
                try:
                    plan = self.deps.compile(mode_id, Tier.T1)
                except PlanError as e:
                    report["skipped"].append({"mode": mode_id, "reason": str(e)})
                    continue
                self._t1_plans[mode_id] = plan
                report["modes"][mode_id] = await self._commission_plan(plan, source)
        finally:
            self._commissioning = False
        statuses = [m["status"] for mode in report["modes"].values() for m in mode["modules"].values()]
        written = statuses.count("applied")
        problems = [s for s in statuses if s not in ("applied", "skipped", "locked")]
        report["ok"] = not problems
        report["finished_at"] = self.clock.now()
        report["message"] = (
            f"Wrote {written} slot(s)"
            + (f"; {len(problems)} need attention" if problems else "")
            + (
                f"; {statuses.count('skipped')} skipped (recently written, hand-painted in the app, or no slot "
                "content — each module says why)"
                if "skipped" in statuses
                else ""
            )
            + self._left_out_line(report["modes"])
            + "."
        )
        self.telemetry.record(
            "alert", level="info" if not problems else "warn", message=f"Commissioning: {report['message']}"
        )
        # The slots' records changed: re-pick the current mode's tier (commissioning can unlock T1) and its copy.
        self._replan_soon()
        self._publish_soon()
        return report

    def _left_out_line(self, commissioned: dict[str, Any]) -> str:
        """The commission report's account of the modules its plans left out (D62): nothing was written there."""
        left_out: list[str] = []
        for mode_id in commissioned:
            plan = self._t1_plans.get(mode_id)
            left_out += [d for d in (plan.skipped if plan is not None else {}) if d not in left_out]
        unregistered, unread = [], []
        for device_id in left_out:
            try:
                self.deps.target(device_id, Purpose.ENGINE)
            except (LookupError, KeyError):
                unregistered.append(device_id)  # no address on the engine's driver (services.target)
            else:
                unread.append(device_id)  # registered, but its profile hasn't been read (D36)

        def names(ids: list[str]) -> str:
            return ", ".join(((e.label if (e := self._entry(d)) else "") or d) for d in ids)

        line = ""
        if unregistered:
            verb = "has" if len(unregistered) == 1 else "have"
            line += (
                f"; {names(unregistered)} {verb} no Hubspace device registered, so nothing was written there"
            )
        if unread:
            verb = "has" if len(unread) == 1 else "have"
            line += f"; {names(unread)} {verb} no profile read yet, so nothing was written there"
        return line

    # ---- state ------------------------------------------------------------------------------------------------
    def state(self) -> CanopyState:
        plan = self._plan
        modules: dict[str, dict[str, Any]] = {}
        simulated = self._simulated()
        active_statuses: list[tuple[str, DeviceStatus]] = []
        unresolved = []
        locked_count = 0
        skipped = plan.skipped if plan is not None else {}
        runner = self._runner if self.stepping else None
        for device_id in self._module_ids():
            entry = self._entry(device_id)
            record: dict[str, Any] = {
                "label": (entry.label if entry else "") or device_id,
                "module": entry.module if entry else None,
                "locked": False,
                "simulated": simulated,
                # presentation only (the kiosk preview): left out of the look; driven but wrong past the window
                "left_out": device_id in skipped,
                "stuck": False,
            }
            target = self._targets.get(device_id)
            if device_id in skipped:  # left out of the look (e.g. its profile hasn't been read yet)
                record.update(status=str(DeviceStatus.NEEDS_ATTENTION), reason=skipped[device_id] + ".")
                unresolved.append(device_id)
                modules[device_id] = record
                continue
            if target is None:
                record.update(
                    status=str(DeviceStatus.NEEDS_ATTENTION),
                    reason=self._unresolved.get(device_id, "No address for this module yet."),
                )
                unresolved.append(device_id)
                modules[device_id] = record
                continue
            record["simulated"] = bool(target.simulated)
            holder = self.locks.holder(target.address)
            if holder is not None:
                locked_count += 1
                record.update(
                    status="locked",
                    locked=True,
                    reason=f"In use by {holder.owner}; the show engine leaves it alone until it's released.",
                )
            elif device_id in self._pending:
                # its ring hasn't been sent yet (D64): the look is still on its way to it
                record.update(status=str(DeviceStatus.UPDATING), reason="Waiting for its turn in the ripple.")
                active_statuses.append((device_id, DeviceStatus.UPDATING))
            elif runner is not None and plan is not None and device_id in plan.modules:
                # a stepped look in motion: judged by whether the writes sent to it verify, not by the step in flight
                status = runner.status(device_id)
                record.update(
                    status=str(status),
                    reason=runner.status_reason(device_id, status),
                    stuck=self._stuck(device_id, status),
                )
                active_statuses.append((device_id, status))
            else:
                status = self.tracker.status(target.address)
                reason = (
                    "Readback matches the look."
                    if status is DeviceStatus.READY
                    else self.tracker.snapshot(target.address)["reason"]
                )
                record.update(status=str(status), reason=reason, stuck=self._stuck(device_id, status))
                active_statuses.append((device_id, status))
            modules[device_id] = record

        phase, message = self._phase(plan, modules, active_statuses, unresolved, locked_count)
        # The guest's phase judges the look over the modules it drives: the ones the plan left out keep /ops at
        # needs attention (D62) but never hide a change in flight or a finished look from the kiosk.
        driven_unresolved = [d for d in unresolved if d not in skipped]
        look_phase, _ = self._phase(plan, modules, active_statuses, driven_unresolved, locked_count)
        remaining = self._cooldown_remaining(self.clock.monotonic())
        return CanopyState(
            mode=plan.mode_id if plan else None,
            label=plan.label if plan else None,
            tier=str(plan.tier) if plan else None,
            # while a stepped look moves, the sentence that says so; the still design's before and after
            feature=(runner.program.moving_feature if runner is not None else plan.feature) if plan else None,
            phase=phase,
            guest_input=not self._paused,
            paused_since=self._paused_since if self._paused else None,
            cooldown_until=(self.clock.now() + remaining) if remaining > 0 else None,
            changed_at=self._changed_at,
            modules=modules,
            simulated=simulated,
            message=message,
            look_phase=look_phase,
            guest_brightness=self._guest_brightness,
            guest_power=self._guest_power,
            brightness=self._brightness_now(plan),
            guest_brightness_min=self.deps.settings.kiosk.guest_brightness_min,
            ripple=self._ripple_json(),
            program=runner.to_state() if runner is not None else None,
        )

    def _ripple_json(self) -> dict | None:
        """The ripple in progress as positions: {t0 (wall), stagger_s, rings: [[position]]}, ring j sent at
        t0 + j × stagger_s. None when no ring is left to send."""
        ripple = self._ripple
        if ripple is None:
            return None

        def position(device_id: str) -> str:
            entry = self._entry(device_id)
            return str(entry.module) if entry is not None and entry.module else device_id

        return {
            "t0": ripple["t0"],
            "stagger_s": ripple["stagger_s"],
            "rings": [[position(d) for d in ring] for ring in ripple["rings"]],
        }

    def _phase(
        self,
        plan: ModePlan | None,
        modules: dict[str, dict],
        active: list[tuple[str, DeviceStatus]],
        unresolved: list[str],
        locked_count: int,
    ) -> tuple[CanopyPhase, str]:
        if self._stopped:
            return CanopyPhase.RESTING, STOPPED_MESSAGE
        if self._outage is not None:
            return CanopyPhase.RESTING, RESTING_MESSAGE
        if self._paused:
            return CanopyPhase.RESTING, PAUSED_MESSAGE
        if plan is None:
            return CanopyPhase.RESTING, STARTING_MESSAGE
        if self._commissioning:
            return CanopyPhase.UPDATING, "Writing the saved patterns to every registered strand…"
        if self._repaint_quiet:
            return CanopyPhase.UPDATING, REPAINT_MESSAGE
        stuck = [d for d, s in active if self._stuck(d, s)]
        if stuck or unresolved:
            names = [modules[d]["label"] for d in [*stuck, *unresolved]]
            first = modules[(stuck or unresolved)[0]]["reason"]
            return (
                CanopyPhase.NEEDS_ATTENTION,
                f"{len(names)} module(s) need attention ({', '.join(names)}): {first}",
            )
        if not active and locked_count:
            return CanopyPhase.NEEDS_ATTENTION, "Every module is in use on a test page."
        if any(s is not DeviceStatus.READY for _, s in active):
            return CanopyPhase.UPDATING, "Changing the light…"
        suffix = f" ({locked_count} module(s) in use on a test page)" if locked_count else ""
        return CanopyPhase.READY, "Ready" + suffix

    def _stuck(self, device_id: str, status: DeviceStatus) -> bool:
        """A driven module that has needed attention longer than `reconciler.downgrade_after_wrong_s` — or, while a
        stepped look moves, one whose write has gone unverified for max(2 steps, 10 s) (`ProgramRunner.status`)."""
        if self.stepping:
            return status is DeviceStatus.NEEDS_ATTENTION
        return status is DeviceStatus.NEEDS_ATTENTION and self.reconciler.wrong_too_long(
            self._targets[device_id].address
        )

    def _refused(self, message: str, retry_after_s: float | None) -> CanopyState:
        state = self.state()
        state.refused = True
        state.message = message
        state.retry_after_s = retry_after_s
        return state

    def _cooldown_remaining(self, now: float) -> float:
        if self._last_kiosk_mono is None:
            return 0.0
        return max(0.0, self._last_kiosk_mono + self.deps.settings.kiosk.cooldown_s - now)

    # ---- the stepped rehearsal (Gate R, conductor/rehearsal.py) ----------------------------------------------------
    def _rehearsal_plan(self, mode_id: str) -> ModePlan:
        """The stepped plan a rehearsal of `mode_id` runs (not installed). RehearsalRefused when there's none."""
        try:
            if self.deps.rehearsal_compile is not None:
                plan = self.deps.rehearsal_compile(mode_id)
            else:
                plan = self.deps.compile(mode_id, Tier.STEPPED)
        except (PlanError, KeyError, LookupError, ValueError) as e:
            raise RehearsalRefused(f"{mode_id} can't be rehearsed stepped: {e}") from None
        if plan.program is None or plan.tier is not Tier.STEPPED or not plan.modules:
            raise RehearsalRefused(
                f"{plan.label} has no stepped design to rehearse on the modules it can drive."
            )
        return plan

    def _rehearsal_checks(self, mode_id: str, minutes: int) -> None:
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= MAX_MINUTES:
            raise RehearsalRefused(f"A stepped rehearsal runs 1 to {MAX_MINUTES} whole minutes.")
        if self._stopped:
            raise RehearsalRefused(STOPPED_MESSAGE)
        if self._rehearsal is not None:
            raise RehearsalRefused(
                f"A stepped rehearsal of {self._rehearsal.label} is already running; stop it first."
            )
        if self._commissioning:
            raise RehearsalRefused(
                "Commissioning is writing the saved patterns; rehearse once it has finished."
            )
        if self._repaint_quiet:
            raise RehearsalRefused(
                f"A publish is repainting a saved pattern (about {math.ceil(self._repaint_left() or 1)} s left); "
                "rehearse once it has finished."
            )
        if self._outage is not None:
            raise RehearsalRefused(
                "Hubspace isn't answering (the canopy is resting), so there's no rate to measure; rehearse once "
                "it answers again."
            )
        if self._paused:
            raise RehearsalRefused(
                "Guest input is paused, which holds every step. Resume guest input first: the rehearsal keeps the "
                "kiosk out by itself while it runs."
            )
        if mode_id == self.deps.settings.kiosk.idle_mode:
            raise RehearsalRefused(
                f"{mode_id} is the idle mode, which never steps (D65); rehearse one of the other looks."
            )

    def rehearsal_preview(self, mode_id: str, minutes: int) -> dict:
        """What a rehearsal of `mode_id` for `minutes` would do, and the sentence ops confirms before it starts (it
        names every module written to). RehearsalRefused (a plain sentence) when it can't run now."""
        self._rehearsal_checks(mode_id, minutes)
        plan = self._rehearsal_plan(mode_id)
        modules, accounts = {}, set()
        for device_id in plan.modules:
            entry = self._entry(device_id)
            modules[device_id] = (entry.label if entry else "") or device_id
            target = self._targets.get(device_id) or self._resolve(device_id)
            if target is not None:
                accounts.add(self._account_of(target.address))
        accounts_sorted = sorted(accounts)
        mono = self.clock.monotonic()
        cooling = sorted(a for a in accounts_sorted if self.stepped_cooloff.get(a, float("-inf")) > mono)
        if cooling:
            left = max(self.stepped_cooloff[a] for a in cooling) - mono
            raise RehearsalRefused(
                f"Hubspace throttled (429/403) a stepped run on account {', '.join(cooling)}; stepped looks there "
                f"hold still for another {math.ceil(left)} s. Rehearse after that."
            )
        simulated = self._simulated()
        stepped = self.deps.settings.motion.stepped
        return {
            "mode": mode_id,
            "label": plan.label,
            "minutes": minutes,
            "modules": list(modules.values()),
            "module_ids": list(modules),
            "accounts": accounts_sorted,
            "simulated": simulated,
            "confirm": confirm_sentence(
                label=plan.label,
                minutes=minutes,
                modules=list(modules.values()),
                accounts=accounts_sorted,
                simulated=simulated,
                cap=stepped.account_writes_per_min,
                planned=planned_writes_per_min(stepped),
            ),
        }

    async def rehearse(self, mode_id: str, minutes: int, *, source: str) -> dict:
        """Start the stepped rehearsal: one stepped run of `mode_id` lasting `minutes`, then the still look. Only an
        operator starts it (the ops route, behind `rehearsal_preview`'s confirm sentence). Returns
        {ok, message, rehearsal}; RehearsalRefused (a plain sentence) when it can't start."""
        preview = self.rehearsal_preview(mode_id, minutes)
        # Before the rehearsal is set: with a try-on still set, compile_best would skip STEPPED for its scene (D66).
        cleared = self._clear_trial("a stepped rehearsal started")
        rehearsal = Rehearsal(
            self,
            mode=mode_id,
            label=preview["label"],
            minutes=minutes,
            modules=dict(zip(preview["module_ids"], preview["modules"], strict=True)),
            accounts=preview["accounts"],
            source=source,
            report_dir=self.deps.rehearsal_dir,
        )
        # A hysteresis floor indexes the mode's own ladder; the rehearsal's has the stepped rung in front of it.
        rehearsal.saved_floor = self.floors.pop(mode_id, None)
        self._rehearsal = rehearsal  # from here the show opens the stepped tier for this mode
        plan, why = self.compile_best(mode_id, floor=0)
        if plan is None or plan.program is None:
            self._rehearsal = None
            self._restore_floor(rehearsal)
            self._restore_approved(cleared)
            raise RehearsalRefused(
                why
                or f"{preview['label']} would show {plan.tier if plan else 'nothing'}, not a stepped look."
            )
        rehearsal.start()
        now = self.clock.monotonic()
        self.last_change_mono = self._chosen_mono = now  # its run counts from here
        self._guest_brightness, self._guest_power = None, True  # a mood chosen: designed brightness, on (D60)
        self._show(plan, source)  # one burst: the hold (step 0); never a ripple
        if self._rehearsal is not rehearsal:  # it couldn't step: ended at once, the still look holds
            return {
                "ok": False,
                "message": f"The rehearsal didn't start: {rehearsal.ended}.",
                "rehearsal": None,
            }
        runner = self._runner
        assert runner is not None
        return {
            "ok": True,
            "message": (
                f"Stepped rehearsal of {rehearsal.label} started: {runner.count} steps of {runner.step_s:g} s on "
                f"{len(rehearsal.modules)} modules. The report is written when it ends."
            ),
            "rehearsal": rehearsal.status(),
        }

    def stop_rehearsal(self, *, source: str) -> dict:
        """Ops stops the rehearsal now: the still look is sent, and the report says it was stopped."""
        rehearsal = self._rehearsal
        if rehearsal is None:
            raise RehearsalRefused("No stepped rehearsal is running.")
        self._stop_rehearsal(f"stopped by {source}")
        return {
            "ok": True,
            "message": f"Stepped rehearsal of {rehearsal.label} stopped; the still look holds.",
        }

    def _stop_rehearsal(self, why: str, *, alert: str | None = None) -> None:
        rehearsal = self._rehearsal
        if rehearsal is None:
            return
        runner = self._runner
        if runner is not None and runner.running and runner is rehearsal.runner:
            runner.finish(why, alert=alert)  # the hold goes out; its "program end" event ends the rehearsal
            if self._rehearsal is rehearsal:
                self._end_rehearsal(why, rehearsal=rehearsal)
        else:
            if alert:
                self.telemetry.record("alert", level="warn", message=alert, source="rehearsal")
            self._end_rehearsal(why, rehearsal=rehearsal)

    def _rehearsal_403(self, rehearsal: Rehearsal, account: str) -> None:
        """The first 403 anywhere: Hubspace's throttling signature (the venue IP may be blocked), so the rehearsal
        stops and nothing more goes out for it: no hold burst (the canopy keeps the step it was sent, a still look
        of one colour per module, installed as the current plan so the kiosk draws what is there), no re-run and no
        stepped look on that account for `governor_cooloff_s`, and the mode's still look only after that cool-off
        (`_settle`, sent where the rows differ). Never retried around."""
        if self._rehearsal is not rehearsal:
            return
        cooloff = self.deps.settings.motion.stepped.governor_cooloff_s
        now = self.clock.monotonic()
        self.stepped_cooloff[account] = max(self.stepped_cooloff.get(account, now), now + cooloff)
        why = f"Hubspace answered 403 on account {account}"
        alert = (
            f"Stepped rehearsal stopped: Hubspace answered 403 (forbidden) on account {account}. Nothing more was "
            f"sent for it: the canopy holds the step it was on, and the look's still design goes out after "
            f"{cooloff / 60:g} min. Stepped motion stays off; check the accounts on /ops/accounts "
            "before running it again."
        )
        runner = self._runner
        if runner is not None and runner.running and runner is rehearsal.runner:
            runner.finish(why, alert=alert, send=False)
        else:
            self.telemetry.record("alert", level="warn", message=alert, source="rehearsal")
        self._end_rehearsal(why, rehearsal=rehearsal, settle=False)
        held = runner.held_plan() if runner is not None and runner is self._runner else None
        if held is not None:
            self._install(held, "reconciler")
        self._settle_later(rehearsal.mode, self._plan, cooloff)
        self._publish_soon()

    def _settle_later(self, mode_id: str, plan: ModePlan | None, delay: float) -> None:
        """`_settle` after `delay` (a 403's cool-off), while `plan` is still the one showing. Not one of the engine's
        burst tasks (`wait_idle` doesn't wait for it); `stop()` cancels it."""
        if self._settle_task is not None:
            self._settle_task.cancel()

        async def later() -> None:
            await self.clock.sleep(delay)
            self._settle_task = None
            self._settle(mode_id, plan)

        self._settle_task = asyncio.get_running_loop().create_task(later(), name=f"canopy:settle:{mode_id}")

    def _end_rehearsal(self, why: str, *, rehearsal: Rehearsal, settle: bool = True) -> None:
        """The rehearsal is over: the gate closes, guests are back, the report is written once the writes in flight
        have their results. With `settle` the mode's still look stays, at its usual tier (`_settle`)."""
        if self._rehearsal is not rehearsal:
            return
        self._rehearsal = None
        self._restore_floor(rehearsal)
        # Whatever shows next holds for kiosk.idle_return_s from here, not from the rehearsal's start.
        self.last_change_mono = self.clock.monotonic()
        plan = self._plan
        self._rehearsals.append(rehearsal)
        rehearsal.end(
            why,
            waiting=list(self._tasks),
            then=(lambda: self._settle(rehearsal.mode, plan)) if settle else None,
        )
        self._publish_soon()

    def _restore_floor(self, rehearsal: Rehearsal) -> None:
        self.floors.pop(rehearsal.mode, None)  # a floor set during it indexes the rehearsal's ladder
        if rehearsal.saved_floor is not None:
            self.floors[rehearsal.mode] = rehearsal.saved_floor

    def _settle(self, mode_id: str, plan: ModePlan | None) -> None:
        """After a rehearsal (once its report is written): while its plan is still the one showing, the mode at the
        best tier of its own ladder that isn't stepped (`compile_best`: from its floor, allowed, compiling), sent
        only to the modules whose rows differ from what was last sent (the run's hold is the module-level design
        already, so on hardware at T3 nothing goes out)."""
        if plan is None or self._plan is not plan or self._stopped:
            return
        still, _ = self.compile_best(mode_id, skip=frozenset({Tier.STEPPED}))  # its floor, if any, holds
        if still is None:
            return  # the run's own hold stays
        self._install(still, "reconciler")
        if self._outage is None and not self.quiet:
            changed = [d for d in still.modules if self.desired_rows(d) != self.sent_rows.get(d)]
            if changed:
                self.send(changed, source="reconciler", label=f"after the stepped rehearsal: {mode_id}")
        self._publish_soon()

    def _rehearsal_left(self) -> float | None:
        runner = self._runner
        if runner is None or not runner.running:
            return None
        return round(max(0.0, runner.t0 + runner.count * runner.step_s - self.clock.monotonic()), 2)

    def _rehearsal_reported(self, rehearsal: Rehearsal) -> None:
        """Called by the rehearsal once its report is written."""
        with contextlib.suppress(ValueError):
            self._rehearsals.remove(rehearsal)
        self.last_rehearsal = rehearsal.summary()
        self._publish_soon()

    def rehearsal_status(self) -> dict:
        """/ops: the rehearsal running now (or None) and the last one's summary (verdict, report file)."""
        return {
            "running": self._rehearsal.status() if self._rehearsal is not None else None,
            "last": self.last_rehearsal,
            "max_minutes": MAX_MINUTES,
        }

    # ---- the Design Lab's try-on (D66 Slice B; conductor/design_lab/trial.py, docs/design-lab/PLAN.md §6) -----------
    def _trial_refusal(self) -> CanopyState | None:
        if self._stopped:
            return self._refused(STOPPED_MESSAGE, None)
        if self._commissioning:
            return self._refused(COMMISSIONING_MESSAGE, 30.0)
        if self._repaint_quiet:
            return self._refused(REPAINT_MESSAGE, self._repaint_left())
        if self._paused:
            return self._refused(TRIAL_PAUSED_MESSAGE, None)
        if self._outage is not None:
            return self._refused(RESTING_MESSAGE, self.deps.settings.reconciler.outage_probe_interval_s)
        if self._rehearsal is not None:
            return self._refused(TRIAL_REHEARSAL_MESSAGE, self._rehearsal_left())
        return None

    async def start_trial(
        self, trial: Trial, *, expect_session: Any = _ANY_SESSION, min_left_s: float = 0.0
    ) -> CanopyState:
        """Show `trial`'s draft of its scene now (source "lab", one burst, module level or strand only), until its
        bound. A re-apply while one runs keeps that session's bound, its start and its bound task. Refused (a plain
        sentence, nothing sent) while stopped, commissioning, paused, resting or rehearsing; a re-apply that is
        refused leaves the running try-on as it was.

        `expect_session`: the session the caller's confirm described (None: no try-on running); refused
        (`TRIAL_CHANGED_MESSAGE`) when another runs now. `min_left_s`: a re-apply with less of the bound left is
        refused (`TRIAL_ENDING_MESSAGE`, retry after the time left). Any exception once the override is set ends the
        try-on (override and bound task cleared first, the approved look put back, an alert) and is re-raised."""
        refused = self._trial_refusal()
        if refused is not None:
            return refused
        previous = self._trial
        if expect_session is not _ANY_SESSION:
            running = previous.session if previous is not None else None
            if running != expect_session:
                return self._refused(TRIAL_CHANGED_MESSAGE, None)
        if previous is not None and min_left_s > 0:
            left = self._trial_left() or 0.0
            if left < min_left_s:
                return self._refused(TRIAL_ENDING_MESSAGE.format(left=math.ceil(left)), max(left, 0.01))
        if previous is not None:  # re-applies never extend the bound
            trial = replace(
                trial,
                started_mono=previous.started_mono,
                started_at=previous.started_at,
                ends_mono=previous.ends_mono,
                applies=previous.applies + 1,
                session=previous.session,
            )
        else:
            self._trial_sessions += 1
            trial = replace(trial, applies=1, session=self._trial_sessions)
        before = self._plan
        self._trial = trial
        try:
            st = await self._request(trial.mode_id, source=TRIAL_SOURCE, ripple=False)
        except BaseException as exc:
            # Never leave the override (no bound ends it) or a try-on plan up: a re-apply's running plan is the
            # previous draft, so it goes too; a first start that installed nothing leaves the approved look alone.
            cleared = self._clear_trial(f"it failed: {type(exc).__name__}")
            self._recover_from_trial_error(
                cleared, exc, restore=previous is not None or self._plan is not before, previous=previous
            )
            raise
        if st.refused:
            if previous is not None:
                # the running try-on stays as it was (nothing of this one was installed)
                self._trial = previous
            else:
                cleared = self._clear_trial(f"it couldn't start: {st.message}")
                # A refused request installs nothing, so the approved look is still up; should that ever change,
                # never leave a trial plan up without its override.
                if self._plan is not before:
                    self._restore_approved(cleared)
            return st
        if previous is None:
            self._arm_trial_bound(trial)
        plan = self._plan
        self.telemetry.record(
            "lab",
            action="trial_start" if previous is None else "trial_apply",
            scene=trial.mode_id,
            draft=trial.label,
            version=trial.version,
            modules=list(plan.modules) if plan is not None else [],
            tier=str(plan.tier) if plan is not None else None,
            bound_s=round(trial.ends_mono - trial.started_mono, 3),
            applies=trial.applies,
            source="engine",
        )
        return st

    def _arm_trial_bound(self, trial: Trial) -> None:
        session = trial.session

        async def bound() -> None:
            await self.clock.sleep(max(0.0, trial.ends_mono - self.clock.monotonic()))
            await self._end_trial_at_bound(session)

        self._trial_task = asyncio.get_running_loop().create_task(
            bound(), name=f"canopy:trial:{trial.mode_id}"
        )

    async def end_trial(self, *, source: str, why: str | None = None) -> CanopyState:
        """Back to approved: the override is cleared and the scene's approved look goes out (source "lab", one
        burst; it counts as a change for the idle hold). Nothing to do when no try-on runs."""
        trial = self._clear_trial(why or f"stopped by {source}")  # the override and the bound task go first
        if trial is None:
            return self.state()
        try:
            st = await self._request(trial.mode_id, source=TRIAL_SOURCE, ripple=False)
        except BaseException as exc:
            self._recover_from_trial_error(trial, exc, restore=True)
            raise
        if st.refused:
            self._restore_approved(trial)
        return st

    async def _end_trial_at_bound(self, session: int) -> None:
        """The try-on's bound: ends it only if it is still the same session (a stale task does nothing)."""
        trial = self._trial
        if trial is None or trial.session != session:
            return
        bound = trial.ends_mono - trial.started_mono
        try:
            await self.end_trial(
                source="the try-on's time limit", why=f"it reached its time limit ({bound:g} s)"
            )
        except Exception:  # already alerted and recovered by end_trial: the task itself ends cleanly
            log.exception("The try-on of %s failed at its time limit", trial.label)

    def _clear_trial(
        self, why: str, *, repick: bool = False, skip: frozenset[Tier] = frozenset()
    ) -> Trial | None:
        """End the override now (before the caller compiles its own look) and return the try-on it cleared (None if
        none). The bound task is cancelled unless it is the caller. With `repick`, the approved look goes back."""
        trial = self._trial
        if trial is None:
            return None
        task, self._trial_task = self._trial_task, None
        if task is not None and task is not _current_task():
            task.cancel()
        self._trial = None
        self.telemetry.record(
            "lab", action="trial_end", scene=trial.mode_id, why=why, applies=trial.applies, source="engine"
        )
        self._publish_soon()
        if repick:
            self._restore_approved(trial, skip=skip)
        return trial

    def _restore_approved(self, trial: Trial | None, *, skip: frozenset[Tier] = frozenset()) -> None:
        """The transactional fallback: a caller that cleared a try-on and then couldn't install its own look puts the
        scene's approved look back (one burst), so a trial plan never stays installed with no override and no bound
        (the reconciler re-asserts the current plan forever, and the idle hold skips the idle mode)."""
        plan = self._plan
        if trial is None or self._stopped or plan is None or plan.mode_id != trial.mode_id:
            return
        approved, why = self.compile_best(trial.mode_id, skip=skip)
        if approved is None:
            self.telemetry.record(
                "alert",
                level="warn",
                message=f"The try-on of {trial.label} ended but its approved look can't be shown: {why}",
            )
            return
        self._show(approved, "reconciler")

    def _recover_from_trial_error(
        self, trial: Trial | None, exc: BaseException, *, restore: bool, previous: Trial | None = None
    ) -> None:
        """An exception (not a refusal: a compiler or context bug) while a try-on started or ended. The override is
        already cleared; with `restore`, the approved look of the scene whose try-on plan is up (`trial`'s, or a
        re-apply's `previous` scene's) goes back, or, should that raise too, the safe look, so a try-on plan never
        stays installed with no override. Never raises; always alerts."""
        if trial is None:
            return
        log.error("The try-on of %s failed: %s: %s", trial.label, type(exc).__name__, exc)
        what = f"The try-on of {trial.label} failed ({type(exc).__name__}: {exc}) and was ended"
        plan = self._plan
        up = next(
            (
                t
                for t in (trial, previous)
                if t is not None and plan is not None and plan.mode_id == t.mode_id
            ),
            None,
        )
        if restore and up is not None and not self._stopped:
            try:
                self._restore_approved(up)
                what += "; its scene's approved look was put back"
            except Exception as again:  # the approved look's compile is what's broken: the safe look
                log.exception("The approved look of %s couldn't be put back", up.mode_id)
                what += self._safe_look_after_error(again)
        self.telemetry.record("alert", level="warn", message=f"{what}.")

    def _safe_look_after_error(self, exc: BaseException) -> str:
        try:
            safe = self.deps.compile(SAFE_MODE, Tier.STRAND)
        except Exception as e:
            log.exception("The safe look couldn't be compiled after a failed try-on")
            return (
                f"; neither its approved look ({type(exc).__name__}: {exc}) nor the safe look "
                f"({type(e).__name__}: {e}) could be compiled"
            )
        self._show(safe, "reconciler")
        return (
            f"; its approved look couldn't be compiled ({type(exc).__name__}: {exc}), so the safe look is on"
        )

    def _trial_left(self) -> float | None:
        trial = self._trial
        if trial is None:
            return None
        return round(max(0.0, trial.ends_mono - self.clock.monotonic()), 2)

    # ---- the live looks: a publish from the Design Lab (D68, docs/live-lab/PLAN.md §2.5) ---------------------------
    @property
    def t1_hold(self) -> frozenset[str]:
        """The painted modes shown below T1 until their slots hold the live design."""
        return frozenset(self._t1_hold)

    def looks_refusal(self) -> tuple[str, float | None] | None:
        """Why a publish can't be installed now — (code, retry_after_s) — or None. Guest input paused is NOT a
        refusal (staff own the canopy then); a try-on isn't either (the publish ends it)."""
        if self._stopped:
            return "stopped", None
        if self._commissioning:
            return "commissioning", 30.0
        if self._outage is not None:
            return "outage", float(self.deps.settings.reconciler.outage_probe_interval_s)
        if self._rehearsal is not None:
            return "rehearsal", self._rehearsal_left()
        # B2: only while a repaint WRITES (a queued one is replaced by the new publish)
        if self._repaint_quiet:
            return "repainting", self._repaint_left()
        return None

    def t1_hold_for(self, modes: Iterable[str]) -> set[str]:
        """The modes of `modes` to hold off T1: a slot, T1 on the ladder and allowed (every position commissioned),
        and some module's commissioned (api) record differing from what the live looks compile to there, or (B2) some
        module's slot pending an operator repaint. Compiled through the show (the live trio), never the cache. A mode
        whose T1 doesn't compile isn't held (it isn't picked anyway)."""
        held: set[str] = set()
        for mode_id in modes:
            try:
                if Tier.T1 not in list(self.deps.ladder(mode_id)):
                    continue
                if not self.deps.tier_allowed(mode_id, Tier.T1)[0]:
                    continue
                plan = self.deps.compile(mode_id, Tier.T1)
            except (PlanError, KeyError, LookupError, ValueError):
                continue
            for device_id, mt in plan.modules.items():
                if not mt.slot or not mt.slot_hash:
                    continue
                if (device_id, mt.slot) in self._repaint_pending:  # its repaint hasn't finished yet
                    held.add(mode_id)
                    break
                record = self.recorded_slot(device_id, mt.slot)
                if record is not None and record["origin"] == "api" and record["hash"] != mt.slot_hash:
                    held.add(mode_id)
                    break
        return held

    def seed_t1_hold(self) -> set[str]:
        """Add every painted mode whose slots don't hold the live design (a restart after a publish or a fallback,
        a module read for the first time). Only adds: a hold lifts through the slot records."""
        try:
            modes = list(self.deps.modes())
        except Exception:  # noqa: BLE001 - nothing to seed
            return set(self._t1_hold)
        found = self.t1_hold_for(modes)
        if found - self._t1_hold:
            log.info("Painted modes held below T1 until their slots hold the live design: %s", sorted(found))
        self._t1_hold |= found
        return set(self._t1_hold)

    def designed_brightness(self) -> int | None:
        """The current look's designed level (the brightest module's), whatever a guest set."""
        plan = self._plan
        if plan is None:
            return None
        levels = [
            r.value
            for mt in plan.modules.values()
            for r in mt.rows
            if r.key == BRIGHTNESS_KEY and isinstance(r.value, int)
        ]
        return max(levels) if levels else None

    async def reload_looks(self, *, changed: Iterable[str], why: str) -> CanopyState:
        """The live looks changed (conductor/design_lab/publish.py, in the SAME synchronous section as the overlay
        write and install_looks: nothing here awaits before the re-pick is installed). A try-on ends (no repick of
        its own); the T1 plans and holds follow the new trio; the current look is re-picked (one burst, guest
        brightness and power kept) when its scene changed or a try-on ran, else nothing is sent. A re-pick that finds
        no plan under the new trio puts the safe look up and alerts; an exception is re-raised (the publish rolls
        back)."""
        changed = list(changed)
        cleared = self._clear_trial(f"the look was published ({why})")
        self.invalidate_t1_plans()
        self._t1_hold = (self._t1_hold - set(changed)) | self.t1_hold_for(changed)
        plan = self._plan
        if plan is None or (plan.mode_id not in changed and cleared is None):
            self._publish_soon()
            return self.state()
        mode_id = plan.mode_id
        try:
            st = await self._request(mode_id, source=LOOKS_SOURCE, ripple=False)
        except BaseException as exc:
            self._recover_from_trial_error(cleared, exc, restore=True)
            raise
        if st.refused:
            self._restore_approved(cleared)  # compiles from the live trio now
            if self._plan is plan:  # still the old trio's (or the try-on's) plan: never left up
                self._safe_look_now()
            self._unshowable.add(mode_id)
            self.telemetry.record(
                "alert",
                level="warn",
                message=f"Published looks: {plan.label} can't be shown under the published looks ({st.message}); "
                "the safe look is on.",
                mode_id=mode_id,
                alert_key=f"looks:show:{mode_id}",
            )
        return st

    def _safe_look_now(self) -> None:
        """The safe look (Lowcountry Calm, strand), now, after a look that can't be shown (never raises)."""
        try:
            safe = self.deps.compile(SAFE_MODE, Tier.STRAND)
        except Exception:
            log.exception("The safe look couldn't be compiled")
            return
        self._show(safe, "reconciler")

    async def show_after_publish(self, mode_id: str) -> CanopyState:
        """The publish dialog's "Show <scene> on the canopy after publishing": staff choose the scene (source ops:
        its designed brightness, on), one burst whatever the ripple setting."""
        return await self._request(mode_id, source="ops", ripple=False)

    async def repick_after_rollback(
        self, why: str, *, changed: Iterable[str] = (), installed: bool = True
    ) -> CanopyState | None:
        """A publish that failed after its install was rolled back: the T1 hold of the publish's changed scenes
        recomputed under the restored trio (reload_looks set it for the failed one: a stale hold would keep a painted
        scene below T1 for good, a dropped one would show T1 copy its slots don't hold), then the current look again
        under the restored trio (one burst), else the safe look. `installed` False (the rollback's own install
        failed, so the wiring may still compile the failed trio): the changed painted scenes are held, to be safe."""
        changed = set(changed)
        self.invalidate_t1_plans()
        if installed:
            self._t1_hold = (self._t1_hold - changed) | self.t1_hold_for(changed)
        else:
            for mode_id in changed:
                try:
                    if Tier.T1 in list(self.deps.ladder(mode_id)):
                        self._t1_hold.add(mode_id)
                except (KeyError, LookupError, PlanError, ValueError):
                    continue
        plan = self._plan
        if plan is None or self._stopped:
            return None
        try:
            st = await self._request(plan.mode_id, source=LOOKS_SOURCE, ripple=False)
        except Exception:
            log.exception("The look couldn't be re-picked after a failed publish (%s)", why)
            self._safe_look_now()
            return None
        if st.refused:
            self._safe_look_now()
        return st

    # ---- the operator repaint (D68 PR B2, docs/live-lab/PLAN.md §2.6–§2.7) -----------------------------------------
    def _repaint_interval(self) -> float:
        lab = getattr(self.deps.settings, "lab", None)
        return float(getattr(lab, "repaint_min_interval_s", 120.0))

    def repaint_wait(self, mode_id: str) -> float:
        """Seconds until an operator repaint of `mode_id` may start (the 2-min gate): 0 = now, never more than
        `lab.repaint_min_interval_s`. A scene still queued keeps its due time."""
        interval = self._repaint_interval()
        now = self.clock.monotonic()
        left = 0.0
        last = self._repaint_last.get(mode_id)
        if last is not None:
            left = last + interval - now
        waiting = self._repaint_waiting.get(mode_id)
        if waiting is not None:
            left = max(left, waiting.due_mono - now)
        return max(0.0, min(interval, left))

    def seed_repaint_gate(self, ages: dict[str, float]) -> None:
        """After a restart: each scene's last operator repaint was `age` seconds ago by the wall clock (the overlay's
        `repaints`). Clamped to [0, interval], so a wall-clock step can't lock a scene out for longer than the
        interval, or open it sooner than the interval allows in this process; a later start seen here wins."""
        interval = self._repaint_interval()
        now = self.clock.monotonic()
        for mode_id, age in ages.items():
            if not isinstance(age, int | float) or not math.isfinite(age):
                continue
            last = now - max(0.0, min(interval, float(age)))
            self._repaint_last[mode_id] = max(self._repaint_last.get(mode_id, float("-inf")), last)

    def queued_repaints(self) -> dict[str, RepaintJob]:
        """The scenes of the running job that haven't started writing (a newer publish keeps their due times)."""
        return dict(self._repaint_waiting)

    def start_repaint(
        self, jobs: Sequence[RepaintJob], *, generation: int, publication: str | None = None
    ) -> list[RepaintJob]:
        """ONE job per publish (D68 PR B2). Synchronously, in this call: the job that hasn't started writing is
        cancelled (its marks with it; a publish is refused while one writes), every (device, slot) of `jobs` is
        marked pending, and the job's task is spawned. Each scene's due time is never sooner than the gate
        (`lab.repaint_min_interval_s` after that scene's last operator repaint started — enforced HERE, so no caller
        can bypass it), keeps a still-queued scene's due time, and is never later than one interval from now.
        Returns the jobs as scheduled. The task's try/finally wraps everything, so marks never leak."""
        if self._repaint_quiet:
            raise RuntimeError("an operator repaint is writing; a new one waits until it ends")
        previous = self._repaint_waiting
        self._cancel_repaint_task()
        now = self.clock.monotonic()
        interval = self._repaint_interval()
        scheduled: list[RepaintJob] = []
        for job in jobs:
            if not job.modules or any(j.mode_id == job.mode_id for j in scheduled):
                continue
            due = max(job.due_mono, now)
            old = previous.get(job.mode_id)
            if old is not None:
                due = max(due, old.due_mono)  # latest wins; the due time stays
            last = self._repaint_last.get(job.mode_id)
            if last is not None:
                due = max(due, last + interval)
            scheduled.append(replace(job, due_mono=min(due, now + interval)))
        # the scenes run one after another: the one due first goes first, so a scene due now never waits behind
        # another scene's gate (stable: equal due times keep the publish's order)
        scheduled.sort(key=lambda j: j.due_mono)
        for mode_id in set(previous) - {j.mode_id for j in scheduled}:
            # its queued repaint went: its slots may hold the live design already
            self._maybe_lift_hold(mode_id)
        if not scheduled:
            self.repaint_status = None
            self._hook("progress", None)
            self._publish_soon()
            return []
        self._repaint_serial += 1
        serial = self._repaint_serial
        for job in scheduled:
            for device_id in job.modules:
                self._repaint_pending[(device_id, job.slot)] = serial
        self._repaint_waiting = {j.mode_id: j for j in scheduled}
        first = scheduled[0]
        self._set_repaint_status(
            first,
            publication,
            first.modules,
            queued_until=self.clock.now() + (first.due_mono - now) if first.due_mono > now else None,
        )
        self._repaint_task = asyncio.get_running_loop().create_task(
            self._repaint(scheduled, generation, publication, serial), name="canopy:repaint"
        )
        self._publish_soon()
        return scheduled

    def cancel_repaint(self, why: str) -> None:
        """Cancel the job that hasn't started writing (a rolled-back publish): its marks and status go now."""
        if self._repaint_quiet:
            return  # a writing window always finishes (its finally clears it)
        had = list(self._repaint_waiting)
        self._cancel_repaint_task()
        if had:
            log.info("Operator repaint of %s cancelled: %s", ", ".join(had), why)
        self.repaint_status = None
        self._hook("progress", None)
        for mode_id in had:
            self._maybe_lift_hold(mode_id)
        self._publish_soon()

    def _cancel_repaint_task(self) -> None:
        task, self._repaint_task = self._repaint_task, None
        if task is not None and not task.done() and task is not _current_task():
            task.cancel()  # its scene's finally sees the cancellation: no bookkeeping, no alerts
        self._repaint_pending.clear()
        self._repaint_waiting = {}

    async def wait_repaint(self) -> None:
        """Wait until no operator repaint job is queued or running (tests, the soak, shutdown)."""
        while True:
            task = self._repaint_task
            if task is None or task.done():
                return
            await asyncio.gather(task, return_exceptions=True)

    def repaint_json(self) -> dict | None:
        """RepaintJSON (docs/live-lab/PLAN.md §2.11): the running, queued or last job's scene and its modules."""
        status = self.repaint_status
        if status is None:
            return None
        return {**status, "modules": [dict(m) for m in status.get("modules", [])]}

    def _repaint_left(self) -> float:
        """Seconds the writing window has left (an estimate: about M·1.2 + 3 s for M modules), at least 1."""
        ends = self._repaint_ends
        left = (ends - self.clock.monotonic()) if ends is not None else REPAINT_BASE_S
        return round(max(1.0, left), 2)

    def repaint_left_total(self) -> float:
        """Seconds until the job is over: the window's estimate, or the first queued scene's wait plus its own."""
        if self._repaint_quiet:
            return self._repaint_left()
        now = self.clock.monotonic()
        total = 0.0
        for job in self._repaint_waiting.values():
            total = max(total, job.due_mono - now) + len(job.modules) * REPAINT_PER_MODULE_S + REPAINT_BASE_S
        return round(max(1.0, total), 2)

    def _hook(self, event: str, info: Any) -> None:
        hook = self.deps.on_repaint
        if hook is None:
            return
        try:
            hook(event, info)
        except Exception:  # the overlay or the Lab failing never stops a repaint
            log.exception("repaint hook %s failed", event)

    def _set_repaint_status(
        self,
        job: RepaintJob,
        publication: str | None,
        modules: Iterable[str],
        *,
        queued_until: float | None = None,
        started_at: float | None = None,
    ) -> None:
        def label(device_id: str) -> str:
            entry = self._entry(device_id)
            return (entry.label if entry else "") or device_id

        self.repaint_status = {
            "running": True,
            "queued_until": queued_until,
            "scene": job.mode_id,
            "label": job.label or job.mode_id,
            "slot": job.slot,
            "publication": publication,
            "started_at": started_at,
            "finished_at": None,
            "modules": [{"id": d, "label": label(d), "status": "pending", "reason": None} for d in modules],
        }
        self._hook("progress", self.repaint_json())

    def _note_repaint_module(self, device_id: str, entry: dict) -> None:
        status = self.repaint_status
        if status is None:
            return
        for row in status["modules"]:
            if row["id"] == device_id:
                row["status"] = str(entry.get("status") or "failed")
                row["reason"] = entry.get("reason") if row["status"] != "applied" else None
        self._hook("progress", self.repaint_json())
        self._publish_soon()

    def _repaint_refusal(self) -> str | None:
        """Why a due scene can't be repainted now (it stays held at module level), else None."""
        if self._stopped:
            return "the canopy engine is stopped"
        if self._commissioning:
            return "a kit was being commissioned"
        if self._outage is not None:
            return "Hubspace wasn't answering (the canopy was resting)"
        if self._rehearsal is not None:
            return "a stepped rehearsal was running"
        return None

    async def _repaint(
        self, jobs: list[RepaintJob], generation: int, publication: str | None, serial: int
    ) -> dict[str, dict]:
        """The job (§2.6): its scenes in sequence; the try/finally around all of it clears its marks."""
        outcomes: dict[str, dict] = {}
        try:
            for job in jobs:
                outcomes[job.mode_id] = await self._repaint_scene(job, generation, publication, serial)
        finally:
            for pair, owner in list(self._repaint_pending.items()):
                if owner == serial:
                    del self._repaint_pending[pair]
            for job in jobs:
                if self._repaint_waiting.get(job.mode_id) is job:
                    del self._repaint_waiting[job.mode_id]
            self._publish_soon()
        return outcomes

    async def _repaint_scene(
        self, job: RepaintJob, generation: int, publication: str | None, serial: int
    ) -> dict:
        mode_id, slot = job.mode_id, job.slot
        outcome: dict[str, Any] = {
            "mode": mode_id,
            "label": job.label or mode_id,
            "slot": slot,
            "publication": publication,
            "ok": False,
            "applied": 0,
            "skipped": 0,
            "failed": 0,
            "listed": list(job.modules),
            "modules": {},
            "reason": None,
            "started": False,
        }
        listed: list[str] = list(job.modules)
        writing = cancelled = False
        try:
            wait = job.due_mono - self.clock.monotonic()
            if wait > 0:  # the 2-min gate: held at module level meanwhile, its marks pending
                self._set_repaint_status(job, publication, listed, queued_until=self.clock.now() + wait)
                await self.clock.sleep(wait)
            # after a restart: never before the show's first capability reads (and, in sim, its commissioning)
            if self.deps.repaint_ready is not None:
                await self.deps.repaint_ready()
            if self._repaint_waiting.get(mode_id) is job:
                del self._repaint_waiting[mode_id]
            looks_generation = self.deps.looks_generation
            if looks_generation is not None and looks_generation() != generation:
                outcome["reason"] = "a newer publish replaced it"
                outcome["aborted"] = True
                return outcome
            why = self._repaint_refusal()
            if why is not None:
                outcome["reason"] = why
                return outcome
            # The quiet window: a try-on ends first (its approved look goes out now, before the window); then nothing
            # else writes until the slots are done, and the commands already on their way finish first.
            self._clear_trial("a publish's slot repaint started", repick=True)
            self._repaint_quiet = writing = True
            self._repaint_ends = self.clock.monotonic() + len(listed) * REPAINT_PER_MODULE_S + REPAINT_BASE_S
            self._publish_soon()
            with contextlib.suppress(TimeoutError):
                await self.wait_idle(timeout=REPAINT_IDLE_WAIT_S)
            target = self.deps.repaint_target(mode_id) if self.deps.repaint_target is not None else None
            plan = getattr(target, "plan", None) if target is not None else None
            listed = list(getattr(target, "listed", None) or []) if target is not None else []
            if plan is None or not listed:  # recomputed now
                unread = list(getattr(target, "unread", None) or []) if target is not None else []
                # unread: nothing to judge them by, so never "already holds" (the reconciler takes them once read)
                if unread:
                    n = len(unread)
                    outcome.update(
                        listed=[],
                        reason=f"the Hubspace profile of {n} module{'s' if n != 1 else ''} hadn't been read yet",
                    )
                else:  # the slots already hold the live design
                    outcome.update(ok=True, listed=[], reason="its slots already hold the live design")
                return outcome
            for device_id in listed:
                self._repaint_pending.setdefault((device_id, slot), serial)
            now = self.clock.monotonic()
            self._repaint_ends = now + len(listed) * REPAINT_PER_MODULE_S + REPAINT_BASE_S
            self._repaint_last[mode_id] = now  # the gate counts from the start of the writing
            self.stats["repaints"] += 1
            outcome.update(started=True, listed=listed)
            started_at = self.clock.now()
            self._set_repaint_status(job, publication, listed, started_at=started_at)
            self._hook(
                "started", {"mode": mode_id, "slot": slot, "at": started_at, "publication": publication}
            )
            report = await self._commission_plan(
                plan,
                LOOKS_SOURCE,
                min_interval=0.0,
                only=listed,
                verb=REPAINT_VERB,
                locked=True,
                on_module=self._note_repaint_module,
            )
            modules = report["modules"]
            outcome["modules"] = modules
            statuses = [m.get("status") for m in modules.values()]
            outcome["applied"] = statuses.count("applied")
            outcome["skipped"] = sum(1 for s in statuses if s in ("skipped", "locked"))
            outcome["failed"] = len(statuses) - outcome["applied"] - outcome["skipped"]
            outcome["ok"] = outcome["applied"] == len(listed)
            return outcome
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as e:  # never leave the window open or the marks set; the scene stays held
            log.exception("The operator repaint of %s failed", mode_id)
            outcome["reason"] = f"{type(e).__name__}: {e}"
            return outcome
        finally:
            for device_id in {*job.modules, *listed}:
                if self._repaint_pending.get((device_id, slot)) == serial:
                    del self._repaint_pending[(device_id, slot)]
            if writing:
                self._repaint_quiet = False
                self._repaint_ends = None
                for listener in list(self.quiet_listeners):
                    try:
                        listener()
                    except Exception:
                        log.exception("quiet-window listener failed")
            if not cancelled:
                self._repaint_done(outcome)
            self._publish_soon()

    def _repaint_done(self, outcome: dict) -> None:
        """A scene's repaint is over (applied, partly, refused, or nothing to do): the hold lifts when its slots hold
        the live design (one re-pick: the re-select after rewriting the active slot), the Lab's status, the overlay
        (via the hook), telemetry and the scene's alert."""
        mode_id, slot, label = outcome["mode"], outcome["slot"], outcome["label"]
        was_held = mode_id in self._t1_hold
        self._maybe_lift_hold(mode_id)
        plan = self._plan
        if (
            outcome["started"]
            and outcome["applied"]
            and not was_held
            and plan is not None
            and plan.mode_id == mode_id
            and plan.tier is Tier.T1
            and not self.quiet
        ):
            # the active slot was rewritten under a look that wasn't held: re-select it (04 item 6), one burst
            self.send(None, source="reconciler", label=f"re-select after the repaint: {mode_id}")
        status = self.repaint_status
        if status is not None and status.get("scene") == mode_id:
            status["running"] = False
            status["queued_until"] = None
            status["finished_at"] = self.clock.now()
            self._hook("progress", self.repaint_json())
        self._hook("finished", dict(outcome))
        if outcome.get("aborted"):
            return
        self.telemetry.record(
            "lab",
            action="repaint",
            scene=mode_id,
            slot=slot,
            publication=outcome["publication"],
            applied=outcome["applied"],
            skipped=outcome["skipped"],
            failed=outcome["failed"],
            ok=outcome["ok"],
            reason=outcome["reason"],
            source="engine",
        )
        key = f"looks:repaint:{mode_id}"
        n = len(outcome["listed"])
        if not outcome["started"] and outcome["ok"]:
            return  # nothing to repaint: no news
        if not outcome["started"]:
            message = (
                f"Published looks: {label}'s repaint of {slot} didn't start ({outcome['reason']}); it shows at "
                "module level until its slots hold the design (the reconciler rewrites them, at most once every 10 "
                "min per slot)."
            )
            level = "warn"
        elif outcome["ok"]:
            message = f"Published looks: {label}'s painted design is in {slot} on {n} module{'s' if n != 1 else ''}."
            level = "info"
        else:
            missed = []
            for device_id, entry in outcome["modules"].items():
                if entry.get("status") == "applied":
                    continue
                e = self._entry(device_id)
                name = (e.label if e else "") or device_id
                missed.append(f"{name}: {entry.get('reason') or entry.get('status')}")
            message = (
                f"Published looks: {label}: {slot} repainted on {outcome['applied']} of {n} modules "
                f"({'; '.join(missed)}); it shows at module level until the rest are current (the reconciler "
                "retries after 10 min)."
            )
            level = "warn"
        self.telemetry.record("alert", level=level, message=message, mode_id=mode_id, alert_key=key)

    # ---- compiling --------------------------------------------------------------------------------------------
    def compile_best(
        self, mode_id: str, *, floor: int | None = None, skip: frozenset[Tier] = frozenset()
    ) -> tuple[ModePlan | None, str]:
        """The best plan for `mode_id`: the first allowed tier that compiles, from the floor down, then above it
        (never a tier in `skip`). While a Design Lab try-on of `mode_id` stands, only module level or strand (D66)."""
        if self._trial is not None and mode_id == self._trial.mode_id:
            skip = skip | TRIAL_SKIP
        # its slots don't hold the live design yet (D68): module level, truthfully
        if mode_id in self._t1_hold:
            skip = skip | {Tier.T1}
        try:
            ladder = list(self.deps.ladder(mode_id))
        except (KeyError, LookupError, PlanError, ValueError):
            return None, f"There's no mode called {mode_id!r}."
        if not ladder:
            return None, f"There's no mode called {mode_id!r}."
        start = self.floors.get(mode_id, 0) if floor is None else floor
        start = min(max(0, start), len(ladder) - 1)
        order = list(range(start, len(ladder))) + list(range(start))
        reasons = []
        for i in order:
            tier = ladder[i]
            if tier in skip:
                continue
            allowed, why = self.deps.tier_allowed(mode_id, tier)
            if not allowed:
                reasons.append(f"{tier}: {why or 'not allowed'}")
                continue
            try:
                return self.deps.compile(mode_id, tier), ""
            except PlanError as e:
                reasons.append(f"{tier}: {e}")
        return None, f"{mode_id} can't be shown right now ({'; '.join(reasons)})."

    # ---- applying ---------------------------------------------------------------------------------------------
    def _show(self, plan: ModePlan, source: str, *, ripple: bool = False) -> None:
        """Make `plan` the current look and (unless resting) send it to every module: one burst, or with `ripple`
        ring by ring when the plan has rings and a stagger is configured (D64)."""
        plan = self._install(plan, source)
        self._shown_again(plan)
        if self._outage is None:
            rings = self._rings(plan) if ripple else None
            if rings is None:
                self.send(None, source=source, label=f"mode: {plan.mode_id}")
            else:
                self._start_ripple(plan, source, rings)
        self._publish_soon()

    def _shown_again(self, plan: ModePlan) -> None:
        """A mode a publish's re-pick couldn't show is on the canopy again under the live looks (never a try-on's
        draft): its "can't be shown … the safe look is on" warn no longer stands."""
        mode_id = plan.mode_id
        if mode_id not in self._unshowable:
            return
        if self._trial is not None and self._trial.mode_id == mode_id:
            return
        self._unshowable.discard(mode_id)
        self.telemetry.record(
            "alert",
            level="info",
            message=f"Published looks: {plan.label} shows again under the published looks.",
            mode_id=mode_id,
            alert_key=f"looks:show:{mode_id}",
        )

    def _rings(self, plan: ModePlan) -> list[list[str]] | None:
        """The plan's rings to send one after another, or None for one burst (no stagger, a motion plan, no rings,
        or only one)."""
        stagger = self.deps.settings.kiosk.ripple_stagger_s
        if stagger <= 0 or plan.motion or not plan.waves:
            return None
        rings = [[d for d in wave if d in plan.modules] for wave in plan.waves]
        placed = {d for ring in rings for d in ring}
        rings[0] += [d for d in plan.modules if d not in placed]  # never leave a module out of the look
        rings = [ring for ring in rings if ring]
        return rings if len(rings) > 1 else None

    def _start_ripple(self, plan: ModePlan, source: str, rings: list[list[str]]) -> None:
        stagger = self.deps.settings.kiosk.ripple_stagger_s
        generation, start = self._generation, self.clock.monotonic()
        self._ripple = {"t0": self.clock.now(), "stagger_s": stagger, "rings": rings}
        self._pending = {d: k for k, ring in enumerate(rings) for d in ring if k > 0}
        self.send(rings[0], source=source, label=f"mode: {plan.mode_id} (ring 1 of {len(rings)})")
        for k in range(1, len(rings)):
            self._spawn(
                self._send_ring(plan, source, generation, k, start + k * stagger, len(rings)),
                name=f"canopy:ripple:{plan.mode_id}:{k}",
            )

    async def _send_ring(
        self, plan: ModePlan, source: str, generation: int, k: int, at: float, count: int
    ) -> None:
        """Ring k of a ripple, at its time — only while its look is still the current one."""
        delay = at - self.clock.monotonic()
        if delay > 0:
            await self.clock.sleep(delay)
        if self._generation != generation or self._outage is not None:
            return  # a newer look replaced it; while resting, recovery sends the whole look at once
        ids = [d for d, ring in self._pending.items() if ring == k]
        for device_id in ids:
            del self._pending[device_id]
        if not self._pending:
            self._ripple = None
        if ids:
            self.send(ids, source=source, label=f"mode: {plan.mode_id} (ring {k + 1} of {count})")
        self._publish_soon()

    def _end_ripple(self) -> None:
        self._ripple = None
        self._pending.clear()

    def wave_pending(self, address: str) -> bool:
        """Is the module at `address` still waiting for its ring of the ripple (nothing of the look sent yet)?"""
        device_id = self._by_address.get(address)
        return device_id is not None and device_id in self._pending

    def _install(self, plan: ModePlan, source: str) -> ModePlan:
        """Make `plan` the current look (the plan installed is returned: a rehearsal's run is its minutes long)."""
        rehearsal = self._rehearsal
        ending = None
        if rehearsal is not None:
            if plan.mode_id == rehearsal.mode and plan.program is not None:
                plan = replace(plan, program=replace(plan.program, run_s=rehearsal.run_s))
            else:  # another look replaced the rehearsal's (ops, a downgrade): it ends with what it measured
                ending = f"{plan.label} ({plan.tier}) replaced it ({source})"
        self._end_ripple()  # the old look's rings not sent yet are dropped
        self._generation += 1
        self._plan = plan
        self._source = source
        self._changed_at = self.clock.now()
        self._early_counts.clear()
        self.last_status.clear()
        for device_id in plan.modules:
            self._resolve(device_id)
        if self._runner is not None:
            self._runner.cancel()  # the old look's steps stop here
            self._runner = None
        if plan.program is not None:
            # t0 = when the look was last chosen: a mood just chosen starts a run; a re-pick of the look now showing
            # (a tier change, new capabilities) keeps the run's phase, or only holds once the run is over. A guest's
            # brightness or on/off (which does count as a change for the idle hold) never restarts a run.
            self._runner = ProgramRunner(self, plan, t0=self._chosen_mono)
            self._runner.start()
        if ending is not None and rehearsal is not None:
            self._end_rehearsal(ending, rehearsal=rehearsal, settle=False)
        elif rehearsal is not None:
            rehearsal.runner = self._runner
            if self._runner is None or not self._runner.running:  # it can't step (or its run is already over)
                why = self._runner.ended if self._runner is not None else "it has no steps"
                self._end_rehearsal(f"the look holds still: {why}", rehearsal=rehearsal, settle=False)
        self.reconciler.on_new_plan()
        self.telemetry.record("mode", mode=plan.mode_id, tier=str(plan.tier), source=source)
        return plan

    def send(self, device_ids: Iterable[str] | None, *, source: str, label: str) -> list[str]:
        """Send the current plan's full state vector to `device_ids` (None = every module) as one burst.

        Locked modules are skipped (and remembered, so the reconciler re-sends on release). Returns the device ids
        a command was queued for."""
        plan = self._plan
        if plan is None:
            return []
        if device_ids is None:
            self._end_ripple()  # every module now: the rings still waiting go with this burst
        wanted = list(plan.modules) if device_ids is None else [d for d in device_ids if d in plan.modules]
        groups: dict[int, tuple[CommandExecutor, list[Command]]] = {}
        sent = []
        for device_id in wanted:
            target = self._targets.get(device_id) or self._resolve(device_id)
            if target is None:
                continue
            if self.locks.holder(target.address) is not None:
                self._locked.add(target.address)
                continue
            mt = plan.modules[device_id]
            expect = self._desired_expect(device_id, mt, plan)
            rows = self.desired_rows(device_id)
            self.tracker.set_desired(
                target.address,
                expect,
                label=self._desired_label(plan),
                source=source,
                meta={"mode": plan.mode_id, "tier": str(plan.tier), "device_id": device_id},
            )
            cmd = Command(
                target.address,
                rows,
                Purpose.ENGINE,
                label=label,
                verify=True,
                expect=expect,
                source=source,
            )
            self._track(cmd, device_id, "show")
            self._latest[target.address] = cmd
            self.dirty.add(target.address)
            self.last_status.pop(target.address, None)
            groups.setdefault(id(target.executor), (target.executor, []))[1].append(cmd)
            self.sent_rows[device_id] = rows
            sent.append(device_id)
        if self._runner is not None and sent:
            self._runner.note_sent(sent)
        for executor, cmds in groups.values():
            self._spawn(self._burst(executor, cmds), name=f"canopy:{label}")
        if sent:
            self._publish_soon()
        return sent

    def _track(self, cmd: Command, device_id: str, kind: str) -> None:
        self._sent[id(cmd)] = _Sent(cmd, device_id, self._generation, kind)

    async def _burst(self, executor: CommandExecutor, cmds: list[Command]) -> list[CommandResult]:
        try:
            return await executor.burst(cmds)
        except Exception:
            log.exception("canopy burst failed")
            for cmd in cmds:
                self._sent.pop(id(cmd), None)
            self.reconciler.schedule_reassert()
            return []

    def _spawn(self, coro, *, name: str) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ---- targets ----------------------------------------------------------------------------------------------
    def _resolve(self, device_id: str) -> Any:
        try:
            target = self.deps.target(device_id, Purpose.ENGINE)
        except (LookupError, KeyError) as e:
            self._targets.pop(device_id, None)
            self._unresolved[device_id] = str(e).strip("'\"")
            return None
        old = self._targets.get(device_id)
        if old is not None and old.address != target.address:
            self._by_address.pop(old.address, None)
        self._targets[device_id] = target
        self._by_address[target.address] = device_id
        self._unresolved.pop(device_id, None)
        self._attach(target)
        return target

    def _attach(self, target: Any) -> None:
        executor = target.executor
        if id(executor) not in self._executors:
            self._executors[id(executor)] = executor
            self._unsubscribe.append(executor.add_listener(self._on_result))
        callbacks = getattr(target.driver, "on_recovery", None)
        if isinstance(callbacks, list) and not any(c is callbacks for c in self._recovery_lists):
            callbacks.append(self._on_driver_recovery)
            self._recovery_lists.append(callbacks)

    def active_modules(self) -> list[tuple[str, Any]]:
        """(device id, Target) for every module of the current plan (or, before the first plan, every module the
        engine knows) that has an address and isn't locked."""
        plan = self._plan
        out = []
        for device_id in plan.modules if plan is not None else list(self._targets):
            target = self._targets.get(device_id)
            if target is not None and self.locks.holder(target.address) is None:
                out.append((device_id, target))
        return out

    def target_of(self, device_id: str) -> Any:
        return self._targets.get(device_id)

    def _module_ids(self) -> list[str]:
        if self._plan is not None:
            return [*self._plan.modules, *(d for d in self._plan.skipped if d not in self._plan.modules)]
        return [d.id for d in self.deps.registry().canopy_modules()]

    def _entry(self, device_id: str) -> Any:
        try:
            return self.deps.registry().get(device_id)
        except (KeyError, LookupError):
            return None

    def _simulated(self) -> bool:
        if self.deps.simulated is not None:
            return bool(self.deps.simulated())
        if self._targets:
            return any(bool(t.simulated) for t in self._targets.values())
        return True  # nothing resolved yet: never claim real hardware

    # ---- results and events -----------------------------------------------------------------------------------
    def _on_result(self, result: CommandResult) -> None:
        record = self._sent.pop(id(result.command), None)
        if record is None:
            return
        cmd = result.command
        address = cmd.device_id
        if (
            result.status is CommandStatus.FAILED
            and result.attempts
            and result.attempts[-1].outcome is Outcome.THROTTLED  # the executor's retries didn't clear it
            and result.attempts[-1].http_status
            not in OUTAGE_HTTP_STATUSES  # 503/504: an outage, not a throttle
        ):
            self.reconciler.note_throttle_failure(self._account_of(address))
        current = record.kind == "show" and record.generation == self._generation
        if current and self._latest.get(address) is cmd:
            self.last_status[address] = result.status
            if result.status is CommandStatus.SUPERSEDED:
                # Something other than a newer engine command replaced it (an older burst that queued late):
                # check again soon.
                self.reconciler.schedule_reassert(1.0)
            elif result.status is CommandStatus.MISMATCH:
                self.reconciler.schedule_reassert(0.0)  # accepted but not stored (a silent drop): re-send now
            elif result.status in _RESEND_STATUSES:
                self.reconciler.schedule_reassert()
            target = self._targets.get(record.device_id)
            runner = self._runner
            if runner is not None and result.status is CommandStatus.APPLIED:
                runner.note_verified(record.device_id)
                # A moving stepped look skips the rolling true-up reads (each step is verified): its slot patterns
                # are checked from these verification readbacks instead, at most once a minute per module.
                if (
                    runner.moving
                    and target is not None
                    and result.readback is not None
                    and result.readback.ok
                ):
                    self.reconciler.note_verification(record.device_id, target, result.readback.rows)
            if result.status is CommandStatus.MISMATCH and target is not None:
                # If a slot's pattern is what differs, that's drift, not something a re-send fixes: check now.
                if result.readback is not None and result.readback.ok:
                    self.reconciler.check_slots(record.device_id, target, result.readback.rows)
                else:
                    self.reconciler.check_module_soon(record.device_id, target)
        self._publish_soon()

    def _on_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind not in ("attempt", "readback", "command"):
            return
        address = event.get("device_id")
        if address not in self._by_address:
            return
        self.dirty.add(address)
        self._publish_soon()
        if kind == "command":
            return
        outcome = event.get("outcome")
        if outcome == str(Outcome.OK):
            self._bad_streak.clear()
            # A verification readback that still differs in a row a re-send can fix (not only a slot's stored
            # pattern, which is drift) after the first: the cloud didn't store the write. The first readback alone
            # isn't evidence — live, it's usually the cloud still echoing the old value (D70).
            differs = [c for c in event.get("mismatched") or () if c != R.FRAME_CLASS]
            if kind == "readback" and differs:
                self._maybe_early_reassert(address, event.get("n") or 1)
        elif (
            is_outage_signal(outcome, event.get("http_status")) and self._outage is None and not self._stopped
        ):
            self._bad_streak.add(address)
            if len(self._bad_streak) >= min(OUTAGE_MIN_MODULES, max(1, len(self._by_address))):
                self.enter_outage(
                    f"Hubspace isn't answering ({outcome.replace('_', ' ')}) for "
                    f"{len(self._bad_streak)} module(s)."
                )

    def _maybe_early_reassert(self, address: str, readback: int = 1) -> None:
        plan = self._plan
        cmd = self._latest.get(address)
        if plan is None or cmd is None or self._outage is not None or self.quiet:
            return
        record = self._sent.get(id(cmd))
        if record is None or record.generation != self._generation or record.kind != "show":
            return  # that command already finished (a true-up readback, not a verification)
        # Motion plans re-trigger every module as one burst (07 §6), once per change; others re-send the one module,
        # at most EARLY_REASSERT_LIMIT times per change (a module that keeps disagreeing falls back to the
        # reconciler's slower cadence and, eventually, the hysteresis downgrade).
        key = "*" if plan.motion else address
        count = self._early_counts.get(key, 0)
        if count >= (1 if plan.motion else EARLY_REASSERT_LIMIT):
            return
        # The first re-send of a change waits for a second readback that still differs (the first is usually echo
        # lag); once a module has been re-sent this change, a differing first readback is enough (≤ 10 s self-heal).
        if count == 0 and readback < EARLY_REASSERT_FROM_READBACK:
            return
        self._early_counts[key] = self._early_counts.get(key, 0) + 1
        self.stats["early_reasserts"] += 1
        targets = None if plan.motion else [self._by_address[address]]

        def resend() -> None:
            if self._plan is plan and self._outage is None:
                self.send(targets, source="reconciler", label=f"re-assert: {plan.mode_id}")

        asyncio.get_running_loop().call_soon(resend)

    def _account_of(self, address: str) -> str:
        device_id = self._by_address.get(address)
        target = self._targets.get(device_id) if device_id else None
        try:
            return target.driver.account_of(address) if target is not None else "unknown"
        except LookupError:
            return "unknown"

    # ---- outage -----------------------------------------------------------------------------------------------
    def enter_outage(self, reason: str) -> None:
        if self._outage is not None:
            return
        self._outage = {"since": self.clock.now(), "since_mono": self.clock.monotonic(), "reason": reason}
        self._bad_streak.clear()
        self.telemetry.record("alert", level="warn", message=f"Canopy resting: {reason}")
        self.reconciler.on_outage()
        self._publish_soon()

    def exit_outage(self, why: str) -> None:
        """Hubspace answers again: re-apply the current look to every module in one burst."""
        if self._outage is None:
            return
        outage, self._outage = self._outage, None
        record = {
            "since": outage["since"],
            "recovered_at": self.clock.now(),
            "duration_s": round(self.clock.monotonic() - outage["since_mono"], 3),
            "reason": outage["reason"],
        }
        self.stats["outages"].append(record)
        self.telemetry.record(
            "alert",
            level="info",
            message=f"Hubspace is answering again ({why}); re-applying "
            f"{self._plan.label if self._plan else 'nothing'} to every driven module.",
        )
        self.reconciler.on_recovery()
        if self._runner is not None:
            self._runner.sync()  # a stepped look comes back at the step its run is at now
        if self._plan is not None:
            self.send(None, source="recovery", label=f"recovery: {self._plan.mode_id}")
        for listener in list(self.recovery_listeners):
            try:
                listener()
            except Exception:
                log.exception("canopy recovery listener failed")
        self._publish_soon()

    def _on_driver_recovery(self, _account: str = "") -> None:
        self.reconciler.on_driver_recovery()

    # ---- hysteresis (called by the reconciler) -----------------------------------------------------------------
    def downgrade(self, reason: str) -> bool:
        """Move the current mode one allowed, compilable rung down its ladder. False when there's none.

        A stepped rung is never a step down: it writes far more often than any still rung (one PUT per module per
        step), so easing off from a better rung (e.g. Golden Hour's per-head sun) passes over it to the next still
        rung (D65)."""
        plan = self._plan
        if plan is None:
            return False
        try:
            ladder = list(self.deps.ladder(plan.mode_id))
        except (KeyError, LookupError, PlanError):
            return False
        if plan.tier not in ladder:
            return False
        for index in range(ladder.index(plan.tier) + 1, len(ladder)):
            tier = ladder[index]
            if tier is Tier.STEPPED or not self.deps.tier_allowed(plan.mode_id, tier)[0]:
                continue
            try:
                lower = self.deps.compile(plan.mode_id, tier)
            except PlanError:
                continue
            self.floors[plan.mode_id] = index
            self._tier_change(plan.mode_id, plan.tier, lower.tier, reason, "down")
            self._show(lower, "reconciler")
            return True
        return False

    def upgrade(self, reason: str) -> list[str]:
        """Lift every hysteresis floor one rung; re-apply the current mode if its tier can rise. Returns the modes."""
        lifted = []
        for mode_id in list(self.floors):
            floor = self.floors[mode_id] - 1
            try:
                ladder = list(self.deps.ladder(mode_id))
            except (KeyError, LookupError, PlanError, ValueError):
                ladder = []
            # A rung that isn't allowed or doesn't compile right now can't be shown, so lifting the floor onto it
            # would change nothing and cost another clean interval (F8): lift past it to the next usable rung. A
            # stepped rung between the floor and the top is passed over too, as downgrade() does (D65): easing back
            # up after throttling never lands on the rung that writes most.
            while 0 < floor < len(ladder) and (
                ladder[floor] is Tier.STEPPED or not self._usable(mode_id, ladder[floor])
            ):
                floor -= 1
            if floor <= 0:
                del self.floors[mode_id]
            else:
                self.floors[mode_id] = floor
            lifted.append(mode_id)
        plan = self._plan
        if plan is not None and plan.mode_id in lifted:
            better, _ = self.compile_best(plan.mode_id)
            if better is not None and better.tier != plan.tier:
                self._tier_change(plan.mode_id, plan.tier, better.tier, reason, "up")
                self._show(better, "reconciler")
        return lifted

    def _usable(self, mode_id: str, tier: Tier) -> bool:
        if not self.deps.tier_allowed(mode_id, tier)[0]:
            return False
        try:
            self.deps.compile(mode_id, tier)
        except (PlanError, KeyError, LookupError):
            return False
        return True

    def _tier_change(self, mode_id: str, old: Tier, new: Tier, reason: str, direction: str) -> None:
        self.stats["tier_changes"].append(
            {
                "t": self.clock.now(),
                "mode": mode_id,
                "from": str(old),
                "to": str(new),
                "direction": direction,
                "reason": reason,
            }
        )
        self.telemetry.record(
            "tier", mode=mode_id, reason=reason, direction=direction, **{"from": str(old), "to": str(new)}
        )
        self.telemetry.record(
            "alert",
            level="warn" if direction == "down" else "info",
            message=f"{mode_id}: {'down' if direction == 'down' else 'back up'} from {old} to {new} — {reason}",
        )

    # ---- slots ------------------------------------------------------------------------------------------------
    async def _commission_plan(
        self,
        plan: ModePlan,
        source: str,
        *,
        min_interval: float | None = None,
        only: Collection[str] | None = None,
        verb: str = "commission",
        locked: bool = False,
        on_module: Callable[[str, dict], Any] | None = None,
    ) -> dict:
        """Write `plan`'s slot content to its modules and record each readback (see the module doc).

        `min_interval` None = `reconciler.slot_rewrite_min_interval_s` (commissioning, as ever); `only` limits the
        modules; `verb` prefixes the command labels; `locked` writes each module under its `_slot_lock` (header read
        → burst → record, one burst per device, the modules concurrently); `on_module(device_id, entry)` hears each
        module's result. When `verb` isn't "commission" (the operator repaint) `slot_written` is stamped at SUBMIT for
        every module whose slot command was built, whatever the outcome, so a throttled or failed repaint is never
        followed by an immediate reconciler rewrite (its 600 s limit counts from the attempt)."""
        if min_interval is None:
            min_interval = self.deps.settings.reconciler.slot_rewrite_min_interval_s
        operator = verb != "commission"
        now = self.clock.monotonic()
        modules: dict[str, dict] = {}
        pending: list[tuple[str, ModuleTarget, Any]] = []
        for device_id, mt in plan.modules.items():
            if only is not None and device_id not in only:
                continue
            if not mt.slot or mt.slot_value is None:
                modules[device_id] = {"status": "skipped", "reason": "No slot content at this tier."}
                continue
            target = self._targets.get(device_id) or self._resolve(device_id)
            if target is None:
                modules[device_id] = {"status": "failed", "reason": self._unresolved.get(device_id, "")}
                continue
            record = self.recorded_slot(device_id, mt.slot)
            if record is not None and record["origin"] == "app":
                modules[device_id] = {
                    "status": "skipped",
                    "slot": mt.slot,
                    "reason": "Painted in the Hubspace app and recorded as hand-painted: commissioning never "
                    "overwrites a hand-painted slot (runbook: install-day commissioning).",
                }
                continue
            last = self.slot_written.get((device_id, mt.slot))
            if last is not None and now - last < min_interval:
                modules[device_id] = {
                    "status": "skipped",
                    "slot": mt.slot,
                    "reason": f"Written {round((now - last) / 60, 1)} min ago; each slot is rewritten at most "
                    f"once every {round(min_interval / 60)} min.",
                }
                continue
            pending.append((device_id, mt, target))
        for device_id, entry in modules.items():
            self._tell_module(on_module, device_id, entry)
        label = f"{verb}: {plan.label}"
        # A write that didn't land (a silent drop, throttling that outlasted the executor's retries, a throttled
        # header read) never changed the slot, so trying it again within this commission is a retry, not a rewrite.
        for _round in range(COMMISSION_ROUNDS):
            retry: list[tuple[str, ModuleTarget, Any]] = []
            if locked:
                outcomes = await asyncio.gather(
                    *(
                        self._slot_write_locked(plan, d, mt, t, source, label, operator)
                        for d, mt, t in pending
                    )
                )
                for (device_id, mt, target), (entry, again) in zip(pending, outcomes, strict=True):
                    modules[device_id] = entry
                    self._tell_module(on_module, device_id, entry)
                    if again:
                        retry.append((device_id, mt, target))
                pending = retry
                if not pending:
                    break
                continue
            batch: list[tuple[str, ModuleTarget, Any, Command]] = []
            for device_id, mt, target in pending:
                if self.locks.holder(target.address) is not None:
                    modules[device_id] = {
                        "status": "locked",
                        "slot": mt.slot,
                        "reason": "In use on a test page.",
                    }
                    continue
                cmd, error, retryable = await self._slot_command(device_id, target, mt, source, label)
                if cmd is None:
                    modules[device_id] = {"status": "failed", "slot": mt.slot, "reason": error}
                    if retryable:
                        retry.append((device_id, mt, target))
                    continue
                if operator:  # the limit counts from the attempt
                    self.slot_written[(device_id, mt.slot)] = self.clock.monotonic()
                batch.append((device_id, mt, target, cmd))
            results = await self._slot_burst(batch) if batch else {}
            for device_id, mt, target, cmd in batch:
                result = results.get(id(cmd))
                modules[device_id] = self._record_slot_result(device_id, mt.slot, plan, result)
                if modules[device_id]["status"] == "applied" and not operator:
                    self.slot_written[(device_id, mt.slot)] = self.clock.monotonic()
                elif result is not None and result.status in _SLOT_RETRY:
                    retry.append((device_id, mt, target))
            for device_id, entry in modules.items():
                self._tell_module(on_module, device_id, entry)
            pending = retry
            if not pending:
                break
        return {"tier": str(plan.tier), "label": plan.label, "modules": modules}

    @staticmethod
    def _tell_module(on_module: Callable[[str, dict], Any] | None, device_id: str, entry: dict) -> None:
        if on_module is None:
            return
        try:
            on_module(device_id, entry)
        except Exception:  # progress reporting never fails a write
            log.exception("slot progress callback failed")

    async def _slot_write_locked(
        self,
        plan: ModePlan,
        device_id: str,
        mt: ModuleTarget,
        target: Any,
        source: str,
        label: str,
        operator: bool,
    ) -> tuple[dict, bool]:
        """One module's slot write under its slot lock: the header read, one burst, the record. (entry, retry?)"""
        try:
            async with self._slot_lock(device_id):
                if self.locks.holder(target.address) is not None:
                    return {"status": "locked", "slot": mt.slot, "reason": "In use on a test page."}, False
                cmd, error, retryable = await self._slot_command(device_id, target, mt, source, label)
                if cmd is None:
                    return {"status": "failed", "slot": mt.slot, "reason": error}, retryable
                if operator:  # the reconciler's limit counts from the attempt, whatever it answers
                    self.slot_written[(device_id, mt.slot)] = self.clock.monotonic()
                results = await self._slot_burst([(device_id, mt, target, cmd)])
                result = results.get(id(cmd))
                entry = self._record_slot_result(device_id, mt.slot, plan, result)
                if entry["status"] == "applied" and not operator:
                    self.slot_written[(device_id, mt.slot)] = self.clock.monotonic()
                again = entry["status"] != "applied" and result is not None and result.status in _SLOT_RETRY
                return entry, again
        except asyncio.CancelledError:
            raise
        except Exception as e:  # one module's failure never stops the others
            log.exception("slot write for %s failed", device_id)
            return {"status": "failed", "slot": mt.slot, "reason": f"{type(e).__name__}: {e}"}, False

    def _record_slot_result(
        self, device_id: str, slot: str, plan: ModePlan, result: CommandResult | None
    ) -> dict:
        if result is None:
            return {"status": "failed", "slot": slot, "reason": "No result from the executor."}
        entry: dict[str, Any] = {"status": str(result.status), "slot": slot}
        if result.status is not CommandStatus.APPLIED:
            entry["reason"] = result.error or str(result.status)
            return entry
        value = result.readback.get(R.FRAME_CLASS, slot) if result.readback is not None else None
        digest = R.slot_hash(value)
        if digest is None:
            entry.update(status="mismatch", reason="The readback didn't include the slot's pattern.")
            return entry
        entry["hash"] = digest
        error = self._record_slot_value(device_id, slot, plan, value)
        if error:
            entry["reason"] = error
        return entry

    def _record_slot_value(self, device_id: str, slot: str, plan: ModePlan, value: Any) -> str | None:
        """Record a commissioned slot's readback (hash, origin "api", effect fields). Returns why saving failed."""
        digest = R.slot_hash(value)
        effects = R.slot_effects(value)
        error = None
        previous = self.recorded_slot(device_id, slot)
        self.slot_records[(device_id, slot)] = {
            "hash": digest,
            "mode": plan.mode_id,
            "tier": str(plan.tier),
            "origin": "api",
            "effects": effects,
        }
        try:
            if self.deps.record_slot is not None:
                self.deps.record_slot(device_id, slot, {"hash": digest, "origin": "api", "effects": effects})
            elif self.deps.record_slot_hash is not None:
                self.deps.record_slot_hash(device_id, slot, digest)
        except Exception as e:  # the overlay refused it: the write happened, the record didn't
            log.exception("record_slot_hash failed for %s %s", device_id, slot)
            error = f"Written, but the hash couldn't be saved: {e}"
        self._slot_recorded(device_id, slot, previous)
        self._maybe_lift_hold(plan.mode_id)
        return error

    def _maybe_lift_hold(self, mode_id: str) -> None:
        """A held mode whose records now equal the live T1 compile on every module: lifted, and the current look
        re-picked once (`_replan_soon` coalesces several modules into one T1 burst)."""
        if mode_id not in self._t1_hold:
            return
        try:
            still = self.t1_hold_for([mode_id])
        except Exception:
            log.exception("Couldn't check whether %s's slots hold the live design", mode_id)
            return
        if not still:
            self._t1_hold.discard(mode_id)
            log.info("%s's slots hold the live design again: T1 allowed", mode_id)
            self._replan_soon()

    def adopt_slot(self, device_id: str, slot: str, value: Any) -> bool:
        """A commissioned slot that already holds exactly what its T1 look compiles to now (a rewrite that landed
        although its verification was superseded by a newer command): record its readback, write nothing."""
        record = self.recorded_slot(device_id, slot)
        if record is not None and record["origin"] == "app":
            return False
        owner = self.t1_target(device_id, slot)
        if owner is None:
            return False
        plan, mt = owner
        if not mt.slot_hash or R.slot_hash(value) != mt.slot_hash:
            return False
        if R.effects_differ(R.slot_effects(mt.slot_value), R.slot_effects(value)):
            return False
        self._record_slot_value(device_id, slot, plan, value)
        return True

    def _slot_lock(self, device_id: str) -> asyncio.Lock:
        """One slot write per module at a time (a second one would supersede the first's verification)."""
        lock = self._slot_locks.get(device_id)
        if lock is None:
            lock = self._slot_locks[device_id] = asyncio.Lock()
        return lock

    # ---- recorded slots -----------------------------------------------------------------------------------------
    def recorded_slot(self, device_id: str, slot: str) -> dict | None:
        """The slot's record {hash, origin, effects}: a hand-painted record saved on the module page wins, then what
        this process commissioned, then the registry overlay's record."""
        entry = self._entry(device_id)
        saved = entry.slot_record(slot) if entry is not None and hasattr(entry, "slot_record") else None
        mine = self.slot_records.get((device_id, slot))
        if saved is not None and saved["origin"] == "app":
            return saved
        if mine is not None:
            return {"hash": mine["hash"], "origin": mine.get("origin", "api"), "effects": mine.get("effects")}
        return saved

    def _expect(self, device_id: str, mt: ModuleTarget, plan: ModePlan) -> list[StateRow]:
        """What readback must show. A T1 slot that was commissioned is judged by the hash recorded from its readback
        then (07 §6), not by the frame compiled now (a recalibration or design edit changes that without touching
        the device: the reconciler rewrites such a stale slot, rate-limited, and the expectation follows)."""
        expect = list(mt.expect) if mt.expect else list(mt.rows)
        if plan.tier is Tier.T1 and mt.slot:
            record = self.recorded_slot(device_id, mt.slot)
            if record is not None and record["origin"] == "api":
                expect = [r for r in expect if r.key != (R.FRAME_CLASS, mt.slot)]
                expect.append(R.slot_hash_expectation(mt.slot, record["hash"]))
                mt.expect = expect
        return expect

    def _slot_recorded(self, device_id: str, slot: str, previous: dict | None) -> None:
        """A slot's record changed: the current look's expectation for that module follows at once, and when the
        effect fields changed (the copy may now claim — or stop claiming — motion) the look is re-picked."""
        plan = self._plan
        record = self.recorded_slot(device_id, slot)
        if plan is None or record is None or plan.tier is not Tier.T1:
            return
        mt = plan.modules.get(device_id)
        if mt is None or mt.slot != slot:
            return
        target = self._targets.get(device_id)
        if previous is None or previous["hash"] != record["hash"]:
            expect = self._desired_expect(device_id, mt, plan)
            if target is not None and self.locks.holder(target.address) is None:
                self.tracker.set_desired(
                    target.address,
                    expect,
                    label=self._desired_label(plan),
                    source=self._source or "reconciler",
                    meta={"mode": plan.mode_id, "tier": str(plan.tier), "device_id": device_id},
                )
                self.dirty.add(target.address)
                if not self.quiet:
                    self.reconciler.check_module_soon(device_id, target)
        if R.animates((previous or {}).get("effects")) != R.animates(record.get("effects")):
            self._replan_soon()

    def _replan_soon(self) -> None:
        """Re-pick the current mode's best plan shortly (coalesced): slot records changed, so its tier, copy or
        motion may change. Skipped when a newer look was installed meanwhile (it was compiled with the records)."""
        if self._plan is None or self._stopped:
            return
        if self._replan_handle is not None:
            self._replan_handle.cancel()
        generation = self._generation
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        def fire() -> None:
            self._replan_handle = None
            if self._stopped or self._plan is None or self._generation != generation:
                return
            if self.quiet:  # commissioning or a repaint's window: the re-pick goes out once it ends
                self._replan_handle = loop.call_later(REPLAN_DELAY_S, fire)
                return
            plan = self._plan
            better, _ = self.compile_best(plan.mode_id)
            if better is None or (better.tier, better.feature, better.motion) == (
                plan.tier,
                plan.feature,
                plan.motion,
            ):
                return
            if better.tier != plan.tier:
                ladder = list(self.deps.ladder(plan.mode_id))
                up = ladder.index(better.tier) < ladder.index(plan.tier) if plan.tier in ladder else True
                self._tier_change(
                    plan.mode_id, plan.tier, better.tier, "the slots' records changed", "up" if up else "down"
                )
            self._show(better, "reconciler")

        self._replan_handle = loop.call_later(REPLAN_DELAY_S, fire)

    def stale_slot(self, device_id: str, slot: str, readback_value: Any) -> tuple[str, str] | None:
        """A commissioned T1 slot whose content still matches its record but no longer what the look compiles to
        now (recalibration, a canvas edit, an effect code enabled or disabled): (why, the compiled content's key).
        None when it's current, or when no T1 look owns the slot here."""
        owner = self.t1_target(device_id, slot)
        if owner is None:
            return None
        _plan, mt = owner
        compiled = R.slot_effects(mt.slot_value)
        if mt.slot_hash and mt.slot_hash != R.slot_hash(readback_value):
            return "the look now compiles to a different pattern (calibration or design changed)", str(
                mt.slot_hash
            )
        fields = R.effects_differ(compiled, R.slot_effects(readback_value))
        if fields:
            wanted = ", ".join(f"{k} {compiled.get(k, 0)}" for k in fields)
            return f"the look's effect fields changed ({wanted})", f"{mt.slot_hash}:{wanted}"
        return None

    def invalidate_t1_plans(self) -> None:
        """Compile inputs changed (capabilities re-read): T1 plans are compiled again when next needed."""
        self._t1_plans.clear()
        self._t1_failed.clear()

    async def _slot_burst(
        self, batch: list[tuple[str, ModuleTarget, Any, Command]]
    ) -> dict[int, CommandResult]:
        groups: dict[int, tuple[CommandExecutor, list[Command]]] = {}
        for device_id, _mt, target, cmd in batch:
            self._track(cmd, device_id, "slot")
            groups.setdefault(id(target.executor), (target.executor, []))[1].append(cmd)
        results: dict[int, CommandResult] = {}
        for executor, cmds in groups.values():
            for result in await executor.burst(cmds):
                results[id(result.command)] = result
        return results

    async def _slot_command(
        self, device_id: str, target: Any, mt: ModuleTarget, source: str, label: str
    ) -> tuple[Command | None, str, bool]:
        """One slot-object write for `mt.slot`, with the header copied from the device's own readback (04).
        Returns (command, why not, whether trying again later could help)."""
        rr = None
        for attempt in range(HEADER_READ_ATTEMPTS):
            if attempt:
                await self.clock.sleep(self.deps.settings.executor.min_write_interval_s)
            rr = await target.driver.read_state(target.address, purpose=Purpose.ENGINE)
            if rr.ok or not rr.outcome.retryable:
                break
        if not rr.ok:
            why = rr.error or str(rr.outcome)
            return (
                None,
                f"Couldn't read the slot's current header first: {why}. Nothing sent.",
                rr.outcome.retryable,
            )
        current = rr.get(R.FRAME_CLASS, mt.slot)
        entries = R.frame_entries(mt.slot_value)
        if entries is None:
            return None, "The compiled slot content isn't a pattern object. Nothing sent.", False
        existing = R.frame_entries(current)
        inner = (mt.slot_value or {}).get(R.FRAME_CLASS, mt.slot_value) or {}
        effects = {k: inner[k] for k in R.EFFECT_FIELDS if isinstance(inner.get(k), int)}
        try:
            value = R.frame_value(
                current if existing is not None else mt.slot_value,
                entries,
                expected_length=len(existing) if existing is not None else None,
                effects=effects,
            )
        except R.PayloadError as e:
            return None, f"{e}. Nothing sent.", False
        row = R.slot_write(mt.slot, value)
        cmd = Command(
            target.address, [row], Purpose.ENGINE, label=f"{label} → {mt.slot}", expect=[row], source=source
        )
        return cmd, "", False

    def slot_expectations(self, device_id: str) -> list[tuple[str, str, Tier, str]]:
        """(slot, expected hash, tier, mode) the true-up checks on this module: slots with a recorded hash
        (commissioned here, or saved in the registry overlay), and the current plan's T2 slot. A slot recorded as
        hand-painted in the app is T2 (alert only, never rewritten); a commissioned one is T1 (rewritten on drift)
        when a canvas mode owns it here."""
        out: dict[str, tuple[str, str, Tier, str]] = {}
        entry = self._entry(device_id)
        slots = set(((entry.slot_hashes if entry is not None else {}) or {}).keys())
        slots |= {slot for dev, slot in self.slot_records if dev == device_id}
        for slot in sorted(slots):
            record = self.recorded_slot(device_id, slot)
            if record is None:
                continue
            owner = self.t1_target(device_id, slot) if record["origin"] == "api" else None
            mine = self.slot_records.get((device_id, slot))
            if owner is None and mine is not None and record["origin"] == "api":
                out[slot] = (slot, record["hash"], Tier.T1, mine["mode"])
            else:
                out[slot] = (
                    slot,
                    record["hash"],
                    Tier.T1 if owner else Tier.T2,
                    owner[0].mode_id if owner else "",
                )
        plan = self._plan
        if plan is not None and plan.tier in (Tier.T1, Tier.T2) and device_id in plan.modules:
            mt = plan.modules[device_id]
            if mt.slot and mt.slot_hash and (plan.tier is Tier.T2 or mt.slot not in out):
                # T1 before a recorded readback hash: the compiled content's hash. T2: the recorded hand-paint.
                out[mt.slot] = (mt.slot, mt.slot_hash, plan.tier, plan.mode_id)
        return list(out.values())

    def t1_target(self, device_id: str, slot: str) -> tuple[ModePlan, ModuleTarget] | None:
        """The T1 module target that owns `slot` on `device_id`, compiled lazily and cached until the registry (its
        calibration, flags, records) changes, the capabilities are re-read, or the next commissioning — so a drift
        or stale rewrite always writes what the look compiles to now."""
        registry = self.deps.registry()
        if registry is not self._t1_key:  # identity, holding a reference (a bare id() could be reused)
            self._t1_key = registry
            self.invalidate_t1_plans()
        for mode_id in self.deps.modes():
            plan = self._t1_plans.get(mode_id)
            if plan is None:
                if mode_id in self._t1_failed:
                    continue
                try:
                    if (
                        Tier.T1 not in self.deps.ladder(mode_id)
                        or not self.deps.tier_allowed(mode_id, Tier.T1)[0]
                    ):
                        continue
                    plan = self._t1_plans[mode_id] = self.deps.compile(mode_id, Tier.T1)
                except (PlanError, KeyError, LookupError):
                    self._t1_failed.add(mode_id)  # until the next commissioning
                    continue
            mt = plan.modules.get(device_id)
            if mt is not None and mt.slot == slot and mt.slot_value is not None:
                return plan, mt
        return None

    async def rewrite_slot(self, device_id: str, slot: str, *, source: str) -> str:
        """Rewrite one drifted or stale T1 slot (rate-limited by the caller; one slot write per module at a time).
        Returns the command status or why not."""
        async with self._slot_lock(device_id):
            return await self._rewrite_slot(device_id, slot, source=source)

    async def _rewrite_slot(self, device_id: str, slot: str, *, source: str) -> str:
        record = self.recorded_slot(device_id, slot)
        if record is not None and record["origin"] == "app":
            return "it was hand-painted in the Hubspace app, and those are never rewritten"
        found = self.t1_target(device_id, slot)
        target = self._targets.get(device_id) or self._resolve(device_id)
        if found is None or target is None:
            return "no canvas content is known for this slot"
        if self.locks.holder(target.address) is not None:
            return "locked"
        plan, mt = found
        cmd, error, _retryable = await self._slot_command(
            device_id, target, mt, source, f"slot rewrite: {plan.label}"
        )
        if cmd is None:
            return error
        self.slot_written[(device_id, slot)] = self.clock.monotonic()
        self._track(cmd, device_id, "slot")
        [result] = await target.executor.burst([cmd])
        entry = self._record_slot_result(device_id, slot, plan, result)
        return entry["status"]

    # ---- publishing -------------------------------------------------------------------------------------------
    def _publish_soon(self) -> None:
        """Push the state on topic "canopy" soon: coalesced, at most every PUBLISH_MIN_INTERVAL_S."""
        if self._publish_scheduled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._publish_scheduled = True
        wait = self._last_publish_mono + PUBLISH_MIN_INTERVAL_S - self.clock.monotonic()
        if wait > 0:
            loop.call_later(wait, self._publish)
        else:
            loop.call_soon(self._publish)

    def _publish(self) -> None:
        self._publish_scheduled = False
        self._last_publish_mono = self.clock.monotonic()
        hub = self.deps.hub
        if hub is None or getattr(hub, "client_count", 1) == 0:
            self._last_published = None  # a subscriber gets the snapshot on subscribe
            return
        try:
            data = self.state().to_json()
        except Exception:
            log.exception("canopy state failed")
            return
        if data != self._last_published:
            self._last_published = data
            hub.publish(TOPIC, data)


def _current_task() -> asyncio.Task | None:
    try:
        return asyncio.current_task()
    except RuntimeError:  # no running loop
        return None


def _clock_time(t: float | None) -> str:
    """A wall time as the box's local HH:MM (alerts staff read at the venue)."""
    if t is None:
        return "?"
    try:
        return datetime.fromtimestamp(t, UTC).astimezone().strftime("%H:%M")  # the box's local time
    except (OverflowError, OSError, ValueError):
        return "?"


def engine_from_services(
    services: Any,
    *,
    compile: Compiler,
    ladder: Ladder,
    modes: Callable[[], list[str]],
    tier_allowed: TierAllowed | None = None,
    rehearsal_compile: Callable[[str], ModePlan] | None = None,
    repaint_target: Callable[[str], Any] | None = None,
    looks_generation: Callable[[], int] | None = None,
    on_repaint: Callable[[str, dict], Any] | None = None,
    repaint_ready: Callable[[], Awaitable[Any]] | None = None,
) -> CanopyEngine:
    """The engine wired to the Services container (the lead's one-liner). Slot hashes are saved to the registry
    overlay (`slot_hashes` per device, merged), which needs CONDUCTOR_DATA_DIR; stepped rehearsal reports go to
    <data>/bench/rehearsals."""

    def record_slot_hash(device_id: str, slot: str, digest: str) -> None:
        record_slot(device_id, slot, {"hash": digest, "origin": "api", "effects": None})

    def record_slot(device_id: str, slot: str, record: dict) -> None:
        entry = services.registry.get(device_id)
        patch = entry.slot_patch(
            slot, digest=record["hash"], origin=record.get("origin", "api"), effects=record.get("effects")
        )
        services.update_registry(device_id, patch)

    return CanopyEngine(
        EngineDeps(
            registry=lambda: services.registry,
            target=services.target,
            tracker=services.tracker,
            telemetry=services.telemetry,
            hub=services.hub,
            clock=services.clock,
            settings=services.settings,
            locks=services.locks,
            compile=compile,
            ladder=ladder,
            modes=modes,
            tier_allowed=tier_allowed or _all_allowed,
            record_slot_hash=record_slot_hash,
            simulated=lambda: bool(services.engine_driver.simulated),
            record_slot=record_slot,
            state_dir=Path(services.data) / "state" if getattr(services, "data", None) else None,
            rehearsal_compile=rehearsal_compile,
            rehearsal_dir=(Path(services.data) / "bench" / REHEARSAL_DIR)
            if getattr(services, "data", None)
            else None,
            repaint_target=repaint_target,
            looks_generation=looks_generation,
            on_repaint=on_repaint,
            repaint_ready=repaint_ready,
        )
    )
