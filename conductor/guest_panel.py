"""The guest panel: the iPad on the stand (/kiosk) choosing one of the six approved scenes, brightness and power.

One API (`conductor/routes/guest.py`, under /api/kiosk) over two targets, chosen per runtime by `choose_target`:

- **WorkshopTarget** — the VWBD workshop laptop: the runtime environment starts with "workshop" (CONDUCTOR_ENV, the
  same source as the home redirect in conductor/app.py) and both shop modules are registered (bench-kit, "Kingston
  test"; shop-string-1, "String light 1"). It drives them only through the module page's own code paths —
  `ModuleSession` (executor, live gate, allowlist, lease), `pair.apply_pair` and `scene_gradient.apply` — never a
  driver or the executor directly.
- **EngineTarget** — everywhere else (venue box, staging, a local sim): a scene is `engine.request_mode(scene_id,
  source="kiosk")` with the kiosk's own refusals; brightness and power go to `engine.set_guest_brightness` /
  `engine.set_guest_power` only when the engine has them (`capabilities` says whether it does).

Workshop design
---------------
**Sessions.** On the first guest action the target opens one ModuleSession per shop module, owned by "guest panel"
(the lock owner the module page names), never with `steal`. If anyone else holds either module (the module page or
the Studio is open) the guest gets "Someone is using the module page…" and nothing is sent. Both sessions close
(releasing the locks) after `IDLE_RELEASE_S` without a guest action, at shutdown, and when the runtime stops choosing
this target. Ops can still take a module over from the module page; the panel notices the lost lease and refuses
calmly from then on.

**One lane.** Every write runs in one background worker per target, under the Studio's pair lock, one job at a time.
Each job waits for both modules' verified readback, and jobs start no closer together than the executor's
`min_write_interval_s` (the executor's own per-device budget still applies underneath). Pending work is coalesced and
the latest request wins: at most one pending scene, one brightness and one power. A dragging slider therefore gives
one brightness write per module at a time, ending on the last level. A scene drops the brightness and power requests
queued before it (a scene sets its own brightness and turns the strands on). Scene choices also have the kiosk's
cooldown (`settings.kiosk.cooldown_s`, 8 s, as /api/kiosk/select): a second choice inside it is a 429.

**Scenes.** A scene runs at its `brightness_draft` (raised to the guest floor). The colours are full strength;
brightness is the separate device setting, never multiplied in.
- Gradient: when BOTH modules have their own valid controller map (`framemap.frame_indices`, through the session) and
  per-head frames are allowed (`t1_status`), the scene's 30-light gradient (`Scene.frame(30)`) goes on both through
  `scene_gradient.apply`, the Studio's path: Custom 1 only (Custom 3 is never written), a dark reset, then the frame
  with the brightness in the same PUT.
- Solid: otherwise the scene's solid colour (`Scene.solid`) as one whole-strand RGB look on both (`apply_pair`). It
  touches no slot.

**Repeated scene changes** against scene_gradient's record (`<data>/studio-gradients/active.json`, shared with the
Studio):
- The first gradient backs up the user's original Custom 1 and active look (a durable backup file, plus `original` in
  the record) and leaves the record pending. Each later gradient finds it pending and checks that Custom 1 still holds
  the original or the last frame the app wrote (`allowed`). `original` is never replaced, so the protected backup is
  always the look from before the first gradient. The guest panel never restores or discards it: the Studio's
  "Restore previous look" does that, and the next gradient after a restore takes a fresh backup.
- A solid look never writes Custom 1, so it never disturbs the record.
- If Custom 1 changed outside the app (painted in the Hubspace app, say), `scene_gradient.apply` refuses before sending
  anything. The panel then shows the scene's solid colour instead, so it never gets stuck behind a pending record and
  nothing in Custom 1 is overwritten. The debug view says why (`gradient_note`).
- If a gradient fails after writing, scene_gradient restores the backup itself (the original look comes back). The
  guest is told the scene didn't finish; the next choice starts again from the same backup.

**State.** power and brightness come from the modules' readback (the open sessions, else the state tracker). The scene
is the one the last job applied, and only while no one else has written either module since (the tracker's last
command). `feature` is the true sentence for what is showing (gradient or solid), else null, and `look` says which
("gradient" | "solid", null while changing or on the engine target) so the panel's preview draws what is lit.

Try it without hardware: `make guest-dev` (scripts/dev_guest_panel.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from conductor.clock import Clock
from conductor.config import ConductorSettings
from conductor.engine_api import CanopyPhase, CanopyState, ModeRefused, PlanError
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager, LockError
from conductor.module import scene_gradient
from conductor.module.pair import PAIR_IDS, apply_pair
from conductor.module.session import ModuleError, ModuleSession
from conductor.routes import kiosk as K
from conductor.scenes import Scene, SceneCatalog, load_scenes

log = logging.getLogger(__name__)

PANEL_KEY = "guest_panel"
GUEST_OWNER = "guest panel"  # the lock owner the module page and the Studio name ("in use by guest panel")
GUEST_FLOOR = 10  # the lowest brightness a guest can set
IDLE_RELEASE_S = 90.0  # release the shop modules this long after the last guest action
LOG_SIZE = 50
HEADS = 30
CLOSE_WAIT_S = 15.0  # at shutdown, let a running job finish (a gradient restores itself if cut short)

FEATURE_GRADIENT = "Every light set individually as a Hubspace custom pattern on both strands."
FEATURE_SOLID = "Each Hubspace strand set to one colour from the scene."
FEATURES = {"gradient": FEATURE_GRADIENT, "solid": FEATURE_SOLID}

CHANGING = "Changing the light…"
BUSY = "Someone is using the module page right now. Try again in a moment."
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


# ---- the workshop: two real shop modules through the module page's code paths -----------------------------------
@dataclass
class WorkshopDeps:
    clock: Clock
    settings: ConductorSettings
    data: Path  # scene_gradient keeps its record and backups under here
    locks: DeviceLockManager
    open_session: Callable[
        [str], Awaitable[ModuleSession]
    ]  # takes the device's lock as "guest panel", never steals
    address_of: Callable[
        [str], str | None
    ]  # the device's bench address (its lock key), None when it has none
    tracker: Any = None  # DeviceStateTracker (readback and last command while no session is open)
    pair_lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # the Studio's paired-command lock
    log: CommandLog | None = None
    simulated: Callable[[], bool] = lambda: True


class WorkshopTarget:
    """The two VWBD shop modules as one guest target (see the module docstring)."""

    kind = "workshop"
    label = "Two shop strands (60 lights)"

    def __init__(self, deps: WorkshopDeps):
        self.deps = deps
        self.idle_release_s = IDLE_RELEASE_S
        self.sessions: dict[str, ModuleSession] | None = None
        self.lane = Lane(deps.clock, self._run, self._gap, on_idle=self._lane_idle)
        self.requested: dict[str, Any] = {}  # what the guest asked for while the lane works
        self.scene_id: str | None = None  # the scene the last scene job applied
        self.shown: str | None = None  # "gradient" | "solid"
        self.fingerprint: tuple | None = None  # each module's last command after our last job
        self.cooldown_until: float | None = None  # wall clock
        self.message = ""
        self.gradient_note: str | None = None  # why the last scene was shown solid
        self.last_job: dict | None = None
        self.last_activity = deps.clock.monotonic()
        self._opening = asyncio.Lock()
        self._idle_task: asyncio.Task | None = None

    # ---- what the panel can do -------------------------------------------------------------------------------------
    def capabilities(self) -> dict[str, bool]:
        return {"scene": True, "brightness": True, "power": True}

    @property
    def simulated(self) -> bool:
        if self.sessions:
            return all(s.target.simulated for s in self.sessions.values())
        return self.deps.simulated()

    def _gap(self) -> float:
        return float(self.deps.settings.executor.min_write_interval_s)

    def _touch(self) -> None:
        self.last_activity = self.deps.clock.monotonic()

    def _record(self, action: str, params: dict, outcome: str, **kw: Any) -> None:
        if self.deps.log is not None:
            self.deps.log.record(action, params, outcome, target=self.kind, **kw)

    def _cooldown_remaining(self, now: float) -> float:
        if self.cooldown_until is None:
            return 0.0
        return round(min(K.MAX_RETRY_AFTER_S, max(0.0, self.cooldown_until - now)), 3)

    # ---- guest actions (each refuses calmly or queues one job) ------------------------------------------------------
    async def scene(self, scene: Scene) -> None:
        now = self.deps.clock.now()
        wait = self._cooldown_remaining(now)
        if wait > 0:
            raise GuestRefused(*K.cooldown_refusal(wait), why="inside the scene cooldown")
        if self.lane.has("scene"):
            raise GuestRefused(
                429, K.COOLDOWN, {"retry_after_s": STILL_CHANGING_S}, why="a scene is still being applied"
            )
        # Claim the cooldown before opening the modules (a profile readback): a second scene arriving meanwhile is a
        # 429, not a second accepted scene. Opening refused → the claim is given back.
        claimed, self.cooldown_until = self.cooldown_until, now + float(self.deps.settings.kiosk.cooldown_s)
        try:
            await self._ensure_sessions()
        except GuestRefused:
            self.cooldown_until = claimed
            raise
        level = guest_level(scene.brightness_draft)
        self.requested.update(scene=scene.id, power=True, brightness=level)
        self.message = ""
        self.lane.put("scene", (scene, level), drop=("brightness", "power"))
        self._touch()

    async def brightness(self, level: int) -> int:
        level = guest_level(level)
        await self._ensure_sessions()
        self.requested["brightness"] = level
        self.message = ""
        self.lane.put("brightness", level)
        self._touch()
        return level

    async def power(self, on: bool) -> None:
        await self._ensure_sessions()
        self.requested["power"] = bool(on)
        self.message = ""
        self.lane.put("power", bool(on))
        self._touch()

    def _lane_idle(self) -> None:
        self.requested = {}
        self._touch()

    # ---- sessions ----------------------------------------------------------------------------------------------------
    def _holding(self) -> bool:
        return self.sessions is not None and all(s.lease_alive() for s in self.sessions.values())

    def _other_holder(self) -> tuple[str, str] | None:
        """(device id, owner) of a shop module someone other than this panel holds, else None."""
        for device_id in PAIR_IDS:
            address = self.deps.address_of(device_id)
            lease = self.deps.locks.holder(address) if address else None
            if lease is None:
                continue
            mine = (self.sessions or {}).get(device_id)
            if mine is None or lease.token != mine.lease.token:
                return device_id, lease.owner
        return None

    async def _ensure_sessions(self) -> dict[str, ModuleSession]:
        async with self._opening:
            if self.sessions is not None:
                if self._holding():
                    return self.sessions
                await self._drop_sessions()  # ops took a module over, or a lease expired
                self._record("release", {"reason": "lease lost"}, "released")
            holder = self._other_holder()
            if holder is not None:
                raise GuestRefused(409, BUSY, why=f"{holder[0]} is held by {holder[1]}")
            opened: dict[str, ModuleSession] = {}
            try:
                for device_id in PAIR_IDS:
                    session = await self.deps.open_session(device_id)
                    opened[device_id] = session
                    if not session.lease_alive():  # taken over while its profile was being read
                        raise LockError(f"{device_id} was taken over while it was opening")
            except (LockError, ModuleError, LookupError) as e:
                for session in opened.values():
                    if session.lease_alive():
                        await session.close()
                    else:
                        session.abandon()
                why = e.message if isinstance(e, ModuleError) else str(e)
                holder = self._other_holder()
                if holder is not None:
                    raise GuestRefused(409, BUSY, why=f"{holder[0]} is held by {holder[1]}") from None
                raise GuestRefused(503, LIGHTS_RESTING, why=why) from None
            self.sessions = opened
            self._touch()
            self._record("open", {"modules": list(PAIR_IDS)}, "opened")
            if self._idle_task is None or self._idle_task.done():
                self._idle_task = asyncio.get_running_loop().create_task(
                    self._idle_watch(), name="guest-panel-idle"
                )
            return opened

    async def _drop_sessions(self) -> None:
        sessions, self.sessions = self.sessions, None
        for session in (sessions or {}).values():
            if session.lease_alive():
                await session.close()
            else:
                session.abandon()  # never release: the lock is someone else's now

    async def release(self, reason: str) -> None:
        """Close both sessions (releasing the locks we still hold)."""
        async with self._opening:
            if self.sessions is None:
                return
            await self._drop_sessions()
            self._record("release", {"reason": reason}, "released")

    async def _idle_watch(self) -> None:
        while self.sessions is not None:
            remaining = self.last_activity + self.idle_release_s - self.deps.clock.monotonic()
            if remaining <= 0 and not self.lane.busy:
                await self.release("idle")
                return
            await self.deps.clock.sleep(max(0.5, min(remaining, 5.0)))

    async def close(self) -> None:
        if self._idle_task is not None and not self._idle_task.done():
            self._idle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._idle_task
        await self.lane.close()
        await self.release("shutdown")

    async def wait_idle(self) -> None:
        await self.lane.wait_idle()

    # ---- jobs (in the lane) ------------------------------------------------------------------------------------------
    async def _run(self, kind: str, value: Any) -> None:
        clock = self.deps.clock
        started = clock.monotonic()
        params = (
            {"scene": value[0].id, "brightness": value[1]}
            if kind == "scene"
            else {"level": value}
            if kind == "brightness"
            else {"on": value}
        )
        outcome, error = "failed", None
        try:
            async with self.deps.pair_lock:
                if not self._holding():
                    raise GuestRefused(409, BUSY, why="the guest panel no longer holds both modules")
                sessions = [self.sessions[d] for d in PAIR_IDS]  # type: ignore[index]
                if kind == "scene":
                    outcome, error = await self._apply_scene(sessions, *value)
                elif kind == "brightness":
                    outcome, error = await self._apply_whole(sessions, {"brightness": value})
                else:
                    outcome, error = await self._apply_whole(sessions, {"power": value})
        except GuestRefused as e:
            outcome, error = "refused", e.why
            self.message = e.message
            await self.release("lease lost")
        except ModuleError as e:
            outcome, error = "failed", e.message
            self.message = DIDNT_FINISH if kind == "scene" else NOT_REACHED
            if kind == "scene":
                self.scene_id, self.shown = None, None
        finally:
            self.fingerprint = self._fingerprint()
            latency = _ms(started, clock.monotonic())
            self.last_job = {"kind": kind, "params": params, "outcome": outcome, "error": error}
            self._record(kind, params, outcome, latency_ms=latency, error=error)

    def gradient_ready(self, sessions: list[ModuleSession]) -> tuple[bool, str | None]:
        """Both modules can take the scene gradient: each its own valid 30-light map, per-head frames allowed, and a
        Custom 1 slot. (scene_gradient.apply checks the same again, after a fresh readback.)"""
        for s in sessions:
            if s.n != HEADS or s.frame_indices() is None:
                return False, f"{s.device_id} has no verified {HEADS}-light controller map"
            ok, why = s.t1_status()
            if not ok:
                return False, f"{s.device_id}: {why}"
            if scene_gradient.SLOT not in s.caps.slots:
                return False, f"{s.device_id} has no Custom 1 slot"
        return True, None

    async def _apply_scene(
        self, sessions: list[ModuleSession], scene: Scene, level: int
    ) -> tuple[str, str | None]:
        ready, why = self.gradient_ready(sessions)
        if ready:
            marks = [s.results[0] if s.results else None for s in sessions]
            try:
                result = await scene_gradient.apply(
                    sessions,
                    [list(rgb) for rgb in scene.frame(HEADS)],
                    level,
                    f"guest panel: {scene.name}",
                    self.deps.data,
                    sleep=self.deps.clock.sleep,
                )
            except ModuleError as e:
                if any((s.results[0] if s.results else None) is not m for s, m in zip(sessions, marks)):
                    # It wrote and then couldn't restore its backup: the Studio's "Restore previous look" is the
                    # way back. Nothing more is sent from here.
                    self.scene_id, self.shown, self.message = None, None, DIDNT_FINISH
                    return "failed", e.message
                why = e.message  # refused before sending anything, e.g. Custom 1 changed outside the app
            else:
                if result.get("ok"):
                    self.scene_id, self.shown, self.gradient_note = scene.id, "gradient", None
                    return "applied: gradient", None
                self.scene_id, self.shown, self.message = None, None, DIDNT_FINISH
                return "failed: gradient rolled back", f"stopped at {result.get('phase')}"
        self.gradient_note = why
        look = {"kind": "rgb", "rgb": list(scene.solid.rgb), "brightness": level}
        result = await apply_pair(sessions, {"look": look})
        if result["ok"]:
            self.scene_id, self.shown = scene.id, "solid"
            return "applied: solid colour", why
        self.scene_id, self.shown, self.message = None, None, NOT_REACHED
        return "failed", _module_errors(result)

    async def _apply_whole(self, sessions: list[ModuleSession], action: dict) -> tuple[str, str | None]:
        result = await apply_pair(sessions, action)
        if result["ok"]:
            return "applied", None
        self.message = NOT_REACHED
        return "failed", _module_errors(result)

    # ---- state -------------------------------------------------------------------------------------------------------
    def _fingerprint(self) -> tuple | None:
        if self.deps.tracker is None:
            return None
        out = []
        for device_id in PAIR_IDS:
            address = self.deps.address_of(device_id)
            last = (self.deps.tracker.snapshot(address).get("last_command") if address else None) or {}
            out.append((last.get("label"), (last.get("timings") or {}).get("queued_at")))
        return tuple(out)

    def _readback(self, device_id: str) -> dict:
        session = (self.sessions or {}).get(device_id)
        if session is not None and session.reported_rows:
            return {k: v for k, v in session._reported_state().items()}
        address = self.deps.address_of(device_id)
        if self.deps.tracker is None or not address:
            return {}
        rows = self.deps.tracker.snapshot(address).get("reported") or []
        return {(r["functionClass"], r.get("functionInstance")): r.get("value") for r in rows}

    def _reported(self) -> tuple[bool | None, int | None]:
        states = [self._readback(d) for d in PAIR_IDS]
        powers = {s.get(("power", None)) for s in states}
        power = True if powers == {"on"} else False if powers == {"off"} else None
        levels = [s.get(("brightness", None)) for s in states]
        known = [v for v in levels if isinstance(v, int) and not isinstance(v, bool)]
        brightness = round(sum(known) / len(known)) if known else None
        return power, brightness

    def state(self, scene_ids: Iterable[str] = ()) -> dict:
        now = self.deps.clock.now()
        power, brightness = self._reported()
        # Our own brightness/power job changes each module's last command before `fingerprint` is refreshed; the
        # scene is still showing (at the new brightness), so it stays ours while that job runs.
        own_job = self.lane.running in ("brightness", "power") and not self.lane.has("scene")
        ours = self.scene_id is not None and (own_job or self.fingerprint == self._fingerprint())
        scene = self.scene_id if ours else None
        shown = self.shown if ours else None
        working = self.lane.busy
        scene_changing = self.lane.has("scene")
        if working:
            power = self.requested.get("power", power)
            brightness = self.requested.get("brightness", brightness)
            scene = self.requested.get("scene", scene)
        cooldown = self._cooldown_remaining(now)
        holder = None if self._holding() else self._other_holder()
        if holder is not None:
            phase, message = "resting", BUSY
        elif working:
            phase, message = "changing", CHANGING
        elif cooldown > 0:
            phase, message = "cooldown", self.message
        else:
            phase, message = "ready", self.message
        feature = None if scene_changing or power is False or shown is None else FEATURES[shown]
        return {
            "power": power,
            "brightness": brightness,
            "scene": scene,
            "look": None if scene_changing else shown,
            "phase": phase,
            "message": message,
            "feature": feature,
            "cooldown_remaining_s": cooldown,
        }

    def lease_holders(self) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for device_id in PAIR_IDS:
            address = self.deps.address_of(device_id)
            lease = self.deps.locks.holder(address) if address else None
            out[device_id] = lease.owner if lease else None
        return out

    def debug(self) -> dict:
        modules = {}
        for device_id in PAIR_IDS:
            session = (self.sessions or {}).get(device_id)
            info: dict[str, Any] = {"session_open": session is not None}
            if session is not None:
                ok, why = session.t1_status()
                info.update(
                    lease_alive=session.lease_alive(),
                    session_id=session.session_id,
                    simulated=session.target.simulated,
                    mapped=session.frame_indices() is not None,
                    per_head_allowed=ok,
                    per_head_reason=why,
                    reported_at=session.reported_at,
                    last_read_error=session.last_read_error,
                    last_result=session.results[0] if session.results else None,
                )
            modules[device_id] = info
        sessions = [self.sessions[d] for d in PAIR_IDS] if self._holding() else None  # type: ignore[index]
        ready = self.gradient_ready(sessions) if sessions else (None, "the modules aren't open")
        idle_in = self.last_activity + self.idle_release_s - self.deps.clock.monotonic()
        return {
            "modules": modules,
            "holding_both": self._holding(),
            "gradient_ready": ready[0],
            "gradient_ready_reason": ready[1],
            "gradient_note": self.gradient_note,
            "gradient_record": scene_gradient.status(self.deps.data),
            "lane": {"running": self.lane.running, "pending": list(self.lane.pending)},
            "requested": dict(self.requested),
            "scene_applied": self.scene_id,
            "shown": self.shown,
            "scene_still_showing": self.scene_id is not None and self.fingerprint == self._fingerprint(),
            "cooldown_until": self.cooldown_until,
            "idle_release_s": self.idle_release_s,
            "release_in_s": round(idle_in, 1) if self.sessions is not None else None,
            "last_job": self.last_job,
        }


def _module_errors(result: dict) -> str:
    bad = []
    for device_id, reply in (result.get("modules") or {}).items():
        r = reply.get("result") or {}
        if r.get("status") != "applied":
            bad.append(f"{device_id}: {r.get('error') or r.get('status')}")
    return "; ".join(bad) or "not confirmed"


# ---- everywhere else: the canopy engine --------------------------------------------------------------------------
class EngineTarget:
    """The canopy engine at `services.extras["canopy"]` (see the module docstring)."""

    kind = "canopy"
    label = "The Color Canopy (nine strands)"

    def __init__(self, services: Any, log: CommandLog | None = None):
        self.services = services
        self.log = log
        self.lane = Lane(services.clock, self._run, self._gap, on_idle=self._lane_idle)
        self.requested: dict[str, Any] = {}
        self.accepted: dict[str, Any] = {}  # the last brightness/power the engine took
        self.message = ""

    def engine(self) -> Any:
        return self.services.extras.get(K.ENGINE_KEY)

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
        }
        if engine is None:
            return resting
        try:
            st = K.kiosk_view(engine, engine.state())
        except Exception:
            log.exception("The canopy engine couldn't report its state.")
            return resting
        cooldown = K._remaining(st.cooldown_until, now)
        if not st.guest_input:
            phase, message = "paused", K.PAUSED
        elif st.phase == CanopyPhase.RESTING:
            phase, message = "resting", (K.BEING_SET_UP if st.message == K.BEING_SET_UP else K.RESTING)
        elif st.phase == CanopyPhase.UPDATING or self.lane.busy:
            phase, message = "changing", CHANGING
        elif cooldown > 0:
            phase, message = "cooldown", self.message
        else:
            phase, message = "ready", self.message
        power = getattr(st, "power", None)
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
            # never a technique sentence for lights that are off or still changing scene (kiosk copy must be true)
            "feature": None if power is False or st.phase == CanopyPhase.UPDATING else st.feature,
            "cooldown_remaining_s": cooldown,
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


# ---- choosing the target ------------------------------------------------------------------------------------------
def choose_target(services: Any) -> str:
    """ "workshop" when the runtime is the VWBD workshop and both shop modules are registered, else "canopy"."""
    if not services.gate_settings.environment.startswith("workshop"):
        return EngineTarget.kind
    for device_id in PAIR_IDS:
        try:
            services.registry.get(device_id)
        except KeyError:
            return EngineTarget.kind
    return WorkshopTarget.kind


def workshop_deps(services: Any, log: CommandLog | None = None) -> WorkshopDeps:
    """The workshop target wired to the running Conductor: sessions opened the way the module page opens them."""
    from conductor.module.manager import ModuleManager

    manager = ModuleManager.of(services)

    async def open_session(device_id: str) -> ModuleSession:
        target = services.target(device_id, Purpose.BENCH)
        return await ModuleSession.open(manager.deps(), target, owner=GUEST_OWNER)

    def address_of(device_id: str) -> str | None:
        try:
            return services.target(device_id, Purpose.BENCH).address
        except LookupError:
            return None

    def simulated() -> bool:
        try:
            return all(services.bench_driver_for(d).simulated for d in PAIR_IDS)
        except LookupError:
            return True

    return WorkshopDeps(
        clock=services.clock,
        settings=services.settings,
        data=Path(services.data),
        locks=services.locks,
        open_session=open_session,
        address_of=address_of,
        tracker=services.tracker,
        pair_lock=services.extras.setdefault("studio_pair_lock", asyncio.Lock()),
        log=log,
        simulated=simulated,
    )


class GuestPanel:
    """The guest panel's state for one Conductor (`services.extras["guest_panel"]`)."""

    def __init__(self, services: Any):
        self.services = services
        self.log = CommandLog(services.clock)
        self.engine_target = EngineTarget(services, self.log)
        self.workshop: WorkshopTarget | None = None
        self.last_state: dict | None = None

    @classmethod
    def of(cls, services: Any) -> GuestPanel:
        panel = services.extras.get(PANEL_KEY)
        if panel is None:
            panel = services.extras[PANEL_KEY] = cls(services)
        return panel

    def scenes(self) -> SceneCatalog:
        return load_scenes(Path(self.services.config_dir))

    async def target(self) -> WorkshopTarget | EngineTarget:
        if choose_target(self.services) == WorkshopTarget.kind:
            if self.workshop is None:
                self.workshop = WorkshopTarget(workshop_deps(self.services, self.log))
            return self.workshop
        if self.workshop is not None:  # the shop module left the registry: let go of both
            workshop, self.workshop = self.workshop, None
            await workshop.close()
        return self.engine_target

    async def close(self) -> None:
        if self.workshop is not None:
            await self.workshop.close()
        await self.engine_target.close()
