# Hand-over: LS on macOS, leading into three-player co-op

Written 2026-11-13 by the owner's main agent session on the Linux PC, for the agent that starts on the owner's
Apple Silicon MacBook Pro (M4 Max). Read it whole once; it tells you what to read next.

## Your job, in this order

1. **Set up this workbench on the Mac** so you work by the same rules and with the same tools as the Linux PC.
2. **Get LS to build and start on macOS**, as small separate pull requests. LS does not support macOS today.
3. **Then join the co-op tests as the third machine**: Linux with NVIDIA, Windows with Intel graphics, macOS on
   Apple Silicon. The Mac sits behind a corporate security proxy, so it is also the hard network case.

Step 3 has a part you can start on day one, before any Mac build exists: see "Co-op from behind the proxy".

## What you need from the owner (private, not in this repository)

- **The private note.** It lies beside the game file on the owner's file server and says where the game file is,
  its checksum, the machines' names and addresses, and where the co-op plan and reports are. Ask him for its path.
- **The game file** (one `.nup` package, about 26 GB). Verify the checksum from the note after copying.
- **Access to the team's forge under his account**, made by him on the Mac. Never copy a token or key from
  another machine, and never put one in a file here.
- **His word before anything is posted**: a pull request, an issue, a comment, a message in the team room.

## Rules that travel with you

Read these before touching anything: [agent-brief.md](agent-brief.md) (rules for every agent), then the skills
`ls-workbench` (start here for building, running and testing), `ls-pr-evidence` (before any pull request or
comment), `ls-function-map` (before reading the game's lifted code) and `ls-skill-upkeep` (how to report what a
skill got wrong). The ones that matter most on a new machine:

- **Nothing from the game leaves the owner's machines**: not the game file, not extracted data, not a built game,
  not lifted code, not saves. Pictures of the game and logs are fine as evidence. Nothing of it goes into a
  repository, a report that is published, or any public text.
- **A pull request opens as work in progress** and goes ready only when every box of the forge's checklist is true
  for the pushed head. A ticked box is a claim about that exact head. A description that says it waits for
  something is not marked ready.
- **Measure honestly**: say what was run, on what, and what was not. "Not tested" is a normal sentence.
- **Commits** carry the owner's identity and name the model that helped in their trailers. Use your own model's
  name there, not the one in this repository's history.
- The machine-specific half of the brief (paths, locks, gates, the 480-second limit on a game run) was written for
  the Linux PC. Keep its intent on the Mac: one game at a time, a hard time limit on every run, logs kept. See
  "The harness on a Mac".

## Where LS stands on macOS today

Checked on LS main `514591a3`, 13 November 2026. File names are the port's own; the port's repository is linked at
the top of this repository's README.

- **Supported platforms are Windows and Linux on x86-64.** The README asks for a 6-core x86-64 CPU and a card with
  Vulkan 1.3 (mesh shaders recommended). No issue or pull request on the forge mentions macOS.
- **No Apple-specific code exists** in `host/`, `gpu/` or `nx2/`. The only `__APPLE__` lines are inside bundled
  third-party libraries.
- **One build script**: `nx2/make_game.py` (`--nup <file>` or `--romfs`/`--exefs`, `--check` to only check the
  game's version). It wants clang and lld, the Vulkan, SPIR-V and VMA headers, wgpu-native, SDL2, FFmpeg and a few
  prebuilt helper tools that a release carries and a clone does not. `tools.lock` pins versions. The README's
  "Build from source" and `docs/development.md` are the reference.
- **The host is written against Win32 calls.** Linux gets them through a translation layer in `host/posix/`
  (`win32_posix.c`, `windows.h`, `display_sdl.c`, `prof.c`). macOS would be that layer's second user. It uses
  things only Linux has: `MAP_FIXED_NOREPLACE`, futex waits, Linux's register names in signal contexts, and
  `SOCK_CLOEXEC`/`SOCK_NONBLOCK` as socket-type flags (`host/control.c`).
- **Guest memory sits at a fixed host address**: `A64_HOST_BASE` is 16 TB (`third_party/a64-kit/rt/a64.h`), mapped
  in `host/hos_main.c`. The guest's page size is 4 KiB (`host/hos_svc.c`).
- **Three places are guarded for x86-64** (`host/hos_svc.c`, `host/nn_os_sync.c`, `gpu/vk/vk_memory.cpp`), and the
  lifter (`third_party/a64-kit/lift/a64_lift.py`) mentions x86 often. What each does without x86 is not known yet.
- **The renderer asks for Vulkan 1.3** (`gpu/vk/vk_main.cpp`) and names many extensions, among them mesh shaders,
  present id and wait, external memory by file descriptor, push descriptors, pipeline libraries and shader module
  identifiers. Which are required and which optional has to be read from the code. Vendor upscalers
  (`vk_dlss.cpp`, `vk_fsr4.cpp`) have no macOS counterpart.
- **Window, input and sound on Linux go through SDL2** (`host/hos_sdl.c`, `host/pad_sdl.c`,
  `host/posix/display_sdl.c`), which exists on macOS.
- **The control bridge** (how the harness plays without a controller) is a Unix socket on Linux and a named pipe
  on Windows (`host/control.c`).

## Settle these first: each is a small experiment

1. **Which CPU route.** macOS on Apple Silicon uses 16 KiB pages; the guest wants 4 KiB, and the port assumes
   x86-64 in places. An x86-64 build run under Rosetta sees 4 KiB pages and matches the port's assumptions, so it
   is probably the shortest way to a first start. A native arm64 build is the better end state and more work. This
   is reasoning, not a result: try the fixed-address mapping and a 4 KiB protection change in a ten-line program
   both ways before choosing.
2. **Vulkan on Metal.** macOS has no Vulkan of its own; a translation layer provides it. List the extensions and
   features the renderer requires (not merely names), compare with what the layer reports on that Mac, and write
   down every gap. That list decides how much renderer work there is.
3. **The lifted code.** Does the generated C compile for the chosen CPU route with Apple's clang, and what do the
   x86-guarded blocks fall back to?
4. **The translation layer.** For each Linux-only call in `host/posix/`, what is the macOS equivalent, and does
   the behaviour the callers rely on (timeouts, wake-ups, flags set atomically) still hold?
5. **The toolchain.** Which pinned tools have macOS builds, and which helper tools must be built from source.

## Suggested pull requests, smallest first

Each is one topic, leaves Windows and Linux untouched, and says plainly what it does not yet do. The Linux PC can
build a branch for Windows and Linux to prove that; ask through the owner.

1. A tracking issue: the plan and the findings from the experiments above (with the owner's word).
2. The game-free test suite runs on macOS: what passes, what is skipped and why. No game needed, and it finds most
   portability problems cheaply.
3. The build scripts know macOS: toolchain discovery and flags, nothing else.
4. The translation layer builds and passes its tests on macOS, one group of calls at a time.
5. The renderer starts: instance, device and a window through the Vulkan-on-Metal layer; vendor upscalers
   compiled out; each missing feature named.
6. First start to the title screen, then into play, with pictures and a test request for other Macs.

## Co-op from behind the proxy: start before the Mac build exists

Co-op needs every player to reach one small relay by UDP. Corporate security proxies often pass web traffic only.

- The relay is a standard-library Python script in the port (`scripts/coop_relay.py`) and the protocol has
  game-free tests. Both should run on macOS as they are; confirm that first.
- **First experiment, no game needed:** from the Mac, can a UDP datagram reach a relay on the owner's home network
  or on a public host and come back? Record what is sent, what arrives, the delay and the loss, with the relay's
  own log. The Linux PC has rehearsal tools for loss, delay and outages; the private note says where.
- **If UDP does not pass, that is a finding, not a problem to route around.** Report it: it means co-op needs a
  second transport that such networks allow. Do not disable, reconfigure or tunnel around the security software on
  that Mac, and do not install remote-access tools on it, without the owner's explicit word. It is his employer's.
- Where co-op stands, what is proven and what the first playtest must show is in the co-op plan the private note
  points to. The work is on a pull request on the forge; ask the owner for its number.

## The harness on a Mac

- **Works as it is:** the skills (plain Markdown), the function map and its tool (`docs/oot/`, standard-library
  Python), the tests under `tests/`, and the logic of the regression, comparison and scripted-play tools.
- **Linux-only, to replace rather than port blindly:** the GPU gates under `gates/` (they read NVIDIA and Intel
  state on Linux), the lock commands (macOS ships no `flock` program), the core-dump wrapper, window control (it
  speaks to one Linux compositor), and everything about AppImages.
- `./relocate.sh /Users/<you>` rewrites the example home in this checkout's text. The README's "Using it on
  another machine" lists the paths the tools expect.
- Keep the two rules the gates exist for: one game at a time, and never start a measurement while something else
  is using the machine.

## Reporting back

- Write what you find as you go, as dated Markdown notes, and copy them to the hand-back folder named in the
  private note. The Linux PC's main session reads them there.
- End every note with **Skill notes**: what a skill got wrong, lacked or made slow on macOS. That is how the
  skills here get a macOS half (`ls-skill-upkeep` describes the loop).
- Rows for the function map go back the same way; only the Linux PC's main session commits to this repository,
  unless the owner tells you otherwise.

## First hour

1. Clone this repository. Read the README, [agent-brief.md](agent-brief.md) and the four skills named above.
2. Get the private note and the game file from the owner; verify the checksum.
3. Clone LS. Read its README ("Build from source"), `CONTRIBUTING.md`, `AGENTS.md`, `docs/development.md` and
   `docs/design.md`.
4. Run the game-free suite on the Mac as it is and keep the full list of what fails. That list is your first
   finding.
5. Run the UDP experiment through the proxy.
6. Write both up with your proposed list of pull requests, and give it to the owner before opening anything.
