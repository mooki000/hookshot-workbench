"""Rolling main orchestration using the nightly builder and the shared regression runner.

All scenes, references, SDK paths and local data come from private configuration. No timer,
posting, second renderer measurement implementation, or automatic hunt is introduced.
Fixed-release initialization and three paired scene/mode comparisons of a newer main have run; catalogue and exhaustive/stress families remain unrun.
"""
from pathlib import Path
from datetime import datetime
import copy
import fcntl
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from common import HARN, LOCAL, capture, command, env_clean, gated, put, slug, stamp, text
from monitor import processes, wait_for_player_idle
from compare import compare_paired
from store import Store
import build as builder

ROOT = LOCAL / 'rolling'
CACHE_FILES = ('hookshot_programs.bin', 'hookshot_programs.idx', 'hookshot_pipelines.bin',
               'mkw_vk_pipelines.bin', 'hookshot_computes.bin', 'hookshot_pipeline_states.bin',
               'hookshot_prepared.bin', '.nv-cache', '.xdg-cache', '.mesa-cache')


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def shared_tools(config):
    folder = Path(config['regress_dir'])
    engine = load_module('rolling_shared_regress', folder / 'regress.py')
    adapter = load_module('rolling_shared_run_case', folder / 'run_case.py')
    for module, names in ((engine, ('classification', 'paired_decision', 'repeats_agree')),
                          (adapter, ('quiet_measure', 'quiet_verdict', 'present_window'))):
        if not all(callable(getattr(module, name, None)) for name in names):
            raise RuntimeError('shared regression tools do not yet provide the approved quiet-rule API')
    return engine, adapter


def begin_main_cycle(state, started):
    state.setdefault('cadence_anchor', state.get('last_started', started))
    state.setdefault('next_full_due_epoch', datetime.fromisoformat(state['cadence_anchor']).timestamp() + 21600)
    state['last_started'] = started


def full_rebuild_due(state, now):
    due = state.get('next_full_due_epoch')
    if due is None and state.get('last_started'):
        due = datetime.fromisoformat(state['last_started']).timestamp() + 21600
    return due is not None and now >= due


def target_decision(head, last_completed, last_started, now, automatic, full_due=False):
    if full_due:
        return 'full-rebuild'
    if last_completed and last_completed.get('commit') == head:
        return 'unchanged'
    if automatic and last_started and now - datetime.fromisoformat(last_started).timestamp() < 10800:
        return 'not-due'
    return 'run'


def source_stamp(build, directory):
    found = re.findall(r'^version: .*\(source ([0-9a-f]+)\)', text(Path(directory) / 'build.log'), re.M)
    if len(found) != 1:
        raise RuntimeError('completed build log must identify exactly one source stamp')
    return found[0]


def snapshot_reference(manifest, destination):
    """Read-only source, owned reflink target; never run from the supplied reference folder."""
    source, destination = Path(manifest['copy']), Path(destination)
    if destination.exists():
        raise RuntimeError('reference destination exists; refusing to overwrite evidence')
    if any(Path(p['exe']) == source / 'hookshot' for p in processes()):
        raise RuntimeError('reference copy is running; obtain an idle completed snapshot before retrying')
    if (source / 'mods').exists():
        raise RuntimeError('reference has mods; cannot silently change its meaning')
    destination.mkdir(parents=True)
    for item in source.iterdir():
        if item.name in ('runs', 'hookshot_options.ini', 'hookshot_env.txt', '.nightly-owned'):
            continue
        capture(['cp', '-a', '--reflink=always', item, destination / item.name])
    hashes = manifest.get('binary_sha256', manifest.get('sha256', {}))
    if not hashes:
        raise RuntimeError('reference needs recorded binary hashes')
    for name, expected in hashes.items():
        with (destination / name).open('rb') as f:
            assert hashlib.file_digest(f, 'sha256').hexdigest() == expected, 'reference binary identity changed'
    if any(Path(p['exe']) == source / 'hookshot' for p in processes()):
        raise RuntimeError('source started during the snapshot; cache consistency is not established')
    (destination / '.rolling-owned').write_text(str(destination.name) + '\n')
    return {**manifest, 'copy': str(destination), 'binary_sha256': hashes,
            'commit': manifest.get('commit', manifest.get('base')), 'snapshot_of': str(source)}


def compatible_cache(config, previous, new):
    old = previous.get('source_commit', previous['commit'])
    new_head = new.get('source_commit', new['commit'])
    p = subprocess.run(['git', '-C', config['repo'], 'diff', '--quiet', old, new_head, '--',
                        'nx2/build.py', 'gpu/vk/eden', 'gpu/vk/sirit', 'gpu/vk/compat',
                        'gpu/vk/vk_shader.cpp', 'gpu/vk/vk_bank.cpp', 'gpu/vk/vk_state.h', 'gpu/vk/vk_api.h'], env=env_clean())
    if p.returncode not in (0, 1):
        raise RuntimeError('cannot establish cache compatibility')
    return p.returncode == 0


def reuse_cache(previous, new):
    copied = []
    for name in CACHE_FILES:
        source, target = Path(previous['copy']) / name, Path(new['copy']) / name
        if source.exists():
            if target.exists():
                raise RuntimeError('new build unexpectedly already has a cache: ' + name)
            capture(['cp', '-a', '--reflink=always', source, target])
            copied.append(name)
    return copied


def scene_for(template, label, stamp_value, timing=True):
    scene = copy.deepcopy(template)
    scene['name'] = label
    scene['kind'] = 'timing' if timing else 'game-check'
    scene.update(quiet_cores=3, quiet_peak_cores=6, repeat_tolerance=0.05)
    scene.pop('build_command', None)
    scene['measure'] = {k: scene['measure'][k] for k in ('name', 'direction')}
    scene['run']['label'] = label
    replaced = 0
    expectations = []
    for pattern in scene['run'].get('expect_log', []):
        if re.fullmatch(r'source [0-9a-f]+', pattern):
            pattern = 'source ' + stamp_value
            replaced += 1
        expectations.append(pattern)
    if replaced != 1:
        raise RuntimeError('shared scene must name exactly one source-stamp expectation')
    scene['run']['expect_log'] = expectations
    return scene


def make_plan(config, out, name, entries, scenarios, timing):
    path = out / 'plans' / (name + '.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    seconds = max(s['seconds'] for s in scenarios)
    grace = max(s.get('grace_seconds', 15) for s in scenarios)
    spare = 480 - 10 - len(entries) * (seconds + grace)
    if spare < 0:
        raise ValueError('shared held-worker plan would exceed eight minutes')
    plan = {'kind': 'timing' if timing else 'game-check', 'max_hold_seconds': 480,
            'seconds': seconds, 'grace_seconds': grace,
            'settle_seconds': min(spare, max(s.get('settle_seconds', 0) for s in scenarios)) if timing else 0,
            'max_load': 8, 'quiet_cores': 3, 'quiet_peak_cores': 6,
            'scenario': entries[0]['scenario'], 'entries': entries,
            'run_command': [config['python'], '-I', '-B', str(Path(config['regress_dir']) / 'run_case.py'),
                            '--copy', '{copy}', '--output', '{output}', '--scenario', '{scenario}',
                            '--commit', '{commit}', '--seconds', '{seconds}']}
    if timing and config.get('avoid'):
        plan['window'] = config['measurement_window']
        plan['avoid'] = config['avoid']
    if path.exists():
        if json.loads(path.read_text()) != plan:
            raise RuntimeError('existing held plan differs; refusing to replace it')
    else:
        put(path, plan)
    return path


def run_hold(config, build_config, path, timing):
    if path.with_suffix('.runs.json').exists():
        rows = json.loads(path.with_suffix('.runs.json').read_text())
        hold = json.loads(path.with_suffix('.hold.json').read_text())
        if len(rows) != len(json.loads(path.read_text())['entries']) or any(not r['result'].get('valid') for r in rows):
            raise RuntimeError('previous hold was incomplete/invalid; no automatic hold retry')
        return rows, hold
    asked = path.with_suffix('.asked.json')
    if asked.exists():
        raise RuntimeError('hold was already requested but has no complete evidence; never queue a duplicate')
    reserved = [json.loads(p.read_text()) for p in path.parent.glob('*.asked.json')]
    same_kind = sum(('--exclusive' in item.get('flags', [])) == timing for item in reserved)
    if same_kind >= (3 if timing else 2):
        raise RuntimeError('cycle hold budget exhausted; no new request is allowed')
    wait_for_player_idle(build_config, path.with_suffix('.idle.jsonl'))
    flags = ['--exclusive'] if timing else []
    argv = gated([config['gpu_gate'], *flags, 'timeout', '--signal=TERM', '--kill-after=5', '475',
                  config['python'], '-I', '-B', Path(config['regress_dir']) / 'regress.py', '_hold', path])
    put(asked, {'asked': stamp(), 'flags': flags, 'argv': list(map(str, argv))})
    plan = json.loads(path.read_text())
    guard = {**build_config, 'owned_copies': sorted({entry['copy'] for entry in plan['entries']})}
    rc = command(argv, path.with_suffix('.log'), env=env_clean(game=True), guard=guard)
    rows = json.loads(path.with_suffix('.runs.json').read_text()) if path.with_suffix('.runs.json').exists() else []
    hold = json.loads(path.with_suffix('.hold.json').read_text()) if path.with_suffix('.hold.json').exists() else {}
    hold['asked'] = json.loads(asked.read_text())['asked']
    hold['queue_seconds'] = (datetime.fromisoformat(hold['started']).timestamp() -
                             datetime.fromisoformat(hold['asked']).timestamp()) if hold.get('started') else None
    hold.update(gate_exit=rc, returned=stamp(), flags=flags, plan=str(path), counted=timing)
    put(path.with_suffix('.hold.json'), hold)
    if rc or not rows or any(r['result'].get('fatal') or r['result'].get('kernel_errors') for r in rows):
        raise RuntimeError('hold stopped on gate/safety/fatal failure; no further hold is requested')
    return rows, hold


def augment_accounting(row, adapter):
    """Re-read legacy quiet evidence through the shared helper; never derive CPU metrics here."""
    result, output = row['result'], Path(row['output'])
    measures = result.get('measures', {})
    if not result.get('valid'):
        return
    try:
        if result.get('others_cores_window') is None and {'first_frame', 'last_frame'} <= measures.keys():
            window = adapter.present_window(output / 'present.csv', measures['first_frame'], measures['last_frame'])
            if window:
                quiet = adapter.quiet_measure(json.loads((output / 'samples.json').read_text()), *window)
                if quiet:
                    result['others_cores_window'], result['others_cores_peak'] = quiet['mean'], quiet['peak']
        result['shared_quiet_rule'] = adapter.quiet_verdict(result, {'quiet_cores': 3, 'quiet_peak_cores': 6})
    except (OSError, ValueError, KeyError, AssertionError) as error:
        result['quiet_reanalysis_unavailable'] = str(error)


def record_rows(store, cycle, scene_id, rows, adapter):
    for index, row in enumerate(rows):
        augment_accounting(row, adapter)
        result = row['result']
        metrics = {key: {'value': value, 'kind': 'timing', 'direction': 'lower' if key.endswith('fps') else 'higher'}
                   for key, value in result.get('measures', {}).items()
                   if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)}
        if result.get('cpu_ms_per_frame') is not None:
            metrics['cpu_ms_per_frame'] = {'value': result['cpu_ms_per_frame'], 'kind': 'timing', 'direction': 'higher'}
        if result.get('peak_rss_mb') is not None:
            metrics['peak_rss_mb'] = {'value': result['peak_rss_mb'], 'kind': 'memory', 'direction': 'higher'}
        stored = {'id': scene_id + '-' + str(index + 1) + '-' + row['role'],
                  'scenario_sha256': 'rolling-shared-v1:' + scene_id, 'status': 'passed' if result.get('valid') else 'failed',
                  'disturbed': result.get('disturbed', True), 'metrics': metrics, 'signatures': {}, 'images': {},
                  'run': row, 'evidence_dir': row['output']}
        store.record('rolling-' + cycle, stored, False)  # paired cycles do not seed nightly median bands


def fixed_references(config, current_scenes, adapter):
    references = {}
    candidates = []
    cache = ROOT / 'fixed-references.json'
    if cache.is_file():
        for rows in json.loads(cache.read_text()).values():
            candidates.extend(rows)
    for pattern in config.get('reference_records', []):
        import glob
        for path in glob.glob(pattern):
            try:
                value = json.loads(Path(path).read_text())
                if isinstance(value, list):
                    candidates.extend({**row, 'record_file': path} for row in value)
            except (OSError, ValueError):
                continue  # an atomically written later result may not exist yet
    for scene in current_scenes:
        candidates.extend(scene.get('runs', []))
    for fixed in config['fixed_commits']:
        for scene in config['scenes']:
            found = []
            template = json.loads(Path(scene['template']).read_text())
            wanted = {k: str(v) for k, v in template['run'].get('env', {}).items()}
            for row in candidates:
                if not row.get('commit', '').startswith(fixed['commit']):
                    continue
                if not row.get('label', '').endswith(scene['reference_suffix']):
                    continue
                result = row.get('result', {})
                actual = {k: str(v) for k, v in result.get('environment', {}).items() if k != 'RX_VK_READBACK_CSV'}
                if actual != wanted or not result.get('valid'):
                    continue
                row = copy.deepcopy(row)
                augment_accounting(row, adapter)
                found.append(row)
            found.sort(key=lambda r: r['result'].get('end', ''))
            # Do not mix two different repetitions of the same path when imported and current overlap.
            unique = {r['output']: r for r in found}
            latest = sorted(unique.values(), key=lambda r: r['result'].get('end', ''))[-2:]
            references[fixed['name'] + '/' + scene['id']] = latest
    put(ROOT / 'fixed-references.json', references)
    return references


def handoff(config, build_config, out, scene, decision, previous, new):
    folder = out / 'handoffs' / scene['id']
    folder.mkdir(parents=True, exist_ok=True)
    patch = capture(['git', '-C', config['repo'], 'diff', new['commit'], new['source_commit'], '--', 'gpu', 'host'])
    (folder / 'diagnostics.patch').write_text(patch)
    prebuilt = {}
    for label, build in (('previous', previous), ('new', new)):
        manifest = folder / (label + '-build.json')
        marker = Path(build['copy']) / 'regress-source.txt'
        if marker.exists() and marker.read_text().strip() != build['source_stamp']:
            raise RuntimeError('prebuilt source marker disagrees with the measured binary')
        if not marker.exists():
            marker.write_text(build['source_stamp'] + '\n')
        put(manifest, {'copy': build['copy'], 'commit': build['commit'], 'sha256': build['binary_sha256'],
                       'source_stamp': build['source_stamp']})
        prebuilt[build['commit']] = str(manifest)
    scenario_path = folder / 'scenario.json'
    case = copy.deepcopy(scene['scenario'])
    case['measure'] = decision['measure']
    # Exact identity moves from a single static arm to the shared adapter's per-copy check.
    case['run']['expect_log'] = [p for p in case['run'].get('expect_log', [])
                                if not re.fullmatch(r'source [0-9a-f]+', p)]
    case['requires_regress_source_marker'] = True
    case['source_identity'] = 'run_case enforces regress-source.txt; build_case writes each midpoint stamp'
    case['build'] = {'python': config['python'], 'cpm': build_config['cpm'],
                     'game_root': str(Path(new['copy']).parent), 'profile_dir': str(Path(build_config['worktree']) / 'work'),
                     'romfs': build_config['romfs'], 'exefs': build_config['exefs'], 'jobs': build_config['build_jobs'],
                     'lift_jobs': 8, 'build_gate': build_config['build_gate'], 'full_build_lock': build_config['full_build_lock'],
                     'ngx_sdk': build_config.get('ngx_sdk'), 'prebuilt': prebuilt,
                     'patches': [{'path': str(folder / 'diagnostics.patch')}]}
    case['build_command'] = [config['python'], '-I', '-B', str(Path(config['regress_dir']) / 'build_case.py'),
                             '--worktree', '{worktree}', '--commit', '{commit}', '--output', '{output}',
                             '--lift-key', '{lift_key}', '--donor-worktree', '{donor_worktree}',
                             '--build-kind', '{build_kind}', '--reverts', '{reverts}', '--scenario', str(scenario_path)]
    case['run_command'] = [config['python'], '-I', '-B', str(Path(config['regress_dir']) / 'run_case.py'),
                           '--copy', '{copy}', '--output', '{output}', '--commit', '{commit}',
                           '--scenario', '{scenario}', '--seconds', '{seconds}']
    case['nightly_confirmation'] = {'decision': decision, 'runs': [r['output'] for r in scene['runs']]}
    put(scenario_path, case)
    argv = [config['python'], '-I', '-B', str(Path(config['regress_dir']) / 'regress.py'), 'hunt',
            '--repo', config['repo'], '--good', previous['commit'], '--bad', new['commit'],
            '--scenario', str(scenario_path), '--output', str(folder / 'hunt'), '--max-steps', '8',
            '--max-wall-seconds', '5400', '--max-holds', '10', '--max-hold-seconds', '480', '--exclusive-authorized']
    record = {'good': previous['commit'], 'bad': new['commit'], 'scenario': str(scenario_path), 'command': argv,
              'decision': decision, 'automatic_hunt_started': False,
              'authorization': 'a future hunt needs its own approval; rolling hold authorization does not cover this command',
              'diagnostic_limit': 'prebuilt endpoints are exact; a midpoint where the logging patch does not apply must stop, not guess'}
    put(folder / 'handoff.json', record)
    (folder / 'commits.txt').write_text(capture(['git', '-C', config['repo'], 'log', '--reverse', '--format=%H %s',
                                               previous['commit'] + '..' + new['commit']]))
    return str(folder / 'handoff.json')


def write_report(config, out, record):
    f = lambda v, digits=3: '-' if v is None else f'{v:.{digits}f}'
    build = record.get('build', {})
    hold_files = list((out / 'plans').glob('*.hold.json'))
    holds = [json.loads(path.read_text()) for path in sorted(hold_files)] if hold_files else record.get('holds', [])
    holds.sort(key=lambda h: h.get('started', ''))
    record['holds'] = holds
    known = [h['seconds_used'] for h in holds if isinstance(h.get('seconds_used'), (int, float))]
    gpu = sum(known)
    unknown = len(holds) - len(known)
    assert gpu >= 0 and len([h for h in holds if h.get('counted')]) <= 3
    is_baseline = record.get('mode') == 'release-baseline'
    title = 'Release baseline' if is_baseline else 'Rolling main'
    lines = [f"# {title} — {record['cycle']}", '', '## State', '',
             f"{stamp()} — {record['status']}. {record.get('error', '')}", '',
             f"Pinned {'release' if is_baseline else 'origin/main'}: `{record['commit']}` at {record['started']}.",
             f"{'Paired main' if is_baseline else 'Previous'}: `{record.get('previous', {}).get('commit', 'not selected')}`.",
             'Source identity includes the recorded identical opt-in logging commit; the pinned upstream base is not silently relabeled.', '',
             '## Cost of this build', '',
             f"Full build including its gate wait: {f(build.get('elapsed_including_gate_s'))} s. "
             + '; '.join(build.get('build_duration_lines', [])),
             f"Build start/end: {build.get('started', '-')} / {build.get('ended', '-')}. This also shows preflight idle waiting.",
             f"Known GPU gate occupancy: {gpu:.1f} s across {len(holds)} holds; {unknown} hold durations unavailable. Queue waiting is separate.",
             'Preparation and warm-up numbers are never used as measurements.', '',
             '| Hold | Kind | GPU seconds | Queue seconds | Gate exit | Asked flags |', '|---|---|---:|---:|---:|---|']
    for hold in holds:
        lines.append(f"| {Path(hold.get('plan', '')).stem} | {'measurement' if hold.get('counted') else 'unmeasured'} | "
                     f"{f(hold.get('seconds_used'), 1)} | {f(hold.get('queue_seconds'), 1)} | "
                     f"{hold.get('gate_exit', '-')} | {hold.get('flags', [])} |")
    lines += ['', '## Measurement table', '',
              'Only valid quiet runs enter a decision: other work <3 cores on average, no five-second sample >6; '
              'each judged arm repeats within 5%. Loads are recorded, not used as a substitute for that rule.', '',
              '| Scene | Arm | Counts? | fps | mean ms | 1% low | >20 ms | >25 ms | CPU ms/frame (all threads) | readback wait ms/frame | peak RSS MiB | other cores avg / peak | load before / after |',
              '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|']
    table_header = lines[-2:]
    for scene_index, scene in enumerate(record.get('scenes', [])):
        if scene_index:
            lines.extend(['', *table_header])
        for row in scene.get('runs', []):
            r, m = row['result'], row['result'].get('measures', {})
            counts = r.get('valid') is True and r.get('disturbed') is False and r.get('kernel_errors') == 0
            lines.append(f"| {scene['id']} | {row['label']} | {'yes' if counts else 'no'} | {f(m.get('fps'), 2)} | "
                         f"{f(m.get('mean_ms'))} | {f(m.get('low1_fps'), 2)} | {m.get('over20', '-')} | {m.get('over25', '-')} | "
                         f"{f(r.get('cpu_ms_per_frame'))} | {f(m.get('readback_wait_ms_per_frame'))} | {f(r.get('peak_rss_mb'), 1)} | "
                         f"{f(r.get('others_cores_window'), 2)} / {f(r.get('others_cores_peak'), 2)} | "
                         f"{f(r.get('load_before'), 2)} / {f(r.get('load_after'), 2)} |")
        lines += ['', f"**{scene['id']}: {scene.get('decision', {}).get('state', 'not run')}**", '',
                  '```json', json.dumps(scene.get('decision', {}), indent=2), '```', '']
        if scene.get('handoff'):
            lines.append('Shared-hunt handover (not started): `' + scene['handoff'] + '`.')
    tested = {s['id'] for s in record.get('scenes', [])}
    lines += ['Not run in this cycle: ' + ', '.join(s['id'] for s in config['scenes'] if s['id'] not in tested), '',
              '## Fixed measured references — not rerun each cycle', '',
              '| Reference / scene | Measured at | fps | mean ms | 1% low | Shared quiet rule? | Evidence |',
              '|---|---|---:|---:|---:|---|---|']
    for name, rows in record.get('references', {}).items():
        if not rows:
            lines.append(f'| {name} | not yet measured | - | - | - | - | - |')
        for row in rows:
            r, m = row['result'], row['result'].get('measures', {})
            lines.append(f"| {name} | {r.get('end', '-')} | {f(m.get('fps'), 2)} | {f(m.get('mean_ms'))} | "
                         f"{f(m.get('low1_fps'), 2)} | {r.get('shared_quiet_rule', 'unavailable')} | `{row['output']}` |")
    lines += ['', '## Limits and provenance', '',
              'This is headless CPU-present timing, not scanout or GPU completion. The shared scene defines the '
              'judged metric; other columns are supporting measurements, not independent regression claims. '
              'The configured high-cap scenes use the shared ceiling; report it with the settings rather than calling a reached cap uncapped.',
              'Both pairs must agree and the smallest paired difference must exceed the sum of both repeat ranges; '
              'the shared paired classifier must also confirm the bad band. No historical percentage threshold is invented.',
              'Every raw attempt, rejected run, shader activity log and picture remains local. Images need visual '
              'review before citation; no automatic claim of scene correctness is made from a loading gap alone.',
              'No timer, push, tracker post, Discord post, or automatic hunt was started.', '',
              f"Raw: `{out}`. Database: `{LOCAL / 'results.sqlite'}`."]
    report = ROOT / 'reports' / (record['cycle'] + '.md')
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('\n'.join(lines) + '\n')
    put(out / 'cycle.json', record)
    put(ROOT / 'latest.json', {'cycle': record['cycle'], 'status': record['status'], 'report': str(report)})
    return report


def retain_cycles(config):
    """Keep seven calendar dates of owned rolling raw/copies; retain SQLite and reference values."""
    folders = sorted((p for p in (ROOT / 'raw').iterdir()
                      if p.is_dir() and re.fullmatch(r'\d{8}T\d{6}', p.name)), reverse=True)
    dates = sorted({p.name[:8] for p in folders}, reverse=True)[:7]
    active = processes()
    for folder in folders:
        if folder.name[:8] in dates or not (folder / 'cycle.json').is_file():
            continue
        record = json.loads((folder / 'cycle.json').read_text())
        copies = [Path(record[k]['copy']) for k in ('build', 'previous') if record.get(k, {}).get('copy')]
        owned = [p for p in copies if str(p).startswith(config['copy_prefix'] + folder.name) and
                 (text(p / '.nightly-owned').strip() == folder.name or text(p / '.rolling-owned').strip() == p.name)]
        if any(Path(proc['exe']).is_relative_to(p) for proc in active for p in owned):
            continue
        for p in owned:
            shutil.rmtree(p)
        shutil.rmtree(folder)


def cycle(config, *, automatic=False, resume=None, release_baseline=False):
    ROOT.mkdir(parents=True, exist_ok=True)
    if not config.get('enabled'):
        raise RuntimeError('rolling mode has not been authorized in private configuration')
    with (LOCAL / 'nightly.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another nightly or rolling cycle owns the lock')
        state = json.loads((ROOT / 'state.json').read_text()) if (ROOT / 'state.json').exists() else {}
        if release_baseline:
            release = config['release_baseline']
            ready = ROOT / 'release-baseline.json'
            if ready.is_file() and json.loads(ready.read_text()).get('complete'):
                return {'status': 'baseline-ready', 'report': json.loads(ready.read_text())['report']}
            head = release['commit']
            cycle_id = 'baseline-' + head[:8]
            out = ROOT / 'baselines' / cycle_id
            out.mkdir(parents=True, exist_ok=True)
            start = json.loads((out / 'build.json').read_text())['started'] if (out / 'build.json').exists() else stamp()
            put(out / 'pin.json', {'schema_version': 1, 'ref': release['name'], 'commit': head,
                                   'started': start, 'cycle': cycle_id, 'mode': 'release-baseline', 'fetches': 0})
        elif resume:
            if not re.fullmatch(r'\d{8}T\d{6}', resume):
                raise ValueError('invalid cycle id')
            out = ROOT / 'raw' / resume
            pin = json.loads((out / 'pin.json').read_text())
            head, start, cycle_id = pin['commit'], pin['started'], pin['cycle']
        else:
            now = time.time()
            if automatic and not full_rebuild_due(state, now) and state.get('last_started') and now - datetime.fromisoformat(state['last_started']).timestamp() < 10800:
                answer = {'status': 'not-due', 'checked': stamp(), 'last_started': state['last_started']}
                put(ROOT / 'check.json', answer)
                return answer
            fetched = capture(['git', '-C', config['repo'], 'fetch', 'origin', 'main'])
            head = capture(['git', '-C', config['repo'], 'rev-parse', 'origin/main']).strip()
            assert re.fullmatch('[0-9a-f]{40}', head)
            decision = target_decision(head, state.get('last_completed'), state.get('last_started'), now, automatic,
                                       full_due=full_rebuild_due(state, now))
            if decision not in ('run', 'full-rebuild'):
                answer = {'status': decision, 'commit': head, 'checked': stamp()}
                put(ROOT / 'check.json', answer)
                print(decision, flush=True)
                return answer
            start, cycle_id = stamp(), stamp('+%Y%m%dT%H%M%S')
            out = ROOT / 'raw' / cycle_id
            out.mkdir(parents=True, exist_ok=False)
            (out / 'fetch.log').write_text(fetched)
            put(out / 'pin.json', {'schema_version': 1, 'ref': 'origin/main', 'commit': head,
                                   'started': start, 'resolved_at': start, 'fetches': 1, 'cycle': cycle_id})
        if not release_baseline:
            begin_main_cycle(state, start)
            put(ROOT / 'state.json', state)
        selected_build_config = config['release_baseline']['build_config'] if release_baseline else config['build_config']
        build_config = json.loads(Path(selected_build_config).read_text())
        if not release_baseline and not (out / 'build.json').exists():
            build_config.update(fresh_build=True,
                work_dir=str(Path(build_config['worktree']) / 'work' / 'full-cycles' / cycle_id),
                build_copy=build_config['build_copy'] + '-' + cycle_id)
        record = json.loads((out / 'cycle.json').read_text()) if (out / 'cycle.json').exists() else {
            'cycle': cycle_id, 'commit': head, 'started': start, 'holds': [], 'scenes': [], 'status': 'building'}
        record['mode'] = 'release-baseline' if release_baseline else 'main'
        if record['status'] in ('complete', 'complete-unjudged', 'regression'):
            answer = {'status': record['status'], 'cycle': cycle_id, 'commit': head,
                      'report': str(ROOT / 'reports' / (cycle_id + '.md')), 'already_completed': True}
            print(json.dumps(answer), flush=True)
            return answer
        record.pop('error', None)
        store = Store(LOCAL / 'results.sqlite')
        try:
            build_path = out / 'build.json'
            if build_path.exists():
                build = json.loads(build_path.read_text())
                if build.get('status') != 'passed' or build['commit'] != head:
                    raise RuntimeError('existing build is incomplete or failed; evidence preserved, no duplicate build started')
            else:
                build = builder.run(build_config, out, head)
            build['source_stamp'] = source_stamp(build, out)
            record['build'] = build
            if not release_baseline:
                while state['next_full_due_epoch'] <= time.time():
                    state['next_full_due_epoch'] += 21600
                state['last_full_build_started'] = build.get('build_command_started', build['started'])
                put(ROOT / 'state.json', state)
            if not store.db.execute('SELECT 1 FROM nights WHERE id=?', ('rolling-' + cycle_id,)).fetchone():
                store.begin('rolling-' + cycle_id, build)
            engine, adapter = shared_tools(config)
            previous_file = out / 'previous.json'
            if previous_file.exists():
                previous = json.loads(previous_file.read_text())
            else:
                previous = state.get('last_completed')
                if release_baseline and not previous:
                    raise RuntimeError('release measurement requires a completed main cycle for its paired arm')
                if not previous:
                    previous = json.loads(Path(config['first_reference_manifest']).read_text())
                    previous['source_stamp'] = re.findall(r'^version: .*\(source ([0-9a-f]+)\)',
                                                         text(config['first_reference_build_log']), re.M)[0]
            record['previous'] = previous
            previous['commit'] = previous.get('commit', previous.get('base'))
            record['references'] = fixed_references(config, record['scenes'], adapter)
            write_report(config, out, record)
            templates = [(scene, json.loads(Path(scene['template']).read_text())) for scene in config['scenes']]
            if len(templates) != 3:
                raise RuntimeError('rolling authorization is for exactly three configured scenes/arms')
            scenarios = out / 'scenarios'
            scenarios.mkdir(exist_ok=True)
            prepared_marker = out / 'prepared.json'
            if not prepared_marker.exists():
                cached = compatible_cache(config, previous, build)
                if cached and not previous_file.exists():
                    previous = snapshot_reference(previous, Path(build['copy'] + '-previous'))
                    put(previous_file, previous)
                    record['previous'] = previous
                record['cache_reuse'] = reuse_cache(previous, build) if cached else []
                prep = scene_for(templates[0][1], 'prepare', build['source_stamp'], timing=False)
                prep.update(seconds=90 if cached else 440, grace_seconds=15 if cached else 25)
                prep['measure'] = {'name': 'prepared', 'direction': 'pass-fail'}
                prep['run'].update(prepare=True, input='', readback_csv=False, expect_log=['source ' + build['source_stamp']], forbid_log=[])
                prep['run'].pop('user', None)
                prep_path = scenarios / 'prepare.json'; put(prep_path, prep)
                entries = [{'role': 'candidate', 'label': 'prepare', 'copy': build['copy'], 'commit': head,
                            'output': str(out / 'runs/prepare'), 'scenario': str(prep_path)}]
                cases = [prep]
                if cached:
                    for scene, template in templates:
                        case = scene_for(template, 'warm-' + scene['id'], build['source_stamp'], timing=False)
                        path = scenarios / ('warm-' + scene['id'] + '.json'); put(path, case)
                        entries.append({'role': 'candidate', 'label': case['name'], 'copy': build['copy'], 'commit': head,
                                        'output': str(out / 'runs' / case['name']), 'scenario': str(path)})
                        cases.append(case)
                path = make_plan(config, out, 'prepare-warm' if cached else 'prepare', entries, cases, False)
                rows, hold = run_hold(config, build_config, path, False)
                record['holds'].append(hold)
                if len(rows) != len(entries) or any(not r['result'].get('valid') for r in rows):
                    raise RuntimeError('shader preparation/warm-up did not finish; no timing is counted')
                put(prepared_marker, {'combined_warmup': cached, 'at': stamp()})
                record['status'] = 'prepared'; write_report(config, out, record)
            prepared = json.loads(prepared_marker.read_text())
            if not prepared['combined_warmup'] and not (out / 'warmed.json').exists():
                entries, cases = [], []
                for scene, template in templates:
                    case = scene_for(template, 'warm-' + scene['id'], build['source_stamp'], timing=False)
                    path = scenarios / ('warm-' + scene['id'] + '.json'); put(path, case)
                    entries.append({'role': 'candidate', 'label': case['name'], 'copy': build['copy'], 'commit': head,
                                    'output': str(out / 'runs' / case['name']), 'scenario': str(path)})
                    cases.append(case)
                path = make_plan(config, out, 'warm', entries, cases, False)
                rows, hold = run_hold(config, build_config, path, False)
                record['holds'].append(hold)
                if len(rows) != 3 or any(not r['result'].get('valid') for r in rows):
                    raise RuntimeError('unmeasured warm-up incomplete; no timing is counted')
                put(out / 'warmed.json', {'at': stamp()})
                record['status'] = 'warmed'; write_report(config, out, record)
            if not previous_file.exists():
                previous = snapshot_reference(previous, Path(build['copy'] + '-previous'))
                put(previous_file, previous)
                record['previous'] = previous
            for prior in record['scenes']:
                if prior['decision']['state'] == 'regression' and not release_baseline:
                    if not prior.get('handoff'):
                        prior['handoff'] = handoff(config, build_config, out, prior, prior['decision'], previous, build)
                    record['status'] = 'regression'
            completed_scenes = {s['id'] for s in record['scenes']}
            for scene, template in templates:
                if record['status'] == 'regression':
                    break
                if scene['id'] in completed_scenes:
                    continue
                entries, cases = [], []
                candidate, reference = (previous, build) if release_baseline else (build, previous)
                for index, (role, binary) in enumerate((('candidate', candidate), ('reference', reference),
                                                        ('candidate', candidate), ('reference', reference))):
                    label = ('new' if role == 'candidate' else 'release' if release_baseline else 'previous') + '-' + scene['reference_suffix']
                    case = scene_for(template, label, binary['source_stamp'])
                    path = scenarios / (scene['id'] + '-' + str(index+1) + '.json'); put(path, case)
                    entries.append({'role': role, 'label': label, 'copy': binary['copy'], 'commit': binary['commit'],
                                    'output': str(out / 'runs' / (scene['id'] + '-' + str(index+1))), 'scenario': str(path)})
                    cases.append(case)
                path = make_plan(config, out, scene['id'], entries, cases, True)
                rows, hold = run_hold(config, build_config, path, True)
                record['holds'].append(hold)
                record_rows(store, cycle_id, scene['id'], rows, adapter)
                decision = compare_paired(rows, cases[0], engine)
                found = {'id': scene['id'], 'scenario': cases[0], 'runs': rows, 'decision': decision}
                record['scenes'].append(found)
                record['references'] = fixed_references(config, record['scenes'], adapter)
                if decision['state'] == 'regression' and not release_baseline:
                    if previous['commit'] != build['commit']:
                        found['handoff'] = handoff(config, build_config, out, found, decision, previous, build)
                    else:
                        found['same_revision_difference'] = True
                    record['status'] = 'regression'
                    break
                record['status'] = 'measuring'; write_report(config, out, record)
            if release_baseline:
                base_scenes = []
                for scene in record['scenes']:
                    base_runs = [r for r in scene['runs'] if r['role'] == 'reference']
                    eligible = len(base_runs) == 2 and all(r['result'].get('valid') and not r['result'].get('disturbed')
                                                               and not r['result'].get('kernel_errors') for r in base_runs)
                    values = [r['result']['value'] for r in base_runs]
                    eligible = eligible and engine.repeats_agree(values, 0.05)
                    base_scenes.append({'id': scene['id'], 'baseline_runs': base_runs, 'eligible': eligible})
                complete = len(base_scenes) == 3 and all(s['eligible'] for s in base_scenes)
                record['status'] = 'baseline-complete' if complete else 'baseline-incomplete'
                put(ROOT / 'release-baseline.json', {'complete': complete, 'build': build, 'scenes': base_scenes,
                    'measured_at': stamp(), 'report': str(ROOT / 'reports' / (cycle_id + '.md'))})
            else:
                if record['status'] != 'regression':
                    record['status'] = 'complete' if all(s['decision']['state'] != 'not-judged' for s in record['scenes']) else 'complete-unjudged'
                state['last_completed'] = build
                state['last_completed']['cycle'] = cycle_id
                put(ROOT / 'state.json', state)
        except Exception as error:
            record.update(status='stopped', error=type(error).__name__ + ': ' + str(error))
        finally:
            record['ended'] = stamp()
            if record.get('build'):
                store.end('rolling-' + cycle_id, record['status'], record['ended'])
            store.db.close()
        report = write_report(config, out, record)
        from report import tracking_draft
        baseline_file = ROOT / 'release-baseline.json'
        baseline = json.loads(baseline_file.read_text()) if baseline_file.exists() else None
        draft = tracking_draft({**config, 'drafts': build_config['drafts']}, record, out, baseline)
        retain_cycles(build_config)
        answer = {'status': record['status'], 'cycle': cycle_id, 'commit': head, 'report': str(report), 'draft': str(draft)}
        (ROOT / 'outbox').mkdir(parents=True, exist_ok=True)
        put(ROOT / 'outbox' / (cycle_id + '.json'), answer)
        print(json.dumps(answer, indent=2), flush=True)
        return answer


def wait_draft():
    """Block on one local completion event; the agent, never this program, sends the IRC notice."""
    import ctypes
    outbox, delivered = ROOT / 'outbox', ROOT / 'delivered'
    outbox.mkdir(parents=True, exist_ok=True)
    delivered.mkdir(parents=True, exist_ok=True)
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.inotify_init1(os.O_CLOEXEC)
    if fd < 0:
        raise OSError(ctypes.get_errno(), 'inotify_init1')
    try:
        if libc.inotify_add_watch(fd, os.fsencode(outbox), 0x80 | 0x8) < 0:
            raise OSError(ctypes.get_errno(), 'inotify_add_watch')
        while True:
            pending = [p for p in sorted(outbox.glob('*.json')) if not (delivered / p.name).exists()]
            if pending:
                value = json.loads(pending[0].read_text())
                put(delivered / pending[0].name, {'received': stamp(), 'event': str(pending[0])})
                print(json.dumps(value), flush=True)
                return 0
            os.read(fd, 65536)
    finally:
        os.close(fd)


def main(args):
    config = json.loads((LOCAL / 'rolling.json').read_text())
    if args.wait_draft:
        return wait_draft()
    if args.watch:
        baseline = ROOT / 'release-baseline.json'
        if not baseline.is_file() or not json.loads(baseline.read_text()).get('complete'):
            raise RuntimeError('initialize all release-baseline scenes before starting the watch')
        print('rolling watch ready; moved main after three hours; mandatory fresh rebuild every six hours; no timer', flush=True)
        while True:
            result = cycle(config, automatic=True)
            if result['status'] in ('regression', 'stopped'):
                return 1
            threading.Event().wait(300)
    result = cycle(config, automatic=args.check, resume=args.resume_cycle, release_baseline=args.baseline)
    return 1 if result['status'] in ('regression', 'stopped', 'baseline-incomplete') else 0
