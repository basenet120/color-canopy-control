"""The guest panel: the iPad on the stand (/kiosk) choosing one of the six approved scenes, brightness and power.

One API (`conductor/routes/guest.py`, under /api/kiosk) over one target, the canopy engine (D67; the panel had a
second target for the shop kits until the canopy-only change):

- **EngineTarget** — a scene is `engine.request_mode(scene_id, source="kiosk")` with the kiosk's own refusals;
  brightness and power go to `engine.set_guest_brightness` / `engine.set_guest_power` only when the engine has them
  (`capabilities` says whether it does).

**One lane.** Every guest write runs in one background worker, one job at a time, jobs no closer together than the
executor's `min_write_interval_s`. Pending work is coalesced and the latest request wins: at most one pending scene,
one brightness and one power. A dragging slider therefore gives one brightness request at a time, ending on the last
level. Scene choices also have the kiosk's cooldown (`settings.kiosk.cooldown_s`, 8 s, as /api/kiosk/select): a
second choice inside it is a 429.

**The canopy picture.** `canopy()` is the 270-light geometry and `state()["lit"]` what each canopy position truthfully
shows (`conductor/canopy_view.py`: off, dark, unknown, lit or changing, with full-strength colours and the level).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from conductor import canopy_view
from conductor.clock import Clock
from conductor.engine_api import CanopyPhase, CanopyState, ModeRefused, PlanError
from conductor.gate import Purpose
from conductor.routes import kiosk as K
from conductor.scenes import Scene, SceneCatalog
from conductor.show import registered_canopy_ids

log = logging.getLogger(__name__)

PANEL_KEY = "guest_panel"
GUEST_FLOOR = 10  # the lowest brightness a guest can set
LOG_SIZE = 50
HEADS = 30
CLOSE_WAIT_S = 15.0  # at shutdown, let a running job finish

CHANGING = "Changing the lights…"
BARE_LABEL = "The Color Canopy"  # the engine target's label while no strand is registered (D62)
LIGHTS_RESTING = "The lights are resting. They will be back shortly."
DIDNT_FINISH = "That scene didn't finish. Choose it again in a moment."
NOT_REACHED = "That didn't reach the lights. Try again in a moment."
UNKNOWN_SCENE = "That scene isn't one of the panel's choices."
SCENE_UNAVAILABLE = "That scene isn't available right now. Choose another."
NOT_AVAILABLE = "That control isn't available right now."
STILL_CHANGING_S = 2.0  # retry_after_s while a scene is still being applied past its cooldown


class GuestRefused(Exception):
    """A calm refusal. `status` for the route, `message` for the guest's screen, `extra` for the JSON (retry_after_s)
    and `why` for the ops debug log (it may name devices and owners; the guest never sees it)."""

    def __init__(self, status: int, message: str, extra: dict | None = None, *, why: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = dict(extra or {})
        self.why = why or message


class CommandLog:
    """The last LOG_SIZE guest actions and jobs, newest first, for GET /api/kiosk/debug."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self.items: deque[dict] = deque(maxlen=LOG_SIZE)

    def record(
        self,
        action: str,
        params: dict,
        outcome: str,
        *,
        target: str | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> None:
        self.items.appendleft(
            {
                "at": self.clock.now(),
                "action": action,
                "params": params,
                "outcome": outcome,
                "target": target,
                "latency_ms": latency_ms,
                "error": error,
            }
        )

    def recent(self) -> list[dict]:
        return list(self.items)


class Lane:
    """One background worker. Pending work is coalesced per kind (latest wins), run in arrival order one job at a
    time, and jobs start at least `gap()` seconds apart. `on_idle` runs when the last job has finished."""

    def __init__(
        self,
        clock: Clock,
        run: Callable[[str, Any], Awaitable[None]],
        gap: Callable[[], float],
        on_idle: Callable[[], None] = lambda: None,
    ):
        self.clock = clock
        self.run = run
        self.gap = gap
        self.on_idle = on_idle
        self.pending: dict[str, Any] = {}
        self.running: str | None = None
        self.task: asyncio.Task | None = None
        self._last_start: float | None = None

    @property
    def busy(self) -> bool:
        return bool(self.pending) or self.running is not None

    def has(self, kind: str) -> bool:
        return self.running == kind or kind in self.pending

    def put(self, kind: str, value: Any, *, drop: Iterable[str] = ()) -> None:
        for k in drop:
            self.pending.pop(k, None)
        self.pending.pop(kind, None)  # re-queued at the end: arrival order
        self.pending[kind] = value
        if self.task is None or self.task.done():
            self.task = asyncio.get_running_loop().create_task(self._work(), name="guest-panel-lane")

    async def _work(self) -> None:
        try:
            while self.pending:
                wait = (
                    0.0
                    if self._last_start is None
                    else self._last_start + self.gap() - self.clock.monotonic()
                )
                if wait > 0:
                    await self.clock.sleep(wait)
                    continue  # the pending work may have changed meanwhile
                kind = next(iter(self.pending))
                value = self.pending.pop(kind)
                self.running = kind
                self._last_start = self.clock.monotonic()
                try:
                    await self.run(kind, value)
                except Exception:
                    log.exception("guest panel: the %s job failed", kind)
                finally:
                    self.running = None
        finally:
            if not self.pending:
                self.on_idle()

    async def wait_idle(self) -> None:
        while self.task is not None and not self.task.done():
            await asyncio.wait({self.task})

    async def close(self, timeout: float = CLOSE_WAIT_S) -> None:
        self.pending.clear()
        task = self.task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def _ms(start: float, end: float) -> int:
    return round((end - start) * 1000)


def scene_json(scene: Scene) -> dict:
    return {
        "id": scene.id,
        "name": scene.name,
        "mood": scene.mood,
        "description": scene.description,
        "palette": [{"name": c.name, "hex": c.hex} for c in scene.palette],
        "frame": [list(rgb) for rgb in scene.frame(HEADS)],
        "solid": scene.solid.hex,
        "brightness_draft": scene.brightness_draft,
    }


def guest_level(level: int) -> int:
    return max(GUEST_FLOOR, min(100, int(level)))


# ---- the canopy engine ------------------------------------------------------------------------------------------
# why guests wait on the canopy, from the engine's own flags: "try-on" (a Design Lab try-on, D66) or "testing" (the
# stepped rehearsal); the iPad drops a waiting toast once it clears
_WAITING_MESSAGES = {K.TRYING_ON: "try-on", K.TESTING: "testing"}


def _waiting(engine: Any) -> str | None:
    if K._flag(engine, "trial_active") is True:
        return "try-on"
    if K._flag(engine, "rehearsal_active") is True:
        return "testing"
    return None


class EngineTarget:
    """The canopy engine at `services.extras["canopy"]` (see the module docstring)."""

    kind = "canopy"
    FULL_LABEL = "The Color Canopy (nine strands)"

    def __init__(self, services: Any, log: CommandLog | None = None):
        self.services = services
        self.log = log
        self.lane = Lane(services.clock, self._run, self._gap, on_idle=self._lane_idle)
        self.requested: dict[str, Any] = {}
        self.accepted: dict[str, Any] = {}  # the last brightness/power the engine took
        self.message = ""

    def engine(self) -> Any:
        return self.services.extras.get(K.ENGINE_KEY)

    @property
    def label(self) -> str:
        """Names the strands the current look actually drives when it leaves some out (D62). Before the first look
        (no plan) it counts the strands registered on the engine's driver: all of them, some, or none."""
        plan = None
        current = getattr(self.engine(), "current_plan", None)
        if callable(current):
            try:
                plan = current()
            except Exception:  # noqa: BLE001 - a label never fails the panel
                plan = None
        total = len(self.services.registry.canopy_modules())
        if plan is None:
            try:
                registered = len(registered_canopy_ids(self.services))
            except Exception:  # noqa: BLE001 - a label never fails the panel, and never claims a count it can't make
                return BARE_LABEL
            if registered == total:
                return self.FULL_LABEL
            if registered == 0:
                return BARE_LABEL
            return f"The Color Canopy ({registered} of {total} strands)"
        if not getattr(plan, "skipped", None):
            return self.FULL_LABEL
        return f"The Color Canopy ({len(plan.modules)} of {total} strands)"

    def _canopy_cfg(self) -> dict:
        return canopy_view.load_canopy(str(self.services.config_dir))

    def canopy(self) -> dict:
        """The canopy's 270 lights in inches, per module in lattice order, with the install's view (kiosk.preview)."""
        return canopy_view.canopy_geometry(self._canopy_cfg(), self.services.settings.kiosk.preview)

    def _lit(self, engine: Any, st: CanopyState) -> dict | None:
        """What each canopy position truthfully shows (canopy_view.lit_state), or None before the first look."""
        try:
            return canopy_view.lit_state(
                self._canopy_cfg(),
                self.services.registry,
                K._current_plan(engine),
                st,
                now=self.services.clock.now(),
            )
        except Exception:  # the picture never fails the panel; no picture is no claim
            log.exception("The guest panel couldn't work out the canopy picture.")
            return None

    def capabilities(self) -> dict[str, bool]:
        engine = self.engine()
        return {
            "scene": engine is not None,
            "brightness": callable(getattr(engine, "set_guest_brightness", None)),
            "power": callable(getattr(engine, "set_guest_power", None)),
        }

    @property
    def simulated(self) -> bool:
        return bool(self.services.engine_driver.simulated)

    def _gap(self) -> float:
        return float(self.services.settings.executor.min_write_interval_s)

    def _lane_idle(self) -> None:
        self.requested = {}

    def _record(self, action: str, params: dict, outcome: str, **kw: Any) -> None:
        if self.log is not None:
            self.log.record(action, params, outcome, target=self.kind, **kw)

    def _engine_state(self, engine: Any) -> CanopyState:
        try:
            return engine.state()
        except Exception:
            log.exception("The canopy engine couldn't report its state.")
            raise GuestRefused(503, K.RESTING, why="the engine couldn't report its state") from None

    def _ready_engine(self) -> tuple[Any, CanopyState]:
        """The engine and its published state, when that state lets a guest change something now (paused input and
        a resting canopy are refused whatever the engine would do — as /api/kiosk/select does)."""
        engine = self.engine()
        if engine is None:
            raise GuestRefused(503, K.RESTING, why="no canopy engine is running")
        current = self._engine_state(engine)
        if not current.guest_input:
            raise GuestRefused(423, K.PAUSED, why="guest input is paused")
        if current.phase == CanopyPhase.RESTING:
            raise GuestRefused(503, K.RESTING, why=current.message or "the canopy is resting")
        return engine, current

    async def scene(self, scene: Scene) -> None:
        engine, current = self._ready_engine()
        wait = K._remaining(current.cooldown_until, self.services.clock.now())
        if wait > 0:
            raise GuestRefused(*K.cooldown_refusal(wait), why="inside the engine's cooldown")
        try:
            new = await engine.request_mode(scene.id, source="kiosk")
        except ModeRefused as exc:
            status, message, extra = K.refusal_for(exc)
            if status == 404:  # the engine doesn't know this scene (yet): a calm 409
                status, message, extra = 409, SCENE_UNAVAILABLE, {}
            raise GuestRefused(status, message, extra, why=f"engine: {exc.message or exc.reason}") from None
        except (PlanError, LookupError) as exc:
            raise GuestRefused(409, SCENE_UNAVAILABLE, why=f"engine: {exc}") from None
        except Exception:
            log.exception("The canopy engine failed a guest panel request for %s.", scene.id)
            raise GuestRefused(503, K.RESTING, why="the engine raised") from None
        if isinstance(new, CanopyState) and new.refused:
            status, message, extra = K.refusal_for_state(engine, new, self.services.clock.now())
            raise GuestRefused(status, message, extra, why=f"engine: {new.message}")
        self.message = ""  # the guest's scene is showing: an older lane refusal no longer applies

    async def brightness(self, level: int) -> int:
        engine, _ = self._ready_engine()
        if not callable(getattr(engine, "set_guest_brightness", None)):
            raise GuestRefused(409, NOT_AVAILABLE, why="the engine has no set_guest_brightness")
        level = guest_level(level)
        self.requested["brightness"] = level
        self.message = ""
        self.lane.put("brightness", level)
        return level

    async def power(self, on: bool) -> None:
        engine, _ = self._ready_engine()
        if not callable(getattr(engine, "set_guest_power", None)):
            raise GuestRefused(409, NOT_AVAILABLE, why="the engine has no set_guest_power")
        self.requested["power"] = bool(on)
        self.message = ""
        self.lane.put("power", bool(on))

    async def _run(self, kind: str, value: Any) -> None:
        clock = self.services.clock
        started = clock.monotonic()
        params = {"level": value} if kind == "brightness" else {"on": value}
        engine = self.engine()
        outcome, error = "failed", None
        try:
            if engine is None:
                raise GuestRefused(503, K.RESTING, why="no canopy engine is running")
            if kind == "brightness":
                result = engine.set_guest_brightness(value, source="kiosk")
            else:
                result = engine.set_guest_power(value, source="kiosk")
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, CanopyState) and result.refused:
                status, message, _extra = K.refusal_for_state(engine, result, clock.now())
                raise GuestRefused(status, message, why=f"engine: {result.message}")
            self.accepted[kind] = value
            outcome = "accepted by the engine"
        except GuestRefused as e:
            outcome, error, self.message = "refused", e.why, e.message
        except ModeRefused as exc:
            _status, self.message, _extra = K.refusal_for(exc)
            outcome, error = "refused", f"engine: {exc.message or exc.reason}"
        except Exception as exc:
            log.exception("The canopy engine failed a guest %s request.", kind)
            outcome, error, self.message = "failed", type(exc).__name__, NOT_REACHED
        finally:
            self._record(kind, params, outcome, latency_ms=_ms(started, clock.monotonic()), error=error)

    def state(self, scene_ids: Iterable[str] = ()) -> dict:
        engine = self.engine()
        now = self.services.clock.now()
        resting = {
            "power": None,
            "brightness": None,
            "scene": None,
            "look": None,
            "phase": "resting",
            "message": K.RESTING,
            "feature": None,
            "cooldown_remaining_s": 0.0,
            "lit": None,
            "waiting": None,
        }
        if engine is None:
            return resting
        try:
            st = K.kiosk_view(engine, engine.state())
        except Exception:
            log.exception("The canopy engine couldn't report its state.")
            return resting
        cooldown = K._remaining(st.cooldown_until, now)
        waiting = _waiting(engine)
        # A lane refusal that names an engine flag (a guest's brightness or power refused during a try-on or the
        # stepped rehearsal) is true only while that flag stands: once it lifts, never keep saying "It will be back
        # shortly" (kiosk copy must be true).
        if self.message in _WAITING_MESSAGES and _WAITING_MESSAGES[self.message] != waiting:
            self.message = ""
        # The look's own phase, over the strands it drives: on a partial canopy `phase` stays needs attention for
        # /ops (D62) while the guest still sees the change and its end. An engine without it: `phase`.
        look = getattr(st, "look_phase", None) or st.phase
        if not st.guest_input:
            phase, message = "paused", K.PAUSED
        elif st.phase == CanopyPhase.RESTING:
            phase, message = "resting", (K.BEING_SET_UP if st.message == K.BEING_SET_UP else K.RESTING)
        elif look == CanopyPhase.UPDATING or self.lane.busy:
            phase, message = "changing", CHANGING
        elif cooldown > 0:
            phase, message = "cooldown", self.message
        else:
            phase, message = "ready", self.message
        # the engine's own truth (D60): `guest_power` is False only while a guest has the canopy off, and the idle
        # reset turns it back on — never the panel's last request
        power = getattr(st, "guest_power", getattr(st, "power", None))
        brightness = getattr(st, "brightness", None)
        if power is None:
            power = self.requested.get("power", self.accepted.get("power"))
        if brightness is None:
            brightness = self.requested.get("brightness", self.accepted.get("brightness"))
        ids = set(scene_ids)
        return {
            "power": power,
            "brightness": brightness,
            "scene": st.mode if st.mode in ids else None,
            "look": None,  # the engine's techniques aren't a 30-light frame; the panel previews the scene itself
            "phase": phase,
            "message": message,
            # never a technique sentence for lights that are off, still changing scene or stuck on a driven strand
            # (kiosk copy must be true)
            "feature": None
            if power is False or look in (CanopyPhase.UPDATING, CanopyPhase.NEEDS_ATTENTION)
            else st.feature,
            "cooldown_remaining_s": cooldown,
            "lit": self._lit(engine, st),
            "waiting": waiting,
        }

    def lease_holders(self) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for entry in self.services.registry.canopy_modules():
            try:
                address = self.services.target(entry.id, Purpose.ENGINE).address
            except LookupError:
                continue
            lease = self.services.locks.holder(address)
            out[entry.id] = lease.owner if lease else None
        return out

    def debug(self) -> dict:
        engine = self.engine()
        state = None
        if engine is not None:
            try:
                state = engine.state().to_json()
            except Exception as exc:  # noqa: BLE001 - shown to ops as-is
                state = {"error": type(exc).__name__}
        return {
            "engine_present": engine is not None,
            "engine_type": type(engine).__name__ if engine is not None else None,
            "engine_state": state,
            "lane": {"running": self.lane.running, "pending": list(self.lane.pending)},
            "requested": dict(self.requested),
            "accepted": dict(self.accepted),
        }

    async def wait_idle(self) -> None:
        await self.lane.wait_idle()

    async def close(self) -> None:
        await self.lane.close()


class GuestPanel:
    """The guest panel's state for one Conductor (`services.extras["guest_panel"]`)."""

    def __init__(self, services: Any):
        self.services = services
        self.log = CommandLog(services.clock)
        self.engine_target = EngineTarget(services, self.log)
        self.last_state: dict | None = None

    @classmethod
    def of(cls, services: Any) -> GuestPanel:
        panel = services.extras.get(PANEL_KEY)
        if panel is None:
            panel = services.extras[PANEL_KEY] = cls(services)
        return panel

    def scenes(self) -> SceneCatalog:
        from conductor.looks.live import live_trio  # the live catalog (D68): approved or published

        return live_trio(self.services).scenes

    async def target(self) -> EngineTarget:
        """The one target: the canopy engine, on every runtime (D67)."""
        return self.engine_target

    async def close(self) -> None:
        await self.engine_target.close()
