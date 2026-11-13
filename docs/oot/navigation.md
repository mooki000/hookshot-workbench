# Navigation: the Havok AI step and collision avoidance

How the game steps Havok AI each frame: the NavMeshSystem module that starts the step, the thread that runs it, the
two navigation-mesh worker pools, the collision-avoidance ("character") step and what one step does per character, and
which parts wait for which. Looked at in November 2026 by static analysis of the binary, for the frame-rate work:
[jobs.md](jobs.md) measured the Havok AI character step `f_820da77c` as the biggest single item running while the main
thread waits in the module phase (22–23% of those instants). This page reads where that step comes from, whether the
frame waits for it, and how it could be spread or thinned. [physics.md](physics.md) has Havok in general and the rows for
the navigation step `f_80781ae0` and several Havok AI functions; this page links to them rather than repeating them.

**LS code:** none yet. The virtual cores the navigation workers share with the job pools are
`host/hos_thread.c`.
**See also:** [jobs.md](jobs.md) (the pools, `JobGraph_Start`, `EntryJobsBgGraphics`, the measurements),
[physics.md](physics.md) (the Havok rows, `HkTaskJob`, `PhiveHkTask_Wrap`), [ai.md](ai.md) (the AI graphs that ask for
paths), [actors.md](actors.md) (the components, the Actor module's calc), [hot-path.md](hot-path.md) (the ranks),
[frame-time.md](frame-time.md) (the frame step)

## In short

1. **The step is started by a module and run on a task thread, and the frame waits for that task.** The NavMeshSystem
   module's calc (`f_832195f0`) stores a delegate (the navigation step, `f_80781ae0`, with the frame's step) and submits
   a task to the "SceneLowPriority" task manager (`[0x84eb74c0]`), whose second thread (priority 17, any core) runs
   it. The task registers a wait under the sync point 0xc7303adb; `EntryJobsBgGraphics` (`f_83aff840`) and the Actor
   module's calc (`f_83aff1f0`) call every wait under it before their actor work, so they block until this frame's
   navigation task has finished. **read** (the submit, the registration, the calls), **guess** (that the module's calc
   runs earlier in the same frame than those two)
2. **The character step is started from that task and nothing in the frame waits for it.** At its end the navigation
   task builds Havok AI's collision-avoidance task graph (12 tasks, `f_820d1420`) and starts it with `JobGraph_Start` on
   its own pool, **NavMeshCAStepDepWorker**: one thread, on core 1 (core mask 2, priority 22, both set in the code,
   `f_8102f530`). The next frame's task starts a new step only if that worker has finished the last one; otherwise that
   frame has no avoidance step. No per-frame code waits on the pool; only the system's destructor (`f_839fbe40`) does.
   So the step runs beside the frame, a frame behind, and is not a dependency of the main thread's wait. [jobs.md](jobs.md)
   found it running in 22% of the instants when at most two job threads ran, but the frame was not waiting for it.
   **read**
3. **One step solves every character in the avoidance system, one at a time, on that one thread.** The
   `GatherBoundariesAndSolveCharacters` task (`f_820da77c` → `f_820de560`) claims characters one by one from per-thread
   ranges, with work stealing. For each character it gathers the nearby navigation-mesh boundaries, if the character
   has a boundary gatherer (`f_820d0640` → `getNearbyBoundariesFlood`), then runs the avoidance solver
   (`Solver::solve`, `f_820def60`: scores capsule, sphere, boundary and agent obstacles, picks the best candidate
   velocity). The cost is the number of characters times (the mesh faces flooded inside each one's sensor box plus its
   neighbours, boundaries and obstacles times the candidates). **read** (the loop, the calls), **guess** (the scaling)
4. **The work is split for threads, but the game runs it as one.** The task splits the characters into one range per
   thread (`f_81708bc0`; up to 17 slots) and reads its instance index from Havok's task input, so several instances
   could run at once. The game passes the CA pool's worker count as the thread count (1), and its Havok-task wrapper
   (`f_839febe0`) gives every task exactly one instance. So the whole step runs on one thread. Neither number comes from
   data: the core mask is a literal in `f_8102f530`, and the instance count is a literal in `f_839febe0`. **read**
5. **No level of detail in the step.** Every character in the system's list (`System+0x60`, count `+0x68`) is solved on
   every step; no distance, visibility or frame-interval test was found in the step's code. Which characters exist is
   decided outside it: the game's PathFinding component makes a character for its actor (`f_82e81090` → `f_80d155e0`),
   and `IsEnableBoundaryGather` (game PathFindingParam `+0xaf`) decides whether it gets a boundary gatherer, the
   hottest per-character part. **read** (what was searched), **guess** (that nothing else gates characters)
6. **Time.** The avoidance step takes no time step (`System::step(hkTaskQueue *, int)`; the async tasks get none
   either): its output is a velocity. The navigation task is handed the frame's step, but the path the game takes (the
   mode byte at the delegate's `+4` is 0) never reads it. Above 60 fps the avoidance step runs once a frame while it
   finishes inside one, so more often in real time, and is skipped on frames where the last one is still running.
   **read**

## Functions

### The module, the task and the frame's wait

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80fb36f0` | `NavMeshSystemModule_Create` | Makes the "NavMeshSystem" module, a 0x158-byte singleton at `[0x84e63d28]` (vtable `0x84bb9ec8`). Its `+0x38` is a task object (`f_80eefb10`: vtable `0x84c37d30`, a LightEvent at `+0x70`); `+0xc0` a delegate that runs that task's functor; `+0xe0` the functor slot the calc fills; `+0x100` a sync-point node with hash 0xc7303adb and the delegate `{module, f_800aef70}` at `+0x108`, registered by `f_80eed160` | read |
| `f_8102f530` | `NavMeshSystemModule_Init` | Slot 7 of the module's vtable. Makes a heap named "NavMeshSystem" (0x1f4000 bytes), fills the navigation system's settings (*NavMeshSystem settings* below: the two pools' priorities and core masks, three radii) and builds the navigation system (`f_8102f6e0`, [jobs.md](jobs.md)). The pool settings are literals here, not data | read |
| `f_832195f0` | `NavMeshSystemModule_Calc(module, arg)` | Slot 2 (`+16`), the module's calc, hot rank 2157. Returns at once while its task is queued or running (task state byte `+0xb`, at module `+0x43`, 2 to 5). Otherwise it stores the delegate `{vtable 0x84bba038, the first 32-bit word of arg}` at `+0xe0`, then submits the task to the SceneLowPriority manager's second queue (`[[0x84eb74c0]+0x10]`) through `f_801bca90` | read |
| `f_801bca90` | `Task_Submit(task, desc)` | Hot rank 3024. Queues a task on a task queue (`desc+0x18`, or `[desc+0x10]+0x118`); when the calling thread is one of that queue's threads it runs the task at once (`f_801aee20`); when `desc+9` is 1 it waits on the task's event (`+0x70`) after queuing. Else it pushes it with `f_801b5b60` | read |
| `f_801b5b60` | `TaskQueue_Push(queue, &{priority, task})` | Hot rank 1499. Pushes a task with a priority byte | guess |
| `f_811f75d0` | `SceneLowPriority_Create(mgr, heap, stack, mask)` | Builds the task manager stored at `[0x84eb74c0]` (0x50 bytes): two queues (`+0x8`, `+0x10`) and two threads, both named "SceneLowPriority" (`+0x18`: priority 28, the core mask from its settings, 2 by default; `+0x20`: priority 17, core mask 0x3f, any core). Its maker `f_811f7560` (a vtable word at `0x84c89fe0`) gives the stack size 0x80000 and mask 2 when no settings are given | read |
| `f_800ffde0` | `Task_RunFunctor(task)` | Hot rank 545. A task's run: calls the functor at `task+0xa8` (slot 0). For this module that is the delegate at `+0xe0`, so it calls `f_80781ae0` | read |
| `f_800aef70` | `NavMeshSystemModule_WaitTask(module)` | Hot rank 3614. Waits on the task's LightEvent (module `+0xa8`, the task's `+0x70`): the wait registered under sync point 0xc7303adb | read |
| `f_80eed160` | `SyncPoint_Register(hash, node)` | Links a node into the sync point with that hash (the list at `0x84eb7df8`, entries found by the hash 0x40 bytes before them), or into a pending list (`0x84eb7e18`) when that sync point is not made yet | read |
| `f_80e4b5f0` | `SyncPoint_NavStep_Init` | A static initialiser (word at `0x8487fb98`): makes the sync point `0x84eb1890` with hash 0xc7303adb, its list at `+8`, its mutex at `+0x20`, and registers it (`f_80e4c860`) | read |

`EntryJobsBgGraphics` (`f_83aff840`, hot rank 457, its row in [jobs.md](jobs.md)) and `ActorModule_Calc` (`f_83aff1f0`,
rank 299, its row in [actors.md](actors.md)) both lock the sync point's mutex and call every node's delegate (at
0x83aff96c and 0x83affa58 in the first, 0x83aff56c in the second), then go on to the actor frame counter (`f_801c5ef0`).
So whichever of them runs waits for the navigation task. **read** Which of the two runs in a frame, and that the
module's calc runs earlier in the same frame, were not traced. **guess**

### The navigation task (on the SceneLowPriority thread)

The task runs `f_80781ae0` ([physics.md](physics.md), `NavStep_Run`): a thunk that passes the global navigation system
(`[0x84eaedb0]`) and the delegate's data to its body.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80781b00` | `NavStep_Body(nav, &{step, mode})` | Hot rank 474. With mode 1 it steps everything on the calling thread (the world with `step`, the avoidance step through a trivial task queue). The module always passes mode 0, and then, in order: (1) when no world processing is pending (`nav+0x190`) and neither pool's worker is busy, applies queued changes (`f_80bf54d0`); (2) notes whether the CA worker is busy (bit 0 of `+0xe8` of each worker thread, which `JobGraph_Start` sets and `PoolB_SchedulerLoop` clears at 0x800823cc); (3) runs or starts Havok AI's world processing: a first part inline (`hkaiWorldProcessingTasks::makeContext`, a trivial task queue), the main part on the NavMeshDepWorker pool (`f_800711c0` on `nav+0x80` / `+0x88`), or, once that has finished and the CA worker is idle, a follow-up job (`nav+0x1f0`) on the same pool; (4) `hkaiWorld::doSynchronousProcessingRun` (`f_81f2b500`) inline, under the lock at `nav+0xc8`; (5) `f_83a06120` for each character with the "CA idle" flag; (6) only when the CA worker is idle: `f_83a05410` for each character, then builds the avoidance tasks (`f_820d1420`, the thread count the CA pool's worker count), wraps them (`f_839febe0` into `nav+0x120`, job `nav+0x128`) and starts them on the CA pool (`f_800711c0` on `nav+0x118`, at 0x80782664). It waits for neither pool | read |
| `f_80bf54d0` | `NavSystem_ApplyPending(…)` | Applies the commands queued since the last time (adding and removing avoidance characters, their settings) through `f_80bf6560`, about 15 calls. Run only while no navigation work is in flight | guess |
| `f_80bf6560` | `NavSystem_ApplyCommand` | One queued change: names `setSteeringProperties`, `setRadius`; calls `System::addCharacter` (`f_80bf8224`, at 0x80bf6a80) and `removeCharacter` (`f_80bf8550`, at 0x80bf6b28) | read |
| `f_80bf8224` | `hkaiCollisionAvoidance::System::addCharacter` | The game's copy of Havok's call ("void hkaiCollisionAvoidance::System::addCharacter(Character *)") | read |
| `f_80bf8550` | `hkaiCollisionAvoidance::System::removeCharacter` | The same for removal | read |
| `f_83a05410` | `NavCharacter_PushState(item, world)` | Hot rank 2395 (836 instructions). Before each avoidance step, for each of the game's navigation characters (the list `[[nav]+0x448]+0x20`, count `+0x28`): writes the game's position and velocities into its avoidance character (`item+0x10`; `setCurrentVelocity`, `setSurfaceVelocity`, and others from Havok's `hkaiCollisionAvoidanceCharacter.inl`) | read |
| `f_83a06120` | `NavCharacter_Post(item, world, caIdle)` | Hot rank 1122 (764 instructions). Called for each navigation character after the synchronous processing, with whether the CA worker is idle. **guess**: reads the avoidance output back only when it is | read |
| `f_81f2b10c` | `hkaiWorld::isAsyncProcessing` | True when the world's stepping mode (`+0x4c8`) is 1, 2 or 3; otherwise reports "Unknown stepping mode". When false, the task turns async processing on (`f_81f29f9c`) | read |
| `f_81f2a000` | `hkaiWorld::disableAsyncProcessing` | "TtdisableAsyncProcessing" | read |
| `f_81f33dc0` | `hkaiWorldProcessingTasks::makeContext` | Hot rank 1186: makes the world processing's context (path requests, navigators), reached through the thunks at 0x81e9a4f0 to 0x81e9a500 | read |
| `f_81f35b80` | `hkaiWorldProcessingTasks::makeMainPhaseTaskGraph` | Hot rank 1477: the main phase's task graph, reached through the thunk at 0x81e9a504 | read |
| `f_839fbe40` | `NavSystem_Destroy` | The only place found that waits on the two navigation pools (`PoolB_WaitPhase` at 0x839fbf08 for NavMeshDepWorker, 0x839fbf40 for NavMeshCAStepDepWorker), before freeing them | read |

### The avoidance step's tasks

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_820d1420` | `CollisionAvoidance_AddStepTasks(graph, system, threads, 0xffff)` | Hot rank 2899 (the caller has reset the graph through its vtable `+0x20`; what the fourth argument does was not read). Makes a StepContext holding the system and the thread count, then 12 tasks, each added once with priority 0x40 (`hkTaskGraph::addTask`, `f_81700b90`) and linked by edges (the array at `graph+0x428`): InitContext, ClearNeighborCollectors, GatherCharacterAabbs, UpdateAndGatherObstacleAabbs, SortAabbs (three), ScanCharacterAabbs, ScanObstacleGeneratorAabbs, GatherBoundariesAndSolveCharacters, ApplySolverOutput, Finalize | read |
| `f_81700b90` | `hkTaskGraph::addTask(graph, task, priority)` | Hot rank 2559. Keeps the task (array at `+0x4b8`, count `+0x4c0`) and adds a 32-byte node (`f_817009dc`, "hkTaskGraph::TaskId hkTaskGraph::addTask(hkTask *, hkTask::Priority::Enum)"): the task and its priority byte. No instance count | read |
| `f_820d6078` | `GatherBoundariesAndSolveCharactersTask_Construct(task, ctx)` | Builds the 0x920-byte task (*Structures*): the character count from the system (`+0x68`), the thread count from the StepContext (`+0x20`), and the ranges (`f_81708bc0`) | read |
| `f_81708bc0` | `WorkRanges_Init(r, threads, count, grain)` | Hot rank 1354. Splits `count` items into `threads` equal ranges (`sdiv` at 0x81708c44) | read |
| `f_81708ea0` | `WorkRanges_Steal(r, slot, grain, &index)` | Hot rank 1873. Called when a slot's own range is empty: takes an item from another slot's range; 0 when none is left | guess |
| `f_820de560` | `GatherBoundariesAndSolve_Run(task, input)` | Hot rank 123. Its slot is the instance index (`input+0x18`) plus 1. Claims characters one at a time (an atomic add on the slot's `{next, end}` pair; `f_81708ea0` when it runs out). For each: the character (`[[ctx]+0x18]+0x60` array) and its 0x70-byte solver entry (`[ctx]+0x58`); if the character has a boundary gatherer (`+0xe0`) calls its slot 6 (`+0x30`, `f_820d0640`), then `Solver::solve` (`f_820def60`, [physics.md](physics.md)) on the entry's `+0x50` | read |
| `f_820d0640` | `DefaultBoundaryGatherer_Gather(gatherer, character, entry)` | Hot rank 185. Slot 6 of the vtable `0x84944020`. Finds the character on the navigation mesh (`hkaiCharacterUtil::resolvePositionOnNavMesh`, `f_81ebbd80`) and collects the boundary edges near it (`hkaiNavMeshUtils::getNearbyBoundaries`, `f_81e69500`, the flood, rank 67, at 0x820d08d4). **guess**: in a box from the character's sensor size | read |
| `f_820d0540` | `DefaultBoundaryGatherer_Construct(g, x1)` | Makes the 0x38-byte gatherer (vtable `0x84944020`) | read |
| `f_820d0cbc` | `hkaiCollisionAvoidance::Character::setBoundaryGatherer(c, g)` | Stores the gatherer at character `+0xe0` (reference-counted) | read |

`f_820da77c` ([jobs.md](jobs.md), `HkaiCharacterStep_Task`, hot rank 124) is the task's `process`: Havok's timers ("TtAi",
"TtCollision Avoidance", "TtGatherBoundariesAndSolveCharacters", 900 instructions of them) around one call of
`f_820de560`. `hkaiCollisionAvoidance::System::step` (`f_820cc4f4`, [physics.md](physics.md)) is the single-threaded form,
used only in mode 1. The solver `f_820def60` (rank 237) holds "solveSingleAgent", "scoreCapsuleObstacles",
"scoreSphereObstacles", "scoreBoundaryObstacles", "scoreAgentObstacles", "applyModifiers", "chooseBestCandidate"; its
inner loop `f_820e2520` (rank 53) is on [physics.md](physics.md). **read**

### Making a character

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_82e81090` | `PathFindingComponent_Setup` | A slot of the game's PathFinding component (the vtable word at `0x84b6d4c0`; the same vtable's word at `0x84b6d550` is `f_82e82650`, which names the icon `ResourceIcon\PathFindi…`). Fills a description from the actor's game PathFindingParam (`+0xaf` IsEnableBoundaryGather into `desc+0x44`, `+0xae` IsAvoidMaterialNavigation into `+0x45`, `+0xad` IsAvoidMaterialLocalSteering into `+0x46`, among others) and makes the navigation character with `f_80d153d0`, kept at component `+0x20` | read |
| `f_80d153d0` | `NavCharacter_Create(nav, desc)` | Allocates the game's navigation character and builds it (`f_80d155e0`) | read |
| `f_80d155e0` | `NavCharacter_Build(obj, …, desc)` | Makes the Havok AI navigator and the avoidance "Character" (the names `hkaiNavigator`, `Character`, `hkaiNavMeshClearanceFilter`, `hkaiAstarEdgeFilter`, `hkaiAstarCostModifier`; `setSteeringProperties`, `setSensorSize`). When `desc+0x44` is 1 (at 0x80d157d4) it makes a `DefaultBoundaryGatherer` (`f_820d0540`) and attaches it (`f_820d0cbc`); otherwise the character has none and the step skips its boundary flood | read |
| `f_82d8a610` | `GamePathFindingParam_Visit` | `game__component__PathFindingParam` (name `f_82d8c100`): CharacterRadius `+0x50`, SensorHeight `+0x9c`, CharacterType `+0x48`, IsEnableBoundaryGather `+0xaf`, IsAvoidMaterialNavigation `+0xae`, IsAvoidMaterialLocalSteering `+0xad`, CollisionAvoidancePriority `+0x54`, PredictedCornerBufferNum `+0x80`, SearchRadius `+0x98`, UseLSSpeedAsPreferredSpeed `+0xb4`, and the search limits. Its twin `f_82d8b880` | read |
| `f_83c15650` | `EnginePathfindingParam_Visit` | `engine__component__PathfindingParam` (name `f_83c18010`): EnableNavMesh `+0xb4`, CharacterRadius `+0x60`, CharacterType `+0x48`, AvoidanceSensorRadiusScale `+0x5c`, AvoidanceSensorHeight `+0x58`, IsEnableBoundaryGather `+0xb8`, PenetrationPenalty `+0x80`, DodgingPenalty `+0x68`, VelocityHysteresis `+0xa0`, SidednessChangingPenalty `+0x94`, WallFollowingAngleDeg `+0xb0`, CollisionPenalty `+0x64`, EnableNavVolume `+0xb5`, and the search settings. Its twin `f_83c16b40` | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84e63d28` | `g_NavMeshSystemModule` | The NavMeshSystem module (0x158 bytes, `f_80fb36f0`) | read |
| `0x84eaedb0` | `g_NavSystem` | The navigation system object (*Structures*), read through the GOT slot `0x84c9df58` by `f_80781ae0` | read |
| `0x84eb74c0` | `g_SceneLowPriorityTaskMgr` | The SceneLowPriority task manager (`f_811f75d0`); its `+0x10` is the queue the navigation task goes to | read |
| `0x84eb1890` | `g_SyncPoint_NavStep` | The sync point with hash 0xc7303adb (`f_80e4b5f0`): its node list at `+8`, its mutex at `+0x20` | read |
| `0x84bb9ec8` | `NavMeshSystemModule_vtable` | Slot 2 the calc `f_832195f0`, slot 7 the init `f_8102f530`, slots 26 to 28 the names "NavMeshSystem", "NavMesh" and its icon | read |
| `0x84bba038` | `NavStepDelegate_vtable` | The delegate the calc stores: slot 0 `f_80781ae0`, slot 2 its copy (`f_83219b20`) | read |
| `0x84944020` | `DefaultBoundaryGatherer_vtable` | Slot 6 `f_820d0640` (the GOT slot `0x84cd5de0` holds it less 16) | read |
| `0x83d483b7` | `str_NavMeshCAStepDepWorker` | "NavMeshCAStepDepWorker", the CA pool's thread name | read |
| `0x83e3f7bd` | `str_NavMeshDepWorker` | "NavMeshDepWorker" | read |
| `0x83da2ad8` | `str_SceneLowPriority` | "SceneLowPriority", both of the task manager's threads | read |

## Structures

### NavMeshSystem settings (the stack block `f_8102f530` fills; `f_8102f6e0` and `f_80f6ecf0` read it)

| Offset | Field | Sure |
|---|---|---|
| `+0x0`, `+0x4` | NavMeshDepWorker's priority (22) and core mask (4: core 2) | read |
| `+0x8`, `+0xc` | NavMeshCAStepDepWorker's priority (22) and core mask (2: core 1). A pool B makes one worker per set bit of `mask & 0x3e` ([jobs.md](jobs.md), `f_8069aa20`), so each pool has one thread | read |
| `+0x10` | A pointer, `0x84d61838` | read |
| `+0x18` | 1000 | read |
| `+0x1c` to `+0x30` | Copied into the 0x150-byte object at `nav+0x10` (`f_810ccb00`) | read |
| `+0x28` | 0x100 | read |
| `+0x38`, `+0x40` | Three radii (2.8, 10.0, 22.1) and their count: one entry each at `nav+0x28` (16 bytes, `f_80f6f2e8`). **guess**: the character size classes of the navigation meshes | read |

### The navigation system (`[0x84eaedb0]`, built by `f_8102f6e0` and `f_80f6ecf0`)

| Offset | Field | Sure |
|---|---|---|
| `+0x0` | The world wrapper (0x530 bytes, `f_80f6f8e4`: hkaiStreamingCollection, hkaiNavMeshCutter, hkaiPathRequestManager, hkaiNavigatorManager, …); `[+0x448]+0x20` (count `+0x28`) the game's navigation characters, `[+0x488]` a second list of the same shape | read |
| `+0x8` | The collision-avoidance System (0x90 bytes, `f_80f6f55c`) | read |
| `+0x20`, `+0x28` | The radius entries' count and array | read |
| `+0x80`, `+0x88`, `+0x90` | NavMeshDepWorker's pool B, its job graph, its Havok-task job | read |
| `+0x98` / `+0xc0`, `+0xc8` / `+0xf0` | Two locks, a mutex with an owner word each | read |
| `+0x118`, `+0x120`, `+0x128` | NavMeshCAStepDepWorker's pool B, its job graph, its Havok-task job | read |
| `+0x190`, `+0x198` | The world processing's context and its task graph, kept between frames | read |
| `+0x1a0` | The avoidance step's hkTaskGraph (0x5e8 bytes) | read |
| `+0x1f0` | The follow-up job the task starts on NavMeshDepWorker (in mode 1 it is called directly, `f_800ae640`) | read |

### The collision-avoidance System (0x90 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0x40` | The up vector (0, 1, 0) | read |
| `+0x60`, `+0x68` | The characters (an array of pointers) and their count | read |

A character's `+0xe0` is its boundary gatherer (null when the actor's IsEnableBoundaryGather is off). **read**

### GatherBoundariesAndSolveCharactersTask (0x920 bytes, `f_820d6078`)

| Offset | Field | Sure |
|---|---|---|
| `+0x30` | The work ranges (`f_81708bc0`) | read |
| `+0x70` | Per-slot range indices, room for 17 inline | read |
| `+0x4c0` | The ranges, 64 bytes each: `{next, end}` claimed by an atomic add | read |
| `+0x910` | The character count | read |
| `+0x914` | The thread count | read |
| `+0x918` | The StepContext: its `+0x18` the System, `+0x20` the thread count, `+0x58` the solver entries (0x70 bytes each, count `+0x60`) | read |

## Notes

### One frame's navigation

1. A module calc job runs `f_832195f0`: it stores the navigation delegate and submits the task to SceneLowPriority
   (priority 17, any core). **read**
2. The SceneLowPriority thread runs `f_80781b00`: queued changes, Havok AI's world processing (part inline, part
   started on NavMeshDepWorker, core 2), `doSynchronousProcessingRun` inline, the characters' state pushed into their
   avoidance characters, and the avoidance step's 12 tasks started on NavMeshCAStepDepWorker (core 1). It waits for
   neither pool, then signals the task's event. **read**
3. `EntryJobsBgGraphics` (stage 5, [jobs.md](jobs.md)) or the Actor module's calc waits on that event before the
   actors' BaseProc phase. **read** (the wait)
4. NavMeshCAStepDepWorker runs the 12 tasks one after another on its one thread, through the frame. Its result is
   read back by the next frame's task (`f_83a06120` gets the "CA idle" flag). **read** (the start, the flag), **guess**
   (the read-back)

LS's job-thread pinning: the CA worker is on core 1 with `BaseProcExecutor1` and `ModuleSystemWorker1`
([jobs.md](jobs.md), *Pinning*), at priority 22 against the executors' 16. Under LS's virtual cores a better
waiter asks the holder to give its core up at its next checkpoint (an nn::os call, an SVC or a loop header,
`hos_thread.c`), so a long Havok loop on core 1 can hold back core-1 work for up to that checkpoint. **read** (the
masks and priorities, the host's rule), **guess** (how much it costs)

### What the measurement shows

[jobs.md](jobs.md)'s *Jobs on the critical path* lists what runs while the main thread waits and at most two job
threads run. The character step's 22% there is time it runs beside that wait, not a wait for it: the frame has no edge
to the CA pool. The work the frame does wait on is the navigation task itself (`f_80781b00` on the SceneLowPriority
thread) and whatever runs before stage 5's join. **read** (the edges), **guess** (that the measured wait is not partly
the core-1 sharing above)

### Above 60 fps

- The avoidance step has no time input; the navigation task's step value is passed but unused in mode 0. **read**
- The task and the step run at most once a frame each. Above 60 fps they run more often in real time while the step
  takes less than a frame. When it takes longer, frames without a step keep the last avoidance velocities. Nothing
  counts frames here. **read** Whether Havok AI's world processing (navigators, path following) reads a time from
  elsewhere was not traced.

## Optimization notes

- **Run several instances of the gather-and-solve task.** It is written for it: per-thread ranges, the instance index
  from its input, work stealing (`f_820de560`, `f_81708bc0`, `f_81708ea0`). Two literals stop it: the CA pool's core
  mask (2, at 0x8102f5a0 in `f_8102f530`, which also sets the thread count `f_820d1420` gets) and the instance count of
  1 that `f_839febe0` gives every task (at 0x839fed00). Giving only this task (identified by its vtable, set in
  `f_820d6078`) N instances and N ranges would let N threads share the characters. The other 11 tasks must keep one
  instance (InitContext and Finalize would run twice). Risk: Havok's per-thread data (the neighbour collectors cleared
  by ClearNeighborCollectorsTask) may be sized by the thread count, so both numbers must change together. **read** (the
  literals and the loop), **guess** (that it is safe)
- **Or widen the CA pool.** A larger mask (for example 0x3e) makes one CA worker per core. Each extra worker is one more
  thread pinned to a core the job pools use, so it also adds wake-ups and core sharing. Without the instance change
  only independent tasks (the three SortAabbs) could run side by side. **read** (the mask's effect), **guess** (the gain)
- **Expect little from a faster step on its own.** The frame does not wait for it, so a faster step only frees core 1
  sooner and lets it run every frame at high frame rates. What the frame does wait on is the navigation task on the
  SceneLowPriority thread: time `f_80781b00` against `EntryJobsBgGraphics`'s wait (`f_800aef70`) first. **read** (the
  edges), **guess** (where the time goes)
- **Fewer boundary floods.** `getNearbyBoundariesFlood` (rank 67) runs only for characters with a gatherer, which the
  game PathFindingParam's IsEnableBoundaryGather decides (`+0xaf` → `desc+0x44` → `f_80d155e0`). Clearing it for far or
  simple characters would cut the hottest per-character part, but would let them walk into walls they no longer see.
  A per-frame skip (gather every other step for far characters) would need a hook in `f_820de560` at the call to the
  gatherer's slot 6. **read** (the gate), **guess** (the effect)
- **Update far characters less often.** The step has no LOD: every character in `System+0x60` is solved on every step.
  A hook in `f_820de560` could skip characters by distance or by the owning actor's calc-skip state and reuse their last
  output. Risk: their avoidance velocities go stale and neighbours react to old positions. **guess**
- **Move the CA worker off core 1 or lower its contention.** It shares core 1 with `BaseProcExecutor1` (to which
  `RunBaseProc` 1 is pinned) and `ModuleSystemWorker1`. A different mask literal (0x8102f5a0) would put it on another
  guest core; `HOS_PIN` in `hos_thread.c` only sets the host thread's affinity, not the guest core it holds. Every guest
  core 1 to 5 carries the same two pools, so the gain is in picking the least-used one. **guess** (that core 1 is the
  busy one)

## Open questions

- Which stage the NavMeshSystem module's calc job runs in, and so whether stage 5's wait is for this frame's task or the
  last one; and which of `EntryJobsBgGraphics` and `ActorModule_Calc` does the wait in a normal frame.
- How long `f_80781b00` takes per frame (the part the frame waits for), against the CA step's ~3 ms.
- Which actors have the game PathFinding component, how many avoidance characters exist in Kokiri Forest, and how many
  have a boundary gatherer.
- Whether characters leave the avoidance system when their actor sleeps or turns into a PreActor ([actors.md](actors.md)):
  who queues `removeCharacter`.
- The CA task graph's edges (which of the 12 tasks could run side by side) and whether Havok's per-thread neighbour
  collectors are sized by the thread count.
- What the follow-up job at `nav+0x1f0` (`f_800ae640`, rank 666) does, and what the world processing on NavMeshDepWorker
  runs (path searches, navigators).
- What the first 32-bit word of a module calc's argument is (the frame-time keeper's value that `ModuleSystem_ModuleCalc`
  stores at 0x80706f14 is a guess); in mode 0 it is unused here anyway.
- Whether Havok's own task queues ever give a task several instances (the game's wrapper never does).
