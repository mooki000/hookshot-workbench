#!/usr/bin/env python3
"""Every retained legacy literal is explicit, scoped and counted, including filenames."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import name_policy as policy


def manifest(rows):
    return policy.BEGIN + '\n' + '\n'.join(json.dumps(row) for row in rows) + '\n' + policy.END


class NamePolicyTests(unittest.TestCase):
    def test_repository_contains_only_documented_kept_literals(self):
        extra = policy.violations(ROOT)
        self.assertFalse(extra, '\n'.join(f'{path}: {literal!r}: {count} extra' for (path, literal), count in extra.items()))

    def test_new_mixed_case_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'source.py').write_text('HoOkShOt and LoNgShOt')
            self.assertEqual(sum(policy.violations(root, manifest([]), ['source.py']).values()), 2)

    def test_filename_is_checked_even_when_content_has_no_old_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); path = root / 'longshot-new.py'; path.write_text('source')
            self.assertTrue(policy.violations(root, manifest([]), [path.name]))

    def test_existing_literal_cannot_spread_to_another_file_or_repeat(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'adapter.py').write_text('hookshot_log.txt hookshot_log.txt')
            (root / 'other.py').write_text('hookshot_log.txt')
            extra = policy.violations(root, manifest([['adapter.py', 'hookshot_log.txt', 1, 'external log']]),
                                      ['adapter.py', 'other.py'])
            self.assertEqual(extra[('adapter.py', 'hookshot_log.txt')], 1)
            self.assertEqual(extra[('other.py', 'hookshot_log.txt')], 1)

    def test_binary_bytes_and_symlink_targets_do_not_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'data').write_bytes(b'\xffhookshot\x00')
            (root / 'alias').symlink_to('data')
            (root / 'dangling').symlink_to('hookshot-missing')
            self.assertTrue(policy.violations(root, manifest([]), ['dangling']))
            self.assertTrue(policy.violations(root, manifest([]), ['data']))

    def test_exception_requires_a_reason_and_nonblank_exact_file(self):
        for row in (['a.py', 'hookshot', 1, ''], ['a.py', 'hookshot', 0, 'why'],
                    ['/outside.py', 'hookshot', 1, 'why'], ['../outside.py', 'hookshot', 1, 'why']):
            with self.subTest(row=row), self.assertRaises(ValueError):
                policy.allowance(manifest([row]))

    def test_manifest_relocation_preserves_untouched_migration_tables(self):
        text = manifest([['source.py', '/home/mooki/Games/.hookshot-harness', 1, 'host path'],
                         ['cutover.py', '/home/mooki/Games/.hookshot-harness', 1, 'migration input']])
        changed = policy.rewrite_manifest(text, {'source.py': ('source.py', lambda value: value.replace('.hookshot-harness', '.ls-harness'))})
        self.assertEqual(policy.records(changed), [['cutover.py', '/home/mooki/Games/.hookshot-harness', 1, 'migration input']])

    def test_relocated_home_with_spaces_still_matches_tokens(self):
        old = '/home/mooki/Code/Hookshot'
        transform = lambda value: value.replace('/home/mooki', '/home/a & b')
        text = policy.rewrite_manifest(manifest([['source.py', old, 1, 'host path']]), {'source.py': ('source.py', transform)})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'source.py').write_text(transform(old))
            self.assertFalse(policy.violations(root, text, ['source.py']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
