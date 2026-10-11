"""The services container: everything the app's routes use, built once in the lifespan (05, 07 §1–§2).

Routing of work to drivers (BUILD_PLAN "The seams"):
- engine (show engine, reconciler, kiosk): the SimDriver unless CONDUCTOR_MODE=live, then the AferoDriver.
- bench (bench jobs, /ops/module): the AferoDriver when the gate allows Purpose.BENCH and the device has a
  metadevice id; otherwise the SimDriver, and every result is labelled SIMULATED.
One CommandExecutor per driver instance, so the per-device rate budget is shared whenever two purposes use the
same driver. Startup never waits on Afero or on decrypting tokens; problems become "Needs attention" on /ops.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from conductor.clock import Clock, RealClock
from conductor.config import (
    CONFIG_DIR,
    ConductorSettings,
    Registry,
    data_dir,
    load_registry,
    load_settings,
    save_overlay_patch,
)
from conductor.drivers.base import DeviceDriver
from conductor.executor import CommandExecutor
from conductor.gate import GateSettings, LiveGate, Purpose
from conductor.locks import DeviceLockManager, LockEvent
from conductor.roles import RoleStore
from conductor.state import DeviceStateTracker
from conductor.telemetry import Telemetry
from conductor.tokens import TokenStore
from conductor.ws import WsHub
from conductor.wsauth import TicketBook

log = logging.getLogger(__name__)


@dataclass
class Target:
    """Where a write for one logical device goes, for one purpose."""

    device_id: str  # logical registry id
    address: str  # driver-level address
    driver: DeviceDriver
    executor: CommandExecutor

    @property
    def simulated(self) -> bool:
        return self.driver.simulated


@dataclass
class Services:
    clock: Clock
    gate_settings: GateSettings
    gate: LiveGate
    settings: ConductorSettings
    registry: Registry
    data: Path
    config_dir: Path
    token_store: TokenStore
    telemetry: Telemetry
    locks: DeviceLockManager
    tickets: TicketBook
    hub: WsHub
    roles: RoleStore
    tracker: DeviceStateTracker
    sim: Any  # SimDriver
    afero: Any | None  # AferoDriver when live features are possible
    accounts: Any  # AccountManager
    executors: dict[int, CommandExecutor] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)  # later tiers hang their services here

    # ---- drivers -------------------------------------------------------------------------------------------------
    @property
    def engine_driver(self) -> DeviceDriver:
        return self.afero if (self.gate_settings.engine_driver == "afero" and self.afero) else self.sim

    def executor_for(self, driver: DeviceDriver) -> CommandExecutor:
        return self.executors[id(driver)]

    def bench_driver_for(self, device_id: str) -> DeviceDriver:
        entry = self.registry.get(device_id)
        if self.afero is not None and entry.metadevice_id and self.gate.purpose_allowed(Purpose.BENCH)[0]:
            return self.afero
        return self.sim

    def target(self, device_id: str, purpose: Purpose) -> Target:
        driver = self.engine_driver if purpose is Purpose.ENGINE else self.bench_driver_for(device_id)
        address = self.registry.address(device_id, driver.name)
        if not address:
            raise LookupError(
                f"{device_id} has no {driver.name} address yet (connect the account and register it)"
            )
        return Target(device_id, address, driver, self.executor_for(driver))

    def lockable(self, address: str) -> tuple[bool, str]:
        entry = self.registry.logical_for(address)
        if entry is None:
            return False, f"{address} is not in the registry"
        if address == entry.sim_address:
            return True, ""
        if self.gate.device_writable(address, Purpose.BENCH):
            return True, ""
        return (
            False,
            f"{entry.id} isn't in this runtime's device allowlist, so it can't be locked or written.",
        )

    # ---- registry ------------------------------------------------------------------------------------------------
    def update_registry(self, device_id: str, patch: Mapping[str, Any]) -> Registry:
        self.registry = save_overlay_patch(
            self.registry, device_id, patch, config_dir=self.config_dir, now=self.clock.now()
        )
        known = set(self.sim.addresses())
        for entry in self.registry.devices:
            if entry.sim_address not in known:  # a spare or new device added through the overlay
                self.sim.add_device(
                    entry.sim_address,
                    account=entry.account,
                    profile=entry.profile or "sqhdk03-sim",
                    label=entry.label,
                )
        return self.registry

    def version(self) -> dict:
        return {
            "mode": str(self.gate_settings.mode),
            "live": self.gate_settings.live,
            "simulated": self.engine_driver.simulated,
            "bench_live": self.afero is not None and self.gate.purpose_allowed(Purpose.BENCH)[0],
            "environment": self.gate_settings.environment,
        }

    # ---- lifecycle ------------------------------------------------------------------------------------------------
    async def start(self) -> None:
        if self.afero is not None:
            await self.afero.start()  # schedules background connects; never waits on Afero

    async def close(self) -> None:
        # Stop page sessions and bench runs first so their locks are released and nothing is left pending.
        for key in ("guest_panel", "show", "module_manager", "bench"):
            owner = self.extras.get(key)
            closer = getattr(owner, "close_all", None) or getattr(owner, "close", None)
            if closer is not None:
                await closer()
        for executor in self.executors.values():
            await executor.close()
        if hasattr(self.accounts, "close"):
            await self.accounts.close()
        for driver in (self.afero, self.sim):
            if driver is not None:
                await driver.close()


def _record_lock(telemetry: Telemetry, event: LockEvent) -> None:
    fields: dict[str, Any] = {
        "device_id": event.device_id,
        "action": event.action,
        "owner": event.lease.owner,
    }
    if event.previous is not None:
        fields["previous_owner"] = event.previous.owner
    if event.expired_at is not None:
        fields["expired_at"] = event.expired_at
    telemetry.record("lock", **fields)


def build_services(
    env: Mapping[str, str] = os.environ,
    *,
    clock: Clock | None = None,
    config_dir: Path = CONFIG_DIR,
    sim_settings: Any = None,
) -> Services:
    """Build every service without touching the network. Raises GateConfigError/ConfigError on bad config."""
    from conductor.accounts_sim import SimAccounts
    from conductor.drivers.sim import SimDriver

    clock = clock or RealClock()
    gate_settings = GateSettings.from_env(env)
    data = data_dir(env)
    settings = load_settings(config_dir)
    registry = load_registry(config_dir, data)
    problems: list[str] = []

    holder: dict[str, Registry] = {"registry": registry}
    gate = LiveGate(gate_settings, lambda: holder["registry"].writable_metadevice_ids(), clock)
    token_store = TokenStore(data, gate_settings.environment, env.get("CONDUCTOR_SECRET_KEY"), clock)
    telemetry = Telemetry(data, clock)
    tickets = TicketBook(clock)
    roles = RoleStore(data, clock)
    tracker = DeviceStateTracker(clock, telemetry)

    from conductor.sim.settings import SimSettings

    if sim_settings is None:
        sim_settings = SimSettings.dev()
        if (
            env.get("CONDUCTOR_SIM_SPEED") == "fast"
        ):  # tests and quick demos: same behaviour, ~20x less latency
            sim_settings = sim_settings.with_(
                ack_ms_min=5, ack_ms_max=40, device_apply_extra_s_min=0.01, device_apply_extra_s_max=0.08
            )
    sim = SimDriver(registry, clock=clock, settings=sim_settings)
    afero = None
    registry_now = lambda: holder["registry"]
    accounts: Any = SimAccounts(lambda: registry_now(), clock)
    if gate_settings.live:
        if not token_store.available:
            problems.append(token_store.problem)
        else:
            from conductor.drivers.afero import AferoAccounts, AferoDriver

            afero = AferoDriver(lambda: registry_now(), gate, token_store, clock=clock, telemetry=telemetry)
            accounts = AferoAccounts(afero)

    services = Services(
        clock=clock,
        gate_settings=gate_settings,
        gate=gate,
        settings=settings,
        registry=registry,
        data=data,
        config_dir=config_dir,
        token_store=token_store,
        telemetry=telemetry,
        locks=None,  # type: ignore[arg-type] - set below (needs services.lockable)
        tickets=tickets,
        hub=None,  # type: ignore[arg-type]
        roles=roles,
        tracker=tracker,
        sim=sim,
        afero=afero,
        accounts=accounts,
        problems=problems,
    )

    # The registry can change at runtime (ops edits); everything reads it through the services object.
    registry_now = lambda: services.registry
    gate.writable_devices = lambda: services.registry.writable_metadevice_ids()
    services.locks = DeviceLockManager(
        services.lockable,
        clock,
        idle_timeout_s=settings.module_page.lock_idle_timeout_s,
        on_change=lambda event: _record_lock(telemetry, event),
    )
    services.hub = WsHub(tickets, hello=services.version, session_active=roles.session_active)
    roles.on_session_revoked(services.hub.close_session)  # a revoked phone's open sockets close at once
    for driver in (sim, afero):
        if driver is None:
            continue
        executor = CommandExecutor(
            driver, locks=services.locks, telemetry=telemetry, clock=clock, policy=settings.executor
        )
        tracker.attach(executor)
        services.executors[id(driver)] = executor
    return services
