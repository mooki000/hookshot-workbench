# Physics, cloth and navigation

The game's physics, cloth and navigation are Havok 2025.2: the build paths name
`Havok/hk2025_2_10_PhysicsClothNavigation`, and the code from about 0x81700000 to 0x82200000 is Havok Physics (`hknp…`),
Havok Cloth (`hcl…`), Havok AI (`hkai…`) and their shared base (`hk…`, `hkcd…`). Over them sits the engine's layer,
"Phive" (the `Phive/…` files, the `[Phive] …` job labels): a physics module, a Dynamics Framework that owns the worlds and
steps them as jobs of the frame's graph, async collision queries, and the actors' rigid bodies, character controllers
and cloth. Looked at in November 2026 by static analysis of the binary, for the frame-rate work: [hot-path.md](hot-path.md)
found Havok to be a third of the 200 hottest functions, and [jobs.md](jobs.md) found cloth and Havok AI on the frame's
critical path. A second pass the same month went inside Havok Physics' step (its task graph, the solver's time step, the
world's settings) and settled where the time step comes from. The focus is what runs each frame, what it costs, and how
its time step is chosen.

**LS code:** none touches physics itself. The movement-speed row scales a character controller's speed before the
physics step uses it (`patch_cct_speed`, [player.md](player.md)).
**See also:** [jobs.md](jobs.md) (the pools, stage 5, the BaseProc phase, the narrow-phase task's row),
[actors.md](actors.md) (the Physics component, the per-actor jobs, cloth skipping), [player.md](player.md) (the
controller's speed setter `f_80559df0`), [navigation.md](navigation.md) (Havok AI's step and collision avoidance),
[effects.md](effects.md) (particle physics, which is not Havok's), [maps.md](maps.md) (the collision and navigation-mesh
files), [frame-time.md](frame-time.md) (the step and the clock), [hot-path.md](hot-path.md) (the ranks)

## In short

1. **The world step is a set of jobs in the frame's graph, not a Havok thread.** The PhysicsSystemBaseProc adds two jobs
   to the BaseProc phase, one per world (`f_806a4570`, `f_806a4140`). Each pushes the Dynamics Framework's seven
   "updateWorld" phase jobs (`f_806a48f0`). The third phase converts Havok's collide task graph into the game's own
   64-byte job records (`f_80372000`, `f_80372400`), and a job builds the solve tasks the same way once collide has run.
   The game's two pool loops recognise a Havok task's job and run `hkTask::processTask` inline. Havok's own thread pool
   (`hkDefaultTaskQueueEx`) is linked; no thread for it is named. **read**
2. **The world's base step is 1/60 s; the frame time reaches the solver through its substep count.** The
   PhysicsModule's init writes 0.016666668 into the framework's config (at 0x83b89570, over whatever PhiveConfig said)
   just before it makes the Dynamics Framework, which copies it to `DF+0x24` (capped at 0.999 s); phase 3 hands that to
   Havok every frame (`hknpSolverInfo::setStepInfo`, `f_818f0374`, from `preCollide`), and the world is made without
   Havok's variable-time-step helper (`hknpWorldCinfo+0xf8` is 0). **read** But each update the game also sets the
   solver's step count from the frame step: `(int)(frame step × the steps of a 60 fps frame + 0.001)` into the solver's
   `+712` (`f_838ceb80`, `f_838cfc10`; `DF+0x38` × `DF+0x244`), 8 a 60 fps frame in the places seen, 4 at 120 fps.
   Cut short, the count moves the world less than the frame's time: at 120 fps a moving platform in the Deku Tree slid
   out from under Link (issue 1798), so above 60 fps LS rounds the count up (`hos_oot_solver_steps`, LS's `host/oot_high_fps.h`).
   **read** (from that header and its fix). How Havok divides `DF+0x24` over the steps was not traced.
3. **One Havok step per world per frame, its substep count set from the frame step** (*In short* 2). The world's
   description holds a count of its own (`hknpWorldCinfo+0xdc`, from the world config's `+0x6c`; Havok's default is 4),
   which the per-update count replaces. **read**
4. **Every Havok task runs as one job, on one thread.** The game gives each task one instance (a literal 1 at
   0x80372540). Havok builds the narrow phase's pairs task with per-thread work ranges for the step's thread count
   (`f_8191e80c`), as Havok AI's avoidance task is ([navigation.md](navigation.md)), so the one instance claims them all.
   The physics step's parallelism is only the graph's width: independent tasks, the two worlds, cloth beside them.
   **read** (the literal, the ranges), **guess** (that nothing else gives a task more instances)
5. **Cloth follows the frame's step.** An actor's cloth update gets the actor update's step and the cloth batch steps
   each instance by it times its own factor (`f_80168320`, `f_8016b320`). The hclWorld has Havok Cloth's fixed-rate
   helper ("Interpolation Time-Stepping"), but the game builds it with that mode off (`f_8016cb20` → `f_80e66b2c`,
   `hclWorld+0x163` = 0). **read**
6. **The async queries are drained after the step**, in a job the post-update phase makes (`f_80380a20` → `f_806d4620`):
   each of the four queues (ray casts, shape casts, the two AABB queries) serves at most its configured number of
   requests a frame. **read**
7. **What costs.** Cloth is the heaviest Havok code: 7 of the 50 hottest functions, from rank 19 (the game's cloth step)
   through the simulate operator (21), collide-and-solve (26, 50), the ordered solve (51) and the constraints (40, 66). Ray
   casts come next: the broad phase's ray walk is rank 42 and the mesh shape's queries rank 63 and 113. Then the contact
   solver (30, 78), compound shapes' instance update (36) and Havok AI's boundary flood and avoidance solver (53, 67). In
   the step itself the narrow phase (its pairs task rank 146, body pairs 99) and the solver's substeps (solve 155, gather
   velocities 119, integrate 167 and 183) lead; the broad phase's tree work and the task builders are below rank 500.
   **read** (the names), ranks from [hot-path.md](hot-path.md)

## Functions

### The physics module and the Dynamics Framework

The root object is `[0x84ea7ff0]` (reached through the GOT slot `0x84c9df00`). Its `+0xc0` is the Dynamics Framework
(DF below), and its `+0x10` holds the per-core Havok contexts that cloth uses.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83b89110` | `PhysicsModule_Init` | PhysicsModule's vtable slot 7 (vtable `0x84c862d0`): reads `Phive/Config/PhiveConfig.byml`, loads `Lib/Phive/Phive_resource.Nin_Ounce_NVN2.release.sarc`, names "physics::PhysicsModule" and "PhiveBackEnd", makes `phive::DepWorker` (`f_80cea370`). Then it writes 1 and 1 at the root's `+0x94`, **writes 0.016666668 (1/60) at the config's `+0x44`** (at 0x83b89578; the framework's config is the block at `+0x20`, so this is its step `+0x24`) and makes the Dynamics Framework through `f_81225f20` with that config | read |
| `f_806a6610` | `PhysicsModuleJob_Run` | The "PhysicsModule" job's run (slot 2 of its vtable `0x84c863f0`; the job is added in stage 5, before StartBaseProc, [jobs.md](jobs.md)). Calls `f_806a6620` on the module (`[0x84eb5fe0]`) | read |
| `f_806a6620` | `PhysicsModule_Calc(module, ctx)` | The module's calc, also its vtable `+24`. Checks two phase hashes (0x9610d708, 0x51897502) in the context, then works on two of the DF's parts: under a lock `f_806a6850` on `[DF+0x130]`, then `f_806a6c20` on `[DF+0x170]`. What those parts are was not traced | read |
| `f_81225f20` | `Physics_CreateDynamicsFramework(root, cfg, heap)` | Makes the DF (`f_81226010`), stores it at `root+0xc0` and registers it | read |
| `f_81226010` | `DynamicsFramework_Create(cfg, heap)` | "Dynamics Framework[%s]": allocates 0x500 bytes and builds the DF (`f_838ce800`). Its step is the config's `+0x24` (the 1/60 `f_83b89110` wrote), capped at 0.999 (`0x83e60ac4`). Then it makes the framework's parts, among them the async queries (`f_80d36e60`, kept at `DF+0x128`) | read |
| `f_838ce800` | `DynamicsFramework_Construct(df, …, s0 = step, s1, s2, s3)` | Vtable `0x84c56788`. On top of the base (`f_838f1f20`) it sets the solver's tau and damping to 0.6 (`+0x234`, `+0x23c`), the substep count to 4 (`+0x244`), 10000.0 at `+0x248`, the config's `+0x90`, `+0x94`, `+0x11c` at `+0x228` to `+0x230`, 200 at `+0x1cc`, and the job slots `+0x2a8` to `+0x450` empty | read |
| `f_838f1f20` | `DynamicsFrameworkBase_Construct(df, w1, w2, s0 = step)` | Vtable `0x84c58188`: the step at `+0x20`, `+0x24`, `+0x2c`, `+0x34`; 1/step at `+0x28`, `+0x30`; ten scales from `+0x38` to `+0x64` at 1.0. Its one caller is `f_838ce800`. Neither vtable has a slot that sets the step | read |
| `f_80d36e60` | `AsyncQueries_Create(cfg, heap)` | Four 0x98-byte query queues in a 0x28-byte holder (vtable `0x84c56978`): "AsyncRayCast(Entity)" at `+0x8`, "AsyncShapeCast(Entity)" `+0x10`, "AsyncQueryAabb(Entity)" `+0x18`, "AsyncQueryAabb(Sensor)" `+0x20`. Each queue's per-frame limit (its `+0x88`) and sizes come from the config (`+0x8` to `+0x2c`, copied by `f_80d36dc0` and its three twins). Drained by `f_806d4620` | read |
| `f_80f56290` | `PhiveWorld_Create(world, desc, heap)` | Builds a world wrapper: its collision filter and "CollisionFilterBackEnd" (`f_80f55290` and `f_80f55540`, or `f_80f55380` and `f_80f554b0`, by the world's type at `+0x84`), an hknpWorldCinfo on the stack (`f_80f57fb8`) filled from the world's description (`desc+0x38`: *hknpWorldCinfo* below), then the hknpWorld (0x1170 bytes, `f_80f58a60`) at `+0xe0`. Also names hknpTaskGraph, hkDynamicBlockStreamAllocator, hknpWorldCommandQueue, the CollectManifold and FlipBackfaceCollision modifiers, the contact-modifier sensors and the custom shape functions (SDF, compound height field, user triangles), and makes the world's Havok-task job (`f_80f6f430`) | read |
| `f_80f57fb8` | `hknpWorldCinfo_Construct` | Havok's defaults for a world: body capacity 4096, collision tolerance 0.05, solver tau 0.6, damping 1.0, 4 solver iterations, 1 microstep, deactivation on, the variable-time-step helper off with an expected step of 1/30 (*hknpWorldCinfo* below) | read |
| `f_80f58a60` | `hknpWorld_Construct(world, cinfo)` | The game's copy of Havok's world constructor ("hknpWorld::hknpWorld(const hknpWorldCinfo &)"): the libraries (materials, motion properties, body qualities), the command processors, the space splitter, the broad phase, the collision dispatcher, the deactivation manager (when `cinfo+0xe4`). For the solver it calls `f_818f0374` with the cinfo's iterations and microsteps, or, when `cinfo+0xf8` is set, makes an hknpVariableTimeStepUtil (`f_818f04a4`, at `world+0xd68`) and lets it set them (`f_818f0584`). The game leaves `cinfo+0xf8` at 0 | read |
| `f_83a44c20` | `PhiveWorld_SetNumSubsteps(world, n)` | Slot 17 of the world wrapper (vtable `0x84c56688`, its hknpWorld at `+0xe0`): `hknpWorld+0xc48` = n, `+0xcb8` = 1/n | read |
| `f_80db2950` | `PhiveWorld_SetSolverTauDamping(world, s0, s1)` | Slot 18 of the wrapper: writes the solver's tau and damping block at `hknpWorld+0xc30` (the halves, inverses and ratios beside them) | read |

### The world step each frame

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80b240a0` | `PhysicsSystemBaseProc_AddJobs` | The PhysicsSystemBaseProc's job setup (made by `f_80b23910`, [jobs.md](jobs.md)). Adds a phase-1 job (vtable `0x84c86798`, run `f_806a4570`) and, when the proc's mode allows, a phase-2 job (vtable `0x84c867c0`, run `f_806a4140`). Both get a flag (bit 0 of their `+0x17`) when the proc's `+0x270` is 1, the same byte the jobs test for single-threaded mode | read |
| `f_806a4570` | `PhysicsWorldJob0_Run` | World 0's update, hot rank 3306. Counts at `[0x84eb5fe0]+0x44`. In single-threaded mode (the proc's `+0x270` is 1) it pushes nothing here. Otherwise it calls `f_806a48f0` with world index 0, the job pool at `[0x84eb1920]+0x138`, the context at `+0x140` (`+0x1b0` when bit 0 of `+0x1d3` is set), priority 0x8000 and a phase mask: 0x3ff, or 0x10c (no world step) when the bit for the phase hash 0x51897502 is set in the job's mask table (**guess**: a physics pause) | read |
| `f_806a4140` | `PhysicsWorldJob1_Run` | The same for world 1 (counter `+0x48`), hot rank 2035 | read |
| `f_806a48f0` | `DynamicsFramework_AddUpdateWorldJobs(df, pool, ctx, …, w7 = priority)` | Hot rank 1109. Stores the pool and context at `DF+0x460`/`+0x468`, the world index at `+0x4d4` and the phase mask at `+0x4d8`. Pushes seven job records into the frame's graph (`+0x478` to `+0x4b8`), the first waiting on a job of the context; each phase links the next as it runs. Each is a delegate (vtable `0x84c56878`) on the DF: "[Phive] updateWorldPreImplExtCallPhase1_" (`f_838cfc10`), "…Phase2_" (`0x80374740`), "…Phase3_" (`f_803749e0`), "updateWorldPostImplExtCallPhase1_" (`f_80380a20`), "…Phase2_" (`0x80375320`), "…Phase3_" (`0x838cf8e0`), "…Phase1Ext_" (`0x803766b0`). With `DF+0x1a0` set it pushes a single job instead ("DynamicsFramework::updateWorldSingleThreadExtCall_") | read |
| `f_838cfc10` | `DF_PreImplPhase1` | Hot rank 1994. Keeps the previous step (`+0x34` = `+0x2c`) and queues phase 2. It applies the solver settings to world 0: substeps (wrapper slot 17) = `(int)(+0x38 × +0x244 + 0.001)`, the frame step times the steps of a 60 fps frame (set each update, LS's `host/oot_high_fps.h`), tau and damping (slot 18) = `+0x234 × (+0x4c)²` and `+0x23c`. The static reading saw this under the DF's dirty bits (`+0x1c`). Hooked by `patch_solver_steps`: the step count is rounded up, at least 1 | read |
| `f_803749e0` | `DF_PreImplPhase3` | Hot rank 171. Steps the current world: `f_80372000` on `[DF+0xc8]` (world 0) or `[DF+0xd0]` (world 1) with `s0 = DF+0x24`, the context, and the post-update job (`+0x4a0`), which is made to wait on the step's jobs. Before that, `f_803799c0` (rank 282, "next_center_of_mass_local"); for world 0 with phase-mask bit 0 it also steps `[DF+0x198]` with the same step (`f_803776c0`) | read |
| `f_80380a20` | `DF_PostImplPhase1` | Hot rank 3013. Calls `f_8038086c` on the hknpWorld (what it does was not traced), then makes the frame's post-step jobs ("phive::JP::AfterUpdateWorldPostImplExtCallPhase1_"), among them a delegate job (vtable `0x84c56878`, at 0x80380d00) that runs the async-query drain `f_806d4620`, pushes more Havok tasks (`f_80372400`, at 0x80380f28), and copies the step and its inverse (`+0x24`, `+0x28`) to the previous pair (`+0x2c`, `+0x30`) | read |
| `f_838d0080` | `DF_UpdateSingleThreaded(df, w1)` | DF slot 5: the whole update on the calling thread: `phive::CharacterController::update` (`f_838ceb80`), the world step (`f_838cf0b0`), then `f_838cf1f0` | read |
| `f_838ceb80` | `DF_CharacterControllerUpdate` | "phive::CharacterController::update": the single-threaded path's pre-step. Applies the solver settings like `f_838cfc10` (also storing the substeps at the wrapper's `+0x2c8`), then the controllers' update; calls `f_838d5730` with the step (`+0x24`) and the frame step (`+0x38`). Hooked by `patch_solver_steps` | read |
| `f_838cf1f0` | `DF_CharacterControllerResult` | "phive::CharacterController::result": the single-threaded path's post-step: drains the four async queues (the same calls as `f_806d4620`), the water managers' post-update (`phive::InteractiveWaterMgr::postUpdateWorld`, `phive::ResponsiveWaterMgr::postUpdateWorld`) | read |
| `f_838cf0b0` | `DF_UpdateWorldSingleThreaded` | The single-threaded path: `f_83a445a0` on the current world with `s0 = DF+0x24` | read |
| `f_83a445a0` | `PhiveWorld_StepSingleThreaded` | Calls Havok's own `hknpWorld::stepCollide` (`f_817f1d68`) and `stepSolve` (`f_817f6f80`) with a task queue, around the game's collide and solve task builders | read |
| `f_80372000` | `PhiveWorld_StepCollide(world, s0 = step, pool, ctx, …)` | Hot rank 601. Stores the step at `world+0x140`. Makes the step's own jobs (labels "[Phive] post collide 1/2/3", "post solve"). Fills an hknpStepInput on the stack: `+0` the step; `+4` the step times the DF's time scale (`+0x58`) when that is not 1, else 0; `+8` a thread count, one more than the pool's `+0`, or 1 single-threaded. Calls `f_80372ad4`, then `f_80372400` to turn the tasks into jobs | read |
| `f_80372ad4` | `hknpWorld::generateCollideTasks` | The game's copy ("BuildCollideTasks"), hot rank 541: runs `hknpWorldEx::preCollide` (`f_817ee880`) with the step input and fills an hknpTaskGraph through the simulation's collide (`f_819923b8`) | read |
| `f_80372400` | `PhiveTaskGraph_PushJobs` | Hot rank 467. Reserves one 64-byte job record per task of an hknpTaskGraph (its count at `+0x20`, the 32-byte nodes at `+0x18`: the task, a priority byte), by an atomic add on the context's `+0x130`, fills them and links their dependencies. Every record gets an instance count of 1 (`w22`, set at 0x80372540) and its job's instance counter reset to 0 | read |
| `f_803719e0` | `PhiveSolveTasks_Job` | A job (vtable `0x84c73fb8`) that builds the solve tasks (`f_80371c40`) and pushes them (`f_80372400`) | read |
| `f_80371c40` | `PhiveWorld_GenerateSolveTasks` | Hot rank 1151: calls `hknpWorldEx::generateSolveTasks` (`f_817f3ce0`); also used by `stepSolve` | read |
| `f_817f3ce0` | `hknpWorldEx::generateSolveTasks` | Hot rank 879. Reports the solver settings to the monitor: "Timestep" (`hknpWorld+0xc90`), "Tau" (`+0xc30`), "Damping" (`+0xc40`), "Num substeps" (`+0xc48`); builds the solver factory task (`f_81993780`) | read |
| `f_817ee880` | `hknpWorldEx::preCollide(world, stepInput)` | Hot rank 828: adds pending bodies, rebuilds dirty materials' caches, the pre-collide signal. Sets the solver's step: `f_818f0374` with the step input's two floats, the world's substeps and microsteps and its gravity (`+0xfb0`), or the variable-time-step helper (`f_818f0584`) when the world has one | read |

### Inside Havok's step: the collide graph

The simulation is Havok's multithreaded one (vtable `0x848d9148`); its single-threaded collide and solve
(`hknpSingleThreadedSimulation`, `f_819bc260`, `f_819bf460`) have no hot rank. The collide graph is about 14 tasks from
`f_819923b8` plus the broad phase's own (its vtable `0x848da718`, slots 10 to 15). Order, from the builder and the labels:
the broad phase updates its tree and finds new pairs; the narrow phase is prepared, run (pairs and grid tasks),
sorted, gathered and finalised; Jacobians are built for the solver; deactivation keeps its caches. **read** (the tasks
and labels), **guess** (the exact edges)

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_819923b8` | `hknpMultithreadedSimulation::collide` | Slot 2 of the simulation's vtable, hot rank 718. Allocates and adds the collide tasks to the hknpTaskGraph (`hkTaskGraph::addTask`, `f_81700b90` and `f_817009dc`) and their edges (the array at `graph+0x428`): hknpPrepareNarrowPhaseTask, hknpPrepareNarrowPhasePairsTask, hknpProcessNarrowPhasePairsTask (one, `f_8191e80c`, with the simulation context's thread counts), hknpPrepareConstraintsTask, hknpProcessNarrowPhaseGridTask, hknpNarrowPhasePairsSortThreadOutputs, …PrepareGatherTask, …GatherThreadOutputsTask, …BuildJacobians, …Finalize, hknpBroadphaseOptimizeTask, hknpBuildConstraintJacobiansTask. It also calls slot 12 of `[world+0xbb0]` (**guess**: the broad phase adding its own tasks) | read |
| `f_8191e80c` | `hknpProcessNarrowPhasePairsTask_Construct(task, out, threads, w3)` | Hot rank 3377. The 0x1ed0-byte task (vtable `0x848d7b90`, through the GOT slot `0x84cc16c8`): per-thread outputs, and work ranges for `threads` (`f_81708bc0` at `+0x130`, up to 17 slots, the same helper the avoidance task uses). Its process is `f_8191ee00` ([jobs.md](jobs.md), rank 146), which claims body-pair work items by range and steals the rest | read |
| `f_8199f7c0` | `hknpPrepareNarrowPhaseTask::process` | Hot rank 902: "AllocateSolverData", "InitNarrowPhaseTasks" | read |
| `f_819a6820` | `hknpPrepareNarrowPhasePairsTask::processImpl` | Hot rank 1506: activation (activating bodies and their constraint groups), removing pairs whose constraints disable collision, creating new collision caches | read |
| `f_819a3d20` | `hknpProcessNarrowPhaseGridTask::process` | Hot rank 888: the narrow phase's grid form ("GridTask"), with outputs for moving, fixed and deferred manifolds. Which pairs go to the grid task and which to the pairs task was not traced | read |
| `f_81996780` | `hknpFinalizeNarrowPhaseGridTask::process` | Hot rank 1629 | read |
| `f_819ab300` | `hknpNarrowPhasePairsPrepareGatherTask::process` | Hot rank 599 | read |
| `f_819affa0` | `hknpNarrowPhasePairsSortThreadOutputs::process` | Hot rank 682: sorts the per-thread outputs of the pairs task | read |
| `f_819ad000` | `hknpNarrowPhasePairsGatherThreadOutputsTask::process` | Hot rank 1732 | read |
| `f_819aeae8` | `hknpNarrowPhasePairsBuildJacobians::process` | Hot rank 295: "NarrowPhase", "PairsTask", "Finish": the contacts' Jacobians for the solver | read |
| `f_819b25e0` | `hknpNarrowPhasePairsFinalize::process` | Hot rank 1673 | read |
| `f_8199c860` | `hknpCollideSyncTask::process` | Hot rank 976 ("CollideSync"): **guess**, the join between the narrow phase's parts | read |
| `f_81928de0` | `mergeAndCollide2StreamsIntern` | Hot rank 99: the narrow phase's body pairs ("collide_bodyPairs") | read |
| `f_819257c0` | `mergeAndCollide2StreamsIntern_B` | A second form of `f_81928de0` (the same signature string), hot rank 798 | read |
| `f_81a14200` | `hknpSimdTreeBroadPhaseTasks::CopyAabbsAndMarkActiveBodiesRecollide::process` | Hot rank 2539: the moving bodies' new AABBs into the broad phase's tree | read |
| `f_81a191a0` | `hknpSimdTreeBroadPhaseTasks::CollideTrees::process` | Hot rank 877: the tree-against-tree walk for new pairs ("ThreadCollisionPairs::m_newCachesOut") | read |
| `f_81a33440` | `SimdTree_FindPairs` | Hot rank 204: under the broad phase's pair finding (`f_81a15ec0`, "ThreadCollisionPairs::m_newCachesOut") | guess |
| `f_81a19f80` | `hknpSimdTreeBroadPhaseTasks::Finalize::process` | Hot rank 1048 | read |
| `f_8199d5b8` | `hknpBroadphaseOptimizeTask::process` | Hot rank 802: "IncrementalOptimize", the tree's incremental rebuild | read |
| `f_81a363c0` | `hkcdBuildRangesTask::process` | Hot rank 995: "hkcdSimdTree::BuildRanges", a SIMD tree's build in ranges | read |
| `f_81a38400` | `hkcdFinalizeTreeTask::process` | Hot rank 1794: the tree build's last step | read |
| `f_818623a0` | `CompoundShape_UpdateInstancesInner` | Hot rank 36, inside `f_81861e40` (2,680 instructions), called from `hknpCompoundShape::updateInstances` (`f_81857ba0`): a compound shape's instances and tree brought up to date | guess |
| `f_81730760` | `Hknp_ConvexCollide` | Hot rank 85, 2,064 instructions, with ten callers: the particle-particle solver (`f_818cc4a0`), the full cast's manifold generator (`f_818fa3c0`, "calcSweptTransform"), `f_8176c980` (rank 158) and others. A shared convex collision routine | guess |

### Inside Havok's step: the solver and deactivation

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_81993780` | `hknpMultithreadedSimulationEx::buildSolverFactoryTask` | Slot 3 of the simulation's vtable, hot rank 749 ("PreSolve") | read |
| `f_81995fc4` | `hknpSolverFactoryTask::process` | "Solver", no hot rank of its own. **guess**: makes the solve graph's tasks, as its name says | read |
| `f_819b5320` | `hknpPrepareSolverData::initPreSolve` | Hot rank 1034: the solver id table, the solver velocities, the deactivation step info ("PrepareDeactivationInfo"), the solver step info | read |
| `f_81997f00` | `hknpGenSolverTasksTask::process` | Hot rank 1935 ("GenerateSolverTasks") | read |
| `f_819b657c` | `BuildTaskGraphHelper::buildTaskGraphDependencies` | Hot rank 1929: the solve graph's edges ("BuildGraph") | read |
| `f_819a9348` | `hknpPrepareConstraintsTask::process` | Hot rank 2014: gathers and groups the constraints ("MiNumConstraints") | read |
| `f_819ba420` | `hknpStepConstraintJacobianProcess` | Hot rank 155: "SolveJacobians0" to "SolveJacobians3+" (**guess**: one label per substep), "SolveConstraints"; its arguments name a Jacobian grid type and cell indices, so the solve is split by cells of space | read |
| `f_8177b2a0` | `hknpStreamContactSolver::solveJacobians` | Hot rank 78: the contact solver ("FireModifiers") | read |
| `f_81785c60` | `ContactSolver_Inner` | Hot rank 30, inside `f_81785c00` (7,648 instructions), whose one caller is `solveJacobians`: the contact Jacobians' inner loop | guess |
| `f_819b8e20` | `GatherSolverVelocities` | Hot rank 119 ("GatherSolverVelocities") | read |
| `f_8182d2c0` | `hknpSolverIntegrator::subIntegrate` | Hot rank 382: a substep's integration ("SubIntegrate") | read |
| `f_8182dc20` | `hknpSolverIntegrator::subIntegrateLastStep` | Hot rank 183: the last substep's integration ("SubIntegrateLast") | read |
| `f_817d9d80` | `SolverIntegrator_Inner` | Hot rank 167; its one caller is `f_8182dc20` | guess |
| `f_81998a60` | `hknpMultithreadedSimulationEx::finalizeSolve` | Hot rank 565: "PostSolve", "UpdateDirtyStaticBodies", "Deactivation", "UpdateSpaceSplitter", "MergeSubIntegrateLastEventsAndCommands", "FinalizeSolve", "PrepareFullCast" (continuous collision for fast bodies) | read |
| `f_8199e714` | `hknpDeactivationMaintenanceTask::process` | Hot rank 1164: "Deactivation", "AddDeactivatedCaches", "PostCollideMaintenance" (its vtable word at `0x848d9798`) | read |
| `f_8199e040` | `hknpAddActiveBodyPairsTask::process` | Hot rank 2025 ("Deactivation/AddActiveBodyPairs"): pairs for bodies that woke up | read |

### The solver's time step

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_818f0374` | `hknpSolverInfo::setStepInfo(info, gravity, s0 = step, s1, w2 = substeps, w3 = microsteps)` | Hot rank 946, once a step from `preCollide`. Writes into the solver info (`hknpWorld+0xc30`): the counts and their inverses, the step and 1/step (`+0x60`, `+0x68`), the ratio of the step to the previous one (`+0x80`, from `+0x70`), the larger of `s1` and the step (`+0x78`), the substep's length and its inverse (`+0x90`, `+0x98`), gravity times the step and the substep (`+0xa0`, `+0xb0`), and values derived from tau and damping | read |
| `f_818f04a4` | `hknpVariableTimeStepUtil_Construct` | The 0x38-byte helper: 1/30 as its expected step, 0.6, 1.0, 4 and 8 as its defaults. Made only when `hknpWorldCinfo+0xf8` is set, which the game leaves at 0 | read |
| `f_818f051c` | `hknpVariableTimeStepUtil_Set(util, desc)` | Copies the cinfo's helper settings (`+0xfc`, `+0xd4`, `+0xd8`, `+0xdc`, `+0x100`, `+0x104`) and derives one value from them | read |
| `f_818f0584` | `hknpVariableTimeStepUtil_Apply(util, gravity, info, d0, d1)` | Sets the solver info from the frame's step, adapting it: the path a world with the helper takes instead of `f_818f0374`. Unused here | read |

### Havok's tasks on the game's pools

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80f6f430` | `HkTaskJob_Create` | Makes the job that runs a Havok task (vtable `0x84c71348`; the GOT slot `0x84cd8960` holds it less 16). Called from `f_80f56290` (a world), `f_80f6ecf0` (NavMeshCAStepDepWorker) and `f_8102f6e0` (NavMeshDepWorker) | read |
| `f_8004fe90` | `HkTaskJob_Invoke` | That job's run (slot 2): takes the next instance index (an atomic add on `+0x18`, for a job run several times) and calls `hkTask::processTask` on the task at `+0x10` with it as the input's `+0x18`. Pool A's loop (at 0x8004b690) and the pool B side (`f_80080000`, at 0x80080ec4) compare a job's run with this one and call `processTask` inline | read |
| `f_8004fee0` | `hkTask::processTask` | Hot rank 1405. Also called by Havok's own queues (`hkDefaultTaskQueueEx`, `f_816f4180`, `f_816f8fc0`; `hkTrivialTaskQueue::processUntilFinished`, `f_81725dc0`) | read |
| `f_839febe0` | `PhiveHkTask_Wrap` | "[Phive] hkTask": wraps Havok tasks for the navigation step (`f_80781ae0`), which then starts them with `JobGraph_Start` (`f_800711c0`, pool B); always one instance per task (at `0x839fed00`, [navigation.md](navigation.md)) | read |

### Bodies and queries

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_804f5030` | `RigidBody_CalcNextTransform` | Hot rank 126, 3,468 instructions, the strings "next_rot" and "next_position". Its one caller is `f_8037adb0`, the "no name" class (vtable `0x84c57190`, which also calls `f_838cd6d0`) | guess |
| `f_80158c10` | `RigidBody_UpdateTransforms` | Hot rank 215: "mLastTransform", "mPrevTransform"; called from 14 places, among them `f_80021ca0` (rank 9) and `f_80159dc0`, which reads the frame-time keeper's step (`+16`) | guess |
| `f_818f4f28` | `hknpWorld::castRay` | "WorldCastRay", hot rank 116: calls the broad phase's slot 26 (`+0xd0`) | read |
| `f_818f5450` | `hknpWorld::castShape` | "WorldCastShape", hot rank 189: the broad phase's slot 27 (`+0xd8`) | read |
| `f_818f5ec8` | `hknpWorld::getClosestPoints` | "WorldGetClosestPoints", hot rank 263: the broad phase's slot 29 | read |
| `f_818f64cc` | `hknpWorld::queryAabb` | "WorldQueryAabb" (the `hkArray<hknpBodyId>` form), hot rank 987 | read |
| `f_81a20780` | `hknpSimdTreeBroadPhase::castRay` | Slot 26 of the broad phase's vtable `0x848da718` (its slots 10 to 15 name `updateBodyAabbs`, `findNewPairs`, "SimdTreeTask"), hot rank 110 | read |
| `f_81a20b40` | `SimdTree_CastRay` | Hot rank 42, 984 instructions: the ray's walk through the broad phase's tree. Its one caller is `f_81a20780`; its only calls are indirect (`+0x30`, `+0x38` of an object it is given: **guess**, the collector) | read |
| `f_81a21aa0` | `hknpSimdTreeBroadPhase::castShape` | Slot 27, hot rank 152; calls `f_81a465e0` | read |
| `f_81a465e0` | `SimdTree_CastShape` | Hot rank 93: a shape cast's inner walk, called from `f_81a21aa0` and from `hknpShapeQueryInterface::castShape` | guess |
| `f_817575ec` | `hknpShapeQueryInterface::castShape` | "CastShape", hot rank 196 | read |
| `f_81757d54` | `hknpShapeQueryInterface::getClosestPoints` | "GetClosestPoints", hot rank 267 | read |
| `f_81a498a0` | `hknpMeshShape_vt22` | Slot 22 of hknpMeshShape's vtable `0x84cbfbe0`, hot rank 225. `hknpShapeQueryInterface::queryAabb` calls a shape's `+0xb0`: **guess**, the mesh's AABB query | read |
| `f_81a4a540` | `hknpMeshShape_vt23` | Slot 23, hot rank 63, 2,361 instructions: a mesh shape query (likely a ray or closest-point query, by its neighbours) | guess |
| `f_81a55380` | `hknpMeshShape_QueryInner` | Hot rank 113: called from slot 24 (`f_81a4ca24`, rank 230, which `f_839f7040` also calls directly) | guess |

### The async queries

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_806d4620` | `AsyncQueries_DrainJob(df)` | Hot rank 2098. The job `f_80380a20` makes after the step. Resets three of the current world's counters (`+0x1a8`, `+0x1b0`, `+0x1d8` → `+0x18`); with phase-mask bit 7, for world 0, drains the four queues of `[DF+0x128]` (`f_806d4510`, `f_806d4400`, `f_806d42f0`, `f_806d41e0`); then, for world 1, tail-calls `f_806d4720` ([frame-time.md](frame-time.md)) on `[[DF+0x10]+0x10]` | read |
| `f_806d4510` | `AsyncRayCastQueue_Drain(queue)` | Under the queue's mutex (`+0x8`) takes up to `+0x88` requests a frame from its list (`+0x30`, count `+0x38`), runs each (`f_8387ac40`), then calls the request's listener (`+0x190`, slot 0) and its delegate (`+0x198`) | read |
| `f_806d4400` | `AsyncShapeCastQueue_Drain(queue)` | The same for shape casts (the query `f_8387b070`) | read |
| `f_806d42f0` | `AsyncQueryAabbQueue_Drain(queue)` | The same for the entities' AABB queries (`f_8387c5c0`) | read |
| `f_806d41e0` | `AsyncSensorQueryAabbQueue_Drain(queue)` | The same for the sensors' AABB queries (`f_8387c5c0`), hot rank 3343 | read |

### Cloth

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8016cb20` | `ClothSystem_CreateWorld` | Builds the cloth system's hclWorld ("hclWorld", 0x168 bytes) and its collidables (hclSphereShape, hclPlaneShape, hclCollisionConvexes, "Collidable_Ground"). Its world description has byte 0 (the time-stepping mode) cleared, and `+0x20`/`+0x21` set | read |
| `f_80e66b2c` | `hclWorld_Construct(world, desc)` | The game's copy of Havok Cloth's world constructor: an hclConstraintStiffnessDispatcher (`+0x78`), always an hclWorldTimeStepUtil (`+0x158`, settings from `desc+4`), and the flag that turns it on (`+0x163`) from `desc+0`, which `f_8016cb20` sets to 0 | read |
| `f_80186a00` | `Actor_ClothUpdate(…, s0 = step)` | Hot rank 29. Called from GameActor slot 52 (`f_82c3fab0`, [actors.md](actors.md)) with that slot's step (`x1+0`), from Link's movement update (`f_82c415e0`), `f_802ca820` and `f_83af3580`. Calls the cloth step `f_80168320` and the target updates (`f_801881a0`, "target_mtx", "target_position") | read |
| `f_80168320` | `Cloth_Step(…, s0 = step)` | Hot rank 19. The game's cloth wrapper: prepares the actor's cloth instances and stores the step at the batch's `+0x134` and a factor at `+0x138`. Then it steps them (`f_8016b620`) or leaves the batch to the cloth job `f_8016b320` ([jobs.md](jobs.md)) | read |
| `f_8016b620` | `hclWorld::stepSingleThreaded(world, s0 = step, ctx, instances)` | Hot rank 20: "Cloth", "Execute Operators". Stores the step at `world+0x58` to `+0x64`, or, when the world's time-stepping helper (`+0x158`) is on (flag `+0x163`), hands it to `f_81de0c18` ("Interpolation Time-Stepping"). Fires the pre- and post-step signals and runs each instance's operators | read |
| `f_81dddcc0` | `hclWorld_InterpolationStep` | "Extrapolate transforms", "Store sim-cloth cache", "Back-interpolate sim positions", "Interpolate transforms": the fixed-step cloth's interpolation, which the game's setting leaves unused | read |
| `f_81b0e600` | `hclClothInstance::_preExecuteCloth` | Hot rank 670; warns "Cloth Setup was executed at runtime" | read |
| `f_81b47080` | `hclSimulateOperator::_execute` | Hot rank 21, inside `f_81b47078`: "Simulate", "Transfer Motion", "Drive Collidables", "Substep Collidables", the pre- and post-simulate signals | read |
| `f_81c66a60` | `hclSimulateOperator::_integrateTask` | Hot rank 117: "Integrate", "Actions" | read |
| `f_81b56de0` | `hclSimulateOperator::_solveTaskOrdered` | Hot rank 51: "Solve", the constraint sets in order | read |
| `f_81b58720` | `hclSimulateOperator::_collideAndSolveTask` | Hot rank 118: "Collide And Solve", "iterateCollidables pinch-enabled", "solve contacts" | read |
| `f_81b57a80` | `_iterateCollidables` | Hot rank 105 (`hclCollideAndSolveTask.cxx`): each particle against each collidable | read |
| `f_81b5a3c0` | `IterateCollidables_Inner` | Hot rank 50, 4,912 instructions; its one caller is `f_81b57a80` | guess |
| `f_81b8c000` | `IterateCollidables_Collide` | Hot rank 26, 17,448 instructions; its one caller is `f_81b5a3c0`: particles against a collidable's shape | guess |
| `f_81c86720` | `hclStandardLinkConstraintSet::apply` | Hot rank 66 | read |
| `f_81c7d2e0` | `hclBendLinkConstraintSet::apply` | Hot rank 147. The stretch links (`f_81c88940`, rank 302) and local ranges (`f_81c67fa0`, rank 477, its inner loop `f_81c7bd00` rank 129) are beside it | read |
| `f_81c7ecc0` | `hclBendStiffnessConstraintSet::apply` | Hot rank 186: "Bend Stiffness Constraints", and its rest-pose form | read |
| `f_81c7f880` | `BendStiffness_Inner` | Hot rank 40; its one caller is `f_81c7ecc0` | guess |
| `f_81b38800` | `hclSimClothInstance::updateAabbs` | Hot rank 91 | read |
| `f_81db9a40` | `hclObjectSpaceSkinPOperator::execute` | Hot rank 61, inside `f_81db9a00` (13,569 instructions): skinning the cloth's display mesh. The other skin operators' shared inner loop is `f_81cdff40` (rank 96) | read |
| `f_81ce6500` | `hclSimpleMeshBoneDeformOperator::execute` | Hot rank 157 | read |

### Navigation (Havok AI)

[navigation.md](navigation.md) has the navigation module, its task and the avoidance step; these rows are the ones it
links to.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80781ae0` | `NavStep_Run` | The navigation step (vtable word `0x84bba038`, on `[0x84eaedb0]`, through the GOT slot `0x84c9df58`). Steps the hkaiWorld (`f_81f2e278`) with the step it is handed (its argument's `+0`), runs the collision-avoidance system's step (`f_820cc4f4`) and `doSynchronousProcessingRun` (`f_81f2b500`), wraps their tasks (`f_839febe0`) and starts them on pool B (`f_800711c0`, [jobs.md](jobs.md)). That is the mode-1 path only: the game uses mode 0, where the step runs as tasks on the two NavMesh pools ([navigation.md](navigation.md)) | read |
| `f_81f2e278` | `hkaiWorld_Step(world, s0)` | Calls `doSynchronousProcessingRunST` (`f_81f2c97c`: silhouettes, path search, navigators) or the stepping-mode switch (`f_81f2df80`, "Unknown stepping mode") | guess |
| `f_81f2b500` | `hkaiWorld::doSynchronousProcessingRun` | Hot rank 812: "InitialProcessing", "Phases" | read |
| `f_820cc4f4` | `hkaiCollisionAvoidance::System::step` | The avoidance step, which makes the tasks `f_820da77c` runs ([jobs.md](jobs.md)) | read |
| `f_81e69500` | `hkaiNavMeshUtils::getNearbyBoundaries` | Hot rank 67 ("getNearbyBoundariesFlood"): a flood over the navigation mesh's faces around a character | read |
| `f_820def60` | `hkaiCollisionAvoidance::Solver::solve` | Hot rank 237 | read |
| `f_820e2520` | `AvoidanceSolver_Inner` | Hot rank 53: under `Solver::solve` (two calls down), through `f_820de560` (rank 123) from the character step | guess |
| `f_81ebbd80` | `hkaiCharacterUtil::resolvePositionOnNavMesh` | Hot rank 269 | read |
| `f_81f768a8` | `hkaiNavMeshStaticTreeFaceIterator::iterateAlongRay` | Hot rank 462 ("StaticTree::iterateAlongRay"); its walk is `f_81fbb000` (rank 94) | read |
| `f_81ebd620` | `hkaiUpOrientedRaycast::castBidirectionalRay` | Hot rank 451 ("StreamingCollectionMediator::castBidirectionalRay") | read |
| `f_81f8c974` | `hkaiNavMeshCollectionQueryMediator::getClosestPoint` | Hot rank 483 ("StreamingCollectionMediator::GCP") | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84ea7ff0` | `g_PhysicsRoot` | The physics root's holder (reached through the GOT slot `0x84c9df00`): `[[0x84ea7ff0]+0xc0]` is the Dynamics Framework, `+0x10` the per-core Havok contexts | read |
| `0x84c56788` | `DynamicsFramework_vtable` | The DF (0x500 bytes) | read |
| `0x84c58188` | `DynamicsFrameworkBase_vtable` | Its base, with the step | read |
| `0x84c56688` | `PhiveWorld_vtable` | A world wrapper, its hknpWorld at `+0xe0`: slot 15 names hknpWorldSnapshot, 17 sets the substeps, 18 tau and damping | read |
| `0x84c56878` | `DfPhaseJob_vtable` | The delegate the seven updateWorld jobs (and the async-query drain) run through | read |
| `0x84c863f0` | `PhysicsModuleJob_vtable` | The "PhysicsModule" job (at the module's `+200`; run `f_806a6610`) | read |
| `0x84c86798` | `PhysicsWorldJob0_vtable` | World 0's BaseProc job (run `f_806a4570`) | read |
| `0x84c867c0` | `PhysicsWorldJob1_vtable` | World 1's (run `f_806a4140`) | read |
| `0x84c71348` | `HkTaskJob_vtable` | A Havok task's job (run `f_8004fe90`) | read |
| `0x848da718` | `hknpSimdTreeBroadPhase_vtable` | The broad phase: slot 26 castRay, 27 castShape, 29 getClosestPoints | read |
| `0x84cbfbe0` | `hknpMeshShape_vtable` | The mesh shape (slots 22 to 24 its big queries) | read |
| `0x848d9148` | `hknpMultithreadedSimulation_vtable` | The simulation: slot 2 collide (`f_819923b8`), 3 the solver factory (`f_81993780`), 4 the monitor's statistics (`f_81994420`: step count, time step, bodies, constraints, contacts) | read |
| `0x848d7b90` | `hknpProcessNarrowPhasePairsTask_vtable` | The narrow phase's pairs task: slot 5 its process `f_8191ee00` | read |
| `0x83e60ac4` | `k_MaxPhysicsStep` | 0.999, the cap on the config's step | read |
| `0x84eb5fe0` | `g_PhysicsModule` | The PhysicsModule (also through the GOT slot `0x84ca29d8`): the module `f_806a6610` runs on; its `+0x44` and `+0x48` count the two world jobs' runs | read |

## Structures

### The Dynamics Framework (0x500 bytes, `f_838ce800`)

| Offset | Field | Sure |
|---|---|---|
| `+0x1c` | Dirty bits: when any of the low three is set, phase 1 applies the solver settings and clears them. No writer was found | read |
| `+0x20`, `+0x24` | The step, 1/60 (`f_83b89110`); `+0x24` is what each frame's step uses | read |
| `+0x28` | 1 / step | read |
| `+0x2c`, `+0x30` | The previous step and its inverse, copied from `+0x24`/`+0x28` each frame | read |
| `+0x38` | The frame step, in 60 fps frames (0.5 at 120 fps): substeps = `(int)(+0x38 × +0x244 + 0.001)` (LS's `host/oot_high_fps.h`) | read |
| `+0x4c` | Tau's scale (1.0), squared | read |
| `+0x58` | A time scale (1.0): the step input's second value is step × it when it is not 1. No writer found | read |
| `+0x64`, `+0x6c` | The phase mask and the world index of the update under way | read |
| `+0x128` | The async queries' holder (`f_80d36e60`) | read |
| `+0xc8`, `+0xd0` | World 0 and world 1 (the wrappers) | read |
| `+0x1a0` | Bit 0: step as one job, single-threaded | read |
| `+0x234`, `+0x23c` | Tau and damping, 0.6 each | read |
| `+0x244` | Solver steps of a 60 fps frame (an int): 4 at construction; 8 where LS's header saw it | read |
| `+0x2a8` to `+0x450` | The seven phase jobs' delegates and names | read |
| `+0x460`, `+0x468` | The job pool and context the frame's jobs go into | read |
| `+0x478` to `+0x4b8` | The seven pushed job records | read |
| `+0x4d0` | Their priority (0x8000) | read |
| `+0x4d4`, `+0x4d8` | The world index (0 or 1) and the phase mask (0x3ff, or 0x10c) | read |

### hknpWorld fields

| Offset | Field | Sure |
|---|---|---|
| `+0xc30` | The solver info (`f_818f0374`): tau, then its derived values | read |
| `+0xc40` | Solver damping | read |
| `+0xc48`, `+0xc4c` | Substeps (the cinfo's solver iterations) and microsteps (ints); `+0xcb8`, `+0xcbc` their inverses | read |
| `+0xc90`, `+0xc98` | The solver's time step (as the monitor reports it) and its inverse | read |
| `+0xca0` | A previous step: `+0xcb0` is the step over it | read |
| `+0xca8` | The larger of the step input's second value and the step | read |
| `+0xcc0`, `+0xcc8` | The substep's length and its inverse | read |
| `+0xd68` | The variable-time-step helper (null: the game does not make one) | read |
| `+0xfb0` | Gravity | read |
| `+0x1050` | The simulation context: its `+0` and `+0x10` are the thread counts the narrow phase's task is built for | read |

### hknpWorldCinfo (on `f_80f56290`'s stack; defaults `f_80f57fb8`)

`desc` is the world's description (`f_80f56290`'s second argument's `+0x38`).

| Offset | Field | Sure |
|---|---|---|
| `+0x10`, `+0x14` | Body capacity: 4096 by default; the game sets `desc+0x34` + 0x800 (+ 0x1800 for a type-1 world) | read |
| `+0x18`, `+0x1c`, `+0x20` | `desc+0x50`, `desc+0x5c` × 2, `desc+0x140` (**guess**: other capacities) | read |
| `+0x80`, `+0x90` | The broad phase's extent: the min and max of two corners in `desc+0xc` to `+0x20` | read |
| `+0xb8`, `+0xbc` | 0.05 and 0.005, times a world scale (`desc` holder's `+0x40` for a type-0 world). **guess**: the collision tolerance and a smaller tolerance | read |
| `+0xd4`, `+0xd8` | Solver tau and damping: 0.6 and 1.0 by default; the game sets `desc+0x70`, `desc+0x74` | read |
| `+0xdc`, `+0xe0` | Solver iterations (the world's substeps; 4 by default, the game sets `desc+0x6c`) and microsteps (1) | read |
| `+0xe4` | Deactivation on (1): the world makes an hknpDeactivationManager. The game keeps it | read |
| `+0xe5` | 1 by default; the game sets the inverse of the holder's `+0x28`. When 0 the world sets bit 7 of `+0xf48` | read |
| `+0xec` | A large limit by default (0x5f7ffff0); the game sets `desc+0xe8` | read |
| `+0xf8` | Use the variable-time-step helper: 0, and the game leaves it | read |
| `+0xfc`, `+0x100`, `+0x104` | The helper's settings: 1/30, 1, 8 | read |

### A cloth batch (`f_8016b320`, filled by `f_80168320`)

| Offset | Field | Sure |
|---|---|---|
| `+0x10` | The instance count | read |
| `+0x18` | The instances, 0x78 bytes each: `+0x0` the instance, `+0x4c` and `+0x50` its step factor (step × (`+0x138` × `+0x4c` + `+0x50`), at least 2⁻²³), `+0x70` a value above which it is not stepped (0x5000) | read |
| `+0x28` | The hclWorld | read |
| `+0x134` | The step | read |
| `+0x138` | A factor of the step | read |
| `+0x170` | The next instance to claim (atomic): every executor running the job takes instances until none are left | read |
| `+0x174`, `+0x198` | The count still running, and the event signalled when the last one ends | read |

## Notes

### How a frame's physics runs

1. **Stage 5, before StartBaseProc:** the PhysicsModule job (`f_806a6610` → `f_806a6620`) does the framework's
   per-frame preparation ([jobs.md](jobs.md)).
2. **The BaseProc phase:** the PhysicsSystemBaseProc's jobs (`f_806a4570`, `f_806a4140`; BaseProc phases 1 and 2,
   which [jobs.md](jobs.md)'s `0x83f90ae0` names `PostCalc` and `PostSensor` if the list counts from 0: **guess**)
   each push the seven updateWorld jobs for one world.
3. **Phase 3** runs Havok's `preCollide` inline (pending bodies, materials, the solver's step) and turns the collide
   graph's tasks into job records in the same frame graph: the broad phase, then the narrow phase's prepare, process,
   sort, gather and finalise tasks, the Jacobians and deactivation's maintenance. The game's "post collide 1/2/3" jobs
   follow, then the solve-tasks job (`f_803719e0`), which builds the solve graph (the solver factory, the per-cell
   Jacobian solves for each of the substeps, the substeps' integration, the last substep, `finalizeSolve` with
   deactivation and continuous collision) and pushes it the same way; then "post solve". **read** (the tasks, the labels),
   **guess** (the order inside each graph)
4. **The post-update phases** copy the step and make the frame's post-step jobs, among them the async-query drain
   (`f_806d4620`). [jobs.md](jobs.md) measured the narrow phase (`f_8191ee00`) at 14% of the BaseProcExecutors' running
   time, rarely alone, and 1.1% of the critical path.
5. **Cloth** runs in the actors' own jobs. Slot 52 → `f_80186a00` → `f_80168320` either steps an actor's cloth at
   once or fills a batch for `f_8016b320`, which several executors work through together. [jobs.md](jobs.md) measured
   that job at 9% of the executors' running time and 6.7% of the critical path.
6. **Navigation** runs on its own threads ([navigation.md](navigation.md)).

The job records Havok's tasks become have the layout [jobs.md](jobs.md) gives (*A job record*): the callable at `+0`,
the priority at `+12` (0x8000 here), the thread class 0x21 ("any") at `+16`, one instance at `+56`, and the unmet
dependencies at `+60`, linked with compare-and-swap. **read**

### Above 60 fps

- **The base step is 1/60 s, fixed.** `f_83b89110` writes 0.016666668 into the framework's config at 0x83b89578, the
  framework copies it once to `DF+0x24`, and phase 3 (and the single-threaded path) hand `DF+0x24` to Havok every
  frame. No other store to it was found, and the world has no variable-time-step helper. **read**
- **The frame time comes in through the step count.** Each update the solver's count is set to
  `(int)(frame step × the steps of a 60 fps frame + 0.001)` (`DF+0x38` the frame step, `DF+0x244` the steps;
  `f_838ceb80`, `f_838cfc10`). The count decides how far the world moves in a frame: cut short by the rounding down
  (8 × 0.49 is 3 at 120 fps), a moving platform slid out from under Link (issue 1798). LS rounds it up while the
  step is under 1, at least 1 and never under the game's (`hos_oot_solver_steps`, LS's `host/oot_high_fps.h`, which has the
  logic). **read** (from that header; the static reading had taken `DF+0x38` for a constant 1.0)
- **Above 240 fps** the game's own count would be 0 with 4 steps a frame; LS's rounding keeps it at 1. **read**
- **The step input's second value** is the step times `DF+0x58` when that is not 1, else 0; Havok's solver info keeps
  the larger of it and the step (`+0x78`). It is 0 here. **read**
- **Cloth** takes the actor update's step, so it follows the frame time: a batch's instances step by it times their
  factor, with a floor of 2⁻²³. Its fixed-rate mode is off. **read**
- **The async queries** serve up to their configured number of requests a frame per queue, so above 60 fps more a
  second. **read**
- **Counted per frame:** the two world jobs count their runs (`[0x84eb5fe0]+0x44`, `+0x48`, the module's); nothing seen
  uses the counts. **read**

### What costs, and the knobs

What the step's work scales with, from the tasks (**guess** unless marked):

- **The narrow phase** scales with the broad phase's overlapping pairs and their shapes (meshes and compounds cost
  most: the mesh queries rank 63 and 113, the compound update 36). It runs once per world per frame as **one job**
  (*In short* 4), so its time is one executor's, however many are idle.
- **The solver** scales with contacts and constraints times the substeps (4): the Jacobian solves (155, 78, 30), the
  velocity gather (119) and the integration (382, 183, 167) run once per substep. Its tasks are split by cells of space,
  so it spreads over the executors better than the narrow phase. **read** (the labels and cells)
- **The broad phase** scales with moving bodies (their new AABBs and the tree walk; ranks 204 to 2539), and its tree is
  optimised a little each step (`f_8199d5b8`).
- **Deactivation** is on (`hknpWorldCinfo+0xe4`): sleeping islands leave the solver and the narrow phase. **read** (that
  it is on)

Knobs a change could turn, none tried:

- **Fewer substeps:** `DF+0x244`, the steps of a 60 fps frame (`DF+0x38` is the frame step, not a knob); or the world
  config's `+0x6c`. Two substeps would roughly halve the solver's per-substep work. Risk: softer stacking, more jitter,
  fast bodies passing through thin ones.
- **More instances of the narrow phase's pairs task:** it is built for the step's thread count; giving only that task
  (identified by its vtable `0x848d7b90`) N instances in `f_80372400` would let N executors share it, as
  [navigation.md](navigation.md) proposes for the avoidance task. The narrow phase is rarely on the critical path, so the
  gain is in the executors' time, not the frame's, unless the BaseProc phase is what the frame waits on.
- **Fewer async queries a frame:** each queue's `+0x88` caps the requests it serves a frame; a lower cap delays answers
  (the AI's sensors ask through `AsyncQueryAabb(Sensor)`, [ai.md](ai.md)).
- **A larger collision tolerance** (`hknpWorldCinfo+0xb8`) makes more pairs into contacts earlier; a smaller one fewer,
  with a risk of late contacts.

### What is the game's and what is Havok's

Havok's code keeps its file paths (`../..\Cloth/…`, `../..\Physics/…`, `../..\Navigation/…`) and its timer labels:
`Tt…` begins a timer, `Et…` ends one, `Mi…`/`Mf…` report an int or a float, `Pd…` a category, `Lt`/`St` a labelled
section. A function with those is Havok's, named after the `hkResult …::…` string it carries. The game's layer has the
`[Phive] …` labels and the paths `D:/home/Project/Owl/App/Lib/sead/Phive/src/…` (two files:
`phCollectManifoldModifier.cpp`, `phLinearCastVsTriggerVolumeModifier.cpp`) and
`D:/home/Project/Owl/App/Lib/Havok/Public/HavokSDK…` (Havok headers compiled into the game's code, such as the game's
copies of `generateCollideTasks`, the hknpWorld and hclWorld constructors, and `hknpShapeQueryInterface`).

## Open questions

- How Havok divides `DF+0x24` over the per-update step count, and whether anything but the count follows the frame
  time; a timed falling object at a 60 and a 144 cap would confirm the physics keeps real time.
- Whether the character controllers (`phive::CharacterController`, Link's `[controller+40]+472` speed,
  [player.md](player.md)) move by the world's step or by the frame's: the multithreaded path's controller update
  was not found (the single-threaded one is `f_838ceb80`).
- Who sets the DF's dirty bits (`+0x1c`), and where the game writes the frame step into `DF+0x38` each update;
  what writes `DF+0x58` and `+0x4c`; the world configs' values (PhiveConfig was not read).
- What the two worlds are (world 1's job runs only in some modes of the proc), what `[DF+0x130]`, `[DF+0x170]` and
  `[DF+0x198]` are, and what sets the pause bit that drops the world step from the phase mask.
- Which pairs the narrow phase's grid task takes and which the pairs task, and whether Havok's own task queues would run
  a task more than once (the game's wrapper never does).
- The units of the step cloth gets (frames or seconds).
- ~~The navigation step's time~~: the collision-avoidance step takes none, and the mode the game uses never reads the
  step it is handed ([navigation.md](navigation.md)).
- ~~Where the async queries are drained~~: in the post-step job `f_806d4620`, up to each queue's limit a frame.
- ~~Whether the game gives the hclWorld a time-stepping helper~~: it makes one but leaves it off (`f_80e66b2c`).
- hknpMeshShape's slots 23 and 24 exactly, and what `f_81b8c000` collides against.
- Whether Havok's own thread pool (`hkDefaultTaskQueueEx`) ever runs, or only the game's pools.
