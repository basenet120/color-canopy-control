"""Show wiring: the canopy engine on the Services container (P1 engine × P2 looks), built in the lifespan.

- Capabilities come from each canopy module's own profile through the engine driver, cached in
  `services.extras["capabilities"]` (the same cache /api/devices uses) and refreshed in the background.
- The compile context is rebuilt per change from the live registry (calibration, flags, recorded slot hashes).
- Tier rules (04 ladder, 07 §6): the looks compiler already refuses T1 without Gate C (`flags.t1_frames`) and T2
  without recorded hashes on hardware; on top of that a T1 mode is only *shown* once every module's slot has a
  recorded hash (it has been commissioned), so the kiosk never selects a slot that was never written. Commissioning
  itself only needs the capability and bench-flag checks.
- In sim, the slots are commissioned automatically at startup, so demos run T1 exactly as the show would after
  install-day commissioning. On hardware commissioning is an explicit ops action (runbook).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from conductor.capabilities import Capabilities, detect
from conductor.engine_api import PlanError, Tier
from conductor.gate import Purpose

log = logging.getLogger(__name__)
CAPS_KEY = "capabilities"
ENGINE_KEY = "canopy"


class ShowWiring:
    def __init__(self, services: Any):
        from conductor.looks import load_modes

        self.services = services
        self.modes_file = load_modes(services.config_dir)
        self.caps: dict[str, Capabilities] = {}
        self.slot_readback: dict[str, dict[str, Any]] = {}
        self.commissioning = False
        self.engine: Any = None
        self._task: asyncio.Task | None = None

    # ---- capabilities --------------------------------------------------------------------------------------------
    async def refresh_capabilities(self) -> None:
        services = self.services
        for entry in services.registry.canopy_modules():
            try:
                target = services.target(entry.id, Purpose.ENGINE)
                info = await target.driver.fetch_profile(target.address, purpose=Purpose.ENGINE)
            except Exception as exc:  # noqa: BLE001 - an unreachable module shows as needs attention, not a crash
                log.warning("Couldn't read %s's profile: %s", entry.id, exc)
                continue
            caps = detect(info.functions, info.states)
            self.caps[entry.id] = caps
            self.slot_readback[entry.id] = {
                r.function_instance: r.value
                for r in info.states
                if r.function_class == "color-sequence-v2" and r.function_instance
            }
            problem = caps.head_count_problem(entry.expected_heads)
            if problem:
                services.tracker.flag_problem(target.address, problem)

    # ---- compiling -----------------------------------------------------------------------------------------------
    def context(self) -> Any:
        from conductor.looks import CompileContext

        services = self.services
        simulated = services.engine_driver.simulated
        flags = None
        if not simulated:
            flags = {}
            for entry in services.registry.canopy_modules():
                address = services.registry.address(entry.id, services.engine_driver.name)
                flags[entry.id] = entry.flags_for(address)
        return CompileContext.from_settings(
            registry=services.registry,
            capabilities=self.caps,
            settings=services.settings,
            slot_readback=self.slot_readback,
            flags=flags,
            modes=self.modes_file,
            config_dir=services.config_dir,
        )

    def compile(self, mode_id: str, tier: Tier) -> Any:
        from conductor.looks import compile_mode

        if not self.caps:
            raise PlanError("the modules' capabilities haven't been read yet")
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
        missing = [
            e.id for e in self.services.registry.canopy_modules() if not (e.slot_hashes or {}).get(slot)
        ]
        if missing:
            return False, f"{slot} hasn't been commissioned on {len(missing)} module(s) yet"
        return True, ""

    # ---- lifecycle -----------------------------------------------------------------------------------------------
    async def start(self) -> Any:
        from conductor.canopy import engine_from_services

        services = self.services
        services.extras[CAPS_KEY + "_wiring"] = self
        await self.refresh_capabilities()
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
                return await original(source=source)
            finally:
                self.commissioning = False

        engine.commission = commission
        services.extras[ENGINE_KEY] = engine
        self.engine = engine
        await engine.start()
        if services.engine_driver.simulated:
            self._task = asyncio.create_task(self._sim_commission())
        return engine

    async def _sim_commission(self) -> None:
        try:
            await self.engine.commission(source="startup (simulator)")
            # Re-pick the tier for whatever is showing now (commissioning unlocks T1); never override a guest's
            # choice made while commissioning ran.
            current = self.engine.state().mode or self.services.settings.kiosk.idle_mode
            await self.engine.request_mode(current, source="startup")
        except Exception:
            log.exception("Simulator commissioning failed")

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        if self.engine is not None:
            await self.engine.close()
