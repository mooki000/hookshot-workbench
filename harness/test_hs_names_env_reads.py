#!/usr/bin/env python3
"""Game-free source-only checks: environment writes/deletes do not attest reads."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('hs_names_env_read_fixture', HERE / 'hs_names.py')
names = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = names
spec.loader.exec_module(names)


class EnvironmentReadTests(unittest.TestCase):
    def check_source(self, project, source, *, reject=False):
        with tempfile.TemporaryDirectory(prefix='names-read-') as td:
            root = Path(td)
            (root / 'scripts').mkdir()
            (root / 'scripts' / (project + '_ctl.py')).touch()
            (root / 'nx2').mkdir()
            # Parse this text only: never import or execute the synthetic build.
            (root / 'nx2/build.py').write_text(source, encoding='utf-8')
            if reject:
                with self.assertRaisesRegex(names.NameError, 'missing or mixed'):
                    names.build_env(root, {'LIFT_JOBS': 8})
            else:
                try:
                    actual = names.build_env(root, {'LIFT_JOBS': 8})
                except names.NameError as error:
                    self.fail('A real source read was refused: ' + str(error))
                self.assertEqual(actual, {project.upper() + '_LIFT_JOBS': '8'})

    def test_plain_assignment_does_not_attest_a_read(self):
        for project in names.PROJECTS:
            with self.subTest(project=project):
                key = project.upper() + '_LIFT_JOBS'
                self.check_source(project, f'import os\nos.environ[{key!r}] = "1"\n', reject=True)

    def test_annotated_assignment_does_not_attest_a_read(self):
        for project in names.PROJECTS:
            with self.subTest(project=project):
                key = project.upper() + '_LIFT_JOBS'
                self.check_source(project, f'import os\nos.environ[{key!r}]: str = "1"\n', reject=True)

    def test_delete_does_not_attest_a_read(self):
        for project in names.PROJECTS:
            with self.subTest(project=project):
                key = project.upper() + '_LIFT_JOBS'
                self.check_source(project, f'import os\ndel os.environ[{key!r}]\n', reject=True)

    def test_literal_reads_remain_supported(self):
        for project in names.PROJECTS:
            key = project.upper() + '_LIFT_JOBS'
            for expression in [f'os.environ[{key!r}]', f'environ[{key!r}]',
                               f'os.environ.get({key!r})', f'os.getenv({key!r})',
                               f'environ.get({key!r})', f'env_on({key!r})', f'env_int({key!r})']:
                with self.subTest(project=project, expression=expression):
                    self.check_source(project, 'import os\nvalue = ' + expression + '\n')

    def test_augmented_assignment_still_counts_as_a_read(self):
        for project in names.PROJECTS:
            with self.subTest(project=project):
                key = project.upper() + '_LIFT_JOBS'
                self.check_source(project, f'import os\nos.environ[{key!r}] += ":fixture"\n')

    def test_other_family_writes_do_not_make_real_reads_mixed(self):
        for project in names.PROJECTS:
            with self.subTest(project=project):
                key = project.upper() + '_LIFT_JOBS'
                other = next(p for p in names.PROJECTS if p != project).upper() + '_LIFT_JOBS'
                self.check_source(project, f'import os\nos.environ[{other!r}] = "1"\nvalue = os.environ[{key!r}]\n')

    def test_real_read_on_assignment_rhs_is_not_lost(self):
        for project in names.PROJECTS:
            with self.subTest(project=project):
                key = project.upper() + '_LIFT_JOBS'
                self.check_source(project, f'import os\nos.environ["FIXTURE_OUT"] = os.environ[{key!r}]\n')

    def test_actual_mixed_reads_still_refuse(self):
        for project in names.PROJECTS:
            with self.subTest(project=project):
                self.check_source(project, 'import os\na = os.environ["HOOKSHOT_LIFT_JOBS"]\nb = os.environ["LONGSHOT_LIFT_JOBS"]\n', reject=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
