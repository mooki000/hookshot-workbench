#!/usr/bin/env python3
"""Made-up histories, synthetic logs and a stand-in run adapter: no game, no GPU, no network."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


r = load('regress')
runner = load('run_case')
SCENARIO = {'schema_version': 1, 'kind': 'timing', 'seconds': 60, 'repetitions': 2, 'max_retries': 1, 'max_load': 8,
            'measure': {'name': 'ms', 'direction': 'higher-worse', 'good_max': 10, 'bad_min': 15},
            'build_command': ['builder'], 'run_command': ['runner'], 'run': {'env': {'KEPT': '1'}}}


def result(value, **kw):
    return {'value': value, 'valid': True, 'disturbed': False, 'kernel_errors': 0, 'load_before': 1, 'load_after': 2,
            'others_cores_window': 1.0, 'others_cores_peak': 2.0, 'summary': 'synthetic fixture', 'evidence': ['fixture']} | kw


def arguments(output):
    return argparse.Namespace(output=str(output), repo='', scenario='', max_holds=20, max_steps=9, max_wall_seconds=100,
                              max_hold_seconds=480, exclusive_authorized=True, priority_authorized=False, suspect=[],
                              switch=[], suspects_only=False)


class Decisions(unittest.TestCase):
    def test_bands(self):
        for value, want in ((9, 'good'), (10, 'good'), (12, 'ambiguous'), (15, 'bad'), (16, 'bad')):
            self.assertEqual(r.classification(result(value), SCENARIO), want)

    def test_disturbed_never_decides(self):
        for kw in ({'disturbed': True}, {'valid': False}, {'kernel_errors': 1}):
            self.assertNotIn(r.classification(result(20, **kw), SCENARIO), ('good', 'bad'))

    def test_quiet_rule_mean_of_the_window(self):
        self.assertEqual(r.classification(result(20, others_cores_window=2.99), SCENARIO), 'bad')
        self.assertEqual(r.classification(result(20, others_cores_window=3.0), SCENARIO), 'disturbed')
        self.assertEqual(r.classification(result(20, others_cores_window=4.0), SCENARIO | {'quiet_cores': 5}), 'bad')

    def test_quiet_rule_highest_five_second_sample(self):
        self.assertEqual(r.classification(result(20, others_cores_peak=6.0), SCENARIO), 'bad')
        self.assertEqual(r.classification(result(20, others_cores_peak=6.01), SCENARIO), 'disturbed')
        self.assertEqual(r.classification(result(20, others_cores_peak=7), SCENARIO | {'quiet_peak_cores': 8}), 'bad')

    def test_quiet_rule_repeats_of_an_arm_agree(self):
        def runs(a1, b1, a2, b2):
            return [{'role': role, 'result': result(v)} for role, v in
                    [('reference', a1), ('candidate', b1), ('reference', a2), ('candidate', b2)]]
        self.assertEqual(r.paired_decision(runs(9.0, 16.0, 9.4, 16.7), SCENARIO, 'good'), 'bad')     # (4.4 % and 4.4 %)
        self.assertEqual(r.paired_decision(runs(9.0, 16.0, 9.5, 16.0), SCENARIO, 'good'), 'retry')   # (the reference: 5.6 %)
        self.assertEqual(r.paired_decision(runs(9.0, 16.0, 9.0, 17.0), SCENARIO, 'good'), 'retry')   # (the candidate: 6.3 %)
        self.assertEqual(r.paired_decision(runs(9.0, 16.0, 9.0, 17.0), SCENARIO | {'repeat_tolerance': 0.1}, 'good'), 'bad')
        self.assertTrue(r.repeats_agree([True, True], 0.05) and not r.repeats_agree([True, False], 0.05))

    def test_the_load_is_information_only(self):
        self.assertEqual(r.classification(result(20, load_before=90, load_after=45, load_max=95), SCENARIO), 'bad')
        unknown = {k: v for k, v in result(20).items() if k != 'others_cores_peak'}
        self.assertEqual(r.classification(unknown, SCENARIO), 'invalid')   # (quiet must be shown, not assumed)

    def test_invalid_numeric_and_missing_evidence(self):
        for value in (float('nan'), float('inf'), '20', True):
            self.assertEqual(r.classification(result(value), SCENARIO), 'invalid')
        self.assertEqual(r.classification({}, SCENARIO), 'invalid')
        self.assertEqual(r.classification(result(20, evidence=[]), SCENARIO), 'invalid')
        self.assertEqual(r.classification(result(20, load_before='busy'), SCENARIO), 'invalid')
        self.assertEqual(r.classification(result(20, load_after=float('nan')), SCENARIO), 'invalid')

    def test_lower_and_pass(self):
        s = SCENARIO | {'measure': {'name': 'fps', 'direction': 'lower-worse', 'good_max': -90, 'bad_min': -70}}
        self.assertEqual(r.classification(result(100), s), 'good')
        self.assertEqual(r.classification(result(60), s), 'bad')
        s = SCENARIO | {'kind': 'game-check', 'measure': {'direction': 'pass-fail'}}
        self.assertEqual(r.classification(result(True, load_before=50), s), 'good')
        self.assertEqual(r.classification(result(False), s), 'bad')
        self.assertEqual(r.classification(result(1), s), 'invalid')

    def test_pair_requires_every_reference_and_candidate(self):
        runs = [{'role': role, 'result': result(v)} for role, v in
                [('reference', 9.8), ('candidate', 15.6), ('reference', 10), ('candidate', 15)]]
        self.assertEqual(r.paired_decision(runs, SCENARIO, 'good'), 'bad')
        runs[-1]['result']['value'] = 12
        self.assertEqual(r.paired_decision(runs, SCENARIO, 'good'), 'retry')
        runs[-1]['result']['value'] = 16; runs[0]['result']['value'] = 11
        self.assertEqual(r.paired_decision(runs, SCENARIO, 'good'), 'retry')
        self.assertEqual(r.paired_decision(runs[:2], SCENARIO, 'good'), 'retry')

    def test_budget_validation(self):
        a = arguments('unused'); r.validate(SCENARIO, a)
        a.exclusive_authorized = False
        with self.assertRaises(ValueError): r.validate(SCENARIO, a)
        a.exclusive_authorized = True; a.max_hold_seconds = 481
        with self.assertRaises(ValueError): r.validate(SCENARIO, a)
        a.max_hold_seconds = 300
        with self.assertRaises(ValueError): r.validate(SCENARIO, a)
        a.max_hold_seconds = 480
        with self.assertRaises(ValueError): r.validate(SCENARIO | {'seconds': 100}, a)
        with self.assertRaises(ValueError):
            r.validate(SCENARIO | {'measure': {'direction': 'higher-worse', 'good_max': 10, 'bad_min': 10}}, a)
        a.switch = ['nolabel']
        with self.assertRaises(ValueError): r.validate(SCENARIO, a)
        a.switch = ['quiet:RX_SOMETHING=1,HOS_OTHER=0']; r.validate(SCENARIO, a)

    def test_groups_and_reusable_selection(self):
        rows = [{'commit': str(i), 'lift_key': k} for i, k in enumerate('aaabbcc')]
        self.assertEqual([(g['first'], g['last']) for g in r.groups(rows)], [(0, 2), (3, 4), (5, 6)])
        self.assertEqual(r.choose_candidate(rows, 0, 6, {'a'}), 2)
        self.assertEqual(r.choose_candidate(rows, 0, 6, set()), 3)
        self.assertIsNone(r.choose_candidate(rows, 2, 3, set()))

    def hunt(self, td, rows, **kw):
        a = arguments(td)
        for k, v in kw.items(): setattr(a, k, v)
        h = r.Hunt(a, SCENARIO, {'commits': rows})
        h.build = lambda i, reverts=(): {'commit': str(i), 'copy': td}
        return h

    def test_stop_at_supported_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            rows = [{'commit': str(i), 'lift_key': 'same'} for i in range(8)]
            h = self.hunt(td, rows)
            h.compare = lambda name, ref, candidate, refclass, scenario=None: 'good' if int(candidate['commit']) < 5 else 'bad'
            with patch.object(r, 'date', return_value='test'):
                answer = h.execute()
            self.assertEqual((answer['status'], answer['first_bad'], answer['remaining']), ('first_bad', '5', ['5']))
            h = self.hunt(td, rows, max_steps=1)
            h.compare = lambda name, ref, candidate, refclass, scenario=None: 'good' if int(candidate['commit']) < 5 else 'bad'
            with patch.object(r, 'date', return_value='test'):
                answer = h.execute()
            self.assertEqual((answer['status'], answer['first_bad']), ('inconclusive', None))
            self.assertEqual(answer['remaining'], ['4', '5', '6', '7'])

    def test_endpoints_not_reproduced_stops(self):
        with tempfile.TemporaryDirectory() as td:
            h = self.hunt(td, [{'commit': str(i), 'lift_key': 'same'} for i in range(4)])
            h.compare = lambda *a, **k: 'good'
            with patch.object(r, 'date', return_value='test'):
                self.assertEqual(h.execute()['status'], 'not_reproduced')

    def test_switch_is_tested_on_the_bad_build(self):
        with tempfile.TemporaryDirectory() as td:
            seen = []
            h = self.hunt(td, [{'commit': str(i), 'lift_key': 'same'} for i in range(4)],
                          switch=['quiet:RX_SOMETHING=1,HOS_OTHER=0'], suspects_only=True)

            def compare(name, ref, candidate, refclass, scenario=None):
                seen.append((name, ref['commit'], candidate['commit'], refclass, scenario))
                return 'bad' if name == 'endpoints' else 'good'
            h.compare = compare
            with patch.object(r, 'date', return_value='test'):
                answer = h.execute()
            self.assertEqual(answer['status'], 'suspect_explains')
            name, ref, candidate, refclass, path = seen[1]
            self.assertEqual((name, ref, candidate, refclass), ('switch quiet', '3', '3', 'bad'))
            env = json.loads(Path(path).read_text())['run']['env']
            self.assertEqual(env, {'KEPT': '1', 'RX_SOMETHING': '1', 'HOS_OTHER': '0'})
            self.assertEqual(answer['suspects'][0]['decision'], 'good')

    def test_wall_and_hold_exhaustion(self):
        with tempfile.TemporaryDirectory() as td:
            h = self.hunt(td, [{'commit': str(i), 'lift_key': 'same'} for i in range(3)])
            h.holds = h.args.max_holds
            with self.assertRaisesRegex(RuntimeError, 'hold budget'): h.budget()
            h.holds = 0; h.args.max_wall_seconds = -1
            with self.assertRaisesRegex(RuntimeError, 'wall-time'): h.budget()


ADAPTER = '''import json, sys
out, scenario, copy, commit, seconds = sys.argv[1:6]
fake = json.load(open(scenario))["fake"]
json.dump({"value": fake["value"], "valid": fake["valid"], "disturbed": False, "kernel_errors": 0, "load_before": 0,
           "load_after": 0, "summary": copy + " " + commit + " " + seconds, "evidence": [out]}, open(out + "/result.json", "w"))
'''


class HoldWorker(unittest.TestCase):
    def plan(self, td, values, **kw):
        td = Path(td); (td / 'adapter.py').write_text(ADAPTER)
        entries = []
        for i, (value, valid) in enumerate(values):
            (td / f's{i}.json').write_text(json.dumps({'fake': {'value': value, 'valid': valid}}))
            entries.append({'role': 'reference' if i % 2 == 0 else 'candidate', 'copy': f'copy{i}', 'commit': f'c{i}',
                            'output': str(td / 'runs' / str(i)), 'scenario': str(td / f's{i}.json')})
        plan = {'kind': 'check', 'entries': entries, 'scenario': str(td / 's0.json'), 'seconds': 1, 'grace_seconds': 5,
                'max_hold_seconds': 60,
                'run_command': [sys.executable, str(td / 'adapter.py'), '{output}', '{scenario}', '{copy}', '{commit}', '{seconds}']} | kw
        (td / 'plan.json').write_text(json.dumps(plan))
        return td / 'plan.json'

    def test_runs_in_order_with_each_entrys_scenario(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.plan(td, [(1, True), (2, True), (3, True), (4, True)])
            r.hold(path)
            runs = json.loads(path.with_suffix('.runs.json').read_text())
            self.assertEqual([x['result']['value'] for x in runs], [1, 2, 3, 4])
            self.assertEqual(runs[1]['result']['summary'], 'copy1 c1 1')
            self.assertEqual(json.loads(path.with_suffix('.hold.json').read_text())['runs'], 4)

    def test_an_invalid_run_ends_the_hold(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.plan(td, [(1, True), (2, False), (3, True), (4, True)])
            r.hold(path)
            self.assertEqual(len(json.loads(path.with_suffix('.runs.json').read_text())), 2)

    def test_settling_spends_spare_seconds_only(self):
        with tempfile.TemporaryDirectory() as td:
            path = self.plan(td, [(1, True)], settle_seconds=400, quiet_cores=-1, max_hold_seconds=18)   # (spare: 18-10-6 = 2 s)
            r.hold(path)
            record = json.loads(path.with_suffix('.hold.json').read_text())
            self.assertTrue(1.5 <= record['settled_seconds'] <= 4, record)
            self.assertEqual(record['runs'], 1)

    def test_a_plan_past_the_limit_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(ValueError, 'does not fit'):
                r.hold(self.plan(td, [(1, True)] * 4, max_hold_seconds=30))
            with self.assertRaisesRegex(ValueError, 'duration'):
                r.hold(self.plan(Path(td) / 'b', [(1, True)], max_hold_seconds=481) if (Path(td) / 'b').mkdir() is None else None)


class GitHistory(unittest.TestCase):
    def test_keys_and_public_plan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); repo = root / 'repo'; repo.mkdir()

            def git(*a):
                return subprocess.check_output(['git', '-C', str(repo), *a], text=True, stderr=subprocess.DEVNULL).strip()
            git('init'); (repo / 'nx2').mkdir(); (repo / 'host').mkdir(); (repo / 'third_party/a64-kit').mkdir(parents=True)
            (repo / 'nx2/lift.py').write_text('lift one\n'); (repo / 'nx2/build.py').write_text('build one\n')
            (repo / 'host/one.c').write_text('HLE(one) {}\n'); (repo / 'third_party/a64-kit/a').write_text('kit\n')

            def commit(subject):
                git('add', '.'); git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', subject)
                return git('rev-parse', 'HEAD')
            a = commit('first (#11)'); (repo / 'nx2/build.py').write_text('build two\n'); b = commit('build (#12)')
            (repo / 'nx2/lift.py').write_text('lift two\n'); c = commit('lift (#13)')
            (repo / 'host/one.c').write_text('HLE(two) {}\n'); d = commit('hook (#14)')
            self.assertEqual(r.lift_key(repo, a), r.lift_key(repo, b))
            self.assertNotEqual(r.lift_key(repo, b), r.lift_key(repo, c))
            self.assertNotEqual(r.lift_key(repo, c), r.lift_key(repo, d))
            history = r.history(repo, a, d)
            self.assertEqual((history['commits_in_range'], history['distinct_lifts']), (3, 3))
            self.assertEqual(history['commits'][2]['prs'], [13])
            out = root / 'plan'
            subprocess.run([sys.executable, '-I', str(HERE / 'regress.py'), 'plan', '--repo', str(repo), '--good', a, '--bad', d,
                            '--output', str(out)], check=True, stdout=subprocess.DEVNULL)
            self.assertEqual(json.loads((out / 'range.json').read_text())['bad'], d)


def frame_log(path, gaps=(), frames=6001, step=10.0, cut=False):
    """A frame log of `frames` presents `step` ms apart, with longer frames at the given (index, ms)."""
    rows, t = ['frame,time_s,frametime_ms'], 0.0
    long = dict(gaps)
    for i in range(frames):
        d = 0.0 if i == 0 else long.get(i, step)
        t += d / 1000
        rows.append(f'{i},{t:.4f},{d:.3f}')
    (path / 'frames.csv').write_text('\n'.join(rows) + ('\n9999,12' if cut else '\n'))


class AdapterLogic(unittest.TestCase):
    def test_all_public_scripts_parse(self):
        for path in HERE.glob('*.py'):
            compile(path.read_text(), str(path), 'exec')

    def test_quiet_measure_mean_and_five_second_peak(self):
        import os
        ticks = os.sysconf('SC_CLK_TCK')

        def samples(burst):
            out, busy, game = [], 0.0, 0.0
            for i in range(17):   # (every 2.5 s from 100 s to 140 s: 2 cores of other work, `burst` cores for 5 s at 120 s)
                t = 100 + 2.5 * i
                out.append({'t': t, 'busy': round(busy), 'game': round(game)})
                others = burst if 120 <= t < 125 else 2.0
                busy += (others + 4.0) * ticks * 2.5; game += 4.0 * ticks * 2.5
            return out
        q = runner.quiet_measure(samples(2.0), 105, 135)
        self.assertAlmostEqual(q['mean'], 2.0, places=2); self.assertAlmostEqual(q['peak'], 2.0, places=2)
        q = runner.quiet_measure(samples(7.0), 105, 135)
        self.assertTrue(q['mean'] < 3 and 6.9 < q['peak'] <= 7.01, q)   # (a 5-second burst the mean hides)
        self.assertTrue(runner.quiet_verdict({'others_cores_window': 2.0, 'others_cores_peak': 2.0}, {}))
        self.assertFalse(runner.quiet_verdict({'others_cores_window': q['mean'], 'others_cores_peak': q['peak']}, {}))
        self.assertFalse(runner.quiet_verdict({'others_cores_window': 3.0, 'others_cores_peak': 3.0}, {}))
        self.assertFalse(runner.quiet_verdict({'others_cores_window': 1.0}, {}))
        self.assertIsNone(runner.quiet_measure(samples(2.0), 105, 300))   # (samples that do not cover the window)

    def test_frame_statistics_and_window(self):
        self.assertEqual(runner.statistics_of([10, 10, 20])['over16_7'], 1)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td); frame_log(path)
            m, _ = runner.analyse(path, {'anchor': 'first', 'from': 5, 'to': 15})
            self.assertAlmostEqual(m['mean_ms'], 10); self.assertEqual(m['frames'], 1000)
            with self.assertRaisesRegex(AssertionError, 'save-load'):
                runner.analyse(path, {'anchor': 'load'})
            with self.assertRaisesRegex(AssertionError, 'short measurement'):
                runner.analyse(path, {'anchor': 'first', 'from': 50, 'to': 80})

    def test_a_whole_run_with_a_stand_in_wrapper_and_game(self):
        """The adapter end to end without a game or a gate: a wrapper that leaves what hs-run.sh's inner half leaves, and a
        sleeping stand-in for the game, so the samples, the quiet measure and the result file are the real code's."""
        import os, shutil, textwrap
        if not shutil.which('journalctl') or not shutil.which('sleep'):
            self.skipTest('needs journalctl and sleep')
        with tempfile.TemporaryDirectory() as td:
            td = Path(td).resolve(); copy = td / '.hookshot-h-standin'; copy.mkdir()
            shutil.copy(shutil.which('sleep'), copy / 'hookshot')
            wrapper = td / 'wrapper.py'
            wrapper.write_text('#!' + sys.executable + '\n' + textwrap.dedent('''\
                import subprocess, sys, time
                from pathlib import Path
                R = Path(sys.argv[2]); copy = Path((R / 'copy.txt').read_text().strip())
                (R / 'load-before.txt').write_text('1.00 1.00 1.00\\n')
                t0 = time.monotonic_ns()
                subprocess.run([str(copy / 'hookshot'), '9'], cwd=copy)
                frames = ['frame,time_s,frametime_ms']; presents = ['frame,return_ns,interval_ns,a,b,c,d']
                for i in range(900):
                    frames.append('%d,%.4f,%.3f' % (i, i * 0.01, 0.0 if i == 0 else 10.0))
                    presents.append('%d,%d,10000000,0,0,0,0' % (i + 1, t0 + i * 10000000))
                (R / 'frames.csv').write_text('\\n'.join(frames) + '\\n')
                (R / 'present.csv').write_text('\\n'.join(presents) + '\\n')
                (R / 'hookshot_log.txt').write_text('hookshot v0-standin (source 0000)\\nvk: device 0: Stand-in GPU, Vulkan\\n')
                (R / 'exit.txt').write_text('124\\n'); (R / 'wall.txt').write_text('9.0\\n')
                (R / 'load-after.txt').write_text('1.00 1.00 1.00\\n')
                '''))
            os.chmod(wrapper, 0o755)
            scenario = {'kind': 'timing', 'measure': {'name': 'mean_ms', 'direction': 'higher-worse'},
                        'run': {'label': 'standin', 'anchor': 'first', 'from': 1, 'to': 8, 'sample_seconds': 0.5,
                                'readback_csv': False, 'wrapper': str(wrapper), 'device_pattern': 'Stand-in GPU',
                                'expect_log': ['source 0000'], 'forbid_log': ['never there']}}
            with patch.object(runner, 'GATE_MARK', 'PATH'):   # (the gate's mark is not set for a test: any set variable stands in)
                got = runner.run_case(copy, td / 'run', scenario, 9, 'c0ffee00')
            saved = json.loads((td / 'run' / 'result.json').read_text())
            self.assertTrue(got['valid'], got.get('problem'))
            self.assertAlmostEqual(saved['measures']['mean_ms'], 10.0); self.assertEqual(saved['measures']['frames'], 700)
            self.assertEqual((saved['exit'], saved['kernel_errors'], saved['mods']['folder_absent']), (124, 0, True))
            for key in ('others_cores_window', 'others_cores_peak', 'game_cores_window', 'cpu_ms_per_frame'):
                self.assertIsInstance(saved[key], float, key)
            self.assertLess(saved['game_cores_window'], 0.5)   # (the stand-in sleeps)
            self.assertEqual(saved['disturbed'], not runner.quiet_verdict(saved, scenario))
            self.assertIn('standin', saved['summary']); self.assertGreaterEqual(saved['threads']['groups']['hookshot']['n'], 1)

    def test_a_copys_own_source_stamp_is_required_in_the_log(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(runner.source_stamp(td), '')
            (Path(td) / 'regress-source.txt').write_text('abcd1234\n')
            self.assertEqual(runner.source_stamp(td), 'abcd1234')
        log = '[hos host] hookshot v2.1.0-rc.3-190-g7331f886-dirty (source abcd1234)\n'
        self.assertTrue(runner.stamp_in_log(log, 'abcd1234'))
        self.assertFalse(runner.stamp_in_log(log, 'ffff0000'))   # (another build's binary ran)
        self.assertTrue(runner.stamp_in_log(log, ''))            # (a copy without the file: the scenario's own lines decide)

    def test_a_run_is_started_later_to_miss_a_periodic_disturbance(self):
        quarter = [{'every': 900, 'at': 536, 'for': 9}]   # (a job at 8:56 past each quarter hour, 9 s long)
        window = [38, 80]
        self.assertEqual(r.avoid_delay(7200 + 0, window, quarter), 0.0)            # (window 38..80 s: before 536)
        self.assertEqual(r.avoid_delay(7200 + 700, window, quarter), 0.0)          # (after it, before the next)
        self.assertAlmostEqual(r.avoid_delay(7200 + 480, window, quarter), 27.0)   # (518..560 covers 536..545: start 27 s later)
        self.assertAlmostEqual(r.avoid_delay(7200 + 500, window, quarter), 7.0)    # (538..580 begins inside it)
        self.assertEqual(r.avoid_delay(7200 + 508, window, quarter), 0.0)          # (546..588: just clear)
        self.assertEqual(r.avoid_delay(7200 + 480, window, []), 0.0)
        two = quarter + [{'every': 900, 'at': 560, 'for': 20}]                      # (a delay that lands on a second one moves again)
        got = r.avoid_delay(7200 + 480, window, two)
        self.assertAlmostEqual(got, 62.0)                                          # (the window then starts at 580, after both)

    def test_a_three_column_present_trace_is_read_like_a_seven_column_one(self):
        with tempfile.TemporaryDirectory() as td:
            old, new = Path(td) / 'old.csv', Path(td) / 'new.csv'
            old.write_text('frame,return_ns,interval_ns\n1,1000000000,0\n2,1016000000,16000000\n3,1033000000,17000000\n')
            new.write_text('frame,return_ns,interval_ns,a,b,c,d\n1,1000000000,0,0,0,0,0\n2,1016000000,16000000,0,0,0,0\n'
                           '3,1033000000,17000000,0,0,0,0\n4,10')
            self.assertEqual([r[:3] for r in runner.present_rows(old)], [r[:3] for r in runner.present_rows(new)])
            self.assertEqual(len(runner.present_rows(old)), 3)   # (no heading, no cut-off line)
            self.assertEqual(runner.present_window(old, 1, 3), (1.0, 1.033))
            self.assertEqual(runner.present_window(new, 1, 3), (1.0, 1.033))
            self.assertEqual(runner.present_metrics(old, 500000000, 10), {'game_presents': 3, 'start_to_first_present_s': 0.5})

    def test_a_windowed_frame_log_is_matched_to_the_games_presents_at_its_end(self):
        # (200 presents of the game; with a window the frame log has the start-up screen's 113 presents before them)
        # (intervals a tenth of a millisecond apart from one present to the next, so that a wrong match shows)
        rows = [[str(n), str(10**9 + n * 16_000_000), str(16_000_000 + (n % 37) * 100_000)] for n in range(1, 201)]
        ms = [0.0] * 114 + [16.0 + (n % 37) * 0.1 for n in range(1, 201)]
        frames = list(range(0, 314))
        self.assertEqual(runner.present_shift(frames, ms, rows), -113)
        # (the game stopped after the frame log's line and before the trace's: one frame more at the end)
        self.assertEqual(runner.present_shift(frames + [314], ms + [17.55], rows), -113)
        # (without a window the frame log is one behind; intervals that never agree leave the tails' difference)
        self.assertEqual(runner.present_shift(list(range(0, 200)), [3.0] * 200, rows), 1)
        self.assertEqual(runner.present_shift([], [], rows), 1)

    def test_another_device_and_a_memory_limit_reach_the_wrapper(self):
        with tempfile.TemporaryDirectory() as td:
            copy, out, plain = Path(td) / 'copy', Path(td) / 'run', Path(td) / 'run2'
            copy.mkdir()
            runner.setup(copy, out, {'intel': True, 'mem_max': '32G'}, 60)
            self.assertTrue((out / 'intel').exists())
            self.assertEqual((out / 'memmax').read_text(), '32G')
            runner.setup(copy, plain, {}, 60)
            self.assertFalse((plain / 'intel').exists())
            self.assertEqual((plain / 'memmax').read_text(), '')

    def test_a_plan_for_the_other_device_needs_that_devices_gate(self):
        with tempfile.TemporaryDirectory() as td:
            plan = Path(td) / 'plan.json'
            plan.write_text(json.dumps({'kind': 'timing', 'gate': 'intel', 'entries': [], 'seconds': 10, 'max_hold_seconds': 60, 'grace_seconds': 5}))
            env = r.os.environ   # (the worker's own view of the environment)
            kept = {k: env.pop(k, None) for k in ('HSGATE_IN_GATE', 'HSGATE_IN_INTEL_GATE')}
            try:
                env['HSGATE_IN_GATE'] = '1'   # (the card's gate is not the one this plan is for)
                with self.assertRaisesRegex(ValueError, 'HSGATE_IN_INTEL_GATE'):
                    r.hold(plan)
                del env['HSGATE_IN_GATE']
                plan.write_text(json.dumps({'kind': 'timing', 'entries': [], 'seconds': 10, 'max_hold_seconds': 60, 'grace_seconds': 5}))
                with self.assertRaisesRegex(ValueError, 'HSGATE_IN_GATE '):
                    r.hold(plan)
            finally:
                for k, v in kept.items():
                    env.pop(k, None)
                    if v is not None:
                        env[k] = v

    def test_the_load_is_the_longest_gap_not_a_later_hitch(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td); frame_log(path, gaps=[(300, 500.0), (1500, 4000.0), (2500, 700.0)], cut=True)
            m, _ = runner.analyse(path, {'anchor': 'load', 'from': 15, 'to': 45, 'load_after': 10, 'load_before': 60})
            self.assertAlmostEqual(m['anchor_s'], 0.5 + 14.99 + 4.0 - 0.01, places=2)   # (the 4 s gap's end)
            self.assertEqual(len(m['gaps_over_limit']), 3)
            self.assertEqual(m['after_load_over100'], 1)   # (the 700 ms hitch after it is counted, not taken for the load)

    def test_readbacks_and_log_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            rows = ['kind,frame_begin,frame_end,start_ns,elapsed_ms,wait_ms,catchup_ms,eligible,waited,fallback']
            for frame in range(100, 200):
                rows.append(f'invalidate,{frame},{frame},0,6.5,6.0,1.0,1,1,0')
                rows.append(f'invalidate,{frame},{frame + 1},0,0.1,0.0,0.0,1,0,0')
                rows.append(f'invalidate,{frame},{frame},0,0.2,0.0,0.0,0,0,0')
            rows.append('read_texels,150,150,0,2.0,1.0,0.0,1,1,0')
            (path / 'readbacks.csv').write_text('\n'.join(rows) + '\ninvalidate,199,19')   # (a cut last line)
            m = runner.readback_metrics(path / 'readbacks.csv', 100, 199)
            self.assertAlmostEqual(m['readbacks_per_frame'], 2.0); self.assertAlmostEqual(m['invalidates_per_frame'], 3.0)
            self.assertAlmostEqual(m['readback_wait_ms_per_frame'], 6.0); self.assertAlmostEqual(m['readback_waited_mean_ms'], 6.0)
            self.assertAlmostEqual(m['readback_wait_mean_ms'], 3.0); self.assertEqual(m['readbacks_spanning_present'], 100)
            self.assertEqual((m['readback_fallbacks'], m['read_texels_calls']), (0, 1))
            log = ("vk: a frame's replay time (ms): submit 9.50, draw 3.25\n"
                   "vk: a frame: 0.0 draws left out (not ready), 2.0 waits for shaders or pipelines (4.5 ms)\n"
                   "vk: 240 presents, 80.1 fps; a frame: 3800 draws\nvk: GPU time 6.2 ms a frame\n"
                   "vk: status: 600 frames, the slowest 30.0 ms; VRAM 7.6 of 13.2 GB\n"
                   "vk: readbacks of GPU results: 2900 of 3000 waited for their write-back, 5.50 ms each\n")
            m, blocks = runner.log_metrics(log, 100, 300)
            self.assertEqual((m['log_blocks_in_window'], m['log_gpu_ms'], m['replay_submit'], m['log_wait_ms']), (1, 6.2, 9.5, 4.5))
            self.assertEqual((m['vram_gb_max'], m['readback_log_ms_each'], blocks[0]['present']), (7.6, 5.5, 240))

    def test_setup_leaves_what_the_wrapper_reads(self):
        with tempfile.TemporaryDirectory() as td:
            copy = Path(td) / '.hookshot-h-x'; copy.mkdir(); out = Path(td) / 'run'
            env = runner.setup(copy, out, {'env': {'A': '1'}, 'input': '5:A'}, 90)
            self.assertEqual((out / 'memmax').stat().st_size, 0)
            self.assertEqual(((out / 'input.txt').read_text(), (out / 'seconds.txt').read_text()), ('5:A', '90\n'))
            self.assertIn('A=1\n', (out / 'extra-env.txt').read_text())
            self.assertTrue(env['RX_VK_READBACK_CSV'].endswith('readbacks.csv') and (out / 'user').is_dir())
            (copy / 'mods').mkdir()
            with self.assertRaisesRegex(AssertionError, 'mods'):
                runner.setup(copy, Path(td) / 'run2', {}, 90)
            (copy / 'mods').rmdir(); (copy / 'hookshot_options.ini').write_text('dlss=2\n')
            with self.assertRaisesRegex(AssertionError, 'options'):
                runner.setup(copy, Path(td) / 'run3', {}, 90)


if __name__ == '__main__':
    unittest.main(verbosity=2)
