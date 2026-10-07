"""Paris's device-bound FramebufferMapping end to end on the app + a 60-entry simulated controller (the real
SQHDK03's frame length with 30 pucks): unmapped → per-head refused; another kit's map → refused (D25); mapped →
paint, load, cards and the Lowcountry preview write the 30 heads into their mapped entries and every other entry
OFF, and the page reads them back as 30 heads; effect start/restart keeps every unmapped entry as read."""

from __future__ import annotations

import pytest

from conductor import rows as R
from conductor.framemap import MAP_MISMATCH, capacity_problem
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


def _stored(client, slot):
    """The slot as the (simulated) cloud stores it: what the last write sent, without the device's apply delay."""
    return {r.key: r.value for r in _services(client).sim.cloud_view(f"sim-{BENCH}")}[(R.FRAME_CLASS, slot)]


def test_a_60_entry_controller_without_a_map_refuses_per_head_frames(app_client, fast):
    _sixty(app_client)
    lease, state = _open(app_client)
    assert state["head_count"]["reported"] == 60 and state["head_count"]["problem"] == capacity_problem(
        60, 30
    )
    assert state["t1"]["allowed"] is False
    assert _post(app_client, "paint", lease, heads=THREE).status_code == 409


def test_another_kits_map_is_never_used(app_client, fast):
    _sixty(app_client, {"frame_length": 60, "indices": INDICES, "proven_on": "a-different-kit"})
    lease, state = _open(app_client)
    assert state["head_count"]["problem"] == MAP_MISMATCH and state["t1"]["allowed"] is False
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


def test_effect_start_and_restart_keep_unmapped_entries_as_read_and_paint_writes_them_off(app_client, fast):
    """A slot's pattern may fill all 60 entries (whether one painted in the Hubspace app does is unknown: none has
    been dumped yet). Starting or restarting its effect from the page rewrites the header only: entries 30–59, which
    no head is mapped to on Kingston's map, stay as read, so the slot's canonical hash (07 §6) doesn't move.
    Painting still writes them OFF."""
    _sixty(app_client, {"frame_length": 60, "indices": list(range(30)), "proven_on": f"sim-{BENCH}"})
    services = _services(app_client)
    services.settings.effects.enabled["motionEffect"][1] = "scroll (benched)"
    painted = R.frame_value(None, [R.FrameEntry(10 + i, 0, 0) for i in range(60)], expected_length=60)
    services.sim.cloud.device(f"sim-{BENCH}").cloud[(R.FRAME_CLASS, "custom-1")] = painted
    as_read = R.frame_entries(painted)
    lease, _ = _open(app_client)

    restart = _post(app_client, "effects", lease, slot="custom-1", action="restart")
    assert restart.status_code == 200 and restart.json()["result"]["status"] == "applied", restart.text
    assert R.frame_entries(_stored(app_client, "custom-1")) == as_read
    start = _post(app_client, "effects", lease, slot="custom-1", action="start", motionEffect=1)
    assert start.status_code == 200 and start.json()["result"]["status"] == "applied", start.text
    stored = _stored(app_client, "custom-1")
    assert R.slot_effects(stored)["motionEffect"] == 1 and R.frame_entries(stored) == as_read
    assert R.slot_hash(stored) == R.slot_hash(painted)

    painted_now = _post(app_client, "paint", lease, slot="custom-1", base="slot", heads=THREE)
    assert painted_now.status_code == 200 and painted_now.json()["result"]["status"] == "applied"
    frame = R.frame_entries(_stored(app_client, "custom-1"))
    assert all(e == OFF for e in frame[30:])
    assert (frame[0].r, frame[7].g, frame[22].b) == (255, 255, 255) and frame[1] == as_read[1]
