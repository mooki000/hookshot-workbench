---
name: hookshot-perf-compare
description: >
  Compare Hookshot builds or settings for fps, frame times, 1% low and stutter;
  distinguish those measurements from ordinary memory/log/image runs. Read
  hookshot-workbench first. Use the shared regression tool, never a second timing runner.
---

# Comparing Hookshot builds or settings

**Use for:** every speed claim, regression comparison or “is this switch/PR faster?”. Read
`hookshot-workbench` and `hookshot-regression-hunt`. Three frequent commands, using
`P=/home/mooki/Code/hookshot-wt/deps/python/bin/python3` and
`H=/home/mooki/Games/.hookshot-harness`:

```sh
"$P" -I -B "$H/regress/regress.py" plan --repo <repo> --good <sha> --bad <sha> --output <new-dir>
"$P" -I -B "$H/regress/regress.py" hunt --repo <repo> --good <sha> --bad <sha> --scenario <local.json> --output <new-dir> --max-holds <authorized-count> --exclusive-authorized
"$H/hs-idle.sh" "$P" -I -B "$H/regress/test_regress.py"
```

The second command is allowed only after Main authorizes those exclusive holds. For a
multi-arm comparison use the same tool's `_hold` plans, as the regression skill describes.
Old `PerfLinux-files/pq.sh`, `ab-hold2.sh` and their tables are historical evidence readers,
not the runner for new measurements. **No windowed run is authorized on 2026-11-12.**

## Before spending a hold

- Read the whole issue/PR thread, current description and `reports/POSTED.md`. A changed
  head is not a request to test: an author may have parked it. A repeat is not a new finding.
- Pin both commits, binaries, profiles, source stamps and cache state. A setting comparison
  on one build is not an old/new build comparison. Use owned copies, no mods or hidden options.
- Choose the same local save, input, scene, cap, output size and upscaler. Warm each scene/mode
  once without measuring. Paths, routes and game data stay in local scenario files.
- `HOS_FPS_MAX=240` in old reports was their high-cap setting, not today's maximum:
  main 51da0464 accepts 61–1000; Uncapped is 1000. Preserve the chosen cap on both arms.
- The frame-clock input script counts 60 presents per script second, not wall seconds.
  Verify loading/scene/control in state and inspected pictures, not a guessed delay.

## What counts as a timing

A B A B in one exclusive hold, with the shared adapter's validity and quiet checks:
non-game work under three cores on average, no five-second sample over six cores, each
arm's repeats within 5%. Load averages are recorded context, not the quiet test. Paired
ranges and the shared classifier decide whether the gap clears repeat spread. Mean
uncapped frame time is steadier than 1% low; hitches need multiple windows and their counts.

Never quote a rate from an ordinary shared run, even as an aside: last night's one-run
“window penalty” disappeared in the quiet pair. Keep rejected rows and reasons. The tool
must retain actual queue time and occupancy separately. One waiter, at most eight minutes
per hold, no priority unless explicitly granted. Never withdraw and re-ask a queued cycle.
Never nest the GPU exclusive gate inside the Intel gate: that deadlocks the quiet lock.

A periodic desktop job is not ours to stop. The regression plan's `window` and `avoid`
fields can place measurement windows between observed disturbances. Do not run analysis
or another job of your own beside your exclusive hold.

## Clocks and parser meanings

- `RX_VK_FRAME_LOG` is the three-column timing CSV. Since #2354 it works headless:
  CPU intervals after submitting the offscreen copy, without swapchain/display waits.
- `HOS_PRESENT_TRACE` is the game's own present clock (older three-column or newer
  seven-column form), limiter included. It is not GPU completion or scan-out.
- `RX_VK_FRAMELOG` (no underscore) is the pixel stream, still swapchain-only. A headless
  run with still screenshots is not a continuous flash proof. Never confuse the two names.
- With a window, frame-log numbers count swapchain/startup/made presents, not game
  presents. The shared adapter aligns the files at their ends; raw frame numbers are not
  interchangeable. No window versus headless claim from mismatched instruments.
- `hs-run.sh` currently labels any populated `frames.csv` as “swapchain”; on headless
  runs that historical label is wrong. Read `env-used.txt` and the actual clock. Its staged
  correction must not replace the wrapper while a live/queued job uses it.
- At the 60 cap, `over16.7` includes pacing jitter; show `over20`, `over25`, `over33`,
  p99 and the longest frame, plus the window length and rejected segments.
- Readback “waited” averages describe a changing subset. #2602 adds last-part/settled
  outcomes without changing the old prefix. Use identically instrumented all-call counts
  on both builds for a per-frame cost; do not compare unlike conditional averages.

## Counts, memory and profiles

Ordinary gated runs can establish counts, images, start/finish and memory observations;
label them **ordinary run, no speed claim**. Short startup RSS slopes do not prove a leak.
Linux `private` is RssAnon, not Windows private commit. Device-wide memory is not the
process's own allocation. A memory experiment does not need exclusive GPU time.

Kernel policy is the brief's signature rule: stop on Xid, kmemsysDoCacheOp and unknown
NVRM lines, not merely on a count greater than one. Known mapping refusals and the bounded
allocation-refusal exception are counted, including repeated-message markers.

Profilers have been run (historical evidence: `reports/PerfLinux.md` section 8.3); the old
“q-prof not run” instruction is obsolete. Keep stock outputs. On main 51da0464,
`scripts/prof.py:37-65` retains argument lists in demangled names but `waiting()` tests
suffixes without them; `scripts/vkprof.py:34` misses `libnvidia-eglcore`. Until repaired,
label local normalization/classification and retain raw stacks; do not treat its waiting
or NVIDIA share as established without checking those names. These source issues are
reported by the performance survey, not a speed result from this skill update.

The newer `scripts/cpu_report.py` is Windows-only and rejects Linux captures. Linux uses the matching-copy ELF
symbols with `scripts/crit.py`/`vkprof.py` or the retained `PerfLinux-files/threadprof.py` reader, with the caveats above.
Every report names the skills read, tools used, pinned sources and actual verification.
