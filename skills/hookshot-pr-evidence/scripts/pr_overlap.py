#!/usr/bin/env python3
"""Which open pull requests of NotRexed/Hookshot overlap with ours, and which of those really conflict.

    pr_overlap.py                      every open pull request by mooki000
    pr_overlap.py 2274 2306            only these
    pr_overlap.py --branch walk-key    a local branch that is not a pull request yet

For each of ours: the open pull requests of others that touch one of its files, and among them the ones where
`git merge-tree` reports a conflict in one of OUR files that the other pull request does not already have with main
(an old branch conflicts with main in many files that are none of our business). Read the conflict hunk before
describing an overlap in a post: this only says where to look.
"""
import argparse
import concurrent.futures as cf
import json
import subprocess
import sys

REPO = "NotRexed/Hookshot"
CLONE = "/home/mooki/Code/Hookshot"


def gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=300)
    if r.returncode:
        sys.exit("gh " + " ".join(args) + ": " + r.stderr.strip()[:400])
    return r.stdout


def git(*args):
    return subprocess.run(["git", "-C", CLONE, *args], capture_output=True, text=True, timeout=600)


def conflicts(a, b):
    r = git("merge-tree", "--write-tree", "--name-only", a, b)
    out = []
    if r.returncode == 1:
        for line in r.stdout.splitlines()[1:]:
            if not line.strip():
                break
            out.append(line.strip())
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("numbers", nargs="*", type=int)
    ap.add_argument("--branch", help="a local branch instead of pull requests")
    ap.add_argument("--author", default="mooki000")
    args = ap.parse_args()

    prs = json.loads(gh("pr", "list", "--repo", REPO, "--state", "open", "--limit", "500", "--json",
                        "number,title,author,isDraft"))
    meta = {p["number"]: p for p in prs}
    numbers = [p["number"] for p in prs]
    with cf.ThreadPoolExecutor(8) as ex:
        lists = ex.map(lambda n: gh("api", f"repos/{REPO}/pulls/{n}/files?per_page=100", "--jq",
                                    ".[].filename").split("\n"), numbers)
    files = {n: [f for f in fl if f] for n, fl in zip(numbers, lists)}

    if git("fetch", "-q", "origin", "main").returncode:
        sys.exit("git fetch origin main failed")
    ours = {}
    if args.branch:
        changed = git("diff", "--name-only", f"origin/main...{args.branch}").stdout.split()
        ours[args.branch] = (args.branch, changed, f"branch {args.branch}")
    else:
        mine = args.numbers or sorted(n for n in numbers if meta[n]["author"]["login"] == args.author)
        for n in mine:
            if n not in files:
                sys.exit(f"#{n} is not an open pull request")
            ours[n] = (f"refs/remotes/pr/{n}", files[n], f"#{n} {meta[n]['title'][:70]}")
    own_numbers = {k for k in ours if isinstance(k, int)} | {n for n in numbers
                                                              if meta[n]["author"]["login"] == args.author}
    others = sorted({n for _, fl, _ in ours.values() for n in numbers
                     if n not in own_numbers and set(files[n]) & set(fl)})
    need = others + [k for k in ours if isinstance(k, int)]
    for i in range(0, len(need), 40):
        r = git("fetch", "-q", "origin", *[f"+refs/pull/{n}/head:refs/remotes/pr/{n}" for n in need[i:i + 40]])
        if r.returncode:
            sys.exit("git fetch of pull request heads failed: " + r.stderr[:300])

    with_main = {}
    for key, (ref, fl, label) in ours.items():
        sharing = [n for n in others if set(files[n]) & set(fl)]
        print(f"{label}: {len(sharing)} open pull requests touch one of its {len(fl)} files")
        code = []
        for n in sharing:
            common = sorted(set(files[n]) & set(fl))
            if n not in with_main:
                with_main[n] = set(conflicts("origin/main", f"refs/remotes/pr/{n}"))
            real = sorted((set(conflicts(ref, f"refs/remotes/pr/{n}")) - with_main[n]) & set(fl))
            who = meta[n]["author"]["login"] + (" (draft)" if meta[n]["isDraft"] else "")
            if real:
                print(f"   CONFLICT #{n} {who}: {meta[n]['title'][:60]} :: {', '.join(real)}")
            elif any(not f.endswith(".md") for f in common):
                code.append(f"#{n}")
        if code:
            print("   same code file, merges cleanly: " + ", ".join(code))


if __name__ == "__main__":
    main()
