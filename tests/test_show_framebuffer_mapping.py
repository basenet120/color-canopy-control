"""The show path through a kit's controller map (D54, STATUS known gap 1), on the opt-in 60-entry simulated
controller (`sqhdk03-sim-60`: the real SQHDK03's frame length with 30 pucks attached).

- A canopy module with its own proven map takes T1 canvas looks through the show's own wiring (ShowWiring → the
  looks compiler → commissioning → the engine): a 60-entry frame with each head's colour at its mapped entry, in
  wire order, every other entry OFF, and a slot hash that stays put when the look is applied again. It is healthy
  (nothing flagged) and /ops draws its heads through the map.
- Without a map, a 60-entry module refuses T1 with the D54 text (controller capacity, not missing pucks) and still
  takes whole-strand looks. A map proven on another kit is never used (D25).
- /api/devices, the attention list and the 7 am head check say the same thing for mapped, unmapped, mismatched-map
  and short-frame modules.
"""

from __future__ import annotations

import pytest
from test_engine_review2 import _boot, _hold, _until

from conductor import framemap
from conductor import rows as R
from conductor.capabilities import Capabilities, detect
from conductor.clock import run_virtual
from conductor.config import DeviceEntry
from conductor.engine_api import CanopyPhase, PlanError, Tier
from conductor.framemap import MAP_MISMATCH, capacity_problem
from conductor.looks import CompileContext, compile_mode
from conductor.routes.ops import canopy_heads
from conductor.sim.profiles import profile_parts

MODULE = "canopy-r1c1"
ADDRESS = f"sim-{MODULE}"
INDICES = [59 - i for i in range(30)]  # deliberately not 0–29: head i → entry 59 − i
OFF = R.FrameEntry(0, 0, 0, 0, 0, 0)
CANVAS_MODE = "rainbow-row-sunset"
NO_MAP = capacity_problem(60, 30)
SHORT = "the device reports 20 heads but this module should have 30"
COMMISSION = (
    "gate_c_safe → address_probe_safe → record its framebuffer_mapping (runbook bench-prototype-01 §5a)"
)


def _sixty(services, device_id=MODULE, mapping=None, profile="sqhdk03-sim-60"):
    """Put `device_id` on another simulated controller (default: 60 entries), with `mapping` as its map."""
    patch = {"profile": profile}
    if mapping is not None:
        patch["framebuffer_mapping"] = mapping
    services.update_registry(device_id, patch)
    label = services.registry.get(device_id).label
    services.sim.add_device(f"sim-{device_id}", account="canopy", profile=profile, label=label)


def _map(proven_on=ADDRESS, indices=INDICES):
    return {"frame_length": 60, "indices": list(indices), "proven_on": proven_on}


def _stored(services, slot):
    """The slot as the (simulated) cloud stores it."""
    return {r.key: r.value for r in services.sim.cloud_view(ADDRESS)}[(R.FRAME_CLASS, slot)]


def _refusal(wiring, mode_id, tier):
    try:
        wiring.compile(mode_id, tier)
    except PlanError as e:
        return str(e)
    return None


# ---- the show's own wiring ---------------------------------------------------------------------------------------
def test_a_mapped_60_entry_module_takes_t1_canvas_looks_through_its_map(tmp_path):
    async def main():
        services, wiring, engine = await _boot(tmp_path, before_start=lambda s: _sixty(s, mapping=_map()))
        assert await _until(services.clock, lambda: wiring.last_commission is not None, 120) is not None
        slot = wiring.modes_file.modes[CANVAS_MODE].slot
        # What a 30-entry module at the same position shows, in wire order (no addresses: no map consulted).
        caps30 = detect(*profile_parts("sqhdk03-sim"))
        plain = compile_mode(
            CANVAS_MODE,
            Tier.T1,
            CompileContext(
                registry=services.registry,
                capabilities={e.id: caps30 for e in services.registry.canopy_modules()},
                config_dir=services.config_dir,
            ),
        ).modules[MODULE]
        await engine.request_mode(CANVAS_MODE, source="ops")
        await _hold(services, engine, 20)
        out = {
            "state": engine.state(),
            "plan": engine.current_plan().modules[MODULE],
            "snapshot": services.tracker.snapshot(ADDRESS),
            "stored": _stored(services, slot),
            "recorded": services.registry.get(MODULE).slot_hashes.get(slot),
            "heads": canopy_heads(services),
            "plain": plain,
        }
        await engine.request_mode("lowcountry-calm", source="ops")  # away and back: applied again
        await _hold(services, engine, 20)
        await engine.request_mode(CANVAS_MODE, source="ops")
        await _hold(services, engine, 20)
        out["again"] = _stored(services, slot)
        out["compiled_again"] = wiring.compile(CANVAS_MODE, Tier.T1).modules[MODULE].slot_hash
        out["state_again"] = engine.state()
        await wiring.close()
        await services.close()
        return out

    out = run_virtual(main)
    assert out["state"].tier == "canvas" and out["state"].phase is CanopyPhase.READY, out["state"]
    assert out["snapshot"]["problems"] == [] and out["snapshot"]["status"] == "ready"
    frame = R.frame_entries(out["stored"])
    heads = R.frame_entries(out["plain"].slot_value)
    assert len(frame) == 60 and len(heads) == 30
    assert [frame[i] for i in INDICES] == heads  # head i's colour at entry INDICES[i]
    assert all(e == OFF for i, e in enumerate(frame) if i not in INDICES)
    digest = R.slot_hash(out["stored"])
    assert (
        digest
        == out["plan"].slot_hash
        == out["recorded"]
        == R.slot_hash(out["again"])
        == out["compiled_again"]
    )
    assert out["state_again"].tier == "canvas"
    # previews and /ops: 30 heads in wire order, read through the map
    assert out["plan"].preview == out["plain"].preview
    module = next(m for m in out["heads"]["modules"] if m["device_id"] == MODULE)
    drawn = sorted((h for h in out["heads"]["heads"] if h["device_id"] == MODULE), key=lambda h: h["index"])
    assert module["heads"] == 30 and module["counts"]["match"] == 30
    assert [h["reported"] for h in drawn] == out["plain"].preview


def test_an_unmapped_60_entry_module_refuses_t1_with_the_d54_text_and_takes_whole_strand_looks(tmp_path):
    async def main():
        services, wiring, engine = await _boot(tmp_path, before_start=_sixty)
        assert await _until(services.clock, lambda: wiring.last_commission is not None, 120) is not None
        refused = _refusal(wiring, CANVAS_MODE, Tier.T1)
        await engine.request_mode(CANVAS_MODE, source="ops")
        await _hold(services, engine, 20)
        canvas_mode = engine.current_plan()
        canvas_ok = not R.compare(canvas_mode.modules[MODULE].expect, services.sim.device_view(ADDRESS))
        await engine.request_mode("lowcountry-calm", source="ops")
        await _hold(services, engine, 20)
        calm = engine.current_plan()
        calm_ok = not R.compare(calm.modules[MODULE].expect, services.sim.device_view(ADDRESS))
        out = (refused, canvas_mode.tier, canvas_ok, calm.tier, calm_ok)
        out += (services.tracker.snapshot(ADDRESS), wiring.last_commission)
        await wiring.close()
        await services.close()
        return out

    refused, canvas_tier, canvas_ok, calm_tier, calm_ok, snapshot, commission = run_virtual(main)
    assert refused == f"{MODULE}: {NO_MAP}"
    assert "missing" not in NO_MAP and "pucks" not in NO_MAP
    assert (
        canvas_tier is not Tier.T1 and canvas_ok
    )  # the mode fell to a whole-strand tier, shown on the module
    assert calm_tier is Tier.T3 and calm_ok  # a module-level look (one colour per strand) needs no map
    assert snapshot["problems"] == [NO_MAP] and snapshot["status"] == "needs_attention"
    skipped = {s["mode"]: s["reason"] for s in commission["skipped"]}
    assert skipped[CANVAS_MODE] == f"{MODULE}: {NO_MAP}"


def test_a_map_proven_on_another_kit_is_never_applied(tmp_path):
    async def main():
        services, wiring, _engine = await _boot(
            tmp_path, before_start=lambda s: _sixty(s, mapping=_map(proven_on="sim-canopy-r0c0"))
        )
        assert await _until(services.clock, lambda: wiring.last_commission is not None, 120) is not None
        entry = services.registry.get(MODULE)
        out = (
            _refusal(wiring, CANVAS_MODE, Tier.T1),
            wiring.context().frame_indices(entry, wiring.caps[MODULE]),
            services.tracker.snapshot(ADDRESS)["problems"],
            R.frame_entries(_stored(services, "custom-1")),
        )
        await wiring.close()
        await services.close()
        return out

    refused, indices, problems, custom_1 = run_virtual(main)
    assert refused == f"{MODULE}: {MAP_MISMATCH}" and indices is None
    assert problems == [MAP_MISMATCH]
    assert any(e != OFF for e in custom_1[30:])  # nothing was written through the other kit's map


# ---- /api/devices, the attention list and the 7 am check -----------------------------------------------------------
def _setup_routes(app_client):
    services = app_client.app.state.services
    _sixty(services, "canopy-r0c0", _map("sim-canopy-r0c0", range(30)))  # mapped (Kingston's map)
    _sixty(services, "canopy-r0c1")  # 60 entries, no map
    _sixty(services, "canopy-r0c2", _map("sim-canopy-r0c0", range(30)))  # another kit's map
    _sixty(services, "canopy-r1c0", profile="string-20seg")  # 20 entries for 30 heads
    body = app_client.get("/api/devices", params={"refresh": 1}).json()
    return services, {d["id"]: d for d in body["devices"]}


def _heads_item(app_client):
    items = app_client.get("/api/ops/checklist").json()["items"]
    return next(i for i in items if i["id"] == "heads")


def test_devices_attention_and_the_7am_check_for_mapped_unmapped_mismatched_and_short_modules(app_client):
    services, devices = _setup_routes(app_client)
    expected = {"canopy-r0c0": None, "canopy-r0c1": NO_MAP, "canopy-r0c2": MAP_MISMATCH, "canopy-r1c0": SHORT}
    assert {d: devices[d]["head_count_problem"] for d in expected} == expected
    assert devices["canopy-r0c0"]["mapped"] is True and devices["canopy-r0c0"]["frame_length"] == 60
    assert devices["canopy-r0c1"]["mapped"] is False and devices["canopy-r0c1"]["frame_length"] == 60
    assert (
        devices["canopy-r1c0"]["frame_length"] == 20 and devices["canopy-r1c1"]["head_count_problem"] is None
    )

    messages = [a["message"] for a in app_client.get("/api/ops/summary").json()["attention"]]
    labels = {d: services.registry.get(d).label for d in expected}
    for device_id, problem in expected.items():
        lines = [m for m in messages if m.startswith(f"{labels[device_id]}: ")]
        assert lines == ([f"{labels[device_id]}: {problem}"] if problem else []), (device_id, messages)

    item = _heads_item(app_client)
    assert item["status"] == "fail"
    for device_id, problem in expected.items():
        assert (f"{labels[device_id]}: {problem}" in item["detail"]) is bool(problem), device_id
    assert "power-cycle" in item["fix"] and "Never cut" in item["fix"] and COMMISSION in item["fix"]


def test_the_7am_check_never_says_power_cycle_for_a_kit_that_only_lacks_its_map(app_client):
    services = app_client.app.state.services
    _sixty(services, "canopy-r0c1")
    app_client.get("/api/devices", params={"refresh": 1})
    item = _heads_item(app_client)
    assert item["status"] == "fail" and NO_MAP in item["detail"]
    assert COMMISSION in item["fix"] and "power-cycle" not in item["fix"] and "cut" not in item["fix"]


def test_a_mapped_kit_with_fewer_entries_than_heads_still_gets_the_power_cycle_fix(app_client):
    """A saved map can't explain a short frame: the kit keeps today's text and fix, and T1 is refused with it."""
    services = app_client.app.state.services
    _sixty(services, "canopy-r0c0", _map("sim-canopy-r0c0", range(30)), profile="string-20seg")
    device = next(
        d
        for d in app_client.get("/api/devices", params={"refresh": 1}).json()["devices"]
        if d["id"] == "canopy-r0c0"
    )
    assert device["head_count_problem"] == SHORT and device["mapped"] is False
    item = _heads_item(app_client)
    assert item["status"] == "fail" and SHORT in item["detail"] and MAP_MISMATCH not in item["detail"]
    assert "power-cycle" in item["fix"] and "Never cut" in item["fix"] and COMMISSION not in item["fix"]
    caps30 = detect(*profile_parts("sqhdk03-sim"))
    caps = {e.id: caps30 for e in services.registry.canopy_modules()}
    caps["canopy-r0c0"] = detect(*profile_parts("string-20seg"))
    ctx = CompileContext(
        registry=services.registry,
        capabilities=caps,
        config_dir=services.config_dir,
        addresses={e.id: f"sim-{e.id}" for e in services.registry.canopy_modules()},
    )
    with pytest.raises(PlanError) as refused:
        compile_mode(CANVAS_MODE, Tier.T1, ctx)
    assert str(refused.value) == f"canopy-r0c0: {SHORT}"


def test_the_one_rule_needs_every_slot_to_report_one_length():
    """Slots that disagree void a map even when the segment hint equals its frame length — and keep their own text
    (and fix), as do a short frame and no length at all; only a map that fails on its own terms is MAP_MISMATCH."""
    entry = DeviceEntry(
        id=MODULE, role="canopy_module", account="canopy", expected_heads=30, framebuffer_mapping=_map()
    )
    lengths = {"custom-1": 60, "custom-2": 30, "custom-3": 60}
    mixed = Capabilities(segment_hint=60, framebuffer_lengths=lengths)
    assert mixed.framebuffer_length == 60 and framemap.frame_indices(entry, ADDRESS, mixed, 30) is None
    check = framemap.head_check(entry, ADDRESS, mixed, 30)
    assert (check.kind, check.problem) == (
        "other",
        "slots report different frame lengths [30, 60] — per-head writes refused",
    )
    no_length = framemap.head_check(entry, ADDRESS, Capabilities(), 30)
    assert (no_length.kind, no_length.problem) == (
        "other",
        "the device reports no frame length — per-head writes refused",
    )
    sixty = Capabilities(segment_hint=60, framebuffer_lengths=dict.fromkeys(lengths, 60))
    assert framemap.head_check(entry, ADDRESS, sixty, 30).mapped
    assert framemap.head_check(entry, "sim-canopy-r0c0", sixty, 30) == framemap.HeadCheck(
        MAP_MISMATCH, "map_mismatch"
    )
    unmapped = entry.model_copy(update={"framebuffer_mapping": None})
    assert framemap.head_check(unmapped, ADDRESS, mixed, 30).kind == "other"
    # a segment hint but no slot readback: the length is only the hint, so the cause is the readback (Refresh), not
    # a map that fails on its own terms
    hint_only = Capabilities(segment_hint=60)
    assert hint_only.framebuffer_length == 60 and not hint_only.framebuffer_lengths
    assert framemap.head_check(entry, ADDRESS, hint_only, 30) == framemap.HeadCheck(
        framemap.NO_SLOT_LENGTHS, "other"
    )


def test_the_7am_check_passes_a_mapped_kit_and_says_the_head_check_is_visual(app_client):
    services = app_client.app.state.services
    _sixty(services, "canopy-r0c0", _map("sim-canopy-r0c0", range(30)))
    app_client.get("/api/devices", params={"refresh": 1})
    item = _heads_item(app_client)
    assert item["status"] == "pass", item
    assert "1 of 9 modules reach their heads through their own proven controller map" in item["detail"]
    assert "60-entry frame is the controller's capacity, not a count of lit heads" in item["detail"]
    assert "count the lit heads" in item["note"]
