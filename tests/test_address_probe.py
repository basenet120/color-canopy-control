"""Address diagnostics retain controller length and require actual commissioning proof."""

import copy
import unittest

from conductor import rows as R
from conductor.bench.jobs import JOBS, address_probe, coerce_params
from conductor.bench_api import Verdict
from conductor.drivers.base import DeviceInfo, Outcome, ReadResult, StateRow, WriteResult
from conductor.executor_api import Command, CommandResult, CommandStatus
from conductor.gate import Purpose


class Context:
    t1_allowed = True
    budget_s = 3
    expected_heads = 30

    def __init__(self):
        self.artifacts, self.writes, self.sleeps = {}, [], []
        self.fail_before = False
        self.header = R.frame_value(None, [R.FrameEntry(0, 0, 255)] * 60, expected_length=60)
        self.header[R.FRAME_CLASS]["id"] = 37
        self.header[R.FRAME_CLASS]["frameBuffer"]["flags"] = 9
        self.rows = [StateRow(R.FRAME_CLASS, "custom-3", self.header)]
        self.functions = [
            {"functionClass": "color-mode", "values": [{"name": "individual"}, {"name": "white"}]},
            {
                "functionClass": "color-individual",
                "functionInstance": "custom",
                "values": [{"name": "custom-3"}],
            },
            {"functionClass": "color-temperature", "range": {"min": 2200, "max": 6500}},
            {"functionClass": R.FRAME_CLASS, "functionInstance": "custom-3"},
            {"functionClass": "power"},
            {"functionClass": "brightness"},
        ]

    async def profile(self):
        return DeviceInfo("test-device", "test-account", functions=self.functions, states=self.rows)

    async def read(self):
        return ReadResult("test-device", Outcome.OK, self.rows)

    async def write(self, rows, **kwargs):
        self.writes.append(rows)
        return CommandResult(
            Command("test-device", rows, Purpose.BENCH),
            CommandStatus.FAILED if self.fail_before else CommandStatus.APPLIED,
            attempts=[WriteResult("test-device", Outcome.OK, http_status=200)],
        )

    async def sleep(self, seconds):
        self.sleeps.append(seconds)

    def step(self, *args, **kwargs):
        pass


def params(**values):
    return coerce_params(JOBS["address_probe"].spec, values)


class AddressProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_full_frame_header_and_backup_despite_30_head_map(self):
        ctx = Context()
        original = copy.deepcopy(ctx.header)
        result = await address_probe(ctx, params(start=30, count=1, color="green"))
        self.assertEqual(result.verdict, Verdict.NEEDS_EYES)
        self.assertEqual(result.flags, {})
        frame = next(r.value for r in ctx.writes[1] if r.function_class == R.FRAME_CLASS)
        entries = R.frame_entries(frame)
        self.assertEqual(len(entries), 60)
        self.assertEqual(entries[30], R.FrameEntry(0, 255, 0, 0, 100, 0))
        self.assertTrue(
            all(e.colorBrightness == e.whiteBrightness == 0 for i, e in enumerate(entries) if i != 30)
        )
        self.assertEqual(frame[R.FRAME_CLASS]["id"], 37)
        self.assertEqual(frame[R.FRAME_CLASS]["frameBuffer"]["flags"], 9)
        self.assertEqual(ctx.artifacts["address_probe_before"]["value"], original)
        self.assertEqual(ctx.header, original)
        self.assertGreaterEqual(ctx.sleeps[0], ctx.budget_s)
        self.assertIn(R.brightness(40), ctx.writes[1])

    async def test_unproven_device_never_reads_or_writes(self):
        ctx = Context()
        ctx.t1_allowed = False
        result = await address_probe(ctx, params())
        self.assertEqual(result.verdict, Verdict.ERROR)
        self.assertEqual(ctx.writes, [])
        self.assertEqual(ctx.artifacts, {})

    async def test_out_of_frame_range_refused_before_any_write(self):
        for values in ({"start": 60}, {"start": 59, "count": 2}):
            ctx = Context()
            result = await address_probe(ctx, params(**values))
            self.assertEqual(result.verdict, Verdict.ERROR)
            self.assertEqual(ctx.writes, [])

    async def test_animated_slot_is_not_silently_changed(self):
        ctx = Context()
        ctx.header[R.FRAME_CLASS]["motionEffect"] = 3
        result = await address_probe(ctx, params())
        self.assertEqual(result.verdict, Verdict.ERROR)
        self.assertEqual(ctx.writes, [])

    async def test_missing_select_declaration_prevents_any_write(self):
        ctx = Context()
        ctx.functions[1]["functionInstance"] = "unexpected"
        result = await address_probe(ctx, params())
        self.assertEqual(result.verdict, Verdict.ERROR)
        self.assertEqual(ctx.writes, [])

    async def test_failed_before_look_stops_probe(self):
        ctx = Context()
        ctx.fail_before = True
        result = await address_probe(ctx, params())
        self.assertEqual(result.verdict, Verdict.FAIL)
        self.assertEqual(len(ctx.writes), 1)

    def test_invalid_parameters_refused(self):
        for values in ({"start": -1}, {"count": 0}, {"brightness": 101}, {"start": 0.5}):
            with self.assertRaises(ValueError):
                params(**values)
