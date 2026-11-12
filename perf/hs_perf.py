#!/usr/bin/env python3
"""Read Hookshot perf CSV v1. Observations and suspicions only, never a speed verdict.

Window endpoints are inclusive sample receipts: elapsed seconds (default), frame:N,
monotonic:S, or marker:EVENT[@OCCURRENCE]. Sample frame statistics are overlapping
trailing windows: their averages are NOT whole-session frame statistics. Only raw
frames.csv can independently recompute the final summary.
"""
import argparse
import csv
import io
import itertools
import json
import math
from pathlib import Path
import re
import statistics
import sys

_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_root if (_root / 'hs_names.py').is_file() else _root / 'harness'))
from hs_names import PERF_HEADERS

COMMON = 'kind sample monotonic_ns elapsed_s game_frame renderer_present frame time_s clock thread_name event detail'.split()
FRAME_METRICS = ('mean_ms mean_fps low_1_fps low_0_1_fps p50_ms p95_ms p99_ms max_ms '
                 'over16_7 over20 over25 over50 over100 frames').split()
METRICS = FRAME_METRICS + ('gpu_use_pct gpu_temp_c gpu_power_w gpu_fan_pct gpu_clock_mhz gpu_throttle_mask '
    'gpu_memory_used_bytes vram_used_bytes vram_budget_bytes texture_used_bytes texture_budget_bytes gpu_ms '
    'cpu_process_pct cpu_thread_pct rss_bytes commit_bytes ram_available_bytes swap_used_bytes disk_read_bytes '
    'cpu_temp_c cpu_clock_mhz battery_pct ac_online pipelines left_out upload_bytes gpu_wait_ms').split()
COLUMNS = COMMON + METRICS
TEXT = {'kind', 'clock', 'thread_name', 'event', 'detail'}
INTEGERS = {'sample', 'monotonic_ns', 'game_frame', 'renderer_present', 'frame', 'frames', 'gpu_throttle_mask',
            'ac_online', 'pipelines', 'left_out', *[k for k in METRICS if k.startswith('over') or k.endswith('_bytes')]}
CLOCKS = {'headless-renderer', 'swapchain'}
EVENTS = {'loading_start', 'loading_end', 'map_change', 'capture_start', 'capture_end'}
NOTICE = ('No speed verdict: frame-time claims require the existing alternating paired comparison, '
          'a quiet machine and acceptable same-arm repeat spread (hookshot-perf-compare / regress). '
          'These are unweighted sampled observations, not causal or statistical-significance claims.')
SPREAD_RULE = ('At least 3 available samples in each run; observed min/max ranges must be strictly disjoint '
               'and abs(mean B - mean A) must exceed max(max A - min A, max B - min B). '
               'Only gauge readings qualify; missing samples remain counted. No speed inference.')
GAUGES = set(METRICS) - set(FRAME_METRICS) - {'gpu_throttle_mask', 'ac_online', 'disk_read_bytes', 'pipelines', 'left_out', 'upload_bytes'}
MAX_ISSUES = 100
# NVIDIA nvml.h, pinned 5e992702f9f516e8db7a0c34d3e462a87f48339d, lines 3673-3799:
# https://github.com/NVIDIA/go-nvml/blob/5e992702f9f516e8db7a0c34d3e462a87f48339d/gen/nvml/nvml.h
NVML_CLOCK_REASONS = {
    0x001: 'gpu_idle', 0x002: 'applications_clocks_setting', 0x004: 'software_power_cap',
    0x008: 'hardware_slowdown', 0x010: 'sync_boost', 0x020: 'software_thermal_slowdown',
    0x040: 'hardware_thermal_slowdown', 0x080: 'hardware_power_brake',
    0x100: 'display_clock_setting', 0x200: 'board_limit', 0x400: 'reliability',
}
# Generic HW slowdown also occurs during PState/clock changes; board/reliability
# limits do not identify a thermal or power cause. None is a suspicion alone.
NVML_THERMAL_POWER_BITS = 0x004 | 0x020 | 0x040 | 0x080


class PerfError(ValueError):
    """Named refusal; safe to retain without the file's raw content."""
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class Capture:
    def __init__(self, source):
        self.source = str(source)
        self.rows = []
        self.issues = []
        self.issue_count = 0
        self.started = None
        self.cutoff = False
        self.last_record_kind = None
        self.gpu_vendor = None
        self.gpu_vendor_source = None
        self.gpu_vendor_conflict = False

    def record_gpu_vendor(self, vendor, source):
        if self.gpu_vendor_conflict:
            return
        if self.gpu_vendor not in (None, '0x0000', vendor) and vendor != '0x0000':
            self.gpu_vendor = None
            self.gpu_vendor_source = 'conflicting GPU vendor provenance'
            self.gpu_vendor_conflict = True
        elif vendor != '0x0000' or self.gpu_vendor is None:
            self.gpu_vendor, self.gpu_vendor_source = vendor, source

    def issue(self, code, line=None, metric=None, message=None, severity='error'):
        self.issue_count += 1
        if len(self.issues) < MAX_ISSUES:
            self.issues.append(dict(code=code, line=line, metric=metric, message=message or code, severity=severity))

    @property
    def samples(self):
        return [r for r in self.rows if r['kind'] == 'sample']

    @property
    def markers(self):
        return [r for r in self.rows if r['kind'] == 'marker']

    @property
    def final(self):
        if not self.cutoff and self.last_record_kind == 'summary' and self.rows and self.rows[-1]['kind'] == 'summary':
            row = self.rows[-1]
            if row['event'] == 'end' and row['detail'] == 'complete':
                return row
        return None

    @property
    def clock(self):
        clocks = {r['clock'] for r in self.rows if r['clock'] in CLOCKS}
        return next(iter(clocks)) if len(clocks) == 1 else None


def unit(name):
    for suffix, value in (('_bytes', 'bytes'), ('_pct', '%'), ('_mhz', 'MHz'), ('_fps', 'fps'),
                          ('_ms', 'ms'), ('_c', 'C'), ('_w', 'W')):
        if name.endswith(suffix):
            return value
    return 'bitmask' if name.endswith('_mask') else 'boolean' if name == 'ac_online' else 'count'


def _numeric(name, value):
    if value == 'na':
        return None
    if name in INTEGERS:
        if not re.fullmatch(r'\d+', value):
            raise ValueError('nonnegative decimal integer required')
        number = int(value)
        if number > 2**64 - 1:
            raise ValueError('integer out of range')
    else:
        if not re.fullmatch(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', value):
            raise ValueError('nonnegative finite decimal required')
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('nonfinite')
    if name in {'gpu_use_pct', 'gpu_fan_pct', 'cpu_thread_pct', 'battery_pct'} and number > 100:
        raise ValueError('percentage out of range')
    if name in {'gpu_temp_c', 'cpu_temp_c'} and not 0 < number <= 150:
        raise ValueError('temperature out of range')
    if name == 'ac_online' and number not in (0, 1):
        raise ValueError('boolean out of range')
    return number


def read_perf(path, *, max_bytes=None, device_log=None):
    path = Path(path)
    with path.open('rb') as stream:
        raw = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
    if max_bytes is not None and len(raw) > max_bytes:
        raise PerfError('input_too_large', 'telemetry exceeds the configured byte limit')
    cap = parse_perf(raw, source=path)
    if device_log is not None:
        # Explicit same-run evidence only: never read an unregistered sibling
        # log automatically (notably when used by the MCP artifact adapter).
        with Path(device_log).open('rb') as stream:
            raw_log = stream.read(64 * 2**20 + 1)
        if len(raw_log) > 64 * 2**20:
            raise PerfError('device_log_too_large', 'explicit device log exceeds 64 MiB')
        vendors = {}
        for line, text in enumerate(raw_log.decode('utf-8', errors='replace').splitlines(), 1):
            match = re.search(r'\bvk: device \d+: ([^,]+)', text)
            if match:
                name = match[1].lower()
                vendor = next((code for prefix, code in (('nvidia', '0x10de'), ('amd', '0x1002'), ('intel', '0x8086'))
                               if name == prefix or name.startswith(prefix + ' ')), None)
                vendors.setdefault(vendor, line)
        if len(vendors) == 1 and None not in vendors:
            vendor, line = next(iter(vendors.items()))
            cap.record_gpu_vendor(vendor, f'explicit device log {device_log}:{line}')
        else:
            cap.gpu_vendor = None
            cap.gpu_vendor_source = 'explicit device log has missing, unknown or ambiguous GPU vendor'
            cap.gpu_vendor_conflict = True
    return cap


def parse_perf(raw, source='<memory>'):
    if isinstance(raw, str):
        raw = raw.encode('utf-8')
    cap = Capture(source)
    complete = raw.rfind(b'\n') + 1
    if complete < len(raw):
        cap.cutoff = True
        cap.issue('truncated_row', raw[:complete].count(b'\n') + 1,
                  message='last unterminated record discarded; complete preceding rows retained', severity='warning')
    try:
        text = raw[:complete].decode('utf-8')
    except UnicodeDecodeError:
        raise PerfError('invalid_utf8', 'complete records are not UTF-8') from None
    stream = io.StringIO(text, newline='')
    first = stream.readline()
    if first.endswith('\r\n'):
        cap.issue('non_lf_line_endings', message='v1 requires LF line endings')
    try:
        header = next(csv.reader([first], strict=True))
    except (csv.Error, StopIteration):
        raise PerfError('invalid_header', 'expected # hookshot_perf,1 or # longshot_perf,1 followed by column names') from None
    if len(header) < 3 or header[0] not in PERF_HEADERS or not header[1].isdigit():
        raise PerfError('invalid_header', 'expected # hookshot_perf,1 or # longshot_perf,1 followed by column names')
    if header[1] != '1':
        raise PerfError('unsupported_version', 'unsupported hookshot_perf version ' + header[1][:16] + '; this reader supports only v1')
    columns = header[2:]
    if len(columns) != len(set(columns)):
        raise PerfError('duplicate_columns', 'duplicate column names in perf header')
    missing, unknown = set(COLUMNS) - set(columns), set(columns) - set(COLUMNS)
    if missing:
        raise PerfError('missing_columns', 'missing v1 columns: ' + ', '.join(sorted(missing)))
    if unknown:
        raise PerfError('unknown_columns', 'unknown columns in a v1 header; a schema change requires a version bump')
    line = 1
    last_sample, last_mono, last_elapsed, last_frame = None, None, None, None
    summary_count = 0
    while True:
        lead = stream.readline()
        if not lead:
            break
        line += 1
        if lead.startswith('#'):
            if line == 2 and lead.startswith('# started,'):
                try:
                    fields = next(csv.reader([lead], strict=True))
                    if len(fields) != 3 or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)', fields[1]):
                        raise ValueError
                    cap.started = dict(utc=fields[1], version=fields[2])
                except (csv.Error, ValueError):
                    cap.issue('invalid_started_comment', line)
            elif lead.startswith('# gpu_vendor,'):
                try:
                    fields = next(csv.reader([lead], strict=True))
                    if len(fields) == 2 and re.fullmatch(r'0x[0-9a-fA-F]{4,8}', fields[1]):
                        # Vulkan vendorID is uint32_t; Khronos IDs start at 0x10000.
                        cap.record_gpu_vendor(f'0x{int(fields[1], 16):04x}', f'perf.csv comment line {line}')
                except csv.Error:
                    pass  # Optional comments remain ignorable, never a vendor guess.
            continue
        start_line = line
        cap.last_record_kind = None
        reader = csv.reader(itertools.chain([lead], stream), strict=True)
        try:
            values = next(reader)
        except csv.Error:
            cap.issue('malformed_csv', start_line, message='invalid or unterminated CSV record')
            break
        line += reader.line_num - 1
        if len(values) != len(columns):
            cap.issue('row_width', start_line, message='record does not have every named v1 column')
            continue
        row = dict(zip(columns, values))
        row['_line'] = start_line
        bad = False
        for name in columns:
            if name not in TEXT:
                try:
                    row[name] = _numeric(name, row[name])
                except ValueError:
                    cap.issue('invalid_number', start_line, name, 'invalid numeric cell; only na represents missing')
                    bad = True
        if bad:
            continue
        if row['kind'] not in {'sample', 'marker', 'summary'}:
            cap.issue('unknown_kind', start_line)
            continue
        if row['clock'] not in CLOCKS:
            cap.issue('invalid_clock', start_line, 'clock')
        for name in ('sample', 'monotonic_ns', 'elapsed_s'):
            if row[name] is None:
                cap.issue('missing_common_value', start_line, name)
        if (row['frame'] is None) != (row['time_s'] is None):
            cap.issue('incomplete_frame_identity', start_line)
        if row['kind'] == 'marker' and row['event'] not in EVENTS:
            cap.issue('invalid_marker_event', start_line)
        if row['kind'] == 'marker' and any(row[k] is not None for k in METRICS):
            cap.issue('marker_metrics_present', start_line, message='marker metric cells must be na')
        if row['kind'] == 'summary':
            summary_count += 1
            if row['event'] != 'end' or row['detail'] != 'complete':
                cap.issue('invalid_summary_end', start_line)
        elif summary_count:
            cap.issue('row_after_summary', start_line)
        for name, old in (('monotonic_ns', last_mono), ('elapsed_s', last_elapsed), ('frame', last_frame)):
            if old is not None and row[name] is not None and row[name] < old:
                cap.issue('nonmonotonic_' + name, start_line, name)
        if row['kind'] == 'sample':
            if row['sample'] is not None and last_sample is not None and row['sample'] <= last_sample:
                cap.issue('nonadvancing_sample', start_line, 'sample')
            last_sample = row['sample']
        last_mono = row['monotonic_ns'] if row['monotonic_ns'] is not None else last_mono
        last_elapsed = row['elapsed_s'] if row['elapsed_s'] is not None else last_elapsed
        last_frame = row['frame'] if row['frame'] is not None else last_frame
        for name in [k for k in FRAME_METRICS if k.startswith('over')]:
            if row[name] is not None and row['frames'] is not None and row[name] > row['frames']:
                cap.issue('threshold_count_exceeds_frames', start_line, name)
        cap.rows.append(row)
        cap.last_record_kind = row['kind']
    if cap.started is None:
        cap.issue('missing_started_comment', 2, message='second line must be # started,<UTC ISO8601 time>,<Hookshot version>')
    if summary_count > 1:
        cap.issue('multiple_summaries')
    if cap.clock is None and cap.rows:
        cap.issue('mixed_or_unknown_clocks')
    if cap.final is None:
        cap.issue('no_final_summary', message='no final summary: the run did not end cleanly', severity='warning')
    return cap


def nearest(values, fraction):
    return values[max(0, math.ceil(len(values) * fraction) - 1)]


def metric_summary(values, name):
    present = sorted(v for v in values if v is not None)
    out = dict(unit=unit(name), count=len(present), missing_count=len(values) - len(present))
    out.update({k: None for k in ('mean', 'median', 'p5', 'p95', 'min', 'max')})
    if present:
        out.update(mean=statistics.fmean(present), median=statistics.median(present), p5=nearest(present, .05),
                   p95=nearest(present, .95), min=present[0], max=present[-1])
    return out


def _bound(cap, value):
    if value is None:
        return None, None
    value = str(value)
    if value.startswith('marker:'):
        name = value[7:]
        event, sep, occurrence = name.partition('@')
        found = [r for r in cap.markers if r['event'] == event]
        if sep:
            if not occurrence.isdigit() or not 1 <= int(occurrence) <= len(found):
                raise PerfError('marker_not_found', 'marker occurrence does not exist')
            found = [found[int(occurrence) - 1]]
        if len(found) != 1 or found[0]['monotonic_ns'] is None:
            raise PerfError('ambiguous_or_missing_marker', 'marker must resolve to exactly one recorded boundary; use @occurrence')
        return 'monotonic_ns', found[0]['monotonic_ns']
    prefix, sep, number = value.partition(':')
    column = {'time': 'elapsed_s', 'frame': 'frame', 'monotonic': 'monotonic_ns'}.get(prefix) if sep else 'elapsed_s'
    if column is None:
        raise PerfError('invalid_window', 'use elapsed seconds, frame:N, monotonic:S or marker:EVENT[@N]')
    try:
        if column == 'frame':
            number = _numeric('frame', number)
            if number is None:
                raise ValueError
            return column, number
        number = float(number if sep else prefix)
    except ValueError:
        raise PerfError('invalid_window', 'window endpoint must be numeric; frame endpoints must be decimal integers') from None
    if not math.isfinite(number) or number < 0 or (column == 'monotonic_ns' and number > (2**64 - 1) / 1e9):
        raise PerfError('invalid_window', 'window endpoint must be finite, nonnegative and within the clock range')
    return column, int(round(number * 1e9)) if column == 'monotonic_ns' else number


def select_window(cap, start=None, end=None):
    a, lo = _bound(cap, start)
    b, hi = _bound(cap, end)
    if a is not None and b is not None and a != b:
        raise PerfError('mixed_window_clocks', 'both window endpoints must use the same clock or frame identity')
    column = a or b or 'elapsed_s'
    if lo is not None and hi is not None and hi < lo:
        raise PerfError('reversed_window', 'window end precedes its start')
    rows = [r for r in cap.samples if r[column] is not None and (lo is None or r[column] >= lo) and (hi is None or r[column] <= hi)]
    meta = dict(column=column, start=lo, end=hi, inclusive=True, samples=len(rows),
                missing_window_coordinate=sum(r[column] is None for r in cap.samples),
                clock=cap.clock, sample_clock='OS monotonic; elapsed_s since monitor start',
                population='unweighted sample receipts; frame metrics are overlapping trailing-window observations',
                first_frame=next((r['frame'] for r in rows if r['frame'] is not None), None),
                last_frame=next((r['frame'] for r in reversed(rows) if r['frame'] is not None), None),
                first_monotonic_ns=rows[0]['monotonic_ns'] if rows else None,
                last_monotonic_ns=rows[-1]['monotonic_ns'] if rows else None)
    return rows, meta


def read_frames(path, *, max_bytes=None):
    with Path(path).open('rb') as stream:
        raw = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
    if max_bytes is not None and len(raw) > max_bytes:
        raise PerfError('frame_input_too_large', 'frame log exceeds the configured byte limit')
    partial = not raw.endswith(b'\n')
    raw = raw[:raw.rfind(b'\n') + 1]
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8')), strict=True)
        if reader.fieldnames is None or set(reader.fieldnames) != {'frame', 'time_s', 'frametime_ms'} or len(reader.fieldnames) != 3:
            raise PerfError('invalid_frame_header', 'frames.csv requires frame,time_s,frametime_ms by name')
        rows = []
        for r in reader:
            if None in r or None in r.values():
                raise ValueError
            frame = _numeric('frame', r['frame'])
            t = _numeric('time_s', r['time_s'])
            ms = _numeric('frametime_ms', r['frametime_ms'])
            if frame is None or t is None or ms is None:
                raise ValueError
            if rows and (frame != rows[-1]['frame'] + 1 or t < rows[-1]['time_s']):
                raise PerfError('frame_gap_or_reversal', 'frames.csv frame identities must be contiguous and time nondecreasing')
            rows.append(dict(frame=frame, time_s=t, frametime_ms=ms))
    except (UnicodeDecodeError, csv.Error, ValueError) as exc:
        if isinstance(exc, PerfError):
            raise
        raise PerfError('invalid_frame_row', 'invalid complete row in frames.csv') from None
    return dict(rows=rows, truncated=partial)


def join_frames(cap, frame_log, rows=None):
    if cap.clock not in CLOCKS:
        raise PerfError('join_clock_unknown', 'cannot join unlike or unknown renderer clocks')
    by_frame = {r['frame']: r for r in frame_log['rows']}
    joined = []
    for sample in cap.samples if rows is None else rows:
        frame = by_frame.get(sample['frame'])
        if frame is not None and (sample['time_s'] is None or not math.isclose(frame['time_s'], sample['time_s'], rel_tol=0, abs_tol=2e-6)):
            raise PerfError('join_time_mismatch', 'same frame number has different time_s; refusing cross-run/clock join')
        joined.append(dict(sample=sample, frame=frame))
    return joined


def frame_statistics(values):
    values = sorted(v for v in values if v is not None and math.isfinite(v) and v > 0)
    out = dict.fromkeys(FRAME_METRICS)
    out['frames'] = len(values)
    if not values:
        return out
    mean = statistics.fmean(values)
    out.update(mean_ms=mean, mean_fps=1000 / mean, max_ms=values[-1])
    for key, fraction in (('low_1_fps', .01), ('low_0_1_fps', .001)):
        out[key] = 1000 / statistics.fmean(values[-max(1, math.ceil(len(values) * fraction)):])
    for key, fraction in (('p50_ms', .5), ('p95_ms', .95), ('p99_ms', .99)):
        out[key] = nearest(values, fraction)
    for key, limit in (('over16_7', 16.7), ('over20', 20), ('over25', 25), ('over50', 50), ('over100', 100)):
        out[key] = sum(v > limit for v in values)
    return out


def verify_summary(cap, frame_log=None):
    out = dict(status='cannot_tell', reason=None, clock=cap.clock, population='whole captured session; positive finite renderer intervals',
               first_frame=None, last_frame=None, recomputed=None, mismatches=[], missing_metrics=[])
    if cap.final is None:
        out['reason'] = 'no final summary: the run did not end cleanly'
        return out
    if frame_log is None:
        out['reason'] = 'raw frames.csv required; overlapping sampled aggregates cannot reconstruct percentiles or lows'
        return out
    starts = [r for r in cap.markers if r['event'] == 'capture_start']
    if len(starts) != 1 or starts[0]['frame'] is None or cap.final['frame'] is None:
        out['reason'] = 'exact capture_start and final frame boundaries required'
        return out
    start, end = starts[0], cap.final
    join = join_frames(cap, frame_log, [start, end])
    if any(r['frame'] is None for r in join):
        out['reason'] = 'frame log does not cover both capture boundaries'
        return out
    first, last = start['frame'] + 1, end['frame']
    frames = [r for r in frame_log['rows'] if first <= r['frame'] <= last]
    out.update(first_frame=first, last_frame=last)
    if len(frames) != max(0, last - first + 1):
        out['reason'] = 'frame log does not cover every captured frame'
        return out
    stats = frame_statistics([r['frametime_ms'] for r in frames])
    out['recomputed'] = stats
    for key, value in stats.items():
        reported = end[key]
        if reported is None or value is None:
            out['missing_metrics'].append(key)
        elif not (reported == value if key in INTEGERS else math.isclose(reported, value, rel_tol=1e-6, abs_tol=1e-6)):
            out['mismatches'].append(dict(metric=key, reported=reported, recomputed=value, unit=unit(key)))
    out['status'] = 'mismatch' if out['mismatches'] else 'cannot_tell' if out['missing_metrics'] else 'match'
    out['reason'] = 'some summary metrics unavailable' if out['missing_metrics'] else None
    out['tolerance'] = 'counts exact; floating metrics abs 1e-6 or rel 1e-6 (CSV decimal rounding)'
    return out


def _evidence(row, fields):
    return {k: row.get(k) for k in ('_line', 'sample', 'monotonic_ns', 'elapsed_s', 'frame', 'clock', *fields)}


def _finding(name, evidence, covered, required, rule, *, missing=0):
    status = 'suspected' if evidence else 'cannot_tell' if not covered or missing else 'not_observed'
    return dict(check=name, status=status, message=('suspicion only; not a bottleneck or causal verdict' if evidence else
        'cannot tell: needed observations unavailable or insufficient' if status == 'cannot_tell' else
        'suspicion not observed in available selected samples; not proof of absence'), rule=rule,
        required_columns=required, unavailable_rows=missing, evidence=evidence[:8], evidence_total=len(evidence))


def decode_throttle_mask(mask, gpu_vendor):
    """Only the game's documented NVIDIA/NVML producer has a supported mask schema."""
    known = gpu_vendor == '0x10de'
    decoded, unknown, suspect = [], [], []
    for exponent in range(mask.bit_length()):
        bit = 1 << exponent
        if not mask & bit:
            continue
        if known and bit in NVML_CLOCK_REASONS:
            label = f'{NVML_CLOCK_REASONS[bit]} ({bit:#x})'
            decoded.append(label)
            if bit & NVML_THERMAL_POWER_BITS:
                suspect.append(label)
        else:
            unknown.append(f'unknown bit {bit:#x}')
    return dict(decoded_bits=decoded, unknown_bits=unknown, suspicion_bits=suspect)


def thermal_or_power_throttling(rows, *, gpu_vendor=None):
    evidence, observations = [], {}
    masks = [r for r in rows if r.get('gpu_throttle_mask') is not None]
    unknown_rows = 0
    for r in masks:
        mask = r['gpu_throttle_mask']
        decoded = decode_throttle_mask(mask, gpu_vendor)
        if gpu_vendor != '0x10de' or decoded['unknown_bits']:
            unknown_rows += 1
        if mask not in observations:
            observations[mask] = dict(**_evidence(r, ['gpu_throttle_mask']), **decoded, sample_count=0)
        observations[mask]['sample_count'] += 1
        if decoded['suspicion_bits']:
            evidence.append(dict(**_evidence(r, ['gpu_throttle_mask']), **decoded))
    pairs = 0
    for device in ('gpu', 'cpu'):
        clock, temp = device + '_clock_mhz', device + '_temp_c'
        for a, b in zip(rows, rows[1:]):
            if any(r.get(k) is None for r in (a, b) for k in (clock, temp)):
                continue
            pairs += 1
            if a[clock] > 0 and b[clock] <= .9 * a[clock] and b[temp] >= a[temp] + 5:
                evidence += [dict(**_evidence(r, [clock, temp]), reason='clock fall with temperature rise') for r in (a, b)]
    missing = unknown_rows + len(rows) - len(masks)
    result = _finding('thermal_or_power_throttling', evidence, (bool(masks) and gpu_vendor == '0x10de') or pairs > 0,
        ['gpu_vendor provenance', 'gpu_throttle_mask', 'gpu_clock_mhz', 'gpu_temp_c', 'cpu_clock_mhz', 'cpu_temp_c'],
        'documented NVIDIA/NVML software power cap (0x4), software thermal (0x20), hardware thermal (0x40), '
        'or hardware power brake (0x80); OR adjacent clock fall >=10% with temperature rise >=5 C; '
        'idle, clock settings, generic HW slowdown, sync boost, board limit, reliability and unknown bits are not thermal/power evidence alone',
        missing=missing)
    result['mask_interpretation'] = {
        'status': 'cannot_tell' if not masks or gpu_vendor != '0x10de' or unknown_rows else 'decoded',
        'gpu_vendor': gpu_vendor,
        'schema': 'NVML current clocks event/throttle reasons' if gpu_vendor == '0x10de' else None,
        'reason': 'GPU vendor absent or its throttle-mask semantics are not documented by this game' if gpu_vendor != '0x10de' else
                  'gpu_throttle_mask unavailable in selected samples' if not masks else
                  'unknown bit(s) retained without interpreting them as throttling' if unknown_rows else None,
        'observations': list(observations.values())[:8], 'distinct_masks': len(observations),
    }
    return result


def video_memory_budget(rows):
    usable = [r for r in rows if r.get('vram_used_bytes') is not None and r.get('vram_budget_bytes') is not None and r['vram_budget_bytes'] > 0]
    hits = [_evidence(r, ['vram_used_bytes', 'vram_budget_bytes']) for r in usable if r['vram_used_bytes'] >= .95 * r['vram_budget_bytes']]
    return _finding('video_memory_budget', hits, bool(usable), ['vram_used_bytes', 'vram_budget_bytes'],
        'VK driver usage >=95% of its positive moving budget; not device capacity or texture budget', missing=len(rows) - len(usable))


def system_memory_pressure(rows):
    ram = [r for r in rows if r.get('ram_available_bytes') is not None]
    evidence = [_evidence(r, ['ram_available_bytes']) for r in ram if r['ram_available_bytes'] <= 512 * 2**20]
    swap = [r for r in rows if r.get('swap_used_bytes') is not None]
    low_ram = [r for r in ram if r['ram_available_bytes'] <= 2 * 2**30]
    if len(swap) >= 2 and swap[-1]['swap_used_bytes'] - swap[0]['swap_used_bytes'] >= 64 * 2**20 and low_ram:
        evidence += [_evidence(r, ['swap_used_bytes']) for r in (swap[0], swap[-1])]
        evidence += [_evidence(r, ['ram_available_bytes']) for r in low_ram]
    return _finding('system_memory_pressure', evidence, bool(ram) or len(swap) >= 2,
        ['ram_available_bytes', 'swap_used_bytes'],
        'system available RAM <=512 MiB (total capacity absent), OR first-to-last swap growth >=64 MiB '
        'AND available RAM <=2 GiB in a selected sample; whole PC, never proof of pressure on the game',
        missing=max(len(rows) - len(ram), len(rows) - len(swap)))


def cpu_limited(rows):
    fields = ['cpu_thread_pct', 'gpu_use_pct', 'thread_name']
    valid = [r for r in rows if all(r.get(k) is not None for k in fields) and r.get('thread_name') not in ('', 'na')]
    evidence = []
    for a, b in zip(rows, rows[1:]):
        if all(r.get('cpu_thread_pct') is not None and r.get('gpu_use_pct') is not None and r.get('thread_name') not in (None, '', 'na') for r in (a, b)):
            if a['thread_name'] == b['thread_name'] and all(r['cpu_thread_pct'] >= 90 and r['gpu_use_pct'] <= 30 for r in (a, b)):
                evidence += [_evidence(r, fields) for r in (a, b)]
    return _finding('cpu_limited', evidence, len(valid) >= 2, fields,
        'two adjacent samples: same named busiest thread >=90% of one logical CPU, whole GPU <=30%; cap/loading/other work remain alternatives',
        missing=len(rows) - len(valid))


def gpu_limited(rows):
    valid = [r for r in rows if r.get('gpu_use_pct') is not None]
    evidence = []
    for a, b in zip(rows, rows[1:]):
        if all(r.get('gpu_use_pct') is not None and r['gpu_use_pct'] >= 95 for r in (a, b)):
            evidence += [_evidence(r, ['gpu_use_pct']) for r in (a, b)]
    return _finding('gpu_limited', evidence, len(valid) >= 2, ['gpu_use_pct'],
        'two adjacent whole-device GPU-use samples >=95%; other workloads and driver sampling periods unresolved', missing=len(rows) - len(valid))


def streaming_stall(rows, frame_log=None):
    required = ['disk_read_bytes', 'monotonic_ns', 'frame', 'time_s', 'frames.csv:frametime_ms']
    rule = ('at least 3 adjacent disk-rate intervals; read rate >=max(16 MiB/s, 4*median rate) and a renderer interval '
            '>max(50 ms, 2*median frame interval) in the same sample-frame bracket; coarse correlation, not disk causation; Windows reads may be cached')
    if frame_log is None:
        return _finding('streaming_stall', [], False, required, rule)
    rates, missing = [], 0
    frames = frame_log['rows']
    by_frame = {r['frame']: r for r in frames}
    indexes = {r['frame']: i for i, r in enumerate(frames)}
    for a, b in zip(rows, rows[1:]):
        if any(r.get(k) is None for r in (a, b) for k in ('disk_read_bytes', 'monotonic_ns', 'frame', 'time_s')):
            missing += 1
            continue
        dt = (b['monotonic_ns'] - a['monotonic_ns']) / 1e9
        delta = b['disk_read_bytes'] - a['disk_read_bytes']
        endpoints = [by_frame.get(r['frame']) for r in (a, b)]
        if dt <= 0 or delta < 0 or b['frame'] <= a['frame'] or any(f is None or abs(f['time_s'] - r['time_s']) > 2e-6 for f, r in zip(endpoints, (a, b))):
            missing += 1
            continue
        bracket = [f['frametime_ms'] for f in frames[indexes[a['frame']] + 1:indexes[b['frame']] + 1]
                   if f['frametime_ms'] > 0]
        if bracket:
            rates.append((a, b, delta / dt, bracket))
    evidence = []
    if len(rates) >= 3:
        disk_limit = max(16 * 2**20, 4 * statistics.median(r[2] for r in rates))
        frame_limit = max(50, 2 * statistics.median(ms for r in rates for ms in r[3]))
        for a, b, rate, bracket in rates:
            if rate >= disk_limit and max(bracket) > frame_limit:
                evidence.append(dict(start=_evidence(a, ['disk_read_bytes']), end=_evidence(b, ['disk_read_bytes']),
                    read_bytes_per_s=rate, read_threshold_bytes_per_s=disk_limit, max_frame_ms=max(bracket),
                    frame_threshold_ms=frame_limit, frame_interval_first=a['frame'] + 1, frame_interval_last=b['frame']))
    return _finding('streaming_stall', evidence, len(rates) >= 3, required, rule, missing=missing)


def suspicions(rows, frame_log=None, *, gpu_vendor=None):
    return [thermal_or_power_throttling(rows, gpu_vendor=gpu_vendor)] + [
        fn(rows) for fn in (video_memory_budget, system_memory_pressure, cpu_limited, gpu_limited)] + [streaming_stall(rows, frame_log)]


def summarize(cap, start=None, end=None, frame_log=None):
    rows, window = select_window(cap, start, end)
    if frame_log is not None:
        joined = join_frames(cap, frame_log, rows)
        join = dict(matched=sum(r['frame'] is not None for r in joined), unmatched=sum(r['frame'] is None for r in joined),
                    clock=cap.clock, rule='exact frame identity plus time_s within 2 microseconds; no interpolation',
                    frame_log_truncated=frame_log['truncated'])
    else:
        join = dict(matched=None, unmatched=None, reason='frames.csv unavailable')
    verification = verify_summary(cap, frame_log)
    return dict(schema_version=1, source=cap.source, started=cap.started, clock=cap.clock, complete=cap.final is not None,
        gpu_vendor=cap.gpu_vendor, gpu_vendor_source=cap.gpu_vendor_source,
        completion='explicit final summary' if cap.final is not None else 'no final summary: the run did not end cleanly',
        window=window, metrics={k: metric_summary([r[k] for r in rows], k) for k in METRICS},
        summary_verification=verification, join=join, suspicions=suspicions(rows, frame_log, gpu_vendor=cap.gpu_vendor),
        issues=cap.issues, issue_count=cap.issue_count, omitted_issues=max(0, cap.issue_count - len(cap.issues)), notice=NOTICE)


def compare(captures, start=None, end=None):
    if len(captures) < 2:
        raise PerfError('two_runs_required', 'compare requires two or more runs')
    clocks = {c.clock for c in captures}
    if None in clocks or len(clocks) != 1:
        raise PerfError('unlike_clocks', 'refusing comparison of unknown or unlike renderer clocks')
    if any(any(i['severity'] == 'error' for i in c.issues) for c in captures):
        raise PerfError('contract_violations', 'check reported contract violations before comparing readings')
    reports = [summarize(c, start, end) for c in captures]
    metrics = {}
    for key in METRICS:
        values = [r['metrics'][key] for r in reports]
        differences = []
        for b in values[1:]:
            a = values[0]
            delta = b['mean'] - a['mean'] if a['mean'] is not None and b['mean'] is not None else None
            gap = dict(delta_mean=delta, unit=unit(key), state='cannot_tell', rule=SPREAD_RULE)
            if key not in GAUGES:
                gap['state'] = 'arithmetic_only_no_speed_verdict' if key in FRAME_METRICS else 'arithmetic_only_not_a_gauge'
            elif min(a['count'], b['count']) >= 3:
                spread_a, spread_b = a['max'] - a['min'], b['max'] - b['min']
                outside = (b['min'] > a['max'] or a['min'] > b['max']) and abs(delta) > max(spread_a, spread_b)
                gap.update(state=('observed_higher_beyond_sample_spread' if delta > 0 else 'observed_lower_beyond_sample_spread') if outside else 'within_sample_spread',
                           range_a=[a['min'], a['max']], range_b=[b['min'], b['max']], spread_a=spread_a, spread_b=spread_b)
            differences.append(gap)
        metrics[key] = dict(unit=unit(key), runs=values, differences_from_first=differences)
    return dict(runs=[dict(source=c.source, window=r['window'], complete=r['complete'], issues=r['issues']) for c, r in zip(captures, reports)],
                metrics=metrics, rule=SPREAD_RULE, notice=NOTICE)


def attach(run, *, start=None, end=None):
    """Optional integration boundary: absent older-build file changes nothing."""
    path = Path(run) / 'perf.csv'
    if not path.is_file():
        return None
    try:
        cap = read_perf(path)
        frames = read_frames(path.with_name('frames.csv')) if path.with_name('frames.csv').is_file() else None
        result = summarize(cap, start, end, frames)
        result['artifact'] = 'perf.csv'
        return result
    except (PerfError, OSError) as exc:
        return dict(artifact='perf.csv', status='cannot_tell', error=getattr(exc, 'code', 'input_unavailable'), notice=NOTICE)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('summary', 'check', 'compare'):
        p = sub.add_parser(command)
        p.add_argument('files', nargs='+' if command == 'compare' else 1)
        p.add_argument('--json', action='store_true')
        if command != 'check':
            p.add_argument('--from', dest='start')
            p.add_argument('--to', dest='end')
        if command != 'compare':
            p.add_argument('--frames', help='explicit same-run renderer trace; default sibling frames.csv')
            p.add_argument('--device-log', help='explicit same-run device log for older CSVs lacking # gpu_vendor; never auto-discovered')
    args = parser.parse_args(argv)
    try:
        captures = [read_perf(f, device_log=getattr(args, 'device_log', None)) for f in args.files]
        if args.command == 'compare':
            result = compare(captures, args.start, args.end)
        else:
            cap = captures[0]
            path = Path(args.frames) if args.frames else Path(args.files[0]).with_name('frames.csv')
            frames = read_frames(path) if path.is_file() else None
            if args.frames and frames is None:
                raise PerfError('frame_input_missing', 'explicit frames.csv does not exist')
            result = summarize(cap, getattr(args, 'start', None), getattr(args, 'end', None), frames)
        if args.json:
            print(json.dumps(result, indent=2, allow_nan=False))
        elif args.command == 'compare':
            print(NOTICE)
            print(SPREAD_RULE)
            print('metric [unit]\t' + '\t'.join(args.files) + '\tdifferences from first (mean)')
            for key, item in result['metrics'].items():
                cells = ['na' if r['mean'] is None else f"{r['mean']:.6g} (n={r['count']}, missing={r['missing_count']}, range={r['min']:.6g}..{r['max']:.6g})" for r in item['runs']]
                diffs = ['na (' + d['state'] + ')' if d['delta_mean'] is None else f"{d['delta_mean']:+.6g} ({d['state']})" for d in item['differences_from_first']]
                print(f"{key} [{item['unit']}]\t" + '\t'.join(cells) + '\t' + '; '.join(diffs))
            for run in result['runs']:
                print(json.dumps(run, allow_nan=False))
        else:
            print(result['completion'])
            print(NOTICE)
            print('window: ' + json.dumps(result['window']))
            if args.command == 'summary':
                print('metric [unit]\tcount\tmissing\tmean\tmedian\tp5\tp95\tmin\tmax')
                for name, metric in result['metrics'].items():
                    print(f"{name} [{metric['unit']}]\t" + '\t'.join('na' if metric[k] is None else f'{metric[k]:.6g}' for k in ('count', 'missing_count', 'mean', 'median', 'p5', 'p95', 'min', 'max')))
                for suspicion in result['suspicions']:
                    print(json.dumps(suspicion, ensure_ascii=True))
            for issue in result['issues']:
                print(f"{issue['code']}: line={issue['line']} metric={issue['metric']} {issue['message']}")
            print('summary verification: ' + json.dumps(result['summary_verification']))
            if result['omitted_issues']:
                print(f"{result['omitted_issues']} additional issues omitted")
        if args.command == 'check':
            return int(bool(result['issues']) or result['summary_verification']['status'] == 'mismatch')
        return 0
    except (PerfError, OSError) as exc:
        error = dict(error=getattr(exc, 'code', 'input_unavailable'), message=str(exc) if isinstance(exc, PerfError) else 'cannot read input file')
        print(json.dumps(error) if args.json else error['error'] + ': ' + error['message'], file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
