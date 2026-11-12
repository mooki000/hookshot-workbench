#!/usr/bin/env python3
"""Game-free integration tests: python -I -B test_longshot_port.py."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOL = Path(__file__).with_name("longshot-port.py")
spec = importlib.util.spec_from_file_location("longshot_port", TOOL)
port = importlib.util.module_from_spec(spec)
spec.loader.exec_module(port)


class PortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-longshot-port-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = port.environment()
        self.env.update(GIT_AUTHOR_NAME="Original Author", GIT_AUTHOR_EMAIL="author@example.invalid",
                        GIT_AUTHOR_DATE="1700000000 +0530", GIT_COMMITTER_NAME="Original Committer",
                        GIT_COMMITTER_EMAIL="committer@example.invalid", GIT_COMMITTER_DATE="1700000100 -0700")
        self.git("init", "--quiet", "--initial-branch=main")
        self.put("scripts/hookshot_ctl.py", "#!/usr/bin/env python3\nNAME = 'hookshot'\nvalue = 1\n")
        (self.root / "scripts/hookshot_ctl.py").chmod(0o755)
        self.put("readme.md", "# Hookshot\n\nmain description\n\nunchanged spacer\n\nbranch description\n")
        self.put("docs/links.md", "https://github.com/NotRexed/Hookshot/issues/11\n\nspacer\n")
        self.put("hookshot-data.bin", b"\x00hookshot\xfforiginal")
        self.put("nonutf8.txt", b"hookshot\xff")
        (self.root / "ctl").symlink_to("scripts/hookshot_ctl.py")
        self.base = self.commit("base Hookshot")
        self.git("update-ref", "refs/remotes/origin/main", self.base)
        self.git("checkout", "--quiet", "-b", "rename")
        # Synthetic equivalent of the mechanical rename, plus one special-file edit.
        tracked = self.git("ls-files", "-z").split(b"\0")[:-1]
        for raw in tracked:
            name = raw.decode()
            old, new = self.root / name, self.root / port.rename(name)
            if old.is_symlink():
                target = port.rename(os.readlink(old))
                old.unlink()
                new.symlink_to(target)
                continue
            data, mode = old.read_bytes(), old.stat().st_mode
            text = port.text_blob(data)
            if old != new:
                new.parent.mkdir(parents=True, exist_ok=True)
                old.unlink()
            new.write_bytes(port.rename(text).encode() if text is not None else data)
            new.chmod(mode)
        self.put("readme.md", "# Longshot\n\nLongshot's own main description\n\nunchanged spacer\n\nbranch description\n")
        self.onto = self.commit("Rename the project to Longshot")
        self.git("update-ref", "refs/remotes/forge/main", self.onto)
        self.git("checkout", "--quiet", "-b", "topic", self.base)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], env=self.env, stderr=subprocess.PIPE)

    def put(self, name, data):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data.encode() if isinstance(data, str) else data)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD").decode().strip()

    def invoke(self, branch="topic", name="longshot/topic", check=False, base=None, expected=0, extra=()):
        args = [sys.executable, "-I", "-B", str(TOOL), branch, "--onto", "forge/main", "--name", name]
        if check:
            args.append("--check")
        if base:
            args.extend(["--base", base])
        args.extend(extra)
        result = subprocess.run(args, cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout if result.stdout else result.stderr)

    def show(self, path, ref="longshot/topic"):
        return self.git("show", ref + ":" + path)

    def fingerprint(self):
        # Include index, reflogs, loose objects and refs, not just worktree status.
        return {str(p.relative_to(self.root)): (p.stat().st_mode, hashlib.sha256(p.read_bytes()).hexdigest())
                for p in self.root.rglob("*") if p.is_file() and not p.is_symlink()}

    def test_renamed_file_modes_author_dates_message_and_multiple_commits(self):
        self.put("scripts/hookshot_ctl.py", "#!/usr/bin/env python3\nNAME = 'hookshot'\nvalue = 2\n")
        first = self.commit("fix: scripts/hookshot_ctl.py reads HOOKSHOT_JOBS (#2904)\n\nHookshot was the old project. Run `hookshot` and hookshot_vk.so.\n\nAssisted-by: Original Model")
        self.put("tests/test_hookshot_ctl.py", "from pathlib import Path\nassert Path('scripts/hookshot_ctl.py').exists()\n")
        second = self.commit("test: tests/test_hookshot_ctl.py")
        receipt = self.invoke()
        self.assertEqual(receipt["commit_count"], 2)
        self.assertEqual(self.show("scripts/longshot_ctl.py"), b"#!/usr/bin/env python3\nNAME = 'longshot'\nvalue = 2\n")
        self.assertIn(b"scripts/longshot_ctl.py", self.show("tests/test_longshot_ctl.py"))
        self.assertTrue(self.git("ls-tree", "longshot/topic", "scripts/longshot_ctl.py").startswith(b"100755"))
        for original, row in zip([first, second], receipt["commits"]):
            self.assertEqual(self.git("show", "-s", "--format=%an%x00%ae%x00%at%x00%ai", original),
                             self.git("show", "-s", "--format=%an%x00%ae%x00%at%x00%ai", row["ported"]))
        message = self.git("show", "-s", "--format=%B", receipt["commits"][0]["ported"]).decode()
        self.assertIn("scripts/longshot_ctl.py reads LONGSHOT_JOBS (#2904)", message)
        self.assertIn("Hookshot was the old project. Run `longshot` and longshot_vk.so.", message)
        self.assertIn("Assisted-by: Original Model", message)
        self.assertEqual(len(receipt["message_changes"]), 5)
        self.assertEqual(self.git("show", "-s", "--format=%cn <%ce>", "longshot/topic").decode().strip(),
                         "Michael Schienbein <7663497+mooki000@users.noreply.github.com>")

    def test_special_file_is_three_way_not_replaced(self):
        self.put("readme.md", "# Hookshot\n\nmain description\n\nunchanged spacer\n\nbranch uses hookshot_options.ini\n")
        self.commit("docs: add options")
        receipt = self.invoke()
        self.assertEqual(self.show("readme.md"), b"# Longshot\n\nLongshot's own main description\n\nunchanged spacer\n\nbranch uses longshot_options.ini\n")
        self.assertEqual(receipt["commits"][0]["special_files"], ["readme.md"])

    def test_binary_and_nonutf8_bytes_are_not_rewritten(self):
        self.put("hookshot-data.bin", b"\x00hookshot\xffchanged")
        self.put("nonutf8.txt", b"new hookshot\xff")
        self.commit("fix: binary fixture")
        self.invoke()
        self.assertEqual(self.show("longshot-data.bin"), b"\x00hookshot\xffchanged")
        self.assertEqual(self.show("nonutf8.txt"), b"new hookshot\xff")

    def test_path_and_symlink_rename_preserve_modes(self):
        self.git("mv", "scripts/hookshot_ctl.py", "scripts/hookshot_runner.py")
        (self.root / "ctl").unlink()
        (self.root / "ctl").symlink_to("scripts/hookshot_runner.py")
        self.put("Hookshot extra/naïve hookshot.txt", "HOOKSHOT mode\n")
        self.commit("refactor: scripts/hookshot_runner.py")
        self.invoke()
        self.assertEqual(self.show("ctl"), b"scripts/longshot_runner.py")
        self.assertTrue(self.git("ls-tree", "longshot/topic", "ctl").startswith(b"120000"))
        self.assertEqual(self.show("Longshot extra/naïve longshot.txt"), b"LONGSHOT mode\n")
        names = self.git("ls-tree", "-r", "--name-only", "longshot/topic").decode()
        self.assertNotIn("longshot_ctl.py", names)
        self.assertIn("longshot_runner.py", names)

    def test_new_links_external_names_and_history_stay_unchanged(self):
        lines = ["https://github.com/NotRexed/Hookshot/issues/11", "", "spacer",
                 "See https://github.com/NotRexed/Hookshot/pull/2904#issuecomment-1",
                 "Use mooki000/hookshot-workbench and skill://hookshot-pr-evidence.",
                 '> Historical: "Hookshot uses hookshot_log.txt"',
                 'The reporter said “Hookshot failed in hookshot_vk.so”.',
                 'Use hookshot_log.txt with HOOKSHOT_JOBS.']
        self.put("docs/links.md", "\n".join(lines) + "\n")
        self.commit("docs: link Hookshot #2904")
        receipt = self.invoke()
        result = self.show("docs/links.md").decode()
        # Existing main links are not silently repaired; only our introduced lines keep their URLs.
        self.assertIn("NotRexed/Longshot/issues/11", result)
        for line in lines[3:7]:
            self.assertIn(line, result)
        self.assertIn("Use longshot_log.txt with LONGSHOT_JOBS.", result)
        self.assertEqual([m["line"] for m in receipt["final_old_mentions"]], [4, 5, 6, 7])

    def test_check_writes_nothing_to_source_repository(self):
        self.put("scripts/hookshot_ctl.py", "print('hookshot')\n")
        self.commit("test: script")
        before = self.fingerprint()
        receipt = self.invoke(check=True)
        self.assertTrue(receipt["complete"])
        self.assertFalse(receipt["published_locally"])
        self.assertEqual(before, self.fingerprint())

    def test_conflict_is_reported_without_partial_branch_or_objects(self):
        self.put("readme.md", "# Hookshot\n\nOUR conflicting main description\n\nunchanged spacer\n\nbranch description\n")
        self.commit("docs: conflict")
        self.put("other", "followup")
        self.commit("followup")
        before = self.fingerprint()
        receipt = self.invoke(expected=1)
        self.assertEqual(receipt["conflicts"], ["readme.md"])
        self.assertEqual(receipt["commits"][1]["status"], "not simulated after earlier conflict")
        self.assertEqual(receipt["commit_count"], 2)
        self.assertEqual(receipt["file_count"], 2)
        self.assertEqual(before, self.fingerprint())

    def test_existing_destination_is_never_moved(self):
        self.git("branch", "longshot/topic", self.base)
        before = self.fingerprint()
        self.assertIn("already exists", self.invoke(expected=2)["error"])
        self.assertEqual(before, self.fingerprint())

    def test_origin_main_is_trivial(self):
        receipt = self.invoke(branch="origin/main")
        self.assertEqual(receipt["head"], self.onto)
        self.assertEqual(receipt["commit_count"], 0)
        self.assertEqual(receipt["files"], [])

    def test_explicit_base_only_carries_selected_commits(self):
        self.put("a", "first\n")
        first = self.commit("first")
        self.put("b", "second\n")
        self.commit("second")
        receipt = self.invoke(base=first)
        self.assertEqual(receipt["commit_count"], 1)
        self.assertEqual(receipt["files"], ["b"])
        self.assertNotIn("a\n", self.git("ls-tree", "--name-only", "longshot/topic").decode())

    def test_new_path_collision_refuses_without_writes(self):
        self.put("scripts/longshot_ctl.py", "collision\n")
        self.commit("test: collision")
        before = self.fingerprint()
        self.assertIn("collision", self.invoke(expected=2)["error"])
        self.assertEqual(before, self.fingerprint())

    def test_merge_topology_and_result_are_preserved(self):
        self.put("a", "hookshot a\n")
        self.commit("first")
        self.git("checkout", "--quiet", "-b", "side", self.base)
        self.put("b", "hookshot b\n")
        self.commit("side")
        self.git("checkout", "--quiet", "topic")
        self.git("merge", "--no-ff", "--quiet", "side", "-m", "merge side")
        receipt = self.invoke()
        self.assertEqual(receipt["commit_count"], 3)
        self.assertEqual(self.show("a"), b"longshot a\n")
        self.assertEqual(self.show("b"), b"longshot b\n")
        self.assertEqual(len(self.git("show", "-s", "--format=%P", "longshot/topic").split()), 2)

    def test_exceptions_survive_later_commit_and_file_move(self):
        self.put("docs/new.md", "https://github.com/NotRexed/Hookshot/pull/2904\n\nspacer\n")
        self.commit("first")
        self.git("mv", "docs/new.md", "docs/hookshot-notes.md")
        self.put("docs/hookshot-notes.md", "https://github.com/NotRexed/Hookshot/pull/2904\n\nspacer\nnew hookshot file\n")
        self.commit("second")
        self.invoke()
        self.assertEqual(self.show("docs/longshot-notes.md"), b"https://github.com/NotRexed/Hookshot/pull/2904\n\nspacer\nnew longshot file\n")

    def test_comment_history_and_message_technical_rules(self):
        text = '// Original text: "Hookshot uses hookshot_log.txt"\nconst char *p = "hookshot_log.txt";\n'
        self.assertEqual(port.transform_text(text, "source.cpp"), '// Original text: "Hookshot uses hookshot_log.txt"\nconst char *p = "longshot_log.txt";\n')
        result, changes = port.transform_message('Hookshot: run "scripts/hookshot_ctl.py --hookshot-test"; #12\nhttps://github.com/NotRexed/Hookshot/pull/12\n', [])
        self.assertIn('Hookshot: run "scripts/longshot_ctl.py --longshot-test"; #12', result)
        self.assertIn("NotRexed/Hookshot/pull/12", result)
        self.assertEqual(len(changes), 2)

    def test_project_sentence_punctuation_and_single_quoted_history(self):
        original = "This was Hookshot. Hookshot: old project; #2904 and #2929.\n"
        self.assertEqual(port.transform_message(original, []), (original, []))
        result, changes = port.transform_message("Read hookshot_log.txt. See scripts/hookshot_ctl.py.\n", [])
        self.assertEqual(result, "Read longshot_log.txt. See scripts/longshot_ctl.py.\n")
        self.assertEqual(len(changes), 2)
        quoted = "The reporter wrote 'Hookshot used hookshot_vk.so'. Use hookshot_log.txt.\n"
        self.assertEqual(port.transform_text(quoted, "notes.md"),
                         "The reporter wrote 'Hookshot used hookshot_vk.so'. Use longshot_log.txt.\n")
        provenance = "Imported from NotRexed/Hookshot#1025; mooki000/Hookshot#7.\n"
        self.assertEqual(port.transform_message(provenance, []), (provenance, []))
        self.assertEqual(port.transform_text(provenance, "notes.md"), provenance)
        short_quote = """The reporter wrote "Hookshot". Use hookshot_log.txt.\n"""
        self.assertEqual(port.transform_text(short_quote, "notes.md"),
                         """The reporter wrote "Hookshot". Use longshot_log.txt.\n""")
        self.assertEqual(port.transform_text("skill://hookshot-future-skill", "notes.md"),
                         "skill://hookshot-future-skill")

    def test_live_protocol_literals_are_not_historical_quotes(self):
        for path in ("docs/coop.md", "host/coop_proto.h", "host/coop_state.c"):
            text = '// locator = BLAKE2b-128("hookshot-coop/2 room")\n'
            self.assertEqual(port.transform_text(text, path),
                             '// locator = BLAKE2b-128("longshot-coop/2 room")\n')

    def test_export_hand_resolve_and_continue_with_followup(self):
        self.put("readme.md", "# Hookshot\n\nOUR conflicting main description\n\nunchanged spacer\n\nbranch description\n")
        first = self.commit("docs: conflict")
        self.put("followup", "hookshot followup\n")
        self.commit("followup")
        directory = self.root / "resolutions"
        receipt = self.invoke(expected=1, extra=("--conflict-dir", str(directory)))
        self.assertFalse(receipt["published_locally"])
        folder = directory / first
        self.assertIn(b"Longshot's own main description", (folder / "000.current").read_bytes())
        self.assertIn(b"OUR conflicting main description", (folder / "000.incoming").read_bytes())
        resolved = b"# Longshot\n\nLongshot's own main description; OUR conflicting main description\n\nunchanged spacer\n\nbranch description\n"
        (folder / "000.resolved").write_bytes(resolved)
        before = self.fingerprint()
        checked = self.invoke(check=True, extra=("--resolutions", str(directory)))
        self.assertEqual(before, self.fingerprint())
        self.assertEqual(checked["commits"][0]["status"], "hand-resolved")
        completed = self.invoke(extra=("--resolutions", str(directory), "--conflict-dir", str(directory)))
        self.assertEqual(completed["head"], checked["head"])
        self.assertEqual(self.show("readme.md"), resolved)
        self.assertEqual(self.show("followup"), b"longshot followup\n")

    def test_resolution_rejects_stale_inputs_and_markers(self):
        self.put("readme.md", "# Hookshot\n\nOUR conflicting main description\n\nunchanged spacer\n\nbranch description\n")
        first = self.commit("docs: conflict")
        directory = self.root / "resolutions"
        self.invoke(expected=1, extra=("--conflict-dir", str(directory)))
        folder = directory / first
        (folder / "000.resolved").write_bytes((folder / "000.merged").read_bytes())
        refused = self.invoke(expected=2, extra=("--resolutions", str(directory)))
        self.assertIn("conflict marker", refused["error"])
        (folder / "000.resolved").write_text("resolved\n")
        manifest = json.loads((folder / "manifest.json").read_text())
        manifest["current"] = self.base
        (folder / "manifest.json").write_text(json.dumps(manifest))
        refused = self.invoke(expected=2, extra=("--resolutions", str(directory)))
        self.assertIn("stale resolution", refused["error"])

    def test_check_cannot_export_files(self):
        refused = self.invoke(check=True, expected=2, extra=("--conflict-dir", str(self.root / "no-write")))
        self.assertIn("cannot export", refused["error"])
        self.assertFalse((self.root / "no-write").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
