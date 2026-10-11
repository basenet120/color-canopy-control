import asyncio
import unittest
from types import SimpleNamespace

from conductor.capabilities import Capabilities
from conductor.module.pair import PAIR_IDS, apply_pair
from conductor.module.session import ModuleError


class Session:
    def __init__(self, key, log, status="applied"):
        self.device_id = key
        self.log = log
        self.status = status
        self.error = None
        self.in_flight = 0
        self.held = False
        self.caps = Capabilities(
            power=True,
            rgb=True,
            color_modes=["color", "white", "sequence"],
            cct_range=(2200, 6500),
            brightness_range=(1, 100),
            speed_range=(-10, 10),
            presets=["rainbow-road"],
        )
        self.deps = SimpleNamespace(
            settings=SimpleNamespace(executor=SimpleNamespace(min_write_interval_s=0))
        )

    def _check_can_write(self):
        if self.held:
            raise ModuleError("held")

    async def whole(self, **action):
        self.log.append((self.device_id, action))
        await asyncio.sleep(0)
        if self.error is not None:
            raise self.error
        return {
            "status": self.status,
            "timings": {"sent_at": 100 if self.device_id == PAIR_IDS[0] else 100.005},
        }

    def snapshot(self):
        return {"device_id": self.device_id}


class PairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.log = []
        self.sessions = [Session(k, self.log) for k in PAIR_IDS]
        self.look = {"look": {"kind": "preset", "preset": "rainbow-road", "brightness": 30, "speed": 0}}

    async def test_validation_of_second_module_prevents_all_writes(self):
        self.sessions[1].caps.presets = []
        with self.assertRaises(ModuleError):
            await apply_pair(self.sessions, self.look)
        self.assertEqual(self.log, [])

    async def test_hold_prevents_all_writes(self):
        self.sessions[1].held = True
        with self.assertRaises(ModuleError):
            await apply_pair(self.sessions, {"power": True})
        self.assertEqual(self.log, [])

    async def test_partial_failure_is_not_claimed_as_success(self):
        self.sessions[1].status = "mismatch"
        out = await apply_pair(self.sessions, {"brightness": 30})
        self.assertFalse(out["ok"])
        self.assertEqual(out["modules"][PAIR_IDS[0]]["result"]["status"], "applied")

    async def test_reset_then_shared_preset_and_no_slot_writes(self):
        out = await apply_pair(self.sessions, self.look, restart=True)
        self.assertTrue(out["ok"])
        self.assertFalse(out["motion_phase_verified"])
        self.assertEqual(out["dispatch_skew_ms"], 5)
        self.assertEqual([a["look"]["kind"] for _, a in self.log], ["rgb", "rgb", "preset", "preset"])
        self.assertNotIn("slot", str(self.log))

    async def test_failed_reset_does_not_start_preset(self):
        self.sessions[1].status = "failed"
        out = await apply_pair(self.sessions, self.look, restart=True)
        self.assertFalse(out["ok"])
        self.assertEqual(out["phase"], "reset")
        self.assertEqual(len(self.log), 2)

    async def test_restart_without_a_brightness_resets_at_the_level_the_preset_will_use(self):
        look = {"look": {"kind": "preset", "preset": "rainbow-road"}}
        out = await apply_pair(self.sessions, look, restart=True)
        self.assertTrue(out["ok"])
        self.assertEqual([a["look"].get("brightness") for _, a in self.log], [100, 100, None, None])

    async def test_a_failed_module_command_is_logged_with_its_device_and_phase(self):
        self.sessions[1].error = RuntimeError("boom")
        with self.assertLogs("conductor.module.pair", "ERROR") as logs:
            out = await apply_pair(self.sessions, {"power": True})
        self.assertFalse(out["ok"])
        self.assertEqual(
            out["modules"][PAIR_IDS[1]]["result"]["error"], "Module command failed; inspect its readback."
        )
        self.assertIn(f"whole-module action failed on {PAIR_IDS[1]}", logs.output[0])
        self.assertIn("RuntimeError: boom", logs.output[0])

    async def test_saved_slots_and_ambiguous_actions_are_refused(self):
        for action in [{"look": {"kind": "slot", "slot": "custom-3"}}, {"brightness": 30, "power": True}]:
            with self.assertRaises(ModuleError):
                await apply_pair(self.sessions, action)
        self.assertEqual(self.log, [])


if __name__ == "__main__":
    unittest.main()
