"""/api/bench/* through the real app on the SimDriver, and the /ops/bench pages."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from conductor.anonymize import check_clean
from conductor.bench.jobs import question

Q_GATE_B = question("gate_b", {"slot": "custom-2"})

ROOT = Path(__file__).resolve().parent.parent
BENCH_WEB = ROOT / "web" / "ops" / "bench"


def wait_for(client, run_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = client.get(f"/api/bench/runs/{run_id}").json()
        if record["status"] != "running":
            return record
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} didn't finish in {timeout} s")


def start(client, job: str, params: dict | None = None, device: str = "bench-kit") -> str:
    r = client.post("/api/bench/run", json={"job": job, "device_id": device, "params": params or {}})
    assert r.status_code == 202, r.text
    assert r.json()["simulated"] is True and r.json()["driver"] == "sim"
    return r.json()["run_id"]


def test_jobs_lists_specs_and_devices(app_client):
    data = app_client.get("/api/bench/jobs").json()
    names = [j["name"] for j in data["jobs"]]
    assert names[:3] == ["gate_a", "index_order", "gate_b"] and "restore" in names
    gate_b = next(j for j in data["jobs"] if j["name"] == "gate_b")
    assert gate_b["params"] == {"slot": "custom-2"} and gate_b["writes"] is True and gate_b["look_for"]
    kit = data["devices"][0]
    assert kit["device_id"] == "bench-kit" and kit["simulated"] is True and kit["driver"] == "sim"
    assert kit["locked_by"] is None and kit["available"] is True
    assert data["mode"] == "sim" and data["bench_live"] is False


def test_gate_a_run_export_and_profile_download(app_client):
    run_id = start(app_client, "gate_a")
    record = wait_for(app_client, run_id)
    assert record["result"]["verdict"] == "pass" and record["simulated"] is True
    assert record["steps"][0]["message"].startswith("SIMULATED")

    export = app_client.get(f"/api/bench/runs/{run_id}/anonymized")
    assert export.status_code == 200 and export.headers["cache-control"] == "no-store"
    text = export.text
    assert "sim-bench-kit" not in text and "sim-" not in json.dumps(export.json()["address"])
    assert check_clean(text) == []
    assert export.json()["device_id"] == "bench-kit" and export.json()["note"]

    profile = app_client.get(f"/api/bench/runs/{run_id}/profile.json")
    assert profile.status_code == 200
    assert 'filename="sqhdk03.json"' in profile.headers["content-disposition"]
    body = profile.json()
    assert body["functions"] and body["friendly_name"] == "<name>"
    assert "sim-bench-kit" not in profile.text and check_clean(profile.text) == []

    listed = app_client.get("/api/bench/runs").json()["runs"]
    assert listed[0]["run_id"] == run_id and listed[0]["verdict"] == "pass"


def test_a_locked_device_is_refused_with_409(app_client):
    services = app_client.app.state.services
    lease = services.locks.acquire("sim-bench-kit", "module page · alon")
    try:
        r = app_client.post("/api/bench/run", json={"job": "gate_a", "device_id": "bench-kit"})
        assert r.status_code == 409 and "module page" in r.json()["detail"]
        kit = app_client.get("/api/bench/jobs").json()["devices"][0]
        assert kit["locked_by"] == "module page · alon"
    finally:
        services.locks.release("sim-bench-kit", lease.token)


def test_a_busy_device_is_refused_while_a_run_is_going(app_client):
    run_id = start(app_client, "restore")
    r = app_client.post("/api/bench/run", json={"job": "gate_a", "device_id": "bench-kit"})
    assert r.status_code == 409 and "already running" in r.json()["detail"]
    wait_for(app_client, run_id)
    assert app_client.app.state.services.locks.holder("sim-bench-kit") is None


def test_bad_requests(app_client):
    for body in (
        {"job": "gate_z", "device_id": "bench-kit"},
        {"job": "gate_a", "device_id": "no-kit"},
        {"job": "gate_b", "device_id": "bench-kit", "params": {"nope": 1}},
    ):
        r = app_client.post("/api/bench/run", json=body)
        assert r.status_code == 400 and r.json()["detail"], body
    assert app_client.get("/api/bench/runs/not-a-run").status_code == 404
    assert app_client.get("/api/bench/runs/..%2F..%2Fetc").status_code == 404
    assert app_client.get("/api/bench/runs/nothing/profile.json").status_code == 404


def test_answers_and_flags(app_client):
    services = app_client.app.state.services
    run_id = start(app_client, "gate_b")
    record = wait_for(app_client, run_id)
    assert record["result"]["flags"] == {"t2_select": True} and record["result"]["ask_human"] == [Q_GATE_B]

    r = app_client.post(f"/api/bench/runs/{run_id}/answers", json={"answers": {"Something else?": "yes"}})
    assert r.status_code == 400
    r = app_client.post(f"/api/bench/runs/{run_id}/flags")
    assert r.status_code == 409 and "simulator" in r.json()["detail"]
    assert services.registry.get("bench-kit").flags.t2_select is None

    r = app_client.post(f"/api/bench/runs/{run_id}/answers", json={"answers": {Q_GATE_B: "yes"}})
    assert r.status_code == 200 and r.json()["answers"] == {Q_GATE_B: "yes"}
    assert r.json()["confirmed_verdict"] == "pass"
    # still refused: a simulated run never changes the registry, answered or not
    assert app_client.post(f"/api/bench/runs/{run_id}/flags").status_code == 409

    # the same run as if it had been on the real kit: applied once the answer is yes
    path = Path(services.data) / "bench" / f"{run_id}.json"
    saved = json.loads(path.read_text())
    saved.update(simulated=False, answers={})
    path.write_text(json.dumps(saved))
    r = app_client.post(f"/api/bench/runs/{run_id}/flags")
    assert r.status_code == 409 and "Yes first" in r.json()["detail"]
    app_client.post(f"/api/bench/runs/{run_id}/answers", json={"answers": {Q_GATE_B: "yes"}})
    r = app_client.post(f"/api/bench/runs/{run_id}/flags")
    assert r.status_code == 200 and r.json()["flags_applied"]["flags"] == {"t2_select": True}
    assert services.registry.get("bench-kit").flags.t2_select is True


def test_server_time(app_client):
    before = time.time() * 1000
    ms = app_client.get("/api/bench/time").json()["epoch_ms"]
    assert before - 5 <= ms <= time.time() * 1000 + 5


def test_bench_is_ops_only(app_client_auth):
    assert app_client_auth.get("/api/bench/jobs").status_code == 401
    assert (
        app_client_auth.post("/api/bench/run", json={"job": "gate_a", "device_id": "bench-kit"}).status_code
        == 401
    )
    ok = app_client_auth.get("/api/bench/jobs", auth=("crew", "canopy"))
    assert ok.status_code == 200
    services = app_client_auth.app.state.services
    _, path = services.roles.create_enrollment("tester", "VWBD crew", ["bench-kit"])
    tester = app_client_auth.post(
        path, follow_redirects=False
    )  # the client keeps the tester's session cookie
    assert tester.status_code == 303
    assert app_client_auth.get("/ops/module/").status_code != 401  # the tester's cookie works where it may go
    for url in ("/api/bench/jobs", "/ops/bench/", "/api/bench/time"):
        assert app_client_auth.get(url).status_code in (401, 403), url


def test_the_pages_are_served_and_vendored(app_client):
    for url, needles in (
        (
            "/ops/bench/",
            ("/ops/bench/bench.js", "/static/base.css", "SIMULATED", "Nothing here touches the real kit"),
        ),
        ("/ops/bench/clock/", ("/ops/bench/clock/clock.js", "/static/base.css", 'id="time"')),
    ):
        r = app_client.get(url)
        assert r.status_code == 200, url
        for needle in needles:
            assert needle in r.text, (url, needle)
    for path in BENCH_WEB.rglob("*"):
        if path.suffix in (".html", ".js", ".css"):
            text = path.read_text()
            assert not re.search(r"https?://", text), f"{path.name} references an external URL"
            assert "@import" not in text and "@font-face" not in text
    js = (BENCH_WEB / "bench.js").read_text()
    assert (
        'from "/static/app.js"' in js
        and 'from "/static/app.js"' in (BENCH_WEB / "clock" / "clock.js").read_text()
    )
    assert "innerHTML" not in js and "connectLive" in js and "simBadge" in js
    clock = (BENCH_WEB / "clock" / "clock.js").read_text()
    assert "requestAnimationFrame" in clock and "wakeLock" in clock and "/api/bench/time" in clock
