"""Game-free hs-run regression oracles; all artifacts are SYNTHETIC.

No upstream gate, game, driver, journal or systemd command is executed. The
outer wrapper is relocated by literal path replacement only. The inner wrapper
is executed unchanged with a shell double, not a game binary. Subprocesses get
an explicit environment whitelist, private HOME/TMPDIR and bounded deadlines.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(os.environ.get('HS_RUN_TEST_ROOT', tempfile.gettempdir()))
SOURCE = Path(os.environ.get('HS_RUN_SOURCE', str(Path(__file__).resolve().parents[1])))


class HarnessExitTests(unittest.TestCase):
    def setUp(self):
        fixtures = ROOT / 'fixtures'
        fixtures.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='SYNTHETIC-', dir=fixtures)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.harness = self.home / 'Games/.hookshot-harness'
        self.harness.mkdir(parents=True)
        self.copy = self.home / 'Games/.hookshot-h-SYNTHETIC'
        self.copy.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = {'PATH': str(self.bin) + ':/usr/bin:/bin', 'HOME': str(self.home),
                    'TMPDIR': str(self.root), 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1',
                    'SYNTHETIC_JOURNAL_ARGS': str(self.root / 'journal-arguments.txt')}
        self.wrapper = self.harness / 'hs-run.sh'
        text = (SOURCE / 'harness/hs-run.sh').read_text()
        self.wrapper.write_text(text.replace('/home/mooki', str(self.home)))
        self.wrapper.chmod(0o700)
        # Stage the real reader and its naming helpers, not a partial API stub.
        for name in ('hs-names.sh', 'hs_names.py'):
            shutil.copy2(SOURCE / 'harness' / name, self.harness / name)
        (self.harness / 'regress').mkdir()
        shutil.copy2(SOURCE / 'regress/run_case.py', self.harness / 'regress/run_case.py')
        # The installed reader also imports its optional checkpoint implementation.
        checkpoint = SOURCE / 'regress/hs_checkpoint.py'
        if checkpoint.is_file():
            shutil.copy2(checkpoint, self.harness / 'regress/hs_checkpoint.py')
        # Both published and installed gate names resolve to the same synthetic gate.
        (self.home / 'Games/nx2navi-gpu-slot').symlink_to('hs-gpu-slot')
        self.executable(self.bin / 'journalctl',
                        '#!/bin/sh\n# SYNTHETIC: no host journal read\n'
                        'printf "%s\\n" "$@" > "$SYNTHETIC_JOURNAL_ARGS"\nexit 0\n')
        self.executable(self.bin / 'systemd-run', '#!/bin/sh\n# SYNTHETIC deny: no service or scope access\nexit 99\n')
        self.executable(self.copy / 'hookshot', '#!/bin/sh\n# SYNTHETIC shell double, no game or GPU\nexit 23\n')
        # Empty guard markers only; not game content.
        (self.copy / 'image.bin').touch()
        (self.copy / 'romfs').mkdir()

    def executable(self, path, text):
        path.write_text(text)
        path.chmod(0o700)

    def run_wrapper(self, args):
        self.assertEqual(shutil.which('journalctl', path=self.env['PATH']), str(self.bin / 'journalctl'))
        result = subprocess.run([str(self.wrapper), *map(str, args)], env=self.env,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=8)
        stage = os.environ.get('HS_RUN_STAGE', 'probe')
        logs = ROOT / 'evidence' / stage
        logs.mkdir(parents=True, exist_ok=True)
        (logs / (self._testMethodName + '.stdout')).write_text(result.stdout)
        (logs / (self._testMethodName + '.stderr')).write_text(result.stderr)
        (logs / (self._testMethodName + '.exit')).write_text(str(result.returncode) + '\n')
        receipt_dir = logs / (self._testMethodName + '.receipts')
        receipt_dir.mkdir(exist_ok=True)
        for name in ('exit.txt', 'wall.txt', 'hookshot_log.txt', 'summary.txt', 'env-used.txt',
                     'kernel.txt', 'kernel-status.json', 'journal-arguments.txt'):
            for path in self.root.rglob(name):
                if path.is_file():
                    (receipt_dir / name).write_bytes(path.read_bytes())
        journal = self.root / 'journal-arguments.txt'
        if str(args[0]) != '--inner' and (self.copy / 'runs' / str(args[1]) / 'exit.txt').is_file():
            run = self.copy / 'runs' / str(args[1])
            self.assertEqual(journal.read_text().splitlines(),
                             ['-k', '--since', (run / 'start.txt').read_text().strip(),
                              '--until', (run / 'end.txt').read_text().strip(),
                              '--no-pager', '--output=short-iso'])
        else:
            self.assertFalse(journal.exists(), 'inner/refused run must not read the journal')
        return result

    def gate(self, malformed=False):
        # A stand-in for the gate's reporting contract, not an actual gate.
        body = '''#!/bin/bash
# SYNTHETIC receipts only: do not start any process or access hardware.
R=${@: -1}
printf '23\\n' > "$R/exit.txt"
printf '2026-01-01 00:00:00\\n' > "$R/start.txt"
printf '2026-01-01 00:00:01\\n' > "$R/end.txt"
printf '0.0\\n' > "$R/wall.txt"
printf '0 0 0\\n' > "$R/load-before.txt"
printf '0 0 0\\n' > "$R/load-after.txt"
printf 'SYNTHETIC no game or GPU\\n' > "$R/hookshot_log.txt"
'''
        if malformed:
            body += '''printf 'frame,time_s,frametime_ms\\n1,SYNTHETIC_BAD,16.7\\n' > "$R/frames.csv"
'''
        self.executable(self.home / 'Games/hs-gpu-slot', body)

    def test_inner_records_game_exit_without_propagating_it(self):
        run = self.root / 'inner'
        run.mkdir()
        for name, text in {'copy.txt': str(self.copy), 'seconds.txt': '2', 'input.txt': '',
                           'extra-env.txt': '', 'memmax': '', 'env-file.txt': ''}.items():
            (run / name).write_text(text + '\n' if text else '')
        result = self.run_wrapper(['--inner', run])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((run / 'exit.txt').read_text().strip(), '23')
        self.assertTrue((run / 'end.txt').exists())

    def test_outer_reports_game_failure_without_changing_wrapper_contract(self):
        self.gate()
        result = self.run_wrapper([self.copy, 'reported', '2'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('exit=23', result.stdout)
        self.assertIn('exit=23', (self.copy / 'runs/reported/summary.txt').read_text())

    def test_outer_summary_failure_must_not_report_wrapper_success(self):
        self.gate(malformed=True)
        result = self.run_wrapper([self.copy, 'bad-summary', '2'])
        self.assertIn('ValueError', result.stderr)
        self.assertNotEqual(result.returncode, 0,
                            'summary generation failed but hs-run claimed wrapper success')
        self.assertEqual((self.copy / 'runs/bad-summary/exit.txt').read_text().strip(), '23')
        self.assertFalse((self.harness / 'runs.log').exists(), 'failed summary was appended')

    def test_gate_failure_without_game_receipt_propagates(self):
        self.executable(self.home / 'Games/hs-gpu-slot', '#!/bin/sh\n# SYNTHETIC refusal\nexit 7\n')
        result = self.run_wrapper([self.copy, 'refused', '2'])
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertIn('gate did not run the game', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
