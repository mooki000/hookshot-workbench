"""Synthetic hs-run reader regressions: real reader, fake journal and receipt gate.

No game, journal, service or GPU is accessed. Run directly or via unittest
discovery; the source checkout is resolved from this file, with no private env.
"""
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]
FRAMES = 'frame,time_s,frametime_ms\n1,0.00,10\n2,0.01,10\n3,0.02,10\n4,0.03,10\n'
PRESENT3 = 'frame,return_ns,interval_ns\n1,1000000000,10000000\n2,1010000000,10000000\n3,1020000000,10000000\n4,1030000000,10000000\n'
PRESENT7 = '\n'.join(line + (',a,b,c,d' if index == 0 else ',0,0,0,0')
                     for index, line in enumerate(PRESENT3.splitlines())) + '\n'


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux/POSIX shell fixtures only')
class ReaderSummaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='hs-readers-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'synthetic home'
        self.harness = self.home / 'Games/.hookshot-harness'
        self.copy = self.home / 'Games/.hookshot-h-SYNTHETIC'
        self.bin = self.root / 'bin'
        for directory in (self.harness, self.copy, self.bin):
            directory.mkdir(parents=True)
        self.denied = self.root / 'denied.txt'
        self.workload_marker = self.root / 'unexpected-workload.txt'
        self.journal_args = self.root / 'journal-arguments.txt'
        self.env = {
            'PATH': str(self.bin) + ':/usr/bin:/bin', 'HOME': str(self.home),
            'TMPDIR': str(self.root), 'LC_ALL': 'C', 'PYTHONDONTWRITEBYTECODE': '1',
            'SYNTHETIC_HOME': str(self.home), 'SYNTHETIC_DENY_LOG': str(self.denied),
            'SYNTHETIC_WORKLOAD_MARKER': str(self.workload_marker),
            'SYNTHETIC_JOURNAL_ARGS': str(self.journal_args),
        }
        deny = '#!/bin/sh\nprintf "%s\\n" "$0" >> "$SYNTHETIC_DENY_LOG"\nexit 99\n'
        for name in ('systemd-run', 'systemctl', 'service', 'ssh', 'curl', 'wget',
                     'gh', 'git', 'hyprctl', 'nvidia-smi', 'busctl', 'sudo'):
            self.executable(self.bin / name, deny)
        self.executable(self.copy / 'hookshot',
                        '#!/bin/sh\nprintf SYNTHETIC > "$SYNTHETIC_WORKLOAD_MARKER"\nexit 99\n')
        (self.copy / 'image.bin').touch()  # Empty guard marker, not a game image.
        (self.copy / 'romfs').mkdir()
        (self.harness / 'regress').mkdir()
        shutil.copy2(SOURCE / 'regress/run_case.py', self.harness / 'regress/run_case.py')
        # Newer workbench readers share the dual-name helpers; older sources do not.
        for helper in ('hs-names.sh', 'hs_names.py'):
            dependency = SOURCE / 'harness' / helper
            if dependency.is_file():
                shutil.copy2(dependency, self.harness / helper)
        self.wrapper = self.harness / 'hs-run.sh'
        shutil.copy2(SOURCE / 'harness/hs-run.sh', self.wrapper)
        original_mode = stat.S_IMODE(self.wrapper.stat().st_mode)
        # Only relocate the hard-coded home. Environment values remain data,
        # including spaces; none is interpolated into shell source.
        body = self.wrapper.read_text()
        for name in ('hs-gpu-slot', 'hs-intel-slot'):
            body = body.replace('/home/mooki/Games/' + name,
                                '"$SYNTHETIC_HOME/Games/' + name + '"')
        body = body.replace('H=/home/mooki/Games/.hookshot-harness',
                            'H="$SYNTHETIC_HOME/Games/.hookshot-harness"')
        body = body.replace('case "$copy" in /home/mooki/Games/.hookshot-h-*',
                            'case "$copy" in "$SYNTHETIC_HOME"/Games/.hookshot-h-*')
        self.wrapper.write_text(body.replace('/home/mooki', '$SYNTHETIC_HOME'))
        self.assertEqual(stat.S_IMODE(self.wrapper.stat().st_mode), original_mode)
        self.assertTrue(original_mode & stat.S_IXUSR, 'source must preserve its executable Git mode')
        self.journal()

    def executable(self, path, body):
        path.write_text(body)
        path.chmod(0o700)  # Authored fixture only, never a source/frozen script.

    def journal(self, status=0):
        body = '#!/bin/sh\nprintf "%s\\n" "$@" > "$SYNTHETIC_JOURNAL_ARGS"\n'
        if status:
            body += 'printf "SYNTHETIC journal refused\\n" >&2\n'
        self.executable(self.bin / 'journalctl', body + 'exit ' + str(status) + '\n')
        self.assertEqual(shutil.which('journalctl', path=self.env['PATH']), str(self.bin / 'journalctl'))

    def run_summary(self, frames=None, present=None):
        receipts = {
            'exit.txt': '23\n', 'start.txt': '2026-01-01 00:00:00\n',
            'end.txt': '2026-01-01 00:00:01\n', 'wall.txt': '0.0\n',
            'load-before.txt': '0 0 0\n', 'load-after.txt': '0 0 0\n',
            'hookshot_log.txt': 'SYNTHETIC no game or GPU\n',
        }
        if frames is not None:
            receipts['frames.csv'] = frames
        if present is not None:
            receipts['present.csv'] = present
        gate = '#!/bin/bash\n# Synthetic receipts only; no workload launched.\nR=${@: -1}\n'
        for name, text in receipts.items():
            gate += "printf '%s' " + shlex.quote(text) + ' > "$R/' + name + '"\n'
        self.executable(self.home / 'Games/hs-gpu-slot', gate)
        result = subprocess.run([str(self.wrapper), str(self.copy), 'readers', '1'],
                                env=self.env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=8)
        message = 'stdout:\n' + result.stdout + '\nstderr:\n' + result.stderr
        self.assertEqual(result.returncode, 0, message)
        self.assertFalse(self.denied.exists(), message)
        self.assertFalse(self.workload_marker.exists(), 'receipt gate must never launch a workload')
        output = self.copy / 'runs/readers'
        self.assertEqual((output / 'exit.txt').read_text().strip(), '23')
        return result, output, message

    def test_renderer_frames_are_labelled_headless_renderer(self):
        result, _, message = self.run_summary(frames=FRAMES)
        self.assertIn('frames=3(headless-renderer) mean=100.0', result.stdout, message)

    def test_three_column_present_without_renderer(self):
        result, _, message = self.run_summary(present=PRESENT3)
        self.assertIn('frames=3(game) mean=100.0', result.stdout, message)

    def test_header_only_renderer_falls_back_to_seven_column_present(self):
        result, _, message = self.run_summary(frames='frame,time_s,frametime_ms\n', present=PRESENT7)
        self.assertIn('frames=3(game) mean=100.0', result.stdout, message)

    def test_seven_column_present_without_renderer_is_a_positive_control(self):
        result, _, message = self.run_summary(present=PRESENT7)
        self.assertIn('frames=3(game) mean=100.0', result.stdout, message)

    def test_failed_journal_is_unknown_not_zero(self):
        self.journal(status=7)
        result, output, message = self.run_summary()
        self.assertIn('kernel=unknown nvrm=unknown', result.stdout, message)
        self.assertIn('journal=unknown', result.stdout, message)
        info = json.loads((output / 'kernel-status.json').read_text())
        self.assertEqual(info['journal_exit'], 7)
        self.assertIsNone(info['kernel_errors'])
        self.assertIsNone(info['nvrm_lines'])
        self.assertEqual(self.journal_args.read_text().splitlines(),
                         ['-k', '--since', '2026-01-01 00:00:00', '--until',
                          '2026-01-01 00:00:01', '--no-pager', '--output=short-iso'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
