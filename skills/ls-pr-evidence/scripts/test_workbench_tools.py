#!/usr/bin/env python3
"""Game-free tests for the read-only CI reader and fixture compiler capture."""
import ast
import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent

def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

ci = load('hs_pr_ci')
win = load('hs_win_fixture_check')

class Tests(unittest.TestCase):
    def test_log_names_and_ansi(self):
        parsed = ci.failures('2026-11-12T00:00:00Z \x1b[31mFAIL  1.2s  tests/test_a.py\x1b[0m\n==== FAIL tests/test_a.py\nFAIL: test_case (__main__.Class.test_case)\nTIMEOUT 90s nx2/test_b.py\n')
        self.assertEqual(parsed['tests'], ['nx2/test_b.py', 'tests/test_a.py'])
        self.assertEqual(parsed['cases'], ['test_case (__main__.Class.test_case)'])
        self.assertFalse(parsed['release_race'])

    def test_race_needs_exact_case_and_signature(self):
        text = ci.RACE + '\nSHA256SUMS still differs from the files after 5 uploads'
        self.assertTrue(ci.failures(text)['release_race'])
        self.assertFalse(ci.failures(ci.RACE)['release_race'])
        self.assertFalse(ci.failures('test_release_scripts.py failed')['release_race'])

    def test_compiler_flags_preserved(self):
        got = win.compile_args(['gcc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I', 'headers', '-include', 'env.h', '-DNAME=1', 'a.c', 'host.c', 'x.o', '-o', 'test.exe', '-lws2_32'], Path('a.c'), 'clang', Path('/mingw'))
        self.assertIn('-Werror', got)
        self.assertIn('-std=c11', got)
        self.assertEqual(got[-2:], ['-fsyntax-only', 'a.c'])
        self.assertNotIn('x.o', got)
        self.assertNotIn('host.c', got)
        self.assertNotIn('-lws2_32', got)
        self.assertEqual(got[got.index('-include') + 1], 'env.h')

    def test_platform_selection_does_not_mutate_host(self):
        tree = ast.fix_missing_locations(win.WindowsBranches().visit(ast.parse('import os\nresult = os.name == "nt"\n')))
        data = {}
        exec(compile(tree, 'test', 'exec'), data)
        self.assertTrue(data['result'])
        import os
        self.assertEqual(os.name, 'posix')

    def test_cxx_uses_target_standard_library_headers(self):
        got = win.compile_args(['g++', '-std=c++17', '-Werror', 'a.cpp'], Path('a.cpp'), 'clang++', Path('/mingw'))
        self.assertIn('/mingw/include/c++/v1', got)
        self.assertIn('-nostdinc++', got)
        self.assertIn('-Werror', got)
        self.assertLess(got.index('/mingw/include/c++/v1'), got.index('/mingw/include'))

    def test_explicit_source_language_and_forced_include(self):
        argv = ['clang++', '-x', 'c++', '-include', 'forced.c', '-I', 'headers.c', 'fixture.src', '-o', 'output.c']
        self.assertEqual(win.compilation_sources(argv), [Path('fixture.src')])
    def test_runner_resolves_declared_header_prerequisites(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'tests').mkdir()
            (root / 'tests/run_all.py').write_text('def find_tools():\n    return {"vulkan_headers": "/fixture/vulkan"}\n')
            self.assertEqual(win.driver_arguments(root, ['--cxx', '{cxx}', '--headers', '{vulkan_headers}']),
                             ['--cxx', 'clang++', '--headers', '/fixture/vulkan'])
            with self.assertRaisesRegex(RuntimeError, 'prerequisites unavailable: spirv_headers'):
                win.driver_arguments(root, ['--headers', '{spirv_headers}'])


    def test_discovery_names_unmatched_and_generated_fixtures(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'tests').mkdir()
            (root / 'tests/test_static.py').write_text('import subprocess\nfixture="case.c"\n')
            (root / 'tests/test_generated.py').write_text('import subprocess\ncc="gcc"\n')
            (root / 'tests/test_python_only.py').write_text('"""See case.c for the native test."""\nimport subprocess\n')
            with patch.object(win, 'git', side_effect=['tests/case.c\ntests/orphan.cpp\ntests/test_generated.py', '']):
                tests, unmatched = win.selected_tests(root, 'base')
            self.assertEqual(tests, ['tests/test_generated.py', 'tests/test_static.py'])
            self.assertEqual(unmatched, ['tests/orphan.cpp'])

    def test_imported_driver_selects_windows_flags(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'tests').mkdir()
            (root / 'out').mkdir()
            helper = root / 'tests/helper.py'
            helper.write_text('import os\nflags = ["windows"] if os.name == "nt" else ["linux"]\n')
            file = root / 'tests/test_probe.py'
            file.write_text('import importlib.util, subprocess\n'
                            's = importlib.util.spec_from_file_location("helper", ' + repr(str(helper)) + ')\n'
                            'm = importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\n'
                            'assert m.flags == ["windows"]\nsubprocess.run(["runtime-not-executed"])\n')
            win.child(root, file, root / 'out', root)
            result = json.loads((root / 'out/capture.json').read_text())
            self.assertIsNone(result['error'])
            self.assertIn('runtime-not-executed', result['boundary'])

    def test_capture_stops_before_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'tests').mkdir()
            (root / 'out').mkdir()
            file = root / 'tests/test_probe.py'
            file.write_text('import subprocess\nsubprocess.run(["this-command-must-not-exist"])\n')
            win.child(root, file, root / 'out', root)
            import json
            result = json.loads((root / 'out/capture.json').read_text())
            self.assertIn('stopped before', result['boundary'])
            self.assertEqual(result['records'], [])
        # child owns process state in production; restore cwd in this direct unit call.
        import os
        os.chdir(HERE)

    def test_same_main_job_required(self):
        class Fake(ci.Reader):
            def gh(self, *args, **kwargs):
                return {'number': 1, 'headRefOid': 'a', 'baseRefOid': 'b', 'reviewDecision': 'APPROVED', 'state': 'OPEN', 'statusCheckRollup': [{'name': 'Windows', 'conclusion': 'FAILURE', 'detailsUrl': 'https://github.com/a/b/actions/runs/1/job/2'}]}
            def log(self, job):
                return {'tests': ['tests/test_a.py'], 'cases': [], 'release_race': False}
        row = Fake('a/b').pull(1, {('Linux', 'tests/test_a.py'): [{'sha':'b'}]})['failed_checks'][0]
        self.assertEqual(row['also_main'], {})
        row = Fake('a/b').pull(1, {('Windows', 'tests/test_a.py'): [{'sha':'b'}]})['failed_checks'][0]
        self.assertIn('tests/test_a.py', row['also_main'])

if __name__ == '__main__':
    unittest.main()
