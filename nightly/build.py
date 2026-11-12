"""Full workbench build of an immutable nightly target, into an owned reflink snapshot."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HARN, LOCAL, capture, command, env_clean, gated, put, stamp, text


def resolve(config, out):
    release = json.loads(capture(['gh', 'release', 'view', 'nightly', '--repo', config['repository'],
                                 '--json', 'tagName,publishedAt,targetCommitish,url,name']))
    fetched = capture(['git', '-C', config['repo'], 'fetch', 'origin', '+refs/tags/nightly:refs/tags/nightly'])
    (out / 'fetch.log').write_text(fetched)
    commit = capture(['git', '-C', config['repo'], 'rev-parse', 'nightly^{commit}']).strip()
    assert re.fullmatch('[0-9a-f]{40}', commit)
    put(out / 'release.json', {**release, 'commit': commit, 'resolved_at': stamp()})
    return commit


def file_identity(path):
    path = Path(path)
    with path.open('rb') as f:
        return {'bytes': path.stat().st_size, 'sha256': hashlib.file_digest(f, 'sha256').hexdigest()}


def selected_diagnostics(config, repo, commit):
    rules = config.get('diagnostic_rules', [])
    if not rules:
        return config.get('diagnostic_commits', [])
    for rule in rules:
        if 'contains' not in rule:
            return rule['commits']
        test = subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', rule['contains'], commit])
        if test.returncode == 0:
            return rule['commits']
        if test.returncode != 1:
            raise RuntimeError('cannot select a compatible logging aid from ancestry')
    raise RuntimeError('no logging aid is declared for this source history')


def build_timings(log):
    times = re.findall(r'^@@times (\d+) (\d+) (-?\d+)', log, re.M)
    linked = re.findall(r'^linked .* \((\d+)s\)', log, re.M)
    compiled = re.findall(r'^compiled in (\d+)s,', log, re.M)
    return {'total': int(times[-1][0]) if times else None,
            'compile': int(times[-1][1]) if times else None,
            'compile_workers_wall': int(compiled[-1]) if compiled else None,
            'translation': None, 'link': sum(map(int, linked)) if linked else None}


def toolchain(config):
    result = {'compiler': capture(['clang', '--version']).splitlines()[0],
              'linker': capture(['ld.lld', '--version']).strip(),
              'python': capture([config['python'], '--version']).strip(),
              'workers': config['build_jobs'], 'lift_workers': 8, 'release': True,
              'hot_optimization': 'O2 with recorded PGO', 'other_lifted_optimization': 'O1',
              'system': capture(['uname', '-srm']).strip()}
    cpu = re.search(r'^model name\s*:\s*(.+)$', text('/proc/cpuinfo'), re.M)
    memory = re.search(r'^MemTotal:\s*(\d+)', text('/proc/meminfo'), re.M)
    result['cpu'] = cpu[1] if cpu else 'unavailable'
    result['memory_kib'] = int(memory[1]) if memory else None
    gpu = subprocess.run(['nvidia-smi', '--query-gpu=name,driver_version,memory.total',
                          '--format=csv,noheader'], capture_output=True, text=True, timeout=10)
    result['gpu'] = gpu.stdout.strip() if gpu.returncode == 0 else 'unavailable: ' + gpu.stderr.strip()
    return result


def run(config, out, commit=None):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    commit = commit or resolve(config, out)
    wt, staging = Path(config['worktree']), Path(config['build_copy'])
    dest = Path(config['copy_prefix'] + out.name)
    work = Path(config.get('work_dir', str(wt / 'work/build')))
    if config.get('fresh_build') and (work.exists() or staging.exists()):
        raise RuntimeError('fresh build requires unused work/output folders; existing data is never deleted to force it')
    info = {'commit': commit, 'started': stamp(), 'warnings_suppressed_by_upstream': True,
            'copy': str(dest), 'worktree': str(wt), 'work_dir': str(work), 'staging_copy': str(staging),
            'fresh_build': bool(config.get('fresh_build')), 'status': 'building'}
    put(out / 'build.json', info)
    from monitor import wait_for_player_idle
    wait_for_player_idle(config, out / 'player-idle.jsonl')
    if not wt.exists():
        capture(['git', '-C', config['repo'], 'worktree', 'add', '--detach', wt, commit])
    else:
        dirty = capture(['git', '-C', wt, 'status', '--porcelain', '--untracked-files=no'])
        if dirty.strip():
            raise RuntimeError('Owned nightly worktree has tracked changes; refusing to replace them')
        capture(['git', '-C', wt, 'checkout', '--detach', commit])
    assert capture(['git', '-C', wt, 'rev-parse', 'HEAD']).strip() == commit
    diagnostics = selected_diagnostics(config, wt, commit)
    for diagnostic in diagnostics:
        capture(['git', '-c', 'user.name=' + config['author_name'],
                 '-c', 'user.email=' + config['author_email'], '-C', wt, 'cherry-pick', '-x', diagnostic])
    info['source_commit'] = capture(['git', '-C', wt, 'rev-parse', 'HEAD']).strip()
    info['diagnostic_commits'] = diagnostics
    put(out / 'build.json', info)
    if config.get('reuse_dependencies'):
        if not os.access(config['python'], os.X_OK) or not Path(config['cpm']).is_dir():
            raise RuntimeError('fixed dependency environment is unavailable')
        (out / 'deps.log').write_text('Reused the configured fixed Python/toolchain/headers; no shared dependency folder was replaced.\n')
    else:
        rc = command([HARN / 'tools/deps-local.sh', wt], out / 'deps.log')
        if rc:
            raise RuntimeError('Dependency setup failed; see deps.log')
    if config.get('profile_source'):
        (wt / 'work').mkdir(exist_ok=True)
        for name in config.get('profile_files', ['hookshot_pieces.prof', 'hot_funcs.txt', 'hot_order.txt']):
            source, target = Path(config['profile_source']) / name, wt / 'work' / name
            expected = file_identity(source)
            if target.exists():
                if file_identity(target) != expected:
                    raise RuntimeError('profile input changed: ' + name)
            else:
                capture(['cp', '--reflink=always', source, target])
            assert file_identity(target) == expected
    profile_env = env_clean({'TMPDIR': config['release_cache']})
    if not (wt / 'work/hookshot_pieces.prof').is_file():
        # release_parts uses gh's authenticated downloads without printing its token.
        profile_env['GH_TOKEN'] = capture(['gh', 'auth', 'token']).strip()
        rc = command([config['python'], '-I', '-B', 'scripts/release_parts.py', 'profile',
                      config['release_setup'], 'work'], out / 'profile.log', cwd=wt, env=profile_env)
        profile_env.pop('GH_TOKEN', None)
        if rc:
            raise RuntimeError('Play profile setup failed; see profile.log')
    info['profile'] = {name: file_identity(wt / 'work' / name)
                       for name in ('hookshot_pieces.prof', 'hot_funcs.txt', 'hot_order.txt')}
    info['toolchain'] = toolchain(config)
    info['object_cache_fresh_before_build'] = not (work / 'objcache').exists()
    if config.get('dependency_reference_commit'):
        info['upstream_dependency_files_differ'] = capture([
            'git', '-C', wt, 'diff', '--name-only', config['dependency_reference_commit'], commit, '--',
            'scripts/linux/deps.sh', 'requirements.txt', 'nx2/prompt_requirements.txt']).splitlines()
    put(out / 'build.json', info)
    build_env = ['RX_CPM=' + config['cpm'], 'HOOKSHOT_LIFT_JOBS=8', 'HOOKSHOT_KEEP_BUILD_FILES=1', 'RX_HOT_OPT=O2']
    if config.get('ngx_sdk'):
        if not Path(config['ngx_sdk']).is_dir():
            raise RuntimeError('Already accepted local upscaler SDK is missing; no license is accepted automatically')
        build_env.append('HOOKSHOT_NGX_SDK=' + config['ngx_sdk'])
    argv = gated(['flock', config['full_build_lock'], config['build_gate'], 'env', *build_env,
                  config['python'], '-I', '-B', '-u', 'nx2/make_game.py',
                  '--romfs', config['romfs'], '--exefs', config['exefs'], '--out', staging,
                  '--work', work, '--jobs', str(config['build_jobs']),
                  '--no-prepare-shaders', '--log', out / 'make-game.log'])
    put(out / 'build-command.json', argv)
    info['build_command_started'] = stamp()
    start = time.monotonic()
    rc = command(argv, out / 'build.log', cwd=wt, guard=config)
    elapsed = time.monotonic() - start
    log = text(out / 'build.log')
    warnings = [line for line in log.splitlines() if re.search(r'\bwarning\b', line, re.I)]
    info.update(exit=rc, elapsed_including_gate_s=elapsed, ended=stamp(), warnings=warnings)
    info['build_command_ended'] = info['ended']
    info['phase_seconds'] = build_timings(log)
    info['phase_limits'] = 'Translation is not separately timed in this log; compile is the builder aggregate; link sums its rounded link lines.'
    info['object_key_lines'] = [line for line in log.splitlines() if line.startswith('object keys:')]
    # make_game reports its own elapsed duration; gate waiting is separate above.
    duration = re.findall(r'the build took[^\n]+', log)
    info['build_duration_lines'] = duration
    if rc or '@@done ' not in log:
        info['status'] = 'failed'
        put(out / 'build.json', info)
        raise RuntimeError('Full build failed or missing @@done; see build.log')
    if dest.exists():
        raise RuntimeError('Night snapshot already exists; refusing to overwrite it')
    capture(['cp', '-a', '--reflink=always', staging, dest])
    (dest / '.nightly-owned').write_text(out.name + '\n')
    info['binary_bytes'] = {name: (dest / name).stat().st_size for name in ('hookshot', 'hookshot_vk.so')}
    info['binary_sha256'] = {name: hashlib.file_digest((dest / name).open('rb'), 'sha256').hexdigest()
                             for name in info['binary_bytes']}
    info['non_romfs_bytes'] = sum(p.stat().st_size for p in dest.rglob('*')
                                 if p.is_file() and 'romfs' not in p.relative_to(dest).parts)
    info['status'] = 'passed'
    put(out / 'build.json', info)
    return info


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    parser.add_argument('--config', type=Path, default=LOCAL / 'config.json')
    parser.add_argument('--commit', help='already fetched immutable commit; avoids a second fetch during a resumed cycle')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    print(json.dumps(run(config, Path(args.output), args.commit), indent=2), flush=True)
