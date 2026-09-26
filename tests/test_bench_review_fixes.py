"""Regression tests for the adversarial review of the bench (BEN-1, F1–F3, F5–F9).

Each test reproduces the reviewer's scenario on the SimDriver (virtual time) or the real app, and proves the fix.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from test_bench_jobs import ADDRESS, RGBW, bench, paint, view, write_sends

from conductor import rows as R
from conductor.bench.jobs import JOBS, Job, JobError, coerce_params, question
from conductor.bench.runner import BenchBusy, BenchError, BenchRefused, RunContext
from conductor.bench_api import JobResult, JobSpec, Verdict
from conductor.config import DeviceEntry, DeviceFlags
from conductor.locks import IDLE_TIMEOUT_S

ROOT = Path(__file__).resolve().parent.parent
RUNBOOK = ROOT / "docs" / "runbooks" / "bench-prototype-01.md"
SELECT = ("color-individual", "custom")
MODE = ("color-mode", None)


def put(services, key: tuple[str, str | None], value: Any, address: str = ADDRESS) -> None:
    """Set a value in the simulated cloud AND on the simulated controller (what the app would leave behind)."""
    device = services.sim.cloud.device(address)
    device.cloud[key] = copy.deepcopy(value)
    device.applied[key] = copy.deepcopy(value)


def copy_slot(services, source: str, target: str, address: str = ADDRESS) -> None:
    device = services.sim.cloud.device(address)
    put(services, (R.FRAME_CLASS, target), device.cloud[(R.FRAME_CLASS, source)], address)


def slot_hashes(services, address: str = ADDRESS) -> dict[str, str | None]:
    device = services.sim.cloud.device(address)
    return {s: R.slot_hash(device.cloud[(R.FRAME_CLASS, s)]) for s in R.SLOTS}


def write_labels(record: dict) -> list[str]:
    return [s["detail"]["label"] for s in record["steps"] if s["detail"].get("kind") == "write"]


def as_real_run(tmp_path, run_id: str, address: str = "kit-a-0001", driver: str = "afero") -> None:
    """Rewrite a saved run as if it had run on the real kit `address` (flags are never applied from the sim)."""
    path = tmp_path / "bench" / f"{run_id}.json"
    saved = json.loads(path.read_text())
    saved.update(simulated=False, address=address, driver=driver)
    path.write_text(json.dumps(saved))


# ---- BEN-1: bounded latency params, a lease that survives waits, and a stop route ---------------------------------
@pytest.mark.parametrize(
    "bad",
    [
        {"spacing_s": "inf"},
        {"spacing_s": "nan"},
        {"spacing_s": 31},
        {"spacing_s": 700},
        {"spacing_s": -1},
        {"n": 0},
        {"n": 101},
        {"n": "1e9"},
    ],
)
def test_ben1_latency_params_are_bounded(bad):
    with pytest.raises(ValueError):
        coerce_params(JOBS["latency_trials"].spec, bad)


def test_ben1_non_finite_floats_are_refused_by_prepare(tmp_path):
    async def body(services, runner):
        with pytest.raises(BenchError, match="spacing_s"):
            runner.prepare("latency_trials", "bench-kit", {"spacing_s": "inf"})
        return services.locks.holder(ADDRESS)

    assert bench(tmp_path, body) is None


def test_ben1_a_long_wait_inside_a_run_keeps_the_lock(tmp_path):
    async def long_wait(ctx, params):
        await ctx.sleep(IDLE_TIMEOUT_S + 100)
        holder = ctx.runner.services.locks.holder(ctx.address)
        res = await ctx.write(R.slot_select("custom-2"), label="after a long wait")
        return JobResult(Verdict.INFO, str(res.status), {"holder": holder.owner if holder else None})

    async def body(services, runner):
        runner.jobs = {
            **runner.jobs,
            "long_wait": Job(JobSpec("long_wait", "Long wait", "", True), long_wait),
        }
        return await runner.run("long_wait", "bench-kit")

    record = bench(tmp_path, body)
    assert record["result"]["summary"] == "applied"
    assert record["result"]["observations"]["holder"] == "bench · long_wait"


def test_ben1_a_running_job_can_be_stopped_and_the_device_is_free_again(tmp_path):
    async def body(services, runner):
        run_id = runner.start("latency_trials", "bench-kit", {"n": 20, "spacing_s": 30})
        await asyncio.sleep(40)  # a trial or two in
        record = await runner.stop(run_id)
        free = services.locks.holder(ADDRESS)
        again = runner.prepare("gate_a", "bench-kit", {})
        await runner.execute(again)
        with pytest.raises(BenchRefused, match="isn't running"):
            await runner.stop(run_id)
        return record, free

    record, free = bench(tmp_path, body)
    assert record["status"] == "finished" and record["result"]["verdict"] == "error"
    assert "Stopped" in record["result"]["summary"]
    assert record["writes_sent"] >= 1 and "already gone out" in record["result"]["summary"]
    assert free is None


def test_ben1_stop_route(app_client):
    r = app_client.post(
        "/api/bench/run",
        json={"job": "latency_trials", "device_id": "bench-kit", "params": {"n": 50, "spacing_s": 30}},
    )
    run_id = r.json()["run_id"]
    stopped = app_client.post(f"/api/bench/runs/{run_id}/stop")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "finished" and stopped.json()["result"]["verdict"] == "error"
    assert app_client.app.state.services.locks.holder("sim-bench-kit") is None
    assert app_client.post(f"/api/bench/runs/{run_id}/stop").status_code == 409
    assert app_client.post("/api/bench/runs/nope/stop").status_code == 404


# ---- F1: Gate C must prove something --------------------------------------------------------------------------------
def test_f1_gate_c_refuses_when_the_target_already_holds_the_source_pattern(tmp_path):
    async def body(services, runner):
        paint(services, "custom-1", RGBW)
        copy_slot(services, "custom-1", "custom-2")
        writes = services.sim.stats()["writes"]
        record = await runner.run("gate_c", "bench-kit")
        return record, services.sim.stats()["writes"] - writes

    record, writes = bench(tmp_path, body)
    result = record["result"]
    assert result["verdict"] == "error" and writes == 0 and result["flags"] == {}
    assert "already holds" in result["summary"] and "Nothing was sent" in result["summary"]
    obs = result["observations"]
    assert obs["target_hash_before"] == obs["source_hash"]


def test_f1_gate_c_moves_the_lights_off_the_source_before_the_replay(tmp_path):
    """The kit is left showing slot 1 (runbook order: Gate B unplugged picks custom-1). Gate C must show a
    BEFORE look that isn't slot 1's pattern, then replay, and ask about the change."""
    shown: list[Any] = []

    async def body(services, runner):
        paint(services, "custom-1", RGBW)
        put(services, SELECT, "custom-1")
        put(services, MODE, "individual")
        original = services.sim.write

        async def spy(device_id, rows, *, purpose):
            if any(r.function_class == R.FRAME_CLASS for r in rows):  # the replay: what is showing right now?
                v = view(services)
                shown.append((v[MODE], v[SELECT]))
            return await original(device_id, rows, purpose=purpose)

        services.sim.write = spy
        record = await runner.run("gate_c", "bench-kit")
        await asyncio.sleep(3)
        return record, view(services)

    record, after = bench(tmp_path, body)
    result = record["result"]
    obs = result["observations"]
    assert result["verdict"] == "needs_eyes" and result["flags"] == {"t1_frames": True}
    assert obs["selected_before"] == "custom-1" and obs["target_hash_before"] != obs["source_hash"]
    assert obs["before_look"] == "custom-3"
    # just before the replay the lights showed custom-3, not slot 1's pattern
    assert shown == [("individual", "custom-3")]
    labels = write_labels(record)
    assert len(labels) == 2 and "BEFORE" in labels[0] and "copy" in labels[1]
    assert result["ask_human"] == [question("gate_c", {"source": "custom-1", "target": "custom-2"})]
    assert "BEFORE" in result["ask_human"][0] and "slot 1" in result["ask_human"][0]
    assert record["flag_questions"] == {"t1_frames": result["ask_human"][0]}
    assert after[SELECT] == "custom-2"


def test_f1_gate_c_uses_a_whole_strand_look_when_the_third_slot_matches_the_source(tmp_path):
    async def body(services, runner):
        paint(services, "custom-1", RGBW)
        copy_slot(services, "custom-1", "custom-3")
        return await runner.run("gate_c", "bench-kit")

    record = bench(tmp_path, body)
    obs = record["result"]["observations"]
    assert record["result"]["verdict"] == "needs_eyes"
    assert obs["before_look"].startswith("white")
    assert "BEFORE" in write_labels(record)[0]


# ---- F2: latency trials alternate between visibly different looks ---------------------------------------------------
def test_f2_latency_trials_after_the_runbook_order_alternate_between_different_slots(tmp_path):
    async def body(services, runner):
        paint(services, "custom-1", RGBW)
        for job in ("gate_b", "gate_c", "hue_ramp"):
            await runner.run(job, "bench-kit")
        await asyncio.sleep(3)
        hashes = slot_hashes(services)
        showing = view(services)[SELECT]
        record = await runner.run("latency_trials", "bench-kit", {"n": 4, "spacing_s": 1})
        return record, hashes, showing

    record, hashes, showing = bench(tmp_path, body)
    trials = record["result"]["observations"]["trials"]
    slots = [t["slot"] for t in trials]
    assert slots[0] != showing  # the first trial changes something visible
    for a, b in pairwise(slots):
        assert hashes[a] != hashes[b]


def test_f2_equal_slots_get_a_contrasting_frame_when_t1_is_allowed(tmp_path):
    async def body(services, runner):
        copy_slot(services, "custom-2", "custom-3")
        record = await runner.run("latency_trials", "bench-kit", {"n": 4, "spacing_s": 1})
        return record, slot_hashes(services)

    record, hashes = bench(tmp_path, body)
    obs = record["result"]["observations"]
    assert record["result"]["verdict"] == "needs_eyes"
    assert hashes["custom-2"] != hashes["custom-3"] and obs["contrast_written_into"] in (
        "custom-2",
        "custom-3",
    )
    assert [t["slot"] for t in obs["trials"]][:2] in (["custom-2", "custom-3"], ["custom-3", "custom-2"])


def test_f2_equal_slots_without_t1_alternate_with_a_whole_strand_look(tmp_path, monkeypatch):
    monkeypatch.setattr(RunContext, "t1_allowed", property(lambda self: False))

    async def body(services, runner):
        copy_slot(services, "custom-2", "custom-3")
        before = slot_hashes(services)
        record = await runner.run("latency_trials", "bench-kit", {"n": 4, "spacing_s": 1})
        return record, before, slot_hashes(services)

    record, before, after = bench(tmp_path, body)
    trials = record["result"]["observations"]["trials"]
    assert before == after  # no frame was written
    kinds = [t["slot"] for t in trials]
    assert any(k.startswith("white") for k in kinds) and any(k in R.SLOTS for k in kinds)
    assert all((a in R.SLOTS) != (b in R.SLOTS) for a, b in pairwise(kinds))
    assert len(write_sends(record)) == 4


# ---- F3: index order records the slot header for the effect-code table ----------------------------------------------
def test_f3_index_order_records_the_slot_headers(tmp_path):
    async def body(services, runner):
        paint(services, "custom-1", RGBW)
        device = services.sim.cloud.device(ADDRESS)
        device.cloud[(R.FRAME_CLASS, "custom-1")][R.FRAME_CLASS].update(motionEffect=6, motionSpeed=20)
        record = await runner.run("index_order", "bench-kit")
        return record, runner.anonymized(record["run_id"])

    record, exported = bench(tmp_path, body)
    obs = record["result"]["observations"]
    header = obs["header"]
    for key in (
        "id",
        "version",
        "headerFlags",
        "sequenceFlags",
        "frameBuffer.flags",
        "motionEffect",
        "motionSpeed",
        "brightnessEffect",
        "brightnessSpeed",
        "brightnessDepth",
    ):
        assert key in header, key
    assert header["motionEffect"] == 6 and header["motionSpeed"] == 20
    assert set(obs["slot_headers"]) == set(R.SLOTS)
    text = json.dumps(exported)
    for field in ("motionEffect", "brightnessEffect", "motionSpeed", "brightnessSpeed", "brightnessDepth"):
        assert field in text


# ---- F5: a confirming answer changed to No after Apply flags ---------------------------------------------------------
def test_f5_changing_the_answer_to_no_undoes_the_applied_flag(tmp_path):
    async def body(services, runner):
        services.update_registry("bench-kit", {"metadevice_id": "kit-a-0001"})
        paint(services, "custom-1", RGBW)
        record = await runner.run("gate_c", "bench-kit")
        run_id = record["run_id"]
        as_real_run(tmp_path, run_id)
        q = record["result"]["ask_human"][0]
        runner.answer(run_id, {q: "yes"})
        runner.apply_flags(run_id)
        applied = services.registry.get("bench-kit").flags.t1_frames
        revoked = runner.answer(run_id, {q: "no"})
        after_no = services.registry.get("bench-kit").flags.t1_frames
        runner.answer(run_id, {q: "yes"})
        reapplied = runner.apply_flags(run_id)
        return applied, revoked, after_no, reapplied, services.registry.get("bench-kit").flags.t1_frames

    applied, revoked, after_no, reapplied, final = bench(tmp_path, body)
    assert applied is True
    assert revoked["confirmed_verdict"] == "fail" and revoked["flags_applied"] is None
    assert revoked["flags_revoked"]["flags"] == {"t1_frames": None}
    assert after_no is None
    assert reapplied["flags_applied"]["flags"] == {"t1_frames": True} and final is True


# ---- F6: Gate B checks the slot isn't already showing; the question names the slot ------------------------------------
def test_f6_gate_b_refuses_a_slot_that_is_already_showing(tmp_path):
    async def body(services, runner):
        put(services, SELECT, "custom-2")
        put(services, MODE, "individual")
        writes = services.sim.stats()["writes"]
        same = await runner.run("gate_b", "bench-kit")
        put(services, SELECT, "custom-1")
        copy_slot(services, "custom-1", "custom-2")
        lookalike = await runner.run("gate_b", "bench-kit")
        return same, lookalike, services.sim.stats()["writes"] - writes

    same, lookalike, writes = bench(tmp_path, body)
    assert writes == 0
    assert same["result"]["verdict"] == "error" and "already showing" in same["result"]["summary"]
    assert lookalike["result"]["verdict"] == "error" and "same pattern" in lookalike["result"]["summary"]


def test_f6_the_gate_b_question_names_the_slot_it_selected(tmp_path):
    async def body(services, runner):
        return await runner.run("gate_b", "bench-kit", {"slot": "custom-3"})

    record = bench(tmp_path, body)
    q = record["result"]["ask_human"][0]
    assert "slot 3" in q and "slot 2" not in q
    assert record["flag_questions"] == {"t2_select": q}


# ---- F7: flags are bound to the kit that proved them ---------------------------------------------------------------
def test_f7_flags_from_a_run_on_another_kit_are_refused(tmp_path):
    async def body(services, runner):
        services.update_registry("bench-kit", {"metadevice_id": "kit-b-0002"})
        record = await runner.run("gate_b", "bench-kit")
        run_id = record["run_id"]
        as_real_run(tmp_path, run_id, address="kit-a-0001")
        runner.answer(run_id, {record["result"]["ask_human"][0]: "yes"})
        with pytest.raises(BenchRefused, match="different kit"):
            runner.apply_flags(run_id)
        refused_flags = services.registry.get("bench-kit").flags.t2_select
        # the same kit registered again: applied, and recorded with the kit that proved it
        services.update_registry("bench-kit", {"metadevice_id": "kit-a-0001"})
        runner.apply_flags(run_id)
        entry = services.registry.get("bench-kit")
        proven = (entry.flags_proven_on, entry.flags_for("kit-a-0001").t2_select)
        # a different kit registered under the same logical id doesn't inherit the proof
        services.update_registry("bench-kit", {"metadevice_id": "kit-b-0002"})
        swapped = services.registry.get("bench-kit").flags_for("kit-b-0002").t2_select
        return refused_flags, proven, swapped

    refused_flags, proven, swapped = bench(tmp_path, body)
    assert refused_flags is None
    assert proven == ("kit-a-0001", True)
    assert swapped is None


def test_f7_flags_for_ignores_flags_proven_on_another_kit():
    entry = DeviceEntry(
        id="bench-kit",
        role="bench",
        account="bench",
        metadevice_id="kit-b-0002",
        flags=DeviceFlags(t1_frames=True),
        flags_proven_on="kit-a-0001",
    )
    assert entry.flags_for("kit-b-0002") == DeviceFlags()
    assert entry.flags_for("kit-a-0001").t1_frames is True
    legacy = DeviceEntry(id="bench-kit", role="bench", account="bench", flags=DeviceFlags(t1_frames=True))
    assert legacy.flags_for("kit-a-0001") == DeviceFlags()  # not bound to any kit: not proven


# ---- F8: ERROR after a write says so ---------------------------------------------------------------------------------
def test_f8_an_error_after_a_write_says_writes_went_out(tmp_path):
    async def write_then_fail(ctx, params):
        await ctx.write(R.slot_select("custom-2"), label="a write")
        raise JobError("Couldn't read the device's state after the write.")

    async def body(services, runner):
        runner.jobs = {
            **runner.jobs,
            "wtf": Job(JobSpec("wtf", "Write then fail", "", True), write_then_fail),
        }
        return await runner.run("wtf", "bench-kit")

    record = bench(tmp_path, body)
    assert record["result"]["verdict"] == "error" and record["writes_sent"] == 1
    assert "1 write had already gone out" in record["result"]["summary"]


def test_f8_an_error_before_any_write_says_nothing_was_sent(tmp_path):
    async def body(services, runner):
        return await runner.run("gate_b_unplugged", "bench-kit")

    record = bench(tmp_path, body)
    assert record["result"]["verdict"] == "error" and record["writes_sent"] == 0
    assert "Nothing was sent" in record["result"]["summary"]


# ---- F9: the runbook's commands reach staging, and ERROR is described truthfully -------------------------------------
def test_f9_every_runbook_cli_example_names_the_server():
    text = RUNBOOK.read_text()
    commands = re.findall(r"`(python -m conductor\.[^`]*)`", text)
    assert commands
    for command in commands:
        if "--local" in command:
            continue
        assert "--server" in command, command
    assert "Nothing was changed" not in text
    assert "confirmed=1" in text


def test_f9_the_unplugged_refusal_tells_a_terminal_user_what_to_pass(tmp_path):
    async def body(services, runner):
        return await runner.run("gate_b_unplugged", "bench-kit")

    assert "--param confirmed=1" in bench(tmp_path, body)["result"]["summary"]


def test_busy_is_still_refused(tmp_path):
    """Sanity: stopping support didn't loosen the one-run-per-device rule."""

    async def body(services, runner):
        run_id = runner.start("latency_trials", "bench-kit", {"n": 3, "spacing_s": 5})
        await asyncio.sleep(0.1)
        with pytest.raises(BenchBusy):
            runner.prepare("gate_a", "bench-kit", {})
        return await runner.wait(run_id)

    assert bench(tmp_path, body)["status"] == "finished"


def test_ben1_cli_stop(app_client):
    from test_bench_cli import over, run_cli

    r = app_client.post(
        "/api/bench/run",
        json={"job": "latency_trials", "device_id": "bench-kit", "params": {"n": 50, "spacing_s": 30}},
    )
    run_id = r.json()["run_id"]
    code, out = run_cli(["--server", "http://testserver", "stop", run_id], transport=over(app_client))
    assert code == 0 and "Stopped" in out
    assert app_client.get(f"/api/bench/runs/{run_id}").json()["result"]["verdict"] == "error"
