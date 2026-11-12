"""Readers for preserved traces and sampled process data; unsupported sources stay missing."""
from collections import Counter, deque
from pathlib import Path
import csv
import importlib.util
import json
import math
import re
import statistics
from compare import signature
from common import text
from hs_names import log_path

WARNING = re.compile(r'\b(?:warn(?:ing)?|error|fatal|crash|failed|failure|unsupported|timeout)\b|VK_ERROR_|ran without a core', re.I)
ACTIVITY = re.compile(r'(?:pipeline|shader|program).*(?:compil|translat|built|pending|wait)|(?:compil|translat).*(?:pipeline|shader|program)', re.I)
VRAM = re.compile(r'vram|memory budget|texture cache|(?:device|card).*memory|memory.*(?:used|budget)', re.I)


def frame_stats(rows):
    rows = [(float(t), float(ms)) for t, ms in rows if ms > 0 and math.isfinite(ms)]
    if not rows:
        return {}
    assert all(rows[i][0] >= rows[i-1][0] for i in range(1, len(rows))), 'Nonmonotonic frame times'
    values = [ms for _, ms in rows]
    slow = sorted(values, reverse=True)
    result = {'count': len(values), 'mean_ms': statistics.fmean(values), 'median_ms': statistics.median(values),
              'mean_fps': 1000 / statistics.fmean(values), 'max_ms': max(values)}
    for pct in (1, 0.1):
        sample = slow[:max(1, math.ceil(len(values) * pct / 100))]
        result[f'low_{pct:g}_fps'] = 1000 / statistics.fmean(sample)
    for limit in (16.7, 25, 50, 100):
        over = [x for x in values if x > limit]
        result[f'over_{limit:g}_count'] = len(over)
        result[f'over_{limit:g}_longest_ms'] = max(over, default=0)
    # Fewest completions in a rolling second; a stall over one second has a zero-frame
    # window before it ends. This is not scanout or a GPU-completion measurement.
    spans = deque()
    worst_fps, worst_start = math.inf, None
    first = rows[0][0] - rows[0][1] / 1000
    for t, ms in rows:
        spans.append(t)
        if ms >= 1000:
            worst_fps, worst_start = 0, t - 1
        while spans and spans[0] <= t - 1:
            spans.popleft()
        if t - first >= 1 and len(spans) < worst_fps:
            worst_fps, worst_start = len(spans), t - 1
    result['worst_second_fps'] = worst_fps if worst_start is not None else None
    result['worst_second_start_s'] = worst_start
    return result


def frame_files(run, play_ns=None):
    run = Path(run)
    sources, missing, raw = {}, {}, {}
    for name, kind in (('present.csv', 'game_present'), ('frames.csv', 'renderer_submit')):
        path = run / name
        if not path.is_file() or path.stat().st_size == 0:
            missing[kind] = 'trace not written by this build/run'
            continue
        with path.open() as f:
            rows = list(csv.reader(f))
        values = []
        if kind == 'game_present':
            numeric = [r for r in rows[1:] if len(r) >= 3 and r[1].isdigit()]
            start_ns = int(numeric[0][1]) if numeric else 0
            values = [((int(r[1]) - start_ns) / 1e9, int(r[2]) / 1e6)
                      for r in numeric[1:] if play_ns is None or int(r[1]) >= play_ns]
            raw[kind] = [(int(r[1]) / 1e9, int(r[2]) / 1e6) for r in numeric[1:]]
        else:
            for r in rows[1:]:
                if len(r) == 3:
                    try:
                        values.append((float(r[1]), float(r[2])))
                    except ValueError:
                        continue
            # Renderer seconds are relative to its own first present, not the game's.
            # Do not invent a common zero. Preserve the complete renderer trace.
        sources[kind] = frame_stats(values)
    return sources, missing, raw


def slope(samples, key):
    pairs = [(s['monotonic_s'], s[key]) for s in samples if s.get(key) is not None]
    if len(pairs) < 3 or pairs[-1][0] - pairs[0][0] < 10:
        return None
    tx = statistics.fmean(t for t, _ in pairs)
    vy = statistics.fmean(v for _, v in pairs)
    return sum((t - tx) * (v - vy) for t, v in pairs) / sum((t - tx)**2 for t, _ in pairs) * 60


def parse_tests(log):
    table = log.rsplit('\nresult', 1)
    if len(table) != 2:
        raise ValueError('Suite has no complete result table')
    result = {}
    for line in table[1].splitlines()[1:]:
        m = re.match(r'^(pass|FAIL|TIMEOUT)\s+([\d.]+)s\s+(.+)$', line)
        if m:
            status, seconds, name = m.groups()
            result[name] = {'status': status, 'seconds': float(seconds)}
        else:
            m = re.match(r'^skip\s+(.+?)\s{2,}\((.*)\)$', line)
            if m:
                result[m[1]] = {'status': 'skip', 'seconds': 0, 'why': m[2]}
    totals = re.findall(r'(\d+) passed, (\d+) failed, (\d+) skipped in ([\d.]+) s', log)
    if not totals:
        raise ValueError('Suite has no totals')
    passed, failed, skipped, _ = totals[-1]
    counts = Counter(r['status'] for r in result.values())
    assert counts['pass'] == int(passed) and counts['FAIL'] + counts['TIMEOUT'] == int(failed)
    assert counts['skip'] == int(skipped)
    return result


def picture(path):
    from PIL import Image, ImageFilter, ImageStat
    with Image.open(path) as image:
        width, height = image.size
        reduced = image.convert('RGB').resize((64, 36)).filter(ImageFilter.GaussianBlur(1))
        gray = reduced.convert('L')
        stat = ImageStat.Stat(gray)
        lo, hi = gray.getextrema()
        return {'width': width, 'height': height, 'features': list(reduced.tobytes()),
                'luma_mean': stat.mean[0], 'luma_sd': stat.stddev[0],
                'black': hi <= 2, 'blank': hi - lo <= 2}


def system_telemetry(run, *, start=None, end=None):
    """Optional sampled context; load only the reader beside this harness, never run input."""
    if not (Path(run) / 'perf.csv').is_file():
        return None
    try:
        path = Path(__file__).resolve().parent.parent / 'perf' / 'hs_perf.py'
        spec = importlib.util.spec_from_file_location('hookshot_system_telemetry', path)
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
        return reader.attach(run, start=start, end=end)
    except Exception:
        # Optional instrumentation cannot invalidate the existing timing/safety evidence.
        return {'artifact': 'perf.csv', 'status': 'cannot_tell', 'error': 'reader_unavailable',
                'notice': 'Sampled context unavailable; existing decisions are unchanged.'}


def collect(run, evidence, metadata):
    run, evidence = Path(run), Path(evidence)
    log = text(log_path(run))
    lines = log.splitlines()
    signatures = Counter(signature(s) for s in lines if WARNING.search(s))
    samples = [json.loads(s) for s in text(evidence / 'samples.jsonl').splitlines() if s]
    samples = [s for s in samples if s.get('own_pids')]
    events = [json.loads(s) for s in text(evidence / 'log-events.jsonl').splitlines() if s]
    traces, missing, raw = frame_files(run, metadata.get('play_monotonic_ns'))
    scalars = {}
    for source, values in traces.items():
        for key, value in values.items():
            if value is not None:
                scalars[source + '.' + key] = {'value': value, 'kind': 'timing',
                                              'direction': 'lower' if key.endswith('_fps') else 'higher'}
    for key in ('rss_mib', 'gpu_process_mib', 'gpu_total_mib'):
        if metadata.get('intel') and key.startswith('gpu_'):
            missing[key] = 'Intel shared memory: NVIDIA device samples are machine-load context, not this game'
            continue
        values = [s[key] for s in samples if s.get(key) is not None]
        if values:
            scalars[key + '.peak'] = {'value': max(values), 'kind': 'memory', 'direction': 'higher'}
            growth = slope(samples, key)
            if growth is not None:
                scalars[key + '.slope_per_min'] = {'value': growth, 'kind': 'memory', 'direction': 'higher'}
        else:
            missing[key] = 'no process/device accounting sample available'
    cpu = [s['cpu_s'] for s in samples if s.get('cpu_s') is not None]
    if cpu:
        scalars['cpu_s'] = {'value': max(cpu), 'kind': 'timing', 'direction': 'higher'}
    for key, value in metadata.get('timings', {}).items():
        scalars[key] = {'value': value, 'kind': 'timing', 'direction': 'higher'}
    nocore = [line for line in lines if 'ran without a core' in line]
    scalars['nocore.count'] = {'value': len(nocore), 'kind': 'counter', 'direction': 'higher'}
    correlation = []
    for event in events:
        if event.get('in_play') and ACTIVITY.search(event['line']):
            nearby = [ms for t, ms in raw.get('game_present', []) if abs(t - event['monotonic_s']) <= 2]
            correlation.append({**event, 'nearby_max_ms': max(nearby) if nearby else None,
                                'alignment': 'log read receipt +/-2 s; not causal proof'})
    images = {}
    for path in sorted((evidence / 'shots').glob('*.png')):
        images[path.stem] = picture(path)
    # A bright/dark/bright reversal is only a flash candidate, retained for human review.
    flash_candidates = []
    for burst in metadata.get('shot_bursts', []):
        ordered = [(name, images[name]) for name in burst if name in images]
        for i in range(1, len(ordered) - 1):
            a, b, c = (ordered[j][1]['luma_mean'] for j in (i-1, i, i+1))
            if abs(a-c) <= 5 and abs(b-(a+c)/2) >= 40:
                flash_candidates.append(ordered[i][0])
    result = {'metrics': scalars, 'signatures': dict(signatures), 'images': images, 'missing': missing,
            'device_lines': [s for s in lines if re.search(r'vk: device \d|Vulkan.*driver', s)],
            'vram_log_lines': [s for s in lines if VRAM.search(s)], 'nocore_lines': nocore,
            'shader_pipeline_lines': [s for s in lines if ACTIVITY.search(s)],
            'in_play_compilation_hitches': correlation, 'flash_candidates': flash_candidates,
            'summary': text(run / 'summary.txt').strip(),
            'exit': int(text(run / 'exit.txt').strip()) if text(run / 'exit.txt').strip() else None,
            'samples': len(samples), 'fatal_lines': [s for s in lines if re.search(r'FATAL|CRASH:|VK_ERROR_DEVICE_LOST|device lost', s)]}
    telemetry = system_telemetry(run)
    if telemetry is not None:
        result['system_telemetry'] = telemetry  # context only, never scalar/baseline inputs
    return result
