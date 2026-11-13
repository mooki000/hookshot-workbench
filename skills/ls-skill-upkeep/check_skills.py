#!/usr/bin/env python3
"""Find what is wrong with the skills in a folder: the mechanical part of keeping them current.

    check_skills.py [--root DIR] [--only PREFIX ...] [--quiet]

DIR holds one folder per skill, each with a SKILL.md (default: ~/.agents/skills). For every skill it reports:
  FAIL  no front matter, a name that is not the folder's, no description, a `skill://` reference to a skill or a
        file that is not there
  WARN  a description that does not say when to use the skill or is too long for the catalogue, an absolute path
        under the home folder that does not exist (the usual sign of a stale skill), a script the text names that is
        neither in the skill's folder nor in the port's repository ($LS_REPO, default ~/Code/Hookshot, at forge/main
        or HEAD), a body over 400 lines
Exit status 1 when anything FAILs. Standard library only; it reads files and asks git whether a path exists.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PORT = os.environ.get("LS_REPO", os.path.expanduser("~/Code/Hookshot"))


def in_port(path):
    """Whether the port's repository has this path at forge/main, or at HEAD when there is no such branch."""
    for rev in ("forge/main", "HEAD"):
        p = subprocess.run(["git", "-C", PORT, "cat-file", "-e", "%s:%s" % (rev, path)], capture_output=True)
        if p.returncode == 0:
            return True
    return False


TRIGGER = re.compile(r"\b(use|start here|when|before|for)\b", re.I)
HOME_PATH = re.compile(r"(?<![\w.$])(/home/[\w.-]+/[^\s`'\"()<>,;|*{}\[\]]+)")
SKILL_REF = re.compile(r"skill://([a-z0-9][a-z0-9-]*)((?:/[\w.-]+)*)")
SCRIPT_REF = re.compile(r"`((?:scripts|references|assets)/[\w./-]+)`")


def front_matter(text):
    """(fields, body) of a SKILL.md; fields is None when there is no front matter. Reads `key: value` and folded
    (`key: >`) values, which is all these files use."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---", 4)
    if end < 0:
        return None, text
    fields, key = {}, None
    for line in text[4:end].splitlines():
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", line)
        if m:
            key = m.group(1)
            fields[key] = "" if m.group(2).strip() in (">", "|", ">-", "|-") else m.group(2).strip().strip("'\"")
        elif key and line.startswith((" ", "\t")):
            fields[key] = (fields[key] + " " + line.strip()).strip()
    return fields, text[end + 4:]


def check(root, only):
    names = sorted(p.name for p in root.iterdir() if (p / "SKILL.md").is_file())
    failed = 0
    for name in names:
        if only and not any(name.startswith(o) for o in only):
            continue
        folder = root / name
        text = (folder / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        fields, body = front_matter(text)
        fail, warn = [], []
        if fields is None:
            fail.append("no front matter (--- name / description ---)")
            fields = {}
        if fields.get("name", name) != name:
            fail.append("name %r is not the folder's name" % fields.get("name"))
        description = fields.get("description", "")
        if not description:
            fail.append("no description: the catalogue cannot offer this skill")
        else:
            if not TRIGGER.search(description):
                warn.append("the description does not say when to use the skill")
            if len(description) > 700:
                warn.append("the description is %d characters (every session pays for it; aim under 700)" % len(description))
        for ref, path in sorted(set((r, p.rstrip(".")) for r, p in SKILL_REF.findall(text))):   # (a sentence's full stop)
            if ref not in names:
                fail.append("skill://%s: no such skill here" % ref)
            elif path and not (root / ref / path.lstrip("/")).exists():
                fail.append("skill://%s%s: no such file" % (ref, path))
        for path in sorted(set(HOME_PATH.findall(text))):
            path = path.rstrip(".:")
            if any(c in path for c in "<>$*") or path.endswith(("...", "…")):
                continue
            if not os.path.exists(os.path.expanduser(path)):
                warn.append("path not found: %s" % path)
        for script in sorted(set(SCRIPT_REF.findall(body))):
            if not (folder / script).exists() and not in_port(script):
                warn.append("named in the text; neither in the skill's folder nor in the port's repository: %s" % script)
        lines = body.count("\n")
        if lines > 400:
            warn.append("%d lines: move reference material into files beside SKILL.md" % lines)
        failed += bool(fail)
        yield name, fail, warn


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=os.path.expanduser("~/.agents/skills"))
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--quiet", action="store_true", help="print only skills that have something to report")
    a = ap.parse_args()
    bad = total = 0
    for name, fail, warn in check(Path(a.root), a.only):
        total += 1
        bad += bool(fail)
        if fail or warn or not a.quiet:
            print("%-4s %s" % ("FAIL" if fail else "WARN" if warn else "OK", name))
        for f in fail:
            print("     FAIL  " + f)
        for w in warn:
            print("     WARN  " + w)
    print("%d skills checked, %d with failures" % (total, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
