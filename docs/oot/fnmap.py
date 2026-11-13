#!/usr/bin/env python3
"""Look up and check the map of the game's lifted functions (the pages beside this script).

    fnmap.py find f_803edf70        the row of one function or data address, with its page
    fnmap.py find "time of day"     rows whose name or text has the words (first 20); with no such row,
                                    the lines of the pages that have them (a thread or class name in the prose)
    fnmap.py check [--repo DIR] [--rev REV]
                                    the pages against themselves and against the port's code at REV
                                    (DIR: the port's clone, default $LS_REPO or the current folder; REV: default HEAD)

Standard library only.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PAGES = Path(__file__).resolve().parent
ROOT = PAGES.parents[1]
ROW = re.compile(r"^\| `(f_[0-9a-f]{8}|0x[0-9a-f]{6,10})` \|")
HEADERS = ("| Function | Name | What it does | Sure |", "| Address | Name | What it is | Sure |")
SURE = {"tested", "read", "guess"}
# Named in the port's code but not a function of this game (a comment about another one).
NOT_OURS = PAGES / "undocumented.txt"


def pages():
    return sorted(p for p in PAGES.glob("*.md"))


def table_rows():
    """(page, line number, address, cells) of every row of a table with one of the two standard headers."""
    for page in pages():
        if page.name == "README.md":   # (the conventions page: its rows are examples)
            continue
        standard = False
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            if line.startswith("| ") and not ROW.match(line):
                standard = line.strip() in HEADERS or (standard and set(line.strip()) <= set("|-: "))
            elif not line.startswith("|"):
                standard = False
            m = ROW.match(line)
            if m and standard:
                yield page, number, m.group(1), [c.strip() for c in line.strip().strip("|").split(" | ")]


def find(query):
    query = query.strip().strip("`")
    exact = re.fullmatch(r"(f_)?(0x)?([0-9a-fA-F]{6,10})", query)
    words = query.lower().split()
    shown = 0
    for page in pages():
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            if exact:
                hit = exact.group(3).lower() in line.lower() and ROW.match(line) and \
                      ROW.match(line).group(1).lower().endswith(exact.group(3).lower())
            else:
                hit = ROW.match(line) and all(w in line.lower() for w in words)
            if hit:
                print("%s:%d\n  %s" % (page.relative_to(ROOT), number, line))
                shown += 1
                if shown == 20:
                    return 0
    if not shown:   # (no row: say where the pages mention it, in a row of another function or in the prose)
        for page in pages():
            for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
                low = line.lower()
                if (exact.group(3).lower() in low) if exact else all(w in low for w in words):
                    print("mentioned, no row: %s:%d\n  %s" % (page.relative_to(ROOT), number, line.strip()[:240]))
                    shown += 1
                    if shown == 20:
                        return 0
    if not shown:
        print("nothing for %r" % query)
    return 0 if shown else 1


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=True).stdout


def check(repo, rev):
    problems, notes = [], []
    seen = {}
    for page, number, address, cells in table_rows():
        where = "%s:%d" % (page.name, number)
        if len(cells) != 4 or cells[-1] not in SURE:
            problems.append("%s: %s: a row needs four cells ending in tested, read or guess" % (where, address))
        if address in seen:
            problems.append("%s: %s has a second row (first: %s)" % (where, address, seen[address]))
        seen.setdefault(address, where)
    index = (PAGES / "README.md").read_text(encoding="utf-8")
    for page in pages():
        if page.name != "README.md" and "(%s)" % page.name not in index:
            problems.append("README.md: the index does not list %s" % page.name)
        fenced = False
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
            if fenced:
                continue
            for target in re.findall(r"\]\(([a-z0-9-]+\.md)(?:#[^)]*)?\)", line):
                if not (PAGES / target).is_file():
                    problems.append("%s:%d: link to a page that is not here: %s" % (page.name, number, target))
    not_ours = set(NOT_OURS.read_text(encoding="utf-8").split()) if NOT_OURS.is_file() else set()
    try:
        named = set(git(repo, "grep", "-h", "-o", "-E", r"\bf_[0-9a-f]{8}\b", rev, "--", "nx2/oot_*.py", "host").split())
        patches = set(re.findall(r"^def (patch_\w+)", git(repo, "grep", "-h", "-E", r"^def patch_\w+", rev, "--", "nx2"), re.M))
        sites = set(re.findall(r'\("void (f_[0-9a-f]{8})\(Ctx', git(repo, "show", rev + ":nx2/oot_patches.py")))
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print("could not read the port's code at %s in %s: %s (--repo or LS_REPO: its clone)" % (rev, repo, e))
        return 2
    for f in sorted(named - set(seen) - not_ours):
        problems.append("%s is named in the port's code (nx2/oot_*.py or host/) and has no row" % f)
    for f in sorted(not_ours & set(seen)):
        problems.append("%s is in undocumented.txt and has a row: take it off the list" % f)
    text = "\n".join(p.read_text(encoding="utf-8") for p in pages() if p.name != "README.md")
    for name in sorted(set(re.findall(r"Hooked by `(patch_\w+)`", text)) - patches):
        problems.append("a page says 'Hooked by `%s`', which nx2/ does not define at %s" % (name, rev))
    for f in sorted(sites):
        row = next((c for _, _, a, c in table_rows() if a == f), None)
        if row and "Hooked by" not in row[2]:
            notes.append("%s is patched in nx2/oot_patches.py; its row does not say 'Hooked by'" % f)
    counts = {s: sum(1 for _, _, _, c in table_rows() if c[-1] == s) for s in sorted(SURE)}
    print("%d pages, %d rows (%s); %d functions named in code at %s, %d of them with a row" % (
        len(pages()) - 1, len(seen), ", ".join("%s %d" % kv for kv in counts.items()), len(named - not_ours), rev,
        len((named - not_ours) & set(seen))))
    for n in notes:
        print("note: " + n)
    for p in problems:
        print("FAIL: " + p)
    print("PASS" if not problems else "FAIL: %d problems" % len(problems))
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("find").add_argument("query")
    c = sub.add_parser("check")
    c.add_argument("--repo", default=os.environ.get("LS_REPO", "."))
    c.add_argument("--rev", default="HEAD")
    a = ap.parse_args()
    return find(a.query) if a.cmd == "find" else check(a.repo, a.rev)


if __name__ == "__main__":
    sys.exit(main())
