#!/usr/bin/env python3
"""Locally replay HS commits on LS, without checking out any branch.

Run in the source clone: ls-port.py topic --onto forge/main --name
longshot/topic [--base COMMIT] [--check]. Output is a JSON receipt. --check
changes no source objects, refs, index or worktrees; its temporary bare object
store is deleted on exit. Exit 0 = clean, 1 = conflict, 2 = invalid input/error.
No network, hooks, credentials, signing, checkout, or conflict auto-resolution.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

IDENTITY = ("Michael Schienbein", "7663497+mooki000@users.noreply.github.com")
PAIRS = (("hookshot", "longshot"), ("Hookshot", "Longshot"), ("HOOKSHOT", "LONGSHOT"))
OLD = re.compile("hookshot", re.I)
URL = re.compile(r"(?:https?://|git@)[^\s<>\"'`)\]]+")
HISTORICAL_SKILL_NAMES = (
    "hookshot-workbench", "hookshot-pr-evidence", "hookshot-issue-triage",
    "hookshot-perf-compare", "hookshot-scripted-play", "hookshot-regression-hunt",
    "hookshot-nightly-playtest", "hookshot-discord-updates", "hookshot-pr-loop",
    "hookshot-perf-monitor",
)
EXTERNAL = re.compile(r"(?:NotRexed|mooki000)/Hookshot(?![\w-])(?:#\d+)?|"
                      r"skill://hookshot-[\w-]+|(?:mooki000/)?hookshot-workbench(?:/[\w./-]+)?|(?:"
                      + "|".join(re.escape(s) for s in HISTORICAL_SKILL_NAMES) + r")(?![\w-])")
HISTORICAL_QUOTES = re.compile(r"""“[^”]*”|‘[^’]*’|"[^"\n]*"|(?<!\w)'[^'\n]*'(?!\w)""")
HISTORY = re.compile(r"histor(?:ic|y)|verbatim|quot(?:e|ed)|reporter|\bsaid\b|\bwrote\b|original text", re.I)
SPECIAL = {
    "CONTRIBUTING.md", "readme.md", "gpu/vk/menu_i18n.h",
    "gpu/vk/shaders/hdr_encode_comp_spv.h", "tests/test_finish_output.py",
    "tests/test_linux_update.py", "assets/longshot.png",
    "builder/LongshotBuilder/Images/longshot.png",
    "builder/py/longshot_builder/assets/longshot.png",
}


class PortError(Exception):
    pass


def rename(text):
    for old, new in PAIRS:
        text = text.replace(old, new)
    return text


def text_blob(data):
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def protected_spans(text, path):
    """Protect external identities and explicitly quoted historical material."""
    spans = [(m.start(), m.end()) for regex in (URL, EXTERNAL) for m in regex.finditer(text)]
    documentation = Path(path).suffix.lower() in {".md", ".rst", ".txt"}
    offset = 0
    historical_fence = False
    previous = ""
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(("```", "~~~")):
            if historical_fence:
                historical_fence = False
            elif HISTORY.search(previous) or HISTORY.search(line):
                historical_fence = True
        elif historical_fence or (documentation and line.lstrip().startswith(">")):
            spans.append((offset, offset + len(line)))
        if HISTORY.search(line):
            spans.extend((offset + m.start(), offset + m.end()) for m in HISTORICAL_QUOTES.finditer(line))
        offset += len(line)
        if line.strip():
            previous = line
    return spans


def transform_text(text, path):
    spans = sorted(protected_spans(text, path))
    chunks, end = [], 0
    for start, stop in spans:
        if stop <= end:
            continue
        if start > end:
            chunks.append(rename(text[end:start]))
        chunks.append(text[max(end, start):stop])
        end = stop
    chunks.append(rename(text[end:]))
    return "".join(chunks)


def transform_message(message, paths):
    """Only technical tokens change, never ordinary project-name prose."""
    protected = protected_spans(message, "message")
    known = {p for p in paths if OLD.search(p)}
    known |= {p.rsplit("/", 1)[-1] for p in known}
    token = re.compile(r"[\w./\\~:-]+", re.UNICODE)
    changes = []

    def replace(match):
        word = match.group()
        if not OLD.search(word) or any(a < match.end() and match.start() < b for a, b in protected):
            return word
        technical = (word in known and word not in {"hookshot", "Hookshot", "HOOKSHOT"})
        technical |= bool(re.search(r"[/\\]|^--|^HOOKSHOT_|\.[A-Za-z0-9]+(?:[.:]?$)", word))
        # A bare executable is unambiguous in code ticks, not in ordinary prose.
        technical |= word == "hookshot" and match.start() > 0 and message[match.start() - 1] == "`"
        new = rename(word) if technical else word
        if new != word:
            changes.append({"line": message.count("\n", 0, match.start()) + 1, "old": word, "new": new})
        return new

    return token.sub(replace, message), changes


def environment():
    env = os.environ.copy()
    # Do not inherit another worktree's index/object overrides, or identity/config injection.
    for key in list(env):
        if key.startswith("GIT_"):
            del env[key]
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    return env


class Git:
    def __init__(self, path, env=None):
        self.path = str(path)
        self.env = environment() if env is None else env

    def run(self, *args, data=None, allowed=(0,)):
        proc = subprocess.run(["git", "-C", self.path, "-c", "core.hooksPath=/dev/null",
                               "-c", "commit.gpgSign=false", "-c", "gc.auto=0",
                               "-c", "user.name=" + IDENTITY[0],
                               "-c", "user.email=" + IDENTITY[1], *args],
                              input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
        if proc.returncode not in allowed:
            raise PortError(f"git {' '.join(args)}: {proc.stderr.decode('utf-8', 'replace').strip()}")
        return proc

    def out(self, *args, **kwargs):
        return self.run(*args, **kwargs).stdout

    def oid(self, ref):
        return self.out("rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()


class Port:
    def __init__(self, source, scratch, base):
        self.source, self.git, self.base = source, scratch, base
        self.trees = {}
        self.renamed = {}
        self.blobs = {}
        self.batch = subprocess.Popen(["git", "-C", scratch.path, "cat-file", "--batch"],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=scratch.env)
        self.baseline = self.entries(base)

    def close(self):
        self.batch.stdin.close()
        self.batch.stdout.close()
        self.batch.wait()

    def blob(self, oid):
        self.batch.stdin.write(oid.encode() + b"\n")
        self.batch.stdin.flush()
        header = self.batch.stdout.readline().split()
        if len(header) != 3:
            raise PortError(f"missing object: {oid}")
        data = self.batch.stdout.read(int(header[2]))
        if self.batch.stdout.read(1) != b"\n":
            raise PortError("invalid cat-file response")
        return data

    def store(self, kind, data):
        return self.git.out("hash-object", "-w", "-t", kind, "--stdin", data=data).decode().strip()

    def entries(self, commit):
        if commit not in self.trees:
            entries = {}
            for row in self.git.out("ls-tree", "-rz", commit).split(b"\0"):
                if row:
                    metadata, path = row.split(b"\t", 1)
                    mode, kind, oid = metadata.decode().split()
                    entries[path.decode("utf-8", "surrogateescape")] = (mode, kind, oid)
            self.trees[commit] = entries
        return self.trees[commit]

    def origins(self, commit):
        # Match source renames too; a moved file does not acquire new historical lines.
        fields = self.git.out("diff-tree", "--no-commit-id", "-r", "-M", "--name-status", "-z",
                              self.base, commit).split(b"\0")
        origins = {}
        i = 0
        while i < len(fields) and fields[i]:
            status, path = fields[i].decode(), fields[i + 1].decode("utf-8", "surrogateescape")
            i += 2
            if status.startswith(("R", "C")):
                dest = fields[i].decode("utf-8", "surrogateescape")
                i += 1
                origins[dest] = path
        return origins

    def renamed_tree(self, commit):
        if commit in self.renamed:
            return self.renamed[commit]
        entries = self.entries(commit)
        origins = self.origins(commit) if commit != self.base else {}
        mapped = {}
        updates = []
        for path, (mode, kind, oid) in entries.items():
            newpath = rename(path)
            if newpath in mapped:
                raise PortError(f"rename collision: {path!r} and {mapped[newpath]!r} -> {newpath!r}")
            mapped[newpath] = path
            newoid = oid
            if kind == "blob":
                original = self.baseline.get(origins.get(path, path))
                baseoid = original[2] if original and original[1] == "blob" else None
                # Path matters only to the documentation/history policy.
                key = (oid, baseoid, mode, Path(path).suffix.lower())
                if key in self.blobs:
                    newoid = self.blobs[key]
                else:
                    data = self.blob(oid)
                    text = text_blob(data)
                    if text is not None:
                        if mode == "120000":
                            result = rename(text)  # preserve the symlink, update its target spelling
                        elif baseoid == oid:
                            result = rename(text)
                        else:
                            result_lines = transform_text(text, path).splitlines(keepends=True)
                            base_text = text_blob(self.blob(baseoid)) if baseoid else None
                            if base_text is not None:
                                before = base_text.splitlines(keepends=True)
                                after = text.splitlines(keepends=True)
                                for match in difflib.SequenceMatcher(None, before, after, autojunk=False).get_matching_blocks():
                                    result_lines[match.b:match.b + match.size] = [rename(s) for s in after[match.b:match.b + match.size]]
                            result = "".join(result_lines)
                        encoded = result.encode("utf-8")
                        if encoded != data:
                            newoid = self.store("blob", encoded)
                    self.blobs[key] = newoid
            if newpath != path:
                updates.append(b"0 " + b"0" * len(oid) + b"\t" + path.encode("utf-8", "surrogateescape") + b"\0")
            if newpath != path or newoid != oid:
                updates.append(f"{mode} {kind} {newoid}\t".encode() + newpath.encode("utf-8", "surrogateescape") + b"\0")
        self.git.out("read-tree", commit)
        if updates:
            self.git.out("update-index", "-z", "--index-info", data=b"".join(updates))
        tree = self.git.out("write-tree").decode().strip()
        self.renamed[commit] = tree
        return tree

    def metadata(self, commit):
        raw = self.blob(commit)
        headers, message = raw.split(b"\n\n", 1)
        fields = {}
        for line in headers.splitlines():
            if not line.startswith(b" "):
                key, value = line.split(b" ", 1)
                fields.setdefault(key, []).append(value)
        return fields, message

    def commit(self, tree, parents, fields, message):
        # Preserve raw author (including date/timezone), message and encoding. Signing
        # headers cannot survive a tree change; never copy an invalid old signature.
        date = fields[b"committer"][0].rsplit(b"> ", 1)[1]
        headers = [b"tree " + tree.encode()]
        headers.extend(b"parent " + p.encode() for p in dict.fromkeys(parents))
        headers.append(b"author " + fields[b"author"][0])
        headers.append(f"committer {IDENTITY[0]} <{IDENTITY[1]}> ".encode() + date)
        if b"encoding" in fields:
            headers.append(b"encoding " + fields[b"encoding"][0])
        return self.store("commit", b"\n".join(headers) + b"\n\n" + message)

    def old_mentions(self, parent, tree):
        result = []
        before, after = self.entries(parent), self.entries(tree)
        for path, (mode, kind, oid) in after.items():
            if kind != "blob" or before.get(path) == after[path]:
                continue
            text = text_blob(self.blob(oid))
            if text is None:
                continue
            old = before.get(path)
            oldtext = text_blob(self.blob(old[2])) if old and old[1] == "blob" else ""
            lines = text.splitlines()
            for tag, a, b, c, d in difflib.SequenceMatcher(None, (oldtext or "").splitlines(), lines, autojunk=False).get_opcodes():
                if tag in {"insert", "replace"}:
                    for index in range(c, d):
                        if OLD.search(lines[index]):
                            result.append({"file": path, "line": index + 1, "text": lines[index]})
        return result


def atomic_file(path, data):
    """Publish one complete conflict artifact, never a partly written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".port-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def resolve_conflict(port, commit, before, current, after, merged, names, export, resolutions):
    """Replay explicit hand resolutions only against identical three-way inputs.

    --conflict-dir DIR exports DIR/<source-commit>/manifest.json and numbered
    .base/.current/.incoming/.merged blobs. Copy/edit .merged to .resolved, or
    write exactly 'delete\\n' to .delete. Rerun the original pinned command with
    --resolutions DIR (and --conflict-dir DIR to export the next conflict).
    No edits to ordinary worktrees or existing branch refs are required.
    """
    sides = {label: port.entries(tree) for label, tree in
             (("base", before), ("current", current), ("incoming", after), ("merged", merged))}
    manifest = {"source": commit, "base_tree": before, "current": current,
                "incoming_tree": after, "merged_tree": merged, "files": []}
    for index, path in enumerate(names):
        item = {"path": path, "slot": f"{index:03d}"}
        for label, entries in sides.items():
            item[label] = list(entries[path]) if path in entries else None
        manifest["files"].append(item)
    if export:
        folder = Path(export) / commit
        existing = folder / "manifest.json"
        if existing.exists():
            if json.loads(existing.read_text()) != manifest:
                raise PortError(f"stale conflict export: {existing}; choose a new directory")
        else:
            for item in manifest["files"]:
                for label in sides:
                    entry = item[label]
                    if entry is not None:
                        atomic_file(folder / (item["slot"] + "." + label), port.blob(entry[2]))
            atomic_file(existing, (json.dumps(manifest, indent=2) + "\n").encode())
    if not resolutions:
        return None, manifest
    folder = Path(resolutions) / commit
    saved = folder / "manifest.json"
    if not saved.exists():
        return None, manifest
    if json.loads(saved.read_text()) != manifest:
        raise PortError(f"stale resolution: {saved}; three-way inputs changed")
    replacements = []
    for item in manifest["files"]:
        resolved = folder / (item["slot"] + ".resolved")
        deleted = folder / (item["slot"] + ".delete")
        if resolved.exists() and deleted.exists():
            raise PortError(f"choose .resolved OR .delete for {item['path']}")
        if deleted.exists():
            if deleted.read_bytes() != b"delete\n":
                raise PortError(f"deletion must contain exactly 'delete\\n': {deleted}")
            replacements.append((item["path"], None))
        elif resolved.is_file() and not resolved.is_symlink():
            data = resolved.read_bytes()
            if re.search(br"(?m)^(?:<<<<<<< |>>>>>>> |\|{7} |=======\r?$)", data):
                raise PortError(f"unresolved conflict marker in {resolved}")
            entry = item["merged"] or item["current"] or item["incoming"] or item["base"]
            if entry is None or entry[1] != "blob":
                raise PortError(f"non-blob conflict needs a separate source fix: {item['path']}")
            replacements.append((item["path"], (entry[0], "blob", port.store("blob", data))))
        else:
            return None, manifest
    port.git.out("read-tree", merged)
    updates = []
    for path, entry in replacements:
        prefix = f"{entry[0]} blob {entry[2]}" if entry else "0 " + "0" * len(merged)
        updates.append(prefix.encode() + b"\t" + path.encode("utf-8", "surrogateescape") + b"\0")
    port.git.out("update-index", "-z", "--index-info", data=b"".join(updates))
    manifest["resolved_files"] = [{"path": p, "entry": e} for p, e in replacements]
    return port.git.out("write-tree").decode().strip(), manifest


def prepare(source, scratch, head, onto, base, name, check, export=None, resolutions=None):
    commits = source.out("rev-list", "--reverse", "--topo-order", base + ".." + head).decode().splitlines()
    receipt = {"source": head, "onto": onto, "base": base, "name": name, "check": check,
               "commits": [], "files": [], "conflicts": [], "old_mentions": [],
               "message_changes": [], "complete": True, "published_locally": False}
    port = Port(source, scratch, base)
    mapped, allfiles, conflicts = {base: onto}, set(), set()
    try:
        for commit in commits:
            fields, message = port.metadata(commit)
            parents = [p.decode() for p in fields.get(b"parent", [])]
            if not parents:
                raise PortError("root replay needs an ancestor --base")
            parent = parents[0]
            before, after = port.renamed_tree(parent), port.renamed_tree(commit)
            paths = scratch.out("diff-tree", "--no-commit-id", "-r", "--name-only", "-z", before, after).decode("utf-8", "surrogateescape").split("\0")[:-1]
            allfiles.update(paths)
            codec = fields.get(b"encoding", [b"UTF-8"])[0].decode("ascii")
            text = message.decode(codec)
            newmessage, changes = transform_message(text, port.entries(commit))
            row = {"source": commit, "subject": text.splitlines()[0] if text else "", "files": paths,
                   "special_files": sorted(SPECIAL.intersection(paths)), "message_changes": changes}
            if b"gpgsig" in fields or b"gpgsig-sha256" in fields or b"mergetag" in fields:
                row["signature_removed"] = True
            receipt["commits"].append(row)
            receipt["message_changes"].extend(dict(change, commit=commit) for change in changes)
            # Includes intentional exceptions; no hidden allowlist in the receipt.
            mentions = port.old_mentions(before, after)
            row["old_mentions"] = mentions
            receipt["old_mentions"].extend(dict(mention, commit=commit) for mention in mentions)
            if not receipt["complete"]:
                row["status"] = "not simulated after earlier conflict"
                continue
            current = mapped.get(parent, onto)
            target = port.commit(after, [], fields, newmessage.encode(codec))
            merge = scratch.run("merge-tree", "--write-tree", "--name-only", "-z", "--messages",
                                "--merge-base=" + before, current, target, allowed=(0, 1))
            parts = merge.stdout.split(b"\0")
            tree = parts[0].decode().strip()
            if merge.returncode:
                names = []
                for item in parts[1:]:
                    if not item:
                        break
                    names.append(item.decode("utf-8", "surrogateescape"))
                resolved, proof = resolve_conflict(port, commit, before, current, after, tree, names,
                                                   export, resolutions)
                if resolved is None:
                    conflicts.update(names)
                    row.update(status="conflict", conflicts=names, conflict_manifest=proof,
                               merge_output=merge.stdout.decode("utf-8", "replace"))
                    receipt["complete"] = False
                    continue
                tree = resolved
                row.update(status="hand-resolved", resolved_conflicts=names, resolution=proof)
            mapped_parents = [mapped.get(p, onto) for p in parents]
            newcommit = port.commit(tree, mapped_parents, fields, newmessage.encode(codec))
            mapped[commit] = newcommit
            row.update(status=row.get("status", "clean"), ported=newcommit, tree=tree,
                       transformed_parent=before, transformed_tree=after)
        receipt["files"] = sorted(allfiles)
        receipt["conflicts"] = sorted(conflicts)
        receipt["commit_count"] = len(commits)
        receipt["file_count"] = len(allfiles)
        if receipt["complete"]:
            receipt["head"] = mapped.get(head, onto)
            receipt["final_old_mentions"] = port.old_mentions(onto, receipt["head"])
        return receipt
    finally:
        port.close()


def run(args):
    if args.check and args.conflict_dir:
        raise PortError("--check cannot export files; omit --conflict-dir")
    source = Git(Path.cwd())
    head, onto = source.oid(args.branch), source.oid(args.onto)
    if args.base:
        base = source.oid(args.base)
        source.run("merge-base", "--is-ancestor", base, head)
    else:
        bases = source.out("merge-base", "--all", head, source.oid("origin/main")).decode().splitlines()
        if len(bases) != 1:
            raise PortError("no unique merge-base with origin/main; supply --base")
        base = bases[0]
    if not args.name.startswith("longshot/"):
        raise PortError("--name must start with longshot/")
    source.out("check-ref-format", "refs/heads/" + args.name)
    exists = source.run("show-ref", "--verify", "--quiet", "refs/heads/" + args.name, allowed=(0, 1))
    if exists.returncode == 0 and not args.check:
        raise PortError("destination already exists; choose a NEW --name (never overwrite a worktree branch)")
    objects = source.out("rev-parse", "--path-format=absolute", "--git-path", "objects").decode().strip()
    fmt = source.out("rev-parse", "--show-object-format").decode().strip()
    with tempfile.TemporaryDirectory(prefix="ls-port-") as directory:
        env = environment()
        env["GIT_ALTERNATE_OBJECT_DIRECTORIES"] = objects
        scratch = Git(directory, env)
        scratch.out("init", "--bare", "--quiet", "--object-format=" + fmt)
        receipt = prepare(source, scratch, head, onto, base, args.name, args.check,
                          args.conflict_dir, args.resolutions)
        if receipt["complete"] and not args.check:
            result = receipt["head"]
            if result != onto:
                # Export only objects reachable from the finished result, not synthetic bases.
                pack = scratch.out("pack-objects", "--stdout", "--revs",
                                   data=(result + "\n^" + onto + "\n").encode())
                source.out("index-pack", "--stdin", data=pack)
            source.out("update-ref", "-m", "ls-port local preparation", "refs/heads/" + args.name,
                       result, "0" * len(result))
            receipt["published_locally"] = True
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("branch", help="source branch or immutable commit")
    parser.add_argument("--onto", default="forge/main")
    parser.add_argument("--name", required=True, help="NEW local branch, longshot/<name>")
    parser.add_argument("--base", help="explicit ancestor instead of merge-base with origin/main")
    parser.add_argument("--check", action="store_true", help="simulate in disposable storage; do not write to the source repository")
    parser.add_argument("--conflict-dir", help="export three-way inputs and manifest for hand resolution")
    parser.add_argument("--resolutions", help="read numbered .resolved/.delete files from an earlier conflict export")
    args = parser.parse_args()
    try:
        receipt = run(args)
    except (PortError, OSError, UnicodeError, LookupError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 2
    print(json.dumps(receipt, indent=2, ensure_ascii=True))
    return 0 if receipt["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
