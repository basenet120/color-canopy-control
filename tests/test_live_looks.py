"""The show's live looks (D68, docs/live-lab/PLAN.md §2.2–§2.3, §5.2 "Overlay load"): `overrides/show-looks.yaml`
is the published trio the show compiles from; a missing file is the approved (git) looks; a file that can't be used
for ANY reason is the approved looks plus an /ops line and a warn alert — never a failed start — and is left byte for
byte where it is; a published trio equal to git's is followed as git; one that masks a changed git warns."""

from __future__ import annotations

import ast
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fake_compiler import settings_with
from live_lab import (
    GIT,
    GOLDEN,
    NEW_PINK,
    PINKER,
    brighter,
    client_of,
    install_no_threads,
    patched_trio,
    pinker_hour,
    publication_document,
    publish,
    save,
    show,
    write_document,
)

from conductor.clock import VirtualClock, run_virtual
from conductor.config import CONFIG_DIR, ConfigError, load_settings
from conductor.engine_api import Tier
from conductor.geometry import load_canopy_config
from conductor.looks import compile_mode, parse_modes
from conductor.looks import live as L

SETTINGS = load_settings(CONFIG_DIR)
CANOPY = load_canopy_config(CONFIG_DIR / "canopy.yaml")
PINK = patched_trio(pinker_hour())


def load(
    data: Path, *, settings=SETTINGS, config_dir: Path = CONFIG_DIR, now: float = 1792800000.0
) -> L.LiveLooks:
    return L.load_live_looks(config_dir, data, settings=settings, canopy=CANOPY, now=now)


def services_for(data: Path, *, config_dir: Path = CONFIG_DIR) -> SimpleNamespace:
    from conductor.telemetry import Telemetry

    clock = VirtualClock()
    return SimpleNamespace(
        config_dir=config_dir,
        data=data,
        settings=SETTINGS,
        clock=clock,
        telemetry=Telemetry(None, clock),
        extras={},
        problems=[],
        gate_settings=SimpleNamespace(live=False),
        roles=SimpleNamespace(problem=None),
    )


def board_keys(services) -> set[str]:
    from conductor.alerts import AlertBoard

    board = AlertBoard()
    board.attach(services.telemetry)
    return {a.key for a in board.active()}


# ---- the plain cases ------------------------------------------------------------------------------------------------
def test_no_data_dir_or_no_file_is_the_approved_looks_with_no_alert(tmp_path):
    for data in (None, tmp_path):
        looks = (
            load(data)
            if data is not None
            else L.load_live_looks(CONFIG_DIR, None, settings=SETTINGS, canopy=CANOPY)
        )
        assert looks.source == "approved" and looks.problem is None and not looks.git_changed
        assert L.trio_sha(looks.trio) == L.trio_sha(GIT)
        assert looks.live_id == "approved" and looks.name == L.APPROVED_NAME
    assert L.load_live_looks(CONFIG_DIR, None, settings=SETTINGS, canopy=CANOPY).path is None
    services = services_for(tmp_path)
    L.build_live_looks(services)
    assert board_keys(services) == set()
    assert L.runtime_problem_lines(services) == []
    assert not (tmp_path / "overrides").exists()  # loading never writes a file it didn't find


def test_a_valid_published_file_is_what_the_show_compiles(tmp_path):
    write_document(tmp_path, publication_document(PINK))
    looks = load(tmp_path)
    assert looks.source == "published" and looks.problem is None and not looks.git_changed
    assert L.trio_sha(looks.trio) == L.trio_sha(PINK)
    assert looks.trio.scenes.get(GOLDEN).name == PINKER
    assert looks.trio.scenes.get(GOLDEN).palette[2].hex == NEW_PINK
    assert looks.live_id == "p0001" and looks.file.seq == 1
    assert L.changed_scenes(looks.git, looks.trio) == [GOLDEN]
    assert (
        L.scene_source(looks, GOLDEN) == "published"
        and L.scene_source(looks, "lowcountry-calm") == "approved"
    )


def test_the_shows_t3_colours_are_the_drafts_after_a_restart_over_the_file(monkeypatch):
    """The show built over a data dir holding the published file compiles Golden Hour from it."""

    async def main():
        def prepare(services):
            write_document(services.data, publication_document(PINK))

        w = await show(prepare=prepare)
        live = compile_mode(GOLDEN, Tier.T3, w.wiring.context())
        ctx = w.wiring.context(PINK.modes, PINK.recipes)
        drafted = compile_mode(GOLDEN, Tier.T3, ctx)
        git = compile_mode(GOLDEN, Tier.T3, w.wiring.context(GIT.modes, GIT.recipes))
        out = (
            {d: mt.rows for d, mt in live.modules.items()},
            {d: mt.rows for d, mt in drafted.modules.items()},
            {d: mt.rows for d, mt in git.modules.items()},
            w.looks.source,
        )
        await w.close()
        return out

    live, drafted, git, source = run_virtual(main)
    assert source == "published" and live == drafted and live != git


# ---- every fallback cause ---------------------------------------------------------------------------------------------
def _doc(**current) -> dict:
    return publication_document(PINK, **current)


def _sha_mismatch() -> dict:
    doc = _doc()
    golden = next(s for s in doc["current"]["trio"]["scenes"]["scenes"] if s["id"] == GOLDEN)
    golden["description"] = (
        "Soft gold and peach, then pinks."  # a valid edit: only the checksum says it was made
    )
    return doc


def _schema() -> dict:
    trio = PINK.dump()
    trio["scenes"]["scenes"][0]["name"] = "x" * 80  # Scene.name is at most 40
    return _doc(trio=trio, trio_sha=L._sha(trio))


def _ladder() -> dict:
    trio = PINK.dump()
    trio["modes"]["modes"][GOLDEN]["ladder"] = ["canvas", "slot", "modules", "strand"]  # stepped dropped
    return _doc(trio=trio, trio_sha=L._sha(trio))


def _blocking(**edit) -> dict:
    """PINK with one edit only validate_trio's blocking scene rules refuse (the checksum recomputed: a valid file)."""
    trio = PINK.dump()
    if "canvas_brightness" in edit:
        trio["modes"]["modes"][GOLDEN]["techniques"]["canvas"]["brightness"] = edit["canvas_brightness"]
    if "description" in edit:
        golden = next(s for s in trio["scenes"]["scenes"] if s["id"] == GOLDEN)
        golden["description"] = edit["description"]
    return _doc(trio=trio, trio_sha=L._sha(trio))


def _deep(n: int) -> str:
    return "format: 1\nseq: " + "[" * n + "]" * n + "\n"


CAUSES = {
    "bad yaml": "format: 1\ncurrent: [unclosed\n",
    "too deep": _deep(100),
    "recursion": _deep(20000),
    "an anchor": "format: 1\nseq: &n 3\nqueued: {}\n",
    "an alias bomb": "a: &a [x, x, x, x, x, x, x, x, x]\n"
    + "".join(f"{chr(98 + i)}: &{chr(98 + i)} [{', '.join(['*' + chr(97 + i)] * 9)}]\n" for i in range(8)),
    "bad top level": "- 1\n- 2\n",
    "unknown key": "format: 1\nseq: 1\nsurprise: true\n",
    "unknown format": "format: 2\nseq: 1\n",
    "not utf-8": b"format: 1\nseq: \xff\xfe\n",
    "version without trio": _doc(trio=None),
    "trio_sha mismatch": _sha_mismatch(),
    "schema refusal": _schema(),
    "a ladder that differs from git's": _ladder(),
    "a bad current": _doc(id="not-an-id"),
    "a blocking scene rule": _blocking(canvas_brightness=37),
    "scene copy that isn't true": _blocking(description="All 270 lights glow gold."),
}


WHO_SAYS = {
    "a ladder that differs from git's": "isn't config/modes.yaml's",
    "trio_sha mismatch": "doesn't match its trio_sha",
    "an anchor": "anchors or aliases",
    "an alias bomb": "anchors or aliases",
    # the scene ↔ mode rules (validate_trio's last step) are the only guard for these two (B1-4)
    "a blocking scene rule": "isn't the scene's brightness",
    "scene copy that isn't true": "can't say '270'",
}


@pytest.mark.parametrize("cause", list(CAUSES))
def test_every_unusable_file_falls_back_to_approved_and_is_left_byte_for_byte(tmp_path, cause):
    document = CAUSES[cause]
    path = tmp_path / "overrides" / L.LOOKS_FILE
    path.parent.mkdir(parents=True)
    if isinstance(document, bytes):
        path.write_bytes(document)
    else:
        write_document(tmp_path, document)
    before = path.read_bytes()
    started = time.perf_counter()
    looks = load(tmp_path)
    assert time.perf_counter() - started < 1.0  # an alias bomb or a deep file can't hang the lifespan
    assert looks.source == "approved" and looks.problem, cause
    assert len(looks.problem) <= L.REASON_MAX and "\n" not in looks.problem
    if cause in WHO_SAYS:  # the guard meant for it says so (the scene rules would catch some of these too)
        assert WHO_SAYS[cause] in looks.problem, looks.problem
    assert L.trio_sha(looks.trio) == L.trio_sha(GIT)
    assert path.read_bytes() == before  # never moved, never rewritten
    assert sorted(p.name for p in path.parent.iterdir()) == [L.LOOKS_FILE]


@pytest.mark.parametrize(
    "why, settings",
    [
        ("idle_scene isn't a mode", settings_with(kiosk={"idle_scene": "disco-night"})),
        ("a stepped run outlasts idle_return_s", settings_with(kiosk={"idle_return_s": 60})),
    ],
)
def test_the_shows_own_load_checks_hold_a_published_trio_too(tmp_path, why, settings):
    write_document(tmp_path, publication_document(PINK))
    looks = load(tmp_path, settings=settings)
    assert looks.source == "approved" and looks.problem, why
    with pytest.raises(ConfigError):
        L.validate_trio(PINK.dump(), git=GIT, settings=settings, canopy=CANOPY)


def test_a_published_trio_that_regroups_a_scene_is_refused():
    """option_of (the iPad's scene groups, D76) is git's, like the ids and ladders: a publication that moves a scene
    under another base is unusable, and a scene that keeps git's (None, left out of the dump) validates."""
    dump = GIT.dump()
    # left out while unset (the six bases): byte-identical; D77's options carry it
    for scene in dump["scenes"]["scenes"]:
        assert ("option_of" in scene) == (GIT.scenes.get(scene["id"]).option_of is not None), scene["id"]
    L.validate_trio(dump, git=GIT, settings=SETTINGS, canopy=CANOPY)
    # D77's Rainbow Row Sunset #2 moved under another base, then taken out of its group: both unusable
    regrouped = GIT.dump()
    option = next(s for s in regrouped["scenes"]["scenes"] if s["id"] == "rainbow-row-sunset-2")
    option["option_of"] = "lowcountry-calm"
    with pytest.raises(
        ConfigError, match=r"scene 'rainbow-row-sunset-2' → option_of: 'lowcountry-calm' isn't"
    ) as exc:
        L.validate_trio(regrouped, git=GIT, settings=SETTINGS, canopy=CANOPY)
    assert "config/scenes.yaml's 'rainbow-row-sunset'" in str(exc.value)
    assert "the Lab never changes it" in str(exc.value)
    option.pop("option_of")
    with pytest.raises(ConfigError, match=r"scene 'rainbow-row-sunset-2' → option_of: None isn't"):
        L.validate_trio(regrouped, git=GIT, settings=SETTINGS, canopy=CANOPY)


def test_an_oversized_file_is_never_read(tmp_path, monkeypatch):
    path = write_document(tmp_path, publication_document(PINK))
    monkeypatch.setattr(L, "MAX_BYTES", 100)

    real_bytes, real_text = Path.read_bytes, Path.read_text

    def no_open(*_a, **_k):
        raise AssertionError("an oversized overlay was read")

    def guard(real):
        def read(self, *a, **k):
            if self.name == L.LOOKS_FILE:
                raise AssertionError("an oversized overlay was read")
            return real(self, *a, **k)

        return read

    monkeypatch.setattr(L, "open", no_open, raising=False)  # the module's only open() is the overlay's
    monkeypatch.setattr(Path, "read_bytes", guard(real_bytes))
    monkeypatch.setattr(Path, "read_text", guard(real_text))
    looks = load(tmp_path)
    assert looks.source == "approved" and "over the 100-byte limit" in looks.problem
    assert path.exists()


def test_a_validate_trio_that_raises_anything_is_still_a_fallback(tmp_path, monkeypatch):
    write_document(tmp_path, publication_document(PINK))

    def boom(*_a, **_k):
        raise RuntimeError("a bug in the validator")

    monkeypatch.setattr(L, "validate_trio", boom)
    looks = load(tmp_path)
    assert looks.source == "approved" and looks.problem == "a bug in the validator"


def test_a_fallback_raises_the_load_alert_and_the_ops_line_until_an_install(tmp_path):
    write_document(tmp_path, CAUSES["trio_sha mismatch"])
    services = services_for(tmp_path)
    looks = L.build_live_looks(services)
    assert board_keys(services) == {L.LOAD_KEY}
    [line] = L.runtime_problem_lines(services)
    assert line.startswith(
        "Published looks: overrides/show-looks.yaml couldn't be used (its trio doesn't match"
    )
    assert line.endswith(
        "so the show runs the approved (git) looks. Publish again from the Design Lab, or Revert to approved."
    )
    L.install_looks(services, looks.git, why="a test")
    assert L.runtime_problem_lines(services) == [] and looks.problem is None
    assert board_keys(services) == set()


def test_a_bad_history_row_never_resets_seq_or_the_repaint_gate(tmp_path):
    doc = publication_document(PINK)
    doc["seq"] = 7
    doc["repaints"] = {GOLDEN: 1792700000.0, "bad": "x"}
    doc["history"].insert(0, {"id": "nope", "kind": "version"})
    write_document(tmp_path, doc)
    looks = load(tmp_path)
    assert looks.source == "published" and looks.problem is None
    assert looks.file.seq == 7 and looks.file.repaints == {GOLDEN: 1792700000.0}
    assert [h.id for h in looks.file.history] == ["p0001"]
    # and when the current can't be used, what parsed leniently survives the fallback
    doc["current"]["trio_sha"] = "000000000000"
    write_document(tmp_path, doc)
    looks = load(tmp_path)
    assert looks.source == "approved" and looks.problem
    assert looks.file.seq == 7 and looks.file.repaints == {GOLDEN: 1792700000.0}
    assert [h.id for h in looks.file.history] == ["p0001"]


@pytest.mark.parametrize("seq", ["x", None, -4, True, 1])
def test_a_missing_or_garbled_seq_never_reissues_an_id(monkeypatch, tmp_path, seq):
    """B1-5: ids are never reissued (expected_live depends on it): a seq that is missing, garbled or below the ids
    the file already uses is raised to the highest of them, and the next publish is p0004."""
    doc = publication_document(PINK)
    doc["current"]["id"] = "p0003"
    doc["history"] = [{**doc["history"][0], "id": f"p000{n}"} for n in (3, 2, 1)]
    if seq is None:
        doc.pop("seq")
    else:
        doc["seq"] = seq
    write_document(tmp_path, doc)
    looks = load(tmp_path)
    assert looks.source == "published" and looks.file.seq == 3
    install_no_threads(monkeypatch)

    async def main():
        w = await show(data=tmp_path)
        async with client_of(w) as c:
            vid = await save(c, {"lowcountry-calm": brighter("lowcountry-calm")})
            r = await publish(c, vid)
        ids = [h.id for h in w.looks.file.history]
        await w.close()
        return r.status_code, r.text, r.json()["report"]["id"] if r.status_code == 200 else None, ids

    status, text, new_id, ids = run_virtual(main)
    assert status == 200, text
    assert new_id == "p0004" and len(ids) == len(set(ids))


LENIENT = {  # what load_live_looks keeps leniently (the show runs the publication): the preflight must agree
    "a bad history row": {"history_row": {"id": "nope", "kind": "version"}},
    "a bad repaints entry": {"repaints": {GOLDEN: 1792700000.0, "bad": "x"}},
    "a bad previous": {"previous": {"id": "nope"}},
    "201 history rows": {"rows": 201},
    "a negative seq": {"seq": -1},
}


def _lenient(kind: dict) -> dict:
    doc = publication_document(PINK)
    if "history_row" in kind:
        doc["history"].insert(0, kind["history_row"])
    if "repaints" in kind:
        doc["repaints"] = kind["repaints"]
    if "previous" in kind:
        doc["previous"] = kind["previous"]
    if "rows" in kind:
        doc["history"] = [{**doc["history"][0], "id": f"p{n:04d}"} for n in range(kind["rows"], 0, -1)]
        doc["current"]["id"] = f"p{kind['rows']:04d}"
    if "seq" in kind:
        doc["seq"] = kind["seq"]
    return doc


@pytest.mark.parametrize("case", [*CAUSES, *LENIENT])
def test_the_preflight_says_exactly_what_the_load_makes_of_the_file(tmp_path, case):
    """B1-1: the preflight is the show's own load (read only), so it never calls a usable file unusable (or the
    reverse), and its reason is the one /ops will show."""
    document = CAUSES[case] if case in CAUSES else _lenient(LENIENT[case])
    path = tmp_path / "overrides" / L.LOOKS_FILE
    path.parent.mkdir(parents=True)
    if isinstance(document, bytes):
        path.write_bytes(document)
    else:
        write_document(tmp_path, document)
    before = path.read_bytes()
    line = L.preflight_line(CONFIG_DIR, tmp_path, SETTINGS)
    looks = load(tmp_path)
    assert (line is None) == (looks.problem is None), (line, looks.problem)
    if case in LENIENT:
        assert line is None and looks.source == "published"
    else:
        assert (
            line
            == f"{L.LOOKS_PATH} couldn't be used ({looks.problem}); the show would run the approved looks"
        )
    assert path.read_bytes() == before


def test_the_preflight_prints_ok_for_a_file_with_a_bad_history_row(tmp_path):
    import io

    from conductor import preflight

    write_document(tmp_path, _lenient(LENIENT["a bad history row"]))
    out, err = io.StringIO(), io.StringIO()
    code = preflight.run(env={"CONDUCTOR_DATA_DIR": str(tmp_path), "CONDUCTOR_MODE": "sim"}, out=out, err=err)
    text = out.getvalue() + err.getvalue()
    assert code == 0 and "looks      ok (overrides/show-looks.yaml loads)" in text, text


# ---- equal to git, and git changed --------------------------------------------------------------------------------------
def _config_copy(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    return config


def test_a_published_trio_equal_to_git_is_followed_as_git_and_a_later_git_change_is_used(tmp_path):
    config = _config_copy(tmp_path)
    data = tmp_path / "data"
    write_document(data, publication_document(GIT, name="Client Oct 22 v3"))
    looks = load(data, config_dir=config)
    assert looks.source == "approved" and looks.equals_approved and looks.problem is None
    written = yaml.safe_load((data / "overrides" / L.LOOKS_FILE).read_text())
    assert written["current"]["kind"] == "approved" and written["current"]["name"] == L.IN_GIT_NAME
    assert written["current"]["trio"] is None and written["previous"]["id"] == "p0001"
    assert [h["outcome"] for h in written["history"]] == ["in git", "replaced"]
    assert list((data / "overrides" / "history").glob("show-looks-*.yaml"))  # the replaced file was kept
    # the next deploy edits git: the restart uses it (nothing masks it)
    scenes = (config / "scenes.yaml").read_text()
    (config / "scenes.yaml").write_text(
        scenes.replace('mood: "Let the evening glow"', 'mood: "Let the evening linger"')
    )
    from conductor.scenes import load_scenes

    load_scenes.cache_clear()  # a restart is a new process (the loader caches by directory)
    again = load(data, config_dir=config)
    assert again.source == "approved" and not again.equals_approved
    assert again.trio.scenes.get(GOLDEN).mood == "Let the evening linger"


def test_a_failed_in_git_write_still_follows_git(tmp_path, monkeypatch):
    write_document(tmp_path, publication_document(GIT))

    def refuse(*_a, **_k):
        raise OSError("read-only volume")

    monkeypatch.setattr(L, "write_looks", refuse)
    looks = load(tmp_path)
    assert looks.source == "approved" and looks.equals_approved and looks.file.current.kind == "approved"


def test_a_published_look_masking_a_changed_git_warns_on_ops(tmp_path):
    write_document(tmp_path, publication_document(PINK, base_config_sha="0123456789ab"))
    services = services_for(tmp_path)
    looks = L.build_live_looks(services)
    assert looks.source == "published" and looks.git_changed
    assert board_keys(services) == {L.GIT_KEY}
    [line] = L.runtime_problem_lines(services)
    assert line.startswith(
        "Published looks: “Client Oct 22 v3 – warmer sunset” is live and masks config/ changes"
    )
    assert line.endswith("Revert to approved or republish.")


# ---- the trio round trip and the module boundary ---------------------------------------------------------------------------
@pytest.mark.parametrize("trio", [GIT, PINK], ids=["git", "patched"])
def test_the_modes_dump_round_trips_through_parse_modes(trio):
    again = parse_modes(trio.modes.model_dump(mode="json"), recipes=trio.recipes, canopy=CANOPY, path="test")
    assert again.model_dump() == trio.modes.model_dump()
    assert L.trio_sha(L.validate_trio(trio.dump(), git=GIT, settings=SETTINGS, canopy=CANOPY)) == L.trio_sha(
        trio
    )


def test_live_py_imports_nothing_from_the_design_lab():
    source = Path(L.__file__).read_text(encoding="utf-8")
    names = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert names and not [n for n in names if n.startswith("conductor.design_lab")]


def test_write_looks_keeps_a_history_copy_of_the_file_it_replaces_and_prunes_to_fifty(tmp_path):
    looks = load(tmp_path)
    history = tmp_path / "overrides" / "history"
    assert L.write_looks(looks, L.ShowLooksFile(seq=1), now=1.0) is None  # nothing replaced
    assert not history.exists()
    first = (tmp_path / "overrides" / L.LOOKS_FILE).read_bytes()
    for i in range(55):
        old = L.write_looks(looks, L.ShowLooksFile(seq=2 + i), now=100.0 + i)
        assert old is not None
    copies = sorted(history.glob("show-looks-*.yaml"))
    assert len(copies) == L.HISTORY_COPIES
    assert first not in [p.read_bytes() for p in copies]  # the oldest copies were pruned
    assert (tmp_path / "overrides" / L.LOOKS_FILE).stat().st_mode & 0o777 == 0o600
    assert looks.file.seq == 56


def test_the_app_starts_over_an_unusable_file_and_leaves_it_alone(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from conductor.app import app

    for key in (
        "CONDUCTOR_BASIC_AUTH",
        "HUBSPACE_LIVE",
        "CONDUCTOR_MODE",
        "RAILWAY_ENVIRONMENT_NAME",
        "CONDUCTOR_ENGINE",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CONDUCTOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONDUCTOR_SIM_SPEED", "fast")
    monkeypatch.setenv("CONDUCTOR_AUTH", "off")
    path = write_document(tmp_path / "data", CAUSES["an alias bomb"])
    before = path.read_bytes()
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/kiosk/panel").status_code == 200
        services = client.app.state.services
        assert services.extras[L.LOOKS_KEY].source == "approved"
        from conductor.routes.devices import runtime_problems

        assert [p for p in runtime_problems(services) if p.startswith(L.ALERT_PREFIX)]  # the /ops line stands
    assert path.read_bytes() == before


def test_a_publish_clears_the_fallback_line_and_alert(monkeypatch):
    install_no_threads(monkeypatch)

    async def main():
        def prepare(services):
            write_document(services.data, CAUSES["trio_sha mismatch"])

        w = await show(prepare=prepare)
        from conductor.routes.devices import runtime_problems

        before = [p for p in runtime_problems(w.services) if p.startswith(L.ALERT_PREFIX)]
        keys_before = {a.key for a in w.alerts()}
        async with client_of(w) as c:
            vid = await save(c, {"lowcountry-calm": brighter("lowcountry-calm")})
            r = await publish(c, vid)
        after = [p for p in runtime_problems(w.services) if p.startswith(L.ALERT_PREFIX)]
        keys_after = {a.key for a in w.alerts()}
        await w.close()
        return before, keys_before, r.status_code, r.text, after, keys_after

    before, keys_before, status, text, after, keys_after = run_virtual(main)
    assert len(before) == 1 and L.LOAD_KEY in keys_before
    assert status == 200, text
    assert after == [] and L.LOAD_KEY not in keys_after


def test_the_preflight_line_reads_without_writing(tmp_path):
    """For conductor.preflight (canopy-mac install runs it before the restart): the fallback's reason, or None; it
    never writes, not even the equals-git rewrite the show's own start makes."""
    assert L.preflight_line(CONFIG_DIR, None, SETTINGS) is None
    assert L.preflight_line(CONFIG_DIR, tmp_path, SETTINGS) is None  # no file
    path = write_document(tmp_path, publication_document(PINK))
    assert L.preflight_line(CONFIG_DIR, tmp_path, SETTINGS) is None
    write_document(tmp_path, publication_document(GIT))
    before = path.read_bytes()
    assert L.preflight_line(CONFIG_DIR, tmp_path, SETTINGS) is None
    assert path.read_bytes() == before and not (tmp_path / "overrides" / "history").exists()
    write_document(tmp_path, CAUSES["trio_sha mismatch"])
    line = L.preflight_line(CONFIG_DIR, tmp_path, SETTINGS)
    assert line.startswith("overrides/show-looks.yaml couldn't be used (") and "approved looks" in line


def test_conductor_preflight_names_the_published_looks_and_never_fails_or_writes_for_them(tmp_path):
    """`canopy-mac install` runs conductor.preflight before the restart (D67): it names what the new code makes of
    the published looks. A file it can't use is named, not fatal (the show would start on git's looks with an /ops
    alert); it never writes, not even the equals-git rewrite."""
    import io

    from conductor import preflight

    def run() -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        code = preflight.run(
            env={"CONDUCTOR_DATA_DIR": str(tmp_path), "CONDUCTOR_MODE": "sim"}, out=out, err=err
        )
        return code, out.getvalue() + err.getvalue()

    code, text = run()
    assert code == 0 and "looks      ok (nothing published: the approved looks)" in text
    path = write_document(tmp_path, publication_document(PINK))
    code, text = run()
    assert code == 0 and "looks      ok (overrides/show-looks.yaml loads)" in text
    write_document(tmp_path, CAUSES["trio_sha mismatch"])
    before = path.read_bytes()
    code, text = run()
    assert code == 0 and "preflight  ok" in text, text
    assert "looks      overrides/show-looks.yaml couldn't be used (" in text
    assert path.read_bytes() == before and not (tmp_path / "overrides" / "history").exists()
