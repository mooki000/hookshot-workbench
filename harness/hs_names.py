"""Names read from an owned Hookshot/Longshot copy or checkout, never its folder name.

No game is launched, no environment is changed, and an ambiguous identity is refused.
The shell companion invokes this same implementation to avoid a second mapping.
"""
import ast
from dataclasses import dataclass
from pathlib import Path
import re

PROJECTS = ('hookshot', 'longshot')
GAME_EXECUTABLES = tuple(p + suffix for p in PROJECTS for suffix in ('', '.exe'))
PERF_HEADERS = tuple('# ' + p + '_perf' for p in PROJECTS)
LOG_VERSION = re.compile(r'(?:hookshot|longshot) (v\S+|nightly\S+)[^\n]*')
ALL_SETTINGS = tuple(p + suffix for p in PROJECTS for suffix in ('_options.ini', '_env.txt'))


class NameError(ValueError):
    """Missing or mixed project markers; the caller must not choose a default."""


@dataclass(frozen=True)
class Names:
    project: str

    def __post_init__(self):
        if self.project not in PROJECTS:
            raise NameError('unknown project: ' + self.project)

    @property
    def executable(self):
        return self.project

    @property
    def renderer(self):
        return self.project + '_vk.so'

    @property
    def log(self):
        return self.project + '_log.txt'

    @property
    def rotated_log(self):
        return self.project + '_log.1.txt'

    @property
    def options(self):
        return self.project + '_options.ini'

    @property
    def env_file(self):
        return self.project + '_env.txt'

    @property
    def env_header(self):
        return self.project + '_env.h'

    @property
    def profile(self):
        return self.project + '_pieces.prof'

    @property
    def control_client(self):
        return 'scripts/' + self.project + '_ctl.py'

    @property
    def control_socket_prefix(self):
        return self.project + '-control-'

    @property
    def cache_files(self):
        # Preserve the rolling runner's cache selection, not a new warming policy.
        return (self.project + '_programs.bin', self.project + '_programs.idx',
                self.project + '_pipelines.bin', 'mkw_vk_pipelines.bin',
                self.project + '_computes.bin', self.project + '_pipeline_states.bin',
                self.project + '_prepared.bin', '.nv-cache', '.xdg-cache', '.mesa-cache')

    def env_key(self, suffix):
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*', suffix):
            raise NameError('invalid environment suffix')
        return self.project.upper() + '_' + suffix

    def project_file(self, filename):
        """Map only renamed build inputs; arbitrary paths are not rewritten."""
        for project in PROJECTS:
            for suffix in ('_pieces.prof', '.ico', '.res'):
                if filename == project + suffix:
                    return self.project + suffix
        return filename


def _select(root, candidates, kind):
    found = [project for project, paths in candidates.items() if any(p.is_file() for p in paths)]
    if len(found) != 1:
        raise NameError(('mixed' if found else 'unknown') + ' ' + kind + ' project in ' + str(root))
    return Names(found[0])


def copy_names(root):
    root = Path(root)
    names = _select(root, {p: (root / p, root / (p + '.exe')) for p in PROJECTS}, 'copy')
    other = next(p for p in PROJECTS if p != names.project)
    if any(p.is_file() and p.name.startswith(other + '_') for p in root.iterdir()):
        raise NameError('mixed copy artifacts in ' + str(root))
    return names


def checkout_names(root):
    root = Path(root)
    clients = {p: root / 'scripts' / (p + '_ctl.py') for p in PROJECTS}
    found = {p for p, path in clients.items() if path.is_file()}
    # Older regression heads predate the client. Their build's explicit output
    # declaration is evidence too; a directory name or absence is never a default.
    declared = set()
    build = root / 'nx2/build.py'
    if build.is_file():
        tree = ast.parse(build.read_text(encoding='utf-8'), filename=str(build))
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ('EXE', 'VK_LIB') for t in node.targets):
                for item in ast.walk(node.value):
                    if isinstance(item, ast.Constant) and isinstance(item.value, str):
                        declared.update(p for p in PROJECTS if item.value in (p, p + '.exe', p + '_vk.so', p + '_vk.dll'))
    identities = found | declared
    if len(identities) != 1:
        raise NameError(('mixed' if identities else 'unknown') + ' checkout project in ' + str(root))
    return Names(next(iter(identities)))


def environment_header(checkout):
    folder = Path(checkout) / 'gpu/vk/compat'
    names = _select(folder, {p: (folder / Names(p).env_header,) for p in PROJECTS}, 'environment header')
    return folder / names.env_header


def log_path(run, required=False):
    """Select an existing run log, or None while a run is still queued."""
    run = Path(run)
    found = [run / Names(p).log for p in PROJECTS if (run / Names(p).log).is_file()]
    if len(found) > 1:
        raise NameError('mixed run logs in ' + str(run))
    if not found and required:
        raise NameError('unknown run log in ' + str(run))
    return found[0] if found else None


def build_env(checkout, suffix_values):
    """Select env assignments only when the checkout's Python source reads the keys.

    Inspect literal environment reads, not comments, git remotes, or inherited values.
    This covers the build adapter's LIFT_JOBS, KEEP_BUILD_FILES, NGX_SDK and VERSION.
    """
    root = Path(checkout)
    names = checkout_names(root)
    keys = set()
    for folder in ('nx2', 'scripts'):
        for path in (root / folder).rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            for node in ast.walk(tree):
                value = None
                if isinstance(node, ast.Call) and node.args:
                    reader = ast.unparse(node.func)
                    if reader in ('os.environ.get', 'os.getenv', 'environ.get', 'env_on', 'env_int'):
                        value = node.args[0]
                elif (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
                      and ast.unparse(node.value) in ('os.environ', 'environ')):
                    value = node.slice
                elif (isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Subscript)
                      and ast.unparse(node.target.value) in ('os.environ', 'environ')):
                    # An augmented assignment reads its target despite its Store context.
                    value = node.target.slice
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    if re.fullmatch(r'(?:HOOKSHOT|LONGSHOT)_[A-Z0-9_]+', value.value):
                        keys.add(value.value)
    result = {}
    for suffix, value in suffix_values.items():
        key = names.env_key(suffix)
        other = Names(PROJECTS[1 - PROJECTS.index(names.project)]).env_key(suffix)
        if key not in keys or other in keys:
            raise NameError('missing or mixed checkout environment read: ' + key)
        result[key] = str(value)
    return result


def environment_value(environ, suffix):
    """A local helper may accept either spelling of an input, never competing ones.

    This selects a value, not a variable to pass to a build; use build_env for that.
    """
    present = [(Names(p).env_key(suffix), environ.get(Names(p).env_key(suffix))) for p in PROJECTS]
    present = [(key, value) for key, value in present if value]
    if len(present) > 1:
        raise NameError('mixed environment inputs: ' + ', '.join(key for key, _ in present))
    return present[0][1] if present else None


def protected_user_paths(home):
    """Both projects' user storage; callers must never overwrite either family."""
    home = Path(home)
    return tuple(home / folder / project for project in PROJECTS
                 for folder in ('.local/share', '.config')) + tuple(home / 'Games' / p.title() for p in PROJECTS)


def main(argv=None):
    import argparse
    import shlex
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=('copy', 'checkout', 'protected'))
    parser.add_argument('root')
    parser.add_argument('assignments', nargs='*', help='checkout-only build suffix=value assignments')
    args = parser.parse_args(argv)
    try:
        if args.kind == 'protected':
            print('\n'.join(map(str, protected_user_paths(args.root))))
            return 0
        names = (copy_names if args.kind == 'copy' else checkout_names)(args.root)
        if args.assignments:
            if args.kind != 'checkout' or any('=' not in item for item in args.assignments):
                parser.error('build assignments require checkout and SUFFIX=value')
            values = build_env(args.root, dict(item.split('=', 1) for item in args.assignments))
            for key, value in values.items():
                print('export ' + key + '=' + shlex.quote(value))
        else:
            values = {'HS_PROJECT': names.project, 'HS_EXE': names.executable,
                      'HS_RENDERER': names.renderer, 'HS_LOG': names.log,
                      'HS_LOG_PREVIOUS': names.rotated_log, 'HS_OPTIONS': names.options,
                      'HS_ENV_FILE': names.env_file, 'HS_PROFILE': names.profile,
                      'HS_CTL': str(Path(args.root) / names.control_client),
                      'HS_ENV_PREFIX': names.project.upper(),
                      'HS_CONTROL_PREFIX': names.control_socket_prefix}
            for key, value in values.items():
                print(key + '=' + shlex.quote(value))
    except (NameError, OSError, SyntaxError) as error:
        parser.exit(2, 'hs-names: ' + str(error) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
