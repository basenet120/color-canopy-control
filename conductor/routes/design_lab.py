"""/api/lab/* — the Design Lab's API (ops only; docs/design-lab/PLAN.md §4). Slice A is screen only: no route here
writes to a device, starts a look, flips `live_enabled` or the ripple, or writes into `config/`.

- `GET    /api/lab/catalog`                  the six scenes as the Lab starts from them (approved drafts, designs,
                                             copy, state vs the current version, the T1 lock), limits, live flags
- `GET    /api/lab/state`                    the store (ok / error / set aside / read-only), the current version, the
                                             engine's flags, the try-on (null in Slice A)
- `POST   /api/lab/preview`                  `{scene_id, draft}` → the draft through the real compiler, every tier,
                                             today; 404 unknown scene, 409 T1 lock, 422 problems
- `GET    /api/lab/versions`                 `{versions: [VersionSummary], current}`
- `GET    /api/lab/versions/{id}`            VersionJSON; 404
- `POST   /api/lab/versions`                 `{drafts, note?, name?, parent?}` → 201 VersionJSON; 409 T1 lock or a
                                             read-only store; 422 problems
- `DELETE /api/lab/versions/{id}`            `{}` → `{deleted, current}`; 404
- `POST   /api/lab/restore`                  `{version: id | "approved"}` → `{current, drafts}` (all six, seeded where
                                             not drafted); 404; 409 stale (with problems)
- `GET    /api/lab/export?version=&format=`  json: ExportJSON; patch: a `text/x-diff` attachment; 404; 409 stale, a
                                             failed re-read check, or a D63 warning standing

Every route depends on `require_ops` first, then the /ops same-site + JSON-only guard (`routes/ops.py`
`_no_cross_site`; DELETE too, so the client sends `{}`). Every answer is `Cache-Control: no-store`. CPU work
(catalog, preview, save, export, restore's stale check) runs in the threadpool so the engine's loop never stalls;
telemetry (`kind="lab"`) is recorded back on the loop thread.

Errors are FastAPI's `{"detail": …}`, extended: a 422 is `{detail, problems: [{path, field, message}]}` whether
pydantic refused the body or the draft didn't apply, so the Lab marks fields one way. A part of the Lab whose stream
hasn't landed raises NotImplementedError, which answers 501 `{detail: "Not built yet: …", not_built: true}` so the
page degrades readably.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from conductor.design_lab import draft as D
from conductor.design_lab import export as X
from conductor.design_lab import preview as P
from conductor.design_lab import store as S
from conductor.design_lab.service import KEY, LabService, lab_of
from conductor.looks import CompileContext
from conductor.looks import scene_rules as R
from conductor.routes.deps import require_ops, services_of
from conductor.routes.design import mode_design
from conductor.routes.ops import _no_cross_site, engine_flag
from conductor.show import STEPPED_LIVE_OFF

log = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store"}
CONFIG_SHA_HEADER = (
    "X-Lab-Config-Sha"  # on every answer: an open page sees the Conductor restart on a new config
)
TELEMETRY_KIND = "lab"
SCENE_ID = r"^[a-z][a-z0-9-]{1,40}$"
FIXTURE_TEST = "tests/test_modes_match_scenes.py"


# ---- answers -------------------------------------------------------------------------------------------------------
class LabAnswer(Exception):
    """An error answer with a body beyond `detail` (problems, warnings, field, …)."""

    def __init__(self, status_code: int, detail: str, **extra: Any) -> None:
        self.status_code = status_code
        self.body = {"detail": detail, **extra}
        super().__init__(detail)


def _validation_problems(errors: list[dict]) -> list[dict]:
    """pydantic's body errors as Lab problems: `field` is the SceneDraft path (`palette.2.hex`), past the body,
    `draft` and the scene id of `drafts.<scene>`."""
    out = []
    for e in errors:
        loc = [str(p) for p in e.get("loc", ())]
        rest = loc[1:] if loc[:1] == ["body"] else loc
        if rest[:1] == ["draft"]:
            rest = rest[1:]
        elif rest[:1] == ["drafts"] and len(rest) >= 2:
            rest = rest[2:]
        if rest[:1] == ["layout"] and len(rest) >= 2 and rest[1] in ("linear", "radial", "sweep"):
            rest = [rest[0], *rest[2:]]  # the union's `kind` tag isn't a SceneDraft field
        field = ".".join(rest) or "draft"
        message = str(e.get("msg", "invalid")).removeprefix("Value error, ")
        out.append({"path": " → ".join(loc) or "(body)", "field": field, "message": message})
    return out


def _problems(problems: list[dict], what: str = "The draft") -> LabAnswer:
    return LabAnswer(422, f"{what} has {len(problems)} problem(s).", problems=problems)


class LabRoute(APIRoute):
    """Every Lab answer is no-store; a body pydantic refuses is a problems 422; a part of the Lab that isn't built yet
    (NotImplementedError from a stream's stub) is a readable 501. Router-scoped (FastAPI exception handlers are
    app-wide)."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def lab_handler(request: Request) -> Response:
            try:
                response = await handler(request)
            except LabAnswer as exc:
                response = JSONResponse(exc.body, status_code=exc.status_code)
            except HTTPException as exc:
                response = JSONResponse(
                    {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
                )
            except RequestValidationError as exc:
                body = _problems(_validation_problems(list(exc.errors())), "The request").body
                response = JSONResponse(body, status_code=422)
            except NotImplementedError as exc:
                what = str(exc) or "this part of the Design Lab"
                response = JSONResponse(
                    {"detail": f"Not built yet: {what}.", "not_built": True}, status_code=501
                )
            response.headers["Cache-Control"] = "no-store"
            sha = _config_sha_of(request)
            if sha:
                response.headers[CONFIG_SHA_HEADER] = sha
            return response

        return lab_handler


def _config_sha_of(request: Request) -> str | None:
    """The approved config the Lab loaded (once it is built), for CONFIG_SHA_HEADER."""
    try:
        lab = services_of(request).extras.get(KEY)
    except Exception:  # noqa: BLE001 - a header never fails an answer
        return None
    return getattr(lab, "config_sha", None)


router = APIRouter(
    prefix="/api/lab",
    dependencies=[Depends(require_ops), Depends(_no_cross_site)],
    route_class=LabRoute,
)
Lab = Annotated[LabService, Depends(lab_of)]


# ---- bodies --------------------------------------------------------------------------------------------------------
class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreviewBody(_Body):
    scene_id: str = Field(pattern=SCENE_ID)
    draft: D.SceneDraft


class SaveBody(_Body):
    drafts: dict[str, D.SceneDraft] = Field(default_factory=dict)
    note: str = Field("", max_length=60)
    name: str | None = Field(None, min_length=1, max_length=80)
    parent: str | None = Field(None, max_length=40)


class RestoreBody(_Body):
    version: str = Field(min_length=1, max_length=40)  # a version id or "approved"


class EmptyBody(_Body):
    pass


# ---- helpers -------------------------------------------------------------------------------------------------------
def _scene_ids(lab: LabService) -> list[str]:
    return [s.id for s in lab.approved.scenes.scenes]


def _known_scene(lab: LabService, scene_id: str) -> None:
    if scene_id not in _scene_ids(lab):
        raise LabAnswer(404, f"There is no scene called {scene_id!r}.")


def _draft_matches(scene_id: str, draft: D.SceneDraft, where: str) -> None:
    if draft.scene_id != scene_id:
        raise _problems(
            [
                {
                    "path": where,
                    "field": "scene_id",
                    "message": f"the draft is for {draft.scene_id!r}, not {scene_id!r}",
                }
            ]
        )


def _draft_error(exc: D.DraftError) -> LabAnswer:
    return _problems([p.to_json() for p in exc.problems])


def _no_version(version_id: str) -> LabAnswer:
    return LabAnswer(404, f"There is no saved version {version_id!r}.")


def _read_only(exc: Exception) -> LabAnswer:
    return LabAnswer(409, str(exc), read_only=True)


def _writable(lab: LabService) -> None:
    if lab.store.read_only:
        reason = lab.store.error or "the file couldn't be read or moved"
        raise _read_only(S.StoreReadOnly(S.READ_ONLY.format(reason=reason)))


def _store_ctx(lab: LabService) -> dict[str, Any]:
    return {
        "approved": lab.approved,
        "canopy": lab.canopy,
        "settings": lab.settings,
        "config_sha": lab.config_sha,
    }


def _record(lab: LabService, action: str, **fields: Any) -> None:
    try:
        lab.services.telemetry.record(TELEMETRY_KIND, action=action, **fields)
    except Exception:  # telemetry never fails an answer
        log.exception("lab telemetry failed")


def _stale(version: S.Version, exc: D.DraftError) -> LabAnswer:
    return LabAnswer(
        409,
        f"{version.name} no longer applies to the approved config ({len(exc.problems)} problem(s)).",
        stale=True,
        problems=[p.to_json() for p in exc.problems],
    )


def _copy(mode: Any) -> dict[str, str]:
    """Each rung's kiosk sentence as the approved mode has it (the stepped rung's is its moving sentence)."""
    t = mode.techniques
    copy = {
        "canvas": t.canvas.feature if t.canvas is not None else None,
        "slot": t.slot.feature if t.slot is not None else None,
        "modules": t.modules.feature if t.modules is not None else None,
        "stepped": t.stepped.moving_feature if t.stepped is not None else None,
        "strand": t.strand.feature if t.strand is not None else None,
    }
    return {k: v for k, v in copy.items() if v is not None}


def catalog_json(lab: LabService) -> dict:
    """CatalogJSON (§4). CPU work: call it in the threadpool."""
    settings = lab.settings
    approved = lab.approved
    ctx = CompileContext(
        registry=[], capabilities={}, modes=approved.modes, recipes=approved.recipes, canopy=lab.canopy
    )
    current = lab.store.file().current
    try:
        drafted = lab.store.get(current).drafts if current else {}
    except S.NoSuchVersion:
        drafted = {}
    idle = {settings.kiosk.idle_mode, settings.kiosk.idle_scene}
    scenes = []
    for scene in approved.scenes.scenes:
        mode = approved.modes.mode(scene.id)
        draft = drafted.get(scene.id)
        seed = D.seed_draft(approved, scene.id)
        # compared with this scene's own seed, never seeded from draft.scene_id: a hand-edited version whose draft
        # names another (or no) scene shows as a draft (the versions list marks it stale) instead of failing the Lab
        state = "draft" if draft is not None and draft.model_dump() != seed.model_dump() else "approved"
        scenes.append(
            {
                "id": scene.id,
                "name": scene.name,
                "mood": scene.mood,
                "description": scene.description,
                "ladder": [str(t) for t in mode.ladder],
                "slot": mode.slot,
                "painted": mode.slot is not None,
                "stepped": "stepped" in mode.ladder,
                "idle": scene.id in idle,
                "approved": seed.model_dump(mode="json"),
                "design": mode_design(scene.id, ctx),
                "copy": _copy(mode),
                "state": state,
                "t1_lock": P.t1_lock(lab, scene.id),
            }
        )
    driver = lab.services.engine_driver
    return {
        "simulated": bool(driver.simulated),
        "driver": str(getattr(driver, "name", "sim")),
        "config_sha": lab.config_sha,
        "limits": {
            "palette": [D.PALETTE_MIN, D.PALETTE_MAX],
            "brightness": [1, 100],
            "name": 40,
            "mood": 60,
            "description": 200,
            "stepped": {
                "steps_per_cycle": [3, 12],
                "step_s": [4, 12],
                "run_s_max": min(180.0, float(settings.kiosk.idle_return_s)),
            },
            "canopy": {"width_in": lab.canopy["width_in"], "height_in": lab.canopy["height_in"]},
        },
        "live": {
            "stepped_live_enabled": bool(settings.motion.stepped.live_enabled),
            "stepped_live_sentence": STEPPED_LIVE_OFF,
            "ripple_stagger_s": settings.kiosk.ripple_stagger_s,
            "effects_enabled": any(bool(codes) for codes in settings.effects.enabled.values()),
        },
        "scenes": scenes,
    }


def _engine_json(lab: LabService) -> dict:
    engine = lab.engine()
    if engine is None:
        return {
            "running": False,
            "simulated": lab.simulated,
            "commissioning": False,
            "paused": False,
            "outage": False,
        }
    return {
        "running": not bool(engine_flag(engine, "stopped")),
        "simulated": lab.simulated,
        "commissioning": bool(engine_flag(engine, "commissioning")),
        "paused": bool(engine_flag(engine, "paused")),
        "outage": bool(engine_flag(engine, "outage")),
    }


def state_json(lab: LabService) -> dict:
    """StateJSON (§4)."""
    store = lab.store
    looks = store.file()
    return {
        "store": {
            "ok": store.error is None and not store.read_only,
            "error": store.error,
            "set_aside": store.set_aside,
            "read_only": store.read_only,
            "versions": len(looks.versions),
            "unreadable": len(store.unreadable()),
        },
        "current": looks.current,
        "engine": _engine_json(lab),
        "trial": None,  # Slice B
    }


def _warning_sentence(problem: R.SceneProblem, patched: D.ConfigTrio) -> str:
    try:
        name = patched.scenes.get(problem.mode).name
    except KeyError:
        name = problem.mode
    test = R.WARNING_TESTS.get(problem.rule, "")
    where = f" — {FIXTURE_TEST}::{test} would fail" if test else ""
    return f"{name}: {problem.message}{where}"


def _export_filename(version_id: str, now: float) -> str:
    return f"design-lab-{version_id}-{datetime.fromtimestamp(now, UTC):%Y%m%dT%H%M%SZ}.patch"


# ---- routes --------------------------------------------------------------------------------------------------------
@router.get("/catalog")
async def catalog(lab: Lab) -> JSONResponse:
    return JSONResponse(await run_in_threadpool(catalog_json, lab))


@router.get("/state")
async def state(lab: Lab) -> JSONResponse:
    return JSONResponse(state_json(lab))


@router.post("/preview")
async def preview(body: PreviewBody, lab: Lab) -> JSONResponse:
    _known_scene(lab, body.scene_id)
    _draft_matches(body.scene_id, body.draft, "draft → scene_id")
    try:
        result = await run_in_threadpool(P.preview, lab, body.draft)
    except D.DraftError as exc:
        raise _draft_error(exc) from None
    except P.T1Locked as exc:
        raise LabAnswer(409, exc.detail, field=exc.field) from None
    return JSONResponse(result)


@router.get("/versions")
async def versions(lab: Lab) -> JSONResponse:
    items = await run_in_threadpool(lambda: lab.store.version_summaries(**_store_ctx(lab)))
    return JSONResponse({"versions": items, "current": lab.store.file().current})


@router.get("/versions/{version_id}")
async def version(version_id: str, lab: Lab) -> JSONResponse:
    try:
        body = await run_in_threadpool(lambda: lab.store.version_json(version_id, **_store_ctx(lab)))
    except S.NoSuchVersion:
        raise _no_version(version_id) from None
    return JSONResponse(body)


@router.post("/versions", status_code=201)
async def save(body: SaveBody, lab: Lab) -> JSONResponse:
    _writable(lab)
    for scene_id, draft in body.drafts.items():
        _known_scene(lab, scene_id)
        _draft_matches(scene_id, draft, f"drafts → {scene_id} → scene_id")
    if body.parent is not None:
        try:
            lab.store.get(body.parent)
        except S.NoSuchVersion:
            raise _no_version(body.parent) from None

    def work() -> dict:
        for draft in body.drafts.values():
            P.check_t1_lock(lab, draft)
        saved = lab.store.save_version(
            drafts=body.drafts,
            note=body.note,
            name=body.name,
            parent=body.parent,
            **_store_ctx(lab),
        )
        # the answer is built from what was saved: a delete landing right after can't turn a save into an error
        return lab.store.version_answer(saved, **_store_ctx(lab))

    try:
        answer = await run_in_threadpool(work)
    except S.NoSuchVersion as exc:  # the parent was deleted after the check above (another tab)
        raise _no_version(str(exc.args[0])) from None
    except D.DraftError as exc:
        raise _draft_error(exc) from None
    except P.T1Locked as exc:
        raise LabAnswer(409, exc.detail, field=exc.field) from None
    except S.StoreReadOnly as exc:
        raise _read_only(exc) from None
    _record(
        lab,
        "save",
        version=answer.get("id"),
        name=answer.get("name"),
        scenes=list(answer.get("scenes") or body.drafts),
        needs_d59=bool(answer.get("needs_d59")),
    )
    return JSONResponse(answer, status_code=201)


@router.delete("/versions/{version_id}")
async def delete(version_id: str, lab: Lab, _body: EmptyBody | None = None) -> JSONResponse:
    _writable(lab)
    try:
        await run_in_threadpool(lab.store.delete_version, version_id)
    except S.NoSuchVersion:
        raise _no_version(version_id) from None
    except S.StoreReadOnly as exc:
        raise _read_only(exc) from None
    _record(lab, "delete", version=version_id)
    return JSONResponse({"deleted": version_id, "current": lab.store.file().current})


@router.post("/restore")
async def restore(body: RestoreBody, lab: Lab) -> JSONResponse:
    _writable(lab)
    approved = lab.approved
    saved = None
    if body.version != "approved":
        try:
            saved = lab.store.get(body.version)
        except S.NoSuchVersion:
            raise _no_version(body.version) from None

    def work() -> dict:
        drafts: dict[str, D.SceneDraft] = {}
        current: str | None = None
        if saved is not None:
            try:
                D.apply_drafts(approved, saved.drafts, canopy=lab.canopy, settings=lab.settings)
            except D.DraftError as exc:
                raise _stale(saved, exc) from None
            drafts, current = dict(saved.drafts), saved.id
        lab.store.set_current(current)
        return {
            "current": current,
            "drafts": {
                sid: (drafts.get(sid) or D.seed_draft(approved, sid)).model_dump(mode="json")
                for sid in _scene_ids(lab)
            },
        }

    try:
        answer = await run_in_threadpool(work)
    except S.NoSuchVersion:  # deleted after the check above (another tab)
        raise _no_version(body.version) from None
    except S.StoreReadOnly as exc:
        raise _read_only(exc) from None
    _record(lab, "restore", version=body.version)
    return JSONResponse(answer)


@router.get("/export")
async def export(
    lab: Lab,
    version: Annotated[str, Query(min_length=1, max_length=40)],
    format: Literal["json", "patch"] = "json",
) -> Response:
    try:
        saved = lab.store.get(version)
    except S.NoSuchVersion:
        raise _no_version(version) from None
    approved, canopy = lab.approved, lab.canopy
    now = lab.services.clock.now()
    root = Path(lab.services.config_dir).parent

    def work() -> tuple[X.LabExport, list[dict]]:
        try:
            patched = D.apply_drafts(approved, saved.drafts, canopy=canopy, settings=lab.settings)
        except D.DraftError as exc:
            raise _stale(saved, exc) from None
        warnings = R.scene_mode_problems(
            patched.scenes, patched.modes, patched.recipes, canopy, approved=approved.modes, rules=R.WARNINGS
        )
        if warnings:
            raise LabAnswer(
                409,
                "; ".join(_warning_sentence(w, patched) for w in warnings),
                warnings=[w.to_json() for w in warnings],
            )
        result = X.export_patch(approved, patched, root=root, canopy=canopy, version=saved, now=now)
        changes = [D.draft_changes(approved, d, canopy=canopy) for d in saved.drafts.values()]
        return result, changes

    result, changes = await run_in_threadpool(work)
    if result.problem:
        raise LabAnswer(
            409,
            f"No patch is offered: the edited files didn't check out ({result.problem}).",
            problem=result.problem,
        )
    _record(
        lab,
        "export",
        version=saved.id,
        files=list(result.files),
        needs_d59=any(bool(c.get("needs_d59")) for c in changes),
        needs_slot_repaint=any(bool(c.get("needs_slot_repaint")) for c in changes),
    )
    filename = _export_filename(saved.id, now)
    if format == "patch":
        return Response(
            result.patch,
            media_type="text/x-diff",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return JSONResponse({**asdict(result), "filename": filename})
