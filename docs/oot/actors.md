# Actors

What an actor is in this game, how actors are made and removed, and how each one is updated every frame: the one actor
class, its components and parameter files, the managers that create and delete actors, the five jobs each actor runs on
the BaseProcExecutors, the resident actors (Link among them), and the settings that decide how often an actor is
updated at all. Looked at in November 2026 by static analysis of the binary, for the frame-rate work:
[jobs.md](jobs.md) found the frame bound by the game's logic on the CPU, and the actors are most of that logic.

**Longshot code:** [host/hos_main.c](../../host/hos_main.c) (`hos_oot_is_player`, `hos_oot_lift_player`, the flight and the
pouch calls), [nx2/oot_patches.py](../../nx2/oot_patches.py) (`patch_life`, `patch_movement`)
**See also:** [jobs.md](jobs.md) (the pools, stage 5, the BaseProc phase), [player.md](player.md) (Link's movement, health
and pouch), [maps.md](maps.md) (the layouts, the actor packs and ActorParam, the actor registry), [culling.md](culling.md),
[events.md](events.md)

## In short

1. **There is one actor class.** The game registers a single class with its actor factory, "GameActor" (`f_83392300`):
   every actor, from a wall piece (`Land_*`) to an enemy to Link, is a 0x788-byte GameActor (vtable `0x84b45e18`), built
   on the engine's actor base (vtable `0x84c7b7a8`). What an actor does comes from its **components**, named by its
   ActorParam's `Components` ([maps.md](maps.md), *What an actor is made of*): two registries map a component's name to
   its maker, the engine's 28 (`f_81209b40`: AS, AIInfo, Physics, Model, LOD, CalcSkip, …) and the game's
   (`f_8122db20`, about 109 entries: Life, Player, BSA, Hookshot, Npc…, Weapon, …). **read**
2. **Creating and deleting** goes through requests. A placement (a map's layout, [maps.md](maps.md)) or an event node
   (`SeqExecuteCreateActor`, `ExecuteGenericRequestCreateActor`) asks the actor manager for an actor by name
   (`f_80392a70`: the pack is loaded, the instance registered); the BaseProc manager (`[0x84eb0d90]`) keeps three request
   queues, and **six "BaseProcCreateAndDeleteThread" threads** create and delete from them (`f_83ad0bf0`), off the frame.
   `SeqExecuteWaitCreateActor` waits for the queues to empty (`f_83acff00`). **read**
3. **Each actor has five jobs** (`f_83af21f0`, at actor `+0x2c0` to `+0x2e0`), each a small functor pointing at the
   actor. In stage 5 of the frame ([jobs.md](jobs.md)) the BaseProcExecutors run them, and each calls one or two of the
   actor's virtual update methods (slots 50 to 56). The game's big per-frame update is slot 52 (`f_82c3fab0`), reached
   from the second job, `f_800b3ce0`, which is the job [jobs.md](jobs.md)'s profile found on 18% of the frame's critical
   path. **read** The game names four BaseProc phases (`PreCalc`, `PostCalc`, `PostSensor`, `PostUpdateMatrix`); which
   job is which phase was not worked out (**guess**: the five are those four and a "Blank" one, from the LOD settings'
   names).
4. **How much an actor is updated is data:** its LOD and calc-skip settings (`engine__component__LODParam`,
   `engine__cskip__…`) say, per actor, whether each phase's job is pushed at all (`ForbidPushJobPreCalc`, `…PostCalc`,
   `…PostSensor`, `…Blank`), beyond what distance or outside the view it stops (`ForbidPushJobDistance`,
   `ForbidPushJobRadiusOutsideView`), whether it may be updated only every few frames (`EnableCalcSkip`,
   `CalcIntervalOnCalcSkipForce`) and whether its cloth, drawing and effects may skip too. **read** (the fields), **guess**
   (what each does, from its name)
5. **Link is a resident actor**, made by the scene's ResidentActorMgr (`Player_Child` or `Player_Adult`, `f_80964800`),
   not by a map. The host finds him by an object every character has (vtable `0x84b6c460`) whose resource record is
   named "Player" (`hos_oot_is_player`). **read**

## Functions

### The actor class and its components

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83392300` | `GameActor_RegisterClass` | Registers the class "GameActor" (`0x83d2d9a4`) with its maker `f_80ad3230` in the actor manager's class table (`[[0x84c9f3e0]]+8`, through `f_8120a760`). It is that function's only caller: no other actor class is registered | read |
| `f_8120a760` | `ClassTable_Add(table, &name, &entry, &flags)` | Adds a name and its maker to a class table | read |
| `f_80ad3230` | `GameActor_Create(heap, align, arg)` | Allocates 0x788 bytes from the heap (its vtable `+0x30`), runs the engine's actor constructor `f_80ad3480` and sets the vtable `0x84b45e18` | read |
| `f_80ad3480` | `ActorBase_Construct(actor, arg)` | The engine's actor constructor: sets the base vtable `0x84c7b7a8` and clears the fields. Also called from `f_83aeff60` | read |
| `f_81209b40` | `EngineComponents_Register` | Registers the engine's components by their ActorParam names: Blackboard, ModelInfo, ModelBind, ASInfo, ASOptimize, Animation, RetargetScale, AS, AIInfo, Physics, ELink, SLink, XLink, Reaction, EventMember, EventPerformer, BeforehandLoadEvent, Camera, GameParameterTable, LOD, CalcSkip, CppExecutorParam, NaviAnimator, Pathfinding, Obstacle, BoneBind, Destruction, MotionMatching | read |
| `f_8122db20` | `GameComponents_Register` | Registers the game's components (about 109 entries), each a `{type, name, maker}` entry (from `0x84e42f90` on): Accessory to WorldInfo, with Life (0x280 bytes), Player, BSA (0xc0), Physics, Hookshot, NpcMovementInfo, Weapon and others | read |
| `f_829c3780` | `QueryActorGetBaseProcId_Evaluate` | Returns the node's actor's BaseProc ID, the 32-bit word at actor `+0x18`; the actor is the node's `f_80853d60` ([input.md](input.md)), and the name getter `f_829c38c0` | read |

### The five jobs each actor runs

`f_83af21f0` is slot 12 of the actor vtable (the base's and GameActor's are the same function). The five jobs' runs
each first record the actor being run against the current executor thread (in a six-entry table at `[[0x84c9dc40]]+0x80`,
keyed by the thread's `+0xa8`), then call the actor's method; most of them also call slot 57 first.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83af21f0` | `ActorBase_CreateJobs` | Makes the actor's five jobs, each a 0x38-byte node and a 0x30-byte functor that points back at the actor (layout below), with the vtables `0x84c7bed8`, `0x84c7bf00`, `0x84c7bf28`, `0x84c7bf50`, `0x84c7bf78`, stored at actor `+0x2c0`, `+0x2c8`, `+0x2d0`, `+0x2d8` and `+0x2e0` (the last also at `+0x138`). It also makes a 0x100-byte block at `+0x470` | read |
| `f_800b73d0` | `ActorJob0_Run` | Job 0 (`+0x2c0`): calls the actor's slot 50 (`+0x190`) with the job's argument, then slot 51 (`+0x198`) | read |
| `f_83af59c0` | `ActorJob2_Run` | Job 2 (`+0x2d0`): slot 57, then slot 53 (`+0x1a8`) | read |
| `f_83af6150` | `ActorJob3_Run` | Job 3 (`+0x2d8`): slot 57, then slot 54 (`+0x1b0`) and slot 56 (`+0x1c0`) | read |
| `f_800b9420` | `ActorJob4_Run` | Job 4 (`+0x2e0`), the largest (3,480 instructions, about 45 indirect calls): slot 55 (`+0x1b8`) and much besides, among them actor links (`f_802dc440`, [events.md](events.md)) and the components at actor `+0x270` | read |
| `f_82c46560` | `GameActor_vt57` | Slot 57: returns the word at actor `+0x5f8` (the base's returns 0). The jobs call it before their method | read |
| `f_82c3fab0` | `GameActor_Calc52` | Slot 52, run by job 1 (`f_800b3ce0`, row in [jobs.md](jobs.md)): the game's main update, 1,724 instructions calling about 40 subsystems by the components present, among them Link's movement update (`f_82c415e0`, twice, [player.md](player.md)), the bones, cloth and look IK (`f_8018dba0`), the player's climb and ladder states (`f_82ea25b0`), the player's game parameters (`f_80609d00`), NPC talk (`f_802c91f0`) and the engine's slot 54 (`f_800cdbe0`) | read |
| `f_800cdbe0` | `ActorBase_vt54` | GameActor's slot 54 (6,236 instructions), run by job 3 and also called from slot 52 and from `f_82c43898` | read |
| `f_800c34e0` | `GameActor_vt56` | Slot 56 (3,052 instructions), run by job 3 | read |
| `f_8018dba0` | `Skeleton_Update` | A 6,004-instruction update with the labels "OffHelperBone", "OffSupportBone", "OffCloth" and "LookIK": the skeleton's helper and support bones, cloth and look IK | guess |
| `f_82ea25b0` | `Player_UpdateClimb` | A 5,100-instruction state update named by its strings ClimbEd, ClimbMoveUpEd, Crouch, LadderUp, LadderDown, … | guess |
| `f_80b4eaa0` | `GameActor_SleepToPreActor` | Slot 72, with the string "SleepToPreActor": puts the actor to sleep as its PreActor (below) | guess |

The other method slots GameActor overrides, from the engine's: 0, 1, 7–9, 11, 14, 16, 17, 21, 23, 35, 37, 44–58, 65, 66,
68–82, 86, 87, 89, 92, 93 and 95. Slots 75 and 77 name "Logic_Disappear", slot 49 "Logic_Appear". **read**

### The managers: creating and deleting

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83aff1f0` | `ActorModule_Calc` | The Actor module's calc (its vtable `+16`, [jobs.md](jobs.md)): among others, calls `f_83afbfb0` on the actor manager and `f_83acf9b0` / `f_83acfa30` on the BaseProc manager | read |
| `f_83afef50` | `ActorModule_Init` | The Actor module's slot 7: builds a settings block (`f_80b5e290`), then `f_80ed5d00` on the BaseProc manager, `f_83afb830` on the actor manager, `f_83b059a0` on the actor registry (`[0x84eb1940]`, [maps.md](maps.md)), and makes a 0x188-byte object (`f_80b5ef60`) kept at Actor module `+0x1b0` | read |
| `f_80ed5d00` | `BaseProcMgr_Init(mgr, settings)` | Sets the BaseProc manager up and calls `f_80ed7f60` | read |
| `f_80ed7f60` | `BaseProcMgr_CreateThreads` | Makes six "BaseProcCreateAndDeleteThread" threads (0x160 bytes each, sead::Thread `f_8069dbe0`, vtable `0x84c79680`), each given its core mask from settings `+0x60`, `+0x64`, … `+0x74` (`f_80ed84a0`) | read |
| `f_80ed84a0` | `Thread_SetCoreMask(thread, &mask)` | Keeps the mask at thread `+0xb0` and sets it on the thread | read |
| `f_83ad0bf0` | `BaseProcCreateAndDeleteThread_Run` | Slot 14 of that thread: works through the manager's requests (`[thread+0xd8]`), creating and deleting, with the profiler labels "UpdateRequestQueue", "%s(%p)" and "Delete %s(%p)" | read |
| `f_83acff00` | `BaseProcMgr_IsRequestEmpty(mgr)` | Under the mutex at `+0x3c8`: true when the three request counts (`+0x3f8`, `+0x410`, `+0x428`) are 0 | read |
| `f_83acf390` | `BaseProcMgr_DestroyInstance` | Destroys `[0x84eb0d90]` (`f_83acf400`, 0x470 bytes) | read |
| `f_83afb830` | `ActorMgr_MakeHeaps` | Makes the heaps "ActorInstanceHeap", "BaseProcCreateOrderHeap" and "DynamicParamHeap" | read |
| `f_83afbfb0` | `ActorMgr_Calc` | The actor manager's per-frame step (972 instructions), from the module's calc | guess |
| `f_80392a70` | `ActorMgr_RequestCreateByName` | Creates an actor by name: loads `Pack/Actor/<name>.pack` (`f_8002f220`), finds the archetype (`f_802dbd90`) and adds the instance (`f_83b072b0`, both [maps.md](maps.md)), then hands it on (`f_83afdf90`). `f_8121f9a0` and `f_82c4b220` have the same strings (**guess**: copies for other callers) | read |
| `f_83afdf90` | `ActorMgr_PushCreate` | Queues the creation (604 instructions; slot of vtable `0x84c7c778`) | guess |
| `f_832dca10` | `SeqExecuteCreateActor_Enter` | The script node SeqExecuteCreateActor (parameters ActorName, InitPos, IsSleep, OverwriteParam, ActorLink) starting: calls `f_80392a70` | read |
| `f_808e90a0` | `SeqExecuteWaitCreateActor_Update` | SeqExecuteWaitCreateActor (parameter IsWaitBaseProcRequestEmpty): waits on `f_83acff00` | read |
| `f_805cf0e0` | `Placement_ReadCreateParams` | Reads a placed actor's creation fields: Presence (FlagName, IsNegation, IsCheckLazy), ExtraCreateRadius, IsForceActive, IsShortCreateRadiusInHeavy, MoveRadius, IsPhysicsStable, IsInWater, ExternalParameter, … and the AreaCulling block (`f_805cefb0`) | read |
| `f_805cefb0` | `Placement_ReadAreaCulling` | The AreaCulling block: AreaCullingActivationArea, AreaCullingTargetArea | read |

### Resident actors

The resident actors live with the scene, not with a map: Link, among others (a `game__scene__ResidentActor` holds
`Actor`, `IsCancelSleepAtEnterBanc`, `HasCancelSleepPos` and `CancelSleepPos`). Their manager, "ResidentActorMgr", is
one of the scene components `f_832d4720` makes ([culling.md](culling.md)). Script nodes create, delete and put them to
sleep: `SeqOneShotSceneRequestCreateResidentActors` (IsCreateChildPlayer), `SeqOneShotSceneRequestDeleteResidentActors`,
`SeqExecuteSceneRequestSleepResidentActors` and `SeqExecuteSceneRequestCancelSleepResidentActors` (CancelSleepPos,
CancelSleepRot).

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80965170` | `Scene_GetResidentActorMgr(scene)` | The scene's component at `[scene+0x58]+0x78`, checked by type, when the count at `scene+0x50` is at least 16 | guess |
| `f_832fe900` | `SeqOneShotSceneRequestCreateResidentActors_Evaluate` | Asks the ResidentActorMgr to create the residents (`f_80965610`, with IsCreateChildPlayer), sets bit 0x2000 of `[0x84e40e20]+0x34` and ends the node | read |
| `f_80965130` | `SeqOneShotSceneRequestDeleteResidentActors_Evaluate` | Asks it to delete them (`f_809659f0`) | read |
| `f_809650c0` | `SeqExecuteSceneRequestSleepResidentActors_Update` | Asks it to put them to sleep (`f_80963920`) and ends the node when that returns true | read |
| `f_80965610` | `ResidentActorMgr_RequestCreate(mgr, child)` | Requests the residents | guess |
| `f_809659f0` | `ResidentActorMgr_RequestDelete(mgr)` | Requests their deletion | guess |
| `f_80963920` | `ResidentActorMgr_SleepStep(mgr)` | One step of putting the residents to sleep; true when done | guess |
| `f_80964800` | `ResidentActorMgr_FindByName(mgr, &name)` | Looks a resident up by name; the player's names (`Player`, `Player_Base`, `Player_Child`, `Player_Adult`) are matched as one | read |

### The update settings

Reflection visitors: each names a parameter and the field it fills (offsets in *Structures*).

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83c0dd50` | `LODParam_Visit` | `engine__component__LODParam` (0xd8 bytes, its name at `0x83d6f614`): 44 fields, the pushing and skipping of the actor's jobs (below). Its twin `f_83c0f780` names the same fields | read |
| `f_83c077b0` | `CalcSkipParam_Visit` | `engine__component__CalcSkipParam` (0x88 bytes): InitParam (`+0x40`) and DefaultPresetParam (`+0x38`) | read |
| `f_83c21730` | `CalcSkipInitParameters_Visit` | `engine__cskip__InitParameters` (0xd8 bytes): the same pushing fields as LODParam and the calc skip's (EnableCalcSkipGlobal, EnableCalcAndPhysicsSkip, CalcSkipRescuePriority, CalcSkipRescueDistance, EnableDrawSkip, EnableClothSkip, IsKinematicsSkipKeepAtInit, …). Its twin `f_83c23280` names the same fields | read |
| `f_83c20470` | `CalcSkipDefaultPresetParameters_Visit` | `engine__cskip__DefaultPresetParameters` (0x58 bytes): ForbidPushJobSetting, DisableDefaultForbidPushJob, DisableForbidPushJobByArea, OverwriteForbidPushJobDistance, DisableSkipAll, DisableSkipProc, DisableSkipKinematics, DisableSkipAppearance, DisableSkipXLink, DisplayRadius | read |
| `f_83c0d650` | `BaseProcPhase_Name(phase, &out)` | Copies the name of phase 0–3 (`[phase+8]`) from the table `0x83f90ae0` ([jobs.md](jobs.md)): PreCalc, PostCalc, PostSensor, PostUpdateMatrix | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84eb0d90` | `g_BaseProcMgr` | The BaseProc manager (0x470 bytes): the request queues, the create and delete threads; `f_83acfb90` runs the BaseProc phase on it ([jobs.md](jobs.md)) | read |
| `0x84eb1888` | `g_ActorMgr` | The actor manager: the class table at `+8`, the actor heaps (`f_83afb830`); reached through the GOT slot `0x84c9f3e0` from about 116 functions, jobs 1 and 4's runs among them | read |
| `0x84c9f3e0` | `got_ActorMgr` | The GOT slot holding `0x84eb1888` | read |
| `0x84b45e18` | `GameActor_vtable` | The only actor class's vtable (96 slots). `hos_oot_lift_player` checks for it | read |
| `0x84c7b7a8` | `ActorBase_vtable` | The engine's actor base vtable | read |
| `0x84b6c460` | `CharacterObj_vtable` | The vtable of a 0x1d20-byte object made by `f_80d1fbb0` (which `f_8122db20` lists under the name "InactiveCounterRef"; **guess** that the pairing is right). Other actors than Link have one; its `+16` record is named "Player" for Link (`hos_oot_is_player`), and Actor_WriteLife's actor (`f_804bd8c0`, [player.md](player.md)) is one, its life component at `+7184` | read |
| `0x84c7bed8` | `ActorJob0_vtable` | Job 0's functor (run `f_800b73d0`) | read |
| `0x84c7bf00` | `ActorJob1_vtable` | Job 1's functor (run `f_800b3ce0`) | read |
| `0x84c7bf28` | `ActorJob2_vtable` | Job 2's functor (run `f_83af59c0`) | read |
| `0x84c7bf50` | `ActorJob3_vtable` | Job 3's functor (run `f_83af6150`) | read |
| `0x84c7bf78` | `ActorJob4_vtable` | Job 4's functor (run `f_800b9420`) | read |
| `0x84c79680` | `BaseProcCreateAndDeleteThread_vtable` | The create and delete threads' vtable (slot 14 `f_83ad0bf0`) | read |
| `0x83d2d9a4` | `str_GameActor` | "GameActor" | read |
| `0x83d7c824` | `str_BaseProcCreateAndDeleteThread` | "BaseProcCreateAndDeleteThread" | read |

## Structures

### A GameActor (0x788 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0x0` | The vtable, `0x84b45e18` | read |
| `+0x18` | The BaseProc ID, 32 bits (QueryActorGetBaseProcId); its low byte goes into each job's functor | read |
| `+0x28` | A byte copied into each job node's `+0x20` and `+0x21` | read |
| `+0x138` | Job 4 again | read |
| `+0x270` (624) | The components; `hos_oot_lift_player` follows `+800` of it to Link's movement component | read |
| `+0x2c0` … `+0x2e0` | The five jobs (`f_83af21f0`) | read |
| `+0x31c` … `+0x324` (796–804) | The position, three floats (the host's flight reads them as Link's position) | read |
| `+0x330`, `+0x348` (816, 840) | Two matrix elements the host takes the heading from | read |
| `+0x3d8` | An object whose `+0x58` holds the settings a job's `+0x20` byte is taken from (`+0x9c`, `+0xbe`) | read |
| `+0x470` | A 0x100-byte block made with the jobs | read |
| `+0x5f8` | The word slot 57 returns | read |

### An actor job (`f_83af21f0`)

| Offset | Field | Sure |
|---|---|---|
| node `+0x10` | The actor | read |
| node `+0x22` | 1 | read |
| node `+0x28`, `+0x30` | The functor | read |
| functor `+0x0` | Its vtable; `+16` of it is the run | read |
| functor `+0x10` | −1 | read |
| functor `+0x15` | The actor's BaseProc ID's low byte | read |
| functor `+0x18` | The run's argument, set when the job is pushed: two floats and a pointer (**guess**: the frame's time step) | read |
| functor `+0x20` | A byte from the actor's settings | read |
| functor `+0x28` | The actor | read |

### The BaseProc manager (`[0x84eb0d90]`, 0x470 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0x80` | Its pool A, through `+0x10` ([jobs.md](jobs.md)) | read |
| `+0x3c8` | The request queues' mutex | read |
| `+0x3f8`, `+0x410`, `+0x428` | The three request queues' counts | read |

### LODParam (`engine__component__LODParam`, 0xd8 bytes)

The fields about updating. The rest: IsCharacter `+0x7c`, IsConstActor `+0x84`, UsePhysicsPause `+0x8c`,
UseActiveAsMoving `+0xa3`, CheckMainRigidBodyTrackingBone `+0x92`, IsNoAutoParentOnModelBind `+0xa1`,
IsCheckKinematicRigidBodyMove `+0xa0`, JudgeCalcSupportBoneWithCloth `+0xa2`, IgnoreProhibitionByXLink `+0x9c`,
IsAlwaysDisableInEventMember `+0x9e`. **read** (`f_83c0dd50`)

| Offset | Field | Sure |
|---|---|---|
| `+0x9f` | IsAutoPause | read |
| `+0x95`, `+0x93`, `+0x94` | DefaultForbidPushJobPreCalc, DefaultForbidPushJobPostCalc, DefaultForbidPushJobPostSensor | read |
| `+0x58` | DefaultForbidPushJobBlank | read |
| `+0x91` | AlwaysForbidPushJob | read |
| `+0x74` | ForbidPushJobDistance | read |
| `+0x78` | ForbidPushJobRadiusOutsideView | read |
| `+0x70` | ForbidPushJobBlank | read |
| `+0x3c` | ApplyScaleTypeToForbidPushJob | read |
| `+0x90` | AlwaysForbidCalcOnSleepJob | read |
| `+0x9b`, `+0x99`, `+0x9a` | ForbidPushJobPreCalc, ForbidPushJobPostCalc, ForbidPushJobPostSensor | read |
| `+0xa4` | UseAreaJudge | read |
| `+0x88`, `+0xa5` | UseOcclusionQuery, UseOcclusionQuerySphere ([culling.md](culling.md)) | read |
| `+0x38`, `+0x44`, `+0x40` | ActorRadiusScale, AreaNearDistance, AreaMiddle0DistanceScale | read |
| `+0x5c` | EnableCalcSkip | read |
| `+0x48` | CalcIntervalOnCalcSkipForce | read |
| `+0x6c` | ForbidCalcSkipInFrontViewDist | read |
| `+0x50`, `+0x96`, `+0x4c`, `+0x97` | CalcSkipRescuePriority, EnableRescueDistanceLimit, CalcSkipRescueDistance, EnableRescueInOutsideViewFar | read |
| `+0x68` | EnableDrawSkip | read |
| `+0x60`, `+0x9d`, `+0x64`, `+0x54`, `+0x80` | EnableClothSkip, IsAlwaysClothSkip, EnableClothSkipRescue, ClothSkipNearestPriority, IsClothSkipKeepAtInit | read |
| `+0x98` | EnableXLinkSkip | read |

## Notes

- Job 0 sets the actor's time group before slot 51 runs: it fills its core's frame-time slot (`keeper+0x20+0x30*core`) with the step scaled by the group's rate and its inverse, scales slot 51's step by the group's rate over group 0's and keeps the ratio at `actor+0x2e8`; the group comes from slot 57 ([animation.md](animation.md)). **read**
- The component-offset map is on [ai.md](ai.md); the base slots 60, 61 and 63 also run AI units, and `f_8018dba0` is called from slot 51 as well as slot 52. `f_802ca820` (hot rank 64) has the shape of job 1's run (`f_800b3ce0`): another actor-job run. **read**

### From a placement to a running actor

1. The map's layout names each actor's archetype and instance; the layout's reader adds them to the actor registry
   ([maps.md](maps.md)). The placement's own fields (`f_805cf0e0`) carry its creation radius (ExtraCreateRadius,
   IsShortCreateRadiusInHeavy), IsForceActive, the Presence flag and its area culling. The ActorInfo row gives the
   create, delete, calc and display radii ([maps.md](maps.md), `f_805cb760`). **read** (the fields), **guess** (that the
   PlacementMgr creates and deletes by those radii)
2. A creation goes through the actor manager (`f_80392a70`) into the BaseProc manager's request queues; the six create
   and delete threads make the GameActor (`f_80ad3230`) and its components and jobs. So creating an actor costs no frame
   time on the BaseProcExecutors, but a burst of creations is bounded by those threads. **read** (the path), **guess**
   (when a created actor joins the frame)
3. Each frame, in stage 5 ([jobs.md](jobs.md)), the actor's jobs that are pushed run on the BaseProcExecutors. Which are
   pushed follows its LOD and calc-skip settings (pushing, distance, view, calc skipping).
4. Far or put to sleep, an actor can turn into its **PreActor**: a light stand-in kept for placed actors that are not
   created (the strings PreActor, `SleepToPreActor`, `Actor2PreActor`, `IsTurnToPreActor`, `UsePreActorRenderer`,
   `IsFarActor`, `MaxPreActors`, `UsePreActorAreaCulling`, and the job "PreActorRendererUpdateJob", `f_80d32160`).
   **guess**

### Link

Link is GameActor too, made by the ResidentActorMgr (`Player_Child` or `Player_Adult`) rather than by a map, so he
survives a change of map. The host knows him in two ways: in `patch_life` by the object `Actor_WriteLife` has in `x19`
(vtable `0x84b6c460`, its `+16` record named "Player"); in `patch_movement` by the GameActor vtable, then his components
(`+624`), their `+800` (the movement component, whose `+24` is the actor), and `hos_oot_is_player` on the address 0x1d50
before that component. **read**

That object is 0x1d20 bytes as `f_80d1fbb0` allocates it, 0x30 less than the 0x1d50 the host steps back. So the
component the host starts from is not inside it: the check holds because of where the two are allocated, one after the
other in the actor's heap, not because of a field. It works in the game; it would fail quietly (the cheats would see no
Link) if the allocation order changed. **guess** (from the sizes; not checked live)

### What an actor's update costs

From the code's size and what it calls, not measured (**guess** unless a measurement is cited):

- **Job 1, slot 52 (`f_82c3fab0`)** is the main game update: movement and the character controller, the skeleton's
  helper bones, cloth and look IK (`f_8018dba0`, 6,004 instructions), the player's state machines (`f_82ea25b0`,
  `f_82e44040`, `f_80609d00`, 3,500–5,100 each), NPC talk, the engine's slot 54 (6,236). [jobs.md](jobs.md) measured its
  job (`f_800b3ce0`) at 12% of the BaseProcExecutors' running time and 18% of the frame's critical path in the Temple of
  Time. **tested** (that measurement, [jobs.md](jobs.md))
- **Physics** runs as a BaseProc of its own ("PhysicsSystemBaseProc", [jobs.md](jobs.md)), Havok's tasks on the same
  executors: the narrow phase (`f_8191ee00`, 14% of their running time) and cloth (`f_8016b320`, which runs "TtCloth",
  9%). **tested** (the shares, [jobs.md](jobs.md)); each actor's physics component (PhysicsRef) feeding that world is
  a **guess**
- **AI:** each actor with AIInfo runs its `.ainb` graphs; the AI groups run in their own job ("AIGroupParallelCalc",
  [jobs.md](jobs.md)), and Havok AI's character step (`f_820da77c`) is the longest single piece of the critical path.
- **Animation:** the AS component plays `AS/<name>.asb` (`f_803fbc80`, `f_8071f0a0`) with its animation events
  (`AnimationEvent/AsNode/…baev`, `f_803fb0c0`); ASOptimize is a component of its own. Where AS's update runs (which
  job) was not traced.
- **How many run:** the Forest Temple's layouts place 3,213 actors ([maps.md](maps.md)), 2,236 of them room pieces with
  only a model and a static body. Every one is a GameActor with five jobs; how many jobs are pushed a frame depends on
  the LOD settings, which for a room piece likely forbid them all. Counting the pushed jobs live is the next step.

## Open questions

- Which job is which phase (PreCalc, PostCalc, PostSensor, PostUpdateMatrix, and a "Blank" one?), and the order the
  five run in within stage 5.
- How many actors exist and how many jobs are pushed a frame in a busy scene (Kokiri Forest, Hyrule Field): a live count
  of the BaseProc manager's lists, or of the five job vtables' runs, would say how much the frame depends on actor count.
- Where the LOD settings are read each frame (the code that checks `ForbidPushJob*` and the distances), and what calc
  skipping does to an actor's update (every Nth frame, or interpolated).
- Who reads `actor+0x3d8` and what its `+0x58` object is (LODParam or the calc skip's InitParameters).
- What slots 50, 51, 53, 55 and 56 do, and where AS and the AI graphs are updated.
- The PreActor: what it holds, when an actor becomes one and back, and what "PreActorRendererUpdateJob" draws.
- What `0x84b6c460`'s object really is: the component registry's pairing gives "InactiveCounterRef", which its size and
  its life pointer don't fit.
- What the six create and delete threads' core masks are (the settings at `+0x60` to `+0x74` are filled at run time).
