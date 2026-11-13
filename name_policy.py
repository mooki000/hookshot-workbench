#!/usr/bin/env python3
"""Check file-scoped, counted external-name exceptions documented in docs/names-kept.md."""
from collections import Counter
from pathlib import Path
import json
import re
import subprocess
import sys

MANIFEST = 'docs/names-kept.md'
OLD = re.compile(r'hookshot|longshot', re.IGNORECASE)
# Whole literal tokens distinguish a host path, URL, environment key or filename
# from a bare project word. Counts prevent even an already allowed token creeping back.
TOKEN = re.compile(r'[\w./:~$*?%+=\\-]*(?:hookshot|longshot)[\w./:~$*?%+=\\-]*', re.IGNORECASE)
BEGIN, END = '<!-- kept-literals:begin -->', '<!-- kept-literals:end -->'


def literals(text):
    return [match[0] for match in TOKEN.finditer(text)]


def records(text):
    try:
        body = text.split(BEGIN, 1)[1].split(END, 1)[0]
    except IndexError:
        raise ValueError('missing kept-literals markers') from None
    result = []
    for line in body.splitlines():
        if not line.startswith('['):
            continue
        row = json.loads(line)
        if (not isinstance(row, list) or len(row) != 4 or not all(isinstance(row[i], str) and row[i] for i in (0, 1, 3))
                or type(row[2]) is not int or row[2] < 1):
            raise ValueError('expected [file, exact literal, positive count, reason]')
        if row[0] == MANIFEST or Path(row[0]).is_absolute() or '..' in Path(row[0]).parts:
            raise ValueError('exception must name a repository-relative file, not the manifest')
        result.append(row)
    return result


def allowance(text):
    result = Counter()
    for path, literal, count, _ in records(text):
        key = (path, literal)
        if key in result:
            raise ValueError('duplicate kept literal: ' + repr(key))
        result[key] = count
    return result


def repository_files(root):
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            capture_output=True, check=False) if (root / '.git').exists() else None
    if result is not None and result.returncode == 0:
        return sorted({Path(name.decode()) for name in result.stdout.split(b'\0') if name})
    return sorted(path.relative_to(root) for path in root.rglob('*')
                  if path.is_file() and not {'.git', '__pycache__'} & set(path.relative_to(root).parts))


def observed(root, files=None):
    counts = Counter()
    for relative in repository_files(root) if files is None else files:
        relative = Path(relative)
        path = root / relative
        if relative.as_posix() == MANIFEST or (not path.exists() and not path.is_symlink()):
            continue
        text = str(path.readlink()) if path.is_symlink() else path.read_bytes().decode('utf-8', errors='replace')
        for value in literals(relative.as_posix()) + literals(text):
            counts[(relative.as_posix(), value)] += 1
    return counts


def violations(root, manifest=None, files=None):
    allowed = allowance((root / MANIFEST).read_text() if manifest is None else manifest)
    return observed(root, files) - allowed


def rewrite_manifest(text, transforms):
    """Keep exceptions aligned with a home/path migration, without changing untouched files."""
    changed = []
    for path, literal, count, reason in records(text):
        if path in transforms:
            new_path, transform = transforms[path]
            path, literal = new_path, transform(literal)
        for token in literals(literal):
            changed.append([path, token, count, reason])
    # Several old literals can converge under a path rewrite. Retain the total bound.
    combined = {}
    for path, literal, count, reason in changed:
        key = (path, literal)
        if key in combined:
            combined[key][2] += count
        else:
            combined[key] = [path, literal, count, reason]
    body = '\n```jsonl\n' + '\n'.join(json.dumps(row, ensure_ascii=False) for row in sorted(combined.values())) + '\n```\n'
    before, rest = text.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    return before + BEGIN + body + END + after


def main():
    root = Path(__file__).resolve().parent
    extra = violations(root)
    for (path, literal), count in sorted(extra.items()):
        print(f'{path}: unlisted literal {literal!r} ({count} excess occurrence(s))')
    print('FAIL names' if extra else 'PASS names: every retained literal is file-scoped and counted')
    return bool(extra)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        sys.exit('names: ' + str(error))
