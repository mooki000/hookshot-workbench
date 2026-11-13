#!/usr/bin/env python3
"""Preview the deferred LS host cutover. Only --apply permits any writes.

Stop workers before applying. Both admission locks must have no holders or waiters.
Game repositories, player folders, saves, runtime names, worktree parent, coordination
lock names, credentials and private MCP registrations are deliberately not renamed.
"""
from contextlib import ExitStack, contextmanager
from pathlib import Path
import argparse
import fcntl
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish
import name_policy

# Existing physical paths: keep this migration table even after the migration.
HOST_NAMES = (('Games/.hookshot-harness', 'Games/.ls-harness'),
              ('Code/hookshot-workbench', 'Code/ls-workbench'))
LOCKS = ('/tmp/longshot-quiet-window.lock', '/tmp/longshot-measuring.lock')
SKIP = {'.git', '.private', '__pycache__', '.uv-cache', '.venv', '.pytest_cache'}


def lock_users(paths, proc_locks=Path('/proc/locks'), exclude_pid=None):
    identities = {}
    for path in paths:
        path = Path(path)
        if path.exists():
            stat = path.stat()
            identities[stat.st_ino] = str(path)
    users = []
    for line in proc_locks.read_text().splitlines():
        fields = line.split()
        for index, field in enumerate(fields):
            match = re.fullmatch(r'([0-9a-fA-F]+):([0-9a-fA-F]+):([0-9]+)', field)
            if not match:
                continue
            # Conservatively refuse a matching inode on any device: subvolume device
            # aliases must not hide holders or waiters. A false refusal is safe.
            key = int(match[3])
            if key in identities and (exclude_pid is None or fields[index - 1] != str(exclude_pid)):
                users.append(identities[key] + ': ' + line.strip())
    return users


@contextmanager
def idle_locks(paths=LOCKS):
    busy = lock_users(paths)
    if busy:
        raise ValueError('admission lock has holders or waiters:\n' + '\n'.join(busy))
    with ExitStack() as stack:
        for path in paths:
            # Existing admission locks are retained: replacing them would split the gates.
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            stack.callback(os.close, descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('admission lock became busy: ' + str(path)) from None
        busy = lock_users(paths, exclude_pid=os.getpid())
        if busy:
            raise ValueError('admission lock has waiters:\n' + '\n'.join(busy))
        yield


def renames(home):
    home = Path(home)
    result = [(home / '.agents/skills' / old, home / '.agents/skills' / new)
              for old, new in publish.SKILL_MAP.items()]
    harness = home / HOST_NAMES[0][0]
    for old, new in publish.FILE_MAP.items():
        folders = ('regress', 'nightly') if old.startswith('test_') and old.endswith('_names.py') else ('',)
        result.extend((harness / folder / old, harness / folder / new) for folder in folders)
    result.extend((home / old, home / new) for old, new in HOST_NAMES)
    # Child files move before their containing harness; all destinations are exclusive.
    return sorted(result, key=lambda pair: (-len(pair[0].parts), str(pair[0])))


def relocated(path, moves):
    for old, new in moves:
        if path == old or path.is_relative_to(old):
            path = new / path.relative_to(old)
    return path


def path_text(text, home):
    for old, new in HOST_NAMES:
        text = text.replace(old, new)
    for old, new in publish.SKILL_MAP.items():
        text = text.replace('.agents/skills/' + old, '.agents/skills/' + new)
    return text


def candidate_files(home):
    home = Path(home)
    harness = home / HOST_NAMES[0][0]
    for name in publish.HARNESS_MAP:
        yield harness / name
    for source, (_, names) in publish.GROUPS.items():
        yield from (harness / source / name for name in names)
    # Installed gate scripts must follow the moved harness but keep their gate names.
    for kind in ('gpu', 'intel', 'build'):
        yield home / 'Games' / (publish.OLD_GATE + '-' + kind + '-slot')
    yield home / 'Games' / ('.' + publish.OLD_GATE + '-harness/gpu-guard.sh')
    yield home / 'Code/hookshot-wt/reports/AGENT-BRIEF.md'
    for live in publish.SKILL_MAP:
        root = home / '.agents/skills' / live
        yield from (path for path in root.rglob('*') if path.suffix in publish.SKILL_TYPES)
    checkout = home / HOST_NAMES[1][0]
    if checkout.is_dir():
        result = subprocess.run(['git', '-C', str(checkout), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                text=True, capture_output=True, check=True)
        for name in result.stdout.split('\0'):
            if name and name not in {'cutover.py', 'tests/test_cutover.py'}:
                yield checkout / name


def rewrite_content(path, text, home):
    checkout = Path(home) / HOST_NAMES[1][0]
    changed = path_text(text, home)
    if not path.is_relative_to(checkout):
        # Do not run the publication-only shared-gate normalization on live tools.
        changed = publish.identity_text(changed, path.name)
    elif path.name == 'publish.py':
        changed = changed.replace("LIVE_LAYOUT = 'legacy'", "LIVE_LAYOUT = 'published'")
    return changed


def plan(home):
    moves = renames(home)
    problems = []
    for old, new in moves:
        if not old.exists() or old.is_symlink():
            problems.append('missing or linked source: ' + str(old))
        if new.exists() or new.is_symlink():
            problems.append('destination already exists: ' + str(new))
    rewrites = []
    checkout = Path(home) / HOST_NAMES[1][0]
    transforms = {}
    for path in sorted(set(candidate_files(home))):
        if not path.exists():
            continue
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            problems.append('linked rewrite source: ' + str(path))
            continue
        if not path.is_file() or set(path.relative_to(home).parts) & SKIP:
            continue
        if path == checkout / name_policy.MANIFEST:
            continue
        try:
            original = path.read_bytes()
            text = original.decode('utf-8')
        except UnicodeDecodeError:
            continue
        changed = rewrite_content(path, text, home)
        if changed != text:
            rewrites.append((path, relocated(path, moves), original, changed.encode('utf-8')))
            if path.is_relative_to(checkout):
                relative = path.relative_to(checkout).as_posix()
                transforms[relative] = (relative, lambda value, source=path: rewrite_content(source, value, home))
    manifest = checkout / name_policy.MANIFEST
    if manifest.is_file():
        original = manifest.read_bytes()
        changed = name_policy.rewrite_manifest(original.decode('utf-8'), transforms).encode('utf-8')
        if changed != original:
            rewrites.append((manifest, relocated(manifest, moves), original, changed))
    return moves, rewrites, problems


def print_plan(moves, rewrites, problems, busy):
    print('DRY RUN: no files, locks, processes or Git metadata are changed')
    for old, new in moves:
        print(f'RENAME {old} -> {new}')
    for old, new, _, _ in rewrites:
        print(f'REWRITE {old}' + (f' -> {new}' if old != new else ''))
    checkout = next(new for old, new in moves if old.parts[-2:] == Path(HOST_NAMES[1][0]).parts)
    print(f'GIT git -C {checkout} worktree repair')
    for problem in problems:
        print('REFUSE APPLY: ' + problem)
    for user in busy:
        print('REFUSE APPLY: lock holder/waiter: ' + user)
    print(f'TOTAL {len(moves)} renames; {len(rewrites)} file rewrites')


def replace_file(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix='.ls-cutover-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(content)
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply(home, moves, rewrites, problems, locks=LOCKS):
    if problems:
        raise ValueError('\n'.join(problems))
    checkout = Path(home) / HOST_NAMES[1][0]
    if not (checkout / 'docs/names-kept.md').is_file() or not (checkout / 'skills/ls-workbench/SKILL.md').is_file():
        raise ValueError('integrate the LS publication into the main checkout before host cutover')
    with idle_locks(locks):
        # Replan under both locks: a dry-run receipt never authorizes a stale write.
        current = plan(home)
        if current != (moves, rewrites, problems):
            raise ValueError('cutover inputs changed; inspect a fresh dry run')
        moved, written = [], []
        try:
            for old, new in moves:
                if lock_users(locks, exclude_pid=os.getpid()):
                    raise ValueError('a new gate waiter arrived during cutover')
                old.rename(new)
                moved.append((old, new))
            for old, new, original, changed in rewrites:
                if lock_users(locks, exclude_pid=os.getpid()):
                    raise ValueError('a new gate waiter arrived during cutover')
                if new.read_bytes() != original:
                    raise ValueError('rewrite input changed: ' + str(new))
                replace_file(new, changed)
                written.append((new, original))
            subprocess.run(['git', '-C', str(relocated(checkout, moves)), 'worktree', 'repair'], check=True)
        except BaseException:
            for path, original in reversed(written):
                replace_file(path, original)
            for old, new in reversed(moved):
                new.rename(old)
            subprocess.run(['git', '-C', str(checkout), 'worktree', 'repair'], check=True)
            raise
    print('APPLIED: host names moved and Git worktrees repaired; no services restarted')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--dry-run', action='store_true', help='print every operation (the default)')
    action.add_argument('--apply', action='store_true', help='perform the reviewed cutover only after all workers stop')
    args = parser.parse_args(argv)
    home = Path.home()
    moves, rewrites, problems = plan(home)
    try:
        busy = lock_users(LOCKS)
    except OSError as error:
        busy = ['lock inspection unavailable: ' + str(error)]
    print_plan(moves, rewrites, problems, busy)
    if args.apply:
        if busy:
            raise ValueError('admission locks are occupied; stop holders and waiters before cutover')
        apply(home, moves, rewrites, problems)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit('cutover refused: ' + str(error))
