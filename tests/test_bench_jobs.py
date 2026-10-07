"""Every bench job end-to-end on the SimDriver (virtual time): verdicts, proposed flags, the lock, the rate budget."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
from collections.abc import Awaitable, Callable
from itertools import pairwise
from typing import Any

import pytest

from conductor import rows as R
from conductor.bench.jobs import (
    FLAG_QUESTIONS,
    JOBS,
    LOOK_SPACING_S,
    PARAM_BOUNDS,
    coerce_params,
    colour_name,
    hue_ramp_entries,
    question,
)
from conductor.bench.runner import BenchBusy, BenchError, BenchRefused, runner_of
from conductor.clock import VirtualClock, run_virtual
from conductor.executor_api import Command
from conductor.gate import Purpose
from conductor.services import build_services
from conductor.sim.settings import SimSettings

ADDRESS = "sim-bench-kit"


def bench(
    tmp_path, body: Callable[[Any, Any], Awaitable[Any]], *, sim_settings: SimSettings | None = None
) -> Any:
    """Run `body(services, runner)` on a virtual-time loop with a fresh simulated world."""

    async def main():
        services = build_services(
            {"CONDUCTOR_DATA_DIR": str(tmp_path)}, clock=VirtualClock(), sim_settings=sim_settings
        )
        try:
            return await body(services, runner_of(services))
        finally:
            await services.close()

    return run_virtual(main)


def view(services, address: str = ADDRESS) -> dict:
    return {r.key: r.value for r in services.sim.device_view(address)}


def cloud(services, address: str = ADDRESS) -> dict:
    return {r.key: r.value for r in services.sim.cloud_view(address)}


def write_sends(record: dict) -> list[float]:
    return [s["detail"]["sent_at"] for s in record["steps"] if s["detail"].get("kind") == "write"]


def paint(services, slot: str, colours: dict[int, tuple[int, int, int]], address: str = ADDRESS) -> None:
    """App-paint a slot in the simulated cloud (what the Hubspace app would do)."""
    device = services.sim.cloud.device(address)
    value = device.cloud[(R.FRAME_CLASS, slot)]
    fb = value[R.FRAME_CLASS]["frameBuffer"]["framebuffer"]
    for i, e in enumerate(fb):
        e.update({"r": 20, "g": 20, "b": 20, "cct": 0, "colorBrightness": 20, "whiteBrightness": 0})
        if i in colours or i - len(fb) in colours:
            r, g, b = colours.get(i, colours.get(i - len(fb)))
            e.update({"r": r, "g": g, "b": b, "colorBrightness": 100})


RGBW = {0: (255, 0, 0), 1: (0, 255, 0), 2: (0, 0, 255), 3: (255, 255, 255)}


# ---- Gate A ------------------------------------------------------------------------------------------------------
def test_gate_a_passes_on_the_30_head_profile_and_stores_a_scrubbed_profile(tmp_path):
    async def body(services, runner):
        writes = services.sim.stats()["writes"]
        record = await runner.run("gate_a", "bench-kit")
        return record, services.sim.stats()["writes"] - writes, runner.profile(record["run_id"])

    record, writes, profile = bench(tmp_path, body)
    result = record["result"]
    assert result["verdict"] == "pass" and writes == 0
    obs = result["observations"]
    assert obs["slots"] == ["custom-1", "custom-2", "custom-3"] and obs["slot_count"] == 3
    assert obs["framebuffer_length"] == 30 and obs["segment_hint"] == 30
    assert obs["individual_mode"] is True and obs["cct_range"] == [2700, 6500]
    assert obs["brightness_instances"] == 1 and obs["warnings"] == []
    assert obs["presets"] and obs["programs"] and obs["speed_range"] == [-10, 10]
    assert obs["party_mode"] is True and "restored" in obs["restore_values"]
    assert record["simulated"] is True and record["driver"] == "sim"
    assert record["artifacts"] == ["profile_raw"]
    text = json.dumps(profile)
    assert profile["functions"] and profile["states"] and "sim-bench-kit" not in text
    assert profile["friendly_name"] == "<name>"
    # the stored artifact itself is already scrubbed
    stored = (tmp_path / "bench" / "artifacts" / f"{record['run_id']}.profile_raw.json").read_text()
    assert "sim-bench-kit" not in stored and "Bench kit (Prototype 01)" not in stored


def test_gate_a_needs_eyes_on_an_unexpected_length_and_fails_without_individual(tmp_path):
    async def body(services, runner):
        services.update_registry(
            "string-20", {"role": "spare", "account": "bench", "profile": "string-20seg"}
        )
        twenty = await runner.run("gate_a", "string-20")
        original = services.sim.fetch_profile

        async def no_individual(address, *, purpose):
            info = await original(address, purpose=purpose)
            functions = []
            for fn in info.functions:
                if fn.get("functionClass") == "color-individual":
                    continue
                if fn.get("functionClass") == "color-mode":
                    fn = {**fn, "values": [v for v in fn["values"] if v.get("name") != "individual"]}
                functions.append(fn)
            return dataclasses.replace(info, functions=functions)

        services.sim.fetch_profile = no_individual
        t3 = await runner.run("gate_a", "bench-kit")
        return twenty, t3

    twenty, t3 = bench(tmp_path, body)
    assert twenty["result"]["verdict"] == "needs_eyes"
    assert twenty["result"]["observations"]["framebuffer_length"] == 20
    assert t3["result"]["verdict"] == "fail" and "T3 only" in t3["result"]["summary"]


def test_gate_a_warns_about_extra_brightness_functions(tmp_path):
    async def body(services, runner):
        original = services.sim.fetch_profile

        async def two_brightness(address, *, purpose):
            info = await original(address, purpose=purpose)
            extra = next(f for f in info.functions if f["functionClass"] == "brightness")
            return dataclasses.replace(
                info, functions=[*info.functions, {**extra, "functionInstance": "white"}]
            )

        services.sim.fetch_profile = two_brightness
        return await runner.run("gate_a", "bench-kit")

    record = bench(tmp_path, body)
    obs = record["result"]["observations"]
    assert obs["brightness_instances"] == 2
    assert any("light_callback" in w for w in obs["warnings"])


# ---- index order -------------------------------------------------------------------------------------------------
def test_index_order_finds_the_painted_pattern_forward_and_reversed(tmp_path):
    async def body(services, runner):
        writes = services.sim.stats()["writes"]
        paint(services, "custom-1", RGBW)
        forward = await runner.run("index_order", "bench-kit")
        paint(services, "custom-1", {-1: (255, 0, 0), -2: (0, 255, 0), -3: (0, 0, 255), -4: (255, 255, 255)})
        reverse = await runner.run("index_order", "bench-kit")
        paint(services, "custom-1", {})
        missing = await runner.run("index_order", "bench-kit")
        return forward, reverse, missing, services.sim.stats()["writes"] - writes

    forward, reverse, missing, writes = bench(tmp_path, body)
    assert writes == 0
    assert forward["result"]["observations"]["order"] == "forward"
    assert forward["result"]["observations"]["first_4_colours"] == ["red", "green", "blue", "white"]
    assert len(forward["result"]["observations"]["first_4"]) == 4
    assert reverse["result"]["observations"]["order"] == "reversed"
    assert reverse["result"]["observations"]["last_4_colours"] == ["white", "blue", "green", "red"]
    assert (
        missing["result"]["verdict"] == "needs_eyes"
        and missing["result"]["observations"]["order"] == "not found"
    )


def test_colour_names():
    assert colour_name(R.FrameEntry(250, 10, 5)) == "red"
    assert colour_name(R.FrameEntry(0, 0, 0, 2700, 0, 100)) == "white"
    assert colour_name(R.FrameEntry(255, 240, 230)) == "white"
    assert colour_name(R.FrameEntry(0, 0, 0)) == "off"
    assert colour_name(R.FrameEntry(255, 128, 0)) == "other"


# ---- Gate B ------------------------------------------------------------------------------------------------------
def test_gate_b_selects_slot_2_and_proposes_t2(tmp_path):
    async def body(services, runner):
        record = await runner.run("gate_b", "bench-kit")
        await asyncio.sleep(3)  # let the simulated controller apply it
        return record, view(services)

    record, device = bench(tmp_path, body)
    result = record["result"]
    assert result["verdict"] == "needs_eyes"
    q_gate_b = question("gate_b", {"slot": "custom-2"})
    assert q_gate_b == "Did the strand switch to slot 2 within about 2 s?"
    assert result["flags"] == {"t2_select": True} and result["ask_human"] == [q_gate_b]
    assert record["flag_questions"] == {"t2_select": q_gate_b} and set(FLAG_QUESTIONS["gate_b"]) == {
        "t2_select"
    }
    obs = result["observations"]
    assert obs["before"]["selected_slot"] == "custom-1"
    assert obs["write"]["status"] == "applied" and obs["write"]["http_status"] == 200
    assert obs["write"]["ack_ms"] > 0 and obs["write"]["verified_ms"] >= obs["write"]["ack_ms"]
    assert (
        device[("color-individual", "custom")] == "custom-2" and device[("color-mode", None)] == "individual"
    )


def test_gate_b_unplugged_needs_confirmation_then_measures_readback(tmp_path):
    async def body(services, runner):
        writes = services.sim.stats()["writes"]
        refused = await runner.run("gate_b_unplugged", "bench-kit")
        unsent = services.sim.stats()["writes"] - writes
        before = view(services)
        cloud = await runner.run("gate_b_unplugged", "bench-kit", {"confirmed": "true"})
        await asyncio.sleep(3)
        after = view(services)
        back_on = services.sim.cloud.device(ADDRESS).available
        services.sim.faults.set_readback_semantics("device")
        device = await runner.run("gate_b_unplugged", "bench-kit", {"confirmed": True})
        return refused, unsent, before, cloud, after, back_on, device

    refused, unsent, before, cloud, after, back_on, device = bench(tmp_path, body)
    assert refused["result"]["verdict"] == "error" and unsent == 0
    assert "wall" in refused["result"]["summary"]
    obs = cloud["result"]["observations"]
    assert cloud["result"]["flags"] == {"readback_device_level": False}
    assert obs["readback_selected"] == obs["target"] != obs["selected_before"]
    assert obs["available_after"] is False and obs["readback_shows"] == "cloud"
    assert "cloud stored" in cloud["result"]["summary"]
    # the switched-off controller never applied it, and the simulated kit is powered again afterwards
    assert after[("color-individual", "custom")] == before[("color-individual", "custom")]
    assert back_on is True
    assert device["result"]["flags"] == {"readback_device_level": True}
    assert device["result"]["observations"]["readback_shows"] == "device"


# ---- Gate C ------------------------------------------------------------------------------------------------------
def test_gate_c_replays_slot_1_into_slot_2_with_equal_hashes(tmp_path):
    async def body(services, runner):
        paint(services, "custom-1", RGBW)
        record = await runner.run("gate_c", "bench-kit")
        await asyncio.sleep(3)
        writes = services.sim.stats()["writes"]
        same = await runner.run("gate_c", "bench-kit", {"source": "custom-2", "target": "custom-2"})
        return record, view(services), cloud(services), services.sim.stats()["writes"] - writes, same

    record, device, stored, extra_writes, same = bench(tmp_path, body)
    result = record["result"]
    obs = result["observations"]
    assert result["verdict"] == "needs_eyes" and result["flags"] == {"t1_frames": True}
    assert result["ask_human"] == [question("gate_c", {"source": "custom-1", "target": "custom-2"})]
    assert obs["before_look"] == "custom-3" and obs["target_hash_before"] != obs["source_hash"]
    assert obs["hashes_match"] and obs["source_hash"] == obs["target_hash"]
    assert obs["write"]["http_status"] == 200 and obs["length"] == 30
    slot1 = stored[(R.FRAME_CLASS, "custom-1")]  # painted in the app: in the cloud
    slot2 = device[(R.FRAME_CLASS, "custom-2")]  # written by Gate C: applied on the controller
    assert R.slot_hash(slot1) == R.slot_hash(slot2) == obs["source_hash"]
    assert device[("color-individual", "custom")] == "custom-2"
    assert same["result"]["verdict"] == "error" and extra_writes == 0


def test_gate_c_reports_a_refused_frame_as_a_fail(tmp_path):
    async def body(services, runner):
        # a frame the cloud refuses: one entry with an out-of-range colour
        value = services.sim.cloud.device(ADDRESS).cloud[(R.FRAME_CLASS, "custom-1")]
        value[R.FRAME_CLASS]["frameBuffer"]["framebuffer"][0]["r"] = 999
        return await runner.run("gate_c", "bench-kit")

    record = bench(tmp_path, body)
    assert record["result"]["verdict"] == "fail" and "HTTP 400" in record["result"]["summary"]
    assert record["result"]["flags"] == {}


# ---- hue ramp ------------------------------------------------------------------------------------------------------
def test_hue_ramp_length_is_the_devices_own_frame_length(tmp_path):
    async def body(services, runner):
        services.update_registry(
            "string-20", {"role": "spare", "account": "bench", "profile": "string-20seg"}
        )
        thirty = await runner.run("hue_ramp", "bench-kit")
        twenty = await runner.run("hue_ramp", "string-20")
        await asyncio.sleep(3)
        return thirty, twenty, view(services), view(services, "sim-string-20")

    thirty, twenty, kit, string20 = bench(tmp_path, body)
    for record, device, n in ((thirty, kit, 30), (twenty, string20, 20)):
        assert record["result"]["verdict"] == "needs_eyes"
        assert record["result"]["observations"]["length"] == n
        entries = R.frame_entries(device[(R.FRAME_CLASS, "custom-3")])
        assert len(entries) == n
        assert entries == hue_ramp_entries(n)
        assert device[("color-individual", "custom")] == "custom-3"
    assert hue_ramp_entries(30)[0] == R.FrameEntry(255, 0, 0, 0, 100, 0)
    assert len({(e.r, e.g, e.b) for e in hue_ramp_entries(30)}) == 30


# ---- latency trials, whole strand and the rate budget ---------------------------------------------------------------
STRICT = SimSettings.dev().with_(per_device_min_interval_s=1.0)


def test_latency_trials_alternate_and_respect_the_spacing(tmp_path):
    async def body(services, runner):
        record = await runner.run("latency_trials", "bench-kit", {"n": 6, "spacing_s": 0.2})
        return record, services.sim.stats()

    record, stats = bench(tmp_path, body, sim_settings=STRICT)
    obs = record["result"]["observations"]
    assert record["result"]["verdict"] == "needs_eyes" and obs["failures"] == 0
    assert obs["spacing_s"] == 1.0  # never below the per-device budget
    assert [t["slot"] for t in obs["trials"]] == ["custom-3", "custom-2"] * 3
    assert all(t["ack_ms"] is not None and t["verified_ms"] is not None for t in obs["trials"])
    assert obs["ack_ms_p50"] <= obs["ack_ms_p95"] and obs["verified_ms_p50"] <= obs["verified_ms_p95"]
    sends = write_sends(record)
    assert len(sends) == 6 and all(b - a >= 1.0 - 1e-6 for a, b in pairwise(sends))
    assert stats["min_interval_violations_total"] == 0


def test_whole_strand_tries_each_look_once_at_least_2_s_apart(tmp_path):
    async def body(services, runner):
        record = await runner.run("whole_strand", "bench-kit")
        return record, services.sim.stats(), services.telemetry.recent(kind="command", limit=100)

    record, stats, commands = bench(tmp_path, body, sim_settings=STRICT)
    result = record["result"]
    looks = [entry["look"] for entry in result["observations"]["looks"]]
    assert result["verdict"] == "needs_eyes" and len(looks) == 8 == len(result["ask_human"])
    assert {"kind": "white", "brightness": 80, "cct": 2700, "speed": 0} in looks
    assert any(look.get("cct") == 6500 for look in looks)
    assert [look["brightness"] for look in looks[2:4]] == [10, 1]
    assert {"kind": "rgb", "brightness": 100, "rgb": [249, 99, 2], "speed": 0} in looks
    assert [look["speed"] for look in looks if look["kind"] == "preset"] == [-10, 0, 10]
    sends = write_sends(record)
    assert all(b - a >= LOOK_SPACING_S - 1e-6 for a, b in pairwise(sends))
    assert stats["min_interval_violations_total"] == 0
    # every write went through the executor as a BENCH command
    bench_cmds = [c for c in commands if c["device_id"] == ADDRESS]
    assert len(bench_cmds) == 8 and {c["purpose"] for c in bench_cmds} == {"bench"}


def test_restore_puts_the_kit_back_to_calm_white(tmp_path):
    async def body(services, runner):
        record = await runner.run("restore", "bench-kit")
        await asyncio.sleep(3)
        return record, view(services)

    record, device = bench(tmp_path, body)
    assert record["result"]["verdict"] == "pass"
    assert device[("color-mode", None)] == "white" and int(device[("color-temperature", None)]) == 2700
    assert device[("brightness", None)] == 50 and device[("power", None)] == "on"


# ---- the lock, busy devices, errors, persistence -------------------------------------------------------------------
def test_the_lock_is_held_during_a_run_and_released_after(tmp_path):
    async def body(services, runner):
        run_id = runner.start("gate_b", "bench-kit", {})
        await asyncio.sleep(0.05)
        holder = services.locks.holder(ADDRESS)
        during = holder.owner if holder else None
        with pytest.raises(BenchBusy, match="already running gate_b"):
            runner.prepare("gate_c", "bench-kit", {})
        listed = next(d for d in runner.devices() if d["device_id"] == "bench-kit")
        engine = await services.executor_for(services.sim).submit(
            Command(ADDRESS, R.slot_select("custom-3"), Purpose.ENGINE, "engine")
        )
        record = await runner.wait(run_id)
        after = services.locks.holder(ADDRESS)
        # a lock held by someone else (the module page) refuses a run
        lease = services.locks.acquire(ADDRESS, "module page · alon")
        with pytest.raises(BenchBusy, match="module page"):
            runner.prepare("gate_a", "bench-kit", {})
        services.locks.release(ADDRESS, lease.token)
        return during, listed, str(engine.status), record, after

    during, listed, engine_status, record, after = bench(tmp_path, body)
    assert during == "bench · gate_b"
    assert listed["locked_by"] == "bench · gate_b" and listed["running"]
    assert engine_status == "locked"  # the show engine leaves a benched device alone
    assert record["status"] == "finished" and after is None


def test_a_failing_job_still_releases_the_lock_and_saves_an_error(tmp_path):
    async def body(services, runner):
        async def broken(address, *, purpose):
            raise LookupError("Hubspace doesn't know this device on this account.")

        services.sim.fetch_profile = broken
        record = await runner.run("gate_b", "bench-kit")
        return record, services.locks.holder(ADDRESS)

    record, holder = bench(tmp_path, body)
    assert record["result"]["verdict"] == "error" and "doesn't know" in record["result"]["summary"]
    assert holder is None


def test_results_are_saved_with_everything_needed(tmp_path):
    async def body(services, runner):
        record = await runner.run("gate_b", "bench-kit", {"slot": "custom-3"})
        return record, runner.list_runs(), services.telemetry.recent(kind="bench")

    record, listed, events = bench(tmp_path, body)
    saved = json.loads((tmp_path / "bench" / f"{record['run_id']}.json").read_text())
    for key in (
        "run_id",
        "job",
        "device_id",
        "simulated",
        "driver",
        "started",
        "finished",
        "params",
        "steps",
    ):
        assert key in saved
    assert saved["params"] == {"slot": "custom-3"} and saved["answers"] == {}
    assert saved["environment"]["mode"] == "sim" and saved["environment"]["version"]
    assert saved["result"]["verdict"] == "needs_eyes" and saved["finished"] >= saved["started"]
    assert all({"t", "elapsed_s", "message"} <= set(step) for step in saved["steps"])
    assert saved["steps"][0]["message"].startswith("SIMULATED")
    assert listed[0]["run_id"] == record["run_id"]
    assert events and all(e["job"] == "gate_b" for e in events)


def test_answers_and_flag_gating(tmp_path):
    async def body(services, runner):
        record = await runner.run("gate_b", "bench-kit")
        run_id = record["run_id"]
        with pytest.raises(BenchError):
            runner.answer(run_id, {"Some other question?": "yes"})
        with pytest.raises(BenchRefused, match="simulator"):
            runner.apply_flags(run_id)
        # pretend it ran on the real kit, to exercise the human-answer gate
        path = tmp_path / "bench" / f"{run_id}.json"
        saved = json.loads(path.read_text())
        saved["simulated"] = False
        path.write_text(json.dumps(saved))
        with pytest.raises(BenchRefused, match="Yes first"):
            runner.apply_flags(run_id)
        q_gate_b = record["result"]["ask_human"][0]
        answered = runner.answer(run_id, {q_gate_b: "No"})
        no_verdict = answered["confirmed_verdict"]
        with pytest.raises(BenchRefused):
            runner.apply_flags(run_id)
        runner.answer(run_id, {q_gate_b: "yes"})
        applied = runner.apply_flags(run_id)
        return no_verdict, applied, services.registry.get("bench-kit").flags

    no_verdict, applied, flags = bench(tmp_path, body)
    assert no_verdict == "fail"
    assert applied["confirmed_verdict"] == "pass" and applied["flags_applied"]["flags"] == {"t2_select": True}
    assert flags.t2_select is True and flags.t1_frames is None


def test_unknown_jobs_devices_and_params_are_refused(tmp_path):
    async def body(services, runner):
        for args in (
            ("nope", "bench-kit", {}),
            ("gate_a", "no-such-kit", {}),
            ("gate_b", "bench-kit", {"x": 1}),
        ):
            with pytest.raises(BenchError):
                runner.prepare(*args)
        return services.locks.holder(ADDRESS)

    assert bench(tmp_path, body) is None


def test_the_original_address_probe_is_not_a_runnable_job(tmp_path):
    """Retired from the catalogue (it leaves its pattern in the slot): only address_probe_safe calls it, and
    restores what it wrote. Refused like any unknown job, before the lock is taken or anything is sent."""

    async def body(services, runner):
        with pytest.raises(BenchError, match="no bench job called 'address_probe'"):
            runner.prepare("address_probe", "bench-kit", {})
        with pytest.raises(BenchError):
            await runner.run("address_probe", "bench-kit")
        return services.locks.holder(ADDRESS), services.sim.stats()

    holder, stats = bench(tmp_path, body)
    assert holder is None and stats["writes"] == 0
    assert "address_probe" not in JOBS and "address_probe" not in PARAM_BOUNDS


def test_coerce_params():
    spec = JOBS["latency_trials"].spec
    assert coerce_params(spec, {"n": "4", "spacing_s": "2.5"})["n"] == 4
    assert coerce_params(spec, {})["spacing_s"] == 3.0
    assert coerce_params(JOBS["gate_b_unplugged"].spec, {"confirmed": "yes"})["confirmed"] is True
    for bad in ({"n": "four"}, {"n": 2.5}, {"n": True}, {"unknown": 1}):
        with pytest.raises(ValueError):
            coerce_params(spec, bad)


def test_every_job_is_listed_with_a_spec():
    assert set(JOBS) == {
        "gate_a",
        "index_order",
        "gate_b",
        "gate_b_unplugged",
        "gate_c",
        "gate_c_safe",
        "hue_ramp",
        "address_probe_safe",
        "effect_probe",
        "latency_trials",
        "whole_strand",
        "stale_clock_put",
        "active_slot_rewrite",
        "rate_ramp",
        "head_count_boot",
        "restore",
    }
    assert {name for name, job in JOBS.items() if not job.spec.writes} == {
        "gate_a",
        "index_order",
        "head_count_boot",
    }
    assert list(JOBS)[-1] == "restore"  # the page lists jobs in order; restore ends every session


def test_rows_the_device_does_not_declare_are_left_out(tmp_path):
    async def body(services, runner):
        original = services.sim.fetch_profile

        async def no_party(address, *, purpose):
            info = await original(address, purpose=purpose)
            functions = [f for f in info.functions if f["functionClass"] not in ("toggle", "speed")]
            return dataclasses.replace(info, functions=functions)

        services.sim.fetch_profile = no_party
        return await runner.run("restore", "bench-kit")

    record = bench(tmp_path, body)
    assert record["result"]["verdict"] == "pass"
    assert any("doesn't declare" in s["message"] for s in record["steps"])


# ---- the bounded commissioning jobs (VWBD, Oct 2026) end-to-end: runner → executor → SimDriver -------------------
def _state(services) -> tuple[dict, dict]:
    return copy.deepcopy(view(services)), copy.deepcopy(cloud(services))


def test_gate_c_safe_restores_the_kit_exactly_within_the_rate_budget(tmp_path):
    async def body(services, runner):
        paint(services, "custom-3", RGBW)
        await asyncio.sleep(3)
        before = _state(services)
        record = await runner.run("gate_c_safe", "bench-kit", {"source": "custom-3", "target": "custom-1"})
        await asyncio.sleep(3)
        return record, before, _state(services), services.sim.stats()

    record, before, after, stats = bench(tmp_path, body, sim_settings=STRICT)
    result = record["result"]
    assert result["verdict"] == "needs_eyes" and result["flags"] == {"t1_frames": True}
    assert result["observations"]["all_slots_unchanged"] and not result["observations"]["custom_3_written"]
    assert after == before  # device view and cloud view: every slot, the selection, mode and controls
    labels = [s["detail"]["label"] for s in record["steps"] if s["detail"].get("kind") == "write"]
    assert labels[-3:] == [
        "gate_c_safe: restore fresh original slot",
        "gate_c_safe: restore slot selection",
        "gate_c_safe: restore previous active look",
    ]  # frame, selected slot and active look as separate commands (String light 1, run f613f6)
    sends = write_sends(record)
    assert (
        all(b - a >= 1.0 - 1e-6 for a, b in pairwise(sends)) and stats["min_interval_violations_total"] == 0
    )


def test_gate_c_safe_restores_after_a_refused_replay_and_after_a_stop(tmp_path):
    async def body(services, runner):
        paint(services, "custom-3", RGBW)
        await asyncio.sleep(3)
        before = _state(services)
        value = services.sim.cloud.device(ADDRESS).cloud[(R.FRAME_CLASS, "custom-3")]
        good = value[R.FRAME_CLASS]["frameBuffer"]["framebuffer"][0]["r"]
        # a frame the cloud refuses (HTTP 400): the replay fails right after the BEFORE look
        value[R.FRAME_CLASS]["frameBuffer"]["framebuffer"][0]["r"] = 999
        refused = await runner.run("gate_c_safe", "bench-kit", {"source": "custom-3", "target": "custom-1"})
        await asyncio.sleep(3)
        value[R.FRAME_CLASS]["frameBuffer"]["framebuffer"][0]["r"] = good
        after_refused = _state(services)
        run_id = runner.start(
            "gate_c_safe", "bench-kit", {"source": "custom-3", "target": "custom-1", "duration_s": 60.0}
        )
        await asyncio.sleep(25)  # inside the 60 s hold
        stopped = await runner.stop(run_id)
        await asyncio.sleep(3)
        return refused, before, after_refused, stopped, _state(services), services.sim.stats()

    refused, before, after_refused, stopped, after_stop, stats = bench(tmp_path, body, sim_settings=STRICT)
    assert refused["result"]["verdict"] == "fail" and refused["result"]["flags"] == {}
    assert after_refused[0] == before[0]
    assert stopped["result"]["verdict"] == "error" and "Stopped" in stopped["result"]["summary"]
    assert any("restore previous active look: applied" in s["message"] for s in stopped["steps"])
    assert after_stop[0] == before[0]
    for record in (refused, stopped):
        assert all(b - a >= 1.0 - 1e-6 for a, b in pairwise(write_sends(record)))
    assert stats["min_interval_violations_total"] == 0


def test_address_probe_safe_holds_then_restores_exactly_and_never_writes_custom_3(tmp_path):
    async def body(services, runner):
        await asyncio.sleep(3)
        before = _state(services)
        record = await runner.run(
            "address_probe_safe", "bench-kit", {"start": 3, "count": 2, "duration_s": 10.0}
        )
        protected = await runner.run("address_probe_safe", "bench-kit", {"slot": "custom-3"})
        await asyncio.sleep(3)
        return record, protected, before, _state(services), services.sim.stats()

    record, protected, before, after, stats = bench(tmp_path, body, sim_settings=STRICT)
    assert record["result"]["verdict"] == "needs_eyes" and record["result"]["flags"] == {}
    assert "No map was saved" in record["result"]["summary"]
    assert protected["result"]["verdict"] == "error" and protected["writes_sent"] == 0
    assert after == before
    assert all(b - a >= 1.0 - 1e-6 for a, b in pairwise(write_sends(record)))
    assert stats["min_interval_violations_total"] == 0
