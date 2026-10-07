import unittest
from conductor import render
from conductor.drivers.base import StateRow
from conductor.rows import compare


def state(name, **extra):
    rows = {('power', None): 'on', ('color-mode', None): 'sequence',
            ('color-sequence', 'preset'): name, ('brightness', None): 40,
            ('speed', 'color-sequence'): 0}
    rows.update(extra)
    return rows


class PresetRenderTests(unittest.TestCase):
    def test_rgb_presets_start_red_without_hashed_colors(self):
        for name in ('fade-3', 'jump-3'):
            self.assertEqual(render.head_colors(state(name), 30), [{'r': 102, 'g': 0, 'b': 0}] * 30)

    def test_unknown_presets_are_neutral_and_marked_unavailable(self):
        for name in ('unknown-one', 'unknown-two'):
            self.assertFalse(render.sequence_preview(state(name))['available'])
            for color in render.head_colors(state(name), 30):
                self.assertEqual(color['r'], color['g'])
                self.assertEqual(color['g'], color['b'])

    def test_gradient_has_matching_pairs_and_color_progression(self):
        colors = render.head_colors(state('gradient-rainbow'), 60)
        for index in range(0, 60, 2):
            self.assertEqual(colors[index], colors[index + 1])
        self.assertGreater(len({tuple(c.values()) for c in colors}), 20)
        self.assertFalse(render.sequence_preview(state('gradient-rainbow'))['animated'])

    def test_animation_metadata_never_claims_synchronized_measurements(self):
        meta = render.sequence_preview(state('rainbow-road'))
        self.assertTrue(meta['animated'])
        self.assertTrue(meta['approximate'])
        self.assertFalse(meta['synchronized'])

    def test_power_off_stops_sequence_preview(self):
        rows = state('rainbow-road')
        rows[('power', None)] = 'off'
        self.assertIsNone(render.sequence_preview(rows))
        self.assertEqual(render.head_colors(rows, 2), [render.BLACK, render.BLACK])

    def test_same_starting_color_does_not_prove_matching_preset_settings(self):
        desired, reported = state('jump-3'), state('fade-3')
        self.assertEqual(render.head_colors(desired, 30), render.head_colors(reported, 30))
        mismatches = compare([StateRow(*key, value) for key, value in desired.items()],
                             [StateRow(*key, value) for key, value in reported.items()])
        self.assertEqual([m.function_class for m in mismatches], ['color-sequence'])
