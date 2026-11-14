"""Metric-definition transitions use the real scenario fingerprint and SQLite store."""
from contextlib import ExitStack
from pathlib import Path
import hashlib
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare import compare_metric
from metrics import frame_stats
import scenario
from store import Store


class BeforeLaunch(Exception):
    pass


class MetricHistoryTests(unittest.TestCase):
    def fingerprint(self, folder, spec):
        # Exercise the actual runner's metadata path, stopping before any resource
        # sampling, save copying, control connection or process launch.
        run = scenario.Run.__new__(scenario.Run)
        run.config = {'gpu_scenarios_enabled': True}
        run.held = False
        run.copy = folder / 'unused-copy'
        run.out = folder / 'evidence'
        run.spec = spec
        run.meta = {}
        with patch.object(scenario, 'copy_names'), patch.object(scenario, 'resources', side_effect=BeforeLaunch):
            with self.assertRaises(BeforeLaunch):
                run.execute()
        return run.meta

    def test_old_definition_history_is_isolated_and_new_history_accumulates(self):
        spec = {'id': 'synthetic', 'seconds': 60, 'settings': {}, 'steps': []}
        rows = [(0.25, 250), (0.5, 250), (0.75, 250),
                (1.0, 250), (1.75, 750), (2.5, 750)]
        old_fingerprint = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        current_stats = frame_stats(rows)
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as cleanup:
            folder = Path(temporary)
            metadata = self.fingerprint(folder, spec)
            store = Store(folder / 'results.sqlite')
            cleanup.callback(store.db.close)

            def record(fingerprint, count, start):
                return {'id': spec['id'], 'scenario_sha256': fingerprint,
                        'status': 'passed', 'disturbed': False, 'signatures': {},
                        'metrics': {source + '.' + key: {'value': value, 'kind': 'timing', 'direction': direction}
                                    for source in ('game_present', 'renderer_submit')
                                    for key, value, direction in (('worst_second_fps', count, 'lower'),
                                                                 ('worst_second_start_s', start, 'higher'))}}

            old = record(old_fingerprint, 2, 1.5)
            for night in ('n01', 'n02', 'n03'):
                store.begin(night, {'commit': 'same-unchanged-trace', 'started': night})
                store.record(night, old, True)
            current = {**metadata, **record(metadata['scenario_sha256'], current_stats['worst_second_fps'],
                                           current_stats['worst_second_start_s'])}
            # Positive control: mixing the definitions really would be a false regression.
            mixed_history = store.previous('n04', {**current, 'scenario_sha256': old_fingerprint})
            self.assertEqual(len(mixed_history), 3)
            count_key = 'game_present.worst_second_fps'
            past = [{'value': r['metrics'][count_key]['value'], 'disturbed': r['disturbed']} for r in mixed_history]
            self.assertEqual(compare_metric(current['metrics'][count_key]['value'], past,
                                            direction='lower', rerun=current['metrics'][count_key]['value'])['state'],
                             'regression')
            self.assertNotEqual(metadata['scenario_sha256'], old_fingerprint)
            self.assertEqual(metadata['metric_definitions'], {'worst_second': 2})
            history = store.previous('n04', current)
            self.assertEqual(history, [])
            for metric in current['metrics'].values():
                self.assertEqual(compare_metric(metric['value'], history, direction=metric['direction'])['state'],
                                 'baseline-only')
            for night in ('n04', 'n05', 'n06'):
                store.begin(night, {'commit': 'same-unchanged-trace', 'started': night})
                store.record(night, current, True)
            history = store.previous('n07', current)
            self.assertEqual([r['night'] for r in history], ['n04', 'n05', 'n06'])
            self.assertTrue(all(r['metric_definitions'] == {'worst_second': 2} for r in history))
            for key, metric in current['metrics'].items():
                past = [{'value': r['metrics'][key]['value'], 'disturbed': r['disturbed']} for r in history]
                self.assertEqual(compare_metric(metric['value'], past, direction=metric['direction'])['state'], 'within-band')
            store.export_baseline(folder / 'baseline.json')
            buckets = json.loads((folder / 'baseline.json').read_text())['scenarios']
            self.assertEqual(set(buckets), {spec['id'] + ':' + old_fingerprint,
                                           spec['id'] + ':' + metadata['scenario_sha256']})
            self.assertEqual({k: len(v['accepted']) for k, v in buckets.items()}, {k: 3 for k in buckets})
            self.assertEqual(store.db.execute('SELECT count(*) FROM metrics').fetchone()[0], 24)

    def test_fingerprint_still_tracks_scenario_settings_and_is_stable(self):
        spec = {'id': 'synthetic', 'seconds': 60, 'settings': {}, 'steps': []}
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            first = self.fingerprint(folder, spec)['scenario_sha256']
            self.assertEqual(self.fingerprint(folder, dict(reversed(list(spec.items()))))['scenario_sha256'], first)
            changed = {**spec, 'seconds': 61}
            self.assertNotEqual(self.fingerprint(folder, changed)['scenario_sha256'], first)


if __name__ == '__main__':
    unittest.main(verbosity=2)
