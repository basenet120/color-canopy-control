"""The Design Lab's version store, `<data>/overrides/looks.yaml` (conductor/design_lab/store.py, PLAN §3.5).

The store's own behaviour is tested over stand-ins for S1's draft functions (`fake_s1`: `apply_drafts`,
`is_approved`, `draft_changes`), so these tests pin the store and nothing else; `test_the_real_drafts_round_trip`
runs the same path through S1's real functions once they are built. Time is the project's VirtualClock (its epoch is
the "now"), so history and set-aside names are deterministic.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conductor.alerts import AlertBoard
from conductor.clock import VirtualClock
from conductor.config import CONFIG_DIR, ConductorSettings, load_registry
from conductor.design_lab import draft as D
from conductor.design_lab import store as S
from conductor.design_lab.draft import ConfigTrio, DraftError, Problem, SceneDraft, load_trio
from conductor.design_lab.store import LookStore, StoreReadOnly
from conductor.telemetry import Telemetry

TRIO: ConfigTrio = load_trio(CONFIG_DIR)
SETTINGS = ConductorSettings()
CANOPY: dict = {"width_in": 225, "height_in": 120}
SHA = "3f9a1c0e2b7d"
OTHER_SHA = "0123456789ab"
OCT_17 = 1792252800.0  # 2026-10-17 16:00 UTC: Oct 17 in every US time zone
STAMP = "20261017T160000Z"


# ---- helpers (imported by test_lab_routes_versions.py and test_lab_access.py) -------------------------------------
def seed(trio: ConfigTrio, scene_id: str) -> SceneDraft:
    """The approved scene as a SceneDraft, built by hand from the trio (independent of S1's seed_draft)."""
    scene, mode = trio.scenes.get(scene_id), trio.modes.mode(scene_id)
    sample = mode.techniques.modules.sample
    stepped = mode.techniques.stepped
    return SceneDraft.model_validate(
        {
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
    )


def edited(scene_id: str, **changes) -> SceneDraft:
    """The approved draft with some fields changed (validated again)."""
    return SceneDraft.model_validate({**seed(TRIO, scene_id).model_dump(mode="json"), **changes})


def pink(hex_: str = "#FF8FBF") -> SceneDraft:
    """The Done-means edit: Golden Hour's Sunset pink gets a new hex."""
    draft = seed(TRIO, "golden-hour").model_dump(mode="json")
    for colour in draft["palette"]:
        if colour["name"] == "Sunset pink":
            colour["hex"] = hex_
    return SceneDraft.model_validate(draft)


REFUSE = "Refused by the stand-in apply_drafts."


class FakeS1:
    """Stand-ins for S1's draft functions: `apply_drafts` refuses a draft whose description is REFUSE (or every
    draft while `refuse_all`), and an unknown scene; `is_approved` compares with the hand-built seed;
    `draft_changes` says needs_d59 when a palette colour differs."""

    def __init__(self) -> None:
        self.applied: list[dict[str, SceneDraft]] = []
        self.refuse_all = False

    def apply_drafts(self, trio, drafts, *, canopy, settings):
        self.applied.append(dict(drafts))
        ids = [s.id for s in trio.scenes.scenes]
        problems = []
        for sid, d in drafts.items():
            if sid not in ids:
                problems.append(Problem(f"scene {sid!r}", "scene_id", "there is no such scene"))
            elif self.refuse_all or d.description == REFUSE:
                problems.append(Problem(f"mode {sid!r} → description", "description", REFUSE))
        if problems:
            raise DraftError(problems)
        return trio

    def is_approved(self, trio, draft):
        return draft == seed(trio, draft.scene_id)

    def draft_changes(self, approved, draft, *, canopy):
        return {"needs_d59": draft.palette != seed(approved, draft.scene_id).palette}


def install_fakes(monkeypatch: pytest.MonkeyPatch) -> FakeS1:
    """Put FakeS1 in place of S1's draft functions (the store and the routes call them through the module)."""
    fake = FakeS1()
    monkeypatch.setattr(D, "apply_drafts", fake.apply_drafts)
    monkeypatch.setattr(D, "is_approved", fake.is_approved)
    monkeypatch.setattr(D, "draft_changes", fake.draft_changes)
    monkeypatch.setattr(D, "seed_draft", seed)
    return fake


@pytest.fixture
def fake_s1(monkeypatch) -> FakeS1:
    return install_fakes(monkeypatch)


@pytest.fixture
def eastern(monkeypatch):
    """The box's local time zone (default_name uses the local date)."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def make_store(data: Path, now: float = OCT_17) -> tuple[LookStore, VirtualClock, Telemetry]:
    clock = VirtualClock(epoch=now)  # off the loop, now() is the epoch: set clock.epoch to move time
    telemetry = Telemetry(None, clock)
    store = LookStore(data, clock=clock, telemetry=telemetry)
    store.load()
    return store, clock, telemetry


CTX = {"approved": TRIO, "canopy": CANOPY, "settings": SETTINGS, "config_sha": SHA}


def save(store: LookStore, drafts: dict | None = None, note: str = "", **kw) -> S.Version:
    return store.save_version(
        drafts=drafts if drafts is not None else {"golden-hour": pink()},
        note=note,
        name=kw.pop("name", None),
        parent=kw.pop("parent", None),
        **{**CTX, **kw},
    )


def looks(data: Path) -> Path:
    return data / "overrides" / "looks.yaml"


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def version_dict(version_id: str, **extra) -> dict:
    return {
        "id": version_id,
        "name": f"Version {version_id}",
        "note": "",
        "created_at": OCT_17,
        "parent": None,
        "config_sha": SHA,
        "drafts": {"golden-hour": pink().model_dump(mode="json")},
        **extra,
    }


def write_looks(data: Path, document: object | str | bytes) -> Path:
    path = looks(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(document, bytes):
        path.write_bytes(document)
    elif isinstance(document, str):
        path.write_text(document)
    else:
        path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True))
    return path


# ---- saving ----------------------------------------------------------------------------------------------------------
def test_save_validates_writes_history_then_atomic_0600(tmp_path, fake_s1, eastern):
    store, _, _ = make_store(tmp_path)
    version = save(
        store, {"golden-hour": pink(), "lowcountry-calm": seed(TRIO, "lowcountry-calm")}, "warmer sunset"
    )

    assert version.id == "v0001" and version.name == "Client Oct 17 v1 – warmer sunset"
    assert list(version.drafts) == ["golden-hour"]  # an unchanged scene isn't stored
    assert fake_s1.applied[-1] == {"golden-hour": pink()}  # the new version was validated before the write
    path = looks(tmp_path)
    assert mode_of(path) == 0o600
    (history,) = (tmp_path / "overrides" / "history").iterdir()
    assert history.name == f"looks-{STAMP}.yaml" and mode_of(history) == 0o600
    assert history.read_bytes() == path.read_bytes()
    on_disk = yaml.safe_load(path.read_text())
    assert on_disk["format"] == 1 and on_disk["seq"] == 1 and on_disk["current"] == "v0001"
    assert on_disk["versions"][0]["drafts"]["golden-hour"]["palette"][2] == {
        "name": "Sunset pink",
        "hex": "#FF8FBF",
    }
    assert [p.name for p in path.parent.iterdir() if p.name.startswith(".")] == []  # no temp file left

    again, _, _ = make_store(tmp_path)  # a restart reads back exactly what was saved
    assert again.file() == store.file() and again.get("v0001") == version and again.error is None


def test_a_refused_draft_writes_nothing(tmp_path, fake_s1):
    store, _, _ = make_store(tmp_path)
    with pytest.raises(DraftError) as err:
        save(store, {"golden-hour": edited("golden-hour", description=REFUSE)})
    assert [p.field for p in err.value.problems] == ["description"]
    assert not (tmp_path / "overrides").exists()
    assert store.file().versions == [] and store.file().seq == 0


def test_history_copies_never_overwrite_each_other(tmp_path, fake_s1):
    store, clock, _ = make_store(tmp_path)
    save(store)
    save(store, {"golden-hour": pink("#FF80B0")})  # the same second
    clock.epoch += 1
    save(store, {"golden-hour": pink("#FF70A0")})
    names = sorted(p.name for p in (tmp_path / "overrides" / "history").iterdir())
    assert names == [f"looks-{STAMP}-2.yaml", f"looks-{STAMP}.yaml", "looks-20261017T160001Z.yaml"]
    newest = tmp_path / "overrides" / "history" / "looks-20261017T160001Z.yaml"
    assert newest.read_bytes() == looks(tmp_path).read_bytes()


def test_a_failed_write_changes_nothing(tmp_path, fake_s1, monkeypatch):
    store, _, _ = make_store(tmp_path)
    first = save(store)
    before = looks(tmp_path).read_bytes()

    def no_disk(*a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(S, "atomic_write", no_disk)
    with pytest.raises(StoreReadOnly, match="couldn't save overrides/looks.yaml .*No space left"):
        save(store, {"golden-hour": pink("#FF80B0")})
    with pytest.raises(StoreReadOnly):
        store.delete_version(first.id)
    assert [v.id for v in store.file().versions] == ["v0001"] and store.file().current == "v0001"
    assert looks(tmp_path).read_bytes() == before
    assert store.read_only is False  # a failed write isn't a corrupt file


def test_a_full_store_refuses_a_new_version(tmp_path, fake_s1):
    write_looks(
        tmp_path,
        {
            "format": 1,
            "seq": 200,
            "current": None,
            "versions": [version_dict(f"v{n:04d}") for n in range(1, 201)],
        },
    )
    store, _, _ = make_store(tmp_path)
    assert len(store.file().versions) == 200 and store.error is None
    with pytest.raises(DraftError, match="problem") as err:
        save(store)
    assert "at most 200 versions" in err.value.problems[0].message
    store.delete_version("v0001")
    assert save(store).id == "v0201"


def test_save_refuses_an_unknown_parent(tmp_path, fake_s1):
    store, _, _ = make_store(tmp_path)
    with pytest.raises(KeyError):
        save(store, parent="v0009")
    assert not looks(tmp_path).exists()


# ---- names and ids ---------------------------------------------------------------------------------------------------
def test_default_names_count_per_day(tmp_path, fake_s1, eastern):
    assert S.default_name("warmer sunset", OCT_17, []) == "Client Oct 17 v1 – warmer sunset"
    assert S.default_name("", OCT_17, ["Client Oct 17 v1", "Client Oct 17 v2 – x"]) == "Client Oct 17 v3"
    assert S.default_name("", OCT_17, ["Client Oct 16 v7", "my own name"]) == "Client Oct 17 v1"

    store, clock, _ = make_store(tmp_path)
    assert save(store, note="first").name == "Client Oct 17 v1 – first"
    assert save(store, {"golden-hour": pink("#FF80B0")}, name="Paris's pick").name == "Paris's pick"
    assert (
        save(store, {"golden-hour": pink("#FF70A0")}, note="warmer sunset").name
        == "Client Oct 17 v2 – warmer sunset"
    )
    clock.epoch += 24 * 3600
    assert save(store, {"golden-hour": pink("#FF60A0")}).name == "Client Oct 18 v1"


def test_a_deleted_version_never_reissues_its_name_or_id(tmp_path, fake_s1, eastern):
    store, _, _ = make_store(tmp_path)
    v1 = save(store)
    v2 = save(store, {"golden-hour": pink("#FF80B0")})
    store.delete_version(v1.id)
    v3 = save(store, {"golden-hour": pink("#FF70A0")})
    assert (v1.name, v2.name, v3.name) == ("Client Oct 17 v1", "Client Oct 17 v2", "Client Oct 17 v3")
    store.delete_version(v3.id)  # the newest: its number still isn't reissued, also after a restart
    again, _, _ = make_store(tmp_path)
    assert again.file().seq == 3
    assert save(again, {"golden-hour": pink("#FF60A0")}).id == "v0004"


# ---- delete, current, restore ----------------------------------------------------------------------------------------
def test_delete_and_restore_and_undo(tmp_path, fake_s1):
    store, _, _ = make_store(tmp_path)
    v1 = save(store)
    v2 = save(store, {"golden-hour": pink("#FF80B0")}, parent=v1.id)
    assert store.file().current == v2.id and v2.parent == v1.id

    store.set_current(v2.parent)  # "Back to previous version" restores the parent
    assert make_store(tmp_path)[0].file().current == v1.id
    history = len(list((tmp_path / "overrides" / "history").iterdir()))
    store.set_current(v1.id)  # already current: nothing written
    assert len(list((tmp_path / "overrides" / "history").iterdir())) == history
    store.set_current(None)  # "Back to all approved"
    assert make_store(tmp_path)[0].file().current is None

    store.set_current(v1.id)
    store.delete_version(v1.id)  # deleting the current version clears `current`
    assert store.file().current is None and [v.id for v in store.file().versions] == [v2.id]
    assert make_store(tmp_path)[0].file().current is None
    with pytest.raises(KeyError):
        store.get(v1.id)
    with pytest.raises(KeyError):
        store.delete_version(v1.id)
    with pytest.raises(KeyError):
        store.set_current("v0042")


def test_a_current_that_names_no_version_opens_on_approved(tmp_path, fake_s1):
    write_looks(tmp_path, {"format": 1, "seq": 1, "current": "v0007", "versions": [version_dict("v0001")]})
    store, _, _ = make_store(tmp_path)
    assert store.file().current is None and store.error is None


# ---- summaries and staleness -----------------------------------------------------------------------------------------
def test_summaries_are_newest_first_with_the_version_json(tmp_path, fake_s1):
    store, clock, _ = make_store(tmp_path)
    v1 = save(store, note="sunset")
    clock.epoch += 60
    v2 = save(store, {"lowcountry-calm": edited("lowcountry-calm", brightness=60)}, parent=v1.id)
    summaries = store.version_summaries(**CTX)
    assert [s["id"] for s in summaries] == [v2.id, v1.id]
    assert summaries[1] == {
        "id": "v0001",
        "name": v1.name,
        "note": "sunset",
        "created_at": OCT_17,
        "parent": None,
        "scenes": ["golden-hour"],
        "needs_d59": True,  # an approved colour changed
        "stale": False,
        "problems": [],
        "older_config": False,
    }
    assert summaries[0]["needs_d59"] is False and summaries[0]["parent"] == v1.id
    body = store.version_json(v1.id, **CTX)
    assert body == {**summaries[1], "drafts": {"golden-hour": pink().model_dump(mode="json")}}
    with pytest.raises(KeyError):
        store.version_json("v0009", **CTX)


def test_a_stale_version_is_flagged_not_fatal(tmp_path, fake_s1):
    store, _, _ = make_store(tmp_path)
    good = save(store, {"golden-hour": pink()})
    assert len(fake_s1.applied) == 1  # the save validated it once ...
    (fine,) = store.version_summaries(**CTX)
    assert fine["stale"] is False and fine["older_config"] is False
    assert len(fake_s1.applied) == 1  # ... and listing it under the same config_sha doesn't apply it again

    fake_s1.applied.clear()
    fake_s1.refuse_all = True  # config/ moved on (a new sha) and the version no longer applies
    (summary,) = store.version_summaries(**{**CTX, "config_sha": OTHER_SHA})
    assert summary["stale"] is True and summary["older_config"] is True
    assert summary["problems"] == [
        {"path": "mode 'golden-hour' → description", "field": "description", "message": REFUSE}
    ]
    assert store.get(good.id) == good and store.error is None  # still listed, still openable for a look

    # Lazily computed and cached by (version id, config_sha): one apply per pair, never at load.
    store.version_summaries(**{**CTX, "config_sha": OTHER_SHA})
    store.version_json(good.id, **{**CTX, "config_sha": OTHER_SHA})
    assert len(fake_s1.applied) == 1
    fake_s1.applied.clear()
    again, _, _ = make_store(tmp_path)
    assert fake_s1.applied == []
    (reloaded,) = again.version_summaries(**{**CTX, "config_sha": OTHER_SHA})
    assert reloaded["stale"] is True and len(fake_s1.applied) == 1


# ---- corrupt files ---------------------------------------------------------------------------------------------------
CORRUPT = {
    "yaml": "versions: [unclosed\n",
    "not-a-mapping": "- just\n- a list\n",
    "bad-top-level": "format: 1\nseq: -3\nversions: []\n",
    "unknown-key": "format: 1\nseq: 0\nversions: []\nrings: true\n",
    "versions-not-a-list": "format: 1\nversions: {v0001: x}\n",
    "format-2": "format: 2\nseq: 0\nversions: []\n",
    "empty": "",
    "binary": b"\xff\xfe\x00garbage",
    "oversize": "#" * (S.MAX_BYTES + 1),
}


@pytest.mark.parametrize("kind", list(CORRUPT))
def test_a_corrupt_looks_yaml_is_set_aside_and_alerted(tmp_path, fake_s1, kind):
    content = CORRUPT[kind]
    path = write_looks(tmp_path, content)
    original = path.read_bytes()
    store, _, telemetry = make_store(tmp_path)

    aside = tmp_path / "overrides" / f"looks.corrupt-{STAMP}.yaml"
    assert not path.exists() and aside.read_bytes() == original and mode_of(aside) == 0o600
    assert store.set_aside == aside.name and store.read_only is False
    assert store.error and len(store.error) <= S.REASON_MAX
    assert store.file() == S.LooksFile() and store.unreadable() == []
    if kind == "format-2":
        assert "format 2" in store.error
    if kind == "oversize":
        assert "over the 1000000-byte limit" in store.error

    # The alert, recorded as lab_of does on the loop thread, is a warn line on /ops "Needs attention".
    board = AlertBoard()
    board.attach(telemetry)
    from conductor.design_lab.service import _record_pending_alert

    _record_pending_alert(SimpleNamespace(store=store, services=SimpleNamespace(telemetry=telemetry)))
    (alert,) = board.active()
    assert alert.level == "warn"
    assert alert.message.startswith("Design Lab: overrides/looks.yaml couldn't be read (")
    assert (
        f"set aside as {aside.name}" in alert.message and "The show doesn't use this file." in alert.message
    )
    assert store.pending_alert is None  # recorded once

    # The Lab works on: the next save starts a fresh file and never touches the set-aside copy.
    assert save(store).id == "v0001"
    assert aside.read_bytes() == original and yaml.safe_load(path.read_text())["seq"] == 1


def test_one_bad_version_is_listed_unreadable_and_kept(tmp_path, fake_s1):
    bad = version_dict("v0002", rings={"stagger_s": 0.4})  # a field from a later slice, rolled back
    write_looks(
        tmp_path,
        {
            "format": 1,
            "seq": 3,
            "current": "v0003",
            "versions": [
                version_dict("v0001"),
                bad,
                version_dict("v0003"),
                "not a version",
                version_dict("v0001"),
            ],
        },
    )
    store, _, _ = make_store(tmp_path)
    assert store.error is None and store.set_aside is None and store.pending_alert is None
    assert [v.id for v in store.file().versions] == ["v0001", "v0003"] and store.file().current == "v0003"
    unreadable = store.unreadable()
    assert [(u["id"], u["unreadable"]) for u in unreadable] == [("v0002", True), ("?", True), ("v0001", True)]
    assert "rings" in unreadable[0]["error"] and "second version" in unreadable[2]["error"]
    with pytest.raises(KeyError):
        store.get("v0002")
    assert [s["id"] for s in store.version_summaries(**CTX)] == ["v0003", "v0001", "v0002", "?", "v0001"]

    new = save(store)
    assert new.id == "v0004"
    kept = yaml.safe_load(looks(tmp_path).read_text())["versions"]
    assert kept[1] == bad and kept[3] == "not a version"  # written back verbatim
    again, _, _ = make_store(tmp_path)
    assert [u["id"] for u in again.unreadable()] == ["v0002", "?", "v0001"]

    store.delete_version("v0002")  # an unreadable version can be cleared out by hand
    assert [u["id"] for u in store.unreadable()] == ["?", "v0001"]
    assert all(
        not (isinstance(v, dict) and v.get("rings"))
        for v in yaml.safe_load(looks(tmp_path).read_text())["versions"]
    )


def test_an_oversize_file_is_never_read(tmp_path, fake_s1, monkeypatch):
    path = write_looks(tmp_path, "#" * (S.MAX_BYTES + 1))
    real_text, real_bytes, real_open = Path.read_text, Path.read_bytes, Path.open

    def guard(real):
        def read(self, *a, **kw):
            assert self.name != "looks.yaml", "an oversize looks.yaml was read"
            return real(self, *a, **kw)

        return read

    monkeypatch.setattr(Path, "read_text", guard(real_text))
    monkeypatch.setattr(Path, "read_bytes", guard(real_bytes))
    monkeypatch.setattr(Path, "open", guard(real_open))
    store, _, _ = make_store(tmp_path)
    assert not path.exists() and store.set_aside == f"looks.corrupt-{STAMP}.yaml"
    assert "over the 1000000-byte limit" in store.error


def test_a_set_aside_name_collision_gets_a_suffix(tmp_path, fake_s1):
    path = write_looks(tmp_path, "versions: [unclosed\n")
    earlier = tmp_path / "overrides" / f"looks.corrupt-{STAMP}.yaml"
    earlier.write_text("the first bad file")
    (tmp_path / "overrides" / f"looks.corrupt-{STAMP}-2.yaml").write_text("the second bad file")
    store, _, _ = make_store(tmp_path)
    assert store.set_aside == f"looks.corrupt-{STAMP}-3.yaml"
    assert earlier.read_text() == "the first bad file" and not path.exists()
    assert (tmp_path / "overrides" / store.set_aside).read_text() == "versions: [unclosed\n"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_an_unwritable_overrides_dir_makes_the_store_read_only(tmp_path, fake_s1):
    path = write_looks(tmp_path, "versions: [unclosed\n")
    overrides = path.parent
    overrides.chmod(0o500)
    try:
        store, _, _ = make_store(tmp_path)
        assert store.read_only is True and store.set_aside is None
        assert path.read_text() == "versions: [unclosed\n"  # left where it was: the evidence
        assert "moving it aside failed" in store.error
        assert store.pending_alert.startswith(
            "Design Lab: overrides/looks.yaml couldn't be read or set aside ("
        )
        assert store.pending_alert.endswith("the Lab won't save until it is moved by hand.")
        for write in (
            lambda: save(store),
            lambda: store.delete_version("v0001"),
            lambda: store.set_current(None),
        ):
            with pytest.raises(StoreReadOnly, match="The Lab's store couldn't be set aside: "):
                write()
    finally:
        overrides.chmod(0o700)
    assert path.read_text() == "versions: [unclosed\n"  # never overwritten
    assert sorted(p.name for p in overrides.iterdir()) == ["looks.yaml"]  # no history copy either


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_file_makes_the_store_read_only(tmp_path, fake_s1):
    path = write_looks(tmp_path, {"format": 1, "seq": 1, "versions": [version_dict("v0001")]})
    before = path.read_bytes()
    path.chmod(0o000)
    try:
        store, _, _ = make_store(tmp_path)
        assert store.read_only is True and store.set_aside is None and "Permission denied" in store.error
        with pytest.raises(StoreReadOnly):
            save(store)
    finally:
        path.chmod(0o600)
    assert path.read_bytes() == before and not (tmp_path / "overrides" / "history").exists()


# ---- boundaries ------------------------------------------------------------------------------------------------------
def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_the_store_never_writes_into_config(tmp_path, fake_s1):
    before = _tree(CONFIG_DIR)
    data = tmp_path / "data"
    store, _, _ = make_store(data)
    v1 = save(store)
    save(store, {"golden-hour": pink("#FF80B0")}, parent=v1.id)
    store.set_current(v1.id)
    store.delete_version(v1.id)
    assert _tree(CONFIG_DIR) == before
    written = [str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file()]
    assert written and all(w.startswith("data/overrides/") for w in written), written


def test_the_show_never_reads_looks_yaml(tmp_path):
    write_looks(tmp_path, "versions: [unclosed\n")
    registry = load_registry(CONFIG_DIR, tmp_path)  # the registry overlay loader ignores it
    assert registry.overlay_path == tmp_path / "overrides" / "registry.yaml"
    conductor = Path(S.__file__).resolve().parents[1]
    readers = [
        str(p.relative_to(conductor))
        for p in conductor.rglob("*.py")
        if "design_lab" not in p.parts
        and p.name != "design_lab.py"
        and any(word in p.read_text() for word in ("looks.yaml", "LOOKS_FILE", "LookStore"))
    ]
    assert readers == []  # only the Lab (conductor/design_lab/, routes/design_lab.py) knows the file


def test_load_never_raises(tmp_path, fake_s1, monkeypatch):
    write_looks(tmp_path, {"format": 1, "versions": []})

    def boom(*a, **kw):
        raise RuntimeError("an unexpected failure\nwith a second line")

    monkeypatch.setattr(S.yaml, "safe_load", boom)
    store, _, _ = make_store(tmp_path)
    assert store.read_only is True and store.error == "an unexpected failure"
    assert looks(tmp_path).exists()


# ---- with S1's real draft functions ----------------------------------------------------------------------------------
def test_the_real_drafts_round_trip(tmp_path):
    try:
        D.seed_draft(TRIO, "golden-hour")
        D.apply_drafts(TRIO, {}, canopy=real_canopy(), settings=SETTINGS)
    except NotImplementedError as exc:
        pytest.skip(f"S1 isn't built yet ({exc})")
    canopy = real_canopy()
    store, _, _ = make_store(tmp_path)
    drafts = {sid: D.seed_draft(TRIO, sid) for sid in (s.id for s in TRIO.scenes.scenes)}
    drafts["golden-hour"] = pink()
    version = store.save_version(
        drafts=drafts,
        note="pink",
        name=None,
        parent=None,
        approved=TRIO,
        canopy=canopy,
        settings=SETTINGS,
        config_sha=SHA,
    )
    assert list(version.drafts) == ["golden-hour"]  # the five approved seeds aren't stored
    (summary,) = store.version_summaries(approved=TRIO, canopy=canopy, settings=SETTINGS, config_sha=SHA)
    assert summary["stale"] is False and summary["needs_d59"] is True
    assert make_store(tmp_path)[0].get(version.id) == version


def real_canopy() -> dict:
    """The real canopy geometry (read lazily)."""
    from conductor.geometry import load_canopy_config

    return load_canopy_config(CONFIG_DIR / "canopy.yaml")
