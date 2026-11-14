"""Game-free evidence for bands, noisy-run exclusion, signatures and trace readers.

Run with --mutations to also prove that disabling each of the three comparison guards
makes these same assertions fail. Fixtures contain made-up numbers and words only.
"""
from pathlib import Path
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
path = Path(sys.argv[sys.argv.index('--module') + 1]) if '--module' in sys.argv else Path(__file__).with_name('compare.py')
spec = importlib.util.spec_from_file_location('tested_compare', path)
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)
from metrics import frame_stats, parse_tests, slope
from monitor import driver_faults
from store import Store


class ComparisonTests(unittest.TestCase):
    def test_bands_come_from_variation(self):
        small = compare.band([99, 100, 101])
        wide = compare.band([80, 100, 120])
        self.assertEqual(small['median'], 100)
        self.assertGreater(wide['high'] - 100, (small['high'] - 100) * 10)
        self.assertLess(small['low'], 99)
        self.assertIsNone(compare.band([99, 100]))
        self.assertEqual(compare.band([10, 10, 10])['high'], 10)
        self.assertEqual(compare.band([999] + [10]*7)['median'], 10)
        with self.assertRaises(ValueError):
            compare.band([1, 2, float('nan')])

    def test_disturbed_values_never_establish_or_confirm_timing(self):
        history = [{'value': 10, 'disturbed': False} for _ in range(3)]
        self.assertEqual(compare.compare_metric(50, history, disturbed=True)['state'], 'not-judged')
        self.assertEqual(compare.compare_metric(50, history, rerun=50, rerun_disturbed=True)['state'], 'not-judged')
        polluted = history[:2] + [{'value': 1000, 'disturbed': True}]
        self.assertEqual(compare.compare_metric(50, polluted)['state'], 'baseline-only')
        self.assertEqual(compare.compare_metric(50, history, kind='memory', disturbed=True)['state'], 'not-judged')

    def test_exactly_one_independent_confirmation(self):
        history = [{'value': v} for v in (9, 10, 11)]
        self.assertEqual(compare.compare_metric(50, history)['state'], 'needs-rerun')
        self.assertEqual(compare.compare_metric(50, history, rerun=49)['state'], 'regression')
        self.assertEqual(compare.compare_metric(50, history, rerun=10)['state'], 'unconfirmed')
        self.assertEqual(compare.compare_metric(1, history, direction='lower', rerun=2)['state'], 'regression')
        self.assertEqual(compare.compare_metric(50, history, kind='counter')['state'], 'regression')
        self.assertEqual(compare.compare_metric(1, history)['state'], 'improvement')
        self.assertEqual(compare.compare_metric(50, [])['state'], 'baseline-only')

    def test_signatures_ignore_numbers_not_error_words(self):
        a = 'worker 12 warning: allocation 20.5 MB at 0xabc123 failed'
        b = 'worker 99 warning: allocation 1 MB at 0x123abc failed'
        self.assertEqual(compare.signature(a), compare.signature(b))
        self.assertNotEqual(compare.signature(a), compare.signature(a.replace('allocation', 'descriptor')))
        self.assertEqual(compare.compare_signatures({'x': 40}, []), [])
        self.assertEqual(compare.compare_signatures({'y': 1}, [{'x': 4}])[0]['state'], 'new-signature')
        self.assertEqual(compare.compare_signatures({'x': 100}, [{'x': n} for n in (8, 10, 12)])[0]['state'], 'count-jump')
        self.assertEqual(compare.compare_signatures({'x': 12}, [{'x': n} for n in (8, 10, 12)]), [])

    def test_test_names_not_totals(self):
        old = {'alpha': {'status': 'pass', 'seconds': 2}, 'beta': {'status': 'FAIL', 'seconds': 3}}
        new = {'alpha': {'status': 'FAIL', 'seconds': 3}, 'gamma': {'status': 'pass', 'seconds': 2}}
        result = compare.compare_tests(new, old)
        self.assertEqual(result['new_failures'], ['alpha'])
        self.assertEqual(result['vanished'], ['beta'])
        self.assertTrue(compare.compare_tests(new, {})['first_run'])

    def test_images_learn_tolerance(self):
        history = [[10]*6, [11]*6, [12]*6]
        self.assertEqual(compare.compare_image([11]*6, history)['state'], 'within-band')
        self.assertEqual(compare.compare_image([200]*6, history)['state'], 'regression')
        self.assertEqual(compare.compare_image([200]*6, history[:2])['state'], 'baseline-only')

    def test_frame_definitions(self):
        result = frame_stats([(i / 10, 100) for i in range(1, 21)] + [(2.2, 200)])
        self.assertEqual(result['count'], 21)
        self.assertEqual(result['median_ms'], 100)
        self.assertEqual(result['low_1_fps'], 5)
        self.assertEqual(result['low_0.1_fps'], 5)
        self.assertEqual(result['over_100_count'], 1)
        self.assertEqual(result['over_100_longest_ms'], 200)
        self.assertIsNotNone(result['worst_second_fps'])
        self.assertAlmostEqual(slope([{'monotonic_s': t, 'rss': t*2} for t in (0, 10, 20)], 'rss'), 120)

    def test_suite_requires_complete_counts(self):
        log = '\nresult       time  check\npass     2.0s  alpha\nFAIL     1.0s  beta\nskip           gamma  (missing)\n1 passed, 1 failed, 1 skipped in 3 s\n'
        self.assertEqual(len(parse_tests(log)), 3)
        with self.assertRaises(AssertionError):
            parse_tests(log.replace('1 passed', '2 passed'))

    def test_busy_nights_do_not_evict_quiet_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'results.sqlite')
            for i in range(10):
                night = f'n{i:02d}'
                store.begin(night, {'commit': str(i), 'started': 'made-up time'})
                value = {'id': 'sample', 'scenario_sha256': 'same', 'status': 'passed', 'disturbed': i >= 3,
                         'metrics': {'tick': {'value': 1000 if i >= 3 else 10, 'kind': 'timing', 'direction': 'higher'}},
                         'signatures': {}}
                store.record(night, value, True)
            history = store.previous('n10', value)
            past = [{'value': r['metrics']['tick']['value'], 'disturbed': r['disturbed']} for r in history]
            result = compare.compare_metric(50, past)
            self.assertEqual(result['state'], 'needs-rerun')
            self.assertEqual(result['band']['median'], 10)
            store.export_baseline(Path(folder) / 'baseline.json')
            baseline = json.loads((Path(folder) / 'baseline.json').read_text())['scenarios']['sample:same']
            self.assertEqual(len(baseline['accepted']), 7)
            self.assertEqual(len(baseline['undisturbed']), 3)
            store.db.close()

    def test_driver_fault_policy(self):
        self.assertFalse(driver_faults(['NVRM: memMap_IMPL: CPU mapping not supported for addressSpace: 0x1']))
        self.assertTrue(driver_faults(['NVRM: Xid 1']))
        self.assertTrue(driver_faults(['NVRM: an unknown kind']))
        self.assertFalse(driver_faults(['NVRM: NV_ERR_NO_MEMORY'] * 3))
        self.assertTrue(driver_faults(['NVRM: NV_ERR_NO_MEMORY'] * 12))


def mutations():
    source = path.read_text()
    changes = {
        'historical-spread': ('width = max(3 * 1.4826 * mad, max(abs(x - mid) for x in values))', 'width = 0'),
        'disturbed-run': ("if kind in ('timing', 'memory') and disturbed:", 'if False:'),
        'signature-normalization': ("def signature(line, *, roots=None):",
                                    "def signature(line, *, roots=None):\n    return line"),
    }
    with tempfile.TemporaryDirectory() as temporary:
        for name, (before, after) in changes.items():
            assert source.count(before) == 1
            mutant = Path(temporary) / (name + '.py')
            mutant.write_text(source.replace(before, after, 1))
            p = subprocess.run([sys.executable, '-I', '-B', __file__, '--module', str(mutant)], capture_output=True, text=True)
            assert p.returncode != 0 and 'FAIL:' in p.stderr, (name, p.stdout, p.stderr)
            print('mutation rejected:', name)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ComparisonTests))
    if not result.wasSuccessful():
        sys.exit(1)
    if '--mutations' in sys.argv:
        mutations()
