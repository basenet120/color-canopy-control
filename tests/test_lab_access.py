"""Who may use the Design Lab, and what it may never do (PLAN §4, §8 "Hard limits"; 07 §2).

- `/api/lab/*` is ops only: the auth middleware's path table keeps kiosk and tester devices out, and the
  router's own `require_ops` keeps them out even if the table ever let them through.
- Every write is same-site JSON (`routes/ops.py` `_no_cross_site`): 415 for anything but JSON, 403 cross-site.
- Saves, restores, deletes and exports are recorded in telemetry (`kind="lab"`).
- No Lab route changes `motion.stepped.live_enabled` or `kiosk.ripple_stagger_s`, and no Lab module imports a
  device writer.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from lab_edits import recoloured
from test_lab_store import FakeS1, install_fakes, seed

from conductor.config import CONFIG_DIR
from conductor.design_lab import export as X
from conductor.design_lab import preview as P
from conductor.design_lab.draft import SceneDraft, load_trio
from conductor.looks import scene_rules as R
from conductor.routes.deps import OPS_ONLY

ROOT = Path(__file__).resolve().parent.parent
OPS = ("crew", "canopy")
# live: the app's Lab reads config/, so the edit is made against it (a colour it doesn't already approve)
DRAFT = recoloured(seed(load_trio(CONFIG_DIR), "golden-hour").model_dump(mode="json"))
# Every Slice A route, as (method, the route's path, a concrete URL, a valid body).
LAB_REQUESTS = [
    ("GET", "/api/lab/catalog", "/api/lab/catalog", None),
    ("GET", "/api/lab/state", "/api/lab/state", None),
    ("POST", "/api/lab/preview", "/api/lab/preview", {"scene_id": "golden-hour", "draft": DRAFT}),
    ("GET", "/api/lab/versions", "/api/lab/versions", None),
    ("GET", "/api/lab/versions/{version_id}", "/api/lab/versions/v0001", None),
    ("POST", "/api/lab/versions", "/api/lab/versions", {"drafts": {"golden-hour": DRAFT}, "note": "x"}),
    ("DELETE", "/api/lab/versions/{version_id}", "/api/lab/versions/v0001", {}),
    ("POST", "/api/lab/restore", "/api/lab/restore", {"version": "approved"}),
    ("GET", "/api/lab/export", "/api/lab/export?version=v0001", None),
    # Slice B, the try-on (no engine under app_client / app_client_auth: 503 once past the guards)
    ("GET", "/api/lab/trial", "/api/lab/trial", None),
    ("POST", "/api/lab/trial/start", "/api/lab/trial/start", {"scene_id": "golden-hour", "draft": DRAFT}),
    ("POST", "/api/lab/trial/stop", "/api/lab/trial/stop", {}),
    # D68, the live show (no engine here either: 503 once past the guards)
    ("GET", "/api/lab/live", "/api/lab/live", None),
    ("POST", "/api/lab/publish", "/api/lab/publish", {"version": "approved", "expected_live": "approved"}),
]
WRITES = [r for r in LAB_REQUESTS if r[0] != "GET"]
# The Studio page the Lab lives in, and its assets: ops only too.
STUDIO_PAGES = [
    "/ops/showcase/",
    "/ops/showcase/lab.js",
    "/ops/showcase/lab.css",
    "/ops/showcase/lab-model.mjs",
]
ENROLLED = [("kiosk", []), ("tester", ["bench-kit"])]


@pytest.fixture
def fakes(monkeypatch) -> FakeS1:
    fake = install_fakes(monkeypatch)
    monkeypatch.setattr(P, "check_t1_lock", lambda lab, draft: None)
    monkeypatch.setattr(R, "scene_mode_problems", lambda *a, **kw: [])
    monkeypatch.setattr(
        X,
        "export_patch",
        lambda approved, patched, **kw: X.LabExport(
            patch="--- a/config/scenes.yaml\n+++ b/config/scenes.yaml\n",
            files=["config/scenes.yaml"],
            scenes=["golden-hour"],
            decision="D66",
            approved_changes=[],
            layout_changes=[],
            review_notes=[],
        ),
    )
    return fake


def send(client, method: str, url: str, payload: dict | None, **kw):
    return (
        client.request(method, url, json=payload, **kw)
        if payload is not None
        else client.request(method, url, **kw)
    )


def lab_events(client) -> list[dict]:
    return client.app.state.services.telemetry.recent(kind="lab")


def looks(client) -> Path:
    return Path(client.app.state.services.data) / "overrides" / "looks.yaml"


def enroll(client, role: str, grants: list[str]) -> None:
    client.cookies.clear()
    link = client.post(
        "/api/roles/enrollments", json={"role": role, "name": f"test {role}", "grants": grants}, auth=OPS
    ).json()
    assert client.post(link["path"], follow_redirects=False).status_code == 303


def test_the_request_list_covers_every_lab_route():
    from fastapi.routing import APIRoute

    from conductor.routes import design_lab

    routes = {(m, r.path) for r in design_lab.router.routes if isinstance(r, APIRoute) for m in r.methods}
    assert {(m, p) for m, p, _, _ in LAB_REQUESTS} == routes


# ---- ops only --------------------------------------------------------------------------------------------------------
def test_every_lab_route_is_ops_only(app_client_auth, fakes):
    c = app_client_auth
    for method, _, url, payload in LAB_REQUESTS:
        assert send(c, method, url, payload).status_code == 401, (method, url)  # nobody signed in
    for role, grants in ENROLLED:
        enroll(c, role, grants)
        for method, _, url, payload in LAB_REQUESTS:
            r = send(c, method, url, payload)
            assert r.status_code in (401, 403), (role, method, url, r.status_code)
        for page in STUDIO_PAGES:
            assert c.get(page).status_code in (401, 403), (role, page)
    c.cookies.clear()
    assert send(c, "GET", "/api/lab/state", None, auth=OPS).status_code == 200  # ops gets in
    assert not looks(c).exists() and lab_events(c) == []  # nothing a refused request did stuck


def test_every_lab_route_requires_ops_past_the_path_table(app_client_auth, fakes, monkeypatch):
    """The router's own guard, not only the auth middleware's path table: with the table opened to everyone, an
    enrolled kiosk or tester is still refused 403 by `require_ops` on every route."""
    from fastapi.routing import APIRoute

    from conductor.app import app
    from conductor.routes import design_lab
    from conductor.routes.deps import require_ops

    def calls(dependant) -> set:
        return {dependant.call} | set().union(*(calls(d) for d in dependant.dependencies))

    routes = [r for r in design_lab.router.routes if isinstance(r, APIRoute)]
    assert routes and all(require_ops in calls(r.dependant) for r in routes)
    mounted = {p for p in app.openapi()["paths"] if p.startswith("/api/lab")}
    assert mounted == {r.path for r in routes}

    c = app_client_auth
    monkeypatch.setattr("conductor.auth.path_allowed", lambda principal, method, path: True)
    for role, grants in ENROLLED:
        enroll(c, role, grants)
        for method, _, url, payload in LAB_REQUESTS:
            r = send(c, method, url, payload)
            assert r.status_code == 403, (role, method, url, r.status_code, r.text[:200])
            assert r.json() == {"detail": OPS_ONLY}
            assert r.headers["cache-control"] == "no-store"
    assert not looks(c).exists() and lab_events(c) == []


# ---- same-site JSON --------------------------------------------------------------------------------------------------
def test_cross_site_and_non_json_writes_are_refused(app_client, fakes):
    for method, _, url, payload in WRITES:
        r = app_client.request(method, url, content=b"x", headers={"Content-Type": "text/plain"})
        assert r.status_code == 415, (method, url, r.status_code)
        r = app_client.request(method, url)  # no body, no content type (a bare DELETE too)
        assert r.status_code == 415, (method, url, r.status_code)
        for header in ("Origin", "Referer"):
            r = app_client.request(method, url, json=payload, headers={header: "https://evil.example/lab"})
            assert r.status_code == 403, (method, url, header, r.status_code)
            assert r.headers["cache-control"] == "no-store"
    assert not looks(app_client).exists() and lab_events(app_client) == []
    # the same requests, same-site JSON, do get through
    assert (
        app_client.post(
            "/api/lab/versions", json=WRITES[1][3], headers={"Origin": "http://testserver"}
        ).status_code
        == 201
    )


# ---- telemetry -------------------------------------------------------------------------------------------------------
def test_save_export_and_restore_are_recorded_in_telemetry(app_client, fakes):
    assert app_client.post("/api/lab/versions", json={"drafts": {"golden-hour": DRAFT}}).status_code == 201
    assert app_client.post("/api/lab/restore", json={"version": "v0001"}).status_code == 200
    export = app_client.get("/api/lab/export?version=v0001")
    assert export.status_code == 200, export.text
    assert export.json()["filename"].startswith("design-lab-v0001-")
    assert app_client.request("DELETE", "/api/lab/versions/v0001", json={}).status_code == 200
    events = [{k: v for k, v in e.items() if k not in ("t", "kind")} for e in lab_events(app_client)]
    name = events[0]["name"]
    assert name.startswith("Client ")
    assert events == [
        {"action": "save", "version": "v0001", "name": name, "scenes": ["golden-hour"], "needs_d59": True},
        {"action": "restore", "version": "v0001"},
        {
            "action": "export",
            "version": "v0001",
            "files": ["config/scenes.yaml"],
            "needs_d59": True,
            "needs_slot_repaint": False,
        },
        {"action": "delete", "version": "v0001"},
    ]
    # refused writes record nothing
    assert app_client.request("DELETE", "/api/lab/versions/v0001", json={}).status_code == 404
    assert len(lab_events(app_client)) == 4


# ---- hard limits -----------------------------------------------------------------------------------------------------
def _names(model: type, seen: set | None = None) -> set[str]:
    """Every field name in a pydantic model, nested models included."""
    from pydantic import BaseModel

    seen = seen if seen is not None else set()
    names: set[str] = set()
    if model in seen:
        return names
    seen.add(model)
    for name, field in model.model_fields.items():
        names.add(name)
        for arg in getattr(field.annotation, "__args__", ()) or (field.annotation,):
            for inner in getattr(arg, "__args__", ()) or (arg,):
                if isinstance(inner, type) and issubclass(inner, BaseModel):
                    names |= _names(inner, seen)
    return names


def test_no_lab_route_changes_live_enabled_or_ripple(app_client, fakes):
    from conductor.routes.design_lab import PreviewBody, RestoreBody, SaveBody, TrialStartBody

    services = app_client.app.state.services
    settings = services.settings.model_dump()
    conductor_yaml = (CONFIG_DIR / "conductor.yaml").read_bytes()
    app_client.post("/api/lab/versions", json={"drafts": {"golden-hour": DRAFT}})
    for method, _, url, payload in LAB_REQUESTS:
        # 503: the try-on routes with no canopy engine (app_client runs without one)
        assert send(app_client, method, url, payload).status_code in {200, 201, 404, 409, 503}, (method, url)
    assert services.settings.model_dump() == settings
    assert services.settings.motion.stepped.live_enabled is True  # as shipped
    assert services.settings.kiosk.ripple_stagger_s == settings["kiosk"]["ripple_stagger_s"]
    assert (CONFIG_DIR / "conductor.yaml").read_bytes() == conductor_yaml
    for model in (SceneDraft, PreviewBody, SaveBody, RestoreBody, TrialStartBody):
        # nor hide a scene on the iPad (kiosk.hidden_scenes is /ops's, D76)
        assert not [n for n in _names(model) if "live" in n or "ripple" in n or "hidden" in n], model


WRITER_MODULES = ("conductor.executor", "conductor.drivers", "conductor.locks")
WRITER_CALLS = (".submit(", ".burst(", "driver.write(", "engine.send(")


def _lab_sources() -> list[Path]:
    return sorted((ROOT / "conductor" / "design_lab").glob("*.py")) + [
        ROOT / "conductor" / "routes" / "design_lab.py"
    ]


def test_no_lab_module_imports_a_writer():
    for path in _lab_sources():
        text = path.read_text()
        imported: set[str] = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                imported |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported |= {f"{node.module}.{a.name}" for a in node.names}
        bad = sorted(m for m in imported if m.startswith(WRITER_MODULES))
        assert bad == [], f"{path.name} imports a device writer: {bad}"
        assert [c for c in WRITER_CALLS if c in text] == [], path.name


# methods that write (or install a look) on any receiver: a local alias (`drv = ….engine_driver; drv.write(…)`)
# slips past a substring scan, never past this
WRITER_ATTRS = frozenset({"write", "write_rows", "submit", "burst", "send", "_show", "_install"})


def test_no_lab_module_calls_a_writer():
    """No Lab source calls a writing method on anything, whatever the receiver is called (the Lab's writes are the
    engine's: start_trial / end_trial), and none matches the writer-path scan of tests/test_writer_paths.py."""
    scan = re.compile(r"\.(submit|burst)\(|driver\.write\(|\.write\(\s*address")
    for path in _lab_sources():
        text = path.read_text()
        calls = [
            (n.func.attr, n.lineno)
            for n in ast.walk(ast.parse(text))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in WRITER_ATTRS
        ]
        assert calls == [], f"{path.name} calls a writer: {calls}"
        assert scan.search(text) is None, path.name


def test_the_writer_check_sees_the_lab_files():
    names = [p.name for p in _lab_sources()]
    assert {"store.py", "draft.py", "preview.py", "export.py", "service.py", "design_lab.py"} <= set(names)
