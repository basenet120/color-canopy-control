"""Regression tests for the third review's findings and gaps: the guest-input pause across restarts (F3), WS tickets
of a revoked session (V5), the ops overlay download (07 §8) and out-of-band drift healing within 10 s (the self-heal
gap). (V1/V2/V3/V7 were the vignette tablets', removed with them in D67.)"""

from __future__ import annotations

import itertools
import json
import os

import pytest
from fake_compiler import make_world, settings_with

from conductor.clock import run_virtual
from conductor.engine_api import CanopyPhase

QUIET = settings_with(kiosk={"idle_return_s": 100_000})


# ---- F3: the guest-input pause survives a restart ------------------------------------------------------------------
def test_a_pause_is_saved_atomically_and_restored_by_the_next_engine(tmp_path):
    state_dir = tmp_path / "state"

    async def main():
        w = make_world(settings=QUIET, state_dir=state_dir)
        await w.engine.start()
        w.engine.pause_guest_input(True, source="ops")
        saved = json.loads((state_dir / "guest_input.json").read_text())
        mode = oct((state_dir / "guest_input.json").stat().st_mode & 0o777)
        await w.close()
        # A restart (the watchdog, a reboot, `canopy-mac install` / `start` / `restart`): a new engine on the same data dir.
        w2 = make_world(settings=QUIET, state_dir=state_dir)
        await w2.engine.start()
        state = w2.engine.state()
        refused = await w2.engine.request_mode("holiday", source="kiosk")
        alerts = [e["message"] for e in w2.events("alert")]
        w2.engine.pause_guest_input(False, source="ops")
        await w2.close()
        w3 = make_world(settings=QUIET, state_dir=state_dir)
        resumed = w3.engine.state().guest_input
        await w3.close()
        return saved, mode, state, refused, alerts, resumed

    saved, mode, state, refused, alerts, resumed = run_virtual(main)
    assert saved["paused"] is True and saved["source"] == "ops" and saved["since"] is not None
    assert mode == "0o600"
    assert (
        state.guest_input is False and state.phase is CanopyPhase.RESTING and state.paused_since is not None
    )
    assert state.to_json()["paused_since"] == state.paused_since
    assert refused.refused and "paused" in refused.message
    assert any("still paused after the restart" in a for a in alerts)
    assert resumed is True  # a resume is saved too


def test_an_unreadable_pause_file_keeps_guests_out(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "guest_input.json").write_text("{not json")

    async def main():
        w = make_world(settings=QUIET, state_dir=state_dir)
        paused = w.engine.paused
        alerts = [e["message"] for e in w.events("alert")]
        await w.close()
        return paused, alerts

    paused, alerts = run_virtual(main)
    assert paused is True
    assert any("couldn't be read" in a for a in alerts)


def test_no_state_dir_means_memory_only(tmp_path):
    async def main():
        w = make_world(settings=QUIET)
        w.engine.pause_guest_input(True, source="ops")
        await w.close()
        return list(tmp_path.iterdir())

    assert run_virtual(main) == []


def test_the_app_keeps_guest_input_paused_across_a_restart(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from conductor.app import app

    for key in ("CONDUCTOR_BASIC_AUTH", "HUBSPACE_LIVE", "CONDUCTOR_MODE", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("CONDUCTOR_ENGINE", raising=False)
    monkeypatch.setenv("CONDUCTOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CONDUCTOR_SIM_SPEED", "fast")
    monkeypatch.setenv("CONDUCTOR_AUTH", "off")
    with TestClient(app) as client:
        response = client.post("/api/canopy/pause", json={"paused": True})
        assert response.status_code == 200 and "across a restart" in response.json()["message"]
    assert os.path.exists(tmp_path / "data" / "state" / "guest_input.json")
    with TestClient(app) as client:  # the restart
        state = client.get("/api/canopy/state").json()
        assert state["guest_input"] is False and state["paused_since"]
        kiosk = client.post("/api/kiosk/select", json={"mode": "rainbow-row-sunset"})
        assert kiosk.status_code == 423
        assert client.post("/api/canopy/pause", json={"paused": False}).status_code == 200
    with TestClient(app) as client:
        assert client.get("/api/canopy/state").json()["guest_input"] is True


# ---- V5: a ticket fetched before a revoke never opens a socket -----------------------------------------------------
def _enrolled_kiosk(client, ops):
    link = client.post(
        "/api/roles/enrollments",
        json={"role": "kiosk", "name": "Guest iPad", "grants": []},
        auth=ops,
    ).json()
    assert client.post(link["path"], follow_redirects=False).status_code == 303
    return client.get("/api/roles/sessions", auth=ops).json()["sessions"][0]["fingerprint"]


def test_a_ticket_fetched_before_the_revoke_is_refused(app_client_auth):
    from starlette.websockets import WebSocketDisconnect

    client = app_client_auth
    ops = ("crew", "canopy")
    fp = _enrolled_kiosk(client, ops)
    ticket = client.get("/api/ws-ticket").json()["ticket"]  # the iPad holds a fresh ticket...
    assert client.delete(f"/api/roles/sessions/{fp}", auth=ops).status_code == 200  # ...when ops revokes it
    assert client.get("/api/kiosk/state").status_code == 401
    with client.websocket_connect("/ws/live") as ws:
        ws.send_json({"type": "auth", "ticket": ticket})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == 1008


def test_the_hub_checks_the_session_even_when_a_ticket_outlived_the_purge():
    from conductor.roles_api import Role
    from conductor.wsauth import TicketBook

    tickets = TicketBook()
    active = {"fp-1": True}
    from conductor.ws import WsHub

    hub = WsHub(tickets, session_active=lambda fp: active.get(fp, False))
    ticket = tickets.issue(Role.KIOSK.value, [], session="fp-1")
    assert hub._session_ok(ticket)
    active["fp-1"] = False  # expired or revoked by a path that didn't call close_session
    assert not hub._session_ok(ticket)
    assert hub._session_ok(tickets.issue("ops"))  # Basic-auth ops has no enrolled session
    other = tickets.issue(Role.KIOSK.value, [], session="fp-2")
    kept = tickets.issue(Role.KIOSK.value, [], session="fp-3")
    hub.close_session("fp-2")
    assert tickets.redeem(other.value) is None and tickets.redeem(kept.value) is not None


def test_role_store_session_active(tmp_path):
    from conductor.roles import RoleStore

    store = RoleStore(tmp_path)
    token, _ = store.create_enrollment("kiosk", "Kiosk")
    _session_id, principal = store.redeem_enrollment(token)
    assert store.session_active(principal.session_id)
    store.revoke_session(principal.session_id)
    assert not store.session_active(principal.session_id)
    assert not store.session_active("nope")


# ---- the self-heal gap: a change made outside the Conductor is found and undone within 10 s ------------------------
def test_rolling_true_up_reads_each_module_once_per_interval_spread_out():
    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")
        await w.until(w.all_correct, timeout=10)
        await w.engine.wait_idle()
        await w.run_for(6)
        reads: list[tuple[float, str]] = []
        original = w.sim.read_state

        async def read_state(address, **kw):
            reads.append((w.clock.monotonic(), address))
            return await original(address, **kw)

        w.sim.read_state = read_state
        writes_before = w.sim.stats()["writes"]
        await w.run_for(30)
        writes_after = w.sim.stats()["writes"]
        await w.close()
        return reads, writes_after - writes_before, w.engine.deps.settings.reconciler.true_up_interval_s

    reads, writes, interval = run_virtual(main)
    assert interval == 5
    per_device: dict[str, list[float]] = {}
    for t, address in reads:
        per_device.setdefault(address, []).append(t)
    assert len(per_device) == 9
    for times in per_device.values():
        assert 5 <= len(times) <= 7  # 30 s / 5 s, give or take a retried read
        gaps = [b - a for a, b in itertools.pairwise(times)]
        assert all(g >= 0.9 for g in gaps) and max(gaps) <= interval + 1.0
    starts = sorted(t for t, _ in reads)
    assert min(b - a for a, b in itertools.pairwise(starts)) >= 0.25  # spread, not a burst of nine
    assert writes == 0  # a canopy that shows its look is only read, never written


def test_out_of_band_changes_heal_within_10_s_each_time():
    from conductor import rows as R

    async def main():
        w = make_world(settings=QUIET)
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("holiday", source="ops")
        await w.until(w.all_correct, timeout=10)
        await w.engine.wait_idle()
        heals = []
        for i, module in enumerate(["canopy-r0c0", "canopy-r1c1", "canopy-r2c2", "canopy-r0c0"]):
            await w.run_for(7 + i)  # a different phase against the rolling reads each time
            started = w.clock.monotonic()
            response = w.sim.faults.change_out_of_band(
                w.address(module), [R.color_mode("white"), R.color_temperature(5000)]
            )
            assert response.ok
            wrong = await w.until(lambda m=module: not w.correct(m), timeout=3)
            healed = await w.until(w.all_correct, timeout=30)
            heals.append((wrong, None if healed is None else w.clock.monotonic() - started))
        stats = dict(w.engine.reconciler.stats)
        oob = w.sim.stats()["out_of_band_changes"]
        await w.close()
        return heals, stats, oob

    heals, stats, oob = run_virtual(main)
    assert oob == 4
    for wrong, healed in heals:
        assert wrong is not None and healed is not None and healed <= 10.0, heals
    assert stats["drift_resends"] >= 4


def test_an_out_of_band_change_never_passes_through_the_executor():
    from conductor import rows as R

    async def main():
        w = make_world(settings=QUIET)
        submitted: list[str] = []
        w.executor.add_submit_listener(lambda c: submitted.append(c.device_id))
        response = w.sim.faults.change_out_of_band(w.address("canopy-r0c0"), [R.power(False)])
        bad = w.sim.faults.change_out_of_band(w.address("canopy-r0c0"), [R.color_temperature(99999)])
        await w.run_for(2)
        view = {r.key: r.value for r in w.sim.device_view(w.address("canopy-r0c0"))}
        writes = w.sim.stats()["writes"]
        await w.close()
        return response, bad, view, submitted, writes

    response, bad, view, submitted, writes = run_virtual(main)
    assert response.ok and view[("power", None)] == "off"
    assert bad.status == 400  # validated like a PUT
    assert submitted == [] and writes == 0  # not the Conductor's write: no executor, no rate budget


# ---- 07 §8: the ops overlay, downloadable as a diff to commit back to git ------------------------------------------
REAL_ID = "0f8e2b9a-1c2d-4e5f-8a9b-0c1d2e3f4a5b"  # made-up ids shaped like real ones
REAL_ID_2 = "7a6b5c4d-3e2f-4a1b-9c8d-7e6f5a4b3c2d"


def _edit_overlay(client) -> None:
    services = client.app.state.services
    services.update_registry("canopy-r1c1", {"metadevice_id": REAL_ID, "cable_path": "serpentine_cols"})
    services.update_registry("bench-kit", {"flags": {"t2_select": True}, "flags_proven_on": REAL_ID})
    services.update_registry(
        "spare-4", {"role": "spare", "account": "canopy", "label": "Spare kit 4", "profile": "sqhdk03-sim"}
    )
    services.update_registry("spare-2", {"metadevice_id": REAL_ID_2})  # an id only: nothing for git


def _apply(before: str, diff: str) -> str:
    """Apply a unified diff with one file (what `git apply` would do), for checking."""
    import re

    lines = before.splitlines(keepends=True)
    out, pos = [], 0
    hunks = re.split(r"(?m)^(?=@@ )", diff.split("+++ b/config/devices.yaml\n", 1)[1])
    for hunk in hunks:
        if not hunk.startswith("@@"):
            continue
        start = int(re.match(r"@@ -(\d+)", hunk).group(1)) - 1
        body = hunk.splitlines(keepends=True)[1:]
        out += lines[pos:start]
        pos = start
        for line in body:
            if line.startswith(" "):
                out.append(lines[pos])
                pos += 1
            elif line.startswith("-"):
                assert lines[pos] == line[1:], (lines[pos], line)
                pos += 1
            elif line.startswith("+"):
                out.append(line[1:])
    return "".join(out + lines[pos:])


def test_the_overlay_download_is_the_yaml_plus_a_diff_for_git(app_client):
    import yaml

    from conductor.config import CONFIG_DIR, RegistryFile, _apply_defaults

    _edit_overlay(app_client)
    response = app_client.get("/api/ops/overlay")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="canopy-overlay-') and disposition.endswith('.patch"')
    text = response.text
    assert REAL_ID in text.split("+++ b/config/devices.yaml")[0]  # the overlay part keeps the ids (restore)
    diff = app_client.get("/api/ops/overlay", params={"part": "diff"}).text
    assert diff.startswith("--- a/config/devices.yaml\n+++ b/config/devices.yaml\n")
    assert REAL_ID not in diff and "metadevice_id" not in diff and "flags_proven_on" not in diff
    assert diff in text
    before = (CONFIG_DIR / "devices.yaml").read_text()
    after = _apply(before, diff)
    raw = yaml.safe_load(after)
    RegistryFile.model_validate(_apply_defaults(raw))
    got = {d["id"]: d for d in raw["devices"]}
    assert got["canopy-r1c1"]["cable_path"] == "serpentine_cols" and "metadevice_id" not in got["canopy-r1c1"]
    assert got["bench-kit"]["flags"] == {"t2_select": True} and "flags_proven_on" not in got["bench-kit"]
    assert got["spare-4"]["label"] == "Spare kit 4"
    assert "metadevice_id" not in got["spare-2"]
    # Comments and every untouched line stay as they are: only the edited entries differ.
    changed = [line for line in diff.splitlines() if line[:1] in "+-" and not line.startswith(("---", "+++"))]
    assert len([c for c in changed if c.startswith("-")]) == 1  # canopy-r1c1's one line, rewritten
    assert (
        len([c for c in changed if c.startswith("+")]) == 1 + 2 + 1
    )  # that line, bench-kit's flags, spare-4
    overlay = yaml.safe_load(app_client.get("/api/ops/overlay", params={"part": "yaml"}).text)
    assert overlay["devices"]["spare-2"]["metadevice_id"] == REAL_ID_2
    assert REAL_ID_2 not in diff
    assert app_client.get("/api/ops/overlay", params={"part": "zip"}).status_code == 422


def test_the_overlay_download_with_no_edits(app_client):
    text = app_client.get("/api/ops/overlay").text
    assert "Devices edited: none." in text and "Nothing to commit" in text


def test_only_ops_can_download_the_overlay(app_client_auth):
    client = app_client_auth
    ops = ("crew", "canopy")
    assert client.get("/api/ops/overlay", auth=ops).status_code == 200
    assert client.get("/api/ops/overlay").status_code == 401  # no credentials
    for role, grants in (("tester", ["bench-kit"]), ("kiosk", [])):
        link = client.post(
            "/api/roles/enrollments",
            json={"role": role, "name": f"{role} device", "grants": grants},
            auth=ops,
        ).json()
        assert client.post(link["path"], follow_redirects=False).status_code == 303
        status = client.get("/api/ops/overlay").status_code
        assert status in (401, 403), (role, status)
        client.cookies.clear()


def test_ops_links_to_the_overlay_download(app_client):
    page = app_client.get("/ops/").text
    assert 'href="/api/ops/overlay"' in page
