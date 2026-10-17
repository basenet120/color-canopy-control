"""Publishing a Lab version to the live show (D68, docs/live-lab/PLAN.md §2.8, §2.11): publish, Revert to approved,
Undo last publish, and the LiveJSON the Lab's show bar reads.

    POST /api/lab/publish {version, confirm?, expected_live, who?, show?}
      build (threadpool): published_trio → plan_publish → confirm sentence + cost
      one synchronous section on the loop thread (no await between the checks and the install):
        the publish lock · looks_refusal() · expected_live + generation · equals live · the slot locks · confirm
        → write_looks (history copy first; OSError → 409 read_only, nothing installed)
        → install_looks (every copy, §2.4) → engine.reload_looks' synchronous prefix (the re-pick is installed)
      then the awaits: the re-pick burst's results, the optional "show <scene>", telemetry

- **Refusals** are Lab sentences (REFUSALS), never the guest copy; nothing is written or sent on any of them.
- **Conflict:** a confirm computed against a live look someone has since replaced (`expected_live`, the plan's
  `generation`) is refused with the new LiveJSON, so two people on one ops login never silently replace each other.
- **Slot locks (§2.6.0):** a painted design change where a module's slot is hand-painted in the app is refused (the
  iPad would describe colours the lights don't show); B1, on hardware: one where the slot is commissioned (api) is
  refused too (the repaint job is PR B2) — except Revert to approved and Undo, which are always allowed (and in the
  simulator, as D66's T1 lock was off there, every such publish is): the scene is held off T1 (it shows truthfully at
  module level) and the reconciler's stale rule rewrites the slots (≤ once per 10 min per slot), then the hold lifts.
- **Rollback:** an exception after the write puts the previous file back byte for byte, installs the previous trio
  and re-picks the current look under it (else the safe look), alerts, and answers a plain 500.

Calls only conductor.looks.live and the engine/wiring — never a driver or the executor (every write is the engine's).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from conductor.config import ConfigError
from conductor.design_lab import draft as D
from conductor.design_lab import preview as P
from conductor.design_lab import store as S
from conductor.looks import scene_rules
from conductor.looks.live import (
    LOCK_KEY,
    MAX_HISTORY,
    ConfigTrio,
    HistoryEntry,
    LiveLooks,
    Publication,
    ShowLooksFile,
    _sha,
    changed_scenes,
    install_looks,
    live_looks,
    restore_file,
    scene_source,
    trio_sha,
    validate_trio,
    write_looks,
)

if TYPE_CHECKING:
    from conductor.design_lab.service import LabService

log = logging.getLogger(__name__)

NO_ENGINE = "The canopy engine isn't running here, so nothing can be published to the show."
NO_DATA = "This runtime has no data folder, so a publish couldn't survive a restart."
REFUSALS: dict[str, str] = {
    "stopped": "The canopy engine is stopped (canopy-mac start).",
    "commissioning": "A kit is being commissioned (/ops/bench); publish once it ends.",
    "outage": (
        "Hubspace isn't answering (resting since {since}), so nothing can reach the lights. Your version is saved; "
        "publish it once /ops says the canopy is back."
    ),
    "rehearsal": "A stepped rehearsal is running; stop it first.",
    "repainting": "{scene}'s slots are being repainted (about {left} s left).",  # B2
}
BUSY = "Another publish is in progress."
NO_PREVIOUS = "There is no earlier publish to go back to."
SAME = "This is already what the show runs."
CONFLICT = (
    "The show changed while you were deciding: “{name}” was published at {time} by {who}. Look again before "
    "publishing."
)
LIVE_VERSION = "This version is live on the show; publish another (or revert) first."
FAILED = (
    "The publish failed on the server ({name}) and was rolled back: the show runs “{live}” again (the alert on /ops "
    "says more)."
)
NOT_WRITTEN = "The published looks couldn't be saved ({reason}); nothing was changed on the show."
CANOPY_OFF = "The canopy is off (guest power) — publishing doesn't turn it on."
GUEST_DIM = "A guest dimmed the canopy to {level}% — publishing keeps that level."
HOLD_NOTE = (
    " {scene} shows at module level until its slots hold this design again (the reconciler rewrites them, at most "
    "once every 10 min per slot)."
)
APPROVED_NAME = "the approved looks"
HISTORY_IN_ANSWER = 20
WHO_MAX = 40


class PublishAnswer(Exception):
    """A refusal (or a failure) with its HTTP status and body: `{detail, …}`."""

    def __init__(self, status_code: int, detail: str, **extra: Any) -> None:
        self.status_code = status_code
        self.body = {"detail": detail, **extra}
        super().__init__(detail)


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=40, pattern=r"^(approved|previous|v\d{4,})$")
    confirm: str | None = Field(None, max_length=2000)
    expected_live: str = Field(min_length=1, max_length=40)
    who: str | None = Field(None, max_length=WHO_MAX)
    show: bool = False


@dataclass(frozen=True)
class PublishPlan:
    changed: list[str]  # scenes whose look differs from the live trio
    to_approved: list[str]  # of those, the ones that end equal to git's
    repaint: dict[str, Any]  # ShowWiring.repaint_plan(new) (B1: the locks' counts)
    locked: dict[str, tuple[str, str]]  # scene → (§2.6.0 refusal sentence, field)
    held: list[str]  # painted scenes that will run at module level after the install
    driven: list[str]  # the current plan's modules when its scene changed or a try-on runs, else []
    generation: int
    live_id: str
    ends_trial: bool
    equals_live: bool
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    changes: list[dict] = field(default_factory=list)
    show_offer: str | None = None
    show_modules: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Candidate:
    """What a publish would install: the trio, and what it is."""

    trio: ConfigTrio
    kind: Literal["version", "approved", "previous"]
    name: str
    version: Any = None  # the Lab Version (kind version)
    publication: Publication | None = None  # the stored previous publication (kind previous)


# ---- small helpers ------------------------------------------------------------------------------------------------
def _clock(t: float | None) -> str:
    if t is None:
        return "?"
    try:
        return datetime.fromtimestamp(t, UTC).astimezone().strftime("%H:%M")
    except (OverflowError, OSError, ValueError):
        return "?"


def _and(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _name(trio: ConfigTrio, scene_id: str) -> str:
    try:
        return trio.scenes.get(scene_id).name
    except KeyError:
        return scene_id


def _labels(lab: LabService, ids: list[str]) -> str:
    out = []
    for device_id in ids:
        try:
            entry = lab.services.registry.get(device_id)
            out.append(entry.label or entry.module or device_id)
        except (KeyError, LookupError):
            out.append(device_id)
    return ", ".join(out)


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _record(lab: LabService, action: str, **fields: Any) -> None:
    try:
        lab.services.telemetry.record("lab", action=action, **fields)
    except Exception:  # telemetry never fails an answer
        log.exception("lab telemetry failed")


def _seed(trio: ConfigTrio, scene_id: str) -> D.SceneDraft | None:
    try:
        return D.seed_draft(trio, scene_id)
    except (KeyError, D.DraftError, ValueError):
        return None


# ---- what a publish would install -----------------------------------------------------------------------------------
def published_trio(lab: LabService, version_id: str) -> Candidate:
    """ "approved" → git's trio; "previous" → the stored previous publication's trio (works when its Lab version is
    gone or stale, as long as git's ids/ladders/slots haven't moved); else the saved version applied over git.
    PublishAnswer: 404 unknown version, 409 no previous / stale, 422 a trio validate_trio refuses. CPU: threadpool."""
    looks = live_looks(lab.services)
    if version_id == "approved":
        return Candidate(lab.approved.copy(), "approved", APPROVED_NAME)
    if version_id == "previous":
        previous = looks.file.previous
        if previous is None:
            raise PublishAnswer(409, NO_PREVIOUS)
        if previous.kind == "approved" or previous.trio is None:
            return Candidate(lab.approved.copy(), "previous", previous.name, publication=previous)
        if previous.trio_sha != _sha(previous.trio):
            raise PublishAnswer(
                422,
                f"“{previous.name}” can't be put back: its stored looks don't match their checksum.",
                problems=[
                    {"path": "overrides/show-looks.yaml → previous", "field": "trio", "message": "trio_sha"}
                ],
            )
        trio = _validated(lab, previous.trio, f"“{previous.name}”")
        return Candidate(trio, "previous", previous.name, publication=previous)
    try:
        version = lab.store.get(version_id)
    except S.NoSuchVersion:
        raise PublishAnswer(404, f"There is no saved version {version_id!r}.") from None
    try:
        trio = D.apply_drafts(lab.approved, version.drafts, canopy=lab.canopy, settings=lab.settings)
    except D.DraftError as exc:
        raise PublishAnswer(
            409,
            f"{version.name} no longer applies to the approved config ({len(exc.problems)} problem(s)).",
            stale=True,
            problems=[p.to_json() for p in exc.problems],
        ) from None
    trio = _validated(lab, trio.dump(), version.name)
    return Candidate(trio, "version", version.name, version=version)


def _validated(lab: LabService, raw: dict, what: str) -> ConfigTrio:
    looks = live_looks(lab.services)
    try:
        return validate_trio(raw, git=looks.git, settings=lab.settings, canopy=lab.canopy)
    except ConfigError as exc:
        problems = D.problems_from_config_error(str(exc))
        raise PublishAnswer(
            422,
            f"{what} can't be published: {len(problems)} problem(s).",
            problems=[p.to_json() for p in problems],
        ) from None


def _what(live: ConfigTrio, new: ConfigTrio, scene_id: str, canopy: dict) -> list[str]:
    a, b = _seed(live, scene_id), _seed(new, scene_id)
    if a is None or b is None:
        return ["look"]
    out = []
    if [c.model_dump() for c in a.palette] != [c.model_dump() for c in b.palette]:
        out.append("palette")
    if a.layout.model_dump() != b.layout.model_dump() or a.sample_at != b.sample_at:
        out.append("layout")
    for what in ("name", "mood", "description", "brightness", "solid"):
        if getattr(a, what) != getattr(b, what):
            out.append(what)
    if (a.stepped.model_dump() if a.stepped else None) != (b.stepped.model_dump() if b.stepped else None):
        out.append("stepped")
    return out or ["copy"]


def plan_publish(lab: LabService, candidate: Candidate) -> PublishPlan:
    """What installing `candidate` would do, against the live looks now (CPU: threadpool). Pure: writes nothing."""
    services = lab.services
    looks = live_looks(services)
    live, git, new = looks.trio, looks.git, candidate.trio
    exempt = candidate.kind in ("approved", "previous")  # Revert and Undo are never refused by the B1 lock
    changed = changed_scenes(live, new)
    vs_git = set(changed_scenes(git, new))
    to_approved = [s for s in changed if s not in vs_git]
    wiring = lab.show()
    repaint = wiring.repaint_plan(new) if wiring is not None else {}
    locked: dict[str, tuple[str, str]] = {}
    held: list[str] = []
    for scene_id in changed:
        mode = new.modes.modes.get(scene_id)
        if mode is None or mode.slot is None:
            continue
        before, after = _seed(live, scene_id), _seed(new, scene_id)
        frame = D.painted_change(before, after) if before is not None and after is not None else "layout"
        if frame is None:
            continue  # brightness, the solid, copy, timing, a colour's name: never locked
        api, app, _total = P.slot_records(lab, mode.slot)
        if app:
            locked[scene_id] = (P.app_lock_sentence(lab, scene_id, app), f"{scene_id}.{frame}")
        elif api and not exempt and not lab.simulated:  # B1 on hardware; the simulator holds, then rewrites
            locked[scene_id] = (P.api_lock_sentence(lab, scene_id, mode.slot, api), f"{scene_id}.{frame}")
        target = repaint.get(scene_id)
        if target is not None and target.listed:  # its commissioned slots won't hold the new design
            held.append(scene_id)
    engine = lab.engine()
    plan = engine.current_plan() if engine is not None else None
    trial = getattr(engine, "trial", None) if engine is not None else None
    driven = list(plan.modules) if plan is not None and (plan.mode_id in changed or trial is not None) else []
    showing = plan.mode_id if plan is not None else None
    show_offer = next((s for s in changed if s != showing), None) if showing not in changed else None
    show_modules: list[str] = []
    if show_offer is not None and wiring is not None:
        addresses = wiring.context().addresses or {}
        show_modules = [e.id for e in services.registry.canopy_modules() if addresses.get(e.id)]
    warnings = [
        f"{_name(new, p.mode)}: {p.message}"
        for p in scene_rules.scene_mode_problems(
            new.scenes, new.modes, new.recipes, lab.canopy, approved=git.modes, rules=scene_rules.WARNINGS
        )
        if p.mode in changed
    ]
    notes = []
    if engine is not None:
        if not getattr(engine, "guest_power", True):
            notes.append(CANOPY_OFF)
        else:
            level = getattr(engine, "_guest_brightness", None)
            if isinstance(level, int):
                notes.append(GUEST_DIM.format(level=level))
    changes = []
    for scene_id in changed:
        after = _seed(new, scene_id)
        needs_d59 = False
        if after is not None:
            try:
                needs_d59 = bool(D.draft_changes(git, after, canopy=lab.canopy).get("needs_d59"))
            except (KeyError, ValueError):
                needs_d59 = False
        changes.append(
            {
                "scene": scene_id,
                "name": _name(live, scene_id),
                "what": _what(live, new, scene_id, lab.canopy),
                "needs_d59": needs_d59 and scene_id not in to_approved,
                "to": "approved" if scene_id in to_approved else "published",
            }
        )
    return PublishPlan(
        changed=changed,
        to_approved=to_approved,
        repaint=repaint,
        locked=locked,
        held=held,
        driven=driven,
        generation=looks.generation,
        live_id=looks.live_id,
        ends_trial=trial is not None,
        equals_live=trio_sha(new) == trio_sha(live),
        warnings=warnings,
        notes=notes,
        changes=changes,
        show_offer=show_offer,
        show_modules=show_modules,
    )


# ---- what ops confirms --------------------------------------------------------------------------------------------------
def confirm_sentence(lab: LabService, plan: PublishPlan, *, name: str, kind: str) -> str:
    """The sentence ops confirms (409 `confirm_required` until `confirm` equals it): what is published, what it
    replaces, which scenes change (or go back to approved), the modules it reaches now, the holds, the try-on."""
    looks = live_looks(lab.services)
    live = looks.trio
    simulated = lab.simulated
    current = looks.file.current
    to_approved = [_name(live, s) for s in plan.to_approved]
    to_new = [_name(live, s) for s in plan.changed if s not in plan.to_approved]
    if kind == "approved":
        names = _and([_name(live, s) for s in plan.changed])
        verb = "changes" if len(plan.changed) == 1 else "change"
        where = "this simulator" if simulated else "the show"
        sentence = f"Revert {where} to the approved looks: {names} {verb} back."
    else:
        parts = []
        if to_new:
            parts.append(f"{_and(to_new)} {'changes' if len(to_new) == 1 else 'change'}")
        if to_approved:
            parts.append(f"{_and(to_approved)} {'goes' if len(to_approved) == 1 else 'go'} back to approved")
        what = ", ".join(parts)
        replacing = (
            f", replacing “{current.name}”" if current is not None and current.kind == "version" else ""
        )
        if kind == "previous":
            sentence = f"Go back to “{name}”{replacing}: {what}."
        elif simulated:
            sentence = f"Publish to this simulator “{name}”{replacing}: {what}."
        else:
            sentence = f"Publish “{name}” to the show{replacing}: {what}."
    canopy = "The simulated canopy" if simulated else "The canopy"
    if plan.driven:
        sentence += (
            f" {canopy} shows it now on {_count(len(plan.driven), 'module', 'modules')} "
            f"({_labels(lab, plan.driven)}) and after every restart."
        )
    else:
        sentence += (
            f" Nothing on the canopy changes now (no changed scene is showing); {'it shows' if len(plan.changed) == 1 else 'they show'} "
            "the next time it is chosen, and after every restart."
        )
    for scene_id in plan.held:
        sentence += HOLD_NOTE.format(scene=_name(live, scene_id))
    if plan.ends_trial:
        sentence += " The running try-on ends."
    return sentence


def cost_line(plan: PublishPlan, *, show: bool = False) -> str:
    """What the publish sends: one write per driven module now (+ one per module for "show <scene>"); B1 never
    repaints a slot itself (a held scene's slots are the reconciler's, later)."""
    n = len(plan.driven) + (len(plan.show_modules) if show and plan.show_offer else 0)
    now = f"sends {_count(n, 'write', 'writes')} now (one per module)" if n else "sends nothing now"
    later = ""
    if plan.held:
        slots = sorted({t.slot for s, t in plan.repaint.items() if s in plan.held and t is not None})
        later = (
            f"; later the reconciler rewrites {', '.join(slots) or 'the held slots'} (one slot write per module, at "
            "most once every 10 min)"
        )
    return f"{now}; no slot repaint{later}"


def confirm_body(lab: LabService, plan: PublishPlan, sentence: str, *, show: bool) -> dict:
    looks = live_looks(lab.services)
    current = looks.file.current
    return {
        "confirm_required": True,
        "confirm": sentence,
        "cost": cost_line(plan, show=show),
        "replaces": {"id": current.id, "name": current.name} if current is not None else None,
        "changes": plan.changes,
        "repaint": [],  # B2
        "ends_trial": plan.ends_trial,
        "show_offer": plan.show_offer,
        "notes": plan.notes,
        "warnings": plan.warnings,
    }


# ---- the answers ----------------------------------------------------------------------------------------------------------
def live_json(lab: LabService) -> dict:
    """LiveJSON (§2.11): what the show runs, where it came from, the Undo target, the six scenes as the iPad shows
    them, the history, and the canopy's power/brightness for the "the canopy is off" note."""
    services = lab.services
    looks = live_looks(services)
    engine = lab.engine()
    versions = {v.id for v in lab.store.file().versions}
    current = looks.file.current
    held = set(getattr(engine, "t1_hold", ()) or ()) if engine is not None else set()

    def exists(pub: Any) -> bool:
        return bool(pub.version) and pub.version in versions

    scenes = []
    for scene in looks.trio.scenes.scenes:
        scenes.append(
            {
                "id": scene.id,
                "name": scene.name,
                "source": scene_source(looks, scene.id),
                "palette": [{"name": c.name, "hex": c.hex} for c in scene.palette],
                "mood": scene.mood,
                "held": scene.id in held,
            }
        )
    canopy = {"power": True, "brightness": None, "designed_brightness": None}
    if engine is not None:
        try:
            st = engine.state()
            canopy = {
                "power": bool(engine.guest_power),
                "brightness": st.brightness,
                "designed_brightness": engine.designed_brightness(),
            }
        except Exception:
            log.exception("the canopy state for LiveJSON failed")
    previous = looks.file.previous
    return {
        "source": looks.source,
        "simulated": lab.simulated,
        "fallback": looks.problem is not None,
        "problem": looks.problem,
        "current": (
            {
                "id": current.id,
                "kind": current.kind,
                "version": current.version,
                "name": current.name,
                "published_at": current.published_at,
                "by": current.by,
                "changed": list(current.changed),
                "git_changed": bool(looks.git_changed),
                "version_exists": exists(current),
                "equals_approved": bool(looks.equals_approved),
            }
            if current is not None
            else None
        ),
        "previous": (
            {
                "id": previous.id,
                "name": previous.name,
                "published_at": previous.published_at,
                "by": previous.by,
            }
            if previous is not None
            else None
        ),
        "scenes": scenes,
        "history": [
            {**h.model_dump(mode="json"), "version_exists": exists(h)}
            for h in looks.file.history[:HISTORY_IN_ANSWER]
        ],
        "repaint": looks.repaint,  # B2 (null in B1)
        "canopy": canopy,
        "writable": looks.path is not None,
    }


def show_looks_brief(lab: LabService) -> dict:
    """The catalog's `show_looks`: {id, source, name}."""
    looks = live_looks(lab.services)
    return {"id": looks.live_id, "source": looks.source, "name": looks.name}


def show_looks_state(lab: LabService) -> dict:
    """StateJSON's `show_looks`: {id, source, generation, fallback, repainting}."""
    looks = live_looks(lab.services)
    return {
        "id": looks.live_id,
        "source": looks.source,
        "generation": looks.generation,
        "fallback": looks.problem is not None,
        "repainting": False,  # B2
    }


def live_version(lab: LabService) -> str | None:
    """The Lab version the show runs now (a DELETE of it is refused), else None."""
    current = live_looks(lab.services).file.current
    return current.version if current is not None and current.kind == "version" else None


def refusal_sentence(lab: LabService, code: str) -> str:
    engine = lab.engine()
    if code == "outage":
        outage = getattr(engine, "outage", None) or {}
        return REFUSALS["outage"].format(since=_clock(outage.get("since")))
    return REFUSALS.get(code, "The show can't take a publish right now.")


# ---- the new file -----------------------------------------------------------------------------------------------------------
def _new_file(
    looks: LiveLooks, candidate: Candidate, plan: PublishPlan, *, now: float, by: str, sha: str
) -> tuple[ShowLooksFile, Publication]:
    old = looks.file
    seq = old.seq + 1
    pub_id = f"p{seq:04d}"
    new = candidate.trio
    vs_git = changed_scenes(looks.git, new)
    if candidate.kind == "approved" or (
        candidate.kind == "previous"
        and (candidate.publication is None or candidate.publication.kind == "approved" or not vs_git)
    ):
        current = Publication(
            id=pub_id,
            kind="approved",
            version=None,
            name=APPROVED_NAME if candidate.kind == "approved" else candidate.name,
            published_at=now,
            by=by,
            base_config_sha=sha,
            changed=[],
        )
    else:
        source = candidate.publication
        current = Publication(
            id=pub_id,
            kind="version",
            version=candidate.version.id
            if candidate.version is not None
            else (source.version if source else None),
            name=candidate.name,
            published_at=now,
            by=by,
            base_config_sha=sha if candidate.kind == "version" or source is None else source.base_config_sha,
            trio_sha=trio_sha(new),
            changed=vs_git,
            drafts=(
                {sid: d.model_dump(mode="json") for sid, d in candidate.version.drafts.items()}
                if candidate.version is not None
                else dict(source.drafts if source is not None else {})
            ),
            trio=new.dump(),
        )
    replaced = "rolled back" if candidate.kind == "previous" else "replaced"
    history = [
        HistoryEntry(
            id=current.id,
            kind=current.kind,
            version=current.version,
            name=current.name,
            published_at=now,
            by=by,
            changed=list(plan.changed),
            repaint=None,
            outcome="live",
        ),
        *[
            h.model_copy(update={"outcome": replaced})
            if old.current is not None and h.id == old.current.id
            else h
            for h in old.history
        ],
    ][:MAX_HISTORY]
    new_file = old.model_copy(
        update={"seq": seq, "current": current, "previous": old.current, "history": history}
    )
    return new_file, current


# ---- publish ---------------------------------------------------------------------------------------------------------------
def _publish_lock(lab: LabService) -> asyncio.Lock:
    return lab.services.extras.setdefault(LOCK_KEY, asyncio.Lock())


async def publish(lab: LabService, body: PublishBody, *, by: str) -> dict:
    """The §2.8 order (see the module doc). Returns `{live, report, state}`; raises PublishAnswer."""
    services = lab.services
    engine = lab.engine()
    if engine is None or not callable(getattr(engine, "reload_looks", None)):
        raise PublishAnswer(503, NO_ENGINE)
    looks = live_looks(services)
    if looks.path is None:
        raise PublishAnswer(409, NO_DATA)
    lock = _publish_lock(lab)
    if lock.locked():
        _record(lab, "publish_refused", why="busy", version=body.version)
        raise PublishAnswer(409, BUSY)
    async with lock:
        return await _publish_locked(lab, engine, looks, body, by=by)


def _refuse(lab: LabService, status: int, why: str, version: str, detail: str, **extra: Any) -> PublishAnswer:
    _record(lab, "publish_refused", why=why, version=version)
    return PublishAnswer(status, detail, **extra)


async def _publish_locked(
    lab: LabService, engine: Any, looks: LiveLooks, body: PublishBody, *, by: str
) -> dict:
    services = lab.services

    def build() -> tuple[Candidate, PublishPlan, str]:
        candidate = published_trio(lab, body.version)
        plan = plan_publish(lab, candidate)
        sentence = confirm_sentence(lab, plan, name=candidate.name, kind=candidate.kind)
        return candidate, plan, sentence

    try:
        candidate, plan, sentence = await run_in_threadpool(build)
    except PublishAnswer as exc:
        _record(lab, "publish_refused", why=exc.body["detail"], version=body.version)
        raise
    # ---- one synchronous section on the loop thread: no await from here to the re-pick's install ----
    refusal = engine.looks_refusal()
    if refusal is not None:
        code, retry = refusal
        extra = {"retry_after_s": retry} if retry else {}
        raise _refuse(lab, 409, code, body.version, refusal_sentence(lab, code), **extra)
    if (
        body.expected_live != looks.live_id
        or plan.generation != looks.generation
        or plan.live_id != looks.live_id
    ):
        current = looks.file.current
        detail = CONFLICT.format(
            name=current.name if current is not None else APPROVED_NAME,
            time=_clock(current.published_at if current is not None else None),
            who=(current.by or "ops") if current is not None else "ops",
        )
        raise _refuse(lab, 409, "conflict", body.version, detail, live=live_json(lab), conflict=True)
    if plan.equals_live:
        raise _refuse(lab, 409, "same", body.version, SAME, nothing=True)
    if plan.locked:
        detail, field_ = next(iter(plan.locked.values()))
        raise _refuse(lab, 409, "slot lock", body.version, detail, field=field_)
    if body.confirm != sentence:
        _record(lab, "publish_refused", why="confirm", version=body.version)
        raise PublishAnswer(409, sentence, **confirm_body(lab, plan, sentence, show=body.show))
    now = services.clock.now()
    new_file, current = _new_file(looks, candidate, plan, now=now, by=by, sha=looks.git_sha)
    old_file, old_trio = looks.file, looks.trio
    old_state = (looks.source, looks.git_changed, looks.equals_approved)
    try:
        old_bytes = write_looks(looks, new_file, now=now)
    except OSError as exc:
        raise _refuse(
            lab,
            409,
            "read only",
            body.version,
            NOT_WRITTEN.format(reason=str(exc) or type(exc).__name__),
            read_only=True,
        ) from None
    why = f"“{candidate.name}”" if candidate.kind != "approved" else "the approved looks"
    try:
        copies = install_looks(services, candidate.trio, why=why)
        # a version equal to the approved looks is followed as approved (as a restart would)
        looks.source = "published" if current.kind == "version" and current.changed else "approved"
        looks.equals_approved = False
        looks.git_changed = current.kind == "version" and current.base_config_sha != looks.git_sha
        repicked = engine.current_plan() is not None and bool(plan.driven)
        st = await engine.reload_looks(changed=plan.changed, why=why)
    except Exception as exc:  # noqa: BLE001 - rolled back, alerted, a plain 500
        await _rollback(lab, engine, looks, old_bytes, old_file, old_trio, old_state, exc)
        _record(lab, "publish_failed", version=body.version, error=type(exc).__name__)
        raise PublishAnswer(500, FAILED.format(name=type(exc).__name__, live=_name_of(old_file))) from None
    sent = len(plan.driven) if repicked else 0
    if body.show and plan.show_offer is not None and not st.refused:
        shown = await engine.show_after_publish(plan.show_offer)
        if not shown.refused:
            sent += len(engine.current_plan().modules) if engine.current_plan() is not None else 0
    if looks.git_changed:
        from conductor.looks.live import GIT_KEY, git_changed_line

        services.telemetry.record("alert", level="warn", message=git_changed_line(looks), alert_key=GIT_KEY)
    held = sorted(set(plan.held) | (set(engine.t1_hold) & set(plan.changed)))
    report = {
        "id": current.id,
        "sent": sent,
        "repick": repicked,
        "ended_trial": plan.ends_trial,
        "copies": copies,
        "held": held,
        "repaint": [],  # B2
    }
    _record(
        lab,
        "publish",
        id=current.id,
        version=current.version,
        name=current.name,
        by=by,
        changed=list(plan.changed),
        repaint=[],
        sent=sent,
        what=candidate.kind,
    )
    state = engine.state()
    return {"live": live_json(lab), "report": report, "state": state.to_json()}


def _name_of(file: ShowLooksFile) -> str:
    return file.current.name if file.current is not None else APPROVED_NAME


async def _rollback(
    lab: LabService,
    engine: Any,
    looks: LiveLooks,
    old_bytes: bytes | None,
    old_file: ShowLooksFile,
    old_trio: ConfigTrio,
    old_state: tuple,
    exc: BaseException,
) -> None:
    services = lab.services
    log.error("The publish failed (%s: %s); rolling back", type(exc).__name__, exc)
    try:
        restore_file(looks, old_bytes, old_file)
    except OSError:
        log.exception("Couldn't put the previous overrides/show-looks.yaml back")
        looks.file = old_file
    try:
        install_looks(services, old_trio, why="a rollback after a failed publish")
    except Exception:
        log.exception("The rollback's install failed")
        looks.trio = old_trio
    looks.source, looks.git_changed, looks.equals_approved = old_state
    try:
        await engine.repick_after_rollback("a failed publish")
    except Exception:
        log.exception("The re-pick after a rolled-back publish failed")
    services.telemetry.record(
        "alert",
        level="warn",
        message=f"Published looks: a publish failed ({type(exc).__name__}: {exc}) and was rolled back; the show runs "
        f"“{_name_of(old_file)}” again.",
        alert_key="looks:publish",
    )


def who_of(body: PublishBody) -> str:
    who = (body.who or "").strip()
    return who[:WHO_MAX] if who else "ops"
