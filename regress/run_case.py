#!/usr/bin/env python3
"""Run one local Hookshot scenario through hs-run.sh inside an existing gate hold, and measure it.

Public text: no scenario or game data lives here. The scenario JSON's `run` object supplies the save folder, the
input script, the environment, the measurement window and the log lines the run must (or must not) show.
`run.perf: true` opts into perf.csv; sampled context is separate from decision measures and quiet checks.

  run_case.py --copy <game copy> --commit <sha> --output <new run folder> --scenario <local json> --seconds <n>

It writes <output>/result.json (value, valid, disturbed, kernel_errors, load_before, load_after, summary,
evidence, measures, others_cores_window, others_cores_peak, threads, ...) and never decides anything itself:
regress.py reads the result. A timing run is marked disturbed when, over its measuring window, everything except the
game used `quiet_cores` processor cores or more on average (3) or any 5-second sample of that was above
`quiet_peak_cores` (6), or when that cannot be shown; the 1-minute load is recorded and marks nothing.
"""
import argparse
import collections
import csv
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import time

HARN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HARN if (HARN / 'hs_names.py').is_file() else HARN / 'harness'))
from hs_names import ALL_SETTINGS, LOG_VERSION, copy_names, log_path

GATE_MARK = 'HSGATE_IN_GATE'
LOG_REPLAY = re.compile(r"vk: a frame's replay time \(ms\): (.*)$")
LOG_PRESENTS = re.compile(r"vk: (\d+) presents, ([\d.]+) fps; a frame: (\d+) draws")
LOG_GPU = re.compile(r"vk: GPU time ([\d.]+) ms a frame")
LOG_WAITS = re.compile(r"vk: a frame: ([\d.]+) draws left out \(not ready\), ([\d.]+) waits for shaders or pipelines \(([\d.]+) ms\)")
LOG_VRAM = re.compile(r"VRAM ([\d.]+) of ([\d.]+) GB")
LOG_READBACKS = re.compile(r"vk: readbacks of GPU results: (\d+) of 3000 waited for their write-back, ([\d.]+) ms each")


def date(fmt='-Iseconds'):
    return subprocess.check_output(['date', fmt], text=True).strip()


def whole_lines(path):
    """A file's text up to its last newline: a stopped game can leave a cut last line."""
    text = Path(path).read_text(errors='replace')
    return text[:text.rfind('\n') + 1]


def statistics_of(values):
    assert values and all(math.isfinite(x) and x > 0 for x in values), 'bad frame times'
    slow = sorted(values, reverse=True)[:max(1, len(values) // 100)]
    ordered = sorted(values)
    return {'frames': len(values), 'mean_ms': statistics.fmean(values), 'median_ms': statistics.median(values),
            'p99_ms': ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))],
            'fps': 1000 / statistics.fmean(values), 'low1_fps': 1000 / statistics.fmean(slow),
            'over16_7': sum(x > 16.7 for x in values), 'over20': sum(x > 20 for x in values),
            'over25': sum(x > 25 for x in values), 'max_ms': max(values)}


def read_frames(path):
    rows = [r for r in csv.reader(io.StringIO(whole_lines(path))) if len(r) == 3]
    assert rows and rows[0] == ['frame', 'time_s', 'frametime_ms'], 'not a frame log'
    rows = rows[1:]
    assert len(rows) >= 3, 'no renderer frame samples'
    frames = [int(r[0]) for r in rows]; seconds = [float(r[1]) for r in rows]; ms = [float(r[2]) for r in rows]
    assert all(b == a + 1 for a, b in zip(frames, frames[1:])), 'missing frames'
    assert all(b >= a for a, b in zip(seconds, seconds[1:])), 'time reversed'
    assert abs(sum(ms[1:]) / 1000 - (seconds[-1] - seconds[0])) < 0.05 + 1e-4 * len(ms), 'frame times do not sum to the span'
    return frames, seconds, ms


def window_of(seconds, ms, settings):
    """(anchor, lo, hi, gaps): the measurement window in the frame log's seconds. anchor `load`: the end of the longest
    gap over load_gap_ms that begins between load_after and load_before (a scripted load; a later hitch is not it)."""
    gap_ms = settings.get('load_gap_ms', 300)
    gaps = [(round(t - d / 1000, 3), round(t, 3), d) for t, d in zip(seconds, ms) if d > gap_ms]
    anchor = 0.0
    if settings.get('anchor', 'load') == 'load':
        lo_s, hi_s = settings.get('load_after', 10), settings.get('load_before', 60)
        inside = [g for g in gaps if lo_s <= g[0] and g[1] <= hi_s]
        assert inside, 'no save-load gap: scene not verified'
        anchor = max(inside, key=lambda g: g[2])[1]
    lo, hi = anchor + settings.get('from', 15), anchor + settings.get('to', 45)
    return anchor, lo, hi, gaps


def readback_metrics(path, first, last):
    rows = [r for r in csv.DictReader(io.StringIO(whole_lines(path))) if r.get('fallback') not in (None, '')]
    rows = [r for r in rows if first <= int(r['frame_begin']) <= last]
    frames = last - first + 1
    inv = [r for r in rows if r['kind'] == 'invalidate']
    elig = [r for r in inv if int(r['eligible'])]
    waited = [r for r in elig if int(r['waited'])]
    waits = [float(r['wait_ms']) for r in elig]
    per_frame = collections.Counter(int(r['frame_begin']) for r in elig)
    texels = [r for r in rows if r['kind'] == 'read_texels']
    ordered = sorted(waits)
    return {'invalidates_per_frame': len(inv) / frames, 'readbacks_per_frame': len(elig) / frames,
            'readbacks_waited_per_frame': len(waited) / frames, 'readbacks_frame_max': max(per_frame.values(), default=0),
            'readback_wait_mean_ms': statistics.fmean(waits) if waits else 0.0,
            'readback_wait_median_ms': statistics.median(waits) if waits else 0.0,
            'readback_wait_p95_ms': ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0.0,
            'readback_wait_max_ms': max(waits, default=0.0),
            'readback_waited_mean_ms': statistics.fmean(float(r['wait_ms']) for r in waited) if waited else 0.0,
            'readback_wait_ms_per_frame': sum(waits) / frames,
            'readback_catchup_ms_per_frame': sum(float(r['catchup_ms']) for r in elig) / frames,
            'invalidate_ms_per_frame': sum(float(r['elapsed_ms']) for r in inv) / frames,
            'readback_fallbacks': sum(int(r['fallback']) for r in elig),
            'readbacks_spanning_present': sum(int(r['frame_end']) > int(r['frame_begin']) for r in elig),
            'read_texels_calls': len(texels), 'read_texels_ms_per_frame': sum(float(r['elapsed_ms']) for r in texels) / frames}


def log_blocks(text):
    """The renderer's 120-present blocks: replay phases, waits, presents and GPU time, closed by the GPU line."""
    blocks, pending = [], {}
    for line in text.splitlines():
        m = LOG_REPLAY.search(line)
        if m:
            phases = {}
            for part in m.group(1).split(','):
                try:
                    key, value = part.rsplit(' ', 1); phases[key.strip()] = float(value)
                except ValueError:
                    continue
            pending['replay'] = phases
            continue
        m = LOG_WAITS.search(line)
        if m:
            pending['left_out'], pending['waits'], pending['wait_ms'] = map(float, m.groups())
            continue
        m = LOG_PRESENTS.search(line)
        if m:
            pending['present'], pending['fps_log'], pending['draws'] = int(m.group(1)), float(m.group(2)), int(m.group(3))
            continue
        m = LOG_GPU.search(line)
        if m:
            pending['gpu_ms'] = float(m.group(1))
            if 'present' in pending:
                blocks.append(pending)
            pending = {}
    return blocks


def log_metrics(text, first, last):
    blocks = log_blocks(text)
    sel = [b for b in blocks if first + 120 <= b['present'] <= last]
    out = {'log_blocks_in_window': len(sel)}
    for key in ('gpu_ms', 'draws', 'waits', 'wait_ms', 'left_out'):
        values = [b[key] for b in sel if key in b]
        if values:
            out['log_' + key] = statistics.fmean(values)
    phases = [b['replay'] for b in sel if b.get('replay')]
    for key in (phases[0] if phases else {}):
        values = [p[key] for p in phases if key in p]
        out['replay_' + re.sub(r'\W+', '_', key)] = statistics.fmean(values)
    vram = [(float(a), float(b)) for a, b in LOG_VRAM.findall(text)]
    if vram:
        out['vram_gb_max'], out['vram_allowed_gb'] = max(a for a, _ in vram), vram[-1][1]
    rb = [(int(n), float(ms)) for n, ms in LOG_READBACKS.findall(text)]
    if rb:
        out['readback_log_lines'] = len(rb)
        out['readback_log_waited_of_3000'] = statistics.fmean(n for n, _ in rb)
        out['readback_log_ms_each'] = statistics.fmean(ms for _, ms in rb)
    return out, blocks


def present_rows(path):
    """The present trace's rows by the fields every build's trace has first: frame, return ns, interval ns (older
    builds write these three, newer ones seven). The heading and a line cut off by the game's end are left out."""
    return [r for r in csv.reader(io.StringIO(whole_lines(path))) if len(r) >= 3 and r[0].isdigit() and r[1].isdigit()]


def present_metrics(path, start_monotonic_ns, seconds):
    rows = present_rows(path)
    if not rows:
        return {}
    out = {'game_presents': len(rows)}
    first = (int(rows[0][1]) - start_monotonic_ns) / 1e9
    if 0 < first < seconds:   # (the trace's clock is the monotonic one this adapter read at the start)
        out['start_to_first_present_s'] = first
    return out


def present_window(path, first, last):
    """The measuring window on the monotonic clock: when its first and its last present returned (the present trace)."""
    times = {int(r[0]): int(r[1]) / 1e9 for r in present_rows(path)}
    return (times[first], times[last]) if first in times and last in times else None


def source_stamp(copy):
    """The copy's own build stamp, when its build left one beside the binary (regress-source.txt: build_case.py)."""
    path = Path(copy) / 'regress-source.txt'
    return path.read_text().strip() if path.exists() else ''


def stamp_in_log(text, stamp):
    """True only when nonempty build provenance matches the binary's log."""
    return bool(stamp) and re.search(r'\(source ' + re.escape(stamp) + r'\)', text) is not None


def quiet_measure(samples, lo, hi, span=5.0):
    """What everything except the game used between lo and hi (monotonic seconds), in processor cores: the mean, and
    the highest sample of about `span` seconds. None when the samples do not cover the window."""
    seen = [s for s in samples if 'game' in s and lo <= s['t'] <= hi]
    if len(seen) < 4 or seen[-1]['t'] - seen[0]['t'] < 0.8 * (hi - lo):
        return None
    ticks = os.sysconf('SC_CLK_TCK')

    def cores(a, b):
        return ((b['busy'] - a['busy']) - (b['game'] - a['game'])) / ticks / (b['t'] - a['t'])
    peaks = []
    for i, a in enumerate(seen):
        b = next((x for x in seen[i + 1:] if x['t'] - a['t'] >= span - 0.3), None)
        if b:
            peaks.append(cores(a, b))
    if not peaks:
        return None
    game = (seen[-1]['game'] - seen[0]['game']) / ticks / (seen[-1]['t'] - seen[0]['t'])   # (the whole process: every thread)
    return {'mean': cores(seen[0], seen[-1]), 'peak': max(peaks), 'samples': len(seen), 'game': game}


def thread_groups(samples, lo, hi):
    """Processor cores each group of the game's threads (a name without its number) used between lo and hi."""
    seen = [s for s in samples if 'threads' in s and lo <= s['t'] <= hi]
    if len(seen) < 2:
        return None
    a, b = seen[0], seen[-1]; dt = b['t'] - a['t']; ticks = os.sysconf('SC_CLK_TCK')
    groups = {}
    for tid, (name, used) in b['threads'].items():
        if tid in a['threads']:
            key = re.sub(r'[\s\d#:_-]+$', '', name) or name
            g = groups.setdefault(key, [0, 0.0]); g[0] += 1; g[1] += (used - a['threads'][tid][1]) / ticks / dt
    top = sorted(groups.items(), key=lambda kv: -kv[1][1])[:12]
    return {'seconds': dt, 'total': sum(c for _, c in groups.values()), 'groups': {k: {'n': v[0], 'cores': v[1]} for k, v in top}}


def present_shift(frames, ms, rows, tail=30):
    """What to add to a frame log's frame number to get the game's present number (the present trace's and the local
    readback log's count). With a window the frame log counts the swapchain's presents, the start-up screen's too, so
    the two files are matched at their ends: their last rows are the same present, unless the game was stopped between
    the two writes, which the intervals of the last rows show (the candidate with the most equal intervals wins; the
    tails' own difference on a tie)."""
    if not rows or len(frames) < 2:
        return 1
    interval = {int(r[0]): int(r[2]) / 1e6 for r in rows[-(tail + 4):]}
    score = lambda shift: sum(1 for f, d in zip(frames[-tail:], ms[-tail:]) if abs(interval.get(f + shift, -1e9) - d) < 0.01)
    base = int(rows[-1][0]) - frames[-1]
    return max((base, base + 1, base - 1, base + 2, base - 2), key=score)


def system_telemetry(run, *, start=None, end=None):
    """Optional sampled context; never import a reader from the caller or run folder."""
    if not (Path(run) / 'perf.csv').is_file():
        return None
    try:
        path = HARN / 'perf' / 'hs_perf.py'
        spec = importlib.util.spec_from_file_location('hookshot_system_telemetry', path)
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
        return reader.attach(run, start=start, end=end)
    except Exception:
        # A missing/broken optional reader must not invalidate the existing run evidence.
        return {'artifact': 'perf.csv', 'status': 'cannot_tell', 'error': 'reader_unavailable',
                'notice': 'Sampled context unavailable; existing decisions are unchanged.'}


def analyse(run, settings, start_monotonic_ns=0, seconds=0, *, receipt=None):
    run = Path(run)
    frames, times, ms = read_frames(run / 'frames.csv')
    anchor, lo, hi, gaps = window_of(times, ms, settings)
    indexes = [i for i, t in enumerate(times) if lo <= t <= hi]
    assert len(indexes) > 2 and times[indexes[-1]] - times[indexes[0]] >= 0.9 * (hi - lo), 'short measurement window'
    result = statistics_of([ms[i] for i in indexes[1:]])
    if receipt is not None:
        # Exclude the first interval as above, using raw frames.csv IDs, not game presents.
        telemetry = system_telemetry(run, start=f"frame:{frames[indexes[1]]}", end=f"frame:{frames[indexes[-1]]}")
        if telemetry is not None:
            receipt['system_telemetry'] = telemetry
    # (present numbers: the log's and the probe's count. Without a window the frame log is one behind them.)
    shift = 1
    if settings.get('window') and (run / 'present.csv').exists():
        shift = present_shift(frames, ms, present_rows(run / 'present.csv'))
    first, last = frames[indexes[0]] + shift, frames[indexes[-1]] + shift
    after = [d for t, d in zip(times, ms) if t > anchor + 1]
    result.update(anchor_s=anchor, from_s=lo, to_s=hi, first_frame=first, last_frame=last, present_shift=shift, gaps_over_limit=gaps[:40],
                  presents_total=len(frames), span_s=times[-1],
                  after_load_over25=sum(d > 25 for d in after), after_load_over50=sum(d > 50 for d in after),
                  after_load_over100=sum(d > 100 for d in after), after_load_max_ms=max(after, default=0.0))
    if (run / 'readbacks.csv').exists():
        result.update(readback_metrics(run / 'readbacks.csv', first, last))
    blocks = []
    logfile = log_path(run)
    if logfile is not None:
        more, blocks = log_metrics(logfile.read_text(errors='replace'), first, last)
        result.update(more)
    if (run / 'present.csv').exists():
        result.update(present_metrics(run / 'present.csv', start_monotonic_ns, seconds))
        if 'start_to_first_present_s' in result:
            result['start_to_play_s'] = result['start_to_first_present_s'] + anchor
    return result, blocks


def kernel_records(text, intel=False):
    """Classify a bounded journal interval; repeats belong to the preceding message."""
    records, retained, previous = [], [], None
    repeat = re.compile(r'(?:last (?:print|message)|message) repeated (\d+) times', re.I)
    intel_fault = re.compile(r'(?i)(i915|xe ).*(hang|reset|error)')
    fault = re.compile(r'NVRM: Xid|kmemsysDoCacheOp')
    allocation = (re.compile(r'NV_ERR_NO_MEMORY.*_memdescAllocInternal.*\bmem_desc\.c\b'),
                  re.compile(r'\brmStatus\b.*\bsystem_mem\.c\b'),
                  re.compile(r'pRmApi->Alloc.*\bnv_gpu_ops\.c\b'))
    repeats = 0
    for line in text.splitlines():
        marker = repeat.search(line)
        if marker:
            if previous is None:
                previous = {'line': line, 'count': 1, 'second': None, 'orphan': True}
                records.append(previous)
                retained.append(line)
            elif previous is not False:
                n = int(marker[1])
                previous['count'] += n
                repeats += n
                retained.append(line)
            continue
        previous = False
        if 'NVRM' not in line and 'kmemsysDoCacheOp' not in line and not intel_fault.search(line):
            continue
        stamp = re.match(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d|[A-Z][a-z]{2} +\d+ \d\d:\d\d:\d\d)', line)
        previous = {'line': line, 'count': 1, 'second': stamp[1] if stamp else None}
        records.append(previous)
        retained.append(line)
    counts = {'kernel_errors': 0, 'memmap_lines': 0, 'no_memory_lines': 0, 'no_memory_events': 0,
              'nvrm_lines': sum(r['count'] for r in records if 'NVRM' in r['line']), 'kernel_repeats': repeats}
    index = 0
    while index < len(records):
        record = records[index]; line, n = record['line'], record['count']
        if record.get('orphan'):
            counts['kernel_errors'] += n
        elif intel:
            counts['kernel_errors'] += n if intel_fault.search(line) else 0
        elif fault.search(line):
            counts['kernel_errors'] += n
        elif 'NVRM' not in line:
            pass
        elif 'memMap_IMPL: CPU mapping not supported for addressSpace: 0x1' in line:
            counts['memmap_lines'] += n
        else:
            triple = records[index:index + 3]
            if (len(triple) == 3 and record['second'] is not None and
                    all(item['second'] == record['second'] and item['count'] == n and
                        'NVRM' in item['line'] and not fault.search(item['line']) and pattern.search(item['line'])
                        for item, pattern in zip(triple, allocation))):
                counts['no_memory_lines'] += 3 * n
                counts['no_memory_events'] += n
                index += 3
                continue
            counts['kernel_errors'] += n
        index += 1
    if counts['no_memory_events'] > 3:
        counts['kernel_errors'] += counts['no_memory_lines']
    return '\n'.join(retained) + ('\n' if retained else ''), counts


def kernel_lines(start, end, intel=False):
    """Keep the journal receipt; unavailable or partial coverage is never zero faults."""
    try:
        completed = subprocess.run(['journalctl', '-k', '--since', start, '--until', end, '--no-pager',
                                    '--output=short-iso'], text=True, capture_output=True)
        retained, counts = kernel_records(completed.stdout, intel)
        code, error = completed.returncode, completed.stderr.strip()
    except OSError as exc:
        retained, counts = kernel_records('', intel)
        code, error = None, str(exc)
    complete = code == 0 and not error
    if not complete:
        counts = dict.fromkeys(counts)
    counts.update(kernel_status='complete' if complete else 'unknown', journal_exit=code, journal_stderr=error)
    return retained, counts


def setup(copy, output, settings, seconds):
    """The run folder as hs-run.sh's caller side leaves it for `hs-run.sh --inner` (which takes no gate itself)."""
    env = dict(settings.get('env', {}))
    assert not (copy / 'mods').exists() and 'HOS_MODS' not in os.environ and 'HOS_MODS' not in env, \
        'mods must be absent for an unmodified comparison'
    for name in ALL_SETTINGS:
        assert settings.get('copy_settings_allowed') or not (copy / name).exists(), name + ' in the copy would change the run'
    output.mkdir(parents=True, exist_ok=True)
    assert not (output / 'start.txt').exists(), 'refusing to overwrite a run'
    if settings.get('readback_csv', True):
        env['RX_VK_READBACK_CSV'] = str(output / 'readbacks.csv')
    if settings.get('framelog'):
        env['RX_VK_FRAMELOG'] = str(output / 'framelog.bin')
    if settings.get('perf'):
        # Match hs-run.sh --perf, including the effective values retained in the case receipt.
        env['RX_VK_PERF_LOG'] = str(output)
        env['RX_VK_PERF_FIELDS'] = 'all'
        (output / 'perf').touch()
    (output / 'copy.txt').write_text(str(copy) + '\n')
    (output / 'seconds.txt').write_text(str(seconds) + '\n')
    (output / 'input.txt').write_text(settings.get('input', ''))
    # (hs-run.sh tests it with -s: any byte starts a memory scope with that limit, e.g. `mem_max: "32G"` for a device
    #  whose driver can take the machine's memory while it compiles)
    (output / 'memmax').write_text(settings.get('mem_max', ''))
    (output / 'env-file.txt').write_text('# empty: the scenario supplies the environment\n')
    (output / 'extra-env.txt').write_text(''.join(f'{k}={v}\n' for k, v in env.items()))
    if settings.get('prepare'):
        (output / 'prepare').touch()
    if settings.get('window'):
        (output / 'window').touch()
    if settings.get('intel'):   # (the other device, through its own gate: the caller holds that gate, not the card's)
        (output / 'intel').touch()
    if settings.get('user'):
        subprocess.run(['cp', '-a', '--reflink=auto', settings['user'], str(output / 'user')], check=True)
    else:
        (output / 'user').mkdir()
    return env


def game_pid(root, exe):
    parents = {}
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            try:
                parents[int(p.name)] = int((p / 'stat').read_text().rsplit(')', 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                continue
    for pid in parents:
        q = pid
        while q in parents and q != root and q > 1:
            q = parents[q]
        if q == root:
            try:
                if os.readlink(f'/proc/{pid}/exe') == str(exe):
                    return pid
            except OSError:
                continue
    return None


def thread_times(pid):
    """Each thread of the game: its name and the processor time it has used so far, in clock ticks."""
    out = {}
    try:
        for t in Path(f'/proc/{pid}/task').iterdir():
            try:
                text = (t / 'stat').read_text()
                f = text.rsplit(')', 1)[1].split()
                out[t.name] = [text[text.index('(') + 1:text.rindex(')')], int(f[11]) + int(f[12])]
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        pass
    return out


def sample(pid):
    s = {'t': time.monotonic(), 'load': os.getloadavg()[0]}
    with open('/proc/stat') as f:
        v = [int(x) for x in f.readline().split()[1:9]]
    s['busy'] = sum(v) - v[3] - v[4]
    if pid:
        try:
            st = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            s['game'] = int(st[11]) + int(st[12])
            status = Path(f'/proc/{pid}/status').read_text()
            for key in ('VmRSS', 'VmHWM', 'RssAnon', 'RssFile', 'RssShmem'):
                m = re.search(rf'^{key}:\s+(\d+)', status, re.M)
                if m:
                    s[key] = int(m.group(1)) / 1024
            s['threads'] = thread_times(pid)
        except (OSError, IndexError, ValueError):
            pass
    return s


def others_cores(samples):
    """Processor cores everything but the game kept busy, a mean over the samples that saw the game (the whole run)."""
    seen = [s for s in samples if 'game' in s]
    if len(seen) < 2 or seen[-1]['t'] <= seen[0]['t']:
        return None
    ticks = os.sysconf('SC_CLK_TCK')
    busy = (seen[-1]['busy'] - seen[0]['busy']) - (seen[-1]['game'] - seen[0]['game'])
    return busy / ticks / (seen[-1]['t'] - seen[0]['t'])


def play_folder_idle(folder):
    for p in Path('/proc').iterdir():
        if p.name.isdigit():
            try:
                if os.readlink(p / 'exe').startswith(folder):
                    return False
            except OSError:
                continue
    return True


def quiet_verdict(result, scenario):
    """True when the run's window is shown quiet by the scenario's two numbers (defaults 3 and 6 cores)."""
    mean, peak = result.get('others_cores_window'), result.get('others_cores_peak')
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in (mean, peak)):
        return False
    return mean < scenario.get('quiet_cores', 3) and peak <= scenario.get('quiet_peak_cores', 6)


def run_case(copy, output, scenario, seconds, commit):
    settings = scenario['run']; copy = Path(copy).resolve(); output = Path(output).resolve()
    # (inside the gate of the device the run is for: the card's, or with `intel` the other device's own)
    mark = 'HSGATE_IN_INTEL_GATE' if settings.get('intel') else GATE_MARK
    assert os.environ.get(mark), 'must be called inside the hold of the device the run is for (' + mark + ')'
    names = copy_names(copy)
    assert copy.name.startswith('.hookshot-h-') and (copy / names.executable).is_file(), 'not a built harness copy'
    if settings.get('window'):
        switch = Path(settings.get('window_switch', str(Path.home() / 'Games/.hookshot-harness/window-ok')))
        assert switch.exists(), 'window authorization switch is absent'
        assert play_folder_idle(settings.get('play_folder', str(Path.home() / 'Games/Hookshot/'))), 'the play folder runs a game'
    env = setup(copy, output, settings, seconds)
    wrapper = settings.get('wrapper', str(Path.home() / 'Games/.hookshot-harness/hs-run.sh'))
    start, start_journal, start_ns = date(), date('+%F %T'), time.monotonic_ns()
    samples, pid = [], None
    framelog = settings.get('framelog') or {}
    go = Path(str(output / 'framelog.bin') + '.go')
    stop = {'asked': False}   # (a hold ending early: the game is asked to end, the wrapper then writes its files)
    signal.signal(signal.SIGTERM, lambda *a: stop.update(asked=True))
    with (output / 'wrapper.log').open('w') as log:
        process = subprocess.Popen([wrapper, '--inner', str(output)], stdout=log, stderr=subprocess.STDOUT)
        while process.poll() is None:
            pid = pid or game_pid(process.pid, copy / names.executable)
            if stop['asked'] and pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            samples.append(sample(pid))
            elapsed = (time.monotonic_ns() - start_ns) / 1e9
            if framelog:
                on = framelog.get('from', 0) <= elapsed < framelog.get('from', 0) + framelog.get('seconds', 0)
                if on and not go.exists():
                    go.touch()
                if not on and go.exists():
                    go.unlink()
            try:
                process.wait(timeout=settings.get('sample_seconds', 2.5))   # (two samples: the quiet rule's 5 seconds)
            except subprocess.TimeoutExpired:
                pass
        rc = process.returncode
    if go.exists():
        go.unlink()
    end, end_journal = date(), date('+%F %T')
    (output / 'samples.json').write_text(json.dumps(samples) + '\n')
    read = lambda name: (output / name).read_text().strip() if (output / name).exists() else ''
    loads = [s['load'] for s in samples]
    result = {'commit': commit, 'label': settings.get('label', ''), 'copy': str(copy), 'start': start, 'end': end,
              'valid': False, 'disturbed': False, 'value': None, 'wrapper_exit': rc, 'environment': env,
              'load_before': float(read('load-before.txt').split()[0]) if read('load-before.txt') else os.getloadavg()[0],
              'load_after': float(read('load-after.txt').split()[0]) if read('load-after.txt') else os.getloadavg()[0],
              'load_max': max(loads, default=0.0), 'others_cores': others_cores(samples), 'evidence': [str(output)],
              'peak_rss_mb': max((s.get('VmHWM', 0) for s in samples), default=0),
              'rss_parts_mb': {k: v for k, v in (samples[-1] if samples else {}).items() if k.startswith('Rss')},
              'shots': len(list((output / 'shots').glob('*.png'))) if (output / 'shots').is_dir() else 0}
    journal, kernel = kernel_lines(start_journal, end_journal, bool(settings.get('intel')))
    (output / 'kernel.txt').write_text(journal)
    result.update(kernel)
    logfile = log_path(output)
    text = logfile.read_text(errors='replace') if logfile is not None else ''
    version = LOG_VERSION.search(text)
    code = int(read('exit.txt')) if read('exit.txt') else -1
    stamp = source_stamp(copy)
    result['source_expected'] = stamp
    result.update(exit=code, wall_s=float(read('wall.txt')) if read('wall.txt') else None,
                  version=version.group(0)[:120] if version else '',
                  fatal=len(re.findall(r'FATAL|CRASH:|VK_ERROR_DEVICE_LOST|(?i:device lost)', text)),
                  nocore=len(re.findall('ran without a core', text)),
                  mods={'folder_absent': not (copy / 'mods').exists(), 'HOS_MODS_unset': 'HOS_MODS' not in os.environ,
                        'log': [line[:200] for line in text.splitlines() if re.search(r'\bmods?\b', line, re.I)][:10]})
    try:
        assert logfile is not None and logfile.name == names.log, 'run log does not match the copy project'
        assert rc == 0 and code == (0 if settings.get('prepare') else 124), 'run did not reach its intended end'
        assert re.search(settings.get('device_pattern', r'vk: device 0: NVIDIA GeForce RTX 4090'), text), 'wrong or missing device'
        assert kernel['kernel_errors'] is not None, 'kernel journal coverage is unknown'
        assert not result['fatal'] and kernel['kernel_errors'] == 0 and kernel['no_memory_events'] <= 3, 'run or kernel fault'
        assert stamp, 'the copy has no source stamp'
        assert stamp_in_log(text, stamp), "the log lacks the copy's own source stamp " + stamp
        for pattern in settings.get('expect_log', []):
            assert re.search(pattern, text), 'log lacks ' + pattern
        for pattern in settings.get('forbid_log', []):
            assert not re.search(pattern, text), 'log has ' + pattern
        if settings.get('window'):
            result['window'] = read('window.txt')
            assert all(t in result['window'] for t in ('window=ok', 'active=same', 'others=same', 'screen=on')), 'window disturbed'
        if settings.get('prepare'):
            assert '@@done shaders' in text, 'preparation incomplete'
            measures, blocks = {}, []
        else:
            measures, blocks = analyse(output, settings, start_ns, seconds, receipt=result)
            window = None
            if (output / 'present.csv').exists():
                window = present_window(output / 'present.csv', measures['first_frame'], measures['last_frame'])
            if window:
                quiet = quiet_measure(samples, *window)
                if quiet:
                    result.update(others_cores_window=quiet['mean'], others_cores_peak=quiet['peak'],
                                  game_cores_window=quiet['game'], cpu_ms_per_frame=quiet['game'] * measures['mean_ms'])
                result['threads'] = thread_groups(samples, *window)
        result['measures'] = measures
        (output / 'log-blocks.json').write_text(json.dumps(blocks) + '\n')
        if scenario['measure']['direction'] == 'pass-fail':
            result['value'] = True
        else:
            name = scenario['measure']['name']
            value = measures.get(name)
            assert not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value), \
                'missing or nonnumeric decision measure: ' + name
            result['value'] = value
        if 'validator_command' in settings:
            command = [s.format(output=str(output), copy=str(copy)) for s in settings['validator_command']]
            with (output / 'validator.log').open('w') as log:
                passed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT).returncode == 0
            if scenario['measure']['direction'] == 'pass-fail':
                result['value'] = passed
            else:
                assert passed, 'scenario validator failed'
        result['valid'] = True
    except (AssertionError, OSError, ValueError, KeyError) as exc:
        result['problem'] = str(exc) or type(exc).__name__
    if ((not result['valid'] or result['value'] is False) and result['kernel_errors'] is not None and
            0 < result['no_memory_events'] <= 3):
        result['kernel_errors'] += result['no_memory_lines']
    loud = scenario.get('kind') == 'timing' and result['valid'] and not settings.get('prepare') and not quiet_verdict(result, scenario)
    result['disturbed'] = bool(result['fatal'] or result['kernel_errors'] != 0 or loud or (settings.get('window') and not result['valid']))
    m = result.get('measures', {})
    f = lambda key, fmt, source=m: fmt % source[key] if isinstance(source.get(key), (int, float)) else '-'
    result['summary'] = ('%s %s exit=%s valid=%s disturbed=%s others %s cores (highest 5 s %s) load %.2f -> %.2f kernel=%s '
                         'fps=%s mean=%s ms readbacks/frame=%s wait/frame=%s ms%s' % (
                             commit[:8], result['label'], code, result['valid'], result['disturbed'],
                             f('others_cores_window', '%.2f', result), f('others_cores_peak', '%.2f', result),
                             result['load_before'], result['load_after'],
                             result['kernel_errors'] if result['kernel_errors'] is not None else 'unknown', f('fps', '%.2f'),
                             f('mean_ms', '%.3f'), f('readbacks_per_frame', '%.2f'), f('readback_wait_ms_per_frame', '%.3f'),
                             ' PROBLEM: ' + result['problem'] if 'problem' in result else ''))
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (output / 'summary.txt').write_text(result['summary'] + '\n')
    print(result['summary'], flush=True)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ('copy', 'output', 'scenario', 'commit'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--seconds', type=int, required=True)
    a = p.parse_args()
    run_case(a.copy, a.output, json.loads(Path(a.scenario).read_text()), a.seconds, a.commit)


if __name__ == '__main__':
    main()
