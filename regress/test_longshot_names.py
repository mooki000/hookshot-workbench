#!/usr/bin/env python3
"""Game-free name compatibility: synthetic copies/checkouts and intercepted children only."""
from pathlib import Path
from types import SimpleNamespace
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_case as builder
import run_case as runner
from hs_names import ALL_SETTINGS, NameError, Names, PROJECTS


def checkout(root, project):
    (root / 'scripts').mkdir(parents=True)
    (root / 'scripts' / (project + '_ctl.py')).write_text('IDENTITY = ' + repr(project) + '\n')
    (root / 'nx2').mkdir()
    (root / 'nx2' / 'build.py').write_text('import os\n' + ''.join(
        'os.environ.get(' + repr(project.upper() + '_' + suffix) + ')\n'
        for suffix in ('LIFT_JOBS', 'KEEP_BUILD_FILES', 'NGX_SDK')))


class ReaderNames(unittest.TestCase):
    def trace(self, root):
        (root / 'frames.csv').write_text('frame,time_s,frametime_ms\n' + ''.join(
            f'{i},{i / 100:.2f},{10 if i else 0}\n' for i in range(501)))

    def test_both_logs_and_versions(self):
        for project in PROJECTS:
            with self.subTest(project=project), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.trace(root)
                version = project + ' v1-fixture (source 1234abcd)'
                (root / Names(project).log).write_text(version + '\n'
                    'vk: 240 presents, 100.0 fps; a frame: 42 draws\n'
                    'vk: GPU time 3.2 ms a frame\n')
                values, blocks = runner.analyse(root, {'anchor': 'first', 'from': 0, 'to': 4})
                self.assertEqual(values['log_gpu_ms'], 3.2)
                self.assertEqual(len(blocks), 1)
                self.assertEqual(runner.LOG_VERSION.search(version)[0], version)

    def test_missing_log_remains_missing_and_mixed_logs_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.trace(root)
            settings = {'anchor': 'first', 'from': 0, 'to': 4}
            values, blocks = runner.analyse(root, settings)
            self.assertNotIn('log_blocks_in_window', values)
            self.assertEqual(blocks, [])
            for project in PROJECTS:
                (root / Names(project).log).touch()
            with self.assertRaises(NameError):
                runner.analyse(root, settings)

    def test_setup_excludes_both_settings_families_without_guessing_identity(self):
        for name in ALL_SETTINGS:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                game = root / 'copy'; game.mkdir()
                (game / name).write_text('synthetic setting\n')
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaisesRegex(AssertionError, name):
                        runner.setup(game, root / 'run', {}, 1)
                self.assertFalse((root / 'run').exists())

    def test_launch_requires_one_known_copy_before_wrapper(self):
        for markers in ((), PROJECTS):
            with self.subTest(markers=markers), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                game = root / '.hookshot-h-fixture'; game.mkdir()
                for marker in markers:
                    (game / marker).touch()
                with patch.dict(os.environ, {runner.GATE_MARK: 'synthetic-held-worker'}, clear=True), \
                     patch.object(runner.subprocess, 'Popen') as child:
                    with self.assertRaises(NameError):
                        runner.run_case(game, root / 'run', {'run': {}}, 1, 'fixture')
                child.assert_not_called()

    def test_run_monitor_and_receipt_follow_copy_identity(self):
        for project in PROJECTS:
            with self.subTest(project=project), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                game = root / '.hookshot-h-fixture'; game.mkdir()
                names = Names(project)
                (game / names.executable).write_text('not an executable\n')
                (game / 'regress-source.txt').write_text('1234abcd\n')
                out = root / 'run'
                child = Mock(pid=123, returncode=0)
                child.poll.side_effect = [None, 0]
                child.wait.return_value = 0
                def launch(*args, **kwargs):
                    (out / names.log).write_text(project + ' v1-fixture (source 1234abcd)\n'
                        'vk: device 0: synthetic device\n@@done shaders\n')
                    for name, value in {'exit.txt': '0', 'wall.txt': '1',
                                        'load-before.txt': '0 0 0', 'load-after.txt': '0 0 0'}.items():
                        (out / name).write_text(value)
                    return child
                kernel = {'kernel_errors': 0, 'no_memory_events': 0, 'no_memory_lines': 0}
                scenario = {'kind': 'game-check', 'measure': {'name': 'prepared', 'direction': 'pass-fail'},
                            'run': {'prepare': True, 'device_pattern': 'synthetic device'}}
                with patch.dict(os.environ, {runner.GATE_MARK: 'synthetic-held-worker'}, clear=True), \
                     patch.object(runner.subprocess, 'Popen', side_effect=launch), \
                     patch.object(runner, 'game_pid', return_value=123) as pid_lookup, \
                     patch.object(runner, 'sample', return_value={'load': 0}), \
                     patch.object(runner, 'date', return_value='fixture-time'), \
                     patch.object(runner, 'kernel_lines', return_value=('', kernel)), \
                     patch.object(runner.signal, 'signal'):
                    result = runner.run_case(game, out, scenario, 1, 'a' * 40)
                pid_lookup.assert_called_once_with(123, game / names.executable)
                self.assertTrue(result['valid'], result.get('problem'))
                self.assertTrue(result['version'].startswith(project + ' '))
                self.assertEqual(json.loads((out / 'result.json').read_text())['source_expected'], '1234abcd')


class BuilderNames(unittest.TestCase):
    def test_profile_sources_map_both_directions_without_config_renames(self):
        for source_project, target_project in (('hookshot', 'longshot'), ('longshot', 'hookshot')):
            with self.subTest(source=source_project), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source_names, target_names = Names(source_project), Names(target_project)
                (root / source_names.profile).touch()
                files = [source_names.profile, 'hot_funcs.txt', 'hot_order.txt',
                         source_project + '.ico', source_project + '.res', 'unchanged.dat']
                pairs = builder.profile_inputs(root, target_names, files)
                self.assertEqual([path.name for path, _ in pairs], files)
                self.assertEqual([name for _, name in pairs], [target_names.profile, 'hot_funcs.txt',
                    'hot_order.txt', target_project + '.ico', target_project + '.res', 'unchanged.dat'])
                self.assertEqual(builder.profile_inputs(root, target_names)[0], (root / source_names.profile, target_names.profile))

    def test_unspecified_profile_identity_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(NameError):
                builder.profile_inputs(root, Names('longshot'))
            for project in PROJECTS:
                (root / Names(project).profile).touch()
            with self.assertRaises(NameError):
                builder.profile_inputs(root, Names('longshot'))
            with self.assertRaises(NameError):
                builder.profile_inputs(root, Names('longshot'), [Names(p).profile for p in PROJECTS])

    def test_full_build_command_uses_selected_source_keys_and_output_names(self):
        for project in PROJECTS:
            with self.subTest(project=project), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                wt = root / 'checkout'; checkout(wt, project)
                profile = root / 'profile'; profile.mkdir()
                for name in ('hookshot_pieces.prof', 'hot_funcs.txt', 'hot_order.txt'):
                    (profile / name).write_text('synthetic profile input\n')
                out = root / 'records/row'; out.mkdir(parents=True)
                config = {'game_root': str(root / 'copies'), 'profile_dir': str(profile), 'cpm': 'fixture-cpm',
                          'python': sys.executable, 'romfs': 'unused-romfs', 'exefs': 'unused-exefs',
                          'ngx_sdk': 'fixture-sdk', 'build_gate': 'fixture-build-gate'}
                scenario = root / 'scenario.json'; scenario.write_text(json.dumps({'build': config}))
                names = Names(project)
                observed = {}
                def compile_fixture(argv, **kwargs):
                    observed.update(argv=argv, env=kwargs['env'])
                    game = Path(argv[argv.index('--out') + 1]); game.mkdir(parents=True)
                    for name in (names.executable, names.renderer):
                        (game / name).write_bytes(b'synthetic build output')
                    Path(argv[argv.index('--log') + 1]).write_text('@@done ' + str(game) + '\n')
                    kwargs['stdout'].write('version: v1-fixture (source 1234abcd)\n')
                    return SimpleNamespace(returncode=0)
                support = SimpleNamespace(date=lambda: 'fixture-time', save=lambda path, value:
                                          Path(path).write_text(json.dumps(value)))
                spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
                argv = ['build_case.py', '--worktree', str(wt), '--commit', 'a' * 40, '--output', str(out),
                        '--lift-key', 'fixture', '--build-kind', 'full', '--scenario', str(scenario)]
                with patch.object(sys, 'argv', argv), patch.dict(os.environ, {}, clear=True), \
                     patch.object(builder.importlib.util, 'spec_from_file_location', return_value=spec), \
                     patch.object(builder.importlib.util, 'module_from_spec', return_value=support), \
                     patch.object(builder, 'copy', side_effect=shutil.copy2), \
                     patch.object(builder.subprocess, 'run', side_effect=compile_fixture):
                    builder.main()
                self.assertEqual(observed['argv'][0], 'flock')
                self.assertIn('fixture-build-gate', observed['argv'])
                self.assertIn('--no-prepare-shaders', observed['argv'])
                for suffix, value in {'LIFT_JOBS': '8', 'KEEP_BUILD_FILES': '1', 'NGX_SDK': 'fixture-sdk'}.items():
                    self.assertEqual(observed['env'][names.env_key(suffix)], value)
                    other = Names('longshot' if project == 'hookshot' else 'hookshot').env_key(suffix)
                    self.assertNotIn(other, observed['env'])
                self.assertTrue((wt / 'work' / names.profile).is_file())
                receipt = json.loads((out / 'build.json').read_text())
                self.assertEqual(set(receipt['sha256']), {names.executable, names.renderer})
                self.assertEqual(receipt['source'], '1234abcd')
                self.assertTrue(Path(receipt['copy']).name.startswith('.hookshot-h-regress-'))


if __name__ == '__main__':
    unittest.main()
