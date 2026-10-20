"""Show wiring: the canopy engine on the Services container (P1 engine × P2 looks), built in the lifespan.

- **Capabilities** come from each canopy module's own profile through the engine driver (07 §2: startup never waits
  on Afero). A background task reads every module at start, then keeps retrying the ones that haven't answered with
  backoff (`CAPS_RETRY_S`), and reads again when a module comes back online, when an outage ends (the engine's
  recovery and the driver's `on_recovery` hook) and on demand (`request_refresh`: /api/devices?refresh=1). A module
  without capabilities shows as "Needs attention" on /ops (`attention()`) and is left out of every look but the safe
  one (`ModePlan.skipped`); the safe look (Lowcountry Calm) compiles with conservative defaults. As soon as new
  capabilities arrive the current mode is compiled again — or, before the first look, the idle mode is shown. The
  reads also fill `services.extras["capabilities"]` (the cache /api/devices and the checklist use) when the bench
  target is the same device. A module with no device registered on the engine's driver is never read or retried; it
  has its own attention line (register one on /ops/accounts) and is left out of every look, the safe one too (D62).
- The compile context is rebuilt per change from the live registry (calibration, flags, recorded slot hashes) and
  each module's engine address, so a kit's controller map (D54) applies only on the kit it was proven on.
- **Head count** (`conductor.framemap.head_check`): a module whose frame can't take per-head looks is flagged on the
  tracker (NEEDS_ATTENTION). A module with its own proven controller map is healthy; a longer frame without one is
  flagged as controller capacity with no proven map (D54), not as missing pucks.
- **Tier rules** (04 ladder, 07 §6): the looks compiler refuses T1 without Gate C (`flags.t1_frames`) and T2 without
  hashes recorded as hand-painted in the Hubspace app; on top of that a T1 mode is only *shown* once every module's
  slot has a hash recorded by commissioning (origin "api"), so the kiosk never selects a slot that was never
  written — and a slot recorded as hand-painted never counts as commissioned. Commissioning itself only needs the
  capability and bench-flag checks.
- **Stepped looks (D65)** are sim-first: on a non-simulated driver the stepped tier is refused unless
  `motion.stepped.live_enabled` is set (Alon flips it after the stepped rehearsal, Gate R) or that rehearsal is
  running for that very mode (`engine.rehearsal_mode`), so the ladder lands on the mode's still module-level rung.
  Loading checks that no stepped run outlasts `kiosk.idle_return_s` and, when `motion.idle` is on (D75: off as
  shipped), that the idle mode has a stepped rung with room for two paced steps in `breathe_run_s`
  (`_check_stepped`). The idle look may carry a rung (D75): start-up and an idle return never start a run unless
  `motion.idle` is on.
- **The stepped rehearsal's design** (`rehearsal_modes`, conductor/rehearsal.py): the mode's own stepped technique
  when modes.yaml has one, else its module-level design (the `modules` sample: same canvas, `at: centre`, same
  brightness) stepped at the D65 defaults, with moving copy that says staff are testing and names the flow
  (`motion_words`). It is checked by the same schema and copy rules as modes.yaml, and only used while the
  rehearsal of that mode runs: then the mode's ladder has the stepped rung first (`ladder`, `compile`).
- **The Design Lab's try-on (D66 Slice B)** is the only other override: while `engine.trial` is of a mode, that
  mode's `modules` and `strand` tiers compile from the trial's patched modes and recipes (`compile`); the engine never
  asks for any other tier of it then (`compile_best` skips T1/T2/stepped). `ladder`, `tier_allowed`, `_record` and
  every T1 compile keep the git design, so the slot checks never see a try-on. A capability re-pick that would ADD a
  module to the try-on's plan (one registered, or newly read, mid-try-on: its confirm never covered it) ends the
  try-on instead (`_joining_trial`): the approved look goes to every module; a re-pick that keeps the set keeps it.
- In sim, the slots are commissioned automatically once every module's capabilities are read, so demos run T1
  exactly as the show would after install-day commissioning. On hardware commissioning is an explicit ops action.
- **The operator repaint (D68 PR B2).** `repaint_plan(trio)` says, per painted mode, which modules a publish would
  repaint (their own api record, Gate C at their address and — on a longer frame — a valid map; one that can't take
  T1 drops out alone) and compiles their T1 plan; `repaint_target(mode)` is that against the live looks now (the
  engine's job recomputes it when the scene's window opens). The engine's hook writes the job's bookkeeping into the
  published-looks overlay (`conductor.looks.live.note_repaint`). After a restart, as the engine starts and before
  any profile is read, the overlay's gate (`repaints`) seeds the engine's and its queued repaints go back to the
  engine at their due times, over the modules with a commissioned (api) record for the slot (`_restore_repaints`):
  their slots are pending at once (the reconciler leaves them alone) and a publish carries the scenes over. A scene's
  window never opens before the show's start-up is over (`_repaint_ready`: the first round of capability reads; in
  sim, the start-up commissioning, which writes the live design into every slot itself, so it replaces a restored
  job and `_requeue_repaints` then re-queues only what is still left).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from conductor.capabilities import Capabilities, detect
from conductor.engine_api import Tier
from conductor.framemap import head_check, module_heads
from conductor.gate import Purpose

log = logging.getLogger(__name__)
CAPS_KEY = "capabilities"
ENGINE_KEY = "canopy"
CAPS_RETRY_S = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 60.0)  # backoff while a module has no capabilities
CAPS_FETCH_TIMEOUT_S = 15.0
CAPS_IDLE_S = (
    300.0  # with every module read, the loop only wakes for a trigger (or this often, to stay alive)
)


def _check_idle_scene(scene: str | None, modes: list[str]) -> None:
    """`kiosk.idle_scene` must name a mode in config/modes.yaml (D60): a typo would make every idle reset fail."""
    if scene is not None and scene not in modes:
        from conductor.config import ConfigError

        raise ConfigError(
            f"config/conductor.yaml → kiosk → idle_scene: {scene!r} isn't a mode; use one of "
            f"{', '.join(modes)} (config/modes.yaml), or null to stay on the current look."
        )


STEPPED_LIVE_OFF = "stepped looks are off on live hardware until the stepped rehearsal (Gate R) passes"
REHEARSAL_FEATURE = (
    "Staff are testing the canopy: each Hubspace strand is sent its next colour every few seconds, so the palette "
    "moves {words}."
)


def _check_stepped(settings: Any, modes_file: Any) -> None:
    """Stepped looks against the kiosk settings: a run ends before the idle hold returns, and motion.idle needs an
    idle look with a stepped rung and room for two paced steps. The idle look may carry a rung (D75); the engine
    never starts a run on start-up or an idle return unless motion.idle is on."""
    from conductor.config import ConfigError

    kiosk = settings.kiosk
    for mode_id, mode in modes_file.modes.items():
        stepped = mode.techniques.stepped
        if stepped is not None and "stepped" in mode.ladder and stepped.run_s > kiosk.idle_return_s:
            raise ConfigError(
                f"config/modes.yaml → {mode_id} → techniques → stepped → run_s: {stepped.run_s:g} s is longer than "
                f"kiosk.idle_return_s ({kiosk.idle_return_s:g} s), so the idle hold would cut every run short. Make "
                "the run shorter."
            )
    idle_motion = settings.motion.idle
    if idle_motion is None:
        return
    idle = modes_file.modes.get(kiosk.idle_mode)
    if idle is None or "stepped" not in idle.ladder:
        raise ConfigError(
            f"config/conductor.yaml → motion → idle: the idle mode {kiosk.idle_mode!r} (kiosk → idle_mode) has no "
            "stepped rung to breathe with; give it one in config/modes.yaml or set motion.idle to null"
        )
    # the runner's paced step on nine (ProgramRunner._effective_step, without a ripple; design_lab.stepped can't be
    # imported here: it imports this module)
    from conductor.sequence import GRID, planned_writes_per_min

    paced = settings.motion.stepped
    rung = idle.techniques.stepped
    step = max(rung.step_s, len(GRID) * 60.0 / planned_writes_per_min(paced), paced.min_step_s)
    step = math.ceil(step * 1000 - 1e-6) / 1000  # whole milliseconds, as the runner rounds it
    if int(idle_motion.breathe_run_s / step + 1e-6) < 2:
        raise ConfigError(
            f"config/conductor.yaml → motion → idle → breathe_run_s: {idle_motion.breathe_run_s:g} s fits fewer "
            f"than two {step:g} s steps of {kiosk.idle_mode}'s stepped rung on nine strands (the first step is the "
            f"still look), so the breath would never move; make it at least {2 * step:g} s"
        )


def _is_registered(services: Any, entry: Any) -> bool:
    return bool(services.registry.address(entry.id, services.engine_driver.name))


def registered_canopy_ids(services: Any) -> frozenset[str]:
    """The canopy modules with a device registered on the engine's driver (D62): the only ones that are ever read,
    retried or driven. A position without one is left out of every look."""
    return frozenset(e.id for e in services.registry.canopy_modules() if _is_registered(services, e))


def split_canopy_positions(services: Any) -> tuple[list[str], list[str]]:
    """(registered, unregistered) canopy module ids, each in registry order (`registered_canopy_ids`' predicate)."""
    registered: list[str] = []
    unregistered: list[str] = []
    for entry in services.registry.canopy_modules():
        (registered if _is_registered(services, entry) else unregistered).append(entry.id)
    return registered, unregistered


@dataclass(frozen=True)
class RepaintTarget:
    """One painted mode's slot against a trio (`ShowWiring.repaint_plan`): the modules whose api record differs from
    the trio's T1 compile (`listed`), how many could be repainted (`eligible`: their own api record, Gate C and — on
    a longer frame — map), of `total`; the modules whose slot is hand-painted in the app (`app`), not commissioned yet
    (`uncommissioned`) or commissioned (`commissioned`); and (B2) the T1 plan compiled over the eligible modules, which
    the operator repaint writes, with the mode's label in the trio."""

    slot: str
    listed: list[str]
    eligible: int
    total: int
    app: list[str]
    uncommissioned: list[str]
    commissioned: list[str]
    plan: Any = field(default=None, compare=False, repr=False)
    label: str = ""
    eligible_ids: list[str] = field(default_factory=list)
    # commissioned (api) modules whose capabilities haven't been read yet: nothing to compile their frame with, so
    # they are never `listed` — and never counted as holding the design (the repaint says so; the reconciler takes
    # them once read)
    unread: list[str] = field(default_factory=list)


class ShowWiring:
    def __init__(self, services: Any):
        from conductor.looks.live import live_looks

        self.services = services
        # The live looks (D68): the approved trio, or the one the Design Lab published (conductor/looks/live.py);
        # `install_looks` swaps both in place on a publish.
        looks = live_looks(services)
        self.modes_file = looks.trio.modes
        self.recipes = looks.trio.recipes
        _check_idle_scene(services.settings.kiosk.idle_scene, list(self.modes_file.modes))
        _check_stepped(services.settings, self.modes_file)
        self.caps: dict[str, Capabilities] = {}
        self.caps_errors: dict[str, str] = {}  # device id → why its profile couldn't be read (last attempt)
        self.slot_readback: dict[str, dict[str, Any]] = {}
        self.commissioning = False
        self.engine: Any = None
        self.last_commission: dict | None = None
        self.caps_ready = asyncio.Event()  # set once every canopy module's capabilities have been read
        self._task: asyncio.Task | None = None
        self._caps_task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._wanted: set[str] = set()  # modules to read again at the next wake (triggers)
        self._offline: set[str] = set()  # modules last seen offline (a readback said available: false)
        self._reapply = False  # new capabilities arrived while the engine couldn't take a request
        self._head_problems: dict[
            str, str
        ] = {}  # device id → the head-count problem flagged from its profile
        self._cleanup: list[Any] = []
        # mode id → the modes file a stepped rehearsal of it compiles against
        self._rehearsal_modes: dict[str, Any] = {}
        self._requeued = False  # the overlay's queued operator repaints went back to the engine (D68 PR B2)
        self._first_round = (
            asyncio.Event()
        )  # the capability loop's first round was applied (B2: `_repaint_ready`)

    # ---- capabilities --------------------------------------------------------------------------------------------
    def _registered(self, entry: Any) -> bool:
        """The module has a device on the engine's driver (D62): one without is never read, retried or driven."""
        return _is_registered(self.services, entry)

    def missing(self) -> list[str]:
        """Registered canopy modules whose capabilities haven't been read yet."""
        return [
            e.id
            for e in self.services.registry.canopy_modules()
            if e.id not in self.caps and self._registered(e)
        ]

    async def refresh_capabilities(self, device_ids: list[str] | None = None) -> list[str]:
        """Read the profiles of `device_ids` (default: every registered canopy module) concurrently. Never raises; a
        module that can't be read keeps its reason in `caps_errors`. Returns the modules whose capabilities are new
        or changed. A module with no device registered is never read (nothing to ask, so no warning every retry)."""
        services = self.services
        entries = []
        for e in services.registry.canopy_modules():
            if device_ids is not None and e.id not in device_ids:
                continue
            if not self._registered(e):
                if device_ids is not None:
                    self.caps_errors[e.id] = "no Hubspace device is registered for it on this runtime"
                continue
            entries.append(e)
        results = await asyncio.gather(*(self._read_one(e) for e in entries))
        changed = [device_id for device_id in results if device_id]
        if changed and self.engine is not None:
            self.engine.invalidate_t1_plans()
        if not self.missing():
            self.caps_ready.set()
        return changed

    async def _read_one(self, entry: Any) -> str | None:
        services = self.services
        try:
            target = services.target(entry.id, Purpose.ENGINE)
            info = await asyncio.wait_for(
                target.driver.fetch_profile(target.address, purpose=Purpose.ENGINE), CAPS_FETCH_TIMEOUT_S
            )
        except TimeoutError:
            self.caps_errors[entry.id] = "reading its profile took longer than 15 s"
            return None
        except Exception as exc:  # noqa: BLE001 - an unreachable module shows as needs attention, not a crash
            log.warning("Couldn't read %s's profile: %s", entry.id, exc)
            self.caps_errors[entry.id] = str(exc) or type(exc).__name__
            return None
        caps = detect(info.functions, info.states)
        before = self.caps.get(entry.id)
        self.caps[entry.id] = caps
        self.caps_errors.pop(entry.id, None)
        self.slot_readback[entry.id] = {
            r.function_instance: r.value
            for r in info.states
            if r.function_class == "color-sequence-v2" and r.function_instance
        }
        problem = head_check(entry, target.address, caps, module_heads(services.config_dir)).problem
        previous = self._head_problems.pop(entry.id, None)
        if previous and previous != problem:
            services.tracker.clear_problem(
                target.address, previous
            )  # fixed (e.g. power-cycled with all strings)
        if problem:
            services.tracker.flag_problem(target.address, problem)
            self._head_problems[entry.id] = problem
        self._share(entry.id, target, caps)
        return entry.id if before != caps else None

    def _share(self, device_id: str, target: Any, caps: Capabilities) -> None:
        """Fill the /api/devices capability cache when its (bench) target is this same device."""
        services = self.services
        try:
            bench = services.target(device_id, Purpose.BENCH)
        except LookupError:
            return
        if (bench.driver.name, bench.address) != (target.driver.name, target.address):
            return
        services.extras.setdefault(CAPS_KEY, {})[device_id] = {
            "caps": caps,
            "error": None,
            "simulated": target.simulated,
            "driver": target.driver.name,
            "address": target.address,
            "fetched_at": services.clock.now(),
            "fetched_mono": services.clock.monotonic(),
        }

    def request_refresh(self, device_ids: list[str] | None = None) -> None:
        """Read these modules' profiles again now (default: all), in the background."""
        self._wanted.update(device_ids if device_ids is not None else [e.id for e in self._modules()])
        self._wake.set()

    def attention(self) -> list[str]:
        """ "Needs attention" lines for /ops: modules with no device registered, and modules whose capabilities
        haven't been read."""
        return [message for message, _link in self.attention_items()]

    def attention_items(self) -> list[tuple[str, str]]:
        """`attention()` with the /ops page that fixes each line."""
        out = []
        for entry in self._modules():
            name = entry.label or entry.id
            if not self._registered(entry):
                message = (
                    f"{name} has no Hubspace device registered on this runtime, so the canopy leaves it out of "
                    "every look, the safe one included. Register one on /ops/accounts."
                )
                out.append((message, "/ops/accounts/"))
                continue
            if entry.id in self.caps:
                continue
            why = self.caps_errors.get(entry.id) or "not read yet"
            message = (
                f"{name}: its Hubspace profile hasn't been read ({why}). The canopy leaves it out of every look but "
                "the safe one and keeps retrying; Refresh on the device list retries now."
            )
            out.append((message, "/ops/"))
        return out

    def _modules(self) -> list[Any]:
        return list(self.services.registry.canopy_modules())

    def _registered_ids(self) -> frozenset[str]:
        return registered_canopy_ids(self.services)

    async def _caps_loop(self) -> None:
        attempt = 0
        first = True
        registered = self._registered_ids()
        while True:
            todo = sorted(set(self.missing()) | self._wanted)
            self._wanted.clear()
            changed = await self.refresh_capabilities(todo) if todo else []
            # A module registered (or unregistered) since the last round changes the current look even when its
            # profile read fails: the safe look covers it with assumed capabilities (D36, D62).
            now = self._registered_ids()
            moved, registered = now != registered, now
            if changed or first or moved or self._reapply:
                try:
                    await self._apply_new_caps(first=first)
                finally:
                    self._first_round.set()
            first = False
            if self.missing():
                delay = CAPS_RETRY_S[min(attempt, len(CAPS_RETRY_S) - 1)]
                attempt += 1
            else:
                delay, attempt = CAPS_IDLE_S, 0
            if await self._sleep_or_wake(delay):
                attempt = 0  # a trigger (recovery, a module back online, ops): retry from the short end

    async def _sleep_or_wake(self, seconds: float) -> bool:
        """Sleep on the services clock; True when a trigger woke the loop early."""
        if self._wake.is_set():
            self._wake.clear()
            return True
        sleeper = asyncio.ensure_future(self.services.clock.sleep(seconds))
        waiter = asyncio.ensure_future(self._wake.wait())
        try:
            await asyncio.wait({sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sleeper, waiter):
                task.cancel()
            await asyncio.gather(sleeper, waiter, return_exceptions=True)
        woke = self._wake.is_set()
        self._wake.clear()
        return woke

    async def _apply_new_caps(self, *, first: bool) -> None:
        """New capabilities: show the idle mode if nothing is showing yet, else compile the current mode again (a
        module that was left out, or planned with assumed capabilities, now gets its real look)."""
        engine = self.engine
        if engine is None or engine.stopped:
            return
        if engine.commissioning:
            self._reapply = True
            return
        self._reapply = False
        # Painted scenes whose commissioned slots don't hold the live design run below T1 until they do (D68): the
        # truth is recomputed from the records whenever new capabilities let T1 compile (a restart after a publish or
        # a fallback, a module read for the first time).
        seed = getattr(engine, "seed_t1_hold", None)
        if seed is not None:
            seed()
        plan = engine.current_plan()
        if plan is None:  # the first look: one burst, never a ripple (the engine sends a first look that way)
            await engine.request_mode(self.services.settings.kiosk.idle_mode, source="idle")
        elif not first:
            joined = self._joining_trial(plan)
            if joined:
                # A module the try-on's confirm never named would get the draft: end it instead. Every module (the new
                # one too) gets the approved look; ops applies again and confirms the new set (D66).
                names = ", ".join(joined)
                await engine.end_trial(
                    source="the show's capability loop",
                    why=f"a canopy module joined the look mid-try-on ({names}; registered or newly read), and its "
                    "confirm didn't cover it",
                )
                return
            await engine.request_mode(plan.mode_id, source="reconciler")

    def _joining_trial(self, plan: Any) -> list[str]:
        """The modules a re-pick would ADD to the running try-on's plan (labels), else []. A re-pick that keeps the
        set, or shrinks it, keeps the try-on: every module it writes was in its confirm."""
        engine = self.engine
        trial = getattr(engine, "trial", None)
        if trial is None or plan.mode_id != trial.mode_id:
            return []
        try:
            again, _why = engine.compile_best(plan.mode_id)
        except Exception:  # noqa: BLE001 - the re-pick itself reports the failure
            return []
        if again is None:
            return []
        added = [d for d in again.modules if d not in plan.modules]
        labels = []
        for device_id in added:
            try:
                entry = self.services.registry.get(device_id)
                labels.append(entry.label or device_id)
            except (KeyError, LookupError):
                labels.append(device_id)
        return labels

    def _on_event(self, event: dict) -> None:
        """A module that comes back online gets its profile read again (it may have been power-cycled: the head
        count is detected at power-up)."""
        if event.get("kind") != "readback" or "available" not in event:
            return
        entry = self.services.registry.logical_for(str(event.get("device_id")))
        if entry is None or entry.role != "canopy_module":
            return
        available = event.get("available")
        if available is False:
            self._offline.add(entry.id)
        elif available is True and entry.id in self._offline:
            self._offline.discard(entry.id)
            self.request_refresh([entry.id])

    def _on_driver_recovery(self, _account: str = "") -> None:
        self.request_refresh()

    # ---- compiling -----------------------------------------------------------------------------------------------
    def context(self, modes: Any = None, recipes: Any = None, *, rehearsing: bool = False) -> Any:
        """The compile context from the live registry. `modes`/`recipes`: another modes file / recipes (the
        rehearsal's design, a Design Lab try-on's patch); None = the show's own (the live looks: git or published).
        Per-light motion (D80): always in the simulator; live with motion.lights.live_enabled or, `rehearsing`, for
        the look an operator is rehearsing (each module still needs Gate C and a map)."""
        from conductor.looks import CompileContext

        services = self.services
        simulated = services.engine_driver.simulated
        addresses = {
            entry.id: services.registry.address(entry.id, services.engine_driver.name)
            for entry in services.registry.canopy_modules()
        }
        flags = None
        if not simulated:
            flags = {}
            for entry in services.registry.canopy_modules():
                flags[entry.id] = entry.flags_for(addresses[entry.id])
        return CompileContext.from_settings(
            registry=services.registry,
            capabilities=self.caps,
            settings=services.settings,
            slot_readback=self.slot_readback,
            flags=flags,
            modes=modes if modes is not None else self.modes_file,
            recipes=recipes if recipes is not None else self.recipes,  # never the git cache (D68)
            config_dir=services.config_dir,
            addresses=addresses,
            lights=simulated or rehearsing or services.settings.motion.lights.live_enabled,
            lights_slot=services.settings.motion.lights.slot,
        )

    def compile(self, mode_id: str, tier: Tier) -> Any:
        from conductor.looks import compile_mode

        # A module without capabilities is left out of the plan (the safe look assumes conservative ones); only
        # when no module has been read does compile_mode refuse (PlanError).
        if tier is Tier.STEPPED and self._rehearsing(mode_id):
            return self.rehearsal_plan(mode_id)
        # The Design Lab's try-on (D66 Slice B): its scene's module-level and strand rungs only, from the draft's own
        # patched modes AND recipes. Every other tier — T1 above all, so t1_target, slot_expectations and stale_slot
        # keep judging the slots by the git design — and the ladder, tier_allowed and records stay on git.
        trial = self._trial_for(mode_id)
        if trial is not None and tier in (Tier.T3, Tier.STRAND):
            return compile_mode(mode_id, tier, self.context(trial.modes, trial.recipes))
        return compile_mode(mode_id, tier, self.context())

    def _trial_for(self, mode_id: str) -> Any | None:
        """The engine's try-on when it is of `mode_id` (None otherwise)."""
        trial = getattr(self.engine, "trial", None) if self.engine is not None else None
        return trial if trial is not None and trial.mode_id == mode_id else None

    def ladder(self, mode_id: str) -> list[Tier]:
        from conductor.looks import ladder

        if self._rehearsing(mode_id):
            return ladder(mode_id, self.rehearsal_modes(mode_id))
        return ladder(mode_id, self.modes_file)

    # ---- the stepped rehearsal's design (Gate R) -----------------------------------------------------------------
    def _rehearsing(self, mode_id: str) -> bool:
        return self.engine is not None and getattr(self.engine, "rehearsal_mode", None) == mode_id

    def rehearsal_plan(self, mode_id: str) -> Any:
        """The stepped plan a rehearsal of `mode_id` runs (PlanError when the mode has nothing to step)."""
        from conductor.looks import compile_mode

        return compile_mode(
            mode_id, Tier.STEPPED, self.context(self.rehearsal_modes(mode_id), rehearsing=True)
        )

    def rehearsal_modes(self, mode_id: str) -> Any:
        """The modes file with `mode_id` given a stepped rung first in its ladder: its own stepped technique, or
        its module-level design stepped at the D65 defaults (SteppedTechnique's), checked like modes.yaml."""
        cached = self._rehearsal_modes.get(mode_id)
        if cached is not None:
            return cached
        from conductor.config import ConfigError
        from conductor.engine_api import PlanError
        from conductor.looks import parse_modes
        from conductor.looks.schema import motion_words

        try:
            mode = self.modes_file.mode(mode_id)
        except KeyError as e:
            raise PlanError(str(e.args[0])) from None
        raw = self.modes_file.model_dump(mode="json", exclude_unset=True)
        entry = raw["modes"][mode_id]
        if mode.techniques.stepped is None:
            sample = mode.techniques.modules.sample if mode.techniques.modules is not None else None
            if sample is None or sample.at != "centre":
                raise PlanError(
                    f"{mode.label} has no module-level design sampled at the module centres to step (a modules rung "
                    "with sample: {canvas, at: centre})"
                )
            # (a design the stepped technique can't take, e.g. a gradient, is refused by the schema check below)
            words = motion_words(sample.canvas, self.context().canopy, "forward")
            entry["techniques"]["stepped"] = {
                "canvas": entry["techniques"]["modules"]["sample"]["canvas"],
                "brightness": sample.brightness,
                "moving_feature": REHEARSAL_FEATURE.format(words=words),
            }
        entry["ladder"] = ["stepped", *(t for t in entry["ladder"] if t != "stepped")]
        ctx = self.context()
        try:
            modes = parse_modes(
                raw, recipes=ctx.recipes, canopy=ctx.canopy, path="the stepped rehearsal's design"
            )
        except ConfigError as e:
            raise PlanError(str(e)) from None
        self._rehearsal_modes[mode_id] = modes
        return modes

    def mode_ids(self) -> list[str]:
        return list(self.modes_file.modes)

    # ---- the live looks (D68) --------------------------------------------------------------------------------------
    def install_looks(self, modes: Any, recipes: Any) -> None:
        """A publish (conductor.looks.live.install_looks): the modes file and recipes every compile reads from now,
        and the stepped rehearsal's designs (derived from the old modes) dropped. Synchronous."""
        self.modes_file = modes
        self.recipes = recipes
        self._rehearsal_modes.clear()

    def repaint_plan(self, trio: Any, *, only: Any = None) -> dict[str, RepaintTarget]:
        """Per painted mode (a slot and T1 on its ladder; `only`: these modes): which modules' recorded slot doesn't
        hold what `trio` compiles to (the publish's counts and app lock; B2: the repaint job). Per MODULE: the
        eligible modules are the canopy modules whose record for the slot is origin "api", that have their own
        t1_frames flag on hardware (Gate C at their address), and whose T1 frame compiles there (a longer frame needs
        its own proven map); T1 is compiled over a context restricted to them. A module that can't take T1 drops out
        alone, never the others. Pure: no engine cache."""
        import dataclasses

        from conductor.engine_api import PlanError
        from conductor.looks import compile_mode

        out: dict[str, RepaintTarget] = {}
        base = self.context(trio.modes, trio.recipes)
        simulated = self.services.engine_driver.simulated
        modules = list(self.services.registry.canopy_modules())

        def compiled(mode_id: str, entries: list[Any]) -> Any:
            try:
                return compile_mode(mode_id, Tier.T1, dataclasses.replace(base, registry=entries))
            except (PlanError, KeyError, LookupError):
                return None

        for mode_id, mode in trio.modes.modes.items():
            if only is not None and mode_id not in only:
                continue
            if mode.slot is None or "canvas" not in mode.ladder:
                continue
            app, uncommissioned, api = [], [], []
            for entry in modules:
                record = self._record(entry, mode.slot)
                if record is None:
                    uncommissioned.append(entry.id)
                elif record["origin"] != "api":
                    app.append(entry.id)
                else:
                    api.append((entry, record))
            eligible = [
                (e, r)
                for e, r in api
                if simulated or bool(getattr((base.flags or {}).get(e.id), "t1_frames", False))
            ]
            listed: list[str] = []
            plan = compiled(mode_id, [e for e, _r in eligible]) if eligible else None
            if plan is None and eligible:
                # one module that can't take T1 (no map for its longer frame, no per-head mode) refuses the whole
                # compile: keep the modules that compile on their own, and compile over those
                eligible = [(e, r) for e, r in eligible if compiled(mode_id, [e]) is not None]
                plan = compiled(mode_id, [e for e, _r in eligible]) if eligible else None
            if plan is None:
                eligible = []
            else:
                eligible = [(e, r) for e, r in eligible if e.id in plan.modules]  # read: driven
                for entry, record in eligible:
                    mt = plan.modules[entry.id]
                    if mt.slot_hash and mt.slot_hash != record["hash"]:
                        listed.append(entry.id)
            out[mode_id] = RepaintTarget(
                slot=mode.slot,
                listed=listed,
                eligible=len(eligible),
                total=len(modules),
                app=app,
                uncommissioned=uncommissioned,
                commissioned=[e.id for e, _r in api],
                plan=plan,
                label=mode.label,
                eligible_ids=[e.id for e, _r in eligible],
                unread=[e.id for e, _r in api if e.id not in self.caps],
            )
        return out

    def repaint_target(self, mode_id: str) -> RepaintTarget | None:
        """The operator repaint's view of one painted mode against the LIVE looks now (the engine's job recomputes it
        when the scene's window opens). None for a mode without a painted slot."""
        from conductor.looks.live import live_trio

        return self.repaint_plan(live_trio(self.services), only=[mode_id]).get(mode_id)

    def _restore_repaints(self) -> None:
        """At engine start, before any profile is read (D68 PR B2): the overlay's gate (`repaints`) seeds the engine's
        (clamped to the interval, so a publish now can't bypass it), and its queued repaints go back to the engine at
        their due times (wall clock, clamped to [0, interval]) over the modules with a commissioned (api) record for
        the slot — no compile needed: the job recomputes what to write when its window opens, which is never before
        the show's start-up is over (`_repaint_ready`). Their (device, slot) pairs are pending from now, so the
        reconciler never rewrites them inside the gate, and a publish carries the scenes over. Once per process."""
        if self._requeued or self.engine is None:
            return
        from conductor.canopy import RepaintJob
        from conductor.looks.live import forget_queued, live_looks

        engine = self.engine
        self._requeued = True
        looks = live_looks(self.services)
        clock = self.services.clock
        now, mono = clock.now(), clock.monotonic()
        engine.seed_repaint_gate({m: now - t for m, t in looks.file.repaints.items()})
        queued = dict(looks.file.queued)
        if not queued:
            return
        interval = self.services.settings.lab.repaint_min_interval_s
        jobs, done = [], []
        for mode_id, due_wall in queued.items():
            mode = self.modes_file.modes.get(mode_id)
            slot = getattr(mode, "slot", None)
            modules = []
            if slot:
                for entry in self.services.registry.canopy_modules():
                    record = self._record(entry, slot) if self._registered(entry) else None
                    if record is not None and record["origin"] == "api":
                        modules.append(entry.id)
            if not modules:
                done.append(mode_id)  # nothing commissioned to repaint
                continue
            left = max(0.0, min(interval, due_wall - now))
            jobs.append(RepaintJob(mode_id, slot, tuple(modules), mode.label, mono + left))
        current = looks.file.current
        scheduled = (
            engine.start_repaint(
                jobs, generation=looks.generation, publication=current.id if current is not None else None
            )
            if jobs
            else []
        )
        done += [m for m in queued if m not in done and m not in {j.mode_id for j in scheduled}]
        if done:
            forget_queued(looks, done, now=now)
        if scheduled:
            log.info(
                "Re-queued the operator repaint of %s after the restart",
                ", ".join(j.mode_id for j in scheduled),
            )

    async def _repaint_ready(self) -> None:
        """A due repaint's window waits for this (the engine's `repaint_ready`): the capability loop's first round
        applied, and (sim) the start-up commissioning over once it can run (every module read; with one module
        unread it never starts, and a repaint doesn't wait for it). Never waits on itself."""
        await self._first_round.wait()
        task = self._task
        if (
            task is not None
            and not task.done()
            and task is not asyncio.current_task()
            and self.caps_ready.is_set()
        ):
            await asyncio.wait({task})

    def _requeue_repaints(self) -> None:
        """Sim, after the start-up commissioning (which wrote the live design into every slot, so it replaced any
        restored job): the overlay's queued repaints still left go back to the engine at their due times; the rest
        leave the queue."""
        if self.engine is None:
            return
        from conductor.canopy import RepaintJob
        from conductor.looks.live import forget_queued, live_looks

        engine = self.engine
        if engine.stopped or getattr(engine, "repainting", False):
            return
        looks = live_looks(self.services)
        clock = self.services.clock
        now, mono = clock.now(), clock.monotonic()
        queued = dict(looks.file.queued)
        if not queued:
            return
        interval = self.services.settings.lab.repaint_min_interval_s
        targets = self.repaint_plan(looks.trio, only=list(queued))
        jobs = []
        for mode_id, due_wall in queued.items():
            target = targets.get(mode_id)
            if target is None or not target.listed or target.plan is None:
                continue
            left = max(0.0, min(interval, due_wall - now))
            jobs.append(RepaintJob(mode_id, target.slot, tuple(target.listed), target.label, mono + left))
        current = looks.file.current
        scheduled = (
            engine.start_repaint(
                jobs, generation=looks.generation, publication=current.id if current is not None else None
            )
            if jobs
            else []
        )
        done = [m for m in queued if m not in {j.mode_id for j in scheduled}]
        if done:
            forget_queued(looks, done, now=now)  # nothing left to repaint for them

    def _after_quiet(self) -> None:
        """A repaint's quiet window ended: new capabilities that arrived during it are applied now."""
        if self._reapply:
            self._wake.set()

    def tier_allowed(self, mode_id: str, tier: Tier) -> tuple[bool, str]:
        if tier is Tier.STEPPED:
            return self._stepped_allowed(mode_id)
        if tier is not Tier.T1 or self.commissioning:
            return True, ""
        slot = getattr(self.modes_file.modes[mode_id], "slot", None)
        missing, painted = [], []
        for entry in self.services.registry.canopy_modules():
            record = self._record(entry, slot)
            if record is None:
                missing.append(entry.id)
            elif record["origin"] != "api":
                painted.append(entry.id)
        if painted:
            return (
                False,
                f"{slot} holds a pattern hand-painted in the Hubspace app on {len(painted)} module(s)",
            )
        if missing:
            return False, f"{slot} hasn't been commissioned on {len(missing)} module(s) yet"
        return True, ""

    def _stepped_allowed(self, mode_id: str) -> tuple[bool, str]:
        """Sim-first: stepped looks run on the simulator; on live hardware only with motion.stepped.live_enabled
        (Alon, after Gate R) or for the mode the stepped rehearsal is running, while it runs."""
        services = self.services
        if services.engine_driver.simulated or services.settings.motion.stepped.live_enabled:
            return True, ""
        if self._rehearsing(mode_id):
            return True, ""
        return False, STEPPED_LIVE_OFF

    def _record(self, entry: Any, slot: str | None) -> dict | None:
        if not slot:
            return None
        if self.engine is not None:
            return self.engine.recorded_slot(entry.id, slot)
        return entry.slot_record(slot)

    # ---- lifecycle -----------------------------------------------------------------------------------------------
    async def start(self) -> Any:
        """Build and start the engine without waiting on any profile read (07 §2)."""
        from conductor.canopy import engine_from_services

        services = self.services
        services.extras[CAPS_KEY + "_wiring"] = self
        from conductor.looks.live import live_looks, note_repaint

        engine = engine_from_services(
            services,
            compile=self.compile,
            ladder=self.ladder,
            modes=self.mode_ids,
            tier_allowed=self.tier_allowed,
            rehearsal_compile=self.rehearsal_plan,
            # the operator repaint (D68 PR B2): the live looks' targets now, their generation, and the overlay's
            # bookkeeping (the gate, the queue, the history row, the Lab's progress)
            repaint_target=self.repaint_target,
            looks_generation=lambda: live_looks(services).generation,
            on_repaint=lambda event, info: note_repaint(services, event, info),
            repaint_ready=self._repaint_ready,
        )
        original = engine.commission

        async def commission(*, source: str) -> dict:
            self.commissioning = True
            try:
                report = await original(source=source)
            finally:
                self.commissioning = False
            self.last_commission = report
            if self._reapply:
                self._wake.set()
            return report

        engine.commission = commission
        services.extras[ENGINE_KEY] = engine
        self.engine = engine
        engine.recovery_listeners.append(self.request_refresh)
        engine.quiet_listeners.append(self._after_quiet)
        callbacks = getattr(services.engine_driver, "on_recovery", None)
        if isinstance(callbacks, list):
            callbacks.append(self._on_driver_recovery)
            self._cleanup.append(lambda: callbacks.remove(self._on_driver_recovery))
        self._cleanup.append(services.telemetry.subscribe(self._on_event))
        # The first look (the idle mode) goes out from the capability loop once its first round of reads is back,
        # so the lifespan never waits on Afero and the modules that answered get their real capabilities.
        await engine.start(apply_initial=False)
        # B2: the persisted gate and queue, before anything can publish or the reconciler can judge a slot
        self._restore_repaints()
        self._caps_task = asyncio.create_task(self._caps_loop(), name="canopy:capabilities")
        if services.engine_driver.simulated:
            self._task = asyncio.create_task(self._sim_commission())
        return engine

    @property
    def started(self) -> bool:
        """Is startup over? On the simulator: its automatic commissioning has run and the look showing was re-picked
        after it. The first look goes out before that commissioning begins, so an engine that merely has a plan and
        isn't commissioning may not have started it yet. On hardware commissioning is an ops action: over once the
        engine has started."""
        return self.engine is not None and (self._task is None or self._task.done())

    async def _sim_commission(self) -> None:
        try:
            await self.caps_ready.wait()
            if self.engine.queued_repaints() and not self.engine.repaint_quiet:
                # the commissioning writes the live design into every slot: it replaces the restored job (which
                # can't start before it) — `_requeue_repaints` re-queues whatever is still left afterwards
                self.engine.cancel_repaint("the simulator's start-up commissioning writes every slot")
            await self.engine.commission(source="startup (simulator)")
            # Re-pick the tier for whatever is showing now (commissioning unlocks T1); never override a guest's
            # choice made while commissioning ran.
            current = self.engine.state().mode or self.services.settings.kiosk.idle_mode
            await self.engine.request_mode(current, source="startup")
            self._requeue_repaints()  # the overlay's queued operator repaints, against what was just commissioned
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Simulator commissioning failed")

    async def close(self) -> None:
        for task in (self._task, self._caps_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        for undo in self._cleanup:
            with contextlib.suppress(Exception):
                undo()
        self._cleanup.clear()
        if self.engine is not None:
            await self.engine.close()
