"""The bench jobs that settle the remaining UNVERIFIED device behaviours (07 §6, §10; 04 bench gates #6, #10, #11):
stale_clock_put, active_slot_rewrite, rate_ramp and head_count_boot — on the SimDriver (virtual time), plus the
executor/driver seam they need (a bench-only lastUpdateTime offset and attempt cap) proven on the Afero wire.
"""

from __future__ import annotations

import asyncio
from itertools import pairwise

import pytest
from fake_afero import BENCH_MD, make_world
from test_bench_jobs import ADDRESS, bench, view, write_sends

from conductor import rows as R
from conductor.bench.jobs import (
    CONFIRM_LABELS,
    JOBS,
    Q_ACTIVE_REWRITE,
    Q_HEAD_BOOT,
    Q_STALE_DROPPED,
    Q_STALE_STORED,
    RAMP_SPACINGS_S,
    STALE_LAG_S,
    coerce_params,
    fill,
)
from conductor.bench.runner import RunContext
from conductor.clock import VirtualClock, run_virtual
from conductor.config import ExecutorSettings
from conductor.executor import CommandExecutor
from conductor.executor_api import Command, CommandStatus
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager
from conductor.services import build_services
from conductor.sim.settings import SimSettings
from conductor.telemetry import Telemetry

QUIET = SimSettings.dev().with_(per_device_min_interval_s=1.0, throttle_429_rate=0.0, silent_drop_rate=0.0)


def commands(services) -> list[dict]:
    return [c for c in services.telemetry.recent(kind="command", limit=500) if c["device_id"] == ADDRESS]


# ---- the executor / driver seam ----------------------------------------------------------------------------------
def test_stamp_offset_and_attempt_cap_are_bench_only_and_bounded(tmp_path):
    async def main():
        services = build_services({"CONDUCTOR_DATA_DIR": str(tmp_path)}, clock=VirtualClock())
        try:
            executor = services.executor_for(services.sim)
            rows = R.slot_select("custom-2")
            engine = await executor.submit(
                Command(ADDRESS, rows, Purpose.ENGINE, "engine", stamp_offset_s=-600)
            )
            capped = await executor.submit(Command(ADDRESS, rows, Purpose.ENGINE, "engine", max_attempts=1))
            lease = services.locks.acquire(ADDRESS, "bench · test")
            future = await executor.submit(
                Command(ADDRESS, rows, Purpose.BENCH, "b", lease_token=lease.token, stamp_offset_s=60)
            )
            too_old = await executor.submit(
                Command(ADDRESS, rows, Purpose.BENCH, "b", lease_token=lease.token, stamp_offset_s=-7200)
            )
            ok = await executor.submit(
                Command(
                    ADDRESS,
                    rows,
                    Purpose.BENCH,
                    "b",
                    lease_token=lease.token,
                    stamp_offset_s=-600,
                    verify=False,
                )
            )
            sent_ms = round((ok.timings.sent_at - 600) * 1000)
            stored = services.sim.cloud.device(ADDRESS).cloud_times[("color-individual", "custom")]
            return [str(r.status) for r in (engine, capped, future, too_old, ok)], sent_ms, stored
        finally:
            await services.close()

    statuses, sent_ms, stored = run_virtual(main)
    assert statuses[:4] == ["invalid"] * 4 and statuses[4] == "applied"
    assert stored == sent_ms  # the cloud holds the stale stamp the driver sent


def test_max_attempts_stops_retrying_a_429():
    """A bench command with max_attempts=1 is sent once, however the policy would retry a 429."""
    from fake_driver import FakeDriver

    from conductor.drivers.base import Outcome

    async def main():
        clock = VirtualClock()
        driver = FakeDriver(clock)
        driver.script_write("dev-1", Outcome.THROTTLED, Outcome.THROTTLED)
        locks = DeviceLockManager(lambda a: (True, ""), clock)
        executor = CommandExecutor(
            driver, locks=locks, telemetry=Telemetry(None, clock), clock=clock, policy=ExecutorSettings()
        )
        lease = locks.acquire("dev-1", "bench")
        once = await executor.submit(
            Command("dev-1", [R.power(True)], Purpose.BENCH, "b", lease_token=lease.token, max_attempts=1)
        )
        return once, len(driver.writes_to("dev-1"))

    once, sent = run_virtual(main)
    assert once.status is CommandStatus.FAILED and len(once.attempts) == 1 and sent == 1


def test_afero_driver_puts_the_stale_stamp_on_the_wire(tmp_path):
    """The shifted lastUpdateTime reaches the real adapter's PUT body (epoch ms, 600 s before the send time)."""

    async def main():
        world = await make_world(tmp_path)
        before = len(world.fake.requests)
        result = await world.driver.write(
            BENCH_MD, R.slot_select("custom-2"), purpose=Purpose.BENCH, stamp_offset_s=-600
        )
        puts = [r for r in world.fake.requests[before:] if r.method == "PUT"]
        normal = await world.driver.write(BENCH_MD, R.slot_select("custom-3"), purpose=Purpose.BENCH)
        puts_normal = [r for r in world.fake.requests if r.method == "PUT"][len(puts) :]
        await world.driver.close()
        return result, puts, normal, puts_normal

    result, puts, normal, puts_normal = run_virtual(main)
    assert result.ok and len(puts) == 1
    stamps = {row["lastUpdateTime"] for row in puts[0].json["values"]}
    assert stamps == {round((result.sent_at - 600) * 1000)}
    assert {row["lastUpdateTime"] for row in puts_normal[-1].json["values"]} == {round(normal.sent_at * 1000)}


# ---- stale_clock_put ---------------------------------------------------------------------------------------------
def test_stale_clock_put_stored_on_the_simulator(tmp_path):
    async def body(services, runner):
        record = await runner.run("stale_clock_put", "bench-kit")
        await asyncio.sleep(3)
        return record, view(services), services.sim.cloud.device(ADDRESS).cloud_times, commands(services)

    record, device, times, cmds = bench(tmp_path, body, sim_settings=QUIET)
    result = record["result"]
    obs = result["observations"]
    assert record["simulated"] is True and result["verdict"] == "needs_eyes"
    assert (
        obs["stale_write"] == "stored" and obs["readback_selected"] == obs["target"] != obs["selected_before"]
    )
    assert obs["lag_s"] == STALE_LAG_S == 600
    stamp = obs["write"]["stamp_sent_ms"]
    assert stamp == round((obs["write"]["sent_at"] - 600) * 1000)
    assert times[("color-individual", "custom")] == stamp == obs["cloud_stamp_after_ms"]
    assert result["ask_human"] == [fill(Q_STALE_STORED, {"slot": obs["target"]})] and result["flags"] == {}
    assert device[("color-individual", "custom")] == obs["target"]
    assert len(cmds) == 1 and cmds[0]["purpose"] == "bench"  # one PUT, through the executor
    assert any(s["detail"].get("stamp_offset_s") == -600 for s in record["steps"])


def test_stale_clock_put_dropped_then_control(tmp_path):
    """A cloud that answers 200 and drops stale-stamped rows: the job says so and proves it with a current stamp."""

    async def body(services, runner):
        cloud = services.sim.cloud
        original = cloud.accept_wire

        def lagging(body, *, silent_drop=False):
            now_ms = services.clock.now() * 1000
            stale = any(row["lastUpdateTime"] < now_ms - 60_000 for row in body["values"])
            return original(body, silent_drop=silent_drop or stale)

        cloud.accept_wire = lagging
        record = await runner.run("stale_clock_put", "bench-kit")
        return record, commands(services)

    record, cmds = bench(tmp_path, body, sim_settings=QUIET)
    result = record["result"]
    obs = result["observations"]
    assert result["verdict"] == "needs_eyes" and obs["stale_write"] == "dropped"
    assert obs["write"]["http_status"] == 200 and obs["readback_selected"] == obs["selected_before"]
    assert obs["control_stored"] is True and "DROPPED" in result["summary"] and "NTP" in result["summary"]
    assert result["ask_human"] == [fill(Q_STALE_DROPPED, {"slot": obs["target"]})]
    sends = write_sends(record)
    assert len(cmds) == 2 and sends[1] - sends[0] >= 1.0


def test_stale_clock_put_picks_a_slot_whose_switch_is_visible_and_refuses_the_selected_one(tmp_path):
    async def body(services, runner):
        device = services.sim.cloud.device(ADDRESS)
        # custom-1 is showing, custom-2 holds the same pattern, so auto must pick custom-3
        device.cloud[("color-mode", None)] = device.applied[("color-mode", None)] = "individual"
        device.cloud[(R.FRAME_CLASS, "custom-2")] = R.deep_copy_value(
            device.cloud[(R.FRAME_CLASS, "custom-1")]
        )
        slot1 = R.frame_entries(device.cloud[(R.FRAME_CLASS, "custom-1")])
        device.cloud[(R.FRAME_CLASS, "custom-3")] = R.frame_value(
            device.cloud[(R.FRAME_CLASS, "custom-3")],
            [R.FrameEntry(0, 0, 255)] * len(slot1),
            expected_length=len(slot1),
        )
        auto = await runner.run("stale_clock_put", "bench-kit")
        writes = services.sim.stats()["writes"]
        same = await runner.run(
            "stale_clock_put", "bench-kit", {"slot": auto["result"]["observations"]["target"]}
        )
        return auto, same, services.sim.stats()["writes"] - writes

    auto, same, writes = bench(tmp_path, body, sim_settings=QUIET)
    assert auto["result"]["observations"]["target"] == "custom-3"
    assert same["result"]["verdict"] == "error" and "already selected" in same["result"]["summary"]
    assert writes == 0


# ---- active_slot_rewrite -----------------------------------------------------------------------------------------
def test_active_slot_rewrite_writes_only_the_showing_slot(tmp_path):
    async def body(services, runner):
        await runner.run("hue_ramp", "bench-kit")  # custom-3 selected, showing the rainbow
        await asyncio.sleep(3)
        before = view(services)
        n = len(commands(services))
        record = await runner.run("active_slot_rewrite", "bench-kit")
        await asyncio.sleep(3)
        return record, before, view(services), commands(services)[n:]

    record, before, after, cmds = bench(tmp_path, body, sim_settings=QUIET)
    result = record["result"]
    obs = result["observations"]
    assert result["verdict"] == "needs_eyes" and obs["slot"] == "custom-3"
    assert result["ask_human"] == [fill(Q_ACTIVE_REWRITE, {"slot": "custom-3"})]
    assert obs["hash_written"] != obs["hash_before"] and obs["hash_after"] == obs["hash_written"]
    assert obs["still_selected"] is True
    # one PUT carrying only the slot object: no select, no colour mode, no power
    assert len(cmds) == 1 and cmds[0]["rows"] == 1
    assert R.slot_hash(after[(R.FRAME_CLASS, "custom-3")]) == obs["hash_written"]
    assert R.slot_hash(before[(R.FRAME_CLASS, "custom-3")]) == obs["hash_before"]
    assert after[("color-individual", "custom")] == "custom-3"
    # the new pattern differs ON THE PUCKS (entries 0–29 of the 60-entry frame); the unused entries stay OFF
    old = R.frame_entries(before[(R.FRAME_CLASS, "custom-3")])
    new = R.frame_entries(after[(R.FRAME_CLASS, "custom-3")])
    assert len(new) == 60 and new[:30] != old[:30] and new[30:] == [R.OFF_ENTRY] * 30


def test_active_slot_rewrite_refuses_without_a_showing_slot_or_proven_frames(tmp_path, monkeypatch):
    async def body(services, runner):
        await runner.run("restore", "bench-kit")  # whole-strand white: no slot is showing
        writes = services.sim.stats()["writes"]
        none_showing = await runner.run("active_slot_rewrite", "bench-kit")
        await runner.run("gate_b", "bench-kit")  # custom-2 showing
        writes2 = services.sim.stats()["writes"]
        other = await runner.run("active_slot_rewrite", "bench-kit", {"slot": "custom-1"})
        monkeypatch.setattr(RunContext, "t1_allowed", property(lambda self: False))
        unproven = await runner.run("active_slot_rewrite", "bench-kit")
        return none_showing, other, unproven, writes, writes2, services.sim.stats()["writes"]

    none_showing, other, unproven, w1, w2, w3 = bench(tmp_path, body, sim_settings=QUIET)
    for record in (none_showing, other, unproven):
        assert record["result"]["verdict"] == "error" and "Nothing was sent" in record["result"]["summary"]
    assert "isn't showing a slot" in none_showing["result"]["summary"]
    assert "SHOWING (custom-2)" in other["result"]["summary"]
    assert "Gate C" in unproven["result"]["summary"]
    assert w2 - w1 == 1 and w3 == w2  # only Gate B's select went out


# ---- rate_ramp ---------------------------------------------------------------------------------------------------
def test_rate_ramp_steps_down_to_one_write_a_second_and_reads_each_back(tmp_path):
    async def body(services, runner):
        record = await runner.run("rate_ramp", "bench-kit", {"writes_per_step": 3})
        return record, services.sim.stats()

    record, stats = bench(tmp_path, body, sim_settings=QUIET)
    result = record["result"]
    obs = result["observations"]
    assert result["verdict"] == "pass" and obs["writes_sent"] == 9 and obs["silent_drops"] == 0
    assert [s["spacing_s"] for s in obs["steps"]] == list(RAMP_SPACINGS_S) == [5.0, 2.0, 1.0]
    assert all(s["writes"] == s["stored"] == 3 for s in obs["steps"])
    sends = write_sends(record)
    gaps = [b - a for a, b in pairwise(sends)]
    expected = [5.0, 5.0, 2.0, 2.0, 2.0, 1.0, 1.0, 1.0]  # the first gap of a step uses that step's spacing
    assert all(g >= e - 1e-6 for g, e in zip(gaps, expected, strict=True))
    assert obs["min_gap_s"] >= 1.0 and stats["min_interval_violations_total"] == 0
    # consecutive frames always differ, so a dropped write can't hide behind an identical earlier one
    hashes = [w["sent_hash"] for w in obs["writes"]]
    assert len(set(hashes)) == 9 and all(w["readback"] == "stored" for w in obs["writes"])
    assert obs["length"] == 60


def test_rate_ramp_counts_silent_drops(tmp_path):
    async def body(services, runner):
        services.sim.faults.set_silent_drop_rate(1.0)
        return await runner.run("rate_ramp", "bench-kit", {"writes_per_step": 2})

    record = bench(tmp_path, body, sim_settings=QUIET)
    obs = record["result"]["observations"]
    assert record["result"]["verdict"] == "info" and obs["silent_drops"] == 6 == obs["writes_sent"]
    assert "silent drops" in record["result"]["summary"]


def test_rate_ramp_stops_at_the_first_429_without_retrying(tmp_path):
    from conductor.drivers.base import Outcome
    from conductor.drivers.sim import _Exchange

    async def body(services, runner):
        original = services.sim._exchange
        seen = [0]

        async def third_write_throttled(device_id, *, write):
            if write:
                seen[0] += 1
                if seen[0] >= 3:
                    return _Exchange(Outcome.THROTTLED, 429, "HTTP 429 (test)")
            return await original(device_id, write=write)

        services.sim._exchange = third_write_throttled
        record = await runner.run("rate_ramp", "bench-kit", {"writes_per_step": 5})
        return record, seen[0]

    record, writes = bench(tmp_path, body, sim_settings=QUIET)
    obs = record["result"]["observations"]
    assert record["result"]["verdict"] == "fail" and obs["throttled"] is True
    assert (
        obs["writes_sent"] == 3 and writes == 3
    )  # the 429 was the last PUT: no executor retry, no more writes
    assert "429" in obs["stopped"] and "5 s spacing" in obs["stopped"]
    assert obs["steps"][0]["stored"] == 2 and len(obs["steps"]) == 1


def test_rate_ramp_bounds_and_needs_proven_frames(tmp_path, monkeypatch):
    spec = JOBS["rate_ramp"].spec
    assert coerce_params(spec, {"writes_per_step": "50"})["writes_per_step"] == 50
    for bad in ("0", "51", "2.5"):
        with pytest.raises(ValueError):
            coerce_params(spec, {"writes_per_step": bad})

    async def body(services, runner):
        monkeypatch.setattr(RunContext, "t1_allowed", property(lambda self: False))
        writes = services.sim.stats()["writes"]
        record = await runner.run("rate_ramp", "bench-kit")
        return record, services.sim.stats()["writes"] - writes

    record, writes = bench(tmp_path, body, sim_settings=QUIET)
    assert record["result"]["verdict"] == "error" and "Gate C" in record["result"]["summary"] and writes == 0


# ---- head_count_boot ---------------------------------------------------------------------------------------------
def test_head_count_boot_needs_confirmation_and_never_writes(tmp_path):
    async def body(services, runner):
        writes = services.sim.stats()["writes"]
        refused = await runner.run("head_count_boot", "bench-kit")
        sim = await runner.run("head_count_boot", "bench-kit", {"confirmed": True})
        services.update_registry(
            "string-20", {"role": "spare", "account": "bench", "profile": "string-20seg"}
        )
        short = await runner.run("head_count_boot", "string-20", {"confirmed": "1"})
        return refused, sim, short, services.sim.stats()["writes"] - writes

    refused, sim, short, writes = bench(tmp_path, body)
    assert writes == 0
    assert (
        refused["result"]["verdict"] == "error" and "unplug strings 2 and 3" in refused["result"]["summary"]
    )
    obs = sim["result"]["observations"]
    assert sim["result"]["verdict"] == "needs_eyes" and sim["result"]["ask_human"] == [Q_HEAD_BOOT]
    # the simulated kit reports the real kit's capacity (60 entries) for its 30 pucks: that is NOT a change — a
    # controller that always reports 60 can't show missing pucks (only a count below the full kit's 30 would)
    assert obs["segment_hint"] == 60 and obs["profile_reflects_detected_count"] is False
    assert (
        obs["profile_framebuffer_lengths"] == {s: 60 for s in R.SLOTS} == obs["readback_framebuffer_lengths"]
    )
    assert "60-segment" in obs["hints"]["custom-1"]
    assert "SIMULATED" in sim["result"]["summary"] and "visually" in sim["result"]["summary"]
    assert "never fewer than the full kit" in sim["result"]["summary"]
    assert sim["artifacts"] == ["profile_boot_raw"]
    # a profile that does report fewer heads (the 20-segment string against a full kit of 30)
    short_obs = short["result"]["observations"]
    assert short_obs["profile_reflects_detected_count"] is True and "CHANGED" in short["result"]["summary"]


def test_confirm_labels_reach_the_page(tmp_path):
    async def body(services, runner):
        return {s["name"]: s for s in runner.specs()}

    specs = bench(tmp_path, body)
    assert set(CONFIRM_LABELS) == {"gate_b_unplugged", "head_count_boot"}
    for name, label in CONFIRM_LABELS.items():
        assert specs[name]["confirm_label"] == label and "confirmed" in specs[name]["params"]
    assert specs["rate_ramp"]["bounds"] == {"writes_per_step": [1, 50]}
    assert specs["gate_b"]["confirm_label"] is None
