"""Adapter to the shared regression hunter; no second bisect or build implementation."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HARN, LOCAL, gated, put, text
from monitor import driver_faults
import scenario


def handoff(config, spec, good, build, finding, out):
    tool = HARN / 'regress/regress.py'
    build_adapter = HARN / 'regress/build_case.py'
    destination = out / 'hunt-scenario.json'
    measure = {'name': 'scenario_passed', 'direction': 'pass-fail'}
    kind = 'game-check'
    if finding['kind'] == 'confirmed-metric':
        metric = finding['metric']
        lower = good['metrics'][metric]['direction'] == 'lower'
        decision = finding
        a = decision['band']['low'] if lower else decision['band']['high']
        b = max(decision['value'], decision['rerun']) if lower else min(decision['value'], decision['rerun'])
        measure = {'name': metric, 'direction': 'lower-worse' if lower else 'higher-worse',
                   'good_max': -a if lower else a, 'bad_min': -b if lower else b}
        kind = 'timing'
    elif finding['kind'] != 'passed-now-fails':
        return {'state': 'not-decidable-by-boolean-adapter',
                'reason': 'log/image findings need their own deterministic validator before a hunt'}
    settings = {
        'schema_version': 1, 'name': spec['id'], 'kind': kind, 'measure': measure,
        'seconds': spec.get('hunt_seconds', spec['seconds']), 'repetitions': 2,
        'max_retries': 1, 'max_load': config['quiet_load_max'],
        'build': {'python': config['python'], 'cpm': config['cpm'],
                  'game_root': str(Path(config['build_copy']).parent), 'profile_dir': str(Path(config['worktree']) / 'work'),
                  'romfs': config['romfs'], 'exefs': config['exefs'], 'jobs': config['build_jobs'],
                  'build_gate': config['build_gate'], 'full_build_lock': config['full_build_lock'], 'lift_jobs': 8},
        'nightly': {'config': str(LOCAL / 'config.json'), 'scenario': spec, 'finding': finding,
                    'good_evidence': good.get('evidence_dir')},
        'build_command': [config['python'], '-I', '-B', str(build_adapter), '--worktree', '{worktree}',
                          '--commit', '{commit}', '--output', '{output}', '--lift-key', '{lift_key}',
                          '--donor-worktree', '{donor_worktree}', '--build-kind', '{build_kind}',
                          '--reverts', '{reverts}', '--scenario', str(destination)],
        'run_command': [config['python'], '-I', '-B', str(Path(__file__).resolve()),
                        '--copy', '{copy}', '--output', '{output}', '--seconds', '{seconds}',
                        '--commit', '{commit}', '--scenario', '{scenario}'],
    }
    if build.get('profile'):
        settings['build']['profile_files'] = list(build['profile'])
    put(destination, settings)
    command = [config['python'], '-I', '-B', str(tool), 'hunt', '--repo', config['repo'],
               '--good', good['commit'], '--bad', build['commit'], '--scenario', str(destination),
               '--output', str(out / 'hunt-result'), '--max-steps', '8', '--max-wall-seconds', '5400',
               '--max-holds', str(config.get('hunt_max_holds', 10)), '--max-hold-seconds', '480']
    result = {'scenario': str(destination), 'command': command, 'state': 'ready-not-launched'}
    if kind == 'timing':
        result['reason'] = 'requires run-specific exclusive authorization; nightly never grants it'
    elif not config.get('hunt_game_checks', False):
        result['reason'] = 'ordinary game-check hunting not enabled in private configuration'
    elif 4 * (settings['seconds'] + 25) > 480:
        result['reason'] = 'four endpoint runs plus termination/evidence reserve do not fit one eight-minute hold; supply a proven shorter scenario'
    elif not tool.is_file() or not build_adapter.is_file():
        result['reason'] = 'shared regression tool/build adapter not installed'
    else:
        with (out / 'hunt.log').open('w') as log:
            p = subprocess.run(gated(command), stdout=log, stderr=subprocess.STDOUT, timeout=5500)
        result.update(exit=p.returncode, state='completed')
        record = out / 'hunt-result/result.json'
        if record.is_file():
            result['result'] = json.loads(record.read_text())
        else:
            result.update(state='inconclusive', reason='shared tool returned without result.json; inspect hunt.log')
    put(out / 'hunt-handoff.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('copy', 'output', 'seconds', 'commit', 'scenario'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    hand = json.loads(Path(args.scenario).read_text())
    config = json.loads(Path(hand['nightly']['config']).read_text())
    spec = dict(hand['nightly']['scenario'])
    spec['seconds'] = int(args.seconds)
    out = Path(args.output)
    tag = 'hunt-' + hashlib.sha256(str(out).encode()).hexdigest()[:16]
    try:
        full = scenario.run(config, args.copy, spec, out / 'nightly', tag, held=True)
        safety = bool(full['safety_faults'])
        is_timing = hand['kind'] == 'timing'
        samples = [json.loads(line) for line in text(out / 'nightly/samples.jsonl').splitlines()]
        active = [s for s in samples if s.get('own_pids')]
        timing_busy = any(s['compiler_count'] or s['other_games'] or s['load'][0] > hand['max_load'] for s in active)
        if hand['measure']['direction'] == 'pass-fail':
            value = full['status'] == 'passed'
        else:
            value = full['metrics'].get(hand['measure']['name'], {}).get('value')
        result = {'value': value, 'valid': not safety and value is not None,
                  'disturbed': safety or (is_timing and timing_busy),
                  'load_before': active[0]['load'][0] if active else os.getloadavg()[0],
                  'load_after': active[-1]['load'][0] if active else os.getloadavg()[0],
                  'kernel_errors': len(driver_faults(text(out / 'nightly/kernel.txt').splitlines())),
                  'summary': full['summary'], 'evidence': [str(out / 'nightly/result.json')], 'commit': args.commit}
    except Exception as error:
        result = {'value': False, 'valid': False, 'disturbed': True, 'load_before': os.getloadavg()[0],
                  'load_after': os.getloadavg()[0], 'kernel_errors': 0, 'summary': str(error), 'evidence': [str(out)]}
    put(out / 'result.json', result)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
