import copy
import unittest

from conductor import rows as R


class EffectReadbackTests(unittest.TestCase):
    def setUp(self):
        self.value = R.frame_value(
            None,
            [R.FrameEntry(255, 0, 0)] * 60,
            expected_length=60,
            effects={"motionEffect": 1, "motionSpeed": 30},
        )

    def compare(self, got):
        return R.compare([R.slot_write("custom-3", self.value)], [R.slot_write("custom-3", got)])

    def test_each_changed_effect_field_is_a_mismatch_with_identical_colors(self):
        for field in R.EFFECT_FIELDS:
            got = copy.deepcopy(self.value)
            got[R.FRAME_CLASS][field] += 1
            self.assertEqual(R.slot_hash(got), R.slot_hash(self.value))
            self.assertEqual(len(self.compare(got)), 1, field)

    def test_missing_effect_field_cannot_confirm_full_write(self):
        got = copy.deepcopy(self.value)
        del got[R.FRAME_CLASS]["motionEffect"]
        self.assertEqual(len(self.compare(got)), 1)

    def test_matching_full_frame_passes(self):
        self.assertEqual(self.compare(copy.deepcopy(self.value)), [])

    def test_hash_only_expectation_still_checks_only_recorded_colors(self):
        got = copy.deepcopy(self.value)
        got[R.FRAME_CLASS]["motionEffect"] = 7
        self.assertEqual(
            R.compare(
                [R.slot_hash_expectation("custom-3", R.slot_hash(self.value))],
                [R.slot_write("custom-3", got)],
            ),
            [],
        )

    def test_color_and_effect_mismatch_not_duplicated(self):
        got = R.frame_value(None, [R.FrameEntry(0, 255, 0)] * 60, expected_length=60)
        self.assertEqual(len(self.compare(got)), 1)
