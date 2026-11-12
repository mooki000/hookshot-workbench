---
name: hookshot-regression-hunt
description: >
  Prove a Hookshot or Longshot regression, test a named suspect by switch or revert, test a proposed fix, then bisect a confirmed
  range with the shared regression worker. Cover frame time, stutter, startup, memory observations, drawing and
  pass/fail checks without confusing ordinary runs with authorized timing. Read hookshot-workbench and
  hookshot-perf-compare first. Use registered MCP evidence readers, not a second hunt runner.
---

# Finding a Hookshot regression

**Hookshot / Longshot:** the shared worker selects executable, profile, settings and existing log names; missing
or mixed identities refuse. Preserve skill/harness names, gates and `HOOKSHOT_WB_*`. GitHub request/CI readers
remain GitHub-only; a renamed binary is neither a Forgejo backend nor a timing grant.

**Use for:** “which change caused this?”, a named suspect, a proposed fix or a confirmed nightly regression.
Read `hookshot-workbench`, `hookshot-perf-compare` and the current agent brief. Read `hookshot-pr-evidence` before
preparing tracker text. All game runs are windowless; a swapchain-only question remains untested. No safety mechanism
is disabled without explicit authorization. Workers never push or post.

```sh
H="$HOME/Games/.hookshot-harness"
P="$HOME/Code/hookshot-wt/deps/python/bin/python3"
```

The live tools are in `$H/regress`; the public workbench copy uses `regress/`. The four scripts are `regress.py`,
`build_case.py`, `run_case.py`, and `test_regress.py`. Their code is game-free; scenario paths, saves and input routes
stay local. Set `REPO`, full `GOOD`/`BAD` commits, `SCENARIO` and a new output directory before using the commands.

## 1. Prove and plan the range

1. Read the whole current request/thread and posted-work record. Check whether a proposed fix is ready for tests.
   Pin the actual head and the commit its changes sit on. A merge base with current main can be wrong for a branch
   stacked on a squash-merged PR. Do not attach old-head results to a moved head.
2. Record both binary hashes, source stamps, settings, release/profile identity and cache state. The log's source
   stamp is not a Git commit. A two-setting test on one binary is not an old/new build comparison.
3. Use immutable owned copies without `mods`, `HOS_MODS`, `hookshot_options.ini` / `longshot_options.ini` or
   `hookshot_env.txt` / `longshot_env.txt` (the shared `ALL_SETTINGS` inventory), unless an explicitly reviewed
   scenario records intentional copy settings. Match scene, save, input, output size, cap and
   upscaler. Warm each scene/mode unmeasured first; copy caches only from an idle finished donor.
4. Plan without a game or build:

   ```sh
   "$P" -I -B "$H/regress/regress.py" plan --repo "$REPO" --good "$GOOD" --bad "$BAD" --output "$PLAN_OUT"
   ```

   `GOOD` must be an ancestor **on BAD's first-parent history**. The output directory must not exist. Read
   `range.json`: commits, subjects/PR numbers, groups and `distinct_lifts`. The key hashes all `nx2/` except
   `build.py`, vendored a64-kit and HLE names; it is broader than current upstream `lift_sources()`. A group is
   conservative compatibility, not proof that a new lift is necessary or sufficient for every generated asset.
5. Select the actual path under test. A present/swapchain change cannot be judged by a windowless run. An input
   acknowledgement or load gap does not prove the intended scene; require fresh state and inspected pictures.
   If the binary lacks a required diagnostic, apply the same reviewed instrument to both arms and disclose it.
6. For timing, obtain authorization for the named exclusive holds. Use reference/candidate/reference/candidate in
   one quiet hold. Other-work mean must be below `quiet_cores` (default 3), its five-second peak at most
   `quiet_peak_cores` (6), and same-arm repeat spread at most `repeat_tolerance` (0.05). Load averages are context.
   Do not quote a rate from an ordinary shared run. Keep every rejected row.

## 2. Test suspects or a fix, then hunt only the remainder

1. Read a suspect's code and platform-specific defaults on both ends. A switch that already had the same behavior
   on both commits cannot alone explain their difference. A wait may repair correctness; record what disabling it
   gives up and the expected log/picture before requesting a diagnostic run.
2. Build a local scenario with **`schema_version:1`**, not `schema:1`. Use the exact decision metric returned by the
   adapter. Missing or nonnumeric decision metrics produce an invalid result with `value:null`, never a measured zero.
   Check the retained `measures` and rejection reason before accepting any classification.
3. Choose the kind: `timing` is numeric and exclusive; `game-check` is ordinary GPU pass/fail; `check` is CPU-only
   pass/fail. Numeric decisions currently require `timing`, even for memory. Collect ordinary memory/count results
   without that classifier; do not request exclusive time merely to satisfy it.
4. Use a new `HUNT_OUT`, not the earlier plan directory. For an authorized timing hunt:

   ```sh
   unset HSGATE_SLOT_DIR HSGATE_INTEL_SLOT_DIR HSGATE_SLOT_TEST HSGATE_SLOT_NODESKTOP HSGATE_IN_GATE HSGATE_IN_INTEL_GATE
   "$P" -I -B "$H/regress/regress.py" hunt --repo "$REPO" --good "$GOOD" --bad "$BAD" \
     --scenario "$SCENARIO" --output "$HUNT_OUT" --max-holds "$HOLDS" --exclusive-authorized
   ```

   For a reviewed `game-check`/`check` scenario omit `--exclusive-authorized`. CPU checks still use the idle/core-dump
   wrapper in their trusted adapter. Every gate/run budget stays at the granted value.
5. To test only named leads, add `--suspects-only` and one or more `--suspect "$SHA"` or
   `--switch 'label:NAME=VALUE'`. A comma-separated suspect group is reverted together; a switch can set several
   comma-separated `NAME=VALUE` pairs. Each suspect must lie inside `GOOD..BAD`. Reverts are made in the hunt's own
   detached worktree. A partial switch tests only that part of the commit.
6. The hunt first confirms both endpoints, then tests each suspect against the bad build. Without `--suspects-only`
   it continues bisecting the **original range**, even if a suspect helped. To decide whether more hunting is useful,
   compare the good build with the bad build after the suspects are removed, in an explicitly planned hold. The tool
   does not automatically combine suspects or measure that remainder.
7. For someone's fix, compare its exact base/head with the same diagnostics and settings. Check that the changed
   game-free test fails on the base for the intended reason, not merely missing source text. Run relevant compiler
   variants through the test gate. A syntax check is not Windows runtime coverage.
8. Read `result.json`, `steps.json`, `range.json` and `table.md`. `first_bad` assumes one change of the measure in the
   range. `suspect_explains` says a tested revert/switch reaches the good band, not the first bad commit.
   `not_reproduced` and `inconclusive` are real outcomes. Retain the remaining range and reason; never increase the
   retry/hold budget or weaken an assertion to get an answer.

### Scenario and adapter contracts

- `seconds`, `repetitions` (default 2), `max_retries` (1), `grace_seconds` (25), `settle_seconds`, `quiet_cores`,
  `quiet_peak_cores`, `repeat_tolerance`, and `measure` are scenario fields. `measure.direction` is
  `higher-worse`, `lower-worse` or `pass-fail`; numeric `good_max < bad_min` are **worseness** bands, so negate a
  metric where lower is worse. `build_command` and `run_command` are trusted argv lists, never shell text.
- CLI budgets are `--max-holds` (default 1), `--max-steps` (9), `--max-hold-seconds` (480) and
  `--max-wall-seconds` (5400). Each retry consumes another hold. Queue/build time consumes the outer wall budget.
  Fit paired runs with `2 * repetitions * (seconds + grace_seconds) + 10 <= max_hold_seconds`. A planned 480-second
  hold is a cap, not observed occupancy. Keep queue evidence and `.hold.json` clocks separately.
- `run_case.py` is called **inside** the worker's gate via `hs-run.sh --inner`; never call `--inner` to bypass a gate.
  The scenario's `run` object accepts `user`, `input`, `env`, `anchor` (`load` or `first`), `from`, `to`,
  `load_after`, `load_before`, `expect_log`, `forbid_log`, `prepare`, `framelog`, and `validator_command`.
  Do not set `window` under current policy. `anchor:"load"` chooses the longest qualifying gap, not proof of a scene.
- Results include the selected window's mean/median/p99, 1% low, maximum, `over16_7`, `over20`, `over25`, log blocks,
  startup time, sampled memory and thread CPU, quiet evidence and driver lines. There is no `over33` column or
  automatic 30-second quiet segmentation. Extra thresholds must be derived from the same retained window and named.
- The scene reader requires renderer `frames.csv`. It accepts old three-column and newer seven-column present traces
  for its other calculations; that does not add a renderer fallback. Headless renderer timing is not display timing.
  `RX_VK_FRAMELOG` is the pixel stream and remains swapchain-only; do not infer continuous visual coverage from stills.
- `rss_parts_mb` currently uses the final sample, possibly after exit. Read the last live `samples.json` record for
  composition, not as the high-water composition. `memmap_lines` counts kernel import-refusal lines, not guest mappings.
- Inspect `kernel.txt` and the journal exit/stderr fields using the brief's signature rule. Repeated markers inherit
  their preceding message; only the named complete same-second allocation triple is exempt. Failed/partial journal
  coverage yields `kernel_errors:null`, `kernel=unknown` in the summary and no automatic hunt retry.
- `build_case.py` uses an explicitly recorded immutable prebuilt endpoint or a finished compatible donor; otherwise
  it takes the full-build lock and build gate. It never prepares shaders. Its `build` configuration supplies paths,
  profile files, optional already-accepted SDK and an identical diagnostic patch for every candidate.
- A nightly worktree can move and completed objects may be in a cycle-specific directory. Use its completed
  `build.json` source commit, work directory, game copy and completion receipt to make an owned detached donor snapshot.
  Match the real profile identity and lift key; never move/edit the active nightly worktree. The fast adapter replaces
  binaries, not every shipped asset: candidate pipeline-state lists need generation/deployment or a full build.
- The run adapter requires a nonempty `regress-source.txt` and its match in the game log. Missing provenance is an
  invalid run, even if another expectation matched. Keep the commit, binary hashes and source stamp in the build receipt.
  Nightly writes/checks the marker on new built copies and new hash-checked reference snapshots; it never adds one
  to an old/live copy during hand-off. Historical comparisons still read older receipts without a dedicated stamp,
  labelled `stamp absent, guarded by its log expectation`; they are not retroactively rejected or rewritten.

## 3. Run a reviewed multi-arm plan and read its evidence

Use the same `_hold` worker for several switches/scenes. There is no separate timing runner or MCP hunt operation.
A plan needs `kind`, **`entries`**, `run_command`, `scenario`, `seconds`, `grace_seconds`, `max_hold_seconds` and any
`settle_seconds`/`quiet_cores`. Each entry needs `role`, `label`, `copy`, `commit`, `output`; an entry's optional
`scenario` is a **path to a local JSON file**, not an inline object. `{seconds}` and `{scenario}` are substituted into
`run_command`. Use new output paths and run each plan only once; `_hold` does not refuse an old plan by itself.

1. Check that all entries fit the cap, including the ten-second worker reserve. Set `window` and `avoid`
   (`every`, `at`, `for`, in seconds) only from an observed periodic disturbance. These are `_hold` plan fields;
   the hunt's automatic plan does not copy them from a scenario.
2. After authorization for that one timing hold, use the existing GPU gate and an outer shutdown bound:

   ```sh
   unset HSGATE_SLOT_DIR HSGATE_INTEL_SLOT_DIR HSGATE_SLOT_TEST HSGATE_SLOT_NODESKTOP HSGATE_IN_GATE HSGATE_IN_INTEL_GATE
   "$HOME/Games/hs-gpu-slot" --exclusive timeout --signal=TERM --kill-after=5 475 \
     "$P" -I -B "$H/regress/regress.py" _hold "$HOLD_PLAN"
   ```

   This example is only for a reviewed plan with `max_hold_seconds:480`. The real gate supplies its marker; never
   set one yourself. Never nest it inside the Intel gate. A non-timing game-check uses ordinary admission, with
   `--whole` when its output/memory needs both slots; it is not a speed measurement.
3. Keep one managed chain and its real process identity. Wait for its owned child/receipt, not a `pgrep` name match,
   zombie PID or a gap between holds. End owned games before releasing their gate. Preserve `.runs.json`,
   `.hold.json`, per-run `result.json`, `summary.txt` and raw artifacts. An invalid run ends the hold.
4. Use the shared classifier on explicit pairs. A cause is supported when taking it back reaches the good band in
   clean paired runs, or a valid bisect isolates it. A partial movement beyond spread is a contribution, not the
   whole cause. Explain what the correctness change fixes beside what it costs.
5. Give one row per run: build, settings, scene, actual clock/window, load and validity. Inspect the largest visual
   events and name the frames inspected. A detector's synthetic fault tests the instrument only; a control that
   never shows the real fault cannot prove a fix. The main session decides whether and where anything is posted.

## Game-free reader check

```sh
"$H/hs-nocore.sh" "$P" -I -B "$H/regress/test_regress.py"
```

The tests exercise made-up Git histories, public `plan`, both band directions, pass/fail, invalid/disturbed runs,
reference drift, held-worker order/bounds/settling, switches and a stand-in wrapper. No game or GPU is started.
Use `--help` for `regress.py`, `build_case.py` and `run_case.py`; `_hold` itself takes a plan, not `--help`.

## MCP routes

Use the workbench skill's discovery/configuration and completion rules. Integrated means registered server code,
not a configured backend. Inspect the live catalogue. No tool here runs a bisect or grants timing holds.
After the operator setup linked in workbench, use `uv run --project mcp hookshot-wb list --json`, `call TOOL --args JSON`
or `read workbench://policy` from the reviewed repository root. Jobs persist in the broker; retain their receipts.
Neither `--profile operate` nor `broker status` grants a hold; `broker stop` ends owned work.

| Step | Shell route | MCP tool (group; integration) | What it saves |
|---|---|---|---|
| Read request and prior proof | Current thread, posted-work record, report sections | `github_thread_read(repository, number)`, `knowledge_search(query, collection="reports")`, `workbench://reports/{id}` (knowledge; integrated) | Head/snapshot, bounded pagination and revision-pinned resource reads. |
| Plan/build candidate | `regress.py plan`; `build_case.py` | `build_plan(repo_id, commit, profile_id)`, `build_start(plan_id, idempotency_key)` (checks; integrated) | Registered pinned build plans and receipts, not a history hunt. |
| Game-free/base checks | `hs-idle.sh ... tests/run_all.py`; Windows helper | `checks_source(repo_id, commit, idempotency_key, compare_with_base=base)`, `checks_windows_syntax(repo_id, commit, base, idempotency_key)` (checks; integrated) | Exact test selections, retained failures and explicit unjudged Windows execution. |
| Observe gate | Ticket/lock/process inspection | `queue_status(device="gpu")`, `job_read(job_id)` (status; integrated) | Observed queue/hold clocks and receipt, not an ETA. |
| Ordinary state/pictures | Bounded `hs-run.sh`; control client | `session_launch(build_id, profile_id, wall_seconds, idempotency_key)`, `session_state(run_id)`, `session_shot(run_id)`, `session_stop(run_id, reason, idempotency_key)` (session; integrated) | Owned disposable lifecycle; never a substitute for the exclusive pair. |
| Hunt/held worker | `regress.py hunt` / `_hold` | **None** | Keep the existing worker and authorization boundary. |
| Analyse/compare retained pair | `run_case.py`/shared paired readers and raw logs | `run_logs(run_id)`, `run_faults(run_id)`, `evidence_analyze(run_id, analyzer_id, arguments, idempotency_key)`, `evidence_compare(pairing)` (evidence; integrated) | Bounded inputs, clock/settings/quiet validation and exactly two explicit pairs. |
| Scan/panel retained visuals | `scripts/framelog.py`; labelled `panels.sh` | `evidence_scan_frames(capture_artifact_id, idempotency_key)`, `evidence_panels(images, label, idempotency_key)` (evidence; integrated) | Idle-gated scans and panels; human inspection remains required. |
| Pre-push/CI/private draft | Existing pre-push/CI readers and evidence table | `checks_pr(repo_id, commit, branch, title_artifact, idempotency_key)`, `ci_read(repository, pull_request)` (checks; integrated); `evidence_bundle(request)` (evidence; integrated) | Retains imports, failing names and private evidence without publishing. |
