# The hot path

Where the game spends its CPU time, function by function: the 200 hottest functions of the sampled profile that
LS's build orders its code by, each put in a subsystem and pointed at the page that documents it. Looked at in
November 2026 by static analysis of the binary (strings, callers and callees, vtables, address ranges), building on the
frame's critical path measured in [jobs.md](jobs.md). This page is an index, not a home: a function keeps its row on its
subsystem's page, and the tables here use their own header, so the check does not read them.

**LS code:** `nx2/build.py` (`HOT_ORDER`: links the lifted functions in `hot_order.txt`'s
order), `nx2/make_game.py` (copies `hot_order.txt` and `hot_funcs.txt` into a build, and sets
`A64_HOT_FUNCS`), `scripts/hotlist.py` (a hot list from an all-thread sample),
`nx2/split_hot.py` (`code_hot.c` in pieces), `scripts/crit.py` (the
critical path)
**See also:** [jobs.md](jobs.md) (the job phase and its critical path), [actors.md](actors.md) (the five per-actor jobs),
[rendering.md](rendering.md), [frame-time.md](frame-time.md), and this batch's pages [drawing.md](drawing.md), [nvn-calls.md](nvn-calls.md), [physics.md](physics.md), [animation.md](animation.md) and [ai.md](ai.md)

## In short

1. **Havok is the largest share of the list:** 66 of the top 200 (33%) are Havok code: Physics 37, Cloth 22, AI
   navigation 7. Physics sits low (its median rank is 142); Cloth starts at rank 19 and has 7 of the top 50. **read**
   (the assignment; the shares are of functions, not of time)
2. **The top 50 is rendering, threads, actors and cloth:** the CPU's render work 11, the thread pools 8, the actor
   update 8, cloth 7. The first seven are the job threads' entries and the two pools' loops, which count waiting as well
   as work (*What the list is*). **read**
3. **The stage slot jobs run render work.** The slot jobs' run, `f_80083960` (slot 2 of the no-op job vtable
   `0x84c36c88`), carries the labels `agl::lyr::Renderer` / `begin` / `end` and calls `f_800998a0`, `f_80084de0`,
   `f_8006cd60`, `f_800738e0`, `f_8007e200` and `f_8007f340` (the effects' calc) directly; `f_80080000`, which follows the
   pool B loop, has the same labels and calls. Three of those are on [jobs.md](jobs.md)'s critical path (`f_8006cd60`
   13.5% of it, `f_800998a0` 8%), and they issue NVN commands (command lists, blend state, command buffers' recording).
   So the ModuleSystem's stage slots record the frame's draw commands and run the effects. **read** (the calls and
   labels), **guess** (that this is all the slots do)
4. **A critical-path job is the sound module's.** `f_80435300` (`ModuleJob_Misc` in [jobs.md](jobs.md), 3.7% of the
   chain, rank 82) lies inside `f_804352d0`, which is slot 2 of the SoundModule's vtable `0x84c92c48`: the module calc job
   runs it. **read**
5. **Effects are hot and have no page:** nn::vfx3 and the game's effect assets make 11 of the 200, from rank 34, run from
   the render slots. **read** (the calls), **guess** (vfx3 by address range)
6. **Still unexplained:** 20 functions with no clear owner (the highest at rank 23) and 10 in a library with no strings
   at 0x814ee000–0x81502000, called from actor code and from beside a render job (*Unexplained*).

## The share by subsystem

| Subsystem | Top 50 | Top 200 | Page |
|---|---|---|---|
| Havok Physics | 3 (6%) | 37 (18%) | [physics.md](physics.md) |
| Rendering (CPU) | 11 (22%) | 23 (12%) | [drawing.md](drawing.md) |
| Havok Cloth | 7 (14%) | 22 (11%) | [physics.md](physics.md) |
| Unexplained | 2 (4%) | 20 (10%) | not documented |
| Actor update | 8 (16%) | 18 (9%) | [actors.md](actors.md) |
| Effects | 1 (2%) | 13 (6%) | [effects.md](effects.md) |
| Unidentified library | 0 (0%) | 10 (5%) | not documented |
| Animation and skeleton | 2 (4%) | 9 (4%) | [animation.md](animation.md) |
| Threads and jobs | 8 (16%) | 9 (4%) | [jobs.md](jobs.md) |
| AI and NPCs | 3 (6%) | 8 (4%) | [ai.md](ai.md) |
| Sound | 0 (0%) | 8 (4%) | [audio.md](audio.md) |
| Havok AI (navigation) | 0 (0%) | 7 (4%) | [navigation.md](navigation.md) |
| Frame loop | 3 (6%) | 6 (3%) | [jobs.md](jobs.md) |
| Runtime | 0 (0%) | 4 (2%) | not documented |
| Camera (guess) | 0 (0%) | 2 (1%) | [camera.md](camera.md) |
| Actor LOD and calc skip | 1 (2%) | 2 (1%) | [animation.md](animation.md) |
| Culling | 0 (0%) | 1 (0%) | [culling.md](culling.md) |
| Player | 1 (2%) | 1 (0%) | [player.md](player.md) |

These are counts of functions, not of time: the list gives an order and no sample counts, so a function at rank 10 can
cost many times one at rank 150. Of the 200, 82 have a row on a page: 39 on [physics.md](physics.md), 21 on
[jobs.md](jobs.md), 12 on [animation.md](animation.md), 6 on [actors.md](actors.md), one each on
[player.md](player.md), [graphics.md](graphics.md), [frame-time.md](frame-time.md) and [culling.md](culling.md). The
*Page* column says "(row)" where the function has one; `drawing.md` and `ai.md` were not on disk when this page was
written, so their functions point at the page name.

Three things dominate:

- **Havok** (66). Physics' contact solver, narrow phase, broad phase and queries; Cloth's simulate, collide-and-solve,
  constraints and skinning operators; navigation's collision avoidance and boundary searches. They run as tasks on the
  BaseProcExecutors (through pool A's loop, `f_8004ab40` → `f_80049280` → `f_80049f10`) and, for the character step, on
  `NavMeshCAStepDepWorker1` ([jobs.md](jobs.md)).
- **The render work on the CPU** (25, and 11 of the top 50): the stage slot jobs above, the passes' recording
  (`f_80660320`), the model draw that 263 functions call (`f_8008fc00`), the render targets' allocator (`f_8005ed00`,
  rank 54), and functions that write GPU buffers.
- **The actor update** (18 here, plus AI 8, animation 9, the LOD module 2 and the player's): GameActor's slots 50 to 56 and the jobs that run
  them ([actors.md](actors.md)).

## What the list is

- `hot_order.txt` holds 3,682 lifted function names, hottest first, made on the development PC from sampled profiles of
  play and copied into each build (`nx2/make_game.py`, from `work/`; on the PC looked at, in
  `%LOCALAPPDATA%\LongshotBuilder\`). `nx2/build.py`'s `HOT_ORDER` gives it to the linker (`--symbol-ordering-file` on
  Linux, `/order:` on Windows), so the hot functions lie together in the executable; `RX_NO_ORDER=1` turns that off.
  The order changes where the code lies, not what it does.
- `hot_funcs.txt` (849 names) is a different list: the functions compiled with the hot flags (`-O2`, the FP helpers'
  inline fast paths, together in `code_hot.c`). 165 of the top 200 and 39 of the top 50 are on it, and 78 of its names
  are not in `hot_order.txt` at all, so the two came from different profiles.
- **How it ranks.** The script that wrote `hot_order.txt` is not in the repository. Its order shows two things:
  - a function can rank above its only direct caller (`f_808226e0` at 23 and its only caller `f_80822260` at 187;
    `f_81b8c000` at 26, `f_81b5a3c0` at 50), so a rank is time spent in the function itself, not in what it calls.
    `scripts/hotlist.py` counts the same way, charging each sample to its innermost lifted function;
  - the top seven are the thread entries and the pools' loops, where idle threads spin and sleep. In
    `scripts/hotlist.py`'s counting a sample in the host's runtime (spinning, sleeping, a lock) counts for the lifted
    function under it ([jobs.md](jobs.md) lists `spin_until_set` and `hos_sleep_ns` among the blocked threads' leaves),
    so a high rank there is waiting.
- **Names are the lifter's function starts.** A few lie inside what the BL targets make one function: `f_81b47080` in
  `f_81b47078`, `f_81785c60` in `f_81785c00`, `f_81db9a40` in `f_81db9a00`, `f_818623a0` in `f_81861e40`, `f_806602e0`
  in `f_80660010`, `f_806e3180` in `f_806e3040`, `f_80435300` in `f_804352d0`, `f_80706be0` in `f_80706ba0`,
  `f_834a2e50` in `f_834a2e30`. The rows on the pages use the lifter's names.

### Its limits

- **One machine's play.** Which scenes, for how long and on which PC is not recorded with the list. The measurements in
  [jobs.md](jobs.md) (Temple of Time and Kokiri Forest, a Ryzen 9 3900X) are separate profiles.
- **Absent is not cold.** What a play profile seldom samples is missing or low: the Zstd decoder runs while areas load
  (`scripts/hotlist.py` adds it by hand). Job 4's run `f_800b9420` (3,480 instructions) and the slot jobs' run
  `f_80083960` are not on the list at all.
- **Host code is charged to its caller.** The FP exactness helpers (about 6% of the running time in [jobs.md](jobs.md)'s
  Kokiri run) and the host's nn:: calls count for the game function that calls them.
- **Order, not cost.** No sample counts, and the frame's time is its critical path, not the sum of the list
  ([jobs.md](jobs.md)): cloth spread over six executors can cost the frame less than a cooler job on the chain.

## How each was assigned

The *Basis* column says what the assignment rests on:

- **row**: a page has its row (its name and meaning are that page's);
- **str**: the function's own strings: Havok's timer labels (`TtSolve`, `TtCloth`…) and its function signatures,
  parameter names, labels such as `Model(...)`;
- **callers**, **callees**: theirs, up to a few calls away (an actor slot, the render slot run, the skeleton update…);
- **vt**: a vtable slot: GameActor `0x84b45e18`, ActorBase `0x84c7b7a8`, the actor jobs' functors `0x84c7bed8` to
  `0x84c7bf78`, the SoundModule `0x84c92c48`, hknpMeshShape `0x84cbfbe0`, Havok's broad phase `0x848da718`;
- **range**: the address range. Havok runs from about 0x81680000 to 0x82140000: its base, then Physics (hknp, to about
  0x81ac0000), Cloth (hcl, 0x81b00000–0x81de0000) and AI (hkai, 0x81e00000–0x82140000). nn::vfx3 is at
  0x81250000–0x812f0000 and 0x81330000–0x81470000 (and a little at 0x814e0000), NintendoWare's ui at
  0x81570000–0x81610000, the sdk from 0x84fa0000;
- **NVN**: it loads NVN procs' pointers (the GOT slots the game's loader fills);
- **import**: an import's GOT slot holds it (`memset`, `sinf`, the sdk's memcpy);
- **nb**: a neighbour's strings.

An assignment from callers, ranges or neighbours alone is a guess; the *What it is* column marks the larger guesses.

## The top 200

### Ranks 1 to 50

| Rank | Function | Subsystem | What it is | Basis | Page |
|---|---|---|---|---|---|
| 1 | `f_80df2680` | Threads and jobs | `SeadThread_Run`: calls a thread's run slot. Idle sead::Threads' 100 ms sleeps return to 0x80df26c0, inside it, so its rank is mostly waiting | row | [jobs.md](jobs.md) (row) |
| 2 | `f_80df25c0` | Threads and jobs | Slot 13 of both pools' worker vtables (`0x84c53310`, `0x84c533a0`): calls `f_80df2680` | vt | [jobs.md](jobs.md) |
| 3 | `f_834a2e50` | Threads and jobs | `SeadThread_Entry`, the entry under every job thread | row | [jobs.md](jobs.md) (row) |
| 4 | `f_8004ab40` | Threads and jobs | `PoolA_WorkerLoop`: the BaseProcExecutors' scheduler; its rank includes their waiting | row | [jobs.md](jobs.md) (row) |
| 5 | `f_8007fe60` | Threads and jobs | `PoolB_SchedulerLoop`: the ModuleSystem workers' and the main thread's scheduler; includes their waiting | row | [jobs.md](jobs.md) (row) |
| 6 | `f_8004aae0` | Threads and jobs | `PoolAWorker_Run` | row | [jobs.md](jobs.md) (row) |
| 7 | `f_8007fe10` | Threads and jobs | `PoolBWorker_Run` | row | [jobs.md](jobs.md) (row) |
| 8 | `f_8009bb80` | Rendering (CPU) | 50 KB, no strings, under the render slot work `f_800998a0`; loads the NVN memory-pool and buffer-builder procs (`nvnMemoryPoolFlushMappedRange`, `nvnBufferBuilderSetStorage`): fills GPU buffers. animation.md calls it the model's calc, reading the skeletons' world matrices | callers, NVN | [drawing.md](drawing.md) (row) |
| 9 | `f_80021ca0` | Actor LOD and calc skip | `LODModule_Worker` on animation.md: the LOD module's parallel worker (its module vtable `0x84c84ac0` names itself "LOD"). Per actor it reads the main rigid body's transforms (`mLastTransform`, `mPrevTransform`), fills and reads back the actor's occlusion sphere query, and sets the calc-skip intervals of the actor's jobs | row | [animation.md](animation.md) (row) |
| 10 | `f_812342c0` | Rendering (CPU) | 7.6 KB, a slot of the large vtable region at `0x84c2f530` (whose slot 88 names ModelSceneDependency); flushes mapped GPU memory. Shares its helpers (`f_834bc250`, `f_834bc460`) with ranks 95 and 165 | vt, NVN | [drawing.md](drawing.md) |
| 11 | `f_800998a0` | Rendering (CPU) | `ModuleJob_RunWithPoll`: called directly from the stage slot job's run (`f_80083960`, labelled `agl::lyr::Renderer` begin/end) | row, callers | [jobs.md](jobs.md) (row) |
| 12 | `f_82c3fab0` | Actor update | `GameActor_Calc52`: the actors' main update | row | [actors.md](actors.md) (row) |
| 13 | `f_800b3ce0` | Actor update | `BaseProcJob_Run`: job 1's run (slot 2 of `0x84c7bf00`) | row | [jobs.md](jobs.md) (row) |
| 14 | `f_82c3d740` | Actor update | GameActor slot 51 (job 0's second call), the string `CommandUseIK`; calls the skeleton update and the AI command `f_800eb9c0` | vt, str | [animation.md](animation.md) (row) |
| 15 | `f_800e98c0` | AI and NPCs | Recursive; 28 callers, most of them at 0x800dd000–0x800ef000, nine naming `%s.ain` (the AI graph files). **guess**: evaluates an AI graph's nodes | callers | [ai.md](ai.md) (row) |
| 16 | `f_80084de0` | Rendering (CPU) | `ModuleJob_Dispatch`: render slot work; calls command lists and sets blend and colour state through NVN | row, NVN | [jobs.md](jobs.md) (row) |
| 17 | `f_82c415e0` | Player | `Player_UpdateMovement` | row | [player.md](player.md) (row) |
| 18 | `f_800cdbe0` | Actor update | `ActorBase_vt54` | row | [actors.md](actors.md) (row) |
| 19 | `f_80168320` | Havok Cloth | The game's cloth code: calls Havok Cloth's step `f_8016b620`; its neighbour `f_8016a8b0` holds the message "Did not set the previous transfer motion transform…" | callees, range | [physics.md](physics.md) (row) |
| 20 | `f_8016b620` | Havok Cloth | `hclWorld::stepSingleThreaded` ("TtCloth"), run by the cloth task `f_8016b320` | str | [physics.md](physics.md) (row) |
| 21 | `f_81b47080` | Havok Cloth | Inside `hclSimulateOperator::_execute` (`f_81b47078`) | str | [physics.md](physics.md) (row) |
| 22 | `f_80070b20` | Threads and jobs | `PoolB_WaitPhase` | row | [jobs.md](jobs.md) (row) |
| 23 | `f_808226e0` | Unexplained | No strings; ticks, memset, memcpy. Its only caller is `f_80822260` (rank 187), under the render slot work `f_800998a0`; beside it functions naming `%s.ain` and ActionSchedule | callers | not documented |
| 24 | `f_8006e160` | Rendering (CPU) | Called from `f_8006cd60` ("Post"); yields, and calls the barrier issuer `f_80a47660` | callers | [drawing.md](drawing.md) (row) |
| 25 | `f_802eacc0` | Frame loop | `CalcTree_Node` | row | [jobs.md](jobs.md) (row) |
| 26 | `f_81b8c000` | Havok Cloth | 70 KB, under Havok Cloth's `_iterateCollidables` (collide and solve) | callers | [physics.md](physics.md) (row) |
| 27 | `f_800d43d0` | Actor update | 26 KB, from GameActor slot 54 and from an NPC function (`f_80406a70`: NpcChase, IsWaitingAtRailPoint, KokkoParam); calls a function naming `%s_EyeOuter` | callers | [ai.md](ai.md) (row) |
| 28 | `f_8006cd60` | Rendering (CPU) | `ModuleJob_Post`: initialises, records and finalises command buffers (`nvnCommandBufferInitialize`, `EndRecording`, `Finalize`) | row, NVN | [jobs.md](jobs.md) (row) |
| 29 | `f_80186a00` | Havok Cloth | Calls the cloth wrapper `f_80168320`; strings `target_mtx`, `target_position` in its callees; from slot 52, Link's movement and the job `f_802ca820`. **guess**: an actor's cloth update | callees | [physics.md](physics.md) (row) |
| 30 | `f_81785c60` | Havok Physics | Inside `f_81785c00`, called by `hknpStreamContactSolver::solveJacobians` (rank 78) | callers | [physics.md](physics.md) (row) |
| 31 | `f_82ef62f0` | AI and NPCs | Slot 6 of `0x84b747e0`, whose other slots name Fly, IsMovementOrbital, IsSpawnNearCamera, IsOrbitPlayer: **guess**, an ambient creature group's update | vt | [ai.md](ai.md) (row) |
| 32 | `f_800a8400` | Animation and skeleton | `Shape_BoundsFromBones` on animation.md: a shape's bounding sphere from its bone spheres (sqrtf); called from the render job `f_8009bb80`, model code and slot 54's path | row | [animation.md](animation.md) (row) |
| 33 | `f_8003c440` | Unexplained | Slot 108 of `0x84b39e50`, beside a function reading `NSurface/AfterDofAutoSwitchDist` | vt | not documented |
| 34 | `f_8125f0e0` | Effects | In nn::vfx3 (0x81250000–0x812f0000); under `f_8007f340`, the render dispatcher's effects call | range, callers | [effects.md](effects.md) (row) |
| 35 | `f_8008fc00` | Rendering (CPU) | 263 callers, among them many `Model(...)` passes, the local shadows' depth, fog and snapshot passes: binds textures (`nvnCommandBufferBindTexture`) and calls command lists. **guess**: draws a list of models | callers, NVN | [drawing.md](drawing.md) (row) |
| 36 | `f_818623a0` | Havok Physics | Inside `f_81861e40`, called from `hknpCompoundShape::updateInstances` | callers | [physics.md](physics.md) (row) |
| 37 | `f_80a47660` | Rendering (CPU) | Issues barriers (`nvnCommandBufferBarrier`, five places), under "Post" | NVN, callers | [drawing.md](drawing.md) (row) |
| 38 | `f_800b73d0` | Actor update | `ActorJob0_Run` | row | [actors.md](actors.md) (row) |
| 39 | `f_800c0920` | Actor update | A job functor's run (slot 2 of `0x84c794e0`) calling the actor jobs' helpers (`f_800b9230`, `f_800bf550`, `f_800bf7d0`, `f_800c2800`): another kind of BaseProc job | vt, callees | [actors.md](actors.md) |
| 40 | `f_81c7f880` | Havok Cloth | Under `hclBendStiffnessConstraintSet::apply` | callers | [physics.md](physics.md) (row) |
| 41 | `f_80660320` | Rendering (CPU) | Labels `Model(IndirectOpaAfterOpa)`, `Model(IndirectXluAfterOpa)`; waits on and signals GPU events (`nvnCommandBufferWaitEvent`, `SignalEvent`); calls functions that allocate render targets. A render pass's recording | str, NVN | [drawing.md](drawing.md) (row) |
| 42 | `f_81a20b40` | Havok Physics | Havok's broad phase (called from `f_81a20780`, slot 26 of the broad phase's vtable `0x848da718`) | vt | [physics.md](physics.md) (row) |
| 43 | `f_8003dac0` | Animation and skeleton | atan2f; under the skeleton update four calls down. **guess**: bone maths | callers | [animation.md](animation.md) (row) |
| 44 | `f_806602e0` | Rendering (CPU) | Inside the bounds of `f_80660010` (`Ssao_MakeWork`, row in rendering.md); called from `f_80660320` | row nearby | [drawing.md](drawing.md) |
| 45 | `f_82c43780` | Actor update | GameActor slot 53 (job 2): calls slot 54, the AI graph lookup `f_800e7780`, and a function naming Link's actions (Wall, Ladder, BackFlip…) | vt, callees | [actors.md](actors.md) |
| 46 | `f_80076cd0` | Rendering (CPU) | Slot 11 of `0x84c847f0`, whose slot 6 reads `p_dynamic_resolution_ratio` | vt | [drawing.md](drawing.md) |
| 47 | `f_800eb9c0` | AI and NPCs | Strings `OriginalActionVerbArg`, `AdjustedActionVerbArg`, `%s.ain`; from slot 51: an actor's AI action command | str | [ai.md](ai.md) (row) |
| 48 | `f_802eabd0` | Frame loop | `CalcTree_Run` | row | [jobs.md](jobs.md) (row) |
| 49 | `f_8348c680` | Frame loop | `Framework_RunFrame` | row | [jobs.md](jobs.md) (row) |
| 50 | `f_81b5a3c0` | Havok Cloth | Under `_iterateCollidables` | callers | [physics.md](physics.md) (row) |

### Ranks 51 to 100

| Rank | Function | Subsystem | What it is | Basis | Page |
|---|---|---|---|---|---|
| 51 | `f_81b56de0` | Havok Cloth | `hclSimulateOperator::_solveTaskOrdered` ("TtSolve") | str | [physics.md](physics.md) (row) |
| 52 | `f_8049dd00` | Unexplained | Called from `f_80494700` (rank 141, "Dm_SY_Ocarina") and `f_80494300` | callers | not documented |
| 53 | `f_820e2520` | Havok AI (navigation) | Under Havok AI's `solveSingleAgent` (collision avoidance) | callers | [physics.md](physics.md) (row) |
| 54 | `f_8005ed00` | Rendering (CPU) | `DynamicTextureAllocator_Alloc`: the render targets' allocator, with 106 callers | row | [graphics.md](graphics.md) (row) |
| 55 | `f_81260600` | Effects | nn::vfx3, from `f_81270c20` | range | [effects.md](effects.md) (row) |
| 56 | `f_8014ba80` | Actor update | ActorBase slot 50, the base of GameActor's slot 50; its callees name `next_rot`, `next_position`, `next_angular_velocity` | vt | [actors.md](actors.md) |
| 57 | `f_809667b0` | Frame loop | `Framework_Frame` | row | [jobs.md](jobs.md) (row) |
| 58 | `f_8575cdc0` | Runtime | `__memcpy_aarch64` (the sdk's memcpy) | import | not documented |
| 59 | `f_8035a8c0` | Camera (guess) | tanf; 15 callers, several of which pass the name "Main" to `f_8035a0f0`. **guess**: a camera's projection | callers | [camera.md](camera.md) |
| 60 | `f_80130600` | Effects | The game's effect assets (`getMtxSetType`, `getAssetBitFlag_`, `getGroupName`), called from the render dispatcher `f_80080000` | str, callers | [effects.md](effects.md) (row) |
| 61 | `f_81db9a40` | Havok Cloth | `hclObjectSpaceSkinPOperator` (cloth skinning) | str | [physics.md](physics.md) (row) |
| 62 | `f_82c3bbb0` | Actor update | GameActor slot 50 (job 0's first call) | vt | [animation.md](animation.md) (row) |
| 63 | `f_81a4a540` | Havok Physics | Slot 23 of hknpMeshShape's vtable `0x84cbfbe0` | vt | [physics.md](physics.md) (row) |
| 64 | `f_802ca820` | AI and NPCs | A job functor's run (slot 2 of `0x84b463e0`), string `ServiceWait`, calling much of slot 52's work (skeleton, cloth, IK); `game__npc__` parameters beside it. **guess**: an NPC's update | vt, str | [ai.md](ai.md) |
| 65 | `f_8572f300` | Runtime | `memset` (the sdk's) | import | not documented |
| 66 | `f_81c86720` | Havok Cloth | `hclStandardLinkConstraintSet::apply` | str | [physics.md](physics.md) (row) |
| 67 | `f_81e69500` | Havok AI (navigation) | `hkaiNavMeshUtils::getNearbyBoundaries` (flood) | str | [physics.md](physics.md) (row) |
| 68 | `f_81501180` | Unidentified library | Inside `f_81500ff0`, no strings | range | not documented |
| 69 | `f_800955a0` | Rendering (CPU) | Slot 11 of `0x84c324d0` (whose slot 2 names `gsys_geometry_culling`): calls the model draw `f_8008fc00` and command lists | vt, NVN | [drawing.md](drawing.md) (row) |
| 70 | `f_800e9800` | AI and NPCs | A wrapper for the AI node evaluation `f_800e98c0` (vtable `0x84c493a8`) | callees | [ai.md](ai.md) (row) |
| 71 | `f_800bf7d0` | Actor update | A helper of actor jobs 1 and 4 and slots 54 and 56 | callers | [actors.md](actors.md) |
| 72 | `f_812707c0` | Effects | nn::vfx3, from `f_81270540` | range | [effects.md](effects.md) (row) |
| 73 | `f_800c2800` | Actor update | ActorBase slot 35, called from the actor jobs | vt | [actors.md](actors.md) |
| 74 | `f_8007f340` | Effects | From the render dispatcher `f_80080000`; calls into nn::vfx3 (`f_81270540`, `f_81262600`, …): the effects' calc | callers, callees | [effects.md](effects.md) (row) |
| 75 | `f_83b873c0` | Animation and skeleton | From GameActor slot 50; beside a function naming `DisableBoneModifier`, `BoneModifier`, `BoneModifierPostIK` | callers, nb | [animation.md](animation.md) (row) |
| 76 | `f_801d99a0` | Animation and skeleton | 58 KB, string `UseRotatedTranslate`; under the job `f_802ca820` and the skeleton update. **guess**: animation | str | [animation.md](animation.md) (row) |
| 77 | `f_81270540` | Effects | nn::vfx3 (fmodf, ticks), from `f_8007f340` | range | [effects.md](effects.md) (row) |
| 78 | `f_8177b2a0` | Havok Physics | `hknpStreamContactSolver::solveJacobians` | str | [physics.md](physics.md) (row) |
| 79 | `f_80364080` | Rendering (CPU) | From the render pass `f_80660320`; locks and sleeps (`SleepThread`) | callers | [drawing.md](drawing.md) (row) |
| 80 | `f_807a9600` | Camera (guess) | Uses the "Main" camera, atan2, sqrt; from `LockOnCursorAsEnemy` (`f_8022ffa0`). **guess**: lock-on targeting | callers | [camera.md](camera.md) |
| 81 | `f_8125ff80` | Effects | nn::vfx3, from rank 34 | range | [effects.md](effects.md) (row) |
| 82 | `f_80435300` | Sound | `ModuleJob_Misc`: inside `f_804352d0`, the SoundModule's calc (slot 2 of `0x84c92c48`) | row, vt | [jobs.md](jobs.md) (row) |
| 83 | `f_806b0e80` | Threads and jobs | A pool A helper called from EntryJobsBgGraphics (`f_83aff840`) | callers | [jobs.md](jobs.md) |
| 84 | `f_814f9460` | Unidentified library | Inside `f_814f8f60`, called from `f_802c7420` and `f_802c6ac0` (actor code) | range | not documented |
| 85 | `f_81730760` | Havok Physics | Havok Physics, under the particle contact solver and casts | callers | [physics.md](physics.md) (row) |
| 86 | `f_81500200` | Unidentified library | Inside `f_814ffe20`, called from `f_8009b8a0` beside the render job `f_8009bb80` | range | not documented |
| 87 | `f_806f5860` | Sound | 28 KB of maths (pow, exp, atan2, acos), called from `searchAndEmit(%s)` / `getSoundHandle` and `getAliveAssetExecutor`: **guess**, sound links' parameter curves | callers | [audio.md](audio.md) |
| 88 | `f_80ba6d80` | Unexplained | Called from a function naming `Skl_Root` and one naming `XLinkVariationId` | callers | not documented |
| 89 | `f_80162aa0` | Havok Cloth | The cloth wrapper's range; from `f_80168320` and Link's movement | range | [physics.md](physics.md) |
| 90 | `f_802c7420` | Actor update | Called from 20 actor functions (HS, horse, Fly, slot 52…); calls the unidentified library (`f_814f8f60`, …) | callers | [actors.md](actors.md) |
| 91 | `f_81b38800` | Havok Cloth | `hclSimClothInstance::updateAabbs` | str | [physics.md](physics.md) (row) |
| 92 | `f_81746440` | Havok Physics | Havok Physics (0x8174…), reached from `hkTask::processTask` | range | [physics.md](physics.md) |
| 93 | `f_81a465e0` | Havok Physics | A shape query (calls `castShape`) | callees | [physics.md](physics.md) (row) |
| 94 | `f_81fbb000` | Havok AI (navigation) | A static tree walk in Havok AI's range, under `StaticTree::iterateAlongRay` | callers, range | [navigation.md](navigation.md) |
| 95 | `f_83647b20` | Rendering (CPU) | Like rank 10 (same helpers, vtable region `0x84c2f530` slot 319), flushes mapped GPU memory | vt, NVN | [drawing.md](drawing.md) |
| 96 | `f_81cdff40` | Havok Cloth | Under the four `hclObjectSpaceSkin` operators | callers | [physics.md](physics.md) |
| 97 | `f_802b9900` | Animation and skeleton | Rotation maths (sin, acos, atan, sqrt); from slots 51 and 52 and the job `f_802ca820`. **guess** | callers | [animation.md](animation.md) (row) |
| 98 | `f_80341840` | Animation and skeleton | Trigonometry and pow, under the skeleton update. **guess**: IK | callers | [animation.md](animation.md) (row) |
| 99 | `f_81928de0` | Havok Physics | `mergeAndCollide2StreamsIntern` (narrow phase) | str | [physics.md](physics.md) (row) |
| 100 | `f_80706be0` | Frame loop | `ModuleSystem_ModuleCalc` (the lifter's start; `f_80706ba0` by BL targets) | row | [frame-time.md](frame-time.md) (row) |

### Ranks 101 to 150

| Rank | Function | Subsystem | What it is | Basis | Page |
|---|---|---|---|---|---|
| 101 | `f_800b5400` | Actor update | Slot 6 of `0x84b457f8`; its callees name `%s.ain` and `Reference` | vt | [ai.md](ai.md) (row) |
| 102 | `f_81864820` | Havok Physics | Havok, under `hkTask::processTask` | callers | [physics.md](physics.md) |
| 103 | `f_8121d820` | Havok Physics | The game's ray cast, beside `hknpShapeQueryInterface::castRay` | nb | [physics.md](physics.md) |
| 104 | `f_8018dba0` | Animation and skeleton | `Skeleton_Update`: helper and support bones, cloth, look IK | row | [actors.md](actors.md) (row) |
| 105 | `f_81b57a80` | Havok Cloth | `_iterateCollidables` | str | [physics.md](physics.md) (row) |
| 106 | `f_80ba37a0` | Unexplained | 13.8 KB, slot 94 of `0x84b41960` and two other vtables | vt | not documented |
| 107 | `f_806eefc0` | Sound | Like rank 87 (same callees) | callers | [audio.md](audio.md) |
| 108 | `f_8008d640` | Rendering (CPU) | Slot 162 of `0x84c29868`; command lists and blend state | vt, NVN | [drawing.md](drawing.md) (row) |
| 109 | `f_8030b390` | Unexplained | From slot 52 and Link's movement; calls `f_8030c240` | callers | not documented |
| 110 | `f_81a20780` | Havok Physics | Broad phase (slot 26 of `0x848da718`) | vt | [physics.md](physics.md) (row) |
| 111 | `f_804ea3e0` | Havok Physics | The game's shape-cast query: string `pos:%@ normal:%@ hit_body:%s` | str | [physics.md](physics.md) |
| 112 | `f_800c34e0` | Actor update | `GameActor_vt56` | row | [actors.md](actors.md) (row) |
| 113 | `f_81a55380` | Havok Physics | Havok Physics (mesh shapes' range) | range | [physics.md](physics.md) (row) |
| 114 | `f_8016b320` | Havok Cloth | `BaseProcJob_Small`: Havok Cloth's task | row | [jobs.md](jobs.md) (row) |
| 115 | `f_80128d70` | Effects | The effect assets' getters: `getEmissionRate`, `getLifeScale`, `getAlpha`, `EFT_ERR[assetExecutorCalced(%s)]` | str | [effects.md](effects.md) (row) |
| 116 | `f_818f4f28` | Havok Physics | `hknpWorld::castRay` | str | [physics.md](physics.md) (row) |
| 117 | `f_81c66a60` | Havok Cloth | `hclSimulateOperator::_integrateTask` | str | [physics.md](physics.md) (row) |
| 118 | `f_81b58720` | Havok Cloth | `hclSimulateOperator::_collideAndSolveTask` | str | [physics.md](physics.md) (row) |
| 119 | `f_819b8e20` | Havok Physics | `gatherSolverVelocities` | str | [physics.md](physics.md) (row) |
| 120 | `f_80435860` | Sound | From the SoundModule's calc; calls `f_836f1440` | callers | [audio.md](audio.md) |
| 121 | `f_814f6c60` | Unidentified library | Slot 154 of `0x8489a3d8`; powf | vt | not documented |
| 122 | `f_83af59c0` | Actor update | `ActorJob2_Run` | row | [actors.md](actors.md) (row) |
| 123 | `f_820de560` | Havok AI (navigation) | Between the character step and `solveSingleAgent` | callers | [navigation.md](navigation.md) (row) |
| 124 | `f_820da77c` | Havok AI (navigation) | `HkaiCharacterStep_Task` | row | [jobs.md](jobs.md) (row) |
| 125 | `f_814f0620` | Unidentified library | No strings | range | not documented |
| 126 | `f_804f5030` | Havok Physics | Strings `next_rot`, `next_position`, beside the game's Havok queries (0x804e–0x804f). **guess** | str, range | [physics.md](physics.md) (row) |
| 127 | `f_831e65d0` | Actor update | Slot 54's path; calls the maths helper `f_800a8400` | callers | [actors.md](actors.md) |
| 128 | `f_800e7780` | AI and NPCs | `%s.ain`, 13 callers (slot 52, Link's movement…): looks up an actor's AI graph | str | [ai.md](ai.md) (row) |
| 129 | `f_81c7bd00` | Havok Cloth | Under `hclLocalRangeConstraintSet` | callers | [physics.md](physics.md) |
| 130 | `f_808ecd80` | Unexplained | No strings, three callers | - | not documented |
| 131 | `f_800c6940` | Actor update | A helper of slots 54 and 56 | callers | [actors.md](actors.md) |
| 132 | `f_85786920` | Runtime | `AtomicNotify` (libc++'s) | row | [jobs.md](jobs.md) (row) |
| 133 | `f_800738e0` | Rendering (CPU) | Render slot work: yields and sleeps | callers | [drawing.md](drawing.md) |
| 134 | `f_85751760` | Runtime | `sinf` (the sdk's) | import | not documented |
| 135 | `f_8007e200` | Rendering (CPU) | Render slot work | callers | [drawing.md](drawing.md) |
| 136 | `f_8027d040` | Effects | The effects' per-pass view set-up, under `f_8027c9e0` (rank 161) | callers | [effects.md](effects.md) (row) |
| 137 | `f_80733be0` | Unexplained | Render slot work five calls down; sqrtf, memset | callers | not documented |
| 138 | `f_80364ee0` | Rendering (CPU) | Calls only memset; two calls below the cube map pass (`CubeMap(Opa)`) and readers of `p_dynamic_resolution_ratio` | callers | [drawing.md](drawing.md) |
| 139 | `f_83b74d30` | Actor LOD and calc skip | `LODModule_Calc` on animation.md: the LOD module's calc (slot 2 of `0x84c84ac0`); sin, cos, tan; runs the worker, rank 9 | row | [animation.md](animation.md) (row) |
| 140 | `f_81a012e0` | Havok Physics | Slot 4 of hknpMeshShape's vtable | vt | [physics.md](physics.md) |
| 141 | `f_80494700` | Unexplained | String `Dm_SY_Ocarina`; calls functions naming map names (HyruleField_Adult, KakarikoVillage, Dungeon) and `depth valid` | str | not documented |
| 142 | `f_80049f10` | Havok Physics | Runs Havok tasks from pool A's loop (through `f_80049280`); reached from PhysicsSystemBaseProc and phive's managers | callers | [physics.md](physics.md) |
| 143 | `f_80150180` | Havok Physics | Slot 0 of `0x84c56840`; `next_linear_velocity`, `next_position`: **guess**, rigid-body motion | str | [physics.md](physics.md) |
| 144 | `f_800e63c0` | AI and NPCs | `%s.ain`, slot 30 of `0x84c48c98` | str | [ai.md](ai.md) (row) |
| 145 | `f_80049280` | Havok Physics | Recursive, from pool A's loop: the Havok task walk above rank 142 | callers | [physics.md](physics.md) |
| 146 | `f_8191ee00` | Havok Physics | `HknpNarrowPhase_Task` | row | [jobs.md](jobs.md) (row) |
| 147 | `f_81c7d2e0` | Havok Cloth | `hclBendLinkConstraintSet::apply` | str | [physics.md](physics.md) (row) |
| 148 | `f_8073a4a0` | Unexplained | Slot 2 of `0x84b6e4b0`; tanf | vt | not documented |
| 149 | `f_80435bc0` | Rendering (CPU) | String `Deformable`; binds textures (`nvnCommandBufferBindTexture`, 58 places) | str, NVN | [drawing.md](drawing.md) |
| 150 | `f_8002b300` | Rendering (CPU) | Called by seven readers of `p_dynamic_resolution_ratio`: a render parameter lookup | callers | [drawing.md](drawing.md) |

### Ranks 151 to 200

| Rank | Function | Subsystem | What it is | Basis | Page |
|---|---|---|---|---|---|
| 151 | `f_81281850` | Effects | nn::vfx3 (slot 103 of `0x8488ad08`; an emitter's gravity parameters beside it) | vt, range | [effects.md](effects.md) (row) |
| 152 | `f_81a21aa0` | Havok Physics | Broad phase (slot 27 of `0x848da718`) | vt | [physics.md](physics.md) (row) |
| 153 | `f_819783e0` | Havok Physics | Havok Physics (collision caches) | callers | [physics.md](physics.md) |
| 154 | `f_814f5260` | Unidentified library | Slot 1 of `0x84cb4e60` | vt | not documented |
| 155 | `f_819ba420` | Havok Physics | Solving constraints' Jacobians | str | [physics.md](physics.md) (row) |
| 156 | `f_8030c240` | Unexplained | From rank 109 and the job `f_802ca820` | callers | not documented |
| 157 | `f_81ce6500` | Havok Cloth | `hclSimpleMeshBoneDeformOperator` | str | [physics.md](physics.md) (row) |
| 158 | `f_8176c980` | Havok Physics | Havok Physics (vtable `0x84cbf428`) | vt, range | [physics.md](physics.md) |
| 159 | `f_8037adc0` | Havok Physics | Inside `f_8037adb0` ("no name"), beside Havok array code. **guess** | nb | [physics.md](physics.md) |
| 160 | `f_80337ae0` | Animation and skeleton | 31 KB under the skeleton update | callers | [animation.md](animation.md) (row) |
| 161 | `f_8027c9e0` | Effects | The effects' per-pass draw, called by 12 pass functions (`LocalDepthShadow::drawDepth`, snapshot, `wet_mask`) | callers | [effects.md](effects.md) (row) |
| 162 | `f_80117780` | Unexplained | From slots 53, 54 and 56; one caller names `Chemical_Fire` | callers | not documented |
| 163 | `f_800e94c0` | AI and NPCs | A wrapper for `f_800e98c0` | callees | [ai.md](ai.md) (row) |
| 164 | `f_806e3180` | Sound | Inside `f_806e3040` (`getAssetName`, `getAliveAssetExecutor failed`), beside `.bwav`: a sound link's asset | str, nb | [audio.md](audio.md) |
| 165 | `f_82bf1020` | Unexplained | Slot 31 of the NSurface vtable `0x84b3ffb8`; rank 10's helpers | vt | not documented |
| 166 | `f_81779980` | Havok Physics | Havok Physics, under the narrow phase and casts | callers | [physics.md](physics.md) |
| 167 | `f_817d9d80` | Havok Physics | Havok's solver integrator | callers | [physics.md](physics.md) (row) |
| 168 | `f_8043d720` | Rendering (CPU) | Called by the Deformable draw (rank 149) | callers | [drawing.md](drawing.md) |
| 169 | `f_80195aa0` | Animation and skeleton | Under the skeleton update; sqrtf | callers | [animation.md](animation.md) (row) |
| 170 | `f_80253750` | Unexplained | Slot 6 of `0x84c74470`, whose slots 15 and 16 are Havok's "Not implemented" | vt | not documented |
| 171 | `f_803749e0` | Havok Physics | Beside `TtBuildCollideTasks`; vtable `0x84c56878` | nb | [physics.md](physics.md) (row) |
| 172 | `f_8003d320` | Unexplained | Calls rank 43; powf; vtable `0x84c70d18` slot 4 | vt | not documented |
| 173 | `f_81501400` | Unidentified library | No strings | range | not documented |
| 174 | `f_836eadb0` | Sound | From `f_836f1440` (rank 199) | callers | [audio.md](audio.md) |
| 175 | `f_82bde830` | Culling | `PreComputedCulling_Load` | row | [culling.md](culling.md) (row) |
| 176 | `f_814efa00` | Unidentified library | Slot 85 of `0x8489a3d8` | vt | not documented |
| 177 | `f_800715e0` | Rendering (CPU) | Slot 18 of `0x84c30490`; calls the model draw `f_8008fc00` and command lists | vt, NVN | [drawing.md](drawing.md) |
| 178 | `f_81b3a558` | Havok Cloth | Beside `hclCollidable`, from `_execute` | callers | [physics.md](physics.md) |
| 179 | `f_8175e640` | Havok Physics | Havok Physics (vtable `0x84cbf648`) | vt | [physics.md](physics.md) |
| 180 | `f_81270d60` | Effects | nn::vfx3, from `f_8027c880` (render passes) | range | [effects.md](effects.md) (row) |
| 181 | `f_801f08e0` | Sound | Calls `nn::audio::IsVoiceValid` | imports | [audio.md](audio.md) |
| 182 | `f_8074c1a0` | Unexplained | Beside a function naming `ElementHeapS`, `ElementHeapL` | nb | not documented |
| 183 | `f_8182dc20` | Havok Physics | `hknpSolverIntegrator::subIntegrateLastStep` | str | [physics.md](physics.md) (row) |
| 184 | `f_80547330` | Unexplained | Inside `f_80547310` (vtable `0x84c568b0`, beside rank 143's) | vt | not documented |
| 185 | `f_820d0640` | Havok AI (navigation) | Calls `getNearbyBoundaries` and `resolvePositionOnNavMesh` | callees | [navigation.md](navigation.md) (row) |
| 186 | `f_81c7ecc0` | Havok Cloth | `hclBendStiffnessConstraintSet::apply` | str | [physics.md](physics.md) (row) |
| 187 | `f_80822260` | Unexplained | From the render slot work `f_800998a0`; calls rank 23 | callers | not documented |
| 188 | `f_814f3500` | Unidentified library | Slot 122 of `0x8489a3d8` | vt | not documented |
| 189 | `f_818f5450` | Havok Physics | `hknpWorld::castShape` | str | [physics.md](physics.md) (row) |
| 190 | `f_80040cd0` | Unexplained | Slot 27 of the NSurface vtable `0x84b3ffb8`; powf | vt | not documented |
| 191 | `f_8176a780` | Havok Physics | Havok's full cast | callers | [physics.md](physics.md) |
| 192 | `f_81270c20` | Effects | nn::vfx3 | range | [effects.md](effects.md) (row) |
| 193 | `f_81fbbe40` | Havok AI (navigation) | A static tree walk in Havok AI's range, under `StaticTree::iterateAroundPoint` | callers, range | [navigation.md](navigation.md) |
| 194 | `f_804ed080` | Havok Physics | `hknpShapeQueryInterface::queryPoint` (the game's copy, `D:/home/Project/Owl/App/Lib/Havok`) | str | [physics.md](physics.md) |
| 195 | `f_814efd20` | Unidentified library | Inside `f_814efce0` | range | not documented |
| 196 | `f_817575ec` | Havok Physics | `hknpShapeQueryInterface::castShape` | str | [physics.md](physics.md) (row) |
| 197 | `f_805621c0` | Unexplained | A reader-writer lock; reached from the actor slots | callers | not documented |
| 198 | `f_8023c820` | Havok Physics | `mLastTransform`, `next_angular_velocity`; from slot 51. **guess**: a body's motion | str | [physics.md](physics.md) |
| 199 | `f_836f1440` | Sound | Beside `AudioTvOutput`; from the SoundModule's calc | nb, callers | [audio.md](audio.md) |
| 200 | `f_802eb020` | Frame loop | `DrawTree_Run` | row | [jobs.md](jobs.md) (row) |

## Unexplained

The functions no page or string claims yet, hottest first:

- **Under the render slots, owner unknown:** `f_808226e0` (23) and its caller `f_80822260` (187), from `f_800998a0`;
  `f_80733be0` (137). Their neighbours name `%s.ain` and ActionSchedule, so they may be AI work run from the slots.
- **Actor-side, no strings:** `f_80ba6d80` (88), `f_80ba37a0` (106, three vtables), `f_8030b390` (109) and
  `f_8030c240` (156), `f_80117780` (162), `f_805621c0` (197, a reader-writer lock).
- **A cutscene or lighting system:** `f_80494700` (141, `Dm_SY_Ocarina`, map names, `depth valid`) and `f_8049dd00`
  (52).
- **NSurface vtables:** `f_8003c440` (33), `f_82bf1020` (165), `f_80040cd0` (190), and `f_8003d320` (172).
- **Others:** `f_808ecd80` (130), `f_8073a4a0` (148), `f_80253750` (170, a vtable with Havok's "Not implemented"
  slots), `f_8074c1a0` (182, beside `ElementHeapS`/`ElementHeapL`), `f_80547330` (184).
- **The library at 0x814ee000–0x81502000:** ten functions (68, 84, 86, 121, 125, 154, 173, 176, 188, 195), no strings,
  vtables at `0x8489a3d8` (slots 85, 122, 154) beside nn::vfx3's; called from actor code (`f_802c7420`, 20 callers among
  the actors) and from `f_8009b8a0`, beside the render job `f_8009bb80`. Its neighbour `f_814e52a0` names
  `nn::vfx3::expression`, so it may be vfx3's expression evaluator, though actors call it too. The effects' view set-up calls it too (`f_814e6be0`, [effects.md](effects.md)).

## Notes

- **What is on the critical path, and where it ranks.** Of [jobs.md](jobs.md)'s chain: the Havok AI character step
  `f_820da77c` rank 124, `f_800b3ce0` (actor job 1) 13, `f_8006cd60` ("Post") 28, `f_800998a0` 11, Havok Cloth's task
  `f_8016b320` 114, the sound module's `f_80435300` 82. The character step ranks low for 22% of the chain, likely because
  it is one thread's work while the others wait, and a rank counts time on all threads.
- **The LOD module is hot:** its worker `f_80021ca0` is rank 9 and its calc `f_83b74d30` rank 139 (rows on
  [animation.md](animation.md)). Per actor the worker reads the main rigid body's transforms, fills and reads back the
  actor's occlusion sphere query ([culling.md](culling.md)) and sets the calc-skip intervals of the actor's jobs
  ([actors.md](actors.md), LODParam). So deciding how often each actor updates costs about as much as one of the
  hottest render jobs. **read** (animation.md's reading), **guess** (the cost, from the rank)
- **Cloth is the deepest Havok tree here:** 22 functions from `hclWorld::stepSingleThreaded` (`f_8016b620`, 20) down to
  its operators (simulate, integrate, collide and solve, bend, stiffness, standard and local-range links, object-space
  skinning, mesh bone deform). The game's own cloth code around `f_80168320` (19) calls it from the actors' update.
- **The render targets' allocator is hot** (`f_8005ed00`, rank 54, row in [graphics.md](graphics.md)): 106
  functions call it, most of them the passes' target makers in [rendering.md](rendering.md), and its rank suggests it runs during play, not only at boot (**guess**: passes allocate their
  targets each frame).
- **Sound:** the SoundModule's calc (`f_804352d0`) leads to `f_80435860` (120), `f_836f1440` (199), `f_836eadb0` (174)
  and `f_801f08e0` (181, `nn::audio::IsVoiceValid`); the sound links' asset executors (`f_806e3180`, 164) and two large
  maths functions under `searchAndEmit` and `getSoundHandle` (`f_806f5860` 87, `f_806eefc0` 107) belong with them.
  [audio.md](audio.md) has the sound system.
- **The runtime's share is small on the list:** the sdk's memcpy (58), memset (65), sinf (134) and the atomic notify
  (132). The host's own helpers, such as the FP exactness helpers, do not show: they are charged to their callers
  (*Its limits*).

## Open questions

- Which scenes and machine the profile behind `hot_order.txt` came from, and the script that wrote it; a list with
  sample counts would turn the shares above into time.
- What the 20 unexplained functions and the library at 0x814ee000–0x81502000 are.
- Whether every stage slot runs render work, or the slots take other queued work too (the switch in `f_80083960`).
- Why job 4's run (`f_800b9420`) and the slot jobs' run (`f_80083960`) are not on the list at all.
- What `f_8009bb80`, `f_812342c0` and `f_83647b20` write into GPU memory (per-model constants or matrices, most likely).
- Whether `f_802ca820` (`ServiceWait`, a job that runs much of slot 52's work) is the NPCs' update.
