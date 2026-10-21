"""DeviceStateTracker: honest Ready / Updating / Needs attention through a full command lifecycle."""

from __future__ import annotations

import asyncio
import json

from conductor.clock import VirtualClock, run_virtual
from conductor.config import ExecutorSettings
from conductor.drivers.base import Outcome, ReadResult, StateRow, WriteResult
from conductor.executor import CommandExecutor
from conductor.executor_api import Command, CommandResult, CommandStatus, DeviceStatus
from conductor.gate import Purpose
from conductor.locks import DeviceLockManager
from conductor.rows import Look, brightness, full_state_vector
from conductor.state import DeviceStateTracker
from conductor.telemetry import Telemetry
from tests.fake_driver import FakeDriver, Step

CALM = Look(kind="white", cct=2700, brightness=60)
GOODNIGHT = Look(kind="white", cct=2700, brightness=10)


class World:
    def __init__(self):
        self.clock = VirtualClock()
        self.driver = FakeDriver(self.clock)
        self.telemetry = Telemetry(None, self.clock)
        self.executor = CommandExecutor(
            self.driver,
            locks=DeviceLockManager(clock=self.clock),
            telemetry=self.telemetry,
            clock=self.clock,
            policy=ExecutorSettings(),
        )
        self.tracker = DeviceStateTracker(self.clock, self.telemetry)
        self.tracker.attach(self.executor)
        self.pushes: list[tuple[str, dict]] = []
        self.tracker.add_listener(lambda a, s: self.pushes.append((a, s)))

    def desire(self, address: str, look: Look, **kw) -> list[StateRow]:
        rows = full_state_vector(look)
        self.tracker.set_desired(
            address, rows, label=kw.get("label", "mode: calm"), source="kiosk", look=look
        )
        return rows

    def read(self, address: str, rows: list[StateRow], available: bool = True) -> ReadResult:
        return ReadResult(
            address,
            Outcome.OK,
            rows=[*rows, StateRow("available", None, available)],
            http_status=200,
            fetched_at=self.clock.now(),
        )


def test_full_lifecycle_unknown_updating_ready_offline_and_recovery():
    w = World()
    seen: list[DeviceStatus] = []

    async def main():
        seen.append(w.tracker.status("sim-a"))  # never heard of it
        rows = w.desire("sim-a", CALM)
        seen.append(w.tracker.status("sim-a"))  # desired, never read back
        task = asyncio.create_task(
            w.executor.submit(Command("sim-a", rows, Purpose.ENGINE, label="mode: calm"))
        )
        await asyncio.sleep(0)
        seen.append(w.tracker.status("sim-a"))  # queued
        await asyncio.sleep(1.1)
        seen.append(w.tracker.status("sim-a"))  # acked, verifying
        await task
        seen.append(w.tracker.status("sim-a"))  # readback matched
        # A true-up readback says the strand dropped off Wi-Fi.
        w.tracker.note_readback("sim-a", w.read("sim-a", rows, available=False))
        seen.append(w.tracker.status("sim-a"))
        offline = w.tracker.snapshot("sim-a")
        # It comes back showing the look.
        w.tracker.note_readback("sim-a", w.read("sim-a", rows, available=True))
        seen.append(w.tracker.status("sim-a"))
        return offline

    offline = run_virtual(main)
    assert seen == [
        DeviceStatus.UNKNOWN,
        DeviceStatus.UNKNOWN,
        DeviceStatus.UPDATING,
        DeviceStatus.UPDATING,
        DeviceStatus.READY,
        DeviceStatus.NEEDS_ATTENTION,
        DeviceStatus.READY,
    ]
    assert "Offline" in offline["reason"] and offline["available"] is False
    snap = w.tracker.snapshot("sim-a")
    json.dumps(snap)  # JSON-safe
    assert snap["address"] == "sim-a" and snap["label"] == "mode: calm" and snap["source"] == "kiosk"
    assert snap["status"] == "ready" and snap["mismatches"] == []
    assert snap["look"] == CALM.to_json()
    assert {r["functionClass"] for r in snap["desired"]} >= {"power", "color-mode", "brightness"}
    assert snap["last_command"]["status"] == "applied"
    assert snap["reported_at"] is not None and snap["updated_at"] is not None
    assert [s["status"] for _, s in w.pushes][-2:] == ["needs_attention", "ready"]
    alerts = w.telemetry.recent(kind="alert")
    assert len(alerts) == 1 and alerts[0]["device_id"] == "sim-a"
    assert len(w.telemetry.recent(kind="readback")) == 3  # 1 from the executor, 2 true-ups


def test_mismatch_and_failure_need_attention_until_readback_matches():
    w = World()
    w.driver.script_read("sim-a", *[Step(drop=frozenset({("brightness", None)}))] * 3)

    async def main():
        rows = w.desire("sim-a", CALM)
        mismatch = await w.executor.submit(Command("sim-a", rows, Purpose.ENGINE))
        after_mismatch = w.tracker.snapshot("sim-a")
        assert w.tracker.mismatched(["sim-a", "sim-b"]) == ["sim-a"]
        # The reconciler re-asserts; it lands.
        again = await w.executor.submit(Command("sim-a", rows, Purpose.ENGINE, source="reconciler"))
        after_reassert = w.tracker.status("sim-a")
        # Next change fails outright.
        rows2 = w.desire("sim-a", GOODNIGHT, label="mode: goodnight")
        w.driver.script_write("sim-a", Outcome.CLIENT_ERROR)
        failed = await w.executor.submit(Command("sim-a", rows2, Purpose.ENGINE, label="mode: goodnight"))
        after_failure = w.tracker.snapshot("sim-a")
        # A true-up shows the device already at the goodnight look: honest READY again.
        w.tracker.note_readback("sim-a", w.read("sim-a", rows2))
        return mismatch, after_mismatch, again, after_reassert, failed, after_failure

    mismatch, after_mismatch, again, after_reassert, failed, after_failure = run_virtual(main)
    assert mismatch.status is CommandStatus.MISMATCH
    assert after_mismatch["status"] == "needs_attention"
    assert after_mismatch["mismatches"][0]["functionClass"] == "brightness"
    assert again.status is CommandStatus.APPLIED and after_reassert is DeviceStatus.READY
    assert failed.status is CommandStatus.FAILED
    assert after_failure["status"] == "needs_attention"
    assert "mode: goodnight failed" in after_failure["reason"]
    assert w.tracker.status("sim-a") is DeviceStatus.READY
    assert w.tracker.mismatched(["sim-a"]) == []


def test_superseded_commands_keep_the_device_updating_until_the_latest_resolves():
    w = World()
    statuses = []

    async def main():
        tasks = []
        for pct in (20, 40, 60, 80):
            rows = w.desire("sim-a", Look(kind="white", cct=2700, brightness=pct))
            tasks.append(asyncio.create_task(w.executor.submit(Command("sim-a", rows, Purpose.ENGINE))))
            await asyncio.sleep(0.05)
        await tasks[1]  # superseded while pending
        statuses.append(w.tracker.status("sim-a"))
        results = await asyncio.gather(*tasks)
        statuses.append(w.tracker.status("sim-a"))
        return results

    results = run_virtual(main)
    assert [r.status for r in results][-1] is CommandStatus.APPLIED
    assert statuses == [DeviceStatus.UPDATING, DeviceStatus.READY]
    assert w.tracker.snapshot("sim-a")["last_command"]["status"] == "applied"


def test_auth_outcome_needs_attention_until_a_write_succeeds():
    w = World()

    async def main():
        rows = w.desire("sim-a", CALM)
        w.driver.script_write("sim-a", Outcome.AUTH)
        await w.executor.submit(Command("sim-a", rows, Purpose.ENGINE))
        auth = w.tracker.snapshot("sim-a")
        # A readback that is also refused keeps it flagged.
        w.tracker.note_readback("sim-a", ReadResult("sim-a", Outcome.AUTH, http_status=401))
        still = w.tracker.status("sim-a")
        await w.executor.submit(Command("sim-a", rows, Purpose.ENGINE))
        return auth, still

    auth, still = run_virtual(main)
    assert auth["status"] == "needs_attention" and "re-sign-in" in auth["reason"]
    assert still is DeviceStatus.NEEDS_ATTENTION
    assert w.tracker.status("sim-a") is DeviceStatus.READY


def test_flagged_problems_and_denied_commands():
    w = World()

    async def main():
        rows = w.desire("sim-a", CALM)
        await w.executor.submit(Command("sim-a", rows, Purpose.ENGINE))
        w.tracker.flag_problem("sim-a", "Only 12 of 30 heads detected.")
        flagged = w.tracker.snapshot("sim-a")
        w.tracker.clear_problem("sim-a", "Only 12 of 30 heads detected.")
        cleared = w.tracker.status("sim-a")
        w.driver.script_write(
            "sim-a", Step(Outcome.DENIED, error="Device sim-a is not in the effective allowlist.")
        )
        await w.executor.submit(Command("sim-a", rows, Purpose.ENGINE, label="mode: calm"))
        return flagged, cleared

    flagged, cleared = run_virtual(main)
    assert flagged["status"] == "needs_attention" and flagged["problems"] == ["Only 12 of 30 heads detected."]
    assert cleared is DeviceStatus.READY
    snap = w.tracker.snapshot("sim-a")
    assert snap["status"] == "needs_attention" and "live gate" in snap["reason"]


def test_stale_readback_and_unverified_commands_are_not_ready():
    w = World()

    async def main():
        rows = w.desire("sim-a", CALM)
        w.tracker.note_readback("sim-a", w.read("sim-a", rows))
        ready = w.tracker.status("sim-a")
        rows2 = w.desire("sim-a", GOODNIGHT)
        stale = w.tracker.status("sim-a")  # the only readback predates the new look
        await w.executor.submit(Command("sim-a", rows2, Purpose.ENGINE, verify=False))
        unverified = w.tracker.status("sim-a")
        # Drift: the reconciler reads back something else (e.g. staff changed it in the app).
        w.tracker.note_readback("sim-a", w.read("sim-a", [*rows2[:-3], brightness(80), *rows2[-2:]]))
        return ready, stale, unverified

    ready, stale, unverified = run_virtual(main)
    assert ready is DeviceStatus.READY
    assert stale is DeviceStatus.UNKNOWN
    assert unverified is DeviceStatus.UNKNOWN  # APPLIED without readback is not proof
    snap = w.tracker.snapshot("sim-a")
    assert snap["status"] == "needs_attention" and "brightness" in snap["reason"]
    assert w.tracker.mismatched(["sim-a"]) == ["sim-a"]


def test_note_command_without_an_executor_and_snapshot_all():
    clock = VirtualClock()
    tracker = DeviceStateTracker(clock, Telemetry(None, clock))
    rows = full_state_vector(CALM)
    tracker.set_desired("sim-b", rows, label="module page", source="ops", meta={"page": "module"})
    command = Command("sim-b", rows, Purpose.ENGINE)
    tracker.note_submitted(command)
    assert tracker.status("sim-b") is DeviceStatus.UPDATING
    tracker.note_command(
        CommandResult(
            command,
            CommandStatus.APPLIED,
            attempts=[WriteResult("sim-b", Outcome.OK, http_status=200)],
            readback=ReadResult("sim-b", Outcome.OK, rows=rows, fetched_at=clock.now()),
        )
    )
    assert tracker.status("sim-b") is DeviceStatus.READY
    everything = tracker.snapshot_all()
    assert list(everything) == ["sim-b"] and everything["sim-b"]["meta"] == {"page": "module"}
    assert tracker.snapshot("sim-unknown")["status"] == "unknown"
    json.dumps(everything)


def test_readback_stale_after_accepted_writes_leave_it_unmoved_and_ends_when_it_moves():
    """D82 (Oct 21 live): Hubspace took write after write while the readback stayed put. Echo lag (≤ 3 stale
    readbacks, D71) isn't stale; four accepted writes over ≥ 10 s are; the first readback that moves ends it."""
    from conductor.state import STALE_AFTER_S, STALE_AFTER_WRITES

    w = World()
    rows = w.desire("sim-a", CALM)
    old = full_state_vector(GOODNIGHT)

    def accepted(readback_rows: list[StateRow]) -> None:
        cmd = Command("sim-a", rows, Purpose.ENGINE, label="re-assert: calm")
        w.tracker.note_submitted(cmd)
        w.tracker.note_command(
            CommandResult(
                cmd,
                CommandStatus.MISMATCH,
                attempts=[WriteResult("sim-a", Outcome.OK, http_status=200)],
                readback=w.read("sim-a", readback_rows),
            )
        )

    async def main():
        w.tracker.note_readback("sim-a", w.read("sim-a", old))
        for _ in range(STALE_AFTER_WRITES - 1):
            await asyncio.sleep(STALE_AFTER_S)
            accepted(old)
        lagging = w.tracker.readback_stale("sim-a")
        accepted(old)
        stale = w.tracker.readback_stale("sim-a")
        snap = w.tracker.snapshot("sim-a")
        # a readback in which a written row moved (not yet the look) ends it at once
        w.tracker.note_readback(
            "sim-a", w.read("sim-a", full_state_vector(Look(kind="white", cct=2700, brightness=30)))
        )
        moved = w.tracker.readback_stale("sim-a")
        return lagging, stale, snap, moved

    lagging, stale, snap, moved = run_virtual(main)
    assert not lagging and stale and not moved
    assert snap["readback_stale"] and snap["status"] == str(DeviceStatus.NEEDS_ATTENTION)
    assert snap["reason"].startswith("Readback stale:")
    alerts = [e for e in w.telemetry.recent(kind="alert") if e.get("readback_stale")]
    assert len(alerts) == 1


def test_a_changing_row_the_look_doesnt_write_cant_hide_a_stale_readback():
    from conductor.state import STALE_AFTER_S, STALE_AFTER_WRITES

    w = World()
    rows = w.desire("sim-a", CALM)
    old = full_state_vector(GOODNIGHT)

    async def main():
        for n in range(STALE_AFTER_WRITES + 1):
            cmd = Command("sim-a", rows, Purpose.ENGINE)
            w.tracker.note_submitted(cmd)
            readback = w.read("sim-a", [*old, StateRow("wifi-rssi", None, -40 - n)])
            w.tracker.note_command(
                CommandResult(cmd, CommandStatus.MISMATCH, [WriteResult("sim-a", Outcome.OK, 200)], readback)
            )
            await asyncio.sleep(STALE_AFTER_S)
        return w.tracker.readback_stale("sim-a")

    assert run_virtual(main)
