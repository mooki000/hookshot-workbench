#!/usr/bin/env python3
"""Find the change that made an LS build worse, from evidence only.

  regress.py plan --repo R --good A --bad B --output NEW_DIR
  regress.py hunt --repo R --good A --bad B --scenario LOCAL.json --output NEW_DIR --max-holds N
                  [--max-steps 9] [--max-hold-seconds 480] [--max-wall-seconds 5400]
                  [--exclusive-authorized] [--priority-authorized]
                  [--suspect SHA[,SHA]]... [--switch LABEL:NAME=VALUE[,NAME=VALUE]]... [--suspects-only]

plan lists the range with its pull request numbers and groups the commits whose lifted inputs are equal (one lift
serves a group). hunt confirms the two ends in one hold, tests named suspects on the bad commit (a revert, or a
switch of the bad build), then bisects. Every step is a hold of runs in turn (reference, candidate, reference,
candidate); a disturbed, invalid or in-between run never decides, the pair is run again while the caller's holds last.
The scenario and its build and run adapters are local JSON argv lists (never shell text): nothing of a game is here.

A timing run is quiet, and may decide, when over its measuring window everything except the game used under
`quiet_cores` processor cores on average (3) and no 5-second sample of that was above `quiet_peak_cores` (6); and a
hold's repeats of one arm must agree within `repeat_tolerance` (0.05) on the measure. The 1-minute load is kept as
information only: the game's own waiting threads raise it, more on one build than on another, so a load limit would
mark the slower build more often.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

SCHEMA = 1
GATE_VARS = ('HSGATE_SLOT_DIR', 'HSGATE_INTEL_SLOT_DIR', 'HSGATE_SLOT_TEST', 'HSGATE_SLOT_NODESKTOP', 'HSGATE_IN_GATE',
             'HSGATE_IN_INTEL_GATE')


def clean_env():
    return {k: v for k, v in os.environ.items() if k not in GATE_VARS}


def date():
    return subprocess.check_output(['date', '-Iseconds'], text=True).strip()


def save(path, data):
    path = Path(path); temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n'); temporary.replace(path)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def lift_key(repo, commit):
    """What a finished lift may be shared by: nx2/ without build.py, the vendored a64-kit, and the HLE names host/ defines
    (the lift hooks them; the rest of host/ is compiled after it)."""
    tree = git(repo, 'ls-tree', '-r', commit, '--', 'nx2', 'third_party/a64-kit').splitlines()
    tree = [x for x in tree if not x.endswith('\tnx2/build.py')]
    names = set()
    for f in git(repo, 'ls-tree', '--name-only', commit + ':host').splitlines():
        if not f.endswith('.c'):
            continue
        source = git(repo, 'show', commit + ':host/' + f)
        names.update(re.findall(r'^void (imp_\w+)\(Ctx', source, re.M))
        names.update('imp_' + n for n in re.findall(r'^HLE\((\w+)\)', source, re.M))
    content = '\n'.join(tree) + '\nHLE\n' + '\n'.join(sorted(names))
    return hashlib.sha256(content.encode()).hexdigest()


def groups(rows):
    out = []
    for i, row in enumerate(rows):
        if not out or out[-1]['lift_key'] != row['lift_key']:
            out.append({'lift_key': row['lift_key'], 'first': i, 'last': i, 'commits': []})
        out[-1]['last'] = i; out[-1]['commits'].append(row['commit'])
    return out


def history(repo, good, bad):
    good = git(repo, 'rev-parse', good + '^{commit}'); bad = git(repo, 'rev-parse', bad + '^{commit}')
    subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', good, bad], check=True)
    if good not in git(repo, 'rev-list', '--first-parent', bad).splitlines():
        raise ValueError("good must be on bad's first-parent history")
    commits = [good] + git(repo, 'rev-list', '--first-parent', '--reverse', good + '..' + bad).splitlines()
    rows, keys = [], {}
    for commit in commits:
        subject = git(repo, 'show', '-s', '--format=%s', commit)
        key = lift_key(repo, commit)
        rows.append({'commit': commit, 'subject': subject, 'prs': [int(n) for n in re.findall(r'#(\d+)', subject)],
                     'lift_key': key})
        keys[key] = True
    return {'schema_version': SCHEMA, 'good': good, 'bad': bad, 'commits': rows, 'groups': groups(rows),
            'distinct_lifts': len(keys), 'commits_in_range': len(rows) - 1, 'created': date()}


def number(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)


def classification(result, scenario):
    """good, bad, ambiguous (between the bands), disturbed or invalid: only good and bad can decide."""
    for key in ('valid', 'disturbed', 'kernel_errors', 'load_before', 'load_after', 'value', 'summary', 'evidence'):
        if key not in result:
            return 'invalid'
    if result['valid'] is not True or result['kernel_errors'] != 0:
        return 'invalid'
    if not isinstance(result['summary'], str) or not isinstance(result['evidence'], list) or not result['evidence']:
        return 'invalid'
    if not all(number(x) for x in (result['load_before'], result['load_after'])):   # (kept as information; never a limit)
        return 'invalid'
    if result['disturbed'] is not False:
        return 'disturbed'
    if scenario['kind'] == 'timing':   # (the quiet rule, from what everything else used in the measuring window)
        mean, peak = result.get('others_cores_window'), result.get('others_cores_peak')
        if not number(mean) or not number(peak):
            return 'invalid'
        if mean >= scenario.get('quiet_cores', 3) or peak > scenario.get('quiet_peak_cores', 6):
            return 'disturbed'
    measure = scenario['measure']; value = result['value']
    if measure['direction'] == 'pass-fail':
        return ('good' if value else 'bad') if isinstance(value, bool) else 'invalid'
    if not number(value):
        return 'invalid'
    worse = value if measure['direction'] == 'higher-worse' else -value
    if worse <= measure['good_max']:
        return 'good'
    if worse >= measure['bad_min']:
        return 'bad'
    return 'ambiguous'


def repeats_agree(values, tolerance):
    """One arm's runs of a hold within the tolerance of each other on the judged measure (pass/fail: all the same)."""
    if not values or any(isinstance(v, bool) for v in values):
        return len(set(values)) == 1
    lo, hi = min(values), max(values)
    if lo == hi:
        return True
    return lo > 0 and hi / lo - 1 <= tolerance


def paired_decision(runs, scenario, reference):
    """The candidate's class when every run of the hold is clean, the reference is where it should be and each arm's
    repeats agree; else retry."""
    n = scenario.get('repetitions', 2)
    if len(runs) != 2 * n:
        return 'retry'
    a = [classification(x['result'], scenario) for x in runs if x['role'] == 'reference']
    b = [classification(x['result'], scenario) for x in runs if x['role'] == 'candidate']
    if len(a) != n or len(b) != n or any(x != reference for x in a) or any(x not in ('good', 'bad') for x in b):
        return 'retry'
    for role in ('reference', 'candidate'):
        if not repeats_agree([x['result']['value'] for x in runs if x['role'] == role], scenario.get('repeat_tolerance', 0.05)):
            return 'retry'
    if all(x == 'good' for x in b):
        return 'good'
    if all(x == 'bad' for x in b):
        return 'bad'
    return 'retry'


def choose_candidate(rows, lo, hi, available):
    """The next commit to test: the middle of the open range, among those a finished lift already serves if any."""
    candidates = list(range(lo + 1, hi))
    if not candidates:
        return None
    cached = [i for i in candidates if rows[i]['lift_key'] in available]
    middle = (lo + hi) / 2
    return min(cached or candidates, key=lambda i: (abs(i - middle), i))


def run_command(argv, log, timeout=None, env=None, cwd=None):
    with Path(log).open('w') as f:
        process = subprocess.Popen(argv, stdout=f, stderr=subprocess.STDOUT, env=env, cwd=cwd, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt, InterruptedError):
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL); process.wait()
            raise
        if code:
            raise RuntimeError(f'command exited {code}; see {log}')


def avoid_delay(now, window, periodic):
    """Seconds to wait before a run starts so that its measuring window (window: seconds after the run's start) misses
    every known periodic disturbance of the machine: [{'every': seconds, 'at': seconds into each period of the clock,
    'for': seconds}] (a desktop job that runs every quarter of an hour, say). 0 when the window is clear."""
    delay = 0.0
    for _ in range(4):   # (a delay can move the window onto the next disturbance)
        moved = False
        for p in periodic:
            lo, hi = now + delay + window[0], now + delay + window[1]
            first = lo - ((lo - p['at']) % p['every'])   # (the last one that began at or before the window's start)
            for mark in (first, first + p['every']):
                if mark + p.get('for', 6) > lo and mark < hi:
                    delay = mark + p.get('for', 6) - now - window[0]
                    moved = True
        if not moved:
            break
    return max(0.0, delay)


def busy_cores(seconds):
    """Processor cores in use on the whole machine, a mean over the next `seconds`."""
    def read():
        with open('/proc/stat') as f:
            v = [int(x) for x in f.readline().split()[1:9]]
        return sum(v) - v[3] - v[4], time.monotonic()
    a, ta = read(); time.sleep(seconds); b, tb = read()
    return (b - a) / os.sysconf('SC_CLK_TCK') / (tb - ta)


def hold(plan_path):
    """The worker inside one hold: the plan's runs in the order written, never past the hold's limit. A run that is not
    valid ends the hold at once (the pair cannot decide any more: the card goes back). Spare seconds, and only those,
    may be spent before the first run letting the machine settle: until a 5-second sample of everything's processor
    use is under quiet_cores (settle_seconds)."""
    plan_path = Path(plan_path); plan = json.loads(plan_path.read_text())
    limit, seconds, grace, entries = plan['max_hold_seconds'], plan['seconds'], plan['grace_seconds'], plan['entries']
    if not 10 < limit <= 480 or seconds <= 0 or grace < 5:
        raise ValueError('invalid held-worker duration or reserve')
    spare = limit - 10 - len(entries) * (seconds + grace)
    if spare < 0:
        raise ValueError('run plan does not fit its hard hold limit')
    # (inside the gate of the device its plan is for: the card's, or with `gate: intel` the other device's own)
    marker = 'HSGATE_IN_INTEL_GATE' if plan.get('gate') == 'intel' else 'HSGATE_IN_GATE'
    if plan['kind'] != 'check' and not os.environ.get(marker):
        raise ValueError('held worker is outside the gate its plan is for (' + marker + ' is not set)')

    def interrupted(signum, frame):
        raise InterruptedError('held worker stopped at its limit or by a signal')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    signal.setitimer(signal.ITIMER_REAL, limit - 10)
    begin = time.monotonic(); runs = []; results = plan_path.with_suffix('.runs.json')
    record = {'started': date(), 'load_at_start': os.getloadavg()[0], 'settle_cores': [], 'avoid_delays': []}
    try:
        until = begin + min(plan.get('settle_seconds', 0), spare)
        while until - time.monotonic() > 0.2:
            record['settle_cores'].append(round(busy_cores(min(5.0, until - time.monotonic())), 2))
            if record['settle_cores'][-1] < plan.get('quiet_cores', 3):
                break
        record.update(settled_seconds=round(time.monotonic() - begin, 1), load_at_first_run=os.getloadavg()[0])
        save(plan_path.with_suffix('.hold.json'), record)
        for index, entry in enumerate(entries):
            left = limit - (time.monotonic() - begin)
            if left < seconds + grace:
                break
            # (a known periodic disturbance in this run's measuring window: start later, if the hold has the seconds)
            wait = avoid_delay(time.time(), plan.get('window', [0, seconds]), plan.get('avoid', []))
            if 0 < wait <= left - (len(entries) - index) * (seconds + grace):
                record['avoid_delays'].append(round(wait, 1))
                time.sleep(wait)
                left = limit - (time.monotonic() - begin)
            out = Path(entry['output']); out.mkdir(parents=True)
            subst = entry | {'seconds': str(seconds), 'scenario': entry.get('scenario', plan['scenario'])}
            argv = [part.format_map(subst) for part in plan['run_command']]
            try:
                run_command(argv, out / 'adapter.log', timeout=min(left - 2, seconds + grace), env=os.environ.copy())
                result = json.loads((out / 'result.json').read_text())
            except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as exc:
                result = {'valid': False, 'disturbed': True, 'kernel_errors': None, 'kernel_status': 'unknown',
                          'load_before': os.getloadavg()[0], 'load_after': os.getloadavg()[0], 'value': None,
                          'summary': str(exc), 'evidence': [str(out)]}
                save(out / 'result.json', result)
            runs.append({'role': entry['role'], 'label': entry.get('label', entry['role']), 'commit': entry['commit'],
                         'copy': entry['copy'], 'output': str(out), 'result': result})
            save(results, runs)
            if result.get('valid') is not True or result.get('kernel_errors', 0) or result.get('fatal', 0):
                break
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        save(results, runs)
        record.update(ended=date(), seconds_used=round(time.monotonic() - begin, 1), runs=len(runs))
        save(plan_path.with_suffix('.hold.json'), record)


class Hunt:
    def __init__(self, args, scenario, range_):
        self.args = args; self.scenario = scenario; self.range = range_; self.rows = range_['commits']
        self.output = Path(args.output)
        self.steps = []; self.built = {}; self.donors = {}; self.holds = 0; self.used_steps = 0; self.suspects = []
        self.lo = 0; self.hi = len(self.rows) - 1; self.started = getattr(args, 'started', time.monotonic())

    def remaining_time(self):
        return self.args.max_wall_seconds - (time.monotonic() - self.started)

    def budget(self):
        if self.remaining_time() <= 0:
            raise RuntimeError('caller wall-time budget exhausted')
        if self.holds >= self.args.max_holds:
            raise RuntimeError('caller hold budget exhausted')

    def persist(self):
        save(self.output / 'steps.json', self.steps)
        lines = ['| Step | Role | Build | Value | Class | Others: cores in the window (highest 5 s) | Load before -> after | Summary |',
                 '|---|---|---|---:|---|---|---|---|']
        for step in self.steps:
            for run in step['runs']:
                r = run['result']; summary = str(r.get('summary', '')).replace('|', '/').replace('\n', ' ')
                lines.append(f"| {step['name']} (hold {step['hold']}: {step['decision']}) | {run['role']} | "
                             f"{run['commit'][:12]} | {r.get('value')} | {classification(r, self.scenario)} | "
                             f"{r.get('others_cores_window')} ({r.get('others_cores_peak')}) | "
                             f"{r.get('load_before')} -> {r.get('load_after')} | {summary} |")
        (self.output / 'table.md').write_text('\n'.join(lines) + '\n')
        self.result('inconclusive', 'hunt in progress')

    def result(self, status, reason, first=None):
        data = {'schema_version': SCHEMA, 'status': status, 'first_bad': first, 'good': self.rows[self.lo]['commit'],
                'bad': self.rows[self.hi]['commit'], 'remaining': [r['commit'] for r in self.rows[self.lo + 1:self.hi + 1]],
                'reason': reason, 'holds_used': self.holds, 'steps_used': self.used_steps, 'suspects': self.suspects,
                'evidence': [str(self.output / x) for x in ('range.json', 'steps.json', 'table.md')], 'updated': date()}
        save(self.output / 'result.json', data)
        return data

    def build(self, index, reverts=()):
        row = self.rows[index]; key = (row['commit'], tuple(reverts))
        if key in self.built:
            return self.built[key]
        if self.remaining_time() <= 0:
            raise RuntimeError('caller wall-time budget exhausted before a build')
        label = row['commit'][:12] + ('rev' + hashlib.sha256(','.join(reverts).encode()).hexdigest()[:8] if reverts else '')
        out = self.output / 'builds' / label; out.mkdir(parents=True)
        root = Path(self.scenario.get('worktree_root', str(self.output / 'worktrees'))).resolve(); root.mkdir(parents=True, exist_ok=True)
        work = root / (self.output.name + '-' + label)
        run_command(['git', '-C', self.args.repo, 'worktree', 'add', '--detach', str(work), row['commit']], out / 'worktree.log',
                    timeout=self.remaining_time(), env=clean_env())
        for rev in reverts:
            run_command(['git', 'revert', '--no-commit', rev], out / ('revert-' + rev[:12] + '.log'), timeout=self.remaining_time(),
                        env=clean_env(), cwd=work)
        actual_key = row['lift_key']   # (a revert outside the lifted inputs shares the bad commit's lift; another one lifts again)
        if reverts and git(work, 'diff', 'HEAD', '--', 'nx2', ':(exclude)nx2/build.py', 'third_party/a64-kit', 'host'):
            actual_key += '-revert-' + hashlib.sha256(','.join(reverts).encode()).hexdigest()[:12]
        donor = self.donors.get(actual_key, '')
        substitutions = {'commit': row['commit'], 'worktree': str(work), 'output': str(out), 'lift_key': actual_key,
                         'donor_worktree': donor, 'build_kind': 'fast' if donor else 'full', 'reverts': ','.join(reverts)}
        save(out / 'request.json', substitutions)
        run_command([x.format_map(substitutions) for x in self.scenario['build_command']], out / 'adapter.log',
                    timeout=self.remaining_time(), env=clean_env(), cwd=work)
        build = json.loads((out / 'build.json').read_text())
        if build.get('commit') != row['commit'] or build.get('lift_key') != actual_key or not Path(build.get('copy', '')).is_dir():
            raise ValueError('build adapter returned a mismatched identity')
        build.update(worktree=str(work)); self.built[key] = build; self.donors[actual_key] = str(work)
        return build

    def compare(self, name, reference, candidate, reference_class, candidate_scenario=None):
        for retry in range(self.scenario.get('max_retries', 1) + 1):
            self.budget(); self.holds += 1
            entries = []
            for repetition in range(self.scenario.get('repetitions', 2)):
                for role, b in (('reference', reference), ('candidate', candidate)):
                    entry = {'role': role, 'copy': b['copy'], 'commit': b['commit'],
                             'output': str(self.output / f'hold-{self.holds:02d}' / f'{repetition + 1}-{role}')}
                    if role == 'candidate' and candidate_scenario:
                        entry['scenario'] = candidate_scenario
                    entries.append(entry)
            plan = {'kind': self.scenario['kind'], 'entries': entries, 'run_command': self.scenario['run_command'],
                    'scenario': str(Path(self.args.scenario).resolve()), 'seconds': self.scenario['seconds'],
                    'max_hold_seconds': self.args.max_hold_seconds, 'grace_seconds': self.scenario.get('grace_seconds', 25),
                    'settle_seconds': self.scenario.get('settle_seconds', 0), 'quiet_cores': self.scenario.get('quiet_cores', 3)}
            plan_path = self.output / f'hold-{self.holds:02d}.json'; save(plan_path, plan)
            argv = [sys.executable, '-I', str(Path(__file__).resolve()), '_hold', str(plan_path)]
            if self.scenario['kind'] != 'check':
                gate = self.scenario.get('gpu_gate', str(Path.home() / 'Games/hs-gpu-slot'))
                flags = ['--exclusive'] if self.scenario['kind'] == 'timing' else []
                if self.args.priority_authorized:
                    flags = ['--priority'] + flags
                argv = [gate, *flags, 'timeout', '--signal=TERM', '--kill-after=5', str(self.args.max_hold_seconds - 5), *argv]
            try:
                run_command(argv, self.output / f'hold-{self.holds:02d}.log', timeout=self.remaining_time(), env=clean_env())
            finally:
                result_path = plan_path.with_suffix('.runs.json')
                runs = json.loads(result_path.read_text()) if result_path.exists() else []
                decision = paired_decision(runs, self.scenario, reference_class)
                self.steps.append({'name': name, 'retry': retry, 'hold': self.holds, 'runs': runs, 'decision': decision})
                self.persist()
            if any(r['result'].get('kernel_errors') != 0 or r['result'].get('fatal', 0) for r in runs):
                raise RuntimeError('GPU/game safety fault or unknown kernel coverage; no more runs')
            if decision != 'retry':
                return decision
        raise RuntimeError(name + ': disturbed, invalid, in-between, disagreeing or reference-drift pairs used up the retries')

    def switch_scenario(self, spec):
        label, _, pairs = spec.partition(':')
        env = dict(p.split('=', 1) for p in pairs.split(','))
        derived = json.loads(json.dumps(self.scenario))
        derived.setdefault('run', {}).setdefault('env', {}).update(env)
        path = self.output / ('switch-' + re.sub(r'[^A-Za-z0-9._-]', '_', label) + '.json')
        save(path, derived)
        return label, env, str(path)

    def execute(self):
        if self.hi == 0:
            return self.result('not_reproduced', 'the two ends are the same commit')
        try:
            good = self.build(0); bad = self.build(self.hi)
            if self.compare('endpoints', good, bad, 'good') != 'bad':
                return self.result('not_reproduced', 'the paired ends did not show the regression the bands describe')
            inside = [r['commit'] for r in self.rows[1:]]
            for suspect in self.args.suspect:
                commits = [git(self.args.repo, 'rev-parse', s + '^{commit}') for s in suspect.split(',')]
                if any(c not in inside for c in commits):
                    raise ValueError('suspect outside good..bad')
                outcome = self.compare('revert ' + ','.join(c[:8] for c in commits), bad, self.build(self.hi, commits), 'bad')
                self.suspects.append({'reverts': commits, 'decision': outcome}); self.persist()
            for spec in self.args.switch:
                label, env, path = self.switch_scenario(spec)
                outcome = self.compare('switch ' + label, bad, bad, 'bad', path)
                self.suspects.append({'switch': label, 'env': env, 'decision': outcome}); self.persist()
            if self.args.suspects_only:   # (what a revert or a switch does on the bad commit is not the first bad commit)
                explained = any(s['decision'] == 'good' for s in self.suspects)
                return self.result('suspect_explains' if explained else 'inconclusive',
                                   'named suspects compared on the bad commit; the history was not bisected')
            while self.hi - self.lo > 1:
                if self.used_steps >= self.args.max_steps:
                    return self.result('inconclusive', 'bisection step allowance used up')
                self.budget()
                index = choose_candidate(self.rows, self.lo, self.hi, self.donors)
                candidate = self.build(index); self.used_steps += 1
                if self.compare('bisect ' + str(self.used_steps), good, candidate, 'good') == 'good':
                    self.lo = index
                else:
                    self.hi = index
                self.persist()
            return self.result('first_bad', 'adjacent tested good and bad commits; assumes one change of the measure in the range',
                               self.rows[self.hi]['commit'])
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
            return self.result('inconclusive', str(exc) or type(exc).__name__)


def validate(s, args):
    if s.get('schema_version') != SCHEMA:
        raise ValueError('unsupported scenario schema')
    if s['kind'] not in ('timing', 'game-check', 'check'):
        raise ValueError('unknown scenario kind')
    if s['kind'] == 'timing' and not args.exclusive_authorized:
        raise ValueError('timing requires explicit --exclusive-authorized')
    if s['measure']['direction'] not in ('higher-worse', 'lower-worse', 'pass-fail'):
        raise ValueError('unknown measure direction')
    if s['kind'] != 'timing' and s['measure']['direction'] != 'pass-fail':
        raise ValueError('numeric decisions require timing holds')
    if s['measure']['direction'] != 'pass-fail':
        a, b = s['measure']['good_max'], s['measure']['bad_min']
        if not (number(a) and number(b)) or not a < b:
            raise ValueError('good and bad bands must be finite and must not overlap')
    quiet, peak, tolerance = s.get('quiet_cores', 3), s.get('quiet_peak_cores', 6), s.get('repeat_tolerance', 0.05)
    if not (number(quiet) and number(peak) and number(tolerance)) or not 0 < quiet <= peak or not 0 < tolerance < 1:
        raise ValueError('the quiet rule needs 0 < quiet_cores <= quiet_peak_cores and 0 < repeat_tolerance < 1')
    if not 0 < args.max_hold_seconds <= 480:
        raise ValueError('a hold is at most 480 seconds')
    if min(args.max_holds, args.max_steps, args.max_wall_seconds) <= 0:
        raise ValueError('budgets must be positive')
    if s.get('repetitions', 2) < 2 or s.get('max_retries', 1) < 0:
        raise ValueError('at least two repetitions, and a retry count of zero or more')
    if s['seconds'] <= 0 or 2 * s.get('repetitions', 2) * (s['seconds'] + s.get('grace_seconds', 25)) + 10 > args.max_hold_seconds:
        raise ValueError('the paired runs and their reserve do not fit the allowed hold')
    for key in ('build_command', 'run_command'):
        if not isinstance(s[key], list) or not s[key] or not all(isinstance(x, str) for x in s[key]):
            raise ValueError(key + ' must be an argv list')
    for spec in getattr(args, 'switch', []):
        label, _, pairs = spec.partition(':')
        if not label or not pairs or not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', p) for p in pairs.split(',')):
            raise ValueError('a switch is LABEL:NAME=VALUE[,NAME=VALUE]')


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '_hold':
        hold(sys.argv[2]); return
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('command', choices=['plan', 'hunt'])
    for key in ('repo', 'good', 'bad', 'output'):
        p.add_argument('--' + key, required=True)
    p.add_argument('--scenario'); p.add_argument('--max-steps', type=int, default=9)
    p.add_argument('--max-holds', type=int, default=1); p.add_argument('--max-hold-seconds', type=int, default=480)
    p.add_argument('--max-wall-seconds', type=int, default=5400)
    p.add_argument('--exclusive-authorized', action='store_true'); p.add_argument('--priority-authorized', action='store_true')
    p.add_argument('--suspect', action='append', default=[]); p.add_argument('--switch', action='append', default=[])
    p.add_argument('--suspects-only', action='store_true')
    a = p.parse_args(); a.repo = str(Path(a.repo).resolve()); a.output = str(Path(a.output).resolve())
    a.started = time.monotonic()
    s = None
    if a.command == 'hunt':
        if not a.scenario:
            p.error('hunt needs --scenario')
        s = json.loads(Path(a.scenario).read_text()); validate(s, a)
    Path(a.output).mkdir(parents=True, exist_ok=False)
    r = history(a.repo, a.good, a.bad); save(Path(a.output) / 'range.json', r)
    if a.command == 'plan':
        print(json.dumps(r, indent=2)); return
    save(Path(a.output) / 'scenario.json', s)
    print(json.dumps(Hunt(a, s, r).execute(), indent=2))


if __name__ == '__main__':
    main()
