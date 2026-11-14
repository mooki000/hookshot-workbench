"""Empirical comparisons. No game, network, or machine-specific data required."""
import math
import re
import statistics


SIGNATURE_VERSION = 2


def signature(line, *, roots=None):
    """Strip known POSIX root prefixes, not resource identities.

    roots maps stable labels to this run's absolute directories, longest first.
    Only whole prefixes at token starts (optionally quoted/bracketed or key=)
    match. No roots means no path removal; never guess a home or temporary path.
    Path/URL tokens, including quoted spaces, retain their suffixes and numbers.
    """
    line = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', line)
    prefixes = {str(path).rstrip('/'): name for name, path in (roots or {}).items()
                if str(path).startswith('/') and str(path).strip('/')}
    if prefixes:
        pattern = (r"""(?<!\S)((?:[A-Za-z_]\w*=)?[('"[{]*)(%s)"""
                   r"""(?=/|$|[\s'"]|[)\]},;:!?]+(?=\s|$|['"])|\.(?:\s|$))""") % '|'.join(
                       re.escape(path) for path in sorted(prefixes, key=len, reverse=True))
        line = re.sub(pattern, lambda m: m[1] + '<' + prefixes[m[2]] + '>', line)
    tokens = re.findall(r"""(?:[^\s"']|"[^"\n]*"|'[^'\n]*'|["'])+""", line)
    for i, token in enumerate(tokens):
        # Do not erase stable numeric/hex filenames, relative resources or URLs.
        if '/' in token or '\\' in token:
            continue
        token = re.sub(r'\b(?:0x[0-9a-f]+|[0-9a-f]{8,})\b', '<addr>', token, flags=re.I)
        tokens[i] = re.sub(r'(?<![\w])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?', '<n>', token)
    return ' '.join(tokens)


def band(values):
    """At least three independent nights, at most the most recent seven; no fixed % allowance.

    Half width is the larger of the observed maximum deviation and three robust sigma
    (sigma = 1.4826*MAD). Zero-spread data has a zero-width band; confirmation still applies.
    """
    values = list(values)[-7:]
    if len(values) < 3:
        return None
    if not all(math.isfinite(x) for x in values):
        raise ValueError('Non-finite baseline metric')
    mid = statistics.median(values)
    mad = statistics.median(abs(x - mid) for x in values)
    width = max(3 * 1.4826 * mad, max(abs(x - mid) for x in values))
    return {'n': len(values), 'median': mid, 'mad': mad, 'low': mid - width, 'high': mid + width}


def compare_metric(value, history, *, kind='timing', direction='higher', disturbed=False,
                   rerun=None, rerun_disturbed=False):
    eligible = [h['value'] for h in history if h.get('good', True) and
                not (kind in ('timing', 'memory') and h.get('disturbed', False))]
    limits = band(eligible)
    if kind in ('timing', 'memory') and disturbed:
        return {'state': 'not-judged', 'why': 'disturbed run', 'band': limits}
    if limits is None:
        return {'state': 'baseline-only', 'samples': len(eligible[-7:])}
    worse = (lambda x: x > limits['high']) if direction == 'higher' else (lambda x: x < limits['low'])
    better = (lambda x: x < limits['low']) if direction == 'higher' else (lambda x: x > limits['high'])
    if worse(value):
        if kind in ('timing', 'memory'):
            if rerun is None:
                state = 'needs-rerun'
            elif rerun_disturbed:
                state = 'not-judged'
            elif worse(rerun):
                state = 'regression'
            else:
                state = 'unconfirmed'
        else:
            state = 'regression'
    elif better(value):
        state = 'improvement'
    else:
        state = 'within-band'
    return {'state': state, 'value': value, 'band': limits, 'rerun': rerun}


def compare_signatures(current, histories):
    if not histories:
        return []
    findings = []
    known = set().union(*(h.keys() for h in histories[-7:]))
    for sig, count in sorted(current.items()):
        if sig not in known:
            findings.append({'signature': sig, 'state': 'new-signature', 'count': count})
        else:
            limits = band([h.get(sig, 0) for h in histories[-7:]])
            if limits and count > limits['high']:
                findings.append({'signature': sig, 'state': 'count-jump', 'count': count, 'band': limits})
    return findings


def compare_tests(now, before):
    if not before:
        return {'new_failures': [], 'vanished': [], 'slower': [], 'first_run': True}
    failures = sorted(k for k, v in now.items() if v['status'] in ('FAIL', 'TIMEOUT')
                      and before.get(k, {}).get('status') not in ('FAIL', 'TIMEOUT'))
    slower = [{'name': k, 'before_s': before[k]['seconds'], 'now_s': v['seconds'],
               'delta_s': v['seconds'] - before[k]['seconds']}
              for k, v in now.items() if k in before and v['status'] == before[k]['status'] == 'pass'
              and v['seconds'] > before[k]['seconds']]
    return {'new_failures': failures, 'vanished': sorted(set(before) - set(now)),
            'slower': sorted(slower, key=lambda v: -v['delta_s'])[:10], 'first_run': False}


def image_distance(a, b):
    if len(a) != len(b) or not a:
        raise ValueError('Image features differ in size or are empty')
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)) / 255


def compare_image(current, history):
    """Distance to the last good image, tolerance learned from pairwise good-night distances."""
    history = list(history)[-7:]
    if len(history) < 3:
        return {'state': 'baseline-only', 'samples': len(history)}
    distances = [image_distance(history[i], history[j]) for i in range(len(history)) for j in range(i)]
    # Unlike per-night metrics all pairwise distances belong to these same 3–7 nights.
    mid = statistics.median(distances)
    mad = statistics.median(abs(x - mid) for x in distances)
    tolerance = max(max(distances), mid + 3 * 1.4826 * mad)
    distance = image_distance(current, history[-1])
    return {'state': 'regression' if distance > tolerance else 'within-band',
            'distance': distance, 'tolerance': tolerance, 'nights': len(history)}


def compare_paired(runs, scenario, shared):
    """Rolling N/P/N/P: shared quiet/repeat rules, then a gap beyond both arms' spread."""
    if [r.get('role') for r in runs] != ['candidate', 'reference', 'candidate', 'reference']:
        return {'state': 'not-judged', 'reason': 'requires exactly new/previous/new/previous'}
    values = [r['result'].get('value') for r in runs]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
        return {'state': 'not-judged', 'reason': 'missing or non-finite judged metric'}
    direction = scenario['measure']['direction']
    if direction not in ('higher-worse', 'lower-worse'):
        raise ValueError('rolling comparisons need a numeric direction')
    sign = 1 if direction == 'higher-worse' else -1
    worse = [sign * v for v in values]
    # A neutral band exercises the shared validity/quiet checks without making a speed claim.
    ceiling = max(worse)
    neutral = {**scenario, 'measure': {**scenario['measure'], 'good_max': ceiling,
                                      'bad_min': math.nextafter(ceiling, math.inf)}}
    classes = [shared.classification(r['result'], neutral) for r in runs]
    if any(c != 'good' for c in classes):
        return {'state': 'not-judged', 'reason': 'shared validity/quiet rule', 'classes': classes}
    new, previous = [values[0], values[2]], [values[1], values[3]]
    tolerance = scenario.get('repeat_tolerance', 0.05)
    if not shared.repeats_agree(new, tolerance) or not shared.repeats_agree(previous, tolerance):
        return {'state': 'not-judged', 'reason': 'an arm differs by more than the repeat tolerance',
                'new': new, 'previous': previous, 'tolerance': tolerance}
    n, p = [sign*x for x in new], [sign*x for x in previous]
    gaps = [n[i] - p[i] for i in range(2)]
    spread = (max(n) - min(n)) + (max(p) - min(p))
    evidence = {'new': new, 'previous': previous, 'paired_gaps_worse_units': gaps,
                'combined_repeat_spread': spread, 'repeat_tolerance': tolerance}
    if min(gaps) > spread and min(n) > max(p):
        measure = {**scenario['measure'], 'good_max': max(p), 'bad_min': min(n)}
        verdict = shared.paired_decision(runs, {**scenario, 'measure': measure}, 'good')
        if verdict != 'bad':
            return {'state': 'not-judged', 'reason': 'shared paired decision did not confirm', **evidence}
        return {'state': 'regression', 'measure': measure, **evidence}
    if max(gaps) < -spread and max(n) < min(p):
        return {'state': 'improvement', **evidence}
    return {'state': 'within-repeat-spread', **evidence}
