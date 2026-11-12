#!/usr/bin/env python3
"""Windows syntax preflight for changed Hookshot C/C++ test fixtures, not a Windows run.

The real Python driver constructs each command. A child selects its Windows branches,
captures compiler calls and stops before executing a fixture. No fake fixture result
is returned. Runtime-dependent/later variants and unsupported drivers are explicit.
Exit 0: captured syntax/contract checks pass; 1: failure; 2: discovery incomplete.
"""
import argparse
import ast
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import types

SUFFIXES = {'.c', '.cc', '.cpp', '.cxx', '.C'}
HERE = Path(__file__).resolve().parent


def command(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], text=True, capture_output=True, **kwargs)


def git(root, *args):
    r = command(['git', '-C', root, *args])
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip()


class WindowsBranches(ast.NodeTransformer):
    """Select platform conditions without changing pathlib/subprocess's host OS."""
    def visit_Attribute(self, node):
        key = ast.unparse(node)
        if key in ('os.name', 'sys.platform'):
            return ast.copy_location(ast.Constant('nt' if key == 'os.name' else 'win32'), node)
        windows_flags = {'subprocess.CREATE_NO_WINDOW': 0x08000000,
                         'subprocess.BELOW_NORMAL_PRIORITY_CLASS': 0x00004000}
        if key in windows_flags:
            return ast.copy_location(ast.Constant(windows_flags[key]), node)
        return self.generic_visit(node)

    def visit_Call(self, node):
        if ast.unparse(node.func) == 'platform.system' and not node.args:
            return ast.copy_location(ast.Constant('Windows'), node)
        return self.generic_visit(node)


class RuntimeBoundary(KeyboardInterrupt):
    pass

def compilation_sources(argv):
    """Honor explicit -x language and skip flag operands, including forced includes."""
    result, language, i = [], None, 1
    operands = {'-o', '-I', '-isystem', '-iquote', '-include', '-imacros', '-D', '-U',
                '-std', '-L', '-l', '-MF', '-MT', '-MQ', '--target', '--sysroot'}
    while i < len(argv):
        arg = str(argv[i])
        if arg == '-x':
            language = str(argv[i + 1])
            i += 2
        elif arg in operands:
            i += 2
        else:
            if not arg.startswith('-') and (Path(arg).suffix in SUFFIXES or language in ('c', 'c++')):
                result.append(Path(arg))
            i += 1
    return result


def driver_arguments(root, args):
    """Reuse the selected revision's runner tool resolution, including header directories."""
    names = set(re.findall(r'\{([^{}]+)\}', ' '.join(args)))
    if not names:
        return args
    spec = importlib.util.spec_from_file_location('fixture_source_runner', root / 'tests/run_all.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    tools = runner.find_tools() | {'cc': 'clang', 'cxx': 'clang++'}
    missing = sorted(name for name in names if not tools.get(name))
    if missing:
        raise RuntimeError('runner prerequisites unavailable: ' + ', '.join(missing) +
                           '; set RX_CPM or the declared HOOKSHOT_*_HEADERS path')
    return [arg.format(**tools) for arg in args]



def compile_args(argv, source, compiler, sysroot):
    """Preserve all compile flags; remove only link/output/other input arguments."""
    kept = []
    takes_value = {'-I', '-isystem', '-iquote', '-include', '-imacros', '-D', '-U', '-x', '-std'}
    i = 1
    while i < len(argv):
        a = str(argv[i])
        if a in ('-o', '-L', '-l', '-MF', '-MT', '-MQ'):
            i += 2
            continue
        if a in takes_value:
            kept.extend([a, str(argv[i + 1])])
            i += 2
            continue
        if a in ('-c', '-S', '-shared', '-MMD', '-MD') or a.startswith(('-Wl,', '-l', '-L')):
            i += 1
            continue
        if not a.startswith('-'):
            i += 1
            continue
        kept.append(a)
        i += 1
    cxx_headers = ['-nostdinc++', '-isystem', str(sysroot / 'include/c++/v1')] if '++' in compiler else []
    return [compiler, '--target=x86_64-w64-mingw32', '--sysroot=' + str(sysroot),
            *cxx_headers, '-isystem', str(sysroot / 'include'), *kept, '-fsyntax-only', str(source)]


def child(root, test, out, sysroot):
    root, test, out, sysroot = map(Path, (root, test, out, sysroot))
    old_cwd, old_argv = Path.cwd(), sys.argv
    os.chdir(root)
    original_main = sys.modules.get('__main__')
    original_run, original_popen = subprocess.run, subprocess.Popen
    original_call, original_output = subprocess.check_call, subprocess.check_output
    original_source_to_code = importlib.machinery.SourceFileLoader.source_to_code

    def windows_module(loader, data, path, *, _optimize=-1):
        if Path(path).resolve().is_relative_to(root / 'tests'):
            tree = ast.fix_missing_locations(WindowsBranches().visit(ast.parse(data, filename=path)))
            return compile(tree, path, 'exec', dont_inherit=True, optimize=_optimize)
        return original_source_to_code(loader, data, path, _optimize=_optimize)

    records = []
    result = {'test': str(test.relative_to(root)), 'records': records, 'boundary': None, 'error': None}

    def intercept(argv, *args, **kwargs):
        if isinstance(argv, (str, bytes)) or kwargs.get('shell'):
            raise RuntimeBoundary('shell command cannot be judged')
        argv = [str(a) for a in argv]
        sources = compilation_sources(argv)
        compiler_name = Path(argv[0]).name
        if not sources or not re.search(r'(?:clang|gcc|g\+\+|c\+\+|cc)(?:[-+.\d]*|\.exe)$', compiler_name):
            raise RuntimeBoundary('Windows execution not tested; stopped before ' + shlex.join(argv))
        cwd = Path(kwargs.get('cwd') or root)
        for source in sources:
            source = source if source.is_absolute() else cwd / source
            if not source.is_file():
                records.append({'source': str(source), 'status': 'UNJUDGED', 'reason': 'generated source missing'})
                continue
            cxx = source.suffix in (SUFFIXES - {'.c'}) or '++' in compiler_name or (
                '-x' in argv and '++' in argv[argv.index('-x') + 1])
            compiler = shutil.which('clang++' if cxx else 'clang')
            target = compile_args(argv, source, compiler, sysroot)
            subprocess.Popen = original_popen
            try:
                r = original_run(target, cwd=cwd, text=True, capture_output=True, timeout=90)
            finally:
                subprocess.Popen = no_popen
            number = len(records) + 1
            saved = out / ('%03d-' % number + source.name)
            shutil.copyfile(source, saved)
            records.append({'source': str(source), 'saved': str(saved), 'original': argv, 'command': target,
                            'status': 'FAIL' if r.returncode else 'PASS', 'returncode': r.returncode,
                            'diagnostics': r.stdout + r.stderr})
        # This is compile capture only. Execution is never replaced with a fake success.
        # Continue collecting other compile calls even if one Windows compile failed.
        return subprocess.CompletedProcess(argv, 0, '' if kwargs.get('text') else b'', '' if kwargs.get('text') else b'')

    def no_popen(argv, *args, **kwargs):
        raise RuntimeBoundary('unsupported direct Popen: ' + str(argv))

    try:
        tree = ast.parse(test.read_text(encoding='utf-8'), filename=str(test))
        declarations = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for name in node.targets:
                    if isinstance(name, ast.Name) and name.id.startswith('RUN_ALL_'):
                        declarations[name.id] = ast.literal_eval(node.value)
        if 'windows' in declarations.get('RUN_ALL_SKIP', {}):
            raise RuntimeBoundary('driver skips Windows: ' + declarations['RUN_ALL_SKIP']['windows'])
        args = declarations.get('RUN_ALL_ARGS', [])
        args = driver_arguments(root, args)
        sys.argv = [str(test), *args]
        subprocess.run, subprocess.Popen = intercept, no_popen
        subprocess.check_call = lambda argv, **kw: intercept(argv, **kw).returncode
        subprocess.check_output = lambda argv, **kw: intercept(argv, **kw).stdout
        importlib.machinery.SourceFileLoader.source_to_code = windows_module
        tree = ast.fix_missing_locations(WindowsBranches().visit(tree))
        module = types.ModuleType('__main__')
        module.__file__ = str(test)
        sys.modules['__main__'] = module
        exec(compile(tree, str(test), 'exec'), module.__dict__)
    except RuntimeBoundary as exc:
        result['boundary'] = str(exc)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            result['error'] = 'driver exit: ' + str(exc.code)
    except Exception as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        subprocess.run, subprocess.Popen = original_run, original_popen
        subprocess.check_call, subprocess.check_output = original_call, original_output
        importlib.machinery.SourceFileLoader.source_to_code = original_source_to_code
        if original_main is not None:
            sys.modules['__main__'] = original_main
        sys.argv = old_argv
        os.chdir(old_cwd)
        (out / 'capture.json').write_text(json.dumps(result, indent=2) + '\n')
    return 0


def selected_tests(root, base):
    changed = set(git(root, 'diff', '--name-only', '--diff-filter=ACMR', base, '--', 'tests').splitlines())
    changed.update(git(root, 'ls-files', '--others', '--exclude-standard', '--', 'tests').splitlines())
    direct = {p for p in changed if Path(p).suffix in SUFFIXES}
    selected, covered = set(), set()
    for path in (root / 'tests').glob('test_*.py'):
        text = path.read_text(encoding='utf-8')
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(text)
        except SyntaxError:
            if rel in changed:
                selected.add(rel)
            continue
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                      and node.body and isinstance(node.body[0], ast.Expr)
                      and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)}
        strings = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                   and isinstance(node.value, str) and id(node) not in docstrings]
        refs = {p for p in direct if any(Path(p).name in value for value in strings)}
        # Comments/docstrings naming a neighbouring C test are not compiler drivers.
        generates = 'subprocess' in text and any(value in ('--cc', '--cxx', 'gcc', 'g++', 'clang', 'clang++')
                                                or Path(value).suffix in SUFFIXES for value in strings)
        if refs or (rel in changed and generates):
            selected.add(rel)
            covered.update(refs)
    return sorted(selected), sorted(direct - covered)


def function(text, name):
    match = re.search(r'(?:static\s+)?(?:int|void)\s+' + re.escape(name) + r'\s*\([^;]*?\)\s*\{', text)
    if not match:
        raise ValueError('cannot locate function ' + name)
    start, depth = match.start(), 1
    i = match.end()
    # Functions selected here contain no braces in strings. Refuse an unbalanced extraction.
    while i < len(text) and depth:
        depth += (text[i] == '{') - (text[i] == '}')
        i += 1
    if depth:
        raise ValueError('unbalanced function ' + name)
    return text[start:i]


def empty_environment_contract(root, out):
    """Focused Linux execution of the actual fixture assertion with Windows CRT semantics.

    This is intentionally separate from syntax success. It proves the historical empty
    environment expectation, not the Windows relay, sockets, threads or game runtime.
    """
    path = root / 'tests/coop_host_relay_test.c'
    text = path.read_text()
    start = text.index('    ini_set("coop_host", "1");', text.index('static void host_settings('))
    stop = text.index('    if (!strcmp(mode, "foreign"))', start)
    prefix = text[start:stop]
    setter = function((root / 'tests/coop_session_test.c').read_text(), 'set_env')
    enabled = function((root / 'host/coop_relay.c').read_text(), 'coop_relay_enabled')
    code = '''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "hookshot_env.h"
static const char *value;
static int failures, ini;
static int windows_putenv(const char *name, const char *v) { (void)name; value = v && *v ? v : NULL; return 0; }
static char *windows_getenv(const char *name) { (void)name; return (char *)value; }
static void ini_set(const char *name, const char *v) { (void)name; ini = atoi(v); }
static int hos_ini_option(const char *name, int def) { (void)name; (void)def; return ini; }
#define _WIN32 1
#define _putenv_s windows_putenv
#define getenv windows_getenv
#define CHECK(ok, message) do { if (!(ok)) { puts(message); failures++; } } while (0)
''' + setter + '\n' + enabled + '\nstatic void check(const char *mode) {\n' + prefix + '''}
int main(void) { const char *modes[] = {"ini", "", "0", "1", "false", "off", "no"};
for (unsigned i = 0; i < sizeof(modes)/sizeof(*modes); ++i) check(modes[i]);
printf("empty-environment contract: %d failures\\n", failures); return failures != 0; }
'''
    src, exe = out / 'empty-env-contract.c', out / 'empty-env-contract'
    src.write_text(code)
    argv = ['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-I' + str(root / 'gpu/vk/compat'), str(src), '-o', str(exe)]
    r = command(argv)
    if not r.returncode:
        r = command([exe])
    return {'status': 'FAIL' if r.returncode else 'PASS', 'command': argv, 'output': r.stdout + r.stderr,
            'scope': 'Linux focused assertion with Windows empty-variable removal; NOT a Windows runtime run'}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--capture':
        return child(*sys.argv[2:])
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('worktree', type=Path)
    p.add_argument('base', nargs='?')
    p.add_argument('--test', action='append', default=[], help='explicit driver, for historical proofs')
    p.add_argument('--output', type=Path, help='new evidence directory; never overwritten')
    p.add_argument('--sysroot', type=Path, default=os.environ.get('HOOKSHOT_MINGW'))
    a = p.parse_args()
    root = a.worktree.resolve()
    base = a.base or git(root, 'merge-base', 'origin/main', 'HEAD')
    sysroot = a.sysroot
    if sysroot is None:
        candidates = sorted((Path.home() / 'Code/hookshot-wt/deps/llvm-mingw').glob('*/include/windows.h'))
        sysroot = candidates[-1].parent.parent if candidates else None
    if not sysroot or not (sysroot / 'include/windows.h').is_file():
        p.error('set HOOKSHOT_MINGW or --sysroot to llvm-mingw with include/windows.h')
    out = a.output or Path(tempfile.mkdtemp(prefix='hs-win-fixtures-'))
    if a.output:
        out.mkdir(parents=True, exist_ok=False)
    out = out.resolve()
    tests, unmatched = selected_tests(root, base) if not a.test else (a.test, [])
    report = {'head': git(root, 'rev-parse', 'HEAD'), 'base': base, 'tests': [], 'unjudged': unmatched}
    print('Windows fixture syntax:', report['head'], 'base', base, 'evidence', out)
    for index, name in enumerate(tests):
        folder = out / str(index)
        folder.mkdir()
        r = command([sys.executable, '-I', '-B', __file__, '--capture', root, root / name, folder, sysroot], timeout=180)
        (folder / 'driver.log').write_text(r.stdout + r.stderr)
        if not (folder / 'capture.json').is_file():
            report['unjudged'].append(name + ': capture failed: ' + r.stderr)
            continue
        row = json.loads((folder / 'capture.json').read_text())
        report['tests'].append(row)
        for record in row['records']:
            print(record['status'], name, '->', record['source'])
            if record.get('diagnostics'):
                print(record['diagnostics'], end='')
        if row['error'] or not row['records']:
            report['unjudged'].append(name + ': ' + (row['error'] or row['boundary'] or 'no compile command reached'))
        if row['boundary']:
            print('NOT TESTED:', name, row['boundary'])
        print('COVERAGE:', name, 'only compile calls reached before execution; later runtime-dependent variants cannot be judged')
        if name.endswith('test_coop_host_relay.py') and row['records']:
            try:
                row['empty_environment_contract'] = empty_environment_contract(root, folder)
                c = row['empty_environment_contract']
                print(c['status'], c['scope'], '\n' + c['output'])
            except (ValueError, OSError) as exc:
                report['unjudged'].append(name + ': empty-environment contract: ' + str(exc))
    for why in report['unjudged']:
        print('UNJUDGED:', why)
    records = [r for t in report['tests'] for r in t['records']]
    failed = sum(r['status'] == 'FAIL' for r in records) + sum(t.get('empty_environment_contract', {}).get('status') == 'FAIL' for t in report['tests'])
    unknown = bool(report['unjudged']) or any(r['status'] == 'UNJUDGED' for r in records)
    report['exit'] = 1 if failed else 2 if unknown else 0
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(('FAIL' if failed else 'INCOMPLETE' if unknown else 'PASS') + ': %d captured translation units, %d failures, %d unjudged drivers/files; Windows execution NOT TESTED' % (len(records), failed, len(report['unjudged'])))
    return report['exit']


if __name__ == '__main__':
    sys.exit(main())
