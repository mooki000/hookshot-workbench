---
name: hookshot-scripted-play
description: >
  Drive Hookshot (the Ocarina of Time Switch 2 recompilation) by script on this machine: control-bridge input and
  state samples, the explicit file driver for older tap-enabled builds, getting from the title into play,
  the step drivers hsap.py and hsap_file.py, the local world model and route planner, the segment
  pilot and how a segment keeps a save, and the traps. Use for controller-free play, test saves at story points, area
  tours, or any run that must know where Link is. Read hookshot-workbench first (building, hs-run.sh, gates).
---

# Hookshot scripted play

**Use for:** controller-free play, bridge capabilities, fresh state samples and save-backed routes.
Three frequent commands (`P` is the pinned workbench Python; run client commands in the matching worktree,
with `HOS_CONTROL` set to your already-gated owned game):

```sh
"$P" -I -B scripts/hookshot_ctl.py --json status
"$P" -I -B scripts/hookshot_ctl.py option get
"$P" -I -B /home/mooki/Games/.hookshot-harness/playthrough/hsap.py <copy> <tag> 470 --live <private-dir> --boot continue
```

Never use the upstream client's `launch` example to bypass `hs-run.sh`/the gate. No windowed run is
authorized on 2026-11-12. Reports name skills read and tools used.

Read `skill://hookshot-workbench` first: building a copy, `hs-run.sh`, the GPU and build gates, pictures, saves.

**This file is public (`mooki000/hookshot-workbench`) and holds the method only.** What was read from or learned about
the game's content (places and coordinates, flag and step names, object names, routes, segment goals, counts from the
world model) is game data: it goes into `/home/mooki/Games/.hookshot-harness/playthrough/NOTES.md`, which stays on this
PC, and never into this file, git, an issue or a pull request. The world model, the saves and the segment files stay
under `/home/mooki/Games/.hookshot-harness/` for the same reason.

Written 2026-11-10; transport and driver selection updated 2026-11-12. The report
`/home/mooki/Code/hookshot-wt/reports/PlayHarness.md` holds the evidence. **Marks:** [run] = exercised in the game
(the report names the run); [offline] = checked without the game; [read] = read in the code only.

## Input: what main has

- The control bridge (`HOS_CONTROL`, merged in #926) accepts frame-timed `press`, `stick` and `sequence` requests;
  `stick` takes x/y from -1 to 1, including partial pushes and exact angles. `sequence` with `units:"hos"` takes
  seconds and milliseconds like the input script. `hsap.py` uses these commands with exclusive input and handles
  completion replies without blocking receipt of state samples. Bridge inputs alone do **not** imply that a build
  has the `oot` state subscription. [read; driver checked offline]
- `HOS_INPUT_SCRIPT='<s>:<buttons>[/<ms>],...'` (hs-run.sh `--input`): times from the first controller read. Buttons `A B
  X Y L R ZL ZR PLUS MINUS LS RS LEFT UP RIGHT DOWN`; sticks only at their edges: `LUP LDOWN LLEFT LRIGHT RUP RDOWN RLEFT
  RRIGHT`, combinable (`LUP+LRIGHT`); hold 200 ms unless `/<ms>`. Unknown names are ignored silently. [read:
  `host/nn_hid.c` `parse_presses`]
- `HOS_INPUT_LIVE=<file>`: lines appended while the game runs, read every 30 ms, each line's times from when it is read;
  a line still being written (no newline) waits. `!cheat <n>` queues a Debug-tools action taken at Link's next update
  (2/3 mark and return, 100+h time, 200+ pouch, 999 age, 1000+i teleport, 3000+ inventory; `host/hos_main.c`
  `oot_update_player_cheats`), `!shots <ms>`, `!dump`, `!synclog`. Keep the file outside `runs/<tag>` (hs-run.sh
  empties that folder). [read]
- `HOS_INPUT_FRAME_CLOCK=1`: file-script times in game frames at 60 a second. `HOS_INPUT_EXCLUSIVE=1`: the file script
  alone drives. `hsap_file.py` sets both; `hsap.py` instead supplies frame counts and exclusivity in socket requests.
- The eight-direction restriction applies to the file-input parser unless the build adds analog tokens.
  `hsap_file.py`'s `push`/`--analog` therefore need a file-input build with `LX=`/`LY=` support; `hsap.py` uses the
  bridge's existing analog inputs and does not need that unpublished parser change.
- Extra debug actions require their matching build extension; neither kind of state tap promises that an autosave
  or other debug command exists. Keep such commands separate from ordinary socket inputs.

## State: the control bridge first

`hsap.py` requires Hookshot #2292's **control-bridge sampled-state revision**, still open at main 51da0464.
Main's #926 bridge has inputs and basic `status.game` event/loading/age (null before it sees the player),
but no #2292 `oot` subscription, sampled position/camera or `game.frame`. Merely setting `HOS_CONTROL` is not
enough. Select from actual binary capability, not folder/head shorthand. Keep the local binary inventory private.

Set `HOS_CONTROL` to a private socket path (a named pipe on Windows), then use `status` for the latest
`status.game` snapshot or `subscribe` for fresh `oot` events:

```json
{"id":1,"cmd":"subscribe","events":["oot"],"every":6,"flags":[],"changes":true}
```

- `every` is the minimum spacing in game presents; the protocol default is 60, and `hsap.py` requests 6.
- `flags` is an array of up to 64 chosen names or explicit `0x` hashes. An unreadable/missing value is `null`;
  Enum values are `#` followed by the hash of the value's name.
- `changes:true` adds changed Bool/Int/Enum/UInt values by hash. The first sample establishes a baseline; at most 32
  changes are returned, with `more` counting the rest. Replacing or ending the subscription resets its baseline.
- `game` includes the sampled map, position/facing, camera yaw, event/loading/age, life/max life, rupees, prompt and
  music. **Use `game.frame` to date the sample**, not the reply/event envelope's current frame. `game.t` is the host
  monotonic clock in seconds. The top-level map is the bridge's last known map, not a substitute for `game.map`.
- Reads happen in Link's update, under its existing debug lock; the bridge receives copies, not guest pointers.
  No update means no new sample. `status.game` can be retained through a pause/load, so only fresh samples with
  known-false event/loading values establish control. Position is the actor's position, not necessarily its drawn
  pose during an event.

The client can show both forms:

```sh
python -I scripts/hookshot_ctl.py --json status
python -I scripts/hookshot_ctl.py --json watch --events oot --every 6 --changes
```

`hsap.py` writes received samples to `<live>/state.jsonl` as a **client journal**, normalizing age/booleans for the
existing pilot schema; that file is not a second game-output channel. `playthrough/tapcheck.py` can inspect this
journal beside captures, but a sample and a rendered capture need not be simultaneous. The bridge sampler has
game-free gcc/clang coverage and an in-game file/socket value comparison; the new driver's steering and error paths
are checked against a simulated bridge, not a further game playthrough (see PlayHarness5 in the report).

Older tap-enabled builds use `HOS_OOT_STATE_FILE`, `HOS_OOT_STATE_EVERY`, `HOS_OOT_STATE_FLAGS` and
`HOS_OOT_STATE_CHANGES`; they need **`hsap_file.py`**, which preserves the former file-state/live-input driver.
A build with neither tap works with neither driver, and there is no silent fallback: an `oot` subscription refusal
from `hsap.py` names `hsap_file.py` or a build with Hookshot #2292's control-bridge commits.

### Main bridge commands versus optional extensions

Main's `scripts/hookshot_ctl.py` supplies `status`, `press`, analog `stick`, `sequence` (`--hos` for script
seconds/milliseconds), `release`, `shot`, `wait`, `option get/set`, `watch`, `slots`, `teleport` and `quit`.
Memory reads/writes need a separate `HOS_CONTROL_MEM=ro/rw` opt-in; do not enable memory writes without the
task's authorization. Input replies mean frames completed, not that play resumed. `state=play` is a draw-count
heuristic; map plus known-false event/loading are stronger, and retained snapshots still need freshness.
`option_set` goes through the real menu setter and persists; restore only your owned copy's intended settings.
The bridge's `shot` does not include the Insert menu overlay. Debug actions and proposed commands are not
implied by the bridge: #2694's `age adult` was still open at main 51da0464.


## From the title into play

- Choose a tap-compatible build and driver before starting a run. Both drivers support
  `--boot newgame|continue|none`; they advance the title through timed input and wait for fresh quiet state samples.
  A retained status snapshot alone is not evidence that boot or a load finished.
- Supply only a copy of a save when needed; the run wrapper makes its own throwaway user folder. Keep particular
  save choices, routes and successful button recipes in local notes rather than this public skill.
- The boot limit counts from the game's start (its log appearing), not from when the driver joined the GPU queue.

## The step drivers (public)

`playthrough/hsap.py <copy> <tag> <wall s> --live <dir> [--save <user folder>] [--boot newgame|continue|none]`
runs a bridge-sampler build through `hs-run.sh` (headless, gate). `hsap_file.py` takes the same form for older
file-tap builds. Both play `<dir>/steps.txt` and answer in `<dir>/out.jsonl` (`gameplay`, `step`, `state`, `bad`,
`done`); pick one explicitly before launch.

`walk`/`run` steer from fresh position/camera samples and report reached, stuck, timeout or held; the handedness
check remains. `--analog`/`HSAP_ANALOG=1` uses exact angles, with the file-parser requirement noted above only for
`hsap_file.py`. Ordinary `press`, `stick`, `push` and `raw` steps use the socket in `hsap.py`; raw sequences keep
their seconds/milliseconds through `units:"hos"`. Discrete steps wait for frame-completion replies while state
reception continues; steering can have several replies outstanding without sending twice for the same sample.

Other steps retain their existing contract: `cheat`, `until`, `wait`, `climb`, `save`, `report`, `shot`, `end`.
Legacy debug cheats/shots use the live file; a save step still requires its build's debug extension and must observe
a newly written save, not merely an existing file.

Game-free checks: `python3 -I -B test_hsap.py` for the socket driver and
`python3 -I -B test_hsap_file.py` for the preserved file driver. `HSAP_TEST_MODULE=<file>` tests a staged copy.
Never replace a driver used by a live run: write beside it, check processes, then rename.

## The world model, the planner and the pilot (local: they carry game data)

These stay on this PC until their code is separated from what they read out of the game; the commands, the model's
contents and every game fact they showed are in `playthrough/NOTES.md`.

- **World model and planner** (`playthrough/world/`): built from your own dump (areas, nav meshes, exits, objects,
  event flows, the story's steps). `plan(area, from, to)` gives a walkable route as `run x z` steps, or why there is
  none (`needs-door`, `needs-climb`, `needs-crawl`, `needs-jump`, `off-mesh`, `not-connected`).
- **`playthrough/probe.py <script> --tag <tag> [--save <lib name>] [--copy <copy>]`**: a scripted segment without
  decisions: hsap steps one at a time, plus `plan x z [y Y] [radius R]` (the planner from where Link is; a door on the
  way: into it, A at `Open`, plan on through doors), `unless <flag>=<v> <step>`, `stop-if life<=N`; the rule **an
  event holds Link -> A every 1.5 s until control** after any step. Output `playthrough/runs/<tag>/probe.log`.
- **`playthrough/pilot/pilot.py run segments/<seg>.toml --tag <tag> [--save <user folder>]`**: the decision loop (a
  rules decider by default; a model decider only after a segment works with rules, with spending caps and a ledger;
  the key is read from the user's file and never printed). Its run goes to `pilot/runs/<tag>/` (`transcript.jsonl`,
  `summary.json`, `live/`), the game's to `<copy>/runs/<tag>/`. `python3 -I pilot/test_pilot.py` [offline: all
  passed]. A segment file (TOML) has `save` (`kept:<tag>`, a library name, or a path), `boot`, `save_after`, `watch`
  and its goals.
- **Keeping a save:** `save` (or `save_after = true`) asks the game for its own autosave; hs-run.sh keeps
  `<copy>/runs/<tag>/user/save`; the newest slot is what Continue loads. Put it into the local save library with
  `tools/hs_make_save.py --lib-add <copy>/runs/<tag>/user --name <area-what> ...`, then `--lib-proven <name> "<what
  the picture shows>" --presses '...'` after a run continued from it [run, twice].
- The current local `tools/hs_make_save.py` imports Python 3.14's `compression.zstd`. Use the installed system
  Python 3.14 for that save-data utility only; pinned 3.12 fails before writing a save. Builds/tests and the
  public play drivers still use the pinned Python and their wrappers. No package install or shared-tool edit is needed.

## Traps met

- A `set -e` in the shared persistent shell stays on and cuts later commands short ("exit 1, no output"): use `set +e`.
- The world builder's packed-index reader returned `KeyError` for a missing file and `navmesh.py` relies on it; a plain
  folder reader must too (`world/game_files.py`).
- The fast build path needs `RX_RELEASE=1` and `RX_PGO` at `<worktree>/work/build/hookshot_pieces.prof`; check the
  `object keys:` line with `--dry-run` first.
- The GPU gate queue on 2026-11-10 evening held 8 to 10 runs, 20 on 2026-11-11 morning: a segment may wait hours before
  the game starts; the pilot waits up to 6 h for `gameplay`, so start it from a script with `setsid nohup`.
- **One hold keeps the card for at most 8 minutes of game time** (Main, 2026-11-11): give a probe or segment `--wall 470`
  or less and cut longer plans into pieces chained from one script, each its own hs-run (its own gate turn), each
  continuing from the user folder the last one left (`<copy>/runs/<tag>/user`; example `PlayHarness-files/run-chain3.sh`).
  No `--exclusive` without Main's word for that hold.
- The full game-free checks go through the test gate: `/home/mooki/Games/.hookshot-harness/hs-idle.sh <python> -I -B
  tests/run_all.py` (idle class, core dumps off); a single test that may die by a signal through `hs-nocore.sh`.
- Use `setsid --wait nohup` for a managed saved launcher, and the child's own completion receipt. Keep game PIDs:
  `timeout` can move children into a different process group; stopping only the shell can strand games outside the gate.
- Scope includes the whole acceptance checker, not only process exit: some old co-op scripts returned 0 with
  failed assertions. A stuck/unreached action invalidates later claims. Record sent, accepted, state change and
  inspected picture separately; do not weaken an assertion to rescue an earlier run.
- `test_recorder_sync.py` and `test_display_pointer.py` are timing-dependent under load (one failed on origin/main
  alone at load 98, the other in a loaded full run): compare at a quiet hour before blaming a branch.
- A probe held by an event needs an explicit rule for resuming; a successful input reply does not prove control
  returned. Keep watching fresh state samples.
- **A straight `walk` is only for open ground.** If it ends stuck, later steps may run somewhere other than their
  intended target and prove nothing. Use the local planner where appropriate, and inspect each step result and
  capture before relying on what followed it.
- **`save` can be refused.** Check the debug reply and a newly written save; finding any old slot is not proof that
  the request succeeded. Keep build-specific timing observations in local notes.
- A probe that ends with its script ends the game by SIGTERM: `exit=143` in the summary line is the normal end.
