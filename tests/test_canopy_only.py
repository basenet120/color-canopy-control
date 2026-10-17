"""The Conductor controls the canopy only (D67): what went is gone, and the live show box's old data still loads.

- The removed surfaces answer 404 to ops: the vignette rooms and their API, the Studio's paired shop-kit commands.
- No conductor module imports what was removed (AST); the `vignette` role, its accounts and devices are gone; an
  enrollment for it is refused in plain English; the WebSocket topics no longer know it.
- **Old data on the show box** (§1.5 of docs/live-lab/PLAN.md): a saved session file holding the guest iPad's kiosk
  session and a vignette record loads the kiosk session and sets nothing aside; a registry overlay holding removed
  vignette devices and removed fields on surviving entries loads (ignored, named on /ops), the app starts, and the
  next ops registry edit prunes them.
- `conductor.preflight` (run by `run-conductor.sh --check`, so by `canopy-mac install` before the restart) passes
  over that data, names what it ignores, fails over data this code can't load, and writes nothing in the data dir.
- The Studio is screen only: no shop-kit connect, lease, module, pair, bench or motion-demo call is left in it.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from conductor import preflight
from conductor.clock import VirtualClock
from conductor.config import (
    CONFIG_DIR,
    REMOVED_DEVICE_IDS,
    load_registry,
    overlay_diff,
    save_overlay_patch,
)
from conductor.roles import RoleStore
from conductor.roles_api import SESSION_COOKIE, Role
from conductor.ws import topic_allowed

ROOT = Path(__file__).resolve().parent.parent
OPS = ("crew", "canopy")
# What the live Mac's data folder may hold from before the removal (we can't read it: the rules forbid it), in the
# shapes the old code wrote: a patch for a removed device, a full vignette entry, a removed field on a canopy entry,
# and a spare copied whole from VWBD's overlay with the removed fields on it.
OLD_OVERLAY = {
    "devices": {
        "living-lamp-1": {"metadevice_id": "md-living-lamp-1", "label": "Table lamp (edited)"},
        "porch-lamp": {
            "role": "vignette",
            "account": "living",
            "vignette": "living",
            "kind": "bulb",
            "label": "Porch lamp",
        },
        "bench-kit": {"kind": "string"},
        "shop-string-1": {
            "role": "spare",
            "account": "bench",
            "profile": "sqhdk03-sim",
            "vignette": "garden",
            "kind": "string",
        },
    }
}
DROPPED_IDS = ["living-lamp-1", "porch-lamp"]
DROPPED_FIELDS = {"bench-kit": ["kind"], "shop-string-1": ["vignette", "kind"]}


def _env(monkeypatch, data: Path, **extra) -> None:
    for key in (
        "CONDUCTOR_AUTH",
        "HUBSPACE_LIVE",
        "CONDUCTOR_MODE",
        "RAILWAY_ENVIRONMENT_NAME",
        "CONDUCTOR_ENV",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CONDUCTOR_DATA_DIR", str(data))
    monkeypatch.setenv("CONDUCTOR_SIM_SPEED", "fast")
    monkeypatch.setenv("CONDUCTOR_ENGINE", "off")
    monkeypatch.setenv("CONDUCTOR_BASIC_AUTH", "crew:canopy")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def _client(monkeypatch, data: Path, **extra) -> TestClient:
    from conductor.app import app

    _env(monkeypatch, data, **extra)
    return TestClient(app, raise_server_exceptions=False)


def _write_overlay(data: Path, overlay: dict = OLD_OVERLAY) -> Path:
    path = data / "overrides" / "registry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(overlay))
    return path


def _record(role: str, digest: str, now: float, grants=()) -> dict:
    return {
        "digest": digest,
        "role": role,
        "name": f"a {role}",
        "grants": list(grants),
        "created_at": now,
        "expires_at": now + 30 * 24 * 3600,
    }


def _old_sessions(data: Path, clock=None) -> str:
    """The guest iPad's kiosk session as the store saves it, plus a vignette tablet's record beside it. Returns the
    kiosk's raw session id (its cookie)."""
    store = RoleStore(data, clock or VirtualClock())
    session_id, _ = store.redeem_enrollment(store.create_enrollment("kiosk", "Guest iPad")[0])
    path = data / "roles" / "sessions.json"
    raw = json.loads(path.read_text())
    now = raw["records"][0]["created_at"]
    raw["records"].append(_record("vignette", "ab" * 32, now, ["living"]))
    path.write_text(json.dumps(raw))
    return session_id


def _snapshot(data: Path) -> dict[str, str]:
    return {
        p.relative_to(data).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(data.rglob("*"))
        if p.is_file()
    }


# ---- (1) the removed surfaces answer 404 -----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/vignette/living/"),
        ("GET", "/api/vignette/living"),
        ("POST", "/api/vignette/living/action"),
        ("POST", "/api/studio/pair/whole"),
        ("GET", "/api/studio/pair/gradient"),
        ("POST", "/api/studio/pair/gradient"),
        ("GET", "/kiosk-resting/"),
    ],
)
def test_the_removed_surfaces_answer_404_to_ops(monkeypatch, tmp_path, method, path):
    with _client(monkeypatch, tmp_path / "data") as c:
        r = c.request(method, path, auth=OPS, json={} if method == "POST" else None)
    assert r.status_code == 404, (method, path, r.status_code)


# ---- (2) nothing imports what was removed ------------------------------------------------------------------------
REMOVED_MODULES = (
    "conductor.vignettes",
    "conductor.module.pair",
    "conductor.module.scene_gradient",
    "conductor.workshop",
)


def _imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_no_conductor_module_imports_what_was_removed():
    for path in sorted((ROOT / "conductor").rglob("*.py")):
        bad = {m for m in _imports(path) if m.startswith(REMOVED_MODULES)}
        bad |= {
            m for m in _imports(path) if m in ("conductor.routes.vignette", "conductor.module.scene_gradient")
        }
        assert not bad, f"{path.relative_to(ROOT)} imports {sorted(bad)}"
    for gone in ("conductor/vignettes.py", "conductor/routes/vignette.py", "conductor/workshop.py"):
        assert not (ROOT / gone).exists(), gone
    for gone in ("conductor/module/pair.py", "conductor/module/scene_gradient.py", "config/vignettes.yaml"):
        assert not (ROOT / gone).exists(), gone


# ---- (3) the role, accounts and devices are gone -----------------------------------------------------------------
def test_the_vignette_role_accounts_and_devices_are_gone():
    assert "vignette" not in {r.value for r in Role}
    registry = load_registry(CONFIG_DIR)
    assert {d.role for d in registry.file.devices} == {"bench", "canopy_module", "spare"}
    assert set(registry.accounts()) == {"bench", "canopy"}
    assert len(REMOVED_DEVICE_IDS) == 15
    assert not REMOVED_DEVICE_IDS & {d.id for d in registry.file.devices}
    text = (CONFIG_DIR / "devices.yaml").read_text()
    assert not any(device_id in text for device_id in REMOVED_DEVICE_IDS)
    assert registry.dropped == [] and registry.dropped_fields == {}


def test_a_vignette_enrollment_is_refused_in_plain_english(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path / "data") as c:
        r = c.post(
            "/api/roles/enrollments", json={"role": "vignette", "name": "x", "grants": ["living"]}, auth=OPS
        )
    assert r.status_code == 400 and r.json()["detail"].endswith("Use kiosk or tester."), r.text


def test_the_websocket_topics_no_longer_know_the_vignette_role():
    assert topic_allowed("vignette", frozenset({"living"}), "canopy") is False
    assert topic_allowed("vignette", frozenset({"living"}), "vignette:living") is False
    assert topic_allowed("kiosk", frozenset(), "vignette:living") is False
    assert topic_allowed("kiosk", frozenset(), "canopy") is True
    assert (
        topic_allowed("ops", frozenset(), "vignette:living") is True
    )  # ops passes everything; no such topic exists


# ---- (4) old data on the show box ----------------------------------------------------------------------------
def test_a_saved_vignette_session_is_skipped_and_the_guest_ipads_session_survives(monkeypatch, tmp_path):
    data = tmp_path / "data"
    session_id = _old_sessions(data)
    store = RoleStore(data, VirtualClock())
    principal = store.principal(session_id)
    assert principal is not None and principal.role is Role.KIOSK
    assert store.problem == "" and store.skipped == [("sessions.json", "vignette", "ab" * 6)]
    assert not list((data / "roles").glob("*.corrupt"))
    # and through the real app: the iPad's cookie still opens its panel
    app_session = _old_sessions(tmp_path / "app-data2")
    with _client(monkeypatch, tmp_path / "app-data2") as c:
        c.cookies.set(SESSION_COOKIE, app_session)
        r = c.get("/api/kiosk/panel")
        assert r.status_code == 200 and r.json()["viewer"] == "kiosk", r.text
        assert c.get("/api/roles/sessions", auth=OPS).json()["sessions"][0]["role"] == "kiosk"
    assert not list((tmp_path / "app-data2" / "roles").glob("*.corrupt"))


def test_an_old_registry_overlay_loads_and_the_next_ops_edit_prunes_it(monkeypatch, tmp_path):
    data = tmp_path / "data"
    path = _write_overlay(data)
    registry = load_registry(CONFIG_DIR, data)
    assert registry.dropped == DROPPED_IDS
    assert registry.dropped_fields == DROPPED_FIELDS
    assert registry.get("shop-string-1").role == "spare" and registry.get("bench-kit").role == "bench"
    for gone in DROPPED_IDS:
        with pytest.raises(KeyError):
            registry.get(gone)
    # the git diff ignores the old data too (else the /ops "download as diff" fails re-validation)
    assert overlay_diff(registry).problem == ""
    assert "living-lamp-1" not in overlay_diff(registry).after and "kind:" not in overlay_diff(registry).after

    with _client(monkeypatch, data) as c:  # the app starts over it (lifespan) …
        assert c.get("/healthz").status_code == 200
        services = c.app.state.services
        problems = " ".join(services.problems)
        assert "living-lamp-1" in problems and "porch-lamp" in problems
        assert "bench-kit" in problems and "shop-string-1" in problems and "D67" in problems
        summary = c.get("/api/ops/summary", auth=OPS).json()
        assert any("removed vignette devices" in json.dumps(item) for item in summary["attention"])

    saved = save_overlay_patch(
        registry, "bench-kit", {"label": "x"}, now=1_792_000_000.0
    )  # … and an ops edit
    written = yaml.safe_load(path.read_text())["devices"]
    assert set(written) == {"bench-kit", "shop-string-1"}
    assert not any(k in entry for entry in written.values() for k in ("kind", "vignette"))
    assert written["bench-kit"] == {"label": "x"}
    assert saved.dropped == [] and saved.dropped_fields == {}
    assert load_registry(CONFIG_DIR, data).dropped == []
    assert overlay_diff(saved).problem == ""


def test_the_guest_panel_target_is_the_engine_with_conductor_env_workshop_sim(monkeypatch, tmp_path):
    from conductor.guest_panel import EngineTarget, GuestPanel

    with _client(monkeypatch, tmp_path / "data", CONDUCTOR_ENV="workshop-sim") as c:
        assert c.get("/api/kiosk/panel", auth=OPS).json()["target"] == "canopy"
        panel = GuestPanel.of(c.app.state.services)
        assert isinstance(c.portal.call(panel.target), EngineTarget)


# ---- the preflight -------------------------------------------------------------------------------------------
def _preflight(data: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = preflight.run(env={"CONDUCTOR_DATA_DIR": str(data), "CONDUCTOR_MODE": "sim"}, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_the_preflight_passes_over_old_data_names_it_and_writes_nothing(tmp_path):
    data = tmp_path / "data"
    _write_overlay(data)
    _old_sessions(data)
    before = _snapshot(data)
    code, out, err = _preflight(data)
    assert code == 0, err
    assert "ignored removed vignette devices (D67): living-lamp-1, porch-lamp" in out
    assert "ignored removed fields on bench-kit (D67): kind" in out
    assert "ignored removed fields on shop-string-1 (D67): vignette, kind" in out
    assert "skipped a sessions record of the removed role 'vignette' (abababababab; D67)" in out
    assert "roles      ok (1 sessions" in out and "preflight  ok" in out
    assert _snapshot(data) == before, "the preflight wrote in the data dir"
    assert "ab" * 32 not in out  # a fingerprint, never the digest


def test_the_preflight_fails_over_a_registry_overlay_this_code_cant_load(tmp_path):
    data = tmp_path / "data"
    _write_overlay(data, {"devices": {"porch-plug": {"label": "not a registry device"}}})
    before = _snapshot(data)
    code, out, err = _preflight(data)
    assert code == 1 and "FAILED in registry" in err and "porch-plug" in err
    assert "preflight  ok" not in out
    assert _snapshot(data) == before


def test_the_preflight_fails_over_a_session_file_the_store_would_set_aside_and_leaves_it_in_place(tmp_path):
    data = tmp_path / "data"
    (data / "roles").mkdir(parents=True)
    (data / "roles" / "sessions.json").write_text("{not json")
    before = _snapshot(data)
    code, _out, err = _preflight(data)
    assert code == 1 and "FAILED in roles" in err and "set aside" in err
    assert _snapshot(data) == before and not list((data / "roles").glob("*.corrupt"))


def test_the_preflight_fails_on_runtime_settings_a_start_would_refuse(tmp_path):
    out, err = io.StringIO(), io.StringIO()
    code = preflight.run(
        env={"CONDUCTOR_DATA_DIR": str(tmp_path), "CONDUCTOR_MODE": "disco"}, out=out, err=err
    )
    assert code == 1 and "FAILED in settings" in err.getvalue() and "CONDUCTOR_MODE" in err.getvalue()


def test_the_preflight_is_offline_and_binds_nothing():
    """It imports no network or server code: the same rule the adapter boundary has (07 §1)."""
    imports = _imports(ROOT / "conductor" / "preflight.py")
    for banned in ("aiohttp", "aioafero", "uvicorn", "socket", "conductor.app", "conductor.services"):
        assert not any(m == banned or m.startswith(banned + ".") for m in imports), banned


# ---- the Studio is screen only -------------------------------------------------------------------------------
STUDIO = ROOT / "web" / "ops" / "showcase"


def test_the_studio_sends_no_light_command():
    js = re.sub(r"(?m)(^|[^:'\"])//.*$", r"\1", (STUDIO / "studio.js").read_text())
    calls = re.findall(r"api\(\s*'([^']*)'", js)
    assert calls == ["/api/canopy/design?mode="], calls  # its one request: a read of the show's design
    for word in (
        "/api/module",
        "/api/studio",
        "/api/bench",
        "method:",
        "sendBeacon",
        "lease",
        "PAIR_IDS",
        "connect(",
        "heartbeat",
    ):
        assert word not in js, word
    html = (STUDIO / "index.html").read_text()
    for gone in (
        'id="connect"',
        'id="disconnect"',
        'id="module-select"',
        'id="scene-gradient"',
        'id="scene-apply"',
        'id="apply-brightness"',
        'id="power-on"',
        'id="motion-demo"',
        'id="pair-status"',
        'id="restart-pair"',
        'id="connection"',
        'value="both"',
        "Not connected",
    ):
        assert gone not in html, gone
    assert 'href="#lab" id="open-lab"' in html and "only place that changes the live show" in html
