"""POST /api/lab/preview and GET /api/lab/catalog through the real app (conductor/routes/design_lab.py over S1's
draft and preview): every scene previews, the Done-means edit answers in time with the draft's colours and its
changes, refusals have the Lab's shapes (422 problems by draft field, 409 T1 lock), the service is built once and no
YAML is parsed on the engine's event loop."""

from __future__ import annotations

import asyncio

import pytest

from conductor.config import CONFIG_DIR
from conductor.design_lab import draft as D
from conductor.design_lab import preview as P
from conductor.design_lab import service as SVC

TRIO = D.load_trio(CONFIG_DIR)
IDS = [s.id for s in TRIO.scenes.scenes]
DONE_MEANS = {"palette.2.hex": "#FF8FBF", "layout": {"kind": "linear", "angle_deg": 30}, "brightness": 90}


def draft_json(scene_id: str, **changes) -> dict:
    data = D.seed_draft(TRIO, scene_id).model_dump(mode="json")
    for key, value in changes.items():
        parts = key.split(".")
        target = data
        for p in parts[:-1]:
            target = target[int(p)] if p.isdigit() else target[p]
        target[parts[-1]] = value
    return data


def post(client, scene_id: str, draft: dict):
    return client.post("/api/lab/preview", json={"scene_id": scene_id, "draft": draft})


@pytest.mark.parametrize("scene_id", IDS)
def test_every_approved_scene_previews_as_approved(app_client, scene_id):
    r = post(app_client, scene_id, draft_json(scene_id))
    assert r.status_code == 200, r.text[:400]
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["approved"] is True and body["warnings"] == []
    assert len(body["design"]["heads"]) == 270 and len(body["design"]["modules"]) == 9
    assert [t["tier"] for t in body["tiers"]] == list(TRIO.modes.mode(scene_id).ladder)
    assert body["copy"]["modules"]["chosen"] == "approved"
    assert body["changes"]["copy"] == [] and not body["changes"]["needs_d59"]
    assert body["today"]["note"] == P.NO_ENGINE  # app_client runs without the canopy engine
    assert body["show_box_today"]["tier"] == "modules"


def test_the_done_means_edit_previews_with_its_changes_and_its_time(app_client):
    r = post(app_client, "golden-hour", draft_json("golden-hour", **DONE_MEANS))
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert body["approved"] is False and body["brightness"] == 90
    assert (
        body["changes"]["needs_d59"]
        and body["changes"]["needs_d63"]
        and body["changes"]["needs_slot_repaint"]
    )
    assert body["copy"]["modules"]["chosen"] == "generated"
    strand = next(t for t in body["tiers"] if t["tier"] == "strand")
    assert strand["solid"] == [255, 143, 191]
    assert 0 < body["elapsed_ms"] < 1000
    assert body["t1_lock"]["simulated"] is True and body["t1_lock"]["locked"] is False


def test_an_explicit_approved_copy_previews_resolved_not_refused(app_client):
    draft = draft_json("golden-hour", **DONE_MEANS, copy={"modules": "approved", "stepped": "approved"})
    r = post(app_client, "golden-hour", draft)
    assert r.status_code == 200
    assert r.json()["copy"]["modules"]["chosen"] == "generated"


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"layout": {"kind": "linear", "angle_deg": 30, "cycles": 2}}, "layout.cycles"),
        ({"sample_at": "mean"}, "sample_at"),
        ({"palette.4.name": "Sea glass", "palette.3.name": "sea-glass"}, "palette.4.name"),
        ({"stepped": None}, "stepped"),
    ],
)
def test_a_draft_that_doesnt_apply_is_a_problems_422_by_field(app_client, changes, field):
    r = post(app_client, "golden-hour", draft_json("golden-hour", **changes))
    assert r.status_code == 422, r.text[:400]
    body = r.json()
    assert body["detail"].endswith("problem(s).")
    assert [p["field"] for p in body["problems"]] == [field]
    assert all(set(p) == {"path", "field", "message"} for p in body["problems"])


def test_the_t1_lock_is_a_409_naming_the_field(app_client, monkeypatch):
    def locked(lab, scene_id):
        return {
            "locked": True,
            "simulated": False,
            "slot": "custom-3",
            "positions": ["r0c0"],
            "sentence": f"Golden Hour: {P.T1_LOCKED}.",
        }

    monkeypatch.setattr(P, "t1_lock", locked)
    r = post(app_client, "golden-hour", draft_json("golden-hour", **{"palette.2.hex": "#FF8FBF"}))
    assert r.status_code == 409
    assert r.json() == {"detail": f"Golden Hour: {P.T1_LOCKED}.", "field": "palette"}
    assert r.headers["cache-control"] == "no-store"
    r = post(app_client, "golden-hour", draft_json("golden-hour", brightness=60))
    assert r.status_code == 200  # brightness is a control row, not slot content


def test_the_catalog_starts_every_scene_from_its_approved_draft(app_client):
    r = app_client.get("/api/lab/catalog")
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    assert body["simulated"] is True and body["config_sha"]
    assert [s["id"] for s in body["scenes"]] == IDS
    for scene in body["scenes"]:
        assert scene["approved"] == D.seed_draft(TRIO, scene["id"]).model_dump(mode="json")
        assert scene["state"] == "approved"
        lock = scene["t1_lock"]
        assert lock["locked"] is False and lock["simulated"] is True
        assert (lock["sentence"] is not None) == scene["painted"]
        # the catalog's approved draft previews as approved
        r = post(app_client, scene["id"], scene["approved"])
        assert r.status_code == 200 and r.json()["approved"] is True


def test_today_in_sim_is_titled_what_this_simulator_shows(app_client_show):
    r = post(app_client_show, "coastal-tides", draft_json("coastal-tides", **{"palette.1.hex": "#6FD0D8"}))
    assert r.status_code == 200, r.text[:400]
    today = r.json()["today"]
    assert today["title"] == P.SIM_TITLE
    assert today["tier"] in ("stepped", "modules") and today["heads"] == "draft"
    assert today["lit"] is not None and today["positions"]["left_out"] == []


def test_lab_service_is_built_once(app_client, monkeypatch):
    built = []

    class Counting(SVC.LabService):
        def __init__(self, services):
            built.append(services)
            super().__init__(services)

    monkeypatch.setattr(SVC, "LabService", Counting)
    assert post(app_client, "golden-hour", draft_json("golden-hour")).status_code == 200
    assert app_client.get("/api/lab/catalog").status_code == 200
    assert post(app_client, "coastal-tides", draft_json("coastal-tides")).status_code == 200
    assert len(built) == 1


def test_no_yaml_is_parsed_on_the_loop_thread_after_construction(app_client, monkeypatch):
    """The Lab's config reads and re-validation run in the threadpool, never on the engine's event loop: each records
    whether its thread is running an event loop."""
    seen: list[tuple[str, bool]] = []

    def on_loop() -> bool:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        return True

    def watch(module, name):
        original = getattr(module, name)

        def wrapper(*a, **k):
            seen.append((name, on_loop()))
            return original(*a, **k)

        monkeypatch.setattr(module, name, wrapper)

    for name in ("load_scenes", "load_modes", "load_recipes", "parse_modes"):
        watch(D, name)
    watch(SVC, "load_canopy_config")
    assert post(app_client, "golden-hour", draft_json("golden-hour", **DONE_MEANS)).status_code == 200
    assert app_client.get("/api/lab/catalog").status_code == 200
    assert {n for n, _ in seen} >= {
        "load_scenes",
        "load_modes",
        "load_recipes",
        "parse_modes",
        "load_canopy_config",
    }
    assert not [n for n, loop in seen if loop], seen
