# Effects

The game's visual effects: sparks, smoke, dust, fire, splashes and the like. They are NintendoWare's vfx3 library
(`nn::vfx3`), driven by xlink2's effect links (ELink: an event key on an actor or in a script becomes an emitter set) and
by the game's own effect manager and two modules. This page covers what the objects are, how an effect is created and
ends, what runs each frame and on which threads, where the GPU does the particle work, what the effects' time step is
(it matters above 60 fps), and the caps in the game's code and data. Looked at in November 2026 by static analysis of
the binary (no builds, no runs), because [hot-path.md](hot-path.md) found effects in 11 of the 200 hottest functions with
no page to explain them. Hot ranks are positions in `hot_order.txt` ([hot-path.md](hot-path.md)).

**LS code:** none yet. LS does nothing for effects in particular. Its renderer replays their compute dispatches
and instanced draws like any other, and the DLSS motion path treats particles as moving with the camera
(`gpu/vk/vk_temporal.cpp`).
**See also:** [hot-path.md](hot-path.md) (the ranks), [jobs.md](jobs.md) (stage 0's fork and the module calc jobs),
[drawing.md](drawing.md) (the slot jobs' work bits, the passes' recording, `Model(XluIndirectPtcl)`),
[rendering.md](rendering.md) (the passes, `CreateOccludedEffect`), [frame-time.md](frame-time.md) (the frame step),
[sequences.md](sequences.md) (the script nodes), [environment.md](environment.md) (the lens flare's occluded effect)

## In short

1. **Three layers.** NintendoWare vfx3 owns the emitter sets, emitters and particles (its code lies at about
   0x81250000–0x812f0000, with more above). The game's effect manager (a 0x1b68-byte singleton at `[0x84ea2ea8]`)
   holds the vfx3 system at `+0x1030`, the list of live emitter sets, and a time step for each of 16 groups. xlink2's
   ELink turns event keys into emitter sets, and updates each one's parameters every frame. Two ModuleSystem modules
   sit around them: the engine's "EffectModule" (resources, ELink, the parallel calc) and a game effect module (the
   group steps). **read**
2. **The calc runs in parallel over emitter sets.** Up to six threads take emitter sets one at a time with an atomic
   counter (`f_8007f100`, rank 296), and calculate each through `f_8007f340` (74) → `f_81270540` (77) → `f_812707c0`
   (72) → per emitter `f_8125f0e0` (34) → for CPU particles `f_8125ff80` (81). It can start two ways:
   - from the EffectModule's calc (`f_83b525b0`, rank 208), which queues up to five jobs and joins in itself;
   - from stage 0 of the frame's job graph, the "frame start" fork in [jobs.md](jobs.md): `f_80764670`, then six per-core
     `f_80adaba0`, then `f_80bb62f0`.
   Pool B's job runner `f_80080000` runs the same loop inline when a job's run is the calc's (`f_83b52550`), and
   `f_8007f6c0` holds a copy of it too. **read** Bit 23 of `manager+0x1378` stops the stage-0 path and lets the upload run;
   the upload is in `hot_order.txt` and the stage-0 jobs are not, so the first path is the one that ran in the profiled
   play. **guess**
3. **Where the particles are worked.** An emitter's calc type (`emitter+0x20`) decides it:
   - type 1: the CPU simulates every particle. Each live particle costs virtual calls into the emitter's modules
     (`f_8125ff80`);
   - type 2: a compute shader simulates them. The CPU only issues one `DispatchCompute` per emitter (`f_81260fc0`), sized
     by the emitter's capacity, not by how many particles are alive;
   - other types: the CPU only walks a ring of birth times and lifetimes to list the live particles. The vertex shader
     then places each particle from its age.

   In every type, each emitter's particle data and constants are copied into a GPU buffer every frame (`f_81260600`,
   rank 55), and particles are drawn as instanced quads. **read** (the code paths), **guess** (that the other types are
   the vertex-shader kind)
4. **ELink adds its own work.** Every live effect link is updated each frame: its matrix, its group, and its curves
   (alpha, emission rate and scale, life scale, colour, directional velocity). The work is shared out from a lock-free
   queue (`f_8012f9c0` → `f_80130600`, rank 60; per asset `f_80128d70`, rank 115). **read**
5. **The time input is the frame step, as a float.** The game effect module's calc (`f_82bee380`) writes each group's
   step every frame from the frame-time keeper of [frame-time.md](frame-time.md). An emitter's time (`+0xe8`) and a set's
   frame (`+0x390`) grow by that step. Air resistance is `powf(resistance, step)` (`f_81281850`). So effects follow the
   clock above 60 fps, and a frame at 144 fps costs as much CPU as one at 60. **read** Whether every module (emission
   intervals, random-per-frame terms) copes with a fractional step was not checked. **guess** that most do
6. **Caps exist in code and data.** The particle config's defaults are 1024 emitters, 256 emitter sets, 256 stripes and
   128 super stripes (`f_80cf7280`), and a `gsys__ParticleConfigParam` file can override them (`f_8359a060`). ELink
   refuses an emit when its limiter group is full ("emit limiter over"; `Effect/Setting/EffectLimiterGroupList/Default`).
   ELink instances can be clipped ("instance is clipped"). The contact-reaction tables merge emitters
   (`UnificationEmitterMax`, `IsEnabledEmitterMerge`) and space out requests (`RequestInterval`, `EmitInterval`). **read**
   No quality or LOD switch for effects as a whole was found. **guess**

## How it fits together

### Resources

The EffectModule (vtable `0x84c82468`, made by `f_80eee830`) sets up the vfx3 data heap (`NnVfxDataHeap`), the ELink
user and its link file (`ELink2/elink2.%s.belnk`), and two resource threads (`EffectResLoadingThread`,
`EffectResUnloadThread`), in `f_83b51a00`. Its slots `+72` (`f_83b50e10`) and `+184` (`f_83b53060`) name the
files. Effect files are emitter-set binaries named `.esetb.byml` and `.vsetb.byml`, listed by
`Effect/EffectFileInfo.%s.%s.byml` and loaded as `shaft::effect::EsetbResource`, with textures from `Tex/%s.bntx`,
effect models from `_Fxmdl.bfres` and the platform name `Nin_Ounce_NVN2`. The binary has no `.ptcl` file name. The only
`.esetlist` is `static.esetlist.xml` beside `EffectModels` (`f_811ca9c0`, `f_805d3b10`). **read** (the strings),
**guess** (which function loads which)

The vfx3 source files the binary names (in its allocation messages) show what the library has: `vfx3_System`,
`vfx3_RuntimeManager`, `vfx3_EmitterSet`, `vfx3_EmitterSetResource`, `vfx3_StaticMeshResource`, the channel and
expression managers (an expression language with a Lua back end, `LuaBackendMarker`), and four renderers:
`RendererModuleVfx2Particle`, `ConsolidatedStripe`, `HistoricalStripe1` and `HistoricalStripe2`. One emitter module is
`Vfx2Physics` (Gravity, GravityDirection, AirResistance, RandomMomentum). **read**

Effect models are drawn as models, not particles: the draw type `Model(XluIndirectPtcl)` is one of the 30 that
`f_80554440` cuts into batches ([drawing.md](drawing.md)). The model heap is `effectmodel_heap_size` in the particle
config. **read** (the names), **guess** (that this type is the effect models)

`CreateOccludedEffect` in [rendering.md](rendering.md)'s pipeline switches, and `agl__OccludedEffectMgrParam`, belong to
the lens flare and the sun's occlusion ([environment.md](environment.md)), not to vfx3. **read**

### Creating and ending an effect

- **From an actor.** Actors carry ELink components (ELink in the engine's component list, [actors.md](actors.md)), and
  their animations and states fire ELink keys. xlink2 finds the key's asset and runs its executor. The executor's
  activate (`f_80136b60`) checks whether the instance is clipped, then emits (`f_8012dae0`). The emit reads the asset's
  name (`@Blank` means no effect), lets a system and a user `effectPreEmit` callback veto it, checks the limiter group,
  and asks the particle system for the emitter set (`ptclSys->createEmitterSetID(AssetName=%s)`). The game's callbacks
  `game::effect::EffectCallbackProcessSet::eventCreate` (`f_82be5f90`) and `initializeEmitterSet_` (`f_809d0f50`) set the
  new set up. **read** (the strings and calls), **guess** (the order)
- **From scripts.** About 108 node class names hold Effect, ELink, Emit, Ptcl or Vfx (some are sound or perception
  emits). The generic effect ones:
  - `OneShotXLinkSearchAndEmit` (XLinkKeyName, ELinkCall, SLinkCall, HandleSet);
  - `ExecuteXLinkXlinkSearchAndEmit` (IsUseElink, IsCalcUpdate, IsFadeOnLeave, HandleElink);
  - `OneShotGenericEmitEventEffectInputActor` (Actor, ELinkKeyName);
  - `ExecuteEventEffectEmitAfterNode` (Actor, ELinkKey, Pos), which emits when the node is left;
  - `ExecuteVisibleOnOffEffect` (Key, Category, IsEmitOnEvent);
  - `OneShotEffectELinkHiddenSetting` (IsAdd, ActorName, KeyName; calc `f_828ecfd0`);
  - `OneShotSetStopEffect` (Ratio, ColorScale; calc `f_8295d7d0`);
  - `QueryXLinkIsEmitting`.

  The others are one boss's or enemy's, such as `ExecuteWallmasterAppearEffectEmit` and
  `OneShotFaroreWindEmitStartEffect`. **read** (the names and parameters)
- **Ending.** An emitter is finished when its modules report it (`f_812615c0`). A set dies (`set+0x1a`) once every
  emitter has finished and nothing else holds it (`set+0x1c`), at the end of `f_812707c0`. Before each set's calc, the
  vfx3 system's callbacks (the list at `+0xbf8`, slot `+0xd8`) may stop or fade it. A dead set's emitters are finalised
  by `f_81262600`, and the manager takes the set off its list at the next frame start (`f_80764670`, `f_80762dc0`).
  **read** (the flags), **guess** (the finalising and the removal)

### Each frame

1. **ELink**: the EffectModule's calc (`f_83b525b0`) calls the ELink system's calc (`f_8012f9c0`). That walks the users
   under a lock, then works the asset executors from a lock-free ring queue: alone, or with up to N jobs queued (N from
   `+0x91a`). Pool B's runner `f_80080000` runs the same job inline (at 0x80082564). **read**
2. **The emitter sets' calc**, by either path in *In short*. Before it starts, the manager prepares (**guess**: rebuilds
   its list of sets) and empties its per-group bits (`f_80764670` or `f_80762dc0`, both calling `f_80764040` and
   `f_80763da0`). Then each participant takes sets until the list runs out; in the module path the last one done signals
   a light event. Each set is
   calculated with its group's step: 0 if the group's bit in `+0x1414` is clear, and left out altogether if its bit in
   `+0x1418` is set. **read**
3. **Upload**: the slot jobs' bit-3 work (`f_800998a0`, [drawing.md](drawing.md)) calls `f_800ad2b0` when bit 23 of
   `manager+0x1378` is set. That walks the 16 groups' set lists and copies each live emitter's particle data into the
   frame's GPU buffer (index `vfx+0x2238`). **read**
4. **GPU calc**: the scene's recording (`f_80660320`) and its main render step (`f_80a47660`) call `f_80664210`. It
   dispatches the compute emitters that the calc collected per core (`vfx+0x4c0`, counts at `+0x4d8`). **read**
5. **Draw**: twelve pass functions call `f_8027c9e0`, among them `LocalDepthShadow::drawDepth`, the snapshot and
   `wet_mask`. It sets the view (`f_8027d040`), prepares each group in the pass's list (`f_8027ce80`) and draws them
   (`f_8027c880` → `f_81270d60` → `f_81261320`). **read**

## Functions

### The manager and the modules

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8087c140` | `EffectMgr_Create(heap)` | Makes the 0x1b68-byte effect manager once and keeps it at `0x84ea2ea8`; a sub-object at `+0x1b48` (vtable `0x84c37090`) is kept at `0x84ea2e08` | read |
| `f_8087c1d0` | `EffectMgr_Construct` | Clears the set list (`+0x18`, count `+0x1018`), the 16 group slots (`+0x1460`, 0x50 bytes each), the group masks (`+0x1414`, `+0x1418`) and the flags (`+0x1378`) | read |
| `f_80eee830` | `EffectModule_Construct` | Makes the engine's EffectModule: vtable `0x84c82468` | read |
| `f_83b51a00` | `EffectModule_Init` | Slot `+56`: makes `NnVfxDataHeap`, the ELink user (`ELinkUserCreate`, `ELink2/elink2.%s.belnk`) and the threads `EffectResLoadingThread` and `EffectResUnloadThread` | read |
| `f_83b525b0` | `EffectModule_Calc(mod, arg)` | Slot `+16`, run by the module's calc job ([jobs.md](jobs.md)): adds `step × tick frequency` to `+0xf0`, puts `arg+4` into the ELink system's `+0xa8`, runs the ELink calc (`f_8012f9c0`), timing each phase in µs (`+0x1a0`, `+0x1b0`, `+0x1b8`). When `+0x1c8` is set and the argument has a job queue, it prepares the set list (`f_80762dc0`), queues `min(+0x1ca − 1, 5)` jobs, runs the calc loop itself (`f_8007f100`), waits on the light event at `+0x228`, then finishes (`f_80073200`). Rank 208 | read |
| `f_83b52550` | `EffectCalcJob_Run(job)` | The queued job: runs `f_8007f100` on the manager, then counts `+0x200` down and signals `+0x228` when it is the last. Its delegate call (`f_83b55050`) runs the loop inline when the target is this function | read |
| `f_8087ae60` | `GameEffectModule_Init` | Slot `+56` of the game effect module (vtable `0x84b3fe90`): loads `System/GameEffect/EffectModuleParam/Default.game__effect__EffectModuleParam.bgym`. The parameter object beside its type name (vtable at `0x84b390e0`) visits cloud rendering fields (`CloudRenderingParam`, `CloudWeatherParamCloudy`…) and debug areas (`HyruleCastleCSCenter`, `KorokForestRadius`…): no effect caps were seen there (**guess**) | read |
| `f_82bee380` | `GameEffectModule_Calc` | Slot `+16`: writes the group steps. Groups 0 and 2, and group `[vfx+0x28] − 1`, get this core's scaled step from the frame-time keeper (`[[0x84c9e890]]`, slot `+0x20 + 0x30k`), plus a one-off extra (`[[0x84ca2cc0]]+0x1c0`, cleared after use). Group 1 gets the keeper's `+0x10`. Group 3 gets 0 or 0.0001. While bit 1 of `+0x300` is set, the steps come from `+0xeda0+0xd8` instead. Rank 488 | read |

### The emitter sets' calc

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80764670` | `EffectCalc_FrameStart` | Stage 0's first job ([jobs.md](jobs.md)). Does nothing while bit 7 of `+0x137a` is set. Otherwise it stamps the tick (`+0x1a00`), handles each group's pending entries (`f_836c06d0`), and stops there while bit 5 is set. Then it zeroes the take and done counters (`+0x1388`, `+0x138c`), rebuilds the list (`f_80764040`, `f_80763da0`) and clears the per-group bits (`+0x1394`, 0x44 bytes). Rank 1827 | read |
| `f_80adaba0` | `EffectCalc_StageJob` | One of stage 0's six per-core jobs: unless `+0x137a` has bits 0xa0, counts itself in at `+0x1388`, runs the loop (`f_80adac20`, a copy of `f_8007f100`) and counts itself out at `+0x138c` | read |
| `f_80bb62f0` | `EffectCalc_FrameEnd` | Stage 0's join: checks `+0x137a`, calls `f_80073200` (the calc's finish, also called by the module path: it checks `+0x1020` and `+0x1028` and calls `f_800733c0` for the groups) and stamps a tick | read |
| `f_80762dc0` | `EffectCalc_Prepare(mgr)` | The module path's preparation: the same steps as `f_80764670`, under locks. Both call `f_80764040` (by `+0x1378`, `f_807642a0` and `f_80764120`) and `f_80763da0` (per-core work with ticks); [drawing.md](drawing.md) lists those two as slot-job work too. Rank 1208 | read |
| `f_8007f100` | `EffectCalc_Worker(mgr)` | The parallel loop: while `+0x1020` is 1, takes set *i* by an atomic add on `+0x101c` (stops at `+0x1018`). It skips dead sets and groups masked in `+0x1418`. The step is the group's float at `+0x1420` if the group's bit in `+0x1414` is set, else 0. It marks the group's bit for this core in `+0x1394`, calls `f_8007f340(vfx, set, [+0x1368], core)` and adds one to the done count `+0x1028`. In mode 2 (`+0x1360`) only one group is calculated. Rank 296 | read |
| `f_8007f340` | `EffectCalc_EmitterSet(vfx, set, mask, core, step)` | Adds the set's counts to this core's 128-byte block at `vfx+0x68` (sets, emitters, particles of types 1, 2 and 3, sets and emitters calculated) and ORs the set's draw-path masks (`+0x38`, `+0x340`, `+0x348`) into per-group arrays. When the set's mask (`+0x358`) meets `mask`, it runs the system callbacks (`+0xbf8`), then either finalises a dead set's emitters (`f_81262600`) or calculates it (`f_81270540`). Rank 74 | read |
| `f_81270540` | `EmitterSet_Calc(set, ctx, core, step)` | First runs any start frames the set still owes (`+0x3ac`) in steps of at most 1.0: up to the next whole frame of `+0x390` (`fmodf`), then whole frames, then the rest. Then runs the frame's step (`f_812707c0`), marks the set's bit in a bit set (`+0x48`), and stores its calc time (`GetSystemTick`, `ConvertToTimeSpan`) at `ctx+0x30`. Rank 77 | read |
| `f_812707c0` | `EmitterSet_CalcStep(set, ctx, core, step, warm)` | For each emitter (`+0xc0`, count `+0xc8`): an inactive one only takes the set's matrix (`+0x70`, 64 bytes, into `+0xac0`) and adds the step to its time. An active one runs its modules' update (`f_812615c0`), puts a flagged type-2 emitter on this core's compute list, calls `f_8125f0e0`, and adds its particles to the set's counts by type (`+0x3c4`, `+0x3c8`, `+0x3cc`). Then adds the step to the set's frame `+0x390` (back to 0 past `+0x3b0`), and marks the set dead when every emitter has finished. Rank 72 | read |
| `f_812615c0` | `Emitter_UpdateModules(em, ctx, step)` | Calls slot `+0x390` of each of the emitter's modules (index list `+0x680`, count `+0x688`); 1 or 2 from any module ends or fades the emitter. Rank 687 | read |
| `f_8125f0e0` | `Emitter_Calc(em, ctx, step)` | One emitter's frame. It calls slot `+0x330`/`+0x358` of the flagged modules and slot `+0x338` (103) of each module in `+0x5f0`. The renderer modules (`+0x6b0`) get `+0x360` and `+0x4d8`. Then a type-1 emitter goes to `f_8125ff80` (and a type-2 one on frames when `ctx+0x50` is set). Any other walks its birth-time ring (`+0x970`, 8 bytes a particle, from `+0xcc` to `+0xc8`, modulo the capacity `+0x7c`), writes the live particles' indices into `+0x2c8` (16 bytes each) and their number into `+0xd0`. Then it calls the `+0x340`, `+0x368` and `+0x4e0` slots and, past the emitter's start time, emits (`f_81260460`). It is timed by ticks. Rank 34 | read |
| `f_8125ff80` | `Emitter_CalcParticlesCpu(em, ctx, step)` | The CPU particles. Clears the index list, then for every slot up to the capacity (`+0x7c`) tests whether the particle is alive (`0 ≤ time − birth < life`). For each live one it calls the modules' per-particle slots `+0x360` and `+0x368` (lists `+0x638`, `+0x650`) and the renderers' `+0x388` (`+0x6f8`), moves it, writes its index, and counts it at `ctx+0x40`. Rank 81 | read |
| `f_81281850` | `Vfx2Physics_CalcEmitter(mod, ctx, step)` | Slot 103 of `0x8488ad08`, the module with Gravity, GravityDirection, AirResistance and RandomMomentum (its resource reader is `f_81281620`): works out the frame's factors, `powf(air resistance, step)` among them (into `+0x98`). Rank 151 | read |

### ELink (xlink2)

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8012f9c0` | `ELinkSystem_Calc(sys, arg)` | When `+0x9c` is set: under the system's lock, walks the users' asset callers (`f_8012fd70`), then runs the executor queue (`f_80130600`). With `+0x918` set it queues jobs (count from `+0x91a`, through `f_80428800`), works the queue itself and waits on `+0x978`. Counts frames at `+0x998`. Rank 337 | read |
| `f_80130600` | `ELinkSystem_RunExecutors(sys, parallel)` | Takes asset executors from a bounded lock-free ring (`+0x78`, size `+0x70`, positions `+0x80`/`+0x88`) until it is empty. For each one it updates the matrix (`getMtxSetType`), group (`getGroupName`) and flags (`getAssetBitFlag_`) and calls the executor's calc. The last of the parallel participants signals `+0x978`. Rank 60 | read |
| `f_80128d70` | `ELinkAssetExecutor_Calc` | Slot 7 of the ELink asset executor vtable `0x84c3e180`. Reads the asset's curves and parameters each frame: clip type, matrix type, rotate source, force calc, alpha, emission rate, life scale, emission scale, directional velocity, emission interval, custom parameters. It hands them to the emitter set. Rank 115 | read |
| `f_80136b60` | `ELinkAssetExecutor_Activate` | Slot 18 of `0x84c3e180`: works out the clip type ("instance is clipped") and, if the instance is not clipped, emits (`f_8012dae0`). Rank 3261 | read |
| `f_8012dae0` | `ELinkAssetExecutor_Emit` | Creates the effect: the asset name (`@Blank`: none), the `effectPreEmit` system and user callbacks (either can veto), the limiter group ("LimiterGroup[%s] emit limiter over"), then `ptclSys->createEmitterSetID` (an error is logged if that fails), and the set's position, rotation and scale (`f_8012d320`). Rank 2048 | read |
| `f_82be5f90` | `EffectCallbackProcessSet_EventCreate` | `game::effect::EffectCallbackProcessSet::eventCreate`: the game's callback when an ELink event creates an effect (`EventCommonArg`) | read |
| `f_809d0f50` | `EffectCallbackProcessSet_InitEmitterSet` | `initializeEmitterSet_`: the game's set-up of a new emitter set (`EmitterSetArg`) | read |

### Script nodes

The node classes' frame is in [sequences.md](sequences.md): slot 39 is a one-shot's or query's calc, 29 to 31 an
execute node's OnEnter, OnUpdate and OnLeave.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80c2dbd0` | `OneShotXLinkSearchAndEmit_Calc` | Slot 39 of `0x84a63f38`: finds the XLink user (`f_804609e0`, `f_8045db00`) and emits the key for ELink, SLink or both (`f_80460630`, whose label is "emit(%s)") | read |
| `f_828ff050` | `OneShotGenericEmitEventEffectInputActor_Calc` | Slot 39 of `0x84a26010`: emits the event effect `ELinkKeyName` on `Actor` (`f_804dedd0`, `f_8045fd50`, under a lock) | read |
| `f_826ca9c0` | `ExecuteEventEffectEmitAfterNode_OnLeave` | Slot 31 of `0x849b6088`: emits `ELinkKey` on `Actor` at `Pos` when the node is left, through the same calls as `f_828ff050` | read |
| `f_828a37c0` | `ExecuteVisibleOnOffEffect_OnUpdate` | Slot 30 of `0x849fef18` (OnEnter `f_828a3770`): each update, turns the effect `Key` of `Category` on or off with the actor's visibility | guess |
| `f_828b3650` | `ExecuteXLinkSearchAndEmit_OnEnter` | Slot 29 of `0x84a03c18` (OnUpdate `f_828b3850` does the same): search and emit (`f_828b36c0`, labelled "searchAndEmit(%s)"). Its OnLeave is `f_828b38c0` (**guess**: the fade of `IsFadeOnLeave`) | read |

### GPU buffers, compute and draw

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_800ad2b0` | `EffectMgr_UploadAll(mgr)` | From the slot jobs' bit-3 work (`f_800998a0`, at 0x8009b050) when bit 23 of `+0x1378` is set: stamps a tick (`+0x1a10`), then `f_800ad840` for groups 0 to 15 with the system's buffer index (`vfx+0x2238`). Rank 251 | read |
| `f_800ad840` | `EffectGroup_Upload(vfx, group, buf)` | The group's live, not dead sets (list at `vfx+0x20`, ranges at `vfx+0x38`): `f_81270c20` for each, prefetching the next. Rank 214 | read |
| `f_81270c20` | `EmitterSet_Upload(set, buf, ctx)` | For each active emitter: `f_81260600`; then gathers the emitters that will draw into `+0xf0`. Rank 192 | read |
| `f_81260600` | `Emitter_Upload(em, ctx)` | Copies the emitter's changed particle bytes (`+0x128`, range `+0x140`–`+0x144`) and its live-index list (`+0x2c8`, 16 bytes a particle) into buffer `buf` of its GPU memory (`memcpy`, then a flush, `f_812bceb0`). It writes the emitter's constant block (matrices and colours, 16-byte stores) and calls its modules' `+0x378`/`+0x2b0` and renderers' `+0x398`/`+0x4f0`. Rank 55 | read |
| `f_80664210` | `Effects_GpuCalc` | Called by the scene's recording (`f_80660320`) and its main render step (`f_80a47660`, [drawing.md](drawing.md)): sets the view (`f_83539260`) and dispatches the compute emitters (`f_80664620`). Rank 813 | read |
| `f_80664620` | `Effects_DispatchCompute(vfx, cmd, …)` | For each core's compute list (`vfx+0x4c0`, counts `+0x4d8`): each active emitter goes through the system's filters (`+0xe68`, `+0xd18`), and type-2 emitters get `f_81260fc0`. Rank 505 | read |
| `f_81260fc0` | `Emitter_DispatchCompute(em, cmd, ctx)` | When the emitter's time moved since its last dispatch (`+0xdc`) and its mask matches: binds its compute program, two constant blocks and its particle storage (this frame's part of `+0x510`). Its modules (`+0x380`) and renderers (`+0x3a0`, `+0x4f8`) bind theirs. Then `DispatchCompute(⌈capacity / group size⌉, 1, 1)` through vfx3's wrapper `f_812bd7f0`. Rank 400 | read |
| `f_8027c9e0` | `Effects_DrawPass` | The effects' draw for one pass (12 pass functions call it): view parameters (`f_8027d040`, `f_8027cde0`), each group in the pass's list (`f_8027ce80`), then the draw (`f_8027c880`) with the frame's buffer index. Rank 161 | read |
| `f_8027d040` | `Effects_SetView` | From two 4×4 matrices and the target size: the view block the effects draw with (float work, and a call to `f_814e6be0`). Rank 136 | guess |
| `f_8027ce80` | `Effects_PrepareGroup` | Per group of the pass: through `f_81271130` and `f_812b9c10`, gets the group's sets ready to draw (sorting, **guess**). Rank 335 | guess |
| `f_8027c880` | `EffectGroup_Draw` | The group's sets (`vfx+0x50`): `f_81270d60` for each. Rank 243 | read |
| `f_81270d60` | `EmitterSet_Draw` | Skips a dead set or one flagged at `+0x19`; sets up the set's draw state (`f_81272400`, `f_812721d0`, the system's callbacks), then `f_81261320` for each emitter gathered in `+0xf0`. Rank 180 | read |
| `f_81261320` | `Emitter_Draw` | Calls its renderers' draw slots (`+0x2b8`, `+0x3b0`, `+0x300`, `+0x500`–`+0x510`) between the system's before and after callbacks. Rank 217 | read |
| `f_81291e00` | `Vfx2ParticleRenderer_Draw` | Slot 3 of `0x84895ec0`: for each range of particles (`f_81291800`), one `DrawArraysInstanced` of a 4-vertex quad, one instance a particle. Rank 332 | read |
| `f_812bd870` | `Vfx3Gfx_DrawArraysInstanced` | vfx3's wrapper over `nvnCommandBufferDrawArraysInstanced` (the command buffer at `+0xd0`) | read |

### Caps and settings

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80cf7280` | `ParticleConfig_Construct` | The `ptclconf` object ("ParticleConfig"), with defaults: `vfx3_resource_heap_size` 0x57800, `max_emitter` 1024, `max_emitter_set` 256, `max_super_stripe` 128, `max_stripe` 256, `max_resource` 256, `dynamic_heap_size` 3072, `memory_pool_heap_size` 5120, `double_buffer_size` 256, `effectmodel_heap_size` 64, `enable_particle_lineardepth` on, `light_map_name_0`–`7` | read |
| `f_8359a060` | `ParticleConfigParam_Visit` | `gsys__ParticleConfigParam`'s fields, the same set as a file's (`MaxEmitter`, `MaxEmitterSet`, `MaxSuperStripe`, `MaxStripe`, `MaxResource`, the heap sizes, `DoubleBufferSize`, `EnableParticleLineardepth`, `LightMapName0`–`7`): the game's data can set them | read |
| `f_83b55390` | `EffectLimiterGroupList_Load` | Loads `Effect/Setting/EffectLimiterGroupList/Default` (`engine__effect__EffectLimiterGroupList`): the limiter groups ELink's emit checks | read |
| `f_83c3d7f0` | `ReactionTableCell_Visit` | A cell of `engine__reaction__TableParam` (the contact reactions): `IsHold`, `HoldEmitInterval`, `IsEnabledUnification`, `UnificationEmitterMax`, `UnificationEmitterBaseRadius`, `UnificationEmitterDistanceRate`, `ThresholdContactSpeed`, `LimiterSetting`, `EmitGroup`, `RequestInterval`, `EmitInterval`, `IsHighestPriorityOnly`, `EmitRequestPriority`, `IsEnabledEmitterMerge` | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84ea2ea8` | `g_EffectMgr` | The effect manager (0x1b68 bytes), made by `f_8087c140`; GOT slot `0x84ca2bc8` | read |
| `0x84c82468` | `EffectModule_vtable` | The engine's EffectModule: `+16` calc `f_83b525b0`, `+56` `f_83b51a00`, `+72` `f_83b50e10`, `+184` `f_83b53060`; its job hook (`+104`) is the empty default | read |
| `0x84b3fe90` | `GameEffectModule_vtable` | The game effect module: `+16` calc `f_82bee380`, `+56` `f_8087ae60` | read |
| `0x84c3e180` | `ELinkAssetExecutor_vtable` | The ELink asset executor: slot 7 `f_80128d70` (calc), slot 18 `f_80136b60` (activate) | guess |
| `0x8488ad08` | `Vfx2PhysicsModule_vtable` | The vfx3 emitter module with gravity and air resistance: slot 103 `f_81281850` | guess |

## Structures

### The effect manager (0x1b68 bytes, `[0x84ea2ea8]`)

| Offset | Field | Sure |
|---|---|---|
| `+0x18` | The live emitter sets (pointers; the count follows at `+0x1018`, so room for 512: **guess**) | read |
| `+0x1018` | Their count | read |
| `+0x101c` | The calc's take counter (atomic) | read |
| `+0x1020` | The calc is open (byte, 1) | read |
| `+0x1028` | The calc's done counter (atomic) | read |
| `+0x1030` | The vfx3 system | read |
| `+0x1360` | Calc mode: 1 gives the groups in `+0x141c` step 0, 2 calculates one group only | read |
| `+0x1368` | The calc mask a set's `+0x358` must meet (set by the EffectModule's calc) | read |
| `+0x1378` | Flags: bit 23 selects the upload in the slot jobs and stops the stage-0 path; bit 21 stops it after the group step | read |
| `+0x1388`, `+0x138c` | The stage-0 jobs in and out | read |
| `+0x1394` | Per group, the cores that calculated it (16 × 32 bits) | read |
| `+0x13d4` | The groups calculated this frame | read |
| `+0x1414` | The groups whose time runs (bit clear: step 0) | read |
| `+0x1418` | The groups not calculated at all | read |
| `+0x1420` | The 16 groups' steps (floats), written by `f_82bee380` | read |
| `+0x1460` | 16 group slots of 0x50 bytes (count at `+0x40` of each) | read |

### An emitter set (vfx3)

| Offset | Field | Sure |
|---|---|---|
| `+0x1a` | Dead (byte) | read |
| `+0x1c` | Holds the set alive while above 0 | guess |
| `+0x5c` | Its index in the system's bit sets | read |
| `+0x60` | Its group (0–15) | read |
| `+0x70` | Its matrix (64 bytes, copied to inactive emitters) | read |
| `+0xc0`, `+0xc8` | Its emitters and their count | read |
| `+0x358` | The mask the manager's `+0x1368` must meet for a calc | read |
| `+0x390` | Its frame (float), plus the step each frame; back to 0 past `+0x3b0` | read |
| `+0x3ac` | Start frames still to run (float), worked off in steps of at most 1 | read |
| `+0x3c0`, `+0x3d0` | Emitters counted, emitters calculated this frame | read |
| `+0x3c4`, `+0x3c8`, `+0x3cc` | This frame's particles of types 1, 2 and 3 | read |

### An emitter (vfx3)

| Offset | Field | Sure |
|---|---|---|
| `+0x20` | Calc type: 1 CPU particles, 2 compute, others birth-time only | read |
| `+0x24`, `+0x25` | Active, finished (bytes) | read |
| `+0x38`, `+0x50` | Its modules and its renderer modules (by index lists further on) | read |
| `+0x7c` | Capacity: the particle ring's size and the compute dispatch's size | read |
| `+0xc8`, `+0xcc` | The particle ring's two ends | read |
| `+0xd0` | Live particles this frame | read |
| `+0xdc`, `+0xe0` | Time not yet simulated on the GPU; its time at the last dispatch | read |
| `+0xe8` | Its time, in frames (float) | read |
| `+0x2c8`, `+0x2d0` | The live-index list (16 bytes each) and its size | read |
| `+0x970` | The birth-time ring (birth as a float, life as an int: 8 bytes each) | read |
| `+0xac0` | Its matrix (64 bytes) | read |

## Notes

### What costs, and how it scales

- **Per frame, per emitter set**: a pass through the system callbacks and one call per emitter; an inactive emitter
  costs a 64-byte copy. These are the ranks 72 to 77 functions. **read**
- **Per active emitter**: virtual calls into each of its modules (several slots each), its emission, and the copy into
  GPU memory: the changed bytes plus 16 bytes per live particle, and its constant block. These are ranks 34 and 55.
  Since every live emitter is copied every frame, the copy scales with live particles. **read**
- **CPU emitters (type 1)** add a loop over the whole capacity, and for each live particle two module slots and one
  renderer slot, all virtual (rank 81, and module code such as rank 151 beside it). Their cost grows with capacity
  and live particles times modules. **read**
- **Compute emitters (type 2)** cost the CPU one dispatch each, but the GPU simulates the whole capacity whatever is
  alive. **read**
- **The draw** costs one or more instanced draws per emitter per pass that draws effects, plus the view set-up per pass
  (rank 136). Shadow passes and the wet mask call the effects' draw too. Whether they skip groups with nothing for them
  (the per-group draw-path masks `f_8007f340` gathers) was not followed. **read** (the calls), **guess** (the skip)
- **ELink** costs per live link per frame, whether or not its emitters show (ranks 60, 115). **read**
- All of this is per frame: at 144 fps it runs 2.4 times as often as at 60. **read**

### The two calc paths

Stage 0's fork in [jobs.md](jobs.md) (`f_80764670` → six `f_80adaba0` → `f_80bb62f0`) and the EffectModule's calc
(`f_83b525b0` with `f_83b52550` jobs) run the same loop on the same manager. Bit 23 of `+0x1378` stops the stage-0
path (with bit 21) and is the only way into the upload (`f_800ad2b0`, rank 251). The stage-0 functions are not in
`hot_order.txt`, while `f_83b525b0` (208), `f_8007f100` (296) and the upload are, so bit 23 was set in the profiled play
and the module path ran. What sets the bits was not found. **read** (the code), **guess** (which runs)

### Time above 60 fps

The step reaches every part of the calc as a float, and the time-dependent code read here handles fractions: the
emitter's time, the set's frame and its loop, the start frames (worked off in steps of at most 1 and a remainder), the
GPU's pending time (`+0xdc`), and air resistance (`powf`). **read** The emission modules and the per-particle module
slots were not read: a site that turns the step into whole frames (as #1832 did) could be in them. The ELink executors
read their curves by the set's frame. **guess**

## Optimization notes

Levers, each with where it would act and its risk. None has been tried.

- **Calculate emitter sets every other frame above 60 fps.** Give a group twice the step on one frame and set its bit
  in `+0x1418` on the next (`f_8007f100`, `f_82bee380`). That halves ranks 34 to 81 at 120 fps and above. Risk: the
  copy into GPU memory (`f_81260600`) would show the same particles twice, so effects would move at half the frame rate
  (judder), and an effect a script waits on could end a frame late. **guess**
- **Fewer live emitters.** Lower `max_emitter` and `max_emitter_set` (defaults 1024 and 256, `f_80cf7280`; or a
  `gsys__ParticleConfigParam` file, `f_8359a060`), or make the ELink limiter groups smaller
  (`EffectLimiterGroupList`). The cost goes down with each emitter left out. Risk: effects silently missing, and which
  ones depends on the order of creation. **guess**
- **Fewer particles.** Scale every ELink asset's emission rate or emission scale down where the executor reads it
  (`f_80128d70`). CPU particles cost per live particle (rank 81), and every emitter's copy scales with live particles
  (rank 55). Risk: sparse effects. Low for gameplay, since effects are drawn, not simulated into the game. **guess**
- **Skip effects off screen.** ELink already clips instances when they activate (`f_80136b60`, the clip type), but
  nothing found tests an emitter set against the view each frame. A test in `f_8007f340` on the set's matrix
  (`+0x70`) and a radius could skip the calc (as step 0 does) and the copy. Risk: needs a bound per set, which was not
  found; a skipped set would jump forward when it shows again unless its time keeps running. **guess**
- **Skip effects in passes that cannot show them.** `f_8027c9e0` runs for the local depth shadows, the snapshot and the
  wet mask as well as the scene. If a group has no emitters for a pass's draw path (the masks `f_8007f340` gathers),
  its view set-up (`f_8027d040`, rank 136) and group walk could be left out. Risk: low if the masks mean what they seem;
  an effect that should cast a shadow would lose it. **guess**
- **Use more cores.** The module path queues `min(+0x1ca − 1, 5)` jobs (`f_83b525b0`) and the ELink calc
  `+0x91a` (`f_8012f9c0`). On a PC with more cores, more participants would shorten the calc, if it is on the critical
  path ([jobs.md](jobs.md)). Risk: low; the work is already shared by atomic counters. **read** (the counts), **guess**
  (the gain)
- **Cheaper module calls.** The per-particle and per-emitter module slots are virtual calls in lifted code. A host fast
  path for the commonest modules (`Vfx2Physics`, `f_81281850`) would cut the CPU emitters' cost. Risk: high: a fast path
  that differs from the game's maths changes how particles move. **guess**

## Open questions

- Which calc path runs in play, and what sets bits 21 and 23 of `+0x1378` (the stage-0 path or the EffectModule's).
- Which emitters are of which calc type in practice (the share of CPU particles), and the meaning of types 0 and 3.
- Where the effect calc lies on the frame's critical path ([jobs.md](jobs.md)'s measurement did not name it): stage 0
  runs before the actors, so whether effects follow last frame's bone matrices.
- Whether the emission modules and the ELink curves cope with a fractional step above 60 fps.
- The fields of `engine__effect__EffectLimiterGroup` (`f_80c80cd0` visits them by hash), and the limiter groups' sizes in
  the game's data.
- Whether `gsys__ParticleConfigParam` is set in the game's data, and to what.
- What `f_8027ce80` does per group (sorting by depth, `EmitterSetSortingData` and `PerEmitterSortedIndices` are names the
  binary has), and whether `f_8027c9e0` skips groups with nothing for a pass.
- The unidentified library at 0x814ee000–0x81502000 ([hot-path.md](hot-path.md)) lies beside vfx3's expression
  evaluator (`f_814e52a0`) and the view set-up calls into it (`f_814e6be0` from `f_8027d040`): whether it is vfx3's
  maths or something shared.
