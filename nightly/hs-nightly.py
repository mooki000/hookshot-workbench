#!/usr/bin/env python3
"""Build the published nightly, run game-free checks and private scenarios, retain evidence. Never post."""
from pathlib import Path
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build as builder
import compare
import report as reporting
import scenario
from common import HARN, LOCAL, capture, command, env_clean, gated, put, slug, stamp, text
from metrics import parse_tests
from store import Store
from hs_names import log_path


def catalogue_scenarios(config):
    catalogue = json.loads((HARN / 'saves/catalogue.json').read_text())
    template = json.loads((LOCAL / 'catalogue-template.json').read_text())
    folder = LOCAL / 'scenarios'
    folder.mkdir(parents=True, exist_ok=True)
    for name, entry in catalogue.items():
        assert '/' not in name and slug(name) == name
        decoded = entry.get('decoded', {})
        slots = decoded.get('slots', [])
        newest = next((s for s in slots if s.get('slot') == decoded.get('newest')), {})
        spec = {**template, 'id': 'catalogue-' + name, 'save': name, 'expected_map': newest.get('area'),
                'description': entry.get('point', name), 'catalogue_entry': name}
        put(folder / (spec['id'] + '.json'), spec)
    return catalogue


def suite(config, out):
    before = stamp()
    start = time.monotonic()
    rc = command(gated([HARN / 'hs-idle.sh', config['python'], '-I', '-B', 'tests/run_all.py']),
                 out / 'tests.log', cwd=config['worktree'], env=env_clean({'RX_CPM': config['cpm']}), guard=config)
    if rc == 125:
        raise scenario.SafetyStop('game-free suite stopped by watchdog; see tests-safety/safety.json')
    values = parse_tests(text(out / 'tests.log'))
    put(out / 'tests.json', {'started': before, 'ended': stamp(), 'elapsed_including_gate_s': time.monotonic()-start,
                            'exit': rc, 'tests': values, 'load_after': list(os.getloadavg())})
    # Deliberate crash fixtures must not become desktop core notifications.
    p = subprocess.run(['coredumpctl', 'list', '--since', before, '--no-pager'], capture_output=True, text=True)
    (out / 'test-period-cores.txt').write_text(p.stdout + p.stderr)
    return values


def comparisons(store, night, current, history):
    current['comparisons'] = {}
    findings = []
    if current['status'] == 'safety-stop' or current.get('cut_short'):
        return []
    logfile = log_path(current['run_dir'])
    log = str(logfile) if logfile is not None else None
    if current['status'] != 'passed' and history:
        findings.append({'scenario': current['id'], 'kind': 'passed-now-fails', 'error': current.get('error'),
                         'summary': current['summary'], 'log': log})
    if current.get('fatal_lines'):
        findings.append({'scenario': current['id'], 'kind': 'crash-or-device-fault', 'lines': current['fatal_lines'],
                         'log': log})
    for metric, record in current['metrics'].items():
        past = [{'value': r['metrics'][metric]['value'], 'disturbed': r['disturbed']}
                for r in history if metric in r['metrics']]
        decision = compare.compare_metric(record['value'], past, kind=record['kind'],
                                          direction=record['direction'], disturbed=current['disturbed'])
        current['comparisons'][metric] = decision
        if decision['state'] == 'regression':
            findings.append({'scenario': current['id'], 'kind': 'metric', 'metric': metric, **decision})
    # Missing versions are legacy keys, not keys made by the currently loaded code.
    signature_history = [r['signatures'] for r in history
                         if r.get('signature_version', 1) == current.get('signature_version', 1)]
    for finding in compare.compare_signatures(current['signatures'], signature_history):
        findings.append({'scenario': current['id'], 'kind': 'log-signature', **finding,
                         'log': log})
    for name, image in current['images'].items():
        past = [r['images'][name]['features'] for r in history if name in r['images']]
        result = compare.compare_image(image['features'], past)
        if result['state'] == 'regression':
            findings.append({'scenario': current['id'], 'kind': 'picture', 'picture': name, **result,
                             'evidence': str(Path(current['evidence_dir']) / 'shots' / (name + '.png'))})
    return findings


def retain(config, store):
    nights = sorted((p.name for p in (LOCAL / 'raw').iterdir()
                     if p.is_dir() and (p / '.nightly-raw').is_file()), reverse=True)
    for night in nights[7:]:
        raw = LOCAL / 'raw' / night
        copy = Path(config['copy_prefix'] + night)
        # Only owned snapshot and its explicitly named raw directory, never arbitrary inputs.
        if copy.is_dir() and text(copy / '.nightly-owned').strip() == night:
            shutil.rmtree(copy)
        if raw.is_dir() and raw.parent == LOCAL / 'raw':
            shutil.rmtree(raw)


def arm(config, calendar=None):
    if not config.get('timer_enabled', False):
        raise RuntimeError('timer is disabled; enabling it requires the user’s explicit word')
    calendar = calendar or capture(['date', '-d', 'tomorrow ' + config['timer_time'], '+%Y-%m-%d %H:%M:%S']).strip()
    unit = 'ls-nightly-' + slug(stamp('+%Y%m%d-%H%M%S'))
    argv = ['systemd-run', '--user', '--unit=' + unit, '--on-calendar=' + calendar,
            '--timer-property=AccuracySec=60s', '--collect', config['python'], '-I', '-B',
            str(Path(__file__).resolve()), 'run', '--rearm']
    answer = capture(argv)
    put(LOCAL / 'timer.json', {'at': stamp(), 'calendar': calendar, 'unit': unit, 'command': argv, 'result': answer})
    print(answer)


def run(config, args):
    LOCAL.mkdir(parents=True, exist_ok=True)
    with (LOCAL / 'nightly.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('another nightly owns the lock; nothing started')
        return run_locked(config, args)


def run_locked(config, args):
    night = args.night or stamp('+%F')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:-[a-zA-Z0-9_-]+)?', night):
        raise ValueError('invalid night id')
    out = LOCAL / 'raw' / night
    out.mkdir(parents=True, exist_ok=True)
    (out / '.nightly-raw').write_text(night + '\n')
    state = {'night': night, 'at': stamp(), 'stage': 'build'}
    put(LOCAL / 'state.json', state)
    game_free = args.game_free or not config.get('gpu_scenarios_enabled', False)
    if args.resume_build or args.resume:
        build = json.loads((out / 'build.json').read_text())
        if build['status'] != 'passed':
            raise RuntimeError('resume-build needs a completed full build')
        assert capture(['git', '-C', config['worktree'], 'rev-parse', 'HEAD']).strip() == build['commit']
    else:
        try:
            build = builder.run(config, out)
        except Exception as failure:
            build = json.loads((out / 'build.json').read_text()) if (out / 'build.json').exists() else {
                'commit': 'not resolved', 'status': 'failed'}
            error = type(failure).__name__ + ': ' + str(failure)
            path = reporting.report(LOCAL, night, build, {}, {}, [], [], 'build-stopped', error)
            put(LOCAL / 'state.json', {**state, 'at': stamp(), 'stage': 'build-stopped',
                                     'clean': False, 'error': error, 'report': str(path)})
            raise
    store = Store(LOCAL / 'results.sqlite')
    existing = store.db.execute('SELECT commit_sha,tests_json FROM nights WHERE id=?', (night,)).fetchone()
    if existing:
        if not args.resume or existing[0] != build['commit']:
            raise RuntimeError('night already exists; use --resume for its same completed build')
        store.db.execute('UPDATE nights SET status=?,ended=NULL WHERE id=?', ('running', night))
        store.db.commit()
    else:
        store.begin(night, build)
    results = []
    findings = [json.loads(row[0]) for row in store.db.execute('SELECT evidence_json FROM findings WHERE night=?', (night,))]
    completed = {name: json.loads(body) for name, body in store.db.execute(
        'SELECT name,result_json FROM scenarios WHERE night=? AND status IN (?,?)', (night, 'passed', 'failed'))}
    tests, test_diff = {}, {}
    clean, error = False, None
    try:
        from monitor import wait_for_player_idle
        put(LOCAL / 'state.json', {**state, 'at': stamp(), 'stage': 'waiting for two player-idle minutes'})
        wait_for_player_idle(config, out / 'player-idle.jsonl')
        put(LOCAL / 'state.json', {**state, 'at': stamp(), 'stage': 'game-free suite'})
        if existing and existing[1]:
            tests = json.loads(existing[1])
        else:
            if (out / 'tests.log').exists():
                archive = out / 'attempts' / ('interrupted-suite-' + stamp('+%Y%m%d-%H%M%S'))
                archive.mkdir(parents=True, exist_ok=False)
                for name in ('tests.log', 'tests-safety', 'tests.json', 'test-period-cores.txt', 'first-ready.json'):
                    if (out / name).exists():
                        (out / name).rename(archive / name)
            tests = suite(config, out)
        test_diff = compare.compare_tests(tests, store.previous_suite(night))
        store.suite(night, tests)
        previous = store.db.execute('SELECT commit_sha FROM nights WHERE id<? AND tests_json IS NOT NULL ORDER BY id DESC LIMIT 1',
                                    (night,)).fetchone()
        for name in test_diff['new_failures']:
            if any(f.get('kind') == 'new-test-failure' and f.get('scenario') == name for f in findings):
                continue
            finding = {'scenario': name, 'kind': 'new-test-failure', 'result': tests[name],
                       'log': str(out / 'tests.log'), 'hardware': capture(['uname', '-sr']).strip()}
            spec = {'id': name, 'description': 'Game-free check ' + name, 'form': 'build',
                    'issue_query': Path(name).stem.replace('test_', '').replace('_', ' '),
                    'affected_paths': [name], 'settings': {}}
            reporting.enrich(config, night, build, finding, spec,
                             {'commit': previous[0]} if previous else None,
                             out / 'findings' / ('test-' + slug(name)))
            findings.append(finding)
            store.finding(night, name, finding['kind'], finding)
        if game_free:
            specs = []
        elif args.resume and (out / 'plan.json').exists():
            specs = json.loads((out / 'plan.json').read_text())
        else:
            catalogue = catalogue_scenarios(config)
            # Preserve the catalogue used, without adding another copy of any save file.
            put(out / 'catalogue-index.json', {k: {'point': v.get('point'), 'decoded': v.get('decoded')} for k, v in catalogue.items()})
            specs = [json.loads(p.read_text()) for p in sorted((LOCAL / 'scenarios').glob('*.json'))]
            specs = [s for s in specs if s.get('enabled', True)]
            specs.sort(key=lambda s: (s.get('order', 50), s['id']))
            put(out / 'plan.json', specs)
        for index, spec in enumerate(specs):
            if spec['id'] in completed:
                results.append(completed[spec['id']])
                continue
            put(LOCAL / 'state.json', {**state, 'at': stamp(), 'stage': 'scenario', 'scenario': spec['id'], 'index': index})
            evidence = out / 'scenarios' / spec['id']
            tag = 'nt-' + night.replace('-', '') + '-' + str(index)
            previous_attempts = []
            if evidence.exists():
                archive = out / 'attempts' / (spec['id'] + '-' + stamp('+%Y%m%d-%H%M%S'))
                archive.parent.mkdir(parents=True, exist_ok=True)
                evidence.rename(archive)
                previous_attempts.append(str(archive))
                tag += '-a' + str(len(list((out / 'attempts').iterdir())))
            current = scenario.run(config, build['copy'], spec, evidence, tag)
            current['evidence_dir'] = str(evidence)
            current['previous_attempts'] = previous_attempts
            history = store.previous(night, current)
            found = comparisons(store, night, current, history)
            if any(v['state'] == 'needs-rerun' for v in current['comparisons'].values()):
                # ONE complete rerun only; retain both attempts regardless of the answer.
                again = scenario.run(config, build['copy'], spec, out / 'confirmations' / spec['id'], tag + '-r1')
                current['confirmation'] = {'evidence': str(out / 'confirmations' / spec['id']), 'status': again['status']}
                for metric, decision in current['comparisons'].items():
                    if decision['state'] != 'needs-rerun':
                        continue
                    record = current['metrics'][metric]
                    past = [{'value': r['metrics'][metric]['value'], 'disturbed': r['disturbed']}
                            for r in history if metric in r['metrics']]
                    decision = compare.compare_metric(record['value'], past, kind=record['kind'], direction=record['direction'],
                                                      rerun=again['metrics'].get(metric, {}).get('value'),
                                                      rerun_disturbed=again['disturbed'] or again['status'] != 'passed')
                    current['comparisons'][metric] = decision
                    if decision['state'] == 'regression':
                        found.append({'scenario': spec['id'], 'kind': 'confirmed-metric', 'metric': metric, **decision})
                if again['status'] == 'safety-stop':
                    current['status'] = 'safety-stop'
                    current['safety_faults'] = again['safety_faults']
            for count, finding in enumerate(found):
                finding['device_lines'] = current.get('device_lines', [])
                finding['hardware'] = capture(['uname', '-sr']).strip()
                logfile = log_path(current['run_dir'])
                finding.setdefault('log', str(logfile) if logfile is not None else None)
                reporting.enrich(config, night, build, finding, spec, history[-1] if history else None,
                                 out / 'findings' / (spec['id'] + '-' + str(count)))
                store.finding(night, spec['id'], finding['kind'], finding)
            findings.extend(found)
            results.append(current)
            store.record(night, current, current['status'] == 'passed' and not found)
            put(evidence / 'result.json', current)
            store.export_baseline(LOCAL / 'baselines/latest.json')
            reporting.report(LOCAL, night, build, tests, test_diff, results, findings, 'running')
            if len(results) == 2:
                put(out / 'first-ready.json', {'at': stamp(), 'commit': build['commit'],
                                               'scenarios': [{k: r.get(k) for k in ('id', 'status', 'evidence_dir', 'summary')}
                                                             for r in results],
                                               'suite_counts': dict(__import__('collections').Counter(t['status'] for t in tests.values()))})
            if current['status'] == 'safety-stop':
                raise scenario.SafetyStop('; '.join(current['safety_faults']))
        unknown = [name for name, t in tests.items() if t['status'] in ('FAIL', 'TIMEOUT')
                   and name not in config.get('known_test_failures', [])]
        clean = bool(results) and all(r['status'] == 'passed' for r in results) and not unknown and not findings
        suite_ok = not unknown and not findings
    except Exception as failure:
        error = type(failure).__name__ + ': ' + str(failure)
    status = 'complete-clean' if clean else 'complete-with-findings' if not error else 'stopped'
    if game_free and not error:
        status = 'complete-game-free' if suite_ok else 'complete-game-free-with-failures'
    store.end(night, status, stamp())
    store.export_baseline(LOCAL / 'baselines/latest.json')
    path = reporting.report(LOCAL, night, build, tests, test_diff, results, findings, status, error)
    reporting.discord(config, night, build, results, findings)
    put(LOCAL / 'state.json', {'night': night, 'at': stamp(), 'stage': status, 'error': error,
                             'report': str(path), 'clean': clean, 'scenarios': len(results)})
    if not (out / 'first-ready.json').exists():
        put(out / 'first-ready.json', {'at': stamp(), 'status': status, 'error': error, 'scenarios': len(results)})
    retain(config, store)
    store.db.close()
    if clean and args.rearm:
        arm(config)
    print(json.dumps({'night': night, 'status': status, 'report': str(path), 'error': error}, indent=2), flush=True)
    return 0 if clean or (game_free and not error and suite_ok) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('run')
    p.add_argument('--night')
    p.add_argument('--resume-build', action='store_true', help='reuse only this night’s completed full build')
    p.add_argument('--resume', action='store_true', help='continue an interrupted night without repeating its build, complete suite or completed scenarios')
    p.add_argument('--game-free', action='store_true', help='build and complete suite only; never launch a GPU scenario or timer')
    p.add_argument('--rearm', action='store_true', help='approved scheduled run: arm the next night only after clean completion')
    p = sub.add_parser('rolling', help='pin main, reuse the full builder and shared paired benchmark protocol')
    group = p.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true', help='one cadence check; run only after three hours and a changed main')
    group.add_argument('--watch', action='store_true', help='managed process, not a timer; check every five minutes')
    group.add_argument('--resume-cycle', help='continue this immutable cycle without rebuilding or repeating completed holds')
    group.add_argument('--baseline', action='store_true', help='initialize the fixed release baseline once with the shared paired protocol')
    group.add_argument('--wait-draft', action='store_true', help='block for one local completed-cycle draft event; posts nothing')
    p = sub.add_parser('arm')
    p.add_argument('--at')
    p.add_argument('--user-authorized', action='store_true', required=True, help='the user explicitly asked to enable this optional timer')
    args = parser.parse_args()
    config = json.loads((LOCAL / 'config.json').read_text())
    if args.command == 'rolling':
        import rolling
        return rolling.main(args)
    if args.command == 'arm':
        state = json.loads((LOCAL / 'state.json').read_text())
        if not state.get('clean'):
            raise RuntimeError('last night did not end cleanly; timer not armed')
        arm(config, args.at)
        return 0
    return run(config, args)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print('hs-nightly:', type(error).__name__ + ':', str(error), file=sys.stderr)
        sys.exit(2)
