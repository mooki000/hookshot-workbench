"""Exact, game-free checks of complete rolling-second completion windows.

Run directly or with unittest discovery. Dyadic grids and received finite floats:
the independent oracle enumerates all change points and interval midpoints using
rational arithmetic, then counts each window from scratch.
"""
from fractions import Fraction
from itertools import combinations_with_replacement
from pathlib import Path
import math
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import frame_stats


def exact_windows(rows):
    """Return (start, count) for every boundary and constant-count interval."""
    times = [Fraction(t) for t, _ in rows]
    first = times[0] - Fraction(rows[0][1]) / 1000
    last_start = times[-1] - 1
    if last_start < first:
        return []
    bounds = {first, last_start}
    bounds.update(t for t in times if first <= t <= last_start)
    bounds.update(t - 1 for t in times if first <= t - 1 <= last_start)
    ordered = sorted(bounds)
    starts = ordered + [(a + b) / 2 for a, b in zip(ordered, ordered[1:])]
    return [(s, sum(s < t <= s + 1 for t in times)) for s in starts]


class WorstSecondTests(unittest.TestCase):
    def assert_worst(self, rows, expected):
        windows = exact_windows(rows)
        self.assertTrue(windows)
        self.assertEqual(min(count for _, count in windows), expected)
        result = frame_stats(rows)
        self.assertEqual(result['worst_second_fps'], expected)
        start = Fraction(result['worst_second_start_s'])
        first = Fraction(rows[0][0]) - Fraction(rows[0][1]) / 1000
        self.assertGreaterEqual(start, first)
        self.assertLessEqual(start + 1, Fraction(rows[-1][0]))
        self.assertEqual(sum(start < Fraction(t) <= start + 1 for t, _ in rows), expected)
        return result

    def test_interior_window_minimum(self):
        rows = [(0.25, 250), (0.5, 250), (0.75, 250),
                (1.0, 250), (1.75, 750), (2.5, 750)]
        self.assertEqual(sum(Fraction(9, 8) < Fraction(t) <= Fraction(17, 8)
                             for t, _ in rows), 1)
        self.assert_worst(rows, 1)

    def test_exact_second_includes_right_completion(self):
        self.assert_worst([(1.0, 1000)], 1)

    def test_stall_strictly_over_second_has_empty_full_window(self):
        self.assert_worst([(1.25, 1250)], 0)

    def test_empty_trace_stays_empty(self):
        self.assertEqual(frame_stats([]), {})

    def test_short_trace_has_no_complete_second(self):
        rows = [(0.25, 250), (0.5, 250), (0.75, 250)]
        self.assertEqual(exact_windows(rows), [])
        result = frame_stats(rows)
        self.assertIsNone(result['worst_second_fps'])
        self.assertIsNone(result['worst_second_start_s'])

    def test_duplicate_completions_and_exact_edges(self):
        self.assert_worst([(1.0, 1000), (1.0, 1000),
                           (2.0, 1000), (2.0, 1000)], 2)

    def test_first_complete_window_is_eligible(self):
        self.assert_worst([(0.75, 750), (1.0, 250), (1.25, 250),
                           (1.5, 250), (1.75, 250), (2.0, 250)], 2)

    def test_last_complete_window_is_eligible(self):
        self.assert_worst([(0.25, 250), (0.5, 250), (0.75, 250),
                           (1.0, 250), (2.0, 1000)], 1)

    def test_negative_time_origin_does_not_change_minimum(self):
        self.assert_worst([(-3.75, 250), (-3.5, 250), (-3.25, 250),
                           (-3.0, 250), (-2.25, 750), (-1.5, 750)], 1)

    def test_small_rational_grid_matches_exact_window_oracle(self):
        # Exhaust all nondecreasing traces of one to four quarter-second ticks.
        # Duplicate completions remain eligible because every recorded ms is positive.
        for length in range(1, 5):
            for ticks in combinations_with_replacement(range(1, 9), length):
                rows = [(Fraction(t, 4), max(1, t - (ticks[i - 1] if i else 0)) * 250)
                        for i, t in enumerate(ticks)]
                with self.subTest(ticks=ticks):
                    windows = exact_windows(rows)
                    if windows:
                        self.assert_worst(rows, min(count for _, count in windows))
                    else:
                        result = frame_stats(rows)
                        self.assertIsNone(result['worst_second_fps'])
                        self.assertIsNone(result['worst_second_start_s'])


    def test_decimal_float_noninitial_boundary_is_exact(self):
        # The oracle uses the actual binary floats received, not decimal tenths.
        rows = [(t, 100.0) for t in
                (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1)]
        self.assertEqual(Fraction(rows[-1][0]) - (Fraction(rows[0][0]) + 1),
                         Fraction(3, 36028797018963968))
        # Any minimizing location is accepted, provided it is in support and
        # independently recounts to nine under the same exact-input model.
        self.assert_worst(rows, 9)


    def test_negative_decimal_origin_recounts_exactly(self):
        rows = [(t, 100.0) for t in
                (-0.9, -0.8, -0.7, -0.6, -0.5, -0.4,
                 -0.3, -0.2, -0.1, 0.0, 0.1)]
        self.assert_worst(rows, 9)


    def test_decimal_domain_rejects_rounded_incomplete_start(self):
        # float(-0.2) - 1 rounds up to float(-1.2), but that start is too late.
        rows = [(-1.2, 250.0), (-1.2, 250.0), (-0.2, 1000.0)]
        self.assertGreater(Fraction(rows[0][0]) + 1, Fraction(rows[-1][0]))
        self.assert_worst(rows, 2)

    def test_first_window_count_precedes_location_rounding(self):
        rows = [(4.0, math.nextafter(1000.0, math.inf))]
        windows = exact_windows(rows)
        self.assertEqual(min(count for _, count in windows), 0)
        self.assertEqual(frame_stats(rows)['worst_second_fps'], 0)
        # The only optimal starts are not representable floats. The count, not
        # an exact recount at the rounded diagnostic location, is the contract.
        first = Fraction(4) - Fraction(rows[0][1]) / 1000
        self.assertLess(first + 1, 4)
        self.assertEqual(float(first), 3.0)

    def test_initial_support_below_float_range_keeps_exact_count(self):
        largest = math.nextafter(math.inf, 0.0)
        rows = [(-largest, largest)]
        self.assertEqual(min(count for _, count in exact_windows(rows)), 0)
        result = frame_stats(rows)
        self.assertEqual(result['worst_second_fps'], 0)
        self.assertEqual(result['worst_second_start_s'], -largest)

    def test_first_boundary_neighbors_match_exact_oracle(self):
        for origin in (-1024.0, -4.0, -0.9, -0.3, 0.0, 0.1, 0.3, 4.0, 1024.0):
            for ms in (math.nextafter(1000.0, -math.inf), 1000.0,
                       math.nextafter(1000.0, math.inf)):
                for times in ((origin,), (origin, origin),
                              (origin, math.nextafter(origin, math.inf)),
                              (origin, origin, origin + 0.5)):
                    rows = [(t, ms if i == 0 else 1.0) for i, t in enumerate(times)]
                    with self.subTest(rows=rows):
                        windows = exact_windows(rows)
                        expected = min((count for _, count in windows), default=None)
                        self.assertEqual(frame_stats(rows)['worst_second_fps'], expected)

    def test_wide_exponents_and_subnormals_match_exact_oracle(self):
        tiny = math.nextafter(0.0, 1.0)
        for times in ((tiny, 1.0, 2.0), (-1e300, tiny, 1e300),
                      (-tiny, 0.0, tiny), (2.0**52, 2.0**52 + 1)):
            for ms in (tiny, 1000.0, math.nextafter(1000.0, math.inf)):
                rows = [(t, ms if i == 0 else 1.0) for i, t in enumerate(times)]
                with self.subTest(rows=rows):
                    windows = exact_windows(rows)
                    expected = min((count for _, count in windows), default=None)
                    self.assertEqual(frame_stats(rows)['worst_second_fps'], expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
