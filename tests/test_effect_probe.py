import asyncio
import unittest

from test_address_probe import Context

from conductor import rows as R
from conductor.bench.jobs import JOBS, coerce_params, effect_probe
from conductor.bench_api import Verdict
from conductor.executor_api import CommandStatus


def params(**overrides):
    return coerce_params(JOBS["effect_probe"].spec, overrides)


class EffectProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_candidate_bounded_hold_and_exact_original_restore(self):
        ctx = Context()
        result = await effect_probe(ctx, params())
        self.assertEqual(result.verdict, Verdict.NEEDS_EYES)
        self.assertEqual(result.flags, {})
        self.assertFalse(result.observations["automatically_enabled"])
        self.assertEqual(ctx.sleeps, [3, 3, 3, 15])
        self.assertIn(R.color_mode("white"), ctx.writes[2])
        self.assertIn(R.color_mode("individual"), ctx.writes[3])
        self.assertTrue(result.observations["reselected_from_whole_strand"])
        frames = [
            next(r.value for r in rows if r.function_class == R.FRAME_CLASS)
            for rows in ctx.writes[1:]
            if any(r.function_class == R.FRAME_CLASS for r in rows)
        ]
        self.assertEqual(frames[0][R.FRAME_CLASS]["motionEffect"], 0)
        self.assertEqual(frames[1][R.FRAME_CLASS]["motionEffect"], 1)
        self.assertEqual(frames[1][R.FRAME_CLASS]["brightnessEffect"], 0)
        self.assertEqual(frames[-1], ctx.header)

    async def test_failed_candidate_still_restores(self):
        ctx = Context()
        write = ctx.write

        async def failed(rows, **kwargs):
            result = await write(rows, **kwargs)
            if len(ctx.writes) == 4:
                result.status = CommandStatus.MISMATCH
            return result

        ctx.write = failed
        result = await effect_probe(ctx, params(field="brightnessEffect", code=1))
        self.assertEqual(result.verdict, Verdict.FAIL)
        self.assertEqual(result.observations["restore"]["status"], "applied")
        self.assertEqual(len(ctx.writes), 5)

    async def test_blocks_have_clear_dark_gaps_and_repeat_across_full_buffer(self):
        ctx = Context()
        result = await effect_probe(ctx, params(pattern="blocks"))
        self.assertEqual(result.verdict, Verdict.NEEDS_EYES)
        frame = R.frame_entries(ctx.writes[3][0].value)
        self.assertEqual(frame[:30], frame[30:])
        self.assertEqual(frame[:3], [R.FrameEntry(255, 0, 0)] * 3)
        self.assertTrue(all(e.colorBrightness == 0 for e in frame[3:10]))
        self.assertEqual(frame[10:13], [R.FrameEntry(0, 255, 0)] * 3)

    async def test_cancellation_attempts_restore_before_leaving(self):
        ctx = Context()
        write = ctx.write

        async def cancelled(rows, **kwargs):
            result = await write(rows, **kwargs)
            if len(ctx.writes) == 4:
                raise asyncio.CancelledError()
            return result

        ctx.write = cancelled
        with self.assertRaises(asyncio.CancelledError):
            await effect_probe(ctx, params())
        self.assertEqual(len(ctx.writes), 5)
        self.assertEqual(ctx.writes[-1][0].value, ctx.header)

    async def test_no_commissioning_proof_or_unknown_field_sends_nothing(self):
        ctx = Context()
        ctx.t1_allowed = False
        self.assertEqual((await effect_probe(ctx, params())).verdict, Verdict.ERROR)
        ctx.t1_allowed = True
        self.assertEqual((await effect_probe(ctx, params(field="headerFlags"))).verdict, Verdict.ERROR)
        self.assertEqual(ctx.writes, [])

    def test_duration_and_codes_are_bounded(self):
        for values in ({"duration_s": 21}, {"duration_s": float("nan")}, {"code": 256}, {"speed": -1}):
            with self.assertRaises(ValueError):
                params(**values)
