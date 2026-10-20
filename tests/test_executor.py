"""CommandExecutor semantics on virtual time: coalescing, rate budget, concurrency, retries, readback, locks."""

from __future__ import annotations

import asyncio
from itertools import pairwise

import pytest

from conductor.clock import VirtualClock, run_virtual
from conductor.config import ExecutorSettings
from conductor.drivers.base import Outcome, StateRow
from conductor.executor import CommandExecutor
from conductor.executor_api import Command, CommandStatus
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager
from conductor.rows import brightness, color_mode, power
from conductor.telemetry import Telemetry
from tests.fake_driver import FakeDriver, Step

EPS = 1e-6


class World:
    def __init__(self, **policy):
        self.clock = VirtualClock()
        self.driver = FakeDriver(self.clock)
        self.locks = DeviceLockManager(clock=self.clock)
        self.telemetry = Telemetry(None, self.clock)
        self.policy = ExecutorSettings(**policy)
        self.executor = CommandExecutor(
            self.driver, locks=self.locks, telemetry=self.telemetry, clock=self.clock, policy=self.policy
        )


def look(pct: int) -> list[StateRow]:
    return [power(True), color_mode("white"), brightness(pct)]


def cmd(device: str = "sim-a", pct: int = 50, **kw) -> Command:
    return Command(
        device_id=device, rows=kw.pop("rows", look(pct)), purpose=kw.pop("purpose", Purpose.ENGINE), **kw
    )


def gaps(records) -> list[float]:
    times = [r.sent_mono for r in records]
    return [b - a for a, b in pairwise(times)]


# ---- coalescing and the rate budget -----------------------------------------------------------------------------
def test_button_mashing_never_grows_a_queue():
    w = World()

    async def main():
        tasks, depths = [], []
        for i in range(200):  # 200 taps in 10 s, one device
            tasks.append(asyncio.create_task(w.executor.submit(cmd(pct=1 + i % 100))))
            await asyncio.sleep(0.05)
            depths.append(w.executor.queue_depth())
        results = await asyncio.gather(*tasks)
        return depths, results

    depths, results = run_virtual(main)
    assert max(depths) <= 2
    writes = w.driver.writes_to("sim-a")
    assert len(writes) <= 12  # ~1 write/s over 10 s, not 200
    assert all(g >= 1.0 - EPS for g in gaps(writes))
    statuses = [r.status for r in results]
    assert statuses[-1] is CommandStatus.APPLIED
    assert statuses.count(CommandStatus.APPLIED) + statuses.count(CommandStatus.SUPERSEDED) == 200
    assert statuses.count(CommandStatus.SUPERSEDED) >= 180
    # The last tap's look is what the device ends up showing.
    assert w.driver.state["sim-a"][("brightness", None)].value == 1 + 199 % 100
    assert w.executor.queue_depth() == 0
    assert not w.executor.in_flight("sim-a")


def test_write_sends_are_spaced_by_the_min_interval_including_retries():
    w = World(backoff_s=[0.1, 0.1, 0.1], max_attempts=4)
    w.driver.latency_s = 0.05
    w.driver.script_write("sim-a", Outcome.THROTTLED, Outcome.SERVER_ERROR, Outcome.OK, Outcome.TIMEOUT)

    async def main():
        first = await w.executor.submit(cmd(pct=10))
        results = [first]
        for pct in (20, 30, 40):
            results.append(await w.executor.submit(cmd(pct=pct, verify=False)))
        return results

    results = run_virtual(main)
    assert [r.status for r in results] == [CommandStatus.APPLIED] * 4
    assert len(results[0].attempts) == 3
    assert len(results[1].attempts) == 2  # TIMEOUT then OK
    writes = w.driver.writes_to("sim-a")
    assert len(writes) == 7
    assert min(gaps(writes)) >= 1.0 - EPS


def test_nine_device_burst_goes_out_in_one_wave():
    w = World()
    devices = [f"sim-r{r}c{c}" for r in range(3) for c in range(3)]

    async def main():
        start = asyncio.get_running_loop().time()
        results = await w.executor.burst([cmd(d, 60, label="mode: calm") for d in devices])
        return start, results

    start, results = run_virtual(main)
    assert [r.status for r in results] == [CommandStatus.APPLIED] * 9
    assert [r.command.device_id for r in results] == devices
    sends = [w.driver.writes_to(d)[0].sent_mono for d in devices]
    assert max(sends) - min(sends) < 0.005
    assert min(sends) - start < 0.005
    assert w.driver.max_active["acct"] == 9


def test_account_concurrency_cap_is_respected_for_writes_and_reads():
    w = World(account_concurrency=2)
    w.driver.latency_s = 0.5
    devices = [f"sim-a{i}" for i in range(6)] + [f"sim-b{i}" for i in range(3)]
    for d in devices:
        w.driver.accounts[d] = "account-a" if d.startswith("sim-a") else "account-b"

    async def main():
        return await w.executor.burst([cmd(d) for d in devices])

    results = run_virtual(main)
    assert all(r.status is CommandStatus.APPLIED for r in results)
    assert w.driver.max_active["account-a"] == 2
    assert w.driver.max_active["account-b"] == 2
    assert len(w.driver.reads) == 9


# ---- retries ----------------------------------------------------------------------------------------------------
def test_throttled_then_ok_is_applied_after_two_attempts():
    w = World()
    w.driver.script_write("sim-a", Outcome.THROTTLED, Outcome.OK)

    result = run_virtual(lambda: w.executor.submit(cmd()))
    assert result.status is CommandStatus.APPLIED
    assert [a.outcome for a in result.attempts] == [Outcome.THROTTLED, Outcome.OK]
    first, second = w.driver.writes_to("sim-a")
    assert second.sent_mono - first.acked_mono >= w.policy.backoff_s[0] - EPS
    assert result.timings.sent_at is not None and result.timings.acked_at is not None
    assert result.timings.verified_at is not None


def test_retries_give_up_after_max_attempts():
    w = World()
    w.driver.script_write("sim-a", Outcome.THROTTLED, Outcome.SERVER_ERROR, Outcome.CONNECTION, Outcome.OK)

    result = run_virtual(lambda: w.executor.submit(cmd()))
    assert result.status is CommandStatus.FAILED
    assert len(result.attempts) == 3
    assert "Gave up after 3 attempts" in result.error
    assert len(w.driver.writes) == 3


@pytest.mark.parametrize("outcome", [Outcome.CLIENT_ERROR, Outcome.NOT_FOUND, Outcome.AUTH])
def test_non_retryable_outcomes_fail_at_once(outcome):
    w = World()
    w.driver.script_write("sim-a", outcome, Outcome.OK)

    result = run_virtual(lambda: w.executor.submit(cmd()))
    assert result.status is CommandStatus.FAILED
    assert len(result.attempts) == 1
    assert len(w.driver.writes) == 1
    assert result.error
    assert w.driver.reads == []


def test_denied_resolves_immediately_with_gate_telemetry():
    w = World()
    w.driver.script_write(
        "sim-a", Step(Outcome.DENIED, error="Device sim-a is not in the effective allowlist.")
    )

    result = run_virtual(lambda: w.executor.submit(cmd(purpose=Purpose.ENGINE)))
    assert result.status is CommandStatus.DENIED
    assert len(result.attempts) == 1
    assert "allowlist" in result.error
    denied = w.telemetry.recent(kind="gate_denied")
    assert len(denied) == 1
    assert denied[0]["device_id"] == "sim-a" and denied[0]["purpose"] == "engine"
    # Nothing left the process, so the device's rate budget is untouched.
    assert w.executor.budget("sim-a")["writes_60s"] == 0


def test_newer_command_during_backoff_supersedes_the_retrying_one():
    w = World(backoff_s=[4.0])
    w.driver.script_write("sim-a", Outcome.THROTTLED)

    async def main():
        older = asyncio.create_task(w.executor.submit(cmd(pct=10)))
        await asyncio.sleep(1.5)  # inside the 4 s backoff
        newer = await w.executor.submit(cmd(pct=90))
        return await older, newer

    older, newer = run_virtual(main)
    assert older.status is CommandStatus.SUPERSEDED
    assert len(older.attempts) == 1
    assert newer.status is CommandStatus.APPLIED
    writes = w.driver.writes_to("sim-a")
    assert len(writes) == 2
    assert writes[1].rows == look(90)
    assert writes[1].sent_mono == pytest.approx(1.5)


def test_newer_command_replaces_pending_without_sending_it():
    w = World()

    async def main():
        first = asyncio.create_task(w.executor.submit(cmd(pct=10)))
        await asyncio.sleep(0.01)  # first is in flight
        second = asyncio.create_task(w.executor.submit(cmd(pct=20)))
        await asyncio.sleep(0)
        third = asyncio.create_task(w.executor.submit(cmd(pct=30)))
        await asyncio.sleep(0)
        assert w.executor.queue_depth() == 2
        return await first, await second, await third

    first, second, third = run_virtual(main)
    assert second.status is CommandStatus.SUPERSEDED and second.attempts == []
    assert second.timings.sent_at is None
    assert third.status is CommandStatus.APPLIED
    assert first.status is CommandStatus.SUPERSEDED  # its verification gave way to the newer command
    assert [w.rows for w in w.driver.writes] == [look(10), look(30)]


# ---- readback -----------------------------------------------------------------------------------------------------
def test_readback_missing_rows_is_a_mismatch():
    w = World()
    drop = frozenset({("brightness", None)})
    w.driver.script_read("sim-a", Step(drop=drop), Step(drop=drop), Step(drop=drop))

    result = run_virtual(lambda: w.executor.submit(cmd()))
    assert result.status is CommandStatus.MISMATCH
    assert len(w.driver.reads) == 3
    assert [(m.function_class, m.missing) for m in result.mismatches] == [("brightness", True)]
    assert result.timings.verified_at is None
    assert "brightness" in result.error


def test_empty_readback_on_a_200_is_never_a_match():
    w = World()
    w.driver.script_read("sim-a", Step(rows=[]), Step(rows=[]), Step(rows=[]))

    result = run_virtual(lambda: w.executor.submit(cmd()))
    assert result.status is CommandStatus.MISMATCH
    assert len(result.mismatches) == 3


def test_readback_that_catches_up_within_the_window_is_applied():
    w = World()
    w.driver.script_write("sim-a", Step(apply=False))  # cloud accepted but hasn't stored it yet
    stale = [power(True), color_mode("white"), brightness(5)]
    w.driver.script_read("sim-a", Step(rows=stale), Step(rows=look(50)))

    result = run_virtual(lambda: w.executor.submit(cmd(pct=50)))
    assert result.status is CommandStatus.APPLIED
    assert result.mismatches == []
    assert len(w.driver.reads) == 2
    t = result.timings
    assert t.verified_at is not None
    # ack (0.2) + delay (1.0) + first GET (0.1) + interval (1.0) + second GET (0.1)
    assert t.verified_at - t.acked_at == pytest.approx(2.2)
    assert w.driver.reads[0].sent_mono - w.driver.writes[0].acked_mono == pytest.approx(1.0)


def test_readback_uses_expect_when_given():
    w = World()
    rows = look(50)
    result = run_virtual(lambda: w.executor.submit(cmd(rows=rows, expect=[power(True)])))
    assert result.status is CommandStatus.APPLIED


def test_failed_readbacks_resolve_mismatch_with_an_honest_error():
    w = World()
    w.driver.script_read("sim-a", Outcome.TIMEOUT, Outcome.TIMEOUT, Outcome.TIMEOUT)
    result = run_virtual(lambda: w.executor.submit(cmd()))
    assert result.status is CommandStatus.MISMATCH
    assert "not verified" in result.error


def test_verify_false_skips_readback():
    w = World()
    result = run_virtual(lambda: w.executor.submit(cmd(verify=False)))
    assert result.status is CommandStatus.APPLIED
    assert result.timings.verified_at is None
    assert w.driver.reads == []


# ---- locks and validation -------------------------------------------------------------------------------------------
def test_engine_command_to_a_locked_device_is_locked_and_not_sent():
    w = World()

    async def main():
        w.locks.acquire("sim-a", "module page · tester")
        return await w.executor.submit(cmd(purpose=Purpose.ENGINE))

    result = run_virtual(main)
    assert result.status is CommandStatus.LOCKED
    assert "module page" in result.error
    assert w.driver.writes == []


def test_bench_commands_need_a_current_lease():
    w = World()

    async def main():
        unlocked = await w.executor.submit(cmd(purpose=Purpose.BENCH, lease_token="anything"))
        lease = w.locks.acquire("sim-a", "bench · gate_b")
        no_token = await w.executor.submit(cmd(purpose=Purpose.BENCH))
        wrong = await w.executor.submit(cmd(purpose=Purpose.BENCH, lease_token="not-the-lease"))
        good = await w.executor.submit(cmd(purpose=Purpose.BENCH, lease_token=lease.token))
        await asyncio.sleep(w.locks.idle_timeout_s + 1)  # the lease goes stale
        stale = await w.executor.submit(cmd(purpose=Purpose.BENCH, lease_token=lease.token))
        lease2 = w.locks.acquire("sim-a", "bench · gate_c")
        w.locks.release("sim-a", lease2.token)
        released = await w.executor.submit(cmd(purpose=Purpose.BENCH, lease_token=lease2.token))
        return unlocked, no_token, wrong, good, stale, released

    unlocked, no_token, wrong, good, stale, released = run_virtual(main)
    for r in (unlocked, no_token, wrong, stale, released):
        assert r.status is CommandStatus.LOCKED
        assert r.attempts == []
    assert good.status is CommandStatus.APPLIED
    assert len(w.driver.writes) == 1 and w.driver.writes[0].purpose is Purpose.BENCH


def test_lock_taken_while_a_command_waits_stops_it():
    w = World()

    async def main():
        first = asyncio.create_task(w.executor.submit(cmd(pct=10, verify=False)))
        await asyncio.sleep(0.3)
        second = asyncio.create_task(w.executor.submit(cmd(pct=20)))  # waits for the rate budget
        await asyncio.sleep(0.1)
        w.locks.acquire("sim-a", "module page · alon")
        return await first, await second

    first, second = run_virtual(main)
    assert first.status is CommandStatus.APPLIED
    assert second.status is CommandStatus.LOCKED
    assert len(w.driver.writes) == 1


@pytest.mark.parametrize(
    "rows",
    [[], [power(True), StateRow("available", None, True)], ["not a row"]],
)
def test_invalid_rows_are_refused_before_sending(rows):
    w = World()
    result = run_virtual(lambda: w.executor.submit(cmd(rows=rows)))
    assert result.status is CommandStatus.INVALID
    assert result.error
    assert w.driver.writes == []


# ---- observability ------------------------------------------------------------------------------------------------
def test_telemetry_records_attempts_readbacks_and_one_command_event():
    w = World()
    w.driver.script_write("sim-a", Outcome.THROTTLED, Outcome.OK)

    result = run_virtual(lambda: w.executor.submit(cmd(label="mode: calm", source="kiosk")))
    assert result.simulated is True
    attempts = w.telemetry.recent(kind="attempt")
    assert [(a["n"], a["outcome"], a["http_status"]) for a in attempts] == [
        (1, "throttled", 429),
        (2, "ok", 200),
    ]
    readbacks = w.telemetry.recent(kind="readback")
    assert len(readbacks) == 1 and readbacks[0]["mismatches"] == 0 and readbacks[0]["available"] is True
    (event,) = w.telemetry.recent(kind="command")
    assert event["status"] == "applied"
    assert event["label"] == "mode: calm" and event["source"] == "kiosk" and event["purpose"] == "engine"
    assert (
        event["rows"] == 3 and event["attempts"] == 2 and event["mismatches"] == 0 and event["bytes"] == 300
    )
    t = event["timings"]
    assert t["queued_at"] == pytest.approx(w.clock.epoch)
    assert t["queued_at"] <= t["sent_at"] < t["acked_at"] < t["verified_at"]
    stats = w.telemetry.command_stats()
    assert stats["count"] == 1 and stats["failures"] == 0


def test_listeners_budget_and_in_flight():
    w = World()
    seen, submitted, during = [], [], []
    w.executor.add_listener(seen.append)
    w.executor.add_submit_listener(submitted.append)

    async def main():
        task = asyncio.create_task(w.executor.submit(cmd()))
        await asyncio.sleep(0.1)
        during.append((w.executor.in_flight("sim-a"), w.executor.budget("sim-a")))
        result = await task
        return result, w.executor.budget("sim-a"), w.executor.budget()

    result, after, overall = run_virtual(main)
    assert seen == [result] and [c.device_id for c in submitted] == ["sim-a"]
    busy, gauge = during[0]
    assert busy is True
    assert gauge["writes_60s"] == 1 and gauge["account"] == "acct"
    assert gauge["next_write_in_s"] == pytest.approx(0.9)
    assert gauge["in_flight"] is True
    assert after["next_write_in_s"] == 0.0 and after["in_flight"] is False
    assert overall["accounts"]["acct"]["writes_60s"] == 1
    assert overall["devices"]["sim-a"]["writes_60s"] == 1
    assert overall["queue_depth"] == 0
    assert w.executor.budget("sim-unknown")["writes_60s"] == 0


def test_account_writes_counts_the_accounts_attempts_within_a_span():
    """The stepped runner's cap check (D75) reads the account's writes in the last 60 s less the per-device gap:
    every attempt on any of the account's devices, retries included, none older than the span."""
    w = World(backoff_s=[1.0], max_attempts=2, readback_delay_s=0.0)
    # sim-b's first attempt is a 429, its retry goes 1 s later
    w.driver.script_write("sim-b", Outcome.THROTTLED)

    async def main():
        await w.executor.submit(cmd("sim-a", verify=False))  # t 0
        await asyncio.sleep(10.0)
        b, c = cmd("sim-b", verify=False), cmd("sim-c", verify=False)
        await asyncio.gather(w.executor.submit(b), w.executor.submit(c))
        at_11 = (w.executor.account_writes("sim-a"), w.executor.account_writes("sim-c", within_s=5.0))
        await asyncio.sleep(59.5 - w.clock.monotonic())  # sim-a's write is 59.5 s old
        late = (w.executor.account_writes("sim-b"), w.executor.account_writes("sim-b", within_s=59.0))
        return at_11, late, w.executor.budget("sim-a")["account_writes_60s"]

    (whole, recent), (still, shared), gauge = run_virtual(main)
    assert whole == 4 and recent == 3  # sim-a, sim-b twice (429, retry), sim-c on one account
    assert still == 4 == gauge and shared == 3  # sim-a's write leaves the 60 s window within the next second
    assert w.executor.account_writes("sim-unknown") == 4  # the fake driver puts every device on one account


def test_close_resolves_unfinished_commands_and_refuses_new_ones():
    w = World()
    w.driver.latency_s = 5.0

    async def main():
        first = asyncio.create_task(w.executor.submit(cmd(pct=10)))
        await asyncio.sleep(0.1)
        second = asyncio.create_task(w.executor.submit(cmd(pct=20)))
        await asyncio.sleep(0)
        await w.executor.close()
        late = await w.executor.submit(cmd(pct=30))
        return await first, await second, late

    first, second, late = run_virtual(main)
    assert first.status is CommandStatus.FAILED and "shut down" in first.error
    assert second.status is CommandStatus.FAILED and second.attempts == []
    assert late.status is CommandStatus.FAILED
    assert len(w.driver.writes) == 1
    assert w.executor.queue_depth() == 0


# ---- review fixes: shutdown mid-command, bounded rate history, token freshness before a burst, withdraw -------
@pytest.mark.parametrize("phase", ["verifying", "retrying"])
def test_close_during_verify_or_retry_resolves_failed_with_one_command_event(phase):
    """EXE-1: close() while a command waits to verify (or to retry) must resolve it FAILED, not hang."""
    w = World(readback_delay_s=5.0, backoff_s=[5.0], max_attempts=3)
    if phase == "retrying":
        w.driver.script_write("sim-a", Outcome.THROTTLED)
    outstanding = []
    w.executor.add_submit_listener(lambda c: outstanding.append(c))
    finished = []
    w.executor.add_listener(finished.append)

    async def main():
        task = asyncio.create_task(w.executor.submit(cmd()))
        await asyncio.sleep(1.0)  # acked (0.2 s); now waiting out readback_delay_s / the retry backoff
        assert w.executor.in_flight("sim-a")
        await w.executor.close()
        return await asyncio.wait_for(task, 5.0)

    result = run_virtual(main)
    assert result.status is CommandStatus.FAILED and "shut down" in result.error
    events = w.telemetry.recent(kind="command")
    assert len(events) == 1 and events[0]["status"] == "failed"
    assert finished == [result] and len(outstanding) == 1  # the tracker hears about the end
    assert w.executor.queue_depth() == 0
    assert len(w.driver.writes) == 1


def test_rate_history_is_pruned_without_anyone_reading_the_budget():
    """EXE-2: the per-device and per-account send-time deques stay bounded when /ops never polls budget()."""
    w = World(readback_delay_s=0.0, readback_interval_s=0.2)

    async def main():
        for i in range(150):  # ~150 s of writes at the 1 write/s budget, nobody polls budget()
            await w.executor.submit(cmd(pct=1 + i % 100, verify=False))

    run_virtual(main)
    slot = w.executor._slots["sim-a"]
    assert len(w.driver.writes) == 150
    assert len(slot.sent) <= 61
    assert len(w.executor._account_sent["acct"]) <= 61


class FreshTokenDriver(FakeDriver):
    """A FakeDriver with the optional `ensure_fresh_token` hook the AferoDriver implements."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.fresh_calls: list[tuple[str, float]] = []
        self.fresh_outcome = Outcome.OK

    async def ensure_fresh_token(self, account: str, min_left_s: float = 15.0, *, purpose=None) -> Outcome:
        self.fresh_calls.append((account, self.clock.monotonic()))
        await self.clock.sleep(0.3)  # one cheap GET that forces a refresh
        return self.fresh_outcome


def test_burst_makes_sure_each_accounts_token_is_fresh_before_any_put():
    """AF-4: a mode-change burst asks the driver for a token with >= 15 s left, once per account, first."""
    w = World()
    w.driver = FreshTokenDriver(w.clock, accounts={"sim-a": "a1", "sim-b": "a1", "sim-c": "a2"})
    w.executor = CommandExecutor(
        w.driver, locks=w.locks, telemetry=w.telemetry, clock=w.clock, policy=w.policy
    )

    async def main():
        return await w.executor.burst([cmd("sim-a"), cmd("sim-b"), cmd("sim-c")])

    results = run_virtual(main)
    assert all(r.ok for r in results)
    assert sorted(a for a, _ in w.driver.fresh_calls) == ["a1", "a2"]
    last_check = max(t for _, t in w.driver.fresh_calls)
    assert min(wr.sent_mono for wr in w.driver.writes) >= last_check + 0.3 - EPS
    events = w.telemetry.recent(kind="token")
    assert sorted(e["account"] for e in events) == ["a1", "a2"]
    assert all(e["outcome"] == "ok" for e in events)


def test_burst_still_sends_when_the_token_check_fails_and_drivers_without_the_hook_work():
    w = World()
    w.driver = FreshTokenDriver(w.clock)
    w.driver.fresh_outcome = Outcome.AUTH
    w.executor = CommandExecutor(
        w.driver, locks=w.locks, telemetry=w.telemetry, clock=w.clock, policy=w.policy
    )
    results = run_virtual(lambda: w.executor.burst([cmd("sim-a"), cmd("sim-b")]))
    assert all(r.ok for r in results)  # the PUTs themselves report what really happened
    assert [e["outcome"] for e in w.telemetry.recent(kind="token")] == ["auth"]

    plain = World()  # FakeDriver has no hook: nothing is called, nothing recorded
    results = run_virtual(lambda: plain.executor.burst([cmd("sim-a"), cmd("sim-b")]))
    assert all(r.ok for r in results) and plain.telemetry.recent(kind="token") == []


def test_withdraw_stops_a_retrying_command_and_drops_the_pending_one():
    """MOD-2: Hold withdraws the page's commands: a throttled command stops retrying, a pending one is never sent."""
    w = World(backoff_s=[2.0, 2.0], max_attempts=3)
    w.driver.script_write("sim-a", Outcome.THROTTLED, Outcome.OK)

    async def main():
        lease = w.locks.acquire("sim-a", "module page · ops")
        first = asyncio.create_task(w.executor.submit(cmd(purpose=Purpose.BENCH, lease_token=lease.token)))
        await asyncio.sleep(0.5)  # throttled at 0.2 s; waiting 2 s to retry
        withdrawn = w.executor.withdraw("sim-a", lease_token=lease.token, reason="Hold is on.")
        return withdrawn, await first

    withdrawn, first = run_virtual(main)
    assert withdrawn == 1
    assert first.status is CommandStatus.SUPERSEDED and first.error == "Hold is on."
    assert len(w.driver.writes) == 1  # the retry was never sent


def test_withdraw_leaves_other_leases_and_verification_alone():
    w = World(readback_delay_s=1.0)

    async def main():
        lease = w.locks.acquire("sim-a", "module page · ops")
        task = asyncio.create_task(w.executor.submit(cmd(purpose=Purpose.BENCH, lease_token=lease.token)))
        await asyncio.sleep(0.5)  # acked; verifying (the write already went out, readback is read-only)
        assert w.executor.withdraw("sim-a", lease_token="someone-else", reason="x") == 0
        w.executor.withdraw("sim-a", lease_token=lease.token, reason="Hold is on.")
        return await task

    result = run_virtual(main)
    assert result.status is CommandStatus.APPLIED
    assert len(w.driver.reads) == 1 and w.driver.reads[0].sent_mono == pytest.approx(0.2 + 1.0)
