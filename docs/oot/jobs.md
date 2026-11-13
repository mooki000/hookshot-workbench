# The job phase

How the game spreads a frame's work over threads: its two thread pools, the job record and the per-frame job graphs, how
jobs become ready and how idle threads wait, how the main thread runs the frame around them, and what a profile shows
running on the frame's critical path. Looked at in November 2026, after the frame rate turned out to be bound by this
phase rather than by the replay.

**Longshot code:** [host/hos_thread.c](../../host/hos_thread.c) (the virtual cores, sleeps), [host/nn_os_sync.c](../../host/nn_os_sync.c)
(LightEvent), [scripts/crit.py](../../scripts/crit.py) (the critical path from an all-thread sample)
**See also:** [frame-time.md](frame-time.md) (the frame step and limiter), [maps.md](maps.md) (the BancModule thread, the
PlacementMgr), [graphics.md](graphics.md) (the boot, `f_83b7a2c0`)

## In short

1. **Two thread pools**, built on sead::Thread, each worker pinned to one core:
   - **Pool A**, lock-free: `BaseProcExecutor0`–`5` (cores 0–5), the actors' per-frame processes.
   - **Pool B**, a mutex-guarded heap: `ModuleSystemWorker1`–`5` (cores 1–5), and the navigation-mesh workers
     (`NavMeshCAStepDepWorker`, `NavMeshDepWorker`).
   **read**
2. **A frame's work is a fixed graph of jobs** (a DAG), built once at boot and run every frame: each job counts its
   unmet dependencies, and when one finishes it counts its dependents down; a job at zero is queued. The graph is a
   chain of **8 stages**, each a begin node, six slot jobs and an end node, so each stage is a fork and join of six and
   the stages run one after another. The actors run in stage 5, on the BaseProcExecutors. **read**
3. **The main thread joins pool B** for the module phase and runs the same scheduler loop as the workers (`f_8007fe60`);
   with no ready job it waits on its event, which is where the earlier profiles saw it "blocked". **read**
4. **In a standing scene (Temple of Time, about 32 fps) the main thread waits in that loop about 22 ms of a 31 ms
   frame, and for 58% of that time at most two job threads run** (on average 2.7 of 14): the frame is bound by its
   longest chain of dependent jobs, not by the number of threads. Havok AI's character step alone is about 3 ms of that
   chain. **tested** (one sampled run, *Measurements* below)

## Functions

### The frame's job graph

Built once by the ModuleSystem's setup (`f_83b797e0`) in two builders (ModuleSystem `+1240` for graph A, `+1248` for
graph B), into the graphs `f_80706be0` runs each frame (`+1224`, `+1232`). A stage k has a begin node and an end node and
six slot jobs between them, all with the no-op job vtable `0x84c36c88`; the slot jobs run `f_80083960`, which runs work
queued for that stage at run time (**guess**). Graph A holds stages 0 to 6, graph B stage 7. **read**

```
Graph A (the main thread and ModuleSystemWorker1-5)
 S0: B0 -> frame start: f_80764670 -> 6 x f_80adaba0 (one per core) -> f_80bb62f0 -> B1;   B0 -> 6 slots -> E0 -> B1
 S1..S4: Bk -> 6 slots -> Ek -> Bk+1   (and the modules' pre-jobs: Actor, EventFlow, Banc, two more -> E4)
 S5: B5 -> 6 slots -> E5;   B5 -> StartBaseProc
     EntryJobsBgGraphics -> PhysicsModule -> StartBaseProc
     EntryJobsBgGraphics -> SoundModule   -> StartBaseProc
     StartBaseProc -> RunBaseProc 0..5 (each pinned to its core) => BaseProcExecutor0-5 run the actors, and the
                      physics and world-manager system BaseProcs (Havok's tasks among them)
 S6: B6 -> 6 slots -> E6 -> CalcCommandEndJob <- StartBaseProc
Graph B: S7: B7 -> 6 slots -> E7
```

Each module (47 module vtables; the real start 24 bytes past the address a scan finds) gets three calls while the graphs
are built: vtable `+104` adds its jobs (graph A and the frame context), `+112` returns a "pre" job (linked to stage 4's
end), `+120` a graph B job (none found). Each module also has two built-in jobs: its calc job at `+16` (vtable
`0x84c84eb8`, run `f_83b78d80`: the module's vtable `+16`, recording its index for the current core) and a job at `+48`
(vtable `0x84b4a860`, run `f_83b78f50`: the module's vtable `+24`). Only four modules add jobs of their own: the Actor
module (BaseProc), PhysicsModule, SoundModule and one unnamed (empty). The UI's jobs (UIMulticoreUpdateJob,
UIScreenMulticoreUpdateEndJob, UIScreenUpdateJob_%d) are added elsewhere (`f_80b22980`). **read**

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83b797e0` | `ModuleSystem_SetupAndRun` | The ModuleSystem's slot 10: runs the boot phases (`f_83b7a2c0`), builds the job graphs (stage chains at 0x83b79fcc and 0x83b79fe0, stage 0's fork at 0x83b79ff8, nodes at 0x83b7a060 and 0x83b7a120), sets the delegate "ModuleSystem::moduleCalc_" (`f_80706be0`, at ModuleSystem `+592`), then calls the framework's run (slot `+32` of `[0x84eb5f40]`, at 0x83b7a188), which doesn't return | read |
| `f_81028640` | `FrameContext_Build` | Builds the frame context the stages live in (`[[0x84ea2de0]+208]`), from `f_836ce9d0`; `f_836d0880` fills it | read |
| `f_80b20010` | `JobGraphBuilder_LinkStages` | Links begin k → six slots → end k, then end k → begin k+1 | read |
| `f_80b208e0` | `JobGraphBuilder_AddNode(g, desc)` | Adds a node: `desc+8` the job, `+20` priority (0x8c, 0xb4), `+22`/`+24` a mask, `+29` the thread (0xff any), `+0`/`+1` a named-node index (`g+112+4*i`) | read |
| `f_80b206e0` | `JobGraphBuilder_AddEdge(g, from, to)` | Adds an edge | read |
| `f_80b20f80` | `JobGraphBuilder_AddEdgeFromNamed(g, i, to)` | Adds an edge from named node i | read |
| `f_83499af0` | `JobGraphBuilder_Named(g, i)` | Named node i | read |
| `f_80b20fe0` | `JobGraph_AddFrameStart` | Stage 0's fork: begin 0 → `f_80764670` → six per-core `f_80adaba0` → `f_80bb62f0` → begin 1. The fork is the effects' calc; the EffectModule's calc job (`f_83b525b0`) runs the same calc instead when bit 23 of the manager's `+0x1378` says so, which the hot list suggests in play ([effects.md](effects.md)) | read |
| `f_80083960` | `StageSlotJob_Run` | A stage slot job's run (about 1,500 lines, 13 indirect calls): **guess**, it runs the work queued for that stage and slot | read |
| `f_83b78d80` | `ModuleCalcJob_Run` | A module's calc job: calls the module's vtable `+16` | read |
| `f_83b78f50` | `ModuleJob48_Run` | A module's second job: calls its vtable `+24` | read |
| `f_80fb6860` | `ActorModule_Construct` | Makes the "Actor" module (vtable `0x84c7c9b8`) | read |
| `f_80b20be0` | `ActorModule_AddJobs` | The Actor module's jobs: EntryJobsBgGraphics, StartBaseProc, RunBaseProc (×6), CalcCommandEndJob, and AIGroupParallelCalc when bit 2 of `Actor+467` is clear | read |
| `f_80b1fe70` | `ActorAIGroupMgr_SetupJob` | Adds "AIGroupParallelCalc" (run `f_83b02580`) | read |
| `f_80b20ed0` | `PhysicsModule_AddJobs` | Adds the "PhysicsModule" job (at `+200`), in stage 5 before StartBaseProc | read |
| `f_80b23910` | `PhysicsModule_CreateSystemBaseProc` | Makes "PhysicsSystemBaseProc": the physics world step runs as a BaseProc, on the BaseProcExecutors | read |
| `f_80806790` | `WorldMgr_CreateSystemBaseProc` | Makes "WorldMgrSystemBaseProc" (the wm module), a BaseProc too | read |
| `f_80b1ff40` | `SoundModule_AddJobs` | Adds the "SoundModule" job, before StartBaseProc | read |
| `f_80b22980` | `UI_AddJobs` | Adds UIMulticoreUpdateJob, UIScreenMulticoreUpdateEndJob and UIScreenUpdateJob_%d; reached from `f_80b213f0`, which also names nodes 0x27–0x3f (which graph is not known) | read |

### Threads and pools

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8069dbe0` | `SeadThread_Construct` | Makes a sead::Thread: a message queue of 1, quit message 0x7fffffff | read |
| `f_834a3060` | `SeadThread_MessageLoop` | A thread's idle loop: `ReceiveLightMessageQueue`, then runs what the message asks | read |
| `f_834a2e50` | `SeadThread_Entry` | The thread entry above `f_80df25c0` and `f_80df2680` in every job thread's stack | guess |
| `f_80df2680` | `SeadThread_Run` | Calls the thread's run slot (`+112` of the vtable, slot 14) | guess |
| `f_8069b020` | `ThreadPoolA_Construct` | Builds a pool A: one worker per set bit of `cfg+36 & 0x3e`, or-ed with the byte at `cfg+16` (cores 1–5, and core 0 when the byte at `cfg+16` is 1), named `%s%u` (`0x83d7c024`) with the core, pinned to that core (`SetThreadCoreMask(t, -1, 1 << core)`, the mask kept at `thread+176`), priority `cfg+32` (16). Worker vtable `0x84c53338` | read |
| `f_8069aa20` | `ThreadPoolB_Construct` | The same for a pool B; worker vtable `0x84c532a8` | read |
| `f_80b5e9e0` | `BaseProcExecutor_Create` | Makes the "BaseProcExecutor" pool (A, cores 0–5: six threads) | read |
| `f_838f0e40` | `PhiveDepWorker_Create` | Makes "phive::DepWorker" (A); no such thread ran in the run measured (**guess**: an empty core mask) | read |
| `f_80dbcd70` | `ModuleSystemWorker_Create` | Makes "ModuleSystemWorker" (B, cores 1–5: five threads, stack 0x96000) from the "ThreadPoolHeap"; the pool is kept at ModuleSystem `+1208` | read |
| `f_80f6ecf0` | `NavMeshCAStepDepWorker_Create` | Makes the navigation-mesh character-step workers (B) | read |
| `f_8102f6e0` | `NavMeshDepWorker_Create` | Makes the navigation-mesh workers (B) | read |

### Pool A: the BaseProcExecutor

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8004aae0` | `PoolAWorker_Run` | Slot 14 of the worker vtable: on message 1, runs `f_8004ab40([this+512], this+216)`, then signals its done event (`this+488`) | read |
| `f_8004ab40` | `PoolA_WorkerLoop` | Takes jobs from the queues until the phase ends: the three priority levels in order, in each the re-push ring before the ready ring. Waits on its event (`WaitLightEvent` at 0x8004af4c) only when all six rings are empty. When the end job completes (`L_8004bc58`) it sets bit 1 of `S+388` and wakes everyone. It also runs a BaseProc job's process inline (from 0x8004b698): a mutex at `+56`, state flags at `+112`, calls through `[[0x84c9df00]]` | read |
| `f_8004fa40` | `PoolA_PushReady` | Queues a ready job by its `+12`: 0 to level 2, −2 to level 0, else level 1; then wakes waiters (`f_85786920`) | read |
| `f_85786920` | `AtomicNotify` | libc++'s atomic notify (its contention table at `0x8600bf80`) | read |
| `f_83acfb90` | `BaseProcExecutor_RunPhase` | Starts the BaseProc phase (sends the workers their message, at 0x83acfc98) and waits for it (`WaitLightEvent` at 0x83acfcf4); its caller doesn't run BaseProc jobs itself | read |
| `f_806afc40` | `PoolA_WaitPhase` | Waits for a pool A phase, joining it when `pool+260` bit 0 is set | read |
| `f_83b001c0` | `RunBaseProc_Invoke` | "RunBaseProc", six of them, job k pinned to thread k (functor vtable `0x84c7c968`): marks its core's bit in `Actor+232` and calls `f_83acfb90` on the manager at `[0x84eb1920]`, which wakes the BaseProcExecutors and waits. So during stage 5 the ModuleSystem participants sit in that wait while the executors run the actors | read |
| `f_83aff840` | `EntryJobsBgGraphics_Invoke` | "EntryJobsBgGraphics" (vtable `0x84c7c918`), before PhysicsModule and SoundModule: calls pool A helpers (`f_806b0e80`, `f_806b08a0`, `f_806affc0`) and `f_83acf9b0` | read |
| `f_83afff70` | `StartBaseProc_Invoke` | "StartBaseProc" (vtable `0x84c7c940`), after begin 5, PhysicsModule and SoundModule, before the six RunBaseProc | read |
| `f_83b00340` | `CalcCommandEndJob_Invoke` | "CalcCommandEndJob" (vtable `0x84c7c990`), after stage 6's end and StartBaseProc | read |

### Pool B: the ModuleSystem

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8007fe10` | `PoolBWorker_Run` | Slot 14 of the worker vtable: runs `f_8007fe60` | read |
| `f_8007fe60` | `PoolB_SchedulerLoop` | Claims ready jobs (the count at `S+200`, by compare-and-swap) from a heap ordered by `job+12` (`S+72`, count `S+84`, under the mutex at `S+88`) and runs them until the phase's done flag (`S+196` bit 1). With nothing ready it waits on its event (`WaitLightEvent` at 0x80080188); one designated participant sleeps instead (`SleepThread` of the tick count at `0x84e9f4f0`) | read |
| `f_80070ea0` | `JobGraph_Build(pool, S, G)` | Builds the frame's job records (64 bytes each, below) from a graph's node descriptors and links each to the end job (`S+128`; `S+188` counts the jobs left), then tail-calls `f_800711c0` | read |
| `f_800711c0` | `JobGraph_Start` | Queues the graph's root jobs and sends the workers their message (`TrySendLightMessageQueue`) | read |
| `f_80083350` | `PoolB_MakeReady` | Queues a job whose dependency count reached zero | read |
| `f_80070b20` | `PoolB_WaitPhase` | Waits for a pool B phase; when `pool+260` bit 0 is set the caller joins it and runs `f_8007fe60` on its own context (`pool+16`), then waits for each worker's done event | read |
| `f_80070d30` | `PoolB_RunPhase` | Runs a pool B phase and waits (`f_80070b20`); used by the limiter's path | read |

### The frame on the main thread

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_809667b0` | `Framework_Frame` | The frame, under `nnMain` (`f_81222ce0`, [graphics.md](graphics.md)) through `f_80f48c30`: calls `f_8348c680` | read |
| `f_8348c680` | `Framework_RunFrame` | The framework's frame: the calc tree (`f_802eabd0`), then the draw (`f_802eb020`) | read |
| `f_802eabd0` | `CalcTree_Run` | Runs the calc tree, recursing through `f_802eacc0` (four levels in the stacks) to the ModuleSystem's calc | read |
| `f_802eacc0` | `CalcTree_Node` | One level of the calc tree | read |
| `f_802eb020` | `DrawTree_Run` | The draw: through `f_8066fc80` and `f_80665980` to `f_806668b0` | read |
| `f_806668b0` | `Draw_Wait` | Where the draw waits (about 7% of the main thread's blocked time) | read |
| `f_80441560` | `FrameLimit_Phase` | From the limiter (`f_8042cd20`, [frame-time.md](frame-time.md)) through `f_80441630` to `f_80070d30`: one more pool B phase | read |

`f_80706be0` (`ModuleSystem_ModuleCalc`, its row in [frame-time.md](frame-time.md)) runs the module phase: it signals
`[0x84eb1920]+368`, sets bit 0x20 of the ModuleSystem's `+3384`, then builds and runs graph `+1232` and waits
(0x80706ff0/0x80706ff8), then graph `+1224` and waits (0x80707008/0x80707010); other branches run only one of the two.
**read**

### Jobs on the critical path

The jobs that run while the main thread waits in the module phase and at most two job threads run (*Measurements*).

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_820da77c` | `HkaiCharacterStep_Task` | Havok AI's character step, on `NavMeshCAStepDepWorker1`: gathers boundaries and solves the characters' steering ("TtAi", "TtGatherBoundariesAndSolveCharacters", "TtCollision", a "…SeedPointsTask::process"). One task, about 94% of that thread's running time. **Asynchronous:** nothing in a normal frame waits for it (the frame waits for the navigation task that starts it, `f_80781b00`); it runs beside the main thread's wait and competes for core 1 ([navigation.md](navigation.md)) | read |
| `f_8191ee00` | `HknpNarrowPhase_Task` | Havok Physics' narrow phase (`hknpProcessNarrowPhasePairsTask`), the biggest single job on the BaseProcExecutors (14% of their running time), rarely alone. One instance, so one thread, although Havok builds it (`f_8191e80c`, vtable `0x848d7b90`) with work ranges for several ([physics.md](physics.md)) | read |
| `f_800b3ce0` | `BaseProcJob_Run` | The BaseProcExecutors' second-biggest job (12% of their running time) and 18% of the chain: no strings, seven indirect calls | guess |
| `f_8006cd60` | `ModuleJob_Post` | A ModuleSystem job (15% of the workers' running time, 13.5% of the chain), with 27 indirect calls and the string "Post" | guess |
| `f_800998a0` | `ModuleJob_RunWithPoll` | A ModuleSystem job (25% of the workers' running time, 8% of the chain) that yields and polls with `SleepThread(tick / 50000)` (about 20 µs, at 0x8009a478); the main thread sleeps in it too | read |
| `f_80084de0` | `ModuleJob_Dispatch` | A large ModuleSystem job (173 indirect calls; 12% of the workers' running time) | guess |
| `f_8016b320` | `BaseProcJob_Small` | A BaseProc job, 9% of the executors' running time | guess |
| `f_80435300` | `ModuleJob_Misc` | A ModuleSystem job, 5% of the workers' running time | guess |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84ea2de0` | `g_FrameContextHolder` | Holds the object whose `+208` is the frame context the stages live in, reached through the GOT slot `0x84ca29c0` (its `+216` is the display-size object, [graphics.md](graphics.md)) | read |
| `0x84c36c88` | `NoOpJob_vtable` | The stages' begin, end and slot jobs' vtable (the slots' run is `f_80083960`) | read |
| `0x84c84eb8` | `ModuleCalcJob_vtable` | A module's calc job (run `f_83b78d80`) | read |
| `0x84c7c9b8` | `ActorModule_vtable` | The Actor module | read |
| `0x84c862d0` | `PhysicsModule_vtable` | PhysicsModule | read |
| `0x84c92c48` | `SoundModule_vtable` | SoundModule | read |
| `0x84eb1920` | `g_BaseProcMgr` | The BaseProc manager's holder; its pool A through `+128` → `+16` | read |
| `0x84c9df00` | `got_BaseProcMgr2` | The manager a BaseProc job's process is run through (`+192`, `+200`, `+224`) | read |
| `0x84c53338` | `PoolAWorker_vtable` | Pool A's worker vtable (slot 14 `f_8004aae0`) | read |
| `0x84c532a8` | `PoolBWorker_vtable` | Pool B's worker vtable (slot 14 `f_8007fe10`) | read |
| `0x84c7c968` | `BaseProcPhaseJob_vtable` | The functor that runs the BaseProc phase (invoke `f_83b001c0`) | read |
| `0x83cbe616` | `str_BaseProcExecutor` | "BaseProcExecutor" | read |
| `0x83daf1f9` | `str_ModuleSystemWorker` | "ModuleSystemWorker" | read |
| `0x83d7c024` | `str_ThreadNameFormat` | `%s%u`, a worker's name with its core | read |
| `0x83f90ae0` | `BaseProcPhaseNames` | The BaseProc phases: `PreCalc`, `PostCalc`, `PostSensor`, `PostUpdateMatrix` | read |
| `0x84e9f510` | `g_PoolASleepTicks` | The sleep of pool A's designated participant, in ticks, set at run time | read |
| `0x84e9f4f0` | `g_PoolBSleepTicks` | The same for pool B | read |

## Structures

### A job record (64 bytes, `f_80070ea0`)

Built each frame from a node descriptor `d`.

| Offset | Field | Sure |
|---|---|---|
| `+0` | The callable: a functor whose vtable `+16` invokes it (`d+8`) | read |
| `+8` | Its index | read |
| `+12` | Priority / queue class, s16 (`d+0`) | read |
| `+14` | A flag (`d+4 == 1`) | read |
| `+15` | `d+6` | read |
| `+16` | Thread class: 0x21 any, else `d+5 & 0x1f` (**guess**) | read |
| `+20` | A profiling tag (`d+20`) | read |
| `+24` | An optional push hook | read |
| `+32`, `+40`, `+48` | Its dependents: sentinel, first link, count | read |
| `+56` | Instance count (`d+2`), for a job run several times | read |
| `+60` | Unmet dependencies, counted down atomically | read |

### A job graph

| Offset | Field | Sure |
|---|---|---|
| `+8` | Node count | read |
| `+16` | Node descriptors | read |
| `+48` | Edge count | read |
| `+56` | Edges, `{from, to}` | read |

The ModuleSystem has two, at `+1224` and `+1232`.

### Pool A's scheduler (`S`)

| Offset | Field | Sure |
|---|---|---|
| `+64` | Three priority levels, 80 bytes each; in each two bounded MPMC rings of `{job, seq}` slots: the re-push ring at `+0` (count, array, head, tail) and the ready ring at `+40` | read |
| `+320` | The end job | read |
| `+384` | The participant that sleeps instead of waiting | read |
| `+388` | Bit 1: the phase is over | read |

### Pool B's scheduler (`S`, ModuleSystem `+1216`)

| Offset | Field | Sure |
|---|---|---|
| `+72` | The ready heap, ordered by `job+12`; its count at `+84` | read |
| `+88` | The nn::os mutex guarding it | read |
| `+128` | The end job; `+188` counts the jobs left | read |
| `+192` | The participant that sleeps instead of waiting | read |
| `+196` | Bit 1: the phase is over | read |
| `+200` | Jobs ready, claimed by compare-and-swap | read |

### A participant (`W`)

| Offset | Field | Sure |
|---|---|---|
| `+8` | 1 while asleep; a waker swaps it to 0 and signals `+176` | read |
| `+176` | Its LightEvent | read |
| `+216` | Its slot (matched against the scheduler's sleeper) | read |

## Notes

- Corrections from [drawing.md](drawing.md) (not yet folded into the rows above): `f_80084de0` ("ModuleJob_Dispatch") records one draw-list batch into its own command buffer; `f_8006cd60` ("ModuleJob_Post") records a type-0 draw task; `f_800998a0` runs bit-3 work (GPU buffers, through `f_8009bb80`); `f_80083960` dispatches by the mask at `job+0x10` (set by `f_836d0880`: bit 10 draw-list batches, bits 13 and 15 draw tasks) and carries the `agl::lyr::Renderer` begin/end labels; `f_806668b0` ("Draw_Wait") ends a command buffer, submits it and optionally waits on its fence. **read**
- From [hot-path.md](hot-path.md): `f_80435300` ("ModuleJob_Misc", on the critical path) lies inside the SoundModule's calc (`f_804352d0`, slot 2 of `0x84c92c48`). **read**
- From [physics.md](physics.md): `0x84c9df00` is the GOT slot holding `0x84ea7ff0`, the physics root; `[0x84eb5fe0]` is the PhysicsModule; `f_80080000` also runs Havok tasks inline. `f_8016b320` lets several executors share one cloth batch through an atomic index. **read**

- From [actors.md](actors.md): `0x84eb1920` is the Actor module's singleton (0x1f8 bytes, vtable `0x84c7c9b8`); the BaseProc manager proper is `[0x84eb0d90]`, which `f_83b001c0` passes to `f_83acfb90`. `f_800b3ce0` is the second of each actor's five jobs, calling actor slot 52 (`f_82c3fab0`), the game's main update. `f_8016b320` ("BaseProcJob_Small") is Havok Cloth's task (`f_8016b620`, "TtCloth"). **read**
- Other named threads: `AudioFrameworkThread` and `aal::LowPrioWorkerThread` ([audio.md](audio.md)), six `BaseProcCreateAndDeleteThread` ([actors.md](actors.md)), one `ParallelDecompThread` per loading slot, pinned to cores 5 down to 0 ([resources.md](resources.md)). **read**
### Waking and waiting

A pool wakes up to as many sleeping participants as it has jobs queued (0x8004b178 for A, 0x8008026c for B). Idle
participants wait on their LightEvent; one of them per pool sleeps a short tick count instead. A LightEvent on the host is
an SRWLOCK and a CONDITION_VARIABLE (`nn_os_sync.c`): a waiter spins a while (`spin_until_set`) and then sleeps, giving up
its virtual core. So every edge of the graph whose job lands on a sleeping thread costs a wake-up and, if the core is
held, a wait for it. **read**

### Pinning and Longshot's virtual cores

Every worker is pinned to one core, so `BaseProcExecutorN`, `ModuleSystemWorkerN` and a navigation-mesh worker on core N
share it. Longshot gives a guest thread one of six virtual cores while it runs guest code, as Horizon runs one thread
per core (`HOS_VCORES`, on by default: the game keys per-core data by core number). The game's pools stay at five or six
threads whatever the host has: the `0x3e` mask is in the game's code. **read**

### Measurements

One run: the test install (`dcaaaa88`), standing in the Temple of Time for 20 s (from 95 s to 115 s after start), serial
replay, Ryzen 9 3900X; `HOS_SAMPLE_ALL` every 2 ms (2,542 instants) read with `scripts/crit.py` and a job attribution of
the same samples (the frame just inside each pool's loop). **tested**

- **Frame:** about 32 fps, 31 ms.
- **The main thread** is blocked in 80% of the instants: 72% waiting in `f_8007fe60` (the module phase), 8% sleeping in
  `f_800998a0`, 7.6% in the draw's wait (`f_806668b0`), 7% in the BaseProc phase's wait (`f_83acfb90`).
- **Job threads running while it waits in the module phase:** none 8%, one 28%, two 22%, three 14%, four 9%, five 8%,
  six 10%, seven 1%. So 58% of the wait (about 13 ms a frame) has at most two job threads running.
- **What runs then** (share of those instants): the Havok AI character step `f_820da77c` 22%, `f_800b3ce0` 17.8%,
  `f_8006cd60` 13.5%, `f_800998a0` 8.3%, `f_8016b320` 6.7%, pool A's loop itself 3.9%, `f_80435300` 3.7%, and a long tail.
- **Running share by thread:** BaseProcExecutor0 30%, the others 20–24%; ModuleSystemWorker1–5 15–20%;
  NavMeshCAStepDepWorker1 22%; the main thread 20%; the Presentation Thread 24%.
- **Leaves while the main thread is blocked:** game code mostly, with the runtime's `obj` and `obj_lookup` (the guest
  sync objects' lookup, on every nn::os call) 7.5%, `spin_until_set` 3.4%, `hos_sleep_ns` 3%, `a64_fpx_mul32_fma32` 3%.
- **Virtual cores** (the log's `vcores:` line): about 7,500 waits a second, 44–54 µs on average (the longest 12–16 ms),
  about 3,500 kicks a second, no escapes: roughly 11 ms of thread time a frame spent waiting for a core.

Three more runs of the same scene, 140 s each, the fps from the log's 5-second lines after the scene settled: **tested**

- **Plain** (no diagnostics): 29.2 fps on average (27.6 to 30.6). The sampled run above averaged 31.8, so runs differ
  by about ±5% on their own.
- **Virtual cores off** (`HOS_VCORES=0`): 30.6 (28.7 to 32.1). No clear gain, within the runs' own spread: waiting for a
  virtual core is not what bounds the frame, though about 11 ms of thread time a frame goes to it. Both job loops ask
  for the current core number (`GetCurrentCoreNumber`: 6 calls in `f_8004ab40`, 18 in `f_8007fe60`, 326 in all the
  game's code), so the game needs one thread per core regardless.
- **Sleeps and waits** (`HOS_SLEEP_LOG`, `HOS_WAKE_LAT`; these slow the run, 23 to 30 fps):
  - The 20 µs poll in `f_800998a0` (`SleepThread` at 0x8009a478) runs about 15,600 times a second across the threads,
    each sleeping 23–24 µs: about 11 ms of thread time a frame asleep in it. The host's sleeps are accurate (20 µs asked,
    about 24 µs slept); the cost is the polling.
  - Wake-ups: from a signal to running took 12–19 µs on average for the BaseProcExecutors (5,000 to 8,000 wakes each in
    5 s, most of them asleep rather than spinning), 15–21 µs for the ModuleSystemWorkers (about 3,000 each), 15 µs for
    the main thread, 156 µs for `NavMeshDepWorker2`. About 12,000 wake-ups a second in all.
  - Other sleeps: 16 ms ones at 0x80032f28 (about 61 a second) and 100 ms ones at 0x80df26c0 (an idle sead::Thread).

A build of current `main` (`ebbba2ea`) runs the same scene faster (41–46 fps), and its profile has the same shape: the
main thread blocked 79.5% of the time, 70% in `f_8007fe60`; 58% of that wait with at most two job threads; on the chain
the Havok AI step 23.1%, `f_8006cd60` 13.9%, `f_800b3ce0` 13.6%, `f_800998a0` 11.1%. **tested**

An earlier run (2026-11-09, Kokiri Forest, about 40 fps) saw the same shape: workers idle 37% and waiting 24% of the
phase, about 3.2 game threads busy, 21% of the running time in runtime helpers (the floating-point exactness helpers
about 6%). **tested** (that run)

## Open questions

- What runs in each stage's six slots (the work `f_80083960` takes), and where the AI graphs and animation run.
- What the unnamed module with an empty job hook is, and the four other job adders (`f_80b20730`, `f_80b24db0`,
  `f_82e877f0`, `f_8389acd0`).
- What `f_800b3ce0`, `f_8006cd60` and `f_80084de0` run: which modules or BaseProc phases.
- ~~Whether the Havok AI character step can be split~~: it is written for several threads but given one (two literals:
  the pool's worker count and one instance per task); and the frame does not wait for it ([navigation.md](navigation.md)).
- Whether the main thread shares virtual core 0 with `BaseProcExecutor0`.
- Pool A's per-slot active counter (`W+216`/`W+224`, one nibble a slot), and the exact meaning of `job+16`.
- Why `phive::DepWorker` has no threads.
