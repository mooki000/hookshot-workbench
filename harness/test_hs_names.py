#!/usr/bin/env python3
"""Game-free dual-name checks: synthetic copies only, never a GPU or a gate bypass."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hs_names as names


def module(label, path):
    spec = importlib.util.spec_from_file_location(label, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class NamesTests(unittest.TestCase):
    def test_copy_families_and_windows_markers(self):
        for project in names.PROJECTS:
            for suffix in ('', '.exe'):
                with self.subTest(project=project, suffix=suffix), tempfile.TemporaryDirectory() as td:
                    (Path(td) / (project + suffix)).touch()
                    got = names.copy_names(td)
                    self.assertEqual(got.project, project)
                    self.assertEqual(got.renderer, project + '_vk.so')
                    self.assertEqual(got.rotated_log, project + '_log.1.txt')
                    self.assertIn('mkw_vk_pipelines.bin', got.cache_files)

    def test_unknown_and_mixed_copies_refuse(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(names.NameError, 'unknown'):
                names.copy_names(td)
            for project in names.PROJECTS:
                (Path(td) / project).touch()
            with self.assertRaisesRegex(names.NameError, 'mixed'):
                names.copy_names(td)

    def test_checkout_markers_not_folder_spelling(self):
        for project in names.PROJECTS:
            with tempfile.TemporaryDirectory(prefix='unrelated-') as td:
                root = Path(td); (root / 'scripts').mkdir()
                (root / 'scripts' / (project + '_ctl.py')).touch()
                self.assertEqual(names.checkout_names(root).project, project)
                other = next(p for p in names.PROJECTS if p != project)
                (root / 'scripts' / (other + '_ctl.py')).touch()
                with self.assertRaisesRegex(names.NameError, 'mixed'):
                    names.checkout_names(root)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(names.NameError, 'unknown'):
                names.checkout_names(td)

    def test_pre_control_checkout_uses_explicit_build_output(self):
        for project in names.PROJECTS:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td); (root / 'nx2').mkdir()
                (root / 'nx2/build.py').write_text(f'EXE = "{project}" if LINUX else "{project}.exe"\n')
                self.assertEqual(names.checkout_names(root).project, project)
                (root / 'scripts').mkdir()
                other = next(p for p in names.PROJECTS if p != project)
                (root / 'scripts' / (other + '_ctl.py')).touch()
                with self.assertRaisesRegex(names.NameError, 'mixed'):
                    names.checkout_names(root)

    def test_log_discovery_waits_but_never_guesses(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertIsNone(names.log_path(root))
            with self.assertRaises(names.NameError):
                names.log_path(root, required=True)
            for project in names.PROJECTS:
                path = root / names.Names(project).log
                path.touch()
                self.assertEqual(names.log_path(root), path)
                path.unlink()
            for project in names.PROJECTS:
                (root / names.Names(project).log).touch()
            with self.assertRaisesRegex(names.NameError, 'mixed'):
                names.log_path(root)

    def test_build_variables_require_real_source_reads(self):
        for project in names.PROJECTS:
            with self.subTest(project=project), tempfile.TemporaryDirectory() as td:
                root = Path(td); (root / 'scripts').mkdir(); (root / 'nx2').mkdir()
                (root / 'scripts' / (project + '_ctl.py')).touch()
                key = project.upper() + '_LIFT_JOBS'
                source = root / 'nx2/build.py'
                source.write_text('import os\nx = os.environ.get(' + repr(key) + ')\n')
                self.assertEqual(names.build_env(root, {'LIFT_JOBS': 8}), {key: '8'})
                source.write_text('# ' + key + '\n')
                with self.assertRaises(names.NameError):
                    names.build_env(root, {'LIFT_JOBS': 8})
                source.write_text('import os\nx = os.getenv("HOOKSHOT_LIFT_JOBS")\ny = os.getenv("LONGSHOT_LIFT_JOBS")\n')
                with self.assertRaisesRegex(names.NameError, 'mixed'):
                    names.build_env(root, {'LIFT_JOBS': 8})

    def test_environment_headers_use_actual_source_files(self):
        for project in names.PROJECTS:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td); folder = root / 'gpu/vk/compat'; folder.mkdir(parents=True)
                with self.assertRaisesRegex(names.NameError, 'unknown'):
                    names.environment_header(root)
                header = folder / (project + '_env.h'); header.touch()
                self.assertEqual(names.environment_header(root), header)
                other = next(p for p in names.PROJECTS if p != project)
                (folder / (other + '_env.h')).touch()
                with self.assertRaisesRegex(names.NameError, 'mixed'):
                    names.environment_header(root)

    def test_fixture_crt_policy_recognizes_both_headers(self):
        base = HERE if (HERE / 'skills').is_dir() else HERE.parent
        script = base / 'skills/hookshot-pr-evidence/scripts/hs_win_fixture_check.py'
        if not script.is_file():
            script = Path('/home/mooki/.agents/skills/hookshot-pr-evidence/scripts/hs_win_fixture_check.py')
        checker = module('compat_windows_fixture', script)
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'fake.c'
            for project in names.PROJECTS:
                text = f'#include "{project}_env.h"\n'
                source.write_text(text)
                self.assertIn(project + '_env.h', checker.crt_policy(source, ['clang', str(source)])[0])
                source.write_text('#define _CRT_SECURE_NO_WARNINGS\n' + text)
                self.assertEqual(checker.crt_policy(source, ['clang', str(source)]), [])

    def test_local_toolchain_input_accepts_either_but_not_both(self):
        self.assertIsNone(names.environment_value({}, 'MINGW'))
        for project in names.PROJECTS:
            self.assertEqual(names.environment_value({project.upper() + '_MINGW': '/fixture/sdk'}, 'MINGW'), '/fixture/sdk')
        with self.assertRaisesRegex(names.NameError, 'mixed'):
            names.environment_value({'HOOKSHOT_MINGW': '/old', 'LONGSHOT_MINGW': '/new'}, 'MINGW')

    def test_profile_mapping_is_narrow(self):
        for project in names.PROJECTS:
            obj = names.Names(project)
            for old in names.PROJECTS:
                for suffix in ('_pieces.prof', '.ico', '.res'):
                    self.assertEqual(obj.project_file(old + suffix), project + suffix)
            self.assertEqual(obj.project_file('hot_funcs.txt'), 'hot_funcs.txt')
            self.assertEqual(obj.project_file('hookshot-private.json'), 'hookshot-private.json')

    def test_protected_paths_cover_both_families(self):
        paths = set(names.protected_user_paths('/fixture'))
        for project in names.PROJECTS:
            self.assertIn(Path('/fixture/.local/share') / project, paths)
            self.assertIn(Path('/fixture/.config') / project, paths)
            self.assertIn(Path('/fixture/Games') / project.title(), paths)

    def test_shell_uses_same_detector(self):
        for project in names.PROJECTS:
            with tempfile.TemporaryDirectory(prefix="quoted-' space-") as td:
                (Path(td) / project).touch()
                got = subprocess.run(['bash', '-c', 'source "$1/hs-names.sh"; hs_names copy "$2" || exit; printf "%s %s %s\\n" "$HS_EXE" "$HS_LOG" "$HS_ENV_PREFIX"',
                                      'names-test', str(HERE), td], text=True, capture_output=True)
                self.assertEqual(got.returncode, 0, got.stderr)
                self.assertEqual(got.stdout.strip(), project + ' ' + project + '_log.txt ' + project.upper())
                (Path(td) / next(p for p in names.PROJECTS if p != project)).touch()
                bad = subprocess.run(['bash', '-c', 'source "$1/hs-names.sh"; hs_names copy "$2"',
                                      'names-test', str(HERE), td], text=True, capture_output=True)
                self.assertEqual(bad.returncode, 2)
                self.assertIn('mixed', bad.stderr)

    def test_mixed_copy_artifacts_refuse(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'longshot').touch()
            (root / 'hookshot_vk.so').touch()
            with self.assertRaisesRegex(names.NameError, 'mixed'):
                names.copy_names(root)

    def test_high_resolution_check_reads_selected_options(self):
        source = (HERE / 'hs-run.sh').read_text()
        functions = source[source.index('size_large()'):source.index('# A windowed run also')]
        for project in names.PROJECTS:
            with self.subTest(project=project), tempfile.TemporaryDirectory() as td:
                root = Path(td); (root / project).touch()
                option = root / (project + '_options.ini')
                for resolution, expected in (('1920x1080', 1), ('3840x2160', 0)):
                    option.write_text('output_res=' + resolution + '\n')
                    script = 'source "$1/hs-names.sh"; hs_names copy "$2" || exit\n' + functions + '\npicture_large "$2"'
                    env = {k: v for k, v in os.environ.items() if k != 'HOS_OUTPUT_RES'}
                    got = subprocess.run(['bash', '-c', script, 'picture-test', str(HERE), td], env=env,
                                         capture_output=True, text=True)
                    self.assertEqual(got.returncode, expected, got.stderr)

    def test_save_helper_refuses_both_protected_families(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); source = root / 'source'; (source / 'save/fixture-user').mkdir(parents=True)
            for project in names.PROJECTS:
                target = root / '.local/share' / project / 'owned-looking-target'
                got = subprocess.run(['bash', str(HERE / 'hs-save.sh'), str(source), str(target)],
                                     env={**os.environ, 'HOME': td}, text=True, capture_output=True)
                self.assertEqual(got.returncode, 2, got.stderr)
                self.assertIn('refusing to write', got.stderr)
                self.assertFalse(target.exists())

    def test_shell_inner_uses_only_selected_executable_and_log(self):
        # --inner executes a two-line shell fake, not a game; no GPU API or device is used.
        for project in names.PROJECTS:
            with self.subTest(project=project), tempfile.TemporaryDirectory() as td:
                root = Path(td); run = root / 'run'; run.mkdir()
                exe = root / project
                exe.write_text('#!/bin/sh\nprintf "updates=%s/%s\\n" "${HOOKSHOT_NO_UPDATE_CHECK-}" "${LONGSHOT_NO_UPDATE_CHECK-}"\n')
                exe.chmod(0o700)
                for file, text in {'copy.txt': str(root), 'seconds.txt': '1', 'input.txt': '', 'extra-env.txt': '', 'memmax': ''}.items():
                    (run / file).write_text(text)
                env = {key: value for key, value in os.environ.items() if not key.startswith(('HOS_', 'HOOKSHOT_', 'LONGSHOT_', 'RX_'))}
                got = subprocess.run(['bash', str(HERE / 'hs-run.sh'), '--inner', str(run)], env=env, text=True, capture_output=True)
                self.assertEqual(got.returncode, 0, got.stderr)
                self.assertEqual((run / 'exit.txt').read_text().strip(), '0')
                log = names.log_path(run, required=True)
                self.assertEqual(log.name, project + '_log.txt')
                self.assertEqual(log.read_text().strip(), 'updates=1/' if project == 'hookshot' else 'updates=/1')
                self.assertIn(project.upper() + '_NO_UPDATE_CHECK=1', (run / 'env-used.txt').read_text())

    def test_summary_is_name_neutral(self):
        runner = HERE / 'regress/run_case.py'
        if not runner.exists():
            runner = HERE.parent / 'regress/run_case.py'
        code = (HERE / 'hs-run.sh').read_text().split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
        summaries = []
        for project in names.PROJECTS:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                for file, text in {'exit.txt': '124', 'wall.txt': '1', 'load-before.txt': '0 0 0', 'load-after.txt': '0 0 0',
                        'start.txt': '2026-11-12 10:00:00', 'end.txt': '2026-11-12 10:00:01',
                        project + '_log.txt': project + ' v0-fixture\nvk: device 0: NVIDIA GeForce RTX 4090, fake\n',
                        'present.csv': 'frame,return_ns,interval_ns\n1,1000000000,10000000\n2,1010000000,10000000\n3,1020000000,10000000\n'}.items():
                    (root / file).write_text(text)
                out = io.StringIO()
                with patch.object(sys, 'argv', ['summary', str(root), '0', 'dry-check', '', '0', str(runner)]), \
                     patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')), contextlib.redirect_stdout(out):
                    exec(compile(code, 'summary-dry-check', 'exec'), {})
                summaries.append(out.getvalue())
        self.assertEqual(summaries[0], summaries[1])
        self.assertIn('frames=2(game)', summaries[0])
        print('UNCHANGED DRY CHECK: ' + summaries[0].strip())

    def test_perf_headers_both_names(self):
        folder = HERE / 'perf'
        if not folder.exists():
            folder = HERE.parent / 'perf'
        fixture = module('compat_perf_fixture', folder / 'test_hs_perf.py')
        old = fixture.perf.parse_perf(fixture.fixture())
        new = fixture.perf.parse_perf(fixture.fixture().replace('# hookshot_perf,1,', '# longshot_perf,1,', 1))
        self.assertEqual(old.rows, new.rows)
        with self.assertRaises(fixture.perf.PerfError):
            fixture.perf.parse_perf(fixture.fixture().replace('# hookshot_perf,1,', '# unknown_perf,1,', 1))

    def test_pilot_log_and_process_identity_both_names(self):
        folder = HERE / 'playthrough'
        if not folder.exists():
            folder = HERE.parent / 'play'
        for filename in ('hsap.py', 'hsap_file.py'):
            driver = module('compat_' + filename[:-3], folder / filename)
            for project in names.PROJECTS:
                with self.subTest(filename=filename, project=project), tempfile.TemporaryDirectory() as td:
                    game = driver.Game.__new__(driver.Game)
                    game.R = Path(td); game.log_tail = None; game.log_lines = []; game.presents = 0
                    game._messages = lambda: []
                    game.state_tail = type('Empty', (), {'lines': lambda self: []})()
                    game.state_out = io.StringIO()
                    (game.R / (project + '_log.txt')).write_text('vk: 42 presents, fake\n')
                    game.poll()
                    self.assertEqual(game.presents, 42)
                    data = ('/fixture/' + project + '\0--user\0' + str(game.R / 'user') + '\0').encode()
                    with patch.object(os, 'listdir', return_value=['123']), patch.object(Path, 'read_bytes', return_value=data):
                        self.assertEqual(game.game_pids(), [123])
                    other = next(p for p in names.PROJECTS if p != project)
                    (game.R / (other + '_log.txt')).touch()
                    game.log_tail = None
                    with self.assertRaises(names.NameError):
                        game.poll()


if __name__ == '__main__':
    unittest.main(verbosity=2)
