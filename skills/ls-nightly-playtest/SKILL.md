---
name: ls-nightly-playtest
description: >
  Use when checking or resuming the existing LS or HS rolling-main watch, reading retained evidence,
  or running its offline checks; resume only explicitly authorized work without repeating completed steps.
  Compare controlled play/logs/images and prepare unposted drafts. Read ls-workbench and ls-perf-compare first.
---

# LS nightly playtest

**LS / HS:** the shared helper selects runtime/client/profile/log names; preserve live harness paths and watch
ownership. Published skill IDs use `ls-*` and workbench configuration uses `LS_WB_*`; host cutover is separate
(see `ls-workbench`). A rename does not retarget `origin/main`, release downloads, searches or saved
cycles to Forgejo. Require matching source/profile provenance; never create a replacement watch or migrate credentials.

**Use for:** the existing rolling-main watch, game-free nightly checks and retained regression reports.
Set paths before using the commands:

```sh
H=/home/mooki/Games/.hookshot-harness
P=/home/mooki/Code/hookshot-wt/deps/python/bin/python3
L="$H/nightly-local"
"$P" -I -B "$H/nightly/hs-nightly.py" --help
"$P" -I -B "$H/nightly/hs-nightly.py" rolling --help
"$H/hs-nocore.sh" "$P" -I -B "$H/nightly/test_rolling.py" -v
```

Adopt the existing watch; never start a second one. `rolling --check` can build/run; it is not a status reader.
Read retained state/report files or `nightly_status()` instead. Reports name skills read and tools used.
**Every game run is windowless**, even if `window-ok` exists. Ordinary catalogue nights and the timer remain paused.
Do not edit tools or local definitions while a watch/queued job uses them. Stage any proven repair and let Main
replace it atomically between cycles; a file's presence is not evidence that no process still uses it.

The public tool is `/home/mooki/Games/.hookshot-harness/nightly/`. Its private input and output are
`/home/mooki/Games/.hookshot-harness/nightly-local/`. The code and this skill contain the method, never a route,
place, save, game value, or a private dump path. **Neither the local folder nor the save library is published.**

## Status and limits

The rolling mode uses the existing builder and shared regression runner. Read its retained cycle/report before
describing what actually ran. Offline fixtures are not gameplay, performance, fresh-state or clean-save proof.
Catalogue gameplay has no accepted readiness proof: its old `boot`/`assert_play` treats missing/null loading as false
and does not prove fresh samples. Keep it out of acceptance evidence until that path is repaired and exercised.

Keep `gpu_scenarios_enabled` and `timer_enabled` false for ordinary nights. Rolling has its own `enabled` setting in
`rolling.json` and needs Main's existing authorization, complete fixed-release samples and owned worker identity.
No long soak, exhaustive option/destination sweep, two-game stress or window scenario is authorized by this skill.
The catalogue runner accepts 20–450 seconds, leaving termination time within the eight-minute hold; do not call
that a long-duration leak test. The separate rolling hold plan also stays within eight minutes.

## Rolling test of each new main

This mode is the ongoing performance tester requested by the user. It reuses the full nightly builder and
`regress/run_case.py` with local scenes supplied by the regression investigator; it does not create a second
measurement or bisect implementation.

Read `config.json` and `rolling.json` locally; use their configured Python if it differs from `P`.
Only Main starts/resumes a cycle after checking ownership, policy and the retained receipt.
Choose **one** action, not this entire block in sequence:

```sh
"$P" -I -B "$H/nightly/hs-nightly.py" rolling                         # requested cycle; may build/run
"$P" -I -B "$H/nightly/hs-nightly.py" rolling --check                 # cadence decision; may build/run
"$P" -I -B "$H/nightly/hs-nightly.py" rolling --resume-cycle <ID>     # unfinished work only
"$P" -I -B "$H/nightly/hs-nightly.py" rolling --watch                 # only when no watch exists
```

The first command is a manually requested cycle. `--check` is **not read-only**: it starts a cycle when main has
moved and three hours have passed, or at the six-hour rebuild mark **even when main is unchanged**, as issue #2595
requires. `--watch` repeats this check every five minutes as a managed command, not a timer/unit. A shared lock
prevents overlap. A confirmed regression or build/safety failure stops it. Do not withdraw and re-ask a queued hold:
time waiting for the card is recorded separately and is not part of its eight-minute hold budget.

One cycle fetches `origin/main` once and pins it. Before the six-hour mark, an unchanged completed head writes
`unchanged` and stops. At the mark it still performs a full fresh build. Every new cycle uses unused translation/
object-cache and output folders with fixed compiler, optimization/PGO and worker settings; shared dependencies are
reused without replacing them or silently following older tags' dependency pins.
It records both the builder's duration and elapsed time including its gate wait, and creates an owned reflink copy.
Identical opt-in logging aids are recorded separately from the pinned upstream commit; an already accepted local
SDK may be reused, but the tool accepts no license or download prompt.

Issue #2595's fixed release baseline is configured locally and built once with the same frozen inputs; initialization
measures all three scene/mode definitions with the same adapter. `rolling --baseline` does this without advancing the
main-cycle cadence or replacing the previous main build. An ancestry check alone is not performance evidence.
The watch refuses to start before the required release samples are complete. Reports retain release, previous
cycle and new-main measurements with their original dates and hold identities.
The older user build and optional historical build remain additional references, never substitutes for the release.

Each completed cycle retains its report and an issue draft under the configured private folders.
`rolling --wait-draft` waits for one local outbox event and writes a `delivered/` receipt; it is not a pure status
read. Run only the existing notifier, not competing consumers. It posts nothing. Read the draft and pictures
before Main publishes; keep rejected runs, hardware, build/GPU cost and deviations from the project protocol.

Only the metric configured in the shared scenario receives a paired regression/improvement verdict; other columns,
including peak memory, remain supporting observations. Fixed-release measurements retain their dates and are not
rerun each cycle: their difference from new main is not a contemporaneous paired new/release decision.

**Authorized GPU cost per cycle:** one ordinary shader-preparation hold, one ordinary unmeasured warm-up hold for
the new build's scene/mode definitions, then at most three exclusive measuring holds, each at most eight minutes,
**never `--priority`**. The regression investigator's priority holds go first. Each measured scene runs
new / previous / new / previous in its own hold. The first previous build is an owned, hash-checked reflink of the
configured reference; the supplied reference folder itself is never run or changed. Later cycles compare with the
previous completed cycle's build. A conservative source-compatibility check permits warmed-cache reuse; when cached
preparation and all warm-ups fit, they share one ordinary hold. Warm-up values are never reported as measurements.
Every report states actual GPU gate occupancy per build; unavailable hold durations stay unavailable, not zero.

The player-folder rule applies to the whole cycle and every gate wait. Two continuously observed player-idle minutes
are required before work starts; a game appearing there stops owned work, with incomplete runs excluded. A disturbed
or invalid hold is retained, never silently replaced by a favorable sample or repeated outside the approved budget.

The quiet rule is the regression tool's: everything except the game uses **under three cores on average**, no
five-second sample exceeds **six cores**, and each judged arm repeats within **5%**. One-minute load averages remain
in the table but do not substitute for these counters. Both pairs must point the same way, their ranges must not
overlap, and the smallest paired gap must exceed the sum of the two arms' repeat ranges; the shared paired
classifier must confirm the bad band. The local scene names its judged metric. Other columns support the diagnosis,
not independent unconfirmed regression claims.

Each scene table shows fps, mean frame ms, 1% low, frames over 20/25 ms, all-thread game CPU ms/frame from the shared
adapter, readback wait/frame, peak RSS, competing CPU use, loads and whether each run counts. Fixed reference rows
retain their actual measurement dates and are not rerun every cycle; a reference not yet built/measured says so.
Source-stamp checks are per arm. New copies/reference snapshots receive a checked `regress-source.txt`; historical
receipts without it remain readable under their recorded log expectation, never rewritten. A confirmed range is handed
to `regress.py` with its actual scene/bands; the tool **starts no hunt**. Execution requires the source-aware adapter.

All definitions, references, results and reports are private under `nightly-local/rolling/`; measurements also enter
the existing results database. Raw/copies retain seven calendar dates, while dated fixed-reference values persist.
`--resume-cycle ID` reuses a completed build and completed holds; it never queues a duplicate or exceeds the hold
budget. Reports go to the user through Main. The rolling watch posts nothing and installs no timer.

## Offline tool checks

These use stand-ins, made-up numbers and temporary state; no game, full build or account operation:

```sh
"$H/hs-nocore.sh" "$P" -I -B "$H/nightly/test_compare.py" --mutations
"$H/hs-nocore.sh" "$P" -I -B "$H/nightly/test_driver.py" -v
"$H/hs-nocore.sh" "$P" -I -B "$H/nightly/test_rolling.py" -v
```

The rolling suite imports the installed shared regression rules; `--shared-dir <directory>` selects an explicit
reviewed version. A passing offline suite does not validate a live watch, game readiness, rendering or timer.

## Run a night

`run --game-free` is **not** the offline self-test: it fetches a release, performs a full game build and runs that
checkout's suite. It writes retained state and can make read-only tracker queries. It needs the same owned paths,
reviewed sources, dependencies, build/test gates and current authorization as any build. Main only while paused:

```sh
"$P" -I -B "$H/nightly/hs-nightly.py" run --game-free
```

One lock covers the night; a second invocation exits without a build or a game. A night resolves the `nightly` tag
once using the published release and a tag fetch, records the exact commit, and never substitutes moving main.
It builds through the full-build lock and build gate, eight lift workers and the configured compile workers,
`--no-prepare-shaders`, then makes an owned reflink snapshot. The stable build workspace preserves cache identity.
Build logs, elapsed duration including queue wait, the builder's own duration line, warning lines, hashes and sizes
are kept. **Upstream suppresses compiler warnings; zero logged warnings is not a warning-clean build.**

The selected commit's `tests/run_all.py` runs through `hs-idle.sh`, the pinned Python and `-I -B`. Named passes,
failures, skips and durations are compared with the preceding night. New failures and vanished names are distinct;
slow tests under load are observations, not performance regressions. Existing machine failures remain visible.

The game-free mode never launches a scenario or timer. With `gpu_scenarios_enabled` false, plain `run` also remains
game-free. The game-facing behavior below describes implemented code, **not live-validated behavior**.

Each scenario gets a fresh save copy from `hs-save.sh`, and `hs-run.sh` makes the played copy again. No library save
is modified. Shared checkout selection supplies `scripts/hookshot_ctl.py` or `scripts/longshot_ctl.py` from the
selected commit; there is no second protocol client, and the name alone proves no optional bridge capability.
Waiting in the GPU queue does not consume the scenario's play deadline. A load requires the game-reported map,
expected map when specified, play state, loading false and no event unless explicitly allowed: a title movie with
many draws does not count as play. Actions/replies are retained. Press durations use game frames; scenario waits
and repeated-press intervals use wall time. Exclusive input is not exclusive GPU admission.
The old catalogue readiness predicate can accept missing/null loading and stale state. Do not treat its “play”
result as acceptance evidence. Basic status is not the optional sampled-state stream; capability-check it.
The rolling adapter is separate. Missing data remains unknown, never successful play.

A safety monitor and the shared watchdog cover builds, the suite and scenario execution. Memory available, swap free
and PSI limits come from private configuration; unreadable safety data fails closed. Driver faults/unknown NVIDIA
lines, a closed gate, missing compositor, or the protected play-folder game stop work. Expected allocation/mapping
refusals follow the brief and are retained. A core stops this cycle only when its executable is one of the cycle's
owned copies or a driver fault accompanies it; the stop reason names the executable. Other binaries' cores are
recorded and ignored while the guardian is restarted. Memory, compositor, guard and unknown-metadata failures still
stop work; an unrelated crash never excuses a simultaneous resource or driver fault.

**The player wins, always:** a game whose executable or working folder is in the play folder means the player is
playing. No nightly GPU scenario starts. A running scenario is asked to quit over a separate control connection,
with bounded termination fallback, marked cut short and excluded from baselines and regression decisions. Resume
only after the tool has observed no such process for two continuous minutes; a new appearance resets that clock.
The stop during the actual suite exercised detection and cancellation; clean in-game shutdown is tested with a
stand-in only. Never touch or stop the player's process.

`run --resume --night ID --game-free` continues an interrupted night. It reuses its exact completed build, a complete
stored suite, and completed scenarios; it archives interrupted attempts before retrying incomplete work.
`run --resume-build --night ID --game-free` reuses only that ID's completed build, not a completed suite.
It refuses an existing database night unless `--resume` is used. Inspect `state.json` and receipts first;
never overwrite an earlier attempt or repeat successful setup.

Optional scenario `perf: true` (rolling: `run.perf: true`) retains sampled system context. Read `ls-perf-monitor`;
telemetry does not replace raw frame windows, quiet rules or paired decisions. Unsupported builds may emit no CSV.

## Read a report

`nightly-local/reports/<date>.md` begins with State and says what ran, which commit, outcomes, durations, known test
failures, findings, improvements and what could not be judged. `state.json` points at it. Evidence is under
`nightly-local/raw/<date>/`; the played run files are in the owned snapshot named by `build.json`.

- `results.sqlite`: one `metrics` row per night/scenario/metric; scenario JSON, findings and named test results are
  preserved too. A confirmation rerun has separate raw evidence and cannot replace the first sample.
- `baselines/latest.json`: up to seven accepted matching-scenario records. One good night seeds it; three are needed
  for a numeric or picture band. Changed steps/settings change the scenario fingerprint and start a new baseline.
- Both `frames.csv` and `present.csv` are read separately. The game's trace can be restricted to loaded play; the
  renderer's trace has its own zero and is reported over its whole run. Headless renderer intervals describe CPU
  offscreen submits, **not GPU completion or scanout**. No trace is silently replaced by another.
- Metrics include mean/median interval, mean fps, 1% and 0.1% low (1000 divided by the mean of the slowest fraction),
  count and longest frame strictly over 16.7/25/50/100 ms, and the fewest completions in a rolling second. At a 60 cap,
  16.7 ms is sensitive to ordinary pacing jitter; it is not automatically a visible hitch.
- Process RSS, CPU seconds, device-wide and available per-process GPU memory are sampled. Peaks and least-squares
  slopes use active-game samples only. Global GPU memory is not the game's own allocation. Missing process accounting
  stays missing, not zero. Short-run slopes include startup effects and are not evidence of a leak.
- Warnings/errors retain every original line and a normalized signature/count. Shader/pipeline activity in play is
  aligned with nearby present hitches using log-read receipt time, with up to two seconds of alignment uncertainty;
  proximity is not causation. Core info and an all-thread GDB backtrace are kept when a matching core is available;
  unavailable symbols are not invented. No core is exported or published.
- Fixed-event images use blurred 64x36 RGB features and normalized RMS distance to the last good night's image.
  Tolerance comes from the pairwise distances across 3–7 good nights. `black` means all reduced luminance <=2/255;
  `blank` means luminance range <=2/255. A short capture burst with a middle luminance reversal >=40/255 and endpoints
  within 5/255 is a **flash candidate**, not proof; sparse capture can miss flashes. These diagnostic limits are not
  empirical regression bands. Look at every cited image before publishing, including every candidate.

## What the bands mean

For the last 3–7 accepted values, center = median. Half-width = max(observed maximum absolute deviation,
3 × 1.4826 × median absolute deviation). The metric's variation, not a guessed percentage, sets the band. A perfectly
constant history has zero width; a timing/memory outlier still requires one independent rerun beyond that same band.
There is no regression claim from the first night.

An ordinary gate run is always marked timing-disturbed, even if its sampled load happens to look low. Compilers,
other games and load are also recorded. Disturbed timing/memory values cannot populate their numeric baseline,
confirm an outlier, or establish an improvement. They still carry useful pass/fail, log, image and count evidence.
This initial nightly does **not** schedule exclusive timing holds; a dedicated approved quiet-run path is needed
before it can accumulate performance baselines. Never reinterpret tonight's raw frame numbers as benchmarks.

A previously passing scenario that fails, a new warning/error signature, a count beyond its empirical band, a crash,
or an image beyond historical tolerance creates a finding. First-night faults remain first observations, not a
fictional regression range. Tests for bands, disturbance exclusion and signatures use only made-up inputs:

```sh
"$H/hs-nocore.sh" "$P" -I -B "$H/nightly/test_compare.py" --mutations
```

The mutation mode proves the same tests fail when spread, disturbed-run exclusion, or signature normalization is
removed. It does not start a game, build a game, contact a service, or require a dump.

## Add a local scenario

Create one JSON file under `nightly-local/scenarios/`, never beside the public code. Fields: `schema_version: 1`,
unique safe `id`, `description`, optional catalogue `save`, `boot: continue|title`, `seconds` (20–450),
`boot_timeout`, optional `expected_map`, `settings` (environment names/values), `affected_paths`, `issue_query`, and
`steps`. `enabled: false` excludes it; `order` controls scheduling. `intel` and `whole` select existing guarded
modes and still need their resource policy. Keep `window` absent/false; the old runner switch is not permission.

Operations: `wait` with seconds; `shot` with name; `burst` with name/count; `request` with a control request and
optional dotted-field `expect`; `assert_play`; `assert_log` with pattern/minimum; `press_until` with seconds,
buttons/every; `teleport` with `to` and `timeout`; `options` with `values` mapping row keys to ordered lists. Options
are read, set through the menu setter, verified and restored. A failed/unreachable value fails the step. Do not use
a generic option sweep to enable a safety override, microphone, or higher output size without its required policy.

Catalogue scenarios are regenerated from `saves/catalogue.json` and the private `catalogue-template.json` each night.
New catalogue entries are therefore included automatically; existing hand-written files do not contain copied saves.
The library is the input, not a distributable artifact. Another tester obtains equivalent early saves by playing a
scripted new-game route on their own copy and storing its own saves, or by using the cleaned save maker with a local
names file and their own seed save. The maker edits a seed; it does not reconstruct another person's complete
progress or create a valid account from nothing. Proven and unproven routes must stay distinct in local notes.

## From finding to issue draft and hunt

The tool searches open issues and all-state pull requests before writing a local draft in the configured drafts
folder. It keeps search output, full commit subjects/PR numbers for last-good..tonight and the commits touching the
scenario's `affected_paths`. These are suspects by overlap, not proven causes. The game-problem form's headings are
used, with evidence paths and explicit unchecked publication prerequisites. The live `report.py` still emits the
obsolete browser `Progress` form for ordinary nightly updates. Main must convert such a draft to the current
team-update form after reading the room; do not pass it straight to `team_chat.py`.
**Nothing is posted, pushed, attached or shared automatically.**

Read `ls-issue-triage` and `ls-pr-evidence`; inspect matching issue threads, choose an existing issue
rather than a duplicate, choose severity × reach × confidence from evidence, review images and redact paths.
A result is called a finding only after reading that whole thread and `POSTED.md`; the same already-posted
outcome is a repeat, not a new finding. A moved PR's parked/hold-off description stops proposed testing.
The first-run report can contain unresolved baseline failures without inventing a past good commit.

`ls-regression-hunt` owns bisection, equal-lift-input fast builds, revert suspects and endpoint decision rules.
The nightly emits `hunt-scenario.json` and `hunt-handoff.json` with the exact scenario/measure, good/bad commits and a
cap of 8 steps/90 minutes, and invokes the hunt's read-only `plan` when installed. It reuses the shared
`regress/build_case.py` and its own `hunt.py` held-control adapter, never a second build/bisect implementation.
Ordinary pass/fail hunts can run when private `hunt_game_checks` is enabled and four endpoint runs plus the shared
tool's 25-second per-run evidence reserve fit eight minutes. `hunt_seconds`, when specified in a local scenario,
must be a separately proven bound that still completes all its steps; do not shorten a test to fit.
Numeric hunts remain handoffs until specific quiet-hold authorization is given. Log/image findings need a
deterministic validator before they can decide a hunt. The shared result is included in the finding and draft;
unsupported or exhausted hunts say why they are inconclusive. The adapter is implemented but not yet exercised.

## Optional timer after reboot — disabled

Do not arm it until the user explicitly asks. Only then enable private `timer_enabled`, and only after a clean full
night (a game-free-only result is not a clean full night):

```sh
"$P" -I -B "$H/nightly/hs-nightly.py" arm --user-authorized
```

The omitted `--at` computes tomorrow's explicit calendar date with `date`; an explicit systemd calendar date can be
supplied instead. `systemd-run --user --on-calendar=...` creates no unit file and touches nothing in `~/.config`.
After that explicit opt-in the transient service uses `run --rearm`; only a clean full completion arms another
night. Failure or game-free-only completion stops the chain. The lock prevents overlap. `timer.json` records the
calendar, unit and command. A reboot removes transient units; re-arming still requires the user's authorization.
No timer has been armed or exercised by this lane.

## Public sync list

`publish.py`'s `GROUPS` is the explicit nightly live-to-repository manifest used by `sync-from-live.sh`;
`HARNESS_MAP` and the other groups carry shared name/perf dependencies and tests, and `SKILL_MAP` carries this skill.
Use those tables, not a second hand-maintained file list.
Never sync configuration, JSON scenarios, baselines, databases, raw runs, reports, drafts, images, saves, game copies,
caches or generated client data.

## Workbench MCP routes

After the operator setup linked in `ls-workbench` (private state root, catalogues, policy and registered roots),
without an MCP client, run `uv run --project mcp ls-wb call nightly_status --args '{}'` from the installed
workbench checkout. `list --json` lists actual tools; `read 'workbench://nightly/<cycle>'` reads a registered report.
These readers are one-shot. The catalogue's **CLI broker** column marks persistent session/job tools; use
`--profile operate` only for authorized work, keep the same state root and read the returned Job's receipt.
Do not replay on a CLI timeout or stop the shared broker as task cleanup.

Check `workbench_status()` and the installed catalogue. All groups below are integrated; live backends
still need operator registration. MCP does not start a rolling watch or arm a timer. Pushes, posts and claims remain
Main's actions. Follow Job receipts/cursors; an evidence reference is not an inspected picture.

| Step | Shell route | MCP tool / group / integration | What it saves |
|---|---|---|---|
| Read rolling status | Read `rolling/state.json`, `latest.json`, selected `raw/<ID>/cycle.json`, baseline and read-only database | `nightly_status(cycle=...)` — knowledge, integrated | One retained snapshot; no accidental `rolling --check`, database creation or claim of live watch ownership |
| Read report / posted obligations | Read retained report and `POSTED.md` | Resource `workbench://nightly/<cycle>`; `knowledge_search(query=..., collection="reports")`; `workbench://obligations` — knowledge, integrated | Bounded revision-pinned report and promises, not arbitrary files |
| Check queue | Existing GPU guard `--status` / gate state | `queue_status()` — status, integrated | Passive live holders/waiters with unknown ETA, no gate mutation |
| Read logs / faults | Retained run log and kernel evidence | `run_logs(run_id=..., last_lines=...)`; `run_faults(run_id=...)` — evidence, integrated | Bounded signatures and explicit missing-journal coverage |
| Compare / scan / panel | Shared paired classifier; `"$P" -I -B scripts/framelog.py <capture> <new.png>` from trusted current main; `panels.sh` | `evidence_compare(pairing=...)`; `evidence_scan_frames(capture_artifact_id=..., idempotency_key=...)`; `evidence_panels(images=..., label=..., idempotency_key=...)` — evidence, integrated | Shared validity/quiet rules, candidate scan and source hashes; sparse stills remain insufficient |
| Package a result | Retained metrics, table and unposted draft | `evidence_bundle(request=...)` — evidence, integrated | Provenance-linked private package; never an automatic regression claim or upload |
| Run a separately authorized reproduction | `hs-run.sh <owned-copy> <tag> <seconds>` | `session_launch(build_id=..., profile_id=..., wall_seconds=..., idempotency_key=...)` — session, integrated | Owned windowless session from registered profile; not a substitute rolling runner or catalogue-readiness fix |

