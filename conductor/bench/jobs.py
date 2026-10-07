"""The bench jobs: Gates A–C, the index-order read, the hue ramp, latency trials, whole-strand looks, the four jobs
that settle the remaining UNVERIFIED device behaviours (stale-clock PUT, active-slot rewrite, rate ramp, head count at
boot) and restore.

Sources: `docs/context/04` "Bench gates" and `research/permanent-lights/_synthesis.md` "Prototype 01 bench plan"
steps 3–9. Rules every job follows:
- Writes go through `ctx.write` only (the bench executor, with this run's lease), one PUT per look, and at least
  the per-device budget apart (`ctx.budget_s`; whole-strand looks ≥ 2 s apart). The executor enforces the budget
  too — the sleeps here keep the looks visible long enough to judge.
- Frame writes copy the header and the frame length from the device's **own readback** of that slot.
- Rows the device doesn't declare are never sent (the device's function list is the truth, 06 #1).
- A job proposes DeviceFlags only for what it measured; each flag is applied later, and only after the person at
  the bench answers its confirming question "yes" (FLAG_QUESTIONS), and never for a simulated run.
"""

from __future__ import annotations

import colorsys
import copy
import math
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Protocol

from conductor import rows as R
from conductor.bench_api import BenchContext, JobResult, JobSpec, Verdict
from conductor.capabilities import Capabilities, detect
from conductor.drivers.base import DeviceInfo, Outcome, ReadResult, StateRow
from conductor.executor_api import CommandResult, CommandStatus
from conductor.telemetry import percentile

HD_ORANGE = (0xF9, 0x63, 0x02)
LOOK_SPACING_S = 2.0  # whole-strand looks: long enough to see each one
UNPLUGGED_SETTLE_S = 3.0  # wait before the GET in the unplugged test
CALM_CCT = 2700
CALM_BRIGHTNESS = 50
PER_HEAD_LENGTHS = (30, 60)  # Gate A: a 30-entry (or fixed 60-entry) frame keeps the per-head path alive
BEFORE_HOLD_S = 4.0  # Gate C: how long the BEFORE look holds before the replay, so it can be seen and filmed
MAX_TRIALS = 100
MAX_SPACING_S = 30.0  # latency trials: well inside the lock's 10-min idle timeout, and a run stays short
STALE_LAG_S = 600.0  # stale_clock_put: lastUpdateTime = now − 10 min (07 §10)
STALE_SETTLE_S = 3.0  # stale_clock_put: wait before the readback (and before the control write)
RAMP_SPACINGS_S = (5.0, 2.0, 1.0)  # rate_ramp: the spacings, slowest first (04 bench gate #11)
RAMP_MIN_SPACING_S = 1.0  # rate_ramp: never faster than 1 write/s to the device, whatever the budget says
MAX_RAMP_WRITES_PER_STEP = 50

# Question templates: `{slot}`, `{source}`, `{target}` are filled from the run's params ("custom-2" → "slot 2"),
# so the question names the slot the job actually used. FLAG_QUESTIONS is keyed by these templates; use
# `flag_questions(job, params)` / `question(job, params)` for the text a run asks.
Q_GATE_B = "Did the strand switch to {slot} within about 2 s?"
Q_GATE_C = "Did the lights change from the BEFORE look to exactly {source}'s pattern?"
Q_UNPLUGGED = "Was the kit switched off at the wall (lights dark) the whole time this ran?"
Q_HUE_RAMP = (
    "Did every puck show a different colour, running through the rainbow along the strand? Take a photo."
)
Q_INDEX = "Is puck 1 (the red one you painted first) the puck nearest the controller?"
Q_LATENCY = "Did you film every trial with the clock page next to the kit?"
Q_STALE_STORED = "Did the strand switch to {slot} after the stale-stamped write?"
Q_STALE_DROPPED = "Did the lights stay as they were after the first (stale) write, and switch to {slot} only after the second?"
Q_ACTIVE_REWRITE = "Did the lights change to the new pattern while {slot} stayed selected (no reselect)?"
Q_HEAD_BOOT = "With strings 2 and 3 unplugged, did only string 1's pucks light when the kit booted?"

# Which answer must be "yes" before a proposed flag may be applied.
FLAG_QUESTIONS: dict[str, dict[str, str]] = {
    "gate_b": {"t2_select": Q_GATE_B},
    "gate_c": {"t1_frames": Q_GATE_C},
    "gate_c_safe": {"t1_frames": Q_GATE_C},
    "gate_b_unplugged": {"readback_device_level": Q_UNPLUGGED},
}

# Bounds for numeric job options (inclusive), checked by coerce_params before a run starts.
PARAM_BOUNDS: dict[str, dict[str, tuple[float, float]]] = {
    "gate_c_safe": {"brightness": (1, 100), "duration_s": (4.0, 120.0), "before_duration_s": (4.0, 60.0)},
    "effect_probe": {"code": (1, 255), "speed": (0, 100), "duration_s": (2.0, 20.0)},
    "address_probe": {"start": (0, 255), "count": (1, 256), "brightness": (1, 100)},
    "address_probe_safe": {"start": (0, 255), "count": (1, 256), "brightness": (1, 100), "duration_s": (4.0, 120.0)},
    "latency_trials": {"n": (1, MAX_TRIALS), "spacing_s": (0.0, MAX_SPACING_S)},
    "rate_ramp": {"writes_per_step": (1, MAX_RAMP_WRITES_PER_STEP)},
    "head_count_boot": {"full_heads": (1, 90)},
}

# Jobs whose `confirmed` option is a checkbox the person ticks once the kit is set up (the page shows this label).
CONFIRM_LABELS: dict[str, str] = {
    "gate_b_unplugged": "I've switched the kit off at the wall",
    "head_count_boot": "I've booted the kit with strings 2 and 3 unplugged",
}


def _slot_words(value: Any) -> Any:
    return f"slot {value.split('-', 1)[1]}" if isinstance(value, str) and value in R.SLOTS else value


class _Keep(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fill(template: str, params: Mapping[str, Any]) -> str:
    """A question template with the run's params filled in (slots as "slot N")."""
    return template.format_map(_Keep({k: _slot_words(v) for k, v in params.items()}))


def flag_questions(job: str, params: Mapping[str, Any]) -> dict[str, str]:
    """flag → the confirming question this run asks (FLAG_QUESTIONS with the run's params filled in)."""
    return {flag: fill(q, params) for flag, q in FLAG_QUESTIONS.get(job, {}).items()}


def question(job: str, params: Mapping[str, Any]) -> str:
    """The confirming question a flag-proposing job asks for these params."""
    return next(iter(flag_questions(job, params).values()))


class JobContext(BenchContext, Protocol):
    """BenchContext plus what the runner's context also offers (`conductor/bench/runner.py`)."""

    budget_s: float  # the per-device write budget (executor min_write_interval_s)
    t1_allowed: bool  # per-head frames may be written (simulator, or Gate C proven on this kit)
    expected_heads: int | None  # the registry's head count for this device (None = not set)
    artifacts: dict[str, Any]  # big outputs saved with the result (Gate A's scrubbed profile)

    async def write(
        self,
        rows: list,
        *,
        label: str,
        verify: bool = True,
        expect: list | None = None,
        stamp_offset_s: float = 0.0,
        max_attempts: int | None = None,
    ) -> CommandResult:
        """One PUT through the bench executor with this run's lease. `stamp_offset_s` (≤ 0) stamps lastUpdateTime
        in the past (stale_clock_put only); `max_attempts` caps retries (rate_ramp: 1)."""

    async def sim_power(self, on: bool) -> bool:
        """Switch the simulated kit's power (the unplugged test on the simulator). False on a real kit."""


JobFn = Callable[[JobContext, dict[str, Any]], Awaitable[JobResult]]


@dataclass(frozen=True)
class Job:
    """A BenchJob: a spec plus the coroutine that runs it."""

    spec: JobSpec
    fn: JobFn

    async def run(self, ctx: JobContext, params: dict[str, Any]) -> JobResult:
        return await self.fn(ctx, params)


# ---- helpers ------------------------------------------------------------------------------------------------------
class JobError(RuntimeError):
    """The job can't go on; the message is plain English for the person at the bench."""


def _caps(info: DeviceInfo) -> Capabilities:
    return detect(info.functions, info.states)


def _declared(info: DeviceInfo) -> set[tuple[str, str | None]]:
    return {(f.get("functionClass"), f.get("functionInstance")) for f in info.functions}


def _only_declared(ctx: JobContext, info: DeviceInfo, rows: Iterable[StateRow]) -> list[StateRow]:
    declared = _declared(info)
    keep, dropped = [], []
    for row in rows:
        (keep if row.key in declared else dropped).append(row)
    if dropped:
        names = ", ".join(sorted({r.function_class for r in dropped}))
        ctx.step(f"Left out rows this device doesn't declare: {names}")
    return keep


async def _read_ok(ctx: JobContext, why: str) -> ReadResult:
    rr = await ctx.read()
    if not rr.ok:
        raise JobError(f"Couldn't read the device's state {why}: {rr.error or rr.outcome}")
    return rr


def _ms(a: float | None, b: float | None) -> float | None:
    return round((b - a) * 1000) if a is not None and b is not None else None


def write_facts(res: CommandResult) -> dict[str, Any]:
    """The measured facts of one bench write: status, HTTP status, send→ack and send→readback-match."""
    t = res.timings
    last = res.attempts[-1] if res.attempts else None
    return {
        "status": str(res.status),
        "http_status": last.http_status if last else None,
        "attempts": len(res.attempts),
        "sent_at": t.sent_at,
        "ack_ms": _ms(t.sent_at, t.acked_at),
        "verified_ms": _ms(t.sent_at, t.verified_at),
        "mismatches": [m.function_class for m in res.mismatches],
        "error": res.error,
    }


def _round(value: float | None) -> int | None:
    return None if value is None else round(value)


def _write_problem(res: CommandResult, what: str) -> JobResult | None:
    """A FAIL/ERROR result for a write that didn't apply, else None."""
    facts = write_facts(res)
    if res.status is CommandStatus.APPLIED:
        return None
    if res.status in (CommandStatus.DENIED, CommandStatus.LOCKED, CommandStatus.INVALID):
        return JobResult(Verdict.ERROR, f"{what} wasn't sent: {res.error}", {"write": facts})
    if res.status is CommandStatus.MISMATCH:
        return JobResult(
            Verdict.FAIL,
            f"{what} was accepted but the readback doesn't show it: {res.error}",
            {"write": facts},
        )
    return JobResult(Verdict.FAIL, f"{what} failed: {res.error}", {"write": facts})


def _slot_entries(rr: ReadResult, slot: str) -> tuple[Any, list[R.FrameEntry]]:
    value = rr.get(R.FRAME_CLASS, slot)
    entries = R.frame_entries(value)
    if entries is None:
        raise JobError(
            f"The device's readback has no pattern object for {slot}; check the slot exists (run Gate A)."
        )
    return value, entries


def _check_slot(caps: Capabilities, *slots: str) -> None:
    for slot in slots:
        if slot not in R.SLOTS:
            raise JobError(f"{slot!r} isn't a slot; use one of {', '.join(R.SLOTS)}.")
        if caps.slots and slot not in caps.slots:
            raise JobError(f"This device has no {slot} (it lists {', '.join(caps.slots)}).")
    if not caps.individual:
        raise JobError(
            "This device has no per-head ('individual') mode with slots, so there is nothing to select."
        )


async def _space(ctx: JobContext, since: float | None, gap_s: float) -> None:
    """Sleep so the next write goes out at least `gap_s` after `since` (a wall-clock send time)."""
    if since is None:
        return
    remaining = max(gap_s, ctx.budget_s) - (ctx.now() - since)
    if remaining > 0:
        await ctx.sleep(remaining)


def colour_name(e: R.FrameEntry) -> str:
    """What a painted puck is, roughly: red / green / blue / white / off / other."""
    if e.colorBrightness == 0:
        return "white" if e.whiteBrightness > 0 else "off"
    hi = max(e.r, e.g, e.b)
    if hi < 40:
        return "white" if e.whiteBrightness > 0 else "off"
    if min(e.r, e.g, e.b) >= 0.6 * hi:
        return "white"
    for name, main, others in (
        ("red", e.r, (e.g, e.b)),
        ("green", e.g, (e.r, e.b)),
        ("blue", e.b, (e.r, e.g)),
    ):
        if main == hi and all(o < 0.5 * main for o in others):
            return name
    return "other"


def hue_ramp_entries(length: int, step_deg: float = 12.0) -> list[R.FrameEntry]:
    """Hue = step·i at full saturation and brightness, one entry per head."""
    out = []
    for i in range(length):
        r, g, b = colorsys.hsv_to_rgb(((step_deg * i) % 360) / 360.0, 1.0, 1.0)
        out.append(R.FrameEntry(round(r * 255), round(g * 255), round(b * 255), 0, 100, 0))
    return out


def _entry_json(e: R.FrameEntry) -> dict:
    return e.to_json()


def slot_header(value: Any) -> dict[str, Any] | None:
    """A slot object's header and effect fields (the effect-code table), or None when it isn't a slot object."""
    if not isinstance(value, Mapping):
        return None
    inner = value.get(R.FRAME_CLASS, value)
    if not isinstance(inner, Mapping):
        return None
    out = {k: inner.get(k) for k in (*R.HEADER_COPY_FIELDS, *R.EFFECT_FIELDS)}
    out["frameBuffer.flags"] = (inner.get("frameBuffer") or {}).get("flags")
    return out


def contrast_entries(avoid: list[R.FrameEntry]) -> list[R.FrameEntry]:
    """A frame of the same length that visibly differs from `avoid`: the hue ramp, reversed if that's `avoid`."""
    n = len(avoid)
    ramp = hue_ramp_entries(n)
    for candidate in (ramp, ramp[::-1], [R.FrameEntry(0, 0, 0, 0, 0, 0)] * n):
        if candidate != avoid:
            return candidate
    return ramp


def contrast_look(caps: Capabilities) -> tuple[str, R.Look] | None:
    """A whole-strand look that isn't a painted pattern: calm white, else HD orange. (name, look) or None."""
    modes = set(caps.color_modes)
    if caps.cct_range and "white" in modes:
        cct = max(caps.cct_range[0], min(CALM_CCT, caps.cct_range[1]))
        return f"white {cct} K {CALM_BRIGHTNESS}%", R.Look("white", CALM_BRIGHTNESS, cct=cct)
    if caps.rgb and "color" in modes:
        return "HD orange", R.Look("rgb", 100, rgb=HD_ORANGE)
    return None


def _showing(rr: ReadResult) -> str | None:
    """The slot the kit is showing, or None when it isn't in per-head (individual) mode."""
    return rr.get("color-individual", "custom") if rr.get("color-mode") == "individual" else None


def _hashes(rr: ReadResult) -> dict[str, str | None]:
    return {s: R.slot_hash(rr.get(R.FRAME_CLASS, s)) for s in R.SLOTS}


# ---- Gate A ------------------------------------------------------------------------------------------------------
async def gate_a(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    ctx.step("Reading the device's profile (functions and state). Nothing is written.")
    info = await ctx.profile()
    caps = _caps(info)
    brightness_instances = sum(1 for f in info.functions if f.get("functionClass") == "brightness")
    lengths = dict(sorted(caps.framebuffer_lengths.items()))
    obs: dict[str, Any] = {
        "model": info.model,
        "device_class": info.device_class,
        "segment_hint": caps.segment_hint,
        "framebuffer_lengths": lengths,
        "framebuffer_length": caps.framebuffer_length,
        "slot_count": len(caps.slots),
        "slots": caps.slots,
        "color_modes": caps.color_modes,
        "individual_mode": "individual" in caps.color_modes,
        "no_white_hint": caps.no_white_hint,
        "cct_range": list(caps.cct_range) if caps.cct_range else None,
        "cct_step": caps.cct_step,
        "rgb": caps.rgb,
        "presets": caps.presets,
        "programs": caps.programs,
        "speed_range": list(caps.speed_range) if caps.speed_range else None,
        "restore_values": caps.restore_values,
        "party_mode": caps.party_mode,
        "brightness_instances": brightness_instances,
        "function_count": len(info.functions),
    }
    ctx.step(
        "Profile read",
        model=info.model,
        slots=caps.slots,
        framebuffer_lengths=lengths,
        color_modes=caps.color_modes,
    )
    warnings = []
    if brightness_instances > 1:
        warnings.append(
            f"{brightness_instances} brightness functions: aioafero's light_callback would split this device "
            "and drop the parent from bridge.lights (07 §4) — tell the build session."
        )
    if len(set(lengths.values())) > 1:
        warnings.append(f"The slots report different frame lengths {lengths}.")
    if caps.segment_hint and caps.framebuffer_length and caps.segment_hint != caps.framebuffer_length:
        warnings.append(
            f"The segment hint says {caps.segment_hint} but the frames hold {caps.framebuffer_length} entries."
        )
    obs["warnings"] = warnings
    if info.raw is not None:
        ctx.artifacts["profile_raw"] = copy.deepcopy(info.raw)
    if not caps.individual:
        return JobResult(
            Verdict.FAIL,
            "Per-head looks can't work: T3 only (no 'individual' colour mode with slots on this device).",
            obs,
        )
    n = caps.framebuffer_length
    if len(caps.slots) == 3 and n in PER_HEAD_LENGTHS and len(set(lengths.values())) == 1:
        return JobResult(
            Verdict.PASS,
            f"Per-head path is alive: 3 slots with a {n}-entry frame. Download the profile for the simulator.",
            obs,
        )
    return JobResult(
        Verdict.NEEDS_EYES,
        f"Per-head mode exists, but the shape is unexpected ({len(caps.slots)} slots, frame length {n}). "
        "Send the anonymized JSON and the profile to the build session.",
        obs,
    )


# ---- index order (read-only) -------------------------------------------------------------------------------------
PATTERN = ["red", "green", "blue", "white"]


async def index_order(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    slot = str(params["slot"])
    if slot not in R.SLOTS:
        raise JobError(f"{slot!r} isn't a slot; use one of {', '.join(R.SLOTS)}.")
    ctx.step(f"Reading {slot}. Nothing is written.")
    rr = await _read_ok(ctx, f"for {slot}")
    value, entries = _slot_entries(rr, slot)
    first = entries[:4]
    last = entries[-4:]
    first_names = [colour_name(e) for e in first]
    last_names = [colour_name(e) for e in last]
    header = slot_header(value)
    # Every slot's header, so each copy of this result adds a row to the effect-code table (04 Bench gates #2).
    headers = {s: h for s in R.SLOTS if (h := slot_header(rr.get(R.FRAME_CLASS, s))) is not None}
    ctx.step(f"{slot} header", **(header or {}))
    obs: dict[str, Any] = {
        "slot": slot,
        "color_mode": rr.get("color-mode"),
        "selected_slot": rr.get("color-individual", "custom"),
        "header": header,
        "slot_headers": headers,
        "length": len(entries),
        "first_4": [_entry_json(e) for e in first],
        "last_4": [_entry_json(e) for e in last],
        "first_4_colours": first_names,
        "last_4_colours": last_names,
    }
    if first_names == PATTERN:
        obs["order"] = "forward"
        return JobResult(
            Verdict.INFO,
            "Index 0 is the puck you painted first (red, green, blue, white sit at the start of the frame).",
            obs,
            [Q_INDEX],
        )
    if list(reversed(last_names)) == PATTERN:
        obs["order"] = "reversed"
        return JobResult(
            Verdict.INFO,
            "The frame is reversed: the puck you painted first is the LAST entry.",
            obs,
            [Q_INDEX],
        )
    obs["order"] = "not found"
    return JobResult(
        Verdict.NEEDS_EYES,
        "The red/green/blue/white pattern isn't at either end of the frame. In the Hubspace app, paint puck 1 "
        "red, 2 green, 3 blue, 4 white into slot 1, save, and run this again.",
        obs,
    )


# ---- Gate B ------------------------------------------------------------------------------------------------------
async def gate_b(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    slot = str(params["slot"])
    info = await ctx.profile()
    _check_slot(_caps(info), slot)
    before = await _read_ok(ctx, "before the select")
    hashes = _hashes(before)
    showing = _showing(before)
    obs: dict[str, Any] = {
        "slot": slot,
        "before": {
            "power": before.get("power"),
            "color_mode": before.get("color-mode"),
            "selected_slot": before.get("color-individual", "custom"),
            "slot_hash": hashes.get(slot),
            "showing_hash": hashes.get(showing) if showing else None,
        },
    }
    ctx.step("Recorded the current state", **obs["before"])
    # The question "did it switch?" can only be answered truthfully if the switch is visible.
    if showing == slot:
        return JobResult(
            Verdict.ERROR,
            f"{slot} is already showing, so selecting it wouldn't change anything you could see. Pick another "
            "slot (the slot option), or select a different slot in the Hubspace app first. Nothing was sent.",
            obs,
        )
    if showing and hashes.get(showing) and hashes.get(showing) == hashes.get(slot):
        return JobResult(
            Verdict.ERROR,
            f"{slot} holds the same pattern as {showing}, which is showing now, so a switch wouldn't be visible. "
            "Pick a slot with a different pattern. Nothing was sent.",
            obs,
        )
    rows = _only_declared(ctx, info, [*R.slot_select(slot), R.power(True)])
    res = await ctx.write(rows, label=f"gate_b: select {slot}")
    obs["write"] = write_facts(res)
    if problem := _write_problem(res, f"The select of {slot}"):
        problem.observations.update(obs)
        return problem
    return JobResult(
        Verdict.NEEDS_EYES,
        f"The cloud accepted the select and readback shows {slot} "
        f"(ack {obs['write']['ack_ms']} ms, readback match {obs['write']['verified_ms']} ms). "
        "Did the lights switch?",
        obs,
        [question("gate_b", params)],
        {"t2_select": True},
    )


# ---- Gate B, unplugged ---------------------------------------------------------------------------------------------
async def gate_b_unplugged(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    if not params.get("confirmed"):
        return JobResult(
            Verdict.ERROR,
            "Switch the kit off at the wall first (unplug its power supply) and wait 10 s. Then run this again "
            "and tick “I've switched the kit off” (in a terminal: --param confirmed=1). Nothing was sent.",
        )
    info = await ctx.profile()
    caps = _caps(info)
    if await ctx.sim_power(False):
        ctx.step("SIMULATED: the simulator switched its kit off at the wall for this test.")
    try:
        before = await _read_ok(ctx, "before the select")
        current = before.get("color-individual", "custom")
        choices = [s for s in (caps.slots or list(R.SLOTS)) if s != current]
        wanted = str(params.get("slot") or "")
        target = wanted if wanted and wanted != "auto" else (choices[0] if choices else "")
        _check_slot(caps, target)
        if target == current:
            raise JobError(f"{target} is already selected; pick a different slot.")
        obs: dict[str, Any] = {
            "selected_before": current,
            "target": target,
            "available_before": before.available,
        }
        ctx.step(f"Selecting {target} while the kit is off", available=before.available)
        res = await ctx.write(R.slot_select(target), label=f"gate_b_unplugged: select {target}", verify=False)
        obs["write"] = write_facts(res)
        if res.status is not CommandStatus.APPLIED:
            return JobResult(
                Verdict.INFO,
                f"The cloud didn't accept a select for a switched-off kit ({res.error}). That is useful: note it, "
                "but it doesn't tell us whether readback reflects the device.",
                obs,
            )
        await ctx.sleep(max(UNPLUGGED_SETTLE_S, ctx.budget_s))
        after = await _read_ok(ctx, "after the select")
        shown = after.get("color-individual", "custom")
        obs.update({"readback_selected": shown, "available_after": after.available})
        ctx.step("Read back", selected=shown, available=after.available)
    finally:
        if await ctx.sim_power(True):
            ctx.step("SIMULATED: the simulator's kit is switched back on.")
    ctx.step("Plug the kit back in now.")
    if shown == target:
        obs["readback_shows"] = "cloud"
        return JobResult(
            Verdict.NEEDS_EYES,
            "Readback shows what the cloud stored, not what the device did: the switched-off kit 'has' the new "
            f"slot. The reconciler must also watch `available` (it read {after.available}). Plug the kit back in.",
            obs,
            [Q_UNPLUGGED],
            {"readback_device_level": False},
        )
    obs["readback_shows"] = "device"
    return JobResult(
        Verdict.NEEDS_EYES,
        "Readback still shows the old slot, so it seems to reflect the device itself. Plug the kit back in.",
        obs,
        [Q_UNPLUGGED],
        {"readback_device_level": True},
    )


# ---- Gate C ------------------------------------------------------------------------------------------------------
async def gate_c(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    """Replay `source` into `target` and select it — in a way whose visual check proves something (F1):
    - the target must NOT already hold the source's pattern (else a dropped frame still "matches");
    - the lights are first moved to a BEFORE look that isn't the source's pattern (a third slot, or a whole-strand
      look), held long enough to see; then the replay changes the target AND selects it (a real select change,
      so the result doesn't depend on whether rewriting the active slot re-renders — bench edge case 6);
    - the human is asked whether the lights changed from the BEFORE look to exactly the source's pattern.
    """
    source, target = str(params["source"]), str(params["target"])
    before_hold = float(params.get('before_duration_s', BEFORE_HOLD_S))
    if source == target:
        return JobResult(Verdict.ERROR, "Pick two different slots: the source is copied INTO the target.")
    info = await ctx.profile()
    caps = _caps(info)
    _check_slot(caps, source, target)
    rr = await _read_ok(ctx, "before the replay")
    value, entries = _slot_entries(rr, source)
    _slot_entries(rr, target)  # the target slot must exist too
    hashes = _hashes(rr)
    src_hash = hashes[source]
    obs: dict[str, Any] = {
        "source": source,
        "target": target,
        "length": len(entries),
        "source_hash": src_hash,
        "target_hash_before": hashes[target],
        "selected_before": rr.get("color-individual", "custom"),
        "color_mode_before": rr.get("color-mode"),
    }
    ctx.step(
        f"Read {source} ({len(entries)} entries) and {target}",
        source_hash=src_hash,
        target_hash=hashes[target],
        showing=_showing(rr),
    )
    if hashes[target] == src_hash:
        return JobResult(
            Verdict.ERROR,
            f"{target} already holds exactly {source}'s pattern, so copying it again would prove nothing: the "
            f"lights would match even if the kit ignored the write. Put a different pattern in {target} first — "
            f"run Hue ramp with slot={target} (you'll see the rainbow) or paint it in the Hubspace app — then run "
            "Gate C again. Nothing was sent.",
            obs,
        )
    # BEFORE: a look that isn't the source's pattern (and isn't the target, so the replay is a real select change).
    others = [s for s in (caps.slots or list(R.SLOTS)) if s not in (source, target)]
    before_slot = next((s for s in others if hashes.get(s) and hashes[s] != src_hash), None)
    if before_slot:
        before_name = before_slot
        before_rows = [*R.slot_select(before_slot), R.power(True)]
    else:
        look = contrast_look(caps)
        if look is None:
            return JobResult(
                Verdict.ERROR,
                f"There's no look to show before the replay: every other slot holds {source}'s pattern and this "
                "device declares no white or colour mode. Put a different pattern in another slot first. "
                "Nothing was sent.",
                obs,
            )
        before_name, before_look = look
        before_rows = R.full_state_vector(before_look, cct_range=caps.cct_range)
    obs["before_look"] = before_name
    ctx.step(
        f"BEFORE: switching the lights to {before_name} (not {source}'s pattern). Watch the strand: it holds "
        f"for about {before_hold:g} s, then {target} is rewritten with {source}'s pattern and selected."
    )
    res = await ctx.write(_only_declared(ctx, info, before_rows), label=f"gate_c: BEFORE look {before_name}")
    obs["before_write"] = write_facts(res)
    if problem := _write_problem(res, f"The BEFORE look ({before_name})"):
        problem.observations.update(obs)
        return problem
    await ctx.sleep(max(before_hold, ctx.budget_s))
    rows = _only_declared(
        ctx, info, [R.slot_write(target, copy.deepcopy(value)), *R.slot_select(target), R.power(True)]
    )
    res = await ctx.write(rows, label=f"gate_c: copy {source} → {target} + select")
    obs["write"] = write_facts(res)
    if problem := _write_problem(res, f"The replay write into {target}"):
        problem.observations.update(obs)
        if obs["write"]["http_status"] == 400:
            problem.summary = f"The cloud refused the replayed frame (HTTP 400). Stop at T2 (hand-painted slots). {res.error}"
        return problem
    after = await _read_ok(ctx, f"for {target}")
    tgt_hash = R.slot_hash(after.get(R.FRAME_CLASS, target))
    obs.update({"target_hash": tgt_hash, "hashes_match": tgt_hash == src_hash})
    ctx.step(f"Read back {target}", hash=tgt_hash, match=tgt_hash == src_hash)
    if tgt_hash != src_hash:
        return JobResult(
            Verdict.FAIL,
            f"The write was accepted but {target} reads back different from {source}.",
            obs,
        )
    return JobResult(
        Verdict.NEEDS_EYES,
        f"HTTP {obs['write']['http_status']}, and {target} reads back identical to {source}. The lights went from "
        f"the BEFORE look ({before_name}) to {target}: do they now show exactly {source}'s pattern?",
        obs,
        [question("gate_c", params)],
        {"t1_frames": True},
    )


async def _restore_touched(ctx, original, touched, label):
    # A frame write can reselect individual mode. Restore storage, then its
    # selector, then the original active look in separate verified commands.
    frames = [StateRow(*k, copy.deepcopy(original[k])) for k in touched if k[0] == R.FRAME_CLASS]
    if frames:
        await ctx.write(frames, label=label + ': restore fresh original slot')
        await ctx.sleep(ctx.budget_s)
    if ('color-individual', 'custom') in touched:
        await ctx.write(R.slot_select(original[('color-individual', 'custom')]), label=label + ': restore slot selection')
        await ctx.sleep(ctx.budget_s)
    controls = [StateRow(*k, copy.deepcopy(original[k])) for k in sorted(touched, key=lambda k:(k[0] == 'color-mode', k[0]))
                if k[0] not in (R.FRAME_CLASS, 'color-individual')]
    return await ctx.write(controls, label=label + ': restore previous active look')


async def gate_c_safe(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    """Bounded Gate C with an exact, freshly captured target-slot restoration.

    Custom 3 may be the read-only source, but is never the write target. The
    ordinary Gate C proof and human confirmation still apply independently.
    """
    source, target = str(params['source']), str(params['target'])
    if target not in ('custom-1', 'custom-2') or source == target:
        return JobResult(Verdict.ERROR, 'Use distinct slots; the temporary target must be Custom 1 or 2.')
    info = await ctx.profile()
    caps = _caps(info)
    _check_slot(caps, source, target)
    before = await _read_ok(ctx, 'before the bounded replay')
    original = {r.key: copy.deepcopy(r.value) for r in before.rows}
    source_value, _ = _slot_entries(before, source)
    target_value, _ = _slot_entries(before, target)
    if R.slot_hash(source_value) == R.slot_hash(target_value):
        return JobResult(Verdict.ERROR, 'Source and target already match; a replay would prove nothing.')
    if ('brightness', None) not in original or ('brightness', None) not in _declared(info):
        return JobResult(Verdict.ERROR, 'A current brightness readback is required. Nothing was sent.')
    ctx.artifacts['gate_c_safe_before'] = {'slot': target, 'value': copy.deepcopy(target_value),
                                         'controls': [r.to_json() for r in before.rows if r.function_class != R.FRAME_CLASS]}
    touched = set()
    class TrackedContext:
        def __getattr__(self, name): return getattr(ctx, name)
        async def write(self, rows, **kw):
            if any(r.key not in original for r in rows):
                raise JobError('A setting has no original readback to restore.')
            touched.update(r.key for r in rows)
            return await ctx.write(rows, **kw)
    tracked = TrackedContext()
    result = None
    restored = None
    try:
        level = await tracked.write([R.brightness(int(params['brightness']))], label='gate_c_safe: observation brightness')
        result = _write_problem(level, 'Setting the observation brightness')
        if result is None:
            await ctx.sleep(ctx.budget_s)
            result = await gate_c(tracked, params)
            if result.verdict is Verdict.NEEDS_EYES:
                ctx.step(f"Observe the replay for {float(params['duration_s']):g} seconds; then the original pattern returns.")
                await ctx.sleep(float(params['duration_s']))
    finally:
        if touched:
            restored = await _restore_touched(ctx, original, touched, 'gate_c_safe')
    if restored is not None:
        if problem := _write_problem(restored, 'Restoring the original slot and controls'):
            return problem
        after = await _read_ok(ctx, 'after restoring the bounded replay')
        exact = all(after.get(*k) == original[k] for k in touched)
        untouched_slots = all(after.get(R.FRAME_CLASS, s) == before.get(R.FRAME_CLASS, s) for s in caps.slots)
        if not exact or not untouched_slots:
            return JobResult(Verdict.FAIL, 'The bounded replay ended, but exact restoration was not confirmed.',
                             {'restored': exact, 'all_slots_unchanged': untouched_slots})
        result.observations.update({'restore': write_facts(restored), 'all_slots_unchanged': True,
                                    'custom_3_written': False, 'duration_s': float(params['duration_s'])})
        result.summary += ' The temporary target and previous controls have been restored exactly.'
    return result


# ---- hue ramp ----------------------------------------------------------------------------------------------------
async def hue_ramp(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    slot = str(params["slot"])
    info = await ctx.profile()
    caps = _caps(info)
    _check_slot(caps, slot)
    rr = await _read_ok(ctx, f"for {slot}")
    header, current = _slot_entries(rr, slot)
    length = len(current)
    entries = hue_ramp_entries(length)
    value = R.frame_value(header, entries, expected_length=length, cct_range=caps.cct_range)
    ctx.step(f"Built a {length}-entry hue ramp (hue = 12° × index)")
    rows = _only_declared(ctx, info, [R.slot_write(slot, value), *R.slot_select(slot), R.power(True)])
    res = await ctx.write(rows, label=f"hue_ramp: {slot}")
    obs: dict[str, Any] = {
        "slot": slot,
        "length": length,
        "hash": R.slot_hash(value),
        "first_4": [_entry_json(e) for e in entries[:4]],
        "write": write_facts(res),
    }
    if problem := _write_problem(res, "The hue ramp"):
        problem.observations.update(obs)
        return problem
    return JobResult(
        Verdict.NEEDS_EYES,
        f"The hue ramp is in {slot} and selected. Photograph the whole strand for the index map.",
        obs,
        [Q_HUE_RAMP],
    )


# ---- address probe ----------------------------------------------------------------------------------------------
async def address_probe(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    """Identify framebuffer addresses without assuming one entry equals one physical head.

    This diagnostic retains the controller's full frame length. It does not change the module map or
    promote a mapping flag; the observer must identify which physical heads respond.
    """
    if not ctx.t1_allowed:
        return JobResult(Verdict.ERROR, "Run and physically confirm Gate C on this kit before probing addresses.")
    slot = str(params["slot"])
    start, count, brightness = (int(params[k]) for k in ("start", "count", "brightness"))
    color = str(params["color"]).lower()
    pattern = str(params.get('pattern', 'range'))
    colors = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255)}
    if color not in colors or not 1 <= brightness <= 100 or pattern not in ('range', 'markers'):
        return JobResult(Verdict.ERROR, "Use red, green or blue and brightness 1–100. Nothing was sent.")
    info = await ctx.profile()
    caps = _caps(info)
    _check_slot(caps, slot)
    rr = await _read_ok(ctx, "before the address probe")
    header, current = _slot_entries(rr, slot)
    length = len(current)
    if start < 0 or count < 1 or start + count > length:
        return JobResult(Verdict.ERROR, f"Select addresses within 0–{length - 1}. Nothing was sent.")
    if caps.framebuffer_length != length or len(set(caps.framebuffer_lengths.values())) > 1:
        return JobResult(Verdict.ERROR, "The slot lengths disagree. Nothing was sent.")
    effects = R.slot_effects(header) or {}
    if any(effects.get(k) != 0 for k in ("motionEffect", "brightnessEffect")):
        return JobResult(Verdict.ERROR, "Choose a static slot with both effect codes zero before probing addresses.")
    before = contrast_look(caps)
    if before is None:
        return JobResult(Verdict.ERROR, "No declared whole-strand look is available for a clean slot reselect.")
    entries = [R.FrameEntry(0, 0, 0, 0, 0, 0)] * length
    entries[start:start + count] = [R.FrameEntry(*colors[color], 0, 100, 0)] * count
    if pattern == 'markers':
        if length != 60 or ctx.expected_heads != 30:
            return JobResult(Verdict.ERROR, 'The three-marker test requires a 60-entry controller and the declared 30-head bench strand.')
        entries = [R.FrameEntry(0, 0, 0, 0, 0, 0)] * length
        for i, rgb in [(0, colors['red']), (1, colors['green']), (29, colors['blue'])]:
            entries[i] = R.FrameEntry(*rgb, 0, 100, 0)
    value = R.frame_value(header, entries, expected_length=length, cct_range=caps.cct_range)
    requested = [R.slot_write(slot, value), *R.slot_select(slot), R.power(True), R.brightness(brightness)]
    if not {row.key for row in requested}.issubset(_declared(info)):
        return JobResult(Verdict.ERROR, "The device does not declare every required probe control. Nothing was sent.")
    # Saved with the run so the previous slot can be recovered after calibration.
    ctx.artifacts["address_probe_before"] = {"slot": slot, "value": copy.deepcopy(header)}
    obs = {"slot": slot, "length": length, "start": start, "count": count, "color": color,
           "brightness": brightness, "previous_hash": R.slot_hash(header), "hash": R.slot_hash(value), "pattern": pattern}
    if pattern == 'markers': obs['markers'] = {0: 'red', 1: 'green', 29: 'blue'}
    ctx.step("Saved the slot; changing to a whole-strand look before reselecting the probe", **obs)
    res = await ctx.write(_only_declared(ctx, info, R.full_state_vector(before[1], cct_range=caps.cct_range)),
                          label="address_probe: before look")
    if problem := _write_problem(res, "The before look"):
        return problem
    await ctx.sleep(max(LOOK_SPACING_S, ctx.budget_s))
    probe_label = '0 red, 1 green, 29 blue' if pattern == 'markers' else f'{start}–{start + count - 1} {color}'
    res = await ctx.write(requested, label=f"address_probe: {probe_label}")
    obs["write"] = write_facts(res)
    if problem := _write_problem(res, "The address probe"):
        problem.observations.update(obs)
        return problem
    return JobResult(
        Verdict.NEEDS_EYES,
        f"Controller addresses lit: {probe_label}; all other entries are off. "
        "The original slot is saved with this run. No physical mapping has been assumed.",
        obs,
        ["How many physical heads lit, and where are they when counted from the controller?"],
    )


async def address_probe_safe(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    """A held address probe with exact restoration; it never certifies a map."""
    if str(params['slot']) not in ('custom-1', 'custom-2'):
        return JobResult(Verdict.ERROR, 'Use Custom 1 or 2 for temporary address tests; Custom 3 is protected.')
    before = await _read_ok(ctx, 'before the bounded address probe')
    original = {r.key: copy.deepcopy(r.value) for r in before.rows}
    touched = set()
    class TrackedContext:
        def __getattr__(self, name): return getattr(ctx, name)
        async def write(self, rows, **kw):
            if any(r.key not in original for r in rows):
                raise JobError('A setting has no original readback to restore.')
            touched.update(r.key for r in rows)
            return await ctx.write(rows, **kw)
    result = None
    restored = None
    try:
        result = await address_probe(TrackedContext(), params)
        if result.verdict is Verdict.NEEDS_EYES:
            detail = 'markers 0 red, 1 green, 29 blue' if params.get('pattern') == 'markers' else f"addresses {params['start']} through {params['start'] + params['count'] - 1}"
            ctx.step(f"Observe {detail} for {params['duration_s']:g} seconds.")
            await ctx.sleep(float(params['duration_s']))
    finally:
        if touched:
            restored = await _restore_touched(ctx, original, touched, 'address_probe_safe')
    if restored is not None:
        if problem := _write_problem(restored, 'Restoring the address probe'):
            return problem
        after = await _read_ok(ctx, 'after restoring the address probe')
        exact = all(after.get(*k) == v for k, v in original.items() if k in touched or k[0] == R.FRAME_CLASS)
        if not exact:
            return JobResult(Verdict.FAIL, 'The address probe ended, but exact restoration was not confirmed.')
        result.observations.update({'restore': write_facts(restored), 'all_slots_unchanged': True, 'custom_3_written': False})
        result.summary += ' Original slot and controls restored exactly. No map was saved.'
    return result


# ---- timed custom-effect commissioning -------------------------------------------------------------------------
async def effect_probe(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    """Explore one explicitly selected code, without enabling it for ordinary controls.

    The candidate's meaning is unknown until observed. A static rainbow first proves the frame; only one
    effect field is then changed. The original slot header and entries are restored after the bounded test.
    """
    if not ctx.t1_allowed:
        return JobResult(Verdict.ERROR, "Physically confirm Gate C before testing custom effects.")
    slot, field = str(params['slot']), str(params['field'])
    pattern = str(params.get('pattern', 'rainbow'))
    code, speed, duration = int(params['code']), int(params['speed']), float(params['duration_s'])
    if (pattern not in ('rainbow', 'blocks') or field not in ('motionEffect', 'brightnessEffect') or not 1 <= code <= 255
            or not 0 <= speed <= 100 or not math.isfinite(duration) or not 2 <= duration <= 20):
        return JobResult(Verdict.ERROR, "Choose one effect field, code 1–255, speed 0–100 and duration 2–20 s.")
    info = await ctx.profile()
    caps = _caps(info)
    _check_slot(caps, slot)
    rr = await _read_ok(ctx, 'before the effect test')
    original, entries = _slot_entries(rr, slot)
    if len(entries) != caps.framebuffer_length or len(set(caps.framebuffer_lengths.values())) > 1:
        return JobResult(Verdict.ERROR, "Slot lengths disagree; no effect test was sent.")
    before = contrast_look(caps)
    if before is None:
        return JobResult(Verdict.ERROR, "No whole-strand look is available for a clean slot reselect.")
    test_entries = hue_ramp_entries(len(entries))
    if pattern == 'blocks':
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
        test_entries = [R.FrameEntry(*colors[(i // 10) % 3]) if i % 10 < 3
                        else R.FrameEntry(0, 0, 0, 0, 0, 0) for i in range(len(entries))]
    static = R.frame_value(original, test_entries, expected_length=len(entries),
                           effects={'motionEffect': 0, 'brightnessEffect': 0}, cct_range=caps.cct_range)
    candidate = R.frame_value(static, R.frame_entries(static), expected_length=len(entries),
                              effects={field: code, 'motionSpeed' if field == 'motionEffect' else 'brightnessSpeed': speed},
                              cct_range=caps.cct_range)
    def slot_rows(value):
        return [R.slot_write(slot, value), *R.slot_select(slot), R.power(True), R.brightness(30)]
    if not {r.key for r in slot_rows(candidate)}.issubset(_declared(info)):
        return JobResult(Verdict.ERROR, "The device does not declare all required effect controls.")
    ctx.artifacts['effect_probe_before'] = {'slot': slot, 'value': copy.deepcopy(original)}
    obs = {'slot': slot, 'field': field, 'code': code, 'speed': speed, 'duration_s': duration,
           'length': len(entries), 'pattern': pattern, 'original_hash': R.slot_hash(original), 'automatically_enabled': False,
           'expected_effects': R.slot_effects(candidate),
           'reselected_from_whole_strand': True}
    result = None
    try:
        res = await ctx.write(_only_declared(ctx, info, R.full_state_vector(before[1], cct_range=caps.cct_range)),
                              label='effect_probe: before look')
        result = _write_problem(res, 'The before look')
        if result is None:
            await ctx.sleep(max(LOOK_SPACING_S, ctx.budget_s))
            res = await ctx.write(slot_rows(static), label=f'effect_probe: static {pattern}')
            result = _write_problem(res, 'The static pattern')
        if result is None:
            await ctx.sleep(max(LOOK_SPACING_S, ctx.budget_s))
            # Some controllers may reload effect headers only when entering individual mode.
            # Keep that behavior separate from whether rewriting an already active slot works.
            res = await ctx.write(_only_declared(ctx, info, R.full_state_vector(before[1], cct_range=caps.cct_range)),
                                  label='effect_probe: leave slot before candidate')
            result = _write_problem(res, 'Leaving the static slot')
        if result is None:
            await ctx.sleep(max(LOOK_SPACING_S, ctx.budget_s))
            res = await ctx.write(slot_rows(candidate), label=f'effect_probe: candidate {field} {code}')
            obs['write'] = write_facts(res)
            obs['reported_effects'] = R.slot_effects(res.readback.get(R.FRAME_CLASS, slot)) if res.readback else None
            result = _write_problem(res, 'The candidate effect')
            ctx.step(f'Observe candidate {field} {code} for {duration:g} seconds; behavior is unverified.')
            # A cloud mismatch can still physically animate, so restoration happens regardless.
            await ctx.sleep(duration)
    finally:
        restore_rows = [R.slot_write(slot, copy.deepcopy(original)), *R.slot_select(slot), R.power(True)]
        if caps.brightness_range:
            level = rr.get('brightness')
            if isinstance(level, int) and 1 <= level <= 100:
                restore_rows.append(R.brightness(level))
        restored = await ctx.write(restore_rows, label='effect_probe: restore original slot and header')
        obs['restore'] = write_facts(restored)
        obs['restore_effects'] = R.slot_effects(restored.readback.get(R.FRAME_CLASS, slot)) if restored.readback else None
    if problem := _write_problem(restored, 'Restoring the original pattern'):
        problem.observations.update(obs)
        return problem
    if result is not None:
        result.observations.update(obs)
        result.ask_human = ['What changed during the timed effect test, if anything?']
        return result
    return JobResult(Verdict.NEEDS_EYES,
                     f'Tested candidate {field} {code} at speed {speed}; original slot restored. '
                     'This code is not automatically enabled or assigned a behavior name.',
                     obs, ['What did the colors do during the timed test: travel, pulse, flash, or stay still?'])


# ---- latency trials ----------------------------------------------------------------------------------------------
async def latency_trials(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    """Alternate between two looks that visibly differ (F2), so every trial is a change you can film:
    - two slots with different patterns (hashes differ), starting with the one that isn't showing;
    - if the two slots hold the same pattern: write a contrasting frame into one first when per-head frames are
      allowed (simulator, or Gate C proven on this kit), else alternate one slot with a whole-strand look.
    """
    n = int(params["n"])
    if not 1 <= n <= MAX_TRIALS:
        return JobResult(Verdict.ERROR, f"Pick between 1 and {MAX_TRIALS} trials. Nothing was sent.")
    requested = float(params["spacing_s"])
    if not math.isfinite(requested) or not 0 <= requested <= MAX_SPACING_S:
        return JobResult(
            Verdict.ERROR, f"Pick a spacing between 0 and {MAX_SPACING_S:g} seconds. Nothing was sent."
        )
    spacing = max(requested, ctx.budget_s)
    a, b = str(params["slot_a"]), str(params["slot_b"])
    if a == b:
        return JobResult(Verdict.ERROR, "Pick two different slots to alternate between.")
    info = await ctx.profile()
    caps = _caps(info)
    _check_slot(caps, a, b)
    rr = await _read_ok(ctx, "before the trials")
    _, entries_a = _slot_entries(rr, a)
    _, entries_b = _slot_entries(rr, b)
    hashes = _hashes(rr)
    showing = _showing(rr)
    setup: dict[str, Any] = {"hash_a": hashes[a], "hash_b": hashes[b], "showing_before": showing}
    last_sent: float | None = None

    def select(slot: str) -> tuple[str, list[StateRow]]:
        return slot, _only_declared(ctx, info, [*R.slot_select(slot), R.power(True)])

    def not_showing_first(x: str, y: str) -> tuple[str, str]:
        """(first, second): start with the slot whose pattern isn't the one on the lights now."""
        if showing == y or (showing and hashes.get(showing) == hashes[y]):
            return x, y
        return y, x

    if hashes[a] != hashes[b]:
        first, second = not_showing_first(a, b)
        looks = [select(first), select(second)]
    elif ctx.t1_allowed:
        into = b if showing != b else a
        other = a if into == b else b
        frame = R.frame_value(
            rr.get(R.FRAME_CLASS, into),
            contrast_entries(entries_a if other == a else entries_b),
            expected_length=len(entries_a),
            cct_range=caps.cct_range,
        )
        ctx.step(
            f"{a} and {b} hold the same pattern, so switching between them wouldn't show. Writing a rainbow into "
            f"{into} first (it replaces what {into} held)."
        )
        res = await ctx.write(
            _only_declared(ctx, info, [R.slot_write(into, frame)]),
            label=f"latency: contrast pattern into {into}",
        )
        setup["contrast_written_into"] = into
        setup["contrast_write"] = write_facts(res)
        if problem := _write_problem(res, f"The contrasting pattern for {into}"):
            problem.observations.update(setup)
            return problem
        last_sent = res.timings.sent_at or ctx.now()
        looks = [select(into), select(other)]
    else:
        look = contrast_look(caps)
        if look is None:
            return JobResult(
                Verdict.ERROR,
                f"{a} and {b} hold the same pattern and this device has no whole-strand look to alternate with. "
                "Put a different pattern in one of them first. Nothing was sent.",
                setup,
            )
        name, whole = look
        ctx.step(
            f"{a} and {b} hold the same pattern, and frames written by the Conductor aren't proven on this kit "
            f"yet (Gate C), so the trials alternate {a} with a whole-strand look ({name})."
        )
        slot_look = select(a)
        whole_look = (name, _only_declared(ctx, info, R.full_state_vector(whole, cct_range=caps.cct_range)))
        looks = [whole_look, slot_look] if showing == a else [slot_look, whole_look]
    ctx.step(
        f"{n} trials, one change every {spacing:g} s ({' ↔ '.join(name for name, _ in looks)}). "
        "Start filming with the clock page next to the kit."
    )
    trials: list[dict[str, Any]] = []
    for i in range(n):
        name, rows = looks[i % 2]
        await _space(ctx, last_sent, spacing)
        res = await ctx.write(rows, label=f"latency trial {i + 1}/{n}: {name}")
        facts = write_facts(res)
        last_sent = res.timings.sent_at or ctx.now()
        trials.append({"trial": i + 1, "slot": name, **facts})
    acks = [t["ack_ms"] for t in trials if t["ack_ms"] is not None]
    verified = [t["verified_ms"] for t in trials if t["verified_ms"] is not None]
    failures = [t for t in trials if t["status"] != str(CommandStatus.APPLIED)]
    obs: dict[str, Any] = {
        "n": n,
        "spacing_s": spacing,
        **setup,
        "ack_ms_p50": _round(percentile(acks, 50)),
        "ack_ms_p95": _round(percentile(acks, 95)),
        "verified_ms_p50": _round(percentile(verified, 50)),
        "verified_ms_p95": _round(percentile(verified, 95)),
        "failures": len(failures),
        "trials": trials,
    }
    summary = (
        f"{n - len(failures)}/{n} changes applied. Send→ack p50 {obs['ack_ms_p50']} ms, p95 {obs['ack_ms_p95']} ms; "
        f"send→readback match p50 {obs['verified_ms_p50']} ms, p95 {obs['verified_ms_p95']} ms. "
        "Read PUT-to-light from the video against the clock."
    )
    verdict = Verdict.NEEDS_EYES if not failures else Verdict.FAIL
    return JobResult(verdict, summary, obs, [Q_LATENCY])


# ---- whole strand (gate 7 lite) ------------------------------------------------------------------------------------
async def whole_strand(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    info = await ctx.profile()
    caps = _caps(info)
    modes = set(caps.color_modes)
    looks: list[tuple[str, R.Look]] = []
    if caps.cct_range and "white" in modes:
        lo, hi = caps.cct_range
        looks += [
            (
                f"White at {lo} K (the warmest this device allows): is it warm white?",
                R.Look("white", 80, cct=lo),
            ),
            (f"White at {hi} K (the coolest): is it cool white?", R.Look("white", 80, cct=hi)),
            (
                "Brightness 10%: is it dim but even, every puck lit?",
                R.Look("white", 10, cct=max(lo, min(CALM_CCT, hi))),
            ),
            (
                "Brightness 1%: is every puck still lit (no dropouts or flicker)?",
                R.Look("white", 1, cct=max(lo, min(CALM_CCT, hi))),
            ),
        ]
    if caps.rgb and "color" in modes:
        looks.append(
            (
                "Home Depot orange (#F96302): does it read as orange, not red or yellow?",
                R.Look("rgb", 100, rgb=HD_ORANGE),
            )
        )
    if caps.presets and "sequence" in modes:
        first = caps.presets[0]
        lo_s, hi_s = caps.speed_range or (-10, 10)
        for speed, words in ((lo_s, "slowest"), (0, "middle"), (hi_s, "fastest")):
            looks.append(
                (
                    f"Preset '{first}' at speed {speed} ({words}): does the speed look right?",
                    R.Look("preset", 70, preset=first, speed=speed),
                )
            )
    if not looks:
        return JobResult(Verdict.FAIL, "This device declares no white, colour or preset modes to try.")
    results: list[dict[str, Any]] = []
    questions: list[str] = []
    last_sent: float | None = None
    for question, look in looks:
        await _space(ctx, last_sent, LOOK_SPACING_S)
        rows = _only_declared(ctx, info, R.full_state_vector(look, cct_range=caps.cct_range))
        res = await ctx.write(rows, label=f"whole_strand: {look.kind} {look.to_json()}")
        last_sent = res.timings.sent_at or ctx.now()
        facts = write_facts(res)
        results.append({"look": look.to_json(), **facts})
        if res.status is CommandStatus.APPLIED:
            questions.append(question)
    failures = [r for r in results if r["status"] != str(CommandStatus.APPLIED)]
    obs = {"looks": results, "failures": len(failures)}
    if failures:
        return JobResult(
            Verdict.FAIL,
            f"{len(failures)} of {len(results)} looks didn't apply; see the observations.",
            obs,
            questions,
        )
    return JobResult(
        Verdict.NEEDS_EYES,
        f"All {len(results)} looks applied (one PUT each, ≥ {LOOK_SPACING_S:g} s apart). Answer what you saw.",
        obs,
        questions,
    )


# ---- restore -----------------------------------------------------------------------------------------------------
async def restore(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    info = await ctx.profile()
    caps = _caps(info)
    cct = CALM_CCT
    if caps.cct_range:
        cct = max(caps.cct_range[0], min(CALM_CCT, caps.cct_range[1]))
    rows = _only_declared(
        ctx,
        info,
        R.full_state_vector(R.Look("white", CALM_BRIGHTNESS, cct=cct), cct_range=caps.cct_range),
    )
    res = await ctx.write(rows, label=f"restore: white {cct} K {CALM_BRIGHTNESS}%")
    obs = {"cct": cct, "brightness": CALM_BRIGHTNESS, "write": write_facts(res)}
    if problem := _write_problem(res, "The calm look"):
        problem.observations.update(obs)
        return problem
    return JobResult(Verdict.PASS, f"The kit is back to calm white {cct} K at {CALM_BRIGHTNESS}%.", obs)


# ---- the remaining UNVERIFIED device behaviours (07 §6, §10; 04 bench gates #6, #10, #11) ----------------------------
def _stored_stamps(info: DeviceInfo) -> dict[tuple[str, str | None], int]:
    """(functionClass, functionInstance) → the lastUpdateTime the cloud holds for that row (from the raw profile:
    the simulator's `states` list or Afero's `state.values`)."""
    raw = info.raw if isinstance(info.raw, Mapping) else {}
    rows = raw.get("states")
    if not isinstance(rows, list):
        state = raw.get("state")
        rows = state.get("values") if isinstance(state, Mapping) else None
    out: dict[tuple[str, str | None], int] = {}
    for row in rows or []:
        stamp = row.get("lastUpdateTime") if isinstance(row, Mapping) else None
        if isinstance(stamp, int) and not isinstance(stamp, bool):
            out[(row.get("functionClass"), row.get("functionInstance"))] = stamp
    return out


def _visible_target(caps: Capabilities, rr: ReadResult, wanted: str) -> str:
    """The slot a select should switch to so the change is visible: `wanted`, or (auto) a slot that isn't
    selected and, if possible, doesn't hold the pattern that is showing. JobError when there is none."""
    current = rr.get("color-individual", "custom")
    showing = _showing(rr)
    hashes = _hashes(rr)
    if wanted and wanted != "auto":
        _check_slot(caps, wanted)
        if wanted == current:
            raise JobError(f"{wanted} is already selected, so selecting it again wouldn't show anything.")
        return wanted
    choices = [s for s in (caps.slots or list(R.SLOTS)) if s != current]
    if not choices:
        raise JobError("There's no other slot to select.")
    differ = [s for s in choices if not showing or hashes.get(s) != hashes.get(showing)]
    target = (differ or choices)[0]
    _check_slot(caps, target)
    return target


async def stale_clock_put(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    """07 §10: does a PUT stamped lastUpdateTime = now − 10 min get stored, or answered 200 and dropped?
    One select stamped STALE_LAG_S in the past, then readback. If it was dropped, the same select with a current
    stamp follows as the control (so a drop is pinned on the stamp, not on the select)."""
    info = await ctx.profile()
    caps = _caps(info)
    before = await _read_ok(ctx, "before the stale write")
    target = _visible_target(caps, before, str(params.get("slot") or "auto"))
    selected_before = before.get("color-individual", "custom")
    stamps = _stored_stamps(info)
    stored_stamp = stamps.get(("color-individual", "custom"))
    obs: dict[str, Any] = {
        "target": target,
        "selected_before": selected_before,
        "color_mode_before": before.get("color-mode"),
        "lag_s": STALE_LAG_S,
        "cloud_stamp_before_ms": stored_stamp,
    }
    rows = _only_declared(ctx, info, [*R.slot_select(target), R.power(True)])
    ctx.step(
        f"Selecting {target} with every row stamped {STALE_LAG_S / 60:g} minutes in the past "
        "(lastUpdateTime = now − 600 s). Watch the strand."
    )
    res = await ctx.write(
        rows,
        label=f"stale_clock_put: select {target}, stamped −{STALE_LAG_S:g} s",
        verify=False,
        stamp_offset_s=-STALE_LAG_S,
    )
    facts = write_facts(res)
    sent_at = res.timings.sent_at
    facts["stamp_sent_ms"] = round((sent_at - STALE_LAG_S) * 1000) if sent_at is not None else None
    obs["write"] = facts
    if res.status is not CommandStatus.APPLIED:
        if res.status in (CommandStatus.DENIED, CommandStatus.LOCKED, CommandStatus.INVALID):
            return JobResult(Verdict.ERROR, f"The stale-stamped select wasn't sent: {res.error}", obs)
        return JobResult(
            Verdict.INFO,
            f"The cloud refused the stale-stamped select ({res.error}). A lagging clock gets an error back, not a "
            "silent drop — the executor would see and report it. Send the JSON to the build session.",
            obs,
        )
    await ctx.sleep(max(STALE_SETTLE_S, ctx.budget_s))
    after = await _read_ok(ctx, "after the stale write")
    shown = after.get("color-individual", "custom")
    after_info = await ctx.profile()
    obs.update(
        {
            "readback_selected": shown,
            "cloud_stamp_after_ms": _stored_stamps(after_info).get(("color-individual", "custom")),
        }
    )
    ctx.step("Read back", selected=shown, cloud_stamp_ms=obs["cloud_stamp_after_ms"])
    if shown == target:
        obs["stale_write"] = "stored"
        return JobResult(
            Verdict.NEEDS_EYES,
            f"HTTP {facts['http_status']} and readback shows {target}: the cloud STORED a write stamped "
            f"{STALE_LAG_S / 60:g} minutes in the past, so a lagging clock doesn't get writes dropped (up to "
            "10 min). Did the lights switch?",
            obs,
            [fill(Q_STALE_STORED, {"slot": target})],
        )
    obs["stale_write"] = "dropped"
    ctx.step(
        f"The cloud answered {facts['http_status']} but readback still shows {shown}: the stale write was "
        f"dropped. Control: the same select with a current stamp, in {max(STALE_SETTLE_S, ctx.budget_s):g} s."
    )
    control = await ctx.write(rows, label=f"stale_clock_put: control select {target}, current stamp")
    obs["control_write"] = write_facts(control)
    control_stored = control.status is CommandStatus.APPLIED
    obs["control_stored"] = control_stored
    if not control_stored:
        return JobResult(
            Verdict.FAIL,
            f"The stale-stamped select was answered {facts['http_status']} and dropped, but the control select "
            f"with a current stamp didn't take either ({control.error}), so the stamp isn't proven to be the "
            "cause. Check the kit and run it again.",
            obs,
        )
    return JobResult(
        Verdict.NEEDS_EYES,
        f"A write stamped {STALE_LAG_S / 60:g} minutes in the past was answered HTTP {facts['http_status']} and "
        f"silently DROPPED; the same select with a current stamp was stored. A lagging venue-box clock would "
        "lose writes without any error: NTP sync is a hard requirement (07 §10).",
        obs,
        [fill(Q_STALE_DROPPED, {"slot": target})],
    )


async def active_slot_rewrite(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    """04 bench gate #6: rewrite the slot that is showing with a visibly different frame, WITHOUT reselecting it,
    then read back and ask whether the lights changed. Settles whether the reconciler's drifted-slot rewrite also
    needs a reselect."""
    info = await ctx.profile()
    caps = _caps(info)
    if not caps.individual:
        _check_slot(caps)  # raises the plain "no per-head mode" message
    if not ctx.t1_allowed:
        return JobResult(
            Verdict.ERROR,
            "Frames written by the Conductor aren't proven on this kit yet, so a 'no' here couldn't be told apart "
            "from a refused frame. Pass Gate C and apply its flag first. Nothing was sent.",
        )
    rr = await _read_ok(ctx, "before the rewrite")
    showing = _showing(rr)
    if showing is None:
        return JobResult(
            Verdict.ERROR,
            "The kit isn't showing a slot (it isn't in per-head mode). Run Gate B or the hue ramp first so a slot "
            "is showing. Nothing was sent.",
            {"color_mode": rr.get("color-mode"), "selected_slot": rr.get("color-individual", "custom")},
        )
    wanted = str(params.get("slot") or "auto")
    slot = showing if wanted == "auto" else wanted
    if slot != showing:
        return JobResult(
            Verdict.ERROR,
            f"This test rewrites the slot that is SHOWING ({showing}); {slot} isn't. Leave the slot option on "
            "auto. Nothing was sent.",
            {"showing": showing},
        )
    _check_slot(caps, slot)
    value, entries = _slot_entries(rr, slot)
    frame = R.frame_value(
        value, contrast_entries(entries), expected_length=len(entries), cct_range=caps.cct_range
    )
    obs: dict[str, Any] = {
        "slot": slot,
        "length": len(entries),
        "hash_before": R.slot_hash(value),
        "hash_written": R.slot_hash(frame),
    }
    ctx.step(
        f"{slot} is showing. Rewriting it with a different pattern (a rainbow, or its reverse) — no select, no "
        "colour-mode or power rows. Watch the strand."
    )
    res = await ctx.write(
        _only_declared(ctx, info, [R.slot_write(slot, frame)]),
        label=f"active_slot_rewrite: {slot}, no select",
    )
    obs["write"] = write_facts(res)
    if problem := _write_problem(res, f"The rewrite of {slot}"):
        problem.observations.update(obs)
        return problem
    after = await _read_ok(ctx, "after the rewrite")
    obs.update(
        {
            "hash_after": R.slot_hash(after.get(R.FRAME_CLASS, slot)),
            "still_selected": after.get("color-individual", "custom") == slot,
            "color_mode_after": after.get("color-mode"),
        }
    )
    ctx.step("Read back", hash=obs["hash_after"], still_selected=obs["still_selected"])
    return JobResult(
        Verdict.NEEDS_EYES,
        f"HTTP {obs['write']['http_status']}; {slot} reads back as the new pattern and is still selected. Yes = "
        "rewriting the showing slot re-renders it on its own; No = a slot rewrite must be followed by a reselect.",
        obs,
        [fill(Q_ACTIVE_REWRITE, {"slot": slot})],
    )


def _ramp_entries(length: int, i: int) -> list[R.FrameEntry]:
    """Write i's frame: the hue ramp rotated by i heads, so consecutive writes always differ."""
    ramp = hue_ramp_entries(length)
    k = (i + 1) % max(length, 1) or 1
    return ramp[k:] + ramp[:k]


async def rate_ramp(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    """04 bench gate #11: frame writes to one device at 5 s, 2 s, then 1 s spacing (never faster than 1 write/s and
    never below the executor's budget); each write is read back and compared with what was sent (a 2xx whose
    readback doesn't show it is a silent drop). No retries: it stops at the first 429/403, or any other failure."""
    per_step = int(params["writes_per_step"])
    lo, hi = PARAM_BOUNDS["rate_ramp"]["writes_per_step"]
    if not lo <= per_step <= hi:
        return JobResult(Verdict.ERROR, f"Pick between {lo:g} and {hi:g} writes per step. Nothing was sent.")
    slot = str(params["slot"])
    info = await ctx.profile()
    caps = _caps(info)
    _check_slot(caps, slot)
    if not ctx.t1_allowed:
        return JobResult(
            Verdict.ERROR,
            "The ramp writes frames, and frames written by the Conductor aren't proven on this kit yet. Pass Gate C "
            "and apply its flag first. Nothing was sent.",
        )
    rr = await _read_ok(ctx, f"for {slot}")
    header, current = _slot_entries(rr, slot)
    length = len(current)
    spacings = [max(s, ctx.budget_s, RAMP_MIN_SPACING_S) for s in RAMP_SPACINGS_S]
    ctx.step(
        f"{per_step} frame writes into {slot} at each spacing ({', '.join(f'{s:g} s' for s in spacings)}), each "
        "read back. Stops at the first 429/403 (no retries)."
    )
    writes: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    stop_reason: str | None = None
    throttled = False
    last_sent: float | None = None
    n = 0
    for spacing in spacings:
        step = {"spacing_s": spacing, "writes": 0, "stored": 0, "silent_drops": 0, "unverified": 0}
        steps.append(step)
        for _ in range(per_step):
            await _space(ctx, last_sent, spacing)
            value = R.frame_value(
                header, _ramp_entries(length, n), expected_length=length, cct_range=caps.cct_range
            )
            sent_hash = R.slot_hash(value)
            res = await ctx.write(
                [R.slot_write(slot, value)],
                label=f"rate_ramp {n + 1}: {spacing:g} s spacing",
                verify=False,
                max_attempts=1,
            )
            n += 1
            last_sent = res.timings.sent_at or ctx.now()
            facts = write_facts(res)
            entry: dict[str, Any] = {
                "n": n,
                "spacing_s": spacing,
                "status": facts["status"],
                "http_status": facts["http_status"],
                "sent_at": facts["sent_at"],
                "ack_ms": facts["ack_ms"],
                "sent_hash": sent_hash,
            }
            writes.append(entry)
            step["writes"] += 1
            if res.status is not CommandStatus.APPLIED:
                status = facts["http_status"]
                last = res.attempts[-1] if res.attempts else None
                throttled = status in (429, 403) or (last is not None and last.outcome is Outcome.THROTTLED)
                stop_reason = (
                    f"throttled (HTTP {status}) at {spacing:g} s spacing, write {n}"
                    if throttled
                    else f"write {n} failed at {spacing:g} s spacing: {res.error}"
                )
                break
            back = await ctx.read()
            got = R.slot_hash(back.get(R.FRAME_CLASS, slot)) if back.ok else None
            entry["readback"] = "unverified" if not back.ok else ("stored" if got == sent_hash else "dropped")
            step[
                {"stored": "stored", "dropped": "silent_drops", "unverified": "unverified"}[entry["readback"]]
            ] += 1
        if stop_reason:
            break
    gaps = [
        round(b["sent_at"] - a["sent_at"], 3) for a, b in pairwise(writes) if a["sent_at"] and b["sent_at"]
    ]
    drops = sum(s["silent_drops"] for s in steps)
    obs: dict[str, Any] = {
        "slot": slot,
        "length": length,
        "writes_sent": len(writes),
        "silent_drops": drops,
        "unverified": sum(s["unverified"] for s in steps),
        "min_gap_s": min(gaps) if gaps else None,
        "throttled": throttled,
        "stopped": stop_reason,
        "steps": steps,
        "writes": writes,
    }
    per = "; ".join(
        f"{s['spacing_s']:g} s: {s['stored']}/{s['writes']} stored, {s['silent_drops']} dropped"
        for s in steps
    )
    if throttled:
        return JobResult(
            Verdict.FAIL,
            f"Hubspace throttled this device: {stop_reason}. The ramp stopped there. {per}. Send the JSON to the "
            "build session: the per-device budget must stay slower than this.",
            obs,
        )
    if stop_reason:
        return JobResult(Verdict.ERROR, f"The ramp stopped: {stop_reason}. {per}.", obs)
    if drops:
        return JobResult(
            Verdict.INFO,
            f"No throttling down to 1 write/s, but {drops} of {len(writes)} writes were answered 2xx and not "
            f"stored (silent drops). {per}.",
            obs,
        )
    return JobResult(
        Verdict.PASS,
        f"All {len(writes)} frame writes stored and read back, no 429/403, down to 1 write/s. {per}.",
        obs,
    )


async def head_count_boot(ctx: JobContext, params: dict[str, Any]) -> JobResult:
    """07 §6 / 04 bench gate #10: does the cloud profile reflect the head count the controller detects at boot?
    The person boots the kit with strings 2–3 unplugged (the '12-puck fault' stand-in); read-only, this dumps the
    segment hints and all three framebuffer lengths (profile and readback) and compares them with the full kit."""
    if not params.get("confirmed"):
        return JobResult(
            Verdict.ERROR,
            "First: switch the kit off at the wall, unplug strings 2 and 3 (leave only string 1 on the "
            "controller), switch it back on and wait for it to boot and reconnect (about 30 s). Then tick “I've "
            "booted the kit with strings 2 and 3 unplugged” and run this again (in a terminal: --param "
            "confirmed=1). Nothing was sent.",
        )
    full = int(params["full_heads"])
    ctx.step("Reading the profile and the state. Nothing is written.")
    info = await ctx.profile()
    caps = _caps(info)
    rr = await _read_ok(ctx, "for the framebuffer lengths")
    hints: dict[str, list[str]] = {}
    for fn in info.functions:
        if fn.get("functionClass") != "color-individual":
            continue
        for v in fn.get("values") or []:
            if isinstance(v, Mapping):
                hints[str(v.get("name"))] = [str(h) for h in v.get("hints") or []]
    profile_lengths = dict(sorted(caps.framebuffer_lengths.items()))
    readback_lengths = {
        s: len(entries) for s in R.SLOTS if (entries := R.frame_entries(rr.get(R.FRAME_CLASS, s))) is not None
    }
    obs: dict[str, Any] = {
        "full_heads": full,
        "expected_heads": ctx.expected_heads,
        "segment_hint": caps.segment_hint,
        "hints": hints,
        "profile_framebuffer_lengths": profile_lengths,
        "readback_framebuffer_lengths": readback_lengths,
        "available": rr.available,
    }
    if info.raw is not None:
        ctx.artifacts["profile_boot_raw"] = copy.deepcopy(info.raw)
    counts = [caps.segment_hint, *profile_lengths.values(), *readback_lengths.values()]
    reflects = any(c is not None and c != full for c in counts)
    obs["profile_reflects_detected_count"] = reflects
    ctx.step(
        "Profile read",
        segment_hint=caps.segment_hint,
        profile_lengths=profile_lengths,
        readback_lengths=readback_lengths,
    )
    ctx.step("Now switch the kit off, plug strings 2 and 3 back in, switch it on, and run Gate A.")
    sim = (
        " SIMULATED: the simulator doesn't model the head-count fault, so this proves nothing."
        if ctx.simulated
        else ""
    )
    if reflects:
        summary = (
            f"The cloud profile CHANGED with strings 2–3 unplugged (segment hint {caps.segment_hint}, frame lengths "
            f"{profile_lengths}, readback {readback_lengths}; full kit = {full}): the 7 am check can read the head "
            "count from the API."
        )
    else:
        summary = (
            f"The cloud profile did NOT change: segment hint {caps.segment_hint} and every frame length is still "
            f"{full} with strings 2–3 unplugged. The 12-puck fault can only be caught visually (07 §6)."
        )
    return JobResult(Verdict.NEEDS_EYES, summary + sim, obs, [Q_HEAD_BOOT])


# ---- the catalogue -----------------------------------------------------------------------------------------------
JOBS: dict[str, Job] = {
    j.spec.name: j
    for j in (
        Job(
            JobSpec(
                "gate_a",
                "Gate A — read-only profile dump",
                "Reads what the kit says it can do: slots, frame length, colour modes, white range, presets.",
                writes=False,
                look_for="Nothing changes on the lights. Pass = 3 slots with a 30-entry frame.",
            ),
            gate_a,
        ),
        Job(
            JobSpec(
                "index_order",
                "Index order — which end is puck 1",
                "Reads slot 1 after you app-paint puck 1 red, 2 green, 3 blue, 4 white.",
                writes=False,
                look_for="In the Hubspace app first: paint pucks 1–4 red, green, blue, white into slot 1 and save.",
                params={"slot": "custom-1"},
            ),
            index_order,
        ),
        Job(
            JobSpec(
                "gate_b",
                "Gate B — select a slot by API",
                "Switches the kit to slot 2 (or the slot option) with one small write and checks the cloud agrees.",
                writes=True,
                look_for="The strand should switch to that slot's pattern within about 2 seconds. It refuses a "
                "slot that is already showing (the switch wouldn't be visible).",
                params={"slot": "custom-2"},
            ),
            gate_b,
        ),
        Job(
            JobSpec(
                "gate_b_unplugged",
                "Gate B — unplugged readback test",
                "With the kit switched off at the wall, selects another slot and reads it back.",
                writes=True,
                look_for="Switch the kit off at the wall first. The lights stay dark; plug back in afterwards.",
                params={"confirmed": False, "slot": "auto"},
            ),
            gate_b_unplugged,
        ),
        Job(
            JobSpec(
                "gate_c",
                "Gate C — replay slot 1 into slot 2",
                "Shows a BEFORE look that isn't slot 1's pattern, then copies slot 1's exact pattern into slot 2 "
                "and selects it, in one write.",
                writes=True,
                look_for="Watch the BEFORE look (about 4 s), then the change: the strand should end up showing "
                "exactly slot 1's pattern. It refuses to run if slot 2 already holds slot 1's pattern.",
                params={"source": "custom-1", "target": "custom-2"},
            ),
            gate_c,
        ),
        Job(
            JobSpec(
                "gate_c_safe",
                "Gate C — bounded replay with exact restoration",
                "Replays a source into Custom 1 or 2, holds briefly for observation, then restores its fresh original value and previous controls.",
                writes=True,
                look_for="Observe the BEFORE look and replay. Original patterns return automatically; Custom 3 is never written.",
                params={"source": "custom-3", "target": "custom-1", "brightness": 30, "duration_s": 8.0, "before_duration_s": 4.0},
            ),
            gate_c_safe,
        ),
        Job(
            JobSpec(
                "hue_ramp",
                "Hue ramp — rainbow for the index map",
                "Writes a rainbow (each puck a different hue) into slot 3 and selects it.",
                writes=True,
                look_for="Every puck a different colour, red at one end through the rainbow. Photograph it.",
                params={"slot": "custom-3"},
            ),
            hue_ramp,
        ),
        Job(
            JobSpec(
                "address_probe",
                "Address probe — locate individual lights",
                "After Gate C, lights one address or a consecutive range in a saved slot; all other entries are off.",
                writes=True,
                look_for="After a brief whole-strand look, count which physical heads light from the controller end. "
                "Addresses start at 0. The prior slot pattern is saved with the run; the probe stays visible.",
                params={"slot": "custom-3", "start": 0, "count": 1, "color": "red", "brightness": 40},
            ),
            address_probe,
        ),
        Job(
            JobSpec(
                "address_probe_safe",
                "Address probe — bounded with exact restoration",
                "Holds one controller address or a range, then restores the original slot and look. Requires its own Gate C proof.",
                writes=True,
                look_for="Count responding physical heads from the controller. No address map is inferred or saved.",
                params={"slot": "custom-2", "start": 0, "count": 1, "color": "red", "brightness": 30, "duration_s": 60.0, "pattern": "range"},
            ),
            address_probe_safe,
        ),
        Job(
            JobSpec(
                "effect_probe",
                "Custom effect test — timed candidate",
                "Tests one unverified effect code on a rainbow or separated color blocks at 30%, then restores the original slot.",
                writes=True,
                look_for="Watch the static rainbow first, then the candidate effect. Report actual behavior; "
                "this does not enable or name the code. The original slot is restored within 20 seconds plus network time.",
                params={"slot": "custom-3", "field": "motionEffect", "code": 1, "speed": 30, "duration_s": 15.0,
                        "pattern": "rainbow"},
            ),
            effect_probe,
        ),
        Job(
            JobSpec(
                "latency_trials",
                "Latency trials — PUT to light",
                "Alternates between two slots with different patterns N times (up to 100, at most 30 s apart), "
                "logging send, ack and readback times.",
                writes=True,
                look_for="Film the kit with the clock page beside it for every trial. Every trial should change "
                "the lights.",
                params={"n": 20, "spacing_s": 3.0, "slot_a": "custom-2", "slot_b": "custom-3"},
            ),
            latency_trials,
        ),
        Job(
            JobSpec(
                "whole_strand",
                "Whole-strand looks",
                "Warmest and coolest white, 10% and 1% dimming, HD orange, and a preset at three speeds.",
                writes=True,
                look_for="Each look holds for about 2 seconds. Watch evenness, dropouts and colour.",
            ),
            whole_strand,
        ),
        Job(
            JobSpec(
                "stale_clock_put",
                "Stale clock — a write stamped 10 minutes ago",
                "Selects another slot with lastUpdateTime set 10 minutes in the past, then reads back: was it stored "
                "or answered 200 and dropped? If dropped, the same select with a current stamp follows.",
                writes=True,
                look_for="Film the strand with the clock page. Note whether it switches after the first write, "
                "after the second (only sent if the first was dropped), or not at all.",
                params={"slot": "auto"},
            ),
            stale_clock_put,
        ),
        Job(
            JobSpec(
                "active_slot_rewrite",
                "Rewrite the showing slot — no reselect",
                "Rewrites the slot that is showing with a visibly different pattern, without selecting it again, "
                "and reads it back.",
                writes=True,
                look_for="A slot must be showing first (run Gate B or the hue ramp). Watch whether the lights "
                "change to the new pattern on their own.",
                params={"slot": "auto"},
            ),
            active_slot_rewrite,
        ),
        Job(
            JobSpec(
                "rate_ramp",
                "Rate ramp — how fast before Hubspace throttles",
                "Frame writes into one slot at 5 s, 2 s, then 1 s spacing (never faster than 1 per second), each "
                "read back to catch silent drops. Stops at the first 429/403; no retries.",
                writes=True,
                look_for="About 1½ minutes with the defaults. If slot 3 is showing, the rainbow steps along with "
                "every write. Nothing to answer: the numbers are the result.",
                params={"writes_per_step": 10, "slot": "custom-3"},
            ),
            rate_ramp,
        ),
        Job(
            JobSpec(
                "head_count_boot",
                "Head count at boot — strings 2–3 unplugged",
                "After you boot the kit with only string 1 attached, reads the segment hints and all three frame "
                "lengths: does the cloud notice the missing pucks?",
                writes=False,
                look_for="Before running: kit off at the wall, unplug strings 2 and 3, back on, wait 30 s. "
                "Count the lit pucks. Afterwards plug them back in, power-cycle, and run Gate A.",
                params={"confirmed": False, "full_heads": 30},
            ),
            head_count_boot,
        ),
        Job(
            JobSpec(
                "restore",
                "Restore — calm white",
                "Puts the kit back to warm white 2700 K at 50%.",
                writes=True,
                look_for="A calm, even warm white.",
            ),
            restore,
        ),
    )
}


def coerce_params(spec: JobSpec, given: dict[str, Any] | None) -> dict[str, Any]:
    """The job's params: defaults overlaid with `given`, each converted to its default's type. ValueError with a
    plain message for unknown names or unusable values."""
    out = dict(spec.params)
    bounds = PARAM_BOUNDS.get(spec.name, {})
    for name, raw in (given or {}).items():
        if name not in spec.params:
            known = ", ".join(spec.params) or "none"
            raise ValueError(f"{spec.name} has no option {name!r} (options: {known}).")
        default = spec.params[name]
        try:
            out[name] = _convert(default, raw)
        except (TypeError, ValueError):
            raise ValueError(f"{name}={raw!r} isn't a valid {type(default).__name__}.") from None
        if name in bounds:
            lo, hi = bounds[name]
            if not lo <= out[name] <= hi:
                raise ValueError(f"{name} must be between {lo:g} and {hi:g} (got {raw!r}).")
    return out


def _convert(default: Any, raw: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on", "y"):
            return True
        if text in ("0", "false", "no", "off", "n", ""):
            return False
        raise ValueError(raw)
    if isinstance(default, int):
        if isinstance(raw, bool):
            raise TypeError(raw)
        value = float(raw)
        if not value.is_integer():
            raise ValueError(raw)
        return int(value)
    if isinstance(default, float):
        if isinstance(raw, bool):
            raise TypeError(raw)
        value = float(raw)
        if not math.isfinite(value):  # inf/nan would make a sleep that never ends
            raise ValueError(raw)
        return value
    text = str(raw).strip()
    if len(text) > 80:
        raise ValueError(raw)
    return text
