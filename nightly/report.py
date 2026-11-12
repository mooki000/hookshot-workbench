"""Plain local reports, tracker searches and unposted issue-form drafts."""
from pathlib import Path
from collections import Counter
import json
import subprocess
from common import HARN, capture, put, slug, stamp


def report(local, night, build, tests, test_diff, results, findings, status, error=None):
    counts = Counter(r['status'] for r in results)
    tc = Counter(t['status'] for t in tests.values())
    elapsed = sum(r.get('elapsed_game_s') or 0 for r in results)
    lines = [f'# Nightly playtest — {night}', '', '## State', '',
             f'{stamp()} — {status}. ' + (error or ''), '',
             '## What was tested', '',
             f"Published nightly commit: `{build['commit']}`; built from source with the full workbench build.",
             f"Build: {build['status']}; elapsed including gate: {build.get('elapsed_including_gate_s', 0):.1f} s.",
             'Build duration: ' + '; '.join(build.get('build_duration_lines', [])),
             'Compiler warnings are suppressed upstream; an empty warning list is not a warning-free build.',
             f"Binary sizes (bytes): `{json.dumps(build.get('binary_bytes', {}), sort_keys=True)}`.",
             f'{len(results)} scenario attempts; {elapsed:.1f} s of wrapper/game elapsed time (queue waits excluded).',
             f'Scenario outcomes: {dict(counts)}. Game-free suite: {dict(tc)}.', '',
             '## Regressions and first observations', '']
    if findings:
        for finding in findings:
            lines += [f"### {finding['scenario']}: {finding['kind']}", '',
                      '```json', json.dumps(finding, indent=2), '```', '']
    else:
        lines += [('No established regression. A first gameplay night only seeds its baseline; it cannot establish historical speed or image stability.'
                   if results else 'No gameplay scenarios ran. No gameplay baseline or GPU regression claim exists.'), '']
    lines += ['## Scenario evidence', '', '| Scenario | Result | Save | Time (s) | Timings judged? |', '|---|---|---|---:|---|']
    for r in results:
        lines.append(f"| {r['id']} | {r['status']} | {r.get('save') or 'fresh profile'} | {r.get('elapsed_game_s') or 0:.1f} | {'no: disturbed' if r['disturbed'] else 'yes'} |")
    for r in results:
        lines += ['', f"### {r['id']}", '', f"Checks: {'; '.join(r.get('checks', [])) or 'none completed'}.",
                  f"Error: {r.get('error', 'none')}", '```text', r.get('summary', 'No wrapper summary.'), '```',
                  'Load/evidence: `' + r.get('evidence_dir', '') + '`.',
                  'Disturbance: ' + '; '.join(r.get('disturbance', [])),
                  'Missing metrics: ' + json.dumps(r.get('missing', {})),
                  'Images are collected, not human-reviewed by this automated report. Review before citing or posting.',
                  'Image diagnostics: ' + json.dumps({k: {x: v[x] for x in ('black', 'blank')} for k, v in r.get('images', {}).items()}),
                  'Flash candidates: ' + json.dumps(r.get('flash_candidates', []))]
    lines += ['', '## Game-free changes', '', 'New failures: ' + json.dumps(test_diff.get('new_failures', [])),
              'Vanished tests: ' + json.dumps(test_diff.get('vanished', [])),
              'Failures tonight: ' + json.dumps([k for k, v in tests.items() if v['status'] in ('FAIL', 'TIMEOUT')]),
              'Slowest tests that became slower (observations only under load):',
              '```json', json.dumps(test_diff.get('slower', []), indent=2), '```', '',
              '## Improvements', '',
              'Only empirical-band comparisons are labeled improvements; first-night and disturbed timing values are not.',
              '```json', json.dumps([{'scenario': r['id'], 'metric': k, **v} for r in results
                                    for k, v in r.get('comparisons', {}).items() if v['state'] == 'improvement'], indent=2), '```', '',
              '## What could not be judged', '',
              'Ordinary queue runs do not establish frame-rate changes, even when a sampled load looks low. '
              'Timings and memory are retained but excluded from their empirical baseline while disturbed. '
              'A timing/memory regression requires one undisturbed confirming rerun. '
              'Headless renderer intervals are CPU offscreen-submit intervals, not scanout or GPU completion.',
              'Image comparison needs three accepted matching-scenario nights; it is tolerant of historical variation, '
              'not proof of correctness. Black/blank and sparse flash candidates require visual review.',
              'Long-duration growth, exhaustive menu/destination coverage, save-write cycles, intentional build load, '
              'cold-cache and two-game stress are designed extensions, not exercised by this initial suite.', '',
              '## Files and privacy', '',
              f'Raw evidence: `{local / "raw" / night}`. Database: `{local / "results.sqlite"}`. '
              f'Baseline index: `{local / "baselines" / "latest.json"}`.',
              'Save inputs and run saves remain in the private harness/game copies. Nothing is uploaded or posted.',
              'No timer is implied by this report; consult timer.json for a separately approved transient arm.']
    destination = local / 'reports' / (night + '.md')
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix('.md.next')
    tmp.write_text('\n'.join(lines) + '\n')
    tmp.replace(destination)
    return destination


def enrich(config, night, build, finding, spec, good, out):
    """Read-only tracker search; never create or comment on an issue."""
    out.mkdir(parents=True, exist_ok=True)
    query = spec.get('issue_query', 'Linux')
    searches = []
    commands = [
        ['gh', 'search', 'issues', '--repo', config['repository'], '--state', 'open', '--limit', '30',
         '--json', 'number,title,url', query],
        ['gh', 'pr', 'list', '--repo', config['repository'], '--state', 'all', '--search', query,
         '--limit', '30', '--json', 'number,title,state,url'],
    ]
    for command in commands:
        p = subprocess.run(command, capture_output=True, text=True, timeout=120)
        searches.append({'argv': command, 'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
    put(out / 'tracker-search.json', {'at': stamp(), 'searches': searches})
    finding['tracker_search'] = str(out / 'tracker-search.json')
    finding['duplicate_review'] = 'Search results require human matching; no issue was opened.'
    if good:
        revision_range = good['commit'] + '..' + build['commit']
        finding['last_good_commit'] = good['commit']
        finding['commit_range'] = revision_range
        (out / 'commits.txt').write_text(capture(['git', '-C', config['repo'], 'log', '--format=%H %s', '--reverse', revision_range]))
        paths = spec.get('affected_paths', [])
        (out / 'suspects.txt').write_text(capture(['git', '-C', config['repo'], 'log', '--format=%H %s', '--reverse',
                                                revision_range, '--', *paths]))
        finding['suspected_commits'] = str(out / 'suspects.txt')
        hunt = HARN / 'regress' / 'regress.py'
        if hunt.is_file():
            p = subprocess.run([config['python'], '-I', '-B', str(hunt), 'plan', '--repo', config['repo'],
                                '--good', good['commit'], '--bad', build['commit'], '--output', str(out / 'hunt-plan')],
                               capture_output=True, text=True, timeout=180)
            (out / 'hunt-plan.log').write_text(p.stdout + p.stderr)
            finding['hunt_plan_exit'] = p.returncode
        from hunt import handoff
        finding['hunt'] = handoff(config, spec, good, build, finding, out)
    draft = Path(config['drafts']) / f'nightly-{night}-{slug(finding["scenario"])}-{slug(finding["kind"])}.md'
    draft.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Draft — {finding["scenario"]}: {finding["kind"]}', '',
             'Draft only; inspect the evidence and duplicate search before publication.', '',
             '### Hookshot version', build['commit'], '', '### What kind of problem', 'Something else', '',
             '### What happened', '```json', json.dumps(finding, indent=2), '```', '',
             '### Where in the game', spec.get('description', spec['id']), '',
             '### How often', 'Once so far; any confirmation evidence is recorded above.', '',
             '### Graphics card and driver version', '\n'.join(finding.get('device_lines', [])) or 'Unavailable in the failed run; no device identity assumed.', '',
             '### CPU, RAM and operating system', finding.get('hardware', config.get('hardware', 'Unavailable in the recorded environment.')), '',
             '### Changes from the defaults', '```json', json.dumps(spec.get('settings', {}), indent=2), '```', '',
             '### Other software hooked into the game', 'No, nothing hooked in. Automated control and trace switches are recorded.', '',
             '### hookshot_log.txt', finding.get('log', 'See the scenario evidence directory in the night report.'), '',
             '### Screenshots or video', 'Collected locally; visual review and selection required, nothing attached automatically.', '',
             '### Before you send it', '- [ ] Confirm still present on the newest release.',
             '- [ ] Read the saved open-issue and PR search and select an existing thread when appropriate.',
             '- [x] No game files, saves, keys, binaries or shader caches are attached.', '',
             'Score is not guessed: choose severity, affected reach and confidence after evidence review.']
    if spec.get('form') == 'build':
        lines = [f'# Draft — {finding["scenario"]}: {finding["kind"]}', '',
                 '### Builder version', build['commit'], '', '### How you built',
                 'Command line (nx2/make_game.py), followed by the complete game-free suite.', '',
                 '### Game files given to it', 'romfs and exefs folders (build only; the failing suite needs no game).', '',
                 '### The error', '```json', json.dumps(finding, indent=2), '```', '',
                 '### Visual Studio or Build Tools version, and Windows SDK version',
                 'Not applicable: Linux.', '', '### Operating system and free space on the output drive',
                 finding.get('hardware', 'See the retained environment.') + '; free bytes: ' +
                 str(__import__('shutil').disk_usage(out).free), '',
                 '### Before you send it', '- [ ] Check whether the newest release has the same failure.',
                 '- [ ] Read the retained open-issue and PR search for duplicates.',
                 '- [x] No game files or anything built from them are attached.']
    draft.write_text('\n'.join(lines) + '\n')
    finding['draft'] = str(draft)


def discord(config, night, build, results, findings):
    dest = Path(config['drafts']) / ('DISCORD-nightly-' + night + '.md')
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not results:
        dest.write_text(f"Progress (mooki000's agent): built nightly {build['commit'][:12]} from source on Linux.\n"
                        "The build and named game-free suite evidence are retained in the local report.\n"
                        "No nightly GPU scenarios ran; no gameplay baseline exists. GPU time is reserved for the regression hunt.\n"
                        "The optional timer is disabled; nothing posted or attached.\n")
        return dest
    passed = sum(r['status'] == 'passed' for r in results)
    dest.write_text(f"Progress (mooki000's agent): tested nightly {build['commit'][:12]} from source on Linux.\n"
                    f"{passed}/{len(results)} scenario attempts completed; {len(findings)} findings need review.\n"
                    "Ordinary-queue timings are disturbed, not performance claims; this first record seeds the baseline.\n"
                    "Logs, pictures and the named game-free results are local; nothing posted or attached.\n")
    return dest


def tracking_draft(config, record, out, baseline=None):
    """A measurements-only #2595 comment, retained per cycle; no posting or private-path export."""
    import math
    import statistics
    out = Path(out)
    build = record.get('build', {})
    required = config['release_baseline']['commit']
    fmt = lambda x, n=3: '-' if x is None else f'{x:.{n}f}'
    phases = build.get('phase_seconds', {})
    def total_seconds(item):
        value = item.get('phase_seconds', {}).get('total')
        if value is not None:
            return value
        import re
        for line in item.get('build_duration_lines', []):
            match = re.search(r'the build took (\d+):(\d+):(\d+)', line)
            if match:
                h, m, s = map(int, match.groups())
                return h * 3600 + m * 60 + s
        return None
    hardware = build.get('toolchain', {})
    known_gpu = [h['seconds_used'] for h in record.get('holds', []) if isinstance(h.get('seconds_used'), (int, float))]
    gpu = sum(known_gpu)
    assert all(math.isfinite(x) and x >= 0 for x in known_gpu)
    lines = [f"## Six-hour benchmark report — `{record['commit']}`", '',
             f"Cycle `{record['cycle']}`; source pinned {record['started']}; result **{record['status']}**.", '',
             '### Baseline', '',
             f"Required release: `v2.1.0`, `{required}`. " +
             ('Measured baseline is recorded below.' if baseline and baseline.get('complete') else
              '**Its gameplay measurements are still pending; the historical previous build below is not substituted for it.**'),
             f"Previous comparison build: `{record.get('previous', {}).get('commit', 'not available')}`.", '',
             '### Build measurements', '',
             f"Build status: **{build.get('status', 'not built')}**. Upstream base `{record['commit']}`; "
             f"local instrumented source `{build.get('source_commit', record['commit'])}`.",
             f"Builder wall time: **{fmt(phases.get('total'), 1)} s**; compile stage **{fmt(phases.get('compile'), 1)} s**; "
             f"compiler-worker wall **{fmt(phases.get('compile_workers_wall'), 1)} s**; "
             f"rounded link total **{fmt(phases.get('link'), 1)} s**. Translation was not separately timed.",
             f"Elapsed including the build-gate wait: **{fmt(build.get('elapsed_including_gate_s'), 1)} s**. "
             'Fetch/setup, tests and shader preparation are outside the builder total.',
             f"Fresh work/output check: **{build.get('fresh_build', 'not recorded by this version')}**. "
             'Fixed release settings and the same frozen PGO inputs are used; profile data and hashes remain local.',
             f"Workers: {hardware.get('workers', 16)} compile, {hardware.get('lift_workers', 8)} lift; "
             f"{hardware.get('hot_optimization', 'release hot-code O2 with PGO')}; other lifted code O1.",
             f"Compiler: {hardware.get('compiler', 'not captured in this record')}. "
             f"Linker: {hardware.get('linker', 'not captured in this record')}.",
             f"Hardware: {hardware.get('cpu', 'not captured')}; "
             f"RAM {fmt(hardware.get('memory_kib', 0) / 1048576 if hardware.get('memory_kib') else None, 2)} GiB; "
             f"{hardware.get('system', 'system not captured')}; GPU/driver {hardware.get('gpu', 'not captured')}.",
             'Compiler warnings are suppressed upstream; no warning lines is not a warning-clean claim.', '',
             '### In-game measurements', '',
             'Local protocol, not yet the tracking issue’s standardized control-bridge route: the same private save '
             'inputs and timed controller/camera script, headless rendering, explicit graphics/cap expectations, '
             'prepared caches and an unmeasured warm-up for each scene/mode. No save or input/route data is attached.',
             'The scene’s default window is 15–45 s after the detected load gap. Frames are CPU offscreen-present '
             'intervals, not scanout. Identical opt-in readback logging is enabled on both builds; its overhead is not removed.',
             'Quiet eligibility: other work <3 cores average, no approximately five-second sample >6; judged-arm '
             'repeats within 5%. One-minute load is reported, not used as that eligibility test.', '',
             'Run identifiers below name the originating cycle/hold; fixed-release rows retain their original dates.']
    table_header = [
        '| Scene | Hold / run / build | Ended | Counts? | FPS | Mean ms | 1% low FPS | >20 / >25 ms | CPU ms/frame, all threads | Readback wait ms/frame | Peak RSS MiB | Other cores mean / peak | Load before / after |',
        '|---|---|---|---|---:|---:|---:|---|---:|---:|---:|---|---|']

    def eligible(result):
        return result.get('valid') is True and result.get('disturbed') is False and not result.get('kernel_errors')

    def fps_summary(rows):
        return ', '.join(fmt(row['result'].get('measures', {}).get('fps'), 2) +
                         ('' if eligible(row['result']) else ' (dropped)') for row in rows)

    def append_runs(scene_id, runs, cycle_id):
        lines.extend(['', *table_header])
        for i, row in enumerate(runs, 1):
            result, m = row['result'], row['result'].get('measures', {})
            counts = eligible(result)
            lines.append(f"| {scene_id} | `{cycle_id}/{scene_id}`; {Path(row.get('output', f'run-{i}')).name}: "
                         f"`{row['commit'][:12]}` ({row['role']}) | {result.get('end', '-')} | "
                         f"{'yes' if counts else '**dropped**'} | {fmt(m.get('fps'), 2)} | {fmt(m.get('mean_ms'))} | "
                         f"{fmt(m.get('low1_fps'), 2)} | {m.get('over20', '-')} / {m.get('over25', '-')} | "
                         f"{fmt(result.get('cpu_ms_per_frame'))} | {fmt(m.get('readback_wait_ms_per_frame'))} | "
                         f"{fmt(result.get('peak_rss_mb'), 1)} | {fmt(result.get('others_cores_window'), 2)} / "
                         f"{fmt(result.get('others_cores_peak'), 2)} | {fmt(result.get('load_before'), 2)} / "
                         f"{fmt(result.get('load_after'), 2)} |")

    for scene in record.get('scenes', []):
        append_runs(scene['id'], scene.get('runs', []), record['cycle'])
        decision = scene.get('decision', {})
        column = 'release baseline' if record.get('mode') == 'release-baseline' else 'previous cycle'
        lines += ['', f"**{scene['id']}: {decision.get('state', 'not judged')} against the {column} column.**"]
        if decision.get('new') and decision.get('previous'):
            new, old = statistics.mean(decision['new']), statistics.mean(decision['previous'])
            assert old > 0 and math.isfinite(new)
            lines.append(f"Judged mean: {new:.6f} versus {old:.6f}; change {(new/old-1)*100:+.2f}%. "
                         f"Combined repeat range {decision.get('combined_repeat_spread', 0):.6f}; "
                         f"paired differences {', '.join(f'{x:.6f}' for x in decision.get('paired_gaps_worse_units', []))}.")
    if not record.get('scenes'):
        lines.append('No counted gameplay comparison completed in this cycle.')
    if baseline and baseline.get('scenes'):
        lines += ['', '### Fixed release / previous / new main', '',
                  '| Scene | Release baseline (dated) | Previous cycle | New main |', '|---|---|---|---|']
        by_scene = {s['id']: s for s in record.get('scenes', [])}
        for item in baseline['scenes']:
            old_rows = item.get('baseline_runs', [])
            previous = [r for r in by_scene.get(item['id'], {}).get('runs', []) if r['role'] == 'reference']
            current = [r for r in by_scene.get(item['id'], {}).get('runs', []) if r['role'] == 'candidate']
            lines.append(f"| {item['id']} | {fps_summary(old_rows) or 'pending'} FPS "
                         f"({max((r['result'].get('end', '') for r in old_rows), default='date unavailable')}) | "
                         f"{fps_summary(previous) or 'not run'} | {fps_summary(current) or 'not run'} |")
        lines += ['', '### Fixed release runs — measured earlier, not rerun in this cycle', '']
        baseline_cycle = Path(baseline.get('report', f'baseline-{required[:8]}')).stem
        for item in baseline['scenes']:
            append_runs(item['id'], item.get('baseline_runs', []), baseline_cycle)
    lines += ['', '### Build-time comparison', '',
              '| Build | Commit | Builder total seconds | Gate-inclusive seconds |', '|---|---|---:|---:|']
    samples = [('release baseline', (baseline or {}).get('build', {})),
               ('previous cycle', record.get('previous', {})), ('this build', build)]
    for label, item in samples:
        lines.append(f"| {label} | `{item.get('commit', 'pending')}` | {fmt(total_seconds(item), 1)} | "
                     f"{fmt(item.get('elapsed_including_gate_s'), 1)} |")
    for label, item in samples[:2]:
        old, new = total_seconds(item), total_seconds(build)
        if old and new is not None:
            lines.append(f"Observed total versus {label}: {new-old:+.1f} s ({100*(new/old-1):+.2f}%). "
                         "This is arithmetic under the recorded machine load, not an isolated compiler-speed claim.")
    lines += ['', '### Protocol differences and status', '',
              f"GPU gate occupancy actually recorded: **{gpu:.1f} s** across {len(record.get('holds', []))} completed/recorded holds. "
              'Preparation/warm-up are not measurements; queue waits are separate.',
              'The first local sweep uses a high cap of 240 where selected, not the newer menu’s 1000 ceiling; '
              'the table keeps the measured rates beside that ceiling. The separate player-settings arm uses a 60 cap and DLAA when run.',
              'The camera sweep is timed, not pixel-locked or controlled by a closed-loop bridge; sampled viewpoints '
              'can differ. This is explicitly a local protocol, not a validated project-wide route.',
              'The release receives logging-only compatibility instrumentation where needed; older three-column '
              'present traces are read through their shared first three fields. No gameplay/pacing change is backported for measurement.',
              'Build-time percentages are only arithmetic on available totals; fresh-folder, toolchain and load differences remain explicit. '
              'Shader caches are warm for steady gameplay; this is not first-visit compilation/loading performance.',
              'The readback comparison uses wait_ms over all calls, not the software-version-dependent boolean waited flag.',
              'A sampled commit gap is not proof of a cause. No hunt is started by this tool; no timer is installed.',
              'Game files, generated code, binaries, caches, saves, maps and raw profiles remain on the local PC. '
              'This draft contains measurements and methodology only.']
    lines += ['', '### This cycle’s GPU holds', '',
              '| Cycle / hold | Kind | GPU seconds | Queue seconds | Started |',
              '|---|---|---:|---:|---|']
    for hold in record.get('holds', []):
        lines.append(f"| `{record['cycle']}/{Path(hold.get('plan', '')).stem}` | "
                     f"{'measurement' if hold.get('counted') else 'unmeasured'} | "
                     f"{fmt(hold.get('seconds_used'), 1)} | {fmt(hold.get('queue_seconds'), 1)} | "
                     f"{hold.get('started', '-')} |")
    if record.get('error'):
        lines.append('The cycle stopped; its local report preserves the exact failure and no missing run is counted.')
    draft = Path(config['drafts']) / ('Rolling-2595-' + record['commit'] + '.md')
    draft.parent.mkdir(parents=True, exist_ok=True)
    rendered = '\n'.join(lines) + '\n'
    (out / 'issue-2595-draft.md').write_text(rendered)
    draft.write_text(rendered)
    return draft
