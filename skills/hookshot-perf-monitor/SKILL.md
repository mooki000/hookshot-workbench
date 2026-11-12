---
name: hookshot-perf-monitor
description: >
  Use when Hookshot or Longshot stutter, CPU saturation, GPU use/temperature/power, video-memory budget, RSS/RAM/swap or
  streaming reports need sampled system context, or when reading, windowing, checking or comparing perf.csv.
  Read hookshot-workbench first. These are observations and suspicions, not bottleneck or speed verdicts;
  speed claims remain with hookshot-perf-compare and hookshot-regression-hunt's existing paired worker.
---

# Hookshot / Longshot system telemetry

**Use for:** finding context around a reported hitch, memory pressure, hot device or unavailable counter; retaining
an incomplete capture; comparing sampled gauges. Read `hookshot-workbench` and the current brief.
This is an optional instrument: a name-compatible build may still lack its emitter.
Read `hookshot-perf-compare` and `hookshot-regression-hunt` before any timing claim. **Ordinary run, no speed claim.**

The installed harness supports `--perf`, regression `run.perf: true` and nightly `perf: true`. These collect context,
not speed evidence. The current MCP catalogue has no `system_telemetry` analyzer; use the file reader below.

## 1. Collect only an owned, bounded run

Select an owned built `COPY`, a local `SAVE` to copy, and a genuinely new `TAG` under the workbench rules.
`hs-run.sh` deletes an existing tag's directory: never reuse another run. Use the wrapper's windowless device gate,
not a nested gate, a second sampler or an exclusive hold. Keep one waiter; ordinary wall limits are at most 450 s.
The name-compatible wrapper detects the owned executable; collect with:

```sh
H="$HOME/Games/.hookshot-harness"
P="$HOME/Code/hookshot-wt/deps/python/bin/python3"
unset HSGATE_SLOT_DIR HSGATE_INTEL_SLOT_DIR HSGATE_SLOT_TEST HSGATE_SLOT_NODESKTOP HSGATE_IN_GATE HSGATE_IN_INTEL_GATE
"$H/hs-run.sh" "$COPY" "$TAG" 120 --user "$SAVE" --perf
RUN="$COPY/runs/$TAG"
source "$H/hs-names.sh"
hs_names copy "$COPY" || exit
LOG="$RUN/$HS_LOG"
```

The option supplies `RX_VK_PERF_LOG="$RUN"` and `RX_VK_PERF_FIELDS=all`. These switches and `perf.csv` are not
project-name substitutions; the optional feature must exist in the selected build. Older builds may produce no file.
Retain `perf.csv`, `frames.csv`, `present.csv`, `env-used.txt`, the detected `$LOG`, `exit.txt`, `summary.txt`,
`kernel.txt`, `kernel-status.json` and inspected captures together. A wrapper exit 0 is not a game completion receipt.
Apply the workbench kernel-stop policy; missing journal coverage is unknown, never zero.

## 2. Read the contract, not a plausible column

- The reader accepts V1 `# hookshot_perf,1,` or `# longshot_perf,1,` with named columns; line two is
  `# started,<UTC ISO8601>,<game version>`. Accepting a header is not proof a build includes the optional monitor.
  Later `#` lines are ignored comments; the second line supplies metadata. Preserve quoted UTF-8 CSV strings.
  The reader refuses unsupported versions (including newer ones), unknown/missing columns and duplicate names.
- Optional `# gpu_vendor,0x10de` identifies NVIDIA by Vulkan vendor ID; `0x0000` is unknown. Older CSVs may lack it.
  IDs have at least four hexadecimal digits, not exactly four: Vulkan's 32-bit Khronos vendor IDs start at `0x10000`.
  Use an explicitly retained same-run device log with `summary ... --device-log "$LOG"` when needed.
  The reader never auto-loads an unregistered sibling log. Missing/ambiguous/conflicting identity stays unknown.
- Numeric `na` is unavailable/disabled, not zero or idle. Preserve missing counts and once-per-metric
  `vk: perf: <name>: <reason>` diagnostics. Zero fan, idle clock or reads can be valid; zero temperature is not.
- Process CPU 100% means one logical CPU and may exceed 100%; busiest-thread CPU is one sampled thread's delta.
  Short/exited threads may be missed; a thread name or last scheduled CPU does not prove P/E-core residency.
- GPU utilization and `gpu_memory_used_bytes` are whole-device readings: shared workloads and driver sampling
  windows count. They are not this game's utilization/allocation; shared system memory is not dedicated VRAM.
- `vram_used_bytes` / `vram_budget_bytes` are Vulkan driver usage / moving allowed budget, not device capacity.
  `texture_used_bytes` / `texture_budget_bytes` describe a different population; do not substitute either pair.
- RSS includes shared resident pages; Windows private commit is not resident RAM. Linux RSS/RssAnon is not commit.
  Available RAM and swap are whole-PC context. Keep bytes in data; convert to MiB (`bytes / 2**20`) only for display.
- `monotonic_ns` is OS monotonic time; `elapsed_s` starts at monitor start. Neither is UTC or renderer `time_s`.
  `frame,time_s` exactly match the same-run `frames.csv` index/origin, not shifted game-present identifiers.
  `game_frame`, shot/replay `renderer_present` and host `present.csv` remain distinct populations.
- `clock` is `headless-renderer` or `swapchain`. Headless intervals end after offscreen submission, not scan-out;
  GPU query milliseconds are another delayed population, not this CPU interval or input/display latency.
- GPU power is device watts, not wall power; fan is driver percent, clock is MHz, and idle zero may be valid.
  CPU temperature is the named package sensor; CPU MHz is the highest reported current clock, not thread residency.
- `gpu_ms` uses already-completed command-buffer timestamp queries, excludes present blit and may lag by frames.
  `gpu_wait_ms` is the existing slot-wait snapshot; retain resource/readback logs separately with their denominators.
- `pipelines,left_out,upload_bytes` are interval deltas, not every stall cause or cumulative disk reads.
  `disk_read_bytes` is cumulative: a rate requires two monotonic receipts; counter reset is not negative I/O.
- `mean_fps=1000/mean_ms`; lows invert the mean of the slowest intervals, not the mean of instantaneous FPS.
  `p50_ms,p95_ms,p99_ms,max_ms` and strict `over16_7,over20,over25,over50,over100` counts use the named renderer clock.
  `frames` counts eligible intervals in that statistic's window, never generated frames or a game/present difference.
- `battery_pct` and `ac_online` require an unambiguous available source; no battery is `na`, not 0% or unplugged.

## 3. Select receipts and inspect coverage

Set `FIRST` and `LAST` from the retained renderer trace and the intended scene, not guessed elapsed/filename times.
These reader commands inspect existing files; they launch no game. Sibling `frames.csv` is used when present.

```sh
"$P" -I -B "$H/perf/hs_perf.py" summary "$RUN/perf.csv" --from "frame:$FIRST" --to "frame:$LAST" --json
"$P" -I -B "$H/perf/hs_perf.py" check "$RUN/perf.csv"
```

Endpoints are inclusive sample receipts: `frame:N` is the exact renderer frame, `time:S` is monitor elapsed seconds,
`monotonic:S` is OS monotonic seconds, and `marker:EVENT[@N]` resolves to a recorded monotonic boundary.
Marker occurrences are one-based; missing/ambiguous markers refuse. Both ends must resolve to the same coordinate.
Missing coordinates are excluded and counted; retain requested bounds and actual first/last coverage. Map/loading
is unknown without recorded host markers; capture boundaries alone do not identify loading or maps.
Joins require the same run/renderer clock, exact frame identity and `time_s` agreement within 2 microseconds;
there is no interpolation, game-frame offset correction or silent present-trace fallback.

Each `metrics[name]` has `unit,count,missing_count,mean,median,p5,p95,min,max`: unweighted available sample values,
not time-weighted measurements or independent run repeats. Sample frame aggregates overlap trailing windows;
never average their means/lows/percentiles into session frame statistics or use them for a repeat verdict.
Final-summary verification uses raw positive finite renderer intervals in `(capture_start.frame, final.frame]`,
with both exact boundary rows and every intervening frame covered. It verifies the whole capture even when the
sample window is narrower. Lows use the slowest `max(1,ceil(N*fraction))` intervals; percentiles use nearest rank.
Missing raw frames/boundaries yields `cannot_tell`, not reconstructed statistics. Inspect `summary_verification`
(`match`, `mismatch`, `cannot_tell`), joins and issues; a successful command alone does not establish coverage.

Only a final `summary` with `event=end,detail=complete` marks graceful completion. Crash, SIGKILL or timeout may
leave no summary: retain flushed records, report the last receipt/cutoff and `completion`, not a complete capture.
The reader discards an unterminated last record with an issue. A final summary does not erase other contract errors.

## 4. Compare gauges, never promote them into speed

Set `A` and `B` to retained run directories with matched settings, scene and clock; select explicit comparable bounds.

```sh
"$P" -I -B "$H/perf/hs_perf.py" compare "$A/perf.csv" "$B/perf.csv" --from "frame:$FIRST" --to "frame:$LAST" --json
```

The reader refuses unknown/unlike renderer clocks and contract errors. A gauge difference exceeds observed spread
only if **each run has at least 3 available samples**, observed `[min,max]` ranges are **strictly disjoint**, and
`abs(mean_B - mean_A) > max(max_A - min_A, max_B - min_B)`. Missing samples remain counted; touching ranges fail.
This is descriptive, not statistical significance, causation or speed. Non-gauges get arithmetic only: all frame
metrics, throttle masks, AC state, cumulative disk bytes, pipeline/left-out counts and upload bytes are excluded.
Only the existing authorized alternating paired regression worker, quiet checks and acceptable same-arm repeat
spread can support speed claims. Its `paired_decision` remains unchanged; monitor comparison is not a replacement.

## 5. Read all six suspicion rules as leads

Statuses are `suspected`, `not_observed` or `cannot_tell`; insufficient/missing evidence is not a negative finding.
`not_observed` is not proof of absence. Review required columns, unavailable rows and the actual evidence receipts.

- `thermal_or_power_throttling`: for proven NVIDIA/NVML, only software power cap `0x4`, software thermal slowdown
  `0x20`, hardware thermal slowdown `0x40`, or hardware power brake `0x80` suspect. Evidence names decoded bits.
  Idle `0x1`, clock settings, generic HW slowdown, sync boost, board limit and reliability policy **`0x400`** do not
  suspect by themselves. Unknown bits are named “unknown bit”; missing vendor/undocumented mask semantics are
  `cannot_tell`, never generic “nonzero means throttling”. Independently, adjacent positive CPU/GPU clocks falling
  at least 10% while corresponding temperature rises at least 5 C remain a suspicion, not a causal verdict.
- `video_memory_budget`: Vulkan usage at least 95% of its **positive** moving budget, not texture/card capacity.
- `system_memory_pressure`: available RAM at most 512 MiB, **or** first-to-last available swap growth at least
  64 MiB **and** available RAM at most 2 GiB in a selected sample. Swap growth with ample RAM is not pressure.
  These are whole-PC observations, not proof the game is under pressure; total RAM capacity is unavailable.
- `cpu_limited`: two adjacent samples name the same nonempty busiest thread, each at least 90% of one logical CPU,
  while whole-device GPU use is at most 30% in both. Caps, loading and other work remain alternatives.
- `gpu_limited`: two adjacent whole-device GPU-use samples at least 95%; other workloads remain unresolved.
- `streaming_stall`: at least three usable adjacent-sample disk-rate intervals with positive monotonic deltas,
  nondecreasing cumulative reads and matching advancing frame endpoints. Rate must be at least
  `max(16 MiB/s, 4 * median eligible rate)` and a positive renderer interval in that same `(a.frame,b.frame]`
  bracket must exceed `max(50 ms, 2 * median eligible frame interval)`. This is coarse correlation, not disk causation;
  Linux read bytes describe physical storage, whereas Windows logical reads may be cached.

Mask meanings are pinned to [NVIDIA's `nvml.h`](https://github.com/NVIDIA/go-nvml/blob/5e992702f9f516e8db7a0c34d3e462a87f48339d/gen/nvml/nvml.h#L3673-L3799);
its reliability and board-limit policies do not name a thermal/power cause. Read the bounded `mask_interpretation`
observations separately from suspicion evidence. Do not infer a vendor from a familiar numeric mask.

## 6. Attach and report without inventing integration

Adapters load `$H/perf/hs_perf.py` with `importlib.util` from their fixed sibling harness path, never from caller
CWD, caller-provided code or `sys.path` sources. `attach(run, start=..., end=...)` returns `None` for absent
`perf.csv`; otherwise it returns the summary. Input/schema failures return `status=cannot_tell` and a named error.
Regression scenarios use `run.perf: true`; their receipt attaches only samples in the selected raw renderer-frame
window, after the same first-interval exclusion as the existing timing decision. Nightly scenarios use `perf: true`;
reports retain four contextual sensor columns with explicit coverage. Neither adapter changes a paired decision.
MCP does not yet expose this CSV analyzer. Do not feed CSV to regex `memory_lines` or `thread_cpu` readers,
or feed sampled telemetry results to the paired speed classifier. Keep registered artifact provenance when handing
results to another tool; monitor elapsed seconds and OS monotonic seconds are not interchangeable.

Report checklist:
- Skills read/tools used; pinned build, settings, GPU/driver, reader/schema and raw source/artifact identities.
- Requested window and actual sample/frame coverage, joins, completion/cutoff, missing coordinates and counters;
  per-metric available/missing counts, units and unavailable reasons. Preserve rejected/disturbed evidence.
- Checks actually exercised, exits, issues and whole-capture summary verification; untested hardware/routes remain so.
- Each suspicion's rule, required columns and cited file/line/sample/frame evidence; output retains at most eight
  evidence entries plus `evidence_total`, so inspect the raw source when more are reported. Human review is required.
- Alternative explanations, inspected captures/logs and remaining unknowns; explicitly **ordinary run, no speed claim**.
