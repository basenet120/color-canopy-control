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
