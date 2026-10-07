"""Coordinated whole-module actions. Never copies or rewrites saved framebuffers."""

from __future__ import annotations

import asyncio

from conductor import rows as R
from conductor.module import cards as C
from conductor.module.session import ModuleError

PAIR_IDS = ("bench-kit", "shop-string-1")


def validate_action(sessions, action):
    if len(sessions) != 2 or tuple(s.device_id for s in sessions) != PAIR_IDS:
        raise ModuleError("Select the two registered shop modules.", 400)
    if sum(action.get(k) is not None for k in ("look", "power", "brightness")) != 1:
        raise ModuleError("Send exactly one whole-module action.", 400)
    for session in sessions:
        session._check_can_write()
        if session.in_flight:
            raise ModuleError("Wait for the current module command to finish.")
        caps = session.caps
        try:
            if action.get("look") is not None:
                look = C.look_from_json(action["look"])
                if look.kind not in ("white", "rgb", "preset"):
                    raise R.PayloadError("Paired control supports white, RGB and built-in presets only.")
                C.check_look(look, caps)
                R.full_state_vector(look, cct_range=caps.cct_range)
            elif action.get("brightness") is not None:
                v = action["brightness"]
                lo, hi = caps.brightness_range or (1, 100)
                if type(v) is not int or not lo <= v <= hi:
                    raise R.PayloadError("Brightness is outside a module’s supported range.")
            elif type(action["power"]) is not bool:
                raise R.PayloadError("Power must be on or off.")
        except R.PayloadError as e:
            raise ModuleError(str(e), 400) from None


async def _wave(sessions, action):
    results = await asyncio.gather(*(s.whole(**action) for s in sessions), return_exceptions=True)
    out = {}
    for session, result in zip(sessions, results):
        if isinstance(result, BaseException):
            result = {
                "status": "failed",
                "error": result.message
                if isinstance(result, ModuleError)
                else "Module command failed; inspect its readback.",
            }
        out[session.device_id] = {"result": result, "state": session.snapshot()}
    return out


async def apply_pair(sessions, action, *, restart=False, sleep=asyncio.sleep):
    validate_action(sessions, action)
    reset = None
    if restart:
        look = action.get("look") or {}
        if look.get("kind") != "preset":
            raise ModuleError("A coordinated restart needs a built-in preset.", 400)
        dark = {"look": {"kind": "rgb", "rgb": [0, 0, 0], "brightness": look["brightness"]}}
        validate_action(sessions, dark)
        reset = await _wave(sessions, dark)
        if not all(x["result"]["status"] == "applied" for x in reset.values()):
            return {"ok": False, "phase": "reset", "modules": reset, "motion_phase_verified": False}
        # Both resets have completed; let both per-device rate windows clear before the next wave.
        await sleep(max(s.deps.settings.executor.min_write_interval_s for s in sessions))
        validate_action(sessions, action)
    modules = await _wave(sessions, action)
    sent = [x["result"].get("timings", {}).get("sent_at") for x in modules.values()]
    return {
        "ok": all(x["result"]["status"] == "applied" for x in modules.values()),
        "phase": "applied",
        "modules": modules,
        "coordinated_restart": restart,
        "dispatch_skew_ms": round((max(sent) - min(sent)) * 1000, 2)
        if all(isinstance(t, (int, float)) for t in sent)
        else None,
        "motion_phase_verified": False,
    }
