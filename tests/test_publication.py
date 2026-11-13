#!/usr/bin/env python3
"""Synthetic publication and relocation tests; never use installed source folders."""
from pathlib import Path
import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import publish
import name_policy


def stage(root):
    for live in publish.SKILL_MAP:
        skill = root / 'skills' / publish.installed(live)
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('---\nname: ' + live + '\n---\nRead skill://' + live + '.\n')
    with patch.dict(os.environ, {'LS_WB_SOURCE_ROOT': str(root)}):
        for source, _ in publish.files():
            if not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text('synthetic source\n')
    return root


class PublicationTests(unittest.TestCase):
    def test_explicit_skill_map_and_logical_cross_references(self):
        self.assertEqual(len(publish.SKILL_MAP), 10)
        for live, public in publish.SKILL_MAP.items():
            original = f'name: {live}\nRead `{live}` and skill://{live}.\nSee skills/{live}/SKILL.md.\n'
            expected = f'name: {public}\nRead `{public}` and skill://{public}.\nSee skills/{public}/SKILL.md.\n'
            self.assertEqual(publish.skill_references(original), expected)

    def test_absolute_live_skill_paths_are_not_cross_references(self):
        for live, public in publish.SKILL_MAP.items():
            paths = f'/home/mooki/.agents/skills/{live}/SKILL.md\n~/.agents/skills/{live}/scripts/tool.py\n'
            original = paths + f'skills/{live}/SKILL.md skill://{live}\n'
            self.assertEqual(publish.skill_references(original), paths + f'skills/{public}/SKILL.md skill://{public}\n')

    def test_unrelated_names_are_not_prefix_matched(self):
        original = 'hookshot-workbench-extra other/hookshot-workbench-file\n'
        self.assertEqual(publish.skill_references(original), original)

    def test_runtime_protocols_urls_and_host_paths_are_preserved(self):
        original = ('hookshot.exe longshot_vk.so hookshot_log.txt longshot_log.txt\n'
                    'HOOKSHOT_LIFT_JOBS LONGSHOT_LIFT_JOBS HOS_CONTROL\n'
                    'scripts/hookshot_ctl.py scripts/longshot_ctl.py\n'
                    'https://github.com/NotRexed/Hookshot https://git.magicbean.dev/MagicBeanDev/Longshot\n'
                    '/home/mooki/Code/Hookshot /home/mooki/Code/hookshot-wt\n'
                    '/home/mooki/Games/.hookshot-harness /home/mooki/Games/Hookshot\n')
        self.assertEqual(publish.normalise(original), original)

    def test_own_identity_and_markdown_prose_are_renamed(self):
        original = '# Hookshot workbench\nHookshot and Longshot. `hookshot_log.txt`\nHOOKSHOT_WB_PROFILE hookshot-wb\n'
        self.assertEqual(publish.normalise(original, 'SKILL.md'),
                         '# LS workbench\nHS and LS. `hookshot_log.txt`\nLS_WB_PROFILE ls-wb\n')
        self.assertEqual(publish.normalise('WCLASS=hookshot-harness\nH=Games/.hookshot-harness\n'),
                         'WCLASS=ls-harness\nH=Games/.hookshot-harness\n')

    def test_source_comments_and_docstrings_leave_runtime_literals_alone(self):
        original = '"""Hookshot and Longshot."""\n# Hookshot helper\nruntime = "Hookshot v1 longshot_log.txt"\n'
        expected = '"""HS and LS."""\n# HS helper\nruntime = "Hookshot v1 longshot_log.txt"\n'
        self.assertEqual(publish.normalise(original, 'helper.py'), expected)

    def test_live_toolchain_helper_and_test_follow_the_new_input_name(self):
        original = 'def environment_value(environ, suffix):\n    return environ.get("OLD_" + suffix)\n'
        namespace = {}
        exec(publish.normalise(original, 'harness/hs_names.py'), namespace)
        self.assertEqual(namespace['environment_value']({'LS_MINGW': '/fixture/sdk'}, 'MINGW'), '/fixture/sdk')
        old_test = ('class Tests:\n    def test_local_toolchain_input_accepts_either_but_not_both(self):\n'
                    '        pass\n')
        actual = publish.normalise(old_test, 'harness/test_hs_names.py')
        self.assertIn('def test_local_toolchain_input_uses_the_workbench_namespace', actual)
        self.assertIn("'LS_MINGW'", actual)

    def test_paths_and_allowlist_use_renamed_destinations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = stage(Path(temporary))
            with patch.dict(os.environ, {'LS_WB_SOURCE_ROOT': str(root)}):
                pairs = publish.files()
            destinations = {str(destination) for _, destination in pairs}
            self.assertEqual(len(pairs), 57)
            self.assertIn('harness/ls-port.py', destinations)
            self.assertIn('regress/test_runtime_names.py', destinations)
            self.assertIn('nightly/test_runtime_names.py', destinations)
            self.assertNotIn('mcp/pyproject.toml', destinations)
            self.assertTrue(all('hookshot' not in destination for destination in destinations))

    def test_published_live_layout_reads_renamed_physical_sources(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(publish, 'LIVE_LAYOUT', 'published'):
            root = stage(Path(temporary))
            with patch.dict(os.environ, {'LS_WB_SOURCE_ROOT': str(root)}):
                pairs = publish.files()
            self.assertTrue((root / 'skills/ls-workbench/SKILL.md').is_file())
            self.assertIn((root / 'harness/ls-port.py', Path('harness/ls-port.py')), pairs)

    def test_unreviewed_skill_data_refuses_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = stage(root / 'stage'); checkout = root / 'checkout'; checkout.mkdir()
            (source / 'skills' / next(iter(publish.SKILL_MAP)) / 'private.json').write_text('{}')
            with patch.dict(os.environ, {'LS_WB_SOURCE_ROOT': str(source)}), self.assertRaisesRegex(ValueError, 'unreviewed'):
                publish.publish(checkout)
            self.assertEqual(list(checkout.iterdir()), [])

    def test_missing_source_refuses_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = stage(root / 'stage'); checkout = root / 'checkout'; checkout.mkdir()
            (source / 'harness/hs-run.sh').unlink()
            with patch.dict(os.environ, {'LS_WB_SOURCE_ROOT': str(source)}), self.assertRaisesRegex(ValueError, 'missing'):
                publish.publish(checkout)
            self.assertEqual(list(checkout.iterdir()), [])

    def test_links_cannot_escape_the_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = stage(root / 'stage'); checkout = root / 'checkout'; checkout.mkdir()
            outside = root / 'outside'; outside.mkdir()
            (checkout / 'harness').symlink_to(outside, target_is_directory=True)
            with patch.dict(os.environ, {'LS_WB_SOURCE_ROOT': str(source)}), self.assertRaisesRegex(ValueError, 'linked'):
                publish.publish(checkout)
            self.assertEqual(list(outside.iterdir()), [])

    def test_staging_is_idempotent_cleans_managed_skills_and_leaves_mcp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = stage(root / 'stage'); checkout = root / 'checkout'; checkout.mkdir()
            legacy = checkout / 'skills' / next(iter(publish.SKILL_MAP)); legacy.mkdir(parents=True)
            (legacy / 'SKILL.md').write_text('obsolete')
            (checkout / 'mcp').mkdir(); protected = checkout / 'mcp/owned.txt'; protected.write_text('unchanged')
            original = {p: p.read_bytes() for p in source.rglob('*') if p.is_file()}
            with patch.dict(os.environ, {'LS_WB_SOURCE_ROOT': str(source)}), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(publish.publish(checkout), 0)
                self.assertFalse(legacy.exists())
                stale = checkout / 'skills/ls-workbench/stale.py'; stale.write_text('obsolete')
                publish.publish(checkout)
                self.assertFalse(stale.exists())
                self.assertEqual(publish.publish(checkout, check=True), 0)
                (source / 'harness/hs-run.sh').write_text('changed fixture')
                before = (checkout / 'harness/hs-run.sh').read_bytes()
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(publish.publish(checkout, check=True), 1)
                self.assertEqual((checkout / 'harness/hs-run.sh').read_bytes(), before)
            self.assertEqual(protected.read_text(), 'unchanged')
            self.assertTrue(all(p.read_bytes() == data for p, data in original.items() if p.name != 'hs-run.sh'))

    def test_relocation_is_literal_and_skips_private_and_linked_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / 'script.sh'; script.write_text('/home/mooki/Games/.hookshot-harness\n'); script.chmod(0o755)
            (root / '.private').mkdir(); private = root / '.private/state'; private.write_text('/home/mooki')
            outside = root / '.private/target'; outside.write_text('/home/mooki'); (root / 'linked').symlink_to(outside)
            (root / 'docs').mkdir()
            manifest = root / name_policy.MANIFEST
            manifest.write_text(name_policy.BEGIN + '\n["script.sh", "/home/mooki/Games/.hookshot-harness", 1, "host path"]\n' + name_policy.END)
            with contextlib.redirect_stdout(io.StringIO()):
                publish.relocate(root, '/home/space & value')
            self.assertEqual(script.read_text(), '/home/space & value/Games/.hookshot-harness\n')
            self.assertEqual(script.stat().st_mode & 0o777, 0o755)
            self.assertEqual(private.read_text(), '/home/mooki')
            self.assertEqual(outside.read_text(), '/home/mooki')
            self.assertFalse(name_policy.violations(root, files=['script.sh']))

    def test_relocation_rejects_invalid_homes(self):
        for home in ('relative', '/', '/home/bad\nnext'):
            with self.subTest(home=home), self.assertRaises(ValueError):
                publish.relocate(ROOT, home)

    def test_relocate_shell_uses_the_tested_function(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('relocate.sh', 'publish.py', 'name_policy.py'):
                shutil.copy2(ROOT / name, root / name)
            (root / 'source.sh').write_text('/home/mooki/Code/Hookshot\n')
            result = subprocess.run(['bash', str(root / 'relocate.sh'), '/home/fixture & space'], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'source.sh').read_text(), '/home/fixture & space/Code/Hookshot\n')


if __name__ == '__main__':
    unittest.main(verbosity=2)
