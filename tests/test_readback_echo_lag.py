"""Cloud echo lag vs a silent drop (D70): a first verification readback that still shows the previous look is not a
drop. Live on Oct 17–18 it was the usual case on a scene change; the engine re-sent at once and wasted a write per
module. The change's first early re-send now waits for the command's second readback; a real drop still heals
within 10 s (the drop tests in test_reconciler.py / test_engine_program.py)."""

from __future__ import annotations

from fake_compiler import MODULES, make_world, settings_with

from conductor.clock import run_virtual
from conductor.sim.settings import SimSettings

QUIET = settings_with(kiosk={"idle_return_s": 100_000})
# Readback shows what the device reports, and the device reports it 1.3–1.6 s after the write lands: past the first
# verification GET (readback_delay_s 1.0), before the second (+1.0 s) — what the live kits looked like (acks and GETs
# held to 150–200 ms, as live, so the first GET can't drift past the apply).
LAGGING = SimSettings.dev().with_(
    readback_semantics="device",
    ack_ms_min=150,
    ack_ms_max=200,
    device_apply_extra_s_min=1.3,
    device_apply_extra_s_max=1.6,
    throttle_429_rate=0.0,
    silent_drop_rate=0.0,
)


def _change_under_lag():
    async def main():
        w = make_world(settings=QUIET, sim_settings=LAGGING)
        await w.engine.start(apply_initial=False)
        await w.engine.request_mode("lowcountry-calm", source="ops")  # brightness 60
        await w.until(w.all_correct, timeout=15)
        await w.engine.wait_idle()
        writes_before = w.sim.stats()["writes_per_device"].copy()
        early_before = w.engine.stats["early_reasserts"]
        n_events = len(w.events("readback"))
        t0 = w.clock.monotonic()
        await w.engine.request_mode("holiday", source="kiosk")  # brightness 70 and a different look
        healed = await w.until(w.all_correct, timeout=15)
        await w.engine.wait_idle()
        writes = {d: n - writes_before.get(d, 0) for d, n in w.sim.stats()["writes_per_device"].items()}
        readbacks = [e for e in w.events("readback")[n_events:] if e.get("label") == "mode: holiday"]
        commands = [e for e in w.events("command") if e.get("label") == "mode: holiday"]
        out = {
            "healed": healed,
            "writes": writes,
            "early": w.engine.stats["early_reasserts"] - early_before,
            "readbacks": readbacks,
            "commands": commands,
            "t0": t0,
        }
        await w.close()
        return out

    return run_virtual(main)


def test_echo_lag_on_the_first_readback_costs_no_extra_write():
    out = _change_under_lag()
    assert out["healed"] is not None and out["healed"] <= 4.0
    assert out["early"] == 0
    assert out["writes"] == {f"sim-{m}": 1 for m in MODULES}  # one PUT per module, no re-send
    assert {c["status"] for c in out["commands"]} == {"applied"}
    first = [r for r in out["readbacks"] if r["n"] == 1]
    second = [r for r in out["readbacks"] if r["n"] == 2]
    assert len(first) == len(MODULES) and all(r["mismatched"] for r in first)  # every first GET was stale ...
    assert len(second) == len(MODULES) and not any(
        r["mismatched"] for r in second
    )  # ... and the second matched


def test_readback_telemetry_names_the_sent_and_read_values():
    out = _change_under_lag()
    stale = next(r for r in out["readbacks"] if r["n"] == 1)
    brightness = next(d for d in stale["differs"] if d["class"] == "brightness")
    assert brightness["sent"] == 70 and brightness["read"] == 60  # the old value: echo lag, not a clamp
    assert "brightness" in stale["mismatched"]
