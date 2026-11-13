#!/usr/bin/env python3
"""Read PR heads/reviews and failed job logs. Never rerun, post, push or approve CI.

Main comparison is the latest completed run per workflow among the last 40 main
runs, pinned to its real SHA. An identical failing name is a same-test observation,
not proof of the same cause. Unavailable logs/names remain UNKNOWN, never green.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

BAD = {'FAILURE', 'ERROR', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED', 'STARTUP_FAILURE', 'STALE'}
ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))')
RACE = 'test_nightly_two_runs_at_once_end_at_the_newer_commit'


def clean(text):
    return ''.join(c for c in ANSI.sub('', text) if c in '\n\t' or ord(c) >= 32)


def failures(text):
    """Both streaming/table run_all forms, unittest cases, and pytest node ids."""
    text = clean(text)
    tests = set(re.findall(r'\b(?:FAIL|TIMEOUT)\s+(?:[\d.]+s\s+)?((?:tests|nx2|gpu)/[^\s:]+)', text))
    tests.update(re.findall(r'\bFAILED\s+((?:tests|nx2|gpu)/\S+)', text))
    cases = set(re.findall(r'\b(?:FAIL|ERROR):\s+(test_\w+)\s+\(([^)]+)\)', text))
    return {'tests': sorted(tests), 'cases': sorted(name + ' (' + cls + ')' for name, cls in cases),
            'release_race': RACE in text and 'SHA256SUMS still differs from the files after 5 uploads' in text}


class Reader:
    def __init__(self, repo, out=None):
        self.repo, self.out, self.cache = repo, out, {}

    def gh(self, *args, raw=False):
        key = tuple(args)
        if key not in self.cache:
            r = subprocess.run(['gh', *args], capture_output=True, text=True, timeout=90)
            if r.returncode:
                raise RuntimeError(clean(r.stderr).strip())
            self.cache[key] = clean(r.stdout) if raw else json.loads(r.stdout)
        return self.cache[key]

    def log(self, job):
        text = self.gh('api', 'repos/' + self.repo + '/actions/jobs/' + str(job) + '/logs', '--allow-escape-sequences', raw=True)
        if self.out:
            (self.out / ('job-' + str(job) + '.log')).write_text(text)
        return failures(text)

    def main_failures(self):
        runs = self.gh('api', 'repos/' + self.repo + '/actions/runs?branch=main&status=completed&per_page=40')['workflow_runs']
        seen, evidence, unavailable = set(), {}, []
        for run in runs:
            key = run['workflow_id']
            if key in seen:
                continue
            seen.add(key)
            pages = self.gh('api', '--paginate', '--slurp', 'repos/' + self.repo + '/actions/runs/' + str(run['id']) + '/jobs?per_page=100')
            for job in (j for page in pages for j in page['jobs']):
                if (job.get('conclusion') or '').upper() not in BAD:
                    continue
                try:
                    parsed = self.log(job['id'])
                    for name in parsed['tests'] + parsed['cases']:
                        evidence.setdefault((job['name'], name), []).append({'sha': run['head_sha'], 'url': job['html_url']})
                except RuntimeError as exc:
                    unavailable.append(job['html_url'] + ': ' + str(exc))
        return evidence, unavailable

    def pull(self, number, main):
        pr = self.gh('pr', 'view', str(number), '--repo', self.repo, '--json', 'number,headRefOid,baseRefOid,reviewDecision,state,statusCheckRollup')
        rows = []
        for check in pr['statusCheckRollup']:
            state = (check.get('conclusion') or check.get('state') or check.get('status') or 'UNKNOWN').upper()
            if state not in BAD:
                continue
            url = check.get('detailsUrl') or check.get('targetUrl') or ''
            row = {'name': check.get('name') or check.get('context'), 'state': state, 'url': url}
            match = re.search(r'/jobs?/(\d+)', url)
            try:
                if not match:
                    raise RuntimeError('no Actions job id; external check/approval or unavailable job log')
                row.update(self.log(match[1]))
                row['also_main'] = {name: main[(row['name'], name)] for name in row['tests'] + row['cases'] if (row['name'], name) in main}
                if not row['tests'] and not row['cases']:
                    row['unknown'] = 'log read; no recognized failing test names (build/infrastructure failure may have no tests)'
            except RuntimeError as exc:
                row['unknown'] = str(exc)
            rows.append(row)
        pr['failed_checks'] = rows
        return pr


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('prs', type=int, nargs='+')
    p.add_argument('--repo', default='NotRexed/Hookshot')
    p.add_argument('--output', type=Path, help='new evidence directory, never overwritten')
    a = p.parse_args()
    if a.output:
        a.output.mkdir(parents=True, exist_ok=False)
    reader = Reader(a.repo, a.output)
    unavailable, main_errors = [], []
    try:
        baseline, main_errors = reader.main_failures()
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        baseline, main_errors = {}, [str(exc)]
    results = []
    for number in a.prs:
        try:
            pr = reader.pull(number, baseline)
            results.append(pr)
            print('#%s %s head=%s base=%s review=%s' % (pr['number'], pr['state'], pr['headRefOid'], pr['baseRefOid'], pr['reviewDecision'] or 'NONE'))
            if not pr['failed_checks']:
                pending = [c.get('name', c.get('context', '?')) for c in pr['statusCheckRollup'] if (c.get('status') not in (None, 'COMPLETED') or c.get('state') in ('PENDING', 'EXPECTED'))]
                print('  No failed checks; ' + ('pending: ' + ', '.join(pending) if pending else '%d checks reported' % len(pr['statusCheckRollup'])))
            for row in pr['failed_checks']:
                print('  %s %s %s' % (row['state'], row['name'], row['url']))
                for name in row.get('tests', []) + row.get('cases', []):
                    print('    ' + name)
                    for match in row.get('also_main', {}).get(name, []):
                        print('      ALSO FAILS ON MAIN (same test/job, cause not established): ' + match['sha'] + ' ' + match['url'])
                if row.get('release_race'):
                    print('    KNOWN INTERMITTENT RELEASE-SCRIPT RACE: concurrent nightly checksum publication; not excused or rerun')
                if row.get('unknown'):
                    print('    UNKNOWN: ' + row['unknown'])
                    unavailable.append(row['url'])
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            unavailable.append(str(number))
            print('#%d UNKNOWN: %s' % (number, exc))
    print('Main comparison: latest completed run per workflow among last 40 main runs; no matching failure is NOT proof main passed that test.')
    for error in main_errors:
        print('MAIN UNKNOWN:', error)
    if a.output:
        (a.output / 'report.json').write_text(json.dumps({'prs': results, 'main_errors': main_errors, 'unknown': unavailable}, indent=2) + '\n')
    return 2 if unavailable or main_errors else 0


if __name__ == '__main__':
    sys.exit(main())
