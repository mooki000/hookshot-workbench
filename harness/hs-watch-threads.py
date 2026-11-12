#!/usr/bin/env python3
"""Waits until someone other than the user comments on, reviews, merges or closes one of his threads on
NotRexed/Hookshot, then prints what is new and exits. Read-only gh calls; the first round at once, then one every 150 s.
Threads: every issue and pull request he authored, plus the numbers given as arguments (threads he commented on).

It remembers where it stopped (a state file beside this script: the time up to which it has looked, the comments and
reviews already reported, each thread's state), so nothing that happens between one run's end and the next one's start
is missed: the next run's first round reports it. Before this, a run looked only from its own start, and three
comments that arrived between two runs were found twelve minutes late (2026-11-11).

    hs-watch-threads.py [--since <ISO time>] [--state <file>] [numbers...]

--since sets the time to look from for this run (otherwise the state file's, or now when there is none)."""
import datetime
import json
import os
import subprocess
import sys
import time

REPO = 'NotRexed/Hookshot'
ME = 'mooki000'
args = sys.argv[1:]
since_arg = state_path = None
while args and args[0].startswith('--'):
    if args[0] == '--since': since_arg = args[1]
    elif args[0] == '--state': state_path = args[1]
    else: sys.exit(__doc__)
    args = args[2:]
extra = {int(a) for a in args if a.isdigit()}
state_path = state_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'watch-threads.state.json')


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def gh(*a):
    r = subprocess.run(['gh', *a], capture_output=True, text=True, timeout=60)
    return r.stdout if r.returncode == 0 else ''


def mine():
    """number -> [state, is a pull request, merged at] of every thread the user authored (None: no answer)"""
    out = gh('api', f'search/issues?q=repo:{REPO}+author:{ME}&per_page=100')
    try:
        return {str(i['number']): [i['state'], bool(i.get('pull_request')), i.get('pull_request', {}).get('merged_at')]
                for i in json.loads(out)['items']}
    except Exception:
        return None


try:
    state = json.load(open(state_path))
except Exception:
    state = {}
since = since_arg or state.get('since') or utc_now()
seen = set(state.get('seen', []))
base = state.get('threads')
while base is None:
    base = mine()
    if base is None: time.sleep(30)

first = True
while True:
    if not first: time.sleep(150)
    first = False
    round_at = utc_now()
    news, new_ids = [], []
    ok = True
    try:
        for kind in ('issues/comments', 'pulls/comments'):
            out = gh('api', f'repos/{REPO}/{kind}?since={since}&per_page=100&sort=created&direction=desc')
            if not out: ok = False
            for c in (json.loads(out) if out else []):
                url = c.get('issue_url') or c.get('pull_request_url') or ''
                n = url.rstrip('/').split('/')[-1] if url else '0'
                key = f"c{c['id']}"
                if c['user']['login'] == ME or c['created_at'] < since or key in seen: continue
                if n in base or int(n) in extra:
                    news.append(f"#{n} {kind.split('/')[0]} comment by {c['user']['login']} at {c['created_at']}: {c['body'][:600]!r}")
                    new_ids.append(key)
        now = mine()
        if now:
            for n, (st, is_pr, merged) in now.items():
                old = base.get(n)
                if old and (old[0] != st or old[2] != merged):
                    news.append(f"#{n} is now {st}{' (merged ' + merged + ')' if merged else ''}")
            for n, (st, is_pr, merged) in now.items():
                if is_pr and st == 'open':
                    out = gh('api', f'repos/{REPO}/pulls/{n}/reviews')
                    for r in (json.loads(out) if out else []):
                        key = f"r{r['id']}"
                        if r['user']['login'] != ME and (r.get('submitted_at') or '') >= since and key not in seen:
                            news.append(f"#{n} review ({r['state']}) by {r['user']['login']} at {r['submitted_at']}: {(r.get('body') or '')[:500]!r}")
                            new_ids.append(key)
            base = now
        else:
            ok = False
    except Exception as e:
        ok = False
        if 'rate limit' in str(e).lower(): news.append(f'watcher error: {e!r}')
    if ok:
        # looked at everything up to this round's start: the next look begins a minute before it (GitHub lists a new
        # comment a little late), and what was reported is remembered so the overlap is not reported twice
        seen.update(new_ids)
        at = datetime.datetime.strptime(round_at, '%Y-%m-%dT%H:%M:%SZ') - datetime.timedelta(seconds=60)
        since = max(since, at.strftime('%Y-%m-%dT%H:%M:%SZ'))
        tmp = state_path + '.tmp'
        json.dump({'since': since, 'seen': sorted(seen)[-800:], 'threads': base}, open(tmp, 'w'))
        os.replace(tmp, state_path)
    if news:
        print(f"THREAD NEWS (looked from {since_arg or state.get('since') or 'this start'} to {round_at}):")
        for x in dict.fromkeys(news): print(' -', x)
        break
