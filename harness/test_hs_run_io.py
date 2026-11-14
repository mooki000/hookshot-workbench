"""SYNTHETIC persistence errors only; inherited committed tests remain unmodified."""
import importlib.util
import json
import os
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('committed_tests', Path(__file__).with_name('test_hs_run.py'))
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)


class OutputIOTests(original.HarnessExitTests):
    def setUp(self):
        super().setUp()
        for name in ('gh', 'git', 'ssh', 'curl', 'wget'):
            self.executable(self.bin / name, '#!/bin/sh\n# SYNTHETIC external access denied\nexit 99\n')

    def run_wrapper(self, args):
        result = super().run_wrapper(args)
        # Preserve observations before the upstream fixture's registered cleanup.
        stage = os.environ.get('HS_RUN_STAGE', 'probe')
        destination = original.ROOT / 'evidence' / stage
        runs = self.copy / 'runs'
        observation = {
            'wrapper_exit': result.returncode,
            'summary_files': [str(p.relative_to(self.root)) for p in runs.rglob('summary.txt') if p.is_file()] if runs.exists() else [],
            'summary_directories': [str(p.relative_to(self.root)) for p in runs.rglob('summary.txt') if p.is_dir()] if runs.exists() else [],
            'runs_log_is_directory': (self.harness / 'runs.log').is_dir(),
            'runs_log_is_file': (self.harness / 'runs.log').is_file(),
        }
        (destination / (self._testMethodName + '.observation.json')).write_text(json.dumps(observation, indent=2) + '\n')
        return result

    def test_summary_write_failure_is_not_success_or_appended(self):
        self.gate()
        gate = self.home / 'Games/hs-gpu-slot'
        gate.write_text(gate.read_text() + '\nmkdir "$R/summary.txt"\n')
        result = self.run_wrapper([self.copy, 'summary-directory', '2'])
        self.assertIn('Is a directory', result.stderr)
        self.assertEqual(result.returncode, 1, 'summary write failure was hidden')
        self.assertEqual((self.copy / 'runs/summary-directory/exit.txt').read_text().strip(), '23')
        self.assertFalse((self.harness / 'runs.log').exists(), 'failed summary must not be appended')

    def test_log_append_failure_is_not_wrapper_success(self):
        self.gate()
        (self.harness / 'runs.log').mkdir()
        result = self.run_wrapper([self.copy, 'log-directory', '2'])
        self.assertIn('Is a directory', result.stderr)
        self.assertEqual(result.returncode, 1, 'log append failure was hidden')
        self.assertEqual((self.copy / 'runs/log-directory/exit.txt').read_text().strip(), '23')
        self.assertIn('exit=23', (self.copy / 'runs/log-directory/summary.txt').read_text())


    def test_tee_failure_preserves_nonstandard_exit_and_does_not_append(self):
        self.gate()
        self.executable(self.bin / 'tee', '#!/bin/sh\n/usr/bin/tee "$@"\nexit 37\n')
        result = self.run_wrapper([self.copy, 'tee-failure', '2'])
        self.assertEqual(result.returncode, 37, 'tee status was lost or normalized')
        self.assertIn('exit=23', (self.copy / 'runs/tee-failure/summary.txt').read_text())
        self.assertFalse((self.harness / 'runs.log').exists(), 'failed tee must not be appended')


if __name__ == '__main__':
    unittest.main(verbosity=2)
