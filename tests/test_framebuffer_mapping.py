import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from conductor import rows as R
from conductor.capabilities import Capabilities
from conductor.config import DeviceEntry, FramebufferMapping
from conductor.drivers.base import StateRow
from conductor.module.session import ModuleError, ModuleSession


def make_session(indices=None):
    entry = DeviceEntry(
        id="test-kit",
        role="bench",
        account="test",
        expected_heads=30,
        flags={"t1_frames": True},
        flags_proven_on="kit-a",
        framebuffer_mapping={"frame_length": 60, "indices": indices or list(range(30)), "proven_on": "kit-a"},
    )
    session = object.__new__(ModuleSession)
    session.device_id, session.address = "test-kit", "kit-a"
    session.target = SimpleNamespace(address="kit-a", simulated=False)
    session.positions = [(i, 0) for i in range(30)]
    session.deps = SimpleNamespace(entry=lambda _: entry)
    session.caps = Capabilities(
        color_modes=["individual"], slots=["custom-3"], framebuffer_lengths={"custom-3": 60}
    )
    header = R.frame_value(None, [R.FrameEntry(0, 0, 255)] * 60, expected_length=60)
    session.reported_rows = [StateRow(R.FRAME_CLASS, "custom-3", header)]
    session.written_slots = set()
    session._check_can_write = lambda: None
    return session, entry


class FramebufferMappingTests(unittest.IsolatedAsyncioTestCase):
    async def test_paint_expands_to_full_wire_frame_and_load_returns_30_heads(self):
        session, _ = make_session()
        sent = []

        async def send(rows, label):
            sent.extend(rows)
            return {"ok": True}

        session._send = send
        paint = [R.FrameEntry(255, 0, i) for i in range(30)]
        await session._write_frame("custom-3", paint, "test")
        value = next(r.value for r in sent if r.function_class == R.FRAME_CLASS)
        entries = R.frame_entries(value)
        self.assertEqual(entries[:30], paint)
        self.assertEqual(entries[30:], [R.FrameEntry(0, 0, 0, 0, 0, 0)] * 30)
        session.reported_rows = [StateRow(R.FRAME_CLASS, "custom-3", value)]
        self.assertEqual(session._slot_entries("custom-3"), paint)
        self.assertEqual(session.paint, paint)
        self.assertTrue(session.t1_status()[0])

    def test_nontrivial_mapping_used_by_writes_load_and_preview(self):
        session, _ = make_session(list(range(29, -1, -1)))
        paint = [R.FrameEntry(0, 0, 0, 0, 0, 0)] * 30
        paint[0] = R.FrameEntry(255, 0, 0)
        wire = session._controller_frame(paint)
        self.assertEqual(wire[29], paint[0])
        self.assertEqual(wire[0].colorBrightness, 0)
        value = R.frame_value(None, wire, expected_length=60)
        state = {
            ("power", None): "on",
            ("color-mode", None): "individual",
            ("color-individual", "custom"): "custom-3",
            (R.FRAME_CLASS, "custom-3"): value,
        }
        colors = session._head_colors(state)
        self.assertEqual(colors[0], {"r": 255, "g": 0, "b": 0})
        self.assertEqual(colors[29], {"r": 0, "g": 0, "b": 0})

    def test_another_device_or_changed_frame_cannot_reuse_mapping(self):
        session, _ = make_session()
        session.address = "different-kit"
        self.assertFalse(session.t1_status()[0])
        session.address = "kit-a"
        session.caps.framebuffer_lengths = {"custom-3": 30}
        self.assertFalse(session.t1_status()[0])

    async def test_mapping_does_not_replace_gate_c_proof(self):
        session, entry = make_session()
        entry.flags.t1_frames = None
        with self.assertRaises(ModuleError):
            await session._write_frame("custom-3", [R.FrameEntry()] * 30, "refused")

    def test_no_mapping_keeps_existing_mismatch_guard(self):
        session, entry = make_session()
        entry.framebuffer_mapping = None
        self.assertFalse(session.t1_status()[0])
        session.caps.framebuffer_lengths = {"custom-3": 30}
        self.assertTrue(session.t1_status()[0])

    def test_malformed_mapping_rejected_on_config_load(self):
        for indices in ([0, 0], [-1], [60], [1.5], [True]):
            with self.assertRaises(ValidationError):
                FramebufferMapping(frame_length=60, indices=indices, proven_on="kit-a")

    def test_inconsistent_slots_and_wrong_head_count_remain_blocked(self):
        session, entry = make_session()
        session.caps.framebuffer_lengths["custom-1"] = 30
        self.assertFalse(session.t1_status()[0])
        session.caps.framebuffer_lengths = {"custom-3": 60}
        entry.expected_heads = 20
        self.assertFalse(session.t1_status()[0])
