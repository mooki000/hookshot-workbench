#!/usr/bin/env python3
"""Exercise cutover only in disposable synthetic homes; never migrate this workstation."""
from pathlib import Path
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cutover
import name_policy
import publish


class CutoverTests(unittest.TestCase):
    def fixture(self, root):
        home = root / 'home'; home.mkdir()
        for old, _ in cutover.renames(home):
            if old.suffix == '.py':
                old.parent.mkdir(parents=True, exist_ok=True); old.write_text('external = "longshot_log.txt"\n')
            else:
                old.mkdir(parents=True, exist_ok=True)
        for live in publish.SKILL_MAP:
            (home / '.agents/skills' / live / 'SKILL.md').write_text('---\nname: ' + live + '\n---\nRead skill://' + live + '\n')
        harness = home / cutover.HOST_NAMES[0][0]
        (harness / 'hs-run.sh').write_text('H=' + str(harness) + '\nGAME=longshot\nWCLASS=hookshot-harness\n')
        checkout = home / cutover.HOST_NAMES[1][0]
        (checkout / 'docs').mkdir(); (checkout / 'docs/names-kept.md').write_text(name_policy.BEGIN + '\n' + name_policy.END)
        skill = checkout / 'skills/ls-workbench'; skill.mkdir(parents=True); (skill / 'SKILL.md').write_text('name: ls-workbench\n')
        (checkout / 'source.py').write_text('HARNESS = "' + str(harness) + '"\nGAME = "longshot"\n')
        (checkout / 'publish.py').write_text("LIVE_LAYOUT = 'legacy'\nH = 'Games/.hookshot-harness'\n")
        env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}
        def git(*args):
            return subprocess.run(['git', '-C', str(checkout), '-c', 'core.hooksPath=/dev/null',
                                   '-c', 'user.name=Michael Schienbein', '-c', 'user.email=7663497+mooki000@users.noreply.github.com',
                                   '-c', 'rerere.enabled=false', '-c', 'commit.gpgsign=false', *args],
                                  env=env, text=True, capture_output=True, check=True)
        git('init', '-q', '--initial-branch=main'); git('add', '.')
        git('commit', '-q', '-m', 'test: synthetic cutover fixture\n\nAssisted-by: GPT-6 Astra\nCo-Authored-By: GPT-6 Astra <noreply@openai.com>')
        linked = root / 'linked'; git('worktree', 'add', '--detach', str(linked))
        locks = [root / 'quiet.lock', root / 'measuring.lock']
        for lock in locks:
            lock.touch()
        return home, checkout, linked, locks

    def fingerprint(self, root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def test_dry_run_names_every_operation_without_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); home, checkout, _, _ = self.fixture(root)
            before = self.fingerprint(root)
            with patch.object(cutover.Path, 'home', return_value=home), patch.object(cutover, 'lock_users', return_value=['fixture waiter']), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(cutover.main(['--dry-run']), 0)
            self.assertEqual(self.fingerprint(root), before)
            moves, rewrites, problems = cutover.plan(home)
            self.assertFalse(problems)
            self.assertEqual(len(moves), 16)
            self.assertEqual(output.getvalue().count('\nRENAME '), len(moves))
            self.assertEqual(output.getvalue().count('\nREWRITE '), len(rewrites))
            self.assertIn(f'GIT git -C {home / cutover.HOST_NAMES[1][1]} worktree repair', output.getvalue())
            self.assertIn('worktree repair', output.getvalue())
            self.assertIn('REFUSE APPLY: lock holder/waiter', output.getvalue())

    def test_kernel_lock_parser_covers_holders_and_blocked_waiters(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); lock = root / 'lock'; lock.touch(); stat = lock.stat()
            identity = f'{os.major(stat.st_dev):02x}:{os.minor(stat.st_dev):02x}:{stat.st_ino}'
            proc = root / 'locks'
            proc.write_text(f'1: FLOCK ADVISORY WRITE 111 {identity} 0 EOF\n1: -> FLOCK ADVISORY READ 222 {identity} 0 EOF\n')
            self.assertEqual(len(cutover.lock_users([lock], proc)), 2)
            self.assertEqual(len(cutover.lock_users([lock], proc, exclude_pid=111)), 1)
            self.assertIn('->', cutover.lock_users([lock], proc, exclude_pid=111)[0])
            proc.write_text(f'2: -> FLOCK ADVISORY WRITE 222 ff:ff:{stat.st_ino} 0 EOF\n')
            self.assertEqual(len(cutover.lock_users([lock], proc)), 1)

    def test_actual_foreign_holder_refuses_without_waiting(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / 'lock'
            holder = subprocess.Popen(['flock', '-x', str(lock), sys.executable, '-I', '-B', '-c',
                                       'import sys; print("held", flush=True); sys.stdin.readline()'],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), 'held')
                with self.assertRaisesRegex(ValueError, 'holders or waiters|became busy'):
                    with cutover.idle_locks([lock]):
                        self.fail('occupied lock admitted')
            finally:
                holder.communicate('\n', timeout=5)

    def test_apply_refuses_a_reported_waiter_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); home, _, _, locks = self.fixture(root)
            plan = cutover.plan(home); before = self.fingerprint(root)
            with patch.object(cutover, 'lock_users', return_value=['blocked waiter']), self.assertRaisesRegex(ValueError, 'holders or waiters'):
                cutover.apply(home, *plan, locks=locks)
            self.assertEqual(self.fingerprint(root), before)

    def test_apply_moves_sources_rewrites_paths_and_repairs_linked_worktree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); home, old_checkout, linked, locks = self.fixture(root)
            plan = cutover.plan(home)
            with contextlib.redirect_stdout(io.StringIO()):
                cutover.apply(home, *plan, locks=locks)
            checkout = home / cutover.HOST_NAMES[1][1]
            self.assertFalse(old_checkout.exists())
            self.assertTrue((home / 'Games/.ls-harness/ls-port.py').is_file())
            self.assertIn('name: ls-workbench', (home / '.agents/skills/ls-workbench/SKILL.md').read_text())
            self.assertIn('WCLASS=ls-harness', (home / 'Games/.ls-harness/hs-run.sh').read_text())
            self.assertIn('GAME=longshot', (home / 'Games/.ls-harness/hs-run.sh').read_text())
            self.assertIn('Games/.ls-harness', (checkout / 'source.py').read_text())
            self.assertIn("LIVE_LAYOUT = 'published'", (checkout / 'publish.py').read_text())
            result = subprocess.run(['git', '-C', str(linked), 'rev-parse', '--git-common-dir'], text=True, capture_output=True, check=True)
            self.assertEqual(Path(result.stdout.strip()).resolve(), (checkout / '.git').resolve())

    def test_existing_destination_and_missing_source_refuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); home, _, _, locks = self.fixture(root)
            (home / 'Games/.ls-harness').mkdir()
            (home / '.agents/skills/hookshot-workbench/SKILL.md').unlink()
            (home / '.agents/skills/hookshot-workbench').rmdir()
            plan = cutover.plan(home); before = self.fingerprint(root)
            self.assertTrue(any('missing' in value for value in plan[2]))
            self.assertTrue(any('destination already exists' in value for value in plan[2]))
            with self.assertRaises(ValueError):
                cutover.apply(home, *plan, locks=locks)
            self.assertEqual(self.fingerprint(root), before)

    def test_stale_plan_refuses_under_locks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); home, checkout, _, locks = self.fixture(root)
            plan = cutover.plan(home)
            (checkout / 'source.py').write_text('different input')
            with self.assertRaisesRegex(ValueError, 'inputs changed'):
                cutover.apply(home, *plan, locks=locks)
            self.assertTrue(checkout.exists())

    def test_failure_rolls_back_files_renames_and_repairs_original_worktree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); home, checkout, linked, locks = self.fixture(root)
            plan = cutover.plan(home); original = (checkout / 'source.py').read_bytes()
            run = subprocess.run; repairs = []
            def failing_first_repair(argv, **kwargs):
                if argv[-2:] == ['worktree', 'repair']:
                    repairs.append(argv)
                    if len(repairs) == 1:
                        raise subprocess.CalledProcessError(1, argv)
                return run(argv, **kwargs)
            with patch.object(cutover.subprocess, 'run', side_effect=failing_first_repair), self.assertRaises(subprocess.CalledProcessError):
                cutover.apply(home, *plan, locks=locks)
            self.assertEqual(len(repairs), 2)
            self.assertEqual((checkout / 'source.py').read_bytes(), original)
            self.assertTrue((home / 'Games/.hookshot-harness/longshot-port.py').is_file())
            self.assertFalse((home / 'Games/.ls-harness').exists())
            subprocess.run(['git', '-C', str(linked), 'rev-parse', 'HEAD'], check=True, capture_output=True)

    def test_failed_atomic_replacement_keeps_original_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); home, checkout, _, locks = self.fixture(root)
            plan = cutover.plan(home); original = (checkout / 'source.py').read_bytes()
            target = home / cutover.HOST_NAMES[1][1] / 'source.py'
            replace = os.replace
            failed = False
            def failing_replace(source, destination):
                nonlocal failed
                if Path(destination) == target and not failed:
                    failed = True
                    raise OSError('fixture replacement refused')
                return replace(source, destination)
            with patch.object(cutover.os, 'replace', side_effect=failing_replace), self.assertRaisesRegex(OSError, 'fixture replacement refused'):
                cutover.apply(home, *plan, locks=locks)
            self.assertTrue(failed)
            self.assertEqual((checkout / 'source.py').read_bytes(), original)
            self.assertFalse(target.exists())
            self.assertFalse(list(home.rglob('.ls-cutover-*')))


if __name__ == '__main__':
    unittest.main(verbosity=2)
