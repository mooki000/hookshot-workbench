#!/usr/bin/env python3
"""Authored game-free fixtures. No game, GPU, sensor, or timing authorization."""
import contextlib
import csv
import importlib.util
import io
import json
import math
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('hs_perf', Path(__file__).with_name('hs_perf.py'))
perf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(perf)


def row(kind='sample', sample=1, **values):
    result = dict.fromkeys(perf.COLUMNS, None)
    result.update(kind=kind, sample=sample, monotonic_ns=(sample + 1) * 10**9, elapsed_s=sample,
                  game_frame=sample, renderer_present=sample, frame=sample, time_s=sample / 100,
                  clock='headless-renderer', thread_name='render', event='', detail='')
    result.update(values)
    return result


def fixture(samples=None, *, columns=None, end=True, comments=True, gpu_vendor=None):
    columns = columns or perf.COLUMNS
    samples = samples if samples is not None else [row(sample=i, gpu_temp_c=40+i, cpu_thread_pct=60, gpu_use_pct=30) for i in range(1, 5)]
    output = io.StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(['# hookshot_perf', '1', *columns])
    writer.writerow(['# started', '2026-11-12T00:00:00Z', 'authored-test'])
    if gpu_vendor is not None:
        writer.writerow(['# gpu_vendor', gpu_vendor])
    start = row('marker', 0, event='capture_start', detail='example, "quoted"\n#inside a quoted field')
    records = [start, *samples]
    if end:
        final = row('summary', samples[-1]['sample'] if samples else 0, event='end', detail='complete')
        records += [row('marker', final['sample'], event='capture_end'), final]
    for record in records:
        if comments:
            output.write('# ignorable comment,not a record\n')
        writer.writerow(['na' if record.get(k) is None else record[k] for k in columns])
    return output.getvalue()


def frames(count=4, *, values=None):
    values = values or [10] * count
    return dict(rows=[dict(frame=0, time_s=0.0, frametime_ms=0)] +
                [dict(frame=i, time_s=i / 100, frametime_ms=v) for i, v in enumerate(values, 1)], truncated=False)


def idle_reliability_ample_ram_fixture():
    """Authored equivalent shapes, not copied game thread names/timestamps/build identity."""
    samples = []
    for i in range(1, 70):
        samples.append(row(sample=i, frame=None if i <= 6 else i, time_s=None if i <= 6 else i / 100,
            thread_name='worker', gpu_throttle_mask=0x400 if i % 7 == 0 else 0x1,
            gpu_temp_c=33 + i % 3, gpu_clock_mhz=2700 if i < 5 else 300,
            cpu_temp_c=87 + i % 2, cpu_clock_mhz=5500,
            ram_available_bytes=(47 + i % 15) * 2**30,
            swap_used_bytes=10 * 2**30 + 590 * 2**20 * (i - 1) // 68))
    return fixture(samples, gpu_vendor='0x10de')


class ReaderTests(unittest.TestCase):
    def test_columns_by_name_and_missing_not_zero(self):
        cap = perf.parse_perf(fixture(columns=list(reversed(perf.COLUMNS))))
        self.assertEqual(len(cap.samples), 4)
        self.assertEqual(len(cap.markers), 2)
        self.assertIsNone(cap.samples[0]['commit_bytes'])
        self.assertIn('#inside a quoted field', cap.markers[0]['detail'])
        self.assertEqual(cap.issues, [])
        s = perf.summarize(cap)
        self.assertEqual(s['metrics']['commit_bytes']['missing_count'], 4)
        self.assertIsNone(s['metrics']['commit_bytes']['mean'])
        self.assertEqual(s['metrics']['gpu_temp_c']['unit'], 'C')
        self.assertEqual(s['metrics']['gpu_temp_c']['mean'], 42.5)
        self.assertEqual(s['metrics']['gpu_temp_c']['median'], 42.5)
        self.assertEqual(s['metrics']['gpu_temp_c']['p5'], 41)
        self.assertEqual(s['metrics']['gpu_temp_c']['p95'], 44)
        self.assertEqual(s['summary_verification']['status'], 'cannot_tell')
        self.assertIn('raw frames.csv', s['summary_verification']['reason'])

    def test_windows_time_frame_and_marker(self):
        cap = perf.parse_perf(fixture())
        for start, end in [('2', '3'), ('time:2', 'time:3'), ('frame:2', 'frame:3'), ('monotonic:3', 'monotonic:4')]:
            rows, window = perf.select_window(cap, start, end)
            self.assertEqual([r['sample'] for r in rows], [2, 3])
            self.assertTrue(window['inclusive'])
        rows, _ = perf.select_window(cap, 'marker:capture_start', 'marker:capture_end')
        self.assertEqual(len(rows), 4)
        cap.rows.insert(1, dict(cap.markers[0]))
        with self.assertRaisesRegex(perf.PerfError, 'exactly one'):
            perf.select_window(cap, 'marker:capture_start', 'marker:capture_end')
        self.assertEqual(len(perf.select_window(cap, 'marker:capture_start@2', 'marker:capture_end')[0]), 4)
        for a, b in [('frame:2', '3'), ('4', '2'), ('nan', None), ('frame:2.5', None), ('marker:missing', None)]:
            with self.assertRaises(perf.PerfError):
                perf.select_window(cap, a, b)
        self.assertEqual(perf.summarize(cap, '20')['metrics']['rss_bytes']['count'], 0)

    def test_truncated_row_retains_every_complete_row(self):
        source = fixture(end=False)
        cap = perf.parse_perf(source + 'sample,5,60000,')
        self.assertEqual(len(cap.samples), 4)
        self.assertIn('truncated_row', [i['code'] for i in cap.issues])
        self.assertEqual(perf.summarize(cap)['completion'], 'no final summary: the run did not end cleanly')
        cap = perf.parse_perf(source.encode() + b'sample,5,"cut\nutf8:\xe2\x82')
        self.assertEqual(len(cap.samples), 4)
        self.assertIn('malformed_csv', [i['code'] for i in cap.issues])

    def test_unknown_newer_version_refuses(self):
        with self.assertRaisesRegex(perf.PerfError, 'version 2.*only v1'):
            perf.parse_perf(fixture().replace('# hookshot_perf,1,', '# hookshot_perf,2,', 1))

    def test_named_contract_violations(self):
        samples = [row(sample=2, gpu_use_pct=101), row(sample=1, rss_bytes='oops'), row(sample=3, clock='game'),
                   row(sample=2, gpu_temp_c=0), row(sample=1, frame=1, time_s=None, frames=1, over20=2)]
        cap = perf.parse_perf(fixture(samples))
        codes = {i['code'] for i in cap.issues}
        self.assertTrue({'invalid_number', 'invalid_clock', 'nonadvancing_sample', 'nonmonotonic_frame',
                         'incomplete_frame_identity', 'threshold_count_exceeds_frames'} <= codes)
        for mutation, expected in [(lambda s: s.replace(',gpu_use_pct,', ',kind,', 1), 'duplicate_columns'),
                                   (lambda s: s.replace(',gpu_use_pct,', ',new_field,', 1), 'missing_columns')]:
            with self.assertRaises(perf.PerfError) as error:
                perf.parse_perf(mutation(fixture()))
            self.assertEqual(error.exception.code, expected)
        cap = perf.parse_perf(fixture().replace('# started,', '# other,', 1))
        self.assertIn('missing_started_comment', [i['code'] for i in cap.issues])
        cap = perf.parse_perf(fixture() + 'sample,too,few\n')
        self.assertIn('row_width', [i['code'] for i in cap.issues])
        self.assertIsNone(cap.final)

    def test_metadata_and_quoted_utf8_are_data(self):
        cap = perf.parse_perf(fixture([row(thread_name='rendu λ', detail='text\rinside, \"quotes\"')]))
        self.assertEqual(cap.issues, [])
        self.assertEqual(cap.samples[0]['thread_name'], 'rendu λ')
        self.assertEqual(perf.summarize(cap)['started']['version'], 'authored-test')
        cap = perf.parse_perf(fixture() + 'cut-after-summary')
        self.assertIsNone(cap.final)
        self.assertIn('no_final_summary', [i['code'] for i in cap.issues])

    def test_gpu_vendor_comment_provenance_and_conflicts(self):
        cap = perf.parse_perf(fixture(gpu_vendor='0x10DE'))
        self.assertEqual(cap.gpu_vendor, '0x10de')
        self.assertEqual(cap.gpu_vendor_source, 'perf.csv comment line 3')
        self.assertEqual(perf.summarize(cap)['gpu_vendor'], '0x10de')
        self.assertIsNone(perf.parse_perf(fixture()).gpu_vendor)
        self.assertIsNone(perf.parse_perf(fixture(gpu_vendor='NVIDIA')).gpu_vendor)
        self.assertEqual(perf.parse_perf(fixture(gpu_vendor='0x0000')).gpu_vendor, '0x0000')
        for value in ('0x10000', '0x10005', '0xffffffff', '0x8086'):
            self.assertEqual(perf.parse_perf(fixture(gpu_vendor=value)).gpu_vendor, value)
        self.assertEqual(perf.parse_perf(fixture(gpu_vendor='0x000010DE')).gpu_vendor, '0x10de')
        self.assertIsNone(perf.parse_perf(fixture(gpu_vendor='0x100000000')).gpu_vendor)
        cap = perf.parse_perf(fixture(gpu_vendor='0x10de') + '# gpu_vendor,0x1002\n# gpu_vendor,0x10de\n')
        self.assertIsNone(cap.gpu_vendor)
        self.assertTrue(cap.gpu_vendor_conflict)
        self.assertIn('conflicting', cap.gpu_vendor_source)

    def test_device_log_provenance_is_explicit_and_unambiguous(self):
        with tempfile.TemporaryDirectory() as folder:
            path, log = Path(folder) / 'perf.csv', Path(folder) / 'hookshot_log.txt'
            path.write_text(fixture([row(gpu_throttle_mask=1)]))
            log.write_text('[hos worker] vk: device 7: NVIDIA Fixture GPU, Vulkan 1.4, driver fixture\n')
            self.assertIsNone(perf.read_perf(path).gpu_vendor)  # Never read an unregistered sibling.
            cap = perf.read_perf(path, device_log=log)
            self.assertEqual(cap.gpu_vendor, '0x10de')
            self.assertTrue(cap.gpu_vendor_source.endswith(':1'))
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                self.assertEqual(perf.main(['summary', str(path), '--device-log', str(log), '--json']), 0)
            report = json.loads(stream.getvalue())
            self.assertEqual(report['suspicions'][0]['status'], 'not_observed')
            path.write_text(fixture(gpu_vendor='0x1002'))
            self.assertIsNone(perf.read_perf(path, device_log=log).gpu_vendor)
            path.write_text(fixture())
            log.write_text(log.read_text() + '[hos worker] vk: device 8: AMD Fixture GPU, Vulkan 1.4\n')
            self.assertIsNone(perf.read_perf(path, device_log=log).gpu_vendor)

    def test_frame_selectors_preserve_large_integer_identity(self):
        cap = perf.parse_perf(fixture([row(frame=2**60 + 1)]))
        selected, _ = perf.select_window(cap, f'frame:{2**60 + 1}', f'frame:{2**60 + 1}')
        self.assertEqual(len(selected), 1)
        selected, _ = perf.select_window(cap, f'frame:{2**60}', f'frame:{2**60}')
        self.assertEqual(selected, [])

    def test_nan_inf_empty_negative_are_not_na(self):
        for value in ('nan', 'inf', '-1', ''):
            cap = perf.parse_perf(fixture([row(gpu_use_pct=value)]))
            self.assertEqual(len(cap.samples), 0)
            self.assertIn('invalid_number', [i['code'] for i in cap.issues])

    def test_integer_identity_preserves_precision(self):
        n = 2**60 + 1
        cap = perf.parse_perf(fixture([row(monotonic_ns=n, disk_read_bytes=n)]))
        self.assertEqual(cap.samples[0]['monotonic_ns'], n)
        self.assertEqual(cap.samples[0]['disk_read_bytes'], n)

    def test_frame_math_ceil_nearest_rank_strict_thresholds(self):
        values = [10] * 100 + [100]
        stats = perf.frame_statistics([0, float('nan'), *values])
        self.assertEqual(stats['frames'], 101)
        self.assertAlmostEqual(stats['low_1_fps'], 1000/55)
        self.assertEqual(stats['low_0_1_fps'], 10)
        self.assertEqual(stats['p99_ms'], 10)
        self.assertEqual(stats['over100'], 0)
        thresholds = perf.frame_statistics([16.7, 20, 25, 50, 100])
        self.assertEqual([thresholds[k] for k in ('over16_7', 'over20', 'over25', 'over50', 'over100')], [4, 3, 2, 1, 0])
        self.assertIsNone(perf.frame_statistics([])['mean_ms'])

    def test_recomputes_summary_and_detects_mismatch(self):
        cap = perf.parse_perf(fixture())
        cap.final.update(perf.frame_statistics([10] * 4))
        self.assertEqual(perf.verify_summary(cap, frames())['status'], 'match')
        cap.final['p95_ms'] = 15
        result = perf.verify_summary(cap, frames())
        self.assertEqual(result['status'], 'mismatch')
        self.assertEqual(result['mismatches'][0]['metric'], 'p95_ms')
        self.assertEqual((result['first_frame'], result['last_frame']), (1, 4))
        cap.final['p95_ms'] = None
        self.assertEqual(perf.verify_summary(cap, frames())['status'], 'cannot_tell')
        cap.markers[0]['frame'] = None
        self.assertEqual(perf.verify_summary(cap, frames())['status'], 'cannot_tell')

    def test_join_uses_frame_not_game_or_present_numbers(self):
        cap = perf.parse_perf(fixture())
        cap.samples[0].update(game_frame=90, renderer_present=70)
        joined = perf.join_frames(cap, frames())
        self.assertEqual(joined[0]['frame']['frame'], 1)
        self.assertEqual(len(joined), 4)
        partial = frames(3)
        self.assertIsNone(perf.join_frames(cap, partial)[-1]['frame'])
        cap.samples[0]['time_s'] = 100
        with self.assertRaisesRegex(perf.PerfError, 'different time_s'):
            perf.join_frames(cap, frames())

    def test_read_frames_by_name_and_cut_tail(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'frames.csv'
            path.write_text('frametime_ms,time_s,frame\n0,0,0\n10,.01,1\n10,.02,2\n10,.0')
            data = perf.read_frames(path)
            self.assertTrue(data['truncated'])
            self.assertEqual(data['rows'][-1]['frame'], 2)
            path.write_text('frame,time_s,frametime_ms\n0,0,0\n2,.02,10\n')
            with self.assertRaisesRegex(perf.PerfError, 'contiguous'):
                perf.read_frames(path)

    def test_file_bound_and_issue_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'perf.csv'
            path.write_text(fixture())
            with self.assertRaisesRegex(perf.PerfError, 'byte limit'):
                perf.read_perf(path, max_bytes=10)
        cap = perf.parse_perf(fixture() + 'broken\n' * 120)
        self.assertEqual(len(cap.issues), 100)
        self.assertGreater(cap.issue_count, 100)

    def test_optional_attach_is_inert_and_keeps_window(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(perf.attach(folder))
            path = Path(folder) / 'perf.csv'
            path.write_text(fixture())
            result = perf.attach(folder, start='frame:2', end='frame:3')
            self.assertEqual(result['window']['samples'], 2)
            path.write_text('# hookshot_perf,2,kind\n')
            self.assertEqual(perf.attach(folder)['error'], 'unsupported_version')

    def test_cli_summary_check_compare(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder)/'a.csv', Path(folder)/'b.csv'
            a.write_text(fixture())
            b.write_text(fixture([row(sample=i, gpu_temp_c=70+i) for i in range(1, 5)]))
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = perf.main(['summary', str(a), '--from', 'frame:2', '--to', 'frame:3', '--json'])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stream.getvalue())['window']['samples'], 2)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                self.assertEqual(perf.main(['check', str(a)]), 0)
            self.assertIn('cannot_tell', stream.getvalue())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(perf.main(['compare', str(a), str(b), '--json']), 0)
            a.write_text(fixture(end=False) + 'partial')
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(perf.main(['check', str(a)]), 1)
            a.write_text('# hookshot_perf,2,kind\n')
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(perf.main(['check', str(a), '--json']), 2)
            self.assertEqual(json.loads(err.getvalue())['error'], 'unsupported_version')


class ComparisonTests(unittest.TestCase):
    def captures(self, *groups):
        return [perf.parse_perf(fixture([row(sample=i, gpu_temp_c=v, mean_ms=v) for i, v in enumerate(values, 1)])) for values in groups]

    def difference(self, *groups):
        return perf.compare(self.captures(*groups))['metrics']['gpu_temp_c']['differences_from_first']

    def test_difference_exceeds_each_runs_full_spread(self):
        result = self.difference([40, 41, 42], [60, 61, 62], [20, 21, 22])
        self.assertEqual(result[0]['state'], 'observed_higher_beyond_sample_spread')
        self.assertEqual(result[1]['state'], 'observed_lower_beyond_sample_spread')
        self.assertEqual(result[0]['delta_mean'], 20)
        self.assertEqual(result[0]['spread_a'], 2)

    def test_overlap_touching_and_small_gap_do_not_qualify(self):
        for a, b in [([40, 41, 42], [42, 43, 44]), ([40, 41, 45], [42, 44, 46]), ([1, 1, 10], [11, 11, 11])]:
            self.assertEqual(self.difference(a, b)[0]['state'], 'within_sample_spread')

    def test_missing_and_singletons_are_honest(self):
        self.assertEqual(self.difference([40, None, None], [60, 61, 62])[0]['state'], 'cannot_tell')
        report = perf.compare(self.captures([40, None, 41, 42], [60, 61, 62]))
        self.assertEqual(report['metrics']['gpu_temp_c']['runs'][0]['missing_count'], 1)
        self.assertIn('No speed verdict', report['notice'])
        self.assertEqual(report['metrics']['mean_ms']['differences_from_first'][0]['state'], 'arithmetic_only_no_speed_verdict')

    def test_unlike_clocks_or_invalid_data_refuse(self):
        caps = self.captures([40, 41, 42], [60, 61, 62])
        for r in caps[1].rows:
            r['clock'] = 'swapchain'
        with self.assertRaisesRegex(perf.PerfError, 'unlike'):
            perf.compare(caps)
        caps = self.captures([40, 41, 42], [60, 61, 62])
        caps[0].issue('invalid_number')
        with self.assertRaisesRegex(perf.PerfError, 'violations'):
            perf.compare(caps)


class SuspicionTests(unittest.TestCase):
    def test_every_check_missing_cannot_tell(self):
        rows = [row(sample=i) for i in range(1, 5)]
        for result in perf.suspicions(rows):
            self.assertEqual(result['status'], 'cannot_tell', result['check'])
            self.assertIn('cannot tell', result['message'])
            self.assertEqual(result['evidence'], [])

    def test_throttle_mask_and_clock_temperature_trend(self):
        result = perf.thermal_or_power_throttling([row(gpu_throttle_mask=4)], gpu_vendor='0x10de')
        self.assertEqual(result['status'], 'suspected')
        self.assertEqual(result['evidence'][0]['gpu_throttle_mask'], 4)
        rows = [row(sample=1, gpu_clock_mhz=2000, gpu_temp_c=70), row(sample=2, gpu_clock_mhz=1800, gpu_temp_c=75)]
        self.assertEqual(perf.thermal_or_power_throttling(rows)['status'], 'suspected')
        rows[1]['gpu_temp_c'] = None
        self.assertEqual(perf.thermal_or_power_throttling(rows)['status'], 'cannot_tell')

    def test_nvml_only_specific_thermal_power_bits_suspect(self):
        expected = {1: 'gpu_idle', 2: 'applications_clocks_setting', 4: 'software_power_cap',
                    8: 'hardware_slowdown', 16: 'sync_boost', 32: 'software_thermal_slowdown',
                    64: 'hardware_thermal_slowdown', 128: 'hardware_power_brake',
                    256: 'display_clock_setting', 512: 'board_limit', 1024: 'reliability'}
        self.assertEqual(perf.NVML_CLOCK_REASONS, expected)
        for bit, name in expected.items():
            with self.subTest(bit=hex(bit)):
                result = perf.thermal_or_power_throttling([row(gpu_throttle_mask=bit)], gpu_vendor='0x10de')
                self.assertEqual(result['status'], 'suspected' if bit in (4, 32, 64, 128) else 'not_observed')
                observations = result['mask_interpretation']['observations']
                self.assertEqual(observations[0]['decoded_bits'], [f'{name} ({bit:#x})'])
                self.assertEqual(observations[0]['unknown_bits'], [])
                if result['evidence']:
                    self.assertEqual(result['evidence'][0]['suspicion_bits'], [f'{name} ({bit:#x})'])

    def test_unknown_mask_bits_and_vendor_never_imply_throttling(self):
        result = perf.thermal_or_power_throttling([row(gpu_throttle_mask=0x801)], gpu_vendor='0x10de')
        self.assertEqual(result['status'], 'cannot_tell')
        self.assertEqual(result['evidence'], [])
        self.assertEqual(result['mask_interpretation']['observations'][0]['unknown_bits'], ['unknown bit 0x800'])
        mixed = perf.thermal_or_power_throttling([row(gpu_throttle_mask=0x804)], gpu_vendor='0x10de')
        self.assertEqual(mixed['status'], 'suspected')  # Known power cap, not the unknown bit.
        self.assertEqual(mixed['evidence'][0]['suspicion_bits'], ['software_power_cap (0x4)'])
        for vendor in (None, '0x0000', '0x1002', '0x8086'):
            result = perf.thermal_or_power_throttling([row(gpu_throttle_mask=4)], gpu_vendor=vendor)
            self.assertEqual(result['status'], 'cannot_tell')
            self.assertEqual(result['evidence'], [])
            self.assertIsNone(result['mask_interpretation']['schema'])
        result = perf.thermal_or_power_throttling([row(gpu_throttle_mask=None)], gpu_vendor='0x10de')
        self.assertEqual(result['status'], 'cannot_tell')

    def test_authored_idle_reliability_and_ample_ram_shapes_do_not_alarm(self):
        cap = perf.parse_perf(idle_reliability_ample_ram_fixture())
        self.assertEqual(cap.issues, [])
        self.assertEqual(len(cap.samples), 69)
        findings = {f['check']: f for f in perf.summarize(cap)['suspicions']}
        self.assertEqual(findings['thermal_or_power_throttling']['status'], 'not_observed')
        self.assertEqual(findings['system_memory_pressure']['status'], 'not_observed')
        observations = findings['thermal_or_power_throttling']['mask_interpretation']['observations']
        self.assertEqual({r['gpu_throttle_mask'] for r in observations}, {1, 1024})
        self.assertEqual(sum(r['sample_count'] for r in observations), 69)
        self.assertEqual(cap.samples[-1]['swap_used_bytes'] - cap.samples[0]['swap_used_bytes'], 590 * 2**20)
        self.assertGreaterEqual(min(r['ram_available_bytes'] for r in cap.samples), 47 * 2**30)

    def test_video_memory_budget_uses_driver_not_device_capacity(self):
        result = perf.video_memory_budget([row(vram_used_bytes=950, vram_budget_bytes=1000, gpu_memory_used_bytes=1)])
        self.assertEqual(result['status'], 'suspected')
        self.assertEqual(perf.video_memory_budget([row(vram_used_bytes=1, vram_budget_bytes=0)])['status'], 'cannot_tell')
        self.assertEqual(perf.video_memory_budget([row(vram_used_bytes=900, vram_budget_bytes=1000)])['status'], 'not_observed')

    def test_ram_exhaustion_and_swap_growth(self):
        self.assertEqual(perf.system_memory_pressure([row(ram_available_bytes=512*2**20)])['status'], 'suspected')
        rows = [row(sample=1, swap_used_bytes=0, ram_available_bytes=3*2**30),
                row(sample=2, swap_used_bytes=64*2**20, ram_available_bytes=2*2**30)]
        self.assertEqual(perf.system_memory_pressure(rows)['status'], 'suspected')
        self.assertEqual(perf.system_memory_pressure([row(ram_available_bytes=None, swap_used_bytes=None)])['status'], 'cannot_tell')
        rows[-1]['ram_available_bytes'] += 1
        self.assertEqual(perf.system_memory_pressure(rows)['status'], 'not_observed')
        rows[-1]['ram_available_bytes'] = None
        self.assertEqual(perf.system_memory_pressure(rows)['status'], 'cannot_tell')
        rows[-1]['ram_available_bytes'] = 2*2**30
        rows[-1]['swap_used_bytes'] = 64*2**20 - 1
        self.assertEqual(perf.system_memory_pressure(rows)['status'], 'not_observed')

    def test_cpu_busiest_same_thread_and_idle_gpu(self):
        rows = [row(sample=i, cpu_thread_pct=90, gpu_use_pct=30) for i in (1, 2)]
        self.assertEqual(perf.cpu_limited(rows)['status'], 'suspected')
        rows[-1]['gpu_use_pct'] = None
        self.assertEqual(perf.cpu_limited(rows)['status'], 'cannot_tell')
        rows[-1]['gpu_use_pct'] = 50
        self.assertEqual(perf.cpu_limited(rows)['status'], 'not_observed')

    def test_gpu_saturation_requires_two_adjacent_readings(self):
        rows = [row(sample=i, gpu_use_pct=95) for i in (1, 2)]
        self.assertEqual(perf.gpu_limited(rows)['status'], 'suspected')
        self.assertEqual(perf.gpu_limited(rows[:1])['status'], 'cannot_tell')
        rows[-1]['gpu_use_pct'] = None
        self.assertEqual(perf.gpu_limited(rows)['status'], 'cannot_tell')

    def test_streaming_disk_rate_and_raw_frame_spike(self):
        rows = [row(sample=i, disk_read_bytes=n) for i, n in enumerate((0, 2**20, 2*2**20, 66*2**20), 1)]
        frame_log = frames(values=[10, 10, 10, 100])
        result = perf.streaming_stall(rows, frame_log)
        self.assertEqual(result['status'], 'suspected')
        self.assertEqual(result['evidence'][0]['read_bytes_per_s'], 64*2**20)
        self.assertEqual(result['evidence'][0]['max_frame_ms'], 100)
        self.assertEqual(perf.streaming_stall(rows)['status'], 'cannot_tell')
        rows[-1]['disk_read_bytes'] = None
        self.assertEqual(perf.streaming_stall(rows, frame_log)['status'], 'cannot_tell')

    def test_evidence_is_bounded(self):
        result = perf.gpu_limited([row(sample=i, gpu_use_pct=99) for i in range(100)])
        self.assertEqual(len(result['evidence']), 8)
        self.assertGreater(result['evidence_total'], 8)


if __name__ == '__main__':
    unittest.main(verbosity=2)
