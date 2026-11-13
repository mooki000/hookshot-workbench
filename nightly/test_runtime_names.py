"""Game-free compatibility fixtures; no real game, GPU, build, tracker or authentication child."""
from pathlib import Path
from types import SimpleNamespace
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build as builder
import common
import hunt
import metrics
import monitor
import report
import rolling
import scenario
from hs_names import ALL_SETTINGS, GAME_EXECUTABLES, NameError, Names, PROJECTS

spec = importlib.util.spec_from_file_location('nightly_names_driver', Path(__file__).with_name('hs-nightly.py'))
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def checkout(root, project):
    (root / 'scripts').mkdir(parents=True)
    (root / 'scripts' / (project + '_ctl.py')).write_text('IDENTITY = ' + repr(project) + '\n')
    (root / 'nx2').mkdir()
    (root / 'nx2' / 'build.py').write_text('import os\n' + ''.join(
        'os.environ.get(' + repr(project.upper() + '_' + suffix) + ')\n'
        for suffix in ('LIFT_JOBS', 'KEEP_BUILD_FILES', 'NGX_SDK')))


def copy_fixture(argv):
    source, target = Path(argv[-2]), Path(argv[-1])
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)
    return ''


class ReaderNames(unittest.TestCase):
    def test_missing_log_is_empty_evidence_not_a_guessed_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(common.text(None), '')
            result = metrics.collect(root / 'missing-run', root / 'missing-evidence', {})
            self.assertEqual(result['fatal_lines'], [])
            self.assertEqual(result['device_lines'], [])
            current = {'id': 'fixture', 'status': 'failed', 'run_dir': str(root), 'summary': 'synthetic',
                       'metrics': {}, 'signatures': {}, 'images': {}, 'fatal_lines': ['FATAL synthetic']}
            finding = driver.comparisons(None, 'fixture', current, [])[0]
            self.assertIsNone(finding['log'])

    def test_both_log_readers_and_finding_paths(self):
        for project in PROJECTS:
            with self.subTest(project=project), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                logfile = root / Names(project).log
                logfile.write_text(project + ' v1-fixture\nvk: device 0: synthetic device\nFATAL synthetic\n')
                result = metrics.collect(root, root, {})
                self.assertEqual(result['fatal_lines'], ['FATAL synthetic'])
                self.assertEqual(result['device_lines'], ['vk: device 0: synthetic device'])
                current = {**result, 'id': 'fixture', 'status': 'failed', 'run_dir': str(root), 'disturbed': False}
                findings = driver.comparisons(None, 'fixture', current, [])
                self.assertTrue(findings)
                self.assertTrue(all(row['log'] == str(logfile) for row in findings))
                with patch.object(scenario, 'Monitor'), patch.object(scenario, 'client_module'):
                    run = scenario.Run({}, root, {'id': 'fixture', 'steps': [{'op': 'assert_log',
                        'pattern': 'FATAL synthetic'}]}, root, 'unused')
                run.run = root
                run.steps()
                self.assertTrue(run.meta['checks'])

    def test_mixed_logs_fail_in_readers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for project in PROJECTS:
                (root / Names(project).log).touch()
            with self.assertRaises(NameError):
                metrics.collect(root, root, {})
            with self.assertRaises(NameError):
                driver.comparisons(None, 'fixture', {'status': 'failed', 'run_dir': str(root)}, [])

    def test_control_client_comes_from_selected_checkout(self):
        for project in PROJECTS:
            with self.subTest(project=project), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                checkout(root, project)
                self.assertEqual(scenario.client_module({'worktree': str(root)}).IDENTITY, project)
                other = Names('longshot' if project == 'hookshot' else 'hookshot')
                (root / other.control_client).touch()
                with self.assertRaises(NameError):
                    scenario.client_module({'worktree': str(root)})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(NameError):
                scenario.client_module({'worktree': directory})

    def test_enabled_launch_requires_copy_identity_before_any_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(scenario, 'Monitor'), patch.object(scenario, 'client_module'):
                run = scenario.Run({'gpu_scenarios_enabled': True}, root / 'unknown',
                                   {'id': 'fixture'}, root / 'out', 'unused')
            with patch.object(scenario.subprocess, 'Popen') as child:
                with self.assertRaises(NameError):
                    run.execute()
            child.assert_not_called()
            self.assertFalse((root / 'out').exists())

    def test_environment_isolation_includes_both_game_names(self):
        with patch.dict(os.environ, {'HOOKSHOT_FAKE': '1', 'LONGSHOT_FAKE': '1', 'HOS_FAKE': '1',
                                     'RX_FAKE': '1', 'UNCHANGED': 'kept'}, clear=True):
            cleaned = common.env_clean(game=True)
        self.assertEqual(cleaned, {'UNCHANGED': 'kept'})


class MonitorNames(unittest.TestCase):
    def test_player_detection_covers_both_platform_names_and_cwd(self):
        records = []
        for index, name in enumerate(GAME_EXECUTABLES):
            records += [{'pid': index * 2, 'exe': '/player/' + name, 'cwd': '/elsewhere'},
                        {'pid': index * 2 + 1, 'exe': '/owned/' + name, 'cwd': '/player'}]
        records.append({'pid': 99, 'exe': '/bin/sh', 'cwd': '/player'})
        self.assertEqual([row['pid'] for row in monitor.player_games({'play_folder': '/player'}, records)],
                         list(range(len(GAME_EXECUTABLES) * 2)))

    def test_owned_core_paths_exist_before_the_binaries(self):
        with patch.object(monitor, 'stamp', return_value='fixture-time'):
            watched = monitor.Monitor({'owned_copies': ['/fixture/previous']}, '/fixture/current',
                                      '/fixture/run', '/fixture/out')
        for root in ('/fixture/previous', '/fixture/current'):
            for name in GAME_EXECUTABLES:
                self.assertIn(root + '/' + name, watched.owned_executables)
        status = ('WATCHDOG 00:00:00: mem available 61 GB (limit 6), pressure full avg10 0 (limit 20), '
                  'swap free 178 GB (limit 120), driver fault lines 0, Hyprland 1, '
                  'core dumps since fixture-start: 1, gate open\n')
        result = monitor.watchdog_decision(status, [{'pid': 1, 'exe': '/fixture/current/longshot'}],
                                           watched.owned_executables, [])
        self.assertTrue(result['faults'])
        self.assertFalse(result['ignored'])

    def test_resource_accounting_recognizes_current_and_other_legacy_game(self):
        processes = [
            {'pid': 1, 'exe': '/owned/longshot', 'cwd': '/owned', 'comm': 'longshot', 'rss_mib': 5, 'cpu_s': 2},
            {'pid': 2, 'exe': '/other/hookshot', 'cwd': '/other', 'comm': 'hookshot', 'rss_mib': 9, 'cpu_s': 3},
            {'pid': 3, 'exe': '/bin/Hyprland', 'cwd': '/', 'comm': 'Hyprland', 'rss_mib': 1, 'cpu_s': 1}]
        def proc_text(path):
            return ('MemAvailable: 83886080 kB\nSwapFree: 167772160 kB\n' if str(path).endswith('meminfo')
                    else 'full avg10=0.0 avg60=0.0 avg300=0.0 total=0\n')
        def query(argv, **kwargs):
            output = '1, 64\n' if '--query-compute-apps=pid,used_gpu_memory' in argv else '128, 1024, 1, fixture-driver\n'
            return SimpleNamespace(returncode=0, stdout=output)
        with patch.object(monitor, 'processes', return_value=processes), \
             patch.object(monitor, 'text', side_effect=proc_text), \
             patch.object(monitor.os, 'getloadavg', return_value=(0, 0, 0)), \
             patch.object(monitor.subprocess, 'run', side_effect=query):
            result = monitor.resources({'play_folder': '/player'}, executables={'/owned/longshot', '/owned/hookshot'})
        self.assertEqual(result['own_pids'], [1])
        self.assertEqual(result['game_pids'], [1, 2])
        self.assertEqual(result['other_games'], 1)
        self.assertEqual(result['rss_mib'], 5)
        self.assertEqual(result['gpu_process_mib'], 64)

    def test_tail_handles_missing_both_names_and_ambiguous_logs(self):
        for projects in ((), ('hookshot',), ('longshot',), PROJECTS):
            with self.subTest(projects=projects), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                for project in projects:
                    (root / Names(project).log).write_text('WARNING synthetic ' + project + '\n')
                with patch.object(monitor, 'stamp', return_value='fixture-time'):
                    watched = monitor.Monitor({}, root / 'copy', root, root)
                watched.watchdog = SimpleNamespace(poll=lambda: None)
                watched.on_fault = Mock()
                with patch.object(monitor, 'resources', return_value={'own_pids': []}) as sample, \
                     patch.object(monitor, 'unsafe', return_value=[]), \
                     patch.object(monitor.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='')), \
                     patch.object(watched.stop, 'wait', side_effect=lambda seconds: watched.stop.set()):
                    watched.work()
                sample.assert_called_once_with({}, executables=watched.sample_executables)
                if len(projects) > 1:
                    self.assertTrue(watched.faults)
                    watched.on_fault.assert_called_once()
                else:
                    self.assertFalse(watched.faults)
                    if projects:
                        events = [json.loads(line) for line in (root / 'log-events.jsonl').read_text().splitlines()]
                        self.assertEqual(events[0]['line'], 'WARNING synthetic ' + projects[0])
                    else:
                        self.assertFalse((root / 'log-events.jsonl').exists())


class RollingNames(unittest.TestCase):
    def test_reference_snapshots_strip_all_settings_and_keep_selected_binaries(self):
        for project in PROJECTS:
            with self.subTest(project=project), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); source = root / 'source'; source.mkdir()
                names = Names(project)
                (source / names.executable).write_bytes(b'synthetic executable marker')
                for setting in ALL_SETTINGS:
                    (source / setting).write_text('synthetic setting\n')
                manifest = {'copy': str(source), 'commit': 'a' * 40, 'source_stamp': '1234abcd',
                            'binary_sha256': {names.executable: hashlib.sha256((source / names.executable).read_bytes()).hexdigest()}}
                with patch.object(rolling, 'processes', return_value=[]):
                    with self.assertRaisesRegex(NameError, 'mixed'):
                        rolling.snapshot_reference(manifest, root / 'mixed-snapshot')
                self.assertFalse((root / 'mixed-snapshot').exists())
                for setting in ALL_SETTINGS:
                    if setting not in (names.options, names.env_file):
                        (source / setting).unlink()
                with patch.object(rolling, 'processes', return_value=[]), \
                     patch.object(rolling, 'capture', side_effect=copy_fixture):
                    result = rolling.snapshot_reference(manifest, root / 'snapshot')
                snapshot = Path(result['copy'])
                self.assertTrue((snapshot / names.executable).is_file())
                self.assertTrue(all(not (snapshot / setting).exists() for setting in ALL_SETTINGS))
                self.assertTrue(all((source / setting).exists() for setting in (names.options, names.env_file)))
                self.assertFalse((source / 'regress-source.txt').exists())
                self.assertEqual((snapshot / 'regress-source.txt').read_text(), '1234abcd\n')

    def test_all_supported_running_names_block_stamp_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in GAME_EXECUTABLES:
                with self.subTest(name=name), patch.object(rolling, 'processes', return_value=[{'exe': str(root / name)}]):
                    with self.assertRaisesRegex(RuntimeError, 'running'):
                        rolling.stamp_new_copy({'copy': str(root), 'source_stamp': '1234abcd'})
            self.assertFalse((root / 'regress-source.txt').exists())

    def test_cache_reuse_uses_selected_names_once(self):
        for project in PROJECTS:
            with self.subTest(project=project), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); old = root / 'old'; new = root / 'new'
                old.mkdir(); new.mkdir(); names = Names(project)
                for folder in (old, new):
                    (folder / names.executable).touch()
                for name in names.cache_files:
                    if name.startswith('.'):
                        (old / name).mkdir(); (old / name / 'fixture').write_text('synthetic cache')
                    else:
                        (old / name).write_text('synthetic cache')
                with patch.object(rolling, 'capture', side_effect=copy_fixture) as copied:
                    result = rolling.reuse_cache({'copy': str(old)}, {'copy': str(new)})
                self.assertEqual(result, list(names.cache_files))
                self.assertEqual(copied.call_count, len(names.cache_files))
                self.assertTrue(all((new / name).exists() for name in names.cache_files))

    def test_rename_requires_cold_cache_and_unknown_or_mixed_copy_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); old = root / 'old'; new = root / 'new'
            old.mkdir(); new.mkdir(); (old / 'hookshot').touch(); (new / 'longshot').touch()
            previous, current = {'copy': str(old)}, {'copy': str(new)}
            with patch.object(rolling.subprocess, 'run', side_effect=AssertionError('no Git query for a name mismatch')), \
                 patch.object(rolling, 'capture', side_effect=AssertionError('must not copy')):
                self.assertFalse(rolling.compatible_cache({}, previous, current))
                with self.assertRaises(NameError):
                    rolling.reuse_cache(previous, current)
                (new / 'hookshot').touch()
                with self.assertRaises(NameError):
                    rolling.compatible_cache({}, previous, current)
                with self.assertRaises(NameError):
                    rolling.reuse_cache(previous, {'copy': str(root / 'absent')})

    def test_profile_receipt_is_carried_to_both_handoff_adapters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {'python': sys.executable, 'repo': 'fixture-repo', 'regress_dir': str(root / 'regress'),
                      'worktree': str(root / 'worktree'), 'cpm': 'fixture-cpm', 'romfs': 'unused-romfs',
                      'exefs': 'unused-exefs', 'build_jobs': 16, 'build_gate': 'fixture-gate',
                      'full_build_lock': 'fixture-lock', 'build_copy': str(root / 'copy'), 'quiet_load_max': 8}
            builds = []
            for label, commit in (('previous', 'a' * 40), ('new', 'b' * 40)):
                folder = root / label; folder.mkdir(); (folder / 'regress-source.txt').write_text('1234abcd\n')
                builds.append({'copy': str(folder), 'commit': commit, 'source_commit': commit,
                               'source_stamp': '1234abcd', 'binary_sha256': {'longshot': '0' * 64},
                               'profile': {'longshot_pieces.prof': {}, 'hot_funcs.txt': {}, 'hot_order.txt': {}}})
            previous, current = builds
            scene = {'id': 'fixture', 'scenario': {'run': {'expect_log': ['source 1234abcd']}}, 'runs': []}
            decision = {'measure': {'name': 'mean_ms', 'direction': 'higher-worse', 'good_max': 1, 'bad_min': 2}}
            with patch.object(rolling, 'capture', return_value=''), \
                 patch.object(hunt.subprocess, 'run', side_effect=AssertionError('must not start a hunt')):
                receipt = rolling.handoff(config, config, root / 'rolling', scene, decision, previous, current)
                emitted = json.loads((Path(receipt).parent / 'scenario.json').read_text())
                self.assertEqual(emitted['build']['profile_files'], list(current['profile']))
                result = hunt.handoff(config, {'id': 'fixture', 'seconds': 90}, previous, current,
                                      {'kind': 'passed-now-fails'}, root / 'nightly')
                emitted = json.loads(Path(result['scenario']).read_text())
                self.assertEqual(emitted['build']['profile_files'], list(current['profile']))
                self.assertEqual(result['state'], 'ready-not-launched')


class BuildAndReportNames(unittest.TestCase):
    def test_frozen_old_profile_config_maps_to_both_selected_checkouts(self):
        for project in PROJECTS:
            with self.subTest(project=project), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); wt = root / 'checkout'; checkout(wt, project)
                profile = root / 'profile'; profile.mkdir(); cpm = root / 'cpm'; cpm.mkdir()
                sdk = root / 'sdk'; sdk.mkdir()
                files = ['hookshot_pieces.prof', 'hot_funcs.txt', 'hot_order.txt', 'hookshot.ico', 'hookshot.res']
                for name in files:
                    (profile / name).write_text('synthetic frozen input ' + name + '\n')
                config = {'repo': 'fixture-repo', 'worktree': str(wt), 'build_copy': str(root / 'built'),
                          'copy_prefix': str(root / 'snapshot-'), 'python': sys.executable, 'cpm': str(cpm),
                          'profile_source': str(profile), 'profile_files': files, 'reuse_dependencies': True,
                          'release_cache': str(root / 'release'), 'release_setup': str(root / 'setup.txt'),
                          'romfs': 'unused-romfs', 'exefs': 'unused-exefs', 'build_gate': 'fixture-gate',
                          'full_build_lock': 'fixture-lock', 'build_jobs': 16, 'ngx_sdk': str(sdk), 'fresh_build': True}
                original = copy.deepcopy(config); names = Names(project); commands = []
                def capture(argv, **kwargs):
                    if argv[0] == 'cp':
                        return copy_fixture(argv)
                    if argv[0] == 'git':
                        if 'rev-parse' in argv:
                            return 'a' * 40 + '\n'
                        if 'status' in argv or 'checkout' in argv:
                            return ''
                    raise AssertionError('unexpected child, including forge/auth: ' + repr(argv))
                def command(argv, log, **kwargs):
                    commands.append(argv)
                    game = Path(config['build_copy']); game.mkdir()
                    for name in (names.executable, names.renderer):
                        (game / name).write_bytes(b'synthetic built marker')
                    Path(log).write_text('version: v1-fixture (source 1234abcd)\n@@done ' + str(game) + '\n')
                    return 0
                with patch.object(builder, 'capture', side_effect=capture), \
                     patch.object(builder, 'command', side_effect=command), \
                     patch.object(builder, 'toolchain', return_value={'fixture': True}), \
                     patch.object(builder, 'stamp', return_value='fixture-time'), \
                     patch.object(monitor, 'wait_for_player_idle'):
                    result = builder.run(config, root / 'record', 'a' * 40)
                self.assertEqual(config, original)
                self.assertEqual(result['status'], 'passed')
                self.assertEqual(set(result['binary_sha256']), {names.executable, names.renderer})
                self.assertEqual(set(result['profile']), {names.profile, 'hot_funcs.txt', 'hot_order.txt'})
                for filename in files:
                    mapped = names.project_file(filename)
                    self.assertEqual((wt / 'work' / mapped).read_bytes(), (profile / filename).read_bytes())
                self.assertEqual(len(commands), 1)
                self.assertIn('--no-prepare-shaders', commands[0])
                self.assertIn('fixture-gate', commands[0])
                self.assertIn('flock', commands[0])
                for suffix, value in {'LIFT_JOBS': '8', 'KEEP_BUILD_FILES': '1', 'NGX_SDK': str(sdk)}.items():
                    self.assertIn(names.env_key(suffix) + '=' + value, commands[0])
                    other = Names('longshot' if project == 'hookshot' else 'hookshot').env_key(suffix)
                    self.assertFalse(any(str(arg).startswith(other + '=') for arg in commands[0]))

    def test_draft_headings_follow_copy_and_tracker_arguments_stay_unchanged(self):
        for project in PROJECTS:
            with self.subTest(project=project), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); game = root / 'copy'; game.mkdir(); names = Names(project)
                (game / names.executable).touch()
                config = {'repository': 'NotRexed/Hookshot', 'drafts': str(root / 'drafts')}
                finding = {'scenario': 'fixture', 'kind': 'log-signature', 'log': None}
                response = SimpleNamespace(returncode=0, stdout='[]', stderr='')
                with patch.object(report.subprocess, 'run', return_value=response) as child, \
                     patch.object(report, 'stamp', return_value='fixture-time'):
                    report.enrich(config, 'fixture', {'commit': 'a' * 40, 'copy': str(game)}, finding,
                                  {'id': 'fixture'}, None, root / 'evidence')
                draft = Path(finding['draft']).read_text()
                self.assertIn('### ' + project.title() + ' version', draft)
                self.assertIn('### ' + names.log, draft)
                self.assertIn('See the scenario evidence directory', draft)
                calls = [call.args[0] for call in child.call_args_list]
                self.assertEqual([argv[:3] for argv in calls], [['gh', 'search', 'issues'], ['gh', 'pr', 'list']])
                self.assertTrue(all(argv[argv.index('--repo') + 1] == 'NotRexed/Hookshot' for argv in calls))


if __name__ == '__main__':
    unittest.main()
