# hookshot-workbench

The tools, test harness and agent skills I use to build, run, test and measure
[Hookshot](https://github.com/NotRexed/Hookshot) (the static recompilation of the Switch 2 remake of *Ocarina of Time*)
on Linux. I'm sharing them so other contributors can see how my test results are made, reuse what helps, and build on
it. It is not part of Hookshot and not endorsed by its maintainers.

**No game files are here, and nothing made from them:** no dump, no saves, no built game, no data read out of the
game's files. You need your own legally obtained game and your own Hookshot build.

I work with coding agents (Claude, and GPT-6 Astra). The `skills/` are the instructions those agents follow on my machine, and most of
the scripts were written by them under those rules, then run and checked here. The commits say so in their trailers.

## What is here

| Folder | What | Start with |
|---|---|---|
| `skills/hookshot-workbench` | How to build Hookshot from source on Arch, run it unattended, test and debug it: the commands, the times they take, the traps | `SKILL.md` |
| `skills/hookshot-pr-evidence` | The standard for a pull request and for anything posted: the claim, the overlap search, the evidence to collect while working, the body's form | `SKILL.md`, `scripts/pr_overlap.py`, `scripts/panels.sh` |
| `skills/hookshot-perf-compare` | Comparing two builds or settings honestly: runs in turn, a quiet machine, per-run tables, what counts as a difference | `SKILL.md` |
| `skills/hookshot-scripted-play` | Playing without a controller: inputs and state samples through the control bridge, the file driver for older builds, saves at story points | `SKILL.md` |
| `skills/hookshot-issue-triage` | Reading an issue or someone else's pull request and reproducing it on this hardware | `SKILL.md` |
| `skills/hookshot-discord-updates` | Posting claims and pull request updates to the project's Discord as me, through my own signed-in browser tab: the fixed forms, the checks before a post, what I see first. Discord's terms do not allow automating a user account; I accepted that for a few short posts a day, and you should decide for yourself | `SKILL.md`, `scripts/discord_tab.mjs` |
| `skills/hookshot-regression-hunt` | Finding which merged pull request made a build worse: show the difference first with the same measuring code in both builds, test named suspects by revert, then bisect | `SKILL.md` |
| `skills/hookshot-nightly-playtest` | Building the project's nightly tag from source each night, running its checks, and comparing the result with the nights before; what a first night can and cannot say | `SKILL.md` |
| `harness/` | The run wrapper and its helpers | `hs-run.sh` |
| `gates/` | Queues that let many test runs share one GPU and one CPU without hurting each other or the desktop | `hs-gpu-slot` |
| `play/` | Step drivers that play on a running game: one for the control bridge's state samples, one for older builds with the state file; the driver is chosen explicitly | `hsap.py`, `hsap_file.py` |
| `regress/` | The regression hunt's engine and its two adapters (build a candidate, run a scenario), with their game-free tests | `regress.py` |
| `nightly/` | The nightly's driver (build, the checks, the comparison with earlier nights, the report) and a rolling test of each new main; its game scenarios are written but have never run on the game | `hs-nightly.py`, `rolling.py`, `README.md` |
| `docs/agent-brief.md` | The rules every agent on my machine reads first | |

### The harness

- **`harness/hs-run.sh <game copy> <tag> <seconds> [options] [NAME=VALUE ...]`**: one unattended run, headless by
  default, through the GPU gate. It leaves everything in `<copy>/runs/<tag>/`: the log, pictures every N presents, the
  frame log, and one summary line (exit, frames, mean and 1% low, slow frames, fatal and device-lost lines, kernel
  driver lines, the load before and after). `--intel` runs on the integrated GPU, `--window` opens a real window for
  what needs a swapchain, `--input` plays a press script, `--user` uses a throwaway save folder.
- **`harness/hs-idle.sh <command>`**: runs the game-free checks (`tests/run_all.py`) in the idle scheduling class, three
  at a time, never beside a timing run, and with core dumps switched off.
- **`harness/hs-nocore.sh <command>`**: runs one command with core dumps off. Hookshot's checks crash three fixture
  programs on purpose; on a desktop with a crash notifier each one raised a notification.
- **`harness/hs-pr-check.sh <branch> [title file]`**: the mechanical checks before a branch is pushed: based on current
  `main`, the commit identity and trailers, no game file or build output among the changed files, a test changed or
  added, a Conventional Commit title. Prints PASS or FAIL.
- **`skills/hookshot-pr-evidence/scripts/pr_overlap.py [numbers | --branch name]`**: which open pull requests touch the
  same files as yours, and which of them really conflict with your change (not just with `main`).
- **`skills/hookshot-pr-evidence/scripts/panels.sh <out.jpg> <per row> <frame> "<caption>" ...`**: several frames as
  one labelled picture for a pull request.
- **`harness/hs-watch-threads.py [numbers]`**: waits until someone comments on, reviews, merges or closes one of your
  threads, then prints what is new.
- **`harness/hs-window.py`**: the compositor's side of a windowed run on Hyprland (a window that never takes focus).
- **`harness/hs-save.sh <name> <folder>`**: copies a save from a local library into a throwaway user folder for one
  run. The library is not here: saves are game data.
- **`harness/deps-local.sh <worktree>`**: fetches what a source build needs on Arch into one folder, from the pins of
  Hookshot's own `scripts/linux/deps.sh`, without touching the system.

### The gates

One GPU that is also the desktop, thirty-two threads, and many test runs at once: without a queue, two games ran the
card out of memory and took the desktop down. The gates are small shell scripts around lock files:

- **`gates/hs-gpu-slot <command>`**: one or two games on the card at a time, served in the order they asked;
  `--priority` for a single short run, `--exclusive` for a frame-rate measurement that needs the machine quiet. It
  checks free card memory and system memory before it lets a second game in.
- **`gates/hs-intel-slot <command>`**: one run at a time on the integrated GPU.
- **`gates/hs-build-slot <command>`**: four builds at a time in the idle scheduling class.
- **`gates/gpu-guard.sh`**: watches the kernel log; at a driver fault line it ends every test run and closes the gate.
  It also ends anything of the harness that uses the card outside the gate.


### Playing without a controller

`play/hsap.py` runs one game through `hs-run.sh` and plays steps on it through the control bridge (`HOS_CONTROL`):
inputs as the bridge's own commands, and Link's place, facing and state from the state samples that
[Hookshot #2292](https://github.com/NotRexed/Hookshot/pull/2292) adds to it (`status.game`, and `subscribe` with
`events:["oot"]`; `game.frame` dates a sample). `walk <x> <z>` steers to a point and says whether it arrived, got
stuck or was held by a scene. Main's control bridge alone is not enough: the build must have that subscription, and
the driver says so in one sentence when it does not.

Older builds with the state file (`HOS_OOT_STATE_FILE`) need `play/hsap_file.py`, the earlier driver kept as it was.
There is no silent fallback from one to the other. `play/tapcheck.py` puts a run's samples beside its pictures;
`play/test_hsap.py` and `play/test_hsap_file.py` test the two drivers without a game.

### Finding a regression

`regress/regress.py plan` reads a commit range and lists it with the pull request numbers and the groups of commits
that can share one long build step. `regress.py hunt` confirms the good and the bad end in one quiet hold (old, new,
old, new), tests named suspects by reverting them, then bisects. It stops at the number of holds, steps and minutes it
was given, and what it then reports is the smallest range the clean runs support, not a guess. The scene, the save and
the bands that count as good and bad come from a scenario file that stays on the machine: none is here.
`regress/build_case.py` and `regress/run_case.py` are the adapters for this workbench's build and run wrappers;
`regress/test_regress.py` tests the engine on made-up histories without a game. First published as written, before a
whole hunt had run through it on the game.

### A nightly build and its checks

`nightly/hs-nightly.py run --game-free` builds the project's `nightly` tag from source, runs its game-free checks and
compares the named results with the night before. The comparison's rules (a band from the last three to seven good
nights, a disturbed run never feeding a timing baseline, one confirming rerun before anything is called a regression)
are tested on made-up numbers by `nightly/test_compare.py`. The part that plays scenarios on the game, collects frame
times, memory, log lines and pictures, and hands a confirmed regression to `regress/` is written and tested against
stand-ins only: it has never run on the game. It stops, and counts nothing, whenever a game is started from the
player's own folder. Saves, scenarios and results stay on the machine.

### A rolling test of each new main

`nightly/hs-nightly.py rolling` is one cycle: fetch main once and pin its head (the same head as the last cycle: write
"unchanged" and stop), build it in full with the build's time recorded, prepare its shaders, give each scene one
unmeasured run, then measure new, previous, new, previous in one exclusive hold per scene with the regression tool's
adapter and quiet rule. It calls something a regression only when both pairs point the same way and the gap is
outside the repeats' spread; then it writes the hand-over file for `regress/` and stops. It starts no hunt, posts
nothing and installs no timer: `rolling --check` starts a cycle when main has moved and three hours have passed, and
`rolling --watch` repeats that check while it runs. The player's-folder rule holds for the whole cycle. Its decisions
are tested on made-up numbers (`nightly/test_rolling.py`); the builds have run, a measuring cycle on the game has
not yet.

## What is not here, and why

- **Anything made from the game**: saves, the world model my planner builds from my own dump (areas, links, event
  flows), and scripts that carry lists read out of the game's files. The planner, the pilot and the save maker follow
  once I have separated their code from that data.
- **My working notes**: the reports, drafts and logs the skills mention under `reports/`. The skills name those paths
  because that is where an agent on my machine writes; you would use your own.

## Using it on another machine

Everything was written for one machine: Arch Linux, Hyprland, an RTX 4090 on NVIDIA's driver with an Intel UHD 770
beside it, and these places:

| Path | What |
|---|---|
| `/home/mooki/Code/Hookshot` | the clone (`origin` = NotRexed, `fork` = mine) |
| `/home/mooki/Code/hookshot-wt/<name>` | worktrees, one per branch |
| `/home/mooki/Code/hookshot-wt/deps` | Python and headers from `deps-local.sh` |
| `/home/mooki/Games/.hookshot-h-<name>` | built game copies for test runs |
| `/home/mooki/Games/.hookshot-harness/` | `harness/` lives here |
| `/home/mooki/Games/` | `gates/hs-*-slot` live here |
| `/home/mooki/.agents/skills/` | `skills/` live here |
| `/tmp/hs-gpu-slots/` | the GPU gate's queue and state |

`./relocate.sh /home/you` rewrites `/home/mooki` in every file of this checkout. The GPU gate's numbers (how much card
and system memory a game needs) are for a 24 GB card and 96 GB of RAM: read the top of `gates/hs-gpu-slot`
before you rely on them. `hs-window.py` speaks to Hyprland only.

The skills are plain Markdown with a short header. Agent tools that read `~/.agents/skills/<name>/SKILL.md` (or
Claude Code's `~/.claude/skills/`) pick them up as they are; a person can read them as the manual.

## How the results I post are made

When I post a number on a Hookshot issue or pull request, it comes from here: a run through `hs-run.sh`, its summary
line and log, a script that makes the table, and a picture I looked at. `skills/hookshot-pr-evidence/SKILL.md` is the
standard I hold those posts to: what was measured, on what, what was not, and what failed.

## Licence

MIT, see `LICENSE`. Hookshot itself is GPL-3.0-or-later and lives in its own repository.
