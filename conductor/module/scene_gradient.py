"""Paired static scene gradients, with a durable backup and exact restoration.

Only Custom 1 is used. Both controllers must pass their own T1 and mapping
checks before either is changed. Custom 3 is never written by this feature.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import secrets
import time
from pathlib import Path

from conductor import rows as R
from conductor.drivers.base import StateRow
from conductor.module.pair import PAIR_IDS, validate_action
from conductor.module.session import ModuleError
from conductor.tokens import atomic_write

log = logging.getLogger(__name__)
SLOT = "custom-1"
CONTROL_KEYS = [
    ("power", None),
    ("brightness", None),
    ("color-mode", None),
    ("color-individual", "custom"),
    ("color-rgb", None),
]
# The dark reset is a full RGB look, so it also writes these; they are backed up when the readback has them.
OPTIONAL_CONTROL_KEYS = [("speed", "color-sequence"), ("toggle", "party-mode")]


def _path(data):
    return Path(data) / "studio-gradients" / "active.json"


def _load(data):
    p = _path(data)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _save(data, record):
    p = _path(data)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8"))


def status(data):
    record = _load(data)
    return {
        "pending": bool(record and record["pending"]),
        "slot": SLOT,
        "label": record.get("label") if record else None,
    }


def _check_sessions(sessions, *, painting=False):
    if tuple(s.device_id for s in sessions) != PAIR_IDS:
        raise ModuleError("Use the two registered shop modules.", 400)
    for s in sessions:
        s._check_can_write()
        if s.in_flight or s.sweep_state.state in ("running", "paused"):
            raise ModuleError("Stop other module activity before changing a scene gradient.")
        if painting:
            s._require_t1()
            if s.n != 30 or s.frame_indices() is None:
                raise ModuleError("Each shop module needs its own verified 30-light controller map.")
            if SLOT not in s.caps.slots:
                raise ModuleError("Custom 1 is unavailable on a module.")


def _check_binding(record, sessions):
    for s in sessions:
        saved = record["modules"].get(s.device_id)
        if not saved or saved["address"] != s.address:
            raise ModuleError("The gradient backup belongs to a different registered controller.")


def _ids(sessions):
    return ", ".join(s.device_id for s in sessions)


def _state_replies(sessions, results):
    return {s.device_id: {"result": r, "state": s.snapshot()} for s, r in zip(sessions, results)}


async def _send_wave(sessions, fn, phase):
    results = await asyncio.gather(*(fn(s) for s in sessions), return_exceptions=True)
    for s, r in zip(sessions, results):
        if isinstance(r, ModuleError):
            log.warning("scene gradient: %s refused on %s: %s", phase, s.device_id, r.message)
        elif isinstance(r, BaseException):
            log.error("scene gradient: %s failed on %s", phase, s.device_id, exc_info=r)
    return [
        {"status": "failed", "error": str(r) if isinstance(r, ModuleError) else "Module command failed."}
        if isinstance(r, BaseException)
        else r
        for r in results
    ]


async def restore(sessions, data):
    _check_sessions(sessions)
    record = _load(data)
    if not record or not record["pending"]:
        raise ModuleError("There is no scene-gradient backup awaiting restoration.")
    _check_binding(record, sessions)
    await asyncio.gather(*(s.load() for s in sessions))
    # Never overwrite an unrelated edit made since the app last wrote Custom 1.
    for s in sessions:
        saved = record["modules"][s.device_id]
        current = s._slot_value(SLOT)
        allowed = [saved["original"], *saved.get("allowed", [])]
        if current not in allowed:
            raise ModuleError(
                f"{s.device_id}: Custom 1 changed outside this gradient session. Its newer value was preserved."
            )

    async def restore_one(s):
        saved = record["modules"][s.device_id]
        # A frame write can reselect individual mode. Restore storage first,
        # then its selector, then the original active look in separate commands.
        await s._send(
            [R.slot_write(SLOT, copy.deepcopy(saved["original"]))],
            "scene gradient: restore original Custom 1",
        )
        controls = [StateRow.from_wire(r) for r in saved["controls"]]
        selected = next(r.value for r in controls if r.key == ("color-individual", "custom"))
        await s._send(R.slot_select(selected), "scene gradient: restore original selection")
        rows = sorted(
            (r for r in controls if r.function_class != "color-individual"),
            key=lambda r: r.function_class == "color-mode",
        )
        return await s._send(rows, "scene gradient: restore original active look")

    results = await _send_wave(sessions, restore_one, "restore")
    exact = all(
        r.get("status") == "applied"
        and s._slot_value(SLOT) == record["modules"][s.device_id]["original"]
        and all(
            s._reported_state().get(StateRow.from_wire(v).key) == v["value"]
            for v in record["modules"][s.device_id]["controls"]
        )
        for s, r in zip(sessions, results)
    )
    if exact:
        record["pending"] = False
        record["restored_at"] = time.time()
        _save(data, record)
    else:
        log.warning(
            "scene gradient: restore on %s not confirmed exactly; the backup stays pending", _ids(sessions)
        )
    return {
        "ok": exact,
        "phase": "restored",
        "gradient": status(data),
        "modules": _state_replies(sessions, results),
    }


async def apply(sessions, colors, brightness, label, data, *, sleep=asyncio.sleep):
    _check_sessions(sessions, painting=True)
    if len(colors) != 30 or any(
        len(c) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in c) for c in colors
    ):
        raise ModuleError("A scene gradient needs exactly 30 RGB colors.", 400)
    if type(brightness) is not int or not 1 <= brightness <= 100:
        raise ModuleError("Choose brightness from 1 to 100.", 400)
    dark = {"kind": "rgb", "rgb": [0, 0, 0], "brightness": brightness}
    validate_action(sessions, {"look": dark})
    await asyncio.gather(*(s.load() for s in sessions))
    _check_sessions(sessions, painting=True)
    record = _load(data)
    if record and record["pending"]:
        _check_binding(record, sessions)
        for s in sessions:
            saved = record["modules"][s.device_id]
            if s._slot_value(SLOT) not in [saved["original"], *saved.get("allowed", [])]:
                raise ModuleError(
                    f"{s.device_id}: Custom 1 changed outside this scene session. Nothing was overwritten."
                )
    else:
        record = {"version": 1, "pending": True, "created_at": time.time(), "modules": {}}
        for s in sessions:
            old = s._reported_state()
            if any(k not in old for k in CONTROL_KEYS) or R.frame_entries(s._slot_value(SLOT)) is None:
                raise ModuleError("A fresh complete readback is required before making the gradient backup.")
            record["modules"][s.device_id] = {
                "address": s.address,
                "original": copy.deepcopy(s._slot_value(SLOT)),
                "all_slots": {slot: copy.deepcopy(s._slot_value(slot)) for slot in s.caps.slots},
                "controls": [
                    StateRow(k[0], k[1], copy.deepcopy(old[k])).to_json()
                    for k in CONTROL_KEYS + [k for k in OPTIONAL_CONTROL_KEYS if k in old]
                ],
                "allowed": [],
            }
        folder = _path(data).parent
        folder.mkdir(parents=True, exist_ok=True)
        atomic_write(
            folder / f"backup-{int(time.time())}-{secrets.token_hex(4)}.json",
            json.dumps(record, indent=2).encode(),
        )
    frame = [R.FrameEntry(*rgb, 0, 100, 0) for rgb in colors]
    candidates = {}
    for s in sessions:
        candidates[s.device_id] = R.frame_value(
            s._slot_value(SLOT),
            s._controller_frame(frame),
            expected_length=s.caps.framebuffer_length,
            effects={"motionEffect": 0, "brightnessEffect": 0},
            cct_range=s.caps.cct_range,
        )
        saved = record["modules"][s.device_id]
        # Keep the previous and proposed frame until readback resolves an interrupted write.
        saved["allowed"] = [copy.deepcopy(s._slot_value(SLOT)), copy.deepcopy(candidates[s.device_id])]
    record["label"] = label
    _save(data, record)  # durable recovery information exists before the first physical command
    phase = "reset_failed"
    try:
        reset = await _send_wave(sessions, lambda s: s.whole(look=dark), "dark reset")
        if not all(r.get("status") == "applied" for r in reset):
            raise ModuleError("The dark reset was not confirmed on both modules.")
        await sleep(max(s.deps.settings.executor.min_write_interval_s for s in sessions))
        phase = "gradient_failed"
        _check_sessions(sessions, painting=True)

        async def paint(s):
            return await s._write_frame(
                SLOT,
                frame,
                "scene gradient: " + label,
                effects={"motionEffect": 0, "brightnessEffect": 0},
                extra=[R.brightness(brightness)],
            )

        results = await _send_wave(sessions, paint, "gradient write")
        exact = all(
            r.get("status") == "applied" and s._slot_value(SLOT) == candidates[s.device_id]
            for s, r in zip(sessions, results)
        )
        if not exact:
            raise ModuleError("The gradient was not confirmed on both modules.")
    except (Exception, asyncio.CancelledError) as error:
        log.warning(
            "scene gradient: %s on %s (%s); restoring the backup",
            phase,
            _ids(sessions),
            error.message if isinstance(error, ModuleError) else type(error).__name__,
            exc_info=None if isinstance(error, ModuleError | asyncio.CancelledError) else error,
        )
        try:
            rolled_back = await asyncio.shield(restore(sessions, data))
        except Exception:  # every failed rollback gets one operator message; the cause is chained
            log.exception("scene gradient: restoring the backup after %s failed on %s", phase, _ids(sessions))
            raise ModuleError(
                "The gradient stopped and restoration needs attention. Its fresh backup is saved; reconnect both and choose Restore previous look."
            ) from error
        if isinstance(error, asyncio.CancelledError):
            raise
        return {**rolled_back, "ok": False, "phase": phase, "rolled_back": rolled_back["ok"]}
    for s in sessions:
        record["modules"][s.device_id]["allowed"] = [copy.deepcopy(candidates[s.device_id])]
    _save(data, record)
    return {
        "ok": True,
        "phase": "applied",
        "gradient": status(data),
        "modules": _state_replies(sessions, results),
        "physical_verified": False,
        "custom_3_written": False,
    }
