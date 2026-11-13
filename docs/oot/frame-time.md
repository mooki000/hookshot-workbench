# Frame time and frame rate

How the game steps its frames: the frame step and the game clock, the frame limiter, movie and cutscene timing, and the sites
examined for a frame-rate cap above 60 (issue #1742). Above a cap of 60 the game's frame step is the frame's own time (0.42 at
144 fps), so a site that cuts the step to whole frames or counts updates goes wrong there.

**Longshot code:** [nx2/oot_patches.py](../../nx2/oot_patches.py) (`patch_frame_seconds`, `patch_movie_frames`,
`patch_whole_frames`), [host/nn_vi.c](../../host/nn_vi.c) (`hos_frame_seconds_bits`, `hos_movie_frames`, the frame limiter)
**See also:** [graphics.md](graphics.md)

## The step and the clock

The engine has two places where its integer nominal rate (`+0x2c8` of the graphics module) becomes a time step, both a float
divide "base / nominal". The engine's own rate is 60 / whole display intervals, never above 60, and its controller puts 60
back over a higher value written from outside before either divide runs.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80706be0` | `ModuleSystem_ModuleCalc` | The ModuleSystem's calc: runs the frame's two module job graphs ([jobs.md](jobs.md)), and in it the frame's step, base 60.0 (30.0 in the half-rate mode): added to the game clock (the double at `[x19+0xd58]`, in 1/60 s) and handed on as the frame's seconds. Hooked by `patch_frame_seconds`: base times the host's measured frame time | tested |
| `f_83b6ea90` | `Frame_GlobalStep` | The global step (60.0 / nominal, the float at `0x84d61fc8`) the rest of the engine reads. Hooked by `patch_frame_seconds` | tested |
| `f_83b6e300` | `FrameTime_Update` | The frame-time keeper (`[[0x84c9e890]]`): `+0` the scaled step, `+16` the step, `+20` 1/step, `+24` the scaled step, `+28` fps, per-slot copies at `+32 + 48k` | read |
| `f_8042cd20` | `Frame_Limit` | The frame limiter: sleeps to 1 / `+0xfc` of the graphics module | tested |
| `f_804fc0f0` | `Display_VrrEnabled` | Whether variable refresh is on (`+0x231`). While it is, the engine's adaptive controller writes `+0xfc`, any rate from 40 up | read |

## Movies and cutscenes

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83b7c710` | `MoviePlayer_CountFrame` | Counts the movie player's updates while a movie plays: one more at `player+760` each frame (#1875). Hooked by `patch_movie_frames`: the count grows by the frame's time, as the clock does | tested |
| `f_83b7ca60` | `MoviePlayer_Accumulate` | The movie's timed accumulator (it keeps its remainder: fine above 60) | read |
| `f_83b5ec10` | `Cutscene_MovieFrame` | A cutscene that shows a movie takes the player's count as its timeline's frame (with `f_83b5e9a0`): the narration, the rumble and the cutscene's end are placed by it | tested |
| `f_83b5e9a0` | `Cutscene_MovieFrame2` | The other half of that, with `f_83b5ec10` | read |
| `f_8124cfc0` | `Timeline_FireEvents` | Fires the events whose time lies between the timeline's last frame and the new one, walking a list sorted by time. The in-engine timelines run it with a rate times the time step | tested |

## Sites examined above 60

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80a87e50` | `PickupCounter_Update` | The pickup counter above Link's head: moves the count it shows toward the new total by `(int)(step * …)`. Stalled above 60 (#1832, fixed in #2006). Hooked by `patch_whole_frames` | tested |
| `f_803edf70` | `TimeOfDay_Update` | The time of day: adds `(int)(2 * step)` game seconds a frame (`+60`) to the seconds of the day (`+40`, mod 86400), then the day (`+44`). Stood still above 120 fps (the fix is PR #2093, open) | tested |
| `f_800672e0` | `SubStepLoop` | A loop of `node+76` sub-steps times `(int)step`; skipped entirely above 60. What it drives is unknown (called from `f_80660320`) | read |
| `f_83be20b0` | `UnchangedTimeout` | A 300-update timeout on an unchanged value (would run fast above 60) | read |
| `f_83c53620` | `ListEntryTimer` | A 180-update timer per list entry (would run fast above 60) | read |
| `f_8057fd20` | `DirectionLookup` | An angle lookup that sorts nearby objects into 16 or 32 directions (fine) | read |
| `f_806d4720` | `NearestHeapA` | A "nearest N" heap by squared distance (fine) | read |
| `f_806d5410` | `NearestHeapB` | Another of the same (fine) | read |
| `f_800fb340` | `HitTest_RoundUp` | A hit test that rounds up (fine) | read |
| `f_802c0540` | `FractionalTimerA` | A timer that copes with fractional steps (fine) | read |
| `f_80596c60` | `FractionalTimerB` | The same (fine) | read |
| `f_80746660` | `FractionalTimerC` | The same (fine) | read |
| `f_8156f0c0` | `FractionalTimerD` | The same (fine) | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84d61fc8` | `g_GlobalStep` | The global step float `f_83b6ea90` writes | read |
| `0x84c9e890` | `g_FrameTimeHolder` | The frame-time keeper, `[[0x84c9e890]]` | read |

## Notes

- More sites counted in frames, from the later pages (each runs proportionally more often in real time above 60 fps): the actors' calc-skip intervals (`(frame - phase) mod interval`, the frame at `[0x84eb0d90]+0x440`) and the LOD module's `mod N` / `mod 5` staggering ([animation.md](animation.md)); AI and NPC component work every 10, 30, 60 or 150 frames by BaseProc id, and AIGroupParallelCalc's every-N ([ai.md](ai.md)). **read**
- The physics world's base step is the literal 1/60 s at 0x83b89578 (`DF+0x24`), and the frame step reaches the solver through its step count, set each update from it (`DF+0x38`); Longshot rounds the count up above 60 fps (issue 1798, `host/oot_high_fps.h`; [physics.md](physics.md)). **read** Cloth also follows the frame's step.
- `0x84d61fc8` (the global step) has one reader, `f_834a3680` ([physics.md](physics.md)'s scan). **read**

- Above 60 fps, before the step hooks, every frame still stepped 1/60 s and the game ran fps / 60 times too fast.
- A movie's picture follows the clock, but its cues follow the update count, so above 60 they ran ahead (fps / 60 times).
  The timeline doesn't need every frame value, since it fires everything in between: measured at 36 fps under a cap of 120,
  steps of 1 and 2 gave every narration line and the rumble as at 60.
