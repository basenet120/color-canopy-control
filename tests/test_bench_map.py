"""Apply map (D58, STATUS gap 9): a kit's controller map recorded in the app from its own two answered address probes.

The proof rule, all on the kit at the run's address: Gate C (T1) applied there; its latest markers probe answered Yes
(head 1 red, head 2 green, head 30 blue, nothing else); its latest probe of entries 30–59 answered Yes (nothing lit);
both saw the same frame length L > heads; heads = the module's = `expected_heads`. Then, and only then, the identity
map {frame_length: L, indices: 0–29, proven_on: that address} goes into the ops overlay. Everything else is refused.

The probes run on the SimDriver with the opt-in 60-entry profile (`sqhdk03-sim-60`); a simulated run is never
evidence, so the seam (as in the apply_flags tests) rewrites a saved run as non-simulated, keeping its address.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest
from test_bench_cli import over, run_cli
from test_bench_jobs import bench
from test_bench_routes import wait_for
from test_engine_review2 import _boot, _until

from conductor import framemap
from conductor import rows as R
from conductor.bench import cli
from conductor.bench.jobs import Q_ADDRESS, Q_MARKERS
from conductor.bench.runner import BenchRefused, runner_of
from conductor.capabilities import detect
from conductor.clock import run_virtual
from conductor.config import NOT_FOR_GIT, overlay_diff
from conductor.engine_api import PlanError, Tier
from conductor.framemap import capacity_problem
from conductor.gate import Purpose

ROOT = Path(__file__).resolve().parent.parent
KIT = "bench-kit"
ADDRESS = f"sim-{KIT}"
IDENTITY = {"frame_length": 60, "indices": list(range(30)), "proven_on": ADDRESS}
MARKERS = {"pattern": "markers", "duration_s": 4.0}
DARK = {"start": 30, "count": 30, "duration_s": 4.0}


def sixty(services, device_id: str = KIT, *, t1: bool = True) -> None:
    """Put `device_id` on the 60-entry simulated controller, with Gate C (T1) applied on it unless `t1` is False."""
    patch: dict = {"profile": "sqhdk03-sim-60"}
    if t1:
        patch.update(flags={"t1_frames": True}, flags_proven_on=f"sim-{device_id}")
    services.update_registry(device_id, patch)
    entry = services.registry.get(device_id)
    services.sim.add_device(
        entry.sim_address, account=entry.account, profile="sqhdk03-sim-60", label=entry.label
    )


def as_real(services, record: dict, **changes) -> None:
    """The seam: a saved run rewritten as if it had run on the real kit (same address and driver unless changed)."""
    path = Path(services.data) / "bench" / f"{record['run_id']}.json"
    saved = json.loads(path.read_text())
    saved.update(simulated=False, **changes)
    path.write_text(json.dumps(saved))


def edit_saved(services, run_id: str, change) -> None:
    path = Path(services.data) / "bench" / f"{run_id}.json"
    saved = json.loads(path.read_text())
    change(saved)
    path.write_text(json.dumps(saved))


async def probe(services, runner, params: dict, answer: str | None = "yes", device_id: str = KIT) -> str:
    """One address_probe_safe run, made real, and its one question answered (None: left unanswered)."""
    record = await runner.run("address_probe_safe", device_id, params)
    assert record["result"]["verdict"] == "needs_eyes", record["result"]
    as_real(services, record)
    if answer is not None:
        runner.answer(record["run_id"], {record["result"]["ask_human"][0]: answer})
    return record["run_id"]


def refusal(runner, run_id: str) -> str:
    proposal = runner.map_proposal(run_id)
    assert proposal["ready"] is False and proposal["mapping"] is None
    return proposal["reason"]


async def refused_apply(runner, run_id: str) -> str:
    with pytest.raises(BenchRefused) as e:
        await runner.apply_map(run_id)
    return str(e.value)


# ---- the questions the probe asks -----------------------------------------------------------------------------------
def test_the_two_map_probes_ask_yes_no_questions_and_other_ranges_still_ask_where(tmp_path):
    async def body(services, runner):
        sixty(services)
        out = {}
        for name, params in (("markers", MARKERS), ("dark", DARK), ("low", {"start": 14, "count": 1})):
            record = await runner.run("address_probe_safe", KIT, {"duration_s": 4.0, **params})
            out[name] = record["result"]["ask_human"]
        return out

    out = bench(tmp_path, body)
    assert out["markers"] == [Q_MARKERS] and "head 1 red, head 2 green and head 30 blue" in Q_MARKERS
    assert out["dark"] == [
        "Once the before look ended, did every head stay dark (nothing lit) while entries 30–59 were held?"
    ]
    assert out["low"] == [Q_ADDRESS]


# ---- the happy path -------------------------------------------------------------------------------------------------
def test_apply_map_records_exactly_the_identity_map_in_the_ops_overlay_and_writes_nothing(tmp_path):
    devices_yaml = (ROOT / "config" / "devices.yaml").read_bytes()

    async def body(services, runner):
        sixty(services)
        markers = await probe(services, runner, MARKERS)
        dark = await probe(services, runner, DARK)
        proposal = runner.map_proposal(dark)
        writes = services.sim.stats()["writes"]
        record = await runner.apply_map(dark)
        after = services.sim.stats()["writes"] - writes
        entry = services.registry.get(KIT)
        target = services.target(KIT, Purpose.BENCH)
        info = await target.driver.fetch_profile(ADDRESS, purpose=Purpose.BENCH)
        caps = detect(info.functions, info.states)
        return {
            "proposal": proposal,
            "record": record,
            "partner": runner.get(markers),
            "runs": (markers, dark),
            "writes": after,
            "mapping": entry.framebuffer_mapping.model_dump(),
            "indices": framemap.frame_indices(entry, ADDRESS, caps, 30),
            "check": framemap.head_check(entry, ADDRESS, caps, 30).kind,
            "diff": overlay_diff(services.registry),
            "overlay": (tmp_path / "overrides" / "registry.yaml").read_text(),
        }

    out = bench(tmp_path, body)
    markers, dark = out["runs"]
    assert out["proposal"]["ready"] is True and out["proposal"]["mapping"] == IDENTITY
    assert out["proposal"]["runs"] == {"markers": markers, "dark": dark}
    reason = out["proposal"]["reason"]
    assert "entries 0–29 drive heads 1–30" in reason and "30–59 drive nothing" in reason
    assert "sends nothing to the lights" in reason and markers in reason and dark in reason
    assert out["mapping"] == IDENTITY  # exactly the identity, proven on this kit's own address
    assert out["indices"] == list(range(30)) and out["check"] == "mapped"  # the one validity rule accepts it
    assert out["writes"] == 0  # applying a map sends nothing to any device
    for record in (out["record"], out["partner"]):
        assert record["map_applied"]["mapping"] == IDENTITY and record["map_applied"]["previous"] is None
        assert record["map_applied"]["runs"] == [markers, dark] and record["map_revoked"] is None
    # ops overlay only: never git-tracked config, and left out of the diff to commit back
    assert "framebuffer_mapping" in NOT_FOR_GIT and "framebuffer_mapping" in out["overlay"]
    assert (ROOT / "config" / "devices.yaml").read_bytes() == devices_yaml
    assert KIT in out["diff"].left_out and "framebuffer_mapping" not in out["diff"].after


def test_applying_from_either_run_of_the_pair_records_the_same_map(tmp_path):
    async def body(services, runner):
        sixty(services)
        markers = await probe(services, runner, MARKERS)
        await probe(services, runner, DARK)
        record = await runner.apply_map(markers)
        return record["map_applied"]["mapping"], services.registry.get(KIT).framebuffer_mapping.model_dump()

    applied, saved = bench(tmp_path, body)
    assert applied == saved == IDENTITY


def test_a_second_apply_is_refused_as_already_recorded(tmp_path):
    async def body(services, runner):
        sixty(services)
        await probe(services, runner, MARKERS)
        dark = await probe(services, runner, DARK)
        await runner.apply_map(dark)
        return refusal(runner, dark), await refused_apply(runner, dark)

    proposal, applied = bench(tmp_path, body)
    assert "already recorded" in proposal and "already recorded" in applied


# ---- every refusal ----------------------------------------------------------------------------------------------------
def _refused(tmp_path, setup) -> tuple[str, str, object]:
    """Run `setup(services, runner) -> run_id`; return the proposal's reason, apply_map's refusal and the saved map."""

    async def body(services, runner):
        run_id = await setup(services, runner)
        reason = refusal(runner, run_id)
        refused = await refused_apply(runner, run_id)
        return reason, refused, services.registry.get(KIT).framebuffer_mapping

    reason, refused, mapping = bench(tmp_path, body)
    assert mapping is None, "a refused map must leave the registry alone"
    return reason, refused, mapping


def test_refused_without_gate_c_on_this_kit(tmp_path):
    async def setup(services, runner):
        sixty(services, t1=False)  # the simulator lets the probes run; the real kit's T1 proof is missing
        await probe(services, runner, MARKERS)
        return await probe(services, runner, DARK)

    reason, refused, _ = _refused(tmp_path, setup)
    assert "Gate C isn't proven on this kit" in reason and "Gate C isn't proven" in refused


def test_refused_when_t1_was_proven_on_another_kit(tmp_path):
    async def setup(services, runner):
        sixty(services)
        await probe(services, runner, MARKERS)
        dark = await probe(services, runner, DARK)
        services.update_registry(KIT, {"flags_proven_on": "some-other-kit"})
        return dark

    reason, _, _ = _refused(tmp_path, setup)
    assert "Gate C isn't proven on this kit" in reason


def test_refused_for_a_simulated_run(tmp_path):
    async def setup(services, runner):
        sixty(services)
        await probe(services, runner, MARKERS)
        record = await runner.run("address_probe_safe", KIT, DARK)  # left simulated
        runner.answer(record["run_id"], {record["result"]["ask_human"][0]: "yes"})
        return record["run_id"]

    reason, refused, _ = _refused(tmp_path, setup)
    assert "simulator" in reason and "never recorded from a simulated run" in refused


def test_refused_when_the_partner_run_is_simulated(tmp_path):
    async def setup(services, runner):
        sixty(services)
        record = await runner.run("address_probe_safe", KIT, MARKERS)  # left simulated
        runner.answer(record["run_id"], {Q_MARKERS: "yes"})
        return await probe(services, runner, DARK)

    reason, _, _ = _refused(tmp_path, setup)
    assert "markers probe" in reason and "was simulated" in reason


@pytest.mark.parametrize(("markers", "dark"), [("no", "yes"), ("yes", "no")])
def test_refused_when_either_probe_was_answered_no(tmp_path, markers, dark):
    async def setup(services, runner):
        sixty(services)
        await probe(services, runner, MARKERS, markers)
        return await probe(services, runner, DARK, dark)

    reason, refused, _ = _refused(tmp_path, setup)
    assert "answered “no”" in reason and "tell Alon" in reason and "tell Alon" in refused


def test_refused_when_the_upper_range_was_not_run_or_not_answered(tmp_path):
    async def setup(services, runner):
        sixty(services)
        markers = await probe(services, runner, MARKERS)
        services.extras["no_dark"] = refusal(runner, markers)
        await probe(services, runner, DARK, None)
        return markers

    async def body(services, runner):
        run_id = await setup(services, runner)
        return services.extras["no_dark"], refusal(runner, run_id)

    not_run, unanswered = bench(tmp_path, body)
    assert "Run the probe of entries 30 onwards on this kit too" in not_run
    assert (
        unanswered.startswith("Answer “Once the before look ended")
        and "probe of entries 30 onwards" in unanswered
    )


def test_refused_for_a_range_that_does_not_cover_every_entry_past_the_heads(tmp_path):
    async def setup(services, runner):
        sixty(services)
        await probe(services, runner, MARKERS)
        return await probe(services, runner, {"start": 30, "count": 29, "duration_s": 4.0})

    reason, _, _ = _refused(tmp_path, setup)
    assert "This run is neither" in reason


def test_refused_for_a_run_on_a_different_kit_from_the_one_registered_now(tmp_path):
    async def setup(services, runner):
        sixty(services)
        services.update_registry(KIT, {"metadevice_id": "kit-b-0002"})
        markers = await probe(services, runner, MARKERS)
        dark = await probe(services, runner, DARK)
        for run_id in (markers, dark):  # they ran on kit A; kit B is registered as bench-kit now
            edit_saved(services, run_id, lambda saved: saved.update(address="kit-a-0001", driver="afero"))
        return dark

    reason, refused, _ = _refused(tmp_path, setup)
    assert "different kit" in reason and "re-registered" in refused


def test_refused_when_the_kit_now_reports_another_frame_length(tmp_path):
    async def body(services, runner):
        sixty(services)
        await probe(services, runner, MARKERS)
        dark = await probe(services, runner, DARK)
        ready = runner.map_proposal(dark)["ready"]
        services.sim.add_device(ADDRESS, account="bench", profile="sqhdk03-sim", label="30-entry now")
        return ready, await refused_apply(runner, dark), services.registry.get(KIT).framebuffer_mapping

    ready, refused, mapping = bench(tmp_path, body)
    assert ready is True  # the records alone can't tell: apply reads the kit once more
    assert "now reports 30 entries" in refused and "not the 60 its probes saw" in refused and mapping is None


def test_refused_when_the_two_probes_saw_different_frame_lengths(tmp_path):
    async def setup(services, runner):
        sixty(services)
        markers = await probe(services, runner, MARKERS)
        edit_saved(services, markers, lambda s: s["result"]["observations"].update(length=90))
        return await probe(services, runner, DARK)

    reason, _, _ = _refused(tmp_path, setup)
    assert "different frame lengths (90 and 60)" in reason


def test_refused_when_heads_and_expected_heads_disagree(tmp_path):
    async def setup(services, runner):
        sixty(services)
        await probe(services, runner, MARKERS)
        dark = await probe(services, runner, DARK)
        services.update_registry(KIT, {"expected_heads": 29})
        return dark

    reason, _, _ = _refused(tmp_path, setup)
    assert "29 expected heads but a module has 30" in reason and "Tell Alon" in reason


def test_refused_when_the_markers_recorded_are_not_0_1_and_29(tmp_path):
    async def setup(services, runner):
        sixty(services)
        markers = await probe(services, runner, MARKERS)
        edit_saved(
            services,
            markers,
            lambda s: s["result"]["observations"].update(markers={"0": "red", "1": "green", "28": "blue"}),
        )
        return await probe(services, runner, DARK)

    reason, _, _ = _refused(tmp_path, setup)
    assert "lit entries [0, 1, 28], not 0, 1 and 29" in reason


@pytest.mark.parametrize(("start", "count", "answer"), [(30, 10, "no"), (45, 15, "no"), (30, 10, None)])
def test_a_later_probe_of_part_of_the_upper_range_must_be_answered_yes_too(tmp_path, start, count, answer):
    async def setup(services, runner):
        sixty(services)
        await probe(services, runner, MARKERS)
        dark = await probe(services, runner, DARK)
        await probe(services, runner, {"start": start, "count": count, "duration_s": 4.0}, answer)
        return dark

    reason, refused, _ = _refused(tmp_path, setup)
    entries = f"entries {start}–{start + count - 1}"
    if answer == "no":  # a head lit past the heads, after the 30–59 run that said nothing did
        assert entries in reason and "answered “no”" in reason
        assert "tell Alon" in reason and "tell Alon" in refused
    else:
        assert reason.startswith("Answer “Once the before look ended") and entries in reason


def test_an_older_probe_of_part_of_the_upper_range_is_superseded_by_a_newer_full_one(tmp_path):
    async def body(services, runner):
        sixty(services)
        await probe(services, runner, MARKERS)
        await probe(services, runner, {"start": 30, "count": 10, "duration_s": 4.0}, "no")
        dark = await probe(services, runner, DARK)  # the newest look at 30–59: nothing lit
        return runner.map_proposal(dark)

    proposal = bench(tmp_path, body)
    assert proposal["ready"] is True and proposal["mapping"] == IDENTITY


def test_refused_when_the_partner_probe_ran_on_this_device_s_previous_controller(tmp_path):
    async def setup(services, runner):
        sixty(services)
        markers = await probe(services, runner, MARKERS)
        # the markers run was on the controller this id had before; only the 30–59 run is on this kit
        edit_saved(services, markers, lambda saved: saved.update(address="sim-old-controller"))
        return await probe(services, runner, DARK)

    reason, refused, _ = _refused(tmp_path, setup)
    assert "Run the markers probe on this kit too" in reason
    assert "Run the markers probe on this kit too" in refused


@pytest.mark.parametrize("verdict", ["fail", "error"])
def test_a_probe_that_did_not_finish_says_so_and_does_not_count(tmp_path, verdict):
    async def body(services, runner):
        sixty(services)
        markers = await probe(services, runner, MARKERS, None)
        edit_saved(  # exact restore not confirmed (FAIL), or stopped short (ERROR): no question was asked
            services,
            markers,
            lambda s: s["result"].update(verdict=verdict, observations={}, ask_human=[]),
        )
        dark = await probe(services, runner, DARK)
        return refusal(runner, markers), refusal(runner, dark)

    own, partner = bench(tmp_path, body)
    assert "didn't finish with an exact restore and a Yes/No question" in own and "neither" not in own
    assert "Run the markers probe on this kit too" in partner and "Only a run that finished" in partner


def test_refused_when_bench_jobs_here_reach_another_kit_than_the_probes(tmp_path):
    async def body(services, runner):
        sixty(services)
        markers = await probe(services, runner, MARKERS)
        dark = await probe(services, runner, DARK)
        # the probes ran on the real kit (afero); bench jobs on this Conductor reach only the simulated one
        services.update_registry(KIT, {"metadevice_id": "kit-a-0001", "flags_proven_on": "kit-a-0001"})
        for run_id in (markers, dark):
            edit_saved(services, run_id, lambda saved: saved.update(address="kit-a-0001", driver="afero"))
        return await refused_apply(runner, dark), services.registry.get(KIT).framebuffer_mapping

    refused, mapping = bench(tmp_path, body)
    assert "don't reach the kit these probes ran on" in refused and mapping is None


def test_a_no_answered_while_apply_map_reads_the_kit_refuses_it(tmp_path, monkeypatch):
    async def body(services, runner):
        sixty(services)
        markers = await probe(services, runner, MARKERS)
        dark = await probe(services, runner, DARK)
        driver = services.target(KIT, Purpose.BENCH).driver
        reading, release = asyncio.Event(), asyncio.Event()
        fetch = driver.fetch_profile

        async def held(*args, **kwargs):
            reading.set()
            await release.wait()
            return await fetch(*args, **kwargs)

        monkeypatch.setattr(driver, "fetch_profile", held)
        applying = asyncio.ensure_future(runner.apply_map(dark))
        await reading.wait()  # apply_map is reading the kit's profile
        answered = runner.answer(markers, {Q_MARKERS: "no"})
        release.set()
        with pytest.raises(BenchRefused) as e:
            await applying
        return str(e.value), answered, services.registry.get(KIT).framebuffer_mapping

    refused, answered, mapping = bench(tmp_path, body)
    assert "answered “no”" in refused and "tell Alon" in refused
    # nothing was recorded, so the No had nothing to take back
    assert mapping is None and answered["map_revoked"] is None


def test_the_newest_probe_of_each_kind_is_the_one_that_counts(tmp_path):
    async def body(services, runner):
        sixty(services)
        old = await probe(services, runner, MARKERS, "yes")
        dark = await probe(services, runner, DARK)
        new = await probe(services, runner, MARKERS, "no")  # the latest look says something else lit
        return (
            refusal(runner, old),
            refusal(runner, dark),
            services.registry.get(KIT).framebuffer_mapping,
            new,
        )

    from_old, from_dark, mapping, new = bench(tmp_path, body)
    assert f"A newer markers probe ran on this kit ({new})" in from_old
    assert "answered “no”" in from_dark and mapping is None


def test_refused_for_other_jobs_and_other_ranges(tmp_path):
    async def body(services, runner):
        sixty(services)
        gate_a = await runner.run("gate_a", KIT)
        as_real(services, gate_a)
        low = await probe(
            services, runner, {"start": 0, "count": 1, "duration_s": 4.0}, "Only the first head"
        )
        return refusal(runner, gate_a["run_id"]), refusal(runner, low)

    gate_a, low = bench(tmp_path, body)
    assert "Only the bounded address probe" in gate_a
    assert "the markers pattern, and a range from entry 30 to the controller's last entry" in low


# ---- taking back a yes ----------------------------------------------------------------------------------------------
def test_taking_back_a_yes_puts_the_previous_map_back_and_both_runs_say_so(tmp_path):
    async def body(services, runner):
        sixty(services)
        markers = await probe(services, runner, MARKERS)
        dark = await probe(services, runner, DARK)
        await runner.apply_map(markers)
        q = runner.get(dark)["result"]["ask_human"][0]
        revoked = runner.answer(dark, {q: "no"})
        after_no = services.registry.get(KIT).framebuffer_mapping
        partner = runner.get(markers)
        runner.answer(dark, {q: "yes"})
        reapplied = await runner.apply_map(dark)
        return revoked, after_no, partner, reapplied, services.registry.get(KIT).framebuffer_mapping

    revoked, after_no, partner, reapplied, final = bench(tmp_path, body)
    assert after_no is None
    for record in (revoked, partner):
        assert record["map_applied"] is None
        assert record["map_revoked"]["undone"] is True and record["map_revoked"]["mapping"] == IDENTITY
    assert revoked["confirmed_verdict"] == "fail"
    assert reapplied["map_applied"]["mapping"] == IDENTITY and final.model_dump() == IDENTITY


def test_taking_back_a_yes_leaves_a_map_that_changed_since_alone(tmp_path):
    other = {"frame_length": 60, "indices": [59 - i for i in range(30)], "proven_on": ADDRESS}

    async def body(services, runner):
        sixty(services)
        await probe(services, runner, MARKERS)
        dark = await probe(services, runner, DARK)
        await runner.apply_map(dark)
        services.update_registry(KIT, {"framebuffer_mapping": other})  # changed since (an offline edit)
        revoked = runner.answer(dark, {runner.get(dark)["result"]["ask_human"][0]: "no"})
        return revoked, services.registry.get(KIT).framebuffer_mapping.model_dump()

    revoked, mapping = bench(tmp_path, body)
    assert revoked["map_revoked"]["undone"] is False and revoked["map_applied"] is None
    assert mapping == other


def test_a_revoke_puts_back_the_map_that_was_there_before(tmp_path):
    stale = {"frame_length": 60, "indices": list(range(30)), "proven_on": "an-earlier-kit"}

    async def body(services, runner):
        sixty(services)
        services.update_registry(KIT, {"framebuffer_mapping": stale})
        await probe(services, runner, MARKERS)
        dark = await probe(services, runner, DARK)
        applied = await runner.apply_map(dark)
        runner.answer(dark, {runner.get(dark)["result"]["ask_human"][0]: "no"})
        return applied["map_applied"]["previous"], services.registry.get(KIT).framebuffer_mapping.model_dump()

    previous, mapping = bench(tmp_path, body)
    assert previous == stale and mapping == stale


# ---- the show picks it up at once (end to end, opt-in 60-entry profile) ---------------------------------------------
def test_the_shows_head_check_flips_from_no_map_to_mapped_after_apply(tmp_path):
    module, address = "canopy-r1c1", "sim-canopy-r1c1"
    canvas = "rainbow-row-sunset"

    async def main():
        services, wiring, _engine = await _boot(tmp_path, before_start=lambda s: sixty(s, module))
        assert await _until(services.clock, lambda: wiring.last_commission is not None, 120) is not None
        heads = framemap.module_heads(services.config_dir)
        before = {
            "check": framemap.head_check(services.registry.get(module), address, wiring.caps[module], heads),
            "problems": services.tracker.snapshot(address)["problems"],
        }
        try:
            wiring.compile(canvas, Tier.T1)
        except PlanError as e:
            before["t1"] = str(e)
        runner = runner_of(services)
        await probe(services, runner, MARKERS, device_id=module)
        dark = await probe(services, runner, DARK, device_id=module)
        writes = services.sim.stats()["writes"]
        await runner.apply_map(dark)
        sent = services.sim.stats()["writes"] - writes
        ok = await _until(
            services.clock,
            lambda: module in wiring.caps and not services.tracker.snapshot(address)["problems"],
            60,
        )
        after = {
            "ok": ok,
            "check": framemap.head_check(services.registry.get(module), address, wiring.caps[module], heads),
            "plan": wiring.compile(canvas, Tier.T1).modules[module],
            "sent": sent,
        }
        await wiring.close()
        await services.close()
        return before, after

    before, after = run_virtual(main)
    assert before["check"].kind == "no_map" and capacity_problem(60, 30) in before["problems"]
    assert "no proven map" in before["t1"]
    assert after["sent"] == 0  # the apply itself wrote nothing (the show may write afterwards, as it should)
    assert after["ok"] is not None and after["check"].kind == "mapped"
    # T1 through the map: the full controller frame
    assert len(R.frame_entries(after["plan"].slot_value)) == 60


# ---- the routes and the CLI ----------------------------------------------------------------------------------------
def run_over_http(client, params: dict, device_id: str = KIT) -> str:
    r = client.post(
        "/api/bench/run", json={"job": "address_probe_safe", "device_id": device_id, "params": params}
    )
    assert r.status_code == 202, r.text
    return wait_for(client, r.json()["run_id"])["run_id"]


def answer_yes_as_real(client, run_ids) -> None:
    services = client.app.state.services
    for run_id in run_ids:
        record = client.get(f"/api/bench/runs/{run_id}").json()
        as_real(services, record)
        answers = {"answers": {record["result"]["ask_human"][0]: "yes"}}
        assert client.post(f"/api/bench/runs/{run_id}/answers", json=answers).is_success


def test_the_map_routes_preview_refuse_and_apply(app_client):
    services = app_client.app.state.services
    sixty(services)
    markers, dark = run_over_http(app_client, MARKERS), run_over_http(app_client, DARK)
    r = app_client.get(f"/api/bench/runs/{dark}/map")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json()["ready"] is False and "simulator" in r.json()["reason"]
    r = app_client.post(f"/api/bench/runs/{dark}/map")
    assert r.status_code == 409 and "simulator" in r.json()["detail"]
    assert services.registry.get(KIT).framebuffer_mapping is None

    answer_yes_as_real(app_client, (markers, dark))
    r = app_client.get(f"/api/bench/runs/{markers}/map")
    assert r.json()["ready"] is True and r.json()["mapping"] == IDENTITY
    r = app_client.post(f"/api/bench/runs/{markers}/map")
    assert r.status_code == 200 and r.json()["map_applied"]["mapping"] == IDENTITY
    assert services.registry.get(KIT).framebuffer_mapping.model_dump() == IDENTITY
    assert app_client.post(f"/api/bench/runs/{markers}/map").status_code == 409  # already recorded
    # the kit's id inside the recorded map is scrubbed from the copyable export too
    export = app_client.get(f"/api/bench/runs/{markers}/anonymized")
    assert export.json()["map_applied"]["mapping"]["indices"] == IDENTITY["indices"]
    assert ADDRESS not in export.text
    assert app_client.get("/api/bench/runs/not-a-run/map").status_code == 404
    assert app_client.post("/api/bench/runs/not-a-run/map").status_code == 404


def test_taking_back_a_yes_after_a_controller_swap_takes_the_map_back_where_the_kit_is_now(app_client):
    services = app_client.app.state.services
    sixty(services, "spare-1")
    spare_map = {**IDENTITY, "proven_on": "sim-spare-1"}
    markers = run_over_http(app_client, MARKERS, "spare-1")
    dark = run_over_http(app_client, DARK, "spare-1")
    answer_yes_as_real(app_client, (markers, dark))
    assert app_client.post(f"/api/bench/runs/{dark}/map", json={}).status_code == 200
    swap = {"metadevice_id": "sim-spare-1", "device_id": "canopy-r1c1"}  # the spare replaces module r1c1
    r = app_client.post("/api/accounts/canopy/register", json=swap)
    assert r.status_code == 200 and r.json()["map_carried"] is True
    assert services.registry.get("canopy-r1c1").framebuffer_mapping.model_dump() == spare_map
    question = app_client.get(f"/api/bench/runs/{dark}").json()["result"]["ask_human"][0]
    r = app_client.post(f"/api/bench/runs/{dark}/answers", json={"answers": {question: "no"}})
    assert r.status_code == 200 and r.json()["map_revoked"]["undone"] is True
    # the controller's map is gone wherever it is registered now, not only from the spare it was proven on
    assert services.registry.get("canopy-r1c1").framebuffer_mapping is None
    assert services.registry.get("spare-1").framebuffer_mapping is None
    assert sorted(r.json()["map_revoked"]["devices"]) == ["canopy-r1c1", "spare-1"]


def test_the_map_routes_are_ops_only(app_client_auth):
    for method in ("get", "post"):
        r = getattr(app_client_auth, method)("/api/bench/runs/20261007T000000-address_probe_safe-abcdef/map")
        assert r.status_code == 401
    services = app_client_auth.app.state.services
    _, path = services.roles.create_enrollment("tester", "VWBD crew", [KIT])
    tester = app_client_auth.post(
        path, follow_redirects=False
    )  # the client keeps the tester's session cookie
    assert tester.status_code == 303
    for method in ("get", "post"):
        r = getattr(app_client_auth, method)("/api/bench/runs/20261007T000000-address_probe_safe-abcdef/map")
        assert r.status_code in (401, 403)


def test_the_cli_map_command_records_the_map_or_says_why_not(app_client, capsys):
    services = app_client.app.state.services
    sixty(services)
    send = over(app_client)
    markers, dark = run_over_http(app_client, MARKERS), run_over_http(app_client, DARK)
    code, _ = run_cli(["--server", "http://testserver", "map", dark], transport=send)
    assert code == 1 and "simulator" in capsys.readouterr().err
    answer_yes_as_real(app_client, (markers, dark))
    code, out = run_cli(["--server", "http://testserver", "map", dark], transport=send)
    assert code == 0 and "Map recorded for bench-kit: 30 heads in a 60-entry frame." in out
    assert services.registry.get(KIT).framebuffer_mapping.model_dump() == IDENTITY


@pytest.mark.parametrize(
    ("ready", "reply", "applied"), [(False, "y", []), (True, "n", []), (True, "y", ["r1"])]
)
def test_the_cli_offers_the_map_only_when_the_proof_holds_and_only_records_on_yes(ready, reply, applied):
    class Client:
        def __init__(self):
            self.recorded: list[str] = []

        def map_proposal(self, run_id):
            return {"ready": ready, "reason": "what it records" if ready else "why not"}

        def apply_map(self, run_id):
            self.recorded.append(run_id)

    client, asked, out = Client(), [], io.StringIO()
    cli.offer_map(client, cli.Printer(out, False), "r1", lambda q: asked.append(q) or reply)
    assert client.recorded == applied and bool(asked) is ready
    assert ("what it records" if ready else "why not") in out.getvalue()


def test_the_bench_page_offers_apply_map_from_the_server_s_answer(app_client):
    page = app_client.get("/ops/bench/").text
    assert 'id="map"' in page and 'id="map-why"' in page
    js = (ROOT / "web" / "ops" / "bench" / "bench.js").read_text()
    assert "renderMap(record);" in js and '/map`, { method: "POST"' in js
    assert "button.hidden = !proposal.ready;" in js  # shown only when the server says the proof holds
