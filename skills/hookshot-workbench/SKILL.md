---
name: hookshot-workbench
description: >
  Start here for any work on Hookshot (NotRexed/Hookshot, the Ocarina of Time Switch 2 recompilation) on this machine:
  where the clone, worktrees, dump, dependencies, built game copies, harness and reports are; the house rules in
  short; how to build a branch into a game copy (full and incremental), run it unattended and headless through the GPU
  gate with hs-run.sh, take pictures, script input, read the log and the frame file, run the game-free checks, and
  tell a run was disturbed. Use when asked to build, run, test, measure or debug Hookshot.
---

# Hookshot workbench

**Use for:** every Hookshot task on this machine; building, running, checking and collecting evidence.
Read the agent brief first, then the matching skill from its task table. Three frequent commands:

```sh
/home/mooki/Games/.hookshot-harness/tools/deps-local.sh <worktree>
RX_CPM=/home/mooki/Code/hookshot-wt/deps/cpm /home/mooki/Games/.hookshot-harness/hs-idle.sh /home/mooki/Code/hookshot-wt/deps/python/bin/python3 -I -B tests/run_all.py
/home/mooki/Games/.hookshot-harness/hs-run.sh <owned-copy> <new-tag> 90
```

The last command is an ordinary run, **no speed claim**. FPS/frame-time work uses the regression tool's authorized
exclusive held worker (read `hookshot-perf-compare`), not a new measurement script.
**2026-11-12: all game runs are windowless; yesterday's window allowance is withdrawn**, regardless of a leftover
`window-ok`. Every report names the skills read and the tools used.

Hookshot is a static recompilation of the Switch 2 *Ocarina of Time* for PC (Vulkan): the game's code lifted to C,
Horizon and NVN2 implemented natively, the GPU work replayed on Vulkan. Upstream `NotRexed/Hookshot` (GPL-3.0-or-later),
the user's fork `mooki000/Hookshot`. This machine: Arch, Hyprland, i9-13900K (32 threads), RTX 4090 (NVIDIA 615.78.08)
and the Intel UHD 770 beside it, a 1920x1080 monitor at 60 Hz.

**Rules first:** `/home/mooki/Code/hookshot-wt/reports/AGENT-BRIEF.md` (the project's rules and this machine's) and, in
the clone, `docs/workflow.md`, `AGENTS.md`, `CONTRIBUTING.md`, `docs/development.md`, `docs/design.md`. In short:

- No game files or anything made from them leave this PC or enter git, an issue or a pull request (dump, `work/`, built
  games, saves, options). Pictures, videos and `hookshot_log.txt` are fine.
- GPL only; never a line from the other port's repository (its local checkout and skills are reference
  only); the user's own earlier files only when `reports/OURS.md` lists them as wholly his.
- Never touch `/home/mooki/Games/Ocarina`, `Ocarina-linux-next`, `/home/mooki/Games/Hookshot` (the user's play folder),
  `~/.config`, `~/Downloads`, the global git config or system packages; never send anything to the compositor.
- Every program that creates a Vulkan device goes through `/home/mooki/Games/hs-gpu-slot`; every build and heavy CPU
  job through `/home/mooki/Games/hs-build-slot`. Begin such commands with
  `unset HSGATE_SLOT_DIR HSGATE_INTEL_SLOT_DIR HSGATE_SLOT_TEST HSGATE_SLOT_NODESKTOP HSGATE_IN_GATE`.
- Subagents never push, post or comment. Commits carry the author and trailers the brief names.
- Timestamps from `date`; every measured number with its build, settings and load average.

## What is where

| Thing | Path |
|---|---|
| Clone (remotes `origin` = NotRexed, `fork` = mooki000) | `/home/mooki/Code/Hookshot` |
| Worktrees | `/home/mooki/Code/hookshot-wt/<name>` (`bench` = the main build's, detached) |
| Reports, brief, mistakes log | `/home/mooki/Code/hookshot-wt/reports/` (`AGENT-BRIEF.md`, `LOG.md`, `HsWorkbench.md`) |
| The dump (exefs/ + romfs/, game data) | `/home/mooki/Code/hookshot-wt/work/dump` |
| Python 3.12 with the pinned packages; CPM headers | `/home/mooki/Code/hookshot-wt/deps/python/bin/python3`, `/home/mooki/Code/hookshot-wt/deps/cpm` |
| Harness: hs-run.sh, tools, saves, run log | `/home/mooki/Games/.hookshot-harness/` |
| Built game copies | `/home/mooki/Games/.hookshot-h-<name>` (`.hookshot-h-main` = main) |
| Gates and the GPU guard | `/home/mooki/Games/hs-gpu-slot`, `hs-build-slot`, `hs-intel-slot`; `/home/mooki/Games/.hookshot-harness/gpu-guard.sh` |

The machine has clang/lld 23, FFmpeg 9 with dav1d, sdl2-compat over SDL3, Vulkan headers and glslang/spirv-val; it has
no ninja, cmake or meson. `scripts/linux/deps.sh` is for Ubuntu (the AppImage) and does not run here; `deps-local.sh`
below supplies the three things a source build lacks.

## The dump

The game's own files (`romfs/` and `exefs/`, from your own copy of the game) lie in the dump folder of the table above
and never leave this PC. Where this machine's came from and how they were made ready is in the local notes
(`/home/mooki/Games/.hookshot-harness/LOCAL-NOTES.md`, not published). Check a dump:

    cd /home/mooki/Code/hookshot-wt/bench
    /home/mooki/Code/hookshot-wt/deps/python/bin/python3 -I nx2/dump.py --dump /home/mooki/Code/hookshot-wt/work/dump

It names the program, the main build id and the console. Builds hard-link its romfs into each game copy.

## A worktree and its dependencies

    git -C /home/mooki/Code/Hookshot worktree add --detach /home/mooki/Code/hookshot-wt/<name> <commit or branch>
    /home/mooki/Games/.hookshot-harness/tools/deps-local.sh /home/mooki/Code/hookshot-wt/<name>

`deps-local.sh` fills `/home/mooki/Code/hookshot-wt/deps` once (python-build-standalone 3.12 + `requirements.txt`, and
Vulkan-Headers, SPIRV-Headers, VMA, all with deps.sh's pins and checksums); later calls only check. The play profile
(hot list, link order, PGO profile: not in git, from the latest release, as CI fetches it), once per worktree:

    cd /home/mooki/Code/hookshot-wt/<name>
    P=/home/mooki/Code/hookshot-wt/deps/python/bin/python3
    TMPDIR=/home/mooki/Code/hookshot-wt/deps/release GH_TOKEN=$(gh auth token) $P -I scripts/release_parts.py previous /home/mooki/Code/hookshot-wt/deps/release/setup.txt --repo NotRexed/Hookshot
    TMPDIR=/home/mooki/Code/hookshot-wt/deps/release GH_TOKEN=$(gh auth token) $P -I scripts/release_parts.py profile /home/mooki/Code/hookshot-wt/deps/release/setup.txt work

## Build a branch into a game copy

From the worktree, through the build gate, without a queue deadline (a saved script started with
`setsid --wait nohup`; the main build's is `reports/HsWorkbench-files/build-main.sh`). The `--wait` matters:
without it a successful launcher exit can leave the real child still queued. Require its completion receipt.
**One full build at a time on this machine** (the lock in front): the lift's workers took 4.8 GB each on 2026-11-10,
so twelve workers used about 58 GB beside games of 14 GB. Keep the local cap at eight workers.

    cd /home/mooki/Code/hookshot-wt/<name>
    flock /tmp/hookshot-full-build.lock \
    /home/mooki/Games/hs-build-slot env RX_CPM=/home/mooki/Code/hookshot-wt/deps/cpm HOOKSHOT_LIFT_JOBS=8 HOOKSHOT_KEEP_BUILD_FILES=1 \
        /home/mooki/Code/hookshot-wt/deps/python/bin/python3 -I -u nx2/make_game.py \
        --romfs /home/mooki/Code/hookshot-wt/work/dump/romfs --exefs /home/mooki/Code/hookshot-wt/work/dump/exefs \
        --out /home/mooki/Games/.hookshot-h-<name> --work /home/mooki/Code/hookshot-wt/<name>/work/build \
        --jobs 16 --no-prepare-shaders --log <your files folder>/build.log

- Passed when it ends with `@@done <folder>` and exit 0. `--no-prepare-shaders` always: the build's last step would
  start the game on the card outside the GPU gate; prepare through hs-run.sh instead (below).
- Always pass a work folder of your own (normally `<worktree>/work/build`); never use the user's default cache.
  The old `SameFileError` when `--work` was the checkout's `work/` was fixed by #2285; it is not a current-main limit.
- `HOOKSHOT_KEEP_BUILD_FILES=1` keeps lifted C and objects. On current main `lift_sources()` follows imported
  translator modules and vendored a64-kit sources; build/packaging/prompt tools are not blanket lift inputs.
  `lift_key()` also includes the HLE names, source inputs, hot/chunk/piece state, environment and Python.
  Preserve the same work/output/profile identity; do not assume a copied work directory is a compatible donor.
  #2674 refreshes self-produced planning inputs after the lift; #2686's separate race guard was still open at
  main 51da0464. Never edit build inputs while a build is running.
- A full build of main bdfe09c, 16 jobs, load 2 to 18: **9 min 53 s** (lift ~3 min, compile 7 min 31 s, 473 files);
  the game folder 1.5 GB besides the hard-linked romfs, `work/build` 2.8 GB.
- **Current incremental finish:** #2698 keeps unchanged button-prompt assets in source builds. Its success line is
  `prompts: unchanged, kept`; `HOOKSHOT_FORCE_PROMPTS=1` forces generation. Packaged launcher builds still regenerate.
  The old 67-second finish and five-pad-face example are historical, not the current per-edit cost (Keyboard is
  another face). Do not classify “not made again” as failure: it is the successful unchanged shader-list message.
  Require exit 0 and `@@done`, not a broad search for the word “not”.
- Main #2702 keeps source/object hashes in `<build>/hash_cache.json` across builds when size, nanosecond time and
  file identity are unchanged, excluding recent writes. #2324/#2518 changed lift caching and development
  optimization. Read the selected commit's guide; a historical object-key hash is not universal acceptance.
- **The build cannot show a warning**: `nx2/build.py` compiles everything with `-Wno-everything` (and `-w`). An empty
  `grep warning` of the build log says nothing; compile the files you changed yourself with `-Wall -Wextra` to see any.
- The simple recipe above omits DLSS. An explicitly recorded build may reuse an already-accepted SDK; never accept
  its licence again on the user's behalf. #2598's frame-generation runtime is separate from the DLSS runtime:
  an old SDK/build with DLSS alone does not prove DLSS-G is present.

### The fast path for a change in `host/` or `gpu/` only

The build gate has four slots and a full build takes one for ten minutes; on 2026-11-10 eight agents queued there.
When a branch changes only `host/` or `gpu/` files, skip the lift and the full compile: copy the main build's files
copy-on-write and let `nx2/build.py` compile what changed and link (agent BuildComma, 2026-11-10: compiled in seconds,
linked `hookshot` 458 MB and `hookshot_vk.so` 10 MB):

    cd /home/mooki/Code/hookshot-wt/<name>
    git diff --stat bdfe09c HEAD -- nx2 third_party/a64-kit      # must show nothing the lift reads (see below)
    mkdir -p work/build      # first: a new worktree has no work/ folder, and the copy below fails without it
    [ -d work/fast ] || cp -a --reflink=always /home/mooki/Code/hookshot-wt/bench/work/build work/fast || exit 1
    [ -f work/build/hookshot_pieces.prof ] || cp --reflink=always work/fast/hookshot_pieces.prof work/build/ || exit 1
    /home/mooki/Games/hs-build-slot env RX_CPM=/home/mooki/Code/hookshot-wt/deps/cpm OOT_WORK=$PWD/work/fast \
        RX_RELEASE=1 RX_PGO=$PWD/work/build/hookshot_pieces.prof \
        /home/mooki/Code/hookshot-wt/deps/python/bin/python3 -I -u nx2/build.py --standalone --jobs 16
    cp -a --reflink=always /home/mooki/Games/.hookshot-h-main /home/mooki/Games/.hookshot-h-<name>       # once
    cp work/fast/sa/build/hookshot work/fast/sa/build/hookshot_vk.so /home/mooki/Games/.hookshot-h-<name>/

- **`RX_RELEASE=1` and `RX_PGO` at exactly `<worktree>/work/build/hookshot_pieces.prof` are required** (BuildComma's
  run had them: `reports/BuildComma-files/link-comma.sh`). Without `RX_RELEASE=1` every Vulkan back-end object is
  compiled again without `-DHOOKSHOT_RELEASE` and the game behaves differently at shader preparation. The hot objects'
  key holds the profile's path relative to the worktree root, so any other path compiles the 8 `code_hot` files again
  at -O2 (minutes each), and without the profile the game is slower and its frame rates do not compare.
- **Historical donor example:** bdfe09c's `object keys:` line had flags `f08f29989d`, hot flags `fc9905f579`
  (`reports/HsWorkbench-files/build-main.log:458`). For a current fast build require its actual donor's profile,
  flags and compatible host/renderer API; main 51da0464 is API19, the text-field/co-op branches use API20.
  `--dry-run` inspects the plan without compiling.

- The copy of `.hookshot-h-main` brings its prepared shaders; a renderer change that alters the program cache's stamp
  prepares again at the first run.
- **Valid only while the lifted C is still right.** The old bench donor is bdfe09c. Compare the selected build's
  actual `lift_sources()`/`lift_key()` inputs and HLE-name set, not every `nx2/` filename. The regression builder
  conservatively groups equal lifted inputs and records donor completion/source stamps; a false mismatch costs a
  lift, a false match invalidates the game. No incompatible donor just to save time.
- Say in the report which path each tested binary came from. `.hookshot-h-main` holds the `runs/` of many agents:
  delete the copy's `runs/` after copying.
- `tests/run_all.py` takes no build slot; it goes through the test gate (three slots, idle class, steps aside for an
  exclusive timing run): `/home/mooki/Games/.hookshot-harness/hs-idle.sh <python> -I -B tests/run_all.py`.
  The gate also switches core dumps off for what it runs (`hs-nocore.sh`: a core limit of one byte, which the kernel
  takes as "do not hand this crash to systemd-coredump"): the checks crash three fixture programs on purpose per run,
  and each core dump raised a "process crashed" notification on the user's desktop (107 in one night). A single test
  or program of yours that may die by a signal: `/home/mooki/Games/.hookshot-harness/hs-nocore.sh <command>`. Check
  afterwards: `coredumpctl list --since '-30min' --no-pager` shows nothing of yours.

## Run it: hs-run.sh

    /home/mooki/Games/.hookshot-harness/hs-run.sh <copy> <tag> <seconds> [--exclusive] [--whole] [--priority] [--from <s>] \
        [--input '<presses>'] [--user <save folder>] [--keep-user] [--prepare] [NAME=VALUE ...]

It goes through the GPU gate itself (do not wrap it), runs the game headless (`HOS_HEADLESS=1`, no window and no
Wayland connection), muted, on NVIDIA's driver only with `RX_VK_DEVICE=0` (the RTX 4090; the summary flags any other
device), without update check or Discord status, on a throw-away copy of the save folder (`--user`, default the copy's
`user-template/` or a fresh profile), with the driver's caches in the copy and `XDG_DATA_HOME` in the run's folder
(nothing goes to `~/.local/share` or `~/.cache`). It ends the game at `<seconds>` (exit 124) and prints one line, also
in `<copy>/runs/<tag>/summary.txt` and `/home/mooki/Games/.hookshot-harness/runs.log`. Everything of a run is in
`<copy>/runs/<tag>/`: `hookshot_log.txt` (stdout and stderr), `frames.csv`, `shots/`, `env-used.txt`, `user/save`.
`--window` gives a real window instead, only while the switch file exists: "With a window" below.

- **First run of a new copy:** `hs-run.sh <copy> prepare1 480 --prepare` (the shader preparation a first start
  would do, before the title). One hold keeps the card at most 8 minutes: a copy made from `.hookshot-h-main` with
  an unchanged `gpu/` needs about 40 s; a copy whose shader stamp moved prepares everything again, which may not
  fit (not measured how long on the RTX 4090, nor whether a second run goes on where the first stopped: find out
  and write it here). When only log lines and pictures matter, run the scene with `RX_VK_PRECOMPILE=0` instead
  (the Intel sessions do).
- `--exclusive` only for frame-rate measurements (no build and no other game beside it), and only with the main
  session's word for that hold. `--from <s>` limits the frame statistics to frames from `<s>` seconds after the first
  present.
- `--whole` takes both of the gate's run slots for this run without stopping the builds: no other game is on the card
  beside it. `hs-run.sh` adds it by itself when the run's picture is larger than the game's own 2048x1152
  (`HOS_OUTPUT_RES` as a NAME=VALUE or in your environment; without it `output_res=` in the copy's
  `hookshot_options.ini`) and for `--window`; ask for it yourself when a run needs the card's memory for another
  reason. A script that calls `hs-gpu-slot` itself (one hold for several runs) adds `--whole` to that call when
  one of its runs has such a picture.
- `--priority`: only for a single short run the main session has named, never for a series.

- `--intel` (the UHD 770 through `hs-intel-slot`, Mesa's ICD only) and `--mem-max 28G` (the game in a
  `systemd-run --user --scope` with that MemoryMax and no swap; Intel's driver took 23-28 GB preparing shaders) were
  added for agent IntelTier on 2026-11-10; see its report for runs with them.
- **Two games can run at once** (since 2026-11-11 08:05, while the user is away). While
  `/home/mooki/Games/.hookshot-harness/gpu-slots` holds `2` (the user's switch; the main session's to change) the
  gate lets a second run in beside one: when the first has run a minute, 30 GB of memory are available, memory
  pressure is calm and the card has 12 GB free. The old 7.6–7.9 GB VRAM/about 14 GB RSS figures describe bdfe09c,
  not today's allocation guarantee; use live free-memory admission. A run is not alone without `--whole` or `--exclusive`, and its
  frame rates mean nothing outside an `--exclusive` hold. Two runs of ONE copy are no longer kept apart: the second
  one runs without the program cache (`vk: program cache …: another Hookshot is using it: this one runs without it`
  in its log; slower start, more stutter). For anything that depends on the cache use a copy of your own
  (`cp -a --reflink=always`). Each run still has its own `runs/<tag>/`. Never run two waiters of your own in the gate
  at once: put a series into one script (examples: `reports/HsWorkbench-files/run-*.sh`), one hold of at most 8
  minutes.
- **The queue.** After each run that came in by `--priority` the oldest ordinary waiter is let in, then priority
  again: a priority waiter stands back by taking its ticket out of `queue/` into `yielded/` for that time. The state
  is in `/tmp/hs-gpu-slots/` (read only): `holder.0` and `holder.1` (the pid on each slot, the file's time is its
  start), `queue/` + `yielded/` (every waiter), `last-kind` (`p` or `o`: the kind let in last), `long-holds.log` (the
  guard's line for a hold of over 8 minutes, again every 5), `closed` (the guard closed the card);
  `/home/mooki/Games/.hookshot-harness/gpu-guard.sh --status` prints guard, card and slots.

### Pictures

`RX_VK_SHOTS` is set by hs-run.sh: a 1280x720 PNG every `RX_VK_SHOT_EVERY` presents (300 = 5 s at 60 fps; pass
`RX_VK_SHOT_EVERY=60` for one a second). Look at them as a contact sheet, the file list **before** the output name
(`xargs magick montage ... out.png` makes the last picture the output and destroys it: it happened once):

    cd <copy>/runs/<tag>/shots && magick montage $(ls *.png | awk 'NR%10==0' | head -8) -tile 4x2 -geometry 400x225+4+4 -pointsize 16 -set label '%f' /tmp/sheet.png

What a fresh profile shows (main bdfe09c): black, the moonlit field with the rider (intro movie), the title (logo,
"New Game", "Options / ZELDA NOTES Setting") from about present 1300 (22 s), over a movie of Hyrule Field and Lon Lon
Ranch. A new game starts the tapestry narration, then the dream at the castle gate (Impa, Zelda, Ganondorf), then the
Deku Tree's monologue; text advances by itself and with A.

### Scripted input

- `--input '<presses>'` = `HOS_INPUT_SCRIPT` (with `HOS_INPUT_EXCLUSIVE=1`): `<seconds>:<button>[+<button>][/<ms>]`,
  comma-separated, buttons `A B X Y L R ZL ZR PLUS MINUS LS RS LEFT UP RIGHT DOWN`, sticks `LUP LDOWN LLEFT LRIGHT
  RUP RDOWN RLEFT RRIGHT`; times count from the game's first controller read. `HOS_INPUT_FRAME_CLOCK=1` counts them in
  game frames at 60 a second instead (steadier under load).
- **Load a save into play** (a save folder of your own; this machine's are listed in the local notes):
  `--user <that folder> --input '5:A,8.7:A,11.7:A,15:A' HOS_INPUT_FRAME_CLOCK=1`: Link stands in play with the HUD
  shown from about 43 s after the first present.
- **New game from a fresh profile:** `--input` with A every 1.5 s from 4 s (`4:A,5.5:A,...`): it skips to the title,
  presses New Game and advances the intro's text (`reports/HsWorkbench-files/run-newgame5.sh`).
- **By hand while it runs:** `HOS_INPUT_LIVE=<file>` as a NAME=VALUE; append lines to the file (`echo '0:A' >> file`);
  each line's times count from when it is read (every 30 ms). Keep the file outside `runs/<tag>` (hs-run.sh empties
  that folder at the start): `/home/mooki/Games/.hookshot-harness/live/<tag>.txt`. Watch the newest pictures to decide.

### Saves

A Hookshot save folder (`--user <folder>`) is `<folder>/save/5a3c9e1f0b7d24680000000000000001/` (Hookshot's fixed user
id, `host/nn_applet.c` `user_uid`, `host/nn_fs.c`) holding `account.sav`, `option.sav` and `slot_00` to `slot_NN`, each
`progress.sav` + `caption.sav`. Saves are game data: they stay on this PC (the local notes say which this machine has
and how a save from elsewhere was brought into this layout). Never run on the original: hs-run.sh copies `--user` into
the run's folder.

### The log and the numbers

- `runs/<tag>/hookshot_log.txt`: first line `hookshot v2.1.0-rc.3-39-gbdfe09c (source ...)`; `vk: device 0: NVIDIA
  GeForce RTX 4090`; `vk: N presents, X fps` every 120 presents and `vi: N frames presented (X fps over the last 5.0 s)`;
  `vcores: <thread> ... ran without a core` (escape threshold is build/switch-dependent; counted as `nocore=`).
- Frame times: `RX_VK_FRAME_LOG=<csv>` writes a line per present (frame, seconds, frame time in ms). Builds from
  60a4b84c on (main since #2354) write it in a headless run too: there the times are the CPU intervals after the
  offscreen copy is submitted, without swapchain or display waits, so do not compare them with a windowed run's. Older
  builds leave it empty without a swapchain. The wrapper selects a populated renderer `frames.csv` when present;
  otherwise the game's `HOS_PRESENT_TRACE` (`present.csv`, three or seven columns) supplies intervals with the limiter.
  Its historical “swapchain” label for headless renderer CSV is wrong; the correction is staged while live jobs use it.
  At the 60 cap intervals scatter around 16.67 ms: use `over20`/`over25`, not pacing jitter near `over16.7`.
- #2340 disables failed dma-buf imports on NVIDIA's driver by default; its log says
  `guest memory as dma-bufs: no (NVIDIA's driver refuses them: host pointers)`. Older builds can emit the known
  `NVRM: memMap_IMPL: CPU mapping not supported for addressSpace: 0x1` refusal more than once.
  Count and classify kernel lines by the brief: any Xid, kmemsysDoCacheOp or unknown NVRM line stops; repeated known
  mapping refusals alone do not. The brief also names the bounded three-line allocation-refusal exception.
  A kernel “last print repeated N times” marker belongs to the preceding message, not a new unknown failure.

### Was the run disturbed?

- Any driver fault or unknown NVRM signature: stop and tell the main session. `nvrm=` is a count, not a fault verdict.
- `dev=` not `0:NVIDIA_GeForce_RTX_4090` (summary says `DEVICE?`), `HOME-WRITES:` in the summary, `fatal=`/`crash=`/
  `devlost=` not 0, exit not 124 for a run meant to reach the wall.
- The load: the summary prints the load average before and after. Builds and other agents' Intel runs (`--prepare` on
  Mesa uses every core) raise it to 40-85; then the game drops frames (`ng1` at load 85: mean 46.6 fps). Frame rates
  only from `--exclusive` runs, and only with the load beside them.

### With a window: `hs-run.sh ... --window` (since 2026-11-11; `reports/WindowRuns.md`)

**Historical mechanism only: not authorized on 2026-11-12.** No window is created for the current work.

The user allowed exactly this on 2026-11-11: two window rules for the test window's own class, sent to his running
Hyprland at run time. Nothing else may ever be sent there (no dispatch, keyword, reload, input, no file under
`~/.config`); reading (`hyprctl version`, `clients -j`, `activewindow -j`, `monitors -j`, `getoption`) is allowed.

- **When.** Headless stays the default. A window only for what needs a real swapchain and the compositor: the
  present mode and V-Sync, frame pacing as the display sees it, frame generation's output, the window's title
  (`RX_VK_FRAME_LOG` in a headless run has no swapchain or display waits in its times). HDR cannot be tested here:
  the monitor (HP E243, 1920x1080 at 60 Hz, scale 1.25) has no HDR.
- **The switch.** Only while `/home/mooki/Games/.hookshot-harness/window-ok` exists (the main session creates it
  while the user is away and removes it when he is back; agents never touch it). Without it `--window` exits 2 and
  starts nothing; a run whose turn in the gate comes after the file went starts nothing either (exit 5); a run in
  progress finishes. One windowed run at a time, at most two minutes each, never `--exclusive` without the main
  session's word; not with `--intel` or `--prepare`.
- **What it does** (`hs-run.sh` and `.hookshot-harness/hs-window.py`): inside the gate it finds the one live
  Hyprland (a safe-mode instance only when `.hookshot-harness/safe-mode-ok` holds its pid), refuses if the session is
  locked or no monitor is on, sends the two rules with `hyprctl eval` and starts the game only if both answer `ok`
  (`runs/<tag>/window-rules.txt`):

      hl.window_rule({ name = "hookshot-harness", match = { class = "^hookshot-harness$" }, no_initial_focus = true, no_focus = true, suppress_event = "fullscreen maximize activate activatefocus fullscreenoutput" })
      hl.window_rule({ name = "hookshot-harness-float", match = { class = "^hookshot-harness$" }, float = true, size = "1536 864", move = "0 0" })

  The game runs with `SDL_APP_ID=hookshot-harness` (SDL 3 names the window so: seen), Wayland only, muted,
  `HOS_WINDOW_SIZE=1536x864` (logical; 1920x1080 pixels). The window floats over the whole monitor on the visible
  workspace and can never be focused: keys, mouse and controllers do not reach it, `--input` and `HOS_INPUT_LIVE`
  do. It is on the visible workspace on purpose: Hyprland 0.56.2 sends a window on a hidden workspace no frame
  callbacks, so presentation cannot be judged there (the old harness's hidden special workspace was for pictures).
  **Never change the rules' text or send a rule of your own**: a rule sent again under its name is appended to,
  not replaced, until the compositor restarts.
- **The summary line gains** `swapchain=<size>,<present mode>,vsync-<row>,x<times made> game=<fps>` and
  `window=ok class=hookshot-harness size=1536x864 at=0,625 ws=<n> float=1 active=same others=same screen=on samples=N`.
  `frames=...(swapchain)` are then the window's presents from `frames.csv` (made frames included) and `game=` the
  game's own rate over the same seconds (`present.csv`). `window=PROBLEM(...)`: the wrapper ended the run because
  the window was focused, tiled, fullscreen, on another workspace or of another class: stop and tell the main
  session. `active=CHANGED`: the user is back and working: stop asking for windowed runs, tell the main session.
  `screen=LOCKED|BLANKED|HIDDEN(k/n)`: for k of n looks the window was not shown (session locked, monitor off, his
  workspace another one): throw the run's frame numbers away. Files: `runs/<tag>/window-before.json`,
  `window-after.json`, `window-samples.jsonl`.
- **What a first run showed** (main bdfe09c, 2026-11-11 07:36, Kokiri Forest from the user's save): `vk: swapchain
  1920x1080, 4 images, mailbox (V-Sync Auto)`; the window class `hookshot-harness`, title `Hookshot`; the user's
  terminal stayed the active window in all 113 samples; kernel 0. Its 29 fps were taken at load 190 to 230 and are no
  frame rate: a windowed frame rate needs a quiet machine like any other, with the load beside it.
- **Presses on the frame clock come late below 60 fps.** With `HOS_INPUT_FRAME_CLOCK=1` a press "at 50 s" is game
  frame 3000; at 25 fps under load that is two minutes of wall time. Time presses in game frames / 60 (play from the
  user's save starts near frame 1500) and check in the pictures that they came.
- **Pictures of made frames:** `RX_VK_SHOT_MADE=1` writes `vk_<n>_made.png` beside `vk_<n>.png` (the frame
  generation's frame before game frame n); `RX_VK_SHOT_W=1920 RX_VK_SHOT_H=1080` for the size. An options file for one
  run (`hookshot_options.ini` beside the binary: `output_res=3840x2160`, `resolution=3` = 67 %, `dlss=3` = FSR 3,
  `frame_generation=2`) belongs in a copy of your own (`cp -a --reflink=always` of `.hookshot-h-main` without its
  `runs/`), never in `.hookshot-h-main`, and is removed after the run.

## The game-free checks

Through the test gate `hs-idle.sh` (the main session's rule since 2026-11-10 22:49: six runs of the checks beside four
builds put the load at 170 during timing holds). It runs the command in the idle class in one of three slots, takes no
build slot, and does not start while an exclusive timing run on the card wants quiet. Once on your branch at the end;
never a second run while one of yours runs. A single test file run alone needs no gate.

    cd /home/mooki/Code/hookshot-wt/<name>
    RX_CPM=/home/mooki/Code/hookshot-wt/deps/cpm /home/mooki/Games/.hookshot-harness/hs-idle.sh /home/mooki/Code/hookshot-wt/deps/python/bin/python3 -I -B tests/run_all.py

Use the selected commit's `RUN_ALL_ARGS`, `RUN_ALL_MODULES` and `RUN_ALL_SKIP` declarations; do not assume `--cc`
means a native compiler. `--jobs 8` limits workers; `--strict` makes missing prerequisites fail; `--shard K/N`
is CI's partition, not a substitute for the full suite. #2666 escapes unencodable Windows-pipe output; the
`pass/FAIL/TIMEOUT <seconds>s <name>` rows and `<N> passed, <N> failed, <N> skipped` totals remain the parser contract.
The old bdfe09c baseline failures were repaired; compare named failures with the pinned current main job using
`hs-pr-ci.sh`, not historical totals or an unexamined red check.

New or changed generated/static C/C++ fixtures also go through `hs-win-fixture-check.sh <worktree> [base]` in the
idle gate. It keeps the test's compile flags, including `-Werror`, and names unsupported discovery. Syntax is not
a Windows execution: keep that execution in Not tested until CI or a Windows tester supplies it.

### Process and evidence traps

- End owned games before their gate; `timeout` can put a child in another process group. Keep actual game PIDs and
  receipts. Neither `pgrep -f` on a shell fragment nor `kill -0` on a zombie proves the owned chain ended.
- Preserve failed output and resume only the unfinished step; a detached launcher's early exit is not a failed run.
- The log's `source <hex>` is `nx2/version.py`'s content fingerprint, not a Git commit. Pin a tag/head separately.
- A headless menu script needs `RX_VK_SHOT_MENU=1` for menu ticks. `RX_VK_FRAME_LOG` is timing; `RX_VK_FRAMELOG`
  is a different pixel stream and remains swapchain-only. Stills cannot prove a continuous flash absent.
- Report a Bug report **row** saving a **folder**, not a tab or ZIP. Read the actual feature and inspect pictures.
- Current Boolean switches use the on/off rule: unset/empty/`0`/`false`/`off`/`no` are off (case/space normalized).
  #2575 applies it to `HOS_FPS_LOG`; #2651 to `RX_VK_CAPTURE_KEEP_OLD` and `RX_VK_CAP_BLOCKS`.
  `RX_VK_PRESENTS_AHEAD` (queue depth) and `RX_VK_PRESENT_AHEAD` (frame-generation submission) are not aliases.

## The play folder

`/home/mooki/Games/Hookshot` is the user's (`play.sh`, `README.txt`, saves in its `user/`): never run, change or
test in it; the guard leaves it alone. To give him a new build, ask the main session.

## Merged switch and log contracts (main 51da0464)

Read the selected commit's source before changing a run. These are not permissions to disable safety paths.
Source inventory: `reports/MergedSweep.md` section 2b, with exact upstream files/lines; `docs/development.md`
is the public reference. No bridge command or test-runner flag was renamed in that merge interval.

| Area | Contract to preserve |
|---|---|
| Window identity (#2116) | `HOS_WINDOW_TITLE`/`HOS_WM_CLASS` are new overrides, not renames. `HOS_APP_ID`/`HOS_GAME_TITLE` are compile-time constants. Headless does not use the window identity path. |
| Readback (#2602) | `RX_VK_READBACK_PART=0` selects whole-submission waits; `RX_VK_READBACK_SETTLE_ALWAYS=1` restores unconditional stopped-queue checking. New part/last/in-place/settled counters change the population, not the old prefix. |
| Capture/staging (#2314/#2437/#2651) | `RX_VK_SHADOW_STAGE=0` disables default cached staging; `RX_VK_CAP_PREWARM=0` disables prewarm. `RX_VK_CAPTURE_KEEP_OLD=1` selects former classes/64 MiB reuse, `RX_VK_CAP_BLOCKS=1` shared blocks. A 256 MiB per-buffer ceiling is not a total-memory cap. |
| Pools/workers (#2438/#2440/#2464) | `RX_VK_IMAGE_PREALLOC=0` disables startup pool preallocation; `RX_VK_WORKERS=1..16` overrides workers. `RX_VK_UB_SNAPSHOT` defaults on for Intel unmirrored pools, not every no-mirror GPU. Keep startup versus steady-state memory distinct. |
| Textures/mirrors (#2154/#2226/#2545) | `RX_VK_MIRROR_KEEP=1` still keeps every mirror, while normal retention now keeps more classes. `RX_VK_ASTC_BC7=1` is opt-in, `RX_VK_ASTC_RGBA8` wins; `RX_ASTC_BC7` is internal. `RX_VK_NO_DRAWN_WRITEBACK=1` disables a repair, never an ordinary baseline choice. |
| CPU/video (#2114/#2603/#2637) | `HOS_VIDEO_RGBA=1` selects eager conversion. `HOS_RESERVE_CORE_MASK` is a hex override. Unset `HOS_SHORT_SLEEP_TIMER` now means guest-only timer sleeps on Linux; explicit off means yield, explicit on all-timer. |
| Vendor paths (#1906/#2499/#2525/#2701) | Retire/import high/low controls are Windows AMD/Intel paths, not equivalent Linux experiments. `RX_VK_REFLEX_SUBMIT_IDS=0` disables tags, not proof of display latency. `RX_VK_FALLBACK_PHI_LIMIT` is the RADV GCN limit; skipped programs omit draws. `RX_VK_DLSS_FG_MOST` is a test cap for fewer generated frames, untestable by headless timing alone. |
| Budget/build (#2603/#2634/#2698/#2748) | Integrated budget uses RAM/reserve/driver allowance; explicit `RX_VK_VRAM_MB` wins. Prompt results are `prompts: unchanged, kept` or `prompts: regenerated (<reason>)`. Windows lift emission is parallel too, but this machine keeps its eight-worker cap and build lock. |

The active regression parsers still match `N presents`, GPU-time, replay-phase, VRAM and readback prefixes.
Do not anchor old full lines: mirror logs add idle-kept bytes; pool logs add startup blocks; the old fixed
integrated-GPU sentence becomes `integrated GPU texture budget: ...`; large ASTC may say BC3 **or BC7**;
subgroup lines can append `RADV on GCN 1-4`. New staging/prewarm/reserved-core/present-mode diagnostics are
not generic failures. Keep raw lines and use the measured clock/scene window rather than a full-log average.
