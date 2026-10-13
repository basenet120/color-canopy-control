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
- In sim, the slots are commissioned automatically once every module's capabilities are read, so demos run T1
  exactly as the show would after install-day commissioning. On hardware commissioning is an explicit ops action.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
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


class ShowWiring:
    def __init__(self, services: Any):
        from conductor.looks import load_modes

        self.services = services
        self.modes_file = load_modes(services.config_dir)
        _check_idle_scene(services.settings.kiosk.idle_scene, list(self.modes_file.modes))
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

    # ---- capabilities --------------------------------------------------------------------------------------------
    def _registered(self, entry: Any) -> bool:
        """The module has a device on the engine's driver (D62): one without is never read, retried or driven."""
        services = self.services
        return bool(services.registry.address(entry.id, services.engine_driver.name))

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
        return frozenset(e.id for e in self._modules() if self._registered(e))

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
                await self._apply_new_caps(first=first)
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
        plan = engine.current_plan()
        if plan is None:
            await engine.request_mode(self.services.settings.kiosk.idle_mode, source="idle")
        elif not first:
            await engine.request_mode(plan.mode_id, source="reconciler")

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
    def context(self) -> Any:
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
            modes=self.modes_file,
            config_dir=services.config_dir,
            addresses=addresses,
        )

    def compile(self, mode_id: str, tier: Tier) -> Any:
        from conductor.looks import compile_mode

        # A module without capabilities is left out of the plan (the safe look assumes conservative ones); only
        # when no module has been read does compile_mode refuse (PlanError).
        return compile_mode(mode_id, tier, self.context())

    def ladder(self, mode_id: str) -> list[Tier]:
        from conductor.looks import ladder

        return ladder(mode_id, self.modes_file)

    def mode_ids(self) -> list[str]:
        return list(self.modes_file.modes)

    def tier_allowed(self, mode_id: str, tier: Tier) -> tuple[bool, str]:
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
        engine = engine_from_services(
            services,
            compile=self.compile,
            ladder=self.ladder,
            modes=self.mode_ids,
            tier_allowed=self.tier_allowed,
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
        callbacks = getattr(services.engine_driver, "on_recovery", None)
        if isinstance(callbacks, list):
            callbacks.append(self._on_driver_recovery)
            self._cleanup.append(lambda: callbacks.remove(self._on_driver_recovery))
        self._cleanup.append(services.telemetry.subscribe(self._on_event))
        # The first look (the idle mode) goes out from the capability loop once its first round of reads is back,
        # so the lifespan never waits on Afero and the modules that answered get their real capabilities.
        await engine.start(apply_initial=False)
        self._caps_task = asyncio.create_task(self._caps_loop(), name="canopy:capabilities")
        if services.engine_driver.simulated:
            self._task = asyncio.create_task(self._sim_commission())
        return engine

    async def _sim_commission(self) -> None:
        try:
            await self.caps_ready.wait()
            await self.engine.commission(source="startup (simulator)")
            # Re-pick the tier for whatever is showing now (commissioning unlocks T1); never override a guest's
            # choice made while commissioning ran.
            current = self.engine.state().mode or self.services.settings.kiosk.idle_mode
            await self.engine.request_mode(current, source="startup")
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
