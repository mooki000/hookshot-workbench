#!/usr/bin/env python3
"""Publish only reviewed tools; rewrite public identities, never live source paths."""
from pathlib import Path
import argparse
import ast
import io
import os
import re
import shutil
import sys
import tokenize

sys.path.insert(0, str(Path(__file__).resolve().parent))
import name_policy

# Physical installed names remain unchanged until the separately reviewed cutover.
SKILL_MAP = {
    'hookshot-workbench': 'ls-workbench',
    'hookshot-pr-evidence': 'ls-pr-evidence',
    'hookshot-perf-compare': 'ls-perf-compare',
    'hookshot-scripted-play': 'ls-scripted-play',
    'hookshot-issue-triage': 'ls-issue-triage',
    'hookshot-discord-updates': 'ls-discord-updates',
    'hookshot-regression-hunt': 'ls-regression-hunt',
    'hookshot-nightly-playtest': 'ls-nightly-playtest',
    'hookshot-pr-loop': 'ls-pr-loop',
    'hookshot-perf-monitor': 'ls-perf-monitor',
    'ls-function-map': 'ls-function-map',
    'ls-skill-upkeep': 'ls-skill-upkeep',
}
LIVE_LAYOUT = 'legacy'  # cutover.py changes only the installed source layout
HARNESS_MAP = {name: Path(name).name for name in (
    'hs-run.sh', 'hs-idle.sh', 'hs-nocore.sh', 'hs-pr-check.sh', 'hs-save.sh',
    'hs-watch-ends2.sh', 'hs-watch-threads.py', 'hs-window.py', 'tools/deps-local.sh',
    'hs-names.sh', 'hs_names.py', 'test_hs_names.py',
)}
HARNESS_MAP.update({'longshot-port.py': 'ls-port.py', 'test_longshot_port.py': 'test_ls_port.py'})
GROUPS = {
    'playthrough': ('play', ('hsap.py', 'hsap_file.py', 'tapcheck.py', 'test_hsap.py', 'test_hsap_file.py')),
    'regress': ('regress', ('regress.py', 'build_case.py', 'run_case.py', 'test_regress.py', 'test_longshot_names.py')),
    'perf': ('perf', ('hs_perf.py', 'test_hs_perf.py')),
    'nightly': ('nightly', ('hs-nightly.py', 'build.py', 'common.py', 'scenario.py', 'monitor.py',
                          'metrics.py', 'compare.py', 'store.py', 'report.py', 'hunt.py', 'rolling.py',
                          'test_compare.py', 'test_driver.py', 'test_rolling.py', 'test_longshot_names.py', 'README.md')),
}
FILE_MAP = {'longshot-port.py': 'ls-port.py', 'test_longshot_port.py': 'test_ls_port.py',
            'test_longshot_names.py': 'test_runtime_names.py'}
LIVE = {'harness': 'Games/.hookshot-harness', 'skills': '.agents/skills', 'gates': 'Games',
        'brief': 'Code/hookshot-wt/reports/AGENT-BRIEF.md'}
# The shared gates predate this workbench; this is the existing publication normalization.
OLD_GATE = 'nx2' + 'navi'
SKILL_TYPES = {'.md', '.py', '.sh', '.mjs'}


def installed(name):
    return SKILL_MAP.get(name, FILE_MAP.get(name, name)) if LIVE_LAYOUT == 'published' else name



def sources():
    stage = os.environ.get('LS_WB_SOURCE_ROOT')
    if stage:
        root = Path(stage).expanduser().absolute()
        return root / 'harness', root / 'skills', root / 'gates', root / 'gates/gpu-guard.sh', root / 'docs/agent-brief.md'
    home = Path.home()
    def configured(key):
        return Path(os.environ.get('LS_WB_' + key.upper(), home / LIVE[key])).expanduser().absolute()
    harness, skills, gates, brief = (configured(key) for key in ('harness', 'skills', 'gates', 'brief'))
    guard = Path(os.environ.get('LS_WB_GUARD', gates / ('.' + OLD_GATE + '-harness/gpu-guard.sh')))
    return harness, skills, gates, guard, brief


def files():
    harness, skills, gates, guard, brief = sources()
    pairs = [(harness / installed(before), Path('harness') / after) for before, after in HARNESS_MAP.items()]
    for source, (destination, names) in GROUPS.items():
        pairs.extend((harness / source / installed(name), Path(destination) / FILE_MAP.get(name, name)) for name in names)
    for kind in ('gpu', 'intel', 'build'):
        public = 'hs-' + kind + '-slot'
        source = gates / public
        if not source.is_file():
            source = gates / (OLD_GATE + '-' + kind + '-slot')
        pairs.append((source, Path('gates') / public))
    pairs.extend(((guard, Path('gates/gpu-guard.sh')), (brief, Path('docs/agent-brief.md'))))
    for live, public in SKILL_MAP.items():
        skill = skills / installed(live)
        if skill.is_symlink() or not (skill / 'SKILL.md').is_file():
            raise ValueError('missing or linked live skill: ' + str(skill))
        for source in sorted(skill.rglob('*')):
            if '__pycache__' in source.parts:
                continue
            if source.is_symlink():
                raise ValueError('symlink in skill source: ' + str(source))
            if not source.is_file():
                continue
            if source.suffix not in SKILL_TYPES:
                raise ValueError('unreviewed skill file type: ' + str(source))
            pairs.append((source, Path('skills') / public / source.relative_to(skill)))
    return pairs


def skill_references(text):
    """Map logical names and front matter, preserving still-installed absolute paths."""
    for live, public in SKILL_MAP.items():
        text = re.sub(r'skill://' + re.escape(live) + r'(?![\w-])', 'skill://' + public, text)
        # A preceding slash is a physical path or external repository, not a logical ID.
        text = re.sub(r'(?<![\w/.-])' + re.escape(live) + r'(?![\w-])', public, text)
        text = re.sub(r'mooki000/' + re.escape(live) + r'(?![\w-])', 'mooki000/' + public, text)
    # Relative published paths are mapped even when the same document has live paths.
    return re.sub(r'(?<![\w/.])skills/(' + '|'.join(map(re.escape, SKILL_MAP)) + r')(?![\w-])',
                  lambda match: 'skills/' + SKILL_MAP[match[1]], text)


def historical_normalization(text):
    """The former private publication step, without renaming any current host path."""
    old, title = OLD_GATE, 'NX2' + 'navi'
    prose = (
        (rf', {title}\. The user ended that\.', '. The user ended that.'),
        (rf'Its repository\n  \(`/home/mooki/Code/{title}`\), worktrees, reports \(`/home/mooki/Code/{old}-wt/reports/`\) and harness\n  \(`/home/mooki/Games/\.{old}-harness/`\) remain', 'Its repository,\n  worktrees, reports and harness remain'),
        (rf'\*\* {title}\x27s repository', "** The other port's repository"),
        (rf'\({title}\x27s \.{old}-\*,\n#    Hookshot\x27s \.hookshot-h-\*\)', "(the game's\n#    .hookshot-h-*)"),
        (rf'\({title}\x27s, Hookshot\x27s', "(the game's"),
        (rf'^[ \t]*/home/mooki/Games/\.{old}-\*\|/home/mooki/Code/{old}-wt/\*\|/home/mooki/Code/{title}/\*\) return 0 ;;\n', ''),
        (rf'the {title} autopilot\x27s', "the earlier autopilot's"),
        (rf'Hookshot = {title} - ', 'HS = the other port - '),
        (rf'in the {old} repo as', "in the [other port's] repo as"),
        (r'on `tangerine' + rf'turtle1/{title}` \(closed now, still readable:\n`gh pr view <n> --repo tangerine' + rf'turtle1/{title}`\): ', "on the other port's repository (closed now):\n"),
        (rf' \(`/home/mooki/Code/{title}` and its `{old}-\*` skills are reference\n  only\)', ' (its local checkout and skills are reference\n  only)'),
        (rf'\n\(The `{old}-` in the names is history[^\n]*\)\n', '\n'),
        (rf'{title}\x27s', "the other port's"),
    )
    for pattern, replacement in prose:
        text = re.sub(pattern, replacement, text, flags=re.M)
    text = text.replace('Code/' + old + '-wt/.venv-kit/bin/python', 'Code/hookshot-wt/deps/python/bin/python3')
    text = text.replace('.' + old + '-harness', Path(LIVE['harness']).name)
    text = re.sub(old + r'-(gpu|intel|build)-slot', r'hs-\1-slot', text)
    return text.replace(old + '-*-slot', 'hs-*-slot').replace(old.upper() + '_', 'HSGATE_')


def prose_names(text):
    # Literal examples, external URLs and filenames are not prose branding.
    pieces = re.split(r'(```[\s\S]*?```|`[^`\n]*`|https?://\S+)', text)
    for index in range(0, len(pieces), 2):
        pieces[index] = re.sub(r'(?<![\w/._-])Hookshot(?![\w/_-]|\.[A-Za-z0-9])', 'HS', pieces[index])
        pieces[index] = re.sub(r'(?<![\w/._-])Longshot(?![\w/_-]|\.[A-Za-z0-9])', 'LS', pieces[index])
    return ''.join(pieces)


def source_prose(text, destination):
    if Path(destination).suffix != '.py':
        return ''.join(prose_names(line) if line.lstrip().startswith(('#', '//')) else line
                       for line in text.splitlines(keepends=True))
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(first.value.lineno)
    tokens = []
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT or (token.type == tokenize.STRING and token.start[0] in docstrings):
            token = token._replace(string=prose_names(token.string))
        tokens.append(token)
    return tokenize.untokenize(tokens)


def local_toolchain_namespace(text, destination):
    # Only this workbench-owned helper and its renamed contract change semantics.
    # Reuse the reviewed canonical definitions, not a second implementation.
    functions = {
        'hs_names.py': ('environment_value', 'environment_value'),
        'test_hs_names.py': ('test_local_toolchain_input_accepts_either_but_not_both',
                             'test_local_toolchain_input_uses_the_workbench_namespace'),
    }
    name = Path(destination).name
    if name not in functions or ('def ' + functions[name][0] + '(') not in text:
        return text
    old_name, new_name = functions[name]
    tree = ast.parse(text)
    canonical = (Path(__file__).resolve().parent / 'harness' / name).read_text()
    definition = next(node for node in ast.walk(ast.parse(canonical))
                      if isinstance(node, ast.FunctionDef) and node.name == new_name)
    replacement = canonical.splitlines(keepends=True)[definition.lineno - 1:definition.end_lineno]
    lines = text.splitlines(keepends=True)
    for node in reversed(list(ast.walk(tree))):
        if isinstance(node, ast.FunctionDef) and node.name == old_name:
            lines[node.lineno - 1:node.end_lineno] = replacement
    return ''.join(lines)


def identity_text(text, destination=''):
    text = skill_references(text)
    for before, after in FILE_MAP.items():
        text = text.replace(before, after)
    for before, after in (('HOOKSHOT_WB_', 'LS_WB_'), ('LONGSHOT_WB_', 'LS_WB_'),
                          ('hookshot-wb', 'ls-wb'), ('longshot-wb', 'ls-wb'),
                          ('longshot-port', 'ls-port'), ('longshot_port', 'ls_port'),
                          ('hookshot-discord-test-', 'ls-discord-test-'),
                          ('HOOKSHOT_DISCORD_CONFIG', 'LS_DISCORD_CONFIG'),
                          ('HOOKSHOT_MINGW', 'LS_MINGW'), ('LONGSHOT_MINGW', 'LS_MINGW'),
                          ('hookshot_system_telemetry', 'ls_system_telemetry'),
                          ('hookshot-nightly-', 'ls-nightly-'),
                          ('Hookshot #2292', 'HS #2292')):
        text = text.replace(before, after)
    # The window class is ours; the hidden live harness directory is not renamed here.
    text = re.sub(r'(?<![\w.])hookshot-harness(?![\w])', 'ls-harness', text)
    if str(destination).endswith('.md'):
        text = re.sub(r'^(#+ )Hookshot\b', r'\1LS', text, flags=re.M)
        return prose_names(text)
    text = local_toolchain_namespace(text, destination)
    return source_prose(text, destination) if destination else text


def normalise(text, destination=''):
    return identity_text(historical_normalization(text), destination)


def safe_target(root, relative):
    target = root / relative
    if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent != root.parent):
        raise ValueError('linked publication destination: ' + str(target))
    return target


def publish(root, check=False):
    prepared = []
    for source, relative in files():
        if not source.is_file() or source.is_symlink():
            raise ValueError('missing or linked allowlisted source: ' + str(source))
        safe_target(root, relative)
        prepared.append((relative, normalise(source.read_text(encoding='utf-8'), relative).encode(), source.stat().st_mode & 0o777))
    expected = {relative for relative, _, _ in prepared}
    obsolete = []
    for live, public in SKILL_MAP.items():
        legacy = safe_target(root, Path('skills') / live)
        if live != public and legacy.exists():
            obsolete.append(legacy)
        current = safe_target(root, Path('skills') / public)
        for path in current.rglob('*') if current.exists() else ():
            safe_target(root, path.relative_to(root))
            if path.is_file() and path.relative_to(root) not in expected:
                obsolete.append(path)
    changed = []
    for relative, content, mode in prepared:
        target = root / relative
        if target.is_file() and target.read_bytes() == content and target.stat().st_mode & 0o777 == mode:
            continue
        changed.append(str(relative))
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.publish-new')
            temporary.write_bytes(content)
            temporary.chmod(mode)
            temporary.replace(target)
    for path in obsolete:
        changed.append(str(path.relative_to(root)))
        if not check:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    print(('source check: ' if check else 'published: ') + str(len(changed)) + ' changed paths')
    if check and changed:
        print('\n'.join(changed), file=sys.stderr)
    return 1 if check and changed else 0


def relocate(root, home):
    if not home.startswith('/') or '\n' in home or '\r' in home or not home.rstrip('/'):
        raise ValueError('an absolute single-line home path other than / is required')
    skip = {'.git', '.private', '__pycache__', '.uv-cache', '.venv', '.pytest_cache'}
    transforms = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if not path.is_file() or path.is_symlink() or set(relative.parts) & skip:
            continue
        if relative.name in {'publish.py', 'relocate.sh', 'sync-from-live.sh'} or relative.as_posix() == name_policy.MANIFEST:
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        changed = text.replace('/home/mooki', home.rstrip('/'))
        if changed != text:
            path.write_text(changed, encoding='utf-8')
            print('rewrote ' + str(relative))
            transforms[relative.as_posix()] = (relative.as_posix(), lambda value: value.replace('/home/mooki', home.rstrip('/')))
    manifest = root / name_policy.MANIFEST
    if manifest.is_file():
        text = manifest.read_text()
        changed = name_policy.rewrite_manifest(text, transforms)
        if changed != text:
            manifest.write_text(changed)
            print('rewrote ' + name_policy.MANIFEST)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='compare sources without writing')
    parser.add_argument('--relocate-home', help='rewrite the example home inside this checkout only')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.relocate_home:
        relocate(root, args.relocate_home)
        return 0
    return publish(root, args.check)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, UnicodeError, ValueError) as error:
        sys.exit('publish: ' + str(error))
