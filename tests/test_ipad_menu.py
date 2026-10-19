"""The iPad's scene menu (D76): `kiosk.hidden_scenes` and its never-fail ops overlay (overrides/settings.yaml), the
menu over the live catalog, the /ops switch's write path and its "Needs attention" lines, the conductor.yaml fold,
the review cards' thumbnails, and the guest panel that serves them.

Proves (config and conductor/ipad_menu.py): git's list is validated and can't hide the idle scene; the overlay
replaces it, never fails a start and is written atomically with a history copy; ids that hide nothing are named and
dropped by the next switch; a failed save changes nothing; the switch's words say what changed; the fold is a
`git apply`-able hunk; the preflight reads the overlay without writing; a card's picture is the basis it is given.

Proves (GET /api/kiosk/panel and the kiosk routes, over the 18-scene review fixture): the panel lists the visible
scenes with their group, and — only in the review menu — a thumbnail at the tier the engine would use now, computed
once per published look and basis; a thumbnail that can't be drawn is none, never a failed panel; a hidden scene is
refused calmly on both choose routes and is still named while it is showing. Nothing touches the network.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_kiosk_routes import FakeEngine

from conductor import guest_panel as GP
from conductor import ipad_menu as IM
from conductor.canopy_view import canopy_geometry, load_canopy
from conductor.config import (
    CONFIG_DIR,
    SETTINGS_OVERLAY_HEADER,
    SETTINGS_OVERLAY_KEY,
    SETTINGS_OVERLAY_PATH,
    ConductorSettings,
    ConfigError,
    KioskSettings,
    SettingsOverlayState,
    apply_settings_overlay,
    load_settings,
    load_settings_overlay,
    save_settings_overlay,
)
from conductor.engine_api import ModePlan, Tier
from conductor.looks import CompileContext
from conductor.looks.live import git_trio, install_looks
from conductor.routes import kiosk as K
from conductor.routes.design import mode_design
from conductor.scenes import load_scenes
from tests.review_fixture import APPROVED, OPTION_IDS, REVIEW_IDS, install_review, review_scenes, review_trio

ROOT = Path(__file__).resolve().parent.parent
CALM = "lowcountry-calm"
GOLDEN = "golden-hour"


def _kiosk(**kw) -> KioskSettings:
    """A KioskSettings with `kw` set in place (as the overlay and the switch do: no validation)."""
    kiosk = load_settings().kiosk.model_copy(deep=True)
    for k, v in kw.items():
        setattr(kiosk, k, v)
    return kiosk


def _snapshot(data: Path) -> dict[str, str]:
    return {
        p.relative_to(data).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(data.rglob("*"))
        if p.is_file()
    }


def _write_overlay(data: Path, text: str | bytes) -> Path:
    path = data / SETTINGS_OVERLAY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode() if isinstance(text, str) else text)
    return path


def _services(tmp_path: Path, overlay: str | None = None):
    from conductor.services import build_services

    data = tmp_path / "data"
    if overlay is not None:
        _write_overlay(data, overlay)
    return build_services({"CONDUCTOR_DATA_DIR": str(data), "CONDUCTOR_MODE": "sim"})


def _ops_events(services) -> list[dict]:
    return [e for e in services.telemetry.ring if e["kind"] == "ops"]


class ReviewEngine(FakeEngine):
    """tests/test_kiosk_routes.py's FakeEngine over the review trio: it knows all 18 ids, and its state names an
    option by its own id and label (FakeEngine's label lookup reads modes.yaml, which has the bases only)."""

    def __init__(self, clock, trio, **kw) -> None:
        super().__init__(clock, known=set(trio.modes.modes), **kw)
        self.trio = trio

    def state(self):
        real = self.mode
        self.mode = self.trio.scenes.get(real).group if real in self.trio.modes.modes else real
        try:
            st = super().state()
        finally:
            self.mode = real
        return dataclasses.replace(st, mode=real, label=self.trio.modes.modes[real].label)

    def invalidate_t1_plans(self) -> None:  # install_looks calls it (live.py)
        pass


class TierEngine(ReviewEngine):
    """A ReviewEngine whose `compile_best` answers the tier in `tiers` (T3 by default; an Exception raises), and
    records every id it was asked to compile."""

    def __init__(self, clock, trio, tiers=None, **kw) -> None:
        super().__init__(clock, trio, **kw)
        self.tiers: dict = dict(tiers or {})
        self.compiled: list[str] = []

    def compile_best(self, mode_id: str):
        self.compiled.append(mode_id)
        tier = self.tiers.get(mode_id, Tier.T3)
        if isinstance(tier, Exception):
            raise tier
        return ModePlan(mode_id, self.trio.modes.modes[mode_id].label, tier, "", False, {}), ""


def counted(monkeypatch) -> list[tuple[str, bool]]:
    """Every `routes.design.mode_design` call as (mode id, heads). A counter, never a raise: catalog_thumbs and the
    panel swallow exceptions, so a raise would pass silently."""
    from conductor.routes import design as DS

    calls: list[tuple[str, bool]] = []
    real = DS.mode_design

    def wrapped(mode_id, ctx, **kw):
        calls.append((mode_id, kw.get("heads", True)))
        return real(mode_id, ctx, **kw)

    monkeypatch.setattr(DS, "mode_design", wrapped)
    return calls


def _client(monkeypatch, tmp_path, overlay: str | None = None, **extra) -> TestClient:
    """The real app (engine off, auth off) on tmp_path/data, with `overlay` as overrides/settings.yaml before the
    start. Use as a context manager; a second one on the same tmp_path is a restart."""
    from conftest import _env

    from conductor.app import app

    if overlay is not None:
        _write_overlay(tmp_path / "data", overlay)
    _env(monkeypatch, tmp_path, CONDUCTOR_AUTH="off", **extra)
    return TestClient(app)


def _svc(client: TestClient):
    return client.app.state.services


def _panel(client: TestClient) -> dict:
    r = client.get("/api/kiosk/panel")
    assert r.status_code == 200, r.text
    return r.json()


def _engine(client: TestClient, trio, cls=ReviewEngine, **kw):
    services = _svc(client)
    engine = cls(services.clock, trio, **kw)
    services.extras["canopy"] = engine
    return engine


class ShowingEngine:
    """Just enough engine for the switch's "still showing" clause (and install_looks' T1 invalidation)."""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def state(self):
        return SimpleNamespace(mode=self.mode)

    def invalidate_t1_plans(self) -> None:
        pass


# ---- kiosk.hidden_scenes in git -----------------------------------------------------------------------------------
def test_kiosk_hidden_scenes_is_validated_and_the_idle_scene_cant_be_hidden():
    with pytest.raises(ValidationError, match=r"idle scene \(kiosk\.idle_mode\)"):
        KioskSettings(hidden_scenes=[CALM])
    with pytest.raises(ValidationError, match=r"idle scene \(kiosk\.idle_scene\)"):
        KioskSettings(idle_scene="goodnight-glow", hidden_scenes=["goodnight-glow"])
    with pytest.raises(ValidationError, match="isn't a scene id"):
        KioskSettings(hidden_scenes=["Golden Hour"])
    with pytest.raises(ValidationError, match="twice"):
        KioskSettings(hidden_scenes=["a1", "a1"])
    with pytest.raises(ValidationError):
        KioskSettings(hidden_scenes=[f"s{i:02d}" for i in range(65)])
    assert KioskSettings(hidden_scenes=[GOLDEN, "golden-hour-2"]).hidden_scenes == [GOLDEN, "golden-hour-2"]
    assert load_settings().kiosk.hidden_scenes == []


def test_conductor_yaml_carries_the_list_as_one_foldable_line():
    text = (CONFIG_DIR / "conductor.yaml").read_text(encoding="utf-8")
    assert [line for line in text.splitlines() if "hidden_scenes:" in line] == ["  hidden_scenes: []"]
    assert yaml.safe_load(text)["kiosk"]["hidden_scenes"] == []


def test_a_git_list_that_hides_the_idle_scene_fails_the_start_naming_the_file(tmp_path):
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    path = config / "conductor.yaml"
    path.write_text(path.read_text().replace("  hidden_scenes: []", f"  hidden_scenes: [{CALM}]"))
    with pytest.raises(ConfigError, match=r"conductor\.yaml.*\n.*kiosk.*idle scene \(kiosk\.idle_mode\)"):
        load_settings(config)


# ---- the settings overlay -------------------------------------------------------------------------------------------
OVERLAY_CASES = {
    "missing": (None, None, None),
    "applied": ("kiosk: {hidden_scenes: [golden-hour-2]}\n", ["golden-hour-2"], None),
    "no list": ("kiosk: {}\n", [GOLDEN], None),
    "bad yaml": ("kiosk: [\n", [GOLDEN], "it isn't valid YAML"),
    "unknown key": ("kiosk: {idle_mode: x}\n", [GOLDEN], "idle_mode"),
    "too big": (
        "kiosk: {hidden_scenes: []}\n" + "#" * 70_000 + "\n",
        [GOLDEN],
        "bytes, over the 65536-byte limit",
    ),
    "a list": ("- a\n", [GOLDEN], "it isn't a mapping with a kiosk section"),
    "empty": ("", [GOLDEN], "it isn't a mapping with a kiosk section"),
    "not an id": ("kiosk: {hidden_scenes: [Golden Hour]}\n", [GOLDEN], "isn't a scene id"),
    "not utf-8": (b"kiosk: {hidden_scenes: [\xff\xfe]}\n", [GOLDEN], "it isn't valid YAML"),
}


@pytest.mark.parametrize("case", list(OVERLAY_CASES))
def test_the_settings_overlay_replaces_gits_list_and_never_fails_the_start(tmp_path, case):
    text, in_force, problem = OVERLAY_CASES[case]
    data = tmp_path / "data"
    path = _write_overlay(data, text) if text is not None else None
    before = path.read_bytes() if path else None
    state = load_settings_overlay(data)  # never raises
    assert state.path == data / SETTINGS_OVERLAY_PATH
    settings = load_settings()
    settings.kiosk.hidden_scenes = [GOLDEN]  # git's list, say
    kiosk = settings.kiosk
    apply_settings_overlay(settings, state)
    assert settings.kiosk is kiosk  # in place: the engine keeps this object
    if case == "missing":
        assert state.overlay is None and state.problem is None and settings.kiosk.hidden_scenes == [GOLDEN]
        return
    assert settings.kiosk.hidden_scenes == in_force
    if problem is None:
        assert state.problem is None and state.overlay is not None
    else:
        assert state.overlay is None and problem in (state.problem or ""), state.problem
    assert path is not None and path.read_bytes() == before  # the file is left as it was


def test_an_overlay_that_cant_be_read_is_a_problem_never_a_raise(tmp_path):
    data = tmp_path / "data"
    (data / SETTINGS_OVERLAY_PATH).mkdir(parents=True)  # a directory where the file should be
    state = load_settings_overlay(data)
    assert state.overlay is None and (state.problem or "").startswith("it can't be read (")
    assert load_settings_overlay(None) == SettingsOverlayState(None)


def test_saving_the_list_is_atomic_versioned_and_clears_the_problem(tmp_path):
    data = tmp_path / "data"
    path = _write_overlay(data, "kiosk: [\n")
    state = load_settings_overlay(data)
    assert state.problem == "it isn't valid YAML"
    now = 1_792_000_000.25
    save_settings_overlay(state, [GOLDEN], now=now)
    save_settings_overlay(state, ["golden-hour-2"], now=now + 0.5)  # the same second
    text = path.read_text(encoding="utf-8")
    assert text.startswith(SETTINGS_OVERLAY_HEADER)
    assert SETTINGS_OVERLAY_HEADER.startswith("# The iPad's scene menu (D76)") and text.count("\n# ") == 0
    assert yaml.safe_load(text) == {"kiosk": {"hidden_scenes": ["golden-hour-2"]}}
    assert os.stat(path).st_mode & 0o777 == 0o600
    history = sorted((data / "overrides" / "history").glob("settings-*.yaml"))
    assert [p.name for p in history] == ["settings-20261014T174640Z-2.yaml", "settings-20261014T174640Z.yaml"]
    assert all(os.stat(p).st_mode & 0o777 == 0o600 for p in history)
    assert yaml.safe_load(history[0].read_text()) == {"kiosk": {"hidden_scenes": ["golden-hour-2"]}}
    assert state.problem is None and state.overlay is not None
    reloaded = load_settings_overlay(data)
    assert reloaded.problem is None and reloaded.overlay.kiosk.hidden_scenes == ["golden-hour-2"]
    with pytest.raises(ConfigError, match="No data directory"):
        save_settings_overlay(SettingsOverlayState(None), [GOLDEN], now=now)
    with pytest.raises(ConfigError, match="isn't a scene id"):
        save_settings_overlay(state, ["Golden Hour"], now=now)
    assert yaml.safe_load(path.read_text()) == {"kiosk": {"hidden_scenes": ["golden-hour-2"]}}


def test_a_start_puts_the_overlay_in_force_in_place_and_keeps_its_state(tmp_path):
    services = _services(tmp_path, "kiosk: {hidden_scenes: [golden-hour-2, disco-inferno]}\n")
    assert services.settings.kiosk.hidden_scenes == ["golden-hour-2", "disco-inferno"]
    state = services.extras[SETTINGS_OVERLAY_KEY]
    assert state.problem is None and state.path == Path(services.data) / SETTINGS_OVERLAY_PATH
    assert IM.settings_overlay_of(services) is state
    broken = _services(tmp_path / "b", "kiosk: {hidden_scenes: [Golden Hour]}\n")  # never a failed start
    assert (
        broken.settings.kiosk.hidden_scenes == []
        and "isn't a scene id" in IM.settings_overlay_of(broken).problem
    )


# ---- the menu --------------------------------------------------------------------------------------------------------
def test_the_menu_hides_listed_scenes_keeps_the_idle_one_and_names_what_hides_nothing():
    catalog = review_scenes(load_scenes())
    m = IM.menu(catalog, _kiosk(hidden_scenes=["golden-hour-2", CALM, "disco-inferno"]))
    assert m.hidden == ("golden-hour-2",)
    assert m.ignored_idle == (CALM,) and m.ignored_unknown == ("disco-inferno",)
    assert m.ignored == ("disco-inferno", CALM)
    assert [s.id for s in m.visible.scenes] == [x for x in REVIEW_IDS if x != "golden-hour-2"]
    assert [s.id for s in m.catalog.scenes] == REVIEW_IDS
    assert m.review is True and IM.GRID_MAX == 6
    git = IM.menu(load_scenes(), load_settings().kiosk)
    assert [s.id for s in git.visible.scenes] == APPROVED and git.hidden == () and git.review is False


def test_a_visible_option_of_a_hidden_base_keeps_its_group():
    m = IM.menu(review_scenes(load_scenes()), _kiosk(hidden_scenes=[GOLDEN]))
    assert GOLDEN not in [s.id for s in m.visible.scenes]
    option = m.visible.get("golden-hour-2")
    assert option.group == GOLDEN and option.option_of == GOLDEN
    assert [s.id for s in m.visible.options_of(GOLDEN)] == ["golden-hour-2", "golden-hour-3"]


def test_six_visible_is_the_grid_seven_is_the_review_menu():
    catalog = review_scenes(load_scenes())
    six = IM.menu(catalog, _kiosk(hidden_scenes=list(OPTION_IDS)))
    assert len(six.visible.scenes) == 6 and six.review is False
    seven = IM.menu(catalog, _kiosk(hidden_scenes=[x for x in OPTION_IDS if x != "golden-hour-2"]))
    assert len(seven.visible.scenes) == 7 and seven.review is True


def test_the_idle_scene_settings_name_themselves():
    assert IM.idle_ids(_kiosk()) == {CALM: "kiosk.idle_mode"}
    assert IM.idle_ids(_kiosk(idle_scene="goodnight-glow")) == {
        CALM: "kiosk.idle_mode",
        "goodnight-glow": "kiosk.idle_scene",
    }
    assert IM.idle_ids(_kiosk(idle_scene=CALM)) == {CALM: "kiosk.idle_mode"}


def test_the_live_menu_follows_the_installed_looks(tmp_path):
    services = _services(tmp_path)
    git = IM.live_menu(services)
    assert [s.id for s in git.catalog.scenes] == APPROVED and git.trio is not None
    trio = install_review(services, hide=["golden-hour-3"])
    m = IM.live_menu(services)
    assert [s.id for s in m.catalog.scenes] == REVIEW_IDS and m.hidden == ("golden-hour-3",)
    assert m.generation == git.generation + 1 and m.trio.dump() == trio.dump()
    install_review(services)  # hide=None keeps the list in force
    assert services.settings.kiosk.hidden_scenes == ["golden-hour-3"]


def test_the_ops_body_lists_every_scene_with_its_switch(tmp_path):
    services = _services(tmp_path)
    install_review(services)
    body = IM.menu_json(services)
    assert [s["id"] for s in body["scenes"]] == REVIEW_IDS
    assert all(s["on_ipad"] is True for s in body["scenes"])
    assert [s["id"] for s in body["scenes"] if s["idle"]] == [CALM]
    gh2 = next(s for s in body["scenes"] if s["id"] == "golden-hour-2")
    assert gh2 == {
        "id": "golden-hour-2",
        "name": "Golden Hour #2",
        "group": GOLDEN,
        "option_of": GOLDEN,
        "on_ipad": True,
        "idle": False,
    }
    assert {k: body[k] for k in ("visible", "total", "grid_max", "review", "notes", "file", "writable")} == {
        "visible": 18,
        "total": 18,
        "grid_max": 6,
        "review": True,
        "notes": [],
        "file": "overrides/settings.yaml",
        "writable": True,
    }


# ---- the switch --------------------------------------------------------------------------------------------------------
def test_the_switch_hides_and_shows_a_scene_and_says_so(tmp_path):
    services = _services(tmp_path)
    install_review(services)
    kiosk = services.settings.kiosk
    path = Path(services.data) / SETTINGS_OVERLAY_PATH
    change = IM.set_on_ipad(services, "golden-hour-2", False)
    assert change.changed is True
    assert change.message == (
        "Golden Hour #2 is off the iPad: guests no longer see its card. Ops can still choose it here and the "
        "Design Lab still edits it."
    )
    assert services.settings.kiosk is kiosk and kiosk.hidden_scenes == ["golden-hour-2"]
    assert yaml.safe_load(path.read_text()) == {"kiosk": {"hidden_scenes": ["golden-hour-2"]}}
    assert "golden-hour-2" not in [s.id for s in IM.live_menu(services).visible.scenes]
    events = _ops_events(services)
    assert len(events) == 1 and events[0]["action"] == "ipad_scenes"
    assert (events[0]["scene"], events[0]["show"], events[0]["hidden"]) == (
        "golden-hour-2",
        False,
        ["golden-hour-2"],
    )
    again = IM.set_on_ipad(services, "golden-hour-2", False)
    assert (
        again == IM.MenuChange(False, "Golden Hour #2 is already off the iPad.")
        and len(_ops_events(services)) == 1
    )
    back = IM.set_on_ipad(services, "golden-hour-2", True)
    assert back == IM.MenuChange(True, "Golden Hour #2 is back on the iPad.")
    assert kiosk.hidden_scenes == [] and yaml.safe_load(path.read_text()) == {"kiosk": {"hidden_scenes": []}}
    assert IM.set_on_ipad(services, GOLDEN, True) == IM.MenuChange(
        False, "Golden Hour is already on the iPad."
    )


def test_two_switches_keep_catalog_order(tmp_path):
    services = _services(tmp_path)
    install_review(services)
    IM.set_on_ipad(services, "golden-hour-2", False)
    IM.set_on_ipad(services, "rainbow-row-sunset-3", False)
    assert services.settings.kiosk.hidden_scenes == ["rainbow-row-sunset-3", "golden-hour-2"]
    path = Path(services.data) / SETTINGS_OVERLAY_PATH
    assert yaml.safe_load(path.read_text())["kiosk"]["hidden_scenes"] == [
        "rainbow-row-sunset-3",
        "golden-hour-2",
    ]


def test_the_switch_says_when_the_menu_changes_shape_and_when_the_scene_is_showing(tmp_path):
    services = _services(tmp_path)
    install_review(services, hide=[x for x in OPTION_IDS if x != "golden-hour-2"])  # 7 visible
    hid = IM.set_on_ipad(services, "golden-hour-2", False)
    assert hid.message.endswith(" The iPad is back to its 3 × 2 grid.")
    shown = IM.set_on_ipad(services, "golden-hour-2", True)
    assert shown.message.endswith(" The iPad now shows the review menu (more than six scenes).")
    services.extras["canopy"] = ShowingEngine("golden-hour-2")
    showing = IM.set_on_ipad(services, "golden-hour-2", False)
    assert " It is still showing; guests can't choose it again from the iPad." in showing.message
    assert "until" not in showing.message
    assert showing.message.index("still showing") < showing.message.index("3 × 2")


def test_the_idle_scene_cant_be_hidden_and_an_unknown_one_is_404(tmp_path):
    services = _services(tmp_path)
    path = Path(services.data) / SETTINGS_OVERLAY_PATH
    with pytest.raises(IM.MenuRefused) as idle:
        IM.set_on_ipad(services, CALM, False)
    assert idle.value.status == 409
    assert (
        idle.value.message
        == "Lowcountry Calm is the idle scene (kiosk.idle_mode), so the iPad always offers it."
    )
    services.settings.kiosk.idle_scene = "goodnight-glow"
    with pytest.raises(IM.MenuRefused) as reset:
        IM.set_on_ipad(services, "goodnight-glow", False)
    assert reset.value.status == 409 and "(kiosk.idle_scene)" in reset.value.message
    with pytest.raises(IM.MenuRefused) as unknown:
        IM.set_on_ipad(services, "disco-inferno", False)
    assert (
        unknown.value.status == 404 and unknown.value.message == "There is no scene called 'disco-inferno'."
    )
    assert not path.exists() and _ops_events(services) == []
    assert IM.set_on_ipad(services, CALM, True).changed is False  # showing it is always fine


def test_a_save_that_fails_changes_nothing(tmp_path, monkeypatch):
    services = _services(tmp_path, "kiosk: {hidden_scenes: [golden-hour]}\n")
    path = Path(services.data) / SETTINGS_OVERLAY_PATH
    before = path.read_bytes()

    def disk_full(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(IM, "save_settings_overlay", disk_full)
    with pytest.raises(IM.MenuRefused) as refused:
        IM.set_on_ipad(services, "coastal-tides", False)
    assert refused.value.status == 503
    assert refused.value.message == "The iPad's scene list couldn't be saved (disk full); nothing changed."
    assert services.settings.kiosk.hidden_scenes == [GOLDEN]
    assert "coastal-tides" in [s.id for s in IM.live_menu(services).visible.scenes]
    assert path.read_bytes() == before and _ops_events(services) == []


def test_without_a_data_directory_the_switch_is_refused_with_the_reason(tmp_path):
    services = _services(tmp_path)
    services.extras[SETTINGS_OVERLAY_KEY] = SettingsOverlayState(None)
    with pytest.raises(IM.MenuRefused) as refused:
        IM.set_on_ipad(services, GOLDEN, False)
    assert refused.value.status == 409 and "No data directory is configured" in refused.value.message
    assert services.settings.kiosk.hidden_scenes == [] and IM.menu_json(services)["writable"] is False


# ---- the /ops lines ------------------------------------------------------------------------------------------------------
def test_an_unusable_overlay_is_a_live_ops_line_and_the_next_switch_writes_it_again(tmp_path):
    services = _services(tmp_path, "kiosk: {hidden_scenes: [golden-hour]\n")
    path = Path(services.data) / SETTINGS_OVERLAY_PATH
    before = path.read_bytes()
    assert services.settings.kiosk.hidden_scenes == [] and len(IM.live_menu(services).visible.scenes) == 6
    lines = IM.problem_lines(services)
    assert lines == [
        (
            "The iPad's scene list (overrides/settings.yaml) couldn't be used (it isn't valid YAML), so the iPad "
            "offers every scene config/conductor.yaml doesn't hide. Flip a “Show on the iPad” switch on /ops to "
            "write it again."
        )
    ]
    assert path.read_bytes() == before
    IM.set_on_ipad(services, GOLDEN, False)
    assert IM.problem_lines(services) == []
    assert load_settings_overlay(Path(services.data)).problem is None


def test_names_that_hide_nothing_are_an_ops_line_and_the_next_switch_drops_them(tmp_path):
    services = _services(tmp_path, "kiosk: {hidden_scenes: [disco-inferno, lowcountry-calm]}\n")
    install_review(services)  # keeps the overlay's list
    assert IM.problem_lines(services) == [
        (
            "kiosk.hidden_scenes names 'disco-inferno', which isn't a scene now, so it hides nothing; the next "
            "“Show on the iPad” switch on /ops drops it."
        ),
        "kiosk.hidden_scenes names 'lowcountry-calm', the idle scene (kiosk.idle_mode): the iPad offers it anyway.",
    ]
    assert IM.menu_json(services)["notes"] == IM.problem_lines(services)
    services.settings.kiosk.hidden_scenes = ["disco-inferno", "disco-two"]
    assert IM.problem_lines(services) == [
        (
            "kiosk.hidden_scenes names 'disco-inferno', 'disco-two', which aren't scenes now, so they hide "
            "nothing; the next “Show on the iPad” switch on /ops drops them."
        )
    ]
    IM.set_on_ipad(services, "golden-hour-2", False)
    path = Path(services.data) / SETTINGS_OVERLAY_PATH
    assert yaml.safe_load(path.read_text())["kiosk"]["hidden_scenes"] == ["golden-hour-2"]
    assert IM.problem_lines(services) == []


# ---- the conductor.yaml fold ---------------------------------------------------------------------------------------------
def _git_apply(tmp_path: Path, diff: str) -> str:
    work = tmp_path / "apply"
    (work / "config").mkdir(parents=True)
    shutil.copyfile(CONFIG_DIR / "conductor.yaml", work / "config" / "conductor.yaml")
    (work / "fold.diff").write_text(diff, encoding="utf-8")
    subprocess.run(["git", "apply", "fold.diff"], cwd=work, check=True, capture_output=True)
    return (work / "config" / "conductor.yaml").read_text(encoding="utf-8")


def test_the_fold_is_a_git_apply_hunk_for_conductor_yaml(tmp_path):
    fold = IM.conductor_yaml_diff(CONFIG_DIR, [GOLDEN, "golden-hour-2"])
    assert fold.problem is None
    assert fold.diff.startswith("--- a/config/conductor.yaml\n+++ b/config/conductor.yaml\n")
    assert "\n-  hidden_scenes: []\n" in fold.diff
    assert "\n+  hidden_scenes: [golden-hour, golden-hour-2]\n" in fold.diff
    changed = [line for line in fold.diff.splitlines() if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    assert len(changed) == 2
    if shutil.which("git"):
        assert _git_apply(tmp_path, fold.diff) == fold.after
    reread = yaml.safe_load(fold.after)
    assert reread["kiosk"]["hidden_scenes"] == [GOLDEN, "golden-hour-2"]
    KioskSettings.model_validate(reread["kiosk"])
    ConductorSettings.model_validate(reread)
    nothing = IM.conductor_yaml_diff(CONFIG_DIR, [])
    assert nothing == IM.ConductorYamlDiff("", (CONFIG_DIR / "conductor.yaml").read_text(), None)


def test_the_fold_refuses_a_file_it_cant_edit_truthfully(tmp_path):
    text = (CONFIG_DIR / "conductor.yaml").read_text()
    two = tmp_path / "two"
    two.mkdir()
    (two / "conductor.yaml").write_text(
        text.replace("  hidden_scenes: []", "  hidden_scenes: []\n  hidden_scenes: []")
    )
    fold = IM.conductor_yaml_diff(two, [GOLDEN])
    assert (
        fold.diff == ""
        and fold.after is None
        and "no one-line `hidden_scenes: [...]` under kiosk" in fold.problem
    )
    block = tmp_path / "block"
    block.mkdir()
    (block / "conductor.yaml").write_text(
        text.replace("  hidden_scenes: []", "  hidden_scenes:\n    - golden-hour")
    )
    assert "set it by hand" in IM.conductor_yaml_diff(block, [GOLDEN]).problem
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "conductor.yaml").write_text(
        text.replace("  hidden_scenes: []\n", "").replace(
            "module_page:\n", "module_page:\n  hidden_scenes: []\n"
        )
    )
    assert IM.conductor_yaml_diff(elsewhere, [GOLDEN]).problem is not None
    missing = IM.conductor_yaml_diff(tmp_path / "nowhere", [GOLDEN])
    assert missing.diff == "" and missing.problem.startswith("it can't be read (")


# ---- the preflight ----------------------------------------------------------------------------------------------------------
def _preflight(data: Path) -> tuple[int, str, str]:
    from conductor import preflight

    out, err = io.StringIO(), io.StringIO()
    code = preflight.run(env={"CONDUCTOR_DATA_DIR": str(data), "CONDUCTOR_MODE": "sim"}, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_the_preflight_names_the_ipad_menu_truthfully(tmp_path):
    data = tmp_path / "data"
    code, out, err = _preflight(data)
    assert code == 0, err
    assert "overrides/settings.yaml" not in out  # nothing when the file is missing
    _write_overlay(data, "kiosk: {hidden_scenes: [golden-hour, disco-inferno]}\n")
    before = _snapshot(data)
    code, out, err = _preflight(data)
    assert code == 0, err
    assert (
        "settings   overrides/settings.yaml hides 1 scene(s); 1 listed id(s) aren't scenes now (ignored)\n"
        in out
    )
    assert _snapshot(data) == before
    _write_overlay(data, "kiosk: {hidden_scenes: [lowcountry-calm]}\n")
    code, out, _ = _preflight(data)
    assert code == 0 and "hides 0 scene(s); it lists the idle scene, which the iPad offers anyway" in out
    _write_overlay(data, "kiosk: [\n")
    before = _snapshot(data)
    code, out, err = _preflight(data)
    assert code == 0, err
    assert (
        "settings   overrides/settings.yaml couldn't be used (it isn't valid YAML); the iPad would offer every "
        "scene config/conductor.yaml doesn't hide" in out
    )
    assert "preflight  ok" in out and _snapshot(data) == before


# ---- card thumbnails ---------------------------------------------------------------------------------------------------------
HEX12 = re.compile(r"^[0-9a-f]{12}$")


def _hex(rgb) -> str:
    r, g, b = rgb
    return f"#{r:02X}{g:02X}{b:02X}"


def _ctx(trio=None) -> CompileContext:
    trio = trio or git_trio(CONFIG_DIR)
    return CompileContext(
        registry=[],
        capabilities={},
        modes=trio.modes,
        recipes=trio.recipes,
        canopy=load_canopy(str(CONFIG_DIR)),
        config_dir=CONFIG_DIR,
    )


@pytest.mark.parametrize("mode_id", APPROVED)
def test_design_thumb_draws_the_basis_it_is_given(mode_id):
    ctx = _ctx()
    order = [m["pos"] for m in canopy_geometry(ctx.canopy)["modules"]]
    design = mode_design(mode_id, ctx)
    thumb = IM.design_thumb(design, "heads")
    assert thumb["basis"] == "heads" and [m["pos"] for m in thumb["modules"]] == order
    for item in thumb["modules"]:
        want = [_hex(h["rgb"]) for h in design["heads"] if h["module"] == item["pos"]]
        assert len(want) == 30
        if len(set(want)) == 1:
            assert item == {"pos": item["pos"], "color": want[0], "colors": None}
        else:
            assert item["color"] is None and item["colors"] == want
    flat = mode_design(mode_id, ctx, heads=False)
    assert flat["heads"] is None and flat["modules"] == design["modules"]
    modules = IM.design_thumb(flat, "modules")
    assert modules["basis"] == "modules" and [m["pos"] for m in modules["modules"]] == order
    for item, module in zip(modules["modules"], design["modules"], strict=True):
        assert item == {
            "pos": module["module"],
            "color": _hex(module["rgb"]),
            "colors": None,
        }
    for t in (thumb, modules):
        assert HEX12.match(t["sig"])
        text = json.dumps(t)
        for word in ("canopy-r", "sim-", "Module ", "Hubspace", "label"):
            assert word not in text
    assert thumb["sig"] != modules["sig"]
    assert IM.design_thumb(flat, "heads") is None  # no heads drawn: no per-light picture
    assert IM.design_thumb(design, None) is None
    assert IM.design_thumb({**design, "modules": None}, "modules") is None


def test_a_thumbnail_sig_changes_with_one_colour_and_with_the_basis_alone():
    ctx = _ctx()
    design = mode_design(GOLDEN, ctx, heads=False)
    sig = IM.design_thumb(design, "modules")["sig"]
    assert IM.design_thumb(design, "modules")["sig"] == sig
    one = [dict(m) for m in design["modules"]]
    one[4] = {**one[4], "rgb": [1, 2, 3]}
    assert IM.design_thumb({**design, "modules": one}, "modules")["sig"] != sig
    # the same nine colours as heads (each module's 30 heads in its one colour) differ only in the basis
    heads = [
        {"module": m["module"], "x": 0, "y": 0, "rgb": m["rgb"]} for m in design["modules"] for _ in range(30)
    ]
    as_heads = IM.design_thumb({**design, "heads": heads}, "heads")
    assert [m["color"] for m in as_heads["modules"]] == [
        m["color"] for m in IM.design_thumb(design, "modules")["modules"]
    ]
    assert as_heads["sig"] != sig


def test_catalog_thumbs_draws_only_the_ids_asked_and_never_fails(monkeypatch):
    trio = review_trio(git_trio(CONFIG_DIR))
    canopy = load_canopy(str(CONFIG_DIR))
    got = IM.catalog_thumbs(
        trio, canopy, CONFIG_DIR, {GOLDEN: "heads", "golden-hour-2": "modules", CALM: None}
    )
    assert set(got) == {GOLDEN, "golden-hour-2", CALM} and got[CALM] is None
    assert got[GOLDEN] == IM.design_thumb(mode_design(GOLDEN, _ctx(trio)), "heads")
    assert got["golden-hour-2"] == IM.design_thumb(
        mode_design("golden-hour-2", _ctx(trio), heads=False), "modules"
    )
    sigs = {
        sid: t["sig"]
        for sid, t in IM.catalog_thumbs(
            trio, canopy, CONFIG_DIR, dict.fromkeys(REVIEW_IDS, "modules")
        ).items()
    }
    for base in APPROVED:  # each group's three module-level designs differ
        assert len({sigs[base], sigs[f"{base}-2"], sigs[f"{base}-3"]}) == 3, base
    assert IM.catalog_thumbs(trio, canopy, CONFIG_DIR, {"disco-inferno": "modules"}) == {
        "disco-inferno": None
    }

    from conductor.routes import design as DS

    real = DS.mode_design

    def broken(mode_id, ctx, **kw):
        if mode_id == "coastal-tides-2":
            raise RuntimeError("no design")
        return real(mode_id, ctx, **kw)

    monkeypatch.setattr(DS, "mode_design", broken)
    got = IM.catalog_thumbs(
        trio, canopy, CONFIG_DIR, dict.fromkeys(["coastal-tides-2", "coastal-tides"], "modules")
    )
    assert got["coastal-tides-2"] is None and got["coastal-tides"] is not None


def test_thumbnail_wall_time_is_bounded():
    trio = review_trio(git_trio(CONFIG_DIR))
    canopy = load_canopy(str(CONFIG_DIR))
    IM.catalog_thumbs(trio, canopy, CONFIG_DIR, dict.fromkeys(REVIEW_IDS, "heads"))  # warm
    timings = {}
    for basis in ("heads", "modules"):
        start = time.perf_counter()
        thumbs = IM.catalog_thumbs(trio, canopy, CONFIG_DIR, dict.fromkeys(REVIEW_IDS, basis))
        timings[basis] = (time.perf_counter() - start) * 1000
        assert all(t is not None and t["basis"] == basis for t in thumbs.values())
        size = len(json.dumps(list(thumbs.values()), separators=(",", ":")))
        print(f"catalog_thumbs, 18 review scenes, basis {basis}: {timings[basis]:.1f} ms, {size} B of JSON")
    assert timings["heads"] < 500  # the worst case: every card per light (a CI guard, not a target)
    assert timings["modules"] < 100


# ---- the Lab never writes the menu -------------------------------------------------------------------------------------------
def test_the_lab_never_writes_the_ipad_menu():
    """The Lab never hides a scene (/ops does): no Lab source names the switch or the overlay's writer."""
    sources = sorted((ROOT / "conductor" / "design_lab").glob("*.py")) + [
        ROOT / "conductor/routes/design_lab.py"
    ]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for word in (
            "set_on_ipad",
            "save_settings_overlay",
            "SETTINGS_OVERLAY_PATH",
            "SETTINGS_OVERLAY_FILE",
        ):
            assert word not in text, f"{path.name} names {word}"
        # the Lab reads the menu (CatalogJSON / LiveJSON on_ipad) and nothing else of it
        for imported in re.findall(r"from conductor\.ipad_menu import ([^\n]+)", text):
            assert imported.strip() == "live_menu", (path.name, imported)
        assert "import ipad_menu" not in text and "ipad_menu as" not in text, path.name


# ---- "Make this the scene" against the Lab's own checks -------------------------------------------------------------------
def test_a_promoted_draft_passes_the_labs_own_checks(app_client):
    """What promoteDraft leaves (another design's palette, layout, sample and description in a base's draft) applies
    and saves through the Lab's real rules. Git has no options before PR D, so the stand-in is another scene's
    design (rainbow-row-sunset's) promoted into golden-hour, a painted base."""
    from conductor.design_lab import draft as D

    git = git_trio(CONFIG_DIR)
    other = D.seed_draft(git, "rainbow-row-sunset")
    promoted = D.seed_draft(git, GOLDEN).model_copy(
        update={
            "palette": other.palette,
            "layout": other.layout,
            "sample_at": other.sample_at,
            "description": other.description,
            "solid": min(other.solid, len(other.palette) - 1),
        }
    )
    patched = D.apply_drafts(
        git, {GOLDEN: promoted}, canopy=load_canopy(str(CONFIG_DIR)), settings=load_settings()
    )  # raises DraftError on any Lab rule
    keys = [s.color for s in patched.modes.mode(GOLDEN).techniques.modules.sample.canvas.stops]
    # the other scene owns the plain slugs, so the base takes '<base id>-<slug>' keys (draft.py recipe_keys)
    assert keys == [f"{GOLDEN}-{D.slug(c.name)}" for c in other.palette], keys
    assert [(c.name, c.hex) for c in patched.scenes.get(GOLDEN).palette] == [
        (c.name, c.hex) for c in other.palette
    ]
    r = app_client.post("/api/lab/versions", json={"drafts": {GOLDEN: promoted.model_dump(mode="json")}})
    assert r.status_code == 201, r.text


# ---- the review cards' basis ---------------------------------------------------------------------------------------------
PAINTED = ("rainbow-row-sunset", "garden-gathering", "golden-hour")  # the canvas-first bases
MODULES_FIRST = ("lowcountry-calm", "coastal-tides", "goodnight-glow")  # modules- or stepped-first


def test_the_thumb_basis_follows_the_tier_the_engine_would_use():
    trio = review_trio(git_trio(CONFIG_DIR))
    clock = SimpleNamespace(now=lambda: 0.0)
    engine = TierEngine(
        clock, trio, {GOLDEN: Tier.T1, "rainbow-row-sunset": Tier.T2, "garden-gathering": Tier.T3}
    )
    basis = GP.thumb_basis(engine, trio.modes, REVIEW_IDS)
    assert list(basis) == REVIEW_IDS
    assert (basis[GOLDEN], basis["rainbow-row-sunset"], basis["garden-gathering"]) == (
        "heads",
        "heads",
        "modules",
    )
    assert all(basis[b] == "modules" for b in MODULES_FIRST)
    assert all(basis[o] == "modules" for o in OPTION_IDS)
    assert (
        set(engine.compiled) <= set(PAINTED) and engine.compiled
    )  # options and modules-first: never compiled
    engine.tiers[GOLDEN] = Tier.STRAND
    assert GP.thumb_basis(engine, trio.modes, [GOLDEN]) == {GOLDEN: None}  # one whole-strand look: no picture
    engine.tiers[GOLDEN] = RuntimeError("no plan")
    assert GP.thumb_basis(engine, trio.modes, [GOLDEN]) == {GOLDEN: "modules"}
    assert GP.thumb_basis(engine, trio.modes, ["disco-inferno"]) == {"disco-inferno": None}
    compiled = len(engine.compiled)
    assert GP.thumb_basis(None, trio.modes, REVIEW_IDS) == dict.fromkeys(REVIEW_IDS, "modules")
    assert len(engine.compiled) == compiled
    strand = SimpleNamespace(ladder=["strand"], techniques=SimpleNamespace(modules=None))
    assert GP.thumb_basis(engine, SimpleNamespace(modes={"x": strand}), ["x"]) == {"x": None}


def test_the_thumb_basis_follows_the_plan_showing_now():
    """The plan the engine shows now wins over a compile: the same rule as /api/kiosk/modes."""
    trio = review_trio(git_trio(CONFIG_DIR))
    engine = TierEngine(SimpleNamespace(now=lambda: 0.0), trio, {GOLDEN: Tier.T3})
    showing = ModePlan(GOLDEN, "Golden Hour", Tier.T1, "", False, {})
    engine.current_plan = lambda: showing
    assert GP.thumb_basis(engine, trio.modes, [GOLDEN]) == {GOLDEN: "heads"}
    assert engine.compiled == []


def test_on_the_sim_engine_painted_scenes_draw_per_light_and_options_at_module_level(app_client_show):
    services = _svc(app_client_show)
    engine = services.extras.get("canopy")
    assert engine is not None, "the sim engine didn't start"
    trio = install_review(services)
    body = _panel(app_client_show)
    by_id = {s["id"]: s for s in body["scenes"]}
    assert list(by_id) == REVIEW_IDS
    assert all(by_id[o]["thumb"]["basis"] == "modules" for o in OPTION_IDS)
    assert all(by_id[b]["thumb"]["basis"] == "modules" for b in MODULES_FIRST)
    current = K._current_plan(engine)
    for base in PAINTED:
        plan = K._likely_plan(engine, base, current)
        tier = str(plan.tier) if plan is not None else None
        want = "heads" if tier in GP.PER_LIGHT_TIERS else "modules"
        assert by_id[base]["thumb"]["basis"] == want, (base, tier)
    start = time.perf_counter()
    GP.thumb_basis(engine, trio.modes, REVIEW_IDS)
    ms = (time.perf_counter() - start) * 1000
    tiers = {b: by_id[b]["thumb"]["basis"] for b in PAINTED}
    print(f"thumb_basis on the sim engine, 18 visible review scenes: {ms:.1f} ms (painted bases: {tiers})")
    assert ms < 100


# ---- the guest panel ------------------------------------------------------------------------------------------------------
def test_the_panel_lists_eighteen_options_with_their_group_and_design(app_client):
    trio = install_review(_svc(app_client))
    body = _panel(app_client)
    scenes = body["scenes"]
    assert [s["id"] for s in scenes] == REVIEW_IDS
    for s in scenes:
        base = s["id"] if s["id"] in APPROVED else s["id"].rsplit("-", 1)[0]
        assert s["group"] == base and s["option_of"] == (None if s["id"] in APPROVED else base)
        thumb = s["thumb"]
        assert thumb["basis"] == "modules" and HEX12.match(
            thumb["sig"]
        )  # engine off: module level for every card
        assert len(thumb["modules"]) == 9
        assert all(
            re.fullmatch(r"#[0-9A-F]{6}", m["color"]) and m["colors"] is None for m in thumb["modules"]
        )
    by_id = {s["id"]: s for s in scenes}
    want = IM.design_thumb(mode_design(GOLDEN, _ctx(trio), heads=False), "modules")
    assert by_id[GOLDEN]["thumb"] == want
    for base in APPROVED:
        assert len({by_id[x]["thumb"]["sig"] for x in (base, f"{base}-2", f"{base}-3")}) == 3, base
    assert body["showing"] is None
    text = json.dumps(body)
    for word in ("canopy-r", "sim-", "Module "):
        assert word not in text


def test_six_or_fewer_visible_compute_and_send_no_thumbnail(app_client, monkeypatch):
    calls = counted(monkeypatch)
    bases: list[tuple] = []
    real = GP.thumb_basis

    def counted_basis(*a, **kw):
        bases.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(GP, "thumb_basis", counted_basis)
    for _ in range(3):  # the git config: six scenes
        body = _panel(app_client)
        assert len(body["scenes"]) == 6 and all(s["thumb"] is None for s in body["scenes"])
        assert all(s["group"] == s["id"] and s["option_of"] is None for s in body["scenes"])
    install_review(_svc(app_client), hide=OPTION_IDS)
    for _ in range(3):  # the review fixture with every option hidden: six again
        body = _panel(app_client)
        assert [s["id"] for s in body["scenes"]] == APPROVED and all(
            s["thumb"] is None for s in body["scenes"]
        )
    assert calls == [] and bases == []


def test_thumbnails_are_computed_once_per_published_look_and_basis(app_client, monkeypatch):
    services = _svc(app_client)
    trio = install_review(services)
    engine = _engine(app_client, trio, TierEngine)  # every painted base at T3: module level for all
    calls = counted(monkeypatch)
    for _ in range(3):
        _panel(app_client)
    assert len(calls) == 18 and all(heads is False for _, heads in calls)
    install_looks(services, trio, why="again")  # a new published look: every card again
    _panel(app_client)
    assert len(calls) == 36
    _panel(app_client)
    assert len(calls) == 36
    engine.tiers[GOLDEN] = Tier.T1  # e.g. its slot commissioned: only that card is redrawn
    body = _panel(app_client)
    assert len(calls) == 37 and calls[-1] == (GOLDEN, True)
    assert next(s for s in body["scenes"] if s["id"] == GOLDEN)["thumb"]["basis"] == "heads"


def test_a_thumbnail_that_cant_be_drawn_is_no_thumbnail_never_a_failed_panel(app_client, monkeypatch):
    install_review(_svc(app_client))
    from conductor.routes import design as DS

    real = DS.mode_design

    def broken(mode_id, ctx, **kw):
        if mode_id == "coastal-tides-2":
            raise RuntimeError("no design")
        return real(mode_id, ctx, **kw)

    monkeypatch.setattr(DS, "mode_design", broken)
    body = _panel(app_client)
    by_id = {s["id"]: s for s in body["scenes"]}
    assert by_id["coastal-tides-2"]["thumb"] is None
    assert all(by_id[x]["thumb"] is not None for x in REVIEW_IDS if x != "coastal-tides-2")


def test_a_panel_whose_thumbnails_fail_altogether_still_serves(app_client, monkeypatch):
    install_review(_svc(app_client))

    def boom(*_a, **_k):
        raise RuntimeError("no thumbnails")

    monkeypatch.setattr(GP, "thumb_basis", boom)
    body = _panel(app_client)
    assert [s["id"] for s in body["scenes"]] == REVIEW_IDS and all(s["thumb"] is None for s in body["scenes"])


def test_a_hidden_scene_is_off_the_panel_and_refused_calmly(app_client):
    services = _svc(app_client)
    trio = install_review(services, hide=["golden-hour-2"])
    engine = _engine(app_client, trio)
    body = _panel(app_client)
    assert "golden-hour-2" not in [s["id"] for s in body["scenes"]] and len(body["scenes"]) == 17
    r = app_client.post("/api/kiosk/scene", json={"scene": "golden-hour-2"})
    assert r.status_code == 404 and r.json()["detail"] == "That scene isn't one of the panel's choices."
    assert engine.calls == []
    debug = app_client.get("/api/kiosk/debug").json()
    assert debug["commands"][0]["error"] == "hidden on the iPad (kiosk.hidden_scenes)"
    assert debug["menu"] == {"hidden": ["golden-hour-2"], "ignored": []}
    r = app_client.post("/api/kiosk/scene", json={"scene": "disco-inferno"})
    assert r.status_code == 404
    assert app_client.get("/api/kiosk/debug").json()["commands"][0]["error"] == "not in scenes.yaml"
    r = app_client.post("/api/kiosk/scene", json={"scene": "golden-hour-3"})
    assert r.status_code == 200, r.text
    assert engine.calls == [("golden-hour-3", "kiosk")]


def test_a_hidden_scene_that_is_showing_is_still_named(app_client):
    services = _svc(app_client)
    trio = install_review(services, hide=["golden-hour-3"])
    engine = _engine(app_client, trio)
    engine.mode = "golden-hour-3"
    body = _panel(app_client)
    assert body["state"]["scene"] == "golden-hour-3"
    assert body["showing"]["id"] == "golden-hour-3" and body["showing"]["name"] == "Golden Hour #3"
    assert body["showing"]["group"] == GOLDEN and body["showing"]["option_of"] == GOLDEN
    assert "golden-hour-3" not in [s["id"] for s in body["scenes"]]
    services.settings.kiosk.hidden_scenes = []
    body = _panel(app_client)
    assert body["showing"] is None and body["state"]["scene"] == "golden-hour-3"


def _attention(client: TestClient) -> list[str]:
    r = client.get("/api/ops/summary")
    assert r.status_code == 200, r.text
    return [i["message"] for i in r.json()["attention"]]


def test_an_unusable_overlay_is_an_ops_summary_line_and_every_scene_stays_on(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path, "kiosk: {hidden_scenes: [golden-hour]\n") as client:
        path = tmp_path / "data" / SETTINGS_OVERLAY_PATH
        before = path.read_bytes()
        assert [s["id"] for s in _panel(client)["scenes"]] == APPROVED
        lines = [m for m in _attention(client) if m.startswith("The iPad's scene list")]
        assert lines == [
            (
                "The iPad's scene list (overrides/settings.yaml) couldn't be used (it isn't valid YAML), so the "
                "iPad offers every scene config/conductor.yaml doesn't hide. Flip a “Show on the iPad” switch on "
                "/ops to write it again."
            )
        ]
        assert lines[0] in client.get("/api/ops/summary").json()["problems"]
        assert path.read_bytes() == before
        r = client.post("/api/ops/ipad-scenes", json={"scene": GOLDEN, "show": False})
        assert r.status_code == 200, r.text
        assert not [m for m in _attention(client) if m.startswith("The iPad's scene list")]
        assert yaml.safe_load(path.read_text()) == {"kiosk": {"hidden_scenes": [GOLDEN]}}
        assert load_settings_overlay(tmp_path / "data").problem is None


# ---- /ops: the switch over HTTP --------------------------------------------------------------------------------------------
def _published(services) -> list[dict]:
    """Record what the routes publish on the WS hub (topic "ops")."""
    seen: list[dict] = []
    real = services.hub.publish

    def publish(topic, data):
        if topic == "ops":
            seen.append(data)
        return real(topic, data)

    services.hub.publish = publish
    return seen


def test_the_ipad_switch_hides_and_shows_a_scene_without_an_engine(app_client):
    services = _svc(app_client)
    install_review(services)
    assert services.extras.get("canopy") is None
    published = _published(services)
    body = app_client.get("/api/ops/ipad-scenes").json()
    assert [s["id"] for s in body["scenes"]] == REVIEW_IDS and all(s["on_ipad"] for s in body["scenes"])
    assert [s["id"] for s in body["scenes"] if s["idle"]] == [CALM]
    assert body["review"] is True and body["notes"] == [] and body["visible"] == body["total"] == 18
    r = app_client.post("/api/ops/ipad-scenes", json={"scene": "golden-hour-2", "show": False})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    res = r.json()
    assert res["ok"] is True and res["message"].startswith("Golden Hour #2 is off the iPad")
    assert next(s for s in res["scenes"] if s["id"] == "golden-hour-2")["on_ipad"] is False
    assert res["visible"] == 17
    assert "golden-hour-2" not in [s["id"] for s in _panel(app_client)["scenes"]]
    path = Path(services.data) / SETTINGS_OVERLAY_PATH
    assert yaml.safe_load(path.read_text()) == {"kiosk": {"hidden_scenes": ["golden-hour-2"]}}
    assert [(e["action"], e["scene"], e["show"]) for e in published] == [
        ("ipad_scenes", "golden-hour-2", False)
    ]
    assert published[0]["kind"] == "control" and len(_ops_events(services)) == 1
    again = app_client.post("/api/ops/ipad-scenes", json={"scene": "golden-hour-2", "show": False}).json()
    assert again["message"] == "Golden Hour #2 is already off the iPad." and len(published) == 1
    back = app_client.post("/api/ops/ipad-scenes", json={"scene": "golden-hour-2", "show": True}).json()
    assert back["message"] == "Golden Hour #2 is back on the iPad." and len(published) == 2
    assert "golden-hour-2" in [s["id"] for s in _panel(app_client)["scenes"]]


def test_the_ipad_switch_refuses_the_idle_scene_and_an_unknown_one(app_client):
    services = _svc(app_client)
    path = Path(services.data) / SETTINGS_OVERLAY_PATH
    r = app_client.post("/api/ops/ipad-scenes", json={"scene": CALM, "show": False})
    assert r.status_code == 409
    assert (
        r.json()["detail"]
        == "Lowcountry Calm is the idle scene (kiosk.idle_mode), so the iPad always offers it."
    )
    services.settings.kiosk.idle_scene = "goodnight-glow"
    r = app_client.post("/api/ops/ipad-scenes", json={"scene": "goodnight-glow", "show": False})
    assert r.status_code == 409 and "(kiosk.idle_scene)" in r.json()["detail"]
    r = app_client.post("/api/ops/ipad-scenes", json={"scene": "disco-inferno", "show": False})
    assert r.status_code == 404 and r.json()["detail"] == "There is no scene called 'disco-inferno'."
    assert not path.exists() and _ops_events(services) == []


def test_a_save_that_fails_over_http_changes_nothing(app_client, monkeypatch):
    services = _svc(app_client)
    install_review(services)
    published = _published(services)

    def disk_full(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(IM, "save_settings_overlay", disk_full)
    r = app_client.post("/api/ops/ipad-scenes", json={"scene": "golden-hour-2", "show": False})
    assert r.status_code == 503
    assert r.json()["detail"] == "The iPad's scene list couldn't be saved (disk full); nothing changed."
    assert services.settings.kiosk.hidden_scenes == []
    assert "golden-hour-2" in [s["id"] for s in _panel(app_client)["scenes"]]
    assert not (Path(services.data) / SETTINGS_OVERLAY_PATH).exists()
    assert published == [] and _ops_events(services) == []


def test_two_switches_back_to_back_both_stick(app_client):
    from conductor.routes import ops as O

    assert inspect.iscoroutinefunction(O.ipad_scenes) and inspect.iscoroutinefunction(O.ipad_scenes_set)
    services = _svc(app_client)
    install_review(services)
    for sid in ("golden-hour-2", "rainbow-row-sunset-3"):
        assert app_client.post("/api/ops/ipad-scenes", json={"scene": sid, "show": False}).status_code == 200
    assert services.settings.kiosk.hidden_scenes == ["rainbow-row-sunset-3", "golden-hour-2"]
    path = Path(services.data) / SETTINGS_OVERLAY_PATH
    assert yaml.safe_load(path.read_text())["kiosk"]["hidden_scenes"] == [
        "rainbow-row-sunset-3",
        "golden-hour-2",
    ]


def test_the_switch_is_ops_only(app_client_auth):
    from test_kiosk_routes import OPS, _enroll

    client = app_client_auth
    assert client.get("/api/ops/ipad-scenes").status_code == 401
    _enroll(client, "kiosk")
    assert client.get("/api/ops/ipad-scenes").status_code in (401, 403)
    r = client.post("/api/ops/ipad-scenes", json={"scene": GOLDEN, "show": False})
    assert r.status_code in (401, 403)
    assert client.get("/api/ops/ipad-scenes", auth=OPS).status_code == 200
    assert _svc(client).settings.kiosk.hidden_scenes == []


def test_the_switch_is_json_only_and_same_site(app_client):
    r = app_client.post(
        "/api/ops/ipad-scenes",
        content=json.dumps({"scene": GOLDEN, "show": False}),
        headers={"Content-Type": "text/plain"},
    )
    assert r.status_code == 415
    r = app_client.post(
        "/api/ops/ipad-scenes",
        json={"scene": GOLDEN, "show": False},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    assert (
        app_client.post("/api/ops/ipad-scenes", json={"scene": GOLDEN, "show": False, "x": 1}).status_code
        == 422
    )
    assert app_client.post("/api/ops/ipad-scenes", json={"scene": GOLDEN, "show": "false"}).status_code == 422
    assert app_client.post("/api/ops/ipad-scenes", json={"scene": "", "show": False}).status_code == 422
    assert _svc(app_client).settings.kiosk.hidden_scenes == []


def test_the_list_survives_a_restart(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        install_review(_svc(client))
        assert (
            client.post("/api/ops/ipad-scenes", json={"scene": "golden-hour-2", "show": False}).status_code
            == 200
        )
    with _client(monkeypatch, tmp_path) as client:  # the restart, on the same data dir
        services = _svc(client)
        assert services.settings.kiosk.hidden_scenes == ["golden-hour-2"]
        install_review(services)  # hide=None keeps the list the overlay put in force
        ids = [s["id"] for s in _panel(client)["scenes"]]
        assert "golden-hour-2" not in ids and len(ids) == 17


def test_names_that_hide_nothing_are_an_ops_summary_line_and_the_next_switch_drops_them(
    monkeypatch, tmp_path
):
    with _client(monkeypatch, tmp_path, "kiosk: {hidden_scenes: [disco-inferno]}\n") as client:
        install_review(_svc(client))
        line = (
            "kiosk.hidden_scenes names 'disco-inferno', which isn't a scene now, so it hides nothing; the next "
            "“Show on the iPad” switch on /ops drops it."
        )
        assert line in _attention(client)  # before any switch
        assert _switch(client, "golden-hour-2", False)
        path = tmp_path / "data" / SETTINGS_OVERLAY_PATH
        assert yaml.safe_load(path.read_text())["kiosk"]["hidden_scenes"] == ["golden-hour-2"]
        assert line not in _attention(client)


def _switch(client: TestClient, scene: str, show: bool) -> bool:
    r = client.post("/api/ops/ipad-scenes", json={"scene": scene, "show": show})
    assert r.status_code == 200, r.text
    return True


# ---- /ops: the overlay download folds the list into conductor.yaml ---------------------------------------------------------
def test_the_overlay_download_folds_the_list_into_conductor_yaml(app_client, tmp_path):
    nothing = app_client.get("/api/ops/overlay").text
    assert "--- a/config/conductor.yaml" not in nothing
    assert (
        "kiosk.hidden_scenes: none." in nothing and "config/conductor.yaml already says the same" in nothing
    )
    assert (
        "--- a/config/conductor.yaml" not in app_client.get("/api/ops/overlay", params={"part": "diff"}).text
    )
    assert _switch(app_client, GOLDEN, False)
    response = app_client.get("/api/ops/overlay")
    assert response.status_code == 200
    text = response.text
    head = text.split("\n---", 1)[0]
    assert "kiosk.hidden_scenes: [golden-hour]" in head and "`git apply` this file" in head
    hunk = text[text.index("--- a/config/conductor.yaml") :]
    assert hunk.startswith("--- a/config/conductor.yaml\n+++ b/config/conductor.yaml\n")
    assert "\n-  hidden_scenes: []\n" in hunk and "\n+  hidden_scenes: [golden-hour]\n" in hunk
    fold = IM.conductor_yaml_diff(CONFIG_DIR, [GOLDEN])
    assert hunk == fold.diff
    if shutil.which("git"):
        after = _git_apply(tmp_path, hunk)
    else:
        after = fold.after
    reread = yaml.safe_load(after)
    assert reread["kiosk"]["hidden_scenes"] == [GOLDEN]
    KioskSettings.model_validate(reread["kiosk"])
    diff = app_client.get("/api/ops/overlay", params={"part": "diff"})
    disposition = diff.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="canopy-config-') and disposition.endswith('.diff"')
    assert diff.text.endswith(fold.diff) and "--- a/config/conductor.yaml" in diff.text
    assert "hidden_scenes" not in app_client.get("/api/ops/overlay", params={"part": "yaml"}).text


def test_the_overlay_download_says_why_it_cant_fold(app_client, tmp_path, monkeypatch):
    assert _switch(app_client, GOLDEN, False)
    config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config)
    path = config / "conductor.yaml"
    path.write_text(
        path.read_text().replace("  hidden_scenes: []", "  hidden_scenes: []\n  hidden_scenes: []")
    )
    monkeypatch.setattr(_svc(app_client), "config_dir", config)
    text = app_client.get("/api/ops/overlay").text
    assert "   No diff: config/conductor.yaml has no one-line `hidden_scenes: [...]` under kiosk" in text
    assert "--- a/config/conductor.yaml" not in text


def test_the_overlay_download_never_fails_over_the_ipad_menu(app_client, monkeypatch):
    def broken(_services):
        raise ConfigError("scenes.yaml is broken")

    monkeypatch.setattr(IM, "live_menu", broken)
    r = app_client.get("/api/ops/overlay")
    assert r.status_code == 200
    assert "   No diff: the live scene catalog couldn't be read (ConfigError)." in r.text
    assert "kiosk.hidden_scenes: none." in r.text


def test_grid_max_is_one_number_on_every_side():
    """More than GRID_MAX visible is the review menu: the server, the kiosk and /ops all use one number."""
    found = {}
    for rel in ("web/kiosk/panel-model.mjs", "web/ops/ipad-model.mjs"):
        found[rel] = re.findall(r"export const GRID_MAX = (\d+);", (ROOT / rel).read_text(encoding="utf-8"))
    assert found == {rel: [str(IM.GRID_MAX)] for rel in found} and IM.GRID_MAX == 6


# ---- the pure models under node (CI runs pytest only) ------------------------------------------------------------------------
@pytest.mark.skipif(
    shutil.which("node") is None, reason="Node isn't installed; ops-ipad.test.mjs is UNVERIFIED here"
)
def test_the_ops_ipad_model_under_node():
    """tests/ops-ipad.test.mjs (the /ops card's words). tests/test_kiosk_node.py (PR A) runs canopy-preview and
    guest-panel; PR D consolidates the wrappers."""
    result = subprocess.run(
        [shutil.which("node"), "--test", str(ROOT / "tests" / "ops-ipad.test.mjs")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    # "ℹ fail 0" with the spec reporter (newer Node, macOS), "# fail 0" in TAP (CI's runner)
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-2000:]
