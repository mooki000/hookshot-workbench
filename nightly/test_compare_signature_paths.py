"""Synthetic absolute POSIX path regression; standard unittest, no private inputs."""
import importlib.util
from pathlib import Path
import unittest

_spec = importlib.util.spec_from_file_location('signature_paths_compare', Path(__file__).with_name('compare.py'))
compare = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare)


class SignaturePathTests(unittest.TestCase):
    def test_volatile_absolute_posix_paths_keep_one_signature(self):
        before = compare.signature('warning: allocation failed at /synthetic/copy-alpha/cache.bin')
        after = compare.signature('warning: allocation failed at /synthetic/copy-beta/cache.bin')
        self.assertEqual(before, after)
        self.assertEqual(compare.compare_signatures({after: 1}, [{before: 1}]), [])

    def test_error_words_remain_distinct(self):
        allocation = compare.signature('warning: allocation failed at /synthetic/copy-alpha/cache.bin')
        descriptor = compare.signature('warning: descriptor failed at /synthetic/copy-beta/cache.bin')
        self.assertNotEqual(allocation, descriptor)
        self.assertEqual(compare.compare_signatures({descriptor: 1}, [{allocation: 1}]),
                         [{'signature': descriptor, 'state': 'new-signature', 'count': 1}])

    def test_existing_volatile_fields_keep_normalizing(self):
        before = '\x1b[31mworker 12 warning:\x1b[0m allocation 20.5 MB at 0xabc123 failed'
        after = '  worker 99\twarning: allocation 1 MB at 0x123abc failed  '
        self.assertEqual(compare.signature(before), compare.signature(after))
        self.assertEqual(compare.signature(before), 'worker <n> warning: allocation <n> MB at <addr> failed')

    def test_path_token_preserves_punctuation_and_following_error_words(self):
        line = 'warning: (/synthetic/copy-alpha/cache.bin), descriptor failed.'
        self.assertEqual(compare.signature(line), 'warning: (<path>), descriptor failed.')
        self.assertEqual(compare.signature('warning: /synthetic/cache.bin. descriptor failed'),
                         'warning: <path>. descriptor failed')

    def test_quotes_around_space_free_paths_are_preserved(self):
        for quote in ('"', "'"):
            with self.subTest(quote=quote):
                line = f'warning: allocation failed at {quote}/synthetic/copy-alpha/cache.bin{quote}'
                self.assertEqual(compare.signature(line), f'warning: allocation failed at {quote}<path>{quote}')

    def test_path_normalization_keeps_count_jump_detection(self):
        before = compare.signature('warning: allocation failed at /synthetic/copy-alpha/cache.bin')
        after = compare.signature('warning: allocation failed at /synthetic/copy-beta/cache.bin')
        histories = [{before: n} for n in (8, 10, 12)]
        self.assertEqual(compare.compare_signatures({after: 12}, histories), [])
        findings = compare.compare_signatures({after: 100}, histories)
        self.assertEqual(len(findings), 1)
        self.assertEqual((findings[0]['signature'], findings[0]['state'], findings[0]['count']),
                         (after, 'count-jump', 100))

    def test_relative_resource_warning_is_new_in_same_format_history(self):
        before = compare.signature('warning: failed to load ./shaders/blit.bin')
        after = compare.signature('warning: failed to load ./textures/hud.bin')
        # Current and all three histories use this same implementation, not old Store keys.
        histories = [{before: 1} for _ in range(3)]
        findings = compare.compare_signatures({after: 1}, histories)
        with self.subTest(check='distinct-relative-signatures'):
            self.assertNotEqual(before, after)
        with self.subTest(check='same-format-new-signature'):
            self.assertEqual(findings,
                             [{'signature': after, 'state': 'new-signature', 'count': 1}])

    def test_neighbor_relative_components_are_not_absolute_suffixes(self):
        for prefix in ('../', 'cache-/'):
            with self.subTest(prefix=prefix):
                first = f'warning: failed to load {prefix}shaders/blit.bin'
                second = f'warning: failed to load {prefix}textures/hud.bin'
                before, after = compare.signature(first), compare.signature(second)
                self.assertEqual(before, first)
                self.assertEqual(after, second)
                self.assertNotEqual(before, after)
                self.assertEqual(compare.compare_signatures({after: 1}, [{before: 1} for _ in range(3)]),
                                 [{'signature': after, 'state': 'new-signature', 'count': 1}])


if __name__ == '__main__':
    unittest.main()
