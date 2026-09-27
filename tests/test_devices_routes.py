"""GET /api/devices, /api/devices/{id} and /api/ops/summary on the simulator: registry + where writes go +
tracker status + lock holder + capabilities from the device's own profile + head-count problems."""

from __future__ import annotations


def _device(body: dict, device_id: str) -> dict:
    return next(d for d in body["devices"] if d["id"] == device_id)


def test_devices_list_registry_targets_and_capabilities(app_client):
    body = app_client.get("/api/devices").json()
    assert body["simulated"] is True and body["mode"] == "sim"
    assert len(body["devices"]) == 13  # bench kit, nine modules, three spares
    kit = _device(body, "bench-kit")
    assert (
        kit["label"] and kit["role"] == "bench" and kit["account"] == "bench" and kit["expected_heads"] == 30
    )
    assert kit["orientation"] == "A" and kit["flags"] == {
        "t2_select": None,
        "t1_frames": None,
        "readback_device_level": None,
    }
    assert kit["has_metadevice_id"] is False and kit["metadevice_id"] is None and kit["allowlisted"] is False
    for purpose in ("engine", "bench"):
        assert kit["purposes"][purpose] == {"driver": "sim", "simulated": True, "address": "sim-bench-kit"}
    assert kit["simulated"] is True and kit["status"] == "unknown" and kit["lock"] is None
    caps = kit["capabilities"]
    assert caps["framebuffer_length"] == 30 and caps["individual"] is True
    assert caps["slots"] == ["custom-1", "custom-2", "custom-3"]
    assert kit["capabilities_simulated"] is True and kit["capabilities_error"] is None
    assert kit["head_count_problem"] is None
    module = _device(body, "canopy-r1c1")
    assert module["module"] == "r1c1" and module["role"] == "canopy_module"


def test_capabilities_are_cached_until_refresh(app_client, monkeypatch):
    services = app_client.app.state.services
    calls = []
    original = services.sim.fetch_profile

    async def counting(address, *, purpose):
        calls.append((address, str(purpose)))
        return await original(address, purpose=purpose)

    monkeypatch.setattr(services.sim, "fetch_profile", counting)
    app_client.get("/api/devices")
    assert len(calls) == 13 and all(p == "bench" for _, p in calls)
    app_client.get("/api/devices")
    app_client.get("/api/devices/bench-kit")
    assert len(calls) == 13
    app_client.get("/api/devices/bench-kit?refresh=1")
    assert len(calls) == 14
    app_client.get("/api/devices?refresh=1")
    assert len(calls) == 27


def test_a_failing_profile_read_is_reported_not_raised(app_client, monkeypatch):
    services = app_client.app.state.services

    async def broken(address, *, purpose):
        raise RuntimeError("Hubspace answered 500")

    monkeypatch.setattr(services.sim, "fetch_profile", broken)
    kit = app_client.get("/api/devices/bench-kit").json()
    assert kit["capabilities"] is None and "Hubspace answered 500" in kit["capabilities_error"]
    assert kit["head_count_problem"] is None


def test_lock_holder_is_shown_without_the_lease_token(app_client):
    services = app_client.app.state.services
    lease = services.locks.acquire("sim-bench-kit", "module page · alon")
    body = app_client.get("/api/devices")
    kit = _device(body.json(), "bench-kit")
    assert kit["lock"]["owner"] == "module page · alon" and kit["lock"]["address"] == "sim-bench-kit"
    assert lease.token not in body.text
    summary = app_client.get("/api/ops/summary")
    [lock] = summary.json()["locks"]
    assert lock["logical_id"] == "bench-kit" and lock["owner"] == "module page · alon"
    assert lease.token not in summary.text


def test_device_detail_and_unknown_id(app_client):
    kit = app_client.get("/api/devices/bench-kit").json()
    assert kit["id"] == "bench-kit" and kit["enabled"] is True
    assert kit["tracker"]["address"] == "sim-bench-kit" and kit["tracker"]["status"] == "unknown"
    r = app_client.get("/api/devices/nope")
    assert r.status_code == 404 and "registry" in r.json()["detail"]


def test_head_count_problem_and_tracker_problems_reach_needs_attention(app_client):
    services = app_client.app.state.services
    services.update_registry("bench-kit", {"expected_heads": 29})
    kit = app_client.get("/api/devices/bench-kit").json()
    assert kit["head_count_problem"] == "the device reports 30 heads but this module should have 29"
    services.tracker.flag_problem("sim-canopy-r0c0", "Only 12 of 30 heads detected")
    summary = app_client.get("/api/ops/summary").json()
    messages = [a["message"] for a in summary["attention"]]
    assert any("reports 30 heads but this module should have 29" in m for m in messages)
    assert any("Only 12 of 30 heads detected" in m for m in messages)
    links = {a["link"] for a in summary["attention"]}
    assert "/ops/module/?device=bench-kit" in links and "/ops/module/?device=canopy-r0c0" in links
    module = _device(app_client.get("/api/devices").json(), "canopy-r0c0")
    assert module["problems"] == ["Only 12 of 30 heads detected"]


def test_summary_on_the_simulator(app_client):
    summary = app_client.get("/api/ops/summary").json()
    v = summary["version"]
    assert v["mode"] == "sim" and v["simulated"] is True and v["version"] and v["commit"]
    assert summary["attention"] == [] and summary["problems"] == []
    assert summary["accounts"]["simulated"] is True and summary["accounts"]["accounts"]
    assert summary["locks"] == []
    [budget] = summary["budget"]
    assert budget["driver"] == "sim" and budget["simulated"] is True
    assert budget["budget"]["min_write_interval_s"] >= 1.0
    assert "writes" in summary["sim"]["stats"] and "outage" in str(summary["sim"]["faults"]).lower()


def test_summary_flags_accounts_that_need_attention(app_client):
    services = app_client.app.state.services
    services.roles.problem = "roles/sessions.json couldn't be read; enrolled devices must be re-enrolled."
    services.problems.append("CONDUCTOR_SECRET_KEY is not set.")

    original = services.accounts.status

    def status():
        rows = original()
        rows[0].needs_reauth = True
        rows[0].unknown_devices = ["md-stranger"]
        return rows

    services.accounts.status = status
    summary = app_client.get("/api/ops/summary").json()
    messages = [a["message"] for a in summary["attention"]]
    assert "CONDUCTOR_SECRET_KEY is not set." in summary["problems"]
    assert any("re-enrolled" in m for m in summary["problems"])
    assert any("needs a new sign-in" in m for m in messages)
    assert any("aren't registered" in m and "read-only" in m for m in messages)
    assert "md-stranger" not in str(messages)


def test_ops_landing_page_is_served(app_client):
    page = app_client.get("/ops/")
    assert page.status_code == 200 and "Needs attention" in page.text and "/ops/ops.js" in page.text
    for asset in ("/ops/ops.js", "/ops/dom.js", "/ops/ops.css", "/ops/accounts/accounts.js"):
        r = app_client.get(asset)
        assert r.status_code == 200, asset
        assert "http://" not in r.text and "https://" not in r.text, f"{asset} references an external URL"
