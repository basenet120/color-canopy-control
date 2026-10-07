"""Paris's device-bound FramebufferMapping end to end on the app + a 60-entry simulated controller (the real
SQHDK03's frame length with 30 pucks): unmapped → per-head refused; another kit's map → refused (D25); mapped →
paint, load, cards and the Lowcountry preview write the 30 heads into their mapped entries and every other entry
OFF, and the page reads them back as 30 heads."""

from __future__ import annotations

import pytest

from conductor import rows as R
from tests.test_module_api import THREE, _open, _post, _services, _wait

BENCH = "bench-kit"
OFF = R.FrameEntry(0, 0, 0, 0, 0, 0)
INDICES = [59 - i for i in range(30)]  # deliberately not 0–29: head i → entry 59 − i


@pytest.fixture
def fast(app_client):
    """Shorter readback waits and rate budget (as in test_module_api)."""
    policy = _services(app_client).settings.executor
    policy.readback_delay_s, policy.readback_interval_s, policy.min_write_interval_s = 0.05, 0.05, 0.1
    return app_client


def _sixty(client, mapping=None):
    services = _services(client)
    patch = {"profile": "sqhdk03-sim-60"}
    if mapping is not None:
        patch["framebuffer_mapping"] = mapping
    services.update_registry(BENCH, patch)
    services.sim.add_device(f"sim-{BENCH}", account="bench", profile="sqhdk03-sim-60")


def _frame(client, slot="custom-3"):
    view = {r.key: r.value for r in _services(client).sim.device_view(f"sim-{BENCH}")}
    return R.frame_entries(view[(R.FRAME_CLASS, slot)])


def test_a_60_entry_controller_without_a_map_refuses_per_head_frames(app_client, fast):
    _sixty(app_client)
    lease, state = _open(app_client)
    assert state["head_count"]["reported"] == 60 and "60 heads" in state["head_count"]["problem"]
    assert state["t1"]["allowed"] is False
    assert _post(app_client, "paint", lease, heads=THREE).status_code == 409


def test_another_kits_map_is_never_used(app_client, fast):
    _sixty(app_client, {"frame_length": 60, "indices": INDICES, "proven_on": "a-different-kit"})
    lease, state = _open(app_client)
    assert "mapping does not match" in state["head_count"]["problem"] and state["t1"]["allowed"] is False
    assert _post(app_client, "paint", lease, heads=THREE).status_code == 409


def test_a_mapped_controller_paints_its_heads_into_the_mapped_entries(app_client, fast):
    _sixty(app_client, {"frame_length": 60, "indices": INDICES, "proven_on": f"sim-{BENCH}"})
    lease, state = _open(app_client)
    assert state["head_count"]["mapped"] is True and state["t1"]["allowed"] is True
    painted = _post(app_client, "paint", lease, base="off", heads=THREE)
    assert painted.status_code == 200, painted.text
    frame = _wait(lambda: (f := _frame(app_client)) and f[59].r == 255 and f)
    lit = {59: "r", 52: "g", 37: "b"}  # heads 0, 7, 22
    assert len(frame) == 60 and all(getattr(frame[i], c) == 255 for i, c in lit.items())
    assert all(e == OFF for i, e in enumerate(frame) if i not in lit)
    st = app_client.get(f"/api/module/{BENCH}", params={"refresh": 1}).json()["state"]
    assert st["diff"] == [] and len(st["heads"]) == 30
    assert st["heads"][22]["reported"] == {"r": 0, "g": 0, "b": 255}
    loaded = _post(app_client, "slot", lease, slot="custom-3", action="load").json()["state"]["heads"]
    assert (loaded[0]["paint"]["r"], loaded[7]["paint"]["g"], loaded[22]["paint"]["b"]) == (255, 255, 255)
    for card, params in (("single-head", {"index": 0}), ("hue-ramp", {}), ("recipe-compare", {})):
        r = _post(app_client, "card", lease, card=card, params=params)
        assert r.status_code == 200 and r.json()["result"]["status"] == "applied", (card, r.text)
        assert all(e == OFF for e in _frame(app_client)[:30]), card
    preview = _post(
        app_client,
        "card",
        lease,
        card="lowcountry-preview",
        params={"mode": "rainbow-row-sunset", "position": "r1c1"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["result"]["tier"] == "canvas" and preview.json()["result"]["status"] == "applied"
    assert all(e == OFF for e in _frame(app_client)[:30])
