# The game's functions

A map of Ocarina of Time's lifted functions: what each one we have looked at does, what its objects hold, and how sure we are.
The game ships without symbols, so the lifted code names every function by its guest address (`f_833ec800` is the function
at 0x833ec800). These pages give those addresses names and meanings, one page per part of the game.

The addresses are for the supported build (`e0f3ccfa`, the one `nx2/oot_patches.py` patches). The game does not change under
us, so an entry stays true however much LS changes; what can go stale is how it links to LS's code (see
[The check](#the-check)). Issue and PR numbers such as #2545 are HS's (the upstream project's, on GitHub), where
these pages were started; LS's are written "LS #N".

To find a function, search for its address: every function has one row, on one page. `python3 docs/oot/fnmap.py find
f_833ec800` (or words) prints the page and the row.

**LS** is the port these pages were written for and **HS** the project it grew out of. Paths such as
`nx2/oot_patches.py`, `host/` and `gpu/` are in the port's repository, not in this one.

## Pages

| Page | What it covers |
|---|---|
| [save-list.md](save-list.md) | The save list screen, its save cards, the confirm screen and the game's slide lists |
| [layout.md](layout.md) | Layout panes, text panes and the letter animation, animations, capture panes |
| [menus.md](menus.md) | The menu screens' focus and update, the pause menu's pages, Key Items |
| [save-data.md](save-data.md) | The caption manager, save files and slots |
| [game-data.md](game-data.md) | Game data (the game's flags and values by hash), the save files' format, `account.sav` |
| [master-quest.md](master-quest.md) | The Master Quest add-on check, Master Quest mode and how its dungeons load, and its saves |
| [maps.md](maps.md) | How a map loads: the map index (`GameBancParam`), the layouts, collision and terrain, preloading, the per-map tables |
| [player.md](player.md) | Link's movement speed, health, movement update and the pouch (items) |
| [events.md](events.md) | Requesting an event (the teleport's change of map), the world's clock |
| [camera.md](camera.md) | The player camera's update, yaw and pitch, its follow, the gameplay view |
| [frame-time.md](frame-time.md) | The frame step, the game clock, the frame limiter, movies, and the sites examined above 60 fps |
| [jobs.md](jobs.md) | The job phase: the thread pools, the job record and graphs, the frame on the main thread, the critical path |
| [graphics.md](graphics.md) | Resolution, the output size, the render targets' memory and the save picture's viewport |
| [rendering.md](rendering.md) | The render pipeline: its switches, the frame's order, the named render targets by pass, what the renderer recognises and fixes |
| [drawing.md](drawing.md) | What the CPU does to issue the frame's GPU work: parallel draw-list batches, per-draw binding and its cost, the draw loops, submission |
| [nvn-calls.md](nvn-calls.md) | The NVN boundary: every NVN function the game calls, how often and from where, the inline-command flag, and what LS's recorder does per call |
| [physics.md](physics.md) | Havok physics, cloth and navigation: the world step and its jobs, substeps and time step, the hot collision and cloth functions |
| [navigation.md](navigation.md) | Navigation and Havok AI: the navigation task, the collision-avoidance character step and its workers, path finding |
| [effects.md](effects.md) | Particle effects (nn::vfx3 and ELink): the effect manager, the per-emitter-set calc and its threads, CPU and GPU particles, effect time, the caps |
| [animation.md](animation.md) | Skeletons and animation: the animation sequencer, bone transforms, per-frame update and its cost |
| [ai.md](ai.md) | NPC and enemy behaviour: what drives the AI graphs each frame, NPC components, perception, work counted in frames |
| [hot-path.md](hot-path.md) | Where the game spends its CPU time: the top hot functions by subsystem and page |
| [shaders.md](shaders.md) | Shader archives, program variations and how a material picks one, binding and its cost; and what the port does with them: translation to SPIR-V, the program cache, preparation, the per-shader fixes |
| [textures.md](textures.md) | How textures are made and filled, the terrain's tiles, LS's texture budget and eviction, and what can come back wrong |
| [culling.md](culling.md) | Occlusion queries and conditional draws, the CPU's visibility queries, the other culling layers, and how each fares in LS |
| [input.md](input.md) | The controllers: nn::hid, sead's controller manager and devices, the Npad and merged controllers, motion, virtual keys, rumble |
| [audio.md](audio.md) | The sound system and what it asks of nn::audio |
| [environment.md](environment.md) | The world manager, time of day, the sun and moon, sky, weather, fog and the environment's parameter files |
| [actors.md](actors.md) | The actor system: creation and deletion, the per-frame processes, resident and sleeping actors |
| [resources.md](resources.md) | File and resource loading: mounts, archives, the Zstd decoder, the resource managers |
| [sequences.md](sequences.md) | The script-node classes: registry, key hash, factory, vtable layout, parameter binding, catalogue |

## What may go here

What we worked out: addresses, names, what a function does, what its arguments and fields are, and how sure we are. Not the
lifted C, disassembly or anything copied out of the game's files (the port's `CONTRIBUTING.md`: nothing made from
the game leaves the PC). Names the game itself uses (a pane's, a layout's, a sequence node's) are quoted as identifiers.

These pages describe behaviour, for interoperability. Apart from identifiers the game itself uses (above), they do not
quote the game's code or data, and they hold none of its files, assets or keys; anything copied from the game file belongs
in the port's `work/` folder, which never leaves the PC. A row needs an address, a name of ours and a description in our
own words, and nothing more from the game.

## Conventions

- **Addresses.** A function is `f_` and its address in eight hex digits (`f_833ec800`); data is `0x` and its address
  (`0x84e98e38`); a place inside a function is its address (`at 0x834208a4`).
- **Arguments.** A call's arguments are in `x0`, `x1`, … and its float arguments in `s0`, `s1`, … (AArch64); the result is
  in `x0` (`s0` for a float). In a name, `(card, entry)` gives the arguments in that order.
- **Fields.** `obj+N` is the field at byte offset N of the object `obj` points to, in decimal as the lifted C writes it; a
  hex offset is written `+0x1a4`. `[x]` is the 64-bit value at x, so `[[0x84ca29c0]+216]` follows two pointers.
- **Names.** A function's name is ours, a working name in `Owner_Action` form (`SaveCard_Fill`), so a search finds it and a
  reader can follow the page. A name the game's own strings give is kept as it is (`UISystemLoadListScreen`,
  `SeqQueryHasAoCMQ`). When a function's role is not known, name it by its owner and vtable slot (`LoadListScreen_vt104`).
  Rename freely as we learn more: the address is the key, not the name.
- **How sure.** Every row says one of:
  - **tested**: seen to work in the game, live or in a test build. A function a shipping patch hooks counts as tested for the
    role the patch relies on;
  - **read**: worked out from the lifted code, not tested;
  - **guess**: likely, from names, constants or the code around it.
- **One home.** Each function and each data address has one row, on the page of the part of the game it belongs to. Other
  pages mention it by address and link to that page.
- **Hooks.** A function LS patches says so at the end of its row: `Hooked by patch_menu_focus` (in backticks, a
  function in `nx2/oot_*.py`). The patch's comment says what the patch changes; the row says what the game's function does.

## A page

Every page has this shape. Leave out the sections a page does not need, and split *Functions* into one section per part of
the subject when it is long, each with its own table.

```markdown
# <What part of the game>

<A paragraph: what this part of the game is, and what we were working on when we looked at it.>

**LS code:** `nx2/oot_patches.py`, … (or "none yet")
**See also:** [other-page.md](other-page.md)

## Functions

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_833ec800` | `SaveCard_Fill(card, entry)` | Fills a card for an entry. … Hooked by `patch_x` | tested |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84e98e38` | `g_CaptionMgr` | The caption manager's pointer | read |

## Structures

### <Object> (<size> bytes)

| Offset | Field | Sure |
|---|---|---|
| `+600` | The entry the card shows | read |

## Notes

<What does not fit a row: how the parts work together, what failed and why.>

## Open questions

- <What we do not know yet.>
```

The check reads the tables by their header rows, so keep those as they are.

## Adding to it

- **Search for the address first.** If it has a row, add to that row (and raise its *Sure* as you confirm it).
- **Write it in the same PR as the code that needed it.** When a patch or a host file names a game function, that function
  gets a row; the check fails until it has one.
- **Say what you saw.** A row is a claim about the game. If you tested it, say how in the row or in *Notes*.

## How we find things out

- The UI shows and hides through animations, not pane flags (see [layout.md](layout.md)), and anything drawn into a capture
  pane can't be changed after the capture is taken.
- To learn what a run-time structure does, poke it live from outside (ReadProcessMemory / WriteProcessMemory on the guest
  window, at host `0x100000000000` plus the guest address) and capture the window. That is much faster than a build per guess.
- To find what writes a value, put a hardware write watchpoint on it in the running game (how the camera's yaw and pitch
  writers were found: [camera.md](camera.md)).
- The game reads its flags by the murmur3 hash of their names, built in registers: look for MOVZ/MOVK pairs such as `0x5097`
  / `0xa299` (that is, 0xa2995097). See [game-data.md](game-data.md).

## The check

`python3 docs/oot/fnmap.py check --repo <the port's clone> --rev <commit or branch>` checks the pages against themselves
and against the port's code (`LS_REPO` names the clone when `--repo` is left out). It checks:

- that every function table and data table row has an address, a name and one of the three *Sure* values;
- that no function or data address has rows on two pages, or twice on one;
- that every game function LS's code names (`nx2/oot_*.py`, `host/`) has a row, except the ones in `undocumented.txt`
  beside the script (named in the code, but not a function of this game). A function on that list that has a row fails too;
- that every `patch_…` a page names is a function in `nx2/oot_*.py`;
- that every relative link resolves, and that this index lists every page.

It also notes, without failing, each function `nx2/oot_patches.py` patches whose row does not say `Hooked by`. Run it
after pulling the port's main: a function the code names without a row is the usual way these pages fall behind.

## Where these pages come from

dyst wrote them, with Claude, and first proposed them for the port's own repository. They are kept here, with his
agreement, because he wants people to use them and the port's repository was not the right place for them. The first
commit of this folder is the pages as he wrote them; later commits adapt them to this repository (the names LS and HS,
paths written as plain text) and keep them up to date. They were written for the port's GPL-3.0-or-later repository:
treat this folder as GPL-3.0-or-later, whatever licence the rest of this repository has, unless dyst says otherwise.
