"""The guest panel: the iPad on the stand (/kiosk) choosing one of the scenes the iPad offers (kiosk.hidden_scenes
left out, D76), brightness and power.

One API (`conductor/routes/guest.py`, under /api/kiosk) over one target, the canopy engine (D67; the panel had a
second target for the shop kits until the canopy-only change):

- **EngineTarget** — a scene is `engine.request_mode(scene_id, source="kiosk")` with the kiosk's own refusals;
  brightness and power go to `engine.set_guest_brightness` / `engine.set_guest_power` only when the engine has them
  (`capabilities` says whether it does).

**One lane.** Every guest write runs in one background worker, one job at a time, jobs no closer together than the
executor's `min_write_interval_s`. Pending work is coalesced and the latest request wins: at most one pending scene,
one brightness and one power, so however fast requests come, the last level wins (the iPad's slider sends once, on
release: D69). Scene choices also have the kiosk's cooldown (D70: the guests' write budget for what the last input
sent, never under `kiosk.cooldown_min_s`; as /api/kiosk/select): a second choice inside it is a 429. The engine's cooldown covers every guest input (D60), so a brightness or power
change made inside it **waits for its end and then goes out** (the latest wins; `state.next` and a "… in a moment"
message say so) — it is never dropped after the panel was told 200, and guest bursts stay one per cooldown.

**The canopy picture.** `canopy()` is the 270-light geometry and `state()["lit"]` what each canopy position truthfully
shows (`conductor/canopy_view.py`: off, dark, unknown, lit or changing, with full-strength colours and the level).

**The menu and the review cards (D76).** `GuestPanel.menu()` is `conductor/ipad_menu.live_menu`: the live catalog
less `kiosk.hidden_scenes`. While more than six scenes are visible (the review menu) each card carries a still
thumbnail of its scene at the tier the engine would show it at now (`thumb_basis`, every visible scene's likely plan
as /api/kiosk/modes reads it): per light only when that plan is canvas or slot (T1/T2: a hand-painted slot copies
the canvas picture), else its nine module colours (module level and the stepped hold), and none for a whole-strand
look, including a scene the reconciler has eased down to its strand rung — a picture is copy too. Thumbnails are
cached per scene, per live-looks generation and basis, and computed synchronously: a thread under the virtual clock
would let virtual time jump while it ran.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import math
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, Any, Literal

from conductor import canopy_view, ipad_menu
from conductor.clock import Clock
from conductor.engine_api import CanopyPhase, CanopyState, ModeRefused, PlanError, Tier
from conductor.gate import Purpose
from conductor.routes import kiosk as K
from conductor.scenes import Scene, SceneCatalog
from conductor.show import registered_canopy_ids

if TYPE_CHECKING:
    from conductor.looks.schema import ModesFile

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
# a held change goes out this long after the cooldown ends, so the engine never sees it inside the cooldown
HOLD_MARGIN_S = 0.02
# A brightness or power change waiting out the guest cooldown (it goes out when the cooldown ends; kiosk copy is true)
NEXT_BRIGHTNESS = "Your brightness is next. It reaches the lights in a moment."
NEXT_OFF = "The lights turn off in a moment."
NEXT_ON = "The lights turn on in a moment."


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
    time, and jobs start at least `gap()` seconds apart — and, for a job `hold(kind)` holds (the guest cooldown), no
    sooner than that many seconds from now: a held job waits, it is never dropped. `on_idle` runs when the last job
    has finished."""

    def __init__(
        self,
        clock: Clock,
        run: Callable[[str, Any], Awaitable[None]],
        gap: Callable[[], float],
        on_idle: Callable[[], None] = lambda: None,
        hold: Callable[[str], float] = lambda _kind: 0.0,
    ):
        self.clock = clock
        self.run = run
        self.gap = gap
        self.on_idle = on_idle
        self.hold = hold
        self.pending: dict[str, Any] = {}
        self.running: str | None = None
        self.task: asyncio.Task | None = None
        self._last_start: float | None = None

    @property
    def busy(self) -> bool:
        return bool(self.pending) or self.running is not None

    @property
    def held(self) -> bool:
        """The next job is waiting out a hold (not the short gap): nothing is being sent for it yet."""
        if self.running is not None or not self.pending:
            return False
        return self._hold_left(next(iter(self.pending))) > 0

    def has(self, kind: str) -> bool:
        return self.running == kind or kind in self.pending

    def _hold_left(self, kind: str) -> float:
        try:
            return max(0.0, float(self.hold(kind)))
        except Exception:  # a hold that can't be read never blocks the guest's change
            log.exception("guest panel: the %s hold failed", kind)
            return 0.0

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
                wait = max(wait, self._hold_left(next(iter(self.pending))))
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
        if self.running is None:  # only waiting (a gap or a hold): nothing is on its way to the lights
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except TimeoutError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def _ms(start: float, end: float) -> int:
    return round((end - start) * 1000)


def scene_json(scene: Scene, thumb: dict | None = None) -> dict:
    """One scene as the panel sends it. `group` is its base's id (or its own); `thumb` is its review card's picture
    (`ipad_menu.design_thumb`: `{sig, basis, modules}`), sent only while the review menu shows, else None."""
    return {
        "id": scene.id,
        "name": scene.name,
        "mood": scene.mood,
        "description": scene.description,
        "palette": [{"name": c.name, "hex": c.hex} for c in scene.palette],
        "frame": [list(rgb) for rgb in scene.frame(HEADS)],
        "solid": scene.solid.hex,
        "brightness_draft": scene.brightness_draft,
        "option_of": scene.option_of,
        "group": scene.group,
        "thumb": thumb,
    }


# ---- the review cards' basis (D76) ----------------------------------------------------------------------------------
# The tiers that light a scene per light: a canvas (T1) and a hand-painted slot (T2, which copies the canvas picture).
PER_LIGHT_TIERS = (str(Tier.T1), str(Tier.T2))
Basis = Literal["heads", "modules"]


def thumb_basis(engine: Any, modes: ModesFile | None, scene_ids: Iterable[str]) -> dict[str, Basis | None]:
    """How each scene's review card draws it: the tier the engine would show it at now (a picture is copy too).

    Every scene with a mode asks the engine for its likely plan (`routes.kiosk._likely_plan`, the rule
    /api/kiosk/modes uses for every listed mode): the plan showing now when it is that scene's, else `compile_best`,
    which honours the reconciler's hysteresis floor. So a modules- or stepped-first scene the reconciler has eased
    down to its strand rung draws as the one whole-strand look the ceiling shows, never as nine module colours. Then:

    - canvas or slot (T1/T2) → "heads";
    - strand → None (one whole-strand look: no picture);
    - module level, or stepped (its hold) → "modules" when it has a modules rung, else None;
    - no mode → None; no engine, or no plan from it → its first rung's picture: strand-first → None, else "modules"
      when it has a modules rung.

    On the show box that is module level until per-light is commissioned (07 §6, D73), and a Lab try-on's scene runs
    module level while it stands (TRIAL_SKIP). One compile per visible scene per poll is what /api/kiosk/modes already
    pays (tests/test_ipad_menu.py times it on the sim engine)."""
    out: dict[str, Basis | None] = {}
    current: Any = None
    current_read = False
    for sid in scene_ids:
        mode = modes.modes.get(sid) if modes is not None else None
        if mode is None:
            out[sid] = None
            continue
        fallback: Basis | None = "modules" if mode.techniques.modules is not None else None
        plan = None
        if engine is not None:
            if not current_read:
                current, current_read = K._current_plan(engine), True
            plan = K._likely_plan(engine, sid, current)
        if plan is None:
            out[sid] = None if str(mode.ladder[0]) == str(Tier.STRAND) else fallback
            continue
        tier = str(plan.tier)
        if tier in PER_LIGHT_TIERS:
            out[sid] = "heads"
        elif tier == str(Tier.STRAND):
            out[sid] = None
        else:
            out[sid] = fallback
    return out


def guest_level(level: int) -> int:
    return max(GUEST_FLOOR, min(100, int(level)))


# ---- the canopy engine ------------------------------------------------------------------------------------------
# why guests wait on the canopy, from the engine's own flags: "try-on" (a Design Lab try-on, D66) or "testing" (the
# stepped rehearsal); the iPad drops a waiting toast once it clears
_WAITING_MESSAGES = {K.TRYING_ON: "try-on", K.TESTING: "testing", K.CHANGING: "changing"}


def _waiting(engine: Any) -> str | None:
    if K._flag(engine, "repaint_quiet") is True:  # a publish's slot repaint (D68 PR B2): a few seconds
        return "changing"
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
        self.lane = Lane(services.clock, self._run, self._gap, on_idle=self._lane_idle, hold=self._hold)
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

    def _hold(self, _kind: str) -> float:
        """Seconds the engine's guest cooldown has left: a brightness or power change made inside it waits for its
        end and then goes out (the latest wins), instead of being refused after the panel was told 200. The
        cooldown still spaces guest bursts by their cost in writes (D70), so the account's write budget holds."""
        engine = self.engine()
        if engine is None:
            return 0.0
        until = getattr(self._engine_state(engine), "cooldown_until", None)
        if until is None or not math.isfinite(until):
            return 0.0
        left = until - self.services.clock.now()
        return left + HOLD_MARGIN_S if left > 0 else 0.0

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
            "next": None,
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
        held = self.lane.held and cooldown > 0
        if not st.guest_input:
            phase, message = "paused", K.PAUSED
        elif st.phase == CanopyPhase.RESTING:
            phase, message = "resting", (K.BEING_SET_UP if st.message == K.BEING_SET_UP else K.RESTING)
        elif look == CanopyPhase.UPDATING or (self.lane.busy and not held):
            phase, message = "changing", CHANGING
        elif held:  # the lights are steady; the guest's change waits for the cooldown, then goes out
            phase, message = "cooldown", self._next_message()
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
            # the guest's brightness / power still on its way (queued or waiting out the cooldown): the panel keeps
            # its control there instead of snapping back to `brightness` / `power`, which stay the engine's truth
            "next": self._next(),
        }

    def _next(self) -> dict | None:
        out = {kind: self.lane.pending[kind] for kind in ("brightness", "power") if kind in self.lane.pending}
        return out or None

    def _next_message(self) -> str:
        """What the queued changes end with: they run in arrival order, so the last one decides."""
        last = next(reversed(self.lane.pending), None)
        if last == "brightness":
            return NEXT_BRIGHTNESS
        return NEXT_OFF if self.lane.pending.get("power") is False else NEXT_ON

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
        # the review cards' thumbnails: (scene id, basis) → thumb, for one (live-looks generation, trio)
        self._thumbs: dict[tuple[str, str | None], dict | None] = {}
        self._thumbs_for: tuple[int, int] | None = None

    @classmethod
    def of(cls, services: Any) -> GuestPanel:
        panel = services.extras.get(PANEL_KEY)
        if panel is None:
            panel = services.extras[PANEL_KEY] = cls(services)
        return panel

    def menu(self) -> ipad_menu.Menu:
        """The iPad's menu over the live catalog (D68: approved or published) with kiosk.hidden_scenes in force."""
        return ipad_menu.live_menu(self.services)

    def scenes(self) -> SceneCatalog:
        """The scenes the iPad offers (kiosk.hidden_scenes left out, D76)."""
        return self.menu().visible

    def thumbs(self, menu: ipad_menu.Menu) -> dict[str, dict | None]:
        """Each visible scene's review-card thumbnail, only while the review menu shows (else {} and nothing is
        computed). Cached per (scene, basis) for one published look: a basis flip (a Lab try-on, a commissioned
        slot) recomputes only that scene. Synchronous on purpose (see the module docstring)."""
        if not menu.review or menu.trio is None:
            return {}
        ids = [s.id for s in menu.visible.scenes]
        basis = thumb_basis(self.engine_target.engine(), menu.trio.modes, ids)
        stamp = (menu.generation, id(menu.trio))
        if stamp != self._thumbs_for:
            self._thumbs = {}
            self._thumbs_for = stamp
        missing = {sid: basis[sid] for sid in ids if (sid, basis[sid]) not in self._thumbs}
        if missing:
            got = ipad_menu.catalog_thumbs(
                menu.trio, self.engine_target._canopy_cfg(), self.services.config_dir, missing
            )
            for sid, b in missing.items():
                self._thumbs[(sid, b)] = got.get(sid)
        return {sid: self._thumbs[(sid, basis[sid])] for sid in ids}

    async def target(self) -> EngineTarget:
        """The one target: the canopy engine, on every runtime (D67)."""
        return self.engine_target

    async def close(self) -> None:
        await self.engine_target.close()
