#!/usr/bin/env python3
"""Regression coverage for CRT header-order policy and real MinGW diagnostics flags."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('fixture_check', Path(__file__).with_name('hs_win_fixture_check.py'))
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class CrtPolicy(unittest.TestCase):
    def errors(self, text, flags=()):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'fixture.cpp'
            source.write_text(text)
            return checker.crt_policy(source, ['clang++', *flags, str(source)])

    def test_env_header_requires_early_define(self):
        for header in ('compat/hookshot_env.h', 'hookshot_env.h'):
            self.assertTrue(self.errors(f'#include "{header}"\n'))
            self.assertFalse(self.errors(f'#define _CRT_SECURE_NO_WARNINGS\n#include "{header}"\n'))

    def test_crt_calls_include_wide_and_environment_variants(self):
        for name in sorted(checker.CRT_UNSAFE_CALLS):
            with self.subTest(name=name):
                source = f'#include <stdio.h>\nvoid f() {{ {name}(0); }}\n'
                self.assertTrue(self.errors(source))
                self.assertFalse(self.errors('#define _CRT_SECURE_NO_WARNINGS\n' + source))

    def test_late_define_is_not_enough(self):
        self.assertTrue(self.errors('#include <vector>\n#define _CRT_SECURE_NO_WARNINGS\n#include "compat/hookshot_env.h"\n'))

    def test_undef_before_headers_is_not_enough(self):
        self.assertTrue(self.errors('#define _CRT_SECURE_NO_WARNINGS\n#undef _CRT_SECURE_NO_WARNINGS\n#include <stdlib.h>\nvoid f() { getenv("X"); }\n'))

    def test_conditional_define_does_not_claim_protection(self):
        self.assertTrue(self.errors('#if 0\n#define _CRT_SECURE_NO_WARNINGS\n#endif\n#include "compat/hookshot_env.h"\n'))

    def test_comments_and_literals_are_not_calls_or_defines(self):
        self.assertFalse(self.errors('// getenv("X")\n/* fopen("x") */\nconst char *s = "strcpy(";\n'))
        self.assertTrue(self.errors('/* #define _CRT_SECURE_NO_WARNINGS */\n#include "compat/hookshot_env.h"\n'))
        self.assertFalse(self.errors('/* header note */\n#define _CRT_SECURE_NO_WARNINGS\n#include "compat/hookshot_env.h"\n'))

    def test_command_line_define_precedes_headers(self):
        source = '#include "compat/hookshot_env.h"\n'
        self.assertFalse(self.errors(source, ['-D_CRT_SECURE_NO_WARNINGS']))
        self.assertFalse(self.errors(source, ['-D', '_CRT_SECURE_NO_WARNINGS=1']))
        self.assertTrue(self.errors(source, ['-D_CRT_SECURE_NO_WARNINGS', '-U_CRT_SECURE_NO_WARNINGS']))

    def test_forced_include_precedes_source_define(self):
        source = '#define _CRT_SECURE_NO_WARNINGS\n#include <vector>\n'
        self.assertTrue(self.errors(source, ['-include', 'compat/hookshot_env.h']))
        self.assertFalse(self.errors(source, ['-include', 'compat/hookshot_env.h', '-D_CRT_SECURE_NO_WARNINGS']))
        self.assertTrue(self.errors(source, ['-includecompat/hookshot_env.h']))

    def test_unrelated_fixture_does_not_require_crt_define(self):
        self.assertFalse(self.errors('#include <vector>\nint main() { return 0; }\n'))

    def test_min_gw_annotations_and_error_are_enabled(self):
        flags = checker.compile_args(['g++', '-Wall', '-Werror', '-Wno-deprecated-declarations', 'fixture.cpp'],
                                     Path('fixture.cpp'), 'clang++', Path('/mingw'))
        self.assertIn('-D__MINGW_MSVC_COMPAT_WARNINGS=1', flags)
        self.assertGreater(flags.index('-Werror=deprecated-declarations'), flags.index('-Wno-deprecated-declarations'))
        self.assertNotIn('-D_CRT_SECURE_NO_WARNINGS', flags)
        self.assertLess(flags.index('/mingw/include/c++/v1'), flags.index('/mingw/include'))


if __name__ == '__main__':
    unittest.main()
