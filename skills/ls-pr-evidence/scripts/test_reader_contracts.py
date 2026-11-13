#!/usr/bin/env python3
"""Offline reader regressions. Never invoke the fork script's publishing path."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('overlap', HERE / 'pr_overlap.py')
overlap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlap)


class ForkChecks(unittest.TestCase):
    def status(self, table):
        source = (HERE / 'hs-fork-ci.sh').read_text()
        # Execute only the pure status classification, never git/gh/cleanup.
        start = source.index('windows=$(')
        end = source.index('if [ "$keep" = 0 ]', start)
        result = subprocess.run(['bash', '-c', source[start:end] + '\nprintf "%s\\n" "$status"'],
                                env={**os.environ, 'table': table}, capture_output=True, text=True, check=True)
        return int(result.stdout)

    def windows(self, first='pass', second='pass'):
        return '\n'.join(f'{state}\tPython source checks (windows-2022, part {part}/2)'
                         for part, state in ((1, first), (2, second)))

    def test_both_parts_required(self):
        self.assertEqual(self.status(self.windows()), 0)
        self.assertEqual(self.status(self.windows().splitlines()[0]), 1)
        self.assertEqual(self.status(''), 1)

    def test_duplicate_part_does_not_replace_missing_part(self):
        first = self.windows().splitlines()[0]
        self.assertEqual(self.status(first + '\n' + first), 1)

    def test_windows_failure_or_skip_is_not_pass(self):
        for state in ('fail', 'pending', 'skipping', 'cancel'):
            self.assertEqual(self.status(self.windows(second=state)), 1)

    def test_non_windows_failure_or_cancel_is_read(self):
        for state in ('fail', 'cancel'):
            self.assertEqual(self.status(self.windows() + '\n' + state + '\tOther check'), 2)
        self.assertEqual(self.status(self.windows() + '\nskipping\tOptional check'), 0)


class OverlapChecks(unittest.TestCase):
    def test_file_after_first_page_is_not_lost(self):
        calls = []
        def gh(*args):
            calls.append(args)
            if args[:2] == ('pr', 'list'):
                return json.dumps([{'number': 8, 'title': 'Neutral change', 'author': {'login': 'other'}, 'isDraft': False}])
            self.assertIn('--paginate', args)
            return '\n'.join([f'file-{n}.cpp' for n in range(100)] + ['shared.cpp'])
        def git(*args):
            return subprocess.CompletedProcess(args, 0, 'shared.cpp\n' if args[0] == 'diff' else 'tree\n', '')
        output = io.StringIO()
        with patch.object(overlap, 'gh', gh), patch.object(overlap, 'git', git), patch.object(sys, 'argv', ['pr_overlap.py', '--branch', 'candidate']), contextlib.redirect_stdout(output):
            overlap.main()
        self.assertIn('same code file, merges cleanly: #8', output.getvalue())

    def test_full_pr_bound_refuses_incomplete_inventory(self):
        rows = [{'number': n, 'title': 'Neutral', 'author': {'login': 'other'}, 'isDraft': False} for n in range(500)]
        with patch.object(overlap, 'gh', return_value=json.dumps(rows)), patch.object(overlap, 'git', side_effect=AssertionError('incomplete inventory must stop before Git')), patch.object(sys, 'argv', ['pr_overlap.py', '--branch', 'candidate']):
            with self.assertRaisesRegex(SystemExit, 'incomplete'):
                overlap.main()

    def test_merge_tree_error_is_not_clean(self):
        failed = subprocess.CompletedProcess([], 128, '', 'missing object')
        with patch.object(overlap, 'git', return_value=failed):
            with self.assertRaisesRegex(SystemExit, 'merge-tree'):
                overlap.conflicts('a', 'b')


if __name__ == '__main__':
    unittest.main()
