"""The Design Lab's router (conductor/routes/design_lab.py) is mounted, guarded and answers every Slice A route.

The contracts commit landed the routes before the streams filled the modules in, so a stub (`NotImplementedError`)
answers a readable 501 ("Not built yet: …") instead of a 500, and the Lab page degrades with a sentence. Slice A is
now built, so every route must give its built answer; the 501 path is pinned by a stub stood in on purpose.
"""

from __future__ import annotations

import pytest

from conductor.looks import load_modes
from conductor.scenes import load_scenes

SLICE_A = {
    ("GET", "/api/lab/catalog"),
    ("GET", "/api/lab/state"),
    ("POST", "/api/lab/preview"),
    ("GET", "/api/lab/versions"),
    ("GET", "/api/lab/versions/{version_id}"),
    ("POST", "/api/lab/versions"),
    ("DELETE", "/api/lab/versions/{version_id}"),
    ("POST", "/api/lab/restore"),
    ("GET", "/api/lab/export"),
}
SLICE_B = {("GET", "/api/lab/trial"), ("POST", "/api/lab/trial/start"), ("POST", "/api/lab/trial/stop")}  # B1


def draft_json(scene_id: str) -> dict:
    """The approved scene as a SceneDraft body, built by hand from the config (independent of seed_draft)."""
    scene, mode = load_scenes().get(scene_id), load_modes().mode(scene_id)
    sample = mode.techniques.modules.sample
    stepped = mode.techniques.stepped
    return {
        "scene_id": scene.id,
        "name": scene.name,
        "mood": scene.mood,
        "description": scene.description,
        "palette": [{"name": c.name, "hex": c.hex} for c in scene.palette],
        "layout": sample.canvas.field.model_dump(mode="json"),
        "sample_at": sample.at,
        "brightness": scene.brightness_draft,
        "solid": scene.default_solid,
        "stepped": (
            {
                "direction": stepped.direction,
                "steps_per_cycle": stepped.steps_per_cycle,
                "step_s": stepped.step_s,
                "run_s": stepped.run_s,
            }
            if stepped is not None
            else None
        ),
    }


def _lab_routes():
    from fastapi.routing import APIRoute

    from conductor.routes import design_lab

    return [r for r in design_lab.router.routes if isinstance(r, APIRoute)]


def _mounted() -> set[tuple[str, str]]:
    """What the app serves under /api/lab, from its OpenAPI paths (routers are nested in app.routes)."""
    from conductor.app import app

    paths = app.openapi()["paths"]
    return {(m.upper(), p) for p, ops in paths.items() if p.startswith("/api/lab") for m in ops}


def test_the_router_is_mounted_with_every_slice_a_route_behind_ops_and_the_same_site_guard():
    from conductor.routes.deps import require_ops
    from conductor.routes.design_lab import LabRoute
    from conductor.routes.ops import _no_cross_site

    def calls(dependant) -> list:
        out = [dependant.call]
        for d in dependant.dependencies:
            out += calls(d)
        return out

    routes = _lab_routes()
    assert {(m, r.path) for r in routes for m in r.methods} == SLICE_A | SLICE_B
    assert _mounted() == SLICE_A | SLICE_B
    for route in routes:
        assert isinstance(route, LabRoute)
        deps = [c for c in calls(route.dependant) if c is not None]
        assert require_ops in deps and _no_cross_site in deps, route.path
        assert deps.index(require_ops) < deps.index(_no_cross_site), "require_ops is checked first"


def _requests():
    draft = draft_json("golden-hour")
    return [
        ("GET", "/api/lab/catalog", None, {200}),
        ("GET", "/api/lab/state", None, {200}),
        ("POST", "/api/lab/preview", {"scene_id": "golden-hour", "draft": draft}, {200}),
        ("GET", "/api/lab/versions", None, {200}),
        ("GET", "/api/lab/versions/v0001", None, {404}),
        (
            "POST",
            "/api/lab/versions",
            {"drafts": {"golden-hour": draft}, "note": "warmer sunset"},
            {201},
        ),
        ("GET", "/api/lab/versions/v0001", None, {200}),
        ("POST", "/api/lab/restore", {"version": "approved"}, {200}),
        ("GET", "/api/lab/export?version=v0001", None, {200}),
        ("DELETE", "/api/lab/versions/v0001", {}, {200}),
        ("GET", "/api/lab/export?version=v0001", None, {404}),
    ]


def test_every_slice_a_route_answers(app_client):
    for method, path, body, allowed in _requests():
        r = (
            app_client.request(method, path, json=body)
            if body is not None
            else app_client.request(method, path)
        )
        assert r.status_code in allowed, (method, path, r.status_code, r.text[:300])
        assert r.headers["cache-control"] == "no-store", (method, path)


def test_the_state_says_the_store_is_fine_and_nothing_is_on_trial(app_client):
    r = app_client.get("/api/lab/state")
    assert r.status_code == 200
    body = r.json()
    assert body["store"] == {
        "ok": True,
        "error": None,
        "set_aside": None,
        "read_only": False,
        "versions": 0,
        "unreadable": 0,
    }
    assert body["current"] is None and body["trial"] is None
    assert body["engine"]["running"] is False  # app_client runs without the canopy engine
    assert body["engine"]["simulated"] is True


def test_a_stub_is_a_readable_501_not_a_500(app_client, monkeypatch):
    from conductor.design_lab import preview as P

    def stub(lab, draft):
        raise NotImplementedError("S1: preview")

    monkeypatch.setattr(P, "preview", stub)
    r = app_client.post(
        "/api/lab/preview", json={"scene_id": "golden-hour", "draft": draft_json("golden-hour")}
    )
    assert r.status_code == 501
    assert r.json() == {"detail": "Not built yet: S1: preview.", "not_built": True}


def test_preview_of_an_unknown_scene_is_a_404_and_a_mismatched_draft_a_422(app_client):
    draft = draft_json("golden-hour")
    r = app_client.post(
        "/api/lab/preview", json={"scene_id": "disco-night", "draft": {**draft, "scene_id": "disco-night"}}
    )
    assert r.status_code == 404 and "disco-night" in r.json()["detail"]
    r = app_client.post("/api/lab/preview", json={"scene_id": "coastal-tides", "draft": draft})
    assert r.status_code == 422
    (problem,) = r.json()["problems"]
    assert problem["field"] == "scene_id"


@pytest.mark.parametrize(
    "edit, field",
    [
        (lambda d: d["palette"][0].update(hex="#12345"), "palette.0.hex"),
        (lambda d: d["palette"][1].update(name="Moving gold"), "palette.1.name"),
        (lambda d: d["palette"][1].update(name="Nine"), "palette.1.name"),
        (lambda d: d.update(brightness=0), "brightness"),
        (lambda d: d.update(solid=9), "solid"),
        (lambda d: d.update(mood="A glow that moves"), "mood"),
        (lambda d: d.update(palette=d["palette"][:1]), "palette"),
    ],
)
def test_a_body_pydantic_refuses_is_a_problems_422_by_draft_field(app_client, edit, field):
    draft = draft_json("golden-hour")
    edit(draft)
    r = app_client.post("/api/lab/preview", json={"scene_id": "golden-hour", "draft": draft})
    assert r.status_code == 422
    body = r.json()
    assert body["detail"].endswith("problem(s).")
    assert field in [p["field"] for p in body["problems"]], body
    assert all(set(p) == {"path", "field", "message"} for p in body["problems"])


def test_a_save_body_names_the_field_past_the_scene_id(app_client):
    draft = draft_json("golden-hour")
    draft["palette"][2]["hex"] = "pink"
    r = app_client.post("/api/lab/versions", json={"drafts": {"golden-hour": draft}})
    assert r.status_code == 422
    assert [p["field"] for p in r.json()["problems"]] == ["palette.2.hex"]


def test_writes_must_be_same_site_json(app_client):
    body = {"scene_id": "golden-hour", "draft": draft_json("golden-hour")}
    r = app_client.post("/api/lab/preview", content="x", headers={"Content-Type": "text/plain"})
    assert r.status_code == 415
    r = app_client.post("/api/lab/preview", json=body, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = app_client.request("DELETE", "/api/lab/versions/v0001")  # no JSON: refused like any other write
    assert r.status_code == 415
    assert r.headers["cache-control"] == "no-store"


def test_the_studio_and_ops_link_to_the_lab(app_client):
    studio = app_client.get("/ops/showcase/")
    assert studio.status_code == 200
    html = studio.text
    assert '<link rel="stylesheet" href="./lab.css?v=lab-2">' in html
    assert '<a class="text-link" href="#lab" id="lab-link">Design Lab</a>' in html
    assert html.index('id="lab-link"') < html.index('id="guest-panel-link"')
    assert '<section id="lab" class="lab" aria-label="Design Lab" hidden></section>' in html
    assert html.index('id="lab" class="lab"') < html.index("</main>")
    assert html.index('src="./studio.js?v=show-design-3"') < html.index('src="./lab.js?v=lab-2"')
    for asset in ("lab.js", "lab.css", "lab-model.mjs"):
        assert app_client.get(f"/ops/showcase/{asset}").status_code == 200, asset
    ops = app_client.get("/ops/").text
    assert '<a class="btn secondary" href="/ops/showcase/#lab">Design Lab</a>' in ops


def test_a_layout_error_names_the_draft_field_not_the_union_tag():
    from conductor.routes.design_lab import _validation_problems

    errors = [
        {"loc": ("body", "draft", "layout", "linear", "angle_deg"), "msg": "Input should be a valid number"}
    ]
    assert _validation_problems(errors)[0]["field"] == "layout.angle_deg"


def test_a_long_note_never_makes_a_name_the_store_refuses():
    from datetime import UTC, datetime

    from conductor.design_lab.store import default_name

    now = datetime(2026, 10, 16, 16, 0, tzinfo=UTC).timestamp()  # Oct 16 in any US time zone
    name = default_name("n" * 60, now, [f"Client Oct 16 v{n}" for n in range(1, 100)])
    assert "v100" in name
    assert len(name) <= 80
