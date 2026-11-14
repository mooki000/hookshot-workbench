"""Synthetic held-run setup; no real game, resource gate or systemd service.

Run from a writable directory. Fixtures are temporary and the short relative
harness path keeps Unix-domain socket names bounded even in deep checkouts.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'nightly'))
import scenario


class SetupComplete(Exception):
    """Stop before Run.execute starts its active monitoring loop."""


class HeldMemmaxTests(unittest.TestCase):
    def test_held_setup_does_not_select_a_memory_scope_without_limit(self):
        temporary = tempfile.TemporaryDirectory(prefix='.held-memmax-', dir='.')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        home, copy, bin_path = root / 'home', root / 'copy', root / 'bin'
        for directory in (home, copy, bin_path):
            directory.mkdir()
        harness = Path(os.path.relpath(root / 'harn', Path.cwd()))
        harness.mkdir()

        def executable(path, body):
            path.write_text(body)
            path.chmod(0o700)

        executable(copy / 'hookshot', '#!/bin/sh\n# SYNTHETIC shell workload, not a game\nexit 23\n')
        denied = root / 'service-denied.txt'
        for name in ('systemd-run', 'systemctl', 'service', 'ssh', 'curl', 'wget',
                     'gh', 'git', 'hyprctl', 'nvidia-smi', 'journalctl', 'busctl'):
            # The receipt path is data in the environment, not shell source.
            executable(bin_path / name,
                       '#!/bin/sh\nprintf "%s %s\\n" "$0" "$*" >> "$SYNTHETIC_DENY_LOG"\nexit 99\n')
        env = {'PATH': str(bin_path) + ':/usr/bin:/bin', 'HOME': str(home),
               'TMPDIR': str(root), 'LC_ALL': 'C', 'HSGATE_IN_GATE': 'SYNTHETIC',
               'SYNTHETIC_DENY_LOG': str(denied)}

        def capture(argv, **kwargs):
            self.assertEqual(list(map(str, argv[:3])), ['cp', '-a', '--reflink=always'])
            shutil.copytree(argv[3], argv[4])
            return ''

        def intercept(argv, **kwargs):
            self.assertEqual(argv, [str(harness / 'hs-run.sh'), '--inner', str(run.run)])
            kwargs['stdout'].close()
            raise SetupComplete

        with patch.dict(os.environ, env, clear=True), \
             patch.object(scenario, 'HARN', harness), \
             patch.object(scenario, 'Monitor', return_value=SimpleNamespace()), \
             patch.object(scenario, 'client_module', return_value=SimpleNamespace()), \
             patch.object(scenario, 'stamp', return_value='SYNTHETIC'), \
             patch.object(scenario, 'resources', return_value={}), \
             patch.object(scenario, 'unsafe', return_value=[]), \
             patch.object(scenario, 'capture', side_effect=capture), \
             patch.object(scenario.subprocess, 'Popen', side_effect=intercept):
            run = scenario.Run({}, copy, {'id': 'synthetic', 'seconds': 30}, root / 'out', 'held', held=True)
            with self.assertRaises(SetupComplete):
                run.execute()

        def inner(label):
            result = subprocess.run(['bash', str(SOURCE / 'harness/hs-run.sh'), '--inner', str(run.run)],
                                    env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
            observed = {'case': label, 'wrapper_exit': result.returncode,
                        'workload_exit': (run.run / 'exit.txt').read_text().strip(),
                        'memmax_hex': (run.run / 'memmax').read_bytes().hex(),
                        'service_denied': denied.read_text() if denied.exists() else ''}
            print(json.dumps(observed, sort_keys=True))
            return observed

        observed = inner('empty-default')
        self.assertEqual(observed['wrapper_exit'], 0)
        self.assertEqual(observed['workload_exit'], '23', 'empty memmax selected a memory scope')
        self.assertEqual((run.run / 'memmax').read_bytes(), b'')
        self.assertFalse(denied.exists(), 'default setup must not attempt service access')

        (run.run / 'memmax').write_text('28G')
        observed = inner('explicit-limit-control')
        self.assertEqual(observed['wrapper_exit'], 0)
        self.assertEqual(observed['workload_exit'], '99')
        self.assertIn('MemoryMax=28G', observed['service_denied'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
