import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from conductor import rows as R
from conductor.capabilities import Capabilities
from conductor.module import scene_gradient as G
from conductor.module.session import ModuleError, ModuleSession


class Session:
    _controller_frame = ModuleSession._controller_frame

    def __init__(self, key):
        self.device_id = key
        self.address = "test-" + key
        self.n = 30
        self.in_flight = 0
        self.verified = True
        self.sweep_state = SimpleNamespace(state="idle")
        self.writes = []
        self.fail = False
        self.caps = Capabilities(
            power=True,
            rgb=True,
            color_modes=["color", "individual"],
            cct_range=(2200, 6500),
            brightness_range=(1, 100),
            slots=list(R.SLOTS),
            framebuffer_lengths={s: 60 for s in R.SLOTS},
        )
        self.deps = SimpleNamespace(
            settings=SimpleNamespace(executor=SimpleNamespace(min_write_interval_s=0))
        )
        self.state = {
            (R.FRAME_CLASS, s): R.frame_value(None, [R.FrameEntry(i, 30, 150)] * 60, expected_length=60)
            for i, s in enumerate(R.SLOTS)
        }
        self.state[(R.FRAME_CLASS, "custom-1")][R.FRAME_CLASS]["motionEffect"] = 3
        self.state.update(
            {
                ("power", None): "on",
                ("brightness", None): 77,
                ("color-mode", None): "color",
                ("color-individual", "custom"): "custom-2",
                ("color-rgb", None): {"color-rgb": {"r": 255, "g": 155, "b": 196}},
                ("speed", "color-sequence"): 4,
                ("toggle", "party-mode"): "on",
            }
        )
        self.original = copy.deepcopy(self.state)

    def _check_can_write(self):
        pass

    def _require_t1(self):
        if not self.verified:
            raise ModuleError("T1 not verified")

    def frame_indices(self):
        return list(range(30))

    async def load(self):
        pass

    def _reported_state(self):
        return self.state

    def _slot_value(self, s):
        return self.state[(R.FRAME_CLASS, s)]

    def snapshot(self):
        return {"device_id": self.device_id}

    async def _send(self, rows, label):
        self.writes.append(copy.deepcopy(rows))
        self.state.update({r.key: copy.deepcopy(r.value) for r in rows})
        # Hardware observation: storing a frame can reselect that slot/mode.
        for r in rows:
            if r.function_class == R.FRAME_CLASS:
                self.state[("color-mode", None)] = "individual"
                self.state[("color-individual", "custom")] = r.function_instance
        return {"status": "applied"}

    async def whole(self, look):
        # What ModuleSession.whole sends for a look: the full state vector (speed 0 and party mode off included).
        return await self._send(R.full_state_vector(R.Look.from_json(look)), "dark")

    async def _write_frame(self, slot, frame, label, effects, extra):
        value = R.frame_value(
            self._slot_value(slot),
            self._controller_frame(frame),
            expected_length=60,
            effects=effects,
            cct_range=self.caps.cct_range,
        )
        out = await self._send(
            [R.slot_write(slot, value), *R.slot_select(slot), R.power(True), *extra], label
        )
        return {"status": "mismatch"} if self.fail else out


class GradientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.sessions = [Session(k) for k in G.PAIR_IDS]
        self.colors = [[255 - i * 8, 0, i * 8] for i in range(30)]

    async def apply(self):
        return await G.apply(self.sessions, self.colors, 30, "Test palette", self.data)

    async def test_second_unverified_module_prevents_every_write(self):
        self.sessions[1].verified = False
        with self.assertRaises(ModuleError):
            await self.apply()
        self.assertTrue(all(not s.writes for s in self.sessions))
        self.assertFalse(G.status(self.data)["pending"])

    async def test_mapped_gradient_and_exact_restore_preserve_current_custom_3(self):
        out = await self.apply()
        self.assertTrue(out["ok"])
        self.assertFalse(out["physical_verified"])
        backup = json.loads(next((self.data / "studio-gradients").glob("backup-*")).read_text())
        for s in self.sessions:
            entries = R.frame_entries(s._slot_value("custom-1"))
            self.assertEqual(len(entries), 60)
            self.assertEqual(entries[:30], [R.FrameEntry(*c, 0, 100, 0) for c in self.colors])
            self.assertTrue(all(e.r == e.g == e.b == 0 for e in entries[30:]))
            self.assertEqual(R.slot_effects(s._slot_value("custom-1"))["motionEffect"], 0)
            self.assertEqual(
                backup["modules"][s.device_id]["original"], s.original[(R.FRAME_CLASS, "custom-1")]
            )
        self.assertTrue((await G.restore(self.sessions, self.data))["ok"])
        for s in self.sessions:
            self.assertEqual(s.state, s.original)
            self.assertFalse(any(r.key == (R.FRAME_CLASS, "custom-3") for rows in s.writes for r in rows))

    async def test_restore_brings_back_the_speed_and_party_mode_the_dark_reset_overwrote(self):
        await self.apply()
        for s in self.sessions:
            self.assertEqual(s.state[("speed", "color-sequence")], 0)
            self.assertEqual(s.state[("toggle", "party-mode")], "off")
        first = self.sessions[0]
        send = first._send

        async def without_speed(rows, label):  # a restore that loses the speed is not reported as exact
            return await send([r for r in rows if r.function_class != "speed"], label)

        first._send = without_speed
        self.assertFalse((await G.restore(self.sessions, self.data))["ok"])
        self.assertTrue(G.status(self.data)["pending"])
        first._send = send
        self.assertTrue((await G.restore(self.sessions, self.data))["ok"])
        self.assertTrue(all(s.state == s.original for s in self.sessions))

    async def test_a_backup_saved_without_speed_and_party_mode_still_restores(self):
        await self.apply()
        path = self.data / "studio-gradients" / "active.json"
        record = json.loads(path.read_text())
        for saved in record["modules"].values():
            saved["controls"] = [
                c for c in saved["controls"] if c["functionClass"] not in ("speed", "toggle")
            ]
        path.write_text(json.dumps(record))
        self.assertTrue((await G.restore(self.sessions, self.data))["ok"])
        self.assertFalse(G.status(self.data)["pending"])
        for s in self.sessions:
            restored = {k: v for k, v in s.state.items() if k[0] not in ("speed", "toggle")}
            self.assertEqual(
                restored, {k: v for k, v in s.original.items() if k[0] not in ("speed", "toggle")}
            )

    async def test_new_scene_retains_first_backup(self):
        await self.apply()
        self.colors.reverse()
        await self.apply()
        self.assertEqual(len(list((self.data / "studio-gradients").glob("backup-*"))), 1)
        await G.restore(self.sessions, self.data)
        self.assertTrue(all(s.state == s.original for s in self.sessions))

    async def test_external_slot_change_is_preserved(self):
        await self.apply()
        s = self.sessions[1]
        s.state[(R.FRAME_CLASS, "custom-1")] = copy.deepcopy(s.original[(R.FRAME_CLASS, "custom-3")])
        count = sum(len(s.writes) for s in self.sessions)
        with self.assertRaises(ModuleError):
            await G.restore(self.sessions, self.data)
        with self.assertRaises(ModuleError):
            await self.apply()
        self.assertEqual(count, sum(len(s.writes) for s in self.sessions))

    async def test_partial_failure_restores_both(self):
        self.sessions[1].fail = True
        result = await self.apply()
        self.assertFalse(result["ok"])
        self.assertTrue(result["rolled_back"])
        self.assertTrue(all(s.state == s.original for s in self.sessions))

    async def test_a_failed_reset_is_logged_and_rolled_back(self):
        async def broken(look):
            raise RuntimeError("boom")

        self.sessions[1].whole = broken
        with self.assertLogs("conductor.module.scene_gradient", "WARNING") as logs:
            result = await self.apply()
        self.assertEqual(
            (result["ok"], result["phase"], result["rolled_back"]), (False, "reset_failed", True)
        )
        self.assertTrue(all(s.state == s.original for s in self.sessions))
        text = "\n".join(logs.output)
        self.assertIn(
            f"ERROR:conductor.module.scene_gradient:scene gradient: dark reset failed on {G.PAIR_IDS[1]}",
            text,
        )
        self.assertIn("RuntimeError: boom", text)
        self.assertIn("reset_failed on bench-kit, shop-string-1 (The dark reset was not confirmed", text)

    async def test_controller_swap_and_invalid_input_send_nothing(self):
        for colors, brightness in [([[1, 2, 3]] * 29, 30), (self.colors, 0), (self.colors, True)]:
            with self.assertRaises(ModuleError):
                await G.apply(self.sessions, colors, brightness, "Test", self.data)
        self.assertTrue(all(not s.writes for s in self.sessions))
        await self.apply()
        self.sessions[1].address = "replacement"
        count = sum(len(s.writes) for s in self.sessions)
        with self.assertRaises(ModuleError):
            await G.restore(self.sessions, self.data)
        self.assertEqual(count, sum(len(s.writes) for s in self.sessions))
