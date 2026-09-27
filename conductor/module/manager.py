"""Open module-page sessions, one per device, kept in `services.extras["module_sessions"]`.

The manager turns a principal + device id into a session: it resolves the bench Target (`services.target(id,
Purpose.BENCH)`), takes the device's lock (ops may take over; a tester re-opening from the same enrolled phone
reclaims its own lock), and registers the `module` WebSocket snapshot so a page that subscribes gets the current
state at once.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Any

from conductor.config import ConfigError, load_recipes
from conductor.gate import Purpose
from conductor.geometry import CONFIG_PATH, load_canopy_config
from conductor.locks import LockError
from conductor.looks import load_modes
from conductor.module.layout import calibration_for, module_map
from conductor.module.session import SESSIONS_KEY, ModuleError, ModuleSession, SessionDeps
from conductor.roles_api import Principal, Role

MANAGER_KEY = "module_manager"
log = logging.getLogger(__name__)


def owner_label(principal: Principal) -> str:
    return f"module page · {principal.name or principal.role.value}"


def owner_key(principal: Principal) -> str:
    """Identifies one enrolled phone (tester sessions); empty for Basic-auth ops, who share one credential."""
    return f"{principal.role.value}:{principal.session_id}" if principal.session_id else ""


class ModuleManager:
    def __init__(self, services: Any):
        self.services = services
        self.sessions: dict[str, ModuleSession] = services.extras.setdefault(SESSIONS_KEY, {})
        canopy_path = Path(services.config_dir) / "canopy.yaml"
        self.canopy = load_canopy_config(canopy_path if canopy_path.exists() else CONFIG_PATH)
        self.recipes = load_recipes(services.config_dir)
        self.modes: Any = None
        try:
            self.modes = load_modes(Path(services.config_dir))
        except ConfigError as e:  # the module page still works; only the Lowcountry preview is unavailable
            log.warning("module page: the Lowcountry Modes couldn't be loaded:\n%s", e)
        services.hub.register_snapshot("module", self.topic_snapshot)

    @classmethod
    def of(cls, services: Any) -> ModuleManager:
        manager = services.extras.get(MANAGER_KEY)
        if manager is None:
            manager = services.extras[MANAGER_KEY] = cls(services)
        return manager

    def deps(self) -> SessionDeps:
        s = self.services
        return SessionDeps(
            clock=s.clock,
            locks=s.locks,
            telemetry=s.telemetry,
            settings=s.settings,
            recipes=self.recipes,
            canopy=self.canopy,
            entry=lambda device_id: s.registry.get(device_id),
            publish=s.hub.publish,
            save_calibration=s.update_registry,
            save_registry=s.update_registry,
            tracker=s.tracker,
            modes=self.modes,
        )

    # ---- lookups -----------------------------------------------------------------------------------------------
    def get(self, device_id: str) -> ModuleSession | None:
        """The open session for a device, dropping it if its lease has gone (idle expiry, take-over)."""
        session = self.sessions.get(device_id)
        if session is not None and not session.lease_alive():
            session.abandon()
            self.sessions.pop(device_id, None)
            return None
        return session

    def require(self, device_id: str, lease: str | None) -> ModuleSession:
        """The session this lease token opened, or ModuleError(409) in plain words."""
        session = self.sessions.get(device_id)
        if session is None or not lease or not secrets.compare_digest(session.lease.token, lease):
            raise ModuleError(
                f"You don't hold {device_id} any more (it was closed, timed out or taken over). Open it again."
            )
        if not session.lease_alive():
            self.get(device_id)
            raise ModuleError(session.gone_message())
        return session

    def devices(self, principal: Principal) -> list[dict]:
        """Devices this principal may open, with who holds each one."""
        out = []
        for entry in self.services.registry.devices:
            if not principal.can_device(entry.id):
                continue
            try:
                target = self.services.target(entry.id, Purpose.BENCH)
            except LookupError:
                continue
            holder = self.services.locks.holder(target.address)
            session = self.get(entry.id)
            out.append(
                {
                    "id": entry.id,
                    "label": entry.label or entry.id,
                    "role": entry.role,
                    "simulated": target.simulated,
                    "driver": target.driver.name,
                    "locked": holder is not None,
                    "holder": holder.owner if holder else None,
                    "open_on_page": session is not None,
                }
            )
        out.sort(key=lambda d: (d["id"] != "bench-kit", d["role"] != "bench", d["id"]))
        return out

    def closed_view(self, device_id: str) -> dict:
        """What GET /api/module/{id} shows when no page has the device open."""
        entry = self.services.registry.get(device_id)
        target = self.services.target(device_id, Purpose.BENCH)
        holder = self.services.locks.holder(target.address)
        return {
            "device_id": device_id,
            "label": entry.label or device_id,
            "open": False,
            "simulated": target.simulated,
            "driver": target.driver.name,
            "lock": {"held": False, "owner": holder.owner if holder else None},
            "map": module_map(self.canopy, calibration_for(self.canopy, entry)),
        }

    def topic_snapshot(self, topic: str) -> dict:
        device_id = topic.partition(":")[2]
        session = self.get(device_id)
        if session is not None:
            return {"event": "snapshot", "state": session.snapshot()}
        try:
            return {"event": "snapshot", "state": self.closed_view(device_id)}
        except (KeyError, LookupError):
            return {"event": "snapshot", "state": None}

    # ---- open / close ------------------------------------------------------------------------------------------
    async def open(self, device_id: str, principal: Principal, *, steal: bool = False) -> ModuleSession:
        if steal and principal.role is not Role.OPS:
            raise ModuleError("Only ops can take over a device someone else is using.", 403)
        try:
            self.services.registry.get(device_id)
            target = self.services.target(device_id, Purpose.BENCH)
        except KeyError:
            raise ModuleError(f"There's no device called {device_id}.", 404) from None
        except LookupError as e:
            raise ModuleError(str(e)) from None
        key = owner_key(principal)
        existing = self.get(device_id)
        reclaim = bool(key) and existing is not None and existing.owner_key == key
        try:
            session = await ModuleSession.open(
                self.deps(), target, owner=owner_label(principal), steal=steal or reclaim, owner_key=key
            )
        except LockError as e:
            holder = self.services.locks.holder(target.address)
            raise ModuleError(
                str(e) if holder is None else f"{device_id} is in use by {holder.owner}.",
                409,
            ) from None
        # Reading the profile awaited the driver; someone may have taken the device over meanwhile. Only the
        # live lease holder may become the device's session, or the lock would be held by nobody's page.
        if not session.lease_alive():
            session.abandon()  # never release: the lock belongs to whoever took it over
            holder = self.services.locks.holder(target.address)
            raise ModuleError(
                f"{device_id} was taken over while it was opening"
                + (f" (now held by {holder.owner})." if holder else ".")
                + " Open it again if you still need it.",
                409,
            )
        existing = self.sessions.get(device_id)  # re-read: it may have changed during the await
        if existing is not None and existing is not session:
            existing.abandon()  # its page notices the new session_id on the next event or heartbeat
        self.sessions[device_id] = session
        session.publish("opened")
        return session

    async def close_all(self) -> None:
        """Shutdown: stop sweeps and release every page lock."""
        for session in list(self.sessions.values()):
            await session.close()
        self.sessions.clear()

    async def close(self, device_id: str, lease: str | None) -> bool:
        session = self.sessions.get(device_id)
        if session is None or not lease or not secrets.compare_digest(session.lease.token, lease):
            return False
        self.sessions.pop(device_id, None)
        released = await session.close()
        self.services.hub.publish(f"module:{device_id}", self.topic_snapshot(f"module:{device_id}"))
        return released
