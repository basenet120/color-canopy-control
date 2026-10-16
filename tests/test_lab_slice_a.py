"""Slice A end to end, through the real routes and the real S1/S2/S3 modules (no stand-ins): PROMPT "Done means".

Golden Hour gets a colour, a layout angle and a brightness edit; the preview answers in under a second; the edit is
saved as "Client Oct 17 v2", compared with approved and exported; the patch `git apply`s to a copy of config/ and
re-reads as exactly the saved drafts applied to approved. Through all of it the purity rule (PLAN §3.0) holds: the
Lab's approved trio, the cached catalog and modes the guest panel serves, and config/ on disk are unchanged.
(The exported config passing the whole suite is `make lab-roundtrip`, gated: about 9 minutes a case.)
"""

from __future__ import annotations

import time

import pytest

from conductor.config import CONFIG_DIR
from conductor.design_lab import draft as D
from conductor.design_lab import export as X
from conductor.design_lab.service import KEY
from conductor.looks import load_modes
from conductor.scenes import load_scenes

ORIGIN = {"Origin": "http://testserver"}
NEW_PINK = "#FF8FBF"


def _config_bytes() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(CONFIG_DIR.glob("*.yaml"))}


def _done_means_draft(seed: dict) -> dict:
    draft = {**seed, "palette": [dict(c) for c in seed["palette"]]}
    (pink,) = [c for c in draft["palette"] if c["name"] == "Sunset pink"]
    assert pink["hex"] != NEW_PINK
    pink["hex"] = NEW_PINK
    draft["layout"] = {"kind": "linear", "angle_deg": 30}
    draft["brightness"] = 90
    return draft


@pytest.mark.parametrize("client", ["app_client", "app_client_show"])
def test_the_client_workflow_end_to_end_and_pure(client, request, tmp_path):
    from test_lab_export import scratch_repo  # the reviewer's copy of config/, as S3's export tests use it

    c = request.getfixturevalue(client)
    config_before = _config_bytes()
    cached = load_scenes(CONFIG_DIR)
    cached_before, modes_before = cached.model_dump(), load_modes(CONFIG_DIR).model_dump()

    catalog = c.get("/api/lab/catalog")
    assert catalog.status_code == 200, catalog.text
    lab = c.app.state.services.extras[KEY]
    approved_before = lab.approved.dump()
    (golden,) = [s for s in catalog.json()["scenes"] if s["id"] == "golden-hour"]
    draft = _done_means_draft(golden["approved"])

    started = time.perf_counter()
    r = c.post("/api/lab/preview", json={"scene_id": "golden-hour", "draft": draft}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert time.perf_counter() - started < 1.0  # "sees ... update in under a second"
    preview = r.json()
    assert preview["approved"] is False and preview["warnings"] == [] and preview["brightness"] == 90
    assert len(preview["design"]["heads"]) == 270 and len(preview["design"]["modules"]) == 9
    assert [t["tier"] for t in preview["tiers"]] == golden["ladder"]
    assert all(t["feature"] for t in preview["tiers"])
    assert preview["changes"]["needs_d59"] and preview["changes"]["needs_slot_repaint"]
    assert preview["today"]["title"] == "What this simulator shows"
    if client == "app_client":
        assert preview["today"]["tier"] is None  # no engine in this app
    else:
        assert preview["today"]["tier"] in golden["ladder"]

    r = c.post(
        "/api/lab/versions",
        json={"drafts": {"golden-hour": draft}, "name": "Client Oct 17 v2", "note": "pinker sunset"},
        headers=ORIGIN,
    )
    assert r.status_code == 201, r.text
    saved = r.json()
    assert (
        saved["name"] == "Client Oct 17 v2" and saved["scenes"] == ["golden-hour"] and saved["stale"] is False
    )

    listing = c.get("/api/lab/versions").json()
    assert listing["current"] == saved["id"] and [v["id"] for v in listing["versions"]] == [saved["id"]]
    # A/B: the saved version beside the catalog's approved
    opened = c.get(f"/api/lab/versions/{saved['id']}").json()
    assert opened["drafts"]["golden-hour"]["palette"] != golden["approved"]["palette"]

    r = c.get(f"/api/lab/export?version={saved['id']}")
    assert r.status_code == 200, r.text
    exported = r.json()
    assert exported["problem"] == "" and exported["scenes"] == ["golden-hour"]
    assert X.SCENES_FILE in exported["files"] and X.MODES_FILE in exported["files"]
    assert any(NEW_PINK in line for line in exported["approved_changes"])
    assert exported["filename"].startswith(f"design-lab-{saved['id']}-")
    patch = c.get(f"/api/lab/export?version={saved['id']}&format=patch")
    assert patch.status_code == 200 and patch.text == exported["patch"]

    # the patch applies to config/ and re-reads as the saved drafts applied to approved
    from test_lab_export import applied

    texts = applied(scratch_repo(tmp_path), exported["patch"])
    saved_drafts = {k: D.SceneDraft.model_validate(v) for k, v in opened["drafts"].items()}
    patched = D.apply_drafts(lab.approved, saved_drafts, canopy=lab.canopy, settings=lab.settings)
    assert X.reread(texts, lab.canopy).dump() == patched.dump()

    r = c.post("/api/lab/restore", json={"version": "approved"}, headers=ORIGIN)
    assert r.status_code == 200 and r.json()["current"] is None

    # purity (§3.0): nothing the show reads changed
    assert lab.approved.dump() == approved_before
    assert load_scenes(CONFIG_DIR) is cached and cached.model_dump() == cached_before
    assert load_modes(CONFIG_DIR).model_dump() == modes_before
    assert _config_bytes() == config_before
    show = c.app.state.services.extras.get("show")
    if show is not None:
        assert show.modes_file.model_dump() == modes_before
