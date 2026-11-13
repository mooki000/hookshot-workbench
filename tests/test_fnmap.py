#!/usr/bin/env python3
"""The function map's tool (docs/oot/fnmap.py) and the skill checker (skills/ls-skill-upkeep/check_skills.py), each
run as a program on a small tree made here. Standard library and git only."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = "# Index\n\n| Page | What |\n|---|---|\n| [a.md](a.md) | a page |\n"
PAGE = """# A page

Prose that names the `WorkerPool` thread without a row.

## Functions

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80000010` | `Clock_Update` | Advances the clock by the frame step. Hooked by `patch_clock` | tested |
%s
"""


def run(cmd, cwd=None, env=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


class FunctionMap(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.pages = self.tmp / "map" / "docs" / "oot"
        self.pages.mkdir(parents=True)
        shutil.copy(ROOT / "docs" / "oot" / "fnmap.py", self.pages / "fnmap.py")
        (self.pages / "README.md").write_text(INDEX, encoding="utf-8")
        self.port = self.tmp / "port"
        (self.port / "nx2").mkdir(parents=True)
        self.git("init", "-q")

    def git(self, *args):
        code, out = run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args], cwd=self.port)
        self.assertEqual(code, 0, out)

    def page(self, extra=""):
        (self.pages / "a.md").write_text(PAGE % extra, encoding="utf-8")

    def code(self, text):
        (self.port / "nx2" / "oot_patches.py").write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "code")

    def tool(self, *args):
        return run([sys.executable, str(self.pages / "fnmap.py"), *args])

    def check(self):
        return self.tool("check", "--repo", str(self.port), "--rev", "HEAD")

    def test_find_gives_the_row_of_an_address_whatever_its_spelling(self):
        self.page()
        for query in ("f_80000010", "0x80000010", "80000010", "F_80000010"):
            code, out = self.tool("find", query)
            self.assertEqual(code, 0, query)
            self.assertIn("a.md:9", out)
            self.assertIn("Clock_Update", out)

    def test_find_falls_back_to_the_prose_and_fails_when_nothing_names_it(self):
        self.page()
        code, out = self.tool("find", "WorkerPool")
        self.assertEqual(code, 0)
        self.assertIn("mentioned, no row: docs/oot/a.md:3", out)
        self.assertEqual(self.tool("find", "no such thing")[0], 1)

    def test_check_passes_when_every_function_the_code_names_has_a_row(self):
        self.page()
        self.code('SITES = (("void f_80000010(Ctx *c) {",),)\ndef patch_clock(s):\n    return s\n')
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("1 functions named in code at HEAD, 1 of them with a row", out)

    def test_check_fails_for_a_function_the_code_names_without_a_row(self):
        self.page()
        self.code('A = "f_80000010 f_80000020"\ndef patch_clock(s):\n    return s\n')
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("f_80000020 is named in the port's code", out)
        self.assertNotIn("f_80000010 is named", out)

    def test_check_fails_for_a_second_row_a_malformed_row_and_a_patch_that_does_not_exist(self):
        self.page("| `f_80000010` | `Clock_Again` | The same address once more | read |\n"
                  "| `f_80000030` | `NoSure` | A row without its last cell |\n"
                  "| `f_80000040` | `Gone` | Hooked by `patch_removed` | read |")
        self.code('A = "f_80000010"\ndef patch_clock(s):\n    return s\n')
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("f_80000010 has a second row", out)
        self.assertIn("f_80000030: a row needs four cells", out)
        self.assertIn("'Hooked by `patch_removed`', which nx2/ does not define", out)

    def test_check_notes_a_patched_function_whose_row_does_not_say_so(self):
        self.page("| `f_80000050` | `Other` | Patched, and the row does not say it | read |")
        self.code('SITES = (("void f_80000050(Ctx *c) {",),)\ndef patch_clock(s):\n    return s\n')
        code, out = self.check()
        self.assertEqual(code, 0, out)
        self.assertIn("note: f_80000050 is patched", out)


class SkillCheck(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)

    def skill(self, name, text):
        (self.root / name).mkdir()
        (self.root / name / "SKILL.md").write_text(text, encoding="utf-8")

    def tool(self):
        env = dict(os.environ, LS_REPO=str(self.root / "no-port"))
        return run([sys.executable, str(ROOT / "skills" / "ls-skill-upkeep" / "check_skills.py"), "--root", str(self.root)],
                   env=env)

    def test_a_sound_skill_passes(self):
        self.skill("one", "---\nname: one\ndescription: Use when testing.\n---\nSee `skill://one`.\n")
        code, out = self.tool()
        self.assertEqual(code, 0, out)
        self.assertIn("OK   one", out)

    def test_broken_references_and_missing_front_matter_fail(self):
        self.skill("one", "---\nname: one\ndescription: Use when testing.\n---\nRead skill://two and skill://one/missing.md.\n")
        self.skill("three", "---\nname: other\n---\nNo description, and a name that is not the folder's.\n")
        code, out = self.tool()
        self.assertEqual(code, 1)
        for expected in ("skill://two: no such skill here", "skill://one/missing.md: no such file",
                         "name 'other' is not the folder's name", "no description"):
            self.assertIn(expected, out)

    def test_a_stale_path_and_a_description_without_a_trigger_warn_without_failing(self):
        self.skill("one", "---\nname: one\ndescription: Things about tests.\n---\nRun /home/nobody-here/tool.sh first.\n")
        code, out = self.tool()
        self.assertEqual(code, 0, out)
        self.assertIn("does not say when to use the skill", out)
        self.assertIn("path not found: /home/nobody-here/tool.sh", out)


if __name__ == "__main__":
    unittest.main()
