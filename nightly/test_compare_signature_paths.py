"""Synthetic absolute POSIX path regression; standard unittest, no private inputs."""
import importlib.util
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

source = Path(__file__).resolve().parent
if '--source-dir' in sys.argv:
    index = sys.argv.index('--source-dir')
    source = Path(sys.argv[index + 1]).resolve()
    del sys.argv[index:index + 2]
sys.path.insert(0, str(source))

_spec = importlib.util.spec_from_file_location('signature_paths_compare', source / 'compare.py')
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)
import metrics
import scenario
from store import Store

_driver_spec = importlib.util.spec_from_file_location('signature_paths_driver', source / 'hs-nightly.py')
driver = importlib.util.module_from_spec(_driver_spec)
_driver_spec.loader.exec_module(driver)


def rooted(line, copy='/synthetic/copy-alpha'):
    return compare.signature(line, roots={'copy': copy})


class SignaturePathTests(unittest.TestCase):
    def test_volatile_absolute_posix_paths_keep_one_signature(self):
        before = rooted('warning: allocation failed at /synthetic/copy-alpha/cache.bin')
        after = rooted('warning: allocation failed at /synthetic/copy-beta/cache.bin', '/synthetic/copy-beta')
        self.assertEqual(before, after)
        self.assertEqual(compare.compare_signatures({after: 1}, [{before: 1}]), [])

    def test_error_words_remain_distinct(self):
        allocation = rooted('warning: allocation failed at /synthetic/copy-alpha/cache.bin')
        descriptor = rooted('warning: descriptor failed at /synthetic/copy-beta/cache.bin', '/synthetic/copy-beta')
        self.assertNotEqual(allocation, descriptor)
        self.assertEqual(compare.compare_signatures({descriptor: 1}, [{allocation: 1}]),
                         [{'signature': descriptor, 'state': 'new-signature', 'count': 1}])

    def test_existing_volatile_fields_keep_normalizing(self):
        before = '\x1b[31mworker 12 warning:\x1b[0m allocation 20.5 MB at 0xabc123 failed'
        after = '  worker 99\twarning: allocation 1 MB at 0x123abc failed  '
        self.assertEqual(compare.signature(before), compare.signature(after))
        self.assertEqual(compare.signature(before), 'worker <n> warning: allocation <n> MB at <addr> failed')

    def test_path_token_preserves_punctuation_and_following_error_words(self):
        line = 'warning: (/synthetic/copy-alpha/cache.bin), descriptor failed.'
        self.assertEqual(rooted(line), 'warning: (<copy>/cache.bin), descriptor failed.')
        self.assertEqual(rooted('warning: /synthetic/cache.bin. descriptor failed', '/synthetic'),
                         'warning: <copy>/cache.bin. descriptor failed')

    def test_quotes_around_space_free_paths_are_preserved(self):
        for quote in ('"', "'"):
            with self.subTest(quote=quote):
                line = f'warning: allocation failed at {quote}/synthetic/copy-alpha/cache.bin{quote}'
                self.assertEqual(rooted(line), f'warning: allocation failed at {quote}<copy>/cache.bin{quote}')

    def test_path_normalization_keeps_count_jump_detection(self):
        before = rooted('warning: allocation failed at /synthetic/copy-alpha/cache.bin')
        after = rooted('warning: allocation failed at /synthetic/copy-beta/cache.bin', '/synthetic/copy-beta')
        histories = [{before: n} for n in (8, 10, 12)]
        self.assertEqual(compare.compare_signatures({after: 12}, histories), [])
        findings = compare.compare_signatures({after: 100}, histories)
        self.assertEqual(len(findings), 1)
        self.assertEqual((findings[0]['signature'], findings[0]['state'], findings[0]['count']),
                         (after, 'count-jump', 100))

    def test_relative_resource_warning_is_new_in_same_format_history(self):
        before = compare.signature('warning: failed to load ./shaders/blit.bin')
        after = compare.signature('warning: failed to load ./textures/hud.bin')
        # Current and all three histories use this same implementation, not old Store keys.
        histories = [{before: 1} for _ in range(3)]
        findings = compare.compare_signatures({after: 1}, histories)
        with self.subTest(check='distinct-relative-signatures'):
            self.assertNotEqual(before, after)
        with self.subTest(check='same-format-new-signature'):
            self.assertEqual(findings,
                             [{'signature': after, 'state': 'new-signature', 'count': 1}])

    def test_neighbor_relative_components_are_not_absolute_suffixes(self):
        for prefix in ('../', 'cache-/', 'cache+/', 'cache@/', 'cache=/'):
            with self.subTest(prefix=prefix):
                first = f'warning: failed to load {prefix}shaders/blit.bin'
                second = f'warning: failed to load {prefix}textures/hud.bin'
                before, after = compare.signature(first), compare.signature(second)
                self.assertEqual(before, first)
                self.assertEqual(after, second)
                self.assertNotEqual(before, after)
                self.assertEqual(compare.compare_signatures({after: 1}, [{before: 1} for _ in range(3)]),
                                 [{'signature': after, 'state': 'new-signature', 'count': 1}])

    def test_only_known_whole_root_prefixes_are_removed(self):
        roots = {'home': '/home/tester', 'copy': '/home/tester/copy alpha',
                 'run': '/home/tester/copy alpha/runs/night-one', 'tmp': '/tmp/owned-one'}
        for path, expected in (
            ('/home/tester/copy alpha/runs/night-one/cache.bin', '<run>/cache.bin'),
            ('/home/tester/copy alpha/cache.bin', '<copy>/cache.bin'),
            ('/home/tester/cache.bin', '<home>/cache.bin'),
            ('/tmp/owned-one/cache.bin', '<tmp>/cache.bin'),
            ('/tmp/owned-one/', '<tmp>/'),
            ('/tmp/owned-one', '<tmp>'),
            ('/tmp/owned-one-other/cache.bin', '/tmp/owned-one-other/cache.bin'),
            ('/tmp/owned-one:other/cache.bin', '/tmp/owned-one:other/cache.bin'),
            ('/tmp/owned-one,other/cache.bin', '/tmp/owned-one,other/cache.bin'),
            ('/tmp/owned-one.more/cache.bin', '/tmp/owned-one.more/cache.bin'),
            ('/home/tester-other/cache.bin', '/home/tester-other/cache.bin'),
            ('/unknown/root/cache.bin', '/unknown/root/cache.bin'),
        ):
            with self.subTest(path=path):
                self.assertEqual(compare.signature('warning: ' + path, roots=roots), 'warning: ' + expected)
        self.assertEqual(rooted('warning: file="/synthetic/copy-alpha/cache.bin"'),
                         'warning: file="<copy>/cache.bin"')

    def test_no_roots_means_no_guessed_path_removal(self):
        for path in ('/synthetic/copy-alpha/cache.bin', '/tmp/random-child/cache.bin',
                     '/home/tester/cache.bin', '/proc/meminfo', '/dev/dri/renderD128'):
            self.assertEqual(compare.signature('warning: ' + path), 'warning: ' + path)

    def test_resource_suffixes_stay_distinct(self):
        for left, right in (
            ('shaders/blit.bin', 'textures/hud.bin'), ('123.bin', '456.bin'),
            ('abcdef12.bin', 'fedcba98.bin'), ('cache#alpha.bin', 'cache#beta.bin'),
            ('café.bin', 'thé.bin'),
        ):
            before = rooted('warning: failed at /synthetic/copy-alpha/' + left)
            after = rooted('warning: failed at /synthetic/copy-alpha/' + right)
            self.assertEqual(before, 'warning: failed at <copy>/' + left)
            self.assertEqual(after, 'warning: failed at <copy>/' + right)
            self.assertEqual(compare.compare_signatures({after: 1}, [{before: 1}] * 3),
                             [{'signature': after, 'state': 'new-signature', 'count': 1}])

    def test_stable_paths_urls_and_windows_tokens_are_unchanged(self):
        for path in (
            '/proc/meminfo', '/proc/cpuinfo', '/dev/udmabuf', '/dev/dri/renderD128',
            'cache+/shaders/blit.bin', 'cache+/textures/hud.bin',
            'cache+/synthetic/copy-alpha/123.bin', './synthetic/copy-alpha/123.bin',
            'https://example.invalid/copy-alpha/cache.bin',
            'https://example.invalid?resource=/synthetic/copy-alpha/cache.bin',
            'https://example.invalid?resource=/synthetic/copy-alpha/123.bin',
            'https://example.invalid?resource="/synthetic/copy-alpha/cache.bin"',
            r'C:\synthetic\copy-alpha\123.bin', 'C:/synthetic/copy-alpha/123.bin',
            r'\\server\synthetic\copy-alpha\123.bin', '//server/synthetic/copy-alpha/123.bin',
        ):
            with self.subTest(path=path):
                self.assertEqual(rooted('warning: ' + path), 'warning: ' + path)

    def test_spaces_unicode_and_hash_in_known_root(self):
        for before_root, after_root in (('/synthetic/copy alpha', '/synthetic/copy beta'),
                                       ('/synthetic/café-alpha', '/synthetic/café-beta'),
                                       ('/synthetic/copy#alpha', '/synthetic/copy#beta')):
            for quote in ('"', "'", ''):
                before = rooted(f'warning: {quote}{before_root}/cache.bin{quote}', before_root)
                after = rooted(f'warning: {quote}{after_root}/cache.bin{quote}', after_root)
                self.assertEqual(before, f'warning: {quote}<copy>/cache.bin{quote}')
                self.assertEqual(before, after)
        self.assertEqual(rooted('warning: "/synthetic/copy-alpha/cache  123.bin"'),
                         'warning: "<copy>/cache  123.bin"')


class SignatureHistoryTests(unittest.TestCase):
    def test_scenario_supplies_actual_roots_and_environment_overrides(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(scenario, 'client_module'), patch.object(scenario, 'Monitor'), \
                    patch.object(scenario, 'stamp', return_value='synthetic'), \
                    patch.dict(os.environ, {'HOME': '/home/inherited', 'TMPDIR': '/tmp/inherited'}):
                for settings, home, tmp in (({}, '/home/inherited', '/tmp/inherited'),
                                            ({'HOME': '/home/override', 'TMPDIR': '/tmp/override'},
                                             '/home/override', '/tmp/override')):
                    run = scenario.Run({}, root / 'copy alpha', {'id': 'fixture', 'settings': settings},
                                       root / 'evidence', 'one')
                    self.assertEqual(run.meta['signature_roots'], {
                        'copy': str(root / 'copy alpha'), 'run': str(root / 'copy alpha/runs/one'),
                        'evidence': str(root / 'evidence'), 'home': home, 'tmp': tmp})
                with patch.dict(os.environ, {'TMPDIR': ''}):
                    run = scenario.Run({}, root / 'copy', {'id': 'fixture'}, root / 'out', 'one')
                    self.assertEqual(run.meta['signature_roots']['tmp'], '/tmp')

    def test_persisted_history_cutover_seeds_then_compares(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / 'history.sqlite'
            store = Store(database)
            old_key = 'warning: failed to load /synthetic/copy-old/cache.bin'
            observations = []
            for index in range(1, 10):
                night = f'n{index:02d}'
                store.begin(night, {'commit': 'synthetic', 'started': 'synthetic'})
                run = root / ('copy-' + night) / 'runs' / night
                run.mkdir(parents=True)
                count = 2 if index == 8 else 1
                resource = 'different.bin' if index == 7 else 'cache.bin'
                (run / 'longshot_log.txt').write_text(
                    (f'warning: failed to load {run}/{resource}\n') * count)
                result = metrics.collect(run, root / 'evidence',
                                         {'signature_roots': {'run': str(run)}})
                self.assertEqual(result['signature_version'], compare.SIGNATURE_VERSION)
                self.assertEqual(result['signatures'], {f'warning: failed to load <run>/{resource}': count})
                result.update(id='fixture', scenario_sha256='unchanged', status='passed', disturbed=False,
                              run_dir=str(run), evidence_dir=str(root / 'evidence'))
                result['metrics']['stable'] = {'value': 10, 'kind': 'counter', 'direction': 'higher'}
                if index <= 3:
                    result['signatures'] = {old_key: 1}
                    result.pop('signature_version')
                    if index == 2:
                        result['signature_version'] = 1  # explicit and unversioned legacy histories
                history = store.previous(night, result)
                findings = driver.comparisons(store, night, result, history)
                accepted = result['status'] == 'passed' and not findings
                store.record(night, result, accepted)
                observations.append((night, accepted, [f['state'] for f in findings],
                                     [h['night'] for h in history]))
                if index == 4:
                    self.assertEqual(result['comparisons']['stable']['state'], 'within-band')
                store.db.close()
                store = Store(database)  # every later comparison reads persisted JSON, not shared objects
            self.assertEqual([accepted for _, accepted, _, _ in observations],
                             [True, True, True, True, True, True, False, False, True])
            self.assertEqual(observations[6][2], ['new-signature'])
            self.assertEqual(observations[7][2], ['count-jump'])
            self.assertNotIn('n07', observations[7][3])
            self.assertNotIn('n08', observations[8][3])
            store.export_baseline(root / 'baseline.json')
            baseline = json.loads((root / 'baseline.json').read_text())['scenarios']['fixture:unchanged']
            self.assertEqual([r['signature_version'] for r in baseline['accepted']],
                             [compare.SIGNATURE_VERSION] * 4 + [1] * 3)
            self.assertEqual(driver.comparisons(store, 'n10', {**result, 'fatal_lines': ['FATAL synthetic']}, [])[0]['kind'],
                             'crash-or-device-fault')
            store.db.close()


if __name__ == '__main__':
    unittest.main()
