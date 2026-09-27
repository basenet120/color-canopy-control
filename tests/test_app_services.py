from conductor.gate import Purpose


def test_app_boots_on_the_simulator(app_client):
    v = app_client.get("/api/version").json()
    assert v["simulated"] is True and v["mode"] == "sim" and v["bench_live"] is False
    services = app_client.app.state.services
    target = services.target("bench-kit", Purpose.BENCH)
    assert target.simulated and target.address == "sim-bench-kit"
    assert app_client.get("/ops/").status_code == 200


def test_auth_applies_to_new_surfaces(app_client_auth):
    assert app_client_auth.get("/ops/").status_code == 401
    assert app_client_auth.get("/ops/", auth=("crew", "canopy")).status_code == 200
    assert app_client_auth.get("/healthz").status_code == 200


def test_shutdown_releases_module_locks_and_stops_bench_runs(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from conductor.app import app

    monkeypatch.setenv("CONDUCTOR_AUTH", "off")
    monkeypatch.setenv("CONDUCTOR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONDUCTOR_SIM_SPEED", "fast")
    with TestClient(app) as client:
        assert client.post("/api/module/bench-kit/open", json={}).status_code == 200
        services = client.app.state.services
        assert services.locks.is_locked("sim-bench-kit")
    assert not services.locks.is_locked("sim-bench-kit")
