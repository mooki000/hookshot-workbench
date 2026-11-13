---
name: hookshot-workbench
description: >
  Start here when building, running, testing or debugging Hookshot or Longshot on this machine. Locate the owned worktree,
  dependencies and harness; build without starting a game; run windowless through the resource gates; collect
  logs and pictures; run game-free and Windows-target checks. Use registered workbench MCP operations where
  configured, without bypassing the gates or treating ordinary runs as performance measurements.
---

# Hookshot workbench

**Hookshot / Longshot:** Longshot continues the project at `https://git.magicbean.dev/MagicBeanDev/Longshot`.
Keep skill names, local harness/clone paths, gates and `HOOKSHOT_WB_*` unchanged. `hs-names.sh` / `hs_names.py`
select the owned checkout/copy's runtime names; missing or mixed identities refuse. Existing `gh`, release, CI and
queue routes below remain GitHub-only. Do not substitute forge names or migrate credentials; use reviewed public
Forgejo GET readers. Proposed forge login/read helpers can change credentials and are not an approved workaround.

**Use for:** every Hookshot or Longshot task on this machine. Read the agent brief, then the matching task skill.
**All game runs are windowless.** A leftover `window-ok` is not permission. Do not send anything to the compositor.
Reports name the skills read, tools used, pinned sources, checks exercised and work not tested.

## Set up the paths and rules

```sh
H="$HOME/Games/.hookshot-harness"
W="$HOME/Code/hookshot-wt"
P="$W/deps/python/bin/python3"
CPM="$W/deps/cpm"
```

Read `$W/reports/AGENT-BRIEF.md`. In the selected Hookshot or Longshot checkout read `docs/workflow.md`, `AGENTS.md`,
`CONTRIBUTING.md`, `docs/development.md` and the relevant `docs/design.md` section. Set `WT` to your owned worktree,
`COPY` to your owned built copy, and `OUT` to your new evidence directory before using the examples.
Source `"$H/hs-names.sh"` once. Run `hs_names checkout "$WT"` after selecting/creating a worktree; repeat it when
`WT` changes. It supplies `HS_EXE`, `HS_RENDERER`, `HS_PROFILE` and `HS_CTL`. `hs_names copy "$COPY"` selects
`HS_LOG`, options and other copy names. `hs_names.log_path(run, required=True)` selects existing evidence; it never
invents a missing log. A pre-client checkout can be named from its build declarations without having a usable client.

```sh
source "$H/hs-names.sh"
```

| Thing | Location |
|---|---|
| Legacy clone and GitHub remote names | `$HOME/Code/Hookshot`; `origin`, `fork` |
| Worktrees and reports | `$W/<name>`; `$W/reports` |
| Read-only dump | `$W/work/dump` (`exefs/`, `romfs/`) |
| Pinned Python and headers | `$P`; `$CPM` |
| Run, test and local play tools | `$H`; `$H/playthrough`; `$H/regress` |
| Owned game copies | `$HOME/Games/.hookshot-h-<name>` |
| GPU, Intel and build gates | `$HOME/Games/hs-gpu-slot`, `hs-intel-slot`, `hs-build-slot` |

- No game files, saves, lifted code, world-model facts or routes leave this PC or enter git. Keep game-content notes
  in `$H/playthrough/NOTES.md`; dump/save provenance belongs in `$H/LOCAL-NOTES.md`, not a public skill.
- Never touch the user's `Games/Ocarina*`, `Games/Hookshot`, `Games/Longshot`, `Downloads`, `.config`, global git
  configuration or system packages. Never run or test in a player folder. No code from the other port's repository.
- Every Vulkan/GL device uses its device gate. Builds use the build gate. Full builds also take
  `/tmp/hookshot-full-build.lock` and set the checkout-confirmed `HOOKSHOT_LIFT_JOBS=8` or `LONGSHOT_LIFT_JOBS=8`
  through `hs_build_env` below. Do not edit build inputs while a build runs.
- Before a gated command, unset the test/bypass variables below. Never set them to skip admission.
- One waiter of yours per gate. A GPU hold is at most 480 seconds **including shutdown**; use 450 seconds or less
  for an ordinary game so the wrapper's 20-second kill reserve fits. The GPU gate itself does not enforce the limit.
- Only an explicitly authorized timing hold uses `--exclusive`; only a named short run uses `--priority`.
  `--whole` reserves both GPU slots, not CPU quiet. Read `hookshot-perf-compare` before any speed claim.
- Workers never push or post. Use the brief's author and trailers. Leave SDK licence acceptance to the user.
- Before reading lifted code or changing `nx2/oot_*.py`, look the `f_<address>` (or the subject) up in the function
  map: `skill://ls-function-map`. What you work out goes back as a row in your report.
- End every final report with **Skill notes**: what in a skill was wrong, missing or misleading, and what no skill
  covers (`skill://ls-skill-upkeep`). Workers report; only the main session edits skills.

```sh
unset HSGATE_SLOT_DIR HSGATE_INTEL_SLOT_DIR HSGATE_SLOT_TEST HSGATE_SLOT_NODESKTOP HSGATE_IN_GATE HSGATE_IN_INTEL_GATE
```

## 1. Prepare and build an owned copy

1. Pin the commit. Create an owned worktree, not a moving donor:

   ```sh
   git -C "$HOME/Code/Hookshot" worktree add --detach "$WT" "$COMMIT"
   hs_names checkout "$WT" || exit
   ```

2. Reuse `$P` and `$CPM` only when their pins match the worktree. The dependency helper is **not** a read-only check:
   changed pins replace its destination. For new pins, install into this worktree, never over shared dependencies:

   ```sh
   "$H/tools/deps-local.sh" "$WT" "$WT/.deps"
   P="$WT/.deps/python/bin/python3"
   CPM="$WT/.deps/cpm"
   ```

   It has no `--help` or dry-run mode. Read it before running it. Do not run Ubuntu's `scripts/linux/deps.sh` here.
   Check the dump without building from the selected checkout:

   ```sh
   cd "$WT"
   "$P" -I -B nx2/dump.py --dump "$W/work/dump"
   ```

3. Use the selected release profile, hot list and link order. The following is the **legacy Hookshot/GitHub**
   download route. Renamed main's `scripts/release_parts.py` still uses GitHub, not Forgejo; for Longshot require
   a separately verified matching release/setup/profile source, not a renamed old profile or an assumed Forgejo
   release. No credential extraction is authorized. If the matching artifacts are absent, create the download parent first;
   keep its cache in the worktree. Record the returned release tag. These commands download; `--help` does not.

   ```sh
   mkdir -p "$WT/.release"
   RUNNER_TEMP="$WT/.release" TMPDIR="$WT/.release" "$P" -I -B scripts/release_parts.py previous "$WT/.release/setup.txt" --repo NotRexed/Hookshot
   RUNNER_TEMP="$WT/.release" TMPDIR="$WT/.release" "$P" -I -B scripts/release_parts.py profile "$WT/.release/setup.txt" work
   ```

4. Make a new evidence directory. Run the full build through both locks, with an owned work/output directory and
   shader preparation disabled:

   ```sh
   mkdir -p "$OUT"
   hs_build_env "$WT" LIFT_JOBS=8 KEEP_BUILD_FILES=1 || exit
   flock /tmp/hookshot-full-build.lock \
     "$HOME/Games/hs-build-slot" env RX_CPM="$CPM" \
     "$P" -I -u nx2/make_game.py \
       --romfs "$W/work/dump/romfs" --exefs "$W/work/dump/exefs" \
       --out "$COPY" --work "$WT/work/build" --jobs 16 --no-prepare-shaders --log "$OUT/build.log"
   ```

   Require exit 0 and `@@done <copy>` in the build log. Shader preparation is a **separate gated game run**.
   A successful launcher exit is not a child receipt. Use `setsid --wait nohup` for a saved long-running launcher,
   retaining its child PID, output and completion receipt. Do not restart a queued build because the launcher ended.

### Incremental host/renderer-only changes

Prefer rebuilding with the same work/output/profile identity. A fast donor build is valid only when the selected
`nx2/make_game.py` lift inputs, HLE names, generated layout and host/renderer API are compatible. A diff limited to
`host/` and `gpu/` alone does **not** prove that. Use the regression builder's conservative grouping and a finished
immutable donor receipt; do not assume the copy named `main` or the old bench is compatible.

After selecting that donor, set `DONOR_WORK`, `DONOR_COPY` and the receipt's relative profile path. Copy to **new**
owned destinations with `cp -a --reflink=always`; make `work/` first. For a same-identity donor whose recorded
profile is `work/build/$HS_PROFILE` (`hookshot_pieces.prof` or `longshot_pieces.prof`):

```sh
mkdir -p "$WT/work/build"
cp -a --reflink=always "$DONOR_WORK" "$WT/work/fast"
cp --reflink=always "$WT/work/fast/$HS_PROFILE" "$WT/work/build/$HS_PROFILE"
RX_CPM="$CPM" OOT_WORK="$WT/work/fast" RX_RELEASE=1 RX_PGO="$WT/work/build/$HS_PROFILE" \
  "$P" -I -B nx2/build.py --standalone --jobs 16 --dry-run
```

Inspect the plan and object/profile keys. `--dry-run` does not compile, but may write planning/cache files; it is not
permission to use another worktree. Compile the same command without `--dry-run`, through `hs-build-slot`.
Copy the donor game into the new `$COPY`, remove only that copy's inherited `runs/`, and deploy both
`work/fast/sa/build/$HS_EXE` and `$HS_RENDERER`. Changes to shipped assets, including pipeline-state lists, need
candidate-owned generation/deployment or a full build. Record the source of both binaries and their checksums.

`RX_RELEASE=1` and the donor's actual PGO identity matter. Different profile paths can rebuild hot objects; no profile
is not a comparable build. `-Wno-everything`/`-w` in the build flags mean an empty warning log proves nothing.
Check changed compiled fixtures with their warning flags and the Windows-target helper below.

## 2. Run windowless and collect evidence

Use a new `TAG`; `hs-run.sh` **deletes an existing `runs/$TAG` before starting**. Confirm the copy and output do not
belong to another run. Read the selected binary's switches before adding any `NAME=VALUE` arguments.

```sh
hs_names copy "$COPY" || exit
"$H/hs-run.sh" "$COPY" "$TAG" 90
```

Do not wrap this command in another GPU gate. It already uses that gate, headless/muted defaults, NVIDIA's ICD,
`RX_VK_DEVICE=0`, an empty `HOS_ENV_FILE`, run-local data and copy-local driver caches. Never override its headless,
device, cache or isolation settings to bypass policy. `--user "$SAVE"` copies a save into disposable run storage;
without it the wrapper copies `user-template/` if present, otherwise starts fresh.

Useful options: `--input '<script>'`, `--from <seconds>`, `--keep-user`, `--prepare`, `--whole`, `--intel`,
`--mem-max <size>` and `--perf` (sampled context: read `hookshot-perf-monitor`). Intel cannot combine with `--whole`, `--exclusive` or `--priority`. `--mem-max` uses a systemd
user scope with `MemoryMax` and no swap; it does not prove the game stayed under the cap.

- Prepare only when needed: `"$H/hs-run.sh" "$COPY" "$TAG" 450 --prepare`. Check the actual game's exit 0 and
  `@@done shaders`; a timed-out preparation is incomplete. Do not infer restart/resume behavior.
- A picture larger than 2048x1152 adds `--whole`, based on `HOS_OUTPUT_RES` or the copy's `output_res`. Keep those
  settings unchanged while queued. Ordinary runs may share the card; their printed rates are **not measurements**.
- Inspect `$COPY/runs/$TAG/summary.txt`, `exit.txt`, `$HS_LOG` (`hookshot_log.txt` or `longshot_log.txt`),
  `env-used.txt`, `frames.csv`, `present.csv`,
  `shots/` and `user/save/`. The **game's** wall timeout is 124 in `exit.txt`; the wrapper normally returns 0 after
  writing its summary, even when the game failed. Check the receipt, not only the shell status.
- For scripted control, use `hookshot-scripted-play`. Enable `HOS_CONTROL` on the already-gated owned run. The
  upstream client's unwrapped `launch` is not a workbench launch. Its test profile alone is not a gate or wall limit.
- Keep a live-input file outside the run directory. Set `HOS_INPUT_LIVE` at launch; append complete newline-terminated
  commands. `HOS_INPUT_FRAME_CLOCK=1` counts script time at 60 game presents per second, not wall time under load.

### Pictures and logs

`RX_VK_SHOTS` writes into the run. Defaults are 1280x720 every 300 renderer presents; that is five seconds **only at
60 presents/s**. `RX_VK_SHOT_EVERY=60` changes the cadence, not a wall-clock guarantee. Headless menu capture needs
`RX_VK_SHOT_MENU=1`. A bridge `shot` captures no Insert-menu overlay.

Use the existing labelled-panel helper with explicit input/output names, not `ls | xargs montage`:

```sh
PANEL_W=400 PANEL_H=225 "$HOME/.agents/skills/hookshot-pr-evidence/scripts/panels.sh" \
  "$OUT/panel.jpg" 2 "$FRAME_A" "1. Before" "$FRAME_B" "2. After"
```

Choose a new output path distinct from all inputs. Inspect the resulting image before citing it. Stills do not prove
that a continuous flash is absent. `RX_VK_FRAMELOG` is the separate pixel stream and remains swapchain-only.

`RX_VK_FRAME_LOG` is timing CSV. Headless renderer intervals are CPU intervals after the offscreen copy submission,
not swapchain waits or display latency. The wrapper uses populated renderer CSV and labels it `headless-renderer`
or `swapchain`; empty/missing renderer CSV falls back to three- or seven-column `present.csv`, labelled `game`.
The regression reader also accepts either present-trace form, but its scene analysis still requires renderer
`frames.csv`. Keep raw files and name the actual instrument; old summaries can carry the former misleading label.

The log's `source <hex>` is a content fingerprint, not a Git commit. Pin the commit separately. Stop owned work and
tell the main session at any Xid, `kmemsysDoCacheOp` or unknown NVRM signature. Count known mapping refusals and the brief's
bounded allocation-refusal exception separately, including repeated-message markers. `nvrm=` alone is no verdict.
Keep `kernel.txt` and `kernel-status.json`: the latter retains journal exit/stderr. Missing/partial journal coverage
prints `kernel=unknown nvrm=unknown journal=unknown`, never zero. Check `DEVICE?`, `fatal`, `crash`, `devlost`,
`HOME-WRITES` and the intended end. `HOME-WRITES` tests path existence, not whether this run created it; investigate
without changing the user's files. Older runs may lack the journal receipt; a later successful read cannot prove its
original command succeeded.

A `vcores: X ran without a core after 2000 ms: core N: Y` line names the thread that gave up waiting and the thread
that owned the core **at that instant**; it does not say Y held the core for two seconds. Guest thread names are not
unique (a pool's workers share one), so neither name identifies one worker. What those threads are in the game:
`skill://ls-function-map`.

Observe the queue without modifying tickets: the GPU state is `/tmp/hs-gpu-slots/` (`queue/`, `yielded/`,
`holder.0`, `holder.1`, `last-kind`, `long-holds.log`, `closed`). File contents alone do not prove process liveness.
The guard's `--status` reports guard/card/slot state, **not queue position**. Prefer `queue_status` when configured.

## 3. Run game-free checks and hand off

Run the suite once at the end of the owned change, through the idle gate, with a fresh HOME:

```sh
cd "$WT"
TEST_HOME=$(mktemp -d)
HOME="$TEST_HOME" RX_CPM="$CPM" "$H/hs-idle.sh" "$P" -I -B tests/run_all.py --jobs 6 --strict
```

The test gate has three slots, uses idle scheduling and suppresses core dumps. It can wait behind an exclusive hold;
no output while queued is not a failed test. Keep its child receipt. A single game-free test may use
`"$H/hs-nocore.sh" "$P" -I -B <test-file>` without the idle gate. Never run the full suite that way.

Check the selected tests' literal `RUN_ALL_ARGS`, `RUN_ALL_MODULES` and `RUN_ALL_SKIP` declarations. `--strict` fails
missing prerequisites; `--shard K/N` partitions CI, not full coverage. A missing dependency is not a pass. Retain the
`pass`/`FAIL`/`TIMEOUT` rows and totals. Check for your unintended dumps with
`coredumpctl list --since '-30min' --no-pager`; do not confuse other work's dumps with yours.

Run the whole suite before handing over a head, not only the tests a change seems to touch: the project's rule tests
(`tests/test_env_on.py` for on/off switches, `tests/test_menu_i18n.py` for translated menu texts,
`tests/test_readme_menu_rows.py`) catch changes far from their files. On a PC whose .NET cannot restore
`Microsoft.AspNetCore.App.Ref` offline, `tests/test_updater_asset_digest.py` fails with `NU1100` on plain main too:
report it as inherited and local after confirming the same failure on the base commit; do not hunt for an SDK.

Changed C/C++ fixtures also need:

```sh
HOME="$TEST_HOME" HS_PYTHON="$P" RX_CPM="$CPM" "$H/hs-idle.sh" "$H/hs-win-fixture-check.sh" "$WT" "$BASE"
```

Keep unsupported discovery visible. Syntax success is not Windows execution. Read `hookshot-pr-evidence` for the
pre-push checker, imported-author rules, CI comparison and drafts. The main session alone syncs live skills into the public
workbench and commits. Run `sync-from-live.sh` only when that integration is explicitly delegated.

## MCP routes

Use registered IDs and current policy. From the reviewed workbench repository root, after the operator's one-time
setup in `mcp/README.md` (**Install and start**, **Configuration**, **Current operator policy**):

```sh
uv run --project mcp hookshot-wb list --json
uv run --project mcp hookshot-wb call workbench_status --args '{}'
uv run --project mcp hookshot-wb read workbench://policy
uv run --project mcp hookshot-wb broker status
```

That setup supplies the private state root, catalogues, policy and registered roots; do not duplicate or relax it.
Use `call TOOL --args JSON` with the advertised complete schema; authorized local/run tools need `--profile operate`.
Simple readers are one-shot; live sessions/jobs share a persistent private broker. JSON results use stdout; errors
use stderr. A returned Job is submission, not completion. Keep its idempotency key; never replay an uncertain reply.
For a launch, read `job_read`'s `.events.events[]`, selecting `.kind == "bridge_ready"`; that is only bridge readiness.
Before first input/picture, observe `menu` or `play` sustained for 30 further frames (`session_wait`, `stable_frames:30`).
Boot input is refused. Cutscenes can report `loading`: a menu/play wait can time out while pictures still advance.
Use `job_read` receipts; CLI `--timeout` bounds transport, not work. `serve` runs the broker in the foreground;
`broker stop --timeout 90` ends all its owned work, so reconcile it first. Stdio remains a server, not a one-shot CLI.
The catalogue marks each broker requirement. Integrated groups still need configuration; current MCP name support
is separate from the dual-name harness and must not be assumed for a Longshot copy or log.

| Step | Shell route | MCP tool (group; integration) | What it saves |
|---|---|---|---|
| Locate rules/reports | Read the brief and registered report sections | `knowledge_search(query, collection="reports")`, then `workbench://reports/{id}` (knowledge; integrated) | Finds registered documents; revision-pinned bounded reads. The second entry is a resource, not a tool. |
| Plan/build | Selected `nx2/build.py --dry-run`; gated build above | `build_plan(repo_id, commit, profile_id)`, `build_start(plan_id, idempotency_key)` (checks; integrated) | Pins the approved plan, owns the gated child and its receipt. |
| Start/stop ordinary run | `hs-run.sh` above; owned runner shutdown | `session_launch(build_id, profile_id, wall_seconds, idempotency_key)`, `session_stop(run_id, reason, idempotency_key)` (session; integrated) | Registered disposable windowless lifecycle; never a clean-save promise. |
| Read queue/receipt | Gate files plus process/lock checks; saved child receipt | `queue_status(device="gpu")`, `job_read(job_id)` (status; integrated) | Separates observed queue and hold state; no guessed wait time. |
| Read log/faults | Run log and bounded kernel interval | `run_logs(run_id, last_lines=40)`, `run_faults(run_id)` (evidence; integrated) | Bounded cursors and signature classification; unknown coverage stays unknown. |
| Capture/panel | Control client `shot`; `panels.sh` above | `session_shot(run_id)` (session; integrated); `evidence_panels(images, label, idempotency_key)` (evidence; integrated) | New paths and artifact metadata; inspection still required. |
| Source/Windows checks | `hs-idle.sh ... tests/run_all.py`; fixture helper above | `checks_source(repo_id, commit, idempotency_key)`, `checks_windows_syntax(repo_id, commit, base, idempotency_key)` (checks; integrated) | Declared prerequisites, owned jobs and explicit Windows runtime not-run. |
| Pre-push/CI read | `hs-pr-check.sh`; `hs-pr-ci.sh` | `checks_pr(repo_id, commit, branch, title_artifact, idempotency_key)`, `ci_read(repository, pull_request)` (checks; integrated) | Preserves imports/failures without pushing, posting or rerunning CI. |
