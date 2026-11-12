---
name: hookshot-regression-hunt
description: >
  Find which merged Hookshot pull request made a build worse: frame times, hitches, start-up, memory, drawing, or a
  pass/fail check; and measure someone's fix or a draft that asks for testers. Use for "which PR caused this", a
  regression against an older build, a lead that names a suspect or a switch, a nightly playtest's confirmed
  regression, "here is the fix, test it". Show the difference first, test named suspects by revert or by switch, then
  bisect over groups of equal lifted inputs. Read hookshot-workbench and hookshot-perf-compare first.
---

# Finding a Hookshot regression

**Use for:** proving a regression, testing a named suspect/fix and bisection. Three frequent commands
(`P` is the pinned workbench Python, `H=/home/mooki/Games/.hookshot-harness`):

```sh
"$P" -I -B "$H/regress/regress.py" plan --repo <repo> --good <sha> --bad <sha> --output <new-dir>
"$P" -I -B "$H/regress/regress.py" hunt --repo <repo> --good <sha> --bad <sha> --scenario <local.json> --output <new-dir> --max-holds <authorized-count> --exclusive-authorized
"$H/hs-idle.sh" "$P" -I -B "$H/regress/test_regress.py"
```

The hunt's holds require explicit authorization. **No windowed runs on 2026-11-12**, even if an old
`window-ok` remains. Window-only questions stay untested. Reports name skills read and tools used.

Read `hookshot-workbench` (building, the fast path, `hs-run.sh`, the gates) and `hookshot-perf-compare` (what a timing
needs) first, and `hookshot-pr-evidence` before anything is written for a tracker. The machine's agent brief and the
caller's word go over every example here. A worker never pushes or posts. This file is public: the method only; which
save and which input a scenario uses live in local files.

## 1. Show the difference before looking for a culprit

1. **Pin commits, not folders.** The good and the bad build by commit, binary checksums, build flags and profile.
   Fetch once and write the head down; main moves several times an hour, and a play folder or a copy called "main" can
   be replaced under you during the hunt. Check what an earlier report really compared: two settings on one build are
   not old against new.
2. **Copies of your own**, by reflink, never the player's folder or another lane's copy. Assert for every run: no
   `mods` folder and `HOS_MODS` unset, no options or environment file in the copy (a saved experiment there changes the
   run silently), the expected source stamp, device and output size in the log. Name every switch of a run.
3. **The same scene, input, size, cap, upscaler, and the same cache state.** A freshly prepared copy compiles the
   pipelines the shipped list lacks in its first run of a scene; an older copy has them. Give every copy one unmeasured
   run of each scene and mode first, then time. The shader caches' stamp covers the translator's files alone
   (`nx2/build.py`), so a variant that leaves them alone can take a prepared, warmed copy's caches by reflink (make the
   copy after the preparation and the warm-up, with no game running from the source).
4. **In turn inside one quiet hold**: A B A B, and "quiet" is judged by what everything else on the machine used
   during the run's measuring window, not by the load average: a run counts when the rest used under `quiet_cores`
   (3) on average, no 5-second sample of it was above `quiet_peak_cores` (6), and the hold's repeats of the same arm
   agree within `repeat_tolerance` (5 %) on the measure. The 1-minute load is printed beside every run as information
   only: it lags by a minute and it counts the game's own waiting threads, more on one build than on another, so a
   load limit marks the slower build more often. The hold may wait the seconds it has to spare before its first run
   (`settle_seconds`) for the rest to fall under `quiet_cores`, never more. No timing from an ordinary shared run.
   A disturbance that comes back at fixed times (a desktop job, a timer) shows as one dropped run in hold after hold:
   write down when the dropped runs' peaks were, look for the period, catch it once with a per-process look at
   `/proc` at the next mark, and give the plan `window` and `avoid` so that the held worker starts a run a few seconds
   later instead of losing it. The user's desktop is left alone. Nothing of yours runs beside your own exclusive hold
   (no analysis, no draft script), and a number from a run that was not quiet is not quoted even as an aside: it is
   read as a finding.
5. **One instrument on both builds.** An old build may lack a switch (`RX_VK_FRAME_LOG` without a window came with
   #2354, `HOS_CHAIN` with #2325): give both the same small local logging patch rather than compare unlike clocks.
   Frame intervals without a window are CPU intervals after the offscreen copy, not scan-out; when the suspected wait
   could sit in the swapchain, add one windowed pair under the window rules. With a window the frame-time log counts
   the swapchain's presents (the start-up screen's too), not the game's: the adapter matches the two at the files'
   ends. The pixel frame log (`RX_VK_FRAMELOG`, no underscore) writes nothing without a window.
6. **Count the whole population.** A log line that averages "those that waited" changes meaning when a change alters
   what counts as waited (the readback line did, with #2125); a line that logs the first N slow cases is a sample.
   For "how many a frame and how long each" log every call the same way on both builds, with the present number.
7. **A claim about threads needs the threads**: each thread's processor time from `/proc/<pid>/task/*/stat` over the
   measurement window (the supplied adapter samples it), not the process total.
8. **Bands from clean runs.** The good and the bad build's ranges must not overlap in the measure the hunt decides by.
   Uncapped mean frame time is the steadiest; 1 % low and slow-frame counts need several runs a side. Add the
   player's own settings (his cap, his upscaler) as a separate arm: what moves uncapped may or may not cost him frames.

## 2. Suspects before a bisect

- **Read the suspect's code and its pull request first**: what it fixes, what it measured, on which hardware, and
  what its reviewers said was not measured. A wait that got longer may be the removal of a false speed-up.
- **A lead that names a switch**: read what the switch does on this platform and in both builds before spending a
  hold. A default can depend on the processor count or the system; a switch can exist in both builds with the same
  default (then it explains nothing between them, and is an A/B on the bad build only); a default that flipped inside
  the range is a real candidate even though "the code is in both". Put the switch's own log line into the run's
  expectations, so a run where it did not take effect is not counted.
- **A switch needs no build**: `--switch LABEL:NAME=VALUE` runs the bad build with it against the bad build as it is.
  **A revert** (`--suspect SHA`) is the independent check of the same story, and the only one where no switch exists.
  A switch that takes back only part of a commit is that part; say so.
- **What the suspect gives up when off**: write it down before the run, and look for exactly that in the pictures of
  the run (a stale readback shows as a one-frame brightness step, not as a slow frame).
- After the suspects: **state the remainder in numbers**: the good build against the bad one with the suspects off, in
  one hold. Only a remainder beyond the runs' own spread is hunted further.
- When someone else is already fixing the cause, the product is the measurement on this hardware (and a test of their
  branch when it exists), not a second fix.

## 3. Testing someone's fix, or a change that asks for testers

- Read the whole current thread, description and `POSTED.md` before work. A moved head may be parked, not ready
  for testing; last night's missed “hold off” description cost 37 minutes of an interrupted tour. A result is
  called a finding only after checking what the thread already establishes.
- **Which two commits.** The head, exactly, and the commit its own commits sit on. Git's merge base with main is the
  wrong base when the branch was stacked on a pull request that has since been squash-merged: say which commit you
  used and why. A head that moves keeps its results under its own hash; a new head is a new build (a minute by the
  fast path) and a new table, never a continuation.
- **The same logging aid on both**, cherry-picked. Where it meets their change, keep their lines and place the aid
  around them, then say which of its columns still mean the same in both builds and drop the others from every
  comparison. (A recorded resolution can fill the conflict in by itself and leave the pick unfinished: add, continue.)
- **Exercise the path the change is in.** A change in the present path does nothing in a run without a window: every
  measuring run is windowed then, under the window rules, still in turn inside an exclusive hold. Put the swapchain's
  line (size, present mode) and the change's own state line into each arm's expectations.
- **For a stutter question** the mean says little: per run the frames over 16.7, 20, 25 and 33 ms, the longest frame,
  the 99th percentile and the 1 % low, over windows as long as the hold allows, judged in 30-second segments by the
  quiet rule so that one disturbance costs a segment and not the run; memory available and pages swapped beside the
  hold when memory pressure is a suspect; the renderer's own slow-frame lines counted and placed by the periodic
  present-count lines around them, with the thread that wrote them. "None in N seconds and M frames" is a result; a
  guess at why is marked as one.
- **Their game-free checks**, on clean checkouts (the logging aid breaks checks that cut source by its text), with
  both compilers. Then run the head's changed checks in the base's checkout: one that fails there with its own
  message tests the change, one that stops for a missing piece of text tests the text, one that passes there does
  not test the change at all. Say which is which.
- **A flash check needs a control that shows the fault** on this machine: the same build with the mechanism switched
  off (only with the main session's word), same scene and length. When the control shows nothing, say exactly that:
  the check then has no proof on a real fault here, and a flash painted into a captured log proves the instrument
  only. Give the events' sizes and not only their count (small moving bright things make one-frame events of a few
  blocks, and their number follows how evenly the frames came), and look at the largest ones.
- Once the fix is merged, what is still worth a hold is on the merged commit: the player's own settings first, then a
  confirmation; the rest belongs to whoever watches main.

## 4. The tool

`regress/` under the harness root holds four scripts (standard library only, nothing of a game in them):

```
python3 -I regress.py plan --repo REPO --good GOOD --bad BAD --output NEW_DIR
python3 -I regress.py hunt --repo REPO --good GOOD --bad BAD --scenario LOCAL.json --output NEW_DIR \
    --max-holds N [--max-steps 9] [--max-hold-seconds 480] [--max-wall-seconds 5400] \
    [--exclusive-authorized] [--priority-authorized] \
    [--suspect SHA[,SHA]]... [--switch LABEL:NAME=VALUE[,NAME=VALUE]]... [--suspects-only]
```

- `plan` reads git only and spends nothing: commit subjects/numbers and **conservative compatibility groups**.
  The local grouping hashes all `nx2/` except `build.py`, vendored a64-kit and HLE names. That is broader than
  current upstream `make_game.lift_sources()`; `distinct_lifts` is a group count, not proof that every group
  necessarily changes lifted output. Builder/prompt-only edits can split it. The adapter may do an unnecessary
  full build at a boundary; do not weaken the grouping without proving donor compatibility.
- `hunt` confirms both ends in one hold, tests each suspect and switch on the bad commit, then bisects, choosing the
  middle of the open range among the commits a finished lift already serves. The budgets are the caller's and are
  never raised: holds (every retry is one), bisect steps, wall time, the seconds of one hold. `--exclusive-authorized`
  and `--priority-authorized` are passed only when the main session gave them for this hunt.
- A step is one hold: reference, candidate, reference, candidate. It decides only when every run is clean and both
  reference runs are in their band; anything else (a disturbed, invalid or in-between run, a reference that drifted)
  runs the whole pair again while holds remain, and otherwise the hunt ends `inconclusive` with the smallest range it
  can support. A run that is not valid ends its hold at once (the card goes back); a kernel fault line ends the hunt.
- The result: `range.json`, `steps.json`, `table.md` (one row per run, the rejected ones too), `result.json`
  (`status`: `first_bad`, `suspect_explains`, `not_reproduced`, `inconclusive`; `remaining`; `reason`; `suspects`).
  `first_bad` assumes the measure changes once in the range; `suspect_explains` says what a revert or a switch does on
  the bad commit, which is not the same as the first bad commit.

**Scenario** (local JSON, schema 1): `kind` (`timing`: an exclusive hold; `game-check`: an ordinary hold, pass/fail
only; `check`: no GPU), `seconds`, `repetitions`, `max_retries`, `quiet_cores`, `quiet_peak_cores`,
`repeat_tolerance`, `grace_seconds` (the reserve after each run, 25), `settle_seconds`, `measure` (`name`,
`direction`, and for numbers `good_max` < `bad_min` in worseness units:
negate a value where lower is worse), `build_command`, `run_command` (argv lists, never shell text), and what the
adapters read. Four runs of 90 s with a 15 s reserve fit one 480 s hold with seconds to spare for settling.

**`run_case.py`** runs one scenario through `hs-run.sh --inner` inside the hold (it never asks the gate itself). Its
`run` object: `user` (a save folder), `input`, `env`, `anchor` (`load`: the window starts at the end of the longest
present gap that begins between `load_after` and `load_before` seconds, so a later hitch is not taken for the load;
`first`), `from`, `to`, `expect_log`, `forbid_log`, `window`, `prepare`, `framelog`, `validator_command`. It writes
`result.json` with the frame statistics of the window (mean, median, 1 % low, frames over 16.7, 20 and 25 ms), the
log's 120-present lines (GPU time, replay phases, pipeline waits), start to first present, the readback counts when the
local readback log is there, memory high-water mark, per-thread processor time samples (and from them the game's
processor time a frame), what everything else on the machine used, and the kernel's driver lines. A copy's own build
stamp (`regress-source.txt` beside the binary) must be in the run's log: a copy that holds another build's binary is
not counted. The present trace may have three columns (older builds) or seven. Look at the run's pictures yourself: a
load gap is not proof of the scene.
The wrapper's historical `frames.csv` “swapchain” label also appears headless; infer the clock from the run's
environment, not that label. #2602's readback last-part/settled suffix leaves the prefix parser working but changes
which calls its conditional mean covers. Use the same all-call diagnostic on both builds for per-frame comparisons.
Memory caveat: the live adapter's `rss_parts_mb` selects the final sample, which can be after game exit and
therefore empty. Until the staged repair is installed, read the last live raw `samples.json` record for composition;
it is not necessarily the `peak_rss_mb` high-water composition. `memmap_lines` counts kernel dma-buf refusal lines,
not guest memfd mappings or resident-memory parts.

**`build_case.py`** builds a candidate: a finished build with the same lift key by reflink and `nx2/build.py` (release
mode, the profile at `work/build/hookshot_pieces.prof`), else a full build through the lock and the build gate. Its
`build` object names the local paths, known finished donors with their completion markers, an already accepted SDK,
and any local patch to apply to every candidate (the logging aid). It leaves the build's source stamp beside the binary.

A moving nightly worktree is not an immutable donor. `build_case.py` looks at the donor's current HEAD and
`work/fast` or `work/build`; completed nightly objects may instead be in `work/full-cycles/<cycle>`.
Use the completed `build.json`'s **source_commit**, **work_dir**, immutable game copy and original completion
receipt: make a detached source snapshot of your own at that commit, reflink that completed work_dir into its
`work/build`, verify exact `regress.lift_key` equality with the requested candidate, then name that snapshot
and the frozen copy/receipt as the donor. Never move or edit the active nightly worktree to make it fit.
Further adapter limits (reported from retained source, not a new timing result):
- Numeric `hunt` decisions currently require timing/exclusive mode, even for memory. Ordinary memory/count
  collection uses the held worker without that numeric classifier; this does not authorize an exclusive hold.
- The fast builder replaces binaries, not every shipped asset. A candidate that changes pipeline-state lists
  must regenerate/deploy its own list with the candidate's `nx2/pipeline_states.py` merge step, or use a full build;
  retaining a donor's list invalidates that comparison. Read its CLI rather than guessing arguments.
- The scene-window reader currently requires renderer `frames.csv`; older headless game-check builds may lack
  it. Use an explicitly recorded common-present adapter on both sides, or mark the comparison unjudgeable.
- Fresh rolling builds can have a different recorded relative PGO path and hot-object key than the old bench
  example. Preserve the receipt's actual profile path/flags/key; do not force the old example onto it.


**`test_regress.py`**: game-free checks with made-up histories, synthetic logs and a stand-in adapter (grouping, the
bands in both directions, pass/fail, disturbed and in-between runs, a drifting reference, the stop conditions, the
held worker's order, limit and settling, switches, the adapter's statistics and what it hands the wrapper).

## 5. Running holds by hand

A hunt whose arms are not one good/bad pair (several switches, two scenes, the player's settings) is easier as named
plans run through the same held worker: `regress.py _hold <plan.json>` inside the gate, the plan listing its runs
(`role`, `label`, `copy`, `commit`, `output`, and a `scenario` of its own per run), `seconds`, `grace_seconds`,
`max_hold_seconds`, `settle_seconds`, and for a known periodic disturbance `window` (the seconds after a run's start
its measuring window covers) and `avoid` (`every`, `at`, `for` in seconds of the clock). Write a plan once and never
run it twice: a hold's evidence is not replaced. One waiter of yours in the gate at a time: a second chain waits for
the first chain's process to end, not for a gap between its holds. A queued chain is withdrawn by renaming its
not-yet-started plans aside (the launcher refuses a missing plan) or by cancelling the managed job while it still
waits; a process search for the chain's command text finds nothing and proves nothing. Count the holds you have used
from the result files, not from memory.

## 6. What counts as proof, and how a finding is written

- A cause is shown when taking it back on the bad commit moves the measure from the bad band to the good one in clean
  paired runs, or when the bisect ends on it; "contributes" when it moves part of the way beyond the spread; "ruled
  out" when its runs stay in the bad band. A list of commits that touch the code involved is a lead, not a finding.
- Every table has one row per run with the build, the switches, the scene, the load, and the state of the run.
- Say what the change fixes beside what it costs, so nobody takes back a correctness fix blindly, and what the
  pictures of the runs without it showed (and how many frames were looked at: stills do not prove a flash is gone).
- Whether anything is posted, and where, is the main session's and the user's decision.

## 7. Limits

A quiet machine and the gate's holds (a hold is at most eight minutes; exclusive holds stop every build and check on
the machine); one waiter in the gate at a time; no synchronisation or safety mechanism switched off without the main
session's word (a diagnostic revert of a wait is such a thing: it is named in the task or asked for).
