import asyncio
import unittest

from test_address_probe import Context as ProbeContext

from conductor import rows as R
from conductor.bench.jobs import EFFECT_SWEEP_MAX_CODES, JOBS, coerce_params, effect_sweep
from conductor.bench_api import Verdict
from conductor.executor_api import CommandStatus


def params(**overrides):
    return coerce_params(JOBS["effect_sweep"].spec, overrides)


class Context(ProbeContext):
    def __init__(self):
        super().__init__()
        self.steps, self.t = [], 1000.0

    def now(self):
        return self.t

    async def sleep(self, seconds):
        await super().sleep(seconds)
        self.t += seconds

    def step(self, message, **detail):
        self.steps.append((message, detail))


def frame_of(rows):
    return next((r.value for r in rows if r.function_class == R.FRAME_CLASS), None)


class EffectSweepTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_code_is_sent_once_held_announced_and_the_original_restored(self):
        ctx = Context()
        result = await effect_sweep(ctx, params(first_code=3, last_code=5, hold_s=6.0))
        self.assertEqual(result.verdict, Verdict.NEEDS_EYES)
        self.assertEqual(result.flags, {})
        self.assertFalse(result.observations["automatically_enabled"])
        # before look, static, then (leave, candidate) per code, then the restore
        self.assertEqual(len(ctx.writes), 2 + 2 * 3 + 1)
        candidates = [frame_of(rows) for rows in ctx.writes[3:-1:2]]
        self.assertEqual([f[R.FRAME_CLASS]["motionEffect"] for f in candidates], [3, 4, 5])
        self.assertTrue(all(f[R.FRAME_CLASS]["motionSpeed"] == 50 for f in candidates))
        self.assertTrue(all(f[R.FRAME_CLASS]["brightnessEffect"] == 0 for f in candidates))
        for leave in ctx.writes[2:-1:2]:
            self.assertIn(R.color_mode("white"), leave)
            self.assertIsNone(frame_of(leave))
        self.assertEqual(frame_of(ctx.writes[1])[R.FRAME_CLASS]["motionEffect"], 0)
        self.assertEqual(frame_of(ctx.writes[-1]), ctx.header)
        # every code holds for the full time with no writes, and its step names it with the send time
        self.assertEqual(ctx.sleeps, [3, 6.0, 3, 6.0, 3, 6.0, 3, 6.0])
        announced = [d for _, d in ctx.steps if "code" in d]
        self.assertEqual([d["code"] for d in announced], [3, 4, 5])
        self.assertEqual(
            [c["sent_at"] for c in result.observations["codes"]], [d["sent_at"] for d in announced]
        )

    async def test_without_reselect_codes_rewrite_the_active_slot(self):
        ctx = Context()
        result = await effect_sweep(
            ctx, params(first_code=1, last_code=2, reselect=False, field="brightnessEffect")
        )
        self.assertEqual(result.verdict, Verdict.NEEDS_EYES)
        self.assertEqual(len(ctx.writes), 2 + 2 + 1)
        self.assertEqual(frame_of(ctx.writes[2])[R.FRAME_CLASS]["brightnessEffect"], 1)
        self.assertEqual(frame_of(ctx.writes[3])[R.FRAME_CLASS]["brightnessSpeed"], 50)

    async def test_a_cloud_mismatch_is_noted_and_the_sweep_continues(self):
        ctx = Context()
        write = ctx.write

        async def mismatched(rows, **kwargs):
            result = await write(rows, **kwargs)
            if len(ctx.writes) == 4:  # the first candidate
                result.status = CommandStatus.MISMATCH
            return result

        ctx.write = mismatched
        result = await effect_sweep(ctx, params(first_code=1, last_code=2))
        self.assertEqual(result.verdict, Verdict.NEEDS_EYES)
        codes = result.observations["codes"]
        self.assertIn("problem", codes[0])
        self.assertNotIn("problem", codes[1])
        self.assertEqual(frame_of(ctx.writes[-1]), ctx.header)

    async def test_a_leave_whose_readback_lags_is_noted_and_the_sweep_continues(self):
        ctx = Context()
        write = ctx.write

        async def lagging(rows, **kwargs):
            result = await write(rows, **kwargs)
            if len(ctx.writes) == 3:  # the leave before the first code (seen live: brightness lagged)
                result.status = CommandStatus.MISMATCH
            return result

        ctx.write = lagging
        result = await effect_sweep(ctx, params(first_code=1, last_code=2))
        self.assertEqual(result.verdict, Verdict.NEEDS_EYES)
        codes = result.observations["codes"]
        self.assertEqual([c["code"] for c in codes], [1, 2])
        self.assertIsNotNone(codes[0]["leave_problem"])
        self.assertIsNone(codes[1]["leave_problem"])
        self.assertEqual(frame_of(ctx.writes[-1]), ctx.header)

    async def test_a_failed_leave_stops_the_sweep_and_still_restores(self):
        ctx = Context()
        write = ctx.write

        async def failed(rows, **kwargs):
            result = await write(rows, **kwargs)
            if len(ctx.writes) == 5:  # the leave before the second code
                result.status = CommandStatus.FAILED
            return result

        ctx.write = failed
        result = await effect_sweep(ctx, params(first_code=1, last_code=4))
        self.assertEqual(result.verdict, Verdict.FAIL)
        self.assertEqual(len(result.observations["codes"]), 1)
        self.assertEqual(len(ctx.writes), 6)
        self.assertEqual(frame_of(ctx.writes[-1]), ctx.header)

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
            await effect_sweep(ctx, params())
        self.assertEqual(len(ctx.writes), 5)
        self.assertEqual(ctx.writes[-1][0].value, ctx.header)

    async def test_bad_ranges_and_no_commissioning_proof_send_nothing(self):
        ctx = Context()
        for bad in (
            params(first_code=5, last_code=4),
            params(first_code=1, last_code=EFFECT_SWEEP_MAX_CODES + 1),
            params(field="headerFlags"),
            params(pattern="plaid"),
        ):
            self.assertEqual((await effect_sweep(ctx, bad)).verdict, Verdict.ERROR)
        ctx.t1_allowed = False
        self.assertEqual((await effect_sweep(ctx, params())).verdict, Verdict.ERROR)
        self.assertEqual(ctx.writes, [])

    def test_codes_hold_and_speed_are_bounded(self):
        for values in (
            {"hold_s": 21},
            {"hold_s": float("nan")},
            {"last_code": 256},
            {"first_code": 0},
            {"speed": 101},
        ):
            with self.assertRaises(ValueError):
                params(**values)


class EffectSpeedSweepTests(unittest.IsolatedAsyncioTestCase):
    async def test_vary_speed_holds_the_code_and_walks_the_speeds(self):
        ctx = Context()
        result = await effect_sweep(ctx, params(vary="speed", first_code=4, speeds="10, 55,100", hold_s=5.0))
        self.assertEqual(result.verdict, Verdict.NEEDS_EYES)
        candidates = [frame_of(rows)[R.FRAME_CLASS] for rows in ctx.writes[3:-1:2]]
        self.assertEqual(
            [(f["motionEffect"], f["motionSpeed"]) for f in candidates], [(4, 10), (4, 55), (4, 100)]
        )
        self.assertEqual(
            [(c["code"], c["speed"]) for c in result.observations["codes"]], [(4, 10), (4, 55), (4, 100)]
        )
        self.assertEqual([d["speed"] for _, d in ctx.steps if "speed" in d], [10, 55, 100])
        self.assertIn("speeds 10, 55, 100", result.summary)
        self.assertEqual(frame_of(ctx.writes[-1]), ctx.header)

    async def test_vary_speed_ignores_last_code_and_refuses_bad_speed_lists(self):
        ctx = Context()
        ok = await effect_sweep(ctx, params(vary="speed", first_code=20, last_code=3, speeds="50"))
        self.assertEqual(ok.verdict, Verdict.NEEDS_EYES)
        sent = len(ctx.writes)
        for speeds in ("", "fast", "10,101", ",".join(["5"] * (EFFECT_SWEEP_MAX_CODES + 1))):
            self.assertEqual(
                (await effect_sweep(ctx, params(vary="speed", speeds=speeds))).verdict, Verdict.ERROR
            )
        self.assertEqual((await effect_sweep(ctx, params(vary="sideways"))).verdict, Verdict.ERROR)
        self.assertEqual(len(ctx.writes), sent)

    async def test_band_is_light_show_4s_six_colours_then_dark(self):
        ctx = Context()
        result = await effect_sweep(ctx, params(pattern="band", vary="speed", speeds="50"))
        self.assertEqual(result.verdict, Verdict.NEEDS_EYES)
        frame = R.frame_entries(frame_of(ctx.writes[1]))
        self.assertEqual(frame[0], R.FrameEntry(255, 23, 68))
        self.assertEqual(frame[5], R.FrameEntry(170, 0, 255))
        self.assertTrue(all(e.colorBrightness == 0 for e in frame[6:]))
