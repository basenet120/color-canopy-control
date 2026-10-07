import asyncio
import copy
import unittest

from conductor import rows as R
from conductor.bench.jobs import JOBS, address_probe_safe, coerce_params, flag_questions, gate_c_safe
from conductor.bench_api import Verdict
from conductor.drivers.base import DeviceInfo, Outcome, ReadResult, StateRow, WriteResult
from conductor.executor_api import Command, CommandResult, CommandStatus
from conductor.gate import Purpose


class Context:
    budget_s = 1
    t1_allowed = False
    expected_heads = 30

    def __init__(self):
        self.artifacts = {}
        self.writes = []
        self.sleeps = []
        self.cancel = False
        self.mismatch = False
        self.state = {
            (R.FRAME_CLASS, s): R.frame_value(None, [R.FrameEntry(*c)] * 60, expected_length=60)
            for s, c in [("custom-1", (255, 30, 140)), ("custom-2", (0, 255, 0)), ("custom-3", (0, 0, 255))]
        }
        self.state[(R.FRAME_CLASS, "custom-1")][R.FRAME_CLASS]["motionEffect"] = 3
        self.state.update(
            {
                ("power", None): "on",
                ("brightness", None): 100,
                ("color-mode", None): "color",
                ("color-individual", "custom"): "custom-2",
                ("color-rgb", None): {"color-rgb": {"r": 255, "g": 155, "b": 196}},
            }
        )
        self.initial = copy.deepcopy(self.state)

    async def profile(self):
        functions = [{"functionClass": k, "functionInstance": i} for k, i in self.state]
        for f in functions:
            if f["functionClass"] == "color-mode":
                f["values"] = [{"name": x} for x in ["individual", "color", "white"]]
            if f["functionClass"] == "color-individual":
                f["values"] = [{"name": x} for x in R.SLOTS]
        return DeviceInfo("kit", "account", functions=functions, states=(await self.read()).rows)

    async def read(self):
        return ReadResult("kit", Outcome.OK, [StateRow(*k, copy.deepcopy(v)) for k, v in self.state.items()])

    async def write(self, rows, **kwargs):
        self.writes.append(copy.deepcopy(rows))
        self.state.update({r.key: copy.deepcopy(r.value) for r in rows})
        if self.cancel and any(r.function_class == R.FRAME_CLASS for r in rows) and len(self.writes) == 3:
            raise asyncio.CancelledError()
        status = CommandStatus.MISMATCH if self.mismatch and len(self.writes) == 3 else CommandStatus.APPLIED
        return CommandResult(
            Command("kit", rows, Purpose.BENCH),
            status,
            attempts=[WriteResult("kit", Outcome.OK, http_status=200)],
        )

    async def sleep(self, n):
        self.sleeps.append(n)

    def step(self, *args, **kwargs):
        pass


def params(**kw):
    return coerce_params(JOBS["gate_c_safe"].spec, kw)


class SafeGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_watched_intervals_are_bounded(self):
        ctx = Context()
        await gate_c_safe(ctx, params(before_duration_s=20, duration_s=90))
        self.assertEqual(ctx.sleeps, [1, 20, 90, 1, 1])
        self.assertEqual(ctx.state, ctx.initial)
        for given in ({"duration_s": 121}, {"before_duration_s": 61}):
            with self.assertRaises(ValueError):
                params(**given)

    async def test_replay_then_restores_current_exact_values_and_never_writes_custom_3(self):
        ctx = Context()
        result = await gate_c_safe(ctx, params())
        self.assertEqual(result.verdict, Verdict.NEEDS_EYES)
        self.assertEqual(ctx.state, ctx.initial)
        self.assertTrue(result.observations["all_slots_unchanged"])
        self.assertEqual(result.flags, {"t1_frames": True})
        self.assertIn(result.ask_human[0], flag_questions("gate_c_safe", params()).values())
        self.assertFalse(any(r.key == (R.FRAME_CLASS, "custom-3") for rows in ctx.writes for r in rows))
        self.assertEqual(ctx.sleeps, [1, 4, 8, 1, 1])

    async def test_cancellation_attempts_exact_restore(self):
        ctx = Context()
        ctx.cancel = True
        with self.assertRaises(asyncio.CancelledError):
            await gate_c_safe(ctx, params())
        self.assertEqual(ctx.state, ctx.initial)

    async def test_replay_mismatch_restores_without_proposing_proof(self):
        ctx = Context()
        ctx.mismatch = True
        result = await gate_c_safe(ctx, params())
        self.assertEqual(result.verdict, Verdict.FAIL)
        self.assertEqual(result.flags, {})
        self.assertEqual(ctx.state, ctx.initial)

    async def test_custom_3_target_refused_and_identical_patterns_send_nothing(self):
        ctx = Context()
        result = await gate_c_safe(ctx, params(source="custom-1", target="custom-3"))
        self.assertEqual(result.verdict, Verdict.ERROR)
        self.assertEqual(ctx.writes, [])
        ctx.state[(R.FRAME_CLASS, "custom-1")] = copy.deepcopy(ctx.state[(R.FRAME_CLASS, "custom-3")])
        self.assertEqual((await gate_c_safe(ctx, params())).verdict, Verdict.ERROR)
        self.assertEqual(ctx.writes, [])


class SafeAddressTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_markers_are_exact_controller_addresses_without_inferred_map(self):
        ctx = Context()
        ctx.t1_allowed = True
        p = coerce_params(JOBS["address_probe_safe"].spec, {"pattern": "markers"})
        result = await address_probe_safe(ctx, p)
        frame = next(r.value for rows in ctx.writes for r in rows if r.key == (R.FRAME_CLASS, "custom-2"))
        entries = R.frame_entries(frame)
        self.assertEqual(
            [(i, e.r, e.g, e.b) for i, e in enumerate(entries) if e.r or e.g or e.b],
            [(0, 255, 0, 0), (1, 0, 255, 0), (29, 0, 0, 255)],
        )
        self.assertEqual(ctx.state, ctx.initial)
        self.assertEqual(result.flags, {})

    async def test_cancel_during_address_hold_restores_exactly(self):
        ctx = Context()
        ctx.t1_allowed = True
        p = coerce_params(JOBS["address_probe_safe"].spec, {})

        async def sleep(n):
            if n == 60:
                raise asyncio.CancelledError()

        ctx.sleep = sleep
        with self.assertRaises(asyncio.CancelledError):
            await address_probe_safe(ctx, p)
        self.assertEqual(ctx.state, ctx.initial)

    async def test_requires_own_frame_proof_and_protects_custom_3(self):
        ctx = Context()
        p = coerce_params(JOBS["address_probe_safe"].spec, {})
        self.assertEqual((await address_probe_safe(ctx, p)).verdict, Verdict.ERROR)
        self.assertEqual(ctx.writes, [])
        ctx.t1_allowed = True
        p["slot"] = "custom-3"
        self.assertEqual((await address_probe_safe(ctx, p)).verdict, Verdict.ERROR)
        self.assertEqual(ctx.writes, [])

    async def test_address_hold_restores_every_slot_and_never_sets_mapping(self):
        ctx = Context()
        ctx.t1_allowed = True
        p = coerce_params(JOBS["address_probe_safe"].spec, {"start": 29, "count": 1, "color": "blue"})
        result = await address_probe_safe(ctx, p)
        self.assertEqual(result.verdict, Verdict.NEEDS_EYES)
        self.assertEqual(result.flags, {})
        self.assertEqual(ctx.state, ctx.initial)
        self.assertIn(60, ctx.sleeps)
        self.assertFalse(any(r.key == (R.FRAME_CLASS, "custom-3") for rows in ctx.writes for r in rows))
