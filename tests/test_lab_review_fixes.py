"""Regression tests for the Design Lab Slice A review (2026-10-16): each one failed before its fix, or kills a
mutation that used to survive the whole suite.

Grouped by what they pin: the client copy's honesty, the generated T3 sentence naming only colours a strand shows, the
T1 lock (on save, and on renames), the export's labels and notes, the store's resilience (non-finite numbers, FIFOs,
deep YAML, twins, name reuse), the routes' 404/409 contracts, and the dev server's environment.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from lab_edits import DONE_ANGLE, OTHER_ANGLE, recoloured
from test_lab_store import (
    OCT_17,
    install_fakes,
    looks,
    make_store,
    save,
    seed,
    version_dict,
    write_looks,
)

from conductor.config import CONFIG_DIR, ConductorSettings
from conductor.design_lab import draft as D
from conductor.design_lab import export as X
from conductor.design_lab import preview as P
from conductor.design_lab import store as S
from conductor.design_lab.service import KEY
from conductor.geometry import load_canopy_config
from conductor.looks import scene_rules as R
from conductor.looks.schema import ModesFile
from conductor.scenes import SceneCatalog

BASELINE = Path(__file__).resolve().parent / "fixtures_lab_baseline"
TRIO = D.load_trio(BASELINE)  # the frozen approved config the unit tests' edits are written against
LIVE = D.load_trio(CONFIG_DIR)  # the app's Lab reads config/: route tests edit that
CANOPY = load_canopy_config(CONFIG_DIR / "canopy.yaml")
SETTINGS = ConductorSettings()
IDS = [s.id for s in TRIO.scenes.scenes]


def edited_json(scene_id: str, trio: D.ConfigTrio = TRIO, **changes) -> dict:
    """The approved draft's JSON with some fields changed (`palette.<i>.<f>` reaches inside), not validated."""
    data = D.seed_draft(trio, scene_id).model_dump(mode="json")
    for key, value in changes.items():
        parts = key.split(".")
        target = data
        for p in parts[:-1]:
            target = target[int(p)] if p.isdigit() else target[p]
        target[parts[-1]] = value
    return data


def edit(scene_id: str, trio: D.ConfigTrio = TRIO, **changes) -> D.SceneDraft:
    return D.SceneDraft.model_validate(edited_json(scene_id, trio, **changes))


def live_json(scene_id: str, **changes) -> dict:
    """A draft of the app's (live) config, as a request body sends it: the server validates it."""
    return edited_json(scene_id, LIVE, **changes)


def other_layout(trio: D.ConfigTrio, scene_id: str) -> dict:
    """A linear layout the scene doesn't have today (whatever an export did to config/)."""
    layout = D.seed_draft(trio, scene_id).layout.model_dump(mode="json")
    angle = (
        OTHER_ANGLE
        if layout.get("kind") == "linear" and layout.get("angle_deg") == DONE_ANGLE
        else DONE_ANGLE
    )
    return {"kind": "linear", "angle_deg": angle}


def lab_of_client(client):
    return client.app.state.services.extras[KEY]


def lab_events(client) -> list[dict]:
    return client.app.state.services.telemetry.recent(kind="lab")


def data_looks(client) -> Path:
    return Path(client.app.state.services.data) / "overrides" / "looks.yaml"


def route_save(client, drafts: dict, **extra):
    return client.post("/api/lab/versions", json={"drafts": drafts, **extra})


def pink_json() -> dict:
    return recoloured(D.seed_draft(LIVE, "golden-hour").model_dump(mode="json"))


def reload_lab(client) -> None:
    client.app.state.services.extras.pop(KEY, None)


# ---- the client copy is held to the honesty rules (findings: name/description motion words and counts) -------------
@pytest.mark.parametrize(
    "field, text",
    [
        ("name", "Moving Waves"),
        ("name", "Ocean Waves"),
        ("description", "Colours ripple and move across all 270 lights, animated every second."),
        ("description", "A wave of warm colour moving across the canopy."),
        ("description", "Colours across all nine strands."),
        ("mood", "Watch it move"),
    ],
)
def test_the_client_copy_never_claims_motion_or_counts_the_lights(field, text):
    data = D.seed_draft(TRIO, "golden-hour").model_dump(mode="json")
    data[field] = text
    with pytest.raises(ValueError) as exc:
        D.SceneDraft.model_validate(data)
    assert f"the {field} can't say" in str(exc.value)
    # every approved scene's own copy still validates
    for sid in IDS:
        D.seed_draft(TRIO, sid)
        D.seed_draft(LIVE, sid)


def test_a_motion_claim_in_the_description_is_a_422_on_its_field(app_client):
    for field, text in (
        ("description", "A wave of warm colour moving across the canopy."),
        ("name", "Moving Waves"),
    ):
        r = app_client.post(
            "/api/lab/preview",
            json={"scene_id": "golden-hour", "draft": live_json("golden-hour", **{field: text})},
        )
        assert r.status_code == 422, r.text
        assert [p["field"] for p in r.json()["problems"]] == [field]
        r = route_save(app_client, {"golden-hour": live_json("golden-hour", **{field: text})})
        assert r.status_code == 422 and not data_looks(app_client).exists()


def test_the_catalog_rule_holds_a_hand_edited_scenes_yaml_to_the_same_words():
    scenes = TRIO.scenes.model_dump(mode="json")
    golden = next(s for s in scenes["scenes"] if s["id"] == "golden-hour")
    golden["description"] = "Colours move across all 270 lights."
    found = R.scene_mode_problems(
        SceneCatalog.model_validate(scenes), TRIO.modes, TRIO.recipes, CANOPY, rules={"catalog"}
    )
    assert [(p.rule, p.mode, p.path) for p in found] == [("catalog", "golden-hour", "description")]
    assert D.problem_of(found[0]).field == "description"
    assert R.scene_mode_problems(TRIO.scenes, TRIO.modes, TRIO.recipes, CANOPY, rules={"catalog"}) == []


def test_a_description_the_colours_or_layout_moved_under_gets_a_note_never_a_refusal(tmp_path):
    from test_lab_preview import make_lab

    lab = make_lab(tmp_path)
    golden = D.seed_draft(TRIO, "golden-hour").model_dump(mode="json")
    golden["palette"] = golden["palette"][:4]  # Lavender dropped; the description still names it
    golden["solid"] = min(golden["solid"], 3)
    answer = P.preview(lab, D.SceneDraft.model_validate(golden))
    assert answer["copy_notes"] == [D.DESCRIPTION_NAMES_GONE.format(names="lavender")]
    calm = edit("lowcountry-calm", layout={"kind": "linear", "angle_deg": 30})
    assert P.preview(lab, calm)["copy_notes"] == [D.DESCRIPTION_UNCHANGED.format(what="layout")]
    assert P.preview(lab, D.seed_draft(TRIO, "lowcountry-calm"))["copy_notes"] == []
    # the export says it too (review notes and the decision)
    patched = D.apply_draft(TRIO, calm, canopy=CANOPY, settings=SETTINGS)
    result = X.export_patch(TRIO, patched, root=_export_root(), canopy=CANOPY, version=None, now=OCT_17)
    note = "Lowcountry Calm: " + D.DESCRIPTION_UNCHANGED.format(what="layout")
    assert note in result.review_notes and note in result.decision


def _export_root() -> Path:
    from test_lab_export import ROOT

    return ROOT


# ---- the T3 sentence names only colours a strand shows (finding: sweep copy names unshown colours) ---------------------
def _l1(a, b) -> int:
    return sum(abs(int(x) - int(y)) for x, y in zip(a, b, strict=True))


def _rgb(hex_: str) -> tuple[int, int, int]:
    return tuple(int(hex_[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


LAYOUTS = [
    {"kind": "sweep", "centre_in": [0, 0], "start_deg": 0},  # a corner: a quarter of the circle
    {"kind": "sweep", "centre_in": [0, 60], "start_deg": 0},  # an edge (the Lab's old default sweep)
    {"kind": "sweep", "centre_in": [112.5, 0], "start_deg": 90},
    {"kind": "sweep", "centre_in": [75, 60], "start_deg": 0},  # inside: the whole circle
    {"kind": "linear", "angle_deg": 30},
    {"kind": "linear", "angle_deg": 120},
    {"kind": "radial", "centre_in": [0, 0], "radius_in": "auto"},
    {"kind": "radial", "centre_in": [112.5, 60], "radius_in": "auto"},
]


@pytest.mark.parametrize(
    "layout", LAYOUTS, ids=lambda x: f"{x['kind']}-{x.get('centre_in', x.get('angle_deg'))}"
)
@pytest.mark.parametrize(
    "variant", [{}, {"invert": True}, {"transform": "mirror"}], ids=["plain", "invert", "mirror"]
)
def test_every_colour_the_t3_sentence_names_is_shown_by_some_strand(layout, variant):
    checked = 0
    for sid in IDS:
        draft = edit(sid, layout={**layout, **variant})
        try:
            patched = D.apply_draft(TRIO, draft, canopy=CANOPY, settings=SETTINGS)
        except D.DraftError:
            continue  # a layout this scene can't take (its stepped rung would go flat): nothing to name
        mode = patched.modes.mode(sid)
        colours = R.module_colours(mode, patched.recipes, CANOPY).values()
        listed = mode.techniques.modules.feature.rsplit(": ", 1)[1]
        palette = [(c.name.lower(), _rgb(c.hex)) for c in draft.palette]
        for i, (name, rgb) in enumerate(palette):
            if not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", listed):
                continue
            near = [palette[j][1] for j in (i - 1, i + 1) if 0 <= j < len(palette)]
            allowed = max(_l1(rgb, n) for n in near) / 2 + 3  # a strand within half a stop of this colour
            closest = min(_l1(rgb, c) for c in colours)
            assert closest <= allowed, (sid, name, closest, allowed, listed)
        checked += 1
    assert checked >= 3


def test_the_lab_names_the_reached_colours_in_stop_order():
    assert D.reached_stops(edit("golden-hour").layout, 5, CANOPY) == [1, 2, 3]
    corner = edit("golden-hour", layout={"kind": "sweep", "centre_in": [0, 0], "start_deg": 0})
    sentence = D.generated_copy(TRIO.modes.mode("golden-hour"), corner, CANOPY)["modules"]
    assert sentence.endswith("around one corner: soft gold, then peach.")
    # the moving sentence still names every colour (over a run each strand steps through the whole palette)
    assert (
        "sunset pink, rose and lavender"
        in D.generated_copy(TRIO.modes.mode("golden-hour"), corner, CANOPY)["stepped"]
    )


# ---- the T1 lock (findings: untested on save; a rename refused with a false sentence) ----------------------------------
def _app_painted_golden(monkeypatch):
    """Golden Hour's custom-3 hand-painted in the app on Module 1 (the app lock, D68 §2.6.0); nothing else recorded."""
    real = P.slot_records

    def records(lab, slot):
        api, app, total = real(lab, slot)
        if slot != "custom-3":
            return api, app, total
        entry = lab.services.registry.get("canopy-r0c0")
        return [], [entry], total

    monkeypatch.setattr(P, "slot_records", records)


def test_the_t1_lock_refuses_a_save_naming_the_field(app_client, monkeypatch):
    _app_painted_golden(monkeypatch)
    sentence = (
        f"{LIVE.scenes.get('golden-hour').name}'s slot is hand-painted in the Hubspace app on 1 module (Module 1); "
        "publishing a new design would "
        "make the iPad describe colours the lights don't show. Repaint it in the app and record it on the module "
        "page's Slots tab first, or publish only its brightness, copy or timing."
    )
    locked = {"detail": sentence, "field": "palette"}
    r = route_save(app_client, {"golden-hour": pink_json()})
    assert r.status_code == 409 and r.json() == locked
    assert r.headers["cache-control"] == "no-store"
    # two scenes at once, only one locked: the whole save is refused before anything is stored
    calm = live_json("lowcountry-calm", brightness=D.seed_draft(LIVE, "lowcountry-calm").brightness % 100 + 1)
    r = route_save(app_client, {"lowcountry-calm": calm, "golden-hour": pink_json()})
    assert r.status_code == 409 and r.json() == locked
    r = route_save(
        app_client, {"golden-hour": live_json("golden-hour", layout=other_layout(LIVE, "golden-hour"))}
    )
    assert r.status_code == 409 and r.json()["field"] == "layout"
    assert not data_looks(app_client).exists()
    assert app_client.get("/api/lab/versions").json() == {"versions": [], "current": None}
    assert lab_events(app_client) == []
    level = D.seed_draft(LIVE, "golden-hour").brightness
    r = route_save(
        app_client, {"golden-hour": live_json("golden-hour", brightness=60 if level != 60 else 61)}
    )
    assert r.status_code == 201, r.text  # brightness is a control row, not slot content


@pytest.mark.parametrize(
    "case",
    ["rename", "hex", "reorder", "add", "remove", "layout", "brightness", "solid"],
)
def test_the_t1_lock_and_needs_slot_repaint_agree(tmp_path, case):
    from test_lab_preview import _commissioned, make_lab

    lab = make_lab(tmp_path, simulated=False, registry=_commissioned(origin="app"))
    data = D.seed_draft(TRIO, "golden-hour").model_dump(mode="json")
    palette = data["palette"]
    if case == "rename":
        palette[1]["name"] = "Apricot"
    elif case == "hex":
        palette[1]["hex"] = "#FFAA90"
    elif case == "reorder":
        palette[0], palette[1] = palette[1], palette[0]
    elif case == "add":
        palette.append({"name": "Dusk", "hex": "#A0A0E0"})
    elif case == "remove":
        palette.pop()
        data["solid"] = min(data["solid"], len(palette) - 1)
    elif case == "layout":
        data["layout"] = {"kind": "linear", "angle_deg": 30}
    elif case == "brightness":
        data["brightness"] = 60
    else:
        data["solid"] = 0
    draft = D.SceneDraft.model_validate(data)
    repaint = D.draft_changes(TRIO, draft, canopy=lab.canopy)["needs_slot_repaint"]
    try:
        P.check_app_lock(lab, draft)
        refused = False
    except P.T1Locked:
        refused = True
    assert refused is repaint, case
    assert refused is (case in {"hex", "reorder", "add", "remove", "layout"})


# ---- the export's labels (findings: D59/D63 labels; the Studio SCENES note; the D59 paths) ----------------------------
def _export(**kw) -> X.LabExport:
    from test_lab_export import APPROVED, ROOT, _version, edit_scene

    return X.export_patch(
        APPROVED,
        edit_scene(APPROVED, "golden-hour", **kw),
        root=ROOT,
        canopy=CANOPY,
        version=_version(),
        now=OCT_17,
    )


def _section(decision: str, header: str) -> str:
    return next((line for line in decision.splitlines() if line.startswith(header)), "")


def test_a_scene_rename_needs_d59_and_the_decision_says_so():
    renamed = D.draft_changes(TRIO, edit("golden-hour", name="Golden Hours"), canopy=CANOPY)
    assert renamed["approved_colours"] == [] and renamed["name"] == {
        "from": "Golden Hour",
        "to": "Golden Hours",
    }
    assert renamed["needs_d59"] is True and not renamed["needs_slot_repaint"]
    result = _export(name="Golden Hours")
    assert "Golden Hour: name → Golden Hours" in result.approved_changes
    assert "Golden Hour: name → Golden Hours" in _section(result.decision, "Approved colour changes (D59")


def test_a_solid_only_change_is_not_listed_as_d59():
    assert D.draft_changes(TRIO, edit("golden-hour", solid=0), canopy=CANOPY)["needs_d59"] is False
    result = _export(solid=0)
    line = "Golden Hour: solid colour Sunset pink #FF9BC4 → Soft gold #FFDB9B"
    assert result.approved_changes == [] and "Approved colour changes" not in result.decision
    assert line in _section(result.decision, "Strand solid (a draft default, not a D59 colour)")


def test_stepped_timing_is_d65_never_d63():
    result = _export(stepped={"direction": "reverse"})
    line = "Golden Hour: stepped run direction forward → reverse"
    assert result.layout_changes == [] and result.stepped_changes == [line]
    assert "Layout changes (D63" not in result.decision
    assert line in _section(result.decision, "Stepped timing (D65; live off until Gate R)")


def test_client_copy_edits_are_listed_as_edited_not_regenerated():
    result = _export(mood="Linger a while")
    assert 'Golden Hour: mood "Let the evening glow" → "Linger a while"' in _section(
        result.decision, "Client copy edited (check it is true at every tier)"
    )
    assert "Copy regenerated" not in result.decision


def test_the_studio_scenes_note_covers_an_added_colour_and_the_opening_look():
    from test_lab_export import GOLDEN

    added = _export(
        palette=[*GOLDEN, ("Deep teal", "#127A7A")], keys=[k for k in _golden_keys()] + ["deep-teal"]
    )
    note = X.STUDIO_SCENES_PALETTE.format(
        scene="Golden Hour", n=5, palette=", ".join(f"{n} {h}" for n, h in GOLDEN)
    )
    assert note in added.review_notes and note in added.decision
    for kw in ({"solid": 0}, {"brightness": 60}):
        result = _export(**kw)
        opens = X.STUDIO_SCENES_OPENS.format(
            scene="Golden Hour", name="Golden Hour", solid="Sunset pink #FF9BC4", brightness=95
        )
        assert opens in result.review_notes, kw


def _golden_keys() -> list[str]:
    from test_lab_export import APPROVED

    return [s.color for s in APPROVED.modes.mode("golden-hour").techniques.modules.sample.canvas.stops]


def test_a_reordered_modes_yaml_is_refused_by_the_reread(monkeypatch):
    from test_lab_export import export, golden_edit

    real = X.rewrite_modes_yaml

    def swapped(text, *a, **k):
        out, notes = real(text, *a, **k)
        lines = out.splitlines(keepends=True)
        starts = [i for i, line in enumerate(lines) if re.match(r"^  [a-z][a-z0-9-]+:\s*(#.*)?$", line)]
        names = [lines[i].strip().split(":")[0] for i in starts]
        a, b = names.index("lowcountry-calm"), names.index("rainbow-row-sunset")
        assert b == a + 1
        block_a, block_b = lines[starts[a] : starts[b]], lines[starts[b] : starts[b + 1]]
        return "".join(lines[: starts[a]] + block_b + block_a + lines[starts[b + 1] :]), notes

    monkeypatch.setattr(X, "rewrite_modes_yaml", swapped)
    result = export(golden_edit())
    assert result.patch == "" and "config/modes.yaml" in result.problem


# ---- the store (findings: non-finite numbers, FIFOs, deep YAML, twins, name reuse) -------------------------------------
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")], ids=[".nan", ".inf", "-.inf"])
def test_a_non_finite_created_at_is_one_unreadable_version(app_client, bad):
    for _ in range(2):
        assert route_save(app_client, {"golden-hour": pink_json()}).status_code == 201
    path = data_looks(app_client)
    doc = yaml.safe_load(path.read_text())
    doc["versions"][0]["created_at"] = bad  # written as YAML's .nan / .inf / -.inf
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))
    assert re.search(r"created_at: -?\.(nan|inf)", path.read_text())
    reload_lab(app_client)
    r = app_client.get("/api/lab/versions")
    assert r.status_code == 200, r.text
    listed = {v["id"]: v for v in r.json()["versions"]}
    assert listed["v0001"]["unreadable"] is True and "created_at" in listed["v0001"]["error"]
    assert app_client.get("/api/lab/state").json()["store"]["unreadable"] == 1
    assert app_client.get("/api/lab/versions/v0002").status_code == 200


def test_quoted_non_finite_coordinates_are_a_422_and_a_stored_one_is_unreadable(app_client):
    for layout in (
        {"kind": "sweep", "centre_in": ["nan", 1], "start_deg": 0},
        {"kind": "radial", "centre_in": ["1e400", 3]},
    ):
        r = app_client.post(
            "/api/lab/preview",
            json={"scene_id": "golden-hour", "draft": live_json("golden-hour", layout=layout)},
        )
        assert r.status_code == 422, r.text
        assert [p["field"] for p in r.json()["problems"]] == ["layout"]
        assert (
            route_save(app_client, {"golden-hour": live_json("golden-hour", layout=layout)}).status_code
            == 422
        )
    assert route_save(app_client, {"golden-hour": pink_json()}).status_code == 201
    path = data_looks(app_client)
    doc = yaml.safe_load(path.read_text())
    doc["versions"][0]["drafts"]["golden-hour"]["layout"] = {
        "kind": "sweep",
        "centre_in": ["nan", 1],
        "start_deg": 0,
    }
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    reload_lab(app_client)
    r = app_client.get("/api/lab/versions")
    assert r.status_code == 200 and r.json()["versions"][0]["unreadable"] is True


def test_a_fifo_looks_yaml_never_blocks_the_store(tmp_path, monkeypatch):
    install_fakes(monkeypatch)
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    path = looks(tmp_path)
    path.parent.mkdir(parents=True)
    path.symlink_to(fifo)
    out: dict = {}
    thread = threading.Thread(target=lambda: out.update(store=make_store(tmp_path)[0]), daemon=True)
    thread.start()
    thread.join(5)
    if thread.is_alive():  # unblock the reader before failing
        with open(fifo, "w") as w:
            w.write("format: 1\n")
        thread.join(5)
        pytest.fail("loading a FIFO looks.yaml blocked")
    store = out["store"]
    assert store.error == "it isn't a regular file" and store.set_aside and not store.read_only
    assert store.file().versions == [] and not path.is_symlink()  # the link itself was set aside
    assert fifo.is_fifo()  # the FIFO it pointed at is left alone


@pytest.mark.parametrize("depth", [400, 400_000])
def test_a_deeply_nested_looks_yaml_is_set_aside_not_read_only(tmp_path, monkeypatch, depth):
    install_fakes(monkeypatch)
    write_looks(tmp_path, "format: 1\nversions:\n  - " + "[" * depth + "]" * depth + "\n")
    store, _, _ = make_store(tmp_path)
    assert store.read_only is False and store.set_aside is not None and store.error
    assert save(store).id == "v0001"  # and it saves again afterwards


def test_deleting_a_version_keeps_its_unreadable_twin(tmp_path, monkeypatch):
    install_fakes(monkeypatch)
    twin = {**version_dict("v0001"), "rings": {"stagger_s": 0.4}}
    write_looks(
        tmp_path,
        {
            "format": 1,
            "seq": 2,
            "current": None,
            "versions": [version_dict("v0001"), version_dict("v0002"), twin],
        },
    )
    store, _, _ = make_store(tmp_path)
    store.delete_version("v0001")
    kept = yaml.safe_load(looks(tmp_path).read_text())["versions"]
    assert [v["id"] for v in kept] == ["v0002", "v0001"] and kept[1] == twin  # the twin, verbatim
    nameless = [{"name": "x"}, {"name": "y"}]
    write_looks(tmp_path, {"format": 1, "seq": 0, "versions": nameless})
    store, _, _ = make_store(tmp_path)
    assert [u["id"] for u in store.unreadable()] == ["?1", "?2"]
    store.delete_version("?1")  # one at a time, never every id-less entry at once
    assert yaml.safe_load(looks(tmp_path).read_text())["versions"] == [{"name": "y"}]


def test_a_deleted_newest_version_never_reissues_its_name(tmp_path, monkeypatch):
    install_fakes(monkeypatch)
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        store, _, _ = make_store(tmp_path)
        assert save(store).name == "Client Oct 17 v1"
        assert save(store, note="bad copy").name == "Client Oct 17 v2 – bad copy"
        store.delete_version("v0002")
        assert save(store).name == "Client Oct 17 v3"
        store, _, _ = make_store(tmp_path)  # a restart keeps the day's count
        for vid in ("v0001", "v0003"):
            store.delete_version(vid)
        assert save(store).name == "Client Oct 17 v4"
    finally:
        monkeypatch.undo()
        time.tzset()


# ---- the routes (findings: races, internal KeyErrors, the catalog, the export's 409s, the config sha) -----------------
def test_a_parent_deleted_mid_save_is_a_404(app_client, monkeypatch):
    assert route_save(app_client, {"golden-hour": pink_json()}).status_code == 201
    real = S.LookStore.save_version

    def racing(self, **kw):
        self.delete_version(kw["parent"])  # another tab deletes the parent after the route's check
        return real(self, **kw)

    monkeypatch.setattr(S.LookStore, "save_version", racing)
    r = route_save(app_client, {"golden-hour": pink_json()}, parent="v0001")
    assert r.status_code == 404 and r.json() == {"detail": "There is no saved version 'v0001'."}
    assert r.headers["cache-control"] == "no-store"


def test_a_version_deleted_mid_restore_is_a_404(app_client, monkeypatch):
    assert route_save(app_client, {"golden-hour": pink_json()}).status_code == 201
    real = S.LookStore.set_current

    def racing(self, version_id):
        self.delete_version(version_id)
        return real(self, version_id)

    monkeypatch.setattr(S.LookStore, "set_current", racing)
    r = app_client.post("/api/lab/restore", json={"version": "v0001"})
    assert r.status_code == 404 and r.json() == {"detail": "There is no saved version 'v0001'."}


def test_an_internal_key_error_is_never_reported_as_a_missing_version(app_client, monkeypatch):
    assert route_save(app_client, {"golden-hour": pink_json()}).status_code == 201

    def broken(self, *a, **kw):
        raise KeyError("golden-hour")

    client = TestClient(app_client.app, raise_server_exceptions=False)
    monkeypatch.setattr(S.LookStore, "save_version", broken)
    r = client.post("/api/lab/versions", json={"drafts": {"golden-hour": pink_json()}, "parent": "v0001"})
    assert r.status_code == 500 and "There is no saved version" not in r.text
    monkeypatch.setattr(S.LookStore, "set_current", broken)
    r = client.post("/api/lab/restore", json={"version": "v0001"})
    assert r.status_code == 500 and "There is no saved version" not in r.text


def test_a_current_version_naming_another_scene_never_breaks_the_catalog(app_client):
    assert route_save(app_client, {"golden-hour": pink_json()}).status_code == 201
    path = data_looks(app_client)
    doc = yaml.safe_load(path.read_text())
    doc["versions"][0]["drafts"]["golden-hour"]["scene_id"] = "zz-top"
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    reload_lab(app_client)
    r = app_client.get("/api/lab/catalog")
    assert r.status_code == 200, r.text
    assert {s["id"]: s["state"] for s in r.json()["scenes"]}["golden-hour"] == "draft"
    assert app_client.get("/api/lab/versions").json()["versions"][0]["stale"] is True


def _export_fakes(monkeypatch):
    fake = install_fakes(monkeypatch)
    monkeypatch.setattr(P, "check_t1_lock", lambda lab, draft: None)
    monkeypatch.setattr(R, "scene_mode_problems", lambda *a, **kw: [])
    return fake


def test_a_failed_reread_check_is_a_409_on_both_formats(app_client, monkeypatch):
    _export_fakes(monkeypatch)
    problem = "the edited config/modes.yaml re-read differently from the draft"
    monkeypatch.setattr(
        X,
        "export_patch",
        lambda approved, patched, **kw: X.LabExport("", [], ["golden-hour"], "", [], [], [], problem),
    )
    assert (
        route_save(
            app_client, {"golden-hour": seed(LIVE, "golden-hour").model_dump(mode="json") | {"brightness": 7}}
        ).status_code
        == 201
    )
    for fmt in ("json", "patch"):
        r = app_client.get(f"/api/lab/export?version=v0001&format={fmt}")
        assert r.status_code == 409, (fmt, r.text)
        assert r.json()["problem"] == problem and problem in r.json()["detail"]
    assert "export" not in [e.get("action") for e in lab_events(app_client)]


def test_exporting_a_stale_version_is_a_409_with_its_problems(app_client, monkeypatch):
    fake = _export_fakes(monkeypatch)
    assert (
        route_save(
            app_client, {"golden-hour": seed(LIVE, "golden-hour").model_dump(mode="json") | {"brightness": 7}}
        ).status_code
        == 201
    )
    fake.refuse_all = True  # the approved config moved on
    for fmt in ("json", "patch"):
        r = app_client.get(f"/api/lab/export?version=v0001&format={fmt}")
        assert r.status_code == 409, (fmt, r.text)
        body = r.json()
        assert body["stale"] is True and body["problems"][0]["field"] == "description"
        assert "no longer applies" in body["detail"]
    assert [e.get("action") for e in lab_events(app_client)] == ["save"]


def test_every_answer_names_the_config_the_lab_loaded(app_client):
    sha = app_client.get("/api/lab/catalog").json()["config_sha"]
    for r in (
        app_client.get("/api/lab/state"),
        app_client.get("/api/lab/versions/v9999"),  # 404
        app_client.post(
            "/api/lab/preview", json={"scene_id": "golden-hour", "draft": {"scene_id": "golden-hour"}}
        ),  # 422
    ):
        assert r.headers.get("x-lab-config-sha") == sha, (r.status_code, r.headers)


# ---- purity and the lock messages (findings: preview mutating its draft; scene_rules negatives; the cycles lock) -----
def test_preview_never_changes_the_draft_it_is_given(tmp_path):
    from test_lab_preview import DONE_MEANS, make_lab
    from test_lab_preview import edit as preview_edit

    stale = preview_edit("golden-hour", **DONE_MEANS, copy={"modules": "approved", "stepped": "approved"})
    before = stale.model_dump()
    P.preview(make_lab(tmp_path), stale)
    assert stale.model_dump() == before and stale.copy.modules == "approved"


def _modes_with(mutate) -> ModesFile:
    modes = TRIO.modes.model_dump(mode="json")
    mutate(modes["modes"])
    return ModesFile.model_validate(modes)


@pytest.mark.parametrize(
    "case, mode_id, path",
    [
        ("strand-ends-on-another-colour", "golden-hour", "techniques → strand → feature"),
        ("strand-with-a-fallback", "golden-hour", "techniques → strand → fallback_look"),
        ("pace-in-another-case", "coastal-tides", "techniques → stepped → moving_feature"),
    ],
)
def test_each_scene_rule_guard_names_its_path(case, mode_id, path):
    def mutate(modes):
        t = modes[mode_id]["techniques"]
        if case == "strand-ends-on-another-colour":
            t["strand"]["feature"] = "Colour and dimming: all nine Hubspace strands set to soft gold."
        elif case == "strand-with-a-fallback":
            t["strand"]["fallback_look"] = dict(t["strand"]["look"])
        else:
            t["stepped"]["moving_feature"] = t["stepped"]["moving_feature"].replace(
                "every few seconds", "Every Few Seconds"
            )

    found = R.scene_mode_problems(TRIO.scenes, _modes_with(mutate), TRIO.recipes, CANOPY, rules=R.BLOCKING)
    assert [(p.mode, p.path) for p in found] == [(mode_id, path)], found


def test_the_cycles_lock_refuses_with_its_own_sentence():
    with pytest.raises(D.DraftError) as exc:
        D.apply_draft(
            TRIO,
            edit("golden-hour", layout={"kind": "linear", "angle_deg": 30, "cycles": 2}),
            canopy=CANOPY,
            settings=SETTINGS,
        )
    (problem,) = exc.value.problems
    assert problem.message == D.LOCKED_CYCLES and problem.path == "the draft of golden-hour → layout → cycles"


@pytest.mark.parametrize("scene_id", IDS)
@pytest.mark.parametrize("case", ["rename", "swap"])
def test_new_colour_names_or_order_regenerate_the_t3_sentence(scene_id, case):
    data = D.seed_draft(TRIO, scene_id).model_dump(mode="json")
    old = data["palette"][1]["name"]
    if case == "rename":
        data["palette"][1]["name"] = "Apricot"
    else:
        data["palette"][0], data["palette"][1] = data["palette"][1], data["palette"][0]
        data["solid"] = {0: 1, 1: 0}.get(data["solid"], data["solid"])
    draft = D.SceneDraft.model_validate(data)
    copy = D.resolve_copy(TRIO, draft, CANOPY)
    assert copy["modules"].chosen == "generated" and "colour names" in copy["modules"].why
    if copy["stepped"] is not None:
        assert copy["stepped"].chosen == "generated"
    feature = (
        D.apply_draft(TRIO, draft, canopy=CANOPY, settings=SETTINGS)
        .modes.mode(scene_id)
        .techniques.modules.feature
    )
    assert feature == copy["modules"].sentence
    if case == "rename":
        assert "apricot" in feature and old.lower() not in feature


# ---- the dev server (findings: environment never applied in a test; the show box's data dirs unpinned) --------------
def _launcher():
    from test_dev_design_lab import _launcher as launcher

    return launcher()


def test_main_runs_uvicorn_with_only_the_stripped_environment(monkeypatch, tmp_path):
    import uvicorn

    lab = _launcher()
    shell = {
        "PATH": "/usr/bin",
        "HUBSPACE_LIVE": "1",
        "HUBSPACE_DEVICE_ALLOWLIST": "a,b",
        "RAILWAY_ENVIRONMENT_NAME": "staging",
        "GITHUB_TOKEN": "t",
        "CONDUCTOR_BASIC_AUTH": "crew:canopy",
        "CONDUCTOR_ENV": "workshop",
    }
    monkeypatch.setattr(
        os, "environ", dict(shell)
    )  # main() clears and refills os.environ: never the real one
    seen: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app, env=dict(os.environ), **kw))
    assert lab.main(["--data", str(tmp_path / "d")]) == 0
    assert seen["env"] == {
        "PATH": "/usr/bin",
        "CONDUCTOR_MODE": "sim",
        "CONDUCTOR_AUTH": "off",
        "CONDUCTOR_DATA_DIR": str((tmp_path / "d").resolve()),
    }
    assert seen["app"] == "conductor.app:app" and seen["host"] == "127.0.0.1" and seen["port"] == 8200


def test_both_show_box_data_dirs_are_refused_by_name(monkeypatch, capsys, tmp_path):
    lab = _launcher()
    home = tmp_path / "home"
    show_dirs = (home / "canopy-show", home / "Library" / "Application Support" / "ColorCanopy")
    assert set(show_dirs) <= set(lab.show_box_dirs(home))
    for show in show_dirs:
        for data in (show, show / "data", show / "a" / ".." / "b"):
            assert "show box's data" in (lab.refusal(port=8200, host="127.0.0.1", data=data, home=home) or "")
    folded = home / "library" / "application support" / "colorcanopy" / "lab"
    assert "show box's data" in (lab.refusal(port=8200, host="127.0.0.1", data=folded, home=home) or "")

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: pytest.fail("the dev server must not start"))
    monkeypatch.setenv("HOME", str(home))
    target = home / "Library" / "Application Support" / "ColorCanopy" / "lab"
    with pytest.raises(SystemExit) as exc:
        lab.main(["--data", str(target)])
    assert exc.value.code == 2 and "show box's data" in capsys.readouterr().err
    assert not target.exists()
