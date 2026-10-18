"""Input to reaction on the guest panel (the panel-latency pass, D69).

Proves: a guest's brightness or power change made inside the engine's guest cooldown (D60) is never dropped after
the panel was told 200 — it waits for the cooldown's end, then goes out, the latest change winning, one burst; the
panel says so truthfully (`state.next`, a "… in a moment" message, phase "cooldown") and its controls stay where the
guest put them; a lane that is only waiting stops at once on shutdown. The Afero driver refreshes a live account's
bearer in the background before a burst's own freshness check (07 §6) would have to, so no guest's first PUT waits on
Keycloak — never in sim mode, never for an account that needs a fresh sign-in, at most once per retry window when it
fails.
"""

from __future__ import annotations

import asyncio
import time

from fake_afero import make_world
from partial_canopy import showing
from show_started import wait_until

from conductor.clock import VirtualClock, run_virtual
from conductor.drivers.afero import PROACTIVE_REFRESH_LEFT_S, PROACTIVE_RETRY_S, TOKEN_CHECK_INTERVAL_S
from conductor.drivers.base import Outcome
from conductor.gate import RuntimeMode
from conductor.guest_panel import NEXT_BRIGHTNESS, NEXT_OFF, NEXT_ON, Lane

COOLDOWN_S = 2.0  # the real 8 s, shortened so the app tests run in real time


# ---- the lane on virtual time --------------------------------------------------------------------------------------
def _lane(hold_until: dict, runs: list):
    clock = VirtualClock()

    async def run(kind, value):
        runs.append((round(clock.monotonic(), 3), kind, value))

    def hold(_kind):
        return max(0.0, hold_until.get("t", 0.0) - clock.monotonic())

    return clock, Lane(clock, run, lambda: 1.0, hold=hold)


def test_a_held_change_waits_for_the_hold_then_goes_out_and_the_latest_wins():
    runs: list = []
    hold_until = {"t": 6.0}  # e.g. a scene was chosen 2 s ago: 6 s of the 8 s cooldown left

    async def main():
        _clock, lane = _lane(hold_until, runs)
        lane.put("brightness", 30)
        await asyncio.sleep(0.5)
        held_early = lane.held
        lane.put("brightness", 35)  # the guest keeps adjusting: still one job, the latest level
        await asyncio.sleep(1.0)
        lane.put("brightness", 40)
        await lane.wait_idle()
        return held_early, lane.held, lane.busy

    held_early, held_after, busy_after = run_virtual(main)
    assert held_early is True
    assert runs == [(6.0, "brightness", 40)]  # never dropped, never early, one job
    assert held_after is False and busy_after is False


def test_without_a_hold_the_lane_runs_at_once_and_a_plain_gap_is_not_held():
    runs: list = []

    async def main():
        _clock, lane = _lane({}, runs)
        lane.put("brightness", 30)
        await asyncio.sleep(0.1)
        lane.put("power", False)  # inside the 1 s gap: queued, but not "held"
        await asyncio.sleep(0.1)
        gap_held = lane.held
        await lane.wait_idle()
        return gap_held

    gap_held = run_virtual(main)
    assert runs == [(0.0, "brightness", 30), (1.0, "power", False)]
    assert gap_held is False


def test_a_hold_that_cant_be_read_never_blocks_the_guest():
    runs: list = []

    async def main():
        clock = VirtualClock()

        async def run(kind, value):
            runs.append((clock.monotonic(), kind, value))

        def broken(_kind):
            raise RuntimeError("engine state unavailable")

        lane = Lane(clock, run, lambda: 1.0, hold=broken)
        lane.put("brightness", 50)
        await lane.wait_idle()

    run_virtual(main)
    assert runs == [(0.0, "brightness", 50)]


def test_closing_a_lane_that_is_only_waiting_stops_it_at_once():
    runs: list = []

    async def main():
        clock, lane = _lane({"t": 8.0}, runs)
        lane.put("brightness", 30)
        await asyncio.sleep(0.1)
        started = clock.monotonic()
        await lane.close()
        return clock.monotonic() - started

    took = run_virtual(main)
    assert took < 0.01 and runs == []  # not the 8 s hold, and nothing sent


# ---- the real app: a scene, then the guest dims inside the cooldown ------------------------------------------------
def _services(c):
    return c.app.state.services


def _panel_state(c) -> dict:
    r = c.get("/api/kiosk/panel")
    assert r.status_code == 200, r.text
    return r.json()["state"]


def _brightness_log(c) -> list[dict]:
    return [e for e in c.get("/api/kiosk/debug").json()["commands"] if e["action"] == "brightness"]


def _other_scene(engine) -> str:
    current = engine.current_plan().mode_id
    return next(s for s in ("garden-gathering", "goodnight-glow") if s != current)


def test_dimming_right_after_a_scene_lands_when_the_cooldown_ends_not_never(app_client_show):
    """Before D69 the engine refused the guest's level inside the shared cooldown after the panel had answered
    200: the slider snapped back and the lights never changed. Now the last level goes out once, at the end."""
    c = app_client_show
    engine = showing(c)
    _services(c).settings.kiosk.cooldown_s = COOLDOWN_S
    r = c.post("/api/kiosk/scene", json={"scene": _other_scene(engine)})
    assert r.status_code == 200, r.text
    chosen = time.monotonic()
    for level in (30, 35, 40):  # a guest adjusting the slider a few times inside the cooldown
        r = c.post("/api/kiosk/brightness", json={"level": level})
        assert r.status_code == 200, r.text
    state = r.json()["state"]
    assert state["next"] == {"brightness": 40}
    wait_until("the scene's own change to finish", lambda: _panel_state(c)["phase"] != "changing", 10)
    state = _panel_state(c)
    if (
        time.monotonic() - chosen < COOLDOWN_S - 0.3
    ):  # still inside the cooldown: the panel says what is coming
        assert state["phase"] == "cooldown" and state["message"] == NEXT_BRIGHTNESS
        assert state["next"] == {"brightness": 40}
        assert engine.state().guest_brightness is None  # nothing sent early
    wait_until("the guest's level to reach the engine", lambda: engine.state().guest_brightness == 40, 10)
    landed = time.monotonic() - chosen
    assert COOLDOWN_S - 0.3 <= landed < COOLDOWN_S + 1.5, landed
    wait_until("the panel to settle", lambda: _panel_state(c)["phase"] in ("ready", "cooldown"), 10)
    state = _panel_state(c)
    assert state["next"] is None and state["brightness"] == 40
    log = _brightness_log(c)
    assert not [e for e in log if e["outcome"] == "refused"], log
    assert [e["params"]["level"] for e in log if e["outcome"] == "accepted by the engine"] == [40]


def test_switching_off_inside_the_cooldown_says_so_and_lands_at_its_end(app_client_show):
    c = app_client_show
    engine = showing(c)
    _services(c).settings.kiosk.cooldown_s = COOLDOWN_S
    assert c.post("/api/kiosk/scene", json={"scene": _other_scene(engine)}).status_code == 200
    r = c.post("/api/kiosk/power", json={"on": False})
    assert r.status_code == 200 and r.json()["state"]["next"] == {"power": False}
    wait_until("the scene's own change to finish", lambda: _panel_state(c)["phase"] != "changing", 10)
    state = _panel_state(c)
    if state["next"] is not None:
        assert state["phase"] == "cooldown" and state["message"] == NEXT_OFF
    wait_until("the canopy to go off", lambda: engine.state().guest_power is False, 10)
    wait_until("the panel to show it off", lambda: _panel_state(c)["power"] is False, 10)
    assert _panel_state(c)["next"] is None


def test_the_held_message_follows_the_last_change_the_guest_made():
    """`_next_message` reads the lane in arrival order: the last queued change decides what the panel promises."""
    from types import SimpleNamespace

    from conductor.guest_panel import EngineTarget

    target = EngineTarget.__new__(EngineTarget)
    for pending, message in (
        ({"brightness": 40}, NEXT_BRIGHTNESS),
        ({"power": False}, NEXT_OFF),
        ({"power": True}, NEXT_ON),
        ({"brightness": 40, "power": False}, NEXT_OFF),  # dimmed, then switched off: it goes off
        ({"power": False, "brightness": 40}, NEXT_BRIGHTNESS),  # off, then dimmed: dimming turns it on (D60)
    ):
        target.lane = SimpleNamespace(pending=dict(pending))
        assert target._next_message() == message, pending


# ---- the Afero driver: the bearer is refreshed ahead, off the burst's path -----------------------------------------
def _refreshes(w) -> int:
    return w.fake.count("POST", "/token")


def test_the_token_keeper_refreshes_ahead_so_a_bursts_check_sends_nothing(tmp_path):
    async def main():
        w = await make_world(tmp_path, connect=False)
        await w.driver.start()  # connects in the background and starts the keeper
        await asyncio.sleep(1)
        assert w.driver.account_status("bench").connected
        before = _refreshes(w)
        w.wall[0] += 118 - PROACTIVE_REFRESH_LEFT_S + 5  # aioafero's clock: under 30 s left, still over 15 s
        await asyncio.sleep(TOKEN_CHECK_INTERVAL_S + 0.1)  # one keeper tick
        refreshed = _refreshes(w) - before
        generation = w.store.status("bench").generation
        events = [e for e in w.telemetry.recent(kind="token") if e.get("action") == "refresh_ahead"]
        # a guest's burst now: the bearer has ~116 s left, so its check is in memory only
        requests = len(w.fake.requests)
        fresh = await w.driver.ensure_fresh_token("bench")
        sent_by_check = len(w.fake.requests) - requests
        await w.driver.close()
        return refreshed, generation, events, fresh, sent_by_check

    refreshed, generation, events, fresh, sent_by_check = run_virtual(main)
    assert refreshed == 1 and generation == 3  # connect rotated once (2), the keeper once more (3), saved
    assert [e["outcome"] for e in events] == [str(Outcome.OK)]
    assert fresh is Outcome.OK and sent_by_check == 0


def test_a_fresh_bearer_is_left_alone(tmp_path):
    async def main():
        w = await make_world(tmp_path, connect=False)
        await w.driver.start()
        await asyncio.sleep(1)
        before = _refreshes(w)
        await asyncio.sleep(3 * TOKEN_CHECK_INTERVAL_S)  # ~116 s left the whole time
        await w.driver.close()
        return _refreshes(w) - before

    assert run_virtual(main) == 0


def test_no_background_refresh_in_sim_mode_or_for_an_account_needing_a_sign_in(tmp_path):
    async def sim():
        w = await make_world(tmp_path / "sim", mode=RuntimeMode.SIM)  # bench calls open bridges on demand
        await w.driver.start()
        before = _refreshes(w)
        w.wall[0] += 100
        await asyncio.sleep(TOKEN_CHECK_INTERVAL_S + 0.1)
        await w.driver.close()
        return _refreshes(w) - before

    async def reauth():
        w = await make_world(tmp_path / "reauth", connect=False)
        await w.driver.start()
        await asyncio.sleep(1)
        w.driver._links["bench"].needs_reauth = True  # a dead sign-in: never loop logins (07 §3)
        before = _refreshes(w)
        w.wall[0] += 100
        await asyncio.sleep(TOKEN_CHECK_INTERVAL_S + 0.1)
        await w.driver.close()
        return _refreshes(w) - before

    assert run_virtual(sim) == 0
    assert run_virtual(reauth) == 0


def test_a_failed_background_refresh_waits_its_retry_window(tmp_path):
    async def main():
        w = await make_world(tmp_path, connect=False)
        await w.driver.start()
        await asyncio.sleep(1)
        w.fake.valid_refresh.clear()  # Keycloak refuses the stored refresh token from now on
        tries = []

        async def failing(account, min_left_s=15.0, *, purpose=None):
            tries.append(w.clock.monotonic())
            return Outcome.CONNECTION

        w.driver.ensure_fresh_token = failing
        w.wall[0] += 100
        await asyncio.sleep(PROACTIVE_RETRY_S + 2 * TOKEN_CHECK_INTERVAL_S)
        await w.driver.close()
        return tries

    tries = run_virtual(main)
    assert len(tries) == 2 and tries[1] - tries[0] >= PROACTIVE_RETRY_S


# ---- the executor: a newer command never waits on an older command's readback ---------------------------------------
def test_a_newer_command_goes_out_without_waiting_for_the_older_ones_readback_on_the_wire():
    """A guest's change landing while the previous command's verification GET is slow (here 3 s) goes out at the
    rate budget, not after that GET; the GET itself is left to finish (never cancelled) and its answer discarded."""
    from fake_driver import FakeDriver, Step

    from conductor.config import ExecutorSettings
    from conductor.executor import CommandExecutor
    from conductor.executor_api import Command, CommandStatus
    from conductor.gate import Purpose
    from conductor.locks import DeviceLockManager
    from conductor.rows import brightness, color_mode, power
    from conductor.telemetry import Telemetry

    clock = VirtualClock()
    driver = FakeDriver(clock)
    executor = CommandExecutor(
        driver,
        locks=DeviceLockManager(clock=clock),
        telemetry=Telemetry(None, clock),
        clock=clock,
        policy=ExecutorSettings(readback_delay_s=1.0),
    )

    def cmd(pct):
        return Command(
            device_id="sim-a",
            rows=[power(True), color_mode("white"), brightness(pct)],
            purpose=Purpose.ENGINE,
        )

    async def main():
        driver.script_read("sim-a", Step(latency_s=3.0))  # the first verification GET hangs for 3 s
        first = asyncio.ensure_future(executor.submit(cmd(40)))
        await asyncio.sleep(1.2 + 0.2)  # PUT acked at 0.2, GET sent at 1.2 and on the wire
        second = await executor.submit(cmd(70))
        results = (await first, second)
        await asyncio.sleep(3)  # let the abandoned GET finish
        await executor.close()
        return results

    first, second = run_virtual(main)
    assert first.status is CommandStatus.SUPERSEDED and second.status is CommandStatus.APPLIED
    sends = [round(w.sent_mono, 3) for w in driver.writes]
    assert sends == [
        0.0,
        1.4,
    ]  # the second PUT at the tap (the 1 s budget had passed), not at 4.2 after the GET
    assert len(driver.reads) == 2  # the slow GET (finished on its own) and the second command's own


# ---- redaction: the same result, at a cost that doesn't grow with the show's age ----------------------------------
def _naive(text: str, secrets_: set[str]) -> str:
    """The pre-D69 secret pass: every secret, longest first."""
    from conductor.redact import MASK

    for s in sorted(secrets_, key=len, reverse=True):
        if s in text:
            text = text.replace(s, MASK)
    return text


def test_redaction_masks_exactly_what_checking_every_secret_did(monkeypatch):
    import random

    from conductor import redact

    rng = random.Random(69)
    alphabet = "abcAB01*-_."
    monkeypatch.setattr(redact, "_secrets", set())
    monkeypatch.setattr(redact, "_stale", True)
    monkeypatch.setattr(redact, "_PATTERNS", [])  # only the secret pass is compared here
    monkeypatch.setattr(redact, "_ENROLL", __import__("re").compile(r"(?!x)x"))
    registered: set[str] = set()
    for _ in range(300):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randint(6, 14)))
        redact.add_secret(s)
        registered.add(s)
    pool = sorted(registered)
    for s in pool[:20]:  # secrets inside secrets, and sharing tails
        for extra in (s + "Z", "Q" + s, s[:-1] + "!" + s[-8:]):
            redact.add_secret(extra)
            registered.add(extra)
    pool = sorted(registered)
    for _ in range(2000):
        parts = []
        for _ in range(rng.randint(1, 6)):
            parts.append(
                rng.choice(pool) if rng.random() < 0.5 else "".join(rng.choice(alphabet) for _ in range(5))
            )
        text = rng.choice(["", " ", "*"]).join(parts)
        assert redact.redact(text) == _naive(text, registered), text
    for _ in range(50):  # long texts (a traceback): checking each secret directly is the cheaper path there
        parts = [rng.choice(pool) if rng.random() < 0.3 else "x" * rng.randint(1, 40) for _ in range(400)]
        text = rng.choice(["", " ", "*"]).join(parts)
        assert len(text) > 4 * len(registered)
        assert redact.redact(text) == _naive(text, registered)


def test_redaction_cost_does_not_grow_with_twelve_days_of_rotated_tokens(monkeypatch):
    import secrets as pysecrets

    from conductor import redact

    line = "PUT https://api2.afero.net/v1/accounts/{aid}/metadevices/abc/state -> 200 in 0.31 s (bench, canopy-r0c0)"
    monkeypatch.setattr(redact, "_secrets", set())
    monkeypatch.setattr(redact, "_stale", True)

    def per_call(n: int) -> float:
        while len(redact._secrets) < n:
            redact.add_secret(pysecrets.token_urlsafe(48))
        redact.redact(line)  # build the index outside the timing
        started = time.perf_counter()
        for _ in range(300):
            assert redact.redact(line) == line
        return (time.perf_counter() - started) / 300

    small, large = per_call(50), per_call(8800)  # ~1.5 hours vs ~12 days of rotation on one account
    assert large < max(4 * small, 0.0002), (small, large)


# ---- delivery: a deploy reaches the iPad's next reload, and never as a mix of versions ----------------------------
def test_the_panel_and_its_modules_are_revalidated_on_every_load(app_client):
    for path in (
        "/kiosk/",
        "/kiosk/kiosk.js",
        "/kiosk/panel-model.mjs",
        "/kiosk/canopy-preview.mjs",
        "/static/app.js",
    ):
        r = app_client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path
        etag = r.headers["etag"]
        again = app_client.get(path, headers={"if-none-match": etag})
        assert again.status_code == 304 and again.headers["cache-control"] == "no-cache", path


def test_the_panel_loads_one_version_of_all_its_modules():
    import re
    from pathlib import Path

    kiosk = Path(__file__).resolve().parent.parent / "web" / "kiosk"
    html = (kiosk / "index.html").read_text()
    js = (kiosk / "kiosk.js").read_text()
    [entry] = re.findall(r'src="/kiosk/kiosk\.js\?v=([\w.-]+)"', html)
    imports = re.findall(r'from "(/[^"]+)"', js)
    assert imports and all(i.endswith(f"?v={entry}") for i in imports), (entry, imports)
